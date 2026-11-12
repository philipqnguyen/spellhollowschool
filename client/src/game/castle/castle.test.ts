// The castle kit's client side (docs/art/castle-kit.md "Building and checking", Client):
// textures.json parsing, one shared material per name across cells and its
// release, camera colliders hidden and the only meshes ray-cast in kitted
// cells, LOD wiring, per-cell camera.max easing, every castle_* material in
// toon_palette.json, and the ?layout overlay's geometry. Cells are loaded
// for real (the glTF loader on a NullEngine) from small glTF files built
// here, with the node names and extras the export writes.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Ray } from "@babylonjs/core/Culling/ray";
import { FreeCamera } from "@babylonjs/core/Cameras/freeCamera";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import type { Texture } from "@babylonjs/core/Materials/Textures/texture";
import "@babylonjs/core/Shaders/default.fragment";
import "@babylonjs/core/Shaders/default.vertex";
import { CellStreamer } from "../cells";
import type { CellInfo } from "../cellplan";
import { FollowCamera, KIT_WALL_RELIEF } from "../camera";
import { worldRamp, WORLD_TOON } from "../toon/palette";
import { CastleLibrary, isWorldMapped, parseTexturesJson, SKY_DIFFUSE, skyEmission } from "./materials";
import { KitLods, distanceToBox } from "./lod";
import { isCameraCollider, kitLodOf } from "./nodes";
import { cameraMaxFor, DEFAULT_DISTANCE, DEFAULT_MAX_DISTANCE, LOW_MAX_DISTANCE, ZoomLimit } from "./camera-max";
import { layoutLines, surfaceHeightAt, type OverlaySurface } from "./layout-overlay";
import { materials as KIT_MATERIALS } from "../../../../data/art/castle_textures.json";
import { camera_max as KIT_CAMERA } from "../../../../data/world/castle_kit.json";

// ------------------------------------------------------------ fixtures

/** A minimal textures.json for the materials the fixture cells use. */
const TEXTURES = {
  version: 1,
  materials: {
    castle_wall_a: { base: "stone_ashlar_a.png", lo: "stone_ashlar_a_lo.png", px: [1024, 1024] },
    castle_floor_square: { base: "stone_flag_square.png", lo: "stone_flag_square_lo.png", px: [1024, 1024] },
    castle_glass: {
      base: "glass_sheet.png",
      lo: "glass_sheet_lo.png",
      px: [1024, 1024],
      emissive: "glass_sheet.png",
      emissive_lo: "glass_sheet_lo.png",
      tint: "sky",
    },
    castle_props: {
      base: "props_atlas.png",
      lo: "props_atlas_lo.png",
      px: [1024, 1024],
      emissive: "props_atlas_e.png",
      emissive_lo: "props_atlas_e_lo.png",
      emissive_strength: 2.5,
      key: "0123456789ab",
    },
    castle_trim: { base: "trim_castle.png", lo: "trim_castle_lo.png", px: [2048, 2048], medium: "lo" },
  },
};

interface NodeSpec {
  name: string;
  /** Material name; omitted for colliders. */
  material?: string;
  /** A quad facing +z at this z, x and y in [-w, w] (y from 0 to 2w). */
  z: number;
  w?: number;
  extras?: Record<string, unknown>;
}

