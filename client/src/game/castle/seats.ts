// Seats for the sit emote (castle kit M3, docs/art/castle-kit.md and
// 17.12 leftover 2): kitted cells export `seats: [[x0, z0, x1, z1, y, yaw,
// front, back, drop], ...]` in the world manifest, one row per bench or
// chair seat (world metres, `y` the seat's top, `yaw` the way a sitter
// faces, 0 = +z as props.json and the characters; the rectangle is the
// hips' band). A character playing `sit` within `SEAT_REACH` of a seat in
// plan is drawn sitting on it: on the seat's top, facing its way, drawn onto
// the seat (inside its rectangle). Visual only: the server and the positions
// it checks are unchanged. The seated `sit` clip puts the sit bones on the
// root's height, so the root goes on the seat's top. With no seat near, the
// characters' renderer plays the floor sit (`floor_rest`,
// docs/art/character-adoption.md) at the feet.
//
// Seats fit the sitter's legs: `front` and `back` are how far the seat
// reaches ahead of the row's centre line (its front edge, the cushion or the
// frame, whichever is further) and behind it (the backrest's face, or the
// back edge of a backless seat), `drop` the seat's top above the floor the
// feet rest on. `find` puts the drawn point on the row's centre line and
// writes them (`ahead`, `behind`, `drop`); the renderer then slides the hips
// along the seat's depth so the sitter's calves hang just in front of the
// front edge (characters/seated.ts `seatShift`) and lifts the knees when the
// shins are longer than the drop. Older six-number rows (and room seats
// without `front`) write NaN there: drawn as before, unfitted.
//
// The rows are read once (`Seats.fromCells`); `find` is a scan over a flat
// array for characters that sit, allocation-free.
//
// Two-sided seats: a row's tenth number, `flip_drop`, is 0 on a one-way
// seat; above 0 the seat may also be sat on facing the other way (a room
// seat's `two_sided` and `flip_drop`): a backless seat with nothing in the
// legs' space behind it (tools/blender/world/seat_sides.py decides at
// export), the number the seat's top over the floor the legs then rest on
// or hang over (seat_sides.FLIP_DROP_MAX over a drop with no floor near).
// `find` given the sitter's own facing draws it on the side nearest that
// facing: the seat's yaw, or the yaw turned half round with `ahead` and
// `behind` swapped and `drop` the flip drop (so the knees rise only for a
// floor that is there). `floor` stays the floor under the seat itself (the
// blob shadow). One-way seats ignore the facing. The facing is the
// character's replicated yaw, so turning on the bench (bench-flip.ts) is a
// `Move` that keeps the position and turns the yaw, and every client draws
// it alike.
//
// Floor seats (`Seats.floorFromCells`): the common rooms' rugs and
// hearth steps, exported by castle M5b as `floor_seats: [[x0, z0, x1, z1,
// y], ...]` (no facing: the sitter keeps its own, `find` writes yaw NaN).
// A `sit` with no bench or chair in reach but on or by one plays `floor_sit`
// (compact, cross-legged, hands in the lap) on its top, at least
// `FLOOR_SEAT_INSET` inside its edges so the folded legs stay on it.

/** How far (m, in plan, to the seat's rectangle) a sitter takes a seat. */
export const SEAT_REACH = 0.6;
/** A seat counts from this far below the feet (a step down onto a bench) to this far above them (m). */
export const SEAT_BELOW = 0.3;
export const SEAT_ABOVE = 1.2;
/** The drawn sitter keeps this far (m) inside a seat's ends and edges, or its middle on a narrow seat. */
export const SEAT_INSET = 0.25;
/** A floor sitter keeps this far inside a floor seat's edges (half the 0.6 m the floor_sit pose takes). */
export const FLOOR_SEAT_INSET = 0.3;

/** Numbers per row as kept (`Seats` keeps every row at `ROW`: NaN where a short row has no fit, 0 where it has no flip drop). */
const ROW = 10;
/** Rows as read: with the flip drop, without it (fitted, one-way), the oldest (no fit), a floor seat's. */
const SIDED_ROW = 10;
const FIT_ROW = 9;
const SHORT_ROW = 6;
const FLOOR_ROW = 5;

/**
 * Rooms (the Charms classroom, instances.json `seats`: `[{ pos, yaw }]`,
 * the hips over a bench with `pos`'s y the tier's floor): the bench top
 * stands this far above it (tools/blender/world/school_rooms.py
 * `SEAT_HEIGHT`), so a seated character's root goes there.
 */
export const ROOM_SEAT_HEIGHT = 0.46;
/** Half the width and depth (m) of a room seat's rectangle round its hips mark. */
const ROOM_SEAT_HALF: readonly [number, number] = [0.3, 0.15];

