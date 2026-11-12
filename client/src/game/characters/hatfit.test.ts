import { describe, expect, it } from "vitest";
import { coverAt, coveredDeep, fitHair, hatFitFor, limitAt, scaleHat, type HatFitTable } from "./hatfit";
import { fitHat, type ManifestEntry } from "./library";
import type { PartData, PartPrimitive } from "./parts";
import type { PartRequest } from "./recipe";

/** A part of one primitive with the given positions and triangles. */
function part(positions: number[], indices: number[], extra: Partial<PartPrimitive> = {}): PartData {
  const n = positions.length / 3;
  const prim: PartPrimitive = {
    name: "p",
    material: "m",
    region: null,
    lod: 0,
    positions: Float32Array.from(positions),
    normals: null,
    mask: null,
    base: [1, 1, 1],
    uvs: null,
    joints: new Uint16Array(n * 4),
    weights: new Float32Array(n * 4),
    indices: Uint32Array.from(indices),
    morphs: new Map(),
    ...extra,
  };
  return { bones: ["head"], primitives: [prim], hides: [], long: false, skeleton: null, clips: [], atlas: null };
}

// Head centre at y = 1.6; a cover line at 60 degrees all round (12 samples, 30 degrees apart).
const TABLE: HatFitTable = {
  centre: [0, 1.6, 0],
  cover_step: 30,
  cut_margin: 1.5,
  hats: { "gear/hat/beanie": { scale: 1.2, cover: Array(12).fill(60) } },
};

