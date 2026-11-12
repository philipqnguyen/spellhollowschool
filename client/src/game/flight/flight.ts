// Brooms (docs/architecture.md): the flight controller
// and the mount state, pure and unit tested (flight.test.ts). game.ts feeds
// them the keys and the server's `mounted` messages; the local player
// (player.ts `fly`) moves by what `BroomFlight.step` asks through sim-wasm's
// `flyAndSlide`, the same sim-core code the server checks every report with
// (`check_flight`), so an honest flyer is never corrected.
//
// The controls:
//   W / S     speed up / slow down. The speed is kept when you let go (a
//             throttle); slowing all the way is a hover, which is normal.
//   A / D     turn (game.ts turns the camera; the broom follows its view).
//   mouse     drag the camera to steer (or turn it with the arrow keys,
//             input.ts): while moving, the broom turns to where the camera
//             looks. Hovering, you may look round freely.
//   R         auto run: forward like W held (input.ts).
//   Space     climb;  Shift  dive.  Neither: the broom levels out.
//   B         mount (on the grounds, after your first Flying lesson) or land
//             (within half a metre of the ground, never over the water).

import type { Anim, FlyParams, MountRefused } from "../../wasm/sim_wasm";
import { turnToward } from "../motion";

/** How fast the broom turns toward the camera's view while moving (rad/s). */
export const BROOM_TURN_RATE = 2.4;
/** How fast A / D swing the camera (and so the broom) round (rad/s). */
export const KEY_TURN_RATE = 1.6;
/** S slows the broom this much faster than W speeds it up. */
const BRAKE = 1.6;
/** The vertical speed follows Space / Shift this fast (times the acceleration). */
const VERTICAL_RESPONSE = 2;
/** Below this speed (m/s) the broom hovers: it no longer turns with the camera. */
export const HOVER_SPEED = 0.3;
/** The broom's drawn pitch: this much per unit of climb over speed, at most MAX_PITCH (radians). */
const PITCH_GAIN = 0.6;
const MAX_PITCH = 0.15;
/** The drawn bank into a turn: radians per rad/s of turning, at most MAX_BANK. */
const BANK_GAIN = 0.22;
const MAX_BANK = 0.2;
/** How fast the drawn pitch and bank settle (1/s): the auto-level. */
const LEVEL_RATE = 4;
/** A mount or landing request with no answer this long (ms) is forgotten. */
export const MOUNT_PENDING_MS = 3000;

/** The keys the broom reads each frame. */
export interface FlightInput {
  /** +1 W (faster), -1 S (slower), 0 neither. */
  throttle: number;
  /** +1 D (turn right), -1 A (turn left), 0 neither. */
  turn: number;
  /** +1 Space (climb), -1 Shift (dive), 0 neither: level out. */
  climb: number;
}

/** Turns `[dx, dz]` (a horizontal direction) into a yaw (yaw 0 = +Z). */
export function yawOfLook(dx: number, dz: number): number | null {
  return Math.hypot(dx, dz) < 1e-6 ? null : Math.atan2(dx, dz);
}

/**
 * One rider's broom: its speed, heading and climb. `step` turns the keys
 * into this frame's move (metres); the caller moves through `flyAndSlide`.
 */
export class BroomFlight {
  /** Forward speed (m/s), 0 to the tier's top speed. */
  speed = 0;
  /** Vertical speed (m/s), up positive. */
  vy = 0;
  /** Heading (radians, yaw 0 = +Z). */
  yaw = 0;
  /** The broom's drawn pitch (nose up positive) and bank (radians), eased: the auto-level. */
  pitch = 0;
  bank = 0;

  /** A fresh start (mounting): still, facing `yaw`. */
  reset(yaw: number): void {
    this.speed = 0;
    this.vy = 0;
    this.yaw = yaw;
    this.pitch = 0;
    this.bank = 0;
  }

  /**
   * Advances `dt` seconds. `params` are the broom's flight numbers
   * (`SimWorld.flyParams(tier, broomPct)`); `lookYaw` is where the camera
   * looks (null: keep the heading). Returns the move `[dx, dy, dz]` (m).
   */
  step(dt: number, input: FlightInput, params: FlyParams, lookYaw: number | null): [number, number, number] {
    if (!(dt > 0)) return [0, 0, 0];
    const top = Math.max(0, params.speed);
    const acc = Math.max(0.1, params.acceleration);
    if (input.throttle > 0) this.speed += acc * dt;
    else if (input.throttle < 0) this.speed -= acc * BRAKE * dt;
    // Never over the top speed the server checks with (a potion wearing off lowers it at once).
    this.speed = Math.min(top, Math.max(0, this.speed));

    const climb = Math.max(0, params.climbSpeed);
    const wantVy = Math.sign(input.climb) * climb;
    // The climb gathers and eases off at twice the acceleration (a third of a second to full climb).
    const dv = acc * VERTICAL_RESPONSE * dt;
    this.vy += Math.max(-dv, Math.min(dv, wantVy - this.vy));
    this.vy = Math.min(climb, Math.max(-climb, this.vy));

    const before = this.yaw;
    // A / D turn the broom (game.ts swings the camera with it); yaw 0 faces +Z, so right is a smaller yaw.
    if (input.turn) this.yaw = wrap(this.yaw - Math.sign(input.turn) * KEY_TURN_RATE * dt);
    if (lookYaw !== null && this.speed > HOVER_SPEED) this.yaw = turnToward(this.yaw, lookYaw, BROOM_TURN_RATE * dt);
    let turned = this.yaw - before;
    if (turned > Math.PI) turned -= 2 * Math.PI;
    if (turned < -Math.PI) turned += 2 * Math.PI;

    const ease = 1 - Math.exp(-LEVEL_RATE * dt);
    const wantPitch = clamp((PITCH_GAIN * this.vy) / Math.max(2, this.speed), MAX_PITCH);
    const wantBank = clamp(BANK_GAIN * (turned / dt), MAX_BANK);
    this.pitch += (wantPitch - this.pitch) * ease;
    this.bank += (wantBank - this.bank) * ease;

    const d = this.speed * dt;
    return [Math.sin(this.yaw) * d, this.vy * dt, Math.cos(this.yaw) * d];
  }

