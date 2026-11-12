// The decorating list (furniture-list.ts): rows per look with icons' data and
// counts, search, the category filter, and reading the trunk's every page.

import { describe, expect, it } from "vitest";
import type { Page } from "../../ui/friends-api";
import { categoryOf } from "./catalog";
import { buildRows, categoryCounts, categoryLabel, filterRows, loadAllFurniture } from "./furniture-list";
import type { FurnitureItem } from "./inventory";
import type { RoomItem } from "./room";

// Catalog ids: 1001 bed, 1004 writing desk, 1005 wooden chair, 1006 armchair,
// 1007 rug, 1008 bookcase, 1009 banner, 1015 lantern, 1020 candelabra.
const item = (id: number, itemDef: number, dyes: [number, number, number] = [1, 2, 3]): FurnitureItem => ({ id, itemDef, dyes });
const placed = (itemId: number, itemDef: number, dyes: [number, number, number] = [1, 2, 3]): RoomItem => ({ itemId, itemDef, dyes, pos: [0, 0, 0], yawUnits: 0 });

describe("the decorating list", () => {
  const trunk = [item(1, 1005), item(2, 1005), item(3, 1005, [9, 9, 9]), item(4, 1015), item(5, 1004), item(6, 1009), item(7, 1001), item(8, 101)];
  const room = [placed(20, 1005), placed(21, 1004), placed(22, 1020)];

  it("has one row per look with how many are free of how many owned", () => {
    const rows = buildRows(trunk, room, new Set([2]));
    const chair = rows.find((r) => r.key === "1005:1,2,3")!;
    expect(chair.name).toBe("Wooden Chair");
    expect(chair.available.map((i) => i.id)).toEqual([1]); // item 2 is waiting on an edit
    expect(chair.owned).toBe(3); // two in the trunk, one in the room
    expect(rows.find((r) => r.key === "1005:9,9,9")?.owned).toBe(1); // another dye is another row
    // All in the room: listed, nothing free.
    const candelabra = rows.find((r) => r.itemDef === 1020)!;
    expect(candelabra.available).toEqual([]);
    expect(candelabra.owned).toBe(1);
    // Clothing (101, a hat) never shows; rows go by category, then name.
    expect(rows.some((r) => r.itemDef === 101)).toBe(false);
    expect(rows.map((r) => r.category)).toEqual(["beds", "seating", "seating", "tables", "lighting", "lighting", "wall"]);
  });

  it("searches names by every word, in any case", () => {
    const rows = buildRows(trunk, room, new Set());
    expect(filterRows(rows, "chair", "all").map((r) => r.itemDef)).toEqual([1005, 1005]);
    expect(filterRows(rows, "  WRITING  desk ", "all").map((r) => r.name)).toEqual(["Writing Desk"]);
    expect(filterRows(rows, "lighting", "all").length).toBe(2); // the category's name counts
    expect(filterRows(rows, "sofa", "all")).toEqual([]);
    expect(filterRows(rows, "", "all").length).toBe(rows.length);
  });

  it("filters by category, alone or with a search", () => {
    const rows = buildRows(trunk, room, new Set());
    expect(filterRows(rows, "", "seating").map((r) => r.name)).toEqual(["Wooden Chair", "Wooden Chair"]);
    expect(filterRows(rows, "cand", "lighting").map((r) => r.name)).toEqual(["Brass Candelabra", "Candle Lantern"]);
    expect(filterRows(rows, "chair", "beds")).toEqual([]);
    expect(categoryCounts(rows)).toEqual([
      ["beds", 1],
      ["seating", 2],
      ["tables", 1],
      ["lighting", 2],
      ["wall", 1],
    ]);
    expect(categoryLabel("all")).toBe("All furniture");
    expect(categoryLabel("decor")).toBe("Decor and tabletop");
  });

  it("files every catalog piece under a category", () => {
    expect(categoryOf("floor", ["starter", "bed"])).toBe("beds");
    expect(categoryOf("floor", ["storage"])).toBe("storage");
    expect(categoryOf("floor", ["rug"])).toBe("rugs");
    expect(categoryOf("wall", ["reward"])).toBe("wall");
    expect(categoryOf("table", ["decor"])).toBe("decor");
    expect(categoryOf("floor", ["plant"])).toBe("decor");
  });

  it("reads the trunk's every page (no piece hides on a later page)", async () => {
    const all = Array.from({ length: 230 }, (_, i) => item(1000 - i, 1005));
    const asked: (string | null)[] = [];
    const fetchPage = async (after: string | null): Promise<Page<FurnitureItem>> => {
      asked.push(after);
      const start = after === null ? 0 : Number(after);
      const end = start + 100;
      return { items: all.slice(start, end), next: end < all.length ? String(end) : null };
    };
    const items = await loadAllFurniture(fetchPage);
    expect(items.length).toBe(230);
    expect(asked).toEqual([null, "100", "200"]);
    const rows = buildRows(items, [], new Set());
    expect(rows[0]!.available.length).toBe(230);
  });

  it("stops on a repeated cursor or after the page limit", async () => {
    let calls = 0;
    const stuck = async (): Promise<Page<FurnitureItem>> => {
      calls++;
      return { items: [item(calls, 1005)], next: "same" };
    };
    expect((await loadAllFurniture(stuck)).length).toBe(2);
    calls = 0;
    let n = 0;
    const endless = async (): Promise<Page<FurnitureItem>> => ({ items: [item(++n, 1005)], next: String(n) });
    expect((await loadAllFurniture(endless, 5)).length).toBe(5);
  });
});
