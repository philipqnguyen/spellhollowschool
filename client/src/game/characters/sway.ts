// Procedural hair sway (docs/art/character-adoption.md section 4.2): a hair file may
// carry up to 12 extra joints in fixed chains, `hair.back.01`-`04`
// (ponytail, long back hair), `hair.side.L.01`-`03`, `hair.side.R.01`-`03`
// (side locks, long front locks) and `hair.front.01`-`02` (long fringe
// pieces), each chain hanging from the head bone (`DEF-spine.006`). The
// client appends them to the body's rig (`extendRig`) and, for characters at
// LOD0 and LOD1, swings each chain on one damped spring of two angles:
//
// - pitch (about the character's left-right axis; positive swings the tip
//   back) and roll (about its forward axis; positive swings the tip to the
//   character's left), about 2.5 Hz with a damping ratio of 0.6;
// - driven by the head's acceleration in the character's frame (the hair
//   lags: speeding up swings it back, a sidestep to the side opposite), the
//   turn rate (thrown outward), and gravity: when the head bows or tilts the
//   chain swings back toward hanging, with a slow idle sine (0.3 Hz, 2°);
// - limited: back chains 25° back, 15° forward (they hang behind the neck)
//   and 15° sideways; side and front chains 12°;
// - bone k of a chain of n turns by 1/n of the chain's angle, so the chain
//   bends evenly and its tip deflects by the whole angle.
//
// Clips are sampled on the body's own rig (`samplePose` writes the first
// `base.count` bones); `applySway` then writes the sway bones' local
// matrices. State is a small Float32Array per character (`swayState`), so
// a frame allocates nothing. About 50 flops per bone plus one walk down the
// rig to the head (8 matrix products). Pure, unit tested.

import { Rig } from "./clips";
import { composeTRS, mul4 } from "./math";
import type { PartData, SkeletonData } from "./parts";

/** Joint names of sway bones start with this. */
export const SWAY_PREFIX = "hair.";
/** The head bone sway chains hang from. */
export const SWAY_HEAD = "DEF-spine.006";
/** Sway bones per style at most (rig buffers are sized for 82 + 12 bones). */
export const MAX_SWAY_BONES = 12;
/** Chains per style at most (back, side.L, side.R, front). */
export const MAX_CHAINS = 4;

export type ChainKind = "back" | "side" | "front";

/** Spring and limits (art direction). Angles in radians. */
export const SWAY = {
  hz: 2.5,
  damping: 0.6,
  /** Angle per m/s² of head acceleration (the tip lags behind). */
  accelGain: 0.022,
  /** Angle per rad/s of turning (thrown outward). */
  turnGain: 0.05,
  /** Share of a head bow or tilt the chain swings back toward hanging. */
  gravity: 0.8,
  idleHz: 0.3,
  idleAngle: (2 * Math.PI) / 180,
  limits: {
    back: { back: (25 * Math.PI) / 180, forward: (15 * Math.PI) / 180, side: (15 * Math.PI) / 180 },
    side: { back: (12 * Math.PI) / 180, forward: (12 * Math.PI) / 180, side: (12 * Math.PI) / 180 },
    front: { back: (12 * Math.PI) / 180, forward: (12 * Math.PI) / 180, side: (12 * Math.PI) / 180 },
  } as Record<ChainKind, { back: number; forward: number; side: number }>,
  /** Longest integration step (s); longer frames take several. */
  maxStep: 1 / 60,
};

export interface SwayChain {
  kind: ChainKind;
  /** Rig indices, root of the chain first. */
  bones: number[];
  /** Per bone: the model's pitch axis (+X) and roll axis (+Z) in the bone's rest frame (6 floats). */
  axes: Float32Array;
}

/** A body rig with one hair style's sway bones appended. */
export interface SwayRig {
  /**
   * `<body key>+<hair file>:<hair joint names>`: views rebuild their skeleton
   * when it changes. The hair file is in it because styles with the same
   * chains (F1, F2) have different joint rest transforms.
   */
  key: string;
  rig: Rig;
  /** The body's rig (clips are sampled on it). */
  base: Rig;
  chains: SwayChain[];
  /** Rig indices from the root down to the head bone. */
  headPath: Int16Array;
  /** Rest up axis (bone Y) of the head in the model frame. */
  headRestUp: Float32Array;
}

