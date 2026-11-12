// Castle kit M2 on the client (docs/art/castle-kit.md "Cell visibility (`sees`)", "Glow"): cell
// visibility (`sees`: the sets, doorways, keeping the last set, the
// streamer disabling cells without unloading them, the camera's rays and
// the torch pool), COLOR_0 kept as normalized bytes on the GPU, and the
// glow arithmetic against the contract's band.

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import "@babylonjs/core/Shaders/default.fragment";
import "@babylonjs/core/Shaders/default.vertex";
import { CellStreamer } from "../cells";
import type { CellInfo } from "../cellplan";
import { CellVisibility, DOOR_NEAR, seesDisabledByUrl, surfacesJoin, type SeesSurface } from "./visibility";
import { CastleLibrary, isGlow, parseTexturesJson } from "./materials";
import { LINEAR_TO_SRGB_BYTE } from "../toon/convert";
import { glowOnScreen, GLOW_MIX, ToonPlugin } from "../toon/material";
import { worldRamp } from "../toon/palette";
import { TOON_POST } from "../toon/post";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import { nearestTorches, TorchLights, type Torch } from "../toon/torches";
import layoutJson from "../../../../data/world/layout.json";
import { materials as KIT_MATERIALS, props_slots as PROPS_SLOTS } from "../../../../data/art/castle_textures.json";

// ------------------------------------------------------------ fixtures

const sq = (id: string, cell: string, x0: number, z0: number, x1: number, z1: number, kind = "room", y = 0): SeesSurface => ({ id, cell, kind, min: [x0, z0], max: [x1, z1], y });

/** Three rooms in a row, a and b joined by a door of a's, b and c by a door of c's; a yard beyond c. */
const SURFACES: SeesSurface[] = [
  sq("ra", "a", 0, 0, 10, 10),
  sq("dab", "a", 10, 4, 11, 6, "door"),
  sq("rb", "b", 11, 0, 21, 10),
  sq("dbc", "c", 21, 4, 22, 6, "door"),
  sq("rc", "c", 22, 0, 32, 10),
  sq("dcy", "c", 32, 4, 33, 6, "door"),
  sq("yard", "y", 33, 0, 60, 30, "courtyard"),
];
const CELLS = [
  { id: "a", sees: ["a", "b", "c"] },
  { id: "b", sees: ["a", "b", "c", "y"] },
  { id: "c", sees: ["a", "b", "c", "y"] },
  { id: "y" }, // outdoor: no `sees`
  { id: "far", sees: ["far"] },
];

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

