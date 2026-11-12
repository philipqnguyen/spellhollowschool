// Furniture placement math for decorating mode: turning the cursor's ray into
// a snapped placement on a floor, table top or wall, rotation steps, and
// picking placed items. Pure (no Babylon, no DOM) so it is unit tested.
//
// The rule itself is the server's (`sim_core::placement::check_placement`,
// previewed with `SimWorld.canPlace`); this module only proposes positions
// that respect it: snapped to the grid, clamped into the area the cursor
// points at, and quantized as the wire quantizes them (centimetres, 1/256
// turn), so what the ghost shows is exactly what the server will check.
//
// Conventions (docs/architecture.md, "Dorms and decorating"): floor and table
// items have their origin at the bottom centre of the footprint and face +Z
// at yaw 0 with `footprint[0]` along X; wall items have their origin at the
// bottom centre of their back and face the wall's `facing`. The tops and
// shelves of placed pieces (`furnitureTopAreas`, from the catalog) carry the
// piece's frame: they are picked and snapped in the piece's own axes, so
// they turn with it at any yaw.

import type { AreaFrame, PlaceArea, PlaceSurface, Vec3 } from "../../wasm/sim_wasm";

/** Placement grid (m), as `sim_core::placement::GRID_STEP_M`. */
export const GRID_STEP_M = 0.25;
/** Grid in fine mode (m), as `FINE_STEP_M`. */
export const FINE_STEP_M = 0.05;
/** Rotation step in wire yaw units (1/256 turn), as `YAW_STEP`: 22.5 degrees. Fine mode turns by 1 unit. */
export const YAW_STEP = 16;
export const YAW_UNITS = 256;
const TAU = Math.PI * 2;
/** Mirrors `PLACE_EPSILON` (m). */
const EPSILON = 0.01;
/** How far past a floor or wall area's edge the cursor may point and still place (clamped inside it), m. */
const EDGE_MARGIN = { floor: 0.75, wall: 0.5 } as const;

/** The placement-relevant part of a catalog entry's `furniture` block. */
export interface FurnitureShape {
  surface: PlaceSurface;
  /** Width along X and depth along Z at yaw 0 (m). */
  footprint: [number, number];
  height: number;
  /** Height of a flat top that table items may stand on (m). */
  top?: number;
}

export interface Ray {
  origin: Vec3;
  /** Need not be normalized. */
  dir: Vec3;
}

/** A proposed placement, already quantized as on the wire. */
export interface Placement {
  pos: Vec3;
  /** Wire yaw units, 0-255. */
  yawUnits: number;
  /** Id of the area it was put on. */
  area: string;
}

export interface Box {
  min: Vec3;
  max: Vec3;
}

// ------------------------------------------------------------------ units

/** Radians to wire yaw units, as `protocol::yaw_to_u8`. */
export function yawToUnits(yaw: number): number {
  const norm = ((yaw % TAU) + TAU) % TAU;
  return Math.round((norm / TAU) * YAW_UNITS) % YAW_UNITS;
}

export function unitsToYaw(units: number): number {
  return (units / YAW_UNITS) * TAU;
}

/** Turns by one step (`dir` +1 counter-clockwise seen from above, -1 clockwise). */
export function rotateUnits(units: number, dir: 1 | -1, fine: boolean): number {
  const step = fine ? 1 : YAW_STEP;
  return (((units + dir * step) % YAW_UNITS) + YAW_UNITS) % YAW_UNITS;
}

/** Rounds to centimetres, as the wire does (`QPos::from_m`). */
export function toCm(v: number): number {
  return Math.round(v * 100) / 100;
}

/** Nearest multiple of `step`. */
export function snap(v: number, step: number): number {
  return toCm(Math.round(v / step) * step);
}

export function gridStep(fine: boolean): number {
  return fine ? FINE_STEP_M : GRID_STEP_M;
}

/** Half extents (x, z) of a footprint turned by `yaw` radians, as sim-core's `half_extents`. */
export function halfExtents(footprint: readonly [number, number], yaw: number): [number, number] {
  const s = Math.abs(Math.sin(yaw));
  const c = Math.abs(Math.cos(yaw));
  const hw = footprint[0] / 2;
  const hd = footprint[1] / 2;
  return [c * hw + s * hd, s * hw + c * hd];
}

// ------------------------------------------------------------------ areas

/** Which area surfaces an item of `surface` can stand on (as `check_placement`). */
export function usableSurfaces(surface: PlaceSurface): readonly PlaceSurface[] {
  switch (surface) {
    case "floor":
      return ["floor"];
    case "table":
      return ["table", "floor"];
    case "wall":
      return ["wall"];
  }
}

