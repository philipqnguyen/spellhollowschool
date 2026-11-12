// On the client (docs/architecture.md): resident cells,
// the terrain material `grounds_terrain` (the turf mixed toward the palette's
// path and rock colours by the TEXCOORD_1 mask), the lake's toon water
// `lake_water`, the sun's shadow on the terrain at High, and the view
// outdoors (fog and far plane). Cells are streamed for real (the glTF loader
// on a NullEngine) from small files with the export's names.

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import "@babylonjs/core/Shaders/default.fragment";
import "@babylonjs/core/Shaders/default.vertex";
import texturesJson from "../../../../data/art/castle_textures.json";
import { CellStreamer } from "../cells";
import { planCells, type CellInfo } from "../cellplan";
import { fogRange, OUTDOOR_FOG, receivesCharacterShadows, receivesSun } from "../scene";
import { worldRamp, WORLD_TOON } from "../toon/palette";
import { CastleLibrary, parseTexturesJson, textureBase, TEXTURE_ALIASES, toonRowName } from "./materials";
import { paletteRgb, TERRAIN_MATERIAL, TERRAIN_TURF, terrainColours, terrainFragment, TerrainPlugin } from "./terrain";
import { LakeWater, WATER_LOOP_S, WATER_MATERIAL, waterColours, waterFragment, waterTime, WaterPlugin } from "./water";
import { gltfFixtureUrl, type FixtureNode } from "./gltf-fixture.test-util";

const PALETTE = texturesJson.palette as Record<string, unknown>;

let engine: NullEngine;
let scene: Scene;

beforeEach(() => {
  engine = new NullEngine();
  scene = new Scene(engine);
  scene.useRightHandedSystem = true;
});

afterEach(() => {
  scene.dispose();
  engine.dispose();
});

const tick = () => new Promise((r) => setTimeout(r, 0));

const info = (id: string, nodes: FixtureNode[], x: number, resident = false): CellInfo => ({
  id,
  file: gltfFixtureUrl(nodes),
  bounds: { min: [x - 10, 0, -10], max: [x + 10, 10, 10] },
  level: 0,
  ...(resident ? { resident: true as const } : {}),
});

function library(): CastleLibrary {
  const lib = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0 });
  lib.useSpecs(parseTexturesJson({ version: 1, materials: { castle_floor_grass: { base: "grass_turf.png", lo: "grass_turf_lo.png", px: [1024, 1024], map: "world" } } }));
  return lib;
}

const meshNamed = (s: CellStreamer, name: string): AbstractMesh => {
  const m = [...s.meshes].find((x) => x.name === name);
  if (!m) throw new Error(`no mesh ${name}`);
  return m;
};

/** The fragment injection points Babylon registered for a material's plugins. */
function injectionPoints(mat: StandardMaterial): string[] {
  const pm = mat.pluginManager as unknown as { _codeInjectionPoints?: { fragment?: Record<string, boolean> } } | undefined;
  return Object.keys(pm?._codeInjectionPoints?.fragment ?? {});
}

/** Babylon's standard fragment shader with `inject` at its points. */
function injected(points: Record<string, string>): string {
  let code = ShaderStore.ShadersStore["defaultPixelShader"]!;
  for (const [point, inject] of Object.entries(points)) {
    expect(code.includes(`#define ${point}`), point).toBe(true);
    code = code.replace(`#define ${point}`, `\n${inject}`);
  }
  return code;
}

describe("resident cells (the manifest's `resident`)", () => {
  const radii = { load: 60, unload: 90 };
  const box = (id: string, x: number, resident = false): CellInfo => ({ id, file: `${id}.glb`, bounds: { min: [x, 0, 0], max: [x + 10, 10, 10] }, level: 0, ...(resident ? { resident: true as const } : {}) });
  const cells = [box("near", 0), box("far", 500), box("grounds_far", 900, true), box("lake", 300, true)];

  it("load with the cells in reach, nearest first, however far", () => {
    expect(planCells(cells, [5, 5, 5], new Set(), radii).load.map((c) => c.id)).toEqual(["near", "lake", "grounds_far"]);
  });

  it("are never unloaded", () => {
    const plan = planCells(cells, [2000, 5, 5], new Set(["near", "lake", "grounds_far"]), radii);
    expect(plan.unload).toEqual(["near"]);
    expect(plan.load.map((c) => c.id)).toEqual([]);
  });

  it("stay loaded in the streamer when the player walks away, and the foliage is not in the camera's rays", async () => {
    const lib = library();
    const infos = [
      info("hall", [{ name: "hall.wall", material: "wall" }], 0),
      info("grounds_r0_c0", [{ name: "grounds_r0_c0.grounds_terrain", material: TERRAIN_MATERIAL, uv2: true, colour: 0.7 }], 400, true),
      info("foliage", [{ name: "foliage.trees", material: "foliage_leaf" }], 500, true),
    ];
    const s = new CellStreamer(scene, "", infos, { load: 70, unload: 100 }, { onLoaded: () => undefined, onUnloading: () => undefined }, { library: lib });
    s.update([0, 0, 0]);
    await s.whenSettled();
    expect(s.loadedCount).toBe(3);
    s.update([2000, 0, 0]);
    await s.whenSettled();
    expect(s.loadedCount).toBe(2);
    expect(s.isCellShown("grounds_r0_c0")).toBe(true);
    const terrain = meshNamed(s, "grounds_r0_c0.grounds_terrain");
    expect(s.cameraMeshes.has(terrain)).toBe(true);
    expect([...s.cameraMeshes].some((m) => m.name.startsWith("foliage"))).toBe(false);
    s.dispose();
    lib.dispose();
  });
});

