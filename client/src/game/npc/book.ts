// NPCs in the open castle (docs/architecture.md "NPCs"): the
// roster, the looks, the talking portraits on their walls and every line,
// from data/npcs through sim-wasm's `NpcBook`. Lines are picked here, on
// this client only: nothing an NPC says is ever sent or broadcast.

import rosterText from "../../../../data/npcs/roster.json?raw";
import looksText from "../../../../data/npcs/looks.json?raw";
import portraitsText from "../../../../data/npcs/portraits.json?raw";
import linesText from "../../../../data/npcs/lines.json?raw";
import { NpcBook, type NpcKind, type NpcRulesView, type NpcView, type PortraitView } from "../../wasm/sim_wasm";

/** The roster with its rules and portraits, read once. */
export class Npcs {
  readonly rules: NpcRulesView;
  readonly portraits: readonly PortraitView[];
  private readonly byId = new Map<string, NpcView>();
  private readonly byStation = new Map<string, NpcView>();

  constructor(readonly book: NpcBook) {
    this.rules = book.rules();
    this.portraits = book.portraits();
    for (const n of book.npcs()) {
      this.byId.set(n.id, n);
      if (n.station) this.byStation.set(n.station, n);
    }
  }

  /** From the bundled data/npcs files and the castle's layout text; null (logged) if the data does not load. */
  static load(layoutText: string): Npcs | null {
    try {
      return new Npcs(new NpcBook(rosterText, looksText, portraitsText, linesText, layoutText));
    } catch (e) {
      console.error("[npcs] the castle's residents are unavailable:", e);
      return null;
    }
  }

  /** A roster NPC by id (`EntityInfo.npc`); undefined for professors, duelists and players. */
  npc(id: string | null | undefined): NpcView | undefined {
    return id ? this.byId.get(id) : undefined;
  }

  kind(id: string | null | undefined): NpcKind | null {
    return this.npc(id)?.kind ?? null;
  }

  /** The shopkeeper keeping station interactable `station`. */
  keeperOf(station: string): NpcView | undefined {
    return this.byStation.get(station);
  }

  all(): IterableIterator<NpcView> {
    return this.byId.values();
  }

  dispose(): void {
    this.book.free();
  }
}

/** "Name, Title" or the name alone (the talk card's heading, an NPC's nameplate). */
export function npcLabel(n: Pick<NpcView, "name" | "title">): string {
  return n.title ? `${n.name} · ${n.title}` : n.name;
}
