// Decorating mode (owner only, in their own room): pick a piece from the
// list or click one in the room, and a ghost follows the cursor over the
// floor, a wall, a table top or a shelf, snapped to the grid and turned as
// it will stand. Click sets it down (`DormPlace` / `DormMove`); Q/E turn it,
// G switches precise placement (5 cm steps, 1/256-turn turning), Del puts a
// lifted piece away (`DormRemove`), Esc cancels. With nothing in hand, the
// placed piece under the cursor is highlighted and the cursor becomes a
// pointer: it can be clicked. A piece with something on its top or shelves
// stays put until that is taken off (the server refuses too: `NotEmpty`).
//
// Nothing changes locally until the server confirms with `DormFurniture`
// (the editor gets the broadcast too), so every client in the room sees the
// same thing; a refused edit comes back as `DormError` and the piece returns.
// Dragging still turns the camera: only a click that hardly moves places.

import { CreatePickingRay } from "@babylonjs/core/Culling/ray.core";
import "@babylonjs/core/Culling/ray";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import { encodeDormMove, encodeDormPlace, encodeDormRemove, type PlaceArea, type SimWorld, type Vec3 } from "../../wasm/sim_wasm";
import { furnitureDef, type FurnitureDef } from "./catalog";
import type { FurnitureRenderer } from "./furniture";
import type { FurnitureItem } from "./inventory";
import { pickPlacement, rotateUnits, unitsToYaw, type Placement, type Ray } from "./placement";
import type { DormRoom } from "./room";

/** A click is a press and release this close together (px, ms). */
const CLICK_SLOP_PX = 6;
const CLICK_MS = 600;

export interface Selection {
  itemId: number;
  itemDef: number;
  dyes: [number, number, number];
  /** Already in the room (being moved) rather than from the inventory. */
  existing: boolean;
}

export interface DecorateHooks {
  /** The room's world, for the placement rule and its areas. */
  sim(): SimWorld;
  /** Where everyone in the room stands (ourselves too): solid pieces may not go on anyone. */
  people(): Vec3[];
  send(frame: Uint8Array): void;
  /** A short message for the strip ("That doesn't fit there."). */
  notify(text: string): void;
  /** Selection, rotation, precise mode or pending edits changed: redraw the panel. */
  onChange(): void;
}

export class DecorateMode {
  active = false;
  selection: Selection | null = null;
  yawUnits = 0;
  /** Precise placement: the 5 cm grid and single-unit (1/256 turn) rotation. */
  precise = false;
  /** The placed piece under the cursor while nothing is in hand (highlighted, clickable). */
  hovered: number | null = null;
  /** Items with an edit sent and not yet confirmed. */
  readonly pending = new Set<number>();
  private placement: Placement | null = null;
  private valid = false;
  private pointer: { x: number; y: number } | null = null;
  private press: { x: number; y: number; t: number } | null = null;
  private layoutAreas: PlaceArea[] | null = null;
  private readonly listeners: [string, (e: PointerEvent) => void][];

  constructor(
    private readonly scene: Scene,
    private readonly canvas: HTMLCanvasElement,
    private readonly camera: Camera,
    private readonly room: DormRoom,
    private readonly renderer: FurnitureRenderer,
    private readonly hooks: DecorateHooks,
  ) {
    this.listeners = [
      ["pointermove", (e) => (this.pointer = this.local(e))],
      ["pointerdown", (e) => e.button === 0 && (this.press = { ...this.local(e), t: performance.now() })],
      ["pointerup", (e) => this.onPointerUp(e)],
      ["pointerleave", () => (this.pointer = null)],
    ];
  }

