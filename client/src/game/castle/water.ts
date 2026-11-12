// The lake's toon water, material `lake_water` (docs/art/castle-kit.md
// "Exterior", docs/architecture.md).
//
// One mesh, one draw: a flat plane at the waterline. The colour is the
// palette's: `lake_shallow` where the water is shallow, `lake_deep` where it
// is deep (COLOR_0 carries the depth the export measured, 0 at the
// shoreline to 1 at 4 m or more), with thin bands of `lake_band` drifting
// slowly across it, and a band-coloured edge along the shore. Until the
// terrain palette rows exist, all three fall back to `water`.
//
// - A toon StandardMaterial like every world material (toon/material.ts:
//   the row `lake_water`, else `water`), so the hour's light and the fog
//   (the night's mist) fall on it as on the grounds. The plugin only sets
//   the albedo, where Babylon would multiply in COLOR_0 (`toonShadeX` is
//   kept at 1: the depth is not a baked shade).
// - The bands come from the world position (the export's TEXCOORD_0 is the
//   same world mapping, x / 16 and z / 16) and the time: two slow waves
//   whose periods divide `WATER_LOOP_S`, so the time can wrap without a
//   jump. Their edge is anti-aliased with the screen derivatives and fades
//   out where a band is thinner than a pixel (far away).
// - Shared like the library's materials (cells.ts takes it by name), never
//   unloaded (the lake is a resident cell); the time is the only uniform
//   written per frame.

