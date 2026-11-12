import { describe, expect, it } from "vitest";
import { CATALOG, PALETTE, itemDef } from "../content/catalog";
import { countByDef, type BagItem } from "./crafting-api";
import {
  availability,
  availabilityText,
  defaultDyes,
  dyeCost,
  dyeCostAffordable,
  dyeItemsByFamily,
  dyesNeeded,
  filterRecipes,
  itemName,
  levelProgress,
  maxSkillLevel,
  mergeRecipeView,
  nextLevelXp,
  pageOf,
  patternRecipe,
  RECIPES,
  recipeById,
  restyleOptions,
  skillLevel,
  stationDiscipline,
  teachingClasses,
  type Recipe,
  type RecipeView,
} from "./crafting-content";
import { familyOf } from "../game/effects/chameleon";

const view = (r: Recipe, known = r.pattern === undefined): RecipeView => ({ ...r, known, can_make: known });
const first = (pred: (r: Recipe) => boolean) => RECIPES.recipes.find(pred)!;

describe("the shipped recipes, as the client reads them", () => {
  it("has every discipline at its station, outputs in the catalog", () => {
    for (const r of RECIPES.recipes) {
      expect(stationDiscipline(r.station), `${r.id}`).toBe(r.discipline);
      expect(itemDef(r.output.item), `${r.id} output`).toBeDefined();
      for (const i of r.inputs) expect(itemDef(i.item), `${r.id} input ${i.item}`).toBeDefined();
    }
    expect(stationDiscipline("owl_exchange")).toBeNull();
  });

  it("names a pattern that teaches it back", () => {
    for (const r of RECIPES.recipes.filter((x) => x.pattern !== undefined)) expect(patternRecipe(r.pattern!)?.id).toBe(r.id);
  });

  it("offers each crafted garment every other style of its family", () => {
    const hat = first((r) => r.kind === "restyle").family!;
    const members = CATALOG.items.filter((i) => (i as { family?: string }).family === hat);
    expect(members.length).toBeGreaterThanOrEqual(2);
    for (const m of members) {
      const opts = restyleOptions(m.id);
      expect(opts.map((o) => o.target).sort()).toEqual(members.filter((x) => x.id !== m.id).map((x) => x.id).sort());
      for (const o of opts) expect(o.recipe.family).toBe(hat);
    }
    // Starter clothing has no family: nothing to restyle.
    const starter = CATALOG.items.find((i) => i.tags.includes("starter") && i.kind === "clothing")!;
    expect(restyleOptions(starter.id)).toEqual([]);
  });
});

describe("skill levels", () => {
  it("matches Recipes::skill_level", () => {
    expect(skillLevel(0)).toBe(1);
    expect(skillLevel(99)).toBe(1);
    expect(skillLevel(100)).toBe(2);
    expect(skillLevel(3200)).toBe(maxSkillLevel());
    expect(nextLevelXp(120)).toBe(250);
    expect(nextLevelXp(5000)).toBeNull();
    expect(levelProgress(175)).toBeCloseTo(0.5);
    expect(levelProgress(5000)).toBe(1);
  });
});

