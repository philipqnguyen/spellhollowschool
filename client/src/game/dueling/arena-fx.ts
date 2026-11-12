// Draws a duel in the arena from its `DuelView` each frame: the wand
// charging in the spell's colour while a cast is telegraphed, Scutara's ring
// in front of its caster (gold-rimmed while it would reflect), every
// projectile where the server has it now, and a burst for each cue (impact,
// block, reflect, shattered shield, broken cast, disarm). Through one
// `SpellVfx`: one draw call for the whole duel.
//
// Spells to level 20 (protocol v11): Rigora flies as a ringed orb,
// Adveni as a hooked bolt, Tardara as a wall of magenta light across the
// stage; a rooted duelist stands in chalk rings, a slowed one in slow
// ripples, a staggered one is dazed, a pulled one is reeled in on a teal
// tether from its opponent's wand, and a Guardian ward shows a faint silver
// halo (the animal is the guardian hub's). Dissolvo unwinds in gold, a ward
// that stops a spell shatters in silver.
//
// Readable at browser latency: projectiles are flown on the estimated duel
// clock (clock.ts), so they are where the server has them, not a round trip
// behind. Their verdict (hit, blocked) arrives a one-way delay after they
// reach their target, so a projectile that reaches the duelist it is aimed
// at waits there briefly for it instead of flying on through them and then
// exploding behind; one that was sidestepped is not held and flies on (a
// wave is as wide as it is, so it is held across its whole width).

import { audio, isSoundId, type SoundId, type Vec3 } from "../../audio";
import type { DuelSide } from "../../wasm/sim_wasm";
import type { Rgb, SpellVfx } from "../vfx/spell-vfx";
import { DUEL_RULES, DUEL_SPELLS, duelSpell, venue, type DuelSpellInfo } from "./content";
import { otherSide, projectileTravel, type DuelCue, type DuelView, type ProjectileView } from "./state";

/** Where a duelist is drawn this frame (feet, metres; yaw in radians). */
export interface DuelistPose {
  x: number;
  y: number;
  z: number;
  yaw: number;
  /** The drawn wand's tip (characters/renderer.ts `wandTip`), when the duelist is drawn; else it is estimated (`wandTip`). */
  tip?: readonly [number, number, number] | null;
}

/** Chest height above the feet, where spells fly and shields stand (m). */
export const CHEST_M = 1.25;
/** Just over a head, where a daze circles (m above the feet). */
const HEAD_M = 1.95;
/** How far in front of its caster Scutara stands (m). */
const SHIELD_AHEAD_M = 0.55;
/** A ward stops a spell this far out from the duelist (m). */
const WARD_AHEAD_M = 0.45;
/** A projectile this close to the line through its target may be held for its verdict (m, beyond the hit reach). */
const HOLD_SLACK_M = 0.3;
/** How long a projectile may wait at its target for the server's verdict, besides the round trip (ms). */
const HOLD_BASE_MS = 90;
/** The tether lingers this long after a pull arrived (ms). */
const TETHER_TAIL_MS = 220;
/** Projectiles fade over their last few metres of range. */
const FADE_FROM_M = DUEL_RULES.max_range_m - 5;
const FALLBACK_RGB: [number, number, number] = [1, 1, 1];

const rgbOf = (spell: number) => duelSpell(spell)?.rgb ?? FALLBACK_RGB;
const colourOf = (pick: (s: DuelSpellInfo) => unknown, fallback: Rgb): Rgb => DUEL_SPELLS.find((s) => pick(s))?.rgb ?? fallback;

/** Each status in its spell's colour (data/spells.json). */
export const STATUS_RGB = {
  root: colourOf((s) => s.root, [0.94, 0.94, 1]),
  slow: colourOf((s) => s.slow, [1, 0.31, 0.85]),
  pull: colourOf((s) => s.pull, [0.12, 0.82, 0.76]),
  cleanse: colourOf((s) => s.dispel, [1, 0.82, 0.48]),
  ward: colourOf((s) => s.wardMs, [0.81, 0.9, 1]),
} as const;

/**
 * Where along its flight projectile `p` stops to wait at the duelist at
 * `target` (m from its start), or null when it would miss them (sidestepped)
 * or is flying away. A wave reaches across its whole width; a raised shield
 * or ward stops it a little out in front.
 */
