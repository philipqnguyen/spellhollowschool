// Uploads the game assets of a client build to an S3-compatible bucket
// (Cloudflare R2, AWS S3, MinIO...) by their content-addressed keys
// (docs/deploy.md, "Assets from a bucket"). The Kamal pre-deploy hook runs it
// after the build and before the new version goes live; prune-assets.mjs
// (the post-deploy hook) later deletes what no recent version uses.
//
//   node client/scripts/upload-assets.mjs [--dist client/dist] [--env-file FILE]
//        [--origin https://game.example.org] [--dry-run] [--concurrency 6]
//
// - Reads `<dist>/asset-map.json` (client/scripts/asset-map.mjs) and uploads
//   every file it lists, as `assets/world/cell_12.<hash>.glb`, that the bucket
//   does not have yet (one listing of the bucket first, then
//   `If-None-Match: *` on every PUT). Nothing is ever overwritten or deleted
//   here: old versions keep working for open tabs and rollbacks. The KTX2 decoder
//   (assets/ktx2/) is skipped: the client loads it from the game's origin.
// - Every object gets its Content-Type and `Cache-Control: public,
//   max-age=31536000, immutable`. Types that shrink (models, JSON, scripts,
//   wasm, text) are stored gzip-compressed with `Content-Encoding: gzip`,
//   since Cloudflare does not compress model/gltf-binary itself; browsers
//   decode it transparently. DEPLOY_ASSETS_GZIP=0 stores everything as is.
// - With DEPLOY_ASSETS_PUBLIC_URL, checks afterwards that a gzip object comes
//   back through the public URL decoded to the right bytes, with the CORS
//   header for the game's origin (--origin, else https://$DEPLOY_DOMAIN) and
//   the immutable Cache-Control; a failure fails the deploy
//   (DEPLOY_ASSETS_VERIFY=0 skips the check).
//
// Settings (the --env-file wins over the shell, as config/deploy.yml does):
//   DEPLOY_ASSETS_BUCKET              the bucket; unset: nothing to do (exit 0)
//   DEPLOY_ASSETS_S3_ENDPOINT         https://<account id>.r2.cloudflarestorage.com,
//                                     https://s3.<region>.amazonaws.com, http://127.0.0.1:9000
//   DEPLOY_ASSETS_S3_REGION           default auto (R2); the bucket's region elsewhere
//   DEPLOY_ASSETS_ACCESS_KEY_ID, DEPLOY_ASSETS_SECRET_ACCESS_KEY
//   DEPLOY_ASSETS_PUBLIC_URL          https://assets.example.org (for the check)
//   DEPLOY_ASSETS_GZIP, DEPLOY_ASSETS_VERIFY   default 1
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseEnv } from "node:util";
import { gzipSync } from "node:zlib";

import { ASSET_DIRS, hashedKey, MAP_FILE, parseMapFile, sameOriginOnly } from "./asset-map.mjs";
import { S3Client } from "./s3.mjs";

export const CACHE_CONTROL = "public, max-age=31536000, immutable";

const TYPES = {
  ".glb": "model/gltf-binary",
  ".gltf": "model/gltf+json",
  ".bin": "application/octet-stream",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".avif": "image/avif",
  ".ktx2": "image/ktx2",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".wasm": "application/wasm",
  ".mp3": "audio/mpeg",
  ".ogg": "audio/ogg",
  ".opus": "audio/ogg",
  ".wav": "audio/wav",
  ".m4a": "audio/mp4",
  ".txt": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".css": "text/css",
  ".html": "text/html; charset=utf-8",
};
/** Extensions worth storing gzip-compressed (images and audio are compressed already). */
const COMPRESSIBLE = new Set([".glb", ".gltf", ".bin", ".json", ".js", ".mjs", ".wasm", ".svg", ".txt", ".md", ".css", ".html"]);

export function contentType(path) {
  return TYPES[extname(path).toLowerCase()] ?? "application/octet-stream";
}

/** The object to store for `bytes` at `path`: the body and its headers (gzip when it saves at least a tenth). */
export function objectFor(path, bytes, { gzip = true } = {}) {
  const headers = { "content-type": contentType(path), "cache-control": CACHE_CONTROL };
  if (gzip && COMPRESSIBLE.has(extname(path).toLowerCase()) && bytes.length > 1024) {
    const gz = gzipSync(bytes, { level: 9 });
    if (gz.length <= bytes.length * 0.9) return { body: gz, headers: { ...headers, "content-encoding": "gzip" } };
  }
  return { body: bytes, headers };
}

/** The bucket objects of an asset map: `{ path, key, hash }`, same-origin files left out. */
export function plannedObjects(map) {
  return Object.entries(map)
    .filter(([path]) => !sameOriginOnly(path))
    .map(([path, hash]) => ({ path, hash, key: hashedKey(path, hash) }));
}

