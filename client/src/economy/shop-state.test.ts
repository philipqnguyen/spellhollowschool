import { describe, expect, it } from "vitest";
import { CATALOG } from "../content/catalog";
import { exchangeCategories, type EconItemDef } from "./items";
import { Basket, ORDER_LINES_MAX, shopCategories, unitsMax } from "./shop-state";

const items = CATALOG.items as EconItemDef[];
const priced = items.filter((i) => typeof i.price === "number");
const material = priced.find((i) => i.kind === "material")!;
const clothing = priced.find((i) => i.kind === "clothing")!;

describe("the basket", () => {
  it("adds, tops up and totals with whole Crowns", () => {
    const b = new Basket();
    expect(b.add({ item_def: material.id, price: 2 }, 5)).toBeNull();
    expect(b.add({ item_def: material.id, price: 2 }, 3)).toBeNull();
    expect(b.add({ item_def: clothing.id, price: 12 }, 2)).toBeNull();
    expect(b.lines).toHaveLength(2);
    expect(b.units).toBe(10);
    expect(b.total()).toBe(8 * 2 + 2 * 12);
    expect(b.orderLines()).toEqual([
      { item_def: material.id, quantity: 8 },
      { item_def: clothing.id, quantity: 2 },
    ]);
  });

  it("keeps quantities within what one line allows", () => {
    expect(unitsMax(material.id)).toBe(99);
    expect(unitsMax(clothing.id)).toBe(10);
    const b = new Basket();
    expect(b.add({ item_def: clothing.id, price: 12 }, 11)).toMatch(/10/);
    expect(b.add({ item_def: clothing.id, price: 12 }, 10)).toBeNull();
    expect(b.add({ item_def: clothing.id, price: 12 }, 1)).toMatch(/10/);
    expect(b.add({ item_def: material.id, price: 2 }, 0)).toMatch(/at least one/);
    expect(b.add({ item_def: material.id, price: 2 }, 1.5)).toMatch(/at least one/);
    b.set(clothing.id, 50);
    expect(b.lines[0]!.quantity).toBe(10);
    b.set(clothing.id, 0);
    expect(b.empty).toBe(true);
  });

  it("holds at most ten lines", () => {
    const b = new Basket();
    const ten = priced.slice(0, ORDER_LINES_MAX + 1);
    ten.slice(0, ORDER_LINES_MAX).forEach((i) => expect(b.add({ item_def: i.id, price: i.price! })).toBeNull());
    expect(b.add({ item_def: ten[ORDER_LINES_MAX]!.id, price: 1 })).toMatch(/10 different/);
  });

  it("says when the purse does not cover it", () => {
    const b = new Basket();
    expect(b.problem(100)).toMatch(/empty/);
    b.add({ item_def: clothing.id, price: 12 }, 3);
    expect(b.problem(36)).toBeNull();
    expect(b.problem(35)).toMatch(/enough/);
  });

  it("keys the basket by its contents", () => {
    const a = new Basket();
    a.add({ item_def: material.id, price: 2 }, 2);
    a.add({ item_def: clothing.id, price: 12 }, 1);
    const b = new Basket();
    b.add({ item_def: clothing.id, price: 12 }, 1);
    b.add({ item_def: material.id, price: 2 }, 2);
    expect(a.key()).toBe(b.key());
    b.set(material.id, 3);
    expect(a.key()).not.toBe(b.key());
  });
});

describe("catalogue sections", () => {
  it("lists only the categories with something for sale, never crafted fashion", () => {
    const sections = shopCategories(exchangeCategories(), items).map((c) => c.id);
    expect(sections).toContain("furniture");
    expect(sections).toContain("materials");
    expect(sections).not.toContain("potions");
    for (const i of priced) expect(i.tags).not.toContain("crafted");
  });
});
