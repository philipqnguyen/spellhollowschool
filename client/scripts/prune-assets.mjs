// Deletes from the asset bucket the objects no recent version of the game
// uses (docs/deploy.md, "Assets from a bucket"). The Kamal post-deploy hook
// runs it once the new version is live.
//
//   node client/scripts/prune-assets.mjs --version <id> [--dist client/dist]
//        [--env-file FILE] [--keep 3] [--dry-run] [--concurrency 6]
//
// - Records this deploy: `<dist>/asset-map.json` is stored in the bucket as
//   `deploys/<UTC time>-<version>.json` (a redeploy of the same version
//   replaces its record). Only deploys that went live are recorded, since
//   the hook runs after the switch.
// - Keeps the newest `keep` records (DEPLOY_ASSETS_PRUNE_KEEP, default 3)
//   and every object any of them names; deletes every other object under
//   assets/ and audio/, and the older records. So the live version, and the
//   versions before it that a rollback can go back to, keep their files.
// - Until `keep` deploys are recorded it deletes nothing (the bucket's older
//   objects belong to deploys made before recording began). A record that
//   cannot be read also stops it: nothing is deleted on a guess.
//
// Settings: those of upload-assets.mjs, plus
//   DEPLOY_ASSETS_PRUNE        default 1; 0 turns pruning off
//   DEPLOY_ASSETS_PRUNE_KEEP   default 3: how many recent versions keep their files
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ASSET_DIRS, MAP_FILE, parseMapFile } from "./asset-map.mjs";
import { S3Client } from "./s3.mjs";
import { loadSettings, plannedObjects, pool, retry } from "./upload-assets.mjs";

export const RECORD_PREFIX = "deploys/";
export const DEFAULT_KEEP = 3;

const RECORD = /^deploys\/(\d{8}T\d{6}Z)-([A-Za-z0-9._-]+)\.json$/;

/** `deploys/20261112T031500Z-<version>.json`: sorts by time. */
export function recordKey(version, date) {
  if (!/^[A-Za-z0-9._-]+$/.test(version)) throw new Error(`not a usable version id: ${JSON.stringify(version)}`);
  const stamp = date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  return `${RECORD_PREFIX}${stamp}-${version}.json`;
}

/** `{ key, stamp, version }` of a record key, or null. */
export function parseRecordKey(key) {
  const m = RECORD.exec(key);
  return m ? { key, stamp: m[1], version: m[2] } : null;
}

/**
 * Records this deploy's asset map, then deletes what the newest `keep`
 * records do not name. `client` is an S3Client (or a fake in tests).
 * Returns `{ recorded, kept, deleted, records, skipped }` (`skipped`: why
 * nothing was deleted, else null).
 */
