// The castle kit's shared material library (docs/art/castle-kit.md,
// "Materials", "Textures", "The client side"). Kitted cells, and the props kit of every cell, are
// drawn with a small fixed set of `castle_*` materials whose textures live
// once in `assets/world/textures/` instead of inside each cell's .glb. The
// library builds each toon material once, on first use, from
// `textures.json`, and every cell that uses it shares that one material:
// one GPU copy of each texture, however many cells are resident.
//
// - Reference-counted per resident cell (`acquire` / `release`); the
//   material and its textures are freed a few seconds after the last cell
//   using it goes (a short grace so a cell streaming out and back in, or a
//   neighbour that uses the same material loading next, does not reload).
// - Quality: Low uses the `_lo` files (a quarter of the memory); switching
//   loads the other set, swaps each texture in as it arrives, then frees the
//   old one. World-mode materials (tiling stone and floors) get anisotropy 4
//   at Medium and High.
// - Graceful: a missing `textures.json`, or a material it does not list,
//   leaves the glTF placeholder (the greybox colour) converted as before; a
//   texture that fails to load leaves the placeholder colour in place.
// - `tint: "sky"` materials (the windows' glass) scale their emission by
//   the hour's sky light, set once per clock tick (`setSkyLight`).
// - KTX2 (castle kit M4, docs/art/castle-kit.md "Textures"): a material whose
//   entry lists `ktx2` files loads those (GPU-compressed: a quarter to an
//   eighth of the PNG's memory) when the decoder is usable (`ktx2.ts`: a
//   compressed format the GPU has, no `?ktx2=off`). Any KTX2 failure loads
//   that texture's PNG instead (one warning per material; a KTX2 that has not
//   arrived after KTX2_TIMEOUT_MS counts as failed, because Babylon's decode
//   never settles when its workers cannot start); if no KTX2 has
//   loaded yet when one fails, the decoder is taken as broken and the whole
//   library loads PNGs from then on.
// - `tint: "sky_dome"` (castle_sky, the Great Hall's enchanted ceiling;
//   castle kit M3, 17.7): unlit, no fog, its colour from the hour's sky
//   through castle/sky.ts (fed by clock/daynight.ts once per clock tick).
//
// - Exterior clones (castle M5c, docs/art/castle-kit.md "Exterior"): a
//   material named `<base>@ext` (an outside face of the courtyard or a
//   facade) is a second material that shares the base's textures (one GPU
//   copy): it holds one reference on the base (so the base's textures load
//   even when no cell draws the base itself), takes the base's textures as
//   they arrive and on every quality swap, and is freed with its cells (its
//   reference on the base goes with it). Its only difference is its name,
//   which makes it outdoor (`isOutdoorMaterial`): High's sun shadow falls on
//   it (scene.ts); Medium and Low draw it as the base.
//
// Nothing here runs per frame.

import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Material } from "@babylonjs/core/Materials/material";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Texture } from "@babylonjs/core/Materials/Textures/texture";
import type { Scene } from "@babylonjs/core/scene";
import type { Quality } from "../../settings";
import { ToonPlugin } from "../toon/material";
import { worldRamp, WORLD_TOON } from "../toon/palette";
import { emissiveTextureLevel, WORLD_MAX_LIGHTS, type SharedMaterials } from "../toon/convert";
import { configureKtx2, gpuFormatName, gpuTextureBytes, ktx2Usable, ktx2Wanted } from "./ktx2";
import { materials as KIT_MATERIALS } from "../../../../data/art/castle_textures.json";
import { CastleSkyPlugin } from "./sky";
import { shadeSummary } from "./shade";
import { TERRAIN_MATERIAL, TERRAIN_TURF, TerrainPlugin } from "./terrain";
import { assetUrl } from "../../assets";

/** The exterior clone suffix (grid.EXT_SUFFIX): `castle_wall_b@ext` shares `castle_wall_b`'s textures. */
export const EXT_SUFFIX = "@ext";

/**
 * Castle materials outdoor by name, never cloned (grid.OUTDOOR_MATERIALS):
 * the slate roofs and the courtyard's paving take the sun's shadow as they are.
 */
export const OUTDOOR_MATERIALS: ReadonlySet<string> = new Set(["castle_roof", "castle_floor_court"]);

