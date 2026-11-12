// The dorm HUD (docs/architecture.md, "Dorms and decorating"): a panel naming whose room
// this is, with the owner's controls (decorate, open to friends, invite a
// friend, send a guest out) and Leave for everyone; and, while decorating,
// the decorating panel: the owner's furniture as a searchable, filterable
// list with icons and counts, the item cap counter and the controls of
// decorating mode.
//
// Both are views: dorm/controller.ts owns the state and calls `update`.

import { itemIcon } from "../economy/icons";
import { CATEGORY_LABELS } from "../game/dorm/catalog";
import { categoryCounts, categoryLabel, filterRows, type CategoryFilter, type FurnitureRow } from "../game/dorm/furniture-list";
import type { FurnitureItem } from "../game/dorm/inventory";
import type { PlaceSurface } from "../wasm/sim_wasm";
import { h } from "./dom";
import "./dorm.css";

export interface Occupant {
  id: number;
  name: string;
}

export interface DormPanelState {
  ownerName: string;
  owner: boolean;
  /** A Wishing Room (its name: "Gathering Room"), not a dorm: nothing to decorate. */
  wishingRoom?: string;
  openToFriends: boolean;
  decorating: boolean;
  /** Other characters in the room (the guests, or the owner and guests for a guest). */
  occupants: readonly Occupant[];
  /** Online friends the owner can invite. */
  invitable: readonly string[];
}

export interface DormPanelOptions {
  onDecorate: () => void;
  onSetOpen: (open: boolean) => void;
  onInvite: (name: string) => void;
  onKick: (entity: number) => void;
  onLeave: () => void;
}

export class DormPanel {
  readonly root: HTMLElement;
  private readonly title: HTMLElement;
  private readonly ownerTools: HTMLElement;
  private readonly openBox: HTMLInputElement;
  private readonly decorate: HTMLButtonElement;
  private readonly inviteSelect: HTMLSelectElement;
  private readonly inviteButton: HTMLButtonElement;
  private readonly people: HTMLElement;
  /** The invitable names last rendered (null: nothing rendered yet). */
  private lastKey: string | null = null;

  constructor(private readonly opts: DormPanelOptions) {
    this.title = h("h2", {}, "");
    this.decorate = h("button", { type: "button", class: "secondary", onclick: () => opts.onDecorate(), title: "Decorate (B)" }, "Decorate");
    this.openBox = h("input", { type: "checkbox", onchange: () => opts.onSetOpen(this.openBox.checked) });
    this.inviteSelect = h("select", { class: "dorm-invite-select", "aria-label": "Friend to invite" });
    this.inviteButton = h(
      "button",
      {
        type: "button",
        class: "secondary",
        onclick: () => {
          const name = this.inviteSelect.value;
          if (name) opts.onInvite(name);
        },
      },
      "Invite",
    );
    this.ownerTools = h(
      "div",
      { class: "dorm-owner" },
      this.decorate,
      h("label", { class: "dorm-check" }, this.openBox, h("span", {}, "Open to friends")),
      h("div", { class: "dorm-invite" }, this.inviteSelect, this.inviteButton),
    );
    this.people = h("ul", { class: "dorm-people", "aria-label": "In this room" });
    this.root = h(
      "aside",
      { class: "dorm-panel panel", "aria-label": "Dormitory" },
      this.title,
      this.ownerTools,
      this.people,
      h("button", { type: "button", class: "secondary dorm-leave", onclick: () => opts.onLeave() }, "Leave the room"),
    );
  }

