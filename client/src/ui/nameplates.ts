// Names above characters' heads, as pooled DOM labels. Only the nearest few
// within reading distance get one, so the per-frame cost stays a handful of
// style writes however crowded the castle is. In a crowd, a name that would
// sit on top of a nearer one is left out, so what shows stays readable.
// A name shows only while its head could be seen (plate-sight.ts): never
// through a wall, a floor or a ceiling.

import { h } from "./dom";
import { lineIcon } from "./effects-icons";
import type { GroupKind } from "./groups";
import { CUP_ICON } from "./house-cup";
import { PlateSight, type SightTarget, type SightTest } from "./plate-sight";

export interface PlateTarget {
  id: number;
  name: string;
  colour: string;
  /** The House Cup's badge: the character's house holds the cup this week. */
  badge?: boolean;
  /** NPCs: a resident of the castle (a ghost, staff, a shopkeeper), drawn in the residents' style, never a player's. */
  npc?: boolean;
  /** A group of ours the character is in (`groups.ts` `plateGroup`): the name takes its colour (party blue). */
  group?: GroupKind | null;
  /** World position of the label anchor (above the head). */
  x: number;
  y: number;
  z: number;
}

/** A candidate plate on screen: bottom-centre anchor and half its width (px). */
export interface PlateBox {
  id: number;
  x: number;
  y: number;
  halfWidth: number;
}

const MAX_PLATES = 40;
const MAX_DISTANCE = 28;
/** Plate height and estimated width per character of name (px), matching style.css. */
export const PLATE_HEIGHT = 21;
const PLATE_CHAR_WIDTH = 6;
const PLATE_PADDING = 16;
/** The cup badge's width with its gap (px). */
const PLATE_BADGE = 18;
/** A new plate must clear nearer ones by this fraction of their combined extent (1 = just touching). */
const OVERLAP_NEW = 1.0;
/** Plates already showing hold on until they overlap a little, so names don't flicker. */
const OVERLAP_SHOWN = 0.85;

/**
 * Picks the plates to show from `boxes` (nearest first): each is kept unless
 * it overlaps a plate already kept. Pushes the kept indices into `out` and
 * returns it. Plates in `wasShown` (by id) tolerate more overlap.
 */
export function declutter(boxes: readonly PlateBox[], wasShown: ReadonlySet<number>, out: number[]): number[] {
  out.length = 0;
  for (let i = 0; i < boxes.length && out.length < MAX_PLATES; i++) {
    const a = boxes[i]!;
    const k = wasShown.has(a.id) ? OVERLAP_SHOWN : OVERLAP_NEW;
    let clear = true;
    for (const j of out) {
      const b = boxes[j]!;
      if (Math.abs(a.x - b.x) < (a.halfWidth + b.halfWidth) * k && Math.abs(a.y - b.y) < PLATE_HEIGHT * k) {
        clear = false;
        break;
      }
    }
    if (clear) out.push(i);
  }
  return out;
}

/**
 * A screen rectangle (px) plates must not sit under: a corner of the HUD
 * (docs/architecture.md "Client"). One along the
 * top edge (the name, counts, level and clock at the top right) pushes plates
 * down below it; one along the bottom edge (`edge: "bottom"`, the menu bar
 * and the emote bar at the bottom right) pushes them up above its `top`.
 */
export interface Keepout {
  left: number;
  right: number;
  bottom: number;
  /** Required for a bottom box. */
  top?: number;
  edge?: "top" | "bottom";
}

/**
 * Moves a plate (bottom-centre anchor) that would sit under one of `bars`
 * out of it: below a box along the top edge, above one along the bottom, so
 * names near the screen's edges stay readable instead of hiding behind the
 * HUD. Plates beside a box are left alone.
 */
export function clearOf(box: PlateBox, bars: Keepout | readonly Keepout[] | null): void {
  if (!bars) return;
  if (!("length" in bars)) return clearOne(box, bars);
  for (const bar of bars) clearOne(box, bar);
}

