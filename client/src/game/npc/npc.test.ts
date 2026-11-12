import { describe, expect, it } from "vitest";
import type { EntityInfo } from "../../wasm/sim_wasm";
import { SchoolRoster } from "./roster";
import { bubbleY } from "./bubbles";

const info = (id: number, level: number, effectsVersion: number, npc: string | null = null): EntityInfo =>
  ({ id, character: { name: `c${id}`, house: "skytalon" }, appearanceVersion: 1, appearance: {} as never, effectsVersion, effects: [], level, npc }) as EntityInfo;

describe("the school roster", () => {
  it("keeps levels and NPC ids from snapshots", () => {
    const r = new SchoolRoster();
    r.snapshot([info(1, 4, 1), info(2, 0, 0, "charms_professor")], []);
    expect(r.level(1)).toBe(4);
    expect(r.npc(1)).toBeNull();
    expect(r.npc(2)).toBe("charms_professor");
    expect([...r.npcs()]).toEqual([[2, "charms_professor"]]);
    r.snapshot([], [1]);
    expect(r.level(1)).toBe(0);
  });

  it("takes LevelChanged unless an older look arrives after it", () => {
    const r = new SchoolRoster();
    r.snapshot([info(1, 4, 3)], []);
    r.levelChanged(1, 5, 4);
    expect(r.level(1)).toBe(5);
    // A cached entry from before the level-up does not undo it.
    r.snapshot([info(1, 4, 3)], []);
    expect(r.level(1)).toBe(5);
    r.levelChanged(1, 3, 2);
    expect(r.level(1)).toBe(5);
    r.clear();
    expect(r.level(1)).toBe(0);
  });
});

describe("speech bubbles", () => {
  it("keep their top edge below the HUD's top bar and strip", () => {
    // A professor near the top of the view: the bubble (60 px tall) drops below the strip (96 px).
    expect(bubbleY(20, 60, 800, 96)).toBe(96 + 60 + 8);
    // Lower down, it stays over the speaker.
    expect(bubbleY(400, 60, 800, 96)).toBe(400);
    // And never off the bottom.
    expect(bubbleY(900, 60, 800, 96)).toBe(788);
  });
});
