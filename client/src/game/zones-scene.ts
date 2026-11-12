// The scenes behind each zone (docs/architecture.md, "Zones"):
// the castle, streamed cell by cell, and dorm instances,
// loaded whole from the instance manifest. One Babylon scene serves both;
// switching swaps what is enabled and the lighting behind a short fade.
//
// Memory: a dorm is one small room (a few MB of geometry and textures plus
// its furniture), so while the player is in one the castle cells around the
// common room stay loaded but disabled (no draw calls), and going back out
// is instant. The dorm is disposed as soon as the player leaves it, so at
// most one instance is ever resident; `memoryReport` (logged on each switch)
// shows what is held.
//
// Instance cells are listed in `client/public/assets/world/instances.json`
// (`{ "<layout id>": { "cells": [...manifest entries...], "lights"?: [...] } }`,
// lights also accepted on each cell entry). A room missing from it may ship
// as one file, `client/public/assets/instances/<layout id>.glb`. When neither
// loads (art not generated yet) the room is drawn as a plain greybox shell
// from its layout, so dorms always work.
//
// Cell visibility (castle kit M2, docs/art/castle-kit.md "Cell visibility (`sees`)"): `place`
// takes the surface under the player on every place update and disables
// the resident castle cells outside its `sees` set (castle/visibility.ts),
// and points the torch pool at the visible cells' torches. Showing the
// castle (arriving from a dorm, the first spawn) places the spawn at once.
//
// The same place update tells whether the player stands outdoors
// (`SimWorld.outdoorsAt`: the grounds, the courtyard), which shows the far
// castle's stand-ins (castle/hlod.ts) and lengthens the view (scene.ts
// `fogRange`); rooms open to the sky are the ones `instances/school.json`
// marks `open_sky` (the Astronomy tower top, the paddock).

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import type { AssetContainer } from "@babylonjs/core/assetContainer";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { PointLight } from "@babylonjs/core/Lights/pointLight";
import type { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Scene } from "@babylonjs/core/scene";
import type { House, Vec3, Zone } from "../wasm/sim_wasm";
import { CellStreamer, type CellKit } from "./cells";
import { CellVisibility, type SeesSurface } from "./castle/visibility";
import { castsSunShadow } from "./castle/nodes";
import { ToonPlugin } from "./toon/material";
import { worldRamp } from "./toon/palette";
import { toonifyContainer } from "./toon/convert";
import { FoliageSystem, type FoliageKitInfo, type FoliageRows } from "./castle/foliage";
import { toonPointIntensity, torchesOf } from "./toon/torches";
import { fireOf } from "./fire/field";
import type { FireData } from "./fire/data";
import { roomFill } from "./toon/lighting";
import type { CellInfo, StreamRadii } from "./cellplan";
import { loadInstanceWorld, type LayoutSurface, type WorldData } from "./world";
import type { WorldScene } from "./scene";
import { Seats } from "./castle/seats";
import { HlodSet, type HlodManifest } from "./castle/hlod";
import { parseInstanceManifest, parseOpenSky, type InstanceAmbient, type InstanceEntry, type InstanceLight } from "./zones";
import "./zones.css";
import { assetUrl } from "../assets";

/** Candle glow (docs/art/style-guide.md). */
const CANDLE = "#ffc877";
/**
 * Lights a room may use, strongest first. The room's own materials are raised
 * to take them all (plus the hemisphere), and so is the character material
 * (characters/renderer.ts).
 */
const MAX_ROOM_LIGHTS = 6;
/** Counts rooms built, for their fire set keys. */
let roomFires = 0;
const FADE_MS = 220;
/** How long to wait for the castle cells around a spawn before showing it anyway (ms). */
const CASTLE_SETTLE_TIMEOUT_MS = 20_000;

