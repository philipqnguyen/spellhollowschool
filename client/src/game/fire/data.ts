// Animated fire (docs/architecture.md "Rendering"): the manifests' `fire`
// entries (tools/blender/castle_kit/fire.py) and the per-instance buffers the
// fire meshes draw from. Pure: no Babylon, so vitest covers it.
//
// Each flame is one camera-facing card; its matrix places the card's foot
// at the flame's foot and scales a unit card to the flame's card size
// (`CARD_W` x `CARD_H` times the tongue's width and height: room for the
// sway and the pieces breaking off the tip). Its `flame` attribute is
// [shape seed, flicker seed, brightness, kind] (kind 0 a fire's tongue, 1 a
// candle's flame).

import toonPaletteJson from "../../../../data/art/toon_palette.json";

/** A card's width and height over its tongue's width and height (fire.ts shaders: the same numbers). */
export const CARD_W = 1.9;
export const CARD_H = 1.45;
/** A candle's flame width over its height. */
export const CANDLE_ASPECT = 0.42;
/** Embers rise this far over their fire's tallest tongue (x its height) and spread this far (x its width). */
const EMBER_RISE = 1.6;
const EMBER_SPREAD = 0.35;

export interface FireTongue {
  /** Offset of the tongue's foot from the fire's anchor (m). */
  d: [number, number, number];
  h: number;
  w: number;
}

export interface FireSpec {
  at: [number, number, number];
  /** Flicker phase (0..1), shared with the fire's light (`flicker` on a manifest light). */
  seed: number;
  tongues: FireTongue[];
  /** Floor glow radius (m), 0 for none. */
  pool: number;
  /** The height the floor glow lies at (m; the anchor's when the manifest gives none). */
  floor: number;
  embers: number;
}

/** A manifest `fire` entry: fires, still candles, and candles riding a moving node (by node name). */
export interface FireData {
  fires: FireSpec[];
  /** [x, y, z, height]: the flame's foot. */
  candles: [number, number, number, number][];
  groups: Map<string, [number, number, number, number][]>;
}

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const vec3 = (v: unknown): v is [number, number, number] => Array.isArray(v) && v.length === 3 && v.every(num);
const candleRow = (v: unknown): v is [number, number, number, number] => Array.isArray(v) && v.length === 4 && v.every(num) && (v[3] as number) > 0;

/** A manifest `fire` entry, or null when there is none or nothing in it is usable. Bad rows are skipped. */
export function parseFire(raw: unknown): FireData | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { fires?: unknown; candles?: unknown; groups?: unknown };
  const fires: FireSpec[] = [];
  for (const f of Array.isArray(r.fires) ? r.fires : []) {
    const o = f as { at?: unknown; seed?: unknown; tongues?: unknown; pool?: unknown; floor?: unknown; embers?: unknown };
    if (!vec3(o?.at) || !Array.isArray(o.tongues)) continue;
    const tongues: FireTongue[] = [];
    for (const t of o.tongues) {
      if (!Array.isArray(t) || t.length !== 5 || !t.every(num) || t[3] <= 0 || t[4] <= 0) continue;
      tongues.push({ d: [t[0], t[1], t[2]], h: t[3], w: t[4] });
    }
    if (tongues.length === 0) continue;
    fires.push({
      at: [o.at[0], o.at[1], o.at[2]],
      seed: num(o.seed) ? o.seed : 0,
      tongues,
      pool: num(o.pool) && o.pool > 0 ? o.pool : 0,
      floor: num(o.floor) ? o.floor : o.at[1],
      embers: num(o.embers) && o.embers > 0 ? Math.min(32, Math.floor(o.embers)) : 0,
    });
  }
  const candles = (Array.isArray(r.candles) ? r.candles : []).filter(candleRow);
  const groups = new Map<string, [number, number, number, number][]>();
  if (r.groups && typeof r.groups === "object") {
    for (const [name, rows] of Object.entries(r.groups as Record<string, unknown>)) {
      const ok = (Array.isArray(rows) ? rows : []).filter(candleRow);
      if (ok.length) groups.set(name, ok);
    }
  }
  if (!fires.length && !candles.length && !groups.size) return null;
  return { fires, candles, groups };
}