describe("the dye rule (Palette::dyes_needed)", () => {
  const blue = 34;
  const blue2 = 35;
  const red = 17;

  it("charges one dye of the new colour's family per changed channel", () => {
    expect(dyesNeeded([1, 2, 3], [1, 2, 3], [blue, blue2, red])).toEqual(
      [
        [familyOf(red), 1],
        [familyOf(blue), 2],
      ].sort((a, b) => a[0]! - b[0]!),
    );
  });

  it("is free to keep a colour or go back to the item's own", () => {
    expect(dyesNeeded([1, 2, 3], [blue, 2, 3], [1, 2, 3])).toEqual([]);
    expect(dyesNeeded([1, 2, 3], [blue, 2, 3], [blue, 2, 3])).toEqual([]);
    expect(dyesNeeded([1, 2, 3], [blue, 2, 3], [red, 2, 3])).toEqual([[familyOf(red), 1]]);
  });

  it("is free to pick a colour of the basic palette", () => {
    const basic = (PALETTE as typeof PALETTE & { basic: number[] }).basic;
    expect(basic.length).toBeGreaterThan(2);
    expect(basic.includes(blue) || basic.includes(red)).toBe(false);
    expect(dyesNeeded([1, 2, 3], [blue, blue, blue], [basic[0]!, basic[1]!, basic[2]!])).toEqual([]);
    expect(dyesNeeded([1, 2, 3], [1, 2, 3], [basic[4]!, 2, red])).toEqual([[familyOf(red), 1]]);
  });

  it("refuses colours outside the palette", () => {
    expect(dyesNeeded([1, 2, 3], [1, 2, 3], [48, 2, 3])).toBeNull();
    expect(dyesNeeded([1, 2, 3], [1, 2, 3], [-1, 2, 3])).toBeNull();
  });

  it("prices against the dyes held, with one dye item per family", () => {
    const by = dyeItemsByFamily();
    expect(by.size).toBe(10);
    const need = dyesNeeded([1, 2, 3], [1, 2, 3], [blue, blue2, 3])!;
    const blueDye = by.get(familyOf(blue))!;
    expect(itemDef(blueDye)?.name).toMatch(/Blue/);
    const short = dyeCost(need, new Map([[blueDye, 1]]), by);
    expect(short).toEqual([{ family: familyOf(blue), item: blueDye, need: 2, have: 1 }]);
    expect(dyeCostAffordable(short)).toBe(false);
    expect(dyeCostAffordable(dyeCost(need, new Map([[blueDye, 5]]), by))).toBe(true);
  });

  it("takes house colours as an item's own when it was granted in them", () => {
    const robe = CATALOG.items.find((i) => i.house_dyes?.primary && i.kind === "clothing")!;
    const skytalon = defaultDyes(robe.id, "skytalon")!;
    const plain = defaultDyes(robe.id, null)!;
    expect(skytalon).not.toEqual(plain);
  });
});

describe("recipe availability", () => {
  const knit = first((r) => r.discipline === "charms" && r.pattern === undefined && r.inputs.length >= 1);
  const enough = new Map(knit.inputs.map((i) => [i.item, i.quantity]));

  it("is ready with the inputs, the skill and the knowledge", () => {
    expect(availability({ recipe: knit, known: true, level: knit.skill, have: enough })).toEqual({ kind: "ready" });
  });

  it("checks in the server's order: known, skill, garment, inputs, dyes", () => {
    const patterned = first((r) => r.pattern !== undefined);
    const unknown = availability({ recipe: patterned, known: false, level: 10, have: new Map() });
    expect(unknown).toEqual({ kind: "unknown", pattern: patterned.pattern, held: false, classes: [] });
    expect(availability({ recipe: patterned, known: false, level: 10, have: new Map([[patterned.pattern!, 1]]) })).toMatchObject({ held: true });
    expect(availabilityText(unknown)).toMatch(/^Learned from Pattern|^Learned from Recipe Card/);

    // A recipe a class also teaches names the class (from the class files' `teaches`).
    const taught = teachingClasses();
    const invisibility = recipeById(330)!;
    expect(taught.get(330)).toEqual(["Potions"]);
    const classUnknown = availability({ recipe: invisibility, known: false, level: 10, have: new Map() });
    expect(availabilityText(classUnknown)).toBe(`Learned in Potions class, or from ${itemName(invisibility.pattern!)}`);
    for (const id of [331, 332, 333]) {
      const r = recipeById(id)!;
      expect(availabilityText(availability({ recipe: r, known: false, level: 10, have: new Map() }))).toMatch(/^Learned in Astronomy class, or from Star Chart: /);
    }
    expect(availabilityText({ kind: "unknown", pattern: null, held: false, classes: ["Potions", "Astronomy"] })).toBe("Learned in Potions or Astronomy class");
    // A card in the bag says to read it.
    expect(availabilityText({ kind: "unknown", pattern: invisibility.pattern!, held: true, classes: ["Potions"] })).toMatch(/^Learn it from your /);

    const hard = { ...knit, skill: 5 };
    expect(availability({ recipe: hard, known: true, level: 2, have: new Map() })).toEqual({ kind: "skill_too_low", need: 5, have: 2 });

    const missing = availability({ recipe: knit, known: true, level: 10, have: new Map() });
    expect(missing.kind).toBe("missing_inputs");
    if (missing.kind === "missing_inputs") expect(missing.missing.map((m) => m.item)).toEqual(knit.inputs.map((i) => i.item));

    const restyle = first((r) => r.kind === "restyle");
    expect(availability({ recipe: restyle, known: true, level: 10, have: new Map(), hasGarment: false })).toEqual({ kind: "needs_garment" });

    const dyes = [{ family: 8, item: 3109, need: 1, have: 0 }];
    expect(availability({ recipe: knit, known: true, level: 10, have: enough, dyes })).toEqual({ kind: "missing_dyes", missing: dyes });
  });
});

