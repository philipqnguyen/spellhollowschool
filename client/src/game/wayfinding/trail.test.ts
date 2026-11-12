import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import keyPlacesText from "../../../../data/world/key_places.json?raw";
import layoutText from "../../../../data/world/layout.json?raw";
import keepOutData from "../../../../data/world/keep_out.json";
import { initSync, Wayfinder, type House } from "../../wasm/sim_wasm";
import { AHEAD_M, ARRIVE_M, arrived, distanceText, flatLengthM, joinVia, makePath, NEAR_M, pointAt, project, remainingM, strayed, VIA_SLACK_M, WISP_SPACING, wisps, type Wisp } from "./trail-path";

const HOUSES: House[] = ["roarwing", "burrowsby", "skytalon", "serpentyne"];
const keepOut = JSON.stringify((keepOutData as { keep_out?: unknown[] }).keep_out ?? []);

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

/** An L: 10 m east, then 10 m up a stair going south (rising 5 m). */
const L = makePath([0, 0, 0, 10, 0, 0, 10, 5, 10]);

describe("the trail's geometry", () => {
  it("measures arc length with the climb and finds points along it", () => {
    expect(L.length).toBeCloseTo(10 + Math.hypot(5, 10), 4);
    const p: [number, number, number] = [0, 0, 0];
    expect(pointAt(L, 5, p)).toEqual([5, 0, 0]);
    expect(pointAt(L, -1, p)).toEqual([0, 0, 0]);
    expect(pointAt(L, 999, p)).toEqual([10, 5, 10]);
  });

  it("projects the player onto the nearest leg, and the storey counts", () => {
    const a = project(L, 4, 0, 1);
    expect(a.s).toBeCloseTo(4, 4);
    expect(a.off).toBeCloseTo(1, 4);
    expect(strayed(a)).toBe(false);
    expect(remainingM(L, a)).toBeCloseTo(L.length - 4 + 1, 4);
    // Off to the side: strayed.
    expect(strayed(project(L, 4, 0, 4))).toBe(true);
    // Right below the stair's top, but a storey down: strayed.
    expect(strayed(project(L, 10, 0, 10))).toBe(true);
  });

  it("knows arrival by plan distance and storey", () => {
    expect(arrived(L, 10, 5, 10 - ARRIVE_M + 0.1)).toBe(true);
    expect(arrived(L, 10, 0, 10)).toBe(false);
    expect(arrived(L, 0, 0, 0)).toBe(false);
  });

  it("lays wisps a few metres ahead at fixed places, rounded and staggered", () => {
    const out: Wisp[] = [];
    const n = wisps(L, 2, out);
    expect(n).toBeGreaterThan(8);
    expect(out[0]!.k * WISP_SPACING).toBeGreaterThanOrEqual(2 + NEAR_M - 1e-6);
    expect(out[n - 1]!.k * WISP_SPACING - 2).toBeLessThanOrEqual(AHEAD_M + 1e-6);
    // Wisps stay put as the player walks: the same k lands in the same place.
    const again: Wisp[] = [];
    const m = wisps(L, 2.3, again);
    const shared = again.slice(0, m).find((w) => w.k === out[3]!.k)!;
    expect(shared.x).toBeCloseTo(out[3]!.x, 6);
    expect(shared.z).toBeCloseTo(out[3]!.z, 6);
    // Alternate wisps step to either side of the line (footprints).
    expect(Math.sign(out[0]!.z)).not.toBe(Math.sign(out[1]!.z));
    // Faded in near the player, full further on.
    expect(out[0]!.alpha).toBeLessThan(1);
    expect(Math.max(...out.slice(0, n).map((w) => w.alpha))).toBe(1);
    // Near the end, the last wisp sits on the end.
    const tail: Wisp[] = [];
    const t = wisps(L, L.length - 3, tail);
    expect(tail[t - 1]!.x).toBeCloseTo(10, 4);
    expect(tail[t - 1]!.z).toBeCloseTo(10, 4);
  });

  it("words distances for the chip", () => {
    expect(distanceText(0.2)).toBe("1 m");
    expect(distanceText(8.4)).toBe("8 m");
    expect(distanceText(122)).toBe("120 m");
    expect(distanceText(347)).toBe("350 m");
    expect(distanceText(NaN)).toBe("");
  });
});

