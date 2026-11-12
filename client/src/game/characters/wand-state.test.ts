import { describe, expect, it } from "vitest";
import type { Anim } from "../../wasm/sim_wasm";
import { composeTRS, mul4 } from "./math";
import { attachMatrix, FALLBACK_TIP, fallbackMatrix, holdsWand, raiseWanted, riding, stepRaise, stepWand, WAND_GROW_S, WAND_RAISE_S, wandMatrix, wandSize, wandTipOf, wandWanted, type WandRoot } from "./wand-state";

/** Steps the wand `n` frames of `dt` from growth `t`. */
function run(t: number, anim: Anim, held: boolean, n: number, dt = 1 / 60): number {
  for (let i = 0; i < n; i++) t = stepWand(t, anim, held, dt);
  return t;
}

describe("when the wand is out", () => {
  it("comes out while casting and goes away after", () => {
    expect(wandWanted("cast", false)).toBe(true);
    const out = run(0, "cast", false, 10);
    expect(out).toBe(1);
    expect(run(out, "idle", false, 10)).toBe(0);
  });

  it("stays out while the wand is lit, a gesture is traced or Elevara is aimed", () => {
    expect(holdsWand(true, false, false)).toBe(true);
    expect(holdsWand(false, true, false)).toBe(true);
    expect(holdsWand(false, false, true)).toBe(true);
    for (const anim of ["idle", "walk", "run", "sit", "wave"] as const) {
      expect(wandWanted(anim, holdsWand(true, false, false)), anim).toBe(true);
      expect(run(0, anim, true, 20), anim).toBe(1);
    }
  });

  it("is hidden otherwise", () => {
    expect(holdsWand(false, false, false)).toBe(false);
    for (const anim of ["idle", "walk", "run", "sit", "wave", "bow"] as const) {
      expect(wandWanted(anim, false), anim).toBe(false);
      expect(run(0, anim, false, 20), anim).toBe(0);
    }
  });

  it("is never out on a broom, and goes at once on mounting", () => {
    for (const anim of ["fly", "hover"] as const) {
      expect(riding(anim)).toBe(true);
      expect(wandWanted(anim, true)).toBe(false);
      expect(stepWand(1, anim, true, 1 / 60)).toBe(0);
    }
    expect(riding("cast")).toBe(false);
  });

  it("grows in and shrinks away over about 100 ms, not a pop", () => {
    const frames = Math.round(WAND_GROW_S * 60);
    const half = run(0, "cast", false, Math.floor(frames / 2));
    expect(half).toBeGreaterThan(0.3);
    expect(half).toBeLessThan(0.7);
    expect(run(0, "cast", false, frames + 1)).toBe(1);
    const shrinking = run(1, "idle", false, Math.floor(frames / 2));
    expect(shrinking).toBeGreaterThan(0.3);
    expect(shrinking).toBeLessThan(0.7);
    expect(wandSize(0)).toBe(0);
    expect(wandSize(1)).toBe(1);
    expect(wandSize(0.5)).toBeCloseTo(0.5);
    expect(wandSize(0.25)).toBeLessThan(0.25);
  });
});

describe("the arm holding a lit wand up", () => {
  it("raises while held out standing, walking or running, not while casting, sitting or riding", () => {
    for (const anim of ["idle", "walk", "run"] as const) expect(raiseWanted(anim, true), anim).toBe(true);
    for (const anim of ["cast", "sit", "wave", "bow", "fly", "hover"] as const) expect(raiseWanted(anim, true), anim).toBe(false);
    expect(raiseWanted("idle", false)).toBe(false);
  });

  it("eases up and down over WAND_RAISE_S", () => {
    let t = 0;
    const frames = Math.round(WAND_RAISE_S * 60);
    for (let i = 0; i < frames / 2; i++) t = stepRaise(t, "walk", true, 1 / 60);
    expect(t).toBeGreaterThan(0.3);
    expect(t).toBeLessThan(0.7);
    for (let i = 0; i < frames; i++) t = stepRaise(t, "walk", true, 1 / 60);
    expect(t).toBe(1);
    // A cast takes the arm: the raise lets go while the cast clip plays.
    for (let i = 0; i < frames + 1; i++) t = stepRaise(t, "cast", true, 1 / 60);
    expect(t).toBe(0);
  });
});