/** A .glb (as a data: URL) with one quad mesh per node, named and with extras as the export writes them. */
function gltfUrl(nodes: NodeSpec[]): string {
  const materials: { name: string; pbrMetallicRoughness: { baseColorFactor: number[] } }[] = [];
  const matIndex = new Map<string, number>();
  const bytes: number[] = [];
  const views: { buffer: number; byteOffset: number; byteLength: number }[] = [];
  const accessors: object[] = [];
  const meshes: object[] = [];
  const push = (arr: Float32Array | Uint16Array) => {
    const offset = bytes.length;
    bytes.push(...new Uint8Array(arr.buffer));
    while (bytes.length % 4) bytes.push(0);
    views.push({ buffer: 0, byteOffset: offset, byteLength: arr.byteLength });
    return views.length - 1;
  };
  for (const n of nodes) {
    const w = n.w ?? 4;
    const pos = new Float32Array([-w, 0, n.z, w, 0, n.z, w, 2 * w, n.z, -w, 2 * w, n.z]);
    const nor = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]);
    const idx = new Uint16Array([0, 1, 2, 0, 2, 3]);
    const pv = push(pos);
    accessors.push({ bufferView: pv, componentType: 5126, count: 4, type: "VEC3", min: [-w, 0, n.z], max: [w, 2 * w, n.z] });
    const pa = accessors.length - 1;
    accessors.push({ bufferView: push(nor), componentType: 5126, count: 4, type: "VEC3" });
    const na = accessors.length - 1;
    accessors.push({ bufferView: push(idx), componentType: 5123, count: 6, type: "SCALAR" });
    const ia = accessors.length - 1;
    const prim: Record<string, unknown> = { attributes: { POSITION: pa, NORMAL: na }, indices: ia };
    if (n.material) {
      if (!matIndex.has(n.material)) {
        materials.push({ name: n.material, pbrMetallicRoughness: { baseColorFactor: [0.5, 0.45, 0.4, 1] } });
        matIndex.set(n.material, materials.length - 1);
      }
      prim.material = matIndex.get(n.material);
    }
    meshes.push({ name: n.name, primitives: [prim] });
  }
  const gltf = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: nodes.map((_, i) => i) }],
    nodes: nodes.map((n, i) => ({ name: n.name, mesh: i, ...(n.extras ? { extras: n.extras } : {}) })),
    meshes,
    materials,
    accessors,
    bufferViews: views,
    buffers: [{ byteLength: bytes.length }],
  };
  // GLB: header, JSON chunk (space-padded), BIN chunk.
  const json = Buffer.from(JSON.stringify(gltf));
  const jsonPad = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)]);
  const bin = Buffer.from(new Uint8Array(bytes));
  const header = Buffer.alloc(12);
  const total = 12 + 8 + jsonPad.length + 8 + bin.length;
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(total, 8);
  const chunk = (type: number, data: Buffer) => {
    const h = Buffer.alloc(8);
    h.writeUInt32LE(data.length, 0);
    h.writeUInt32LE(type, 4);
    return Buffer.concat([h, data]);
  };
  const glb = Buffer.concat([header, chunk(0x4e4f534a, jsonPad), chunk(0x004e4942, bin)]);
  return `data:model/gltf-binary;base64,${glb.toString("base64")}`;
}

/** A kitted corridor: drawn wall and floor, a collider behind the wall, a LOD for the wall. */
function kittedCell(id: string): NodeSpec[] {
  return [
    { name: `${id}.castle_wall_a`, material: "castle_wall_a", z: -2 },
    { name: `${id}.castle_wall_a_lod1`, material: "castle_wall_a", z: -2, extras: { kit_lod: { base: `${id}.castle_wall_a`, distance: 30 } } },
    { name: `${id}.castle_glass`, material: "castle_glass", z: -2.1, w: 1 },
    { name: `${id}.castle_props`, material: "castle_props", z: -1.9, w: 0.5 },
    { name: `${id}.collider`, z: -3, w: 20, extras: { role: "camera_collider" } },
  ];
}

/** An un-kitted cell as today: greybox materials, no collider (and a castle_* name the library lacks). */
function greyboxCell(id: string): NodeSpec[] {
  return [
    { name: `${id}.wall`, material: "wall", z: -2.5 },
    { name: `${id}.castle_cloth`, material: "castle_cloth", z: -2.4, w: 1 },
  ];
}

const cellInfo = (id: string, file: string, x: number): CellInfo => ({ id, file, bounds: { min: [x - 10, 0, -10], max: [x + 10, 10, 10] }, level: 0 });

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
  vi.useRealTimers();
});