/** Whether `name` is an exterior clone's (`<base>@ext`, with a base name before the suffix). */
export function isExtName(name: string): boolean {
  return name.length > EXT_SUFFIX.length && name.endsWith(EXT_SUFFIX);
}

/** `castle_wall_b@ext` -> `castle_wall_b` (grid.ext_base); any other name unchanged. */
export function extBase(name: string): string {
  return isExtName(name) ? name.slice(0, -EXT_SUFFIX.length) : name;
}

/**
 * Materials drawn on another library material's textures under
 * their own name (castle/terrain.ts: `grounds_terrain` on the turf of
 * `castle_floor_grass`). Built like an exterior clone (one reference on the
 * base, its textures shared); the toon row is the material's own when the
 * toon palette has one, else the base's.
 */
export const TEXTURE_ALIASES: ReadonlyMap<string, string> = new Map([[TERRAIN_MATERIAL, TERRAIN_TURF]]);

/** The library material whose textures `name` draws: an alias's base, an exterior clone's base, or itself. */
export function textureBase(name: string): string {
  return TEXTURE_ALIASES.get(name) ?? extBase(name);
}

/** The toon row `name` lights with: its own, or an alias's base's when the palette has none for it. */
export function toonRowName(name: string): string {
  const alias = TEXTURE_ALIASES.get(name);
  return alias !== undefined && !WORLD_TOON.materials[name] ? alias : name;
}

/**
 * Whether a castle material is outdoor (grid.is_outdoor_material): an
 * exterior clone or one of `OUTDOOR_MATERIALS`. High's sun shadow falls on
 * these (scene.ts), as on the greybox `wall_exterior` and `roof`.
 */
export function isOutdoorMaterial(name: string): boolean {
  return isExtName(name) || OUTDOOR_MATERIALS.has(name);
}

/** One material of `textures.json`, checked and with its defaults filled in. */
export interface CastleTextureSpec {
  /** Files relative to textures.json: full size (Medium, High) and half size (Low). */
  base: string;
  lo: string;
  px: [number, number];
  emissive: string | null;
  emissiveLo: string | null;
  emissiveStrength: number;
  unlit: boolean;
  alpha: "opaque" | "mask" | "blend";
  alphaCutoff: number;
  doubleSided: boolean;
  /** Vegetation look A (`castle_foliage`): both faces light with the authored normal (no back-face flip). */
  sharedNormals: boolean;
  /** "sky": emission scaled by the sky light (glass); "sky_dome": the enchanted ceiling (castle/sky.ts). */
  tint: "sky" | "sky_dome" | null;
  /** castle_props only: the atlas's slot-table key (props_atlas.key()). */
  key: string | null;
  /** Medium also uses the half-size files (`medium: "lo"`; castle_trim, for GPU memory). */
  mediumLo: boolean;
  /** KTX2 copies of `base` / `lo` (castle kit M4), null when the library has none. */
  ktx2: string | null;
  ktx2Lo: string | null;
  /** KTX2 copies of the emissive images: the emissive's own, or `ktx2` again when the emissive is the base image (castle_glass). */
  emissiveKtx2: string | null;
  emissiveKtx2Lo: string | null;
  ktx2Mode: "etc1s" | "uastc" | null;
}

const isKtx2File = (v: unknown): v is string => isFile(v) && v.endsWith(".ktx2");

/**
 * The KTX2 fields of a textures.json entry, checked: all four files
 * resolved (null when the entry has none or they are malformed: the
 * material then loads its PNGs only).
 */