describe("the terrain material (grounds_terrain)", () => {
  it("is the turf of castle_floor_grass under its own name, with the mask mix", async () => {
    expect(TEXTURE_ALIASES.get(TERRAIN_MATERIAL)).toBe(TERRAIN_TURF);
    expect(textureBase(TERRAIN_MATERIAL)).toBe("castle_floor_grass");
    const lib = library();
    expect(lib.has(TERRAIN_MATERIAL)).toBe(true);
    const s = new CellStreamer(scene, "", [info("grounds_r0_c3", [{ name: "grounds_r0_c3.grounds_terrain", material: TERRAIN_MATERIAL, uv2: true, colour: 0.6 }], 0, true)], { load: 70, unload: 100 }, { onLoaded: () => undefined, onUnloading: () => undefined }, { library: lib });
    s.update([0, 0, 0]);
    await s.whenSettled();
    await tick(); // textures
    const mesh = meshNamed(s, "grounds_r0_c3.grounds_terrain");
    const mat = mesh.material as StandardMaterial;
    expect(mat.name).toBe(TERRAIN_MATERIAL);
    const grass = lib.take(TERRAIN_TURF)!;
    expect(mat).not.toBe(grass);
    expect(mat.diffuseTexture).toBe(grass.diffuseTexture);
    expect(mat.pluginManager?.getPlugin("Terrain")).toBeInstanceOf(TerrainPlugin);
    // Babylon collects the injection points when the plugin is added (inside its constructor).
    expect(injectionPoints(mat)).toContain("CUSTOM_FRAGMENT_UPDATE_ALPHA");
    expect(mat.pluginManager?.getPlugin("Toon")).toBeTruthy();
    lib.release(TERRAIN_TURF);
    // One reference from the cell, one from the clone on its base.
    expect(lib.refs(TERRAIN_MATERIAL)).toBe(1);
    expect(lib.refs(TERRAIN_TURF)).toBe(1);
    // The mask reaches the fragment only on a mesh with TEXCOORD_1.
    const plugin = mat.pluginManager!.getPlugin("Terrain") as TerrainPlugin;
    const d = new MaterialDefines();
    d["UV2"] = true;
    plugin.prepareDefines(d, scene, mesh);
    expect(d["TERRAIN_MASK"]).toBe(true);
    expect(d["MAINUV2"]).toBe(true);
    const plain = new MaterialDefines();
    plain["UV2"] = false;
    plugin.prepareDefines(plain, scene, mesh);
    expect(plain["TERRAIN_MASK"]).toBe(false);
    s.dispose();
    lib.dispose();
  });

  it("lights with its own toon row when the palette has one, else the turf's", () => {
    const own = !!WORLD_TOON.materials[TERRAIN_MATERIAL];
    expect(toonRowName(TERRAIN_MATERIAL)).toBe(own ? TERRAIN_MATERIAL : TERRAIN_TURF);
    expect(toonRowName("castle_wall_b")).toBe("castle_wall_b");
  });

  it("takes the palette's path and rock colours (parcel_tan and statue_stone until the Terrain rows exist)", () => {
    const c = terrainColours(PALETTE);
    expect(c.path).toEqual(paletteRgb(["grounds_path", "parcel_tan"], PALETTE));
    expect(c.rock).toEqual(paletteRgb(["grounds_rock", "statue_stone"], PALETTE));
    expect(terrainColours({ grounds_path: "#FF0000", grounds_rock: "#0000FF", parcel_tan: "#00FF00" })).toEqual({ path: [1, 0, 0], rock: [0, 0, 1] });
    expect(paletteRgb(["missing"], {})).toBeNull();
  });

  it("mixes where the turf texel is read, before the baked shade and the light", () => {
    const code = injected(terrainFragment(terrainColours(PALETTE)));
    const mix = code.indexOf("terrainW");
    expect(mix).toBeGreaterThan(code.indexOf("baseColor=TEXRD(diffuseSampler"));
    expect(mix).toBeLessThan(code.indexOf("baseColor.rgb*=vColor.rgb;"));
  });

  it("takes the sun's shadow at High and the characters' shadows", () => {
    const mesh = { material: { name: TERRAIN_MATERIAL } } as unknown as AbstractMesh;
    expect(receivesSun(mesh)).toBe(true);
    expect(receivesCharacterShadows(mesh)).toBe(true);
    expect(receivesSun({ material: { name: WATER_MATERIAL } } as unknown as AbstractMesh)).toBe(false);
  });
});

