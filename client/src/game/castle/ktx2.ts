// Self-hosted KTX2 decoding for the castle texture library (castle kit M4,
// docs/art/castle-kit.md "Textures").
//
// Babylon's KTX2 loader fetches its decoder script and transcoders from
// cdn.babylonjs.com by default. The game never does: `client/scripts/prepare.mjs`
// copies @babylonjs/ktx2decoder's transcoders and a worker-loadable build of
// the decoder into `public/assets/ktx2/`, and `configureKtx2` points every
// `KhronosTextureContainer2.URLConfig` entry there (same origin, absolute
// URLs: the decode workers run from a blob: URL, where a relative URL would
// not resolve) before the first KTX2 texture loads. Workers on, the engine's
// default count. The decoder stays on the game's origin even when the other
// assets come from a bucket (workers cannot start from another origin); each
// URL carries its build's content hash (`?v=`, src/assets.ts), so the server
// may cache it for a year.
//
// Also here: whether the KTX2 path is wanted and usable (`?ktx2=off` forces
// the PNGs; an engine without a compressed format the decoder can transcode
// to gains nothing from KTX2), the decoder options (ETC1S prefers BC1 to BC7
// on desktop GPUs: same quality from an ETC1S source, half the memory), and
// the real GPU size of a texture in its format (the F3 line and the perf
// probe).

import type { EngineCapabilities } from "@babylonjs/core/Engines/engineCapabilities";
import { DefaultKTX2DecoderOptions, KhronosTextureContainer2 } from "@babylonjs/core/Misc/khronosTextureContainer2";
import { TranscodeTarget, type IDecisionTree, type IKTX2DecoderOptions } from "@babylonjs/core/Materials/Textures/ktx2decoderTypes";
import { assetHash } from "../../assets";

/** Where prepare.mjs puts the decoder, under the app's base URL. */
export const KTX2_DIR = "assets/ktx2/";

/**
 * Every URLConfig entry and the file prepare.mjs serves for it (its
 * KTX2_DECODER and KTX2_WASM lists; a vitest checks they agree).
 */
export const KTX2_FILES = {
  jsDecoderModule: "babylon.ktx2Decoder.js",
  wasmUASTCToASTC: "uastc_astc.wasm",
  wasmUASTCToBC7: "uastc_bc7.wasm",
  wasmUASTCToRGBA_UNORM: "uastc_rgba8_unorm_v2.wasm",
  wasmUASTCToRGBA_SRGB: "uastc_rgba8_srgb_v2.wasm",
  wasmUASTCToR8_UNORM: "uastc_r8_unorm.wasm",
  wasmUASTCToRG8_UNORM: "uastc_rg8_unorm.wasm",
  jsMSCTranscoder: "msc_basis_transcoder.js",
  wasmMSCTranscoder: "msc_basis_transcoder.wasm",
  wasmZSTDDecoder: "zstddec.wasm",
} as const satisfies Record<keyof typeof KhronosTextureContainer2.URLConfig, string>;

/**
 * The URLConfig for a page at `pageUrl` whose app is served under `baseUrl`
 * (Vite's BASE_URL): absolute, same origin, with `?v=<hash>` where `hashOf`
 * (the asset map) knows the file.
 */