function parseKtx2(name: string, m: Record<string, unknown>, base: string, emissive: string | null): Pick<CastleTextureSpec, "ktx2" | "ktx2Lo" | "emissiveKtx2" | "emissiveKtx2Lo" | "ktx2Mode"> {
  const none = { ktx2: null, ktx2Lo: null, emissiveKtx2: null, emissiveKtx2Lo: null, ktx2Mode: null };
  if (m.ktx2 === undefined && m.ktx2_lo === undefined) return none;
  const ownEmissive = emissive !== null && emissive !== base;
  const ok =
    isKtx2File(m.ktx2) &&
    isKtx2File(m.ktx2_lo) &&
    (m.ktx2_mode === "etc1s" || m.ktx2_mode === "uastc") &&
    (m.emissive_ktx2 === undefined || isKtx2File(m.emissive_ktx2)) &&
    (m.emissive_ktx2_lo === undefined || isKtx2File(m.emissive_ktx2_lo)) &&
    (m.emissive_ktx2 === undefined) === (m.emissive_ktx2_lo === undefined);
  if (!ok) {
    console.warn(`[castle] textures.json: ignoring the malformed KTX2 fields of ${JSON.stringify(name)} (PNG only)`);
    return none;
  }
  const own = ownEmissive && m.emissive_ktx2 !== undefined;
  return {
    ktx2: m.ktx2 as string,
    ktx2Lo: m.ktx2_lo as string,
    emissiveKtx2: own ? (m.emissive_ktx2 as string) : emissive !== null && !ownEmissive ? (m.ktx2 as string) : null,
    emissiveKtx2Lo: own ? (m.emissive_ktx2_lo as string) : emissive !== null && !ownEmissive ? (m.ktx2_lo as string) : null,
    ktx2Mode: m.ktx2_mode as "etc1s" | "uastc",
  };
}

/** What a texture slot loads: `file` first (the KTX2 copy when `ktx2` is on and the entry has one), `png` if that fails. */
export interface TextureChoice {
  file: string;
  png: string;
  ktx2: boolean;
}

/**
 * The file a material's `slot` loads at half size (`lo`) or full size,
 * with or without KTX2. Null when the slot has no image (no emissive).
 */
export function chooseTexture(spec: CastleTextureSpec, slot: "diffuse" | "emissive", lo: boolean, ktx2: boolean): TextureChoice | null {
  const png = slot === "diffuse" ? (lo ? spec.lo : spec.base) : lo ? spec.emissiveLo : spec.emissive;
  if (!png) return null;
  const k = slot === "diffuse" ? (lo ? spec.ktx2Lo : spec.ktx2) : lo ? spec.emissiveKtx2Lo : spec.emissiveKtx2;
  return ktx2 && k ? { file: k, png, ktx2: true } : { file: png, png, ktx2: false };
}

/** The glow's emissive texture level (grid.GLOW_LEVEL; docs/art/castle-kit.md "Glow"). */
export const GLOW_LEVEL = 1;

/**
 * Whether a material's emissive image is glow (the props atlas: lanterns,
 * lamps, braziers, candles), shown as painted by the toon plugin: an
 * emissive image that is not the sky's (castle_glass) on a lit material.
 */
export function isGlow(spec: Pick<CastleTextureSpec, "emissive" | "tint" | "unlit">): boolean {
  return !!spec.emissive && spec.tint !== "sky" && !spec.unlit;
}

/**
 * How long a KTX2 texture may take to arrive and decode before it counts as
 * failed (ms). Babylon's decode never settles when its workers cannot start
 * (a missing decoder script), so a timeout is the only signal; generous, so
 * a slow connection still gets its KTX2 (2 MB of KTX2 in all at Medium).
 */
export const KTX2_TIMEOUT_MS = 15000;

/** How long a material outlives the last cell using it (ms). */
export const RELEASE_GRACE_MS = 4000;
/** Anisotropic filtering of the tiling (world-mapped) materials at Medium and High. */
const WORLD_ANISOTROPY = 4;
/**
 * How much of its lit colour a `tint: "sky"` material with an emissive
 * image (the windows' glass) keeps under that emission. Glass shows the
 * light behind it, not the light falling on it: with the full lit colour
 * (its toon row is always lit) plus the emission, the panes clipped to
 * white by day and stayed bright at night.
 */
export const SKY_DIFFUSE = 0.25;

const isFile = (v: unknown): v is string => typeof v === "string" && v.length > 0 && !v.includes("..") && !v.startsWith("/") && !/^[a-z]+:/i.test(v);

/**
 * `textures.json` (`{ version: 1, materials: { <castle_* name>: {...} } }`,
 * docs/art/castle-kit.md "Textures") as checked specs. Entries that are not
 * valid are skipped with a warning (their cells keep the placeholder); an
 * unknown version gives an empty library.
 */
