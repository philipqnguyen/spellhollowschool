// The wayfinding trail on the HUD (the Wayfinder Charm, "Monstra Viam"): the
// chip "Trail to Potions Classroom, 120 m" with an X while a trail is on,
// and the Wayfinder Charm window ("Find my way"): the key places this house is
// offered, grouped, each a button that starts the trail there. Class doors
// say when their lesson starts. The trail itself is game/wayfinding.

import "./wayfinding.css";
import type { KeyPlaceView, PlaceGroup } from "../wasm/sim_wasm";
import { focusOrigin, h, restoreFocus } from "./dom";
import { lineIcon } from "./effects-icons";

/** A wand drawing a dotted way: the Wayfinder Charm button. */
export const MONSTRA_VIAM_ICON = ["M3 21 11 13", "M10 12l2 2", "M14.5 9.5h.01", "M17 7h.01", "M19.5 4.5h.01", "M16.5 3.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6Z"];

/** What the chip shows. */
export interface TrailChipView {
  name: string;
  /** "120 m", or "" while unknown. */
  distance: string;
  /** No way from here (a dorm, a classroom, off the castle's ground). */
  waiting: boolean;
  /** The first-day tour's stop ("First-day tour, 1 of 3"): the X skips the tour. */
  tour?: string;
}

export class TrailChip {
  readonly root: HTMLElement;
  private readonly text: HTMLElement;
  private readonly close: HTMLButtonElement;
  private key = "";

  constructor(onClose: () => void) {
    this.text = h("span", { class: "trail-text" }, "");
    this.close = h("button", { type: "button", class: "icon-button trail-close", "aria-label": "End the trail", title: "End the trail (Esc)", onclick: () => onClose() }, "×");
    this.root = h("div", { class: "trail-chip panel", role: "status", "aria-live": "polite", hidden: true }, h("span", { class: "trail-dot", "aria-hidden": "true" }), this.text, this.close);
  }

  show(v: TrailChipView | null): void {
    const key = v ? `${v.name}|${v.distance}|${v.waiting}|${v.tour ?? ""}` : "";
    if (key === this.key) return;
    this.key = key;
    this.root.hidden = !v;
    if (!v) return;
    this.text.textContent = trailText(v);
    this.root.classList.toggle("waiting", v.waiting);
    const label = v.tour ? "Skip the tour" : "End the trail";
    this.close.setAttribute("aria-label", label);
    this.close.title = v.tour ? "Skip the tour (X)" : "End the trail (Esc)";
  }
}

/** "Trail to Potions Classroom, 120 m". */
export function trailText(v: TrailChipView): string {
  const to = v.tour ? `${v.tour}: ${v.name}` : `Trail to ${v.name}`;
  if (v.waiting) return `${to}: waiting until you are back in the castle`;
  return v.distance ? `${to}, ${v.distance}` : to;
}

const GROUPS: { title: string; groups: PlaceGroup[] }[] = [
  { title: "Your house", groups: ["dorm", "common_room", "owl_order"] },
  // "castle" (the Hospital Wing, the Clock Tower Courtyard).
  { title: "Around the castle", groups: ["entrance_hall", "great_hall", "owl_exchange", "dueling_club", "castle"] },
  // Groundskeeper's Cottage, the Lake Shore and the Boathouse.
  { title: "On the grounds", groups: ["grounds"] },
  { title: "Classrooms", groups: ["class"] },
];

export interface MonstraViamOptions {
  places: () => readonly KeyPlaceView[];
  /** The destination now (null: no trail). */
  current: () => string | null;
  /** A class door's lesson in words ("door open", "in 12 min"), or "". */
  classNote: (classId: string) => string;
  /** "third floor", the dungeons: where a place is. */
  floorOf: (place: KeyPlaceView) => string;
  onChoose: (placeId: string) => void;
  onStop: () => void;
}

/** The Wayfinder Charm window: "Find my way". */
export class MonstraViamWindow {
  readonly root: HTMLElement;
  readonly button: HTMLButtonElement;
  private readonly body: HTMLElement;
  private origin: HTMLElement | null = null;

