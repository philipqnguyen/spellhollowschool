// The results card's reward lines, for a first and a retried reward.

import { describe, expect, it } from "vitest";
import type { ClassRewards } from "../wasm/sim_wasm";
import { CAP_NOTE, rewardLines, type RewardSource } from "./classes-results";

function rewards(over: Partial<ClassRewards> = {}): ClassRewards {
  return { xp: 0, xpCapped: false, skillXp: 0, housePoints: 0, spells: [], crowns: 0, items: [], recipes: [], lore: null, hint: null, fortune: null, ...over };
}

function source(r: ClassRewards, over: Partial<RewardSource> = {}): RewardSource {
  return { rewards: r, discipline: "Defensive Magic", houseName: "Skytalon", spells: [], items: [], recipes: [], lore: null, fortune: null, ...over };
}

describe("results card lines", () => {
  it("lists every reward in order", () => {
    const r = rewards({ xp: 120, xpCapped: true, skillXp: 55, housePoints: 3, crowns: 17, items: [{ itemDef: 3008, quantity: 2 }], spells: [7], hint: "the Entrance Hall" });
    const lines = rewardLines(
      source(r, {
        spells: [{ name: "Tickling Charm", how: "Add it to your dueling loadout at the Dueling Club." }],
        items: [{ itemDef: 3008, name: "Lynxkit Fur", quantity: 2 }],
        recipes: ["Brew Moonlit Glow Draught"],
        fortune: { name: "Quick Hands", blurb: "Crafting goes faster." },
      }),
    );
    expect(lines.map((l) => l.kind)).toEqual(["xp", "cap", "skill", "points", "crowns", "item", "recipe", "spell", "hint", "fortune"]);
    expect(lines.find((l) => l.kind === "spell")).toEqual({ kind: "spell", text: "New spell: Tickling Charm", note: " Add it to your dueling loadout at the Dueling Club." });
    expect(lines.find((l) => l.kind === "item")).toEqual({ kind: "item", text: "Lynxkit Fur × 2", itemDef: 3008 });
    expect(lines.find((l) => l.kind === "points")!.text).toBe("+3 points to Skytalon");
  });

  it("shows a retried reward's spells and the XP cap note too (the server stores both)", () => {
    // A retry repeats what the attendance row recorded: here the cap cut the XP to nothing.
    const retried = rewards({ xp: 0, xpCapped: true, skillXp: 40, spells: [7] });
    const lines = rewardLines(source(retried, { spells: [{ name: "Tickling Charm", how: "Add it to your dueling loadout at the Dueling Club." }] }));
    expect(lines.map((l) => l.kind)).toEqual(["cap", "skill", "spell"]);
    expect(lines[0]!.text).toBe(CAP_NOTE);
    expect(lines[2]!.text).toBe("New spell: Tickling Charm");
  });

  it("says one point, and nothing for a lesson with no marks", () => {
    expect(rewardLines(source(rewards({ housePoints: 1 })))[0]!.text).toBe("+1 point to Skytalon");
    expect(rewardLines(source(rewards()))).toEqual([]);
    expect(rewardLines(source(rewards({ lore: 41 }), { lore: "a new page in your lore book" }))).toEqual([{ kind: "lore", text: "New lore: ", note: "a new page in your lore book" }]);
  });
});
