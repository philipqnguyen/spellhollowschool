// Item 8: opening a HUD window closes the others, so windows
// never pile up on top of each other. Every window opened with its key or
// button (the wardrobe, the map, mail, the catalogue at its stand, friends, the players
// list, a player's card, the timetable, ...) closes whatever window was open.
//
// - Windows that can't be closed this way stay: one with no close button
//   (a lesson's or a duel's result card) and one marked `keepOpen` (the trade
//   window, which would cancel a trade). The window opened last is drawn on
//   top of those, and a click in a window brings it up.
// - Esc closes the top window (game.ts asks `closeTop` first).
//
// No window changes: the stack watches the HUD for a window's `hidden`
// attribute going off and on (a MutationObserver), and closes a window with
// its own close button (or a closer registered for it), so each window's own
// clean-up runs. The order and the choice of what to close are pure
// (`StackOrder`, `toClose`), unit tested in windows.test.ts.
//
// Every window it sees open is also placed on screen and made draggable by
// its title bar (drag.ts `WindowPlacer`): nothing per window.

import { audio, type SoundId } from "../audio";
import { WindowPlacer } from "./drag";

/** The z-index of the bottom window; each one above adds one (toasts and menus sit higher, style.css). */
export const BASE_Z = 3;

/** The open windows in stack order, bottom first. Pure. */
export class StackOrder<T> {
  private readonly list: T[] = [];

  /** Opened (or clicked): to the top. */
  raise(w: T): void {
    const i = this.list.indexOf(w);
    if (i >= 0) this.list.splice(i, 1);
    this.list.push(w);
  }

  /** Closed. */
  remove(w: T): void {
    const i = this.list.indexOf(w);
    if (i >= 0) this.list.splice(i, 1);
  }

  get top(): T | null {
    return this.list[this.list.length - 1] ?? null;
  }

  get size(): number {
    return this.list.length;
  }

  /** Bottom first. */
  get order(): readonly T[] {
    return this.list;
  }

  has(w: T): boolean {
    return this.list.includes(w);
  }
}

/**
 * The windows to close when `opened` opens among `open` (bottom first): every
 * other one that `closable` says can be closed. Pure.
 */
export function toClose<T>(opened: T, open: readonly T[], closable: (w: T) => boolean): T[] {
  return open.filter((w) => w !== opened && closable(w));
}

/** Whether `el` is one of the HUD's windows (not a toast, the perf panel or a menu by the pointer). */
export function isWindow(el: Element): el is HTMLElement {
  if (!(el instanceof HTMLElement) || !el.classList.contains("panel")) return false;
  if (el.classList.contains("toast") || el.classList.contains("perf") || el.classList.contains("player-menu")) return false;
  return el.getAttribute("role") === "dialog" || el.classList.contains("friends") || el.classList.contains("menu");
}

/** Sound: the map and the timetable unroll, the books turn a page, the rest open and close. */
function windowSound(el: HTMLElement, open: boolean): SoundId {
  const c = el.classList;
  if (c.contains("map-window") || c.contains("sched-pop")) return open ? "ui.parchment_open" : "ui.parchment_close";
  if (open && (c.contains("spellbook") || c.contains("lore"))) return "ui.page";
  return open ? "ui.open" : "ui.close";
}

export class WindowStack {
  private readonly order = new StackOrder<HTMLElement>();
  /** Windows closed because another opened: they close without a sound (the new one's is heard). */
  private readonly replaced = new Set<HTMLElement>();
  private readonly closers = new Map<HTMLElement, () => void>();
  private readonly kept = new Set<HTMLElement>();
  /** Fits every open window on screen and drags it by its title bar. */
  private readonly placer: WindowPlacer;
  private readonly observer: MutationObserver | null;
  private readonly onDown = (e: PointerEvent) => {
    const el = (e.target as Element | null)?.closest?.(".panel");
    if (el instanceof HTMLElement && this.order.has(el) && this.order.top !== el) {
      this.order.raise(el);
      this.restack();
    }
  };

