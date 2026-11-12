// What the HUD's clock, class bar and timetable say (docs/architecture.md,
// "Classes and progression" and "The class bar"), from the
// game clock's numbers: the time of day, each class's state with its
// countdown and where it is, and the class bar's "Classes starting in ..."
// with its panel of the slot's classes. Pure (no DOM, no wasm), unit tested
// in schedule.test.ts and class-bar.test.ts; schedule.ts and class-bar.ts
// draw it.

import type { ClassTimes, Period } from "../wasm/sim_wasm";

export type ScheduleKind =
  /** The door admits students now. */
  | "join"
  /** The client is in this session's classroom. */
  | "attending"
  /** A session is under way, its door already closed. */
  | "running"
  /** Nothing under way: the next session's countdown. */
  | "next";

export interface ScheduleView {
  classId: string;
  kind: ScheduleKind;
  /** What the countdown counts to, in words: "Door closes in 2:41". */
  detail: string;
  /** Milliseconds to the moment `detail` names. */
  countdownMs: number;
  /** Where the class is: "Charms Classroom, third floor". */
  where: string;
}

/**
 * The schedule line for one class at `nowMs` (server Unix ms). `attending`
 * is the session id the client is in, if any.
 */
export function scheduleView(cls: { id: string; name: string }, times: ClassTimes, nowMs: number, where: string, attending: number | null = null): ScheduleView {
  const cur = times.current;
  if (cur && attending === cur.session) {
    return { classId: cls.id, kind: "attending", detail: `Lesson ends in ${formatCountdown(cur.endMs - nowMs)}`, countdownMs: cur.endMs - nowMs, where };
  }
  if (cur && times.joinable) {
    const left = cur.joinUntilMs - nowMs;
    return { classId: cls.id, kind: "join", detail: `The door closes in ${formatCountdown(left)}`, countdownMs: left, where };
  }
  const toNext = times.next.startMs - nowMs;
  if (cur) {
    return { classId: cls.id, kind: "running", detail: `Next lesson in ${formatCountdown(toNext)}`, countdownMs: toNext, where };
  }
  return { classId: cls.id, kind: "next", detail: `Next lesson in ${formatCountdown(toNext)}`, countdownMs: toNext, where };
}

/**
 * "45 s", "7 min 20 s", "23 min", "1 h 05 min": a real-time countdown of
 * `ms` (never negative), in words ("Charms in 21:11" next
 * to the game clock read like a time of day).
 */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  if (total < 60) return `${total} s`;
  if (total < 600 && total % 60 !== 0) return `${Math.floor(total / 60)} min ${total % 60} s`;
  if (total < 3600) return `${Math.floor(total / 60)} min`;
  const mins = Math.floor(total / 60);
  return `${Math.floor(mins / 60)} h ${String(mins % 60).padStart(2, "0")} min`;
}

