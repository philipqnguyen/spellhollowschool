// The guardian's silver look (spells to level 20, GUARDIAN): one shader
// material shared by every companion, translucent and self-lit in the toon
// style (no scene lights): a full silver-blue body, a crisp bright rim band
// where the surface turns away (the toon cue) edged in a deeper blue that
// reads on pale stone even without bloom, a soft fresnel glow inside it,
// shimmer bands drifting up through the body, and a hashed dissolve that
// materialises and dissolves the animal with a bright edge. Blending is
// premultiplied light plus some cover (like the spell VFX layer): light
// adds, so it glows on dark stone, and the cover tints a pale wall behind it
// blue so it stays visible there; the rim is bright enough for the bloom to
// halo it. Depth is tested, not written.
//
// The vertex shader moves each vertex by its part's matrix (the `part`
// attribute indexes the `parts` uniform array; rig.ts writes them), so a
// whole animal, legs and wings swinging, is one draw. Per-companion values
// (part matrices, clock, materialise, flare) are set as each mesh binds,
// from its `metadata.guardian` (`GuardianDraw`).

import { Constants } from "@babylonjs/core/Engines/constants";
import { ShaderMaterial } from "@babylonjs/core/Materials/shaderMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Scene } from "@babylonjs/core/scene";
import { MAX_PARTS } from "./rig";

/** The silver tint (linear-ish RGB, a cool blue-white). */
export const SILVER: readonly [number, number, number] = [0.6, 0.8, 1.0];
/** The silhouette's edge: a deeper, saturated blue that reads on pale stone. */
export const EDGE: readonly [number, number, number] = [0.32, 0.58, 1.0];

/** What one companion's mesh carries for the shader (`mesh.metadata.guardian`). */
export interface GuardianDraw {
  /** MAX_PARTS matrices, 16 floats each (rig.ts layout). */
  parts: Float32Array;
  /** The companion's clock (s). */
  time: number;
  /** 0 gone .. 1 fully formed (materialise and dissolve). */
  shown: number;
  /** 0 .. 1: a ward's block flashes it bright. */
  flare: number;
  /** A per-companion offset of the dissolve pattern. */
  seed: number;
}

export const VERTEX = /* glsl */ `
precision highp float;
attribute vec3 position;
attribute vec3 normal;
attribute float part;
uniform mat4 world;
uniform mat4 viewProjection;
uniform mat4 parts[${MAX_PARTS}];
varying vec3 vWorld;
varying vec3 vNormal;
varying vec3 vLocal;
void main(void) {
  mat4 pm = parts[int(part + 0.5)];
  vec4 local = pm * vec4(position, 1.0);
  vec4 w = world * local;
  vWorld = w.xyz;
  vNormal = normalize(mat3(world) * (mat3(pm) * normal));
  vLocal = position;
  gl_Position = viewProjection * w;
}
`;

export const FRAGMENT = /* glsl */ `
precision highp float;
varying vec3 vWorld;
varying vec3 vNormal;
varying vec3 vLocal;
uniform vec3 eye;
uniform vec3 tint;
uniform vec3 edgeTint;
// x: clock (s), y: shown (0..1), z: flare (0..1), w: seed
uniform vec4 state;

float hash(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}

float noise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i), hash(i + vec3(1, 0, 0)), f.x),
                 mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x), f.y),
             mix(mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x),
                 mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}

void main(void) {
  float t = state.x;
  vec3 n = normalize(vNormal);
  vec3 v = normalize(eye - vWorld);
  float fres = 1.0 - abs(dot(n, v));
  // Toon bands: a crisp rim, a soft glow inside it.
  float rim = smoothstep(0.46, 0.6, fres);
  float glow = pow(fres, 2.0);
  // Shimmer drifting up through the body.
  float wave = sin(vLocal.y * 34.0 - t * 3.0 + sin(vLocal.x * 19.0 + vLocal.z * 15.0 + t * 1.2) * 1.8);
  float shimmer = smoothstep(0.35, 1.0, wave);
  // Materialise / dissolve: shown where the noise is under the threshold, a bright seam at its edge.
  float shown = state.y;
  float nz = noise(vLocal * 15.0 + state.w) * 0.85 + noise(vLocal * 41.0) * 0.15;
  float k = shown * 1.12;
  float vis = smoothstep(nz - 0.05, nz + 0.01, k);
  float seam = shown < 0.999 ? vis * (1.0 - smoothstep(0.0, 0.1, k - nz)) : 0.0;
  float flare = state.z;
  // The silhouette's outermost band: a deeper, saturated blue with more
  // cover, so the shape reads against pale stone without bloom (light added
  // to a pale wall barely shows; a blue edge over it does).
  float edge = smoothstep(0.78, 0.95, fres);
  // A full silver body (it must read on pale stone as well as dark wood),
  // the rim brighter still for the toon cue, whitening toward its core.
  float light = 0.42 + 0.22 * shimmer + 0.85 * glow + 1.35 * rim + flare * (0.8 + 0.9 * rim);
  vec3 rgb = tint * light * (1.0 - 0.45 * edge) + edgeTint * edge * 0.95
           + vec3(1.0) * (0.25 * rim * (1.0 - edge) + seam * 1.4 + flare * 0.35);
  float cover = 0.26 + 0.2 * glow + 0.18 * rim + 0.3 * edge + 0.2 * flare;
  if (vis < 0.003) discard;
  gl_FragColor = vec4(rgb * vis, cover * vis);
}
`;

/** The shared material: per-mesh values are set as each companion binds. */
export function createGuardianMaterial(
  scene: Scene,
  camera: () => Camera | null,
): ShaderMaterial {
  const mat = new ShaderMaterial(
    "guardian",
    scene,
    { vertexSource: VERTEX, fragmentSource: FRAGMENT },
    {
      attributes: ["position", "normal", "part"],
      uniforms: ["world", "viewProjection", "parts", "eye", "tint", "edgeTint", "state"],
      needAlphaBlending: true,
    },
  );
  mat.alphaMode = Constants.ALPHA_PREMULTIPLIED;
  mat.disableDepthWrite = true;
  mat.backFaceCulling = true;
  mat.setColor3("tint", new Color3(SILVER[0], SILVER[1], SILVER[2]));
  mat.setColor3("edgeTint", new Color3(EDGE[0], EDGE[1], EDGE[2]));
  mat.onBindObservable.add((mesh: AbstractMesh) => {
    const effect = mat.getEffect();
    const d = (mesh.metadata as { guardian?: GuardianDraw } | null)?.guardian;
    if (!effect || !d) return;
    effect.setMatrices("parts", d.parts);
    effect.setFloat4("state", d.time, d.shown, d.flare, d.seed);
    const cam = camera();
    if (cam) effect.setVector3("eye", cam.globalPosition);
  });
  return mat;
}
