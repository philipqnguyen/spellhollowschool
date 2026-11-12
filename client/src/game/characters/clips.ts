// Skeleton animation data shared by every character on a rig.
//
// A `Rig` is one body type's skeleton (bone order, rest pose, inverse bind
// matrices). Clips are resampled once per rig into dense frames for every
// bone (`BakedClip`), so posing a character is a lookup, a blend and a matrix
// compose per bone: no Babylon animation objects, nothing allocated per
// frame, and all characters on the rig share the same arrays.
//
// When the art has no clip for an animation (or no skeleton at all yet), a
// procedural clip is generated for the spec's Rigify bone names, rotating
// bones about model-space axes so it works on any rest orientation.
//
// Pure, unit tested.

import type { Anim } from "../../wasm/sim_wasm";
import { composeTRS, mul4, quatAxisAngle, quatConj, quatFromMatrix, quatMul, slerpInto, type Quat } from "./math";
import type { ChannelData, ClipData, SkeletonData } from "./parts";

export const CLIP_FPS = 30;
// Protocol v8 `fly` / `hover` (on a broom): the authored
// `broom_ride` and `broom_hover` clips where the art has them (animForClipName),
// else a procedural seat astride the handle (rideFrame).
export const ANIMS: readonly Anim[] = ["idle", "walk", "run", "wave", "bow", "sit", "cast", "fly", "hover"];

/** How each animation plays: looping, or once and then held on its last frame. */
export const ANIM_LOOPS: Record<Anim, boolean> = { idle: true, walk: true, run: true, wave: false, bow: false, sit: true, cast: false, fly: true, hover: true };

export class Rig {
  readonly bones: readonly string[];
  readonly parents: Int16Array;
  readonly count: number;
  /** Rest world rotation of every bone (including any root pre-transform). */
  readonly restWorldRot: Float32Array;
  /** Rest (bind) world position of every bone's head. */
  readonly restWorldPos: Float32Array;
  readonly hasRootPre: Uint8Array;
  private readonly index: Map<string, number>;

  constructor(readonly data: SkeletonData) {
    this.bones = data.bones;
    this.parents = data.parents;
    this.count = data.bones.length;
    this.index = new Map(data.bones.map((b, i) => [b, i]));
    this.restWorldRot = new Float32Array(this.count * 4);
    this.restWorldPos = new Float32Array(this.count * 3);
    this.hasRootPre = new Uint8Array(this.count);
    const world = new Float32Array(this.count * 16);
    const local = new Float32Array(16);
    const tmp = new Float32Array(16);
    for (let i = 0; i < this.count; i++) {
      const r = data.rest;
      composeTRS(r[i * 10]!, r[i * 10 + 1]!, r[i * 10 + 2]!, r[i * 10 + 3]!, r[i * 10 + 4]!, r[i * 10 + 5]!, r[i * 10 + 6]!, r[i * 10 + 7]!, r[i * 10 + 8]!, r[i * 10 + 9]!, local);
      const p = this.parents[i]!;
      if (p >= 0) mul4(world, local, tmp, p * 16, 0, 0);
      else {
        const pre = data.rootPre.subarray(i * 16, i * 16 + 16);
        this.hasRootPre[i] = isIdentity(pre) ? 0 : 1;
        mul4(pre, local, tmp);
      }
      world.set(tmp, i * 16);
      const q = quatFromMatrix(tmp);
      this.restWorldRot.set(q, i * 4);
      this.restWorldPos.set([tmp[12]!, tmp[13]!, tmp[14]!], i * 3);
    }
  }

  boneIndex(name: string): number {
    return this.index.get(name) ?? -1;
  }

  /** Rest world position of `name`, or null when the rig has no such bone. */
  bonePos(name: string): [number, number, number] | null {
    const i = this.boneIndex(name);
    return i < 0 ? null : [this.restWorldPos[i * 3]!, this.restWorldPos[i * 3 + 1]!, this.restWorldPos[i * 3 + 2]!];
  }
}

function isIdentity(m: ArrayLike<number>): boolean {
  for (let i = 0; i < 16; i++) if (Math.abs(m[i]! - (i % 5 === 0 ? 1 : 0)) > 1e-6) return false;
  return true;
}

