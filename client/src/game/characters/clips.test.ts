import { describe, expect, it } from "vitest";
import { Gltf } from "./gltf";
import { partFromGltf } from "./parts";
import { animForClipName, bakeClip, clipSet, proceduralClip, Rig, samplePose, ANIMS } from "./clips";
import { Animator, playbackRate } from "./animator";
import { greyboxSkeleton } from "./greybox";
import { fixtureGlb } from "./fixtures";
import type { ClipData } from "./parts";
import { mul4, quatAxisAngle, transformPoint } from "./math";

const SPEEDS = { walk: 3, run: 6.5 };
const fixtureRig = (opts = {}) => {
  const part = partFromGltf(Gltf.parse(fixtureGlb(opts)));
  return { rig: new Rig(part.skeleton!), part };
};

/** World matrices from local ones (parents first). */
function worlds(rig: Rig, local: Float32Array): Float32Array {
  const w = new Float32Array(local.length);
  for (let i = 0; i < rig.count; i++) {
    const p = rig.parents[i]!;
    if (p < 0) w.set(local.subarray(i * 16, i * 16 + 16), i * 16);
    else mul4(w, local, w, p * 16, i * 16, i * 16);
  }
  return w;
}

describe("Rig", () => {
  it("computes rest world positions", () => {
    const { rig } = fixtureRig();
    expect(rig.bonePos("DEF-spine.006")).toEqual([0, 1.5, 0]);
    expect(rig.bonePos("nope")).toBeNull();
  });

  it("applies an armature pre-transform to roots when posing", () => {
    const { rig } = fixtureRig({ armatureOffset: [0, 0.5, 0] });
    const local = new Float32Array(rig.count * 16);
    samplePose(rig, [], local);
    const w = worlds(rig, local);
    expect(w[2 * 16 + 13]).toBeCloseTo(2.0); // head at 1.5 + 0.5
  });

  it("poses the rest pose so skinning is the identity (world · inverse bind)", () => {
    const rig = new Rig(greyboxSkeleton(0));
    const local = new Float32Array(rig.count * 16);
    samplePose(rig, [], local);
    const w = worlds(rig, local);
    const skin = new Float32Array(16);
    for (let i = 0; i < rig.count; i++) {
      mul4(w, rig.data.inverseBind, skin, i * 16, i * 16, 0);
      const p = transformPoint(skin, 0.3, 1.2, -0.4);
      expect(p[0]).toBeCloseTo(0.3);
      expect(p[1]).toBeCloseTo(1.2);
      expect(p[2]).toBeCloseTo(-0.4);
    }
  });
});

