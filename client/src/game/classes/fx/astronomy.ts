// Astronomy (night only): the student charts a constellation
// through its stars. The trace pad turns into a patch of night sky: a
// faint field of background stars and the constellation's own stars (its
// template's vertices, data/classes/gestures.json `const_*`) as bright
// points, the dashed guide between them dimmed. The room itself is open to
// the sky (`instances/school.json` `open_sky`, zones.ts `parseOpenSky`), lit
// by the castle's night.
// A charted constellation twinkles over the student's telescope.
//
// Pure helpers, unit tested in fx.test.ts.

import { PALETTE, hexToRgb01 } from "../../../content/catalog";
import type { ClassTraced } from "./hooks";

/** Gestures that are constellations (`const_*`): the pad shows the sky for them. */
export function isConstellation(gestureKey: string | undefined | null): boolean {
  return !!gestureKey && gestureKey.startsWith("const_");
}

/** A constellation's stars: its template's vertices (pad units), each once, in stroke order. */
export function constellationStars(points: readonly (readonly [number, number])[]): [number, number][] {
  const out: [number, number][] = [];
  for (const p of points) {
    if (out.some((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) < 1)) continue;
    out.push([p[0], p[1]]);
  }
  return out;
}

/** A background star: where on the pad (-100..100, y up), how big (0..1) and how bright (0..1). */
export interface SkyStar {
  x: number;
  y: number;
  size: number;
  bright: number;
}

/** `n` background stars scattered over the pad, the same every time for `seed` (no per-frame randomness). */
export function starField(n: number, seed = 1): SkyStar[] {
  let s = (seed * 2654435761) >>> 0 || 1;
  const rnd = () => {
    // xorshift32
    s ^= s << 13;
    s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5;
    s >>>= 0;
    return s / 4294967296;
  };
  const out: SkyStar[] = [];
  for (let i = 0; i < n; i++) {
    const r = rnd();
    out.push({ x: rnd() * 220 - 110, y: rnd() * 220 - 110, size: 0.25 + 0.75 * r * r, bright: 0.25 + 0.6 * rnd() });
  }
  return out;
}

const SKY = PALETTE.colours.find((c) => c.id === "sky");
const SKY_RGB: [number, number, number] = SKY ? hexToRgb01(SKY.hex) : [0.56, 0.71, 0.85];

/** A charted constellation twinkles above the student's telescope. */
export const astronomyTraced: ClassTraced = (ctx, gesture) => {
  if (!isConstellation(gesture)) return;
  const at = ctx.desk();
  if (at) ctx.burst(at[0], at[1] + 0.9, at[2], SKY_RGB, 10, 0.6);
};
