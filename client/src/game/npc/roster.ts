// Who the characters in view are, beyond their names and looks: each one's
// player level (for the school year on its nameplate) and, for NPCs, the
// data id (`EntityInfo.npc`, a professor of data/classes/<class>.json).
// Kept from snapshots (`entered` brings `level` and `npc`, `left` drops
// them) and `LevelChanged`, versioned like effects so a late cached entry
// never undoes a newer level. Pure, unit tested in npc.test.ts.

import type { EntityInfo } from "../../wasm/sim_wasm";

interface Entry {
  level: number;
  npc: string | null;
  /** `effectsVersion` the level came with (it versions the level since protocol v5). */
  version: number;
}

export class SchoolRoster {
  private readonly byId = new Map<number, Entry>();

  /** A snapshot's entered and left entities. */
  snapshot(entered: readonly EntityInfo[], left: readonly number[]): void {
    for (const id of left) this.byId.delete(id);
    for (const info of entered) {
      const held = this.byId.get(info.id);
      if (held && info.effectsVersion < held.version) continue;
      this.byId.set(info.id, { level: info.level, npc: info.npc ?? null, version: info.effectsVersion });
    }
  }

  /** `LevelChanged`. */
  levelChanged(id: number, level: number, effectsVersion: number): void {
    const held = this.byId.get(id);
    if (held && effectsVersion < held.version) return;
    this.byId.set(id, { level, npc: held?.npc ?? null, version: effectsVersion });
  }

  /** The level shown for `id` (0 when unknown, and for NPCs). */
  level(id: number): number {
    return this.byId.get(id)?.level ?? 0;
  }

  /** The NPC data id of `id`, or null for players and unknown ids. */
  npc(id: number): string | null {
    return this.byId.get(id)?.npc ?? null;
  }

  /** Every NPC in view: id and data id. */
  *npcs(): IterableIterator<[number, string]> {
    for (const [id, e] of this.byId) if (e.npc) yield [id, e.npc];
  }

  /** A zone change or a new connection: the visible set starts over. */
  clear(): void {
    this.byId.clear();
  }
}
