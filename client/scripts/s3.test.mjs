import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { gunzipSync, gzipSync } from "node:zlib";

import { afterEach, describe, expect, it } from "vitest";

import { mapFileJson } from "./asset-map.mjs";
import { canonicalQuery, EMPTY_SHA256, parseListObjects, S3Client, sha256Hex, signRequest } from "./s3.mjs";
import { CACHE_CONTROL, contentType, loadSettings, objectFor, plannedObjects, uploadAssets, verifyPublic } from "./upload-assets.mjs";

// The AWS Signature Version 4 test suite (aws-c-auth tests/aws-signing-test-suite/v4).
const SUITE = {
  accessKeyId: "AKIDEXAMPLE",
  secretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
  region: "us-east-1",
  service: "service",
  date: new Date("2015-08-30T12:36:00Z"),
  host: "example.amazonaws.com",
};

describe("AWS Signature Version 4", () => {
  it("matches the get-vanilla vector step by step", () => {
    const s = signRequest({ ...SUITE, method: "GET", path: "/", payloadHash: EMPTY_SHA256 });
    expect(s.canonicalRequest).toBe(
      ["GET", "/", "", "host:example.amazonaws.com", "x-amz-date:20150830T123600Z", "", "host;x-amz-date", EMPTY_SHA256].join("\n"),
    );
    expect(s.stringToSign).toBe(
      ["AWS4-HMAC-SHA256", "20150830T123600Z", "20150830/us-east-1/service/aws4_request", "bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63"].join("\n"),
    );
    expect(s.signature).toBe("5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31");
    expect(s.headers.authorization).toBe(
      "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, SignedHeaders=host;x-amz-date, Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31",
    );
    expect(s.headers.host).toBeUndefined();
  });

  it.each([
    ["get-vanilla-query-order-key-case", "/", [["Param2", "value2"], ["Param1", "value1"]], "b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500"],
    ["get-vanilla-utf8-query", "/", [["ሴ", "bar"]], "2cdec8eed098649ff3a119c94853b13c643bcf08f8b0a1d91e12c9027818dd04"],
    ["get-utf8", "/ሴ", [], "8318018e0b0f223aa2bbf98705b62bb787dc9c0e678f255a891fd03141be5d85"],
    ["get-space-normalized", "/example space/", [], "652487583200325589f1fba4c7e578f72c47cb61beeca81406b39ddec1366741"],
  ])("matches %s", (_name, path, query, signature) => {
    expect(signRequest({ ...SUITE, method: "GET", path, query, payloadHash: EMPTY_SHA256 }).signature).toBe(signature);
  });

  it("matches post-x-www-form-urlencoded (a signed body and headers)", () => {
    const body = "Param1=value1";
    const payloadHash = sha256Hex(body);
    const s = signRequest({
      ...SUITE,
      method: "POST",
      path: "/",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "Content-Length": "13", "x-amz-content-sha256": payloadHash },
      payloadHash,
    });
    expect(s.stringToSign.split("\n")[3]).toBe("b1edd1d03544c25390e32085d55b57acc9a3961bb59415ff86c45c3d89d16cfb");
    expect(s.signature).toBe("d3875051da38690788ef43de4db0d8f280229d82040bfac253562e56c3f20e0b");
  });

  it("matches the S3 documentation's GET Object example", () => {
    const s = signRequest({
      method: "GET",
      host: "examplebucket.s3.amazonaws.com",
      path: "/test.txt",
      headers: { range: "bytes=0-9", "x-amz-content-sha256": EMPTY_SHA256 },
      payloadHash: EMPTY_SHA256,
      region: "us-east-1",
      service: "s3",
      accessKeyId: "AKIAIOSFODNN7EXAMPLE",
      secretAccessKey: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
      date: new Date("2013-05-24T00:00:00Z"),
    });
    expect(s.signature).toBe("f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41");
  });

  it("sorts and encodes query parameters", () => {
    expect(canonicalQuery([["prefix", "assets/"], ["list-type", "2"], ["continuation-token", "a+b/c="]])).toBe("continuation-token=a%2Bb%2Fc%3D&list-type=2&prefix=assets%2F");
  });
});

