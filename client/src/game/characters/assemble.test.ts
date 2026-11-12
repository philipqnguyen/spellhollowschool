import { describe, expect, it } from "vitest";
import { Gltf } from "./gltf";
import { partFromGltf, type PartData } from "./parts";
import { Rig } from "./clips";
import { assemble, hasLod, hiddenRegions, lodFor, type PartInput } from "./assemble";
import { bodyChannels, dyeColours, finishAlpha, finishOfAlpha, hairChannels, packColour, tintVertex } from "./dye";
import { regionIndex, regionOfAlpha, toonAlpha } from "../toon/palette";
import { whiteTexel } from "./atlas";
import { greyboxKind, greyboxPart, greyboxSkeleton, type GreyboxKind } from "./greybox";
import { fixtureGlb } from "./fixtures";
import { PALETTE, paletteRgb } from "../../content/catalog";

const read = (o = {}) => partFromGltf(Gltf.parse(fixtureGlb(o)));
const SKIN: [number, number, number] = [0.8, 0.6, 0.5];
const HAIR: [number, number, number] = [0.2, 0.1, 0.05];

function merge(parts: PartInput[], rig: Rig, lod = 0, shape = new Map<string, number>(), whiteUv: [number, number] | null = null) {
  return assemble({ rig, parts, lod, skin: SKIN, hair: HAIR, shapeWeights: shape, whiteUv });
}

describe("dye mapping", () => {
  it("maps palette indices to sRGB colours", () => {
    const [p, s, t] = dyeColours([16, 21, 0]);
    expect(p).toEqual(paletteRgb(16));
    expect(s).toEqual(paletteRgb(21));
    expect(t).toEqual(paletteRgb(0));
    expect(PALETTE.colours[16]!.hex).toBe("#7F0909");
  });

  it("tints by mask channel and keeps the base where unmasked (the atlas contract)", () => {
    const dyes = dyeColours([16, 21, 0]);
    const out = [0, 0, 0];
    const white = [1, 1, 1];
    tintVertex(white, [0, 1, 0], 0, dyes, out, 0);
    expect(out).toEqual(dyes[1]);
    tintVertex(white, [0, 0, 0], 0, dyes, out, 0);
    expect(out).toEqual(white); // a boot sole keeps its own colour
    tintVertex(white, [0.5, 0, 0.5], 0, dyes, out, 0);
    expect(out[0]).toBeCloseTo((dyes[0][0] + dyes[2][0]) / 2);
    tintVertex(white, null, 0, dyes, out, 0);
    expect(out).toEqual(dyes[0]); // no mask: all channel R
    // Half masked: half the base, half the dye; the base factor multiplies everything.
    tintVertex([0.5, 0.5, 0.5], [0.5, 0, 0], 0, dyes, out, 0);
    expect(out[0]).toBeCloseTo(0.5 * (0.5 + 0.5 * dyes[0][0]));
    // Weights above 1 are normalised.
    tintVertex(white, [1, 1, 0], 0, dyes, out, 0);
    expect(out[1]).toBeCloseTo((dyes[0][1] + dyes[1][1]) / 2);
  });

  it("uses skin and hair colour on the body, hair colour on hair", () => {
    const out = [0, 0, 0];
    tintVertex([1, 1, 1], [0, 1, 0], 0, bodyChannels(SKIN, HAIR), out, 0);
    expect(out).toEqual(HAIR); // brows take the hair colour
    tintVertex([1, 1, 1], [1, 0, 0], 0, hairChannels(HAIR), out, 0);
    expect(out).toEqual(HAIR);
    const bytes = new Uint8Array(4);
    packColour([1, 0.5, 2], 0, bytes, 0);
    expect(Array.from(bytes)).toEqual([255, 128, 255, 255]);
  });
});

