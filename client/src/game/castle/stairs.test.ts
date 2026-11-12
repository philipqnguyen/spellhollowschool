// The moving staircases' client side against the real schedule
// (sim-wasm's `SimWorld`: stairState, flightStates, carry): the stair
// state from the clock, riders turned with a swinging flight and carried at
// the switch, the gates' leaves, the frame-to-frame events, the trail's
// re-planning rule and the owls' motion.

import { readFileSync } from "node:fs";
import { Matrix, Quaternion, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { beforeAll, describe, expect, it } from "vitest";
import keyPlacesText from "../../../../data/world/key_places.json?raw";
import keepOutData from "../../../../data/world/keep_out.json";
import layoutText from "../../../../data/world/layout.json?raw";
import stairsText from "../../../../data/world/stairs.json?raw";
import { initSync, SimWorld, Wayfinder, type FlightView } from "../../wasm/sim_wasm";
import type { LayoutSurface } from "../world";
import { devStairsPeriod } from "./stairs";
import { owlPose } from "./owls";
import { entersSwinging, warnsNear, flightGeoms, gateRail, onStair, planState, RAIL_MID_Y, RAIL_W, railShown, riderTurn, shudder, stairHeightAt, StairWatch, TrailStairs, turnAbout, turnPose, type FlightGeom, type RailPose } from "./stairs-state";
import { PUFFS_PER_BURST, RailSmoke } from "./rail-smoke";

const surfaces = (JSON.parse(layoutText) as { surfaces: LayoutSurface[] }).surfaces;
const stairs = JSON.parse(stairsText) as { period_s: number; swing_s: number; warning_s: number; gate_s: number };
const PERIOD_MS = stairs.period_s * 1000;
const SWING_MS = stairs.swing_s * 1000;
const WARNING_MS = stairs.warning_s * 1000;

let sim: SimWorld;
let geoms: FlightGeom[];

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
  sim = new SimWorld(layoutText, "roarwing");
  sim.setStairs(stairsText);
  geoms = flightGeoms(sim.stairFlights(), surfaces);
});

/** A point a fraction (u along x, v along z) into a surface, at its height. */
function inside(s: FlightGeom["a"], u: number, v: number): [number, number, number] {
  const x = s.min[0] + (s.max[0] - s.min[0]) * u;
  const z = s.min[1] + (s.max[1] - s.min[1]) * v;
  return [x, stairHeightAt(s, x, z), z];
}

/** The first instant at or after `from` (ms, 100 ms steps) when `pred` holds for flight `i`. */
function findTime(i: number, from: number, pred: (v: FlightView) => boolean): number {
  for (let t = from; t < from + 2 * PERIOD_MS; t += 100) if (pred(sim.flightStates(t)[i]!)) return t;
  throw new Error("not found");
}

