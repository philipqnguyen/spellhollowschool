// Which world cells to load and unload, by distance from the player. Pure so it
// is unit tested; cells.ts does the actual loading.

export interface CellBounds {
  min: [number, number, number];
  max: [number, number, number];
}

/** One entry of client/public/assets/world/manifest.json (docs/architecture.md). */
export interface CellInfo {
  id: string;
  file: string;
  bounds: CellBounds;
  level: number;
  // Castle kit additions (docs/art/castle-kit.md "Manifest fields"), all optional.
  /** The castle_* library materials the cell's file uses (sorted). */
  library?: string[];
  /** Kitted cells: LOD0 triangles and meshes drawn. */
  kit?: { tris: number; draws: number };
  /** The file has `<cell>.collider` (kitted cells). */
  collider?: true;
  /** How many `_lod1` nodes the file has. */
  lod?: number;
  /** The wheel's furthest zoom in this cell (m), when it differs from 16. */
  camera?: { max: number };
  /**
   * Loaded
   * with the castle zone and never unloaded, whatever the radii (the
   * grounds cells, the lake, the backdrop, the foliage). Their meshes carry
   * an LOD1 drawn beyond 60 m (castle/lod.ts), so a far one costs little.
   */
  resident?: true;
  /**
   * Vegetation look A: the cell's plantings of the foliage kit (castle/foliage.ts
   * `FoliageRows`: the species used and one row per planting).
   */
  foliage?: { species: string[]; plants: number[][]; min_lod?: number };
}

export interface Manifest {
  cells: CellInfo[];
  /** The far castle's stand-ins, when the export has them (parsed by castle/hlod.ts). */
  hlod?: unknown;
  /** Vegetation look A: the foliage kit the cells' plantings draw (castle/foliage.ts `FoliageKitInfo`). */
  foliageKit?: unknown;
}

export interface StreamRadii {
  /** Cells closer than this are loaded (m). */
  load: number;
  /** Loaded cells farther than this are disposed (m). Larger than `load`, so a
   * player walking along a boundary doesn't make a cell load and unload repeatedly. */
  unload: number;
}

/** Distance from point `p` to the box `b` (0 inside it). */
export function distanceToBounds(p: readonly [number, number, number], b: CellBounds): number {
  let sq = 0;
  for (let i = 0; i < 3; i++) {
    const v = p[i]!;
    const d = v < b.min[i]! ? b.min[i]! - v : v > b.max[i]! ? v - b.max[i]! : 0;
    sq += d * d;
  }
  return Math.sqrt(sq);
}

export interface CellPlan {
  /** Cells to start loading, nearest first. */
  load: CellInfo[];
  /** Ids of resident (loaded or loading) cells to dispose. */
  unload: string[];
}

/**
 * @param resident ids of cells that are loaded or loading.
 *
 * Cells the manifest marks `resident` are always loaded, in the
 * same nearest-first queue as the cells in reach, and never unloaded.
 */
export function planCells(
  cells: readonly CellInfo[],
  pos: readonly [number, number, number],
  resident: ReadonlySet<string>,
  radii: StreamRadii,
): CellPlan {
  const load: { cell: CellInfo; d: number }[] = [];
  const unload: string[] = [];
  for (const cell of cells) {
    const d = distanceToBounds(pos, cell.bounds);
    if (resident.has(cell.id)) {
      if (d > radii.unload && cell.resident !== true) unload.push(cell.id);
    } else if (d <= radii.load || cell.resident === true) {
      load.push({ cell, d });
    }
  }
  load.sort((a, b) => a.d - b.d);
  return { load: load.map((l) => l.cell), unload };
}

/** Throws a descriptive error if `json` is not a valid manifest. */
export function parseManifest(json: unknown): Manifest {
  const cells = (json as { cells?: unknown } | null)?.cells;
  if (!Array.isArray(cells)) throw new Error("manifest.json has no cells array");
  for (const c of cells as Partial<CellInfo>[]) {
    const ok =
      typeof c.id === "string" &&
      typeof c.file === "string" &&
      Array.isArray(c.bounds?.min) &&
      Array.isArray(c.bounds?.max) &&
      c.bounds.min.length === 3 &&
      c.bounds.max.length === 3;
    if (!ok) throw new Error(`manifest.json has an invalid cell: ${JSON.stringify(c)}`);
  }
  const hlod = (json as { hlod?: unknown }).hlod;
  const foliageKit = (json as { foliage_kit?: unknown }).foliage_kit;
  const out: Manifest = { cells: cells as CellInfo[] };
  if (hlod !== undefined) out.hlod = hlod;
  if (foliageKit !== undefined) out.foliageKit = foliageKit;
  return out;
}
