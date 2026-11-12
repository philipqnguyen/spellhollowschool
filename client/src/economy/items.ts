// Items as the economy windows see them: the catalog's economy fields
// (stacks, required level, house theme, Exchange category, shop price, dye
// family, finish, potion, pattern), the Owl Exchange category table, and the
// API's item instance with its quantity, maker's mark and finish.
//
// The catalog file is the one shared with the server (content/catalog.ts
// imports it); this module types the newer fields without changing that
// module. Display names for categories are plain words; potion categories are
// named after their potions, which live in the data. Pure, unit tested in
// items.test.ts.

import { CATALOG, itemDef, type Catalog, type Dyes, type ItemDef } from "../content/catalog";
import { HOUSES } from "../content/houses";
import type { EffectKind, Finish, GearSlot } from "../wasm/sim_wasm";

/** Every kind of item. */
export type EconKind = "clothing" | "furniture" | "material" | "dye" | "finish" | "potion" | "pattern";

/** `sim_core::catalog::ItemDef` with the economy fields. */
export interface EconItemDef extends Omit<ItemDef, "kind"> {
  kind: EconKind;
  stackable?: boolean;
  max_stack?: number;
  required_level?: number;
  house_theme?: string;
  exchange: { category: string; subcategory: string };
  price?: number;
  family?: string;
  dye?: { family: string };
  finish?: Exclude<Finish, "none">;
  potion?: { effect: EffectKind; part?: "hair" | "robe" };
  pattern?: { recipe: number };
}

/** Where an item instance is (`items.location`). */
export type ItemLocation = "inventory" | "equipped" | "dorm" | "mail" | "exchange";

/** An item instance as the HTTP API sends it (`Item`). */
export interface EconItem {
  id: number;
  item_def: number;
  dyes: Dyes;
  location: ItemLocation;
  slot: GearSlot | null;
  created_at: string;
  /** 1 for clothing and furniture. */
  quantity: number;
  /** The crafter's name (maker's mark), or null for bought and found items. */
  maker: string | null;
  finish: Finish;
}

/** The catalog entry for `id` with its economy fields, or undefined. */
export function econDef(id: number, catalog: Catalog = CATALOG): EconItemDef | undefined {
  return itemDef(id, catalog) as EconItemDef | undefined;
}

/** Display name of a catalog id (a placeholder for ids this build does not know). */
export function defName(id: number, catalog: Catalog = CATALOG): string {
  return itemDef(id, catalog)?.name ?? `Unknown item #${id}`;
}

/** Most units one row holds (`ItemDef::stack_limit`): 1 unless stackable. */
export function stackLimit(def: EconItemDef | undefined): number {
  return def?.stackable ? Math.max(1, def.max_stack ?? 1) : 1;
}

/** Player level needed to use it (1 when the data leaves it out). */
export function requiredLevel(def: EconItemDef | undefined): number {
  return def?.required_level ?? 1;
}

export const KIND_LABELS: Record<EconKind, string> = {
  clothing: "Clothing",
  furniture: "Furniture",
  material: "Material",
  dye: "Dye",
  finish: "Finish",
  potion: "Potion",
  pattern: "Pattern",
};

export const SLOT_NAMES: Record<GearSlot, string> = { hat: "Hat", body: "Body", legs: "Legs", shoes: "Shoes" };

export const FINISH_LABELS: Record<Finish, string> = { none: "", shimmer: "Shimmer finish", colour_shift: "Colour-shifting finish" };

// ------------------------------------------------------------------ Exchange categories

/** One Exchange category and its subcategories (`sim_core::exchange::CATEGORIES`). */
export interface Category {
  id: string;
  label: string;
  subs: { id: string; label: string }[];
}

const CATEGORY_LABELS: [string, string][] = [
  ["hats", "Hats"],
  ["body", "Body"],
  ["legs", "Legs"],
  ["shoes", "Shoes"],
  ["furniture", "Furniture"],
  ["materials", "Materials"],
  ["dyes", "Dyes"],
  ["potions", "Potions"],
];

