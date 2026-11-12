// The garments' sitting drape in the shadow maps (charlib/drape.py,
// toon/material.ts `TOON_DRAPE_KINDS`): Babylon draws shadow casters with its
// own `shadowMap` shader, which knows nothing of the toon plugin's drape, so
// a seated character's skirt, robe or coat cast the shadow of its standing
// shape, a hoop round the thighs, onto the floor and onto the character's
// own hands and lap. `drapeShadows` gives a shadow generator that shader with
// the drape added before skinning, as the toon material and the outline hull
// add it, weighted per mesh drawn (`metadata.toonDrape`).
//
// The drape attributes are listed for every caster: a mesh without them has
// those attribute arrays disabled (Babylon binds only the buffers a mesh
// has), so they read zero, and its weights are zero too.

import type { ShadowGenerator } from "@babylonjs/core/Lights/Shadows/shadowGenerator";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import { shadowMapPixelShader } from "@babylonjs/core/Shaders/shadowMap.fragment";
import { shadowMapVertexShader } from "@babylonjs/core/Shaders/shadowMap.vertex";
import { drapeOf, TOON_DRAPE_KINDS } from "./material";

/** The shadow shader's name (`<name>VertexShader` / `<name>PixelShader` in the shader store). */
export const DRAPE_SHADOW_SHADER = "toonDrapeShadowMap";

/** Babylon's shadow-map vertex shader with the drape added before skinning; throws if its hooks moved. */
export function drapeShadowVertexShader(source: string = shadowMapVertexShader.shader): string {
  const defs = "#define CUSTOM_VERTEX_DEFINITIONS";
  const start = "{vec3 positionUpdated=position;";
  if (!source.includes(defs) || !source.includes(start)) throw new Error("drape-shadow: Babylon's shadowMap vertex shader changed");
  return source
    .replace(defs, `${TOON_DRAPE_KINDS.map((k) => `attribute vec3 ${k};`).join("\n")}\nuniform vec4 toonDrape;\n${defs}`)
    .replace(start, `${start}\npositionUpdated += ${TOON_DRAPE_KINDS[0]} * toonDrape.x + ${TOON_DRAPE_KINDS[1]} * toonDrape.y + ${TOON_DRAPE_KINDS[2]} * toonDrape.z;`);
}

function register(): void {
  if (ShaderStore.ShadersStore[`${DRAPE_SHADOW_SHADER}VertexShader`]) return;
  ShaderStore.ShadersStore[`${DRAPE_SHADOW_SHADER}VertexShader`] = drapeShadowVertexShader();
  ShaderStore.ShadersStore[`${DRAPE_SHADOW_SHADER}PixelShader`] = shadowMapPixelShader.shader;
}

/** Makes `gen` cast the draped shape of the garments: set right after creating it, before it compiles. */
export function drapeShadows(gen: ShadowGenerator): void {
  register();
  gen.customShaderOptions = { shaderName: DRAPE_SHADOW_SHADER, attributes: [...TOON_DRAPE_KINDS], uniforms: ["toonDrape"] };
  // Babylon notifies the mesh, then the effect it is drawn with, for every caster drawn.
  let drawn: AbstractMesh | null = null;
  gen.onBeforeShadowMapRenderMeshObservable.add((mesh) => {
    drawn = mesh;
  });
  gen.onBeforeShadowMapRenderObservable.add((effect) => {
    const d = drapeOf(drawn);
    effect.setFloat4("toonDrape", d[0]!, d[1]!, d[2]!, 0);
  });
}