const tick = () => new Promise((r) => setTimeout(r, 0));

async function stream(cells: { id: string; nodes: NodeSpec[]; x: number }[], library: CastleLibrary | undefined, lods?: KitLods): Promise<CellStreamer> {
  const infos = cells.map((c) => cellInfo(c.id, gltfUrl(c.nodes), c.x));
  const streamer = new CellStreamer(scene, "", infos, { load: 70, unload: 100 }, { onLoaded: () => undefined, onUnloading: () => undefined }, { library, lods });
  streamer.update([0, 0, 0]);
  await streamer.whenSettled();
  await tick(); // textures (NullEngine reports them loaded on the next tick)
  return streamer;
}

const meshNamed = (s: CellStreamer, name: string): AbstractMesh => {
  const m = [...s.meshes].find((x) => x.name === name);
  if (!m) throw new Error(`no mesh ${name}`);
  return m;
};

// ------------------------------------------------------------ textures.json

describe("textures.json", () => {
  it("parses materials with their defaults", () => {
    const specs = parseTexturesJson(TEXTURES);
    expect([...specs.keys()].sort()).toEqual(["castle_floor_square", "castle_glass", "castle_props", "castle_trim", "castle_wall_a"]);
    const wall = specs.get("castle_wall_a")!;
    expect(wall).toMatchObject({ base: "stone_ashlar_a.png", lo: "stone_ashlar_a_lo.png", px: [1024, 1024], emissive: null, alpha: "opaque", unlit: false, doubleSided: false, tint: null, emissiveStrength: 1 });
    expect(specs.get("castle_glass")).toMatchObject({ emissive: "glass_sheet.png", emissiveLo: "glass_sheet_lo.png", tint: "sky" });
    expect(specs.get("castle_props")).toMatchObject({ emissiveStrength: 2.5, key: "0123456789ab" });
  });

  it("is empty for an empty textures file, and skips entries it cannot use", () => {
    expect(parseTexturesJson({ version: 1, materials: {} }).size).toBe(0);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    expect(parseTexturesJson({ version: 2, materials: TEXTURES.materials }).size).toBe(0);
    expect(parseTexturesJson(null).size).toBe(0);
    const bad = parseTexturesJson({
      version: 1,
      materials: {
        castle_ok: { base: "a.png", lo: "a_lo.png", px: [512, 512] },
        castle_up: { base: "../secret.png", lo: "a_lo.png", px: [512, 512] },
        castle_abs: { base: "https://elsewhere/a.png", lo: "a_lo.png", px: [512, 512] },
        castle_px: { base: "a.png", lo: "a_lo.png", px: [512] },
        castle_alpha: { base: "a.png", lo: "a_lo.png", px: [512, 512], alpha: "glow" },
        wall: { base: "a.png", lo: "a_lo.png", px: [512, 512] },
      },
    });
    expect([...bad.keys()]).toEqual(["castle_ok"]);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("knows which materials tile in world space (anisotropic filtering)", () => {
    expect(isWorldMapped("castle_wall_b")).toBe(true);
    expect(isWorldMapped("castle_floor_square")).toBe(true);
    expect(isWorldMapped("castle_trim")).toBe(false);
    expect(isWorldMapped("castle_props")).toBe(false);
  });
});

// ------------------------------------------------------------ shared materials

describe("shared castle materials", () => {
  it("gives every cell the same material per name, outside the cells' containers", async () => {
    const library = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0 });
    library.useSpecs(parseTexturesJson(TEXTURES));
    const s = await stream(
      [
        { id: "a", nodes: kittedCell("a"), x: 0 },
        { id: "b", nodes: kittedCell("b"), x: 20 },
        { id: "c", nodes: greyboxCell("c"), x: -20 },
      ],
      library,
    );
    const wallA = meshNamed(s, "a.castle_wall_a").material as StandardMaterial;
    const wallB = meshNamed(s, "b.castle_wall_a").material as StandardMaterial;
    expect(wallA).toBe(wallB);
    expect(wallA.getClassName()).toBe("StandardMaterial");
    expect(wallA.pluginManager?.getPlugin("Toon")).toBeTruthy();
    expect(library.refs("castle_wall_a")).toBe(2); // one per cell (base and LOD share the glTF material)
    expect(library.refs("castle_props")).toBe(2);
    // Textured: the texture's own colour (white diffuse), invertY off (glTF UVs), repeat, mips, anisotropy on tiling stone.
    const tex = wallA.diffuseTexture as Texture;
    expect(tex.url).toBe("/tex/stone_ashlar_a.png");
    expect(tex.invertY).toBe(false);
    expect(tex.noMipmap).toBe(false);
    expect(tex.wrapU).toBe(1);
    expect(tex.anisotropicFilteringLevel).toBe(4);
    expect(wallA.diffuseColor.asArray()).toEqual([1, 1, 1]);
    // Emissive: the props atlas's own emissive image; glow never runs above level 1 (castle kit M2,
    // 15.6), even at the fixture's M1 strength of 2.5 (the converter would map it to 1.6).
    const props = meshNamed(s, "a.castle_props").material as StandardMaterial;
    expect((props.emissiveTexture as Texture).url).toBe("/tex/props_atlas_e.png");
    expect(props.emissiveTexture!.level).toBe(1);
    expect(props.useEmissiveAsIllumination).toBe(true);
    expect((props.diffuseTexture as Texture).anisotropicFilteringLevel).toBe(1);
    // Glass: its own image again for the emission, one GPU copy shared by URL.
    const glass = meshNamed(s, "a.castle_glass").material as StandardMaterial;
    expect(glass.emissiveTexture).not.toBe(glass.diffuseTexture);
    expect(glass.emissiveTexture!.getInternalTexture()).toBe(glass.diffuseTexture!.getInternalTexture());
    // Un-kitted cell: greybox converted per cell as before; a castle_* name the library lacks keeps its placeholder.
    const grey = meshNamed(s, "c.wall").material as StandardMaterial;
    const cloth = meshNamed(s, "c.castle_cloth").material as StandardMaterial;
    expect(cloth.name).toBe("castle_cloth");
    expect(cloth.diffuseTexture).toBeNull();
    expect(library.refs("castle_cloth")).toBe(0);
    expect(grey.getClassName()).toBe("StandardMaterial");
    // One shared material per name, not one per cell.
    expect(library.size).toBe(3); // wall, glass, props (no floor in the fixture)
    expect(scene.materials.filter((m) => m.name === "castle_wall_a").length).toBe(1);
    s.dispose();
  });

  it("survives a cell's unload and frees the material and its textures on the last release", async () => {
    const library = new CastleLibrary(scene, "tex/", "medium", { graceMs: 1000 });
    library.useSpecs(parseTexturesJson(TEXTURES));
    const s = await stream(
      [
        { id: "a", nodes: kittedCell("a"), x: 0 },
        { id: "b", nodes: kittedCell("b"), x: 60 },
      ],
      library,
    );
    const mat = meshNamed(s, "a.castle_wall_a").material as StandardMaterial;
    const tex = mat.diffuseTexture!;
    vi.useFakeTimers();
    // Walk away from b: it unloads; a still uses the material.
    s.update([-60, 0, 0]);
    expect([...s.meshes].some((m) => m.name.startsWith("b."))).toBe(false);
    expect(library.refs("castle_wall_a")).toBe(1);
    expect(scene.materials.includes(mat)).toBe(true);
    // Walk away from both: no references, freed after the grace period.
    s.update([-200, 0, 0]);
    expect(library.refs("castle_wall_a")).toBe(0);
    expect(scene.materials.includes(mat)).toBe(true); // grace: a neighbour may still want it
    vi.advanceTimersByTime(1100);
    expect(scene.materials.includes(mat)).toBe(false);
    expect(scene.textures.includes(tex)).toBe(false);
    expect(library.size).toBe(0);
    s.dispose();
  });

  it("re-takes a material inside the grace period without rebuilding it", () => {
    vi.useFakeTimers();
    const library = new CastleLibrary(scene, "tex/", "medium", { graceMs: 1000 });
    library.useSpecs(parseTexturesJson(TEXTURES));
    const m1 = library.take("castle_floor_square");
    library.release("castle_floor_square");
    vi.advanceTimersByTime(500);
    const m2 = library.take("castle_floor_square");
    vi.advanceTimersByTime(2000);
    expect(m2).toBe(m1);
    expect(library.refs("castle_floor_square")).toBe(1);
    expect(library.take("castle_unknown")).toBeNull();
    library.dispose();
  });

  it("swaps to the half-size files on Low and frees the others", async () => {
    const library = new CastleLibrary(scene, "tex/", "high", { graceMs: 0 });
    library.useSpecs(parseTexturesJson(TEXTURES));
    const mat = library.take("castle_props")!;
    await tick();
    const old = mat.diffuseTexture!;
    library.setQuality("low");
    await tick();
    expect((mat.diffuseTexture as Texture).url).toBe("/tex/props_atlas_lo.png");
    expect((mat.emissiveTexture as Texture).url).toBe("/tex/props_atlas_e_lo.png");
    expect(scene.textures.includes(old)).toBe(false);
    library.setQuality("medium");
    await tick();
    expect((mat.diffuseTexture as Texture).url).toBe("/tex/props_atlas.png");
    expect(library.textureBytes()).toBeGreaterThan(0);
    library.dispose();
  });

  it("draws the half-size trim sheet at Medium too (medium: lo), the full one at High", async () => {
    expect(parseTexturesJson(TEXTURES).get("castle_trim")!.mediumLo).toBe(true);
    expect(parseTexturesJson(TEXTURES).get("castle_props")!.mediumLo).toBe(false);
    const bad = { version: 1, materials: { castle_trim: { ...TEXTURES.materials.castle_trim, medium: "half" } } };
    expect(parseTexturesJson(bad).size).toBe(0);
    const library = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0 });
    library.useSpecs(parseTexturesJson(TEXTURES));
    const trim = library.take("castle_trim")!;
    const props = library.take("castle_props")!;
    await tick();
    expect((trim.diffuseTexture as Texture).url).toBe("/tex/trim_castle_lo.png");
    expect((props.diffuseTexture as Texture).url).toBe("/tex/props_atlas.png");
    library.setQuality("high");
    await tick();
    expect((trim.diffuseTexture as Texture).url).toBe("/tex/trim_castle.png");
    expect((props.diffuseTexture as Texture).url).toBe("/tex/props_atlas.png");
    library.setQuality("low");
    await tick();
    expect((trim.diffuseTexture as Texture).url).toBe("/tex/trim_castle_lo.png");
    expect((props.diffuseTexture as Texture).url).toBe("/tex/props_atlas_lo.png");
    library.dispose();
  });

  it("dims the windows' glow with the sky, once per tick", async () => {
    const library = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0 });
    library.useSpecs(parseTexturesJson(TEXTURES));
    const glass = library.take("castle_glass")!;
    const props = library.take("castle_props")!;
    await tick();
    expect(glass.emissiveTexture!.level).toBeCloseTo(1);
    // Glass shows its emission, not much of the light falling on it (always lit, it clipped to white).
    expect(glass.diffuseColor.r).toBeCloseTo(SKY_DIFFUSE);
    expect(props.diffuseColor.r).toBe(1);
    library.setSkyLight(0.25);
    expect(glass.emissiveTexture!.level).toBeCloseTo(0.25);
    expect(props.emissiveTexture!.level).toBe(1); // not sky-tinted
    expect(skyEmission(1, 0.5, 1, 0.5)).toBe(1);
    expect(skyEmission(0.1, 0.2, 1, 0.5)).toBeCloseTo(0.12);
    expect(skyEmission(0.5, 0.5, 1, 0.5)).toBeCloseTo(0.125);
    expect(skyEmission(0.6, 0.5, 1, 0.5)).toBeCloseTo(0.216); // the toon night: about 60 % of the noon fill
    library.dispose();
  });

  it("keeps today's behaviour without a library or textures.json", async () => {
    const empty = new CastleLibrary(scene, "tex/", "medium");
    empty.useSpecs(parseTexturesJson({ version: 1, materials: {} }));
    for (const lib of [undefined, empty]) {
      const s = await stream([{ id: "a", nodes: kittedCell("a"), x: 0 }], lib);
      const wall = meshNamed(s, "a.castle_wall_a").material as StandardMaterial;
      expect(wall.name).toBe("castle_wall_a");
      expect(wall.diffuseTexture).toBeNull();
      // The placeholder's flat colour, converted to sRGB as before.
      expect(wall.diffuseColor.r).toBeCloseTo(Math.pow(0.5, 1 / 2.2), 2);
      s.dispose();
    }
  });
});

