// The map (key M): parchment and ink, one
// layer per floor plus the grounds, drawn from the castle's walkable
// surfaces (map-state.ts): the places you have stood in, whole, in ink; fog
// over the rest; the passages you have found, by their own names; the
// moving flights where they stand now. On it: you (your position only; no
// footprints of anyone), friends by the place they are in when you have
// revealed it, your party (where they stand when they are with you, at the
// door of the class, dorm or duel they are in), the key places, and each
// class door with its next lesson.
// Drag to pan, the wheel or + and − to zoom; the tabs change floor.
//
// Every colour is a palette colour (data/items/palette.json). It is a 2D
// canvas over the HUD, drawn only while open and only when something
// changed (frame time only: no draws in the 3D scene).

import { PALETTE } from "../content/catalog";
import { focusOrigin, h, restoreFocus } from "./dom";
import { lineIcon } from "./effects-icons";
import { layerName, onLayer, type FriendPlace, type MapLayer, type MapModel, type MapView, type PartyMapMember } from "./map-state";
import "./map.css";

/** A folded map: the HUD button. */
const MAP_ICON = ["M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2Z", "M9 4v14", "M15 6v14"];

/** A palette colour by id (data/items/palette.json). */
function pal(id: string): string {
  return PALETTE.colours.find((c) => c.id === id)?.hex ?? PALETTE.colours[0]!.hex;
}

/** The map's inks, all palette colours. */
export const MAP_INKS = {
  parchment: "parchment",
  room: "ivory",
  ink: "umber_black",
  faint: "tan",
  fog: "dove_grey",
  stair: "oak_brown",
  secret: "plum",
  you: "crimson",
  friend: "cobalt",
  /** Party members: sky blue ringed in navy (the party's blue family, readable on parchment). */
  party: "sky",
  partyRing: "navy",
  place: "mahogany",
  door: "teal",
} as const;

type Inks = Record<keyof typeof MAP_INKS, string>;

/** A key place or a class door on the map. */
export interface MapMark {
  name: string;
  /** A class door's next lesson ("next lesson in 12 min", "door open"). */
  note?: string;
  x: number;
  z: number;
  /** The surface it stands on (its layer). */
  surfaceId: string | undefined;
  kind: "place" | "class";
}

export interface MapWindowOptions {
  /** The castle's map (null until the castle is known). */
  model: () => MapModel | null;
  /** Where we stand in the castle (null in a room: the map still opens). */
  me: () => { x: number; z: number; yaw: number; surfaceId: string | undefined } | null;
  /** Where each moving flight stands now (`FlightView.walk`). */
  walk: (flight: string) => "a" | "b" | undefined;
  friends: () => FriendPlace[];
  /** Our party's other members (none outside a party). */
  party?: () => PartyMapMember[];
  marks: () => MapMark[];
  /** The map opened: a chance to refresh the friends' places (a friend's place only comes by itself at a zone change). */
  onOpen?: () => void;
}

/** A written label's box on the canvas (px). */
interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** Up to this many lines above or below its place a label moves to keep clear of the others. */
const LABEL_TRIES = 6;
const LINE_PX = 13;

/** Writes `text` at (x, y), or the nearest line above or below that is clear of `taken`; notes its box. */
function writeClear(g: CanvasRenderingContext2D, taken: Box[], text: string, x: number, y: number, align: "left" | "center"): void {
  const w = g.measureText(text).width;
  const x0 = align === "left" ? x : x - w / 2;
  let dy = 0;
  for (let i = 0; i <= LABEL_TRIES * 2; i++) {
    dy = (i % 2 ? 1 : -1) * Math.ceil(i / 2) * LINE_PX;
    const b = { x0, y0: y + dy - LINE_PX / 2, x1: x0 + w, y1: y + dy + LINE_PX / 2 };
    if (!taken.some((t) => b.x0 < t.x1 && b.x1 > t.x0 && b.y0 < t.y1 && b.y1 > t.y0)) break;
  }
  taken.push({ x0, y0: y + dy - LINE_PX / 2, x1: x0 + w, y1: y + dy + LINE_PX / 2 });
  g.textAlign = align;
  // A parchment halo keeps the words readable over a stair's hatching or an outline.
  const halo = g.canvas.dataset?.halo;
  if (halo) {
    g.lineJoin = "round";
    g.lineWidth = 3;
    g.strokeStyle = halo;
    g.strokeText(text, x, y + dy);
  }
  g.fillText(text, x, y + dy);
}

