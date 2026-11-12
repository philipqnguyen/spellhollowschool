// One duel as this client sees it, rebuilt from the server's messages
// (docs/architecture.md, "Spells and dueling"): `DuelStart` (with the duel's
// state for spectators joining late), each tick's `DuelEvents` and the
// `DuelResult`. The server runs the rules (`sim_core::duel`); this only
// keeps what the HUD and the VFX draw: Poise, who is casting what until
// when, raised shields, disarms, cooldowns and the projectiles in flight
// (flown from their launch parameters: the server sends no per-tick
// positions). Protocol v11 adds the hindrances (a root, a slow, a pull and
// its stagger) and the Guardian ward, each with the duel time it ends.
//
// Every time here is duel time (ms since the arena opened; see clock.ts).
// `apply` turns events into *cues* (a hit at this spot, a shield shattered)
// for the VFX and callouts to play once.
//
// Our own casts are predicted: `predict` starts the cast bar and the
// cooldown sweep the moment the key is pressed, before the server's
// `CastStarted` (which re-anchors them) or `Refused` (which takes them
// back). Nothing else is predicted: the server decides every outcome.
// Pure, unit tested.

import type { ArenaPos, CastRefusal, DuelEndReason, DuelEvent, DuelRewards, DuelSide, DuelistInfo, DuelSnapshot, ServerMessage } from "../../wasm/sim_wasm";
import { DUEL_RULES, duelSpell } from "./content";

type DuelStartMsg = Extract<ServerMessage, { type: "duelStart" }>;
type DuelEventsMsg = Extract<ServerMessage, { type: "duelEvents" }>;
type DuelResultMsg = Extract<ServerMessage, { type: "duelResult" }>;

export const SIDES: readonly DuelSide[] = ["a", "b"];

export function sideIndex(side: DuelSide): 0 | 1 {
  return side === "a" ? 0 : 1;
}

export function otherSide(side: DuelSide): DuelSide {
  return side === "a" ? "b" : "a";
}

/** A cast in progress: telegraphed from `startMs`, released at `releaseMs`. */
export interface CastView {
  spell: number;
  startMs: number;
  releaseMs: number;
}

export interface ShieldView {
  spell: number;
  startMs: number;
  untilMs: number;
  perfectUntilMs: number;
}

export interface SideView {
  poise: number;
  casting: CastView | null;
  shield: ShieldView | null;
  /** No casting before this (0: never disarmed). */
  disarmedUntil: number;
  /** Spell → when it can be cast again (server values). */
  cooldowns: Map<number, number>;
  /** Rigora: no moving before this (0: never rooted). */
  rootedUntil: number;
  /** Tardara: moving at `pct`% until `untilMs`. */
  slowed: { untilMs: number; pct: number } | null;
  /** Adveni: no casting and no moving before this (0: never staggered). */
  staggeredUntil: number;
  /** A Guardian ward stands until this (0: none). */
  wardUntil: number;
  /** Adveni: the slide toward the caster (arena metres), while it lasts. */
  pull: PullView | null;
}

/** A pull's straight slide from `from` to `to` over duel time `startMs`..`endMs`. */
export interface PullView {
  from: [number, number];
  to: [number, number];
  startMs: number;
  endMs: number;
}

/** A projectile flying from `from` (arena metres, x and z) along `dir` from duel time `t0`. */
export interface ProjectileView {
  id: number;
  side: DuelSide;
  spell: number;
  from: [number, number];
  dir: [number, number];
  speedMps: number;
  t0: number;
  reflected: boolean;
}

/** Our own cast, sent but not yet confirmed by the server. */
export interface PendingCast {
  spell: number;
  /** When the server should start it (duel ms, estimated). */
  startMs: number;
  releaseMs: number;
  /** When it was sent (local ms), to give up on it. */
  sentLocal: number;
}

