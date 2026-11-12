// Merges a character's parts (body, hair, up to four gear items) into one
// skinned mesh: one vertex buffer, one index buffer, one material, so each
// character costs a single draw call however it is dressed.
//
// On the way it bakes everything that is fixed for an appearance version:
// shape keys (face sliders) into the positions, the skin tone, hair and eye
// colour and dyes into vertex colours (dye.ts), and body regions hidden by
// gear out of the mesh entirely. The parts' shade layer (`_SHADE`, parts.ts)
// goes into a fourth byte buffer for the toon shader (`shade`), with faces
// forced open and the fringe's soft shadow folded in (`fringeShadow`). Bone indices are remapped by name onto the
// rig, so parts may list their joints in any order. Atlas coordinates are
// kept; primitives without a texture point at a white texel of the atlas,
// so textured and untextured parts share the one material. Pure, unit tested.

import type { Finish, GearSlot } from "../../wasm/sim_wasm";
import { PALETTE, type Dyes, type Palette } from "../../content/catalog";
import type { Rig } from "./clips";
import { bodyChannels, channelWeight, dyeColours, finishAlpha, hairChannels, packColour, tintVertex, type Channels, type Rgb } from "./dye";
import { COVER_STEP_M, type FringeData, type PartData, type PartPrimitive } from "./parts";
import { BASE_RAMP, CHARACTER_TOON, regionIndex } from "../toon/palette";

export type PartRole = "base" | "hair" | GearSlot;

export interface PartInput {
  role: PartRole;
  part: PartData;
  /** Gear only: palette indices of the primary, secondary and trim dyes. */
  dyes?: Dyes;
  /** Gear only: its brewed finish, written into the vertex colours' alpha (finish.ts). */
  finish?: Finish;
}

export interface AssembleInput {
  rig: Rig;
  parts: readonly PartInput[];
  /** 0 = full detail. Parts without this LOD use their next more detailed one. */
  lod: number;
  skin: Rgb;
  hair: Rgb;
  /** The iris tint (body mask channel B); white when not given. */
  eye?: Rgb;
  /**
   * Creator palette indices of the eye and hair colour (docs/art/character-look.md
   * section 3.1): written into a face-textured head's `shade` y and z (index / 255),
   * where the face shader looks the colours up. 0 when not given.
   */
  eyeIndex?: number;
  hairIndex?: number;
  /** Shape-key weights by key name (content/appearance.ts `shapeKeyWeights`). */
  shapeWeights: ReadonlyMap<string, number>;
  /**
   * Where untextured primitives sample the atlas (a white texel), or null
   * when the character has no atlas: then no texture coordinates are output.
   */
  whiteUv?: [number, number] | null;
  palette?: Palette;
  /**
   * Dye channels to keep separable (a Chameleon Tonic recolours them live):
   * the output then also carries `tint`, so the colour of every vertex is
   * `rest + coef · C` for any new channel colour C.
   */
  track?: readonly TrackedChannel[];
}

/** One part role's dye channel (0 primary / skin / hair, 1 secondary / brows, 2 trim). */
export interface TrackedChannel {
  role: PartRole;
  channel: 0 | 1 | 2;
}

/** A tracked colour, split per vertex (3 floats each, sRGB 0..1). */
export interface TintSplit {
  /** The colour with the tracked channels' colours set to black. */
  rest: Float32Array;
  /** What one unit of the tracked colour adds (base colour × mask weight). */
  coef: Float32Array;
}

