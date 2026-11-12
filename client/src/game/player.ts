// The local player: client-predicted movement through sim-wasm's
// `SimWorld.moveAndSlide`, which is the same sim-core code the server validates
// `Move` messages with, so an honest client is never corrected.
//
// `MoveSender` reports the predicted position at 10 Hz (CLIENT_MOVE_HZ) while
// the player moves, and once a second while standing still.
//
// Spells to level 20 (protocol v11): `force` is the server moving us
// (Adveni, `ServerMsg::ForcedMove`): the predicted position slides linearly
// to where it says over `moveMs`, the keys do nothing until `holdMs`, then
// prediction carries on from there. (The server drops our moves meanwhile.)

import type { Anim, FlyParams, MoveParams, SimWorld } from "../wasm/sim_wasm";
import type { BroomFlight, FlightInput } from "./flight/flight";
import type { MoveInput } from "./input";
import { inputDirection, turnToward } from "./motion";

/** Turn rate of the character toward its walking direction (rad/s). */
const TURN_RATE = 12;
/** Longest frame simulated in one step (s); longer frames (a hidden tab) are clamped. */
const MAX_FRAME = 0.1;

/** Spells L20: a slide the server started (Adveni). Times in seconds since it began. */
interface Forced {
  from: [number, number, number];
  to: [number, number, number];
  moveS: number;
  holdS: number;
  t: number;
}

/** Where a forced slide from `from` to `to` over `moveS` is `t` seconds in: a linear lerp, then `to`. Pure. */
export function forcedAt(from: readonly number[], to: readonly number[], moveS: number, t: number): [number, number, number] {
  const k = moveS > 0 ? Math.max(0, Math.min(1, t / moveS)) : 1;
  return [from[0]! + (to[0]! - from[0]!) * k, from[1]! + (to[1]! - from[1]!) * k, from[2]! + (to[2]! - from[2]!) * k];
}

export class LocalPlayer {
  x = 0;
  y = 0;
  z = 0;
  /** Facing, radians (yaw 0 = +Z). */
  yaw = 0;
  anim: Anim = "idle";
  private forced: Forced | null = null;

  constructor(
    private readonly sim: SimWorld,
    private params: MoveParams,
  ) {}

  /** New movement constants, e.g. a potion's speed (`moveParams(speedPct)`, as the server validates). */
  setParams(params: MoveParams): void {
    this.params = params;
  }

  placeAt(pos: ArrayLike<number>, yaw?: number): void {
    this.forced = null;
    this.x = pos[0]!;
    this.y = pos[1]!;
    this.z = pos[2]!;
    if (yaw !== undefined) this.yaw = yaw;
    this.anim = "idle";
  }

  /**
   * Advances one frame. `look` is the camera's horizontal view direction, so W
   * walks away from the camera.
   */
  update(dtSeconds: number, input: MoveInput, look: readonly [number, number]): void {
    if (this.forced) {
      this.forcedStep(dtSeconds);
      return;
    }
    const dt = Math.min(dtSeconds, MAX_FRAME);
    const dir = inputDirection(input.forward, input.strafe, look[0], look[1]);
    if (!dir || dt <= 0) {
      this.anim = "idle";
      return;
    }
    const speed = input.run ? this.params.runSpeed : this.params.walkSpeed;
    const next = this.sim.moveAndSlide(this.x, this.y, this.z, dir[0] * speed * dt, dir[1] * speed * dt);
    const moved = Math.hypot(next[0]! - this.x, next[2]! - this.z);
    this.x = next[0]!;
    this.y = next[1]!;
    this.z = next[2]!;
    // Face where we try to go (also when pressed against a wall), but only
    // animate as walking when we actually got somewhere.
    this.yaw = turnToward(this.yaw, Math.atan2(dir[0], dir[1]), TURN_RATE * dt);
    // (Bound, at speed 0: turning on the spot, never walking.)
    this.anim = speed <= 0 || moved < speed * dt * 0.1 ? "idle" : input.run ? "run" : "walk";
  }

