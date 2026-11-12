// What the House Cup shows (docs/architecture.md): the
// week's standings from `GET /api/house-cup`, read into one state the panel,
// the Great Hall's hourglasses and banners and the nameplates all use. Pure
// (no DOM, no Babylon), unit tested in house-cup.test.ts.
//
// - Houses come in `House::ALL` order (Roarwing, Burrowsby, Skytalon,
//   Serpentyne), spelled as serde does ("Roarwing"); ids here are lower case.
// - A house's bar and hourglass fill is its points over the leader's: the
//   leader is full, every house empty while nobody has scored.
// - Bar colours are the houses' primary and secondary colours in
//   data/items/palette.json (`houses`), never a colour of our own.

import cupRules from "../../../data/house_cup.json";
import { PALETTE } from "../content/catalog";
import { houseInfo } from "../content/houses";

/** The houses in `House::ALL` order (the castle kit's house_a..d). */
export const CUP_HOUSES = ["roarwing", "burrowsby", "skytalon", "serpentyne"] as const;
export type CupHouse = (typeof CUP_HOUSES)[number];

/** The castle kit's house slot of a house (`house_a` Roarwing ... `house_d` Serpentyne). */
export function houseSlot(id: CupHouse): string {
  return `house_${"abcd"[CUP_HOUSES.indexOf(id)]}`;
}

/** The house of a castle kit slot (`house_c` → skytalon), or null. */
export function slotHouse(slot: string): CupHouse | null {
  const m = /^house_([abcd])$/.exec(slot);
  return m ? CUP_HOUSES["abcd".indexOf(m[1]!)]! : null;
}

export interface CupHouseView {
  id: CupHouse;
  name: string;
  points: number;
  /** Points over the leader's, 0..1 (the bar and the hourglass). */
  share: number;
  /** On the top score (and someone scored). */
  leading: boolean;
}

export interface CupWeekView {
  startMs: number;
  endMs: number;
}

export interface LastCupView {
  week: CupWeekView;
  winners: CupHouse[];
  houses: CupHouseView[];
}

