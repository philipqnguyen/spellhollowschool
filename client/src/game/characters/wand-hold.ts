// The hand and arm that hold the wand (wand-state.ts says when), laid over
// whatever clip plays. Pure, unit tested (wand-hold.test.ts).
//
// The pose comes from the authored `wand_light` clip
// (tools/blender/characters/charlib/anim_spells.py): the right arm holding a
// lit wand up like a lantern, the hand closed round the handle as
// charlib/wand_grip.py solves it. Two layers of it, by bone:
//
// - the grip (the right palm, fingers and thumb) whenever the wand is drawn,
//   weighted by the wand's growth, so the hand closes as the wand appears in
//   it and opens as it goes, over any clip (the cast and gesture clips close
//   the hand the same way already; idle, walk and the rest do not);
// - the arm (upper arm, forearm, hand) while the wand is held out without a
//   cast (a lit wand, an aim) over idle, walk and run, so the light is held
//   up in front rather than swinging at the hip.
//
// Each layer replaces the local rotation of its bones (slerped by weight)
// and keeps their translation and scale, so the rest of the body plays its
// clip untouched.

import type { BakedClip, Rig } from "./clips";

/** The arm layer's bones, shoulder to hand (absent ones are skipped). */
export const HOLD_ARM_BONES = ["DEF-upper_arm.R", "DEF-upper_arm.R.001", "DEF-forearm.R", "DEF-forearm.R.001", "DEF-hand.R"];
/** The grip layer's bones: the right palm, fingers and thumb. */
export const HOLD_GRIP_BONES = [
  "DEF-palm.01.R",
  "DEF-palm.02.R",
  "DEF-palm.03.R",
  "DEF-palm.04.R",
  ...["f_index", "f_middle", "f_ring", "f_pinky", "thumb"].flatMap((f) => [1, 2, 3].map((j) => `DEF-${f}.0${j}.R`)),
];
/** The clip the pose is read from. */
export const HOLD_CLIP = "wand_light";

/** One layer: its bones' indices in the rig and their local rotations (x, y, z, w) to blend toward. */
export interface HoldLayer {
  bones: Int16Array;
  rot: Float32Array;
}

export interface WandHold {
  arm: HoldLayer;
  grip: HoldLayer;
}

function layer(rig: Rig, clip: BakedClip, names: readonly string[]): HoldLayer {
  const idx = names.map((n) => rig.boneIndex(n)).filter((i) => i >= 0);
  const bones = Int16Array.from(idx);
  const rot = new Float32Array(bones.length * 4);
  // Frame 0 (the clip is still).
  for (let k = 0; k < bones.length; k++) for (let c = 0; c < 4; c++) rot[k * 4 + c] = clip.rot[bones[k]! * 4 + c]!;
  return { bones, rot };
}

/** The hold pose of `rig` from its baked `wand_light` clip; null without that clip or a right hand. */
export function wandHold(rig: Rig, clip: BakedClip | null | undefined): WandHold | null {
  if (!clip) return null;
  const arm = layer(rig, clip, HOLD_ARM_BONES);
  const grip = layer(rig, clip, HOLD_GRIP_BONES);
  if (!arm.bones.length || !grip.bones.length) return null;
  return { arm, grip };
}

const Q = new Float64Array(4);

