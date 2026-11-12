import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import gesturesText from "../../../../data/classes/gestures.json?raw";
import { initSync, PickScorer, type ClassInfo, type LessonBeat } from "../../wasm/sim_wasm";
import { parseLorePage } from "../../ui/lore";
import { fortuneBlurb, fortuneName } from "../../ui/potions-state";
import classFiles from "./class-files";
import { ingredientRgb } from "./fx/potions";
import { targetOf } from "./fx/transfiguration";
import { CLASS_HITS } from "./fx";
import { LessonState } from "./lesson";
import { classPicks, pickCue, PICK_NOW_MS, pickSet, resolvePick } from "./picks";

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

const info = (cls: string): ClassInfo => ({
  class: cls,
  session: 64 * 3,
  section: 0,
  startUnixMs: 1_000_000,
  joinUntilUnixMs: 1_180_000,
  endUnixMs: 1_540_000,
  professor: 77,
  phase: { kind: "gathering" },
  phaseUntilUnixMs: 1_185_000,
});

const pickBeats = (t0: number, n: number): LessonBeat[] => Array.from({ length: n }, (_, i) => ({ beat: i, prompt: { kind: "pick", prompt: i, options: [0, 1, 2, 3] }, atUnixMs: t0 + i * 3500 }));

describe("pick vocabularies", () => {
  it("resolve every pick class's beats to words and icons", () => {
    for (const id of ["potions", "transfiguration"]) {
      const picks = classPicks(id);
      expect(picks, id).not.toBeNull();
      const file = classFiles[id]!;
      for (const step of file.lesson.filter((a) => a.kind === "practice")) {
        for (const b of step.beats ?? []) {
          const prompt = picks!.prompts.findIndex((p) => p.key === b.prompt);
          const options = (b.options ?? []).map((o) => picks!.options.findIndex((p) => p.key === o));
          expect(prompt, `${id} ${b.prompt}`).toBeGreaterThanOrEqual(0);
          expect(options.every((o) => o >= 0)).toBe(true);
          expect(options.length).toBeGreaterThanOrEqual(2);
          expect(options.length).toBeLessThanOrEqual(6);
          const r = resolvePick(picks, prompt, options);
          expect(r.prompt.label.length).toBeGreaterThan(0);
          expect(r.options.map((o) => o.key)).toEqual(b.options);
        }
      }
    }
    expect(classPicks("charms")).toBeNull();
    expect(resolvePick(null, 3, [1]).prompt.label).toBe("?");
  });

  it("show ingredients in their own item's colour, and name what an object becomes", () => {
    const shelf = pickSet("potions_ingredients")!;
    for (const p of shelf) expect(ingredientRgb(p.item), p.key).not.toBeNull();
    expect(ingredientRgb(undefined)).toBeNull();
    expect(targetOf("Teacup → giant teacup")).toBe("giant teacup");
    expect(targetOf("Quill → tiny silver quill (1 of 2)")).toBe("tiny silver quill");
    expect(Object.keys(CLASS_HITS).sort()).toEqual(["creatures", "divination", "herbology", "history", "potions", "transfiguration"]);
  });

  it("swap the Transfiguration card and puff the Potions cauldron on a hit", () => {
    const calls: string[] = [];
    const ctx = { desk: () => [1, 1, 1] as [number, number, number], burst: () => void calls.push("burst"), swap: (t: string) => void calls.push(`swap ${t}`) };
    const t = pickSet("transfiguration_puzzles")![0]!;
    CLASS_HITS.transfiguration!(ctx, t, pickSet("transfiguration_changes")![0]!);
    expect(calls).toEqual(["swap giant teacup", "burst"]);
    calls.length = 0;
    const herb = pickSet("potions_ingredients")![0]!;
    CLASS_HITS.potions!(ctx, herb, herb);
    expect(calls).toEqual(["burst", "burst"]);
  });
});

