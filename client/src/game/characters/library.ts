// Loads and caches character parts and rigs.
//
// Part files are fetched once per URL and kept as CPU data (parts.ts), so the
// world and the wardrobe mirror, which run separate engines, share them. A
// file that is missing or unreadable falls back to the greybox part of the
// same shape (greybox.ts), fitted to the rig, so a character always draws.
//
// A rig is one body type's skeleton plus its clip set: authored clips from the
// body file or `characters/animations.glb` (and `animations_<body>.glb`),
// procedural ones for whatever is missing. A greybox body means a greybox
// rig, and then every part is greybox too: art parts are fitted to the art
// skeleton and would not line up with the stand-in one.

import type { BodyType } from "../../content/catalog";
import { Gltf, isGlb } from "./gltf";
import { partFromGltf, type ClipData, type PartData } from "./parts";
import { clipSet, Rig, type BakedClip, type ClipKey } from "./clips";
import { greyboxKind, greyboxPart, greyboxSkeleton } from "./greybox";
import { underFile, type PartRequest, type Recipe } from "./recipe";
import type { PartInput } from "./assemble";
import { fitHair, hatFitFor, scaleHat, type HatFitTable } from "./hatfit";
import { assetShipped, assetUrl } from "../../assets";

/** Files a rig looks for but does not need: extra clip sets (the body file carries its own). */
const OPTIONAL_FILE = /^characters\/animations(_[a-z0-9_]+)?\.glb$/;

export type Fetcher = (url: string) => Promise<{ ok: boolean; arrayBuffer(): Promise<ArrayBuffer> }>;

export interface RigKit {
  /** `body_a`, or `body_a/greybox`. */
  key: string;
  rig: Rig;
  clips: Map<ClipKey, BakedClip>;
  greybox: boolean;
  /** The base body part. */
  body: PartData;
  /** The body's face texture pair (URLs), from the manifest's body entry; null without one. */
  face: FaceTextureUrls | null;
}

/** A body's face textures (docs/art/character-look.md section 3.1), as URLs. */
export interface FaceTextureUrls {
  colour: string;
  mask: string;
}

/** What the art manifest says about an asset (only the fields the client uses). */
export interface ManifestEntry {
  hides?: string[];
  long?: boolean;
  /** Bodies: the face texture pair, paths relative to the assets root. */
  face?: { colour?: unknown; mask?: unknown };
  kind?: string;
  /** Hair: per body, how each hat fits over it (hatfit.ts). */
  hat_fit?: Record<string, HatFitTable>;
  /** Hair: per body, the hat variant's file, where the style has one (its tails change under a hat). */
  cap_files?: Record<string, unknown>;
}

/** The face texture URLs of a body's manifest entry (its paths under the logical folder `baseUrl`), or null when it has none. */
export function faceTextureUrls(baseUrl: string, entry: ManifestEntry | undefined): FaceTextureUrls | null {
  const f = entry?.face;
  if (!f || typeof f.colour !== "string" || typeof f.mask !== "string") return null;
  return { colour: assetUrl(baseUrl + f.colour), mask: assetUrl(baseUrl + f.mask) };
}

/** A part with the manifest's `hides` and `long` where the file itself has none. */
export function withManifest(part: PartData, entry: ManifestEntry | undefined): PartData {
  if (!entry) return part;
  const hides = part.hides.length || !Array.isArray(entry.hides) ? part.hides : entry.hides.filter((h) => typeof h === "string");
  const long = part.long || entry.long === true;
  return hides === part.hides && long === part.long ? part : { ...part, hides, long };
}

/**
 * Whether a hat covers the scalp, so hair under it takes its `_cap` variant
 * (crown pressed under the band). Hair trims (the ribbon bow, the comb) hide
 * nothing and leave the hair as styled. Unknown hats count as covering.
 */
export function hatCoversHair(entry: ManifestEntry | undefined): boolean {
  return !Array.isArray(entry?.hides) || entry.hides.includes("scalp");
}

