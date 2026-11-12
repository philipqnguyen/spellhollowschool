// Spells to level 20 (protocol v11): what a social cast is aimed at, from
// the world selection (Tab/E). The hindering spells (bind, slow, pull) need
// a selected friend; Tinctura and Dissolvo fall back to yourself with nothing
// selected, and Dissolvo first to a piece floating in reach (in a dorm room).
// Friendship is checked here only to say so at once: the server decides.
// Pure, unit tested (aim.test.ts).

import { mayTargetSelf, needsFriend, type SpellDef } from "./data";

export type CastAim = { kind: "none" } | { kind: "entity"; id: number } | { kind: "furniture"; itemId: number } | { kind: "refuse"; text: string };

export interface AimContext {
  /** The character selected (null: nobody). */
  selected: number | null;
  selfId: number;
  isFriend: (id: number) => boolean;
  /** A piece floating in reach of us (Elevara), or null. */
  floatingInReach: number | null;
}

export const SELECT_A_FRIEND = "Select a friend first (Tab).";

export function onlyFriends(s: SpellDef): string {
  return `${s.name} works only on a friend.`;
}

/** What `spell` is cast at, or why not. */
export function chooseAim(spell: SpellDef, c: AimContext): CastAim {
  const other = c.selected !== null && c.selected !== c.selfId ? c.selected : null;
  if (needsFriend(spell)) {
    if (other === null) return { kind: "refuse", text: SELECT_A_FRIEND };
    if (!c.isFriend(other)) return { kind: "refuse", text: onlyFriends(spell) };
    return { kind: "entity", id: other };
  }
  if (mayTargetSelf(spell)) {
    if (other !== null) return c.isFriend(other) ? { kind: "entity", id: other } : { kind: "refuse", text: onlyFriends(spell) };
    if (spell.social.kind === "dispel" && c.floatingInReach !== null) return { kind: "furniture", itemId: c.floatingInReach };
    return { kind: "none" };
  }
  return { kind: "none" };
}

/** The floating piece nearest `feet` within `range` (m), or null. Pure. */
export function nearestFloating(floating: readonly { itemId: number; x: number; y: number; z: number }[], feet: readonly [number, number, number], range: number): number | null {
  let best: number | null = null;
  let bestD = Infinity;
  for (const f of floating) {
    const d = Math.hypot(f.x - feet[0], f.y - feet[1], f.z - feet[2]);
    if (d <= range && d < bestD) {
      bestD = d;
      best = f.itemId;
    }
  }
  return best;
}