export interface ZoneScenesOptions {
  view: WorldScene;
  /** `assets/world/` (a logical path, src/assets.ts): castle cells, instance cells and both manifests. */
  worldBase: string;
  castleCells: readonly CellInfo[];
  castleWorld: WorldData;
  house: House;
  radii: StreamRadii;
  /** The castle kit's shared materials and LOD (castle/); optional. */
  kit?: CellKit;
  /** The far castle's stand-ins (the manifest's `hlod`), loaded with the castle; none without. */
  hlod?: HlodManifest | null;
  /** Vegetation look A: the foliage kit (the manifest's `foliage_kit`); no trees or hedges without. */
  foliageKit?: FoliageKitInfo | null;
}

/** A zone scene loaded but not shown yet (`activate`), or thrown away (`dispose`). */
export interface PreparedZone {
  world: WorldData;
  activate(): void;
  dispose(): void;
}

export class ZoneScenes {
  /** The castle's cells. While the player is in a dorm they stay loaded, disabled. */
  readonly streamer: CellStreamer;
  /** Which castle cells are shown from where the player stands (castle kit M2 `sees`). */
  readonly visibility: CellVisibility;
  private readonly scene: Scene;
  private castleHidden = false;
  private room: DormShell | null = null;
  private current: WorldData;
  private manifest: Promise<Map<string, InstanceEntry>> | null = null;
  private readonly fade: HTMLElement;
  private disposed = false;
  private readonly outdoor: OutdoorLook;
  /** The far castle (null without an `hlod` in the manifest). */
  readonly hlod: HlodSet | null;
  /** Vegetation look A: every tree and hedge (null without a foliage kit in the manifest). */
  readonly foliage: FoliageSystem | null;
  /** Whether the last place update stood outdoors (in the castle zone). */
  private outdoorsHere = false;
  /** Instance rooms open to the sky (`instances/school.json` `open_sky`), fetched once. */
  private openSkyRooms: Promise<ReadonlySet<string>> | null = null;
  private openSkyKnown: ReadonlySet<string> = new Set();

  constructor(private readonly opts: ZoneScenesOptions) {
    this.scene = opts.view.scene;
    this.current = opts.castleWorld;
    this.outdoor = captureOutdoor(this.scene);
    this.visibility = new CellVisibility(opts.castleCells, opts.castleWorld.layoutSurfaces as unknown as SeesSurface[]);
    this.streamer = new CellStreamer(this.scene, opts.worldBase, opts.castleCells, opts.radii, {
      onLoaded: (meshes) => {
        // Castle kit M3: the enchanted ceiling and the floating candles cast no sun shadow (castle/nodes.ts).
        opts.view.addStatic(meshes.filter(castsSunShadow));
        if (this.castleHidden) for (const m of meshes) m.setEnabled(false);
        this.foliage?.invalidate();
      },
      onUnloading: (meshes) => {
        opts.view.removeStatic(meshes);
        this.foliage?.invalidate();
      },
    }, opts.kit);
    this.hlod = opts.hlod ? new HlodSet(this.scene, opts.worldBase, opts.hlod, opts.kit?.library) : null;
    // Vegetation look A: the trees and hedges cast the sun's shadow on High like the cells' meshes.
    this.foliage = opts.foliageKit
      ? new FoliageSystem(this.scene, opts.worldBase, opts.foliageKit, opts.kit?.library, (meshes) => opts.view.addStatic(meshes))
      : null;
    this.foliage?.setCastle(opts.castleCells);
    this.fade = document.createElement("div");
    this.fade.className = "zone-fade";
    this.fade.setAttribute("aria-hidden", "true");
    document.body.append(this.fade);
  }

  /** The world (layout, prediction) of the zone shown. */
  get world(): WorldData {
    return this.current;
  }

  get inCastle(): boolean {
    return this.room === null;
  }

  /**
   * Meshes the camera keeps in front of: in the castle the camera colliders
   * of kitted cells and every mesh of the others (cells.ts).
   */
  get walls(): Iterable<AbstractMesh> {
    return this.room ? this.room.meshes : this.streamer.cameraMeshes;
  }

  /** Whether the player stands outdoors in the castle zone (the grounds, the courtyard); false in a room. */
  get outdoors(): boolean {
    return !this.room && this.outdoorsHere;
  }

