// The class effect hooks: Defence's drill, Astronomy's sky pad,
// Divination's close-ups and History's words-only tray; the
// props stages of Herbology (plants that lunge, recoil and snap) and Creature
// Keeping (needs over the creatures, a settle or a startle).

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import scheduleJson from "../../../../../data/classes/schedule.json";
import { initSync, type GestureResult, type LessonBeat } from "../../../wasm/sim_wasm";
import { gestureByKey, GESTURES } from "../../spells/data";
import classFiles from "../class-files";
import { LessonState } from "../lesson";
import { classPicks, pickSet, sharedIcon } from "../picks";
import { hasGlyph } from "../../../ui/classes-pick";
import { constellationStars, isConstellation, starField } from "./astronomy";
import { parseOpenSky } from "../../zones";
import { CHARGE_MS, DEFENCE_SPELLS, DefenceDrill, HOLD_MS, isShieldBeat, OUT_MS, SHIELD_MS, telegraph, TELEGRAPH_MS } from "./defence";
import { closeupOf, symbolOf } from "./divination";
import { CLASS_DRILLS, CLASS_HITS, CLASS_STAGES, CLASS_TRACED } from "./index";
import type { DrillContext, DrillVfx } from "./hooks";
import { CREATURE_PROPS, creaturesStage } from "./creatures";
import { HERBOLOGY_PROPS, herbologyStage, LUNGE_TILT } from "./herbology";
import { cueStrength, CUE_FULL_MS, CUE_LEAD_MS, propFor, REACT_MS } from "./stage";
import type { BeatState } from "../lesson";

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

const id = (key: string) => gestureByKey(key)!.id;

/** A VFX that records what was drawn. */
function recorder(): DrillVfx & { calls: string[] } {
  const calls: string[] = [];
  const rec =
    (name: string) =>
    (..._args: unknown[]) =>
      void calls.push(name);
  return { calls, bolt: rec("bolt"), charge: rec("charge"), shield: rec("shield"), flash: rec("flash"), muzzle: rec("muzzle"), fizzle: rec("fizzle") };
}

function drillCtx(vfx: DrillVfx): DrillContext {
  return { self: () => ({ x: 0, y: 0, z: 4, yaw: Math.PI }), professor: () => ({ x: 0, y: 0, z: -6, yaw: 0 }), vfx };
}

const pass = (score: number): GestureResult => ({ score, grade: score >= 88 ? "perfect" : score >= 70 ? "great" : score >= 45 ? "good" : "miss", rejected: null }) as GestureResult;

function defenceRound(t0: number, keys: string[]): LessonState {
  const l = new LessonState("defence");
  l.setPhase({ kind: "practice", round: 1 }, t0 + 60_000);
  const beats: LessonBeat[] = keys.map((k, i) => ({ beat: i, prompt: { kind: "trace", gesture: id(k) }, atUnixMs: t0 + i * 3000 }));
  l.startRound(1, 700, beats);
  return l;
}

