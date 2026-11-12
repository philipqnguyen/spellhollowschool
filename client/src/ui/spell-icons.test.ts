import { describe, expect, it } from "vitest";
import { SPELLS } from "../game/spells/data";
import { glyphPaths, SPELL_GLYPHS } from "./spell-icons";

describe("spell glyphs", () => {
  it("gives every spell of data/spells.json a glyph of its own", () => {
    for (const s of SPELLS) {
      const g = SPELL_GLYPHS[s.key];
      expect(g, `${s.key} has no glyph in data/spell_glyphs.json`).toBeDefined();
      expect(g!.about.trim(), s.key).not.toBe("");
      expect(g!.paths.length, s.key).toBeGreaterThan(0);
      expect(glyphPaths(s)).toBe(g!.paths);
    }
  });

  it("draws no two spells alike", () => {
    const seen = new Map<string, string>();
    for (const s of SPELLS) {
      const d = glyphPaths(s).join(" ").replace(/\s+/g, " ").trim();
      expect(seen.get(d), `${s.key} has the same glyph as ${seen.get(d)}`).toBeUndefined();
      seen.set(d, s.key);
    }
    expect(seen.size).toBe(SPELLS.length);
  });

  it("has no glyph for a spell that doesn't exist", () => {
    const keys = new Set(SPELLS.map((s) => s.key));
    expect(Object.keys(SPELL_GLYPHS).filter((k) => !keys.has(k))).toEqual([]);
  });

  it("keeps to path data in the 24 × 24 box", () => {
    for (const [key, g] of Object.entries(SPELL_GLYPHS)) {
      for (const d of g.paths) {
        expect(d, key).toMatch(/^M[0-9MmLlHhVvCcSsQqTtAaZz .,-]+$/);
        // Absolute coordinates stay inside the box (relative ones are small steps).
        for (const n of d.match(/-?\d*\.?\d+/g) ?? []) expect(Math.abs(Number(n)), `${key}: ${d}`).toBeLessThanOrEqual(24);
      }
    }
  });

  it("falls back to a spark for a spell the file doesn't know", () => {
    expect(glyphPaths({ key: "not-a-spell" }).length).toBeGreaterThan(0);
    expect(glyphPaths({ key: "toString" })).toEqual(glyphPaths({ key: "not-a-spell" }));
  });
});
