import { describe, expect, it } from "vitest";
import { BurstPool } from "./bursts";
import { hasAxis, Shape } from "./shapes";

const RED = [1, 0, 0] as const;

describe("burst particles", () => {
  it("move, fall, grow, fade and expire in a fixed pool", () => {
    const pool = new BurstPool(4);
    expect(pool.spawn({ shape: Shape.orb, x: 0, y: 1, z: 0, vx: 2, vy: 0, life: 1, size: 0.2, sizeEnd: 1, rgb: RED, gravity: 10 })).toBe(true);
    pool.step(0.5);
    let seen = 0;
    pool.forEach((shape, x, y, _z, size, r, _g, _b, a) => {
      seen++;
      expect(shape).toBe(Shape.orb);
      expect(x).toBeCloseTo(1);
      expect(y).toBeLessThan(1); // fell
      expect(size).toBeGreaterThan(0.6); // eased toward 1
      expect(size).toBeLessThan(1);
      expect(a).toBeGreaterThan(0);
      expect(a).toBeLessThan(0.5);
      expect(r).toBe(1);
    });
    expect(seen).toBe(1);
    pool.step(0.6);
    expect(pool.count).toBe(0);
  });

  it("refuses particles beyond its capacity and swap-removes the spent", () => {
    const pool = new BurstPool(3);
    pool.spawn({ shape: Shape.spark, x: 0, y: 0, z: 0, life: 0.1, size: 1, rgb: RED });
    pool.spawn({ shape: Shape.ring, x: 1, y: 0, z: 0, life: 1, size: 1, rgb: RED });
    pool.spawn({ shape: Shape.shard, x: 2, y: 0, z: 0, life: 1, size: 1, rgb: RED });
    expect(pool.spawn({ shape: Shape.orb, x: 3, y: 0, z: 0, life: 1, size: 1, rgb: RED })).toBe(false);
    expect(pool.spawn({ shape: Shape.orb, x: 3, y: 0, z: 0, life: 0, size: 1, rgb: RED })).toBe(false);
    pool.step(0.2);
    expect(pool.count).toBe(2);
    const xs: number[] = [];
    pool.forEach((_s, x) => xs.push(x));
    expect(xs.sort()).toEqual([1, 2]);
    // Sparks stop at the capacity too.
    pool.sparks(10, 0, 0, 0, 3, RED);
    expect(pool.count).toBe(3);
    pool.clear();
    expect(pool.count).toBe(0);
  });

  it("keeps the shape ids distinct and knows which need an axis (discs and beams, protocol v11's VFX)", () => {
    const ids = Object.values(Shape);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.filter(hasAxis).sort()).toEqual([Shape.streak, Shape.disc, Shape.beam].sort());
    // Disc particles in a burst (no axis: they lie flat) shrink toward their end size: bind rings closing.
    const pool = new BurstPool(2);
    pool.spawn({ shape: Shape.disc, x: 0, y: 1, z: 0, life: 0.5, size: 2.2, sizeEnd: 0.75, rgb: RED });
    pool.step(0.25);
    pool.forEach((shape, _x, y, _z, size) => {
      expect(shape).toBe(Shape.disc);
      expect(y).toBe(1);
      expect(size).toBeLessThan(1.5);
      expect(size).toBeGreaterThan(0.75);
    });
  });

  it("jitters deterministically", () => {
    const a = new BurstPool(1);
    const b = new BurstPool(1);
    const xs = Array.from({ length: 5 }, () => a.jitter());
    expect(Array.from({ length: 5 }, () => b.jitter())).toEqual(xs);
    for (const x of xs) expect(Math.abs(x)).toBeLessThanOrEqual(1);
  });
});
