// Small vector, quaternion and 4x4 matrix helpers on plain arrays, for the
// character pipeline's hot loops (posing up to a hundred skeletons a frame)
// without allocating Babylon objects.
//
// Matrices are column-major with the translation in elements 12-14: glTF's
// layout, which is also the memory layout of Babylon's `Matrix.m`. The maths
// below is glTF's (column vectors): `world = parentWorld · local`, which is
// the same product Babylon writes as `local.multiply(parentWorld)`.
// Quaternions are [x, y, z, w].

export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number];
export type Mat4 = Float32Array | number[];

export const IDENTITY: readonly number[] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

/** Writes the matrix of translation `t`, rotation `q` and scale `s` into `out` at `o`. */
export function composeTRS(
  tx: number,
  ty: number,
  tz: number,
  qx: number,
  qy: number,
  qz: number,
  qw: number,
  sx: number,
  sy: number,
  sz: number,
  out: Mat4,
  o = 0,
): void {
  const x2 = qx + qx;
  const y2 = qy + qy;
  const z2 = qz + qz;
  const xx = qx * x2;
  const xy = qx * y2;
  const xz = qx * z2;
  const yy = qy * y2;
  const yz = qy * z2;
  const zz = qz * z2;
  const wx = qw * x2;
  const wy = qw * y2;
  const wz = qw * z2;
  out[o] = (1 - (yy + zz)) * sx;
  out[o + 1] = (xy + wz) * sx;
  out[o + 2] = (xz - wy) * sx;
  out[o + 3] = 0;
  out[o + 4] = (xy - wz) * sy;
  out[o + 5] = (1 - (xx + zz)) * sy;
  out[o + 6] = (yz + wx) * sy;
  out[o + 7] = 0;
  out[o + 8] = (xz + wy) * sz;
  out[o + 9] = (yz - wx) * sz;
  out[o + 10] = (1 - (xx + yy)) * sz;
  out[o + 11] = 0;
  out[o + 12] = tx;
  out[o + 13] = ty;
  out[o + 14] = tz;
  out[o + 15] = 1;
}

/** `out = a · b` (column vectors: apply `b` first). `out` may alias neither input. */
export function mul4(a: Mat4, b: Mat4, out: Mat4, ao = 0, bo = 0, oo = 0): void {
  for (let c = 0; c < 4; c++) {
    const b0 = b[bo + c * 4]!;
    const b1 = b[bo + c * 4 + 1]!;
    const b2 = b[bo + c * 4 + 2]!;
    const b3 = b[bo + c * 4 + 3]!;
    for (let r = 0; r < 4; r++) {
      out[oo + c * 4 + r] = a[ao + r]! * b0 + a[ao + 4 + r]! * b1 + a[ao + 8 + r]! * b2 + a[ao + 12 + r]! * b3;
    }
  }
}

/** Inverse of a 4x4 matrix; the identity when it is singular. */
export function invert4(m: Mat4, out: Mat4): void {
  const a00 = m[0]!, a01 = m[1]!, a02 = m[2]!, a03 = m[3]!;
  const a10 = m[4]!, a11 = m[5]!, a12 = m[6]!, a13 = m[7]!;
  const a20 = m[8]!, a21 = m[9]!, a22 = m[10]!, a23 = m[11]!;
  const a30 = m[12]!, a31 = m[13]!, a32 = m[14]!, a33 = m[15]!;
  const b00 = a00 * a11 - a01 * a10;
  const b01 = a00 * a12 - a02 * a10;
  const b02 = a00 * a13 - a03 * a10;
  const b03 = a01 * a12 - a02 * a11;
  const b04 = a01 * a13 - a03 * a11;
  const b05 = a02 * a13 - a03 * a12;
  const b06 = a20 * a31 - a21 * a30;
  const b07 = a20 * a32 - a22 * a30;
  const b08 = a20 * a33 - a23 * a30;
  const b09 = a21 * a32 - a22 * a31;
  const b10 = a21 * a33 - a23 * a31;
  const b11 = a22 * a33 - a23 * a32;
  const det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
  if (!det) {
    for (let i = 0; i < 16; i++) out[i] = IDENTITY[i]!;
    return;
  }
  const d = 1 / det;
  out[0] = (a11 * b11 - a12 * b10 + a13 * b09) * d;
  out[1] = (a02 * b10 - a01 * b11 - a03 * b09) * d;
  out[2] = (a31 * b05 - a32 * b04 + a33 * b03) * d;
  out[3] = (a22 * b04 - a21 * b05 - a23 * b03) * d;
  out[4] = (a12 * b08 - a10 * b11 - a13 * b07) * d;
  out[5] = (a00 * b11 - a02 * b08 + a03 * b07) * d;
  out[6] = (a32 * b02 - a30 * b05 - a33 * b01) * d;
  out[7] = (a20 * b05 - a22 * b02 + a23 * b01) * d;
  out[8] = (a10 * b10 - a11 * b08 + a13 * b06) * d;
  out[9] = (a01 * b08 - a00 * b10 - a03 * b06) * d;
  out[10] = (a30 * b04 - a31 * b02 + a33 * b00) * d;
  out[11] = (a21 * b02 - a20 * b04 - a23 * b00) * d;
  out[12] = (a11 * b07 - a10 * b09 - a12 * b06) * d;
  out[13] = (a00 * b09 - a01 * b07 + a02 * b06) * d;
  out[14] = (a31 * b01 - a30 * b03 - a32 * b00) * d;
  out[15] = (a20 * b03 - a21 * b01 + a22 * b00) * d;
}

