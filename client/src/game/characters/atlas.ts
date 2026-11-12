// The character atlas: the one small texture every character file shares
// (neutral swatches and the painted eyes, tools/blender/characters). All
// characters draw with one material that samples it; primitives without a
// texture of their own (greybox parts) point at its whitest texel, which
// the texture multiplies by one, so they share the material too.

import type { AtlasImage } from "./parts";

/** A content key for an atlas: each file embeds its own copy of the same image. */
export function atlasKey(img: AtlasImage): string {
  let h = 0x811c9dc5;
  const b = img.bytes;
  for (let i = 0; i < b.length; i++) {
    h ^= b[i]!;
    h = Math.imul(h, 0x01000193);
  }
  return `${img.mimeType}:${b.length}:${(h >>> 0).toString(16)}`;
}

/**
 * Centre of the whitest texel of an RGBA image (rows top first, as decoded)
 * in glTF texture coordinates (origin at the top left). Pure, unit tested.
 */
export function whiteTexel(rgba: ArrayLike<number>, width: number, height: number): [number, number] {
  let best = -1;
  let at = 0;
  for (let i = 0, p = 0; i < width * height; i++, p += 4) {
    const v = Math.min(rgba[p]!, rgba[p + 1]!, rgba[p + 2]!);
    if (v > best) {
      best = v;
      at = i;
      if (v === 255) break;
    }
  }
  const x = at % width;
  const y = Math.floor(at / width);
  return [(x + 0.5) / width, (y + 0.5) / height];
}

export interface DecodedAtlas {
  bitmap: ImageBitmap;
  whiteUv: [number, number];
}

/** Decodes an atlas in the browser and finds its white texel. */
export async function decodeAtlas(img: AtlasImage): Promise<DecodedAtlas> {
  const bitmap = await createImageBitmap(new Blob([img.bytes as Uint8Array<ArrayBuffer>], { type: img.mimeType }));
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no 2D canvas to read the character atlas");
  ctx.drawImage(bitmap, 0, 0);
  const data = ctx.getImageData(0, 0, bitmap.width, bitmap.height).data;
  return { bitmap, whiteUv: whiteTexel(data, bitmap.width, bitmap.height) };
}
