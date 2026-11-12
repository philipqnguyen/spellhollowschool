// Creature Keeping: a Fluffbell and a Lynxkit in the
// paddock. Each beat's prompt names a creature by its key's prefix (`puff_`
// the Fluffbell, `lynxkit_` the Lynxkit) and shows that creature's need:
// the prompt's icon (`icons/class/need_*`) floats over it for the beat's
// window while it fidgets; the student picks the care it needs. Right, and
// it settles with a contented squash; wrong or none, and it startles with a
// hop. At rest the Fluffbell bobs and squashes, and the Lynxkit turns its
// head about and breathes. All by node transforms (fx/stage.ts) on the props
// the Paddock export makes (`instances/props/beast_*.glb`).
// A right pick also gives a glint of butter yellow at the student's hands.

import { PALETTE, hexToRgb01 } from "../../../content/catalog";
import { classPicks } from "../picks";
import type { ClassHit } from "./hooks";
import { hump, PropStage, type Motion, type PropSpec } from "./stage";

/** The Fluffbell: a fluffy ball that bobs and squashes, jiggles while it needs something. */
export const FLUFFBELL: Motion = {
  showsNeed: true,
  rest(t, seed, out) {
    const s = Math.sin(2.4 * t + seed);
    out.lift += 0.05 * Math.max(0, s);
    // Squashed as it lands, a little taller in the air.
    out.sy *= 1 - 0.07 * Math.max(0, -s) + 0.03 * Math.max(0, s);
    out.sxz *= 1 + 0.05 * Math.max(0, -s);
  },
  cue(t, c, out) {
    out.lift += 0.03 * c * Math.abs(Math.sin(9 * t));
    out.yaw += 0.15 * c * Math.sin(5 * t);
  },
  react(kind, p, _from, t, out) {
    if (kind === "right") {
      // A long, contented squash.
      out.sy *= 1 - 0.16 * hump(p);
      out.sxz *= 1 + 0.1 * hump(p);
      return;
    }
    // A startled hop with a wobble.
    out.lift += 0.22 * hump(Math.min(1, p / 0.55));
    out.yaw += 0.25 * Math.sin(22 * t) * (1 - p);
  },
};

/** The Lynxkit: a low cat that looks about and breathes, and flattens while it needs something. */
export const LYNXKIT: Motion = {
  showsNeed: true,
  rest(t, seed, out) {
    out.yaw += 0.22 * Math.sin(0.45 * t + seed);
    out.tilt += 0.03 * Math.sin(0.9 * t + seed);
    out.sy *= 1 + 0.025 * Math.sin(1.8 * t + seed);
  },
  cue(t, c, out) {
    // Hunched back from the students, tail-twitchy.
    out.tilt -= 0.08 * c;
    out.sy *= 1 - 0.06 * c;
    out.yaw += 0.06 * c * Math.sin(7 * t);
  },
  react(kind, p, _from, t, out) {
    if (kind === "right") {
      // It settles: a slow stretch and a lean in toward the student.
      out.tilt += 0.1 * hump(p);
      out.sxz *= 1 + 0.05 * hump(p);
      out.sy *= 1 - 0.05 * hump(p);
      return;
    }
    out.lift += 0.16 * hump(Math.min(1, p / 0.45));
    out.tilt -= 0.12 * hump(p);
    out.yaw += 0.2 * Math.sin(18 * t) * (1 - p);
  },
};

/** The paddock's creatures (prop ids are the layout's landmarks: `beast_puff` [-2, 0, -3], `beast_lynxkit` [2, 0, -3]). */
export const CREATURE_PROPS: readonly PropSpec[] = [
  { id: "beast_puff", prefix: "puff_", motion: FLUFFBELL },
  { id: "beast_lynxkit", prefix: "lynxkit_", motion: LYNXKIT },
];

/** A Creature Keeping lesson's stage. */
export function creaturesStage(): PropStage {
  return new PropStage(CREATURE_PROPS, classPicks("creatures")?.prompts ?? []);
}

const BUTTER = PALETTE.colours.find((c) => c.id === "butter");
const BUTTER_RGB: [number, number, number] = BUTTER ? hexToRgb01(BUTTER.hex) : [0.91, 0.84, 0.56];

export const creaturesHit: ClassHit = (ctx) => {
  const at = ctx.desk();
  if (at) ctx.burst(at[0], at[1], at[2], BUTTER_RGB, 10, 0.3);
};
