// Custodio's companion: how it moves (spells to level 20;
// docs/architecture.md "Spells and dueling"). Pure, no
// Babylon, unit tested in guardian.test.ts.
//
// - Following: a critically damped spring toward a spot behind and beside
//   its holder, held within KEEP_MIN..KEEP_MAX of them (radially, so it never
//   lags far behind a runner nor walks into them). It turns toward where it
//   travels. While the holder stands still the spot wanders a small circle
//   round a point behind them, so the animal ambles about. No collision: it
//   may pass through scenery, as a guardian would.
// - Guarding (a duel ward): it circles the holder at chest height, facing
//   out, scaled down so a horse fits round a duelist.
// - Gaits: each animal's parts swing procedurally by its manifest gait (hop,
//   trot, glide, fly, swim, gallop), faster and wider the faster it goes,
//   with a breathing idle.
// - Size: each animal is drawn at about its real size (`PRESENCE`), a long
//   one following a little further off (`followRoom`).
// - Summoning: a newly cast companion bursts from the wand tip and bounds to
//   its place (`summonAt`).

import { forwardOf } from "../motion";

/** The animals (protocol `GuardianAnimal`). */
export type AnimalKey = "hare" | "fox" | "cat" | "owl" | "otter" | "horse";
export type Gait = "hop" | "trot" | "glide" | "fly" | "swim" | "gallop";

/** The distance band (m) a following companion keeps from its holder (horizontal). */
export const KEEP_MIN = 1.0;
export const KEEP_MAX = 1.6;
/** Where it heads while its holder moves: behind and to the left (m, in the holder's frame). */
export const FOLLOW_BACK = 1.15;
export const FOLLOW_SIDE = 0.6;
/** Holder speeds (m/s) below this count as standing still (the companion wanders). */
export const STILL_SPEED = 0.25;
/** The idle wander: a circle this wide (m) round the follow spot, once every WANDER_PERIOD s. */
export const WANDER_RADIUS = 0.22;
export const WANDER_PERIOD = 7;
/** The spring's smoothing time (s): about how long it takes to settle on its spot. */
export const FOLLOW_SMOOTH = 0.35;
/** A holder this far away (a teleport, a zone's spawn) brings the companion with them (m). */
export const SNAP_DISTANCE = 8;
/** Guarding: the orbit's radius (m), height above the holder's feet (chest), and speed (rad/s). */
export const GUARD_RADIUS = 1.1;
export const GUARD_HEIGHT = 1.15;
export const GUARD_SPEED = 1.7;
/** Guarding: the animal is scaled so its longest side is at most this (m). */
export const GUARD_SIZE = 1.1;
/** How fast it turns toward its travel (1/s, exponential). */
export const TURN_RATE = 7;

/**
 * How much larger than its model each animal is drawn: about the real
 * animal's size, a little more for presence (a hare about 0.7 m long, a
 * fox 1.5 m nose to tail, an owl 1.2 m across the wings), the horse kept
 * pony-sized (1.8 m long, 1.65 m tall) so it still fits indoors.
 */
export const PRESENCE: Readonly<Record<AnimalKey, number>> = {
  hare: 1.4,
  fox: 1.2,
  cat: 1.35,
  owl: 1.3,
  otter: 1.25,
  horse: 1.05,
};

/**
 * The extra distance (m) a following animal `length` metres long (as drawn)
 * keeps from its holder, so a long one (the horse, the fox) never stands
 * in them: none up to 0.9 m long.
 */
export function followRoom(length: number): number {
  return Math.max(0, length / 2 - 0.45);
}

/** Where the holder is this frame. */
export interface Holder {
  x: number;
  y: number;
  z: number;
  yaw: number;
}

/** One companion's motion state (mutated in place: no allocation per frame). */
export interface FollowState {
  x: number;
  y: number;
  z: number;
  vx: number;
  vz: number;
  yaw: number;
  /** Its horizontal speed (m/s), for the gait. */
  speed: number;
  /** The holder's last position and speed estimate. */
  hx: number;
  hz: number;
  holderSpeed: number;
  /** The wander's and the guard orbit's angle (radians). */
  wander: number;
  orbit: number;
  started: boolean;
}

export function newFollow(): FollowState {
  return {
    x: 0,
    y: 0,
    z: 0,
    vx: 0,
    vz: 0,
    yaw: 0,
    speed: 0,
    hx: 0,
    hz: 0,
    holderSpeed: 0,
    wander: 0,
    orbit: 0,
    started: false,
  };
}

/**
 * A critically damped spring step of one coordinate toward `target`
 * (Game Programming Gems 4, "Critically damped ease-in/out smoothing"):
 * returns the new position and writes the new velocity to `out[0]`.
 */
