import { describe, expect, it } from "vitest";
import { CULL_NEAR_M, fadePair, foliageLod, LOD_HYSTERESIS, LOD_MIN_M, nextLod, nodeName, plantsOf, rowMatrix, type FoliageKitInfo } from "./foliage";
import { parseManifest } from "../cellplan";
import { nightTintOf } from "./mist";
import { parseTexturesJson } from "./materials";
import { worldRamp } from "../toon/palette";

const KIT: FoliageKitInfo = {
  file: "foliage_kit.glb",
  lods: 4,
  species: { pine_a: { size: 9, tris: [2000, 700, 400, 144] }, hedge_a: { size: 1.5, tris: [330, 150, 60, 16] } },
};

describe("foliage LOD by size on screen", () => {
  it("steps at 25 %, 10 % and 4 % of the screen's height for a tall tree", () => {
    const fov = 0.9;
    const span = 2 * Math.tan(fov / 2);
    const at = (share: number) => 9 / (share * span);
    expect(foliageLod(9, at(0.25) - 1, fov)).toBe(0);
    expect(foliageLod(9, at(0.25) + 1, fov)).toBe(1);
    expect(foliageLod(9, at(0.1) + 1, fov)).toBe(2);
    expect(foliageLod(9, at(0.04) + 1, fov)).toBe(3);
  });

  it("keeps small hedges whole to LOD_MIN_M and honours a cell's min_lod", () => {
    expect(foliageLod(1.5, LOD_MIN_M[0] - 0.5, 0.9)).toBe(0);
    expect(foliageLod(1.5, LOD_MIN_M[0] + 0.5, 0.9)).toBe(1);
    expect(foliageLod(9, 5, 0.9, 3)).toBe(3);
    expect(CULL_NEAR_M).toBeGreaterThan(LOD_MIN_M[1]);
  });

  it("needs the distance past a threshold by the hysteresis before it changes LOD", () => {
    const at = LOD_MIN_M[0];
    expect(nextLod(-1, 1.5, at + 0.1, 0.9)).toBe(1); // first sight: no hysteresis
    expect(nextLod(0, 1.5, at + 0.1, 0.9)).toBe(0); // just past: holds
    expect(nextLod(0, 1.5, at * (1 + LOD_HYSTERESIS) + 0.1, 0.9)).toBe(1);
    expect(nextLod(1, 1.5, at - 0.1, 0.9)).toBe(1); // just back: holds
    expect(nextLod(1, 1.5, at / (1 + LOD_HYSTERESIS) - 0.1, 0.9)).toBe(0);
  });

  it("crossfades with complementary dithers: every pixel draws one of the two LODs", () => {
    for (const k of [0, 0.1, 0.5, 0.9, 0.999]) {
      const [fin, fout] = fadePair(k);
      expect(fout).toBe(-fin);
      for (let i = 0; i < 16; i++) {
        const d = (i + 0.5) / 16;
        const keepIn = d < fin;
        const keepOut = !(d < -fout);
        expect(keepIn !== keepOut).toBe(true);
      }
    }
    expect(fadePair(2)[0]).toBe(1);
  });

  it("names the kit's nodes", () => {
    expect(nodeName("pine_a", 0)).toBe("foliage.pine_a");
    expect(nodeName("pine_a", 2)).toBe("foliage.pine_a_lod2");
  });
});

describe("foliage manifest rows", () => {
  const row = [0, 2, 0, 0, 0, 2, 0, 0, 0, 2, 10, 1, -5];

  it("become Babylon world matrices (basis rows, translation last)", () => {
    const m = rowMatrix(row);
    expect(Array.from(m)).toEqual([2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 10, 1, -5, 1]);
  });

  it("size a planting by its species and scale, skipping unknown species", () => {
    const plants = plantsOf("grounds_r0_c0", { species: ["pine_a", "oak_x"], plants: [row, [1, ...row.slice(1)]] }, KIT);
    expect(plants).toHaveLength(1);
    expect(plants[0]!.size).toBeCloseTo(18);
    expect(plants[0]!.cy).toBeCloseTo(1 + 9);
    expect(plants[0]!.minLod).toBe(0);
    expect(plantsOf("x", { species: ["pine_a"], plants: [row], min_lod: 3 }, KIT)[0]!.minLod).toBe(3);
  });

  it("are read from the manifest with the kit", () => {
    const m = parseManifest({
      cells: [{ id: "foliage", file: "foliage.glb", bounds: { min: [0, 0, 0], max: [1, 1, 1] }, level: 0, foliage: { species: ["pine_a"], plants: [row] } }],
      foliage_kit: KIT,
    });
    expect(m.foliageKit).toEqual(KIT);
    expect(m.cells[0]!.foliage?.plants).toHaveLength(1);
  });
});

describe("the castle_foliage material", () => {
  it("lights both faces with the authored normal, takes the grounds' night tint and its own toon row", () => {
    const specs = parseTexturesJson({
      version: 1,
      materials: { castle_foliage: { base: "foliage_atlas.png", lo: "foliage_atlas_lo.png", px: [2048, 1024], alpha: "mask", alpha_cutoff: 0.5, double_sided: true, shared_normals: true } },
    });
    const s = specs.get("castle_foliage")!;
    expect(s.alpha).toBe("mask");
    expect(s.doubleSided && s.sharedNormals).toBe(true);
    expect(nightTintOf("castle_foliage")).toEqual({ kind: "grounds", foliageOnly: false });
    const r = worldRamp("castle_foliage");
    expect(r.softness).toBeCloseTo(0.14);
    expect(r.shadowTint[2]).toBeGreaterThan(r.shadowTint[0]); // cool blue-green shadows
  });
});
