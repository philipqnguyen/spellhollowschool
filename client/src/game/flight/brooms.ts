// Brooms under their riders: one mesh drawn with thin instances,
// so every broom in view costs one draw call. A rider is anyone drawn with
// the `fly` or `hover` anim (ourselves predicted, the others from the
// snapshots); the broom's seat goes under the rider's hips bone as posed this
// frame (characters/renderer.ts `hips`), turned with the rider, and our own
// tips with the climb and banks into turns (flight.ts `pitch`, `bank`).
//
// The prop is `assets/characters/broom.glb` (one mesh,
// at most 800 triangles, palette colours, origin at the seat, the handle
// along +Z, Y up). Until that file exists the broom is a placeholder built
// here: a handle and a bundle of twigs in the palette's oak and tan
// (data/art/castle_textures.json), about 300 triangles.
//
// Instance matrices live in one preallocated Float32Array written in place
// (no garbage per frame); growing past its capacity doubles it.

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Matrix, Quaternion, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { CreateCylinder } from "@babylonjs/core/Meshes/Builders/cylinderBuilder";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { Scene } from "@babylonjs/core/scene";
import { paletteRgb } from "../castle/terrain";
import { toonifyContainer } from "../toon/convert";
import { assetUrl } from "../../assets";

/** The seat sits this far under the hips bone (m, at the character's drawn scale). */
export const SEAT_BELOW_HIPS = 0.1;
/** Where the hips are above the feet when a character has no skeleton yet (m, at scale 1). */
export const HIPS_FALLBACK = 0.95;
const START_CAPACITY = 16;

const scratchQ = new Quaternion();
const scratchM = new Matrix();
const scratchS = new Vector3();
const scratchP = new Vector3();

/** One broom's matrix (column-major as Babylon stores it) into `out` at `o`: seat at (x, y, z), heading `yaw`, nose up `pitch`, banked right `bank`. */
export function broomMatrix(x: number, y: number, z: number, yaw: number, pitch: number, bank: number, out: Float32Array, o: number): void {
  // Babylon's X turn tips +Z down for a positive angle, its Z turn lifts +X (the rider's left): so both are negated.
  Quaternion.RotationYawPitchRollToRef(yaw, -pitch, -bank, scratchQ);
  scratchS.set(1, 1, 1);
  scratchP.set(x, y, z);
  Matrix.ComposeToRef(scratchS, scratchQ, scratchP, scratchM);
  scratchM.copyToArray(out, o);
}

export class Brooms {
  private mesh: Mesh | null = null;
  private matrices = new Float32Array(START_CAPACITY * 16);
  private count = 0;
  private disposed = false;
  private source = "loading";

  constructor(
    private readonly scene: Scene,
    assetsBase: string,
  ) {
    void this.load(assetUrl(`${assetsBase}characters/broom.glb`));
  }

  private async load(url: string): Promise<void> {
    let mesh: Mesh | null = null;
    try {
      const c = await LoadAssetContainerAsync(url, this.scene, { pluginExtension: ".glb" });
      if (this.disposed) {
        c.dispose();
        return;
      }
      toonifyContainer(c, this.scene);
      c.addAllToScene();
      const parts = c.meshes.filter((m): m is Mesh => m instanceof Mesh && m.getTotalVertices() > 0);
      for (const m of parts) {
        m.computeWorldMatrix(true);
        m.setParent(null);
        m.bakeCurrentTransformIntoVertices();
      }
      mesh = parts.length > 1 ? Mesh.MergeMeshes(parts, true, true, undefined, false, true) : (parts[0] ?? null);
      for (const n of c.transformNodes) n.dispose();
      for (const m of c.meshes) if (m !== mesh && !m.isDisposed()) m.dispose();
      this.source = mesh ? "broom.glb" : "placeholder";
    } catch {
      this.source = "placeholder";
    }
    if (this.disposed) {
      mesh?.dispose();
      return;
    }
    mesh ??= placeholderBroom(this.scene);
    mesh.name = "brooms";
    mesh.isPickable = false;
    mesh.alwaysSelectAsActiveMesh = true; // the instances move every frame; their bounds are not the mesh's
    mesh.thinInstanceSetBuffer("matrix", this.matrices, 16, false);
    mesh.thinInstanceCount = 0;
    this.mesh = mesh;
  }

  /** Starts a frame's brooms. */
  begin(): void {
    this.count = 0;
  }

  /** A broom with its seat at (x, y, z) (see `broomMatrix`). */
  add(x: number, y: number, z: number, yaw: number, pitch = 0, bank = 0): void {
    if (!this.mesh) return;
    if ((this.count + 1) * 16 > this.matrices.length) {
      const grown = new Float32Array(this.matrices.length * 2);
      grown.set(this.matrices);
      this.matrices = grown;
      this.mesh.thinInstanceSetBuffer("matrix", this.matrices, 16, false);
    }
    broomMatrix(x, y, z, yaw, pitch, bank, this.matrices, this.count * 16);
    this.count++;
  }

  /** Ends the frame: draws the brooms added (one draw call, none when there are none). */
  commit(): void {
    const m = this.mesh;
    if (!m) return;
    m.thinInstanceCount = this.count;
    m.setEnabled(this.count > 0);
    if (this.count > 0) m.thinInstanceBufferUpdated("matrix");
  }

  /** For the F3 overlay. */
  perfLine(): [string, string] {
    return ["brooms", `${this.count} drawn (${this.source}${this.mesh ? `, ${Math.round((this.mesh.getTotalIndices() || 0) / 3)} triangles` : ""})`];
  }

  dispose(): void {
    this.disposed = true;
    this.mesh?.dispose();
    this.mesh = null;
  }
}

/**
 * Where the broom prop is not built: a handle (2 m, from 0.9 m behind the
 * seat to 1.1 m in front) and a bundle of twigs flaring out behind, in the
 * palette's oak and tan, with the seat at the origin and the handle along +Z.
 */
function placeholderBroom(scene: Scene): Mesh {
  const oak = paletteRgb(["oak", "oak_aged"]) ?? [0.5, 0.5, 0.5];
  const twig = paletteRgb(["parcel_tan", "canvas"]) ?? [0.6, 0.6, 0.6];
  const handle = CreateCylinder("broom-handle", { height: 2, diameter: 0.05, tessellation: 8 }, scene);
  handle.rotation.x = Math.PI / 2;
  handle.position.z = 0.1;
  const twigs = CreateCylinder("broom-twigs", { height: 0.62, diameterTop: 0.07, diameterBottom: 0.36, tessellation: 12 }, scene);
  // The narrow end at the handle, flaring backwards.
  twigs.rotation.x = -Math.PI / 2;
  twigs.position.z = -1.12;
  for (const [m, rgb] of [
    [handle, oak],
    [twigs, twig],
  ] as const) {
    m.bakeCurrentTransformIntoVertices();
    const n = m.getTotalVertices();
    const colours = new Float32Array(n * 4);
    for (let i = 0; i < n; i++) colours.set([rgb[0], rgb[1], rgb[2], 1], i * 4);
    m.setVerticesData(VertexBuffer.ColorKind, colours);
  }
  const mesh = Mesh.MergeMeshes([handle, twigs], true, true)!;
  const mat = new StandardMaterial("broom-placeholder", scene);
  mat.diffuseColor = Color3.White();
  mat.specularColor = Color3.Black();
  mesh.useVertexColors = true;
  mesh.material = mat;
  return mesh;
}
