// Choosing the piece of furniture for Elevara. Casting it (V, or
// the spellbook) in a dorm room starts targeting: the pieces within the
// spell's range are candidates, the one most in front of the camera is
// picked first and outlined, Tab or the arrow keys step through the others
// (nearest first), a click picks one under the cursor, and Enter, V or E
// casts. Escape, walking out of range or leaving the room cancels. A hint
// under the outline says what is targeted and which keys do what.

import { CreatePickingRay } from "@babylonjs/core/Culling/ray.core";
import "@babylonjs/core/Culling/ray";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { Scene } from "@babylonjs/core/scene";
import { furnitureDef } from "../dorm/catalog";
import { itemBox, type Box } from "../dorm/placement";
import type { DormRoom, RoomItem } from "../dorm/room";
import { h } from "../../ui/dom";
import { LEVITATION, spellRgb } from "./data";

const RANGE_M = LEVITATION.social.kind === "levitate" ? LEVITATION.social.range_m : 6;
/** A press and release this close (px, ms) is a click, not a camera drag. */
const CLICK_SLOP_PX = 6;
const CLICK_MS = 400;
const REFRESH_S = 0.25;
/** Kept off the spell's reach, for the player still walking while the cast travels (m). */
const RANGE_MARGIN_M = 0.25;

/** A candidate: the item, its box and how far it is from the player (m). */
export interface Candidate {
  item: RoomItem;
  box: Box;
  distance: number;
  /** How squarely in front of the camera it is (1 straight ahead, -1 behind). */
  facing: number;
}

/**
 * The pieces in `room` within `range` of `feet`, nearest first, and the
 * index of the one to pick first: the most squarely in front of the camera
 * (`look`, horizontal), nearest on a tie. Pure, unit tested.
 */
export function candidates(room: Iterable<RoomItem>, feet: readonly [number, number, number], look: readonly [number, number], range = RANGE_M): { list: Candidate[]; first: number } {
  const list: Candidate[] = [];
  const ll = Math.hypot(look[0], look[1]) || 1;
  for (const item of room) {
    const def = furnitureDef(item.itemDef);
    if (!def) continue;
    const box = itemBox(def, item.pos, item.yawUnits);
    const cx = (box.min[0] + box.max[0]) / 2;
    const cz = (box.min[2] + box.max[2]) / 2;
    // From the piece's origin, as the server measures the spell's reach (a
    // little short of it, so a piece at the very edge is not offered).
    const distance = Math.hypot(item.pos[0] - feet[0], item.pos[2] - feet[2]);
    if (distance > range - RANGE_MARGIN_M) continue;
    const ox = cx - feet[0];
    const oz = cz - feet[2];
    const ol = Math.hypot(ox, oz);
    const facing = ol < 0.01 ? 1 : (ox * look[0] + oz * look[1]) / (ol * ll);
    list.push({ item, box, distance, facing });
  }
  list.sort((a, b) => a.distance - b.distance || a.item.itemId - b.item.itemId);
  let first = -1;
  for (let i = 0; i < list.length; i++) {
    const c = list[i]!;
    if (first < 0 || c.facing > list[first]!.facing + 0.15 || (Math.abs(c.facing - list[first]!.facing) <= 0.15 && c.distance < list[first]!.distance)) first = i;
  }
  return { list, first };
}

export interface TargetingHooks {
  scene: Scene;
  canvas: HTMLCanvasElement;
  camera: Camera;
  hudRoot: HTMLElement;
  room: () => DormRoom;
  feet: () => [number, number, number];
  /** The camera's horizontal look direction (x, z). */
  look: () => [number, number];
  /** Cast at the chosen piece. */
  cast: (itemId: number) => void;
  notify: (text: string) => void;
}

export class LevitationTargeting {
  private list: Candidate[] = [];
  private selected: number | null = null;
  private outline: Mesh | null = null;
  private material: StandardMaterial | null = null;
  private readonly hint: HTMLElement;
  private readonly hintName: HTMLElement;
  private refresh = 0;
  private press: { x: number; y: number; t: number } | null = null;
  private readonly onDown = (e: PointerEvent) => {
    if (e.button === 0) this.press = { ...this.local(e), t: performance.now() };
  };
  private readonly onUp = (e: PointerEvent) => this.pointerUp(e);

  constructor(private readonly hooks: TargetingHooks) {
    this.hintName = h("strong", {}, "");
    this.hint = h(
      "div",
      { class: "spell-target panel", role: "status", hidden: true },
      h("span", { class: "spell-target-spell" }, LEVITATION.name),
      this.hintName,
      h("span", { class: "spell-target-keys" }, "Tab next · Enter cast · Esc cancel · or click a piece"),
    );
    hooks.hudRoot.append(this.hint);
  }

  get active(): boolean {
    return this.outline !== null;
  }