/** A wall area's plane is `x = const` (else `z = const`). */
function wallOnXPlane(area: PlaceArea): boolean {
  return Math.abs(area.max[0] - area.min[0]) <= EPSILON;
}

// ------------------------------------------------------------------ frames

/** A position in `frame`'s axes (`sim_core::placement::AreaFrame::to_local`). */
export function toLocal(frame: AreaFrame, p: Vec3): Vec3 {
  const s = Math.sin(frame.yaw);
  const c = Math.cos(frame.yaw);
  const dx = p[0] - frame.origin[0];
  const dz = p[2] - frame.origin[2];
  return [dx * c - dz * s, p[1] - frame.origin[1], dx * s + dz * c];
}

/** A direction in `frame`'s axes. */
function toLocalDir(frame: AreaFrame, d: Vec3): Vec3 {
  const s = Math.sin(frame.yaw);
  const c = Math.cos(frame.yaw);
  return [d[0] * c - d[2] * s, d[1], d[0] * s + d[2] * c];
}

/** A position in `frame`'s axes back in the world (`AreaFrame::to_world`). */
export function toWorld(frame: AreaFrame, l: Vec3): Vec3 {
  const s = Math.sin(frame.yaw);
  const c = Math.cos(frame.yaw);
  return [frame.origin[0] + l[0] * c + l[2] * s, frame.origin[1] + l[1], frame.origin[2] - l[0] * s + l[2] * c];
}

/**
 * How far below a piece's top the cursor still finds it (m): pointing at a
 * desk's drawers or a trunk's front puts a lamp on top, not on the floor
 * behind it. A shelf catches the cursor over its whole headroom.
 */
export const TOP_CATCH_M = 0.5;

/**
 * Where `ray` finds a framed table area (a placed piece's top or shelf): the
 * box from the top down `TOP_CATCH_M` (no lower than the piece's foot), or a
 * shelf's headroom, in the piece's axes. Returns the distance along the ray
 * and the point on the surface, in the piece's axes.
 */
function rayHitCatch(local: Ray, area: PlaceArea): { t: number; point: Vec3 } | null {
  const y = area.min[1];
  const lo = area.clear !== undefined ? y : Math.max(0, y - TOP_CATCH_M);
  const hi = area.clear !== undefined ? y + area.clear : y;
  const t = rayHitBox(local, { min: [area.min[0], lo, area.min[2]], max: [area.max[0], hi, area.max[2]] });
  if (t === null) return null;
  const o = local.origin;
  const d = local.dir;
  return { t, point: [o[0] + d[0] * t, y, o[2] + d[2] * t] };
}

/**
 * Where `ray` meets `area`'s plane, if it does within the area grown by
 * `margin` (m). Walls only count when seen from the room side.
 */