/** Joins several entries (an instance room's own and its cells'). */
export function mergeFire(list: readonly (FireData | null)[]): FireData | null {
  const out: FireData = { fires: [], candles: [], groups: new Map() };
  for (const d of list) {
    if (!d) continue;
    out.fires.push(...d.fires);
    out.candles.push(...d.candles);
    for (const [k, v] of d.groups) out.groups.set(k, [...(out.groups.get(k) ?? []), ...v]);
  }
  return out.fires.length || out.candles.length || out.groups.size ? out : null;
}

/**
 * A model's fire (in its own frame) at each of its placements: turned by
 * `yaw` about +y (x' = x cos + z sin, z' = -x sin + z cos, the furniture's
 * matrices) and moved to `pos`, `lift` higher. Each placed fire gets a seed
 * of its own (from where it stands), so two lanterns never flicker together.
 */
export function placeFire(model: FireData, places: readonly { pos: readonly [number, number, number]; yaw: number; lift?: number }[]): FireData | null {
  const out: FireData = { fires: [], candles: [], groups: new Map() };
  for (const { pos, yaw, lift = 0 } of places) {
    const c = Math.cos(yaw);
    const s = Math.sin(yaw);
    const at = (x: number, y: number, z: number): [number, number, number] => [x * c + z * s + pos[0], y + pos[1] + lift, -x * s + z * c + pos[2]];
    for (const f of model.fires) {
      const a = at(f.at[0], f.at[1], f.at[2]);
      out.fires.push({
        ...f,
        at: a,
        floor: f.floor + pos[1] + lift,
        seed: seedAt(a[0], a[1], a[2]),
        tongues: f.tongues.map((t) => ({ ...t, d: [t.d[0] * c + t.d[2] * s, t.d[1], -t.d[0] * s + t.d[2] * c] })),
      });
    }
    for (const [x, y, z, h] of model.candles) out.candles.push([...at(x, y, z), h]);
  }
  return out.fires.length || out.candles.length ? out : null;
}

/** A small deterministic hash of a position (0..1): a candle's seeds. */
export function seedAt(x: number, y: number, z: number): number {
  const s = Math.sin(x * 12.9898 + y * 78.233 + z * 37.719) * 43758.5453;
  return s - Math.floor(s);
}

/** Instance buffers: a column-major 4x4 per instance (Babylon's thin-instance "matrix") and a vec4 each. */
export interface InstanceBuffers {
  count: number;
  matrix: Float32Array;
  data: Float32Array;
}

function writeCard(m: Float32Array, data: Float32Array, i: number, x: number, y: number, z: number, sx: number, sy: number, d: [number, number, number, number]): void {
  const o = i * 16;
  m.fill(0, o, o + 16);
  m[o] = sx;
  m[o + 5] = sy;
  m[o + 10] = sx;
  m[o + 12] = x;
  m[o + 13] = y;
  m[o + 14] = z;
  m[o + 15] = 1;
  data.set(d, i * 4);
}

/**
 * The flame cards of `fires` and `candles` in drawing order (each fire's
 * tongues back to front as authored, then the candles). `offset` moves every
 * position (a moving group's rest position taken out).
 */
export function tongueBuffers(fires: readonly FireSpec[], candles: readonly (readonly [number, number, number, number])[], offset: readonly [number, number, number] = [0, 0, 0]): InstanceBuffers {
  const count = fires.reduce((n, f) => n + f.tongues.length, 0) + candles.length;
  const matrix = new Float32Array(count * 16);
  const data = new Float32Array(count * 4);
  let i = 0;
  for (const f of fires) {
    f.tongues.forEach((t, k) => {
      const shape = (f.seed * 7.31 + k * 0.618034) % 1;
      writeCard(matrix, data, i++, f.at[0] + t.d[0] - offset[0], f.at[1] + t.d[1] - offset[1], f.at[2] + t.d[2] - offset[2], t.w * CARD_W, t.h * CARD_H, [shape, f.seed, 1, 0]);
    });
  }
  for (const [x, y, z, h] of candles) {
    const s = seedAt(x, y, z);
    writeCard(matrix, data, i++, x - offset[0], y - offset[1], z - offset[2], h * CANDLE_ASPECT * CARD_W, h * CARD_H, [s, (s * 3.7) % 1, 0.95, 1]);
  }
  return { count, matrix, data };
}

