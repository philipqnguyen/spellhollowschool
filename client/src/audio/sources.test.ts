// The committed audio files (client/public/audio/) and their records
// (data/audio/sources.json): CC0 only, each file recorded with where it came
// from, nothing unrecorded, and the download budget kept.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { FILE_BUDGET } from "./rules";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "../../..");
const publicAudio = join(repo, "client/public/audio");

interface SourceRecord {
  file: string;
  ids: string[];
  source: string;
  author: string;
  licence: string;
  licenceUrl: string;
  original: string;
  downloadSha256: string;
  retrieved: string;
  edits: string;
}

const records = (JSON.parse(readFileSync(join(repo, "data/audio/sources.json"), "utf8")) as { version: number; files: SourceRecord[] }).files;

function walk(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]));
}

const AUDIO_EXT = /\.(mp3|m4a|ogg|opus|wav|flac|aac|webm)$/i;

describe("audio sources", () => {
  it("records every file as CC0, with its source", () => {
    const seen = new Set<string>();
    for (const r of records) {
      expect(seen.has(r.file), `${r.file} recorded twice`).toBe(false);
      seen.add(r.file);
      expect(r.licence, r.file).toBe("CC0-1.0");
      expect(r.licenceUrl, r.file).toMatch(/^https:\/\/creativecommons\.org\/publicdomain\/zero\/1\.0\/?$/);
      expect(r.source, r.file).toMatch(/^https:\/\//);
      expect(r.author.length, r.file).toBeGreaterThan(0);
      expect(r.original.length, r.file).toBeGreaterThan(0);
      expect(r.downloadSha256, r.file).toMatch(/^[0-9a-f]{64}$/);
      expect(r.retrieved, r.file).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(r.ids.length, r.file).toBeGreaterThan(0);
      expect(r.file, "MP3 plays in every browser").toMatch(/^sfx\/[a-z0-9_/]+\.mp3$/);
      expect(existsSync(join(publicAudio, r.file)), `${r.file} is missing`).toBe(true);
    }
  });

  it("commits no audio file without a record", () => {
    const recorded = new Set(records.map((r) => r.file));
    for (const f of walk(publicAudio).filter((p) => AUDIO_EXT.test(p))) expect(recorded.has(relative(publicAudio, f)), relative(publicAudio, f)).toBe(true);
  });

  it("keeps the download budget", () => {
    let total = 0;
    for (const f of walk(publicAudio).filter((p) => AUDIO_EXT.test(p))) {
      const size = statSync(f).size;
      expect(size, relative(publicAudio, f)).toBeLessThanOrEqual(FILE_BUDGET.perFileBytes);
      total += size;
    }
    expect(total).toBeLessThanOrEqual(FILE_BUDGET.totalBytes);
  });
});
