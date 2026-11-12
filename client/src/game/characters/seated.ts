// Seats fit the sitter's legs (docs/architecture.md):
// where on a seat the seated `sit` clip's root goes, and
// how far its knees rise, for this body at this scale.
//
// The clip is one pose per body, authored at the body's default height and
// drawn scaled with the height slider (and potions): its thighs about
// horizontal, its shins hanging a little back toward the heels. On a seat
// (castle/seats.ts, which writes how far the seat reaches ahead of and
// behind the drawn point and its drop to the floor):
//
// - the hips slide along the seat's depth so the hanging calves and heels
//   (their rearmost point, `calfBack`, times the scale) clear the seat's
//   front edge by `CALF_CLEAR` (the trouser legs and boots round them
//   included): short sitters slide forward on a deep seat, tall ones back;
//   never further back than the body's back (`back` plus `BACK_CLEAR` for
//   the robe) against the backrest or over the back edge, nor so far
//   forward that the sit bones leave the seat (`SIT_HOLD`);
// - when the heels would reach below the floor (the scaled `heelDrop`
//   longer than the seat's drop) both thighs turn up about the hips and the
//   shins turn back by as much, so the shins keep hanging as authored and
//   the feet rest on the floor with the knees risen (`kneeLift`,
//   `liftKnees`); shorter legs dangle.
//
// `measureSeatedLegs` reads the numbers off the body's own skinned mesh in
// the clip (once per rig), so they follow the art. Pure, unit tested
// (seated.test.ts checks the exported bodies against the seats' boxes).

import type { BakedClip, Rig } from "./clips";
import { samplePose } from "./clips";
import { invert4, mul4 } from "./math";
import type { PartData } from "./parts";

/** The seated clip's legs at scale 1 (metres, the root's frame: +z ahead, +y up). */
export interface SeatedLegs {
  /** The rearmost point of the hanging lower legs and feet (below the seat top), ahead of the root. */
  calfBack: number;
  /** How far the hips and lower back reach behind the root (up to `BACK_REACH` above the seat). */
  back: number;
  /** The lowest point of the feet below the root. */
  heelDrop: number;
  /** Hip joint to knee joint: the lever the knees rise on. */
  thigh: number;
}

/** The calves clear the seat's front edge by this much (m): the widest trouser legs and boots hang this far behind the body's calves. */
export const CALF_CLEAR = 0.04;
/** The robe's back stands this far off the body's (m); it may press into a cushion a little more. */
export const BACK_CLEAR = 0.06;
/** The sit bones stay at least this far (m, scaled) behind the seat's front edge. */
export const SIT_HOLD = 0.12;
/** The knees rise at most this much (radians). */
export const MAX_LIFT = 0.6;
/** Body parts below this (m under the root) count as hanging, above as on the seat. */
const HANG_BELOW = 0.02;
/** The back is measured from the seat up to this height (m): what meets a backrest. */
const BACK_REACH = 0.45;
/** Clip times sampled (fractions of the loop): the worst of them counts. */
const SAMPLES = [0, 0.25, 0.5, 0.75];

/** The rigify leg bones the knee lift turns: thigh then shin, left then right. */
export const LEG_BONES = ["DEF-thigh.L", "DEF-shin.L", "DEF-thigh.R", "DEF-shin.R"] as const;

/**
 * The seated legs of `body` (skinned to `rig`) in `clip` (the sitting
 * loop), or null when the body has no lower legs or hips to measure.
 */
export function measureSeatedLegs(rig: Rig, clip: BakedClip, body: PartData): SeatedLegs | null {
  const n = rig.count;
  const local = new Float32Array(n * 16);
  const world = new Float32Array(n * 16);
  const skin = new Float32Array(n * 16);
  const ib = rig.data.inverseBind;
  const joint = body.bones.map((b) => rig.boneIndex(b));
  const thighI = rig.boneIndex(LEG_BONES[0]);
  const shinI = rig.boneIndex(LEG_BONES[1]);
  let calfBack = Infinity;
  let back = -Infinity;
  let heelDrop = -Infinity;
  let thigh = 0;
  for (const f of SAMPLES) {
    samplePose(rig, [{ clip, time: f * clip.duration, weight: 1 }], local);
    worldMatrices(rig, local, world);
    for (let j = 0; j < n; j++) mul4(world, ib, skin, j * 16, j * 16, j * 16);
    if (thighI >= 0 && shinI >= 0) {
      const dx = world[shinI * 16 + 12]! - world[thighI * 16 + 12]!;
      const dy = world[shinI * 16 + 13]! - world[thighI * 16 + 13]!;
      const dz = world[shinI * 16 + 14]! - world[thighI * 16 + 14]!;
      thigh = Math.max(thigh, Math.sqrt(dx * dx + dy * dy + dz * dz));
    }
    for (const prim of body.primitives) {
      if (prim.lod !== 0) continue;
      const legs = prim.region === "calves" || prim.region === "feet";
      const seat = prim.region === "hips" || prim.region === "torso";
      if (!legs && !seat) continue;
      const pos = prim.positions;
      for (let v = 0, count = pos.length / 3; v < count; v++) {
        let y = 0;
        let z = 0;
        const px = pos[v * 3]!;
        const py = pos[v * 3 + 1]!;
        const pz = pos[v * 3 + 2]!;
        for (let k = 0; k < 4; k++) {
          const w = prim.weights[v * 4 + k]!;
          if (w <= 0) continue;
          const j = joint[prim.joints[v * 4 + k]!]!;
          if (j < 0) continue;
          const o = j * 16;
          y += w * (skin[o + 1]! * px + skin[o + 5]! * py + skin[o + 9]! * pz + skin[o + 13]!);
          z += w * (skin[o + 2]! * px + skin[o + 6]! * py + skin[o + 10]! * pz + skin[o + 14]!);
        }
        if (legs) {
          heelDrop = Math.max(heelDrop, -y);
          if (y < -HANG_BELOW) calfBack = Math.min(calfBack, z);
        } else if (y >= 0 && y <= BACK_REACH) back = Math.max(back, -z);
      }
    }
  }
  if (!Number.isFinite(calfBack) || !Number.isFinite(back) || !Number.isFinite(heelDrop) || thigh <= 0) return null;
  return { calfBack, back, heelDrop, thigh };
}