describe("the moving flights' geometry", () => {
  it("reads the three flights and both surfaces of each, in bit order", () => {
    expect(geoms.map((g) => g.id)).toEqual(["flight_3_4", "flight_5_6", "flight_6_7"]);
    expect(geoms.map((g) => g.bit)).toEqual([0, 1, 2]);
    for (const g of geoms) {
      expect(g.a.id).not.toEqual(g.b.id);
      expect(Math.abs(g.angle)).toBeCloseTo(Math.PI / 2, 6);
    }
  });

  it("turns a point on A onto the same spot of B exactly as sim-core carries it, facing turned the same", () => {
    const out: [number, number] = [0, 0];
    for (const g of geoms) {
      for (const [u, v] of [
        [0.1, 0.1],
        [0.5, 0.5],
        [0.9, 0.2],
        [0.3, 0.95],
      ] as const) {
        const [x, y, z] = inside(g.a, u, v);
        // Carried in the world predicted before the switch (who rides is whose ground is the flight).
        sim.setStairState(0);
        const carried = sim.carry(x, y, z, g.bit, "a", "b")!;
        expect(carried).toBeDefined();
        turnAbout(g.pivot, g.angle, x, z, out);
        expect(out[0]).toBeCloseTo(carried[0]!, 2);
        expect(out[1]).toBeCloseTo(carried[2]!, 2);
        // The carried rider stands on B at its height there.
        expect(onStair(g.b, carried[0]!, carried[1]!, carried[2]!)).toBe(true);
        expect(carried[1]!).toBeCloseTo(stairHeightAt(g.b, carried[0]!, carried[2]!), 1);
        // And back again, from the state with the flight at B.
        sim.setStairState(1 << g.bit);
        expect(sim.carry(x, y, z, g.bit, "a", "b")).toBeUndefined();
        const back = sim.carry(carried[0]!, carried[1]!, carried[2]!, g.bit, "b", "a")!;
        sim.setStairState(0);
        expect(back[0]!).toBeCloseTo(x, 2);
        expect(back[2]!).toBeCloseTo(z, 2);
      }
      // Off the flight: nobody to carry.
      expect(sim.carry(g.pivot[0] + 30, 0, g.pivot[1] + 30, g.bit, "a", "b")).toBeUndefined();
    }
  });

  it("matches Babylon's Y turn (the swing node's matrix) to the layout's turn", () => {
    for (const g of geoms) {
      const m = Matrix.Translation(-g.pivot[0], 0, -g.pivot[1]).multiply(Matrix.RotationY(g.angle)).multiply(Matrix.Translation(g.pivot[0], 0, g.pivot[1]));
      const [x, y, z] = inside(g.a, 0.7, 0.3);
      const w = Vector3.TransformCoordinates(new Vector3(x, y, z), m);
      const out: [number, number] = [0, 0];
      turnAbout(g.pivot, g.angle, x, z, out);
      expect(w.x).toBeCloseTo(out[0], 5);
      expect(w.z).toBeCloseTo(out[1], 5);
    }
  });

  it("turns a drawn pose's facing with its place (forward = (sin yaw, cos yaw))", () => {
    const g = geoms[0]!;
    const pose = { x: g.pivot[0], z: g.pivot[1] - 5, yaw: 0.3 };
    const ahead = { x: pose.x + Math.sin(pose.yaw), z: pose.z + Math.cos(pose.yaw) };
    turnPose(pose, g.pivot, g.angle, [0, 0]);
    const out: [number, number] = [0, 0];
    turnAbout(g.pivot, g.angle, ahead.x, ahead.z, out);
    expect(pose.x + Math.sin(pose.yaw)).toBeCloseTo(out[0], 6);
    expect(pose.z + Math.cos(pose.yaw)).toBeCloseTo(out[1], 6);
  });
});

describe("the stair state from the clock", () => {
  it("switches each flight every period, staggered, with the warning and the swing before each switch", () => {
    for (const g of geoms) {
      const swing = findTime(g.bit, 0, (v) => v.phase === "swing");
      const v = sim.flightStates(swing)[g.bit]!;
      const sw = v.nextSwitchMs;
      // The swing is the 6 s before the switch; the warning the 5 s before that.
      expect(sw - swing).toBeLessThanOrEqual(SWING_MS);
      expect(sw - swing).toBeGreaterThan(SWING_MS - 200);
      const warn = sim.flightStates(sw - SWING_MS - WARNING_MS + 100)[g.bit]!;
      expect(warn.phase).toBe("rest");
      expect(warn.warning).toBe(true);
      expect(sim.flightStates(sw - SWING_MS - WARNING_MS - 100)[g.bit]!.warning).toBe(false);
      // The walk position changes at the switch, and the state's bit with it.
      const before = sim.flightStates(sw - 50)[g.bit]!;
      const after = sim.flightStates(sw + 50)[g.bit]!;
      expect(after.walk).not.toBe(before.walk);
      expect(((sim.stairState(sw + 50) >> g.bit) & 1) === 1).toBe(after.walk === "b");
      expect(after.nextSwitchMs - sw).toBe(PERIOD_MS);
      // The model's turn: 0 at A, the whole angle at B, between in the swing.
      expect(Math.abs(after.turnDeg - (after.walk === "b" ? (g.angle * 180) / Math.PI : 0))).toBeLessThan(0.01);
      const mid = sim.flightStates(sw - SWING_MS / 2)[g.bit]!;
      expect(mid.phase).toBe("swing");
      expect(mid.progress).toBeGreaterThan(0.3);
      expect(mid.progress).toBeLessThan(0.7);
    }
    // Staggered: the three flights switch a third of a period apart.
    const sw = geoms.map((g) => sim.flightStates(1000)[g.bit]!.nextSwitchMs % PERIOD_MS).sort((a, b) => a - b);
    expect(sw[1]! - sw[0]!).toBeCloseTo(PERIOD_MS / 3, -3);
    expect(sw[2]! - sw[1]!).toBeCloseTo(PERIOD_MS / 3, -3);
  });

  it("uses the development period when asked (and only in development or on this machine)", () => {
    const dev = new SimWorld(layoutText, "roarwing");
    dev.setStairs(stairsText, 20);
    const v = dev.flightStates(5000)[0]!;
    expect(v.nextSwitchMs - v.lastSwitchMs).toBe(20_000);
    dev.free();
    expect(devStairsPeriod("?stairsPeriod=20", true, "example.org")).toBe(20);
    expect(devStairsPeriod("?stairsPeriod=20", false, "localhost")).toBe(20);
    expect(devStairsPeriod("?stairsPeriod=20", false, "play.example.org")).toBeUndefined();
    expect(devStairsPeriod("?stairsPeriod=abc", true, "localhost")).toBeUndefined();
    expect(devStairsPeriod("", true, "localhost")).toBeUndefined();
  });

  it("plans trails in the state each swinging flight is heading for", () => {
    expect(planState(0b000, [{ phase: "rest" }, { phase: "swing" }, { phase: "rest" }])).toBe(0b010);
    expect(planState(0b011, [{ phase: "swing" }, { phase: "rest" }, { phase: "swing" }])).toBe(0b110);
  });
});

