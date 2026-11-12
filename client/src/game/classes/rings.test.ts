import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import flyingClass from "../../../../data/classes/flying.json";
import pitchText from "../../../../data/world/instances/class_pitch.json?raw";
import { initSync, SimWorld, type GestureResult, type LessonBeat } from "../../wasm/sim_wasm";
import { isFlown } from "./controller";
import { LessonState, type BeatState } from "./lesson";
import { FLASH_MS, ringBrightness, ringColours, ringLooks } from "./rings";

const WINDOW = 2000;

function ringBeat(beat: number, ring: number, atMs: number, status: BeatState["status"] = "upcoming", result: GestureResult | null = null): BeatState {
  return { beat, kind: "ring", gesture: 0, prompt: 0, options: [], ring, choice: null, atMs, status, preview: null, result };
}

const hit = (score: number): GestureResult => ({ score, grade: score >= 90 ? "perfect" : score > 0 ? "good" : "miss", rejected: null }) as GestureResult;

describe("the ring course's looks", () => {
  it("rests every ring outside a round", () => {
    const looks = ringLooks(8, null, WINDOW, 0);
    expect(looks).toHaveLength(8);
    expect(looks.every((l) => l.colour === "rest" && l.glow === 0)).toBe(true);
  });

  it("makes the next beat's ring pulse, brightest on its beat", () => {
    const beats = [ringBeat(0, 0, 10_000), ringBeat(1, 1, 14_000)];
    const far = ringLooks(8, beats, WINDOW, 4_000)[0]!;
    expect(far.colour).toBe("next");
    const onBeat = ringLooks(8, beats, WINDOW, 10_000)[0]!;
    expect(onBeat.glow).toBe(1);
    expect(onBeat.glow).toBeGreaterThan(far.glow);
    expect(ringLooks(8, beats, WINDOW, 10_000)[1]!.colour).toBe("rest");
    // Its window passed untried: the next ring takes over.
    beats[0]!.status = "missed";
    const after = ringLooks(8, beats, WINDOW, 12_500);
    expect(after[1]!.colour).toBe("next");
    expect(after[0]!.colour).toBe("missed");
    expect(ringLooks(8, beats, WINDOW, 12_000 + FLASH_MS + 1)[0]!.colour).toBe("rest");
  });

  it("flashes a ring flown through (our own prediction, then the server's mark) and dims a miss", () => {
    const sent = ringBeat(0, 2, 10_000, "sent");
    expect(ringLooks(8, [sent], WINDOW, 10_100)[2]!.colour).toBe("passed");
    const scored = ringBeat(0, 2, 10_000, "scored", hit(80));
    expect(ringLooks(8, [scored], WINDOW, 12_500)[2]!.colour).toBe("passed");
    const zero = ringBeat(0, 2, 10_000, "scored", hit(0));
    expect(ringLooks(8, [zero], WINDOW, 12_500)[2]!.colour).toBe("missed");
  });

  it("lets the next ring win over a flash on the second lap", () => {
    const beats = [ringBeat(0, 0, 10_000, "sent"), ringBeat(8, 0, 11_000)];
    expect(ringLooks(8, beats, WINDOW, 10_500)[0]!.colour).toBe("next");
  });

  it("ignores rings the layout does not have", () => {
    expect(() => ringLooks(2, [ringBeat(0, 5, 0)], WINDOW, 0)).not.toThrow();
  });

  it("takes its colours from the palette, the next ring brighter than the rest", () => {
    const c = ringColours();
    for (const k of ["rest", "next", "passed", "missed"] as const) expect(c[k].every((v) => v >= 0 && v <= 1)).toBe(true);
    expect(c.next).not.toEqual(c.rest);
    expect(ringBrightness({ colour: "next", glow: 1 })).toBeGreaterThan(ringBrightness({ colour: "rest", glow: 0 }));
  });
});

describe("ring beats in the lesson", () => {
  it("keeps each beat's ring and finds the open ring beats a flight may count for", () => {
    const l = new LessonState("flying");
    l.setPhase({ kind: "practice", round: 1 }, 99_999);
    const beats: LessonBeat[] = [0, 1, 2].map((i) => ({ beat: i, atUnixMs: 10_000 + i * 4_000, prompt: { kind: "ring", ring: i } }) as LessonBeat);
    l.startRound(1, WINDOW, beats);
    expect(l.flying).toBe(true);
    expect(l.picking).toBe(false);
    expect(l.round!.beats.map((b) => b.ring)).toEqual([0, 1, 2]);
    expect(l.openRings(7_000, 7_500)).toEqual([]);
    expect(l.openRings(8_000, 8_100).map((b) => b.beat)).toEqual([0]);
    expect(l.openRings(11_900, 12_100).map((b) => b.beat)).toEqual([0, 1]);
    l.sent(l.round!.beats[0]!, hit(100));
    expect(l.openRings(11_900, 12_100).map((b) => b.beat)).toEqual([1]);
  });

  it("reads the Flying class's three rounds from its file", () => {
    const l = new LessonState("flying");
    expect(l.script?.professorName).toBe(flyingClass.professor.name);
    expect(l.roundsTotal).toBe(3);
  });
});

describe("the Flying class's room", () => {
  beforeAll(() => {
    initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
  });

  it("knows the Flying class is flown from its file, and no other class", () => {
    expect(isFlown("flying")).toBe(true);
    for (const id of ["charms", "potions", "herbology", "creatures", "astronomy"]) expect(isFlown(id), id).toBe(false);
  });

  it("counts a flight through a ring's middle, either way, and not one that misses it", () => {
    const sim = new SimWorld(pitchText);
    const rings = sim.rings();
    expect(rings).toHaveLength(8);
    const r = rings[0]!;
    // The ring faces along its yaw: cross its plane through the middle.
    const fx = Math.sin(r.yaw);
    const fz = Math.cos(r.yaw);
    const a = [r.pos[0] - fx, r.pos[1], r.pos[2] - fz] as const;
    const b = [r.pos[0] + fx, r.pos[1], r.pos[2] + fz] as const;
    expect(sim.passesRing(0, ...a, ...b)).toBe(true);
    expect(sim.passesRing(0, ...b, ...a)).toBe(true);
    expect(sim.passesRing(0, a[0], a[1] + r.radius + 1, a[2], b[0], b[1] + r.radius + 1, b[2])).toBe(false);
    expect(sim.passesRing(1, ...a, ...b)).toBe(false);
    sim.free();
  });
});