/** A .glb (data: URL) of one quad per node; `colour` adds COLOR_0 in the given layout. */
function glb(nodes: { name: string; material?: string; colour?: "bytes" | "float" }[]): string {
  const bytes: number[] = [];
  const views: object[] = [];
  const accessors: object[] = [];
  const meshes: object[] = [];
  const materials: object[] = [];
  const push = (arr: ArrayBufferView) => {
    const offset = bytes.length;
    bytes.push(...new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength));
    while (bytes.length % 4) bytes.push(0);
    views.push({ buffer: 0, byteOffset: offset, byteLength: arr.byteLength });
    return views.length - 1;
  };
  for (const n of nodes) {
    const pos = new Float32Array([-1, 0, 0, 1, 0, 0, 1, 2, 0, -1, 2, 0]);
    accessors.push({ bufferView: push(pos), componentType: 5126, count: 4, type: "VEC3", min: [-1, 0, 0], max: [1, 2, 0] });
    const attributes: Record<string, number> = { POSITION: accessors.length - 1 };
    if (n.colour === "bytes") {
      // Linear 0.64 (163), 1.0 (255), 0 and 128, as the export packs them (visibility.pack_color0).
      accessors.push({ bufferView: push(new Uint8Array([163, 163, 163, 255, 255, 255, 255, 255, 0, 0, 0, 255, 128, 64, 32, 255])), componentType: 5121, normalized: true, count: 4, type: "VEC4" });
      attributes.COLOR_0 = accessors.length - 1;
    } else if (n.colour === "float") {
      accessors.push({ bufferView: push(new Float32Array([0.64, 0.64, 0.64, 1, 1, 1, 0, 0, 0, 0.5, 0.25, 0.125])), componentType: 5126, count: 4, type: "VEC3" });
      attributes.COLOR_0 = accessors.length - 1;
    }
    accessors.push({ bufferView: push(new Uint16Array([0, 1, 2, 0, 2, 3])), componentType: 5123, count: 6, type: "SCALAR" });
    const prim: Record<string, unknown> = { attributes, indices: accessors.length - 1 };
    if (n.material) {
      materials.push({ name: n.material, pbrMetallicRoughness: { baseColorFactor: [0.5, 0.5, 0.5, 1] } });
      prim.material = materials.length - 1;
    }
    meshes.push({ name: n.name, primitives: [prim] });
  }
  const gltf = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: nodes.map((_, i) => i) }],
    nodes: nodes.map((n, i) => ({ name: n.name, mesh: i, ...(n.name.endsWith(".collider") ? { extras: { role: "camera_collider" } } : {}) })),
    meshes,
    ...(materials.length ? { materials } : {}),
    accessors,
    bufferViews: views,
    buffers: [{ byteLength: bytes.length }],
  };
  const json = Buffer.from(JSON.stringify(gltf));
  const jsonPad = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)]);
  const bin = Buffer.from(new Uint8Array(bytes));
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonPad.length + 8 + bin.length, 8);
  const chunk = (type: number, data: Buffer) => {
    const h = Buffer.alloc(8);
    h.writeUInt32LE(data.length, 0);
    h.writeUInt32LE(type, 4);
    return Buffer.concat([h, data]);
  };
  return `data:model/gltf-binary;base64,${Buffer.concat([header, chunk(0x4e4f534a, jsonPad), chunk(0x004e4942, bin)]).toString("base64")}`;
}

const info = (id: string, file: string): CellInfo => ({ id, file, bounds: { min: [-10, 0, -10], max: [10, 10, 10] }, level: 0 });
const tick = () => new Promise((r) => setTimeout(r, 0));

async function stream(ids: string[], before?: (s: CellStreamer) => void, library?: CastleLibrary, colour?: "bytes" | "float"): Promise<CellStreamer> {
  const infos = ids.map((id) =>
    info(id, glb([
      { name: `${id}.wall`, material: "wall", colour },
      { name: `${id}.castle_props`, material: "castle_props" },
      ...(id === "a" ? [{ name: `${id}.collider` }] : []),
    ])),
  );
  const s = new CellStreamer(scene, "", infos, { load: 70, unload: 100 }, { onLoaded: () => undefined, onUnloading: () => undefined }, { library });
  before?.(s);
  s.update([0, 0, 0]);
  await s.whenSettled();
  await tick();
  return s;
}

const named = (s: CellStreamer, name: string): AbstractMesh => {
  const m = [...s.meshes].find((x) => x.name === name) ?? scene.meshes.find((x) => x.name === name);
  if (!m) throw new Error(`no mesh ${name}`);
  return m;
};

// ------------------------------------------------------------ the sets

