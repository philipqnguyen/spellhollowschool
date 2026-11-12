// The toon lighting model as a plugin on Babylon's StandardMaterial: the
// anime look of docs/art/style-guide.md ("Toon rendering") without a single
// extra draw call. The scene keeps its usual lights (the "sky" hemisphere,
// the sun or moon, room lamps, torches, lit wands, the sun's shadow map on
// High) and every lit material keeps Babylon's fog, skinning and texture
// paths; the plugin only replaces how the lights add up:
//
// - each light's contribution is collected by the material's own light loop
//   (Babylon's `CUSTOMUSERLIGHTING` hook plus a few lines at each light's
//   `CUSTOM_LIGHT{X}_COLOR` point), so lights are evaluated once, as before;
// - the hemisphere is the ambient fill (sky above, warm bounce below),
//   smooth; the key light (sun or moon) becomes a 2-band step with a narrow
//   soft edge; the shadow band keeps part of the light, tinted warm; where
//   the key is weak next to the fill (rooms: no sun indoors) the fill counts
//   as lit, so a room does not turn into one shadow band; local lights
//   (torches, room lamps, lit wands) add soft pools on top and never darken
//   the fill;
// - then a thin rim on the shadow side, a crisp highlight where the row asks
//   for one, a little extra saturation in the shadow band and a soft
//   shoulder instead of clipping (palette.ts `TOON_GRADE`).
//
// The ramp row comes from palette.ts: per material for the world (uniforms
// `toonMatA/B/C/D/E`), per vertex for characters: the vertex shader reads the
// region from the vertex colour alpha and hands it on as one flat varying
// (a single float per vertex: on tile-based GPUs every varying is written
// to memory, and 50 skinned characters are over a million vertices); the
// fragment shader indexes the uniform arrays `toonRegionA/B/C` with it.
//
// With the outline pass on (toon/post.ts), opaque toon materials also write
// the row's outline weight into the frame's alpha channel, which the pass
// reads to decide how strong a line each pixel may get: characters as
// weight / 2 (below 0.5), the world as 0.5 + weight / 2, so the pass can
// also tell a character's edge from the world's (post.ts
// `decodeOutlineAlpha`).
//
// Glow (castle kit M2, docs/art/castle-kit.md "Glow"): a material made with
// `glow` (the props atlas: lanterns, lamp newels, braziers, candles) shows
// its emissive texels as painted, after the grade: no light, rim, highlight,
// saturation or shoulder on top. The painted glow colours (#FFA94D, #FFAD5C)
// keep red at 1.0, so the bloom's bright pass halos them, and green and blue
// low enough that neither the lit colour nor the bloom pushes them to white
// (`glowOnScreen` is the same arithmetic, for tests). Texels without glow
// light as before; the mix follows the glow image's brightness, so the
// half-resolution mask's edges blend.
//
// Characters (docs/art/character-adoption.md section 5) also read the baked
// shade layer, a `toonShade` vertex attribute (4 normalised bytes: crease
// shade, the hair's root-to-tip coordinate, its band phase, an outline
// weight) handed on as one `vec4` varying: creases move the key's threshold
// (`shadeShift`) so they fall into the tinted shadow band instead of going
// grey, the fill keeps `aoFloor` in the deepest crease and is brighter
// facing up (`skyLift`), the rim lights both bands (`rimWidth`, brighter on
// the lit side), hair gets its highlight band (angel ring) along the
// root-to-tip coordinate, and the outline weight is the row's times the
// vertex's. The `face` region (the skin of the head) takes no shadow-map
// shadow (clean faces); the fringe's soft shadow on the forehead is folded
// into the shade at assembly (characters/assemble.ts).
//
// Character shadows (Medium and High, scene.ts): a shadow-only directional
// light whose `range` is negative (`CHARACTER_SHADOW_RANGE`) carries the
// shadow map of the nearest characters. The plugin adds no light for it: its
// shadow term only moves the lit share into the shadow band, as a cast
// shadow should. It reaches only the meshes in its layer (character meshes
// and floors). A mesh may also say `metadata.toonKeyShadow === false`: the
// key light's own shadow map (High sun shadows) then does not darken it
// (characters and indoor floors, which would otherwise sit in the roof's
// shadow).
//
// The castle's depth pass (castle D1, docs/art/castle-kit.md "Shading: the AO bake"): the
// world takes the characters' shading model. Kit cells carry a baked shade
// value x in COLOR_0 (r = g = b: a ray-cast AO and a gentle wall-base
// gradient, greyscale, never a colour). A world row with `shadeShift`,
// `aoFloor` or `valueFloor` (palette.ts `worldShades`) on a mesh with vertex
// colours compiles `TOON_WORLD_SHADE`: Babylon's albedo multiply by COLOR_0
// becomes `toonShadeX = pow(vColor.r, 2.2)` (the bytes arrive sRGB-converted,
// convert.ts), and x moves the key's threshold (`shadeShift`: corners fall
// into the tinted shadow band, softly), keeps `aoFloor` of the fill and the
// torch pools in the deepest corner, and `valueFloor` of the colour (some
// darkening where the key is weak: rooms, night). Hue stays the palette's.
// `skyLift` lifts the fill facing up and lowers it facing down; on such a
// row the hemisphere's ground term takes the sky's hue at the ground's
// level, so undersides never take the brown bounce (the Grand Staircase
// look-up). A row that sets `rimWidth` takes the characters' rim (rim mode
// in `toonMatE.w`). A row with `nearFade` (walk-through props and cloth)
// compiles `TOON_NEARFADE`: a 4 x 4 screen-door discard from fully drawn at
// its far distance to gone at its near one, before any texture or light
// work. The world fields are `toonMatD` (shadeShift, aoFloor, skyLift,
// rimWidth) and `toonMatE` (valueFloor, nearFade near, far, rim mode).
//
// Faces (character look, docs/art/character-look.md section 3): a character
// material given a body's face textures (`setFace`) compiles `TOON_FACE`: on
// the `face` region the albedo comes from the painted face texture instead of
// the atlas, `c.rgb * mix(1, skin, c.a) * mix(1, eye, m.r) * mix(1, hair, m.g)`
// (`faceAlbedo`), with the skin tone the vertex colour and the eye and hair
// colours looked up in the creator's palettes (uniform arrays) by the indices
// assembly writes into the face vertices' `toonShade.y` and `.z`; the mask's
// flat weight (eye white, iris, highlights, lashes, mouth line) is always lit,
// with no rim and no line. At Medium and High (`setToonFaceShadow`) it also
// compiles `TOON_FACE_SDF`: the key light's lit share on the face comes from
// the face-shadow threshold map (the mask's B) instead of the toon step: the
// vertex shader turns the key's direction (`toonKeyDir`, written once a frame
// by the character renderer, `setToonKeyDirection`) into the skinned head's
// frame (one vec2 varying), the fragment shader reads the threshold (mirrored
// in u for a light on the -X side) and steps it at the light's angle
// (`faceSdfLit`).
//
// No per-frame work beyond that one direction: uniforms are written when
// Babylon re-syncs a material's uniform buffer, and nothing is allocated after
// creation.

import { MaterialPluginBase } from "@babylonjs/core/Materials/materialPluginBase";
import { ShaderLanguage } from "@babylonjs/core/Materials/shaderLanguage";
import type { Material } from "@babylonjs/core/Materials/material";
import type { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import type { UniformBuffer } from "@babylonjs/core/Materials/uniformBuffer";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Scene } from "@babylonjs/core/scene";
import type { AbstractEngine } from "@babylonjs/core/Engines/abstractEngine";
import type { SubMesh } from "@babylonjs/core/Meshes/subMesh";
import type { BaseTexture } from "@babylonjs/core/Materials/Textures/baseTexture";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import { CHARACTER_TOON, packRamp, packRegionTable, packWorldFields, regionIndex, REGION_COUNT, TOON_GRADE, worldNearFades, worldShades, type ToonRamp } from "./palette";
import { RIM_COLOUR } from "./lighting";
import { CREATOR, eyeColourHex, hairColourHex, hexToRgb01 } from "../../content/catalog";

/** The key light's step half-width far off, where a row asks for the distance fade (`farStart`, `farEnd`). */
export const FAR_SOFTNESS = 0.8;

