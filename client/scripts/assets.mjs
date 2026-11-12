// The asset fingerprint, published asset releases, and the tool probes that
// client/scripts/prepare.mjs and tools/assets/publish.sh share
// (tools/blender/README.md, "Asset releases").
//
// - Fingerprint: a hash of every input the Blender build reads
//   (asset-steps.mjs fingerprintRoots, by content: every step's listed
//   sources, the Python modules its script runs and the data they name, the
//   art sources, the pins, asset-steps.mjs), written as
//   client/public/assets/assets-fingerprint.txt after a complete build or
//   fetch. Same sources, same fingerprint, on any machine.
// - Release: `assets-<fingerprint>.tar.gz` (client/public/assets without the
//   KTX2 decoder, which prepare builds from node_modules, with the credits
//   and licence of credits.mjs, which pack writes) and its `.sha256`,
//   at `<base>/assets-<fingerprint>/`: a GitHub Release tagged
//   `assets-<fingerprint>` (base https://github.com/<owner>/<repo>/releases/download),
//   or any folder or web server laid out the same way (SHS_ASSETS_URL).
// - planAssets: what prepare does (nothing, fetch, build, fall back).
//
// Importing this module has no side effects. Run directly it is a small CLI:
//   node client/scripts/assets.mjs fingerprint [--list] [--repo DIR]
//   node client/scripts/assets.mjs pack --out DIR [--assets DIR] [--repo DIR]
//   node client/scripts/assets.mjs tools [--strict]     (Blender, MPFB, basisu vs the pins)
//   node client/scripts/assets.mjs release-url [--repo DIR]
//   node client/scripts/assets.mjs release-repo [--repo DIR]   (owner/repo that publish.sh uploads to)
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  accessSync,
  constants,
  createReadStream,
  createWriteStream,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  utimesSync,
  writeFileSync,
} from "node:fs";
import { delimiter, dirname, join, resolve, sep } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

import { BASISU_PIN, BLENDER_PIN, fingerprintRoots, MPFB_PIN } from "./asset-steps.mjs";
import { ASSET_CREDIT_FILES, writeAssetCredits } from "./credits.mjs";

export const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const FINGERPRINT_FILE = "assets-fingerprint.txt";
/** Bump to change every fingerprint (a new archive layout, a new hashing rule). */
export const FINGERPRINT_FORMAT = "shs-assets 1";
/** Left out of releases: prepare builds it from node_modules without Blender. */
export const UNPACKED_DIRS = ["ktx2"];
export const MODES = ["auto", "fetch", "build"];

// ---------------------------------------------------------------------------
// Fingerprint

/** Path segments under which nothing changes an output (tests, caches) or that are generated. */
const SKIP_SEGMENTS = new Set(["tests", "__pycache__", "node_modules", "target", "renders"]);
const SKIP_SUFFIXES = [".md", ".pyc", ".log", ".blend", ".blend1", ".blend2"];

/**
 * Whether a repo-relative (posix) path counts as a build input: not under a
 * dot folder or SKIP_SEGMENTS, not a doc, licence, cache or a .blend the build
 * writes back under art/.
 */
export function isFingerprintInput(rel) {
  const parts = rel.split("/");
  if (parts.some((p) => p.startsWith(".") || SKIP_SEGMENTS.has(p))) return false;
  const base = parts[parts.length - 1];
  if (/^(LICENSE|COPYING|CREDITS)/i.test(base)) return false;
  return !SKIP_SUFFIXES.some((s) => base.endsWith(s));
}

function walk(repo, rel, out) {
  const abs = join(repo, rel);
  let stat;
  try {
    stat = statSync(abs);
  } catch {
    return;
  }
  if (!stat.isDirectory()) {
    out.push(rel);
    return;
  }
  for (const entry of readdirSync(abs)) walk(repo, rel ? `${rel}/${entry}` : entry, out);
}

