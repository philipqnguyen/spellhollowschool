import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { clearOf, declutter, PLATE_HEIGHT, plateHalfWidth, type PlateBox } from "./nameplates";

const box = (id: number, x: number, y: number, halfWidth = 40): PlateBox => ({ id, x, y, halfWidth });

describe("declutter", () => {
  it("keeps plates that don't touch", () => {
    const boxes = [box(1, 100, 100), box(2, 300, 100), box(3, 100, 200)];
    expect(declutter(boxes, new Set(), [])).toEqual([0, 1, 2]);
  });

  it("drops a farther plate that would cover a nearer one", () => {
    // Nearest first: 1 is kept; 2 sits almost on it; 3 overlaps 2 only, so it stays.
    const boxes = [box(1, 100, 100), box(2, 110, 104), box(3, 190, 104)];
    expect(declutter(boxes, new Set(), [])).toEqual([0, 2]);
  });

  it("lets plates already showing overlap a little more, so they don't flicker", () => {
    // 90% of the combined half-widths apart: hidden if new, kept if it was showing.
    const boxes = [box(1, 100, 100), box(2, 172, 100)];
    expect(declutter(boxes, new Set(), [])).toEqual([0]);
    expect(declutter(boxes, new Set([2]), [])).toEqual([0, 1]);
  });

  it("only compares rows that overlap vertically", () => {
    const boxes = [box(1, 100, 100), box(2, 100, 100 + PLATE_HEIGHT)];
    expect(declutter(boxes, new Set(), [])).toEqual([0, 1]);
  });

  it("caps a crowd at forty plates and reuses the output array", () => {
    const boxes = Array.from({ length: 60 }, (_, i) => box(i, i * 200, 100));
    const out = [99];
    expect(declutter(boxes, new Set(), out)).toBe(out);
    expect(out).toHaveLength(40);
  });
});

describe("plateHalfWidth", () => {
  it("grows with the name", () => {
    expect(plateHalfWidth("Basil Wren")).toBeGreaterThan(plateHalfWidth("Bo"));
    expect(plateHalfWidth("")).toBeGreaterThan(0);
  });
});

describe("clearOf", () => {
  const BAR = { left: 300, right: 1100, bottom: 60 };

  it("moves a plate under the top bar to just below it", () => {
    const b = box(1, 700, 40, 30);
    clearOf(b, BAR);
    expect(b.y).toBe(BAR.bottom + PLATE_HEIGHT);
    // A plate whose top edge already clears the bar stays put.
    const low = box(2, 700, 200, 30);
    clearOf(low, BAR);
    expect(low.y).toBe(200);
  });

  it("leaves plates beside the bar alone", () => {
    const left = box(1, 200, 10, 30);
    clearOf(left, BAR);
    expect(left.y).toBe(10);
    // Overlapping the bar's edge by its half-width counts as under it.
    const edge = box(2, 290, 10, 20);
    clearOf(edge, BAR);
    expect(edge.y).toBe(BAR.bottom + PLATE_HEIGHT);
    const none = box(3, 700, 10, 30);
    clearOf(none, null);
    expect(none.y).toBe(10);
  });

  it("plates pushed together below the bar are decluttered", () => {
    const a = box(1, 700, 30, 30);
    const b = box(2, 705, 50, 30);
    clearOf(a, BAR);
    clearOf(b, BAR);
    expect(declutter([a, b], new Set(), [])).toEqual([0]);
  });
});

describe("the world's labels under the HUD (the 2d review)", () => {
  /** The declarations of `selector`'s first rule in a stylesheet. */
  const rule = (css: string, selector: string): string => {
    const at = css.indexOf(`${selector} {`);
    expect(at, selector).toBeGreaterThanOrEqual(0);
    return css.slice(at, css.indexOf("}", at));
  };

  it("draws name plates and speech bubbles below every HUD strip and panel", () => {
    const style = readFileSync(new URL("../style.css", import.meta.url), "utf8");
    const npc = readFileSync(new URL("../game/npc/npc.css", import.meta.url), "utf8");
    // #hud is the stacking context (fixed, z-index 10); a negative z-index inside it sits under its other children.
    expect(rule(style, "#hud")).toMatch(/z-index:\s*10;/);
    expect(rule(style, ".nameplates")).toMatch(/z-index:\s*-1;/);
    expect(rule(npc, ".npc-bubbles")).toMatch(/z-index:\s*-1;/);
  });
});