describe("recipe lists", () => {
  const charms = RECIPES.recipes.filter((r) => r.discipline === "charms").map((r) => view(r));

  it("filters by knowledge, slot and name, in id order", () => {
    const known = filterRecipes(charms, { filter: "known", text: "", slot: "all" }, () => false);
    expect(known.every((r) => r.known)).toBe(true);
    expect(filterRecipes(charms, { filter: "unknown", text: "", slot: "all" }, () => false).every((r) => !r.known)).toBe(true);
    const hats = filterRecipes(charms, { filter: "all", text: "", slot: "hat" }, () => false);
    expect(hats.length).toBeGreaterThan(0);
    expect(hats.every((r) => itemDef(r.output.item)?.slot === "hat")).toBe(true);
    const ids = hats.map((r) => r.id);
    expect(ids).toEqual([...ids].sort((a, b) => a - b));
    const named = filterRecipes(charms, { filter: "all", text: "BERET", slot: "all" }, () => false);
    expect(named.length).toBeGreaterThan(0);
    expect(filterRecipes(charms, { filter: "makeable", text: "", slot: "all" }, (r) => r.id === charms[0]!.id)).toEqual([charms[0]]);
  });

  it("pages", () => {
    const p = pageOf([1, 2, 3, 4, 5], 1, 2);
    expect(p).toEqual({ items: [3, 4], page: 1, pages: 3 });
    expect(pageOf([1, 2, 3], 9, 2).page).toBe(1); // clamped to the last page
    expect(pageOf([], 0, 2)).toEqual({ items: [], page: 0, pages: 1 });
  });

  it("merges server rows with the shipped recipe data", () => {
    const r = RECIPES.recipes[0]!;
    expect(mergeRecipeView({ id: r.id, known: true, can_make: false })).toMatchObject({ id: r.id, name: r.name, known: true, can_make: false });
    expect(mergeRecipeView({ id: 65000 })).toBeNull();
    expect(recipeById(r.id)).toBe(r);
  });

  it("counts the bag by definition across stacks, inventory only", () => {
    const item = (id: number, def: number, quantity: number, location: BagItem["location"] = "inventory"): BagItem => ({ id, item_def: def, quantity, location, dyes: [0, 0, 0], slot: null, maker: null, finish: "none", created_at: "" });
    const counts = countByDef([item(1, 3001, 99), item(2, 3001, 4), item(3, 3002, 2, "mail"), item(4, 3003, 1)]);
    expect(counts.get(3001)).toBe(103);
    expect(counts.has(3002)).toBe(false);
    expect(counts.get(3003)).toBe(1);
  });
});
