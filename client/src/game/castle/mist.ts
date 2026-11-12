// The grounds by night (docs/architecture.md).
//
// Two things, both driven by the clock (clock/daynight.ts calls
// `setGroundsNight` with 1 - daylight once a clock tick):
//
// - **The night tint** (`NightTintPlugin`): at night the grass, the trees, the
//   backdrop (the forest edge, the mountains, the crag) and the lake go darker
//   and cooler than the moonlight alone makes them, so the far castle's lit
//   windows read over dark grounds instead of a grey castle over bright green
//   grass. The colour is multiplied, after the toon light and before the fog,
//   by a palette multiplier (`night_grounds`, `night_water` in
//   data/art/castle_textures.json) to the power of the night (1 by day). The
//   plugin is attached by material name (`nightTintOf`; scene.ts does it as
//   the materials appear): `grounds_terrain`, `backdrop_*`, `lake_water`, and
//   `castle_props` on the foliage's meshes only (the trees and rocks share
//   the props atlas with the castle's furniture, which keeps its colours).
// - **The low mist over the lake** (`LakeMist`): three thin layers of the
//   lake's own mesh (one draw: thin instances 0.5, 1.2 and 2 m over the water),
//   alpha blended, unlit, in the palette's `lake_mist`, drifting slowly, thin
//   at the shoreline (the lake's depth in COLOR_0) and near the camera. Night
//   only, Medium and High (Low is the fallback: nothing is made for it, and the
//   blended layers are the cost it would drop first). It shows only while the
//   lake does (the castle zone is shown).
//
// No per-frame work beyond a few uniforms and one enabled check.

import { MaterialPluginBase } from "@babylonjs/core/Materials/materialPluginBase";
import { ShaderLanguage } from "@babylonjs/core/Materials/shaderLanguage";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Material } from "@babylonjs/core/Materials/material";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Matrix } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import type { UniformBuffer } from "@babylonjs/core/Materials/uniformBuffer";
import type { Scene } from "@babylonjs/core/scene";
import type { Observer } from "@babylonjs/core/Misc/observable";
import { palette as KIT_PALETTE } from "../../../../data/art/castle_textures.json";
import { paletteRgb } from "./terrain";

type Rgb = [number, number, number];

const vec3 = (c: Rgb) => `vec3(${c.map((v) => v.toFixed(4)).join(", ")})`;

// ---------------------------------------------------------------- the night (shared)

let NIGHT = 0;

/** How much night the grounds show (0 by day .. 1 at full night); clock/daynight.ts sets it. */
export function setGroundsNight(v: number): void {
  NIGHT = Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0));
}

export function groundsNight(): number {
  return NIGHT;
}

// ---------------------------------------------------------------- the night tint

/** The night multipliers (sRGB 0..1) from the palette; white (no change) for a row that is missing. */
export function nightTints(palette: Readonly<Record<string, unknown>> = KIT_PALETTE): { grounds: Rgb; water: Rgb } {
  return {
    grounds: paletteRgb(["night_grounds"], palette) ?? [1, 1, 1],
    water: paletteRgb(["night_water", "night_grounds"], palette) ?? [1, 1, 1],
  };
}

/** The foliage cell's meshes (the trees and rocks on `castle_props`). */
export const FOLIAGE_PREFIX = "foliage.";

export type TintKind = "grounds" | "water";

/** Which night tint a material takes, by name; null for one that keeps its colours. `foliageOnly`: only the foliage's meshes. */
export function nightTintOf(name: string): { kind: TintKind; foliageOnly: boolean } | null {
  if (name === "grounds_terrain" || name.startsWith("backdrop_")) return { kind: "grounds", foliageOnly: false };
  if (name === "lake_water") return { kind: "water", foliageOnly: false };
  if (name === "castle_props") return { kind: "grounds", foliageOnly: true };
  // Vegetation look A: every tree and hedge (castle/foliage.ts).
  if (name === "castle_foliage") return { kind: "grounds", foliageOnly: false };
  return null;
}

/** The fragment code (exported for tests): after the toon light, before the fog. */
export function nightFragment(tint: Rgb): Record<string, string> {
  return {
    CUSTOM_FRAGMENT_BEFORE_FOG: `
#ifdef NIGHT_TINT
color.rgb *= mix(vec3(1.0), ${vec3(tint)}, groundsNight);
#endif
`,
  };
}

/** The night tint on one material (see the top of the file). */
export class NightTintPlugin extends MaterialPluginBase {
  private readonly code: Record<string, string>;

  constructor(
    material: Material,
    tint: Rgb,
    private readonly foliageOnly = false,
  ) {
    super(material, "NightTint", 190, { NIGHT_TINT: false }, true, true);
    this.code = nightFragment(tint);
  }