export function rayHitArea(ray: Ray, area: PlaceArea, margin: number): { t: number; point: Vec3 } | null {
  const o = ray.origin;
  const d = ray.dir;
  // Axis of the area's plane: y for floors and tables, x or z for walls.
  const axis = area.surface === "wall" ? (wallOnXPlane(area) ? 0 : 2) : 1;
  if (Math.abs(d[axis]) < 1e-9) return null;
  if (area.surface === "wall") {
    const f = area.facing ?? 0;
    // The ray must travel against the wall's facing (toward the wall from inside).
    if (d[0] * Math.sin(f) + d[2] * Math.cos(f) >= 0) return null;
  } else if (d[1] >= 0) {
    return null; // looking up at a floor from below
  }
  const t = (area.min[axis] - o[axis]) / d[axis];
  if (t <= 0) return null;
  const point: Vec3 = [o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t];
  for (let i = 0; i < 3; i++) {
    if (i === axis) continue;
    if (point[i] < area.min[i] - margin || point[i] > area.max[i] + margin) return null;
  }
  point[axis] = area.min[axis];
  return { t, point };
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * Snaps an item to the grid around `hit` on `area` and pulls it inside the
 * area. Floor and table items keep `yawUnits`; wall items take the wall's
 * facing. Null when the item is larger than the area.
 */
export function placeOnArea(shape: FurnitureShape, area: PlaceArea, hit: Vec3, yawUnits: number, fine: boolean): Placement | null {
  const step = gridStep(fine);
  if (area.surface === "wall") {
    const plane = wallOnXPlane(area) ? 0 : 2;
    const along = plane === 0 ? 2 : 0;
    const half = shape.footprint[0] / 2;
    const lo = area.min[along] + half;
    const hi = area.max[along] - half;
    const bottomMax = area.max[1] - shape.height;
    if (lo > hi + EPSILON || area.min[1] > bottomMax + EPSILON) return null;
    const pos: Vec3 = [0, 0, 0];
    pos[plane] = toCm(area.min[plane]);
    pos[along] = inward(clamp(snap(hit[along], step), lo, hi), lo, hi);
    // The cursor points at the item's middle, not its bottom edge.
    pos[1] = inward(clamp(snap(hit[1] - shape.height / 2, step), area.min[1], bottomMax), area.min[1], bottomMax);
    return { pos, yawUnits: yawToUnits(area.facing ?? 0), area: area.id };
  }
  const [hx, hz] = halfExtents(shape.footprint, unitsToYaw(yawUnits));
  const xlo = area.min[0] + hx;
  const xhi = area.max[0] - hx;
  const zlo = area.min[2] + hz;
  const zhi = area.max[2] - hz;
  if (xlo > xhi + EPSILON || zlo > zhi + EPSILON) return null;
  return {
    pos: [inward(clamp(snap(hit[0], step), xlo, xhi), xlo, xhi), toCm(area.min[1]), inward(clamp(snap(hit[2], step), zlo, zhi), zlo, zhi)],
    yawUnits,
    area: area.id,
  };
}

/** Rounds a clamped value to centimetres without leaving [lo, hi] by more than the server's slack. */
function inward(v: number, lo: number, hi: number): number {
  const cm = toCm(v);
  if (cm < lo - EPSILON / 2) return Math.ceil(v * 100) / 100;
  if (cm > hi + EPSILON / 2) return Math.floor(v * 100) / 100;
  return cm;
}

/**
 * The placement for an item of `shape` where `ray` (from the camera through
 * the cursor) meets the nearest area it can stand on. `areas` are the
 * layout's plus the table tops of placed items. Null when the cursor points
 * at nothing usable.
 */
export function pickPlacement(ray: Ray, areas: readonly PlaceArea[], shape: FurnitureShape, yawUnits: number, fine: boolean): Placement | null {
  const usable = usableSurfaces(shape.surface);
  let best: { t: number; area: PlaceArea; point: Vec3 } | null = null;
  for (const area of areas) {
    if (!usable.includes(area.surface)) continue;
    if (area.frame) {
      // A placed piece's top or shelf, in the piece's own axes.
      const f = area.frame;
      const hit = rayHitCatch({ origin: toLocal(f, ray.origin), dir: toLocalDir(f, ray.dir) }, area);
      // A shelf beats the top's catch box it lies in (both start at the front face).
      const t = hit ? hit.t - (area.clear !== undefined ? 0.02 : 0) : Infinity;
      if (hit && (!best || t < best.t)) best = { t, area, point: hit.point };
      continue;
    }
    // Only the floor and walls catch a cursor past their edge; a table top
    // with a margin would swallow the floor around it.
    const margin = area.surface === "floor" ? EDGE_MARGIN.floor : area.surface === "wall" ? EDGE_MARGIN.wall : 0;
    const hit = rayHitArea(ray, area, margin);
    if (!hit) continue;
    // A hit inside an area beats a hit in another area's margin.
    const inside = rayHitArea(ray, area, 0) !== null;
    const t = inside ? hit.t : hit.t + 1000;
    if (!best || t < best.t) best = { t, area, point: hit.point };
  }
  if (!best) return null;
  const f = best.area.frame;
  if (!f) return placeOnArea(shape, best.area, best.point, yawUnits, fine);
  // Snap and clamp in the piece's axes (its own grid), then back to the world.
  const frameUnits = yawToUnits(f.yaw);
  const rel = (((yawUnits - frameUnits) % YAW_UNITS) + YAW_UNITS) % YAW_UNITS;
  const local = placeOnArea(shape, { ...best.area, frame: undefined }, best.point, rel, fine);
  if (!local) return null;
  const w = toWorld(f, local.pos);
  return { pos: [toCm(w[0]), toCm(w[1]), toCm(w[2])], yawUnits, area: best.area.id };
}

/**
 * A catalog seat (`furniture.seats`): its top `y` over `area` `[x0, z0, x1,
 * z1]` in the piece's axes, the sitter facing `yaw` relative to the piece;
 * `front` and `back` how far it reaches ahead of and behind the area's
 * centre line along the facing (its front edge; the backrest's face or the
 * back edge), `drop` its top above the floor the feet rest on: the castle
 * rows' fit (castle/seats.ts, "Seats fit the sitter's legs").
 */
export interface FurnitureSeat {
  y: number;
  area: [number, number, number, number];
  yaw?: number;
  front: number;
  back: number;
  drop: number;
}

/**
 * The seat rows (`castle/seats.ts`: `[x0, z0, x1, z1, y, yaw, front, back,
 * drop]`, world axes) of a piece's seats where it stands. A row is
 * axis-aligned, so a long seat (a bed's side) is cut into pieces about as
 * long as it is deep and each piece's box is a row: turned at any yaw, the
 * sitter stays on it. A row's box keeps its piece's centre, so the row's
 * centre line (across the facing, mid-depth) is the seat area's and
 * `front` and `back` hold for every row.
 */
export function seatRows(seats: readonly FurnitureSeat[], pos: Vec3, yawUnits: number): number[] {
  const frame: AreaFrame = { origin: pos, yaw: unitsToYaw(yawUnits) };
  const rows: number[] = [];
  for (const s of seats) {
    const [x0, z0, x1, z1] = s.area;
    const w = x1 - x0;
    const d = z1 - z0;
    const alongX = w >= d;
    const n = Math.max(1, Math.round(alongX ? w / d : d / w));
    for (let i = 0; i < n; i++) {
      const a0 = i / n;
      const a1 = (i + 1) / n;
      const [lx0, lx1, lz0, lz1] = alongX ? [x0 + w * a0, x0 + w * a1, z0, z1] : [x0, x1, z0 + d * a0, z0 + d * a1];
      const corners = [toWorld(frame, [lx0, 0, lz0]), toWorld(frame, [lx1, 0, lz0]), toWorld(frame, [lx0, 0, lz1]), toWorld(frame, [lx1, 0, lz1])];
      const xs = corners.map((c) => c[0]);
      const zs = corners.map((c) => c[2]);
      const yaw = (((frame.yaw + (s.yaw ?? 0)) % TAU) + TAU) % TAU;
      rows.push(Math.min(...xs), Math.min(...zs), Math.max(...xs), Math.max(...zs), pos[1] + s.y, yaw, s.front, s.back, s.drop);
    }
  }
  return rows;
}

/**
 * The box a placed piece's ghost outline and hover highlight are drawn
 * with: centred on the piece, turned with it (`yaw` radians), `size` its
 * footprint and height. Wall items stick out from the wall along their facing.
 */
export function orientedBox(shape: FurnitureShape, pos: Vec3, yawUnits: number): { centre: Vec3; size: Vec3; yaw: number } {
  const yaw = unitsToYaw(yawUnits);
  const h = Math.max(shape.height, 0.02);
  const depth = Math.max(shape.footprint[1], 0.02);
  const out = shape.surface === "wall" ? depth / 2 : 0;
  return {
    centre: [pos[0] + Math.sin(yaw) * out, pos[1] + h / 2, pos[2] + Math.cos(yaw) * out],
    size: [Math.max(shape.footprint[0], 0.02), h, depth],
    yaw,
  };
}

// ------------------------------------------------------------------ placed items

/** The box an item occupies, for picking it with the cursor. */
export function itemBox(shape: FurnitureShape, pos: Vec3, yawUnits: number): Box {
  const yaw = unitsToYaw(yawUnits);
  let cx = pos[0];
  let cz = pos[2];
  if (shape.surface === "wall") {
    // The origin is the middle of its back; it sticks out along its facing.
    cx += (Math.sin(yaw) * shape.footprint[1]) / 2;
    cz += (Math.cos(yaw) * shape.footprint[1]) / 2;
  }
  // Thin items (rugs, paintings) still get a box the cursor can find.
  const [hx, hz] = halfExtents(shape.footprint, yaw).map((v) => Math.max(v, 0.05)) as [number, number];
  return { min: [cx - hx, pos[1], cz - hz], max: [cx + hx, pos[1] + Math.max(shape.height, 0.05), cz + hz] };
}

/** Distance along `ray` to `box` (slab test), or null when it misses. */
export function rayHitBox(ray: Ray, box: Box): number | null {
  let tmin = 0;
  let tmax = Infinity;
  for (let i = 0; i < 3; i++) {
    const o = ray.origin[i]!;
    const d = ray.dir[i]!;
    if (Math.abs(d) < 1e-12) {
      if (o < box.min[i]! || o > box.max[i]!) return null;
      continue;
    }
    let t1 = (box.min[i]! - o) / d;
    let t2 = (box.max[i]! - o) / d;
    if (t1 > t2) [t1, t2] = [t2, t1];
    tmin = Math.max(tmin, t1);
    tmax = Math.min(tmax, t2);
    if (tmin > tmax) return null;
  }
  return tmin;
}
