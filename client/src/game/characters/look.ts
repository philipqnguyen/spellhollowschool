// Characters look at their target (docs/architecture.md "Anims and emotes"):
// a procedural head and neck turn, with a little of the
// upper chest, toward a point (another character's head), layered over
// whatever clip plays (idle, walk, run, the seated and floor sits, emotes,
// the wand gesture).
//
// - The turn is a yaw about the body's up axis, then a pitch about the
//   side axis as yawed (so no roll: the ear never drops toward the
//   shoulder), in the root's frame (+z ahead, +y up, as
//   characters/seated.ts), shared out along the spine: the head and the
//   two neck bones most, the chest a little (`LOOK_SHARES`). Spread over
//   the neck rather than piled on the head, so no one joint twists far
//   (the neck does not thin under the jaw). Each bone turns about its
//   own head by its share, after the clip is sampled, so the clip's own
//   head motion (the UAL retarget's level head and its kept bob,
//   charlib/ual.py `HEAD_BOB`, `NECK_SHARE`) stays under it and the
//   seat's knee lift is untouched.
// - Clamped: yaw at most `LOOK_YAW_MAX` either way, pitch `LOOK_PITCH_UP`
//   up and `LOOK_PITCH_DOWN` down. A target behind, past the yaw limit,
//   is held at the limit on the side the head was already turned for as
//   long as it is the target (the head only goes back to neutral when the
//   target goes): the head never swings round through the back (no
//   twist), and it never snaps.
// - Eased with a critically damped spring (`LOOK_SMOOTH_S`), so a new
//   target, a lost one or a target that moves is followed smoothly.
//
// Cheap: a handful of 4x4 products per looking character per pose (the
// chain from the root to the chest once, then four bones), nothing for a
// character at rest with no target, nothing allocated. Pure, unit tested
// (look.test.ts).

import type { Rig } from "./clips";
import { mul4 } from "./math";

const DEG = Math.PI / 180;
/** Yaw limit either way (radians), head, neck and chest together. */
export const LOOK_YAW_MAX = 85 * DEG;
/** Pitch limits (radians): up and down. */
export const LOOK_PITCH_UP = 25 * DEG;
export const LOOK_PITCH_DOWN = 30 * DEG;
/** The spring's smoothing time (s): about where a new target is reached. */
export const LOOK_SMOOTH_S = 0.25;
/** Below these (radians, radians a second) a target-less look has settled at neutral. */
const REST_ANGLE = 1e-3;
const REST_SPEED = 1e-2;
/**
 * The eyes above the head bone's head (the top of the neck), m at scale 1:
 * only for a rig without eye bones (the greybox stand-in). The built bodies
 * export `EYE_BONES`, and the eyes are measured from them.
 */
export const EYE_ABOVE_HEAD_BONE = 0.09;
/** The eye bones (children of the head bone, their heads at the eye centres) the look is aimed from. */
export const EYE_BONES = ["DEF-eye.L", "DEF-eye.R"] as const;

/** The bones the turn is shared over, chest to head, and each one's share (they sum to 1). */
export const LOOK_BONES = ["DEF-spine.003", "DEF-spine.004", "DEF-spine.005", "DEF-spine.006"] as const;
export const LOOK_SHARES: readonly number[] = [0.1, 0.25, 0.25, 0.4];

/** One character's look: the eased angles and their speeds, and the hold behind. */
export interface LookState {
  /** Radians: + turns the face toward the body's +x (its left as it faces +z), + pitch looks up. */
  yaw: number;
  pitch: number;
  yawSpeed: number;
  pitchSpeed: number;
  /** Whether the target is past the yaw limit (held at it on `side`). */
  out: boolean;
  /** The side (+1, -1) held while it is. */
  side: number;
  /** False once a target-less look has settled at neutral (nothing to apply). */
  active: boolean;
}

export function lookState(): LookState {
  return { yaw: 0, pitch: 0, yawSpeed: 0, pitchSpeed: 0, out: false, side: 1, active: false };
}

/** Sets `s` back to neutral at once (a character drawn again after a while, a teleport). */
export function resetLook(s: LookState): void {
  s.yaw = 0;
  s.pitch = 0;
  s.yawSpeed = 0;
  s.pitchSpeed = 0;
  s.out = false;
  s.active = false;
}

/**
 * A critically damped spring from `x` toward `target` over `dt` with
 * smoothing time `smooth` (the classic SmoothDamp step: stable for any
 * `dt`, no overshoot). Returns the new value; writes the speed to `v[0]`.
 */
