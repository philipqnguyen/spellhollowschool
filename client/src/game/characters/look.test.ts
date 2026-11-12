// Characters look at their target (look.ts): the easing, the limits and the
// hold behind, the frames, and the bone turn on a rigify skeleton.

import { describe, expect, it } from "vitest";
import { Rig, samplePose } from "./clips";
import { greyboxSkeleton } from "./greybox";
import type { SkeletonData } from "./parts";
import { anglesTo, applyLook, bodyToWorld, EYE_BONES, eyeInBody, LOOK_BONES, LOOK_PITCH_DOWN, LOOK_PITCH_UP, LOOK_SHARES, LOOK_YAW_MAX, lookBones, lookState, modelMatrix, smoothDamp, stepLook, worldToBody, type LookState } from "./look";

const DEG = Math.PI / 180;

/** Steps `s` for `secs` at `hz` toward (yaw, pitch), recording every yaw. */
function run(s: LookState, yaw: number, pitch: number, secs: number, hz = 60): number[] {
  const out: number[] = [];
  const dt = 1 / hz;
  for (let t = 0; t < secs - 1e-9; t += dt) {
    stepLook(s, yaw, pitch, dt);
    out.push(s.yaw);
  }
  return out;
}

describe("smoothDamp (the critically damped spring)", () => {
  it("reaches the target without overshoot, most of the way in about the smoothing time", () => {
    const v: [number] = [0];
    let x = 0;
    let speed = 0;
    const seen: number[] = [];
    for (let i = 0; i < 120; i++) {
      x = smoothDamp(x, 1, speed, 0.25, 1 / 60, v);
      speed = v[0];
      seen.push(x);
    }
    for (let i = 1; i < seen.length; i++) expect(seen[i]!).toBeGreaterThanOrEqual(seen[i - 1]!);
    expect(Math.max(...seen)).toBeLessThanOrEqual(1);
    // A quarter second in: well under way; half a second: about 90 %; two seconds: there.
    expect(seen[14]!).toBeGreaterThan(0.5);
    expect(seen[29]!).toBeGreaterThan(0.88);
    expect(seen[119]!).toBeGreaterThan(0.999);
  });

  it("does not depend on the frame rate", () => {
    const at = (hz: number) => {
      const s = lookState();
      run(s, 40 * DEG, 0, 0.4, hz);
      return s.yaw;
    };
    expect(Math.abs(at(144) - at(15))).toBeLessThan(1.5 * DEG);
    expect(Math.abs(at(60) - at(30))).toBeLessThan(0.5 * DEG);
  });
});

