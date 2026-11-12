// The game's credits and third-party notices, from one source
// (data/credits.json), to everywhere they have to go:
//
// - client/public/assets/CREDITS.md, LICENSE-CC-BY-SA-4.0.txt and LICENSE-MIT.txt
//   (writeAssetCredits): the attribution the CC BY works in the built assets
//   need, beside them. prepare.mjs writes them after every build or fetch (so
//   an asset release published before they existed gets them too) and
//   assets.mjs packAssets before packing a release. They are outputs, never
//   build inputs: neither this file nor data/credits.json is hashed into the
//   asset fingerprint, and the release's sha256 is checked before they are
//   written.
// - The in-game Credits panel (src/ui/credits.ts): the Vite plugin
//   (creditsPlugin, client/vite.config.ts) compiles the model into the
//   bundle as the module `virtual:shs-credits`.
// - client/dist/THIRD-PARTY-NOTICES.txt: the licence texts of every library
//   in the shipped client (the npm packages Rollup bundled, the runtime
//   dependencies of client/package.json, the Rust crates compiled into the
//   WebAssembly, Vite's runtime helpers), emitted by the same plugin on
//   `vite build` and served at /THIRD-PARTY-NOTICES.txt by the dev server.
//
// The sound rows come from client/public/audio/CREDITS.md (its table names
// every author, including the second sources of mixed sounds), the code
// libraries from client/package.json and `cargo metadata`.
//
// Importing this module has no side effects and needs nothing from
// node_modules (publish.sh's worktree has none). Run directly:
//   node client/scripts/credits.mjs assets [--assets DIR] [--repo DIR]   (writes the asset credits)
//   node client/scripts/credits.mjs markdown [--repo DIR]                (prints CREDITS.md)
//   node client/scripts/credits.mjs notices [--repo DIR]                 (prints the notices)
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const CREDITS_SOURCE = "data/credits.json";
export const AUDIO_CREDITS = "client/public/audio/CREDITS.md";
export const ART_LICENSE = "art/LICENSE";
export const CODE_LICENSE = "LICENSE";
/** The files writeAssetCredits puts in client/public/assets (and so every asset release and image). */
export const ASSET_CREDIT_FILES = ["CREDITS.md", "LICENSE-CC-BY-SA-4.0.txt", "LICENSE-MIT.txt"];
export const NOTICES_FILE = "THIRD-PARTY-NOTICES.txt";
export const VIRTUAL_ID = "virtual:shs-credits";
const RESOLVED_ID = `\0${VIRTUAL_ID}`;

// ---------------------------------------------------------------------------
// Reading the sources

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

