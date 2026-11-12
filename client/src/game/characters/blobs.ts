// Soft round blob shadows under characters (docs/art/character-adoption.md
// section 7): everyone at Low, and at Medium and High everyone the
// character shadow map does not cover. One thin-instanced quad for all of
// them, so they cost one draw call however many characters stand around.
//
// The quad is darkened by a radial gradient in its shader and multiplied
// onto the frame (`ALPHA_MULTIPLY`), writing no alpha of its own: the toon
// outline pass reads the frame's alpha as each pixel's line weight
// (toon/post.ts), and a blob must not change that. 0.9 m across the
// shoulders and 0.6 m front to back at default height, scaled with the
// character, turned with it, 35 % dark at its centre, on the floor under
// the feet (the floor under a seat when seated: the caller passes it).
//
// Instance matrices live in one preallocated Float32Array written in place;
// growing past its capacity reallocates (rare: twice the old size).

import { Constants } from "@babylonjs/core/Engines/constants";
import { ShaderMaterial } from "@babylonjs/core/Materials/shaderMaterial";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
// The shader's includes, registered here (castle M5c, the page-load shader
// race): a ShaderMaterial imports nothing lazily, so without these the blobs,
// compiled before any standard material had imported Babylon's default
// shader, fetched the includes over HTTP, got the page's index.html, and
// Babylon kept that as the include: every toon material compiled after it
// failed (shader-includes.test.ts).
import "@babylonjs/core/Shaders/ShadersInclude/instancesDeclaration";
import "@babylonjs/core/Shaders/ShadersInclude/instancesVertex";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { VertexData } from "@babylonjs/core/Meshes/mesh.vertexData";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { Scene } from "@babylonjs/core/scene";

/** Blob size at default height (m): across the shoulders, front to back. */
export const BLOB_SIZE: readonly [number, number] = [0.9, 0.6];
/** How dark the centre gets (the contract's alpha 0.35). */
export const BLOB_STRENGTH = 0.35;
/** Lifted this far above the floor (m) against z-fighting. */
const LIFT = 0.012;

ShaderStore.ShadersStore["characterBlobVertexShader"] = `
precision highp float;
attribute vec3 position;
attribute vec2 uv;
#include<instancesDeclaration>
uniform mat4 viewProjection;
varying vec2 vUV;
void main(void) {
#include<instancesVertex>
  gl_Position = viewProjection * finalWorld * vec4(position, 1.0);
  vUV = uv;
}
`;

ShaderStore.ShadersStore["characterBlobFragmentShader"] = `
precision highp float;
varying vec2 vUV;
uniform float strength;
void main(void) {
  vec2 p = vUV * 2.0 - 1.0;
  float g = 1.0 - smoothstep(0.1, 1.0, dot(p, p));
  // Multiplied onto the frame: colour darkens, the frame's alpha is kept.
  gl_FragColor = vec4(vec3(1.0 - strength * g), 0.0);
}
`;

/** The matrix of one blob, column-major, into `out` at `o` (pure: for tests). */
export function blobMatrix(x: number, y: number, z: number, yaw: number, scale: number, out: Float32Array, o: number): void {
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  const w = BLOB_SIZE[0] * scale;
  const d = BLOB_SIZE[1] * scale;
  // A unit quad in XZ, scaled (w, 1, d), turned by yaw about +Y.
  out[o] = c * w;
  out[o + 1] = 0;
  out[o + 2] = -s * w;
  out[o + 3] = 0;
  out[o + 4] = 0;
  out[o + 5] = 1;
  out[o + 6] = 0;
  out[o + 7] = 0;
  out[o + 8] = s * d;
  out[o + 9] = 0;
  out[o + 10] = c * d;
  out[o + 11] = 0;
  out[o + 12] = x;
  out[o + 13] = y + LIFT;
  out[o + 14] = z;
  out[o + 15] = 1;
}

export class BlobShadows {
  private readonly mesh: Mesh;
  private readonly material: ShaderMaterial;
  private matrices: Float32Array;
  private count = 0;
  private shown = -1;

  constructor(scene: Scene, capacity = 128) {
    const mesh = new Mesh("character-blobs", scene);
    const data = new VertexData();
    // A unit quad in XZ facing up (both windings drawn: no culling).
    data.positions = [-0.5, 0, -0.5, 0.5, 0, -0.5, 0.5, 0, 0.5, -0.5, 0, 0.5];
    data.uvs = [0, 0, 1, 0, 1, 1, 0, 1];
    data.indices = [0, 2, 1, 0, 3, 2];
    data.normals = [0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0];
    data.applyToMesh(mesh, false);
    mesh.isPickable = false;
    mesh.doNotSyncBoundingInfo = true;
    mesh.alwaysSelectAsActiveMesh = true;
    const mat = new ShaderMaterial("character-blobs", scene, { vertex: "characterBlob", fragment: "characterBlob" }, {
      attributes: ["position", "uv"],
      // `world` too: thin instances multiply the mesh's world matrix in (instancesVertex).
      uniforms: ["world", "viewProjection", "strength"],
      needAlphaBlending: true,
    });
    mat.setFloat("strength", BLOB_STRENGTH);
    mat.alphaMode = Constants.ALPHA_MULTIPLY;
    mat.disableDepthWrite = true;
    mat.backFaceCulling = false;
    mat.zOffset = -2;
    mesh.material = mat;
    this.mesh = mesh;
    this.material = mat;
    this.matrices = new Float32Array(capacity * 16);
    mesh.thinInstanceSetBuffer("matrix", this.matrices, 16, false);
    mesh.thinInstanceCount = 0;
    mesh.setEnabled(false);
  }

  /** Starts a frame's blobs. */
  begin(): void {
    this.count = 0;
  }

  /** A blob under a character standing at (x, y, z) facing `yaw`, `scale` times its default size. */
  add(x: number, y: number, z: number, yaw: number, scale: number): void {
    if ((this.count + 1) * 16 > this.matrices.length) {
      const grown = new Float32Array(this.matrices.length * 2);
      grown.set(this.matrices);
      this.matrices = grown;
      this.mesh.thinInstanceSetBuffer("matrix", this.matrices, 16, false);
    }
    blobMatrix(x, y, z, yaw, scale, this.matrices, this.count * 16);
    this.count++;
  }

  /** Uploads this frame's blobs (one buffer update, one draw). */
  commit(): void {
    const n = this.count;
    if (n !== this.shown) {
      this.mesh.thinInstanceCount = n;
      this.mesh.setEnabled(n > 0);
      this.shown = n;
    }
    if (n > 0) this.mesh.thinInstanceBufferUpdated("matrix");
  }

  get size(): number {
    return this.count;
  }

  dispose(): void {
    this.mesh.dispose();
    this.material.dispose();
  }
}
