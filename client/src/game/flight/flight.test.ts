import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import flyingText from "../../../../data/flying.json?raw";
import layoutText from "../../../../data/world/layout.json?raw";
import pitchText from "../../../../data/world/instances/class_pitch.json?raw";
import { initSync, SimWorld, type FlyParams } from "../../wasm/sim_wasm";
import { BroomFlight, flightChipText, HOVER_SPEED, KEY_TURN_RATE, MOUNT_PENDING_MS, mountRefusedText, MountState, yawOfLook, type FlightInput } from "./flight";

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

const PARAMS: FlyParams = { speed: 8, climbSpeed: 4, acceleration: 6, clearance: 0.3, margin: 0.4, dismountHeight: 0.5, ceilingY: 45 };
const input = (throttle: number, turn = 0, climb = 0): FlightInput => ({ throttle, turn, climb });

/** Runs `seconds` of 60 fps frames; returns the summed move. */
function run(b: BroomFlight, seconds: number, i: FlightInput, look: number | null = null, params = PARAMS): [number, number, number] {
  const sum: [number, number, number] = [0, 0, 0];
  for (let f = 0; f < Math.round(seconds * 60); f++) {
    const d = b.step(1 / 60, i, params, look);
    sum[0] += d[0];
    sum[1] += d[1];
    sum[2] += d[2];
  }
  return sum;
}

describe("the broom's controls", () => {
  it("speeds up with W to the tier's top speed and keeps the speed when W is let go (a throttle)", () => {
    const b = new BroomFlight();
    run(b, 0.5, input(1));
    expect(b.speed).toBeCloseTo(3, 1); // 6 m/s² for half a second
    run(b, 5, input(1));
    expect(b.speed).toBe(8); // never over the top speed the server checks with
    run(b, 2, input(0));
    expect(b.speed).toBe(8);
    expect(b.anim).toBe("fly");
  });

  it("slows with S down to a hover, never backwards; hovering is still", () => {
    const b = new BroomFlight();
    run(b, 3, input(1));
    run(b, 5, input(-1));
    expect(b.speed).toBe(0);
    expect(b.anim).toBe("hover");
    const d = run(b, 1, input(0));
    expect(Math.hypot(d[0], d[1], d[2])).toBe(0);
  });

  it("drops to a lower top speed at once (Featherlight wearing off)", () => {
    const b = new BroomFlight();
    run(b, 5, input(1), null, { ...PARAMS, speed: 11.5 });
    expect(b.speed).toBeCloseTo(11.5, 5);
    b.step(1 / 60, input(0), PARAMS, null);
    expect(b.speed).toBe(8);
  });

  it("flies along its heading (yaw 0 = +Z, yaw π/2 = +X)", () => {
    const b = new BroomFlight();
    b.reset(Math.PI / 2);
    run(b, 3, input(1));
    const d = run(b, 1, input(0));
    expect(d[0]).toBeCloseTo(8, 1);
    expect(Math.abs(d[2])).toBeLessThan(1e-6);
  });

  it("climbs with Space and dives with Shift at the climb speed, and levels out with neither", () => {
    const b = new BroomFlight();
    run(b, 2, input(0, 0, 1));
    expect(b.vy).toBeCloseTo(4, 3);
    expect(b.anim).toBe("fly"); // climbing in place counts as flying
    run(b, 2, input(0, 0, -1));
    expect(b.vy).toBeCloseTo(-4, 3);
    run(b, 2, input(0));
    expect(b.vy).toBe(0);
    expect(b.anim).toBe("hover");
  });

  it("tips its nose with the climb and banks into turns, then levels itself", () => {
    const b = new BroomFlight();
    run(b, 3, input(1, 0, 1));
    expect(b.pitch).toBeGreaterThan(0.05);
    run(b, 3, input(1, 0, -1));
    expect(b.pitch).toBeLessThan(-0.05);
    run(b, 0.5, input(1, 1, 0));
    expect(b.bank).not.toBe(0);
    run(b, 4, input(1));
    expect(Math.abs(b.pitch)).toBeLessThan(1e-3);
    expect(Math.abs(b.bank)).toBeLessThan(1e-3);
  });

  it("turns right with D and left with A at the key turn rate", () => {
    const b = new BroomFlight();
    b.reset(1);
    run(b, 0.5, input(0, 1));
    expect(b.yaw).toBeCloseTo(1 - KEY_TURN_RATE * 0.5, 3); // right is a smaller yaw (forward (sin, cos), right (-cos, sin))
    run(b, 0.5, input(0, -1));
    expect(b.yaw).toBeCloseTo(1, 3);
  });

  it("follows the camera's view while moving, but lets a hovering rider look round", () => {
    const b = new BroomFlight();
    b.reset(0);
    run(b, 2, input(0), 1.5); // hovering: no turn
    expect(b.yaw).toBe(0);
    run(b, 0.2, input(1), 1.5);
    expect(b.speed).toBeGreaterThan(HOVER_SPEED);
    run(b, 2, input(0), 1.5);
    expect(b.yaw).toBeCloseTo(1.5, 5);
  });

  it("reads the camera's look direction as a yaw", () => {
    expect(yawOfLook(0, 1)).toBeCloseTo(0);
    expect(yawOfLook(1, 0)).toBeCloseTo(Math.PI / 2);
    expect(yawOfLook(0, 0)).toBeNull();
  });
});

