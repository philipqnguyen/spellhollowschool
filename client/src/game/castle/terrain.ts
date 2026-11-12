// The grounds' terrain material `grounds_terrain`
// (docs/art/castle-kit.md "Exterior", docs/architecture.md).
//
// The terrain is drawn with the turf of the castle library's
// `castle_floor_grass` (the paddock's `grass_turf`, world-mapped on
// TEXCOORD_0): the library builds `grounds_terrain` as a second material on
// the turf's textures (castle/materials.ts `TEXTURE_ALIASES`, like the `@ext`
// clones), and this plugin mixes the turf toward two palette colours by the
// mask the export writes in TEXCOORD_1: u is the gravel path's weight, v the
// rock's, turf the rest. COLOR_0 stays the baked shade (greyscale), read by
// the toon plugin as on the castle.
//
// - The colours are `grounds_path` and `grounds_rock` of
//   `data/art/castle_textures.json` `palette`;
//   until they exist, `parcel_tan` and `statue_stone`.
// - The mix runs where the turf texel is read (`CUSTOM_FRAGMENT_UPDATE_ALPHA`,
//   inside Babylon's `#ifdef DIFFUSE`), before the baked shade and the light:
//   path and rock take the same toon light, shade and fog as the turf. The
//   turf's own brightness gives the path and rock a little grain.
// - Only with a mask (the mesh has TEXCOORD_1); anything else on the
//   material draws as turf.
//
// No per-frame work: the colours are constants of the shader.

import { MaterialPluginBase } from "@babylonjs/core/Materials/materialPluginBase";
import { ShaderLanguage } from "@babylonjs/core/Materials/shaderLanguage";
import type { Material } from "@babylonjs/core/Materials/material";
import type { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Scene } from "@babylonjs/core/scene";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import { palette as KIT_PALETTE } from "../../../../data/art/castle_textures.json";
import { hexToRgb01 } from "../../content/catalog";

/** The terrain's material name in the grounds cells. */
export const TERRAIN_MATERIAL = "grounds_terrain";
/** The library material whose turf textures the terrain draws (castle/materials.ts `TEXTURE_ALIASES`). */
export const TERRAIN_TURF = "castle_floor_grass";

type Rgb = [number, number, number];

/** A palette colour by name (sRGB 0..1), the first of `names` the palette has; null when it has none. */
export function paletteRgb(names: readonly string[], palette: Readonly<Record<string, unknown>> = KIT_PALETTE): Rgb | null {
  for (const n of names) {
    const v = palette[n];
    if (typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v)) return hexToRgb01(v);
  }
  return null;
}

/** The path's and the rock's colours (sRGB 0..1), from the palette (fallbacks until the Terrain rows merge). */
export function terrainColours(palette: Readonly<Record<string, unknown>> = KIT_PALETTE): { path: Rgb; rock: Rgb } {
  return {
    path: paletteRgb(["grounds_path", "parcel_tan"], palette) ?? [0.5, 0.5, 0.5],
    rock: paletteRgb(["grounds_rock", "statue_stone"], palette) ?? [0.5, 0.5, 0.5],
  };
}

const vec3 = (c: Rgb) => `vec3(${c.map((v) => v.toFixed(4)).join(", ")})`;

/** The plugin's code for the palette's own colours (built once). */
let DEFAULT_CODE: Record<string, string> | undefined;

/** The fragment code (exported for the shader include test). */
export function terrainFragment(colours = terrainColours()): Record<string, string> {
  return {
    CUSTOM_FRAGMENT_UPDATE_ALPHA: `
#if defined(TERRAIN_MASK) && defined(MAINUV2)
{
  vec2 terrainW = clamp(vMainUV2, 0.0, 1.0);
  float terrainGrain = 0.85 + 0.4 * dot(baseColor.rgb, vec3(0.3, 0.59, 0.11));
  baseColor.rgb = mix(baseColor.rgb, ${vec3(colours.path)} * terrainGrain, terrainW.x);
  baseColor.rgb = mix(baseColor.rgb, ${vec3(colours.rock)} * terrainGrain, terrainW.y);
}
#endif
`,
  };
}

/** The mask mix on `grounds_terrain` (see the top of the file). */
export class TerrainPlugin extends MaterialPluginBase {
  private readonly code: Record<string, string> | undefined;

  constructor(material: Material, colours = terrainColours()) {
    super(material, "Terrain", 160, { TERRAIN_MASK: false }, true, true);
    this.code = terrainFragment(colours);
  }

  override getClassName(): string {
    return "TerrainPlugin";
  }

  override isCompatible(shaderLanguage: ShaderLanguage): boolean {
    return shaderLanguage === ShaderLanguage.GLSL;
  }

  override prepareDefines(defines: MaterialDefines, _scene: Scene, mesh: AbstractMesh): void {
    // UV2 is Babylon's (the mesh has TEXCOORD_1 and a texture needs UVs); MAINUV2 hands it to the fragment.
    const mask = defines["UV2"] === true && mesh.isVerticesDataPresent(VertexBuffer.UV2Kind);
    defines["TERRAIN_MASK"] = mask;
    if (mask) defines["MAINUV2"] = true;
  }

  override getCustomCode(shaderType: string): { [pointName: string]: string } | null {
    // Babylon asks for the points inside the base constructor, before `code` is set: the default colours' code has the same points.
    return shaderType === "fragment" ? (this.code ?? (DEFAULT_CODE ??= terrainFragment())) : null;
  }
}