/** Settings: the env file's values over `env` (blank counts as unset). */
export function loadSettings(env, envFile) {
  const merged = { ...env };
  if (envFile) {
    if (!existsSync(envFile)) throw new Error(`${envFile} does not exist`);
    for (const [k, v] of Object.entries(parseEnv(readFileSync(envFile, "utf8")))) if (v !== "") merged[k] = v;
  }
  const get = (name, fallback = "") => (merged[name] ?? "").trim() || fallback;
  return {
    bucket: get("DEPLOY_ASSETS_BUCKET"),
    endpoint: get("DEPLOY_ASSETS_S3_ENDPOINT"),
    region: get("DEPLOY_ASSETS_S3_REGION", "auto"),
    accessKeyId: get("DEPLOY_ASSETS_ACCESS_KEY_ID"),
    secretAccessKey: get("DEPLOY_ASSETS_SECRET_ACCESS_KEY"),
    publicUrl: get("DEPLOY_ASSETS_PUBLIC_URL").replace(/\/+$/, ""),
    gzip: get("DEPLOY_ASSETS_GZIP", "1") !== "0",
    verify: get("DEPLOY_ASSETS_VERIFY", "1") !== "0",
    prune: get("DEPLOY_ASSETS_PRUNE", "1") !== "0",
    pruneKeep: get("DEPLOY_ASSETS_PRUNE_KEEP"),
    domain: get("DEPLOY_DOMAIN"),
  };
}

/** Runs `fn` over `items`, `limit` at a time. */
export async function pool(items, limit, fn) {
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      await fn(items[i], i);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
}

export async function retry(what, fn, log, tries = 4) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      const status = e?.status ?? 0;
      const transient = status === 0 || status === 429 || status >= 500;
      if (!transient || attempt >= tries) throw e;
      log(`[assets] ${what}: ${e.message}; retrying (${attempt}/${tries - 1})`);
      await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
    }
  }
}

const mb = (n) => `${(n / 1e6).toFixed(1)} MB`;

/**
 * Uploads what the bucket lacks. Returns `{ planned, present, uploaded, bytes, sent }`.
 * `client` is an S3Client (or a fake in tests).
 */
export async function uploadAssets({ dist, client, gzip = true, concurrency = 6, dryRun = false, log = console.log }) {
  const mapPath = join(dist, MAP_FILE);
  if (!existsSync(mapPath)) throw new Error(`${mapPath} is missing: build the client first (npm run build -w client)`);
  const objects = plannedObjects(parseMapFile(readFileSync(mapPath, "utf8")));
  const present = new Set();
  for (const dir of ASSET_DIRS) for (const k of await retry(`listing ${dir}/`, () => client.listKeys(`${dir}/`), log)) present.add(k);
  const todo = objects.filter((o) => !present.has(o.key));
  log(`[assets] ${objects.length} objects in the asset map, ${objects.length - todo.length} already in the bucket, ${todo.length} to upload`);
  const result = { planned: objects.length, present: objects.length - todo.length, uploaded: 0, bytes: 0, sent: 0, gzipped: [] };
  if (dryRun) {
    for (const o of todo.slice(0, 20)) log(`[assets]   would upload ${o.key}`);
    if (todo.length > 20) log(`[assets]   ... and ${todo.length - 20} more`);
    return result;
  }
  let done = 0;
  await pool(todo, concurrency, async (o) => {
    const bytes = readFileSync(join(dist, o.path));
    const actual = createHash("sha256").update(bytes).digest("hex").slice(0, o.hash.length);
    if (actual !== o.hash) throw new Error(`${o.path} changed since the build (hash ${actual}, asset map ${o.hash}): build the client again`);
    const { body, headers } = objectFor(o.path, bytes, { gzip });
    const outcome = await retry(o.key, () => client.putNew(o.key, body, headers), log);
    if (outcome === "uploaded") {
      result.uploaded++;
      result.bytes += bytes.length;
      result.sent += body.length;
      if (headers["content-encoding"] === "gzip") result.gzipped.push(o);
    }
    done++;
    if (done % 50 === 0 || done === todo.length) log(`[assets]   ${done}/${todo.length} (${mb(result.sent)} sent)`);
  });
  log(`[assets] uploaded ${result.uploaded} objects (${mb(result.bytes)} of files, ${mb(result.sent)} stored)`);
  return result;
}

/**
 * Checks one object through the public URL as a browser on `origin` would
 * load it: 200, the right bytes after decoding, the CORS header, the
 * immutable Cache-Control. Returns a list of problems (empty: fine).
 */