  override getClassName(): string {
    return "NightTintPlugin";
  }

  override isCompatible(shaderLanguage: ShaderLanguage): boolean {
    return shaderLanguage === ShaderLanguage.GLSL;
  }

  override prepareDefines(defines: MaterialDefines, _scene: Scene, mesh: AbstractMesh): void {
    defines["NIGHT_TINT"] = !this.foliageOnly || mesh.name.startsWith(FOLIAGE_PREFIX);
  }

  override getUniforms(): { ubo: { name: string; size: number; type: string }[]; fragment: string } {
    return { ubo: [{ name: "groundsNight", size: 1, type: "float" }], fragment: "uniform float groundsNight;" };
  }

  override bindForSubMesh(uniformBuffer: UniformBuffer): void {
    uniformBuffer.updateFloat("groundsNight", NIGHT);
  }

  override getCustomCode(shaderType: string): { [pointName: string]: string } | null {
    // Asked inside the base constructor, before `code` is set: the white tint's code has the same points.
    return shaderType === "fragment" ? (this.code ?? nightFragment([1, 1, 1])) : null;
  }
}

/** Attaches the night tint to `material` when its name asks for one; returns whether it did. */
export function attachNightTint(material: Material, tints = nightTints()): boolean {
  const t = nightTintOf(material.name);
  if (!t || !(material instanceof StandardMaterial)) return false;
  if (material.pluginManager?.getPlugin("NightTint")) return false;
  new NightTintPlugin(material, tints[t.kind], t.foliageOnly);
  return true;
}

// ---------------------------------------------------------------- the lake's mist

/** The lake's mesh in the `lake` cell (grounds_backdrop.py `water_mesh`). */
export const LAKE_MESH = "lake.lake_water";
/** The mist layers' heights over the water (m). */
export const MIST_LAYERS: readonly number[] = [0.5, 1.2, 2.0];
/** A layer's opacity at full night, where the mist is thickest. */
export const MIST_ALPHA = 0.34;
/** The mist's brightness (x the palette colour): moonlit, never glowing. */
export const MIST_LEVEL = 0.5;
/** The mist thins out this near the camera (m): no hard layer edge at the eye. */
export const MIST_NEAR: readonly [number, number] = [5, 55];
/** The drift's time wraps every this many seconds. */
export const MIST_LOOP_S = 240;

/** Whether the mist is drawn: night, the lake shown, not Low. Pure, unit tested. */
export function mistShown(night: number, lakeShown: boolean, quality: string): boolean {
  return night > 0.02 && lakeShown && quality !== "low";
}

/** The mist's fragment code (exported for tests). */
export function mistFragment(colour: Rgb): Record<string, string> {
  return {
    CUSTOM_FRAGMENT_BEFORE_FOG: `
#ifdef LAKE_MIST
{
#ifdef VERTEXCOLOR
  float mistDepth = clamp(pow(vColor.r, 2.2), 0.0, 1.0);
#else
  float mistDepth = 1.0;
#endif
  vec2 mistP = vPositionW.xz * 0.018 + vec2(vPositionW.y * 3.1, vPositionW.y * 1.7);
  float mistT = mistTime * ${(2 * Math.PI / 120).toFixed(5)};
  float mistN = 0.5 + 0.25 * sin(mistP.x * 2.3 + mistT) * sin(mistP.y * 1.9 - mistT * 0.5)
              + 0.25 * sin((mistP.x + mistP.y) * 4.1 - mistT * 2.0);
  float mistNear = smoothstep(${MIST_NEAR[0].toFixed(1)}, ${MIST_NEAR[1].toFixed(1)}, distance(vEyePosition.xyz, vPositionW));
  float mistA = ${MIST_ALPHA.toFixed(3)} * mistNight * smoothstep(0.02, 0.4, mistDepth) * mistNear * clamp(mistN, 0.0, 1.0);
  color = vec4(${vec3(colour)} * ${MIST_LEVEL.toFixed(3)}, mistA);
}
#endif
`,
  };
}

class MistPlugin extends MaterialPluginBase {
  night = 0;
  time = 0;
  private readonly code: Record<string, string>;

  constructor(material: Material, colour: Rgb) {
    super(material, "LakeMist", 200, { LAKE_MIST: false }, true, true);
    this.code = mistFragment(colour);
  }

  override getClassName(): string {
    return "MistPlugin";
  }

  override isCompatible(shaderLanguage: ShaderLanguage): boolean {
    return shaderLanguage === ShaderLanguage.GLSL;
  }

