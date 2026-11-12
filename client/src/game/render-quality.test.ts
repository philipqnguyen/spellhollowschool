// Character look pass, render quality (docs/art/character-look.md section 6): the
// profile per quality and display density, the outline width scale, the pass
// chain with MSAA, and the dynamic-resolution guard.
import { describe, expect, it } from "vitest";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import { qualityProfile, QUALITY_HINTS } from "../settings";
import { postPasses, sceneSamples } from "./toon/post";
import { outlineScale, OUTLINE_SCALE } from "./toon/outline";
import { GUARD, guardLadder, ResolutionGuard } from "./resolution-guard";

describe("quality profiles per display density", () => {
  it("renders Low at 0.75 of the display's pixels, never above one per CSS pixel", () => {
    expect(qualityProfile("low", 1).resolution).toBeCloseTo(0.75);
    expect(qualityProfile("low", 1.25).resolution).toBeCloseTo(0.9375);
    expect(qualityProfile("low", 2).resolution).toBe(1);
    expect(qualityProfile("low", 3).resolution).toBe(1);
    const low = qualityProfile("low", 2);
    expect(low.sceneSamples).toBe(1);
    expect(low.dynamicResolution).toBe(false);
    expect(low.shadows || low.ssao || low.outlines).toBe(false);
  });

  it("renders Medium and High natively up to DPR 2 with 4x MSAA on the scene target", () => {
    for (const q of ["medium", "high"] as const) {
      expect(qualityProfile(q, 1).resolution).toBe(1);
      expect(qualityProfile(q, 1.5).resolution).toBe(1.5);
      expect(qualityProfile(q, 2).resolution).toBe(2);
      expect(qualityProfile(q, 3).resolution).toBe(2);
      const p = qualityProfile(q, 2);
      expect(p.sceneSamples).toBe(4);
      expect(p.dynamicResolution).toBe(true);
      expect(p.outlines).toBe(true);
    }
    // Castle D1 stays: SSAO on High only.
    expect(qualityProfile("medium", 2).ssao).toBe(false);
    expect(qualityProfile("high", 2).ssao).toBe(true);
    expect(qualityProfile("medium", 0).resolution).toBe(1); // a bogus density reads as 1
    for (const q of ["low", "medium", "high"] as const) expect(QUALITY_HINTS[q].length).toBeGreaterThan(0);
  });
});

describe("outlines: width and the pass chain", () => {
  it("scales the character outline's width with the render height, one at 1000 high", () => {
    expect(outlineScale(675)).toBe(OUTLINE_SCALE.min);
    expect(outlineScale(900)).toBe(1);
    expect(outlineScale(1000)).toBe(1);
    expect(outlineScale(1800)).toBeCloseTo(1.8);
    expect(outlineScale(1350)).toBeCloseTo(1.35);
    expect(outlineScale(4000)).toBe(OUTLINE_SCALE.max);
  });

  it("runs no outline or line AA pass: the scene MSAA smooths the hulls (9.2)", () => {
    const med = qualityProfile("medium", 2);
    expect(postPasses({ ...med, samples: med.sceneSamples })).toEqual(["bright", "blur", "final"]);
    const high = qualityProfile("high", 2);
    expect(postPasses({ ...high, samples: high.sceneSamples })).toEqual(["ssao", "ssaoBlur", "bright", "blur", "final"]);
    expect(sceneSamples({ bloom: true })).toBe(1);
    expect(sceneSamples({ bloom: true, samples: 4 })).toBe(4);
    expect(sceneSamples({ bloom: true, samples: 0 })).toBe(1);
    expect(ShaderStore.ShadersStore["toonLineAaFragmentShader"]).toBeUndefined();
    const final = ShaderStore.ShadersStore["toonFinalFragmentShader"]!;
    expect(final).not.toContain("lineK");
    expect(final).not.toContain("MSAA_EDGE");
    // Every exit writes an opaque pixel to the canvas.
    expect(final).toContain("gl_FragColor = vec4(col, 1.0);");
  });
});

/** Feeds `seconds` of frames of `dt` ms from `t0`; returns the time after and every scale stepped to. */
function run(g: ResolutionGuard, t0: number, seconds: number, dt: number | ((i: number) => number)): { t: number; steps: number[] } {
  const steps: number[] = [];
  let t = t0;
  for (let i = 0; t < t0 + seconds * 1000; i++) {
    const d = typeof dt === "number" ? dt : dt(i);
    t += d;
    const s = g.frame(t, d);
    if (s > 0) steps.push(s);
  }
  return { t, steps };
}

