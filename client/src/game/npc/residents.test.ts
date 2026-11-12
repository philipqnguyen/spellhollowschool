// Who may be targeted and whom E talks to (residents.ts), which talking
// portraits come alive (portrait-state.ts), and the NPC styles from palette
// data (styles.ts).

import { describe, expect, it } from "vitest";
import type { PortraitView } from "../../wasm/sim_wasm";
import { hasPlate, selectable, talksByNpcInteract, talkTarget, type TalkEntity } from "./residents";
import { DRAW_M, MAX_SUBJECTS, nodDip, NOD_MS, subjectStates } from "./portrait-state";
import { canvasPlanes, ghostLook, ghostStyle, portraitLook, portraitStyle, STUDENT_STYLE } from "./styles";

describe("targeting", () => {
  it("never selects, targets or plates a background student; everyone else as before", () => {
    expect(selectable("student")).toBe(false);
    expect(hasPlate("student")).toBe(false);
    for (const k of [null, "ghost", "staff", "shopkeeper"] as const) {
      expect(selectable(k)).toBe(true);
      expect(hasPlate(k)).toBe(true);
    }
  });

  it("talks to ghosts, staff and students by NpcInteract, to shopkeepers through their station", () => {
    expect(talksByNpcInteract("ghost")).toBe(true);
    expect(talksByNpcInteract("staff")).toBe(true);
    expect(talksByNpcInteract("student")).toBe(true);
    expect(talksByNpcInteract("shopkeeper")).toBe(false);
    expect(talksByNpcInteract(null)).toBe(false);
  });
});

describe("whom E talks to", () => {
  const me = { x: 0, y: 0, z: 0 };
  const ent = (id: number, x: number, y = 0, kind: TalkEntity["kind"] = "staff"): TalkEntity => ({ id, npc: `n${id}`, kind, x, y, z: 0 });
  const wall: PortraitView = { npc: "p", pos: [2.5, 2.2, 0], yaw: 0, size: [1, 1.3], backdrop: 0, level: 0 };

  it("picks the nearest within the talk radius, on our floor only", () => {
    expect(talkTarget(me, [ent(1, 2), ent(2, 1)], [], 3, "on")).toMatchObject({ kind: "entity", id: 2 });
    expect(talkTarget(me, [ent(1, 3.5)], [], 3, "on")).toBeNull();
    expect(talkTarget(me, [ent(1, 1, 5)], [], 3, "on")).toBeNull();
  });

  it("prefers the selected resident when it is in reach", () => {
    expect(talkTarget(me, [ent(1, 2.5), ent(2, 1)], [], 3, "on", 1)).toMatchObject({ id: 1 });
    expect(talkTarget(me, [ent(1, 4), ent(2, 1)], [], 3, "on", 1)).toMatchObject({ id: 2 });
  });

  it("includes a portrait unless chatter is Off (it stays alive, but keeps its words)", () => {
    expect(talkTarget(me, [], [wall], 3, "on")).toMatchObject({ kind: "portrait", npc: "p" });
    expect(talkTarget(me, [], [wall], 3, "quiet")).toMatchObject({ kind: "portrait", npc: "p" });
    expect(talkTarget(me, [], [wall], 3, "off")).toBeNull();
    // A portrait on the floor above is not ours.
    expect(talkTarget({ x: 0, y: -9, z: 0 }, [], [wall], 3, "on")).toBeNull();
    // Nor one on the far side of its wall (the room next door).
    expect(talkTarget({ x: 2.5, y: 0, z: -1 }, [], [wall], 3, "on")).toBeNull();
    expect(talkTarget({ x: 2.5, y: 0, z: 1 }, [], [wall], 3, "on")).toMatchObject({ kind: "portrait", npc: "p" });
  });

  it("lets a student bark (talk), though it can never be selected", () => {
    expect(talkTarget(me, [ent(9, 1, 0, "student")], [], 3, "on")).toMatchObject({ id: 9, npcKind: "student" });
  });
});

