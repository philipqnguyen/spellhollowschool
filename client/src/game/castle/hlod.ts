// The far castle (HLOD; docs/architecture.md "Cells and the world
// manifest", docs/art/castle-kit.md "Exterior"). From the grounds and
// the lake the whole castle shows its full skyline, though only the cells
// near the player are loaded: `hlod.glb` holds cheap stand-ins, one mesh per
// chunk and material (`hlod.<chunk>.<material>`, on the library's
// `castle_wall_c@ext` and `castle_roof`), loaded once with the castle zone
// and never in an instance.
//
// The rule (`hlodShown`, unit tested):
// - nothing is drawn unless the player stands outdoors (`SimWorld.outdoorsAt`:
//   the grounds, the courtyard), so indoors, where walls hide the outside,
//   the chunks cost nothing;
// - a chunk without `cells` (towers with no interior yet: the Astronomy
//   Tower, the Clock Tower ranges, the south front, the spires) is drawn
//   whenever the player is outdoors;
// - a chunk with `cells` stands in for those playable cells and is hidden
//   while every one of them is loaded and shown; while any is missing it is
//   drawn. The stand-ins sit 0.3 m inside the real walls, so a partly
//   loaded chunk shows behind the real cells, never in front of them.
//
// The chunks cast and take no shadow, are never picked and the camera does
// not collide with them. The rule runs on place updates (four times a
// second, zones-scene.ts), never per frame.
//
// The skyline (docs/architecture.md):
// - The stone is the silhouette's own material `hlod_stone`: the library's
//   `castle_wall_c` texture (one GPU copy, followed when the library swaps it)
//   lit by the toon row `hlod_stone` (a cool shadow band: the shadow sides of
//   the far towers read as the pale cool stone in shade, not the warm mauve
//   of the indoor walls' row), and the window plugin: the panes the export
//   marks lit (COLOR_0 `LIT_PANE_BELOW`) glow the palette's `window_night` by
//   night only (`setHlodNight`, from clock/daynight.ts).
// - The slate: every `castle_roof` (the castle's real roofs too, the hut's
//   and the boathouse's) draws the palette's `roof_slate` and
//   `roof_slate_joint`: `RoofSlatePlugin` maps the slate texture's tones
//   (painted round its own blue) onto them, so the roofs read as dark
//   weathered grey-blue slate. scene.ts attaches it as the material appears.
// - The crag chunk (`hlod_crag`, `backdrop_crag`): the rock faces under the
//   outer walls, converted like any glTF material (toon row `backdrop*`).

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import type { AssetContainer } from "@babylonjs/core/assetContainer";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Scene } from "@babylonjs/core/scene";
import { MaterialPluginBase } from "@babylonjs/core/Materials/materialPluginBase";
import { ShaderLanguage } from "@babylonjs/core/Materials/shaderLanguage";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import type { Material } from "@babylonjs/core/Materials/material";
import type { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import type { UniformBuffer } from "@babylonjs/core/Materials/uniformBuffer";
import type { Observer } from "@babylonjs/core/Misc/observable";
import { palette as KIT_PALETTE } from "../../../../data/art/castle_textures.json";
import { toonifyContainer, WORLD_MAX_LIGHTS } from "../toon/convert";
import { ToonPlugin } from "../toon/material";
import { worldRamp } from "../toon/palette";
import type { CastleLibrary } from "./materials";
import { paletteRgb } from "./terrain";
import { assetUrl } from "../../assets";

export interface HlodChunk {
  id: string;
  name: string;
  /** The playable cells it stands in for; empty for exterior-only chunks. */
  cells: string[];
  bounds?: { min: [number, number, number]; max: [number, number, number] };
  tris?: number;
  draws?: number;
  library?: string[];
}

/** The manifest's `hlod` entry. */
export interface HlodManifest {
  file: string;
  tris: number;
  chunks: HlodChunk[];
}

/** The node name prefix of every chunk mesh (grid.hlod_node). */
export const HLOD_PREFIX = "hlod.";

/** The export's outside stone (hlod_build.STONE): drawn with the silhouette's own `hlod_stone`. */
export const HLOD_EXPORT_STONE = "castle_wall_c@ext";
/** The silhouette's stone material and its toon row (data/art/toon_palette.json). */
export const HLOD_STONE = "hlod_stone";
/**
 * A pane the export lit (hlod_build.SHADE_LIT, 0.22 linear) reads below this
 * in the shader's sRGB COLOR_0; an unlit pane (SHADE_PANE, 0.30) and every
 * stone face (0.62 and up) read above it.
 */
export const LIT_PANE_BELOW = 0.54;

type Rgb = [number, number, number];
const vec3 = (c: Rgb) => `vec3(${c.map((v) => v.toFixed(4)).join(", ")})`;

// ---------------------------------------------------------------- night windows

let NIGHT = 0;

/** How brightly the far castle's lit panes glow (0 by day .. 1 at night); clock/daynight.ts sets it. */
export function setHlodNight(v: number): void {
  NIGHT = Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0));
}

