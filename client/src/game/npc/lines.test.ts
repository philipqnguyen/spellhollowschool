// Line conditions on the client side (lines.ts and sim-wasm's NpcBook): the
// context we build (house, period, cup, worn tags, found secrets), and the
// picks it gives against the shipped roster and castle with hand-made lines,
// so a hint never points at a secret we found and lines follow house, time
// of day and the cup.

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import layoutText from "../../../../data/world/layout.json?raw";
import rosterText from "../../../../data/npcs/roster.json?raw";
import looksText from "../../../../data/npcs/looks.json?raw";
import portraitsText from "../../../../data/npcs/portraits.json?raw";
import shippedLines from "../../../../data/npcs/lines.json?raw";
import { CATALOG, type Catalog } from "../../content/catalog";
import { initSync, NpcBook, type Appearance, type LineContext } from "../../wasm/sim_wasm";
import { Npcs } from "./book";
import { cupStanding, fillLine, giftKind, lineContext, wornTags } from "./lines";
import { giftVars, giftWords, nameSeed } from "./hub";

describe("the cup standing", () => {
  const cup = (a: number, b: number, c: number, d: number) => [
    { id: "roarwing", points: a },
    { id: "burrowsby", points: b },
    { id: "skytalon", points: c },
    { id: "serpentyne", points: d },
  ];
  it("leads, ties or trails, and says nothing before anyone scores", () => {
    expect(cupStanding(cup(10, 5, 3, 1), "roarwing")).toBe("yours_leads");
    expect(cupStanding(cup(10, 10, 3, 1), "burrowsby")).toBe("tied");
    expect(cupStanding(cup(10, 5, 3, 1), "serpentyne")).toBe("other_leads");
    expect(cupStanding(cup(0, 0, 0, 0), "serpentyne")).toBeNull();
    expect(cupStanding(null, "serpentyne")).toBeNull();
  });
});

describe("what we wear", () => {
  it("collects the catalog tags of every worn garment once", () => {
    const catalog = { items: [{ id: 1, tags: ["robe", "formal"] }, { id: 2, tags: ["formal", "boots"] }] } as unknown as Catalog;
    const a = { outfit: { hat: null, body: { itemDef: 1, dyes: [0, 0, 0] }, legs: null, shoes: { itemDef: 2, dyes: [0, 0, 0] } } } as unknown as Appearance;
    expect(wornTags(a, catalog).sort()).toEqual(["boots", "formal", "robe"]);
    expect(wornTags(null)).toEqual([]);
  });

  it("uses the shipped catalog by default", () => {
    const first = CATALOG.items.find((i) => i.kind === "clothing" && i.tags.length);
    if (!first) return;
    const a = { outfit: { hat: null, body: { itemDef: first.id, dyes: [0, 0, 0] }, legs: null, shoes: null } } as unknown as Appearance;
    expect(wornTags(a)).toEqual(expect.arrayContaining(first.tags));
  });
});

describe("placeholders", () => {
  it("fills name, house and a gift's words, and drops what has nothing to fill", () => {
    expect(fillLine("Good day, {name} of {house}.", { name: "Ada", house: "the Badgers" })).toBe("Good day, Ada of the Badgers.");
    expect(fillLine("Take {item}, {name}.", { name: "Ada", house: "x", item: "3 × Moonpetal" })).toBe("Take 3 × Moonpetal, Ada.");
    expect(fillLine("Look near {place} today.", { name: "Ada", house: "x" })).toBe("Look near today.");
  });

  it("words each gift kind", () => {
    expect(giftKind({ kind: "housePoint", points: 1 })).toBe("house_point");
    expect(giftVars({ kind: "housePoint", points: 1 })).toEqual({ points: "1 point" });
    expect(giftVars({ kind: "housePoint", points: 2 })).toEqual({ points: "2 points" });
    expect(giftVars({ kind: "lore", loreId: 4, title: "The Old Bell" })).toEqual({ lore: "The Old Bell" });
    expect(giftVars({ kind: "hint", place: "the Herb Garden" })).toEqual({ place: "the Herb Garden" });
    expect(giftWords({ kind: "housePoint", points: 1 }, "Ours")).toBe("1 point for Ours");
    expect(giftWords({ kind: "housePoint", points: 2 }, "Ours")).toBe("2 points for Ours");
  });

  it("seeds today's hint from the character's name, the same every time", () => {
    expect(nameSeed("Ada")).toBe(nameSeed("Ada"));
    expect(nameSeed("Ada")).not.toBe(nameSeed("Adb"));
  });
});

