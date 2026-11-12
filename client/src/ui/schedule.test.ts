import { describe, expect, it } from "vitest";
import type { ClassTimes } from "../wasm/sim_wasm";
import { clockHeading, clockText, nextPeriodText, timeOfDay } from "./schedule-state";
import { trailText } from "./wayfinding";
import { floorName, formatCountdown, gameClockText, inMinutes, onGrounds, periodLabel, placeText, scheduleView, slotAt, timetableGroups, type ClassSlotState, type SlotInfo } from "./schedule-state";

const cls = { id: "charms", name: "Charms" };
const session = (start: number) => ({ session: 3 * 64, startMs: start, joinUntilMs: start + 180_000, endMs: start + 540_000 });

describe("the timetable's words", () => {
  it("says join now while the door is open, with the time it closes", () => {
    const t: ClassTimes = { current: session(0), joinable: true, next: session(2_400_000) };
    const v = scheduleView(cls, t, 60_000, "Charms Classroom, third floor");
    expect(v.kind).toBe("join");
    expect(v.detail).toBe("The door closes in 2 min");
    expect(v.countdownMs).toBe(120_000);
    expect(v.where).toBe("Charms Classroom, third floor");
  });

  it("counts down to the next lesson when none runs, or the door has closed", () => {
    const running = scheduleView(cls, { current: session(0), joinable: false, next: session(2_400_000) }, 300_000, "x");
    expect(running.kind).toBe("running");
    expect(running.detail).toBe("Next lesson in 35 min");
    const idle = scheduleView(cls, { current: null, joinable: false, next: session(2_400_000) }, 1_000_000, "x");
    expect(idle.kind).toBe("next");
    expect(idle.detail).toBe("Next lesson in 23 min");
  });

  it("knows when the player is in the lesson", () => {
    const v = scheduleView(cls, { current: session(0), joinable: true, next: session(2_400_000) }, 100_000, "x", 3 * 64);
    expect(v.kind).toBe("attending");
    expect(v.detail).toBe("Lesson ends in 7 min 20 s");
    // Another session's id is not this one.
    expect(scheduleView(cls, { current: session(0), joinable: true, next: session(2_400_000) }, 100_000, "x", 5).kind).toBe("join");
  });

  it("formats countdowns, the clock and places", () => {
    expect(formatCountdown(-5)).toBe("0 s");
    expect(formatCountdown(45_000)).toBe("45 s");
    expect(formatCountdown(44_001)).toBe("45 s");
    expect(formatCountdown(3_599_000)).toBe("59 min");
    expect(formatCountdown(150_000)).toBe("2 min 30 s");
    expect(formatCountdown(3_900_000)).toBe("1 h 05 min");
    expect(gameClockText(9 * 3600 + 41 * 60 + 59)).toBe("09:41");
    expect(gameClockText(86_400 + 60)).toBe("00:01");
    expect(periodLabel("night")).toBe("Night");
    expect(floorName(0)).toBe("ground floor");
    expect(floorName(3)).toBe("third floor");
    expect(floorName(-1)).toBe("the dungeons");
    expect(placeText("Charms Classroom", 3)).toBe("Charms Classroom, third floor");
    expect(placeText("Somewhere", undefined)).toBe("Somewhere");
    // The grounds are not a floor.
    expect(floorName(0, true)).toBe("on the grounds");
    expect(placeText("Creature Paddock", 0, true)).toBe("Creature Paddock, on the grounds");
    expect(onGrounds("grounds_r1_c2")).toBe(true);
    expect(onGrounds("great_hall")).toBe(false);
    expect(onGrounds(undefined)).toBe(false);
  });
});

