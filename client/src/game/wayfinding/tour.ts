// The first-day tour (one way inward from the front doors): on a
// character's first login the Monstra Viam trail leads a fixed tour that never
// depends on the clock. New characters arrive on the front steps outside the
// castle, so it leads inside (through the front doors: `TOUR_VIA_LANDMARK`)
// to the Entrance Hall and its Owl Exchange counter (said
// at once only when the player already stands at the counter), then to
// the house's common room and its notice board, and ends at the house's
// dormitory door, with no walk back. X (or the chip's
// X, or choosing another destination) skips it; Esc only asks "Skip the
// tour?" (a second Esc puts the question away). After it, one one-time
// prompt when a class door is open ("Potions is starting: follow the
// trail"): the next door to open, or one already open as the tour ends or
// at login, if it stays open long enough to get there. Shown once, followed
// or not, it never comes back.
//
// The progress is the server's (`characters.tour_step`, so it follows the
// character to another device): 0 not toured, 1 toured or skipped with the
// prompt still due, 2 done. This is the state machine alone, pure and unit
// tested (tour.test.ts); game/classes/school.ts drives the trail and sends
// each new step (`POST /api/characters/me/tour`).

import type { PlaceGroup } from "../../wasm/sim_wasm";

export type TourStep = 0 | 1 | 2;

/**
 * The first leg leads in through the front doors (the
 * layout's landmark just inside them), not by the Owl Gate beside them that
 * is a few metres shorter; from inside the castle it goes direct.
 */
export const TOUR_VIA_LANDMARK = "front_doors";

/** The tour's stops, in order: key place groups (the Wayfinder offers only our house's dorm and common room). */
export const TOUR_GROUPS: readonly PlaceGroup[] = ["owl_exchange", "common_room", "dorm"];

/**
 * The tour starts by naming its first stop at once when the player stands
 * this close to it (m, across). Only right at the counter, so
 * a new character on the front steps walks in through the doors.
 */
export const START_NEAR_M = 4;
/** ... and this close in height (m: the same floor). */
const START_NEAR_Y_M = 3;

/** The one-time class prompt takes an open door only with at least this long before it closes (ms): time to get there. */
export const PROMPT_MIN_LEFT_MS = 60_000;

/** What the tour says on arriving at each stop (our own words). */
export const TOUR_LINES: Readonly<Record<string, string>> = {
  owl_exchange: "The Entrance Hall, the heart of the castle: the Great Hall, the grounds and the stairs to every classroom start here. This counter is the Owl Exchange, where students buy and sell to one another (press E here).",
  common_room: "Your common room's notice board: press E here to read the House Cup standings and your house's next classes. The Owl Order Catalogue stands in this room too: use it to order basics, which arrive by owl.",
  dorm: "This door leads to your dormitory, your own room: rest there, decorate it with furniture and invite your friends in.",
};

export const TOUR_INTRO = "Welcome to the castle! A trail of light will lead you in through the front doors to the Entrance Hall and its Owl Exchange, then to your common room's notice board and, last, your dormitory. Press X to skip the tour.";
export const TOUR_DONE = "That is the tour. Press F any time to find your way, and watch the clock at the top for your classes: when a door opens, the trail can take you there.";
export const TOUR_SKIPPED = "Tour skipped. Press F any time to find your way round the castle.";
/** Esc during the tour asks first. */
export const TOUR_ASK = "Skip the tour? Press X to skip it, or Esc to keep following the light.";

/** "Potions is starting: follow the trail". */
export function promptText(className: string): string {
  return `${className} is starting: follow the trail`;
}

/** The ids of the tour's stops among `places` (in order; a group the house is not offered is left out). */
export function tourStops(places: readonly { id: string; group: PlaceGroup }[]): string[] {
  const out: string[] = [];
  for (const g of TOUR_GROUPS) {
    const p = places.find((x) => x.group === g);
    if (p) out.push(p.id);
  }
  return out;
}

