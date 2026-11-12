// The HUD class bar (docs/architecture.md, "Classes and progression"): the segment in
// the level and clock strip that reads "Classes starting in 2:41" while
// classroom doors admit students (glowing green until they close) and
// "Classes starting in 12 min" between slots, and its small panel: the
// slot's classes, where each is, how long its door stays open (or when it
// opens), and "Show me the way". It replaced the door-opening toasts. The
// words come from schedule-state.ts `classBar`; this only draws them, and
// only touches the DOM when a line changes.
//
// The panel is a HUD window (ui/windows.ts: its open and close sounds, Esc,
// dragging by its title); a click outside it, or on the segment again,
// closes it too.

import "./class-bar.css";
import { focusOrigin, h, restoreFocus } from "./dom";
import type { ClassBarView } from "./schedule-state";

export interface ClassBarOptions {
  /** A row's "Show me the way": the trail to the class's door (null: no trail in this build). */
  onWay: ((classId: string) => void) | null;
  /** The panel's "Full timetable". */
  onTimetable: () => void;
}

export class ClassBar {
  /** The segment, for the HUD strip. */
  readonly button: HTMLButtonElement;
  /** The panel, for the HUD layer. */
  readonly root: HTMLElement;
  /** Screen reader announcements (a door opening), for the HUD strip. */
  readonly live: HTMLElement;
  private readonly lead: HTMLElement;
  private readonly time: HTMLElement;
  private readonly title: HTMLElement;
  private readonly body: HTMLElement;
  private shownKey = "";
  private bodyKey = "";
  private origin: HTMLElement | null = null;
  private last: ClassBarView | null = null;
  private readonly onDocDown = (e: PointerEvent) => {
    const t = e.target as Node | null;
    if (t && !this.root.contains(t) && !this.button.contains(t)) this.toggle(false);
  };

  constructor(private readonly opts: ClassBarOptions) {
    this.lead = h("span", { class: "cbar-lead" });
    this.time = h("span", { class: "cbar-time" });
    this.button = h(
      "button",
      {
        type: "button",
        class: "cbar",
        "aria-expanded": "false",
        "aria-haspopup": "dialog",
        hidden: true,
        onclick: () => this.toggle(),
        // Enter presses the focused segment (the game's own Enter, on the window, would open the chat instead).
        onkeydown: (e: Event) => {
          if ((e as KeyboardEvent).key === "Enter") e.stopPropagation();
        },
      },
      this.lead,
      this.time,
    );
    this.live = h("p", { class: "sr-only", "aria-live": "polite" });
    this.title = h("h2", {}, "Classes");
    this.body = h("div", { class: "cbar-body" });
    this.root = h(
      "section",
      { class: "cbar-pop panel", role: "dialog", "aria-label": "Classes", hidden: true },
      h("header", { class: "cbar-head" }, this.title, h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
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
      this.bodyKey = "";
      if (this.last) this.renderBody(this.last);
      window.addEventListener("pointerdown", this.onDocDown, true);
    } else {
      window.removeEventListener("pointerdown", this.onDocDown, true);
      restoreFocus(this.origin, this.root);
    }
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  /** Says something to screen readers (a door opening). */
  announce(text: string): void {
    this.live.textContent = text;
  }

  /** The bar at this moment (a few times a second). */
  update(v: ClassBarView): void {
    this.last = v;
    const key = `${v.kind}|${v.text}`;
    if (key !== this.shownKey) {
      this.shownKey = key;
      this.button.hidden = v.kind === "none";
      this.button.classList.toggle("open", v.kind === "open");
      // "Classes starting in" shortens to "Classes in" on a narrow screen (class-bar.css); the time never does.
      if (v.time) {
        this.lead.replaceChildren("Classes ", h("span", { class: "cbar-long" }, "starting "), "in ");
        this.time.textContent = v.time;
      } else {
        this.lead.textContent = v.text;
        this.time.textContent = "";
      }
      const names = v.rows.map((r) => r.name).join(", ");
      this.button.title = names ? `${v.text}: ${names}. Click for the classes.` : v.text;
      this.button.setAttribute("aria-label", `${v.text}. Show the classes`);
    }
    if (this.isOpen) this.renderBody(v);
  }

  private renderBody(v: ClassBarView): void {
    const key = `${v.heading}|${v.rows.map((r) => `${r.classId}:${r.state}:${r.status}`).join(",")}`;
    if (key === this.bodyKey) return;
    this.bodyKey = key;
    this.title.textContent = v.heading;
    // Keep the keyboard on the same Wayfinder Charm button across redraws.
    const active = document.activeElement;
    const focused = active instanceof HTMLElement && this.body.contains(active) ? active.dataset.class : undefined;
    const rows = v.rows.map((r) =>
      h(
        "li",
        { class: `cbar-row ${r.state}` },
        h("div", { class: "cbar-line" }, h("strong", {}, r.name), h("span", { class: `cbar-status ${r.state}` }, r.status)),
        h("span", { class: "cbar-where" }, r.where),
        r.wayable && this.opts.onWay ? h("button", { type: "button", class: "secondary cbar-way", "data-class": r.classId, onclick: () => this.way(r.classId) }, "Show me the way") : null,
      ),
    );
    this.body.replaceChildren(
      rows.length ? h("ul", { class: "cbar-rows" }, ...rows) : h("p", { class: "muted-line" }, "No classes are running this term."),
      h("button", { type: "button", class: "secondary cbar-all", onclick: () => this.opts.onTimetable() }, "Full timetable"),
    );
    if (focused) (this.body.querySelector(`[data-class="${focused}"]`) as HTMLElement | null)?.focus({ preventScroll: true });
  }

  private way(classId: string): void {
    this.toggle(false);
    this.opts.onWay?.(classId);
  }

  dispose(): void {
    window.removeEventListener("pointerdown", this.onDocDown, true);
    this.button.remove();
    this.root.remove();
    this.live.remove();
  }
}