// ------------------------------------------------------------ colliders

describe("camera colliders", () => {
  it("are hidden, never drawn, and the only meshes ray-cast in kitted cells", async () => {
    const s = await stream(
      [
        { id: "a", nodes: kittedCell("a"), x: 0 },
        { id: "c", nodes: greyboxCell("c"), x: 30 },
      ],
      undefined,
    );
    const all = scene.meshes.filter((m) => m.name === "a.collider");
    expect(all.length).toBe(1);
    const collider = all[0]!;
    expect(isCameraCollider(collider)).toBe(true);
    expect(collider.isEnabled()).toBe(false);
    expect(collider.isVisible).toBe(false);
    expect(collider.isPickable).toBe(false);
    expect(s.meshes.has(collider)).toBe(false);
    expect(s.cameraMeshes.has(collider)).toBe(true);
    expect([...s.cameraMeshes].filter((m) => m.name.startsWith("a."))).toEqual([collider]);
    expect(s.hasCollider("a")).toBe(true);
    // The un-kitted cell keeps today's behaviour: all its meshes.
    expect(s.hasCollider("c")).toBe(false);
    for (const m of s.meshes) if (m.name.startsWith("c.")) expect(s.cameraMeshes.has(m)).toBe(true);
    // A ray hits the collider's triangles (not just its box): it needs a material for that.
    const ray = new Ray(new Vector3(0, 1.5, 5), new Vector3(0, 0, -1), 20);
    const hit = ray.intersectsMesh(collider, false);
    expect(hit.hit).toBe(true);
    expect(hit.distance).toBeCloseTo(8);
    s.dispose();
  });

  it("decide where the follow camera stops, not the drawn walls in front of them", async () => {
    const s = await stream([{ id: "a", nodes: kittedCell("a"), x: 0 }], undefined);
    const canvas = { addEventListener: () => undefined, removeEventListener: () => undefined } as unknown as HTMLCanvasElement;
    const cam = new FollowCamera(scene, canvas, 300);
    // The player at z = 2 facing +z: the camera sits behind, towards -z, where the drawn
    // wall (z = -2, 4 m back) stands in front of the collider (z = -3, 5 m back).
    cam.faceAlong(0);
    for (let i = 0; i < 120; i++) cam.update(0, 0, 2, 1 / 60, s.cameraMeshes);
    const r = cam.camera.radius;
    const sb = Math.sin(cam.camera.beta);
    // Pulled in by the collider (short of the 6.5 m default), past the drawn wall.
    expect(r).toBeLessThan(6.4);
    expect(r * sb).toBeGreaterThan(4.2);
    // ... and it keeps the kit's relief (shafts, cornices) clear of the collider's face.
    expect(r * sb).toBeLessThan(5 - KIT_WALL_RELIEF);
    // With every mesh ray-cast (as before the kit) the drawn wall would have stopped it.
    const cam2 = new FollowCamera(scene, canvas, 300);
    cam2.faceAlong(0);
    for (let i = 0; i < 120; i++) cam2.update(0, 0, 2, 1 / 60, s.meshes);
    expect(cam2.camera.radius * Math.sin(cam2.camera.beta)).toBeLessThan(4);
    cam2.dispose();
    cam.dispose();
    s.dispose();
  });
});