export function parseTexturesJson(json: unknown): Map<string, CastleTextureSpec> {
  const out = new Map<string, CastleTextureSpec>();
  const j = json as { version?: unknown; materials?: unknown } | null;
  if (!j || j.version !== 1 || typeof j.materials !== "object" || j.materials === null) {
    if (j) console.warn("[castle] textures.json: unsupported version or no materials; the kit draws its placeholders");
    return out;
  }
  for (const [name, raw] of Object.entries(j.materials as Record<string, unknown>)) {
    const m = raw as Record<string, unknown> | null;
    const px = m?.px;
    const okPx = Array.isArray(px) && px.length === 2 && px.every((v) => Number.isInteger(v) && (v as number) > 0);
    const alpha = m?.alpha ?? "opaque";
    const strength = m?.emissive_strength ?? 1;
    const cutoff = m?.alpha_cutoff ?? 0.5;
    const ok =
      name.startsWith("castle_") &&
      m !== null &&
      isFile(m.base) &&
      isFile(m.lo) &&
      okPx &&
      (m.emissive === undefined || isFile(m.emissive)) &&
      (m.emissive_lo === undefined || isFile(m.emissive_lo)) &&
      (alpha === "opaque" || alpha === "mask" || alpha === "blend") &&
      typeof strength === "number" &&
      Number.isFinite(strength) &&
      strength >= 0 &&
      typeof cutoff === "number" &&
      (m.tint === undefined || m.tint === "sky" || m.tint === "sky_dome") &&
      (m.medium === undefined || m.medium === "full" || m.medium === "lo");
    if (!ok) {
      console.warn(`[castle] textures.json: skipping invalid material ${JSON.stringify(name)}`);
      continue;
    }
    const emissive = (m.emissive as string | undefined) ?? null;
    const emissiveLo = (m.emissive_lo as string | undefined) ?? emissive;
    out.set(name, {
      ...parseKtx2(name, m, m.base as string, emissive),
      base: m.base as string,
      lo: m.lo as string,
      px: [px[0] as number, px[1] as number],
      emissive,
      emissiveLo,
      emissiveStrength: strength,
      unlit: m.unlit === true,
      alpha,
      alphaCutoff: cutoff,
      doubleSided: m.double_sided === true,
      sharedNormals: m.shared_normals === true,
      tint: m.tint === "sky" || m.tint === "sky_dome" ? m.tint : null,
      key: typeof m.key === "string" ? m.key : null,
      mediumLo: m.medium === "lo",
    });
  }
  return out;
}

/** Whether `name` tiles in world space (castle_textures.json `map: "world"`): those get anisotropic filtering. */
export function isWorldMapped(name: string): boolean {
  return (KIT_MATERIALS as Record<string, { map?: string } | undefined>)[name]?.map === "world";
}

/**
 * The emission scale of `tint: "sky"` materials for the castle's current
 * sky fill (the "sky" hemisphere light), relative to noon: windows glow with
 * daylight by day and dim at night. The ratio is cubed because the toon
 * night keeps about 60 % of the noon fill (moonlight), which left the
 * painted daytime panes glowing at midnight. Clamped so night glass still
 * reads.
 */
export function skyEmission(hemiIntensity: number, hemiLuma: number, noonIntensity: number, noonLuma: number): number {
  const noon = noonIntensity * noonLuma;
  if (!(noon > 0)) return 1;
  const f = (hemiIntensity * hemiLuma) / noon;
  return Math.min(1, Math.max(0.12, Number.isFinite(f) ? f ** 3 : 1));
}

interface Live {
  name: string;
  spec: CastleTextureSpec;
  mat: StandardMaterial;
  refs: number;
  timer: ReturnType<typeof setTimeout> | null;
  /** Bumped on every texture (re)load; a load that finishes for an old generation frees itself. */
  gen: number;
  /** The diffuse colour shown until the texture arrives, and kept if it fails (the glTF placeholder's). */
  placeholder: Color3;
  /** The emissive texture's level before the sky tint. */
  emissiveLevel: number;
  /** Whether the half-size files are the ones loaded (or loading). */
  lo: boolean;
  /** An exterior clone's base (it shares the base's textures and holds one reference on it); null for a base. */
  base: Live | null;
  /** A base's live exterior clones (empty for a clone). */
  clones: Set<Live>;
}

export class CastleLibrary implements SharedMaterials {
  private specs = new Map<string, CastleTextureSpec>();
  private loading: Promise<void> | null = null;
  private readonly live = new Map<string, Live>();
  private sky = 1;
  private disposed = false;
  private readonly graceMs: number;
  /** Whether textures load from their KTX2 copies; turned off for good if the decoder never works. */
  private useKtx2: boolean;
  /** Whether any KTX2 texture has loaded (a failure after that is the file's, not the decoder's). */
  private ktx2Loaded = false;
  /** Materials already warned about a KTX2 failure (one warning each). */
  private readonly ktx2Warned = new Set<string>();

