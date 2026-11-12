import { describe, expect, it } from "vitest";
import { forwardOf, inputDirection, turnToward, yawOf } from "./motion";

describe("yaw convention", () => {
  it("yaw 0 faces +Z and π/2 faces +X", () => {
    const [x0, z0] = forwardOf(0);
    expect(x0).toBeCloseTo(0);
    expect(z0).toBeCloseTo(1);
    const [x1, z1] = forwardOf(Math.PI / 2);
    expect(x1).toBeCloseTo(1);
    expect(z1).toBeCloseTo(0);
  });

  it("yawOf inverts forwardOf", () => {
    for (const yaw of [0, 0.5, 2, 3.5, 6]) {
      const [dx, dz] = forwardOf(yaw);
      expect(yawOf(dx, dz)).toBeCloseTo(yaw);
    }
  });
});

describe("inputDirection", () => {
  it("is null without input", () => {
    expect(inputDirection(0, 0, 0, -1)).toBeNull();
  });

  it("moves along the camera's view and strafes to its right", () => {
    // Camera looking north (-Z): right is east (+X) in a right-handed, Y-up world.
    expect(inputDirection(1, 0, 0, -1)).toEqual([0, -1]);
    const right = inputDirection(0, 1, 0, -1)!;
    expect(right[0]).toBeCloseTo(1);
    expect(right[1]).toBeCloseTo(0);
  });

  it("normalises diagonals so they are not faster", () => {
    const [dx, dz] = inputDirection(1, 1, 3, 4)!;
    expect(Math.hypot(dx, dz)).toBeCloseTo(1);
  });
});

describe("turnToward", () => {
  it("turns at most maxStep, the short way round", () => {
    expect(turnToward(0, 1, 0.25)).toBeCloseTo(0.25);
    expect(turnToward(0.1, 2 * Math.PI - 0.1, 1)).toBeCloseTo(2 * Math.PI - 0.1);
    expect(turnToward(0.1, 2 * Math.PI - 0.5, 0.2)).toBeCloseTo(2 * Math.PI - 0.1);
  });
});
