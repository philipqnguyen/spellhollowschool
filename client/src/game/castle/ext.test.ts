// The castle's exterior material clones (castle M5c, docs/art/castle-kit.md
// "Exterior"): a `<base>@ext` material shares its base's
// textures (one GPU copy), takes High's sun shadow, reads its base's toon
// row, is freed with its cells and follows the base through a quality swap.
// Cells are loaded for real (the glTF loader on a NullEngine) from small glTF
// files with the node and material names the export writes.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import type { Texture } from "@babylonjs/core/Materials/Textures/texture";
import "@babylonjs/core/Shaders/default.fragment";
import "@babylonjs/core/Shaders/default.vertex";
import { CellStreamer } from "../cells";
import type { CellInfo } from "../cellplan";
import { receivesCharacterShadows, receivesSun } from "../scene";
import { worldRamp } from "../toon/palette";
import { CastleLibrary, extBase, isExtName, isOutdoorMaterial, OUTDOOR_MATERIALS, parseTexturesJson } from "./materials";

const TEXTURES = {
  version: 1,
  materials: {
    castle_wall_b: { base: "stone_ashlar_b.png", lo: "stone_ashlar_b_lo.png", px: [1024, 1024] },
    castle_trim: { base: "trim_castle.png", lo: "trim_castle_lo.png", px: [2048, 2048], medium: "lo" },
    castle_props: {
      base: "props_atlas.png",
      lo: "props_atlas_lo.png",
      px: [1024, 1024],
      emissive: "props_atlas_e.png",
      emissive_lo: "props_atlas_e_lo.png",
      key: "0123456789ab",
    },
    castle_roof: { base: "roof_slate.png", lo: "roof_slate_lo.png", px: [1024, 1024] },
  },
};

/** A .glb (as a data: URL) with one quad per [node name, material name]. */
function gltfUrl(nodes: [string, string][]): string {
  const materials: object[] = [];
  const matIndex = new Map<string, number>();
  const bytes: number[] = [];
  const views: object[] = [];
  const accessors: object[] = [];
  const meshes: object[] = [];
  const push = (arr: Float32Array | Uint16Array) => {
    const offset = bytes.length;
    bytes.push(...new Uint8Array(arr.buffer));
    while (bytes.length % 4) bytes.push(0);
    views.push({ buffer: 0, byteOffset: offset, byteLength: arr.byteLength });
    return views.length - 1;
  };
  nodes.forEach(([name, material], i) => {
    const z = -2 - i * 0.1;
    accessors.push({ bufferView: push(new Float32Array([-4, 0, z, 4, 0, z, 4, 8, z, -4, 8, z])), componentType: 5126, count: 4, type: "VEC3", min: [-4, 0, z], max: [4, 8, z] });
    accessors.push({ bufferView: push(new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1])), componentType: 5126, count: 4, type: "VEC3" });
    accessors.push({ bufferView: push(new Uint16Array([0, 1, 2, 0, 2, 3])), componentType: 5123, count: 6, type: "SCALAR" });
    if (!matIndex.has(material)) {
      materials.push({ name: material, pbrMetallicRoughness: { baseColorFactor: [0.5, 0.45, 0.4, 1] } });
      matIndex.set(material, materials.length - 1);
    }
    const a = accessors.length;
    meshes.push({ name, primitives: [{ attributes: { POSITION: a - 3, NORMAL: a - 2 }, indices: a - 1, material: matIndex.get(material) }] });
  });
  const gltf = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: nodes.map((_, i) => i) }],
    nodes: nodes.map(([name], i) => ({ name, mesh: i })),
    meshes,
    materials,
    accessors,
    bufferViews: views,
    buffers: [{ byteLength: bytes.length }],
  };
  const json = Buffer.from(JSON.stringify(gltf));
  const jsonPad = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)]);
  const bin = Buffer.from(new Uint8Array(bytes));
  const chunk = (type: number, data: Buffer) => {
    const h = Buffer.alloc(8);
    h.writeUInt32LE(data.length, 0);
    h.writeUInt32LE(type, 4);
    return Buffer.concat([h, data]);
  };
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonPad.length + 8 + bin.length, 8);
  return `data:model/gltf-binary;base64,${Buffer.concat([header, chunk(0x4e4f534a, jsonPad), chunk(0x004e4942, bin)]).toString("base64")}`;
}

/** A facing cell: interior walls in the base, its outside in the clone, a slate roof. */
const facing = (id: string): [string, string][] => [
  [`${id}.castle_wall_b`, "castle_wall_b"],
  [`${id}.castle_wall_b@ext`, "castle_wall_b@ext"],
  [`${id}.castle_trim@ext`, "castle_trim@ext"],
  [`${id}.castle_roof`, "castle_roof"],
];
/** An outdoor cell (the courtyard): every face a clone, its base drawn by no cell. */
const outdoor = (id: string): [string, string][] => [
  [`${id}.castle_props@ext`, "castle_props@ext"],
  [`${id}.castle_unknown@ext`, "castle_unknown@ext"],
];

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