export async function pruneAssets({ dist, client, version, keep = DEFAULT_KEEP, now = new Date(), concurrency = 6, dryRun = false, log = console.log }) {
  if (!Number.isInteger(keep) || keep < 1) throw new Error(`keep must be a whole number of versions, at least 1 (not ${keep})`);
  const mapPath = join(dist, MAP_FILE);
  if (!existsSync(mapPath)) throw new Error(`${mapPath} is missing: build the client first (npm run build -w client)`);
  const mapText = readFileSync(mapPath, "utf8");
  const current = parseMapFile(mapText);

  const record = recordKey(version, now);
  const existing = [...(await retry(`listing ${RECORD_PREFIX}`, () => client.listKeys(RECORD_PREFIX), log))].map(parseRecordKey).filter(Boolean);
  const replaced = existing.filter((r) => r.version === version);
  if (!dryRun) {
    await retry(record, () => client.put(record, Buffer.from(mapText), { "content-type": "application/json", "cache-control": "no-store" }), log);
    for (const r of replaced) await retry(`deleting ${r.key}`, () => client.delete(r.key), log);
  }
  log(`[prune] recorded ${version} as ${record}${dryRun ? " (dry run: not written)" : ""}`);

  const records = [parseRecordKey(record), ...existing.filter((r) => r.version !== version)].sort((a, b) => (a.stamp < b.stamp ? 1 : a.stamp > b.stamp ? -1 : 0));
  const kept = records.slice(0, keep);
  const old = records.slice(keep);
  const result = { recorded: record, kept: kept.map((r) => r.version), deleted: 0, records: 0, skipped: null };
  if (kept.length < keep) {
    result.skipped = `${kept.length} of ${keep} versions recorded`;
    log(`[prune] ${result.skipped}: nothing deleted until ${keep} deploys are recorded`);
    return result;
  }

  const needed = new Set(plannedObjects(current).map((o) => o.key));
  for (const r of kept) {
    if (r.key === record) continue;
    let files;
    try {
      const text = await retry(`reading ${r.key}`, () => client.getText(r.key), log);
      if (text === null) throw new Error("it is gone");
      files = parseMapFile(text);
    } catch (e) {
      result.skipped = `${r.key} could not be read (${e.message})`;
      log(`[prune] ${result.skipped}: nothing deleted`);
      return result;
    }
    for (const o of plannedObjects(files)) needed.add(o.key);
  }

  const stale = [];
  for (const dir of ASSET_DIRS) for (const k of await retry(`listing ${dir}/`, () => client.listKeys(`${dir}/`), log)) if (!needed.has(k)) stale.push(k);
  log(`[prune] keeping the files of ${kept.map((r) => r.version).join(", ")} (${needed.size} objects); ${stale.length} objects and ${old.length} older records to delete`);
  if (dryRun) {
    for (const k of stale.slice(0, 20)) log(`[prune]   would delete ${k}`);
    if (stale.length > 20) log(`[prune]   ... and ${stale.length - 20} more`);
    return result;
  }
  await pool(stale, concurrency, async (k) => {
    await retry(`deleting ${k}`, () => client.delete(k), log);
    result.deleted++;
  });
  for (const r of old) {
    await retry(`deleting ${r.key}`, () => client.delete(r.key), log);
    result.records++;
  }
  log(`[prune] deleted ${result.deleted} objects and ${result.records} older records`);
  return result;
}

function arg(argv, name) {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
}

async function main(argv) {
  const clientDir = resolve(fileURLToPath(import.meta.url), "..", "..");
  const dist = resolve(arg(argv, "--dist") ?? join(clientDir, "dist"));
  const envFile = arg(argv, "--env-file");
  const s = loadSettings(process.env, envFile);
  if (!s.bucket) {
    console.log("[prune] no asset bucket configured (DEPLOY_ASSETS_BUCKET): nothing to prune");
    return 0;
  }
  if (!s.prune) {
    console.log("[prune] DEPLOY_ASSETS_PRUNE=0: not pruning");
    return 0;
  }
  const keep = Number(arg(argv, "--keep") ?? (s.pruneKeep || DEFAULT_KEEP));
  const version = arg(argv, "--version") ?? process.env.KAMAL_VERSION;
  if (!version) {
    console.error("[prune] no version: pass --version (the Kamal hook passes KAMAL_VERSION)");
    return 1;
  }
  if (!s.endpoint || !s.accessKeyId || !s.secretAccessKey) {
    console.error("[prune] DEPLOY_ASSETS_BUCKET is set but the endpoint or the access keys are not (docs/deploy.md, \"Assets from a bucket\")");
    return 1;
  }
  const s3 = new S3Client({ endpoint: s.endpoint, bucket: s.bucket, region: s.region, accessKeyId: s.accessKeyId, secretAccessKey: s.secretAccessKey });
  await pruneAssets({ dist, client: s3, version, keep, dryRun: argv.includes("--dry-run"), concurrency: Number(arg(argv, "--concurrency") ?? 6) });
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = await main(process.argv.slice(2));
  } catch (e) {
    console.error(`[prune] ${e.message}`);
    process.exitCode = 1;
  }
}