describe("cell visibility sets", () => {
  it("shows the current cell's sees, everything before the first place and for outdoor cells", () => {
    const v = new CellVisibility(CELLS, SURFACES, { enabled: true });
    expect(v.visible).toBeNull();
    expect(v.isVisible("far")).toBe(true);
    expect(v.place("ra")).toBe(true);
    expect([...v.visible!].sort()).toEqual(["a", "b", "c"]);
    expect(v.isVisible("y")).toBe(false);
    expect(v.isVisible("far")).toBe(false);
    expect(v.place("ra")).toBe(false); // same set: nothing to do
    expect(v.place("yard")).toBe(true);
    expect(v.visible).toBeNull(); // outdoor: everything
  });

  it("shows the union over the cells a door joins while standing in it", () => {
    const v = new CellVisibility(CELLS, SURFACES, { enabled: true });
    v.place("dab");
    expect([...v.visible!].sort()).toEqual(["a", "b", "c", "y"]); // a's and b's sets
    // A door onto the yard (an outdoor cell) shows everything.
    v.place("dcy");
    expect(v.visible).toBeNull();
  });

  it("shows a door's union within DOOR_NEAR of it, not further (review: a step short of a door the camera sees through it)", () => {
    const v = new CellVisibility(CELLS, SURFACES, { enabled: true });
    expect(DOOR_NEAR).toBe(3);
    v.place("ra", 5, 5); // 5 m from the door dab (x 10..11)
    expect([...v.visible!].sort()).toEqual(["a", "b", "c"]);
    v.place("ra", 8, 5); // 2 m short of it
    expect(v.visible).toBe(v.setFor("dab"));
    expect([...v.visible!].sort()).toEqual(["a", "b", "c", "y"]);
    v.place("ra", 7, 9.5); // diagonal: (3, 3.5) from the rectangle's corner, 3.2 m
    expect([...v.visible!].sort()).toEqual(["a", "b", "c"]);
    v.place("rc", 31, 5); // beside the door onto the yard: everything
    expect(v.visible).toBeNull();
    v.place("rb", 15, 5); // 4 m from both of b's doors: its own set
    expect(v.visible).toBe(v.setFor("rb"));
    v.place("ra"); // no position: the surface's own set, as before
    expect([...v.visible!].sort()).toEqual(["a", "b", "c"]);
  });

  it("keeps the last set in the air or on an unknown surface, and switches at once on a teleport", () => {
    const v = new CellVisibility(CELLS, SURFACES, { enabled: true });
    v.place("ra");
    const set = v.visible;
    expect(v.place(null)).toBe(false);
    expect(v.place(undefined)).toBe(false);
    expect(v.place("nowhere")).toBe(false);
    expect(v.visible).toBe(set);
    // A teleport from a to c: c's set on the first place update, with nothing in between.
    expect(v.place("rc")).toBe(true);
    expect([...v.visible!].sort()).toEqual(["a", "b", "c", "y"]);
    // A zone change forgets the set.
    expect(v.reset()).toBe(true);
    expect(v.visible).toBeNull();
    expect(v.hasPlace).toBe(false);
  });

  it("reuses one set per cell and door: switching allocates nothing", () => {
    const v = new CellVisibility(CELLS, SURFACES, { enabled: true });
    v.place("ra");
    const a = v.visible;
    v.place("rb");
    v.place("ra");
    expect(v.visible).toBe(a);
    expect(v.setFor("ra")).toBe(a);
    expect(v.setFor("dab")).toBe(v.setFor("dab"));
  });

  it("turns off with ?sees=off, and treats cells without sees as seeing everything", () => {
    expect(seesDisabledByUrl("?sees=off")).toBe(true);
    expect(seesDisabledByUrl("?probe&sees=off")).toBe(true);
    expect(seesDisabledByUrl("?probe")).toBe(false);
    expect(seesDisabledByUrl("")).toBe(false);
    const off = new CellVisibility(CELLS, SURFACES, { enabled: false });
    expect(off.place("ra")).toBe(false);
    expect(off.visible).toBeNull();
    const old = new CellVisibility([{ id: "a" }, { id: "b" }], SURFACES, { enabled: true });
    old.place("ra");
    expect(old.visible).toBeNull(); // an old manifest: no culling
  });

  it("joins a door to the surfaces it touches at a step's height, not corners or other floors", () => {
    expect(surfacesJoin(sq("d", "a", 10, 4, 11, 6, "door"), sq("r", "b", 11, 0, 21, 10))).toBe(true);
    expect(surfacesJoin(sq("d", "a", 10, 4, 11, 6, "door"), sq("r", "b", 11, 6, 21, 10))).toBe(false); // corner
    expect(surfacesJoin(sq("d", "a", 10, 4, 11, 6, "door"), sq("r", "b", 11, 0, 21, 10, "room", 9))).toBe(false); // a floor above
    // A stair whose top end reaches the door.
    expect(surfacesJoin(sq("d", "a", 10, 4, 11, 6, "door", 9), { ...sq("s", "b", 11, 0, 21, 10, "stair", 0), y_top: 9 })).toBe(true);
  });

  it("joins the real layout's doors to both sides (the front doors, the Marble Arch)", () => {
    const surfaces = (layoutJson as unknown as { surfaces: SeesSurface[] }).surfaces;
    const cells = [...new Set(surfaces.map((s) => s.cell!))].map((id) => ({ id, sees: [id] }));
    const v = new CellVisibility(cells, surfaces, { enabled: true });
    const doorCells = (id: string) => [...(v.setFor(id) ?? [])].sort();
    expect(doorCells("ff_marble_arch")).toEqual(expect.arrayContaining(["entrance_hall", "ff_corridor_north"]));
    expect(doorCells("eh_west_door")).toEqual(expect.arrayContaining(["entrance_hall", "gf_corridor_west"]));
    expect(doorCells("gh_doors")).toEqual(expect.arrayContaining(["entrance_hall", "great_hall"]));
    for (const s of surfaces) if (s.kind === "door") expect(v.setFor(s.id)!.size).toBeGreaterThanOrEqual(2);
  });
});

