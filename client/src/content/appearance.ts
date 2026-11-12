// `protocol::Appearance` on the client: the HTTP API spells it in snake_case
// (`body_type`), the wasm decoder in camelCase (`bodyType`). The client works
// with the camelCase form everywhere and converts at the HTTP boundary.
//
// Also the creator rules that turn appearance bytes into something drawable:
// the default appearance for a house, slider reads and writes, shape-key
// weights and the height in metres. Pure, unit tested in content.test.ts.
//
// Protocol 6 (creator.json v2, docs/art/character-adoption.md section 1):
// no build sliders, height a uniform scale of the whole character, hair
// styles per body (a retired id is mapped to its replacement for the body),
// and a new eye colour (absent = Brown).

import type { Appearance, Finish, GearSlot, House, Outfit, Worn } from "../wasm/sim_wasm";
import {
  bodyType,
  clothingDef,
  hairStylesFor,
  migrateHair,
  CREATOR,
  defaultClothing,
  GEAR_SLOTS,
  grantedDyes,
  PALETTE,
  SLIDER_NEUTRAL,
  type Catalog,
  type Creator,
  type Dyes,
  type Palette,
  type Slider,
  CATALOG,
} from "./catalog";

export type { Appearance, Outfit, Worn };

/** An item as worn, as the HTTP API spells it (`finish`). */
export interface WireWorn {
  item_def: number;
  dyes: Dyes;
  finish?: Finish;
}

const FINISHES: readonly Finish[] = ["none", "shimmer", "colour_shift"];

/** `Appearance` as the HTTP API spells it (`characters.appearance`, `POST /api/characters`). */
export interface WireAppearance {
  body_type: number;
  height: number;
  face: [number, number, number, number, number];
  skin_tone: number;
  hair_style: number;
  hair_colour: number;
  /** Absent in looks stored before protocol 6 (Brown). */
  eye_colour?: number;
  outfit: { hat: WireWorn | null; body: WireWorn | null; legs: WireWorn | null; shoes: WireWorn | null };
}

const byte = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.min(255, Math.round(v))) : fallback);

function wornFromWire(w: WireWorn | null | undefined): Worn | null {
  if (!w || typeof w.item_def !== "number") return null;
  const d = Array.isArray(w.dyes) ? w.dyes : [];
  const worn: Worn = { itemDef: w.item_def, dyes: [byte(d[0]), byte(d[1]), byte(d[2])] };
  if (w.finish && w.finish !== "none" && FINISHES.includes(w.finish)) worn.finish = w.finish;
  return worn;
}

function wornToWire(w: Worn | null): WireWorn | null {
  if (!w) return null;
  const wire: WireWorn = { item_def: w.itemDef, dyes: [w.dyes[0], w.dyes[1], w.dyes[2]] };
  if (w.finish && w.finish !== "none") wire.finish = w.finish;
  return wire;
}

/**
 * HTTP → client. Tolerates missing fields (a NULL appearance means creator
 * defaults); an old look's `build` is ignored, a missing eye colour reads
 * as the default (Brown) and a retired or foreign hair style is mapped to
 * the body's own (`migrateHair`), as the server does.
 */
export function fromWire(w: (Partial<WireAppearance> & { build?: unknown }) | null | undefined, creator: Creator = CREATOR): Appearance {
  const a = defaultBodyAppearance(creator);
  if (!w) return a;
  const face = Array.isArray(w.face) ? w.face : a.face;
  const bodyIndex = byte(w.body_type, a.bodyType);
  return {
    bodyType: bodyIndex,
    height: byte(w.height, a.height),
    face: [byte(face[0], SLIDER_NEUTRAL), byte(face[1], SLIDER_NEUTRAL), byte(face[2], SLIDER_NEUTRAL), byte(face[3], SLIDER_NEUTRAL), byte(face[4], SLIDER_NEUTRAL)],
    skinTone: byte(w.skin_tone, a.skinTone),
    hairStyle: migrateHair(typeof w.hair_style === "number" ? w.hair_style : a.hairStyle, bodyIndex, creator),
    hairColour: byte(w.hair_colour, a.hairColour),
    eyeColour: byte(w.eye_colour, creator.default_eye_colour),
    outfit: {
      hat: wornFromWire(w.outfit?.hat),
      body: wornFromWire(w.outfit?.body),
      legs: wornFromWire(w.outfit?.legs),
      shoes: wornFromWire(w.outfit?.shoes),
    },
  };
}

