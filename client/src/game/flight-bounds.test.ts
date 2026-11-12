import { describe, expect, it } from "vitest";
import castleLayout from "../../../data/world/layout.json";
import pitchLayout from "../../../data/world/instances/class_pitch.json";
import { BOUNDS_MARGIN, FlightBounds } from "./flight-bounds";
import { FLIGHT_DISTANCE } from "./camera-fit";

/** The camera's shoulders over a broom at (x, y, z) (camera.ts TARGET_HEIGHT). */
const SHOULDERS = 1.45;

describe("the flight camera's bounds", () => {
  const castle = FlightBounds.fromAreas((castleLayout as { areas?: unknown }).areas)!;
  const pitch = FlightBounds.fromAreas((pitchLayout as { areas?: unknown }).areas)!;

  it("are read from a layout's airspace and no-fly boxes (none without airspace)", () => {
    expect(castle).not.toBeNull();
    expect(pitch).not.toBeNull();
    expect(FlightBounds.fromAreas([{ id: "x", kind: "solid", min: [0, 0, 0], max: [1, 1, 1] }])).toBeNull();
    expect(FlightBounds.fromAreas(undefined)).toBeNull();
  });

  it("keep the camera out of the castle's outer walls when hovering at its edge", () => {
    // The broom stops 0.4 m off the castle's no-fly box (z 100); the camera behind it looks south from the castle side.
    const y = 8 + SHOULDERS;
    const room = castle.room(0, y, 100.4, 0, 0.3, -Math.sqrt(1 - 0.09), FLIGHT_DISTANCE);
    expect(room).toBeLessThan(0.5);
    // Where it stops is outside the castle's box by the margin.
    expect(castle.allows(0, y + 0.3 * room, 100.4 - Math.sqrt(1 - 0.09) * room)).toBe(true);
    // Looking north from the castle's side (the camera out over the grounds), nothing stops it.
    expect(castle.room(0, y, 100.4, 0, 0.3, Math.sqrt(1 - 0.09), FLIGHT_DISTANCE)).toBeCloseTo(FLIGHT_DISTANCE, 5);
    // And the other no-fly boxes too: the Owl Loft from its east side.
    const owl = castle.room(-71.5, 10, 124, -1, 0, 0, FLIGHT_DISTANCE);
    expect(owl).toBeLessThanOrEqual(0.5 - BOUNDS_MARGIN + 0.01);
  });

  it("keep the camera inside the Flying class field (its walls and towers stand outside the airspace)", () => {
    // A broom near the field's east end (airspace to x 29, the wall at 30.5, the towers beyond), the camera east of it.
    const room = pitch.room(27, 5 + SHOULDERS, 0, 1, 0, 0, FLIGHT_DISTANCE);
    expect(27 + room).toBeLessThanOrEqual(29);
    expect(room).toBeGreaterThan(1.5);
    // The south wall the same way, and a diagonal into the corner tower.
    expect(pitch.room(0, 6, 17, 0, 0, 1, FLIGHT_DISTANCE)).toBeLessThanOrEqual(2);
    const d = Math.SQRT1_2;
    const corner = pitch.room(26, 6, 16, d, 0, d, FLIGHT_DISTANCE);
    expect(26 + corner * d).toBeLessThanOrEqual(29);
    expect(16 + corner * d).toBeLessThanOrEqual(19);
    // In the middle of the field the camera sits at its full distance.
    expect(pitch.room(0, 6, 0, 0, 0.3, Math.sqrt(1 - 0.09), FLIGHT_DISTANCE)).toBeCloseTo(FLIGHT_DISTANCE, 5);
  });

  it("never pin the camera to a broom that starts outside them", () => {
    // A start inside a no-fly margin (a broom that slid to an edge): the march finds the first free point.
    expect(castle.room(0, 9, 100.1, 0, 0, 1, FLIGHT_DISTANCE)).toBeGreaterThan(5);
  });
});