export function smoothDamp(x: number, target: number, speed: number, smooth: number, dt: number, v: [number]): number {
  const omega = 2 / smooth;
  const k = omega * dt;
  const e = 1 / (1 + k + 0.48 * k * k + 0.235 * k * k * k);
  const change = x - target;
  const temp = (speed + omega * change) * dt;
  v[0] = (speed - omega * temp) * e;
  return target + (change + temp) * e;
}

const SPEED: [number] = [0];

/**
 * Advances `s` by `dt` toward the target at (`yaw`, `pitch`) radians from
 * straight ahead (the body's frame), or toward neutral when `yaw` is NaN
 * (no target). Applies the limits and the hold behind (for as long as the target is there).
 */
export function stepLook(s: LookState, yaw: number, pitch: number, dt: number): void {
  let wantYaw = 0;
  let wantPitch = 0;
  if (Number.isNaN(yaw)) s.out = false;
  else if (Math.abs(yaw) <= LOOK_YAW_MAX) {
    s.out = false;
    wantYaw = yaw;
    wantPitch = Math.max(-LOOK_PITCH_DOWN, Math.min(LOOK_PITCH_UP, pitch));
  } else {
    if (!s.out) {
      // Hold on the side the head is already turned (a target passing behind does not drag it across).
      s.out = true;
      s.side = Math.abs(s.yaw) > 0.05 ? Math.sign(s.yaw) : Math.sign(yaw) || 1;
    }
    wantYaw = s.side * LOOK_YAW_MAX;
    wantPitch = Math.max(-LOOK_PITCH_DOWN, Math.min(LOOK_PITCH_UP, pitch));
  }
  if (dt > 0) {
    s.yaw = smoothDamp(s.yaw, wantYaw, s.yawSpeed, LOOK_SMOOTH_S, dt, SPEED);
    s.yawSpeed = SPEED[0];
    s.pitch = smoothDamp(s.pitch, wantPitch, s.pitchSpeed, LOOK_SMOOTH_S, dt, SPEED);
    s.pitchSpeed = SPEED[0];
  }
  const settled = Number.isNaN(yaw) && Math.abs(s.yaw) < REST_ANGLE && Math.abs(s.pitch) < REST_ANGLE && Math.abs(s.yawSpeed) < REST_SPEED && Math.abs(s.pitchSpeed) < REST_SPEED;
  if (settled) resetLook(s);
  else s.active = true;
}

/**
 * The yaw and pitch (radians, into `out`) from an eye at `eye` to `target`,
 * both in the body's frame (+z ahead, +y up). Yaw in (-π, π].
 */
export function anglesTo(eye: ArrayLike<number>, target: ArrayLike<number>, out: [number, number]): void {
  const dx = target[0]! - eye[0]!;
  const dy = target[1]! - eye[1]!;
  const dz = target[2]! - eye[2]!;
  out[0] = Math.atan2(dx, dz);
  out[1] = Math.atan2(dy, Math.sqrt(dx * dx + dz * dz));
}

/**
 * World point (wx, wy, wz) in the frame of a character standing at
 * (rx, ry, rz) facing `yaw`, drawn at `scale` (the renderer's root:
 * `rotation.y = yaw`, uniform scaling), into `out`.
 */
export function worldToBody(wx: number, wy: number, wz: number, rx: number, ry: number, rz: number, yaw: number, scale: number, out: Float64Array | number[]): void {
  const dx = wx - rx;
  const dz = wz - rz;
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  const k = scale > 0 ? 1 / scale : 1;
  out[0] = (dx * c - dz * s) * k;
  out[1] = (wy - ry) * k;
  out[2] = (dx * s + dz * c) * k;
}

/** The inverse: a point in the body's frame to the world. */
export function bodyToWorld(bx: number, by: number, bz: number, rx: number, ry: number, rz: number, yaw: number, scale: number, out: Float64Array | number[]): void {
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  out[0] = rx + (bx * c + bz * s) * scale;
  out[1] = ry + by * scale;
  out[2] = rz + (-bx * s + bz * c) * scale;
}

/** `LOOK_BONES` indices in `rig` (-1 where it lacks one), or null without a head bone. */
export function lookBones(rig: Rig): Int16Array | null {
  const out = new Int16Array(LOOK_BONES.length);
  for (let i = 0; i < LOOK_BONES.length; i++) out[i] = rig.boneIndex(LOOK_BONES[i]!);
  return out[LOOK_BONES.length - 1]! < 0 ? null : out;
}

