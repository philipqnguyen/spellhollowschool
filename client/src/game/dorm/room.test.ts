import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import { seatPlace } from "../castle/seats";
import { initSync, type PlacedFurniture } from "../../wasm/sim_wasm";
import { furnitureDef } from "./catalog";
import { DormRoom } from "./room";
import { FurnitureSeats } from "./seats";

// Catalog ids (data/items/catalog.json): 1001 bed, 1004 writing desk (top 0.76),
// 1005 wooden chair, 1008 oak bookcase (top 2.0, shelves 0.525 and 1.365), 1014 owl cage, 1015 lantern.
const desk: PlacedFurniture = { itemId: 7, itemDef: 1004, dyes: [10, 20, 21], pos: [1, 0, 1], yaw: 0 };
const bed: PlacedFurniture = { itemId: 8, itemDef: 1001, dyes: [10, 16, 21], pos: [-2, 0, -2], yaw: Math.PI / 2 };

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

describe("the catalog", () => {
  it("has the starter furniture with shapes, tops and seats", () => {
    expect(furnitureDef(1004)).toMatchObject({ surface: "floor", footprint: [1.2, 0.6], top: 0.76, hasTops: true, category: "tables" });
    expect(furnitureDef(1009)?.surface).toBe("wall");
    expect(furnitureDef(101)).toBeUndefined(); // a hat
    expect(furnitureDef(1008)).toMatchObject({ hasTops: true, category: "storage" }); // the bookcase: top and shelves
    expect(furnitureDef(1005)?.seats.length).toBe(1);
    expect(furnitureDef(1001)?.seats.length).toBe(2); // a bed's two sides
    expect(furnitureDef(1015)?.category).toBe("lighting");
    expect(furnitureDef(1014)?.category).toBe("decor");
  });
});

describe("DormRoom", () => {
  it("loads DormState and applies edits", () => {
    const room = new DormRoom();
    room.reset({ openToFriends: true, cap: 150, items: [desk] });
    expect(room.count).toBe(1);
    expect(room.openToFriends).toBe(true);

    expect(room.apply({ kind: "placed", item: bed }).kind).toBe("placed");
    expect(room.items.get(8)?.yawUnits).toBe(64);
    const moved = room.apply({ kind: "moved", itemId: 7, pos: [2, 0, 2], yaw: Math.PI });
    expect(moved.kind === "moved" && moved.item).toMatchObject({ itemDef: 1004, pos: [2, 0, 2], yawUnits: 128 });
    const removed = room.apply({ kind: "removed", itemId: 8 });
    expect(removed.kind === "removed" && removed.item.itemDef).toBe(1001);
    expect(room.count).toBe(1);
    // Edits for unknown items change nothing.
    expect(room.apply({ kind: "removed", itemId: 99 }).kind).toBe("none");
  });

  it("knows when the cap is reached", () => {
    const room = new DormRoom();
    room.reset({ openToFriends: false, cap: 2, items: [desk] });
    expect(room.full).toBe(false);
    room.apply({ kind: "placed", item: bed });
    expect(room.full).toBe(true);
  });

  it("offers the tops and shelves of placed pieces, turned with them, except the one being moved", () => {
    const room = new DormRoom();
    const turned: PlacedFurniture = { ...desk, yaw: Math.PI / 8 }; // one 22.5 degree step
    const bookcase: PlacedFurniture = { itemId: 9, itemDef: 1008, dyes: [10, 18, 21], pos: [3, 0, -3], yaw: 0 };
    room.reset({ openToFriends: false, cap: 150, items: [turned, bed, bookcase] });
    expect(room.tableTops().map((a) => a.id)).toEqual(["top:7", "top:9", "top:9.shelf0", "top:9.shelf1"]); // the bed has no top
    expect(room.tableTops(7).map((a) => a.id)).toEqual(["top:9", "top:9.shelf0", "top:9.shelf1"]);
    const top = room.tableTops()[0]!;
    expect(top.frame?.yaw).toBeCloseTo(Math.PI / 8); // the desk's top turns with it
    expect(top.min[1]).toBeCloseTo(0.76);
    expect(room.tableTops()[2]!.clear).toBeCloseTo(0.39);
  });

  it("knows what stands on a piece (it may not move until that is taken off)", () => {
    const room = new DormRoom();
    const bookcase: PlacedFurniture = { itemId: 9, itemDef: 1008, dyes: [10, 18, 21], pos: [3, 0, -3], yaw: 0 };
    const onDesk: PlacedFurniture = { itemId: 20, itemDef: 1015, dyes: [1, 1, 1], pos: [1.3, 0.76, 1], yaw: 0 };
    const onShelf: PlacedFurniture = { itemId: 21, itemDef: 1015, dyes: [1, 1, 1], pos: [3.37, 0.525, -3], yaw: 0 };
    const onFloor: PlacedFurniture = { itemId: 22, itemDef: 1014, dyes: [1, 1, 1], pos: [0, 0, 3], yaw: 0 };
    room.reset({ openToFriends: false, cap: 150, items: [desk, bookcase, onFloor] });
    expect(room.heldBy(7)).toEqual([]);
    room.apply({ kind: "placed", item: onDesk });
    room.apply({ kind: "placed", item: onShelf });
    expect(room.heldBy(7).map((i) => i.itemId)).toEqual([20]);
    expect(room.heldBy(9).map((i) => i.itemId)).toEqual([21]);
    expect(room.heldBy(20)).toEqual([]); // nothing stands on a lantern
    room.apply({ kind: "moved", itemId: 20, pos: [0, 0, 0], yaw: 0 });
    expect(room.heldBy(7)).toEqual([]);
  });

  it("picks the nearest item along a ray", () => {
    const room = new DormRoom();
    room.reset({ openToFriends: false, cap: 150, items: [desk, bed] });
    expect(room.pick({ origin: [1, 3, 4], dir: [0, -3, -3] })?.itemId).toBe(7);
    expect(room.pick({ origin: [0, 3, 4], dir: [0, 1, 0] })).toBeNull();
  });
});

