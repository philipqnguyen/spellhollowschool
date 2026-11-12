// Spells to level 20 (SOCIAL): what a cast is aimed at (aim.ts), the
// refusal words, the cast signatures (cast-fx.ts), Volucra's flight path
// (volucra.ts), Aperio's fade (reveal.ts), Dissolvo landing a float
// (levitate.ts), and what the spell effects mean for drawing a character
// (effects/state.ts).

import { describe, expect, it } from "vitest";
import type { Appearance } from "../../wasm/sim_wasm";
import { animRate, liveTint, OwnEffects, RECOLOUR_VERSION_BASE, RecolouredLooks, recolouredLook, summarize } from "../effects/state";
import { chooseAim, nearestFloating, SELECT_A_FRIEND, type AimContext } from "./aim";
import { BIRDS_MAX, birdAt, CIRCLE_CLIMB, CIRCLE_RADIUS, CONJURE_S, conjureAt, flockSeeds, HEAD_CLEARANCE, TURN_FORWARD, TURN_UP } from "./volucra";
import { hueRgb, signatureOf, streakAt, STREAK_MS } from "./cast-fx";
import { spellErrorText } from "./controller";
import { SPELLS, targetsFriend, type SpellDef } from "./data";
import { liftAt, Levitations } from "./levitate";
import { REVEAL_FADE_IN_MS, REVEAL_FADE_OUT_MS, revealAlpha } from "./reveal";

const byKey = (key: string): SpellDef => SPELLS.find((s) => s.key === key)!;
const ME = 1;
const FRIEND = 2;
const STRANGER = 3;
const ctx = (selected: number | null, floatingInReach: number | null = null): AimContext => ({ selected, selfId: ME, isFriend: (id) => id === FRIEND, floatingInReach });

describe("aiming the social spells", () => {
  it("aims the hindering spells at a selected friend only", () => {
    for (const key of ["rigora", "tardara", "adveni"]) {
      const s = byKey(key);
      expect(chooseAim(s, ctx(FRIEND))).toEqual({ kind: "entity", id: FRIEND });
      expect(chooseAim(s, ctx(null))).toEqual({ kind: "refuse", text: SELECT_A_FRIEND });
      expect(chooseAim(s, ctx(ME))).toEqual({ kind: "refuse", text: SELECT_A_FRIEND });
      expect(chooseAim(s, ctx(STRANGER))).toEqual({ kind: "refuse", text: `${s.name} works only on a friend.` });
    }
  });

  it("falls back to yourself for Tinctura and Dissolvo, and Dissolvo first to a floating piece", () => {
    const tinctura = byKey("tinctura");
    const dissolvo = byKey("dissolvo");
    expect(chooseAim(tinctura, ctx(null))).toEqual({ kind: "none" });
    expect(chooseAim(tinctura, ctx(ME))).toEqual({ kind: "none" });
    expect(chooseAim(tinctura, ctx(FRIEND))).toEqual({ kind: "entity", id: FRIEND });
    expect(chooseAim(tinctura, ctx(STRANGER)).kind).toBe("refuse");
    // A floating piece only matters to Dissolvo, and only with nobody selected.
    expect(chooseAim(tinctura, ctx(null, 77))).toEqual({ kind: "none" });
    expect(chooseAim(dissolvo, ctx(null, 77))).toEqual({ kind: "furniture", itemId: 77 });
    expect(chooseAim(dissolvo, ctx(FRIEND, 77))).toEqual({ kind: "entity", id: FRIEND });
    expect(chooseAim(dissolvo, ctx(null))).toEqual({ kind: "none" });
  });

  it("casts the untargeted ones at nothing", () => {
    for (const key of ["volucra", "aperio", "torpeo"]) expect(chooseAim(byKey(key), ctx(FRIEND))).toEqual({ kind: "none" });
  });

  it("finds the floating piece in reach", () => {
    const floats = [
      { itemId: 1, x: 5, y: 0, z: 0 },
      { itemId: 2, x: 2, y: 0.5, z: 0 },
      { itemId: 3, x: 40, y: 0, z: 0 },
    ];
    expect(nearestFloating(floats, [0, 0, 0], 6)).toBe(2);
    expect(nearestFloating(floats, [39, 0, 0], 6)).toBe(3);
    expect(nearestFloating(floats, [20, 0, 0], 6)).toBeNull();
  });

  it("lists the spells a friend's card can cast", () => {
    expect(SPELLS.filter(targetsFriend).map((s) => s.key)).toEqual(["tinctura", "rigora", "adveni", "dissolvo", "tardara"]);
  });

  it("words every new refusal", () => {
    const adveni = byKey("adveni").id;
    expect(spellErrorText(adveni, "not_friend", null)).toBe("Summoning Charm works only on a friend.");
    expect(spellErrorText(adveni, "needs_target", null)).toBe("Select a friend first (Tab).");
    expect(spellErrorText(adveni, "unavailable", null)).toMatch(/sitting, flying, trading, dueling, in class/);
    expect(spellErrorText(adveni, "no_path", null)).toMatch(/no safe way/);
    // Busy: a friend just hindered, or we are bound (only the Unweaving works then).
    expect(spellErrorText(adveni, "busy", null)).toMatch(/hindered a moment ago/);
    expect(spellErrorText(byKey("tinctura").id, "busy", null, true)).toMatch(/only the Unweaving/);
    expect(spellErrorText(byKey("elevara").id, "busy", null)).toBe("You are in the middle of something else.");
  });
});