export function holdAt(p: ProjectileView, target: { x: number; z: number }, def: DuelSpellInfo | undefined, guarded: "shield" | "ward" | null): number | null {
  const rx = target.x - p.from[0];
  const rz = target.z - p.from[1];
  const along = rx * p.dir[0] + rz * p.dir[1];
  const across = Math.abs(rx * p.dir[1] - rz * p.dir[0]);
  const reach = DUEL_RULES.duelist_radius_m + HOLD_SLACK_M + (def?.waveHalfWidthM || def?.radiusM || 0.2);
  if (along <= 0 || across >= reach) return null;
  return guarded === "shield" ? along - SHIELD_AHEAD_M : guarded === "ward" ? along - WARD_AHEAD_M : along;
}

export class ArenaFx {
  /** Projectile id → local ms it started waiting at its target. */
  private readonly held = new Map<number, number>();
  private stageY = 0.9;

  constructor(private readonly vfx: SpellVfx) {}

  /** A new duel in venue `venueId`: projectiles fly at its marks' height. */
  start(venueId: string): void {
    this.held.clear();
    this.stageY = venue(venueId)?.duelists[0]?.pos[1] ?? 0.9;
  }

  /**
   * The continuous effects for duel time `now` (call between the VFX's
   * `begin` and `end`). `pose(side)` is where each duelist is drawn.
   */
  draw(view: DuelView, now: number, localNow: number, oneWayMs: number, pose: (side: DuelSide) => DuelistPose | null): void {
    const vfx = this.vfx;
    for (const side of ["a", "b"] as const) {
      const me = pose(side);
      if (!me) continue;
      const seed = side === "a" ? 0 : 3;
      const opp = pose(otherSide(side));
      const [fx, fz] = facing(me, opp);
      const cast = view.castOf(side);
      if (cast && now < cast.releaseMs + 60) {
        const span = Math.max(1, cast.releaseMs - cast.startMs);
        const w = wandTip(me, fx, fz);
        vfx.charge(w[0], w[1], w[2], rgbOf(cast.spell), (now - cast.startMs) / span, seed);
      }
      const shield = view.shieldAt(side, now);
      if (shield) {
        const left = shield.untilMs - now;
        const def = duelSpell(shield.spell) ?? duelSpell(10);
        vfx.shield(me.x + fx * SHIELD_AHEAD_M, me.y + CHEST_M, me.z + fz * SHIELD_AHEAD_M, def?.rgb ?? FALLBACK_RGB, Math.min(1, left / 150), now < shield.perfectUntilMs, 1.9, side === "a" ? 0 : 2);
      }
      // The statuses: fading over their last moments.
      const s = view.side(side);
      if (view.wardAt(side, now)) vfx.ward(me.x, me.y + CHEST_M, me.z, STATUS_RGB.ward, Math.min(1, (s.wardUntil - now) / 400), seed);
      if (view.rootedAt(side, now)) vfx.rooted(me.x, me.y, me.z, STATUS_RGB.root, Math.min(1, (s.rootedUntil - now) / 200), seed);
      const slow = view.slowedAt(side, now);
      if (slow) vfx.slowed(me.x, me.y, me.z, STATUS_RGB.slow, Math.min(1, (slow.untilMs - now) / 300), seed);
      if (view.staggeredAt(side, now)) vfx.dazed(me.x, me.y + HEAD_M, me.z, Math.min(1, (s.staggeredUntil - now) / 120), seed);
      const pull = s.pull;
      if (pull && opp && now < pull.endMs + TETHER_TAIL_MS && now >= pull.startMs - 50) {
        const [ox, oz] = facing(opp, me);
        const w = wandTip(opp, ox, oz);
        const strength = now < pull.endMs ? 1 : 1 - (now - pull.endMs) / TETHER_TAIL_MS;
        vfx.tether(w[0], w[1], w[2], me.x, me.y + CHEST_M, me.z, STATUS_RGB.pull, strength);
      }
    }

    const y = this.stageY + CHEST_M;
    for (const p of view.projectiles.values()) {
      const def = duelSpell(p.spell);
      let travel = projectileTravel(p, now);
      // Waiting for the verdict at its target?
      const targetSide = otherSide(p.side);
      const target = pose(targetSide);
      if (target) {
        const guarded = view.shieldAt(targetSide, now) ? "shield" : view.wardAt(targetSide, now) ? "ward" : null;
        const stop = holdAt(p, target, def, guarded);
        if (stop !== null && travel > stop) {
          const since = this.held.get(p.id) ?? localNow;
          this.held.set(p.id, since);
          if (localNow - since < HOLD_BASE_MS + 2 * oneWayMs) travel = stop;
        }
      }
      const x = p.from[0] + p.dir[0] * travel;
      const z = p.from[1] + p.dir[1] * travel;
      const fade = travel > FADE_FROM_M ? Math.max(0, 1 - (travel - FADE_FROM_M) / 5) : 1;
      const rgb = def?.rgb ?? FALLBACK_RGB;
      if (def?.waveHalfWidthM) vfx.wave(x, y, z, p.dir[0], p.dir[1], def.waveHalfWidthM, rgb, fade, p.id);
      else if (def?.root) vfx.bindBolt(x, y, z, p.dir[0], p.dir[1], rgb, fade, p.id);
      else if (def?.pull) vfx.hookBolt(x, y, z, p.dir[0], p.dir[1], rgb, fade, p.id);
      else vfx.bolt(x, y, z, p.dir[0], 0, p.dir[1], rgb, Math.min(1.5, 0.3 + travel), fade, p.id);
    }
    if (this.held.size > 16) for (const id of this.held.keys()) if (!view.projectiles.has(id)) this.held.delete(id);
  }