/** Slow frames until the guard steps (the next frames run at the new scale); returns the time of the step. */
function slowUntilStep(g: ResolutionGuard, t0: number, dt: number, maxS = 5): { t: number; steps: number[] } {
  let t = t0;
  while (t < t0 + maxS * 1000) {
    t += dt;
    const s = g.frame(t, dt);
    if (s > 0) return { t, steps: [s] };
  }
  return { t, steps: [] };
}

describe("the dynamic-resolution guard", () => {
  it("uses the ladder's rungs below the top", () => {
    expect(guardLadder(2)).toEqual([2, 1.5, 1.25]);
    expect(guardLadder(1.5)).toEqual([1.5, 1.25]);
    expect(guardLadder(1)).toEqual([1]);
  });

  it("never steps on a healthy 60 Hz frame with vsync jitter, nor during the warm-up", () => {
    const g = new ResolutionGuard(2);
    // Loading: slow frames, all inside the warm-up.
    let r = run(g, 0, GUARD.warmupS - 0.5, 40);
    expect(r.steps).toEqual([]);
    // 16.7 ms with up to +-1.5 ms of jitter.
    r = run(g, r.t, 30, (i) => 16.7 + ((i * 37) % 7 - 3) * 0.5);
    expect(r.steps).toEqual([]);
    expect(g.scale).toBe(2);
  });

  it("ignores hitches: one long frame now and then is not slowness", () => {
    const g = new ResolutionGuard(2);
    const r = run(g, 0, 40, (i) => (i % 30 === 0 ? 250 : 8));
    expect(r.steps).toEqual([]);
  });

  it("steps down one rung at a time, at most one step per 2 s, and stops at the bottom", () => {
    const g = new ResolutionGuard(2);
    let r = run(g, 0, GUARD.warmupS, 8);
    // 33 ms frames (a missed 60 Hz frame every time): too slow at every rung.
    r = run(g, r.t, 1.9, 33);
    expect(r.steps).toEqual([]);
    r = run(g, r.t, 0.4, 33);
    expect(r.steps).toEqual([1.5]);
    r = run(g, r.t, 1.5, 33);
    expect(r.steps).toEqual([]);
    r = run(g, r.t, 1, 33);
    expect(r.steps).toEqual([1.25]);
    r = run(g, r.t, 20, 33);
    expect(r.steps).toEqual([]);
    expect(g.scale).toBe(1.25);
    expect(g.steps).toBe(2);
  });

  it("steps back up after 10 s of fast frames, and waits longer after a bounce", () => {
    const g = new ResolutionGuard(2);
    let r = run(g, 0, GUARD.warmupS, 8);
    r = slowUntilStep(g, r.t, 30);
    expect(g.scale).toBe(1.5);
    // Not yet: under 10 s of fast frames.
    r = run(g, r.t, 9, 8);
    expect(r.steps).toEqual([]);
    r = run(g, r.t, 1.5, 8);
    expect(r.steps).toEqual([2]);
    // Slow again at the top soon after: down, and the next step up waits twice as long.
    r = slowUntilStep(g, r.t, 30);
    expect(r.steps).toEqual([1.5]);
    r = run(g, r.t, 15, 8);
    expect(r.steps).toEqual([]);
    r = run(g, r.t, 6, 8);
    expect(r.steps).toEqual([2]);
  });

  it("does not step up while frames are only moderately fast", () => {
    const g = new ResolutionGuard(2);
    let r = run(g, 0, GUARD.warmupS, 8);
    r = slowUntilStep(g, r.t, 30);
    expect(g.scale).toBe(1.5);
    // 14 ms frames: not slow, not fast (95 % under 12 ms): stays.
    r = run(g, r.t, 30, 14);
    expect(r.steps).toEqual([]);
    expect(g.scale).toBe(1.5);
  });

  it("starts over on a reset and has nothing to do at DPR 1", () => {
    const g = new ResolutionGuard(2);
    let r = run(g, 0, GUARD.warmupS, 8);
    r = slowUntilStep(g, r.t, 30);
    expect(g.scale).toBe(1.5);
    g.reset(1);
    expect(g.scale).toBe(1);
    r = run(g, r.t, 30, 40);
    expect(r.steps).toEqual([]);
    expect(g.scale).toBe(1);
  });
});