const CHAIN = /^hair\.(back|side\.L|side\.R|front)\.(\d\d)$/;

/** The sway joints of a hair part's skeleton, in chain order (back, side.L, side.R, front; .01 first). */
export function swayJoints(part: PartData): string[] {
  const names = part.skeleton?.bones.filter((b) => CHAIN.test(b)) ?? [];
  const order = ["back", "side.L", "side.R", "front"];
  return names
    .map((n) => ({ n, m: CHAIN.exec(n)! }))
    .sort((a, b) => order.indexOf(a.m[1]!) - order.indexOf(b.m[1]!) || Number(a.m[2]) - Number(b.m[2]))
    .slice(0, MAX_SWAY_BONES)
    .map((x) => x.n);
}

/**
 * The body rig `base` with the sway joints of `hair` appended (rest pose and
 * inverse bind matrices from the hair file's skin), or null when the hair
 * has none (short styles) or they do not hang from the body's head.
 * `hairKey` names the hair file (asset and variant), so two styles with the
 * same joint names never share a skeleton.
 */
export function extendRig(base: Rig, baseKey: string, hair: PartData, hairKey = ""): SwayRig | null {
  const skel = hair.skeleton;
  const joints = swayJoints(hair);
  if (!skel || joints.length === 0 || base.boneIndex(SWAY_HEAD) < 0) return null;
  const src = new Map(skel.bones.map((b, i) => [b, i]));
  const n0 = base.count;
  const n = n0 + joints.length;
  const data: SkeletonData = {
    bones: [...base.bones, ...joints],
    parents: new Int16Array(n),
    rest: new Float32Array(n * 10),
    inverseBind: new Float32Array(n * 16),
    rootPre: new Float32Array(n * 16),
  };
  data.parents.set(base.parents);
  data.rest.set(base.data.rest);
  data.inverseBind.set(base.data.inverseBind);
  data.rootPre.set(base.data.rootPre);
  const index = new Map(data.bones.map((b, i) => [b, i]));
  for (let k = 0; k < joints.length; k++) {
    const i = n0 + k;
    const s = src.get(joints[k]!)!;
    const parentName = skel.parents[s]! >= 0 ? skel.bones[skel.parents[s]!]! : SWAY_HEAD;
    const p = index.get(parentName) ?? base.boneIndex(SWAY_HEAD);
    if (p >= i) return null; // a chain must hang from the head or an earlier joint of its chain
    data.parents[i] = p;
    data.rest.set(skel.rest.subarray(s * 10, s * 10 + 10), i * 10);
    data.inverseBind.set(skel.inverseBind.subarray(s * 16, s * 16 + 16), i * 16);
    data.rootPre.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], i * 16);
  }
  const rig = new Rig(data);
  const chains: SwayChain[] = [];
  for (const kind of ["back", "side.L", "side.R", "front"]) {
    const bones = joints.map((j, k) => (j.startsWith(`hair.${kind}.`) ? n0 + k : -1)).filter((i) => i >= 0);
    if (!bones.length) continue;
    const axes = new Float32Array(bones.length * 6);
    bones.forEach((b, k) => {
      const q = rig.restWorldRot.subarray(b * 4, b * 4 + 4);
      rotateByConj(q, 1, 0, 0, axes, k * 6);
      rotateByConj(q, 0, 0, 1, axes, k * 6 + 3);
    });
    chains.push({ kind: kind === "back" ? "back" : kind === "front" ? "front" : "side", bones, axes });
  }
  const path: number[] = [];
  for (let b = base.boneIndex(SWAY_HEAD); b >= 0; b = base.parents[b]!) path.unshift(b);
  const head = base.boneIndex(SWAY_HEAD);
  const hq = rig.restWorldRot.subarray(head * 4, head * 4 + 4);
  const up = new Float32Array(3);
  rotateBy(hq, 0, 1, 0, up, 0);
  return { key: `${baseKey}+${hairKey ? `${hairKey}:` : ""}${joints.join(",")}`, rig, base, chains, headPath: Int16Array.from(path), headRestUp: up };
}

/** `v` rotated by the inverse of unit quaternion `q`, into `out` at `o`. */
function rotateByConj(q: ArrayLike<number>, x: number, y: number, z: number, out: Float32Array, o: number): void {
  rotateQ(-q[0]!, -q[1]!, -q[2]!, q[3]!, x, y, z, out, o);
}