export interface MergedGeometry {
  vertexCount: number;
  positions: Float32Array;
  normals: Float32Array;
  /** Atlas coordinates, 2 per vertex, or null without an atlas. */
  uvs: Float32Array | null;
  /** RGBA bytes. */
  colors: Uint8Array;
  /**
   * The shade layer, 4 normalised bytes per vertex (toon/material.ts
   * `toonShade`): crease shade (1 open), root-to-tip, band phase (0.5
   * neutral), outline weight (1 full). Parts without `_SHADE` read
   * (1, 0, 0.5, 1): exactly the look before the shade layer.
   */
  shade: Uint8Array;
  /**
   * Outlines (docs/art/character-look.md section 4): the inverted hull's
   * layer, 4 normalised signed bytes per vertex (toon/outline.ts
   * `toonOutline`): the smoothed normal (xyz) and the width weight (w,
   * 0..1). Parts without `_OUTLINE` read all zero: no line.
   */
  outline: Int8Array;
  /** 4 rig bone indices per vertex. */
  joints: Uint8Array | Uint16Array;
  /** 4 weights per vertex as bytes summing to 255. */
  weights: Uint8Array;
  indices: Uint16Array | Uint32Array;
  min: [number, number, number];
  max: [number, number, number];
  /** Bone names the parts used that the rig lacks (bound to its first bone). */
  unknownBones: string[];
  /** Body regions left out because gear hides them. */
  hidden: string[];
  /** Garment triangles left out because a higher layer covers them (`coveredTriangles`). */
  coveredTriangles: number;
  /**
   * The sitting drape (charlib/drape.py): per `DRAPE_KEYS` key, 3 floats per
   * vertex (the rest-pose offset the seated clips turn on, toon material
   * `toonDrape`), or null when no part carries that key.
   */
  drape: Record<DrapeKey, Float32Array | null>;
  /** Present when the input asked to `track` channels. */
  tint?: TintSplit;
}

/**
 * Packs `rest + coef · colour` into the RGB bytes of `out`: a tracked channel
 * recoloured. Alpha (the finish, finish.ts) is left as assembled.
 */
export function recolour(tint: TintSplit, colour: readonly [number, number, number], out: Uint8Array): void {
  const { rest, coef } = tint;
  const n = rest.length / 3;
  const [r, g, b] = colour;
  for (let v = 0; v < n; v++) {
    const s = v * 3;
    const o = v * 4;
    out[o] = Math.round(Math.max(0, Math.min(1, rest[s]! + coef[s]! * r)) * 255);
    out[o + 1] = Math.round(Math.max(0, Math.min(1, rest[s + 1]! + coef[s + 1]! * g)) * 255);
    out[o + 2] = Math.round(Math.max(0, Math.min(1, rest[s + 2]! + coef[s + 2]! * b)) * 255);
  }
}

/** Regions the worn gear hides; long robes always hide the legs under them. */
export function hiddenRegions(parts: readonly PartInput[]): Set<string> {
  const out = new Set<string>();
  for (const p of parts) {
    if (p.role === "base" || p.role === "hair") continue;
    for (const r of p.part.hides) out.add(r);
    if (p.part.long) {
      out.add("thighs");
      out.add("calves");
    }
  }
  return out;
}

/**
 * Hidden covered geometry (the clothing pass, docs/architecture.md;
 * charlib/layers.py): gear is
 * layered shoes < legs < body. A garment triangle is left out when each of
 * its vertices lies over a body vertex that a garment of a higher layer
 * covers in every pose, no further out than `COVER_SCALE` times that cover
 * plus `COVER_TOLERANCE_M` (a skirt's waistband under a shirt, trousers
 * under a robe: never seen, but they broke through as the hips twisted).
 */
/**
 * The garments' sitting drape keys (glTF morph target names, charlib/drape.py):
 * on with the chair sit, the cross-legged floor sit and the floor rest
 * (sitting where no seat is, knees up).
 */
export const DRAPE_KEYS = ["drape_chair", "drape_floor", "drape_rest"] as const;
export type DrapeKey = (typeof DRAPE_KEYS)[number];
const IS_DRAPE_KEY: ReadonlySet<string> = new Set(DRAPE_KEYS);

export const LAYER_OF_ROLE: Partial<Record<PartRole, number>> = { shoes: 1, legs: 2, body: 3 };
export const COVER_SCALE = 1.25;
export const COVER_TOLERANCE_M = 0.002;

