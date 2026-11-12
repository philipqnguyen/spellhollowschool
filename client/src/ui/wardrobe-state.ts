// The wardrobe window's state and rules, without the DOM: paged item lists,
// filters, the selected item and its unsaved dyes, what the mirror shows,
// and how a reply from the server updates what is on screen. Pure, unit
// tested in wardrobe-state.test.ts.

import { cloneAppearance } from "../content/appearance";
import { CATALOG, GEAR_SLOTS, bodyCanWear, clothingDef, isPaletteIndex, itemDef, styleTags, wearLabel, wearRefusal, type Catalog, type Dyes, type ItemDef, type ItemKind } from "../content/catalog";
import type { Appearance, GearSlot } from "../wasm/sim_wasm";
import type { InventoryQuery, Item, Outfit, Page } from "./wardrobe-api";

export type WardrobeTab = "clothing" | "furniture" | "outfits";
export type SlotFilter = "all" | GearSlot;

export const SLOT_LABELS: Record<GearSlot, string> = { hat: "Hat", body: "Body", legs: "Legs", shoes: "Shoes" };
export const DYE_CHANNELS = ["Primary", "Secondary", "Trim"] as const;
export const PAGE_SIZE = 24;
export const OUTFIT_NAME_MAX = 32;

/** A list loaded a page at a time. */
export interface Paged<T> {
  items: T[];
  next: string | null;
  /** Whether the first page has arrived. */
  loaded: boolean;
}

export const emptyPaged = <T>(): Paged<T> => ({ items: [], next: null, loaded: false });

/** Appends a page, skipping ids already listed (a page boundary can shift while items change). */
export function mergePage<T extends { id: number }>(list: Paged<T>, page: Page<T>): Paged<T> {
  const seen = new Set(list.items.map((i) => i.id));
  return { items: [...list.items, ...page.items.filter((i) => !seen.has(i.id))], next: page.next, loaded: true };
}

/** Replaces the entry with `item.id` (or leaves the list as it is). */
export function replaceItem<T extends { id: number }>(list: Paged<T>, item: T): Paged<T> {
  return { ...list, items: list.items.map((i) => (i.id === item.id ? item : i)) };
}

export function removeItem<T extends { id: number }>(list: Paged<T>, id: number): Paged<T> {
  return { ...list, items: list.items.filter((i) => i.id !== id) };
}

/** The inventory query for a tab and slot filter. */
export function inventoryQuery(tab: WardrobeTab, slot: SlotFilter, after: string | null): InventoryQuery {
  const kind: ItemKind = tab === "furniture" ? "furniture" : "clothing";
  return { kind, slot: tab === "clothing" && slot !== "all" ? slot : undefined, limit: PAGE_SIZE, after };
}

/** What is worn per slot, from the equipped items we know of. */
export type Worn = Record<GearSlot, Item | null>;

export const nothingWorn = (): Worn => ({ hat: null, body: null, legs: null, shoes: null });

/**
 * After `slot` was set to `item` (null = taken off): the new item is worn,
 * the one it replaced is back in the inventory, everywhere they are listed.
 */
export function afterEquip(list: Paged<Item>, worn: Worn, slot: GearSlot, item: Item | null): { list: Paged<Item>; worn: Worn } {
  const previous = worn[slot];
  const nextWorn = { ...worn, [slot]: item ? { ...item, location: "equipped" as const, slot } : null };
  const items = list.items.map((i) => {
    if (item && i.id === item.id) return { ...i, location: "equipped" as const, slot };
    if (previous && i.id === previous.id) return { ...i, location: "inventory" as const, slot: null };
    return i;
  });
  return { list: { ...list, items }, worn: nextWorn };
}

/** The catalog entry of an item instance (undefined for an id the catalog dropped). */
export function defOf(item: Pick<Item, "item_def">, catalog: Catalog = CATALOG): ItemDef | undefined {
  return itemDef(item.item_def, catalog);
}

/** The slot a clothing item is worn in. */
export function slotOf(item: Pick<Item, "item_def">, catalog: Catalog = CATALOG): GearSlot | null {
  const def = defOf(item, catalog);
  return def?.kind === "clothing" ? (def.slot ?? null) : null;
}

/** Display line under an item's name: slot or surface, then its style tags. */
export function itemSubtitle(item: Pick<Item, "item_def">, catalog: Catalog = CATALOG): string {
  const def = defOf(item, catalog);
  if (!def) return "Unknown item";
  const kind = def.kind === "clothing" ? SLOT_LABELS[def.slot ?? "body"] : def.furniture ? `${def.furniture.surface[0]!.toUpperCase()}${def.furniture.surface.slice(1)} piece` : "Furniture";
  const tags = styleTags(def);
  return tags.length ? `${kind} · ${tags.join(", ")}` : kind;
}

/** Whether the server would accept these dyes (all inside the palette). */
export function validDyes(dyes: readonly number[]): boolean {
  return dyes.length === 3 && dyes.every((d) => isPaletteIndex(d));
}

export function sameDyes(a: readonly number[], b: readonly number[]): boolean {
  return a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
}

