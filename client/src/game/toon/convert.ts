// World, room and furniture art arrives as glTF, which Babylon loads as PBR
// materials. The toon look lights them with the toon model instead
// (toon/material.ts), on a StandardMaterial, which is also a much lighter
// shader than PBR: the converted world costs less per pixel than before,
// which pays for most of the outline pass.
//
// `toonifyContainer` swaps every PBR material of a freshly loaded container
// for a toon StandardMaterial with the same name, colours, textures, alpha
// mode and culling (glTF factors are linear, StandardMaterial works in sRGB
// like the characters, so colours are converted), and disposes the PBR
// material without its textures. The container keeps owning the new
// materials, so disposing a cell or room frees them as before. Run it before
// the container is added to the scene, so PBR shaders are never compiled.
//
// The castle kit hook (docs/art/castle-kit.md "The client side"): with `shared`,
// a PBR material whose name the shared library knows (the `castle_*`
// placeholders of kitted cells and of every cell's props kit) is replaced
// by the library's one shared material instead of a converted copy. The
// shared material is not added to the container, so disposing the cell
// leaves it alone (the library counts the cells using it). Names the
// library does not know convert as before.

import type { AssetContainer } from "@babylonjs/core/assetContainer";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Material } from "@babylonjs/core/Materials/material";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import type { BaseTexture } from "@babylonjs/core/Materials/Textures/baseTexture";
import type { Scene } from "@babylonjs/core/scene";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { Geometry } from "@babylonjs/core/Meshes/geometry";
import { ToonPlugin } from "./material";
import { worldRamp } from "./palette";

/**
 * Lights a toon world material takes: sky, sun, up to three torches, the
 * character shadow light (floors only, Medium and High: its layer reaches
 * nothing else), two lit wands.
 */
export const WORLD_MAX_LIGHTS = 8;

/** A library of materials shared across containers (castle/materials.ts). */
export interface SharedMaterials {
  /**
   * The shared material standing in for the glTF material `placeholder`
   * named `name`, with one more reference held, or null to convert it as
   * usual.
   */
  take(name: string, placeholder?: Material): Material | null;
}

/** The PBR material fields the conversion reads (PBRMaterial as the glTF loader fills it). */
interface PbrLike {
  name: string;
  albedoColor?: Color3;
  albedoTexture?: BaseTexture | null;
  emissiveColor?: Color3;
  emissiveTexture?: BaseTexture | null;
  emissiveIntensity?: number;
  unlit?: boolean;
  alpha: number;
  alphaCutOff: number;
  transparencyMode: number | null;
  backFaceCulling: boolean;
  twoSidedLighting?: boolean;
  sideOrientation?: number | null;
  useAlphaFromAlbedoTexture?: boolean;
  getClassName(): string;
  dispose(forceDisposeEffect?: boolean, forceDisposeTextures?: boolean): void;
}

function isPbr(m: Material): boolean {
  const c = m.getClassName();
  return c === "PBRMaterial" || c === "PBRMetallicRoughnessMaterial";
}

/**
 * A linear glTF emissive colour times its strength, in sRGB, keeping its hue
 * when the strength pushes a channel past 1 (StandardMaterial clips).
 */
export function emissiveSrgb(linear: readonly [number, number, number], strength: number): [number, number, number] {
  const s = Math.max(0, Number.isFinite(strength) ? strength : 1);
  let r = linear[0] * s;
  let g = linear[1] * s;
  let b = linear[2] * s;
  const peak = Math.max(r, g, b);
  if (peak > 1) {
    r /= peak;
    g /= peak;
    b /= peak;
  }
  const gamma = (v: number) => Math.pow(Math.max(0, v), 1 / 2.2);
  return [gamma(r), gamma(g), gamma(b)];
}

/**
 * The level of an emissive texture for a glTF emissive strength: a texture
 * keeps its texels; its strength only brightens (clipped later).
 */
export function emissiveTextureLevel(strength: number): number {
  const s = Number.isFinite(strength) ? strength : 1;
  return Math.min(1.6, Math.max(1, 1 + (s - 1) * 0.4));
}