const CHAIN = new Int16Array(64);
const M = new Float32Array(16);
const T = new Float32Array(16);

/** The model matrix of bone `b` (or the identity for -1) from local matrices `pose`, into `out`. */
export function modelMatrix(rig: Rig, pose: Float32Array, b: number, out: Float32Array): void {
  let depth = 0;
  for (let p = b; p >= 0 && depth < CHAIN.length; p = rig.parents[p]!) CHAIN[depth++] = p;
  out.fill(0);
  out[0] = out[5] = out[10] = out[15] = 1;
  for (let k = depth - 1; k >= 0; k--) {
    mul4(out, pose, T, 0, CHAIN[k]! * 16, 0);
    out.set(T);
  }
}

/** `EYE_BONES` indices per rig (-1 where it lacks one), looked up once. */
const EYES_OF = new WeakMap<Rig, Int16Array>();

function eyeBones(rig: Rig): Int16Array {
  let e = EYES_OF.get(rig);
  if (!e) {
    e = new Int16Array(EYE_BONES.length);
    for (let i = 0; i < EYE_BONES.length; i++) e[i] = rig.boneIndex(EYE_BONES[i]!);
    EYES_OF.set(rig, e);
  }
  return e;
}

/**
 * Where the eyes are in the body's frame (m, at scale 1) in sampled pose
 * `pose`: midway between the eye bones' heads, which each body's build puts
 * at its own eye centres (the wrapped female's head bone sits 9 cm below and
 * 11 cm behind them, the male's 9 cm below and 13 cm behind). A rig without
 * eye bones takes `EYE_ABOVE_HEAD_BONE` above the head bone's head.
 */
export function eyeInBody(rig: Rig, pose: Float32Array, head: number, out: Float64Array | number[]): void {
  modelMatrix(rig, pose, head, M);
  const eyes = eyeBones(rig);
  let n = 0;
  out[0] = out[1] = out[2] = 0;
  for (let i = 0; i < eyes.length; i++) {
    const e = eyes[i]!;
    if (e < 0) continue;
    // An eye bone is a child of the head: one step from the head's model matrix.
    if (rig.parents[e] === head) mul4(M, pose, T, 0, e * 16, 0);
    else modelMatrix(rig, pose, e, T);
    out[0] += T[12]!;
    out[1] += T[13]!;
    out[2] += T[14]!;
    n++;
  }
  if (n) {
    out[0] /= n;
    out[1] /= n;
    out[2] /= n;
    return;
  }
  out[0] = M[12]!;
  out[1] = M[13]! + EYE_ABOVE_HEAD_BONE;
  out[2] = M[14]!;
}

const PARENT = new Float32Array(16);

/**
 * Turns the look bones (`bones`, from `lookBones`) of sampled pose `pose`
 * (local matrices, 16 floats a bone, as `samplePose` writes) by `yaw` and
 * `pitch` radians in the body's frame, each bone by its share. The turn
 * is built up the chain so that every bone, the head last, ends up turned
 * by exactly C = Ry(yaw so far) · Rx(-pitch so far) in the model frame: a
 * yaw about the body's up, then a pitch about the side axis as yawed, and
 * so no roll (the ear never drops toward the shoulder). Bone i's own turn
 * is the step C_i · C_(i-1)⁻¹ = Ry(Y_i) · Rx(-(X_i - X_(i-1))) · Ry(-Y_(i-1)),
 * applied about its head: its local rotation becomes P⁻¹ · step · P ·
 * rotation, P its parent's model rotation (already carrying C_(i-1)).
 * Turning each bone by Ry(share·yaw) · Rx(-share·pitch) instead pitches
 * the head about the body's un-yawed side axis once the chest and neck
 * have turned, which rolls it (10 degrees at the yaw limit looking up).
 * The translations (the heads) stay. Allocation-free.
 */
export function applyLook(rig: Rig, pose: Float32Array, bones: Int16Array, yaw: number, pitch: number): void {
  let parentOf = -2;
  let yawSoFar = 0;
  for (let i = 0; i < bones.length; i++) {
    const b = bones[i]!;
    const share = LOOK_SHARES[i]!;
    if (b < 0 || share <= 0) continue;
    const p = rig.parents[b]!;
    // The parent's model matrix: carried along the chain, recomputed if the chain skips.
    if (p !== parentOf) modelMatrix(rig, pose, p, PARENT);
    const yawTo = yawSoFar + share * yaw;
    turnBone(pose, b, PARENT, yawTo, -share * pitch, yawSoFar);
    yawSoFar = yawTo;
    // This bone, turned, is the next one's parent.
    mul4(PARENT, pose, T, 0, b * 16, 0);
    PARENT.set(T);
    parentOf = b;
  }
}