describe("riders", () => {
  it("are drawn turned with a swinging flight and land where the switch carries them", () => {
    for (const g of geoms) {
      // A swing away from A.
      const swing = findTime(g.bit, 0, (v) => v.phase === "swing" && v.walk === "a");
      const sw = sim.flightStates(swing)[g.bit]!.nextSwitchMs;
      const [x, y, z] = inside(g.a, 0.5, 0.5);
      const mid = sim.flightStates(sw - SWING_MS / 2)[g.bit]!;
      const rad = riderTurn(g, mid.turnDeg, x, y, z)!;
      expect(rad).toBeCloseTo((mid.turnDeg * Math.PI) / 180, 6);
      expect(Math.abs(rad)).toBeGreaterThan(0.2);
      expect(Math.abs(rad)).toBeLessThan(Math.abs(g.angle) - 0.2);
      // At the end of the swing the drawn rider stands where carry puts it: no jump at the switch.
      const end = sim.flightStates(sw - 1)[g.bit]!;
      const pose = { x, z, yaw: 0 };
      turnPose(pose, g.pivot, riderTurn(g, end.turnDeg, x, y, z)!, [0, 0]);
      const carried = sim.carry(x, y, z, g.bit, "a", "b")!;
      expect(Math.hypot(pose.x - carried[0]!, pose.z - carried[2]!)).toBeLessThan(0.05);
      // After the switch: a remote whose snapshots still show it on A is drawn carried; one on B as it stands.
      const after = sim.flightStates(sw + 50)[g.bit]!;
      expect(riderTurn(g, after.turnDeg, x, y, z)).toBeCloseTo(g.angle, 4);
      expect(riderTurn(g, after.turnDeg, carried[0]!, carried[1]!, carried[2]!)).toBeCloseTo(0, 4);
      // At rest on A, a rider is drawn where it stands; someone off the flight is never turned.
      const rest = sim.flightStates(sw - SWING_MS - WARNING_MS - 2000)[g.bit]!;
      expect(rest.phase).toBe("rest");
      expect(riderTurn(g, rest.turnDeg, x, y, z)).toBeCloseTo(0, 9);
      expect(riderTurn(g, mid.turnDeg, x, y + 5, z)).toBeNull();
      expect(riderTurn(g, mid.turnDeg, g.pivot[0] + 20, y, g.pivot[1])).toBeNull();
    }
  });

  it("never step onto a flight swinging away from off it (its gate is shut); riders already on it are not stopped", () => {
    // Flight 3-4 at A: its foot meets the third floor's north landing at z -8 (the landing is z < -8).
    const g = geoms[0]!;
    const swing = findTime(g.bit, 0, (v) => v.phase === "swing" && v.walk === "a");
    const views = sim.flightStates(swing + 1500);
    const rest = sim.flightStates(swing - SWING_MS - WARNING_MS - 2000);
    const foot = inside(g.a, 0.5, 0.02);
    const landing: [number, number, number] = [foot[0], 27, g.a.min[1] - 0.3];
    expect(entersSwinging(geoms, views, landing, foot)).toBe(true);
    expect(entersSwinging(geoms, rest, landing, foot), "at rest the flight is open").toBe(false);
    const on = inside(g.a, 0.5, 0.5);
    const on2 = inside(g.a, 0.5, 0.55);
    expect(entersSwinging(geoms, views, on, on2), "a rider").toBe(false);
    expect(entersSwinging(geoms, views, landing, [landing[0] + 1, 27, landing[2]]), "along the landing").toBe(false);
  });

  it("predict in the new state's world after the switch (the old flight is gone, the new one walkable)", () => {
    const g = geoms[0]!;
    const w = new SimWorld(layoutText, "roarwing");
    w.setStairs(stairsText);
    const [x, y, z] = inside(g.b, 0.5, 0.5);
    w.setStairState(0);
    expect(w.surfaceIdAt(x, z, y)).not.toBe(g.b.id);
    w.setStairState(1 << g.bit);
    expect(w.surfaceIdAt(x, z, y)).toBe(g.b.id);
    expect(w.currentStairState).toBe(1 << g.bit);
    const [ax, ay, az] = inside(g.a, 0.5, 0.5);
    expect(w.surfaceIdAt(ax, az, ay)).not.toBe(g.a.id);
    w.free();
  });
});