describe("Defensive Magic", () => {
  it("casts the shield, Titillara and Repulsa with the gestures the professor demonstrates", () => {
    expect(DEFENCE_SPELLS.get("shield_arc")?.key).toBe("scutara");
    expect(DEFENCE_SPELLS.get("spiral")?.key).toBe("titillara");
    expect(DEFENCE_SPELLS.get("push")?.key).toBe("repulsa");
    // Every gesture the lesson asks for has a spell to show.
    for (const a of classFiles.defence!.lesson) for (const b of a.beats ?? []) expect(DEFENCE_SPELLS.has(b.gesture!)).toBe(true);
    expect(Object.keys(CLASS_DRILLS)).toEqual(["defence"]);
  });

  it("telegraphs the professor's cast so the bolt lands on the beat, then waits for the verdict", () => {
    const at = 100_000;
    expect(telegraph(at, 700, at - TELEGRAPH_MS - CHARGE_MS - 1).stage).toBe("none");
    expect(telegraph(at, 700, at - TELEGRAPH_MS - CHARGE_MS / 2)).toEqual({ stage: "charge", progress: 0.5 });
    expect(telegraph(at, 700, at - TELEGRAPH_MS / 2)).toEqual({ stage: "flight", progress: 0.5 });
    expect(telegraph(at, 700, at).stage).toBe("held");
    expect(telegraph(at, 700, at + 700 + HOLD_MS).stage).toBe("held");
    expect(telegraph(at, 700, at + 700 + HOLD_MS + 1).stage).toBe("none");
  });

  it("draws the telegraph only on shield beats, and blocks it with a passed trace", () => {
    const t0 = 50_000;
    const l = defenceRound(t0, ["shield_arc", "spiral", "push"]);
    const [shield, spiral] = l.round!.beats;
    expect(isShieldBeat(shield!)).toBe(true);
    expect(isShieldBeat(spiral!)).toBe(false);
    const vfx = recorder();
    const ctx = drillCtx(vfx);
    const d = new DefenceDrill();
    const round = { windowMs: 700, beats: l.round!.beats };
    // The charge, then the bolt in flight, for the shield beat only (the spiral's beat is 3 s later).
    d.frame(ctx, round, t0 - TELEGRAPH_MS - 100);
    expect(vfx.calls).toEqual(["charge"]);
    vfx.calls.length = 0;
    d.frame(ctx, round, t0 - 200);
    expect(vfx.calls).toEqual(["bolt"]);
    // Traced and passed: Scutara raised, the bolt breaks on it and is gone.
    vfx.calls.length = 0;
    l.sent(shield!, pass(80));
    l.scored(1, shield!.beat, pass(80));
    d.scored(ctx, shield!, pass(80), t0 + 300);
    expect(vfx.calls).toEqual(["flash", "flash"]);
    vfx.calls.length = 0;
    d.frame(ctx, round, t0 + 400);
    expect(vfx.calls).toEqual(["shield"]);
    vfx.calls.length = 0;
    d.frame(ctx, round, t0 + 300 + SHIELD_MS + 1);
    expect(vfx.calls).toEqual([]);
  });

  it("lets a missed shield beat through, once", () => {
    const t0 = 50_000;
    const l = defenceRound(t0, ["shield_arc"]);
    const vfx = recorder();
    const ctx = drillCtx(vfx);
    const d = new DefenceDrill();
    for (const b of l.expire(t0 + 701)) d.missed(ctx, b, t0 + 701);
    expect(vfx.calls).toEqual(["flash", "flash"]);
    d.missed(ctx, l.round!.beats[0]!, t0 + 800);
    expect(vfx.calls.length).toBe(2);
    // A missed beat draws no bolt any more.
    vfx.calls.length = 0;
    d.frame(ctx, { windowMs: 700, beats: l.round!.beats }, t0 + 750);
    expect(vfx.calls).toEqual([]);
  });

  it("sends a passed spell at the professor, who shields; a failed one fizzles", () => {
    const t0 = 50_000;
    const l = defenceRound(t0, ["spiral", "push"]);
    const [spiral, push] = l.round!.beats;
    const vfx = recorder();
    const ctx = drillCtx(vfx);
    const d = new DefenceDrill();
    d.scored(ctx, spiral!, pass(75), t0);
    expect(vfx.calls).toEqual(["muzzle"]);
    vfx.calls.length = 0;
    d.frame(ctx, null, t0 + OUT_MS / 2);
    expect(vfx.calls).toEqual(["bolt"]);
    vfx.calls.length = 0;
    d.frame(ctx, null, t0 + OUT_MS + 1);
    expect(vfx.calls).toEqual(["flash", "flash", "shield"]);
    vfx.calls.length = 0;
    d.scored(ctx, push!, pass(0), t0 + 3000);
    expect(vfx.calls).toEqual(["fizzle"]);
    d.clear();
    vfx.calls.length = 0;
    d.frame(ctx, null, t0 + 3001);
    expect(vfx.calls).toEqual([]);
  });
});