  constructor(
    private readonly scene: Scene,
    /** `assets/world/textures/` (a logical path, src/assets.ts: textures.json and the images). */
    private readonly baseUrl: string,
    private quality: Quality,
    opts: { graceMs?: number; ktx2?: boolean } = {},
  ) {
    this.graceMs = opts.graceMs ?? RELEASE_GRACE_MS;
    this.useKtx2 = opts.ktx2 ?? (ktx2Wanted() && ktx2Usable(scene.getEngine().getCaps()));
  }

  /** Whether textures load from their KTX2 copies (false: PNGs; `?ktx2=off`, no usable format, or the decoder failed). */
  get ktx2(): boolean {
    return this.useKtx2;
  }

  /** Fetches textures.json once; a missing or broken file is an empty library (never rejects). */
  ready(): Promise<void> {
    this.loading ??= fetch(assetUrl(`${this.baseUrl}textures.json`))
      .then(async (r) => {
        if (!r.ok) {
          console.info(`[castle] no texture library (textures.json: ${r.status}); kit cells draw their placeholders`);
          return;
        }
        this.useSpecs(parseTexturesJson(await r.json()));
      })
      .catch((e: unknown) => {
        console.info("[castle] could not read textures.json; kit cells draw their placeholders", e instanceof Error ? e.message : e);
      });
    return this.loading;
  }

  /** Replaces the parsed library (`ready` does this; tests call it directly). */
  useSpecs(specs: Map<string, CastleTextureSpec>): void {
    this.specs = specs;
    this.loading ??= Promise.resolve();
  }

  /** Whether `name` has textures in the library (an exterior clone: its base's). */
  has(name: string): boolean {
    return this.specs.has(textureBase(name));
  }

  /** Materials currently built (shared by the resident cells). */
  get size(): number {
    return this.live.size;
  }

  /** References held on `name` (0 when not built or waiting out its grace). */
  refs(name: string): number {
    return this.live.get(name)?.refs ?? 0;
  }

  /**
   * The shared material `name`, built on first use, with one more reference
   * (release it with `release`). Null when the library has no such material:
   * the caller keeps its placeholder. `placeholder` is the glTF material it
   * stands in for (its colour shows until the texture arrives).
   */
  take(name: string, placeholder?: Material): StandardMaterial | null {
    if (this.disposed) return null;
    let e = this.live.get(name);
    if (!e) {
      if (isExtName(name) || TEXTURE_ALIASES.has(name)) {
        // An exterior clone, or an alias: one reference on its base for the clone's lifetime.
        const baseName = textureBase(name);
        if (isExtName(baseName) || !this.specs.has(baseName) || !this.take(baseName, placeholder)) return null;
        e = this.buildClone(name, this.live.get(baseName)!, placeholder);
        if (name === TERRAIN_MATERIAL) new TerrainPlugin(e.mat);
      } else {
        const spec = this.specs.get(name);
        if (!spec) return null;
        e = this.build(name, spec, placeholder);
      }
      this.live.set(name, e);
    }
    if (e.timer !== null) {
      clearTimeout(e.timer);
      e.timer = null;
    }
    e.refs++;
    return e.mat;
  }

  /** Drops one reference; the last one frees the material and its textures after the grace period. */
  release(name: string): void {
    const e = this.live.get(name);
    if (!e || e.refs === 0) return;
    e.refs--;
    if (e.refs > 0) return;
    if (this.graceMs <= 0) {
      this.free(e);
      return;
    }
    e.timer = setTimeout(() => {
      e.timer = null;
      if (e.refs === 0 && this.live.get(e.name) === e) this.free(e);
    }, this.graceMs);
  }

  /** Low uses the half-size files (Medium too for `medium: "lo"`); the others the full ones. Reloads what changes. */
  setQuality(q: Quality): void {
    this.quality = q;
    for (const e of this.live.values()) {
      if (e.base) continue; // a clone draws its base's textures (shareTextures)
      if (this.wantsLo(e.spec) !== e.lo) this.loadTextures(e);
      else this.applyAnisotropy(e);
    }
  }

