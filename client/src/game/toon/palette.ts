// Toon ramp parameters: how each material region takes light in the
// anime look (docs/art/style-guide.md, "Toon rendering"). The art side fills
// these tables; the renderer (toon/material.ts) reads them, so retuning a
// shadow colour is a data edit, never a shader change.
//
// The lighting model, per pixel (sRGB, as the materials work):
//
//   lit     = step(n·l) of the key         soft over threshold ± softness,
//             (sun or moon)                times the shadow map on High;
//                                          lit where the key is weak next
//                                          to the fill (rooms)
//   direct  = key · (lit + (1 − lit) · shadowLight · shadowTint)
//   ambient = sky/ground hemisphere fill, tinted by shadowTint where unlit
//   local   = Σ torch, lamp, wand · (wrap + (1 − wrap) · shadowLight · shadowTint),
//             wrap a wide soft step over n·l
//   colour  = albedo · (ambient + direct + local + rim) + specular
//
// then a little more saturation in the shadow band and a soft shoulder
// instead of a hard clip (no filmic curve: it greys flat colour).
//
// Characters pick a row per vertex (the region, packed into the vertex
// colour alpha with the garment finish, see `toonAlpha`); world and
// furniture materials pick one row per material by name. Pure, unit tested.
//
// Characters (docs/art/character-adoption.md section 5) also read their
// baked shade layer (`_SHADE`: crease shade, the hair's root-to-tip
// coordinate and band phase, an outline weight) per vertex, and their rows
// have the fields that use it: `shadeShift` (creases fall into the tinted
// shadow band), `aoFloor`, `skyLift` (up-facing fill brighter), a rim on
// both bands (`rimWidth`) and the hair's highlight band (`band`,
// `bandCentre`, `bandWidth`). Their rows live in data/art/toon_palette.json
// (`characters`), next to the world's.
//
// Castle D1 (docs/art/castle-kit.md "Shading: the AO bake"): world rows use the same fields
// with the castle's baked shade x (COLOR_0, greyscale) in place of the
// shade layer, plus `valueFloor` (colour kept at the deepest shade) and
// `nearFade` (walk-through pieces dither out near the camera); a row that
// sets `rimWidth` takes the characters' rim. `post.ssao` holds the High-only
// screen-space AO's look (`TOON_SSAO`). Every value is palette data: the
// geometry carries a grey value, never a hue.

import type { Finish } from "../../wasm/sim_wasm";
import toonPaletteJson from "../../../../data/art/toon_palette.json";

export type Rgb = [number, number, number];

/** One row of a ramp table. */
export interface ToonRamp {
  /** n·l where the lit band is half on (−1..1; 0 is the terminator, −1 = always lit). */
  threshold: number;
  /** Half-width of the soft step in n·l (the style brief: a total step of 0.05-0.1). */
  softness: number;
  /** Multiplier of the shadow band (sRGB 0..1): a warm, saturated tint, never grey. */
  shadowTint: Rgb;
  /** Share of the direct light the shadow band still gets (0..1), before the tint. */
  shadowLight: number;
  /** Thin rim light on the shadow side (0 = none). */
  rim: number;
  /** Crisp highlight (metal, the hair band): 0 = none. */
  specular: number;
  /** Extra saturation in the shadow band (0 = none, 0.3 = noticeably richer). */
  saturation: number;
  /**
   * Outline weight for the screen-space outline pass (toon/post.ts):
   * 0 none, about 0.4 silhouettes only (faces, skin), 1 silhouettes and creases.
   */
  outline: number;
  /**
   * Distance fade of the crisp parts (castle kit M3 + M4, 17.12: far rib
   * facets): from `farStart` to `farEnd` metres from the camera the key
   * light's step widens into a soft gradient (material.ts FAR_SOFTNESS) and
   * the rim and highlight fade out. Both 0 (the default): no fade.
   */
  farStart: number;
  farEnd: number;
  /** Characters: how far the shade layer's crease value moves the threshold (t += (1 - shade) · shadeShift). */
  shadeShift: number;
  /** Characters: ambient kept in the deepest crease (ambient · mix(aoFloor, 1, shade)). */
  aoFloor: number;
  /** Characters: hemispheric fill by normal (fill · (1 + skyLift · n.y)). */
  skyLift: number;
  /** Characters: the rim's width in 1 − n·v (on both bands, brighter on the lit side). */
  rimWidth: number;
  /** Characters, hair: the highlight band's strength, centre (root-to-tip) and half-width. */
  band: number;
  bandCentre: number;
  bandWidth: number;
  /**
   * World (castle D1, docs/art/castle-kit.md "Shading: the AO bake"): the colour kept at the
   * deepest baked shade (colour · mix(valueFloor, 1, x)); 1 = no darkening.
   */
  valueFloor: number;
  /**
   * World: [near, far] metres from the camera over which a walk-through piece
   * dithers out (fully drawn at far, gone at near); [0, 0] = off.
   */
  nearFade: [number, number];
  /**
   * World: the row (or its prefix row, or the table's default) sets
   * `rimWidth`, so it takes the characters' rim (both bands, brighter lit)
   * instead of the thin M1-M4 world rim. Set by `resolveRamp`, never in data.
   */
  characterRim?: boolean;
}