describe("a lesson of picks", () => {
  it("follows pick beats, matches answers to the beat in the window, and counts them", () => {
    const l = new LessonState("potions");
    l.join(info("potions"));
    l.setPhase({ kind: "practice", round: 1 }, 2_000_000);
    l.startRound(1, 1400, pickBeats(10_000, 3));
    expect(l.picking).toBe(true);
    const b0 = l.current(9_000)!;
    expect(b0.kind).toBe("pick");
    expect(b0.options).toEqual([0, 1, 2, 3]);
    expect(b0.gesture).toBe(0);
    expect(l.beatFor(10_000 - 1401)).toBeNull();
    expect(l.beatFor(9_000)?.beat).toBe(0);
    l.sent(b0, { score: 100, grade: "perfect", rejected: null }, 2);
    expect(b0.choice).toBe(2);
    // A wrong pick: a score of 0, not a refusal.
    l.scored(1, 0, { score: 0, grade: "miss", rejected: null });
    const b1 = l.beatFor(13_600)!;
    l.sent(b1, { score: 90, grade: "great", rejected: null }, 0);
    l.scored(1, 1, { score: 88, grade: "great", rejected: null });
    l.expire(99_999);
    expect(l.summary()).toEqual({ beats: 3, answered: 2, scored: 2, average: 44, perfect: 0 });
  });

  it("previews a right pick's timing with sim-core's scorer", () => {
    const s = new PickScorer(gesturesText);
    expect(s.preview(1400, 0).score).toBe(100);
    expect(s.preview(1400, 1300).score).toBeLessThan(100);
    expect(s.preview(1400, -1500).rejected).toBe("bad_timing");
    s.free();
  });
});

describe("lore and fortunes", () => {
  it("reads the lore route's keyset pages, titles and text from the server", () => {
    expect(parseLorePage({ items: [{ id: 1, title: "t", text: "x", unlocked_at: "2026-10-27T00:00:00Z" }, { id: 2 }, { nope: 4 }], next: "WzEsMl0" })).toEqual({
      entries: [
        { id: 1, title: "t", text: "x" },
        { id: 2, title: "", text: "" },
      ],
      next: "WzEsMl0",
    });
    expect(parseLorePage({ items: [{ id: 6, title: 7 }], next: null })).toEqual({ entries: [{ id: 6, title: "", text: "" }], next: null });
    expect(parseLorePage(null)).toEqual({ entries: [], next: null });
  });

  it("bundles no lore text: no client module imports the server's lore file", () => {
    const sources = import.meta.glob("../../**/*.ts", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.endsWith(".test.ts"))
      .filter(([, text]) => /(?:from|import)\s*\(?\s*["'][^"']*lore\.json|glob[^)]*classes\/lore/.test(text))
      .map(([path]) => path);
    expect(Object.keys(sources).length).toBeGreaterThan(50);
    expect(offenders).toEqual([]);
  });

  it("names every fortune", () => {
    for (const k of ["luck", "quick_hands", "keen_mind"] as const) {
      expect(fortuneName(k).length).toBeGreaterThan(3);
      expect(fortuneBlurb(k).length).toBeGreaterThan(10);
    }
  });
});

describe("the pick cue", () => {
  it('says "Now!" only where a right pick scores well, not as the window opens', () => {
    const scorer = new PickScorer(gesturesText);
    const windowMs = 1600;
    // The window's first instant counts, but scores poorly: no "Now!" there.
    const early = pickCue(windowMs, windowMs, true);
    expect(early.open).toBe(true);
    expect(early.onBeat).toBe(false);
    expect(early.countdown).toBe("in 1.6 s");
    expect(scorer.preview(windowMs, -windowMs).score).toBeLessThan(70);
    // On the cue, either side of the beat: a right pick scores at least 90.
    for (const lead of [PICK_NOW_MS, 0, -PICK_NOW_MS]) {
      const cue = pickCue(lead, windowMs, true);
      expect(cue.onBeat).toBe(true);
      expect(cue.countdown).toBe("Now!");
      expect(scorer.preview(windowMs, -lead).score).toBeGreaterThanOrEqual(90);
    }
    expect(pickCue(-windowMs - 1, windowMs, true).open).toBe(false);
    expect(pickCue(0, windowMs, false)).toEqual({ open: false, onBeat: false, countdown: "" });
    scorer.free();
  });
});
