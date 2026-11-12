// The economy content the crafting, potion and dye screens read: the
// catalog's economy fields (stacks, dyes, finishes, potions, patterns, restyle
// families), the recipes (data/crafting/recipes.json) and the dye rule
// (`Palette::dyes_needed`), the same rule the server charges by. Imported at
// build time like the catalog, so client and server always agree. Every
// display name comes from the data files.
//
// Pure (no DOM), unit tested in crafting.test.ts.

import recipesJson from "../../../data/crafting/recipes.json";
import scheduleJson from "../../../data/classes/schedule.json";
import { CATALOG, PALETTE, grantedDyes, itemDef, type Catalog, type Dyes, type ItemDef, type Palette } from "../content/catalog";
import classFiles, { type ClassFile } from "../game/classes/class-files";
import { familyOf } from "../game/effects/chameleon";
import type { EffectKind, Finish, GearSlot, House, StationKind } from "../wasm/sim_wasm";

export type Discipline = "charms" | "transfiguration" | "potions";
export const DISCIPLINES: readonly Discipline[] = ["charms", "transfiguration", "potions"];

/** The catalog entry with the economy fields (catalog.json version 2). */
export interface ItemDef1c extends Omit<ItemDef, "kind"> {
  kind: ItemDef["kind"] | "material" | "dye" | "finish" | "potion" | "pattern";
  stackable?: boolean;
  max_stack?: number;
  required_level?: number;
  house_theme?: string;
  family?: string;
  price?: number;
  dye?: { family: string };
  finish?: Exclude<Finish, "none">;
  potion?: { effect: EffectKind; part?: "hair" | "robe" };
  pattern?: { recipe: number };
  furniture?: ItemDef["furniture"] & { station?: "cauldron" };
}

export interface Ingredient {
  item: number;
  quantity: number;
}

/** `sim_core::crafting::Recipe`. */
export interface Recipe {
  id: number;
  name: string;
  discipline: Discipline;
  station: StationKind;
  kind: "craft" | "restyle";
  inputs: Ingredient[];
  output: Ingredient;
  family?: string;
  dyeable?: boolean;
  tint?: boolean;
  skill: number;
  xp: number;
  craft_secs: number;
  pattern?: number;
}

export interface Recipes {
  version: number;
  /** XP needed for level 2, 3, ... */
  skill_levels: number[];
  recipes: Recipe[];
}

export const RECIPES = recipesJson as Recipes;

/** The crafting view of catalog entry `id`. */
export function def1c(id: number, catalog: Catalog = CATALOG): ItemDef1c | undefined {
  return itemDef(id, catalog) as ItemDef1c | undefined;
}

/** Whether recipe `r` makes a night-only brew (its output is tagged `night`): the server brews it only at night. */
export function isNightRecipe(r: Pick<Recipe, "output">, catalog: Catalog = CATALOG): boolean {
  return itemDef(r.output.item, catalog)?.tags.includes("night") ?? false;
}

/** "22:00 to 06:00": the night by the castle clock (data/classes/schedule.json's periods). */
export function nightHours(periods: readonly { period: string; starts: string }[] = scheduleJson.clock.periods): string {
  const sorted = [...periods].sort((a, b) => a.starts.localeCompare(b.starts));
  const i = sorted.findIndex((p) => p.period === "night");
  if (i < 0) return "after dark";
  return `${sorted[i]!.starts} to ${sorted[(i + 1) % sorted.length]!.starts}`;
}

/** Display name of an item definition ("Unknown item" for a dropped id). */
export function itemName(id: number, catalog: Catalog = CATALOG): string {
  return itemDef(id, catalog)?.name ?? "Unknown item";
}

export function recipeById(id: number, recipes: Recipes = RECIPES): Recipe | undefined {
  return recipes.recipes.find((r) => r.id === id);
}

/**
 * The classes that teach each recipe (a class file's `rewards.teaches`), by
 * recipe id: their timetable names (schedule.json `classes`), in timetable
 * order.
 */
export function teachingClasses(
  files: Readonly<Record<string, Pick<ClassFile, "id" | "rewards">>> = classFiles,
  timetable: readonly { id: string; name: string }[] = scheduleJson.classes,
): ReadonlyMap<number, readonly string[]> {
  const out = new Map<number, string[]>();
  for (const c of timetable) {
    for (const t of files[c.id]?.rewards.teaches ?? []) {
      const names = out.get(t.recipe) ?? [];
      if (!names.includes(c.name)) names.push(c.name);
      out.set(t.recipe, names);
    }
  }
  return out;
}

const TAUGHT_IN = teachingClasses();

