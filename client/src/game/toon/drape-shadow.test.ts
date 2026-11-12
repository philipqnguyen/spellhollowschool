import { describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight";
import { ShadowGenerator } from "@babylonjs/core/Lights/Shadows/shadowGenerator";
import "@babylonjs/core/Lights/Shadows/shadowGeneratorSceneComponent";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import type { Effect } from "@babylonjs/core/Materials/effect";
import { Scene } from "@babylonjs/core/scene";
import { shadowMapVertexShader } from "@babylonjs/core/Shaders/shadowMap.vertex";
import { DRAPE_SHADOW_SHADER, drapeShadows, drapeShadowVertexShader } from "./drape-shadow";
import { TOON_DRAPE_KINDS } from "./material";

describe("the sitting drape in the shadow maps", () => {
  it("adds the drape to Babylon's shadow-map shader before skinning", () => {
    const src = drapeShadowVertexShader();
    for (const k of TOON_DRAPE_KINDS) expect(src).toContain(`attribute vec3 ${k};`);
    expect(src).toContain("uniform vec4 toonDrape;");
    const add = src.indexOf("positionUpdated += toonDrapeChair * toonDrape.x + toonDrapeFloor * toonDrape.y + toonDrapeRest * toonDrape.z;");
    expect(add).toBeGreaterThan(src.indexOf("vec3 positionUpdated=position;"));
    expect(add).toBeLessThan(src.indexOf("#include<bonesVertex>"));
    // Everything else is Babylon's shader as it is.
    expect(src.length).toBeGreaterThan(shadowMapVertexShader.shader.length);
    expect(() => drapeShadowVertexShader("void main(void){}")).toThrow();
  });

  it("gives a generator the shader and each caster's own weights", () => {
    const engine = new NullEngine();
    const scene = new Scene(engine);
    const gen = new ShadowGenerator(256, new DirectionalLight("l", new Vector3(0, -1, 0), scene));
    drapeShadows(gen);
    expect(gen.customShaderOptions.shaderName).toBe(DRAPE_SHADOW_SHADER);
    expect(gen.customShaderOptions.attributes).toEqual([...TOON_DRAPE_KINDS]);
    expect(gen.customShaderOptions.uniforms).toEqual(["toonDrape"]);
    expect(ShaderStore.ShadersStore[`${DRAPE_SHADOW_SHADER}VertexShader`]).toBe(drapeShadowVertexShader());
    expect(ShaderStore.ShadersStore[`${DRAPE_SHADOW_SHADER}PixelShader`]).toBeTruthy();
    const set: number[][] = [];
    const effect = { setFloat4: (name: string, ...v: number[]) => name === "toonDrape" && set.push(v) } as unknown as Effect;
    const skirt = CreateBox("skirt", {}, scene);
    skirt.metadata = { toonDrape: new Float32Array([1, 0, 0.5, 0]) };
    const wall = CreateBox("wall", {}, scene);
    gen.onBeforeShadowMapRenderMeshObservable.notifyObservers(skirt);
    gen.onBeforeShadowMapRenderObservable.notifyObservers(effect);
    gen.onBeforeShadowMapRenderMeshObservable.notifyObservers(wall);
    gen.onBeforeShadowMapRenderObservable.notifyObservers(effect);
    expect(set).toEqual([
      [1, 0, 0.5, 0],
      [0, 0, 0, 0],
    ]);
    scene.dispose();
    engine.dispose();
  });
});
