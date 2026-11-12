// Brewed finishes on worn garments (shimmer lacquer and
// colour-shift varnish), drawn without a single extra draw call.
//
// A character is one merged mesh with one shared material (assemble.ts), so
// a finish cannot be a material of its own. Instead each vertex carries its
// garment's finish in the alpha byte of its vertex colour (the material
// never blends by vertex alpha, so the byte is free), shared with the toon
// region: region · 16 + finish · 4, finish 0 none, 1 shimmer, 2 colour-shift
// (toon/palette.ts `toonAlpha`). A material plugin on the character material
// reads it and, per pixel:
//
//   shimmer       twinkling glints scattered over the cloth plus a soft rim
//                 sheen, added on top of the lit colour;
//   colour-shift  an iridescent film over the albedo whose hue turns with
//                 the viewing angle and slowly with time.
//
// Unfinished parts (body, hair, plain gear) skip both branches. The finish is
// part of the appearance key (content/appearance.ts), so a finished look gets
// its own assembled geometry like any dye change. With the toon look the
// glints and sheen add on top of the toon-lit colour (toon/material.ts).

import { MaterialPluginBase } from "@babylonjs/core/Materials/materialPluginBase";
import { ShaderLanguage } from "@babylonjs/core/Materials/shaderLanguage";
import type { Material } from "@babylonjs/core/Materials/material";
import type { UniformBuffer } from "@babylonjs/core/Materials/uniformBuffer";

// The byte codes are dye.ts `finishAlpha` / `finishOfAlpha` over
// toon/palette.ts (kept there so the pure code and its tests need no Babylon).

const VERTEX = {
  CUSTOM_VERTEX_DEFINITIONS: `
varying float vFinish;
`,
  // The byte is region · 16 + finish · 4 (toon/palette.ts `toonAlpha`);
  // vFinish keeps the fragment code's old scale: 1 none, 0.67 shimmer, 0.33 colour-shift.
  CUSTOM_VERTEX_MAIN_END: `
#ifdef VERTEXCOLOR
float finishCode = floor(mod(floor(color.a * 255.0 + 0.5), 16.0) / 4.0);
vFinish = finishCode == 1.0 ? 0.67 : finishCode == 2.0 ? 0.33 : 1.0;
#else
vFinish = 1.0;
#endif
`,
};

const FRAGMENT = {
  CUSTOM_FRAGMENT_DEFINITIONS: `
varying float vFinish;
`,
  // After the albedo is final, before lighting: colour-shift tints it.
  CUSTOM_FRAGMENT_BEFORE_LIGHTS: `
float finishFacing = clamp(dot(normalW, viewDirectionW), 0.0, 1.0);
if (vFinish < 0.5) {
  float filmPhase = (1.0 - finishFacing) * 1.4 + finishTime * 0.12 + dot(vPositionW, vec3(0.35, 0.5, 0.3));
  vec3 film = 0.5 + 0.5 * cos(6.2832 * (vec3(0.0, 0.33, 0.67) + filmPhase));
  float filmLuma = max(dot(baseColor.rgb, vec3(0.3, 0.59, 0.11)), 0.18);
  float filmAmount = 0.35 + 0.4 * (1.0 - finishFacing);
  baseColor.rgb = mix(baseColor.rgb, film * (0.55 + filmLuma), filmAmount);
}
`,
  // After lighting: shimmer adds its glints and sheen as light of its own.
  CUSTOM_FRAGMENT_BEFORE_FOG: `
if (vFinish >= 0.5 && vFinish < 0.9) {
  vec3 glintCell = floor(vPositionW * 60.0);
  float glintSeed = fract(sin(dot(glintCell, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
  float glintTwinkle = pow(max(0.0, sin(finishTime * 2.6 + glintSeed * 60.0)), 10.0);
  float glint = step(0.93, glintSeed) * glintTwinkle;
  float sheen = pow(1.0 - finishFacing, 2.5);
  color.rgb += vec3(1.0, 0.95, 0.82) * (glint * 0.85 + sheen * 0.28);
}
`,
};

/** Seconds for the shader's animation, kept small so float precision lasts. */
function finishClock(): number {
  return (performance.now() / 1000) % 600;
}

/** The finish plugin of one character material (renderer.ts attaches it). */
export class CharacterFinishPlugin extends MaterialPluginBase {
  constructor(material: Material) {
    // Added to the material's plugin list and enabled at once.
    super(material, "CharacterFinish", 200, undefined, true, true);
  }

  override getClassName(): string {
    return "CharacterFinishPlugin";
  }

  override isCompatible(shaderLanguage: ShaderLanguage): boolean {
    return shaderLanguage === ShaderLanguage.GLSL;
  }

  override getUniforms(): { ubo: { name: string; size: number; type: string }[]; fragment: string } {
    return {
      ubo: [{ name: "finishTime", size: 1, type: "float" }],
      fragment: "uniform float finishTime;",
    };
  }

  override bindForSubMesh(uniformBuffer: UniformBuffer): void {
    uniformBuffer.updateFloat("finishTime", finishClock());
  }

  override getCustomCode(shaderType: string): { [pointName: string]: string } | null {
    return shaderType === "vertex" ? VERTEX : shaderType === "fragment" ? FRAGMENT : null;
  }
}
