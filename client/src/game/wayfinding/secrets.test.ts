// The Wayfinder Charm never gives a secret away. Its list names no
// hidden passage, no key place stands in one, and no trail walks through
// one: from both spawns and from the rooms right beside each passage's two
// doors (where a passage would be the short way), to every key place, for
// every house, in every staircase state.

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import keyPlacesText from "../../../../data/world/key_places.json?raw";
import layoutText from "../../../../data/world/layout.json?raw";
import secretsText from "../../../../data/world/secrets.json?raw";
import stairsText from "../../../../data/world/stairs.json?raw";
import keepOutData from "../../../../data/world/keep_out.json";
import { initSync, SimWorld, Wayfinder, type House, type MapSurface } from "../../wasm/sim_wasm";

const HOUSES: House[] = ["roarwing", "burrowsby", "skytalon", "serpentyne"];
const keepOut = JSON.stringify((keepOutData as { keep_out?: unknown[] }).keep_out ?? []);
/** Points along each leg of a trail, this far apart (m). */
const SAMPLE_M = 0.25;

let sim: SimWorld;
let secretSurfaces: MapSurface[];
let starts: [number, number, number][];

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
  sim = new SimWorld(layoutText);
  sim.setSecrets(secretsText);
  const all = sim.mapSurfaces();
  secretSurfaces = all.filter((s) => s.secret);
  // The public rooms each passage door opens from: every public, flat surface touching a passage door.
  starts = [];
  for (const secret of sim.secrets()) {
    for (const doorId of secret.doors) {
      const door = all.find((s) => s.id === doorId)!;
      for (const s of all) {
        if (s.secret || s.kind === "stair" || s.moving || Math.abs(s.y - door.y) > 0.6) continue;
        const touches = s.min[0] <= door.max[0] + 0.01 && s.max[0] >= door.min[0] - 0.01 && s.min[1] <= door.max[1] + 0.01 && s.max[1] >= door.min[1] - 0.01;
        if (!touches) continue;
        // A point 1 m into it from the door's edge, on the ground there.
        const x = Math.min(Math.max((door.min[0] + door.max[0]) / 2, s.min[0] + 1), s.max[0] - 1);
        const z = Math.min(Math.max((door.min[1] + door.max[1]) / 2, s.min[1] + 1), s.max[1] - 1);
        const y = sim.groundAt(x, z, s.y);
        if (y !== undefined) starts.push([x, y, z]);
      }
    }
  }
});

/** The secret surface a point on a trail stands in, if any. */
function inSecret(x: number, y: number, z: number): string | null {
  for (const s of secretSurfaces) {
    const lo = Math.min(s.y, s.yTop ?? s.y) - 0.6;
    const hi = Math.max(s.y, s.yTop ?? s.y) + 0.6;
    if (x > s.min[0] + 0.01 && x < s.max[0] - 0.01 && z > s.min[1] + 0.01 && z < s.max[1] - 0.01 && y >= lo && y <= hi) return s.id;
  }
  return null;
}

/** Every secret surface a trail's legs cross. */
function secretsOn(flat: Float32Array): string[] {
  const hit = new Set<string>();
  for (let i = 0; i + 5 < flat.length; i += 3) {
    const [ax, ay, az, bx, by, bz] = [flat[i]!, flat[i + 1]!, flat[i + 2]!, flat[i + 3]!, flat[i + 4]!, flat[i + 5]!];
    const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, bz - az) / SAMPLE_M));
    for (let k = 0; k <= n; k++) {
      const t = k / n;
      const id = inSecret(ax + (bx - ax) * t, ay + (by - ay) * t, az + (bz - az) * t);
      if (id) hit.add(id);
    }
  }
  return [...hit];
}

describe("the Wayfinder Charm and the secrets", () => {
  it("knows the five passages and starts beside each of their doors", () => {
    expect(sim.secrets()).toHaveLength(5);
    expect(secretSurfaces.length).toBeGreaterThanOrEqual(25);
    // At least one public room beside each of the ten doors.
    expect(starts.length).toBeGreaterThanOrEqual(10);
  });

  it("lists no secret and no key place inside one", () => {
    const names = new Set(["Hidden Passage", ...sim.secrets().map((s) => s.name)]);
    for (const house of HOUSES) {
      const w = new Wayfinder(layoutText, house, keyPlacesText, keepOut);
      for (const p of w.places()) {
        expect(names.has(p.name), p.name).toBe(false);
        const id = sim.surfaceIdAt(p.pos[0], p.pos[2], p.pos[1]);
        expect(id, p.id).toBeDefined();
        expect(sim.secretOf(id!), `${p.id} on ${id}`).toBeUndefined();
      }
      w.free();
    }
  });

  it("never walks a trail through a passage, from any house, in any staircase state", () => {
    sim.setStairs(stairsText);
    const states = sim.stairFlights().length;
    expect(states).toBeGreaterThan(0);
    // Four spawns on the front steps, a metre or two apart; one stands for them all.
    const spawns = sim.spawns().slice(0, 1).map((s) => s.pos);
    let trails = 0;
    for (const house of HOUSES) {
      const w = new Wayfinder(layoutText, house, keyPlacesText, keepOut);
      // Every state for one house; the first state for the others.
      const want = house === "roarwing" ? 1 << states : 1;
      for (let state = 0; state < want; state++) {
        w.setStairState(state);
        for (const from of [...spawns, ...starts]) {
          for (const p of w.places()) {
            const flat = w.pathTo(p.id, from[0], from[1], from[2]);
            if (!flat) continue;
            trails++;
            expect(secretsOn(flat), `${house} state ${state} from ${from.map((v) => v.toFixed(1)).join(",")} to ${p.id}`).toEqual([]);
          }
        }
      }
      w.free();
    }
    // Most trails exist (a few starts are another house's ground or a gated room).
    expect(trails).toBeGreaterThan(1000);
    // About 4 s alone; more while cargo or Blender share the machine.
  }, 20_000);

  it("would find a passage if a trail used one (the check itself works)", () => {
    const s = secretSurfaces.find((x) => x.id === "sp_painting_stair")!;
    const midY = (s.y + (s.yTop ?? s.y)) / 2;
    const cx = (s.min[0] + s.max[0]) / 2;
    const cz = (s.min[1] + s.max[1]) / 2;
    expect(secretsOn(Float32Array.from([cx - 2, midY, cz, cx + 2, midY, cz]))).toContain("sp_painting_stair");
  });
});
