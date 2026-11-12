// Selecting a character in the world, without the DOM or the
// scene. A click that does not drag the camera selects the character under
// it (a ring under their feet, game/selection.ts); a double-click, or E
// while one is selected, opens a player's card; Tab steps through the
// players and NPCs nearby, nearest first (Shift+Tab back); Esc clears it.
// E on an NPC, and E with nothing selected, do what E always did (doors,
// stations, the counter); a door or station in reach (its E prompt shown)
// takes E before the selection (game.ts orders the handlers). Pure, unit tested in selection-state.test.ts.

/** Someone who can be selected: a player or an NPC in view. */
export interface SelectTarget {
  id: number;
  name: string;
  npc: boolean;
  x: number;
  y: number;
  z: number;
}

/** Tab reaches characters this close (m). */
export const TAB_RANGE_M = 25;
/** ... and this close in height (m: about a floor). */
export const TAB_RANGE_Y_M = 4;
/** A press and release this close (px) and quick (ms) is a click, not a camera drag. */
export const CLICK_SLOP_PX = 6;
export const CLICK_MS = 450;
/** Two clicks this close in time (ms) and place (px) are a double-click. */
export const DOUBLE_MS = 400;
export const DOUBLE_SLOP_PX = 10;
/** A selection further than this (m) is dropped (walked away from it). */
export const KEEP_RANGE_M = 60;

export interface PointerAt {
  x: number;
  y: number;
  /** performance.now() ms. */
  t: number;
}

/** Whether a press then release is a click (not a drag to look round). */
export function isClick(down: PointerAt, up: PointerAt): boolean {
  return Math.hypot(up.x - down.x, up.y - down.y) <= CLICK_SLOP_PX && up.t - down.t <= CLICK_MS;
}

/** Whether a click follows the last one closely enough to be a double-click. */
export function isDoubleClick(last: PointerAt | null, now: PointerAt): boolean {
  return !!last && now.t - last.t <= DOUBLE_MS && Math.hypot(now.x - last.x, now.y - last.y) <= DOUBLE_SLOP_PX;
}

/** The characters Tab steps through from `feet`: within reach, nearest first (then by id, so the order holds). */
export function tabTargets<T extends SelectTarget>(all: readonly T[], feet: readonly [number, number, number]): T[] {
  const near: { t: T; d: number }[] = [];
  for (const t of all) {
    if (Math.abs(t.y - feet[1]) > TAB_RANGE_Y_M) continue;
    const d = Math.hypot(t.x - feet[0], t.z - feet[2]);
    if (d <= TAB_RANGE_M) near.push({ t, d });
  }
  near.sort((a, b) => a.d - b.d || a.t.id - b.t.id);
  return near.map((n) => n.t);
}

/**
 * Tab (dir 1) or Shift+Tab (-1): the next of `ordered` (nearest first)
 * after the one selected now; the nearest when none of them is. Null when
 * no one is near.
 */
export function nextTarget<T extends SelectTarget>(ordered: readonly T[], current: number | null, dir: 1 | -1 = 1): T | null {
  if (!ordered.length) return null;
  const i = current === null ? -1 : ordered.findIndex((t) => t.id === current);
  if (i < 0) return dir === 1 ? ordered[0]! : ordered[ordered.length - 1]!;
  return ordered[(i + dir + ordered.length) % ordered.length]!;
}

/** What E does: open the selected player's card, or what E always did (an NPC, or nobody selected). */
export function eAction(selected: Pick<SelectTarget, "npc"> | null): "card" | "pass" {
  return selected && !selected.npc ? "card" : "pass";
}

/** The current selection. */
export class Selection {
  private target: SelectTarget | null = null;

  get selected(): SelectTarget | null {
    return this.target;
  }

  get id(): number | null {
    return this.target?.id ?? null;
  }

  select(t: SelectTarget): void {
    this.target = { ...t };
  }

  /** Esc, or the target left: true when something was selected. */
  clear(): boolean {
    const had = this.target !== null;
    this.target = null;
    return had;
  }

  /**
   * Each frame: follow the selected character among those in view (their
   * name and place), or drop it when it has left the view or is far away.
   * Returns the selection (null when dropped).
   */
  follow(inView: readonly SelectTarget[], feet: readonly [number, number, number]): SelectTarget | null {
    const t = this.target;
    if (!t) return null;
    const now = inView.find((v) => v.id === t.id);
    if (!now || Math.hypot(now.x - feet[0], now.z - feet[2]) > KEEP_RANGE_M) {
      this.target = null;
      return null;
    }
    t.x = now.x;
    t.y = now.y;
    t.z = now.z;
    t.name = now.name;
    t.npc = now.npc;
    return t;
  }
}

/** Whether Tab belongs to the game (the selection) rather than the page's focus: not while typing or in a window's field. */
export function tabIsOurs(e: { key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean }, typing: boolean): boolean {
  return e.key === "Tab" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey;
}
