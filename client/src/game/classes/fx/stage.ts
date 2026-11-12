// The props stage: a pick class's animated props, driven by its
// beats. The prompt key's prefix names the prop (`vine_lunge` the plant
// `plant_vine`, `puff_hungry` the creature `beast_puff`; docs/architecture.md).
// Each prop has a motion: how it idles, how
// it reacts while a beat names it (the cue: a plant lunges, a creature
// fidgets and shows its need), and how it answers the student's pick (right:
// a plant recoils, a creature settles; wrong or missed: a plant snaps, a
// creature startles). Everything is a pure function of the server clock, so
// every student in the section sees the same cue on the same beat.
//
// Unit tested in fx.test.ts.

import type { BeatState } from "../lesson";
import type { PickItem } from "../picks";
import type { ClassStage, DrillRound, PropNeed, PropPose } from "./hooks";

/** A beat's cue starts this long before the beat (ms): as the pick tray's approach ring starts closing. */
export const CUE_LEAD_MS = 1600;
/** The cue is full this long before the beat (ms). */
export const CUE_FULL_MS = 250;
/** A reaction to a pick lasts this long (ms). */
export const REACT_MS = 800;

export type Reaction = "right" | "wrong";

/** How a kind of prop moves. Times in seconds; `cue` and `from` 0..1; `p` a reaction's progress 0..1. */
export interface Motion {
  /** At rest (`seed` gives each prop its own phase). */
  rest(t: number, seed: number, out: PropPose): void;
  /** While a beat names it, at strength `cue`. */
  cue(t: number, cue: number, out: PropPose): void;
  /** Its answer to a pick, `p` of the way through, from a cue that stood at `from`. */
  react(kind: Reaction, p: number, from: number, t: number, out: PropPose): void;
  /** Whether the beat's prompt (its need icon) shows over it while cued. */
  showsNeed: boolean;
}

/** One prop: its id (layout landmark and glb), the prompt prefix that names it, and how it moves. */
export interface PropSpec {
  id: string;
  prefix: string;
  motion: Motion;
}

/** The pose of a prop standing still. */
export function stillPose(): PropPose {
  return { tilt: 0, yaw: 0, lift: 0, sy: 1, sxz: 1 };
}

/** The prop a prompt key names, by its prefix (`vine_lunge` → the `vine_` prop). */
export function propFor(specs: readonly PropSpec[], promptKey: string): PropSpec | undefined {
  return specs.find((s) => promptKey.startsWith(s.prefix));
}

/** How strongly a beat at `atMs` cues its prop at `now` (0..1); the window's end (`atMs + windowMs`) ends it. */
export function cueStrength(atMs: number, windowMs: number, now: number): number {
  if (now > atMs + windowMs) return 0;
  const lead = atMs - now;
  if (lead >= CUE_LEAD_MS) return 0;
  if (lead <= CUE_FULL_MS) return 1;
  const x = (CUE_LEAD_MS - lead) / (CUE_LEAD_MS - CUE_FULL_MS);
  return x * x * (3 - 2 * x);
}

interface Cue {
  beat: BeatState;
  prompt: PickItem;
  strength: number;
}

interface Playing {
  kind: Reaction;
  at: number;
  from: number;
}

/** A class's props on stage (one per lesson followed). */
export class PropStage implements ClassStage {
  readonly props: readonly string[];
  private readonly cues = new Map<string, Cue>();
  private readonly playing = new Map<string, Playing>();
  /** The beats already answered on stage (their reaction played). */
  private readonly answered = new Set<BeatState>();
  private readonly seeds: Map<string, number>;

  constructor(
    private readonly specs: readonly PropSpec[],
    /** The class's prompt vocabulary (a beat's `prompt` indexes it). */
    private readonly prompts: readonly PickItem[],
  ) {
    this.props = specs.map((s) => s.id);
    this.seeds = new Map(specs.map((s, i) => [s.id, i * 1.7 + 0.4]));
  }

  frame(round: DrillRound | null, now: number): void {
    this.cues.clear();
    if (!round) return;
    for (const b of round.beats) {
      if (b.kind !== "pick" || this.answered.has(b) || b.status === "missed" || b.status === "scored") continue;
      const strength = cueStrength(b.atMs, round.windowMs, now);
      if (strength <= 0) continue;
      const prompt = this.prompts[b.prompt];
      const spec = prompt ? propFor(this.specs, prompt.key) : undefined;
      // The earliest beat naming a prop holds it.
      if (!prompt || !spec || this.cues.has(spec.id)) continue;
      this.cues.set(spec.id, { beat: b, prompt, strength });
    }
  }

  scored(beat: BeatState, right: boolean, now: number): void {
    this.react(beat, right ? "right" : "wrong", now);
  }

  missed(beat: BeatState, now: number): void {
    this.react(beat, "wrong", now);
  }

  private react(beat: BeatState, kind: Reaction, now: number): void {
    if (beat.kind !== "pick" || this.answered.has(beat)) return;
    this.answered.add(beat);
    const prompt = this.prompts[beat.prompt];
    const spec = prompt ? propFor(this.specs, prompt.key) : undefined;
    if (!spec) return;
    const cue = this.cues.get(spec.id);
    const from = cue && cue.beat === beat ? cue.strength : 1;
    if (cue?.beat === beat) this.cues.delete(spec.id);
    this.playing.set(spec.id, { kind, at: now, from });
  }

  pose(prop: string, now: number): PropPose {
    const out = stillPose();
    const spec = this.specs.find((s) => s.id === prop);
    if (!spec) return out;
    const t = now / 1000;
    spec.motion.rest(t, this.seeds.get(prop) ?? 0, out);
    const play = this.playing.get(prop);
    const cue = this.cues.get(prop);
    if (play) {
      const p = (now - play.at) / REACT_MS;
      if (p >= 1 || p < 0) this.playing.delete(prop);
      else {
        spec.motion.react(play.kind, p, play.from, t, out);
        return out; // the reaction plays out before the next cue takes over
      }
    }
    if (cue) spec.motion.cue(t, cue.strength, out);
    return out;
  }

  needs(_now: number): readonly PropNeed[] {
    const out: PropNeed[] = [];
    for (const [prop, cue] of this.cues) {
      const spec = this.specs.find((s) => s.id === prop);
      if (spec?.motion.showsNeed) out.push({ prop, prompt: cue.prompt, strength: cue.strength });
    }
    return out;
  }

  /** The reaction a prop is playing (tests and the F3 overlay), if any. */
  reaction(prop: string): Reaction | null {
    return this.playing.get(prop)?.kind ?? null;
  }

  clear(): void {
    this.cues.clear();
    this.playing.clear();
    this.answered.clear();
  }
}

/** 0 → 1 → 0 over p in 0..1. */
export function hump(p: number): number {
  return Math.sin(Math.PI * Math.max(0, Math.min(1, p)));
}
