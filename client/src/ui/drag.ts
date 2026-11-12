// Every HUD window fits on screen and can be dragged by its title bar
// (docs/architecture.md "Client").
//
// - A window opens where its stylesheet puts it (its default), or where the
//   player last dragged a window of its kind (remembered in this browser).
//   Either way it is clamped fully on screen, and a window taller or wider
//   than the screen is capped to it (its content scrolls inside: `.win-capped`).
//   The same happens when the browser window changes size, and when a
//   window's content grows while it is open.
// - Dragging the title bar (not its buttons or fields) moves it, clamped so the
//   whole window, and so its title bar, stays on screen. A double-click on the
//   title bar puts it back where it opens by default; Settings, Graphics,
//   "Reset window positions" does that for every kind.
// - The offset from the default is the CSS `translate` property, which adds to
//   whatever `transform` the window's own stylesheet uses (`translateX(-50%)`
//   to centre it, an opening animation), so no window's CSS changes.
//
// Nothing here is per window: the window stack (windows.ts) hands every
// window it sees open (`isWindow`) to `WindowPlacer`, so a new window gets
// all of this by being a window. The arithmetic is pure and unit tested
// (drag.test.ts).

/** Distance kept between a window and the screen's edges (px). */
export const WINDOW_EDGE = 8;

/** Where window positions are kept in localStorage. */
export const POSITIONS_KEY = "shs.windowPositions";

export interface Point {
  x: number;
  y: number;
}

export interface Size {
  width: number;
  height: number;
}

/**
 * The top-left corner of a window of `size` at `pos`, moved the least so the
 * whole window is on a `screen`-sized viewport, `edge` px in from its sides.
 * A window bigger than the screen (it is capped first, so only in a sliver of
 * a window) keeps its top-left corner on screen, so its title bar shows. Pure.
 */
export function clampWindow(pos: Point, size: Size, screen: Size, edge = WINDOW_EDGE): Point {
  const axis = (v: number, len: number, room: number) => {
    const hi = room - edge - len;
    return Math.round(Math.max(Math.min(v, hi), Math.min(edge, Math.max(0, hi))));
  };
  return { x: axis(pos.x, size.width, screen.width), y: axis(pos.y, size.height, screen.height) };
}

/** The most a window may measure on a `screen`-sized viewport (px). Pure. */
export function windowCap(screen: Size, edge = WINDOW_EDGE): Size {
  return { width: Math.max(0, screen.width - 2 * edge), height: Math.max(0, screen.height - 2 * edge) };
}

/** Where a drag that started with the window at `start` and the pointer at `from` puts it, the pointer now at `to`. Pure. */
export function dragTo(start: Point, from: Point, to: Point, size: Size, screen: Size, edge = WINDOW_EDGE): Point {
  return clampWindow({ x: start.x + to.x - from.x, y: start.y + to.y - from.y }, size, screen, edge);
}

/** Saved positions by window kind: only well-formed entries survive a bad or old value. Pure. */
export function parsePositions(raw: string | null): Record<string, Point> {
  const out: Record<string, Point> = {};
  if (!raw) return out;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return out;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return out;
  for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
    const p = v as Partial<Point> | null;
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) out[k] = { x: p.x!, y: p.y! };
  }
  return out;
}

/** The classes that say nothing about which window an element is. */
const GENERIC_CLASSES = new Set(["panel", "econ-window", "hidden"]);

/**
 * A window's kind, the key its position is kept under: its `data-window`
 * attribute, else its classes bar the shared ones ("mail-window",
 * "monstra-viam.lore", "player-card"). Pure apart from reading the element.
 */
export function windowKind(el: Element): string {
  const named = el.getAttribute("data-window");
  if (named) return named;
  const own = [...el.classList].filter((c) => !GENERIC_CLASSES.has(c) && !c.startsWith("win-"));
  return own.join(".") || el.tagName.toLowerCase();
}

/** What a drag starts on: the window's title bar. */
const HANDLE_SELECTOR = ":scope > header, :scope > .econ-head, :scope > h2, :scope > .results-kicker";
/** What inside a title bar does not start a drag (it is clicked or typed in). */
const INTERACTIVE = "button, input, select, textarea, a, label, [role='tab'], [contenteditable]";