  /** Starts targeting; false (with a note) when no piece is in range. */
  start(): boolean {
    if (this.active) return true;
    const found = candidates(this.hooks.room().items.values(), this.hooks.feet(), this.hooks.look());
    if (!found.list.length) {
      this.hooks.notify(`There is no furniture within ${RANGE_M} m to levitate.`);
      return false;
    }
    this.list = found.list;
    this.selected = found.list[found.first]!.item.itemId;
    const box = CreateBox("levitate-target", { size: 1 }, this.hooks.scene);
    const mat = new StandardMaterial("levitate-target", this.hooks.scene);
    const [r, g, b] = spellRgb(LEVITATION);
    mat.wireframe = true;
    mat.disableLighting = true;
    mat.emissiveColor = new Color3(r, g, b);
    box.material = mat;
    box.isPickable = false;
    this.outline = box;
    this.material = mat;
    this.hooks.canvas.addEventListener("pointerdown", this.onDown);
    this.hooks.canvas.addEventListener("pointerup", this.onUp);
    this.hint.hidden = false;
    this.place();
    return true;
  }

  /** Characters look at their target: the middle of the piece aimed at, into `out`; false when none. */
  aimPoint(out: { x: number; y: number; z: number }): boolean {
    const c = this.active ? this.list.find((x) => x.item.itemId === this.selected) : undefined;
    if (!c) return false;
    const b = c.box;
    out.x = (b.min[0] + b.max[0]) / 2;
    out.y = (b.min[1] + b.max[1]) / 2;
    out.z = (b.min[2] + b.max[2]) / 2;
    return true;
  }

  stop(): void {
    if (!this.active) return;
    this.hooks.canvas.removeEventListener("pointerdown", this.onDown);
    this.hooks.canvas.removeEventListener("pointerup", this.onUp);
    this.outline?.dispose();
    this.material?.dispose();
    this.outline = null;
    this.material = null;
    this.list = [];
    this.selected = null;
    this.hint.hidden = true;
  }

  /** Per frame: the candidates follow the player; out of range ends it. */
  update(dt: number): void {
    if (!this.active) return;
    this.refresh -= dt;
    if (this.refresh > 0) return;
    this.refresh = REFRESH_S;
    this.list = candidates(this.hooks.room().items.values(), this.hooks.feet(), this.hooks.look()).list;
    if (!this.list.some((c) => c.item.itemId === this.selected)) {
      if (!this.list.length) {
        this.stop();
        this.hooks.notify("You are too far from the furniture now.");
        return;
      }
      this.selected = this.list[0]!.item.itemId;
    }
    this.place();
  }

  handleKey(e: KeyboardEvent): boolean {
    if (!this.active) return false;
    switch (e.code) {
      case "Tab":
      case "ArrowRight":
      case "ArrowDown":
        this.step(e.shiftKey && e.code === "Tab" ? -1 : 1);
        return true;
      case "ArrowLeft":
      case "ArrowUp":
        this.step(-1);
        return true;
      case "Enter":
      case "NumpadEnter":
      case "KeyV":
      case "KeyE":
        this.castSelected();
        return true;
      case "Escape":
        this.stop();
        return true;
      default:
        return false;
    }
  }

  private step(dir: 1 | -1): void {
    if (!this.list.length) return;
    const i = this.list.findIndex((c) => c.item.itemId === this.selected);
    const next = (i + dir + this.list.length) % this.list.length;
    this.selected = this.list[next]!.item.itemId;
    this.place();
  }

  private castSelected(): void {
    const id = this.selected;
    this.stop();
    if (id !== null) this.hooks.cast(id);
  }

  private place(): void {
    const c = this.list.find((x) => x.item.itemId === this.selected);
    if (!c || !this.outline) return;
    const b = c.box;
    this.outline.position.set((b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2);
    this.outline.scaling.set(b.max[0] - b.min[0] + 0.06, b.max[1] - b.min[1] + 0.06, b.max[2] - b.min[2] + 0.06);
    const name = furnitureDef(c.item.itemDef)?.name ?? "This piece";
    if (this.hintName.textContent !== name) this.hintName.textContent = name;
  }

  private local(e: PointerEvent): { x: number; y: number } {
    const r = this.hooks.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  private pointerUp(e: PointerEvent): void {
    const p = this.press;
    this.press = null;
    if (e.button !== 0 || !p) return;
    const at = this.local(e);
    if (Math.hypot(at.x - p.x, at.y - p.y) > CLICK_SLOP_PX || performance.now() - p.t > CLICK_MS) return;
    const r = CreatePickingRay(this.hooks.scene, at.x, at.y, null, this.hooks.camera);
    const hit = this.hooks.room().pick({ origin: [r.origin.x, r.origin.y, r.origin.z], dir: [r.direction.x, r.direction.y, r.direction.z] });
    if (!hit) return;
    if (!this.list.some((c) => c.item.itemId === hit.itemId)) {
      this.hooks.notify(`That piece is too far away: ${LEVITATION.name} reaches ${RANGE_M} m.`);
      return;
    }
    this.selected = hit.itemId;
    this.castSelected();
  }

  dispose(): void {
    this.stop();
    this.hint.remove();
  }
}