export interface CupState {
  week: CupWeekView;
  /** Local `Date.now()` ms at which the week resets (from `resets_in_secs` when it arrived). */
  resetAtMs: number;
  houses: CupHouseView[];
  /** The caller's points this week. */
  mine: number;
  last: LastCupView | null;
  /** Houses wearing the cup badge and hanging their banners in the Great Hall now. */
  badge: ReadonlySet<CupHouse>;
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

function cupHouse(v: unknown): CupHouse | null {
  if (typeof v !== "string") return null;
  const id = v.toLowerCase();
  return (CUP_HOUSES as readonly string[]).includes(id) ? (id as CupHouse) : null;
}

function week(v: unknown): CupWeekView {
  const w = isObj(v) ? v : {};
  return { startMs: num(w.start_ms), endMs: num(w.end_ms) };
}

/** The four houses (in order, missing ones at 0) with their shares of the leader. */
export function standings(rows: unknown): CupHouseView[] {
  const pts = new Map<CupHouse, number>();
  if (Array.isArray(rows)) {
    for (const r of rows) {
      if (!isObj(r)) continue;
      const id = cupHouse(r.house);
      if (id) pts.set(id, Math.max(0, num(r.points)));
    }
  }
  const fills = fillShares(CUP_HOUSES.map((id) => pts.get(id) ?? 0));
  const top = Math.max(...CUP_HOUSES.map((id) => pts.get(id) ?? 0));
  return CUP_HOUSES.map((id, i) => {
    const points = pts.get(id) ?? 0;
    return { id, name: houseInfo(id).name, points, share: fills[i]!, leading: top > 0 && points === top };
  });
}

/** Each house's fill: points over the leader's (0..1); all 0 while nobody has scored. */
export function fillShares(points: readonly number[]): number[] {
  const top = Math.max(0, ...points);
  return points.map((p) => (top > 0 ? Math.min(1, Math.max(0, p) / top) : 0));
}

/** `GET /api/house-cup`'s answer, read at local time `nowMs` (`Date.now()`); null when it is not one. */
export function parseCup(json: unknown, nowMs: number): CupState | null {
  if (!isObj(json) || !Array.isArray(json.houses)) return null;
  let last: LastCupView | null = null;
  if (isObj(json.last)) {
    const winners = Array.isArray(json.last.winners) ? json.last.winners.map(cupHouse).filter((h): h is CupHouse => h !== null) : [];
    last = { week: week(json.last.week), winners, houses: standings(json.last.houses) };
  }
  const badge = new Set<CupHouse>();
  if (Array.isArray(json.badge)) for (const b of json.badge) {
    const id = cupHouse(b);
    if (id) badge.add(id);
  }
  return {
    week: week(json.week),
    resetAtMs: nowMs + Math.max(0, num(json.resets_in_secs)) * 1000,
    houses: standings(json.houses),
    mine: Math.max(0, num(json.mine)),
    last,
    badge,
  };
}

/**
 * Eases a shown fill toward `target` (both 0..1) over `dt` seconds: about
 * two thirds of the way each `tau` seconds, and snapped once within a hair.
 */
export function easeFill(current: number, target: number, dt: number, tau = 1.2): number {
  const next = target + (current - target) * Math.exp(-Math.max(0, dt) / tau);
  return Math.abs(next - target) < 0.002 ? target : next;
}

const WEEKDAYS: Record<string, string> = { monday: "Monday", tuesday: "Tuesday", wednesday: "Wednesday", thursday: "Thursday", friday: "Friday", saturday: "Saturday", sunday: "Sunday" };

/** "Monday 00:00 UTC": when the cup resets (data/house_cup.json `week_start`). */
export function resetWhen(rules: { week_start?: { weekday?: string; hour_utc?: number } } = cupRules): string {
  const ws = rules.week_start ?? {};
  const day = WEEKDAYS[String(ws.weekday ?? "monday").toLowerCase()] ?? "Monday";
  return `${day} ${String(ws.hour_utc ?? 0).padStart(2, "0")}:00 UTC`;
}

/** "3 d 4 h", "5 h 12 min", "12 min", "under a minute" (real time, never negative). */
export function untilText(ms: number): string {
  const mins = Math.floor(Math.max(0, ms) / 60_000);
  if (mins < 1) return "under a minute";
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h ${String(mins % 60).padStart(2, "0")} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

/** "Resets Monday 00:00 UTC, in 3 d 4 h". */
export function resetText(s: CupState, nowMs: number): string {
  return `Resets ${resetWhen()}, in ${untilText(s.resetAtMs - nowMs)}`;
}

/** "A, B and C". */
export function listNames(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The newest closed week in words: who won (ties shared), or that none has closed yet. */
export function lastText(s: CupState): string {
  const l = s.last;
  if (!l) return "No week has closed yet: this is the first House Cup.";
  const when = l.week.endMs > 0 && s.week.startMs > 0 && l.week.startMs >= s.week.startMs ? "This week" : "Last week";
  if (!l.winners.length) return `${when}: no house scored, so nobody won the cup.`;
  const names = l.winners.map((id) => houseInfo(id).name);
  const pts = l.houses.find((h) => h.id === l.winners[0])?.points ?? 0;
  if (names.length === 1) return `${when}: ${names[0]} won the House Cup with ${pts} points.`;
  return `${when}: ${listNames(names)} shared the House Cup with ${pts} points each.`;
}

/** "You have earned 14 points for Skytalon this week." */
export function mineText(s: CupState, house: string): string {
  const name = houseInfo(house).name;
  if (s.mine <= 0) return `You have not earned points for ${name} yet this week: classes, finds, crafts and duel wins all count.`;
  const reward = cupRules.reward.min_points;
  const tail = s.mine >= reward ? "" : ` ${reward} or more by the week's end earn a pennant if ${name} wins.`;
  return `You have earned ${s.mine} point${s.mine === 1 ? "" : "s"} for ${name} this week.${tail}`;
}

/** A house's bar colours: its primary and secondary in data/items/palette.json. */
export function houseColours(id: CupHouse): { fill: string; edge: string } {
  const h = PALETTE.houses[id];
  const hex = (i: number | undefined) => PALETTE.colours.find((c) => c.index === i)?.hex ?? PALETTE.colours[0]!.hex;
  return { fill: hex(h?.primary), edge: hex(h?.secondary) };
}
