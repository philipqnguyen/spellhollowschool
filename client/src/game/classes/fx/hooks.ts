// Per-class effect hooks: a small 3D reaction when a pick is
// right, one module per class (fx/<class>.ts), so later classes add a file
// instead of touching the controller. Each hook gets what it may draw with:
// a burst of the spell VFX (one draw shared with every other spell effect)
// at the student's desk, and the pick tray.
//
// Adds drills (`ClassDrill`): trace classes whose beats play out
// in the room, drawn every frame with the duel's spell VFX (Defence: the
// professor's telegraphed casts and the student's shield and spells) and
// told of each trace the server scored or the student let pass. A trace
// class may also react to a passed trace with a burst (`ClassTraced`).

import type { GestureResult } from "../../../wasm/sim_wasm";
import type { BeatState } from "../lesson";
import type { PickItem } from "../picks";

/** What a class's hook may do. */
export interface ClassFxContext {
  /** Where the student's desk or cauldron is: just in front of them at hand height. */
  desk: () => [number, number, number] | null;
  /** A burst of `count` glints in colour `rgb` (0..1) at (x, y, z), `radius` metres across. */
  burst: (x: number, y: number, z: number, rgb: readonly [number, number, number], count: number, radius: number) => void;
  /** The pick tray's card shows the change made (`text`: what it became). */
  swap: (text: string) => void;
}

/** A right pick: the beat's prompt and the option chosen. */
export type ClassHit = (ctx: ClassFxContext, prompt: PickItem, chosen: PickItem) => void;

/** A trace the server passed: the gesture's key. */
export type ClassTraced = (ctx: ClassFxContext, gesture: string) => void;

export type Rgb = readonly [number, number, number];

/** Where someone stands this frame (feet, metres; yaw in radians, facing +z at 0). */
export interface FxPose {
  x: number;
  y: number;
  z: number;
  yaw: number;
}

/** The duel's spell VFX as a drill draws with it (game/vfx/spell-vfx.ts; one draw call). */
export interface DrillVfx {
  bolt(x: number, y: number, z: number, dx: number, dy: number, dz: number, rgb: Rgb, tail?: number, fade?: number, seed?: number): void;
  charge(x: number, y: number, z: number, rgb: Rgb, progress: number, seed?: number): void;
  shield(x: number, y: number, z: number, rgb: Rgb, strength: number, perfect: boolean, size?: number, seed?: number): void;
  flash(x: number, y: number, z: number, rgb: Rgb, scale?: number, sparks?: number): void;
  muzzle(x: number, y: number, z: number, rgb: Rgb): void;
  fizzle(x: number, y: number, z: number, rgb: Rgb): void;
}

/** What a drill draws with: the two people in it and the VFX. */
export interface DrillContext {
  self: () => FxPose | null;
  professor: () => FxPose | null;
  vfx: DrillVfx;
}

/** The round a drill draws: its window and beats. */
export interface DrillRound {
  windowMs: number;
  beats: readonly BeatState[];
}

/** A trace class's drill: its continuous effects and its reactions. Times are the server clock (ms). */
export interface ClassDrill {
  /** Every frame while the lesson runs: the round being practised (null between rounds). */
  frame(ctx: DrillContext, round: DrillRound | null, now: number): void;
  /** The server scored a trace beat (`result.score > 0` and not rejected: it passed). */
  scored(ctx: DrillContext, beat: BeatState, result: GestureResult, now: number): void;
  /** A beat's window passed with no trace. */
  missed(ctx: DrillContext, beat: BeatState, now: number): void;
  /** The lesson ended or the player left: drop whatever is in flight. */
  clear(): void;
}

// stages. A pick class whose room has animated props (Herbology's
// plants on the bench, the paddock's creatures) keeps a stage: which prop
// the beat's prompt names (the prompt key's prefix), how each prop moves this
// frame (node transforms only: lean, turn, lift, squash) and which need icon
// shows over a creature. The stage is pure (fx/stage.ts); game/classes/props.ts
// loads the props, applies the poses and draws the need icons.

/** A prop's transform this frame, relative to its rest at the layout landmark. */
export interface PropPose {
  /** Lean toward the students (+z), radians about the prop's base. */
  tilt: number;
  /** Turn about the vertical, radians. */
  yaw: number;
  /** Raised off its spot (m). */
  lift: number;
  /** Height and width scale (1: as built). */
  sy: number;
  sxz: number;
}

/** A need shown over a prop: the beat's prompt (its icon and words) and how far it has faded in (0..1). */
export interface PropNeed {
  prop: string;
  prompt: PickItem;
  strength: number;
}

/** A pick class's props, driven by its beats. Times are the server clock (ms). */
export interface ClassStage {
  /** The prop ids this class animates (layout landmarks and `instances/props/<id>.glb`). */
  readonly props: readonly string[];
  /** Every frame while the lesson runs: the round being practised (null between rounds). */
  frame(round: DrillRound | null, now: number): void;
  /** The server scored a pick beat: right or not. */
  scored(beat: BeatState, right: boolean, now: number): void;
  /** A pick beat's window passed with no answer. */
  missed(beat: BeatState, now: number): void;
  /** Where prop `prop` is this frame. */
  pose(prop: string, now: number): PropPose;
  /** The needs shown over props now (the creatures'). */
  needs(now: number): readonly PropNeed[];
  /** The lesson ended or the player left. */
  clear(): void;
}
