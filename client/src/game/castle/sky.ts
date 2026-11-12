// The enchanted ceiling (castle kit M3, docs/art/castle-kit.md): the
// Great Hall's `castle_sky` panels show the sky of the hour instead of a
// roof. The panels' texture (`sky_clouds`) is greyscale: R = G = B is cloud
// cover (0 clear .. 1 full cloud), A the star field. Every hue comes from
// the lighting data (data/art/lighting.json through toon/lighting.ts
// `castleLight`), so retinting the sky is a data edit:
//
// - the sky runs from the hour's `horizon` at the wall plate (v = 0) to its
//   `zenith` at the ridge (v = 1), as the outdoor dome does from the horizon
//   up;
// - clouds lift towards the sun's (or moon's) colour by day and darken at
//   night (their luminance at most `NIGHT_CLOUD_LUMA_MAX` at midnight), their
//   thick parts shaded towards the hour's fog;
// - stars add `stars x A x (1 - cloud)`, `stars` being `nightLook(daylight)`'s,
//   tinted by the moon's hue;
// - nothing is lit, fogged or outlined: the panels are a picture of the sky,
//   and no colour channel reaches the bloom's cut (`SKY_PANEL_PEAK`), or the
//   whole ceiling would halo.
//
// `castleSkyLook` is the per-hour arithmetic (pure, unit tested, mirrored
// per pixel by `skyPanelPixel`); clock/daynight.ts feeds it once per clock
// tick through `castleSkyOf(scene).setFromLight`. The plugin only reads the
// shared numbers when Babylon rebinds the material: no per-frame work and
// no per-frame allocation.

import { MaterialPluginBase } from "@babylonjs/core/Materials/materialPluginBase";
import { ShaderLanguage } from "@babylonjs/core/Materials/shaderLanguage";
import type { Material } from "@babylonjs/core/Materials/material";
import type { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import type { UniformBuffer } from "@babylonjs/core/Materials/uniformBuffer";
import type { AbstractEngine } from "@babylonjs/core/Engines/abstractEngine";
import type { Scene } from "@babylonjs/core/scene";
import { Vector4 } from "@babylonjs/core/Maths/math.vector";
import type { SubMesh } from "@babylonjs/core/Meshes/subMesh";
import { castleLight, type CastleLight, type Rgb } from "../toon/lighting";
import { toonOutlineMask, trackOutlineMask, untrackOutlineMask } from "../toon/material";

/** No channel of the panels goes above this: the bloom cuts from 0.95 (toon/post.ts). */
export const SKY_PANEL_PEAK = 0.92;
/** Cloud luminance in full day and at midnight (the contract's ceiling: 0.35). */
export const DAY_CLOUD_LUMA = 0.84;
export const NIGHT_CLOUD_LUMA = 0.27;
export const NIGHT_CLOUD_LUMA_MAX = 0.35;
/** How far the lit cloud colour leans to the sun's or moon's hue (0 white .. 1 the light's own hue). */
const CLOUD_HUE = 0.45;
/** How far a thick cloud's shade leans to the hour's fog, and how dark it is against the lit part. */
const CLOUD_SHADE_FOG = 0.5;
const CLOUD_SHADE = 0.78;
/** Cover below this is clear sky, above `COVER_FULL` all cloud; thick from `THICK`. */
const COVER_EDGE = 0.1;
const COVER_FULL = 0.62;
const THICK: readonly [number, number] = [0.6, 1.0];
/** The sky's gradient bends towards the zenith (as the outdoor dome's `pow(h, 0.5)`, gentler). */
const GRADIENT_POWER = 0.75;
/** How far the stars lean to the moon's hue, and their brightness. */
const STAR_HUE = 0.25;
const STAR_LEVEL = 0.95;

/** What the panels show for one hour (sRGB 0..1). */
export interface CastleSkyLook {
  horizon: Rgb;
  zenith: Rgb;
  /** A lit cloud. */
  cloud: Rgb;
  /** A thick cloud's underside. */
  shade: Rgb;
  /** The stars' colour times how many show (0 by day). */
  stars: Rgb;
}

/** Perceived brightness, as the toon shaders measure it. */
export function luma(c: readonly number[]): number {
  return 0.3 * c[0]! + 0.59 * c[1]! + 0.11 * c[2]!;
}

const clamp01 = (v: number): number => Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0));
const smoothstep = (a: number, b: number, x: number): number => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};

/** `c` scaled down (hue kept) so no channel passes `peak`. */
function capPeak(c: Rgb, peak = SKY_PANEL_PEAK): Rgb {
  const m = Math.max(c[0], c[1], c[2]);
  if (m <= peak) return c;
  const k = peak / m;
  return [c[0] * k, c[1] * k, c[2] * k];
}

/** `c` scaled (hue kept) to luminance `target`, then capped at the peak. */
function toLuma(c: Rgb, target: number): Rgb {
  const l = luma(c);
  const k = l > 1e-4 ? target / l : 0;
  return capPeak([c[0] * k, c[1] * k, c[2] * k]);
}

