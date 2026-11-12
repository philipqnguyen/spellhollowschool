import { describe, expect, it } from "vitest";
import progressionJson from "../../../data/progression.json";
import type { Progress } from "../wasm/sim_wasm";
import { FlourishGate, gainText, LEVEL_MAX, levelFraction, levelTitle, levelUp, loadoutSlots, mergeLevelUps, xpText } from "./progression-state";
import { levelNeeded } from "./wardrobe-state";

const progress = (level: number, xp: number): Progress => {
  const levels = progressionJson.levels;
  return { level, xp, levelXp: levels[level - 1]!, nextLevelXp: level < levels.length ? levels[level]! : null };
};

describe("levels", () => {
  it("runs to level 20 and titles a name with its level", () => {
    expect(LEVEL_MAX).toBe(20);
    expect(levelTitle(5)).toBe("Lv 5");
    expect(levelTitle(0)).toBe(""); // NPCs
  });

  it("opens loadout slots at 1, 5 and 10", () => {
    expect([1, 4, 5, 9, 10, 20].map(loadoutSlots)).toEqual([3, 3, 4, 4, 5, 5]);
  });

  it("fills the XP bar through the level, full at the cap", () => {
    expect(levelFraction(progress(1, 0))).toBe(0);
    expect(levelFraction(progress(1, 100))).toBe(0.5);
    expect(levelFraction(progress(20, 99_999))).toBe(1);
    expect(xpText(progress(2, 300))).toBe("300 / 440 XP");
    expect(xpText(progress(20, 10_640))).toBe("10,640 XP · top level");
  });

  it("describes grants, capped ones too", () => {
    expect(gainText({ source: "class_attend", amount: 80, capped: false })).toBe("+80 XP · class attended");
    expect(gainText({ source: "duel_win", amount: 12, capped: true })).toBe("+12 XP · duel won (today's limit reached)");
    expect(gainText({ source: "find", amount: 0, capped: true })).toContain("limit");
    expect(gainText({ source: "find", amount: 0, capped: false })).toBe("");
  });

  it("says what a level-up brings", () => {
    expect(levelUp(3, 3)).toBeNull();
    expect(levelUp(4, 3)).toBeNull();
    expect(levelUp(2, 3)).toEqual({ level: 3, slots: null });
    expect(levelUp(3, 5)).toEqual({ level: 5, slots: 4 });
  });

  it("marks clothes above the player's level in the wardrobe", () => {
    // Item ids from data/items/catalog.json: a starter hat (level 1) and the first piece needing more.
    const catalog = import.meta.glob("../../../data/items/catalog.json", { eager: true, import: "default" });
    const items = (Object.values(catalog)[0] as { items: { id: number; kind: string; required_level?: number }[] }).items;
    const easy = items.find((i) => i.kind === "clothing" && (i.required_level ?? 1) === 1)!;
    const hard = items.find((i) => i.kind === "clothing" && (i.required_level ?? 1) > 1)!;
    expect(levelNeeded({ item_def: easy.id }, 1)).toBeNull();
    expect(levelNeeded({ item_def: hard.id }, 1)).toBe(hard.required_level);
    expect(levelNeeded({ item_def: hard.id }, hard.required_level!)).toBeNull();
  });
});

describe("the level-up flourish and the results card (2a, 2d review fix)", () => {
  const up = (level: number, slots: number | null = null) => ({ level, slots });

  it("plays at once while no results card is open", () => {
    const g = new FlourishGate();
    expect(g.offer(up(5))).toEqual(up(5));
    expect(g.waiting).toBe(false);
  });

  it("waits while the card is open and plays when it closes", () => {
    const g = new FlourishGate();
    expect(g.hold(true)).toBeNull();
    expect(g.offer(up(4))).toBeNull();
    expect(g.waiting).toBe(true);
    expect(g.hold(false)).toEqual(up(4));
    expect(g.hold(false)).toBeNull();
  });

  it("takes down a flourish already playing when the card opens, and plays it after", () => {
    const g = new FlourishGate();
    expect(g.hold(true, up(3))).toBeNull();
    expect(g.hold(false)).toEqual(up(3));
  });

  it("shows two waiting level-ups as one, keeping new slots", () => {
    const g = new FlourishGate();
    g.hold(true);
    g.offer(up(4));
    g.offer(up(5, 4));
    expect(g.hold(false)).toEqual({ level: 5, slots: 4 });
    expect(mergeLevelUps(up(7, 5), up(6))).toEqual({ level: 7, slots: 5 });
  });
});
