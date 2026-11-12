// The decorating list (docs/architecture.md, "Dorms and decorating"):
// every furniture piece the owner has, one row
// per look (catalog item and dyes) with how many are in the trunk and how
// many they own (trunk plus room), searchable by name and filtered by
// category. Pure (no DOM), unit tested; ui/dorm.ts draws the rows.
//
// The trunk is read whole: `GET /api/inventory?kind=furniture&location=inventory`
// is keyset-paginated, and `loadAllFurniture` follows its `next` cursors,
// so nothing hides on a later page.

import type { Page } from "../../ui/friends-api";
import { CATEGORY_LABELS, furnitureDef as catalogFurniture, type FurnitureCategory, type FurnitureDef } from "./catalog";
import type { FurnitureItem } from "./inventory";
import type { RoomItem } from "./room";

export interface FurnitureRow {
  /** `itemDef:p,s,t`. */
  key: string;
  itemDef: number;
  dyes: [number, number, number];
  name: string;
  category: FurnitureCategory;
  surface: FurnitureDef["surface"];
  /** Trunk items of this look not waiting on an edit: picking the row takes the first. */
  available: FurnitureItem[];
  /** In the trunk and in the room. */
  owned: number;
}

export type CategoryFilter = FurnitureCategory | "all";

/** The list's category order (the filter's too). */
export const CATEGORY_ORDER: readonly FurnitureCategory[] = ["beds", "seating", "tables", "storage", "decor", "lighting", "rugs", "wall"];

export function categoryLabel(c: CategoryFilter): string {
  return c === "all" ? "All furniture" : CATEGORY_LABELS[c];
}

const dyeKey = (itemDef: number, dyes: readonly number[]) => `${itemDef}:${dyes[0]},${dyes[1]},${dyes[2]}`;

/**
 * One row per look the owner has: the trunk's items (`pending` ones, sent
 * and not confirmed, are not available) and the room's, sorted by category
 * then name. Items the catalog does not know as furniture are skipped.
 */
export function buildRows(
  trunk: readonly FurnitureItem[],
  placed: Iterable<RoomItem>,
  pending: ReadonlySet<number>,
  lookup: (itemDef: number) => FurnitureDef | undefined = catalogFurniture,
): FurnitureRow[] {
  const rows = new Map<string, FurnitureRow>();
  const row = (itemDef: number, dyes: [number, number, number]): FurnitureRow | null => {
    const def = lookup(itemDef);
    if (!def) return null;
    const key = dyeKey(itemDef, dyes);
    let r = rows.get(key);
    if (!r) {
      r = { key, itemDef, dyes: [...dyes], name: def.name, category: def.category, surface: def.surface, available: [], owned: 0 };
      rows.set(key, r);
    }
    return r;
  };
  for (const it of trunk) {
    const r = row(it.itemDef, it.dyes);
    if (!r) continue;
    r.owned++;
    if (!pending.has(it.id)) r.available.push(it);
  }
  for (const it of placed) {
    const r = row(it.itemDef, it.dyes);
    if (r) r.owned++;
  }
  const order = (c: FurnitureCategory) => CATEGORY_ORDER.indexOf(c);
  return [...rows.values()].sort((a, b) => order(a.category) - order(b.category) || a.name.localeCompare(b.name) || a.key.localeCompare(b.key));
}

/** Rows whose name (or category) holds every word of `query` (any case), in `category`. */
export function filterRows(rows: readonly FurnitureRow[], query: string, category: CategoryFilter): FurnitureRow[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter((r) => {
    if (category !== "all" && r.category !== category) return false;
    const text = `${r.name} ${CATEGORY_LABELS[r.category]}`.toLowerCase();
    return words.every((w) => text.includes(w));
  });
}

/** The categories the owner has anything in, in list order, with how many looks each. */
export function categoryCounts(rows: readonly FurnitureRow[]): [FurnitureCategory, number][] {
  const counts = new Map<FurnitureCategory, number>();
  for (const r of rows) counts.set(r.category, (counts.get(r.category) ?? 0) + 1);
  return CATEGORY_ORDER.filter((c) => counts.has(c)).map((c) => [c, counts.get(c)!]);
}

/** Pages the trunk is read in, at most (100 items each: a room holds 150). */
export const MAX_TRUNK_PAGES = 20;

/** Every furniture item in the trunk, following the pages' `next` cursors. */
export async function loadAllFurniture(fetchPage: (after: string | null) => Promise<Page<FurnitureItem>>, maxPages = MAX_TRUNK_PAGES): Promise<FurnitureItem[]> {
  const out: FurnitureItem[] = [];
  const seen = new Set<string>();
  let after: string | null = null;
  for (let i = 0; i < maxPages; i++) {
    const page = await fetchPage(after);
    out.push(...page.items);
    if (page.next === null || seen.has(page.next)) break;
    seen.add(page.next);
    after = page.next;
  }
  return out;
}
