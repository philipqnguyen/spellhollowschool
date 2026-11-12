import { describe, expect, it } from "vitest";
import { BENCH_TAP_MS, BENCH_TURN_S, BenchTap, isOpposite, SeatTurns } from "./bench-flip";
import { Seats, seatPlace, wrapAngle } from "./castle/seats";
import { kneeLift } from "./characters/seated";

const N: [number, number] = [0, 1]; // walking +z (yaw 0)
const S: [number, number] = [0, -1];
const E: [number, number] = [1, 0];
const seatedN = { yaw: 0, twoSided: true };

describe("isOpposite", () => {
  it("counts directions more than 120 degrees from the facing", () => {
    expect(isOpposite(S, 0)).toBe(true);
    expect(isOpposite(N, 0)).toBe(false);
    expect(isOpposite(E, 0)).toBe(false);
    const at = (deg: number): [number, number] => [Math.sin((deg * Math.PI) / 180), Math.cos((deg * Math.PI) / 180)];
    expect(isOpposite(at(125), 0)).toBe(true);
    expect(isOpposite(at(115), 0)).toBe(false);
    expect(isOpposite(at(-125), 0)).toBe(true);
    expect(isOpposite(N, Math.PI)).toBe(true);
  });
});

describe("BenchTap", () => {
  it("turns round on a quick tap of the way behind", () => {
    const t = new BenchTap();
    expect(t.step(0, S, seatedN, false)).toBe("wait");
    expect(t.step(80, S, seatedN, false)).toBe("wait");
    expect(t.step(150, null, seatedN, false)).toBe("flip");
    expect(t.step(170, null, seatedN, false)).toBe("move");
  });

  it("stands up once the press passes the threshold, and keeps walking", () => {
    const t = new BenchTap();
    expect(t.step(0, S, seatedN, false)).toBe("wait");
    expect(t.step(BENCH_TAP_MS - 1, S, seatedN, false)).toBe("wait");
    expect(t.step(BENCH_TAP_MS, S, seatedN, false)).toBe("move");
    // Up and walking: no longer seated, the key still held.
    expect(t.step(BENCH_TAP_MS + 16, S, null, false)).toBe("move");
    expect(t.step(BENCH_TAP_MS + 400, null, null, false)).toBe("move");
  });

  it("lets any other press through at once", () => {
    const t = new BenchTap();
    expect(t.step(0, N, seatedN, false)).toBe("move"); // forward
    expect(t.step(16, null, null, false)).toBe("move");
    expect(t.step(32, E, seatedN, false)).toBe("move"); // sideways
    expect(t.step(48, null, null, false)).toBe("move");
    expect(t.step(96, S, null, false)).toBe("move"); // standing
    expect(t.step(112, null, null, false)).toBe("move");
    expect(t.step(128, S, seatedN, true)).toBe("move"); // auto run
  });

  it("on a one-way seat a tap behind does nothing and a hold stands up", () => {
    const t = new BenchTap();
    const oneWay = { yaw: 0, twoSided: false };
    expect(t.step(0, S, oneWay, false)).toBe("wait");
    expect(t.step(100, null, oneWay, false)).toBe("move"); // no keys: nothing happens
    expect(t.step(300, S, oneWay, false)).toBe("wait");
    expect(t.step(300 + BENCH_TAP_MS, S, oneWay, false)).toBe("move");
    expect(t.step(600, N, oneWay, false)).toBe("move");
  });

  it("a held key that started as a move never turns into a tap", () => {
    const t = new BenchTap();
    expect(t.step(0, N, seatedN, false)).toBe("move");
    // Still held, the camera turned so it now points behind: still a move.
    expect(t.step(16, S, seatedN, false)).toBe("move");
    expect(t.step(32, null, null, false)).toBe("move");
  });

  it("stops waiting as soon as the direction is no longer behind", () => {
    const t = new BenchTap();
    expect(t.step(0, S, seatedN, false)).toBe("wait");
    expect(t.step(50, E, seatedN, false)).toBe("move");
    expect(t.step(80, null, null, false)).toBe("move");
  });
});