/** `c`'s hue at full strength (its brightest channel 1), `t` of the way from white. */
function hue(c: Rgb, t: number): Rgb {
  const m = Math.max(c[0], c[1], c[2], 1e-3);
  return [1 + (c[0] / m - 1) * t, 1 + (c[1] / m - 1) * t, 1 + (c[2] / m - 1) * t];
}

/**
 * The panels' colours for a castle light (`castleLight(hour)`) and the
 * night's star amount (`nightLook(daylight).stars`, 0 by day, 1 at night).
 * Writes into `out` when given (daynight.ts reuses one).
 */
export function castleSkyLook(l: CastleLight, stars: number, out?: CastleSkyLook): CastleSkyLook {
  const s = clamp01(stars);
  const o = out ?? { horizon: [0, 0, 0], zenith: [0, 0, 0], cloud: [0, 0, 0], shade: [0, 0, 0], stars: [0, 0, 0] };
  const set = (dst: Rgb, src: Rgb) => {
    dst[0] = src[0];
    dst[1] = src[1];
    dst[2] = src[2];
  };
  set(o.horizon, capPeak([l.horizon[0], l.horizon[1], l.horizon[2]]));
  set(o.zenith, capPeak([l.zenith[0], l.zenith[1], l.zenith[2]]));
  // Lit clouds: the sun's (or moon's) hue, as bright as the hour allows.
  const cloudLuma = DAY_CLOUD_LUMA + (NIGHT_CLOUD_LUMA - DAY_CLOUD_LUMA) * s;
  const cloud = toLuma(hue(l.sunColour, CLOUD_HUE), cloudLuma);
  set(o.cloud, cloud);
  // Thick cloud: towards the fog's colour, darker than the lit part.
  const f = CLOUD_SHADE_FOG;
  const shadeHue: Rgb = [cloud[0] + (l.fog[0] - cloud[0]) * f, cloud[1] + (l.fog[1] - cloud[1]) * f, cloud[2] + (l.fog[2] - cloud[2]) * f];
  set(o.shade, toLuma(shadeHue, cloudLuma * CLOUD_SHADE));
  const star = hue(l.sunColour, STAR_HUE * l.moon);
  const k = STAR_LEVEL * s;
  set(o.stars, [star[0] * k, star[1] * k, star[2] * k]);
  return o;
}

/**
 * One panel pixel (sRGB 0..1), as the shader computes it: `v` 0 at the
 * plate .. 1 at the ridge, `cover` the texture's cloud (R), `starA` its
 * star field (A).
 */
export function skyPanelPixel(look: CastleSkyLook, v: number, cover: number, starA: number): Rgb {
  const g = Math.pow(clamp01(v), GRADIENT_POWER);
  const c = clamp01(cover);
  const w = smoothstep(COVER_EDGE, COVER_FULL, c);
  const thick = smoothstep(THICK[0], THICK[1], c);
  const out: Rgb = [0, 0, 0];
  for (let i = 0; i < 3; i++) {
    const sky = look.horizon[i]! + (look.zenith[i]! - look.horizon[i]!) * g;
    const cloud = look.cloud[i]! + (look.shade[i]! - look.cloud[i]!) * thick;
    out[i] = Math.min(SKY_PANEL_PEAK, sky + (cloud - sky) * w + look.stars[i]! * clamp01(starA) * (1 - c));
  }
  return out;
}

// ------------------------------------------------------------ the shared look

/** Floats per look in the uniform buffer: five vec4 (horizon, zenith, cloud, shade, stars). */
const LOOK_FLOATS = 20;

/** The panels' look for one scene: set once per clock tick, read by every sky material when it binds. */
export class CastleSky {
  /** horizon, zenith, cloud, shade, stars as vec4 (w unused). */
  readonly packed = new Float32Array(LOOK_FLOATS);
  /**
   * The same five colours as Vector4s for the plugin's binds: passing a vector by
   * reference keeps the per-bind path allocation-free (fifteen loose floats read
   * from a Float32Array and handed to a call are boxed as heap numbers on every
   * bind: about 140 B a frame, found in review).
   */
  readonly vectors: readonly Vector4[] = [new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4()];
  private readonly look: CastleSkyLook = { horizon: [0, 0, 0], zenith: [0, 0, 0], cloud: [0, 0, 0], shade: [0, 0, 0], stars: [0, 0, 0] };
  private version = 0;

  constructor() {
    // Noon until the clock is known (as scene.ts starts the castle).
    this.setFromLight(castleLight(13), 0);
  }

  /** Bumped on every change (tests, the F3 overlay). */
  get generation(): number {
    return this.version;
  }

  get current(): Readonly<CastleSkyLook> {
    return this.look;
  }

  /** The hour's light and stars (daynight.ts, once per clock tick: a few small arrays, never per frame). */
  setFromLight(l: CastleLight, stars: number): void {
    castleSkyLook(l, stars, this.look);
    const p = this.packed;
    const rows = [this.look.horizon, this.look.zenith, this.look.cloud, this.look.shade, this.look.stars];
    let changed = false;
    for (let r = 0; r < 5; r++) {
      const c = rows[r]!;
      for (let i = 0; i < 3; i++) {
        const v = c[i]!;
        if (p[r * 4 + i] !== v) {
          p[r * 4 + i] = v;
          changed = true;
        }
      }
      this.vectors[r]!.set(p[r * 4]!, p[r * 4 + 1]!, p[r * 4 + 2]!, 1);
    }
    if (changed) this.version++;
  }
}