export function hlodNight(): number {
  return NIGHT;
}

/** The lit panes' colour (sRGB 0..1): the palette's `window_night`. */
export function windowColour(palette: Readonly<Record<string, unknown>> = KIT_PALETTE): Rgb {
  return paletteRgb(["window_night", "glow_lantern"], palette) ?? [1, 1, 1];
}

/** The window plugin's fragment code (exported for tests): after the toon light, before the fog. */
export function windowFragment(colour: Rgb = windowColour()): Record<string, string> {
  return {
    CUSTOM_FRAGMENT_BEFORE_FOG: `
#if defined(HLOD_WINDOWS) && defined(VERTEXCOLOR)
color.rgb = mix(color.rgb, ${vec3(colour)}, hlodNight * (1.0 - step(${LIT_PANE_BELOW.toFixed(3)}, vColor.r)));
#endif
`,
  };
}

/** The lit panes' night glow on `hlod_stone`. */
export class HlodWindowPlugin extends MaterialPluginBase {
  private readonly code: Record<string, string>;

  constructor(material: Material, colour: Rgb = windowColour()) {
    super(material, "HlodWindows", 180, { HLOD_WINDOWS: false }, true, true);
    this.code = windowFragment(colour);
  }

  override getClassName(): string {
    return "HlodWindowPlugin";
  }

  override isCompatible(shaderLanguage: ShaderLanguage): boolean {
    return shaderLanguage === ShaderLanguage.GLSL;
  }

  override prepareDefines(defines: MaterialDefines, _scene: Scene, mesh: AbstractMesh): void {
    defines["HLOD_WINDOWS"] = mesh.isVerticesDataPresent(VertexBuffer.ColorKind);
  }

  override getUniforms(): { ubo: { name: string; size: number; type: string }[]; fragment: string } {
    return { ubo: [{ name: "hlodNight", size: 1, type: "float" }], fragment: "uniform float hlodNight;" };
  }

  override bindForSubMesh(uniformBuffer: UniformBuffer): void {
    uniformBuffer.updateFloat("hlodNight", NIGHT);
  }

  override getCustomCode(shaderType: string): { [pointName: string]: string } | null {
    return shaderType === "fragment" ? (this.code ?? windowFragment([1, 1, 1])) : null;
  }
}

// ---------------------------------------------------------------- slate roofs

/** The roofs' material (castle M5c): every one takes the slate plugin. */
export const ROOF_MATERIAL = "castle_roof";
/**
 * The slate texture's own tones as painted (castle_kit/textures.py
 * `roof_slate`): the luma (0.299, 0.587, 0.114, sRGB) of its slate base and
 * of its joint colour. Numbers, not colours: the colours are the palette's.
 */
export const SLATE_TEXTURE_LUMA: readonly [number, number] = [0.3887, 0.2879];

/** The slate's colours (sRGB 0..1) from the palette. */
export function slateColours(palette: Readonly<Record<string, unknown>> = KIT_PALETTE): { slate: Rgb; joint: Rgb } | null {
  const slate = paletteRgb(["roof_slate"], palette);
  const joint = paletteRgb(["roof_slate_joint"], palette);
  return slate && joint ? { slate, joint } : null;
}

/** The slate plugin's fragment code (exported for tests): where the texel is read. */
export function slateFragment(c: { slate: Rgb; joint: Rgb }): Record<string, string> {
  const [base, joint] = SLATE_TEXTURE_LUMA;
  return {
    CUSTOM_FRAGMENT_UPDATE_ALPHA: `
#ifdef ROOF_SLATE
{
  float slateL = dot(baseColor.rgb, vec3(0.299, 0.587, 0.114));
  vec3 slateC = mix(${vec3(c.joint)}, ${vec3(c.slate)}, smoothstep(${joint.toFixed(4)}, ${base.toFixed(4)}, slateL));
  baseColor.rgb = slateC * max(1.0, slateL / ${base.toFixed(4)});
}
#endif
`,
  };
}

