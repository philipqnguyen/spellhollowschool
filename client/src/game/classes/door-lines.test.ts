// The class doors' lines in the chat: once per session, across sign-ins.

import { describe, expect, it } from "vitest";
import { DOOR_LINES_KEY, DoorLines, type LineStore } from "./door-lines";

function memoryStore(): LineStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
}

describe("door lines", () => {
  it("says each session's line once", () => {
    const lines = new DoorLines("Ivy_Thornfield", memoryStore());
    expect(lines.due("charms", 40)).toBe(true);
    lines.said("charms", 40);
    expect(lines.due("charms", 40)).toBe(false);
    // Another class, and the next session of the same one, are still due.
    expect(lines.due("potions", 40)).toBe(true);
    expect(lines.due("charms", 41)).toBe(true);
    // An older session (a clock that went back) is not said again.
    expect(lines.due("charms", 39)).toBe(false);
  });

  it("remembers them across a sign-in (a new game reading the same storage)", () => {
    const store = memoryStore();
    const before = new DoorLines("Ivy_Thornfield", store);
    before.said("creatures", 12);
    before.said("herbology", 12);
    const after = new DoorLines("Ivy_Thornfield", store);
    expect(after.due("creatures", 12)).toBe(false);
    expect(after.due("herbology", 12)).toBe(false);
    expect(after.due("astronomy", 12)).toBe(true);
    // Another character on the same browser has heard nothing yet.
    expect(new DoorLines("Rowan_Ashby", store).due("creatures", 12)).toBe(true);
  });

  it("keeps a few characters and drops the oldest", () => {
    const store = memoryStore();
    for (let i = 0; i < 12; i++) new DoorLines(`C${i}`, store).said("charms", 5);
    const all = JSON.parse(store.data.get(DOOR_LINES_KEY)!) as Record<string, unknown>;
    expect(Object.keys(all)).toEqual(["C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11"]);
  });

  it("works without storage, or with broken or throwing storage", () => {
    const none = new DoorLines("Ivy", null);
    none.said("charms", 1);
    expect(none.due("charms", 1)).toBe(false);
    const junk = memoryStore();
    junk.data.set(DOOR_LINES_KEY, "{not json");
    const fromJunk = new DoorLines("Ivy", junk);
    expect(fromJunk.due("charms", 1)).toBe(true);
    fromJunk.said("charms", 1);
    expect(new DoorLines("Ivy", junk).due("charms", 1)).toBe(false);
    const throwing: LineStore = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    const blocked = new DoorLines("Ivy", throwing);
    expect(blocked.due("charms", 1)).toBe(true);
    blocked.said("charms", 1);
    expect(blocked.due("charms", 1)).toBe(false);
  });
});
