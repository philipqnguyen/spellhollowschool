// Spells L20: the words on our own effect chips (ui/effects.ts).
import { describe, expect, it } from "vitest";
import { chipWords } from "./effects";

describe("effect chip words", () => {
  it("offers to dismiss our own guardian, but not a duel ward's", () => {
    const [name, words] = chipWords("guardian");
    expect(name).toBe("Your guardian");
    expect(words).toContain("Cast it again to dismiss it");
    const [wardName, wardWords] = chipWords("guardian", true);
    expect(wardName).toBe("Guardian ward");
    expect(wardWords).not.toMatch(/dismiss/i);
    expect(wardWords).toContain("stops the next spell");
    // Other kinds do not care about the mode.
    expect(chipWords("petrified", true)).toEqual(chipWords("petrified"));
  });
});
