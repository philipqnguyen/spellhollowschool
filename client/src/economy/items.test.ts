import { describe, expect, it } from "vitest";
import { CATALOG } from "../content/catalog";
import { dyedAwayFromArt } from "./icons";
import { categoryPath, econDef, effectLabel, exchangeCategories, isMovable, isValidCategory, itemLine, nameWithQuantity, POTION_EFFECTS, stackLimit, type EconItemDef } from "./items";

const items = CATALOG.items as EconItemDef[];

describe("the Exchange category table", () => {
  it("matches the server's table (sim_core::exchange::CATEGORIES)", () => {
    expect(exchangeCategories().map((c) => [c.id, c.subs.map((s) => s.id)])).toEqual([
      ["hats", ["hats", "hoods", "hair_accessories"]],
      ["body", ["robes", "jackets", "sweaters", "tops"]],
      ["legs", ["trousers", "skirts"]],
      ["shoes", ["shoes", "boots"]],
      ["furniture", ["beds", "seating", "tables", "decor", "lighting", "plants"]],
      ["materials", ["fibers", "feathers", "fur", "dye_plants", "ingredients", "patterns"]],
      ["dyes", ["dyes", "finishes"]],
      ["potions", [...POTION_EFFECTS]],
    ]);
  });

  it("holds every shipped item's category", () => {
    for (const i of items) expect(isValidCategory(i.exchange.category, i.exchange.subcategory), `${i.id}`).toBe(true);
  });

  it("names potion categories after their potions (names live in the data)", () => {
    for (const e of POTION_EFFECTS) {
      const label = effectLabel(e);
      expect(label.length).toBeGreaterThan(2);
      expect(label).not.toMatch(/\(/);
    }
    expect(categoryPath("hats", "hoods")).toBe("Hats › Hoods");
    expect(categoryPath("hats", null)).toBe("Hats");
  });
});

describe("item helpers", () => {
  it("know stacks", () => {
    const wool = items.find((i) => i.kind === "material")!;
    const robe = items.find((i) => i.kind === "clothing")!;
    expect(stackLimit(wool)).toBe(wool.max_stack);
    expect(stackLimit(robe)).toBe(1);
    expect(stackLimit(undefined)).toBe(1);
  });

  it("describe an instance with its maker's mark and finish", () => {
    const hat = items.find((i) => i.kind === "clothing" && i.slot === "hat")!;
    expect(itemLine({ item_def: hat.id, quantity: 1, maker: "Ottilie", finish: "shimmer" })).toBe("Hat · Shimmer finish · Made by Ottilie");
    const wool = items.find((i) => i.kind === "material")!;
    expect(itemLine({ item_def: wool.id, quantity: 12, maker: null, finish: "none" })).toBe("Material · 12 units");
    expect(nameWithQuantity(wool.id, 3)).toBe(`${wool.name} ×3`);
    expect(econDef(wool.id)?.stackable).toBe(true);
  });

  it("only lets trunk items move", () => {
    expect(isMovable({ location: "inventory" })).toBe(true);
    for (const l of ["equipped", "dorm", "mail", "exchange"] as const) expect(isMovable({ location: l })).toBe(false);
  });
});

describe("icons of dyed items", () => {
  it("mark clothing and furniture dyed away from the colours their art shows", () => {
    const hat = items.find((i) => i.kind === "clothing")!;
    const chair = items.find((i) => i.kind === "furniture")!;
    const dye = items.find((i) => i.kind === "dye")!;
    const art = (d: EconItemDef) => [d.dyes.primary, d.dyes.secondary, d.dyes.trim];
    expect(dyedAwayFromArt(hat, art(hat))).toBe(false);
    expect(dyedAwayFromArt(hat, undefined)).toBe(false);
    const other = art(hat).map((c, i) => (i === 2 ? (c + 1) % 48 : c));
    expect(dyedAwayFromArt(hat, other)).toBe(true);
    expect(dyedAwayFromArt(chair, [(art(chair)[0]! + 5) % 48, art(chair)[1]!, art(chair)[2]!])).toBe(true);
    // A dye's or a potion's colour is the item itself, not a dyeing.
    expect(dyedAwayFromArt(dye, [40, 41, 42])).toBe(false);
    expect(dyedAwayFromArt(undefined, [1, 2, 3])).toBe(false);
  });
});