/** A clip resampled for one rig: every bone, every frame. */
export interface BakedClip {
  name: string;
  /** Seconds. */
  duration: number;
  frames: number;
  loop: boolean;
  /** Local rotations, frames × bones × 4. */
  rot: Float32Array;
  /** Local translations, frames × bones × 3. */
  pos: Float32Array;
  /** Ground speed the clip's stride matches (m/s), for walk / run playback rate; 0 otherwise. */
  speed: number;
  /** Generated here rather than authored. */
  procedural: boolean;
  /** A once-clip that leads into another (sit down, then sit): played when this one ends. */
  next?: BakedClip;
}

/**
 * A clip the animator can play: an animation, `sit_floor`, the sit
 * emote with no seat near (the authored `floor_rest`: sitting on the floor),
 * or `floor_sit`, the sit emote on a floor seat (a common room's
 * rug or hearth step: the authored `floor_sit`, compact and cross-legged;
 * `floor_rest` until the art has it). The seated `sit` clip puts the sit
 * bones on the root, which goes on a seat's top (castle/seats.ts); on the
 * floor it would put the legs through it (docs/art/character-adoption.md).
 */
export type ClipKey = Anim | "sit_floor" | "floor_sit";

/** What an authored clip is for: a clip key, or the sit-down that leads into the sitting loop. */
export type ClipRole = ClipKey | "sit_intro";

/** Which animation a clip name stands for ("Armature|Walk_Loop" → walk), if any. */
export function animForClipName(name: string): ClipRole | null {
  const n = name.toLowerCase().replace(/^.*\|/, "");
  const tokens = n.split(/[^a-z]+/).filter(Boolean);
  // A wand-gesture clip (`gesture_<key>`, one per data/classes/gestures.json
  // entry) never stands for a built-in animation, whatever its key says
  // (`gesture_colour_wave` is not the wave emote).
  if (tokens[0] === "gesture") return null;
  if (tokens.length === 2 && tokens[0] === "floor" && tokens[1] === "sit") return "floor_sit";
  if (tokens.includes("floor") && (tokens.includes("rest") || tokens.includes("sit"))) return "sit_floor";
  if ((tokens.includes("sit") || tokens.includes("sitting")) && tokens.includes("down")) return "sit_intro";
  // The authored `broom_ride` (flying) and `broom_hover` (still in the air).
  if (tokens.includes("broom")) return tokens.includes("hover") ? "hover" : tokens.includes("ride") || tokens.includes("fly") ? "fly" : null;
  for (const a of ANIMS) if (tokens.includes(a)) return a;
  if (tokens.includes("sitting") || tokens.includes("seated")) return "sit";
  if (tokens.includes("waving")) return "wave";
  if (tokens.includes("bowing")) return "bow";
  if (tokens.includes("running") || tokens.includes("jog")) return "run";
  if (tokens.includes("walking")) return "walk";
  return null;
}

function sampleChannel(ch: ChannelData, t: number, width: number, out: Float32Array | number[], o: number): void {
  const times = ch.times;
  const n = times.length;
  if (n === 0) return;
  if (t <= times[0]! || n === 1) {
    for (let c = 0; c < width; c++) out[o + c] = ch.values[c]!;
    return;
  }
  if (t >= times[n - 1]!) {
    for (let c = 0; c < width; c++) out[o + c] = ch.values[(n - 1) * width + c]!;
    return;
  }
  let k = 0;
  while (k < n - 2 && times[k + 1]! <= t) k++;
  const t0 = times[k]!;
  const t1 = times[k + 1]!;
  const a = ch.interpolation === "STEP" || t1 <= t0 ? 0 : (t - t0) / (t1 - t0);
  if (width === 4) slerpInto(ch.values, k * 4, ch.values, (k + 1) * 4, a, out, o);
  else for (let c = 0; c < width; c++) out[o + c] = ch.values[k * width + c]! * (1 - a) + ch.values[(k + 1) * width + c]! * a;
}

