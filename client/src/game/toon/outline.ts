// Character outlines, Genshin style (docs/art/character-look.md section 4):
// an inverted hull. Each character tier mesh gets a second mesh on the
// same geometry (the same vertex buffers, so the same skinning, hair sway and
// Chameleon Tonic colour buffer) drawn with this material: front faces culled,
// every vertex pushed out along its baked smoothed normal (`toonOutline`, the
// glTF attribute `_OUTLINE`, tools/blender/characters/charlib/outline_bake.py)
// by a width in screen pixels, so only a thin rim of back faces shows round
// each silhouette and fold. One extra draw per outlined character; MSAA on
// the scene target anti-aliases the rim like any other edge.
//
// Per vertex, in the vertex shader:
//
// - width (render pixels) = `width` x outlineScale(render height) x the
//   region rule's `width` x the baked weight (0 on the face's eyes, brows,
//   nose and mouth; `outlineWidthPx`), thinned with the camera distance from
//   `near` to `far` (down to `farScale`) and gone from `fadeStart` to
//   `fadeEnd` (beyond it the hull mesh is not drawn at all);
// - the hull sits `depthPush` metres behind the surface along the view ray,
//   so it only shows past a silhouette, never in front of nearby surfaces
//   of the same character (no lines across the nose or the cheek);
// - colour = a darker, more saturated shade of the vertex colour (the skin
//   tone, the hair colour, the garment's dye), by the region's rule:
//   max(pow(c, gamma) x scale x tint, floor) (`outlineColour`): dark warm
//   brown round skin, a deep shade of the hair colour round hair, a deep
//   shade of the dye round cloth, never pure black (`floor`). Then times the
//   scene's fill and key light (`outlineLight`, written once a frame), so a
//   line at night is a night line.
//
// Every number is art data: data/art/toon_palette.json `outline` (no colour
// is written per character; recolouring a dye or the hair recolours its
// line). Low draws no outlines (settings.ts `outlines`). No per-frame
// allocation: the per-frame uniforms are preallocated vectors.

import { ShaderMaterial } from "@babylonjs/core/Materials/shaderMaterial";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import { Material } from "@babylonjs/core/Materials/material";
import { Vector4, type Matrix } from "@babylonjs/core/Maths/math.vector";
import type { Scene } from "@babylonjs/core/scene";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { SubMesh } from "@babylonjs/core/Meshes/subMesh";
import type { Effect } from "@babylonjs/core/Materials/effect";
import type { Nullable } from "@babylonjs/core/types";
import { BindBonesParameters } from "@babylonjs/core/Materials/materialHelper.functions";
import "@babylonjs/core/Shaders/ShadersInclude/bonesDeclaration";
import "@babylonjs/core/Shaders/ShadersInclude/bonesVertex";
import toonPaletteJson from "../../../../data/art/toon_palette.json";
import { CHARACTER_REGIONS, REGION_COUNT, regionIndex, type CharacterRegion, type Rgb } from "./palette";
import { drapeOf, TOON_DRAPE_KINDS } from "./material";

/** The vertex attribute (4 normalised signed bytes: smoothed normal xyz, width weight w) the hull reads. */
export const OUTLINE_KIND = "toonOutline";

/** How one material region's line is drawn. */
export interface OutlineRule {
  /** Width multiplier (0: no line). */
  width: number;
  /** The darken/saturate rule: max(pow(c, gamma) * scale * tint, floor). */
  gamma: number;
  scale: number;
  tint: Rgb;
  floor: Rgb;
}

/** The outline's look (toon_palette.json `outline`). */
export interface OutlineLook {
  /** Line width in render pixels at a render height of 1000 (scaled by `outlineScale`). */
  width: number;
  /** Distance thinning (m): full width to `near`, `farScale` of it at `far`. */
  near: number;
  far: number;
  farScale: number;
  /** The line fades out from `fadeStart` to `fadeEnd` (m); beyond, the hull is not drawn. */
  fadeStart: number;
  fadeEnd: number;
  /** How far (m) the hull sits behind the surface along the view ray. */
  depthPush: number;
  /** The coarsest LOD tier that is outlined (0-3). */
  maxTier: number;
  /** The light (fill + key, luma-ish sum) at which the line takes its full colour, and the least it keeps. */
  lightRef: number;
  lightFloor: number;
  regions: Record<CharacterRegion, OutlineRule>;
}

export const OUTLINE_DEFAULT_RULE: OutlineRule = { width: 1, gamma: 1.6, scale: 0.55, tint: [1, 1, 1], floor: [0.07, 0.055, 0.06] };

