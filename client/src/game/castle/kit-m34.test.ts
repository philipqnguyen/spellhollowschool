// Castle kit M3 + M4, the client side (docs/art/castle-kit.md,
// 17.12): the enchanted ceiling's colour arithmetic and material, the
// floating candles, group LOD and Low (LOD1 everywhere, one candle group a
// cell), seats for the sit emote, the toon rows (the far-fade on the trim,
// the lighter vault row) and the colour rule for client/src/game/castle/**.
// Cells are loaded for real (the glTF loader on a NullEngine) from small
// glTF files built here, with the node names and extras the export writes.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { FreeCamera } from "@babylonjs/core/Cameras/freeCamera";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import "@babylonjs/core/Shaders/default.fragment";
import "@babylonjs/core/Shaders/default.vertex";
import { CellStreamer } from "../cells";
import type { CellInfo } from "../cellplan";
import { castleLight, type CastleLight } from "../toon/lighting";
import { packRamp, resolveRamp, worldRamp, WORLD_TOON } from "../toon/palette";
import { DayNight, nightLook } from "../clock/daynight";
import { CastleLibrary, parseTexturesJson } from "./materials";
import { KitLods, beyondBox, distanceToBox } from "./lod";
import { bobOffset, KitCandles } from "./candles";
import { castsSunShadow, kitAnimOf } from "./nodes";
import { castleSkyFragment, castleSkyLook, castleSkyOf, CastleSkyPlugin, luma, NIGHT_CLOUD_LUMA_MAX, SKY_PANEL_PEAK, skyPanelPixel } from "./sky";
import { Seats, SEAT_REACH, seatPlace, type SeatPlace } from "./seats";
import lightingJson from "../../../../data/art/lighting.json";
import { materials as KIT_MATERIALS } from "../../../../data/art/castle_textures.json";

// ------------------------------------------------------------ fixtures

interface NodeSpec {
  name: string;
  material: string;
  /** A quad facing +z: x in [cx - w, cx + w], y in [y0, y0 + 2w], at z. */
  cx: number;
  y0?: number;
  z: number;
  w?: number;
  extras?: Record<string, unknown>;
}

/** A .glb (data: URL) with one quad mesh per node, named and with extras as the export writes them. */
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
    const w = n.w ?? 1;
    const y0 = n.y0 ?? 0;
    const pos = new Float32Array([n.cx - w, y0, n.z, n.cx + w, y0, n.z, n.cx + w, y0 + 2 * w, n.z, n.cx - w, y0 + 2 * w, n.z]);
    const nor = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]);
    const uv = new Float32Array([0, 1, 1, 1, 1, 0, 0, 0]);
    const idx = new Uint16Array([0, 1, 2, 0, 2, 3]);
    accessors.push({ bufferView: push(pos), componentType: 5126, count: 4, type: "VEC3", min: [n.cx - w, y0, n.z], max: [n.cx + w, y0 + 2 * w, n.z] });
    const pa = accessors.length - 1;
    accessors.push({ bufferView: push(nor), componentType: 5126, count: 4, type: "VEC3" });
    const na = accessors.length - 1;
    accessors.push({ bufferView: push(uv), componentType: 5126, count: 4, type: "VEC2" });
    const ua = accessors.length - 1;
    accessors.push({ bufferView: push(idx), componentType: 5123, count: 6, type: "SCALAR" });
    const ia = accessors.length - 1;
    if (!matIndex.has(n.material)) {
      materials.push({ name: n.material, pbrMetallicRoughness: { baseColorFactor: [0.5, 0.45, 0.4, 1] } });
      matIndex.set(n.material, materials.length - 1);
    }
    meshes.push({ name: n.name, primitives: [{ attributes: { POSITION: pa, NORMAL: na, TEXCOORD_0: ua }, indices: ia, material: matIndex.get(n.material) }] });
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

const PERIODS = [5, 5.6, 6.2, 6.8];
const anim = (k: number) => ({ kind: "bob", group: `candles_${k}`, amp: 0.06, period: PERIODS[k]!, phase: (k * Math.PI) / 2 });

