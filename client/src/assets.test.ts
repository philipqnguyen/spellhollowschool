import { describe, expect, it, vi } from "vitest";

import { assetCdnFromPage, assetHash, assetUrl, createAssetResolver, hashedKey, normalizeAssetPath } from "./assets";
import { hashedKey as uploaderKey } from "../scripts/asset-map.mjs";

const MAP = {
  "assets/world/manifest.json": "0123456789abcdef",
  "assets/instances/dorm_a.glb": "fedcba9876543210",
  "assets/ktx2/zstddec.wasm": "1111222233334444",
  "audio/sfx/foot/stone_0.mp3": "aaaabbbbccccdddd",
};

describe("asset URLs", () => {
  it("normalizes logical paths", () => {
    expect(normalizeAssetPath("/assets/world/manifest.json")).toBe("assets/world/manifest.json");
    expect(normalizeAssetPath("assets/world/../instances/./dorm_a.glb")).toBe("assets/instances/dorm_a.glb");
    expect(normalizeAssetPath("assets//world/")).toBe("assets/world");
  });

  it("puts the hash before the last extension, as the uploader does", () => {
    for (const [path, key] of [
      ["assets/world/cell_12.glb", "assets/world/cell_12.0123456789abcdef.glb"],
      ["assets/characters/face.v2.png", "assets/characters/face.v2.0123456789abcdef.png"],
      ["assets/LICENSE", "assets/LICENSE.0123456789abcdef"],
      ["assets/.hidden", "assets/.hidden.0123456789abcdef"],
      ["assets/dir.d/file", "assets/dir.d/file.0123456789abcdef"],
    ] as const) {
      expect(hashedKey(path, "0123456789abcdef")).toBe(key);
      expect(uploaderKey(path, "0123456789abcdef")).toBe(key);
    }
  });

  it("loads from the bucket by content-addressed key when there is one", () => {
    const url = createAssetResolver({ map: MAP, cdn: "https://assets.example.org", base: "/" });
    expect(url("assets/world/manifest.json")).toBe("https://assets.example.org/assets/world/manifest.0123456789abcdef.json");
    // Manifest entries relative to their folder resolve to the file's own key.
    expect(url("assets/world/../instances/dorm_a.glb")).toBe("https://assets.example.org/assets/instances/dorm_a.fedcba9876543210.glb");
    expect(url("audio/sfx/foot/stone_0.mp3")).toBe("https://assets.example.org/audio/sfx/foot/stone_0.aaaabbbbccccdddd.mp3");
    // Worker files stay on the game's origin, versioned.
    expect(url("assets/ktx2/zstddec.wasm", { sameOrigin: true })).toBe("/assets/ktx2/zstddec.wasm?v=1111222233334444");
  });

  it("loads from the game server with ?v= without a bucket", () => {
    const url = createAssetResolver({ map: MAP, cdn: null, base: "/game" });
    expect(url("assets/world/manifest.json")).toBe("/game/assets/world/manifest.json?v=0123456789abcdef");
    expect(url("/audio/sfx/foot/stone_0.mp3")).toBe("/game/audio/sfx/foot/stone_0.mp3?v=aaaabbbbccccdddd");
  });

  it("falls back to the plain path for unknown files, warning once per path when the map is not empty", () => {
    const warn = vi.fn();
    const url = createAssetResolver({ map: MAP, cdn: "https://assets.example.org", base: "/", warn });
    expect(url("assets/icons/nope.png")).toBe("/assets/icons/nope.png");
    expect(url("assets/icons/nope.png")).toBe("/assets/icons/nope.png");
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]![0]).toMatch(/assets\/icons\/nope\.png is not in the asset map/);
    // Development: an empty map is quiet.
    const quiet = vi.fn();
    const dev = createAssetResolver({ map: {}, cdn: null, base: "/", warn: quiet });
    expect(dev("assets/world/manifest.json")).toBe("/assets/world/manifest.json");
    expect(quiet).not.toHaveBeenCalled();
    // Object prototype names are not assets.
    expect(dev("constructor")).toBe("/constructor");
    // URLs (tests load data: GLBs) pass through.
    expect(url("data:model/gltf-binary;base64,Z2xURg==")).toBe("data:model/gltf-binary;base64,Z2xURg==");
    expect(url("blob:http://localhost/x")).toBe("blob:http://localhost/x");
    expect(url("https://example.org/a.glb")).toBe("https://example.org/a.glb");
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("reads the bucket from the page's meta tag", () => {
    const doc = (content: string | null) => ({
      querySelector: () => (content === null ? null : ({ content } as HTMLMetaElement)),
    });
    expect(assetCdnFromPage(doc("https://assets.example.org/") as unknown as Document)).toBe("https://assets.example.org");
    expect(assetCdnFromPage(doc("http://127.0.0.1:9000/shs-assets") as unknown as Document)).toBe("http://127.0.0.1:9000/shs-assets");
    expect(assetCdnFromPage(doc("") as unknown as Document)).toBeNull();
    expect(assetCdnFromPage(doc("javascript:alert(1)") as unknown as Document)).toBeNull();
    expect(assetCdnFromPage(doc(null) as unknown as Document)).toBeNull();
    expect(assetCdnFromPage(undefined)).toBeNull();
  });

  it("is the plain path in development and tests (an empty map, no bucket)", () => {
    expect(assetUrl("assets/world/manifest.json")).toBe("/assets/world/manifest.json");
    expect(assetHash("assets/world/manifest.json")).toBeUndefined();
  });
});
