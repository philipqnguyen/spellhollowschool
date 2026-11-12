// The dueling data the client draws with: every spell's name, colour and
// duel numbers (data/spells.json), the combat constants (data/dueling/rules.json),
// the Dueling Club venue (data/dueling/venues.json) and the loadout slots by
// level (data/progression.json). The same files the server plays by, so a
// cast bar fills in exactly the spell's cast time and a cooldown sweep ends
// when the server will accept the spell again. Pure, unit tested.
//
// Spells to level 20 (protocol v11): projectiles may carry riders (a root,
// a slow, a pull) and fly as a wave; Dissolvo is a `dispel` and the Guardian a
// `ward` (docs/architecture.md "Spells and dueling").

import spellsJson from "../../../../data/spells.json";
import rulesJson from "../../../../data/dueling/rules.json";
import venuesJson from "../../../../data/dueling/venues.json";
import progressionJson from "../../../../data/progression.json";

/** A spell's kind (spells/data.ts's `SpellKind`): what it does in a duel. */
export type SpellKind = "utility" | "attack" | "shield" | "disarm" | "bind" | "slow" | "pull" | "counter" | "ward";

/** Rigora: a hit roots the target (no moving; casting and shielding still work) for `ms`. */
export interface RootInfo {
  ms: number;
}

/** Tardara: a hit slows the target to `pct`% of its move speed for `ms`. */
export interface SlowInfo {
  ms: number;
  pct: number;
}

/** Adveni: a hit pulls the target `distanceM` toward the caster over `moveMs` (never within `gapM`), breaks its cast and staggers it `staggerMs`. */
export interface PullInfo {
  distanceM: number;
  gapM: number;
  moveMs: number;
  staggerMs: number;
}

/** One spell as the duel HUD and VFX need it. */
export interface DuelSpellInfo {
  id: number;
  key: string;
  name: string;
  incantation: string;
  kind: SpellKind;
  /** `#rrggbb` as written in the data. */
  colour: string;
  /** The colour as sRGB 0..1, for the VFX shaders. */
  rgb: [number, number, number];
  /** Learned at this level (at `unlockAt`: a class or venue id). */
  unlockLevel: number;
  unlockAt: string;
  castMs: number;
  cooldownMs: number;
  interruptible: boolean;
  /** Projectiles: flight speed (m/s), radius (m), Poise damage. 0 for the others. */
  speedMps: number;
  radiusM: number;
  damage: number;
  breaksShield: boolean;
  disarmMs: number;
  /** Scutara: how long the shield stands and its reflecting window (ms). */
  shieldMs: number;
  perfectMs: number;
  /** Projectile riders (null: none). */
  root: RootInfo | null;
  slow: SlowInfo | null;
  pull: PullInfo | null;
  /** A wave instead of a bolt: half its width (m); 0 for a bolt. */
  waveHalfWidthM: number;
  /** Dissolvo: at release, clears the caster's root and slow. */
  dispel: boolean;
  /** Guardian: a ward blocking the next spell to land, for this long after release (ms); 0 otherwise. */
  wardMs: number;
}

interface RawSpell {
  id: number;
  key: string;
  name: string;
  incantation: string;
  kind: string;
  colour: string;
  unlock: { level: number; at: string };
  duel?: {
    cast_ms: number;
    cooldown_ms: number;
    interruptible?: boolean;
    effect:
      | {
          kind: "projectile";
          speed_mps: number;
          radius_m: number;
          poise_damage: number;
          breaks_shield?: boolean;
          disarm_ms?: number;
          root?: { ms: number };
          slow?: { ms: number; pct: number };
          pull?: { distance_m: number; gap_m: number; move_ms: number; stagger_ms: number };
          wave?: { half_width_m: number };
        }
      | { kind: "shield"; shield_ms: number; perfect_ms: number }
      | { kind: "dispel" }
      | { kind: "ward"; ward_ms: number };
  };
}