describe("the frame-to-frame watch", () => {
  it("reports each switch once with its positions, and swings and warnings as they begin", () => {
    const watch = new StairWatch();
    const switches: { t: number; flight: number; from: string; to: string }[] = [];
    const swingStarts: { t: number; flight: number }[] = [];
    const warnStarts: { t: number; flight: number }[] = [];
    let first = 0;
    for (let t = 0; t <= PERIOD_MS + 1000; t += 50) {
      const e = watch.update(sim.stairState(t), sim.flightStates(t));
      if (e.first) first++;
      for (const s of e.switched) switches.push({ t, ...s });
      for (let i = 0; i < 3; i++) {
        if (e.swingStarted & (1 << i)) swingStarts.push({ t, flight: i });
        if (e.warningStarted & (1 << i)) warnStarts.push({ t, flight: i });
      }
      // While a flight swings, trails plan in the state it heads for.
      const views = sim.flightStates(t);
      views.forEach((v, i) => {
        if (v.phase === "swing") expect(((e.plan >> i) & 1) === 1).toBe(v.walk === "a");
      });
    }
    expect(first).toBe(1);
    // One switch of each flight in a period (plus the window's edge), each with a swing and a warning before it.
    expect(switches.length).toBeGreaterThanOrEqual(3);
    for (const s of switches) {
      expect(s.from).not.toBe(s.to);
      const sw = swingStarts.find((x) => x.flight === s.flight && x.t <= s.t && s.t - x.t <= SWING_MS + 100);
      expect(sw).toBeDefined();
      const wn = warnStarts.find((x) => x.flight === s.flight && x.t <= sw!.t && sw!.t - x.t <= WARNING_MS + 100);
      expect(wn).toBeDefined();
    }
  });
});

