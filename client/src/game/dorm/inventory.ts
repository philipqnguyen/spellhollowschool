// The decorating list's furniture: the owner's furniture items still in the
// inventory (`GET /api/inventory?kind=furniture&location=inventory`, newest
// first, keyset-paginated). The list reads every page (furniture-list.ts
// `loadAllFurniture`) and shows them all, grouped, searchable and filtered.
//
// The server moves items between the inventory and the room in the same
// transaction as the placement, so after each confirmed edit the list simply
// reloads; items sent but not yet confirmed are not available.

import { authedRequest } from "../../api";
import type { Page } from "../../ui/friends-api";

export interface FurnitureItem {
  /** `items.id`. */
  id: number;
  itemDef: number;
  dyes: [number, number, number];
}

/** Items per request: the server's most (`characters::page::MAX_LIMIT`). */
export const TRUNK_PAGE = 100;

interface RawItem {
  id: number;
  item_def: number;
  dyes: [number, number, number];
}

export async function listFurniture(after: string | null, limit = TRUNK_PAGE): Promise<Page<FurnitureItem>> {
  const q = new URLSearchParams({ kind: "furniture", location: "inventory", limit: String(limit) });
  if (after) q.set("after", after);
  const r = await authedRequest<{ items: RawItem[]; next: string | null }>("GET", `/inventory?${q}`);
  return { items: r.items.map((i) => ({ id: i.id, itemDef: i.item_def, dyes: i.dyes })), next: r.next ?? null };
}
