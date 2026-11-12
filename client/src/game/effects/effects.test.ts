import { describe, expect, it } from "vitest";
import { PALETTE } from "../../content/catalog";
import type { EffectKind, EntityInfo, VisibleEffect } from "../../wasm/sim_wasm";
import { assemble, recolour, type PartInput } from "../characters/assemble";
import { Rig } from "../characters/clips";
import { greyboxPart, greyboxSkeleton, type GreyboxKind } from "../characters/greybox";
import { CHAMELEON_CYCLE_MS, chameleonColour, chameleonOrder, chameleonStep, dyeFamilies, familyColours, familyOf } from "./chameleon";
import { EffectBook, effectDuration, formatRemaining, glowColour, hasParticles, NO_FX, OwnEffects, summarize } from "./state";
import { SteamPool } from "./steam";

describe("chameleon colours", () => {
  const blues = familyColours(dyeFamilies().findIndex((f) => f.id === "blues"));

  it("reads the palette's dye families, every colour in exactly one", () => {
    const fams = dyeFamilies();
    expect(fams.length).toBe(10);
    const seen = fams.flatMap((f) => f.colours).sort((a, b) => a - b);
    expect(seen).toEqual(PALETTE.colours.map((c) => c.index));
    expect(familyOf(blues[0]!)).toBe(fams.findIndex((f) => f.id === "blues"));
    expect(familyOf(999)).toBe(-1);
  });

  it("orders a family deterministically per seed: a permutation, the same every time", () => {
    for (const seed of [0, 1, 7, 12345, 2 ** 31 + 5]) {
      const a = chameleonOrder(blues, seed);
      expect(chameleonOrder(blues, seed)).toEqual(a);
      expect([...a].sort((x, y) => x - y)).toEqual([...blues].sort((x, y) => x - y));
    }
    // Different seeds usually differ (so neighbours drift out of step).
    const orders = new Set([1, 2, 3, 4, 5, 6, 7, 8].map((s) => chameleonOrder(blues, s).join(",")));
    expect(orders.size).toBeGreaterThan(2);
  });

  it("is a pure function of seed and wall-clock time, so every viewer agrees", () => {
    const t0 = 1_760_000_000_000;
    const seq = (seed: number) => Array.from({ length: 12 }, (_, i) => chameleonColour(blues, seed, t0 + i * CHAMELEON_CYCLE_MS));
    expect(seq(42)).toEqual(seq(42));
    // Within one step the colour holds; the next step changes it (no repeats back to back).
    expect(chameleonColour(blues, 42, t0)).toBe(chameleonColour(blues, 42, t0 + CHAMELEON_CYCLE_MS - 1 - (t0 % CHAMELEON_CYCLE_MS)));
    const s = seq(42);
    for (let i = 1; i < s.length; i++) expect(s[i]).not.toBe(s[i - 1]);
    // It cycles through the whole family.
    expect(new Set(s.slice(0, blues.length))).toEqual(new Set(blues));
    // Passing the precomputed order gives the same answers.
    const order = chameleonOrder(blues, 42);
    expect(chameleonColour(blues, 42, t0 + 5 * CHAMELEON_CYCLE_MS, CHAMELEON_CYCLE_MS, order)).toBe(s[5]);
    expect(chameleonStep(3000, 1500)).toBe(2);
    expect(chameleonColour([], 1, t0)).toBe(-1);
  });
});

