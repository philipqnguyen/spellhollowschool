// Watching duels at the Dueling Club: the list of duels under way
// (`DuelListRequest` → `DuelList`, refreshed every few seconds while open),
// each with a Watch button (`DuelSpectate`: into the arena with no actions;
// `LeaveInstance` comes back). Only from the club's area: the window closes
// when we walk out of it or go into an arena.

import "../economy/economy.css";
import "./spectate.css";
import { houseInfo } from "../content/houses";
import type { DuelSummary } from "../wasm/sim_wasm";
import { focusOrigin, h, restoreFocus } from "./dom";

export interface SpectateWindowOptions {
  /** Asks the server for the list (`encodeDuelListRequest`). */
  requestList: () => void;
  /** Watch duel `duelId` (`encodeDuelSpectate`). */
  watch: (duelId: number) => void;
}

const REFRESH_MS = 4000;

/** "1:07" for a fight `ms` long. */
export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export class SpectateWindow {
  readonly root: HTMLElement;
  private readonly list: HTMLElement;
  private readonly status: HTMLElement;
  private timer: ReturnType<typeof setInterval> | null = null;
  private opener: HTMLElement | null = null;
  private waiting: number | null = null;

  constructor(private readonly opts: SpectateWindowOptions) {
    this.list = h("ul", { class: "spectate-list", "aria-label": "Duels under way" });
    this.status = h("p", { class: "muted", role: "status" }, "Looking for duels…");
    this.root = h(
      "section",
      { class: "econ-window spectate-window panel", role: "dialog", "aria-label": "Watch a duel", hidden: true },
      h("header", { class: "econ-head" }, h("h2", {}, "Watch a duel"), h("button", { type: "button", class: "icon-button", "aria-label": "Close the duel list", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
      h("p", { class: "muted" }, "Spectators stand by the stage and can't cast. Leave whenever you like."),
      this.status,
      this.list,
      h("div", { class: "econ-actions" }, h("button", { type: "button", class: "secondary", onclick: () => this.refresh() }, "Refresh")),
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = !this.isOpen): void {
    if (open === this.isOpen) return;
    this.root.hidden = !open;
    if (open) {
      this.opener = focusOrigin();
      this.status.textContent = "Looking for duels…";
      this.refresh();
      this.timer = setInterval(() => this.refresh(), REFRESH_MS);
    } else {
      if (this.timer !== null) clearInterval(this.timer);
      this.timer = null;
      this.waiting = null;
      restoreFocus(this.opener, this.root);
      this.opener = null;
    }
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  private refresh(): void {
    this.opts.requestList();
  }

  /** `DuelList`. */
  show(duels: readonly DuelSummary[]): void {
    if (!this.isOpen) return;
    this.status.textContent = duels.length ? "" : "No duels right now. Challenge someone yourself!";
    this.status.hidden = duels.length > 0;
    this.list.replaceChildren(
      ...duels.map((d) =>
        h(
          "li",
          { class: "spectate-row" },
          h(
            "span",
            { class: "spectate-vs" },
            h("span", { class: "spectate-name", style: `--house: ${houseInfo(d.aHouse).colour}` }, d.aName),
            " vs ",
            h("span", { class: "spectate-name", style: `--house: ${houseInfo(d.bHouse).colour}` }, d.bName),
            h("span", { class: "spectate-meta" }, `${formatElapsed(d.elapsedMs)} in · ${d.spectators} watching`),
          ),
          h(
            "button",
            {
              type: "button",
              class: "secondary",
              disabled: this.waiting === d.duelId,
              onclick: () => {
                this.waiting = d.duelId;
                this.opts.watch(d.duelId);
              },
            },
            this.waiting === d.duelId ? "Going in…" : "Watch",
          ),
        ),
      ),
    );
  }

  /** A refusal while the window is open (shown in it). */
  error(text: string): void {
    this.waiting = null;
    this.status.hidden = false;
    this.status.textContent = text;
  }

  dispose(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.root.remove();
  }
}
