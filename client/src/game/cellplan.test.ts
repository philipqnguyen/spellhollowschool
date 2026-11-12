import { describe, expect, it } from "vitest";
import { distanceToBounds, parseManifest, planCells, type CellInfo } from "./cellplan";

const cell = (id: string, minX: number, maxX: number): CellInfo => ({
  id,
  file: `${id}.glb`,
  bounds: { min: [minX, 0, 0], max: [maxX, 10, 10] },
  level: 0,
});

describe("distanceToBounds", () => {
  it("is zero inside and euclidean outside", () => {
    const b = { min: [0, 0, 0] as [number, number, number], max: [10, 10, 10] as [number, number, number] };
    expect(distanceToBounds([5, 5, 5], b)).toBe(0);
    expect(distanceToBounds([13, 5, 5], b)).toBe(3);
    expect(distanceToBounds([13, 14, 5], b)).toBe(5);
  });
});

describe("planCells", () => {
  const cells = [cell("near", 0, 10), cell("mid", 50, 60), cell("far", 200, 210)];
  const radii = { load: 60, unload: 90 };

  it("loads cells within the load radius, nearest first", () => {
    const plan = planCells([...cells].reverse(), [5, 5, 5], new Set(), radii);
    expect(plan.load.map((c) => c.id)).toEqual(["near", "mid"]);
    expect(plan.unload).toEqual([]);
  });

  it("keeps resident cells until they pass the larger unload radius", () => {
    // Player at x = 85: "near" is 75 m away (beyond load, within unload).
    const plan = planCells(cells, [85, 5, 5], new Set(["near"]), radii);
    expect(plan.unload).toEqual([]);
    expect(plan.load.map((c) => c.id)).toEqual(["mid"]);
    const later = planCells(cells, [105, 5, 5], new Set(["near", "mid"]), radii);
    expect(later.unload).toEqual(["near"]);
  });
});

describe("parseManifest", () => {
  it("accepts the documented format and rejects broken entries", () => {
    expect(parseManifest({ cells: [cell("a", 0, 1)] }).cells).toHaveLength(1);
    expect(() => parseManifest({})).toThrow();
    expect(() => parseManifest({ cells: [{ id: "x" }] })).toThrow();
  });
});