describe("visible effects", () => {
  const glow: VisibleEffect = { kind: "glow", colour: 22 };
  const info = (id: number, version: number, effects: VisibleEffect[]): EntityInfo =>
    ({ id, character: { name: `c${id}`, house: "skytalon" }, appearanceVersion: 1, appearance: {} as never, effectsVersion: version, effects, level: 1, npc: null }) as EntityInfo;

  it("keeps the newest version per character", () => {
    const b = new EffectBook();
    expect(b.set(5, 2, [glow])).toBe(true);
    expect(b.set(5, 1, [])).toBe(false); // older: ignored
    expect(b.get(5)).toEqual([glow]);
    expect(b.set(5, 3, [])).toBe(true);
    expect(b.get(5)).toEqual([]);
    expect(b.set(9, 0, [])).toBe(false); // never had any: nothing stored
    expect(b.size).toBe(1);
  });

  it("follows snapshots and keeps our own effects across zone changes", () => {
    const b = new EffectBook();
    b.set(1, 1, [{ kind: "steam" }]); // ourselves
    b.applySnapshot([info(2, 4, [glow]), info(3, 0, [])], [], 1);
    expect(b.get(2)).toEqual([glow]);
    expect(b.get(3)).toEqual([]);
    // An entity re-entering with an older cached look does not undo a newer EffectsChanged.
    b.set(2, 6, [{ kind: "lucky" }]);
    b.applySnapshot([info(2, 5, [glow])], [], 1);
    expect(b.get(2)).toEqual([{ kind: "lucky" }]);
    b.applySnapshot([], [2], 1);
    expect(b.get(2)).toEqual([]);
    b.applySnapshot([info(2, 7, [glow])], [], 1);
    b.clearOthers(1);
    expect(b.get(2)).toEqual([]);
    expect(b.get(1)).toEqual([{ kind: "steam" }]);
  });

  it("summarizes what to draw", () => {
    expect(summarize([])).toBe(NO_FX);
    const s = summarize([glow, { kind: "scale", pct: 50 }, { kind: "chameleon", part: "robe", family: 8 }, { kind: "nameplateHidden" }, { kind: "babbling" }]);
    expect(s.glow).toBe(22);
    expect(s.scalePct).toBe(50);
    expect(s.chameleon).toEqual({ part: "robe", family: 8 });
    expect(s.nameplateHidden && s.babbling).toBe(true);
    expect(s.steam || s.lucky).toBe(false);
    expect(hasParticles(s)).toBe(true);
    expect(summarize([{ kind: "scale", pct: 255 }]).scalePct).toBe(200);
  });
});

describe("own effect timers", () => {
  it("counts down locally between messages and shows effects in order", () => {
    const own = new OwnEffects();
    own.set(
      [
        { kind: "pick_me_up_tonic", remainingSecs: 600 },
        { kind: "glow", remainingSecs: 90 },
      ],
      115,
      1000,
    );
    expect(own.speedPct).toBe(115);
    expect(own.view(1000).map((v) => v.kind)).toEqual(["glow", "pick_me_up_tonic"]);
    expect(own.remaining("glow", 1000)).toBe(90);
    expect(own.remaining("glow", 1000 + 30_500)).toBe(60); // rounded up
    expect(own.remaining("glow", 1000 + 90_000)).toBe(0);
    expect(own.remaining("fortunes_draught", 1000)).toBe(0);
    const pep = own.view(1000 + 300_000).find((v) => v.kind === "pick_me_up_tonic")!;
    expect(pep.remainingSecs).toBe(300);
    expect(pep.fraction).toBeCloseTo(300 / effectDuration("pick_me_up_tonic"));
    // The full length comes from data/economy.json, at least what is left.
    expect(own.view(1000).find((v) => v.kind === "glow")!.fraction).toBeCloseTo(90 / effectDuration("glow"));
    own.set([], 100, 2000);
    expect(own.count).toBe(0);
    expect(own.speedPct).toBe(100);
  });

  it("never shows more than full for a relog with time to spare, and bad speeds fall back", () => {
    const own = new OwnEffects();
    own.set([{ kind: "babbling" as EffectKind, remainingSecs: 9999 }], Number.NaN, 0);
    expect(own.view(0)[0]!.fraction).toBe(1);
    expect(own.speedPct).toBe(100);
  });

  it("formats time left", () => {
    expect(formatRemaining(899.2)).toBe("15:00");
    expect(formatRemaining(65)).toBe("1:05");
    expect(formatRemaining(9)).toBe("9 s");
    expect(formatRemaining(-3)).toBe("0 s");
  });
});