/** "09:41" for game seconds since midnight. */
export function gameClockText(secs: number): string {
  const s = ((Math.floor(secs) % 86_400) + 86_400) % 86_400;
  return `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}

/**
 * "1:31 am" for game seconds since midnight: the HUD's clock chip and the
 * timetable's heading ("01:31" beside "In Care of Magical
 * Creatures" read like a class timer counting up; am and pm say it is the
 * time of day). Midnight is "12:00 am", noon "12:00 pm".
 */
export function clockText(secs: number): string {
  const s = ((Math.floor(secs) % 86_400) + 86_400) % 86_400;
  const hour = Math.floor(s / 3600);
  const mins = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  return `${hour % 12 || 12}:${mins} ${hour < 12 ? "am" : "pm"}`;
}

/** "08:30" game time for a session start ("HH:MM" in the data). */
export function sessionTimeText(hhmm: string): string {
  return /^\d\d:\d\d$/.test(hhmm) ? hhmm : "";
}

const PERIODS: Record<Period, string> = { morning: "Morning", afternoon: "Afternoon", night: "Night" };

/** A lesson period's name ("Morning"): the timetable's groups say "Morning lessons". */
export function periodLabel(p: Period): string {
  return PERIODS[p] ?? p;
}

/**
 * The time of day in words for game seconds since midnight, by the hour
 * (the clock said "Morning, 13:19" because the morning lesson
 * period runs to 14:00): morning 05-12, midday 12-14, afternoon 14-18,
 * evening 18-22, night 22-05.
 */
export function timeOfDay(gameSecs: number): string {
  const hour = Math.floor((((gameSecs % 86_400) + 86_400) % 86_400) / 3600);
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 14) return "midday";
  if (hour >= 14 && hour < 18) return "afternoon";
  if (hour >= 18 && hour < 22) return "evening";
  return "night";
}

/** "1:19 pm, midday": the timetable's heading (`clock` from `clockText`). */
export function clockHeading(clock: string, gameSecs: number): string {
  return `${clock}, ${timeOfDay(gameSecs)}`;
}

/**
 * "Afternoon lessons from 2:00 pm, in 4 real min": when the next lesson
 * period begins, at its game time, and how long that is in real time (a
 * game day is two real hours, so "3:24" read like a clock time).
 */
export function nextPeriodText(next: Period, startsGameSecs: number, inMs: number): string {
  const at = clockText(Math.round(startsGameSecs / 60) * 60);
  const mins = Math.ceil(Math.max(0, inMs) / 60_000);
  const when = mins <= 1 ? "in under a real minute" : `in ${mins} real min`;
  return `${periodLabel(next)} lessons from ${at}, ${when}`;
}

const ORDINALS = ["ground", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"];

/**
 * "third floor" for castle level 3 (0 is the ground floor, below it the dungeons); "on the grounds"
 * outside the castle (the greenhouses and the paddock are not on a floor).
 */
export function floorName(level: number, grounds = false): string {
  if (grounds) return "on the grounds";
  if (level < 0) return "the dungeons";
  return `${ORDINALS[level] ?? `level ${level}`} floor`;
}

/** "Charms Classroom, third floor", "Greenhouses, on the grounds" (just the name when the floor is unknown). */
export function placeText(name: string, level: number | undefined, grounds = false): string {
  return level === undefined ? name : `${name}, ${floorName(level, grounds)}`;
}

/** Whether a walkable surface id is one of the castle grounds' cells (`grounds_r0_c2`). */
export function onGrounds(surfaceId: string | undefined): boolean {
  return surfaceId?.startsWith("grounds_") ?? false;
}

// ------------------------------------------------------------------ timetable by slot

/** A class "starting soon" may be followed with the Wayfinder Charm trail this long before it starts (real ms). */
export const STARTING_SOON_MS = 10 * 60_000;

/** "in 12 min", "in under a minute", "in 1 h 05 min" (real time). */
export function inMinutes(ms: number): string {
  const mins = Math.ceil(Math.max(0, ms) / 60_000);
  if (mins <= 1) return ms <= 60_000 ? "in under a minute" : "in 1 min";
  if (mins < 60) return `in ${mins} min`;
  return `in ${Math.floor(mins / 60)} h ${String(mins % 60).padStart(2, "0")} min`;
}

/** One slot of the day (`GameClock.timetable()`). */
export interface SlotInfo {
  slot: number;
  /** "08:00" game time. */
  at: string;
  gameSeconds: number;
  period: Period;
  classes: readonly string[];
}

/** One class's state for the timetable: its view, and the slots of its session under way and its next. */
export interface ClassSlotState {
  name: string;
  view: ScheduleView;
  /** The slot its session under way started in (null: none under way). */
  currentSlot: number | null;
  nextSlot: number;
  /** Real ms to its next session's start. */
  nextInMs: number;
}

export interface SlotClassView {
  classId: string;
  name: string;
  where: string;
  /** How it stands in this slot: the class's own state, or "later" for a slot further off. */
  kind: ScheduleKind | "later";
  /** "Door open, closes in 2:41", "in 12 min", "In session", "You are here", or "". */
  status: string;
  /** The Wayfinder Charm trail is offered: its door is open, or it starts soon. */
  wayable: boolean;
}

export interface SlotView {
  at: string;
  period: Period;
  classes: SlotClassView[];
}

export interface PeriodGroup {
  period: Period;
  slots: SlotView[];
}

/**
 * The timetable by slot, grouped by period: from the slot under way (or the
 * next one) round the day, slots without classes left out.
 */
export function timetableGroups(slots: readonly SlotInfo[], classes: ReadonlyMap<string, ClassSlotState>, nowGameSecs: number): PeriodGroup[] {
  if (!slots.length) return [];
  // Start at a slot with a session under way, else the next slot to come.
  let start = slots.findIndex((s) => s.classes.some((c) => classes.get(c)?.currentSlot === s.slot));
  if (start < 0) {
    start = slots.findIndex((s) => s.gameSeconds > nowGameSecs);
    if (start < 0) start = 0;
  }
  const groups: PeriodGroup[] = [];
  for (let i = 0; i < slots.length; i++) {
    const s = slots[(start + i) % slots.length]!;
    const list: SlotClassView[] = [];
    for (const id of s.classes) {
      const c = classes.get(id);
      if (!c) continue;
      list.push(slotClass(id, c, s.slot));
    }
    if (!list.length) continue;
    const last = groups[groups.length - 1];
    const view = { at: s.at, period: s.period, classes: list };
    if (last && last.period === s.period) last.slots.push(view);
    else groups.push({ period: s.period, slots: [view] });
  }
  return groups;
}

function slotClass(classId: string, c: ClassSlotState, slot: number): SlotClassView {
  const base = { classId, name: c.name, where: c.view.where };
  if (c.currentSlot === slot && c.view.kind !== "next") {
    switch (c.view.kind) {
      case "join":
        return { ...base, kind: "join", status: `Door open, closes in ${formatCountdown(c.view.countdownMs)}`, wayable: true };
      case "attending":
        return { ...base, kind: "attending", status: "You are here", wayable: false };
      default:
        return { ...base, kind: "running", status: "In session", wayable: false };
    }
  }
  if (c.nextSlot === slot) {
    return { ...base, kind: "next", status: inMinutes(c.nextInMs), wayable: c.nextInMs <= STARTING_SOON_MS };
  }
  return { ...base, kind: "later", status: "", wayable: false };
}

/** The slot (index) a session starting at `gameSecs` belongs to: the slot at that time, within a game minute. */
export function slotAt(slots: readonly SlotInfo[], gameSecs: number): number | null {
  for (const s of slots) {
    const d = Math.abs(s.gameSeconds - gameSecs);
    if (Math.min(d, 86_400 - d) <= 60) return s.slot;
  }
  return null;
}

// ------------------------------------------------------------------ the class bar

/** One class as the class bar needs it: its times now, where it is, and whether we are in its lesson. */
export interface ClassBarInput {
  id: string;
  name: string;
  /** "Charms Classroom, third floor". */
  where: string;
  times: ClassTimes;
  /** The client is in this class's session under way. */
  attending: boolean;
}

export type ClassBarKind =
  /** Doors admit students now: the countdown runs to the last one closing (the lessons begin). */
  | "open"
  /** The client is in a lesson. */
  | "attending"
  /** No door open: the countdown runs to the next slot's doors opening. */
  | "next"
  /** No classes at all. */
  | "none";

export interface ClassBarRow {
  classId: string;
  name: string;
  /** "Charms Classroom, third floor", "Creature Paddock, on the grounds". */
  where: string;
  state: "open" | "attending" | "running" | "next";
  /** "Door closes in 2:41", "Door opens in 12 min", "You are here", "In session". */
  status: string;
  /** The row offers "Show me the way" (not for the lesson we are in). */
  wayable: boolean;
}

export interface ClassBarView {
  kind: ClassBarKind;
  /** The segment's words: "Classes starting in 2:41", "Classes starting in 12 min", "In Charms". */
  text: string;
  /** The countdown part of `text` ("2:41", "12 min"), or "" (the segment shortens the rest on a narrow screen). */
  time: string;
  /** The panel's heading: "Starting now", "Next classes", "In class". */
  heading: string;
  /** The classes of the slot under way (doors open, or the lesson we are in) or of the next slot. */
  rows: ClassBarRow[];
}

/** "2:41": minutes and seconds of `ms` (never negative), rounded up to the second. */
export function clockCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** The class bar's countdown to the next slot: whole minutes ("12 min", "1 h 05 min"), "1:58" from two minutes down. */
export function barCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  if (total <= 120) return clockCountdown(ms);
  const mins = Math.ceil(total / 60);
  if (mins < 60) return `${mins} min`;
  return `${Math.floor(mins / 60)} h ${String(mins % 60).padStart(2, "0")} min`;
}

/** Sessions starting within this of each other (real ms) are one slot's. */
const SAME_SLOT_MS = 60_000;

/**
 * The HUD class bar at `nowMs` (server Unix ms): while doors admit students,
 * "Classes starting in 2:41" to the last door closing (when the lessons
 * begin) and the open classes; in a lesson, "In Charms" and its slot's
 * classes; otherwise "Classes starting in 12 min" to the next slot's doors
 * opening, and that slot's classes. Rows keep `classes`' order.
 */
export function classBar(classes: readonly ClassBarInput[], nowMs: number): ClassBarView {
  const inLesson = classes.find((c) => c.attending && c.times.current);
  if (inLesson) {
    const start = inLesson.times.current!.startMs;
    const rows = classes.filter((c) => c.times.current && Math.abs(c.times.current.startMs - start) <= SAME_SLOT_MS).map((c) => currentRow(c, nowMs));
    return { kind: "attending", text: `In ${inLesson.name}`, time: "", heading: "In class", rows };
  }
  const open = classes.filter((c) => c.times.current && c.times.joinable);
  if (open.length) {
    const closes = Math.max(...open.map((c) => c.times.current!.joinUntilMs));
    const time = clockCountdown(closes - nowMs);
    return { kind: "open", text: `Classes starting in ${time}`, time, heading: "Starting now", rows: open.map((c) => currentRow(c, nowMs)) };
  }
  if (!classes.length) return { kind: "none", text: "", time: "", heading: "Next classes", rows: [] };
  const next = Math.min(...classes.map((c) => c.times.next.startMs));
  const time = barCountdown(next - nowMs);
  const rows = classes
    .filter((c) => c.times.next.startMs - next <= SAME_SLOT_MS)
    .map((c): ClassBarRow => ({ classId: c.id, name: c.name, where: c.where, state: "next", status: `Door opens in ${barCountdown(c.times.next.startMs - nowMs)}`, wayable: true }));
  return { kind: "next", text: `Classes starting in ${time}`, time, heading: "Next classes", rows };
}

function currentRow(c: ClassBarInput, nowMs: number): ClassBarRow {
  const base = { classId: c.id, name: c.name, where: c.where };
  if (c.attending) return { ...base, state: "attending", status: "You are here", wayable: false };
  if (c.times.joinable) return { ...base, state: "open", status: `Door closes in ${clockCountdown(c.times.current!.joinUntilMs - nowMs)}`, wayable: true };
  return { ...base, state: "running", status: "In session", wayable: false };
}