describe("stepLook (limits and the hold behind)", () => {
  it("follows a target inside the limits and comes back to neutral without one", () => {
    const s = lookState();
    expect(s.active).toBe(false);
    run(s, 50 * DEG, 10 * DEG, 2);
    expect(s.yaw).toBeCloseTo(50 * DEG, 3);
    expect(s.pitch).toBeCloseTo(10 * DEG, 3);
    expect(s.active).toBe(true);
    run(s, NaN, 0, 3);
    expect(s.yaw).toBe(0);
    expect(s.pitch).toBe(0);
    expect(s.active).toBe(false);
  });

  it("clamps the pitch to 25 degrees up and 30 down", () => {
    const s = lookState();
    run(s, 0, 60 * DEG, 3);
    expect(s.pitch).toBeCloseTo(LOOK_PITCH_UP, 3);
    run(s, 0, -80 * DEG, 3);
    expect(s.pitch).toBeCloseTo(-LOOK_PITCH_DOWN, 3);
    expect(LOOK_PITCH_UP).toBeCloseTo(25 * DEG, 6);
    expect(LOOK_PITCH_DOWN).toBeCloseTo(30 * DEG, 6);
  });

  it("holds at the 85 degree limit for a target behind for as long as it is the target", () => {
    const s = lookState();
    expect(LOOK_YAW_MAX).toBeCloseTo(85 * DEG, 6);
    run(s, 60 * DEG, 0, 2);
    // The target walks round behind (120 degrees): the head stops at the limit...
    const held = run(s, 120 * DEG, 0, 0.7);
    expect(Math.max(...held)).toBeLessThanOrEqual(LOOK_YAW_MAX + 1e-6);
    expect(s.yaw).toBeGreaterThan(LOOK_YAW_MAX - 2 * DEG);
    // ...and stays there while the target stays behind (no going back to straight ahead).
    run(s, 120 * DEG, 0, 10);
    expect(s.yaw).toBeCloseTo(LOOK_YAW_MAX, 3);
    // Only losing the target lets it go back to neutral, with no snap.
    const back = run(s, NaN, 0, 3);
    for (let i = 1; i < back.length; i++) expect(Math.abs(back[i]! - back[i - 1]!)).toBeLessThan(7 * DEG);
    expect(Math.abs(s.yaw)).toBeLessThan(0.5 * DEG);
  });

  it("never twists through the back when the target crosses behind", () => {
    const s = lookState();
    run(s, 65 * DEG, 0, 2);
    // The target goes behind on the left and comes out behind on the right.
    const seen = [...run(s, 150 * DEG, 0, 0.2), ...run(s, 179 * DEG, 0, 0.1), ...run(s, -170 * DEG, 0, 0.2), ...run(s, -120 * DEG, 0, 0.2)];
    // Held on the left side all along (never swung over to the right).
    for (const y of seen) expect(y).toBeGreaterThan(0);
    // Back in front on the right: the head comes across the front, smoothly.
    const across = run(s, -40 * DEG, 0, 2);
    for (let i = 1; i < across.length; i++) expect(Math.abs(across[i]! - across[i - 1]!)).toBeLessThan(7 * DEG);
    expect(s.yaw).toBeCloseTo(-40 * DEG, 2);
  });

  it("does not snap when the target changes", () => {
    const s = lookState();
    run(s, 60 * DEG, 0, 2);
    const seen = run(s, -60 * DEG, 0, 2);
    // The turn starts from rest (the speed is continuous: the first frame moves little)...
    expect(Math.abs(seen[0]! - 60 * DEG)).toBeLessThan(1.5 * DEG);
    // ...and no frame jumps (a 120 degree turn peaks under 7 degrees a frame at 60 Hz).
    for (let i = 1; i < seen.length; i++) expect(Math.abs(seen[i]! - seen[i - 1]!)).toBeLessThan(7 * DEG);
    expect(s.yaw).toBeCloseTo(-60 * DEG, 2);
  });
});

describe("frames", () => {
  it("puts a point ahead of a character at +z of its body, and back", () => {
    const out = [0, 0, 0];
    const yaw = 0.7;
    // Ahead of a character facing `yaw` is (sin yaw, cos yaw) in the world (castle/seats.ts, the renderer's root).
    worldToBody(10 + Math.sin(yaw) * 2, 1.5, -4 + Math.cos(yaw) * 2, 10, 0, -4, yaw, 1, out);
    expect(out[0]).toBeCloseTo(0, 6);
    expect(out[1]).toBeCloseTo(1.5, 6);
    expect(out[2]).toBeCloseTo(2, 6);
    const back = [0, 0, 0];
    worldToBody(3, 2, 1, 1, 0, -1, 2.1, 1.2, out);
    bodyToWorld(out[0]!, out[1]!, out[2]!, 1, 0, -1, 2.1, 1.2, back);
    expect(back[0]).toBeCloseTo(3, 6);
    expect(back[1]).toBeCloseTo(2, 6);
    expect(back[2]).toBeCloseTo(1, 6);
  });

  it("measures yaw toward the body's +x and pitch up as positive", () => {
    const a: [number, number] = [0, 0];
    anglesTo([0, 1.6, 0], [1, 1.6, 1], a);
    expect(a[0]).toBeCloseTo(45 * DEG, 6);
    expect(a[1]).toBeCloseTo(0, 6);
    anglesTo([0, 1.6, 0], [0, 2.6, 1], a);
    expect(a[1]).toBeCloseTo(45 * DEG, 6);
  });
});