/** The discipline a station kind serves (`Discipline::station`, reversed). */
export function stationDiscipline(kind: StationKind): Discipline | null {
  switch (kind) {
    case "craft_charms":
      return "charms";
    case "craft_transfiguration":
      return "transfiguration";
    case "cauldron":
      return "potions";
    default:
      return null;
  }
}

// ------------------------------------------------------------------ skills

/** `Recipes::skill_level`: level 1 at 0 XP, one more per threshold reached. */
export function skillLevel(xp: number, recipes: Recipes = RECIPES): number {
  let reached = 0;
  for (const need of recipes.skill_levels) {
    if (xp >= need) reached++;
    else break;
  }
  return reached + 1;
}

export function maxSkillLevel(recipes: Recipes = RECIPES): number {
  return recipes.skill_levels.length + 1;
}

/** XP needed for the next level, or null at the top. */
export function nextLevelXp(xp: number, recipes: Recipes = RECIPES): number | null {
  return recipes.skill_levels.find((need) => xp < need) ?? null;
}

/** Progress (0..1) from the current level's threshold to the next. */
export function levelProgress(xp: number, recipes: Recipes = RECIPES): number {
  const next = nextLevelXp(xp, recipes);
  if (next === null) return 1;
  const level = skillLevel(xp, recipes);
  const from = level >= 2 ? recipes.skill_levels[level - 2]! : 0;
  return Math.max(0, Math.min(1, (xp - from) / Math.max(1, next - from)));
}

// ------------------------------------------------------------------ the dye rule

/** `(family position, count)` pairs, sorted by family. */
export type DyeNeed = [number, number][];

/** `Palette::is_basic`: a colour of the free basic palette (absent in older palette files). */
export function isBasic(colour: number, palette: Palette = PALETTE): boolean {
  return ((palette as Palette & { basic?: number[] }).basic ?? []).includes(colour);
}

/**
 * `Palette::dyes_needed(defaults, current, wanted)`: each channel that
 * changes to a colour other than its current one and other than the item's
 * default for that channel and outside the free basic palette
 * (`palette.json` `basic`) uses up one dye of that colour's family. Going
 * back to the default, keeping a colour or picking a basic one is free.
 * Null when a colour is outside the palette or in no family.
 */
export function dyesNeeded(defaults: Dyes, current: Dyes, wanted: Dyes, palette: Palette = PALETTE): DyeNeed | null {
  const need = new Map<number, number>();
  for (let i = 0; i < 3; i++) {
    const w = wanted[i]!;
    if (!Number.isInteger(w) || w < 0 || w >= palette.colours.length) return null;
    if (w === current[i] || w === defaults[i] || isBasic(w, palette)) continue;
    const family = familyOf(w, palette);
    if (family < 0) return null;
    need.set(family, (need.get(family) ?? 0) + 1);
  }
  return [...need.entries()].sort((a, b) => a[0] - b[0]);
}

/** The item's own default dyes as granted to an owner of `house` (`ItemDef::granted_dyes`). */
export function defaultDyes(itemDefId: number, house: House | null, catalog: Catalog = CATALOG): Dyes | null {
  const def = itemDef(itemDefId, catalog);
  if (!def) return null;
  return house ? grantedDyes(def, house) : [def.dyes.primary, def.dyes.secondary, def.dyes.trim];
}

/** The dye item (catalog id) of each family position, from the catalog's `dye.family`. */
export function dyeItemsByFamily(catalog: Catalog = CATALOG, palette: Palette = PALETTE): Map<number, number> {
  const families = (palette as Palette & { families?: { id: string }[] }).families ?? [];
  const out = new Map<number, number>();
  for (const item of catalog.items as ItemDef1c[]) {
    if (item.kind !== "dye" || !item.dye || item.retired) continue;
    const f = families.findIndex((x) => x.id === item.dye!.family);
    if (f >= 0 && !out.has(f)) out.set(f, item.id);
  }
  return out;
}

export interface DyeCostLine {
  family: number;
  /** The dye item that pays for this family, or null if the catalog has none. */
  item: number | null;
  need: number;
  have: number;
}

/** What a recolour costs, line by line, against the dyes held (`have` by catalog id). */
export function dyeCost(need: DyeNeed, have: ReadonlyMap<number, number>, byFamily: ReadonlyMap<number, number> = dyeItemsByFamily()): DyeCostLine[] {
  return need.map(([family, count]) => {
    const item = byFamily.get(family) ?? null;
    return { family, item, need: count, have: item === null ? 0 : (have.get(item) ?? 0) };
  });
}

