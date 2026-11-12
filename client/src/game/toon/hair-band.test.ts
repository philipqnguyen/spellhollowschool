import { describe, expect, it } from "vitest";
import { HAIR_BAND_EDGE, HAIR_BAND_LIFT, HAIR_BAND_SATURATION, hairBandColour } from "./material";

const luma = (c: readonly number[]): number => 0.3 * c[0]! + 0.59 * c[1]! + 0.11 * c[2]!;
const chroma = (c: readonly number[]): number[] => {
  const s = c[0]! + c[1]! + c[2]!;
  return c.map((v) => v / s);
};

// The hair highlight band (review fix): a lighter tone of the hair colour, not a grey.
describe("hair highlight band colour", () => {
  it("keeps a dark hair colour's hue and lifts its luma by HAIR_BAND_LIFT", () => {
    for (const base of [[0.2, 0.12, 0.07], [0.12, 0.1, 0.18], [0.25, 0.08, 0.06]]) {
      const out = hairBandColour(base);
      const l = luma(base);
      expect(luma(out)).toBeCloseTo(l + (1 - l) * HAIR_BAND_LIFT, 5);
      // The hue is the base's: the scaled colour, HAIR_BAND_SATURATION of the way out from its grey.
      const k = (l + (1 - l) * HAIR_BAND_LIFT) / l;
      const lo = luma(out);
      for (let i = 0; i < 3; i++) expect(out[i]!).toBeCloseTo(lo + (base[i]! * k - lo) * HAIR_BAND_SATURATION, 5);
      // Never the old grey: the channels keep their order and most of their spread.
      const a = chroma(base), b = chroma(out);
      expect(Math.sign(b[0]! - b[2]!)).toBe(Math.sign(a[0]! - a[2]!));
    }
  });

  it("caps light hair at white without a blown-out bar", () => {
    const base = [0.95, 0.8, 0.45];
    const out = hairBandColour(base);
    for (const v of out) expect(v).toBeLessThanOrEqual(1);
    const l = luma(base);
    expect(luma(out)).toBeCloseTo(l + (1 - l) * HAIR_BAND_LIFT, 3);
    // Still warmer than grey: red stays above blue.
    expect(out[0]! - out[2]!).toBeGreaterThan(0.05);
  });

  it("has a flat core inside a short edge", () => {
    expect(HAIR_BAND_EDGE[0]).toBeGreaterThan(0);
    expect(HAIR_BAND_EDGE[1]).toBeGreaterThan(HAIR_BAND_EDGE[0]);
    expect(HAIR_BAND_EDGE[1]).toBeLessThanOrEqual(1);
  });
});