/**
 * Character material regions, in shader order (the index is part of the
 * vertex format, below 16). `face` is the skin of the body's head: the skin
 * row's colour but no shadow-map shadow and a faint rim (clean faces).
 */
export const CHARACTER_REGIONS = ["skin", "hair", "primary", "secondary", "trim", "metal", "flat", "neutral", "face"] as const;
export type CharacterRegion = (typeof CHARACTER_REGIONS)[number];
export const REGION_COUNT = CHARACTER_REGIONS.length;

export function regionIndex(r: CharacterRegion): number {
  return CHARACTER_REGIONS.indexOf(r);
}

/** The defaults every partial row starts from (a neutral painted surface). */
export const BASE_RAMP: ToonRamp = {
  threshold: 0.02,
  softness: 0.04,
  shadowTint: [0.84, 0.74, 0.76],
  shadowLight: 0.5,
  rim: 0,
  specular: 0,
  saturation: 0.22,
  outline: 1,
  farStart: 0,
  farEnd: 0,
  shadeShift: 0,
  aoFloor: 1,
  skyLift: 0,
  rimWidth: 0.3,
  band: 0,
  bandCentre: 0.28,
  bandWidth: 0.05,
  valueFloor: 1,
  nearFade: [0, 0],
};

/**
 * Characters, one row per region, from data/art/toon_palette.json
 * (`characters`): skin and face shadows warm, cloth and hair shadows cool,
 * metal neutral with a crisp highlight, `flat` (painted eyes, mouth, the
 * tinted iris) ignoring light direction. A region the file lacks takes the
 * base row.
 */
export const CHARACTER_TOON: Record<CharacterRegion, Partial<ToonRamp>> = parseCharacterRows((toonPaletteJson as { characters?: unknown }).characters);

/** The `characters` block of the toon data as rows by region (`_doc` and unknown regions are ignored). */
export function parseCharacterRows(json: unknown): Record<CharacterRegion, Partial<ToonRamp>> {
  const j = (json ?? {}) as Record<string, Partial<ToonRamp>>;
  const out = {} as Record<CharacterRegion, Partial<ToonRamp>>;
  for (const r of CHARACTER_REGIONS) out[r] = typeof j[r] === "object" && j[r] !== null ? j[r]! : {};
  return out;
}

/**
 * World and furniture materials by glTF material name (the world export's
 * names: `wall`, `trim`, `floor_room`, room materials by full name). The art
 * side keeps the table in data/art/toon_palette.json (docs/art/style-guide.md,
 * "Toon ramp data"). A key ending in `*` matches by prefix; the longest match
 * wins; anything else uses `default`.
 */
export const WORLD_TOON: ToonTable = parseToonTable(toonPaletteJson);

export interface ToonTable {
  default: Partial<ToonRamp>;
  materials: Record<string, Partial<ToonRamp>>;
}

/** The JSON table (`{ _doc?, default, materials }`) as rows; unknown fields are ignored by `resolveRamp`. */
export function parseToonTable(json: unknown): ToonTable {
  const j = (json ?? {}) as { default?: Partial<ToonRamp>; materials?: Record<string, Partial<ToonRamp>> };
  return { default: j.default ?? {}, materials: j.materials ?? {} };
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, Number.isFinite(v) ? v : lo));