describe("Astronomy", () => {
  it("knows its constellations and draws their stars from the templates' vertices", () => {
    const consts = GESTURES.filter((g) => isConstellation(g.key));
    expect(consts.map((g) => g.key)).toEqual(["const_lantern", "const_heron", "const_kettle", "const_hound", "const_owl"]);
    expect(isConstellation("shield_arc")).toBe(false);
    expect(isConstellation(undefined)).toBe(false);
    for (const g of consts) {
      const stars = constellationStars(g.points);
      expect(stars.length).toBeGreaterThanOrEqual(4);
      expect(stars.length).toBeLessThanOrEqual(g.points.length);
      for (const s of stars) for (const v of s) expect(Math.abs(v)).toBeLessThanOrEqual(100);
    }
    // A repeated vertex (a closed shape) is one star.
    expect(constellationStars([[0, 0], [10, 0], [0, 0]])).toEqual([[0, 0], [10, 0]]);
    expect(Object.keys(CLASS_TRACED)).toEqual(["astronomy"]);
  });

  it("opens Astronomy's room and the paddock to the sky by the rooms manifest's flag", () => {
    // build_school.py writes `"open_sky": true` on the rooms of OPEN_SKY_ROOMS into instances/school.json.
    const py = readFileSync(new URL("../../../../../tools/blender/world/build_school.py", import.meta.url), "utf8");
    const tuple = /^OPEN_SKY_ROOMS = \(([^)]*)\)/m.exec(py);
    expect(tuple, "OPEN_SKY_ROOMS in build_school.py").not.toBeNull();
    const open = [...tuple![1]!.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]!);
    const rooms = Object.fromEntries(scheduleJson.classes.map((c) => [c.layout, open.includes(c.layout) ? { file: `${c.layout}.glb`, open_sky: true } : { file: `${c.layout}.glb` }]));
    const sky = parseOpenSky({ version: 1, rooms });
    // Astronomy's room, the creature paddock and the practice pitch are open to the sky; no other class's is.
    for (const c of scheduleJson.classes) expect(sky.has(c.layout), c.id).toBe(c.id === "astronomy" || c.id === "creatures" || c.id === "flying");
    expect(sky.has("class_paddock")).toBe(true);
    expect(sky.has("class_greenhouse")).toBe(false);
    // Only a real `true` opens a room; a missing or broken manifest opens none.
    expect([...parseOpenSky({ rooms: { a: { open_sky: "yes" }, b: { open_sky: 1 }, c: { open_sky: true } } })]).toEqual(["c"]);
    expect(parseOpenSky(null).size).toBe(0);
    expect(parseOpenSky({ rooms: [] }).size).toBe(0);
  });

  it("scatters the same background stars every time, over the whole pad", () => {
    const a = starField(90, 7);
    expect(a).toEqual(starField(90, 7));
    expect(a).not.toEqual(starField(90, 8));
    expect(a.length).toBe(90);
    for (const s of a) {
      expect(Math.abs(s.x)).toBeLessThanOrEqual(110);
      expect(Math.abs(s.y)).toBeLessThanOrEqual(110);
      expect(s.size).toBeGreaterThan(0);
      expect(s.bright).toBeGreaterThan(0);
      expect(s.bright).toBeLessThanOrEqual(1);
    }
    // Spread over all four quarters.
    for (const [sx, sy] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) expect(a.some((s) => s.x * sx! > 20 && s.y * sy! > 20)).toBe(true);
  });

  it("twinkles over the telescope for a charted constellation only", () => {
    const calls: string[] = [];
    const ctx = { desk: () => [0, 1, 0] as [number, number, number], burst: () => void calls.push("burst"), swap: () => undefined };
    CLASS_TRACED.astronomy!(ctx, "const_owl");
    CLASS_TRACED.astronomy!(ctx, "spiral");
    expect(calls).toEqual(["burst"]);
  });
});

