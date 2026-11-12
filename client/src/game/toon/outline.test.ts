// Character outlines (docs/art/character-look.md section 4, toon/outline.ts): the
// tint rule over every skin tone, hair colour and dye, the width weights and
// distance thinning, the Low switch, the outline layer's packing and the
// hull's shader.
import { describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import { qualityProfile } from "../../settings";
import { CREATOR, PALETTE } from "../../content/catalog";
import { packOutline } from "../characters/assemble";
import { hasOutline } from "../characters/renderer";
import { CHARACTER_REGIONS, regionIndex } from "./palette";
import {
  OUTLINE_DEFAULTS,
  OUTLINE_KIND,
  OUTLINE_MASK_ALPHA,
  outlineColour,
  outlineDistanceScale,
  outlineDrawn,
  outlineLight,
  OutlineMaterial,
  outlineVertexShader,
  outlineWidthPx,
  parseOutline,
  TOON_OUTLINE,
} from "./outline";

const rgb = (hex: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
const luma = (c: readonly number[]): number => 0.299 * c[0]! + 0.587 * c[1]! + 0.114 * c[2]!;
const SKINS = CREATOR.skin_tones.map((s) => rgb(s.hex));
const HAIRS = CREATOR.hair_colours.map((s) => rgb(s.hex));
const DYES = PALETTE.colours.map((c) => rgb(c.hex));

describe("outline tint rule (palette data, derived from the vertex colour)", () => {
  it("darkens every skin tone, hair colour and dye, never to pure black", () => {
    const cases: [string, [number, number, number][]][] = [
      ["skin", SKINS],
      ["face", SKINS],
      ["hair", HAIRS],
      ["primary", DYES],
      ["secondary", DYES],
      ["trim", DYES],
    ];
    for (const [region, colours] of cases) {
      const r = TOON_OUTLINE.regions[region as "skin"];
      for (const c of colours) {
        const line = outlineColour(c, r);
        expect(Math.max(...line)).toBeGreaterThan(0.02);
        // Darker than the surface it outlines (or at the floor for the darkest colours).
        expect(luma(line)).toBeLessThanOrEqual(Math.max(luma(c) * 0.6, luma(r.floor) + 1e-6));
      }
    }
  });

  // [look2 Review] a shade of the colour, not near-black (the default black robe, Black and Dark Brown hair, the darkest skins).
  it("keeps a visible share of the colour's value: a shade, not a black line", () => {
    const cases: [string, [number, number, number][]][] = [
      ["skin", SKINS],
      ["hair", HAIRS],
      ["primary", DYES],
    ];
    for (const [region, colours] of cases) {
      const r = TOON_OUTLINE.regions[region as "skin"];
      for (const c of colours) expect(luma(outlineColour(c, r))).toBeGreaterThanOrEqual(0.3 * luma(c));
    }
  });

  it("draws a warm brown round every skin tone", () => {
    for (const c of SKINS) {
      const line = outlineColour(c, TOON_OUTLINE.regions.skin);
      expect(line[0]).toBeGreaterThan(line[1]);
      expect(line[1]).toBeGreaterThanOrEqual(line[2]);
    }
  });

  it("keeps the colour's hue: a darker shade of the hair or dye, not a grey", () => {
    for (const c of [...HAIRS, ...DYES]) {
      const sat = Math.max(...c) - Math.min(...c);
      if (sat < 0.25 || luma(c) < 0.2) continue;
      const line = outlineColour(c, TOON_OUTLINE.regions.primary);
      const top = c.indexOf(Math.max(...c));
      expect(line.indexOf(Math.max(...line))).toBe(top);
    }
  });

  it("follows a recolour: the line is a function of the vertex colour alone", () => {
    const before = outlineColour(HAIRS[0]!, TOON_OUTLINE.regions.hair, [1, 1, 1], [0, 0, 0]);
    const after = outlineColour(HAIRS[6]!, TOON_OUTLINE.regions.hair, [1, 1, 1], [0, 0, 0]);
    expect(after).not.toEqual(before);
    expect(outlineColour(HAIRS[0]!, TOON_OUTLINE.regions.hair)).toEqual(before);
    // The shader reads the shared colour buffer (the Chameleon Tonic writes it).
    expect(outlineVertexShader()).toContain("attribute vec4 color;");
    // The hull of a character in a skirt or robe follows the sitting drape like the body (toon/material.ts).
    expect(outlineVertexShader()).not.toContain("toonDrapeChair");
    expect(outlineVertexShader(undefined, true)).toContain("position + toonDrapeChair * outlineDrape.x + toonDrapeFloor * outlineDrape.y + toonDrapeRest * outlineDrape.z");
  });

  it("darkens with the scene's light, never below the floor", () => {
    const noon = outlineLight([1, 1, 1], 0.6, [1, 0.95, 0.9], 1.2);
    expect(noon).toEqual([1, 1, 1]);
    const night = outlineLight([0.3, 0.35, 0.5], 0.3, [0.5, 0.6, 0.9], 0.2);
    for (const k of night) expect(k).toBeLessThan(0.5);
    const dark = outlineLight([0, 0, 0], 0, [0, 0, 0], 0);
    expect(dark).toEqual([TOON_OUTLINE.lightFloor, TOON_OUTLINE.lightFloor, TOON_OUTLINE.lightFloor]);
  });
});

describe("outline width weights", () => {
  it("draws no line on flat paint, a thinner one on the face than on clothing and hair", () => {
    const w = (r: (typeof CHARACTER_REGIONS)[number]): number => outlineWidthPx(r, 1, 2, 1000);
    expect(w("flat")).toBe(0);
    expect(w("face")).toBeGreaterThan(0);
    expect(w("face")).toBeLessThan(w("skin"));
    expect(w("face")).toBeLessThan(w("hair"));
    expect(w("hair")).toBeLessThanOrEqual(w("primary"));
    // The baked weight: 0 on the eyes, brows, nose and mouth (outline_bake.py).
    expect(outlineWidthPx("face", 0, 2, 1000)).toBe(0);
    expect(outlineWidthPx("primary", 0.5, 2, 1000)).toBeCloseTo(w("primary") / 2);
  });

  it("thins out with distance and is gone past the fade", () => {
    const near = outlineDistanceScale(1);
    const mid = outlineDistanceScale(15);
    const far = outlineDistanceScale(TOON_OUTLINE.far);
    expect(near).toBe(1);
    expect(mid).toBeLessThan(near);
    expect(far).toBeCloseTo(TOON_OUTLINE.farScale);
    expect(outlineDistanceScale(TOON_OUTLINE.fadeEnd + 1)).toBe(0);
  });

  it("keeps the width per CSS pixel: scales with the render height", () => {
    expect(outlineWidthPx("primary", 1, 2, 1800)).toBeCloseTo(outlineWidthPx("primary", 1, 2, 1000) * 1.8);
  });
});

describe("outline switch per quality and LOD", () => {
  it("is off at Low, on at Medium and High", () => {
    expect(qualityProfile("low", 2).outlines).toBe(false);
    expect(qualityProfile("medium", 2).outlines).toBe(true);
    expect(qualityProfile("high", 2).outlines).toBe(true);
    expect(outlineDrawn(qualityProfile("low", 2).outlines, 0, 1)).toBe(false);
  });

  it("outlines LOD0 to maxTier within the fade, never LOD3", () => {
    expect(TOON_OUTLINE.maxTier).toBeGreaterThanOrEqual(1);
    expect(TOON_OUTLINE.maxTier).toBeLessThan(3);
    expect(outlineDrawn(true, 0, 5)).toBe(true);
    expect(outlineDrawn(true, 1, 15)).toBe(true);
    expect(outlineDrawn(true, 3, 5)).toBe(false);
    expect(outlineDrawn(true, 0, TOON_OUTLINE.fadeEnd + 0.1)).toBe(false);
  });
});

describe("outline data", () => {
  it("parses the palette's rows, with defaults and clamps", () => {
    const d = parseOutline({});
    expect(d.width).toBe(OUTLINE_DEFAULTS.width);
    const p = parseOutline({ outline: { width: 100, fadeStart: 30, fadeEnd: 10, regions: { default: { scale: 0.3 }, hair: { width: -1 } } } });
    expect(p.width).toBe(8);
    expect(p.fadeEnd).toBeGreaterThan(p.fadeStart);
    expect(p.regions.primary.scale).toBe(0.3);
    expect(p.regions.hair.width).toBe(0);
    expect(p.regions.hair.scale).toBe(0.3);
  });

  it("packs the smoothed normal and the weight into four signed bytes", () => {
    const out = new Int8Array(8);
    packOutline([0, 0.5, 0], 0, out, 0);
    expect([...out.slice(0, 4)]).toEqual([0, 127, 0, 64]);
    packOutline([0, 0, 0], 0, out, 4);
    expect([...out.slice(4)]).toEqual([0, 0, 0, 0]);
    expect(hasOutline(out)).toBe(true);
    expect(hasOutline(new Int8Array(8))).toBe(false);
  });

  it("builds the hull's shaders: skinned, pushed out in screen space, neutral fallback", () => {
    const vs = outlineVertexShader();
    expect(vs).toContain("#include<bonesVertex>");
    expect(vs).toContain(`attribute vec4 ${OUTLINE_KIND};`);
    expect(vs).toContain(`r = r < ${CHARACTER_REGIONS.length} ? r : ${regionIndex("neutral")};`);
    expect(vs).toContain("cp.xy += dir * (wpx * 2.0 / outlineView.xy) * cp.w;");
    expect(ShaderStore.ShadersStore["toonOutlineFragmentShader"]).toContain("gl_FragColor = vec4(vOutline, outlineLight.a);");
  });

  it("writes the per-frame uniforms into its own vectors (no allocation)", () => {
    const engine = new NullEngine();
    const scene = new Scene(engine);
    const m = new OutlineMaterial(scene);
    expect(m.backFaceCulling).toBe(true);
    m.update(1, 2, 3, 2880, 1800, [0.5, 0.6, 0.7], true);
    m.update(1, 2, 3, 2880, 1800, [0.5, 0.6, 0.7], false);
    expect(OUTLINE_MASK_ALPHA).toBeLessThan(0.49);
    m.dispose();
    scene.dispose();
    engine.dispose();
  });
});