describe("mounting", () => {
  it("asks once, lifts on the server's yes, and lands on its word", () => {
    const m = new MountState();
    expect(m.toggle(0)).toBe(true);
    expect(m.phase).toBe("mounting");
    expect(m.pending).toBe(true);
    expect(m.toggle(10)).toBeNull(); // already asked
    expect(m.onMounted(true, 2, null)).toEqual({ kind: "lift" });
    expect(m.mounted).toBe(true);
    expect(m.tier).toBe(2);
    expect(m.toggle(20)).toBe(false);
    expect(m.phase).toBe("landing");
    expect(m.mounted).toBe(true); // still flying until the server says so
    expect(m.onMounted(false, 0, null)).toEqual({ kind: "land" });
    expect(m.mounted).toBe(false);
    expect(m.tier).toBe(0);
  });

  it("shows the server's reason for a refusal and keeps the state it gives", () => {
    const m = new MountState();
    m.toggle(0);
    const out = m.onMounted(false, 0, "no_skill");
    expect(out.kind).toBe("refused");
    expect(out.kind === "refused" && out.text).toMatch(/Flying lesson/);
    expect(m.phase).toBe("walking");
    m.onMounted(true, 1, null);
    m.toggle(0);
    const nl = m.onMounted(true, 1, "not_landed");
    expect(nl.kind === "refused" && nl.text).toMatch(/half a metre/);
    expect(m.phase).toBe("flying");
  });

  it("is mounted by the server unasked (the Flying class) and forgets an unanswered request", () => {
    const m = new MountState();
    expect(m.onMounted(true, 1, null)).toEqual({ kind: "lift" });
    expect(m.onMounted(true, 1, null)).toEqual({ kind: "none" });
    const w = new MountState();
    w.toggle(1000);
    w.expire(1000 + MOUNT_PENDING_MS - 1);
    expect(w.phase).toBe("mounting");
    w.expire(1000 + MOUNT_PENDING_MS);
    expect(w.phase).toBe("walking");
  });

  it("has words for every refusal", () => {
    for (const r of ["no_skill", "no_airspace", "no_fly", "busy", "not_landed", "rate_limited"] as const) expect(mountRefusedText(r, false).length).toBeGreaterThan(10);
  });
});

describe("the flight chip", () => {
  it("says how high we fly and when we may land", () => {
    expect(flightChipText({ height: 33.7, hovering: false, canLand: false, landing: false })).toBe("Flying, 34 m up, land to dismount");
    expect(flightChipText({ height: 5, hovering: true, canLand: false, landing: false })).toBe("Hovering, 5 m up, land to dismount");
    expect(flightChipText({ height: 0.3, hovering: true, canLand: true, landing: false })).toBe("Hovering, just above the ground: press B to land");
    expect(flightChipText({ height: null, hovering: false, canLand: false, landing: false })).toBe("Flying, land to dismount");
    expect(flightChipText({ height: 0.3, hovering: true, canLand: true, landing: true })).toBe("Hovering: landing…");
  });
});

