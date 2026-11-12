// The wardrobe's HTTP endpoints (docs/architecture.md, "HTTP API"):
// inventory, equipment, dyes and saved outfits. Appearances come
// back in the API's snake_case and are converted to the client's form here.

import { authedRequest } from "../api";
import { fromWire, type WireAppearance } from "../content/appearance";
import type { Dyes, ItemKind } from "../content/catalog";
import type { Appearance, Finish, GearSlot } from "../wasm/sim_wasm";

export type ItemLocation = "inventory" | "equipped" | "dorm";

/** An item instance (`Item` in the API). Kind, slot, name and asset come from the catalog by `item_def`. */
export interface Item {
  id: number;
  item_def: number;
  dyes: Dyes;
  location: ItemLocation;
  /** Where it is worn, when equipped. */
  slot: GearSlot | null;
  created_at: string;
  /** Units in the stack (1 for clothing and furniture), the crafter's name, the brewed finish. */
  quantity?: number;
  maker?: string | null;
  finish?: Finish;
}

export interface Outfit {
  id: number;
  name: string;
  hat: number | null;
  body: number | null;
  legs: number | null;
  shoes: number | null;
  created_at: string;
}

/** A keyset page: pass `next` back as `after`; null means the end. */
export interface Page<T> {
  items: T[];
  next: string | null;
}

export interface Look {
  appearance: Appearance;
  appearanceVersion: number;
}

export interface InventoryQuery {
  kind?: ItemKind;
  slot?: GearSlot;
  location?: ItemLocation;
  limit?: number;
  after?: string | null;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

const look = (r: { appearance: WireAppearance; appearance_version: number }): Look => ({ appearance: fromWire(r.appearance), appearanceVersion: r.appearance_version });

export function inventory(q: InventoryQuery = {}): Promise<Page<Item>> {
  return authedRequest("GET", `/inventory${query({ kind: q.kind, slot: q.slot, location: q.location, limit: q.limit, after: q.after })}`);
}

export interface Equipment {
  hat: Item | null;
  body: Item | null;
  legs: Item | null;
  shoes: Item | null;
  appearance_version: number;
}

export function equipment(): Promise<Equipment> {
  return authedRequest("GET", "/equipment");
}

/** Wears `itemId` in `slot` (null empties it); the previous item goes back to the inventory. */
export async function equip(slot: GearSlot, itemId: number | null): Promise<Look> {
  return look(await authedRequest("POST", "/equipment", { slot, item_id: itemId }));
}

export function dye(itemId: number, dyes: Dyes): Promise<Item> {
  return authedRequest("POST", `/items/${itemId}/dye`, { dyes });
}

/** Applies (and uses up) the finish stack `finishItemId` to clothing item `itemId`. */
export function applyFinish(itemId: number, finishItemId: number): Promise<Item> {
  return authedRequest("POST", `/items/${itemId}/finish`, { finish_item_id: finishItemId });
}

export function outfits(after?: string | null, limit?: number): Promise<Page<Outfit>> {
  return authedRequest("GET", `/outfits${query({ after, limit })}`);
}

/** Saves what is worn now under `name`. */
export function saveOutfit(name: string): Promise<Outfit> {
  return authedRequest("POST", "/outfits", { name });
}

export function renameOutfit(id: number, name: string): Promise<Outfit> {
  return authedRequest("PATCH", `/outfits/${id}`, { name });
}

export function deleteOutfit(id: number): Promise<void> {
  return authedRequest("DELETE", `/outfits/${id}`);
}

/** Wears a saved outfit; `missing` lists slots whose item is gone or placed in the dorm. */
export async function wearOutfit(id: number): Promise<Look & { missing: GearSlot[] }> {
  const r = await authedRequest<{ appearance: WireAppearance; appearance_version: number; missing: GearSlot[] }>("POST", `/outfits/${id}/wear`);
  return { ...look(r), missing: r.missing ?? [] };
}
