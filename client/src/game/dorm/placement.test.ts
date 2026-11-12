import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import { furnitureTopAreas, initSync, placementFits, type PlaceArea } from "../../wasm/sim_wasm";
import { furnitureDef } from "./catalog";
import {
  FINE_STEP_M,
  GRID_STEP_M,
  YAW_STEP,
  halfExtents,
  itemBox,
  pickPlacement,
  placeOnArea,
  rayHitArea,
  rayHitBox,
  rotateUnits,
  snap,
  orientedBox,
  seatRows,
  toLocal,
  unitsToYaw,
  yawToUnits,
  type FurnitureShape,
  type Ray,
} from "./placement";

// The Roarwing dorm shell's areas (data/world/instances/dorm_roarwing.json).
const floor: PlaceArea = { id: "floor", surface: "floor", min: [-4.4, 0, -4.4], max: [4.4, 0, 4.4] };
const northWall: PlaceArea = { id: "wall_north", surface: "wall", min: [-4.4, 0.3, -4.5], max: [4.4, 4.6, -4.5], facing: 0 };
const westWall: PlaceArea = { id: "wall_west", surface: "wall", min: [-4.5, 0.3, -4.4], max: [-4.5, 4.6, 4.4], facing: Math.PI / 2 };
const areas = [floor, northWall, westWall];

const bed: FurnitureShape = { surface: "floor", footprint: [1.6, 2.3], height: 2.5 };
const desk: FurnitureShape = { surface: "floor", footprint: [1.2, 0.6], height: 0.78, top: 0.76 };
const lantern: FurnitureShape = { surface: "table", footprint: [0.18, 0.18], height: 0.35 };
const banner: FurnitureShape = { surface: "wall", footprint: [0.9, 0.05], height: 1.6 };

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

/** The writing desk's top (catalog 1004) where it stands, as the room offers it (`furnitureTopAreas`). */
const deskTop = (pos: [number, number, number], yawUnits: number): PlaceArea =>
  furnitureTopAreas(furnitureDef(1004)!.raw, "top:7", pos[0], pos[1], pos[2], unitsToYaw(yawUnits))[0]!;

/** A ray from a camera at `from` toward `to`. */
const look = (from: [number, number, number], to: [number, number, number]): Ray => ({
  origin: from,
  dir: [to[0] - from[0], to[1] - from[1], to[2] - from[2]],
});

describe("units and snapping", () => {
  it("quantizes yaw like the wire", () => {
    expect(yawToUnits(0)).toBe(0);
    expect(yawToUnits(Math.PI / 2)).toBe(64);
    expect(yawToUnits(-Math.PI / 2)).toBe(192);
    expect(yawToUnits(Math.PI * 2 - 0.001)).toBe(0);
    expect(unitsToYaw(128)).toBeCloseTo(Math.PI);
  });

  it("rotates in 22.5 degree steps, or single units in fine mode, wrapping", () => {
    expect(YAW_STEP).toBe(16);
    expect(rotateUnits(0, 1, false)).toBe(16);
    expect(rotateUnits(0, -1, false)).toBe(240);
    expect(rotateUnits(250, 1, false)).toBe(10);
    expect(rotateUnits(0, -1, true)).toBe(255);
    let u = 0;
    for (let i = 0; i < 4; i++) u = rotateUnits(u, 1, false);
    expect(u).toBe(64); // four steps make a quarter turn
  });

  it("snaps to the grid in centimetres", () => {
    expect(snap(1.13, GRID_STEP_M)).toBe(1.25);
    expect(snap(-0.37, GRID_STEP_M)).toBe(-0.25);
    expect(snap(1.13, FINE_STEP_M)).toBe(1.15);
    expect(snap(0.1 + 0.2, FINE_STEP_M)).toBe(0.3); // no float dust
  });

  it("swaps a footprint's extents on a quarter turn", () => {
    const [hx, hz] = halfExtents([1.6, 2.3], Math.PI / 2);
    expect(hx).toBeCloseTo(1.15);
    expect(hz).toBeCloseTo(0.8);
    // At 45 degrees the box grows to hold the corners.
    expect(halfExtents([1, 1], Math.PI / 4)[0]).toBeCloseTo(Math.SQRT2 / 2);
  });
});