/** Something to show once (a burst, a callout). `pos`: arena metres where it happened, if known. */
export type DuelCue =
  | { kind: "fight" }
  | { kind: "cast"; side: DuelSide; spell: number; releaseMs: number }
  | { kind: "refused"; side: DuelSide; spell: number; reason: CastRefusal }
  | { kind: "interrupted"; side: DuelSide; spell: number }
  | { kind: "shield"; side: DuelSide; spell: number; untilMs: number; perfectUntilMs: number }
  | { kind: "launched"; side: DuelSide; spell: number; id: number }
  | { kind: "blocked"; id: number; side: DuelSide; spell: number; pos: [number, number] | null }
  | { kind: "reflected"; id: number; side: DuelSide; spell: number; pos: [number, number] }
  | { kind: "shattered"; side: DuelSide }
  | { kind: "hit"; id: number; side: DuelSide; spell: number; damage: number; poise: number; pos: [number, number] | null }
  | { kind: "disarmed"; side: DuelSide; untilMs: number }
  | { kind: "missed"; id: number; spell: number; pos: [number, number] | null }
  | { kind: "ended"; winner: DuelSide | null; reason: DuelEndReason }
  // Protocol v11.
  | { kind: "rooted"; side: DuelSide; untilMs: number }
  | { kind: "slowed"; side: DuelSide; untilMs: number; pct: number }
  | { kind: "pulled"; side: DuelSide; from: [number, number]; to: [number, number]; untilMs: number }
  | { kind: "staggered"; side: DuelSide; untilMs: number }
  /** Dissolvo released; `freed`: a root or a slow was on. */
  | { kind: "cleansed"; side: DuelSide; spell: number; freed: boolean }
  | { kind: "wardUp"; side: DuelSide; spell: number; untilMs: number }
  /** The ward of `side` stopped projectile `id` (at `pos`, if known). */
  | { kind: "warded"; id: number; side: DuelSide; spell: number; pos: [number, number] | null }
  | { kind: "wardFaded"; side: DuelSide };

export interface DuelOutcome {
  winner: DuelSide | null;
  reason: DuelEndReason;
  poise: [number, number];
  rewards: DuelRewards | null;
  /** When everyone goes back to the club (local ms). */
  returnAtLocal: number;
}

/** Ms of the server's tick: a launched projectile has already flown one tick when its event is stamped. */
const TICK_MS = DUEL_RULES.tick_ms;
/** Casts are sent anyway when the server should accept them within this (ms): a tick of slack. */
const CAST_SLACK_MS = TICK_MS;

const metres = (p: ArenaPos): [number, number] => [p[0] / 100, p[1] / 100];

function unit(from: [number, number], to: [number, number], side: DuelSide): [number, number] {
  const dx = to[0] - from[0];
  const dz = to[1] - from[1];
  const len = Math.hypot(dx, dz);
  // The server's fallback when they coincide: straight down the lane.
  if (len < 1e-4) return side === "a" ? [1, 0] : [-1, 0];
  return [dx / len, dz / len];
}

/** Where projectile `p` is at duel time `t` (arena metres). */
export function projectileAt(p: ProjectileView, t: number, out: [number, number] = [0, 0]): [number, number] {
  const d = p.speedMps * Math.max(0, t - p.t0) / 1000;
  out[0] = p.from[0] + p.dir[0] * d;
  out[1] = p.from[1] + p.dir[1] * d;
  return out;
}

/** How far `p` has flown by `t` (m). */
export function projectileTravel(p: ProjectileView, t: number): number {
  return (p.speedMps * Math.max(0, t - p.t0)) / 1000;
}

export class DuelView {
  readonly duelId: number;
  /** Our side, or null when watching. */
  readonly you: DuelSide | null;
  readonly duelists: readonly [DuelistInfo, DuelistInfo];
  readonly poiseMax: number;
  readonly timeLimitMs: number;
  readonly fightAtMs: number;
  readonly sides: [SideView, SideView];
  readonly projectiles = new Map<number, ProjectileView>();
  pending: PendingCast | null = null;
  /** Set by the `Ended` event (or the result). */
  ended: { winner: DuelSide | null; reason: DuelEndReason } | null = null;
  outcome: DuelOutcome | null = null;
  /** The last duel time any message carried. */
  lastAtMs: number;

  constructor(msg: DuelStartMsg) {
    this.duelId = msg.duelId;
    this.you = msg.you.kind === "duelist" ? msg.you.value : null;
    this.duelists = [msg.a, msg.b];
    this.poiseMax = msg.poiseMax;
    this.timeLimitMs = msg.timeLimitMs;
    const s = msg.state;
    this.fightAtMs = s.fightAtMs;
    this.lastAtMs = s.atMs;
    this.sides = [sideFromSnapshot(s, 0), sideFromSnapshot(s, 1)];
    for (const p of s.projectiles) {
      const from = metres(p.pos);
      this.projectiles.set(p.id, { id: p.id, side: p.side, spell: p.spell, from, dir: unit(from, metres(p.to), p.side), speedMps: p.speedCms / 100, t0: s.atMs, reflected: false });
    }
  }

