// LOD for kitted cells (docs/art/castle-kit.md "glTF conventions"): a node
// `<cell>.<material>_lod1` with `extras.kit_lod = { base, distance }` is
// wired to its base with Babylon's `addLODLevel` (so the LOD mesh is never
// drawn on its own and the base stays the one that is culled), and drawn
// instead of the base from `distance` on, measured from the camera to the
// base's bounding box (Babylon measures to the bounding sphere's centre,
// which in a 68 m corridor is far from where the player stands, and
// allocates a vector per mesh per frame). Low draws LOD1 everywhere.
//
// The choice runs in the scene's LOD selector: no allocation, a WeakMap
// lookup per active mesh; meshes without kit LOD keep Babylon's own choice.
//
// Castle kit M3 + M4 (docs/art/castle-kit.md), the same selector:
// - groups: a group's LOD1 (`<cell>.<material>.<group>_lod1`: the Grand
//   Staircase's portrait bands beyond one storey, the candle groups beyond
//   40 m) is wired like any LOD1, by its `kit_lod.base`;
// - candle groups (castle/candles.ts): at Low only the nearest group of a
//   cell is drawn.

import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { Scene } from "@babylonjs/core/scene";
import { kitAnimOf, kitLodOf } from "./nodes";
import type { KitCandles } from "./candles";

interface Level {
  /** The LOD1 mesh, or null for a candle group without LOD1. */
  lod: Mesh | null;
  distance: number;
  /** A candle group's base (at Low only its cell's nearest is drawn). */
  moving: boolean;
}

/** Distance from `p` to the world-space box (0 inside). */
export function distanceToBox(p: Vector3, min: Vector3, max: Vector3): number {
  const dx = p.x < min.x ? min.x - p.x : p.x > max.x ? p.x - max.x : 0;
  const dy = p.y < min.y ? min.y - p.y : p.y > max.y ? p.y - max.y : 0;
  const dz = p.z < min.z ? min.z - p.z : p.z > max.z ? p.z - max.z : 0;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

/**
 * Whether `p` is beyond `limit` of the world-space box. The per-frame form of
 * `distanceToBox`: a boolean result, where a returned double is boxed as a
 * heap number on every call the engine does not inline (review M3+M4: the
 * selector allocated 10-60 B a frame through it).
 */
export function beyondBox(p: Vector3, min: Vector3, max: Vector3, limit: number): boolean {
  const dx = p.x < min.x ? min.x - p.x : p.x > max.x ? p.x - max.x : 0;
  const dy = p.y < min.y ? min.y - p.y : p.y > max.y ? p.y - max.y : 0;
  const dz = p.z < min.z ? min.z - p.z : p.z > max.z ? p.z - max.z : 0;
  return dx * dx + dy * dy + dz * dz > limit * limit;
}

export class KitLods {
  private readonly levels = new WeakMap<AbstractMesh, Level>();
  private forced = false;
  private installed = false;
  private wired = 0;

  constructor(
    private readonly scene: Scene,
    /** The candle groups (Low draws only each cell's nearest); optional. */
    private readonly candles: KitCandles | null = null,
  ) {}

  /** LOD pairs wired so far (all cells, loaded or since unloaded). */
  get count(): number {
    return this.wired;
  }

  /** Low: draw LOD1 wherever a cell has one, one candle group a cell. */
  setForced(on: boolean): void {
    this.forced = on;
    this.candles?.setLow(on);
  }

  get isForced(): boolean {
    return this.forced;
  }

  /** Wires the `_lod1` nodes and candle groups among a freshly loaded cell's meshes. Returns how many LOD pairs. */
  wire(meshes: readonly AbstractMesh[]): number {
    let n = 0;
    let any = false;
    for (const m of meshes) {
      const info = kitLodOf(m);
      if (!info) continue;
      const base = meshes.find((b) => b.name === info.base);
      if (!(base instanceof Mesh) || !(m instanceof Mesh) || base === m || this.levels.get(base)?.lod) {
        console.warn(`[castle] ${m.name}: no base mesh ${JSON.stringify(info.base)} for its LOD`);
        continue;
      }
      base.addLODLevel(info.distance, m);
      this.levels.set(base, { lod: m, distance: info.distance, moving: kitAnimOf(base) !== null });
      n++;
    }
    for (const m of meshes) {
      if (this.levels.has(m) || kitLodOf(m)) continue;
      if (kitAnimOf(m) === null) continue;
      this.levels.set(m, { lod: null, distance: Infinity, moving: true });
      any = true;
    }
    if ((n > 0 || any) && !this.installed) {
      this.installed = true;
      this.scene.customLODSelector = this.select;
    }
    this.wired += n;
    return n;
  }

  /** The mesh to draw for `mesh` (the scene's LOD selector); null draws nothing. */
  readonly select = (mesh: AbstractMesh, camera: Camera): AbstractMesh | null => {
    const level = this.levels.get(mesh);
    if (!level) return mesh.getLOD(camera);
    if (this.forced) {
      if (level.moving && this.candles && !this.candles.drawnAtLow(mesh)) return null;
      return level.lod ? this.useLod(mesh, level.lod) : mesh;
    }
    if (!level.lod) return mesh;
    const box = mesh.getBoundingInfo().boundingBox;
    if (!beyondBox(camera.globalPosition, box.minimumWorld, box.maximumWorld, level.distance)) return mesh;
    return this.useLod(mesh, level.lod);
  };

  /** What Babylon's own getLOD does for the level it picks. */
  private useLod(mesh: AbstractMesh, lod: Mesh): Mesh {
    lod._preActivate();
    lod._updateSubMeshesBoundingInfo(mesh.worldMatrixFromCache);
    return lod;
  }

  dispose(): void {
    if (this.installed && this.scene.customLODSelector === this.select) {
      (this.scene as { customLODSelector: unknown }).customLODSelector = undefined;
    }
    this.installed = false;
  }
}
