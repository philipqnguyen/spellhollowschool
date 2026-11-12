import { describe, expect, it } from "vitest";
import { FADE_MS, NEW_PER_FRAME, PlateSight, RECHECK_MS, RETEST_PER_FRAME, type SightTarget, type SightTest } from "./plate-sight";
import { levelSightTest, segmentHitsBox, soupBlocks, soupFromTriangles, type TriangleSoup } from "../game/plate-sight-ray";
import { BUBBLE_ABOVE_PLATE, BUBBLE_SIGHT_DROP } from "../game/npc/bubbles";

// A room (x < 5, y 0..4) with a wall at x = 5 into the room next door and a
// floor at y = 0 over the floor below. The camera stands in the room.
const quad = (a: number[], b: number[], c: number[], d: number[]): number[] => [...a, ...b, ...c, ...a, ...c, ...d];
const WALL = soupFromTriangles(new Float32Array(quad([5, -1, -10], [5, -1, 10], [5, 5, 10], [5, 5, -10])));
const FLOOR = soupFromTriangles(new Float32Array(quad([-10, 0, -10], [10, 0, -10], [10, 0, 10], [-10, 0, 10])));

function levelOf(soups: TriangleSoup[]): SightTest {
  return (ax, ay, az, bx, by, bz) => {
    const dx = bx - ax;
    const dy = by - ay;
    const dz = bz - az;
    const len = Math.hypot(dx, dy, dz);
    const [ux, uy, uz] = [dx / len, dy / len, dz / len];
    return soups.some((s) => soupBlocks(s, ax, ay, az, ux, uy, uz, 1 / ux, 1 / uy, 1 / uz, len));
  };
}

const EYE: [number, number, number] = [0, 2.5, 0];
/** Plate anchors (2.1 m over the feet): in the room, through the wall, on the floor below. */
const IN_ROOM: SightTarget = { id: 1, x: 2, y: 2.1, z: 3 };
const NEXT_DOOR: SightTarget = { id: 2, x: 8, y: 2.1, z: 0 };
const BELOW: SightTarget = { id: 3, x: 2, y: -4.5 + 2.1, z: 2 };

/** Runs `frames` frames 16 ms apart from `start`, returning the last opacities. */
function run(sight: PlateSight, targets: SightTarget[], frames: number, start = 0): number[] {
  const out: number[] = [];
  for (let f = 0; f < frames; f++) sight.update(targets, EYE, start + f * 16, out);
  return out;
}

describe("nameplate line of sight", () => {
  const level = levelOf([WALL, FLOOR]);

  it("shows a name whose head is in plain sight, fading in", () => {
    const sight = new PlateSight(level);
    const first = run(sight, [IN_ROOM], 1);
    expect(first[0]).toBe(0);
    const partway = run(sight, [IN_ROOM], 5, 16);
    expect(partway[0]).toBeGreaterThan(0);
    expect(partway[0]).toBeLessThan(1);
    expect(run(sight, [IN_ROOM], Math.ceil(FADE_MS / 16) + 2, 100)[0]).toBe(1);
  });

  it("never shows a name behind a wall", () => {
    const sight = new PlateSight(level);
    for (let f = 0; f < 60; f++) expect(sight.update([NEXT_DOOR], EYE, f * 16, [])[0]).toBe(0);
  });

  it("never shows a name on another floor, through the floor", () => {
    const sight = new PlateSight(level);
    for (let f = 0; f < 60; f++) expect(sight.update([BELOW], EYE, f * 16, [])[0]).toBe(0);
  });

  it("shows the room-next-door's name once the wall is gone (the test, not the distance, hides it)", () => {
    const sight = new PlateSight(levelOf([FLOOR]));
    expect(run(sight, [NEXT_DOOR], 30)[0]).toBe(1);
  });

  it("fades a name out when its owner walks behind the wall", () => {
    const sight = new PlateSight(level);
    const walker = { ...IN_ROOM };
    expect(run(sight, [walker], 30)[0]).toBe(1);
    walker.x = 8;
    walker.z = 0;
    // Retested within RECHECK_MS (plus its stagger), then eased out over FADE_MS.
    const fading: number[] = [];
    for (let t = 30 * 16; t < 30 * 16 + RECHECK_MS + 80 + FADE_MS + 64; t += 16) fading.push(sight.update([walker], EYE, t, [])[0]!);
    expect(fading.some((a) => a > 0 && a < 1)).toBe(true);
    expect(fading[fading.length - 1]).toBe(0);
  });

  it("is never shown before it has been tested, and rations the rays", () => {
    let rays = 0;
    const sight = new PlateSight((...a) => {
      rays++;
      return level(...a);
    });
    const crowd: SightTarget[] = [];
    for (let i = 0; i < 30; i++) crowd.push({ id: 100 + i, x: -4 + (i % 6), y: 2.1, z: -4 + Math.floor(i / 6) });
    const out = sight.update(crowd, EYE, 0, []);
    expect(rays).toBeLessThanOrEqual(NEW_PER_FRAME + RETEST_PER_FRAME);
    expect(out.every((a) => a === 0)).toBe(true);
    // A second of frames: everyone answered and shown, at no more than the ration a frame.
    let most = 0;
    for (let f = 1; f < 60; f++) {
      const before = rays;
      sight.update(crowd, EYE, f * 16, out);
      most = Math.max(most, rays - before);
    }
    expect(most).toBeLessThanOrEqual(NEW_PER_FRAME + RETEST_PER_FRAME);
    expect(out.every((a) => a === 1)).toBe(true);
  });

  it("retests everyone at once after a camera cut", () => {
    const sight = new PlateSight(level);
    expect(run(sight, [IN_ROOM], 30)[0]).toBe(1);
    // Teleported into the room next door: the name in our old room is now behind the wall.
    const out: number[] = [];
    const eye: [number, number, number] = [8, 2.5, 3];
    // Gone on the first frame after the cut: no fade through the wall.
    sight.update([IN_ROOM], eye, 1000, out);
    expect(out[0]).toBe(0);
    for (let f = 1; f < Math.ceil(FADE_MS / 16) + 3; f++) sight.update([IN_ROOM], eye, 1000 + f * 16, out);
    expect(out[0]).toBe(0);
  });

  it("without a level to test against, hides nothing", () => {
    expect(run(new PlateSight(null), [NEXT_DOOR, BELOW], 30)).toEqual([1, 1]);
  });
});