  /** Whether `spec` draws its half-size files at the current quality. */
  private wantsLo(spec: CastleTextureSpec): boolean {
    return this.quality === "low" || (this.quality === "medium" && spec.mediumLo);
  }

  /** The emission scale of the `tint: "sky"` materials (skyEmission); once per clock tick. */
  setSkyLight(f: number): void {
    if (Math.abs(f - this.sky) < 0.005) return;
    this.sky = f;
    for (const e of this.live.values()) if (e.spec.tint === "sky") this.applySky(e);
  }

  /** GPU bytes of the library's textures in their real formats (with mips; `ktx2.ts` gpuTextureBytes), each image counted once. */
  textureBytes(): number {
    const seen = new Set<object>();
    let bytes = 0;
    for (const e of this.live.values()) {
      for (const t of [e.mat.diffuseTexture, e.mat.emissiveTexture]) {
        const internal = t?.getInternalTexture();
        if (!t || !internal || seen.has(internal)) continue;
        seen.add(internal);
        bytes += gpuTextureBytes(internal.width, internal.height, internal.format, internal.type, internal.generateMipMaps);
      }
    }
    return bytes;
  }

  /** The GPU formats of the library's textures, with counts ("KTX2: ASTC 4x4 x5, ETC2 RGB x4" or "PNG: RGBA8 x9"), for the F3 line. */
  formatSummary(): string {
    const seen = new Set<object>();
    const counts = new Map<string, number>();
    for (const e of this.live.values()) {
      for (const t of [e.mat.diffuseTexture, e.mat.emissiveTexture]) {
        const internal = t?.getInternalTexture();
        if (!t || !internal || seen.has(internal)) continue;
        seen.add(internal);
        const name = gpuFormatName(internal.format, internal.type);
        counts.set(name, (counts.get(name) ?? 0) + 1);
      }
    }
    const list = [...counts].map(([n, c]) => `${n} x${c}`).join(", ");
    return `${this.useKtx2 ? "KTX2" : "PNG"}${list ? `: ${list}` : ""}`;
  }

  /** How many of the resident materials read the baked shade (castle D1, castle/shade.ts), for the F3 line: "shade 9/11". */
  shadeSummary(): string {
    return shadeSummary(this.live.keys(), (name) => worldRamp(name));
  }

  dispose(): void {
    this.disposed = true;
    // Clones first: they hold references on their bases.
    const all = [...this.live.values()];
    for (const e of all) if (e.base) this.free(e);
    for (const e of all) if (!e.base && this.live.get(e.name) === e) this.free(e);
  }

  // ------------------------------------------------------------ internals

  private build(name: string, spec: CastleTextureSpec, src: Material | undefined): Live {
    const { mat, placeholder } = this.makeMaterial(name, spec, src);
    // Glow never runs above level 1 (a brighter glow is a hotter colour, not a higher level).
    const level = isGlow(spec) ? Math.min(GLOW_LEVEL, emissiveTextureLevel(spec.emissiveStrength)) : emissiveTextureLevel(spec.emissiveStrength);
    const e: Live = { name, spec, mat, refs: 0, timer: null, gen: 0, placeholder, emissiveLevel: level, lo: false, base: null, clones: new Set() };
    this.loadTextures(e);
    if (spec.tint === "sky") this.applySky(e);
    return e;
  }

  /**
   * An exterior clone of `base`: the same material settings under its
   * own name, drawing the base's texture objects (shareTextures); it loads
   * nothing itself. Its toon row is the base's (`worldRamp` strips the suffix).
   */
  private buildClone(name: string, base: Live, src: Material | undefined): Live {
    const { mat, placeholder } = this.makeMaterial(name, base.spec, src);
    const e: Live = { name, spec: base.spec, mat, refs: 0, timer: null, gen: 0, placeholder, emissiveLevel: base.emissiveLevel, lo: base.lo, base, clones: new Set() };
    base.clones.add(e);
    this.shareTextures(base, e);
    if (base.spec.unlit) mat.emissiveColor.copyFrom(base.mat.emissiveColor);
    return e;
  }

  /** Points `clone` at `base`'s current textures and texture colour (the textures stay the base's to free). */
  private shareTextures(base: Live, clone: Live): void {
    const from = base.mat;
    const to = clone.mat;
    clone.lo = base.lo;
    if (from.diffuseTexture) {
      to.diffuseTexture = from.diffuseTexture;
      to.diffuseColor.copyFrom(from.diffuseColor);
    }
    if (from.emissiveTexture) to.emissiveTexture = from.emissiveTexture;
  }

