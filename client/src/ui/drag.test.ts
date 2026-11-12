// Every window fits on screen and drags by its title bar (ui/drag.ts): the pure parts.

import { describe, expect, it } from "vitest";
import { clampWindow, dragTo, parsePositions, WINDOW_EDGE, windowCap, windowKind } from "./drag";

const SCREEN = { width: 1280, height: 720 };
const SMALL = { width: 1024, height: 640 };

describe("clampWindow", () => {
  it("leaves a window that is on screen where it is", () => {
    expect(clampWindow({ x: 300, y: 62 }, { width: 400, height: 300 }, SCREEN)).toEqual({ x: 300, y: 62 });
  });

  it("moves a window the least that brings all of it on screen, an edge in from each side", () => {
    const size = { width: 400, height: 300 };
    expect(clampWindow({ x: -200, y: -50 }, size, SCREEN)).toEqual({ x: WINDOW_EDGE, y: WINDOW_EDGE });
    expect(clampWindow({ x: 1100, y: 600 }, size, SCREEN)).toEqual({ x: 1280 - WINDOW_EDGE - 400, y: 720 - WINDOW_EDGE - 300 });
    // Only the axis that is off screen moves.
    expect(clampWindow({ x: 500, y: 900 }, size, SCREEN)).toEqual({ x: 500, y: 412 });
  });

  it("brings a window saved on a big screen back on a small one (its title bar too)", () => {
    const size = { width: 440, height: 300 };
    const saved = { x: 1460, y: 820 }; // dragged there at 1920x1080
    const at = clampWindow(saved, size, SMALL);
    expect(at).toEqual({ x: 1024 - WINDOW_EDGE - 440, y: 640 - WINDOW_EDGE - 300 });
    expect(at.x).toBeGreaterThanOrEqual(0);
    expect(at.x + size.width).toBeLessThanOrEqual(SMALL.width);
    expect(at.y).toBeGreaterThanOrEqual(0);
  });

  it("keeps the top-left corner (the title bar) on screen for a window bigger than the screen", () => {
    expect(clampWindow({ x: 300, y: 200 }, { width: 2000, height: 1000 }, SCREEN)).toEqual({ x: 0, y: 0 });
    expect(clampWindow({ x: -300, y: -200 }, { width: 2000, height: 1000 }, SCREEN)).toEqual({ x: 0, y: 0 });
    // Just too big for the edges: flush with the far edge, never past the near one.
    expect(clampWindow({ x: 50, y: 0 }, { width: 1270, height: 100 }, SCREEN)).toEqual({ x: 2, y: WINDOW_EDGE });
  });

  it("rounds to whole pixels", () => {
    expect(clampWindow({ x: 100.4, y: 99.6 }, { width: 10, height: 10 }, SCREEN)).toEqual({ x: 100, y: 100 });
  });
});

describe("windowCap", () => {
  it("is the screen less an edge on each side", () => {
    expect(windowCap(SCREEN)).toEqual({ width: 1280 - 2 * WINDOW_EDGE, height: 720 - 2 * WINDOW_EDGE });
    expect(windowCap({ width: 10, height: 10 })).toEqual({ width: 0, height: 0 });
  });
});

describe("dragTo", () => {
  const size = { width: 400, height: 300 };

  it("moves the window with the pointer", () => {
    expect(dragTo({ x: 100, y: 100 }, { x: 150, y: 110 }, { x: 250, y: 160 }, size, SCREEN)).toEqual({ x: 200, y: 150 });
  });

  it("stops at the screen's edges however far the pointer goes, title bar fully on screen", () => {
    const start = { x: 868, y: 135 };
    const at = dragTo(start, { x: 880, y: 150 }, { x: -50, y: 2000 }, size, SCREEN);
    expect(at).toEqual({ x: WINDOW_EDGE, y: 720 - WINDOW_EDGE - 300 });
    const up = dragTo(start, { x: 880, y: 150 }, { x: 5000, y: -900 }, size, SCREEN);
    expect(up).toEqual({ x: 1280 - WINDOW_EDGE - 400, y: WINDOW_EDGE });
  });
});

describe("parsePositions", () => {
  it("reads saved positions by kind", () => {
    expect(parsePositions('{"skills":{"x":8,"y":247},"mail-window":{"x":30,"y":40}}')).toEqual({ skills: { x: 8, y: 247 }, "mail-window": { x: 30, y: 40 } });
  });

  it("drops what is not a position, and survives nothing or garbage", () => {
    expect(parsePositions(null)).toEqual({});
    expect(parsePositions("not json")).toEqual({});
    expect(parsePositions("[1,2]")).toEqual({});
    expect(parsePositions('{"a":{"x":"1","y":2},"b":null,"c":{"x":1},"d":{"x":1,"y":2}}')).toEqual({ d: { x: 1, y: 2 } });
  });
});

describe("windowKind", () => {
  // A stand-in for an element: its classes and attributes.
  const el = (classes: string, attrs: Record<string, string> = {}) =>
    ({ classList: classes.split(" "), getAttribute: (k: string) => attrs[k] ?? null, tagName: "SECTION" }) as unknown as Element;

  it("names a window by its own classes, not the shared ones or the placer's", () => {
    expect(windowKind(el("econ-window mail-window panel"))).toBe("mail-window");
    expect(windowKind(el("econ-window player-card panel win-capped"))).toBe("player-card");
    // The Wayfinder Charm window and the lore book share a class but are different windows.
    expect(windowKind(el("monstra-viam panel"))).toBe("monstra-viam");
    expect(windowKind(el("monstra-viam lore panel"))).toBe("monstra-viam.lore");
    expect(windowKind(el("menu panel"))).toBe("menu");
  });

  it("takes a window's data-window name first", () => {
    expect(windowKind(el("econ-window mail-window panel", { "data-window": "outbox" }))).toBe("outbox");
    expect(windowKind(el("panel"))).toBe("section");
  });
});