  /** Whether the instance room `layout` is open to the sky (`school.json` `open_sky`; known once a room has loaded). */
  isOpenSky(layout: string): boolean {
    return this.openSkyKnown.has(layout);
  }

  /**
   * A place update in the castle (game.ts, four times a second): the
   * surface under the player, or null in the air, and where the player
   * stands. Shows the cells it sees, and the far castle from outdoors.
   * On a broom (`flying`) the player is outdoors wherever it is
   * (brooms only fly in the open sky; `outdoorsAt` is false in the air).
   */
  place(surfaceId: string | null | undefined, x?: number, z?: number, y?: number, flying = false): void {
    if (this.room) return;
    if (this.visibility.place(surfaceId, x, z)) this.applyVisibility();
    // In the air (no surface) the last answer stays, as for the visible set.
    if (flying) this.outdoorsHere = true;
    else if (surfaceId && x !== undefined && z !== undefined && y !== undefined) this.outdoorsHere = this.opts.castleWorld.sim.outdoorsAt(x, z, y);
    this.applyHlod();
  }

  /** The far castle's chunks for where the player stands and what is loaded (castle/hlod.ts). */
  private applyHlod(): void {
    this.hlod?.update(this.outdoors, (cell) => this.streamer.isCellShown(cell));
  }

  private applyVisibility(): void {
    const set = this.visibility.visible;
    this.streamer.setVisibleCells(set);
    this.foliage?.invalidate();
    torchesOf(this.scene)?.setVisibleCells(set);
  }

  /** Vegetation look A: whether a planting's owner (a castle cell, or "#room") is shown now. */
  readonly foliageShown = (owner: string): boolean =>
    owner === "#room" ? this.room !== null : this.room === null && this.streamer.isCellShown(owner);

  /** Streams castle cells around the player (call regularly; nothing happens in a dorm). */
  update(pos: readonly [number, number, number]): void {
    if (!this.room) this.streamer.update(pos);
  }

  /**
   * Loads `zone` around `spawn` without showing it. The castle loads its
   * cells hidden while a dorm is still shown; a dorm loads its layout, shell
   * and lights disabled.
   */
  async prepare(zone: Zone, spawn: Vec3): Promise<PreparedZone> {
    if (zone.kind === "castle") {
      // The spawn's set at once (a teleport or a zone change), so cells load into it.
      this.visibility.reset();
      const sim = this.opts.castleWorld.sim;
      const at = sim.surfaceIdAt(spawn[0], spawn[2], spawn[1]);
      this.visibility.place(at, spawn[0], spawn[2]);
      this.outdoorsHere = !!at && sim.outdoorsAt(spawn[0], spawn[2], spawn[1]);
      this.applyVisibility();
      this.streamer.update(spawn);
      await Promise.race([this.streamer.whenSettled(), new Promise((r) => setTimeout(r, CASTLE_SETTLE_TIMEOUT_MS))]);
      const world = this.opts.castleWorld;
      return { world, activate: () => this.showCastle(), dispose: () => undefined };
    }
    const [world, entry, openSky] = await Promise.all([loadInstanceWorld(zone.layout, this.opts.house), this.instanceEntry(zone.layout), this.openSky()]);
    let shell: DormShell;
    try {
      // Not in the manifest: try a single exported room file (`assets/instances/<layout>.glb`).
      const cells = entry ?? { cells: [{ id: zone.layout, file: `../instances/${zone.layout}.glb`, bounds: { min: [0, 0, 0], max: [0, 0, 0] }, level: 0 }], lights: [] };
      shell = await DormShell.load(this.scene, this.opts.worldBase, cells, world.layoutSurfaces, zone.kind === "dorm" ? zone.house : this.opts.house);
    } catch (e) {
      world.sim.free();
      throw e;
    }
    world.seats = Seats.fromRoomSeats(entry?.seats);
    // The Gathering Room's rugs and cushions (its cells' floor_seats, as the common rooms').
    const floor = entry ? Seats.floorFromCells(entry.cells) : null;
    world.floorSeats = floor && floor.count > 0 ? floor : null;
    if (this.disposed) {
      shell.dispose();
      world.sim.free();
      throw new Error("disposed");
    }
    return {
      world,
      activate: () => this.showRoom(shell, world, openSky.has(zone.layout), zone.layout, entry),
      dispose: () => {
        shell.dispose();
        world.sim.free();
      },
    };
  }

