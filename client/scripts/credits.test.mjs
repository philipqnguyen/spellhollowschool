// Tests for client/scripts/credits.mjs: the credits agree with the repository
// (every committed hair source mesh, every sound source, the works
// art/CREDITS.md records), the asset credits are written without touching
// the fingerprint and travel in a packed release, and the notices name the
// libraries the client ships.
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { computeFingerprint, isFingerprintInput, packAssets, writeFingerprint } from "./assets.mjs";
import { fingerprintRoots } from "./asset-steps.mjs";
import {
  ASSET_CREDIT_FILES,
  AUDIO_CREDITS,
  CREDITS_SOURCE,
  creditsMarkdown,
  packagesInModules,
  parseAudioCredits,
  readCredits,
  REPO,
  runtimeDependencies,
  thirdPartyNotices,
  writeAssetCredits,
} from "./credits.mjs";

const source = JSON.parse(readFileSync(join(REPO, CREDITS_SOURCE), "utf8"));
const artCredits = readFileSync(join(REPO, "art/CREDITS.md"), "utf8");

let tmp;
beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "shs-credits-test-"));
});
afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("the credits source", () => {
  it("lists every committed hair source mesh, and art/CREDITS.md does too", () => {
    const hair = source.sections.find((s) => s.id === "hair").entries[0];
    const committed = readdirSync(join(REPO, "art/characters/hair_meshy"))
      .filter((f) => f.endsWith(".npz"))
      .map((f) => ({ file: f, style: f.replace(/_body_[ab]\.npz$/, "") }));
    expect(committed.length).toBeGreaterThan(0);
    expect(new Set(hair.styles)).toEqual(new Set(committed.map((c) => c.style)));
    const creator = JSON.parse(readFileSync(join(REPO, "data/characters/creator.json"), "utf8"));
    for (const { file, style } of committed) {
      expect(artCredits, `art/CREDITS.md names ${file}`).toContain(`\`${file}\``);
      const name = creator.hair_styles.find((s) => s.asset === `hair/${style}`)?.name;
      expect(name, `${style} is a creator hair style`).toBeTruthy();
      expect(hair.note, `the credits name ${name}`).toContain(name);
    }
  });

  it("names the works art/CREDITS.md records, with their links and licences", () => {
    for (const id of ["models", "animation", "fonts"]) {
      for (const e of source.sections.find((s) => s.id === id).entries) {
        if (e.author === "The MakeHuman team") continue; // recorded in CREDITS-makehuman.md
        expect(artCredits, `${e.work}`).toContain(e.work.split(":")[0]);
        if (e.link && id !== "animation") expect(artCredits, `${e.work} link`).toContain(e.link);
      }
    }
    for (const author of ["ALexzart", "DJP3D", "Quaternius"]) {
      const entry = source.sections.flatMap((s) => s.entries ?? []).find((e) => e.author === author);
      expect(entry, author).toBeTruthy();
      expect(entry.licenceUrl).toMatch(/^https:\/\/creativecommons\.org\//);
    }
  });

  it("credits every recorded sound file and source (data/audio/sources.json)", () => {
    const rows = parseAudioCredits(readFileSync(join(REPO, AUDIO_CREDITS), "utf8"));
    expect(rows.length).toBeGreaterThan(10);
    const records = JSON.parse(readFileSync(join(REPO, "data/audio/sources.json"), "utf8")).files;
    for (const r of records) {
      const row = rows.find((x) => x.link === r.source && x.author === r.author);
      expect(row, `${r.author} ${r.source}`).toBeTruthy();
      expect(row.files, r.file).toContain(r.file);
    }
  });

  it("is not an asset build input", () => {
    for (const f of [CREDITS_SOURCE, AUDIO_CREDITS, "client/scripts/credits.mjs", "art/CREDITS.md", "art/CREDITS-makehuman.md"]) {
      const inRoots = fingerprintRoots().some((r) => f === r || f.startsWith(`${r}/`));
      expect(inRoots && isFingerprintInput(f), f).toBe(false);
    }
  });
});

describe("the model", () => {
  it("has every section, the sound rows and the runtime libraries", () => {
    const model = readCredits({ crates: { crates: [] } });
    expect(model.sections.map((s) => s.id)).toEqual(source.sections.map((s) => s.id));
    const sound = model.sections.find((s) => s.id === "sound");
    expect(sound.entries.some((e) => e.author.startsWith("Kenney") && e.licence === "CC0 1.0")).toBe(true);
    const code = model.sections.find((s) => s.id === "code");
    for (const dep of runtimeDependencies()) expect(code.entries.some((e) => e.work.startsWith(`${dep} `)), dep).toBe(true);
    for (const s of model.sections) for (const e of s.entries) expect(e.work && e.licence, `${s.id}: ${JSON.stringify(e)}`).toBeTruthy();
  });

  it("leaves the code libraries out of the asset credits and points to the notices", () => {
    const md = creditsMarkdown(readCredits({ code: false }));
    expect(md).toContain("CC BY-SA 4.0");
    expect(md).toContain("LICENSE-CC-BY-SA-4.0.txt");
    expect(md).toContain('"Anime boy male model T-pose base basic FREE" (https://skfb.ly/oxIPp) by ALexzart, licensed CC BY 4.0');
    expect(md).toContain('"Anime BaseMesh Sculpt 01" (https://skfb.ly/oPv6y) by DJP3D');
    expect(md).toContain("THIRD-PARTY-NOTICES.txt");
    expect(md).not.toContain("@babylonjs/core");
  });
});

describe("the asset credits", () => {
  it("are written once, beside the assets, from the checkout", () => {
    const assets = join(tmp, "assets");
    expect(writeAssetCredits(assets)).toEqual(ASSET_CREDIT_FILES);
    expect(writeAssetCredits(assets)).toEqual([]);
    expect(readFileSync(join(assets, "LICENSE-CC-BY-SA-4.0.txt"), "utf8")).toBe(readFileSync(join(REPO, "art/LICENSE"), "utf8").replace(/\r\n/g, "\n"));
    expect(readFileSync(join(assets, "CREDITS.md"), "utf8")).toMatch(/^# SpellHollow School: credits for the game assets/);
  });

  it("travel in a packed release without changing the fingerprint", async () => {
    const before = computeFingerprint().fingerprint;
    const built = join(tmp, "built", "assets");
    mkdirSync(join(built, "world"), { recursive: true });
    writeFileSync(join(built, "world/manifest.json"), "{}");
    const FP = "0123456789abcdef";
    writeFingerprint(built, FP);
    const packed = await packAssets(built, join(tmp, "out"), FP, { repo: REPO });
    const names = spawnSync("tar", ["-tzf", packed.archive], { encoding: "utf8" }).stdout.split("\n");
    for (const f of ASSET_CREDIT_FILES) expect(names, f).toContain(`assets/${f}`);
    expect(computeFingerprint().fingerprint).toBe(before);
  });
});

describe("the third-party notices", () => {
  it("finds the npm packages in bundled module ids", () => {
    expect(
      packagesInModules([
        "/r/node_modules/@babylonjs/core/Engines/engine.js",
        "/r/client/node_modules/.pnpm/x/node_modules/left-pad/index.js",
        "C:\\r\\node_modules\\@fontsource\\cinzel\\index.css",
        "\0vite/preload-helper.js",
        "/r/client/src/main.ts",
      ]),
    ).toEqual(["@babylonjs/core", "@fontsource/cinzel", "left-pad"]);
  });

  it("carry each runtime library's licence text and the Rust crates given", () => {
    const crate = join(tmp, "crate");
    mkdirSync(crate);
    writeFileSync(join(crate, "LICENSE-MIT"), "MIT License\n\nCopyright (c) Test Crate Authors\n");
    const crates = { crates: [{ name: "test-crate", version: "1.2.3", licence: "MIT", link: "https://example.org", dir: crate, files: ["LICENSE-MIT"] }] };
    const { text, warnings } = thirdPartyNotices({ crates });
    expect(warnings).toEqual([]);
    for (const dep of runtimeDependencies()) expect(text, dep).toMatch(new RegExp(`Applies to: ${dep.replace("/", "\\/")} `));
    expect(text).toContain("test-crate 1.2.3  MIT  https://example.org");
    expect(text).toContain("Copyright (c) Test Crate Authors");
    expect(text).toContain("Apache License");
    expect(text).toContain("SIL OPEN FONT LICENSE");
  });
});