describe("clips", () => {
  it("matches clip names to animations", () => {
    expect(animForClipName("Armature|Walk_Loop")).toBe("walk");
    expect(animForClipName("idle")).toBe("idle");
    expect(animForClipName("sit_ground")).toBe("sit");
    expect(animForClipName("Running")).toBe("run");
    // The broom clips.
    expect(animForClipName("broom_ride")).toBe("fly");
    expect(animForClipName("Armature|broom_hover")).toBe("hover");
    expect(animForClipName("broom_stand")).toBeNull();
    // Protocol v5: `cast` is an animation too.
    expect(animForClipName("cast_spell")).toBe("cast");
    expect(animForClipName("dance_party")).toBeNull();
    // Wand-gesture clips are never built-in animations.
    expect(animForClipName("gesture_colour_wave")).toBeNull();
    expect(animForClipName("Armature|gesture_circle")).toBeNull();
  });

  it("resamples authored clips per rig and blends layers", () => {
    const { rig, part } = fixtureRig({ animation: "walk" });
    const clip = bakeClip(rig, part.clips[0]!, true, 4);
    expect(clip.loop).toBe(true);
    expect(clip.frames).toBe(30); // 1 s at 30 fps; the repeated last key is dropped for loops
    const local = new Float32Array(rig.count * 16);
    samplePose(rig, [{ clip, time: 0.5, weight: 1 }], local);
    // DEF-spine rotated about X by 0.25 rad at t = 0.5: the Y axis tips toward +Z.
    const spine = 1;
    expect(local[spine * 16 + 6]).toBeCloseTo(Math.sin(0.25), 2);
    // Blending half with the rest pose halves the angle (nlerp, close enough).
    samplePose(rig, [{ clip, time: 0.5, weight: 1 }, { clip: proceduralClip(rig, "idle", SPEEDS), time: 0, weight: 1 }], local);
    expect(local[spine * 16 + 6]).toBeCloseTo(Math.sin(0.125), 2);
  });

  it("fills every animation of a clip set, preferring authored clips", () => {
    const { rig, part } = fixtureRig({ animation: "Walk" });
    const set = clipSet(rig, part.clips, SPEEDS);
    for (const a of ANIMS) expect(set.get(a)).toBeDefined();
    expect(set.get("walk")!.procedural).toBe(false);
    expect(set.get("run")!.procedural).toBe(true);
    expect(set.get("walk")!.speed).toBe(3);
  });

  it("procedural sit lowers the hips and walk swings the legs in opposition", () => {
    const rig = new Rig(greyboxSkeleton(0));
    const sit = proceduralClip(rig, "sit", SPEEDS);
    expect(sit.loop).toBe(false);
    const local = new Float32Array(rig.count * 16);
    samplePose(rig, [{ clip: sit, time: sit.duration, weight: 1 }], local);
    const w = worlds(rig, local);
    const hips = rig.boneIndex("DEF-spine");
    expect(w[hips * 16 + 13]!).toBeLessThan(rig.bonePos("DEF-spine")![1] - 0.5);

    const walk = proceduralClip(rig, "walk", SPEEDS);
    samplePose(rig, [{ clip: walk, time: walk.duration / 4, weight: 1 }], local);
    const ww = worlds(rig, local);
    const knee = (side: string) => ww[rig.boneIndex(`DEF-shin.${side}`) * 16 + 14]!; // z of the knee
    expect(knee("L")).toBeGreaterThan(0.05); // left leg forward (+Z)
    expect(knee("R")).toBeLessThan(-0.05);
  });

  it("procedural turns work on any rest orientation", () => {
    // Give the left thigh a rest orientation a quarter turn about its length (as a
    // Rigify bone roll would): the model-space swing still moves the knee forward.
    const s = greyboxSkeleton(0);
    const thigh = s.bones.indexOf("DEF-thigh.L");
    s.rest.set(quatAxisAngle(0, 1, 0, Math.PI / 2), thigh * 10 + 3);
    const rig = new Rig(s);
    const walk = proceduralClip(rig, "walk", SPEEDS);
    const local = new Float32Array(rig.count * 16);
    samplePose(rig, [{ clip: walk, time: walk.duration / 4, weight: 1 }], local);
    expect(worlds(rig, local)[rig.boneIndex("DEF-shin.L") * 16 + 14]!).toBeGreaterThan(0.05);
  });
});