/** Client → HTTP. */
export function toWire(a: Appearance): WireAppearance {
  return {
    body_type: a.bodyType,
    height: a.height,
    face: [a.face[0], a.face[1], a.face[2], a.face[3], a.face[4]],
    skin_tone: a.skinTone,
    hair_style: a.hairStyle,
    hair_colour: a.hairColour,
    eye_colour: a.eyeColour,
    outfit: {
      hat: wornToWire(a.outfit.hat),
      body: wornToWire(a.outfit.body),
      legs: wornToWire(a.outfit.legs),
      shoes: wornToWire(a.outfit.shoes),
    },
  };
}

function cloneWorn(w: Worn | null): Worn | null {
  if (!w) return null;
  const worn: Worn = { itemDef: w.itemDef, dyes: [w.dyes[0], w.dyes[1], w.dyes[2]] };
  if (w.finish && w.finish !== "none") worn.finish = w.finish;
  return worn;
}

export function cloneAppearance(a: Appearance): Appearance {
  return {
    ...a,
    face: [...a.face],
    outfit: {
      hat: cloneWorn(a.outfit.hat),
      body: cloneWorn(a.outfit.body),
      legs: cloneWorn(a.outfit.legs),
      shoes: cloneWorn(a.outfit.shoes),
    },
  };
}

/**
 * A canonical string for `a`: two appearances draw identically exactly when
 * their keys are equal, so assembled geometry is shared by key. A worn
 * item's finish is part of it (finished garments carry it in their vertex
 * data, game/characters/finish.ts).
 */
export function appearanceKey(a: Appearance): string {
  const worn = (w: Worn | null) => (w ? `${w.itemDef}.${w.dyes[0]}.${w.dyes[1]}.${w.dyes[2]}${w.finish && w.finish !== "none" ? `.${w.finish}` : ""}` : "-");
  return [
    a.bodyType,
    a.height,
    a.face.join("."),
    a.skinTone,
    a.hairStyle,
    a.hairColour,
    a.eyeColour,
    worn(a.outfit.hat),
    worn(a.outfit.body),
    worn(a.outfit.legs),
    worn(a.outfit.shoes),
  ].join("|");
}

// ------------------------------------------------------------------ creator defaults

/**
 * `Creator::default_appearance(body)`: slider defaults, the body's default
 * hair style, the default eye colour, the first skin and hair colour;
 * nothing worn.
 */
export function defaultBodyAppearance(creator: Creator = CREATOR, body = 0): Appearance {
  const index = creator.body_types[body] ? body : 0;
  const a: Appearance = {
    bodyType: index,
    height: SLIDER_NEUTRAL,
    face: [SLIDER_NEUTRAL, SLIDER_NEUTRAL, SLIDER_NEUTRAL, SLIDER_NEUTRAL, SLIDER_NEUTRAL],
    skinTone: 0,
    hairStyle: creator.body_types[index]?.default_hair_style ?? hairStylesFor(index, creator)[0]?.id ?? 0,
    hairColour: 0,
    eyeColour: creator.default_eye_colour,
    outfit: { hat: null, body: null, legs: null, shoes: null },
  };
  for (const s of creator.sliders) setSlider(a, s, s.default);
  return a;
}

/** The creator's starting point for `house`: defaults plus the `default` items with their granted dyes. */
export function defaultAppearance(house: House, creator: Creator = CREATOR, catalog: Catalog = CATALOG, palette: Palette = PALETTE): Appearance {
  const a = defaultBodyAppearance(creator);
  for (const slot of GEAR_SLOTS) {
    const item = defaultClothing(house, slot, catalog);
    a.outfit[slot] = item ? { itemDef: item.id, dyes: grantedDyes(item, house, palette) } : null;
  }
  return a;
}

/** Wears `itemDef` in `slot` with the dyes a new character of `house` is granted (null takes it off). */
export function wearStarter(a: Appearance, slot: GearSlot, itemDef: number | null, house: House, catalog: Catalog = CATALOG, palette: Palette = PALETTE): Appearance {
  const next = cloneAppearance(a);
  const item = itemDef === null ? undefined : clothingDef(itemDef, slot, catalog);
  next.outfit[slot] = item ? { itemDef: item.id, dyes: grantedDyes(item, house, palette) } : null;
  return next;
}