/** `#rrggbb` → sRGB 0..1 (white for anything malformed). */
export function hexRgb(hex: string): [number, number, number] {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return [1, 1, 1];
  const n = parseInt(m[1]!, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function toInfo(s: RawSpell): DuelSpellInfo | null {
  const d = s.duel;
  if (!d) return null;
  const e = d.effect;
  const projectile = e.kind === "projectile" ? e : null;
  const shield = e.kind === "shield" ? e : null;
  const pull = projectile?.pull;
  return {
    id: s.id,
    key: s.key,
    name: s.name,
    incantation: s.incantation,
    kind: s.kind as SpellKind,
    colour: s.colour,
    rgb: hexRgb(s.colour),
    unlockLevel: s.unlock.level,
    unlockAt: s.unlock.at,
    castMs: d.cast_ms,
    cooldownMs: d.cooldown_ms,
    interruptible: d.interruptible ?? false,
    speedMps: projectile?.speed_mps ?? 0,
    radiusM: projectile?.radius_m ?? 0,
    damage: projectile?.poise_damage ?? 0,
    breaksShield: projectile?.breaks_shield ?? false,
    disarmMs: projectile?.disarm_ms ?? 0,
    shieldMs: shield?.shield_ms ?? 0,
    perfectMs: shield?.perfect_ms ?? 0,
    root: projectile?.root ? { ms: projectile.root.ms } : null,
    slow: projectile?.slow ? { ms: projectile.slow.ms, pct: projectile.slow.pct } : null,
    pull: pull ? { distanceM: pull.distance_m, gapM: pull.gap_m, moveMs: pull.move_ms, staggerMs: pull.stagger_ms } : null,
    waveHalfWidthM: projectile?.wave?.half_width_m ?? 0,
    dispel: e.kind === "dispel",
    wardMs: e.kind === "ward" ? e.ward_ms : 0,
  };
}

const RAW_SPELLS = (spellsJson as { spells: RawSpell[] }).spells;

/** Every spell with duel numbers, in file order (the server's default loadout order). */
export const DUEL_SPELLS: readonly DuelSpellInfo[] = RAW_SPELLS.map(toInfo).filter((s): s is DuelSpellInfo => s !== null);
const BY_ID = new Map(DUEL_SPELLS.map((s) => [s.id, s]));
const NAMES = new Map(RAW_SPELLS.map((s) => [s.id, s.name]));

/** The duel numbers of spell `id`, or undefined for a spell that cannot be cast in a duel. */
export function duelSpell(id: number): DuelSpellInfo | undefined {
  return BY_ID.get(id);
}

/** Any spell's display name (dueling or not). */
export function spellName(id: number): string {
  return NAMES.get(id) ?? `Spell ${id}`;
}

/** The first dueling spell with `dispel` (Dissolvo), for "the Unweaving frees you" hints. */
export const DISSOLVO: DuelSpellInfo | undefined = DUEL_SPELLS.find((s) => s.dispel);

export interface DuelRulesData {
  poise_max: number;
  time_limit_ms: number;
  countdown_ms: number;
  tick_ms: number;
  duelist_radius_m: number;
  max_range_m: number;
  move_speed_pct: number;
  return_delay_ms: number;
  challenge_secs: number;
  disconnect_grace_ms?: number;
  rewards: { win_house_points: number; house_point_wins_daily: number; min_fight_ms: number; pair_daily_max: number };
}

export const DUEL_RULES = rulesJson as DuelRulesData;

export interface VenueData {
  id: string;
  name: string;
  area: string;
  arena_layout: string;
  duelists: { pos: [number, number, number]; yaw: number }[];
  ring: { min: [number, number]; max: [number, number] };
  spectators: { pos: [number, number, number]; yaw: number };
  max_spectators: number;
  max_arenas: number;
}

export const VENUES = (venuesJson as unknown as { venues: VenueData[] }).venues;

export function venue(id: string): VenueData | undefined {
  return VENUES.find((v) => v.id === id);
}

/** The venue whose dueling area is `areaId`. */
export function venueByArea(areaId: string): VenueData | undefined {
  return VENUES.find((v) => v.area === areaId);
}

const SLOTS = [...(progressionJson as { loadout_slots: { level: number; slots: number }[] }).loadout_slots].sort((a, b) => a.level - b.level);

/** Loadout slots at `level` (`Progression::loadout_slots`). */
export function loadoutSlots(level: number): number {
  let n = 0;
  for (const s of SLOTS) if (level >= s.level) n = s.slots;
  return n;
}

/** Where the slots open: `[{ level, slots }]`, lowest level first. */
export const SLOT_STEPS: readonly { level: number; slots: number }[] = SLOTS;

/** The most slots any level gives. */
export const MAX_SLOTS = SLOTS.reduce((m, s) => Math.max(m, s.slots), 0);

const KIND_LABELS: Record<SpellKind, string> = {
  utility: "Charm",
  attack: "Attack",
  shield: "Shield",
  disarm: "Disarm",
  bind: "Bind",
  slow: "Hinder",
  pull: "Summon",
  counter: "Counter",
  ward: "Ward",
};

/** The kinds in the order the loadout groups them: defence first, then what hurts, then what hinders. */
export const KIND_ORDER: readonly SpellKind[] = ["shield", "attack", "disarm", "bind", "slow", "pull", "counter", "ward"];

export function kindLabel(kind: SpellKind): string {
  return KIND_LABELS[kind];
}

/** Seconds in words: "6 s", "1.5 s", "0.25 s". */
export function secs(ms: number): string {
  const s = ms / 1000;
  return `${Number.isInteger(s) ? s : Number(s.toFixed(s < 1 ? 2 : 1))} s`;
}

/** One line of what a spell does in a duel, for the loadout and the spell bar's tooltip. */
export function spellSummary(s: DuelSpellInfo): string {
  if (s.kind === "shield") return `Blocks attacks for ${secs(s.shieldMs)}; one landing in its first ${secs(s.perfectMs)} flies back. Cooldown ${secs(s.cooldownMs)}.`;
  if (s.dispel) return `Frees you from a bind and a slow · cast ${secs(s.castMs)} · cooldown ${secs(s.cooldownMs)}`;
  if (s.wardMs) {
    const tail = s.interruptible ? " · a hit interrupts it" : "";
    return `Your guardian stops the next spell that would land, within ${secs(s.wardMs)} · cast ${secs(s.castMs)} · cooldown ${secs(s.cooldownMs)}${tail}`;
  }
  const parts = [`${s.damage} Poise`];
  if (s.root) parts.push(`roots ${secs(s.root.ms)}`);
  if (s.waveHalfWidthM) parts.push(`a wave ${Number((s.waveHalfWidthM * 2).toFixed(1))} m wide`);
  if (s.slow) parts.push(`slows to ${s.slow.pct}% for ${secs(s.slow.ms)}`);
  if (s.pull) parts.push(`pulls ${s.pull.distanceM} m and staggers ${secs(s.pull.staggerMs)}`, "breaks casts");
  parts.push(`cast ${secs(s.castMs)}`, `cooldown ${secs(s.cooldownMs)}`);
  if (s.breaksShield) parts.push("breaks shields");
  if (s.disarmMs) parts.push(`disarms ${secs(s.disarmMs)}`);
  if (s.interruptible) parts.push("a hit interrupts it");
  return parts.join(" · ");
}

/** How to answer each spell (or what it answers): one line for the loadout. */
const COUNTER_HINTS: Record<string, string> = {
  scutara: "The Disarming Charm breaks it; the wave and the pull stop on it too.",
  torpeo: "Sidestep it, or the Shield Charm (raised late, it flies back).",
  dimitto: "Its long cast is a target: a quick Tickling Charm breaks it.",
  titillara: "Weak, but quick enough to break a long cast. The Shield Charm stops it.",
  repulsa: "Slow and heavy: sidestep it, or the Shield Charm.",
  rigora: "The slowest bolt: sidestep it. The Unweaving frees you if it lands.",
  adveni: "Break its 1 s cast, or the Shield Charm (a reflected pull yanks its caster).",
  dissolvo: "Answers the Binding Curse and the Slowing Jinx; not a disarm or a stagger.",
  tardara: "Too wide to sidestep: the Shield Charm, or the Unweaving after.",
  custodio: "Break its 1.5 s cast, or spend the ward with a cheap spell.",
};

const KIND_HINTS: Record<SpellKind, string> = {
  utility: "",
  attack: "Sidestep it, or the Shield Charm.",
  shield: "A shield breaker goes through it.",
  disarm: "Interrupt its cast.",
  bind: "Sidestep it; the Unweaving frees you.",
  slow: "The Shield Charm, or the Unweaving after.",
  pull: "Interrupt its cast, or the Shield Charm.",
  counter: "Answers binds and slows.",
  ward: "Interrupt its cast, or spend the ward with a cheap spell.",
};

/** A one-line counter hint for `s` (loadout cards). */
export function counterHint(s: DuelSpellInfo): string {
  return COUNTER_HINTS[s.key] ?? KIND_HINTS[s.kind];
}