async function stream(cells: { id: string; nodes: [string, string][]; x: number }[], library: CastleLibrary): Promise<CellStreamer> {
  const infos = cells.map((c) => cellInfo(c.id, gltfUrl(c.nodes), c.x));
  const streamer = new CellStreamer(scene, "", infos, { load: 70, unload: 100 }, { onLoaded: () => undefined, onUnloading: () => undefined }, { library });
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

function library(quality: "low" | "medium" | "high" = "medium", graceMs = 0): CastleLibrary {
  const lib = new CastleLibrary(scene, "tex/", quality, { graceMs });
  lib.useSpecs(parseTexturesJson(TEXTURES));
  return lib;
}

describe("@ext names (grid.ext_name, ext_base, is_outdoor_material)", () => {
  it("splits the suffix and knows the outdoor materials", () => {
    expect(extBase("castle_wall_b@ext")).toBe("castle_wall_b");
    expect(extBase("castle_wall_b")).toBe("castle_wall_b");
    expect(extBase("@ext")).toBe("@ext");
    expect(isExtName("castle_props@ext")).toBe(true);
    expect(isExtName("@ext")).toBe(false);
    expect([...OUTDOOR_MATERIALS].sort()).toEqual(["castle_floor_court", "castle_roof"]);
    for (const n of ["castle_wall_b@ext", "castle_trim@ext", "castle_roof", "castle_floor_court"]) expect(isOutdoorMaterial(n), n).toBe(true);
    for (const n of ["castle_wall_b", "castle_glass", "castle_floor_b", "wall_exterior"]) expect(isOutdoorMaterial(n), n).toBe(false);
  });

  it("reads the base's toon row (worldRamp strips the suffix)", () => {
    for (const base of ["castle_wall_b", "castle_trim", "castle_props", "castle_wood", "castle_floor_b"]) expect(worldRamp(`${base}@ext`)).toEqual(worldRamp(base));
    expect(worldRamp("castle_roof")).not.toEqual(worldRamp("castle_unlisted_name"));
  });
});

describe("the exterior clones in the library", () => {
  it("shares the base's texture objects (one GPU copy) under its own material", async () => {
    const lib = library();
    const s = await stream([{ id: "eh", nodes: facing("eh"), x: 0 }], lib);
    const wall = meshNamed(s, "eh.castle_wall_b").material as StandardMaterial;
    const ext = meshNamed(s, "eh.castle_wall_b@ext").material as StandardMaterial;
    expect(ext).not.toBe(wall);
    expect(ext.name).toBe("castle_wall_b@ext");
    expect(ext.getClassName()).toBe("StandardMaterial");
    expect(ext.pluginManager?.getPlugin("Toon")).toBeTruthy();
    expect(ext.diffuseTexture).toBe(wall.diffuseTexture);
    expect((ext.diffuseTexture as Texture).url).toBe("/tex/stone_ashlar_b.png");
    expect(ext.diffuseColor.asArray()).toEqual(wall.diffuseColor.asArray());
    expect(ext.backFaceCulling).toBe(wall.backFaceCulling);
    expect(ext.sideOrientation).toBe(wall.sideOrientation);
    // One texture per image in the scene: the clone adds none.
    expect(scene.textures.filter((t) => (t as Texture).url === "/tex/stone_ashlar_b.png").length).toBe(1);
    // The clone holds one reference on its base besides the cell's own.
    expect(lib.refs("castle_wall_b")).toBe(2);
    expect(lib.refs("castle_wall_b@ext")).toBe(1);
    expect(lib.has("castle_wall_b@ext")).toBe(true);
    expect(lib.has("castle_unknown@ext")).toBe(false);
    // Bytes count each image once.
    const solo = library();
    solo.take("castle_wall_b");
    solo.take("castle_trim");
    solo.take("castle_roof");
    await tick();
    expect(lib.textureBytes()).toBe(solo.textureBytes());
    solo.dispose();
    s.dispose();
    lib.dispose();
  });

  it("builds a clone whose base no cell draws, with its glow; an unknown base keeps the placeholder", async () => {
    const lib = library();
    const s = await stream([{ id: "courtyard", nodes: outdoor("courtyard"), x: 0 }], lib);
    const props = meshNamed(s, "courtyard.castle_props@ext").material as StandardMaterial;
    expect(props.name).toBe("castle_props@ext");
    expect((props.diffuseTexture as Texture).url).toBe("/tex/props_atlas.png");
    expect((props.emissiveTexture as Texture).url).toBe("/tex/props_atlas_e.png");
    expect(props.emissiveTexture!.level).toBe(1);
    expect(props.useEmissiveAsIllumination).toBe(true);
    expect(lib.refs("castle_props")).toBe(1); // the clone's
    const unknown = meshNamed(s, "courtyard.castle_unknown@ext").material as StandardMaterial;
    expect(unknown.diffuseTexture).toBeNull();
    expect(lib.refs("castle_unknown@ext")).toBe(0);
    expect(lib.take("castle_unknown@ext")).toBeNull();
    expect(lib.size).toBe(2); // castle_props and its clone
    s.dispose();
    lib.dispose();
  });

  it("frees with its cells: the clone, then its base and the shared textures", async () => {
    const lib = library("medium", 1000);
    const s = await stream([{ id: "courtyard", nodes: outdoor("courtyard"), x: 0 }], lib);
    const clone = meshNamed(s, "courtyard.castle_props@ext").material as StandardMaterial;
    const tex = clone.diffuseTexture!;
    const base = scene.materials.find((m) => m.name === "castle_props")!;
    vi.useFakeTimers();
    s.update([-200, 0, 0]); // walk away: the cell unloads
    expect(lib.refs("castle_props@ext")).toBe(0);
    expect(scene.materials.includes(clone)).toBe(true); // the grace period
    vi.advanceTimersByTime(1100);
    expect(scene.materials.includes(clone)).toBe(false);
    expect(scene.textures.includes(tex)).toBe(true); // the base outlives its clone by its own grace
    expect(lib.refs("castle_props")).toBe(0);
    vi.advanceTimersByTime(1100);
    expect(scene.materials.includes(base)).toBe(false);
    expect(scene.textures.includes(tex)).toBe(false);
    expect(lib.size).toBe(0);
    s.dispose();
    lib.dispose();
  });

  it("keeps the base and its textures while another cell draws the base", () => {
    const lib = library();
    const base = lib.take("castle_wall_b")!;
    const ext = lib.take("castle_wall_b@ext")!;
    lib.release("castle_wall_b@ext");
    expect(scene.materials.includes(ext)).toBe(false);
    expect(scene.materials.includes(base)).toBe(true);
    expect(lib.refs("castle_wall_b")).toBe(1);
    // Taken again, a new clone of the same base.
    const again = lib.take("castle_wall_b@ext")!;
    expect(again).not.toBe(ext);
    expect(lib.refs("castle_wall_b")).toBe(2);
    lib.dispose();
    expect(scene.materials.includes(again)).toBe(false);
    expect(scene.materials.includes(base)).toBe(false);
  });

  it("swaps with its base on a quality change (one load, the old textures freed)", async () => {
    const lib = library("high");
    const props = lib.take("castle_props@ext")!;
    const trim = lib.take("castle_trim@ext")!;
    await tick();
    const old = props.diffuseTexture!;
    expect((trim.diffuseTexture as Texture).url).toBe("/tex/trim_castle.png");
    lib.setQuality("low");
    await tick();
    expect((props.diffuseTexture as Texture).url).toBe("/tex/props_atlas_lo.png");
    expect((props.emissiveTexture as Texture).url).toBe("/tex/props_atlas_e_lo.png");
    expect(scene.textures.includes(old)).toBe(false);
    lib.setQuality("medium");
    await tick();
    expect((props.diffuseTexture as Texture).url).toBe("/tex/props_atlas.png");
    expect((trim.diffuseTexture as Texture).url).toBe("/tex/trim_castle_lo.png"); // medium: lo, as its base
    const base = scene.materials.find((m) => m.name === "castle_props") as StandardMaterial;
    expect(props.diffuseTexture).toBe(base.diffuseTexture);
    expect(props.emissiveTexture).toBe(base.emissiveTexture);
    expect(scene.textures.filter((t) => (t as Texture).url?.startsWith("/tex/props_atlas")).length).toBe(2); // diffuse and emissive, once
    lib.dispose();
  });
});

describe("the sun's shadow on the outdoor castle (scene.ts, High)", () => {
  const withMaterial = (name: string): Mesh => {
    const m = new Mesh(`cell.${name}`, scene);
    m.material = new StandardMaterial(name, scene);
    return m;
  };

  it("falls on the clones and the outdoor materials, not on the interiors", () => {
    for (const n of ["castle_wall_b@ext", "castle_trim@ext", "castle_props@ext", "castle_roof", "castle_floor_court", "wall_exterior", "roof", "floor_courtyard"]) expect(receivesSun(withMaterial(n)), n).toBe(true);
    for (const n of ["castle_wall_b", "castle_trim", "castle_props", "castle_floor_b", "castle_glass", "floor"]) expect(receivesSun(withMaterial(n)), n).toBe(false);
  });

  it("gives the courtyard's paving the characters' shadows too, as a floor (walls take none)", () => {
    expect(receivesCharacterShadows(withMaterial("castle_floor_court"))).toBe(true);
    expect(receivesCharacterShadows(withMaterial("castle_wall_b@ext"))).toBe(false);
    expect(receivesCharacterShadows(withMaterial("castle_roof"))).toBe(false);
  });
});
