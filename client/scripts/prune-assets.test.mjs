import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { hashedKey, mapFileJson } from "./asset-map.mjs";
import { parseRecordKey, pruneAssets, recordKey } from "./prune-assets.mjs";
import { S3Client } from "./s3.mjs";

const dirs = [];
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

/** A client/dist holding only an asset map of `files` (path → hash). */
function dist(files) {
  const dir = mkdtempSync(join(tmpdir(), "shs-prune-"));
  dirs.push(dir);
  writeFileSync(join(dir, "asset-map.json"), mapFileJson(files));
  return dir;
}

/** An in-memory bucket with the S3Client methods the pruner uses. */
function bucket(initial = {}) {
  const objects = new Map(Object.entries(initial));
  return {
    objects,
    listKeys: async (prefix) => new Set([...objects.keys()].filter((k) => k.startsWith(prefix))),
    put: async (key, body) => void objects.set(key, body.toString()),
    getText: async (key) => (objects.has(key) ? objects.get(key) : null),
    delete: async (key) => void objects.delete(key),
  };
}

const H = (n) => String(n).repeat(16).slice(0, 16);
const key = (path, n) => hashedKey(path, H(n));
const quiet = () => {};

/** Version n of the game: a cell that changes every version, a texture that never does. */
const version = (n) => ({ "assets/world/cell.glb": H(n), "assets/world/stone.png": H(9) });

describe("the asset pruner", () => {
  it("names records by time and version", () => {
    const k = recordKey("abc123", new Date("2026-11-12T03:15:00.123Z"));
    expect(k).toBe("deploys/20261112T031500Z-abc123.json");
    expect(parseRecordKey(k)).toEqual({ key: k, stamp: "20261112T031500Z", version: "abc123" });
    expect(parseRecordKey("deploys/notes.txt")).toBeNull();
    expect(() => recordKey("../x", new Date())).toThrow(/version/);
  });

  it("records every deploy but deletes nothing until three are recorded", async () => {
    const b = bucket({ [key("assets/world/cell.glb", 1)]: "old", [key("assets/world/stone.png", 9)]: "stone" });
    for (const [i, v] of [2, 3].entries()) {
      const r = await pruneAssets({ dist: dist(version(v)), client: b, version: `v${v}`, now: new Date(Date.UTC(2026, 10, 12, i)), log: quiet });
      expect(r.skipped).toBe(`${i + 1} of 3 versions recorded`);
    }
    expect(b.objects.has(key("assets/world/cell.glb", 1))).toBe(true);
    expect([...b.objects.keys()].filter((k) => k.startsWith("deploys/"))).toHaveLength(2);
  });

  it("keeps the files of the last three versions and deletes the rest and the older records", async () => {
    const b = bucket();
    for (let v = 1; v <= 5; v++) {
      b.objects.set(key("assets/world/cell.glb", v), `cell ${v}`);
      b.objects.set(key("assets/world/stone.png", 9), "stone");
      b.objects.set(key("audio/bell.mp3", 8), "bell, used by no version");
      const r = await pruneAssets({ dist: dist(version(v)), client: b, version: `v${v}`, now: new Date(Date.UTC(2026, 10, 12, v)), log: quiet });
      if (v >= 3) expect(r.kept).toEqual([`v${v}`, `v${v - 1}`, `v${v - 2}`]);
    }
    const keys = [...b.objects.keys()].sort();
    expect(keys.filter((k) => k.startsWith("assets/"))).toEqual([key("assets/world/cell.glb", 3), key("assets/world/cell.glb", 4), key("assets/world/cell.glb", 5), key("assets/world/stone.png", 9)].sort());
    expect(keys.some((k) => k.startsWith("audio/"))).toBe(false);
    expect(keys.filter((k) => k.startsWith("deploys/")).map((k) => parseRecordKey(k).version)).toEqual(["v3", "v4", "v5"]);
  });

  it("replaces the record of a redeployed version instead of counting it twice", async () => {
    const b = bucket();
    for (const [i, v] of ["a", "b", "a"].entries()) await pruneAssets({ dist: dist(version(i + 1)), client: b, version: v, now: new Date(Date.UTC(2026, 10, 12, i)), log: quiet });
    const records = [...b.objects.keys()].filter((k) => k.startsWith("deploys/")).map((k) => parseRecordKey(k).version);
    expect(records.sort()).toEqual(["a", "b"]);
  });

  it("deletes nothing when a kept record cannot be read", async () => {
    const b = bucket({ [key("assets/world/cell.glb", 1)]: "cell 1" });
    b.objects.set(recordKey("v1", new Date(Date.UTC(2026, 10, 12, 1))), "not json");
    b.objects.set(recordKey("v2", new Date(Date.UTC(2026, 10, 12, 2))), mapFileJson(version(2)));
    const r = await pruneAssets({ dist: dist(version(3)), client: b, version: "v3", now: new Date(Date.UTC(2026, 10, 12, 3)), log: quiet });
    expect(r.skipped).toMatch(/v1\.json could not be read/);
    expect(b.objects.has(key("assets/world/cell.glb", 1))).toBe(true);
  });

  it("writes and deletes nothing on a dry run", async () => {
    const b = bucket({ [key("assets/world/cell.glb", 1)]: "cell 1" });
    b.objects.set(recordKey("v1", new Date(Date.UTC(2026, 10, 12, 1))), mapFileJson(version(1)));
    b.objects.set(recordKey("v2", new Date(Date.UTC(2026, 10, 12, 2))), mapFileJson(version(2)));
    b.objects.set(key("audio/bell.mp3", 8), "unused");
    const before = [...b.objects.keys()].sort();
    const r = await pruneAssets({ dist: dist(version(3)), client: b, version: "v3", dryRun: true, now: new Date(Date.UTC(2026, 10, 12, 3)), log: quiet });
    expect(r.deleted).toBe(0);
    expect([...b.objects.keys()].sort()).toEqual(before);
  });
});

describe("the S3 client's put, get and delete", () => {
  it("signs each request and treats a missing key as absent", async () => {
    const calls = [];
    const fetch = async (url, init) => {
      calls.push({ method: init.method, url });
      if (url.endsWith("/missing")) return new Response("", { status: 404 });
      if (init.method === "DELETE") return new Response(null, { status: 204 });
      return new Response(init.method === "GET" ? "body" : "", { status: 200 });
    };
    const s3 = new S3Client({ endpoint: "https://s3.example.org", bucket: "b", accessKeyId: "id", secretAccessKey: "secret", fetch });
    await s3.put("deploys/x.json", Buffer.from("{}"), { "content-type": "application/json" });
    expect(await s3.getText("deploys/x.json")).toBe("body");
    expect(await s3.getText("missing")).toBeNull();
    await s3.delete("assets/a.glb");
    await s3.delete("missing");
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      "PUT https://s3.example.org/b/deploys/x.json",
      "GET https://s3.example.org/b/deploys/x.json",
      "GET https://s3.example.org/b/missing",
      "DELETE https://s3.example.org/b/assets/a.glb",
      "DELETE https://s3.example.org/b/missing",
    ]);
  });
});
