// The clothing pass (docs/architecture.md):
// hidden covered geometry and the sitting drape.
import { describe, expect, it } from "vitest";
import { Rig } from "./clips";
import { assemble, COVER_SCALE, COVER_TOLERANCE_M, coverAbove, DRAPE_KEYS, uncoveredIndices, type PartInput, type PartRole } from "./assemble";
import { coversFrom, COVER_STEP_M, type PartData, type PartPrimitive } from "./parts";
import { greyboxSkeleton } from "./greybox";
import { Animator } from "./animator";
import type { BakedClip, ClipKey } from "./clips";

/** A garment of two triangles over body vertices 0..3 (heights in metres), with optional covers and drape keys. */
function garment(bodyVertex: number[], bodyHeight: number[], covers: number[] | null = null, drape = false): PartData {
  const n = bodyVertex.length;
  const morphs = new Map<string, { positions: Float32Array; normals: null }>();
  if (drape) for (const [k, key] of DRAPE_KEYS.entries()) morphs.set(key, { positions: new Float32Array(n * 3).fill(k + 1), normals: null });
  const prim: PartPrimitive = {
    name: "g_lod0",
    material: "",
    region: null,
    lod: 0,
    positions: new Float32Array(n * 3),
    normals: null,
    mask: null,
    base: [1, 1, 1],
    uvs: null,
    joints: new Uint16Array(n * 4),
    weights: new Float32Array(n * 4).map((_, i) => (i % 4 === 0 ? 1 : 0)),
    indices: Uint32Array.from([0, 1, 2, 1, 3, 2]),
    morphs,
    bodyVertex: Float32Array.from(bodyVertex),
    bodyHeight: Float32Array.from(bodyHeight),
  };
  return { bones: ["root"], primitives: [prim], hides: [], long: false, skeleton: null, clips: [], atlas: null, covers: covers ? Uint8Array.from(covers) : null };
}

const step = (m: number) => Math.round(m / COVER_STEP_M);

describe("hidden covered geometry", () => {
  it("reads the covers from base64", () => {
    expect(Array.from(coversFrom(btoa(String.fromCharCode(0, 5, 255)))!)).toEqual([0, 5, 255]);
    expect(coversFrom("")).toBeNull();
    expect(coversFrom(3)).toBeNull();
  });

  it("takes the covers of higher layers only (shoes < legs < body)", () => {
    const body = garment([0, 1, 2, 3], [0.01, 0.01, 0.01, 0.01], [step(0.02), 0, step(0.05), 0]);
    const legs = garment([0, 1, 2, 3], [0.005, 0.005, 0.005, 0.005], [step(0.01), step(0.01), 0, 0]);
    const parts: PartInput[] = [
      { role: "body", part: body },
      { role: "legs", part: legs },
    ];
    expect(coverAbove(parts, "body")).toBeNull();
    const overLegs = coverAbove(parts, "legs")!;
    expect(overLegs[0]).toBeCloseTo(0.02);
    expect(overLegs[1]).toBe(0);
    const overShoes = coverAbove(parts, "shoes")!;
    expect(overShoes[1]).toBeCloseTo(0.01); // the legs cover the shoes too
    expect(overShoes[2]).toBeCloseTo(0.05);
    expect(coverAbove(parts, "hat" as PartRole)).toBeNull();
  });

  it("drops a triangle only when all its vertices lie under a cover, not too far out", () => {
    const cover = Float32Array.from([0.02, 0.02, 0.02, 0]);
    const prim = garment([0, 1, 2, 3], [0.01, 0.01, 0.01, 0.01]).primitives[0]!;
    // Triangle 0-1-2 is covered; 1-3-2 has vertex 3 over an uncovered body vertex.
    expect(Array.from(uncoveredIndices(prim, cover))).toEqual([1, 3, 2]);
    // A vertex further out than the cover's tolerance stays (a boot shaft over the tights).
    const out = garment([0, 1, 2, 3], [0.01, 0.01, 0.02 * COVER_SCALE + COVER_TOLERANCE_M + 0.001, 0.01]).primitives[0]!;
    expect(uncoveredIndices(out, cover)).toBe(out.indices);
    // No cover, or a garment without anchors: unchanged.
    expect(uncoveredIndices(prim, null)).toBe(prim.indices);
    expect(uncoveredIndices({ ...prim, bodyVertex: null }, cover)).toBe(prim.indices);
  });

  it("leaves the covered triangles out of the merged mesh", () => {
    const rig = new Rig(greyboxSkeleton(0));
    const parts: PartInput[] = [
      { role: "body", part: garment([0, 1, 2, 3], [0.01, 0.01, 0.01, 0.01], [step(0.03), step(0.03), step(0.03), step(0.03)]) },
      { role: "legs", part: garment([0, 1, 2, 3], [0.005, 0.005, 0.005, 0.04]) },
    ];
    const m = assemble({ rig, parts, lod: 0, skin: [1, 1, 1], hair: [1, 1, 1], shapeWeights: new Map() });
    expect(m.coveredTriangles).toBe(1);
    expect(m.indices.length).toBe(9); // the robe's two triangles and the trousers' uncovered one
  });
});

