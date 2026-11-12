// The sky: a gradient dome drawn per pixel from the view direction (zenith,
// horizon and the ground below it), a soft sun or moon glow and, at night, a
// sparse star field. One draw call, unlit, no fog; it never writes depth, so
// it stays behind everything and the outline pass treats it as empty sky.
// Day and night (clock/daynight.ts) set its colours through `setLook`.
// The dome sits inside the camera's far plane (the caller sizes it), as it
// must for the depth test to keep it behind the world.

import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import { ShaderMaterial } from "@babylonjs/core/Materials/shaderMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Vector2, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { CreateSphere } from "@babylonjs/core/Meshes/Builders/sphereBuilder";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { Scene } from "@babylonjs/core/scene";

export type Rgb = [number, number, number];

/** The sky's colours (sRGB 0..1). */
export interface SkyColours {
  zenith: Rgb;
  horizon: Rgb;
  /** Below the horizon (seen from towers and the grounds). */
  ground: Rgb;
  /** The glow around the sun or moon. */
  glow: Rgb;
  /** 0 by day, 1 at night: how many stars show. */
  stars: number;
}

ShaderStore.ShadersStore["toonSkyVertexShader"] = `
precision highp float;
attribute vec3 position;
uniform mat4 world;
uniform mat4 viewProjection;
varying vec3 vDir;
void main(void) {
  vDir = position;
  vec4 p = viewProjection * world * vec4(position, 1.0);
  // On the far plane: behind everything whatever the draw order.
  gl_Position = vec4(p.xy, p.w * 0.99999, p.w);
}
`;

ShaderStore.ShadersStore["toonSkyFragmentShader"] = `
precision highp float;
varying vec3 vDir;
uniform vec3 zenith;
uniform vec3 horizon;
uniform vec3 groundColour;
uniform vec3 glow;
uniform vec3 sunDir;
uniform vec2 extra;
float hash(vec3 p) {
  return fract(sin(dot(p, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
}
void main(void) {
  vec3 d = normalize(vDir);
  float h = d.y;
  vec3 col = h >= 0.0 ? mix(horizon, zenith, pow(h, 0.5)) : mix(horizon, groundColour, smoothstep(0.0, 0.25, -h));
  float s = max(dot(d, sunDir), 0.0);
  col += glow * (pow(s, 48.0) * 0.55 + pow(s, 6.0) * 0.18);
  if (extra.x > 0.0 && h > 0.05) {
    vec3 cell = floor(d * 260.0);
    float star = step(0.9965, hash(cell)) * smoothstep(0.05, 0.3, h);
    col += vec3(0.85, 0.9, 1.0) * star * extra.x;
  }
  gl_FragColor = vec4(col, extra.y);
}
`;

const scratch = new Vector3();
const registry = new WeakMap<Scene, SkyDome>();

/** The sky dome of `scene` (day and night recolour it). */
export function skyDomeOf(scene: Scene): SkyDome | undefined {
  return registry.get(scene);
}

export class SkyDome {
  readonly mesh: Mesh;
  private readonly material: ShaderMaterial;
  private readonly colours: SkyColours;
  /** Towards the sun (or moon). */
  private readonly sun = new Vector3(0, 1, 0);
  private alphaOut = 1;

  constructor(scene: Scene, radius: number, initial: SkyColours, towardSun: Vector3) {
    this.mesh = CreateSphere("sky", { diameter: radius * 2, segments: 16, sideOrientation: Mesh.BACKSIDE }, scene);
    this.colours = { zenith: [...initial.zenith], horizon: [...initial.horizon], ground: [...initial.ground], glow: [...initial.glow], stars: initial.stars };
    this.sun.copyFrom(towardSun).normalize();
    const mat = new ShaderMaterial("sky", scene, "toonSky", {
      attributes: ["position"],
      uniforms: ["world", "viewProjection", "zenith", "horizon", "groundColour", "glow", "sunDir", "extra"],
      // Drawn in the alpha-test queue: after every opaque mesh, so the depth test
      // leaves only the pixels the world does not cover (no full-screen overdraw),
      // and before transparent effects, which still draw over it.
      needAlphaTesting: true,
    });
    mat.backFaceCulling = false;
    mat.disableDepthWrite = true;
    mat.fogEnabled = false;
    this.material = mat;
    this.mesh.material = mat;
    this.mesh.infiniteDistance = true;
    this.mesh.isPickable = false;
    this.mesh.applyFog = false;
    this.mesh.alwaysSelectAsActiveMesh = true;
    this.mesh.doNotSyncBoundingInfo = true;
    registry.set(scene, this);
    this.push();
  }

  /** New colours (day and night); cheap, but meant for about once a second. */
  setLook(c: SkyColours): void {
    const o = this.colours;
    for (let i = 0; i < 3; i++) {
      o.zenith[i] = c.zenith[i]!;
      o.horizon[i] = c.horizon[i]!;
      o.ground[i] = c.ground[i]!;
      o.glow[i] = c.glow[i]!;
    }
    o.stars = c.stars;
    this.push();
  }

  /** Where the glow sits: the direction towards the sun or moon. */
  setSunDirection(toward: Vector3): void {
    scratch.copyFrom(toward).normalize();
    if (scratch.equalsWithEpsilon(this.sun, 1e-4)) return;
    this.sun.copyFrom(scratch);
    this.push();
  }

  /**
   * The outline pass reads the frame's alpha as a line weight: the sky
   * writes 0 there (no lines), and 1 when it draws straight to the canvas.
   */
  setOutlineMask(on: boolean): void {
    this.alphaOut = on ? 0 : 1;
    this.push();
  }

  get look(): Readonly<SkyColours> {
    return this.colours;
  }

  private push(): void {
    const m = this.material;
    const c = this.colours;
    m.setColor3("zenith", Color3.FromArray(c.zenith));
    m.setColor3("horizon", Color3.FromArray(c.horizon));
    m.setColor3("groundColour", Color3.FromArray(c.ground));
    m.setColor3("glow", Color3.FromArray(c.glow));
    m.setVector3("sunDir", this.sun);
    m.setVector2("extra", new Vector2(c.stars, this.alphaOut));
  }

  dispose(): void {
    this.mesh.dispose();
    this.material.dispose();
  }
}