  constructor(private readonly opts: MonstraViamOptions) {
    this.button = h("button", { type: "button", class: "icon-button", "aria-label": "Wayfinder Charm: find your way", "aria-expanded": "false", title: "Wayfinder Charm: find your way (F)", onclick: () => this.toggle() }, lineIcon(MONSTRA_VIAM_ICON));
    this.body = h("div", { class: "monstra-viam-body" });
    this.root = h(
      "section",
      { class: "monstra-viam panel", role: "dialog", "aria-label": "Wayfinder Charm", hidden: true },
      h("header", { class: "craft-head" }, h("h2", {}, "Wayfinder Charm"), h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
      h("p", { class: "monstra-viam-sub" }, "Find my way: choose a place and a trail of light leads you there."),
      this.body,
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = !this.isOpen): void {
    if (open === this.isOpen) return;
    if (open) this.origin = focusOrigin();
    this.root.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    if (open) {
      this.render();
      (this.body.querySelector("button") as HTMLButtonElement | null)?.focus({ preventScroll: true });
    } else restoreFocus(this.origin, this.root);
  }

  /** Redraws while open (the class notes change). */
  refresh(): void {
    if (this.isOpen) this.render();
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.code === "KeyF" && !e.repeat) {
      this.toggle();
      return true;
    }
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  private render(): void {
    const places = this.opts.places();
    const current = this.opts.current();
    const focused = document.activeElement instanceof HTMLElement && this.body.contains(document.activeElement) ? document.activeElement.dataset.place : undefined;
    const sections = GROUPS.map((g) => {
      const mine = places.filter((p) => g.groups.includes(p.group));
      if (!mine.length) return null;
      return h(
        "section",
        { class: "monstra-viam-group" },
        h("h3", {}, g.title),
        h(
          "ul",
          { class: "monstra-viam-list" },
          ...mine.map((p) => {
            const note = p.class ? this.opts.classNote(p.class) : "";
            const on = p.id === current;
            return h(
              "li",
              {},
              h(
                "button",
                { type: "button", class: `monstra-viam-place${on ? " on" : ""}`, "data-place": p.id, "aria-pressed": on ? "true" : "false", onclick: () => (this.opts.onChoose(p.id), this.toggle(false)) },
                h("span", { class: "monstra-viam-name" }, p.name),
                h("span", { class: "monstra-viam-where" }, [this.opts.floorOf(p), note].filter(Boolean).join(" · ")),
              ),
            );
          }),
        ),
      );
    });
    const stop = current ? h("button", { type: "button", class: "secondary monstra-viam-stop", onclick: () => (this.opts.onStop(), this.render()) }, "End the trail") : null;
    this.body.replaceChildren(...[...sections, stop].filter((x): x is HTMLElement => x !== null));
    if (focused) (this.body.querySelector(`[data-place="${focused}"]`) as HTMLElement | null)?.focus({ preventScroll: true });
  }
}

/** Toasts with an action button that close themselves: the first-day tour's lines, its one-time class prompt, secrets found (class doors opening show in the class bar instead). */
export class ClassToasts {
  readonly root: HTMLElement;
  private readonly shown = new Map<string, { el: HTMLElement; until: number }>();

  constructor() {
    this.root = h("div", { class: "toasts class-toasts", "aria-live": "polite" });
  }

  /** `key` names the session (one toast each); it closes by itself at `untilMs` (performance.now()). `wayLabel` names the button. */
  show(key: string, text: string, untilMs: number, onWay: (() => void) | null, wayLabel = "Show me the way"): void {
    if (this.shown.has(key)) return;
    const close = () => this.drop(key);
    const el = h(
      "div",
      { class: "toast panel class-toast", role: "status" },
      h("p", {}, text),
      h(
        "div",
        { class: "toast-actions" },
        onWay ? h("button", { type: "button", class: "secondary toast-accept", onclick: () => (onWay(), close()) }, wayLabel) : null,
        h("button", { type: "button", class: "secondary", onclick: close }, "Dismiss"),
      ),
    );
    this.shown.set(key, { el, until: untilMs });
    this.root.append(el);
  }

  /** Whether the toast `key` is up. */
  isShown(key: string): boolean {
    return this.shown.has(key);
  }

  /** Closes toasts whose time is up. */
  tick(nowMs: number): void {
    for (const [key, t] of this.shown) if (nowMs >= t.until) this.drop(key);
  }

  private drop(key: string): void {
    this.shown.get(key)?.el.remove();
    this.shown.delete(key);
  }

  clear(prefix = ""): void {
    for (const key of [...this.shown.keys()]) if (key.startsWith(prefix)) this.drop(key);
  }
}