/** The toon StandardMaterial standing in for `src`. */
export function toonMaterialFrom(src: Material, scene: Scene, maxLights = WORLD_MAX_LIGHTS): StandardMaterial {
  const p = src as unknown as PbrLike;
  const mat = new StandardMaterial(src.name, scene);
  const albedo = p.albedoColor ?? Color3.White();
  mat.diffuseColor = albedo.toGammaSpace();
  if (p.albedoTexture) mat.diffuseTexture = p.albedoTexture;
  mat.specularColor = Color3.Black();
  const e = p.emissiveColor ?? Color3.Black();
  const strength = p.emissiveIntensity ?? 1;
  if (e.r > 0 || e.g > 0 || e.b > 0 || p.emissiveTexture) {
    const [r, g, b] = emissiveSrgb([e.r, e.g, e.b], strength);
    mat.emissiveColor = new Color3(r, g, b);
    if (p.emissiveTexture) {
      mat.emissiveTexture = p.emissiveTexture;
      mat.emissiveTexture.level = emissiveTextureLevel(strength);
      mat.emissiveColor = Color3.Black();
    }
    // Emission adds to the lit colour (as glTF's does), not multiplied by the albedo.
    mat.useEmissiveAsIllumination = true;
  }
  if (p.unlit) {
    mat.disableLighting = true;
    mat.emissiveColor = mat.diffuseColor.clone();
  }
  mat.alpha = p.alpha;
  mat.alphaCutOff = p.alphaCutOff;
  mat.transparencyMode = p.transparencyMode;
  if (p.useAlphaFromAlbedoTexture) mat.useAlphaFromDiffuseTexture = true;
  if (mat.diffuseTexture && p.albedoTexture?.hasAlpha) mat.diffuseTexture.hasAlpha = true;
  mat.backFaceCulling = p.backFaceCulling;
  mat.twoSidedLighting = !!p.twoSidedLighting;
  mat.sideOrientation = p.sideOrientation ?? (scene.useRightHandedSystem ? Material.CounterClockWiseSideOrientation : Material.ClockWiseSideOrientation);
  mat.maxSimultaneousLights = maxLights;
  if (!p.unlit) new ToonPlugin(mat, worldRamp(src.name));
  return mat;
}

/**
 * Replaces the container's PBR materials with toon materials (see the top
 * of this file); with `shared`, library materials replace the ones it
 * knows. Returns how many were replaced.
 */
export function toonifyContainer(
  container: AssetContainer,
  scene: Scene,
  maxLights = WORLD_MAX_LIGHTS,
  opts: { vertexColours?: "value" | "mask"; shared?: SharedMaterials } = {},
): number {
  if ((opts.vertexColours ?? "value") === "value") srgbVertexColours(container);
  const swaps = new Map<Material, Material>();
  const shared = new Set<Material>();
  for (const m of container.materials) {
    if (!isPbr(m)) continue;
    const lib = opts.shared?.take(m.name, m) ?? null;
    if (lib) shared.add(lib);
    swaps.set(m, lib ?? toonMaterialFrom(m, scene, maxLights));
  }
  if (swaps.size === 0) return 0;
  for (const mesh of container.meshes) {
    const mat = mesh.material;
    const to = mat ? swaps.get(mat) : undefined;
    if (to) mesh.material = to;
  }
  // Shared materials stay out of the container: disposing the cell must not dispose them.
  container.materials = container.materials.map((m) => swaps.get(m) ?? m).filter((m) => !shared.has(m));
  for (const [from, to] of swaps) {
    // A new material lives in the container, like the one it replaces.
    if (!shared.has(to)) scene.removeMaterial(to);
    (from as unknown as PbrLike).dispose(false, false);
  }
  return swaps.size;
}

/**
 * Linear byte to sRGB byte, the toon materials' colour space: round(255 * (i / 255) ^ (1 / 2.2))
 * (docs/art/castle-kit.md "Shading: the AO bake").
 */