// ------------------------------------------------------------ the streamer

describe("disabling resident cells", () => {
  it("disables cells outside the set at their root, without unloading them or dropping library references", async () => {
    const library = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0 });
    library.useSpecs(parseTexturesJson({ version: 1, materials: { castle_props: { base: "p.png", lo: "p_lo.png", px: [64, 64], emissive: "e.png", emissive_strength: 1 } } }));
    const s = await stream(["a", "b", "c"], undefined, library);
    const before = { meshes: s.meshes.size, loaded: s.loadedCount, refs: library.refs("castle_props") };
    expect(before.refs).toBe(3);
    expect(s.visibleCounts()).toEqual({ shown: 3, loaded: 3 });
    s.setVisibleCells(new Set(["a"]));
    expect(named(s, "a.wall").isEnabled()).toBe(true);
    expect(named(s, "b.wall").isEnabled()).toBe(false);
    expect(named(s, "b.castle_props").isEnabled()).toBe(false);
    expect(named(s, "c.wall").isEnabled()).toBe(false);
    expect(s.visibleCounts()).toEqual({ shown: 1, loaded: 3 });
    expect({ meshes: s.meshes.size, loaded: s.loadedCount, refs: library.refs("castle_props") }).toEqual(before);
    // The camera's rays skip hidden cells; a's collider stays ray-cast (and never drawn).
    expect([...s.cameraMeshes].some((m) => m.name.startsWith("b."))).toBe(false);
    expect([...s.cameraMeshes].map((m) => m.name)).toEqual(["a.collider"]);
    expect(named(s, "a.collider").isEnabled()).toBe(false);
    // Back again, in the same call.
    s.setVisibleCells(null);
    expect(named(s, "b.wall").isEnabled()).toBe(true);
    expect([...s.cameraMeshes].filter((m) => m.name.startsWith("b.")).length).toBe(2);
    expect(s.isCellShown("b")).toBe(true);
    s.dispose();
    library.dispose();
  });

  it("starts a cell that loads outside the set disabled, and keeps a mesh's own hiding (dorms)", async () => {
    const s = await stream(["a", "b"], (st) => st.setVisibleCells(new Set(["a"])));
    expect(named(s, "b.wall").isEnabled()).toBe(false);
    expect(s.isCellShown("b")).toBe(false);
    // The castle hidden behind a dorm: meshes disabled one by one stay so whatever the set does.
    named(s, "a.wall").setEnabled(false);
    s.setVisibleCells(null);
    expect(named(s, "a.wall").isEnabled()).toBe(false);
    expect(named(s, "b.wall").isEnabled()).toBe(true);
    s.dispose();
  });

  it("lights only torches of visible cells", () => {
    const t = (x: number, cell: string): Torch => ({ pos: [x, 0, 0], colour: [1, 0.7, 0.4], intensity: 1, range: 8, cell });
    const torches = [t(1, "b"), t(3, "a"), t(5, "a"), t(2, "b")];
    const out = new Int32Array(2);
    const dist = new Float64Array(2);
    nearestTorches(torches, [0, 0, 0], out, dist, new Uint8Array([0, 1, 1, 0]));
    expect([...out]).toEqual([1, 2]);
    const pool = new TorchLights(scene, "medium");
    pool.setTorches(torches);
    expect(pool.usableCount).toBe(4);
    pool.setVisibleCells(new Set(["a"]));
    expect(pool.usableCount).toBe(2);
    pool.setVisibleCells(null);
    expect(pool.usableCount).toBe(4);
    pool.dispose();
  });
});

