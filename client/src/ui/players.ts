// The players list (U, or the "online" count in the top bar).
// Everyone online, name and house only, never where they are; a search box
// asks the server for names containing what is typed (it scales to
// thousands online: the server searches and pages, the client shows one
// page at a time). Click a name for Add friend, Whisper, Examine (the card)
// and Challenge to a duel; the actions are game.ts's (players-state.ts
// `PlayerAction`). The words and the paging are in players-state.ts.

import "./players.css";
import { authedRequest } from "../api";
import { houseInfo } from "../content/houses";
import { KeysetPager, type PageMove } from "../economy/paging";
import { errorMessage, pagerNav, windowHead } from "../economy/view";
import { h } from "./dom";
import { ACTION_LABELS, listSummary, normaliseQuery, parsePlayersPage, playerActions, playersError, playersPath, QUERY_MAX, SEARCH_DEBOUNCE_MS, sameName, type PlayerAction, type PlayerRow } from "./players-state";
import { PlayerMenu } from "./trade";

export interface PlayersWindowOptions {
  myName: string;
  /** Where the name's menu goes (fixed to the window, so not inside a moved one). */
  layer: HTMLElement;
  /** An action chosen for a player. */
  onAction: (action: PlayerAction, name: string) => void;
  /** Whether we may invite to our club (an officer or its owner): the menu offers it. */
  canClubInvite?: () => boolean;
}

export class PlayersWindow {
  readonly root: HTMLElement;
  private readonly input: HTMLInputElement;
  private readonly summary: HTMLElement;
  private readonly list: HTMLElement;
  private readonly nav: HTMLElement;
  private readonly error: HTMLElement;
  private readonly pager = new KeysetPager<PlayerRow>();
  private readonly menu = new PlayerMenu();
  /** The search the list shows (normalised). */
  private searched = "";
  private timer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;

  constructor(private readonly opts: PlayersWindowOptions) {
    this.input = h("input", {
      type: "search",
      class: "players-search",
      maxlength: QUERY_MAX,
      placeholder: "Search by name",
      "aria-label": "Search players by name",
      autocomplete: "off",
      spellcheck: "false",
      oninput: () => this.typed(),
      onkeydown: (e: Event) => {
        const k = e as KeyboardEvent;
        if (k.key === "Enter") {
          k.preventDefault();
          this.searchNow();
        } else if (k.key === "Escape") {
          // The game's keys skip a focused field: Esc here closes the window.
          k.preventDefault();
          k.stopPropagation();
          this.toggle(false);
        }
      },
    });
    this.summary = h("p", { class: "players-summary muted", "aria-live": "polite" });
    this.list = h("ul", { class: "players-list", "aria-label": "Players online" });
    this.nav = h("div", { class: "players-nav" });
    this.error = h("p", { class: "econ-error", role: "alert", hidden: true });
    this.root = h(
      "section",
      { class: "econ-window players-window panel", role: "dialog", "aria-label": "Players online", hidden: true },
      windowHead("Players online", () => this.toggle(false)),
      h("div", { class: "players-bar" }, this.input, h("button", { type: "button", class: "secondary", title: "Ask again", onclick: () => void this.load("reload") }, "Refresh")),
      this.summary,
      this.error,
      this.list,
      this.nav,
    );
    opts.layer.append(this.menu.root);
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = !this.isOpen): void {
    if (open === this.isOpen) return;
    this.root.hidden = !open;
    if (open) {
      void this.load("first");
      this.input.focus({ preventScroll: true });
    } else {
      this.menu.close();
      if (this.timer !== null) clearTimeout(this.timer);
      this.timer = null;
      if (this.root.contains(document.activeElement)) (document.activeElement as HTMLElement).blur();
    }
  }

  /** U toggles the list; Esc closes it. */
  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.code === "KeyU" && !e.repeat) {
      this.toggle();
      return true;
    }
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  /** Typing waits a moment, then searches (one request for a burst of keys). */
  private typed(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.searchNow(), SEARCH_DEBOUNCE_MS);
  }

  private searchNow(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    if (normaliseQuery(this.input.value) === this.searched && this.pager.loaded) return;
    void this.load("first");
  }

  private async load(move: PageMove): Promise<void> {
    if (move === "first") this.searched = normaliseQuery(this.input.value);
    const req = this.pager.begin(move);
    if (!req) return;
    this.render();
    try {
      const raw = await authedRequest<unknown>("GET", playersPath(this.searched, req.after));
      if (this.disposed || !this.pager.receive(req.ticket, parsePlayersPage(raw))) return;
      this.setError(null);
    } catch (e) {
      if (this.disposed || !this.pager.fail(req.ticket)) return;
      this.setError(errorMessage(e, playersError));
    }
    this.render();
  }

  private setError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  private render(): void {
    const p = this.pager;
    this.root.toggleAttribute("aria-busy", p.loading);
    if (!p.loaded) {
      this.summary.textContent = p.loading ? "Looking round the castle…" : "";
      this.list.replaceChildren();
      this.nav.replaceChildren();
      return;
    }
    this.summary.textContent = listSummary(p.items.length, this.searched);
    this.list.replaceChildren(...p.items.map((row) => this.row(row)));
    this.nav.replaceChildren(p.hasPrev || p.hasNext ? pagerNav(p, (m) => void this.load(m), "Players") : h("span", {}));
  }

  private row(row: PlayerRow): HTMLElement {
    const house = houseInfo(row.house);
    const me = sameName(row.name, this.opts.myName);
    return h(
      "li",
      { class: "players-row" },
      h(
        "button",
        {
          type: "button",
          class: "players-name",
          "aria-haspopup": "menu",
          title: `${row.name}: Add friend, Whisper, Examine, Challenge to a duel`,
          onclick: (e: Event) => this.openMenu(row.name, e as MouseEvent),
        },
        row.name,
        me ? h("span", { class: "players-you" }, " (you)") : null,
      ),
      h("span", { class: "players-house", style: `--house: ${house.colour}` }, h("span", { class: "players-crest", "aria-hidden": "true" }), house.name || row.house),
    );
  }

  private openMenu(name: string, e: MouseEvent): void {
    const target = e.currentTarget as HTMLElement | null;
    const r = target?.getBoundingClientRect();
    const x = e.clientX || (r ? r.left : 0);
    const y = e.clientY || (r ? r.bottom : 0);
    this.menu.open(
      name,
      x + 4,
      y + 4,
      playerActions(name, this.opts.myName, this.opts.canClubInvite?.() ?? false).map((a) => ({ label: ACTION_LABELS[a], run: () => this.opts.onAction(a, name) })),
    );
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer !== null) clearTimeout(this.timer);
    this.menu.dispose();
    this.menu.root.remove();
  }
}