describe("hat fit", () => {
  it("reads a fit from the manifest table and rejects malformed ones", () => {
    const fit = hatFitFor(TABLE, "gear/hat/beanie")!;
    expect(fit.scale).toBe(1.2);
    expect(fit.centre).toEqual([0, 1.6, 0]);
    expect(hatFitFor(TABLE, "gear/hat/other")).toBeNull();
    expect(hatFitFor(undefined, "gear/hat/beanie")).toBeNull();
    expect(hatFitFor({ ...TABLE, cover_step: 0 }, "gear/hat/beanie")).toBeNull();
    expect(hatFitFor({ ...TABLE, hats: { "gear/hat/beanie": { scale: 1.1, cover: [60, 60] } } }, "gear/hat/beanie")).toBeNull();
    // Never shrinks a hat, never more than doubles it.
    expect(hatFitFor({ ...TABLE, hats: { "gear/hat/beanie": { scale: 0.5, cover: Array(12).fill(60) } } }, "gear/hat/beanie")!.scale).toBe(1);
  });

  it("interpolates the cover line round the head", () => {
    const fit = hatFitFor({ ...TABLE, hats: { h: { scale: 1, cover: [40, 80, ...Array(10).fill(60)] } } }, "h")!;
    expect(coverAt(fit, 0)).toBeCloseTo(40);
    expect(coverAt(fit, 15)).toBeCloseTo(60);
    expect(coverAt(fit, 30)).toBeCloseTo(80);
    expect(coverAt(fit, 345)).toBeCloseTo(50);
    expect(coverAt(fit, -15)).toBeCloseTo(50);
  });

  it("drops only the hair wholly under the crown and keeps the inner cap", () => {
    const fit = hatFitFor(TABLE, "gear/hat/beanie")!;
    const top = (dx: number, dz: number) => [dx, 1.7, dz]; // near straight up: deep under the crown
    const low = (dx: number, dz: number) => [dx, 1.6, dz + 0.1]; // level with the centre, in front: uncovered
    expect(coveredDeep(fit, 0, 1.7, 0.01)).toBe(true);
    expect(coveredDeep(fit, 0, 1.6, 0.1)).toBe(false);
    const hair = part(
      [...top(0, 0.01), ...top(0.01, 0), ...top(-0.01, 0), ...low(0, 0), ...top(0, -0.01)],
      [0, 1, 2, 0, 1, 3, 1, 2, 4],
    );
    const kept = fitHair(hair, fit).primitives[0]!.indices;
    // The all-deep triangles go; the one reaching below the cover stays (the hat covers its upper corners).
    expect(Array.from(kept)).toEqual([0, 1, 3]);
    const capped = part(Array.from(hair.primitives[0]!.positions), [0, 1, 2, 0, 1, 3], { scalp: Uint8Array.from([1, 1, 1, 0, 0]) });
    expect(Array.from(fitHair(capped, fit).primitives[0]!.indices)).toEqual([0, 1, 2, 0, 1, 3]);
    // Nothing covered: the same part back.
    const free = part([...low(0, 0), ...low(0.01, 0), ...low(0, 0.01)], [0, 1, 2]);
    expect(fitHair(free, fit)).toBe(free);
  });

  it("presses the hair left under a capped hat's crown in to its reach, and nothing below the cover line", () => {
    const table: HatFitTable = {
      ...TABLE,
      limit_rows: [0, 3, 6, 9, 12],
      hats: { "gear/hat/beanie": { scale: 1.15, cover: Array(12).fill(60), limit: Array(5).fill(Array(12).fill(0.1)) } },
    };
    const fit = hatFitFor(table, "gear/hat/beanie")!;
    expect(limitAt(fit, 0, 2)).toBeCloseTo(0.1);
    expect(limitAt({ ...fit, limit: null }, 0, 2)).toBe(0);
    // Under the crown, a little inside the cover line (59 degrees), 0.15 m out: pressed to 0.1 m.
    const ph = (59 * Math.PI) / 180;
    const under = [0, 1.6 + 0.15 * Math.cos(ph), 0.15 * Math.sin(ph)];
    // Below the cover line (70 degrees), 0.15 m out: untouched.
    const pl = (70 * Math.PI) / 180;
    const below = [0, 1.6 + 0.15 * Math.cos(pl), 0.15 * Math.sin(pl)];
    const hair = part([...under, ...below, 0.01, 1.6 + 0.15 * Math.cos(pl), 0.15 * Math.sin(pl)], [0, 1, 2]);
    const out = fitHair(hair, fit).primitives[0]!.positions;
    expect(Math.hypot(out[0]!, out[1]! - 1.6, out[2]!)).toBeCloseTo(0.1);
    expect(out[4]).toBeCloseTo(below[1]!);
    expect(out[5]).toBeCloseTo(below[2]!);
    // The file's own positions are untouched.
    expect(hair.primitives[0]!.positions[2]).toBeCloseTo(under[2]!);
  });

  it("scales the hat about the head centre, morph offsets too", () => {
    const fit = hatFitFor(TABLE, "gear/hat/beanie")!;
    const morphs = new Map([["build", { positions: Float32Array.from([0, 0.01, 0]), normals: null }]]);
    const hat = part([0, 1.7, 0.1], [], { morphs });
    const out = scaleHat(hat, fit).primitives[0]!;
    expect(out.positions[1]).toBeCloseTo(1.6 + 0.1 * 1.2);
    expect(out.positions[2]).toBeCloseTo(0.12);
    expect(out.morphs.get("build")!.positions[1]).toBeCloseTo(0.012);
    // The file's own part is untouched (the library caches it).
    expect(hat.primitives[0]!.positions[1]).toBeCloseTo(1.7);
    expect(scaleHat(hat, { ...fit, scale: 1 })).toBe(hat);
  });

  it("fits the hat over hair-under-hat files that carry a fit, and leaves the rest alone", () => {
    const reqs: PartRequest[] = [
      { role: "hair", asset: "hair/crop", files: ["hair/crop_cap_body_a.glb"], cap: true },
      { role: "hat", asset: "gear/hat/beanie", files: ["gear/hat/beanie_body_a.glb"] },
    ];
    const hair = part([0, 1.7, 0.01, 0.01, 1.7, 0, -0.01, 1.7, 0], [0, 1, 2]);
    const hat = part([0, 1.75, 0], []);
    const manifest = new Map<string, ManifestEntry>([["hair/crop", { hat_fit: { body_a: TABLE } }]]);
    const loaded: (PartData | null)[] = [hair, hat];
    fitHat(reqs, loaded, manifest, "body_a");
    expect(loaded[0]!.primitives[0]!.indices.length).toBe(0);
    expect(loaded[1]!.primitives[0]!.positions[1]).toBeCloseTo(1.6 + 0.15 * 1.2);
    // Another body, or a hair with no fit: as loaded.
    const again: (PartData | null)[] = [hair, hat];
    fitHat(reqs, again, manifest, "body_b");
    expect(again[0]).toBe(hair);
    expect(again[1]).toBe(hat);
    fitHat(reqs, again, new Map(), "body_a");
    expect(again[1]).toBe(hat);
    // A style worn under hats in its own file (no hat variant) is fitted too.
    const own: (PartData | null)[] = [hair, hat];
    fitHat([{ ...reqs[0]!, cap: false }, reqs[1]!], own, manifest, "body_a");
    expect(own[0]!.primitives[0]!.indices.length).toBe(0);
  });
});
