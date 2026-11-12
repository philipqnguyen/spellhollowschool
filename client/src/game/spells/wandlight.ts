// Lit wands (Lucerna, `VisibleEffect::Lucerna`): every lit wand glows (a soft
// sprite and a glint at the wand hand), and the nearest few also cast real
// light on the world around them (docs/architecture.md, "Spells and dueling").
// Real lights are what costs: each one is a light every material
// evaluates per pixel, and adding one recompiles the shaders that see it.
// So there are at most two (one on Medium, none on Low), created the first
// time a lit wand comes into view and then kept (an unused one is turned
// down to nothing, never removed), so shaders compile once per session at
// most, and a castle where nobody casts Lucerna never pays anything.

import { PointLight } from "@babylonjs/core/Lights/pointLight";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { Scene } from "@babylonjs/core/scene";
import type { Quality } from "../../settings";
import { WAND_LIGHT, spellRgb } from "./data";
import type { SpellFx } from "./vfx";

/** Real lights per quality level. */
const LIGHTS: Record<Quality, number> = { low: 0, medium: 1, high: 2 };
/** The spell data's light strength in the toon model's exposure (toon/lighting.ts). */
const WAND_TOON_SCALE = 0.45;
/** Lit wands drawn as glows at most (beyond, the farthest go unlit). */
const GLOWS_MAX = 60;

/** A character drawn this frame (nearest the camera first), with whether its wand is lit. */
export interface WandHolder {
  id: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
}

export type WandTip = (id: number, holder: WandHolder, out: Vector3) => void;

const social = WAND_LIGHT.social.kind === "light" ? WAND_LIGHT.social : { radius_m: 7, intensity: 1.2 };
const RGB = spellRgb(WAND_LIGHT);
const WHITE: readonly [number, number, number] = [1, 1, 1];

export class WandLights {
  private readonly lights: PointLight[] = [];
  private max: number;
  private readonly tip = new Vector3();
  private time = 0;
  /** Lit wands drawn last frame (for the F3 overlay). */
  shown = 0;

  constructor(
    private readonly scene: Scene,
    quality: Quality,
  ) {
    this.max = LIGHTS[quality];
  }

  setQuality(q: Quality): void {
    this.max = LIGHTS[q];
  }

  /**
   * Per frame: `lit` are the characters whose wand is lit, nearest the
   * camera first; `tip` finds a wand hand. Glows go into `fx`.
   */
  update(dt: number, lit: readonly WandHolder[], tip: WandTip, fx: SpellFx): void {
    this.time += dt;
    const real = Math.min(this.max, lit.length);
    while (this.lights.length < real) this.lights.push(this.makeLight(this.lights.length));
    const n = Math.min(lit.length, GLOWS_MAX);
    for (let i = 0; i < n; i++) {
      const w = lit[i]!;
      tip(w.id, w, this.tip);
      const flicker = 0.9 + 0.1 * Math.sin(this.time * 9 + w.id * 1.7) * Math.sin(this.time * 5.3 + w.id);
      fx.pushGlow(this.tip.x, this.tip.y, this.tip.z, 0.3 * flicker, RGB, 0.7);
      fx.pushGlint(this.tip.x, this.tip.y, this.tip.z, 0.12, WHITE, 0.9);
      if (i < real) {
        const l = this.lights[i]!;
        l.position.copyFrom(this.tip);
        l.intensity = social.intensity * WAND_TOON_SCALE * flicker;
      }
    }
    for (let i = real; i < this.lights.length; i++) this.lights[i]!.intensity = 0;
    this.shown = n;
  }

  private makeLight(i: number): PointLight {
    const l = new PointLight(`wand-light-${i}`, new Vector3(0, -100, 0), this.scene);
    l.diffuse = new Color3(RGB[0], RGB[1], RGB[2]);
    l.specular = Color3.Black(); // toon highlights come from the ramp rows
    l.range = social.radius_m;
    l.intensity = 0;
    return l;
  }

  get realLights(): number {
    return this.lights.length;
  }

  dispose(): void {
    for (const l of this.lights) l.dispose();
    this.lights.length = 0;
  }
}

/**
 * Where a wand hand roughly is, from the feet and facing alone: a little to
 * the right and in front, at chest height. For characters too far for the
 * skeleton to be worth asking.
 */
export function approximateTip(h: WandHolder, height: number, out: Vector3): void {
  const fx = Math.sin(h.yaw);
  const fz = Math.cos(h.yaw);
  // The character's right is -X when it faces +Z (yaw 0).
  out.set(h.x - fz * 0.24 + fx * 0.3, h.y + height * 0.6, h.z + fx * 0.24 + fz * 0.3);
}
