// Streams world cells (one .glb each, see client/public/assets/world/manifest.json)
// in and out by distance from the player. Loading is a few cells at a time,
// nearest first; disposing a cell frees its meshes, materials, textures and GPU
// buffers through its AssetContainer.
//
// Castle kit (docs/art/castle-kit.md "glTF conventions", "The client side"):
// - Materials the shared library knows (castle/materials.ts) are taken from
//   it instead of converted per cell; the cell holds a reference on each
//   until it is unloaded.
// - A kitted cell's camera collider (`<cell>.collider`) is never drawn; the
//   camera ray-casts only it in that cell (`cameraMeshes`), and every mesh
//   of the cells that have none, as before.
// - `_lod1` nodes are wired to their bases (castle/lod.ts).
//
// Cell visibility (castle kit M2; castle/visibility.ts): a
// resident cell outside the visible set is disabled at its root (drawn
// meshes, castle_props, collider) and its camera meshes leave
// `cameraMeshes`, so the camera's rays skip it; it stays loaded, and the
// library keeps its references. A cell that loads while outside the set
// starts disabled.
//
// Castle kit M3 + M4: candle groups (`kit_anim`) are
// handed to castle/candles.ts, the only meshes of the castle that move (their
// world matrices are not frozen); group LOD1 nodes go to castle/lod.ts with
// the other LOD nodes.
//
// The House Cup's sand and cup banners (castle/cup.ts) are wired
// the same way: the sand scales with the standings, the banners show and hide.
//
// So are the Grand Staircase's swing nodes (castle/stairs.ts
// `KitSwings`: each moving flight, its meshes and any camera collider under
// it turned about the flight's pivot) and the Owl Loft's owl markers
// (castle/owls.ts).
//
// Cells the manifest marks
// `resident` (the grounds, the lake, the backdrop, the foliage) load with
// the castle and are never unloaded (cellplan.ts); the lake's `lake_water`
// is the shared toon water (castle/water.ts), the terrain's
// `grounds_terrain` the library's (castle/terrain.ts); the foliage's trees
// and rocks arrive as EXT_mesh_gpu_instancing (thin instances) and are not
// in the camera's rays (walk-through, like the castle's props).

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
// Node extras (the kit's collider role and LOD links) arrive as metadata.
import "@babylonjs/loaders/glTF/2.0/Extensions/ExtrasAsMetadata";
// The foliage cell's trees and rocks (thin instances).
import "@babylonjs/loaders/glTF/2.0/Extensions/EXT_mesh_gpu_instancing";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import type { AssetContainer } from "@babylonjs/core/assetContainer";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Node } from "@babylonjs/core/node";
import type { Scene } from "@babylonjs/core/scene";
import { planCells, type CellInfo, type StreamRadii } from "./cellplan";
import { toonifyContainer } from "./toon/convert";
import type { CastleLibrary } from "./castle/materials";
import type { KitLods } from "./castle/lod";
import type { KitCandles } from "./castle/candles";
import type { KitCup } from "./castle/cup";
import type { KitSwings } from "./castle/stairs";
import type { KitOwls } from "./castle/owls";
import type { KitEntrances } from "./castle/entrances";
import type { LakeWater } from "./castle/water";
import { isCameraCollider } from "./castle/nodes";
import type { FireField } from "./fire/field";
import { assetUrl } from "../assets";

export interface CellHooks {
  /** Called after a cell's meshes are added to the scene. */
  onLoaded(meshes: readonly AbstractMesh[]): void;
  /** Called just before a cell's meshes are disposed. */
  onUnloading(meshes: readonly AbstractMesh[]): void;
}

/** A cell that is loading or loaded (queued cells are only in `pending`). */
interface Resident {
  cell: CellInfo;
  /** Null while loading. */
  container: AssetContainer | null;
  meshes: AbstractMesh[];
  /** What the camera ray-casts in this cell: its colliders, or its drawn meshes when it has none. */
  cameraMeshes: AbstractMesh[];
  /** Shared library materials this cell holds a reference on (one entry per reference). */
  library: string[];
  /** The container's top-level nodes: enabling them shows or hides the whole cell. */
  roots: Node[];
  /** Whether the cell is in the visible set (its roots enabled, its camera meshes ray-cast). */
  shown: boolean;
}

/** The castle kit pieces the streamer works with (all optional: without them cells load as before). */
export interface CellKit {
  library?: CastleLibrary;
  lods?: KitLods;
  /** The floating candles (castle kit M3): moved once a frame by the game. */
  candles?: KitCandles;
  /** The House Cup's hourglass sand and cup banners in the Great Hall. */
  cup?: KitCup;
  /** The lake's shared toon water (`lake_water`). */
  water?: LakeWater;
  /** The Grand Staircase's swing nodes (turned by the staircase schedule). */
  swings?: KitSwings;
  /** The Owl Loft's owl markers (thin instances of the owl prop). */
  owls?: KitOwls;
  /** The hidden passages' entrances and the Wishing Room's door. */
  entrances?: KitEntrances;
  /** Animated fire (game/fire/): the cell's flames from its manifest `fire`, under its root. */
  fire?: Pick<FireField, "wireCell" | "remove">;
}

