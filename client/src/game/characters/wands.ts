// Wands in characters' right hands (wand-state.ts says when and where): one
// mesh drawn with thin instances, so every wand out costs one draw call
// together, and none when nobody casts. The renderer (renderer.ts) adds an
// instance per character whose wand is out, after posing it.
//
// The mesh is `assets/vfx/wand.glb` (tools/blender/vfx/vfx_models.py
// `wand_mesh`: origin at the grip, the shaft along +Y), and
// `assets/vfx/manifest.json` gives its tip and its transform under each
// body's hand socket. Until those load, or where the assets are greybox
// stand-ins, the wand is a placeholder built here (a dark handle and a
// warm tapering shaft, about 60 triangles) that hangs along the hand bone.
//
// Instance matrices live in one preallocated Float32Array written in place
// (no garbage per frame); growing past its capacity doubles it.

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { CreateCylinder } from "@babylonjs/core/Meshes/Builders/cylinderBuilder";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { Scene } from "@babylonjs/core/scene";
import { assetShipped, assetUrl } from "../../assets";
import { toonifyContainer } from "../toon/convert";
import type { Rig } from "./clips";
import { attachMatrix, fallbackMatrix, STAND_IN_BUTT, STAND_IN_TIP, WAND_HAND, WAND_SOCKET, type WandAttach, type WandFit } from "./wand-state";

const START_CAPACITY = 8;

/** What `vfx/manifest.json` says about the wand (only the fields used). */
interface WandManifest {
  file?: unknown;
  tip?: unknown;
  attach?: Record<string, WandAttach>;
}

export class Wands {
  private mesh: Mesh | null = null;
  private matrices = new Float32Array(START_CAPACITY * 16);
  private count = 0;
  private disposed = false;
  private source = "loading";
  /** Grip to tip (m) of the mesh drawn. */
  private tip = STAND_IN_TIP;
  /** The built wand's transform per body id (empty: the fallback on every rig). */
  private attach: Record<string, WandAttach> = {};
  /** Each rig's fit, worked out once per rig and body (renewed when the wand loads). */
  private fits = new WeakMap<Rig, Map<string, WandFit>>();

  constructor(
    private readonly scene: Scene,
    private readonly assetsBase: string,
  ) {
    void this.load();
  }

  private async load(): Promise<void> {
    let mesh: Mesh | null = null;
    try {
      const wand = await this.manifest();
      const file = typeof wand?.file === "string" ? wand.file : null;
      if (wand && file && !this.disposed) {
        const c = await LoadAssetContainerAsync(assetUrl(this.assetsBase + file), this.scene, { pluginExtension: ".glb" });
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
        if (mesh) {
          const tip = Array.isArray(wand.tip) && typeof wand.tip[1] === "number" ? wand.tip[1] : STAND_IN_TIP;
          this.tip = tip;
          this.attach = wand.attach ?? {};
          this.source = `wand.glb, ${Object.keys(this.attach).length} bodies fitted`;
        }
      }
    } catch {
      mesh?.dispose();
      mesh = null;
    }
    if (this.disposed) {
      mesh?.dispose();
      return;
    }
    if (!mesh) {
      this.source = "placeholder";
      this.tip = STAND_IN_TIP;
      this.attach = {};
      mesh = placeholderWand(this.scene);
    }
    mesh.name = "wands";
    mesh.isPickable = false;
    mesh.alwaysSelectAsActiveMesh = true; // the instances move every frame; their bounds are not the mesh's
    mesh.thinInstanceSetBuffer("matrix", this.matrices, 16, false);
    mesh.thinInstanceCount = 0;
    mesh.setEnabled(false);
    this.fits = new WeakMap();
    this.mesh = mesh;
  }

  /** The wand entry of `vfx/manifest.json`, or null (not built: greybox stand-ins). */
  private async manifest(): Promise<WandManifest | null> {
    const path = `${this.assetsBase}vfx/manifest.json`;
    if (assetShipped(path) === false) return null;
    const res = await fetch(assetUrl(path));
    if (!res.ok) return null;
    // Dev servers answer unknown paths with index.html: not JSON, so null.
    const json = (await res.json().catch(() => null)) as { wand?: WandManifest } | null;
    return json?.wand ?? null;
  }

