import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import gesturesText from "../../../../data/classes/gestures.json?raw";
import { GestureScorer, initSync } from "../../wasm/sim_wasm";
import { GESTURES, SCORING, type GestureDef } from "../spells/data";
import { padPoint, penStep, sampleInterval, TraceRecorder, TRACE_POINTS_MAX, PEN_SPEED } from "./trace";

let scorer: GestureScorer;

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
  scorer = new GestureScorer(gesturesText);
});

/** A deterministic pseudo-random sequence (so the tests never flake). */
function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

/** Points along the template at even spacing, `n` of them. */
function along(g: GestureDef, n: number): [number, number][] {
  const pts = g.points;
  const lens = [0];
  for (let i = 1; i < pts.length; i++) lens.push(lens[i - 1]! + Math.hypot(pts[i]![0] - pts[i - 1]![0], pts[i]![1] - pts[i - 1]![1]));
  const total = lens[lens.length - 1]!;
  const out: [number, number][] = [];
  for (let k = 0; k < n; k++) {
    const want = (total * k) / (n - 1);
    let i = 1;
    while (i < pts.length - 1 && lens[i]! < want) i++;
    const f = (want - lens[i - 1]!) / Math.max(1e-6, lens[i]! - lens[i - 1]!);
    out.push([pts[i - 1]![0] + (pts[i]![0] - pts[i - 1]![0]) * f, pts[i - 1]![1] + (pts[i]![1] - pts[i - 1]![1]) * f]);
  }
  return out;
}

/**
 * A hand-like trace of `g` through the recorder: pointer events every
 * 14-20 ms over `ms`, with wobble, scaled and shifted on the pad.
 */
function handTrace(g: GestureDef, ms: number, seed: number, scale = 0.8, wobble = 4, cutAt = Infinity): TraceRecorder {
  const r = rng(seed);
  const events: number[] = [0];
  while (events[events.length - 1]! < ms) events.push(events[events.length - 1]! + 14 + r() * 6);
  events[events.length - 1] = ms;
  const path = along(g, events.length);
  const at = (i: number): [number, number] => [path[i]![0] * scale + 6 + (r() - 0.5) * wobble, path[i]![1] * scale - 4 + (r() - 0.5) * wobble];
  const [x0, y0] = at(0);
  const t0 = 1000;
  const rec = new TraceRecorder(t0, x0, y0, g.max_ms);
  for (let i = 1; i < events.length - 1; i++) {
    if (events[i]! >= cutAt) {
      // Cut off by the pad (its beat's time is over): ended where the pen is.
      rec.end(t0 + cutAt);
      return rec;
    }
    const [x, y] = at(i);
    rec.move(t0 + events[i]!, x, y);
  }
  const [x1, y1] = at(events.length - 1);
  rec.end(t0 + ms, x1, y1);
  return rec;
}