/** The largest cover (metres per body vertex) of the parts on a layer above `role`'s, or null when none covers. */
export function coverAbove(parts: readonly PartInput[], role: PartRole): Float32Array | null {
  const layer = LAYER_OF_ROLE[role];
  if (layer === undefined) return null;
  let out: Float32Array | null = null;
  for (const p of parts) {
    const c = p.part.covers;
    if (!c || (LAYER_OF_ROLE[p.role] ?? 0) <= layer) continue;
    if (!out || out.length < c.length) {
      const grown = new Float32Array(c.length);
      if (out) grown.set(out);
      out = grown;
    }
    for (let i = 0; i < c.length; i++) out[i] = Math.max(out[i]!, c[i]! * COVER_STEP_M);
  }
  return out;
}

/** The primitive's indices without the triangles `cover` hides (the same array when none is). */
export function uncoveredIndices(prim: Pick<PartPrimitive, "indices" | "bodyVertex" | "bodyHeight">, cover: Float32Array | null): Uint32Array {
  const bv = prim.bodyVertex;
  const bh = prim.bodyHeight;
  if (!cover || !bv || !bh) return prim.indices;
  const n = bv.length;
  const under = new Uint8Array(n);
  let any = false;
  for (let v = 0; v < n; v++) {
    const c = cover[bv[v]!] ?? 0;
    if (c > 0 && bh[v]! <= c * COVER_SCALE + COVER_TOLERANCE_M) (under[v] = 1), (any = true);
  }
  if (!any) return prim.indices;
  const idx = prim.indices;
  const out = new Uint32Array(idx.length);
  let k = 0;
  for (let t = 0; t + 2 < idx.length; t += 3) {
    const a = idx[t]!;
    const b = idx[t + 1]!;
    const c = idx[t + 2]!;
    if (under[a] && under[b] && under[c]) continue;
    out[k++] = a;
    out[k++] = b;
    out[k++] = c;
  }
  return k === idx.length ? idx : out.slice(0, k);
}

/** The LOD level of `part` used for `lod`: the most detailed one not finer than asked. */
export function lodFor(part: PartData, lod: number): number {
  let best = -1;
  for (const p of part.primitives) if (p.lod <= lod && p.lod > best) best = p.lod;
  return best < 0 ? Math.min(...part.primitives.map((p) => p.lod)) : best;
}

/** Whether any part has its own geometry for `lod` (otherwise that LOD would repeat a finer one). */
export function hasLod(parts: readonly PartInput[], lod: number): boolean {
  return parts.some((p) => p.part.primitives.some((q) => q.lod === lod));
}

function selected(input: AssembleInput, hidden: Set<string>): { part: PartInput; prim: PartPrimitive; indices: Uint32Array }[] {
  const out: { part: PartInput; prim: PartPrimitive; indices: Uint32Array }[] = [];
  for (const part of input.parts) {
    const level = lodFor(part.part, input.lod);
    const cover = coverAbove(input.parts, part.role);
    for (const prim of part.part.primitives) {
      if (prim.lod !== level) continue;
      if (part.role === "base" && prim.region && hidden.has(prim.region)) continue;
      out.push({ part, prim, indices: uncoveredIndices(prim, cover) });
    }
  }
  return out;
}

const R_SKIN = regionIndex("skin");
const R_FACE = regionIndex("face");
const R_HAIR = regionIndex("hair");
const R_FLAT = regionIndex("flat");
const R_METAL = regionIndex("metal");
const R_NEUTRAL = regionIndex("neutral");
/** Gear regions by dominant mask channel: none, primary, secondary, trim. */
const GEAR_REGIONS = [R_NEUTRAL, regionIndex("primary"), regionIndex("secondary"), regionIndex("trim")] as const;

