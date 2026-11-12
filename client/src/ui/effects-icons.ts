// Line icons for the crafting, potion and effect UI, drawn as inline SVG in
// the style of the wardrobe's coat hanger (stroke only, `.hud-icon`), so no
// image files are needed.

import type { EffectKind } from "../wasm/sim_wasm";

const NS = "http://www.w3.org/2000/svg";

/** An SVG icon from path data in a 24 × 24 box. */
export function lineIcon(paths: readonly string[], cls = "hud-icon"): SVGSVGElement {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add(cls);
  for (const d of paths) {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}

/** A potion bottle: the HUD's potions button. */
export const POTION_ICON = ["M9.5 3h5", "M10.5 3v4.2L6.4 14a5.6 5.6 0 0 0 4.8 8h1.6a5.6 5.6 0 0 0 4.8-8l-4.1-6.8V3", "M7.6 14.6h8.8"];
/** A closed spellbook with a spark on its cover: the spellbook button. */
export const SPELLBOOK_ICON = ["M5 19.5V4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5Z", "M5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3", "M12 6.8l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9Z"];
/** A wand with a spark: the skills button. */
export const SKILLS_ICON = ["M4 20 14.5 9.5", "M13 8l3 3", "M17.5 2.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7Z", "M20.5 9.5l.4.9.9.4-.9.4-.4.9-.4-.9-.9-.4.9-.4Z"];

/** One small glyph per potion effect, for the HUD timers. */
export const EFFECT_ICONS: Record<EffectKind, string[]> = {
  glow: ["M12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9Z", "M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"],
  chameleon: ["M12 20a8 8 0 1 1 8-8c0 2.2-1.8 3-3.4 3H15a2 2 0 0 0-1.6 3.2A1.2 1.2 0 0 1 12 20Z", "M8 10.5h.01M11 7.5h.01M15.5 8.5h.01"],
  shrinking: ["M12 3v8M8.5 7.5 12 11l3.5-3.5", "M12 21v-5M9.5 18.5 12 16l2.5 2.5", "M5 13h14"],
  swelling: ["M12 11V3M8.5 6.5 12 3l3.5 3.5", "M12 13v8M8.5 17.5 12 21l3.5-3.5", "M5 12h14"],
  babbling: ["M4 5h16v10H9l-5 4Z", "M8 9.5c1-1 2 1 3 0s2 1 3 0 2 1 2 0"],
  mimic_draught: ["M8.5 9a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z", "M15.5 6a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z", "M4 21c.8-2.4 2.4-3.6 4.5-3.6M20 18c-.8-2.4-2.4-3.6-4.5-3.6"],
  fortunes_draught: ["M12 12c-2.5-3-5.5-3-5.5-.5S9.5 14 12 12Zm0 0c3-2.5 3-5.5.5-5.5S10 9.5 12 12Zm0 0c2.5 3 5.5 3 5.5.5S14.5 10 12 12Zm0 0c-3 2.5-3 5.5-.5 5.5S14 14.5 12 12Z", "M12 12c1.5 3 3 6 5 9"],
  pick_me_up_tonic: ["M7 20c-2-2 2-3 0-5s2-3 0-5", "M12 20c-2-2 2-3 0-5s2-3 0-5s2-3 0-5", "M17 20c-2-2 2-3 0-5s2-3 0-5"],
  invisibility: ["M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z", "M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z", "M4 20 20 4"],
  // Placeholder (the fortune chip): a small star.
  fortune: ["M12 4l2.2 4.6 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L4.8 9.3l5-.7Z"],
  // Placeholder: a feather.
  featherlight: ["M19 5C12 5 7 10 6 18", "M19 5c0 6-4 11-11 12", "M4 20l2-2"],
  // Spells L20: a figure stiff as a board, a snail's spiral, a dye drop with a swirl, a hare's head in silver.
  petrified: ["M12 3.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z", "M10 9h4v11.5h-4Z", "M6.5 21h11"],
  slowed: ["M12 12a3 3 0 1 1 3-3 6 6 0 1 1-6 6", "M3 20h18"],
  recoloured: ["M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11Z", "M9.5 14.5a2.5 2.5 0 0 0 4 1.5"],
  guardian: ["M9 3c-1 3-.5 6 1 8M15 3c1 3 .5 6-1 8", "M7 15a5 5 0 0 1 10 0c0 3-2.2 5.5-5 5.5S7 18 7 15Z", "M10.3 15h.01M13.7 15h.01"],
};
