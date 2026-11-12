// The anti-annoyance rules for talking portraits (chatter.ts): the
// pass-by cooldowns per portrait, the one-line-per-screen gap, Quiet and
// Off, and how a conversation opens and ends.

import { describe, expect, it } from "vitest";
import { CONVERSATION_IDLE_MS, continueConversation, nextLine, parseChatter, PassWatch, type Conversation, type PassStore } from "./chatter";
import type { LineWhen } from "../../wasm/sim_wasm";

const RULES = { pass_cooldown_s: 300, pass_gap_s: 20, pass_line_pct: 40, talk_lines_max: 4, hint_pct: 15 };
const ALWAYS = () => 0; // a roll under every percentage: a line when the gap allows
const NEVER = () => 0.99;

describe("passing a talking portrait", () => {
  it("notices us once as we come into its radius, then never again within five minutes", () => {
    const w = new PassWatch(RULES);
    expect(w.step(0, ["a"], "on", ALWAYS)).toEqual({ npc: "a", kind: "line" });
    // Still in range: nothing more.
    expect(w.step(250, ["a"], "on", ALWAYS)).toBeNull();
    // Out and back in a minute later: still cooling down.
    expect(w.step(1_000, [], "on", ALWAYS)).toBeNull();
    expect(w.step(60_000, ["a"], "on", ALWAYS)).toBeNull();
    expect(w.step(61_000, [], "on", ALWAYS)).toBeNull();
    // Five minutes after it noticed us, it may again.
    expect(w.step(300_000, ["a"], "on", ALWAYS)).toEqual({ npc: "a", kind: "line" });
  });

  it("gives at most one line on screen per 20 s: a second portrait nods", () => {
    const w = new PassWatch(RULES);
    expect(w.step(0, ["a"], "on", ALWAYS)?.kind).toBe("line");
    expect(w.step(5_000, ["a", "b"], "on", ALWAYS)).toEqual({ npc: "b", kind: "nod" });
    // A third after the gap may speak.
    expect(w.step(25_000, ["a", "b", "c"], "on", ALWAYS)).toEqual({ npc: "c", kind: "line" });
  });

  it("nods instead of speaking when the 40 % roll fails", () => {
    const w = new PassWatch(RULES);
    expect(w.step(0, ["a"], "on", NEVER)).toEqual({ npc: "a", kind: "nod" });
    // A nod also starts the cooldown.
    expect(w.step(1_000, [], "on", ALWAYS)).toBeNull();
    expect(w.step(2_000, ["a"], "on", ALWAYS)).toBeNull();
  });

  it("is one outcome a step: a row of portraits entered at once gives one", () => {
    const w = new PassWatch(RULES);
    expect(w.step(0, ["a", "b", "c"], "on", ALWAYS)).toEqual({ npc: "a", kind: "line" });
    // The others are already in range: they do not chime in.
    expect(w.step(250, ["a", "b", "c"], "on", ALWAYS)).toBeNull();
  });

  it("says nothing and nods at nobody while chatter is Quiet or Off, and keeps no cooldown for it", () => {
    for (const mode of ["quiet", "off"] as const) {
      const w = new PassWatch(RULES);
      expect(w.step(0, ["a"], mode, ALWAYS)).toBeNull();
      expect(w.noticedAt("a")).toBeUndefined();
      // Switched back on: the next pass is noticed (a fresh entry into the radius).
      expect(w.step(1_000, [], "on", ALWAYS)).toBeNull();
      expect(w.step(2_000, ["a"], "on", ALWAYS)?.npc).toBe("a");
    }
  });

  it("keeps its cooldowns across a reload through the store (running ones only)", () => {
    let saved: Record<string, number> = {};
    const store: PassStore = { load: () => saved, save: (n) => (saved = { ...n }) };
    const w = new PassWatch(RULES, store);
    w.step(1_000_000, ["a"], "on", ALWAYS);
    expect(saved).toEqual({ a: 1_000_000 });
    const again = new PassWatch(RULES, store);
    expect(again.step(1_100_000, ["a"], "on", ALWAYS)).toBeNull();
    expect(again.step(1_300_001, ["b"], "on", ALWAYS)?.npc).toBe("b");
    // `a` cooled down by then: dropped from the store.
    expect(Object.keys(saved)).toEqual(["b"]);
  });

  it("survives a store that throws or holds rubbish", () => {
    const bad: PassStore = { load: () => ({ a: "x" as unknown as number }), save: () => undefined };
    const w = new PassWatch(RULES, bad);
    expect(w.step(0, ["a"], "on", ALWAYS)?.npc).toBe("a");
  });
});

describe("the chatter setting", () => {
  it("reads On by default and anything unknown as On", () => {
    expect(parseChatter(undefined)).toBe("on");
    expect(parseChatter("loud")).toBe("on");
    expect(parseChatter("quiet")).toBe("quiet");
    expect(parseChatter("off")).toBe("off");
  });
});

describe("a conversation", () => {
  /** A pick over `n` talk lines and the hint lines given (the first not excluded). */
  const picker =
    (talk: number, hints: number[] = []) =>
    (when: LineWhen, exclude: readonly number[]) => {
      const pool = when === "hint" ? hints : when === "talk" ? Array.from({ length: talk }, (_, i) => 100 + i) : [];
      return pool.find((i) => !exclude.includes(i));
    };
  const fresh = (): Conversation => ({ npc: "p", said: [], at: 0 });

  it("opens with today's hint on a daily-hint portrait", () => {
    expect(nextLine(fresh(), RULES, { dailyHint: 7, hints: true, roll: 0.99, pick: picker(5) })).toEqual({ index: 7, when: "hint" });
  });

  it("opens with a hint toward an unfound secret 15 % of the time, else talks", () => {
    expect(nextLine(fresh(), RULES, { dailyHint: undefined, hints: true, roll: 0.1, pick: picker(5, [3]) })).toEqual({ index: 3, when: "hint" });
    expect(nextLine(fresh(), RULES, { dailyHint: undefined, hints: true, roll: 0.2, pick: picker(5, [3]) })).toEqual({ index: 100, when: "talk" });
    // No hint line holds (every secret found): it talks.
    expect(nextLine(fresh(), RULES, { dailyHint: undefined, hints: true, roll: 0, pick: picker(5, []) })).toEqual({ index: 100, when: "talk" });
    // Residents that are not portraits never open with a hint.
    expect(nextLine(fresh(), RULES, { dailyHint: undefined, hints: false, roll: 0, pick: picker(5, [3]) })?.when).toBe("talk");
  });

  it("says each line once and ends after four", () => {
    const conv = fresh();
    const said: number[] = [];
    for (;;) {
      const next = nextLine(conv, RULES, { dailyHint: undefined, hints: false, roll: 1, pick: picker(10) });
      if (!next) break;
      conv.said.push(next.index);
      said.push(next.index);
    }
    expect(said).toEqual([100, 101, 102, 103]);
  });

  it("ends early when it has nothing new to say", () => {
    const conv = fresh();
    conv.said.push(100, 101);
    expect(nextLine(conv, RULES, { dailyHint: undefined, hints: false, roll: 1, pick: picker(2) })).toBeNull();
  });

  it("continues with the same NPC, starts over with another or after a long pause", () => {
    const a = continueConversation(null, "p", 0);
    a.said.push(1);
    expect(continueConversation(a, "p", 1000)).toBe(a);
    expect(continueConversation(a, "q", 1000).said).toEqual([]);
    expect(continueConversation(a, "p", CONVERSATION_IDLE_MS + 1).said).toEqual([]);
  });
});
