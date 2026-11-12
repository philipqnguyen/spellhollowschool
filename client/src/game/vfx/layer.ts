// Spell VFX drawn as thin instances of one quad: every bolt, trail, spark,
// shield and flash of a frame in a single draw call. Each instance is a
// centre and size, a colour and an *axis* with a shape id (three vec4
// attributes); the vertex shader either turns the quad toward the camera
// (orbs, sparks, rings), stretches it along the axis and turns it about
// it (streaks: a comet's head and tail along its flight; beams: a band
// spanning the axis), or lays it across the axis (discs: a ring with the
// axis as its normal, a chalk circle on the floor), so the CPU writes
// twelve floats per sprite per frame. The shapes are computed in the
// fragment shader: no textures, no art files, clean saturated shapes with a
// white-hot core (the stylized look; no toon shader yet).
//
// Blending is premultiplied light plus a little cover (like the potion
// glows): bright on dark wood, still saturated on pale stone instead of
// washing out to white. Depth is tested but not written, so bodies and
// walls hide what is behind them and sprites never hide each other.
//
// The layer has a fixed capacity (buffers are allocated once); past it, the
// rest of a frame's sprites are dropped, so callers push the important ones
// (bolts, shields) before decoration (sparks).

import { Constants } from "@babylonjs/core/Engines/constants";
import { ShaderMaterial } from "@babylonjs/core/Materials/shaderMaterial";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { VertexData } from "@babylonjs/core/Meshes/mesh.vertexData";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import type { ShapeId } from "./shapes";

export { Shape, type ShapeId } from "./shapes";

const VERTEX = /* glsl */ `
precision highp float;
attribute vec3 position;
attribute vec4 vfxCenter;
attribute vec4 vfxColor;
attribute vec4 vfxAxis;
uniform mat4 viewProjection;
uniform vec3 camRight;
uniform vec3 camUp;
uniform vec3 camPos;
varying vec2 vUv;
varying vec4 vColor;
varying float vShape;
void main(void) {
  vec3 c = vfxCenter.xyz;
  float size = vfxCenter.w;
  vec3 p;
  if (vfxAxis.w > 2.5 && vfxAxis.w < 3.5) {
    // Streak: from the centre (x = +0.5, the front) back along the axis to
    // the end of the tail (x = -0.5), size wide, turned about the axis to face
    // the camera as far as it can.
    vec3 axis = vfxAxis.xyz;
    vec3 side = cross(axis, normalize(camPos - c));
    float sl = length(side);
    side = sl > 1e-4 ? side / sl : camUp;
    p = c + axis * (0.5 - position.x) + side * position.y * size;
  } else if (vfxAxis.w > 4.5 && vfxAxis.w < 5.5) {
    // Disc: a ring lying across its axis (the normal; up when unset).
    float nl = length(vfxAxis.xyz);
    vec3 n = nl > 1e-4 ? vfxAxis.xyz / nl : vec3(0.0, 1.0, 0.0);
    vec3 ref = abs(n.y) < 0.95 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
    vec3 u = normalize(cross(ref, n));
    vec3 v = cross(n, u);
    p = c + (u * position.x + v * position.y) * size;
  } else if (vfxAxis.w > 5.5 && vfxAxis.w < 6.5) {
    // Beam: centred, spanning the axis end to end, size wide, turned about
    // the axis to face the camera.
    vec3 axis = vfxAxis.xyz;
    vec3 side = cross(axis, normalize(camPos - c));
    float sl = length(side);
    side = sl > 1e-4 ? side / sl : camUp;
    p = c + axis * position.x + side * position.y * size;
  } else {
    p = c + (camRight * position.x + camUp * position.y) * size;
  }
  gl_Position = viewProjection * vec4(p, 1.0);
  vUv = position.xy * 2.0;
  vColor = vfxColor;
  vShape = vfxAxis.w;
}
`;