/** Where a sitter is drawn (written by `find`). */
export interface SeatPlace {
  x: number;
  y: number;
  z: number;
  yaw: number;
  /**
   * How far the seat reaches ahead of (x, z) along the facing to its front
   * edge, behind it to the backrest (or the back edge), and its top above
   * the floor under it (m). NaN when the seat does not say (unfitted).
   */
  ahead: number;
  behind: number;
  drop: number;
  /** The floor under the seat itself (its top less the drop it was placed with; NaN unfitted): the blob shadow's. */
  floor: number;
  /** The seat may be sat on facing either way (`yaw` is then the side nearest the sitter's facing). */
  twoSided: boolean;
  /** Which seat it is (a row's index, -1: none), so a sitter turning on it can be told from one moving to another. */
  seat: number;
}

/** A blank place for `find` to write into. */
export function seatPlace(): SeatPlace {
  return { x: 0, y: 0, z: 0, yaw: 0, ahead: Number.NaN, behind: Number.NaN, drop: Number.NaN, floor: Number.NaN, twoSided: false, seat: -1 };
}

/** A manifest cell, as far as seats go. */
export interface SeatsCell {
  id: string;
  seats?: unknown;
  floor_seats?: unknown;
}

/**
 * A row `[x0, z0, x1, z1, y, yaw, front, back, drop, flip_drop]` of finite
 * numbers (corners in any order; `front`, `back`, `drop` and `flip_drop` not
 * negative, `flip_drop` 0 one-way), one without it (one-way), an older six-number
 * row (no fit: NaN for the fit, one-way), or a floor seat's `[x0, z0, x1,
 * z1, y]` (`floor`; yaw NaN), or null.
 */
function parseRow(r: unknown, floor = false): number[] | null {
  if (!Array.isArray(r) || !r.every((v) => typeof v === "number" && Number.isFinite(v))) return null;
  if (floor ? r.length !== FLOOR_ROW : r.length !== SIDED_ROW && r.length !== FIT_ROW && r.length !== SHORT_ROW) return null;
  const [x0, z0, x1, z1, y, yaw, front, back, drop, two] = r as number[];
  const fit = r.length >= FIT_ROW;
  if (fit && (front! < 0 || back! < 0 || drop! <= 0)) return null;
  if (r.length === SIDED_ROW && !(two! >= 0)) return null;
  const nan = Number.NaN;
  return [Math.min(x0!, x1!), Math.min(z0!, z1!), Math.max(x0!, x1!), Math.max(z0!, z1!), y!, floor ? nan : yaw!, fit ? front! : nan, fit ? back! : nan, fit ? drop! : nan, r.length === SIDED_ROW && fit ? two! : 0];
}

/** A finite number at least `min`, or NaN. */
function fitNumber(v: unknown, min: number): number {
  return typeof v === "number" && Number.isFinite(v) && v >= min ? v : Number.NaN;
}

function inset(v: number, lo: number, hi: number, by: number): number {
  const m = Math.min(by, (hi - lo) / 2);
  return Math.min(hi - m, Math.max(lo + m, v));
}

export class Seats {
  private constructor(
    private readonly rows: Float64Array,
    readonly count: number,
    private readonly insetBy = SEAT_INSET,
  ) {}

  /**
   * A room's seats (instances.json `seats`: `[{ pos: [x, y, z], yaw, front?,
   * back?, drop? }]`, room coordinates): each a small rectangle round the
   * hips mark with its top `ROOM_SEAT_HEIGHT` above `pos`'s y. `front` and
   * `back` fit the sitter as a castle row's (from the hips mark; without
   * them the seat is unfitted), `drop` the top above the floor the feet
   * rest on (default `ROOM_SEAT_HEIGHT`), `two_sided` true when it may be
   * sat on facing either way, `flip_drop` the legs' drop then (default
   * `drop`). Malformed entries are skipped; null when
   * there are none.
   */
  static fromRoomSeats(seats: unknown): Seats | null {
    if (!Array.isArray(seats)) return null;
    const rows: number[] = [];
    for (const s of seats) {
      const { pos, yaw, front, back, drop, two_sided, flip_drop } = (s ?? {}) as { pos?: unknown; yaw?: unknown; front?: unknown; back?: unknown; drop?: unknown; two_sided?: unknown; flip_drop?: unknown };
      if (!Array.isArray(pos) || pos.length !== 3 || !pos.every((v) => typeof v === "number" && Number.isFinite(v))) continue;
      if (typeof yaw !== "number" || !Number.isFinite(yaw)) continue;
      const [x, y, z] = pos as number[];
      const f = fitNumber(front, 0);
      const b = fitNumber(back, 0);
      const fitted = !Number.isNaN(f) && !Number.isNaN(b);
      const d = drop === undefined ? ROOM_SEAT_HEIGHT : fitNumber(drop, 1e-3);
      rows.push(x! - ROOM_SEAT_HALF[0], z! - ROOM_SEAT_HALF[1], x! + ROOM_SEAT_HALF[0], z! + ROOM_SEAT_HALF[1], y! + ROOM_SEAT_HEIGHT, yaw);
      const flipDrop = two_sided === true && fitted ? (flip_drop === undefined ? d : fitNumber(flip_drop, 1e-3)) : 0;
      rows.push(fitted ? f : Number.NaN, fitted ? b : Number.NaN, fitted ? d : Number.NaN, Number.isNaN(flipDrop) ? 0 : flipDrop);
    }
    return rows.length ? new Seats(Float64Array.from(rows), rows.length / ROW) : null;
  }