  update(s: DormPanelState): void {
    this.title.textContent = s.wishingRoom ? (s.owner ? s.wishingRoom : `${s.ownerName}'s ${s.wishingRoom}`) : s.owner ? "Your room" : `${s.ownerName}'s room`;
    this.ownerTools.hidden = !s.owner;
    this.decorate.hidden = !!s.wishingRoom;
    this.openBox.checked = s.openToFriends;
    this.decorate.textContent = s.decorating ? "Done decorating" : "Decorate";
    this.decorate.setAttribute("aria-pressed", String(s.decorating));

    const inviteKey = s.invitable.join("\n");
    if (inviteKey !== this.lastKey) {
      this.lastKey = inviteKey;
      const keep = this.inviteSelect.value;
      this.inviteSelect.replaceChildren(
        ...(s.invitable.length ? s.invitable.map((n) => h("option", { value: n, selected: n === keep }, n)) : [h("option", { value: "" }, "No friends online")]),
      );
    }
    this.inviteSelect.disabled = this.inviteButton.disabled = s.invitable.length === 0;

    this.people.replaceChildren(
      ...s.occupants.map((o) =>
        h(
          "li",
          {},
          h("span", { class: "dorm-person" }, o.name),
          s.owner ? h("button", { type: "button", class: "link-button", onclick: () => this.opts.onKick(o.id), title: `Send ${o.name} back` }, "Send out") : null,
        ),
      ),
    );
    this.people.hidden = s.occupants.length === 0;
  }
}

// ------------------------------------------------------------------ decorating list

export interface DecorStripState {
  /** Every look the owner has (furniture-list.ts `buildRows`), before search and filter. */
  rows: readonly FurnitureRow[];
  loading: boolean;
  query: string;
  category: CategoryFilter;
  /** Selected item id and whether it is a placed piece being moved. */
  selected: { itemId: number; existing: boolean; name: string } | null;
  count: number;
  cap: number;
  precise: boolean;
  /** Rotation in degrees, for the selected piece. */
  degrees: number;
  status: string;
}

export interface DecorStripOptions {
  onPick: (item: FurnitureItem) => void;
  onSearch: (query: string) => void;
  onCategory: (category: CategoryFilter) => void;
  onRotate: (dir: 1 | -1) => void;
  onPrecise: () => void;
  onPutAway: () => void;
  onCancel: () => void;
  onDone: () => void;
}

const SURFACE_LABEL: Record<PlaceSurface, string> = { floor: "Floor", wall: "Wall", table: "Table top" };

/** The Precise button's tooltip. */
export const PRECISE_TITLE = "Precise placement: 5 cm steps and fine turning";

/**
 * The decorating panel: a search box and a category filter over a
 * scrolling list of the owner's furniture (a row per look: icon, name,
 * how many are in the trunk of how many owned), the controls for the piece
 * in hand, and the status line.
 */
export class DecorStrip {
  readonly root: HTMLElement;
  private readonly counter: HTMLElement;
  private readonly precise: HTMLButtonElement;
  private readonly search: HTMLInputElement;
  private readonly filter: HTMLSelectElement;
  private readonly list: HTMLElement;
  private readonly hint: HTMLElement;
  private readonly status: HTMLElement;
  private readonly selectedTools: HTMLElement;
  private readonly putAway: HTMLButtonElement;
  /** What the list was last drawn from, to skip redrawing an unchanged list. */
  private listKey = "";
  private filterKey = "";

  constructor(private readonly opts: DecorStripOptions) {
    this.counter = h("span", { class: "decor-count" }, "");
    this.precise = h("button", { type: "button", class: "secondary decor-precise", onclick: () => opts.onPrecise(), title: PRECISE_TITLE }, "Precise (G)");
    this.search = h("input", {
      type: "search",
      class: "decor-search",
      placeholder: "Search your furniture",
      "aria-label": "Search your furniture",
      oninput: () => opts.onSearch(this.search.value),
      onkeydown: ((e: KeyboardEvent) => {
        // Esc leaves the box (the game's keys come back) rather than ending decorating.
        if (e.key === "Escape") {
          e.stopPropagation();
          this.search.blur();
        }
      }) as EventListener,
    });
    this.filter = h("select", { class: "decor-filter", "aria-label": "Category", onchange: () => opts.onCategory(this.filter.value as CategoryFilter) });
    this.list = h("ul", { class: "decor-list", "aria-label": "Your furniture" });
    this.hint = h("p", { class: "decor-hint" }, "");
    this.status = h("p", { class: "decor-status", role: "status" }, "");
    this.putAway = h("button", { type: "button", class: "secondary", onclick: () => opts.onPutAway(), title: "Put away (Del)" }, "Put away");
    this.selectedTools = h(
      "div",
      { class: "decor-selected" },
      h("button", { type: "button", class: "secondary", onclick: () => opts.onRotate(1), title: "Turn left (Q)" }, "⟲"),
      h("button", { type: "button", class: "secondary", onclick: () => opts.onRotate(-1), title: "Turn right (E)" }, "⟳"),
      this.putAway,
      h("button", { type: "button", class: "secondary", onclick: () => opts.onCancel(), title: "Cancel (Esc)" }, "Cancel"),
    );
    this.root = h(
      "section",
      { class: "decor-strip panel", "aria-label": "Decorating" },
      h(
        "header",
        { class: "decor-head" },
        h("h2", {}, "Decorating"),
        this.counter,
        this.precise,
        h("button", { type: "button", class: "secondary", onclick: () => opts.onDone() }, "Done"),
      ),
      h("div", { class: "decor-tools" }, this.search, this.filter),
      this.list,
      this.selectedTools,
      this.hint,
      this.status,
    );
  }