const MIN_SCALE = 0.6;
const MAX_SCALE = 24;
const FIT_MAX_SCALE = 8;

export class MapWindow {
  readonly root: HTMLElement;
  readonly button: HTMLButtonElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly tabs: HTMLElement;
  private readonly status: HTMLElement;
  private readonly inks: Inks;
  private origin: HTMLElement | null = null;
  private layer: MapLayer = 0;
  /** The view: the world point at the canvas's middle and pixels per metre. */
  private cx = 0;
  private cz = 0;
  private scale = 4;
  private drawnKey = "";
  private drag: { x: number; y: number; cx: number; cz: number; id: number } | null = null;
  private layersKey = "";

  constructor(private readonly opts: MapWindowOptions) {
    this.inks = Object.fromEntries(Object.entries(MAP_INKS).map(([k, id]) => [k, pal(id)])) as Inks;
    this.button = h("button", { type: "button", class: "icon-button", "aria-label": "Map", "aria-expanded": "false", title: "Map (M)", onclick: () => this.toggle() }, lineIcon(MAP_ICON));
    this.canvas = h("canvas", { class: "map-canvas", role: "img", "aria-label": "Map of the places you have explored" });
    this.tabs = h("div", { class: "map-tabs", role: "tablist", "aria-label": "Floors" });
    this.status = h("p", { class: "map-status" }, "");
    const zoom = (f: number) => () => this.zoomAt(f, null);
    this.root = h(
      "section",
      { class: "map-window panel", role: "dialog", "aria-label": "Map", hidden: true },
      h(
        "header",
        { class: "craft-head map-head" },
        h("h2", {}, "Map"),
        h(
          "div",
          { class: "map-tools" },
          h("button", { type: "button", class: "icon-button", "aria-label": "Zoom in", title: "Zoom in (+)", onclick: zoom(1.4) }, "+"),
          h("button", { type: "button", class: "icon-button", "aria-label": "Zoom out", title: "Zoom out (−)", onclick: zoom(1 / 1.4) }, "−"),
          h("button", { type: "button", class: "secondary map-me", title: "Back to where you stand", onclick: () => this.centreOnMe(true) }, "Find me"),
          h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×"),
        ),
      ),
      this.tabs,
      this.canvas,
      this.status,
    );
    this.root.style.setProperty("--map-parchment", this.inks.parchment);
    this.canvas.dataset.halo = this.inks.parchment;
    this.root.style.setProperty("--map-ink", this.inks.ink);
    this.canvas.addEventListener("pointerdown", (e) => this.onDown(e));
    this.canvas.addEventListener("pointermove", (e) => this.onMove(e));
    this.canvas.addEventListener("pointerup", (e) => this.onUp(e));
    this.canvas.addEventListener("pointercancel", (e) => this.onUp(e));
    this.canvas.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** The floor shown. */
  get shownLayer(): MapLayer {
    return this.layer;
  }

  toggle(open = !this.isOpen): void {
    if (open === this.isOpen) return;
    if (open) this.origin = focusOrigin();
    this.root.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    if (open) {
      this.opts.onOpen?.();
      this.layersKey = "";
      this.centreOnMe(false);
      this.refresh(true);
    } else {
      this.drag = null;
      restoreFocus(this.origin, this.root);
    }
  }

  /** M opens and closes the map; Escape closes it; + and − zoom while it is open. */
  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.code === "KeyM" && !e.repeat) {
      this.toggle();
      return true;
    }
    if (!this.isOpen) return false;
    if (e.key === "Escape") {
      this.toggle(false);
      return true;
    }
    if (e.key === "+" || e.key === "=") {
      this.zoomAt(1.4, null);
      return true;
    }
    if (e.key === "-" || e.key === "_") {
      this.zoomAt(1 / 1.4, null);
      return true;
    }
    return false;
  }

  /** A few times a second: redraws while open when anything changed. */
  refresh(force = false): void {
    if (!this.isOpen) return;
    const model = this.opts.model();
    if (!model) return;
    this.renderTabs(model);
    const me = this.opts.me();
    const party = this.opts.party?.() ?? [];
    // A friend in the party is pinned once, as a member.
    const inParty = new Set(party.map((p) => p.name.toLowerCase()));
    const friends = this.opts.friends().filter((f) => !inParty.has(f.name.toLowerCase()));
    const marks = this.opts.marks();
    const w = this.canvas.clientWidth;
    const hgt = this.canvas.clientHeight;
    const key = [model.changes, this.layer, this.cx.toFixed(2), this.cz.toFixed(2), this.scale.toFixed(3), w, hgt, me ? `${me.x.toFixed(1)},${me.z.toFixed(1)},${me.surfaceId}` : "", friends.map((f) => `${f.name}@${f.place}`).join(","), party.map((p) => `${p.name}@${p.at ? `${p.at.x.toFixed(1)},${p.at.z.toFixed(1)}` : (p.entrance?.note ?? p.place)}`).join(","), marks.map((m) => m.note ?? "").join(","), this.walkKey(model)].join("|");
    if (!force && key === this.drawnKey) return;
    this.drawnKey = key;
    this.draw(model, model.view(this.layer, this.opts.walk), me, friends, marks, party);
  }

  private walkKey(model: MapModel): string {
    let k = "";
    for (const s of model.surfaces) if (s.moving && s.moving.position === "a") k += this.opts.walk(s.moving.flight) ?? "a";
    return k;
  }

  private renderTabs(model: MapModel): void {
    const layers = model.layers();
    const key = `${layers.join(",")}|${this.layer}`;
    if (key === this.layersKey) return;
    this.layersKey = key;
    this.tabs.replaceChildren(
      ...layers.map((l) =>
        h(
          "button",
          { type: "button", role: "tab", class: `map-tab${l === this.layer ? " on" : ""}`, "aria-selected": l === this.layer ? "true" : "false", onclick: () => this.showLayer(l, true) },
          layerName(l),
        ),
      ),
    );
  }

  /** Shows `layer`, fitted to what is drawn on it (`fit`), or keeping the view. */
  showLayer(layer: MapLayer, fit: boolean): void {
    this.layer = layer;
    const model = this.opts.model();
    if (fit && model) {
      const me = this.opts.me();
      const mine = me?.surfaceId ? model.surface(me.surfaceId) : undefined;
      if (me && mine && onLayer(mine, layer)) this.centre(me.x, me.z, 4);
      else this.fit(model.view(layer, this.opts.walk));
    }
    this.layersKey = "";
    this.refresh(true);
  }

  /** Opens on our floor, centred on us (`force`: the button, which also brings our floor back). */
  private centreOnMe(force: boolean): void {
    const model = this.opts.model();
    const me = this.opts.me();
    const s = me?.surfaceId ? model?.surface(me.surfaceId) : undefined;
    if (!model || !me || !s) {
      if (force && model) this.fit(model.view(this.layer, this.opts.walk));
      return;
    }
    this.layer = s.kind === "grounds" ? "grounds" : s.level;
    this.centre(me.x, me.z, this.layer === "grounds" ? 2 : 4);
    this.layersKey = "";
    if (force) this.refresh(true);
  }

  private centre(x: number, z: number, scale: number): void {
    this.cx = x;
    this.cz = z;
    this.scale = scale;
  }

  private fit(view: MapView): void {
    // Fitted to the places explored on the floor (review fix: a far fogged tower made the explored part tiny); all of it when none is.
    const ink = view.shapes.filter((s) => !s.fog);
    const b = ink.length
      ? {
          min: [Math.min(...ink.map((s) => s.min[0])), Math.min(...ink.map((s) => s.min[1]))],
          max: [Math.max(...ink.map((s) => s.max[0])), Math.max(...ink.map((s) => s.max[1]))],
        }
      : view.bounds;
    if (!b) return;
    const w = Math.max(200, this.canvas.clientWidth);
    const hgt = Math.max(150, this.canvas.clientHeight);
    this.cx = (b.min[0] + b.max[0]) / 2;
    this.cz = (b.min[1] + b.max[1]) / 2;
    const s = Math.min(w / Math.max(1, b.max[0] - b.min[0] + 10), hgt / Math.max(1, b.max[1] - b.min[1] + 10));
    // Not too close, so one small explored room still shows its neighbours.
    this.scale = Math.min(FIT_MAX_SCALE, Math.max(MIN_SCALE, s));
  }

  /** Zooms by `f` about a canvas point (null: the middle). */
  private zoomAt(f: number, at: { x: number; y: number } | null): void {
    const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, this.scale * f));
    if (at) {
      // Keep the world point under the cursor where it is.
      const w = this.canvas.clientWidth;
      const hgt = this.canvas.clientHeight;
      const wx = this.cx + (at.x - w / 2) / this.scale;
      const wz = this.cz + (at.y - hgt / 2) / this.scale;
      this.cx = wx - (at.x - w / 2) / next;
      this.cz = wz - (at.y - hgt / 2) / next;
    }
    this.scale = next;
    this.refresh();
  }

  private onDown(e: PointerEvent): void {
    this.drag = { x: e.clientX, y: e.clientY, cx: this.cx, cz: this.cz, id: e.pointerId };
    this.canvas.setPointerCapture(e.pointerId);
    this.canvas.classList.add("dragging");
  }

  private onMove(e: PointerEvent): void {
    const d = this.drag;
    if (!d || d.id !== e.pointerId) return;
    this.cx = d.cx - (e.clientX - d.x) / this.scale;
    this.cz = d.cz - (e.clientY - d.y) / this.scale;
    this.refresh();
  }

  private onUp(e: PointerEvent): void {
    if (this.drag?.id !== e.pointerId) return;
    this.drag = null;
    this.canvas.classList.remove("dragging");
  }

  private onWheel(e: WheelEvent): void {
    e.preventDefault();
    const r = this.canvas.getBoundingClientRect();
    this.zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, { x: e.clientX - r.left, y: e.clientY - r.top });
  }

  // ---------------------------------------------------------------- drawing

  private draw(model: MapModel, view: MapView, me: ReturnType<MapWindowOptions["me"]>, friends: readonly FriendPlace[], marks: readonly MapMark[], party: readonly PartyMapMember[] = []): void {
    const c = this.canvas;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = c.clientWidth;
    const hgt = c.clientHeight;
    if (w === 0 || hgt === 0) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(hgt * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(hgt * dpr);
    }
    const g = c.getContext("2d");
    if (!g) return;
    const k = this.inks;
    const s = this.scale;
    const sx = (x: number) => w / 2 + (x - this.cx) * s;
    // The scene is right-handed (x east, -z north): north up the page, east to the right, unmirrored.
    const sy = (z: number) => hgt / 2 + (z - this.cz) * s;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = k.parchment;
    g.fillRect(0, 0, w, hgt);

    // The places: fog first, ink over it.
    const rect = (sh: MapView["shapes"][number]): [number, number, number, number] => [sx(sh.min[0]), sy(sh.min[1]), (sh.max[0] - sh.min[0]) * s, (sh.max[1] - sh.min[1]) * s];
    const inked = new Map<string, MapView["shapes"]>();
    for (const sh of view.shapes) {
      if (!sh.fog) {
        const list = inked.get(sh.place);
        if (list) list.push(sh);
        else inked.set(sh.place, [sh]);
        continue;
      }
      const [x0, y0, rw, rh] = rect(sh);
      g.globalAlpha = 0.45;
      g.fillStyle = k.fog;
      g.fillRect(x0, y0, rw, rh);
      g.globalAlpha = 1;
    }
    // A place of several surfaces reads as one room (review fix): its outlines first, then its
    // floor over them, so the seams between its own surfaces vanish and its outer edge stays.
    for (const shapes of inked.values()) {
      for (const sh of shapes) {
        const [x0, y0, rw, rh] = rect(sh);
        g.setLineDash(sh.secret ? [4, 3] : []);
        g.strokeStyle = sh.secret ? k.secret : sh.kind === "grounds" ? k.faint : k.ink;
        g.lineWidth = sh.kind === "door" ? 1.5 : 2.5;
        g.strokeRect(x0, y0, rw, rh);
        g.setLineDash([]);
      }
      for (const sh of shapes) {
        if (sh.kind === "grounds") continue;
        const [x0, y0, rw, rh] = rect(sh);
        g.fillStyle = k.room;
        g.fillRect(x0, y0, rw, rh);
        if (sh.kind === "stair") this.hatch(g, x0, y0, rw, rh, k.stair);
      }
    }

    // Place names, then the marks' words, each moved clear of those already written.
    g.textBaseline = "middle";
    const taken: Box[] = [];
    for (const l of view.labels) {
      g.font = l.secret ? "italic 600 12px 'EB Garamond', Georgia, serif" : "600 12px 'EB Garamond', Georgia, serif";
      g.fillStyle = l.secret ? k.secret : k.ink;
      writeClear(g, taken, l.text, sx(l.x), sy(l.z), "center");
    }

    // Key places and class doors on this floor.
    const named = new Set(view.labels.map((l) => l.text));
    for (const m of marks) {
      const surf = m.surfaceId ? model.surface(m.surfaceId) : undefined;
      if (!surf || !onLayer(surf, this.layer)) continue;
      const x = sx(m.x);
      const y = sy(m.z);
      g.fillStyle = m.kind === "class" ? k.door : k.place;
      g.beginPath();
      g.moveTo(x, y - 5);
      g.lineTo(x + 5, y);
      g.lineTo(x, y + 5);
      g.lineTo(x - 5, y);
      g.closePath();
      g.fill();
      // A place already named on the map ("Great Hall") keeps just its mark; class doors always say their lesson.
      if (!m.note && named.has(m.name)) continue;
      g.font = "600 11px 'EB Garamond', Georgia, serif";
      writeClear(g, taken, m.note ? `${m.name} · ${m.note}` : m.name, x + 8, y, "left");
    }

    // Friends by the place they are in.
    for (const p of model.friendPins(friends, this.layer)) {
      const x = sx(p.x);
      const y = sy(p.z);
      g.fillStyle = k.friend;
      g.beginPath();
      g.arc(x, y, 5, 0, Math.PI * 2);
      g.fill();
      g.font = "600 11px 'EB Garamond', Georgia, serif";
      writeClear(g, taken, p.name, x, y - 12, "center");
    }

    // The party: sky blue ringed in navy, named (and where, at an entrance).
    for (const p of model.partyPins(party, this.layer)) {
      const x = sx(p.x);
      const y = sy(p.z);
      g.fillStyle = k.party;
      g.strokeStyle = k.partyRing;
      g.lineWidth = 2;
      g.beginPath();
      g.arc(x, y, 6, 0, Math.PI * 2);
      g.fill();
      g.stroke();
      g.font = "700 11px 'EB Garamond', Georgia, serif";
      g.fillStyle = k.partyRing;
      writeClear(g, taken, p.note ? `${p.name} · ${p.note}` : p.name, x, y - 13, "center");
    }

    // You.
    const mine = me?.surfaceId ? model.surface(me.surfaceId) : undefined;
    if (me && mine && onLayer(mine, this.layer)) {
      const x = sx(me.x);
      const y = sy(me.z);
      g.strokeStyle = k.you;
      g.fillStyle = k.you;
      g.lineWidth = 2;
      g.beginPath();
      g.arc(x, y, 8, 0, Math.PI * 2);
      g.stroke();
      // The way we face (yaw 0 = +z: down the page).
      g.beginPath();
      g.moveTo(x + Math.sin(me.yaw) * 7, y + Math.cos(me.yaw) * 7);
      g.lineTo(x + Math.sin(me.yaw + 2.5) * 5, y + Math.cos(me.yaw + 2.5) * 5);
      g.lineTo(x + Math.sin(me.yaw - 2.5) * 5, y + Math.cos(me.yaw - 2.5) * 5);
      g.closePath();
      g.fill();
    }

    const places = new Set(view.shapes.map((x) => x.place));
    const shown = new Set(view.shapes.filter((x) => !x.fog).map((x) => x.place));
    this.status.textContent = `${layerName(this.layer)}: ${shown.size} of ${places.size} places explored. The moving staircases are drawn where they stand now. Drag to look around; scroll to zoom.`;
  }

  /** Steps across a stair. */
  private hatch(g: CanvasRenderingContext2D, x: number, y: number, w: number, hgt: number, colour: string): void {
    g.save();
    g.beginPath();
    g.rect(x, y, w, hgt);
    g.clip();
    g.strokeStyle = colour;
    g.lineWidth = 0.75;
    g.beginPath();
    const step = Math.max(3, this.scale * 0.6);
    if (w >= hgt) for (let i = x; i <= x + w; i += step) (g.moveTo(i, y), g.lineTo(i, y + hgt));
    else for (let j = y; j <= y + hgt; j += step) (g.moveTo(x, j), g.lineTo(x + w, j));
    g.stroke();
    g.restore();
  }
}