describe("talking portraits come alive near us", () => {
  const rules = { alive_radius_m: 14, look_radius_m: 7 };
  // A row of portraits on a north wall facing +z, 5 m apart, canvases 2 m up.
  const row: PortraitView[] = Array.from({ length: 8 }, (_, i) => ({ npc: `p${i}`, pos: [i * 5, 2, 0], yaw: 0, size: [1, 1.3], backdrop: 0, level: 0 }));

  it("draws the nearest few on our floor, alive within 14 m, looking within 7 m", () => {
    const s = subjectStates(row, [0, 0, 3], rules);
    expect(s.length).toBe(MAX_SUBJECTS);
    expect(s.map((x) => x.index)).toEqual([0, 1, 2, 3]);
    expect(s.map((x) => x.looks)).toEqual([true, true, false, false]);
    expect(s.map((x) => x.alive)).toEqual([true, true, true, false]);
  });

  it("draws none from another floor, from behind the wall, beyond the draw range, or outside the castle", () => {
    expect(subjectStates(row, [0, 9, 3], rules)).toEqual([]);
    expect(subjectStates(row, [0, 0, -3], rules)).toEqual([]);
    expect(subjectStates(row, [0, 0, DRAW_M + 1], rules)).toEqual([]);
    expect(subjectStates(row, null, rules)).toEqual([]);
  });

  it("nods down and back up within the nod's time", () => {
    expect(nodDip(0, -1)).toBe(0);
    expect(nodDip(0, NOD_MS / 2)).toBeCloseTo(1);
    expect(nodDip(0, NOD_MS)).toBe(0);
  });
});

describe("NPC styles from palette data", () => {
  it("reads the ghosts' and portraits' looks from the toon palette, with fallbacks", () => {
    const g = ghostLook();
    expect(g.alpha).toBeGreaterThan(0);
    expect(g.alpha).toBeLessThan(1);
    expect(g.tint).toHaveLength(3);
    expect(ghostLook({}).alpha).toBe(0.5);
    expect(ghostLook({ npcs: { ghost: { alpha: 7, tint: [2, 0, 0] } } })).toMatchObject({ alpha: 1, tint: [1, 1, 1] });
    expect(portraitLook().tint).toHaveLength(3);
  });

  it("floats a ghost without shadow or outline, and keeps a student cheap far off", () => {
    const s = ghostStyle(0.25);
    expect(s).toMatchObject({ lift: 0.25, shadow: false, outline: false });
    expect(STUDENT_STYLE.coarseBeyond).toBe(10);
    expect(STUDENT_STYLE.tweak).toBeNull();
  });

  it("clips a portrait's subject to its canvas (Babylon clips where the plane's value is positive)", () => {
    const c = { pos: [10, 3, -4], yaw: Math.PI / 2, size: [1, 1.2] };
    const planes = canvasPlanes(c);
    const value = (p: (typeof planes)[number], x: number, y: number, z: number) => p.normal.x * x + p.normal.y * y + p.normal.z * z + p.d;
    // Inside the canvas: kept by all four.
    for (const p of planes) expect(value(p, 10.2, 3, -4.3)).toBeLessThan(0);
    // Below it (the legs): clipped.
    expect(planes.some((p) => value(p, 10.2, 2, -4) > 0)).toBe(true);
    // Above it, and beyond either side along the wall (yaw 90°: the wall runs along z).
    expect(planes.some((p) => value(p, 10.2, 3.8, -4) > 0)).toBe(true);
    expect(planes.some((p) => value(p, 10.2, 3, -4.7) > 0)).toBe(true);
    expect(planes.some((p) => value(p, 10.2, 3, -3.3) > 0)).toBe(true);
    expect(portraitStyle("p", c)).toMatchObject({ key: "npc-portrait-p", shadow: false, outline: false });
  });
});
