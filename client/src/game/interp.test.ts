import { describe, expect, it } from "vitest";
import { angleDelta, SnapshotClock, Track, MAX_EXTRAPOLATE_MS, type Pose } from "./interp";

const pose = (): Pose => ({ x: 0, y: 0, z: 0, yaw: 0, anim: "idle" });

describe("SnapshotClock", () => {
  it("tracks the smallest offset and follows drift slowly", () => {
    const c = new SnapshotClock();
    c.observe(1000, 5050); // 50 ms late
    c.observe(1100, 5120); // 20 ms late: new minimum
    expect(c.serverTime(5120)).toBe(1100);
    c.observe(1200, 5300); // 80 ms later than the best: barely moves the estimate
    const t = c.serverTime(5300);
    expect(t).toBeLessThan(1280); // the best-case mapping would say 1280
    expect(t).toBeGreaterThan(1275);
  });
});

describe("Track", () => {
  it("interpolates between bracketing states", () => {
    const t = new Track(100);
    t.push({ t: 0, x: 0, y: 0, z: 0, yaw: 0, anim: "walk" });
    t.push({ t: 100, x: 1, y: 0, z: 2, yaw: 0, anim: "walk" });
    const p = pose();
    expect(t.sampleAt(50, p)).toBe(true);
    expect(p.x).toBeCloseTo(0.5);
    expect(p.z).toBeCloseTo(1);
  });

  it("holds the first state before it and is empty-safe", () => {
    const t = new Track(100);
    const p = pose();
    expect(t.sampleAt(0, p)).toBe(false);
    t.push({ t: 100, x: 3, y: 0, z: 0, yaw: 0, anim: "idle" });
    t.sampleAt(0, p);
    expect(p.x).toBe(3);
  });

  it("extrapolates a moving entity briefly, but not a stopped one", () => {
    const t = new Track(100);
    t.push({ t: 0, x: 0, y: 0, z: 0, yaw: 0, anim: "run" });
    t.push({ t: 100, x: 1, y: 0, z: 0, yaw: 0, anim: "run" });
    const p = pose();
    t.sampleAt(150, p);
    expect(p.x).toBeCloseTo(1.5);
    t.sampleAt(10_000, p);
    expect(p.x).toBeCloseTo(1 + MAX_EXTRAPOLATE_MS / 100);

    t.push({ t: 200, x: 2, y: 0, z: 0, yaw: 0, anim: "idle" });
    t.sampleAt(10_000, p);
    expect(p.x).toBe(2);
  });

  it("starts moving from rest instead of gliding across a quiet gap", () => {
    const t = new Track(100);
    t.push({ t: 0, x: 0, y: 0, z: 0, yaw: 0, anim: "idle" });
    // Nothing for 2 s (standing still), then a state 0.4 m away.
    t.push({ t: 2000, x: 0.4, y: 0, z: 0, yaw: 0, anim: "walk" });
    const p = pose();
    t.sampleAt(1000, p);
    expect(p.x).toBe(0);
    t.sampleAt(1950, p);
    expect(p.x).toBeCloseTo(0.2);
  });

  it("snaps across teleports", () => {
    const t = new Track(100);
    t.push({ t: 0, x: 0, y: 0, z: 0, yaw: 0, anim: "walk" });
    t.push({ t: 100, x: 50, y: 0, z: 0, yaw: 0, anim: "walk" });
    const p = pose();
    t.sampleAt(50, p);
    expect(p.x).toBe(50);
    expect(t.length).toBe(1);
  });

  it("ignores out-of-order states and replaces duplicates", () => {
    const t = new Track(100);
    t.push({ t: 100, x: 1, y: 0, z: 0, yaw: 0, anim: "walk" });
    t.push({ t: 50, x: 9, y: 0, z: 0, yaw: 0, anim: "walk" });
    t.push({ t: 100, x: 2, y: 0, z: 0, yaw: 0, anim: "walk" });
    expect(t.length).toBe(1);
    expect(t.latest?.x).toBe(2);
  });

  it("turns the short way round", () => {
    const t = new Track(100);
    t.push({ t: 0, x: 0, y: 0, z: 0, yaw: 6.2, anim: "walk" });
    t.push({ t: 100, x: 0.1, y: 0, z: 0, yaw: 0.1, anim: "walk" });
    const p = pose();
    t.sampleAt(50, p);
    // Halfway between 6.2 and 0.1 through 2π, not back through π.
    expect(Math.abs(angleDelta(p.yaw, (6.2 + 0.1 + 2 * Math.PI) / 2))).toBeLessThan(1e-6);
  });
});

describe("angleDelta", () => {
  it("is the shortest signed difference", () => {
    expect(angleDelta(0, Math.PI / 2)).toBeCloseTo(Math.PI / 2);
    expect(angleDelta(0.1, 2 * Math.PI - 0.1)).toBeCloseTo(-0.2);
    expect(angleDelta(2 * Math.PI - 0.1, 0.1)).toBeCloseTo(0.2);
  });
});
