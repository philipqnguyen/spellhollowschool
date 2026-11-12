// The far castle's chunk rules (castle/hlod.ts; docs/architecture.md
// "The manifest's new fields"): drawn only outdoors, exterior-only chunks
// always there outdoors, stand-ins hidden while all their cells are loaded
// and back as soon as one unloads; no `hlod` in the manifest draws nothing.
// The set is loaded for real (the glTF loader on a NullEngine) from a small
// file with the export's node names, on the library's shared materials.
// The stone is the silhouette's own `hlod_stone` (the
// library's texture, its own toon row and the night windows), the slate
// roofs' palette colours, the window glow's night level.

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import "@babylonjs/core/Shaders/default.fragment";
import "@babylonjs/core/Shaders/default.vertex";
import exteriorJson from "../../../../data/world/exterior.json";
import { parseManifest } from "../cellplan";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Texture } from "@babylonjs/core/Materials/Textures/texture";
import { palette as KIT_PALETTE } from "../../../../data/art/castle_textures.json";
import toonPalette from "../../../../data/art/toon_palette.json";
import {
  attachRoofSlate,
  chunkOfNode,
  HLOD_STONE,
  HlodSet,
  hlodNight,
  hlodShown,
  LIT_PANE_BELOW,
  parseHlod,
  ROOF_MATERIAL,
  setHlodNight,
  slateColours,
  slateFragment,
  windowColour,
  windowFragment,
  type HlodManifest,
} from "./hlod";
import { hexToRgb01 } from "../../content/catalog";
import { CastleLibrary, parseTexturesJson } from "./materials";
import { gltfFixtureUrl } from "./gltf-fixture.test-util";

/** The manifest's `hlod` as the HLOD export writes it, from exterior.json's chunks. */
function manifestFromExterior(file = "hlod.glb"): HlodManifest {
  return parseHlod({
    file,
    tris: 40000,
    chunks: exteriorJson.chunks.map((c) => ({ id: c.id, name: c.name, cells: c.cells, bounds: { min: [0, 0, 0], max: [1, 1, 1] }, tris: 5000, draws: 2, library: ["castle_roof", "castle_wall_c@ext"] })),
  })!;
}

describe("the HLOD chunk rule", () => {
  const m = manifestFromExterior();
  const standIn = m.chunks.find((c) => c.id === "hlod_great_hall")!;
  const exteriorOnly = m.chunks.find((c) => c.id === "hlod_north")!;
  const all = new Set(standIn.cells);

  it("draws nothing indoors", () => {
    for (const c of m.chunks) {
      expect(hlodShown(c, false, () => false), c.id).toBe(false);
      expect(hlodShown(c, false, () => true), c.id).toBe(false);
    }
  });

  it("draws the exterior-only chunks whenever the player is outdoors", () => {
    expect(exteriorOnly.cells).toEqual([]);
    expect(hlodShown(exteriorOnly, true, () => false)).toBe(true);
    expect(hlodShown(exteriorOnly, true, () => true)).toBe(true);
  });

  it("hides a stand-in while every one of its cells is loaded, and shows it when one unloads", () => {
    expect(standIn.cells.length).toBeGreaterThan(1);
    expect(hlodShown(standIn, true, (c) => all.has(c))).toBe(false);
    const missing = standIn.cells[0]!;
    expect(hlodShown(standIn, true, (c) => all.has(c) && c !== missing)).toBe(true);
    expect(hlodShown(standIn, true, () => false)).toBe(true);
  });

  it("covers exterior.json: four stand-ins and four exterior-only chunks, every playable cell in one chunk at most", () => {
    expect(m.chunks.length).toBeGreaterThanOrEqual(6);
    // + hlod_clock_court (Wings A) and hlod_owl_loft (Wings B), 11 in all.
    expect(m.chunks.length).toBeLessThanOrEqual(11);
    const seen = new Set<string>();
    for (const c of m.chunks) for (const cell of c.cells) {
      expect(seen.has(cell), cell).toBe(false);
      seen.add(cell);
    }
    for (const cell of exteriorJson.no_outside) expect(seen.has(cell), cell).toBe(false);
  });
});