describe("rayHitArea", () => {
  it("hits the floor from above, not from below", () => {
    const hit = rayHitArea(look([0, 3, 4], [1, 0, 1]), floor, 0);
    expect(hit?.point).toEqual([1, 0, 1]);
    expect(rayHitArea(look([0, -1, 0], [0, 0, 0.5]), floor, 0)).toBeNull();
  });

  it("only hits walls from the room side", () => {
    expect(rayHitArea(look([0, 1.6, 0], [0, 2, -4.5]), northWall, 0)?.point[2]).toBe(-4.5);
    expect(rayHitArea(look([0, 1.6, -6], [0, 2, -4.5]), northWall, 0)).toBeNull();
  });

  it("respects the area's extent plus the margin", () => {
    const ray = look([0, 3, 0], [5, 0, 0]); // 0.6 m past the floor's east edge
    expect(rayHitArea(ray, floor, 0)).toBeNull();
    expect(rayHitArea(ray, floor, 0.75)).not.toBeNull();
  });
});

describe("placeOnArea", () => {
  it("snaps floor items to the grid at the floor's height", () => {
    expect(placeOnArea(desk, floor, [1.13, 0, -0.62], 0, false)).toEqual({ pos: [1.25, 0, -0.5], yawUnits: 0, area: "floor" });
    expect(placeOnArea(desk, floor, [1.13, 0, -0.62], 0, true)?.pos).toEqual([1.15, 0, -0.6]);
  });

  it("pulls an item inside the area, flush with the edge", () => {
    // The bed is 1.6 wide: its centre can come no closer than 0.8 to the wall at x = 4.4.
    expect(placeOnArea(bed, floor, [4.4, 0, 0], 0, false)?.pos).toEqual([3.6, 0, 0]);
    // Turned a quarter, its 2.3 m length lies along X.
    expect(placeOnArea(bed, floor, [4.4, 0, 0], 64, false)?.pos[0]).toBeCloseTo(3.25);
    // An odd size sits flush rather than on the grid.
    const chest: FurnitureShape = { surface: "floor", footprint: [0.9, 0.5], height: 0.55 };
    expect(placeOnArea(chest, floor, [-9, 0, -9], 0, false)?.pos).toEqual([-3.95, 0, -4.15]);
  });

  it("rejects items larger than the area", () => {
    const huge: FurnitureShape = { surface: "floor", footprint: [10, 1], height: 1 };
    expect(placeOnArea(huge, floor, [0, 0, 0], 0, false)).toBeNull();
  });

  it("hangs wall items on the wall plane facing into the room", () => {
    const p = placeOnArea(banner, northWall, [1.1, 2.4, -4.5], 99, false);
    // Bottom edge at the cursor minus half its height, snapped: 2.4 - 0.8 = 1.6 -> 1.5.
    expect(p).toEqual({ pos: [1, 1.5, -4.5], yawUnits: 0, area: "wall_north" });
    const w = placeOnArea(banner, westWall, [-4.5, 9, 0.3], 0, false);
    expect(w?.pos).toEqual([-4.5, 3, 0.25]); // clamped below the top: 4.6 - 1.6
    expect(w?.yawUnits).toBe(64);
  });
});