describe("steam pool", () => {
  it("emits at a steady rate, ages puffs out and never exceeds its capacity", () => {
    const pool = new SteamPool(50);
    const e = { owed: 0 };
    for (let i = 0; i < 30; i++) {
      pool.emit(e, 1 / 60, 0, 0, 0, 0, 1.7, 1, 5);
      pool.step(1 / 60);
    }
    expect(pool.count).toBeGreaterThan(3);
    expect(pool.count).toBeLessThanOrEqual(7); // about 12 a second for half a second
    for (let i = 0; i < 600; i++) pool.emit({ owed: 0 }, 1, 0, 0, 0, 0, 1.7, 1, 5);
    expect(pool.count).toBe(50);
    let drawn = 0;
    pool.forEach((_x, y, _z, size, alpha) => {
      drawn++;
      expect(y).toBeGreaterThan(1.4);
      expect(size).toBeGreaterThan(0);
      expect(alpha).toBeGreaterThanOrEqual(0);
    });
    expect(drawn).toBe(50);
    for (let i = 0; i < 120; i++) pool.step(1 / 60);
    expect(pool.count).toBe(0);
  });

  it("does not emit for characters far away", () => {
    const pool = new SteamPool(10);
    pool.emit({ owed: 0 }, 1, 0, 0, 0, 0, 1.7, 1, 100);
    expect(pool.count).toBe(0);
  });

  it("draws puffs opaque enough to read on pale floors", () => {
    const pool = new SteamPool(40);
    for (let i = 0; i < 30; i++) {
      pool.emit({ owed: 0 }, 0.1, 0, 0, 0, 0, 1.7, 1, 5);
      pool.step(1 / 30);
    }
    let strongest = 0;
    pool.forEach((_x, _y, _z, _size, alpha) => (strongest = Math.max(strongest, alpha)));
    expect(strongest).toBeGreaterThan(0.5);
  });
});

describe("glow colours", () => {
  it("are the dye's hue, saturated and at full brightness", () => {
    const muted: [number, number, number] = [0.35, 0.3, 0.55]; // a dusky purple
    const g = glowColour(muted);
    expect(Math.max(...g)).toBeCloseTo(1);
    // Still purple (blue strongest, green weakest), and more saturated.
    expect(g[2]).toBeGreaterThan(g[0]);
    expect(g[0]).toBeGreaterThan(g[1]);
    const spread = (c: readonly number[]) => (Math.max(...c) - Math.min(...c)) / Math.max(...c);
    expect(spread(g)).toBeGreaterThan(spread(muted));
    // Black dye glows a cool white rather than nothing.
    expect(Math.min(...glowColour([0, 0, 0]))).toBeGreaterThan(0.5);
    for (const c of glowColour([0.9, 0.2, 0.1])) expect(c).toBeGreaterThanOrEqual(0);
  });
});

describe("live recolour (Chameleon Tonic)", () => {
  const rig = new Rig(greyboxSkeleton(0));
  const parts: PartInput[] = (["body", "hair_long", "robe"] as GreyboxKind[]).map((k, i) => ({
    role: (["base", "hair", "body"] as const)[i]!,
    part: greyboxPart(k, rig),
    dyes: [16, 21, 0],
  }));
  const input = { rig, parts, lod: 0, skin: [0.8, 0.6, 0.5] as [number, number, number], hair: [0.2, 0.1, 0.05] as [number, number, number], shapeWeights: new Map<string, number>() };

  it("splits the tracked channel so the original colour gives the same vertex colours", () => {
    const plain = assemble(input);
    const robe = assemble({ ...input, track: [{ role: "body", channel: 0 }] });
    expect(robe.tint).toBeDefined();
    expect(plain.tint).toBeUndefined();
    // As the renderer does: a copy of the assembled colours (alpha carries the finish).
    const bytes = new Uint8Array(robe.colors);
    // The robe's own primary dye reproduces the baked colours (to a byte of rounding).
    const red = PALETTE.colours[16]!.hex;
    const rgb: [number, number, number] = [parseInt(red.slice(1, 3), 16) / 255, parseInt(red.slice(3, 5), 16) / 255, parseInt(red.slice(5, 7), 16) / 255];
    recolour(robe.tint!, rgb, bytes);
    let worst = 0;
    for (let i = 0; i < bytes.length; i++) worst = Math.max(worst, Math.abs(bytes[i]! - plain.colors[i]!));
    expect(worst).toBeLessThanOrEqual(1);
  });

  it("changes only the tracked channel", () => {
    const hair = assemble({ ...input, track: [{ role: "hair", channel: 0 }, { role: "base", channel: 1 }] });
    const green = new Uint8Array(hair.colors.length);
    recolour(hair.tint!, [0, 1, 0], green);
    let changed = 0;
    let same = 0;
    for (let v = 0; v < hair.vertexCount; v++) {
      const o = v * 4;
      if (green[o] !== hair.colors[o] || green[o + 1] !== hair.colors[o + 1] || green[o + 2] !== hair.colors[o + 2]) changed++;
      else same++;
    }
    expect(changed).toBeGreaterThan(0); // the hair (and brows)
    expect(same).toBeGreaterThan(changed); // skin and robe stay
  });
});
