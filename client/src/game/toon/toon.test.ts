import { describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import "@babylonjs/core/Shaders/default.fragment";
import "@babylonjs/core/Shaders/default.vertex";
import { qualityProfile, QUALITY_HINTS } from "../../settings";
import {
  BASE_RAMP,
  CHARACTER_REGIONS,
  CHARACTER_TOON,
  finishOfToonAlpha,
  packRamp,
  packRegionTable,
  parseCharacterRows,
  regionIndex,
  regionOfAlpha,
  REGION_COUNT,
  resolveRamp,
  shoulderCurve,
  toonAlpha,
  worldRamp,
  WORLD_TOON,
} from "./palette";
import { setToonOutlineMask, toonFragmentPoints, toonOutlineMask, ToonPlugin } from "./material";
import { decodeOutlineAlpha, frameAlpha, postPasses } from "./post";
import { emissiveSrgb } from "./convert";
import { nearestTorches, torchLevel, TORCH_LIGHTS, type Torch } from "./torches";
import { castleTorches } from "../zones";
import { vertexRegion } from "../characters/assemble";

const luma = (c: readonly number[]) => 0.3 * c[0]! + 0.59 * c[1]! + 0.11 * c[2]!;

describe("toon ramp tables", () => {
  it("resolves partial rows over the base and clamps them", () => {
    const r = resolveRamp({ threshold: 4, softness: -1, shadowTint: [2, 0.5, -1], outline: Number.NaN });
    expect(r.threshold).toBe(1);
    expect(r.softness).toBeGreaterThan(0);
    expect(r.shadowTint).toEqual([1, 0.5, 0]);
    expect(r.outline).toBe(0);
    expect(r.rim).toBe(BASE_RAMP.rim);
  });

  it("keeps the soft step narrow, skin shadows warm and cloth shadows cool, never grey", () => {
    for (const name of CHARACTER_REGIONS) {
      const r = resolveRamp(CHARACTER_TOON[name]);
      if (name === "flat") continue;
      // Soft but narrow: a total step of 0.08-0.12 (docs/art/character-adoption.md section 5).
      expect(r.softness * 2).toBeGreaterThanOrEqual(0.05);
      expect(r.softness * 2).toBeLessThanOrEqual(0.12);
      expect(r.shadowLight).toBeGreaterThan(0.4);
      const [red, green, blue] = r.shadowTint;
      if (name === "skin" || name === "face") {
        // Warm: red leads, pink-orange.
        expect(red).toBeGreaterThan(green);
        expect(green).toBeGreaterThan(blue - 0.05);
      } else if (name !== "metal") {
        // Cool: blue leads; tinted, not a flat grey.
        expect(blue).toBeGreaterThan(red);
        expect(Math.max(red, green, blue) - Math.min(red, green, blue)).toBeGreaterThan(0.05);
      }
      // A rim on both bands and the hemispheric lift on every lit region.
      expect(r.rim).toBeGreaterThan(0.1);
      expect(r.skyLift).toBeGreaterThan(0);
      expect(r.aoFloor).toBeLessThan(1);
    }
    // Creases fall into the shadow band on cloth and hair more than on skin; faces stay clean (no rim noise).
    const t = (n: (typeof CHARACTER_REGIONS)[number]) => resolveRamp(CHARACTER_TOON[n]);
    expect(t("primary").shadeShift).toBeGreaterThan(t("skin").shadeShift);
    expect(t("hair").shadeShift).toBeGreaterThan(t("skin").shadeShift);
    expect(t("face").rim).toBeLessThan(t("skin").rim);
    // Only hair has the highlight band; only metal a crisp highlight; hair lines thinner than cloth.
    for (const n of CHARACTER_REGIONS) {
      expect(t(n).band > 0).toBe(n === "hair");
      expect(t(n).specular > 0).toBe(n === "metal");
    }
    expect(t("hair").outline).toBeLessThan(t("primary").outline);
    expect(t("face").outline).toBeLessThan(t("skin").outline);
    // The rows come from the art data (data/art/toon_palette.json `characters`).
    expect(parseCharacterRows({ skin: { rim: 0.6 } }).skin.rim).toBe(0.6);
    expect(parseCharacterRows(undefined).face).toEqual({});
  });

  it("finds world rows by exact name, then the longest prefix, then the default", () => {
    expect(worldRamp("wall").shadowTint).toEqual(resolveRamp(WORLD_TOON.materials.wall).shadowTint);
    expect(worldRamp("floor_room").shadowLight).toBe(resolveRamp(WORLD_TOON.materials["floor_*"]).shadowLight);
    expect(worldRamp("glow_sun").threshold).toBe(-1);
    expect(worldRamp("prop_glass").outline).toBe(0);
    expect(worldRamp("something_new")).toEqual(resolveRamp(WORLD_TOON.default));
    // Furniture models (`furniture_<asset>`) and the stand-in block (`furniture`) share the art side's row.
    const furniture = resolveRamp(WORLD_TOON.materials["furniture*"], resolveRamp(WORLD_TOON.default));
    expect(WORLD_TOON.materials["furniture*"]).toBeDefined();
    expect(worldRamp("furniture_armchair_velvet_a")).toEqual(furniture);
    expect(worldRamp("furniture")).toEqual(furniture);
    const table = { default: {}, materials: { "a*": { rim: 0.1 }, "ab*": { rim: 0.2 } } };
    expect(worldRamp("abc", table).rim).toBe(0.2);
    expect(worldRamp("ax", table).rim).toBe(0.1);
  });

  it("packs rows into the shader's three vec4 per region", () => {
    const t = packRegionTable();
    expect(t.a.length).toBe(REGION_COUNT * 4);
    const skin = resolveRamp(CHARACTER_TOON.skin);
    const i = regionIndex("skin") * 4;
    expect([...t.a.slice(i, i + 4)].map((v) => Number(v.toFixed(4)))).toEqual([...skin.shadowTint, skin.shadowLight].map((v) => Number(v.toFixed(4))));
    expect(t.b[i]).toBeCloseTo(skin.threshold);
    expect(t.b[i + 1]).toBeCloseTo(skin.softness);
    expect(t.c[i]).toBeCloseTo(skin.outline);
    const a = new Float32Array(4);
    const b = new Float32Array(4);
    const c = new Float32Array(4);
    packRamp(resolveRamp({ specular: 0.5, saturation: 0.25 }), a, b, c);
    expect(b[3]).toBeCloseTo(0.5);
    expect(c[1]).toBeCloseTo(0.25);
  });

  it("rolls highlights off softly instead of clipping", () => {
    expect(shoulderCurve(0.5)).toBe(0.5);
    expect(shoulderCurve(0.86)).toBeCloseTo(0.86);
    expect(shoulderCurve(1)).toBeLessThan(1);
    expect(shoulderCurve(3)).toBeLessThan(1);
    expect(shoulderCurve(1.2)).toBeGreaterThan(shoulderCurve(1));
  });
});

describe("the character vertex byte", () => {
  it("round-trips region and finish", () => {
    for (let r = 0; r < REGION_COUNT; r++) {
      for (const f of ["none", "shimmer", "colour_shift"] as const) {
        const a = toonAlpha(r, f);
        expect(a).toBeGreaterThanOrEqual(0);
        expect(a).toBeLessThanOrEqual(255);
        expect(regionOfAlpha(a)).toBe(r);
        expect(finishOfToonAlpha(a)).toBe(f);
      }
    }
    // Bytes from before the toon look (255) are neutral, unfinished.
    expect(regionOfAlpha(255)).toBe(regionIndex("neutral"));
    expect(finishOfToonAlpha(255)).toBe("none");
  });

  it("picks regions from the tint mask, the file's hints and the part", () => {
    const mask = (r: number, g: number, b: number) => new Float32Array([r, g, b]);
    expect(vertexRegion("base", { mask: mask(1, 0, 0) }, 0, false)).toBe(regionIndex("skin"));
    expect(vertexRegion("base", { mask: mask(0, 1, 0) }, 0, false)).toBe(regionIndex("hair"));
    expect(vertexRegion("base", { mask: mask(0, 0, 0) }, 0, false)).toBe(regionIndex("flat"));
    expect(vertexRegion("base", { mask: null }, 0, true)).toBe(regionIndex("flat"));
    expect(vertexRegion("hair", { mask: null }, 0, false)).toBe(regionIndex("hair"));
    expect(vertexRegion("body", { mask: mask(0, 0.7, 0.3) }, 0, false)).toBe(regionIndex("secondary"));
    expect(vertexRegion("body", { mask: mask(0, 0, 1) }, 0, false)).toBe(regionIndex("trim"));
    expect(vertexRegion("shoes", { mask: mask(0, 0, 0) }, 0, false)).toBe(regionIndex("neutral"));
    expect(vertexRegion("shoes", { mask: mask(0, 0, 0), metal: new Uint8Array([1]) }, 0, false)).toBe(regionIndex("metal"));
    expect(vertexRegion("hat", { mask: mask(1, 0, 0), toon: "metal" }, 0, false)).toBe(regionIndex("metal"));
  });
});

describe("the toon material plugin", () => {
  const engine = new NullEngine();
  const scene = new Scene(engine);
  const mesh = CreateBox("box", {}, scene);

  it("turns on the toon defines, and the outline mask only for opaque materials", () => {
    const mat = new StandardMaterial("wall", scene);
    const plugin = new ToonPlugin(mat, worldRamp("wall"));
    const defines = new MaterialDefines();
    setToonOutlineMask(null);
    plugin.prepareDefines(defines, scene, mesh);
    expect(defines["TOON"]).toBe(true);
    expect(defines["CUSTOMUSERLIGHTING"]).toBe(true);
    expect(defines["TOON_REGIONS"]).toBe(false);
    expect(defines["TOON_OUTLINE_MASK"]).toBe(false);
    setToonOutlineMask(scene);
    expect(toonOutlineMask(scene)).toBe(true);
    plugin.prepareDefines(defines, scene, mesh);
    expect(defines["TOON_OUTLINE_MASK"]).toBe(true);
    // Another scene (the wardrobe's preview, a see-through canvas) keeps its alpha at 1.
    const preview = new Scene(engine);
    const previewMat = new StandardMaterial("preview-character", preview);
    const previewPlugin = new ToonPlugin(previewMat, "regions");
    const previewDefines = new MaterialDefines();
    previewPlugin.prepareDefines(previewDefines, preview, CreateBox("preview-box", {}, preview));
    expect(toonOutlineMask(preview)).toBe(false);
    expect(previewDefines["TOON_OUTLINE_MASK"]).toBe(false);
    preview.dispose();
    mat.alpha = 0.5; // blended: its alpha is real alpha
    plugin.prepareDefines(defines, scene, mesh);
    expect(defines["TOON_OUTLINE_MASK"]).toBe(false);
    setToonOutlineMask(null);

    const chars = new StandardMaterial("characters", scene);
    const regions = new ToonPlugin(chars, "regions");
    regions.prepareDefines(defines, scene, mesh);
    expect(defines["TOON_REGIONS"]).toBe(true);
    expect(regions.getUniforms().ubo.map((u) => u.name)).toEqual(["toonRegionA", "toonRegionB", "toonRegionC", "toonRegionD", "toonRegionE", "toonEyeColours", "toonHairColours", "toonKeyDir", "toonFaceParams", "toonDrape"]);
    // The shade layer's attribute, only for meshes that carry it.
    const attrs: string[] = [];
    regions.getAttributes(attrs, scene, mesh);
    expect(attrs).toEqual([]);
    expect(defines["TOON_SHADE"]).toBe(false);
    expect(defines["TOON_KEY_SHADOW"]).toBe(true);
    // The sitting drape (skirts and robes): only for meshes that carry it, added before skinning.
    expect(defines["TOON_DRAPE"]).toBe(false);
    expect(regions.getCustomCode("vertex")!["CUSTOM_VERTEX_UPDATE_POSITION"]).toContain("positionUpdated += toonDrapeChair * toonDrape.x + toonDrapeFloor * toonDrape.y + toonDrapeRest * toonDrape.z");
    // Its weights are set per mesh drawn: Babylon calls the plugin's hard bind only when it registered for the extra events.
    expect(regions.registerForExtraEvents).toBe(true);
    expect(plugin.registerForExtraEvents).toBe(false);
    type HardBind = Parameters<ToonPlugin["hardBindForSubMesh"]>;
    const sub = { getMesh: () => mesh, effect: null } as unknown as HardBind[3];
    const bound: number[][] = [];
    const ubo = { updateFloat4: (name: string, ...v: number[]) => name === "toonDrape" && bound.push(v) } as unknown as HardBind[0];
    const hard = regions.hardBindForSubMesh.bind(regions);
    regions.hardBindForSubMesh = (_u, s, _e, m) => hard(ubo, s, { supportsUniformBuffers: true } as unknown as HardBind[2], m);
    mesh.metadata = { toonDrape: new Float32Array([0.25, 0.5, 0.75, 0]) };
    // What the material calls on every draw (StandardMaterial.bindForSubMesh).
    (chars as unknown as { _callbackPluginEventHardBindForSubMesh: (e: unknown) => void })._callbackPluginEventHardBindForSubMesh({ subMesh: sub });
    expect(bound).toEqual([[0.25, 0.5, 0.75, 0]]);
    mesh.metadata = null;
    mesh.metadata = { toonKeyShadow: false };
    regions.prepareDefines(defines, scene, mesh);
    expect(defines["TOON_KEY_SHADOW"]).toBe(false);
    mesh.metadata = null;
    expect(regions.getUniforms().ubo[0]!.arraySize).toBe(REGION_COUNT);
    expect(plugin.getUniforms().ubo.map((u) => u.name)).toEqual(["toonMatA", "toonMatB", "toonMatC", "toonMatD", "toonMatE", "toonMatF"]);
  });

  it("hooks points that exist in this Babylon's shaders", () => {
    const fragment = ShaderStore.ShadersStore["defaultPixelShader"]!;
    const lights = ShaderStore.IncludesShadersStore["lightFragment"]!;
    expect(fragment).toBeTruthy();
    for (const p of toonFragmentPoints()) {
      if (p.startsWith("!")) {
        // A regular expression: the light loop's tail, or a line of each light's block.
        const rx = new RegExp(p.slice(1));
        expect(rx.test(fragment) || rx.test(lights)).toBe(true);
      } else if (p.startsWith("CUSTOM_LIGHT")) {
        expect(lights).toContain("#define CUSTOM_LIGHT{X}_COLOR");
      } else {
        expect(fragment).toContain(`#define ${p}`);
      }
    }
    expect(lights).toContain("computeCustomDiffuseLighting(info,diffuseBase,shadow)");
    expect(lights).toContain("vec4 diffuse{X}=light{X}.vLightDiffuse;");
  });
});

describe("quality levels and screen passes", () => {
  it("turns the character outlines and bloom off on Low, on above", () => {
    const low = qualityProfile("low", 2);
    expect(low.outlines).toBe(false);
    expect(low.bloom).toBe(false);
    expect(postPasses(low)).toEqual([]);
    const med = qualityProfile("medium", 2);
    expect(med.outlines).toBe(true);
    expect(postPasses(med).at(-1)).toBe("final");
    const high = qualityProfile("high", 2);
    expect(high.outlines && high.bloom && high.shadows).toBe(true);
    // Castle D1: the SSAO passes on High only.
    expect(low.ssao || med.ssao).toBe(false);
    expect(high.ssao).toBe(true);
    expect(postPasses(high)).toEqual(["ssao", "ssaoBlur", "bright", "blur", "final"]);
    expect(postPasses(med)).toEqual(["bright", "blur", "final"]);
    // No screen-space outline any more (9.2): SSAO reads the scene target's depth on its own.
    expect(postPasses({ bloom: false, ssao: true })).toEqual(["ssao", "ssaoBlur", "final"]);
    expect(postPasses({ bloom: false })).toEqual([]);
    // FXAA (without canvas multisampling) runs inside the final pass: no pass of its own.
    expect(postPasses({ bloom: true, fxaa: true })).toEqual(["bright", "blur", "final"]);
    expect(postPasses({ bloom: false, fxaa: true })).toEqual([]);
    // The depth texture and frame alpha only where a pass reads them.
    expect(frameAlpha(med)).toBe(false);
    expect(frameAlpha(high)).toBe(true);

    for (const q of ["low", "medium", "high"] as const) expect(QUALITY_HINTS[q].length).toBeGreaterThan(0);
  });

  it("tells characters from the world in the frame alpha, weights intact", () => {
    // material.ts writes characters as weight / 2 and the world as 0.5 + weight / 2.
    for (const w of [0, 0.42, 0.5, 1]) {
      const world = decodeOutlineAlpha(Math.round((0.5 + w / 2) * 255) / 255);
      expect(world.character).toBe(false);
      expect(world.weight).toBeCloseTo(w, 1);
    }
    for (const r of CHARACTER_REGIONS) {
      const w = resolveRamp(CHARACTER_TOON[r]).outline;
      const ch = decodeOutlineAlpha(Math.round((w / 2) * 255) / 255);
      expect(ch.weight).toBeCloseTo(w, 1);
      if (w > 0.05) expect(ch.character).toBe(true);
    }
    expect(decodeOutlineAlpha(0)).toEqual({ character: false, weight: 0 });
  });

});

describe("converted materials and torches", () => {
  it("keeps an emissive colour's hue when its strength clips", () => {
    const [r, g, b] = emissiveSrgb([1, 0.76, 0.32], 1.8);
    expect(r).toBeCloseTo(1);
    expect(g).toBeLessThan(1);
    expect(b).toBeLessThan(g);
    expect(emissiveSrgb([0.5, 0.5, 0.5], 1)[0]).toBeCloseTo(Math.pow(0.5, 1 / 2.2));
    expect(emissiveSrgb([0, 0, 0], 3)).toEqual([0, 0, 0]);
  });

  it("finds the nearest torches without allocating", () => {
    const t = (x: number): Torch => ({ pos: [x, 0, 0], colour: [1, 0.7, 0.4], intensity: 1, range: 8 });
    const torches = [t(10), t(-3), t(5), t(1), t(40)];
    const out = new Int32Array(3);
    const dist = new Float64Array(3);
    nearestTorches(torches, [0, 0, 0], out, dist);
    expect([...out]).toEqual([3, 1, 2]);
    nearestTorches([t(2)], [0, 0, 0], out, dist);
    expect([...out]).toEqual([0, -1, -1]);
    expect(torchLevel(0)).toBe(1);
    expect(torchLevel(1)).toBeLessThan(0.5);
    expect(TORCH_LIGHTS.low).toBeLessThanOrEqual(TORCH_LIGHTS.high);
  });

  it("reads the castle's torches from the world manifest", () => {
    const cells = [
      { id: "a", file: "a.glb", bounds: { min: [0, 0, 0], max: [1, 1, 1] }, level: 0, lights: [{ pos: [1, 2, 3], color: "#FFA04A", intensity: 12, range: 7 }, { nope: true }] },
      { id: "b", file: "b.glb", bounds: { min: [0, 0, 0], max: [1, 1, 1] }, level: 0 },
    ] as unknown as Parameters<typeof castleTorches>[0];
    const list = castleTorches(cells);
    expect(list).toHaveLength(1);
    expect(list[0]!.pos).toEqual([1, 2, 3]);
    expect(list[0]!.colour[0]).toBeCloseTo(1);
    expect(list[0]!.intensity).toBeCloseTo(0.9);
    expect(list[0]!.range).toBe(7);
    expect(luma(list[0]!.colour)).toBeGreaterThan(0.5);
    expect(list[0]!.ripple).toBeUndefined();
  });

  it("keeps a lake window light's ripple phase", () => {
    const cells = [
      { id: "a", file: "a.glb", bounds: { min: [0, 0, 0], max: [1, 1, 1] }, level: 0, lights: [{ pos: [1, 2, 3], color: "#78AEC8", intensity: 6, range: 9, ripple: 0.42 }] },
    ] as unknown as Parameters<typeof castleTorches>[0];
    expect(castleTorches(cells)[0]!.ripple).toBe(0.42);
  });
});

describe("the castle light mapping", () => {
  it("lights a noon surface to about 1 and keeps night readable with bands", async () => {
    const { castleLight, sceneLights } = await import("./lighting");
    const noon = sceneLights(castleLight(13));
    const night = sceneLights(castleLight(1));
    // A lit, up-facing surface: fill (sky colour) plus key.
    const lit = (s: typeof noon) => s.hemiIntensity * luma(s.hemiSky) + s.sunIntensity * luma(s.sunColour);
    expect(lit(noon)).toBeGreaterThan(0.85);
    expect(lit(noon)).toBeLessThan(1.2);
    expect(lit(night)).toBeGreaterThan(0.35); // night reads by hue, not black
    expect(lit(night)).toBeLessThan(lit(noon));
    // The shadow band (fill + a share of the key, tinted) stays darker than the lit band.
    const wall = resolveRamp(WORLD_TOON.materials.wall);
    for (const s of [noon, night]) {
      const shade = (s.hemiIntensity * luma(s.hemiSky) + s.sunIntensity * luma(s.sunColour) * wall.shadowLight) * luma(wall.shadowTint);
      expect(shade).toBeLessThan(lit(s) * 0.9);
    }
    expect(night.sunColour[2]).toBeGreaterThan(night.sunColour[0]);
  });

  it("fills rooms brighter than the castle's fill (rooms have no sun)", async () => {
    const { castleLight, sceneLights, roomFill } = await import("./lighting");
    const noon = sceneLights(castleLight(13));
    const dim = roomFill([0.56, 0.61, 0.7], [0.38, 0.28, 0.2], 0.35); // the Roarwing dorm's ambient
    expect(dim.intensity).toBeGreaterThan(noon.hemiIntensity);
    expect(dim.intensity * luma(dim.sky)).toBeGreaterThan(0.55);
    expect(roomFill([1, 1, 1], [0.5, 0.5, 0.5], 1).intensity).toBeLessThan(1);
  });
});