  private showCastle(): void {
    this.dropRoom();
    if (this.castleHidden) {
      for (const m of this.streamer.meshes) m.setEnabled(true);
      this.castleHidden = false;
    }
    applyOutdoor(this.scene, this.outdoor);
    this.foliage?.setRoom(null);
    this.current = this.opts.castleWorld;
    this.applyHlod();
    this.logMemory("castle");
  }

  private showRoom(shell: DormShell, world: WorldData, openSky = false, layout: string | null = null, entry?: InstanceEntry): void {
    this.dropRoom();
    if (!this.castleHidden) {
      for (const m of this.streamer.meshes) m.setEnabled(false);
      this.castleHidden = true;
    }
    applyIndoor(this.scene, shell.ambient);
    if (openSky) this.scene.getMeshByName("sky")?.setEnabled(true);
    shell.show();
    this.hlod?.update(false, () => false);
    // Vegetation look A: the room's trees and hedges (its cells' `foliage`).
    if (this.foliage) {
      const rows = (entry?.cells ?? []).map((c) => c.foliage).filter((f): f is FoliageRows => !!f);
      this.foliage.setRoomRows(new Map(layout ? [[layout, rows]] : []));
      this.foliage.setRoom(rows.length && layout ? layout : null);
    }
    // Its floors take the nearest characters' shadows (Medium, High).
    this.opts.view.addShadowReceivers(shell.meshes);
    this.room = shell;
    this.current = world;
    this.logMemory("dorm");
  }

  private dropRoom(): void {
    if (!this.room) return;
    this.opts.view.removeShadowReceivers(this.room.meshes);
    this.room.dispose();
    // The dorm's world is not the castle's; free its wasm memory.
    if (this.current !== this.opts.castleWorld) this.current.sim.free();
    this.room = null;
  }

  /** The rooms `instances/school.json` marks `open_sky` (fetched once; none when it cannot be read). */
  private openSky(): Promise<ReadonlySet<string>> {
    this.openSkyRooms ??= fetch(assetUrl(`${this.opts.worldBase}../instances/school.json`))
      .then(async (r) => (r.ok ? parseOpenSky(await r.json()) : new Set<string>()))
      .catch(() => new Set<string>())
      .then((set) => {
        // An empty answer is not kept for good: the rooms may be generated while the page is open.
        if (set.size === 0) this.openSkyRooms = null;
        this.openSkyKnown = set;
        return set;
      });
    return this.openSkyRooms;
  }

  private instanceEntry(layoutId: string): Promise<InstanceEntry | undefined> {
    this.manifest ??= fetch(assetUrl(`${this.opts.worldBase}instances.json`))
      .then(async (r) => (r.ok ? parseInstanceManifest(await r.json()) : new Map<string, InstanceEntry>()))
      .catch(() => new Map<string, InstanceEntry>());
    // A missing manifest is not cached for good: art may be generated while the page is open.
    return this.manifest.then((m) => {
      if (m.size === 0) this.manifest = null;
      return m.get(layoutId);
    });
  }

  /** Fades the view to black (resolves when it is). */
  fadeOut(caption: string): Promise<void> {
    this.fade.textContent = caption;
    this.fade.classList.add("shown");
    return new Promise((r) => setTimeout(r, reducedMotion() ? 0 : FADE_MS));
  }

  fadeIn(): void {
    this.fade.classList.remove("shown");
  }

  /** Approximate GPU bytes held by the castle cells and the dorm (geometry + textures). */
  memoryReport(): { castleBytes: number; roomBytes: number } {
    return { castleBytes: gpuBytes(this.streamer.meshes), roomBytes: this.room ? gpuBytes(this.room.meshes) : 0 };
  }

