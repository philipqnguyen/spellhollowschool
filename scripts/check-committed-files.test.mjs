// Tests for scripts/check-committed-files.mjs: `node --test scripts/check-committed-files.test.mjs`
// (npm run test:scripts; scripts/check.sh and CI run it). The CLI cases build
// throwaway git repositories in the system temp folder.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { checkEntries, parseAllowlist } from "./check-committed-files.mjs";

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "check-committed-files.mjs");
const MiB = 1024 * 1024;
const none = { imageDirs: [], files: new Map() };

describe("checkEntries", () => {
  it("blocks screenshots, wherever they are", () => {
    const problems = checkEntries(
      [
        { path: "docs/screenshots/p3a-01.png", size: 10 },
        { path: "docs/screenshots/notes.md", size: 10 },
        { path: "tools/loadtest/screenshots/run.png", size: 10 },
        { path: "client/hud.screenshot.png", size: 10 },
        { path: ".out/screenshots/look.png", size: 10 },
        { path: ".playwright-mcp/page.png", size: 10 },
      ],
      none,
    );
    assert.deepEqual(
      problems.map((p) => p.path),
      [
        "docs/screenshots/p3a-01.png",
        "docs/screenshots/notes.md",
        "tools/loadtest/screenshots/run.png",
        "client/hud.screenshot.png",
        ".out/screenshots/look.png",
        ".playwright-mcp/page.png",
      ],
    );
    assert.match(problems[0].why, /\.out\/screenshots\//);
  });

  it("blocks .blend files and Blender's backups anywhere", () => {
    const problems = checkEntries(
      [
        { path: "art/characters/body_a.blend", size: 10 },
        { path: "tools/blender/fixtures/test.blend1", size: 10 },
        { path: "art/world/furniture.blend2", size: 10 },
      ],
      none,
    );
    assert.equal(problems.length, 3);
    assert.ok(problems.every((p) => p.rule === "a .blend file"));
  });

  it("does not let a file line lift the .blend rule", () => {
    const allow = parseAllowlist("file art/world/castle.blend hand-sculpted, honest\n");
    const problems = checkEntries([{ path: "art/world/castle.blend", size: 10 }], allow);
    assert.deepEqual(problems.map((p) => p.rule), ["a .blend file"]);
  });

  it("blocks videos and other image formats, and dist/ and __pycache__/ folders at any depth", () => {
    const problems = checkEntries(
      [
        { path: "docs/playtest.mp4", size: 10 },
        { path: "client/recording.WEBM", size: 10 },
        { path: "notes/capture.mov", size: 10 },
        { path: "art/textures/stone.tga", size: 10 },
        { path: "art/sky/clouds.exr", size: 10 },
        { path: "docs/diagram.tiff", size: 10 },
        { path: "art/concept.psd", size: 10 },
        { path: "dist/index.js", size: 10 },
        { path: "client/dist/assets/index.js", size: 10 },
        { path: "tools/blender/__pycache__/x.cpython-311.pyc", size: 10 },
      ],
      none,
    );
    assert.equal(problems.length, 10);
    assert.deepEqual(
      [...new Set(problems.map((p) => p.rule))],
      ["a video", "a raster image outside the allowed image folders", "under a dist/ folder", "under a __pycache__/ folder"],
    );
    // A file or folder merely named like one is fine.
    assert.deepEqual(
      checkEntries([{ path: "tools/distance/dist.ts", size: 10 }, { path: "docs/dist.md", size: 10 }], none),
      [],
    );
  });

  it("blocks build output folders and built binaries, even when allowlisted", () => {
    const allow = parseAllowlist("images client/public/assets/\nfile client/public/assets/world/cell.glb built on purpose\n");
    const problems = checkEntries(
      [
        { path: "client/public/assets/world/cell.glb", size: 10 },
        { path: "client/src/wasm/sim_wasm_bg.wasm", size: 10 },
        { path: "art/renders/greybox/hall.png", size: 10 },
        { path: "loadtest-results/run.json", size: 10 },
        { path: "client/public/models/owl.glb", size: 10 },
      ],
      allow,
    );
    assert.equal(problems.length, 5);
  });

  it("blocks a file over the size limit unless its exact path is allowlisted", () => {
    const allow = parseAllowlist("file art/characters/big.npz sculpt source nobody can remake\n");
    const problems = checkEntries(
      [
        { path: "art/characters/big.npz", size: 3 * MiB },
        { path: "data/world/huge.json", size: 3 * MiB },
        { path: "data/world/small.json", size: 2 * MiB },
      ],
      allow,
    );
    assert.deepEqual(
      problems.map((p) => p.path),
      ["data/world/huge.json"],
    );
    assert.match(problems[0].rule, /over the 2\.00 MiB \(2097152 bytes\) limit/);
  });

  it("prints sizes exactly enough to tell a file just over the limit from the limit", () => {
    const [p] = checkEntries([{ path: "data/edge.json", size: 2 * MiB + 1 }], none);
    assert.equal(p.rule, "2.00 MiB (2097153 bytes), over the 2.00 MiB (2097152 bytes) limit");
  });

  it("allows raster images only in allowlisted folders", () => {
    const allow = parseAllowlist("# ui art\nimages client/public/ui\n");
    const problems = checkEntries(
      [
        { path: "client/public/ui/crest.png", size: 1000 },
        { path: "client/public/ui/sub/frame.webp", size: 1000 },
        { path: "docs/diagram.png", size: 1000 },
        { path: "client/public/uix/crest.png", size: 1000 },
      ],
      allow,
    );
    assert.deepEqual(
      problems.map((p) => p.path),
      ["docs/diagram.png", "client/public/uix/crest.png"],
    );
  });

  it("lets ordinary sources through", () => {
    const problems = checkEntries(
      [
        { path: "client/src/main.ts", size: 5000 },
        { path: "client/public/audio/sfx/door/wood_0.mp3", size: 40000 },
        { path: "art/characters/hair_meshy/bob_locks_body_b.npz", size: 1.2 * MiB },
        { path: "tools/blender/render_previews.py", size: 9000 },
      ],
      none,
    );
    assert.deepEqual(problems, []);
  });
});

describe("parseAllowlist", () => {
  it("rejects lines it does not understand", () => {
    assert.throws(() => parseAllowlist("file just/a/path.bin\n"), /expected/);
    assert.throws(() => parseAllowlist("allow everything\n"), /expected/);
  });

  it("reads the committed allowlist", async () => {
    const { readFileSync } = await import("node:fs");
    const text = readFileSync(join(dirname(SCRIPT), "committed-files-allowlist.txt"), "utf8");
    assert.doesNotThrow(() => parseAllowlist(text));
  });
});

describe("the command line", () => {
  const dirs = [];
  after(() => dirs.forEach((d) => rmSync(d, { recursive: true, force: true })));

  function repo() {
    const dir = mkdtempSync(join(tmpdir(), "check-committed-files-"));
    dirs.push(dir);
    git(dir, "init", "-q");
    git(dir, "config", "user.email", "test@example.com");
    git(dir, "config", "user.name", "Test");
    git(dir, "config", "commit.gpgsign", "false");
    return dir;
  }
  function git(cwd, ...args) {
    const r = spawnSync("git", args, { cwd, encoding: "utf8" });
    assert.equal(r.status, 0, `git ${args.join(" ")}: ${r.stderr}`);
    return r.stdout;
  }
  function put(dir, path, content) {
    mkdirSync(dirname(join(dir, path)), { recursive: true });
    writeFileSync(join(dir, path), content);
  }
  function check(dir, ...args) {
    return spawnSync(process.execPath, [SCRIPT, ...args], { cwd: dir, encoding: "utf8" });
  }

  it("--staged refuses a staged screenshot and says where screenshots go", () => {
    const dir = repo();
    put(dir, "README.md", "hello\n");
    put(dir, "docs/screenshots/hud.png", "png");
    git(dir, "add", ".");
    const r = check(dir, "--staged");
    assert.equal(r.status, 1);
    assert.match(r.stderr, /docs\/screenshots\/hud\.png/);
    assert.match(r.stderr, /\.out\/screenshots\//);
    assert.match(r.stderr, /--no-verify/);
    assert.doesNotMatch(r.stderr, /README\.md/);
  });

  it("--staged checks only added or changed files, judged by the staged blob", () => {
    const dir = repo();
    put(dir, "data/big.json", "x".repeat(3000));
    put(dir, "client/src/a.ts", "export {};\n");
    git(dir, "add", ".");
    // An old file that breaks the policy is --all's business, not the hook's.
    assert.equal(check(dir, "--staged", "--max-bytes", "100000").status, 0);
    git(dir, "commit", "-q", "-m", "first");
    put(dir, "client/src/a.ts", "export const a = 1;\n");
    git(dir, "add", ".");
    assert.equal(check(dir, "--staged", "--max-bytes", "1000").status, 0, "the unchanged big file is not staged");
    assert.equal(check(dir, "--all", "--max-bytes", "1000").status, 1, "--all still sees it");
    // A deletion is always fine, even of a file the policy refuses.
    git(dir, "rm", "-q", "data/big.json");
    assert.equal(check(dir, "--staged", "--max-bytes", "1000").status, 0);
    // The staged content counts, not the working copy.
    put(dir, "data/grow.json", "small");
    git(dir, "add", "data/grow.json");
    put(dir, "data/grow.json", "y".repeat(5000));
    assert.equal(check(dir, "--staged", "--max-bytes", "1000").status, 0);
    git(dir, "add", "data/grow.json");
    assert.equal(check(dir, "--staged", "--max-bytes", "1000").status, 1);
  });

  it("--staged refuses a .blend and a big file, and passes an allowlisted image", () => {
    const dir = repo();
    put(dir, "scripts/committed-files-allowlist.txt", "images client/public/ui/\nfile data/atlas.bin hand-made, see docs\n");
    put(dir, "client/public/ui/crest.png", "png");
    put(dir, "data/atlas.bin", "z".repeat(5000));
    git(dir, "add", ".");
    assert.equal(check(dir, "--staged", "--max-bytes", "1000").status, 0);
    put(dir, "art/world/castle.blend", "blend");
    put(dir, "data/other.bin", "z".repeat(5000));
    git(dir, "add", ".");
    const r = check(dir, "--staged", "--max-bytes", "1000");
    assert.equal(r.status, 1);
    assert.match(r.stderr, /art\/world\/castle\.blend/);
    assert.match(r.stderr, /data\/other\.bin/);
    assert.match(r.stderr, /2 files must not be committed/);
  });

  it("--staged reads the allowlist from the index, not the working copy", () => {
    const dir = repo();
    put(dir, "scripts/committed-files-allowlist.txt", "# none yet\n");
    put(dir, "client/src/a.ts", "export {};\n");
    git(dir, "add", ".");
    git(dir, "commit", "-q", "-m", "first");
    put(dir, "client/public/ui/crest.png", "png");
    git(dir, "add", "client/public/ui/crest.png");
    // An unstaged allowlist edit must not let the staged image through.
    put(dir, "scripts/committed-files-allowlist.txt", "images client/public/ui/\n");
    const r = check(dir, "--staged");
    assert.equal(r.status, 1, r.stderr);
    assert.match(r.stderr, /client\/public\/ui\/crest\.png/);
    // Staging the allowlist line in the same commit lets it through.
    git(dir, "add", "scripts/committed-files-allowlist.txt");
    assert.equal(check(dir, "--staged").status, 0);
  });

  it("--all passes a clean tree and fails a tracked screenshot", () => {
    const dir = repo();
    put(dir, "client/src/a.ts", "export {};\n");
    git(dir, "add", ".");
    git(dir, "commit", "-q", "-m", "first");
    const ok = check(dir, "--all");
    assert.equal(ok.status, 0, ok.stderr);
    assert.match(ok.stdout, /1 tracked files follow the policy/);
    put(dir, "screenshots/x.jpg", "jpg");
    git(dir, "add", "-f", ".");
    assert.equal(check(dir, "--all").status, 1);
  });

  it("refuses bad usage and a broken allowlist", () => {
    const dir = repo();
    assert.equal(check(dir).status, 2);
    assert.equal(check(dir, "--everything").status, 2);
    put(dir, "scripts/committed-files-allowlist.txt", "permit all\n");
    git(dir, "add", ".");
    const r = check(dir, "--all");
    assert.equal(r.status, 2);
    assert.match(r.stderr, /committed-files-allowlist\.txt:1/);
  });
});