  /**
   * Spells L20: the server moves us to `to` (Adveni): a slide over `moveMs`,
   * then standing still until `holdMs` (both from now); the keys do nothing
   * meanwhile. A later correction or zone change (`placeAt`) ends it.
   */
  force(to: ArrayLike<number>, moveMs: number, holdMs: number): void {
    const moveS = Math.max(0, moveMs) / 1000;
    this.forced = { from: [this.x, this.y, this.z], to: [to[0]!, to[1]!, to[2]!], moveS, holdS: Math.max(moveS, holdMs / 1000), t: 0 };
    this.anim = "idle";
  }

  /** Whether the server is moving us (the slide and its hold). */
  get beingMoved(): boolean {
    return this.forced !== null;
  }

  private forcedStep(dtSeconds: number): void {
    const f = this.forced!;
    // Real time (not clamped like a walking step): the slide keeps the server's pace.
    f.t += Math.max(0, dtSeconds);
    const p = forcedAt(f.from, f.to, f.moveS, f.t);
    this.x = p[0];
    this.y = p[1];
    this.z = p[2];
    this.anim = "idle";
    if (f.t >= f.holdS) this.forced = null;
  }

  /**
   * One frame on a broom. `broom` turns the keys into a move
   * (flight/flight.ts), which goes through sim-wasm's `flyAndSlide` (the
   * airspace, the no-fly boxes, the ground: the server's own rules) with the
   * speed tier and Featherlight's percent the server checks with.
   */
  fly(dtSeconds: number, broom: BroomFlight, input: FlightInput, lookYaw: number | null, params: FlyParams, tier: number, broomPct: number): void {
    const dt = Math.min(dtSeconds, MAX_FRAME);
    const d = broom.step(dt, input, params, lookYaw);
    if (d[0] !== 0 || d[1] !== 0 || d[2] !== 0) {
      const next = this.sim.flyAndSlide(this.x, this.y, this.z, d[0], d[1], d[2], tier, broomPct);
      this.x = next[0]!;
      this.y = next[1]!;
      this.z = next[2]!;
    }
    this.yaw = broom.yaw;
    this.anim = broom.anim;
  }
}

export interface MoveFrame {
  seq: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  anim: Anim;
}

/** Decides when to send `Move` and with which sequence number. Pure, unit tested. */
export class MoveSender {
  private seq = 0;
  private acc = 0;
  private sinceSent = Infinity;
  private last: Omit<MoveFrame, "seq"> | null = null;
  /** Corrections for moves up to this seq were sent before we snapped; ignore them. */
  private ignoreCorrectionsUpTo = 0;

  constructor(
    private readonly hz: number,
    private readonly idleHeartbeatSeconds = 1,
  ) {}

  /** Starts over for a new connection (the server tracks `seq` per socket). */
  reset(): void {
    this.seq = 0;
    this.acc = 0;
    this.sinceSent = Infinity;
    this.last = null;
    this.ignoreCorrectionsUpTo = 0;
  }

  get lastSeq(): number {
    return this.seq;
  }

  /**
   * Call every frame with the predicted state; returns the frame to send, if any.
   * Sends on a fixed 1/hz cadence while anything changes, and on a slow
   * heartbeat while idle.
   */
  tick(dt: number, x: number, y: number, z: number, yaw: number, anim: Anim): MoveFrame | null {
    const interval = 1 / this.hz;
    this.acc += dt;
    this.sinceSent += dt;
    if (this.acc < interval) return null;
    // Keep the cadence, but don't burst to catch up after a stall.
    this.acc = Math.min(this.acc - interval, interval);

    const l = this.last;
    const changed = !l || l.x !== x || l.y !== y || l.z !== z || l.yaw !== yaw || l.anim !== anim;
    if (!changed && this.sinceSent < this.idleHeartbeatSeconds) return null;
    this.last = { x, y, z, yaw, anim };
    this.sinceSent = 0;
    this.seq = (this.seq + 1) >>> 0;
    return { seq: this.seq, x, y, z, yaw, anim };
  }

  /**
   * Whether a `Correction` for `seq` should be applied. After snapping, the
   * moves already in flight were predicted from the rejected position, so their
   * corrections are stale.
   */
  acceptCorrection(seq: number): boolean {
    if (seq <= this.ignoreCorrectionsUpTo) return false;
    this.ignoreCorrectionsUpTo = this.seq;
    // Report the snapped position promptly.
    this.last = null;
    return true;
  }
}