  /** The event's position in canvas CSS pixels (what `CreatePickingRay` takes). */
  private local(e: PointerEvent): { x: number; y: number } {
    const r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  start(): void {
    if (this.active) return;
    this.active = true;
    this.layoutAreas = null;
    for (const [type, fn] of this.listeners) this.canvas.addEventListener(type, fn as EventListener);
    this.hooks.onChange();
  }

  stop(): void {
    if (!this.active) return;
    this.cancel();
    this.active = false;
    for (const [type, fn] of this.listeners) this.canvas.removeEventListener(type, fn as EventListener);
    this.renderer.showGhost(null, [], null, 0, false);
    this.setHover(null);
    this.hooks.onChange();
  }

  /** Picks a piece from the decorating list to place. */
  selectFromInventory(item: FurnitureItem): void {
    if (this.pending.has(item.id)) return;
    if (this.room.full) {
      this.hooks.notify(`Your room holds at most ${this.room.cap} pieces. Put something away first.`);
      return;
    }
    this.cancel();
    this.selection = { itemId: item.id, itemDef: item.itemDef, dyes: item.dyes, existing: false };
    this.renderer.preload(item.itemDef);
    this.hooks.onChange();
  }

  /** Lifts a placed piece to move it. */
  private selectPlaced(itemId: number): void {
    const it = this.room.items.get(itemId);
    if (!it || this.pending.has(itemId)) return;
    const held = this.room.heldBy(itemId);
    if (held.length > 0) {
      const what = [...new Set(held.map((h) => furnitureDef(h.itemDef)?.name ?? "piece"))].join(", ");
      this.hooks.notify(`Take the ${what} off the ${furnitureDef(it.itemDef)?.name ?? "piece"} first.`);
      return;
    }
    this.cancel();
    this.setHover(null);
    this.selection = { itemId, itemDef: it.itemDef, dyes: it.dyes, existing: true };
    this.yawUnits = it.yawUnits;
    this.renderer.setHidden(itemId, true);
    this.hooks.onChange();
  }

  /** Drops the selection; a lifted piece goes back where it was. */
  cancel(): void {
    const s = this.selection;
    if (!s) return;
    this.selection = null;
    if (s.existing && !this.pending.has(s.itemId)) this.renderer.setHidden(s.itemId, false);
    this.renderer.showGhost(null, [], null, 0, false);
    this.hooks.onChange();
  }

  rotate(dir: 1 | -1): void {
    this.yawUnits = rotateUnits(this.yawUnits, dir, this.precise);
    this.hooks.onChange();
  }

  togglePrecise(): void {
    this.precise = !this.precise;
    this.hooks.onChange();
  }

  /** Sends the lifted piece back to the inventory. */
  putAway(): void {
    const s = this.selection;
    if (!s?.existing) return;
    this.send(s.itemId, encodeDormRemove(s.itemId));
    this.selection = null;
    this.renderer.showGhost(null, [], null, 0, false);
    this.hooks.onChange();
  }

  /** `DormFurniture` for `itemId` was applied to the room. */
  confirmed(itemId: number): void {
    if (!this.pending.delete(itemId)) return;
    if (this.selection?.itemId !== itemId) this.renderer.setHidden(itemId, false);
    this.hooks.onChange();
  }

  /** `DormError` for `itemId`: the piece stays where it was. */
  failed(itemId: number | undefined): void {
    if (itemId === undefined) return;
    this.pending.delete(itemId);
    if (this.selection?.itemId !== itemId) this.renderer.setHidden(itemId, false);
    this.hooks.onChange();
  }

  /**
   * A fresh `DormState` (after a reconnect): edits still in flight were lost
   * with the old connection, so nothing is pending any more.
   */
  resync(): void {
    for (const id of this.pending) if (this.selection?.itemId !== id) this.renderer.setHidden(id, false);
    this.pending.clear();
    this.hooks.onChange();
  }

  /** Per frame: follows the cursor (the camera may move under a still cursor). */
  update(): void {
    if (!this.active) return;
    const s = this.selection;
    const def = s ? furnitureDef(s.itemDef) : undefined;
    // Nothing in hand: the placed piece under the cursor is highlighted (with a piece in hand, none is).
    this.setHover(!s && this.pointer ? (this.room.pick(this.ray(this.pointer.x, this.pointer.y))?.itemId ?? null) : null);
    if (!s || !def || !this.pointer) {
      this.placement = null;
      this.renderer.showGhost(null, [], null, 0, false);
      return;
    }
    const ray = this.ray(this.pointer.x, this.pointer.y);
    const areas = [...this.areas(), ...this.room.tableTops(s.existing ? s.itemId : undefined)];
    const p = pickPlacement(ray, areas, def, this.yawUnits, this.precise);
    this.placement = p;
    this.valid = p !== null && this.allowed(def, p, s);
    this.renderer.showGhost(s.itemDef, s.dyes, p?.pos ?? null, p?.yawUnits ?? 0, this.valid);
  }

  /**
   * The server's rule, as `SimWorld.canPlace` previews it, and the solid
   * furniture rule (`SimWorld.keepsWay`: not on anyone or the entry, the way
   * out and to everyone open), checked again only when the ghost moves.
   */
  private allowed(def: FurnitureDef, p: Placement, s: Selection): boolean {
    try {
      const [x, y, z] = p.pos;
      const yaw = unitsToYaw(p.yawUnits);
      const except = s.existing ? s.itemId : undefined;
      const tops = this.room.tableTops(except);
      if (!this.hooks.sim().canPlace(def.surface, def.footprint[0], def.footprint[1], def.height, x, y, z, yaw, tops)) return false;
      const people = this.hooks.people();
      const key = `${s.itemId}:${x}:${y}:${z}:${p.yawUnits}:${this.room.version}:${people.map((q) => q.map((v) => v.toFixed(1)).join(",")).join(";")}`;
      if (this.wayCache?.key !== key) {
        const ok = this.hooks.sim().keepsWay(this.room.pieces(except), { id: s.itemId, furniture: def.raw, pos: [x, y, z], yaw }, people);
        this.wayCache = { key, ok };
      }
      return this.wayCache.ok;
    } catch {
      return false;
    }
  }

  /** The last `keepsWay` answer (the flood is cheap but the pointer moves every frame). */
  private wayCache: { key: string; ok: boolean } | null = null;

  private areas(): PlaceArea[] {
    this.layoutAreas ??= this.hooks.sim().placeAreas();
    return this.layoutAreas;
  }

  private ray(x: number, y: number): Ray {
    const r = CreatePickingRay(this.scene, x, y, null, this.camera);
    return { origin: [r.origin.x, r.origin.y, r.origin.z], dir: [r.direction.x, r.direction.y, r.direction.z] };
  }

  private onPointerUp(e: PointerEvent): void {
    const p = this.press;
    this.press = null;
    if (e.button !== 0 || !p) return;
    const at = this.local(e);
    if (Math.hypot(at.x - p.x, at.y - p.y) > CLICK_SLOP_PX || performance.now() - p.t > CLICK_MS) return;
    this.pointer = at;
    this.click();
  }

  private click(): void {
    const s = this.selection;
    if (!s) {
      const hit = this.pointer ? this.room.pick(this.ray(this.pointer.x, this.pointer.y)) : null;
      if (hit) this.selectPlaced(hit.itemId);
      return;
    }
    this.update();
    const p = this.placement;
    if (!p || !this.valid) {
      this.hooks.notify(p ? "That doesn't fit there." : "Point at the floor, a wall, or a table top or shelf it fits on.");
      return;
    }
    const yaw = unitsToYaw(p.yawUnits);
    const frame = s.existing ? encodeDormMove(s.itemId, p.pos[0], p.pos[1], p.pos[2], yaw) : encodeDormPlace(s.itemId, p.pos[0], p.pos[1], p.pos[2], yaw);
    this.send(s.itemId, frame);
    // A lifted piece stays hidden until the server's `Moved` puts it in its new place.
    this.selection = null;
    this.renderer.showGhost(null, [], null, 0, false);
    this.hooks.onChange();
  }

  private send(itemId: number, frame: Uint8Array): void {
    this.pending.add(itemId);
    this.hooks.send(frame);
  }

  /** Decorating keys; returns true when the key was used. */
  handleKey(e: KeyboardEvent): boolean {
    if (!this.active) return false;
    switch (e.code) {
      case "KeyQ":
        if (!this.selection) return false;
        this.rotate(1);
        return true;
      case "KeyE":
        if (!this.selection) return false;
        this.rotate(-1);
        return true;
      case "KeyG":
        this.togglePrecise();
        return true;
      case "Delete":
      case "Backspace":
        if (!this.selection?.existing) return false;
        this.putAway();
        return true;
      case "Escape":
        if (this.selection) this.cancel();
        else this.stop();
        return true;
    }
    return false;
  }

  /** Highlights the placed piece under the cursor (null: none) and shows a pointer over it. */
  private setHover(itemId: number | null): void {
    if (itemId !== null && this.pending.has(itemId)) itemId = null;
    if (itemId === this.hovered) return;
    this.hovered = itemId;
    this.renderer.setHighlight(itemId);
    // Babylon writes the scene's default cursor onto the canvas on every pointer move.
    const cursor = itemId === null ? "" : "pointer";
    this.scene.defaultCursor = cursor;
    this.canvas.style.cursor = cursor;
  }

  dispose(): void {
    this.stop();
  }
}
