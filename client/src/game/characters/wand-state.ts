// The wand in a character's right hand (docs/architecture.md "Spells and
// dueling"): when it is out and where it is drawn. Pure, unit tested
// (wand-state.test.ts); characters/wands.ts draws it, renderer.ts poses it.
//
// The wand is not an item: nobody equips it. It grows into the hand when a
// cast starts (the `cast` anim: our own predicted cast, the server's for
// everyone else, a duelist's telegraph) and stays out while something needs
// it: a lit wand (Lucerna), tracing a gesture in class, aiming Elevara. It
// shrinks away when none of that holds, and is never drawn on a broom.
//
// Where: the asset build gives the wand's transform under the body's
// `socket_hand.R` joint (tools/blender/vfx/build_vfx.py `wand_attach`),
// so its tip lands where the character clips solve it; the spell effects
// start at that drawn tip (renderer.ts `wandTip`). A greybox rig has no
// socket: the wand then runs along `DEF-hand.R` with its tip where the
// effects started before the wand was drawn (`FALLBACK_TIP`).

import type { Anim } from "../../wasm/sim_wasm";
import { composeTRS, mul4 } from "./math";

/** How long the wand takes to grow into the hand, or to shrink away (s). */
export const WAND_GROW_S = 0.1;
/** The bone the built wand hangs under, and the bone of the fallback. */
export const WAND_SOCKET = "socket_hand.R";
export const WAND_HAND = "DEF-hand.R";
/** Without a socket: the tip this far along the hand bone (m, at scale 1). */
export const FALLBACK_TIP = 0.24;
/** The stand-in wand's grip-to-tip and grip-to-butt lengths (m), as the built wand's. */
export const STAND_IN_TIP = 0.26;
export const STAND_IN_BUTT = 0.09;

/** On a broom (no wand is drawn, whatever else holds). */
export function riding(anim: Anim): boolean {
  return anim === "fly" || anim === "hover";
}

/**
 * Whether a character holds its wand out apart from a cast: its wand is
 * lit (Lucerna), it traces a gesture in class, or it aims Elevara.
 */
export function holdsWand(lit: boolean, tracing: boolean, aiming: boolean): boolean {
  return lit || tracing || aiming;
}

/** Whether the wand should be out: casting, or held (`holdsWand`); never on a broom. */
export function wandWanted(anim: Anim, held: boolean): boolean {
  if (riding(anim)) return false;
  return anim === "cast" || held;
}

/**
 * The wand's growth (0 away .. 1 fully in the hand) after `dt` seconds,
 * from `t`: toward 1 while wanted, toward 0 otherwise, over `WAND_GROW_S`.
 * On a broom it is gone at once.
 */
export function stepWand(t: number, anim: Anim, held: boolean, dt: number): number {
  if (riding(anim)) return 0;
  const step = dt / WAND_GROW_S;
  return wandWanted(anim, held) ? Math.min(1, t + step) : Math.max(0, t - step);
}

/** How long the arm takes to raise a held-out wand, or to let it down (s). */
export const WAND_RAISE_S = 0.3;

/**
 * Whether the arm holds the wand up in front (wand-hold.ts, the lantern
 * pose): held out without a cast (a lit wand, an aim) while standing,
 * walking or running. A cast, a sit, an emote or a broom keep their own arm.
 */
export function raiseWanted(anim: Anim, held: boolean): boolean {
  return held && (anim === "idle" || anim === "walk" || anim === "run");
}

/** The raise (0 down .. 1 held up) after `dt` seconds, from `t`, over `WAND_RAISE_S`. */
export function stepRaise(t: number, anim: Anim, held: boolean, dt: number): number {
  const step = dt / WAND_RAISE_S;
  return raiseWanted(anim, held) ? Math.min(1, t + step) : Math.max(0, t - step);
}

/** The drawn size for growth `t` (eased in and out). */
export function wandSize(t: number): number {
  const c = Math.max(0, Math.min(1, t));
  return c * c * (3 - 2 * c);
}

/** The wand's transform under its bone, as the asset manifest gives it (`vfx/manifest.json` `wand.attach.<body>`). */
export interface WandAttach {
  bone: string;
  /** Quaternion [x, y, z, w]. */
  rotation: readonly number[];
  translation: readonly number[];
}

/** How the wand hangs on one rig: the bone's index (-1: none), the wand's matrix under it, and the grip-to-tip length (m). */
export interface WandFit {
  bone: number;
  matrix: Float32Array;
  tip: number;
}

/** The matrix of `a` (column-major, Babylon's memory layout). */
export function attachMatrix(a: WandAttach, out: Float32Array = new Float32Array(16)): Float32Array {
  const [qx = 0, qy = 0, qz = 0, qw = 1] = a.rotation;
  const [tx = 0, ty = 0, tz = 0] = a.translation;
  composeTRS(tx, ty, tz, qx, qy, qz, qw, 1, 1, 1, out);
  return out;
}

/** The fallback's matrix: the wand along the hand bone, its tip `FALLBACK_TIP` along it. */
export function fallbackMatrix(tip: number, out: Float32Array = new Float32Array(16)): Float32Array {
  composeTRS(0, FALLBACK_TIP - tip, 0, 0, 0, 0, 1, 1, 1, 1, out);
  return out;
}

/** Where a character is drawn: feet (the root), heading and uniform scale. */
export interface WandRoot {
  x: number;
  y: number;
  z: number;
  yaw: number;
  scale: number;
}

const ROOT = new Float32Array(16);
const A = new Float32Array(16);
const B = new Float32Array(16);
const W = new Float32Array(16);

/**
 * The wand's world matrix, `root · bone · attach · size`, into `out` at `o`:
 * `bone` is the hand bone's model matrix as posed (look.ts `modelMatrix`),
 * `attach` the wand under it, `size` its growth (wandSize). Allocation-free.
 */
export function wandMatrix(root: WandRoot, bone: Float32Array, attach: Float32Array, size: number, out: Float32Array, o = 0): void {
  const h = root.yaw / 2;
  composeTRS(root.x, root.y, root.z, 0, Math.sin(h), 0, Math.cos(h), root.scale, root.scale, root.scale, ROOT);
  mul4(ROOT, bone, A);
  mul4(A, attach, B);
  for (let c = 0; c < 3; c++) for (let r = 0; r < 3; r++) out[o + c * 4 + r] = B[c * 4 + r]! * size;
  out[o + 3] = out[o + 7] = out[o + 11] = 0;
  out[o + 12] = B[12]!;
  out[o + 13] = B[13]!;
  out[o + 14] = B[14]!;
  out[o + 15] = 1;
}

/** The wand tip in the world (`tip` m along the wand's +Y) for the full-size wand of `wandMatrix`, into `out`. */
export function wandTipOf(root: WandRoot, bone: Float32Array, attach: Float32Array, tip: number, out: { x: number; y: number; z: number }): void {
  wandMatrix(root, bone, attach, 1, W, 0);
  out.x = W[12]! + W[4]! * tip;
  out.y = W[13]! + W[5]! * tip;
  out.z = W[14]! + W[6]! * tip;
}