describe("a trail by way of a point", () => {
  it("joins the lead and the rest while the point saves nothing, and goes direct once the player is as near", () => {
    const direct = [0, 0, 0, 10, 0, 0];
    const lead = [0, 0, 0, 0, 0, 5];
    const rest = [0, 0, 5, 10, 0, 5, 10, 0, 0];
    expect(flatLengthM(rest)).toBe(15);
    // The point's way to the place (15 m) is longer than ours (10 m) minus the slack: not worth it.
    expect(joinVia(direct, lead, rest)).toBeNull();
    // A long way round from here (the doors are on the way): lead then rest, the point once.
    const far = [0, 0, 0, 0, 0, 30, 10, 0, 30];
    expect(joinVia(far, lead, rest)).toEqual([0, 0, 0, 0, 0, 5, 10, 0, 5, 10, 0, 0]);
    // Within the slack of the point's own way: direct.
    expect(joinVia([0, 0, 0, 15 + VIA_SLACK_M, 0, 0], lead, rest)).toBeNull();
    // A missing leg: direct.
    expect(joinVia(far, undefined, rest)).toBeNull();
    expect(joinVia(far, lead, undefined)).toBeNull();
  });
});

describe("the castle's trails (sim-core's graph through wasm)", () => {
  it("offers each house its own key places and reaches every one from the spawns", () => {
    for (const house of HOUSES) {
      const w = new Wayfinder(layoutText, house, keyPlacesText, keepOut);
      const places = w.places();
      expect(places.filter((p) => p.group === "dorm").map((p) => p.id)).toEqual([`dorm_${house}`]);
      expect(places.filter((p) => p.group === "common_room").map((p) => p.id)).toEqual([`common_room_${house}`]);
      // The Owl Order Catalogue: the stand in the house's own common room.
      expect(places.filter((p) => p.group === "owl_order").map((p) => [p.id, p.name])).toEqual([[`owl_order_${house}`, "Owl Order Catalogue"]]);
      for (const p of places) {
        for (const spawn of [[14, 0, 32], [18, 0, 32]] as const) {
          const flat = w.pathTo(p.id, spawn[0], spawn[1], spawn[2]);
          expect(flat, `${house} -> ${p.id}`).toBeDefined();
          const path = makePath(flat!);
          expect(path.length).toBeGreaterThan(0);
          // The trail ends at the place.
          expect(arrived(path, p.pos[0], p.pos[1], p.pos[2])).toBe(true);
        }
      }
      // Never another house's rooms.
      expect(w.pathTo("dorm_roarwing", 14, 0, 32) === undefined).toBe(house !== "roarwing");
      w.free();
    }
  });

  it("walks a believable distance to the Owl Exchange and a class door", () => {
    const w = new Wayfinder(layoutText, "skytalon", keyPlacesText, keepOut);
    const owl = makePath(w.pathTo("owl_exchange", 14, 0, 32)!);
    // The Exchange is 10 m from the spawn as the crow flies.
    expect(owl.length).toBeGreaterThan(9);
    expect(owl.length).toBeLessThan(40);
    const charms = makePath(w.pathTo("class_charms", 14, 0, 32)!);
    // Three floors up: the trail climbs.
    expect(charms.pts[charms.pts.length - 2]).toBeCloseTo(27, 1);
    expect(charms.length).toBeGreaterThan(60);
    w.free();
  });
});

describe("arrival at a door", () => {
  it("arrives within reach of every key place door, so its E prompt shows", () => {
    const layout = JSON.parse(layoutText) as { interactables: { id: string; radius: number }[] };
    const places = (JSON.parse(keyPlacesText) as { places: { target: { interactable?: string } }[] }).places;
    const doors = places.map((p) => p.target.interactable).filter((id): id is string => !!id);
    expect(doors.length).toBeGreaterThan(0);
    for (const id of doors) {
      const door = layout.interactables.find((i) => i.id === id);
      expect(door, id).toBeDefined();
      expect(ARRIVE_M, id).toBeLessThan(door!.radius);
    }
  });
});