  /**
   * Seats from ready rows `[x0, z0, x1, z1, y, yaw, front, back, drop]` (x0
   * <= x1, z0 <= z1): a dorm's placed furniture (dorm/room.ts `seatRows`),
   * one-way (a chair's or a bed's).
   */
  static fromRows(rows: readonly number[]): Seats {
    const count = Math.floor(rows.length / FIT_ROW);
    const out = new Float64Array(count * ROW);
    for (let i = 0; i < count; i++) out.set(rows.slice(i * FIT_ROW, (i + 1) * FIT_ROW), i * ROW);
    return new Seats(out, count);
  }

  /** The seats of every manifest cell (malformed rows are skipped with one warning per cell). */
  static fromCells(cells: readonly SeatsCell[]): Seats {
    const rows = collect(cells, "seats", false);
    return new Seats(Float64Array.from(rows), rows.length / ROW);
  }

  /** The floor seats (rugs, hearth steps) of every manifest cell: `find` writes yaw NaN (keep the sitter's facing). */
  static floorFromCells(cells: readonly SeatsCell[]): Seats {
    const rows = collect(cells, "floor_seats", true);
    return new Seats(Float64Array.from(rows), rows.length / ROW, FLOOR_SEAT_INSET);
  }

  /**
   * The seat nearest to feet at (x, y, z) within reach, written into `out`
   * (the drawn place: on a fitted seat on the row's centre line, with how
   * far the seat reaches ahead and behind). On a two-sided seat with a
   * `facing` (the sitter's own yaw) the side nearest it: the seat's yaw or
   * the other way round, `ahead` and `behind` swapped. False when there is
   * none: draw the character where it is.
   */
  find(x: number, y: number, z: number, out: SeatPlace, facing = Number.NaN): boolean {
    const r = this.rows;
    let best = -1;
    let bestD = SEAT_REACH * SEAT_REACH;
    for (let i = 0; i < this.count; i++) {
      const o = i * ROW;
      const sy = r[o + 4]!;
      if (sy < y - SEAT_BELOW || sy > y + SEAT_ABOVE) continue;
      const dx = Math.max(r[o]! - x, 0, x - r[o + 2]!);
      const dz = Math.max(r[o + 1]! - z, 0, z - r[o + 3]!);
      const d = dx * dx + dz * dz;
      if (d <= bestD) {
        bestD = d;
        best = o;
      }
    }
    if (best < 0) return false;
    const x0 = r[best]!;
    const z0 = r[best + 1]!;
    const x1 = r[best + 2]!;
    const z1 = r[best + 3]!;
    const yaw = r[best + 5]!;
    let px = inset(x, x0, x1, this.insetBy);
    let pz = inset(z, z0, z1, this.insetBy);
    const front = r[best + 6]!;
    if (!Number.isNaN(front)) {
      // Onto the row's centre line (along the facing through the rectangle's middle).
      const fx = Math.sin(yaw);
      const fz = Math.cos(yaw);
      const t = ((x0 + x1) / 2 - px) * fx + ((z0 + z1) / 2 - pz) * fz;
      px += fx * t;
      pz += fz * t;
    }
    const flipDrop = r[best + 9]!;
    const twoSided = flipDrop > 0;
    const flip = twoSided && Math.cos(facing - yaw) < 0;
    out.x = px;
    out.z = pz;
    out.y = r[best + 4]!;
    out.yaw = flip ? wrapAngle(yaw + Math.PI) : yaw;
    out.ahead = flip ? r[best + 7]! : front;
    out.behind = flip ? front : r[best + 7]!;
    out.drop = flip ? flipDrop : r[best + 8]!;
    out.floor = out.y - r[best + 8]!;
    out.twoSided = twoSided;
    out.seat = best / ROW;
    return true;
  }
}

/** `a` in (-pi, pi]. */
export function wrapAngle(a: number): number {
  const t = Math.PI * 2;
  const w = a - t * Math.floor((a + Math.PI) / t);
  return w === -Math.PI ? Math.PI : w;
}

/** The rows of the cells' `key` lists, `ROW` numbers each (malformed rows skipped with one warning per cell). */
function collect(cells: readonly SeatsCell[], key: "seats" | "floor_seats", floor: boolean): number[] {
  const rows: number[] = [];
  for (const c of cells) {
    const list = c[key];
    if (list === undefined) continue;
    if (!Array.isArray(list)) {
      console.warn(`[castle] ${c.id}: manifest ${key} is not a list`);
      continue;
    }
    let bad = 0;
    for (const r of list) {
      const row = parseRow(r, floor);
      if (row) rows.push(...row);
      else bad++;
    }
    if (bad > 0) console.warn(`[castle] ${c.id}: ${bad} malformed ${key} rows skipped`);
  }
  return rows;
}
