// The castle's lighting by game hour, from the art side's
// data/art/lighting.json (docs/art/style-guide.md, "Lighting data"): the
// sun or moon (direction, colour, intensity), the hemisphere fill, the sky
// gradient and the fog, interpolated linearly between the keys and wrapping
// at 24 h. Pure (no Babylon), unit tested; clock/daynight.ts applies it.

import lightingJson from "../../../../data/art/lighting.json";

export type Rgb = [number, number, number];
export type Vec3 = [number, number, number];

/** The castle's light at one game hour (colours sRGB 0..1). */
export interface CastleLight {
  /** From the light into the scene (Babylon `DirectionalLight.direction`), unit length. */
  sunDirection: Vec3;
  sunColour: Rgb;
  sunIntensity: number;
  /** 0 when the sun is up, 1 when the moon is (blended across the hand-over). */
  moon: number;
  hemiSky: Rgb;
  hemiGround: Rgb;
  hemiIntensity: number;
  zenith: Rgb;
  horizon: Rgb;
  fog: Rgb;
}

interface KeyJson {
  hour: number;
  sun: { body: string; direction: number[]; color: string; intensity: number };
  hemi: { sky: string; ground: string; intensity: number };
  sky: { zenith: string; horizon: string };
  fog: string;
}

export function hexRgb(hex: string): Rgb {
  const n = Number.parseInt(hex.replace("#", ""), 16);
  if (!Number.isFinite(n)) return [1, 1, 1];
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

interface Key {
  hour: number;
  light: CastleLight;
}

function parseKey(k: KeyJson): Key {
  const d = k.sun.direction;
  const len = Math.hypot(d[0] ?? 0, d[1] ?? -1, d[2] ?? 0) || 1;
  return {
    hour: k.hour,
    light: {
      sunDirection: [(d[0] ?? 0) / len, (d[1] ?? -1) / len, (d[2] ?? 0) / len],
      sunColour: hexRgb(k.sun.color),
      sunIntensity: k.sun.intensity,
      moon: k.sun.body === "moon" ? 1 : 0,
      hemiSky: hexRgb(k.hemi.sky),
      hemiGround: hexRgb(k.hemi.ground),
      hemiIntensity: k.hemi.intensity,
      zenith: hexRgb(k.sky.zenith),
      horizon: hexRgb(k.sky.horizon),
      fog: hexRgb(k.fog),
    },
  };
}

/** The keys, sorted by hour (the shipped file's, unless a test passes its own). */
export function parseLighting(json: { castle: { keys: KeyJson[] } } = lightingJson as unknown as { castle: { keys: KeyJson[] } }): Key[] {
  const keys = json.castle.keys.map(parseKey).sort((a, b) => a.hour - b.hour);
  if (keys.length === 0) throw new Error("lighting.json has no castle keys");
  return keys;
}

const KEYS = parseLighting();

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;
const mix3 = <T extends Rgb | Vec3>(a: T, b: T, t: number): T => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)] as T;

/** The rim light colour of the data (sRGB 0..1). */
export const RIM_COLOUR: Rgb = hexRgb((lightingJson as { rim?: { color?: string } }).rim?.color ?? "#fff2de");

/** The castle's light at game hour `hour` (0-24, wrapping). */
export function castleLight(hour: number, keys: readonly Key[] = KEYS): CastleLight {
  const h = ((Number.isFinite(hour) ? hour : 12) % 24 + 24) % 24;
  let i = keys.length - 1;
  for (let k = 0; k < keys.length; k++) if (keys[k]!.hour <= h) i = k;
  const a = keys[i]!;
  const b = keys[(i + 1) % keys.length]!;
  let span = b.hour - a.hour;
  let at = h - a.hour;
  if (span <= 0) span += 24;
  if (at < 0) at += 24;
  const t = span > 0 ? Math.min(1, Math.max(0, at / span)) : 0;
  const A = a.light;
  const B = b.light;
  // The sun and the moon trade places between keys: blend directions only within one body.
  const sameBody = A.moon === B.moon;
  const dir = sameBody ? mix3(A.sunDirection, B.sunDirection, t) : t < 0.5 ? A.sunDirection : B.sunDirection;
  const len = Math.hypot(dir[0], dir[1], dir[2]) || 1;
  return {
    sunDirection: [dir[0] / len, dir[1] / len, dir[2] / len],
    sunColour: mix3(A.sunColour, B.sunColour, t),
    sunIntensity: lerp(A.sunIntensity, B.sunIntensity, t),
    moon: lerp(A.moon, B.moon, t),
    hemiSky: mix3(A.hemiSky, B.hemiSky, t),
    hemiGround: mix3(A.hemiGround, B.hemiGround, t),
    hemiIntensity: lerp(A.hemiIntensity, B.hemiIntensity, t),
    zenith: mix3(A.zenith, B.zenith, t),
    horizon: mix3(A.horizon, B.horizon, t),
    fog: mix3(A.fog, B.fog, t),
  };
}