describe("the trace recorder", () => {
  it("never breaks the wire limits, however long or paused the stroke", () => {
    for (const g of GESTURES) {
      // As slow as allowed, with pauses longer than a byte of milliseconds.
      const rec = new TraceRecorder(0, 0, 0, g.max_ms);
      let t = 0;
      let alive = true;
      for (let i = 0; alive && t < g.max_ms + 500; i++) {
        t += i % 20 === 19 ? 600 : 8;
        alive = rec.move(t, Math.sin(i / 7) * 90, Math.cos(i / 9) * 90);
      }
      rec.end(t);
      expect(rec.points.length).toBeLessThanOrEqual(TRACE_POINTS_MAX);
      for (const p of rec.points.slice(1)) {
        expect(p.dtMs).toBeGreaterThanOrEqual(1);
        expect(p.dtMs).toBeLessThanOrEqual(255);
      }
      expect(rec.points[0]!.dtMs).toBe(0);
      expect(rec.durationMs).toBeLessThanOrEqual(g.max_ms);
      expect(rec.points.reduce((s, p) => s + p.dtMs, 0)).toBe(rec.durationMs);
    }
  });

  it("holds the pen still across a long pause instead of jumping", () => {
    const rec = new TraceRecorder(0, -50, 0, 3000);
    rec.move(20, -40, 0);
    rec.move(720, 40, 0); // 700 ms later
    rec.end(740, 50, 0);
    const pts = rec.points;
    // 0 → 20 ms, then two 255 ms holds at (-40, 0), then the move.
    expect(pts.map((p) => p.dtMs)).toEqual([0, 20, 255, 255, 190, 20]);
    expect(pts[2]).toMatchObject({ x: -40, y: 0 });
    expect(pts[4]).toMatchObject({ x: 40, y: 0 });
    expect(rec.durationMs).toBe(740);
  });

  it("samples no faster than its interval", () => {
    expect(sampleInterval(1000)).toBe(16);
    expect(sampleInterval(3000)).toBeGreaterThanOrEqual(Math.ceil(3000 / TRACE_POINTS_MAX));
    const rec = new TraceRecorder(0, 0, 0, 1000);
    for (let t = 1; t <= 100; t++) rec.move(t, t / 2, 0);
    rec.end(101);
    expect(rec.points.length).toBeLessThanOrEqual(1 + Math.ceil(101 / 16) + 1);
  });

  it("flattens to dt, x, y triples and clamps to the pad", () => {
    const rec = new TraceRecorder(0, 150, -150, 1000);
    rec.end(30, 20, 30);
    expect([...rec.flat()]).toEqual([0, 100, -100, 30, 20, 30]);
  });

  it("maps the pad with y up", () => {
    const rect = { left: 10, top: 20, width: 200, height: 200 };
    expect(padPoint(10, 20, rect)).toEqual([-100, 100]);
    expect(padPoint(210, 220, rect)).toEqual([100, -100]);
    expect(padPoint(110, 120, rect)).toEqual([0, 0]);
    expect(padPoint(-50, 500, rect)).toEqual([-100, -100]);
  });

  it("moves the keyboard pen at one speed in every direction", () => {
    const [x, y] = penStep(0, 0, { left: false, right: true, up: true, down: false }, 100);
    expect(Math.hypot(x, y)).toBeCloseTo(PEN_SPEED * 100, 5);
    expect(penStep(95, 0, { left: false, right: true, up: false, down: false }, 100)).toEqual([100, 0]);
    expect(penStep(3, 4, { left: false, right: false, up: false, down: false }, 100)).toEqual([3, 4]);
  });
});

describe("recorded traces under the server's scorer (sim-core via wasm)", () => {
  it("scores a hand-drawn trace of every class gesture, on the beat", () => {
    for (const g of GESTURES) {
      const ms = Math.min(g.max_ms - 100, Math.max(g.min_ms + 200, 1200));
      const rec = handTrace(g, ms, g.id * 7919);
      const res = scorer.score(g.id, 600, 40, rec.flat());
      expect(res.rejected, `${g.key}: ${res.rejected}`).toBeNull();
      expect(res.score, g.key).toBeGreaterThanOrEqual(SCORING.grades.good);
    }
  });

  it("gives the same verdict for the same samples (the preview is the server's score)", () => {
    const g = GESTURES[0]!;
    const flat = handTrace(g, 1100, 42).flat();
    expect(scorer.score(g.id, 600, 120, flat)).toEqual(scorer.score(g.id, 600, 120, flat));
  });

  it("marks a late start down and a wrong shape as a miss", () => {
    const g = GESTURES.find((x) => x.key === "circle")!;
    const flat = handTrace(g, 1400, 3).flat();
    const onBeat = scorer.score(g.id, 600, 0, flat);
    const late = scorer.score(g.id, 600, 500, flat);
    expect(late.score).toBeLessThan(onBeat.score);
    const zig = GESTURES.find((x) => x.key === "zigzag")!;
    const wrong = scorer.score(zig.id, 600, 0, flat);
    expect(wrong.score).toBeLessThan(SCORING.grades.good);
  });

  it("scores a stroke cut off part way as drawn, lower than the whole", () => {
    for (const key of ["circle", "const_owl"]) {
      const g = GESTURES.find((x) => x.key === key)!;
      const whole = scorer.score(g.id, 600, 0, handTrace(g, 2600, 11).flat());
      const cut = scorer.score(g.id, 600, 0, handTrace(g, 2600, 11, 0.8, 4, 1700).flat());
      expect(whole.rejected, key).toBeNull();
      expect(cut.rejected, key).toBeNull();
      expect(cut.score, key).toBeLessThan(whole.score);
    }
  });

  it("rejects a tap (the controller keeps the beat open for those)", () => {
    const g = GESTURES[0]!;
    const rec = new TraceRecorder(0, 0, 0, g.max_ms);
    rec.move(16, 1, 1);
    rec.end(40, 2, 1);
    expect(scorer.score(g.id, 600, 0, rec.flat()).rejected).not.toBeNull();
  });
});