import { MaterialPluginBase } from "@babylonjs/core/Materials/materialPluginBase";
import { ShaderLanguage } from "@babylonjs/core/Materials/shaderLanguage";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Material } from "@babylonjs/core/Materials/material";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import type { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import type { UniformBuffer } from "@babylonjs/core/Materials/uniformBuffer";
import type { Scene } from "@babylonjs/core/scene";
import { palette as KIT_PALETTE } from "../../../../data/art/castle_textures.json";
import { ToonPlugin } from "../toon/material";
import { worldRamp, WORLD_TOON } from "../toon/palette";
import { WORLD_MAX_LIGHTS, type SharedMaterials } from "../toon/convert";
import { paletteRgb } from "./terrain";

/** The water's material name in the `lake` cell. */
export const WATER_MATERIAL = "lake_water";
/** The bands' time wraps every this many seconds (both waves' periods divide it). */
export const WATER_LOOP_S = 240;
/** The two waves' periods (s): slow drift. */
const WAVE_PERIODS: readonly [number, number] = [60, 80];

type Rgb = [number, number, number];

/** The water's colours (sRGB 0..1), from the palette; `water` for any row not there yet. */
export function waterColours(palette: Readonly<Record<string, unknown>> = KIT_PALETTE): { shallow: Rgb; deep: Rgb; band: Rgb } {
  // Last resort (no palette rows at all): neutral grey, like terrain.ts; never a colour of our own.
  const water = paletteRgb(["water"], palette) ?? [0.5, 0.5, 0.5];
  return {
    shallow: paletteRgb(["lake_shallow"], palette) ?? water,
    deep: paletteRgb(["lake_deep"], palette) ?? water,
    band: paletteRgb(["lake_band"], palette) ?? water,
  };
}

/** The water's time uniform for `nowMs` (s, wrapped every WATER_LOOP_S). */
export function waterTime(nowMs: number): number {
  return (nowMs / 1000) % WATER_LOOP_S;
}

const vec3 = (c: Rgb) => `vec3(${c.map((v) => v.toFixed(4)).join(", ")})`;
const f = (v: number) => v.toFixed(5);

/** The plugin's code for the palette's own colours (built once). */
let DEFAULT_CODE: Record<string, string> | undefined;

/** The fragment code (exported for tests). */
export function waterFragment(colours = waterColours()): Record<string, string> {
  const w1 = (2 * Math.PI) / WAVE_PERIODS[0];
  const w2 = (2 * Math.PI) / WAVE_PERIODS[1];
  return {
    CUSTOM_FRAGMENT_UPDATE_DIFFUSE: `
#ifdef LAKE_WATER
{
#ifdef VERTEXCOLOR
  float lakeDepth = clamp(pow(vColor.r, 2.2), 0.0, 1.0);
#else
  float lakeDepth = 1.0;
#endif
#ifdef TOON_WORLD_SHADE
  toonShadeX = 1.0;
#endif
  vec3 lakeCol = mix(${vec3(colours.shallow)}, ${vec3(colours.deep)}, smoothstep(0.0, 1.0, lakeDepth));
  vec2 lakeP = vPositionW.xz * 0.0625;
  float lakeW = sin(lakeP.x * 1.9 + lakeP.y * 0.6 + lakeTime * ${f(w1)}) * 0.55
              + sin(lakeP.y * 2.7 - lakeP.x * 0.9 - lakeTime * ${f(w2)}) * 0.45;
  float lakeAa = fwidth(lakeW);
  float lakeBand = smoothstep(0.78 - lakeAa, 0.78 + lakeAa, lakeW) * (1.0 - smoothstep(0.15, 0.45, lakeAa));
  lakeCol = mix(lakeCol, ${vec3(colours.band)}, lakeBand * mix(0.8, 0.45, lakeDepth));
  lakeCol = mix(lakeCol, ${vec3(colours.band)}, (1.0 - smoothstep(0.03, 0.12, lakeDepth)) * 0.65);
  baseColor = vec4(lakeCol, baseColor.a);
}
#endif
`,
  };
}

/** The colour and bands of `lake_water` (see the top of the file). */
export class WaterPlugin extends MaterialPluginBase {
  private readonly code: Record<string, string> | undefined;
  /** Seconds, wrapped (`waterTime`), written by `LakeWater.tick`. */
  time = 0;

  constructor(material: Material, colours = waterColours()) {
    super(material, "LakeWater", 170, { LAKE_WATER: false }, true, true);
    this.code = waterFragment(colours);
  }

  override getClassName(): string {
    return "WaterPlugin";
  }

  override isCompatible(shaderLanguage: ShaderLanguage): boolean {
    return shaderLanguage === ShaderLanguage.GLSL;
  }

  override prepareDefines(defines: MaterialDefines): void {
    defines["LAKE_WATER"] = true;
  }

  override getUniforms(): { ubo: { name: string; size: number; type: string }[]; fragment: string } {
    return { ubo: [{ name: "lakeTime", size: 1, type: "float" }], fragment: "uniform float lakeTime;" };
  }

  override bindForSubMesh(uniformBuffer: UniformBuffer): void {
    uniformBuffer.updateFloat("lakeTime", this.time);
  }

  override getCustomCode(shaderType: string): { [pointName: string]: string } | null {
    // Babylon asks for the points inside the base constructor, before `code` is set: the default colours' code has the same points.
    return shaderType === "fragment" ? (this.code ?? (DEFAULT_CODE ??= waterFragment())) : null;
  }
}

/** The water's toon row: its own, else the greybox `water`'s. */
function waterRow(): ReturnType<typeof worldRamp> {
  return worldRamp(WORLD_TOON.materials[WATER_MATERIAL] ? WATER_MATERIAL : "water");
}

/** The shared `lake_water` material, handed to the cells that name it (cells.ts). */
export class LakeWater implements SharedMaterials {
  private mat: StandardMaterial | null = null;
  private plugin: WaterPlugin | null = null;

  constructor(private readonly scene: Scene) {}

  take(name: string): Material | null {
    if (name !== WATER_MATERIAL) return null;
    if (!this.mat) {
      const scene = this.scene;
      const mat = new StandardMaterial(WATER_MATERIAL, scene);
      mat.diffuseColor = Color3.White();
      mat.specularColor = Color3.Black();
      mat.maxSimultaneousLights = WORLD_MAX_LIGHTS;
      mat.sideOrientation = scene.useRightHandedSystem ? Material.CounterClockWiseSideOrientation : Material.ClockWiseSideOrientation;
      new ToonPlugin(mat, waterRow());
      this.plugin = new WaterPlugin(mat);
      this.mat = mat;
    }
    return this.mat;
  }

  /** Whether the lake's material has been built (a lake cell loaded). */
  get built(): boolean {
    return this.mat !== null;
  }

  /** Moves the bands on (once a frame, before the render). */
  tick(nowMs: number): void {
    if (this.plugin) this.plugin.time = waterTime(nowMs);
  }

  dispose(): void {
    this.mat?.dispose(false, true);
    this.mat = null;
    this.plugin = null;
  }
}
