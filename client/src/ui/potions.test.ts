import { describe, expect, it } from "vitest";
import { CATALOG } from "../content/catalog";
import type { BagItem } from "./crafting-api";
import { findMessage, FindCooldowns, formatWait, secondsUntil } from "./finds-state";
import { ConsentQueue, durationText, effectName, mimicDraughtCandidates, MIMIC_DRAUGHT_RANGE_M, potionEffect, potionStacks } from "./potions-state";
import { EFFECT_KINDS } from "../game/effects/state";

const bag = (id: number, def: number, quantity: number, primary = 0, location: BagItem["location"] = "inventory"): BagItem => ({ id, item_def: def, quantity, location, dyes: [primary, 0, 0], slot: null, maker: "Basil", finish: "none", created_at: "" });

describe("potions", () => {
  it("names every effect from the catalog's potions", () => {
    for (const kind of EFFECT_KINDS) {
      const name = effectName(kind);
      expect(name, kind).not.toBe(kind);
      expect(name).not.toMatch(/\(/);
      expect(durationText(kind)).toMatch(/min$/);
    }
  });

  it("groups potion stacks by definition and colour, inventory only", () => {
    const glow = CATALOG.items.find((i) => i.name === "Glow Draught")!.id;
    expect(potionEffect(glow)).toBe("glow");
    expect(potionEffect(3001)).toBeNull();
    const stacks = potionStacks([bag(1, glow, 2, 22), bag(2, glow, 3, 22), bag(3, glow, 1, 34), bag(4, glow, 5, 22, "mail"), bag(5, 3001, 9)]);
    expect(stacks).toEqual([
      { itemId: 1, itemDef: glow, effect: "glow", colour: 22, count: 5 },
      { itemId: 3, itemDef: glow, effect: "glow", colour: 34, count: 1 },
    ]);
  });

  it("offers Mimic Draught only for online friends within range, nearest first", () => {
    const friends = new Set(["Ada", "Basil"]);
    const c = mimicDraughtCandidates(
      [
        { id: 1, name: "Basil", distance: 4 },
        { id: 2, name: "Ada", distance: 2 },
        { id: 3, name: "Cyril", distance: 1 },
        { id: 4, name: "Ada", distance: MIMIC_DRAUGHT_RANGE_M + 1 },
      ],
      (n) => friends.has(n),
    );
    expect(c.map((x) => x.id)).toEqual([2, 1]);
  });

  it("keeps one consent request per asker and lets them lapse", () => {
    const q = new ConsentQueue();
    q.add(1, "Ada", 30, 0);
    q.add(2, "Ada", 30, 1000); // replaces Ada's first
    q.add(3, "Basil", 10, 0);
    expect(q.items.map((r) => r.requestId)).toEqual([2, 3]);
    expect(q.expire(10_000).map((r) => r.requestId)).toEqual([3]);
    expect(q.items.map((r) => r.requestId)).toEqual([2]);
    q.remove(2);
    expect(q.items).toEqual([]);
  });
});

describe("finds", () => {
  it("words each outcome", () => {
    const found = findMessage({ kind: "found", items: [{ itemDef: 3001, quantity: 3 }], crowns: 5 });
    expect(found.tone).toBe("found");
    expect(found.lines).toEqual(["3 × Wool Skein", "5 Crowns"]);
    expect(findMessage({ kind: "found", items: [], crowns: 1 }).lines).toEqual(["1 Crown"]);
    expect(findMessage({ kind: "cooldown", readyInSecs: 3 * 3600 + 600 }).lines[0]).toBe("Come back in 3 h 10 min.");
    expect(findMessage({ kind: "nothing" }).tone).toBe("nothing");
    expect(findMessage({ kind: "failed" }).tone).toBe("failed");
  });

  it("formats waits", () => {
    expect(formatWait(40)).toBe("40 s");
    expect(formatWait(600)).toBe("10 min");
    expect(formatWait(7200)).toBe("2 h");
  });

  it("tracks spots on cooldown until they are ready", () => {
    const c = new FindCooldowns();
    c.set("find_a", 60, 0);
    c.set("find_b", 10, 0);
    expect(c.isCooling("find_a", 5000)).toBe(true);
    expect(c.expire(20_000)).toEqual(["find_b"]);
    expect(c.isCooling("find_b", 20_000)).toBe(false);
    expect([...c.spots()]).toEqual(["find_a"]);
    expect(secondsUntil("1970-01-01T00:01:00Z", 0)).toBe(60);
    expect(secondsUntil("garbage", 0)).toBe(0);
  });
});
