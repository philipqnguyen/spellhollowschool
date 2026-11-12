// KTX2 for the castle texture library (castle kit M4, docs/art/castle-kit.md
// "Textures"): the self-hosted decoder's URLs (every one same-origin, no CDN), the
// files prepare.mjs serves, the decoder options, `?ktx2=off` and the GPU
// check, the KTX2 fields of textures.json, which file each slot loads at each
// quality, the PNG fallback (per texture, and for the whole library when the
// decoder never works), and GPU sizes in the real formats.

import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import type { Texture } from "@babylonjs/core/Materials/Textures/texture";
import { KhronosTextureContainer2 } from "@babylonjs/core/Misc/khronosTextureContainer2";
import { SourceTextureFormat, TranscodeTarget } from "@babylonjs/core/Materials/Textures/ktx2decoderTypes";
import { TranscodeDecisionTree } from "@babylonjs/ktx2decoder/transcodeDecisionTree.js";
import { CastleKtx2DecoderOptions, configureKtx2, gpuFormatName, gpuTextureBytes, KTX2_DIR, KTX2_FILES, ktx2Urls, ktx2Usable, ktx2Wanted } from "./ktx2";
import { CastleLibrary, chooseTexture, KTX2_TIMEOUT_MS, parseTexturesJson } from "./materials";

/** textures.json as the KTX2 build writes it (castle_textures.py `ktx2_entries`). */
const TEXTURES = {
  version: 1,
  materials: {
    castle_wall_a: { base: "stone_ashlar_a.png", lo: "stone_ashlar_a_lo.png", px: [1024, 1024], ktx2: "stone_ashlar_a.ktx2", ktx2_lo: "stone_ashlar_a_lo.ktx2", ktx2_mode: "etc1s" },
    castle_glass: {
      base: "glass_sheet.png",
      lo: "glass_sheet_lo.png",
      px: [1024, 1024],
      emissive: "glass_sheet.png",
      emissive_lo: "glass_sheet_lo.png",
      tint: "sky",
      ktx2: "glass_sheet.ktx2",
      ktx2_lo: "glass_sheet_lo.ktx2",
      ktx2_mode: "uastc",
    },
    castle_props: {
      base: "props_atlas.png",
      lo: "props_atlas_lo.png",
      px: [1024, 1024],
      emissive: "props_atlas_e.png",
      emissive_lo: "props_atlas_e_lo.png",
      emissive_px: [512, 512],
      ktx2: "props_atlas.ktx2",
      ktx2_lo: "props_atlas_lo.ktx2",
      emissive_ktx2: "props_atlas_e.ktx2",
      emissive_ktx2_lo: "props_atlas_e_lo.ktx2",
      ktx2_mode: "uastc",
    },
    castle_trim: { base: "trim_castle.png", lo: "trim_castle_lo.png", px: [2048, 2048], medium: "lo", ktx2: "trim_castle.ktx2", ktx2_lo: "trim_castle_lo.ktx2", ktx2_mode: "uastc" },
    castle_wood: { base: "wood_sheet.png", lo: "wood_sheet_lo.png", px: [1024, 1024] },
  },
};

let engine: NullEngine;
let scene: Scene;
beforeEach(() => {
  engine = new NullEngine();
  scene = new Scene(engine);
});
afterEach(() => {
  scene.dispose();
  engine.dispose();
  vi.restoreAllMocks();
});
const tick = () => new Promise((r) => setTimeout(r, 0));
const url = (t: unknown) => (t as Texture | null)?.url;

