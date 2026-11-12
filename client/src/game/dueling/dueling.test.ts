import { describe, expect, it } from "vitest";
import castleLayout from "../../../../data/world/layout.json";
import type { DuelEvent, ServerMessage } from "../../wasm/sim_wasm";
import type { SpellVfx } from "../vfx/spell-vfx";
import { chipSeconds, chipText, slowedSpeedPct } from "../../ui/duel";
import { ArenaFx, facing, holdAt, wandTip, type DuelistPose } from "./arena-fx";
import { areaAt, areaContains, duelAreas } from "./area";
import { ChallengeBook, duelCommand, duelErrorText, secondsLeft } from "./challenges";
import { DuelClock } from "./clock";
import { counterHint, DUEL_RULES, DUEL_SPELLS, duelSpell, hexRgb, kindLabel, loadoutSlots, MAX_SLOTS, secs, spellName, spellSummary, venue, venueByArea } from "./content";
import { duelRewardsText, DuelView, endText, projectileAt, projectileTravel, refusalText, xpText } from "./state";

const SCUTARA = 10;
const TORPEO = 11;
const DIMITTO = 12;
const TITILLARA = 13;

type Start = Extract<ServerMessage, { type: "duelStart" }>;

function start(you: "a" | "b" | null, over: Partial<Start["state"]> = {}): Start {
  return {
    type: "duelStart",
    duelId: 7,
    you: you ? { kind: "duelist", value: you } : { kind: "spectator" },
    a: { entity: 100, name: "Ada", house: "skytalon", level: 3, loadout: [SCUTARA, TORPEO, DIMITTO] },
    b: { entity: 200, name: "Bram", house: "burrowsby", level: 1, loadout: [SCUTARA, TORPEO, DIMITTO] },
    poiseMax: 100,
    timeLimitMs: 90_000,
    state: {
      atMs: 0,
      fightAtMs: 3000,
      sides: [
        { poise: 100, casting: null, shieldUntil: null, disarmedUntil: null, cooldowns: [], rootedUntil: null, slowed: null, staggeredUntil: null, wardUntil: null },
        { poise: 100, casting: null, shieldUntil: null, disarmedUntil: null, cooldowns: [], rootedUntil: null, slowed: null, staggeredUntil: null, wardUntil: null },
      ],
      projectiles: [],
      ...over,
    },
  };
}

const events = (atMs: number, ...evs: DuelEvent[]): Extract<ServerMessage, { type: "duelEvents" }> => ({ type: "duelEvents", duelId: 7, atMs, events: evs });

describe("dueling content", () => {
  it("reads every dueling spell with its numbers and colour", () => {
    // Spells L20 (CORE): Rigora, Adveni, Dissolvo, Tardara, Guardian after the first five.
    expect(DUEL_SPELLS.map((s) => s.id)).toEqual([10, 11, 12, 13, 14, 15, 4, 16, 17, 18]);
    const torpeo = duelSpell(TORPEO)!;
    expect(torpeo.castMs).toBeGreaterThan(0);
    expect(torpeo.damage).toBeGreaterThan(0);
    expect(torpeo.kind).toBe("attack");
    expect(duelSpell(SCUTARA)!.shieldMs).toBeGreaterThan(duelSpell(SCUTARA)!.perfectMs);
    expect(duelSpell(DIMITTO)!.breaksShield).toBe(true);
    expect(duelSpell(DIMITTO)!.interruptible).toBe(true);
    // Lucerna is a spell, but not a dueling one.
    expect(duelSpell(1)).toBeUndefined();
    expect(spellName(1)).toBe("Lantern Charm");
    expect(spellName(999)).toBe("Spell 999");
    expect(spellSummary(torpeo)).toContain("Poise");
    expect(spellSummary(duelSpell(SCUTARA)!)).toContain("Blocks");
  });

  it("converts colours and slot steps", () => {
    expect(hexRgb("#ff0080")).toEqual([1, 0, 128 / 255]);
    expect(hexRgb("nope")).toEqual([1, 1, 1]);
    expect(loadoutSlots(1)).toBe(3);
    expect(loadoutSlots(4)).toBe(3);
    expect(loadoutSlots(5)).toBe(4);
    expect(loadoutSlots(20)).toBe(5);
    expect(MAX_SLOTS).toBe(5);
    expect(venue("dueling_club")?.arena_layout).toBe("duel_arena");
    expect(venueByArea("dueling_club")?.id).toBe("dueling_club");
  });
});

describe("dueling areas", () => {
  // The shipped club's box (read from the castle layout, so the test follows
  // the Great Hall) next to an area of another kind.
  const club = castleLayout.areas.find((a) => a.id === "dueling_club")!;
  const layout = { areas: [club, { id: "x", kind: "other", name: "", min: [0, 0, 0], max: [1, 1, 1] }] };
  const [x0, y0, z0] = club.min as [number, number, number];
  const [x1, y1, z1] = club.max as [number, number, number];
  const cx = (x0 + x1) / 2;
  const cz = (z0 + z1) / 2;

  it("keeps only dueling areas and finds the one at a point (edges included)", () => {
    const areas = duelAreas(layout);
    expect(areas.map((a) => a.id)).toEqual(["dueling_club"]);
    expect(areaAt(areas, cx, 0.9, cz)?.id).toBe("dueling_club");
    expect(areaContains(areas[0]!, x0, y0, z0)).toBe(true);
    expect(areaContains(areas[0]!, x1, y1, z1)).toBe(true);
    expect(areaAt(areas, x0 - 0.1, 0, cz)).toBeNull();
    expect(areaAt(areas, cx, y1 + 0.5, cz)).toBeNull(); // the floor above
    expect(duelAreas(null)).toEqual([]);
    expect(duelAreas({})).toEqual([]);
  });

  it("matches the shipped castle layout", async () => {
    const castle = (await import("../../../../data/world/layout.json")).default;
    const areas = duelAreas(castle);
    expect(areas.length).toBeGreaterThan(0);
    const club = areas.find((a) => a.id === venue("dueling_club")!.area);
    expect(club).toBeTruthy();
  });
});