/** Transforms point (x, y, z) by `m`. */
export function transformPoint(m: Mat4, x: number, y: number, z: number): Vec3 {
  return [m[0]! * x + m[4]! * y + m[8]! * z + m[12]!, m[1]! * x + m[5]! * y + m[9]! * z + m[13]!, m[2]! * x + m[6]! * y + m[10]! * z + m[14]!];
}

// ------------------------------------------------------------------ quaternions

export function quatMul(a: readonly number[], b: readonly number[]): Quat {
  const [ax, ay, az, aw] = a as Quat;
  const [bx, by, bz, bw] = b as Quat;
  return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz];
}

export function quatConj(q: readonly number[]): Quat {
  return [-q[0]!, -q[1]!, -q[2]!, q[3]!];
}

export function quatAxisAngle(ax: number, ay: number, az: number, angle: number): Quat {
  const len = Math.hypot(ax, ay, az) || 1;
  const s = Math.sin(angle / 2) / len;
  return [ax * s, ay * s, az * s, Math.cos(angle / 2)];
}

export function quatNormalize(q: Quat): Quat {
  const len = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / len, q[1] / len, q[2] / len, q[3] / len];
}

/** Rotation part of a column-major matrix with positive scale, as a quaternion. */
export function quatFromMatrix(m: Mat4): Quat {
  const sx = Math.hypot(m[0]!, m[1]!, m[2]!) || 1;
  const sy = Math.hypot(m[4]!, m[5]!, m[6]!) || 1;
  const sz = Math.hypot(m[8]!, m[9]!, m[10]!) || 1;
  const m00 = m[0]! / sx;
  const m10 = m[1]! / sx;
  const m20 = m[2]! / sx;
  const m01 = m[4]! / sy;
  const m11 = m[5]! / sy;
  const m21 = m[6]! / sy;
  const m02 = m[8]! / sz;
  const m12 = m[9]! / sz;
  const m22 = m[10]! / sz;
  const trace = m00 + m11 + m22;
  let q: Quat;
  if (trace > 0) {
    const s = 0.5 / Math.sqrt(trace + 1);
    q = [(m21 - m12) * s, (m02 - m20) * s, (m10 - m01) * s, 0.25 / s];
  } else if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22);
    q = [0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s];
  } else if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22);
    q = [(m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s];
  } else {
    const s = 2 * Math.sqrt(1 + m22 - m00 - m11);
    q = [(m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s];
  }
  return quatNormalize(q);
}

/** Spherical interpolation from `a` to `b` by `t`, into `out` at `o`. */
export function slerpInto(a: ArrayLike<number>, ao: number, b: ArrayLike<number>, bo: number, t: number, out: Float32Array | number[], o: number): void {
  let bx = b[bo]!;
  let by = b[bo + 1]!;
  let bz = b[bo + 2]!;
  let bw = b[bo + 3]!;
  const ax = a[ao]!;
  const ay = a[ao + 1]!;
  const az = a[ao + 2]!;
  const aw = a[ao + 3]!;
  let cos = ax * bx + ay * by + az * bz + aw * bw;
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
    const angle = Math.acos(cos);
    const sin = Math.sin(angle);
    k0 = Math.sin((1 - t) * angle) / sin;
    k1 = Math.sin(t * angle) / sin;
  }
  out[o] = ax * k0 + bx * k1;
  out[o + 1] = ay * k0 + by * k1;
  out[o + 2] = az * k0 + bz * k1;
  out[o + 3] = aw * k0 + bw * k1;
}

// ------------------------------------------------------------------ 3-vectors

export type V3 = Vec3;

export const add3 = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const sub3 = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const scale3 = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
export const lerp3 = (a: V3, b: V3, t: number): V3 => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
export const cross3 = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export function norm3(a: V3): V3 {
  const len = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / len, a[1] / len, a[2] / len];
}