  /** Plays the bursts for `cues` (from `DuelView.apply`), and their sounds where they happen (`owner`: whose a side's are). */
  play(cues: readonly DuelCue[], pose: (side: DuelSide) => DuelistPose | null, owner: (side: DuelSide) => number | undefined = () => undefined): void {
    const vfx = this.vfx;
    const sound = (id: SoundId, at: Vec3 | null, side: DuelSide | null) => {
      if (at) audio.playAt(id, at, { owner: side ? owner(side) : undefined });
    };
    const y = this.stageY + CHEST_M;
    const chest = (side: DuelSide): [number, number, number] | null => {
      const p = pose(side);
      return p ? [p.x, p.y + CHEST_M, p.z] : null;
    };
    const ahead = (side: DuelSide, by = SHIELD_AHEAD_M): [number, number, number] | null => {
      const me = pose(side);
      if (!me) return null;
      const [fx, fz] = facing(me, pose(otherSide(side)));
      return [me.x + fx * by, me.y + CHEST_M, me.z + fz * by];
    };
    const wand = (side: DuelSide): [number, number, number] | null => {
      const me = pose(side);
      if (!me) return null;
      const [fx, fz] = facing(me, pose(otherSide(side)));
      return wandTip(me, fx, fz);
    };
    for (const c of cues) {
      switch (c.kind) {
        case "cast":
          sound("duel.charge", wand(c.side), c.side);
          break;
        case "shield":
          sound("spell.scutara", ahead(c.side), c.side);
          break;
        case "launched": {
          const w = wand(c.side);
          if (w) vfx.muzzle(w[0], w[1], w[2], rgbOf(c.spell));
          const id = `spell.${duelSpell(c.spell)?.key ?? ""}`;
          if (isSoundId(id)) sound(id, w, c.side);
          break;
        }
        case "hit": {
          const at = chest(c.side) ?? (c.pos ? [c.pos[0], y, c.pos[1]] : null);
          if (at) vfx.flash(at[0], at[1], at[2], rgbOf(c.spell), 1.2, 14);
          sound("duel.hit", at, c.side);
          break;
        }
        case "blocked": {
          const at = ahead(c.side) ?? (c.pos ? [c.pos[0], y, c.pos[1]] : null);
          sound("duel.blocked", at, c.side);
          if (at) {
            vfx.flash(at[0], at[1], at[2], duelSpell(10)?.rgb ?? FALLBACK_RGB, 0.9, 8);
            vfx.flash(at[0], at[1], at[2], rgbOf(c.spell), 0.5, 4);
          }
          break;
        }
        case "reflected": {
          const at = ahead(c.side) ?? [c.pos[0], y, c.pos[1]];
          vfx.flash(at[0], at[1], at[2], [1, 0.84, 0.36], 1.1, 12);
          sound("duel.reflected", at, c.side);
          break;
        }
        case "shattered": {
          const at = ahead(c.side);
          if (at) vfx.shatter(at[0], at[1], at[2], duelSpell(10)?.rgb ?? FALLBACK_RGB);
          sound("duel.shield_broken", at, c.side);
          break;
        }
        case "interrupted": {
          const w = wand(c.side);
          if (w) vfx.fizzle(w[0], w[1], w[2], rgbOf(c.spell));
          sound("duel.interrupted", w, c.side);
          break;
        }
        case "disarmed": {
          const me = pose(c.side);
          if (me) {
            const [fx, fz] = facing(me, pose(otherSide(c.side)));
            const w = wandTip(me, fx, fz);
            vfx.disarm(w[0], w[1], w[2], -fx, -fz);
            sound("duel.disarmed", w, c.side);
          }
          break;
        }
        case "missed":
          if (c.pos) vfx.fizzle(c.pos[0], y, c.pos[1], rgbOf(c.spell));
          sound("duel.missed", c.pos ? [c.pos[0], y, c.pos[1]] : null, null);
          break;
        case "rooted": {
          const me = pose(c.side);
          if (me) vfx.bind(me.x, me.y, me.z, STATUS_RGB.root);
          sound("duel.rooted", chest(c.side), c.side);
          break;
        }
        case "slowed": {
          const me = pose(c.side);
          if (me) vfx.slowHit(me.x, me.y, me.z, STATUS_RGB.slow);
          sound("duel.slowed", chest(c.side), c.side);
          break;
        }
        case "pulled": {
          const at = chest(c.side);
          const dx = c.to[0] - c.from[0];
          const dz = c.to[1] - c.from[1];
          const len = Math.hypot(dx, dz) || 1;
          if (at) vfx.yank(at[0], at[1], at[2], dx / len, dz / len, STATUS_RGB.pull);
          sound("duel.pulled", at, c.side);
          break;
        }
        case "cleansed": {
          const me = pose(c.side);
          if (me) vfx.cleanse(me.x, me.y, me.z, rgbOf(c.spell));
          sound("spell.dissolvo", wand(c.side), c.side);
          if (c.freed) sound("duel.cleansed", chest(c.side), c.side);
          break;
        }
        case "wardUp": {
          const me = pose(c.side);
          if (me) vfx.wardUp(me.x, me.y, me.z, STATUS_RGB.ward);
          sound("spell.custodio", wand(c.side), c.side);
          sound("duel.ward_up", chest(c.side), c.side);
          break;
        }
        case "warded": {
          const at = ahead(c.side, WARD_AHEAD_M) ?? (c.pos ? [c.pos[0], y, c.pos[1]] : null);
          if (at) {
            vfx.wardBreak(at[0], at[1], at[2], STATUS_RGB.ward);
            vfx.flash(at[0], at[1], at[2], rgbOf(c.spell), 0.5, 4);
          }
          sound("duel.warded", at, c.side);
          break;
        }
        case "wardFaded": {
          const at = chest(c.side);
          if (at) vfx.wardFade(at[0], at[1], at[2], STATUS_RGB.ward);
          sound("duel.ward_faded", at, c.side);
          break;
        }
        default:
          break;
      }
      if (c.kind === "hit" || c.kind === "blocked" || c.kind === "missed" || c.kind === "reflected" || c.kind === "warded") this.held.delete(c.id);
    }
  }

  clear(): void {
    this.held.clear();
  }
}

/** The unit direction `me` faces in a duel: toward the opponent (their yaw when alone). */
export function facing(me: DuelistPose, opp: DuelistPose | null): [number, number] {
  if (opp) {
    const dx = opp.x - me.x;
    const dz = opp.z - me.z;
    const len = Math.hypot(dx, dz);
    if (len > 1e-3) return [dx / len, dz / len];
  }
  return [Math.sin(me.yaw), Math.cos(me.yaw)];
}

/** Where a wand's tip is while casting toward (fx, fz): the drawn wand's, else chest high, a little ahead and to the right. */
export function wandTip(me: DuelistPose, fx: number, fz: number): [number, number, number] {
  if (me.tip) return [me.tip[0], me.tip[1], me.tip[2]];
  // Right of the facing direction: (cos yaw, -sin yaw) for facing (sin yaw, cos yaw).
  const rx = fz;
  const rz = -fx;
  return [me.x + fx * 0.5 + rx * 0.22, me.y + CHEST_M + 0.12, me.z + fz * 0.5 + rz * 0.22];
}
