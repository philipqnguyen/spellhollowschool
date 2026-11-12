// The castle's dueling areas (`Layout.areas`, kind `dueling_club`): named
// boxes where players may challenge each other and watch duels. The server
// decides (`World::area_at`); the client uses the same boxes to show the
// Dueling Club badge, offer "Challenge to a duel" only where it can work, and
// open the list of duels to watch. `SimWorld` does not expose areas, so they
// are read from the layout file itself (the browser has it cached from the
// world load).

import layoutUrl from "../../../../data/world/layout.json?url";
import type { Vec3 } from "../../wasm/sim_wasm";

export interface DuelArea {
  id: string;
  kind: string;
  name: string;
  min: Vec3;
  max: Vec3;
}

/** `Area::contains`: inside the box, edges included. */
export function areaContains(a: Pick<DuelArea, "min" | "max">, x: number, y: number, z: number): boolean {
  return x >= a.min[0] && x <= a.max[0] && y >= a.min[1] && y <= a.max[1] && z >= a.min[2] && z <= a.max[2];
}

/** The dueling area at (x, y, z), if any. */
export function areaAt(areas: readonly DuelArea[], x: number, y: number, z: number): DuelArea | null {
  for (const a of areas) if (areaContains(a, x, y, z)) return a;
  return null;
}

/** The dueling areas of a layout file's JSON (other kinds are skipped). */
export function duelAreas(layout: unknown): DuelArea[] {
  const areas = (layout as { areas?: DuelArea[] } | null)?.areas;
  return Array.isArray(areas) ? areas.filter((a) => a.kind === "dueling_club") : [];
}

/** Loads the castle's dueling areas (none if the file cannot be read: the badge just stays hidden). */
export async function loadDuelAreas(): Promise<DuelArea[]> {
  try {
    const r = await fetch(layoutUrl);
    if (!r.ok) return [];
    return duelAreas(await r.json());
  } catch {
    return [];
  }
}
