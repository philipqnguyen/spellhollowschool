// Player levels as the client shows them (data/progression.json, the file
// sim-core's `Progression` reads): the title beside a name, the XP bar's
// fill, the words for where XP came from, and what a level-up opens.
// The server decides levels (`Progress`, `LevelChanged`); this only
// describes them. Pure, unit tested in progression.test.ts.

import progressionJson from "../../../data/progression.json";
import type { Progress, XpSource } from "../wasm/sim_wasm";

const SLOTS: readonly { level: number; slots: number }[] = [...progressionJson.loadout_slots].sort((a, b) => a.level - b.level);
/** Total XP each level starts at (index 0 = level 1). */
export const LEVEL_XP: readonly number[] = progressionJson.levels;
export const LEVEL_MAX = LEVEL_XP.length;

/** Dueling loadout slots at `level` (3 at 1, 4 at 5, 5 at 10). */
export function loadoutSlots(level: number): number {
  let slots = SLOTS[0]?.slots ?? 3;
  for (const s of SLOTS) if (level >= s.level) slots = s.slots;
  return slots;
}

/** "Lv 5": the title beside a name. Empty for NPCs (level 0). */
export function levelTitle(level: number): string {
  if (!Number.isFinite(level) || level < 1) return "";
  return `Lv ${level}`;
}

/** How far through its level `p` is (0..1; 1 at the cap). */
export function levelFraction(p: Progress): number {
  if (p.nextLevelXp === null) return 1;
  const span = p.nextLevelXp - p.levelXp;
  if (span <= 0) return 1;
  return Math.min(1, Math.max(0, (p.xp - p.levelXp) / span));
}

/** "512 / 720 XP", or "10,640 XP · top level". */
export function xpText(p: Progress): string {
  if (p.nextLevelXp === null) return `${p.xp.toLocaleString("en-GB")} XP · top level`;
  return `${p.xp.toLocaleString("en-GB")} / ${p.nextLevelXp.toLocaleString("en-GB")} XP`;
}

const SOURCES: Record<XpSource, string> = {
  class_attend: "class attended",
  class_bonus: "class bonus",
  craft: "crafting",
  find: "exploring",
  duel_win: "duel won",
  duel_loss: "duel fought",
  duel_draw: "duel drawn",
  admin: "granted",
  explore: "a secret found",
};

export function xpSourceLabel(source: XpSource): string {
  return SOURCES[source] ?? source;
}

/** "+80 XP · class attended", noting the daily limit when the grant was capped. */
export function gainText(g: { source: XpSource; amount: number; capped: boolean }): string {
  if (g.amount <= 0) return g.capped ? `No XP: today's limit for ${xpSourceLabel(g.source)} is reached` : "";
  return `+${g.amount} XP · ${xpSourceLabel(g.source)}${g.capped ? " (today's limit reached)" : ""}`;
}

/** What reaching `after` from `before` brings, for the level-up flourish. */
export interface LevelUp {
  level: number;
  /** Loadout slots now, when they grew. */
  slots: number | null;
}

export function levelUp(before: number, after: number): LevelUp | null {
  if (!(after > before)) return null;
  const slots = loadoutSlots(after);
  return { level: after, slots: slots > loadoutSlots(before) ? slots : null };
}

/** Two level-ups shown as one (the later level; new slots from either). */
export function mergeLevelUps(a: LevelUp, b: LevelUp): LevelUp {
  const later = b.level >= a.level ? b : a;
  const slots = a.slots === null ? b.slots : b.slots === null ? a.slots : Math.max(a.slots, b.slots);
  return { level: later.level, slots };
}

/**
 * When the level-up flourish may play (it once drew over the class results card, which a lesson's level-up
 * arrives with). While the card is open (`hold(true)`) a level-up waits,
 * a flourish already playing is taken down and waits too; closing the card
 * (`hold(false)`) gives back what to play then. Pure, unit tested.
 */
export class FlourishGate {
  private held = false;
  private pending: LevelUp | null = null;

  /** A level-up arrived: what to play now, or null while held (it waits). */
  offer(up: LevelUp): LevelUp | null {
    if (!this.held) return up;
    this.pending = this.pending ? mergeLevelUps(this.pending, up) : up;
    return null;
  }

  /** The card opened (`playing`: the flourish on screen, which waits now) or closed: what to play now, if anything. */
  hold(on: boolean, playing: LevelUp | null = null): LevelUp | null {
    if (on) {
      this.held = true;
      if (playing) this.pending = this.pending ? mergeLevelUps(playing, this.pending) : playing;
      return null;
    }
    this.held = false;
    const p = this.pending;
    this.pending = null;
    return p;
  }

  get waiting(): boolean {
    return this.pending !== null;
  }
}