  private logMemory(shown: string): void {
    const m = this.memoryReport();
    const mb = (b: number) => (b / 1048576).toFixed(2);
    console.info(`[zones] showing ${shown}: castle cells ${mb(m.castleBytes)} MB${this.castleHidden ? " (hidden)" : ""}, dorm ${mb(m.roomBytes)} MB`);
  }

  /** Disposes every scene and frees both worlds' wasm memory (this owns the castle world too). */
  dispose(): void {
    this.disposed = true;
    this.dropRoom();
    this.hlod?.dispose();
    this.foliage?.dispose();
    this.streamer.dispose();
    this.opts.castleWorld.sim.free();
    this.fade.remove();
  }
}

function reducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// ------------------------------------------------------------------ lighting

interface OutdoorLook {
  clearColor: Color4;
  fogMode: number;
  hemiIntensity: number;
  hemiSky: Color3;
  hemiGround: Color3;
}

/** The fill of a room whose manifest gives none. */
const DEFAULT_ROOM_AMBIENT: InstanceAmbient = { sky: "#8c96a8", ground: "#4a3a2c", intensity: 0.3 };

function captureOutdoor(scene: Scene): OutdoorLook {
  const hemi = scene.getLightByName("sky") as HemisphericLight | null;
  return {
    clearColor: scene.clearColor.clone(),
    fogMode: scene.fogMode,
    hemiIntensity: hemi?.intensity ?? 0.42,
    hemiSky: hemi?.diffuse.clone() ?? Color3.White(),
    hemiGround: hemi?.groundColor.clone() ?? Color3.Black(),
  };
}

/** The castle's sky, sun and fog (scene.ts). */
function applyOutdoor(scene: Scene, look: OutdoorLook): void {
  scene.clearColor = look.clearColor.clone();
  scene.fogMode = look.fogMode;
  const hemi = scene.getLightByName("sky") as HemisphericLight | null;
  if (hemi) {
    hemi.intensity = look.hemiIntensity;
    hemi.diffuse = look.hemiSky.clone();
    hemi.groundColor = look.hemiGround.clone();
  }
  scene.getLightByName("sun")?.setEnabled(true);
  scene.getMeshByName("sky")?.setEnabled(true);
  torchesOf(scene)?.setIndoor(false);
}

/** A room lit from inside: no sky, sun or fog, the room's fill (manifest `ambient`), its own lights. */
function applyIndoor(scene: Scene, ambient: InstanceAmbient = DEFAULT_ROOM_AMBIENT): void {
  scene.clearColor = new Color4(0.03, 0.025, 0.03, 1);
  scene.fogMode = 0; // Scene.FOGMODE_NONE
  const hemi = scene.getLightByName("sky") as HemisphericLight | null;
  if (hemi) {
    const s = Color3.FromHexString(ambient.sky);
    const g = Color3.FromHexString(ambient.ground);
    const fill = roomFill([s.r, s.g, s.b], [g.r, g.g, g.b], ambient.intensity);
    hemi.intensity = fill.intensity;
    hemi.diffuse = Color3.FromArray(fill.sky);
    hemi.groundColor = Color3.FromArray(fill.ground);
  }
  scene.getLightByName("sun")?.setEnabled(false);
  scene.getMeshByName("sky")?.setEnabled(false);
  torchesOf(scene)?.setIndoor(true);
}

// ------------------------------------------------------------------ dorm shell

/** A dorm room's walls, floor and lights. */
class DormShell {
  readonly meshes: AbstractMesh[] = [];
  /** The room's fill light from the manifest, if it gives one. */
  ambient: InstanceAmbient | undefined;
  private readonly lights: PointLight[] = [];
  /** Animated fire: per light, the seed of the fire it flickers with (-1: steady). */
  private readonly flickers: number[] = [];
  /** A lake window's light: per light, the phase of its slow swell (-1: none; game/fire/flicker.ts `ripple`). */
  private readonly ripples: number[] = [];
  /** The room's flames (manifest `fire`), drawn while it is shown. */
  private fire: FireData | undefined;
  /** Its fire set's key (each shell its own: a new room may show before the old one goes). */
  private readonly fireKey = `room-${++roomFires}`;
  private readonly containers: AssetContainer[] = [];
  private readonly owned: { dispose(): void }[] = [];

