import { describe, expect, it } from "vitest";
import { grantHolds, horizontalDistance, pickCharacter, project, segmentDistance, type PickTarget } from "./pick";

/**
 * A row-major view-projection (Babylon's layout) for a camera at the origin
 * looking down -Z with a 90° field of view and a square screen: x' = x,
 * y' = y, w = -z.
 */
const M = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1, 0, 0, 0, 0];
const W = 800;
const H = 800;

const target = (id: number, x: number, z: number, height = 1.8, y = -1): PickTarget => ({ id, name: `P${id}`, x, y, z, height });

describe("projection", () => {
  it("puts the view axis in the middle of the screen", () => {
    expect(project(M, 0, 0, -5, W, H)).toEqual({ x: 400, y: 400, w: 5 });
    const p = project(M, 1, 1, -2, W, H)!;
    expect(p.x).toBeCloseTo(600);
    expect(p.y).toBeCloseTo(200);
    expect(project(M, 0, 0, 3, W, H)).toBeNull(); // behind the camera
  });

  it("measures distance to a segment", () => {
    expect(segmentDistance(5, 0, 0, 0, 10, 0)).toBe(0);
    expect(segmentDistance(5, 3, 0, 0, 10, 0)).toBe(3);
    expect(segmentDistance(-4, 3, 0, 0, 10, 0)).toBe(5);
    expect(segmentDistance(1, 1, 0, 0, 0, 0)).toBeCloseTo(Math.SQRT2);
  });
});

describe("picking a character", () => {
  it("takes the body under the pointer", () => {
    const a = target(1, 0, -5);
    const b = target(2, 2, -5);
    // a stands on the view axis: its body spans y 480 (feet) to 336 (head) at x 400.
    expect(pickCharacter([a, b], M, W, H, 402, 400)?.id).toBe(1);
    expect(pickCharacter([a, b], M, W, H, 560, 400)?.id).toBe(2);
    expect(pickCharacter([a, b], M, W, H, 480, 400)).toBeNull(); // between them
    expect(pickCharacter([a, b], M, W, H, 400, 700)).toBeNull(); // below the feet
  });

  it("prefers the nearer of two overlapping bodies", () => {
    const far = target(1, 0, -10);
    const near = target(2, 0, -4);
    expect(pickCharacter([far, near], M, W, H, 400, 420)?.id).toBe(2);
  });

  it("ignores characters behind the camera", () => {
    expect(pickCharacter([target(1, 0, 5)], M, W, H, 400, 400)).toBeNull();
  });
});

describe("proximity", () => {
  const counter = { pos: [3, 0, 23] as [number, number, number], radius: 2 };

  it("holds the counter within its reach plus the slack", () => {
    expect(grantHolds(counter, [3, 0, 23])).toBe(true);
    expect(grantHolds(counter, [5.9, 0, 23])).toBe(true); // 2.9 m: inside 2 + 1
    expect(grantHolds(counter, [6.1, 0, 23])).toBe(false);
    expect(grantHolds(counter, [3, 2, 23])).toBe(false); // another floor
  });

  it("measures trade distance on the ground", () => {
    expect(horizontalDistance([0, 0, 0], { x: 3, y: 9, z: 4 })).toBe(5);
  });
});