export function smoothDamp(
  x: number,
  v: number,
  target: number,
  smooth: number,
  dt: number,
  out: [number],
): number {
  const omega = 2 / Math.max(1e-4, smooth);
  const k = omega * dt;
  const e = 1 / (1 + k + 0.48 * k * k + 0.235 * k * k * k);
  const change = x - target;
  const temp = (v + omega * change) * dt;
  out[0] = (v - omega * temp) * e;
  return target + (change + temp) * e;
}

/** The shortest signed turn from `from` to `to` (radians, in [-π, π)). */
export function angleDelta(from: number, to: number): number {
  let d = (to - from) % (2 * Math.PI);
  if (d < -Math.PI) d += 2 * Math.PI;
  if (d >= Math.PI) d -= 2 * Math.PI;
  return d;
}

const vel: [number] = [0];

/**
 * Moves a following companion one frame. `height` is its follow height
 * above the holder's feet (the owl at the shoulder, the otter at the knee,
 * ground animals 0); `room` (m, `followRoom`) widens its distance band and
 * follow spot for a long animal.
 */
export function stepFollow(
  s: FollowState,
  h: Holder,
  height: number,
  dt: number,
  room = 0,
): void {
  const back = FOLLOW_BACK + room;
  const side = FOLLOW_SIDE + room * 0.5;
  const keepMin = KEEP_MIN + room;
  const keepMax = KEEP_MAX + room;
  if (dt <= 0) return;
  const [fx, fz] = forwardOf(h.yaw);
  // Left of the holder (the motion.ts convention: yaw 0 faces +z, right is (cos, -sin)).
  const lx = -fz;
  const lz = fx;
  if (!s.started || Math.hypot(s.x - h.x, s.z - h.z) > SNAP_DISTANCE) {
    s.x = h.x - fx * back + lx * side;
    s.z = h.z - fz * back + lz * side;
    s.y = h.y + height;
    s.vx = s.vz = 0;
    s.yaw = h.yaw;
    s.hx = h.x;
    s.hz = h.z;
    s.holderSpeed = 0;
    s.speed = 0;
    s.started = true;
    return;
  }
  // The holder's speed, smoothed (positions arrive interpolated, a little uneven).
  const hs = Math.hypot(h.x - s.hx, h.z - s.hz) / dt;
  s.holderSpeed += (hs - s.holderSpeed) * Math.min(1, dt * 6);
  s.hx = h.x;
  s.hz = h.z;
  let tx = h.x - fx * back + lx * side;
  let tz = h.z - fz * back + lz * side;
  // Standing still: amble round the spot.
  const still = Math.max(0, 1 - s.holderSpeed / STILL_SPEED);
  s.wander += (dt * 2 * Math.PI) / WANDER_PERIOD;
  if (s.wander > 2 * Math.PI) s.wander -= 2 * Math.PI;
  tx += Math.cos(s.wander) * WANDER_RADIUS * still;
  tz += Math.sin(s.wander) * WANDER_RADIUS * still;
  const ox = s.x;
  const oz = s.z;
  vel[0] = s.vx;
  s.x = smoothDamp(s.x, s.vx, tx, FOLLOW_SMOOTH, dt, vel);
  s.vx = vel[0];
  vel[0] = s.vz;
  s.z = smoothDamp(s.z, s.vz, tz, FOLLOW_SMOOTH, dt, vel);
  s.vz = vel[0];
  // Keep the band (radially): never into the holder, never far behind.
  const dx = s.x - h.x;
  const dz = s.z - h.z;
  const d = Math.hypot(dx, dz);
  if (d < keepMin || d > keepMax) {
    const k = (d < keepMin ? keepMin : keepMax) / Math.max(d, 1e-4);
    s.x = h.x + (d < 1e-4 ? -fx * keepMin : dx * k);
    s.z = h.z + (d < 1e-4 ? -fz * keepMin : dz * k);
  }
  // Height: eased (stairs, a jump).
  const ty = h.y + height;
  s.y += (ty - s.y) * Math.min(1, dt * 5);
  // Facing: toward its travel.
  const mx = (s.x - ox) / dt;
  const mz = (s.z - oz) / dt;
  s.speed = Math.hypot(mx, mz);
  if (s.speed > 0.08) {
    const want = Math.atan2(mx, mz);
    s.yaw += angleDelta(s.yaw, want) * Math.min(1, dt * TURN_RATE);
  }
}

/** The scale a guarding animal of `size` (x, y, z metres) is drawn at. */
export function guardScale(size: readonly [number, number, number]): number {
  return Math.min(1, GUARD_SIZE / Math.max(size[0], size[2], 1e-3));
}

/**
 * Moves a guarding companion one frame: it circles the holder at
 * GUARD_RADIUS, its middle (`midY`, metres above its own origin, already
 * scaled) at chest height, facing out.
 */
