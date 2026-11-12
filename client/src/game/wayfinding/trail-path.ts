// The wayfinding trail's geometry (the "Monstra Viam" charm), without
// Babylon or the DOM: a route from sim-core's walkable-surface graph
// (`Wayfinder.path`, flat [x, y, z, ...] waypoints on the ground) as a
// polyline with arc lengths; where the player stands along it; how far is
// left; whether they have strayed or arrived; and the wisps to draw a few
// metres ahead.
//
// Wisps sit at fixed arc lengths (every `WISP_SPACING` metres from the
// trail's start), so they stay put on the floor as the player walks instead
// of sliding along with them; each is the average of its neighbours a
// spacing either side, which rounds the route's corners into a natural line.
// Alternate wisps step a little to either side, like footprints.
//
// Pure, unit tested in trail.test.ts.

/** Wisps are this far apart along the trail (m). */
export const WISP_SPACING = 0.7;
/** The trail is drawn from just ahead of the player to this far ahead (m). */
export const AHEAD_M = 11;
/** The first wisp is at least this far ahead (m): never under the player's feet. */
export const NEAR_M = 0.9;
/** Footprint stagger either side of the line (m). */
export const STAGGER_M = 0.11;
/** Height of a wisp above the ground (m). */
export const LIFT_M = 0.09;
/** Off the trail by more than this (plan, m) is strayed: the trail is planned again. */
export const STRAY_M = 2.6;
/** A storey change of more than this (m) away from the trail is strayed too. */
export const STRAY_DY = 1.8;
/** Within this of the end (plan, m, and on its storey) is arrived: inside the smallest door reach of a key place (dorm doors 1.5 m, class doors 1.8 m), so the door's E prompt shows on arrival. */
export const ARRIVE_M = 1.2;
/** Height counts this much more than plan distance when finding the nearest point (stairs over floors). */
const HEIGHT_WEIGHT = 3;

export interface TrailPath {
  /** Waypoints, flat x, y, z. */
  readonly pts: Float32Array;
  /** Arc length at each waypoint (plan distance plus climb). */
  readonly cum: Float32Array;
  readonly length: number;
}

/** Where a point stands against the trail. */
export interface Projection {
  /** Arc length of the nearest point. */
  s: number;
  /** Plan distance to it (m). */
  off: number;
  /** Height difference to it (m). */
  dy: number;
}

export function makePath(flat: ArrayLike<number>): TrailPath {
  const n = Math.floor(flat.length / 3);
  const pts = Float32Array.from({ length: n * 3 }, (_, i) => flat[i]!);
  const cum = new Float32Array(n);
  for (let i = 1; i < n; i++) {
    const dx = pts[i * 3]! - pts[i * 3 - 3]!;
    const dy = pts[i * 3 + 1]! - pts[i * 3 - 2]!;
    const dz = pts[i * 3 + 2]! - pts[i * 3 - 1]!;
    cum[i] = cum[i - 1]! + Math.sqrt(dx * dx + dy * dy + dz * dz);
  }
  return { pts, cum, length: n ? cum[n - 1]! : 0 };
}

/** The point at arc length `s` (clamped to the ends), written to `out`. */
export function pointAt(path: TrailPath, s: number, out: [number, number, number]): [number, number, number] {
  const { pts, cum } = path;
  const n = cum.length;
  if (n === 0) return out;
  if (s <= 0 || n === 1) {
    out[0] = pts[0]!;
    out[1] = pts[1]!;
    out[2] = pts[2]!;
    return out;
  }
  if (s >= path.length) {
    out[0] = pts[n * 3 - 3]!;
    out[1] = pts[n * 3 - 2]!;
    out[2] = pts[n * 3 - 1]!;
    return out;
  }
  // Binary search for the segment holding s.
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (cum[mid]! <= s) lo = mid;
    else hi = mid;
  }
  const len = cum[hi]! - cum[lo]!;
  const t = len > 1e-6 ? (s - cum[lo]!) / len : 0;
  for (let k = 0; k < 3; k++) out[k] = pts[lo * 3 + k]! + (pts[hi * 3 + k]! - pts[lo * 3 + k]!) * t;
  return out;
}

/** The nearest point of the trail to (x, y, z). */
export function project(path: TrailPath, x: number, y: number, z: number): Projection {
  const { pts, cum } = path;
  const n = cum.length;
  let best: Projection = { s: 0, off: Infinity, dy: 0 };
  let bestScore = Infinity;
  if (n === 1) {
    const off = Math.hypot(x - pts[0]!, z - pts[2]!);
    return { s: 0, off, dy: y - pts[1]! };
  }
  for (let i = 0; i + 1 < n; i++) {
    const ax = pts[i * 3]!;
    const ay = pts[i * 3 + 1]!;
    const az = pts[i * 3 + 2]!;
    const bx = pts[i * 3 + 3]!;
    const by = pts[i * 3 + 4]!;
    const bz = pts[i * 3 + 5]!;
    const ex = bx - ax;
    const ez = bz - az;
    const len2 = ex * ex + ez * ez;
    let t = len2 > 1e-9 ? ((x - ax) * ex + (z - az) * ez) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    const px = ax + ex * t;
    const py = ay + (by - ay) * t;
    const pz = az + ez * t;
    const off = Math.hypot(x - px, z - pz);
    const dy = y - py;
    const score = off + Math.abs(dy) * HEIGHT_WEIGHT;
    if (score < bestScore) {
      bestScore = score;
      best = { s: cum[i]! + (cum[i + 1]! - cum[i]!) * t, off, dy };
    }
  }
  return best;
}

