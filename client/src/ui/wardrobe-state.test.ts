import { describe, expect, it } from "vitest";
import { defaultAppearance } from "../content/appearance";
import { CATALOG, starterClothing } from "../content/catalog";
import type { Item } from "./wardrobe-api";
import {
  afterEquip,
  emptyPaged,
  inventoryQuery,
  isTryingOn,
  itemSubtitle,
  mergePage,
  mirrorAppearance,
  missingText,
  nothingWorn,
  outfitNameProblem,
  outfitPieces,
  removeItem,
  replaceItem,
  slotOf,
  validDyes,
  wardrobeError,
  wearProblem,
  withDye,
} from "./wardrobe-state";

const def = (slot: "hat" | "body" | "legs" | "shoes") => starterClothing("skytalon", slot)[0]!;
const item = (id: number, itemDef: number, extra: Partial<Item> = {}): Item => ({
  id,
  item_def: itemDef,
  dyes: [1, 2, 3],
  location: "inventory",
  slot: null,
  created_at: "2026-10-01T00:00:00Z",
  ...extra,
});

describe("paging", () => {
  it("appends pages without duplicates and remembers the cursor", () => {
    let list = emptyPaged<Item>();
    expect(list.loaded).toBe(false);
    list = mergePage(list, { items: [item(3, 101), item(2, 101)], next: "c1" });
    list = mergePage(list, { items: [item(2, 101), item(1, 101)], next: null });
    expect(list.items.map((i) => i.id)).toEqual([3, 2, 1]);
    expect(list.next).toBeNull();
    expect(list.loaded).toBe(true);
    expect(replaceItem(list, item(2, 102)).items[1]!.item_def).toBe(102);
    expect(removeItem(list, 2).items.map((i) => i.id)).toEqual([3, 1]);
  });

  it("queries by kind and slot", () => {
    expect(inventoryQuery("clothing", "all", null)).toMatchObject({ kind: "clothing", slot: undefined, after: null });
    expect(inventoryQuery("clothing", "legs", "x")).toMatchObject({ kind: "clothing", slot: "legs", after: "x" });
    expect(inventoryQuery("furniture", "legs", null)).toMatchObject({ kind: "furniture", slot: undefined });
  });
});

describe("equipping", () => {
  it("marks the new item worn and sends the old one back to the trunk", () => {
    const oldShoes = item(1, def("shoes").id, { location: "equipped", slot: "shoes" });
    const newShoes = item(2, starterClothing("skytalon", "shoes")[1]!.id);
    const list = mergePage(emptyPaged<Item>(), { items: [oldShoes, newShoes], next: null });
    const worn = { ...nothingWorn(), shoes: oldShoes };
    const r = afterEquip(list, worn, "shoes", newShoes);
    expect(r.worn.shoes?.id).toBe(2);
    expect(r.list.items.find((i) => i.id === 2)).toMatchObject({ location: "equipped", slot: "shoes" });
    expect(r.list.items.find((i) => i.id === 1)).toMatchObject({ location: "inventory", slot: null });
    const off = afterEquip(r.list, r.worn, "shoes", null);
    expect(off.worn.shoes).toBeNull();
    expect(off.list.items.find((i) => i.id === 2)!.location).toBe("inventory");
  });
});

describe("the mirror", () => {
  const base = defaultAppearance("skytalon");

  it("tries the selected clothing on in its slot with unsaved dyes", () => {
    const hat = item(9, def("hat").id);
    const a = mirrorAppearance(base, hat, [5, 6, 7]);
    expect(a.outfit.hat).toEqual({ itemDef: hat.item_def, dyes: [5, 6, 7] });
    expect(base.outfit.hat).toBeNull();
    expect(mirrorAppearance(base, hat, null).outfit.hat?.dyes).toEqual([1, 2, 3]);
    expect(isTryingOn(nothingWorn(), hat, null)).toBe(true);
  });

  it("ignores furniture and shows what is worn when nothing is selected", () => {
    const bed = item(10, CATALOG.items.find((i) => i.kind === "furniture")!.id);
    expect(mirrorAppearance(base, bed, [1, 1, 1])).toBe(base);
    expect(mirrorAppearance(base, null, null)).toBe(base);
    expect(slotOf(bed)).toBeNull();
    expect(isTryingOn(nothingWorn(), bed, null)).toBe(false);
  });

  it("is not trying on when the selected item is worn as it is", () => {
    const robe = item(4, def("body").id, { location: "equipped", slot: "body" });
    const worn = { ...nothingWorn(), body: robe };
    expect(isTryingOn(worn, robe, [1, 2, 3])).toBe(false);
    expect(isTryingOn(worn, robe, [1, 2, 4])).toBe(true);
  });
});

describe("dyes, names and messages", () => {
  it("checks dyes against the palette and sets one channel", () => {
    expect(validDyes([0, 47, 12])).toBe(true);
    expect(validDyes([0, 48, 12])).toBe(false);
    expect(validDyes([0, -1, 12])).toBe(false);
    expect(withDye([1, 2, 3], 1, 9)).toEqual([1, 9, 3]);
  });

  it("validates outfit names like the server", () => {
    expect(outfitNameProblem("  ")).not.toBeNull();
    expect(outfitNameProblem("x".repeat(33))).not.toBeNull();
    expect(outfitNameProblem(" Sunday best ")).toBeNull();
  });

  it("names an outfit's pieces when it knows them", () => {
    const robe = item(4, def("body").id);
    const pieces = outfitPieces({ id: 1, name: "a", hat: null, body: 4, legs: 77, shoes: null, created_at: "" }, new Map([[4, robe]]));
    expect(pieces.find((p) => p.slot === "body")!.name).toBe(def("body").name);
    expect(pieces.find((p) => p.slot === "legs")!.name).toBe("An item");
    expect(pieces.find((p) => p.slot === "hat")!.name).toBeNull();
  });

  it("explains missing slots and API errors", () => {
    expect(missingText([])).toBeNull();
    expect(missingText(["hat", "shoes"])).toContain("hat and shoes");
    expect(wardrobeError("item_unavailable", "x")).toContain("placed in your room");
    expect(wardrobeError("something_new", "Server text")).toBe("Server text");
    expect(itemSubtitle(item(1, def("legs").id))).toMatch(/^Legs/);
  });

  it("refuses what the body may not wear, and does not try it on", () => {
    // Body 0 male, body 1 female; 302 Pleated School Skirt is female only, 305 Tartan Kilt male only.
    const skirt = item(9, 302);
    const kilt = item(10, 305);
    expect(wearProblem(skirt, 0)).toBe("That is female only.");
    expect(wearProblem(skirt, 1)).toBeNull();
    expect(wearProblem(kilt, 1)).toBe("That is male only.");
    expect(wearProblem(item(11, 301), 0)).toBeNull();
    expect(wearProblem(item(12, 1001), 1)).toBeNull();
    const male = defaultAppearance("skytalon");
    expect(male.bodyType).toBe(0);
    expect(mirrorAppearance(male, skirt, null)).toBe(male);
    const female = { ...male, bodyType: 1 };
    expect(mirrorAppearance(female, skirt, null).outfit.legs?.itemDef).toBe(302);
    expect(mirrorAppearance(female, kilt, null)).toBe(female);
    expect(wardrobeError("wear_restricted", "That is female only.")).toBe("That is female only.");
    const pieces = outfitPieces({ id: 1, name: "Kilt day", hat: null, body: null, legs: 10, shoes: null } as never, new Map([[10, kilt]]));
    expect(pieces.find((p) => p.slot === "legs")!.name).toBe("Tartan Kilt (Male only)");
  });
});