describe("the duel clock", () => {
  it("estimates the server's duel time from delayed stamps", () => {
    // The server's duel clock runs at the local rate from local 1000 ms (duel 0).
    const truth = (local: number) => local - 1000;
    const c = new DuelClock();
    expect(c.synced).toBe(false);
    expect(c.now(5000, 100)).toBe(0);
    // One-way delays of 60-140 ms (a 200 ms round trip with jitter).
    let seed = 3;
    const rand = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed / 4294967296;
    };
    for (let at = 0; at < 6000; at += 50 * (1 + Math.floor(rand() * 6))) {
      const arrive = at + 1000 + 60 + rand() * 80;
      c.observe(at, arrive);
    }
    expect(c.synced).toBe(true);
    const local = 7500;
    // Within a few tens of ms of the truth (half the rtt is added back).
    expect(Math.abs(c.now(local, 200) - truth(local))).toBeLessThan(45);
    // And the inverse.
    expect(Math.abs(c.localAt(truth(local), 200) - local)).toBeLessThan(45);
  });

  it("sinks slowly after a late stretch and clamps the one-way delay", () => {
    const c = new DuelClock();
    c.observe(1000, 1000); // offset 0
    c.observe(1500, 2500); // the server stalled: offset -1000
    // The estimate does not jump down at once…
    expect(c.now(2500, 0)).toBeGreaterThan(2400);
    // …but follows over time.
    for (let t = 2500; t < 60_000; t += 500) c.observe(t - 1000, t);
    expect(Math.abs(c.now(60_000, 0) - 59_000)).toBeLessThan(30);
    expect(DuelClock.oneWay(null)).toBe(0);
    expect(DuelClock.oneWay(10_000)).toBe(400);
    expect(DuelClock.oneWay(120)).toBe(60);
    c.reset();
    expect(c.synced).toBe(false);
  });
});

