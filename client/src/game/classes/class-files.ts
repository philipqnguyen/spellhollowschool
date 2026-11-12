// The class files this build ships (data/classes/<class>.json, sim-core
// `ClassDef`), by class id. The timetable and the gestures live in the same
// directory and are left out (they have no professor). Only the parts the
// client reads are typed.

export interface ClassFile {
  id: string;
  /** The skill track (sim-core `Subject` id; display names in schedule.json `subjects`). */
  subject: string;
  professor: { id: string; name: string; house: string };
  /** The pick vocabularies (data/classes/picks.json set ids), for classes with pick beats. */
  picks?: { prompts: string; options: string };
  lesson: { at_secs: number; kind: string; round?: number; window_ms?: number; beats?: { at_ms: number; gesture?: string; prompt?: string; options?: string[]; ring?: number }[]; gesture?: string; spell?: string }[];
  rewards: {
    attend_skill_xp: number;
    bonus_skill_xp_max: number;
    min_answers: number;
    house_points: { min_pct: number; points: number }[];
    /** Recipes the class teaches once the subject skill reaches `skill` (sim-core `TeachRule`). */
    teaches?: { recipe: number; skill: number }[];
  };
}

const files = import.meta.glob<{ default: unknown }>("../../../../data/classes/*.json", { eager: true });

const byId: Record<string, ClassFile> = {};
for (const mod of Object.values(files)) {
  const def = mod.default as Partial<ClassFile> | null;
  if (def && typeof def.id === "string" && def.professor && Array.isArray(def.lesson)) byId[def.id] = def as ClassFile;
}

export default byId;

/** The professor NPC ids of every class (`EntityInfo.npc`), mapped to their class. */
export const PROFESSORS: ReadonlyMap<string, string> = new Map(Object.values(byId).map((c) => [c.professor.id, c.id]));
