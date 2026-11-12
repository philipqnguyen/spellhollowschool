// The asset map: every file the client loads from client/public/assets/ and
// client/public/audio/, by its logical path (`assets/world/manifest.json`), to
// a short hash of its content. docs/deploy.md, "Assets from a bucket".
//
// - The Vite plugin (assetMapPlugin, client/vite.config.ts) compiles the map
//   into the bundle as the module `virtual:shs-asset-map` (the bundle is
//   itself content-hashed, so the map is cached with it) and writes it as
//   `asset-map.json` into client/dist, for the server and the uploader. In
//   development the module is an empty map: the dev server serves the files
//   as they are, and they change while Blender rebuilds them.
// - The client (src/assets.ts) turns a logical path into a URL: on the asset
//   bucket the content-addressed key (`hashedKey`, immutable), else the game
//   server's own file with `?v=<hash>`, which the server caches for a year
//   only when the hash matches its file.
// - client/scripts/upload-assets.mjs uploads every key of the map that the
//   bucket does not have yet.
//
// Importing this module has no side effects. Run directly:
//   node client/scripts/asset-map.mjs [--public DIR]   (prints the map's size)
import { createHash } from "node:crypto";
import { createReadStream, existsSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** The folders under client/public (and so client/dist) whose files the map covers. */
export const ASSET_DIRS = ["assets", "audio"];
/** Hex digits of sha256 kept: 64 bits, so two versions of one file never share a key. */
export const HASH_HEX = 16;
/** The map's file in client/dist (and the server's static folder). */
export const MAP_FILE = "asset-map.json";
export const MAP_VERSION = 1;
/**
 * Loaded from the game's own origin only, never the bucket: the KTX2 decoder
 * runs in workers, which cannot start from another origin. The uploader skips
 * it and the image keeps it.
 */
export const SAME_ORIGIN_PREFIXES = ["assets/ktx2/"];
export const VIRTUAL_ID = "virtual:shs-asset-map";

/** Files that are never assets: OS and editor droppings. */
const IGNORED = /^(\.DS_Store|Thumbs\.db|\._.*)$/;

/** Every file under `dir`, as paths relative to `root` with forward slashes, sorted. */
function walk(root, dir, out) {
  for (const entry of readdirSync(join(root, dir), { withFileTypes: true })) {
    if (IGNORED.test(entry.name)) continue;
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) walk(root, rel, out);
    else if (entry.isFile()) out.push(rel);
  }
  return out;
}

/** sha256 of a file, hex. */
export function hashFile(path) {
  return new Promise((ok, fail) => {
    const h = createHash("sha256");
    createReadStream(path)
      .on("error", fail)
      .on("data", (chunk) => h.update(chunk))
      .on("end", () => ok(h.digest("hex")));
  });
}

/**
 * The map of the folder `publicDir` (client/public or client/dist): logical
 * path to the first HASH_HEX hex digits of its sha256, sorted by path. Folders
 * of ASSET_DIRS that do not exist are skipped.
 */
export async function buildAssetMap(publicDir, dirs = ASSET_DIRS) {
  const files = [];
  for (const d of dirs) if (existsSync(join(publicDir, d))) walk(publicDir, d, files);
  files.sort();
  const hashes = [];
  // A few files at a time: hashing is I/O bound, and 700 open streams at once is rude.
  for (let i = 0; i < files.length; i += 16) {
    const batch = files.slice(i, i + 16);
    hashes.push(...(await Promise.all(batch.map((f) => hashFile(join(publicDir, f))))));
  }
  const map = {};
  files.forEach((f, i) => (map[f] = hashes[i].slice(0, HASH_HEX)));
  return map;
}

/**
 * The content-addressed key of `path`: the hash before the last extension
 * (`assets/world/cell_12.glb` -> `assets/world/cell_12.3f9a1c7e5b2d4f60.glb`),
 * or after the name when it has none. Must match hashedKey in src/assets.ts.
 */
export function hashedKey(path, hash) {
  const slash = path.lastIndexOf("/");
  const dot = path.lastIndexOf(".");
  return dot > slash + 1 ? `${path.slice(0, dot)}.${hash}${path.slice(dot)}` : `${path}.${hash}`;
}

/** Whether `path` must load from the game's origin (SAME_ORIGIN_PREFIXES). */
export function sameOriginOnly(path) {
  return SAME_ORIGIN_PREFIXES.some((p) => path.startsWith(p));
}

/** The JSON written to client/dist/asset-map.json. */
export function mapFileJson(map) {
  return `${JSON.stringify({ version: MAP_VERSION, hashLength: HASH_HEX, sameOrigin: SAME_ORIGIN_PREFIXES, files: map }, null, 1)}\n`;
}

/** Parses asset-map.json (throws on another version or a malformed file). */
export function parseMapFile(text) {
  const data = JSON.parse(text);
  if (data?.version !== MAP_VERSION || typeof data.files !== "object" || data.files === null) {
    throw new Error(`${MAP_FILE}: not a version ${MAP_VERSION} asset map`);
  }
  for (const [path, hash] of Object.entries(data.files)) {
    if (typeof hash !== "string" || !/^[0-9a-f]+$/.test(hash) || path.startsWith("/") || path.split("/").includes("..")) {
      throw new Error(`${MAP_FILE}: bad entry ${JSON.stringify(path)}`);
    }
  }
  return data.files;
}

/**
 * The Vite plugin: `virtual:shs-asset-map` (the map of `publicDir` in a
 * build, empty when serving) and `asset-map.json` in the build's output.
 */
export function assetMapPlugin({ publicDir } = {}) {
  const resolvedId = `\0${VIRTUAL_ID}`;
  let build = false;
  let dir = publicDir;
  let map = null;
  return {
    name: "shs-asset-map",
    configResolved(config) {
      build = config.command === "build" && !config.build.lib;
      dir ??= config.publicDir;
    },
    async buildStart() {
      map = build ? await buildAssetMap(dir) : {};
      if (build) this.info?.(`asset map: ${Object.keys(map).length} files`);
    },
    resolveId(id) {
      return id === VIRTUAL_ID ? resolvedId : null;
    },
    async load(id) {
      if (id !== resolvedId) return null;
      map ??= build ? await buildAssetMap(dir) : {};
      return `export default ${JSON.stringify(map)};\n`;
    },
    generateBundle() {
      if (!build || !map) return;
      this.emitFile({ type: "asset", fileName: MAP_FILE, source: mapFileJson(map) });
    },
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const i = process.argv.indexOf("--public");
  const dir = i > 0 ? resolve(process.argv[i + 1]) : join(dirname(fileURLToPath(import.meta.url)), "..", "public");
  const map = await buildAssetMap(dir);
  console.log(`${Object.keys(map).length} files in the asset map of ${dir}`);
}
