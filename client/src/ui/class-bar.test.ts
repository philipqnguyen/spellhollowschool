import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { ClassTimes } from "../wasm/sim_wasm";
import { barCountdown, classBar, clockCountdown, type ClassBarInput } from "./schedule-state";

// A slot opens at 0: its doors admit students for 3 min, its lessons run 9.
const session = (startMs: number, id = 1) => ({ session: id, startMs, joinUntilMs: startMs + 180_000, endMs: startMs + 540_000 });
const NEXT = 750_000; // the next slot, 12.5 real min on

const input = (id: string, name: string, where: string, times: ClassTimes, attending = false): ClassBarInput => ({ id, name, where, times, attending });
const openNow = (id: string, name: string, where: string) => input(id, name, where, { current: session(0), joinable: true, next: session(NEXT * 4) });
const nextUp = (id: string, name: string, where: string, start = NEXT) => input(id, name, where, { current: null, joinable: false, next: session(start) });

const defence = openNow("defence", "Defensive Magic", "Defensive Magic Classroom, first floor");
const creatures = openNow("creatures", "Creature Keeping", "Creature Paddock, on the grounds");

describe("the class bar's countdowns", () => {
  it("reads minutes and seconds while doors are open", () => {
    expect(clockCountdown(178_000)).toBe("2:58");
    expect(clockCountdown(59_001)).toBe("1:00");
    expect(clockCountdown(9_000)).toBe("0:09");
    expect(clockCountdown(-5)).toBe("0:00");
  });

  it("reads whole minutes to the next slot, then minutes and seconds from two minutes down", () => {
    expect(barCountdown(12 * 60_000)).toBe("12 min");
    expect(barCountdown(11 * 60_000 + 1)).toBe("12 min");
    expect(barCountdown(150_000)).toBe("3 min");
    expect(barCountdown(120_000)).toBe("2:00");
    expect(barCountdown(61_000)).toBe("1:01");
    expect(barCountdown(0)).toBe("0:00");
    expect(barCountdown(65 * 60_000)).toBe("1 h 05 min");
  });
});

describe("the class bar", () => {
  it("counts down to the doors closing while they are open, with the open classes", () => {
    const v = classBar([defence, creatures, nextUp("charms", "Charms", "Charms Classroom, third floor")], 2_000);
    expect(v.kind).toBe("open");
    expect(v.text).toBe("Classes starting in 2:58");
    expect(v.time).toBe("2:58");
    expect(v.heading).toBe("Starting now");
    expect(v.rows.map((r) => r.classId)).toEqual(["defence", "creatures"]);
    expect(v.rows[0]).toEqual({
      classId: "defence",
      name: "Defensive Magic",
      where: "Defensive Magic Classroom, first floor",
      state: "open",
      status: "Door closes in 2:58",
      wayable: true,
    });
    expect(v.rows[1]!.where).toBe("Creature Paddock, on the grounds");
  });

  it("counts to the last door to close when they close at different times", () => {
    const late = input("history", "History of Magic", "x", { current: { ...session(0), joinUntilMs: 240_000 }, joinable: true, next: session(NEXT * 4) });
    const v = classBar([defence, late], 60_000);
    expect(v.text).toBe("Classes starting in 3:00");
    expect(v.rows.map((r) => r.status)).toEqual(["Door closes in 2:00", "Door closes in 3:00"]);
  });

  it("counts down to the next slot's doors opening when none is open, with that slot's classes", () => {
    const running = input("defence", "Defensive Magic", "x", { current: session(0), joinable: false, next: session(NEXT * 4) });
    const charms = nextUp("charms", "Charms", "Charms Classroom, third floor");
    const potions = nextUp("potions", "Potions", "Potions Classroom, the dungeons");
    const later = nextUp("astronomy", "Astronomy", "Astronomy Tower", NEXT * 2);
    const v = classBar([running, charms, potions, later], 200_000);
    expect(v.kind).toBe("next");
    expect(v.text).toBe("Classes starting in 10 min");
    expect(v.time).toBe("10 min");
    expect(v.heading).toBe("Next classes");
    expect(v.rows.map((r) => r.classId)).toEqual(["charms", "potions"]);
    expect(v.rows[0]).toMatchObject({ state: "next", status: "Door opens in 10 min", wayable: true, where: "Charms Classroom, third floor" });
    // Under two minutes the countdown reads minutes and seconds.
    expect(classBar([charms], NEXT - 95_000).text).toBe("Classes starting in 1:35");
  });

  it("says which lesson we are in, with its slot's other classes", () => {
    const inDefence = input("defence", "Defensive Magic", "x", { current: session(0), joinable: true, next: session(NEXT * 4) }, true);
    const flying = input("flying", "Flying", "Flying Pitch, on the grounds", { current: session(0, 2), joinable: false, next: session(NEXT * 4) });
    const v = classBar([inDefence, creatures, flying, nextUp("charms", "Charms", "y")], 30_000);
    expect(v.kind).toBe("attending");
    expect(v.text).toBe("In Defensive Magic");
    expect(v.time).toBe("");
    expect(v.heading).toBe("In class");
    expect(v.rows.map((r) => [r.classId, r.state, r.status, r.wayable])).toEqual([
      ["defence", "attending", "You are here", false],
      ["creatures", "open", "Door closes in 2:30", true],
      ["flying", "running", "In session", false],
    ]);
  });

  it("is hidden with no classes", () => {
    expect(classBar([], 0)).toMatchObject({ kind: "none", text: "", rows: [] });
  });
});

describe("the class bar's styles", () => {
  const css = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

  it("opens its panel under the top-right corner and keeps the green glow for open doors", () => {
    const bar = css("./class-bar.css");
    expect(bar).toMatch(/\.cbar-pop \{[^}]*top: calc\(var\(--hud-corner-bottom\) \+ 6px\)/);
    expect(bar).toMatch(/\.cbar\.open \{[^}]*border-color: #7fe0b0/);
  });

  it("shortens to \"Classes in ...\" on a narrow screen, and only the words give way", () => {
    const bar = css("./class-bar.css");
    expect(bar).toMatch(/@media \(max-width: 640px\) \{\s*\.cbar-long \{\s*display: none;/);
    expect(bar).toMatch(/\.cbar-time \{[^}]*flex: none/);
    expect(bar).toMatch(/\.cbar-lead \{[^}]*text-overflow: ellipsis/);
  });

  it("has no door toasts: a class door opening shows in the class bar and the chat", () => {
    const school = css("../game/classes/school.ts");
    expect(school).not.toMatch(/admits students[\s\S]{0,400}toasts\.show/);
    expect(school).toMatch(/this\.classBar\.update\(classBar\(/);
  });
});