  /** A toon StandardMaterial for `spec` (as the converter makes one), and its placeholder colour. */
  private makeMaterial(name: string, spec: CastleTextureSpec, src: Material | undefined): { mat: StandardMaterial; placeholder: Color3 } {
    const scene = this.scene;
    const mat = new StandardMaterial(name, scene);
    const albedo = (src as unknown as { albedoColor?: Color3 } | undefined)?.albedoColor;
    const placeholder = albedo ? albedo.toGammaSpace() : Color3.White();
    mat.diffuseColor = placeholder.clone();
    mat.specularColor = Color3.Black();
    mat.backFaceCulling = !spec.doubleSided;
    // Vegetation look A: cards whose normals are bent away from the crown's centre light the same on both faces.
    mat.twoSidedLighting = spec.doubleSided && !spec.sharedNormals;
    const side = (src as unknown as { sideOrientation?: number | null } | undefined)?.sideOrientation;
    mat.sideOrientation = side ?? (scene.useRightHandedSystem ? Material.CounterClockWiseSideOrientation : Material.ClockWiseSideOrientation);
    mat.maxSimultaneousLights = WORLD_MAX_LIGHTS;
    if (spec.alpha === "mask") {
      mat.transparencyMode = Material.MATERIAL_ALPHATEST;
      mat.alphaCutOff = spec.alphaCutoff;
    } else if (spec.alpha === "blend") {
      mat.transparencyMode = Material.MATERIAL_ALPHABLEND;
      mat.useAlphaFromDiffuseTexture = true;
    } else {
      mat.transparencyMode = Material.MATERIAL_OPAQUE;
    }
    if (spec.tint === "sky_dome") {
      // The enchanted ceiling (castle/sky.ts): unlit and unfogged; until its texture
      // arrives the placeholder colour shows (no white flash), then the sky's colour.
      mat.disableLighting = true;
      mat.fogEnabled = false;
      mat.emissiveColor = Color3.Black();
      new CastleSkyPlugin(mat);
    } else if (spec.unlit) {
      // As the converter does: lighting off, the texture shown as it is.
      mat.disableLighting = true;
      mat.emissiveColor = Color3.White();
    } else {
      // Castle kit M2: an emissive image that is not the sky's (the props atlas) is glow, shown as painted.
      new ToonPlugin(mat, worldRamp(toonRowName(name)), { glow: isGlow(spec) });
    }
    if (spec.emissive && !spec.unlit) {
      // Emission adds to the lit colour (as glTF's does), from the texture.
      mat.useEmissiveAsIllumination = true;
      mat.emissiveColor = Color3.Black();
    }
    return { mat, placeholder };
  }

  /** (Re)loads the textures for the current quality; each is swapped in when it arrives. */
  private loadTextures(e: Live): void {
    const gen = ++e.gen;
    const spec = e.spec;
    const low = this.wantsLo(spec);
    e.lo = low;
    if (this.useKtx2 && spec.ktx2) configureKtx2();
    const diffuse = chooseTexture(spec, "diffuse", low, this.useKtx2);
    if (diffuse) this.loadTexture(e, gen, diffuse, "diffuse");
    // Its own Texture object even when it is the base image again (castle_glass):
    // the engine shares the GPU copy by URL, and the two keep separate levels.
    const em = spec.unlit ? null : chooseTexture(spec, "emissive", low, this.useKtx2);
    if (em) this.loadTexture(e, gen, em, "emissive");
  }

  /** A KTX2 texture failed: fall back to its PNG (and to PNGs for the whole library if no KTX2 ever loaded). */
  private ktx2Failed(e: Live, file: string): void {
    if (this.useKtx2 && !this.ktx2Loaded) {
      this.useKtx2 = false;
      console.warn(`[castle] the KTX2 decoder did not work (${file}); the castle texture library loads PNGs`);
    } else if (this.useKtx2 && !this.ktx2Warned.has(e.name)) {
      // (Once the library has switched to PNGs, loads still in flight fall back silently.)
      this.ktx2Warned.add(e.name);
      console.warn(`[castle] KTX2 texture ${file} failed to load; ${e.name} loads its PNG`);
    }
  }

