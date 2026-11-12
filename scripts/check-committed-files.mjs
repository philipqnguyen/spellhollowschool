#!/usr/bin/env node
// The committed-files policy (docs/repo-hygiene.md): screenshots, renders and
// anything a build makes are never committed. Plain Node, no dependencies.
//
//   node scripts/check-committed-files.mjs --staged   the files added or changed
//                                                     in the index (the pre-commit hook)
//   node scripts/check-committed-files.mjs --all      every tracked file (CI, scripts/check.sh)
//
// Options: --allowlist <file> (default: scripts/committed-files-allowlist.txt as
// it is in the index, so an unstaged edit to it cannot let a staged file
// through), --max-bytes <n> (default 2 MiB).
// Exit status: 0 clean, 1 a file breaks the policy, 2 bad usage or git failed.
//
// Sizes are those of the blobs in the index, so a file is judged as it would
// be committed, not as it is on disk.

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const DEFAULT_MAX_BYTES = 2 * 1024 * 1024;
export const ALLOWLIST_PATH = "scripts/committed-files-allowlist.txt";

const SCREENSHOT_HELP =
  "Screenshots are never committed: save playtest and review screenshots under .out/screenshots/ " +
  "(gitignored) and share them outside git (the pull request, the chat).";
const RENDER_HELP =
  "Blender review renders are never committed: write them under art/renders/ or .out/ (both gitignored); " +
  "tools/blender/README.md has the commands that remake them.";
const BUILD_HELP =
  "This is a build output: it is regenerated (bin/dev, client/scripts/prepare.mjs) or downloaded as the " +
  "asset release (tools/blender/README.md, \"Asset releases\"). Commit the scripts and data it is made from instead.";

/** Folders whose contents are never committed. The allowlist cannot lift these. */
export const BLOCKED_DIRS = [
  { prefix: "docs/screenshots/", why: SCREENSHOT_HELP },
  { prefix: ".out/", why: "Local build and review output (gitignored). " + SCREENSHOT_HELP },
  { prefix: ".playwright-mcp/", why: "Browser automation scratch output. " + SCREENSHOT_HELP },
  { prefix: "loadtest-results/", why: "Load test run output: quote the numbers in the pull request instead." },
  { prefix: "art/renders/", why: RENDER_HELP },
  { prefix: "client/public/assets/", why: BUILD_HELP },
  { prefix: "client/src/wasm/", why: "The wasm bindings are built by scripts/build-wasm.sh (client/scripts/prepare.mjs runs it)." },
  { prefix: "vendor_assets/", why: "Third-party source assets are not distributed (docs/art/repo-layout.md)." },
  { prefix: ".blender-user/", why: "The local Blender user folder (MPFB install, tools/blender/install_mpfb.sh)." },
  { prefix: ".claude/worktrees/", why: "Agent git worktrees (Claude Code): each is its own checkout, merged through git, never committed." },
  { prefix: "target/", why: "Cargo build output." },
  { prefix: "node_modules/", why: "npm install output." },
];

/** Folder names that are never committed at any depth. The allowlist cannot lift these. */
export const BLOCKED_SEGMENTS = [
  { name: "dist", why: "A bundler's output (vite build and the like): it is rebuilt, never committed." },
  { name: "node_modules", why: "npm install output." },
  { name: "__pycache__", why: "Python bytecode cache." },
];

const RASTER_EXTS = "png|jpe?g|webp|gif|avif|bmp|tiff?|tga|exr|hdr|psd|xcf|kra|heic";
const RASTER = new RegExp(`\\.(${RASTER_EXTS})$`, "i");
const BLEND = /\.blend\d*$/i;
/** Screen recordings and other video: shared outside git, like screenshots. */
const VIDEO = /\.(mp4|webm|mov|mkv|avi|m4v)$/i;
/** Built binaries: exported meshes, compressed textures, compiled wasm. */
const BUILT_BINARY = /\.(glb|gltf|ktx2|wasm)$/i;

const MiB = 1024 * 1024;
/** "2.00 MiB (2097153 bytes)": exact enough that a file just over the limit does not read as equal to it. */
function formatSize(bytes) {
  return `${(bytes / MiB).toFixed(2)} MiB (${bytes} bytes)`;
}

/**
 * Parses the allowlist text: `images <dir>/` and `file <path> <reason>` lines,
 * '#' comments. Throws on a line it does not understand, so a typo cannot
 * silently allow nothing (or everything).
 */
