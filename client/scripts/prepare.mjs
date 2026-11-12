// Runs before `dev`, `build` and `typecheck`: makes sure the generated inputs the
// client imports exist.
//
// - src/wasm/: the sim-wasm bindings. Built with scripts/build-wasm.sh when missing
//   or older than the Rust sources they come from (sim-core, protocol, sim-wasm).
// - public/assets/: everything Blender exports (see tools/blender/README.md;
//   the steps and their sources are in asset-steps.mjs):
//   - world/textures/: the castle kit's shared texture library (textures.json);
//   - world/: the greybox castle cells (kitted cells per data/world/castle_kit.json);
//   - furniture/: every catalog furniture model;
//   - instances/ (+ world/instances.json): the four dorm room shells, then
//     the Charms classroom and the duel arena (instances/school.json);
//   - characters/, gear/, hair/: bodies, rig, animations, clothing and hair
//     (the slow one, several minutes);
//   - icons/: a 128 px icon for every catalog item (after the characters,
//     whose clothing files the clothing icons are rendered from);
//   - vfx/: the spell effects and the wand (after the characters, whose
//     hand socket the wand attaches to).
//   assets-fingerprint.txt records the fingerprint (assets.mjs: a hash of
//   every build input) the folder was last completely built or fetched for.
//   When it matches this checkout and every step's output exists, nothing
//   happens. Otherwise, by SHS_ASSETS (tools/blender/README.md "Asset releases"):
//   - auto (default): download the published release for this fingerprint
//     (assets-<fp>.tar.gz, checked against its sha256; from SHS_ASSETS_URL,
//     else the GitHub repository in package.json or the origin remote); if
//     there is none, build with Blender: each step whose output is missing or
//     older than its sources (incremental); if Blender (or, for the
//     characters, MPFB) is missing or not the pinned version, say so and go
//     on: the client draws greybox stand-ins for what is missing.
//   - fetch: only download; no release is an error.
//   - build: only build; a missing or wrong Blender or MPFB, or a failed
//     step, is an error.
//   Blender must be the major.minor in tools/blender/BLENDER_VERSION, and a
//   character build needs the MPFB in tools/blender/MPFB_VERSION
//   (tools/blender/install_mpfb.sh); prepare checks both before building
//   anything. SHS_SKIP_ASSETS=1 skips public/assets entirely (CI).
// - public/assets/ktx2/: the self-hosted KTX2 decoder
//   (docs/art/castle-kit.md "Textures"): @babylonjs/ktx2decoder's transcoder files
//   and a worker-loadable classic script of the decoder (babylon.ktx2Decoder.js,
//   defining the global KTX2DECODER), built with Vite's library mode. Rebuilt
//   when the package version changes or a file is missing; no Blender needed
//   (and not part of an asset release).
//   Nothing is fetched from a CDN at runtime (src/game/castle/ktx2.ts points
//   Babylon's URLs here).
//
// - public/assets/CREDITS.md and LICENSE-CC-BY-SA-4.0.txt: the credits and
//   licence the built assets carry (credits.mjs writeAssetCredits, from
//   data/credits.json), written after every build or fetch, whichever way the
//   folder was made, so a release published before they existed gets them
//   too. They are outputs only: not in the fingerprint, written after the
//   release's sha256 and fingerprint checks.
//
// layout.json is not copied: src/game/world.ts imports data/world/layout.json
// with Vite's `?url`, so dev serves the source file and builds emit a hashed copy.
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { BASISU_PIN, BASISU_SCRIPTS, BLENDER_PIN, blenderArgs, CHARACTER_SCRIPT, MPFB_PIN, STEPS, stepSources } from "./asset-steps.mjs";
import {
  BASISU_DEFAULT,
  basisuBinary,
  basisuVersion,
  blenderVersion,
  clearFingerprint,
  computeFingerprint,
  fetchRelease,
  installArchive,
  installedMpfb,
  parseMode,
  planAssets,
  readFingerprint,
  readPin,
  releaseBase,
  releaseUrls,
  userResources,
  writeFingerprint,
} from "./assets.mjs";
import { ASSET_CREDIT_FILES, writeAssetCredits } from "./credits.mjs";