describe("the duel view", () => {
  it("starts from DuelStart: sides, countdown, clock", () => {
    const v = new DuelView(start("a"));
    expect(v.you).toBe("a");
    expect(v.loadout).toEqual([SCUTARA, TORPEO, DIMITTO]);
    expect(v.sideOf(100)).toBe("a");
    expect(v.sideOf(200)).toBe("b");
    expect(v.sideOf(5)).toBeNull();
    expect(v.countdownLeft(1000)).toBe(2000);
    expect(v.fighting(1000)).toBe(false);
    expect(v.fighting(3000)).toBe(true);
    expect(v.timeLeft(0)).toBe(90_000);
    expect(v.timeLeft(33_000)).toBe(60_000);
    expect(v.timeLeft(200_000)).toBe(0);
  });

  it("follows a cast from telegraph to hit", () => {
    const v = new DuelView(start("b"));
    const def = duelSpell(TORPEO)!;
    let cues = v.apply(events(4000, { kind: "castStarted", side: "a", spell: TORPEO, releaseMs: 4000 + def.castMs }));
    expect(cues).toEqual([{ kind: "cast", side: "a", spell: TORPEO, releaseMs: 4000 + def.castMs }]);
    expect(v.castOf("a")).toEqual({ spell: TORPEO, startMs: 4000, releaseMs: 4000 + def.castMs });
    expect(v.readyAt("a", TORPEO)).toBe(4000 + def.cooldownMs);
    const at = 4000 + def.castMs;
    cues = v.apply(events(at, { kind: "launched", id: 3, side: "a", spell: TORPEO, from: [-550, 0], to: [1850, 0], speedCms: def.speedMps * 100 }));
    expect(cues[0]).toMatchObject({ kind: "launched", id: 3 });
    expect(v.castOf("a")).toBeNull();
    const p = v.projectiles.get(3)!;
    // Stamped after flying its first tick: at `at` it is one tick out.
    const [x0] = projectileAt(p, at);
    expect(x0).toBeCloseTo(-5.5 + (def.speedMps * DUEL_RULES.tick_ms) / 1000, 5);
    const [x1, z1] = projectileAt(p, at + 500);
    expect(x1).toBeCloseTo(-5.5 + def.speedMps * 0.55, 4);
    expect(z1).toBeCloseTo(0, 6);
    cues = v.apply(events(at + 800, { kind: "hit", id: 3, target: "b", damage: def.damage, poise: 100 - def.damage }));
    expect(v.side("b").poise).toBe(100 - def.damage);
    expect(v.projectiles.size).toBe(0);
    expect(cues[0]).toMatchObject({ kind: "hit", id: 3, side: "b", spell: TORPEO, damage: def.damage });
    expect((cues[0] as { pos: [number, number] }).pos[0]).toBeGreaterThan(4);
  });

  it("predicts our own cast and takes the server's word over it", () => {
    const v = new DuelView(start("a"));
    const def = duelSpell(TORPEO)!;
    // Before the fight: refused, but close enough to hold the key.
    expect(v.refusal(TORPEO, 2900)).toBe("not_fighting");
    expect(v.readyIn(TORPEO, 2800)).toBe(150);
    expect(v.refusal(TORPEO, 2960)).toBeNull(); // within a tick of the start
    expect(v.refusal(99, 5000)).toBe("not_in_loadout");
    expect(v.readyIn(TITILLARA, 5000)).toBeNull(); // not in this loadout

    v.predict(TORPEO, 5000, 123);
    expect(v.castOf("a")).toMatchObject({ spell: TORPEO, startMs: 5000, releaseMs: 5000 + def.castMs });
    expect(v.readyAt("a", TORPEO)).toBe(5000 + def.cooldownMs);
    expect(v.refusal(SCUTARA, 5100)).toBe("busy");
    // A second key waits for the first cast.
    expect(v.readyIn(SCUTARA, 5100)).toBe(def.castMs - 100 - DUEL_RULES.tick_ms);

    // The server started it a little later: its numbers win.
    v.apply(events(5040, { kind: "castStarted", side: "a", spell: TORPEO, releaseMs: 5040 + def.castMs }));
    expect(v.pending).toBeNull();
    expect(v.castOf("a")!.startMs).toBe(5040);
    expect(v.readyAt("a", TORPEO)).toBe(5040 + def.cooldownMs);
    expect(v.refusal(TORPEO, 5040 + def.castMs + 100)).toBe("cooldown");

    // A refused prediction is taken back, cooldown and all.
    const w = new DuelView(start("a"));
    w.predict(DIMITTO, 6000, 1);
    w.apply(events(6050, { kind: "refused", side: "a", spell: DIMITTO, reason: "cooldown" }));
    expect(w.pending).toBeNull();
    expect(w.readyAt("a", DIMITTO)).toBe(0);
    // And one never answered expires.
    w.predict(TORPEO, 7000, 50);
    expect(w.expirePending(40)).toBe(false);
    expect(w.expirePending(60)).toBe(true);
    expect(w.castOf("a")).toBeNull();
  });

  it("raises, times and drops shields; reflects turn projectiles around", () => {
    const v = new DuelView(start("a"));
    const prot = duelSpell(SCUTARA)!;
    v.apply(events(5000, { kind: "shieldUp", side: "b", spell: SCUTARA, untilMs: 5000 + prot.shieldMs, perfectUntilMs: 5000 + prot.perfectMs }));
    expect(v.shieldAt("b", 5100)).toBeTruthy();
    expect(v.shieldAt("b", 5000 + prot.shieldMs)).toBeNull();
    // Starting a cast drops it.
    v.apply(events(5100, { kind: "castStarted", side: "b", spell: TORPEO, releaseMs: 5700 }));
    expect(v.shieldAt("b", 5150)).toBeNull();

    v.apply(events(6000, { kind: "launched", id: 9, side: "a", spell: TORPEO, from: [-550, 0], to: [1850, 0], speedCms: 1400 }));
    const cues = v.apply(events(6500, { kind: "reflected", id: 9, by: "b", from: [550, 20], to: [-1850, 20] }));
    expect(cues[0]).toMatchObject({ kind: "reflected", id: 9, side: "b", spell: TORPEO, pos: [5.5, 0.2] });
    const p = v.projectiles.get(9)!;
    expect(p.side).toBe("b");
    expect(p.reflected).toBe(true);
    expect(p.dir[0]).toBeCloseTo(-1, 5);
    expect(projectileAt(p, 7000)[0]).toBeCloseTo(5.5 - 7, 4);

    // Dimitto: a shattered shield, a disarm and an interrupted cast.
    const w = new DuelView(start("b"));
    w.apply(events(5000, { kind: "shieldUp", side: "b", spell: SCUTARA, untilMs: 5700, perfectUntilMs: 5220 }));
    w.apply(events(5050, { kind: "castStarted", side: "b", spell: DIMITTO, releaseMs: 6150 }));
    const c2 = w.apply(events(5300, { kind: "shieldBroken", side: "b" }, { kind: "interrupted", side: "b", spell: DIMITTO }, { kind: "disarmed", side: "b", untilMs: 7300 }));
    expect(c2.map((c) => c.kind)).toEqual(["shattered", "interrupted", "disarmed"]);
    expect(w.castOf("b")).toBeNull();
    expect(w.disarmedAt("b", 7000)).toBe(true);
    expect(w.refusal(TORPEO, 7000)).toBe("disarmed");
    expect(w.readyIn(TORPEO, 7100)).toBe(150);
    expect(w.refusal(TORPEO, 7300)).toBeNull();
  });

  it("ends: projectiles cleared, the result and our verdict", () => {
    const v = new DuelView(start("a"));
    v.apply(events(6000, { kind: "launched", id: 1, side: "b", spell: TORPEO, from: [550, 0], to: [-1850, 0], speedCms: 1400 }));
    const cues = v.apply(events(9000, { kind: "hit", id: 1, target: "a", damage: 10, poise: 0 }, { kind: "ended", winner: "b", reason: "poise" }));
    expect(cues.map((c) => c.kind)).toEqual(["hit", "ended"]);
    expect(v.ended).toEqual({ winner: "b", reason: "poise" });
    expect(v.projectiles.size).toBe(0);
    expect(v.verdict()).toBe("loss");
    expect(v.timeLeft(9000)).toBe(0);
    expect(v.refusal(TORPEO, 9100)).toBe("not_fighting");
    v.applyResult({ type: "duelResult", duelId: 7, winner: "b", reason: "poise", poise: [0, 64], rewards: { xp: 15, capped: false, housePoints: 0 }, returnInMs: 5000 }, 100);
    expect(v.outcome).toMatchObject({ winner: "b", poise: [0, 64], returnAtLocal: 5100, rewards: { xp: 15 } });
    expect(v.side("b").poise).toBe(64);
    expect(endText(v, "b", "poise")).toBe("Ada ran out of Poise.");
    expect(endText(v, null, "timeout")).toContain("Time ran out");
    expect(endText(v, "a", "forfeit")).toBe("Bram forfeited.");
    // Messages for another duel are ignored.
    expect(v.apply({ ...events(1, { kind: "fight" }), duelId: 8 })).toEqual([]);
  });

  it("lets a spectator join late: casts, shields, disarms and projectiles from the snapshot", () => {
    const v = new DuelView(
      start(null, {
        atMs: 20_000,
        sides: [
          { poise: 70, casting: [TORPEO, 20_300], shieldUntil: null, disarmedUntil: 21_000, cooldowns: [[TORPEO, 21_500]], rootedUntil: null, slowed: null, staggeredUntil: null, wardUntil: null },
          { poise: 55, casting: null, shieldUntil: 20_400, disarmedUntil: null, cooldowns: [], rootedUntil: null, slowed: null, staggeredUntil: null, wardUntil: null },
        ],
        projectiles: [{ id: 4, side: "b", spell: TITILLARA, pos: [100, 0], to: [-99_900, 0], speedCms: 1800 }],
      }),
    );
    expect(v.you).toBeNull();
    expect(v.loadout).toEqual([]);
    expect(v.refusal(TORPEO, 20_000)).toBe("not_fighting");
    expect(v.readyIn(TORPEO, 20_000)).toBeNull();
    expect(v.side("a").poise).toBe(70);
    expect(v.castOf("a")).toEqual({ spell: TORPEO, startMs: 20_300 - duelSpell(TORPEO)!.castMs, releaseMs: 20_300 });
    expect(v.disarmedAt("a", 20_500)).toBe(true);
    expect(v.readyAt("a", TORPEO)).toBe(21_500);
    expect(v.shieldAt("b", 20_200)).toBeTruthy();
    expect(projectileAt(v.projectiles.get(4)!, 21_000)[0]).toBeCloseTo(1 - 18, 4);
    expect(v.verdict()).toBeNull();
  });

  it("words every refusal", () => {
    for (const r of ["not_in_loadout", "cooldown", "disarmed", "busy", "not_fighting", "staggered"] as const) expect(refusalText(r).length).toBeGreaterThan(3);
  });
});