export const OUTLINE_DEFAULTS: Omit<OutlineLook, "regions"> = {
  width: 1.2,
  near: 3,
  far: 20,
  farScale: 0.5,
  fadeStart: 24,
  fadeEnd: 34,
  depthPush: 0.008,
  maxTier: 2,
  lightRef: 1.2,
  lightFloor: 0.15,
};

function num(v: unknown, d: number, lo = -Infinity, hi = Infinity): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : d;
}

function rgb(v: unknown, d: Rgb): Rgb {
  return Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number" && Number.isFinite(x)) ? [v[0], v[1], v[2]] : [d[0], d[1], d[2]];
}

function rule(j: unknown, base: OutlineRule): OutlineRule {
  const r = (j ?? {}) as Partial<Record<keyof OutlineRule, unknown>>;
  return {
    width: num(r.width, base.width, 0, 4),
    gamma: num(r.gamma, base.gamma, 0.5, 4),
    scale: num(r.scale, base.scale, 0, 1),
    tint: rgb(r.tint, base.tint),
    floor: rgb(r.floor, base.floor),
  };
}

/** The outline look from the palette json (`outline`), every field clamped, defaults where absent. */
export function parseOutline(json: unknown): OutlineLook {
  const j = ((json as { outline?: Record<string, unknown> } | null)?.outline ?? {}) as Record<string, unknown>;
  const d = OUTLINE_DEFAULTS;
  const rows = (j.regions ?? {}) as Record<string, unknown>;
  const base = rule(rows.default, OUTLINE_DEFAULT_RULE);
  const regions = {} as Record<CharacterRegion, OutlineRule>;
  for (const r of CHARACTER_REGIONS) regions[r] = rule(rows[r], base);
  const near = num(j.near, d.near, 0);
  const fadeStart = num(j.fadeStart, d.fadeStart, 0);
  return {
    width: num(j.width, d.width, 0, 8),
    near,
    far: Math.max(near + 0.01, num(j.far, d.far, 0)),
    farScale: num(j.farScale, d.farScale, 0, 1),
    fadeStart,
    fadeEnd: Math.max(fadeStart + 0.01, num(j.fadeEnd, d.fadeEnd, 0)),
    depthPush: num(j.depthPush, d.depthPush, 0, 0.1),
    maxTier: Math.round(num(j.maxTier, d.maxTier, 0, 3)),
    lightRef: num(j.lightRef, d.lightRef, 0.01),
    lightFloor: num(j.lightFloor, d.lightFloor, 0, 1),
    regions,
  };
}

export const TOON_OUTLINE: OutlineLook = parseOutline(toonPaletteJson);

/** The line's reach scale for a render height: one at 1000 high, up to 2.5 (a native Retina frame is 1.8). */
export const OUTLINE_SCALE = { referenceHeight: 1000, min: 1, max: 2.5 };

export function outlineScale(renderHeight: number): number {
  return Math.min(OUTLINE_SCALE.max, Math.max(OUTLINE_SCALE.min, renderHeight / OUTLINE_SCALE.referenceHeight));
}

