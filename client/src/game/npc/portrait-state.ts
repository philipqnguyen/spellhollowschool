// Which talking portraits come alive (docs/architecture.md "NPCs"):
// a portrait's subject is drawn only on our floor within
// `DRAW_M`, the nearest `MAX_SUBJECTS` at most; within `alive_radius_m` it
// breathes and shifts (the idle clip plays), beyond it holds still (the clip
// frozen: a painting); within `look_radius_m` its head follows us. A nod
// dips the head for `NOD_MS`. Every other portrait in the castle (the
// decorative clusters) is never touched. Pure; unit tested in
// portrait-state.test.ts.

import type { NpcRulesView, PortraitView } from "../../wasm/sim_wasm";

/** A subject is drawn within this (m), on our floor: beyond, the canvas shows its backdrop alone. */
export const DRAW_M = 22;
/** At most this many subjects are drawn at once (the nearest). */
export const MAX_SUBJECTS = 4;
/** A canvas centre this far above our feet or less (m) hangs on our floor. */
export const FLOOR_BAND_M: readonly [number, number] = [-0.5, 4.5];
/** A nod: the head dips and comes back up over this long (ms). */
export const NOD_MS = 900;
/** How far below the eyes a nod looks (m, at the subject's distance from us). */
export const NOD_DIP_M = 0.55;
/** The slow shift: the body turns this far each way (radians) over this period (s). */
export const SHIFT_YAW = 0.07;
export const SHIFT_PERIOD_S = 9;

export interface SubjectState {
  index: number;
  /** Horizontal distance to us (m). */
  d: number;
  alive: boolean;
  looks: boolean;
}

type Rules = Pick<NpcRulesView, "alive_radius_m" | "look_radius_m">;

/** The subjects to draw this frame from where we stand (`me`: our feet; null: none, e.g. not in the castle). Nearest first. */
export function subjectStates(portraits: readonly PortraitView[], me: readonly [number, number, number] | null, rules: Rules, out: SubjectState[] = []): SubjectState[] {
  out.length = 0;
  if (!me) return out;
  for (let i = 0; i < portraits.length; i++) {
    const p = portraits[i]!;
    const dy = p.pos[1]! - me[1];
    if (dy < FLOOR_BAND_M[0] || dy > FLOOR_BAND_M[1]) continue;
    const d = Math.hypot(p.pos[0]! - me[0], p.pos[2]! - me[2]);
    if (d > DRAW_M) continue;
    // In front of the wall only: behind it (the next room) the frame is not seen.
    if (!inFrontOf(p, me[0], me[2])) continue;
    out.push({ index: i, d, alive: d <= rules.alive_radius_m, looks: d <= rules.look_radius_m });
  }
  out.sort((a, b) => a.d - b.d);
  if (out.length > MAX_SUBJECTS) out.length = MAX_SUBJECTS;
  return out;
}

/** A point this far behind the canvas plane (m) or less still counts as in front (the frame stands off the wall; the next room is a wall's thickness further). */
export const IN_FRONT_M = -0.2;

/**
 * Whether (x, z) is in front of portrait `p`'s wall: only there is it seen,
 * does it notice a passer-by, or can it be talked to. Behind the wall (the
 * room next door, a passage in the wall) it is not there at all.
 */
export function inFrontOf(p: { pos: readonly number[]; yaw: number }, x: number, z: number): boolean {
  return (x - p.pos[0]!) * Math.sin(p.yaw) + (z - p.pos[2]!) * Math.cos(p.yaw) >= IN_FRONT_M;
}

/** How far a nod started at `startMs` dips the look now (0..1: down, then back up). */
export function nodDip(startMs: number, nowMs: number): number {
  const t = (nowMs - startMs) / NOD_MS;
  if (!(t >= 0 && t < 1)) return 0;
  return Math.sin(t * Math.PI);
}

/** The slow shift's turn (radians) at `seconds`, out of phase per portrait. */
export function shiftYaw(seconds: number, index: number): number {
  return SHIFT_YAW * Math.sin((seconds / SHIFT_PERIOD_S) * Math.PI * 2 + index * 1.7);
}