export const LINEAR_TO_SRGB_BYTE: Uint8Array = (() => {
  const t = new Uint8Array(256);
  for (let i = 0; i < 256; i++) t[i] = Math.round(255 * Math.pow(i / 255, 1 / 2.2));
  return t;
})();

/**
 * The colours of `vb` (any glTF colour layout: normalized bytes, normalized
 * shorts or floats; VEC3 or VEC4) as tightly packed sRGB RGBA bytes, alpha
 * 255. Bytes go through the table; the others are quantized first, as the
 * export packs them (round(clamp(v, 0, 1) * 255)).
 */
export function srgbColourBytes(vb: VertexBuffer, count: number): Uint8Array | null {
  const out = new Uint8Array(count * 4);
  const size = vb.getSize();
  const table = LINEAR_TO_SRGB_BYTE;
  const data = vb.getData();
  if (vb.type === VertexBuffer.UNSIGNED_BYTE && vb.normalized && data && !Array.isArray(data)) {
    const view = ArrayBuffer.isView(data) ? data : new Uint8Array(data as ArrayBuffer);
    const bytes = new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
    const stride = vb.byteStride;
    for (let i = 0; i < count; i++) {
      const at = vb.byteOffset + i * stride;
      if (at + size > bytes.length) return null;
      out[i * 4] = table[bytes[at]!]!;
      out[i * 4 + 1] = table[size > 1 ? bytes[at + 1]! : 0]!;
      out[i * 4 + 2] = table[size > 2 ? bytes[at + 2]! : 0]!;
      out[i * 4 + 3] = 255;
    }
    return out;
  }
  // Shorts or floats (instances, older files): read as floats once, per load.
  const f = vb.getFloatData(count, false);
  if (!f) return null;
  const q = (v: number) => table[Math.round(Math.min(1, Math.max(0, v)) * 255)]!;
  for (let i = 0; i < count; i++) {
    out[i * 4] = q(f[i * size]!);
    out[i * 4 + 1] = size > 1 ? q(f[i * size + 1]!) : 0;
    out[i * 4 + 2] = size > 2 ? q(f[i * size + 2]!) : 0;
    out[i * 4 + 3] = 255;
  }
  return out;
}

/**
 * World cells may carry `COLOR_0` as a plain multiplicative colour (the
 * painted value ramps of docs/art/style-guide.md section 2): linear, as glTF
 * defines it. The toon materials work in sRGB, so the values are converted
 * once at load; vertex alpha is ignored (it would make the meshes blend).
 * Castle kit M2 (docs/art/castle-kit.md "Shading: the AO bake"): the export writes them as
 * normalized RGBA bytes, and they stay 4 bytes a vertex on the GPU: the
 * bytes go through a 256-entry table into a new normalized byte buffer (the
 * old float path built a 16-byte float buffer per vertex). Furniture keeps
 * its `COLOR_0` untouched: there it is a dye mask (`"mask"`).
 */
function srgbVertexColours(container: AssetContainer): void {
  const swapped = new Map<VertexBuffer, VertexBuffer | null>();
  const geometries = new Set<Geometry>();
  for (const m of container.meshes) {
    const mesh = m as Mesh;
    const geo = mesh.geometry;
    if (!geo || geometries.has(geo)) continue;
    const vb = geo.getVertexBuffer(VertexBuffer.ColorKind);
    if (!vb) continue;
    geometries.add(geo);
    let next = swapped.get(vb);
    if (next === undefined) {
      const bytes = srgbColourBytes(vb, geo.getTotalVertices());
      next = bytes
        ? new VertexBuffer(geo.getEngine(), bytes, VertexBuffer.ColorKind, { updatable: false, size: 4, type: VertexBuffer.UNSIGNED_BYTE, normalized: true, useBytes: true, stride: 4 })
        : null;
      swapped.set(vb, next);
    }
    if (!next) continue;
    // Disposes the old buffer's reference held by this geometry (shared buffers count one per geometry).
    geo.setVerticesBuffer(next, null, true);
    for (const user of geo.meshes) user.hasVertexAlpha = false;
  }
}