describe("speech bubbles' line of sight", () => {
  const level = levelOf([WALL, FLOOR]);
  /** A bubble's anchor over a speaker whose plate anchor is `t`. */
  const bubble = (t: SightTarget): SightTarget => ({ ...t, y: t.y + BUBBLE_ABOVE_PLATE });

  it("shows a professor's bubble in the room, hides one behind the wall or on the floor below", () => {
    const sight = new PlateSight(level, BUBBLE_SIGHT_DROP);
    expect(run(sight, [bubble(IN_ROOM), bubble(NEXT_DOOR), bubble(BELOW)], 30)).toEqual([1, 0, 0]);
  });

  it("aims at the speaker's head, not the bubble over it", () => {
    let aimedAt = NaN;
    const sight = new PlateSight((_ax, _ay, _az, _bx, by) => {
      aimedAt = by;
      return false;
    }, BUBBLE_SIGHT_DROP);
    sight.update([bubble(IN_ROOM)], EYE, 0, []);
    // The plate's anchor is 0.32 m over the crown: the ray ends inside the head (crown at 1.78 m), short of it.
    expect(aimedAt).toBeGreaterThan(1.3);
    expect(aimedAt).toBeLessThan(1.78);
  });
});

describe("the level a name is seen past", () => {
  /** A stand-in for a frozen Babylon mesh holding `tris` (world space). */
  const mesh = (name: string, tris: number[]) => {
    const xs = tris.filter((_, i) => i % 3 === 0);
    const ys = tris.filter((_, i) => i % 3 === 1);
    const zs = tris.filter((_, i) => i % 3 === 2);
    const v = (x: number[], y: number[], z: number[], f: (...n: number[]) => number) => ({ x: f(...x), y: f(...y), z: f(...z) });
    const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    return {
      name,
      material: {},
      isWorldMatrixFrozen: true,
      getBoundingInfo: () => ({ boundingBox: { minimumWorld: v(xs, ys, zs, Math.min), maximumWorld: v(xs, ys, zs, Math.max) } }),
      getVerticesData: () => tris,
      getIndices: () => null,
      getWorldMatrix: () => ({ m: identity }),
    } as never;
  };
  const wallTris = quad([5, -1, -10], [5, -1, 10], [5, 5, 10], [5, 5, -10]);

  it("is hidden by a wall, never by the furniture (a cell's castle_props)", () => {
    const head: [number, number, number] = [8, 1.6, 0];
    expect(levelSightTest({ walls: () => [mesh("great_hall.collider", wallTris)] })(...EYE, ...head)).toBe(true);
    expect(levelSightTest({ walls: () => [mesh("class_potions.castle_props", wallTris)] })(...EYE, ...head)).toBe(false);
    // A door is its own castle_props node and still hides what is behind it.
    expect(levelSightTest({ walls: () => [mesh("entrance_hall.castle_props.entrance_eh_door", wallTris)] })(...EYE, ...head)).toBe(true);
  });

  it("is hidden in a castle cell that is not shown, without a ray", () => {
    expect(levelSightTest({ walls: () => [], hiddenAt: () => true })(...EYE, 2, 1.6, 3)).toBe(true);
  });
});

describe("segment against a box", () => {
  const hits = (o: number[], to: number[], lo: number[], hi: number[]): boolean => {
    const d = [to[0]! - o[0]!, to[1]! - o[1]!, to[2]! - o[2]!];
    const len = Math.hypot(d[0]!, d[1]!, d[2]!);
    return segmentHitsBox(o[0]!, o[1]!, o[2]!, len / d[0]!, len / d[1]!, len / d[2]!, len, lo[0]!, lo[1]!, lo[2]!, hi[0]!, hi[1]!, hi[2]!);
  };

  it("meets a box it passes through, axis-aligned or not", () => {
    expect(hits([0, 0, 0], [10, 0, 0], [4, -1, -1], [5, 1, 1])).toBe(true);
    expect(hits([0, 0, 0], [10, 10, 10], [4, 4, 4], [5, 5, 5])).toBe(true);
  });

  it("misses a box beyond its end (the endless ray would not)", () => {
    // The lake under the castle: the ray down to a head would reach it, the segment stops short.
    expect(hits([0, 5, 0], [3, 1, 0], [-50, -20, -50], [50, -19, 50])).toBe(false);
  });

  it("misses a box beside it, and one behind its start", () => {
    expect(hits([0, 0, 0], [10, 0, 0], [4, 2, -1], [5, 3, 1])).toBe(false);
    expect(hits([0, 0, 0], [10, 0, 0], [-5, -1, -1], [-4, 1, 1])).toBe(false);
  });
});