// ------------------------------------------------------------ LOD

describe("kit LOD", () => {
  it("wires _lod1 nodes to their bases and draws them beyond the distance, always on Low", async () => {
    const lods = new KitLods(scene);
    const s = await stream([{ id: "a", nodes: kittedCell("a"), x: 0 }], undefined, lods);
    const base = meshNamed(s, "a.castle_wall_a") as Mesh;
    const lod = meshNamed(s, "a.castle_wall_a_lod1") as Mesh;
    expect(kitLodOf(lod)).toEqual({ base: "a.castle_wall_a", distance: 30 });
    expect(lods.count).toBe(1);
    expect(base.hasLODLevels).toBe(true);
    expect(base.getLODLevels()[0]!.mesh).toBe(lod);
    expect(lod.isBlocked).toBe(true); // never drawn on its own
    expect(scene.customLODSelector).toBe(lods.select);
    const camera = new FreeCamera("probe", new Vector3(0, 2, 10), scene);
    camera.getViewMatrix(true);
    // 12 m from the wall's box: the base.
    expect(lods.select(base, camera)).toBe(base);
    camera.position.set(0, 2, 40);
    camera.getViewMatrix(true);
    expect(lods.select(base, camera)).toBe(lod);
    lods.setForced(true);
    camera.position.set(0, 2, 1);
    camera.getViewMatrix(true);
    expect(lods.select(base, camera)).toBe(lod);
    // Meshes without kit LOD keep Babylon's own choice.
    const other = meshNamed(s, "a.castle_glass");
    expect(lods.select(other, camera)).toBe(other);
    expect(distanceToBox(new Vector3(0, 0, 5), new Vector3(-1, -1, -1), new Vector3(1, 1, 1))).toBeCloseTo(4);
    lods.dispose();
    s.dispose();
  });
});