describe("cast signatures", () => {
  it("gives each social spell its own", () => {
    const sig = Object.fromEntries(SPELLS.map((s) => [s.key, signatureOf(s.social.kind)]));
    expect(sig).toMatchObject({ tinctura: "swirl", rigora: "rings", tardara: "ripple", adveni: "tether", dissolvo: "unravel", aperio: "sweep", volucra: null, lucerna: null });
  });

  it("flies the streak from the wand to the target", () => {
    expect(streakAt(0)).toBe(0);
    expect(streakAt(STREAK_MS / 2)).toBeGreaterThan(0.5);
    expect(streakAt(STREAK_MS)).toBe(1);
    expect(streakAt(STREAK_MS * 3)).toBe(1);
    expect(streakAt(-5)).toBe(0);
  });

  it("cycles bright hues", () => {
    const red = hueRgb(0);
    expect(red[0]).toBeCloseTo(1);
    expect(red[1]).toBeCloseTo(0.35);
    const green = hueRgb(1 / 3);
    expect(green[1]).toBeCloseTo(1);
    expect(hueRgb(1.25)).toEqual(hueRgb(0.25));
  });
});

describe("Volucra's birds", () => {
  const seeds = flockSeeds(7, 1234);
  const D = 4.5;

  it("seeds the same flock for the same seed", () => {
    expect(flockSeeds(7, 1234)).toEqual(seeds);
    expect(flockSeeds(7, 99)).not.toEqual(seeds);
    expect(new Set(seeds.map((b) => b.turn)).size).toBe(1);
  });

  it("conjures a feather at the wand tip that floats up shimmering and is gone as it turns", () => {
    const f = conjureAt(0, { forward: 0, up: 0, side: 0, scale: 0, spin: 0, tilt: 0, glow: 0, alive: false });
    expect(f.alive).toBe(true);
    expect(f.scale).toBeLessThan(0.05);
    expect(Math.hypot(f.forward, f.up)).toBeLessThan(0.02);
    expect({ ...conjureAt(0.12, f) }.scale).toBeGreaterThan(0.9);
    let glow = -1;
    let up = -1;
    for (let t = 0.12; t < CONJURE_S - 0.1; t += 0.02) {
      conjureAt(t, f);
      expect(f.glow).toBeGreaterThanOrEqual(glow);
      expect(f.up).toBeGreaterThanOrEqual(up - 1e-9);
      glow = f.glow;
      up = f.up;
    }
    // At the turn: at the turning point, glowing gold, shrinking into the puff.
    conjureAt(CONJURE_S - 0.005, f);
    expect(f.forward).toBeCloseTo(TURN_FORWARD, 2);
    expect(f.up).toBeCloseTo(TURN_UP, 2);
    expect(f.glow).toBeGreaterThan(0.95);
    expect(f.scale).toBeLessThan(0.45);
    expect(conjureAt(CONJURE_S, f).alive).toBe(false);
    expect(conjureAt(-0.1, f).alive).toBe(false);
  });

  // The caster's head 1.6 m up; the turning point 0.62 m in front of them, at chest height.
  const HEAD = 1.6;
  const FROM = [0.12, 1.45, 0.61] as const;

  it("hatches where the feather turned, rises over the caster's head, circles once, then leaves and is gone by the end", () => {
    for (const b of seeds) {
      expect(birdAt(CONJURE_S - 0.01, D, b, FROM, HEAD).alive).toBe(false);
      const start = { ...birdAt(CONJURE_S, D, b, FROM, HEAD) };
      expect(start.alive).toBe(true);
      expect(Math.hypot(start.x - FROM[0], start.y - FROM[1], start.z - FROM[2])).toBeLessThan(1e-6);
      expect(start.grow).toBeCloseTo(0.25, 5);
      expect(birdAt(CONJURE_S + 0.3, D, b, FROM, HEAD).grow).toBe(1);
      // Never over the caster's head: it swings round the outside, not across the middle.
      const flight = D - CONJURE_S;
      for (let t = CONJURE_S; t < CONJURE_S + flight * 0.6; t += 0.01) {
        const p = birdAt(t, D, b, FROM, HEAD);
        expect(Math.hypot(p.x, p.z), `t ${t.toFixed(2)}`).toBeGreaterThan(0.55);
      }
      // Circling: over everyone's heads, at the circle's radius, once round.
      const c0 = CONJURE_S + flight * 0.2;
      const c1 = CONJURE_S + flight * 0.6;
      let swept = 0;
      let prev = Math.atan2(birdAt(c0, D, b, FROM, HEAD).z, birdAt(c0, D, b, FROM, HEAD).x);
      for (let t = c0; t <= c1; t += 0.05) {
        const p = birdAt(t, D, b, FROM, HEAD);
        expect(Math.hypot(p.x, p.z)).toBeCloseTo(CIRCLE_RADIUS * b.spread, 5);
        expect(p.y).toBeGreaterThan(HEAD + HEAD_CLEARANCE - 0.08);
        const a = Math.atan2(p.z, p.x);
        let d = a - prev;
        while (d > Math.PI) d -= 2 * Math.PI;
        while (d < -Math.PI) d += 2 * Math.PI;
        swept += d;
        prev = a;
      }
      expect(Math.abs(swept)).toBeGreaterThan(Math.PI * 1.4);
      // Climbing while it circles, then up and away.
      expect(birdAt(c1 - 0.01, D, b, FROM, HEAD).y).toBeGreaterThan(birdAt(c0, D, b, FROM, HEAD).y + CIRCLE_CLIMB * 0.8);
      const late = birdAt(D - 0.05, D, b, FROM, HEAD);
      expect(Math.hypot(late.x, late.z)).toBeGreaterThan(6);
      expect(late.y).toBeGreaterThan(HEAD + 4);
      expect(late.alpha).toBeLessThan(0.1);
      expect(birdAt(D, D, b, FROM, HEAD).alive).toBe(false);
      expect(birdAt(D + 1, D, b, FROM, HEAD).alpha).toBe(0);
    }
  });

  it("flies a smooth path and flaps", () => {
    const b = seeds[0]!;
    for (let t = CONJURE_S; t < D - 0.02; t += 0.02) {
      const p = { ...birdAt(t, D, b, FROM, HEAD) };
      const q = birdAt(t + 0.02, D, b, FROM, HEAD);
      expect(Math.hypot(q.x - p.x, q.y - p.y, q.z - p.z)).toBeLessThan(0.6);
    }
    const flaps = new Set([0.1, 0.13, 0.16, 0.19].map((t) => Math.sign(birdAt(CONJURE_S + t, D, b, FROM, HEAD).flap)));
    expect(flaps.size).toBe(2);
  });

  it("keeps a bounded flock size", () => {
    expect(BIRDS_MAX).toBe(48);
    expect(flockSeeds(12, 1)).toHaveLength(12);
  });
});

