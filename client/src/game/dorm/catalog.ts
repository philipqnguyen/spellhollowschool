// Furniture entries of the item catalog (content/catalog.ts, which imports
// data/items/catalog.json at build time) in the shape placement works with,
// and the decorating list's categories.

import { itemDef } from "../../content/catalog";
import type { FurnitureSeat, FurnitureShape } from "./placement";

export interface FurnitureDef extends FurnitureShape {
  id: number;
  name: string;
  /** Asset key: `client/public/assets/<asset>.glb`. */
  asset: string;
  tags: readonly string[];
  /** Where a sitter is drawn on the placed piece (chairs, stools, armchairs, a bed's sides). */
  seats: readonly FurnitureSeat[];
  /** Whether the piece offers a top or shelves for table items (`furnitureTopAreas`). */
  hasTops: boolean;
  /** The catalog's `furniture` block as written, for `furnitureTopAreas` (sim-wasm). */
  raw: object;
  category: FurnitureCategory;
}

/** The decorating list's filter (derived from the catalog's tags and surface). */
export type FurnitureCategory = "seating" | "tables" | "storage" | "beds" | "decor" | "wall" | "rugs" | "lighting";

export const CATEGORY_LABELS: Record<FurnitureCategory, string> = {
  seating: "Seating",
  tables: "Tables",
  storage: "Storage",
  beds: "Beds",
  decor: "Decor and tabletop",
  wall: "Wall",
  rugs: "Rugs",
  lighting: "Lighting",
};

/** Tag to category, first match wins. */
const TAG_CATEGORY: readonly [string, FurnitureCategory][] = [
  ["bed", "beds"],
  ["seating", "seating"],
  ["table", "tables"],
  ["storage", "storage"],
  ["rug", "rugs"],
  ["light", "lighting"],
  ["wall", "wall"],
];

/** A piece's category: walls by surface, then its tags; anything else (plants, cages, cauldrons) is decor. */
export function categoryOf(surface: FurnitureShape["surface"], tags: readonly string[]): FurnitureCategory {
  if (surface === "wall") return "wall";
  for (const [tag, cat] of TAG_CATEGORY) if (tags.includes(tag)) return cat;
  return "decor";
}

const cache = new Map<number, FurnitureDef | null>();

/** The catalog's furniture entry for `itemDef`, or undefined for clothing and unknown ids. */
export function furnitureDef(id: number): FurnitureDef | undefined {
  let def = cache.get(id);
  if (def === undefined) {
    const item = itemDef(id);
    const f = item?.kind === "furniture" ? item.furniture : undefined;
    def =
      item && f
        ? {
            id,
            name: item.name,
            asset: item.asset,
            tags: item.tags,
            surface: f.surface,
            footprint: [f.footprint[0], f.footprint[1]],
            height: f.height,
            top: f.top,
            seats: f.seats ?? [],
            hasTops: f.top !== undefined || (f.shelves?.length ?? 0) > 0,
            raw: f,
            category: categoryOf(f.surface, item.tags),
          }
        : null;
    cache.set(id, def);
  }
  return def ?? undefined;
}