/** Resamples an authored clip for `rig`; bones the clip does not animate keep their rest pose. */
export function bakeClip(rig: Rig, clip: ClipData, loop: boolean, speed = 0): BakedClip {
  // A looping clip's last key repeats its first; sampling wraps instead of holding it.
  const span = Math.round(clip.duration * CLIP_FPS);
  const frames = Math.max(1, loop && span >= 2 ? span : span + 1);
  const n = rig.count;
  const rot = new Float32Array(frames * n * 4);
  const pos = new Float32Array(frames * n * 3);
  const rest = rig.data.rest;
  for (let b = 0; b < n; b++) {
    const track = clip.tracks.get(rig.bones[b]!);
    for (let f = 0; f < frames; f++) {
      const t = Math.min(f / CLIP_FPS, clip.duration);
      const ro = (f * n + b) * 4;
      const po = (f * n + b) * 3;
      if (track?.rotation) sampleChannel(track.rotation, t, 4, rot, ro);
      else rot.set(rest.subarray(b * 10 + 3, b * 10 + 7), ro);
      if (track?.translation) sampleChannel(track.translation, t, 3, pos, po);
      else pos.set(rest.subarray(b * 10, b * 10 + 3), po);
    }
  }
  return { name: clip.name, duration: Math.max(clip.duration, 1 / CLIP_FPS), frames, loop, rot, pos, speed, procedural: false };
}

// ------------------------------------------------------------------ procedural clips

/** A model-space rotation of one bone about its own head. */
interface BoneTurn {
  bone: string;
  axis: [number, number, number];
  angle: number;
}

interface ProceduralFrame {
  turns: BoneTurn[];
  /** Model-space offset of the hips (m). */
  hips?: [number, number, number];
}

const X: [number, number, number] = [1, 0, 0];
const Y: [number, number, number] = [0, 1, 0];
const Z: [number, number, number] = [0, 0, 1];
const TAU = Math.PI * 2;

/**
 * Model-space conventions (docs/art/base-body-spec.md): the character faces
 * +Z with Y up, so its left (`.L`) is +X. Swinging a hanging limb forward is
 * a negative turn about X; leaning the upper body forward is a positive one.
 */
function walkFrame(p: number, amp: number, lean: number): ProceduralFrame {
  const s = Math.sin(p * TAU);
  const c = Math.cos(p * TAU);
  const kneeL = 0.12 + amp * 0.9 * Math.max(0, Math.sin(p * TAU + Math.PI * 0.55));
  const kneeR = 0.12 + amp * 0.9 * Math.max(0, Math.sin(p * TAU + Math.PI * 1.55));
  return {
    turns: [
      { bone: "DEF-thigh.L", axis: X, angle: -amp * s },
      { bone: "DEF-thigh.R", axis: X, angle: amp * s },
      { bone: "DEF-shin.L", axis: X, angle: kneeL },
      { bone: "DEF-shin.R", axis: X, angle: kneeR },
      { bone: "DEF-foot.L", axis: X, angle: -0.2 * amp * c },
      { bone: "DEF-foot.R", axis: X, angle: 0.2 * amp * c },
      { bone: "DEF-upper_arm.L", axis: X, angle: amp * 0.75 * s },
      { bone: "DEF-upper_arm.R", axis: X, angle: -amp * 0.75 * s },
      { bone: "DEF-forearm.L", axis: X, angle: -0.25 - lean * 2.2 },
      { bone: "DEF-forearm.R", axis: X, angle: -0.25 - lean * 2.2 },
      { bone: "DEF-spine.001", axis: X, angle: lean },
      { bone: "DEF-spine.003", axis: Y, angle: 0.1 * amp * s },
      { bone: "DEF-spine.006", axis: Y, angle: -0.06 * amp * s },
    ],
    hips: [0, -0.025 * amp * 2 * Math.abs(c), 0],
  };
}

function idleFrame(t: number): ProceduralFrame {
  const breath = Math.sin((t / 3.6) * TAU);
  const sway = Math.sin((t / 7.2) * TAU);
  return {
    turns: [
      { bone: "DEF-spine.003", axis: X, angle: -0.015 * breath },
      { bone: "DEF-spine.001", axis: Z, angle: 0.012 * sway },
      { bone: "DEF-spine.006", axis: Y, angle: 0.05 * sway },
      { bone: "DEF-upper_arm.L", axis: Z, angle: -0.04 + 0.01 * breath },
      { bone: "DEF-upper_arm.R", axis: Z, angle: 0.04 - 0.01 * breath },
      { bone: "DEF-forearm.L", axis: X, angle: -0.12 },
      { bone: "DEF-forearm.R", axis: X, angle: -0.12 },
    ],
  };
}

/** 0 → 1 over `rise` seconds, held, and back to 0 over the last `fall` seconds of `total`. */
function envelope(t: number, rise: number, fall: number, total: number): number {
  const smooth = (x: number) => x * x * (3 - 2 * x);
  if (t < rise) return smooth(t / rise);
  if (total > 0 && t > total - fall) return smooth(Math.max(0, (total - t) / fall));
  return 1;
}

