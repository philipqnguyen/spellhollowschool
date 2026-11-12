import { describe, expect, it } from "vitest";
import type { House } from "../wasm/sim_wasm";
import {
  CATALOG,
  CREATOR,
  PALETTE,
  defaultClothing,
  grantedDyes,
  hairStylesFor,
  hexToRgb01,
  isStarterFor,
  migrateHair,
  itemDef,
  paletteHex,
  starterClothing,
  starterItems,
  styleTags,
} from "./catalog";
import {
  appearanceKey,
  cloneAppearance,
  defaultAppearance,
  defaultBodyAppearance,
  feetInches,
  fromWire,
  heightByte,
  heightLabel,
  heightMetres,
  heightScale,
  setSlider,
  shapeKeyWeights,
  sliderShapeWeights,
  sliderValue,
  toWire,
  wearStarter,
} from "./appearance";

const HOUSES: House[] = ["roarwing", "burrowsby", "skytalon", "serpentyne"];

describe("catalog", () => {
  it("grants every house 14 clothing items and 12 furniture pieces", () => {
    for (const h of HOUSES) {
      const items = starterItems(h);
      expect(items.filter((i) => i.kind === "clothing")).toHaveLength(14);
      expect(items.filter((i) => i.kind === "furniture")).toHaveLength(12);
    }
  });

  it("limits house items to their house", () => {
    const robes = CATALOG.items.filter((i) => i.slot === "body" && i.tags.some((t) => t.startsWith("house:")));
    expect(robes).toHaveLength(4);
    for (const robe of robes) {
      const owners = HOUSES.filter((h) => isStarterFor(robe, h));
      expect(owners).toHaveLength(1);
    }
  });

  it("preselects one body, legs and shoes item per house and no hat", () => {
    for (const h of HOUSES) {
      expect(defaultClothing(h, "body")).toBeDefined();
      expect(defaultClothing(h, "legs")).toBeDefined();
      expect(defaultClothing(h, "shoes")).toBeDefined();
      expect(defaultClothing(h, "hat")).toBeUndefined();
      expect(starterClothing(h, "hat").length).toBeGreaterThan(0);
    }
  });

  it("dyes house channels in the owner's colours (ItemDef::granted_dyes)", () => {
    const jumper = CATALOG.items.find((i) => i.house_dyes?.secondary === "house_primary" && i.house_dyes?.trim === "house_secondary")!;
    for (const h of HOUSES) {
      const d = grantedDyes(jumper, h);
      expect(d[0]).toBe(jumper.dyes.primary);
      expect(d[1]).toBe(PALETTE.houses[h]!.primary);
      expect(d[2]).toBe(PALETTE.houses[h]!.secondary);
    }
    const plain = CATALOG.items.find((i) => !i.house_dyes)!;
    expect(grantedDyes(plain, "skytalon")).toEqual([plain.dyes.primary, plain.dyes.secondary, plain.dyes.trim]);
  });

  it("looks up items and palette colours", () => {
    const first = CATALOG.items[0]!;
    expect(itemDef(first.id)).toBe(first);
    expect(itemDef(65000)).toBeUndefined();
    expect(paletteHex(0)).toBe(PALETTE.colours[0]!.hex);
    expect(paletteHex(999)).toBe("#808080");
    expect(hexToRgb01("#ff8000")).toEqual([1, 128 / 255, 0]);
    expect(styleTags(first)).not.toContain("starter");
  });
});