/**
 * A covering hat over hair that carries a fit for it (hatfit.ts, the
 * hair's manifest `hat_fit`): the hair its crown covers dropped (and,
 * under a capped hat, the rest under the crown pressed in), the hat scaled
 * about the head centre to sit on the rest, in place in `loaded`. Done once
 * per look, in the bind pose. Hair without a fit (stand-ins) stays as it is.
 */
export function fitHat(reqs: PartRequest[], loaded: (PartData | null)[], manifest: Map<string, ManifestEntry>, body: string): void {
  const hi = reqs.findIndex((r) => r.role === "hair");
  const ti = reqs.findIndex((r) => r.role === "hat");
  const hair = hi >= 0 ? loaded[hi] : null;
  const hat = ti >= 0 ? loaded[ti] : null;
  if (!hair || !hat) return;
  const fit = hatFitFor(manifest.get(reqs[hi]!.asset)?.hat_fit?.[body], reqs[ti]!.asset);
  if (!fit) return;
  loaded[hi] = fitHair(hair, fit);
  loaded[ti] = scaleHat(hat, fit);
}

export interface LibraryStats {
  files: number;
  missing: number;
  greyboxParts: number;
}

export class PartLibrary {
  private readonly files = new Map<string, Promise<PartData | null>>();
  private readonly rigs = new Map<number, Promise<RigKit>>();
  private readonly greybox = new Map<string, PartData>();
  private missing = 0;
  private manifest: Promise<Map<string, ManifestEntry>> | null = null;

  constructor(
    private readonly baseUrl: string,
    private readonly speeds: { walk: number; run: number },
    private readonly fetcher: Fetcher = (url) => fetch(url),
  ) {}

  get stats(): LibraryStats {
    return { files: this.files.size, missing: this.missing, greyboxParts: this.greybox.size };
  }

  /** A part file, or null when it does not exist or cannot be read. Cached per file. */
  file(path: string): Promise<PartData | null> {
    let p = this.files.get(path);
    if (!p) {
      p = this.fetchPart(path);
      this.files.set(path, p);
    }
    return p;
  }

  private async fetchPart(path: string): Promise<PartData | null> {
    try {
      // Optional files (animations.glb...) the build does not ship: no request.
      if (assetShipped(this.baseUrl + path) === false) return this.miss(path);
      const res = await this.fetcher(assetUrl(this.baseUrl + path));
      if (!res.ok) return this.miss(path);
      const bytes = await res.arrayBuffer();
      // Dev servers answer unknown paths with index.html.
      if (!isGlb(bytes)) return this.miss(path);
      return partFromGltf(Gltf.parse(bytes));
    } catch (e) {
      console.warn(`[characters] could not read ${path}:`, e);
      return this.miss(path);
    }
  }

  /** Counts a missing part file (the optional shared clip files are not parts: their absence is normal). */
  private miss(path: string): null {
    if (!OPTIONAL_FILE.test(path)) this.missing++;
    return null;
  }

  /** The first of `paths` that exists. */
  async first(paths: readonly string[]): Promise<PartData | null> {
    for (const p of paths) {
      const part = await this.file(p);
      if (part) return part;
    }
    return null;
  }

  /** The rig of body type `body`, from its file or the greybox. */
  rig(body: BodyType): Promise<RigKit> {
    let r = this.rigs.get(body.index);
    if (!r) {
      r = this.loadRig(body);
      this.rigs.set(body.index, r);
    }
    return r;
  }

  private async loadRig(body: BodyType): Promise<RigKit> {
    const part = await this.file(`${body.asset}.glb`);
    if (part?.skeleton && part.primitives.length > 0) {
      const rig = new Rig(part.skeleton);
      const extra = await Promise.all([this.file("characters/animations.glb"), this.file(`characters/animations_${body.id}.glb`)]);
      // Body-specific clips win over shared ones, which win over the body file's own.
      const clips: ClipData[] = [...(extra[1]?.clips ?? []), ...(extra[0]?.clips ?? []), ...part.clips];
      // The face textures only fit a head built for them.
      const faced = part.primitives.some((p) => p.faceTexture);
      const face = faced ? faceTextureUrls(this.baseUrl, (await this.manifestEntries()).get(body.asset)) : null;
      return { key: body.id, rig, clips: clipSet(rig, clips, this.speeds), greybox: false, body: part, face };
    }
    const rig = new Rig(greyboxSkeleton(body.index));
    const key = `${body.id}/greybox`;
    return { key, rig, clips: clipSet(rig, [], this.speeds), greybox: true, body: this.greyboxFor(key, rig, { role: "base", asset: body.asset, files: [] }), face: null };
  }