/** A floor glow lies this far over its floor (m; with the material's depth offset, no z-fighting). */
export const POOL_LIFT = 0.012;

/** The floor glow under each fire that has one: a flat disc `pool` metres in radius on the fire's floor. */
export function poolBuffers(fires: readonly FireSpec[]): InstanceBuffers {
  const list = fires.filter((f) => f.pool > 0);
  const matrix = new Float32Array(list.length * 16);
  const data = new Float32Array(list.length * 4);
  list.forEach((f, i) => writeCard(matrix, data, i, f.at[0], f.floor + POOL_LIFT, f.at[2], f.pool * 2, 1, [f.seed, f.seed, 1, 0]));
  return { count: list.length, matrix, data };
}

/** Each fire's embers: one instance per spark, spread and rise from the fire's size, seeds by index. */
export function emberBuffers(fires: readonly FireSpec[]): InstanceBuffers {
  const count = fires.reduce((n, f) => n + f.embers, 0);
  const matrix = new Float32Array(count * 16);
  const data = new Float32Array(count * 4);
  let i = 0;
  for (const f of fires) {
    const h = Math.max(...f.tongues.map((t) => t.h));
    let x0 = Infinity, x1 = -Infinity;
    for (const t of f.tongues) {
      x0 = Math.min(x0, t.d[0]);
      x1 = Math.max(x1, t.d[0]);
    }
    const spread = Math.max(0.05, (x1 - x0) * 0.5 + 0.1) * EMBER_SPREAD;
    for (let k = 0; k < f.embers; k++) {
      const s = (f.seed * 3.17 + k * 0.381966) % 1;
      // Each spark starts somewhere along the fire's width, a little up the flames.
      const sx = x0 + (x1 - x0) * ((k + 0.5) / f.embers);
      writeCard(matrix, data, i++, f.at[0] + sx, f.at[1] + h * 0.35, f.at[2] + f.tongues[0]!.d[2] * 0.5, spread, h * EMBER_RISE, [s, f.seed, 1, 0]);
    }
  }
  return { count, matrix, data };
}

// ------------------------------------------------------------------ palette

/** The fire's colours (linear 0..1 sRGB values as the shaders take them) and amounts, from toon_palette.json `fire`. */
export interface FireLook {
  core: [number, number, number];
  mid: [number, number, number];
  edge: [number, number, number];
  tip: [number, number, number];
  pool: [number, number, number];
  poolStrength: number;
  ember: [number, number, number];
  flicker: number;
  lightFlicker: number;
}

export const FIRE_LOOK_DEFAULT: FireLook = {
  core: [1, 0.965, 0.81],
  mid: [1, 0.77, 0.28],
  edge: [1, 0.54, 0.18],
  tip: [0.95, 0.32, 0.16],
  pool: [1, 0.56, 0.23],
  poolStrength: 0.42,
  ember: [1, 0.7, 0.29],
  flicker: 0.07,
  lightFlicker: 0.1,
};

function hex(v: unknown, fallback: [number, number, number]): [number, number, number] {
  if (typeof v !== "string" || !/^#[0-9a-f]{6}$/i.test(v)) return fallback;
  const n = Number.parseInt(v.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function amount(v: unknown, lo: number, hi: number, fallback: number): number {
  return num(v) ? Math.min(hi, Math.max(lo, v)) : fallback;
}

/** toon_palette.json's `fire` block (each field falls back to the default on its own). */
export function parseFireLook(json: unknown): FireLook {
  const f = ((json as { fire?: unknown } | null)?.fire ?? {}) as Record<string, unknown>;
  const d = FIRE_LOOK_DEFAULT;
  return {
    core: hex(f.core, d.core),
    mid: hex(f.mid, d.mid),
    edge: hex(f.edge, d.edge),
    tip: hex(f.tip, d.tip),
    pool: hex(f.pool, d.pool),
    poolStrength: amount(f.poolStrength, 0, 1, d.poolStrength),
    ember: hex(f.ember, d.ember),
    flicker: amount(f.flicker, 0, 0.3, d.flicker),
    lightFlicker: amount(f.lightFlicker, 0, 0.3, d.lightFlicker),
  };
}

export const FIRE_LOOK: FireLook = parseFireLook(toonPaletteJson);