/** A full row: `partial` over the base, every value clamped to its range. */
export function resolveRamp(partial: Partial<ToonRamp> | undefined, base: ToonRamp = BASE_RAMP): ToonRamp {
  const r = { ...base, ...partial };
  const t = r.shadowTint;
  const nf = Array.isArray(r.nearFade) ? r.nearFade : [0, 0];
  const near = clamp(nf[0] ?? 0, 0, 50);
  const far = clamp(nf[1] ?? 0, 0, 50);
  return {
    threshold: clamp(r.threshold, -1, 1),
    softness: clamp(r.softness, 0.001, 0.5),
    shadowTint: [clamp(t[0], 0, 1), clamp(t[1], 0, 1), clamp(t[2], 0, 1)],
    shadowLight: clamp(r.shadowLight, 0, 1),
    rim: clamp(r.rim, 0, 2),
    specular: clamp(r.specular, 0, 2),
    saturation: clamp(r.saturation, 0, 1),
    outline: clamp(r.outline, 0, 1),
    farStart: clamp(r.farStart, 0, 500),
    // An end at or before the start turns the fade off.
    farEnd: clamp(r.farEnd, 0, 1000) > clamp(r.farStart, 0, 500) ? clamp(r.farEnd, 0, 1000) : 0,
    shadeShift: clamp(r.shadeShift, 0, 2),
    aoFloor: clamp(r.aoFloor, 0, 1),
    skyLift: clamp(r.skyLift, -1, 1),
    rimWidth: clamp(r.rimWidth, 0.01, 1),
    band: clamp(r.band, 0, 2),
    bandCentre: clamp(r.bandCentre, 0, 1),
    bandWidth: clamp(r.bandWidth, 0.005, 0.5),
    valueFloor: clamp(r.valueFloor, 0, 1),
    // A far at or before the near turns the fade off.
    nearFade: far > near ? [near, far] : [0, 0],
    characterRim: (partial !== undefined && partial.rimWidth !== undefined) || base.characterRim === true,
  };
}

/**
 * Whether a world row reads the baked shade in `COLOR_0` (castle D1, 19.5:
 * `TOON_WORLD_SHADE`) instead of multiplying the albedo by it.
 */
export function worldShades(r: ToonRamp): boolean {
  return r.shadeShift > 0 || r.aoFloor < 1 || r.valueFloor < 1;
}

/** Whether a world row dithers out near the camera (`TOON_NEARFADE`). */
export function worldNearFades(r: ToonRamp): boolean {
  return r.nearFade[1] > r.nearFade[0];
}

/** The castle's exterior clone suffix (grid.EXT_SUFFIX; castle/materials.ts). */
const EXT_SUFFIX = "@ext";

/**
 * The row for world material `name` (see `WORLD_TOON`). An exterior clone
 * (`<base>@ext`, castle M5c, docs/art/castle-kit.md "Exterior") takes its base's
 * row: `castle_wall_b@ext` reads as `castle_wall_b` (then `castle_wall*`).
 */
export function worldRamp(name: string, table: ToonTable = WORLD_TOON): ToonRamp {
  if (name.length > EXT_SUFFIX.length && name.endsWith(EXT_SUFFIX)) name = name.slice(0, -EXT_SUFFIX.length);
  const exact = table.materials[name];
  if (exact) return resolveRamp(exact, resolveRamp(table.default));
  let best: string | null = null;
  for (const key of Object.keys(table.materials)) {
    if (!key.endsWith("*")) continue;
    const prefix = key.slice(0, -1);
    if (name.startsWith(prefix) && (best === null || prefix.length > best.length - 1)) best = key;
  }
  return resolveRamp(best ? table.materials[best] : undefined, resolveRamp(table.default));
}

/** Floats per packed row: three vec4 (A = tint rgb + shadowLight, B = threshold, softness, rim, specular, C = outline, saturation, farStart, farEnd). */
export const RAMP_FLOATS = 12;

/** Writes `r` as three vec4 into `a`, `b`, `c` at vec4 index `i`. */
export function packRamp(r: ToonRamp, a: Float32Array, b: Float32Array, c: Float32Array, i = 0): void {
  const o = i * 4;
  a[o] = r.shadowTint[0];
  a[o + 1] = r.shadowTint[1];
  a[o + 2] = r.shadowTint[2];
  a[o + 3] = r.shadowLight;
  b[o] = r.threshold;
  b[o + 1] = r.softness;
  b[o + 2] = r.rim;
  b[o + 3] = r.specular;
  c[o] = r.outline;
  c[o + 1] = r.saturation;
  c[o + 2] = r.farStart;
  c[o + 3] = r.farEnd;
}

/**
 * The world's shade fields as two more vec4 (castle D1, 19.5): D =
 * shadeShift, aoFloor, skyLift, rimWidth (the regions' D layout); E =
 * valueFloor, nearFade near, nearFade far, rim mode (1 = the characters' rim).
 */
export function packWorldFields(r: ToonRamp, d: Float32Array, e: Float32Array): void {
  d[0] = r.shadeShift;
  d[1] = r.aoFloor;
  d[2] = r.skyLift;
  d[3] = r.rimWidth;
  e[0] = r.valueFloor;
  e[1] = r.nearFade[0];
  e[2] = r.nearFade[1];
  e[3] = r.characterRim ? 1 : 0;
}

/** The character fields as two more vec4 (D = shadeShift, aoFloor, skyLift, rimWidth; E = band, bandCentre, bandWidth, 0). */
export function packCharacterFields(r: ToonRamp, d: Float32Array, e: Float32Array, i = 0): void {
  const o = i * 4;
  d[o] = r.shadeShift;
  d[o + 1] = r.aoFloor;
  d[o + 2] = r.skyLift;
  d[o + 3] = r.rimWidth;
  e[o] = r.band;
  e[o + 1] = r.bandCentre;
  e[o + 2] = r.bandWidth;
  e[o + 3] = 0;
}