function clearOne(box: PlateBox, bar: Keepout): void {
  const overlapsX = box.x + box.halfWidth > bar.left && box.x - box.halfWidth < bar.right;
  if (!overlapsX) return;
  if (bar.edge === "bottom") {
    const top = bar.top ?? bar.bottom;
    if (box.y > top && box.y - PLATE_HEIGHT < bar.bottom) box.y = top;
  } else if (box.y - PLATE_HEIGHT < bar.bottom) {
    box.y = bar.bottom + PLATE_HEIGHT;
  }
}

/** Estimated half-width of a plate showing `name` (px); measuring the DOM every frame would force layout. */
export function plateHalfWidth(name: string, badge = false): number {
  return (name.length * PLATE_CHAR_WIDTH + PLATE_PADDING + (badge ? PLATE_BADGE : 0)) / 2;
}

const byDistance = (a: { d: number }, b: { d: number }): number => a.d - b.d;

interface Plate {
  el: HTMLDivElement;
  name: HTMLSpanElement;
  cup: SVGSVGElement;
  badge: boolean;
  npc: boolean;
  group: GroupKind | null;
  /** What the element currently shows, to skip redundant DOM writes. */
  shown: string;
  colour: string;
  visible: boolean;
}

export class Nameplates {
  private readonly root: HTMLDivElement;
  private readonly pool: Plate[] = [];
  private readonly scratch: { t: PlateTarget; d: number }[] = [];
  private readonly nearPool: { t: PlateTarget; d: number }[] = [];
  // Per-frame scratch for the declutter pass, reused to avoid garbage.
  private readonly onScreen: { t: PlateTarget; d: number }[] = [];
  private readonly boxes: PlateBox[] = [];
  private readonly boxPool: PlateBox[] = [];
  private readonly kept: number[] = [];
  private shownIds = new Set<number>();
  private nextShownIds = new Set<number>();
  /** Line of sight to each head: plates behind level geometry stay hidden. */
  readonly sight = new PlateSight();
  private readonly sightTargets: SightTarget[] = [];
  private readonly sightAlpha: number[] = [];

  constructor(parent: HTMLElement) {
    this.root = h("div", { class: "nameplates", "aria-hidden": "true" });
    parent.append(this.root);
  }