describe("pickPlacement", () => {
  it("puts floor items where the cursor meets the floor", () => {
    expect(pickPlacement(look([0, 4, 4], [-2, 0, -1]), areas, desk, 0, false)?.pos).toEqual([-2, 0, -1]);
  });

  it("ignores walls for floor items and floors for wall items", () => {
    const atWall = look([0, 1.6, 0], [0, 2.5, -4.5]);
    expect(pickPlacement(atWall, areas, desk, 0, false)).toBeNull();
    expect(pickPlacement(atWall, areas, banner, 0, false)?.area).toBe("wall_north");
    expect(pickPlacement(look([0, 3, 2], [0, 0, 0]), areas, banner, 0, false)).toBeNull();
  });

  it("stands table items on a desk top when the cursor is on it, else on the floor", () => {
    const top = deskTop([1, 0, 1], 0);
    expect(top.min[1]).toBeCloseTo(0.76);
    const all = [...areas, top];
    const onDesk = pickPlacement(look([1, 3, 3], [1.2, 0.76, 1.1]), all, lantern, 0, false);
    expect(onDesk?.area).toBe("top:7");
    expect(onDesk?.pos[1]).toBe(0.76);
    const beside = pickPlacement(look([1, 3, 3], [-1, 0, 2]), all, lantern, 0, false);
    expect(beside?.area).toBe("floor");
  });

  it("puts a lamp on the desk when the cursor is on the desk's front, not on the floor behind it", () => {
    const all = [...areas, deskTop([1, 0, 1], 0)];
    // Low camera, aimed at the drawers 0.3 m below the top on the desk's front face (z = 1.3).
    const p = pickPlacement(look([1, 0.9, 4], [1.1, 0.46, 1.3]), all, lantern, 0, false);
    expect(p?.area).toBe("top:7");
    expect(p?.pos[1]).toBe(0.76);
    // Aimed under the catch (at the floor in front of the desk): the floor.
    expect(pickPlacement(look([1, 0.9, 4], [1.1, 0, 1.6]), all, lantern, 0, false)?.area).toBe("floor");
  });

  it("uses a turned piece's top in its own axes (any yaw, not only quarter turns)", () => {
    const yawUnits = 16; // 22.5 degrees
    const top = deskTop([0, 0, 0], yawUnits);
    const p = pickPlacement(look([0, 3, 3], [0.5, 0.76, 0]), [floor, top], lantern, 0, false);
    expect(p?.area).toBe("top:7");
    // The placement is on the desk where the server checks it: inside its top in the desk's axes.
    const local = toLocal(top.frame!, p!.pos);
    expect(Math.abs(local[0])).toBeLessThanOrEqual(0.6 - 0.09 + 0.01);
    expect(Math.abs(local[2])).toBeLessThanOrEqual(0.3 + 0.01);
    expect(placementFits([top], "table", 0.18, 0.18, 0.35, p!.pos[0], p!.pos[1], p!.pos[2], unitsToYaw(p!.yawUnits))).toBe(true);
  });

  it("puts the bed flush with the diagonal wall its floor area is turned to", () => {
    // The Roarwing room's bed wall (dorm_roarwing.json `floor_bed_wall`) and its starter bed spot.
    const yaw = unitsToYaw(43);
    const bedWall: PlaceArea = { id: "floor_bed_wall", surface: "floor", min: [-1.24, 0, 0.07], max: [1.24, 0, 2.67], frame: { origin: [-4.33, 0, -2.5], yaw } };
    const room = [{ ...floor, min: [-3.6, 0, -2.6], max: [3.0, 0, 3.4] } as PlaceArea, bedWall];
    // Pointing where the room's floor and the wall's area overlap: the wall's area, square to the wall.
    expect(pickPlacement(look([0, 3, 0], [-2.6, 0, -1.5]), room, bed, 43, false)?.area).toBe("floor_bed_wall");
    // Pointing by the wall: the bed's head against it (snapped in the wall's axes, then pulled inside).
    const p = pickPlacement(look([0, 3, 0], [-3.9, 0, -2.25]), room, bed, 43, false);
    expect(p?.area).toBe("floor_bed_wall");
    const local = toLocal(bedWall.frame!, p!.pos);
    expect(local[2]).toBeCloseTo(0.07 + 1.15, 1);
    expect(Math.abs(local[0])).toBeLessThan(0.3);
    expect(placementFits(room, "floor", 1.6, 2.3, 2.5, p!.pos[0], p!.pos[1], p!.pos[2], unitsToYaw(p!.yawUnits))).toBe(true);
    expect(placementFits(room, "floor", 1.6, 2.3, 2.5, -3.26, 0, -1.89, yaw)).toBe(true); // the starter spot
    // A piece too big for the wall's area goes on the room's floor instead.
    const rug: FurnitureShape = { surface: "floor", footprint: [3, 3], height: 0.02 };
    expect(pickPlacement(look([0, 3, 0], [-2.6, 0, -1.5]), room, rug, 0, false)?.area).toBe("floor");
  });

  it("finds a bookcase's free shelf end, and its headroom keeps tall pieces off it", () => {
    const tops = furnitureTopAreas(furnitureDef(1008)!.raw, "top:9", 0, 0, 0, 0);
    expect(tops.map((t) => t.id)).toEqual(["top:9", "top:9.shelf0", "top:9.shelf1"]);
    // Looking into the lower shelf's free end from the front.
    const p = pickPlacement(look([0.37, 0.8, 2], [0.37, 0.6, 0.2]), [floor, ...tops], lantern, 0, false);
    expect(p?.area).toBe("top:9.shelf0");
    expect(p?.pos[1]).toBe(0.53); // 0.525 to the centimetre, as the wire carries it
    expect(placementFits(tops, "table", 0.18, 0.18, 0.35, p!.pos[0], 0.525, p!.pos[2], 0)).toBe(true);
    // The owl cage (0.55 m) is taller than the shelf's 0.39 m headroom.
    expect(placementFits(tops, "table", 0.35, 0.35, 0.55, 0.37, 0.525, 0, 0)).toBe(false);
  });
});