  side(side: DuelSide): SideView {
    return this.sides[sideIndex(side)];
  }

  info(side: DuelSide): DuelistInfo {
    return this.duelists[sideIndex(side)];
  }

  /** Which side entity `id` duels on, or null (a spectator, the professor…). */
  sideOf(entity: number): DuelSide | null {
    if (this.duelists[0].entity === entity) return "a";
    if (this.duelists[1].entity === entity) return "b";
    return null;
  }

  /** Our loadout in key order (empty when watching). */
  get loadout(): readonly number[] {
    return this.you ? this.info(this.you).loadout : [];
  }

  get endAtMs(): number {
    return this.fightAtMs + this.timeLimitMs;
  }

  /** Countdown ms left before the fight (0 once it began). */
  countdownLeft(now: number): number {
    return Math.max(0, this.fightAtMs - now);
  }

  /** Fighting ms left on the clock. */
  timeLeft(now: number): number {
    if (this.ended) return 0;
    return Math.max(0, Math.min(this.timeLimitMs, this.endAtMs - Math.max(now, this.fightAtMs)));
  }

  fighting(now: number): boolean {
    return !this.ended && now >= this.fightAtMs;
  }

  /** The cast to draw for `side`: our prediction first, then the server's. */
  castOf(side: DuelSide): CastView | null {
    if (side === this.you && this.pending) return this.pending;
    return this.side(side).casting;
  }

  /** The shield of `side` standing at `now`, if any. */
  shieldAt(side: DuelSide, now: number): ShieldView | null {
    const s = this.side(side).shield;
    return s && now < s.untilMs ? s : null;
  }

  disarmedAt(side: DuelSide, now: number): boolean {
    return this.side(side).disarmedUntil > now;
  }

  /** Whether `side` is rooted at `now` (Rigora). */
  rootedAt(side: DuelSide, now: number): boolean {
    return this.side(side).rootedUntil > now;
  }

  /** The slow on `side` at `now`, if any (Tardara). */
  slowedAt(side: DuelSide, now: number): { untilMs: number; pct: number } | null {
    const s = this.side(side).slowed;
    return s && s.untilMs > now ? s : null;
  }

  /** Whether `side` is staggered at `now` (Adveni: no casting, no moving). */
  staggeredAt(side: DuelSide, now: number): boolean {
    return this.side(side).staggeredUntil > now;
  }

  /** Whether `side`'s Guardian ward stands at `now`. */
  wardAt(side: DuelSide, now: number): boolean {
    return this.side(side).wardUntil > now;
  }

  /** The pull sliding `side` at `now` (null once it arrived). */
  pullAt(side: DuelSide, now: number): PullView | null {
    const p = this.side(side).pull;
    return p && now < p.endMs ? p : null;
  }

  /** When our `spell` is ready (duel ms), counting a cast we predicted. */
  readyAt(side: DuelSide, spell: number): number {
    const server = this.side(side).cooldowns.get(spell) ?? 0;
    const p = side === this.you ? this.pending : null;
    if (p && p.spell === spell) return Math.max(server, p.startMs + (duelSpell(spell)?.cooldownMs ?? 0));
    return server;
  }

  /**
   * Whether the server would refuse our `spell` if it arrived at duel time
   * `arrival`, and why (null: it would start). A tick of slack in our
   * favour: the server is the judge, this only saves pointless sends.
   */
  refusal(spell: number, arrival: number): CastRefusal | null {
    if (!this.you || this.ended || arrival + CAST_SLACK_MS < this.fightAtMs) return "not_fighting";
    if (!this.loadout.includes(spell) || !duelSpell(spell)) return "not_in_loadout";
    const me = this.side(this.you);
    if (me.disarmedUntil > arrival + CAST_SLACK_MS) return "disarmed";
    if (me.staggeredUntil > arrival + CAST_SLACK_MS) return "staggered";
    if (this.pending) return "busy";
    if (me.casting && me.casting.releaseMs > arrival + CAST_SLACK_MS) return "busy";
    if (this.readyAt(this.you, spell) > arrival + CAST_SLACK_MS) return "cooldown";
    return null;
  }

