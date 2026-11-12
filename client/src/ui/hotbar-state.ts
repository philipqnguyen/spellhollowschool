// The social hotbar (protocol v17; docs/architecture.md "Spells and
// dueling"): ten slots at the bottom of the HUD, keys 1-9 then 0, each a
// learned spell that does something outside a duel (every spell of
// data/spells.json but the flourishes) or empty. The server stores the
// layout with the character and is the judge of it (`SetHotbar`, answered
// by `Hotbar`; spells just learned arrive already placed in `Spellbook`);
// this holds the rules the view needs to rearrange it before saying so,
// and what each slot shows. Pure, unit tested in hotbar-state.test.ts;
// ui/hotbar.ts draws it.

import { SPELLS, type SpellDef } from "../game/spells/data";
import { aimText, SCHOOLS } from "./spellbook-state";

/** Slots on the bar (`protocol::HOTBAR_SLOTS`). */
export const HOTBAR_SLOTS = 10;

/** A layout: one spell id or null per slot, in key order. */
export type Slots = readonly (number | null)[];

/** Whether `s` may go on the hotbar: it does something outside a duel (a flourish does not). */
export function isHotbarSpell(s: SpellDef): boolean {
  return s.social.kind !== "flourish";
}

/** The spells the hotbar can hold, in spellbook order (by school, file order within). */
export function hotbarSpells(spells: readonly SpellDef[] = SPELLS): SpellDef[] {
  return SCHOOLS.flatMap((school) => spells.filter((s) => s.school === school.id && isHotbarSpell(s)));
}

/** Why a spell can't be dragged onto the bar, or null when it can. */
export function dragRefusal(s: SpellDef, learned: readonly number[]): string | null {
  if (!isHotbarSpell(s)) return "Outside a duel this is only a flourish: it doesn't go on the hotbar.";
  if (!learned.includes(s.id)) return "Learn it first, then drag it onto the hotbar.";
  return null;
}

/** `slots` as exactly `HOTBAR_SLOTS` slots (missing ones empty, extra ones dropped). */
export function normalize(slots: Slots): (number | null)[] {
  return Array.from({ length: HOTBAR_SLOTS }, (_, i) => slots[i] ?? null);
}

/** Whether two layouts are the same. */
export function sameSlots(a: Slots, b: Slots): boolean {
  const x = normalize(a);
  const y = normalize(b);
  return x.every((v, i) => v === y[i]);
}

/** The layout of a character that never set one: its learned hotbar spells in spellbook order. */
export function defaultHotbar(learned: readonly number[], spells: readonly SpellDef[] = SPELLS): (number | null)[] {
  return normalize(hotbarSpells(spells).filter((s) => learned.includes(s.id)).map((s) => s.id));
}

/** Spells just learned go into the first empty slots (hotbar spells not on it yet, while there is room); as the server does. */
export function autoAdd(slots: Slots, learnedNow: readonly number[], spells: readonly SpellDef[] = SPELLS): (number | null)[] {
  const out = normalize(slots);
  for (const id of learnedNow) {
    const s = spells.find((x) => x.id === id);
    if (!s || !isHotbarSpell(s) || out.includes(id)) continue;
    const free = out.indexOf(null);
    if (free < 0) break;
    out[free] = id;
  }
  return out;
}

/** Dragging slot `from` onto slot `to`: an empty slot takes it, an occupied one swaps with it. */
export function moveSlot(slots: Slots, from: number, to: number): (number | null)[] {
  const out = normalize(slots);
  if (from === to || !inRange(from) || !inRange(to)) return out;
  [out[from], out[to]] = [out[to]!, out[from]!];
  return out;
}

/** Dropping spell `id` (from the spellbook) on slot `to`: it replaces what is there, and leaves the slot it was in (a spell is on the bar once). */
export function placeSpell(slots: Slots, id: number, to: number): (number | null)[] {
  const out = normalize(slots);
  if (!inRange(to)) return out;
  const at = out.indexOf(id);
  if (at >= 0) out[at] = null;
  out[to] = id;
  return out;
}

/** "Remove from hotbar". */
export function removeSlot(slots: Slots, i: number): (number | null)[] {
  const out = normalize(slots);
  if (inRange(i)) out[i] = null;
  return out;
}

/** `slots` with what can't be on the bar emptied: unknown, unlearned and flourish-only spells, and repeats (the first one stays). */
export function cleanSlots(slots: Slots, learned: readonly number[], spells: readonly SpellDef[] = SPELLS): (number | null)[] {
  const out = normalize(slots);
  return out.map((id, i) => {
    if (id === null) return null;
    const s = spells.find((x) => x.id === id);
    return s && isHotbarSpell(s) && learned.includes(id) && out.indexOf(id) === i ? id : null;
  });
}

function inRange(i: number): boolean {
  return Number.isInteger(i) && i >= 0 && i < HOTBAR_SLOTS;
}

/** The key of slot `i`: "1"-"9", then "0". */
export function slotKey(i: number): string {
  return String((i + 1) % 10);
}

/** The slot a key press picks (the digits along the top or the keypad), or null. */
export function slotOfCode(code: string): number | null {
  const m = /^(?:Digit|Numpad)([0-9])$/.exec(code);
  if (!m) return null;
  const d = Number(m[1]);
  return d === 0 ? 9 : d - 1;
}