function waveFrame(t: number): ProceduralFrame {
  const k = envelope(t, 0.35, 0, 0);
  const w = Math.sin(t * TAU * 1.6);
  return {
    turns: [
      ...idleFrame(t).turns.filter((x) => !x.bone.endsWith(".R")),
      { bone: "DEF-upper_arm.R", axis: Z, angle: -1.75 * k },
      { bone: "DEF-upper_arm.R", axis: X, angle: -0.35 * k },
      { bone: "DEF-forearm.R", axis: Z, angle: (-1.15 + 0.35 * w) * k },
      { bone: "DEF-hand.R", axis: Z, angle: 0.15 * w * k },
      { bone: "DEF-spine.006", axis: Z, angle: -0.06 * k },
    ],
  };
}

function bowFrame(t: number, total: number): ProceduralFrame {
  const k = envelope(t, 0.45, 0.5, total);
  return {
    turns: [
      { bone: "DEF-spine.001", axis: X, angle: 0.3 * k },
      { bone: "DEF-spine.003", axis: X, angle: 0.35 * k },
      { bone: "DEF-spine.006", axis: X, angle: 0.25 * k },
      { bone: "DEF-upper_arm.L", axis: X, angle: -0.2 * k },
      { bone: "DEF-upper_arm.R", axis: X, angle: -0.6 * k },
      { bone: "DEF-forearm.R", axis: Y, angle: 1.2 * k },
      { bone: "DEF-forearm.R", axis: X, angle: -1.1 * k },
    ],
    hips: [0, 0, -0.05 * k],
  };
}

function sitFrame(t: number, hipHeight: number): ProceduralFrame {
  const k = envelope(t, 0.8, 0, 0);
  return {
    turns: [
      { bone: "DEF-thigh.L", axis: X, angle: -1.45 * k },
      { bone: "DEF-thigh.R", axis: X, angle: -1.45 * k },
      { bone: "DEF-thigh.L", axis: Y, angle: 0.75 * k },
      { bone: "DEF-thigh.R", axis: Y, angle: -0.75 * k },
      { bone: "DEF-shin.L", axis: X, angle: 2.35 * k },
      { bone: "DEF-shin.R", axis: X, angle: 2.35 * k },
      { bone: "DEF-shin.L", axis: Y, angle: 0.5 * k },
      { bone: "DEF-shin.R", axis: Y, angle: -0.5 * k },
      { bone: "DEF-spine.001", axis: X, angle: 0.12 * k },
      { bone: "DEF-upper_arm.L", axis: X, angle: -0.55 * k },
      { bone: "DEF-upper_arm.R", axis: X, angle: -0.55 * k },
      { bone: "DEF-forearm.L", axis: X, angle: -0.5 * k },
      { bone: "DEF-forearm.R", axis: X, angle: -0.5 * k },
    ],
    hips: [0, -(hipHeight - 0.16) * k, -0.05 * k],
  };
}

/**
 * Placeholder where the art has no broom clips: seated astride the
 * handle (the hips stay where they stand: the broom goes under them), thighs
 * forward and a little apart, shins tucked back, both hands resting on the
 * handle in front of the lap. `lean` tips the body forward (riding), `bob`
 * is a slow rise and fall (hovering).
 */
function rideFrame(t: number, duration: number, lean: number, bob: number): ProceduralFrame {
  const s = Math.sin((t / duration) * TAU);
  return {
    turns: [
      { bone: "DEF-thigh.L", axis: X, angle: -1.25 },
      { bone: "DEF-thigh.R", axis: X, angle: -1.25 },
      { bone: "DEF-thigh.L", axis: Y, angle: 0.22 },
      { bone: "DEF-thigh.R", axis: Y, angle: -0.22 },
      { bone: "DEF-shin.L", axis: X, angle: 1.75 },
      { bone: "DEF-shin.R", axis: X, angle: 1.75 },
      { bone: "DEF-foot.L", axis: X, angle: 0.35 },
      { bone: "DEF-foot.R", axis: X, angle: 0.35 },
      { bone: "DEF-spine.001", axis: X, angle: lean + 0.015 * s },
      { bone: "DEF-spine.003", axis: X, angle: lean * 0.5 },
      // Hands forward and down to the handle at lap height.
      { bone: "DEF-upper_arm.L", axis: X, angle: -0.55 },
      { bone: "DEF-upper_arm.R", axis: X, angle: -0.55 },
      { bone: "DEF-upper_arm.L", axis: Z, angle: -0.12 },
      { bone: "DEF-upper_arm.R", axis: Z, angle: 0.12 },
      { bone: "DEF-forearm.L", axis: X, angle: -0.75 },
      { bone: "DEF-forearm.R", axis: X, angle: -0.75 },
    ],
    hips: [0, bob * s, 0],
  };
}