  private greyboxFor(rigKey: string, rig: Rig, req: PartRequest, under = false, body?: PartData): PartData {
    const kind = greyboxKind(req.role, req.asset);
    const key = `${rigKey}|${kind}|${req.cap ? "cap" : ""}|${under ? "under" : ""}`;
    let part = this.greybox.get(key);
    if (!part) {
      part = greyboxPart(kind, rig, { cap: req.cap, under, body });
      this.greybox.set(key, part);
    }
    return part;
  }

  /**
   * The art manifest (`characters/manifest.json`, tools/blender/characters):
   * per asset key, what its files do not say themselves (`hides`, `long`).
   */
  private manifestEntries(): Promise<Map<string, ManifestEntry>> {
    this.manifest ??= (async () => {
      try {
        const res = await this.fetcher(assetUrl(`${this.baseUrl}characters/manifest.json`));
        if (!res.ok) return new Map();
        const json = JSON.parse(new TextDecoder().decode(await res.arrayBuffer())) as { assets?: Record<string, ManifestEntry> };
        return new Map(Object.entries(json.assets ?? {}));
      } catch {
        return new Map(); // none yet, or index.html from a dev server
      }
    })();
    return this.manifest;
  }

  /** Every part of `recipe` on `kit`'s rig, greybox where a file is missing. */
  async parts(recipe: Recipe, kit: RigKit): Promise<PartInput[]> {
    const reqs = recipe.parts.filter((r) => r.role !== "base");
    const manifest = kit.greybox ? new Map<string, ManifestEntry>() : await this.manifestEntries();
    const hat = reqs.find((r) => r.role === "hat");
    const capped = !!hat && hatCoversHair(manifest.get(hat.asset));
    // The hat variant only where the style has one (its tails change under a
    // hat); every other style wears its own file under hats, fitted below.
    const noCapFile = (r: PartRequest): boolean => {
      const e = manifest.get(r.asset);
      return e?.kind === "hair" && !(e.cap_files && typeof e.cap_files[recipe.body.id] === "string");
    };
    const uncap = (r: PartRequest): PartRequest =>
      r.role === "hair" && r.cap && (!capped || noCapFile(r)) ? { ...r, cap: false, files: r.files.filter((f) => !f.includes("_cap_")) } : r;
    for (let i = 0; i < reqs.length; i++) reqs[i] = uncap(reqs[i]!);
    const files = await Promise.all(reqs.map((r) => (kit.greybox ? Promise.resolve(null) : this.first(r.files))));
    const loaded = files.map((part, i) => (part ? withManifest(part, manifest.get(reqs[i]!.asset)) : null));
    if (capped) fitHat(reqs, loaded, manifest, recipe.body.id);
    const out: PartInput[] = [{ role: "base", part: kit.body }];
    // Stand-ins on an art rig are fitted to the art body (its head, for hair and hats).
    const art = kit.greybox ? undefined : kit.body;
    const bodyGear = loaded[reqs.findIndex((r) => r.role === "body")] ?? null;
    const bodyKind = reqs.find((r) => r.role === "body");
    const longRobe = bodyGear ? bodyGear.long : bodyKind ? this.greyboxFor(kit.key, kit.rig, bodyKind, false, art).long : false;
    for (let i = 0; i < reqs.length; i++) {
      const req = reqs[i]!;
      let part = loaded[i] ?? null;
      if (part && req.role === "legs" && longRobe && !kit.greybox) {
        part = (await this.file(underFile(req.asset, recipe.body))) ?? part;
      }
      out.push({ role: req.role, part: part ?? this.greyboxFor(kit.key, kit.rig, req, req.role === "legs" && longRobe, art), dyes: req.dyes, finish: req.finish });
    }
    return out;
  }
}