/** Text with CRLF read as LF. */
function readText(path) {
  return readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

/**
 * The rows of the sound credits table (client/public/audio/CREDITS.md:
 * `| Author | Source (CC0) | Our files |`), as { author, link, files }.
 */
export function parseAudioCredits(md) {
  const rows = [];
  let inTable = false;
  for (const line of md.replace(/\r\n/g, "\n").split("\n")) {
    if (/^\|\s*Author\s*\|\s*Source/i.test(line)) {
      inTable = true;
      continue;
    }
    if (!inTable) continue;
    if (!line.startsWith("|")) {
      if (rows.length > 0) break;
      continue;
    }
    if (/^\|\s*-/.test(line)) continue;
    const cells = line.split("|").slice(1, -1).map((c) => c.trim());
    if (cells.length < 3) continue;
    const files = [...cells[2].matchAll(/`([^`]+)`/g)].map((m) => m[1]);
    rows.push({ author: cells[0], link: cells[1], files });
  }
  return rows;
}

/** "https://opengameart.org/content/fire-crackling" -> "Fire crackling". */
function titleFromLink(link) {
  const slug = link.replace(/\/+$/, "").split("/").pop() ?? link;
  const words = decodeURIComponent(slug).replace(/[-_]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : link;
}

/** Where an npm package is installed for the client (its own node_modules, then the workspace root's), or null. */
export function packageDir(repo, name) {
  for (const base of [join(repo, "client", "node_modules"), join(repo, "node_modules")]) {
    const dir = join(base, ...name.split("/"));
    if (existsSync(join(dir, "package.json"))) return dir;
  }
  return null;
}

function repoUrl(repository) {
  const url = typeof repository === "string" ? repository : repository?.url;
  if (!url) return null;
  return url.replace(/^git\+/, "").replace(/\.git$/, "").replace(/^git:\/\//, "https://");
}

function authorName(author) {
  if (!author) return null;
  return typeof author === "string" ? author.replace(/\s*[<(].*$/, "").trim() : (author.name ?? null);
}

/** Licence and notice files in a package or crate folder, sorted. */
function licenceFiles(dir) {
  try {
    return readdirSync(dir)
      .filter((f) => /^(LICEN[CS]E|COPYING|NOTICE|UNLICENSE)/i.test(f) && statSync(join(dir, f)).isFile())
      .sort();
  } catch {
    return [];
  }
}

/**
 * An npm package as a library record: { name, version, licence, link,
 * author, dir, files } (files: its licence and notice files), or null when it
 * is not installed.
 */
export function npmLibrary(repo, name) {
  const dir = packageDir(repo, name);
  if (!dir) return null;
  const pkg = readJson(join(dir, "package.json"));
  return {
    name,
    version: pkg.version ?? "",
    licence: typeof pkg.license === "string" ? pkg.license : (pkg.license?.type ?? "see its licence file"),
    link: pkg.homepage ?? repoUrl(pkg.repository) ?? `https://www.npmjs.com/package/${name}`,
    author: authorName(pkg.author),
    dir,
    files: licenceFiles(dir),
  };
}

/** The client's runtime dependencies (client/package.json "dependencies"), sorted. */
export function runtimeDependencies(repo = REPO) {
  return Object.keys(readJson(join(repo, "client", "package.json")).dependencies ?? {}).sort();
}

/**
 * The third-party Rust crates compiled into the client's WebAssembly
 * (crates/sim-wasm and what it depends on for wasm32, normal dependencies
 * only; proc-macro crates and build scripts run at build time and are left
 * out with what only they need), from `cargo metadata`: { crates: [{ name,
 * version, licence, link, author, dir, files }] } or { crates: null, why }.
 */
export function wasmCrates(repo = REPO, { cargo = process.env.CARGO ?? "cargo" } = {}) {
  const r = spawnSync(cargo, ["metadata", "--format-version", "1", "--locked", "--filter-platform", "wasm32-unknown-unknown"], {
    cwd: repo,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  if (r.error || r.status !== 0) return { crates: null, why: `cargo metadata failed: ${r.error?.message ?? r.stderr.trim().split("\n").pop()}` };
  let meta;
  try {
    meta = JSON.parse(r.stdout);
  } catch (e) {
    return { crates: null, why: `cargo metadata printed no JSON: ${e.message}` };
  }
  const packages = new Map(meta.packages.map((p) => [p.id, p]));
  const nodes = new Map((meta.resolve?.nodes ?? []).map((n) => [n.id, n]));
  const root = meta.packages.find((p) => p.name === "sim-wasm" && p.source === null);
  if (!root) return { crates: null, why: "cargo metadata lists no sim-wasm crate" };
  const seen = new Set();
  const queue = [root.id];
  while (queue.length > 0) {
    const id = queue.pop();
    const p = packages.get(id);
    if (!p || seen.has(id) || p.targets.some((t) => t.kind.includes("proc-macro"))) continue;
    seen.add(id);
    for (const d of nodes.get(id)?.deps ?? []) if (d.dep_kinds.some((k) => k.kind === null)) queue.push(d.pkg);
  }
  const crates = [...seen]
    .map((id) => packages.get(id))
    .filter((p) => p.source !== null)
    .map((p) => {
      const dir = dirname(p.manifest_path);
      return {
        name: p.name,
        version: p.version,
        licence: p.license ?? (p.license_file ? "see its licence file" : "unknown"),
        link: p.repository ?? p.homepage ?? `https://crates.io/crates/${p.name}`,
        author: p.authors?.length ? p.authors.join(", ").replace(/\s*<[^>]*>/g, "") : null,
        dir,
        files: licenceFiles(dir),
      };
    })
    .sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));
  return { crates };
}

// ---------------------------------------------------------------------------
// The model

/**
 * The credits model the panel and CREDITS.md are drawn from:
 * { game, sections: [{ id, title, intro, entries: [{ work, author, link,
 * licence, licenceUrl, note }] }], warnings }.
 * `code`: also list the code libraries (needs node_modules; the asset
 * credits point to THIRD-PARTY-NOTICES.txt instead). `crates`: a
 * wasmCrates() result, to reuse one cargo run.
 */
export function readCredits({ repo = REPO, code = true, crates = null } = {}) {
  const source = readJson(join(repo, CREDITS_SOURCE));
  const warnings = [];
  const sections = [];
  for (const s of source.sections) {
    let entries = (s.entries ?? []).map(({ styles: _styles, ...e }) => e);
    if (s.from === AUDIO_CREDITS) {
      entries = parseAudioCredits(readText(join(repo, AUDIO_CREDITS))).map((row) => ({
        work: titleFromLink(row.link),
        author: row.author,
        link: row.link,
        licence: "CC0 1.0",
        licenceUrl: "https://creativecommons.org/publicdomain/zero/1.0/",
      }));
    } else if (s.from === "client/package.json" && !code) {
      entries = [];
    } else if (s.from === "client/package.json") {
      entries = [];
      for (const name of runtimeDependencies(repo)) {
        const lib = npmLibrary(repo, name);
        if (!lib) {
          warnings.push(`${name} is not installed (npm ci): left out of the credits`);
          continue;
        }
        entries.push({ work: `${lib.name} ${lib.version}`, author: lib.author ?? undefined, link: lib.link, licence: lib.licence });
      }
      const rust = crates ?? wasmCrates(repo);
      if (rust.crates?.length) {
        const licences = [...new Set(rust.crates.map((c) => c.licence))].sort();
        entries.push({
          work: "Rust crates in the game's WebAssembly",
          author: "their authors",
          link: "https://crates.io",
          licence: licences.join("; "),
          note: `${rust.crates.map((c) => c.name).filter((n, i, a) => a.indexOf(n) === i).join(", ")}.`,
        });
      } else if (!rust.crates) {
        warnings.push(`the Rust crates of the WebAssembly are not listed: ${rust.why}`);
      }
    }
    sections.push({ id: s.id, title: s.title, ...(s.intro ? { intro: s.intro } : {}), entries });
  }
  return { game: source.game, sections, warnings };
}

// ---------------------------------------------------------------------------
// CREDITS.md

function mdLink(text, url) {
  return url ? `[${text}](${url})` : text;
}

/** One entry as an attribution line: "Work" (link) by Author, licensed Licence (url). Note. */
function entryLine(e) {
  const by = e.author ? ` by ${e.author}` : "";
  const licence = e.licenceUrl ? `${e.licence} (${e.licenceUrl})` : e.licence;
  const note = e.note ? ` ${e.note}` : "";
  return `- "${e.work}"${e.link ? ` (${e.link})` : ""}${by}, licensed ${licence}.${note}`;
}

/** CREDITS.md for client/public/assets: the licence of the folder, then every credit. */
export function creditsMarkdown(model) {
  const { game } = model;
  const lines = [
    `# ${game.name}: credits for the game assets`,
    "",
    "The files in this folder are built by the game's asset pipeline",
    `(\`tools/blender/\` in ${game.repository}) from its scripts, its game data and`,
    "the art sources in `art/`. Generated by `client/scripts/credits.mjs` from",
    "`data/credits.json`; do not edit this copy.",
    "",
    "## Licence",
    "",
    "- The character models and hair (`characters/`, `hair/` and the clothing",
    "  fitted to them in `gear/`) are adaptations of the art sources in `art/` and",
    "  are licensed under Creative Commons Attribution-ShareAlike 4.0",
    "  (`LICENSE-CC-BY-SA-4.0.txt` beside this file,",
    "  https://creativecommons.org/licenses/by-sa/4.0/). They contain the",
    "  third-party works credited below, which keep their own licences.",
    "- Everything else in this folder (the castle, grounds, rooms, furniture,",
    "  props, effects, icons and textures) is generated by the pipeline from its",
    "  MIT-licensed code and data and is licensed under the MIT License",
    "  (`LICENSE-MIT.txt` beside this file).",
    ...game.licences.filter((l) => !/^Art/.test(l.what)).map((l) => `- ${l.what}: ${l.licence} (${l.licenceUrl}).`),
    "- The full records (what was changed, checksums, the licence findings):",
    `  \`art/CREDITS.md\` and \`art/CREDITS-makehuman.md\` in ${game.repository}.`,
    "",
  ];
  for (const s of model.sections) {
    lines.push(`## ${s.title}`, "");
    if (s.intro) lines.push(s.intro, "");
    if (s.entries.length === 0) continue;
    for (const e of s.entries) lines.push(entryLine(e));
    lines.push("");
  }
  return lines.join("\n");
}

/** The asset credit files' names and contents (ASSET_CREDIT_FILES). */
export function assetCreditFiles(repo = REPO) {
  const model = readCredits({ repo, code: false });
  return {
    "CREDITS.md": creditsMarkdown(model),
    "LICENSE-CC-BY-SA-4.0.txt": readText(join(repo, ART_LICENSE)),
    "LICENSE-MIT.txt": readText(join(repo, CODE_LICENSE)),
  };
}

/**
 * Writes the asset credit files into `assetsDir` (created if missing), each
 * only when its content differs. Returns the names written.
 */
export function writeAssetCredits(assetsDir, repo = REPO) {
  mkdirSync(assetsDir, { recursive: true });
  const written = [];
  for (const [name, text] of Object.entries(assetCreditFiles(repo))) {
    const path = join(assetsDir, name);
    if (existsSync(path) && readFileSync(path, "utf8") === text) continue;
    writeFileSync(path, text);
    written.push(name);
  }
  return written;
}

// ---------------------------------------------------------------------------
// THIRD-PARTY-NOTICES.txt

const RULE = "=".repeat(78);

/** Vite's own licence (its runtime helpers end up in the bundle), without the list of what Vite itself bundles. */
function viteLicence(repo) {
  const dir = packageDir(repo, "vite");
  if (!dir || !existsSync(join(dir, "LICENSE.md"))) return null;
  const text = readText(join(dir, "LICENSE.md"));
  const cut = text.indexOf("\n# Licenses of bundled dependencies");
  return (cut > 0 ? text.slice(0, cut) : text).trim();
}

/**
 * The npm package names in module ids (".../node_modules/@scope/name/..."),
 * sorted, without duplicates.
 */
export function packagesInModules(ids) {
  const names = new Set();
  for (const id of ids) {
    const m = [...id.replace(/\\/g, "/").matchAll(/\/node_modules\/((?:@[^/]+\/)?[^/]+)\//g)].pop();
    if (m) names.add(m[1]);
  }
  return [...names].sort();
}

/**
 * The notices text: every library's name, version, licence and link, then
 * each distinct licence or notice text once, with the libraries it belongs
 * to. `bundled`: npm package names found in the bundle (added to the
 * client's runtime dependencies). Returns { text, warnings }.
 */
export function thirdPartyNotices({ repo = REPO, bundled = [], crates = null } = {}) {
  const warnings = [];
  const libs = [];
  for (const name of [...new Set([...runtimeDependencies(repo), ...bundled])].sort()) {
    const lib = npmLibrary(repo, name);
    if (lib) libs.push({ ...lib, kind: "npm" });
    else warnings.push(`${name} is not installed: no licence text for it`);
  }
  const rust = crates ?? wasmCrates(repo);
  if (rust.crates) libs.push(...rust.crates.map((c) => ({ ...c, kind: "crate" })));
  else warnings.push(`the Rust crates of the WebAssembly are missing from the notices: ${rust.why}`);

  const texts = new Map(); // sha256 -> { text, owners }
  for (const lib of libs) {
    if (lib.files.length === 0) warnings.push(`${lib.name} ${lib.version} ships no licence file`);
    for (const f of lib.files) {
      const text = readText(join(lib.dir, f)).trim();
      const key = createHash("sha256").update(text).digest("hex");
      const entry = texts.get(key) ?? { text, owners: [] };
      entry.owners.push(`${lib.name} ${lib.version} (${f})`);
      texts.set(key, entry);
    }
  }
  const vite = viteLicence(repo);

  const out = [
    "SpellHollow School: third-party software notices",
    "",
    "The game's client includes the software listed below. Each keeps its own",
    "licence; the texts follow the list. Generated by client/scripts/credits.mjs",
    "when the client is built. The game's art, sound and font credits are in its",
    "Credits panel (title screen and Settings) and in assets/CREDITS.md.",
    "",
    RULE,
    "Libraries",
    RULE,
    "",
  ];
  const row = (l) => `${l.name} ${l.version}  ${l.licence}  ${l.link}`;
  const npm = libs.filter((l) => l.kind === "npm");
  const crateLibs = libs.filter((l) => l.kind === "crate");
  out.push("JavaScript packages:", ...npm.map((l) => `  ${row(l)}`), "");
  if (crateLibs.length > 0) out.push("Rust crates compiled into the WebAssembly (sim_wasm_bg.wasm):", ...crateLibs.map((l) => `  ${row(l)}`), "");
  if (vite) out.push("Vite's runtime helpers (module preloading), MIT: https://vite.dev", "");
  for (const { text, owners } of texts.values()) {
    out.push(RULE, ...owners.map((o) => `Applies to: ${o}`), RULE, "", text, "");
  }
  if (vite) out.push(RULE, "Applies to: Vite's runtime helpers", RULE, "", vite, "");
  return { text: out.join("\n"), warnings };
}

// ---------------------------------------------------------------------------
// The Vite plugin

/**
 * `virtual:shs-credits` (the credits model, for src/ui/credits.ts) and, on
 * build, THIRD-PARTY-NOTICES.txt in client/dist. One cargo run per process.
 */
export function creditsPlugin({ repo = REPO } = {}) {
  let crates = null;
  const rust = () => (crates ??= wasmCrates(repo));
  let command = "serve";
  return {
    name: "shs-credits",
    configResolved(config) {
      command = config.command;
    },
    resolveId(id) {
      return id === VIRTUAL_ID ? RESOLVED_ID : null;
    },
    load(id) {
      if (id !== RESOLVED_ID) return null;
      for (const f of [CREDITS_SOURCE, AUDIO_CREDITS, "client/package.json"]) this.addWatchFile?.(join(repo, f));
      const { warnings, ...model } = readCredits({ repo, crates: rust() });
      for (const w of warnings) this.warn(`credits: ${w}`);
      return `export default ${JSON.stringify({ ...model, notices: NOTICES_FILE })};`;
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if ((req.url ?? "").split("?")[0] !== `/${NOTICES_FILE}`) return next();
        res.setHeader("Content-Type", "text/plain; charset=utf-8");
        res.end(thirdPartyNotices({ repo, crates: rust() }).text);
      });
    },
    generateBundle(_options, bundle) {
      if (command !== "build") return;
      const ids = [];
      for (const out of Object.values(bundle)) {
        if (out.type !== "chunk") continue;
        ids.push(...(out.moduleIds ?? Object.keys(out.modules ?? {})));
      }
      const { text, warnings } = thirdPartyNotices({ repo, bundled: packagesInModules(ids), crates: rust() });
      for (const w of warnings) this.warn(`notices: ${w}`);
      this.emitFile({ type: "asset", fileName: NOTICES_FILE, source: text });
    },
  };
}

// ---------------------------------------------------------------------------
// CLI

function option(args, name, fallback = null) {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : fallback;
}

function main(argv) {
  const [cmd, ...args] = argv;
  const repo = resolve(option(args, "--repo", REPO));
  if (cmd === "assets") {
    const dir = resolve(option(args, "--assets", join(repo, "client/public/assets")));
    const written = writeAssetCredits(dir, repo);
    console.log(written.length ? `wrote ${written.join(", ")} in ${dir}` : `${dir} has the current credits`);
    return 0;
  }
  if (cmd === "markdown") {
    process.stdout.write(assetCreditFiles(repo)["CREDITS.md"]);
    return 0;
  }
  if (cmd === "notices") {
    const { text, warnings } = thirdPartyNotices({ repo });
    process.stdout.write(text);
    for (const w of warnings) console.error(`warning: ${w}`);
    return 0;
  }
  console.error("usage: node client/scripts/credits.mjs assets [--assets DIR] | markdown | notices  [--repo DIR]");
  return 2;
}

function isMain() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isMain()) process.exit(main(process.argv.slice(2)));
