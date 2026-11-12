// Every written piece (music/scores/<name>.ts, one per music and stinger id,
// each a default export), by id.

import type { MusicId, StingerId } from "../catalogue";
import type { Score } from "./score";

const modules = import.meta.glob<Score>("./scores/*.ts", { eager: true, import: "default" });

export const SCORES: ReadonlyMap<string, Score> = new Map(Object.values(modules).map((s) => [s.id, s]));

export function scoreFor(id: MusicId | StingerId): Score | undefined {
  return SCORES.get(id);
}
