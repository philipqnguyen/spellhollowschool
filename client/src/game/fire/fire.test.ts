import { describe, expect, it } from "vitest";
import { CARD_H, CARD_W, emberBuffers, FIRE_LOOK, mergeFire, parseFire, parseFireLook, placeFire, poolBuffers, POOL_LIFT, tongueBuffers } from "./data";
import { FIRE_PERIOD, fireTime, flicker, FLICKER_GLSL, ripple, RIPPLE_AMOUNT } from "./flicker";
import { FLAME_FRAGMENT, FLAME_VERTEX } from "./shaders";

const MANIFEST = {
  fires: [
    { at: [1, 2, 3], seed: 0.25, tongues: [[0, 0, -0.1, 1.2, 0.4], [0.3, 0, 0.1, 0.8, 0.3]], pool: 2, floor: 1.7, embers: 6 },
    { at: [10, 0, 0], seed: 0.5, tongues: [[0, 0, 0, 0.4, 0.15]] },
    { at: [0, 0], tongues: [[0, 0, 0, 1, 1]] }, // bad anchor: skipped
  ],
  candles: [[5, 6, 7, 0.13], [1, 1, 1, -1]], // the second has no height: skipped
  groups: { "great_hall.castle_props.candles_0": [[0, 5, 0, 0.155]] },
};

describe("fire data", () => {
  it("parses a manifest entry, skipping bad rows", () => {
    const d = parseFire(MANIFEST)!;
    expect(d.fires).toHaveLength(2);
    expect(d.fires[0]!.tongues).toHaveLength(2);
    expect(d.fires[0]!.pool).toBe(2);
    expect(d.fires[0]!.floor).toBe(1.7);
    expect(d.fires[1]!.floor).toBe(0); // no floor given: the anchor's height
    expect(d.fires[1]!.embers).toBe(0);
    expect(d.candles).toEqual([[5, 6, 7, 0.13]]);
    expect([...d.groups.keys()]).toEqual(["great_hall.castle_props.candles_0"]);
    expect(parseFire(null)).toBeNull();
    expect(parseFire({ fires: [], candles: [] })).toBeNull();
  });

  it("joins a room's entries", () => {
    const a = parseFire(MANIFEST);
    const b = parseFire({ candles: [[0, 0, 0, 0.1]], groups: { "great_hall.castle_props.candles_0": [[1, 5, 0, 0.155]] } });
    const m = mergeFire([a, null, b])!;
    expect(m.candles).toHaveLength(2);
    expect(m.groups.get("great_hall.castle_props.candles_0")).toHaveLength(2);
    expect(mergeFire([null, null])).toBeNull();
  });

  it("places each tongue's card on its foot, sized from the tongue", () => {
    const d = parseFire(MANIFEST)!;
    const b = tongueBuffers(d.fires, d.candles);
    expect(b.count).toBe(4); // 2 + 1 tongues, 1 candle
    // First tongue: foot at at + d, scale w x CARD_W, h x CARD_H.
    expect([b.matrix[12], b.matrix[13], b.matrix[14]]).toEqual([1, 2, 3 - 0.1].map(Math.fround));
    expect(b.matrix[0]).toBeCloseTo(0.4 * CARD_W, 5);
    expect(b.matrix[5]).toBeCloseTo(1.2 * CARD_H, 5);
    expect(b.matrix[15]).toBe(1);
    // flame data: [shape seed, flicker seed (the fire's), brightness, kind].
    expect(b.data[1]).toBeCloseTo(0.25);
    expect(b.data[5]).toBeCloseTo(0.25);
    expect(b.data[3]).toBe(0);
    // Tongues of one fire get different shape seeds.
    expect(b.data[0]).not.toBeCloseTo(b.data[4]!, 3);
    // The candle: kind 1, at its foot.
    expect(b.data[15]).toBe(1);
    expect([b.matrix[48 + 12], b.matrix[48 + 13], b.matrix[48 + 14]]).toEqual([5, 6, 7]);
    // An offset moves every card (a moving group's rest position).
    const o = tongueBuffers([], d.candles, [5, 6, 7]);
    expect([o.matrix[12], o.matrix[13], o.matrix[14]]).toEqual([0, 0, 0]);
  });

  it("places a furniture model's flames at each copy, turned like the copy", () => {
    const model = parseFire({ fires: [{ at: [0, 0.1, 0], seed: 0, tongues: [[0.1, 0, 0, 0.1, 0.05]] }], candles: [[0, 0.2, 0.1, 0.06]] })!;
    const placed = placeFire(model, [
      { pos: [5, 1, 5], yaw: 0 },
      { pos: [0, 0, 0], yaw: Math.PI / 2, lift: 0.5 },
    ])!;
    expect(placed.fires).toHaveLength(2);
    expect(placed.candles).toHaveLength(2);
    expect(placed.fires[0]!.at).toEqual([5, 1.1, 5]);
    // A quarter turn: x' = z, z' = -x (the furniture matrices' convention).
    const t = placed.fires[1]!.tongues[0]!.d;
    expect(t[0]).toBeCloseTo(0, 6);
    expect(t[2]).toBeCloseTo(-0.1, 6);
    expect(placed.candles[1]![0]).toBeCloseTo(0.1, 6);
    expect(placed.candles[1]![1]).toBeCloseTo(0.7, 6);
    // Each copy flickers on its own.
    expect(placed.fires[0]!.seed).not.toBeCloseTo(placed.fires[1]!.seed, 3);
    expect(placeFire(model, [])).toBeNull();
  });

  it("lays floor glows on the floor and counts embers", () => {
    const d = parseFire(MANIFEST)!;
    const p = poolBuffers(d.fires);
    expect(p.count).toBe(1);
    expect(p.matrix[13]).toBeCloseTo(1.7 + POOL_LIFT, 5);
    expect(p.matrix[0]).toBeCloseTo(4, 5);
    const e = emberBuffers(d.fires);
    expect(e.count).toBe(6);
    for (let i = 0; i < e.count; i++) expect(e.data[i * 4 + 1]).toBeCloseTo(0.25); // the fire's seed
  });
});

