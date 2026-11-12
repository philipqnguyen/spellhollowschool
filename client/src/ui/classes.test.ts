import { describe, expect, it } from "vitest";
import { phaseWords, type LessonView } from "./classes";
import { rejectText } from "../game/classes/controller";

const view = (over: Partial<LessonView>): LessonView => ({
  className: "Charms",
  professor: "Professor",
  section: 1,
  phase: { kind: "gathering" } as LessonView["phase"],
  phaseLeftMs: 60_000,
  roundsTotal: 4,
  answered: 0,
  beats: 0,
  picking: false,
  average: 0,
  ...over,
});

describe("the lesson panel's words", () => {
  it("names the rounds plainly", () => {
    const w = phaseWords(view({ phase: { kind: "practice", round: 2 } as LessonView["phase"] }));
    expect(w.phase).toBe("Round 2 of 4");
    expect(phaseWords(view({ roundsTotal: 0, phase: { kind: "practice", round: 3 } as LessonView["phase"] })).phase).toBe("Round 3");
  });

  it("says the rounds start once the door has closed", () => {
    expect(phaseWords(view({})).hint).toContain("The rounds start once the door has closed.");
    expect(phaseWords(view({})).hint).not.toMatch(/Practice/);
  });

  it("keeps Flying's practise line", () => {
    const w = phaseWords(view({ flying: true, phase: { kind: "demonstration" } as LessonView["phase"] }));
    expect(w.hint).toContain("Practise steering while you wait.");
  });

  it("says the rounds are over", () => {
    expect(rejectText("not_in_practice")).toBe("the rounds are over");
  });
});