describe("the timetable by slot", () => {
  const slots: SlotInfo[] = [
    { slot: 0, at: "01:00", gameSeconds: 3600, period: "night", classes: [] },
    { slot: 1, at: "08:00", gameSeconds: 8 * 3600, period: "morning", classes: ["charms", "potions"] },
    { slot: 2, at: "10:30", gameSeconds: 10.5 * 3600, period: "morning", classes: ["transfiguration"] },
    { slot: 3, at: "14:30", gameSeconds: 14.5 * 3600, period: "afternoon", classes: ["potions"] },
    { slot: 4, at: "17:00", gameSeconds: 17 * 3600, period: "afternoon", classes: ["charms", "transfiguration"] },
  ];
  const state = (name: string, view: ReturnType<typeof scheduleView>, currentSlot: number | null, nextSlot: number, nextInMs: number): ClassSlotState => ({ name, view, currentSlot, nextSlot, nextInMs });

  it("groups slots by period from the one under way, with each class's state and the Wayfinder Charm offer", () => {
    const open = scheduleView({ id: "charms", name: "Charms" }, { current: session(0), joinable: true, next: session(2_400_000) }, 60_000, "Charms Classroom");
    const soon = scheduleView({ id: "transfiguration", name: "Transfiguration" }, { current: null, joinable: false, next: session(400_000) }, 60_000, "Transfiguration Classroom");
    const later = scheduleView({ id: "potions", name: "Potions" }, { current: null, joinable: false, next: session(3_000_000) }, 60_000, "Potions Classroom");
    const classes = new Map<string, ClassSlotState>([
      ["charms", state("Charms", open, 1, 4, 2_340_000)],
      ["transfiguration", state("Transfiguration", soon, null, 2, 340_000)],
      ["potions", state("Potions", later, null, 3, 2_940_000)],
    ]);
    const g = timetableGroups(slots, classes, 8 * 3600 + 120);
    expect(g.map((x) => x.period)).toEqual(["morning", "afternoon"]);
    expect(g[0]!.slots.map((s) => s.at)).toEqual(["08:00", "10:30"]);
    const [charms, potions] = g[0]!.slots[0]!.classes;
    expect(charms).toMatchObject({ classId: "charms", kind: "join", wayable: true });
    expect(charms!.status).toBe("Door open, closes in 2 min");
    // Potions' 08:00 lesson is not its next one: just listed.
    expect(potions).toMatchObject({ kind: "later", status: "", wayable: false });
    expect(g[0]!.slots[1]!.classes[0]).toMatchObject({ kind: "next", status: "in 6 min", wayable: true });
    expect(g[1]!.slots[0]!.classes[0]).toMatchObject({ classId: "potions", kind: "next", status: "in 49 min", wayable: false });
    // The empty night slot is left out.
    expect(g.flatMap((x) => x.slots).some((s) => s.at === "01:00")).toBe(false);
  });

  it("starts at the next slot when nothing runs, and words minutes", () => {
    const idle = scheduleView({ id: "charms", name: "Charms" }, { current: null, joinable: false, next: session(9_000_000) }, 0, "x");
    const g = timetableGroups(slots, new Map([["charms", state("Charms", idle, null, 4, 9_000_000)]]), 15 * 3600);
    expect(g[0]!.slots[0]!.at).toBe("17:00");
    expect(g[0]!.slots[0]!.classes[0]!.status).toBe("in 2 h 30 min");
    expect(inMinutes(30_000)).toBe("in under a minute");
    expect(inMinutes(61_000)).toBe("in 2 min");
    expect(slotAt(slots, 10.5 * 3600 + 30)).toBe(2);
    expect(slotAt(slots, 12 * 3600)).toBeNull();
  });
});

describe("the timetable's wording", () => {
  it("names the time of day by the hour, not the lesson period", () => {
    // 13:19 is in the morning lesson period (06:00-14:00), but it is not "Morning".
    expect(clockHeading("13:19", 13 * 3600 + 19 * 60)).toBe("13:19, midday");
    expect(timeOfDay(8 * 3600)).toBe("morning");
    expect(timeOfDay(15 * 3600)).toBe("afternoon");
    expect(timeOfDay(19 * 3600)).toBe("evening");
    expect(timeOfDay(23 * 3600)).toBe("night");
    expect(timeOfDay(2 * 3600)).toBe("night");
  });

  it("says when the next lessons begin in game time and real minutes", () => {
    expect(nextPeriodText("afternoon", 14 * 3600 - 0.4, 204_000)).toBe("Afternoon lessons from 2:00 pm, in 4 real min");
    expect(nextPeriodText("night", 22 * 3600, 30_000)).toBe("Night lessons from 10:00 pm, in under a real minute");
  });

  it("reads the clock as a time of day, am and pm", () => {
    expect(clockText(1 * 3600 + 31 * 60 + 59)).toBe("1:31 am");
    expect(clockText(0)).toBe("12:00 am");
    expect(clockText(12 * 3600)).toBe("12:00 pm");
    expect(clockText(13 * 3600 + 5 * 60)).toBe("1:05 pm");
    expect(clockText(23 * 3600 + 59 * 60)).toBe("11:59 pm");
    expect(clockText(86_400 + 9 * 60)).toBe("12:09 am");
    expect(clockText(-60)).toBe("11:59 pm");
    expect(clockHeading(clockText(1 * 3600 + 31 * 60), 1 * 3600 + 31 * 60)).toBe("1:31 am, night");
  });

  it("labels the trail chip during the first-day tour", () => {
    expect(trailText({ name: "Entrance Hall", distance: "40 m", waiting: false, tour: "First-day tour, 3 of 3" })).toBe("First-day tour, 3 of 3: Entrance Hall, 40 m");
    expect(trailText({ name: "Entrance Hall", distance: "40 m", waiting: false })).toBe("Trail to Entrance Hall, 40 m");
  });
});