describe("two-sided seats", () => {
  // Two-sided, its back over a drop: the legs facing that way hang 1.2 m over the floor there.
  const bench = [0, -0.2, 2, 0.2, 0.45, 0, 0.2, 0.25, 0.45, 1.2];
  const oneWay = [0, 9.8, 2, 10.2, 0.45, 0, 0.2, 0.25, 0.45, 0];

  it("draw a two-sided seat on the side nearest the sitter's facing", () => {
    const seats = Seats.fromCells([{ id: "yard", seats: [bench, oneWay] }]);
    const out = seatPlace();
    expect(seats.find(1, 0, 0.5, out, 0.3)).toBe(true);
    expect(out.yaw).toBeCloseTo(0);
    expect(out.twoSided).toBe(true);
    expect([out.ahead, out.behind, out.drop]).toEqual([0.2, 0.25, 0.45]);
    expect(out.floor).toBeCloseTo(0);
    expect(seats.find(1, 0, 0.5, out, Math.PI - 0.3)).toBe(true);
    expect(out.yaw).toBeCloseTo(Math.PI);
    // Facing the drop: the legs fit to the floor there; the seat's own floor stays (the shadow).
    expect([out.ahead, out.behind, out.drop]).toEqual([0.25, 0.2, 1.2]);
    expect(out.floor).toBeCloseTo(0);
    // No facing: the seat's own way.
    expect(seats.find(1, 0, 0.5, out)).toBe(true);
    expect(out.yaw).toBeCloseTo(0);
  });

  it("keep a one-way seat's facing whatever the sitter's", () => {
    const seats = Seats.fromCells([{ id: "yard", seats: [bench, oneWay] }]);
    const out = seatPlace();
    expect(seats.find(1, 0, 10.5, out, Math.PI)).toBe(true);
    expect(out.yaw).toBeCloseTo(0);
    expect(out.twoSided).toBe(false);
    expect(out.seat).toBe(1);
  });

  it("read nine-number rows (and dorm rows) as one-way, and refuse a bad flip drop", () => {
    const seats = Seats.fromCells([{ id: "yard", seats: [bench.slice(0, 9), [...bench.slice(0, 9), -1]] }]);
    expect(seats.count).toBe(1);
    const out = seatPlace();
    expect(seats.find(1, 0, 0.5, out, Math.PI)).toBe(true);
    expect(out.yaw).toBeCloseTo(0);
    const dorm = Seats.fromRows([...bench.slice(0, 9), ...oneWay.slice(0, 9)]);
    expect(dorm.count).toBe(2);
    expect(dorm.find(1, 0, 10.5, out, Math.PI)).toBe(true);
    expect(out.twoSided).toBe(false);
    expect(out.ahead).toBe(0.2);
  });

  it("read a room seat's two_sided", () => {
    const seats = Seats.fromRoomSeats([
      { pos: [0, 0, 0], yaw: 0, front: 0.2, back: 0.2, drop: 0.45, two_sided: true, flip_drop: 0.9 },
      { pos: [5, 0, 0], yaw: 0 },
      { pos: [9, 0, 0], yaw: 0, front: 0.2, back: 0.2, drop: 0.45, two_sided: true },
    ])!;
    const out = seatPlace();
    expect(seats.find(0, 0, 0, out, Math.PI)).toBe(true);
    expect(out.yaw).toBeCloseTo(Math.PI);
    expect(out.drop).toBe(0.9);
    expect(seats.find(9, 0, 0, out, Math.PI)).toBe(true);
    expect(out.drop).toBe(0.45); // no flip_drop: the seat's own
    expect(seats.find(5, 0, 0, out, Math.PI)).toBe(true);
    expect(out.yaw).toBeCloseTo(0);
  });
});

describe("legs over a drop", () => {
  it("hang: the knees rise only for a floor the feet would go through", () => {
    const legs = { calfBack: 0.1, back: 0.15, heelDrop: 0.52, thigh: 0.45 };
    const bench = [0, -0.2, 2, 0.2, 0.45, 0, 0.2, 0.2, 0.45, 2];
    const seats = Seats.fromCells([{ id: "stand", seats: [bench] }]);
    const out = seatPlace();
    seats.find(1, 0, 0.5, out, 0);
    expect(kneeLift(legs, 1, out.drop)).toBeGreaterThan(0); // the floor in front, 0.45 down: knees up
    seats.find(1, 0, 0.5, out, Math.PI);
    expect(out.drop).toBe(2);
    expect(kneeLift(legs, 1, out.drop)).toBe(0); // over the drop: they hang
  });
});

describe("SeatTurns", () => {
  it("eases a turn on the same seat over BENCH_TURN_S, knees tucked mid-turn", () => {
    const turns = new SeatTurns();
    const place = seatPlace();
    place.seat = 3;
    place.yaw = 0;
    turns.begin();
    expect(turns.draw(7, place, 1 / 60)).toBe(0);
    expect(place.yaw).toBe(0);
    // The side changes: the drawn yaw starts where it was.
    turns.begin();
    place.yaw = Math.PI;
    expect(turns.draw(7, place, 1 / 60)).toBe(0);
    expect(place.yaw).toBeCloseTo(0);
    expect(turns.turning(7)).toBe(true);
    let tuckMid = 0;
    let yawMid = 0;
    for (let t = 0; t < BENCH_TURN_S / 2 - 1e-9; t += 0.05) {
      turns.begin();
      place.yaw = Math.PI;
      tuckMid = turns.draw(7, place, 0.05);
      yawMid = place.yaw;
    }
    expect(tuckMid).toBeGreaterThan(0.9);
    expect(Math.abs(wrapAngle(yawMid))).toBeCloseTo(Math.PI / 2, 1);
    for (let t = 0; t < BENCH_TURN_S; t += 0.05) {
      turns.begin();
      place.yaw = Math.PI;
      turns.draw(7, place, 0.05);
    }
    expect(place.yaw).toBeCloseTo(Math.PI);
    expect(turns.turning(7)).toBe(false);
  });

  it("does not turn a sitter onto another seat, and forgets those not drawn", () => {
    const turns = new SeatTurns();
    const place = seatPlace();
    place.seat = 1;
    place.yaw = 0;
    turns.begin();
    turns.draw(7, place, 0.016);
    place.seat = 2;
    place.yaw = Math.PI;
    turns.begin();
    expect(turns.draw(7, place, 0.016)).toBe(0);
    expect(place.yaw).toBeCloseTo(Math.PI);
    turns.begin();
    turns.begin();
    place.seat = 2;
    place.yaw = 0;
    turns.draw(7, place, 0.016);
    expect(turns.turning(7)).toBe(false);
  });
});
