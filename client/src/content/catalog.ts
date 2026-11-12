// The content data shared with the server and art (docs/architecture.md,
// "Content data"): the item catalog, the dye palette and the character
// creator. Imported at build time from data/, so the client and the server
// (which compiles the same files in) always agree. Every display name,
// including the IP names, lives in those files; this module only types them
// and ports the few rules of sim_core::catalog the client needs.
//
// Pure (no DOM, no Babylon), unit tested in content.test.ts.

import catalogJson from "../../../data/items/catalog.json";
import paletteJson from "../../../data/items/palette.json";
import creatorJson from "../../../data/characters/creator.json";
import type { GearSlot, House, Wear } from "../wasm/sim_wasm";

export type ItemKind = "clothing" | "furniture";
export type HouseColourChoice = "house_primary" | "house_secondary";
/** Dye channels in wire order: primary, secondary, trim. */
export type Dyes = [number, number, number];

export interface DyeChannels {
  primary: number;
  secondary: number;
  trim: number;
}

export interface FurnitureDef {
  surface: "floor" | "wall" | "table";
  footprint: [number, number];
  height: number;
  top?: number;
  /** The part of the top items may stand on, `[x0, z0, x1, z1]` in the piece's axes (absent: the footprint). */
  top_area?: [number, number, number, number];
  /** Lower surfaces for table items, each with its headroom (`clear`, m). */
  shelves?: { y: number; area: [number, number, number, number]; clear: number }[];
  /**
   * Where a sitter is drawn, `yaw` relative to the piece; `front`, `back` and
   * `drop` how far the seat reaches ahead of and behind the area's centre
   * line and its top above the floor (castle/seats.ts, the legs' fit).
   */
  seats?: { y: number; area: [number, number, number, number]; yaw?: number; front: number; back: number; drop: number }[];
  station?: string;
}

/** `sim_core::catalog::ItemDef`. */
export interface ItemDef {
  id: number;
  kind: ItemKind;
  slot?: GearSlot;
  name: string;
  asset: string;
  dyes: DyeChannels;
  house_dyes?: { primary?: HouseColourChoice; secondary?: HouseColourChoice; trim?: HouseColourChoice };
  tags: string[];
  furniture?: FurnitureDef;
  retired?: boolean;
  /** Clothing only: only bodies of this class may wear it (sim-wasm `canWear`); absent: anyone. */
  wear?: Wear;
}

export interface Catalog {
  version: number;
  items: ItemDef[];
}

export interface PaletteColour {
  index: number;
  id: string;
  name: string;
  /** sRGB `#RRGGBB`. */
  hex: string;
}

export interface Palette {
  version: number;
  colours: PaletteColour[];
  houses: Record<string, { primary: number; secondary: number }>;
}

export interface BodyType {
  index: number;
  /** `body_a` / `body_b`: the suffix of fitted asset files. */
  id: string;
  /** The creator's label ("Male" / "Female"). */
  name: string;
  asset: string;
  /** The height slider's range (metres): a uniform scale of the whole character. */
  height_m: [number, number];
  default_height_m: number;
  /** The shipped hair style a new character of this body starts with. */
  default_hair_style: number;
  /** This body's wear class (`body_a` male, `body_b` female), for `canWear`. */
  wear: Wear;
}

/** `creator.json` version 2: no build sliders (every body is the neutral build). */
export type SliderField = "height" | "face";

export interface Slider {
  id: string;
  name: string;
  group: string;
  field: SliderField;
  /** Index into `face`; absent for height. */
  byte?: number;
  /** Face: one or two shape keys. Height: none (a uniform scale). */
  shape_keys: string[];
  default: number;
}

export interface Swatch {
  index: number;
  name: string;
  hex: string;
}

/** A hair style: shipped ones have an asset and one body; retired ones migrate per body. */
export interface HairStyle {
  id: number;
  name: string;
  asset?: string;
  /** The body type id (`body_a`) a shipped style belongs to. */
  body?: string;
  retired?: boolean;
  /** Retired: body type id → the shipped style of that body that replaces it. */
  migrate_to?: Record<string, number>;
}

export interface Creator {
  version: number;
  body_types: BodyType[];
  sliders: Slider[];
  skin_tones: Swatch[];
  hair_styles: HairStyle[];
  hair_colours: Swatch[];
  /** Version 2: the iris tint (the body dye mask's B channel). */
  eye_colours: Swatch[];
  default_eye_colour: number;
}

