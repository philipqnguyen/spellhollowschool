// Spells L20: a duel's statuses sound once. The duel's cues (arena-fx) play
// duel.rooted / duel.slowed / duel.ward_up; the world effects the server
// turns those into must not add spell.bound, spell.slowed, a second
// ward_up or a potion wear-off chime on top.

import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { afterEach, describe, expect, it, vi } from "vitest";
import { audio, type SoundId } from "../../audio";
import type { DuelEvent, EffectKind, ServerMessage, VisibleEffect } from "../../wasm/sim_wasm";
import type { SpellVfx } from "../vfx/spell-vfx";
import { ArenaFx, type DuelistPose } from "../dueling/arena-fx";
import { DuelView } from "../dueling/state";
import { EffectsController, ownChangeSounds, visibleChangeSounds } from "./controller";

type Start = Extract<ServerMessage, { type: "duelStart" }>;

const SELF = 100;
const FOE = 200;
const side = { poise: 100, casting: null, shieldUntil: null, disarmedUntil: null, cooldowns: [], rootedUntil: null, slowed: null, staggeredUntil: null, wardUntil: null };
const start: Start = {
  type: "duelStart",
  duelId: 7,
  you: { kind: "duelist", value: "a" },
  a: { entity: SELF, name: "Ada", house: "skytalon", level: 20, loadout: [10, 15, 4, 16, 17] },
  b: { entity: FOE, name: "Bram", house: "burrowsby", level: 20, loadout: [10, 11, 18] },
  poiseMax: 100,
  timeLimitMs: 90_000,
  state: { atMs: 0, fightAtMs: 3000, sides: [side, side], projectiles: [] },
};
const events = (atMs: number, ...evs: DuelEvent[]): Extract<ServerMessage, { type: "duelEvents" }> => ({ type: "duelEvents", duelId: 7, atMs, events: evs });

/** The status sounds (a duel cue's or an effect's), in the order played. */
const STATUS: ReadonlySet<SoundId> = new Set<SoundId>(["duel.rooted", "duel.slowed", "duel.ward_up", "duel.cleansed", "spell.bound", "spell.slowed", "guardian.summon", "potion.wear_off"]);

function harness() {
  const played: SoundId[] = [];
  vi.spyOn(audio, "play").mockImplementation((id) => void played.push(id));
  vi.spyOn(audio, "playAt").mockImplementation((id) => void played.push(id));
  const scene = new Scene(new NullEngine());
  const view = new DuelView(start);
  const fx = new EffectsController({ scene, headHeight: () => 1.8, inDuel: (id) => view.sideOf(id) !== null });
  fx.welcome({ type: "welcome", yourId: SELF, effectsVersion: 1, effects: [] } as unknown as Extract<ServerMessage, { type: "welcome" }>);
  const own = (...kinds: EffectKind[]) => fx.ownEffects({ type: "ownEffects", effects: kinds.map((kind) => ({ kind, remainingSecs: 2, fortune: null })), speedPct: 100, broomPct: 100 });
  own(); // the report after the connection opened
  fx.prepare(0, [{ id: SELF, x: -5.5, y: 0.9, z: 0, yaw: 0 }, { id: FOE, x: 5.5, y: 0.9, z: 0, yaw: 0 }], 0);
  let version = 1;
  const changed = (effects: VisibleEffect[]) => fx.effectsChanged({ type: "effectsChanged", id: FOE, effectsVersion: ++version, effects });
  changed([]);
  const vfx = new Proxy({}, { get: () => () => {} }) as unknown as SpellVfx;
  const arena = new ArenaFx(vfx);
  arena.start("dueling_club");
  const pose = (s: "a" | "b"): DuelistPose => (s === "a" ? { x: -5.5, y: 0.9, z: 0, yaw: Math.PI / 2 } : { x: 5.5, y: 0.9, z: 0, yaw: -Math.PI / 2 });
  const cue = (atMs: number, ...evs: DuelEvent[]) => arena.play(view.apply(events(atMs, ...evs)), pose);
  const status = () => played.filter((s) => STATUS.has(s));
  return { played, own, changed, cue, status };
}

describe("duel status sounds", () => {
  afterEach(() => vi.restoreAllMocks());

  it("one Rooted cue and its EffectsChanged play exactly one status sound, and the lapse none", () => {
    const h = harness();
    h.cue(5100, { kind: "rooted", side: "b", untilMs: 6600 });
    h.changed([{ kind: "bound" }]);
    expect(h.status()).toEqual(["duel.rooted"]);
    h.changed([]);
    expect(h.status()).toEqual(["duel.rooted"]);
  });

  it("our own root, slow and ward: the cue's sound alone, and nothing as they end", () => {
    const h = harness();
    h.cue(5100, { kind: "rooted", side: "a", untilMs: 6600 });
    h.own("petrified");
    h.cue(7000, { kind: "slowed", side: "a", untilMs: 9000, pct: 50 });
    h.own("petrified", "slowed");
    h.own();
    expect(h.status()).toEqual(["duel.rooted", "duel.slowed"]);
    h.cue(9500, { kind: "wardUp", side: "a", spell: 18, untilMs: 20_000 });
    h.own("guardian");
    expect(h.status()).toEqual(["duel.rooted", "duel.slowed", "duel.ward_up"]);
  });

  it("their ward: one ward_up (the guarding guardian is silent as an effect)", () => {
    const h = harness();
    h.cue(5100, { kind: "wardUp", side: "b", spell: 18, untilMs: 15_100 });
    h.changed([{ kind: "guardian", animal: "owl", guarding: true }]);
    expect(h.status()).toEqual(["duel.ward_up"]);
  });
});

describe("spell effect sounds outside duels", () => {
  it("a bind or slow begins with its spell's sound and lapses without the potion chime", () => {
    expect(visibleChangeSounds([], [{ kind: "bound" }], false)).toEqual(["spell.bound"]);
    expect(visibleChangeSounds([{ kind: "bound" }], [], false)).toEqual([]);
    expect(visibleChangeSounds([{ kind: "slowed", pct: 50 }], [], false)).toEqual([]);
    expect(ownChangeSounds(new Set(), new Set<EffectKind>(["slowed"]), false)).toEqual(["spell.slowed"]);
    expect(ownChangeSounds(new Set<EffectKind>(["petrified", "recoloured"]), new Set(), false)).toEqual([]);
    // A potion still chimes as it wears off.
    expect(visibleChangeSounds([{ kind: "glow", colour: 3 }], [], false)).toEqual(["potion.wear_off"]);
    expect(ownChangeSounds(new Set<EffectKind>(["glow"]), new Set(), false)).toEqual(["potion.wear_off"]);
    // On a duelist the duel's statuses are silent here; a potion is not.
    expect(visibleChangeSounds([], [{ kind: "bound" }, { kind: "glow", colour: 3 }], true)).toEqual(["potion.glow"]);
  });
});
