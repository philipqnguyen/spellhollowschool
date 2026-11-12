// Turning round on a bench (docs/architecture.md).
// Sitting on a two-sided seat (castle/seats.ts: a backless bench with clear
// floor either side), a quick tap of the movement direction opposite our
// facing turns us to sit facing the other way; holding it stands us up and
// walks off as before.
//
// `BenchTap` tells a tap from a hold. Only a press that could be a turn
// waits: seated on a bench or chair, a movement direction (camera-relative,
// as walking) more than `BENCH_FLIP_ANGLE` from our facing, from the keys
// (auto run never turns us). Such a press keeps us seated for up to
// `BENCH_TAP_MS`: released by then it is a tap (turn round on a two-sided
// seat; on a one-way seat nothing happens), still held then it stands us up
// from that frame on, as any other press does at once. Any other press
// (forward, sideways, standing, sitting on the floor) moves us the frame it
// starts, as before; a second key or a camera turn that makes the direction
// no longer opposite ends the wait at once.
//
// The turn itself is our yaw turned half round: the `Move` that follows
// keeps the position, which keeps the server's sit (realtime/world.rs
// `on_move`: a move that does not change the position keeps an emote) and
// replicates the yaw; every client draws the seat on the side nearest the
// sitter's yaw (`Seats.find`). `SeatTurns` eases the drawn turn over
// `BENCH_TURN_S`, the knees tucked up mid-turn so the legs swing over.

import type { SeatPlace } from "./castle/seats";
import { wrapAngle } from "./castle/seats";

/** A press released within this (ms) is a tap: turn round on a two-sided bench. Longer, a hold: stand up. */
export const BENCH_TAP_MS = 200;
/** A movement direction this far (rad) or further from the sitter's facing is "opposite" (120 degrees). */
export const BENCH_FLIP_ANGLE = (2 * Math.PI) / 3;
/** The drawn turn on the bench lasts this long (s). */
export const BENCH_TURN_S = 0.4;
/** Mid-turn the seat's drop is drawn this much shorter (a share of it), so the knees tuck up over the bench. */
export const BENCH_TUCK = 0.6;

const OPPOSITE_COS = Math.cos(BENCH_FLIP_ANGLE);

/** Where we sit as last drawn: the facing (the side of the seat) and whether the seat turns. */
export interface SeatedFacing {
  yaw: number;
  twoSided: boolean;
}

/** What a frame's movement keys do: move (stand up and walk, as ever), wait (still seated: tap or hold?), or turn round now. */
export type BenchTapResult = "move" | "wait" | "flip";

/** Whether walking along `dir` ([dx, dz], unit) is opposite facing `yaw`. */
export function isOpposite(dir: readonly [number, number], yaw: number): boolean {
  return dir[0] * Math.sin(yaw) + dir[1] * Math.cos(yaw) < OPPOSITE_COS + 1e-9;
}

/** Tap or hold, frame by frame. Pure, unit tested (bench-flip.test.ts). */
export class BenchTap {
  /** When the press that may be a tap began (ms), or -1. */
  private pressAt = -1;
  /** ... on a two-sided seat (a tap turns us round; on a one-way seat it does nothing). */
  private pressTurns = false;
  /** The keys held now are a move: let them through until every movement key is up. */
  private moving = false;

  /**
   * One frame at `nowMs`: `dir` the keys' camera-relative direction (null:
   * no movement key), `seat` where we sit (null: not seated), `auto` auto
   * run on. "wait" and "flip" keep us still this frame; on "flip" turn our
   * yaw half round. A tap on a one-way seat ends as "move" with no key
   * held: nothing happens.
   */
  step(nowMs: number, dir: readonly [number, number] | null, seat: SeatedFacing | null, auto: boolean): BenchTapResult {
    if (!dir) {
      this.moving = false;
      if (this.pressAt < 0) return "move";
      const tap = nowMs - this.pressAt < BENCH_TAP_MS;
      this.pressAt = -1;
      return tap && this.pressTurns ? "flip" : "move";
    }
    const turnable = !auto && seat !== null && isOpposite(dir, seat.yaw);
    if (this.pressAt >= 0) {
      if (turnable && nowMs - this.pressAt < BENCH_TAP_MS) return "wait";
      this.pressAt = -1;
      this.moving = true;
      return "move";
    }
    if (this.moving || !turnable) {
      this.moving = true;
      return "move";
    }
    this.pressAt = nowMs;
    this.pressTurns = seat!.twoSided;
    return "wait";
  }
}

/** Per character, the drawn turn on a bench: which seat, the side drawn now, the turn in progress. */
interface Turn {
  seat: number;
  /** The side's yaw the turn goes to. */
  to: number;
  /** ... from this drawn yaw. */
  from: number;
  /** Seconds into the turn (>= BENCH_TURN_S: done). */
  t: number;
  /** The frame it was last drawn in. */
  frame: number;
}

/** The drawn turn of sitters on two-sided seats, eased (smoothstep) over BENCH_TURN_S. */
export class SeatTurns {
  private readonly turns = new Map<number, Turn>();
  private frame = 0;

  /** A new frame: sitters not drawn last frame are forgotten. */
  begin(): void {
    this.frame++;
    for (const [id, t] of this.turns) if (t.frame < this.frame - 1) this.turns.delete(id);
  }

  /**
   * Character `id` drawn on `place` (as `Seats.find` wrote it) this frame,
   * `dt` seconds on: rewrites `place.yaw` to the drawn yaw (mid-turn when
   * its side just changed on the same seat) and returns the turn's tuck,
   * 0..1 (0 settled): how far to shorten the drawn drop (`BENCH_TUCK`).
   */
  draw(id: number, place: SeatPlace, dt: number): number {
    let t = this.turns.get(id);
    if (!t || t.seat !== place.seat) {
      // Sat down (or onto another seat): no turn.
      t = { seat: place.seat, to: place.yaw, from: place.yaw, t: BENCH_TURN_S, frame: this.frame };
      this.turns.set(id, t);
    } else if (Math.abs(wrapAngle(place.yaw - t.to)) > 1e-3) {
      // Our side changed: turn from where we are drawn now.
      t.from = this.drawn(t);
      t.to = place.yaw;
      t.t = 0;
    } else {
      t.t = Math.min(BENCH_TURN_S, t.t + Math.max(0, dt));
    }
    t.frame = this.frame;
    place.yaw = this.drawn(t);
    const u = t.t / BENCH_TURN_S;
    return u >= 1 ? 0 : Math.sin(Math.PI * u);
  }

  /** Whether `id` is turning on its bench now (tests, probes). */
  turning(id: number): boolean {
    const t = this.turns.get(id);
    return !!t && t.t < BENCH_TURN_S;
  }

  private drawn(t: Turn): number {
    const u = Math.min(1, t.t / BENCH_TURN_S);
    const e = u * u * (3 - 2 * u);
    // Half round either way is the same turn; go the way the yaw's wrap says (always the same for a given pair).
    const d = wrapAngle(t.to - t.from);
    return wrapAngle(t.from + d * e);
  }
}