function rotateBy(q: ArrayLike<number>, x: number, y: number, z: number, out: Float32Array, o: number): void {
  rotateQ(q[0]!, q[1]!, q[2]!, q[3]!, x, y, z, out, o);
}

function rotateQ(qx: number, qy: number, qz: number, qw: number, x: number, y: number, z: number, out: Float32Array, o: number): void {
  // v' = v + 2w (q × v) + 2 q × (q × v)
  const tx = 2 * (qy * z - qz * y);
  const ty = 2 * (qz * x - qx * z);
  const tz = 2 * (qx * y - qy * x);
  out[o] = x + qw * tx + (qy * tz - qz * ty);
  out[o + 1] = y + qw * ty + (qz * tx - qx * tz);
  out[o + 2] = z + qw * tz + (qx * ty - qy * tx);
}

/** Floats of a character's sway state: 8 for the head's motion, 4 per chain. */
export const SWAY_STATE_FLOATS = 8 + 4 * MAX_CHAINS;

/** A character's sway state (allocated once per character). */
export function swayState(): Float32Array {
  return new Float32Array(SWAY_STATE_FLOATS);
}

/** Forget the motion history (a teleport, a new look): the next frame starts at rest. */
export function resetSway(state: Float32Array): void {
  state.fill(0);
}

const HEAD = new Float32Array(16);
const TMP = new Float32Array(16);

/** Where the character stands this frame (its root). */
export interface SwayRoot {
  x: number;
  y: number;
  z: number;
  yaw: number;
  scale: number;
}

/**
 * Advances the springs by `dt` and writes the sway bones' local matrices
 * into `pose` (16 floats per bone of `s.rig`, the body's bones already
 * posed). `time` drives the idle sine; `phase` (0..1) keeps a crowd out of
 * step. Allocation-free.
 */
export function applySway(s: SwayRig, pose: Float32Array, state: Float32Array, root: SwayRoot, dt: number, time: number, phase = 0): void {
  // The head's model matrix from the posed locals.
  const path = s.headPath;
  // A copy loop, not `set(subarray())`: a subarray is a new view object per call.
  const h0 = path[0]! * 16;
  for (let i = 0; i < 16; i++) HEAD[i] = pose[h0 + i]!;
  for (let k = 1; k < path.length; k++) {
    mul4(HEAD, pose, TMP, 0, path[k]! * 16, 0);
    HEAD.set(TMP);
  }
  // Head position in the world, and its motion in the character's frame.
  const c = Math.cos(root.yaw);
  const sn = Math.sin(root.yaw);
  const hx = HEAD[12]! * root.scale;
  const hy = HEAD[13]! * root.scale;
  const hz = HEAD[14]! * root.scale;
  const wx = root.x + c * hx + sn * hz;
  const wy = root.y + hy;
  const wz = root.z - sn * hx + c * hz;
  const step = Math.max(1e-4, dt);
  let ax = 0;
  let az = 0;
  let turn = 0;
  if (state[7]! > 0.5) {
    const vx = (wx - state[0]!) / step;
    const vy = (wy - state[1]!) / step;
    const vz = (wz - state[2]!) / step;
    const gx = (vx - state[3]!) / step;
    const gz = (vz - state[5]!) / step;
    // Into the character's frame (+Z forward, +X its left).
    ax = c * gx - sn * gz;
    az = sn * gx + c * gz;
    let dyaw = root.yaw - state[6]!;
    dyaw = Math.atan2(Math.sin(dyaw), Math.cos(dyaw));
    turn = dyaw / step;
    // A teleport or a snap (seated, respawn): no swing from it.
    if (vx * vx + vy * vy + vz * vz > 400 || Math.abs(turn) > 30) {
      ax = 0;
      az = 0;
      turn = 0;
    }
    state[3] = vx;
    state[4] = vy;
    state[5] = vz;
  }
  state[0] = wx;
  state[1] = wy;
  state[2] = wz;
  state[6] = root.yaw;
  state[7] = 1;
  // Head bow (+) and tilt from the rest pose: gravity swings the chain back toward hanging.
  const ux = HEAD[4]!;
  const uy = HEAD[5]!;
  const uz = HEAD[6]!;
  const r = s.headRestUp;
  const bow = Math.atan2(uz, uy) - Math.atan2(r[2]!, r[1]!);
  const tilt = Math.atan2(-ux, uy) - Math.atan2(-r[0]!, r[1]!);
  const idle = SWAY.idleAngle * Math.sin(2 * Math.PI * (SWAY.idleHz * time + phase));
  const w0 = 2 * Math.PI * SWAY.hz;
  const k = w0 * w0;
  const damp = 2 * SWAY.damping * w0;
  const steps = Math.min(4, Math.max(1, Math.ceil(dt / SWAY.maxStep)));
  const h = dt / steps;
  for (let ci = 0; ci < s.chains.length && ci < MAX_CHAINS; ci++) {
    const chain = s.chains[ci]!;
    const lim = SWAY.limits[chain.kind];
    const o = 8 + ci * 4;
    const targetP = SWAY.accelGain * az - SWAY.gravity * bow + idle;
    const targetR = -SWAY.accelGain * ax - SWAY.turnGain * turn - SWAY.gravity * tilt;
    let p = state[o]!;
    let vp = state[o + 1]!;
    let q = state[o + 2]!;
    let vq = state[o + 3]!;
    for (let i = 0; i < steps; i++) {
      vp += (-k * (p - targetP) - damp * vp) * h;
      p += vp * h;
      vq += (-k * (q - targetR) - damp * vq) * h;
      q += vq * h;
    }
    if (p > lim.back) (p = lim.back), (vp = Math.min(0, vp));
    if (p < -lim.forward) (p = -lim.forward), (vp = Math.max(0, vp));
    if (q > lim.side) (q = lim.side), (vq = Math.min(0, vq));
    if (q < -lim.side) (q = -lim.side), (vq = Math.max(0, vq));
    if (!Number.isFinite(p + vp + q + vq)) (p = 0), (vp = 0), (q = 0), (vq = 0);
    state[o] = p;
    state[o + 1] = vp;
    state[o + 2] = q;
    state[o + 3] = vq;
    writeChain(s.rig, chain, state, o, pose);
  }
}