describe("spells to level 20 in a duel (protocol v11)", () => {
  const RIGORA = 15;
  const ADVENI = 4;
  const DISSOLVO = 16;
  const TARDARA = 17;
  const GUARDIAN = 18;
  const loadout = [SCUTARA, RIGORA, ADVENI, DISSOLVO, TARDARA];
  const startL20 = (you: "a" | "b" | null, over: Partial<Start["state"]> = {}): Start => {
    const s = start(you, over);
    return { ...s, a: { ...s.a, loadout }, b: { ...s.b, loadout: [SCUTARA, TORPEO, GUARDIAN] } };
  };

  it("reads the new spells' riders, waves, cleanse and ward from the data", () => {
    const pet = duelSpell(RIGORA)!;
    expect(pet.kind).toBe("bind");
    expect(pet.root).toEqual({ ms: 1500 });
    expect(pet.slow).toBeNull();
    expect(pet.waveHalfWidthM).toBe(0);
    const acc = duelSpell(ADVENI)!;
    expect(acc.pull).toEqual({ distanceM: 3, gapM: 2, moveMs: 300, staggerMs: 500 });
    expect(acc.interruptible).toBe(true);
    const imp = duelSpell(TARDARA)!;
    expect(imp.waveHalfWidthM).toBe(1.5);
    expect(imp.slow).toEqual({ ms: 3000, pct: 50 });
    expect(duelSpell(DISSOLVO)!.dispel).toBe(true);
    expect(duelSpell(DISSOLVO)!.speedMps).toBe(0);
    expect(duelSpell(GUARDIAN)!.wardMs).toBe(10_000);
    expect(duelSpell(TORPEO)!).toMatchObject({ root: null, slow: null, pull: null, waveHalfWidthM: 0, dispel: false, wardMs: 0 });
    // Words for every dueling spell: the kind, a summary with the key numbers and a counter hint.
    expect(DUEL_SPELLS.map((s) => kindLabel(s.kind))).toEqual(["Shield", "Attack", "Disarm", "Attack", "Attack", "Bind", "Summon", "Counter", "Hinder", "Ward"]);
    expect(spellSummary(pet)).toContain("roots 1.5 s");
    expect(spellSummary(acc)).toContain("pulls 3 m and staggers 0.5 s");
    expect(spellSummary(imp)).toContain("a wave 3 m wide");
    expect(spellSummary(imp)).toContain("slows to 50% for 3 s");
    expect(spellSummary(duelSpell(DISSOLVO)!)).toMatch(/^Frees you from a bind and a slow · cast 0\.25 s/);
    expect(spellSummary(duelSpell(GUARDIAN)!)).toContain("within 10 s");
    for (const s of DUEL_SPELLS) expect(counterHint(s).length, s.key).toBeGreaterThan(10);
    expect(secs(250)).toBe("0.25 s");
    expect(secs(6000)).toBe("6 s");
    expect(secs(1500)).toBe("1.5 s");
  });

  it("roots, slows and frees with Dissolvo; cues each, and Freed only when something was on", () => {
    const v = new DuelView(startL20("a"));
    let cues = v.apply(events(5000, { kind: "hit", id: 1, target: "a", damage: 8, poise: 92 }, { kind: "rooted", side: "a", untilMs: 6500 }));
    expect(cues.map((c) => c.kind)).toEqual(["hit", "rooted"]);
    expect(v.rootedAt("a", 6000)).toBe(true);
    expect(v.rootedAt("a", 6500)).toBe(false);
    // A rooted duelist may still cast.
    expect(v.refusal(DISSOLVO, 5200)).toBeNull();
    cues = v.apply(events(5300, { kind: "slowed", side: "a", untilMs: 8300, pct: 50 }));
    expect(cues[0]).toEqual({ kind: "slowed", side: "a", untilMs: 8300, pct: 50 });
    expect(v.slowedAt("a", 7000)).toEqual({ untilMs: 8300, pct: 50 });
    v.apply(events(5400, { kind: "castStarted", side: "a", spell: DISSOLVO, releaseMs: 5650 }));
    cues = v.apply(events(5650, { kind: "cleansed", side: "a", spell: DISSOLVO }));
    expect(cues[0]).toEqual({ kind: "cleansed", side: "a", spell: DISSOLVO, freed: true });
    expect(v.rootedAt("a", 5700)).toBe(false);
    expect(v.slowedAt("a", 5700)).toBeNull();
    expect(v.castOf("a")).toBeNull();
    // Dissolvo with nothing on: still a cue, not a "Freed!".
    cues = v.apply(events(9000, { kind: "cleansed", side: "a", spell: DISSOLVO }));
    expect(cues[0]).toMatchObject({ kind: "cleansed", freed: false });
  });

  it("pulls with a tether, staggers (refused locally), then lets go", () => {
    const v = new DuelView(startL20("b"));
    v.apply(events(4000, { kind: "castStarted", side: "b", spell: TORPEO, releaseMs: 4600 }));
    const cues = v.apply(
      events(
        4200,
        { kind: "hit", id: 2, target: "b", damage: 4, poise: 96 },
        { kind: "interrupted", side: "b", spell: TORPEO },
        { kind: "pulled", side: "b", from: [550, 0], to: [250, 0], untilMs: 4500 },
        { kind: "staggered", side: "b", untilMs: 4700 },
      ),
    );
    expect(cues.map((c) => c.kind)).toEqual(["hit", "interrupted", "pulled", "staggered"]);
    expect(cues[2]).toEqual({ kind: "pulled", side: "b", from: [5.5, 0], to: [2.5, 0], untilMs: 4500 });
    expect(v.pullAt("b", 4300)).toEqual({ from: [5.5, 0], to: [2.5, 0], startMs: 4200, endMs: 4500 });
    expect(v.pullAt("b", 4500)).toBeNull();
    expect(v.castOf("b")).toBeNull();
    expect(v.staggeredAt("b", 4600)).toBe(true);
    // Our own cast is refused while staggered, and held until it lapses.
    expect(v.refusal(SCUTARA, 4300)).toBe("staggered");
    expect(v.readyIn(SCUTARA, 4300)).toBe(4700 - 4300 - DUEL_RULES.tick_ms);
    expect(v.refusal(SCUTARA, 4700)).toBeNull();
    // The broken cast's cooldown still runs.
    expect(v.refusal(TORPEO, 4700)).toBe("cooldown");
    expect(refusalText("staggered")).toBe("Staggered!");
  });

  it("raises a ward, spends it on a spell, or lets it fade", () => {
    const v = new DuelView(startL20(null));
    v.apply(events(5000, { kind: "castStarted", side: "b", spell: GUARDIAN, releaseMs: 6500 }));
    let cues = v.apply(events(6500, { kind: "wardUp", side: "b", spell: GUARDIAN, untilMs: 16_500 }));
    expect(cues[0]).toEqual({ kind: "wardUp", side: "b", spell: GUARDIAN, untilMs: 16_500 });
    expect(v.wardAt("b", 10_000)).toBe(true);
    expect(v.castOf("b")).toBeNull();
    v.apply(events(7000, { kind: "launched", id: 5, side: "a", spell: TARDARA, from: [-550, 0], to: [1850, 0], speedCms: 600 }));
    cues = v.apply(events(8800, { kind: "warded", id: 5, by: "b" }));
    expect(cues[0]).toMatchObject({ kind: "warded", id: 5, side: "b", spell: TARDARA });
    expect(v.projectiles.size).toBe(0);
    expect(v.wardAt("b", 9000)).toBe(false);
    v.apply(events(20_000, { kind: "wardUp", side: "a", spell: GUARDIAN, untilMs: 30_000 }));
    cues = v.apply(events(30_000, { kind: "wardFaded", side: "a" }));
    expect(cues[0]).toEqual({ kind: "wardFaded", side: "a" });
    expect(v.wardAt("a", 29_000)).toBe(false);
  });

  it("lets a late spectator see a ward, a root, a slow and a stagger, and the end clears them", () => {
    const v = new DuelView(
      startL20(null, {
        atMs: 20_000,
        sides: [
          { poise: 80, casting: null, shieldUntil: null, disarmedUntil: null, cooldowns: [], rootedUntil: 20_900, slowed: [22_000, 50], staggeredUntil: 20_300, wardUntil: null },
          { poise: 90, casting: null, shieldUntil: null, disarmedUntil: null, cooldowns: [], rootedUntil: null, slowed: null, staggeredUntil: null, wardUntil: 27_000 },
        ],
      }),
    );
    expect(v.rootedAt("a", 20_500)).toBe(true);
    expect(v.slowedAt("a", 21_000)).toEqual({ untilMs: 22_000, pct: 50 });
    expect(v.staggeredAt("a", 20_100)).toBe(true);
    expect(v.wardAt("b", 25_000)).toBe(true);
    expect(v.rootedAt("b", 20_500)).toBe(false);
    v.apply(events(21_000, { kind: "ended", winner: "b", reason: "forfeit" }));
    expect(v.rootedAt("a", 20_500)).toBe(false);
    expect(v.slowedAt("a", 21_000)).toBeNull();
    expect(v.wardAt("b", 25_000)).toBe(false);
  });

  it("flies a wave like a bolt and holds it across its whole width for the verdict", () => {
    const v = new DuelView(startL20("b"));
    v.apply(events(5000, { kind: "launched", id: 3, side: "a", spell: TARDARA, from: [-550, 0], to: [1850, 0], speedCms: 600 }));
    const p = v.projectiles.get(3)!;
    expect(projectileTravel(p, 6000)).toBeCloseTo(6 * (1 + DUEL_RULES.tick_ms / 1000), 5);
    const imp = duelSpell(TARDARA)!;
    const stu = duelSpell(TORPEO)!;
    // 1.2 m to the side: the wave still reaches them, a bolt would not.
    expect(holdAt(p, { x: 5.5, z: 1.2 }, imp, null)).toBeCloseTo(11, 5);
    expect(holdAt(p, { x: 5.5, z: 1.2 }, stu, null)).toBeNull();
    // A shield or a ward stops it a little out in front.
    expect(holdAt(p, { x: 5.5, z: 0 }, imp, "shield")!).toBeLessThan(holdAt(p, { x: 5.5, z: 0 }, imp, "ward")!);
    expect(holdAt(p, { x: -8, z: 0 }, imp, null)).toBeNull(); // behind it
  });

  it("draws the new projectiles and statuses, and a burst for each new cue", () => {
    const calls: string[] = [];
    const rec = (name: string) => () => void calls.push(name);
    const vfx = Object.fromEntries(
      ["bolt", "charge", "shield", "flash", "muzzle", "wave", "bindBolt", "hookBolt", "tether", "rooted", "slowed", "dazed", "ward", "bind", "slowHit", "yank", "cleanse", "wardUp", "wardBreak", "wardFade", "fizzle", "shatter", "disarm"].map((n) => [n, rec(n)]),
    ) as unknown as SpellVfx;
    const fx = new ArenaFx(vfx);
    fx.start("dueling_club");
    const pose = (side: "a" | "b"): DuelistPose => (side === "a" ? { x: -5.5, y: 0.9, z: 0, yaw: Math.PI / 2 } : { x: 5.5, y: 0.9, z: 0, yaw: -Math.PI / 2 });
    const v = new DuelView(startL20("a"));
    v.apply(events(5000, { kind: "launched", id: 1, side: "a", spell: TARDARA, from: [-550, 0], to: [1850, 0], speedCms: 600 }));
    v.apply(events(5000, { kind: "launched", id: 2, side: "a", spell: RIGORA, from: [-550, 0], to: [1850, 0], speedCms: 800 }));
    v.apply(events(5000, { kind: "launched", id: 3, side: "a", spell: ADVENI, from: [-550, 0], to: [1850, 0], speedCms: 1500 }));
    v.apply(events(5100, { kind: "rooted", side: "b", untilMs: 6600 }, { kind: "slowed", side: "b", untilMs: 8100, pct: 50 }, { kind: "pulled", side: "b", from: [550, 0], to: [250, 0], untilMs: 5400 }, { kind: "staggered", side: "b", untilMs: 5600 }, { kind: "wardUp", side: "a", spell: GUARDIAN, untilMs: 15_100 }));
    fx.draw(v, 5200, 1000, 50, pose);
    for (const n of ["wave", "bindBolt", "hookBolt", "rooted", "slowed", "dazed", "tether", "ward"]) expect(calls, n).toContain(n);
    expect(calls).not.toContain("bolt");
    calls.length = 0;
    fx.play(
      [
        { kind: "rooted", side: "b", untilMs: 1 },
        { kind: "slowed", side: "b", untilMs: 1, pct: 50 },
        { kind: "pulled", side: "b", from: [5.5, 0], to: [2.5, 0], untilMs: 1 },
        { kind: "cleansed", side: "a", spell: DISSOLVO, freed: true },
        { kind: "wardUp", side: "a", spell: GUARDIAN, untilMs: 1 },
        { kind: "warded", id: 9, side: "a", spell: TORPEO, pos: [-5, 0] },
        { kind: "wardFaded", side: "a" },
      ],
      pose,
    );
    expect(calls).toEqual(["bind", "slowHit", "yank", "cleanse", "wardUp", "wardBreak", "flash", "wardFade"]);
  });

  it("words the status chips: seconds, the stage's slowed pace", () => {
    expect(chipSeconds(1500)).toBe("1.5 s");
    expect(chipSeconds(8200)).toBe("9 s");
    expect(chipSeconds(-5)).toBe("0.0 s");
    // In the arena duelists move at 60%; slowed to 50% of that is 30%.
    expect(slowedSpeedPct(50)).toBe(Math.round(DUEL_RULES.move_speed_pct / 2));
    expect(chipText("bound", 1500)).toBe("Bound 1.5 s");
    expect(chipText("slowed", 3000, { pct: 50 })).toBe(`Slowed ${slowedSpeedPct(50)}% · 3 s`);
    expect(chipText("ward", 10_000)).toBe("Guardian ward 10 s");
    expect(chipText("staggered", 400)).toBe("Staggered");
    expect(chipText("shield", 0, { perfect: true })).toBe("Shield Charm: reflecting");
  });
});