/** Whether a player at `p` has left the trail (it should be planned again). */
export function strayed(p: Projection): boolean {
  return p.off > STRAY_M || Math.abs(p.dy) > STRAY_DY;
}

/** Whether (x, y, z) is at the trail's end. */
export function arrived(path: TrailPath, x: number, y: number, z: number): boolean {
  const n = path.cum.length;
  if (n === 0) return false;
  const ex = path.pts[n * 3 - 3]!;
  const ey = path.pts[n * 3 - 2]!;
  const ez = path.pts[n * 3 - 1]!;
  return Math.hypot(x - ex, z - ez) <= ARRIVE_M && Math.abs(y - ey) <= STRAY_DY;
}

/** A way point is worth walking through only while it saves the player no more than this (m) on the direct way. */
export const VIA_SLACK_M = 1;

/** The plan length (m) of flat `[x, y, z, ...]` waypoints. */
export function flatLengthM(flat: ArrayLike<number>): number {
  let m = 0;
  for (let i = 3; i + 2 < flat.length; i += 3) m += Math.hypot(flat[i]! - flat[i - 3]!, flat[i + 2]! - flat[i - 1]!);
  return m;
}

/**
 * A trail by way of a point (the tour's first leg in
 * through the front doors). `lead` runs from the player to the point,
 * `rest` from the point to the place, `direct` from the player to the place.
 * Returns the joined waypoints, or null when the point is no longer worth it
 * (a missing leg, or the player already as close to the place as the point
 * is: through the doors, or in by another way): then the trail goes direct.
 */
export function joinVia(direct: ArrayLike<number> | undefined, lead: ArrayLike<number> | undefined, rest: ArrayLike<number> | undefined): number[] | null {
  if (!direct || !lead || !rest || lead.length < 3 || rest.length < 3) return null;
  if (flatLengthM(direct) <= flatLengthM(rest) + VIA_SLACK_M) return null;
  const out = Array.from(lead);
  for (let i = 3; i < rest.length; i++) out.push(rest[i]!);
  return out;
}

/** Metres left to walk from a projection (the way back onto the trail, then along it). */
export function remainingM(path: TrailPath, p: Projection): number {
  return Math.max(0, path.length - p.s) + p.off;
}

/** "120 m", "8 m": distances for the HUD chip, rounded to a readable step. */
export function distanceText(m: number): string {
  if (!Number.isFinite(m)) return "";
  if (m < 20) return `${Math.max(1, Math.round(m))} m`;
  if (m < 200) return `${Math.round(m / 5) * 5} m`;
  return `${Math.round(m / 10) * 10} m`;
}

/** One wisp to draw: where, its index along the trail (for the pulse), and its opacity. */
export interface Wisp {
  x: number;
  y: number;
  z: number;
  k: number;
  alpha: number;
}

const A: [number, number, number] = [0, 0, 0];
const B: [number, number, number] = [0, 0, 0];
const C: [number, number, number] = [0, 0, 0];

/**
 * The wisps from just ahead of arc length `s` to `AHEAD_M` beyond it,
 * written into `out` (reused; returns the count). Fades in near the player
 * and out at the far end; the trail's last wisp sits on its end.
 */
export function wisps(path: TrailPath, s: number, out: Wisp[], max = 32): number {
  let count = 0;
  const first = Math.max(1, Math.ceil((s + NEAR_M) / WISP_SPACING));
  const lastK = Math.floor(path.length / WISP_SPACING);
  for (let k = first; count < max; k++) {
    const at = Math.min(k * WISP_SPACING, path.length);
    if (at - s > AHEAD_M || k > lastK + 1) break;
    // Rounded corners: the average of the points a spacing either side.
    pointAt(path, at - WISP_SPACING, A);
    pointAt(path, at, B);
    pointAt(path, at + WISP_SPACING, C);
    const end = at >= path.length;
    let x = end ? B[0] : (A[0] + 2 * B[0] + C[0]) / 4;
    let z = end ? B[2] : (A[2] + 2 * B[2] + C[2]) / 4;
    const y = B[1];
    // Footprint stagger: across the local direction.
    const dx = C[0] - A[0];
    const dz = C[2] - A[2];
    const dl = Math.hypot(dx, dz);
    if (dl > 1e-4 && !end) {
      const side = (k & 1 ? 1 : -1) * STAGGER_M;
      x += (-dz / dl) * side;
      z += (dx / dl) * side;
    }
    const ahead = at - s;
    const fadeIn = Math.min(1, Math.max(0, (ahead - NEAR_M * 0.5) / 1.2));
    const fadeOut = Math.min(1, Math.max(0, (AHEAD_M - ahead) / 3));
    const w = (out[count] ??= { x: 0, y: 0, z: 0, k: 0, alpha: 0 });
    w.x = x;
    w.y = y + LIFT_M;
    w.z = z;
    w.k = k;
    w.alpha = fadeIn * fadeOut;
    count++;
    if (at >= path.length) break;
  }
  return count;
}
