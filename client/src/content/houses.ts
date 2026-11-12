// House display data. Names and colours live in houses.json (CONTRIBUTING.md:
// IP names stay in data files, never in code identifiers).
import houses from "./houses.json";

export interface HouseInfo {
  /** Lower-case id, as `decodeServer` spells it (`"skytalon"`). */
  id: string;
  name: string;
  /** Primary colour, CSS hex. */
  colour: string;
  /** Secondary colour, CSS hex. */
  accent: string;
  virtues: string;
  /** The crest's charge, drawn by the creator: `lion`, `badger`, `eagle` or `serpent`. */
  emblem?: string;
  /** A line in the spirit of the house, under its crest card. */
  motto?: string;
}

export const HOUSES: readonly HouseInfo[] = houses;

const FALLBACK: HouseInfo = { id: "", name: "", colour: "#8a7a5c", accent: "#d8c9a3", virtues: "" };

/**
 * Looks a house up by any spelling of its id: the HTTP API sends `"Skytalon"`,
 * the wasm decoder `"skytalon"`. Unknown ids get a neutral parchment colour.
 */
export function houseInfo(id: string): HouseInfo {
  const key = id.toLowerCase();
  return HOUSES.find((h) => h.id === key) ?? { ...FALLBACK, id: key, name: id };
}

/** The spelling the HTTP API expects in `POST /api/characters` (`"Skytalon"`). */
export function houseWireName(id: string): string {
  return id.charAt(0).toUpperCase() + id.slice(1).toLowerCase();
}

/** `#rrggbb` → linear-ish `[r, g, b]` in 0..1 (sRGB values; good enough for tints). */
export function hexToRgb(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.replace("#", ""), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}