// ------------------------------------------------------------ camera.max

describe("per-cell camera.max", () => {
  it("takes the manifest's value, else the kit's table, and caps Low", () => {
    expect(KIT_CAMERA.default).toBe(16);
    expect(cameraMaxFor("entrance_hall", undefined, "medium")).toBe(24);
    expect(cameraMaxFor("great_hall", undefined, "high")).toBe(24);
    expect(cameraMaxFor("grand_staircase_upper", undefined, "medium")).toBe(24);
    expect(cameraMaxFor("courtyard", undefined, "medium")).toBe(24);
    expect(cameraMaxFor("common_room_house_c", undefined, "medium")).toBe(20);
    expect(cameraMaxFor("gf_corridor_west", undefined, "medium")).toBe(DEFAULT_MAX_DISTANCE);
    expect(cameraMaxFor(null, undefined, "medium")).toBe(16);
    expect(cameraMaxFor("entrance_hall", undefined, "low")).toBe(LOW_MAX_DISTANCE);
    expect(cameraMaxFor("common_room_house_a", undefined, "low")).toBe(18);
    expect(cameraMaxFor("gf_corridor_west", undefined, "low")).toBe(16);
    expect(cameraMaxFor("entrance_hall", { camera: { max: 22 } }, "medium")).toBe(22);
    expect(cameraMaxFor("x", { camera: { max: 900 } }, "medium")).toBe(40);
  });

  it("eases the wheel's limit to the cell's and pulls in smoothly in a smaller cell", () => {
    const z = new ZoomLimit();
    expect(z.update(1 / 60)).toBe(DEFAULT_DISTANCE);
    z.setTarget(24); // first cell: applied at once
    expect(z.limit).toBe(24);
    for (let i = 0; i < 40; i++) z.wheel(1);
    expect(z.desired).toBe(24);
    expect(z.update(1 / 60)).toBe(24);
    // Into a corridor: the camera comes in to 16 over about a second, never jumping.
    z.setTarget(16);
    let prev = 24;
    let steps = 0;
    for (let t = 0; t < 3; t += 1 / 60) {
      const d = z.update(1 / 60);
      expect(d).toBeLessThanOrEqual(prev + 1e-9);
      expect(prev - d).toBeLessThan(0.5);
      if (d > 16) steps++;
      prev = d;
    }
    expect(prev).toBe(16);
    expect(steps).toBeGreaterThan(30); // eased over more than half a second
    // Scrolling out in the corridor keeps the longer choice for the next hall...
    z.wheel(1);
    expect(z.desired).toBe(24);
    // ...and back in the hall the camera eases out to it again.
    z.setTarget(24);
    for (let i = 0; i < 300; i++) z.update(1 / 60);
    expect(z.update(1 / 60)).toBe(24);
    // Zooming in responds at once from the distance shown.
    z.setTarget(16);
    for (let i = 0; i < 300; i++) z.update(1 / 60);
    z.wheel(-1);
    expect(z.update(1 / 60)).toBeLessThan(16);
    for (let i = 0; i < 200; i++) z.wheel(-1);
    expect(z.desired).toBeCloseTo(1.6);
  });
});