describe("assemble", () => {
  const body = read();
  const rig = new Rig(body.skeleton!);

  it("remaps joints by name onto the rig", () => {
    const gear = read({ jointOrder: ["DEF-spine.006", "DEF-spine", "root"], meshName: "shirt" });
    const m = merge([{ role: "base", part: body }, { role: "body", part: gear, dyes: [1, 2, 3] }], rig);
    expect(m.vertexCount).toBe(8);
    // Vertex 4 is the gear's first: on DEF-spine (rig index 1) whatever the gear's order.
    expect(m.joints[4 * 4]).toBe(rig.boneIndex("DEF-spine"));
    expect(m.joints[6 * 4]).toBe(rig.boneIndex("DEF-spine.006"));
    expect(m.indices[6]).toBe(4); // second part's indices are offset
    expect(m.unknownBones).toEqual([]);
  });

  it("drops body regions the gear hides", () => {
    const robe = read({ meshName: "robe", extras: { hides: ["torso"] } });
    const parts: PartInput[] = [{ role: "base", part: body }, { role: "body", part: robe, dyes: [0, 0, 0] }];
    expect(hiddenRegions(parts)).toEqual(new Set(["torso"]));
    const m = merge(parts, rig);
    expect(m.vertexCount).toBe(4); // only the robe
    expect(m.hidden).toEqual(["torso"]);
  });

  it("long robes hide the legs", () => {
    const robe = read({ meshName: "robe", extras: { long: true } });
    expect(hiddenRegions([{ role: "body", part: robe }])).toEqual(new Set(["thighs", "calves"]));
  });

  it("quantises weights to bytes that sum to 255", () => {
    const part: PartData = read();
    part.primitives[0]!.weights.set([0.333, 0.333, 0.334, 0], 0);
    const m = merge([{ role: "base", part }], new Rig(part.skeleton!));
    expect(m.weights[0]! + m.weights[1]! + m.weights[2]! + m.weights[3]!).toBe(255);
  });

  it("writes each garment's finish into its vertex colours' alpha", () => {
    const shirt = read({ meshName: "shirt" });
    const hat = read({ meshName: "hat" });
    const m = merge(
      [
        { role: "base", part: body },
        { role: "body", part: shirt, dyes: [1, 2, 3], finish: "shimmer" },
        { role: "hat", part: hat, dyes: [1, 2, 3], finish: "colour_shift" },
      ],
      rig,
    );
    const alpha = (v: number) => m.colors[v * 4 + 3]!;
    // Body vertices 0-3, the shirt 4-7, the hat 8-11.
    expect([alpha(0), alpha(3)].map(finishOfAlpha)).toEqual(["none", "none"]);
    expect([alpha(4), alpha(7)].map(finishOfAlpha)).toEqual(["shimmer", "shimmer"]);
    expect([alpha(8), alpha(11)].map(finishOfAlpha)).toEqual(["colour_shift", "colour_shift"]);
    // The same byte carries the toon region: skin on the body, the dye channel on gear (no mask: primary).
    expect(regionOfAlpha(alpha(0))).toBe(regionIndex("skin"));
    expect(regionOfAlpha(alpha(4))).toBe(regionIndex("primary"));
    expect(regionOfAlpha(alpha(8))).toBe(regionIndex("primary"));
    expect(finishAlpha(undefined)).toBe(toonAlpha(regionIndex("neutral"), "none"));
    // Geometry made before the toon look (255) reads as neutral, no finish.
    expect(finishOfAlpha(255)).toBe("none");
    expect(regionOfAlpha(255)).toBe(regionIndex("neutral"));
    for (const f of ["none", "shimmer", "colour_shift"] as const) expect(finishOfAlpha(finishAlpha(f))).toBe(f);
  });

  it("bakes shape keys and colours", () => {
    const gear = read({ morph: "build_heavy", colors: true, meshName: "shirt" });
    const m = merge([{ role: "body", part: gear, dyes: [16, 21, 0] }], rig, 0, new Map([["build_heavy", 0.5]]));
    expect(m.positions[0]).toBeCloseTo(-0.2 + 0.05);
    // Vertex 0 is masked red (primary), vertex 2 green (secondary). Base is white (shade 1).
    const p = paletteRgb(16).map((c) => Math.round(c * 255));
    const s = paletteRgb(21).map((c) => Math.round(c * 255));
    expect(Array.from(m.colors.subarray(0, 3))).toEqual(p);
    expect(Array.from(m.colors.subarray(8, 11))).toEqual(s);
    // The body takes the skin tone.
    const b = merge([{ role: "base", part: body }], rig);
    expect(b.colors[0]).toBe(Math.round(SKIN[0] * 255));
  });

  it("keeps atlas coordinates and points untextured parts at the white texel", () => {
    const textured = read({ texture: true });
    expect(textured.atlas?.mimeType).toBe("image/png");
    expect(textured.atlas?.bytes[0]).toBe(0x89);
    expect(textured.primitives[0]!.uvs).not.toBeNull();
    const plain = read({ meshName: "shirt" });
    const parts: PartInput[] = [{ role: "base", part: textured }, { role: "body", part: plain, dyes: [0, 0, 0] }];
    const m = merge(parts, rig, 0, new Map(), [0.5, 0.25]);
    expect(Array.from(m.uvs!.subarray(0, 2))).toEqual([Math.fround(0.1), Math.fround(0.9)]);
    expect(Array.from(m.uvs!.subarray(8, 10))).toEqual([0.5, 0.25]);
    expect(merge(parts, rig).uvs).toBeNull(); // no atlas: no coordinates
  });

  it("finds the whitest texel of an atlas", () => {
    const rgba = new Uint8Array(4 * 4 * 4).fill(100);
    rgba.set([255, 255, 255, 255], (2 * 4 + 1) * 4); // x 1, y 2
    expect(whiteTexel(rgba, 4, 4)).toEqual([1.5 / 4, 2.5 / 4]);
  });

  it("uses each part's best LOD and falls back to full detail", () => {
    const withLod = read({ lodNode: true, meshName: "shirt" });
    expect(lodFor(withLod, 1)).toBe(1);
    expect(lodFor(body, 1)).toBe(0);
    const parts: PartInput[] = [{ role: "base", part: body }, { role: "body", part: withLod, dyes: [0, 0, 0] }];
    expect(hasLod(parts, 1)).toBe(true);
    expect(hasLod([{ role: "base", part: body }], 1)).toBe(false);
    expect(merge(parts, rig, 1).vertexCount).toBe(8);
  });
});