/** The character table packed for the shader: five vec4 arrays of `REGION_COUNT`. */
export function packRegionTable(table: Record<CharacterRegion, Partial<ToonRamp>> = CHARACTER_TOON): { a: Float32Array; b: Float32Array; c: Float32Array; d: Float32Array; e: Float32Array } {
  const a = new Float32Array(REGION_COUNT * 4);
  const b = new Float32Array(REGION_COUNT * 4);
  const c = new Float32Array(REGION_COUNT * 4);
  const d = new Float32Array(REGION_COUNT * 4);
  const e = new Float32Array(REGION_COUNT * 4);
  CHARACTER_REGIONS.forEach((name, i) => {
    const r = resolveRamp(table[name]);
    packRamp(r, a, b, c, i);
    packCharacterFields(r, d, e, i);
  });
  return { a, b, c, d, e };
}

// ------------------------------------------------------------ vertex alpha

/**
 * The finish codes in the vertex colour alpha (finish.ts reads them): the
 * byte is `region · 16 + finish · 4`. Region 15 and finish 3 (the byte 255
 * of files and geometry made before the toon look) read as `neutral` and
 * no finish.
 */
const FINISH_CODES: Record<Finish, number> = { none: 0, shimmer: 1, colour_shift: 2 };

export function toonAlpha(region: number, finish: Finish | undefined): number {
  const r = Math.min(15, Math.max(0, Math.floor(region)));
  return r * 16 + FINISH_CODES[finish ?? "none"] * 4;
}

/** The region an alpha byte carries (clamped to the table: unknown is `neutral`). */
export function regionOfAlpha(alpha: number): number {
  const r = Math.floor(Math.round(alpha) / 16);
  return r < REGION_COUNT ? r : regionIndex("neutral");
}

/** The finish an alpha byte carries. */
export function finishOfToonAlpha(alpha: number): Finish {
  const f = Math.floor((Math.round(alpha) % 16) / 4);
  return f === 1 ? "shimmer" : f === 2 ? "colour_shift" : "none";
}

// ------------------------------------------------------------ grading

/** The final grade every toon material applies (no filmic curve: it greys flat colour). */
export const TOON_GRADE = {
  /** Overall saturation multiplier. */
  saturation: 1.06,
  /** Above this value a soft shoulder rolls highlights off instead of clipping. */
  shoulder: 0.86,
};

/** The grade's tone curve on one channel (mirrors the shader; for tests and tools). */
export function shoulderCurve(v: number, start = TOON_GRADE.shoulder): number {
  if (v <= start) return v;
  const room = 1 - start;
  return 1 - room * Math.exp(-(v - start) / room);
}

// ------------------------------------------------------------ screen-space AO

/**
 * The High-only screen-space AO's look (castle D1, docs/art/castle-kit.md),
 * from data/art/toon_palette.json `post.ssao`: art data, so swapping
 * the palette re-tints it.
 */
export interface SsaoLook {
  /** The multiplier at full occlusion (sRGB 0..1): a mauve, never grey. */
  tint: Rgb;
  /** How far full occlusion goes toward the tint (0..1). */
  strength: number;
  /** World radius of the samples (m). */
  radius: number;
  /** Distance (m) by which the AO has faded out. */
  fadeEnd: number;
  /** The share of the strength on character pixels (contact only). */
  characters: number;
}

export const SSAO_DEFAULT: SsaoLook = { tint: [0.7, 0.62, 0.8], strength: 0.55, radius: 0.6, fadeEnd: 60, characters: 0.3 };

/** The `post.ssao` block as a full look, every value clamped (missing values take `SSAO_DEFAULT`). */
export function parseSsao(json: unknown): SsaoLook {
  const j = ((json as { post?: { ssao?: Partial<SsaoLook> } } | null)?.post?.ssao ?? {}) as Partial<SsaoLook>;
  const d = SSAO_DEFAULT;
  const t = Array.isArray(j.tint) && j.tint.length === 3 ? j.tint : d.tint;
  return {
    tint: [clamp(t[0], 0, 1), clamp(t[1], 0, 1), clamp(t[2], 0, 1)],
    strength: clamp(j.strength ?? d.strength, 0, 1),
    radius: clamp(j.radius ?? d.radius, 0.05, 3),
    fadeEnd: clamp(j.fadeEnd ?? d.fadeEnd, 1, 500),
    characters: clamp(j.characters ?? d.characters, 0, 1),
  };
}

export const TOON_SSAO: SsaoLook = parseSsao(toonPaletteJson);