/** A Great Hall-like cell: four candle groups along x (0, 20, 40, 60) at y 5..7, one with flame cards; a banner; a portrait band. */
function hallCell(id: string): NodeSpec[] {
  const out: NodeSpec[] = [];
  for (let k = 0; k < 4; k++) {
    out.push({ name: `${id}.castle_props.candles_${k}`, material: "castle_props", cx: 20 * k, y0: 5, z: 0, w: 1, extras: { kit_anim: anim(k) } });
  }
  out.push({ name: `${id}.castle_props.candles_0_lod1`, material: "castle_props", cx: 0, y0: 5, z: 0, w: 1, extras: { kit_lod: { base: `${id}.castle_props.candles_0`, distance: 40 } } });
  out.push({ name: `${id}.castle_cloth`, material: "castle_cloth", cx: 0, y0: 8, z: -10, w: 1 });
  out.push({ name: `${id}.castle_portraits_a.l0`, material: "castle_portraits_a", cx: 0, y0: 0, z: -20, w: 2 });
  out.push({
    name: `${id}.castle_portraits_a.l0_lod1`,
    material: "castle_portraits_a",
    cx: 0,
    y0: 0,
    z: -20,
    w: 2,
    extras: { kit_lod: { base: `${id}.castle_portraits_a.l0`, distance: 9 } },
  });
  out.push({ name: `${id}.castle_wall_b`, material: "castle_wall_b", cx: 0, z: -30, w: 30 });
  out.push({ name: `${id}.castle_sky`, material: "castle_sky", cx: 30, y0: 17.5, z: 0, w: 5 });
  return out;
}

const cellInfo = (id: string, file: string): CellInfo => ({ id, file, bounds: { min: [-10, 0, -40], max: [70, 30, 10] }, level: 0 });

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

async function streamHall(lods: KitLods, candles: KitCandles, library?: CastleLibrary): Promise<CellStreamer> {
  const s = new CellStreamer(scene, "", [cellInfo("gh", gltfUrl(hallCell("gh")))], { load: 70, unload: 100 }, { onLoaded: () => undefined, onUnloading: () => undefined }, { lods, candles, library });
  s.update([0, 0, 0]);
  await s.whenSettled();
  await tick();
  return s;
}

const meshNamed = (s: CellStreamer, name: string): AbstractMesh => {
  const m = [...s.meshes].find((x) => x.name === name);
  if (!m) throw new Error(`no mesh ${name}`);
  return m;
};

function cameraAt(x: number, y: number, z: number): FreeCamera {
  const c = scene.activeCamera instanceof FreeCamera ? scene.activeCamera : new FreeCamera("probe", new Vector3(), scene);
  c.position.set(x, y, z);
  c.getViewMatrix(true);
  c.computeWorldMatrix();
  return c;
}

// ------------------------------------------------------------ the enchanted ceiling

