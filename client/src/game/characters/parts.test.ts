import { describe, expect, it } from "vitest";
import { Gltf, GltfError, glbBuilder, isGlb } from "./gltf";
import { partFromGltf, regionFromName, sortSkeleton } from "./parts";
import { fixtureGlb, FIXTURE_BONES } from "./fixtures";

const read = (bytes: ArrayBuffer) => partFromGltf(Gltf.parse(bytes));

describe("glb reader", () => {
  it("recognises glb files and rejects anything else", () => {
    expect(isGlb(fixtureGlb())).toBe(true);
    expect(isGlb(new TextEncoder().encode("<!doctype html>").buffer)).toBe(false);
    expect(() => Gltf.parse(new ArrayBuffer(4))).toThrow(GltfError);
  });

  it("decodes normalised integer accessors and sparse values", () => {
    const b = glbBuilder({ asset: { version: "2.0" } });
    const acc = b.add(new Uint8Array([0, 255, 51, 102]), "VEC4", { normalized: true });
    const base = b.add(new Float32Array([1, 2, 3, 4]), "SCALAR");
    const idx = b.add(new Uint16Array([2]), "SCALAR");
    const val = b.add(new Float32Array([9]), "SCALAR");
    b.json.accessors!.push({
      ...b.json.accessors![base]!,
      sparse: { count: 1, indices: { bufferView: b.json.accessors![idx]!.bufferView!, componentType: 5123 }, values: { bufferView: b.json.accessors![val]!.bufferView! } },
    });
    const g = Gltf.parse(b.build());
    const f = g.floats(acc);
    [0, 1, 0.2, 0.4].forEach((v, i) => expect(f[i]).toBeCloseTo(v));
    expect(Array.from(g.floats(b.json.accessors!.length - 1))).toEqual([1, 2, 9, 4]);
  });

  it("refuses files that need extensions it cannot decode", () => {
    const b = glbBuilder({ asset: { version: "2.0" }, extensionsRequired: ["EXT_meshopt_compression"] });
    expect(() => Gltf.parse(b.build())).toThrow(/EXT_meshopt_compression/);
  });
});

describe("partFromGltf", () => {
  it("reads the skeleton, skinning and region of a body part", () => {
    const part = read(fixtureGlb());
    expect(part.skeleton?.bones).toEqual(FIXTURE_BONES);
    expect(Array.from(part.skeleton!.parents)).toEqual([-1, 0, 1]);
    expect(part.primitives).toHaveLength(1);
    const p = part.primitives[0]!;
    expect(p.region).toBe("torso");
    expect(p.material).toBe("skin");
    expect(p.positions).toHaveLength(12);
    // Bottom vertices on DEF-spine, top ones on DEF-spine.006 (by name).
    expect(part.bones[p.joints[0]!]).toBe("DEF-spine");
    expect(part.bones[p.joints[8]!]).toBe("DEF-spine.006");
    expect(p.weights[0]).toBe(1);
  });

  it("binds joints by name whatever order the file lists them in", () => {
    const part = read(fixtureGlb({ jointOrder: ["DEF-spine.006", "root", "DEF-spine"] }));
    const p = part.primitives[0]!;
    expect(part.bones[p.joints[0]!]).toBe("DEF-spine");
    expect(part.bones[p.joints[8]!]).toBe("DEF-spine.006");
    // The skeleton is reordered parents-first.
    expect(part.skeleton!.bones).toEqual(["root", "DEF-spine", "DEF-spine.006"]);
  });

  it("reads hides, long, dye masks, morph targets and LOD nodes", () => {
    const part = read(fixtureGlb({ extras: { hides: ["torso", "upper_arms"], long: true }, colors: true, morph: "build_heavy", lodNode: true, meshName: "robe" }));
    expect(part.hides).toEqual(["torso", "upper_arms"]);
    expect(part.long).toBe(true);
    const [lod0, lod1] = part.primitives;
    expect(lod0!.lod).toBe(0);
    expect(lod1!.lod).toBe(1);
    expect(Array.from(lod0!.mask!.subarray(0, 3))).toEqual([1, 0, 0]);
    expect(Array.from(lod0!.mask!.subarray(6, 9))).toEqual([0, 1, 0]);
    expect(lod0!.morphs.get("build_heavy")?.positions[0]).toBeCloseTo(0.1);
  });

  it("binds unskinned meshes under a joint rigidly to it, baked into bind space", () => {
    const part = read(fixtureGlb({ rigidProp: true }));
    const prop = part.primitives.find((p) => p.name === "prop")!;
    expect(part.bones[prop.joints[0]!]).toBe("DEF-spine.006");
    // The quad's first vertex (−0.2, 1, 0) sits under the head joint (0, 1.5, 0) + 0.1.
    expect(prop.positions[1]).toBeCloseTo(1 + 1.6);
  });

  it("keeps an armature transform above the root as the root's pre-transform", () => {
    const part = read(fixtureGlb({ armatureOffset: [0, 0.5, 0] }));
    const s = part.skeleton!;
    expect(s.rootPre[13]).toBeCloseTo(0.5); // translation y of root's pre-transform
    expect(s.rootPre[16 + 13]).toBeCloseTo(0); // other bones: identity
    expect(s.inverseBind[16 + 13]).toBeCloseTo(-1.5);
  });

  it("reads animations by bone name", () => {
    const part = read(fixtureGlb({ animation: "Armature|Walk" }));
    expect(part.clips).toHaveLength(1);
    const clip = part.clips[0]!;
    expect(clip.name).toBe("Armature|Walk");
    expect(clip.duration).toBe(1);
    expect(clip.tracks.get("DEF-spine")?.rotation?.values).toHaveLength(8);
  });
});

describe("regions and skeleton order", () => {
  it("finds regions at the end of node names", () => {
    expect(regionFromName("body_a_upper_arms")).toBe("upper_arms");
    expect(regionFromName("body_b_head_lod2")).toBe("head");
    expect(regionFromName("hands")).toBe("hands");
    expect(regionFromName("robe")).toBeNull();
  });

  it("sorts parents before children", () => {
    const s = sortSkeleton({
      bones: ["c", "a", "b"],
      parents: Int16Array.from([2, -1, 1]),
      rest: new Float32Array(30),
      inverseBind: new Float32Array(48),
      rootPre: new Float32Array(48),
    });
    expect(s.bones).toEqual(["a", "b", "c"]);
    expect(Array.from(s.parents)).toEqual([-1, 0, 1]);
  });
});
