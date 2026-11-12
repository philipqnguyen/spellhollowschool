// What is in the dorm room the client stands in: the placed furniture, the
// item cap and the "open to friends" flag, kept current from `DormState` and
// `DormFurniture` (docs/architecture.md, "Dorms and decorating"). Pure (no Babylon, no DOM)
// so it is unit tested; the renderer and decorating mode read it.

import { furnitureTopAreas, placementFits, type FurnitureEdit, type FurniturePiece, type PlaceArea, type PlacedFurniture, type Vec3 } from "../../wasm/sim_wasm";
import { furnitureDef as catalogFurniture, type FurnitureDef } from "./catalog";
import { itemBox, rayHitBox, seatRows, unitsToYaw, yawToUnits, type Ray } from "./placement";

export interface RoomItem {
  itemId: number;
  itemDef: number;
  dyes: [number, number, number];
  pos: Vec3;
  /** Wire yaw units (0-255): placements are exact in these. */
  yawUnits: number;
}

/** What an edit changed, for the renderer and the inventory strip. */
export type RoomChange =
  | { kind: "placed"; item: RoomItem }
  | { kind: "moved"; item: RoomItem }
  | { kind: "removed"; item: RoomItem }
  /** An edit for an item this client does not know (ignored). */
  | { kind: "none" };

export type FurnitureLookup = (itemDef: number) => FurnitureDef | undefined;

export function roomItem(p: PlacedFurniture): RoomItem {
  return { itemId: p.itemId, itemDef: p.itemDef, dyes: [...p.dyes], pos: [...p.pos], yawUnits: yawToUnits(p.yaw) };
}

export class DormRoom {
  readonly items = new Map<number, RoomItem>();
  cap = 150;
  openToFriends = false;
  /** Bumped on every change, so views can tell when to redraw. */
  version = 0;

  constructor(private readonly lookup: FurnitureLookup = catalogFurniture) {}

  /** `DormState`: everything at once (after every arrival in a dorm). */
  reset(state: { openToFriends: boolean; cap: number; items: readonly PlacedFurniture[] }): void {
    this.items.clear();
    for (const p of state.items) this.items.set(p.itemId, roomItem(p));
    this.cap = state.cap;
    this.openToFriends = state.openToFriends;
    this.version++;
  }

  /** Empties the room (when the client leaves it). */
  clear(): void {
    this.items.clear();
    this.openToFriends = false;
    this.version++;
  }

  apply(edit: FurnitureEdit): RoomChange {
    switch (edit.kind) {
      case "placed": {
        const item = roomItem(edit.item);
        this.items.set(item.itemId, item);
        this.version++;
        return { kind: "placed", item };
      }
      case "moved": {
        const old = this.items.get(edit.itemId);
        if (!old) return { kind: "none" };
        const item: RoomItem = { ...old, pos: [...edit.pos], yawUnits: yawToUnits(edit.yaw) };
        this.items.set(item.itemId, item);
        this.version++;
        return { kind: "moved", item };
      }
      case "removed": {
        const old = this.items.get(edit.itemId);
        if (!old) return { kind: "none" };
        this.items.delete(edit.itemId);
        this.version++;
        return { kind: "removed", item: old };
      }
    }
  }

  get count(): number {
    return this.items.size;
  }

  get full(): boolean {
    return this.items.size >= this.cap;
  }

  /** The tops and shelves of every placed piece, by item (rebuilt when the room changes). */
  private topsCache: { version: number; byItem: Map<number, PlaceArea[]> } | null = null;

  private topsByItem(): Map<number, PlaceArea[]> {
    if (this.topsCache?.version === this.version) return this.topsCache.byItem;
    const byItem = new Map<number, PlaceArea[]>();
    for (const it of this.items.values()) {
      const def = this.lookup(it.itemDef);
      if (!def?.hasTops) continue;
      byItem.set(it.itemId, furnitureTopAreas(def.raw, `top:${it.itemId}`, it.pos[0], it.pos[1], it.pos[2], unitsToYaw(it.yawUnits)));
    }
    this.topsCache = { version: this.version, byItem };
    return byItem;
  }

  /**
   * The table areas placed pieces offer (tops and shelves, turning with
   * their piece: `furnitureTopAreas`, the server's own rule), leaving out
   * `exceptId` (the piece being moved cannot stand on itself).
   */
  tableTops(exceptId?: number): PlaceArea[] {
    const out: PlaceArea[] = [];
    for (const [id, areas] of this.topsByItem()) if (id !== exceptId) out.push(...areas);
    return out;
  }

  /**
   * What stands on `itemId`'s top or shelves (table items that fit one of
   * them where they are: `server::dorm::rules::holds_items`). A piece that
   * holds something cannot be moved or put away until it is taken off.
   */
  heldBy(itemId: number): RoomItem[] {
    const tops = this.topsByItem().get(itemId);
    if (!tops?.length) return [];
    const out: RoomItem[] = [];
    for (const it of this.items.values()) {
      if (it.itemId === itemId) continue;
      const def = this.lookup(it.itemDef);
      if (def?.surface !== "table") continue;
      const [x, y, z] = it.pos;
      if (placementFits(tops, def.surface, def.footprint[0], def.footprint[1], def.height, x, y, z, unitsToYaw(it.yawUnits))) out.push(it);
    }
    return out;
  }

  /**
   * Solid furniture: every placed piece as `SimWorld.setFurniture` and
   * `keepsWay` take it (all but `except`, a piece being moved). The wasm
   * side keeps the solid ones (floor pieces taller than a step), as the
   * server does.
   */
  pieces(except?: number): FurniturePiece[] {
    const out: FurniturePiece[] = [];
    for (const it of this.items.values()) {
      if (it.itemId === except) continue;
      const def = this.lookup(it.itemDef);
      if (def) out.push({ id: it.itemId, furniture: def.raw, pos: it.pos, yaw: unitsToYaw(it.yawUnits) });
    }
    return out;
  }

  /** The seat rows (`castle/seats.ts`) of every placed piece with seats. */
  seatRows(): number[] {
    const rows: number[] = [];
    for (const it of this.items.values()) {
      const def = this.lookup(it.itemDef);
      if (def?.seats.length) rows.push(...seatRows(def.seats, it.pos, it.yawUnits));
    }
    return rows;
  }

  /** The placed item nearest along `ray`, for picking one up. */
  pick(ray: Ray): RoomItem | null {
    let best: RoomItem | null = null;
    let bestT = Infinity;
    for (const it of this.items.values()) {
      const def = this.lookup(it.itemDef);
      if (!def) continue;
      const t = rayHitBox(ray, itemBox(def, it.pos, it.yawUnits));
      if (t !== null && t < bestT) {
        best = it;
        bestT = t;
      }
    }
    return best;
  }
}