/** `dyes` with channel `channel` set to palette index `colour`. */
export function withDye(dyes: Dyes, channel: number, colour: number): Dyes {
  const next: Dyes = [dyes[0], dyes[1], dyes[2]];
  next[channel] = colour;
  return next;
}

/**
 * What the mirror shows: our appearance, with the selected clothing item
 * tried on in its slot in its (possibly unsaved) dyes. Furniture and unknown
 * items change nothing.
 */
export function mirrorAppearance(base: Appearance, selected: Item | null, dyes: Dyes | null, catalog: Catalog = CATALOG): Appearance {
  if (!selected) return base;
  const slot = slotOf(selected, catalog);
  const def = slot ? clothingDef(selected.item_def, slot, catalog) : undefined;
  // A piece the body may not wear is not tried on either.
  if (!slot || !def || !bodyCanWear(base.bodyType, def)) return base;
  const a = cloneAppearance(base);
  const d = dyes ?? selected.dyes;
  a.outfit[slot] = { itemDef: selected.item_def, dyes: [d[0], d[1], d[2]], ...(selected.finish && selected.finish !== "none" ? { finish: selected.finish } : {}) };
  return a;
}

/** Whether the mirror is showing something other than what is worn. */
export function isTryingOn(worn: Worn, selected: Item | null, dyes: Dyes | null): boolean {
  if (!selected || !slotOf(selected)) return false;
  const slot = slotOf(selected)!;
  const current = worn[slot];
  return current?.id !== selected.id || (dyes !== null && !sameDyes(dyes, selected.dyes));
}

/** An outfit name problem (1-32 characters after trimming), or null. */
export function outfitNameProblem(name: string): string | null {
  const n = [...name.trim()].length;
  if (n === 0) return "Give the outfit a name.";
  if (n > OUTFIT_NAME_MAX) return `Outfit names are at most ${OUTFIT_NAME_MAX} characters.`;
  return null;
}

/** The pieces of a saved outfit by slot, with names where we know the item. */
export function outfitPieces(o: Outfit, known: ReadonlyMap<number, Item>, catalog: Catalog = CATALOG): { slot: GearSlot; name: string | null }[] {
  return GEAR_SLOTS.map((slot) => {
    const id = o[slot];
    if (id === null) return { slot, name: null };
    const item = known.get(id);
    if (!item) return { slot, name: "An item" };
    const def = defOf(item, catalog);
    // A restricted piece says so here too.
    const label = def?.wear ? wearLabel(def) : null;
    return { slot, name: `${def?.name ?? "Unknown item"}${label ? ` (${label})` : ""}` };
  });
}

/** Words for the slots a worn outfit could not fill. */
export function missingText(missing: readonly GearSlot[]): string | null {
  if (!missing.length) return null;
  const names = missing.map((s) => SLOT_LABELS[s].toLowerCase());
  const list = names.length === 1 ? names[0]! : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]!}`;
  return `Some pieces are no longer in your wardrobe or need a higher level (${list}); those slots were left as they were.`;
}

/**
 * The level an item needs before it can be put on, when that
 * is above `level` (null when it can be worn). Required levels are checked
 * only when equipping: an item already worn stays worn.
 */
export function levelNeeded(item: Pick<Item, "item_def">, level: number, catalog: Catalog = CATALOG): number | null {
  const need = (defOf(item, catalog) as { required_level?: number } | undefined)?.required_level ?? 1;
  return need > level ? need : null;
}

/**
 * Why a character of body type `bodyIndex` may not put
 * `item` on (the server's own words, "That is female only."), or null when
 * it may. Owning, dyeing, trading and mailing it are always allowed.
 */
export function wearProblem(item: Pick<Item, "item_def">, bodyIndex: number, catalog: Catalog = CATALOG): string | null {
  return wearRefusal(bodyIndex, defOf(item, catalog));
}

/** Friendlier words for the wardrobe's API errors, by code. */
export function wardrobeError(code: string, fallback: string): string {
  switch (code) {
    case "item_unavailable":
      return "That item is placed in your room. Take it back to your trunk first.";
    case "item_not_found":
      return "That item is no longer in your wardrobe.";
    case "wrong_slot":
      return "That doesn't go there.";
    case "invalid_dye":
      return "That colour is not in the dye palette.";
    case "missing_dyes":
      return "You do not have the dyes for those colours. Their own colours are free; others use up a dye.";
    case "outfit_limit":
      return "You have saved as many outfits as you can (10). Delete one to save another.";
    case "outfit_not_found":
      return "That outfit has already been deleted.";
    case "wear_restricted":
      // The server's message names the class ("That is female only.").
      return fallback;
    case "level_too_low":
      // The server's message names the level ("You need to be level 4 to wear that.").
      return `${fallback} Attend classes, craft and explore to level up.`;
    case "invalid_name":
      return "Outfit names are 1 to 32 characters.";
    case "rate_limited":
      return "Slow down a little: the wardrobe needs a moment.";
    default:
      return fallback;
  }
}
