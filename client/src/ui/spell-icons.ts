// Line glyphs for the spells, one per spell of data/spells.json, drawn from
// data/spell_glyphs.json (keyed by the spell's `key`, so the spell names
// stay in data): on the social hotbar's slots (ui/hotbar.ts), the
// spellbook's rows, the duel spell bar (ui/duel.ts) and the loadout
// (ui/loadout.ts). Stroke-only paths in a 24 × 24 box, like the HUD's other
// icons (effects-icons.ts `lineIcon`); CSS strokes them in the spell's
// colour (`--spell`). spell-icons.test.ts checks every spell has its own.

import glyphsJson from "../../../data/spell_glyphs.json";
import { lineIcon } from "./effects-icons";

export interface SpellGlyph {
  /** What it shows, in a few words. */
  about: string;
  /** SVG path data in a 24 × 24 box. */
  paths: readonly string[];
}

/** Every spell's glyph, by spell key. */
export const SPELL_GLYPHS: Readonly<Record<string, SpellGlyph>> = glyphsJson.glyphs;

/** A generic spark, for a spell the glyph file doesn't know yet. */
const SPARK = ["M12 4l1.8 4.6L18.5 10l-4.7 1.6L12 16l-1.8-4.4L5.5 10l4.7-1.4Z", "M18 16.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6Z"];

/** The glyph paths of the spell with key `key` (a spark for one without its own). */
export function glyphPaths(spell: { key: string }): readonly string[] {
  return Object.hasOwn(SPELL_GLYPHS, spell.key) ? SPELL_GLYPHS[spell.key]!.paths : SPARK;
}

/** The glyph of `spell` as an SVG, stroked in its colour by CSS (`.spell-icon`, `--spell`). */
export function spellIcon(spell: { key: string }): SVGSVGElement {
  return lineIcon(glyphPaths(spell), "spell-icon");
}