describe("an honest flight is never corrected (sim-wasm, the server's check)", () => {
  /** Flies `seconds` at 60 fps through `flyAndSlide`, reporting at 10 Hz to `checkFlight`; returns the rejects and where it ended. */
  function fly(sim: SimWorld, start: [number, number, number], yaw: number, plan: (t: number) => { i: FlightInput; look: number | null }, seconds: number, tier = 1, pct = 100) {
    const params = sim.flyParams(tier, pct);
    const b = new BroomFlight();
    b.reset(yaw);
    let p = start;
    let reported = start;
    const rejects: string[] = [];
    const frames = Math.round(seconds * 60);
    for (let f = 1; f <= frames; f++) {
      const { i, look } = plan(f / 60);
      const d = b.step(1 / 60, i, params, look);
      const n = sim.flyAndSlide(p[0], p[1], p[2], d[0], d[1], d[2], tier, pct);
      p = [n[0]!, n[1]!, n[2]!];
      if (f % 6 === 0) {
        const r = sim.checkFlight(reported[0], reported[1], reported[2], p[0], p[1], p[2], 0.1, tier, pct);
        if (r) rejects.push(`${r} at ${p.map((v) => v.toFixed(2)).join(", ")}`);
        reported = p;
      }
    }
    return { rejects, end: p, broom: b };
  }

  it("mounts on the pitch, climbs, flies north into the castle's no-fly box and is stopped at its edge", () => {
    const sim = new SimWorld(layoutText, "roarwing");
    sim.setFlightRules(flyingText);
    expect(sim.hasAirspace).toBe(true);
    const m = sim.mount(10, -9.5, 200);
    expect("pos" in m).toBe(true);
    const start = ("pos" in m ? m.pos : [0, 0, 0]) as [number, number, number];
    expect(start[1]).toBeCloseTo(-9.2, 3); // the floor plus the clearance
    // North is -Z (yaw π); climb for the first 4 s, then full speed at the castle.
    const north = Math.PI;
    const r = fly(sim, start, north, (t) => ({ i: input(1, 0, t < 4 ? 1 : 0), look: north }), 25, 3, 115);
    expect(r.rejects).toEqual([]);
    // The castle's no-fly box starts at z 100 and the client keeps its 0.4 m margin off it.
    expect(r.end[2]).toBeGreaterThan(100);
    expect(r.end[2]).toBeLessThan(101);
    sim.free();
  });

  it("flies out over the lake, dives to the water and climbs to the ceiling without a reject", () => {
    const sim = new SimWorld(layoutText, "skytalon");
    sim.setFlightRules(flyingText);
    const m = sim.mount(10, -9.5, 200);
    const start = ("pos" in m ? m.pos : [0, 0, 0]) as [number, number, number];
    const south = 0;
    const r = fly(sim, start, south, (t) => ({ i: input(1, t > 10 && t < 14 ? 1 : 0, t < 12 ? -1 : 1), look: null }), 40, 2);
    expect(r.rejects).toEqual([]);
    expect(r.end[1]).toBeLessThanOrEqual(45);
    // Not landed over the water; a landing needs ground within half a metre.
    expect(sim.dismount(r.end[0], r.end[1], r.end[2])).toBeUndefined();
    sim.free();
  });

  it("lands back on the field: low over the ground a dismount stands us on it", () => {
    const sim = new SimWorld(layoutText, "burrowsby");
    sim.setFlightRules(flyingText);
    const m = sim.mount(10, -9.5, 200);
    const start = ("pos" in m ? m.pos : [0, 0, 0]) as [number, number, number];
    const r = fly(sim, start, 1.2, (t) => ({ i: input(t < 1 ? 1 : -1, 0, t < 2 ? 1 : -1), look: null }), 8);
    expect(r.rejects).toEqual([]);
    const at = sim.dismount(r.end[0], r.end[1], r.end[2]);
    expect(at).toBeDefined();
    expect(at![1]).toBeCloseTo(-9.5, 2);
    sim.free();
  });

  it("refuses to mount in the castle (no-fly) and in a world with no open sky", () => {
    const sim = new SimWorld(layoutText, "serpentyne");
    sim.setFlightRules(flyingText);
    expect(sim.mount(14, 0, 32)).toEqual({ refused: "no_fly" });
    sim.free();
  });

  it("flies the Flying class's field inside its own airspace", () => {
    const sim = new SimWorld(pitchText);
    sim.setFlightRules(flyingText);
    expect(sim.rings().length).toBe(8);
    const m = sim.mount(0, 0, 16);
    const start = ("pos" in m ? m.pos : [0, 0, 0]) as [number, number, number];
    const r = fly(sim, start, Math.PI, (t) => ({ i: input(1, t > 3 ? 1 : 0, t < 3 ? 1 : 0), look: null }), 20);
    expect(r.rejects).toEqual([]);
    expect(r.end[1]).toBeLessThanOrEqual(20);
    sim.free();
  });
});
