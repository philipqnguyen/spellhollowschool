// The class effect hooks by class id (Potions, Transfiguration, Defence,
// History of Magic, Divination, Astronomy, and Herbology and Creature
// Keeping with their stages).

import { astronomyTraced } from "./astronomy";
import { creaturesHit, creaturesStage } from "./creatures";
import { DefenceDrill } from "./defence";
import { divinationHit } from "./divination";
import { herbologyHit, herbologyStage } from "./herbology";
import { historyHit } from "./history";
import type { ClassDrill, ClassHit, ClassStage, ClassTraced } from "./hooks";
import { potionsHit } from "./potions";
import { transfigurationHit } from "./transfiguration";

/** A right pick's reaction, by class. */
export const CLASS_HITS: Readonly<Record<string, ClassHit>> = {
  potions: potionsHit,
  transfiguration: transfigurationHit,
  history: historyHit,
  divination: divinationHit,
  herbology: herbologyHit,
  creatures: creaturesHit,
};

/** A passed trace's reaction, by class. */
export const CLASS_TRACED: Readonly<Record<string, ClassTraced>> = {
  astronomy: astronomyTraced,
};

/** The drills played out in the room, by class (a new one for each lesson followed). */
export const CLASS_DRILLS: Readonly<Record<string, () => ClassDrill>> = {
  defence: () => new DefenceDrill(),
};

/** The classes whose room has animated props, driven by the beats (a new stage for each lesson followed). */
export const CLASS_STAGES: Readonly<Record<string, () => ClassStage>> = {
  herbology: herbologyStage,
  creatures: creaturesStage,
};

export type { ClassDrill, ClassFxContext, ClassHit, ClassStage, ClassTraced, DrillContext, DrillVfx, FxPose, PropNeed, PropPose } from "./hooks";