describe("the landing gates", () => {
  it("open on their own position: closed while their flight is away or swinging off, opening after it arrives", () => {
    const defs = sim.stairFlights();
    for (const g of geoms) {
      const gates = defs[g.bit]!.gates;
      const swing = findTime(g.bit, 0, (v) => v.phase === "swing" && v.walk === "a");
      const sw = sim.flightStates(swing)[g.bit]!.nextSwitchMs;
      const openAt = (t: number, pos: "a" | "b") =>
        gates
          .map((gate, k) => ({ gate, open: sim.flightStates(t)[g.bit]!.gates[k]!.open }))
          .filter((x) => x.gate.position === pos)
          .map((x) => x.open);
      // Resting at A: A gates open, B gates shut.
      const rest = sw - SWING_MS - WARNING_MS - 2000;
      expect(openAt(rest, "a").every((o) => o === 1)).toBe(true);
      expect(openAt(rest, "b").every((o) => o === 0)).toBe(true);
      // Swinging away: the A gates have shut behind it.
      expect(openAt(sw - 1000, "a").every((o) => o === 0)).toBe(true);
      // Arrived at B: its gates open within gate_s.
      expect(openAt(sw + stairs.gate_s * 1000 + 100, "b").every((o) => o === 1)).toBe(true);
      expect(openAt(sw + stairs.gate_s * 1000 + 100, "a").every((o) => o === 0)).toBe(true);
    }
  });

  it("stand each rail across its gate line when shut, its landing side onto the landing, and conjure it from its middle", () => {
    const out: RailPose = { x: 0, y: 0, z: 0, rotY: 0, sx: 1, s: 1 };
    // The prop's point (lx, ly, lz) under the pose, as the instance matrix places it.
    const place = (p: RailPose, lx: number, ly: number, lz: number) =>
      Vector3.TransformCoordinates(
        new Vector3(lx, ly, lz),
        Matrix.Compose(new Vector3(p.sx, p.s, p.s), Quaternion.RotationYawPitchRoll(p.rotY, 0, 0), new Vector3(p.x, p.y, p.z)),
      );
    expect(railShown(0)).toBe(1);
    expect(railShown(1)).toBe(0);
    expect(railShown(0.5)).toBeCloseTo(0.5, 6);
    for (const f of sim.stairFlights()) {
      for (const gate of f.gates) {
        const [h0, h1] = gate.line;
        gateRail(gate, 1, out);
        expect(out.s).toBe(1);
        expect(out.sx).toBeCloseTo(Math.hypot(h1[0] - h0[0], h1[1] - h0[1]) / RAIL_W, 6);
        // Whole: from one hinge to the other at the gate's floor ...
        const a = place(out, 0, 0, 0);
        const b = place(out, RAIL_W, 0, 0);
        const ends = [
          [a.x, a.z],
          [b.x, b.z],
        ].sort((p, q) => p[0]! - q[0]! || p[1]! - q[1]!);
        const hinges = [h0, h1].map((h) => [...h]).sort((p, q) => p[0]! - q[0]! || p[1]! - q[1]!);
        for (let k = 0; k < 2; k++) for (let j = 0; j < 2; j++) expect(ends[k]![j]).toBeCloseTo(hinges[k]![j]!, 6);
        expect(a.y).toBeCloseTo(gate.y, 6);
        // ... its +z side (no coping overhang) toward the landing.
        const side = place(out, RAIL_W / 2, 0, 1).subtract(place(out, RAIL_W / 2, 0, 0));
        expect(side.x).toBeCloseTo(gate.opens[0], 6);
        expect(side.z).toBeCloseTo(gate.opens[1], 6);
        // Part conjured: its middle stays put; vanished, nothing stands.
        const mid = place(out, RAIL_W / 2, RAIL_MID_Y, 0);
        gateRail(gate, 0.3, out);
        const mid3 = place(out, RAIL_W / 2, RAIL_MID_Y, 0);
        expect(mid3.subtract(mid).length()).toBeLessThan(1e-4); // float32 matrices at ~50 m from the origin
        expect(gateRail(gate, 0, out).s).toBe(0);
      }
    }
  });

  it("bloom a cloud of smoke along a gate's line that billows and thins away", () => {
    const smoke = new RailSmoke(PUFFS_PER_BURST + 4);
    smoke.burst(-48, -8, -44, -8, 27);
    expect(smoke.count).toBe(PUFFS_PER_BURST);
    // A second cloud fills only what is left.
    smoke.burst(-48, -8, -44, -8, 27);
    expect(smoke.count).toBe(PUFFS_PER_BURST + 4);
    const seen: number[][] = [];
    const sink = { push: (x: number, y: number, z: number, size: number, _r: number, _g: number, _b: number, a: number) => (seen.push([x, y, z, size, a]), true) };
    smoke.step(0.2);
    smoke.draw(sink);
    expect(seen.length).toBe(PUFFS_PER_BURST + 4);
    for (const [x, y, , size, a] of seen) {
      expect(x).toBeGreaterThan(-49.5);
      expect(x).toBeLessThan(-42.5);
      expect(y).toBeGreaterThan(27);
      expect(y).toBeLessThan(29.5);
      expect(size).toBeGreaterThan(0.5);
      expect(a).toBeGreaterThan(0);
    }
    for (let i = 0; i < 30; i++) smoke.step(0.1);
    expect(smoke.count).toBe(0);
  });
});