describe("self-hosted KTX2 decoder", () => {
  it("points every URLConfig entry at the game's own origin, never a CDN", () => {
    const keys = Object.keys(KhronosTextureContainer2.URLConfig).sort();
    expect(Object.keys(KTX2_FILES).sort()).toEqual(keys);
    for (const [base, page] of [
      ["/", "http://localhost:5273/?probe"],
      ["/game/", "https://example.org/game/index.html"],
    ]) {
      const urls = ktx2Urls(base, page);
      for (const k of keys) {
        const u = new URL(urls[k as keyof typeof urls]);
        expect(u.origin).toBe(new URL(page).origin);
        expect(u.pathname.startsWith(`${base}${KTX2_DIR}`)).toBe(true);
      }
    }
    configureKtx2("/", "http://localhost:5273/");
    for (const [k, v] of Object.entries(KhronosTextureContainer2.URLConfig)) {
      expect(v, k).not.toBeNull();
      expect(new URL(v as string).origin, k).toBe("http://localhost:5273");
      expect(v as string).not.toMatch(/babylonjs\.com|cdn|unpkg|jsdelivr/);
    }
    expect(KhronosTextureContainer2.DefaultDecoderOptions).toBeInstanceOf(CastleKtx2DecoderOptions);
  });

  it("serves exactly the files prepare.mjs copies and builds", () => {
    const src = readFileSync(new URL("../../../scripts/prepare.mjs", import.meta.url), "utf8");
    const decoder = /const KTX2_DECODER = "([^"]+)"/.exec(src)?.[1];
    const wasm = /const KTX2_WASM = \[([^\]]+)\]/.exec(src)?.[1].match(/"[^"]+"/g)?.map((s) => s.slice(1, -1));
    expect(decoder).toBe(KTX2_FILES.jsDecoderModule);
    expect([decoder, ...(wasm ?? [])].sort()).toEqual(Object.values(KTX2_FILES).sort());
  });

  it("prefers BC1 for ETC1S on desktop GPUs, keeps ETC2 on Apple and Babylon's UASTC choices", () => {
    const opts = new CastleKtx2DecoderOptions()._getKTX2DecoderOptions();
    const pick = (src: SourceTextureFormat, alpha: boolean, caps: Record<string, boolean>) => {
      const t = new TranscodeDecisionTree(src, alpha, true, caps, opts);
      t.parseTree(opts.transcodeFormatDecisionTree!);
      return t.transcodeFormat;
    };
    const desktop = { bptc: true, s3tc: true };
    const apple = { astc: true, etc2: true, etc1: true, bptc: true, s3tc: true };
    expect(pick(SourceTextureFormat.ETC1S, false, desktop)).toBe(TranscodeTarget.BC1_RGB);
    expect(pick(SourceTextureFormat.ETC1S, true, desktop)).toBe(TranscodeTarget.BC7_RGBA);
    expect(pick(SourceTextureFormat.ETC1S, false, apple)).toBe(TranscodeTarget.ETC1_RGB);
    expect(pick(SourceTextureFormat.UASTC4x4, false, desktop)).toBe(TranscodeTarget.BC7_RGBA);
    expect(pick(SourceTextureFormat.UASTC4x4, false, apple)).toBe(TranscodeTarget.ASTC_4X4_RGBA);
    expect(pick(SourceTextureFormat.UASTC4x4, false, { s3tc: true })).toBe(TranscodeTarget.RGBA32); // Babylon's default kept
    // Cached: the decoder asks per texture.
    const o = new CastleKtx2DecoderOptions();
    expect(o._getKTX2DecoderOptions()).toBe(o._getKTX2DecoderOptions());
  });

  it("is off with ?ktx2=off and on GPUs without a format it transcodes to", () => {
    expect(ktx2Wanted("")).toBe(true);
    expect(ktx2Wanted("?probe&ktx2=on")).toBe(true);
    expect(ktx2Wanted("?ktx2=off")).toBe(false);
    expect(ktx2Wanted("?probe&ktx2=OFF")).toBe(false);
    expect(ktx2Usable({ astc: {}, bptc: null, s3tc: undefined, etc2: null } as never)).toBe(true);
    expect(ktx2Usable({ astc: null, bptc: null, s3tc: {}, etc2: null } as never)).toBe(true);
    expect(ktx2Usable({ astc: null, bptc: null, s3tc: undefined, etc2: null } as never)).toBe(false);
    expect(ktx2Usable(null)).toBe(false);
    // The NullEngine has none: the library loads PNGs unless told otherwise.
    const lib = new CastleLibrary(scene, "tex/", "medium");
    expect(lib.ktx2).toBe(false);
  });
});