/** The palette's slate on `castle_roof` (see the top of the file). */
export class RoofSlatePlugin extends MaterialPluginBase {
  private readonly code: Record<string, string> | null;

  constructor(material: Material, colours = slateColours()) {
    super(material, "RoofSlate", 165, { ROOF_SLATE: false }, true, true);
    this.code = colours ? slateFragment(colours) : null;
  }

  override getClassName(): string {
    return "RoofSlatePlugin";
  }

  override isCompatible(shaderLanguage: ShaderLanguage): boolean {
    return shaderLanguage === ShaderLanguage.GLSL;
  }

  override prepareDefines(defines: MaterialDefines): void {
    defines["ROOF_SLATE"] = this.code !== null;
  }

  override getCustomCode(shaderType: string): { [pointName: string]: string } | null {
    if (shaderType !== "fragment") return null;
    return this.code ?? slateFragment({ slate: [1, 1, 1], joint: [1, 1, 1] });
  }
}

/** Attaches the slate to a `castle_roof` material (once); returns whether it did. */
export function attachRoofSlate(material: Material): boolean {
  if (material.name !== ROOF_MATERIAL || !(material instanceof StandardMaterial)) return false;
  if (material.pluginManager?.getPlugin("RoofSlate")) return false;
  new RoofSlatePlugin(material);
  return true;
}

/** The manifest's `hlod` entry, checked; null when there is none or it is broken (nothing is drawn). */
export function parseHlod(json: unknown): HlodManifest | null {
  if (json === undefined || json === null) return null;
  const o = json as Partial<HlodManifest>;
  const ok =
    typeof o.file === "string" &&
    o.file.length > 0 &&
    !o.file.includes("..") &&
    Array.isArray(o.chunks) &&
    o.chunks.every((c: Partial<HlodChunk>) => typeof c?.id === "string" && (c.cells === undefined || (Array.isArray(c.cells) && c.cells.every((x) => typeof x === "string"))));
  if (!ok) {
    console.warn("[hlod] the manifest's hlod entry is malformed; the far castle is not drawn");
    return null;
  }
  return {
    file: o.file!,
    tris: typeof o.tris === "number" ? o.tris : 0,
    chunks: o.chunks!.map((c) => ({ ...c, name: typeof c.name === "string" ? c.name : c.id, cells: c.cells ?? [] })),
  };
}

/** `hlod.<chunk>.<material>` -> the chunk id, or null for any other name (a glTF primitive suffix is allowed). */
export function chunkOfNode(name: string): string | null {
  if (!name.startsWith(HLOD_PREFIX)) return null;
  const rest = name.slice(HLOD_PREFIX.length);
  const dot = rest.indexOf(".");
  return dot > 0 ? rest.slice(0, dot) : null;
}

/**
 * Whether `chunk` is drawn: only outdoors; an exterior-only chunk always
 * there, a stand-in while any of its cells is not loaded (`loaded`: loaded
 * and shown).
 */
export function hlodShown(chunk: Pick<HlodChunk, "cells">, outdoors: boolean, loaded: (cell: string) => boolean): boolean {
  if (!outdoors) return false;
  if (chunk.cells.length === 0) return true;
  for (const c of chunk.cells) if (!loaded(c)) return true;
  return false;
}

interface LiveChunk {
  info: HlodChunk;
  meshes: AbstractMesh[];
  shown: boolean;
}

/** The loaded far castle: its chunks and their show/hide. */
export class HlodSet {
  private readonly chunks = new Map<string, LiveChunk>();
  private container: AssetContainer | null = null;
  /** Library materials this set holds a reference on. */
  private readonly taken: string[] = [];
  /** The silhouette's own stone (`hlod_stone`), drawing the library's texture. */
  private stone: StandardMaterial | null = null;
  private stoneSync: Observer<Scene> | null = null;
  private disposed = false;
  private outdoors = false;
  private loaded: (cell: string) => boolean = () => false;

  constructor(
    private readonly scene: Scene,
    baseUrl: string,
    readonly manifest: HlodManifest,
    private readonly library?: CastleLibrary,
  ) {
    for (const c of manifest.chunks) this.chunks.set(c.id, { info: c, meshes: [], shown: false });
    void this.load(assetUrl(baseUrl + manifest.file));
  }