// ------------------------------------------------------------ toon palette

describe("castle_* toon rows", () => {
  it("resolve for every castle_* material, by name or prefix, not the default row", () => {
    const names = Object.keys(KIT_MATERIALS);
    expect(names.length).toBeGreaterThanOrEqual(14);
    const keys = Object.keys(WORLD_TOON.materials).filter((k) => k.startsWith("castle_"));
    for (const name of names) {
      const own = keys.find((k) => k === name || (k.endsWith("*") && name.startsWith(k.slice(0, -1))));
      expect(own, name).toBeDefined();
      const r = worldRamp(name);
      // Vegetation look A: the foliage's shadows are a cool blue-green by design (style guide section 12).
      if (name === "castle_foliage") {
        expect(r.shadowTint[2], name).toBeGreaterThan(r.shadowTint[0]);
        continue;
      }
      expect(r.shadowTint[0], name).toBeGreaterThanOrEqual(r.shadowTint[2] - 0.12);
    }
    expect(worldRamp("castle_glass").outline).toBe(0);
    expect(worldRamp("castle_trim").outline).toBe(1); // the outline pass catches plinths, rims and frames
    expect(worldRamp("castle_wall_c")).toEqual(worldRamp("castle_wall_a"));
  });
});

// ------------------------------------------------------------ ?layout