describe("sitting drape", () => {
  it("carries the garments' drape keys out of assembly, zero elsewhere", () => {
    const rig = new Rig(greyboxSkeleton(0));
    const parts: PartInput[] = [
      { role: "body", part: garment([0, 1, 2, 3], [0, 0, 0, 0]) },
      { role: "legs", part: garment([0, 1, 2, 3], [0, 0, 0, 0], null, true) },
    ];
    const m = assemble({ rig, parts, lod: 0, skin: [1, 1, 1], hair: [1, 1, 1], shapeWeights: new Map([["drape_chair", 1]]) });
    const chair = m.drape.drape_chair!;
    expect(chair.length).toBe(m.vertexCount * 3);
    expect(Array.from(chair.subarray(0, 12)).every((v) => v === 0)).toBe(true);
    expect(Array.from(chair.subarray(12)).every((v) => v === 1)).toBe(true);
    expect(m.drape.drape_floor![12]).toBe(2);
    expect(m.drape.drape_rest![12]).toBe(3);
    // Never baked into the positions, whatever the shape weights say.
    expect(m.positions[12]).toBe(0);
    const plain = assemble({ rig, parts: [parts[0]!], lod: 0, skin: [1, 1, 1], hair: [1, 1, 1], shapeWeights: new Map() });
    expect(plain.drape.drape_chair).toBeNull();
  });

  it("turns the chair key on through the sit-down, the floor key with the floor sit and the rest key with the floor rest", () => {
    const clip = (name: string, duration: number, loop: boolean, next?: BakedClip): BakedClip =>
      ({ name, duration, frames: 2, loop, rot: new Float32Array(), pos: new Float32Array(), speed: 0, procedural: true, next }) as unknown as BakedClip;
    const sit = clip("sit", 3, true);
    const clips = new Map<ClipKey, BakedClip>([
      ["idle", clip("idle", 4, true)],
      ["sit", clip("sit_down", 1.2, false, sit)],
      ["floor_sit", clip("floor_sit", 6, true)],
      ["sit_floor", clip("floor_rest", 3, true)],
    ]);
    const a = new Animator(clips, 0);
    const w = new Float32Array(4);
    a.set("idle", 0);
    a.update(0.1);
    a.drapeWeights(w);
    expect(w[0]).toBe(0);
    a.set("sit", 0);
    a.update(0.2);
    a.drapeWeights(w);
    expect(w[0]).toBeGreaterThan(0);
    expect(w[0]).toBeLessThan(0.2); // early in the sit-down
    a.update(1.5); // into the sitting loop, fully faded in
    a.drapeWeights(w);
    expect(w[0]).toBeCloseTo(1);
    expect(w[1]).toBe(0);
    a.set("floor_sit", 0);
    a.update(2);
    a.drapeWeights(w);
    expect(w[0]).toBe(0);
    expect(w[1]).toBeCloseTo(1);
    a.set("sit_floor", 0);
    a.update(2);
    a.drapeWeights(w);
    expect(w[1]).toBe(0);
    expect(w[2]).toBeCloseTo(1); // the floor rest has a key of its own (knees up)
  });
});