describe("the warning's chat line", () => {
  it("is for players by a warned flight, not the whole well", () => {
    const g = geoms[0]!; // flight 3-4, the third to fourth floor
    const [x, y, z] = inside(g.a, 0.5, 0.5);
    expect(warnsNear(geoms, 1 << g.bit, x, y, z)).toBe(true);
    expect(warnsNear(geoms, 1 << g.bit, x, 27, g.a.min[1] - 3), "on its landing").toBe(true);
    expect(warnsNear(geoms, 1 << g.bit, x, 63, z), "four floors up").toBe(false);
    expect(warnsNear(geoms, 1 << g.bit, x, 0, z), "on the ground floor").toBe(false);
    expect(warnsNear(geoms, 1 << geoms[2]!.bit, x, y, z), "another flight's warning").toBe(false);
  });
});

describe("the warning's shudder", () => {
  it("trembles only in the warning, a fraction of a degree", () => {
    const v = { warning: true, phase: "rest" as const, nextSwitchMs: 100_000 };
    let max = 0;
    for (let t = 100_000 - SWING_MS - WARNING_MS; t < 100_000 - SWING_MS; t += 7) max = Math.max(max, Math.abs(shudder(v, t, SWING_MS, WARNING_MS)));
    expect(max).toBeGreaterThan(0.001);
    expect(max).toBeLessThan((0.5 * Math.PI) / 180);
    expect(shudder({ ...v, warning: false }, 90_000, SWING_MS, WARNING_MS)).toBe(0);
    expect(shudder({ ...v, phase: "swing" }, 90_000, SWING_MS, WARNING_MS)).toBe(0);
  });
});

describe("the Monstra Viam trail and the staircases", () => {
  it("plans again when the state changes or a flight it crosses starts to swing, not for another flight's swing", () => {
    const t = new TrailStairs();
    t.flights = 0b001;
    expect(t.onStairs({ changed: false, swingStarted: 0, plan: 0 })).toBe(false);
    // Flight 5-6 swings: the trail does not cross it.
    expect(t.onStairs({ changed: false, swingStarted: 0b010, plan: 0b010 })).toBe(false);
    expect(t.plan).toBe(0b010);
    // Flight 3-4, which it crosses, swings: plan again (in the state it heads for).
    expect(t.onStairs({ changed: false, swingStarted: 0b001, plan: 0b011 })).toBe(true);
    expect(t.plan).toBe(0b011);
    // Any switch: plan again.
    expect(t.onStairs({ changed: true, swingStarted: 0, plan: 0b011 })).toBe(true);
  });

  it("knows the flights a trail crosses, and a new state gives a new way", () => {
    const keepOut = JSON.stringify((keepOutData as { keep_out?: unknown[] }).keep_out ?? []);
    const finder = new Wayfinder(layoutText, "roarwing", keyPlacesText, keepOut);
    const spawn = [14, 0, 32] as const;
    // From the Entrance Hall up to the Roarwing dormitory: all three moving flights in every state.
    finder.setStairState(0);
    expect(finder.pathFlights("dorm_roarwing", ...spawn)).toBe(0b111);
    const rest = finder.pathTo("dorm_roarwing", ...spawn)!;
    // Flight 3-4 swings to the east balcony: the trail the walker would follow is another way.
    finder.setStairState(0b001);
    expect(finder.stairState).toBe(0b001);
    const swung = finder.pathTo("dorm_roarwing", ...spawn)!;
    expect(swung.length).not.toBe(rest.length);
    // The Great Hall's way never touches the staircase.
    expect(finder.pathFlights("great_hall", ...spawn)).toBe(0);
    finder.free();
  });
});

describe("the Owl Loft's owls", () => {
  it("bob a little and turn their heads now and then, each by its phase", () => {
    let maxBob = 0;
    let minHead = 0;
    let maxHead = 0;
    for (let t = 0; t < 60; t += 0.05) {
      const p = owlPose(t, 1.3);
      maxBob = Math.max(maxBob, Math.abs(p.bob));
      minHead = Math.min(minHead, p.head);
      maxHead = Math.max(maxHead, p.head);
    }
    expect(maxBob).toBeLessThanOrEqual(0.02);
    expect(maxHead).toBeGreaterThan(0.9);
    expect(minHead).toBeLessThan(-0.9);
    expect(owlPose(10, 0).head).not.toBeCloseTo(owlPose(10, 2).head, 2);
  });
});