  private constructor(private readonly scene: Scene) {}

  static async load(scene: Scene, base: string, entry: InstanceEntry | undefined, surfaces: readonly LayoutSurface[], house: House): Promise<DormShell> {
    const shell = new DormShell(scene);
    shell.ambient = entry?.ambient;
    shell.fire = entry?.fire;
    let loaded = false;
    if (entry && entry.cells.length > 0) {
      try {
        for (const cell of entry.cells) {
          const c = await LoadAssetContainerAsync(assetUrl(base + cell.file), scene, { pluginExtension: ".glb" });
          toonifyContainer(c, scene, MAX_ROOM_LIGHTS + 2);
          shell.containers.push(c);
        }
        loaded = true;
      } catch (e) {
        console.info(`dorm art not available (${entry.cells.map((c) => c.file).join(", ")}); drawing the greybox shell`, e instanceof Error ? e.message.split("\n")[0] : e);
        for (const c of shell.containers) c.dispose();
        shell.containers.length = 0;
      }
    }
    if (loaded) {
      for (const c of shell.containers) shell.meshes.push(...c.meshes.filter((m) => m.getTotalVertices() > 0));
    } else {
      shell.buildGreybox(surfaces, house);
    }
    shell.addLights(entry?.lights, surfaces);
    for (const m of shell.meshes) {
      m.isPickable = false;
      m.setEnabled(false);
      const mat = m.material as { maxSimultaneousLights?: number } | null;
      // The room's lamps, the sky fill and the character shadow light.
      if (mat && typeof mat.maxSimultaneousLights === "number") mat.maxSimultaneousLights = MAX_ROOM_LIGHTS + 2;
    }
    for (const l of shell.lights) l.setEnabled(false);
    return shell;
  }

  show(): void {
    for (const c of this.containers) c.addAllToScene();
    for (const m of this.meshes) {
      m.setEnabled(true);
      m.freezeWorldMatrix();
      m.doNotSyncBoundingInfo = true;
    }
    for (const l of this.lights) l.setEnabled(true);
    // Animated fire: the room's flames, its lights at a fire flickering with them, a lake window's light swelling.
    const fire = fireOf(this.scene);
    if (fire) {
      fire.add(this.fireKey, this.fire ?? null, null, this.meshes);
      fire.setLights(
        this.fireKey,
        this.lights.flatMap((light, i) =>
          this.flickers[i]! >= 0 ? [{ light, seed: this.flickers[i]! }] : this.ripples[i]! >= 0 ? [{ light, seed: this.ripples[i]!, ripple: true }] : [],
        ),
      );
    }
  }

  /** Lights from the manifest, else a candle-coloured lamp under each room's ceiling. */
  private addLights(lights: readonly InstanceLight[] | undefined, surfaces: readonly LayoutSurface[]): void {
    const list: InstanceLight[] = lights?.length
      ? [...lights]
      : surfaces.map((s) => {
          const y = (s.y ?? 0) + Math.max(2.2, (s.ceiling ?? 3) - 1);
          const span = Math.hypot(s.max[0] - s.min[0], s.max[1] - s.min[1]);
          return { pos: [(s.min[0] + s.max[0]) / 2, y, (s.min[1] + s.max[1]) / 2] as Vec3, range: span * 1.4, intensity: 6 };
        });
    const strength = (l: InstanceLight) => (l.intensity ?? 1) * (l.range ?? 14);
    list.sort((a, b) => strength(b) - strength(a));
    for (const [i, l] of list.slice(0, MAX_ROOM_LIGHTS).entries()) {
      const light = new PointLight(`dorm-light-${i}`, new Vector3(...l.pos), this.scene);
      light.diffuse = Color3.FromHexString(l.color ?? CANDLE);
      light.specular = Color3.Black(); // toon highlights come from the ramp rows
      light.intensity = toonPointIntensity(l.intensity);
      light.range = l.range ?? 14;
      this.lights.push(light);
      this.flickers.push(l.flicker ?? -1);
      this.ripples.push(l.ripple ?? -1);
    }
  }

