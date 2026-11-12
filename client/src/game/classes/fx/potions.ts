// Potions: the right ingredient goes in and the student's
// cauldron puffs in that ingredient's colour (its catalog item's primary
// dye, a palette colour).

import { paletteRgb } from "../../../content/catalog";
import { econDef } from "../../../economy/items";
import type { ClassHit } from "./hooks";

/** The puff's colour for an ingredient pick: its item's primary dye (null: not an item). */
export function ingredientRgb(item: number | undefined): [number, number, number] | null {
  if (item === undefined) return null;
  const def = econDef(item);
  return def ? paletteRgb(def.dyes.primary) : null;
}

export const potionsHit: ClassHit = (ctx, _prompt, chosen) => {
  const at = ctx.desk();
  const rgb = ingredientRgb(chosen.item);
  if (!at || !rgb) return;
  // A puff rising from the cauldron's mouth.
  ctx.burst(at[0], at[1], at[2], rgb, 16, 0.45);
  ctx.burst(at[0], at[1] + 0.25, at[2], rgb, 8, 0.3);
};