describe("fire flicker", () => {
  it("stays within its amount and never strobes", () => {
    let lo = Infinity;
    let hi = -Infinity;
    let maxStep = 0;
    let prev = flicker(0, 0.3, 0.1);
    for (let i = 1; i <= 6000; i++) {
      const v = flicker(i / 60, 0.3, 0.1);
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
      maxStep = Math.max(maxStep, Math.abs(v - prev));
      prev = v;
    }
    expect(lo).toBeGreaterThanOrEqual(0.9 - 1e-9);
    expect(hi).toBeLessThanOrEqual(1.1 + 1e-9);
    // At 60 fps a frame never changes the light by more than a few per cent.
    expect(maxStep).toBeLessThan(0.03);
  });

  it("a lake window's ripple swells slowly within its amount and runs on across the clock's wrap", () => {
    let lo = Infinity;
    let hi = -Infinity;
    let maxStep = 0;
    let prev = ripple(0, 0.4);
    for (let i = 1; i <= 60 * 60; i++) {
      const v = ripple(i / 60, 0.4);
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
      maxStep = Math.max(maxStep, Math.abs(v - prev));
      prev = v;
    }
    expect(lo).toBeGreaterThanOrEqual(1 - RIPPLE_AMOUNT - 1e-9);
    expect(hi).toBeLessThanOrEqual(1 + RIPPLE_AMOUNT + 1e-9);
    expect(hi - lo).toBeGreaterThan(RIPPLE_AMOUNT); // it does swell
    // Far slower than a fire: well under half a per cent a frame at 60 fps.
    expect(maxStep).toBeLessThan(0.005);
    // Whole turns per FIRE_PERIOD: the wrapped clock gives the same light.
    expect(ripple(FIRE_PERIOD, 0.4)).toBeCloseTo(ripple(0, 0.4), 6);
    expect(ripple(3, 0.1)).not.toBeCloseTo(ripple(3, 0.7), 3);
  });

  it("has a different phase per seed and a wrapped clock", () => {
    expect(flicker(1, 0.1, 0.1)).not.toBeCloseTo(flicker(1, 0.6, 0.1), 3);
    expect(fireTime(1000 * (FIRE_PERIOD + 2.5))).toBeCloseTo(2.5, 6);
  });

  it("the shaders evaluate the same sum as the lights", () => {
    // The GLSL sum's constants are the TS ones (one source).
    for (const c of ["7.3", "12.9", "21.7", "0.55", "0.30", "0.15"]) expect(FLICKER_GLSL).toContain(c);
    expect(FLAME_VERTEX).toContain("fireFlicker(time, flame.y, flickerAmount)");
    // The cards' size factors are baked into the fragment shader.
    expect(FLAME_FRAGMENT).toContain(`vUv.x * ${CARD_W}`);
    expect(FLAME_FRAGMENT).toContain(`vUv.y * ${CARD_H}`);
  });
});

describe("fire palette", () => {
  it("reads toon_palette.json's fire block, falling back per field", () => {
    expect(FIRE_LOOK.core[0]).toBeGreaterThan(0.95);
    const l = parseFireLook({ fire: { core: "#ff0000", flicker: 5, mid: "nope" } });
    expect(l.core).toEqual([1, 0, 0]);
    expect(l.flicker).toBe(0.3); // clamped
    expect(l.mid).toEqual(parseFireLook({}).mid);
  });

  it("keeps every flame colour bright enough to bloom (post.ts threshold 0.95 on the brightest channel)", () => {
    for (const c of [FIRE_LOOK.core, FIRE_LOOK.mid, FIRE_LOOK.edge, FIRE_LOOK.tip]) expect(Math.max(...c)).toBeGreaterThanOrEqual(0.95);
  });
});