/**
 * Every bone of `chain` at its rest pose turned by 1/n of (pitch, roll), read
 * from `state[o]` and `state[o + 2]` (not passed as numbers: a double crossing
 * a call V8 does not inline is boxed, once per chain per character per frame).
 */
function writeChain(rig: Rig, chain: SwayChain, state: Float32Array, o0: number, pose: Float32Array): void {
  const n = chain.bones.length;
  const rest = rig.data.rest;
  const pitch = state[o0]!;
  const roll = state[o0 + 2]!;
  const hp = (pitch / n) * 0.5;
  const hr = (roll / n) * 0.5;
  const sp = Math.sin(hp);
  const cp = Math.cos(hp);
  const sr = Math.sin(hr);
  const cr = Math.cos(hr);
  for (let k = 0; k < n; k++) {
    const b = chain.bones[k]!;
    const a = chain.axes;
    // qp · qr, each about its axis in the bone's frame.
    const px = a[k * 6]! * sp;
    const py = a[k * 6 + 1]! * sp;
    const pz = a[k * 6 + 2]! * sp;
    const rx = a[k * 6 + 3]! * sr;
    const ry = a[k * 6 + 4]! * sr;
    const rz = a[k * 6 + 5]! * sr;
    const dx = cp * rx + px * cr + (py * rz - pz * ry);
    const dy = cp * ry + py * cr + (pz * rx - px * rz);
    const dz = cp * rz + pz * cr + (px * ry - py * rx);
    const dw = cp * cr - (px * rx + py * ry + pz * rz);
    // rest rotation · delta.
    const o = b * 10;
    const qx = rest[o + 3]!;
    const qy = rest[o + 4]!;
    const qz = rest[o + 5]!;
    const qw = rest[o + 6]!;
    const x = qw * dx + qx * dw + (qy * dz - qz * dy);
    const y = qw * dy + qy * dw + (qz * dx - qx * dz);
    const z = qw * dz + qz * dw + (qx * dy - qy * dx);
    const w = qw * dw - (qx * dx + qy * dy + qz * dz);
    composeTRS(rest[o]!, rest[o + 1]!, rest[o + 2]!, x, y, z, w, rest[o + 7]!, rest[o + 8]!, rest[o + 9]!, pose, b * 16);
  }
}
