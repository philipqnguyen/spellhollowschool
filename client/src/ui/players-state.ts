// The players list and the player card, without the DOM.
// The list is everyone online, name and house only (never where they are),
// searched on the server (`GET /api/players?q=&limit=&after=`: a
// case-insensitive part of the name) a page at a time (the economy's
// KeysetPager); the card (`GET /api/players/{name}/card`) is a player's
// name, house, level, skill levels and worn outfit. The actions
// reuse what exists: Add friend (`POST /api/friends/requests`), Whisper
// (the chat's whisper channel), Examine (the card), Challenge to a duel
// (`DuelChallengeName`). Pure, unit tested in players.test.ts.

import { fromWire, type WireAppearance } from "../content/appearance";
import { GEAR_SLOTS, clothingDef, wearLabel } from "../content/catalog";
import { houseInfo } from "../content/houses";
import type { Page } from "../economy/paging";
import type { Appearance, GearSlot } from "../wasm/sim_wasm";
import { SLOT_LABELS } from "./wardrobe-state";

/** The server's longest search (characters). */
export const QUERY_MAX = 24;
/** Names per page (the server's default; it allows up to 100). */
export const PAGE_LIMIT = 25;
/** Typing waits this long (ms) before a search, so a burst of keys asks once (the server allows 20, then 2 a second). */
export const SEARCH_DEBOUNCE_MS = 350;

/** One row of the list. */
export interface PlayerRow {
  name: string;
  /** Lower-case house id. */
  house: string;
}

/** What may be done to a player from the list (the card shows all but Examine). */
export type PlayerAction = "add_friend" | "whisper" | "examine" | "challenge" | "party_invite" | "club_invite";

export const ACTION_LABELS: Readonly<Record<PlayerAction, string>> = {
  add_friend: "Add friend",
  whisper: "Whisper",
  examine: "Examine",
  challenge: "Challenge to a duel",
  party_invite: "Invite to party",
  club_invite: "Invite to club",
};

/** The search as sent: trimmed and at most `QUERY_MAX` characters. */
export function normaliseQuery(q: string): string {
  return [...q.trim()].slice(0, QUERY_MAX).join("");
}

/** `GET /api/players` for a search, a page cursor and a page size (the path after `/api`). */
export function playersPath(q: string, after: string | null, limit = PAGE_LIMIT): string {
  const p = new URLSearchParams();
  const query = normaliseQuery(q);
  if (query) p.set("q", query);
  p.set("limit", String(limit));
  if (after) p.set("after", after);
  return `/players?${p.toString()}`;
}

/** `GET /api/players/{name}/card` (the path after `/api`). */
export function cardPath(name: string): string {
  return `/players/${encodeURIComponent(name)}/card`;
}

/** The list endpoint's answer, checked. */
export function parsePlayersPage(raw: unknown): Page<PlayerRow> {
  const r = (raw ?? {}) as { items?: unknown; next?: unknown };
  const items = Array.isArray(r.items) ? r.items : [];
  return {
    items: items.filter((i): i is { name: string; house?: unknown } => !!i && typeof (i as { name?: unknown }).name === "string").map((i) => ({ name: i.name, house: typeof i.house === "string" ? i.house.toLowerCase() : "" })),
    next: typeof r.next === "string" && r.next ? r.next : null,
  };
}

/** The actions for `name`'s row (none of them on our own row but Examine); `clubInvite`: we may invite to our club (an officer or its owner). */
export function playerActions(name: string, myName: string, clubInvite = false): PlayerAction[] {
  if (sameName(name, myName)) return ["examine"];
  return ["add_friend", "whisper", "party_invite", ...(clubInvite ? (["club_invite"] as const) : []), "examine", "challenge"];
}

/** Names compare without case (the server's `name_key` folds case). */
export function sameName(a: string, b: string): boolean {
  return a.toLocaleLowerCase() === b.toLocaleLowerCase();
}

/** The line over the list: what it shows (the HUD bar counts everyone online). */
export function listSummary(shown: number, q: string): string {
  const query = normaliseQuery(q);
  if (!shown) return query ? `No one online has "${query}" in their name.` : "No one is online.";
  return query ? `Online now, with "${query}" in their name` : "Online now, A to Z";
}

/** Friendlier words for the players endpoints' errors, by code. */
export function playersError(code: string, fallback: string): string {
  switch (code) {
    case "rate_limited":
      return "That was a lot of searching at once. Wait a moment and try again.";
    case "invalid_query":
      return `Search with at most ${QUERY_MAX} letters.`;
    case "player_not_found":
      return "No one goes by that name.";
    default:
      return fallback;
  }
}

// ---------------------------------------------------------------- the card

export interface PlayerCard {
  name: string;
  /** Lower-case house id. */
  house: string;
  level: number;
  /** Every subject, in the server's order, with its level. */
  skills: { subject: string; level: number }[];
  /** What they wear (null when the server has no look stored). */
  appearance: Appearance | null;
  /** The club they are in (null: none). */
  club: { name: string; tag: string } | null;
}

/** The card endpoint's answer, checked. */
export function parseCard(raw: unknown): PlayerCard {
  const r = (raw ?? {}) as Record<string, unknown>;
  const num = (v: unknown, fallback = 0) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
  const skills = Array.isArray(r.skills) ? r.skills : [];
  return {
    name: typeof r.name === "string" ? r.name : "",
    house: typeof r.house === "string" ? r.house.toLowerCase() : "",
    level: num(r.level, 1),
    skills: skills
      .filter((s): s is { discipline: string; level?: unknown } => !!s && typeof (s as { discipline?: unknown }).discipline === "string")
      .map((s) => ({ subject: s.discipline, level: num(s.level, 1) })),
    appearance: r.appearance && typeof r.appearance === "object" ? fromWire(r.appearance as Partial<WireAppearance>) : null,
    club: parseCardClub(r.club),
  };
}

function parseCardClub(raw: unknown): { name: string; tag: string } | null {
  const c = (raw ?? null) as { name?: unknown; tag?: unknown } | null;
  return c && typeof c.name === "string" && typeof c.tag === "string" ? { name: c.name, tag: c.tag } : null;
}

/** "Skytalon · Level 5". */
export function cardSubtitle(c: Pick<PlayerCard, "house" | "level">): string {
  const house = houseInfo(c.house).name || c.house;
  return [house, `Level ${c.level}`].filter(Boolean).join(" · ");
}

/** The skill levels with their subjects' names ("History of Magic"). */
export function skillRows(c: Pick<PlayerCard, "skills">, subjectName: (id: string) => string): { name: string; level: number }[] {
  return c.skills.map((s) => ({ name: subjectName(s.subject) || s.subject, level: s.level }));
}

/** What they wear, slot by slot (empty slots left out), with the wear labels. */
export function wornRows(a: Appearance | null): { slot: GearSlot; label: string; name: string; wear: string | null }[] {
  if (!a) return [];
  const out: { slot: GearSlot; label: string; name: string; wear: string | null }[] = [];
  for (const slot of GEAR_SLOTS) {
    const worn = a.outfit[slot];
    if (!worn) continue;
    const def = clothingDef(worn.itemDef, slot);
    out.push({ slot, label: SLOT_LABELS[slot], name: def?.name ?? "Something unusual", wear: wearLabel(def) });
  }
  return out;
}
