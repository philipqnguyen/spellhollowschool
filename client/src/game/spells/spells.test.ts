import { describe, expect, it } from "vitest";
import spellsJson from "../../../../data/spells.json";
import type { RoomItem } from "../dorm/room";
import { furnitureDef } from "../dorm/catalog";
import { spellErrorText } from "./controller";
import { findSpell, gestureByKey, isDueling, LEVITATION, lockedText, placeName, SPELLS, spellRgb, unlockText, WAND_LIGHT } from "./data";
import { liftAt } from "./levitate";
import { candidates } from "./targeting";
import { resample } from "./vfx";

describe("the spell data the client reads", () => {
  it("has the wand light and the levitation charm, every gesture it names, and colours", () => {
    expect(WAND_LIGHT.social.kind).toBe("light");
    expect(LEVITATION.social.kind).toBe("levitate");
    for (const s of SPELLS) {
      expect(gestureByKey(s.gesture), s.key).toBeDefined();
      expect(spellRgb(s).every((c) => c >= 0 && c <= 1)).toBe(true);
    }
    expect(SPELLS.length).toBe(spellsJson.spells.length);
    expect(SPELLS.filter(isDueling).length).toBeGreaterThan(0);
    expect(spellRgb(undefined)).toEqual([1, 1, 1]);
  });

  it("names where spells are taught from the timetable and the venues", () => {
    expect(placeName(WAND_LIGHT.unlock.at)).toMatch(/ class$/);
    const duel = SPELLS.find(isDueling)!;
    expect(placeName(duel.unlock.at)).toMatch(/^the /);
    expect(unlockText(LEVITATION)).toContain(`level ${LEVITATION.unlock.level}`);
    expect(lockedText(LEVITATION, 1)).toContain(`Reach level ${LEVITATION.unlock.level}`);
    expect(lockedText(LEVITATION, 20)).toMatch(/^Attend /);
    expect(lockedText(duel, 20)).toMatch(/^Visit the /);
  });

  it("finds a spell by name, incantation, key or a unique prefix", () => {
    expect(findSpell(WAND_LIGHT.name)?.id).toBe(WAND_LIGHT.id);
    expect(findSpell(WAND_LIGHT.incantation.toUpperCase())?.id).toBe(WAND_LIGHT.id);
    expect(findSpell(LEVITATION.key)?.id).toBe(LEVITATION.id);
    expect(findSpell(LEVITATION.name.slice(0, 5))?.id).toBe(LEVITATION.id);
    expect(findSpell("")).toBeUndefined();
    expect(findSpell("no such spell at all")).toBeUndefined();
  });

  it("explains refusals", () => {
    expect(spellErrorText(WAND_LIGHT.id, "cooldown", null)).toContain(WAND_LIGHT.name);
    expect(spellErrorText(WAND_LIGHT.id, "not_here", { kind: "arena", duelId: 1, venue: "v", layout: "l" })).toContain("dueling");
  });
});

describe("a floating piece", () => {
  const social = LEVITATION.social.kind === "levitate" ? LEVITATION.social : null;

  it("rises, bobs around its height and settles back down", () => {
    const d = social!.duration_ms;
    expect(liftAt(0, d)).toBe(0);
    expect(liftAt(-5, d)).toBe(0);
    expect(liftAt(d, d)).toBe(0);
    expect(liftAt(d + 100, d)).toBe(0);
    let max = 0;
    for (let t = 0; t <= d; t += 10) {
      const y = liftAt(t, d);
      expect(y).toBeGreaterThanOrEqual(0);
      max = Math.max(max, y);
    }
    expect(max).toBeGreaterThan(social!.height_m * 0.9);
    expect(max).toBeLessThanOrEqual(social!.height_m + social!.bob_m + 1e-9);
    // Close to the ground just after the start and just before the end.
    expect(liftAt(20, d)).toBeLessThan(0.1);
    expect(liftAt(d - 20, d)).toBeLessThan(0.05);
  });
});

describe("choosing what to levitate", () => {
  const def = [1001, 1002, 1003, 1004, 1005].find((id) => furnitureDef(id))!;
  const item = (itemId: number, x: number, z: number): RoomItem => ({ itemId, itemDef: def, dyes: [0, 0, 0], pos: [x, 0, z], yawUnits: 0 });

  it("offers the pieces in reach, nearest first, starting with the one ahead", () => {
    const room = [item(1, 0, -3), item(2, 0, 2), item(3, 1.5, 0), item(4, 0, 40)];
    const { list, first } = candidates(room, [0, 0, 0], [0, -1]);
    expect(list.map((c) => c.item.itemId)).toEqual([3, 2, 1]);
    expect(list[first]!.item.itemId).toBe(1); // straight ahead (north), though farther
    expect(candidates(room, [0, 0, 0], [0, 1]).list[candidates(room, [0, 0, 0], [0, 1]).first]!.item.itemId).toBe(2);
    expect(candidates([item(5, 50, 50)], [0, 0, 0], [0, 1]).list).toEqual([]);
  });
});

describe("air trails", () => {
  it("resamples a gesture evenly along its length", () => {
    const flat = resample([[0, 0], [100, 0], [100, 100]], 5);
    expect([...flat]).toEqual([0, 0, 50, 0, 100, 0, 100, 50, 100, 100]);
    expect([...resample([[3, 4]], 3)]).toEqual([3, 4, 3, 4, 3, 4]);
  });
});