describe("the lake's toon water (lake_water)", () => {
  it("is one shared toon material with the water plugin, handed to the lake cell", async () => {
    const water = new LakeWater(scene);
    expect(water.take("castle_floor_grass")).toBeNull();
    const s = new CellStreamer(scene, "", [info("lake", [{ name: "lake.lake_water", material: WATER_MATERIAL, colour: 0.5 }], 0, true)], { load: 70, unload: 100 }, { onLoaded: () => undefined, onUnloading: () => undefined }, { water });
    s.update([0, 0, 0]);
    await s.whenSettled();
    const mat = meshNamed(s, "lake.lake_water").material as StandardMaterial;
    expect(mat.name).toBe(WATER_MATERIAL);
    expect(mat).toBe(water.take(WATER_MATERIAL));
    expect(water.built).toBe(true);
    const plugin = mat.pluginManager?.getPlugin("LakeWater") as WaterPlugin;
    expect(plugin).toBeInstanceOf(WaterPlugin);
    expect(injectionPoints(mat)).toContain("CUSTOM_FRAGMENT_UPDATE_DIFFUSE");
    expect(mat.pluginManager?.getPlugin("Toon")).toBeTruthy();
    water.tick(1000 * (WATER_LOOP_S + 12.5));
    expect(plugin.time).toBeCloseTo(12.5, 6);
    // Disposing the cell leaves the shared material.
    s.dispose();
    expect(scene.materials.includes(mat)).toBe(true);
    water.dispose();
  });

  it("takes the palette's lake colours, `water` until the Terrain rows exist", () => {
    const water = paletteRgb(["water"], PALETTE)!;
    const c = waterColours(PALETTE);
    expect(c.shallow).toEqual(paletteRgb(["lake_shallow"], PALETTE) ?? water);
    expect(c.deep).toEqual(paletteRgb(["lake_deep"], PALETTE) ?? water);
    expect(c.band).toEqual(paletteRgb(["lake_band"], PALETTE) ?? water);
    expect(waterColours({ water: "#000000", lake_band: "#FFFFFF" })).toEqual({ shallow: [0, 0, 0], deep: [0, 0, 0], band: [1, 1, 1] });
  });

  it("sets the albedo after the vertex colour (depth, not shade) and moves its bands with a wrapping time", () => {
    const code = injected(waterFragment(waterColours(PALETTE)));
    expect(code.indexOf("lakeDepth")).toBeGreaterThan(code.indexOf("baseColor.rgb*=vColor.rgb;"));
    expect(code).toContain("toonShadeX = 1.0;");
    expect(waterTime(0)).toBe(0);
    expect(waterTime(WATER_LOOP_S * 1000)).toBe(0);
    expect(waterTime(1500)).toBeCloseTo(1.5, 9);
    // Both waves' periods divide the loop, so the wrap is seamless.
    for (const p of [60, 80]) expect(WATER_LOOP_S % p).toBe(0);
  });

  it("lights with its own toon row, else the greybox water's", () => {
    expect(worldRamp("water")).toBeTruthy();
  });
});

describe("the view outdoors (fogRange)", () => {
  it("is as before indoors", () => {
    expect(fogRange(220, false)).toEqual({ start: 77, end: 220, far: 270 });
    expect(fogRange(140, false, 0)).toEqual({ start: 49, end: 140, far: 190 });
  });

  it("reaches the mountains by day and closes in to a mist at night, scaled by the quality's view distance", () => {
    const day = fogRange(220, true, 1);
    const night = fogRange(220, true, 0);
    expect(day).toEqual({ start: 220 * OUTDOOR_FOG.dayStart, end: 220 * OUTDOOR_FOG.dayEnd, far: 220 * OUTDOOR_FOG.dayEnd + 50 });
    expect(night.end).toBeLessThan(day.end);
    expect(night.start).toBeLessThan(day.start);
    // The castle from the lake shore (about 250 m) stays readable at night at Medium.
    expect(night.end).toBeGreaterThan(500);
    expect(fogRange(320, true, 1).end).toBeGreaterThan(day.end);
    const dusk = fogRange(220, true, 0.5);
    expect(dusk.end).toBeGreaterThan(night.end);
    expect(dusk.end).toBeLessThan(day.end);
    expect(fogRange(220, true, Number.NaN)).toEqual(day);
  });
});