describe("applyLook (the bone turn)", () => {
  const rig = new Rig(greyboxSkeleton(0));
  const bones = lookBones(rig)!;
  const rest = new Float32Array(rig.count * 16);
  samplePose(rig, [], rest);
  const head = bones[LOOK_BONES.length - 1]!;
  const m = new Float32Array(16);

  /** The head's model rotation applied to +z (where the face points, for a bone that faces ahead at rest). */
  function headForward(pose: Float32Array): [number, number, number] {
    modelMatrix(rig, pose, head, m);
    // Third column, normalised (a scaled rig keeps its direction).
    const l = Math.hypot(m[8]!, m[9]!, m[10]!);
    return [m[8]! / l, m[9]! / l, m[10]! / l];
  }

  it("finds the chest, neck and head bones, and the shares sum to one", () => {
    expect([...bones].every((b) => b >= 0)).toBe(true);
    expect(LOOK_SHARES.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 9);
    // The head and the neck most, spread so no one joint twists far; the chest a little (40/50/10).
    expect(LOOK_SHARES[3]).toBeCloseTo(0.4, 9);
    expect(LOOK_SHARES[1]! + LOOK_SHARES[2]!).toBeCloseTo(0.5, 9);
    expect(LOOK_SHARES[0]).toBeCloseTo(0.1, 9);
    // No one bone turns more than 40 % of the look relative to its parent (no candy wrap at the head).
    for (const share of LOOK_SHARES) expect(share).toBeLessThanOrEqual(0.4 + 1e-9);
  });

  it("turns the face toward the target's direction and moves no bone's head", () => {
    // In the greybox the head bone points up; its model z axis is the body's +z at rest.
    const before = headForward(rest);
    expect(before[2]).toBeCloseTo(1, 4);
    for (const [yaw, pitch] of [
      [40 * DEG, 0],
      [-55 * DEG, 0],
      [0, 20 * DEG],
      [0, -25 * DEG],
      [30 * DEG, 15 * DEG],
      [-70 * DEG, -20 * DEG],
    ] as const) {
      const pose = rest.slice();
      applyLook(rig, pose, bones, yaw, pitch);
      const f = headForward(pose);
      const want = [Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch)];
      const angle = Math.acos(Math.min(1, f[0] * want[0]! + f[1] * want[1]! + f[2] * want[2]!));
      expect(angle).toBeLessThan(3 * DEG);
      // The turn changes rotations only: every bone's head stays where its parent puts it.
      for (let b = 0; b < rig.count; b++) {
        for (const k of [12, 13, 14]) expect(pose[b * 16 + k]).toBeCloseTo(rest[b * 16 + k]!, 6);
      }
    }
  });

  it("turns the head with no roll: yaw about the body's up, then pitch about the yawed side axis", () => {
    // Pitching each bone about the body's un-yawed side axis once the bones below have turned
    // rolled the head (ear toward the shoulder): 6 degrees at 60/20, 10 at the limit looking up.
    for (const [yaw, pitch] of [
      [60 * DEG, 20 * DEG],
      [LOOK_YAW_MAX, LOOK_PITCH_UP],
      [-LOOK_YAW_MAX, -LOOK_PITCH_DOWN],
      [-40 * DEG, 25 * DEG],
    ] as const) {
      const pose = rest.slice();
      applyLook(rig, pose, bones, yaw, pitch);
      let yawSoFar = 0;
      let pitchSoFar = 0;
      for (let i = 0; i < LOOK_BONES.length; i++) {
        yawSoFar += LOOK_SHARES[i]! * yaw;
        pitchSoFar += LOOK_SHARES[i]! * pitch;
        modelMatrix(rig, pose, bones[i]!, m);
        const lx = Math.hypot(m[0]!, m[1]!, m[2]!);
        const lz = Math.hypot(m[8]!, m[9]!, m[10]!);
        // The side axis stays level (no roll)...
        expect(Math.abs(Math.asin(m[1]! / lx))).toBeLessThan(0.05 * DEG);
        // ...and every bone faces exactly its running yaw and pitch.
        expect(Math.atan2(m[8]!, m[10]!)).toBeCloseTo(yawSoFar, 4);
        expect(Math.asin(m[9]! / lz)).toBeCloseTo(pitchSoFar, 4);
      }
    }
  });

  it("gives the chest only its share", () => {
    const pose = rest.slice();
    applyLook(rig, pose, bones, 50 * DEG, 0);
    modelMatrix(rig, pose, bones[0]!, m);
    const chestYaw = Math.atan2(m[8]!, m[10]!);
    expect(chestYaw).toBeCloseTo(5 * DEG, 3);
  });

  it("measures the eyes above the head bone on a rig without eye bones", () => {
    const eye = [0, 0, 0];
    eyeInBody(rig, rest, head, eye);
    expect(eye[1]).toBeGreaterThan(1.5);
    expect(Math.abs(eye[0]!)).toBeLessThan(0.01);
  });
});