  /**
   * How long (ms) until our `spell` could be cast at `arrival`: 0 now, null
   * never (not in the loadout, the duel over, watching). Used to hold a key
   * pressed a moment early and send it on time.
   */
  readyIn(spell: number, arrival: number): number | null {
    const why = this.refusal(spell, arrival);
    if (why === null) return 0;
    if (why === "not_in_loadout" || !this.you || this.ended) return null;
    const me = this.side(this.you);
    let at = arrival;
    if (why === "not_fighting") at = Math.max(at, this.fightAtMs);
    at = Math.max(at, me.disarmedUntil, me.staggeredUntil, me.casting?.releaseMs ?? 0, this.pending?.releaseMs ?? 0, this.readyAt(this.you, spell));
    return Math.max(0, at - arrival - CAST_SLACK_MS);
  }

  /** We sent `spell`, to arrive at duel time `arrival`: show it now. */
  predict(spell: number, arrival: number, sentLocal: number): void {
    const def = duelSpell(spell);
    if (!def || !this.you) return;
    this.pending = { spell, startMs: arrival, releaseMs: arrival + def.castMs, sentLocal };
    // Starting a cast drops our shield (the server does the same).
    this.side(this.you).shield = null;
  }

  /** Drops a prediction the server never answered (sent before `beforeLocal`). */
  expirePending(beforeLocal: number): boolean {
    if (this.pending && this.pending.sentLocal < beforeLocal) {
      this.pending = null;
      return true;
    }
    return false;
  }

  /** One tick's events. Returns what to show. */
  apply(msg: DuelEventsMsg): DuelCue[] {
    const cues: DuelCue[] = [];
    if (msg.duelId !== this.duelId) return cues;
    const at = msg.atMs;
    this.lastAtMs = Math.max(this.lastAtMs, at);
    for (const e of msg.events) this.applyEvent(e, at, cues);
    return cues;
  }