/** A box on screen (px from the window's top left), as `getBoundingClientRect` gives it. */
export interface ScreenBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** The gap kept from the window's bottom edge, and from whatever the bar keeps clear of (px). */
export const HOTBAR_MARGIN = 12;
const SIDE_GAP = 6;

/** Where the bar sits: its centre (px from the left edge) and how high its bottom edge is (px from the bottom edge). */
export interface HotbarPlace {
  centre: number;
  bottom: number;
}

/**
 * Where the bar (`barWidth` × `barHeight`) goes among `boxes` (the chat, the
 * menu bar, the emote bar), in a window of `width` × `height` (pure): along
 * the bottom edge, centred, or moved sideways into the gap the boxes beside
 * it leave when it fits there; otherwise just above the boxes it would
 * overlap, centred, never off the top.
 */
export function hotbarPlace(barWidth: number, barHeight: number, boxes: readonly (ScreenBox | null)[], width: number, height: number): HotbarPlace {
  const centre = width / 2;
  const top = height - HOTBAR_MARGIN - barHeight;
  const low = boxes.filter((b): b is ScreenBox => !!b && b.top < height - HOTBAR_MARGIN && b.bottom > top);
  let gapLeft = 0;
  let gapRight = width;
  for (const b of low) {
    if ((b.left + b.right) / 2 < centre) gapLeft = Math.max(gapLeft, b.right);
    else gapRight = Math.min(gapRight, b.left);
  }
  const half = barWidth / 2 + SIDE_GAP;
  if (gapRight - gapLeft >= 2 * half) {
    return { centre: Math.round(Math.min(Math.max(centre, gapLeft + half), gapRight - half)), bottom: HOTBAR_MARGIN };
  }
  const left = centre - barWidth / 2;
  const right = centre + barWidth / 2;
  let bottom = HOTBAR_MARGIN;
  for (const b of boxes) {
    if (!b || b.right <= left || b.left >= right) continue;
    bottom = Math.max(bottom, Math.round(height - b.top) + 6);
  }
  return { centre: Math.round(centre), bottom: Math.min(bottom, Math.max(HOTBAR_MARGIN, height - barHeight - HOTBAR_MARGIN)) };
}

/** What the game says about where we are and what is on (for a slot's look). */
export interface HotbarContext {
  learned: readonly number[];
  /** The kind of zone we are in (null while one loads). */
  zone: string | null;
  /** Our wand is lit (Lucerna). */
  lit: boolean;
  /** Our guardian is out (Custodio). */
  guardian: boolean;
  /** Choosing a piece to levitate (Elevara). */
  aiming: boolean;
  /** Fraction (0..1) of a spell's cooldown still to run. */
  cooldown: (id: number) => number;
}

/** One slot as the bar draws it. */
export interface SlotView {
  index: number;
  key: string;
  spell: SpellDef | null;
  /** A lasting spell of ours is on (a lit wand, our guardian, aiming Elevara). */
  on: boolean;
  /** It can't be cast where we are (an arena; Elevara outside a dorm room). */
  off: boolean;
  /** 0..1: the cooldown still to run. */
  cooldown: number;
  /** The tooltip: name, key, and how to aim it. */
  title: string;
}

/** Whether `s` can be cast in a zone of kind `zone`, from the bar. */
export function castableHere(s: SpellDef, zone: string | null): boolean {
  if (zone === "arena") return false;
  if (s.social.kind === "levitate") return zone === "dorm";
  return true;
}

/** Whether a lasting spell of ours is on (the slot glows). */
export function isOn(s: SpellDef, c: Pick<HotbarContext, "lit" | "guardian" | "aiming">): boolean {
  switch (s.social.kind) {
    case "light":
      return c.lit;
    case "guardian":
      return c.guardian;
    case "levitate":
      return c.aiming;
    default:
      return false;
  }
}

/** The tooltip of a slot holding `s` (key `key`). */
export function slotTitle(s: SpellDef, key: string, c: Pick<HotbarContext, "lit" | "guardian" | "zone">): string {
  const parts = [`${s.name} (${key})`];
  if (s.social.kind === "light" && c.lit) parts.push("Casting it again puts your light out.");
  if (s.social.kind === "guardian" && c.guardian) parts.push("Casting it again dismisses your guardian.");
  if (!castableHere(s, c.zone)) parts.push(c.zone === "arena" ? "Only dueling casts work on the dueling stage." : "It works in a dorm room.");
  const aim = aimText(s);
  if (aim) parts.push(aim);
  return parts.join("\n");
}

/** Every slot as the bar draws it. Unknown and unlearned spells show as empty. */
export function slotViews(slots: Slots, c: HotbarContext, spells: readonly SpellDef[] = SPELLS): SlotView[] {
  return cleanSlots(slots, c.learned, spells).map((id, index) => {
    const key = slotKey(index);
    const spell = id === null ? null : (spells.find((s) => s.id === id) ?? null);
    if (!spell) return { index, key, spell: null, on: false, off: false, cooldown: 0, title: `Empty slot (${key}): drag a spell here from your spellbook (Y)` };
    return { index, key, spell, on: isOn(spell, c), off: !castableHere(spell, c.zone), cooldown: Math.max(0, Math.min(1, c.cooldown(spell.id))), title: slotTitle(spell, key, c) };
  });
}