describe("the S3 client", () => {
  it("parses ListObjectsV2 pages", () => {
    const page = parseListObjects(
      `<?xml version="1.0"?><ListBucketResult><IsTruncated>true</IsTruncated><Contents><Key>assets/a&amp;b.glb</Key></Contents><Contents><Key>audio/x.mp3</Key></Contents><NextContinuationToken>tok&lt;1</NextContinuationToken></ListBucketResult>`,
    );
    expect(page).toEqual({ keys: ["assets/a&b.glb", "audio/x.mp3"], truncated: true, token: "tok<1" });
    expect(parseListObjects("<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>")).toEqual({ keys: [], truncated: false, token: null });
  });

  it("signs path-style requests, pages through listings and never overwrites", async () => {
    const calls = [];
    const fetch = async (url, init) => {
      calls.push({ url, ...init });
      const u = new URL(url);
      if (init.method === "GET" && !u.searchParams.has("continuation-token"))
        return new Response("<ListBucketResult><IsTruncated>true</IsTruncated><Key>assets/a.glb</Key><NextContinuationToken>t2</NextContinuationToken></ListBucketResult>");
      if (init.method === "GET") return new Response("<ListBucketResult><IsTruncated>false</IsTruncated><Key>assets/b.glb</Key></ListBucketResult>");
      if (init.method === "PUT") return new Response("", { status: u.pathname.endsWith("taken.glb") ? 412 : 200 });
      return new Response("", { status: 404 });
    };
    const s3 = new S3Client({ endpoint: "http://127.0.0.1:9000", bucket: "shs", region: "us-east-1", accessKeyId: "k", secretAccessKey: "s", fetch });
    expect([...(await s3.listKeys("assets/"))]).toEqual(["assets/a.glb", "assets/b.glb"]);
    expect(calls[0].url).toBe("http://127.0.0.1:9000/shs?list-type=2&max-keys=1000&prefix=assets%2F");
    expect(calls[1].url).toContain("continuation-token=t2");
    expect(calls[0].headers.authorization).toMatch(/^AWS4-HMAC-SHA256 Credential=k\/\d{8}\/us-east-1\/s3\/aws4_request, SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature=[0-9a-f]{64}$/);

    expect(await s3.putNew("assets/new.0123.glb", Buffer.from("x"), { "content-type": "model/gltf-binary" })).toBe("uploaded");
    const put = calls.at(-1);
    expect(put.url).toBe("http://127.0.0.1:9000/shs/assets/new.0123.glb");
    expect(put.headers["if-none-match"]).toBe("*");
    expect(put.headers["x-amz-content-sha256"]).toBe(sha256Hex("x"));
    expect(put.headers.authorization).toContain("SignedHeaders=content-type;host;if-none-match;x-amz-content-sha256;x-amz-date");
    expect(await s3.putNew("assets/taken.glb", Buffer.from("x"))).toBe("exists");
  });
});

const dirs = [];
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});
const short = (b) => createHash("sha256").update(b).digest("hex").slice(0, 16);

/** A client/dist with a model, a texture, a KTX2 decoder file and the map. */
function dist() {
  const dir = mkdtempSync(join(tmpdir(), "shs-upload-"));
  dirs.push(dir);
  const files = {
    "assets/world/cell.glb": Buffer.from("glTF".repeat(2000)),
    "assets/world/textures/stone.png": Buffer.from("PNG...."),
    "assets/ktx2/zstddec.wasm": Buffer.from("wasm"),
  };
  const map = {};
  for (const [p, b] of Object.entries(files)) {
    mkdirSync(join(dir, p, ".."), { recursive: true });
    writeFileSync(join(dir, p), b);
    map[p] = short(b);
  }
  writeFileSync(join(dir, "asset-map.json"), mapFileJson(map));
  return { dir, files, map };
}

