// Cell visibility (docs/art/castle-kit.md "Cell visibility (`sees`)"): the export writes
// `sees` on every indoor cell of the world manifest (the cells seen from it
// through up to two doors or open joins, the stacked levels of a shaft, and
// the facades of a courtyard next door); the client disables resident cells
// outside the set of the cell the player stands in. Disabled, not unloaded:
// streaming, residency and the castle library's reference counts do not
// change, so a cell comes back in the same frame its set does.
//
// - The set is recomputed on place updates (game.ts, four times a second)
//   and when a zone is shown, never per frame. The current cell is the cell
//   of the surface under the player, as for the zoom limit.
// - On a door surface the set is the union of `sees` over the cells of the
//   surfaces the door joins, so both sides and their next doors show while
//   the player stands in the passage. Teleports and respawns need nothing
//   special: the set is the new surface's, at once.
// - Within DOOR_NEAR of a door of the surface (castle kit M2 review): the
//   door's union too. The camera trails the player by up to 24 m, so a
//   player a step short of a door, facing it, can see through it and down
//   the next cell's open joins (from `ff_corridor_north` through the Marble
//   Arch down the hall's east stairwell along `basement_east` to
//   `common_room_house_b`'s door: three hops from the corridor), and a
//   runner can cross a short door surface between two place updates.
// - No surface under the player (a jump, a fall across a stairwell) or an
//   unknown surface: the last set stays. No set yet, a cell without `sees`
//   (outdoor cells see everything) or `?sees=off`: everything is visible.
//
// Every set is built once, at construction, and shared: switching sets
// allocates nothing.

/** The manifest fields this reads. */
export interface SeesCell {
  id: string;
  sees?: unknown;
}

/** The layout fields this reads (data/world/layout.json surfaces). */
export interface SeesSurface {
  id: string;
  kind: string;
  cell?: string;
  min: [number, number];
  max: [number, number];
  y?: number;
  y_top?: number;
  ceiling?: number;
  /** A grounds heightfield (its samples' range is the floor's). */
  heights?: { h: readonly number[] };
}

/** How far a surface's floor may sit from a door's and still be joined to it (m): a step, with slack. */
const JOIN_HEIGHT = 0.5;
/** How near a door (m, in plan, to its rectangle) the door's union set already applies. */
export const DOOR_NEAR = 3;
const EPS = 1e-3;

/** `?sees=off` turns culling off (measurements). */
export function seesDisabledByUrl(search: string | undefined = typeof location !== "undefined" ? location.search : undefined): boolean {
  if (!search) return false;
  return new URLSearchParams(search).get("sees") === "off";
}

function floorRange(s: SeesSurface): [number, number] {
  if (s.heights && s.heights.h.length > 0) return [Math.min(...s.heights.h), Math.max(...s.heights.h)];
  const y = s.y ?? 0;
  const t = s.y_top ?? y;
  return [Math.min(y, t), Math.max(y, t)];
}

/** Whether `a` and `b` meet along an edge or overlap (positive length), at heights a step apart. */
export function surfacesJoin(a: SeesSurface, b: SeesSurface): boolean {
  const x0 = Math.max(a.min[0], b.min[0]);
  const x1 = Math.min(a.max[0], b.max[0]);
  const z0 = Math.max(a.min[1], b.min[1]);
  const z1 = Math.min(a.max[1], b.max[1]);
  if (x1 < x0 - EPS || z1 < z0 - EPS) return false;
  if (x1 - x0 <= EPS && z1 - z0 <= EPS) return false; // corner touch only
  const [alo, ahi] = floorRange(a);
  const [blo, bhi] = floorRange(b);
  return alo <= bhi + JOIN_HEIGHT && blo <= ahi + JOIN_HEIGHT;
}

export class CellVisibility {
  /** Cell -> its `sees` (null: sees everything). Cells missing from the map are unknown. */
  private readonly byCell = new Map<string, ReadonlySet<string> | null>();
  /** Surface -> the set shown while standing on it (null: everything). */
  private readonly bySurface = new Map<string, ReadonlySet<string> | null>();
  /** Non-door surface -> the doors it joins whose union differs from its own set (DOOR_NEAR). */
  private readonly nearDoors = new Map<string, { x0: number; z0: number; x1: number; z1: number; set: ReadonlySet<string> | null }[]>();
  private current: ReadonlySet<string> | null = null;
  private placed = false;
  private version = 0;
  readonly enabled: boolean;