describe("the manifest's hlod entry", () => {
  it("is optional: without it nothing is drawn", () => {
    expect(parseManifest({ cells: [] }).hlod).toBeUndefined();
    expect(parseHlod(undefined)).toBeNull();
    expect(parseHlod(null)).toBeNull();
  });

  it("passes through the manifest and is checked", () => {
    const entry = { file: "hlod.glb", tris: 1, chunks: [{ id: "hlod_x", cells: ["a"] }, { id: "hlod_y" }] };
    const parsed = parseHlod(parseManifest({ cells: [], hlod: entry }).hlod)!;
    expect(parsed.chunks.map((c) => [c.id, c.name, c.cells])).toEqual([["hlod_x", "hlod_x", ["a"]], ["hlod_y", "hlod_y", []]]);
    expect(parseHlod({ file: "../x.glb", chunks: [] })).toBeNull();
    expect(parseHlod({ file: "hlod.glb", chunks: [{ id: 3 }] })).toBeNull();
    expect(parseHlod({ file: "hlod.glb", chunks: [{ id: "a", cells: [1] }] })).toBeNull();
  });

  it("names chunks by their nodes (grid.hlod_node)", () => {
    expect(chunkOfNode("hlod.hlod_court.castle_wall_c@ext")).toBe("hlod_court");
    expect(chunkOfNode("hlod.hlod_north.castle_roof")).toBe("hlod_north");
    expect(chunkOfNode("hlod.hlod_north.castle_roof_primitive1")).toBe("hlod_north");
    expect(chunkOfNode("courtyard.castle_wall_c@ext")).toBeNull();
    expect(chunkOfNode("hlod.")).toBeNull();
  });
});

describe("the loaded far castle", () => {
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

  const settle = async (set: HlodSet) => {
    for (let i = 0; i < 50 && !set.counts().loaded; i++) await new Promise((r) => setTimeout(r, 5));
  };

  it("shows and hides whole chunks on the library's shared materials, never picked or shadowed", async () => {
    const lib = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0 });
    lib.useSpecs(
      parseTexturesJson({
        version: 1,
        materials: {
          castle_wall_c: { base: "stone_c.png", lo: "stone_c_lo.png", px: [1024, 1024] },
          castle_roof: { base: "roof_slate.png", lo: "roof_slate_lo.png", px: [1024, 1024] },
        },
      }),
    );
    const url = gltfFixtureUrl([
      { name: "hlod.hlod_court.castle_wall_c@ext", material: "castle_wall_c@ext", colour: 0.8 },
      { name: "hlod.hlod_court.castle_roof", material: "castle_roof" },
      { name: "hlod.hlod_north.castle_wall_c@ext", material: "castle_wall_c@ext", x: 20 },
      { name: "hlod.hlod_north.castle_roof", material: "castle_roof", x: 20 },
    ]);
    const manifest = parseHlod({ file: url, tris: 8, chunks: [{ id: "hlod_court", cells: ["courtyard", "entrance_hall"] }, { id: "hlod_north", cells: [] }] })!;
    const set = new HlodSet(scene, "", manifest, lib);
    await settle(set);
    expect(set.counts()).toEqual({ shown: 0, total: 2, loaded: true });
    const court = set.meshesOf("hlod_court");
    const north = set.meshesOf("hlod_north");
    expect(court.length).toBe(2);
    expect(north.length).toBe(2);
    // The library's materials: the file holds one reference on each (its chunks share them);
    // the stone draws as the silhouette's own `hlod_stone` on the library's texture.
    expect(court[0]!.material!.name).toBe(HLOD_STONE);
    expect(court[0]!.material).toBe(north[0]!.material);
    expect(court[0]!.material).toBe(set.stoneMaterial);
    expect(court[1]!.material!.name).toBe("castle_roof");
    expect(set.stoneMaterial!.pluginManager?.getPlugin("HlodWindows")).toBeTruthy();
    expect(set.stoneMaterial!.pluginManager?.getPlugin("Toon")).toBeTruthy();
    expect(lib.refs("castle_wall_c@ext")).toBe(1);
    expect(lib.refs("castle_roof")).toBe(1);
    for (const m of [...court, ...north]) {
      expect(m.isPickable).toBe(false);
      expect(m.receiveShadows).toBe(false);
      expect(m.isEnabled()).toBe(false);
    }
    const loaded = new Set<string>();
    set.update(true, (c) => loaded.has(c));
    expect(court.every((m) => m.isEnabled())).toBe(true);
    expect(north.every((m) => m.isEnabled())).toBe(true);
    loaded.add("courtyard");
    set.update(true, (c) => loaded.has(c));
    expect(court.every((m) => m.isEnabled())).toBe(true);
    loaded.add("entrance_hall");
    set.update(true, (c) => loaded.has(c));
    expect(court.some((m) => m.isEnabled())).toBe(false);
    expect(north.every((m) => m.isEnabled())).toBe(true);
    expect(set.counts().shown).toBe(1);
    // Indoors: nothing.
    set.update(false, (c) => loaded.has(c));
    expect([...court, ...north].some((m) => m.isEnabled())).toBe(false);
    // It follows the library's texture as it arrives (or swaps on a quality change).
    const libStone = scene.getMaterialByName("castle_wall_c@ext") as StandardMaterial;
    const tex = new Texture(null, scene);
    libStone.diffuseTexture = tex;
    scene.onBeforeRenderObservable.notifyObservers(scene);
    expect(set.stoneMaterial!.diffuseTexture).toBe(tex);
    set.dispose();
    expect(scene.getMaterialByName(HLOD_STONE)).toBeNull();
    expect(lib.refs("castle_wall_c@ext")).toBe(0);
    expect(lib.refs("castle_roof")).toBe(0);
    lib.dispose();
  });

  it("draws nothing when its file cannot load", async () => {
    const set = new HlodSet(scene, "", parseHlod({ file: "missing-hlod.glb", tris: 0, chunks: [{ id: "hlod_x" }] })!);
    await new Promise((r) => setTimeout(r, 30));
    set.update(true, () => false);
    expect(set.counts()).toEqual({ shown: 0, total: 1, loaded: false });
    set.dispose();
  });
});