export function stepGuard(
  s: FollowState,
  h: Holder,
  midY: number,
  dt: number,
): void {
  s.orbit = (s.orbit + GUARD_SPEED * Math.max(0, dt)) % (2 * Math.PI);
  const ox = s.x;
  const oz = s.z;
  s.x = h.x + Math.sin(s.orbit) * GUARD_RADIUS;
  s.z = h.z + Math.cos(s.orbit) * GUARD_RADIUS;
  s.y = h.y + GUARD_HEIGHT - midY;
  // Facing out, along the radius.
  s.yaw = s.orbit;
  s.vx = s.vz = 0;
  s.speed = s.started && dt > 0 ? Math.hypot(s.x - ox, s.z - oz) / dt : 0;
  s.hx = h.x;
  s.hz = h.z;
  s.started = true;
}

// ---------------------------------------------------------------- gaits

/**
 * A pose: the body's lift and pitch, and each part's swing (radians). Legs
 * and the tail swing about the animal's x axis (pitch: positive swings the
 * lower end forward); the head pitches and turns; wings flap about the
 * forward axis (positive lifts the tip).
 */
export interface Pose {
  bodyY: number;
  bodyPitch: number;
  bodyRoll: number;
  headPitch: number;
  headYaw: number;
  /** leg_fl, leg_fr, leg_bl, leg_br. */
  legs: [number, number, number, number];
  tailPitch: number;
  tailYaw: number;
  /** wing_l, wing_r (the owl). */
  wings: [number, number];
}

export function newPose(): Pose {
  return {
    bodyY: 0,
    bodyPitch: 0,
    bodyRoll: 0,
    headPitch: 0,
    headYaw: 0,
    legs: [0, 0, 0, 0],
    tailPitch: 0,
    tailYaw: 0,
    wings: [0, 0],
  };
}

/** Per gait: metres covered per cycle, the most cycles a second, and the speed (m/s) of a full stride. */
export const GAITS: Readonly<
  Record<Gait, { stride: number; maxHz: number; full: number }>
> = {
  hop: { stride: 0.75, maxHz: 3.2, full: 3 },
  trot: { stride: 0.8, maxHz: 3.4, full: 3 },
  glide: { stride: 0.55, maxHz: 3.2, full: 2.5 },
  fly: { stride: 1.2, maxHz: 3, full: 3 },
  swim: { stride: 0.9, maxHz: 2.4, full: 3 },
  gallop: { stride: 1.8, maxHz: 2.6, full: 4 },
};

/** The cycle rate (Hz) of `gait` at `speed` m/s (a slow idle rate for fly and swim, which never stop). */
export function cadence(gait: Gait, speed: number): number {
  const g = GAITS[gait];
  const idle = gait === "fly" ? 1.1 : gait === "swim" ? 0.45 : 0;
  return Math.max(idle, Math.min(g.maxHz, speed / g.stride));
}

const TAU = 2 * Math.PI;

/**
 * The pose of `gait` at cycle `phase` (0..1) and stride amount `amount`
 * (0 standing .. 1 full stride), at clock `t` (s, for the idle), into `out`.
 */