describe("sitting down", () => {
  const rig = new Rig(greyboxSkeleton(0));
  const clip = (name: string, duration: number): ClipData => ({ name, duration, tracks: new Map() });

  it("chains the art's sit-down into its sitting loop", () => {
    expect(animForClipName("sit_down")).toBe("sit_intro");
    const set = clipSet(rig, [clip("sit", 3), clip("sit_down", 1.2), clip("wave", 2.5)], SPEEDS);
    const sit = set.get("sit")!;
    expect(sit.name).toBe("sit_down");
    expect(sit.loop).toBe(false);
    expect(sit.next?.name).toBe("sit");
    expect(sit.next?.loop).toBe(true);
    expect(set.get("wave")!.loop).toBe(false); // waves once, as long as the emote
  });

  it("sits on the floor with the art's floor_rest when there is no seat", () => {
    expect(animForClipName("floor_rest")).toBe("sit_floor");
    expect(animForClipName("desk_rest")).toBeNull();
    const set = clipSet(rig, [clip("sit", 3), clip("sit_down", 1.2), clip("floor_rest", 3)], SPEEDS);
    expect(set.get("sit_floor")!.name).toBe("floor_rest");
    expect(set.get("sit_floor")!.loop).toBe(true);
    expect(set.get("sit")!.name).toBe("sit_down");
    // Without the clip (older art, greybox) the floor sit is the sit clip.
    expect(clipSet(rig, [clip("sit", 3)], SPEEDS).get("sit_floor")!.name).toBe("sit");
  });

  it("sits on a floor seat with the art's floor_sit, else the floor sit", () => {
    expect(animForClipName("floor_sit")).toBe("floor_sit");
    expect(animForClipName("Armature|floor_sit")).toBe("floor_sit");
    expect(animForClipName("floor_rest")).toBe("sit_floor");
    const set = clipSet(rig, [clip("sit", 3), clip("floor_rest", 3), clip("floor_sit", 4)], SPEEDS);
    expect(set.get("floor_sit")!.name).toBe("floor_sit");
    expect(set.get("floor_sit")!.loop).toBe(true);
    expect(set.get("sit_floor")!.name).toBe("floor_rest");
    // Until the art has it: the floor sit, a pose on the floor (never the chair sit's hanging legs).
    expect(clipSet(rig, [clip("sit", 3), clip("floor_rest", 3)], SPEEDS).get("floor_sit")!.name).toBe("floor_rest");
  });

  it("the animator moves on from the intro and starts it again next time", () => {
    const set = clipSet(rig, [clip("sit", 3), clip("sit_down", 1.2)], SPEEDS);
    const a = new Animator(set);
    a.set("sit", 0);
    a.update(1.5);
    const layer = a.layers[0]!;
    expect(layer.clip.name).toBe("sit");
    expect(layer.time).toBeCloseTo(0.3);
    a.set("idle", 0);
    a.update(0.1);
    a.set("sit", 0);
    expect(a.layers.find((l) => l.clip.name.startsWith("sit"))!.clip.name).toBe("sit_down");
  });
});

describe("Animator", () => {
  const rig = new Rig(greyboxSkeleton(1));
  const clips = clipSet(rig, [], SPEEDS);

  it("starts on its first animation without fading in", () => {
    const a = new Animator(clips);
    a.set("idle", 0);
    expect(a.layers).toHaveLength(1);
    expect(a.layers[0]!.weight).toBe(1);
  });

  it("crossfades to a new animation and drops the old one", () => {
    const a = new Animator(clips);
    a.set("idle", 0);
    a.set("walk", 4);
    expect(a.layers).toHaveLength(2);
    a.update(0.1);
    const [idle, walk] = a.layers;
    expect(idle!.weight).toBeCloseTo(0.5);
    expect(walk!.weight).toBeCloseTo(0.5);
    a.update(0.2);
    expect(a.layers).toHaveLength(1);
    expect(a.current).toBe("walk");
  });

  it("keeps the stride phase between walk and run", () => {
    const a = new Animator(clips);
    a.set("walk", 3);
    a.update(clips.get("walk")!.duration * 0.25);
    a.set("run", 6.5);
    const run = a.layers.find((l) => l.clip === clips.get("run"))!;
    expect(run.time / run.clip.duration).toBeCloseTo(0.25, 2);
  });

  it("scales locomotion playback with ground speed, within bounds", () => {
    const walk = clips.get("walk")!;
    expect(playbackRate(walk, 3)).toBe(1);
    expect(playbackRate(walk, 1.5)).toBe(0.55);
    expect(playbackRate(walk, 4.5)).toBeCloseTo(1.5);
    expect(playbackRate(clips.get("idle")!, 3)).toBe(1);
  });

  it("plays once-clips to the end and holds", () => {
    const a = new Animator(clips);
    a.set("idle", 0);
    a.set("bow", 0);
    a.update(5);
    const bow = a.layers.find((l) => l.clip === clips.get("bow"))!;
    expect(bow.time).toBe(bow.clip.duration);
  });
});
