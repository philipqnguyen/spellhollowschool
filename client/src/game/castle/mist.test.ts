// The grounds by night (castle/mist.ts): which materials
// take the night tint (the foliage's meshes only on the shared props atlas),
// the tint's palette colours, the night level, and the lake's low mist (night
// only, Medium and High, on the lake's own geometry, one draw).

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MeshBuilder } from "@babylonjs/core/Meshes/meshBuilder";
import "@babylonjs/core/Shaders/default.fragment";
import "@babylonjs/core/Shaders/default.vertex";
import { palette as KIT_PALETTE } from "../../../../data/art/castle_textures.json";
import { hexToRgb01 } from "../../content/catalog";
import {
  attachNightTint,
  FOLIAGE_PREFIX,
  groundsNight,
  LAKE_MESH,
  LakeMist,
  MIST_LAYERS,
  mistFragment,
  mistShown,
  nightFragment,
  nightTintOf,
  nightTints,
  packLayers,
  setGroundsNight,
} from "./mist";
import { outdoorLook } from "../scene";
import { nightLook } from "../clock/daynight";

const PAL = KIT_PALETTE as Record<string, string>;

describe("the night tint", () => {
  it("names the grass, the backdrop, the lake and the foliage", () => {
    expect(nightTintOf("grounds_terrain")).toEqual({ kind: "grounds", foliageOnly: false });
    expect(nightTintOf("backdrop_forest")).toEqual({ kind: "grounds", foliageOnly: false });
    expect(nightTintOf("backdrop_crag")).toEqual({ kind: "grounds", foliageOnly: false });
    expect(nightTintOf("lake_water")).toEqual({ kind: "water", foliageOnly: false });
    expect(nightTintOf("castle_props")).toEqual({ kind: "grounds", foliageOnly: true });
    for (const n of ["castle_wall_c@ext", "castle_roof", "castle_floor_grass", "hlod_stone", "water"]) expect(nightTintOf(n), n).toBeNull();
  });

  it("darkens and cools by the palette's multipliers", () => {
    const t = nightTints();
    expect(t.grounds).toEqual(hexToRgb01(PAL.night_grounds!));
    expect(t.water).toEqual(hexToRgb01(PAL.night_water!));
    for (const c of [t.grounds, t.water]) {
      expect(Math.max(...c)).toBeLessThan(0.85); // darker
      expect(c[2]).toBeGreaterThan(c[0]); // cooler
    }
    expect(nightTints({})).toEqual({ grounds: [1, 1, 1], water: [1, 1, 1] });
    expect(nightFragment([0.5, 0.5, 1])["CUSTOM_FRAGMENT_BEFORE_FOG"]).toContain("groundsNight");
  });

  it("is 1 - daylight from the clock, clamped", () => {
    expect(nightLook(1).grounds).toBe(0);
    expect(nightLook(0).grounds).toBe(1);
    expect(nightLook(1).windows).toBe(0);
    expect(nightLook(0).windows).toBe(1);
    setGroundsNight(3);
    expect(groundsNight()).toBe(1);
    setGroundsNight(-1);
    expect(groundsNight()).toBe(0);
  });
});

describe("on materials and meshes", () => {
  let engine: NullEngine;
  let scene: Scene;

  beforeEach(() => {
    engine = new NullEngine();
    scene = new Scene(engine);
  });

  afterEach(() => {
    scene.dispose();
    engine.dispose();
  });

  it("attaches by name, once; the props atlas tints only the foliage's meshes", () => {
    const props = new StandardMaterial("castle_props", scene);
    const wall = new StandardMaterial("castle_wall_b", scene);
    expect(attachNightTint(props)).toBe(true);
    expect(attachNightTint(props)).toBe(false);
    expect(attachNightTint(wall)).toBe(false);
    const tree = MeshBuilder.CreateBox(`${FOLIAGE_PREFIX}tree_pine`, {}, scene);
    const desk = MeshBuilder.CreateBox("great_hall.castle_props", {}, scene);
    tree.material = props;
    desk.material = props;
    const plugin = props.pluginManager!.getPlugin("NightTint")!;
    const defines = (on: typeof tree) => {
      const d: Record<string, unknown> = {};
      plugin.prepareDefines(d as never, scene, on);
      return d["NIGHT_TINT"];
    };
    expect(defines(tree)).toBe(true);
    expect(defines(desk)).toBe(false);
  });

  it("outdoorLook gives castle_roof its slate and the grass its night", () => {
    const roof = new StandardMaterial("castle_roof", scene);
    const grass = new StandardMaterial("grounds_terrain", scene);
    outdoorLook(roof);
    outdoorLook(grass);
    expect(roof.pluginManager?.getPlugin("RoofSlate")).toBeTruthy();
    expect(roof.pluginManager?.getPlugin("NightTint") ?? null).toBeNull();
    expect(grass.pluginManager?.getPlugin("NightTint")).toBeTruthy();
  });

  it("lays the mist on the lake's geometry: one mesh, three layers, night only, not Low", async () => {
    const mist = new LakeMist(scene);
    expect(mist.layers).toBeNull();
    const lake = MeshBuilder.CreateGround(LAKE_MESH, { width: 100, height: 100 }, scene);
    await new Promise((r) => setTimeout(r, 0)); // the scene announces a new mesh on the next tick
    mist.update(0, 0); // ... and the mist attaches on the update after that
    const layers = mist.layers!;
    expect(layers).toBeTruthy();
    expect(layers.geometry).toBe(lake.geometry);
    expect(layers.thinInstanceCount).toBe(MIST_LAYERS.length);
    expect(layers.material!.name).toBe("lake_mist");
    expect(layers.isPickable).toBe(false);
    mist.setQuality("medium");
    mist.update(0, 0);
    expect(layers.isEnabled()).toBe(false);
    mist.update(1, 1000);
    expect(layers.isEnabled()).toBe(true);
    lake.setEnabled(false);
    mist.update(1, 2000);
    expect(layers.isEnabled()).toBe(false);
    lake.setEnabled(true);
    mist.setQuality("low");
    mist.update(1, 3000);
    expect(layers.isEnabled()).toBe(false);
    lake.dispose();
    expect(mist.layers).toBeNull();
    mist.dispose();
    expect(scene.getMaterialByName("lake_mist")).toBeNull();
  });

  it("packs the layers' heights and colours the mist from the palette", () => {
    const m = packLayers([0.5, 2]);
    expect(m.length).toBe(32);
    expect(m[13]).toBeCloseTo(0.5);
    expect(m[29]).toBeCloseTo(2);
    expect(mistShown(0.5, true, "high")).toBe(true);
    expect(mistShown(0, true, "high")).toBe(false);
    const [r, g, b] = hexToRgb01(PAL.lake_mist!);
    const code = mistFragment([r, g, b])["CUSTOM_FRAGMENT_BEFORE_FOG"]!;
    expect(code).toContain(`vec3(${r.toFixed(4)}, ${g.toFixed(4)}, ${b.toFixed(4)})`);
    expect(code).toContain("mistNight");
  });
});
