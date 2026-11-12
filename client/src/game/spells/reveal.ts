// Aperio (spells to level 20, protocol v11): `Revealed { spell, durationMs,
// spots }` comes to its caster alone; each spot (a hidden passage's door, a
// find spot) shows a soft mint column of light and a ring of glints on the
// ground for `durationMs`, fading in and out. A second, dimmer copy of every
// column is drawn through walls (an x-ray sprite layer), so a passage behind
// a wall still reads. A glint sound per spot (the first few, staggered).
//
// Two sprite layers (four draws while anything shows, none otherwise).
// `revealAlpha` is pure and unit tested (reveal.test.ts).

import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import { audio } from "../../audio";
import type { RevealKind } from "../../wasm/sim_wasm";
import { cameraAxes, SpriteLayer } from "../effects/billboards";

/** Fading in and out (ms). */
export const REVEAL_FADE_IN_MS = 450;
export const REVEAL_FADE_OUT_MS = 1500;
/** At most this many glint sounds for one cast. */
export const REVEAL_SOUNDS_MAX = 5;
/** Through a wall, a column shows at this share of its brightness. */
const XRAY_ALPHA = 0.6;
/** protocol::REVEAL_SPOTS_MAX. */
const SPOTS_MAX = 32;
const MINT: [number, number, number] = [0.65, 1, 0.81];
const GLINTS = 10;

export interface RevealedSpot {
  kind: RevealKind;
  x: number;
  y: number;
  z: number;
}

/** How bright a spot shows `ageMs` into a reveal of `durationMs`: fades in, holds, fades out by the end; 0 outside. Pure. */
export function revealAlpha(ageMs: number, durationMs: number): number {
  if (!(durationMs > 0) || !(ageMs >= 0) || ageMs >= durationMs) return 0;
  const fadeIn = Math.min(REVEAL_FADE_IN_MS, durationMs / 3);
  const fadeOut = Math.min(REVEAL_FADE_OUT_MS, durationMs / 3);
  if (ageMs < fadeIn) {
    const t = ageMs / fadeIn;
    return t * t * (3 - 2 * t);
  }
  const left = durationMs - ageMs;
  if (left < fadeOut) {
    const t = left / fadeOut;
    return t * t * (3 - 2 * t);
  }
  return 1;
}

export class RevealMarks {
  private spots: RevealedSpot[] = [];
  private start = 0;
  private duration = 0;
  private readonly glow: SpriteLayer;
  private readonly glint: SpriteLayer;
  private readonly xray: SpriteLayer;
  private readonly right = new Vector3();
  private readonly up = new Vector3();

  constructor(scene: Scene) {
    // A column is a tall soft glow; its x-ray twin ignores depth.
    this.glow = new SpriteLayer(scene, "reveal-column", { shape: "glow", blend: "add", capacity: SPOTS_MAX * 2, stretch: [0.45, 2.6] });
    this.xray = new SpriteLayer(scene, "reveal-xray", { shape: "glow", blend: "add", capacity: SPOTS_MAX, stretch: [0.45, 2.6], xray: true });
    this.glint = new SpriteLayer(scene, "reveal-glint", { shape: "star", blend: "add", capacity: SPOTS_MAX * (GLINTS + 3) });
  }

  /** `Revealed`: these spots shine for `durationMs` from local time `now` (they replace any shown). */
  show(spots: readonly RevealedSpot[], durationMs: number, now: number, from: readonly [number, number, number] | null): void {
    this.spots = spots.slice(0, SPOTS_MAX).map((s) => ({ ...s }));
    this.start = now;
    this.duration = Math.max(0, durationMs);
    // Nearest first (the server's order): a glint each for the first few, as the sweep reaches them.
    this.spots.slice(0, REVEAL_SOUNDS_MAX).forEach((s, i) => {
      const d = from ? Math.hypot(s.x - from[0], s.z - from[2]) : 0;
      audio.playAt("spell.reveal_glint", [s.x, s.y + 1, s.z], { delayS: Math.min(1.2, 0.15 + d / 20 + i * 0.05) });
    });
  }

  get count(): number {
    return this.spots.length;
  }

  clear(): void {
    this.spots = [];
  }

  /** Per frame. */
  update(now: number, camera: Camera): void {
    this.glow.begin();
    this.glint.begin();
    this.xray.begin();
    const age = now - this.start;
    const a = this.spots.length ? revealAlpha(age, this.duration) : 0;
    if (a <= 0 && this.spots.length && age >= this.duration) this.spots = [];
    if (a > 0) {
      cameraAxes(camera, this.right, this.up);
      const t = age / 1000;
      for (const s of this.spots) {
        // A passage's column is taller (a doorway); a find spot's lower and rounder.
        const h = s.kind === "passage" ? 1.15 : 0.7;
        const size = s.kind === "passage" ? 1.2 : 0.85;
        const pulse = 0.85 + 0.15 * Math.sin(t * 2.4 + s.x);
        this.glow.push(s.x, s.y + h, s.z, size, MINT[0], MINT[1], MINT[2], 0.7 * a * pulse);
        this.glow.push(s.x, s.y + 0.15, s.z, size * 0.9, MINT[0], MINT[1], MINT[2], 0.35 * a);
        this.xray.push(s.x, s.y + h, s.z, size, MINT[0], MINT[1], MINT[2], XRAY_ALPHA * a * pulse);
        // A ring of glints turning on the ground, and motes rising up the column.
        for (let k = 0; k < GLINTS; k++) {
          const ang = (k / GLINTS) * Math.PI * 2 + t * 0.8;
          const tw = 0.6 + 0.4 * Math.sin(t * 4 + k * 1.7);
          this.glint.push(s.x + Math.cos(ang) * 0.6, s.y + 0.05, s.z + Math.sin(ang) * 0.6, 0.12, MINT[0], MINT[1], MINT[2], a * tw);
        }
        for (let k = 0; k < 3; k++) {
          const climb = (t * 0.45 + k / 3) % 1;
          const ang = k * 2.1 + t;
          this.glint.push(s.x + Math.cos(ang) * 0.18, s.y + climb * h * 2, s.z + Math.sin(ang) * 0.18, 0.09, 1, 1, 1, a * Math.sin(Math.PI * climb));
        }
      }
    }
    this.glow.end(this.right, this.up);
    this.glint.end(this.right, this.up);
    this.xray.end(this.right, this.up);
  }

  dispose(): void {
    this.glow.dispose();
    this.glint.dispose();
    this.xray.dispose();
  }
}
