// Selecting characters (ui/selection-state.ts).

import { describe, expect, it } from "vitest";
import { CLICK_MS, eAction, isClick, isDoubleClick, KEEP_RANGE_M, nextTarget, Selection, TAB_RANGE_M, tabIsOurs, tabTargets, type SelectTarget } from "./selection-state";

const who = (id: number, x: number, z: number, npc = false, y = 0): SelectTarget => ({ id, name: npc ? `Professor ${id}` : `P${id}`, npc, x, y, z });
const FEET: [number, number, number] = [0, 0, 0];

describe("selecting characters", () => {
  it("tells a click from a drag to look round, and a double-click from two clicks", () => {
    expect(isClick({ x: 100, y: 100, t: 0 }, { x: 103, y: 102, t: 120 })).toBe(true);
    expect(isClick({ x: 100, y: 100, t: 0 }, { x: 140, y: 100, t: 120 })).toBe(false); // dragged the camera
    expect(isClick({ x: 100, y: 100, t: 0 }, { x: 100, y: 100, t: CLICK_MS + 1 })).toBe(false); // held
    expect(isDoubleClick(null, { x: 0, y: 0, t: 0 })).toBe(false);
    expect(isDoubleClick({ x: 100, y: 100, t: 1000 }, { x: 104, y: 101, t: 1250 })).toBe(true);
    expect(isDoubleClick({ x: 100, y: 100, t: 1000 }, { x: 104, y: 101, t: 1600 })).toBe(false);
    expect(isDoubleClick({ x: 100, y: 100, t: 1000 }, { x: 160, y: 101, t: 1200 })).toBe(false);
  });

  it("Tab steps through players and NPCs nearby, nearest first, and round again", () => {
    const all = [who(1, 10, 0), who(2, 3, 0, true), who(3, 0, 6), who(4, TAB_RANGE_M + 1, 0), who(5, 2, 2, false, 9)];
    const order = tabTargets(all, FEET);
    // Too far, or a floor up: left out.
    expect(order.map((t) => t.id)).toEqual([2, 3, 1]);
    expect(nextTarget(order, null)?.id).toBe(2);
    expect(nextTarget(order, 2)?.id).toBe(3);
    expect(nextTarget(order, 1)?.id).toBe(2);
    expect(nextTarget(order, 3, -1)?.id).toBe(2);
    expect(nextTarget(order, null, -1)?.id).toBe(1);
    // The selected one walked out of reach: Tab starts at the nearest again.
    expect(nextTarget(order, 4)?.id).toBe(2);
    expect(nextTarget([], null)).toBeNull();
    // Equal distances keep a steady order.
    expect(tabTargets([who(9, 5, 0), who(7, 0, 5)], FEET).map((t) => t.id)).toEqual([7, 9]);
  });

  it("E opens a selected player's card; on an NPC or with nobody selected it does what it always did", () => {
    expect(eAction(who(1, 0, 0))).toBe("card");
    expect(eAction(who(2, 0, 0, true))).toBe("pass");
    expect(eAction(null)).toBe("pass");
  });

  it("follows the selected character, drops it when it leaves, and Esc clears it", () => {
    const s = new Selection();
    expect(s.clear()).toBe(false);
    s.select(who(1, 2, 2));
    expect(s.id).toBe(1);
    expect(s.follow([who(1, 4, 5)], FEET)).toMatchObject({ id: 1, x: 4, z: 5 });
    expect(s.selected?.x).toBe(4);
    // Out of view: gone.
    expect(s.follow([who(2, 1, 1)], FEET)).toBeNull();
    expect(s.id).toBeNull();
    s.select(who(1, 2, 2));
    expect(s.follow([who(1, KEEP_RANGE_M + 5, 0)], FEET)).toBeNull();
    s.select(who(3, 1, 1));
    expect(s.clear()).toBe(true);
    expect(s.selected).toBeNull();
  });

  it("takes Tab from the page while playing, never while typing", () => {
    expect(tabIsOurs({ key: "Tab" }, false)).toBe(true);
    expect(tabIsOurs({ key: "Tab" }, true)).toBe(false);
    expect(tabIsOurs({ key: "Tab", altKey: true }, false)).toBe(false);
    expect(tabIsOurs({ key: "e" }, false)).toBe(false);
  });
});