/** What the scene's lights are set to for a castle light (Babylon units, sRGB colours). */
export interface SceneLights {
  sunIntensity: number;
  sunColour: Rgb;
  hemiIntensity: number;
  hemiSky: Rgb;
  hemiGround: Rgb;
}

/**
 * How the data's numbers become the toon model's lights, matching the art
 * side's review renders (tools/blender/world/render_toon.py): the key light
 * is `0.3 + 0.7 · intensity` in its hue (the colour normalised, 60 % toward
 * white), the fill `intensity / 0.62` of a half-whitened sky colour, and the
 * pair is scaled by one exposure so a surface lit at noon is about 1. The
 * toon model adds them (lit = fill + key; shadow = (fill + key · shadowLight)
 * · tint), so night stays bright enough to read by hue and keeps visible bands.
 */
export const LIGHT_MAPPING = { keyBase: 0.3, keySlope: 0.7, hueMix: 0.6, fillLevel: 1.64, fillRef: 0.62, exposure: 1 / (1 + 1.64) };

export function sceneLights(l: CastleLight): SceneLights {
  const m = LIGHT_MAPPING;
  const peak = Math.max(l.sunColour[0], l.sunColour[1], l.sunColour[2], 1e-3);
  const hue = (c: number) => 1 + ((c / peak) - 1) * m.hueMix;
  const half = (c: number) => Math.min(1.1, 0.5 + (0.5 * c) / 0.85);
  return {
    sunIntensity: m.exposure * (m.keyBase + m.keySlope * l.sunIntensity),
    sunColour: [hue(l.sunColour[0]), hue(l.sunColour[1]), hue(l.sunColour[2])],
    hemiIntensity: (m.exposure * m.fillLevel * l.hemiIntensity) / m.fillRef,
    hemiSky: [half(l.hemiSky[0]), half(l.hemiSky[1]), half(l.hemiSky[2])],
    hemiGround: [half(l.hemiGround[0]), half(l.hemiGround[1]), half(l.hemiGround[2])],
  };
}

/**
 * A room's fill (its manifest `ambient`: sky, ground, intensity) for the toon
 * model, as the review renders light rooms: the colours normalised and
 * mostly whitened, the strength `0.75 + 0.35 · min(1, intensity)` times
 * `ROOM_FILL`. Rooms have no sun: the fill carries most of a wall's
 * light (the review renders use the strength as is, with the lamps' pools
 * on top), so it sits well above the castle's fill (0.62 at noon, where the
 * sun adds the rest); at the castle's 0.62 rooms read dim and muddy.
 */
export const ROOM_FILL = 0.8;

export function roomFill(sky: Rgb, ground: Rgb, intensity: number): { intensity: number; sky: Rgb; ground: Rgb } {
  const norm = (c: Rgb, base: number): Rgb => {
    const peak = Math.max(c[0], c[1], c[2], 1e-3);
    return [base + (1 - base) * (c[0] / peak), base + (1 - base) * (c[1] / peak), base + (1 - base) * (c[2] / peak)];
  };
  const level = 0.75 + 0.35 * Math.min(1, Math.max(0, Number.isFinite(intensity) ? intensity : 0.4));
  return { intensity: level * ROOM_FILL, sky: norm(sky, 0.6), ground: norm(ground, 0.45).map((v) => v * 0.8) as Rgb };
}