describe("Aperio's shining spots", () => {
  it("fades in, holds and fades out by the end", () => {
    const D = 10_000;
    expect(revealAlpha(-1, D)).toBe(0);
    expect(revealAlpha(0, D)).toBe(0);
    expect(revealAlpha(REVEAL_FADE_IN_MS / 2, D)).toBeCloseTo(0.5);
    expect(revealAlpha(REVEAL_FADE_IN_MS, D)).toBe(1);
    expect(revealAlpha(5_000, D)).toBe(1);
    expect(revealAlpha(D - REVEAL_FADE_OUT_MS / 2, D)).toBeCloseTo(0.5);
    expect(revealAlpha(D, D)).toBe(0);
    expect(revealAlpha(D + 5, D)).toBe(0);
    // A short reveal still fades both ways.
    expect(revealAlpha(700, 900)).toBeLessThan(1);
    expect(revealAlpha(100, 900)).toBeLessThan(1);
  });
});

describe("Dissolvo landing a floating piece", () => {
  it("settles it from where it is, soon", () => {
    const f = new Levitations();
    f.start(9, 5000, 0);
    const lifts: [number, number][] = [];
    const fx = { pushGlint() {}, pushGlow() {} } as unknown as Parameters<Levitations["update"]>[4];
    f.update(2000, 0.016, (id, dy) => lifts.push([id, dy]), () => null, fx);
    const high = lifts.at(-1)![1];
    expect(high).toBeGreaterThan(0.3);
    expect(f.land(9, 2000)).toBe(true);
    expect(f.land(10, 2000)).toBe(false);
    f.update(2350, 0.016, (id, dy) => lifts.push([id, dy]), () => null, fx);
    const mid = lifts.at(-1)![1];
    expect(mid).toBeLessThan(high);
    expect(mid).toBeGreaterThan(0);
    f.update(2701, 0.016, (id, dy) => lifts.push([id, dy]), () => null, fx);
    expect(lifts.at(-1)).toEqual([9, 0]);
    expect(f.count).toBe(0);
    expect(liftAt(2701, 2700)).toBe(0);
  });
});