/**
 * The toon region of one vertex (toon/palette.ts): an `extras.toon` on the
 * primitive wins; painted eyes and mouths are `flat`; then the mask's
 * strongest channel decides: skin or brows (hair) on the body, where the
 * skin of the head primitive is `face` and the tinted iris (channel B) is
 * `flat` like the rest of the eye, hair on hair, the dye channel on gear,
 * where undyed areas are `neutral` or, marked in the file, `metal`.
 */
export function vertexRegion(role: PartRole, prim: Pick<PartPrimitive, "mask" | "metal" | "toon"> & { region?: PartPrimitive["region"]; faceTexture?: boolean }, v: number, untinted: boolean): number {
  if (prim.toon) return regionIndex(prim.toon);
  // A face-textured head is all `face`: the texture's mask tells eyes and brows apart.
  if (role === "base" && prim.faceTexture) return R_FACE;
  if (untinted) return R_FLAT;
  const m = prim.mask;
  const s = v * 3;
  const r = m ? Math.max(0, m[s]!) : 1;
  const g = m ? Math.max(0, m[s + 1]!) : 0;
  const b = m ? Math.max(0, m[s + 2]!) : 0;
  const rest = Math.max(0, 1 - r - g - b);
  if (role === "base") {
    if (g > r && g >= b) return R_HAIR;
    if (b > r && b > g) return R_FLAT;
    if (rest > r && rest > g) return R_FLAT;
    return prim.region === "head" ? R_FACE : R_SKIN;
  }
  if (role === "hair") return rest > r ? R_NEUTRAL : R_HAIR;
  let best = 0;
  let w = rest;
  if (r > w) (best = 1), (w = r);
  if (g > w) (best = 2), (w = g);
  if (b > w) best = 3;
  if (best === 0 && prim.metal?.[v]) return R_METAL;
  return GEAR_REGIONS[best as 0 | 1 | 2 | 3];
}

/** Body materials that keep their own colour when a file has no tint mask. */
const UNTINTED = /eye|teeth|tongue|mouth/i;
const NO_TINT = new Float32Array(3);

function channelsFor(part: PartInput, input: AssembleInput, palette: Palette): Channels {
  switch (part.role) {
    case "base":
      return bodyChannels(input.skin, input.hair, input.eye);
    case "hair":
      return hairChannels(input.hair);
    default:
      return dyeColours(part.dyes ?? [0, 0, 0], palette);
  }
}

/**
 * The fringe's shadow on one forehead vertex (docs/art/character-adoption.md
 * sections 4.4 and 5): 1 where none falls, down to `1 - strength` just under the
 * hem. `d` is the vertex's drop below the hem's smoothed shadow line
 * (`FringeData.line`) at its x (negative above: the skin under the hair is
 * shadowed too); the shadow fades out from `depth - soft` to `depth` below
 * it. Outside the hem's x span, 1.
 */
export function fringeShadow(f: FringeData, x: number, y: number): number {
  const h = f.line;
  const n = h.length / 3;
  if (n < 2 || x < h[0]! || x > h[(n - 1) * 3]!) return 1;
  let k = 0;
  while (k < n - 2 && x > h[(k + 1) * 3]!) k++;
  const x0 = h[k * 3]!;
  const x1 = h[(k + 1) * 3]!;
  const t = x1 > x0 ? (x - x0) / (x1 - x0) : 0;
  const hemY = h[k * 3 + 1]! + (h[(k + 1) * 3 + 1]! - h[k * 3 + 1]!) * t;
  const d = hemY - y;
  return 1 - f.strength * (1 - smoothstep(f.depth - f.soft, f.depth, d));
}