  private applyEvent(e: DuelEvent, at: number, cues: DuelCue[]): void {
    switch (e.kind) {
      case "fight":
        cues.push({ kind: "fight" });
        break;
      case "castStarted": {
        const s = this.side(e.side);
        const def = duelSpell(e.spell);
        const startMs = e.releaseMs - (def?.castMs ?? 0);
        s.casting = { spell: e.spell, startMs, releaseMs: e.releaseMs };
        s.cooldowns.set(e.spell, startMs + (def?.cooldownMs ?? 0));
        s.shield = null;
        if (e.side === this.you) this.pending = null;
        cues.push({ kind: "cast", side: e.side, spell: e.spell, releaseMs: e.releaseMs });
        break;
      }
      case "refused":
        if (e.side === this.you) this.pending = null;
        cues.push({ kind: "refused", side: e.side, spell: e.spell, reason: e.reason });
        break;
      case "interrupted": {
        const s = this.side(e.side);
        s.casting = null;
        if (e.side === this.you) this.pending = null;
        cues.push({ kind: "interrupted", side: e.side, spell: e.spell });
        break;
      }
      case "shieldUp": {
        const s = this.side(e.side);
        s.casting = null;
        s.shield = { spell: e.spell, startMs: at, untilMs: e.untilMs, perfectUntilMs: e.perfectUntilMs };
        cues.push({ kind: "shield", side: e.side, spell: e.spell, untilMs: e.untilMs, perfectUntilMs: e.perfectUntilMs });
        break;
      }
      case "launched": {
        this.side(e.side).casting = null;
        const from = metres(e.from);
        // Stamped at the end of the tick it was released in, after flying that tick.
        this.projectiles.set(e.id, { id: e.id, side: e.side, spell: e.spell, from, dir: unit(from, metres(e.to), e.side), speedMps: e.speedCms / 100, t0: at - TICK_MS, reflected: false });
        cues.push({ kind: "launched", side: e.side, spell: e.spell, id: e.id });
        break;
      }
      case "blocked": {
        const p = this.projectiles.get(e.id);
        this.projectiles.delete(e.id);
        cues.push({ kind: "blocked", id: e.id, side: e.by, spell: p?.spell ?? 0, pos: p ? projectileAt(p, at) : null });
        break;
      }
      case "reflected": {
        const p = this.projectiles.get(e.id);
        const from = metres(e.from);
        const turned: ProjectileView = { id: e.id, side: e.by, spell: p?.spell ?? 0, from, dir: unit(from, metres(e.to), e.by), speedMps: p?.speedMps ?? 14, t0: at, reflected: true };
        this.projectiles.set(e.id, turned);
        cues.push({ kind: "reflected", id: e.id, side: e.by, spell: turned.spell, pos: from });
        break;
      }
      case "shieldBroken":
        this.side(e.side).shield = null;
        cues.push({ kind: "shattered", side: e.side });
        break;
      case "hit": {
        const p = this.projectiles.get(e.id);
        this.projectiles.delete(e.id);
        this.side(e.target).poise = e.poise;
        cues.push({ kind: "hit", id: e.id, side: e.target, spell: p?.spell ?? 0, damage: e.damage, poise: e.poise, pos: p ? projectileAt(p, at) : null });
        break;
      }
      case "disarmed": {
        const s = this.side(e.side);
        s.disarmedUntil = e.untilMs;
        s.shield = null;
        s.casting = null;
        if (e.side === this.you) this.pending = null;
        cues.push({ kind: "disarmed", side: e.side, untilMs: e.untilMs });
        break;
      }
      case "missed": {
        const p = this.projectiles.get(e.id);
        this.projectiles.delete(e.id);
        cues.push({ kind: "missed", id: e.id, spell: p?.spell ?? 0, pos: p ? projectileAt(p, at) : null });
        break;
      }
      case "ended":
        this.finish(e.winner, e.reason);
        cues.push({ kind: "ended", winner: e.winner, reason: e.reason });
        break;
      case "rooted":
        this.side(e.side).rootedUntil = e.untilMs;
        cues.push({ kind: "rooted", side: e.side, untilMs: e.untilMs });
        break;
      case "slowed":
        this.side(e.side).slowed = { untilMs: e.untilMs, pct: e.pct };
        cues.push({ kind: "slowed", side: e.side, untilMs: e.untilMs, pct: e.pct });
        break;
      case "pulled": {
        const s = this.side(e.side);
        const from = metres(e.from);
        const to = metres(e.to);
        // The server drops the shield and breaks the cast (its `Interrupted` comes first).
        s.pull = { from, to, startMs: at, endMs: e.untilMs };
        s.shield = null;
        s.casting = null;
        if (e.side === this.you) this.pending = null;
        cues.push({ kind: "pulled", side: e.side, from, to, untilMs: e.untilMs });
        break;
      }
      case "staggered":
        this.side(e.side).staggeredUntil = e.untilMs;
        cues.push({ kind: "staggered", side: e.side, untilMs: e.untilMs });
        break;
      case "cleansed": {
        const s = this.side(e.side);
        const freed = s.rootedUntil > at || (s.slowed !== null && s.slowed.untilMs > at);
        s.rootedUntil = Math.min(s.rootedUntil, at);
        s.slowed = null;
        s.casting = null;
        if (e.side === this.you) this.pending = null;
        cues.push({ kind: "cleansed", side: e.side, spell: e.spell, freed });
        break;
      }
      case "wardUp": {
        const s = this.side(e.side);
        s.wardUntil = e.untilMs;
        s.casting = null;
        if (e.side === this.you) this.pending = null;
        cues.push({ kind: "wardUp", side: e.side, spell: e.spell, untilMs: e.untilMs });
        break;
      }
      case "warded": {
        const p = this.projectiles.get(e.id);
        this.projectiles.delete(e.id);
        this.side(e.by).wardUntil = 0;
        cues.push({ kind: "warded", id: e.id, side: e.by, spell: p?.spell ?? 0, pos: p ? projectileAt(p, at) : null });
        break;
      }
      case "wardFaded":
        this.side(e.side).wardUntil = 0;
        cues.push({ kind: "wardFaded", side: e.side });
        break;
    }
  }

  private finish(winner: DuelSide | null, reason: DuelEndReason): void {
    this.ended = { winner, reason };
    this.projectiles.clear();
    this.pending = null;
    for (const s of this.sides) {
      s.casting = null;
      s.shield = null;
      // The server clears every hindrance and ward at the end.
      s.rootedUntil = 0;
      s.slowed = null;
      s.staggeredUntil = 0;
      s.wardUntil = 0;
      s.pull = null;
    }
  }

