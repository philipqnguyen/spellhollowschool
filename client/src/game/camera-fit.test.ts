import { describe, expect, it } from "vitest";
import { betaAboveFloor, clampLift, COMFORT_DISTANCE, FLIGHT_DISTANCE, FLIGHT_FLOOR_CLEARANCE, liftFor, MAX_LIFT, TIGHT_DISTANCE } from "./camera-fit";

describe("liftFor", () => {
  it("rises only when the room behind is short, and smoothly", () => {
    expect(liftFor(10)).toBe(0);
    expect(liftFor(COMFORT_DISTANCE)).toBe(0);
    expect(liftFor(TIGHT_DISTANCE)).toBeCloseTo(MAX_LIFT);
    expect(liftFor(0.2)).toBeCloseTo(MAX_LIFT);
    const mid = liftFor((COMFORT_DISTANCE + TIGHT_DISTANCE) / 2);
    expect(mid).toBeCloseTo(MAX_LIFT / 2);
    expect(liftFor(1.2)).toBeGreaterThan(liftFor(1.8));
    expect(liftFor(Number.NaN)).toBe(0);
  });
});

describe("clampLift", () => {
  it("never lifts past the camera's highest pitch", () => {
    expect(clampLift(0.5, 1.2, 0.35)).toBe(0.5);
    expect(clampLift(0.5, 0.6, 0.35)).toBeCloseTo(0.25);
    expect(clampLift(0.5, 0.3, 0.35)).toBe(0);
  });
});

describe("betaAboveFloor (the flight camera)", () => {
  it("leaves a camera already over the floor alone", () => {
    expect(betaAboveFloor(10, 9, 1.2, 0, FLIGHT_FLOOR_CLEARANCE, 0.35)).toBe(1.2);
  });

  it("raises a camera that would sit in the ground until it clears it by the clearance", () => {
    // A target 0.6 m over the floor with the camera looking from below the horizon.
    const beta = betaAboveFloor(0.6, FLIGHT_DISTANCE, 1.6, 0, FLIGHT_FLOOR_CLEARANCE, 0.35);
    expect(beta).toBeLessThan(1.6);
    expect(0.6 + FLIGHT_DISTANCE * Math.cos(beta)).toBeCloseTo(FLIGHT_FLOOR_CLEARANCE, 6);
  });

  it("goes no higher than the camera's own limit, and ignores a floor it cannot know", () => {
    expect(betaAboveFloor(0, 9, 1.4, 20, 0.8, 0.35)).toBe(0.35);
    expect(betaAboveFloor(1, 9, 1.4, Number.NaN, 0.8, 0.35)).toBe(1.4);
    expect(betaAboveFloor(1, 0, 1.4, 5, 0.8, 0.35)).toBe(1.4);
  });
});