const SUBCATEGORY_LABELS: Record<string, [string, string][]> = {
  hats: [
    ["hats", "Hats"],
    ["hoods", "Hoods"],
    ["hair_accessories", "Hair accessories"],
  ],
  body: [
    ["robes", "Robes"],
    ["jackets", "Jackets"],
    ["sweaters", "Sweaters"],
    ["tops", "Tops"],
  ],
  legs: [
    ["trousers", "Trousers"],
    ["skirts", "Skirts"],
  ],
  shoes: [
    ["shoes", "Shoes"],
    ["boots", "Boots"],
  ],
  furniture: [
    ["beds", "Beds"],
    ["seating", "Seating"],
    ["tables", "Tables"],
    ["decor", "Decor"],
    ["lighting", "Lighting"],
    ["plants", "Plants"],
  ],
  materials: [
    ["fibers", "Fibres"],
    ["feathers", "Feathers"],
    ["fur", "Fur and hide"],
    ["dye_plants", "Dye plants"],
    ["ingredients", "Ingredients"],
    ["patterns", "Patterns"],
  ],
  dyes: [
    ["dyes", "Dyes"],
    ["finishes", "Finishes"],
  ],
};

/** Potion subcategories in `EffectKind` order (the server's table). */
export const POTION_EFFECTS: readonly EffectKind[] = ["glow", "chameleon", "shrinking", "swelling", "babbling", "mimic_draught", "fortunes_draught", "pick_me_up_tonic", "invisibility", "featherlight"];

/**
 * A potion category's label: the name of the first potion with that effect
 * ("Chameleon Tonic (Hair)" → "Chameleon Tonic"), since potion names are
 * data; the effect id when no potion has it.
 */
export function effectLabel(effect: EffectKind, catalog: Catalog = CATALOG): string {
  const potion = (catalog.items as EconItemDef[]).find((i) => i.kind === "potion" && i.potion?.effect === effect);
  if (!potion) return effect.charAt(0).toUpperCase() + effect.slice(1);
  return potion.name.replace(/\s*\([^)]*\)\s*$/, "");
}

/** The whole category tree, in the server's order. */
export function exchangeCategories(catalog: Catalog = CATALOG): Category[] {
  return CATEGORY_LABELS.map(([id, label]) => ({
    id,
    label,
    subs: id === "potions" ? POTION_EFFECTS.map((e) => ({ id: e, label: effectLabel(e, catalog) })) : (SUBCATEGORY_LABELS[id] ?? []).map(([sid, sl]) => ({ id: sid, label: sl })),
  }));
}

/** Whether `category` (and `subcategory`, when given) is in the table (`exchange::is_valid`). */
export function isValidCategory(category: string, subcategory?: string | null, catalog: Catalog = CATALOG): boolean {
  const c = exchangeCategories(catalog).find((x) => x.id === category);
  return !!c && (!subcategory || c.subs.some((s) => s.id === subcategory));
}

/** "Hats › Hoods", for a listing's breadcrumb. */
export function categoryPath(category: string, subcategory: string | null | undefined, catalog: Catalog = CATALOG): string {
  const c = exchangeCategories(catalog).find((x) => x.id === category);
  if (!c) return category;
  const s = subcategory ? c.subs.find((x) => x.id === subcategory) : undefined;
  return s ? `${c.label} › ${s.label}` : c.label;
}

/** Houses for the Exchange's house-theme filter (`house=` takes the lower-case id). */
export function houseThemes(): { id: string; name: string; colour: string }[] {
  return HOUSES.map((h) => ({ id: h.id, name: h.name, colour: h.colour }));
}

// ------------------------------------------------------------------ labels

/**
 * One line about an item instance: kind or slot, units, finish and maker's
 * mark ("Body · Shimmer finish · Made by Ottilie").
 */
export function itemLine(item: Pick<EconItem, "item_def" | "quantity" | "maker" | "finish">, catalog: Catalog = CATALOG): string {
  const def = econDef(item.item_def, catalog);
  const parts: string[] = [];
  if (def) parts.push(def.kind === "clothing" && def.slot ? SLOT_NAMES[def.slot] : KIND_LABELS[def.kind]);
  if (item.quantity > 1) parts.push(`${item.quantity} units`);
  if (item.finish !== "none") parts.push(FINISH_LABELS[item.finish]);
  if (item.maker) parts.push(makersMark(item.maker));
  return parts.join(" · ");
}

/** The maker's mark as shown on crafted items. */
export function makersMark(maker: string): string {
  return `Made by ${maker}`;
}

/** "Wool Skein ×12" or just the name for a single unit. */
export function nameWithQuantity(itemDefId: number, quantity: number, catalog: Catalog = CATALOG): string {
  const name = defName(itemDefId, catalog);
  return quantity > 1 ? `${name} ×${quantity}` : name;
}

/** Whether the item can leave its owner (trade, letter, listing): it must sit in the inventory. */
export function isMovable(item: Pick<EconItem, "location">): boolean {
  return item.location === "inventory";
}

/** Inventory kinds to filter pickers by, in display order. */
export const PICKER_KINDS: readonly EconKind[] = ["clothing", "furniture", "material", "dye", "finish", "potion", "pattern"];
