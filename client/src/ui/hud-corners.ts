// The HUD's corners, measured (docs/architecture.md "Client"):
// what they mean for nameplates and for the CSS the rest of the HUD
// is placed by. Pure, unit tested in hud-layout.test.ts; ui/hud.ts measures.

import type { Keepout } from "./nameplates";

/** A box on screen (px from the window's top left). */
export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** The HUD's two corners as measured, and the screen. */
export interface CornerBoxes {
  /** The top-right stack: the name and counts panel and the level and clock strip under it. */
  corner: Box | null;
  /** The bottom-right menu bar, and the emote bar above it (null when not shown). */
  dock: Box | null;
  emotes: Box | null;
  /** Other boxes along the top edge plates keep below (the party list at the top left); null when hidden. */
  topLeft?: (Box | null)[];
  width: number;
  height: number;
}

/**
 * What the corners mean for the rest of the HUD (pure): the nameplate keep-outs
 * and the CSS custom properties (px) the other parts of the HUD are placed by.
 *
 * - `--hud-corner-bottom`: the top-right stack's bottom edge (windows and
 *   panels that open at the right go below it);
 * - `--hud-dock-h`, `--hud-dock-w`: how far the menu bar reaches up from the
 *   bottom edge and in from the right edge (the Settings menu opens above it);
 * - `--hud-br-h`: how far the menu bar and the emote bar together reach up
 *   (panels at the bottom right sit above both).
 */
export function hudCorners(b: CornerBoxes): { keepouts: Keepout[]; vars: Record<string, string> } {
  const keepouts: Keepout[] = [];
  const vars: Record<string, string> = {};
  if (b.corner) {
    keepouts.push({ left: b.corner.left, right: b.corner.right, bottom: b.corner.bottom, edge: "top" });
    vars["--hud-corner-bottom"] = `${Math.round(b.corner.bottom)}px`;
  }
  for (const t of b.topLeft ?? []) {
    if (t) keepouts.push({ left: t.left, right: t.right, bottom: t.bottom, edge: "top" });
  }
  if (b.dock) {
    vars["--hud-dock-h"] = `${Math.round(b.height - b.dock.top)}px`;
    vars["--hud-dock-w"] = `${Math.round(b.width - b.dock.left)}px`;
    const top = Math.min(b.dock.top, b.emotes?.top ?? Infinity);
    vars["--hud-br-h"] = `${Math.round(b.height - top)}px`;
    keepouts.push({ left: b.dock.left, right: b.dock.right, top: b.dock.top, bottom: b.dock.bottom, edge: "bottom" });
    if (b.emotes) keepouts.push({ left: b.emotes.left, right: b.emotes.right, top: b.emotes.top, bottom: b.emotes.bottom, edge: "bottom" });
  }
  return { keepouts, vars };
}