  update(s: DecorStripState): void {
    this.counter.textContent = `${s.count} / ${s.cap} pieces`;
    this.counter.classList.toggle("full", s.count >= s.cap);
    this.precise.setAttribute("aria-pressed", String(s.precise));
    if (this.search.value !== s.query && document.activeElement !== this.search) this.search.value = s.query;
    this.updateFilter(s);
    this.updateList(s);

    this.selectedTools.hidden = !s.selected;
    this.putAway.hidden = !s.selected?.existing;
    const precise = s.precise ? " · precise" : "";
    this.hint.textContent = !s.selected
      ? "Pick a piece from the list, or click one in the room to move it."
      : `${s.selected.name}, turned ${s.degrees}°${precise}. Click to set it down · Q/E turn · G precise${s.selected.existing ? " · Del put away" : ""} · Esc cancel`;
    this.status.textContent = s.status;
  }

  private updateFilter(s: DecorStripState): void {
    const counts = categoryCounts(s.rows);
    const key = `${counts.map(([c, n]) => `${c}${n}`).join(",")}|${s.category}`;
    if (key === this.filterKey) return;
    this.filterKey = key;
    const options: [CategoryFilter, number][] = [["all", s.rows.length], ...counts];
    // Keep the chosen category listed even when nothing is left in it.
    if (s.category !== "all" && !counts.some(([c]) => c === s.category)) options.push([s.category, 0]);
    this.filter.replaceChildren(...options.map(([c, n]) => h("option", { value: c, selected: c === s.category }, `${categoryLabel(c)} (${n})`)));
  }

  private updateList(s: DecorStripState): void {
    const shown = filterRows(s.rows, s.query, s.category);
    const key = `${s.loading}|${s.selected?.itemId ?? ""}|${s.query}|${s.category}|${shown.map((r) => `${r.key}:${r.available.map((i) => i.id).join(".")}/${r.owned}`).join(",")}`;
    if (key === this.listKey) return;
    this.listKey = key;
    const rows = shown.map((r) => {
      const selected = s.selected !== null && !s.selected.existing && r.available.some((i) => i.id === s.selected!.itemId);
      const free = r.available.length;
      return h(
        "li",
        {},
        h(
          "button",
          {
            type: "button",
            class: `decor-row${selected ? " selected" : ""}`,
            disabled: free === 0,
            "aria-pressed": String(selected),
            title: free === 0 ? `All your ${r.name} pieces are in the room.` : `${free} in your trunk, ${r.owned} owned`,
            onclick: () => {
              const item = r.available[0];
              if (item) this.opts.onPick(item);
            },
          },
          itemIcon(r.itemDef, r.dyes, "small"),
          h("span", { class: "decor-row-text" }, h("span", { class: "decor-name" }, r.name), h("span", { class: "decor-surface" }, `${CATEGORY_LABELS[r.category]} · ${SURFACE_LABEL[r.surface]}`)),
          h("span", { class: "decor-qty", "aria-label": `${free} available of ${r.owned} owned` }, `${free} / ${r.owned}`),
        ),
      );
    });
    if (rows.length === 0) {
      const text = s.loading
        ? "Opening your trunk…"
        : s.rows.length === 0
          ? "You have no furniture yet. The Owl Order Catalogue in your common room sells some."
          : "Nothing matches. Try another word or category.";
      rows.push(h("li", { class: "decor-empty" }, text));
    }
    this.list.replaceChildren(...rows);
  }
}