const registry = new WeakMap<Scene, CastleSky>();

/** The panels' look of `scene` (made on first use; day and night set it). */
export function castleSkyOf(scene: Scene): CastleSky {
  let s = registry.get(scene);
  if (!s) registry.set(scene, (s = new CastleSky()));
  return s;
}

// ------------------------------------------------------------ the material plugin

const f = (v: number): string => (Number.isInteger(v) ? `${v}.0` : `${v}`);

/** The fragment code (exported for tests): the sky replaces the unlit colour, after the texture is read. */
export function castleSkyFragment(): { [point: string]: string } {
  return {
    CUSTOM_FRAGMENT_DEFINITIONS: `
#if defined(CASTLE_SKY) && defined(DIFFUSE)
vec3 castleSkyColour(vec4 tex, float v) {
  float g = pow(clamp(v, 0.0, 1.0), ${f(GRADIENT_POWER)});
  float c = clamp(tex.r, 0.0, 1.0);
  float w = smoothstep(${f(COVER_EDGE)}, ${f(COVER_FULL)}, c);
  float thick = smoothstep(${f(THICK[0])}, ${f(THICK[1])}, c);
  vec3 sky = mix(castleSkyH.rgb, castleSkyZ.rgb, g);
  vec3 cloud = mix(castleSkyC.rgb, castleSkyS.rgb, thick);
  return min(mix(sky, cloud, w) + castleSkyT.rgb * clamp(tex.a, 0.0, 1.0) * (1.0 - c), vec3(${f(SKY_PANEL_PEAK)}));
}
#endif
`,
    CUSTOM_FRAGMENT_BEFORE_FOG: `
#if defined(CASTLE_SKY) && defined(DIFFUSE)
// The texel as read (R = cloud, A = stars; the panels have no vertex colours); v is 0
// at the plate and 1 at the ridge, and glTF's v runs down the image (invertY off).
color.rgb = castleSkyColour(baseColor, 1.0 - vDiffuseUV.y);
#endif
`,
    CUSTOM_FRAGMENT_BEFORE_FRAGCOLOR: `
#if defined(CASTLE_SKY) && defined(CASTLE_SKY_OUTLINE_MASK)
// The outline pass reads alpha as the line weight: world, weight 0 (no lines on the sky).
color.a = 0.5;
#endif
`,
  };
}

/**
 * The enchanted ceiling on an unlit StandardMaterial (castle/materials.ts
 * builds it for `tint: "sky_dome"`): the colour comes from the scene's
 * `CastleSky` and the texture's cloud and star channels.
 */
export class CastleSkyPlugin extends MaterialPluginBase {
  private readonly sky: CastleSky;

  constructor(material: Material) {
    super(material, "CastleSky", 160, { CASTLE_SKY: false, CASTLE_SKY_OUTLINE_MASK: false }, true, true);
    this.sky = castleSkyOf(material.getScene());
    trackOutlineMask(this);
  }

  override getClassName(): string {
    return "CastleSkyPlugin";
  }

  override isCompatible(shaderLanguage: ShaderLanguage): boolean {
    return shaderLanguage === ShaderLanguage.GLSL;
  }

  override prepareDefines(defines: MaterialDefines): void {
    defines["CASTLE_SKY"] = true;
    defines["CASTLE_SKY_OUTLINE_MASK"] = toonOutlineMask(this._material.getScene());
  }

  override getUniforms(): { ubo: { name: string; size: number; type: string }[]; fragment: string } {
    const names = ["castleSkyH", "castleSkyZ", "castleSkyC", "castleSkyS", "castleSkyT"];
    return {
      ubo: names.map((name) => ({ name, size: 4, type: "vec4" })),
      fragment: names.map((n) => `uniform vec4 ${n};`).join("\n"),
    };
  }

  override bindForSubMesh(uniformBuffer: UniformBuffer, _scene: Scene, _engine: AbstractEngine, _subMesh: SubMesh): void {
    // By reference (no loose floats: see CastleSky.vectors).
    const v = this.sky.vectors;
    uniformBuffer.updateVector4("castleSkyH", v[0]!);
    uniformBuffer.updateVector4("castleSkyZ", v[1]!);
    uniformBuffer.updateVector4("castleSkyC", v[2]!);
    uniformBuffer.updateVector4("castleSkyS", v[3]!);
    uniformBuffer.updateVector4("castleSkyT", v[4]!);
  }

  override getCustomCode(shaderType: string): { [pointName: string]: string } | null {
    return shaderType === "fragment" ? castleSkyFragment() : null;
  }

  override dispose(forceDisposeTextures?: boolean): void {
    untrackOutlineMask(this);
    super.dispose(forceDisposeTextures);
  }
}
