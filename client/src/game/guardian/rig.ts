// The guardian's parts as matrices (spells to level 20, GUARDIAN): one
// merged mesh per animal whose vertices carry their part's index; the
// silver shader moves each vertex by its part's matrix (material.ts), so a
// whole animal is one draw. Pure, no Babylon: matrices are written in
// Babylon's layout (row vectors, v' = v * M, translation in [12..14]), which
// uploads as the column-major `mat4` the shader multiplies `M * v` with.
//
// A part turns about its joint (its pivot, in the animal's frame as the
// client loaded it: Babylon's left-handed axes, facing +z). Every part
// rides on the body: part = T(-p) R T(p) · body.

import type { Pose } from "./follow";

/** Most parts an animal has (the shader's uniform array). */
export const MAX_PARTS = 8;

/** What each part does in a pose. */
export type Role =
  | "body"
  | "head"
  | "leg_fl"
  | "leg_fr"
  | "leg_bl"
  | "leg_br"
  | "tail"
  | "wing_l"
  | "wing_r";

const ROLES: readonly Role[] = [
  "body",
  "head",
  "leg_fl",
  "leg_fr",
  "leg_bl",
  "leg_br",
  "tail",
  "wing_l",
  "wing_r",
];

export function isRole(name: string): name is Role {
  return (ROLES as readonly string[]).includes(name);
}

/** m = identity (16 floats at `o`). */
function identity(m: Float32Array, o: number): void {
  m.fill(0, o, o + 16);
  m[o] = m[o + 5] = m[o + 10] = m[o + 15] = 1;
}

/** Scratch: a rotation built from pitch (x), yaw (y) and roll (z), applied roll, then pitch, then yaw. */
const R = new Float32Array(16);
const T = new Float32Array(16);

/** out (at `o`) = a (at `ao`) * b (at `bo`), 4x4 row-vector matrices. `out` must not alias `a` or `b`. */
export function mul(
  out: Float32Array,
  o: number,
  a: Float32Array,
  ao: number,
  b: Float32Array,
  bo: number,
): void {
  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 4; c++) {
      out[o + r * 4 + c] =
        a[ao + r * 4]! * b[bo + c]! +
        a[ao + r * 4 + 1]! * b[bo + 4 + c]! +
        a[ao + r * 4 + 2]! * b[bo + 8 + c]! +
        a[ao + r * 4 + 3]! * b[bo + 12 + c]!;
    }
  }
}

/** Babylon's RotationZ · RotationX · RotationY (roll, then pitch, then yaw) into R. */
function rotation(pitch: number, yaw: number, roll: number): void {
  const cx = Math.cos(pitch),
    sx = Math.sin(pitch);
  const cy = Math.cos(yaw),
    sy = Math.sin(yaw);
  const cz = Math.cos(roll),
    sz = Math.sin(roll);
  // (Rz · Rx) · Ry, multiplied out.
  identity(R, 0);
  R[0] = cz * cy + sz * sx * sy;
  R[1] = sz * cx;
  R[2] = -cz * sy + sz * sx * cy;
  R[4] = -sz * cy + cz * sx * sy;
  R[5] = cz * cx;
  R[6] = sz * sy + cz * sx * cy;
  R[8] = cx * sy;
  R[9] = -sx;
  R[10] = cx * cy;
}

/** out (at `o`) = T(-p) · R(pitch, yaw, roll) · T(p) · T(lift): a turn about pivot p, then a lift. */
function about(
  out: Float32Array,
  o: number,
  px: number,
  py: number,
  pz: number,
  pitch: number,
  yaw: number,
  roll: number,
  lift = 0,
): void {
  rotation(pitch, yaw, roll);
  // T(-p) · R: R with its translation row set to -p · R3.
  identity(T, 0);
  T[12] = -px;
  T[13] = -py;
  T[14] = -pz;
  mul(out, o, T, 0, R, 0);
  out[o + 12]! += px;
  out[o + 13]! += py + lift;
  out[o + 14]! += pz;
}

/** One animal's rig: the role and pivot of each of its parts, in part-index order. */
export interface Rig {
  roles: Role[];
  /** x, y, z per part. */
  pivots: Float32Array;
}

const LOCAL = new Float32Array(16);
const BODY = new Float32Array(16);

/**
 * The part matrices of `rig` in `pose` into `out` (16 floats per part, in
 * part order). Legs and the tail pitch about x, the head pitches and turns,
 * wings flap about the forward axis (positive lifts the tip, whichever side
 * the wing is on), the body lifts, pitches (positive: nose up) and rolls.
 */
export function partMatrices(
  rig: Rig,
  pose: Pose,
  out: Float32Array,
): Float32Array {
  const n = rig.roles.length;
  const bi = Math.max(0, rig.roles.indexOf("body"));
  const pv = rig.pivots;
  about(
    BODY,
    0,
    pv[bi * 3]!,
    pv[bi * 3 + 1]!,
    pv[bi * 3 + 2]!,
    -pose.bodyPitch,
    0,
    pose.bodyRoll,
    pose.bodyY,
  );
  for (let i = 0; i < n; i++) {
    const px = pv[i * 3]!,
      py = pv[i * 3 + 1]!,
      pz = pv[i * 3 + 2]!;
    const o = i * 16;
    switch (rig.roles[i]) {
      case "body":
        out.set(BODY, o);
        continue;
      case "head":
        about(LOCAL, 0, px, py, pz, -pose.headPitch, pose.headYaw, 0);
        break;
      case "leg_fl":
        about(LOCAL, 0, px, py, pz, -pose.legs[0], 0, 0);
        break;
      case "leg_fr":
        about(LOCAL, 0, px, py, pz, -pose.legs[1], 0, 0);
        break;
      case "leg_bl":
        about(LOCAL, 0, px, py, pz, -pose.legs[2], 0, 0);
        break;
      case "leg_br":
        about(LOCAL, 0, px, py, pz, -pose.legs[3], 0, 0);
        break;
      case "tail":
        about(LOCAL, 0, px, py, pz, pose.tailPitch, pose.tailYaw, 0);
        break;
      case "wing_l":
      case "wing_r": {
        const flap = pose.wings[rig.roles[i] === "wing_l" ? 0 : 1];
        // A tip at +x rises with a positive roll; one at -x with a negative one.
        about(LOCAL, 0, px, py, pz, 0, 0, px >= 0 ? flap : -flap);
        break;
      }
    }
    mul(out, o, LOCAL, 0, BODY, 0);
  }
  return out;
}

/** Applies matrix `m` (at `o`) to point (x, y, z): row vector times matrix. */
export function transformPoint(
  m: Float32Array,
  o: number,
  x: number,
  y: number,
  z: number,
): [number, number, number] {
  return [
    x * m[o]! + y * m[o + 4]! + z * m[o + 8]! + m[o + 12]!,
    x * m[o + 1]! + y * m[o + 5]! + z * m[o + 9]! + m[o + 13]!,
    x * m[o + 2]! + y * m[o + 6]! + z * m[o + 10]! + m[o + 14]!,
  ];
}