// ------------------------------------------------------------ COLOR_0

describe("COLOR_0 as normalized bytes", () => {
  it("maps linear bytes to sRGB through the table", () => {
    expect(LINEAR_TO_SRGB_BYTE.length).toBe(256);
    expect(LINEAR_TO_SRGB_BYTE[0]).toBe(0);
    expect(LINEAR_TO_SRGB_BYTE[255]).toBe(255);
    for (const i of [1, 32, 128, 163, 200]) expect(LINEAR_TO_SRGB_BYTE[i]).toBe(Math.round(255 * Math.pow(i / 255, 1 / 2.2)));
    expect(LINEAR_TO_SRGB_BYTE[163]).toBe(208); // the walls' 0.64 ramp floor
  });

  for (const layout of ["bytes", "float"] as const) {
    it(`keeps ${layout} colours as a 4-byte normalized buffer, sRGB, alpha 255`, async () => {
      const s = await stream(["a"], undefined, undefined, layout);
      const wall = named(s, "a.wall") as Mesh;
      const vb = wall.getVertexBuffer(VertexBuffer.ColorKind)!;
      expect(vb.type).toBe(VertexBuffer.UNSIGNED_BYTE);
      expect(vb.normalized).toBe(true);
      expect(vb.getSize()).toBe(4);
      expect(vb.byteStride).toBe(4);
      expect(vb.byteOffset).toBe(0);
      const data = vb.getData() as Uint8Array;
      expect(data).toBeInstanceOf(Uint8Array);
      expect(data.byteLength).toBe(16);
      const T = LINEAR_TO_SRGB_BYTE;
      expect([...data]).toEqual([T[163], T[163], T[163], 255, 255, 255, 255, 255, 0, 0, 0, 255, T[128], T[64], T[32], 255]);
      expect(wall.hasVertexAlpha).toBe(false);
      s.dispose();
    });
  }
});

// ------------------------------------------------------------ glow

