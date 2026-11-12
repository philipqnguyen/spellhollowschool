import { describe, expect, it } from "vitest";
import { composeTRS, quatFromMatrix } from "./math";
import { applyHold, HOLD_ARM_BONES, HOLD_CLIP, HOLD_GRIP_BONES, wandHold, type HoldLayer } from "./wand-hold";
import type { BakedClip, Rig } from "./clips";

/** A rig-like stand-in: named bones, indices by name. */
function fakeRig(names: string[]): Rig {
  const index = new Map(names.map((n, i) => [n, i]));
  return { bones: names, count: names.length, boneIndex: (n: string) => index.get(n) ?? -1 } as unknown as Rig;
}

function quat(axis: [number, number, number], deg: number): [number, number, number, number] {
  const h = (deg * Math.PI) / 360;
  const l = Math.hypot(...axis);
  return [(axis[0] / l) * Math.sin(h), (axis[1] / l) * Math.sin(h), (axis[2] / l) * Math.sin(h), Math.cos(h)];
}

function angleBetween(a: readonly number[], b: readonly number[]): number {
  const d = Math.abs(a[0]! * b[0]! + a[1]! * b[1]! + a[2]! * b[2]! + a[3]! * b[3]!);
  return (2 * Math.acos(Math.min(1, d)) * 180) / Math.PI;
}

describe("the wand hold pose", () => {
  it("takes the right arm and grip bones from the wand_light clip's first frame", () => {
    expect(HOLD_CLIP).toBe("wand_light");
    const names = ["root", "DEF-upper_arm.R", "DEF-forearm.R", "DEF-hand.R", "DEF-f_index.01.R", "DEF-thumb.02.R", "DEF-hand.L"];
    const rig = fakeRig(names);
    const rot = new Float32Array(names.length * 4 * 2);
    names.forEach((_, i) => rot.set(quat([0, 0, 1], 10 * i), i * 4));
    const clip = { name: "wand_light", duration: 1, frames: 2, loop: true, rot, pos: new Float32Array(names.length * 6), speed: 0, procedural: false } as BakedClip;
    const hold = wandHold(rig, clip)!;
    expect([...hold.arm.bones]).toEqual([1, 2, 3]);
    expect([...hold.grip.bones]).toEqual([4, 5]);
    expect([...hold.arm.rot.subarray(4, 8)]).toEqual([...rot.subarray(8, 12)]);
    // Only right-side bones: the left hand is never in a layer.
    for (const n of [...HOLD_ARM_BONES, ...HOLD_GRIP_BONES]) expect(n).toMatch(/\.R(\.\d+)?$/);
    expect(wandHold(rig, null)).toBeNull();
    expect(wandHold(fakeRig(["root"]), clip)).toBeNull();
  });

  it("turns only its bones' rotation toward the hold, keeping translation and scale", () => {
    const pose = new Float32Array(3 * 16);
    const from = quat([1, 0, 0], 20);
    const to = quat([0, 1, 0], 70);
    for (let b = 0; b < 3; b++) composeTRS(0.1 * b, 0.2, 0.3, from[0], from[1], from[2], from[3], 1.2, 1.2, 1.2, pose, b * 16);
    const before = pose.slice();
    const layer: HoldLayer = { bones: Int16Array.from([1]), rot: Float32Array.from(to) };
    applyHold(pose, layer, 0);
    expect([...pose]).toEqual([...before]);
    applyHold(pose, layer, 1);
    // Other bones untouched.
    expect([...pose.subarray(0, 16)]).toEqual([...before.subarray(0, 16)]);
    expect([...pose.subarray(32, 48)]).toEqual([...before.subarray(32, 48)]);
    const m = pose.subarray(16, 32);
    expect(angleBetween(quatFromMatrix(m), to)).toBeLessThan(0.01);
    expect([m[12], m[13], m[14]].map((v) => +v!.toFixed(5))).toEqual([0.1, 0.2, 0.3]);
    expect(Math.hypot(m[0]!, m[1]!, m[2]!)).toBeCloseTo(1.2, 5);
  });

  it("blends part of the way by weight (slerp)", () => {
    const pose = new Float32Array(16);
    composeTRS(0, 0, 0, 0, 0, 0, 1, 1, 1, 1, pose);
    applyHold(pose, { bones: Int16Array.from([0]), rot: Float32Array.from(quat([0, 0, 1], 80)) }, 0.25);
    expect(angleBetween(quatFromMatrix(pose), [0, 0, 0, 1])).toBeCloseTo(20, 3);
  });
});