describe("?layout overlay", () => {
  it("outlines each walkable rectangle, stairs along their slope", () => {
    const stair: OverlaySurface = { id: "s", kind: "stair", level: 0, min: [0, 0], max: [8, 18], y: 0, y_top: 9, rise: "-z" };
    expect(surfaceHeightAt(stair, 4, 18)).toBe(0);
    expect(surfaceHeightAt(stair, 4, 0)).toBe(9);
    expect(surfaceHeightAt(stair, 4, 9)).toBeCloseTo(4.5);
    const room: OverlaySurface = { id: "r", kind: "room", level: 0, min: [0, 0], max: [4, 4], y: 9 };
    const door: OverlaySurface = { id: "d", kind: "door", level: 0, min: [4, 1], max: [6, 3], y: 9 };
    const { lines, colors } = layoutLines([stair, room, door, { ...room, id: "empty", max: [0, 4] }]);
    expect(lines.length).toBe(5); // three outlines, the door's two diagonals, nothing for a degenerate rectangle
    expect(lines[0]!.length).toBe(5);
    expect(lines[0]![0]!.y).toBeCloseTo(9.04);
    expect(lines[1]![0]!.y).toBeCloseTo(9.04);
    expect(colors.every((c, i) => c.length === lines[i]!.length)).toBe(true);
  });
});