/** Cells the camera does not collide with (the grounds' trees and rocks are walk-through). */
const NO_CAMERA_CELLS: ReadonlySet<string> = new Set(["foliage"]);

const MAX_CONCURRENT_LOADS = 3;

export class CellStreamer {
  private readonly resident = new Map<string, Resident>();
  private loading = 0;
  private pending: CellInfo[] = [];
  private disposed = false;
  /** Every drawn mesh of the loaded cells (enabling, shadows, memory). */
  readonly meshes = new Set<AbstractMesh>();
  /** What the camera keeps in front of: kitted cells' colliders, every mesh of the other cells. */
  readonly cameraMeshes = new Set<AbstractMesh>();
  /** Material for the colliders: ray tests skip meshes without one. Never compiled (colliders are never drawn). */
  private colliderMaterial: StandardMaterial | null = null;
  private failed = new Set<string>();
  private settledWaiters: (() => void)[] = [];
  /** The cells shown (castle kit M2 `sees`), or null for every resident cell. */
  private visible: ReadonlySet<string> | null = null;

  constructor(
    private readonly scene: Scene,
    private readonly baseUrl: string,
    private readonly cells: readonly CellInfo[],
    private radii: StreamRadii,
    private readonly hooks: CellHooks,
    private readonly kit: CellKit = {},
  ) {}

  get loadedCount(): number {
    let n = 0;
    for (const r of this.resident.values()) if (r.container) n++;
    return n;
  }

  get loadingCount(): number {
    return this.loading + this.pending.length;
  }

  get totalCount(): number {
    return this.cells.length;
  }

  /** New load and unload distances (Low streams closer), applied from the next `update`. */
  setRadii(radii: StreamRadii): void {
    this.radii = radii;
  }

  /** Call regularly with the player position: queues loads and disposes far cells. */
  update(pos: readonly [number, number, number]): void {
    if (this.disposed) return;
    const residentIds = new Set(this.resident.keys());
    for (const id of this.failed) residentIds.add(id); // don't retry broken files every update
    const plan = planCells(this.cells, pos, residentIds, this.radii);
    for (const id of plan.unload) this.unload(id);
    // Re-plan the queue from scratch so it stays nearest-first as the player moves
    // and drops cells the player has walked away from before they started loading.
    this.pending = plan.load;
    this.pump();
    this.checkSettled();
  }

  /** Resolves once nothing is queued or loading (e.g. the cells around the spawn). */
  whenSettled(): Promise<void> {
    if (this.loadingCount === 0) return Promise.resolve();
    return new Promise((resolve) => this.settledWaiters.push(resolve));
  }

  private pump(): void {
    while (this.loading < MAX_CONCURRENT_LOADS && this.pending.length > 0) {
      const cell = this.pending.shift()!;
      this.resident.set(cell.id, { cell, container: null, meshes: [], cameraMeshes: [], library: [], roots: [], shown: true });
      void this.load(cell);
    }
  }

