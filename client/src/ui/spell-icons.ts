// Line glyphs for the spells that go on the social hotbar (ui/hotbar.ts),
// shown on its slots and on the spellbook's rows, one per kind of social
// spell (data/spells.json `social.kind`), so a new spell of a known kind
// gets its glyph from the data. Stroke-only paths in a 24 × 24 box, like
// the HUD's other icons (effects-icons.ts `lineIcon`); CSS strokes them in
// the spell's colour (`--spell`). Original drawings.

import type { SocialSpell, SpellDef } from "../game/spells/data";
import { lineIcon } from "./effects-icons";

type HotbarKind = Exclude<SocialSpell["kind"], "flourish">;

export const SPELL_GLYPHS: Record<HotbarKind, readonly string[]> = {
  // Lucerna: a wand whose tip shines.
  light: ["M4 20 12.2 11.8", "M13 9a2 2 0 1 0 4 0 2 2 0 1 0-4 0Z", "M15 3.2v1.6M15 13.2v1.6M9.2 9h1.6M19.2 9h1.6M10.9 4.9l1.1 1.1M18 12l1.1 1.1M19.1 4.9 18 6"],
  // Elevara: a chest lifted off the floor, rising.
  levitate: ["M7 4.5h10v6.5H7Z", "M7 7.5h10", "M12 6.5v2", "M9 16.5l3-2.5 3 2.5", "M4.5 20.5h15"],
  // Tinctura: a robe with a swash of new colour across it.
  recolour: ["M9.5 3.5h5l4.5 3.5-2.3 3.2-2.2-1.4V20.5H9.5V8.8L7.3 10.2 5 7Z", "M9.5 14.2c1.6-1.4 3.4.9 5-.6"],
  // Volucra: three small birds on the wing.
  birds: ["M2.5 11c1.6-2 3.4-2 4.5 0 1.1-2 2.9-2 4.5 0", "M12 6c1.3-1.6 2.7-1.6 3.6 0 .9-1.6 2.3-1.6 3.6 0", "M11.5 17.5c1.3-1.6 2.7-1.6 3.6 0 .9-1.6 2.3-1.6 3.6 0"],
  // Aperio: a hidden doorway that shines.
  reveal: ["M7.5 20.5v-9a4.5 4.5 0 0 1 9 0v9", "M4 20.5h16", "M12 13.5v4", "M12 2.5v2M5.2 5.4l1.3 1.3M18.8 5.4l-1.3 1.3M3 11h1.8M19.2 11H21"],
  // Rigora: two links of a chain.
  bind: ["M10.3 13.7 8.2 15.8a2.9 2.9 0 0 1-4.1-4.1l3-3a2.9 2.9 0 0 1 4.1 0", "M13.7 10.3l2.1-2.1a2.9 2.9 0 0 1 4.1 4.1l-3 3a2.9 2.9 0 0 1-4.1 0", "M9.5 14.5l5-5"],
  // Tardara: an hourglass.
  slow: ["M6.5 3.5h11M6.5 20.5h11", "M8 3.5c0 4.5 4 5.5 4 8.5s-4 4-4 8.5", "M16 3.5c0 4.5-4 5.5-4 8.5s4 4 4 8.5", "M10 18.5h4"],
  // Adveni: a horseshoe magnet that draws.
  pull: ["M6 4v8a6 6 0 0 0 12 0V4", "M10 4v8a2 2 0 0 0 4 0V4", "M6 8h4M14 8h4"],
  // Dissolvo: a ring of magic coming undone.
  dispel: ["M6.3 8A7 7 0 0 1 12 5", "M16.2 6.4A7 7 0 0 1 19 12", "M17.7 16A7 7 0 0 1 12 19", "M7.8 17.6A7 7 0 0 1 5 12", "M10.3 10.3l3.4 3.4M13.7 10.3l-3.4 3.4"],
  // Custodio: a shield with the guardian's light in it.
  guardian: ["M12 3l7 3v5.2c0 4.4-2.9 7.7-7 9.8-4.1-2.1-7-5.4-7-9.8V6Z", "M12 8.2l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9Z"],
};

/** A generic spark, for a hotbar spell of a kind with no glyph of its own. */
const SPARK = ["M12 4l1.8 4.6L18.5 10l-4.7 1.6L12 16l-1.8-4.4L5.5 10l4.7-1.4Z", "M18 16.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6Z"];

/** The glyph paths of `s` (a spark for one without its own). */
export function glyphPaths(s: SpellDef): readonly string[] {
  const k = s.social.kind;
  return k === "flourish" ? SPARK : (SPELL_GLYPHS[k] ?? SPARK);
}

/** The glyph of `s` as an SVG, stroked in its colour by CSS (`.spell-icon`, `--spell`). */
export function spellIcon(s: SpellDef): SVGSVGElement {
  return lineIcon(glyphPaths(s), "spell-icon");
}