export function gaitPose(
  gait: Gait,
  phase: number,
  amount: number,
  t: number,
  out: Pose,
): Pose {
  const a = Math.max(0, Math.min(1, amount));
  const p = phase - Math.floor(phase);
  const s = Math.sin(TAU * p);
  const breathe = Math.sin(t * 2.1) * 0.006 * (1 - a);
  out.bodyY = breathe;
  out.bodyPitch = 0;
  out.bodyRoll = 0;
  out.headPitch = Math.sin(t * 0.7) * 0.05 * (1 - a);
  out.headYaw = Math.sin(t * 0.43) * 0.35 * (1 - a);
  out.tailPitch = 0;
  out.tailYaw = Math.sin(t * 1.3) * 0.15;
  out.wings[0] = out.wings[1] = 0;
  const L = out.legs;
  switch (gait) {
    case "trot": {
      // Diagonal pairs together.
      const A = 0.55 * a;
      L[0] = A * s;
      L[3] = A * s;
      L[1] = -A * s;
      L[2] = -A * s;
      out.bodyY += 0.018 * a * Math.abs(Math.sin(TAU * p));
      out.headPitch += -0.06 * a * Math.cos(2 * TAU * p);
      out.tailYaw += 0.25 * a * s;
      break;
    }
    case "glide": {
      // A cat's walk: each foot a quarter cycle after the last (FL, BR, FR, BL).
      const A = 0.5 * a;
      L[0] = A * Math.sin(TAU * p);
      L[3] = A * Math.sin(TAU * (p - 0.25));
      L[1] = A * Math.sin(TAU * (p - 0.5));
      L[2] = A * Math.sin(TAU * (p - 0.75));
      out.bodyY += 0.008 * a * Math.sin(2 * TAU * p);
      out.bodyRoll = 0.03 * a * s;
      out.tailYaw += 0.2 * a * Math.sin(TAU * p * 0.5 + 1);
      out.tailPitch = 0.1 * Math.sin(t * 0.9);
      break;
    }
    case "gallop": {
      // Rotary gallop: hind left, hind right, fore left, fore right.
      const A = 0.75 * a;
      L[2] = A * Math.sin(TAU * p);
      L[3] = A * Math.sin(TAU * (p - 0.12));
      L[0] = A * Math.sin(TAU * (p - 0.45));
      L[1] = A * Math.sin(TAU * (p - 0.57));
      out.bodyPitch = 0.12 * a * Math.sin(TAU * (p - 0.2));
      out.bodyY += 0.06 * a * Math.max(0, Math.sin(TAU * (p - 0.3)));
      out.headPitch += 0.15 * a * Math.sin(TAU * (p - 0.1));
      out.tailPitch = -0.3 * a + 0.15 * a * Math.sin(TAU * p);
      break;
    }
    case "hop": {
      // Push off with the hind legs, fly (the first 55 %), land on the fore legs.
      const air = 0.55;
      if (p < air) {
        const u = p / air;
        out.bodyY += 0.13 * a * 4 * u * (1 - u);
        out.bodyPitch = 0.35 * a * (0.5 - u);
        L[2] = L[3] = a * (-0.9 + 1.4 * u);
        L[0] = L[1] = a * (0.7 - 0.3 * u);
      } else {
        const u = (p - air) / (1 - air);
        out.bodyPitch = -0.12 * a * Math.sin(Math.PI * u);
        L[2] = L[3] = a * (0.5 - 1.4 * u);
        L[0] = L[1] = a * (0.4 - 0.6 * u);
      }
      out.tailPitch = 0.2 * a;
      break;
    }
    case "fly": {
      // Wing beats (wider the faster), the body rising on the down stroke.
      const A = 0.35 + 0.5 * a;
      out.wings[0] = out.wings[1] = A * s;
      out.bodyY = -0.035 * A * s + Math.sin(t * 1.7) * 0.03;
      out.bodyPitch = -0.05 * a;
      out.tailPitch = 0.12 * Math.sin(TAU * p + 1);
      out.headPitch = 0.05 * a;
      break;
    }
    case "swim": {
      // An undulating stroke through the air, the paws paddling.
      const A = 0.5 + 0.5 * a;
      out.bodyPitch = 0.12 * A * s;
      out.bodyY = 0.03 * A * Math.sin(TAU * p - 0.6) + Math.sin(t * 1.2) * 0.02;
      out.headPitch = -0.16 * A * s;
      out.tailPitch = 0.45 * A * Math.sin(TAU * p - 1.2);
      L[0] = L[1] = 0.6 * A * Math.sin(TAU * p + 1.2);
      L[2] = L[3] = 0.6 * A * Math.sin(TAU * p + 1.2 + Math.PI);
      break;
    }
  }
  return out;
}

// ---------------------------------------------------------------- summoning

/** The summon (s): the companion bursts from the wand tip and bounds to its place. */
export const SUMMON_S = 1.1;
/** How sharply it leaves the wand and eases into its place (the ease-out power). */
export const SUMMON_EASE = 1.7;
/** How high (m) its bound arcs over the way. */
export const SUMMON_ARC = 0.5;
/** Its size at the wand tip, as a share of its full size. */
export const SUMMON_START_SCALE = 0.3;

/** Where a summoned companion is drawn (written by `summonAt`). */
export interface SummonPose {
  x: number;
  y: number;
  z: number;
  /** Its size as a share of full (SUMMON_START_SCALE at the tip .. 1). */
  grow: number;
}

/**
 * A summoned companion `t` s after it burst from the wand tip (`fx`, `fy`,
 * `fz`) toward its place (`tx`, `ty`, `tz`; its live follow spot): out fast
 * and easing in, arcing up SUMMON_ARC on the way (a bound), growing from
 * SUMMON_START_SCALE to full in the first half. False once the summon is
 * over (it is at its place, full size). Pure.
 */
export function summonAt(
  t: number,
  fx: number,
  fy: number,
  fz: number,
  tx: number,
  ty: number,
  tz: number,
  out: SummonPose,
): boolean {
  const u = Math.max(0, Math.min(1, t / SUMMON_S));
  const e = 1 - Math.pow(1 - u, SUMMON_EASE);
  out.x = fx + (tx - fx) * e;
  out.z = fz + (tz - fz) * e;
  out.y = fy + (ty - fy) * e + SUMMON_ARC * 4 * e * (1 - e);
  const g = Math.min(1, u / 0.5);
  out.grow = SUMMON_START_SCALE + (1 - SUMMON_START_SCALE) * g * g * (3 - 2 * g);
  return t < SUMMON_S;
}