describe("the far castle by night and its slate", () => {
  it("lights the marked panes in the palette's window_night, by the night level only", () => {
    expect(windowColour()).toEqual(hexToRgb01((KIT_PALETTE as Record<string, string>).window_night!));
    const code = windowFragment([1, 0.5, 0.25])["CUSTOM_FRAGMENT_BEFORE_FOG"]!;
    expect(code).toContain("hlodNight");
    expect(code).toContain(`step(${LIT_PANE_BELOW.toFixed(3)}, vColor.r)`);
    expect(code).toContain("vec3(1.0000, 0.5000, 0.2500)");
    // The export's lit pane (0.22 linear, as an sRGB byte) reads below the threshold, an unlit one (0.30) above.
    const srgb = (v: number) => (Math.round(v * 255) / 255) ** (1 / 2.2);
    expect(srgb(0.22)).toBeLessThan(LIT_PANE_BELOW);
    expect(srgb(0.3)).toBeGreaterThan(LIT_PANE_BELOW);
    setHlodNight(2);
    expect(hlodNight()).toBe(1);
    setHlodNight(Number.NaN);
    expect(hlodNight()).toBe(0);
  });

  it("has a cool toon row of its own for the stone (no warm mauve shadow side)", () => {
    const row = (toonPalette.materials as Record<string, { shadowTint: number[] }>)[HLOD_STONE]!;
    expect(row).toBeTruthy();
    const [r, , b] = row.shadowTint as [number, number, number];
    expect(b).toBeGreaterThan(r);
  });

  it("maps the slate texture's tones onto the palette's roof_slate and roof_slate_joint", () => {
    const c = slateColours()!;
    expect(c.slate).toEqual(hexToRgb01((KIT_PALETTE as Record<string, string>).roof_slate!));
    expect(c.joint).toEqual(hexToRgb01((KIT_PALETTE as Record<string, string>).roof_slate_joint!));
    // Dark weathered grey-blue: blue a little over red, never the bright slate blue.
    expect(c.slate[2]).toBeGreaterThan(c.slate[0]);
    expect(c.slate[2] - c.slate[0]).toBeLessThan(0.12);
    expect(Math.max(...c.slate)).toBeLessThan(0.5);
    expect(slateColours({})).toBeNull();
    const code = slateFragment(c)["CUSTOM_FRAGMENT_UPDATE_ALPHA"]!;
    expect(code).toContain("#ifdef ROOF_SLATE");
  });

  it("attaches the slate to castle_roof only, once", () => {
    const engine = new NullEngine();
    const scene = new Scene(engine);
    const roof = new StandardMaterial(ROOF_MATERIAL, scene);
    const wall = new StandardMaterial("castle_wall_c", scene);
    expect(attachRoofSlate(roof)).toBe(true);
    expect(attachRoofSlate(roof)).toBe(false);
    expect(attachRoofSlate(wall)).toBe(false);
    expect(roof.pluginManager?.getPlugin("RoofSlate")).toBeTruthy();
    scene.dispose();
    engine.dispose();
  });
});