/** The contract's glow numbers, read from their one source (tools/blender/castle_kit/grid.py). */
function gridGlow(): { lantern: string; candle: string; band: Record<string, [number, number]>; after: Record<string, number>; baseMax: number } {
  const src = readFileSync(fileURLToPath(new URL("../../../../tools/blender/castle_kit/grid.py", import.meta.url)), "utf8");
  const str = (name: string) => src.match(new RegExp(`^${name} = "(#[0-9A-Fa-f]{6})"`, "m"))![1]!;
  const dict = (name: string) => src.match(new RegExp(`^${name} = (\\{.*\\})`, "m"))![1]!.replace(/'/g, '"').replace(/\(/g, "[").replace(/\)/g, "]");
  return {
    lantern: str("GLOW_LANTERN"),
    candle: str("GLOW_CANDLE"),
    band: JSON.parse(dict("GLOW_BAND")),
    after: JSON.parse(dict("GLOW_AFTER_BLOOM")),
    baseMax: Number(src.match(/^GLOW_BASE_MAX = ([0-9.]+)/m)![1]),
  };
}

const rgb = (hex: string): [number, number, number] => {
  const n = Number.parseInt(hex.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
};

describe("warm glow", () => {
  const g = gridGlow();
  const bloom = { threshold: TOON_POST.bloomThreshold, knee: TOON_POST.bloomKnee, strength: TOON_POST.bloomStrength };

  it("keeps the lantern and candle colours in the band before and after the bloom", () => {
    for (const hex of [g.lantern, g.candle]) {
      for (const b of [bloom, null]) {
        const px = glowOnScreen(rgb(hex), b);
        expect(px.before[0]).toBeGreaterThanOrEqual(g.band.r![0]);
        expect(px.before[1]).toBeGreaterThanOrEqual(g.band.g![0]);
        expect(px.before[1]).toBeLessThanOrEqual(g.band.g![1]);
        expect(px.before[2]).toBeGreaterThanOrEqual(g.band.b![0]);
        expect(px.before[2]).toBeLessThanOrEqual(g.band.b![1]);
        expect(px.after[1]).toBeLessThanOrEqual(g.after.g!);
        expect(px.after[2]).toBeLessThanOrEqual(g.after.b!);
      }
      // Red at the top: the bright pass halos it (Medium and High).
      const k = glowOnScreen(rgb(hex), bloom);
      expect(k.after[1]).toBeGreaterThan(k.before[1]);
    }
    // The bright pass's threshold is what the band's red floor is made for.
    expect(TOON_POST.bloomThreshold).toBeLessThanOrEqual(g.band.r![0]);
  });

  it("would have clipped the M1 glow (strength 2.5 and the pale slots) towards white", () => {
    // M1: #FFC774 at texture level 1.6 clipped green to 1 before the bloom.
    const m1 = rgb("#FFC774").map((c) => Math.min(1, c * 1.6)) as [number, number, number];
    expect(glowOnScreen(m1, bloom).after[1]).toBeGreaterThan(g.after.g!);
    // The shipped glow slots of the props atlas: the M2 colours pass wherever they are listed.
    const glowSlots = (PROPS_SLOTS as [string, string][]).filter(([tile]) => tile === "glow").map(([, c]) => c.toUpperCase());
    for (const c of glowSlots.filter((c) => c === g.lantern.toUpperCase() || c === g.candle.toUpperCase())) {
      expect(glowOnScreen(rgb(c), bloom).after[1]).toBeLessThanOrEqual(g.after.g!);
    }
    expect(KIT_MATERIALS.castle_props.emissive_strength).toBe(1);
  });

  it("shows glow texels as painted on the props atlas only (not glass, not walls)", () => {
    const spec = (name: string) => parseTexturesJson({ version: 1, materials: { [name]: { base: "x.png", lo: "x_lo.png", px: [8, 8], ...(KIT_MATERIALS as Record<string, { emissive?: string; tint?: string }>)[name] } } }).get(name)!;
    expect(isGlow(spec("castle_props"))).toBe(true);
    expect(isGlow(spec("castle_glass"))).toBe(false);
    expect(isGlow(spec("castle_wall_a"))).toBe(false);
    const mesh = CreateBox("box", {}, scene);
    const defines = new MaterialDefines();
    const lamp = new ToonPlugin(new StandardMaterial("castle_props", scene), worldRamp("castle_props"), { glow: true });
    lamp.prepareDefines(defines, scene, mesh);
    expect(defines["TOON_GLOW"]).toBe(true);
    new ToonPlugin(new StandardMaterial("castle_glass", scene), worldRamp("castle_glass")).prepareDefines(defines, scene, mesh);
    expect(defines["TOON_GLOW"]).toBe(false);
    // The glow mix runs last, after the grade (shoulder, saturation, highlight): no rim or highlight on top.
    const code = lamp.getCustomCode("fragment")!["CUSTOM_FRAGMENT_BEFORE_FOG"]!;
    expect(code.indexOf("TOON_GLOW")).toBeGreaterThan(code.indexOf("toonShoulder(color.rgb)"));
    expect(code).toContain("mix(color.rgb, clamp(emissiveColor, 0.0, 1.0), toonGlowW)");
    expect(GLOW_MIX[0]).toBeLessThan(GLOW_MIX[1]);
    expect(g.baseMax).toBeLessThanOrEqual(0.1);
  });
});

// ------------------------------------------------------------ rib speckle

describe("castle lines (character look pass 2, 9.2)", () => {
  it("draws no castle outline: no silhouette, crease or character line in the final pass", () => {
    const final = ShaderStore.ShadersStore["toonFinalFragmentShader"]!;
    expect(final).not.toContain("crease");
    expect(final).not.toContain("isChar");
    expect(final).not.toContain("zFar");
    expect(final).not.toContain("OUTLINE");
  });
});