/** The glow image's brightest channel where its texel starts replacing the lit colour, and fully replaces it. */
export const GLOW_MIX: readonly [number, number] = [0.2, 0.6];
/**
 * Animated fire (game/fire/): how far flame-coloured glow texels (orange:
 * G under 0.75, B under 0.4) swing in G and B with the fire clock, which
 * game/fire/field.ts writes here once a frame (`t`, seconds).
 */
export const GLOW_FLICKER = 0.06;
export const GLOW_CLOCK = { t: 0 };

/** What `glowOnScreen` reports: a glow texel before and after the bloom (sRGB 0..1). */
export interface GlowPixel {
  before: [number, number, number];
  after: [number, number, number];
}

/**
 * A glow texel of colour `glow` (sRGB 0..1, the emissive image's texel at
 * level 1) on screen, before and after the bloom, as the shaders compute it
 * (material.ts TOON_GLOW, post.ts BRIGHT and FINAL) for a glow area wider
 * than the bloom's blur, so the bloom at its core is the full cut colour.
 * `bloom` is { threshold, knee, strength } (post.ts TOON_POST), or null
 * (Low: no passes).
 */
export function glowOnScreen(glow: readonly [number, number, number], bloom: { threshold: number; knee: number; strength: number } | null): GlowPixel {
  const before: [number, number, number] = [clamp01(glow[0]), clamp01(glow[1]), clamp01(glow[2])];
  if (!bloom) return { before, after: [...before] };
  const m = Math.max(before[0], before[1], before[2]);
  const t = Math.min(1, Math.max(0, (m - bloom.threshold) / Math.max(1e-6, bloom.knee)));
  const k = t * t * (3 - 2 * t);
  // The final pass adds the bloom unclamped; the canvas clips at 1.
  const after = before.map((c) => Math.min(1, c + c * k * bloom.strength)) as [number, number, number];
  return { before, after };
}

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v));
}

/** Lights per material the plugin hooks (characters use up to 8, plus the character shadow light). */
export const TOON_MAX_LIGHTS = 9;

/** Characters: the share of the rim added on top of the albedo on dark colours (black robes keep an edge). */
export const CHARACTER_DARK_RIM = 0.3;

/**
 * Hair: how far the highlight band's brightness (luma) is lifted from the
 * hair colour's toward white. The band keeps the hair's hue and saturation
 * (`hairBandColour`): a lighter tone of the same colour, not a grey.
 */
export const HAIR_BAND_LIFT = 0.42;

/** Hair: the band's share of the hair colour's saturation (the rest goes to its grey: a sheen, not a stripe of paint). */
export const HAIR_BAND_SATURATION = 0.85;

/** Hair: the band's core and edge (fractions of `bandWidth`): flat to the first, gone at the second. */
export const HAIR_BAND_EDGE: readonly [number, number] = [0.25, 0.75];

/**
 * The hair highlight band's colour for a hair albedo `base` (0..1), as the
 * shader computes it: the base scaled (hue and saturation kept) to the luma
 * `l + (1 - l) * HAIR_BAND_LIFT`; where that would push a channel past 1
 * (light hair), it is capped and the rest of the lift comes from white;
 * then HAIR_BAND_SATURATION of its saturation is kept (luma unchanged).
 * Writes into `out`.
 */
export function hairBandColour(base: readonly number[], out: number[] = [0, 0, 0]): number[] {
  const luma = (c: readonly number[]): number => 0.3 * c[0]! + 0.59 * c[1]! + 0.11 * c[2]!;
  const l = luma(base);
  const lt = l + (1 - l) * HAIR_BAND_LIFT;
  const k = lt / Math.max(l, 0.03);
  for (let i = 0; i < 3; i++) out[i] = base[i]! * k;
  const m = Math.max(out[0]!, out[1]!, out[2]!);
  if (m > 1) for (let i = 0; i < 3; i++) out[i] = out[i]! / m;
  const lc = luma(out);
  const w = Math.min(1, Math.max(0, (lt - lc) / Math.max(1 - lc, 1e-3)));
  for (let i = 0; i < 3; i++) out[i] = out[i]! + (1 - out[i]!) * w;
  const lf = luma(out);
  for (let i = 0; i < 3; i++) out[i] = lf + (out[i]! - lf) * HAIR_BAND_SATURATION;
  return out;
}

/** The `range` that marks the character shadow light (scene.ts): its shadow, no light. */
export const CHARACTER_SHADOW_RANGE = -1;

/** The `face` region's index (palette.ts `CHARACTER_REGIONS`). */
const FACE_REGION = regionIndex("face");
const NEUTRAL_REGION = regionIndex("neutral");

/** Entries of the eye and hair colour palettes the face shader holds (creator.json indices). */
export const FACE_PALETTE_SIZE = 32;

/** The face-shadow step's half-width in light angle / 180 (the `face` row's `faceSdfSoftness`, default 0.02). */
export const FACE_SDF_SOFTNESS = faceSdfSoftness((CHARACTER_TOON.face as { faceSdfSoftness?: unknown }).faceSdfSoftness);

/** A `faceSdfSoftness` value from the art data, clamped (0.002..0.2), 0.02 when absent. */
export function faceSdfSoftness(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(0.2, Math.max(0.002, v)) : 0.02;
}

/** With the face-shadow map, the face's shade x (the fringe's shadow) is in the shadow band below the first, lit above the second. */
export const FACE_FRINGE_STEP: readonly [number, number] = [0.45, 0.9];

/** Below this horizontal length of the key's direction (overhead) the face-shadow map fades out, fully above the second. */
export const FACE_SDF_FADE: readonly [number, number] = [0.15, 0.5];

/**
 * The face's albedo (sRGB 0..1) from the face texture's texel `c` (RGBA),
 * the mask's `m` (RGBA) and the skin, eye and hair colours, as the shader
 * computes it (`TOON_FACE`). Writes into `out`.
 */
export function faceAlbedo(c: readonly number[], m: readonly number[], skin: readonly number[], eye: readonly number[], hair: readonly number[], out: number[] = [0, 0, 0]): number[] {
  // [look2 Face] the paint's lines deepened on dark skin (faceLinePaint).
  faceLinePaint(c, skin, LINE_PAINT);
  // [look2 Review] skin and hair weights blend linearly: the
  // paint lays hair over skin, so at the hairline and the brow edges the two
  // weights share a texel; their product lit a halo there on dark skin.
  const n = Math.max(1, c[3]! + m[1]!);
  for (let k = 0; k < 3; k++) {
    out[k] = LINE_PAINT[k]! * (1 + ((skin[k]! - 1) * c[3]! + (hair[k]! - 1) * m[1]!) / n) * (1 + (eye[k]! - 1) * m[0]!);
  }
  return out;
}

// ---- [look2 Face] begin: face lines, iris glow (docs/art/character-look.md section 3) ----
const LINE_PAINT = [1, 1, 1];

/** A number field of the `face` palette row, clamped to [lo, hi]; `dflt` when absent. */
function faceField(name: string, lo: number, hi: number, dflt: number): number {
  const v = (CHARACTER_TOON.face as Record<string, unknown>)[name];
  return typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : dflt;
}

/**
 * Lines on the skin (the mouth line, the nose shade, the lid crease: paint
 * multipliers with R < 1; blush and lip tint keep R = 1) darken any skin at
 * least as much, in sRGB value, as they darken a skin of this luma (the
 * `face` row's `faceLineSkin`): lighter skins keep the paint as it is,
 * darker ones get deeper lines in the paint's own warm hue.
 */
export const FACE_LINE_SKIN = faceField("faceLineSkin", 0, 1, 0.5);
/** The deepest a line may take the skin to (a share of the skin's value, the row's `faceLineFloor`). */
export const FACE_LINE_FLOOR = faceField("faceLineFloor", 0.05, 1, 0.3);
/** The iris and catchlight glow: the share of their albedo added unlit (the row's `faceIrisGlow`). */
export const FACE_IRIS_GLOW = faceField("faceIrisGlow", 0, 1, 0.25);
/** The face mask's A: the flat weight up to this level, the glow above it (face_paint.FLAT_LEVEL). */
export const FACE_FLAT_LEVEL = 192 / 255;
/** Skin weights above this take the line rule fully (the scalp and lash edges, which mix skin with paint, do not). */
const FACE_LINE_SKIN_WEIGHT: readonly [number, number] = [0.9, 0.99];
/** A texel is a line from this 1 - R (fully from the second): blush and lip tint (R = 1) never are. */
const FACE_LINE_R: readonly [number, number] = [0.003, 0.03];