  /**
   * @param viewProj the camera's view-projection matrix (Babylon `scene.getTransformMatrix().m`, row-vector layout)
   * @param eye camera position, for the distance cut-off
   * @param bars the HUD's corner boxes (canvas px), which plates keep clear of
   */
  update(targets: readonly PlateTarget[], viewProj: ArrayLike<number>, eye: readonly [number, number, number], width: number, height: number, bars: readonly Keepout[] = [], now = performance.now()): void {
    const near = this.scratch;
    near.length = 0;
    for (const t of targets) {
      const d = Math.hypot(t.x - eye[0], t.y - eye[1], t.z - eye[2]);
      if (d > MAX_DISTANCE) continue;
      // Entries are pooled: this runs every frame for up to 100 characters.
      const n = (this.nearPool[near.length] ??= { t, d });
      n.t = t;
      n.d = d;
      near.push(n);
    }
    near.sort(byDistance);

    // Project, nearest first, keeping what is in front of the camera and on screen.
    const m = viewProj;
    const onScreen = this.onScreen;
    const boxes = this.boxes;
    onScreen.length = 0;
    for (const n of near) {
      const t = n.t;
      const w = t.x * m[3]! + t.y * m[7]! + t.z * m[11]! + m[15]!;
      if (w <= 0.1) continue; // behind the camera
      const sx = ((t.x * m[0]! + t.y * m[4]! + t.z * m[8]! + m[12]!) / w) * 0.5 + 0.5;
      const sy = 0.5 - ((t.x * m[1]! + t.y * m[5]! + t.z * m[9]! + m[13]!) / w) * 0.5;
      if (sx < -0.1 || sx > 1.1 || sy < -0.1 || sy > 1.1) continue;
      const i = onScreen.length;
      onScreen.push(n);
      const box = (this.boxPool[i] ??= { id: 0, x: 0, y: 0, halfWidth: 0 });
      boxes[i] = box;
      box.id = t.id;
      box.x = sx * width;
      box.y = sy * height;
      box.halfWidth = plateHalfWidth(t.name, t.badge);
      clearOf(box, bars);
    }
    boxes.length = onScreen.length;

    // Only the heads in sight keep a plate (fading ones too); the hidden take no room from the rest.
    const st = this.sightTargets;
    st.length = onScreen.length;
    for (let i = 0; i < onScreen.length; i++) st[i] = onScreen[i]!.t;
    const alpha = this.sight.update(st, eye, now, this.sightAlpha);
    let kept = 0;
    for (let i = 0; i < onScreen.length; i++) {
      if (alpha[i]! <= 0) continue;
      if (kept !== i) {
        const n = onScreen[kept]!;
        onScreen[kept] = onScreen[i]!;
        onScreen[i] = n;
        const b = boxes[kept]!;
        boxes[kept] = boxes[i]!;
        boxes[i] = b;
        alpha[kept] = alpha[i]!;
      }
      kept++;
    }
    onScreen.length = kept;
    boxes.length = kept;

    const shown = this.nextShownIds;
    shown.clear();
    let used = 0;
    for (const i of declutter(boxes, this.shownIds, this.kept)) {
      const { t, d } = onScreen[i]!;
      const seen = alpha[i]!;
      const box = boxes[i]!;
      shown.add(t.id);
      const plate = this.plate(used++);
      if (plate.shown !== t.name) {
        plate.name.textContent = t.name;
        plate.shown = t.name;
      }
      const badge = t.badge === true;
      if (plate.badge !== badge) {
        plate.cup.style.display = badge ? "" : "none";
        plate.badge = badge;
      }
      const npc = t.npc === true;
      if (plate.npc !== npc) {
        plate.el.classList.toggle("nameplate-npc", npc);
        plate.npc = npc;
      }
      const group = t.group ?? null;
      if (plate.group !== group) {
        if (group) plate.el.dataset.group = group;
        else delete plate.el.dataset.group;
        plate.group = group;
      }
      if (plate.colour !== t.colour) {
        plate.el.style.setProperty("--plate", t.colour);
        plate.colour = t.colour;
      }
      const fade = d > MAX_DISTANCE - 6 ? (MAX_DISTANCE - d) / 6 : 1;
      plate.el.style.transform = `translate3d(${box.x.toFixed(1)}px, ${box.y.toFixed(1)}px, 0) translate(-50%, -100%)`;
      plate.el.style.opacity = (fade * seen).toFixed(2);
      if (!plate.visible) {
        plate.el.hidden = false;
        plate.visible = true;
      }
    }
    for (let i = used; i < this.pool.length; i++) {
      const p = this.pool[i]!;
      if (p.visible) {
        p.el.hidden = true;
        p.visible = false;
      }
    }
    this.nextShownIds = this.shownIds;
    this.shownIds = shown;
  }

  private plate(i: number): Plate {
    let p = this.pool[i];
    if (!p) {
      const name = h("span", { class: "nameplate-name" });
      const cup = lineIcon(CUP_ICON, "nameplate-cup");
      cup.style.display = "none";
      const el = h("div", { class: "nameplate", hidden: true }, cup, name);
      this.root.append(el);
      p = { el, name, cup, badge: false, npc: false, group: null, shown: "", colour: "", visible: false };
      this.pool.push(p);
    }
    return p;
  }

  /** The level a head must be seen past (null: nothing hides a plate). */
  setSightTest(test: SightTest | null): void {
    this.sight.setTest(test);
  }

  dispose(): void {
    this.root.remove();
  }
}