  /** Floor, walls and ceiling for each room surface: the shell before its art exists. */
  private buildGreybox(surfaces: readonly LayoutSurface[], house: House): void {
    const floorMat = flat(this.scene, "dorm-floor", "#6b4a32");
    const wallMat = flat(this.scene, "dorm-wall", HOUSE_WALLS[house] ?? "#8a7f70");
    const ceilMat = flat(this.scene, "dorm-ceiling", "#3a2c22");
    this.owned.push(floorMat, wallMat, ceilMat);
    const t = 0.2;
    for (const s of surfaces) {
      const y = s.y ?? 0;
      const h = (s.ceiling ?? 3.2) - y;
      const w = s.max[0] - s.min[0];
      const d = s.max[1] - s.min[1];
      const cx = (s.min[0] + s.max[0]) / 2;
      const cz = (s.min[1] + s.max[1]) / 2;
      const box = (name: string, sx: number, sy: number, sz: number, x: number, by: number, z: number) => {
        const m = CreateBox(name, { width: sx, height: sy, depth: sz }, this.scene);
        m.position.set(x, by + sy / 2, z);
        return m;
      };
      const floor = box(`${s.id}-floor`, w, t, d, cx, y - t, cz);
      floor.material = floorMat;
      const walls = Mesh.MergeMeshes(
        [
          box("n", w + 2 * t, h, t, cx, y, s.min[1] - t / 2),
          box("s", w + 2 * t, h, t, cx, y, s.max[1] + t / 2),
          box("w", t, h, d, s.min[0] - t / 2, y, cz),
          box("e", t, h, d, s.max[0] + t / 2, y, cz),
        ],
        true,
      )!;
      walls.name = `${s.id}-walls`;
      walls.material = wallMat;
      const ceiling = box(`${s.id}-ceiling`, w + 2 * t, t, d + 2 * t, cx, y + h, cz);
      ceiling.material = ceilMat;
      this.meshes.push(floor, walls, ceiling);
    }
  }

  dispose(): void {
    const fire = fireOf(this.scene);
    fire?.remove(this.fireKey);
    fire?.clearLights(this.fireKey);
    for (const l of this.lights) l.dispose();
    for (const c of this.containers) {
      c.removeAllFromScene();
      c.dispose();
    }
    if (this.containers.length === 0) for (const m of this.meshes) m.dispose();
    for (const o of this.owned) o.dispose();
    this.meshes.length = 0;
  }
}

/** Greybox wall tints per house dorm: red, warm ochre, blue, green-grey stone. */
const HOUSE_WALLS: Record<House, string> = {
  roarwing: "#7a3a30",
  burrowsby: "#8c7448",
  skytalon: "#4a5a78",
  serpentyne: "#3e5248",
};

function flat(scene: Scene, name: string, hex: string): StandardMaterial {
  const m = new StandardMaterial(name, scene);
  m.diffuseColor = Color3.FromHexString(hex);
  m.specularColor = Color3.Black();
  new ToonPlugin(m, worldRamp(name.replace(/^dorm-/, "")));
  return m;
}

// ------------------------------------------------------------------ memory

/**
 * Approximate GPU memory behind `meshes`: vertex and index buffers plus
 * textures (with mips), each shared resource counted once.
 */
export function gpuBytes(meshes: Iterable<AbstractMesh>): number {
  const geometries = new Set<object>();
  const textures = new Set<object>();
  let bytes = 0;
  for (const m of meshes) {
    const geo = (m as Mesh).geometry;
    if (geo && !geometries.has(geo)) {
      geometries.add(geo);
      const n = geo.getTotalVertices();
      for (const vb of Object.values(geo.getVertexBuffers() ?? {})) bytes += n * vb.getSize() * 4;
      bytes += geo.getTotalIndices() * 4;
    }
    for (const tex of m.material?.getActiveTextures() ?? []) {
      if (textures.has(tex)) continue;
      textures.add(tex);
      const size = tex.getSize();
      bytes += size.width * size.height * 4 * (4 / 3);
    }
  }
  return bytes;
}