  constructor(cells: readonly SeesCell[], surfaces: readonly SeesSurface[], opts: { enabled?: boolean } = {}) {
    this.enabled = opts.enabled ?? !seesDisabledByUrl();
    for (const c of cells) {
      const sees = Array.isArray(c.sees) ? c.sees.filter((s): s is string => typeof s === "string") : null;
      this.byCell.set(c.id, sees ? new Set([c.id, ...sees]) : null);
    }
    for (const s of surfaces) {
      if (!s.cell || !this.byCell.has(s.cell)) continue;
      if (s.kind !== "door") {
        this.bySurface.set(s.id, this.byCell.get(s.cell)!);
        continue;
      }
      // A door: the union over the cells of the surfaces it joins (its own cell included).
      const cellsJoined = new Set<string>([s.cell]);
      for (const o of surfaces) if (o !== s && o.cell && surfacesJoin(s, o)) cellsJoined.add(o.cell);
      let union: Set<string> | null = new Set<string>();
      for (const c of cellsJoined) {
        const seen = this.byCell.get(c);
        if (seen === undefined) continue; // a cell the manifest does not list
        if (seen === null) {
          union = null; // an outdoor cell sees everything
          break;
        }
        for (const id of seen) union.add(id);
      }
      this.bySurface.set(s.id, union);
    }
    for (const s of surfaces) {
      if (s.kind === "door" || !this.bySurface.has(s.id)) continue;
      const own = this.bySurface.get(s.id)!;
      for (const d of surfaces) {
        if (d.kind !== "door" || !this.bySurface.has(d.id) || !surfacesJoin(s, d)) continue;
        const set = this.bySurface.get(d.id)!;
        if (set === own) continue;
        let list = this.nearDoors.get(s.id);
        if (!list) this.nearDoors.set(s.id, (list = []));
        list.push({ x0: d.min[0], z0: d.min[1], x1: d.max[0], z1: d.max[1], set });
      }
    }
  }

  /** The cells shown now, or null when every resident cell is. */
  get visible(): ReadonlySet<string> | null {
    return this.enabled ? this.current : null;
  }

  /** Bumped whenever `visible` changes (for callers caching per set). */
  get generation(): number {
    return this.version;
  }

  isVisible(cell: string): boolean {
    const v = this.visible;
    return v === null || v.has(cell);
  }

  /** The set shown on `surfaceId` (null: everything), or undefined for an unknown surface. */
  setFor(surfaceId: string): ReadonlySet<string> | null | undefined {
    return this.bySurface.get(surfaceId);
  }

  /**
   * A place update: the surface under the player (null when there is none)
   * and, when known, the player's plan position (for DOOR_NEAR).
   * Returns true when the visible set changed.
   */
  place(surfaceId: string | null | undefined, x?: number, z?: number): boolean {
    if (!surfaceId) return false; // in the air: keep the last set
    let next = this.bySurface.get(surfaceId);
    if (next === undefined) return false; // a surface of no known cell
    const doors = x === undefined || z === undefined ? undefined : this.nearDoors.get(surfaceId);
    if (doors) {
      for (const d of doors) {
        const dx = Math.max(d.x0 - x!, 0, x! - d.x1);
        const dz = Math.max(d.z0 - z!, 0, z! - d.z1);
        if (dx * dx + dz * dz <= DOOR_NEAR * DOOR_NEAR) {
          next = d.set;
          break;
        }
      }
    }
    this.placed = true;
    if (next === this.current) return false;
    this.current = next;
    this.version++;
    return this.enabled;
  }

  /** Forget the set (a zone change): everything shows until the next place update. */
  reset(): boolean {
    const changed = this.current !== null;
    this.current = null;
    this.placed = false;
    if (changed) this.version++;
    return changed && this.enabled;
  }

  /** Whether a place update has chosen a set since the last reset. */
  get hasPlace(): boolean {
    return this.placed;
  }
}