  private async load(cell: CellInfo): Promise<void> {
    this.loading++;
    let container: AssetContainer | null = null;
    try {
      // The library's list must be known before the cell is converted (cached after the first).
      const [c] = await Promise.all([LoadAssetContainerAsync(assetUrl(this.baseUrl + cell.file), this.scene, { pluginExtension: ".glb" }), this.kit.library?.ready()]);
      container = c;
    } catch (e) {
      console.error(`could not load world cell ${cell.id}`, e);
      this.failed.add(cell.id);
    }
    this.loading--;

    const entry = this.resident.get(cell.id);
    if (!container || this.disposed || !entry || entry.container) {
      // Failed, the streamer was disposed, or the cell was unloaded while loading.
      container?.dispose();
      if (entry && !entry.container) this.resident.delete(cell.id);
    } else {
      entry.container = container;
      // The toon look: PBR from glTF becomes toon StandardMaterials before anything compiles;
      // the kit's castle_* materials come from the shared library.
      const library = this.kit.library;
      const water = this.kit.water;
      const taken = entry.library;
      toonifyContainer(container, this.scene, undefined, {
        shared: (library || water) && {
          take: (name, placeholder) => {
            const w = water?.take(name) ?? null;
            if (w) return w;
            const m = library?.take(name, placeholder) ?? null;
            if (m) taken.push(name);
            return m;
          },
        },
      });
      const colliders: AbstractMesh[] = [];
      for (const m of container.meshes) if (m.getTotalVertices() > 0 && isCameraCollider(m)) colliders.push(m);
      for (const m of colliders) {
        // Never drawn; only the camera's rays see it (they skip meshes without a material).
        m.material = this.colliderMaterial ??= colliderMaterial(this.scene);
        m.isVisible = false;
        m.setEnabled(false);
      }
      entry.roots = [...container.meshes.filter((m) => !m.parent), ...container.transformNodes.filter((n) => !n.parent)];
      entry.shown = this.isShown(cell.id);
      if (!entry.shown) for (const r of entry.roots) r.setEnabled(false);
      container.addAllToScene();
      entry.meshes = container.meshes.filter((m) => m.getTotalVertices() > 0 && !colliders.includes(m));
      entry.cameraMeshes = NO_CAMERA_CELLS.has(cell.id) ? [] : colliders.length > 0 ? colliders : entry.meshes;
      for (const m of container.meshes) {
        m.isPickable = false;
        m.freezeWorldMatrix();
        m.doNotSyncBoundingInfo = true;
      }
      this.kit.lods?.wire(entry.meshes);
      this.kit.candles?.wire(cell.id, entry.meshes);
      this.kit.cup?.wire(cell.id, [...container.meshes, ...container.transformNodes]);
      this.kit.swings?.wire(cell.id, [...container.meshes, ...container.transformNodes]);
      this.kit.owls?.wire(cell.id, container.transformNodes);
      this.kit.entrances?.wire(cell.id, [...container.meshes, ...container.transformNodes]);
      this.kit.fire?.wireCell(cell.id, (cell as { fire?: unknown }).fire, entry.roots[0] ?? null, entry.meshes);
      for (const m of entry.meshes) this.meshes.add(m);
      if (entry.shown) for (const m of entry.cameraMeshes) this.cameraMeshes.add(m);
      this.hooks.onLoaded(entry.meshes);
    }
    this.pump();
    this.checkSettled();
  }

  private unload(id: string): void {
    const entry = this.resident.get(id);
    if (!entry) return;
    this.resident.delete(id);
    if (!entry.container) return; // still loading: load() sees it is gone and disposes it
    this.hooks.onUnloading(entry.meshes);
    this.kit.candles?.unwire(id);
    this.kit.cup?.unwire(id);
    this.kit.swings?.unwire(id);
    this.kit.owls?.unwire(id);
    this.kit.entrances?.unwire(id);
    this.kit.fire?.remove(id);
    for (const m of entry.meshes) this.meshes.delete(m);
    for (const m of entry.cameraMeshes) this.cameraMeshes.delete(m);
    entry.container.removeAllFromScene();
    entry.container.dispose(); // meshes, geometry, its own materials, textures
    // Shared materials are not the container's: give back this cell's references.
    for (const name of entry.library) this.kit.library?.release(name);
    entry.library.length = 0;
  }

  /**
   * Shows the resident cells in `cells` and disables the others (null shows
   * every one). Called when the visible set changes, never per frame; cells
   * loading later follow the same set.
   */
  setVisibleCells(cells: ReadonlySet<string> | null): void {
    this.visible = cells;
    for (const entry of this.resident.values()) {
      if (!entry.container) continue;
      const shown = this.isShown(entry.cell.id);
      if (shown === entry.shown) continue;
      entry.shown = shown;
      for (const r of entry.roots) r.setEnabled(shown);
      if (shown) for (const m of entry.cameraMeshes) this.cameraMeshes.add(m);
      else for (const m of entry.cameraMeshes) this.cameraMeshes.delete(m);
    }
  }

  private isShown(id: string): boolean {
    return this.visible === null || this.visible.has(id);
  }

  /** Loaded cells shown now, and loaded cells in all (the F3 overlay). */
  visibleCounts(): { shown: number; loaded: number } {
    let shown = 0;
    let loaded = 0;
    for (const e of this.resident.values()) {
      if (!e.container) continue;
      loaded++;
      if (e.shown) shown++;
    }
    return { shown, loaded };
  }

  /** Whether the loaded cell `id` is shown (in the visible set). */
  isCellShown(id: string): boolean {
    const e = this.resident.get(id);
    return !!e && !!e.container && e.shown;
  }

  /** Whether the loaded cell `id` has a camera collider (a kitted cell). */
  hasCollider(id: string): boolean {
    const e = this.resident.get(id);
    return !!e && e.cameraMeshes !== e.meshes && e.cameraMeshes.length > 0;
  }

  private checkSettled(): void {
    if (this.loadingCount > 0) return;
    const waiters = this.settledWaiters;
    this.settledWaiters = [];
    for (const w of waiters) w();
  }

  dispose(): void {
    this.disposed = true;
    for (const id of [...this.resident.keys()]) this.unload(id);
    this.pending = [];
    this.colliderMaterial?.dispose();
    this.colliderMaterial = null;
    this.checkSettled();
  }
}

function colliderMaterial(scene: Scene): StandardMaterial {
  const m = new StandardMaterial("castle_collider", scene);
  m.disableLighting = true;
  m.checkReadyOnlyOnce = true;
  return m;
}