describe("appearance", () => {
  it("round-trips between the HTTP and wasm spellings", () => {
    const a = defaultAppearance("skytalon");
    a.face = [1, 2, 3, 4, 5];
    a.eyeColour = 9;
    a.hairColour = 17;
    const back = fromWire(toWire(a));
    expect(back).toEqual(a);
    expect(toWire(a).outfit.body?.item_def).toBe(a.outfit.body?.itemDef);
    expect(toWire(a).eye_colour).toBe(9);
    expect("build" in toWire(a)).toBe(false);
  });

  it("reads looks stored before protocol 6: no build, Brown eyes, hair mapped per body", () => {
    const old = { body_type: 1, height: 40, build: [200, 0, 30], face: [128, 128, 128, 128, 128] as [number, number, number, number, number], skin_tone: 2, hair_style: 6, hair_colour: 3, outfit: { hat: null, body: null, legs: null, shoes: null } };
    const a = fromWire(old);
    expect(a.eyeColour).toBe(CREATOR.default_eye_colour);
    expect(CREATOR.eye_colours[a.eyeColour]!.name).toBe("Brown");
    expect(a.hairStyle).toBe(18); // High Ponytail → the new high ponytail
    expect("build" in a).toBe(false);
    expect(fromWire({ ...old, body_type: 0 }).hairStyle).toBe(16);
    // A style of the other body becomes the body's default.
    expect(fromWire({ ...old, hair_style: 15 }).hairStyle).toBe(17);
  });

  it("treats a NULL appearance as the creator defaults", () => {
    expect(fromWire(null)).toEqual(defaultBodyAppearance());
    const partial = fromWire({ body_type: 1, skin_tone: 3 });
    expect(partial.bodyType).toBe(1);
    expect(partial.skinTone).toBe(3);
    expect(partial.outfit.hat).toBeNull();
  });

  it("starts each house in its own robe with the default items", () => {
    for (const h of HOUSES) {
      const a = defaultAppearance(h);
      const robe = itemDef(a.outfit.body!.itemDef)!;
      expect(robe.tags).toContain(`house:${h}`);
      expect(a.outfit.legs).not.toBeNull();
      expect(a.outfit.shoes).not.toBeNull();
      expect(a.outfit.hat).toBeNull();
    }
  });

  it("wears starter items with their granted dyes", () => {
    const a = defaultAppearance("serpentyne");
    const hat = starterClothing("serpentyne", "hat").find((i) => i.house_dyes)!;
    const b = wearStarter(a, "hat", hat.id, "serpentyne");
    expect(b.outfit.hat).toEqual({ itemDef: hat.id, dyes: grantedDyes(hat, "serpentyne") });
    expect(a.outfit.hat).toBeNull(); // the input is not changed
    expect(wearStarter(b, "hat", null, "serpentyne").outfit.hat).toBeNull();
    // An item from another slot is not worn.
    expect(wearStarter(a, "hat", a.outfit.body!.itemDef, "serpentyne").outfit.hat).toBeNull();
  });

  it("keys equal appearances equally and different ones differently", () => {
    const a = defaultAppearance("roarwing");
    const b = cloneAppearance(a);
    expect(appearanceKey(a)).toBe(appearanceKey(b));
    b.outfit.body!.dyes[2] = 7;
    expect(appearanceKey(a)).not.toBe(appearanceKey(b));
    expect(a.outfit.body!.dyes[2]).not.toBe(7); // clone is deep
  });

  it("keeps finishes when cloning and tells finished looks apart", () => {
    const a = defaultAppearance("skytalon");
    a.outfit.body!.finish = "shimmer";
    // A clone kept only item and dyes before: the wardrobe mirror and the
    // Exchange preview showed finished garments plain.
    const b = cloneAppearance(a);
    expect(b.outfit.body!.finish).toBe("shimmer");
    expect(appearanceKey(b)).toBe(appearanceKey(a));
    const plain = cloneAppearance(a);
    delete plain.outfit.body!.finish;
    expect(appearanceKey(plain)).not.toBe(appearanceKey(a));
    const shifted = cloneAppearance(a);
    shifted.outfit.body!.finish = "colour_shift";
    expect(appearanceKey(shifted)).not.toBe(appearanceKey(a));
    // "none" spelled out is the same look as no finish at all.
    const none = cloneAppearance(plain);
    none.outfit.body!.finish = "none";
    expect(appearanceKey(none)).toBe(appearanceKey(plain));
    expect(toWire(a).outfit.body!.finish).toBe("shimmer");
    expect(fromWire(toWire(a)).outfit.body!.finish).toBe("shimmer");
  });

  it("maps sliders onto shape keys (one-sided and two-sided)", () => {
    // Creator v2: no build sliders; height takes no shape keys.
    expect(CREATOR.sliders.some((s) => s.id.startsWith("build") || (s.field as string) === "build")).toBe(false);
    const height = CREATOR.sliders.find((s) => s.field === "height")!;
    expect(height.shape_keys).toEqual([]);
    expect(sliderShapeWeights(height, 255)).toEqual([]);
    const oneSided = { ...height, field: "face" as const, byte: 0, shape_keys: ["k"] };
    expect(sliderShapeWeights(oneSided, 255)).toEqual([["k", 1]]);
    expect(sliderShapeWeights(oneSided, 0)).toEqual([["k", 0]]);
    const jaw = CREATOR.sliders.find((s) => s.id === "face_jaw")!;
    expect(sliderShapeWeights(jaw, 128)).toEqual([
      [jaw.shape_keys[0], 0],
      [jaw.shape_keys[1], 0],
    ]);
    expect(sliderShapeWeights(jaw, 0)[0]![1]).toBe(1);
    expect(sliderShapeWeights(jaw, 255)[1]![1]).toBe(1);

    const a = defaultBodyAppearance();
    setSlider(a, jaw, 64);
    setSlider(a, height, 3);
    expect(sliderValue(a, jaw)).toBe(64);
    expect(a.height).toBe(3);
    const w = shapeKeyWeights(a);
    expect(w.get(jaw.shape_keys[0]!)).toBeCloseTo(0.5);
    expect([...w.keys()].some((k) => k.startsWith("height") || k.startsWith("build"))).toBe(false);
  });

  it("maps the height byte onto the body's range", () => {
    const body = CREATOR.body_types[0]!;
    const a = defaultBodyAppearance();
    a.height = 0;
    expect(heightMetres(a)).toBeCloseTo(body.height_m[0]);
    a.height = 128;
    expect(heightMetres(a)).toBeCloseTo(body.default_height_m);
    a.height = 255;
    expect(heightMetres(a)).toBeCloseTo(body.height_m[1]);
  });

  it("gives Male and Female their own heights, a uniform scale of the default", () => {
    // Heights: male 1.60-1.88 m (1.78), female 1.52-1.80 m (1.68).
    const [male, female] = CREATOR.body_types;
    expect([male!.name, female!.name]).toEqual(["Male", "Female"]);
    expect([...male!.height_m, male!.default_height_m]).toEqual([1.6, 1.88, 1.78]);
    expect([...female!.height_m, female!.default_height_m]).toEqual([1.52, 1.8, 1.68]);
    const a = defaultBodyAppearance(CREATOR, 1);
    expect(a.bodyType).toBe(1);
    expect(heightScale(a)).toBeCloseTo(1);
    a.height = 255;
    expect(heightMetres(a)).toBeCloseTo(1.8);
    expect(heightScale(a)).toBeCloseTo(1.8 / 1.68);
    a.height = 0;
    expect(heightMetres(a)).toBeCloseTo(1.52);
    // The byte for a height, and the labels.
    for (const m of [1.52, 1.6, 1.68, 1.75, 1.8]) {
      a.height = heightByte(m, 1);
      expect(Math.abs(heightMetres(a) - m)).toBeLessThan(0.0015);
    }
    expect(feetInches(1.88)).toBe(`6'2"`);
    expect(feetInches(1.6)).toBe(`5'3"`);
    expect(feetInches(1.52)).toBe(`5'0"`);
    expect(feetInches(1.8)).toBe(`5'11"`);
    expect(heightLabel(1.78)).toBe(`1.78 m (5'10")`);
  });

  it("offers each body its own hair styles and maps retired ones", () => {
    expect(hairStylesFor(0).map((h) => h.id)).toEqual([14, 15, 16, 20, 21, 22]);
    // (Id 25 was removed on 2026-11-07: unknown ids fall back to the body's default.)
    expect(hairStylesFor(1).map((h) => h.id)).toEqual([17, 18, 19, 23, 24]);
    expect(migrateHair(25, 1)).toBe(17);
    expect(defaultBodyAppearance(CREATOR, 0).hairStyle).toBe(14);
    expect(defaultBodyAppearance(CREATOR, 1).hairStyle).toBe(17);
    // The contract's table, a few rows.
    expect(migrateHair(0, 0)).toBe(14);
    expect(migrateHair(0, 1)).toBe(19);
    expect(migrateHair(4, 1)).toBe(17);
    expect(migrateHair(11, 0)).toBe(15);
    expect(migrateHair(7, 1)).toBe(18);
    expect(migrateHair(16, 0)).toBe(16);
    expect(migrateHair(16, 1)).toBe(17);
    expect(migrateHair(24, 1)).toBe(24);
    expect(migrateHair(24, 0)).toBe(14);
    expect(migrateHair(21, 1)).toBe(17);
    expect(migrateHair(9999, 0)).toBe(14);
    for (const h of CREATOR.hair_styles) {
      if (h.retired) expect(h.asset).toBeUndefined();
      else expect(h.asset).toMatch(/^hair\/[a-z0-9_]+$/);
    }
  });

  it("lists one list each of hair and eye colours, Brown eyes by default", () => {
    expect(CREATOR.hair_colours.length).toBe(20);
    expect(CREATOR.hair_colours.map((c) => c.index)).toEqual([...Array(20).keys()]);
    expect(CREATOR.eye_colours.length).toBe(13);
    expect(CREATOR.eye_colours.slice(0, 7).map((c) => c.name)).toEqual(["Dark Brown", "Brown", "Hazel", "Amber", "Green", "Blue", "Grey"]);
    for (const c of [...CREATOR.hair_colours, ...CREATOR.eye_colours]) expect(c).not.toHaveProperty("fantasy");
    expect(CREATOR.eye_colours[CREATOR.default_eye_colour]!.name).toBe("Brown");
    for (const s of [...CREATOR.hair_colours, ...CREATOR.eye_colours]) expect(s.hex).toMatch(/^#[0-9A-Fa-f]{6}$/);
    expect(defaultBodyAppearance().eyeColour).toBe(1);
  });
});