export function dyeCostAffordable(lines: readonly DyeCostLine[]): boolean {
  return lines.every((l) => l.item !== null && l.have >= l.need);
}

// ------------------------------------------------------------------ recipe availability

/** A recipe as `GET /api/recipes` lists it: the data plus what the character may do. */
export interface RecipeView extends Recipe {
  known: boolean;
  /** Known and the skill is high enough. */
  can_make: boolean;
}

export interface InputLine {
  item: number;
  need: number;
  have: number;
}

export type Availability =
  /** Everything is there: craft it. */
  | { kind: "ready" }
  /** Not learned; `pattern` teaches it (held: one is in the bag), and so do `classes` (timetable names). */
  | { kind: "unknown"; pattern: number | null; held: boolean; classes: readonly string[] }
  | { kind: "skill_too_low"; need: number; have: number }
  | { kind: "missing_inputs"; missing: InputLine[] }
  /** Restyles: no garment of the family to change (or only the target itself). */
  | { kind: "needs_garment" }
  | { kind: "missing_dyes"; missing: DyeCostLine[] };

export interface AvailabilityInput {
  recipe: Recipe;
  known: boolean;
  /** The character's level in the recipe's discipline. */
  level: number;
  /** Units held per catalog id (inventory only). */
  have: ReadonlyMap<number, number>;
  /** Restyles: whether a garment of the family other than the target is at hand. */
  hasGarment?: boolean;
  /** The dye choice's cost, when the recipe takes dyes. */
  dyes?: readonly DyeCostLine[];
}

/** The inputs of a recipe against what is held. */
export function inputLines(recipe: Recipe, have: ReadonlyMap<number, number>): InputLine[] {
  return recipe.inputs.map((i) => ({ item: i.item, need: i.quantity, have: have.get(i.item) ?? 0 }));
}

/**
 * Whether the character can make `recipe` now, and if not the first reason
 * why, in the order the server checks: known, skill, garment, inputs, dyes.
 */
export function availability(a: AvailabilityInput): Availability {
  const r = a.recipe;
  if (!a.known) {
    const pattern = r.pattern ?? null;
    const held = pattern !== null && (a.have.get(pattern) ?? 0) > 0;
    return { kind: "unknown", pattern, held, classes: TAUGHT_IN.get(r.id) ?? [] };
  }
  if (a.level < r.skill) return { kind: "skill_too_low", need: r.skill, have: a.level };
  if (r.kind === "restyle" && a.hasGarment === false) return { kind: "needs_garment" };
  const missing = inputLines(r, a.have).filter((l) => l.have < l.need);
  if (missing.length) return { kind: "missing_inputs", missing };
  const dyesShort = (a.dyes ?? []).filter((l) => l.item === null || l.have < l.need);
  if (dyesShort.length) return { kind: "missing_dyes", missing: dyesShort };
  return { kind: "ready" };
}

/** A short line for a recipe's availability. */
export function availabilityText(av: Availability, catalog: Catalog = CATALOG): string {
  switch (av.kind) {
    case "ready":
      return "Ready to make";
    case "unknown": {
      if (av.pattern !== null && av.held) return `Learn it from your ${itemName(av.pattern, catalog)}`;
      const inClass = av.classes.length ? `Learned in ${av.classes.join(" or ")} class` : null;
      if (av.pattern === null) return inClass ?? "Not learned yet";
      const card = itemName(av.pattern, catalog);
      return inClass ? `${inClass}, or from ${card}` : `Learned from ${card}`;
    }
    case "skill_too_low":
      return `Needs skill level ${av.need} (you are level ${av.have})`;
    case "needs_garment":
      return "Needs a garment of this family to change";
    case "missing_inputs":
      return `Missing ${av.missing.map((l) => `${l.need - l.have} × ${itemName(l.item, catalog)}`).join(", ")}`;
    case "missing_dyes":
      return `Missing dyes: ${av.missing.map((l) => (l.item === null ? "an unknown dye" : `${l.need - l.have} × ${itemName(l.item, catalog)}`)).join(", ")}`;
  }
}

// ------------------------------------------------------------------ recipe lists

export type RecipeFilter = "all" | "makeable" | "known" | "unknown";

export interface RecipeQuery {
  filter: RecipeFilter;
  /** Name search (case-insensitive, matches the recipe or its output). */
  text: string;
  /** Clothing recipes: only this slot. */
  slot: GearSlot | "all";
}

export const RECIPE_PAGE = 10;

