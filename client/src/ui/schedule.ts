// The HUD's clock and timetable: a chip in the level and clock strip with
// the time of day (a clock face, "1:31 am" and the sun or moon: Fixes round
// 1; the classes' countdown beside it is the class bar, class-bar.ts);
// clicking it (or /schedule, or the class panel's "Full timetable") opens the
// timetable: the day's slots grouped by period, each with its
// classes, where they are and how they stand ("in 12 min", "door open"),
// and a Wayfinder Charm button for a class starting soon or with its door open.
// The words come from schedule-state.ts; this only draws them, and only
// touches the DOM when a line changes.

import "./schedule.css";
import type { Period } from "../wasm/sim_wasm";
import { focusOrigin, h, restoreFocus } from "./dom";
import { clockHeading, nextPeriodText, periodLabel, type PeriodGroup } from "./schedule-state";

/** Everything the chip and the timetable show at one moment. */
export interface ScheduleState {
  /** "9:41 am" game time (`clockText`). */
  clock: string;
  /** Game seconds since midnight now, and at the next period's start. */
  gameSecs: number;
  nextPeriodGameSecs: number;
  period: Period;
  /** Real ms until the next period begins, and which it is. */
  nextPeriodMs: number;
  nextPeriod: Period;
  /** The day's slots with classes, by period, from the one under way. */
  groups: PeriodGroup[];
  /** Real minutes a door stays open, and a lesson lasts. */
  joinMins: number;
  lessonMins: number;
}

export class ScheduleChip {
  /** The chip, for the HUD bar. */
  readonly button: HTMLButtonElement;
  /** The timetable popover, for the HUD layer. */
  readonly root: HTMLElement;
  private readonly icon: HTMLElement;
  private readonly time: HTMLElement;
  private readonly body: HTMLElement;
  private shownKey = "";
  private bodyKey = "";
  private origin: HTMLElement | null = null;
  private last: ScheduleState | null = null;

  /** `onWay`: a class's Wayfinder Charm button (null: none). */
  constructor(private readonly onWay: ((classId: string) => void) | null = null) {
    this.icon = h("span", { class: "sched-icon", "aria-hidden": "true" });
    this.time = h("span", { class: "sched-time" }, "--:--");
    // The time of day sits in a part of its own (a clock face, "1:31 am", the sun or
    // moon), apart from the class bar, so it never reads as a lesson's timer.
    this.button = h(
      "button",
      { type: "button", class: "sched-chip", "aria-expanded": "false", "aria-label": "Timetable", title: "Timetable", onclick: () => this.toggle() },
      h("span", { class: "sched-clockface", "aria-label": "Time of day" }, clockIcon(), this.time, this.icon),
    );
    this.body = h("div", { class: "sched-body" });
    this.root = h(
      "section",
      { class: "sched-pop panel", role: "dialog", "aria-label": "Timetable", hidden: true },
      h("header", { class: "sched-head" }, h("h2", {}, "Timetable"), h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
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
    } else {
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

  /** The state at this moment (a few times a second). */
  update(s: ScheduleState): void {
    this.last = s;
    const night = s.period === "night";
    const key = `${s.clock}|${night}`;
    if (key !== this.shownKey) {
      this.shownKey = key;
      this.icon.className = `sched-icon ${night ? "moon" : "sun"}`;
      this.time.textContent = s.clock;
      this.button.title = `${clockHeading(s.clock, s.gameSecs)}. Click for the timetable.`;
    }
    if (this.isOpen) this.renderBody(s);
  }

  private renderBody(s: ScheduleState): void {
    const lines = s.groups.map((g) => g.slots.map((sl) => sl.classes.map((c) => `${c.kind}:${c.status}:${c.wayable}`).join(",")).join(";")).join("/");
    const sub = nextPeriodText(s.nextPeriod, s.nextPeriodGameSecs, s.nextPeriodMs);
    const key = `${s.clock}|${s.period}|${sub}|${lines}`;
    if (key === this.bodyKey) return;
    this.bodyKey = key;
    // Keep the keyboard on the same Wayfinder Charm button across redraws.
    const active = document.activeElement;
    const focusedWay = active instanceof HTMLElement && this.body.contains(active) ? active.dataset.class : undefined;
    const groups = s.groups.map((g) =>
      h(
        "section",
        { class: "sched-period" },
        h("h3", {}, `${periodLabel(g.period)} lessons`),
        ...g.slots.map((slot) =>
          h(
            "div",
            { class: "sched-slot" },
            h("span", { class: "sched-at" }, slot.at),
            h(
              "ul",
              { class: "sched-rows" },
              ...slot.classes.map((c) =>
                h(
                  "li",
                  { class: `sched-row ${c.kind}` },
                  h("div", { class: "sched-line" }, h("strong", {}, c.name), c.status ? h("span", { class: `sched-status ${c.kind}` }, c.status) : null),
                  h("span", { class: "sched-where" }, c.where),
                  c.wayable && this.onWay ? h("button", { type: "button", class: "secondary sched-way", "data-class": c.classId, onclick: () => this.onWay?.(c.classId) }, "Show me the way") : null,
                ),
              ),
            ),
          ),
        ),
      ),
    );
    this.body.replaceChildren(
      h("p", { class: "sched-clock" }, clockHeading(s.clock, s.gameSecs), h("span", { class: "sched-sub" }, ` · ${sub}`)),
      groups.length ? h("div", { class: "sched-groups" }, ...groups) : h("p", { class: "muted-line" }, "No classes are running this term."),
      h("p", { class: "sched-foot" }, `Times are game time; a game day lasts two real hours. A lesson lasts about ${s.lessonMins} minutes, and its door admits students for the first ${s.joinMins}: walk up to it and press E.`),
    );
    if (focusedWay) (this.body.querySelector(`[data-class="${focusedWay}"]`) as HTMLElement | null)?.focus({ preventScroll: true });
  }
}

/** A small clock face (the chip's time-of-day mark). */
function clockIcon(): SVGSVGElement {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("sched-clock-icon");
  const face = document.createElementNS(NS, "circle");
  face.setAttribute("cx", "8");
  face.setAttribute("cy", "8");
  face.setAttribute("r", "6.3");
  const hands = document.createElementNS(NS, "path");
  hands.setAttribute("d", "M8 4.4V8l2.6 1.6");
  svg.append(face, hands);
  return svg;
}
