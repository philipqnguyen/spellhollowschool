// Defensive Magic: solo drills against the
// professor, drawn with the duel's spell VFX (game/vfx/spell-vfx.ts) and
// the arena's geometry (chest height, wand tip, the shield ahead).
//
// - A shield beat (`shield_arc`, Scutara): the professor's wand gathers a
//   spell, then a bolt flies at the student and arrives on the beat. It
//   waits at the student's shield for the verdict: a passed trace raises
//   Scutara and the bolt breaks on it; a failed trace or no trace lets it
//   through (a harmless flash at the chest).
// - A spell beat (`spiral` Titillara, `push` Repulsa): a passed trace
//   sends the student's spell at the professor, who blocks it with a
//   shield; a failed one fizzles at the wand.
//
// Which spell a gesture casts comes from the class file's demonstrations
// (data/classes/defence.json), its colour from data/spells.json. The
// timing (`telegraph`) is pure and unit tested (fx.test.ts).

import type { GestureResult } from "../../../wasm/sim_wasm";
import { CHEST_M, facing, wandTip } from "../../dueling/arena-fx";
import { gestureById, spellRgb, SPELLS, type SpellDef } from "../../spells/data";
import classFiles from "../class-files";
import type { BeatState } from "../lesson";
import type { ClassDrill, DrillContext, DrillRound, FxPose, Rgb } from "./hooks";

/** The professor's wand gathers the spell this long before it flies (ms). */
export const CHARGE_MS = 500;
/** The professor's bolt flies this long, landing on the beat (ms). */
export const TELEGRAPH_MS = 1000;
/** After the window, the bolt waits at the shield this much longer for the verdict (ms). */
export const HOLD_MS = 900;
/** The student's spell flies to the professor in this long (ms). */
export const OUT_MS = 450;
/** A raised shield shows this long (ms). */
export const SHIELD_MS = 650;
/** How far in front of its caster a shield stands (m), as in the duel. */
const SHIELD_AHEAD_M = 0.55;
/** The gesture of the shield beats. */
export const SHIELD_GESTURE = "shield_arc";

const WHITE: Rgb = [1, 1, 1];

/** The spell each Defence gesture casts (from the class's demonstrations, else by gesture). */
export const DEFENCE_SPELLS: ReadonlyMap<string, SpellDef> = (() => {
  const out = new Map<string, SpellDef>();
  for (const a of classFiles.defence?.lesson ?? []) {
    if (a.kind !== "demonstrate" || !a.gesture || !a.spell) continue;
    const s = SPELLS.find((x) => x.key === a.spell);
    if (s) out.set(a.gesture, s);
  }
  for (const s of SPELLS) if (s.school === "defence" && !out.has(s.gesture)) out.set(s.gesture, s);
  return out;
})();

/** The professor's telegraphed attack (a duel attack spell's colour: Torpeo). */
const ATTACK = SPELLS.find((s) => s.key === "torpeo") ?? SPELLS.find((s) => s.kind === "attack");

export type TelegraphStage = "none" | "charge" | "flight" | "held";

/** The professor's cast at a shield beat on `atMs`, at `now`: its stage and how far along (0..1). */
export function telegraph(atMs: number, windowMs: number, now: number): { stage: TelegraphStage; progress: number } {
  const launch = atMs - TELEGRAPH_MS;
  if (now < launch - CHARGE_MS) return { stage: "none", progress: 0 };
  if (now < launch) return { stage: "charge", progress: (now - (launch - CHARGE_MS)) / CHARGE_MS };
  if (now < atMs) return { stage: "flight", progress: (now - launch) / TELEGRAPH_MS };
  if (now <= atMs + windowMs + HOLD_MS) return { stage: "held", progress: 1 };
  return { stage: "none", progress: 0 };
}

/** Whether beat `b` is a shield beat. */
export function isShieldBeat(b: BeatState): boolean {
  return b.kind === "trace" && gestureById(b.gesture)?.key === SHIELD_GESTURE;
}

/** Whether a beat's answer (the server's verdict, else the local preview) passes. */
function passing(b: BeatState): boolean {
  const r = b.result ?? b.preview;
  return !!r && !r.rejected && r.score > 0;
}

interface Shot {
  start: number;
  rgb: Rgb;
}

/** What the drill shows besides the telegraphs: spells in flight and raised shields. */
export class DefenceDrill implements ClassDrill {
  /** The student's spells flying at the professor (start: server ms). */
  private shots: Shot[] = [];
  /** The student's shield is up until then (server ms), the professor's likewise. */
  private selfShieldUntil = 0;
  private profShieldUntil = 0;
  /** Shield beats already resolved (their bolt is gone). */
  private readonly resolved = new Set<BeatState>();