/** The recipes of a list that match `q`, in recipe-id order. `ready(r)` says whether one can be made now. */
export function filterRecipes(list: readonly RecipeView[], q: RecipeQuery, ready: (r: RecipeView) => boolean, catalog: Catalog = CATALOG): RecipeView[] {
  const text = q.text.trim().toLowerCase();
  return list
    .filter((r) => {
      if (q.filter === "known" && !r.known) return false;
      if (q.filter === "unknown" && r.known) return false;
      if (q.filter === "makeable" && !ready(r)) return false;
      const out = itemDef(r.output.item, catalog);
      if (q.slot !== "all" && (out?.kind !== "clothing" || out.slot !== q.slot)) return false;
      if (text && !r.name.toLowerCase().includes(text) && !(out?.name.toLowerCase().includes(text) ?? false)) return false;
      return true;
    })
    .sort((a, b) => a.id - b.id);
}

/** Page `page` (0-based) of `list` at `size` a page, with whether more pages follow. */
export function pageOf<T>(list: readonly T[], page: number, size = RECIPE_PAGE): { items: T[]; page: number; pages: number } {
  const pages = Math.max(1, Math.ceil(list.length / size));
  const p = Math.max(0, Math.min(page, pages - 1));
  return { items: list.slice(p * size, p * size + size), page: p, pages };
}

/**
 * A recipes page from the server plus the data the client ships. Where the
 * server knows nothing more (an older server, or a field it leaves out) the
 * shipped recipe fills in.
 */
export function mergeRecipeView(raw: Partial<RecipeView> & { id: number }, recipes: Recipes = RECIPES): RecipeView | null {
  const base = recipeById(raw.id, recipes);
  if (!base) return null;
  const known = raw.known ?? base.pattern === undefined;
  return { ...base, ...raw, known, can_make: raw.can_make ?? known } as RecipeView;
}

// ------------------------------------------------------------------ restyles

export interface RestyleOption {
  recipe: Recipe;
  /** The garment it turns the item into. */
  target: number;
}

/** The restyle family of a clothing definition, or null. */
export function familyOfItem(itemDefId: number, catalog: Catalog = CATALOG): string | null {
  return def1c(itemDefId, catalog)?.family ?? null;
}

/** Every restyle that can change `itemDefId` into another member of its family (`Recipes::restyle_into`). */
export function restyleOptions(itemDefId: number, recipes: Recipes = RECIPES, catalog: Catalog = CATALOG): RestyleOption[] {
  const family = familyOfItem(itemDefId, catalog);
  if (!family) return [];
  return recipes.recipes
    .filter((r) => r.kind === "restyle" && r.family === family && r.output.item !== itemDefId)
    .map((r) => ({ recipe: r, target: r.output.item }))
    .sort((a, b) => a.target - b.target);
}

/** The recipe (if any) a pattern item teaches. */
export function patternRecipe(itemDefId: number, recipes: Recipes = RECIPES, catalog: Catalog = CATALOG): Recipe | undefined {
  const p = def1c(itemDefId, catalog)?.pattern;
  return p ? recipeById(p.recipe, recipes) : undefined;
}

// ------------------------------------------------------------------ errors

/** Friendlier words for the crafting routes' API errors, by code. */
export function craftingError(code: string, fallback: string): string {
  switch (code) {
    case "not_at_station":
      return "You have stepped away from the station. Go back and use it again.";
    case "recipe_unknown":
      return "You have not learned that recipe yet.";
    case "skill_too_low":
      return "Your skill is not high enough for that yet.";
    case "crafting_busy":
      return "Your hands are still busy with the last piece.";
    case "missing_inputs":
      return "You are missing some of the materials.";
    case "missing_dyes":
      return "You do not have the dyes for those colours.";
    case "missing_finish":
      return "You no longer have that finish.";
    case "invalid_dye":
      return "That colour is not in the dye palette.";
    case "wrong_item":
      return "That garment cannot become this.";
    case "item_not_found":
      return "That item is no longer in your bag.";
    case "item_unavailable":
      return "That item is in the post, on the Exchange or placed in your room.";
    case "not_a_pattern":
      return "That is not a pattern or recipe card.";
    case "already_known":
      return "You already know that recipe.";
    case "not_a_finish":
      return "That is not a finish.";
    case "not_clothing":
      return "Finishes only take on clothing.";
    case "recipe_not_found":
      return "That recipe does not exist.";
    case "rate_limited":
      return "Slow down a little: the needles need a moment.";
    case "not_implemented":
      return "The castle cannot do that yet.";
    case "night_only":
      return `That brew can only be made at night (${nightHours()} by the castle clock). Nothing was used up: come back after dark.`;
    default:
      return fallback;
  }
}