  private async load(url: string): Promise<void> {
    let container: AssetContainer;
    try {
      [container] = await Promise.all([LoadAssetContainerAsync(url, this.scene, { pluginExtension: ".glb" }), this.library?.ready()]);
    } catch (e) {
      console.warn(`[hlod] could not load ${url}; the far castle is not drawn`, e instanceof Error ? e.message.split("\n")[0] : e);
      return;
    }
    if (this.disposed) {
      container.dispose();
      return;
    }
    const library = this.library;
    const taken = this.taken;
    toonifyContainer(container, this.scene, undefined, {
      shared: library && {
        take: (name, placeholder) => {
          const m = library.take(name, placeholder);
          if (m) taken.push(name);
          return m && name === HLOD_EXPORT_STONE ? this.stoneOn(m) : m;
        },
      },
    });
    for (const m of container.meshes) {
      m.isPickable = false;
      m.receiveShadows = false;
      m.freezeWorldMatrix();
      m.doNotSyncBoundingInfo = true;
      if (m.getTotalVertices() === 0) continue;
      const id = chunkOfNode(m.name);
      const live = id ? this.chunks.get(id) : undefined;
      if (!live) {
        console.warn(`[hlod] ${m.name}: not a chunk of the manifest; not drawn`);
        m.setEnabled(false);
        continue;
      }
      live.meshes.push(m);
      m.setEnabled(false);
    }
    container.addAllToScene();
    this.container = container;
    for (const live of this.chunks.values()) live.shown = false;
    this.apply();
  }

  /** `hlod_stone`, drawing `base`'s texture (built once). */
  private stoneOn(base: Material): Material {
    if (this.stone) return this.stone;
    const lib = base as StandardMaterial;
    const mat = new StandardMaterial(HLOD_STONE, this.scene);
    mat.specularColor.set(0, 0, 0);
    mat.backFaceCulling = lib.backFaceCulling;
    mat.sideOrientation = lib.sideOrientation;
    mat.maxSimultaneousLights = WORLD_MAX_LIGHTS;
    new ToonPlugin(mat, worldRamp(HLOD_STONE));
    new HlodWindowPlugin(mat);
    const sync = () => {
      if (mat.diffuseTexture !== lib.diffuseTexture) mat.diffuseTexture = lib.diffuseTexture;
      if (!mat.diffuseColor.equals(lib.diffuseColor)) mat.diffuseColor.copyFrom(lib.diffuseColor);
    };
    sync();
    // The library loads its texture late and swaps it on a quality change: follow it (a pointer compare a frame).
    this.stoneSync = this.scene.onBeforeRenderObservable.add(sync);
    this.stone = mat;
    return mat;
  }

  /** The silhouette's stone material (tests, probes); null before the file has loaded. */
  get stoneMaterial(): StandardMaterial | null {
    return this.stone;
  }

  /**
   * Re-applies the rule for where the player stands (`outdoors`) and which
   * castle cells are loaded and shown (`loaded`). Call on place updates.
   */
  update(outdoors: boolean, loaded: (cell: string) => boolean): void {
    this.outdoors = outdoors;
    this.loaded = loaded;
    this.apply();
  }

  private apply(): void {
    for (const live of this.chunks.values()) {
      const show = this.container !== null && hlodShown(live.info, this.outdoors, this.loaded);
      if (show === live.shown) continue;
      live.shown = show;
      for (const m of live.meshes) m.setEnabled(show);
    }
  }

  /** Chunks drawn now and in all (the F3 overlay). */
  counts(): { shown: number; total: number; loaded: boolean } {
    let shown = 0;
    for (const c of this.chunks.values()) if (c.shown) shown++;
    return { shown, total: this.chunks.size, loaded: this.container !== null };
  }

  /** The meshes of chunk `id` (tests, probes). */
  meshesOf(id: string): readonly AbstractMesh[] {
    return this.chunks.get(id)?.meshes ?? [];
  }

  dispose(): void {
    this.disposed = true;
    if (this.container) {
      this.container.removeAllFromScene();
      this.container.dispose();
      this.container = null;
    }
    if (this.stoneSync) this.scene.onBeforeRenderObservable.remove(this.stoneSync);
    this.stoneSync = null;
    // Its texture is the library's: not disposed here.
    this.stone?.dispose(false, false);
    this.stone = null;
    for (const name of this.taken) this.library?.release(name);
    this.taken.length = 0;
    this.chunks.clear();
  }
}