/** sRGB luma as the toon shader weighs it. */
function luma(c: readonly number[]): number {
  return 0.3 * c[0]! + 0.59 * c[1]! + 0.11 * c[2]!;
}

/**
 * The face paint's RGB with its lines deepened for the skin tone `skin`
 * (sRGB), as the shader does it (`TOON_FACE`): a line's ratio r (its R) goes
 * to q = max(1 - FACE_LINE_SKIN (1 - r) / luma(skin), FACE_LINE_FLOOR) when q
 * is below r, the whole multiplier raised to log q / log r (its hue kept),
 * only where the paint is a line on skin (skin weight near 1, R below 1).
 * Writes into `out`.
 */
export function faceLinePaint(c: readonly number[], skin: readonly number[], out: number[] = [0, 0, 0]): number[] {
  const r = Math.min(c[0]!, 0.999);
  const ls = Math.max(luma(skin), 0.02);
  const q = Math.max(1 - (FACE_LINE_SKIN * (1 - r)) / ls, FACE_LINE_FLOOR);
  const on = smooth(FACE_LINE_SKIN_WEIGHT[0], FACE_LINE_SKIN_WEIGHT[1], c[3]!) * smooth(FACE_LINE_R[0], FACE_LINE_R[1], 1 - c[0]!);
  const p = 1 + (Math.max(1, Math.log(q) / Math.log(r)) - 1) * on;
  for (let k = 0; k < 3; k++) out[k] = Math.pow(c[k]!, p);
  return out;
}

/** The face mask's A decoded: [flat weight, glow weight] (face_paint.FLAT_LEVEL). */
export function faceFlatGlow(a: number, out: number[] = [0, 0]): number[] {
  out[0] = Math.min(1, a / (FACE_FLAT_LEVEL - 0.002));
  out[1] = Math.min(1, Math.max(0, (a - FACE_FLAT_LEVEL) / (1 - FACE_FLAT_LEVEL)));
  return out;
}
// ---- [look2 Face] end ----

/**
 * The face-shadow map's lit share (`TOON_FACE_SDF`) for a key light whose
 * direction toward the light, in the head's frame, is (`side`: along the
 * head's +X, `fwd`: along its facing). `threshold(mirror)` reads the map's B
 * at the texel (mirrored in u when the light is on the -X side).
 */
export function faceSdfLit(side: number, fwd: number, threshold: (mirror: boolean) => number, softness = FACE_SDF_SOFTNESS): number {
  const lh = Math.sqrt(side * side + fwd * fwd);
  const t = Math.acos(Math.min(1, Math.max(-1, fwd / Math.max(lh, 1e-4)))) / Math.PI;
  const thr = threshold(side < 0);
  const lit = smooth(t - softness, t + softness, thr);
  const fade = smooth(FACE_SDF_FADE[0], FACE_SDF_FADE[1], lh);
  return 1 + (lit - 1) * fade;
}

