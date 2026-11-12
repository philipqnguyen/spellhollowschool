// Spells and wand gestures as the client reads them: data/spells.json and
// data/classes/gestures.json, the files sim-core's `Spells` and `Gestures`
// read (names, incantations and colours live only there). The server
// decides what is learned and what a cast does; this holds the words,
// colours, cooldowns and shapes the client shows. Pure, unit tested in
// spells.test.ts.
//
// Spells L20 (CORE, protocol v11): the eight new spells' kinds, schools and
// social shapes, and the casting words (`castLabel`) and target rules
// (`needsFriend`, `mayTargetSelf`) every panel shares. Frozen after CORE;
// SOCIAL may append helpers at the end.

import spellsJson from "../../../../data/spells.json";
import gesturesJson from "../../../../data/classes/gestures.json";
import scheduleJson from "../../../../data/classes/schedule.json";
import venuesJson from "../../../../data/dueling/venues.json";

export type SpellKind = "utility" | "attack" | "shield" | "disarm" | "bind" | "slow" | "pull" | "counter" | "ward";
export type SpellSchool = "charms" | "transfiguration" | "defence";

export type SocialSpell =
  | { kind: "flourish"; cooldown_ms: number; range_m: number }
  | { kind: "light"; cooldown_ms: number; radius_m: number; intensity: number }
  | { kind: "levitate"; cooldown_ms: number; range_m: number; duration_ms: number; height_m: number; bob_m: number; bob_hz: number }
  /** Tinctura: a friend's (or your own) robe shows one of `colours` (palette indices) for `duration_ms`. */
  | { kind: "recolour"; cooldown_ms: number; range_m: number; duration_ms: number; colours: number[] }
  /** Rigora outside duels: a friend stiffens for `duration_ms`; immune to hindrances for `immune_ms`. */
  | { kind: "bind"; cooldown_ms: number; range_m: number; duration_ms: number; immune_ms: number }
  /** Volucra: `count` birds circle the caster and fly off over `duration_ms`. */
  | { kind: "birds"; cooldown_ms: number; range_m: number; count: number; duration_ms: number }
  /** Adveni outside duels: a friend slides up to `distance_m` toward you (at least `min_m`, never within `gap_m`). */
  | { kind: "pull"; cooldown_ms: number; range_m: number; distance_m: number; min_m: number; gap_m: number; move_ms: number; hold_ms: number; immune_ms: number }
  /** Dissolvo outside duels: ends a friend's (or your own) Lucerna, bind, slow and recolour, or lands a floating piece. */
  | { kind: "dispel"; cooldown_ms: number; range_m: number }
  /** Aperio: passages and find spots near you shine for you alone. */
  | { kind: "reveal"; cooldown_ms: number; radius_m: number; height_m: number; duration_ms: number }
  /** Tardara outside duels: a friend walks at `pct`% for `duration_ms`. */
  | { kind: "slow"; cooldown_ms: number; range_m: number; duration_ms: number; pct: number; immune_ms: number }
  /** Custodio outside duels: your silver animal follows you for `duration_ms`. */
  | { kind: "guardian"; cooldown_ms: number; range_m: number; duration_ms: number };

export interface SpellDef {
  id: number;
  key: string;
  name: string;
  incantation: string;
  school: SpellSchool;
  kind: SpellKind;
  unlock: { level: number; at: string };
  /** "#rrggbb". */
  colour: string;
  /** A gesture key (gestures.json). */
  gesture: string;
  social: SocialSpell;
  /** Present on dueling spells. */
  duel?: unknown;
}

export interface GestureDef {
  id: number;
  key: string;
  name: string;
  min_ms: number;
  max_ms: number;
  /** The template on the -100..100 pad, y up, in stroke order. */
  points: [number, number][];
}

export const SPELLS: readonly SpellDef[] = spellsJson.spells as SpellDef[];
export const GESTURES: readonly GestureDef[] = gesturesJson.gestures as GestureDef[];
/** The trace scorer's numbers the client needs to know (window rules, sample limits). */
export const SCORING = gesturesJson.scoring;

/** Lucerna and Elevara: the two spells the client casts by key. */
export const WAND_LIGHT = SPELLS.find((s) => s.social.kind === "light")!;
export const LEVITATION = SPELLS.find((s) => s.social.kind === "levitate")!;

export function spellById(id: number): SpellDef | undefined {
  return SPELLS.find((s) => s.id === id);
}

