// Camera-facing soft sprites for potion effects and find-spot shimmer, drawn
// as thin instances: one mesh and one draw call per layer, however many
// characters glow or steam. Each instance is a centre, a size and a colour
// (two vec4 attributes); the vertex shader turns the quad toward the camera,
// so the CPU only writes eight floats per sprite per frame. The shapes (a
// soft aura, a four-pointed glint, a steam puff) are computed in the
// fragment shader: no textures, no art files.
//
// Layers have a fixed capacity (their instance buffers are allocated once);
// past it, further sprites in a frame are dropped, nearest characters first
// because the caller pushes them in that order.

import { Constants } from "@babylonjs/core/Engines/constants";
import { ShaderMaterial } from "@babylonjs/core/Materials/shaderMaterial";
import { Vector2, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { VertexData } from "@babylonjs/core/Meshes/mesh.vertexData";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";

export type SpriteShape = "glow" | "star" | "puff";

/**
 * How a layer's sprites combine with what is behind them:
 *
 * - `add`: pure light (glints). Reads on dark ground, vanishes on pale.
 * - `alpha`: ordinary transparency (steam): its own colour over the ground.
 * - `halo`: premultiplied light plus cover (Glow auras): the sprite adds its
 *   colour like light and also covers part of the ground, so it tints a pale
 *   stone floor instead of washing out to white on it, and still glows on
 *   dark wood.
 */
export type SpriteBlend = "add" | "alpha" | "halo";

export interface LayerOptions {
  shape: SpriteShape;
  blend: SpriteBlend;
  /** Most sprites drawn per frame. */
  capacity: number;
  /** Width and height of a sprite of size 1 (an aura is taller than wide). */
  stretch?: [number, number];
  /** Spells L20: drawn through walls (no depth test; Aperio's spots read behind a wall). */
  xray?: boolean;
}

const VERTEX = /* glsl */ `
precision highp float;
attribute vec3 position;
attribute vec4 fxCenter;
attribute vec4 fxColor;
uniform mat4 viewProjection;
uniform vec3 camRight;
uniform vec3 camUp;
uniform vec2 stretch;
varying vec2 vUv;
varying vec4 vColor;
void main(void) {
  vec3 p = fxCenter.xyz + (camRight * position.x * stretch.x + camUp * position.y * stretch.y) * fxCenter.w;
  gl_Position = viewProjection * vec4(p, 1.0);
  vUv = position.xy * 2.0;
  vColor = fxColor;
}
`;

const FRAGMENT = /* glsl */ `
precision highp float;
varying vec2 vUv;
varying vec4 vColor;
void main(void) {
  float r = length(vUv);
  vec3 rgb = vColor.rgb;
#if defined(SHAPE_GLOW)
  // A soft aura with a brighter band around the silhouette's edge, so the
  // halo has a shape of its own and does not read as a smudge.
  float halo = pow(clamp(1.0 - r, 0.0, 1.0), 1.5);
  float band = exp(-pow((r - 0.6) * 6.5, 2.0));
  // Faded out at the bottom, where the sprite (behind the character) would
  // otherwise be cut by the floor in a hard line.
  float a = clamp(halo * 0.7 + band * 0.6, 0.0, 1.0) * smoothstep(-0.95, -0.4, vUv.y);
#elif defined(SHAPE_STAR)
  float core = exp(-r * r * 22.0);
  float rays = max(0.0, 1.0 - abs(vUv.x * vUv.y) * 70.0) * clamp(1.0 - r, 0.0, 1.0);
  float a = clamp(core + rays * rays, 0.0, 1.0);
#else
  // A puff with a shaded rim: light in the middle, a cool grey edge, so it
  // reads against pale stone as well as dark wood.
  float a = smoothstep(1.0, 0.25, r) * (0.8 + 0.2 * sin(vUv.x * 7.0 + vUv.y * 5.0));
  rgb *= mix(0.58, 1.05, smoothstep(0.95, 0.15, r));
#endif
  if (a < 0.004) discard;
#if defined(BLEND_HALO)
  // Premultiplied: light added (rgb) and ground covered (alpha).
  float k = a * vColor.a;
  gl_FragColor = vec4(rgb * k * 1.3, k * 0.5);
#else
  gl_FragColor = vec4(rgb, a * vColor.a);
#endif
}
`;

const SHAPE_DEFINE: Record<SpriteShape, string> = { glow: "#define SHAPE_GLOW", star: "#define SHAPE_STAR", puff: "#define SHAPE_PUFF" };
const BLEND_MODE: Record<SpriteBlend, number> = { add: Constants.ALPHA_ADD, alpha: Constants.ALPHA_COMBINE, halo: Constants.ALPHA_PREMULTIPLIED };

/** One layer of sprites: fill it with `push` between `begin` and `end` each frame. */
export class SpriteLayer {
  readonly mesh: Mesh;
  private readonly material: ShaderMaterial;
  private readonly centres: Float32Array;
  private readonly colours: Float32Array;
  private count = 0;
  private shown = -1;
  readonly capacity: number;

  constructor(scene: Scene, name: string, opts: LayerOptions) {
    this.capacity = opts.capacity;
    const mesh = new Mesh(`fx-${name}`, scene);
    const quad = new VertexData();
    quad.positions = [-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0];
    quad.indices = [0, 1, 2, 0, 2, 3];
    quad.applyToMesh(mesh, false);
    mesh.isPickable = false;
    mesh.doNotSyncBoundingInfo = true;
    // The sprites follow characters all over the zone: never frustum-cull the whole layer.
    mesh.alwaysSelectAsActiveMesh = true;
    // Thin instances need a matrix buffer for their count; the shader ignores it.
    const identity = new Float32Array(opts.capacity * 16);
    for (let i = 0; i < opts.capacity; i++) {
      const o = i * 16;
      identity[o] = identity[o + 5] = identity[o + 10] = identity[o + 15] = 1;
    }
    mesh.thinInstanceSetBuffer("matrix", identity, 16, true);
    this.centres = new Float32Array(opts.capacity * 4);
    this.colours = new Float32Array(opts.capacity * 4);
    mesh.thinInstanceSetBuffer("fxCenter", this.centres, 4, false);
    mesh.thinInstanceSetBuffer("fxColor", this.colours, 4, false);
    mesh.thinInstanceCount = 0;

    const mat = new ShaderMaterial(
      `fx-${name}`,
      scene,
      { vertexSource: VERTEX, fragmentSource: FRAGMENT },
      {
        attributes: ["position", "fxCenter", "fxColor"],
        uniforms: ["viewProjection", "camRight", "camUp", "stretch"],
        defines: [SHAPE_DEFINE[opts.shape], ...(opts.blend === "halo" ? ["#define BLEND_HALO"] : [])],
        needAlphaBlending: true,
      },
    );
    mat.alphaMode = BLEND_MODE[opts.blend];
    mat.disableDepthWrite = true;
    if (opts.xray) mat.depthFunction = Constants.ALWAYS;
    mat.backFaceCulling = false;
    const [sx, sy] = opts.stretch ?? [1, 1];
    mat.setVector2("stretch", new Vector2(sx, sy));
    mesh.material = mat;
    this.material = mat;
    this.mesh = mesh;
    mesh.setEnabled(false);
  }

  begin(): void {
    this.count = 0;
  }

  /** Adds a sprite centred at (x, y, z), `size` metres across, colour (r, g, b) at opacity `a`. False when full. */
  push(x: number, y: number, z: number, size: number, r: number, g: number, b: number, a: number): boolean {
    if (this.count >= this.capacity) return false;
    const o = this.count * 4;
    this.centres[o] = x;
    this.centres[o + 1] = y;
    this.centres[o + 2] = z;
    this.centres[o + 3] = size;
    this.colours[o] = r;
    this.colours[o + 1] = g;
    this.colours[o + 2] = b;
    this.colours[o + 3] = a;
    this.count++;
    return true;
  }

  /** Uploads this frame's sprites (only the used part of the buffers). */
  end(right: Vector3, up: Vector3): void {
    const n = this.count;
    if (n === 0) {
      if (this.shown !== 0) this.mesh.setEnabled(false);
      this.shown = 0;
      return;
    }
    if (this.shown <= 0) this.mesh.setEnabled(true);
    this.shown = n;
    this.mesh.thinInstanceCount = n;
    this.mesh.thinInstanceBufferUpdated("fxCenter");
    this.mesh.thinInstanceBufferUpdated("fxColor");
    this.material.setVector3("camRight", right);
    this.material.setVector3("camUp", up);
  }

  get size(): number {
    return this.count;
  }

  dispose(): void {
    this.material.dispose();
    this.mesh.dispose();
  }
}

/** The camera's right and up directions in world space, for billboarding. */
export function cameraAxes(camera: Camera, right: Vector3, up: Vector3): void {
  camera.getDirectionToRef(Vector3.RightReadOnly, right);
  camera.getDirectionToRef(Vector3.UpReadOnly, up);
}
