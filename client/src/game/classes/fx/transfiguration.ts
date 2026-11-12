// Transfiguration: the right change is made and the object on
// the desk swaps shape: the pick tray's card turns over to what it became
// ("giant teacup"), with a glint of gold at the student's desk.

import { PALETTE, hexToRgb01 } from "../../../content/catalog";
import type { ClassHit } from "./hooks";

/** "Teacup → giant teacup (1 of 2)" → "giant teacup": what a puzzle's object becomes. */
export function targetOf(label: string): string {
  const after = label.split("→")[1] ?? label;
  return after.replace(/\(\d+ of \d+\)/, "").trim();
}

const GOLD = PALETTE.colours.find((c) => c.id === "gold");
const GOLD_RGB: [number, number, number] = GOLD ? hexToRgb01(GOLD.hex) : [0.83, 0.65, 0.15];

export const transfigurationHit: ClassHit = (ctx, prompt) => {
  ctx.swap(targetOf(prompt.label));
  const at = ctx.desk();
  if (at) ctx.burst(at[0], at[1], at[2], GOLD_RGB, 12, 0.35);
};