export function ktx2Urls(baseUrl: string, pageUrl: string, hashOf: (path: string) => string | undefined = assetHash): Record<keyof typeof KTX2_FILES, string> {
  const dir = new URL(`${baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`}${KTX2_DIR}`, pageUrl);
  const out = {} as Record<keyof typeof KTX2_FILES, string>;
  for (const [key, file] of Object.entries(KTX2_FILES) as [keyof typeof KTX2_FILES, string][]) {
    const hash = hashOf(`${KTX2_DIR}${file}`);
    out[key] = new URL(hash ? `${file}?v=${hash}` : file, dir).href;
  }
  return out;
}

/** ETC1S without alpha: BC1 (8 bytes a block) where the default tree picks BC7 (16) on GPUs without ETC2. */
const ETC1S_PREFER_BC1: IDecisionTree[string] = {
  transcodeFormat: TranscodeTarget.BC7_RGBA,
  yes: {
    cap: "s3tc",
    yes: {
      alpha: false,
      yes: { transcodeFormat: TranscodeTarget.BC1_RGB, engineFormat: 33776 /* COMPRESSED_RGB_S3TC_DXT1_EXT */ },
    },
  },
};

/** Babylon's default decoder options plus ETC1S_PREFER_BC1 (cached: the decoder asks for them per texture). */
export class CastleKtx2DecoderOptions extends DefaultKTX2DecoderOptions {
  private base: IKTX2DecoderOptions | null = null;
  private merged: IKTX2DecoderOptions | null = null;

  override _getKTX2DecoderOptions(): IKTX2DecoderOptions {
    const base = super._getKTX2DecoderOptions();
    if (base !== this.base || !this.merged) {
      this.base = base;
      this.merged = { ...base, transcodeFormatDecisionTree: { ...(base.transcodeFormatDecisionTree ?? {}), ETC1S: ETC1S_PREFER_BC1 } };
    }
    return this.merged;
  }
}

let configured = false;

/**
 * Points Babylon's KTX2 decoder at the self-hosted files and installs the
 * castle's decoder options. Once per page, before the first KTX2 load (the
 * library calls it); later calls do nothing.
 */
export function configureKtx2(baseUrl: string = import.meta.env.BASE_URL, pageUrl: string = globalThis.location?.href ?? "http://localhost/"): void {
  if (configured) return;
  configured = true;
  Object.assign(KhronosTextureContainer2.URLConfig, ktx2Urls(baseUrl, pageUrl));
  KhronosTextureContainer2.DefaultDecoderOptions = new CastleKtx2DecoderOptions();
}

/** `?ktx2=off` (any case) forces the PNG library. */
export function ktx2Wanted(search: string = globalThis.location?.search ?? ""): boolean {
  return (new URLSearchParams(search).get("ktx2") ?? "").toLowerCase() !== "off";
}

/** Whether the GPU has a compressed format the castle's KTX2 files transcode to (else they would decode to RGBA8: no gain). */
export function ktx2Usable(caps: Pick<EngineCapabilities, "astc" | "bptc" | "s3tc" | "etc2"> | null | undefined): boolean {
  return !!caps && !!(caps.astc || caps.bptc || caps.s3tc || caps.etc2);
}

// ------------------------------------------------------------ GPU sizes

/** Bytes per 4x4 block of the compressed formats the KTX2 decoder can produce (WebGL internal formats). */
const BLOCK_BYTES: Record<number, [bytes: number, name: string]> = {
  33776: [8, "BC1"], // COMPRESSED_RGB_S3TC_DXT1_EXT
  33777: [8, "BC1"], // COMPRESSED_RGBA_S3TC_DXT1_EXT
  33778: [16, "BC2"],
  33779: [16, "BC3"], // COMPRESSED_RGBA_S3TC_DXT5_EXT
  36492: [16, "BC7"], // COMPRESSED_RGBA_BPTC_UNORM_EXT
  36196: [8, "ETC1"], // COMPRESSED_RGB_ETC1_WEBGL
  37492: [8, "ETC2 RGB"], // COMPRESSED_RGB8_ETC2
  37494: [8, "ETC2 RGB A1"],
  37496: [16, "ETC2 RGBA"], // COMPRESSED_RGBA8_ETC2_EAC
  37808: [16, "ASTC 4x4"], // COMPRESSED_RGBA_ASTC_4x4_KHR
  35840: [8, "PVRTC RGB"],
  35842: [8, "PVRTC RGBA"],
};

/** Bytes per texel of Babylon's uncompressed formats (TEXTUREFORMAT_* x TEXTURETYPE_*); the GPU pads RGB to 4 bytes. */
function texelBytes(format: number, type: number): number {
  const channels = format === 6 ? 1 : format === 7 ? 2 : format === 0 || format === 1 ? 1 : format === 2 ? 2 : 4;
  const size = type === 1 ? 4 : type === 2 ? 2 : 1;
  return channels * size;
}

/** A texture's format name: the compressed format, or RGBA8 (and friends) for uncompressed ones. */
export function gpuFormatName(format: number, type = 0): string {
  const c = BLOCK_BYTES[format];
  if (c) return c[1];
  const ch = format === 6 ? "R" : format === 7 ? "RG" : "RGBA";
  return `${ch}${type === 1 ? "32F" : type === 2 ? "16F" : "8"}`;
}

/**
 * GPU bytes of a 2D texture in its real format: compressed formats by
 * whole 4x4 blocks per mip level (BC1 / ETC2 RGB 0.5 B/px, BC3 / BC7 / ASTC
 * 4x4 / ETC2 RGBA 1 B/px), uncompressed by texel size (RGBA8 4 B/px); with
 * `mips`, the full chain down to 1 x 1.
 */
export function gpuTextureBytes(width: number, height: number, format: number, type: number, mips: boolean): number {
  const c = BLOCK_BYTES[format];
  let w = Math.max(1, width);
  let h = Math.max(1, height);
  let total = 0;
  for (;;) {
    total += c ? Math.ceil(w / 4) * Math.ceil(h / 4) * c[0] : w * h * texelBytes(format, type);
    if (!mips || (w === 1 && h === 1)) return total;
    w = Math.max(1, w >> 1);
    h = Math.max(1, h >> 1);
  }
}