// ------------------------------------------------------------------ sliders

/** The byte a slider controls. */
export function sliderValue(a: Appearance, s: Slider): number {
  switch (s.field) {
    case "height":
      return a.height;
    case "face":
      return a.face[s.byte ?? 0] ?? SLIDER_NEUTRAL;
  }
}

/** Sets the byte a slider controls, in place. */
export function setSlider(a: Appearance, s: Slider, value: number): void {
  const v = byte(value);
  switch (s.field) {
    case "height":
      a.height = v;
      break;
    case "face":
      a.face[s.byte ?? 0] = v;
      break;
  }
}

/**
 * Shape-key weights (0..1) for one slider value (docs/architecture.md,
 * "Content data"): one key goes 0..1 over 0..255; two keys are two-sided
 * around 128, the first toward 0 and the second toward 255.
 */
export function sliderShapeWeights(s: Slider, value: number): [string, number][] {
  const keys = s.shape_keys;
  if (keys.length === 1) return [[keys[0]!, value / 255]];
  if (keys.length >= 2) {
    const low = value < SLIDER_NEUTRAL ? (SLIDER_NEUTRAL - value) / SLIDER_NEUTRAL : 0;
    const high = value > SLIDER_NEUTRAL ? (value - SLIDER_NEUTRAL) / (255 - SLIDER_NEUTRAL) : 0;
    return [
      [keys[0]!, low],
      [keys[1]!, high],
    ];
  }
  return [];
}

/**
 * Every mesh shape-key weight `a` implies, by key name. Height is not a mesh
 * key (a uniform scale of the whole character), so the height slider
 * contributes nothing here.
 */
export function shapeKeyWeights(a: Appearance, creator: Creator = CREATOR): Map<string, number> {
  const out = new Map<string, number>();
  for (const s of creator.sliders) {
    if (s.field === "height") continue;
    for (const [key, w] of sliderShapeWeights(s, sliderValue(a, s))) if (w > 0) out.set(key, w);
  }
  return out;
}

/** Standing height in metres: 0 / 128 / 255 map to the body's min / default / max. */
export function heightMetres(a: Appearance, creator: Creator = CREATOR): number {
  const body = bodyType(a.bodyType, creator);
  const [lo, hi] = body.height_m;
  const d = body.default_height_m;
  const h = a.height;
  return h <= SLIDER_NEUTRAL ? lo + ((d - lo) * h) / SLIDER_NEUTRAL : d + ((hi - d) * (h - SLIDER_NEUTRAL)) / (255 - SLIDER_NEUTRAL);
}

/** Uniform scale applied to the body's default-height mesh to reach `heightMetres` (never a stretch). */
export function heightScale(a: Appearance, creator: Creator = CREATOR): number {
  return heightMetres(a, creator) / bodyType(a.bodyType, creator).default_height_m;
}

/** The height byte nearest to `metres` for body `index` (inverse of `heightMetres`, clamped). */
export function heightByte(metres: number, index: number, creator: Creator = CREATOR): number {
  const body = bodyType(index, creator);
  const [lo, hi] = body.height_m;
  const d = body.default_height_m;
  const m = Math.max(lo, Math.min(hi, metres));
  const b = m <= d ? (SLIDER_NEUTRAL * (m - lo)) / (d - lo) : SLIDER_NEUTRAL + ((255 - SLIDER_NEUTRAL) * (m - d)) / (hi - d);
  return Math.max(0, Math.min(255, Math.round(b)));
}

/** `metres` as feet and inches, rounded to the inch (`5'10"`). */
export function feetInches(metres: number): string {
  const inches = Math.round(metres / 0.0254);
  return `${Math.floor(inches / 12)}'${inches % 12}"`;
}

/** The creator's height label: metres and feet/inches (`1.78 m (5'10")`). */
export function heightLabel(metres: number): string {
  return `${metres.toFixed(2)} m (${feetInches(metres)})`;
}

/** The worn item of each slot, in slot order (for loops over the outfit). */
export function wornSlots(o: Outfit): [GearSlot, Worn | null][] {
  return GEAR_SLOTS.map((s) => [s, o[s]]);
}
