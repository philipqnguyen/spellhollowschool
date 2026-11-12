import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import keyPlacesText from "../../../../data/world/key_places.json?raw";
import layoutText from "../../../../data/world/layout.json?raw";
import { initSync, Wayfinder, type House } from "../../wasm/sim_wasm";
import { PROMPT_MIN_LEFT_MS, promptDoor, promptText, standsAt, Tour, TOUR_ASK, TOUR_INTRO, TOUR_LINES, TOUR_VIA_LANDMARK, tourKey, tourStops } from "./tour";
import { joinVia } from "./trail-path";

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

const STOPS = ["owl_exchange", "common_room_skytalon", "dorm_skytalon"];
/** A path's length (m), from the Wayfinder's flat x, y, z points. */
function length(path: Float32Array | undefined): number {
  if (!path) return Number.POSITIVE_INFINITY;
  let m = 0;
  for (let i = 3; i < path.length; i += 3) m += Math.hypot(path[i]! - path[i - 3]!, path[i + 1]! - path[i - 2]!, path[i + 2]! - path[i - 1]!);
  return m;
}

const key = (code: string, k = "", extra: Partial<KeyboardEvent> = {}) => ({ code, key: k || code, ...extra });

describe("the first-day tour", () => {
  it("leads in through the front doors to the Entrance Hall's Owl Exchange, then the common room's notice board, and ends at the dorm door", () => {
    const layout = JSON.parse(layoutText) as { spawns: { pos: [number, number, number] }[] };
    for (const house of ["roarwing", "burrowsby", "skytalon", "serpentyne"] as House[]) {
      const w = new Wayfinder(layoutText, house, keyPlacesText, "[]");
      const places = w.places();
      const stops = tourStops(places);
      expect(stops).toEqual(["owl_exchange", `common_room_${house}`, `dorm_${house}`]);
      // The common room stop ends at the house's notice board (data/world/key_places.json).
      const board = JSON.parse(keyPlacesText).places.find((p: { id: string }) => p.id === `common_room_${house}`);
      expect(board.target.interactable).toMatch(/^notice_board_house_[abcd]$/);
      // New characters arrive on the front steps, outside: the first stop is walked to, never said at once.
      const counter = places.find((p) => p.id === "owl_exchange")!;
      for (const s of layout.spawns) expect(standsAt(counter.pos, s.pos)).toBe(false);
      expect(standsAt(counter.pos, counter.pos)).toBe(true);
      // The spawns are outside the castle, beyond the front doors (`eh_front_doors`, z 40 to 42).
      for (const s of layout.spawns) expect(s.pos[2]).toBeGreaterThan(42);
      // One way inward: spawn -> counter -> common room -> dorm, each leg a trail, with no walk back.
      const toCounter = w.pathTo("owl_exchange", ...layout.spawns[0]!.pos);
      const common = places.find((p) => p.id === `common_room_${house}`)!;
      const toCommon = w.pathTo(`common_room_${house}`, ...counter.pos);
      const toDorm = w.pathTo(`dorm_${house}`, ...common.pos);
      expect(toCounter).toBeTruthy();
      expect(toCommon).toBeTruthy();
      expect(toDorm).toBeTruthy();
      // The first leg comes in through the doors: it ends in the hall, nearer the counter than it started.
      expect(length(toCounter)).toBeLessThan(60);
      // Review: by way of the landmark just inside the front doors from every spawn (the direct way takes the
      // Owl Gate beside them); the joined trail crosses the doors' surface (x 12 to 20, z 40 to 42).
      const doors = (JSON.parse(layoutText) as { landmarks: { id: string; pos: [number, number, number] }[] }).landmarks.find((l) => l.id === TOUR_VIA_LANDMARK)!;
      for (const s of layout.spawns) {
        const joined = joinVia(w.pathTo("owl_exchange", ...s.pos), w.path(...s.pos, ...doors.pos), w.pathTo("owl_exchange", ...doors.pos));
        expect(joined).not.toBeNull();
        let through = false;
        for (let i = 3; i < joined!.length; i += 3) {
          const [x0, z0, x1, z1] = [joined![i - 3]!, joined![i - 1]!, joined![i]!, joined![i + 2]!];
          if (z0 > 41 !== z1 > 41) through ||= Math.min(x0, x1) >= 12 && Math.max(x0, x1) <= 20;
        }
        expect(through).toBe(true);
      }
      // From inside the hall (in by the Owl Gate, or past the doors) the trail goes direct.
      for (const inside of [[2.5, 0, 38], [16, 0, 37]] as [number, number, number][]) {
        expect(joinVia(w.pathTo("owl_exchange", ...inside), w.path(...inside, ...doors.pos), w.pathTo("owl_exchange", ...doors.pos))).toBeNull();
      }
      // The common room is on the way to the dorm: the tour's two last legs are no longer than going to the dorm,
      // then the common room, then back (order) would be.
      const dorm = places.find((p) => p.id === `dorm_${house}`)!;
      const before = length(w.pathTo(`dorm_${house}`, ...counter.pos)) + length(w.pathTo(`common_room_${house}`, ...dorm.pos)) + length(w.pathTo("owl_exchange", ...common.pos));
      expect(length(toCommon) + length(toDorm)).toBeLessThan(before * 0.7);
      w.free();
    }
    expect(TOUR_INTRO).toContain("in through the front doors");
    expect(TOUR_INTRO).toContain("Owl Exchange");
    expect(TOUR_INTRO).toContain("Press X to skip");
    expect(TOUR_INTRO).not.toContain("Esc");
    expect(TOUR_LINES.owl_exchange).toContain("Entrance Hall");
    expect(TOUR_LINES.owl_exchange).toContain("Owl Exchange");
  });

  it("waits for the server's step and the castle, then leads stop by stop to step 1", () => {
    const t = new Tour();
    expect(t.shouldStart(true)).toBe(false); // step not known yet
    t.known(0);
    expect(t.shouldStart(false)).toBe(false); // still in the dorm
    expect(t.shouldStart(true)).toBe(true);
    expect(t.begin(STOPS)).toBe("owl_exchange");
    expect(t.running).toBe(true);
    expect(t.shouldStart(true)).toBe(false);
    expect(t.label).toBe("First-day tour, 1 of 3");
    // Arriving somewhere else is not the tour's business.
    expect(t.arrived("entrance_hall", "entrance_hall")).toBeNull();
    expect(t.arrived("owl_exchange", "owl_exchange")).toEqual({ line: TOUR_LINES.owl_exchange, next: "common_room_skytalon" });
    expect(t.label).toBe("First-day tour, 2 of 3");
    expect(t.arrived("common_room_skytalon", "common_room")?.next).toBe("dorm_skytalon");
    expect(t.promptDue).toBe(false);
    // The dorm door ends it.
    expect(t.arrived("dorm_skytalon", "dorm")).toEqual({ line: TOUR_LINES.dorm, next: null });
    expect(t.running).toBe(false);
    expect(t.step).toBe(1);
    expect(t.promptDue).toBe(true);
    expect(t.shouldStart(true)).toBe(false);
  });

  it("skips with X; Esc only asks, and a second Esc puts the question away", () => {
    expect(tourKey(key("KeyX", "x"), false)).toBe("skip");
    expect(tourKey(key("KeyX", "x"), true)).toBe("skip");
    expect(tourKey(key("KeyX", "x", { repeat: true }), false)).toBeNull();
    expect(tourKey(key("KeyX", "x", { ctrlKey: true }), false)).toBeNull();
    expect(tourKey(key("Escape", "Escape"), false)).toBe("ask");
    expect(tourKey(key("Escape", "Escape"), true)).toBe("dismiss");
    expect(tourKey(key("KeyW", "w"), false)).toBeNull();
    expect(TOUR_ASK).toMatch(/^Skip the tour\?/);
  });

  it("is skippable at any point, which leaves the class prompt due", () => {
    const t = new Tour();
    t.known(0);
    t.begin(STOPS);
    t.arrived("owl_exchange", "owl_exchange");
    expect(t.skip()).toBe(true);
    expect(t.running).toBe(false);
    expect(t.step).toBe(1);
    expect(t.skip()).toBe(false);
    expect(t.shouldStart(true)).toBe(false);
  });

  it("prompts once after the tour, followed or not, and never during it", () => {
    const t = new Tour();
    t.known(0);
    expect(t.takePrompt()).toBe(false); // not toured yet
    t.begin(STOPS);
    expect(t.takePrompt()).toBe(false); // touring
    t.skip();
    expect(t.takePrompt()).toBe(true);
    expect(t.step).toBe(2);
    expect(t.takePrompt()).toBe(false);
    expect(promptText("Potions")).toBe("Potions is starting: follow the trail");
  });

  it("names a door already open when the tour ends, if it stays open long enough to get there", () => {
    expect(promptDoor([])).toBeNull();
    // Just opened, or open a while: the one open longest still.
    const open = [
      { classId: "charms", leftMs: 90_000 },
      { classId: "potions", leftMs: 170_000 },
      { classId: "history", leftMs: 20_000 },
    ];
    expect(promptDoor(open)?.classId).toBe("potions");
    // About to close: not worth the walk; the next door to open takes the prompt instead.
    expect(promptDoor([{ classId: "history", leftMs: PROMPT_MIN_LEFT_MS - 1 }])).toBeNull();
    expect(promptDoor([{ classId: "history", leftMs: PROMPT_MIN_LEFT_MS }])?.classId).toBe("history");
  });

  it("follows the server: a step only rises, and a toured character never tours again", () => {
    const t = new Tour();
    t.known(2);
    t.known(0); // another device's late answer cannot undo it
    expect(t.step).toBe(2);
    expect(t.shouldStart(true)).toBe(false);
    expect(t.takePrompt()).toBe(false);
    const u = new Tour();
    u.known(1);
    expect(u.shouldStart(true)).toBe(false);
    expect(u.promptDue).toBe(true); // at login at step 1: a door already open takes it
    expect(u.takePrompt()).toBe(true);
    // No stops at all (no graph): it counts as toured.
    const v = new Tour();
    v.known(0);
    expect(v.begin([])).toBeNull();
    expect(v.step).toBe(1);
  });
});
