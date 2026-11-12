// What the spellbook shows (ui/spellbook.ts draws it): every spell of
// data/spells.json in its school's group (Charms, Transfiguration, Defence),
// learned or locked, with what it does outside a duel, what it does in one
// (dueling spells), its cast button's words (`castLabel`) and, for spells
// aimed at someone else, how to aim them. Pure, unit tested.

import { duelSpell, spellSummary } from "../game/dueling/content";
import { castLabel, lockedText, mayTargetSelf, needsAlly, placeName, SPELLS, unlockText, type SpellDef, type SpellSchool } from "../game/spells/data";

export interface SpellbookState {
  learned: readonly number[];
  loadout: readonly number[];
  /** Our wand is lit (Lantern Charm's button puts it out). */
  lit: boolean;
  /** Our guardian is out (its button dismisses it). */
  guardian: boolean;
}

export interface SpellRow {
  spell: SpellDef;
  learned: boolean;
  inLoadout: boolean;
  /** The cast button's words; null while locked. */
  button: string | null;
  /** "Taught in Charms class from level 6", or why it is still locked. */
  where: string;
  /** The short tag by the name: "Lv 6 · Charms class". */
  tag: string;
  /** What it does outside a duel. */
  outside: string;
  /** What it does in a duel (dueling spells only). */
  duel: string | null;
  /** How to aim it at someone (spells cast on a friend, party or club member). */
  aim: string | null;
}

export interface SpellGroup {
  id: SpellSchool;
  title: string;
  blurb: string;
  rows: SpellRow[];
}

export const SCHOOLS: readonly { id: SpellSchool; title: string; blurb: string }[] = [
  { id: "charms", title: "Charms", blurb: "Learned in Charms class. Everyday magic; the Summoning Charm can also go in your dueling loadout." },
  { id: "transfiguration", title: "Transfiguration", blurb: "Learned in Transfiguration class. Just for fun: not for duels." },
  {
    id: "defence",
    title: "Defence and dueling",
    blurb: "Learned at the Dueling Club and in Defensive Magic. Every one can go in your dueling loadout; each says what it does outside a duel.",
  },
];

const s1 = (ms: number) => `${Number((ms / 1000).toFixed(1))} s`;

/** What `s` does when cast outside a duel, in words (its numbers from the data). */
export function outsideText(s: SpellDef): string {
  const x = s.social;
  switch (x.kind) {
    case "flourish":
      return "Outside a duel: a harmless flourish of sparks.";
    case "light":
      return "Lights your wand tip to see by.";
    case "levitate":
      return "Lifts a piece of your dorm's furniture into the air.";
    case "recolour":
      return `Turns the robe of a friend, party or club member (or your own) another bright colour for ${s1(x.duration_ms)}.`;
    case "bind":
      return `Outside a duel: a friend, party or club member stiffens on the spot for ${s1(x.duration_ms)}.`;
    case "birds":
      return `Turns a conjured feather into a flutter of ${x.count} small birds that circle over your head and fly off.`;
    case "pull":
      return `Outside a duel: slides a friend, party or club member up to ${x.distance_m} m toward you.`;
    case "dispel":
      return "Outside a duel: ends the wand light, bind, slow and recolour of a friend, party or club member (or your own), or lands a floating piece of furniture.";
    case "reveal":
      return `Secret passages and hidden finds within ${x.radius_m} m shine for you alone, for ${s1(x.duration_ms)}.`;
    case "slow":
      return `Outside a duel: a friend, party or club member walks at ${x.pct}% for ${s1(x.duration_ms)}.`;
    case "guardian":
      return `Your silver guardian appears and follows you for ${s1(x.duration_ms)}; cast again to dismiss it.`;
  }
}

/** How to aim `s`, or null for a spell cast on nobody in particular. */
export function aimText(s: SpellDef): string | null {
  if (needsAlly(s)) return "Select a friend, party or club member first (Tab), then cast.";
  if (mayTargetSelf(s)) return "On your selected friend, party or club member (Tab), or on yourself.";
  return null;
}

/** One row of the spellbook. */
export function spellRow(s: SpellDef, level: number, st: SpellbookState): SpellRow {
  const learned = st.learned.includes(s.id);
  const d = duelSpell(s.id);
  return {
    spell: s,
    learned,
    inLoadout: st.loadout.includes(s.id),
    button: learned ? castLabel(s, { lit: st.lit, guardian: st.guardian }) : null,
    where: learned ? unlockText(s) : lockedText(s, level),
    tag: `Lv ${s.unlock.level} · ${placeName(s.unlock.at)}`,
    outside: outsideText(s),
    duel: d ? `In a duel: ${spellSummary(d)}` : null,
    aim: aimText(s),
  };
}

/** Every spell by school, in file order (the school order of `SCHOOLS`; empty schools left out). */
export function spellbookGroups(level: number, st: SpellbookState, spells: readonly SpellDef[] = SPELLS): SpellGroup[] {
  return SCHOOLS.map((school) => ({ ...school, rows: spells.filter((s) => s.school === school.id).map((s) => spellRow(s, level, st)) })).filter((g) => g.rows.length > 0);
}
