// Item 8: opening a window closes the others (ui/windows.ts), the pure parts.

import { describe, expect, it } from "vitest";
import { StackOrder, toClose } from "./windows";

describe("the window stack", () => {
  it("keeps the last opened (or clicked) window on top, and Esc's target is the top one", () => {
    const s = new StackOrder<string>();
    expect(s.top).toBeNull();
    s.raise("result card");
    s.raise("map");
    expect(s.order).toEqual(["result card", "map"]);
    expect(s.top).toBe("map");
    // A click in the result card brings it up; nothing closes.
    s.raise("result card");
    expect(s.order).toEqual(["map", "result card"]);
    s.remove(s.top!);
    expect(s.top).toBe("map");
    s.remove("not open");
    expect(s.size).toBe(1);
    expect(s.has("map")).toBe(true);
  });

  it("closes every other window that can close when one opens", () => {
    const closable = (w: string) => w !== "trade" && w !== "result card";
    expect(toClose("friends", ["wardrobe", "map", "friends"], closable)).toEqual(["wardrobe", "map"]);
    // The trade window and a result card (no close button) stay.
    expect(toClose("map", ["trade", "wardrobe", "result card", "map"], closable)).toEqual(["wardrobe"]);
    // Nothing else open: nothing to close.
    expect(toClose("map", ["map"], closable)).toEqual([]);
  });
});
