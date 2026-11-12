// Every effect's and ambience bed's definition (docs/architecture.md
// "Sound and music"): a CC0 file list (public/audio/,
// each file recorded in data/audio/sources.json), a synthesized recipe
// (rendered once into a buffer by the engine), an ambience bed (layers and
// sprinkles) or an alias. The engine plays a short placeholder beep for an id
// with no definition yet; the acceptance is `missingDefs()`
// empty (catalogue.test.ts reports it, sfx.test.ts requires it).
//
// The recipes live by family: files.ts (the CC0 recordings), ui.ts, magic.ts
// (spells, duels, potions, secrets, level up), school.ts (classes), world.ts
// (entrances, staircases, brooms, crafting, owl mail, the Cup, the clock),
// ambience.ts (room tones, winds, emitters, sprinkles and the beds). Each
// synthesized sound's level is set by levels.ts, measured on the preview page
// (/src/audio/sfx/preview.html in the dev server).

import { SOUNDS, type EffectId } from "../catalogue";
import type { SoundDef, SoundDefs } from "../types";
import { AMBIENCE_SYNTH_DEFS, BED_DEFS, EMITTER_SYNTH_DEFS, SPRINKLE_SYNTH_DEFS } from "./ambience";
import { FILE_DEFS } from "./files";
import { SYNTH_LEVELS } from "./levels";
import { DUEL_DEFS, LEVEL_DEFS, POTION_DEFS, SECRET_DEFS, SPELL_DEFS, SPELL_L20_DEFS } from "./magic";
import { CLASS_DEFS } from "./school";
import { UI_DEFS } from "./ui";
import { CRAFT_DEFS, CUP_DEFS, DOOR_SYNTH_DEFS, FLIGHT_DEFS, MAIL_DEFS, STAIRS_DEFS, WORLD_DEFS } from "./world";

const PARTS: readonly SoundDefs[] = [
  FILE_DEFS,
  UI_DEFS,
  SPELL_DEFS,
  // Spells to level 20: the new spells and their effects.
  SPELL_L20_DEFS,
  DUEL_DEFS,
  POTION_DEFS,
  SECRET_DEFS,
  LEVEL_DEFS,
  CLASS_DEFS,
  DOOR_SYNTH_DEFS,
  STAIRS_DEFS,
  FLIGHT_DEFS,
  CRAFT_DEFS,
  MAIL_DEFS,
  CUP_DEFS,
  WORLD_DEFS,
  AMBIENCE_SYNTH_DEFS,
  EMITTER_SYNTH_DEFS,
  SPRINKLE_SYNTH_DEFS,
  BED_DEFS,
];

/** The parts merged; a synthesized sound's gain is its measured level (levels.ts). Throws on an id defined twice. */
export function mergeDefs(parts: readonly SoundDefs[], levels: Readonly<Record<string, number>> = SYNTH_LEVELS): SoundDefs {
  const out: Partial<Record<EffectId, SoundDef>> = {};
  for (const part of parts)
    for (const [id, def] of Object.entries(part) as [EffectId, SoundDef][]) {
      if (id in out) throw new Error(`sfx: ${id} defined twice`);
      const level = levels[id];
      out[id] = def.kind === "synth" && level !== undefined ? { ...def, gain: level } : def;
    }
  return out;
}

export const SOUND_DEFS: SoundDefs = mergeDefs(PARTS);

/** The definitions as written, before levels.ts (the preview page measures these). */
export const RAW_SOUND_DEFS: SoundDefs = mergeDefs(PARTS, {});

/** The effect and ambience ids still without a definition. */
export function missingDefs(defs: SoundDefs = SOUND_DEFS): EffectId[] {
  return SOUNDS.filter((s) => s.source !== "score" && !(s.id in defs)).map((s) => s.id as EffectId);
}