describe("castle_sky: the enchanted ceiling", () => {
  const keys = (lightingJson as { castle: { keys: { hour: number; sky: { zenith: string; horizon: string } }[] } }).castle.keys;
  const hex = (h: string) => [1, 3, 5].map((i) => Number.parseInt(h.slice(i, i + 2), 16) / 255);
  const noon = castleLight(13);
  const midnight = castleLight(0);

  it("runs from the hour's horizon at the plate to its zenith at the ridge (lighting.json)", () => {
    const key = keys.find((k) => k.hour === 13)!;
    const look = castleSkyLook(noon, nightLook(1).stars);
    // The data's colours, scaled down only where a channel would reach the bloom's cut.
    const h = hex(key.sky.horizon);
    const peak = Math.max(...h);
    for (let i = 0; i < 3; i++) expect(look.horizon[i]).toBeCloseTo((h[i]! * Math.min(1, SKY_PANEL_PEAK / peak)), 5);
    expect(look.zenith).toEqual(hex(key.sky.zenith).map((v) => expect.closeTo(v, 5)));
    const plate = skyPanelPixel(look, 0, 0, 0);
    const ridge = skyPanelPixel(look, 1, 0, 0);
    for (let i = 0; i < 3; i++) {
      expect(plate[i]).toBeCloseTo(look.horizon[i]!, 5);
      expect(ridge[i]).toBeCloseTo(look.zenith[i]!, 5);
    }
    // By day a daytime sky, bluer towards the ridge; no stars.
    const blue = (c: number[]) => c[2]! / (c[0]! + c[1]! + c[2]!);
    expect(blue(ridge)).toBeGreaterThan(blue(plate));
    expect(look.stars).toEqual([0, 0, 0]);
    expect(skyPanelPixel(look, 0.8, 0, 1)).toEqual(skyPanelPixel(look, 0.8, 0, 0));
  });

  it("lifts clouds towards the sun by day and keeps them dark at midnight", () => {
    const day = castleSkyLook(noon, 0);
    const night = castleSkyLook(midnight, nightLook(0).stars);
    expect(luma(day.cloud)).toBeGreaterThan(0.75);
    // Warm-white by day (the sun's hue), never brighter than the bloom's cut.
    expect(day.cloud[0]).toBeGreaterThanOrEqual(day.cloud[2]! - 0.02);
    for (const v of [0, 0.5, 1]) {
      for (const c of [0, 0.3, 0.6, 0.8, 1]) {
        for (const a of [0, 1]) {
          const d = skyPanelPixel(day, v, c, a);
          expect(Math.max(...d)).toBeLessThanOrEqual(SKY_PANEL_PEAK + 1e-9);
          // Full cloud at midnight: at most the contract's 0.35.
          if (c >= 0.6) expect(luma(skyPanelPixel(night, v, c, 0))).toBeLessThanOrEqual(NIGHT_CLOUD_LUMA_MAX);
        }
      }
    }
    expect(luma(night.cloud)).toBeLessThanOrEqual(NIGHT_CLOUD_LUMA_MAX);
    expect(luma(night.shade)).toBeLessThan(luma(night.cloud));
    // The moon's colour, not grey: blue-leaning clouds at night.
    expect(night.cloud[2]).toBeGreaterThan(night.cloud[0]!);
  });

  it("adds stars x A x (1 - cloud) at night, on a dark blue sky", () => {
    const look = castleSkyLook(midnight, nightLook(0).stars);
    expect(nightLook(0).stars).toBe(1);
    const clear = skyPanelPixel(look, 0.7, 0, 0);
    // Dark blue: blue leads, dark overall.
    expect(clear[2]).toBeGreaterThan(clear[0]!);
    expect(clear[2]).toBeGreaterThan(clear[1]!);
    expect(luma(clear)).toBeLessThan(0.25);
    const star = skyPanelPixel(look, 0.7, 0, 1);
    for (let i = 0; i < 3; i++) expect(star[i]! - clear[i]!).toBeCloseTo(Math.min(SKY_PANEL_PEAK - clear[i]!, look.stars[i]!), 5);
    expect(luma(look.stars)).toBeGreaterThan(0.8);
    // Half cloud lets half the star through (over the cloud's own colour).
    const half = skyPanelPixel(look, 0.7, 0.5, 1);
    const halfNoStar = skyPanelPixel(look, 0.7, 0.5, 0);
    for (let i = 0; i < 3; i++) expect(half[i]! - halfNoStar[i]!).toBeCloseTo(Math.min(SKY_PANEL_PEAK - halfNoStar[i]!, look.stars[i]! * 0.5), 5);
    // Full cloud hides them.
    expect(skyPanelPixel(look, 0.7, 1, 1)).toEqual(skyPanelPixel(look, 0.7, 1, 0));
  });

  it("follows every hour of the data without a jump and with no colour literal (the data's colours only)", () => {
    let last: number[] | null = null;
    for (let h = 0; h <= 24; h += 0.25) {
      const l: CastleLight = castleLight(h);
      const p = skyPanelPixel(castleSkyLook(l, nightLook(h > 6 && h < 22 ? 1 : 0).stars), 0.5, 0, 0);
      for (const v of p) expect(Number.isFinite(v)).toBe(true);
      if (last) for (let i = 0; i < 3; i++) expect(Math.abs(p[i]! - last[i]!)).toBeLessThan(0.2);
      last = p;
    }
    // The shader mirrors the arithmetic's constants.
    const code = Object.values(castleSkyFragment()).join("\n");
    expect(code).toContain(`${SKY_PANEL_PEAK}`);
    expect(code).toContain("castleSkyT.rgb * clamp(tex.a, 0.0, 1.0) * (1.0 - c)");
    expect(code).toContain("1.0 - vDiffuseUV.y");
  });

  it("is fed once per clock tick by DayNight.apply", () => {
    const sky = castleSkyOf(scene);
    const dn = new DayNight(scene);
    dn.apply(0, 0);
    const g0 = sky.generation;
    const mid = castleSkyLook(midnight, 1);
    expect(sky.current.zenith).toEqual(mid.zenith);
    expect(sky.current.stars).toEqual(mid.stars);
    expect(Array.from(sky.packed.slice(4, 7))).toEqual(mid.zenith.map((v) => expect.closeTo(v, 5)));
    // The plugin binds these vectors by reference (no loose floats per bind); they mirror the packed look.
    for (let r = 0; r < 5; r++) {
      const v = sky.vectors[r]!;
      expect([v.x, v.y, v.z, v.w]).toEqual([sky.packed[r * 4], sky.packed[r * 4 + 1], sky.packed[r * 4 + 2], 1]);
    }
    dn.apply(0.005, 0); // within a game minute: not re-applied
    expect(sky.generation).toBe(g0);
    dn.apply(13, 1);
    expect(sky.generation).toBeGreaterThan(g0);
    expect(sky.current.stars).toEqual([0, 0, 0]);
  });

  it("compares box distances without a returned double (beyondBox agrees with distanceToBox)", () => {
    const lo = new Vector3(-1, -2, -3), hi = new Vector3(1, 2, 3);
    for (const p of [new Vector3(0, 0, 0), new Vector3(5, 0, 0), new Vector3(1.5, 2.5, -4), new Vector3(-9, 9, 9)]) {
      const d = distanceToBox(p, lo, hi);
      for (const limit of [0, 0.4, 1, 3, 20, Infinity]) expect(beyondBox(p, lo, hi, limit)).toBe(d > limit);
    }
  });

  it("builds castle_sky (tint sky_dome) unlit, unfogged, with the sky plugin", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const specs = parseTexturesJson({ version: 1, materials: { castle_sky: { base: "sky_clouds.png", lo: "sky_clouds_lo.png", px: [1024, 512], unlit: true, tint: "sky_dome" } } });
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
    expect(specs.get("castle_sky")).toMatchObject({ tint: "sky_dome", unlit: true });
    expect((KIT_MATERIALS as Record<string, { tint?: string }>).castle_sky?.tint).toBe("sky_dome");
    const library = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0 });
    library.useSpecs(specs);
    const m = library.take("castle_sky") as StandardMaterial;
    expect(m.disableLighting).toBe(true);
    expect(m.fogEnabled).toBe(false);
    expect(m.pluginManager?.getPlugin("CastleSky")).toBeInstanceOf(CastleSkyPlugin);
    // No white flash while the texture loads: emission off, the placeholder shows.
    expect(m.emissiveColor.r).toBe(0);
    library.setSkyLight(0.2); // the glass tint leaves the ceiling alone
    expect(m.emissiveColor.r).toBe(0);
    library.dispose();
  });
});