/** Lowers the arms from the rest A-pose (45 degrees) to hang about 10 degrees from the body. */
const ARMS_DOWN: readonly BoneTurn[] = [
  { bone: "DEF-upper_arm.L", axis: Z, angle: -0.62 },
  { bone: "DEF-upper_arm.R", axis: Z, angle: 0.62 },
];

/** Durations and reference speeds of the procedural clips. */
export const PROCEDURAL = {
  idle: { duration: 7.2 },
  walk: { duration: 0.8 },
  run: { duration: 0.6 },
  wave: { duration: 2.5 },
  bow: { duration: 2.0 },
  sit: { duration: 0.8 },
  // Protocol v5 `cast` (a wand gesture): a placeholder pose until the spell clips land.
  cast: { duration: 0.9 },
  // Protocol v8: placeholders until the broom clips are wired.
  fly: { duration: 1.2 },
  hover: { duration: 2.4 },
} as const;

/**
 * Generates a clip for `anim` on `rig`. `speeds` are the ground speeds the
 * walk and run strides are tuned to (sim-core's walk and run speeds).
 */
export function proceduralClip(rig: Rig, anim: Anim, speeds: { walk: number; run: number }): BakedClip {
  const duration = PROCEDURAL[anim].duration;
  // The procedural wave raises the arm once and waves until the emote ends, so it plays once.
  // The procedural sit eases down once and holds (there is no separate intro).
  const loop = anim === "sit" ? false : ANIM_LOOPS[anim];
  const frames = Math.round(duration * CLIP_FPS) + (loop ? 0 : 1);
  const n = rig.count;
  const rot = new Float32Array(frames * n * 4);
  const pos = new Float32Array(frames * n * 3);
  const rest = rig.data.rest;
  const hipsBone = ["DEF-spine", "hips", "root"].map((b) => rig.boneIndex(b)).find((i) => i >= 0) ?? -1;
  const thigh = rig.bonePos("DEF-thigh.L");
  const hipHeight = thigh ? thigh[1] : 0.9;

  for (let f = 0; f < frames; f++) {
    const t = f / CLIP_FPS;
    const p = t / duration;
    const frame =
      anim === "walk"
        ? walkFrame(p, 0.45, 0.03)
        : anim === "run"
          ? walkFrame(p, 0.8, 0.14)
          : anim === "wave"
            ? waveFrame(t)
            : anim === "bow"
              ? bowFrame(t, duration)
              : anim === "sit"
                ? sitFrame(t, hipHeight)
                : anim === "fly"
                  ? rideFrame(t, duration, 0.16, 0.01)
                  : anim === "hover"
                    ? rideFrame(t, duration, 0.04, 0.03)
                    : idleFrame(t);
    // The rig rests in an A-pose; every clip first lets the arms hang down.
    frame.turns.unshift(...ARMS_DOWN);
    // Start from rest, then apply each turn conjugated into the bone's frame.
    for (let b = 0; b < n; b++) {
      rot.set(rest.subarray(b * 10 + 3, b * 10 + 7), (f * n + b) * 4);
      pos.set(rest.subarray(b * 10, b * 10 + 3), (f * n + b) * 3);
    }
    for (const turn of frame.turns) {
      const b = rig.boneIndex(turn.bone);
      if (b < 0) continue;
      const w: Quat = [rig.restWorldRot[b * 4]!, rig.restWorldRot[b * 4 + 1]!, rig.restWorldRot[b * 4 + 2]!, rig.restWorldRot[b * 4 + 3]!];
      const delta = quatAxisAngle(turn.axis[0], turn.axis[1], turn.axis[2], turn.angle);
      const localDelta = quatMul(quatMul(quatConj(w), delta), w);
      const o = (f * n + b) * 4;
      const cur: Quat = [rot[o]!, rot[o + 1]!, rot[o + 2]!, rot[o + 3]!];
      rot.set(quatMul(cur, localDelta), o);
    }
    if (frame.hips && hipsBone >= 0) {
      // The offset is in model space; express it in the hips' parent frame.
      const parent = rig.parents[hipsBone]!;
      let off: [number, number, number] = frame.hips;
      if (parent >= 0) {
        const pq: Quat = [rig.restWorldRot[parent * 4]!, rig.restWorldRot[parent * 4 + 1]!, rig.restWorldRot[parent * 4 + 2]!, rig.restWorldRot[parent * 4 + 3]!];
        off = rotate(quatConj(pq), off);
      }
      const o = (f * n + hipsBone) * 3;
      pos[o] = pos[o]! + off[0];
      pos[o + 1] = pos[o + 1]! + off[1];
      pos[o + 2] = pos[o + 2]! + off[2];
    }
  }
  const speed = anim === "walk" ? speeds.walk : anim === "run" ? speeds.run : 0;
  return { name: `procedural_${anim}`, duration, frames, loop, rot, pos, speed, procedural: true };
}