const client = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = resolve(client, "..");
const assets = join(client, "public/assets");

const wasmOut = join(client, "src/wasm/sim_wasm_bg.wasm");
const wasmSources = ["crates/sim-core", "crates/protocol", "crates/sim-wasm"].map((p) => join(repo, p));

/** Directories whose files never change an output: build caches and tests. */
const SKIP_DIRS = new Set(["target", "__pycache__", "tests"]);

/** Newest modification time of a file, or of any file under a directory (skipping SKIP_DIRS and dot dirs). */
function newestMtime(path) {
  if (!existsSync(path)) return 0;
  const stat = statSync(path);
  if (!stat.isDirectory()) return stat.mtimeMs;
  let newest = 0;
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    if (entry.name.startsWith(".") || SKIP_DIRS.has(entry.name)) continue;
    newest = Math.max(newest, newestMtime(join(path, entry.name)));
  }
  return newest;
}

// The character build needs MPFB (MakeHuman) in the Blender user resources:
// the gitignored .blender-user folder at the repository root, unless the
// environment names one (docs/art/character-adoption.md).
const blenderUser = join(repo, ".blender-user");
const resources = userResources(repo, process.env);
const runEnv = resources ? { ...process.env, BLENDER_USER_RESOURCES: resources } : process.env;

function run(cmd, args) {
  const result = spawnSync(cmd, args, { cwd: repo, stdio: "inherit", env: runEnv });
  return result.status === 0;
}

/** Why `output` must be rebuilt from `sources` (repo-relative), or null when it is current. */
function staleReason(output, sources) {
  if (!existsSync(output)) return "missing";
  const built = statSync(output).mtimeMs;
  const newest = Math.max(...sources.map((s) => newestMtime(join(repo, s))));
  return newest > built ? "out of date" : null;
}

function ensureWasm() {
  if (process.env.SHS_SKIP_WASM_BUILD === "1") return;
  let reason = null;
  if (!existsSync(wasmOut)) {
    reason = "missing";
  } else {
    const built = statSync(wasmOut).mtimeMs;
    const sources = Math.max(...wasmSources.filter(existsSync).map(newestMtime), statSync(join(repo, "Cargo.lock")).mtimeMs);
    if (sources > built) reason = "out of date";
  }
  if (!reason) return;
  console.log(`[prepare] client/src/wasm is ${reason}; running scripts/build-wasm.sh`);
  if (!run("bash", ["scripts/build-wasm.sh"])) {
    console.error("[prepare] scripts/build-wasm.sh failed");
    process.exit(1);
  }
}

const blender = process.env.BLENDER ?? "blender";

/** Runs one step's Blender script headless; false (with a warning) when the script fails. */
function blenderScript(step) {
  const ok = run(blender, blenderArgs(step));
  if (!ok) console.warn(`[prepare] warning: could not generate ${step.what} with Blender (${blender}).`);
  return ok;
}

/** Warns, before a stale texture or portrait step, when basisu is missing or not the pinned major.minor. */
function checkBasisu(stale) {
  if (!stale.some((s) => BASISU_SCRIPTS.has(s.script))) return;
  const want = readPin(repo, BASISU_PIN);
  const bin = basisuBinary();
  const have = basisuVersion(bin);
  if (have === want) return;
  console.warn(
    [
      `[prepare] warning: the castle textures and portrait backdrops want basisu ${want} (${BASISU_PIN}) for their KTX2`,
      `          files; ${bin ? `${bin} is basisu ${have}` : "basisu was not found ($BASISU, " + BASISU_DEFAULT + ", PATH)"}.`,
      bin
        ? "          The KTX2 files will differ from a published asset release."
        : "          They are written as PNG only (the client loads those too).",
      "          macOS: brew install basis_universal; elsewhere https://github.com/BinomialLLC/basis_universal/releases",
    ].join("\n"),
  );
}