describe("Divination", () => {
  it("picks the teacup or the crystal ball by the prompt's prefix", () => {
    expect(closeupOf("cup_key")).toBe("cup");
    expect(closeupOf("ball_moon")).toBe("ball");
    expect(closeupOf("q_accord_year")).toBeNull();
    expect(symbolOf("ball_moon")).toBe("moon");
    const prompts = pickSet("divination_symbols")!;
    for (const p of prompts) expect(closeupOf(p.key)).not.toBeNull();
    expect(prompts.filter((p) => closeupOf(p.key) === "cup").length).toBe(10);
  });

  it("has a glyph for every symbol until its art exists", () => {
    for (const p of pickSet("divination_symbols")!) expect(hasGlyph(p.icon)).toBe(true);
    // And for every class icon a pick shows (icons/class/...).
    for (const set of ["transfiguration_puzzles", "transfiguration_changes", "history_questions", "history_answers", "divination_meanings", "herbology_plants", "herbology_tools", "creatures_needs", "creatures_care"]) {
      for (const p of pickSet(set)!) if (p.icon.startsWith("icons/class/")) expect(hasGlyph(p.icon)).toBe(true);
    }
  });
});

describe("History of Magic and the words-only tray", () => {
  it("shows History's questions and answers, and Divination's meanings, as words alone", () => {
    const history = classPicks("history")!;
    expect(sharedIcon(history.prompts)).toBe(true);
    expect(sharedIcon(history.options)).toBe(true);
    const divination = classPicks("divination")!;
    expect(sharedIcon(divination.prompts)).toBe(false);
    expect(sharedIcon(divination.options)).toBe(true);
    // Potions and Transfiguration keep their icons.
    for (const c of ["potions", "transfiguration"]) {
      const p = classPicks(c)!;
      expect(sharedIcon(p.prompts)).toBe(false);
      expect(sharedIcon(p.options)).toBe(false);
    }
  });

  it("gives questions long windows, and reacts to right answers", () => {
    const rounds = classFiles.history!.lesson.filter((a) => a.kind === "practice");
    for (const r of rounds) expect(r.window_ms).toBeGreaterThanOrEqual(6000);
    expect(CLASS_HITS.history).toBeDefined();
    expect(CLASS_HITS.divination).toBeDefined();
  });
});

/** A pick beat of round `windowMs` for prompt `key` of class `classId`, on `atMs`. */
function pickBeat(classId: string, key: string, atMs: number, beat = 1): BeatState {
  const prompts = classPicks(classId)!.prompts;
  const prompt = prompts.findIndex((p) => p.key === key);
  expect(prompt, key).toBeGreaterThanOrEqual(0);
  return { beat, kind: "pick", gesture: 0, prompt, options: [0, 1, 2], ring: -1, choice: null, atMs, status: "upcoming", preview: null, result: null };
}