// ------------------------------------------------------------ candles

describe("floating candles", () => {
  it("bob each group's node by amp x sin(2 pi t / period + phase): four matrix updates a hall", async () => {
    const candles = new KitCandles();
    const lods = new KitLods(scene, candles);
    const s = await streamHall(lods, candles);
    expect(candles.count).toBe(4);
    const bases = [0, 1, 2, 3].map((k) => meshNamed(s, `gh.castle_props.candles_${k}`));
    expect(kitAnimOf(bases[1]!)).toEqual({ kind: "bob", group: "candles_1", amp: 0.06, period: 5.6, phase: Math.PI / 2 });
    // Only the candles move: every other mesh keeps its frozen world matrix.
    for (const b of bases) expect(b.isWorldMatrixFrozen).toBe(false);
    expect(meshNamed(s, "gh.castle_wall_b").isWorldMatrixFrozen).toBe(true);
    const eye = new Vector3(30, 2, 5);
    for (const t of [0, 1.25, 7.9]) {
      candles.update(t, eye);
      expect(candles.movedLastFrame).toBe(4);
      bases.forEach((b, k) => expect(b.position.y).toBeCloseTo(bobOffset(0.06, PERIODS[k]!, (k * Math.PI) / 2, t), 9));
    }
    expect(Math.abs(bobOffset(0.06, 5, 0, 1.25))).toBeCloseTo(0.06, 6); // a quarter period: the top
    // The flame cards (LOD1) move with their group: Babylon draws a LOD level with its master's matrix.
    const lod = meshNamed(s, "gh.castle_props.candles_0_lod1");
    bases[0]!.computeWorldMatrix(true);
    expect(lod.getWorldMatrix()).toBe(bases[0]!.getWorldMatrix());
    expect(bases[0]!.getWorldMatrix().m[13]).toBeCloseTo(bases[0]!.position.y, 9);
    // Neither the candles nor the enchanted ceiling cost draws in the sun's shadow pass (High).
    expect(bases.some(castsSunShadow)).toBe(false);
    expect(castsSunShadow(meshNamed(s, "gh.castle_sky"))).toBe(false);
    expect(castsSunShadow(meshNamed(s, "gh.castle_wall_b"))).toBe(true);
    expect(castsSunShadow(meshNamed(s, "gh.castle_cloth"))).toBe(true);
    // Hidden cells (sees) are skipped.
    s.setVisibleCells(new Set());
    candles.update(2, eye);
    expect(candles.movedLastFrame).toBe(0);
    s.setVisibleCells(null);
    s.dispose();
    expect(candles.count).toBe(0);
    lods.dispose();
  });

  it("draw only each cell's nearest group at Low, chosen again as the camera moves", async () => {
    const candles = new KitCandles();
    const lods = new KitLods(scene, candles);
    const s = await streamHall(lods, candles);
    lods.setForced(true);
    expect(candles.isLow).toBe(true);
    const bases = [0, 1, 2, 3].map((k) => meshNamed(s, `gh.castle_props.candles_${k}`));
    let cam = cameraAt(41, 6, 4);
    candles.update(1, cam.globalPosition);
    expect(candles.movedLastFrame).toBe(1);
    expect(lods.select(bases[2]!, cam)).toBe(bases[2]);
    for (const k of [0, 1, 3]) expect(lods.select(bases[k]!, cam)).toBeNull();
    cam = cameraAt(-1, 6, 4);
    candles.update(1.1, cam.globalPosition);
    // Group 0 is nearest now, drawn as its LOD1 (forced at Low).
    expect(lods.select(bases[0]!, cam)!.name).toBe("gh.castle_props.candles_0_lod1");
    expect(lods.select(bases[2]!, cam)).toBeNull();
    // Medium again: all four, LOD1 only beyond 40 m.
    lods.setForced(false);
    candles.update(1.2, cam.globalPosition);
    expect(candles.movedLastFrame).toBe(4);
    for (const b of bases) expect(lods.select(b, cam)).toBe(b);
    cam = cameraAt(-60, 6, 4);
    expect(lods.select(bases[0]!, cam)!.name).toBe("gh.castle_props.candles_0_lod1");
    s.dispose();
    lods.dispose();
  });
});