describe("where the wand is drawn", () => {
  const root: WandRoot = { x: 3, y: 1, z: -2, yaw: Math.PI / 2, scale: 1.1 };
  /** A hand bone 1.2 m up, turned a quarter about Z. */
  const bone = new Float32Array(16);
  composeTRS(0.2, 1.2, 0.1, 0, 0, Math.SQRT1_2, Math.SQRT1_2, 1, 1, 1, bone);

  it("puts the tip where the wand's +Y reaches, through root, bone and attach", () => {
    const attach = attachMatrix({ bone: "socket_hand.R", rotation: [-0.409161, 0.576704, 0.409161, 0.576704], translation: [-0.04935, 0.02118, -0.02086] });
    const m = new Float32Array(16);
    wandMatrix(root, bone, attach, 1, m);
    const tip = { x: 0, y: 0, z: 0 };
    wandTipOf(root, bone, attach, 0.26, tip);
    expect(tip.x).toBeCloseTo(m[12]! + m[4]! * 0.26, 6);
    expect(tip.y).toBeCloseTo(m[13]! + m[5]! * 0.26, 6);
    expect(tip.z).toBeCloseTo(m[14]! + m[6]! * 0.26, 6);
    // The grip-to-tip length scales with the character only.
    expect(Math.hypot(tip.x - m[12]!, tip.y - m[13]!, tip.z - m[14]!)).toBeCloseTo(0.26 * root.scale, 5);
  });

  it("turns with the character's yaw as the renderer's root does (yaw 0 faces +Z)", () => {
    const flat = new Float32Array(16);
    composeTRS(0, 0, 0, 0, 0, 0, 1, 1, 1, 1, flat);
    // A wand lying along the body's +Z (forward): +Y turned a quarter about X.
    const forward = new Float32Array(16);
    composeTRS(0, 0, 0, Math.SQRT1_2, 0, 0, Math.SQRT1_2, 1, 1, 1, forward);
    const tip = { x: 0, y: 0, z: 0 };
    wandTipOf({ x: 0, y: 0, z: 0, yaw: 0, scale: 1 }, flat, forward, 1, tip);
    expect([tip.x, tip.y, tip.z].map((v) => +v.toFixed(5) + 0)).toEqual([0, 0, 1]);
    wandTipOf({ x: 0, y: 0, z: 0, yaw: Math.PI / 2, scale: 1 }, flat, forward, 1, tip);
    // Facing (sin yaw, cos yaw) = +X.
    expect([tip.x, tip.y, tip.z].map((v) => +v.toFixed(5) + 0)).toEqual([1, 0, 0]);
  });

  it("scales the wand about its grip as it grows, the grip staying in the hand", () => {
    const attach = attachMatrix({ bone: "socket_hand.R", rotation: [0, 0, 0, 1], translation: [0.01, 0.02, 0.03] });
    const full = new Float32Array(16);
    const small = new Float32Array(16);
    wandMatrix(root, bone, attach, 1, full);
    wandMatrix(root, bone, attach, 0.5, small, 0);
    for (const i of [12, 13, 14]) expect(small[i]).toBeCloseTo(full[i]!, 6);
    for (const i of [0, 1, 2, 4, 5, 6, 8, 9, 10]) expect(small[i]).toBeCloseTo(full[i]! * 0.5, 6);
    // Written at an offset into a shared buffer, the rest untouched.
    const buf = new Float32Array(48).fill(7);
    wandMatrix(root, bone, attach, 1, buf, 16);
    expect(buf[0]).toBe(7);
    expect(buf[32]).toBe(7);
    expect(buf[16 + 15]).toBe(1);
  });

  it("without a socket hangs the wand along the hand bone with its tip where effects started before", () => {
    const fit = fallbackMatrix(0.26);
    const tip = { x: 0, y: 0, z: 0 };
    const id = { x: 0, y: 0, z: 0, yaw: 0, scale: 1 };
    wandTipOf(id, bone, fit, 0.26, tip);
    // The old tip: FALLBACK_TIP along the bone's +Y.
    const along = new Float32Array(16);
    composeTRS(0, FALLBACK_TIP, 0, 0, 0, 0, 1, 1, 1, 1, along);
    const want = new Float32Array(16);
    mul4(bone, along, want);
    expect(tip.x).toBeCloseTo(want[12]!, 6);
    expect(tip.y).toBeCloseTo(want[13]!, 6);
    expect(tip.z).toBeCloseTo(want[14]!, 6);
  });
});