/** Whether a player at `feet` already stands at a stop at `pos` (the tour's start: it is said, not walked to). */
export function standsAt(pos: readonly [number, number, number], feet: readonly [number, number, number]): boolean {
  return Math.hypot(pos[0] - feet[0], pos[2] - feet[2]) <= START_NEAR_M && Math.abs(pos[1] - feet[1]) <= START_NEAR_Y_M;
}

/** What a key does to a running tour: X skips, Esc asks (or, while asking, puts the question away). */
export type TourKeyAction = "skip" | "ask" | "dismiss" | null;

export function tourKey(e: { key: string; code: string; repeat?: boolean; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean }, asking: boolean): TourKeyAction {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  if (e.code === "KeyX" && !e.repeat) return "skip";
  if (e.key === "Escape") return asking ? "dismiss" : "ask";
  return null;
}

/** An open class door the prompt could name, with how long it stays open (ms). */
export interface OpenDoor {
  classId: string;
  leftMs: number;
}

/** The open door the one-time prompt names: the one open longest still, if long enough to get there. */
export function promptDoor<T extends OpenDoor>(open: readonly T[], minLeftMs = PROMPT_MIN_LEFT_MS): T | null {
  let best: T | null = null;
  for (const d of open) if (d.leftMs >= minLeftMs && (!best || d.leftMs > best.leftMs)) best = d;
  return best;
}

/** What arriving at a stop does. */
export interface TourArrival {
  /** The line to say about the stop. */
  line: string;
  /** The next stop to lead to, or null: the tour is over (send step 1). */
  next: string | null;
}

export class Tour {
  /** The server's step, null until it is known. */
  private stepNow: TourStep | null = null;
  private stops: string[] = [];
  private index = -1;

  get step(): TourStep | null {
    return this.stepNow;
  }

  /** Leading a stop now. */
  get running(): boolean {
    return this.index >= 0;
  }

  /** The stop being led to (null while not running). */
  get stop(): string | null {
    return this.running ? (this.stops[this.index] ?? null) : null;
  }

  /** "First-day tour, 2 of 3" while running. */
  get label(): string {
    return this.running ? `First-day tour, ${this.index + 1} of ${this.stops.length}` : "";
  }

  /** The one-time class prompt is due: toured or skipped (step 1), no tour under way. */
  get promptDue(): boolean {
    return this.stepNow === 1 && !this.running;
  }

  /** The server's step arrived (it only rises: a lower one is ignored). */
  known(step: number): void {
    const s = Math.min(2, Math.max(0, Math.floor(step))) as TourStep;
    if (this.stepNow === null || s > this.stepNow) this.stepNow = s;
    if (this.stepNow !== 0) this.index = -1;
  }

  /** Whether to begin the tour now: step 0, not under way, and we stand in the castle. */
  shouldStart(inCastle: boolean): boolean {
    return inCastle && this.stepNow === 0 && !this.running;
  }

  /** Begins it at the first of `stops` (tourStops). Returns that stop, or null when there is none (then it counts as done: step 1). */
  begin(stops: readonly string[]): string | null {
    this.stops = [...stops];
    if (!this.stops.length) {
      this.finish();
      return null;
    }
    this.index = 0;
    return this.stops[0]!;
  }

  /** The trail arrived at `placeId`: the stop's line and the next stop, or null when it is not the tour's stop. */
  arrived(placeId: string, group: PlaceGroup): TourArrival | null {
    if (!this.running || placeId !== this.stop) return null;
    const line = TOUR_LINES[group] ?? "";
    this.index++;
    if (this.index >= this.stops.length) {
      this.finish();
      return { line, next: null };
    }
    return { line, next: this.stops[this.index]! };
  }

  /** Skips the rest (X, the chip's X, another destination). True when a tour was under way (send step 1). */
  skip(): boolean {
    if (!this.running) return false;
    this.finish();
    return true;
  }

  /**
   * The prompt is shown now: true once, at step 1 with no tour under way (it
   * moves to step 2; send it whether the trail is followed or not).
   */
  takePrompt(): boolean {
    if (!this.promptDue) return false;
    this.stepNow = 2;
    return true;
  }

  private finish(): void {
    this.index = -1;
    if (this.stepNow === null || this.stepNow < 1) this.stepNow = 1;
  }
}