  /** Whether wands are drawn yet (the mesh, built or placeholder, is loaded). */
  get ready(): boolean {
    return this.mesh !== null;
  }

  /**
   * How the wand hangs on `rig` of body `body` (`body_a`; null: a greybox
   * rig): under the hand socket as the asset build fitted it, else along
   * the hand bone. Its `bone` is -1 on a rig with neither.
   */
  fit(rig: Rig, body: string | null): WandFit {
    let byBody = this.fits.get(rig);
    if (!byBody) {
      byBody = new Map();
      this.fits.set(rig, byBody);
    }
    const key = body ?? "";
    let f = byBody.get(key);
    if (!f) {
      const a = body ? this.attach[body] : undefined;
      const socket = a ? rig.boneIndex(a.bone || WAND_SOCKET) : -1;
      f = socket >= 0 && a ? { bone: socket, matrix: attachMatrix(a), tip: this.tip } : { bone: rig.boneIndex(WAND_HAND), matrix: fallbackMatrix(this.tip), tip: this.tip };
      byBody.set(key, f);
    }
    return f;
  }

  /** Starts a frame's wands. */
  begin(): void {
    this.count = 0;
  }

  /**
   * Room for one more wand: returns the offset in `buffer` to write its
   * matrix at (wand-state.ts `wandMatrix`), or -1 while nothing is loaded.
   */
  reserve(): number {
    const m = this.mesh;
    if (!m) return -1;
    if ((this.count + 1) * 16 > this.matrices.length) {
      const grown = new Float32Array(this.matrices.length * 2);
      grown.set(this.matrices);
      this.matrices = grown;
      m.thinInstanceSetBuffer("matrix", this.matrices, 16, false);
    }
    return this.count++ * 16;
  }

  /** The instance matrices (valid until the next `reserve`). */
  get buffer(): Float32Array {
    return this.matrices;
  }

  /** Ends the frame: draws the wands reserved (one draw call, none when there are none). */
  commit(): void {
    const m = this.mesh;
    if (!m) return;
    m.thinInstanceCount = this.count;
    m.setEnabled(this.count > 0);
    if (this.count > 0) m.thinInstanceBufferUpdated("matrix");
  }

  /** Wands drawn this frame (tests, probes). */
  get drawn(): number {
    return this.count;
  }

  /** For the F3 overlay. */
  perfLine(): [string, string] {
    return ["wands", `${this.count} out (${this.source}${this.mesh ? `, ${Math.round((this.mesh.getTotalIndices() || 0) / 3)} triangles` : ""})`];
  }

  dispose(): void {
    this.disposed = true;
    this.mesh?.dispose();
    this.mesh = null;
  }
}

/**
 * Where the wand is not built: a dark handle with a pommel and a warm
 * shaft tapering to a pale tip, origin at the grip, +Y to the tip.
 */
function placeholderWand(scene: Scene): Mesh {
  const handle = CreateCylinder("wand-handle", { height: STAND_IN_BUTT + 0.03, diameterTop: 0.019, diameterBottom: 0.023, tessellation: 6 }, scene);
  handle.position.y = (0.03 - STAND_IN_BUTT) / 2;
  const shaft = CreateCylinder("wand-shaft", { height: STAND_IN_TIP - 0.03, diameterTop: 0.007, diameterBottom: 0.016, tessellation: 6 }, scene);
  shaft.position.y = (STAND_IN_TIP + 0.03) / 2;
  const parts: [Mesh, readonly [number, number, number]][] = [
    [handle, [0.23, 0.13, 0.09]],
    [shaft, [0.42, 0.25, 0.16]],
  ];
  for (const [m, rgb] of parts) {
    m.bakeCurrentTransformIntoVertices();
    const n = m.getTotalVertices();
    const colours = new Float32Array(n * 4);
    for (let i = 0; i < n; i++) colours.set([rgb[0], rgb[1], rgb[2], 1], i * 4);
    m.setVerticesData(VertexBuffer.ColorKind, colours);
  }
  const mesh = Mesh.MergeMeshes([handle, shaft], true, true)!;
  const mat = new StandardMaterial("wand-placeholder", scene);
  mat.diffuseColor = Color3.White();
  mat.specularColor = Color3.Black();
  mesh.useVertexColors = true;
  mesh.material = mat;
  return mesh;
}