function rotate(q: Quat, v: [number, number, number]): [number, number, number] {
  const r = quatMul(quatMul(q, [v[0], v[1], v[2], 0]), quatConj(q));
  return [r[0], r[1], r[2]];
}

/**
 * The clip set of a rig: authored clips where the art has them (matched by
 * name), procedural ones for the rest.
 */
export function clipSet(rig: Rig, authored: readonly ClipData[], speeds: { walk: number; run: number }): Map<ClipKey, BakedClip> {
  const out = new Map<ClipKey, BakedClip>();
  let intro: BakedClip | null = null;
  for (const clip of authored) {
    const role = animForClipName(clip.name);
    if (role === "sit_intro") {
      intro ??= bakeClip(rig, clip, false);
      continue;
    }
    if (!role || out.has(role)) continue;
    out.set(role, bakeClip(rig, clip, role === "sit_floor" || role === "floor_sit" ? true : ANIM_LOOPS[role], role === "walk" ? speeds.walk : role === "run" ? speeds.run : 0));
  }
  // Sitting: the sit-down plays once, then the sitting loop.
  const sit = out.get("sit");
  if (intro && sit) out.set("sit", { ...intro, next: sit });
  for (const anim of ANIMS) if (!out.has(anim)) out.set(anim, proceduralClip(rig, anim, speeds));
  // No floor-sit clip (older art, greybox): the sit clip, as before.
  if (!out.has("sit_floor")) out.set("sit_floor", out.get("sit")!);
  // No floor-seat clip yet (older art): the floor sit, also a pose on the floor.
  if (!out.has("floor_sit")) out.set("floor_sit", out.get("sit_floor")!);
  return out;
}

// ------------------------------------------------------------------ posing

const ROOT_SCRATCH = new Float32Array(16);
const SAMPLE_SCRATCH = new Float32Array(7);

export interface PoseLayer {
  clip: BakedClip;
  /** Seconds into the clip. */
  time: number;
  weight: number;
}

/**
 * Blends `layers` into local bone matrices (16 floats per bone, Babylon's
 * memory layout) in `out`. Rotations are blended by normalised weighted sum
 * (with hemisphere correction), translations linearly; scale stays at rest.
 */
