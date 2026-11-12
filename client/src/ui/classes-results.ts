// The results card's reward lines (ui/classes.ts ResultsCard), as data:
// one line per reward the professor hands out. A retried reward (the
// server answering again for a lesson already rewarded: the server stores the
// spells taught and whether the daily XP cap cut the XP, so a retry reports
// them too) shows the same lines as the first answer did.
//
// Pure, unit tested in classes-results.test.ts.

import type { ClassRewards } from "../wasm/sim_wasm";

export type RewardLineKind = "xp" | "cap" | "skill" | "points" | "crowns" | "item" | "recipe" | "spell" | "lore" | "hint" | "fortune";

export interface RewardLine {
  kind: RewardLineKind;
  /** The line's main words (bold where the card makes them so). */
  text: string;
  /** Words after it, in the note style. */
  note?: string;
  /** An item line's catalog item (its icon). */
  itemDef?: number;
}

/** What the reward lines are made from (the card's `ResultView`). */
export interface RewardSource {
  rewards: ClassRewards;
  /** The subject whose skill XP grew ("Charms"). */
  discipline: string;
  houseName: string;
  spells: readonly { name: string; how: string }[];
  items: readonly { itemDef: number; name: string; quantity: number }[];
  recipes: readonly string[];
  /** The lore entry unlocked: words for it until its title comes; null: none. */
  lore: string | null;
  fortune: { name: string; blurb: string } | null;
}

/** The daily class XP cap's note. */
export const CAP_NOTE = "That is all the class XP for today; skills keep growing.";

/** The card's reward lines, in order. */
export function rewardLines(r: RewardSource): RewardLine[] {
  const w = r.rewards;
  const out: RewardLine[] = [];
  if (w.xp > 0) out.push({ kind: "xp", text: `+${w.xp} XP` });
  if (w.xpCapped) out.push({ kind: "cap", text: CAP_NOTE });
  if (w.skillXp > 0) out.push({ kind: "skill", text: `+${w.skillXp} ${r.discipline} skill XP` });
  if (w.housePoints > 0) out.push({ kind: "points", text: `+${w.housePoints} ${w.housePoints === 1 ? "point" : "points"} to ${r.houseName}` });
  if (w.crowns > 0) out.push({ kind: "crowns", text: `+${w.crowns} Crowns` });
  for (const it of r.items) out.push({ kind: "item", text: `${it.name}${it.quantity > 1 ? ` × ${it.quantity}` : ""}`, itemDef: it.itemDef });
  for (const name of r.recipes) out.push({ kind: "recipe", text: "New recipe: ", note: name });
  for (const s of r.spells) out.push({ kind: "spell", text: `New spell: ${s.name}`, note: ` ${s.how}` });
  if (r.lore) out.push({ kind: "lore", text: "New lore: ", note: r.lore });
  if (w.hint) out.push({ kind: "hint", text: `A hint: something waits to be found in ${w.hint}.` });
  if (r.fortune) out.push({ kind: "fortune", text: `Fortune: ${r.fortune.name}`, note: ` ${r.fortune.blurb}` });
  return out;
}
