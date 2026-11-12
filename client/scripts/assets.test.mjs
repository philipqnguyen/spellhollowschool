// Tests for client/scripts/assets.mjs: the asset fingerprint, what prepare
// decides to do, where releases are looked up, and the pack / fetch / install
// round trip (with a file:// base and a local HTTP server).
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ASSET_STEPS_FILE, BLENDER_PIN, dataRefs, fingerprintRoots, pythonClosure, pythonImports, STEPS, stepSources } from "./asset-steps.mjs";
import {
  checkArchive,
  computeFingerprint,
  fetchRelease,
  FINGERPRINT_FILE,
  githubReleaseBase,
  hashContent,
  installArchive,
  isFingerprintInput,
  listInputs,
  packAssets,
  parseMode,
  planAssets,
  readFingerprint,
  releaseBase,
  releaseRepo,
  releaseUrls,
  REPO,
  writeFingerprint,
} from "./assets.mjs";

let tmp;
beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "shs-assets-test-"));
});
afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

/** Writes files ({ "a/b.txt": "content" }) under `root`. */
function write(root, files) {
  for (const [rel, content] of Object.entries(files)) {
    mkdirSync(join(root, rel, ".."), { recursive: true });
    writeFileSync(join(root, rel), content);
  }
}

/** Every file under `dir` as { "rel/path": sha-ish content string }. */
function snapshot(dir, prefix = "") {
  const out = {};
  for (const e of readdirSync(join(dir, prefix), { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) Object.assign(out, snapshot(dir, rel));
    else out[rel] = hashContent(readFileSync(join(dir, rel)));
  }
  return out;
}

function git(cwd, ...args) {
  const r = spawnSync("git", ["-C", cwd, ...args], { encoding: "utf8" });
  if (r.status !== 0) throw new Error(r.stderr);
  return r.stdout;
}

describe("isFingerprintInput", () => {
  it.each([
    ["tools/blender/greybox.py", true],
    ["tools/blender/BLENDER_VERSION", true],
    ["art/characters/hair_meshy/crew_cut_body_a.npz", true],
    ["data/world/terrain/grounds.json", true],
    ["tools/blender/tests/test_kit.py", false],
    ["tools/blender/characters/tests/run_tests.py", false],
    ["tools/blender/world/__pycache__/kit.cpython-311.pyc", false],
    ["tools/blender/README.md", false],
    ["art/README.md", false],
    ["art/LICENSE", false],
    ["art/.gitignore", false],
    ["art/characters/body_a.blend", false],
    ["art/world/furniture.blend1", false],
    ["art/renders/greybox/plan_level0.png", false],
    ["tools/blender/.cache/x", false],
  ])("%s -> %s", (rel, want) => {
    expect(isFingerprintInput(rel)).toBe(want);
  });
});

describe("computeFingerprint", () => {
  beforeEach(() => write(tmp, { "a.txt": "one\ntwo\n", "b/c.json": "{}", "d.bin": Buffer.from([0, 13, 10, 1]) }));

  it("is 16 hex digits and the same for the same files in any order", () => {
    const a = computeFingerprint(tmp, ["a.txt", "b/c.json", "d.bin"]);
    const b = computeFingerprint(tmp, ["d.bin", "a.txt", "b/c.json"]);
    expect(a.fingerprint).toMatch(/^[0-9a-f]{16}$/);
    expect(b.fingerprint).toBe(a.fingerprint);
    expect(a.files.map((f) => f.path)).toEqual(["a.txt", "b/c.json", "d.bin"]);
  });

  it("changes when a file's content, name or presence changes", () => {
    const base = computeFingerprint(tmp, ["a.txt", "b/c.json"]).fingerprint;
    write(tmp, { "b/c.json": "{ }" });
    const edited = computeFingerprint(tmp, ["a.txt", "b/c.json"]).fingerprint;
    expect(edited).not.toBe(base);
    write(tmp, { "e.json": "{ }" });
    expect(computeFingerprint(tmp, ["a.txt", "e.json"]).fingerprint).not.toBe(edited);
    expect(computeFingerprint(tmp, ["a.txt"]).fingerprint).not.toBe(edited);
  });

  it("reads CRLF text as LF but leaves binary files alone", () => {
    const lf = computeFingerprint(tmp, ["a.txt"]).fingerprint;
    write(tmp, { "a.txt": "one\r\ntwo\r\n" });
    expect(computeFingerprint(tmp, ["a.txt"]).fingerprint).toBe(lf);
    expect(hashContent(Buffer.from([0, 13, 10]))).not.toBe(hashContent(Buffer.from([0, 10])));
  });
});

describe("listInputs", () => {
  const files = {
    "tools/blender/greybox.py": "x",
    "tools/blender/README.md": "doc",
    "tools/blender/tests/test_x.py": "t",
    "tools/blender/world/__pycache__/m.pyc": "c",
    "art/characters/hair_meshy/s.npz": "n",
    "art/characters/body_a.blend": "generated",
    "art/renders/r.png": "render",
    "art/README.md": "doc",
    "data/world/layout.json": "{}",
    "data/spells.json": "{} not a root here",
  };
  const roots = ["tools/blender", "art", "data/world/layout.json"];
  const want = ["art/characters/hair_meshy/s.npz", "data/world/layout.json", "tools/blender/greybox.py"];

  it("walks the roots outside git, leaving out tests, docs, caches and generated files", () => {
    write(tmp, files);
    expect(listInputs(tmp, roots)).toEqual(want);
  });

  it("in a git checkout leaves out ignored files and counts new untracked ones", () => {
    write(tmp, { ...files, ".gitignore": "/tools/blender/local_notes.py\n", "tools/blender/local_notes.py": "ignored" });
    git(tmp, "init", "-q");
    git(tmp, "add", "tools/blender/greybox.py", "data", ".gitignore");
    write(tmp, { "tools/blender/new_module.py": "untracked" });
    expect(listInputs(tmp, roots)).toEqual([...want, "tools/blender/new_module.py"].sort());
  });
});

describe("what a step's script runs", () => {
  it("reads the import forms the build uses", () => {
    const code = [
      "import a, b.c as d",
      "from e.f import (g,",
      "    h as hh)",
      "from . import i",
      "from ..j import k",
      "def f():",
      "    import l",
      'm = importlib.import_module("n")',
      "o = outdoor.optional_module('p')",
      'q = __import__("r.s", fromlist=["t"])',
    ].join("\n");
    expect(pythonImports(code).map((i) => [i.name, i.level, i.from])).toEqual([
      ["a", 0, []],
      ["b.c", 0, []],
      ["l", 0, []],
      ["e.f", 0, ["g", "h"]],
      ["", 1, ["i"]],
      ["j", 2, ["k"]],
      ["n", 0, []],
      ["p", 0, []],
      ["r.s", 0, []],
    ]);
    // A variable import_module: every string literal that names a module counts.
    const loose = pythonImports('for m in ("school_rooms_x", "school_rooms_y"):\n    importlib.import_module(m)\n').map((i) => i.name);
    expect(loose).toEqual(expect.arrayContaining(["school_rooms_x", "school_rooms_y"]));
  });

  it("follows imports through modules, packages, namespace packages and relative imports", () => {
    write(tmp, {
      "tools/blender/world/build_x.py": "import helper\nfrom castle_kit import fire\nfrom npcs import frames\nimport bpy, math\n",
      "tools/blender/world/helper.py": "def f():\n    from layout_lib import thing\n",
      "tools/blender/layout_lib.py": "import pins\n# import commented_out\n",
      "tools/blender/pins.py": "",
      "tools/blender/commented_out.py": "",
      "tools/blender/castle_kit/__init__.py": "",
      "tools/blender/castle_kit/fire.py": "from .grid import G\n",
      "tools/blender/castle_kit/grid.py": "",
      "tools/blender/npcs/frames.py": "",
      "tools/blender/unused.py": "",
      "tools/blender/world/tests/helper.py": "",
    });
    expect(pythonClosure("tools/blender/world/build_x.py", tmp)).toEqual([
      "tools/blender/castle_kit/__init__.py",
      "tools/blender/castle_kit/fire.py",
      "tools/blender/castle_kit/grid.py",
      "tools/blender/layout_lib.py",
      "tools/blender/npcs/frames.py",
      "tools/blender/pins.py",
      "tools/blender/world/build_x.py",
      "tools/blender/world/helper.py",
    ]);
  });

  it("finds the data files a module names", () => {
    write(tmp, { "data/items/catalog.json": "{}", "data/world/stairs.json": "{}" });
    const code = 'A = "data/items/catalog.json"\nB = os.path.join(REPO, "data", "world", "stairs.json")\nC = "data/missing.json"\n';
    expect(dataRefs(code, tmp)).toEqual(["data/items/catalog.json", "data/world/stairs.json"]);
  });
});

describe("the repository's step sources and fingerprint inputs", () => {
  const sourcesOf = (script) => stepSources(STEPS.find((s) => s.script === script));
  const covers = (sources, file) => sources.some((r) => file === r || file.startsWith(`${r}/`));

  it("give every step the modules its script runs, the pins and asset-steps.mjs", () => {
    for (const step of STEPS) {
      const sources = stepSources(step);
      for (const f of [step.script, BLENDER_PIN, ASSET_STEPS_FILE, "tools/blender/pins.py"]) {
        expect(covers(sources, f), `${step.script}: ${f}`).toBe(true);
      }
    }
    // Imports the hand-written lists missed (the review of the asset fingerprint).
    const want = {
      "tools/blender/world/build_furniture.py": ["tools/blender/castle_kit/fire.py", "tools/blender/layout_lib.py", "tools/blender/check_layout.py"],
      "tools/blender/world/build_dorms.py": ["tools/blender/castle_kit/fire.py", "tools/blender/layout_lib.py"],
      "tools/blender/castle_kit/textures.py": ["tools/blender/layout_lib.py"],
      "tools/blender/icons/render_icons.py": ["tools/blender/layout_lib.py"],
      "tools/blender/vfx/build_vfx.py": ["tools/blender/characters/charlib/anim_spells.py"],
      "tools/blender/npcs/build_portraits.py": ["tools/blender/greybox.py", "tools/blender/npcs/frames.py", "tools/blender/npcs/backdrops.py"],
      "tools/blender/world/build_school.py": ["tools/blender/npcs/frames.py", "tools/blender/npcs/placements.py"],
      "tools/blender/world/build_wishing_room.py": ["tools/blender/npcs/frames.py", "tools/blender/npcs/placements.py"],
    };
    for (const [script, files] of Object.entries(want)) {
      for (const f of files) expect(covers(sourcesOf(script), f), `${script}: ${f}`).toBe(true);
    }
  });

  it("make some step stale for every file the fingerprint hashes", () => {
    const all = STEPS.flatMap((s) => stepSources(s));
    for (const f of listInputs(REPO)) expect(covers(all, f), f).toBe(true);
  });

  it("hash what the build reads and not the tools no step runs", () => {
    const roots = fingerprintRoots();
    for (const root of roots) expect(statSync(join(REPO, root)), root).toBeTruthy();
    expect(roots).toEqual(
      expect.arrayContaining([ASSET_STEPS_FILE, BLENDER_PIN, "art/characters/hair_meshy", "art/characters/shape_targets", "tools/blender/pins.py"]),
    );
    const inputs = listInputs(REPO);
    for (const f of [
      "client/scripts/prepare.mjs",
      "tools/blender/install_mpfb.sh",
      "tools/blender/build_art_blends.sh",
      "tools/blender/render_previews.py",
      "tools/blender/terrain_layout.py",
      "tools/blender/README.md",
      "art/README.md",
    ]) {
      expect(inputs, f).not.toContain(f);
    }
  });

  // A data file a step's modules read must be one of that step's sources, or
  // editing it neither rebuilds the step nor changes the fingerprint. The
  // modules' "data/..." names are found automatically (dataRefs); this checks
  // the hand-written lists agree for the files the build is known to read.
  it("include every data file a step's modules name", () => {
    for (const step of STEPS) {
      const sources = stepSources(step);
      for (const m of pythonClosure(step.script)) {
        const code = readFileSync(join(REPO, m), "utf8")
          .split("\n")
          .filter((l) => !l.trim().startsWith("#"))
          .join("\n");
        for (const ref of dataRefs(code)) expect(covers(sources, ref), `${step.script} (${m}): ${ref}`).toBe(true);
      }
    }
    expect(covers(sourcesOf("tools/blender/world/build_furniture.py"), "data/items/palette.json")).toBe(true);
  });

  it("give a 16-digit fingerprint", () => {
    expect(computeFingerprint(REPO).fingerprint).toMatch(/^[0-9a-f]{16}$/);
  });
});

describe("planAssets", () => {
  it.each([
    ["auto", true, [], null],
    ["auto", false, ["fetch", "build"], "fallback"],
    ["fetch", true, [], null],
    ["fetch", false, ["fetch"], "error"],
    ["build", true, ["build"], "error"],
    ["build", false, ["build"], "error"],
  ])("%s, current=%s -> %j then %s", (mode, current, attempts, onFail) => {
    expect(planAssets({ mode, current })).toEqual({ attempts, onFail });
  });

  it("parses SHS_ASSETS", () => {
    expect(parseMode(undefined)).toBe("auto");
    expect(parseMode("")).toBe("auto");
    expect(parseMode(" Fetch ")).toBe("fetch");
    expect(parseMode("build")).toBe("build");
    expect(() => parseMode("download")).toThrow(/SHS_ASSETS must be one of auto, fetch, build/);
  });
});

describe("where releases are looked up", () => {
  it.each([
    ["https://github.com/owner/repo", "owner/repo"],
    ["https://github.com/owner/repo.git", "owner/repo"],
    ["git+https://github.com/owner/repo.git", "owner/repo"],
    ["git@github.com:owner/my.repo.git", "owner/my.repo"],
    ["github:owner/repo", "owner/repo"],
    ["owner/repo", "owner/repo"],
    [{ type: "git", url: "https://github.com/owner/repo.git" }, "owner/repo"],
  ])("%j is on GitHub", (repository, slug) => {
    expect(githubReleaseBase(repository)).toBe(`https://github.com/${slug}/releases/download`);
  });

  it("knows nothing for other hosts or nothing", () => {
    expect(githubReleaseBase("https://gitlab.com/owner/repo")).toBeNull();
    expect(githubReleaseBase(undefined)).toBeNull();
  });

  it("prefers SHS_ASSETS_URL, then package.json, then the origin remote", () => {
    write(tmp, { "package.json": JSON.stringify({ name: "x" }) });
    git(tmp, "init", "-q");
    expect(releaseBase({ env: {}, repo: tmp }).base).toBeNull();
    git(tmp, "remote", "add", "origin", "git@github.com:me/fork.git");
    expect(releaseBase({ env: {}, repo: tmp })).toEqual({ base: "https://github.com/me/fork/releases/download", source: "the origin remote" });
    write(tmp, { "package.json": JSON.stringify({ repository: "github:team/game" }) });
    expect(releaseBase({ env: {}, repo: tmp }).base).toBe("https://github.com/team/game/releases/download");
    expect(releaseBase({ env: { SHS_ASSETS_URL: "http://127.0.0.1:9/rel/" }, repo: tmp }).base).toBe("http://127.0.0.1:9/rel");
    expect(releaseBase({ env: { SHS_ASSETS_URL: "rel" }, repo: tmp, cwd: tmp }).base).toBe(pathToFileURL(join(tmp, "rel")).href);
    expect(releaseBase({ env: { SHS_ASSETS_URL: "" }, repo: tmp })).toEqual({ base: null, why: expect.stringMatching(/fetching is off/) });
    // publish.sh uploads where prepare looks, whatever SHS_ASSETS_URL says.
    expect(releaseRepo({ env: { SHS_ASSETS_URL: "http://127.0.0.1:9/rel" }, repo: tmp }).repo).toBe("team/game");
    write(tmp, { "package.json": JSON.stringify({ name: "x" }) });
    expect(releaseRepo({ env: {}, repo: tmp }).repo).toBe("me/fork");
    git(tmp, "remote", "set-url", "origin", "https://gitlab.com/me/fork.git");
    expect(releaseRepo({ env: {}, repo: tmp })).toEqual({ repo: null, why: expect.stringMatching(/no release location/) });
  });

  it("names the release after the fingerprint", () => {
    expect(releaseUrls("https://h/x/releases/download/", "0123456789abcdef")).toEqual({
      tag: "assets-0123456789abcdef",
      file: "assets-0123456789abcdef.tar.gz",
      archive: "https://h/x/releases/download/assets-0123456789abcdef/assets-0123456789abcdef.tar.gz",
      checksum: "https://h/x/releases/download/assets-0123456789abcdef/assets-0123456789abcdef.tar.gz.sha256",
    });
  });
});

describe("pack, fetch and install", () => {
  const FP = "0123456789abcdef";
  let built;
  let out;
  let target;

  beforeEach(() => {
    built = join(tmp, "built", "assets");
    out = join(tmp, "release");
    target = join(tmp, "checkout", "assets");
    write(built, {
      "world/manifest.json": '{"cells":[]}',
      "world/c0.glb": Buffer.from([0, 1, 2, 3, 13, 10]),
      "characters/manifest.json": "{}",
      "ktx2/decoder.json": "built from node_modules, not released",
    });
    writeFingerprint(built, FP);
  });

  it("packs without the KTX2 decoder and restores the same files from a file:// base", async () => {
    const packed = await packAssets(built, out, FP);
    expect(packed.archive).toBe(join(out, `assets-${FP}`, `assets-${FP}.tar.gz`));
    expect(readFileSync(packed.checksum, "utf8")).toBe(`${packed.sha256}  assets-${FP}.tar.gz\n`);
    const names = spawnSync("tar", ["-tzf", packed.archive], { encoding: "utf8" }).stdout;
    expect(names).not.toMatch(/ktx2|\._/);

    write(target, { "ktx2/decoder.json": "kept", "old/stale.glb": "removed" });
    const got = await fetchRelease(pathToFileURL(out).href, FP, join(tmp, "work"));
    expect(got).toMatchObject({ ok: true, sha256: packed.sha256 });
    installArchive(got.archive, target, FP, join(tmp, "work"));

    const want = snapshot(built);
    delete want["ktx2/decoder.json"];
    const have = snapshot(target);
    expect(have["ktx2/decoder.json"]).toBe(hashContent(Buffer.from("kept")));
    delete have["ktx2/decoder.json"];
    expect(have).toEqual(want);
    expect(readFingerprint(target)).toBe(FP);
    // One modification time for everything, so no step looks older than another's output.
    const times = new Set(["world/manifest.json", "characters/manifest.json", FINGERPRINT_FILE].map((f) => statSync(join(target, f)).mtimeMs));
    expect(times.size).toBe(1);
  });

  it("reports a missing release and refuses a bad checksum", async () => {
    const base = pathToFileURL(out).href;
    expect(await fetchRelease(base, FP, join(tmp, "work"))).toMatchObject({ ok: false, missing: true });
    const packed = await packAssets(built, out, FP);
    writeFileSync(packed.checksum, `${"0".repeat(64)}  assets-${FP}.tar.gz\n`);
    const r = await fetchRelease(base, FP, join(tmp, "work"));
    expect(r).toMatchObject({ ok: false, missing: false });
    expect(r.reason).toMatch(/sha256/);
  });

  it("downloads over HTTP", async () => {
    const packed = await packAssets(built, out, FP);
    const server = createServer((req, res) => {
      const rel = decodeURIComponent(new URL(req.url, "http://x").pathname).replace(/^\/rel\//, "");
      try {
        const body = readFileSync(join(out, rel));
        res.writeHead(200, { "content-length": body.length });
        res.end(body);
      } catch {
        res.writeHead(404);
        res.end();
      }
    });
    await new Promise((r) => server.listen(0, "127.0.0.1", r));
    try {
      const base = `http://127.0.0.1:${server.address().port}/rel`;
      const got = await fetchRelease(base, FP, join(tmp, "work"));
      expect(got).toMatchObject({ ok: true, sha256: packed.sha256 });
      expect(await fetchRelease(base, "fedcba9876543210", join(tmp, "work"))).toMatchObject({ ok: false, missing: true });
    } finally {
      server.close();
    }
  });

  it("gives up a download that stalls", async () => {
    const server = createServer((req, res) => {
      if (req.url.endsWith(".sha256")) {
        res.writeHead(200);
        res.end(`${"a".repeat(64)}  assets-${FP}.tar.gz\n`);
        return;
      }
      res.writeHead(200, { "content-length": 1_000_000 });
      res.write(Buffer.alloc(1000));
      // ...and nothing more.
    });
    await new Promise((r) => server.listen(0, "127.0.0.1", r));
    try {
      const base = `http://127.0.0.1:${server.address().port}`;
      const t0 = Date.now();
      const r = await fetchRelease(base, FP, join(tmp, "work"), { idleMs: 300 });
      expect(r).toMatchObject({ ok: false, missing: false });
      expect(r.reason).toMatch(/stalled/);
      expect(Date.now() - t0).toBeLessThan(5000);
      expect(readdirSync(join(tmp, "work"))).toEqual([]);
    } finally {
      server.closeAllConnections();
      server.close();
    }
  });

  it("refuses to pack or install for another fingerprint and keeps the old assets", async () => {
    await expect(packAssets(built, out, "fedcba9876543210")).rejects.toThrow(/built for fingerprint 0123456789abcdef/);
    const packed = await packAssets(built, out, FP);
    write(target, { "world/manifest.json": "old" });
    expect(() => installArchive(packed.archive, target, "fedcba9876543210", join(tmp, "work"))).toThrow(/not fedcba9876543210/);
    expect(readFileSync(join(target, "world/manifest.json"), "utf8")).toBe("old");
  });

  it("rejects archives with entries outside assets/ or links", () => {
    const src = join(tmp, "evil");
    write(src, { "assets/ok.txt": "ok", "other/x.txt": "x" });
    const outside = join(tmp, "outside.tar.gz");
    spawnSync("tar", ["-czf", outside, "-C", src, "assets", "other"]);
    expect(() => checkArchive(outside)).toThrow(/other\/ is outside assets/);

    symlinkSync("/etc/hosts", join(src, "assets", "link"));
    const linked = join(tmp, "linked.tar.gz");
    spawnSync("tar", ["-czf", linked, "-C", src, "assets"]);
    expect(() => checkArchive(linked)).toThrow(/assets\/link is not a plain file/);
  });
});