/** Why the pinned tools cannot build the `stale` steps (message lines), or null when they can. */
function buildBlockers(stale) {
  const want = readPin(repo, BLENDER_PIN);
  const have = blenderVersion(blender, runEnv);
  const blenderHelp = [
    `Install Blender ${want} (https://www.blender.org/download/; every ${want}.x release is at`,
    `https://download.blender.org/release/Blender${want}/) and put it on PATH, or set BLENDER=/path/to/blender.`,
  ];
  if (have === null) return [`Blender ${want} is needed to build (${BLENDER_PIN}); ${blender} does not run.`, ...blenderHelp];
  if (have !== want) return [`client/public/assets needs Blender ${want} (${BLENDER_PIN}); ${blender} is Blender ${have}.`, ...blenderHelp];
  if (!stale.some((s) => s.script === CHARACTER_SCRIPT)) return null;
  const mpfb = readPin(repo, MPFB_PIN, "version");
  const found = installedMpfb(runEnv.BLENDER_USER_RESOURCES);
  if (found === mpfb) return null;
  const where = runEnv.BLENDER_USER_RESOURCES ?? `${blenderUser} (missing)`;
  return [
    `the character build needs MPFB ${mpfb} (${MPFB_PIN}); ${found ? `MPFB ${found} is` : "MPFB is not"} installed in ${where}.`,
    "Install it (downloads the pinned zip from extensions.blender.org, about 46 MB, and checks its sha256):",
    "    tools/blender/install_mpfb.sh",
    `or offline, from a zip you have:  tools/blender/install_mpfb.sh --zip add-on-mpfb-v${mpfb}.zip`,
  ];
}

const allOutputsExist = () => STEPS.every((s) => existsSync(join(assets, s.output)));

/**
 * The build attempt: every step whose output is missing or older than its
 * sources, in order (a rebuilt step can make a later one stale). The pins are
 * checked once, before anything is built. On success with every output
 * present, the fingerprint is recorded.
 *
 * A step's sources (asset-steps.mjs stepSources) cover every file the
 * fingerprint hashes, so a changed input makes some step stale. When none is
 * stale but the folder does not record this fingerprint, the change is one
 * modification times cannot show (a deleted source, files copied with their
 * old times, a full rebuild that stopped part way, a folder built before
 * fingerprints): every step is rebuilt, never this fingerprint written over
 * assets that were not built from these sources.
 */
function attemptBuild(fingerprint) {
  const recorded = readFingerprint(assets);
  let stale = STEPS.filter((step) => staleReason(join(assets, step.output), stepSources(step)));
  const rebuildAll = stale.length === 0 && recorded !== fingerprint;
  if (rebuildAll) {
    console.log(
      `[prepare] client/public/assets records fingerprint ${recorded ?? "(none)"}, not ${fingerprint}, but no step is older than its sources; rebuilding every step`,
    );
    stale = [...STEPS];
  }
  if (stale.length > 0) {
    const blockers = buildBlockers(stale);
    if (blockers) return { ok: false, lines: blockers };
    checkBasisu(stale);
    // Until every step has run, the folder matches no fingerprint.
    clearFingerprint(assets);
  }
  const failed = [];
  for (const step of STEPS) {
    const reason = rebuildAll ? "built from other sources" : staleReason(join(assets, step.output), stepSources(step));
    if (!reason) continue;
    console.log(`[prepare] ${step.what} is ${reason}; generating with Blender`);
    if (!blenderScript(step)) failed.push(step.what);
  }
  if (failed.length > 0) return { ok: false, lines: [`Blender could not generate ${failed.join("; ")} (see the output above).`] };
  if (!allOutputsExist()) return { ok: false, lines: ["a step ran but left its manifest missing (see the output above)."] };
  writeFingerprint(assets, fingerprint);
  return { ok: true };
}

