// What an NPC's line conditions are checked against on this client
// (`LineContext`): our house, the game period, the House Cup standing, the
// catalog tags of what we wear and the secrets we found; and the
// placeholders a line may carry. Pure; unit tested in lines.test.ts.

import { CATALOG, itemDef, type Catalog } from "../../content/catalog";
import type { Appearance, CupStanding, GiftKind, House, LineContext, NpcGift, Period } from "../../wasm/sim_wasm";

export interface CupPoints {
  id: string;
  points: number;
}

/** Our house's standing in this week's cup: leading alone, tied for the lead, or behind; null before anyone scores. */
export function cupStanding(houses: readonly CupPoints[] | null | undefined, house: string): CupStanding | null {
  if (!houses?.length) return null;
  const mine = houses.find((h) => h.id === house)?.points;
  if (mine === undefined) return null;
  const best = Math.max(...houses.map((h) => h.points));
  if (best <= 0) return null;
  if (mine < best) return "other_leads";
  return houses.filter((h) => h.points === best).length > 1 ? "tied" : "yours_leads";
}

/** The catalog tags of every garment `a` wears (no repeats). */
export function wornTags(a: Appearance | null | undefined, catalog: Catalog = CATALOG): string[] {
  if (!a) return [];
  const tags = new Set<string>();
  for (const w of [a.outfit.hat, a.outfit.body, a.outfit.legs, a.outfit.shoes]) {
    if (!w) continue;
    for (const t of itemDef(w.itemDef, catalog)?.tags ?? []) tags.add(t);
  }
  return [...tags];
}

export interface ContextInput {
  house: House;
  npcHouse?: House | null;
  period: Period | null;
  cup: CupStanding | null;
  appearance: Appearance | null;
  found: Iterable<string> | null;
  gift?: GiftKind | null;
}

export function lineContext(i: ContextInput, catalog: Catalog = CATALOG): LineContext {
  return {
    house: i.house,
    npcHouse: i.npcHouse ?? null,
    period: i.period,
    cup: i.cup,
    wornTags: wornTags(i.appearance, catalog),
    foundSecrets: i.found ? [...i.found] : [],
    gift: i.gift ?? null,
  };
}

/** The gift kind a gift line is picked for (`gift` condition). */
export function giftKind(g: NpcGift): GiftKind {
  switch (g.kind) {
    case "lore":
      return "lore";
    case "item":
      return "item";
    case "housePoint":
      return "house_point";
    case "hint":
      return "hint";
  }
}

export interface LineVars {
  name: string;
  house: string;
  place?: string;
  item?: string;
  lore?: string;
  points?: string;
}

/** Fills `{name}`, `{house}`, `{place}`, `{item}`, `{lore}` and `{points}`; a placeholder with nothing to fill reads as nothing. */
export function fillLine(text: string, vars: LineVars): string {
  return text
    .replace(/\{(name|house|place|item|lore|points)\}/g, (_, k: keyof LineVars) => vars[k] ?? "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** A seed for `NpcBook.pick` (any u32). */
export function randomSeed(): number {
  return Math.floor(Math.random() * 0x1_0000_0000) >>> 0;
}