function smooth(a: number, b: number, v: number): number {
  const t = Math.min(1, Math.max(0, (v - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

/** The distance factor of the width (mirrors the shader): 1 near, `farScale` far, 0 past the fade. */
export function outlineDistanceScale(dist: number, look: OutlineLook = TOON_OUTLINE): number {
  const thin = 1 + (look.farScale - 1) * smooth(look.near, look.far, dist);
  return thin * (1 - smooth(look.fadeStart, look.fadeEnd, dist));
}

/** A vertex's line width in render pixels (mirrors the shader). `weight` is the baked width weight (0..1). */
export function outlineWidthPx(region: CharacterRegion, weight: number, dist: number, renderHeight: number, look: OutlineLook = TOON_OUTLINE): number {
  return look.width * outlineScale(renderHeight) * look.regions[region].width * Math.min(1, Math.max(0, weight)) * outlineDistanceScale(dist, look);
}

/**
 * The line colour (sRGB 0..1) for a vertex colour `c` under the region's
 * rule, times the light (`light`, per channel, already clamped), as the
 * shader computes it. Writes into `out`.
 */
export function outlineColour(c: readonly number[], r: OutlineRule, light: readonly number[] = [1, 1, 1], out: number[] = [0, 0, 0]): number[] {
  for (let k = 0; k < 3; k++) {
    const v = Math.pow(Math.min(1, Math.max(0, c[k]!)), r.gamma) * r.scale * r.tint[k]!;
    out[k] = Math.max(v, r.floor[k]!) * light[k]!;
  }
  return out;
}

/**
 * The line's light from the scene's fill (hemisphere sky colour x intensity)
 * and key (sun or moon colour x intensity), per channel, over `lightRef`,
 * clamped to [lightFloor, 1] (mirrors what the renderer writes). Writes into `out`.
 */
export function outlineLight(fill: readonly number[], fillIntensity: number, key: readonly number[], keyIntensity: number, look: OutlineLook = TOON_OUTLINE, out: number[] = [0, 0, 0]): number[] {
  for (let k = 0; k < 3; k++) out[k] = Math.min(1, Math.max(look.lightFloor, (fill[k]! * fillIntensity + key[k]! * keyIntensity) / look.lightRef));
  return out;
}

/** Whether a character drawing `tier` at `dist` metres gets its hull drawn. */
export function outlineDrawn(on: boolean, tier: number, dist: number, look: OutlineLook = TOON_OUTLINE): boolean {
  return on && tier <= look.maxTier && dist < look.fadeEnd;
}

/** GLSL float literal. */
const f = (v: number): string => (Number.isInteger(v) ? `${v}.0` : `${v}`);
const v3 = (c: readonly number[]): string => `vec3(${f(c[0]!)}, ${f(c[1]!)}, ${f(c[2]!)})`;

/** The vertex shader for a look: the region rules are constants (art data at load), the rest uniforms. */
export function outlineVertexShader(look: OutlineLook = TOON_OUTLINE, drape = false): string {
  const rules = CHARACTER_REGIONS.map((r) => look.regions[r]);
  const ruleA = rules.map((r) => `vec4(${f(r.width)}, ${f(r.gamma)}, ${f(r.scale)}, 0.0)`).join(", ");
  const tints = rules.map((r) => v3(r.tint)).join(", ");
  const floors = rules.map((r) => v3(r.floor)).join(", ");
  return `
precision highp float;
attribute vec3 position;
attribute vec4 color;
attribute vec4 ${OUTLINE_KIND};
${drape ? "attribute vec3 toonDrapeChair;\nattribute vec3 toonDrapeFloor;\nattribute vec3 toonDrapeRest;\n// the sitting drape's weights (chair, floor, rest), per mesh (toon/material.ts TOON_DRAPE_KINDS)\nuniform vec4 outlineDrape;" : ""}
#include<bonesDeclaration>
uniform mat4 world;
uniform mat4 viewProjection;
// eye position (world), the width's scale (base width x outlineScale, render px)
uniform vec4 outlineEye;
// render width, height (px), depth push (m), 0
uniform vec4 outlineView;
// the line's light (rgb, per channel) and the frame alpha it writes
uniform vec4 outlineLight;
varying vec3 vOutline;
const vec4 OUTLINE_A[${REGION_COUNT}] = vec4[${REGION_COUNT}](${ruleA});
const vec3 OUTLINE_TINT[${REGION_COUNT}] = vec3[${REGION_COUNT}](${tints});
const vec3 OUTLINE_FLOOR[${REGION_COUNT}] = vec3[${REGION_COUNT}](${floors});
void main(void) {
  mat4 finalWorld = world;
#include<bonesVertex>
  vec3 restPosition = position${drape ? " + toonDrapeChair * outlineDrape.x + toonDrapeFloor * outlineDrape.y + toonDrapeRest * outlineDrape.z" : ""};
  vec4 wp = finalWorld * vec4(restPosition, 1.0);
  vec3 n = mat3(finalWorld) * ${OUTLINE_KIND}.xyz;
  int r = int(floor(floor(color.a * 255.0 + 0.5) / 16.0));
  r = r < ${REGION_COUNT} ? r : ${regionIndex("neutral")};
  vec4 A = OUTLINE_A[r];
  vec3 toEye = wp.xyz - outlineEye.xyz;
  float d = length(toEye);
  float thin = mix(1.0, ${f(look.farScale)}, smoothstep(${f(look.near)}, ${f(look.far)}, d)) * (1.0 - smoothstep(${f(look.fadeStart)}, ${f(look.fadeEnd)}, d));
  float wpx = outlineEye.w * A.x * ${OUTLINE_KIND}.w * thin;
  // Behind the surface along the view ray: the rim shows only past a silhouette.
  wp.xyz += toEye / max(d, 1e-4) * outlineView.z;
  vec4 cp = viewProjection * wp;
  vec2 dir = (viewProjection * vec4(n, 0.0)).xy * outlineView.xy;
  float dl = length(dir);
  dir = dl > 1e-6 ? dir / dl : vec2(0.0);
  cp.xy += dir * (wpx * 2.0 / outlineView.xy) * cp.w;
  gl_Position = cp;
  vec3 c = clamp(color.rgb, 0.0, 1.0);
  vOutline = max(pow(c, vec3(A.y)) * A.z * OUTLINE_TINT[r], OUTLINE_FLOOR[r]) * outlineLight.rgb;
}
`;
}

const FRAGMENT = `
precision highp float;
varying vec3 vOutline;
uniform vec4 outlineLight;
void main(void) {
  gl_FragColor = vec4(vOutline, outlineLight.a);
}
`;

ShaderStore.ShadersStore["toonOutlineFragmentShader"] = FRAGMENT;

/** The frame alpha the hull writes while the screen passes read it (SSAO's class: a character, toon/post.ts). */
export const OUTLINE_MASK_ALPHA = 0.25;

/**
 * The hull's material (one per scene; every outlined character shares it).
 * `update` writes the per-frame uniforms (no allocation).
 */
export class OutlineMaterial extends ShaderMaterial {
  private readonly eye = new Vector4(0, 0, 0, 1);
  private readonly view = new Vector4(1, 1, 0, 0);
  private readonly light = new Vector4(1, 1, 1, 1);

  /** `drape`: the variant for characters wearing a skirt or robe (the sitting drape's attributes). */
  constructor(
    scene: Scene,
    private readonly look: OutlineLook = TOON_OUTLINE,
    readonly drape = false,
  ) {
    const name = drape ? "toonOutlineDrape" : "toonOutline";
    ShaderStore.ShadersStore[`${name}VertexShader`] = outlineVertexShader(look, drape);
    super(drape ? "character-outline-drape" : "character-outline", scene, { vertex: name, fragment: "toonOutline" }, {
      attributes: drape ? ["position", "color", OUTLINE_KIND, ...TOON_DRAPE_KINDS] : ["position", "color", OUTLINE_KIND],
      uniforms: drape ? ["world", "viewProjection", "outlineEye", "outlineView", "outlineLight", "outlineDrape"] : ["world", "viewProjection", "outlineEye", "outlineView", "outlineLight"],
      needAlphaBlending: false,
    });
    // glTF winds counter-clockwise (as the character material); the hull culls the front faces.
    this.sideOrientation = scene.useRightHandedSystem ? Material.CounterClockWiseSideOrientation : Material.ClockWiseSideOrientation;
    this.backFaceCulling = true;
    this.cullBackFaces = false;
    this.setVector4("outlineEye", this.eye);
    this.setVector4("outlineView", this.view);
    this.setVector4("outlineLight", this.light);
  }

  /**
   * ShaderMaterial binds the bones only when the material changes between
   * draws (`scene.getCachedMaterial() !== this`), so the second of two hulls
   * drawn in a row would be skinned by the first one's skeleton (wrong pose;
   * a rig with more bones, the hair's sway joints, read zero matrices and
   * shot vertices to the origin). Every hull binds its own skeleton.
   */
  override bind(world: Matrix, mesh?: AbstractMesh, effectOverride?: Nullable<Effect>, subMesh?: SubMesh): void {
    super.bind(world, mesh, effectOverride, subMesh);
    const effect = effectOverride ?? this.getEffect();
    if (mesh && effect) BindBonesParameters(mesh, effect);
    if (this.drape && effect) {
      const d = drapeOf(mesh);
      effect.setFloat4("outlineDrape", d[0]!, d[1]!, d[2]!, 0);
    }
  }

  /**
   * Once a frame: the camera position, the render size, the line's light
   * (rgb, from `outlineLight`) and whether the frame alpha carries the SSAO
   * class (`mask`).
   */
  update(eyeX: number, eyeY: number, eyeZ: number, renderWidth: number, renderHeight: number, light: readonly number[], mask: boolean): void {
    this.eye.set(eyeX, eyeY, eyeZ, this.look.width * outlineScale(renderHeight));
    this.view.set(Math.max(1, renderWidth), Math.max(1, renderHeight), this.look.depthPush, 0);
    this.light.set(light[0]!, light[1]!, light[2]!, mask ? OUTLINE_MASK_ALPHA : 1);
  }
}

/** Measurements (perf-probe.ts `outlines`): forces the hulls off (false) or leaves them to the quality (null). */
export const OUTLINE_OVERRIDE: { on: boolean | null } = { on: null };
