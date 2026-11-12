// Herbology: plants on the bench that fight back. Each beat's
// prompt names a plant by its key's prefix (`vine_` the Gnashvine, `root_`
// the Shriekroot, `briar_` the Needlebriar, `fern_` the Shiverfern), and
// that plant lunges toward the students as the beat comes; the student
// picks the tool it needs. A right tool and it recoils and settles; a
// wrong one, or none, and it snaps at the air before it settles. At rest
// every plant sways a little. All by node transforms (fx/stage.ts), on the
// props the Greenhouse export makes (`instances/props/plant_*.glb`).
// A right pick also gives a glint of mint green at the student's hands.

import { PALETTE, hexToRgb01 } from "../../../content/catalog";
import { classPicks } from "../picks";
import type { ClassHit, PropPose } from "./hooks";
import { hump, PropStage, type Motion, type PropSpec, type Reaction } from "./stage";

/** How far a plant leans at its lunge's height (rad), and how much it stretches. */
export const LUNGE_TILT = 0.38;
export const LUNGE_STRETCH = 0.16;

/** A plant on the bench: it sways at rest, lunges when named, recoils or snaps after a pick. */
export const PLANT: Motion = {
  showsNeed: false,
  rest(t, seed, out: PropPose) {
    out.tilt += 0.05 * Math.sin(1.1 * t + seed);
    out.yaw += 0.07 * Math.sin(0.6 * t + 2 * seed);
    out.sy *= 1 + 0.02 * Math.sin(1.7 * t + seed);
  },
  cue(t, c, out) {
    // Rearing toward the students, quivering once it is fully up.
    out.tilt += LUNGE_TILT * c + 0.025 * c * c * Math.sin(38 * t);
    out.sy *= 1 + LUNGE_STRETCH * c;
    out.sxz *= 1 - 0.06 * c;
  },
  react(kind: Reaction, p, from, t, out) {
    if (kind === "right") {
      // The recoil: it drops out of the lunge fast, shrinks back past rest, then settles.
      const leave = from * Math.max(0, 1 - p / 0.2);
      out.tilt += LUNGE_TILT * leave - 0.16 * hump(p) * (1 - p);
      out.sy *= (1 + LUNGE_STRETCH * leave) * (1 - 0.12 * hump(p) * (1 - p));
      out.sxz *= 1 + 0.05 * hump(p) * (1 - p);
      return;
    }
    // The snap: a bite further forward, then back with a shake.
    const bite = p < 0.3 ? hump(p / 0.6) : hump(0.5 + (p - 0.3) / 1.4);
    out.tilt += LUNGE_TILT * from * (1 - p) + 0.3 * bite;
    out.yaw += 0.12 * Math.sin(30 * t) * (1 - p);
    out.sy *= 1 + 0.2 * bite;
  },
};

/** The bench's plants (prop ids are the layout's landmarks: `plant_vine` [-3, 0.9, -4] ...). */
export const HERBOLOGY_PROPS: readonly PropSpec[] = [
  { id: "plant_vine", prefix: "vine_", motion: PLANT },
  { id: "plant_root", prefix: "root_", motion: PLANT },
  { id: "plant_briar", prefix: "briar_", motion: PLANT },
  { id: "plant_fern", prefix: "fern_", motion: PLANT },
];

/** A Herbology lesson's stage. */
export function herbologyStage(): PropStage {
  return new PropStage(HERBOLOGY_PROPS, classPicks("herbology")?.prompts ?? []);
}

const MINT = PALETTE.colours.find((c) => c.id === "mint");
const MINT_RGB: [number, number, number] = MINT ? hexToRgb01(MINT.hex) : [0.66, 0.84, 0.73];

export const herbologyHit: ClassHit = (ctx) => {
  const at = ctx.desk();
  if (at) ctx.burst(at[0], at[1], at[2], MINT_RGB, 10, 0.3);
};
