// The castle depth pass (castle D1, docs/art/castle-kit.md "Shading: the AO bake") as the
// F3 overlay reports it: the SSAO pass (High only, toon/post.ts) and the
// baked shade layer. Kit meshes carry one grey occlusion
// value x in COLOR_0 (r = g = b, a multiplier, never a colour); a world
// material reads it as shade instead of an albedo multiply when its toon row
// asks for it (the row has shadeShift > 0, aoFloor < 1 or valueFloor < 1:
// the renderer's `TOON_WORLD_SHADE`, toon/material.ts). Pure, unit tested.

import type { ToonRamp } from "../toon/palette";

/** The row fields that put a world material on the shade path (valueFloor is D1's; absent reads as 1). */
export type ShadeFields = Pick<ToonRamp, "shadeShift" | "aoFloor"> & { valueFloor?: number };

/** Whether a world row reads the baked shade (shadeShift > 0, aoFloor < 1 or valueFloor < 1). */
export function readsShade(r: ShadeFields): boolean {
  return r.shadeShift > 0 || r.aoFloor < 1 || (r.valueFloor ?? 1) < 1;
}

/**
 * The F3 line's part for the castle's shade: how many of `names` (the shared
 * castle materials resident now) read the baked shade, e.g. "shade 9/11".
 * `rampOf` resolves a material's row (toon/palette.ts `worldRamp`).
 */
export function shadeSummary(names: Iterable<string>, rampOf: (name: string) => ShadeFields): string {
  let on = 0;
  let all = 0;
  for (const n of names) {
    all++;
    if (readsShade(rampOf(n))) on++;
  }
  return `shade ${on}/${all}`;
}

/**
 * The F3 toon line's SSAO state (castle D1, 19.6: High only): "on" or "off"
 * from the quality profile's `ssao`, "none" for a build without it.
 */
export function ssaoState(profile: object): "on" | "off" | "none" {
  const ssao = (profile as { ssao?: unknown }).ssao;
  return typeof ssao !== "boolean" ? "none" : ssao ? "on" : "off";
}