describe("GPU sizes", () => {
  it("count the real format, mips by whole blocks", () => {
    const rgba = gpuTextureBytes(1024, 1024, 5, 0, true);
    expect(rgba).toBeCloseTo((1024 * 1024 * 4 * 4) / 3, -2);
    expect(gpuTextureBytes(1024, 1024, 5, 0, false)).toBe(1024 * 1024 * 4);
    expect(gpuTextureBytes(1024, 1024, 37808, 0, false)).toBe(1024 * 1024); // ASTC 4x4: 1 B/px
    expect(gpuTextureBytes(1024, 1024, 37492, 0, false)).toBe(1024 * 512); // ETC2 RGB: 0.5 B/px
    expect(gpuTextureBytes(1024, 1024, 33776, 0, false)).toBe(1024 * 512); // BC1
    expect(gpuTextureBytes(1024, 1024, 36492, 0, false)).toBe(1024 * 1024); // BC7
    // Mips: levels below 4 px still take a whole block (1024 -> 1: 11 levels).
    let blocks = 0;
    for (let s = 1024; s >= 1; s >>= 1) blocks += Math.ceil(s / 4) ** 2;
    expect(gpuTextureBytes(1024, 1024, 37808, 0, true)).toBe(blocks * 16);
    expect(gpuTextureBytes(1024, 1024, 37808, 0, true) / rgba).toBeCloseTo(0.25, 2);
    expect(gpuFormatName(37808)).toBe("ASTC 4x4");
    expect(gpuFormatName(37492)).toBe("ETC2 RGB");
    expect(gpuFormatName(5, 0)).toBe("RGBA8");
  });
});

describe("textures.json KTX2 fields", () => {
  it("parse, with the emissive's own copy or the base's again (glass)", () => {
    const specs = parseTexturesJson(TEXTURES);
    expect(specs.get("castle_wall_a")).toMatchObject({ ktx2: "stone_ashlar_a.ktx2", ktx2Lo: "stone_ashlar_a_lo.ktx2", ktx2Mode: "etc1s", emissiveKtx2: null });
    expect(specs.get("castle_glass")).toMatchObject({ emissiveKtx2: "glass_sheet.ktx2", emissiveKtx2Lo: "glass_sheet_lo.ktx2", ktx2Mode: "uastc" });
    expect(specs.get("castle_props")).toMatchObject({ emissiveKtx2: "props_atlas_e.ktx2", emissiveKtx2Lo: "props_atlas_e_lo.ktx2" });
    expect(specs.get("castle_wood")).toMatchObject({ ktx2: null, ktx2Lo: null, ktx2Mode: null });
  });

  it("keep the PNGs when the KTX2 fields are malformed", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const bad = (extra: object) => parseTexturesJson({ version: 1, materials: { castle_x: { base: "a.png", lo: "a_lo.png", px: [64, 64], ...extra } } }).get("castle_x");
    expect(bad({ ktx2: "a.ktx2", ktx2_lo: "a_lo.ktx2", ktx2_mode: "astc" })?.ktx2).toBeNull();
    expect(bad({ ktx2: "../a.ktx2", ktx2_lo: "a_lo.ktx2", ktx2_mode: "uastc" })?.ktx2).toBeNull();
    expect(bad({ ktx2: "https://cdn.example/a.ktx2", ktx2_lo: "a_lo.ktx2", ktx2_mode: "uastc" })?.ktx2).toBeNull();
    expect(bad({ ktx2: "a.png", ktx2_lo: "a_lo.ktx2", ktx2_mode: "uastc" })?.ktx2).toBeNull();
    expect(bad({ ktx2: "a.ktx2", ktx2_mode: "uastc" })?.ktx2).toBeNull();
    expect(bad({ ktx2: "a.ktx2", ktx2_lo: "a_lo.ktx2", ktx2_mode: "uastc" })?.base).toBe("a.png");
    expect(warn).toHaveBeenCalledTimes(5);
  });

  it("choose the file each slot loads", () => {
    const specs = parseTexturesJson(TEXTURES);
    const props = specs.get("castle_props")!;
    expect(chooseTexture(props, "diffuse", false, true)).toEqual({ file: "props_atlas.ktx2", png: "props_atlas.png", ktx2: true });
    expect(chooseTexture(props, "diffuse", true, true)).toEqual({ file: "props_atlas_lo.ktx2", png: "props_atlas_lo.png", ktx2: true });
    expect(chooseTexture(props, "emissive", true, true)).toEqual({ file: "props_atlas_e_lo.ktx2", png: "props_atlas_e_lo.png", ktx2: true });
    expect(chooseTexture(props, "diffuse", false, false)).toEqual({ file: "props_atlas.png", png: "props_atlas.png", ktx2: false });
    expect(chooseTexture(specs.get("castle_wall_a")!, "emissive", false, true)).toBeNull();
    expect(chooseTexture(specs.get("castle_wood")!, "diffuse", false, true)).toEqual({ file: "wood_sheet.png", png: "wood_sheet.png", ktx2: false });
  });
});