  frame(ctx: DrillContext, round: DrillRound | null, now: number): void {
    const me = ctx.self();
    const prof = ctx.professor();
    const vfx = ctx.vfx;
    const shieldRgb = spellRgb(DEFENCE_SPELLS.get(SHIELD_GESTURE));
    const attackRgb = spellRgb(ATTACK);
    if (round && me && prof) {
      for (const b of round.beats) {
        if (!isShieldBeat(b) || this.resolved.has(b) || b.status === "missed" || b.status === "scored") continue;
        const t = telegraph(b.atMs, round.windowMs, now);
        if (t.stage === "none") continue;
        const [fx, fz] = facing(prof, me);
        const from = wandTip(prof, fx, fz);
        if (t.stage === "charge") {
          vfx.charge(from[0], from[1], from[2], attackRgb, t.progress, b.beat);
          continue;
        }
        // In flight: from the professor's wand to the student's shield.
        const to = ahead(me, prof);
        const p = t.stage === "held" ? 1 : easeIn(t.progress);
        const x = from[0] + (to[0] - from[0]) * p;
        const y = from[1] + (to[1] - from[1]) * p;
        const z = from[2] + (to[2] - from[2]) * p;
        const [dx, dy, dz] = unit(to[0] - from[0], to[1] - from[1], to[2] - from[2]);
        vfx.bolt(x, y, z, dx, dy, dz, attackRgb, 0.3 + 1.1 * p, 1, b.beat);
        // A trace sent and passing locally raises the shield while the verdict is on its way.
        if (b.status === "sent" && passing(b)) this.selfShieldUntil = Math.max(this.selfShieldUntil, now + 120);
      }
    }
    // The student's spells in flight.
    if (me && prof) {
      const [fx, fz] = facing(me, prof);
      const from = wandTip(me, fx, fz);
      const to = ahead(prof, me);
      const [dx, dy, dz] = unit(to[0] - from[0], to[1] - from[1], to[2] - from[2]);
      for (let i = this.shots.length - 1; i >= 0; i--) {
        const s = this.shots[i]!;
        const p = (now - s.start) / OUT_MS;
        if (p >= 1) {
          // It breaks on the professor's shield.
          this.shots.splice(i, 1);
          this.profShieldUntil = now + SHIELD_MS;
          vfx.flash(to[0], to[1], to[2], shieldRgb, 0.8, 8);
          vfx.flash(to[0], to[1], to[2], s.rgb, 0.5, 4);
          continue;
        }
        if (p < 0) continue;
        vfx.bolt(from[0] + (to[0] - from[0]) * p, from[1] + (to[1] - from[1]) * p, from[2] + (to[2] - from[2]) * p, dx, dy, dz, s.rgb, 0.3 + p, 1, i + 7);
        if (p > 0.6) this.profShieldUntil = Math.max(this.profShieldUntil, now + 200);
      }
    } else {
      this.shots.length = 0;
    }
    if (me && now < this.selfShieldUntil) {
      const at = ahead(me, prof);
      vfx.shield(at[0], at[1], at[2], shieldRgb, Math.min(1, (this.selfShieldUntil - now) / 150), false, 1.6, 1);
    }
    if (prof && now < this.profShieldUntil) {
      const at = ahead(prof, me);
      vfx.shield(at[0], at[1], at[2], shieldRgb, Math.min(1, (this.profShieldUntil - now) / 150), false, 1.6, 2);
    }
  }

  scored(ctx: DrillContext, beat: BeatState, result: GestureResult, now: number): void {
    const me = ctx.self();
    const prof = ctx.professor();
    const pass = !result.rejected && result.score > 0;
    const key = gestureById(beat.gesture)?.key ?? "";
    if (key === SHIELD_GESTURE) {
      this.resolved.add(beat);
      if (!me) return;
      if (pass) {
        // Scutara holds: the bolt breaks on it.
        this.selfShieldUntil = now + SHIELD_MS;
        const at = ahead(me, prof);
        ctx.vfx.flash(at[0], at[1], at[2], spellRgb(DEFENCE_SPELLS.get(SHIELD_GESTURE)), 0.9, 8);
        ctx.vfx.flash(at[0], at[1], at[2], spellRgb(ATTACK), 0.5, 4);
      } else this.letThrough(ctx, me);
      return;
    }
    const spell = DEFENCE_SPELLS.get(key);
    if (!spell || !me) return;
    const [fx, fz] = facing(me, prof);
    const w = wandTip(me, fx, fz);
    if (pass) {
      ctx.vfx.muzzle(w[0], w[1], w[2], spellRgb(spell));
      this.shots.push({ start: now, rgb: spellRgb(spell) });
      if (this.shots.length > 6) this.shots.shift();
    } else ctx.vfx.fizzle(w[0], w[1], w[2], spellRgb(spell));
  }

  missed(ctx: DrillContext, beat: BeatState, _now?: number): void {
    if (!isShieldBeat(beat) || this.resolved.has(beat)) return;
    this.resolved.add(beat);
    const me = ctx.self();
    if (me) this.letThrough(ctx, me);
  }

  clear(): void {
    this.shots.length = 0;
    this.selfShieldUntil = 0;
    this.profShieldUntil = 0;
    this.resolved.clear();
  }

  /** The professor's spell gets through: a harmless flash at the chest. */
  private letThrough(ctx: DrillContext, me: FxPose): void {
    ctx.vfx.flash(me.x, me.y + CHEST_M, me.z, spellRgb(ATTACK), 0.8, 8);
    ctx.vfx.flash(me.x, me.y + CHEST_M, me.z, WHITE, 0.3, 0);
  }
}

/** Where `me`'s shield stands: in front of them, toward `other`, at chest height. */
function ahead(me: FxPose, other: FxPose | null): [number, number, number] {
  const [fx, fz] = facing(me, other);
  return [me.x + fx * SHIELD_AHEAD_M, me.y + CHEST_M, me.z + fz * SHIELD_AHEAD_M];
}

function unit(x: number, y: number, z: number): [number, number, number] {
  const l = Math.hypot(x, y, z) || 1;
  return [x / l, y / l, z / l];
}

/** Starts slow and lands fast, like a duel bolt leaving the wand. */
function easeIn(t: number): number {
  const c = Math.max(0, Math.min(1, t));
  return c * (0.6 + 0.4 * c);
}
