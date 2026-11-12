// Which character a click on the world landed on, for the player menu
// (right-click someone: Trade, Whisper). Characters are drawn as one merged
// skinned mesh each, so rather than ray-picking meshes this projects each
// visible character's body (a vertical segment from the feet to the head)
// to the screen and takes the nearest segment within a few pixels, nearest
// to the camera on ties. The view-projection is Babylon's row-major
// `scene.getTransformMatrix().m`, as the nameplates use. Also the proximity
// rules for counters and trades. Pure, unit tested in pick.test.ts.

import type { Interactable, Vec3 } from "../wasm/sim_wasm";

export interface PickTarget {
  id: number;
  name: string;
  /** Feet. */
  x: number;
  y: number;
  z: number;
  /** Height of the head above the feet (m). */
  height: number;
}

/** How far from a body's outline a click still counts (px). */
export const PICK_SLOP_PX = 22;

type Matrix = ArrayLike<number>;

/** Screen position (px) of a world point, or null behind the camera. */
export function project(m: Matrix, x: number, y: number, z: number, width: number, height: number): { x: number; y: number; w: number } | null {
  const w = x * m[3]! + y * m[7]! + z * m[11]! + m[15]!;
  if (w <= 0.1) return null;
  const sx = ((x * m[0]! + y * m[4]! + z * m[8]! + m[12]!) / w) * 0.5 + 0.5;
  const sy = 0.5 - ((x * m[1]! + y * m[5]! + z * m[9]! + m[13]!) / w) * 0.5;
  return { x: sx * width, y: sy * height, w };
}

/** Distance (px) from point `p` to the segment `a`-`b`. */
export function segmentDistance(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 > 0 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/**
 * The character under screen point (`cx`, `cy`), or null. A body counts
 * within `PICK_SLOP_PX` of its projected feet-to-head line; between several,
 * the closest line wins, then the one nearer the camera.
 */
export function pickCharacter(targets: readonly PickTarget[], m: Matrix, width: number, height: number, cx: number, cy: number): PickTarget | null {
  let best: PickTarget | null = null;
  let bestD = Infinity;
  let bestW = Infinity;
  for (const t of targets) {
    const feet = project(m, t.x, t.y, t.z, width, height);
    const head = project(m, t.x, t.y + t.height, t.z, width, height);
    if (!feet || !head) continue;
    const d = segmentDistance(cx, cy, feet.x, feet.y, head.x, head.y);
    if (d > PICK_SLOP_PX) continue;
    // Prefer the clearly closer line; within a few pixels, the nearer body.
    if (d < bestD - 4 || (Math.abs(d - bestD) <= 4 && feet.w < bestW)) {
      best = t;
      bestD = d;
      bestW = feet.w;
    }
  }
  return best;
}

/** Horizontal distance between two points (m). */
export function horizontalDistance(a: Vec3 | { x: number; y: number; z: number }, b: Vec3 | { x: number; y: number; z: number }): number {
  const [ax, , az] = Array.isArray(a) ? a : [a.x, a.y, a.z];
  const [bx, , bz] = Array.isArray(b) ? b : [b.x, b.y, b.z];
  return Math.hypot(ax - bx, az - bz);
}

/** The server's height tolerance for reaching an interactable (`INTERACT_HEIGHT_TOLERANCE`, m). */
export const REACH_HEIGHT_M = 1.5;
/** How far beyond its reach a station grant holds (`realtime::stations::REACH_SLACK_M`, m). */
export const REACH_SLACK_M = 1.0;

/**
 * Whether a station's grant still holds at `pos` (the server's rule: within
 * the interactable's radius plus the slack, and its height tolerance). The
 * server decides; this lets a window close as the player walks off instead
 * of waiting for a refused request.
 */
export function grantHolds(station: Pick<Interactable, "pos" | "radius">, pos: Vec3, slack = REACH_SLACK_M): boolean {
  const dx = pos[0] - station.pos[0];
  const dz = pos[2] - station.pos[2];
  const r = station.radius + slack;
  return dx * dx + dz * dz <= r * r && Math.abs(pos[1] - station.pos[1]) <= REACH_HEIGHT_M;
}
