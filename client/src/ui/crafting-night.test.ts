// Night-only brews. The server brews a recipe whose output is
// tagged `night` only while the game clock says night, and answers 403
// `night_only` otherwise; the client says so in its own words, and marks
// those recipes in the list.

import { describe, expect, it } from "vitest";
import { craftingError, isNightRecipe, nightHours, RECIPES } from "./crafting-content";

describe("night-only brews", () => {
  it("knows the night recipes by their output's tag (331-333)", () => {
    const night = RECIPES.recipes.filter((r) => isNightRecipe(r)).map((r) => r.id);
    expect(night.sort()).toEqual([331, 332, 333]);
  });

  it("names the night's hours from the schedule", () => {
    expect(nightHours()).toBe("22:00 to 06:00");
    expect(nightHours([{ period: "day", starts: "07:00" }, { period: "night", starts: "19:30" }])).toBe("19:30 to 07:00");
    expect(nightHours([{ period: "day", starts: "07:00" }])).toBe("after dark");
  });

  it("gives the server's night_only error its own words", () => {
    const text = craftingError("night_only", "That brew can only be made at night.");
    expect(text).toMatch(/^That brew can only be made at night \(22:00 to 06:00 by the castle clock\)\./);
    expect(text).toContain("Nothing was used up");
    expect(craftingError("something_new", "The server's words")).toBe("The server's words");
  });
});
