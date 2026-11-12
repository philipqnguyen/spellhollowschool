// Sitting on the common rooms' rugs and hearth steps (castle/seats.ts
// `Seats.floorFromCells`, the manifest's `floor_seats` rows from castle M5b).

import { describe, expect, it, vi } from "vitest";
import { FLOOR_SEAT_INSET, Seats, seatPlace, type SeatPlace } from "./seats";

// Common room B as exported: three rugs on the floor (y -8.98) and the hearth step (0.28 m up).
const CELLS = [
  {
    id: "common_room_house_b",
    seats: [[70, 10, 71, 10.6, -8.5, 0]],
    floor_seats: [
      [70.6, 17.6, 74.4, 21.4, -8.98],
      [75.6, 22.2, 79.4, 25.0, -8.98],
      [86.75, 17.25, 87.45, 21.75, -8.7],
    ],
  },
  { id: "great_hall" },
];

const place = (): SeatPlace => seatPlace();

describe("floor seats", () => {
  it("reads the manifest's five-number rows, apart from the benches and chairs", () => {
    expect(Seats.floorFromCells(CELLS).count).toBe(3);
    expect(Seats.fromCells(CELLS).count).toBe(1);
  });

  it("seats a sitter on a rug where they stand, keeping their facing (yaw NaN)", () => {
    const seats = Seats.floorFromCells(CELLS);
    const out = place();
    expect(seats.find(72, -9, 19, out)).toBe(true);
    expect(out).toMatchObject({ x: 72, y: -8.98, z: 19 });
    expect(Number.isNaN(out.yaw)).toBe(true);
    // Near the rug's edge the folded legs are kept on it.
    expect(seats.find(70.7, -9, 17.7, out)).toBe(true);
    expect(out.x).toBeCloseTo(70.6 + FLOOR_SEAT_INSET, 6);
    expect(out.z).toBeCloseTo(17.6 + FLOOR_SEAT_INSET, 6);
  });

  it("seats a sitter standing in front of the hearth on its step, inside its depth", () => {
    const seats = Seats.floorFromCells(CELLS);
    const out = place();
    expect(seats.find(86.3, -8.98, 19, out)).toBe(true);
    expect(out.y).toBe(-8.7);
    expect(out.x).toBeCloseTo(86.75 + FLOOR_SEAT_INSET, 6);
    // Too far from any floor seat, or on another storey: none.
    expect(seats.find(82, -8.98, 19, out)).toBe(false);
    expect(seats.find(72, -4, 19, out)).toBe(false);
  });

  it("skips malformed rows with one warning per cell", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const seats = Seats.floorFromCells([{ id: "x", floor_seats: [[1, 2, 3, 4], [0, 0, 1, 1, Number.NaN], [0, 0, 2, 2, 0]] }, { id: "y", floor_seats: "no" }]);
    expect(seats.count).toBe(1);
    expect(warn).toHaveBeenCalledTimes(2);
    warn.mockRestore();
  });
});
