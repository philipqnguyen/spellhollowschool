// Vertex colours of an assembled character: skin tone, hair colour and the
// three dye channels of each gear item, from palette and creator indices.
//
// The art pipeline's contract (tools/blender/characters, the shared atlas):
// every vertex has a tint mask in COLOR_0 and the final colour is
//
//   albedo = atlas texel · base · ((1 − r − g − b) + r·C0 + g·C1 + b·C2)
//
// where (C0, C1, C2) is (primary, secondary, trim dye) on gear, (skin tone,
// hair colour, eye colour) on the body (hair colour for the brows; the eye
// colour tints the greyscale-painted iris, docs/art/character-adoption.md)
// and (hair colour, –, –) on hair. Unmasked areas (a boot sole, a collar) keep the neutral
// texel. The bracket and the base factor are baked into the vertex colour
// here; the shared material multiplies in the atlas texel.
//
// Colours are sRGB 0..1, as the palette and the atlas are authored and as
// the character material uses them. Pure, unit tested.

import { hexToRgb01, paletteHex, PALETTE, type Dyes, type Palette } from "../../content/catalog";
import type { Finish } from "../../wasm/sim_wasm";
import { finishOfToonAlpha, regionIndex, toonAlpha } from "../toon/palette";

export type Rgb = [number, number, number];
export type Channels = readonly [Rgb, Rgb, Rgb];

const WHITE: Rgb = [1, 1, 1];

/** The three dye colours of an item from palette indices. */
export function dyeColours(dyes: Dyes, palette: Palette = PALETTE): [Rgb, Rgb, Rgb] {
  return [hexToRgb01(paletteHex(dyes[0], palette)), hexToRgb01(paletteHex(dyes[1], palette)), hexToRgb01(paletteHex(dyes[2], palette))];
}

/** Body channels: skin tone, hair colour (brows), eye colour (iris; white when not given). */
export function bodyChannels(skin: Rgb, hair: Rgb, eye: Rgb = WHITE): Channels {
  return [skin, hair, eye];
}

/** Hair channels: the hair colour. */
export function hairChannels(hair: Rgb): Channels {
  return [hair, WHITE, WHITE];
}

/**
 * The colour of one vertex: `mask` weights at `mo` (null = all channel R)
 * over `base`, into `out` at `o`. Weights summing above 1 are normalised.
 */
export function tintVertex(base: readonly number[], mask: ArrayLike<number> | null, mo: number, channels: Channels, out: Float32Array | number[], o: number): void {
  let r = mask ? Math.max(0, mask[mo]!) : 1;
  let g = mask ? Math.max(0, mask[mo + 1]!) : 0;
  let b = mask ? Math.max(0, mask[mo + 2]!) : 0;
  const sum = r + g + b;
  if (sum > 1) {
    r /= sum;
    g /= sum;
    b /= sum;
  }
  const rest = 1 - Math.min(1, sum);
  const [c0, c1, c2] = channels;
  for (let c = 0; c < 3; c++) out[o + c] = base[c]! * (rest + r * c0[c]! + g * c1[c]! + b * c2[c]!);
}

/**
 * The weight `tintVertex` gives channel `channel` (0-2) of one vertex's mask
 * (normalised the same way): times the base colour, how much that channel's
 * colour adds to the vertex colour. A Chameleon Tonic recolours one channel
 * live with it (assemble.ts `track`).
 */
export function channelWeight(mask: ArrayLike<number> | null, mo: number, channel: number): number {
  const r = mask ? Math.max(0, mask[mo]!) : 1;
  const g = mask ? Math.max(0, mask[mo + 1]!) : 0;
  const b = mask ? Math.max(0, mask[mo + 2]!) : 0;
  const sum = r + g + b;
  const w = channel === 0 ? r : channel === 1 ? g : b;
  return sum > 1 ? w / sum : w;
}

/**
 * The vertex-colour alpha byte of a character vertex: its toon region and
 * its garment's finish (`region · 16 + finish · 4`, toon/palette.ts
 * `toonAlpha`). The character material never blends by vertex alpha, so the
 * byte is free; the toon plugin reads the region (toon/material.ts) and the
 * finish plugin the finish (finish.ts).
 */
export function finishAlpha(finish: Finish | undefined, region = regionIndex("neutral")): number {
  return toonAlpha(region, finish);
}

/** The finish an alpha byte marks. */
export function finishOfAlpha(alpha: number): Finish {
  return finishOfToonAlpha(alpha);
}

/** Packs 0..1 floats into normalised bytes (RGBA, alpha 255). */
export function packColour(rgb: ArrayLike<number>, i: number, out: Uint8Array, o: number): void {
  out[o] = Math.round(Math.max(0, Math.min(1, rgb[i]!)) * 255);
  out[o + 1] = Math.round(Math.max(0, Math.min(1, rgb[i + 1]!)) * 255);
  out[o + 2] = Math.round(Math.max(0, Math.min(1, rgb[i + 2]!)) * 255);
  out[o + 3] = 255;
}
