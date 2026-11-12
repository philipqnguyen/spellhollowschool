// The face pass (docs/art/character-look.md section 3): the face albedo, the
// palette index packing, the face region, the face-shadow map's lit share,
// the face textures' manifest entry and the material's face defines.
import { describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import { Texture } from "@babylonjs/core/Materials/Textures/texture";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import { CREATOR, eyeColourHex, hairColourHex, hexToRgb01, skinToneHex } from "../../content/catalog";
import {
  claimLiveHairSlot,
  firstLiveHairSlot,
  hairPaletteEntry,
  releaseLiveHairSlot,
  setLiveHairColour,
  FACE_FRINGE_STEP,
  FACE_PALETTE_SIZE,
  FACE_SDF_SOFTNESS,
  faceAlbedo,
  FACE_FLAT_LEVEL,
  FACE_IRIS_GLOW,
  FACE_LINE_FLOOR,
  FACE_LINE_SKIN,
  faceFlatGlow,
  faceLinePaint,
  facePalettes,
  faceSdfLit,
  faceSdfSoftness,
  setToonFaceShadow,
  setToonKeyDirection,
  setToonKeyNight,
  FACE_NIGHT_MAX_ELEVATION,
  TOON_SHADE_KIND,
  toonFaceShadow,
  toonKeyDirection,
  ToonPlugin,
} from "../toon/material";
import { regionIndex, worldRamp } from "../toon/palette";
import { Gltf } from "./gltf";
import { partFromGltf, type PartData } from "./parts";
import { Rig } from "./clips";
import { assemble, paletteByte, vertexRegion } from "./assemble";
import { fixtureGlb } from "./fixtures";
import { faceTextureUrls } from "./library";
import { recipeFor } from "./recipe";

const W = [1, 1, 1];

describe("face albedo", () => {
  it("tints by skin, eye and hair through the texture's weights, for every creator colour", () => {
    const skins = CREATOR.skin_tones.map((s) => hexToRgb01(s.hex));
    const eyes = CREATOR.eye_colours.map((e) => hexToRgb01(e.hex));
    const hairs = CREATOR.hair_colours.map((h) => hexToRgb01(h.hex));
    const out = [0, 0, 0];
    for (const s of skins)
      for (const e of eyes)
        for (const h of hairs) {
          // Skin: white texel, skin weight 1 -> the skin tone.
          faceAlbedo([1, 1, 1, 1], [0, 0, 0.5, 0], s, e, h, out);
          for (let k = 0; k < 3; k++) expect(out[k]).toBeCloseTo(s[k]!);
          // A skin multiplier (blush) stays a tone of the skin.
          faceAlbedo([1, 0.93, 0.91, 1], [0, 0, 0.5, 0], s, e, h, out);
          expect(out[0]).toBeCloseTo(s[0]);
          expect(out[1]).toBeCloseTo(s[1] * 0.93);
          // Iris: greyscale, eye weight 1 -> the eye colour times the grey.
          faceAlbedo([0.5, 0.5, 0.5, 0], [1, 0, 0.5, 1], s, e, h, out);
          for (let k = 0; k < 3; k++) expect(out[k]).toBeCloseTo(0.5 * e[k]!);
          // Brow: hair weight 1.
          faceAlbedo([0.55, 0.55, 0.55, 0], [0, 1, 0.5, 0], s, e, h, out);
          for (let k = 0; k < 3; k++) expect(out[k]).toBeCloseTo(0.55 * h[k]!);
          // Eye white and highlights: untinted.
          expect(faceAlbedo([0.96, 0.95, 0.94, 0], [0, 0, 0.5, 1], s, e, h, out)).toEqual([0.96, 0.95, 0.94]);
          for (const c of out) expect(c).toBeGreaterThanOrEqual(0);
        }
  });

  it("interpolates cleanly between weights (bilinear filtering and mips)", () => {
    const s = [0.8, 0.6, 0.5];
    const a = faceAlbedo([1, 1, 1, 0.5], [0, 0, 0, 0], s, W, W);
    expect(a[0]).toBeCloseTo(0.9);
    expect(a[2]).toBeCloseTo(0.75);
  });

  // [look2 Review] the hairline and brow edges: hair laid over skin, the weights sharing a texel.
  it("never lights a halo where hair paint meets the skin, on any skin tone", () => {
    const skins = CREATOR.skin_tones.map((s) => hexToRgb01(s.hex));
    const hairs = CREATOR.hair_colours.map((h) => hexToRgb01(h.hex));
    const out = [0, 0, 0];
    const SCALP = 0.6; // face_paint.SCALP_TONE
    for (const s of skins)
      for (const h of hairs)
        for (let t = 0; t <= 1.0001; t += 0.125) {
          const p = 1 - t + SCALP * t;
          faceAlbedo([p, p, p, 1 - t], [0, t, 0.5, 0], s, W, h, out);
          for (let k = 0; k < 3; k++) expect(out[k]).toBeLessThanOrEqual(Math.max(s[k]!, SCALP * h[k]!) * 1.08); // the product of the weights lit up to 1.35x on dark skin
        }
  });
});

// [look2 Face] docs/art/character-look.md section 3.
describe("face lines on every skin tone", () => {
  const skins = CREATOR.skin_tones.map((s) => hexToRgb01(s.hex));
  const luma = (c: readonly number[]) => 0.3 * c[0]! + 0.59 * c[1]! + 0.11 * c[2]!;
  // The paint's line multipliers (charlib/face_paint.py PAINT): the nose shade, the lid crease, then the mouth
  // line and the mark under the nose tip of each LINE_OPTIONS option (current, A dark brown-black, B near-black).
  const LINES = [
    [0.9, 0.82, 0.8], [0.9, 0.83, 0.81],
    [0.58, 0.4, 0.38], [0.72, 0.56, 0.53],
    [0.3, 0.21, 0.19], [0.34, 0.24, 0.22],
    [0.15, 0.105, 0.095], [0.17, 0.12, 0.105],
  ];

  it("darkens dark skin as much, in value, as a skin of FACE_LINE_SKIN, down to the floor", () => {
    expect(FACE_LINE_SKIN).toBeGreaterThan(0.4);
    expect(FACE_LINE_FLOOR).toBeGreaterThan(0.2);
    const out = [0, 0, 0];
    for (const line of LINES)
      for (const s of skins) {
        faceAlbedo([...line, 1], [0, 0, 0.5, 0], s, W, W, out);
        const q = out[0]! / s[0]!; // the line's ratio in R
        // Deepened down to the floor, never lightened: a line darker than the floor stays as painted.
        const want = Math.min(line[0]!, Math.max(1 - (FACE_LINE_SKIN * (1 - line[0]!)) / luma(s), FACE_LINE_FLOOR));
        expect(q).toBeCloseTo(want, 3);
        // Its value drop: about the one on a skin of FACE_LINE_SKIN, unless the floor holds it.
        const drop = luma(s) - luma(out);
        if (want > FACE_LINE_FLOOR + 1e-6) expect(drop).toBeGreaterThan(FACE_LINE_SKIN * (1 - line[0]!) * 0.85);
        // Warm: the line keeps the paint's hue order (R the lightest ratio).
        expect(out[1]! / s[1]!).toBeLessThanOrEqual(q + 1e-9);
        expect(out[2]! / s[2]!).toBeLessThanOrEqual(out[1]! / s[1]! + 1e-9);
      }
  });

  it("leaves light skin's lines as painted (never heavy on light skin)", () => {
    const light = skins.filter((s) => luma(s) >= FACE_LINE_SKIN);
    expect(light.length).toBeGreaterThan(1);
    const out = [0, 0, 0];
    for (const line of LINES)
      for (const s of light) {
        faceAlbedo([...line, 1], [0, 0, 0.5, 0], s, W, W, out);
        for (let k = 0; k < 3; k++) expect(out[k]).toBeCloseTo(line[k]! * s[k]!, 6);
      }
  });

  it("never touches tints (R = 1), unskinned paint or part-skin edges", () => {
    const dark = skins[skins.length - 1]!;
    expect(faceLinePaint([1, 0.93, 0.91, 1], dark)).toEqual([1, 0.93, 0.91]);
    expect(faceLinePaint([1, 0.9, 0.9, 1], dark)).toEqual([1, 0.9, 0.9]);
    expect(faceLinePaint([0.23, 0.17, 0.16, 0], dark)).toEqual([0.23, 0.17, 0.16]); // lashes
    expect(faceLinePaint([0.6, 0.6, 0.6, 0.5], dark)).toEqual([0.6, 0.6, 0.6]); // the scalp's edge
  });
});

describe("the face mask's flat weight and glow", () => {
  it("decodes A: flat up to FACE_FLAT_LEVEL, the glow above it", () => {
    expect(FACE_FLAT_LEVEL * 255).toBeCloseTo(192, 9);
    expect(faceFlatGlow(0)).toEqual([0, 0]);
    const [f0, g0] = faceFlatGlow(192 / 255);
    expect(f0).toBe(1);
    expect(g0).toBe(0);
    expect(faceFlatGlow(1)).toEqual([1, 1]);
    const [fh, gh] = faceFlatGlow(96 / 255);
    expect(fh).toBeCloseTo(0.5, 2);
    expect(gh).toBe(0);
    const [, gm] = faceFlatGlow((192 + 31.5) / 255);
    expect(gm).toBeCloseTo(0.5, 2);
  });

  it("glows only a little (a share of the iris's own albedo)", () => {
    expect(FACE_IRIS_GLOW).toBeGreaterThan(0);
    expect(FACE_IRIS_GLOW).toBeLessThanOrEqual(0.5);
  });
});

describe("face palettes and indices", () => {
  it("holds every creator eye and hair colour at its index", () => {
    const { eye, hair } = facePalettes();
    expect(eye.length).toBe(FACE_PALETTE_SIZE * 4);
    expect(CREATOR.eye_colours.length).toBeLessThanOrEqual(FACE_PALETTE_SIZE);
    expect(CREATOR.hair_colours.length).toBeLessThanOrEqual(FACE_PALETTE_SIZE);
    for (const e of CREATOR.eye_colours) expect(Array.from(eye.subarray(e.index * 4, e.index * 4 + 3))).toEqual(hexToRgb01(e.hex).map((v) => Math.fround(v)));
    for (const h of CREATOR.hair_colours) expect(Array.from(hair.subarray(h.index * 4, h.index * 4 + 3))).toEqual(hexToRgb01(h.hex).map((v) => Math.fround(v)));
  });

  it("packs indices into bytes the shader reads back exactly", () => {
    for (let i = 0; i < FACE_PALETTE_SIZE; i++) {
      const b = paletteByte(i);
      expect(Math.floor((b / 255) * 255 + 0.5)).toBe(i);
    }
    expect(paletteByte(undefined)).toBe(0);
    expect(paletteByte(-3)).toBe(0);
  });

  it("carries the appearance's eye and hair indices in the recipe", () => {
    const r = recipeFor({ bodyType: 1, height: 128, face: [128, 128, 128, 128, 128], skinTone: 3, hairStyle: 17, hairColour: 7, eyeColour: 4, outfit: { hat: null, body: null, legs: null, shoes: null } } as never);
    expect(r.eyeIndex).toBe(4);
    expect(r.hairIndex).toBe(7);
    expect(r.eye).toEqual(hexToRgb01(eyeColourHex(4)));
    expect(r.hair).toEqual(hexToRgb01(hairColourHex(7)));
    expect(r.skin).toEqual(hexToRgb01(skinToneHex(3)));
  });
});

describe("the face shadow's key at night", () => {
  it("holds a high moon to the night elevation (keeping its bearing), and leaves the sun alone", () => {
    setToonKeyNight(0);
    setToonKeyDirection(0.1, 0.95, 0.2);
    const day = Array.from(toonKeyDirection().subarray(0, 3));
    expect(day[1]).toBeGreaterThan(0.9);
    setToonKeyNight(1);
    setToonKeyDirection(0.1, 0.95, 0.2);
    const k = toonKeyDirection();
    expect(k[1]).toBeCloseTo(Math.sin(FACE_NIGHT_MAX_ELEVATION), 5);
    expect(Math.atan2(k[0]!, k[2]!)).toBeCloseTo(Math.atan2(0.1, 0.2), 5);
    // Straight overhead: the last bearing.
    setToonKeyDirection(0, 1, 0);
    expect(Math.atan2(toonKeyDirection()[0]!, toonKeyDirection()[2]!)).toBeCloseTo(Math.atan2(0.1, 0.2), 5);
    // A low moon is unchanged.
    setToonKeyDirection(0.8, 0.3, 0);
    expect(toonKeyDirection()[1]).toBeCloseTo(0.3 / Math.hypot(0.8, 0.3), 5);
    // The face-shadow map does not fade out under it.
    const k2 = toonKeyDirection();
    expect(Math.hypot(k2[0]!, k2[2]!)).toBeGreaterThan(0.5);
    setToonKeyNight(0);
  });
});

describe("live hair slots (Chameleon Tonic on the brows and lashes)", () => {
  it("claims slots past the creator's colours, starting at the chosen colour, and frees them", () => {
    const first = firstLiveHairSlot();
    expect(first).toBe(CREATOR.hair_colours.length);
    const a = claimLiveHairSlot(3);
    expect(a).toBe(first);
    expect(hairPaletteEntry(a)).toEqual(Array.from(facePalettes().hair.subarray(12, 15)));
    setLiveHairColour(a, 0.25, 0.5, 0.75);
    expect(hairPaletteEntry(a)).toEqual([0.25, 0.5, 0.75]);
    // Creator colours are never overwritten.
    setLiveHairColour(3, 0, 0, 0);
    expect(hairPaletteEntry(3)).toEqual(Array.from(facePalettes().hair.subarray(12, 15)));
    const b = claimLiveHairSlot(0);
    expect(b).toBe(first + 1);
    releaseLiveHairSlot(a);
    expect(claimLiveHairSlot(0)).toBe(a);
    // Every slot taken: -1, and the face keeps the chosen colour.
    const taken = [a, b];
    for (let i = first + 2; i < FACE_PALETTE_SIZE; i++) taken.push(claimLiveHairSlot(0));
    expect(claimLiveHairSlot(0)).toBe(-1);
    for (const t of taken) releaseLiveHairSlot(t);
  });
});

describe("face-textured head", () => {
  const body = partFromGltf(Gltf.parse(fixtureGlb()));
  const rig = new Rig(body.skeleton!);
  const faced: PartData = {
    ...body,
    primitives: body.primitives.map((p) => ({ ...p, region: "head" as const, faceTexture: true, uvs: p.uvs ?? new Float32Array((p.positions.length / 3) * 2), mask: new Float32Array((p.positions.length / 3) * 3).fill(0) })),
  };

  it("is the face region everywhere, whatever its mask says", () => {
    expect(vertexRegion("base", { mask: new Float32Array([0, 1, 0]), region: "head", faceTexture: true }, 0, false)).toBe(regionIndex("face"));
    expect(vertexRegion("base", { mask: new Float32Array([0, 1, 0]), region: "head" }, 0, false)).toBe(regionIndex("hair"));
    // Hair and gear never take the face path.
    expect(vertexRegion("hair", { mask: new Float32Array([1, 0, 0]), faceTexture: true }, 0, false)).toBe(regionIndex("hair"));
  });

  it("writes the eye and hair indices into the face vertices' shade y and z", () => {
    const m = assemble({ rig, parts: [{ role: "base", part: faced }], lod: 0, skin: [0.8, 0.6, 0.5], hair: [0.2, 0.1, 0.05], eyeIndex: 11, hairIndex: 19, shapeWeights: new Map(), whiteUv: [0.5, 0.5] });
    expect(m.vertexCount).toBeGreaterThan(0);
    for (let v = 0; v < m.vertexCount; v++) {
      expect(m.shade[v * 4]).toBe(255); // faces open (no fringe here)
      expect(m.shade[v * 4 + 1]).toBe(11);
      expect(m.shade[v * 4 + 2]).toBe(19);
      expect(m.colors[v * 4 + 3]! >> 4).toBe(regionIndex("face"));
    }
    // Without the flag: the old layout (strand 0, band phase 0.5).
    const plain = assemble({ rig, parts: [{ role: "base", part: body }], lod: 0, skin: [0.8, 0.6, 0.5], hair: [0.2, 0.1, 0.05], eyeIndex: 11, hairIndex: 19, shapeWeights: new Map(), whiteUv: [0.5, 0.5] });
    expect(plain.shade[1]).toBe(0);
    expect(plain.shade[2]).toBe(128);
  });

  it("reads the face textures from the body's manifest entry", () => {
    expect(faceTextureUrls("/assets/", { face: { colour: "characters/face_body_b.png", mask: "characters/face_mask_body_b.png" } })).toEqual({ colour: "/assets/characters/face_body_b.png", mask: "/assets/characters/face_mask_body_b.png" });
    expect(faceTextureUrls("/assets/", {})).toBeNull();
    expect(faceTextureUrls("/assets/", undefined)).toBeNull();
    expect(faceTextureUrls("/assets/", { face: { colour: 3 } })).toBeNull();
  });
});

describe("face shadow map", () => {
  // A synthetic map: the texel's dark angle grows from 0 at u = 0 to 180 at u = 1 (the +X side lit longest).
  const map = (u: number) => u;
  const at = (u: number) => (mirror: boolean) => map(mirror ? 1 - u : u);
  const deg = (a: number): [number, number] => [Math.sin((a * Math.PI) / 180), Math.cos((a * Math.PI) / 180)];

  it("lights a texel while the light's angle is below its threshold, softly", () => {
    const [s, f] = deg(90);
    expect(faceSdfLit(s, f, at(0.9))).toBeCloseTo(1);
    expect(faceSdfLit(s, f, at(0.1))).toBeCloseTo(0);
    expect(faceSdfLit(s, f, at(0.5))).toBeCloseTo(0.5);
    // Front light: everything with a threshold above 0.02 is lit.
    const [s0, f0] = deg(0);
    expect(faceSdfLit(s0, f0, at(0.05))).toBeCloseTo(1);
  });

  it("is monotonic in the light's angle and mirrored for a light on the -X side", () => {
    for (const u of [0.1, 0.3, 0.5, 0.7, 0.9]) {
      let last = 2;
      for (let a = 0; a <= 180; a += 2.5) {
        const [s, f] = deg(a);
        const lit = faceSdfLit(s * 0.999, f, at(u));
        expect(lit).toBeLessThanOrEqual(last + 1e-9);
        last = lit;
        expect(faceSdfLit(-s * 0.999, f, at(1 - u))).toBeCloseTo(lit);
      }
    }
  });

  it("fades out for a key from overhead", () => {
    expect(faceSdfLit(0.01, 0.01, at(0))).toBeCloseTo(1);
    expect(faceSdfLit(0.3, -0.05, at(0.1))).toBeGreaterThan(0.2);
    expect(faceSdfLit(0.6, -0.3, at(0.1))).toBeCloseTo(0);
  });

  it("takes its softness from the art data, clamped", () => {
    expect(FACE_SDF_SOFTNESS).toBeCloseTo(0.02);
    expect(faceSdfSoftness(undefined)).toBe(0.02);
    expect(faceSdfSoftness(5)).toBe(0.2);
    expect(FACE_FRINGE_STEP[0]).toBeLessThan(FACE_FRINGE_STEP[1]);
  });
});

describe("the character material's face path", () => {
  const engine = new NullEngine();
  const scene = new Scene(engine);
  const mesh = CreateBox("head", {}, scene);
  mesh.setVerticesData(TOON_SHADE_KIND, new Float32Array(mesh.getTotalVertices() * 4).fill(1), false, 4);

  it("compiles TOON_FACE with face textures and TOON_FACE_SDF only at Medium and High", () => {
    const mat = new StandardMaterial("characters", scene);
    mat.diffuseTexture = new Texture(null, scene);
    const plugin = new ToonPlugin(mat, "regions");
    const defines = new MaterialDefines();
    plugin.prepareDefines(defines, scene, mesh);
    expect(defines["TOON_SHADE"]).toBe(true);
    expect(defines["TOON_FACE"]).toBe(false); // no face textures yet
    plugin.setFace({ colour: new Texture(null, scene), mask: new Texture(null, scene) });
    setToonFaceShadow(false);
    plugin.prepareDefines(defines, scene, mesh);
    expect(defines["TOON_FACE"]).toBe(true);
    expect(defines["TOON_FACE_SDF"]).toBe(false);
    setToonFaceShadow(true);
    expect(toonFaceShadow()).toBe(true);
    plugin.prepareDefines(defines, scene, mesh);
    expect(defines["TOON_FACE_SDF"]).toBe(true);
    const samplers: string[] = [];
    plugin.getSamplers(samplers);
    expect(samplers).toEqual(["faceColourSampler", "faceMaskSampler"]);
    expect(plugin.hasTexture(plugin.faceTextures!.mask)).toBe(true);
    // The face path uses the material's one draw: no second material, no extra mesh.
    expect(scene.materials.filter((m) => m.name === "characters").length).toBe(1);
    setToonFaceShadow(false);
    // A world material never takes the face path.
    const wall = new ToonPlugin(new StandardMaterial("wall", scene), worldRamp("wall"));
    wall.prepareDefines(defines, scene, mesh);
    expect(defines["TOON_FACE"]).toBe(false);
  });

  it("keeps the key direction in a preallocated vector", () => {
    const v = toonKeyDirection();
    setToonKeyDirection(0, 2, 0);
    expect(toonKeyDirection()).toBe(v);
    expect(Array.from(v.subarray(0, 3))).toEqual([0, 1, 0]);
    setToonKeyDirection(3, 0, 4);
    expect(v[0]).toBeCloseTo(0.6);
    expect(v[2]).toBeCloseTo(0.8);
  });
});