describe("spell effects on a character", () => {
  it("freezes a bound pose and slows a slowed one", () => {
    expect(animRate(summarize([]))).toBe(1);
    expect(animRate(summarize([{ kind: "bound" }]))).toBe(0);
    expect(animRate(summarize([{ kind: "slowed", pct: 40 }]))).toBeCloseTo(0.4);
    expect(animRate(summarize([{ kind: "bound" }, { kind: "slowed", pct: 40 }]))).toBe(0);
    expect(animRate(summarize([{ kind: "glow", colour: 3 }]))).toBe(1);
  });

  it("keeps a Chameleon hair tint beside Tinctura and lets Tinctura win on the robe", () => {
    expect(liveTint(summarize([{ kind: "chameleon", part: "robe", family: 1 }]))).toBe("robe");
    expect(liveTint(summarize([{ kind: "chameleon", part: "hair", family: 1 }, { kind: "recoloured", colour: 21 }]))).toBe("hair");
    expect(liveTint(summarize([{ kind: "recoloured", colour: 21 }, { kind: "chameleon", part: "robe", family: 1 }]))).toBeNull();
    expect(liveTint(summarize([{ kind: "recoloured", colour: 21 }]))).toBeNull();
    expect(liveTint(summarize([{ kind: "lucerna" }]))).toBeNull();
  });

  it("dyes Tinctura's colour into the look, once per combination", () => {
    const robe = { itemDef: 101, dyes: [3, 4, 5] as [number, number, number] };
    const me: Appearance = { bodyType: 0, height: 128, face: [128, 128, 128, 128, 128], skinTone: 2, hairStyle: 1, hairColour: 6, eyeColour: 0, outfit: { hat: null, body: robe, legs: null, shoes: null } };
    const dyed = recolouredLook(me, 21);
    expect(dyed.outfit.body!.dyes).toEqual([21, 4, 5]);
    expect(dyed.hairColour).toBe(6);
    expect(me.outfit.body!.dyes).toEqual([3, 4, 5]);
    expect(recolouredLook(me, 3)).toBe(me);
    expect(recolouredLook({ ...me, outfit: { ...me.outfit, body: null } }, 21).outfit.body).toBeNull();

    const looks = new RecolouredLooks();
    // No effects, or no Tinctura: the character as it is (or Mimic Draught's look itself).
    expect(looks.shown(7, null, me, 4)).toBeNull();
    const poly = { appearance: { ...me, hairColour: 9 }, version: -2, fx: null, recolour: null };
    expect(looks.shown(7, poly, me, 4)).toBe(poly);
    // Tinctura: a stable look and version while it lasts, negative and clear of Mimic Draught's.
    const a = looks.shown(7, { appearance: null, version: 0, recolour: 21 }, me, 4)!;
    expect(a.appearance.outfit.body!.dyes[0]).toBe(21);
    expect(a.version).toBeLessThan(RECOLOUR_VERSION_BASE);
    expect(looks.shown(7, { appearance: null, version: 0, recolour: 21 }, me, 4)).toBe(a);
    // A new colour or a new appearance version: a new look.
    const b = looks.shown(7, { appearance: null, version: 0, recolour: 24 }, me, 4)!;
    expect(b.version).not.toBe(a.version);
    const c = looks.shown(7, { appearance: null, version: 0, recolour: 24 }, me, 5)!;
    expect(c.version).not.toBe(b.version);
    // Over a Mimic Draught look: the disguise's robe takes the colour, its hair stays.
    const d = looks.shown(7, { ...poly, recolour: 24 }, me, 4)!;
    expect(d.appearance.hairColour).toBe(9);
    expect(d.appearance.outfit.body!.dyes[0]).toBe(24);
    // It ends: back to the character's own.
    expect(looks.shown(7, { appearance: null, version: 0, recolour: null }, me, 5)).toBeNull();
  });

  it("predicts a bound walk at speed 0", () => {
    const own = new OwnEffects();
    own.set([{ kind: "petrified", remainingSecs: 2 }], 0, 0);
    expect(own.speedPct).toBe(0);
    own.set([{ kind: "slowed", remainingSecs: 4 }], 40, 0);
    expect(own.speedPct).toBe(40);
    own.set([], Number.NaN, 0);
    expect(own.speedPct).toBe(100);
  });
});