/**
 * The input files under `roots` (repo-relative posix paths, sorted, no
 * duplicates). In a git checkout gitignored files are left out (`git
 * ls-files --cached --others --exclude-standard`), so the generated .blend
 * scenes and renders under art/ never count; elsewhere (an unpacked source
 * archive) the folders are walked. isFingerprintInput filters both.
 */
export function listInputs(repo = REPO, roots = fingerprintRoots(repo)) {
  let files = null;
  const git = spawnSync("git", ["-C", repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ...roots], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  if (!git.error && git.status === 0 && spawnSync("git", ["-C", repo, "rev-parse", "--show-prefix"], { encoding: "utf8" }).stdout.trim() === "") {
    // --cached lists files deleted from the working tree too: only what exists counts.
    files = git.stdout.split("\0").filter((f) => f && existsSync(join(repo, f)));
  } else {
    files = [];
    for (const root of roots) walk(repo, root, files);
  }
  return [...new Set(files.filter(isFingerprintInput))].sort();
}

/**
 * sha256 of a file's content. Text (no NUL byte) is hashed with CRLF read as
 * LF, so a Windows checkout with core.autocrlf gets the same fingerprint.
 */
export function hashContent(buf) {
  const text = !buf.includes(0);
  const data = text && buf.includes(13) ? Buffer.from(buf.toString("latin1").replace(/\r\n/g, "\n"), "latin1") : buf;
  return createHash("sha256").update(data).digest("hex");
}

/**
 * The fingerprint of `files` (repo-relative): the first 16 hex digits of a
 * sha256 over the format line and each file's path and content hash, in path
 * order. Returns { fingerprint, files: [{ path, sha256 }] }.
 */
export function computeFingerprint(repo = REPO, files = listInputs(repo)) {
  const sorted = [...files].sort();
  const h = createHash("sha256").update(`${FINGERPRINT_FORMAT}\n`);
  const listed = [];
  for (const path of sorted) {
    const sha256 = hashContent(readFileSync(join(repo, path)));
    listed.push({ path, sha256 });
    h.update(`${path}\0${sha256}\n`);
  }
  return { fingerprint: h.digest("hex").slice(0, 16), files: listed };
}

/** The fingerprint client/public/assets was last built or fetched for, or null. */
export function readFingerprint(assetsDir) {
  try {
    const fp = readFileSync(join(assetsDir, FINGERPRINT_FILE), "utf8").trim();
    return /^[0-9a-f]{16}$/.test(fp) ? fp : null;
  } catch {
    return null;
  }
}

export function writeFingerprint(assetsDir, fingerprint) {
  mkdirSync(assetsDir, { recursive: true });
  writeFileSync(join(assetsDir, FINGERPRINT_FILE), `${fingerprint}\n`);
}

export function clearFingerprint(assetsDir) {
  rmSync(join(assetsDir, FINGERPRINT_FILE), { force: true });
}

// ---------------------------------------------------------------------------
// What to do

/** SHS_ASSETS: auto (default), fetch or build; throws on anything else. */
export function parseMode(value) {
  const mode = (value ?? "").trim().toLowerCase() || "auto";
  if (!MODES.includes(mode)) throw new Error(`SHS_ASSETS must be one of ${MODES.join(", ")}, not ${JSON.stringify(value)}`);
  return mode;
}

/**
 * What prepare does with client/public/assets. `current`: the fingerprint
 * file matches this checkout and every step's output exists.
 * Returns { attempts, onFail }: the attempts to make in order until one
 * succeeds ("fetch": download the release for this fingerprint; "build": run
 * the stale steps with the pinned Blender), and what happens when none does
 * ("fallback": say why, keep what is there, the client draws greybox
 * stand-ins for the rest; "error": exit non-zero).
 *  - auto:  current -> nothing; else fetch, then build, then fallback.
 *  - fetch: current -> nothing; else fetch, else error.
 *  - build: always the (incremental) build, which does nothing when no step
 *           is stale; a failure is an error.
 */
export function planAssets({ mode, current }) {
  if (mode === "build") return { attempts: ["build"], onFail: "error" };
  if (current) return { attempts: [], onFail: null };
  if (mode === "fetch") return { attempts: ["fetch"], onFail: "error" };
  return { attempts: ["fetch", "build"], onFail: "fallback" };
}

// ---------------------------------------------------------------------------
// Where releases live

/** "https://github.com/<owner>/<repo>/releases/download" for a GitHub repository URL or shorthand, else null. */
export function githubReleaseBase(repository) {
  if (!repository) return null;
  const url = (typeof repository === "string" ? repository : repository.url ?? "").trim();
  let m = /github\.com[:/]([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?(?:[#?].*)?$/.exec(url);
  if (!m) m = /^(?:github:)?([\w.-]+)\/([\w.-]+)$/.exec(url);
  return m ? `https://github.com/${m[1]}/${m[2]}/releases/download` : null;
}

/** A SHS_ASSETS_URL value as a URL string: http(s) and file URLs as they are, a path as a file URL. */
function normalizeBase(value, cwd) {
  if (/^(https?|file):\/\//.test(value)) return value.replace(/\/+$/, "");
  return pathToFileURL(resolve(cwd, value)).href.replace(/\/+$/, "");
}

/**
 * Where to look for asset releases: { base, source } or { base: null, why }.
 * SHS_ASSETS_URL first (empty turns fetching off), then package.json
 * "repository", then the `origin` remote, when either is on GitHub.
 */
export function releaseBase({ env = process.env, repo = REPO, cwd = process.cwd() } = {}) {
  if (env.SHS_ASSETS_URL !== undefined) {
    const v = env.SHS_ASSETS_URL.trim();
    if (!v) return { base: null, why: "SHS_ASSETS_URL is empty (fetching is off)" };
    return { base: normalizeBase(v, cwd), source: "SHS_ASSETS_URL" };
  }
  try {
    const pkg = JSON.parse(readFileSync(join(repo, "package.json"), "utf8"));
    const base = githubReleaseBase(pkg.repository);
    if (base) return { base, source: 'package.json "repository"' };
  } catch {
    // no package.json: try the remote
  }
  const origin = spawnSync("git", ["-C", repo, "remote", "get-url", "origin"], { encoding: "utf8" });
  if (!origin.error && origin.status === 0) {
    const base = githubReleaseBase(origin.stdout.trim());
    if (base) return { base, source: "the origin remote" };
  }
  return {
    base: null,
    why: 'no release location: set SHS_ASSETS_URL, or "repository" in package.json, or a GitHub origin remote',
  };
}

/**
 * The GitHub repository ("owner/repo") releases are published to: the one
 * releaseBase finds from package.json "repository" or the origin remote (not
 * SHS_ASSETS_URL, which only redirects downloads), so publish.sh uploads
 * where prepare looks. { repo } or { repo: null, why }.
 */
export function releaseRepo({ env = process.env, repo = REPO } = {}) {
  const { SHS_ASSETS_URL: _ignored, ...rest } = env;
  const r = releaseBase({ env: rest, repo });
  const m = r.base ? /^https:\/\/github\.com\/([^/]+\/[^/]+)\/releases\/download$/.exec(r.base) : null;
  return m ? { repo: m[1], source: r.source } : { repo: null, why: r.why ?? `${r.base} is not a GitHub repository` };
}

/** The release names and URLs for a fingerprint under `base`. */
export function releaseUrls(base, fingerprint) {
  const tag = `assets-${fingerprint}`;
  const file = `${tag}.tar.gz`;
  const dir = `${base.replace(/\/+$/, "")}/${tag}`;
  return { tag, file, archive: `${dir}/${file}`, checksum: `${dir}/${file}.sha256` };
}

// ---------------------------------------------------------------------------
// Download, verify, install

class NotFound extends Error {}

function sha256Stream() {
  const hash = createHash("sha256");
  const t = new Transform({
    transform(chunk, _enc, cb) {
      hash.update(chunk);
      cb(null, chunk);
    },
  });
  t.digest = () => hash.digest("hex");
  return t;
}

/** How long a download may go without receiving anything before it is given up (SHS_ASSETS_IDLE_SECONDS). */
export const IDLE_TIMEOUT_MS = (Number(process.env.SHS_ASSETS_IDLE_SECONDS) || 60) * 1000;

/**
 * Copies `url` (http(s) or file) to `dest`; returns its sha256. Throws
 * NotFound on a 404 or a missing file. An HTTP download that receives
 * nothing (no response, no data) for `idleMs` is aborted with an error, so a
 * stalled connection cannot hang prepare.
 */
export async function download(url, dest, { log = () => {}, idleMs = IDLE_TIMEOUT_MS } = {}) {
  mkdirSync(dirname(dest), { recursive: true });
  const hash = sha256Stream();
  if (url.startsWith("file:")) {
    const path = fileURLToPath(url);
    if (!existsSync(path)) throw new NotFound(`${url} does not exist`);
    await pipeline(createReadStream(path), hash, createWriteStream(dest));
    return hash.digest();
  }
  const abort = new AbortController();
  let timer = null;
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => abort.abort(new Error(`nothing received for ${Math.round(idleMs / 1000)} s; the download stalled`)), idleMs);
  };
  arm();
  try {
    const res = await fetch(url, { redirect: "follow", signal: abort.signal });
    if (res.status === 404) throw new NotFound(`${url}: 404 Not Found`);
    if (!res.ok || !res.body) throw new Error(`${url}: HTTP ${res.status} ${res.statusText}`);
    const total = Number(res.headers.get("content-length")) || 0;
    let seen = 0;
    let shown = 0;
    const progress = new Transform({
      transform(chunk, _enc, cb) {
        arm();
        seen += chunk.length;
        if (total && seen / total >= shown + 0.1) {
          shown = Math.floor((seen / total) * 10) / 10;
          log(`  ${Math.round(shown * 100)}% of ${(total / 1e6).toFixed(0)} MB`);
        }
        cb(null, chunk);
      },
    });
    await pipeline(Readable.fromWeb(res.body), progress, hash, createWriteStream(dest), { signal: abort.signal });
    return hash.digest();
  } catch (e) {
    if (abort.signal.aborted && !(e instanceof NotFound)) throw abort.signal.reason;
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

async function readText(url) {
  if (url.startsWith("file:")) {
    const path = fileURLToPath(url);
    if (!existsSync(path)) throw new NotFound(`${url} does not exist`);
    return readFileSync(path, "utf8");
  }
  const res = await fetch(url, { redirect: "follow", signal: AbortSignal.timeout(30_000) });
  if (res.status === 404) throw new NotFound(`${url}: 404 Not Found`);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status} ${res.statusText}`);
  return res.text();
}

/**
 * Downloads the release for `fingerprint` into `workDir` and checks it
 * against its published sha256. Returns { ok: true, archive, sha256 } or
 * { ok: false, missing, reason } (missing: there is no such release).
 */
export async function fetchRelease(base, fingerprint, workDir, { log = () => {}, idleMs = IDLE_TIMEOUT_MS } = {}) {
  const urls = releaseUrls(base, fingerprint);
  let expected;
  try {
    const text = await readText(urls.checksum);
    expected = /^([0-9a-f]{64})\b/i.exec(text.trim())?.[1]?.toLowerCase();
    if (!expected) return { ok: false, missing: false, reason: `${urls.checksum} holds no sha256` };
  } catch (e) {
    if (e instanceof NotFound) return { ok: false, missing: true, reason: `no release ${urls.tag} (${urls.checksum} not found)` };
    return { ok: false, missing: false, reason: `could not read ${urls.checksum}: ${e.cause?.message ?? e.message}` };
  }
  const archive = join(workDir, urls.file);
  log(`[prepare] downloading ${urls.archive}`);
  let actual;
  try {
    actual = await download(urls.archive, archive, { log, idleMs });
  } catch (e) {
    rmSync(archive, { force: true });
    if (e instanceof NotFound) return { ok: false, missing: true, reason: `release ${urls.tag} has no ${urls.file}` };
    return { ok: false, missing: false, reason: `could not download ${urls.archive}: ${e.cause?.message ?? e.message}` };
  }
  if (actual !== expected) {
    rmSync(archive, { force: true });
    return { ok: false, missing: false, reason: `${urls.file} has sha256 ${actual}, but the release says ${expected}` };
  }
  return { ok: true, archive, sha256: actual };
}

function tar(args, opts = {}) {
  const r = spawnSync("tar", args, { encoding: "utf8", maxBuffer: 256 * 1024 * 1024, ...opts });
  if (r.error) throw new Error(`tar could not run: ${r.error.message}`);
  if (r.status !== 0) throw new Error(`tar ${args.join(" ")} failed (${r.status}): ${r.stderr.trim()}`);
  return r.stdout;
}

/**
 * Throws unless every entry of the archive is a plain file or folder under
 * `assets/` (no absolute paths, no `..`, no links or devices), so unpacking
 * cannot write outside the folder it is unpacked in.
 */
export function checkArchive(archive) {
  const names = tar(["-tzf", archive]).split("\n").filter(Boolean);
  const long = tar(["-tvzf", archive]).split("\n").filter(Boolean);
  if (names.length === 0) throw new Error(`${archive} is empty`);
  if (names.length !== long.length) throw new Error(`${archive}: entry names with line breaks`);
  for (const [i, name] of names.entries()) {
    const type = long[i][0];
    if (type !== "-" && type !== "d") throw new Error(`${archive}: ${name} is not a plain file or folder (${type})`);
    const parts = name.replace(/\/$/, "").split("/");
    if (name.startsWith("/") || name.includes("\\") || parts.includes("..") || parts[0] !== "assets") {
      throw new Error(`${archive}: ${name} is outside assets/`);
    }
  }
  return names.length;
}

/** Sets every file under `dir` to one modification time, so prepare's steps all see their outputs as built together. */
function touchTree(dir, when) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) touchTree(p, when);
    utimesSync(p, when, when);
  }
}

/**
 * Unpacks a release archive over `assetsDir` (client/public/assets): checked
 * (checkArchive), unpacked beside it in `workDir`, its fingerprint file
 * compared with `fingerprint`, then swapped in. The KTX2 decoder folder of
 * the old assets is kept. Every file gets the current time, so the
 * incremental build sees the fetched outputs as newer than this checkout's
 * sources until one of them changes.
 */
export function installArchive(archive, assetsDir, fingerprint, workDir) {
  const count = checkArchive(archive);
  const staging = join(workDir, "unpacked");
  rmSync(staging, { recursive: true, force: true });
  mkdirSync(staging, { recursive: true });
  tar(["-xzf", archive, "-C", staging]);
  const incoming = join(staging, "assets");
  const got = readFingerprint(incoming);
  if (got !== fingerprint) throw new Error(`the archive is for fingerprint ${got ?? "(none)"}, not ${fingerprint}`);
  touchTree(incoming, new Date());
  const previous = join(workDir, "previous");
  rmSync(previous, { recursive: true, force: true });
  mkdirSync(dirname(assetsDir), { recursive: true });
  if (existsSync(assetsDir)) renameSync(assetsDir, previous);
  try {
    for (const keep of UNPACKED_DIRS) {
      if (existsSync(join(previous, keep)) && !existsSync(join(incoming, keep))) renameSync(join(previous, keep), join(incoming, keep));
    }
    renameSync(incoming, assetsDir);
  } catch (e) {
    if (!existsSync(assetsDir) && existsSync(previous)) renameSync(previous, assetsDir);
    throw e;
  }
  rmSync(previous, { recursive: true, force: true });
  rmSync(staging, { recursive: true, force: true });
  return count;
}

/** sha256 of a file on disk. */
export async function sha256File(path) {
  const hash = sha256Stream();
  await pipeline(createReadStream(path), hash, async function* (source) {
    for await (const _ of source);
  });
  return hash.digest();
}

/**
 * Packs `assetsDir` (whose fingerprint file must say `fingerprint`) into
 * `<outDir>/assets-<fp>/assets-<fp>.tar.gz` and writes its `.sha256`
 * (`<hex>  <file>`, as sha256sum prints it). Returns the paths and the hash.
 * With `repo`, first writes that checkout's credits and licence into the
 * folder (credits.mjs writeAssetCredits), so every release carries them;
 * they are not build inputs, so the fingerprint is unchanged.
 */
export async function packAssets(assetsDir, outDir, fingerprint, { repo = null } = {}) {
  const got = readFingerprint(assetsDir);
  if (got !== fingerprint) throw new Error(`${assetsDir} was built for fingerprint ${got ?? "(none)"}, not ${fingerprint}`);
  if (repo) {
    writeAssetCredits(assetsDir, repo);
    for (const f of ASSET_CREDIT_FILES) if (!existsSync(join(assetsDir, f))) throw new Error(`${assetsDir} has no ${f}`);
  }
  const { tag, file } = releaseUrls("x", fingerprint);
  const dir = join(outDir, tag);
  mkdirSync(dir, { recursive: true });
  const archive = join(dir, file);
  const parent = dirname(resolve(assetsDir));
  const name = resolve(assetsDir).split(sep).pop();
  if (name !== "assets") throw new Error(`${assetsDir} must be a folder named assets`);
  // COPYFILE_DISABLE: macOS tar would add AppleDouble ._ files for extended attributes.
  tar(["-czf", archive, ...UNPACKED_DIRS.map((d) => `--exclude=assets/${d}`), "--exclude=.DS_Store", "-C", parent, "assets"], {
    env: { ...process.env, COPYFILE_DISABLE: "1" },
  });
  checkArchive(archive);
  const sha256 = await sha256File(archive);
  const checksum = `${archive}.sha256`;
  writeFileSync(checksum, `${sha256}  ${file}\n`);
  return { tag, archive, checksum, sha256, bytes: statSync(archive).size };
}

// ---------------------------------------------------------------------------
// Tools against their pins

/** The first non-comment line of a pin file, or the value of `key = value` in it. */
export function readPin(repo, rel, key = null) {
  for (const raw of readFileSync(join(repo, rel), "utf8").split("\n")) {
    const line = raw.split("#")[0].trim();
    if (!line) continue;
    if (key === null) return line;
    const m = /^([A-Za-z_]+)\s*=\s*(.*)$/.exec(line);
    if (m && m[1] === key) return m[2].trim();
  }
  throw new Error(`${rel} has no ${key ?? "version"}`);
}

/** An executable named `name` on PATH, or null. */
export function onPath(name, path = process.env.PATH ?? "") {
  for (const dir of path.split(delimiter)) {
    if (!dir) continue;
    const candidate = join(dir, name);
    try {
      accessSync(candidate, constants.X_OK);
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      // not here
    }
  }
  return null;
}

/** "major.minor" of `<blender> --version`, "unknown", or null when it cannot be run. */
export function blenderVersion(blender, env = process.env) {
  const probe = spawnSync(blender, ["--version"], { encoding: "utf8", env });
  if (probe.error || probe.status !== 0) return null;
  const m = /^Blender (\d+)\.(\d+)/m.exec(probe.stdout ?? "");
  return m ? `${m[1]}.${m[2]}` : "unknown";
}

/** The MPFB version installed in a Blender user resources folder, or null. */
export function installedMpfb(resources) {
  if (!resources) return null;
  const manifest = join(resources, "extensions/user_default/mpfb/blender_manifest.toml");
  if (!existsSync(manifest)) return null;
  const m = /^version\s*=\s*"?([^"\s]+)"?/m.exec(readFileSync(manifest, "utf8"));
  return m ? m[1] : null;
}

export const BASISU_DEFAULT = "/opt/homebrew/bin/basisu";

/** The basisu the build encodes with, found as castle_kit/textures.py basisu_binary() finds it, or null. */
export function basisuBinary(env = process.env) {
  const v = env.BASISU;
  if (v === undefined) return existsSync(BASISU_DEFAULT) ? BASISU_DEFAULT : onPath("basisu", env.PATH);
  if (!v) return null;
  if (!v.includes("/")) return onPath(v, env.PATH);
  return existsSync(v) ? v : null;
}

/** "major.minor" of a basisu binary, "unknown", or null when there is none. */
export function basisuVersion(bin) {
  if (!bin) return null;
  const probe = spawnSync(bin, ["-version"], { encoding: "utf8" });
  const m = /v(\d+)\.(\d+)/.exec(`${probe.stdout ?? ""}${probe.stderr ?? ""}`);
  return m ? `${m[1]}.${m[2]}` : "unknown";
}

/** The Blender user resources the build runs with: $BLENDER_USER_RESOURCES, else <repo>/.blender-user when it exists. */
export function userResources(repo = REPO, env = process.env) {
  if (env.BLENDER_USER_RESOURCES) return env.BLENDER_USER_RESOURCES;
  const dir = join(repo, ".blender-user");
  return existsSync(dir) ? dir : null;
}

/** Each pinned tool: { tool, want, have, ok, where }. */
export function toolReport(repo = REPO, env = process.env) {
  const blender = env.BLENDER ?? "blender";
  const resources = userResources(repo, env);
  const bin = basisuBinary(env);
  const rows = [
    { tool: "Blender", want: readPin(repo, BLENDER_PIN), have: blenderVersion(blender, resources ? { ...env, BLENDER_USER_RESOURCES: resources } : env), where: blender },
    { tool: "MPFB", want: readPin(repo, MPFB_PIN, "version"), have: installedMpfb(resources), where: resources ?? join(repo, ".blender-user") + " (missing)" },
    { tool: "basisu", want: readPin(repo, BASISU_PIN), have: basisuVersion(bin), where: bin ?? "not found" },
  ];
  return rows.map((r) => ({ ...r, ok: r.have === r.want }));
}

// ---------------------------------------------------------------------------
// CLI

function option(args, name, fallback = null) {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : fallback;
}

async function main(argv) {
  const [cmd, ...args] = argv;
  const repo = resolve(option(args, "--repo", REPO));
  if (cmd === "fingerprint") {
    const { fingerprint, files } = computeFingerprint(repo);
    if (args.includes("--list")) for (const f of files) console.log(`${f.sha256}  ${f.path}`);
    console.log(fingerprint);
    return 0;
  }
  if (cmd === "pack") {
    const out = option(args, "--out");
    if (!out) throw new Error("pack needs --out DIR");
    const assetsDir = resolve(option(args, "--assets", join(repo, "client/public/assets")));
    const { fingerprint } = computeFingerprint(repo);
    const r = await packAssets(assetsDir, resolve(out), fingerprint, { repo });
    console.log(JSON.stringify({ fingerprint, ...r }, null, 2));
    return 0;
  }
  if (cmd === "tools") {
    const rows = toolReport(repo);
    for (const r of rows) console.log(`${r.ok ? "ok  " : "BAD "} ${r.tool.padEnd(8)} want ${r.want.padEnd(7)} have ${r.have ?? "none"}  (${r.where})`);
    return args.includes("--strict") && rows.some((r) => !r.ok) ? 1 : 0;
  }
  if (cmd === "release-url") {
    const { fingerprint } = computeFingerprint(repo);
    const r = releaseBase({ repo });
    console.log(r.base ? `${releaseUrls(r.base, fingerprint).archive}  (from ${r.source})` : r.why);
    return r.base ? 0 : 1;
  }
  if (cmd === "release-repo") {
    const r = releaseRepo({ repo });
    console.log(r.repo ?? r.why);
    return r.repo ? 0 : 1;
  }
  console.error(
    "usage: node client/scripts/assets.mjs fingerprint [--list] | pack --out DIR [--assets DIR] | tools [--strict] | release-url | release-repo  [--repo DIR]",
  );
  return 2;
}

/** Whether this module is the script node was started with (through symlinks too, e.g. /var -> /private/var on macOS). */
function isMain() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isMain()) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (e) => {
      console.error(`error: ${e.message}`);
      process.exit(1);
    },
  );
}

export { NotFound };