describe("the props stages", () => {
  it("registers a stage for the two grounds classes only, with their props", () => {
    expect(Object.keys(CLASS_STAGES).sort()).toEqual(["creatures", "herbology"]);
    expect(CLASS_STAGES.herbology!().props).toEqual(["plant_vine", "plant_root", "plant_briar", "plant_fern"]);
    expect(CLASS_STAGES.creatures!().props).toEqual(["beast_puff", "beast_lynxkit"]);
    expect(CLASS_HITS.herbology).toBeDefined();
    expect(CLASS_HITS.creatures).toBeDefined();
    expect(CLASS_DRILLS.herbology).toBeUndefined();
  });

  it("names a prop by every prompt's prefix, and every prop has a landmark in its room", () => {
    for (const [classId, specs, layout] of [
      ["herbology", HERBOLOGY_PROPS, "class_greenhouse"],
      ["creatures", CREATURE_PROPS, "class_paddock"],
    ] as const) {
      const prompts = classPicks(classId)!.prompts;
      for (const p of prompts) expect(propFor(specs, p.key), p.key).toBeDefined();
      // Every prop is named by some prompt.
      for (const s of specs) expect(prompts.some((p) => propFor(specs, p.key)?.id === s.id), s.id).toBe(true);
      const room = JSON.parse(readFileSync(new URL(`../../../../../data/world/instances/${layout}.json`, import.meta.url), "utf8")) as { landmarks: { id: string }[] };
      for (const s of specs) expect(room.landmarks.some((l) => l.id === s.id), `${layout} ${s.id}`).toBe(true);
    }
    expect(propFor(HERBOLOGY_PROPS, "vine_lunge")?.id).toBe("plant_vine");
    expect(propFor(HERBOLOGY_PROPS, "fern_pale")?.id).toBe("plant_fern");
    expect(propFor(CREATURE_PROPS, "lynxkit_limp")?.id).toBe("beast_lynxkit");
    expect(propFor(CREATURE_PROPS, "vine_lunge")).toBeUndefined();
  });

  it("cues a prop as the beat's ring closes, fully on the beat, and lets go after the window", () => {
    const at = 100_000;
    expect(cueStrength(at, 2000, at - CUE_LEAD_MS - 1)).toBe(0);
    expect(cueStrength(at, 2000, at - CUE_LEAD_MS / 2)).toBeGreaterThan(0);
    expect(cueStrength(at, 2000, at - CUE_LEAD_MS / 2)).toBeLessThan(1);
    expect(cueStrength(at, 2000, at - CUE_FULL_MS)).toBe(1);
    expect(cueStrength(at, 2000, at + 1999)).toBe(1);
    expect(cueStrength(at, 2000, at + 2001)).toBe(0);
  });

  it("lunges the plant a beat names, and only that plant", () => {
    const stage = herbologyStage();
    const at = 50_000;
    const b = pickBeat("herbology", "briar_bristle", at);
    const round = { windowMs: 2000, beats: [b] };
    stage.frame(round, at - 5000);
    const restBriar = stage.pose("plant_briar", at - 5000).tilt;
    expect(Math.abs(restBriar)).toBeLessThan(0.1); // a sway at rest
    stage.frame(round, at);
    const briar = stage.pose("plant_briar", at);
    const vine = stage.pose("plant_vine", at);
    expect(briar.tilt).toBeGreaterThan(LUNGE_TILT - 0.1); // leaning at the students
    expect(briar.sy).toBeGreaterThan(1.1); // stretched
    expect(Math.abs(vine.tilt)).toBeLessThan(0.1);
    // Plants show no need badge.
    expect(stage.needs(at)).toEqual([]);
  });

  it("recoils after a right tool and snaps after a wrong one or none, then rests", () => {
    for (const [verdict, kind] of [
      ["right", "right"],
      ["wrong", "wrong"],
      ["missed", "wrong"],
    ] as const) {
      const stage = herbologyStage();
      const at = 80_000;
      const b = pickBeat("herbology", "vine_lunge", at);
      const round = { windowMs: 2000, beats: [b] };
      stage.frame(round, at + 100);
      if (verdict === "missed") {
        b.status = "missed";
        stage.missed(b, at + 2100);
      } else {
        b.status = "scored";
        stage.scored(b, verdict === "right", at + 100);
      }
      expect(stage.reaction("plant_vine"), verdict).toBe(kind);
      const t0 = verdict === "missed" ? at + 2100 : at + 100;
      stage.frame(round, t0 + REACT_MS * 0.3);
      const mid = stage.pose("plant_vine", t0 + REACT_MS * 0.3);
      if (kind === "right") expect(mid.tilt, verdict).toBeLessThan(0.1); // pulled back out of the lunge
      else expect(mid.tilt, verdict).toBeGreaterThan(LUNGE_TILT); // the bite, further forward
      // A second verdict for the same beat plays nothing new.
      stage.scored(b, true, t0 + 10);
      expect(stage.reaction("plant_vine")).toBe(kind);
      // Over: back to the sway, and the answered beat cues no more.
      stage.frame(round, t0 + REACT_MS + 10);
      expect(Math.abs(stage.pose("plant_vine", t0 + REACT_MS + 10).tilt)).toBeLessThan(0.1);
      expect(stage.reaction("plant_vine")).toBeNull();
    }
  });

  it("holds the lunge while a pick waits for the server's verdict", () => {
    const stage = herbologyStage();
    const at = 20_000;
    const b = pickBeat("herbology", "root_wail", at);
    b.status = "sent";
    stage.frame({ windowMs: 2000, beats: [b] }, at + 300);
    expect(stage.pose("plant_root", at + 300).tilt).toBeGreaterThan(LUNGE_TILT - 0.1);
    stage.clear();
    stage.frame(null, at + 400);
    expect(Math.abs(stage.pose("plant_root", at + 400).tilt)).toBeLessThan(0.1);
  });

  it("shows a creature's need over it for the beat's window, and settles it after the right care", () => {
    const stage = creaturesStage();
    const at = 60_000;
    const b1 = pickBeat("creatures", "lynxkit_limp", at, 1);
    const b2 = pickBeat("creatures", "puff_hungry", at + 4000, 2);
    const round = { windowMs: 2000, beats: [b1, b2] };
    stage.frame(round, at - CUE_LEAD_MS - 10);
    expect(stage.needs(at - CUE_LEAD_MS - 10)).toEqual([]);
    stage.frame(round, at);
    const needs = stage.needs(at);
    expect(needs.map((n) => [n.prop, n.prompt.key, n.prompt.icon])).toEqual([["beast_lynxkit", "lynxkit_limp", "icons/class/need_hurt"]]);
    expect(needs[0]!.strength).toBe(1);
    // The right care: the need goes and the Lynxkit settles (no hop).
    b1.status = "scored";
    stage.scored(b1, true, at + 200);
    stage.frame(round, at + 200);
    expect(stage.needs(at + 200)).toEqual([]);
    expect(stage.reaction("beast_lynxkit")).toBe("right");
    expect(stage.pose("beast_lynxkit", at + 200 + REACT_MS / 2).lift).toBe(0);
    // The next beat names the Fluffbell; a wrong pick startles it into a hop.
    const t = at + 4000;
    stage.frame(round, t);
    expect(stage.needs(t).map((n) => n.prop)).toEqual(["beast_puff"]);
    b2.status = "scored";
    stage.scored(b2, false, t);
    expect(stage.reaction("beast_puff")).toBe("wrong");
    expect(stage.pose("beast_puff", t + REACT_MS * 0.27).lift).toBeGreaterThan(0.15);
  });

  it("puts a need away when its window passes unanswered", () => {
    const stage = creaturesStage();
    const at = 30_000;
    const b = pickBeat("creatures", "puff_cold", at);
    const round = { windowMs: 2000, beats: [b] };
    stage.frame(round, at + 1000);
    expect(stage.needs(at + 1000).length).toBe(1);
    b.status = "missed";
    stage.missed(b, at + 2001);
    stage.frame(round, at + 2001);
    expect(stage.needs(at + 2001)).toEqual([]);
    expect(stage.reaction("beast_puff")).toBe("wrong");
  });

  it("moves every prop by small, bounded transforms over a whole lesson's worth of time", () => {
    for (const stage of [herbologyStage(), creaturesStage()]) {
      for (let ms = 0; ms < 60_000; ms += 37) {
        for (const id of stage.props) {
          const p = stage.pose(id, ms);
          expect(Math.abs(p.tilt)).toBeLessThan(1);
          expect(Math.abs(p.yaw)).toBeLessThan(0.6);
          expect(p.lift).toBeGreaterThanOrEqual(0);
          expect(p.lift).toBeLessThan(0.4);
          expect(p.sy).toBeGreaterThan(0.7);
          expect(p.sy).toBeLessThan(1.4);
          expect(p.sxz).toBeGreaterThan(0.8);
          expect(p.sxz).toBeLessThan(1.25);
        }
      }
    }
  });
});