// ------------------------------------------------------------ LOD and Low

describe("group LOD and Low", () => {
  it("draws portrait bands' LOD1 beyond one storey, and everywhere at Low", async () => {
    const candles = new KitCandles();
    const lods = new KitLods(scene, candles);
    const s = await streamHall(lods, candles);
    const band = meshNamed(s, "gh.castle_portraits_a.l0");
    const bandLod = meshNamed(s, "gh.castle_portraits_a.l0_lod1");
    expect(lods.count).toBe(2); // the candle group's flame cards and the band
    // Medium: LOD0 within 9 m of the band's box, LOD1 beyond.
    let cam = cameraAt(0, 2, -15);
    expect(lods.select(band, cam)).toBe(band);
    cam = cameraAt(0, 2, -5);
    expect(lods.select(band, cam)).toBe(bandLod);
    // Low: the LOD1 frames near and far (nothing is hidden by distance).
    lods.setForced(true);
    cam = cameraAt(0, 2, -15);
    expect(lods.select(band, cam)).toBe(bandLod);
    cam = cameraAt(0, 2, -5);
    expect(lods.select(band, cam)).toBe(bandLod);
    s.dispose();
    lods.dispose();
  });

  it("keeps banners (castle_cloth) at any distance at Low", async () => {
    const candles = new KitCandles();
    const lods = new KitLods(scene, candles);
    const s = await streamHall(lods, candles);
    const cloth = meshNamed(s, "gh.castle_cloth");
    let cam = cameraAt(0, 9, 25);
    expect(lods.select(cloth, cam)).toBe(cloth); // Medium: always
    lods.setForced(true);
    expect(lods.select(cloth, cam)).toBe(cloth); // 35 m away
    cam = cameraAt(0, 9, 15);
    expect(lods.select(cloth, cam)).toBe(cloth); // 25 m
    // A mesh without kit rules keeps Babylon's own choice at Low too.
    const wall = meshNamed(s, "gh.castle_wall_b");
    expect(lods.select(wall, cam)).toBe(wall);
    s.dispose();
    lods.dispose();
  });
});

