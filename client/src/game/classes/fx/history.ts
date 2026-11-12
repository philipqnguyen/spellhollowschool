// History of Magic: a lecture, then questions with long windows
// (6 s). The pick tray shows the question itself large, with no icon (every
// question shares the quill), and the answers as words alone (they share
// one icon too: picks.ts `sharedIcon`). The approach ring takes the whole
// window to close, so there is time to read. A right answer gives a glint
// of gilt over the student's desk, as a good mark in the margin.

import { PALETTE, hexToRgb01 } from "../../../content/catalog";
import type { ClassHit } from "./hooks";

const GILT = PALETTE.colours.find((c) => c.id === "gold");
const GILT_RGB: [number, number, number] = GILT ? hexToRgb01(GILT.hex) : [0.83, 0.65, 0.15];

export const historyHit: ClassHit = (ctx) => {
  const at = ctx.desk();
  if (at) ctx.burst(at[0], at[1] + 0.2, at[2], GILT_RGB, 8, 0.25);
};
