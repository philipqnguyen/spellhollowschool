// Per-character animation state: which clips play, at what time and weight.
// The entity's `anim` (from snapshots, or the local player's own state)
// picks the target clip; switching crossfades, walk and run keep their foot
// phase when blending into each other, and their playback rate follows the
// character's ground speed so feet don't slide. Pure, unit tested.

import type { BakedClip, ClipKey, PoseLayer } from "./clips";

/** Crossfade durations (s). */
export const FADE_DEFAULT = 0.2;
const FADE_SIT = 0.4;
const FADE_EMOTE = 0.25;
/** Walk / run playback rate is held within these bounds. */
const RATE_MIN = 0.55;
const RATE_MAX = 1.8;

const isLocomotion = (a: ClipKey) => a === "walk" || a === "run";

export function fadeTime(from: ClipKey, to: ClipKey): number {
  if (from === "sit" || to === "sit" || from === "sit_floor" || to === "sit_floor" || from === "floor_sit" || to === "floor_sit") return FADE_SIT;
  if (from === "wave" || from === "bow" || to === "wave" || to === "bow") return FADE_EMOTE;
  return FADE_DEFAULT;
}

/**
 * Playback rate for a clip at ground speed `speed` (m/s). Locomotion clips
 * scale with speed; everything else plays at 1.
 */
export function playbackRate(clip: BakedClip, speed: number): number {
  if (clip.speed <= 0 || speed <= 0) return 1;
  return Math.max(RATE_MIN, Math.min(RATE_MAX, speed / clip.speed));
}

interface Layer extends PoseLayer {
  anim: ClipKey;
}

export class Animator {
  private readonly active: Layer[] = [];
  private target: ClipKey | null = null;
  private fade = FADE_DEFAULT;
  private speed = 0;

  /**
   * @param clips the rig's clip set (every animation present)
   * @param phase 0..1, offsets the first clip so a crowd's idles aren't in lockstep
   */
  constructor(
    private clips: ReadonlyMap<ClipKey, BakedClip>,
    private readonly phase = 0,
  ) {}

  /** Swaps in another rig's clips (the character changed body type). */
  setClips(clips: ReadonlyMap<ClipKey, BakedClip>): void {
    this.clips = clips;
    for (const l of this.active) l.clip = clips.get(l.anim) ?? l.clip;
  }

  get current(): ClipKey | null {
    return this.target;
  }

  get layers(): readonly PoseLayer[] {
    return this.active;
  }

  /** Sets the animation to show and the character's ground speed (m/s). */
  set(anim: ClipKey, speed: number): void {
    this.speed = speed;
    if (anim === this.target) return;
    const clip = this.clips.get(anim);
    if (!clip) return;
    const from = this.target;
    this.target = anim;
    if (from === null) {
      // First pose: no fade in from the rest pose. Loops start at the crowd phase; once-clips (an emote) from their start.
      this.active.push({ anim, clip, time: clip.loop ? this.phase * clip.duration : 0, weight: 1 });
      return;
    }
    this.fade = fadeTime(from, anim);
    let layer = this.active.find((l) => l.anim === anim);
    if (!layer) {
      let time = 0;
      // Walk <-> run: start at the same point of the stride.
      const prev = this.active.find((l) => l.anim === from);
      if (prev && isLocomotion(from) && isLocomotion(anim)) time = (prev.time / prev.clip.duration) * clip.duration;
      else if (clip.loop && !isLocomotion(anim)) time = this.phase * clip.duration;
      layer = { anim, clip, time, weight: 0 };
      this.active.push(layer);
    } else if (!clip.loop || layer.clip !== clip) {
      // A once-clip coming back restarts (a sit that had moved on to its loop sits down again).
      layer.clip = clip;
      layer.time = 0;
    }
  }

  /**
   * The garments' sitting drape (assemble.ts `DRAPE_KEYS`) for the current
   * layers, into `out` (chair, floor, rest): the chair key with the seated
   * clip, coming on through the sit-down as the body lowers onto the seat;
   * the floor key with the cross-legged floor sit, the rest key with the
   * floor rest (`sit_floor`). Each follows its clip's crossfade.
   */
  drapeWeights(out: Float32Array): void {
    let chair = 0;
    let floor = 0;
    let rest = 0;
    for (const l of this.active) {
      if (l.anim === "sit") {
        const t = l.clip.next ? Math.min(1, Math.max(0, l.time / l.clip.duration)) : 1;
        chair += l.weight * t * t * (3 - 2 * t);
      } else if (l.anim === "floor_sit") floor += l.weight;
      else if (l.anim === "sit_floor") rest += l.weight;
    }
    out[0] = Math.min(1, chair);
    out[1] = Math.min(1, floor);
    out[2] = Math.min(1, rest);
  }

  /** Advances time and the crossfade by `dt` seconds. */
  update(dt: number): void {
    if (dt <= 0) return;
    const step = dt / this.fade;
    for (let i = this.active.length - 1; i >= 0; i--) {
      const l = this.active[i]!;
      const on = l.anim === this.target;
      l.weight = on ? Math.min(1, l.weight + step) : l.weight - step;
      if (l.weight <= 0) {
        this.active.splice(i, 1);
        continue;
      }
      const t = l.time + dt * playbackRate(l.clip, this.speed);
      if (!l.clip.loop && t >= l.clip.duration && l.clip.next) {
        // The intro is done: carry on into the clip it leads to.
        l.time = (t - l.clip.duration) % l.clip.next.duration;
        l.clip = l.clip.next;
      } else {
        l.time = l.clip.loop ? t % l.clip.duration : Math.min(t, l.clip.duration);
      }
    }
  }
}