  /** `DuelResult`: final Poise, our rewards, when we go back. */
  applyResult(msg: DuelResultMsg, localMs: number): void {
    if (msg.duelId !== this.duelId) return;
    if (!this.ended) this.finish(msg.winner, msg.reason);
    this.ended = { winner: msg.winner, reason: msg.reason };
    this.sides[0].poise = msg.poise[0];
    this.sides[1].poise = msg.poise[1];
    this.outcome = { winner: msg.winner, reason: msg.reason, poise: msg.poise, rewards: msg.rewards, returnAtLocal: localMs + msg.returnInMs };
  }

  /** From our side's point of view: "win", "loss" or "draw" (null when watching or not over). */
  verdict(): "win" | "loss" | "draw" | null {
    if (!this.ended || !this.you) return null;
    if (this.ended.winner === null) return "draw";
    return this.ended.winner === this.you ? "win" : "loss";
  }
}

function sideFromSnapshot(s: DuelSnapshot, i: 0 | 1): SideView {
  const d = s.sides[i];
  const casting = d.casting ? { spell: d.casting[0], startMs: d.casting[1] - (duelSpell(d.casting[0])?.castMs ?? 0), releaseMs: d.casting[1] } : null;
  const shield = d.shieldUntil !== null ? { spell: 0, startMs: s.atMs, untilMs: d.shieldUntil, perfectUntilMs: 0 } : null;
  return {
    poise: d.poise,
    casting,
    shield,
    disarmedUntil: d.disarmedUntil ?? 0,
    cooldowns: new Map(d.cooldowns),
    rootedUntil: d.rootedUntil ?? 0,
    slowed: d.slowed ? { untilMs: d.slowed[0], pct: d.slowed[1] } : null,
    staggeredUntil: d.staggeredUntil ?? 0,
    wardUntil: d.wardUntil ?? 0,
    pull: null,
  };
}

/** The words for a refused cast, shown on the spell bar. */
export function refusalText(reason: CastRefusal): string {
  switch (reason) {
    case "not_in_loadout":
      return "Not in your loadout";
    case "cooldown":
      return "Not ready yet";
    case "disarmed":
      return "Disarmed!";
    case "busy":
      return "Still casting";
    case "not_fighting":
      return "Wait for the start";
    case "staggered":
      return "Staggered!";
  }
}

/** How a duel ended, in words, from our side (or a spectator's). */
export function endText(view: Pick<DuelView, "you" | "duelists">, winner: DuelSide | null, reason: DuelEndReason): string {
  const name = (s: DuelSide) => view.duelists[sideIndex(s)].name;
  if (winner === null) return reason === "timeout" ? "Time ran out with Poise level." : "Both duelists left the stage.";
  const loser = name(otherSide(winner));
  switch (reason) {
    case "poise":
      return `${loser} ran out of Poise.`;
    case "timeout":
      return `Time ran out: ${name(winner)} kept more Poise.`;
    case "forfeit":
      return `${loser} forfeited.`;
    case "disconnect":
      return `${loser} left the duel.`;
  }
}

/** A duel's rewards in words for the chat line: the XP, then the house points it gave ("+40 XP, +2 house points for Roarwing"). */
export function duelRewardsText(rewards: Pick<DuelRewards, "xp" | "capped" | "housePoints">, houseName: string): string {
  const xp = xpText(rewards);
  if (rewards.housePoints <= 0) return xp;
  const points = `+${rewards.housePoints} house point${rewards.housePoints === 1 ? "" : "s"} for ${houseName}`;
  return rewards.xp > 0 ? `${xp}, ${points}` : `${xp} ${points}.`;
}

/** A duel's XP in words ("+40 XP", or why there was none). */
export function xpText(rewards: Pick<DuelRewards, "xp" | "capped">): string {
  const r = DUEL_RULES.rewards;
  // `capped`: the day's dueling XP, or the day's rewarded duels against
  // this opponent (`pair_daily_max`), cut the XP (docs/architecture.md).
  const limits = `today's dueling XP, or ${r.pair_daily_max} rewarded duels a day with the same opponent`;
  if (rewards.xp > 0) return `+${rewards.xp} XP${rewards.capped ? ` (a daily limit cut it: ${limits})` : ""}`;
  if (rewards.capped) return `No XP: a daily limit is reached (${limits}).`;
  return `No XP: duels shorter than ${Math.round(r.min_fight_ms / 1000)} s give none.`;
}
