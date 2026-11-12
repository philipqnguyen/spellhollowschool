// The game clock and the class timetable (data/classes/schedule.json), read
// through sim-core's own code (`sim_wasm.GameClock`): game time is a pure
// function of the server's Unix time, so the HUD shows exactly the periods
// and sessions the server runs. Display names and landmarks come from the
// same file; the wasm module does the arithmetic.

import scheduleText from "../../../../data/classes/schedule.json?raw";
import scheduleJson from "../../../../data/classes/schedule.json";
import { GameClock, type ClassTimes, type Period, type TimetableSlot } from "../../wasm/sim_wasm";

/** One class of the timetable, as the HUD needs it. */
export interface ScheduledClass {
  id: string;
  name: string;
  /** Session start times in game time ("09:00"). */
  sessions: readonly string[];
  /** Real seconds a session lasts, and how long its door admits students. */
  durationSecs: number;
  joinSecs: number;
  /** The castle `class_door` interactable that admits students. */
  door: string;
  /** The castle landmark the HUD names as "where". */
  landmark: string;
}

/** Every class of the timetable, in file order. */
export const CLASSES: readonly ScheduledClass[] = scheduleJson.classes.map((c) => ({
  id: c.id,
  name: c.name,
  sessions: c.sessions,
  durationSecs: c.duration_secs,
  joinSecs: c.join_secs,
  door: c.door,
  landmark: c.landmark,
}));

/** Real milliseconds in one game day (two hours). */
export const DAY_REAL_MS = scheduleJson.clock.day_real_secs * 1000;

export function scheduledClass(id: string): ScheduledClass | undefined {
  return CLASSES.find((c) => c.id === id);
}

/** The class whose door is `doorId`, if any. */
export function classByDoor(doorId: string): ScheduledClass | undefined {
  return CLASSES.find((c) => c.door === doorId);
}

/** The game clock: sim-core's, built once the wasm module is loaded. */
export class SchoolClock {
  private readonly clock = new GameClock(scheduleText);

  /** Game seconds since game midnight (0-86,399). */
  gameSeconds(unixMs: number): number {
    return this.clock.gameSeconds(unixMs);
  }

  period(unixMs: number): Period {
    return this.clock.period(unixMs);
  }

  /** 0 at night to 1 in full day, for the lighting. */
  daylight(unixMs: number): number {
    return this.clock.daylight(unixMs);
  }

  /** When the next period starts (server Unix ms). */
  nextPeriodAt(unixMs: number): number {
    return this.clock.nextPeriodAt(unixMs);
  }

  /** The day's slots in order: when each starts, its period and its classes. */
  timetable(): TimetableSlot[] {
    return this.clock.timetable();
  }

  /** A subject's display name ("History of Magic"). */
  subjectName(subject: string): string {
    return this.clock.subjectName(subject);
  }

  /** `id`'s session under way (if any), whether its door is open, and the next one. */
  classTimes(id: string, unixMs: number): ClassTimes {
    return this.clock.classTimes(id, unixMs);
  }

  dispose(): void {
    this.clock.free();
  }
}