export function samplePose(rig: Rig, layers: readonly PoseLayer[], out: Float32Array, scratch: Float32Array = SAMPLE_SCRATCH): void {
  const n = rig.count;
  const rest = rig.data.rest;
  let total = 0;
  // Indexed loops: an iterator per bone per character was most of a crowd frame's garbage.
  for (let i = 0; i < layers.length; i++) total += layers[i]!.weight;
  // Each layer's frame pair and blend, once per pose rather than once per bone
  // (and so no double crosses a call per bone: V8 boxes it when the call is not inlined).
  if (LAYER_FRAMES.length < layers.length * 3) LAYER_FRAMES = new Float64Array(layers.length * 6);
  for (let i = 0; i < layers.length; i++) layerFrames(layers[i]!, i);
  for (let b = 0; b < n; b++) {
    let qx = 0;
    let qy = 0;
    let qz = 0;
    let qw = 0;
    let tx = 0;
    let ty = 0;
    let tz = 0;
    if (total <= 1e-6) {
      tx = rest[b * 10]!;
      ty = rest[b * 10 + 1]!;
      tz = rest[b * 10 + 2]!;
      qx = rest[b * 10 + 3]!;
      qy = rest[b * 10 + 4]!;
      qz = rest[b * 10 + 5]!;
      qw = rest[b * 10 + 6]!;
    } else {
      for (let li = 0; li < layers.length; li++) {
        const l = layers[li]!;
        if (l.weight <= 0) continue;
        const w = l.weight / total;
        sampleBone(l.clip, li, b, n, scratch);
        const sx = scratch[0]!;
        const sy = scratch[1]!;
        const sz = scratch[2]!;
        const sw = scratch[3]!;
        const sign = qx * sx + qy * sy + qz * sz + qw * sw < 0 ? -w : w;
        qx += sx * sign;
        qy += sy * sign;
        qz += sz * sign;
        qw += sw * sign;
        tx += scratch[4]! * w;
        ty += scratch[5]! * w;
        tz += scratch[6]! * w;
      }
      // Not Math.hypot: V8 allocates for its arguments, and this runs per bone per character per frame.
      const len = Math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw) || 1;
      qx /= len;
      qy /= len;
      qz /= len;
      qw /= len;
    }
    composeTRS(tx, ty, tz, qx, qy, qz, qw, rest[b * 10 + 7]!, rest[b * 10 + 8]!, rest[b * 10 + 9]!, out, b * 16);
    if (rig.hasRootPre[b]) {
      const local = ROOT_SCRATCH;
      for (let k = 0; k < 16; k++) local[k] = out[b * 16 + k]!;
      mul4(rig.data.rootPre, local, out, b * 16, 0, b * 16);
    }
  }
}

/** Frame pairs and blends of the layers being sampled (3 per layer: f0, f1, blend). */
let LAYER_FRAMES = new Float64Array(24);

/** Layer `i`'s frame pair and blend at its time into `LAYER_FRAMES`. */
function layerFrames(l: PoseLayer, i: number): void {
  const clip = l.clip;
  const last = clip.frames - 1;
  let f = l.time * CLIP_FPS;
  let f0: number;
  let f1: number;
  if (clip.loop) {
    const span = clip.frames; // a loop wraps from the last frame back to the first
    f = ((f % span) + span) % span;
    f0 = Math.floor(f);
    f1 = (f0 + 1) % span;
  } else {
    f = Math.max(0, Math.min(f, last));
    f0 = Math.floor(f);
    f1 = Math.min(f0 + 1, last);
  }
  LAYER_FRAMES[i * 3] = f0;
  LAYER_FRAMES[i * 3 + 1] = f1;
  LAYER_FRAMES[i * 3 + 2] = f - f0;
}

/** One bone of a clip at layer `li`'s frames (`layerFrames`): [qx, qy, qz, qw, tx, ty, tz] into `out`. */
function sampleBone(clip: BakedClip, li: number, b: number, n: number, out: Float32Array): void {
  const f0 = LAYER_FRAMES[li * 3]!;
  const f1 = LAYER_FRAMES[li * 3 + 1]!;
  const a = LAYER_FRAMES[li * 3 + 2]!;
  const r0 = (f0 * n + b) * 4;
  const r1 = (f1 * n + b) * 4;
  // nlerp is enough at 30 fps.
  let x1 = clip.rot[r1]!;
  let y1 = clip.rot[r1 + 1]!;
  let z1 = clip.rot[r1 + 2]!;
  let w1 = clip.rot[r1 + 3]!;
  const x0 = clip.rot[r0]!;
  const y0 = clip.rot[r0 + 1]!;
  const z0 = clip.rot[r0 + 2]!;
  const w0 = clip.rot[r0 + 3]!;
  if (x0 * x1 + y0 * y1 + z0 * z1 + w0 * w1 < 0) {
    x1 = -x1;
    y1 = -y1;
    z1 = -z1;
    w1 = -w1;
  }
  out[0] = x0 + (x1 - x0) * a;
  out[1] = y0 + (y1 - y0) * a;
  out[2] = z0 + (z1 - z0) * a;
  out[3] = w0 + (w1 - w0) * a;
  const p0 = (f0 * n + b) * 3;
  const p1 = (f1 * n + b) * 3;
  out[4] = clip.pos[p0]! + (clip.pos[p1]! - clip.pos[p0]!) * a;
  out[5] = clip.pos[p0 + 1]! + (clip.pos[p1 + 1]! - clip.pos[p0 + 1]!) * a;
  out[6] = clip.pos[p0 + 2]! + (clip.pos[p1 + 2]! - clip.pos[p0 + 2]!) * a;
}