const FRAGMENT = /* glsl */ `
precision highp float;
varying vec2 vUv;
varying vec4 vColor;
varying float vShape;
void main(void) {
  float r = length(vUv);
  vec3 rgb = vColor.rgb;
  float a = 0.0;
  float core = 0.0;
  if (vShape < 0.5) {
    // Orb: a soft body and a white-hot core.
    a = pow(clamp(1.0 - r, 0.0, 1.0), 1.6);
    core = exp(-r * r * 14.0);
  } else if (vShape < 1.5) {
    // Spark: a four-pointed glint.
    core = exp(-r * r * 26.0);
    float rays = max(0.0, 1.0 - abs(vUv.x * vUv.y) * 60.0) * clamp(1.0 - r, 0.0, 1.0);
    a = clamp(core + rays * rays, 0.0, 1.0);
  } else if (vShape < 2.5) {
    // Ring: a crisp band, a faint hexagonal shimmer inside.
    float band = exp(-pow((r - 0.82) * 11.0, 2.0));
    float ang = atan(vUv.y, vUv.x);
    float hex = 0.5 + 0.5 * cos(ang * 6.0 + r * 9.0);
    float fill = smoothstep(0.95, 0.2, r) * (0.1 + 0.12 * hex);
    a = clamp(band + fill, 0.0, 1.0) * step(r, 1.0);
    core = band * 0.45;
  } else if (vShape < 3.5) {
    // Streak (a comet's tail): x = 1 at the front, -1 at the tail's end;
    // it tapers and fades backwards, with a hot thread down the middle.
    float along = (vUv.x + 1.0) * 0.5;
    float across = abs(vUv.y);
    float width = 0.25 + 0.75 * along;
    a = pow(along, 1.8) * (1.0 - smoothstep(width * 0.55, width, across));
    core = pow(along, 3.0) * exp(-across * across * 30.0);
  } else if (vShape < 4.5) {
    // Shard: a jagged diamond, bright at the edges.
    float d = abs(vUv.x) * 1.6 + abs(vUv.y) * 0.75;
    a = (1.0 - smoothstep(0.85, 1.0, d)) * (0.55 + 0.45 * smoothstep(0.4, 0.95, d));
    core = (1.0 - smoothstep(0.0, 0.5, d)) * 0.4;
  } else if (vShape < 5.5) {
    // Disc: a crisp chalk band broken into runes (eight dashes), a faint fill.
    float band = exp(-pow((r - 0.84) * 14.0, 2.0));
    float ang = atan(vUv.y, vUv.x);
    float dash = 0.55 + 0.45 * smoothstep(0.15, 0.35, abs(fract(ang * 1.2732) - 0.5));
    float fill = smoothstep(0.9, 0.3, r) * 0.08;
    a = clamp(band * dash + fill, 0.0, 1.0) * step(r, 1.0);
    core = band * 0.5;
  } else {
    // Beam: a soft band across (y), fading at its two ends (x), a hot thread.
    float across = abs(vUv.y);
    float along = abs(vUv.x);
    float ends = 1.0 - smoothstep(0.7, 1.0, along);
    a = (1.0 - smoothstep(0.25, 1.0, across)) * ends;
    core = exp(-across * across * 24.0) * ends;
  }
  float k = a * vColor.a;
  if (k < 0.004) discard;
  // A white-hot middle over the saturated colour.
  rgb = mix(rgb, vec3(1.0), clamp(core, 0.0, 1.0) * 0.75);
  // Premultiplied: light added (rgb) and a little of the ground covered (alpha).
  gl_FragColor = vec4(rgb * k * 1.35, k * 0.35);
}
`;

/** One frame's spell sprites: fill with `push` between `begin` and `end`. */
export class VfxLayer {
  readonly mesh: Mesh;
  readonly capacity: number;
  private readonly material: ShaderMaterial;
  private readonly centres: Float32Array;
  private readonly colours: Float32Array;
  private readonly axes: Float32Array;
  private readonly right = new Vector3();
  private readonly up = new Vector3();
  private count = 0;
  private shown = -1;