/** The fetch attempt: the release published for this fingerprint, checked and unpacked over client/public/assets. */
async function attemptFetch(fingerprint) {
  const where = releaseBase({ repo });
  if (!where.base) return { ok: false, lines: [where.why] };
  const { tag } = releaseUrls(where.base, fingerprint);
  console.log(`[prepare] looking for the asset release ${tag} (${where.source}: ${where.base})`);
  const work = join(repo, ".out", "assets-fetch");
  rmSync(work, { recursive: true, force: true });
  mkdirSync(work, { recursive: true });
  try {
    const got = await fetchRelease(where.base, fingerprint, work, { log: (l) => console.log(l) });
    if (!got.ok) return { ok: false, lines: [got.reason] };
    const entries = installArchive(got.archive, assets, fingerprint, work);
    console.log(`[prepare] installed ${tag} (${entries} entries, sha256 ${got.sha256.slice(0, 12)}...) into client/public/assets`);
    return { ok: true };
  } catch (e) {
    return { ok: false, lines: [`could not install ${tag}: ${e.message}`] };
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

async function ensureAssets() {
  if (process.env.SHS_SKIP_ASSETS === "1") return;
  let mode;
  try {
    mode = parseMode(process.env.SHS_ASSETS);
  } catch (e) {
    console.error(`[prepare] ERROR: ${e.message}`);
    process.exit(1);
  }
  const { fingerprint } = computeFingerprint(repo);
  const built = readFingerprint(assets);
  const current = built === fingerprint && allOutputsExist();
  const plan = planAssets({ mode, current });
  if (plan.attempts.length === 0) return;
  if (!current) {
    const none = STEPS.every((s) => !existsSync(join(assets, s.output)));
    const state = none ? "missing" : built === fingerprint ? "incomplete" : "not built from these sources";
    console.log(`[prepare] client/public/assets is ${state} (fingerprint ${fingerprint}, last built ${built ?? "unknown"})`);
  }
  const failures = [];
  for (const [i, attempt] of plan.attempts.entries()) {
    const r = attempt === "fetch" ? await attemptFetch(fingerprint) : attemptBuild(fingerprint);
    if (r.ok) return;
    failures.push(`${attempt}: ${r.lines[0]}`, ...r.lines.slice(1).map((l) => `    ${l}`));
    const next = plan.attempts[i + 1];
    if (next) console.log(`[prepare] ${attempt}: ${r.lines[0]}; trying to ${next} instead`);
  }
  const pad = (l) => `          ${l}`;
  if (plan.onFail === "error") {
    console.error(
      ["", `[prepare] ERROR: SHS_ASSETS=${mode}: client/public/assets could not be ${mode === "fetch" ? "fetched" : "built"}.`, ...failures.map(pad), ""].join("\n"),
    );
    process.exit(1);
  }
  const kept = existsSync(assets) && readdirSync(assets).some((e) => e !== "ktx2" && !ASSET_CREDIT_FILES.includes(e));
  console.warn(
    [
      "",
      "[prepare] warning: client/public/assets could not be fetched or built:",
      ...failures.map(pad),
      pad(
        kept
          ? "The client uses the assets that are there (built from other sources) and draws greybox stand-ins for anything missing."
          : "The client draws greybox stand-ins (characters, furniture, rooms) and no castle.",
      ),
      pad("To get them: install the pinned Blender and MPFB (above), or point SHS_ASSETS_URL at an asset release."),
      pad("SHS_SKIP_ASSETS=1 skips this step."),
      "",
    ].join("\n"),
  );
}

// The self-hosted KTX2 decoder (docs/art/castle-kit.md "Textures").
// Must match KTX2_FILES in src/game/castle/ktx2.ts (a vitest checks both lists).
const KTX2_OUT = join(assets, "ktx2");
const KTX2_DECODER = "babylon.ktx2Decoder.js";
/** The package's transcoders (its wasm/ directory), served as they are. */
const KTX2_WASM = [
  "msc_basis_transcoder.js",
  "msc_basis_transcoder.wasm",
  "uastc_astc.wasm",
  "uastc_bc7.wasm",
  "uastc_rgba8_unorm_v2.wasm",
  "uastc_rgba8_srgb_v2.wasm",
  "uastc_r8_unorm.wasm",
  "uastc_rg8_unorm.wasm",
  "zstddec.wasm",
];
const KTX2_STAMP = "decoder.json";

async function ensureKtx2Decoder() {
  if (process.env.SHS_SKIP_ASSETS === "1") return;
  const require = createRequire(join(client, "package.json"));
  let pkgDir;
  try {
    pkgDir = dirname(require.resolve("@babylonjs/ktx2decoder/package.json"));
  } catch {
    console.warn("[prepare] warning: @babylonjs/ktx2decoder is not installed (npm ci); the castle textures load as PNG");
    return;
  }
  const version = JSON.parse(readFileSync(join(pkgDir, "package.json"), "utf8")).version;
  const files = [KTX2_DECODER, ...KTX2_WASM];
  let stamp = null;
  try {
    stamp = JSON.parse(readFileSync(join(KTX2_OUT, KTX2_STAMP), "utf8"));
  } catch {
    // missing or unreadable: rebuild
  }
  if (stamp?.version === version && files.every((f) => existsSync(join(KTX2_OUT, f)))) return;
  console.log(`[prepare] the KTX2 decoder (public/assets/ktx2) is ${stamp ? "out of date" : "missing"}; building @babylonjs/ktx2decoder ${version}`);
  mkdirSync(KTX2_OUT, { recursive: true });
  for (const f of KTX2_WASM) copyFileSync(join(pkgDir, "wasm", f), join(KTX2_OUT, f));
  for (const f of ["license.md", "NOTICE.md"]) if (existsSync(join(pkgDir, f))) copyFileSync(join(pkgDir, f), join(KTX2_OUT, f));
  // The decoder as a classic script for the decode workers' importScripts: an
  // IIFE that defines the global KTX2DECODER (the namespace the UMD build on
  // Babylon's CDN defines), from the package's own legacy entry.
  const { build } = await import("vite");
  const tmp = mkdtempSync(join(tmpdir(), "shs-ktx2-"));
  try {
    await build({
      configFile: false,
      logLevel: "warn",
      root: client,
      publicDir: false,
      build: {
        outDir: tmp,
        emptyOutDir: true,
        copyPublicDir: false,
        sourcemap: false,
        minify: true,
        target: "es2020",
        lib: { entry: join(pkgDir, "legacy", "legacy.js"), name: "KTX2DECODER", formats: ["iife"], fileName: () => KTX2_DECODER },
      },
    });
    copyFileSync(join(tmp, KTX2_DECODER), join(KTX2_OUT, KTX2_DECODER));
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
  writeFileSync(join(KTX2_OUT, KTX2_STAMP), JSON.stringify({ package: "@babylonjs/ktx2decoder", version, files }, null, 2) + "\n");
}

/** The credits and licence beside the assets (credits.mjs), whether they were built, fetched or kept. */
function ensureCredits() {
  if (process.env.SHS_SKIP_ASSETS === "1") return;
  try {
    const written = writeAssetCredits(assets, repo);
    if (written.length > 0) console.log(`[prepare] wrote ${written.join(", ")} in client/public/assets`);
  } catch (e) {
    console.error(`[prepare] ERROR: could not write the asset credits (client/scripts/credits.mjs): ${e.message}`);
    process.exit(1);
  }
}

ensureWasm();
await ensureAssets();
ensureCredits();
await ensureKtx2Decoder();