describe("greybox", () => {
  const kinds: GreyboxKind[] = [
    "body",
    "hair_short",
    "hair_long",
    "hair_tail",
    "hair_bun",
    "hair_volume",
    "hat_pointed",
    "hat_knit",
    "robe",
    "cloak",
    "shirt",
    "shirt_rolled",
    "jumper",
    "trousers",
    "skirt",
    "shoes",
    "boots",
  ];

  it("builds a valid skeleton for both bodies (parents first, sane heights)", () => {
    for (const b of [0, 1]) {
      const s = greyboxSkeleton(b);
      s.parents.forEach((p, i) => expect(p).toBeLessThan(i));
      const rig = new Rig(s);
      expect(rig.bonePos("DEF-spine.006")![1]).toBeGreaterThan(1.3);
      expect(rig.bonePos("DEF-upper_arm.L")![0]).toBeGreaterThan(0); // left is +X
    }
  });

  it("makes every kind with valid indices, rig bones and both LODs", () => {
    const rig = new Rig(greyboxSkeleton(0));
    for (const kind of kinds) {
      const part = greyboxPart(kind, rig);
      expect(part.primitives.length, kind).toBeGreaterThan(0);
      for (const name of part.bones) expect(rig.boneIndex(name), `${kind} ${name}`).toBeGreaterThanOrEqual(0);
      for (const p of part.primitives) {
        const n = p.positions.length / 3;
        expect(p.indices.length % 3).toBe(0);
        for (const i of p.indices) expect(i).toBeLessThan(n);
        for (const [, morph] of p.morphs) expect(morph.positions.length).toBe(n * 3);
      }
      expect(hasLod([{ role: "base", part }], 1), kind).toBe(true);
      const lod0 = part.primitives.filter((p) => p.lod === 0).reduce((s, p) => s + p.positions.length, 0);
      const lod1 = part.primitives.filter((p) => p.lod === 1).reduce((s, p) => s + p.positions.length, 0);
      expect(lod1, kind).toBeLessThanOrEqual(lod0); // boxes (shoes) have nothing to drop
    }
  });

  it("winds every greybox triangle counter-clockwise seen from outside (glTF front faces)", () => {
    const rig = new Rig(greyboxSkeleton(0));
    for (const kind of kinds) {
      for (const cap of [false, true]) {
        const part = greyboxPart(kind, rig, { cap });
        let agree = 0;
        let total = 0;
        for (const p of part.primitives) {
          const P = p.positions;
          const N = p.normals!;
          for (let t = 0; t < p.indices.length; t += 3) {
            const [a, b, c] = [p.indices[t]! * 3, p.indices[t + 1]! * 3, p.indices[t + 2]! * 3];
            const u = [P[b]! - P[a]!, P[b + 1]! - P[a + 1]!, P[b + 2]! - P[a + 2]!];
            const v = [P[c]! - P[a]!, P[c + 1]! - P[a + 1]!, P[c + 2]! - P[a + 2]!];
            const n = [u[1]! * v[2]! - u[2]! * v[1]!, u[2]! * v[0]! - u[0]! * v[2]!, u[0]! * v[1]! - u[1]! * v[0]!];
            if (Math.hypot(n[0]!, n[1]!, n[2]!) < 1e-9) continue; // degenerate (cone tips)
            const vn = [N[a]! + N[b]! + N[c]!, N[a + 1]! + N[b + 1]! + N[c + 1]!, N[a + 2]! + N[b + 2]! + N[c + 2]!];
            total++;
            if (n[0]! * vn[0]! + n[1]! * vn[1]! + n[2]! * vn[2]! > 0) agree++;
          }
        }
        expect(agree / total, `${kind}${cap ? " cap" : ""}`).toBeGreaterThan(0.97);
      }
    }
  });

  it("dresses a greybox character within the per-character triangle budget", () => {
    const rig = new Rig(greyboxSkeleton(1));
    const parts: PartInput[] = (["body", "hair_long", "hat_pointed", "robe", "trousers", "boots"] as GreyboxKind[]).map((k, i) => ({
      role: (["base", "hair", "hat", "body", "legs", "shoes"] as const)[i]!,
      part: greyboxPart(k, rig, { cap: k === "hair_long" }),
      dyes: [0, 16, 21],
    }));
    const m = merge(parts, rig, 0, new Map([["build_heavy", 1]]));
    expect(m.unknownBones).toEqual([]);
    expect(m.hidden).toContain("thighs");
    expect(m.indices.length / 3).toBeLessThan(30000);
    expect(m.min[1]).toBeGreaterThanOrEqual(-0.01);
    expect(m.max[1]).toBeGreaterThan(1.6); // the hat's tip
  });

  it("picks shapes from asset words", () => {
    expect(greyboxKind("body", "gear/body/robe_school_house_a")).toBe("robe");
    expect(greyboxKind("legs", "gear/legs/skirt_pleated_a")).toBe("skirt");
    expect(greyboxKind("shoes", "gear/shoes/boots_lace_a")).toBe("boots");
    expect(greyboxKind("hat", "gear/hat/bobble_knit_a")).toBe("hat_knit");
    expect(greyboxKind("hair", "hair/ponytail_high")).toBe("hair_tail");
    expect(greyboxKind("hair", "hair/afro")).toBe("hair_volume");
    expect(greyboxKind("body", "gear/body/sweater_cable_knit_a")).toBe("jumper");
    expect(greyboxKind("body", "gear/body/shirt_linen_rolled_a")).toBe("shirt_rolled");
  });
});
