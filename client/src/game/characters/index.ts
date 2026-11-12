// Player characters: assembled from the art pipeline's body, hair and gear
// files (or their greybox stand-ins), one skinned mesh and one draw call each.
//
//   recipe.ts    appearance → part files, dyes, tints, shape keys, height
//   library.ts   fetches and caches parts and rigs (shared across engines)
//   gltf.ts      .glb reader          parts.ts   glTF → part vertex data
//   greybox.ts   stand-in parts       assemble.ts  parts → one mesh
//   dye.ts       palette → vertex colours
//   clips.ts     rigs, shared clip data, posing   animator.ts  crossfades
//   renderer.ts  Babylon meshes, skeletons, LOD tiers, caching
//   lod.ts       tier selection and the triangle budget
//   sway.ts      hair sway bones on springs
//   blobs.ts     blob shadows (one draw for all)
//   preview.ts   a single character in its own canvas (creator, mirror)

import { PartLibrary } from "./library";

export { CharacterRenderer, LOD_DISTANCE, type CharacterFx, type CharacterShadowHost, type CharacterStats, type CharacterStyle, type TintPart } from "./renderer";
export { PartLibrary } from "./library";

/** sim-core's default walk and run speeds (m/s); the strides of the walk and run clips are tuned to them. */
export const DEFAULT_SPEEDS = { walk: 3.0, run: 6.5 } as const;

/** The logical folder of the game files (src/assets.ts). */
const ASSETS = "assets/";
let shared: PartLibrary | null = null;

/**
 * The page's part library. The first caller fixes the reference speeds (the
 * game passes sim-core's `moveParams`); later callers get the same instance.
 */
export function sharedLibrary(speeds: { walk: number; run: number } = DEFAULT_SPEEDS): PartLibrary {
  shared ??= new PartLibrary(ASSETS, speeds);
  return shared;
}
