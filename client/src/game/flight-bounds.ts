// Where the flight camera may sit. On a
// broom the camera sits about 9 m back, further than the castle cells'
// camera colliders reach from outside (a kitted cell's collider is its
// inside faces, so from the grounds the camera met the collider behind the
// outer wall's thickness and ended inside the masonry), and instance rooms
// (the Flying class's field) have no colliders at all, so at its edge the
// camera passed into the field's walls and towers.
//
// The zone's own flight boxes are the collider: the camera keeps to the
// open sky a broom may fly in (`airspace` areas of the layout, the same
// boxes the server checks flights against) and out of every no-fly box (the
// castle, the Owl Loft, the hut, the stands, ...), with a margin. The castle
// grounds and the class field both have them; a zone without airspace has
// no flight camera. Pure (no Babylon), unit tested in flight-bounds.test.ts.

export interface Box {
  min: [number, number, number];
  max: [number, number, number];
}

/** How far the camera keeps from a no-fly box (m): less than the broom's own stand-off (0.4 m at the castle's edge). */
export const BOUNDS_MARGIN = 0.25;
/** The march along the camera's ray (m). */
const STEP = 0.2;

interface Area extends Box {
  kind: string;
}

export class FlightBounds {
  constructor(
    private readonly airspace: readonly Box[],
    private readonly noFly: readonly Box[],
  ) {}

  /** From a layout file's `areas` (`airspace` and `no_fly`); null without airspace. */
  static fromAreas(areas: unknown): FlightBounds | null {
    if (!Array.isArray(areas)) return null;
    const ok = (a: unknown): a is Area => {
      const x = a as Area | null;
      return !!x && typeof x.kind === "string" && Array.isArray(x.min) && Array.isArray(x.max) && x.min.length === 3 && x.max.length === 3;
    };
    const list = areas.filter(ok);
    const air = list.filter((a) => a.kind === "airspace");
    if (air.length === 0) return null;
    return new FlightBounds(air, list.filter((a) => a.kind === "no_fly"));
  }

  /**
   * Whether the camera may sit at (x, y, z): over some airspace (its plan
   * and above its floor; the sky over its ceiling is open too) and outside
   * every no-fly box by the margin.
   */
  allows(x: number, y: number, z: number): boolean {
    const m = BOUNDS_MARGIN;
    let inAir = false;
    for (const b of this.airspace) {
      if (x >= b.min[0] && x <= b.max[0] && y >= b.min[1] && z >= b.min[2] && z <= b.max[2]) {
        inAir = true;
        break;
      }
    }
    if (!inAir) return false;
    for (const b of this.noFly) {
      if (x > b.min[0] - m && x < b.max[0] + m && y > b.min[1] - m && y < b.max[1] + m && z > b.min[2] - m && z < b.max[2] + m) return false;
    }
    return true;
  }

  /**
   * How far from (ox, oy, oz) along the unit direction (dx, dy, dz) the
   * camera may sit, up to `want` (m): the last allowed point before the
   * first disallowed one. A start that is not allowed itself (the broom
   * at an edge) is not held against the camera: the march starts from the
   * first allowed point instead, so the camera is never pinned to the rider.
   */
  room(ox: number, oy: number, oz: number, dx: number, dy: number, dz: number, want: number): number {
    let t = 0;
    let free = this.allows(ox, oy, oz);
    let good = 0;
    while (t < want) {
      t = Math.min(want, t + STEP);
      const ok = this.allows(ox + dx * t, oy + dy * t, oz + dz * t);
      if (ok) {
        good = t;
        free = true;
      } else if (free) {
        return good;
      }
    }
    return free ? good : want;
  }
}