  constructor(scene: Scene, name: string, capacity: number) {
    this.capacity = capacity;
    const mesh = new Mesh(`vfx-${name}`, scene);
    const quad = new VertexData();
    quad.positions = [-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0];
    quad.indices = [0, 1, 2, 0, 2, 3];
    quad.applyToMesh(mesh, false);
    mesh.isPickable = false;
    mesh.doNotSyncBoundingInfo = true;
    mesh.alwaysSelectAsActiveMesh = true;
    // Thin instances need a matrix buffer for their count; the shader ignores it.
    const identity = new Float32Array(capacity * 16);
    for (let i = 0; i < capacity; i++) {
      const o = i * 16;
      identity[o] = identity[o + 5] = identity[o + 10] = identity[o + 15] = 1;
    }
    mesh.thinInstanceSetBuffer("matrix", identity, 16, true);
    this.centres = new Float32Array(capacity * 4);
    this.colours = new Float32Array(capacity * 4);
    this.axes = new Float32Array(capacity * 4);
    mesh.thinInstanceSetBuffer("vfxCenter", this.centres, 4, false);
    mesh.thinInstanceSetBuffer("vfxColor", this.colours, 4, false);
    mesh.thinInstanceSetBuffer("vfxAxis", this.axes, 4, false);
    mesh.thinInstanceCount = 0;

    const mat = new ShaderMaterial(
      `vfx-${name}`,
      scene,
      { vertexSource: VERTEX, fragmentSource: FRAGMENT },
      {
        attributes: ["position", "vfxCenter", "vfxColor", "vfxAxis"],
        uniforms: ["viewProjection", "camRight", "camUp", "camPos"],
        needAlphaBlending: true,
      },
    );
    mat.alphaMode = Constants.ALPHA_PREMULTIPLIED;
    mat.disableDepthWrite = true;
    mat.backFaceCulling = false;
    mesh.material = mat;
    // Among transparent meshes, after the potion sprites.
    mesh.alphaIndex = 1000;
    this.material = mat;
    this.mesh = mesh;
    mesh.setEnabled(false);
  }

  begin(): void {
    this.count = 0;
  }

  get size(): number {
    return this.count;
  }

  /**
   * Adds a sprite of `shape` centred at (x, y, z), `size` metres across,
   * colour (r, g, b) at opacity `a`. Streaks also take their tail: a world
   * vector (ax, ay, az) from the head back to the tail's end. False when full.
   */
  push(shape: ShapeId, x: number, y: number, z: number, size: number, r: number, g: number, b: number, a: number, ax = 0, ay = 0, az = 0): boolean {
    if (this.count >= this.capacity || a <= 0.003 || size <= 0) return false;
    const o = this.count * 4;
    const c = this.centres;
    c[o] = x;
    c[o + 1] = y;
    c[o + 2] = z;
    c[o + 3] = size;
    const k = this.colours;
    k[o] = r;
    k[o + 1] = g;
    k[o + 2] = b;
    k[o + 3] = a;
    const s = this.axes;
    s[o] = ax;
    s[o + 1] = ay;
    s[o + 2] = az;
    s[o + 3] = shape;
    this.count++;
    return true;
  }

  /** Uploads this frame's sprites (only the used part of the buffers matters). */
  end(camera: Camera): void {
    const n = this.count;
    if (n === 0) {
      if (this.shown !== 0) this.mesh.setEnabled(false);
      this.shown = 0;
      return;
    }
    if (this.shown <= 0) this.mesh.setEnabled(true);
    this.shown = n;
    this.mesh.thinInstanceCount = n;
    this.mesh.thinInstanceBufferUpdated("vfxCenter");
    this.mesh.thinInstanceBufferUpdated("vfxColor");
    this.mesh.thinInstanceBufferUpdated("vfxAxis");
    camera.getDirectionToRef(Vector3.RightReadOnly, this.right);
    camera.getDirectionToRef(Vector3.UpReadOnly, this.up);
    this.material.setVector3("camRight", this.right);
    this.material.setVector3("camUp", this.up);
    this.material.setVector3("camPos", camera.globalPosition);
  }

  dispose(): void {
    this.material.dispose();
    this.mesh.dispose();
  }
}
