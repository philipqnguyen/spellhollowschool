import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { assetMapPlugin, buildAssetMap, hashedKey, MAP_FILE, mapFileJson, parseMapFile, sameOriginOnly, VIRTUAL_ID } from "./asset-map.mjs";

const dirs = [];
function publicDir(files) {
  const dir = mkdtempSync(join(tmpdir(), "shs-asset-map-"));
  dirs.push(dir);
  for (const [path, content] of Object.entries(files)) {
    mkdirSync(join(dir, path, ".."), { recursive: true });
    writeFileSync(join(dir, path), content);
  }
  return dir;
}
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});
const short = (s) => createHash("sha256").update(s).digest("hex").slice(0, 16);

describe("the asset map", () => {
  it("hashes every file under assets/ and audio/ by content, and nothing else", async () => {
    const dir = publicDir({
      "assets/world/manifest.json": "{}",
      "assets/world/cell_1.glb": "glTF one",
      "assets/.DS_Store": "junk",
      "audio/sfx/a.mp3": "mp3",
      "fonts/LICENSE.txt": "licence",
      "index.html": "<html>",
    });
    const map = await buildAssetMap(dir);
    expect(map).toEqual({
      "assets/world/cell_1.glb": short("glTF one"),
      "assets/world/manifest.json": short("{}"),
      "audio/sfx/a.mp3": short("mp3"),
    });
    // The same content in another file has the same hash, but another key.
    expect(hashedKey("assets/world/cell_1.glb", map["assets/world/cell_1.glb"])).toBe(`assets/world/cell_1.${short("glTF one")}.glb`);
    expect(await buildAssetMap(publicDir({}))).toEqual({});
  });

  it("round-trips through asset-map.json and refuses bad entries", () => {
    const map = { "assets/a.glb": "0123456789abcdef" };
    expect(parseMapFile(mapFileJson(map))).toEqual(map);
    expect(() => parseMapFile(JSON.stringify({ version: 2, files: {} }))).toThrow(/version 1/);
    expect(() => parseMapFile(JSON.stringify({ version: 1, files: { "../etc/passwd": "00" } }))).toThrow(/bad entry/);
    expect(() => parseMapFile(JSON.stringify({ version: 1, files: { "assets/a": "XYZ" } }))).toThrow(/bad entry/);
  });

  it("keeps the KTX2 decoder on the game's origin", () => {
    expect(sameOriginOnly("assets/ktx2/zstddec.wasm")).toBe(true);
    expect(sameOriginOnly("assets/world/textures/stone.ktx2")).toBe(false);
  });

  it("compiles the map into a build and writes asset-map.json; serving gets an empty map", async () => {
    const dir = publicDir({ "assets/a.glb": "a" });
    const emitted = [];
    const ctx = { emitFile: (f) => emitted.push(f), info: () => {} };
    const build = assetMapPlugin();
    build.configResolved({ command: "build", build: {}, publicDir: dir });
    await build.buildStart.call(ctx);
    const id = build.resolveId(VIRTUAL_ID);
    expect(await build.load(id)).toBe(`export default {"assets/a.glb":"${short("a")}"};\n`);
    build.generateBundle.call(ctx);
    expect(emitted).toHaveLength(1);
    expect(emitted[0].fileName).toBe(MAP_FILE);
    expect(parseMapFile(emitted[0].source)).toEqual({ "assets/a.glb": short("a") });

    const serve = assetMapPlugin();
    serve.configResolved({ command: "serve", build: {}, publicDir: dir });
    await serve.buildStart.call(ctx);
    expect(await serve.load(serve.resolveId(VIRTUAL_ID))).toBe("export default {};\n");
    expect(serve.resolveId("something-else")).toBeNull();
  });
});