// ------------------------------------------------------------ seats

describe("seats for the sit emote", () => {
  const cells = [
    { id: "great_hall", seats: [[38.5, 10.45, 81.5, 10.85, 0.45, 0], [98.0, 12.0, 98.5, 12.5, 1.08, -Math.PI / 2], [1, 2, 3]] },
    { id: "elsewhere" },
    { id: "bad", seats: "nope" },
  ];

  it("reads the manifest rows, skipping malformed ones", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const seats = Seats.fromCells(cells);
    expect(seats.count).toBe(2);
    expect(warn).toHaveBeenCalledTimes(2);
    warn.mockRestore();
  });

  it("puts a sitter within 0.6 m on the nearest seat's top, facing its way, on the seat", () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const seats = Seats.fromCells(cells);
    const out: SeatPlace = seatPlace();
    // Standing beside the north bench of a house table.
    expect(seats.find(60, 0, 10.0, out)).toBe(true);
    expect(out).toMatchObject({ x: 60, y: 0.45, z: expect.closeTo(10.65, 9), yaw: 0 });
    // An older six-number row says nothing of its depth: unfitted.
    expect(out.ahead).toBeNaN();
    // Along the bench the sitter keeps their place, short of its ends.
    expect(seats.find(38.4, 0, 10.6, out)).toBe(true);
    expect(out.x).toBeCloseTo(38.75);
    // Out of reach: no seat (the sit plays where the character stands).
    expect(seats.find(60, 0, 10.45 - SEAT_REACH - 0.05, out)).toBe(false);
    // On the dais: the staff chair (0.6 m up), facing the hall.
    expect(seats.find(98.25, 0.6, 12.9, out)).toBe(true);
    expect(out).toMatchObject({ x: expect.closeTo(98.25, 9), y: 1.08, z: expect.closeTo(12.25, 9), yaw: -Math.PI / 2 });
    // A seat a storey below or above is not taken.
    expect(seats.find(60, 9, 10.6, out)).toBe(false);
    vi.restoreAllMocks();
  });
});

// ------------------------------------------------------------ toon rows

describe("castle toon rows (M2 leftovers 6 and 7a)", () => {
  it("fades the trim's crisp step, rim and highlight with distance like the outline fade", () => {
    const trim = worldRamp("castle_trim");
    expect(trim.farStart).toBe(22);
    expect(trim.farEnd).toBe(40);
    const a = new Float32Array(4);
    const b = new Float32Array(4);
    const c = new Float32Array(4);
    packRamp(trim, a, b, c);
    expect([c[2], c[3]]).toEqual([22, 40]);
    // Off by default (walls, characters), and an end before the start turns it off.
    expect(worldRamp("castle_wall_a").farEnd).toBe(0);
    expect(resolveRamp({ farStart: 30, farEnd: 20 }).farEnd).toBe(0);
  });

  it("lifts and warms the vault row so ribs and webs separate", () => {
    const vault = worldRamp("castle_ceiling");
    const wall = worldRamp("castle_wall_a");
    expect(vault.shadowLight).toBeGreaterThan(wall.shadowLight);
    // Castle D1 (docs/art/castle-kit.md "Shading: the AO bake"): the vault shadow is cool, never brown (it was warm in M3 + M4).
    expect(vault.shadowTint[2]).toBeGreaterThan(vault.shadowTint[0]);
    // Review M3+M4: no extra shadow saturation, or every down-facing soffit (the Grand Staircase's
    // galleries, under the brown hemisphere ground light) reads orange.
    expect(vault.saturation).toBe(0);
    expect(WORLD_TOON.materials.castle_ceiling).toBeDefined();
  });
});

// ------------------------------------------------------------ the colour rule

describe("colours come from palette data (docs/art/castle-kit.md)", () => {
  it("has no #RRGGBB or numeric Color3 literal in client/src/game/castle/**", () => {
    const dir = join(__dirname);
    const files = readdirSync(dir).filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"));
    expect(files).toContain("sky.ts");
    const bad: string[] = [];
    for (const f of files) {
      readFileSync(join(dir, f), "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (/#[0-9A-Fa-f]{6}(?![0-9A-Fa-f])/.test(line) || /new Color3\(\s*[-\d.]/.test(line) || /Color3\.(FromHexString|FromInts|FromArray)\(\s*[["\d#]/.test(line)) bad.push(`${f}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(bad).toEqual([]);
  });
});
