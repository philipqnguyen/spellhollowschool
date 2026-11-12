// The House Cup panel: the week's standings as four bars in the
// houses' palette colours, when it resets, your own points this week and
// last week's winner. Opened with H, the HUD's cup button or /cup; a common
// room's notice board opens it with the house's next classes too, each with
// "Show me the way" (the wayfinding trail to its door). The words come from
// house-cup-state.ts; this only draws them.

import "./house-cup.css";
import { houseColours, lastText, mineText, resetText, type CupState } from "./house-cup-state";
import { focusOrigin, h, restoreFocus } from "./dom";
import { lineIcon } from "./effects-icons";

/** A cup with two handles on a foot: the House Cup button and the nameplate badge. */
export const CUP_ICON = ["M7 4h10v5a5 5 0 0 1-10 0Z", "M7 6H4.5a2.5 2.5 0 0 0 2.6 4", "M17 6h2.5a2.5 2.5 0 0 1-2.6 4", "M12 14v3.5", "M8.5 20.5h7l-1-3h-5Z"];

/** A class on the notice board. */
export interface BoardClass {
  classId: string;
  name: string;
  /** "08:00" game time. */
  at: string;
  /** "in 12 min", "Door open, closes in 2:41". */
  status: string;
  where: string;
}

export interface HouseCupPanelOptions {
  /** Our house id (`"skytalon"`). */
  house: string;
  /** The house's next classes for the notice board. */
  boardClasses: () => readonly BoardClass[];
  /** "Show me the way": the trail to a class's door. */
  onWay: (classId: string) => void;
  /** The panel opened (fetch fresh standings if they are old). */
  onOpen: () => void;
}

export class HouseCupPanel {
  readonly root: HTMLElement;
  readonly button: HTMLButtonElement;
  private readonly title: HTMLElement;
  private readonly body: HTMLElement;
  private origin: HTMLElement | null = null;
  private state: CupState | null = null;
  private board = false;
  private key = "";

  constructor(private readonly opts: HouseCupPanelOptions) {
    this.button = h("button", { type: "button", class: "icon-button", "aria-label": "The House Cup", "aria-expanded": "false", title: "The House Cup (H)", onclick: () => this.toggle() }, lineIcon(CUP_ICON));
    this.title = h("h2", {}, "The House Cup");
    this.body = h("div", { class: "cup-body" });
    this.root = h(
      "section",
      { class: "cup-panel panel", role: "dialog", "aria-label": "The House Cup", hidden: true },
      h("header", { class: "craft-head" }, this.title, h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
      this.body,
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** Opens or closes it; `board` adds the house's next classes (a notice board). */
  toggle(open = !this.isOpen, board = false): void {
    if (open && this.isOpen && board !== this.board) {
      this.board = board;
      this.render(true);
      return;
    }
    if (open === this.isOpen) return;
    if (open) {
      this.origin = focusOrigin();
      this.board = board;
      this.opts.onOpen();
    }
    this.root.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    if (open) {
      this.render(true);
      (this.root.querySelector(".cup-body button, header button") as HTMLButtonElement | null)?.focus({ preventScroll: true });
    } else restoreFocus(this.origin, this.root);
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.code === "KeyH" && !e.repeat) {
      this.toggle();
      return true;
    }
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  /** New standings. */
  update(s: CupState): void {
    this.state = s;
    this.render(false);
  }

  /** Once a second while open: the countdown and the class statuses. */
  tick(): void {
    this.render(false);
  }

  private render(force: boolean): void {
    if (!this.isOpen) return;
    const s = this.state;
    const now = Date.now();
    const classes = this.board ? this.opts.boardClasses() : [];
    const key = s ? `${this.board}|${s.houses.map((x) => x.points).join(",")}|${s.mine}|${resetText(s, now)}|${lastText(s)}|${classes.map((c) => `${c.classId}${c.at}${c.status}`).join(",")}` : `none|${this.board}`;
    if (!force && key === this.key) return;
    this.key = key;
    this.title.textContent = this.board ? "Notice Board" : "The House Cup";
    const focused = document.activeElement instanceof HTMLElement && this.body.contains(document.activeElement) ? document.activeElement.dataset.class : undefined;
    const cup = s
      ? [
          h("p", { class: "cup-reset" }, resetText(s, now)),
          h(
            "ol",
            { class: "cup-bars", "aria-label": "This week's standings" },
            ...s.houses.map((x) => {
              const c = houseColours(x.id);
              return h(
                "li",
                { class: `cup-row${x.leading ? " leading" : ""}${x.id === this.opts.house ? " mine" : ""}` },
                h("span", { class: "cup-name" }, x.name),
                h("span", { class: "cup-track", "aria-hidden": "true" }, h("span", { class: "cup-fill", style: `width: ${(x.share * 100).toFixed(1)}%; --fill: ${c.fill}; --edge: ${c.edge}` })),
                h("span", { class: "cup-points" }, `${x.points}`),
              );
            }),
          ),
          h("p", { class: "cup-mine" }, mineText(s, this.opts.house)),
          h("p", { class: "cup-last" }, lastText(s)),
        ]
      : [h("p", { class: "muted-line" }, "Fetching the standings…")];
    const board = this.board
      ? [
          h("h3", { class: "cup-sub" }, "Your next classes"),
          classes.length
            ? h(
                "ul",
                { class: "cup-classes" },
                ...classes.map((c) =>
                  h(
                    "li",
                    {},
                    h("div", { class: "sched-line" }, h("strong", {}, `${c.at} ${c.name}`), c.status ? h("span", { class: "sched-status" }, c.status) : null),
                    h("span", { class: "sched-where" }, c.where),
                    h("button", { type: "button", class: "secondary sched-way", "data-class": c.classId, onclick: () => (this.opts.onWay(c.classId), this.toggle(false)) }, "Show me the way"),
                  ),
                ),
              )
            : h("p", { class: "muted-line" }, "No classes are on the timetable."),
          h("h3", { class: "cup-sub" }, "The House Cup"),
        ]
      : [];
    this.body.replaceChildren(...board, ...cup);
    if (focused) (this.body.querySelector(`[data-class="${focused}"]`) as HTMLElement | null)?.focus({ preventScroll: true });
  }
}
