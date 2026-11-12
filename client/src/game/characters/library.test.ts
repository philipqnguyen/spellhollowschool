import { describe, expect, it } from "vitest";
import { defaultAppearance, wearStarter } from "../../content/appearance";
import { CREATOR, starterClothing } from "../../content/catalog";
import { hatCoversHair, PartLibrary, withManifest, type Fetcher } from "./library";
import { recipeFor, underFile } from "./recipe";
import { fixtureGlb } from "./fixtures";

const SPEEDS = { walk: 3, run: 6.5 };

/** A fake asset server: `files` maps paths to glb bytes; everything else is the SPA's index.html. */
function server(files: Record<string, ArrayBuffer>): { fetcher: Fetcher; requested: string[] } {
  const requested: string[] = [];
  const html = new TextEncoder().encode("<!doctype html><html></html>").buffer;
  return {
    requested,
    fetcher: async (url) => {
      requested.push(url);
      const path = url.replace(/^\/assets\//, "");
      const body = files[path];
      return { ok: true, arrayBuffer: async () => body ?? html };
    },
  };
}

describe("recipe", () => {
  it("names the fitted files for the body type, with the hat-friendly hair under a hat", () => {
    const a = defaultAppearance("roarwing");
    const r = recipeFor(a);
    expect(r.parts.map((p) => p.role)).toEqual(["base", "hair", "body", "legs", "shoes"]);
    expect(r.parts[0]!.files).toEqual(["characters/body_a.glb"]);
    expect(r.parts[1]!.files).toEqual([`${CREATOR.hair_styles[0]!.asset}_body_a.glb`]);
    expect(r.parts[2]!.files[0]).toBe("gear/body/robe_school_house_a_body_a.glb");
    expect(r.parts[2]!.dyes).toEqual(a.outfit.body!.dyes);

    const hat = starterClothing("roarwing", "hat")[0]!;
    const withHat = wearStarter({ ...a, bodyType: 1 }, "hat", hat.id, "roarwing");
    const r2 = recipeFor(withHat);
    const hair = r2.parts.find((p) => p.role === "hair")!;
    expect(hair.cap).toBe(true);
    expect(hair.files[0]).toMatch(/_cap_body_b\.glb$/);
    expect(hair.files[1]).toMatch(/_body_b\.glb$/);
    expect(underFile("gear/legs/trousers_school_a", r2.body)).toBe("gear/legs/trousers_school_a_under_body_b.glb");
  });

  it("skips items the catalog does not know and scales for height", () => {
    const a = defaultAppearance("burrowsby");
    a.outfit.hat = { itemDef: 9999, dyes: [0, 0, 0] };
    a.height = 255;
    const r = recipeFor(a);
    expect(r.parts.some((p) => p.role === "hat")).toBe(false);
    expect(r.scale).toBeCloseTo(1.88 / 1.78);
    // Female: her own range; and the eye colour tints the iris.
    a.bodyType = 1;
    a.hairStyle = 18;
    a.eyeColour = 5;
    const f = recipeFor(a);
    expect(f.scale).toBeCloseTo(1.8 / 1.68);
    expect(f.parts.find((p) => p.role === "hair")!.files).toEqual(["hair/ponytail_locks_body_b.glb"]);
    expect(f.eye.map((c) => Math.round(c * 255))).toEqual([0x4a, 0x7d, 0xb8]);
    // A style of the other body draws the body's own default, never a file of the other's.
    a.hairStyle = 14;
    expect(recipeFor(a).parts.find((p) => p.role === "hair")!.asset).toBe("hair/long_bangs");
  });
});

describe("PartLibrary", () => {
  it("falls back to a greybox rig and parts when the files are missing", async () => {
    const s = server({});
    const lib = new PartLibrary("/assets/", SPEEDS, s.fetcher);
    const r = recipeFor(defaultAppearance("skytalon"));
    const kit = await lib.rig(r.body);
    expect(kit.greybox).toBe(true);
    expect(kit.clips.size).toBe(11); // the nine animations (fly, hover), the floor sit and the floor-seat sit
    const parts = await lib.parts(r, kit);
    expect(parts.map((p) => p.role)).toEqual(["base", "hair", "body", "legs", "shoes"]);
    expect(parts.find((p) => p.role === "body")!.part.long).toBe(true); // a robe
    expect(lib.stats.missing).toBeGreaterThan(0);
    // With a greybox rig no gear files are fetched at all.
    expect(s.requested.some((u) => u.includes("gear/"))).toBe(false);
  });

  it("uses the art body and its skeleton when present, greybox only for missing gear", async () => {
    const r = recipeFor(defaultAppearance("serpentyne"));
    const legs = r.parts.find((p) => p.role === "legs")!;
    const s = server({
      "characters/body_a.glb": fixtureGlb({ animation: "idle" }),
      [legs.files[0]!]: fixtureGlb({ meshName: "trousers", extras: { hides: ["thighs"] } }),
    });
    const lib = new PartLibrary("/assets/", SPEEDS, s.fetcher);
    const kit = await lib.rig(r.body);
    expect(kit.greybox).toBe(false);
    expect(kit.rig.bones).toContain("DEF-spine.006");
    expect(kit.clips.get("idle")!.procedural).toBe(false);
    expect(kit.clips.get("walk")!.procedural).toBe(true);
    const parts = await lib.parts(r, kit);
    expect(parts.find((p) => p.role === "legs")!.part.hides).toEqual(["thighs"]);
    expect(parts.find((p) => p.role === "body")!.part.bones.every((b) => b.startsWith("DEF-") || b === "root")).toBe(true);
    // Files are fetched once.
    await lib.parts(r, kit);
    expect(s.requested.filter((u) => u.endsWith(legs.files[0]!))).toHaveLength(1);
  });

  it("prefers the _under legs under a long robe", async () => {
    const r = recipeFor(defaultAppearance("roarwing"));
    const body = r.parts.find((p) => p.role === "body")!;
    const legs = r.parts.find((p) => p.role === "legs")!;
    const s = server({
      "characters/body_a.glb": fixtureGlb(),
      [body.files[0]!]: fixtureGlb({ meshName: "robe", extras: { long: true } }),
      [legs.files[0]!]: fixtureGlb({ meshName: "full" }),
      [underFile(legs.asset, r.body)]: fixtureGlb({ meshName: "under" }),
    });
    const lib = new PartLibrary("/assets/", SPEEDS, s.fetcher);
    const kit = await lib.rig(r.body);
    const parts = await lib.parts(r, kit);
    expect(parts.find((p) => p.role === "legs")!.part.primitives[0]!.name).toBe("under");
  });

  it("takes hides and long from the art manifest when a file has none", async () => {
    const r = recipeFor(defaultAppearance("burrowsby"));
    const body = r.parts.find((p) => p.role === "body")!;
    const manifest = { version: 1, assets: { [body.asset]: { kind: "clothing", hides: ["torso", "upper_arms"], long: true } } };
    const s = server({
      "characters/body_a.glb": fixtureGlb(),
      [body.files[0]!]: fixtureGlb({ meshName: "robe" }),
      "characters/manifest.json": new TextEncoder().encode(JSON.stringify(manifest)).buffer,
    });
    const lib = new PartLibrary("/assets/", SPEEDS, s.fetcher);
    const parts = await lib.parts(r, await lib.rig(r.body));
    const robe = parts.find((p) => p.role === "body")!.part;
    expect(robe.hides).toEqual(["torso", "upper_arms"]);
    expect(robe.long).toBe(true);
    // A file's own extras win.
    const own = { ...robe, hides: ["feet"], long: false };
    expect(withManifest(own, { hides: ["torso"] }).hides).toEqual(["feet"]);
    expect(withManifest(own, undefined)).toBe(own);
  });

  it("keeps the styled hair under hair trims that hide nothing (a ribbon, a comb)", async () => {
    const base = recipeFor(defaultAppearance("skytalon"));
    const hair = base.parts.find((p) => p.role === "hair")!;
    const plain = hair.files[0]!;
    const cap = plain.replace(/_body_a\.glb$/, "_cap_body_a.glb");
    const withHat = (hat: string) => ({
      ...base,
      parts: [...base.parts.map((p) => (p.role === "hair" ? { ...p, cap: true, files: [cap, plain] } : p)), { role: "hat" as const, asset: hat, files: [`${hat}_body_a.glb`] }],
    });
    const manifest = { version: 1, assets: { "gear/hat/ribbon_bow_a": { kind: "clothing", hides: [] }, "gear/hat/pointed_a": { kind: "clothing", hides: ["scalp"] } } };
    const s = server({
      "characters/body_a.glb": fixtureGlb(),
      [plain]: fixtureGlb({ meshName: "styled" }),
      [cap]: fixtureGlb({ meshName: "capped" }),
      "characters/manifest.json": new TextEncoder().encode(JSON.stringify(manifest)).buffer,
    });
    const lib = new PartLibrary("/assets/", SPEEDS, s.fetcher);
    const kit = await lib.rig(base.body);
    const hairOf = async (hat: string) => (await lib.parts(withHat(hat), kit)).find((p) => p.role === "hair")!.part.primitives[0]!.name;
    expect(await hairOf("gear/hat/ribbon_bow_a")).toBe("styled");
    expect(await hairOf("gear/hat/pointed_a")).toBe("capped");
    expect(hatCoversHair(undefined)).toBe(true);
    // A style the manifest lists without a hat variant wears its own file under hats.
    const own = { ...manifest, assets: { ...manifest.assets, [hair.asset]: { kind: "hair", files: { body_a: plain } } } };
    const s2 = server({
      "characters/body_a.glb": fixtureGlb(),
      [plain]: fixtureGlb({ meshName: "styled" }),
      [cap]: fixtureGlb({ meshName: "capped" }),
      "characters/manifest.json": new TextEncoder().encode(JSON.stringify(own)).buffer,
    });
    const lib2 = new PartLibrary("/assets/", SPEEDS, s2.fetcher);
    const kit2 = await lib2.rig(base.body);
    const hair2 = (await lib2.parts(withHat("gear/hat/pointed_a"), kit2)).find((p) => p.role === "hair")!.part.primitives[0]!.name;
    expect(hair2).toBe("styled");
    expect(s2.requested.some((u) => u.includes("_cap_"))).toBe(false);
  });

  it("does not count the optional shared clip files as missing parts", async () => {
    const r = recipeFor(defaultAppearance("skytalon"));
    const s = server({ "characters/body_a.glb": fixtureGlb({ animation: "idle" }) });
    const lib = new PartLibrary("/assets/", SPEEDS, s.fetcher);
    await lib.rig(r.body);
    expect(s.requested.some((u) => u.endsWith("characters/animations.glb"))).toBe(true);
    expect(lib.stats.missing).toBe(0);
  });
});