export async function verifyPublic({ publicUrl, origin, object, fetchFn = globalThis.fetch }) {
  const url = `${publicUrl}/${object.key}`;
  const problems = [];
  let res;
  try {
    res = await fetchFn(url, { headers: { origin, "accept-encoding": "gzip, deflate, br" } });
  } catch (e) {
    return [`${url}: ${e.message}`];
  }
  if (!res.ok) return [`${url}: HTTP ${res.status}`];
  // fetch decodes one layer of Content-Encoding: an object compressed twice on the way comes out still gzipped.
  const body = Buffer.from(await res.arrayBuffer());
  const hash = createHash("sha256").update(body).digest("hex").slice(0, object.hash.length);
  if (hash !== object.hash) {
    const gz = body[0] === 0x1f && body[1] === 0x8b;
    problems.push(`${url}: the body does not match the file${gz ? " (it arrives gzipped twice: set DEPLOY_ASSETS_GZIP=0 for new uploads)" : ""}`);
  }
  const acao = res.headers.get("access-control-allow-origin");
  if (acao !== "*" && acao !== origin) problems.push(`${url}: Access-Control-Allow-Origin is ${acao ?? "missing"}, not ${origin} (the bucket's CORS policy; docs/deploy.md)`);
  const cc = res.headers.get("cache-control") ?? "";
  if (!/immutable/.test(cc)) problems.push(`${url}: Cache-Control is "${cc}", not "${CACHE_CONTROL}"`);
  return problems;
}

function arg(argv, name) {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
}

async function main(argv) {
  const client = resolve(fileURLToPath(import.meta.url), "..", "..");
  const dist = resolve(arg(argv, "--dist") ?? join(client, "dist"));
  const envFile = arg(argv, "--env-file");
  const s = loadSettings(process.env, envFile);
  if (!s.bucket) {
    console.log("[assets] no asset bucket configured (DEPLOY_ASSETS_BUCKET): the game server serves the assets");
    return 0;
  }
  const missing = ["DEPLOY_ASSETS_S3_ENDPOINT", "DEPLOY_ASSETS_ACCESS_KEY_ID", "DEPLOY_ASSETS_SECRET_ACCESS_KEY"].filter(
    (n) => !{ DEPLOY_ASSETS_S3_ENDPOINT: s.endpoint, DEPLOY_ASSETS_ACCESS_KEY_ID: s.accessKeyId, DEPLOY_ASSETS_SECRET_ACCESS_KEY: s.secretAccessKey }[n],
  );
  if (missing.length) {
    console.error(`[assets] DEPLOY_ASSETS_BUCKET is set but ${missing.join(", ")} ${missing.length > 1 ? "are" : "is"} not (docs/deploy.md, "Assets from a bucket")`);
    return 1;
  }
  const s3 = new S3Client({ endpoint: s.endpoint, bucket: s.bucket, region: s.region, accessKeyId: s.accessKeyId, secretAccessKey: s.secretAccessKey });
  console.log(`[assets] uploading ${dist} to ${s.bucket} at ${s.endpoint}${s.gzip ? "" : " (gzip off)"}`);
  const result = await uploadAssets({
    dist,
    client: s3,
    gzip: s.gzip,
    dryRun: argv.includes("--dry-run"),
    concurrency: Number(arg(argv, "--concurrency") ?? 6),
  });
  if (argv.includes("--dry-run")) return 0;
  if (!s.publicUrl || !s.verify) {
    if (!s.publicUrl) console.log("[assets] DEPLOY_ASSETS_PUBLIC_URL is not set: not checking the public URL");
    return 0;
  }
  const origin = arg(argv, "--origin") ?? (s.domain ? `https://${s.domain}` : null);
  if (!origin) {
    console.log("[assets] no game origin (--origin or DEPLOY_DOMAIN): not checking the public URL");
    return 0;
  }
  // A gzip object (the case that can break on the way), else any object.
  const objects = plannedObjects(parseMapFile(readFileSync(join(dist, MAP_FILE), "utf8")));
  const sample = result.gzipped[0] ?? objects.find((o) => o.path.endsWith(".glb")) ?? objects[0];
  if (!sample) return 0;
  const problems = await verifyPublic({ publicUrl: s.publicUrl, origin, object: sample });
  if (problems.length) {
    console.error(["[assets] the public URL does not serve the assets right:", ...problems.map((p) => `  ${p}`), "  (DEPLOY_ASSETS_VERIFY=0 skips this check)"].join("\n"));
    return 1;
  }
  console.log(`[assets] ${s.publicUrl} serves ${sample.key} right for ${origin}`);
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = await main(process.argv.slice(2));
  } catch (e) {
    console.error(`[assets] ${e.message}`);
    process.exitCode = 1;
  }
}
