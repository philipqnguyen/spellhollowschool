// The level geometry a nameplate's line of sight is tested against
// (ui/plate-sight.ts): the camera's walls (zones-scene.ts `walls`: the camera
// colliders of the shown castle cells, every mesh of the others, or the
// instance room's meshes), which hold the walls, floors, ceilings and stairs
// but not the walk-through props: a cell's or room's merged `<name>.castle_props`
// mesh (benches, desks, beds) is skipped wherever it turns up (an instance
// room has no colliders, so its walls are every mesh it has; the doors and
// the swinging flights are their own `castle_props.<node>` meshes and still
// count). A head inside a castle cell the visibility
// set has hidden is out of sight without a ray: nothing in that cell is drawn.
//
// Cost: Babylon's `Ray.intersectsMesh` boxes the endless ray, so the lake
// under the castle or the far backdrop had every triangle tried (~100 µs a
// ray in the Great Hall). Here the segment is boxed against each mesh, then
// against runs of CHUNK triangles kept in world space (the castle's meshes
// never move: their world matrices are frozen), and only the runs it meets
// are tried. A mesh that moves (a swinging flight's collider) goes to
// Babylon's test as the camera's does.

import { Ray } from "@babylonjs/core/Culling/ray";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { SightTest } from "../ui/plate-sight";

export interface SightLevel {
  /** The meshes that block sight now (re-read every test: cells stream and show). */
  walls(): Iterable<AbstractMesh>;
  /** Whether (x, y, z) lies in a castle cell that is not shown; false when unknown. */
  hiddenAt?(x: number, y: number, z: number): boolean;
}

/** Triangles per run with its own box. */
const CHUNK = 24;
/** Margin on a box (m), for faces lying in its planes. */
const BOX_MARGIN = 0.01;

/** A mesh's triangles in world space (9 floats each) and a box per run of CHUNK (6 floats each). */
export interface TriangleSoup {
  tris: Float32Array;
  boxes: Float32Array;
}

/** Each mesh's soup, built the first time a segment reaches its box (shared: meshes are the level's). */
const soups = new WeakMap<AbstractMesh, TriangleSoup | null>();

export function levelSightTest(level: SightLevel): SightTest {
  const ray = new Ray(Vector3.Zero(), Vector3.Forward(), 1);
  return (ax, ay, az, bx, by, bz) => {
    if (level.hiddenAt?.(bx, by, bz)) return true;
    const dx = bx - ax;
    const dy = by - ay;
    const dz = bz - az;
    const len = Math.hypot(dx, dy, dz);
    if (len < 1e-4) return false;
    const ux = dx / len;
    const uy = dy / len;
    const uz = dz / len;
    const ix = 1 / ux;
    const iy = 1 / uy;
    const iz = 1 / uz;
    for (const mesh of level.walls()) {
      // Babylon's own ray tests skip a mesh without a material (cells.ts gives the colliders one).
      if (!mesh.material || isFurniture(mesh)) continue;
      const bb = mesh.getBoundingInfo().boundingBox;
      const lo = bb.minimumWorld;
      const hi = bb.maximumWorld;
      if (!segmentHitsBox(ax, ay, az, ix, iy, iz, len, lo.x, lo.y, lo.z, hi.x, hi.y, hi.z)) continue;
      if (!mesh.isWorldMatrixFrozen) {
        ray.origin.set(ax, ay, az);
        ray.direction.set(ux, uy, uz);
        ray.length = len;
        if (ray.intersectsMesh(mesh, true).hit) return true;
        continue;
      }
      let soup = soups.get(mesh);
      if (soup === undefined) {
        soup = soupOf(mesh);
        soups.set(mesh, soup);
      }
      if (soup && soupBlocks(soup, ax, ay, az, ux, uy, uz, ix, iy, iz, len)) return true;
    }
    return false;
  };
}

/** A merged furniture and props mesh (`<cell>.castle_props`): furniture never hides a name. */
export function isFurniture(mesh: { name: string }): boolean {
  return mesh.name.endsWith(".castle_props");
}

/** `mesh`'s triangles in world space, boxed in runs; null without positions. */
function soupOf(mesh: AbstractMesh): TriangleSoup | null {
  const pos = mesh.getVerticesData(VertexBuffer.PositionKind);
  if (!pos) return null;
  const ind = mesh.getIndices();
  const m = mesh.getWorldMatrix().m;
  const count = ind ? Math.floor(ind.length / 3) : Math.floor(pos.length / 9);
  const world = new Float32Array(count * 9);
  for (let t = 0; t < count; t++) {
    for (let v = 0; v < 3; v++) {
      const i = (ind ? ind[t * 3 + v]! : t * 3 + v) * 3;
      const x = pos[i]!;
      const y = pos[i + 1]!;
      const z = pos[i + 2]!;
      const o = t * 9 + v * 3;
      world[o] = x * m[0]! + y * m[4]! + z * m[8]! + m[12]!;
      world[o + 1] = x * m[1]! + y * m[5]! + z * m[9]! + m[13]!;
      world[o + 2] = x * m[2]! + y * m[6]! + z * m[10]! + m[14]!;
    }
  }
  return soupFromTriangles(world);
}