  private loadTexture(e: Live, gen: number, choice: TextureChoice, slot: "diffuse" | "emissive"): void {
    const file = choice.file;
    let tex: Texture | null = null;
    let state: "pending" | "loaded" | "failed" = "pending";
    const settle = () => {
      if (!tex || state === "pending") return;
      const t = tex;
      if (gen !== e.gen || this.live.get(e.name) !== e || this.disposed) {
        t.dispose(); // a newer load (or a free) overtook this one
        return;
      }
      if (state === "failed") {
        t.dispose();
        if (choice.ktx2) {
          this.ktx2Failed(e, file);
          this.loadTexture(e, gen, { file: choice.png, png: choice.png, ktx2: false }, slot);
          return;
        }
        console.warn(`[castle] texture ${file} failed to load; ${e.name} keeps its placeholder colour`);
        return;
      }
      if (choice.ktx2) this.ktx2Loaded = true;
      const mat = e.mat;
      if (slot === "diffuse") {
        const old = mat.diffuseTexture;
        mat.diffuseTexture = t;
        const k = e.spec.tint === "sky" && e.spec.emissive ? SKY_DIFFUSE : 1;
        mat.diffuseColor = new Color3(k, k, k);
        if (e.spec.alpha !== "opaque") t.hasAlpha = true;
        for (const c of e.clones) this.shareTextures(e, c);
        if (old && old !== t) old.dispose();
      } else {
        const old = mat.emissiveTexture;
        mat.emissiveTexture = t;
        this.applySky(e);
        for (const c of e.clones) this.shareTextures(e, c);
        if (old && old !== t) old.dispose();
      }
    };
    let timer: ReturnType<typeof setTimeout> | null = null;
    const finish = (result: "loaded" | "failed") => {
      if (state !== "pending") return; // a late answer after the KTX2 timeout
      if (timer !== null) clearTimeout(timer);
      state = result;
      settle();
    };
    if (choice.ktx2) timer = setTimeout(() => finish("failed"), KTX2_TIMEOUT_MS);
    tex = new Texture(assetUrl(this.baseUrl + file), this.scene, {
      noMipmap: false,
      invertY: false, // glTF UV convention, like the glTF loader's own textures (KTX2: rows top first, never flipped)
      samplingMode: Texture.TRILINEAR_SAMPLINGMODE,
      onLoad: () => finish("loaded"),
      onError: () => finish("failed"),
    });
    tex.wrapU = Texture.WRAP_ADDRESSMODE;
    tex.wrapV = Texture.WRAP_ADDRESSMODE;
    tex.anisotropicFilteringLevel = this.anisotropyFor(e.name);
    if (slot === "emissive") tex.level = e.emissiveLevel;
    settle(); // the load may have finished inside the constructor
  }

  private anisotropyFor(name: string): number {
    return this.quality !== "low" && isWorldMapped(name) ? WORLD_ANISOTROPY : 1;
  }

  private applyAnisotropy(e: Live): void {
    const a = this.anisotropyFor(e.name);
    for (const t of [e.mat.diffuseTexture, e.mat.emissiveTexture]) if (t) t.anisotropicFilteringLevel = a;
  }

  private applySky(e: Live): void {
    if (e.spec.tint === "sky_dome") return; // castle/sky.ts colours it
    const f = e.spec.tint === "sky" ? this.sky : 1;
    // (A clone's emissive texture is its base's object: the level set here is both's.)
    if (e.spec.unlit) {
      e.mat.emissiveColor.set(f, f, f);
      for (const c of e.clones) c.mat.emissiveColor.set(f, f, f);
    } else if (e.mat.emissiveTexture) e.mat.emissiveTexture.level = e.emissiveLevel * f;
  }

  private free(e: Live): void {
    if (e.timer !== null) clearTimeout(e.timer);
    e.timer = null;
    e.gen++;
    this.live.delete(e.name);
    const base = e.base;
    if (base) {
      // A clone's textures are its base's: detached before the dispose, then its reference on the base goes.
      e.mat.diffuseTexture = null;
      e.mat.emissiveTexture = null;
      e.mat.dispose(false, false);
      base.clones.delete(e);
      if (!this.disposed) this.release(base.name);
      return;
    }
    e.mat.dispose(false, true);
  }
}