describe("sitting on placed furniture", () => {
  it("draws a sitter on a placed chair's seat, facing the way the chair does", () => {
    const room = new DormRoom();
    const chair: PlacedFurniture = { itemId: 30, itemDef: 1005, dyes: [10, 16, 21], pos: [2, 0, 2], yaw: Math.PI / 2 };
    room.reset({ openToFriends: false, cap: 150, items: [chair, desk] });
    const seats = new FurnitureSeats(room);
    expect(seats.count).toBe(1);
    const out = seatPlace();
    expect(seats.find(2.1, 0, 2.2, out)).toBe(true);
    expect(out.y).toBeCloseTo(0.46);
    expect(out.yaw).toBeCloseTo(Math.PI / 2);
    expect(Math.hypot(out.x - 2, out.z - 2)).toBeLessThan(0.1);
    // Fitted like the castle's chairs: on the seat's centre line (0.02 m ahead
    // of the chair's middle, turned to +x), with how far the seat reaches.
    expect(out.x).toBeCloseTo(2.02);
    expect(out.z).toBeCloseTo(2);
    expect([out.ahead, out.behind, out.drop]).toEqual([0.23, 0.204, 0.46]);
    // Far from it: no seat (the room's own seats, then the floor sit).
    expect(seats.find(-3, 0, -3, out)).toBe(false);
    // The room changes: the seat goes with the chair.
    room.apply({ kind: "removed", itemId: 30 });
    expect(seats.find(2.1, 0, 2.2, out)).toBe(false);
  });

  it("seats a bed's two sides, facing out", () => {
    const room = new DormRoom();
    room.reset({ openToFriends: false, cap: 150, items: [{ ...bed, yaw: 0, pos: [0, 0, 0] }] });
    const out = seatPlace();
    const seats = new FurnitureSeats(room);
    expect(seats.count).toBe(8); // each side cut into four rows
    expect(seats.find(0.9, 0, 0.3, out)).toBe(true);
    expect(out.yaw).toBeCloseTo(Math.PI / 2);
    expect(out.x).toBeCloseTo(0.535); // the side's centre line
    expect(out.z).toBeCloseTo(0.3875); // the middle of the row (a quarter of the side) it sits on
    expect([out.ahead, out.behind, out.drop]).toEqual([0.225, 1.267, 0.68]);
    expect(seats.find(-0.9, 0, 0.3, out)).toBe(true);
    expect(out.yaw).toBeCloseTo((3 * Math.PI) / 2);
    expect(out.x).toBeCloseTo(-0.535);
  });
});