function smoothstep(a: number, b: number, v: number): number {
  const t = Math.min(1, Math.max(0, (v - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

/**
 * The hair's highlight band (angel ring, toon/material.ts): the shader lights
 * a band where `_SHADE.y` is near the hair row's `bandCentre`. The file's
 * strand coordinate puts that crossing at a different height on every lock,
 * so close up the band broke into bars. At assembly the hair's y is
 * replaced by its elevation round the head's centre (the head primitive's
 * bounds): `y = bandCentre + (RING_ELEVATION - elevation) * RING_SCALE`, one
 * continuous ring following the head's curve on every lock (the per-lock
 * phase in z still breaks it slightly). Elevation in radians.
 */
export const RING_ELEVATION = 0.8;
export const RING_SCALE = 1;

/** The ring coordinate of a hair vertex at (x, y, z) round the head centre `c` (see `RING_ELEVATION`). */
export function ringY(x: number, y: number, z: number, c: readonly [number, number, number], bandCentre: number): number {
  const dx = x - c[0];
  const dy = y - c[1];
  const dz = z - c[2];
  const e = Math.atan2(dy, Math.sqrt(dx * dx + dz * dz));
  return bandCentre + (RING_ELEVATION - e) * RING_SCALE;
}

/** The centre of the base body's head primitive (rest frame), or null without one. */
function headCentre(prims: readonly { part: PartInput; prim: PartPrimitive }[]): [number, number, number] | null {
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (const { part, prim } of prims) {
    if (part.role !== "base" || prim.region !== "head") continue;
    const p = prim.positions;
    for (let s = 0; s < p.length; s += 3) {
      for (let k = 0; k < 3; k++) {
        const v = p[s + k]!;
        if (v < lo[k]!) lo[k] = v;
        if (v > hi[k]!) hi[k] = v;
      }
    }
  }
  if (!(lo[0]! <= hi[0]!)) return null;
  return [(lo[0]! + hi[0]!) / 2, (lo[1]! + hi[1]!) / 2, (lo[2]! + hi[2]!) / 2];
}

/** Head-skin vertices facing forward at least this much (normal z) take the fringe's shadow. */
const FRINGE_FACING = 0.2;

const toByte = (v: number): number => Math.round(Math.max(0, Math.min(1, v)) * 255);

/** A palette index as the face shader reads it from a shade byte (`index / 255`). */
export function paletteByte(index: number | undefined): number {
  return Math.max(0, Math.min(255, Math.round(index ?? 0)));
}

/**
 * Outlines: one vertex of the outline layer (`src[s..s+2]`: smoothed
 * normal x width weight) as 4 signed bytes at `out[o]`: the unit normal, then
 * the weight (clamped to 0..1). A zero vector packs to all zero (no line).
 */
export function packOutline(src: ArrayLike<number>, s: number, out: Int8Array, o: number): void {
  const x = src[s]!;
  const y = src[s + 1]!;
  const z = src[s + 2]!;
  const len = Math.sqrt(x * x + y * y + z * z);
  if (!(len > 1e-6)) {
    out[o] = out[o + 1] = out[o + 2] = out[o + 3] = 0;
    return;
  }
  out[o] = Math.round((x / len) * 127);
  out[o + 1] = Math.round((y / len) * 127);
  out[o + 2] = Math.round((z / len) * 127);
  out[o + 3] = Math.round(Math.min(1, len) * 127);
}

export function assemble(input: AssembleInput): MergedGeometry {
  const palette = input.palette ?? PALETTE;
  const hidden = hiddenRegions(input.parts);
  const prims = selected(input, hidden);
  const whiteUv = input.whiteUv ?? null;
  // The hair's fringe shadow falls on the face (recomputed per look and LOD, never per frame).
  const fringe = input.parts.find((p) => p.role === "hair")?.part.fringe ?? null;
  // The hair's band runs round the head (`ringY`).
  const head = input.parts.some((p) => p.role === "hair") ? headCentre(prims) : null;
  const bandCentre = CHARACTER_TOON.hair.bandCentre ?? BASE_RAMP.bandCentre;

  let vertexCount = 0;
  let indexCount = 0;
  let covered = 0;
  for (const { prim, indices } of prims) {
    vertexCount += prim.positions.length / 3;
    indexCount += indices.length;
    covered += (prim.indices.length - indices.length) / 3;
  }

  const positions = new Float32Array(vertexCount * 3);
  const normals = new Float32Array(vertexCount * 3);
  const uvs = whiteUv ? new Float32Array(vertexCount * 2) : null;
  const colors = new Uint8Array(vertexCount * 4);
  const shade = new Uint8Array(vertexCount * 4);
  const outline = new Int8Array(vertexCount * 4);
  const joints = input.rig.count <= 256 ? new Uint8Array(vertexCount * 4) : new Uint16Array(vertexCount * 4);
  const weights = new Uint8Array(vertexCount * 4);
  const indices = vertexCount <= 65536 ? new Uint16Array(indexCount) : new Uint32Array(indexCount);
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  const unknown = new Set<string>();
  const rgb = [0, 0, 0];
  const track = input.track?.length ? input.track : null;
  const tint: TintSplit | null = track ? { rest: new Float32Array(vertexCount * 3), coef: new Float32Array(vertexCount * 3) } : null;
  const drape = {} as Record<DrapeKey, Float32Array | null>;
  for (const key of DRAPE_KEYS) drape[key] = prims.some(({ part, prim }) => part.role !== "base" && part.role !== "hair" && prim.morphs.has(key)) ? new Float32Array(vertexCount * 3) : null;

  let v0 = 0;
  let i0 = 0;
  const eyeByte = paletteByte(input.eyeIndex);
  const hairByte = paletteByte(input.hairIndex);
  for (const { part, prim, indices: primIndices } of prims) {
    const n = prim.positions.length / 3;
    // The face-textured head: its shade y and z carry the eye and hair colour indices.
    const faceTex = part.role === "base" && !!prim.faceTexture && !!prim.uvs;
    // Shape keys this primitive has.
    const morphs: { w: number; pos: Float32Array; nrm: Float32Array | null }[] = [];
    for (const [key, data] of prim.morphs) {
      if (IS_DRAPE_KEY.has(key)) continue; // posed live by the material, never baked
      const w = input.shapeWeights.get(key) ?? 0;
      if (w > 0) morphs.push({ w, pos: data.positions, nrm: data.normals });
    }
    // Bone remap for this part.
    const remap = part.part.bones.map((name) => {
      const i = input.rig.boneIndex(name);
      if (i < 0) unknown.add(name);
      return Math.max(0, i);
    });
    const channels = channelsFor(part, input, palette);
    const untinted = !prim.mask && part.role === "base" && UNTINTED.test(prim.material);
    // The tracked channel of this part, if any (one per role is enough: hair
    // tracks the hair colour, the body its brows, a garment one dye).
    const tracked = tint && !untinted ? (track!.find((t) => t.role === part.role)?.channel ?? -1) : -1;
    // Body and hair never carry a finish; gear carries its garment's.
    const finish = part.role === "base" || part.role === "hair" ? undefined : part.finish;

    for (let v = 0; v < n; v++) {
      const s = v * 3;
      const d = (v0 + v) * 3;
      let x = prim.positions[s]!;
      let y = prim.positions[s + 1]!;
      let z = prim.positions[s + 2]!;
      let nx = prim.normals ? prim.normals[s]! : 0;
      let ny = prim.normals ? prim.normals[s + 1]! : 1;
      let nz = prim.normals ? prim.normals[s + 2]! : 0;
      for (const m of morphs) {
        x += m.pos[s]! * m.w;
        y += m.pos[s + 1]! * m.w;
        z += m.pos[s + 2]! * m.w;
        if (m.nrm) {
          nx += m.nrm[s]! * m.w;
          ny += m.nrm[s + 1]! * m.w;
          nz += m.nrm[s + 2]! * m.w;
        }
      }
      const len = Math.hypot(nx, ny, nz) || 1;
      positions[d] = x;
      positions[d + 1] = y;
      positions[d + 2] = z;
      normals[d] = nx / len;
      normals[d + 1] = ny / len;
      normals[d + 2] = nz / len;
      if (x < min[0]) min[0] = x;
      if (y < min[1]) min[1] = y;
      if (z < min[2]) min[2] = z;
      if (x > max[0]) max[0] = x;
      if (y > max[1]) max[1] = y;
      if (z > max[2]) max[2] = z;

      if (uvs) {
        const u = (v0 + v) * 2;
        uvs[u] = prim.uvs ? prim.uvs[v * 2]! : whiteUv![0];
        uvs[u + 1] = prim.uvs ? prim.uvs[v * 2 + 1]! : whiteUv![1];
      }
      tintVertex(prim.base, untinted ? NO_TINT : prim.mask, untinted ? 0 : s, channels, rgb, 0);
      packColour(rgb, 0, colors, (v0 + v) * 4);
      const region = vertexRegion(part.role, prim, v, untinted);
      colors[(v0 + v) * 4 + 3] = finishAlpha(finish, region);
      {
        const o = (v0 + v) * 4;
        const sh = prim.shade;
        // Faces are kept clean: no baked occlusion on them (eye sockets, nose, mouth).
        let sx = region === R_FACE ? 1 : sh ? sh[v * 4]! : 1;
        if (fringe && region === R_FACE && normals[d + 2]! > FRINGE_FACING) sx *= fringeShadow(fringe, x, y);
        shade[o] = toByte(sx);
        if (faceTex) {
          shade[o + 1] = eyeByte;
          shade[o + 2] = hairByte;
        } else {
          shade[o + 1] = toByte(head && region === R_HAIR && part.role === "hair" ? ringY(x, y, z, head, bandCentre) : sh ? sh[v * 4 + 1]! : 0);
          shade[o + 2] = toByte(sh ? sh[v * 4 + 2]! : 0.5);
        }
        shade[o + 3] = toByte(sh ? sh[v * 4 + 3]! : 1);
      }
      // --- Outlines: the outline layer ---
      if (prim.outline) packOutline(prim.outline, s, outline, (v0 + v) * 4);
      // --- end Outlines ---
      if (tint) {
        const t = (v0 + v) * 3;
        const w = tracked >= 0 ? channelWeight(prim.mask, s, tracked) : 0;
        const c = tracked >= 0 ? channels[tracked as 0 | 1 | 2] : NO_TINT;
        for (let k = 0; k < 3; k++) {
          const coef = prim.base[k]! * w;
          tint.coef[t + k] = coef;
          tint.rest[t + k] = rgb[k]! - coef * c[k]!;
        }
      }

      // Skinning: remap and quantise to bytes summing to exactly 255.
      const j = v * 4;
      const o = (v0 + v) * 4;
      let sum = 0;
      let top = 0;
      for (let k = 0; k < 4; k++) {
        joints[o + k] = remap[prim.joints[j + k]!] ?? 0;
        const q = Math.round(prim.weights[j + k]! * 255);
        weights[o + k] = q;
        sum += q;
        if (q > weights[o + top]!) top = k;
      }
      weights[o + top] = Math.max(0, weights[o + top]! + 255 - sum);
    }
    for (const key of DRAPE_KEYS) {
      const out = drape[key];
      const m = part.role !== "base" && part.role !== "hair" ? prim.morphs.get(key) : undefined;
      if (out && m && m.positions.length === n * 3) out.set(m.positions, v0 * 3);
    }
    for (let k = 0; k < primIndices.length; k++) indices[i0 + k] = primIndices[k]! + v0;
    v0 += n;
    i0 += primIndices.length;
  }

  if (vertexCount === 0) {
    min.fill(0);
    max.fill(0);
  }
  const out: MergedGeometry = { vertexCount, positions, normals, uvs, colors, shade, outline, joints, weights, indices, min, max, unknownBones: [...unknown], hidden: [...hidden], coveredTriangles: covered, drape };
  if (tint) out.tint = tint;
  return out;
}