/**
 * Rotates bone `b`'s local 3x3 by Ry(ay) · Rx(ax) · Ry(-ay0) given in the
 * model frame (yaw back by what the chain below already turned, pitch, yaw
 * on to the new total), its parent's model matrix `parent`.
 */
function turnBone(pose: Float32Array, b: number, parent: Float32Array, ay: number, ax: number, ay0: number): void {
  const cy = Math.cos(ay);
  const sy = Math.sin(ay);
  const cx = Math.cos(ax);
  const sx = Math.sin(ax);
  const c0 = Math.cos(ay0);
  const s0 = Math.sin(ay0);
  // Rx: [1 0 0; 0 cx -sx; 0 sx cx]; Ry(t): [ct 0 st; 0 1 0; -st 0 ct].
  // E = Rx(ax) · Ry(-ay0) (rows).
  const e00 = c0;
  const e01 = 0;
  const e02 = -s0;
  const e10 = -sx * s0;
  const e11 = cx;
  const e12 = -sx * c0;
  const e20 = cx * s0;
  const e21 = sx;
  const e22 = cx * c0;
  // R = Ry(ay) · E.
  const r00 = cy * e00 + sy * e20;
  const r01 = cy * e01 + sy * e21;
  const r02 = cy * e02 + sy * e22;
  const r10 = e10;
  const r11 = e11;
  const r12 = e12;
  const r20 = -sy * e00 + cy * e20;
  const r21 = -sy * e01 + cy * e21;
  const r22 = -sy * e02 + cy * e22;
  // The parent's model rotation P (3x3, column-major in `parent`), normalised columns (a uniform scale divides out).
  const p00 = parent[0]!, p10 = parent[1]!, p20 = parent[2]!;
  const p01 = parent[4]!, p11 = parent[5]!, p21 = parent[6]!;
  const p02 = parent[8]!, p12 = parent[9]!, p22 = parent[10]!;
  const s2 = p00 * p00 + p10 * p10 + p20 * p20 || 1;
  // Q = P⁻¹ R P with P⁻¹ = Pᵀ / s² (rotation times a uniform scale).
  // First RP = R · P.
  const a00 = r00 * p00 + r01 * p10 + r02 * p20;
  const a01 = r00 * p01 + r01 * p11 + r02 * p21;
  const a02 = r00 * p02 + r01 * p12 + r02 * p22;
  const a10 = r10 * p00 + r11 * p10 + r12 * p20;
  const a11 = r10 * p01 + r11 * p11 + r12 * p21;
  const a12 = r10 * p02 + r11 * p12 + r12 * p22;
  const a20 = r20 * p00 + r21 * p10 + r22 * p20;
  const a21 = r20 * p01 + r21 * p11 + r22 * p21;
  const a22 = r20 * p02 + r21 * p12 + r22 * p22;
  // Q = Pᵀ · RP / s².
  const k = 1 / s2;
  const q00 = (p00 * a00 + p10 * a10 + p20 * a20) * k;
  const q01 = (p00 * a01 + p10 * a11 + p20 * a21) * k;
  const q02 = (p00 * a02 + p10 * a12 + p20 * a22) * k;
  const q10 = (p01 * a00 + p11 * a10 + p21 * a20) * k;
  const q11 = (p01 * a01 + p11 * a11 + p21 * a21) * k;
  const q12 = (p01 * a02 + p11 * a12 + p21 * a22) * k;
  const q20 = (p02 * a00 + p12 * a10 + p22 * a20) * k;
  const q21 = (p02 * a01 + p12 * a11 + p22 * a21) * k;
  const q22 = (p02 * a02 + p12 * a12 + p22 * a22) * k;
  const o = b * 16;
  for (let col = 0; col < 3; col++) {
    const x = pose[o + col * 4]!;
    const y = pose[o + col * 4 + 1]!;
    const z = pose[o + col * 4 + 2]!;
    pose[o + col * 4] = q00 * x + q01 * y + q02 * z;
    pose[o + col * 4 + 1] = q10 * x + q11 * y + q12 * z;
    pose[o + col * 4 + 2] = q20 * x + q21 * y + q22 * z;
  }
}