describe("the library with KTX2", () => {
  it("loads the KTX2 copies at every quality, the half-size ones where it used the _lo PNGs", async () => {
    const lib = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0, ktx2: true });
    lib.useSpecs(parseTexturesJson(TEXTURES));
    const wall = lib.take("castle_wall_a")!;
    const trim = lib.take("castle_trim")!;
    const props = lib.take("castle_props")!;
    const glass = lib.take("castle_glass")!;
    const wood = lib.take("castle_wood")!;
    await tick();
    expect(url(wall.diffuseTexture)).toBe("/tex/stone_ashlar_a.ktx2");
    expect(url(trim.diffuseTexture)).toBe("/tex/trim_castle_lo.ktx2"); // medium: lo
    expect(url(props.diffuseTexture)).toBe("/tex/props_atlas.ktx2");
    expect(url(props.emissiveTexture)).toBe("/tex/props_atlas_e.ktx2");
    expect(url(glass.emissiveTexture)).toBe("/tex/glass_sheet.ktx2");
    expect(glass.emissiveTexture!.getInternalTexture()).toBe(glass.diffuseTexture!.getInternalTexture()); // one GPU copy
    expect(url(wood.diffuseTexture)).toBe("/tex/wood_sheet.png"); // no KTX2 copy listed
    expect((wall.diffuseTexture as Texture).invertY).toBe(false);
    lib.setQuality("low");
    await tick();
    expect(url(wall.diffuseTexture)).toBe("/tex/stone_ashlar_a_lo.ktx2");
    expect(url(props.emissiveTexture)).toBe("/tex/props_atlas_e_lo.ktx2");
    lib.setQuality("high");
    await tick();
    expect(url(trim.diffuseTexture)).toBe("/tex/trim_castle.ktx2");
    expect(lib.formatSummary()).toMatch(/^KTX2: RGBA8 x\d+$/); // the NullEngine stores everything as RGBA8
    lib.dispose();
  });

  it("loads PNGs with ?ktx2=off (ktx2: false)", async () => {
    const lib = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0, ktx2: false });
    lib.useSpecs(parseTexturesJson(TEXTURES));
    const wall = lib.take("castle_wall_a")!;
    await tick();
    expect(url(wall.diffuseTexture)).toBe("/tex/stone_ashlar_a.png");
    expect(lib.formatSummary()).toMatch(/^PNG/);
    lib.dispose();
  });

  /** Makes the NullEngine fail the loads whose URL `fails` matches. */
  function failLoads(fails: (u: string) => boolean, hang = false): void {
    const orig = engine.createTexture.bind(engine);
    engine.createTexture = ((u: string, noMip: boolean, invertY: boolean, sc: Scene, sm: number, onLoad: unknown, onError: ((m?: string) => void) | null, ...rest: unknown[]) => {
      if (fails(String(u))) {
        const t = (orig as (...a: unknown[]) => ReturnType<typeof orig>)(u, noMip, invertY, sc, sm, null, null, ...rest);
        if (!hang) setTimeout(() => onError?.("decode failed"));
        return t;
      }
      return (orig as (...a: unknown[]) => ReturnType<typeof orig>)(u, noMip, invertY, sc, sm, onLoad, onError, ...rest);
    }) as typeof engine.createTexture;
  }

  it("falls back to a texture's PNG when its KTX2 fails, one warning per material", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    failLoads((u) => u.endsWith("props_atlas.ktx2"));
    const lib = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0, ktx2: true });
    lib.useSpecs(parseTexturesJson(TEXTURES));
    const wall = lib.take("castle_wall_a")!;
    await tick();
    expect(url(wall.diffuseTexture)).toBe("/tex/stone_ashlar_a.ktx2"); // the decoder works
    const props = lib.take("castle_props")!;
    await tick();
    await tick();
    expect(url(props.diffuseTexture)).toBe("/tex/props_atlas.png");
    expect(url(props.emissiveTexture)).toBe("/tex/props_atlas_e.ktx2");
    expect(lib.ktx2).toBe(true);
    lib.setQuality("high");
    lib.setQuality("medium"); // reloads: fails again, no second warning
    await tick();
    await tick();
    expect(url(props.diffuseTexture)).toBe("/tex/props_atlas.png");
    expect(warn.mock.calls.filter((c) => String(c[0]).includes("castle_props")).length).toBe(1);
    lib.dispose();
  });

  it("takes a KTX2 that never arrives as failed (workers that cannot start never answer)", async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    failLoads((u) => u.endsWith(".ktx2"), true);
    const lib = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0, ktx2: true });
    lib.useSpecs(parseTexturesJson(TEXTURES));
    const wall = lib.take("castle_wall_a")!;
    await vi.advanceTimersByTimeAsync(KTX2_TIMEOUT_MS - 100);
    expect(wall.diffuseTexture).toBeNull(); // still waiting: the placeholder colour
    await vi.advanceTimersByTimeAsync(200);
    expect(lib.ktx2).toBe(false);
    expect(url(wall.diffuseTexture)).toBe("/tex/stone_ashlar_a.png");
    expect(warn).toHaveBeenCalledTimes(1);
    lib.dispose();
    vi.useRealTimers();
  });

  it("loads PNGs for the whole library when the decoder never works", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    failLoads((u) => u.endsWith(".ktx2"));
    const lib = new CastleLibrary(scene, "tex/", "medium", { graceMs: 0, ktx2: true });
    lib.useSpecs(parseTexturesJson(TEXTURES));
    const wall = lib.take("castle_wall_a")!;
    const props = lib.take("castle_props")!;
    await tick();
    await tick();
    expect(lib.ktx2).toBe(false);
    expect(url(wall.diffuseTexture)).toBe("/tex/stone_ashlar_a.png");
    expect(url(props.diffuseTexture)).toBe("/tex/props_atlas.png");
    expect(url(props.emissiveTexture)).toBe("/tex/props_atlas_e.png");
    // Later materials go straight to their PNGs.
    const trim = lib.take("castle_trim")!;
    await tick();
    expect(url(trim.diffuseTexture)).toBe("/tex/trim_castle_lo.png");
    expect(warn.mock.calls.filter((c) => String(c[0]).includes("KTX2 decoder")).length).toBe(1);
    lib.dispose();
  });
});
