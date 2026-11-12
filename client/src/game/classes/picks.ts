// Pick beats' vocabularies: data/classes/picks.json, the sets
// of prompts and options a pick class draws from. A `LessonRound`'s pick
// beat names its prompt and options by index into its class's sets (the
// class file's `picks: { prompts, options }`); which option is right is
// never sent (the answer keys live in data/classes/answers/, server only;
// the client's class-file glob is not recursive, so it never bundles them).

import picksJson from "../../../../data/classes/picks.json";
import classFiles from "./class-files";

/** One prompt or option: its words and its icon key (`icons/...`); `item` shows a catalog item's icon. */
export interface PickItem {
  key: string;
  label: string;
  icon: string;
  item?: number;
}

interface PickSetFile {
  id: string;
  items: PickItem[];
}

const SETS = new Map<string, PickItem[]>((picksJson as { sets: PickSetFile[] }).sets.map((s) => [s.id, s.items]));

/**
 * A pick shows "Now!" and the green ring this close to its beat (ms). The window opens a whole
 * window before the beat, but the marks are on the beat (timing full marks within
 * gestures.json `timing_full_ms`): this band still scores about 95, the window's first instant 60.
 */
export const PICK_NOW_MS = 250;

/** The pick tray's cue for a beat `leadMs` away (negative: past it) in a round with `windowMs`. */
export function pickCue(leadMs: number, windowMs: number, upcoming: boolean): { open: boolean; onBeat: boolean; countdown: string } {
  const open = upcoming && Math.abs(leadMs) <= windowMs;
  const onBeat = open && Math.abs(leadMs) <= PICK_NOW_MS;
  const countdown = !upcoming ? "" : onBeat ? "Now!" : leadMs > 0 ? `in ${(leadMs / 1000).toFixed(1)} s` : "";
  return { open, onBeat, countdown };
}

/** A vocabulary by id (`potions_ingredients`). */
export function pickSet(id: string): readonly PickItem[] | undefined {
  return SETS.get(id);
}

export interface ClassPicks {
  prompts: readonly PickItem[];
  options: readonly PickItem[];
}

/** The vocabularies class `classId` picks from (null: not a pick class, or unknown). */
export function classPicks(classId: string): ClassPicks | null {
  const spec = classFiles[classId]?.picks;
  if (!spec) return null;
  const prompts = SETS.get(spec.prompts);
  const options = SETS.get(spec.options);
  return prompts && options ? { prompts, options } : null;
}

/**
 * Whether every item of a vocabulary shows the same icon (History's questions and answers,
 * Divination's meanings): the icon then tells nothing, so the tray shows the words alone.
 */
export function sharedIcon(items: readonly PickItem[]): boolean {
  if (items.length < 2) return false;
  const first = items[0]!;
  return items.every((i) => i.item === undefined && i.icon === first.icon);
}

/** A pick beat's prompt and options as words and icons (unknown indices give a placeholder). */
export function resolvePick(picks: ClassPicks | null, prompt: number, options: readonly number[]): { prompt: PickItem; options: PickItem[] } {
  const unknown = (i: number): PickItem => ({ key: `?${i}`, label: "?", icon: "" });
  return {
    prompt: picks?.prompts[prompt] ?? unknown(prompt),
    options: options.map((o) => picks?.options[o] ?? unknown(o)),
  };
}