  constructor(private readonly root: HTMLElement) {
    this.placer = new WindowPlacer(root);
    this.observer = typeof MutationObserver === "undefined" ? null : new MutationObserver((records) => this.changed(records));
    this.observer?.observe(root, { subtree: true, attributes: true, attributeFilter: ["hidden"] });
    root.addEventListener("pointerdown", this.onDown, true);
  }

  /** How to close `el` when Esc reaches it, or another window opens (default: its own close button). */
  setCloser(el: HTMLElement, close: () => void): void {
    this.closers.set(el, close);
  }

  /** `el` stays open when another window opens (Esc still closes it). */
  keepOpen(el: HTMLElement): void {
    this.kept.add(el);
  }

  /** Settings: every window back to where it opens by default (forgets where they were dragged). */
  resetPositions(): void {
    this.placer.resetAll();
  }

  /** The window on top, or null when none is open. */
  get top(): HTMLElement | null {
    this.sync();
    return this.order.top;
  }

  /**
   * Esc: closes the top window (its registered closer, else its own close
   * button). True when it did; false when nothing is open or the top window
   * has neither (a lesson's or a duel's result card): then the window's own
   * Esc handling in game.ts's key chain answers instead.
   */
  closeTop(): boolean {
    const el = this.top;
    if (!el || !this.close(el)) return false;
    this.sync();
    return true;
  }

  /** Closes `el` its own way; false when it has no way to close. */
  private close(el: HTMLElement): boolean {
    const closer = this.closers.get(el);
    if (closer) {
      closer();
      return true;
    }
    const button = this.closeButton(el);
    if (!button) return false;
    button.click();
    return true;
  }

  private closeButton(el: HTMLElement): HTMLButtonElement | null {
    return el.querySelector<HTMLButtonElement>('header button[aria-label^="Close"]');
  }

  private changed(records: readonly MutationRecord[]): void {
    let touched = false;
    const opened: HTMLElement[] = [];
    for (const r of records) {
      if (!isWindow(r.target as Element)) continue;
      const el = r.target as HTMLElement;
      if (el.hidden) {
        if (this.closed(el)) {
          touched = true;
          if (!this.replaced.delete(el)) audio.play(windowSound(el, false));
        }
      } else if (this.opened(el)) {
        touched = true;
        opened.push(el);
      }
    }
    // The last window opened stays; every other one that can close, closes.
    const last = opened[opened.length - 1];
    if (last) {
      audio.play(windowSound(last, true));
      const closable = (w: HTMLElement) => !this.kept.has(w) && (this.closers.has(w) || this.closeButton(w) !== null);
      for (const w of toClose(last, this.order.order, closable)) {
        this.replaced.add(w);
        this.close(w);
      }
    }
    if (touched) this.restack();
  }

  /** Mutation records arrive after a microtask: catch up now (Esc right after a click). */
  private sync(): void {
    const records = this.observer?.takeRecords() ?? [];
    if (records.length) this.changed(records);
    // A window removed from the page (a dispose) leaves the stack.
    for (const el of [...this.order.order]) if (!el.isConnected || el.hidden) this.closed(el);
  }

  private opened(el: HTMLElement): boolean {
    if (this.order.has(el)) return false;
    this.order.raise(el);
    this.placer.open(el);
    return true;
  }

  private closed(el: HTMLElement): boolean {
    if (!this.order.has(el)) {
      this.replaced.delete(el);
      return false;
    }
    this.order.remove(el);
    this.placer.close(el);
    el.style.zIndex = "";
    return true;
  }

  private restack(): void {
    this.order.order.forEach((el, i) => (el.style.zIndex = String(BASE_Z + i)));
  }

  dispose(): void {
    this.observer?.disconnect();
    this.placer.dispose();
    this.root.removeEventListener("pointerdown", this.onDown, true);
  }
}