describe("the turned preview and seats", () => {
  it("turns the ghost's box with the piece", () => {
    const at0 = orientedBox(desk, [1, 0, 1], 0);
    expect(at0).toMatchObject({ centre: [1, 0.39, 1], size: [1.2, 0.78, 0.6], yaw: 0 });
    const turned = orientedBox(desk, [1, 0, 1], 16);
    expect(turned.yaw).toBeCloseTo(Math.PI / 8);
    expect(turned.size).toEqual(at0.size); // the box turns; it does not grow
    // A wall item's box stands out from the wall along its facing.
    const b = orientedBox(banner, [-4.5, 1, 0], 64);
    expect(b.centre[0]).toBeCloseTo(-4.475);
  });

  it("gives a turned piece's seats as rows the sitter stays on", () => {
    const seats = [{ y: 0.46, area: [-0.23, -0.21, 0.23, 0.25] as [number, number, number, number], front: 0.23, back: 0.204, drop: 0.46 }];
    const rows = seatRows(seats, [2, 0, 2], 64);
    expect(rows.length).toBe(9); // castle/seats.ts rows: [x0, z0, x1, z1, y, yaw, front, back, drop]
    expect(rows[4]).toBeCloseTo(0.46);
    expect(rows[5]).toBeCloseTo(Math.PI / 2);
    expect(rows.slice(6)).toEqual([0.23, 0.204, 0.46]);
    // A long seat (a bed's side) is cut into short rows, each fitted as the whole side.
    const side = [{ y: 0.68, area: [0.35, -0.55, 0.72, 0.95] as [number, number, number, number], yaw: Math.PI / 2, front: 0.225, back: 1.267, drop: 0.68 }];
    const sideRows = seatRows(side, [0, 0, 0], 0);
    expect(sideRows.length).toBe(9 * 4);
    for (let i = 0; i < 4; i++) {
      const r = sideRows.slice(i * 9, i * 9 + 9);
      expect((r[0]! + r[2]!) / 2).toBeCloseTo(0.535); // the side's centre line along its facing (+x)
      expect(r.slice(6)).toEqual([0.225, 1.267, 0.68]);
    }
  });
});

describe("picking placed items", () => {
  it("finds an item's box along a ray", () => {
    const box = itemBox(desk, [1, 0, 1], 0);
    expect(box.min).toEqual([0.4, 0, 0.7]);
    expect(box.max[1]).toBeCloseTo(0.78);
    expect(rayHitBox(look([1, 3, 4], [1, 0, 1]), box)).not.toBeNull();
    expect(rayHitBox(look([3, 3, 4], [3, 0, 1]), box)).toBeNull();
  });

  it("puts a wall item's box in front of the wall", () => {
    const box = itemBox(banner, [0, 1, -4.5], 0); // facing +Z, into the room
    expect((box.min[2] + box.max[2]) / 2).toBeCloseTo(-4.475); // half its 5 cm depth out
    expect(box.max[2] - box.min[2]).toBeCloseTo(0.1); // grown so the cursor can find it
  });
});
