import { describe, expect, it } from "vitest";
import { CREATOR, PALETTE, clothingDef, grantedDyes, hairStylesFor, itemDef, starterClothing } from "../content/catalog";
import {
  chooseHouse,
  createRequest,
  goTo,
  initialState,
  nextStep,
  outfitChoices,
  randomLook,
  setBodyType,
  setEyeColour,
  setHairColour,
  setHairStyle,
  setOutfit,
  setSkinTone,
  setSliderById,
  submitProblem,
} from "./creator-state";
import { sliderEnds } from "./creator";

describe("creator state", () => {
  it("starts dressed in the house's defaults", () => {
    const s = initialState("burrowsby");
    expect(itemDef(s.appearance.outfit.body!.itemDef)!.tags).toContain("house:burrowsby");
    expect(s.step).toBe("house");
  });

  it("re-dresses the outfit when the house changes", () => {
    let s = initialState("roarwing");
    const hat = starterClothing("roarwing", "hat").find((i) => i.house_dyes)!;
    s = setOutfit(s, "hat", hat.id);
    s = setSliderById(s, "face_jaw", 40);
    const r = chooseHouse(s, "serpentyne");
    // The house robe is swapped for the new house's; body and face stay.
    expect(itemDef(r.appearance.outfit.body!.itemDef)!.tags).toContain("house:serpentyne");
    expect(r.appearance.face[0]).toBe(40);
    // A shared item stays, re-dyed in the new house's colours.
    expect(r.appearance.outfit.hat!.itemDef).toBe(hat.id);
    expect(r.appearance.outfit.hat!.dyes).toEqual(grantedDyes(hat, "serpentyne"));
    expect(r.appearance.outfit.hat!.dyes).toContain(PALETTE.houses.serpentyne!.primary);
    expect(chooseHouse(r, "serpentyne")).toBe(r);
  });

  it("only allows starter clothing of the house, and always something on body, legs and feet", () => {
    let s = initialState("skytalon");
    const rival = starterClothing("roarwing", "body").find((i) => i.tags.includes("house:roarwing"))!;
    expect(setOutfit(s, "body", rival.id)).toBe(s);
    expect(setOutfit(s, "legs", null)).toBe(s);
    s = setOutfit(s, "hat", null);
    expect(s.appearance.outfit.hat).toBeNull();
    expect(outfitChoices(s, "body").every((i) => clothingDef(i.id, "body"))).toBe(true);
    expect(outfitChoices(s, "body").some((i) => i.tags.includes("house:roarwing"))).toBe(false);
  });

  it("ignores choices outside the creator data", () => {
    const s = initialState();
    expect(setBodyType(s, 9)).toBe(s);
    expect(setSkinTone(s, 99)).toBe(s);
    expect(setHairStyle(s, 999)).toBe(s);
    expect(setSliderById(s, "nope", 3)).toBe(s);
    expect(setBodyType(s, 1).appearance.bodyType).toBe(1);
  });

  it("offers ten skin tones, Porcelain to Mocha (Espresso and Ebony are gone)", () => {
    expect(CREATOR.skin_tones.map((t) => t.name)).toEqual(["Porcelain", "Ivory", "Warm Beige", "Sand", "Honey", "Olive", "Caramel", "Bronze", "Chestnut", "Mocha"]);
    const s = initialState();
    expect(setSkinTone(s, 9).appearance.skinTone).toBe(9);
    expect(setSkinTone(s, 10)).toBe(s);
    expect(setSkinTone(s, 11)).toBe(s);
  });

  it("keeps hair styles to their body and remembers each body's choice", () => {
    let s = initialState("burrowsby");
    expect(s.appearance.bodyType).toBe(0);
    expect(s.appearance.hairStyle).toBe(14);
    // Female styles are not offered to the Male body.
    expect(setHairStyle(s, 18)).toBe(s);
    s = setHairStyle(s, 16);
    expect(s.appearance.hairStyle).toBe(16);
    const height = setSliderById(s, "height", 200).appearance.height;
    s = setSliderById(s, "height", 200);
    s = setBodyType(s, 1);
    expect(s.appearance.bodyType).toBe(1);
    expect(s.appearance.hairStyle).toBe(17); // her default
    expect(s.appearance.height).toBe(height); // relative: kept
    s = setHairStyle(s, 19);
    s = setBodyType(s, 0);
    expect(s.appearance.hairStyle).toBe(16); // his last choice
    s = setBodyType(s, 1);
    expect(s.appearance.hairStyle).toBe(19); // hers
    // No build sliders to set.
    expect(setSliderById(s, "build_slim", 200)).toBe(s);
  });

  it("chooses eye and hair colours from one list each", () => {
    let s = initialState();
    expect(s.appearance.eyeColour).toBe(1);
    s = setEyeColour(s, 11);
    expect(s.appearance.eyeColour).toBe(11);
    expect(setEyeColour(s, 13)).toBe(s);
    s = setHairColour(s, 18);
    expect(CREATOR.hair_colours[s.appearance.hairColour]!.name).toBe("Rose Pink");
  });

  it("randomises within range and keeps the outfit", () => {
    const s = initialState("skytalon");
    let seed = 1;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    const r = randomLook(s, rand);
    expect(r.appearance.outfit).toEqual(s.appearance.outfit);
    // A hair style of the body it rolled, and colours from the lists.
    expect(hairStylesFor(r.appearance.bodyType).some((h) => h.id === r.appearance.hairStyle)).toBe(true);
    expect(CREATOR.eye_colours[r.appearance.eyeColour]).toBeDefined();
    expect(CREATOR.hair_colours[r.appearance.hairColour]).toBeDefined();
    for (const v of [...r.appearance.face, r.appearance.height]) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(255);
    }
  });

  it("offers only what the body may wear, and re-checks the outfit when the body changes", () => {
    // Body 0 is the male body, body 1 the female.
    let s = initialState("serpentyne");
    expect(s.appearance.bodyType).toBe(0);
    const skirt = itemDef(302)!;
    expect(skirt.wear).toBe("female");
    expect(outfitChoices(s, "legs").map((i) => i.id)).not.toContain(302);
    expect(setOutfit(s, "legs", 302)).toBe(s);
    s = setBodyType(s, 1);
    expect(outfitChoices(s, "legs").map((i) => i.id)).toContain(302);
    s = setOutfit(s, "legs", 302);
    expect(s.appearance.outfit.legs!.itemDef).toBe(302);
    // Back to the male body: the skirt goes, the default trousers come back.
    s = setBodyType(s, 0);
    expect(s.appearance.outfit.legs!.itemDef).toBe(301);
    expect(s.appearance.outfit.legs!.dyes).toEqual(grantedDyes(itemDef(301)!, "serpentyne"));
    // The rest of the outfit stays.
    expect(s.appearance.outfit.body).toEqual(initialState("serpentyne").appearance.outfit.body);
    // A random look that changes the body re-checks it too.
    const f = setOutfit(setBodyType(initialState("skytalon"), 1), "legs", 302);
    for (let i = 0; i < 20; i++) {
      let seed = i + 1;
      const r = randomLook(f, () => (seed = (seed * 16807) % 2147483647) / 2147483647);
      expect(r.appearance.outfit.legs!.itemDef).toBe(r.appearance.bodyType === 1 ? 302 : 301);
    }
  });

  it("walks the steps and checks the name before sending", () => {
    let s = initialState("roarwing");
    expect(nextStep(s, -1)).toBeNull();
    expect(nextStep(s, 1)).toBe("body");
    s = goTo(s, "name");
    expect(nextStep(s, 1)).toBeNull();
    expect(submitProblem(s)).not.toBeNull();
    s = { ...s, name: "  Mary-Kate O'Neil " };
    expect(submitProblem(s)).toBeNull();
    const req = createRequest(s);
    expect(req).toMatchObject({ name: "Mary-Kate O'Neil", house: "Roarwing" });
    expect(req.appearance).toBe(s.appearance);
  });

  it("labels slider ends from their shape keys", () => {
    expect(sliderEnds(CREATOR.sliders.find((s) => s.id === "face_jaw")!)).toEqual(["narrow", "wide"]);
    expect(sliderEnds(CREATOR.sliders.find((s) => s.id === "height")!)).toEqual(["shorter", "taller"]);
  });
});