/** Boxes runs of CHUNK triangles (world space, 9 floats each). */
export function soupFromTriangles(tris: Float32Array): TriangleSoup {
  const count = Math.floor(tris.length / 9);
  const runs = Math.ceil(count / CHUNK);
  const boxes = new Float32Array(runs * 6);
  for (let r = 0; r < runs; r++) {
    let x0 = Infinity;
    let y0 = Infinity;
    let z0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    let z1 = -Infinity;
    const end = Math.min(count, (r + 1) * CHUNK) * 9;
    for (let o = r * CHUNK * 9; o < end; o += 3) {
      const x = tris[o]!;
      const y = tris[o + 1]!;
      const z = tris[o + 2]!;
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
      if (z < z0) z0 = z;
      if (z > z1) z1 = z;
    }
    const b = r * 6;
    boxes[b] = x0;
    boxes[b + 1] = y0;
    boxes[b + 2] = z0;
    boxes[b + 3] = x1;
    boxes[b + 4] = y1;
    boxes[b + 5] = z1;
  }
  return { tris, boxes };
}

/** Whether the segment (origin, unit direction and its inverses, length) crosses any triangle of `soup`. */
export function soupBlocks(soup: TriangleSoup, ox: number, oy: number, oz: number, ux: number, uy: number, uz: number, ix: number, iy: number, iz: number, len: number): boolean {
  const { tris, boxes } = soup;
  const count = Math.floor(tris.length / 9);
  for (let r = 0, b = 0; b < boxes.length; r++, b += 6) {
    if (!segmentHitsBox(ox, oy, oz, ix, iy, iz, len, boxes[b]!, boxes[b + 1]!, boxes[b + 2]!, boxes[b + 3]!, boxes[b + 4]!, boxes[b + 5]!)) continue;
    const end = Math.min(count, (r + 1) * CHUNK) * 9;
    for (let o = r * CHUNK * 9; o < end; o += 9) {
      // Möller-Trumbore, either winding.
      const v0x = tris[o]!;
      const v0y = tris[o + 1]!;
      const v0z = tris[o + 2]!;
      const e1x = tris[o + 3]! - v0x;
      const e1y = tris[o + 4]! - v0y;
      const e1z = tris[o + 5]! - v0z;
      const e2x = tris[o + 6]! - v0x;
      const e2y = tris[o + 7]! - v0y;
      const e2z = tris[o + 8]! - v0z;
      const px = uy * e2z - uz * e2y;
      const py = uz * e2x - ux * e2z;
      const pz = ux * e2y - uy * e2x;
      const det = e1x * px + e1y * py + e1z * pz;
      if (det > -1e-9 && det < 1e-9) continue;
      const inv = 1 / det;
      const sx = ox - v0x;
      const sy = oy - v0y;
      const sz = oz - v0z;
      const u = (sx * px + sy * py + sz * pz) * inv;
      if (u < 0 || u > 1) continue;
      const qx = sy * e1z - sz * e1y;
      const qy = sz * e1x - sx * e1z;
      const qz = sx * e1y - sy * e1x;
      const v = (ux * qx + uy * qy + uz * qz) * inv;
      if (v < 0 || u + v > 1) continue;
      const t = (e2x * qx + e2y * qy + e2z * qz) * inv;
      if (t >= 0 && t <= len) return true;
    }
  }
  return false;
}

/**
 * Whether the segment from (ox, oy, oz) along a unit direction (given as its
 * inverses `ix`, `iy`, `iz`) for `len` metres meets the box (a slab test).
 * A zero direction gives infinite inverses: outside that slab, never; on its
 * face (NaN), the span is left as it is.
 */
export function segmentHitsBox(ox: number, oy: number, oz: number, ix: number, iy: number, iz: number, len: number, x0: number, y0: number, z0: number, x1: number, y1: number, z1: number): boolean {
  let t0 = 0;
  let t1 = len;
  let a = (x0 - BOX_MARGIN - ox) * ix;
  let b = (x1 + BOX_MARGIN - ox) * ix;
  if (a > b) {
    const t = a;
    a = b;
    b = t;
  }
  if (a > t0) t0 = a;
  if (b < t1) t1 = b;
  if (t0 > t1) return false;
  a = (y0 - BOX_MARGIN - oy) * iy;
  b = (y1 + BOX_MARGIN - oy) * iy;
  if (a > b) {
    const t = a;
    a = b;
    b = t;
  }
  if (a > t0) t0 = a;
  if (b < t1) t1 = b;
  if (t0 > t1) return false;
  a = (z0 - BOX_MARGIN - oz) * iz;
  b = (z1 + BOX_MARGIN - oz) * iz;
  if (a > b) {
    const t = a;
    a = b;
    b = t;
  }
  if (a > t0) t0 = a;
  if (b < t1) t1 = b;
  return t0 <= t1;
}