/** The spell a player named (`/cast ...`): by name, incantation or key, ignoring case, spaces and the "!"; a unique prefix will do. */
export function findSpell(query: string): SpellDef | undefined {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  const q = norm(query);
  if (!q) return undefined;
  const names = (s: SpellDef) => [norm(s.name), norm(s.incantation), norm(s.key)];
  const exact = SPELLS.find((s) => names(s).includes(q));
  if (exact) return exact;
  const prefixed = SPELLS.filter((s) => names(s).some((n) => n.startsWith(q)));
  return prefixed.length === 1 ? prefixed[0] : undefined;
}

export function spellName(id: number): string {
  return spellById(id)?.name ?? "a spell";
}

export function gestureById(id: number): GestureDef | undefined {
  return GESTURES.find((g) => g.id === id);
}

export function gestureByKey(key: string): GestureDef | undefined {
  return GESTURES.find((g) => g.key === key);
}

/** Whether `s` can go in the dueling loadout. */
export function isDueling(s: SpellDef): boolean {
  return s.duel !== undefined;
}

/** A spell's colour as sRGB 0..1 (white when the data is malformed). */
export function spellRgb(s: SpellDef | undefined): [number, number, number] {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(s?.colour ?? "");
  if (!m) return [1, 1, 1];
  return [parseInt(m[1]!, 16) / 255, parseInt(m[2]!, 16) / 255, parseInt(m[3]!, 16) / 255];
}

/**
 * The places that teach spells (`unlock.at`): a class of the timetable
 * ("Charms class") or a dueling venue ("the Dueling Club"), named from their
 * data files.
 */
const CLASS_NAMES = new Map(scheduleJson.classes.map((c) => [c.id, c.name]));
const VENUE_NAMES = new Map(venuesJson.venues.map((v) => [v.id, v.name]));

export function placeName(at: string): string {
  const cls = CLASS_NAMES.get(at);
  if (cls) return `${cls} class`;
  const venue = VENUE_NAMES.get(at);
  return venue ? `the ${venue}` : at.replace(/_/g, " ");
}

/** Whether spells taught at `at` come from a dueling venue (rather than a class). */
export function isVenue(at: string): boolean {
  return VENUE_NAMES.has(at);
}

/** "Taught in Charms class from level 2". */
export function unlockText(s: SpellDef): string {
  return `Taught in ${placeName(s.unlock.at)}${s.unlock.level > 1 ? ` from level ${s.unlock.level}` : ""}`;
}

/** Why `s` is still locked for a character at `level`, in words. */
export function lockedText(s: SpellDef, level: number): string {
  if (level < s.unlock.level) return `Reach level ${s.unlock.level}, then learn it in ${placeName(s.unlock.at)}.`;
  return isVenue(s.unlock.at) ? `Visit ${placeName(s.unlock.at)} to learn it.` : `Attend ${placeName(s.unlock.at)} to learn it.`;
}

/**
 * Spells L20: the words on a spell's cast button. `state.lit`: our wand is
 * lit (Lucerna puts it out); `state.guardian`: our guardian is out (casting
 * again dismisses it).
 */
export function castLabel(s: SpellDef, state: { lit: boolean; guardian: boolean }): string {
  switch (s.social.kind) {
    case "light":
      return state.lit ? "Put out (L)" : "Light (L)";
    case "levitate":
      return "Levitate… (V)";
    case "flourish":
      return "Flourish";
    case "recolour":
      return "Recolour";
    case "bind":
      return "Bind";
    case "birds":
      return "Release birds";
    case "pull":
      return "Summon";
    case "dispel":
      return "End spells";
    case "reveal":
      return "Reveal";
    case "slow":
      return "Slow";
    case "guardian":
      return state.guardian ? "Dismiss guardian" : "Summon guardian";
  }
}

/** Spells L20: spells that must be aimed at a selected friend (the hindering ones). */
export function needsFriend(s: SpellDef): boolean {
  const k = s.social.kind;
  return k === "bind" || k === "slow" || k === "pull";
}

/** Spells L20: spells that fall back to yourself with nothing selected. */
export function mayTargetSelf(s: SpellDef): boolean {
  const k = s.social.kind;
  return k === "recolour" || k === "dispel";
}

// ---- Spells L20 (SOCIAL): helpers.

/** Spells that can be aimed at a friend (the player card's "Cast on" row): the hindering ones, Tinctura and Dissolvo. */
export function targetsFriend(s: SpellDef): boolean {
  return needsFriend(s) || mayTargetSelf(s);
}
