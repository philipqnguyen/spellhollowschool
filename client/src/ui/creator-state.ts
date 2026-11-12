// The character creator's state and rules, without the DOM: the chosen
// house, the appearance being built and the name. Choosing a house re-dresses
// the starter outfit for it (its robe, its colours on house-dyed items); every
// choice stays inside what the server accepts for a new character
// (`Creator::check_appearance` plus starter clothing only; since Fixes
// round 1 also only what the chosen body may wear, re-checked whenever the
// body changes). Pure, unit tested.

import { cloneAppearance, defaultAppearance, setSlider, wearStarter } from "../content/appearance";
import { CATALOG, CREATOR, GEAR_SLOTS, TAG_DEFAULT, bodyCanWear, clothingDef, defaultClothing, grantedDyes, hairStylesFor, isStarterFor, migrateHair, starterClothing, type Catalog, type Creator, type ItemDef } from "../content/catalog";
import { houseWireName } from "../content/houses";
import type { Appearance, GearSlot, House } from "../wasm/sim_wasm";
import { nameProblem } from "./validate";

export type CreatorStep = "house" | "body" | "face" | "hair" | "outfit" | "name";
export const STEPS: { id: CreatorStep; label: string }[] = [
  { id: "house", label: "House" },
  { id: "body", label: "Body" },
  { id: "face", label: "Face" },
  { id: "hair", label: "Hair" },
  { id: "outfit", label: "Outfit" },
  { id: "name", label: "Name" },
];

export interface CreatorState {
  house: House;
  appearance: Appearance;
  name: string;
  step: CreatorStep;
  /**
   * The hair style last chosen for each body type (by index): switching to
   * Male and back to Female keeps the Female style. Each style belongs to
   * one body (creator.json v2), so a switch never carries a style across.
   */
  hairByBody?: Record<number, number>;
}

export function initialState(house: House = "roarwing", creator: Creator = CREATOR): CreatorState {
  return { house, appearance: defaultAppearance(house, creator), name: "", step: "house" };
}

/**
 * Switches house. Body, face and hair stay; each outfit slot keeps its item
 * when the new house may wear it (re-dyed in the new house's colours where
 * the item takes them), and otherwise takes the new house's default.
 */
export function chooseHouse(s: CreatorState, house: House, catalog: Catalog = CATALOG): CreatorState {
  if (house === s.house) return s;
  let a = cloneAppearance(s.appearance);
  for (const slot of GEAR_SLOTS) {
    const worn = a.outfit[slot];
    const item = worn ? clothingDef(worn.itemDef, slot, catalog) : undefined;
    if (worn && item && isStarterFor(item, house)) a = wearStarter(a, slot, item.id, house, catalog);
    else if (worn) a = wearStarter(a, slot, defaultClothing(house, slot, catalog)?.id ?? null, house, catalog);
  }
  return { ...s, house, appearance: a };
}

export function update(s: CreatorState, change: (a: Appearance) => void): CreatorState {
  const a = cloneAppearance(s.appearance);
  change(a);
  return { ...s, appearance: a };
}

/**
 * Switches body type ("Male" / "Female"). The height byte is kept (it is
 * relative: the default stays the default); the hair takes the style last
 * chosen for that body, else the migrated equivalent of the current one
 * (the body's default for a style of the other body).
 */
export function setBodyType(s: CreatorState, index: number, creator: Creator = CREATOR): CreatorState {
  if (!creator.body_types[index] || index === s.appearance.bodyType) return s;
  const remembered = s.hairByBody?.[index];
  const hairByBody = { ...s.hairByBody, [s.appearance.bodyType]: s.appearance.hairStyle };
  const next = update(s, (a) => {
    a.bodyType = index;
    a.hairStyle = migrateHair(remembered ?? a.hairStyle, index, creator);
  });
  // What the new body may not wear goes back to the slot's default.
  return { ...next, appearance: fitOutfit(next.appearance, s.house, CATALOG, creator), hairByBody };
}

/**
 * `a` with every piece its body may not wear (`canWear`)
 * swapped for the slot's default starter item (anyone's), or taken off
 * (the hat, which has no default). The creator only offers what the body
 * may wear, so this runs when the body changes.
 */
export function fitOutfit(a: Appearance, house: House, catalog: Catalog = CATALOG, creator: Creator = CREATOR): Appearance {
  let out = a;
  for (const slot of GEAR_SLOTS) {
    const worn = out.outfit[slot];
    const item = worn ? clothingDef(worn.itemDef, slot, catalog) : undefined;
    if (!worn || bodyCanWear(out.bodyType, item, creator)) continue;
    const wearable = starterClothing(house, slot, catalog).filter((i) => bodyCanWear(out.bodyType, i, creator));
    const fallback = wearable.find((i) => i.tags.includes(TAG_DEFAULT)) ?? (slot === "hat" ? undefined : wearable[0]);
    out = wearStarter(out, slot, fallback?.id ?? null, house, catalog);
  }
  return out;
}