describe("the uploader", () => {
  it("names types and gzips only what shrinks", () => {
    expect(contentType("a/b.glb")).toBe("model/gltf-binary");
    expect(contentType("a/b.KTX2")).toBe("image/ktx2");
    expect(contentType("a/b.mp3")).toBe("audio/mpeg");
    expect(contentType("a/b.unknown")).toBe("application/octet-stream");
    const glb = objectFor("x.glb", Buffer.from("glTF".repeat(1000)));
    expect(glb.headers).toEqual({ "content-type": "model/gltf-binary", "cache-control": CACHE_CONTROL, "content-encoding": "gzip" });
    expect(gunzipSync(glb.body).toString()).toBe("glTF".repeat(1000));
    expect(objectFor("x.glb", Buffer.from("glTF".repeat(1000)), { gzip: false }).headers["content-encoding"]).toBeUndefined();
    expect(objectFor("x.png", Buffer.alloc(5000)).headers["content-encoding"]).toBeUndefined();
  });

  it("plans content-addressed keys and leaves the KTX2 decoder on the game's origin", () => {
    expect(plannedObjects({ "assets/a.glb": "0123456789abcdef", "assets/ktx2/zstddec.wasm": "1111111111111111" })).toEqual([
      { path: "assets/a.glb", hash: "0123456789abcdef", key: "assets/a.0123456789abcdef.glb" },
    ]);
  });

  it("uploads only what the bucket lacks, with its headers, and never what already exists", async () => {
    const { dir, files, map } = dist();
    const cellKey = `assets/world/cell.${map["assets/world/cell.glb"]}.glb`;
    const pngKey = `assets/world/textures/stone.${map["assets/world/textures/stone.png"]}.png`;
    const puts = [];
    const client = {
      listKeys: async (prefix) => new Set(prefix === "assets/" ? [pngKey, "assets/world/cell.ffffffffffffffff.glb"] : []),
      putNew: async (key, body, headers) => (puts.push({ key, body, headers }), "uploaded"),
    };
    const r = await uploadAssets({ dist: dir, client, log: () => {} });
    expect(r).toMatchObject({ planned: 2, present: 1, uploaded: 1 });
    expect(puts.map((p) => p.key)).toEqual([cellKey]);
    expect(puts[0].headers).toMatchObject({ "content-type": "model/gltf-binary", "cache-control": CACHE_CONTROL, "content-encoding": "gzip" });
    expect(gunzipSync(puts[0].body).equals(files["assets/world/cell.glb"])).toBe(true);

    // A dry run uploads nothing.
    puts.length = 0;
    expect((await uploadAssets({ dist: dir, client, dryRun: true, log: () => {} })).uploaded).toBe(0);
    expect(puts).toEqual([]);

    // A file rebuilt after the map was written is refused, not uploaded under the old key.
    writeFileSync(join(dir, "assets/world/cell.glb"), "changed");
    await expect(uploadAssets({ dist: dir, client, log: () => {} })).rejects.toThrow(/changed since the build/);
  });

  it("reads settings from the env file over the shell", () => {
    const { dir } = dist();
    const file = join(dir, "deploy.test.env");
    writeFileSync(file, "DEPLOY_ASSETS_BUCKET=from-file\nDEPLOY_ASSETS_S3_REGION=\n# DEPLOY_ASSETS_GZIP=0\nDEPLOY_ASSETS_PUBLIC_URL=https://assets.example.org/\n");
    const s = loadSettings({ DEPLOY_ASSETS_BUCKET: "from-shell", DEPLOY_ASSETS_ACCESS_KEY_ID: "id" }, file);
    expect(s).toMatchObject({ bucket: "from-file", region: "auto", accessKeyId: "id", publicUrl: "https://assets.example.org", gzip: true, verify: true });
    expect(loadSettings({}, undefined).bucket).toBe("");
  });

  it("checks the public URL like a browser on the game's origin", async () => {
    const bytes = Buffer.from("glTF".repeat(1000));
    const object = { key: "assets/a.0.glb", hash: short(bytes) };
    const answer = (body, headers) => async () => new Response(body, { headers });
    const good = { "access-control-allow-origin": "https://game.example.org", "cache-control": CACHE_CONTROL };
    expect(await verifyPublic({ publicUrl: "https://a", origin: "https://game.example.org", object, fetchFn: answer(bytes, good) })).toEqual([]);
    // Compressed twice on the way: fetch undoes one layer only.
    const twice = await verifyPublic({ publicUrl: "https://a", origin: "https://game.example.org", object, fetchFn: answer(gzipSync(bytes), good) });
    expect(twice.join()).toMatch(/gzipped twice/);
    const noCors = await verifyPublic({ publicUrl: "https://a", origin: "https://game.example.org", object, fetchFn: answer(bytes, { "cache-control": CACHE_CONTROL }) });
    expect(noCors.join()).toMatch(/Access-Control-Allow-Origin is missing/);
    const notFound = await verifyPublic({ publicUrl: "https://a", origin: "https://g", object, fetchFn: async () => new Response("", { status: 404 }) });
    expect(notFound).toEqual(["https://a/assets/a.0.glb: HTTP 404"]);
  });
});