export const TAG_STARTER = "starter";
export const TAG_DEFAULT = "default";
export const TAG_HOUSE_PREFIX = "house:";
export const GEAR_SLOTS: readonly GearSlot[] = ["hat", "body", "legs", "shoes"];
/** Slider neutral value (`protocol::SLIDER_NEUTRAL`). */
export const SLIDER_NEUTRAL = 128;

/** The shipped files, typed. */
export const CATALOG = catalogJson as Catalog;
export const PALETTE = paletteJson as Palette;
export const CREATOR = creatorJson as unknown as Creator;

// ------------------------------------------------------------------ items

const byId = new Map<number, ItemDef>();
function index(catalog: Catalog): Map<number, ItemDef> {
  if (catalog === CATALOG) {
    if (byId.size === 0) for (const item of catalog.items) byId.set(item.id, item);
    return byId;
  }
  return new Map(catalog.items.map((i) => [i.id, i]));
}

/** The catalog entry for `id`, or undefined for an unknown id. */
export function itemDef(id: number, catalog: Catalog = CATALOG): ItemDef | undefined {
  return index(catalog).get(id);
}

/** The clothing entry `id` when it is worn in `slot` (`Catalog::clothing`). */
export function clothingDef(id: number, slot: GearSlot, catalog: Catalog = CATALOG): ItemDef | undefined {
  const item = itemDef(id, catalog);
  return item && item.kind === "clothing" && item.slot === slot ? item : undefined;
}

export function hasTag(item: ItemDef, tag: string): boolean {
  return item.tags.includes(tag);
}

/** The house a `house:<id>` tag limits the item to. */
export function itemHouse(item: ItemDef): House | undefined {
  const tag = item.tags.find((t) => t.startsWith(TAG_HOUSE_PREFIX));
  return tag ? (tag.slice(TAG_HOUSE_PREFIX.length) as House) : undefined;
}

/** Whether new characters of `house` are granted it (`ItemDef::is_starter_for`). */
export function isStarterFor(item: ItemDef, house: House): boolean {
  if (item.retired || !hasTag(item, TAG_STARTER)) return false;
  const only = itemHouse(item);
  return only === undefined || only === house;
}

/** What a new character of `house` is granted, in catalog order. */
export function starterItems(house: House, catalog: Catalog = CATALOG): ItemDef[] {
  return catalog.items.filter((i) => isStarterFor(i, house));
}

/** The starter clothing a new character of `house` may wear in `slot` (creator choices). */
export function starterClothing(house: House, slot: GearSlot, catalog: Catalog = CATALOG): ItemDef[] {
  return starterItems(house, catalog).filter((i) => i.kind === "clothing" && i.slot === slot);
}

/** The creator's preselected item for `slot` (`default` tag), if any. */
export function defaultClothing(house: House, slot: GearSlot, catalog: Catalog = CATALOG): ItemDef | undefined {
  return starterClothing(house, slot, catalog).find((i) => hasTag(i, TAG_DEFAULT));
}

// ------------------------------------------------------------------ wear

/** The label every item list shows on a restricted piece (`sim_core::catalog::Wear::label`). */
export const WEAR_LABELS: Readonly<Record<Wear, string>> = { female: "Female only", male: "Male only" };

/** "Female only" / "Male only" for a restricted clothing item (or its catalog id); null for anyone's. */
export function wearLabel(item: ItemDef | number | undefined, catalog: Catalog = CATALOG): string | null {
  const def = typeof item === "number" ? itemDef(item, catalog) : item;
  return def?.wear ? WEAR_LABELS[def.wear] : null;
}

/**
 * The one rule (`sim_core::catalog::can_wear`, sim-wasm `canWear`): a body
 * may wear an item with no class or of its own class. Mirrored here for the
 * creator, which runs before the wasm loads (content.test.ts checks that
 * the two agree).
 */
export function canWear(body: Wear, item: Wear | undefined): boolean {
  return item === undefined || item === body;
}

/** The wear class of body type `index` (`body_a` male, `body_b` female). */
export function bodyWear(index: number, creator: Creator = CREATOR): Wear {
  return bodyType(index, creator).wear;
}

/** Whether a character of body type `bodyIndex` may wear `item` (true for anything without a class). */
export function bodyCanWear(bodyIndex: number, item: Pick<ItemDef, "wear"> | undefined, creator: Creator = CREATOR): boolean {
  return canWear(bodyWear(bodyIndex, creator), item?.wear);
}