/**
 * How far the knees rise (radians) for a sitter at `scale` on a seat `drop`
 * above the floor: the angle the thighs turn up so the heels, carried up
 * with the knees, rest on the floor. 0 when the feet hang clear.
 */
export function kneeLift(legs: SeatedLegs, scale: number, drop: number): number {
  const over = legs.heelDrop * scale - drop;
  if (!(over > 0) || !(scale > 0)) return 0;
  return Math.min(MAX_LIFT, Math.asin(Math.min(1, over / (legs.thigh * scale))));
}

/**
 * How far (m, along the seat's facing, + = forward) the hips go from the
 * row's centre line, the seat reaching `ahead` of it to its front edge and
 * `behind` it to the backrest or back edge, `drop` above the floor (the
 * rule in the module notes).
 */
export function seatShift(legs: SeatedLegs, scale: number, ahead: number, behind: number, drop: number): number {
  // Risen knees swing the shins back a little: L (1 - cos lift).
  const lift = kneeLift(legs, scale, drop);
  const calf = (legs.calfBack - legs.thigh * (1 - Math.cos(lift))) * scale;
  const knees = ahead + CALF_CLEAR - calf;
  const backMost = -behind + (legs.back + BACK_CLEAR) * scale;
  const frontMost = ahead - SIT_HOLD * scale;
  return Math.max(backMost, Math.min(frontMost, knees));
}

/** Indices of `LEG_BONES` in `rig`, or null when it lacks one. */
export function legBones(rig: Rig): Int16Array | null {
  const out = new Int16Array(LEG_BONES.length);
  for (let i = 0; i < LEG_BONES.length; i++) {
    const b = rig.boneIndex(LEG_BONES[i]!);
    if (b < 0) return null;
    out[i] = b;
  }
  return out;
}

/**
 * Raises the knees of a sampled pose (`pose`: local matrices, 16 floats per
 * bone, as `samplePose` writes) by `angle`: each thigh turns up about its
 * hip, the shin turns back by as much about its knee, both about the body's
 * side axis (model +x), so the shins hang as before, `thigh * sin(angle)`
 * higher. `bones` from `legBones`. Allocation-free.
 */
export function liftKnees(rig: Rig, pose: Float32Array, bones: Int16Array, angle: number): void {
  if (!(angle > 0)) return;
  for (let i = 0; i < bones.length; i += 2) {
    turnAboutModelX(rig, pose, bones[i]!, -angle);
    turnAboutModelX(rig, pose, bones[i + 1]!, angle);
  }
}

const CHAIN = new Int16Array(64);
const PARENT = new Float32Array(16);
const TMP = new Float32Array(16);
const INV = new Float32Array(16);

/** Turns bone `b` of `pose` by `angle` about the model's x axis through its head (a rotation in its parent's frame). */
function turnAboutModelX(rig: Rig, pose: Float32Array, b: number, angle: number): void {
  // The parent's model matrix: its chain from the root.
  let depth = 0;
  for (let p = rig.parents[b]!; p >= 0 && depth < CHAIN.length; p = rig.parents[p]!) CHAIN[depth++] = p;
  PARENT.set(IDENTITY16);
  for (let k = depth - 1; k >= 0; k--) {
    mul4(PARENT, pose, TMP, 0, CHAIN[k]! * 16, 0);
    PARENT.set(TMP);
  }
  // Model x in the parent's frame.
  invert4(PARENT, INV);
  let ax = INV[0]!;
  let ay = INV[1]!;
  let az = INV[2]!;
  const len = Math.sqrt(ax * ax + ay * ay + az * az) || 1;
  ax /= len;
  ay /= len;
  az /= len;
  // Rodrigues: R = cI + s[a]x + (1 - c) a aT, applied to the local rotation's columns (the translation, the head, stays).
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const t = 1 - c;
  const r00 = c + t * ax * ax;
  const r01 = t * ax * ay - s * az;
  const r02 = t * ax * az + s * ay;
  const r10 = t * ax * ay + s * az;
  const r11 = c + t * ay * ay;
  const r12 = t * ay * az - s * ax;
  const r20 = t * ax * az - s * ay;
  const r21 = t * ay * az + s * ax;
  const r22 = c + t * az * az;
  const o = b * 16;
  for (let col = 0; col < 3; col++) {
    const x = pose[o + col * 4]!;
    const y = pose[o + col * 4 + 1]!;
    const z = pose[o + col * 4 + 2]!;
    pose[o + col * 4] = r00 * x + r01 * y + r02 * z;
    pose[o + col * 4 + 1] = r10 * x + r11 * y + r12 * z;
    pose[o + col * 4 + 2] = r20 * x + r21 * y + r22 * z;
  }
}

const IDENTITY16 = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);

/** Model matrices from local ones (parents come first in a rig). */
export function worldMatrices(rig: Rig, local: Float32Array, out: Float32Array): void {
  for (let i = 0; i < rig.count; i++) {
    const p = rig.parents[i]!;
    if (p < 0) out.set(local.subarray(i * 16, i * 16 + 16), i * 16);
    else mul4(out, local, out, p * 16, i * 16, i * 16);
  }
}
