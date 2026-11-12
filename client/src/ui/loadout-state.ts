// The loadout editor's draft: which learned dueling spells sit in which
// slot (slot order is the key order in a duel, 1-5), checked with the
// server's rule (`Spells::check_loadout`: learned dueling spells, no
// repeats, at most the level's slots) before `PUT /api/loadout`. The
// spellbook (`Spellbook` after Welcome and on every change) is the truth; a
// draft being edited keeps the player's choices when a new spellbook
// arrives, minus anything no longer allowed. Pure, unit tested.

import { DUEL_SPELLS, duelSpell, KIND_ORDER, kindLabel, spellName, type DuelSpellInfo, type SpellKind } from "../game/dueling/content";
import { isVenue, placeName } from "../game/spells/data";

/** Spells of one kind, for the loadout's grouped list. */
export interface KindGroup {
  kind: SpellKind;
  label: string;
  spells: DuelSpellInfo[];
}

/** `spells` grouped by kind in `KIND_ORDER` (data order within a kind; empty kinds left out). */
export function byKind(spells: readonly DuelSpellInfo[]): KindGroup[] {
  const groups: KindGroup[] = [];
  for (const kind of KIND_ORDER) {
    const of = spells.filter((s) => s.kind === kind);
    if (of.length) groups.push({ kind, label: kindLabel(kind), spells: of });
  }
  // A kind the order does not know (new data) still shows, last.
  const rest = spells.filter((s) => !KIND_ORDER.includes(s.kind));
  if (rest.length) groups.push({ kind: rest[0]!.kind, label: "Other", spells: rest });
  return groups;
}

/**
 * Where a dueling spell is taught, for "Taught ... from level N": "at the Dueling Club", or a
 * class by its name ("in Defensive Magic").
 */
export function taughtWhere(unlockAt: string): string {
  return isVenue(unlockAt) ? `at ${placeName(unlockAt)}` : `in ${placeName(unlockAt).replace(/ class$/, "")}`;
}

export interface Spellbook {
  learned: readonly number[];
  loadout: readonly number[];
  slots: number;
}

const same = (a: readonly number[], b: readonly number[]) => a.length === b.length && a.every((x, i) => x === b[i]);

export class LoadoutDraft {
  learned: readonly number[] = [];
  /** The loadout the server has. */
  saved: readonly number[] = [];
  slots = 0;
  /** The loadout being edited, in slot order. */
  spells: number[] = [];

  constructor(book?: Spellbook) {
    if (book) this.adopt(book);
  }

  /** A new spellbook: taken as is when nothing is being edited; otherwise the edit is kept where still allowed. */
  adopt(book: Spellbook): void {
    const editing = this.dirty;
    this.learned = [...book.learned];
    this.saved = [...book.loadout];
    this.slots = book.slots;
    if (!editing) this.spells = [...book.loadout];
    else this.spells = this.spells.filter((s, i, all) => this.learned.includes(s) && duelSpell(s) && all.indexOf(s) === i).slice(0, this.slots);
  }

  get dirty(): boolean {
    return !same(this.spells, this.saved);
  }

  /** The dueling spells we know, in data order. */
  known(): DuelSpellInfo[] {
    return DUEL_SPELLS.filter((s) => this.learned.includes(s.id));
  }

  /** Dueling spells not learned yet (shown greyed with where they are taught). */
  unknown(): DuelSpellInfo[] {
    return DUEL_SPELLS.filter((s) => !this.learned.includes(s.id));
  }

  has(spell: number): boolean {
    return this.spells.includes(spell);
  }

  /** Puts `spell` in the first free slot. Returns why not, or null. */
  add(spell: number): string | null {
    if (this.has(spell)) return `${spellName(spell)} is already in your loadout.`;
    if (!duelSpell(spell)) return `${spellName(spell)} can't be cast in a duel.`;
    if (!this.learned.includes(spell)) return `You haven't learned ${spellName(spell)} yet.`;
    if (this.spells.length >= this.slots) return `Your ${this.slots} slots are full: take a spell out first.`;
    this.spells.push(spell);
    return null;
  }

  remove(spell: number): void {
    this.spells = this.spells.filter((s) => s !== spell);
  }

  /** Adds or removes `spell`. Returns why not, or null. */
  toggle(spell: number): string | null {
    if (this.has(spell)) {
      this.remove(spell);
      return null;
    }
    return this.add(spell);
  }

  /** Moves the spell in slot `index` by `delta` slots (its key changes). */
  move(index: number, delta: number): void {
    const to = index + delta;
    if (index < 0 || index >= this.spells.length || to < 0 || to >= this.spells.length) return;
    const [s] = this.spells.splice(index, 1);
    this.spells.splice(to, 0, s!);
  }

  /** Back to what the server has. */
  revert(): void {
    this.spells = [...this.saved];
  }

  /** What the server would refuse (`invalid_loadout`), or null. */
  problem(): string | null {
    if (this.spells.length > this.slots) return `At most ${this.slots} spells at your level.`;
    for (let k = 0; k < this.spells.length; k++) {
      const id = this.spells[k]!;
      if (this.spells.indexOf(id) !== k) return `${spellName(id)} is in your loadout twice.`;
      if (!duelSpell(id)) return `${spellName(id)} can't be cast in a duel.`;
      if (!this.learned.includes(id)) return `You haven't learned ${spellName(id)} yet.`;
    }
    return null;
  }

  /** A warning that does not stop saving. */
  warning(): string | null {
    if (this.spells.length === 0) return "With no spells in your loadout you can't accept or make challenges.";
    if (!this.spells.some((s) => duelSpell(s)?.kind === "shield") && this.known().some((s) => s.kind === "shield")) return "No shield: nothing will stop their attacks.";
    return null;
  }
}