/** `data` with eye bones under its head bone at `offset` (x for the left eye; m, the body's frame: +z ahead). */
function withEyes(data: SkeletonData, offset: [number, number, number]): SkeletonData {
  const head = data.bones.indexOf("DEF-spine.006");
  const n = data.bones.length;
  const parents = new Int16Array(n + 2);
  parents.set(data.parents);
  const rest = new Float32Array((n + 2) * 10);
  rest.set(data.rest);
  const inverseBind = new Float32Array((n + 2) * 16);
  inverseBind.set(data.inverseBind);
  const rootPre = new Float32Array((n + 2) * 16);
  rootPre.set(data.rootPre);
  EYE_BONES.forEach((_, k) => {
    const i = n + k;
    parents[i] = head;
    rest.set([k ? -offset[0] : offset[0], offset[1], offset[2], 0, 0, 0, 1, 1, 1, 1], i * 10);
    inverseBind.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], i * 16);
    rootPre.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], i * 16);
  });
  return { bones: [...data.bones, ...EYE_BONES], parents, rest, inverseBind, rootPre };
}

describe("eyeInBody (where the look is aimed from)", () => {
  // The wrapped female: her eyes 9 cm above and 11 cm ahead of her head bone's head.
  const OFFSET: [number, number, number] = [0.04, 0.091, 0.108];
  const rig = new Rig(withEyes(greyboxSkeleton(1), OFFSET));
  const bones = lookBones(rig)!;
  const head = bones[LOOK_BONES.length - 1]!;
  const rest = new Float32Array(rig.count * 16);
  samplePose(rig, [], rest);
  const m = new Float32Array(16);

  it("sits between the eye bones, ahead of the head bone", () => {
    const eye = [0, 0, 0];
    eyeInBody(rig, rest, head, eye);
    modelMatrix(rig, rest, head, m);
    expect(eye[0]).toBeCloseTo(m[12]!, 6);
    expect(eye[1]! - m[13]!).toBeCloseTo(OFFSET[1], 5);
    expect(eye[2]! - m[14]!).toBeCloseTo(OFFSET[2], 5);
  });

  it("turns with the head", () => {
    const pose = rest.slice();
    applyLook(rig, pose, bones, 60 * DEG, 20 * DEG);
    const eye = [0, 0, 0];
    eyeInBody(rig, pose, head, eye);
    modelMatrix(rig, pose, head, m);
    // The offset carried by the head's model rotation.
    for (let a = 0; a < 3; a++) {
      const want = m[12 + a]! + m[4 + a]! * OFFSET[1] + m[8 + a]! * OFFSET[2];
      expect(eye[a]).toBeCloseTo(want, 5);
    }
    // Ahead of the head bone along the turned face, not behind or above it.
    const dx = eye[0]! - m[12]!;
    const dz = eye[2]! - m[14]!;
    expect(Math.atan2(dx, dz)).toBeCloseTo(60 * DEG, 1);
  });
});
