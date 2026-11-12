// Divination: each beat shows a symbol in a teacup's leaves or
// in a crystal ball's mist, and the student picks its meaning. The prompt
// key's prefix picks the close-up on the pick tray (`cup_*` the teacup,
// `ball_*` the crystal ball; ui/classes-pick.ts draws it with the symbol's
// icon inside), and a right reading lifts a wisp of lavender mist from the
// student's table.

import { PALETTE, hexToRgb01 } from "../../../content/catalog";
import type { ClassHit } from "./hooks";

export type Closeup = "cup" | "ball";

/** The close-up a prompt shows: `cup_key` a teacup, `ball_moon` a crystal ball, anything else none. */
export function closeupOf(promptKey: string): Closeup | null {
  if (promptKey.startsWith("cup_")) return "cup";
  if (promptKey.startsWith("ball_")) return "ball";
  return null;
}

/** The symbol a prompt shows (`cup_key` → `key`). */
export function symbolOf(promptKey: string): string {
  return promptKey.replace(/^(cup|ball)_/, "");
}

const MIST = PALETTE.colours.find((c) => c.id === "lavender");
const MIST_RGB: [number, number, number] = MIST ? hexToRgb01(MIST.hex) : [0.66, 0.61, 0.79];

export const divinationHit: ClassHit = (ctx) => {
  const at = ctx.desk();
  if (!at) return;
  ctx.burst(at[0], at[1], at[2], MIST_RGB, 10, 0.35);
  ctx.burst(at[0], at[1] + 0.3, at[2], MIST_RGB, 6, 0.2);
};
