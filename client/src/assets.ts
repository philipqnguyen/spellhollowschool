// Where the game's files come from: every model, texture, icon, manifest and
// sound under public/assets/ and public/audio/ goes through `assetUrl`.
// docs/deploy.md, "Assets from a bucket".
//
// Code names a file by its logical path, relative to public/ as served
// (`assets/world/manifest.json`, `audio/sfx/foot/stone_0.mp3`; a leading
// slash, `./` and `../` segments are fine, so manifest entries such as
// `../instances/x.glb` resolve against their folder). A build compiles the
// asset map (client/scripts/asset-map.mjs: every file's content hash) into
// the bundle, and the URL is
//
// - with an asset bucket (the server's SHS_ASSETS_CDN_URL, handed to the page
//   as <meta name="shs-assets-cdn">): `<bucket URL>/<content-addressed key>`,
//   `assets/world/cell_12.3f9a1c7e5b2d4f60.glb`, immutable and cached forever;
// - without: the game server's own file with `?v=<hash>`, which the server
//   caches for a year only when the hash matches the file it has;
// - for a file the map does not know (development, where the map is empty, or
//   a path that is not a shipped file): the plain same-origin path, with a
//   warning in a build.
//
// The KTX2 decoder (assets/ktx2/) runs in workers, which must come from the
// page's own origin: `assetUrl(path, { sameOrigin: true })`.

import assetMap from "virtual:shs-asset-map";

/** The meta tag the server adds to index.html when assets come from a bucket. */
export const ASSET_CDN_META = "shs-assets-cdn";

export interface AssetResolverOptions {
  /** Logical path to content hash (the asset map). */
  map: Readonly<Record<string, string>>;
  /** The bucket's public URL without a trailing slash, or null to use the game server. */
  cdn: string | null;
  /** Where the app is served (Vite's BASE_URL, ending in a slash). */
  base: string;
  /** Told once per path the map does not know (only when the map is not empty). */
  warn?: (message: string) => void;
}

export interface AssetUrlOptions {
  /** Always the game's own origin (files loaded by workers). */
  sameOrigin?: boolean;
}

export type AssetResolver = (path: string, opts?: AssetUrlOptions) => string;

/** `path` without a leading slash and with `.` and `..` segments applied (`assets/world/../instances/a.glb` -> `assets/instances/a.glb`). */
export function normalizeAssetPath(path: string): string {
  const out: string[] = [];
  for (const seg of path.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") out.pop();
    else out.push(seg);
  }
  return out.join("/");
}

/**
 * The content-addressed key of `path` on the bucket: the hash before the last
 * extension, or after the name when it has none. Must match hashedKey in
 * client/scripts/asset-map.mjs (the uploader's keys).
 */
export function hashedKey(path: string, hash: string): string {
  const slash = path.lastIndexOf("/");
  const dot = path.lastIndexOf(".");
  return dot > slash + 1 ? `${path.slice(0, dot)}.${hash}${path.slice(dot)}` : `${path}.${hash}`;
}

/** The bucket URL the server put in the page (null without one, or when it is not an http(s) URL). */
export function assetCdnFromPage(doc: Pick<Document, "querySelector"> | undefined = globalThis.document): string | null {
  const raw = doc?.querySelector<HTMLMetaElement>(`meta[name="${ASSET_CDN_META}"]`)?.content?.trim() ?? "";
  if (!/^https?:\/\/[^/]/i.test(raw)) return null;
  return raw.replace(/\/+$/, "");
}

/** A resolver over one map and one bucket (tests make their own; the game uses `assetUrl`). */
export function createAssetResolver(opts: AssetResolverOptions): AssetResolver {
  const base = opts.base.endsWith("/") ? opts.base : `${opts.base}/`;
  const known = Object.keys(opts.map).length > 0;
  const warned = new Set<string>();
  return (path, o) => {
    // Already a URL (data:, blob:, http(s):, protocol-relative): left alone.
    if (/^([a-z][a-z0-9+.-]*:|\/\/)/i.test(path)) return path;
    const logical = normalizeAssetPath(path);
    const hash = Object.hasOwn(opts.map, logical) ? opts.map[logical] : undefined;
    if (hash === undefined) {
      if (known && opts.warn && !warned.has(logical)) {
        warned.add(logical);
        opts.warn(`[assets] ${logical} is not in the asset map; loading it from the game server uncached`);
      }
      return `${base}${logical}`;
    }
    if (opts.cdn && !o?.sameOrigin) return `${opts.cdn}/${hashedKey(logical, hash)}`;
    return `${base}${logical}?v=${hash}`;
  };
}

let resolver: AssetResolver | null = null;

/** The URL of the game file at logical path `path` (see the top of this file). */
export function assetUrl(path: string, opts?: AssetUrlOptions): string {
  resolver ??= createAssetResolver({
    map: assetMap,
    cdn: assetCdnFromPage(),
    base: import.meta.env.BASE_URL,
    warn: (m) => console.warn(m),
  });
  return resolver(path, opts);
}

/**
 * Whether the build ships a file at `path`: true or false in a build, null
 * when nobody knows (development and tests, where the map is empty). Lets
 * code that probes for optional files skip a request bound to 404.
 */
export function assetShipped(path: string): boolean | null {
  if (Object.keys(assetMap).length === 0) return null;
  return assetHash(path) !== undefined;
}

/** The content hash the build recorded for `path`, or undefined (development, or not a shipped file). */
export function assetHash(path: string): string | undefined {
  const logical = normalizeAssetPath(path);
  return Object.hasOwn(assetMap, logical) ? assetMap[logical] : undefined;
}