/** The element a window is dragged by, or null when it has none. */
export function windowHandle(win: HTMLElement): HTMLElement | null {
  return win.querySelector<HTMLElement>(HANDLE_SELECTOR);
}

function viewport(): Size {
  return { width: window.innerWidth, height: window.innerHeight };
}

interface Placed {
  kind: string;
  /** The window's offset from its default place (the CSS `translate`). */
  offset: Point;
}

interface Drag {
  win: HTMLElement;
  handle: HTMLElement;
  pointer: number;
  from: Point;
  /** The window's top-left at the start, and its default top-left (without the offset). */
  start: Point;
  base: Point;
  size: Size;
  moved: boolean;
}

/** Places, fits and drags the open windows (see the top of this file). */
export class WindowPlacer {
  private readonly placed = new Map<HTMLElement, Placed>();
  private readonly kinds = new WeakMap<HTMLElement, string>();
  private positions: Record<string, Point>;
  private drag: Drag | null = null;
  private readonly sizes: ResizeObserver | null;
  private readonly onResize = () => {
    for (const el of this.placed.keys()) this.fit(el);
  };

  constructor(private readonly root: HTMLElement) {
    this.positions = this.load();
    this.sizes = typeof ResizeObserver === "undefined" ? null : new ResizeObserver((entries) => {
      for (const e of entries) if (this.placed.has(e.target as HTMLElement) && !this.drag) this.fit(e.target as HTMLElement);
    });
    root.addEventListener("pointerdown", this.onDown);
    root.addEventListener("dblclick", this.onDouble);
    root.addEventListener("animationend", this.onAnimationEnd);
    window.addEventListener("resize", this.onResize);
  }

  /** A window opened: put it where it goes, on screen. */
  open(el: HTMLElement): void {
    let kind = this.kinds.get(el);
    if (!kind) this.kinds.set(el, (kind = windowKind(el)));
    this.placed.set(el, { kind, offset: { x: 0, y: 0 } });
    windowHandle(el)?.classList.add("win-handle");
    this.fit(el);
    this.sizes?.observe(el);
  }

  /** A window closed. */
  close(el: HTMLElement): void {
    if (!this.placed.delete(el)) return;
    this.sizes?.unobserve(el);
    if (this.drag?.win === el) this.endDrag();
    el.classList.remove("win-capped", "win-capped-w");
    el.style.translate = "";
  }

  /** Settings: every window back to where it opens by default. */
  resetAll(): void {
    this.positions = {};
    this.save();
    for (const el of this.placed.keys()) this.fit(el);
  }

  /**
   * Caps `el` to the screen and clamps it on screen: at its kind's saved
   * place if there is one, else at its default. Reads the layout (only on
   * open, a resize or a content change, never per frame).
   */
  private fit(el: HTMLElement): void {
    const p = this.placed.get(el);
    if (!p || el.hidden) return;
    const screen = viewport();
    const cap = windowCap(screen);
    el.style.setProperty("--win-max-w", `${cap.width}px`);
    el.style.setProperty("--win-max-h", `${cap.height}px`);
    // Measure it as its stylesheet has it, then cap what does not fit.
    el.classList.remove("win-capped", "win-capped-w");
    el.style.translate = "";
    let r = el.getBoundingClientRect();
    if (r.height > cap.height + 0.5) el.classList.add("win-capped");
    if (r.width > cap.width + 0.5) el.classList.add("win-capped-w");
    if (r.height > cap.height + 0.5 || r.width > cap.width + 0.5) r = el.getBoundingClientRect();
    const base = { x: r.left, y: r.top };
    const want = this.positions[p.kind] ?? base;
    const at = clampWindow(want, { width: r.width, height: r.height }, screen);
    this.setOffset(el, p, { x: at.x - base.x, y: at.y - base.y });
  }

  private setOffset(el: HTMLElement, p: Placed, offset: Point): void {
    p.offset = { x: Math.round(offset.x), y: Math.round(offset.y) };
    el.style.translate = p.offset.x || p.offset.y ? `${p.offset.x}px ${p.offset.y}px` : "";
  }

  /** The open window whose title bar `target` is on (not a button or field in it), with that bar. */
  private grabbed(target: EventTarget | null): { win: HTMLElement; handle: HTMLElement } | null {
    if (!(target instanceof Element) || target.closest(INTERACTIVE)) return null;
    for (const win of this.placed.keys()) {
      if (!win.contains(target)) continue;
      const handle = windowHandle(win);
      if (handle?.contains(target)) return { win, handle };
    }
    return null;
  }

