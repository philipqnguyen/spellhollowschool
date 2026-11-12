// Vegetation look A (docs/architecture.md "Rendering"; tools/blender/world/foliage.py):
// every tree and hedge of the world is a planting of one species of the foliage kit,
// `world/foliage_kit.glb`: one mesh per species and LOD (`foliage.<species>`,
// `foliage.<species>_lod<N>`), all on the castle library's `castle_foliage` (the painted atlas,
// alpha mask, double-sided with shared normals). The cells list their plantings in the
// manifests (`foliage`: the species used and one row per planting, [species index, ex, ey, ez,
// t] in glTF axes): the castle's cells in `world/manifest.json`, the instance rooms' in
// `world/instances.json`. This draws them as thin instances: per species and LOD one draw,
// whatever the count.
//
// - LOD per planting, by its size on screen (style guide section 9): its species' size (a
//   tree's height, a hedge segment's length) times its scale, over the distance times the
//   field of view: LOD1 below 25 % of the screen's height, LOD2 below 10 %, LOD3 (a tree's
//   impostor, a hedge's bare core) below 4 %; never closer than LOD_MIN_M; Low starts at
//   LOD1; a cell may ask for a coarsest-first `min_lod` (the backdrop forest: impostors only).
// - Shown with their cell: in the castle a planting draws while its cell is loaded and in the
//   visible set (`shown`); in a room, the room's.
// - Plantings beyond CULL_NEAR_M and outside the camera's frustum are skipped; nearer ones
//   always draw (their shadows fall into view on High).
// - The buffers are rebuilt when the camera has moved REBUCKET_M or turned, or the set changed,
//   and every frame while a crossfade runs.
// - LOD changes crossfade (no pop): for LOD_FADE_S the planting draws at both its old and its new
//   LOD, the two cut by complementary 4 x 4 screen-door dithers (a per-instance `foliageFade`:
//   t in the new LOD keeps the pixels whose threshold is below t, -t in the old one keeps the
//   rest), so every pixel draws exactly one of them; a LOD change needs the distance past the
//   threshold by LOD_HYSTERESIS, so a camera resting on it does not flicker. The overlap is the
//   only extra cost: the same draws, a few more instances for 0.3 s.
// - Wind (`FoliageWind`): a vertex offset growing with the height over the planting's foot
//   (squared), a slow sway per planting phased by where it stands and a small leaf flutter
//   above 1.5 m, in the vertex shader (no work per tree on the CPU). The shadow pass does not
//   sway (Babylon's depth shader takes no material plugin): the offsets are a few centimetres.

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import { BoundingInfo } from "@babylonjs/core/Culling/boundingInfo";
import { Frustum } from "@babylonjs/core/Maths/math.frustum";
import type { Plane } from "@babylonjs/core/Maths/math.plane";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { MaterialPluginBase } from "@babylonjs/core/Materials/materialPluginBase";
import type { Material } from "@babylonjs/core/Materials/material";
import type { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import type { UniformBuffer } from "@babylonjs/core/Materials/uniformBuffer";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import { toonifyContainer, WORLD_MAX_LIGHTS, type SharedMaterials } from "../toon/convert";
import type { CellInfo } from "../cellplan";
import { assetUrl } from "../../assets";

/** The world manifest's `foliage_kit` (tools/blender/world/foliage.py `kit_manifest`). */
export interface FoliageKitInfo {
  file: string;
  lods: number;
  species: Record<string, { size: number; tris: number[] }>;
}

/** A cell's `foliage` (foliage.py `manifest_rows`). */
export interface FoliageRows {
  species: string[];
  plants: number[][];
  min_lod?: number;
}

/** The kit's node name of `species` at `lod`. */
export function nodeName(species: string, lod: number): string {
  return `foliage.${species}${lod ? `_lod${lod}` : ""}`;
}

/** Screen-height shares below which LOD1, LOD2 and LOD3 draw (style guide section 9). */
export const LOD_SHARE: readonly [number, number, number] = [0.25, 0.1, 0.04];
/** ... but never nearer than these (m): hedges are small, their LOD1 would show at arm's length. */
export const LOD_MIN_M: readonly [number, number, number] = [16, 40, 90];
/** Plantings nearer than this always draw (m); farther ones only inside the frustum. */
export const CULL_NEAR_M = 80;
/** How long a LOD change crossfades (s). */
export const LOD_FADE_S = 0.3;
/** A LOD change needs the distance this share past its threshold. */
export const LOD_HYSTERESIS = 0.04;
/** Rebuild the buffers when the camera has moved this far (m) or its view turned (dot below REBUCKET_DOT). */
export const REBUCKET_M = 0.75;
const REBUCKET_DOT = 0.9995;

/**
 * The LOD a planting of on-screen `size` (m) draws at `distance` (m), the vertical field of
 * view `fov` (rad), at least `minLod`. Pure (unit tested).
 */
export function foliageLod(size: number, distance: number, fov: number, minLod = 0): number {
  const span = 2 * Math.tan(fov / 2);
  let lod = 0;
  for (let k = 0; k < 3; k++) {
    const at = Math.max(LOD_MIN_M[k]!, size / (LOD_SHARE[k]! * span));
    if (distance > at) lod = k + 1;
  }
  return Math.max(lod, Math.min(3, minLod));
}

/**
 * The LOD a planting showing `current` (-1: none yet) moves to: `foliageLod`, but only once the
 * distance is LOD_HYSTERESIS past the threshold it crosses. Pure (unit tested).
 */
export function nextLod(current: number, size: number, distance: number, fov: number, minLod = 0): number {
  const want = foliageLod(size, distance, fov, minLod);
  if (current < 0 || want === current) return want;
  const past = foliageLod(size, want > current ? distance / (1 + LOD_HYSTERESIS) : distance * (1 + LOD_HYSTERESIS), fov, minLod);
  return past === current ? current : want;
}

/**
 * The `foliageFade` values of a planting `k` (0..1) into its crossfade: [new LOD, old LOD]
 * (complementary dithers; 1 when settled). Pure.
 */
export function fadePair(k: number): [number, number] {
  const t = Math.min(1, Math.max(1e-3, k));
  return [t, -t];
}

/** A manifest row as a Babylon world matrix (row vectors: the basis images, then the translation). */
export function rowMatrix(row: readonly number[], out = new Float32Array(16), at = 0): Float32Array {
  out[at + 0] = row[1]!;
  out[at + 1] = row[2]!;
  out[at + 2] = row[3]!;
  out[at + 3] = 0;
  out[at + 4] = row[4]!;
  out[at + 5] = row[5]!;
  out[at + 6] = row[6]!;
  out[at + 7] = 0;
  out[at + 8] = row[7]!;
  out[at + 9] = row[8]!;
  out[at + 10] = row[9]!;
  out[at + 11] = 0;
  out[at + 12] = row[10]!;
  out[at + 13] = row[11]!;
  out[at + 14] = row[12]!;
  out[at + 15] = 1;
  return out;
}

interface Plant {
  owner: string;
  species: string;
  matrix: Float32Array;
  /** Where its LOD is measured from: its foot plus half its height. */
  cx: number;
  cy: number;
  cz: number;
  size: number;
  radius: number;
  minLod: number;
  /** The LOD drawn (-1: not drawn), the one it is fading from (-1: none) and when that began (s). */
  lod: number;
  prev: number;
  t0: number;
}

/** A cell's plantings as plants owned by `owner`, for the species the kit has. */
export function plantsOf(owner: string, rows: FoliageRows | undefined, kit: FoliageKitInfo): Plant[] {
  if (!rows || !Array.isArray(rows.plants) || !Array.isArray(rows.species)) return [];
  const out: Plant[] = [];
  for (const r of rows.plants) {
    const species = rows.species[r[0]!];
    if (!species || !kit.species[species] || r.length !== 13) continue;
    const m = rowMatrix(r);
    const sy = Math.hypot(r[4]!, r[5]!, r[6]!);
    const sx = Math.hypot(r[1]!, r[2]!, r[3]!);
    const size = kit.species[species]!.size * Math.max(sy, sx);
    out.push({
      owner,
      species,
      matrix: m,
      cx: r[10]! + (r[4]! / Math.max(sy, 1e-6)) * size * 0.5,
      cy: r[11]! + (r[5]! / Math.max(sy, 1e-6)) * size * 0.5,
      cz: r[12]! + (r[6]! / Math.max(sy, 1e-6)) * size * 0.5,
      size,
      radius: size * 0.6,
      minLod: rows.min_lod ?? 0,
      lod: -1,
      prev: -1,
      t0: 0,
    });
  }
  return out;
}

/** The foliage material's own shader work: the wind sway and the LOD crossfade (see the top of the file). */
export class FoliageWind extends MaterialPluginBase {
  time = 0;
  strength = 1;
  constructor(material: Material) {
    super(material, "FoliageWind", 250, { FOLIAGE_WIND: false, FOLIAGE_FADE: false });
    this._enable(true);
  }
  override prepareDefines(defines: MaterialDefines, _scene: unknown, mesh: AbstractMesh): void {
    defines["FOLIAGE_WIND"] = this.strength > 0;
    // Every kit mesh carries the per-instance fade (FoliageSystem registers it).
    defines["FOLIAGE_FADE"] = !!(mesh as Mesh).getVertexBuffer?.(FADE_ATTRIBUTE);
  }
  override getAttributes(attributes: string[], _scene: unknown, mesh: AbstractMesh): void {
    if ((mesh as Mesh).getVertexBuffer?.(FADE_ATTRIBUTE)) attributes.push(FADE_ATTRIBUTE);
  }
  override getClassName(): string {
    return "FoliageWindPlugin";
  }
  override getUniforms() {
    return { ubo: [{ name: "foliageWind", size: 4, type: "vec4" }], vertex: "uniform vec4 foliageWind;" };
  }
  override bindForSubMesh(ubo: UniformBuffer): void {
    ubo.updateFloat4("foliageWind", this.time, this.strength, 0, 0);
  }
  override getCustomCode(shaderType: string): { [point: string]: string } | null {
    if (shaderType === "fragment") {
      return {
        CUSTOM_FRAGMENT_DEFINITIONS: `
#ifdef FOLIAGE_FADE
varying float vFoliageFade;
float foliageBayer(vec2 p) {
  ivec2 q = ivec2(mod(p, 4.0));
  int i = q.x + q.y * 4;
  float m[16] = float[16](0., 8., 2., 10., 12., 4., 14., 6., 3., 11., 1., 9., 15., 7., 13., 5.);
  return (m[i] + 0.5) / 16.0;
}
#endif
`,
        CUSTOM_FRAGMENT_MAIN_BEGIN: `
#ifdef FOLIAGE_FADE
{
  float d = foliageBayer(gl_FragCoord.xy);
  if (vFoliageFade >= 0.0 ? d >= vFoliageFade : d < -vFoliageFade) discard;
}
#endif
`,
      };
    }
    if (shaderType !== "vertex") return null;
    return {
      CUSTOM_VERTEX_DEFINITIONS: `
#ifdef FOLIAGE_FADE
attribute float foliageFade;
varying float vFoliageFade;
#endif
`,
      CUSTOM_VERTEX_MAIN_END: `
#ifdef FOLIAGE_FADE
vFoliageFade = foliageFade;
#endif
`,
      CUSTOM_VERTEX_UPDATE_WORLDPOS: `
#ifdef FOLIAGE_WIND
{
  float h = max(positionUpdated.y, 0.0);
  float bend = h * h * 0.0035 * foliageWind.y;
  vec2 ph = vec2(finalWorld[3][0], finalWorld[3][2]) * 0.21;
  float t = foliageWind.x;
  float sway = sin(t * 1.15 + ph.x + ph.y) + 0.35 * sin(t * 2.7 + ph.y * 1.7);
  float flutter = 0.02 * foliageWind.y * smoothstep(1.5, 4.0, h) * sin(t * 7.0 + dot(worldPos.xyz, vec3(1.7, 2.3, 1.3)));
  worldPos.x += bend * sway + flutter;
  worldPos.z += bend * 0.55 * sin(t * 0.9 + ph.y) + flutter * 0.6;
  worldPos.y += flutter * 0.5;
}
#endif
`,
    };
  }
}

/** The per-instance crossfade attribute of the kit's meshes. */
export const FADE_ATTRIBUTE = "foliageFade";

interface Slot {
  mesh: Mesh;
  buf: Float32Array;
  fade: Float32Array;
  n: number;
}

export class FoliageSystem {
  private readonly slots = new Map<string, Slot>();
  private castle: Plant[] = [];
  private room: Plant[] = [];
  private roomId: string | null = null;
  private roomRows = new Map<string, FoliageRows[]>();
  private ready = false;
  private disposed = false;
  private dirty = true;
  private readonly lastPos = new Vector3(Infinity, 0, 0);
  private readonly lastDir = new Vector3(0, 0, 0);
  /** Whether a crossfade ran in the last rebuild (rebuild again next frame). */
  private fading = false;
  private readonly frustum: Plane[] = [];
  private readonly tmp = new Vector3();
  private wind: FoliageWind | null = null;
  private readonly meshes: Mesh[] = [];
  /** Plantings drawn at each LOD in the last rebuild, and their triangles (the F3 overlay, probes). */
  readonly drawn = [0, 0, 0, 0];
  drawnTris = 0;

  constructor(
    private readonly scene: Scene,
    private readonly baseUrl: string,
    private readonly kit: FoliageKitInfo,
    private readonly library: SharedMaterials | undefined,
    private readonly onMeshes: (meshes: readonly AbstractMesh[]) => void = () => {},
  ) {
    void this.load();
  }

  /** The castle's plantings, by cell (the manifest's cells). */
  setCastle(cells: readonly CellInfo[]): void {
    this.castle = cells.flatMap((c) => plantsOf(c.id, (c as { foliage?: FoliageRows }).foliage, this.kit));
    this.fitBounds();
    this.dirty = true;
  }

  /** Instance rooms' plantings by layout id (instances.json entries' cells). */
  setRoomRows(rows: Map<string, FoliageRows[]>): void {
    this.roomRows = rows;
    if (this.roomId) this.setRoom(this.roomId);
  }

  /** The room shown (its layout id), or null in the castle. */
  setRoom(layout: string | null): void {
    this.roomId = layout;
    this.room = layout ? (this.roomRows.get(layout) ?? []).flatMap((r) => plantsOf("#room", r, this.kit)) : [];
    this.fitBounds();
    this.dirty = true;
  }

  get count(): number {
    return this.castle.length + this.room.length;
  }

  private async load(): Promise<void> {
    try {
      const c = await LoadAssetContainerAsync(assetUrl(this.baseUrl + this.kit.file), this.scene, { pluginExtension: ".glb" });
      if (this.disposed) {
        c.dispose();
        return;
      }
      toonifyContainer(c, this.scene, WORLD_MAX_LIGHTS, { shared: this.library });
      c.addAllToScene();
      for (const m of c.meshes) {
        if (!(m instanceof Mesh) || m.getTotalVertices() === 0) continue;
        m.setParent(null);
        m.isPickable = false;
        m.doNotSyncBoundingInfo = true;
        m.alwaysSelectAsActiveMesh = false;
        const buf = new Float32Array(16 * 8);
        const fade = new Float32Array(8).fill(1);
        m.thinInstanceSetBuffer("matrix", buf, 16, false);
        m.thinInstanceSetBuffer(FADE_ATTRIBUTE, fade, 1, false);
        m.thinInstanceCount = 0;
        m.setEnabled(false);
        this.slots.set(m.name, { mesh: m, buf, fade, n: 0 });
        this.meshes.push(m);
        const mat = m.material;
        if (mat && !this.wind && mat.name === "castle_foliage") this.wind = new FoliageWind(mat);
      }
      for (const n of c.transformNodes) n.dispose();
      this.ready = true;
      this.fitBounds();
      this.dirty = true;
      this.onMeshes(this.meshes);
    } catch (e) {
      console.info("[foliage] no foliage kit; the trees and hedges are not drawn", e instanceof Error ? e.message : e);
    }
  }

  private fitBounds(): void {
    if (!this.ready) return;
    const all = [...this.castle, ...this.room];
    if (all.length === 0) return;
    const min = new Vector3(Infinity, Infinity, Infinity);
    const max = new Vector3(-Infinity, -Infinity, -Infinity);
    for (const p of all) {
      min.minimizeInPlaceFromFloats(p.cx - p.radius * 2, p.cy - p.size, p.cz - p.radius * 2);
      max.maximizeInPlaceFromFloats(p.cx + p.radius * 2, p.cy + p.size, p.cz + p.radius * 2);
    }
    for (const m of this.meshes) m.setBoundingInfo(new BoundingInfo(min, max));
  }

  /**
   * Once a frame: the plantings shown (`shown(owner)`, owner a castle cell id or "#room"),
   * each at its LOD from `camera`. `low`: Low quality (LOD1 at the nearest).
   */
  update(camera: Camera, t: number, shown: (owner: string) => boolean, low = false): void {
    if (this.wind) this.wind.time = t;
    if (!this.ready) return;
    const pos = camera.globalPosition;
    const dir = camera.getForwardRay(1).direction;
    if (!this.dirty && !this.fading && Vector3.DistanceSquared(pos, this.lastPos) < REBUCKET_M * REBUCKET_M && Vector3.Dot(dir, this.lastDir) > REBUCKET_DOT) {
      return;
    }
    this.fading = false;
    this.dirty = false;
    this.lastPos.copyFrom(pos);
    this.lastDir.copyFrom(dir);
    const planes = Frustum.GetPlanes(camera.getTransformationMatrix());
    this.frustum.length = 0;
    this.frustum.push(...planes);
    for (const s of this.slots.values()) s.n = 0;
    this.drawn.fill(0);
    this.drawnTris = 0;
    const fov = (camera as { fov?: number }).fov ?? 0.9;
    const plants = this.room.length && this.roomId ? this.room : this.castle;
    for (const p of plants === this.castle ? this.room : this.castle) p.lod = p.prev = -1;
    for (const p of plants) {
      const d = Math.hypot(p.cx - pos.x, p.cy - pos.y, p.cz - pos.z);
      if (!shown(p.owner) || (d > CULL_NEAR_M && !this.inFrustum(p))) {
        p.lod = p.prev = -1; // reappearing: no fade
        continue;
      }
      const lod = nextLod(p.lod, p.size, d, fov, Math.max(p.minLod, low ? 1 : 0));
      if (p.lod >= 0 && lod !== p.lod) {
        p.prev = p.lod;
        p.t0 = t;
      }
      p.lod = lod;
      let f = 1;
      if (p.prev >= 0) {
        const k = (t - p.t0) / LOD_FADE_S;
        if (k >= 1) p.prev = -1;
        else {
          const [fin, fout] = fadePair(k);
          f = fin;
          this.push(p, p.prev, fout);
          this.fading = true;
        }
      }
      this.push(p, lod, f);
      this.drawn[lod]!++;
      this.drawnTris += this.kit.species[p.species]?.tris[lod] ?? 0;
    }
    for (const s of this.slots.values()) {
      s.mesh.thinInstanceCount = s.n;
      s.mesh.setEnabled(s.n > 0);
      if (s.n > 0) {
        s.mesh.thinInstanceBufferUpdated("matrix");
        s.mesh.thinInstanceBufferUpdated(FADE_ATTRIBUTE);
      }
    }
  }

  /** Forces a rebuild on the next update (cells shown or hidden). */
  invalidate(): void {
    this.dirty = true;
  }

  private inFrustum(p: Plant): boolean {
    this.tmp.set(p.cx, p.cy, p.cz);
    for (const plane of this.frustum) if (plane.dotCoordinate(this.tmp) < -p.radius) return false;
    return true;
  }

  /** Appends planting `p` to its species' `lod` buffer with crossfade value `fade`. */
  private push(p: Plant, lod: number, fade: number): void {
    const slot = this.slots.get(nodeName(p.species, lod));
    if (!slot) return;
    if ((slot.n + 1) * 16 > slot.buf.length) this.grow(slot);
    slot.buf.set(p.matrix, slot.n * 16);
    slot.fade[slot.n] = fade;
    slot.n++;
  }

  private grow(slot: Slot): void {
    const b = new Float32Array(slot.buf.length * 2);
    b.set(slot.buf);
    slot.buf = b;
    const f = new Float32Array(slot.fade.length * 2).fill(1);
    f.set(slot.fade);
    slot.fade = f;
    slot.mesh.thinInstanceSetBuffer("matrix", b, 16, false);
    slot.mesh.thinInstanceSetBuffer(FADE_ATTRIBUTE, f, 1, false);
  }

  /** One line for the F3 overlay. */
  get line(): string {
    return `${this.count} plantings, drawn ${this.drawn.join("/")} by LOD (${Math.round(this.drawnTris / 1000)}k tris)`;
  }

  dispose(): void {
    this.disposed = true;
    for (const m of this.meshes) m.dispose(false, false);
    this.meshes.length = 0;
    this.slots.clear();
  }
}