export function parseAllowlist(text, source = ALLOWLIST_PATH) {
  const imageDirs = [];
  const files = new Map();
  text.split("\n").forEach((raw, i) => {
    const line = raw.replace(/#.*/, "").trim();
    if (!line) return;
    const [kind, path, ...reason] = line.split(/\s+/);
    if (kind === "images" && path && reason.length === 0) {
      imageDirs.push(path.endsWith("/") ? path : `${path}/`);
    } else if (kind === "file" && path && reason.length > 0) {
      files.set(path, reason.join(" "));
    } else {
      throw new Error(
        `${source}:${i + 1}: expected "images <dir>/" or "file <path> <reason>", got "${raw.trim()}"`,
      );
    }
  });
  return { imageDirs, files };
}

function isScreenshotName(path) {
  return /(^|\/)screenshots?\//i.test(path) || new RegExp(`\\.screenshot\\.(${RASTER_EXTS})$`, "i").test(path);
}

/**
 * Checks entries `{ path, size }` against the policy. Returns the problems as
 * `{ path, rule, why }`, one per file (the first rule it breaks).
 */
export function checkEntries(entries, allowlist = { imageDirs: [], files: new Map() }, maxBytes = DEFAULT_MAX_BYTES) {
  const problems = [];
  for (const { path, size } of entries) {
    const blocked = BLOCKED_DIRS.find((d) => path.startsWith(d.prefix));
    if (blocked) {
      problems.push({ path, rule: `under ${blocked.prefix}`, why: blocked.why });
      continue;
    }
    const segment = BLOCKED_SEGMENTS.find((s) => path.split("/").slice(0, -1).includes(s.name));
    if (segment) {
      problems.push({ path, rule: `under a ${segment.name}/ folder`, why: segment.why });
      continue;
    }
    if (isScreenshotName(path)) {
      problems.push({ path, rule: "a screenshot", why: SCREENSHOT_HELP });
      continue;
    }
    // Like the blocked folders, a .blend cannot be allowlisted: every one is a build output.
    if (BLEND.test(path)) {
      problems.push({
        path,
        rule: "a .blend file",
        why:
          "Every .blend is written by the Blender build (and .blend1/.blend2 are Blender's backups): change the " +
          "scripts or data under tools/blender/ and data/ instead; tools/blender/build_art_blends.sh remakes the scenes.",
      });
      continue;
    }
    const exempt = allowlist.files.has(path);
    if (VIDEO.test(path) && !exempt) {
      problems.push({
        path,
        rule: "a video",
        why:
          "Screen recordings and videos are never committed: keep them under .out/ (gitignored) and share them " +
          "outside git (the pull request, the chat).",
      });
      continue;
    }
    if (BUILT_BINARY.test(path) && !exempt) {
      problems.push({ path, rule: "a built binary", why: BUILD_HELP });
      continue;
    }
    if (RASTER.test(path) && !exempt && !allowlist.imageDirs.some((d) => path.startsWith(d))) {
      problems.push({
        path,
        rule: "a raster image outside the allowed image folders",
        why:
          `${SCREENSHOT_HELP} A hand-made image the client or the build reads goes in a folder listed with ` +
          `"images <dir>/" in ${ALLOWLIST_PATH}.`,
      });
      continue;
    }
    if (size > maxBytes && !exempt) {
      problems.push({
        path,
        rule: `${formatSize(size)}, over the ${formatSize(maxBytes)} limit`,
        why:
          `Large files are almost always generated. If this one is a source nothing can remake, add ` +
          `"file ${path} <reason>" to ${ALLOWLIST_PATH}.`,
      });
    }
  }
  return problems;
}

function git(args, cwd) {
  const r = spawnSync("git", args, { cwd, encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
  if (r.error || r.status !== 0) {
    throw new Error(`git ${args.join(" ")} failed: ${r.error?.message ?? r.stderr.trim()}`);
  }
  return r.stdout;
}

/** Sizes of blob ids, through one `git cat-file --batch-check`. */
function blobSizes(ids, cwd) {
  if (ids.length === 0) return [];
  const r = spawnSync("git", ["cat-file", "--batch-check=%(objectsize)"], {
    cwd,
    input: ids.join("\n") + "\n",
    encoding: "utf8",
    maxBuffer: 256 * 1024 * 1024,
  });
  if (r.error || r.status !== 0) throw new Error(`git cat-file failed: ${r.error?.message ?? r.stderr.trim()}`);
  return r.stdout.trim().split("\n").map(Number);
}

/** Every tracked file with its index blob size (gitlinks skipped). */
export function trackedEntries(cwd) {
  const rows = git(["ls-files", "-s", "-z"], cwd)
    .split("\0")
    .filter(Boolean)
    .map((row) => {
      const [meta, path] = row.split("\t");
      const [mode, id] = meta.split(" ");
      return { mode, id, path };
    })
    .filter((r) => r.mode !== "160000");
  const sizes = blobSizes(rows.map((r) => r.id), cwd);
  return rows.map((r, i) => ({ path: r.path, size: sizes[i] }));
}

/** The files added, copied, changed or retyped in the index against HEAD (or every staged file before the first commit). */
export function stagedEntries(cwd) {
  const hasHead = spawnSync("git", ["rev-parse", "--verify", "-q", "HEAD"], { cwd }).status === 0;
  // Before the first commit: compare against the empty tree (its id depends on the hash algorithm).
  const base = hasHead ? "HEAD" : git(["hash-object", "-t", "tree", "--stdin"], cwd).trim();
  const fields = git(["diff-index", "--cached", "--no-renames", "--diff-filter=ACMT", "-z", base], cwd).split("\0");
  const rows = [];
  for (let i = 0; i + 1 < fields.length; i += 2) {
    // ":<old mode> <new mode> <old id> <new id> <status>", then the path
    const [, newMode, , newId] = fields[i].slice(1).split(" ");
    if (newMode !== "160000") rows.push({ id: newId, path: fields[i + 1] });
  }
  const sizes = blobSizes(rows.map((r) => r.id), cwd);
  return rows.map((r, i) => ({ path: r.path, size: sizes[i] }));
}

/**
 * The allowlist as it is in the index: the version that would be committed
 * (and, in CI, the committed one). None in the index means no exceptions.
 */
export function indexAllowlist(cwd) {
  const r = spawnSync("git", ["show", `:${ALLOWLIST_PATH}`], { cwd, encoding: "utf8" });
  if (r.error) throw new Error(`git show failed: ${r.error.message}`);
  if (r.status !== 0) return { imageDirs: [], files: new Map() };
  return parseAllowlist(r.stdout, ALLOWLIST_PATH);
}

export function formatProblems(problems, mode) {
  const lines = [
    `check-committed-files: ${problems.length} file${problems.length === 1 ? "" : "s"} must not be committed:`,
    "",
  ];
  for (const p of problems) lines.push(`  ${p.path}  (${p.rule})`, `      ${p.why}`, "");
  lines.push(
    mode === "staged"
      ? "Unstage them with `git restore --staged <path>` (and move screenshots to .out/screenshots/)."
      : "Remove them from the index with `git rm --cached <path>` (and move screenshots to .out/screenshots/).",
    "The policy and its exceptions: docs/repo-hygiene.md and scripts/committed-files-allowlist.txt.",
  );
  if (mode === "staged") {
    lines.push("In a real emergency `git commit --no-verify` skips this hook, but CI runs the same check on the whole tree.");
  }
  return lines.join("\n");
}

function main(argv) {
  let mode = null;
  let allowlistPath = null;
  let maxBytes = DEFAULT_MAX_BYTES;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--staged" || a === "--all") mode = a.slice(2);
    else if (a === "--allowlist") allowlistPath = argv[++i];
    else if (a === "--max-bytes") maxBytes = Number(argv[++i]);
    else {
      console.error(`check-committed-files: unknown option ${a}\nusage: check-committed-files.mjs --staged|--all [--allowlist <file>] [--max-bytes <n>]`);
      return 2;
    }
  }
  if (!mode || !Number.isFinite(maxBytes) || maxBytes <= 0) {
    console.error("usage: check-committed-files.mjs --staged|--all [--allowlist <file>] [--max-bytes <n>]");
    return 2;
  }
  try {
    const root = git(["rev-parse", "--show-toplevel"], process.cwd()).trim();
    const allowlist = allowlistPath
      ? parseAllowlist(readFileSync(allowlistPath, "utf8"), allowlistPath)
      : indexAllowlist(root);
    const entries = mode === "staged" ? stagedEntries(root) : trackedEntries(root);
    const problems = checkEntries(entries, allowlist, maxBytes);
    if (problems.length > 0) {
      console.error(formatProblems(problems, mode));
      return 1;
    }
    if (mode === "all") console.log(`check-committed-files: ${entries.length} tracked files follow the policy`);
    return 0;
  } catch (e) {
    console.error(`check-committed-files: ${e.message}`);
    return 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main(process.argv.slice(2));
}