describe("picking lines with the shipped roster and castle", () => {
  let book: NpcBook;
  const NPC = "portrait_gs_l1";
  const lines = JSON.stringify({
    _doc: "test lines",
    version: 1,
    npcs: {
      [NPC]: [
        { text: "pass", when: "pass" },
        { text: "morning only", when: "talk", period: ["morning"] },
        { text: "night only", when: "talk", period: ["night"] },
        { text: "to roarwing", when: "talk", house: ["roarwing"] },
        { text: "your house leads", when: "talk", cup: "yours_leads" },
        { text: "fine robes", when: "talk", wears: "robe_test_tag" },
        { text: "hint a", when: "hint", secret: "secret_a" },
        { text: "hint b", when: "hint", secret: "secret_b" },
      ],
      ghost_house_a: [
        { text: "same house", when: "talk", house: "same" },
        { text: "other house", when: "talk", house: "other" },
      ],
    },
  });
  const base: LineContext = { house: "burrowsby", period: "afternoon", cup: null, wornTags: [], foundSecrets: [], gift: null };
  /** Every line `when` could pick in `cx` (indices tried with many seeds). */
  const reachable = (npc: string, when: "talk" | "hint" | "pass", cx: LineContext): string[] => {
    const out = new Set<string>();
    for (let s = 0; s < 64; s++) {
      const i = book.pick(npc, when, cx, s * 2654435761, new Uint16Array());
      if (i !== undefined) out.add(book.text(npc, i)!);
    }
    return [...out].sort();
  };

  beforeAll(() => {
    initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
    book = new NpcBook(rosterText, looksText, portraitsText, lines, layoutText);
  });

  it("follows the time of day, our house, the cup and what we wear", () => {
    expect(reachable(NPC, "talk", base)).toEqual([]);
    expect(reachable(NPC, "talk", { ...base, period: "morning" })).toEqual(["morning only"]);
    expect(reachable(NPC, "talk", { ...base, period: "night", house: "roarwing" })).toEqual(["night only", "to roarwing"]);
    expect(reachable(NPC, "talk", { ...base, cup: "yours_leads" })).toEqual(["your house leads"]);
    expect(reachable(NPC, "talk", { ...base, wornTags: ["robe_test_tag"] })).toEqual(["fine robes"]);
  });

  it("never hints at a secret we found, and has no hint once all are found", () => {
    expect(reachable(NPC, "hint", base)).toEqual(["hint a", "hint b"]);
    expect(reachable(NPC, "hint", { ...base, foundSecrets: ["secret_a"] })).toEqual(["hint b"]);
    expect(reachable(NPC, "hint", { ...base, foundSecrets: ["secret_a", "secret_b"] })).toEqual([]);
    expect(book.dailyHint(NPC, { ...base, foundSecrets: ["secret_a", "secret_b"] }, Date.UTC(2026, 10, 6), 7)).toBeUndefined();
    const today = book.dailyHint(NPC, base, Date.UTC(2026, 10, 6, 1), 7);
    // The same all day for one character.
    expect(book.dailyHint(NPC, base, Date.UTC(2026, 10, 6, 23), 7)).toBe(today);
  });

  it("leaves out what was said in the conversation", () => {
    const cx = { ...base, period: "night" as const, house: "roarwing" as const };
    const first = book.pick(NPC, "talk", cx, 1, new Uint16Array())!;
    const second = book.pick(NPC, "talk", cx, 1, Uint16Array.from([first]))!;
    expect(second).not.toBe(first);
    expect(book.pick(NPC, "talk", cx, 1, Uint16Array.from([first, second]))).toBeUndefined();
  });

  it("tells a ghost's own house from the others", () => {
    expect(reachable("ghost_house_a", "talk", { ...base, house: "roarwing", npcHouse: "roarwing" })).toEqual(["same house"]);
    expect(reachable("ghost_house_a", "talk", { ...base, house: "serpentyne", npcHouse: "roarwing" })).toEqual(["other house"]);
  });

  it("builds the context the book reads from what the client knows", () => {
    const cx = lineContext({ house: "skytalon", npcHouse: null, period: "morning", cup: "tied", appearance: null, found: new Set(["secret_a"]) });
    expect(cx).toEqual({ house: "skytalon", npcHouse: null, period: "morning", cup: "tied", wornTags: [], foundSecrets: ["secret_a"], gift: null });
    expect(reachable(NPC, "hint", cx)).toEqual(["hint b"]);
  });

  it("loads the shipped files: 15 to 20 portraits on their walls, every NPC kind, the binding rules", () => {
    const npcs = new Npcs(new NpcBook(rosterText, looksText, portraitsText, shippedLines, layoutText));
    expect(npcs.portraits.length).toBeGreaterThanOrEqual(15);
    expect(npcs.portraits.length).toBeLessThanOrEqual(20);
    const kinds = new Set([...npcs.all()].map((n) => n.kind));
    expect([...kinds].sort()).toEqual(["clerk", "ghost", "portrait", "shopkeeper", "staff", "student"]);
    expect(npcs.npc("clerk_clubs")?.name).toBe("Miss Odette Penrose");
    expect(npcs.rules.pass_cooldown_s).toBeGreaterThanOrEqual(180);
    expect(npcs.keeperOf("owl_exchange")?.kind).toBe("shopkeeper");
    for (const p of npcs.portraits) expect(npcs.book.look(p.npc), p.npc).toBeDefined();
    npcs.dispose();
  });
});