export function setSliderById(s: CreatorState, id: string, value: number, creator: Creator = CREATOR): CreatorState {
  const slider = creator.sliders.find((x) => x.id === id);
  return slider ? update(s, (a) => setSlider(a, slider, value)) : s;
}

export function setSkinTone(s: CreatorState, index: number, creator: Creator = CREATOR): CreatorState {
  return creator.skin_tones[index] ? update(s, (a) => (a.skinTone = index)) : s;
}

/** Chooses a hair style: only a shipped style of the current body. */
export function setHairStyle(s: CreatorState, id: number, creator: Creator = CREATOR): CreatorState {
  return hairStylesFor(s.appearance.bodyType, creator).some((h) => h.id === id) ? update(s, (a) => (a.hairStyle = id)) : s;
}

export function setHairColour(s: CreatorState, index: number, creator: Creator = CREATOR): CreatorState {
  return creator.hair_colours[index] ? update(s, (a) => (a.hairColour = index)) : s;
}

export function setEyeColour(s: CreatorState, index: number, creator: Creator = CREATOR): CreatorState {
  return creator.eye_colours[index] ? update(s, (a) => (a.eyeColour = index)) : s;
}

/** Wears a starter item (or nothing, for the hat) in `slot`. Body, legs and shoes are always worn. */
export function setOutfit(s: CreatorState, slot: GearSlot, itemDef: number | null, catalog: Catalog = CATALOG): CreatorState {
  if (itemDef === null && slot !== "hat") return s;
  if (itemDef !== null) {
    const item = clothingDef(itemDef, slot, catalog);
    if (!item || !isStarterFor(item, s.house) || !bodyCanWear(s.appearance.bodyType, item)) return s;
  }
  return { ...s, appearance: wearStarter(s.appearance, slot, itemDef, s.house, catalog) };
}

/** The starter items a slot can be dressed in for the chosen house and body (only what the body may wear). */
export function outfitChoices(s: CreatorState, slot: GearSlot, catalog: Catalog = CATALOG): ItemDef[] {
  return starterClothing(s.house, slot, catalog).filter((i) => bodyCanWear(s.appearance.bodyType, i));
}

/** The granted dyes an item will be given (and shown with) for the chosen house. */
export function choiceDyes(s: CreatorState, item: ItemDef): [number, number, number] {
  return grantedDyes(item, s.house);
}

/**
 * A random look within the creator's choices (body, height and face, skin,
 * a hair style of that body, hair and eye colour); the house and outfit
 * stay. `rand` returns 0..1.
 */
export function randomLook(s: CreatorState, rand: () => number, creator: Creator = CREATOR): CreatorState {
  const pick = <T>(xs: readonly T[]): T => xs[Math.min(xs.length - 1, Math.floor(rand() * xs.length))]!;
  const colour = (xs: readonly { index: number }[]): number => pick(xs).index;
  const next = update(s, (a) => {
    a.bodyType = pick(creator.body_types).index;
    for (const slider of creator.sliders) {
      // Mostly near the middle: extremes are for choosing, not for dice.
      setSlider(a, slider, Math.round(Math.max(0, Math.min(255, 128 + (rand() - 0.5) * 150))));
    }
    a.skinTone = pick(creator.skin_tones).index;
    a.hairStyle = pick(hairStylesFor(a.bodyType, creator)).id;
    a.hairColour = colour(creator.hair_colours);
    a.eyeColour = colour(creator.eye_colours);
  });
  // A new body may not wear what the old one did.
  return { ...next, appearance: fitOutfit(next.appearance, s.house, CATALOG, creator) };
}

export function goTo(s: CreatorState, step: CreatorStep): CreatorState {
  return { ...s, step };
}

/** The step after (or before) the current one, or null at either end. */
export function nextStep(s: CreatorState, delta: 1 | -1): CreatorStep | null {
  const i = STEPS.findIndex((x) => x.id === s.step) + delta;
  return STEPS[i]?.id ?? null;
}

/** Why the character cannot be created yet, or null when it can. */
export function submitProblem(s: CreatorState): string | null {
  return nameProblem(s.name);
}

/** The `POST /api/characters` body (the outfit's dyes are the granted ones). */
export function createRequest(s: CreatorState): { name: string; house: string; appearance: Appearance } {
  return { name: s.name.trim(), house: houseWireName(s.house), appearance: s.appearance };
}