function smooth(a: number, b: number, v: number): number {
  const t = Math.min(1, Math.max(0, (v - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

/** The creator's eye and hair colours as the face shader's palettes (sRGB, 4 floats per entry). */
export function facePalettes(creator = CREATOR): { eye: Float32Array; hair: Float32Array } {
  const eye = new Float32Array(FACE_PALETTE_SIZE * 4);
  const hair = new Float32Array(FACE_PALETTE_SIZE * 4);
  for (let i = 0; i < FACE_PALETTE_SIZE; i++) {
    const e = hexToRgb01(eyeColourHex(i, creator));
    const h = hexToRgb01(hairColourHex(i, creator));
    eye.set([e[0], e[1], e[2], 1], i * 4);
    hair.set([h[0], h[1], h[2], 1], i * 4);
  }
  return { eye, hair };
}

let palettes: { eye: Float32Array; hair: Float32Array } | null = null;

/**
 * Live hair slots (Chameleon Tonic): the hair palette's entries past the
 * creator's colours. A character whose hair is recoloured live claims one;
 * its face vertices carry that slot as their hair index, so its brows and
 * lashes take the live colour (written here as it steps) without touching
 * anyone else's. Slots are few (FACE_PALETTE_SIZE less the creator's
 * colours): when none is free the face keeps the chosen hair colour.
 */
const liveHairUsed = new Uint8Array(FACE_PALETTE_SIZE);

/** The first live hair slot (the creator's hair colours come before it). */
export function firstLiveHairSlot(creator = CREATOR): number {
  return Math.min(FACE_PALETTE_SIZE, creator.hair_colours.length);
}

/** Claims a free live hair slot, set to the hair palette's colour `start`; -1 when none is free. */
export function claimLiveHairSlot(start: number): number {
  palettes ??= facePalettes();
  for (let i = firstLiveHairSlot(); i < FACE_PALETTE_SIZE; i++) {
    if (liveHairUsed[i]) continue;
    liveHairUsed[i] = 1;
    const s = Math.min(FACE_PALETTE_SIZE - 1, Math.max(0, start)) * 4;
    palettes.hair.copyWithin(i * 4, s, s + 4);
    return i;
  }
  return -1;
}

/** Writes a live hair slot's colour (sRGB 0..1). No allocation. */
export function setLiveHairColour(slot: number, r: number, g: number, b: number): void {
  if (slot < firstLiveHairSlot() || slot >= FACE_PALETTE_SIZE) return;
  palettes ??= facePalettes();
  palettes.hair[slot * 4] = r;
  palettes.hair[slot * 4 + 1] = g;
  palettes.hair[slot * 4 + 2] = b;
}

/** Frees a live hair slot (and puts back the palette's own entry for it). */
export function releaseLiveHairSlot(slot: number): void {
  if (slot < firstLiveHairSlot() || slot >= FACE_PALETTE_SIZE || !liveHairUsed[slot]) return;
  liveHairUsed[slot] = 0;
  palettes ??= facePalettes();
  palettes.hair.set(facePalettes().hair.subarray(slot * 4, slot * 4 + 4), slot * 4);
}

/** The hair palette entry the face shader reads at `slot` (tests). */
export function hairPaletteEntry(slot: number): number[] {
  palettes ??= facePalettes();
  return Array.from(palettes.hair.subarray(slot * 4, slot * 4 + 3));
}

/** The key light's direction toward the light (world), for the face shadow; written once a frame. */
const KEY_DIR = new Float32Array([0, 1, 0, 0]);

/**
 * At night the face shadow never takes the moon as overhead: its elevation is
 * held to at most this (rad, 40 degrees), so the face-shadow map keeps a
 * lit side and a shadow side instead of fading to fully lit (the fade is
 * for a noon sun; a face lit flat by a high moon glowed against the night).
 */
export const FACE_NIGHT_MAX_ELEVATION = (40 * Math.PI) / 180;

/** 0 by day, 1 when the key is the moon (blended across the hand-over); clock/daynight.ts sets it. */
let keyNight = 0;
/** The last horizontal direction of the key (for a light straight overhead). */
const KEY_AZ = new Float32Array([0, 1]);

/** How much the key is the moon (0..1), from the castle light (clock/daynight.ts). */
export function setToonKeyNight(moon: number): void {
  keyNight = Math.min(1, Math.max(0, moon));
}

/**
 * Sets the key light's direction (toward the light, world space) the face
 * shadow uses; at night its elevation is held to FACE_NIGHT_MAX_ELEVATION
 * (blended by how much the key is the moon). No allocation.
 */
export function setToonKeyDirection(x: number, y: number, z: number): void {
  const l = Math.sqrt(x * x + y * y + z * z) || 1;
  x /= l;
  y /= l;
  z /= l;
  const h = Math.sqrt(x * x + z * z);
  if (h > 1e-4) {
    KEY_AZ[0] = x / h;
    KEY_AZ[1] = z / h;
  }
  if (keyNight > 0 && y > Math.sin(FACE_NIGHT_MAX_ELEVATION)) {
    const cy = Math.sin(FACE_NIGHT_MAX_ELEVATION);
    const ch = Math.cos(FACE_NIGHT_MAX_ELEVATION);
    const t = keyNight;
    x += (KEY_AZ[0]! * ch - x) * t;
    y += (cy - y) * t;
    z += (KEY_AZ[1]! * ch - z) * t;
    const m = Math.sqrt(x * x + y * y + z * z) || 1;
    x /= m;
    y /= m;
    z /= m;
  }
  KEY_DIR[0] = x;
  KEY_DIR[1] = y;
  KEY_DIR[2] = z;
}

/** The key direction as last set (tests, probes). */
export function toonKeyDirection(): Float32Array {
  return KEY_DIR;
}

let faceShadow = false;

/** The face-shadow map at Medium and High (`TOON_FACE_SDF`); Low keeps the toon step on the face's normals. */
export function setToonFaceShadow(on: boolean): void {
  if (on === faceShadow) return;
  faceShadow = on;
  for (const p of LIVE) p.markAllDefinesAsDirty();
}

export function toonFaceShadow(): boolean {
  return faceShadow;
}

/** A body's face textures (tools/blender/characters/charlib/face_paint.py). */
export interface FaceTextures {
  colour: BaseTexture;
  mask: BaseTexture;
}

/** GLSL float literal. */
const f = (v: number): string => (Number.isInteger(v) ? `${v}.0` : `${v}`);

/** Shared by the world and character variants: the light loop and the composition. */
function fragmentCode(ubo: boolean): { [point: string]: string } {
  // Without uniform buffers Babylon names light uniforms vLightData0 instead of light0.vLightData.
  const L = (i: number, field: string): string => (ubo ? `light${i}.${field}` : `${field}${i}`);
  const code: { [point: string]: string } = {
    CUSTOM_FRAGMENT_DEFINITIONS: `
#ifdef TOON
#ifdef TOON_REGIONS
flat varying float vToonRegion;
#ifdef TOON_SHADE
varying vec4 vToonShade;
#endif
float toonFace;
#ifdef TOON_FACE
uniform sampler2D faceColourSampler;
uniform sampler2D faceMaskSampler;
// The face mask's texel (R eye, G hair, B shadow threshold, A flat) and the flat weight.
vec4 toonFm;
float toonFaceFlat;
// [look2 Face] the mask A's glow (the iris and catchlights), added unlit.
float toonFaceGlow;
#endif
#ifdef TOON_FACE_SDF
varying vec2 vToonFaceL;
float toonFaceLit;
#endif
#endif
// D and E: the regions' character fields, or the world's shade fields (castle D1, palette.ts packWorldFields).
vec4 toonPD;
vec4 toonPE;
// The baked shade x (characters: the shade layer; the world: COLOR_0 under TOON_WORLD_SHADE); 1 = open.
float toonShadeX;
float toonCharShadow;
vec4 toonPA;
vec4 toonPB;
vec4 toonPC;
float toonKind;
vec3 toonLc;
float toonNdl;
vec3 toonL;
vec3 toonV;
vec3 toonN;
vec3 toonAmb;
vec3 toonLitSum;
vec3 toonDarkSum;
vec3 toonLocalLit;
vec3 toonLocalDark;
vec3 toonSpecSum;
float toonLitW;
float toonAllW;
float toonFar;
vec3 computeCustomDiffuseLighting(lightingInfo info, vec3 diffuseBase, float shadow) {
  if (toonKind > 2.5) {
    // The character shadow light: its shadow only (faces stay clean).
#ifdef TOON_REGIONS
    toonCharShadow = min(toonCharShadow, mix(shadow, 1.0, toonFace));
#else
    toonCharShadow = min(toonCharShadow, shadow);
#endif
    return vec3(0.0);
  }
  if (toonKind > 0.5) {
    // Far off (the row's fade distances), the step widens into a soft gradient: thin
    // facets (ribs, mouldings) stop flickering between the bands (castle kit M3 + M4, 17.12).
    float toonSoft = mix(toonPB.y, ${f(FAR_SOFTNESS)}, toonFar);
    // Creases and corners (the baked shade) fall into the tinted shadow band.
    float toonT = toonPB.x + (1.0 - toonShadeX) * toonPD.x;
#ifdef TOON_KEY_SHADOW
    float toonSh = shadow;
#else
    float toonSh = 1.0;
#endif
    float toonStep = smoothstep(toonT - toonSoft, toonT + toonSoft, toonNdl);
#ifdef TOON_FACE_SDF
    // The face's key comes from the face-shadow map (local lights keep their pools).
    // The fringe's soft shadow (folded into the shade at assembly) still falls into the band.
    if (toonKind < 1.5 && toonFace > 0.5) toonStep = toonFaceLit * smoothstep(${f(FACE_FRINGE_STEP[0])}, ${f(FACE_FRINGE_STEP[1])}, toonShadeX);
#endif
    float lit = toonStep * smoothstep(0.3, 0.7, toonSh);
    if (toonKind > 1.5) {
      // A local light (torch, lamp, lit wand): a soft pool of its own (a wide
      // wrap instead of the key's narrow step: a lamp or a wand held close
      // would otherwise cut every facet of a low-poly face into blotches).
      float soft = smoothstep(-0.25, 0.65, toonNdl);
      toonLocalLit += toonLc * soft;
      toonLocalDark += toonLc * (1.0 - soft);
    } else {
      // The key (sun or moon) decides the bands.
      toonLitSum += toonLc * lit;
      toonDarkSum += toonLc * (1.0 - lit);
      float w = dot(toonLc, vec3(0.3, 0.59, 0.11));
      toonLitW += w * lit;
      toonAllW += w;
    }
    if (toonPB.w > 0.0) toonSpecSum += toonLc * (lit * smoothstep(0.955, 0.972, dot(toonN, normalize(toonL + toonV))));
  }
  return vec3(0.0);
}
#ifdef SPECULARTERM
vec3 computeCustomSpecularLighting(lightingInfo info, vec3 specularBase, float shadow) {
  return vec3(0.0);
}
#endif
#ifdef TOON_NEARFADE
// A 4 x 4 ordered (Bayer) threshold in [0, 1) for the screen-door fade.
float toonBayer2(vec2 a) {
  a = floor(a);
  return fract(a.x / 2.0 + a.y * a.y * 0.75);
}
float toonBayer4(vec2 a) {
  return toonBayer2(0.5 * a) * 0.25 + toonBayer2(a);
}
#endif
vec3 toonShoulder(vec3 c) {
  vec3 room = vec3(${f(1 - TOON_GRADE.shoulder)});
  vec3 over = max(c - vec3(${f(TOON_GRADE.shoulder)}), vec3(0.0));
  return mix(c, vec3(1.0) - room * exp(-over / room), step(vec3(${f(TOON_GRADE.shoulder)}), c));
}
#endif
`,
    CUSTOM_FRAGMENT_MAIN_BEGIN: `
#ifdef TOON
toonShadeX = 1.0;
#ifdef TOON_NEARFADE
// Walk-through pieces (castle D1): a screen-door fade from fully drawn at
// nearFade's far to gone at its near, before any texture or light work.
if (smoothstep(toonMatE.y, toonMatE.z, length(vEyePosition.xyz - vPositionW)) < toonBayer4(gl_FragCoord.xy) + 0.03125) discard;
#endif
#endif
`,
    // Castle D1: on the world shade path COLOR_0 is the baked shade x
    // (grey, sRGB-converted at load: convert.ts), read as shade, never
    // multiplied into the albedo; elsewhere Babylon's multiply stays.
    // Character look (section 3): on the face the albedo is the painted face
    // texture, tinted by skin (vertex colour), eye and hair (palettes).
    "!baseColor\\.rgb\\*=vColor\\.rgb;": `
#ifdef TOON_WORLD_SHADE
toonShadeX=pow(vColor.r,2.2);
#elif defined(TOON_FACE)
toonFm = vec4(0.0);
toonFaceFlat = 0.0;
toonFaceGlow = 0.0;
if (int(vToonRegion + 0.5) == ${FACE_REGION}) {
  vec4 toonFc = texture2D(faceColourSampler, vDiffuseUV);
  toonFm = texture2D(faceMaskSampler, vDiffuseUV);
  vec3 toonEye = toonEyeColours[int(min(vToonShade.y * 255.0 + 0.5, ${f(FACE_PALETTE_SIZE - 1)}))].rgb;
  vec3 toonHair = toonHairColours[int(min(vToonShade.z * 255.0 + 0.5, ${f(FACE_PALETTE_SIZE - 1)}))].rgb;
  // [look2 Face] lines on the skin keep their contrast on dark skin (faceLinePaint).
  float toonLr = min(toonFc.r, 0.999);
  float toonLq = max(1.0 - ${f(FACE_LINE_SKIN)} * (1.0 - toonLr) / max(dot(vColor.rgb, vec3(0.3, 0.59, 0.11)), 0.02), ${f(FACE_LINE_FLOOR)});
  float toonLp = 1.0 + (max(1.0, log(toonLq) / log(toonLr)) - 1.0) * smoothstep(${f(FACE_LINE_SKIN_WEIGHT[0])}, ${f(FACE_LINE_SKIN_WEIGHT[1])}, toonFc.a) * smoothstep(${f(FACE_LINE_R[0])}, ${f(FACE_LINE_R[1])}, 1.0 - toonFc.r);
  // [look2 Review] skin and hair blend linearly where they share a texel (no halo at the hairline on dark skin; faceAlbedo).
  baseColor.rgb = pow(toonFc.rgb, vec3(toonLp)) * (1.0 + ((vColor.rgb - 1.0) * toonFc.a + (toonHair - 1.0) * toonFm.g) / max(1.0, toonFc.a + toonFm.g)) * mix(vec3(1.0), toonEye, toonFm.r);
  // [look2 Face] the mask A: flat up to FACE_FLAT_LEVEL, glow above it (faceFlatGlow).
  toonFaceFlat = min(1.0, toonFm.a * ${f(1 / (FACE_FLAT_LEVEL - 0.002))});
  toonFaceGlow = clamp((toonFm.a - ${f(FACE_FLAT_LEVEL)}) * ${f(1 / (1 - FACE_FLAT_LEVEL))}, 0.0, 1.0);
} else {
  baseColor.rgb*=vColor.rgb;
}
#else
baseColor.rgb*=vColor.rgb;
#endif
`,
    CUSTOM_FRAGMENT_BEFORE_LIGHTS: `
#ifdef TOON
#ifdef TOON_REGIONS
int toonR = int(vToonRegion + 0.5);
toonPA = toonRegionA[toonR];
toonPB = toonRegionB[toonR];
toonPC = toonRegionC[toonR];
toonPD = toonRegionD[toonR];
toonPE = toonRegionE[toonR];
#ifdef TOON_SHADE
toonShadeX = vToonShade.x;
#else
toonShadeX = 1.0;
#endif
toonFace = toonR == ${FACE_REGION} ? 1.0 : 0.0;
#ifdef TOON_FACE_SDF
// The face shadow (section 3.4): the key's angle round the head's up axis
// against the threshold map (mirrored for a light on the -X side).
toonFaceLit = 1.0;
if (toonFace > 0.5) {
  float toonLh = length(vToonFaceL);
  float toonT = acos(clamp(vToonFaceL.y / max(toonLh, 1e-4), -1.0, 1.0)) * 0.31830989;
  vec2 toonFuv = vToonFaceL.x >= 0.0 ? vDiffuseUV : vec2(1.0 - vDiffuseUV.x, vDiffuseUV.y);
  float toonThr = texture2D(faceMaskSampler, toonFuv).b;
  toonFaceLit = mix(1.0, smoothstep(toonT - toonFaceParams.x, toonT + toonFaceParams.x, toonThr), smoothstep(${f(FACE_SDF_FADE[0])}, ${f(FACE_SDF_FADE[1])}, toonLh));
}
#endif
#else
toonPA = toonMatA;
toonPB = toonMatB;
toonPC = toonMatC;
toonPD = toonMatD;
toonPE = toonMatE;
#endif
toonKind = 0.0;
toonLc = vec3(0.0);
toonNdl = 0.0;
toonL = vec3(0.0, 1.0, 0.0);
toonV = viewDirectionW;
toonN = normalW;
toonAmb = vec3(0.0);
toonLitSum = vec3(0.0);
toonDarkSum = vec3(0.0);
toonLocalLit = vec3(0.0);
toonLocalDark = vec3(0.0);
toonSpecSum = vec3(0.0);
toonLitW = 0.0;
toonAllW = 0.0;
toonCharShadow = 1.0;
// The distance fade of the crisp parts (toonPC.zw: start, end in metres; 0 = off).
toonFar = toonPC.w > 0.0 ? smoothstep(toonPC.z, toonPC.w, length(vEyePosition.xyz - vPositionW)) : 0.0;
#endif
`,
    // Babylon's own per-light maths (Lambert, Blinn-Phong) is not used by the
    // toon sum: skip it rather than trust the compiler to drop it.
    "!(info=computeLighting\\([^;]*\\);)": "\n#ifndef TOON\n$1\n#endif\n",
    "!(info=computeHemisphericLighting\\([^;]*\\);)": "\n#ifndef TOON\n$1\n#endif\n",
    // After the light loop: the toon sum replaces Babylon's diffuse sum.
    "!aggShadow=aggShadow\\/numLights;": `aggShadow=aggShadow/numLights;
#ifdef TOON
// The bands come from the key light (sun or moon): where it is weak next to
// the fill, or absent (rooms: no sun indoors), the surface takes the lit
// band, not the shadow band. With the sun or moon up the key is well above
// half the fill, so the castle keeps its bands. Local lights (torches, room
// lamps, lit wands) add their own soft pools on top: their lit side full,
// their far side the shadowLight share of it, tinted; they never darken the
// fill (a wall beside a window lamp used to drop into the shadow band).
// The deepest creases and corners keep aoFloor of the fill.
toonAmb *= mix(toonPD.y, 1.0, toonShadeX);
#ifndef TOON_REGIONS
// The world's torch and lamp pools likewise (castle D1).
toonLocalLit *= mix(toonPD.y, 1.0, toonShadeX);
toonLocalDark *= mix(toonPD.y, 1.0, toonShadeX);
#endif
float toonDirectShare = toonAllW / (dot(toonAmb, vec3(0.3, 0.59, 0.11)) + 0.02);
float toonLitFrac = mix(1.0, toonLitW / (toonAllW + 0.001), smoothstep(0.1, 0.5, toonDirectShare));
// A character's cast shadow (the character shadow light) is the shadow band, in rooms too.
toonLitFrac *= toonCharShadow;
toonSpecSum *= toonCharShadow;
#ifdef TOON_FACE
// The face's flat paint (eyes, lashes, mouth line) is always lit.
toonLitFrac = mix(toonLitFrac, 1.0, toonFaceFlat);
#endif
float toonNdv = clamp(dot(normalW, viewDirectionW), 0.0, 1.0);
#ifdef TOON_REGIONS
// Characters: a stronger rim on both bands, brighter on the lit side.
float toonRim = smoothstep(1.0 - toonPD.w, 1.0, 1.0 - toonNdv) * toonPB.z * mix(0.35, 1.0, toonLitFrac) * (1.0 - toonFar);
#ifdef TOON_FACE
toonRim *= 1.0 - toonFaceFlat;
#endif
#else
// The world: rows that set rimWidth (toonPE.w, castle D1) take the characters' rim; the rest the thin shadow-side rim.
float toonRim = toonPE.w > 0.5
  ? smoothstep(1.0 - toonPD.w, 1.0, 1.0 - toonNdv) * toonPB.z * mix(0.35, 1.0, toonLitFrac) * (1.0 - toonFar)
  : smoothstep(0.7, 0.76, 1.0 - toonNdv) * toonPB.z * (1.0 - 0.7 * toonLitFrac) * (1.0 - toonFar);
#endif
vec3 toonDirect = toonLitSum + toonDarkSum;
vec3 toonShade = (toonAmb + toonDirect * toonPA.a) * toonPA.rgb;
vec3 toonLit = toonDirect + toonAmb;
vec3 toonLocal = toonLocalLit + toonLocalDark * toonPA.a * toonPA.rgb;
diffuseBase = mix(toonShade, toonLit, toonLitFrac) + toonLocal + vec3(${f(RIM_COLOUR[0])}, ${f(RIM_COLOUR[1])}, ${f(RIM_COLOUR[2])}) * (dot(toonDirect + toonAmb + toonLocalLit, vec3(0.3, 0.59, 0.11)) * 0.5 * toonRim);
#ifndef TOON_REGIONS
// Some of the baked shade survives where the key is weak (rooms, night): a value, never a hue.
diffuseBase *= mix(toonPE.x, 1.0, toonShadeX);
#endif
#endif
`,
    CUSTOM_FRAGMENT_BEFORE_FOG: `
#ifdef TOON
float toonLuma = dot(color.rgb, vec3(0.3, 0.59, 0.11));
color.rgb = max(mix(vec3(toonLuma), color.rgb, ${f(TOON_GRADE.saturation)} + toonPC.y * (1.0 - toonLitFrac)), vec3(0.0));
color.rgb += toonSpecSum * (toonPB.w * 0.8 * (1.0 - toonFar)) * mix(vec3(1.0), baseColor.rgb, 0.5);
#ifdef TOON_REGIONS
// Characters: part of the rim is not multiplied by the albedo, so dark cloth
// (black robes, dark hair) still shows its edge against the light; light
// colours already show it through the albedo.
color.rgb += vec3(${f(RIM_COLOUR[0])}, ${f(RIM_COLOUR[1])}, ${f(RIM_COLOUR[2])}) * (toonRim * ${f(CHARACTER_DARK_RIM)} * (1.0 - dot(baseColor.rgb, vec3(0.3, 0.59, 0.11))) * dot(toonDirect + toonAmb + toonLocalLit, vec3(0.3, 0.59, 0.11)));
#endif
#if defined(TOON_REGIONS) && defined(TOON_SHADE)
// Hair: the highlight band (angel ring) along the lock's root-to-tip
// coordinate, offset per lock, sliding gently with the camera's pitch; a
// lighter tone of the hair colour (its hue kept, most of its saturation), full in the
// lit band, with a flat core and a short edge (a clean ring, not a smear).
if (toonPE.x > 0.0) {
  float toonBandC = toonPE.y + (vToonShade.z - 0.5) + 0.06 * viewDirectionW.y;
  float toonBand = toonPE.x * (1.0 - smoothstep(${f(HAIR_BAND_EDGE[0])} * toonPE.z, ${f(HAIR_BAND_EDGE[1])} * toonPE.z, abs(vToonShade.y - toonBandC))) * mix(0.33, 1.0, toonLitFrac);
  // Mixed in (not added): dark hair gets a clear ring, light hair a subtle
  // one instead of a blown-out white bar (hairBandColour).
  float toonBl = dot(baseColor.rgb, vec3(0.3, 0.59, 0.11));
  float toonBt = toonBl + (1.0 - toonBl) * ${f(HAIR_BAND_LIFT)};
  vec3 toonBandCol = baseColor.rgb * (toonBt / max(toonBl, 0.03));
  toonBandCol /= max(1.0, max(toonBandCol.r, max(toonBandCol.g, toonBandCol.b)));
  float toonBc = dot(toonBandCol, vec3(0.3, 0.59, 0.11));
  toonBandCol += (vec3(1.0) - toonBandCol) * clamp((toonBt - toonBc) / max(1.0 - toonBc, 0.001), 0.0, 1.0);
  toonBandCol = mix(vec3(dot(toonBandCol, vec3(0.3, 0.59, 0.11))), toonBandCol, ${f(HAIR_BAND_SATURATION)});
  color.rgb = mix(color.rgb, toonBandCol * dot(diffuseBase, vec3(0.3, 0.59, 0.11)), toonBand);
}
#endif
#ifdef TOON_FACE
// [look2 Face] the iris and catchlights glow a little: part of their albedo added unlit.
color.rgb += baseColor.rgb * (toonFaceGlow * ${f(FACE_IRIS_GLOW)});
#endif
color.rgb = toonShoulder(color.rgb);
#if defined(TOON_GLOW) && defined(EMISSIVE)
float toonGlowW = smoothstep(${f(GLOW_MIX[0])}, ${f(GLOW_MIX[1])}, max(emissiveColor.r, max(emissiveColor.g, emissiveColor.b)));
color.rgb = mix(color.rgb, clamp(emissiveColor, 0.0, 1.0), toonGlowW);
// Animated fire (game/fire/): flame-coloured glow (lanterns, lamp globes, coals) flickers a
// little in hue, never in red, so its halo stays; the phase by where it hangs (toonMatF.x = the fire clock).
vec3 toonGlowP = floor(vPositionW * 0.5);
float toonGlowS = fract(sin(dot(toonGlowP, vec3(12.9898, 78.233, 37.719))) * 43758.5453) * 6.2831853;
float toonGlowF = 1.0 + ${f(GLOW_FLICKER)} * (0.55 * sin(7.3 * toonMatF.x + toonGlowS * 5.0) + 0.3 * sin(12.9 * toonMatF.x + toonGlowS * 3.0) + 0.15 * sin(21.7 * toonMatF.x + toonGlowS * 7.0));
float toonFlameW = toonGlowW * (1.0 - smoothstep(0.75, 0.85, emissiveColor.g)) * (1.0 - smoothstep(0.4, 0.5, emissiveColor.b));
color.rgb *= mix(vec3(1.0), vec3(1.0, toonGlowF, toonGlowF * toonGlowF), toonFlameW);
#endif
#endif
`,
    CUSTOM_FRAGMENT_BEFORE_FRAGCOLOR: `
#if defined(TOON) && defined(TOON_OUTLINE_MASK)
#ifdef TOON_REGIONS
#ifdef TOON_SHADE
// The vertex's outline weight (thin lines on hair and inner seams).
#ifdef TOON_FACE
if (visibility >= 0.999) color.a = toonPC.x * vToonShade.w * 0.5 * (1.0 - toonFaceFlat);
#else
if (visibility >= 0.999) color.a = toonPC.x * vToonShade.w * 0.5;
#endif
#else
if (visibility >= 0.999) color.a = toonPC.x * 0.5;
#endif
#else
if (visibility >= 0.999) color.a = 0.5 + toonPC.x * 0.5;
#endif
#endif
`,
  };
  for (let i = 0; i < TOON_MAX_LIGHTS; i++) {
    code[`CUSTOM_LIGHT${i}_COLOR`] = `
#ifdef TOON
#ifdef HEMILIGHT${i}
toonKind = 0.0;
#ifdef TOON_REGIONS
// Characters: hemispheric sky lift (up-facing brighter, down-facing darker).
toonAmb += mix(${L(i, "vLightGround")}, diffuse${i}.rgb, smoothstep(0.1, 0.9, dot(normalW, ${L(i, "vLightData")}.xyz) * 0.5 + 0.5)) * max(0.0, 1.0 + toonPD.z * normalW.y);
#else
{
  // The world: with skyLift (castle D1) the ground term takes the sky's hue at
  // the ground's level, so undersides get a darker sky fill, never the brown
  // bounce; and the fill grows facing up, shrinks facing down.
  vec3 toonGr = ${L(i, "vLightGround")};
  if (toonPD.z > 0.0) toonGr = diffuse${i}.rgb * (dot(toonGr, vec3(0.3, 0.59, 0.11)) / max(dot(diffuse${i}.rgb, vec3(0.3, 0.59, 0.11)), 1e-4));
  toonAmb += mix(toonGr, diffuse${i}.rgb, smoothstep(0.1, 0.9, dot(normalW, ${L(i, "vLightData")}.xyz) * 0.5 + 0.5)) * max(0.0, 1.0 + toonPD.z * normalW.y);
}
#endif
#else
toonKind = 1.0;
if (diffuse${i}.a < 0.0) {
  // The character shadow light (CHARACTER_SHADOW_RANGE): no light of its own.
  toonKind = 3.0;
  toonLc = vec3(0.0);
} else {
  vec4 toonLd = ${L(i, "vLightData")};
  float toonAtt = 1.0;
  if (toonLd.w == 0.0) {
    vec3 toonD = toonLd.xyz - vPositionW;
    toonAtt = max(0.0, 1.0 - length(toonD) / diffuse${i}.a);
    toonL = normalize(toonD);
    toonKind = 2.0;
  } else {
    toonL = normalize(-toonLd.xyz);
  }
  toonLc = diffuse${i}.rgb * toonAtt;
  toonNdl = dot(normalW, toonL);
}
#endif
#endif
`;
  }
  return code;
}

const VERTEX_REGIONS = {
  CUSTOM_VERTEX_DEFINITIONS: `
#ifdef TOON_DRAPE
attribute vec3 toonDrapeChair;
attribute vec3 toonDrapeFloor;
attribute vec3 toonDrapeRest;
#endif
#if defined(TOON) && defined(TOON_REGIONS)
flat varying float vToonRegion;
#ifdef TOON_SHADE
attribute vec4 toonShade;
varying vec4 vToonShade;
#endif
#ifdef TOON_FACE_SDF
varying vec2 vToonFaceL;
#endif
#endif
`,
  // The garments' sitting drape (charlib/drape.py): rest-pose offsets, weighted per mesh, before skinning.
  CUSTOM_VERTEX_UPDATE_POSITION: `
#ifdef TOON_DRAPE
positionUpdated += toonDrapeChair * toonDrape.x + toonDrapeFloor * toonDrape.y + toonDrapeRest * toonDrape.z;
#endif
`,
  CUSTOM_VERTEX_MAIN_END: `
#if defined(TOON) && defined(TOON_REGIONS)
#ifdef VERTEXCOLOR
int toonRegion = int(floor(floor(color.a * 255.0 + 0.5) / 16.0));
#else
int toonRegion = ${NEUTRAL_REGION};
#endif
toonRegion = toonRegion < ${REGION_COUNT} ? toonRegion : ${NEUTRAL_REGION};
vToonRegion = float(toonRegion);
#ifdef TOON_SHADE
vToonShade = toonShade;
#endif
#ifdef TOON_FACE_SDF
// The key light in the skinned head's frame (glTF rest frame: +Z facing, +X the side the map is baked for).
{
  mat3 toonM = mat3(finalWorld);
  vToonFaceL = vec2(dot(toonKeyDir.xyz, normalize(toonM * vec3(1.0, 0.0, 0.0))), dot(toonKeyDir.xyz, normalize(toonM * vec3(0.0, 0.0, 1.0))));
}
#endif
#endif
`,
};

/** The vertex attribute (4 normalised bytes) assembled characters carry their shade layer in. */
export const TOON_SHADE_KIND = "toonShade";

/**
 * The garments' sitting drape (assemble.ts `DRAPE_KEYS`, charlib/drape.py):
 * a vec3 rest-pose offset per vertex for the chair, the floor sit and the
 * floor rest, added before skinning with the weights in the mesh's
 * `metadata.toonDrape` (chair, floor, rest; animator.ts `drapeWeights`). Present only on characters
 * wearing a skirt or robe.
 */
export const TOON_DRAPE_KINDS = ["toonDrapeChair", "toonDrapeFloor", "toonDrapeRest"] as const;

/** The drape weights of a mesh (its `metadata.toonDrape`), zero when it has none. */
export function drapeOf(mesh: AbstractMesh | null | undefined): ArrayLike<number> {
  const d = (mesh?.metadata as { toonDrape?: ArrayLike<number> } | null | undefined)?.toonDrape;
  return d && d.length >= 3 ? d : NO_DRAPE;
}
const NO_DRAPE = new Float32Array(4);

/** Every plugin that writes the outline mask, so the switch can recompile them (toon plugins, castle/sky.ts). */
const LIVE = new Set<{ markAllDefinesAsDirty(): void }>();

/** A material plugin that reads `toonOutlineMask()` in its defines: recompiled when the mask switches. */
export function trackOutlineMask(p: { markAllDefinesAsDirty(): void }): void {
  LIVE.add(p);
}

export function untrackOutlineMask(p: { markAllDefinesAsDirty(): void }): void {
  LIVE.delete(p);
}
/** The scene whose post passes read the frame alpha, or null. */
let outlineMaskScene: Scene | null = null;

/**
 * Opaque toon materials of `scene` write their outline weight into the
 * frame's alpha (only while the outline pass reads it: on the canvas the
 * alpha must stay 1). Per scene: the creator's and the wardrobe's previews
 * (characters/preview.ts) draw on a see-through canvas with no post passes,
 * where a written alpha would show the character half transparent.
 * null: no scene.
 */
export function setToonOutlineMask(scene: Scene | null): void {
  if (scene === outlineMaskScene) return;
  outlineMaskScene = scene;
  for (const p of LIVE) p.markAllDefinesAsDirty();
}

/** Whether `scene`'s opaque toon materials write the outline weight into the frame alpha. */
export function toonOutlineMask(scene: Scene | null | undefined): boolean {
  return !!scene && scene === outlineMaskScene;
}

/**
 * The toon plugin of one material: `ramp` for a world material (one row),
 * `"regions"` for the character material (a row per vertex region).
 */
export class ToonPlugin extends MaterialPluginBase {
  private readonly regions: boolean;
  /** Emissive texels show as painted glow (castle kit M2). */
  readonly glow: boolean;
  private readonly a: Float32Array;
  private readonly b: Float32Array;
  private readonly c: Float32Array;
  private readonly d: Float32Array;
  private readonly e: Float32Array;
  /** World rows: the row reads the baked shade in `COLOR_0` (castle D1, `TOON_WORLD_SHADE`). */
  readonly worldShade: boolean;
  /** World rows: the row dithers out near the camera (`TOON_NEARFADE`). */
  readonly nearFade: boolean;

  /** `glow`: emissive texels show as painted (see the top of the file). */
  constructor(material: Material, rows: ToonRamp | "regions", opts: { glow?: boolean } = {}) {
    super(
      material,
      "Toon",
      150,
      { TOON: false, TOON_REGIONS: false, TOON_SHADE: false, TOON_DRAPE: false, TOON_FACE: false, TOON_FACE_SDF: false, TOON_WORLD_SHADE: false, TOON_NEARFADE: false, TOON_KEY_SHADOW: true, CUSTOMUSERLIGHTING: false, TOON_OUTLINE_MASK: false, TOON_GLOW: false },
      true,
      false,
    );
    this.regions = rows === "regions";
    // The character material sets the sitting drape's weights per mesh drawn
    // (`hardBindForSubMesh`), which Babylon only calls for a plugin that
    // registers for the extra events before it is enabled.
    this.registerForExtraEvents = this.regions;
    this._enable(true);
    this.glow = !!opts.glow;
    if (rows === "regions") {
      const t = packRegionTable();
      this.a = t.a;
      this.b = t.b;
      this.c = t.c;
      this.d = t.d;
      this.e = t.e;
      this.worldShade = false;
      this.nearFade = false;
    } else {
      this.a = new Float32Array(4);
      this.b = new Float32Array(4);
      this.c = new Float32Array(4);
      this.d = new Float32Array(4);
      this.e = new Float32Array(4);
      packRamp(rows, this.a, this.b, this.c);
      packWorldFields(rows, this.d, this.e);
      this.worldShade = worldShades(rows);
      this.nearFade = worldNearFades(rows);
    }
    LIVE.add(this);
  }

  override getClassName(): string {
    return "ToonPlugin";
  }

  override isCompatible(shaderLanguage: ShaderLanguage): boolean {
    return shaderLanguage === ShaderLanguage.GLSL;
  }

  /** Off: the material lights as a plain StandardMaterial (measurements and debugging). */
  get active(): boolean {
    return this.on;
  }

  set active(on: boolean) {
    if (on === this.on) return;
    this.on = on;
    this.markAllDefinesAsDirty();
  }

  private on = true;

  /** `setToonFaceShadow`, reachable from a page for live measurements (the perf probes' variant switch). */
  static readonly setFaceShadow = setToonFaceShadow;

  /** The body's face textures (character material only), or null. */
  private face: FaceTextures | null = null;
  private readonly faceParams = new Float32Array([FACE_SDF_SOFTNESS, 0, 0, 0]);

  /** Gives the character material its body's face textures (null: the atlas everywhere). */
  setFace(face: FaceTextures | null, softness = FACE_SDF_SOFTNESS): void {
    this.face = face;
    this.faceParams[0] = softness;
    this.markAllDefinesAsDirty();
  }

  get faceTextures(): FaceTextures | null {
    return this.face;
  }

  override prepareDefines(defines: MaterialDefines, _scene: Scene, mesh: AbstractMesh): void {
    defines["TOON"] = this.on;
    defines["CUSTOMUSERLIGHTING"] = this.on;
    defines["TOON_REGIONS"] = this.regions;
    defines["TOON_SHADE"] = this.regions && mesh.isVerticesDataPresent(TOON_SHADE_KIND);
    defines["TOON_DRAPE"] = this.regions && mesh.isVerticesDataPresent(TOON_DRAPE_KINDS[0]);
    const face = this.on && this.regions && !!this.face && defines["TOON_SHADE"] === true && !!(this._material as { diffuseTexture?: unknown }).diffuseTexture;
    defines["TOON_FACE"] = face;
    defines["TOON_FACE_SDF"] = face && faceShadow;
    // The world shade path needs the mesh's COLOR_0 (Babylon's VERTEXCOLOR); without it the row's x is 1.
    defines["TOON_WORLD_SHADE"] = this.on && !this.regions && this.worldShade && mesh.useVertexColors && mesh.isVerticesDataPresent(VertexBuffer.ColorKind);
    defines["TOON_NEARFADE"] = this.on && !this.regions && this.nearFade;
    defines["TOON_KEY_SHADOW"] = (mesh.metadata as { toonKeyShadow?: boolean } | null)?.toonKeyShadow !== false;
    defines["TOON_GLOW"] = this.on && this.glow;
    defines["TOON_OUTLINE_MASK"] = toonOutlineMask(this._material.getScene()) && !this._material.needAlphaBlendingForMesh(mesh);
  }

  override getUniforms(): { ubo: { name: string; size: number; type: string; arraySize?: number }[]; vertex?: string; fragment?: string } {
    if (this.regions) {
      const decl =
        ["A", "B", "C", "D", "E"].map((k) => `uniform vec4 toonRegion${k}[${REGION_COUNT}];`).join("\n") +
        `\nuniform vec4 toonEyeColours[${FACE_PALETTE_SIZE}];\nuniform vec4 toonHairColours[${FACE_PALETTE_SIZE}];\nuniform vec4 toonKeyDir;\nuniform vec4 toonFaceParams;\nuniform vec4 toonDrape;`;
      return {
        ubo: [
          { name: "toonRegionA", size: 4, type: "vec4", arraySize: REGION_COUNT },
          { name: "toonRegionB", size: 4, type: "vec4", arraySize: REGION_COUNT },
          { name: "toonRegionC", size: 4, type: "vec4", arraySize: REGION_COUNT },
          { name: "toonRegionD", size: 4, type: "vec4", arraySize: REGION_COUNT },
          { name: "toonRegionE", size: 4, type: "vec4", arraySize: REGION_COUNT },
          { name: "toonEyeColours", size: 4, type: "vec4", arraySize: FACE_PALETTE_SIZE },
          { name: "toonHairColours", size: 4, type: "vec4", arraySize: FACE_PALETTE_SIZE },
          { name: "toonKeyDir", size: 4, type: "vec4" },
          { name: "toonFaceParams", size: 4, type: "vec4" },
          { name: "toonDrape", size: 4, type: "vec4" },
        ],
        vertex: decl,
        fragment: decl,
      };
    }
    return {
      ubo: [
        { name: "toonMatA", size: 4, type: "vec4" },
        { name: "toonMatB", size: 4, type: "vec4" },
        { name: "toonMatC", size: 4, type: "vec4" },
        { name: "toonMatD", size: 4, type: "vec4" },
        { name: "toonMatE", size: 4, type: "vec4" },
        // Animated fire: x = the fire clock (flicker.ts fireTime), for the glow's flicker.
        { name: "toonMatF", size: 4, type: "vec4" },
      ],
      fragment: "uniform vec4 toonMatA;\nuniform vec4 toonMatB;\nuniform vec4 toonMatC;\nuniform vec4 toonMatD;\nuniform vec4 toonMatE;\nuniform vec4 toonMatF;",
    };
  }

  override bindForSubMesh(uniformBuffer: UniformBuffer, _scene: Scene, engine: AbstractEngine, subMesh: SubMesh): void {
    if (this.regions) {
      palettes ??= facePalettes();
      if (engine.supportsUniformBuffers) {
        uniformBuffer.updateFloatArray("toonRegionA", this.a);
        uniformBuffer.updateFloatArray("toonRegionB", this.b);
        uniformBuffer.updateFloatArray("toonRegionC", this.c);
        uniformBuffer.updateFloatArray("toonRegionD", this.d);
        uniformBuffer.updateFloatArray("toonRegionE", this.e);
        uniformBuffer.updateFloatArray("toonEyeColours", palettes.eye);
        uniformBuffer.updateFloatArray("toonHairColours", palettes.hair);
        uniformBuffer.updateFloat4("toonKeyDir", KEY_DIR[0]!, KEY_DIR[1]!, KEY_DIR[2]!, 0);
        uniformBuffer.updateFloat4("toonFaceParams", this.faceParams[0]!, 0, 0, 0);
        if (this.face) {
          uniformBuffer.setTexture("faceColourSampler", this.face.colour);
          uniformBuffer.setTexture("faceMaskSampler", this.face.mask);
        }
      } else {
        // Plain uniforms: vec4 arrays need the vec4 setter (the buffer's float-array path sets floats).
        const effect = subMesh.effect;
        effect?.setFloatArray4("toonRegionA", this.a);
        effect?.setFloatArray4("toonRegionB", this.b);
        effect?.setFloatArray4("toonRegionC", this.c);
        effect?.setFloatArray4("toonRegionD", this.d);
        effect?.setFloatArray4("toonRegionE", this.e);
        effect?.setFloatArray4("toonEyeColours", palettes.eye);
        effect?.setFloatArray4("toonHairColours", palettes.hair);
        effect?.setFloat4("toonKeyDir", KEY_DIR[0]!, KEY_DIR[1]!, KEY_DIR[2]!, 0);
        effect?.setFloat4("toonFaceParams", this.faceParams[0]!, 0, 0, 0);
        if (this.face) {
          effect?.setTexture("faceColourSampler", this.face.colour);
          effect?.setTexture("faceMaskSampler", this.face.mask);
        }
      }
    } else {
      uniformBuffer.updateFloat4("toonMatA", this.a[0]!, this.a[1]!, this.a[2]!, this.a[3]!);
      uniformBuffer.updateFloat4("toonMatB", this.b[0]!, this.b[1]!, this.b[2]!, this.b[3]!);
      uniformBuffer.updateFloat4("toonMatC", this.c[0]!, this.c[1]!, this.c[2]!, this.c[3]!);
      uniformBuffer.updateFloat4("toonMatD", this.d[0]!, this.d[1]!, this.d[2]!, this.d[3]!);
      uniformBuffer.updateFloat4("toonMatE", this.e[0]!, this.e[1]!, this.e[2]!, this.e[3]!);
      uniformBuffer.updateFloat4("toonMatF", GLOW_CLOCK.t, 0, 0, 0);
    }
  }

  /** Per mesh, every draw (the material is shared): the drape weights of the mesh drawn. */
  override hardBindForSubMesh(uniformBuffer: UniformBuffer, _scene: Scene, engine: AbstractEngine, subMesh: SubMesh): void {
    if (!this.regions) return;
    const d = drapeOf(subMesh.getMesh());
    if (engine.supportsUniformBuffers) uniformBuffer.updateFloat4("toonDrape", d[0]!, d[1]!, d[2]!, 0);
    else subMesh.effect?.setFloat4("toonDrape", d[0]!, d[1]!, d[2]!, 0);
  }

  override getSamplers(samplers: string[]): void {
    if (this.regions) samplers.push("faceColourSampler", "faceMaskSampler");
  }

  override hasTexture(texture: BaseTexture): boolean {
    return !!this.face && (this.face.colour === texture || this.face.mask === texture);
  }

  override getActiveTextures(active: BaseTexture[]): void {
    if (this.face) active.push(this.face.colour, this.face.mask);
  }

  override isReadyForSubMesh(defines: MaterialDefines): boolean {
    if (!defines["TOON_FACE"] || !this.face) return true;
    return this.face.colour.isReady() && this.face.mask.isReady();
  }

  /** The shade layer's vertex attribute, for meshes that carry it. */
  override getAttributes(attributes: string[], _scene: Scene, mesh: AbstractMesh): void {
    if (this.regions && mesh.isVerticesDataPresent(TOON_SHADE_KIND)) attributes.push(TOON_SHADE_KIND);
    if (this.regions && mesh.isVerticesDataPresent(TOON_DRAPE_KINDS[0])) attributes.push(...TOON_DRAPE_KINDS);
  }

  override getCustomCode(shaderType: string): { [pointName: string]: string } | null {
    if (shaderType === "fragment") return fragmentCode(this._material.getScene().getEngine().supportsUniformBuffers);
    if (shaderType === "vertex") return VERTEX_REGIONS;
    return null;
  }

  override dispose(forceDisposeTextures?: boolean): void {
    LIVE.delete(this);
    super.dispose(forceDisposeTextures);
  }
}

/** For tests: the fragment injection points the plugin uses. */
export function toonFragmentPoints(): string[] {
  return Object.keys(fragmentCode(true));
}