  override prepareDefines(defines: MaterialDefines): void {
    defines["LAKE_MIST"] = true;
  }

  override getUniforms(): { ubo: { name: string; size: number; type: string }[]; fragment: string } {
    return {
      ubo: [
        { name: "mistNight", size: 1, type: "float" },
        { name: "mistTime", size: 1, type: "float" },
      ],
      fragment: "uniform float mistNight;\nuniform float mistTime;",
    };
  }

  override bindForSubMesh(uniformBuffer: UniformBuffer): void {
    uniformBuffer.updateFloat("mistNight", this.night);
    uniformBuffer.updateFloat("mistTime", this.time);
  }

  override getCustomCode(shaderType: string): { [pointName: string]: string } | null {
    return shaderType === "fragment" ? (this.code ?? mistFragment([1, 1, 1])) : null;
  }
}

/** The low mist over the lake (see the top of the file). */
export class LakeMist {
  private mesh: Mesh | null = null;
  private lake: AbstractMesh | null = null;
  /** The lake mesh as it was added (it may not have its geometry yet): attached on the next update. */
  private pending: Mesh | null = null;
  private mat: StandardMaterial | null = null;
  private plugin: MistPlugin | null = null;
  private readonly added: Observer<AbstractMesh>;
  private quality = "medium";

  constructor(private readonly scene: Scene) {
    this.added = scene.onNewMeshAddedObservable.add((m) => {
      if (m.name === LAKE_MESH && m instanceof Mesh) this.pending = m;
    });
  }

  /** The mist's mesh (tests, probes); null until the lake has loaded. */
  get layers(): Mesh | null {
    return this.mesh;
  }

  setQuality(q: string): void {
    this.quality = q;
  }

  /** Builds the layers on `lake`'s geometry (its own mesh, the lake's buffers shared). */
  attach(lake: Mesh): void {
    this.detach();
    const scene = this.scene;
    const colour = paletteRgb(["lake_mist"], KIT_PALETTE) ?? [0.5, 0.5, 0.5];
    if (!this.mat) {
      const mat = new StandardMaterial("lake_mist", scene);
      mat.disableLighting = true;
      mat.diffuseColor = Color3.Black();
      mat.specularColor = Color3.Black();
      mat.emissiveColor = Color3.Black();
      mat.backFaceCulling = false;
      mat.disableDepthWrite = true;
      mat.transparencyMode = Material.MATERIAL_ALPHABLEND;
      this.plugin = new MistPlugin(mat, colour);
      this.mat = mat;
    }
    const mesh = new Mesh("lake_mist", scene);
    lake.geometry?.applyToMesh(mesh);
    mesh.parent = lake.parent;
    mesh.position.copyFrom(lake.position);
    if (lake.rotationQuaternion) mesh.rotationQuaternion = lake.rotationQuaternion.clone();
    mesh.rotation.copyFrom(lake.rotation);
    mesh.scaling.copyFrom(lake.scaling);
    mesh.material = this.mat;
    mesh.isPickable = false;
    mesh.receiveShadows = false;
    mesh.alwaysSelectAsActiveMesh = false;
    mesh.thinInstanceSetBuffer("matrix", packLayers(MIST_LAYERS), 16, true);
    mesh.setEnabled(false);
    this.mesh = mesh;
    this.lake = lake;
    lake.onDisposeObservable.addOnce(() => {
      if (this.lake === lake) this.detach();
    });
  }

  /** Per frame: shows the layers at night while the lake shows; moves the drift. */
  update(night: number, nowMs: number): void {
    const p = this.pending;
    if (p && (p.isDisposed() || p.geometry)) {
      this.pending = null;
      if (!p.isDisposed()) this.attach(p);
    }
    const mesh = this.mesh;
    if (!mesh || !this.plugin) return;
    const show = mistShown(night, !!this.lake && this.lake.isEnabled(), this.quality);
    if (mesh.isEnabled() !== show) mesh.setEnabled(show);
    if (!show) return;
    this.plugin.night = night;
    this.plugin.time = (nowMs / 1000) % MIST_LOOP_S;
  }

  private detach(): void {
    this.mesh?.dispose(false, false);
    this.mesh = null;
    this.lake = null;
  }

  dispose(): void {
    this.scene.onNewMeshAddedObservable.remove(this.added);
    this.detach();
    this.mat?.dispose(false, true);
    this.mat = null;
    this.plugin = null;
  }
}

/** The layers' instance matrices: the lake lifted by each height. */
export function packLayers(heights: readonly number[]): Float32Array {
  const out = new Float32Array(heights.length * 16);
  heights.forEach((h, i) => Matrix.Translation(0, h, 0).copyToArray(out, i * 16));
  return out;
}