/** The server's refusal in its words ("That is female only."), or null when the body may wear it. */
export function wearRefusal(bodyIndex: number, item: Pick<ItemDef, "wear"> | undefined, creator: Creator = CREATOR): string | null {
  if (!item?.wear || bodyCanWear(bodyIndex, item, creator)) return null;
  return `That is ${item.wear} only.`;
}

/** The dyes a fresh instance granted to a character of `house` gets (`ItemDef::granted_dyes`). */
export function grantedDyes(item: ItemDef, house: House, palette: Palette = PALETTE): Dyes {
  const dyes: Dyes = [item.dyes.primary, item.dyes.secondary, item.dyes.trim];
  const hd = item.house_dyes;
  const colours = palette.houses[house];
  if (!hd || !colours) return dyes;
  const choices = [hd.primary, hd.secondary, hd.trim];
  for (let i = 0; i < 3; i++) {
    if (choices[i] === "house_primary") dyes[i] = colours.primary;
    else if (choices[i] === "house_secondary") dyes[i] = colours.secondary;
  }
  return dyes;
}

/** The free-form tags of an item (no `starter`, `default` or `house:`), for wardrobe filters and labels. */
export function styleTags(item: ItemDef): string[] {
  return item.tags.filter((t) => t !== TAG_STARTER && t !== TAG_DEFAULT && !t.startsWith(TAG_HOUSE_PREFIX));
}

// ------------------------------------------------------------------ palette

/** sRGB bytes of `#RRGGBB` as 0..1 floats; black for malformed input. */
export function hexToRgb01(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return [0, 0, 0];
  const n = Number.parseInt(m[1]!, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function paletteColour(index: number, palette: Palette = PALETTE): PaletteColour | undefined {
  return palette.colours[index];
}

/** CSS colour of a palette index (a neutral grey outside the palette). */
export function paletteHex(index: number, palette: Palette = PALETTE): string {
  return palette.colours[index]?.hex ?? "#808080";
}

/** sRGB 0..1 of a palette index. */
export function paletteRgb(index: number, palette: Palette = PALETTE): [number, number, number] {
  return hexToRgb01(paletteHex(index, palette));
}

export function isPaletteIndex(index: number, palette: Palette = PALETTE): boolean {
  return Number.isInteger(index) && index >= 0 && index < palette.colours.length;
}

// ------------------------------------------------------------------ creator

export function bodyType(index: number, creator: Creator = CREATOR): BodyType {
  return creator.body_types[index] ?? creator.body_types[0]!;
}

export function hairStyle(id: number, creator: Creator = CREATOR): HairStyle | undefined {
  return creator.hair_styles.find((h) => h.id === id);
}

/** Whether a style is offered and drawn (not retired, has an asset). */
export function isShippedStyle(h: HairStyle): h is HairStyle & { asset: string; body: string } {
  return !h.retired && typeof h.asset === "string" && typeof h.body === "string";
}

/** The shipped hair styles of body type `index`, in file order (`Creator::styles_of`). */
export function hairStylesFor(index: number, creator: Creator = CREATOR): (HairStyle & { asset: string; body: string })[] {
  const body = creator.body_types[index];
  if (!body) return [];
  return creator.hair_styles.filter((h): h is HairStyle & { asset: string; body: string } => isShippedStyle(h) && h.body === body.id);
}

/**
 * The hair style a look of body `index` wearing `id` should wear
 * (`Creator::migrate_hair`): `id` when it is a shipped style of that body,
 * a retired style's `migrate_to` for the body, else the body's default.
 */
export function migrateHair(id: number, index: number, creator: Creator = CREATOR): number {
  const body = bodyType(index, creator);
  const style = hairStyle(id, creator);
  if (!style) return body.default_hair_style;
  if (isShippedStyle(style)) return style.body === body.id ? id : body.default_hair_style;
  return style.migrate_to?.[body.id] ?? body.default_hair_style;
}

export function eyeColourHex(index: number, creator: Creator = CREATOR): string {
  return creator.eye_colours[index]?.hex ?? creator.eye_colours[creator.default_eye_colour]?.hex ?? "#6B4226";
}

export function skinToneHex(index: number, creator: Creator = CREATOR): string {
  return creator.skin_tones[index]?.hex ?? creator.skin_tones[0]?.hex ?? "#C68E62";
}

export function hairColourHex(index: number, creator: Creator = CREATOR): string {
  return creator.hair_colours[index]?.hex ?? creator.hair_colours[0]?.hex ?? "#4A3324";
}