/** The rotation of the column-major matrix at `pose[o..o+16)` with its column scales, into Q; returns nothing (scales in S). */
const S = new Float64Array(3);
function readRotation(m: Float32Array, o: number): void {
  const sx = Math.sqrt(m[o]! * m[o]! + m[o + 1]! * m[o + 1]! + m[o + 2]! * m[o + 2]!) || 1;
  const sy = Math.sqrt(m[o + 4]! * m[o + 4]! + m[o + 5]! * m[o + 5]! + m[o + 6]! * m[o + 6]!) || 1;
  const sz = Math.sqrt(m[o + 8]! * m[o + 8]! + m[o + 9]! * m[o + 9]! + m[o + 10]! * m[o + 10]!) || 1;
  S[0] = sx;
  S[1] = sy;
  S[2] = sz;
  const m00 = m[o]! / sx;
  const m10 = m[o + 1]! / sx;
  const m20 = m[o + 2]! / sx;
  const m01 = m[o + 4]! / sy;
  const m11 = m[o + 5]! / sy;
  const m21 = m[o + 6]! / sy;
  const m02 = m[o + 8]! / sz;
  const m12 = m[o + 9]! / sz;
  const m22 = m[o + 10]! / sz;
  const trace = m00 + m11 + m22;
  let x: number, y: number, z: number, w: number;
  if (trace > 0) {
    const s = 0.5 / Math.sqrt(trace + 1);
    x = (m21 - m12) * s;
    y = (m02 - m20) * s;
    z = (m10 - m01) * s;
    w = 0.25 / s;
  } else if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22);
    x = 0.25 * s;
    y = (m01 + m10) / s;
    z = (m02 + m20) / s;
    w = (m21 - m12) / s;
  } else if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22);
    x = (m01 + m10) / s;
    y = 0.25 * s;
    z = (m12 + m21) / s;
    w = (m02 - m20) / s;
  } else {
    const s = 2 * Math.sqrt(1 + m22 - m00 - m11);
    x = (m02 + m20) / s;
    y = (m12 + m21) / s;
    z = 0.25 * s;
    w = (m10 - m01) / s;
  }
  const len = Math.sqrt(x * x + y * y + z * z + w * w) || 1;
  Q[0] = x / len;
  Q[1] = y / len;
  Q[2] = z / len;
  Q[3] = w / len;
}

/**
 * Turns each of `layer`'s bones in `pose` (local matrices, 16 floats per
 * bone) toward the layer's rotation by `weight` (0..1, slerp), keeping each
 * bone's translation and scale. Allocation-free.
 */
export function applyHold(pose: Float32Array, layer: HoldLayer, weight: number): void {
  if (weight <= 0) return;
  const t = Math.min(1, weight);
  const { bones, rot } = layer;
  for (let k = 0; k < bones.length; k++) {
    const o = bones[k]! * 16;
    readRotation(pose, o);
    let bx = rot[k * 4]!;
    let by = rot[k * 4 + 1]!;
    let bz = rot[k * 4 + 2]!;
    let bw = rot[k * 4 + 3]!;
    let cos = Q[0]! * bx + Q[1]! * by + Q[2]! * bz + Q[3]! * bw;
    if (cos < 0) {
      cos = -cos;
      bx = -bx;
      by = -by;
      bz = -bz;
      bw = -bw;
    }
    let k0 = 1 - t;
    let k1 = t;
    if (cos < 0.9995) {
      const a = Math.acos(cos);
      const sin = Math.sin(a);
      k0 = Math.sin((1 - t) * a) / sin;
      k1 = Math.sin(t * a) / sin;
    }
    let x = Q[0]! * k0 + bx * k1;
    let y = Q[1]! * k0 + by * k1;
    let z = Q[2]! * k0 + bz * k1;
    let w = Q[3]! * k0 + bw * k1;
    const len = Math.sqrt(x * x + y * y + z * z + w * w) || 1;
    x /= len;
    y /= len;
    z /= len;
    w /= len;
    // Rewrite the rotation columns (translation, column 3, untouched).
    const x2 = x + x;
    const y2 = y + y;
    const z2 = z + z;
    const xx = x * x2;
    const xy = x * y2;
    const xz = x * z2;
    const yy = y * y2;
    const yz = y * z2;
    const zz = z * z2;
    const wx = w * x2;
    const wy = w * y2;
    const wz = w * z2;
    const sx = S[0]!;
    const sy = S[1]!;
    const sz = S[2]!;
    pose[o] = (1 - (yy + zz)) * sx;
    pose[o + 1] = (xy + wz) * sx;
    pose[o + 2] = (xz - wy) * sx;
    pose[o + 4] = (xy - wz) * sy;
    pose[o + 5] = (1 - (xx + zz)) * sy;
    pose[o + 6] = (yz + wx) * sy;
    pose[o + 8] = (xz + wy) * sz;
    pose[o + 9] = (yz - wx) * sz;
    pose[o + 10] = (1 - (xx + yy)) * sz;
  }
}