describe("duel XP in words", () => {
  it("names both daily limits when the XP was capped", () => {
    expect(xpText({ xp: 40, capped: false })).toBe("+40 XP");
    // The pair limit and the daily XP cap both set `capped`.
    expect(xpText({ xp: 0, capped: true })).toContain(`${DUEL_RULES.rewards.pair_daily_max} rewarded duels a day with the same opponent`);
    expect(xpText({ xp: 20, capped: true })).toMatch(/^\+20 XP \(a daily limit/);
    expect(xpText({ xp: 0, capped: false })).toContain("shorter than 15 s");
  });

  it("names the house points a win gave in the result line", () => {
    expect(duelRewardsText({ xp: 40, capped: false, housePoints: 2 }, "Roarwing")).toBe("+40 XP, +2 house points for Roarwing");
    expect(duelRewardsText({ xp: 40, capped: false, housePoints: 1 }, "Skytalon")).toBe("+40 XP, +1 house point for Skytalon");
    expect(duelRewardsText({ xp: 40, capped: false, housePoints: 0 }, "Roarwing")).toBe("+40 XP");
    expect(duelRewardsText({ xp: 0, capped: true, housePoints: 2 }, "Serpentyne")).toMatch(/^No XP: .* \+2 house points for Serpentyne\.$/);
  });
});

describe("arena effects", () => {
  /** A stand-in for SpellVfx that records the bolts it is asked to draw. */
  function fakeVfx() {
    const bolts: number[][] = [];
    const bursts: string[] = [];
    const vfx = {
      bolt: (x: number, y: number, z: number) => bolts.push([x, y, z]),
      charge: () => undefined,
      shield: () => undefined,
      flash: () => bursts.push("flash"),
      shatter: () => bursts.push("shatter"),
      fizzle: () => bursts.push("fizzle"),
      disarm: () => bursts.push("disarm"),
      muzzle: () => bursts.push("muzzle"),
    };
    return { vfx: vfx as unknown as SpellVfx, bolts, bursts };
  }

  const poses = (b: DuelistPose) => (side: "a" | "b"): DuelistPose => (side === "a" ? { x: -5.5, y: 0.9, z: 0, yaw: Math.PI / 2 } : b);

  it("holds a projectile at the duelist it reaches until the verdict (not one that was sidestepped)", () => {
    const { vfx, bolts } = fakeVfx();
    const fx = new ArenaFx(vfx);
    fx.start("dueling_club");
    const v = new DuelView(start("b"));
    v.apply(events(5000, { kind: "launched", id: 1, side: "a", spell: TORPEO, from: [-550, 0], to: [1850, 0], speedCms: 1400 }));
    const standing = poses({ x: 5.5, y: 0.9, z: 0, yaw: 0 });
    // 1 s later the server's projectile would be past the target (14 m from -5.5).
    fx.draw(v, 6000, 10_000, 75, standing);
    expect(bolts.at(-1)![0]).toBeCloseTo(5.5, 3);
    // Still held a little later…
    fx.draw(v, 6100, 10_100, 75, standing);
    expect(bolts.at(-1)![0]).toBeCloseTo(5.5, 3);
    // …then it flies on when no verdict came.
    fx.draw(v, 6400, 10_400, 75, standing);
    expect(bolts.at(-1)![0]).toBeGreaterThan(6);

    // A duelist who stepped aside is not waited for.
    const w = new DuelView(start("b"));
    w.apply(events(5000, { kind: "launched", id: 2, side: "a", spell: TORPEO, from: [-550, 0], to: [1850, 0], speedCms: 1400 }));
    const fx2 = new ArenaFx(vfx);
    fx2.start("dueling_club");
    fx2.draw(w, 6000, 20_000, 75, poses({ x: 5.5, y: 0.9, z: 1.6, yaw: 0 }));
    expect(bolts.at(-1)![0]).toBeGreaterThan(8);
    // Bolts fly at chest height over the stage.
    expect(bolts.at(-1)![1]).toBeCloseTo(0.9 + 1.25, 5);
  });

  it("plays a burst per cue and aims wands at the opponent", () => {
    const { vfx, bursts } = fakeVfx();
    const fx = new ArenaFx(vfx);
    fx.start("dueling_club");
    const p = poses({ x: 5.5, y: 0.9, z: 0, yaw: 0 });
    fx.play(
      [
        { kind: "launched", side: "a", spell: TORPEO, id: 1 },
        { kind: "hit", id: 1, side: "b", spell: TORPEO, damage: 10, poise: 90, pos: [5, 0] },
        { kind: "shattered", side: "b" },
        { kind: "interrupted", side: "a", spell: DIMITTO },
        { kind: "disarmed", side: "a", untilMs: 1 },
      ],
      p,
    );
    expect(bursts).toEqual(["muzzle", "flash", "shatter", "fizzle", "disarm"]);
    const [fx0, fz0] = facing({ x: 0, y: 0, z: 0, yaw: 0 }, { x: 3, y: 0, z: 4, yaw: 0 });
    expect(fx0).toBeCloseTo(0.6);
    expect(fz0).toBeCloseTo(0.8);
    // Alone: its own yaw.
    expect(facing({ x: 0, y: 0, z: 0, yaw: Math.PI / 2 }, null)[0]).toBeCloseTo(1);
    const tip = wandTip({ x: 0, y: 0, z: 0, yaw: 0 }, 1, 0);
    expect(tip[0]).toBeCloseTo(0.5);
    expect(tip[2]).toBeLessThan(0); // the right hand, facing +x
  });
});

describe("challenges", () => {
  const challenged = (id: number, from: number, name = "Bram"): Extract<ServerMessage, { type: "duelChallenged" }> => ({ type: "duelChallenged", challengeId: id, from, fromName: name, fromLevel: 4, expiresInSecs: 30 });

  it("learns our challenge's id from the echo and ends it", () => {
    const b = new ChallengeBook();
    b.sent("Bram", 0, 30);
    expect(b.outgoing).toMatchObject({ challengeId: null, targetName: "Bram" });
    expect(b.challenged(challenged(5, 1, "Me"), 1, 100)).toBeNull();
    expect(b.outgoing).toMatchObject({ challengeId: 5, targetName: "Bram", until: 30_100 });
    expect(b.ended(5, "declined")).toBe("Bram declined your duel.");
    expect(b.outgoing).toBeNull();
    expect(b.empty).toBe(true);
  });

  it("keeps challenges to us until answered, ended or lapsed", () => {
    const b = new ChallengeBook();
    expect(b.challenged(challenged(8, 2), 1, 0)).toContain("Bram (level 4) challenges you");
    expect(b.challenged(challenged(9, 3, "Cass"), 1, 0)).toBeTruthy();
    expect(b.incoming.map((c) => c.challengeId)).toEqual([8, 9]);
    expect(b.answered(8)?.fromName).toBe("Bram");
    // The server's own end of an answered challenge says nothing more.
    expect(b.ended(8, "declined")).toBeNull();
    expect(b.ended(9, "started")).toBeNull();
    expect(b.incoming).toEqual([]);
    b.challenged(challenged(10, 2), 1, 0);
    expect(b.expire(10_000)).toBe(false);
    expect(b.expire(30_001)).toBe(true);
    expect(b.incoming).toEqual([]);
    expect(secondsLeft(2500, 1000)).toBe(2);
    expect(secondsLeft(0, 1000)).toBe(0);
  });

  it("words refusals, naming who we challenged", () => {
    const b = new ChallengeBook();
    b.sent("Bram", 0, 30);
    expect(b.error(null, "target_not_in_area")).toBe("Bram must be at the Dueling Club too.");
    expect(b.outgoing).toBeNull();
    const codes = ["unknown_target", "self_duel", "not_in_area", "target_not_in_area", "busy", "unknown_challenge", "expired", "unknown_duel", "full", "no_spells", "rate_limited", "failed"] as const;
    for (const c of codes) expect(duelErrorText(c, null).length).toBeGreaterThan(5);
  });

  it("parses the chat commands", () => {
    expect(duelCommand("/duel Bram")).toEqual({ verb: "duel", arg: "Bram" });
    expect(duelCommand('/duel "Ann Marie" ')).toEqual({ verb: "duel", arg: "Ann Marie" });
    expect(duelCommand("/DUEL")).toEqual({ verb: "duel", arg: "" });
    expect(duelCommand("/duels")).toEqual({ verb: "duels", arg: "" });
    expect(duelCommand("/loadout")).toEqual({ verb: "loadout", arg: "" });
    expect(duelCommand("/forfeit")).toEqual({ verb: "forfeit", arg: "" });
    expect(duelCommand("/duelling")).toBeNull();
    expect(duelCommand("hello")).toBeNull();
  });
});