  /** The anim the server is told and others see: `fly` while moving, `hover` while still. */
  get anim(): Anim {
    return this.speed > HOVER_SPEED || Math.abs(this.vy) > HOVER_SPEED ? "fly" : "hover";
  }
}

function wrap(a: number): number {
  const tau = 2 * Math.PI;
  return ((a % tau) + tau) % tau;
}

function clamp(v: number, max: number): number {
  return Math.max(-max, Math.min(max, v));
}

// ------------------------------------------------------------------ mounting

/** What the player is doing about the broom. */
export type MountPhase = "walking" | "mounting" | "flying" | "landing";

/** What the game should do with a `mounted` message. */
export type MountOutcome =
  /** Now mounted: lift to `SimWorld.mount`'s position and fly. */
  | { kind: "lift" }
  /** Now walking: stand at `SimWorld.dismount`'s position. */
  | { kind: "land" }
  /** Refused: say why (the state stays as it was). */
  | { kind: "refused"; text: string }
  /** Nothing changes (a repeat). */
  | { kind: "none" };

/**
 * The mount state as the server last said it, with the request in flight.
 * The server decides (`ClientMsg::Mount` → `ServerMsg::Mounted`); it also
 * mounts students joining the Flying class and lands riders on a zone
 * change.
 */
export class MountState {
  phase: MountPhase = "walking";
  /** The broom's speed tier (`mounted.tier`; 0 when walking). */
  tier = 0;
  private pendingSince = 0;

  /** Whether the server has us on a broom (also while a landing is asked). */
  get mounted(): boolean {
    return this.phase === "flying" || this.phase === "landing";
  }

  /** A request is waiting for its answer (the player holds still meanwhile). */
  get pending(): boolean {
    return this.phase === "mounting" || this.phase === "landing";
  }

  /**
   * B was pressed at `nowMs`: the request to send (`true` mount, `false`
   * land), or null while one is already on its way.
   */
  toggle(nowMs: number): boolean | null {
    this.expire(nowMs);
    if (this.pending) return null;
    this.pendingSince = nowMs;
    if (this.phase === "walking") {
      this.phase = "mounting";
      return true;
    }
    this.phase = "landing";
    return false;
  }

  /** A request with no answer for MOUNT_PENDING_MS goes back to where it was. */
  expire(nowMs: number): void {
    if (!this.pending || nowMs - this.pendingSince < MOUNT_PENDING_MS) return;
    this.phase = this.phase === "mounting" ? "walking" : "flying";
  }

  /** `ServerMsg::Mounted`. */
  onMounted(on: boolean, tier: number, reason: MountRefused | null): MountOutcome {
    const was = this.mounted;
    this.phase = on ? "flying" : "walking";
    this.tier = on ? Math.max(1, tier) : 0;
    if (reason) return { kind: "refused", text: mountRefusedText(reason, on) };
    if (on && !was) return { kind: "lift" };
    if (!on && was) return { kind: "land" };
    return { kind: "none" };
  }

  /** A new connection or a zone the server has not mounted us in: on foot. */
  clear(): void {
    this.phase = "walking";
    this.tier = 0;
  }
}

/** Words for a refused mount or landing (`mounted.reason`). `on`: still mounted after the refusal. */
export function mountRefusedText(reason: MountRefused, on: boolean): string {
  switch (reason) {
    case "no_skill":
      return "You need your first Flying lesson before you can ride a broom. Madam Swift teaches it at the Flying Pitch.";
    case "no_airspace":
      return "Brooms only fly out on the grounds, under the open sky.";
    case "no_fly":
      return "No flying here: move away from the castle and the buildings to take off.";
    case "busy":
      return "You can't take out a broom right now. Finish what you are doing first.";
    case "not_landed":
      return "Fly lower to land: come within half a metre of the ground (hold Shift to dive). You can't land on the water.";
    case "rate_limited":
      return on ? "One moment: your broom is still settling." : "One moment: your broom is not ready yet.";
  }
}

// ------------------------------------------------------------------ the HUD

/** What the flight chip shows. */
export interface FlightChipView {
  /** Height over the floor under the broom (m; the ground, or the lake's floor), null when unknown. */
  height: number | null;
  hovering: boolean;
  /** Low enough to land (within the dismount height over walkable ground). */
  canLand: boolean;
  /** A landing was asked and the answer is awaited. */
  landing: boolean;
}

/** "Flying, 34 m up, land to dismount". */
export function flightChipText(v: FlightChipView): string {
  const what = v.hovering ? "Hovering" : "Flying";
  if (v.landing) return `${what}: landing…`;
  if (v.canLand) return `${what}, just above the ground: press B to land`;
  const up = v.height === null ? "" : `, ${Math.max(0, Math.round(v.height))} m up`;
  return `${what}${up}, land to dismount`;
}