  private readonly onDown = (e: PointerEvent) => {
    if (e.button !== 0 || this.drag) return;
    const hit = this.grabbed(e.target);
    if (!hit) return;
    const p = this.placed.get(hit.win)!;
    const r = hit.win.getBoundingClientRect();
    // No text selection, and nothing under the window (the camera, picking a character) sees the drag.
    e.preventDefault();
    e.stopPropagation();
    this.drag = {
      win: hit.win,
      handle: hit.handle,
      pointer: e.pointerId,
      from: { x: e.clientX, y: e.clientY },
      start: { x: r.left, y: r.top },
      base: { x: r.left - p.offset.x, y: r.top - p.offset.y },
      size: { width: r.width, height: r.height },
      moved: false,
    };
    try {
      hit.handle.setPointerCapture(e.pointerId);
    } catch {
      // A pointer that is already gone: the drag ends at once.
    }
    hit.handle.addEventListener("pointermove", this.onMove);
    hit.handle.addEventListener("pointerup", this.onUp);
    hit.handle.addEventListener("pointercancel", this.onUp);
    hit.handle.addEventListener("lostpointercapture", this.onUp);
    hit.win.classList.add("win-dragging");
  };

  private readonly onMove = (e: PointerEvent) => {
    const d = this.drag;
    if (!d || e.pointerId !== d.pointer) return;
    e.preventDefault();
    const at = dragTo(d.start, d.from, { x: e.clientX, y: e.clientY }, d.size, viewport());
    const p = this.placed.get(d.win);
    if (!p) return;
    if (at.x !== d.start.x || at.y !== d.start.y) d.moved = true;
    this.setOffset(d.win, p, { x: at.x - d.base.x, y: at.y - d.base.y });
  };

  private readonly onUp = (e: PointerEvent) => {
    const d = this.drag;
    if (!d || e.pointerId !== d.pointer) return;
    const p = this.placed.get(d.win);
    if (p && d.moved) {
      this.positions[p.kind] = { x: Math.round(d.base.x + p.offset.x), y: Math.round(d.base.y + p.offset.y) };
      this.save();
    }
    this.endDrag();
  };

  private endDrag(): void {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    d.handle.removeEventListener("pointermove", this.onMove);
    d.handle.removeEventListener("pointerup", this.onUp);
    d.handle.removeEventListener("pointercancel", this.onUp);
    d.handle.removeEventListener("lostpointercapture", this.onUp);
    try {
      if (d.handle.hasPointerCapture(d.pointer)) d.handle.releasePointerCapture(d.pointer);
    } catch {
      // Already released.
    }
    d.win.classList.remove("win-dragging");
  }

  /** A double-click on a title bar: that window back to its default place. */
  private readonly onDouble = (e: MouseEvent) => {
    const hit = this.grabbed(e.target);
    if (!hit) return;
    const p = this.placed.get(hit.win)!;
    e.preventDefault();
    if (p.kind in this.positions) {
      delete this.positions[p.kind];
      this.save();
    }
    this.fit(hit.win);
  };

  /** An opening animation (a scale or a slide) ended: measure the window as it now stands. */
  private readonly onAnimationEnd = (e: AnimationEvent) => {
    const el = e.target as HTMLElement;
    if (this.placed.has(el) && !this.drag) this.fit(el);
  };

  private load(): Record<string, Point> {
    try {
      return parsePositions(localStorage.getItem(POSITIONS_KEY));
    } catch {
      return {};
    }
  }

  private save(): void {
    try {
      if (Object.keys(this.positions).length) localStorage.setItem(POSITIONS_KEY, JSON.stringify(this.positions));
      else localStorage.removeItem(POSITIONS_KEY);
    } catch {
      // Storage refused (a private window): positions last for this page only.
    }
  }

  dispose(): void {
    this.endDrag();
    this.sizes?.disconnect();
    this.root.removeEventListener("pointerdown", this.onDown);
    this.root.removeEventListener("dblclick", this.onDouble);
    this.root.removeEventListener("animationend", this.onAnimationEnd);
    window.removeEventListener("resize", this.onResize);
  }
}
