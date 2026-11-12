// The Owl Exchange: the auction counter in the Entrance Hall. It opens only
// when the player uses the counter (`Interact` → `StationOpened { kind:
// owl_exchange }`), and closes when they walk away or the counter's grant
// runs out, so the hall stays the castle's crossroads. Your own listings
// (and taking one down) work anywhere: /listings.
//
//   Browse       category tree, name search, level, colour and house filters,
//                sort by price or newest, pages of 25 (keyset: Previous /
//                Next), the item in 3D with its maker's mark, buy (with a
//                confirmation step). The item and the seller's proceeds go
//                by owl mail.
//   Sell         pick from your trunk, set a price: the fee, the tax and what
//                you will receive are shown before you list.
//   My listings  what you have up (time left), sold, taken down or unsold;
//                take one down (the item comes back by owl).
//
// Rules and numbers: economy/exchange-state.ts and economy/money.ts.

import "../economy/economy.css";
import "./exchange.css";
import { ApiError } from "../api";
import { PALETTE, paletteHex } from "../content/catalog";
import * as econApi from "../economy/api";
import type { Listing, ListingState } from "../economy/api";
import {
  activeFilterCount,
  browseQuery,
  defaultFilters,
  eachText,
  EXCHANGE_PAGE,
  exchangeError,
  filtersKey,
  LEVEL_MAX,
  LEVEL_MIN,
  LISTING_STATE_LABELS,
  SEARCH_DEBOUNCE_MS,
  sellProblem,
  sellQuote,
  SORT_LABELS,
  timeLeft,
  type BrowseFilters,
  type ExchangeSort,
} from "../economy/exchange-state";
import { itemIcon } from "../economy/icons";
import { categoryPath, defName, econDef, exchangeCategories, FINISH_LABELS, houseThemes, KIND_LABELS, makersMark, requiredLevel, SLOT_NAMES, stackLimit, type EconItem } from "../economy/items";
import { ECONOMY, formatCrowns } from "../economy/money";
import { isRetryable, KeysetPager, RetryKeys } from "../economy/paging";
import { ItemPreview } from "../economy/preview";
import { errorMessage, crowns, ItemPicker, itemRow, pagerNav, tabBar, wholeInput, windowHead } from "../economy/view";
import type { Appearance } from "../wasm/sim_wasm";
import { focusOrigin, h, mount, restoreFocus } from "./dom";
import { wearTag } from "./wear";

export interface ExchangeWindowOptions {
  /** Our character name: our own listings show in Browse but cannot be bought. */
  myName: string;
  balance: () => number | null;
  onBalance: (crowns: number) => void;
  notify: (text: string) => void;
  /** Our look, for trying clothes on in the preview. */
  appearance: () => Promise<Appearance | null>;
}

type Tab = "browse" | "sell" | "mine";
type MineFilter = ListingState | "all";

const MINE_FILTERS: { id: MineFilter; label: string }[] = [
  { id: "active", label: "On sale" },
  { id: "sold", label: "Sold" },
  { id: "expired", label: "Unsold" },
  { id: "cancelled", label: "Taken down" },
  { id: "all", label: "All" },
];

export class ExchangeWindow {
  readonly root: HTMLElement;
  private readonly tabs: { el: HTMLElement; select: (t: Tab) => void };
  private readonly purse: HTMLElement;
  private readonly counterNote: HTMLElement;
  /** The clerk's greeting (NPCs), while the window is open from the counter. */
  private readonly greeting: HTMLElement = h("p", { class: "econ-greeting", role: "status", hidden: true });
  private readonly error: HTMLElement;
  private readonly browsePanel: HTMLElement;
  private readonly sellPanel: HTMLElement;
  private readonly minePanel: HTMLElement;
  private readonly tree: HTMLElement;
  private readonly results: HTMLElement;
  private readonly detail: HTMLElement;
  private readonly filterCount: HTMLElement;
  private readonly swatches: HTMLElement;
  private readonly preview: ItemPreview;

  private tab: Tab = "browse";
  /** Whether the counter's grant holds (we are at the counter). */
  private atCounter = false;
  private grantUntil = 0;
  private filters: BrowseFilters = defaultFilters();
  private searched = "";
  private searchTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly browse = new KeysetPager<Listing>();
  private selected: Listing | null = null;
  private confirmBuy = false;
  private readonly expanded = new Set<string>();

  private picker: ItemPicker | null = null;
  private selling: EconItem | null = null;
  private sellQty = 1;
  private sellPrice: number | null = null;
  private activeCount: number | null = null;

  private readonly mine = new KeysetPager<Listing>();
  private mineFilter: MineFilter = "active";
  /** The listing whose take-down waits for "Take it down" or "Keep it listed" (no timeout). */
  private confirmTakeDown: number | null = null;

  private readonly keys = new RetryKeys();
  private busy = false;
  private disposed = false;
  private opener: HTMLElement | null = null;

  constructor(private readonly opts: ExchangeWindowOptions) {
    this.purse = h("span", { class: "exchange-purse" });
    this.counterNote = h("p", { class: "exchange-counter", role: "status" });
    this.tabs = tabBar<Tab>("Owl Exchange", [
      { id: "browse", label: "Browse" },
      { id: "sell", label: "Sell" },
      { id: "mine", label: "My listings" },
    ], (t) => this.showTab(t));
    this.error = h("p", { class: "econ-error", role: "alert", hidden: true });
    this.tree = h("nav", { class: "exchange-tree", "aria-label": "Categories" });
    this.results = h("div", { class: "exchange-results" });
    this.preview = new ItemPreview(opts.appearance);
    this.detail = h("aside", { class: "exchange-detail", "aria-label": "Listing", "aria-live": "polite" });
    this.filterCount = h("span", { class: "muted" });
    this.swatches = h("div", { class: "swatch-filter", role: "group", "aria-label": "Colour" });
    this.browsePanel = h("div", { class: "exchange-browse", role: "tabpanel" }, this.tree, h("div", { class: "exchange-main" }, this.filterBar(), this.results), this.detail);
    this.sellPanel = h("div", { class: "exchange-sell", role: "tabpanel", hidden: true });
    this.minePanel = h("div", { class: "exchange-mine", role: "tabpanel", hidden: true });
    this.root = h(
      "section",
      { class: "econ-window exchange-window panel", role: "dialog", "aria-label": "Owl Exchange", hidden: true },
      windowHead("Owl Exchange", () => this.close(), this.purse),
      this.counterNote,
      this.greeting,
      this.tabs.el,
      this.error,
      this.browsePanel,
      this.sellPanel,
      this.minePanel,
    );
    this.tabs.select("browse");
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** `StationOpened { owl_exchange }`: we stand at the counter. */
  openAtCounter(expiresInSecs: number): void {
    this.atCounter = true;
    this.grantUntil = performance.now() + expiresInSecs * 1000;
    this.show(this.tab === "mine" ? "browse" : this.tab);
  }

  /**
   * The keeper behind the counter greets us as the window opens (NPCs: the
   * shopkeepers in person). In the window itself: a subtitle would sit under
   * it. Gone when the window closes.
   */
  greet(name: string, text: string): void {
    this.greeting.replaceChildren(h("span", { class: "econ-greeting-name" }, name), " ", text);
    this.greeting.hidden = false;
  }

  /** Our own listings, from anywhere (/listings). */
  openMine(): void {
    this.show("mine");
  }

  private show(tab: Tab): void {
    const wasOpen = this.isOpen;
    this.root.hidden = false;
    if (!wasOpen) this.opener = focusOrigin();
    this.renderPurse();
    this.renderCounter();
    this.showTab(tab, !wasOpen);
    if (!wasOpen) queueMicrotask(() => this.root.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus());
  }

  close(): void {
    if (!this.isOpen) return;
    this.root.hidden = true;
    this.greeting.hidden = true;
    this.preview.clear();
    this.picker?.dispose();
    this.picker = null;
    this.selling = null;
    this.confirmBuy = false;
    this.confirmTakeDown = null;
    restoreFocus(this.opener, this.root);
    this.opener = null;
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      if (this.confirmBuy) {
        this.confirmBuy = false;
        this.renderDetail();
      } else if (this.confirmTakeDown !== null && this.tab === "mine") {
        this.cancelTakeDown(this.confirmTakeDown);
      } else this.close();
      return true;
    }
    return false;
  }

  /**
   * Once in a while (the HUD tick): the counter's grant lapses when we walk
   * off or its time runs out; the shopping tabs close with it.
   */
  update(nearCounter: boolean): void {
    if (!this.atCounter) return;
    const timeUp = performance.now() > this.grantUntil;
    if (nearCounter && !timeUp) return;
    this.leftCounter(timeUp ? "Your time at the counter is up. Use the counter again to keep trading." : "You stepped away from the Owl Exchange counter.");
  }

  /** The counter no longer serves us (walked away, zone change, or the server said so). */
  leftCounter(why: string | null): void {
    if (!this.atCounter) return;
    this.atCounter = false;
    if (!this.isOpen) return;
    if (why) this.opts.notify(why);
    if (this.tab === "mine") this.renderCounter();
    else this.close();
  }

  balanceChanged(): void {
    if (!this.isOpen) return;
    this.renderPurse();
    if (this.tab === "sell") this.renderSellForm();
    if (this.tab === "browse" && this.confirmBuy) this.renderDetail();
  }

  private renderPurse(): void {
    const b = this.opts.balance();
    this.purse.replaceChildren(b === null ? "" : crowns(b));
  }

  private renderCounter(): void {
    this.counterNote.textContent = this.atCounter ? "The clerk peers at you over a ledger. Listings last 5 days; purchases and proceeds arrive by owl." : "You are away from the counter: you can see and take down your listings here. Browse and sell at the counter in the Entrance Hall.";
    for (const b of this.tabs.el.querySelectorAll<HTMLButtonElement>('[role="tab"]')) {
      const isMine = b.textContent === "My listings";
      b.disabled = !this.atCounter && !isMine;
    }
  }

  /** `fresh`: the window just opened, so anything shown may be stale. */
  private showTab(tab: Tab, fresh = false): void {
    if (!this.atCounter && tab !== "mine") tab = "mine";
    const returning = fresh || this.tab !== tab;
    this.tab = tab;
    this.tabs.select(tab);
    this.browsePanel.hidden = tab !== "browse";
    this.sellPanel.hidden = tab !== "sell";
    this.minePanel.hidden = tab !== "mine";
    this.showError(null);
    if (tab !== "browse") this.preview.clear();
    if (tab === "browse") {
      this.renderTree();
      this.renderSwatches();
      if (!this.browse.loaded || this.searched !== filtersKey(this.filters)) this.search();
      // Back from selling or your listings: the market moved meanwhile (your
      // own new listings at least), so ask for the current page again.
      else if (returning) void this.moveBrowse("reload");
      else this.renderResults();
      this.renderDetail();
    } else if (tab === "sell") {
      this.picker ??= new ItemPicker({ label: "Your trunk", action: "Sell this", askQuantity: false, onPick: (item) => this.chooseToSell(item) });
      this.picker.reload();
      void this.countActive();
      this.renderSell();
    } else {
      void this.moveMine("first");
    }
  }

  // ---------------------------------------------------------------- browse: filters

  private filterBar(): HTMLElement {
    const search = h("input", {
      type: "search",
      class: "exchange-search",
      placeholder: "Search by name",
      "aria-label": "Search by name",
      autocomplete: "off",
      maxlength: 64,
      oninput: () => {
        this.filters = { ...this.filters, q: search.value };
        this.scheduleSearch();
      },
      onkeydown: (e: Event) => {
        if ((e as KeyboardEvent).key === "Enter") {
          e.preventDefault();
          this.search();
        }
      },
    });
    const levels = (label: string, pick: (n: number | null) => void) => {
      const sel = h("select", { "aria-label": label, onchange: () => pick(sel.value ? Number(sel.value) : null) }, h("option", { value: "" }, "Any"));
      for (let l = LEVEL_MIN; l <= LEVEL_MAX; l++) sel.append(h("option", { value: String(l) }, String(l)));
      return sel;
    };
    const minLevel = levels("Lowest required level", (n) => this.setFilter({ minLevel: n }));
    const maxLevel = levels("Highest required level", (n) => this.setFilter({ maxLevel: n }));
    const house = h("select", { "aria-label": "House theme", onchange: () => this.setFilter({ house: house.value || null }) }, h("option", { value: "" }, "Any house"), ...houseThemes().map((t) => h("option", { value: t.id }, t.name)));
    const sort = h("select", { "aria-label": "Sort", onchange: () => this.setFilter({ sort: sort.value as ExchangeSort }) }, ...(Object.keys(SORT_LABELS) as ExchangeSort[]).map((s) => h("option", { value: s, selected: s === this.filters.sort }, SORT_LABELS[s])));
    const clear = h(
      "button",
      {
        type: "button",
        class: "link",
        onclick: () => {
          search.value = "";
          minLevel.value = "";
          maxLevel.value = "";
          house.value = "";
          this.setFilter({ q: "", minLevel: null, maxLevel: null, colour: null, house: null });
        },
      },
      "Clear filters",
    );
    return h(
      "div",
      { class: "exchange-filters", role: "search" },
      h("div", { class: "filter-row" }, search, h("label", { class: "filter-sort" }, "Sort ", sort)),
      h("div", { class: "filter-row" }, h("span", { class: "filter-group" }, "Level ", minLevel, " to ", maxLevel), house, this.filterCount, clear),
      this.swatches,
    );
  }

  private renderSwatches(): void {
    const any = h("button", { type: "button", class: "swatch-any", "aria-pressed": String(this.filters.colour === null), onclick: () => this.setFilter({ colour: null }) }, "Any colour");
    const buttons = PALETTE.colours.map((c) =>
      h("button", {
        type: "button",
        class: "swatch-pick",
        style: `--swatch: ${c.hex}`,
        title: c.name,
        "aria-label": c.name,
        "aria-pressed": String(this.filters.colour === c.index),
        onclick: () => this.setFilter({ colour: this.filters.colour === c.index ? null : c.index }),
      }),
    );
    this.swatches.replaceChildren(any, ...buttons);
    const n = activeFilterCount(this.filters);
    this.filterCount.textContent = n ? `${n} filter${n === 1 ? "" : "s"}` : "";
  }

  private setFilter(change: Partial<BrowseFilters>): void {
    const focused = document.activeElement;
    this.filters = { ...this.filters, ...change };
    if ("colour" in change) {
      this.renderSwatches();
      // Keep the keyboard on the swatch just chosen.
      if (focused instanceof HTMLElement && focused.classList.contains("swatch-pick")) {
        this.swatches.querySelector<HTMLButtonElement>(`[aria-label="${focused.getAttribute("aria-label")}"]`)?.focus();
      }
    } else {
      this.renderSwatches();
    }
    this.search();
  }

  private scheduleSearch(): void {
    if (this.searchTimer !== null) clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => this.search(), SEARCH_DEBOUNCE_MS);
  }

  /** A new search from the first page (unless the filters ask for what is shown). */
  private search(): void {
    if (this.searchTimer !== null) clearTimeout(this.searchTimer);
    this.searchTimer = null;
    const key = filtersKey(this.filters);
    if (key === this.searched && this.browse.loaded) return;
    this.searched = key;
    void this.moveBrowse("first");
  }

  private async moveBrowse(move: "first" | "next" | "prev" | "reload"): Promise<void> {
    const req = this.browse.begin(move);
    if (!req) return;
    this.renderResults();
    try {
      const page = await econApi.listings(browseQuery(this.filters, req.after, EXCHANGE_PAGE));
      if (this.disposed || !this.browse.receive(req.ticket, page)) return;
      this.showError(null);
      if (this.selected && !page.items.some((l) => l.id === this.selected!.id)) this.select(null);
    } catch (e) {
      if (this.disposed || !this.browse.fail(req.ticket)) return;
      // A first page that failed must not count as searched (try again on the next change).
      if (move === "first") this.searched = "";
      this.onFailure(e);
    }
    this.renderResults();
  }

  // ---------------------------------------------------------------- browse: tree and results

  private renderTree(): void {
    const f = this.filters;
    const button = (label: string, pressed: boolean, onclick: () => void, extra: Record<string, string> = {}) => h("button", { type: "button", class: "tree-item", "aria-pressed": String(pressed), onclick, ...extra }, label);
    const ul = h("ul", { class: "tree" });
    ul.append(h("li", {}, button("Everything", f.category === null, () => this.pickCategory(null, null))));
    for (const c of exchangeCategories()) {
      const open = this.expanded.has(c.id) || f.category === c.id;
      const li = h(
        "li",
        {},
        h(
          "div",
          { class: "tree-row" },
          h("button", { type: "button", class: "tree-toggle", "aria-expanded": String(open), "aria-label": `${open ? "Collapse" : "Expand"} ${c.label}`, onclick: () => this.toggleCategory(c.id) }, open ? "▾" : "▸"),
          button(c.label, f.category === c.id && f.subcategory === null, () => this.pickCategory(c.id, null)),
        ),
      );
      if (open) li.append(h("ul", { class: "tree-subs" }, ...c.subs.map((s) => h("li", {}, button(s.label, f.category === c.id && f.subcategory === s.id, () => this.pickCategory(c.id, s.id))))));
      ul.append(li);
    }
    this.tree.replaceChildren(ul);
  }

  private toggleCategory(id: string): void {
    if (this.expanded.has(id)) this.expanded.delete(id);
    else this.expanded.add(id);
    this.renderTree();
    this.tree.querySelector<HTMLButtonElement>(`.tree-toggle[aria-label$="${exchangeCategories().find((c) => c.id === id)?.label}"]`)?.focus();
  }

  private pickCategory(category: string | null, subcategory: string | null): void {
    if (category) this.expanded.add(category);
    this.filters = { ...this.filters, category, subcategory };
    this.renderTree();
    this.tree.querySelector<HTMLButtonElement>('.tree-item[aria-pressed="true"]')?.focus();
    this.search();
  }

  private renderResults(): void {
    const p = this.browse;
    const head = h("p", { class: "results-head" }, this.filters.category ? categoryPath(this.filters.category, this.filters.subcategory) : "Everything");
    if (!p.loaded) {
      this.results.replaceChildren(head, h("p", { class: "muted-line" }, p.loading ? "The clerk thumbs through the ledger…" : ""));
      return;
    }
    const ul = h("ul", { class: "listing-list", "aria-label": "Listings" });
    for (const l of p.items) ul.append(this.listingRow(l));
    if (!p.items.length) ul.append(h("li", { class: "muted-line" }, activeFilterCount(this.filters) ? "Nothing matches. Try fewer filters." : "Nothing listed here yet."));
    this.results.replaceChildren(head, ul, pagerNav(p, (m) => void this.moveBrowse(m), "Listings"));
  }

  private listingRow(l: Listing): HTMLElement {
    const name = defName(l.item_def);
    const level = l.required_level > 1 ? `Level ${l.required_level}` : "";
    const selected = this.selected?.id === l.id;
    return h(
      "li",
      {},
      h(
        "button",
        {
          type: "button",
          class: "listing-row",
          "aria-pressed": String(selected),
          "aria-label": `${name}${l.quantity > 1 ? `, ${l.quantity} units` : ""}${l.maker ? `, ${makersMark(l.maker)}` : ""}, ${formatCrowns(l.price)} Crowns, ${timeLeft(l.expires_at)} left`,
          onclick: () => this.select(selected ? null : l),
        },
        itemIcon(l.item_def, l.dyes, "small", l.finish),
        h("span", { class: "listing-name" }, name, l.quantity > 1 ? h("span", { class: "item-qty" }, ` ×${l.quantity}`) : null, wearTag(l.item_def), l.maker ? h("span", { class: "listing-maker" }, makersMark(l.maker)) : null),
        h("span", { class: "listing-level" }, level),
        h("span", { class: "listing-time" }, timeLeft(l.expires_at)),
        crowns(l.price, "crowns price"),
      ),
    );
  }

  private select(l: Listing | null): void {
    this.selected = l;
    this.confirmBuy = false;
    this.renderResults();
    this.renderDetail();
    if (l) void this.preview.show({ itemDef: l.item_def, dyes: l.dyes, finish: l.finish });
    else this.preview.clear();
  }

  // ---------------------------------------------------------------- browse: detail and buying

  private renderDetail(): void {
    const l = this.selected;
    if (!l) {
      this.detail.replaceChildren(h("p", { class: "muted-line" }, "Choose a listing to see it up close."));
      return;
    }
    const def = econDef(l.item_def);
    const balance = this.opts.balance() ?? 0;
    const facts = h("dl", { class: "listing-facts" });
    const fact = (k: string, v: Node | string) => facts.append(h("dt", {}, k), h("dd", {}, v));
    fact("Kind", def ? (def.kind === "clothing" && def.slot ? `Clothing, ${SLOT_NAMES[def.slot].toLowerCase()}` : KIND_LABELS[def.kind]) : "");
    fact("Category", categoryPath(l.category, l.subcategory));
    if (l.quantity > 1) fact("Quantity", String(l.quantity));
    fact("Level", String(l.required_level || requiredLevel(def)));
    fact("Colours", h("span", { class: "swatches" }, ...l.dyes.map((d) => h("span", { class: "swatch", style: `--swatch: ${paletteHex(d)}`, title: PALETTE.colours[d]?.name ?? "" }))));
    if (l.finish !== "none") fact("Finish", FINISH_LABELS[l.finish]);
    if (l.house_theme) fact("House", houseThemes().find((t) => t.id === l.house_theme)?.name ?? l.house_theme);
    fact("Seller", l.seller_name);
    fact("Time left", timeLeft(l.expires_at));
    const each = eachText(l.price, l.quantity);
    const price = h("p", { class: "listing-price" }, crowns(l.price), each ? h("span", { class: "muted" }, ` (${each})`) : null);
    let buy: HTMLElement;
    if (this.confirmBuy) {
      const after = balance - l.price;
      buy = h(
        "div",
        { class: "buy-confirm", role: "group", "aria-label": "Confirm purchase" },
        h("p", {}, `Buy ${defName(l.item_def)}${l.quantity > 1 ? ` ×${l.quantity}` : ""} for `, h("strong", {}, `${formatCrowns(l.price)} Crowns`), "?"),
        h("p", { class: "muted" }, after >= 0 ? `You'll have ${formatCrowns(after)} left. It arrives by owl mail.` : "You don't have enough Crowns."),
        h(
          "div",
          { class: "econ-actions" },
          h("button", { type: "button", class: "primary", disabled: this.busy || after < 0, onclick: () => void this.buy(l) }, this.busy ? "Paying…" : "Confirm purchase"),
          h("button", { type: "button", class: "secondary", onclick: () => ((this.confirmBuy = false), this.renderDetail()) }, "Not now"),
        ),
      );
    } else if (l.seller_name === this.opts.myName) {
      buy = h("div", { class: "econ-actions" }, h("p", { class: "muted" }, "Your own listing."), h("button", { type: "button", class: "secondary", onclick: () => this.showTab("mine") }, "My listings"));
    } else {
      buy = h("div", { class: "econ-actions" }, h("button", { type: "button", class: "primary", disabled: balance < l.price || this.busy, onclick: () => this.askBuy() }, balance < l.price ? "Not enough Crowns" : "Buy"));
    }
    mount(
      this.detail,
      this.preview.root,
      h("h3", { class: "listing-title" }, defName(l.item_def), wearTag(l.item_def)),
      l.maker ? h("p", { class: "makers-mark" }, makersMark(l.maker)) : null,
      price,
      facts,
      buy,
    );
  }

  private askBuy(): void {
    this.confirmBuy = true;
    this.renderDetail();
    this.detail.querySelector<HTMLButtonElement>(".buy-confirm button.primary")?.focus();
  }

  private async buy(l: Listing): Promise<void> {
    const action = `buy:${l.id}`;
    await this.act(
      async () => {
        const r = await econApi.buyListing(l.id, this.keys.get(action));
        this.keys.settle(action);
        this.opts.onBalance(r.crowns);
        this.opts.notify(`You bought ${defName(l.item_def)}${l.quantity > 1 ? ` ×${l.quantity}` : ""} for ${formatCrowns(l.price)} Crowns. An owl is bringing it to your mailbox.`);
        this.browse.remove((x) => x.id === l.id);
        this.select(null);
      },
      (e) => {
        if (!(e instanceof ApiError && isRetryable(e.status))) this.keys.settle(action);
        if (e instanceof ApiError && (e.code === "listing_closed" || e.code === "listing_not_found")) {
          this.browse.remove((x) => x.id === l.id);
          this.select(null);
        }
      },
    );
  }

  // ---------------------------------------------------------------- selling

  private async countActive(): Promise<void> {
    try {
      // At most `active_listings_max` (20) are active: one page holds them all.
      const page = await econApi.myListings("active", null, Math.max(ECONOMY.exchange.active_listings_max, EXCHANGE_PAGE));
      if (this.disposed) return;
      this.activeCount = page.next === null ? page.items.length : ECONOMY.exchange.active_listings_max;
      if (this.tab === "sell") this.renderSellForm();
    } catch {
      this.activeCount = null;
    }
  }

  private chooseToSell(item: EconItem): void {
    this.selling = item;
    this.sellQty = item.quantity;
    this.sellPrice = null;
    this.renderSell();
    this.sellPanel.querySelector<HTMLInputElement>('input[name="price"]')?.focus();
  }

  private renderSell(): void {
    const left = h("div", { class: "sell-pick" }, h("h3", {}, "From your trunk"), this.picker?.root ?? null);
    const right = h("div", { class: "sell-form", "aria-live": "polite" });
    this.sellPanel.replaceChildren(left, right);
    this.renderSellForm();
  }

  private renderSellForm(): void {
    const slot = this.sellPanel.querySelector<HTMLElement>(".sell-form");
    if (!slot) return;
    const item = this.selling;
    const ex = ECONOMY.exchange;
    const limits = h("p", { class: "muted" }, `Listings stay up ${Math.round(ex.listing_secs / 86_400)} days. Fee ${ex.listing_fee} C (kept if it doesn't sell), sales tax ${ex.sales_tax_pct}%.`, this.activeCount !== null ? ` You have ${this.activeCount} of ${ex.active_listings_max} listings up.` : "");
    if (!item) {
      slot.replaceChildren(h("h3", {}, "New listing"), h("p", { class: "muted-line" }, "Choose something from your trunk to sell."), limits);
      return;
    }
    // Inputs are kept while typing: only the quote below them is redrawn.
    let form = slot.querySelector<HTMLElement>(`form[data-item="${item.id}"]`);
    if (!form) {
      const max = Math.min(item.quantity, stackLimit(econDef(item.item_def)));
      const qty = max > 1 ? wholeInput({ label: `Quantity (1 to ${max})`, name: "quantity", value: this.sellQty, oninput: (n) => ((this.sellQty = n ?? 0), this.renderQuote()) }) : null;
      const price = wholeInput({ label: "Price in Crowns", name: "price", value: this.sellPrice ?? undefined, placeholder: "Your price", oninput: (n) => ((this.sellPrice = n), this.renderQuote()) });
      form = h(
        "form",
        { class: "sell-fields", "data-item": String(item.id), onsubmit: (e: Event) => (e.preventDefault(), void this.list()) },
        h("div", { class: "sell-item" }, itemRow(item)),
        qty ? h("label", { class: "econ-field inline" }, h("span", {}, "Quantity"), qty, h("span", { class: "muted" }, ` of ${max}`)) : null,
        h("label", { class: "econ-field inline" }, h("span", {}, "Price for the lot"), price, h("span", { class: "muted" }, " Crowns")),
        h("div", { class: "sell-quote" }),
        h("div", { class: "econ-actions" }, h("button", { type: "submit", class: "primary" }, "List it"), h("button", { type: "button", class: "secondary", onclick: () => ((this.selling = null), this.renderSell()) }, "Choose another")),
      );
      slot.replaceChildren(h("h3", {}, "New listing"), form, limits);
    } else {
      slot.lastElementChild?.replaceWith(limits);
    }
    this.renderQuote();
  }

  /** Fee now, tax and proceeds at sale, for the price typed. */
  private renderQuote(): void {
    const quote = this.sellPanel.querySelector<HTMLElement>(".sell-quote");
    const item = this.selling;
    if (!quote || !item) return;
    const balance = this.opts.balance() ?? 0;
    const form = { quantity: this.sellQty, price: this.sellPrice ?? 0 };
    const q = this.sellPrice === null ? null : sellQuote(form);
    const problem = this.sellPrice === null ? null : sellProblem(item, form, balance, this.activeCount);
    const rows = h("dl", { class: "quote" });
    const row = (k: string, v: string, cls = "") => rows.append(h("dt", { class: cls }, k), h("dd", { class: cls }, v));
    row("Listing fee, paid now", `${formatCrowns(ECONOMY.exchange.listing_fee)} C`);
    if (q) {
      row(`Sales tax (${ECONOMY.exchange.sales_tax_pct}%), when it sells`, q.tax > 0 ? `−${formatCrowns(q.tax)} C` : "0 C");
      row("You receive by owl when it sells", `${formatCrowns(q.proceeds)} C`, "quote-total");
      const each = eachText(q.price, form.quantity);
      if (each) row("Buyers pay", each);
    } else {
      row("You receive when it sells", "Set a price");
    }
    const submit = this.sellPanel.querySelector<HTMLButtonElement>('form button[type="submit"]');
    if (submit) submit.disabled = this.busy || !q || !!problem;
    mount(quote, rows, problem ? h("p", { class: "econ-hint" }, problem) : null, q && q.net < 0 ? h("p", { class: "econ-hint" }, "At this price the fee costs more than the sale brings in.") : null);
  }

  private async list(): Promise<void> {
    const item = this.selling;
    if (!item || this.sellPrice === null) return;
    const form = { quantity: this.sellQty, price: this.sellPrice };
    const problem = sellProblem(item, form, this.opts.balance() ?? 0, this.activeCount);
    if (problem) {
      this.showError(problem);
      return;
    }
    const action = `list:${item.id}:${form.quantity}:${form.price}`;
    await this.act(
      async () => {
        const r = await econApi.createListing(item.id, form.quantity, form.price, this.keys.get(action));
        this.keys.settle(action);
        this.opts.onBalance(r.crowns);
        this.opts.notify(`Listed ${defName(item.item_def)}${form.quantity > 1 ? ` ×${form.quantity}` : ""} for ${formatCrowns(form.price)} Crowns (fee ${ECONOMY.exchange.listing_fee} C paid).`);
        this.selling = null;
        if (this.activeCount !== null) this.activeCount++;
        this.picker?.reload();
        this.renderSell();
      },
      (e) => {
        if (!(e instanceof ApiError && isRetryable(e.status))) this.keys.settle(action);
      },
    );
  }

  // ---------------------------------------------------------------- my listings

  private async moveMine(move: "first" | "next" | "prev" | "reload"): Promise<void> {
    const req = this.mine.begin(move);
    if (!req) return;
    this.renderMine();
    try {
      const page = await econApi.myListings(this.mineFilter === "all" ? null : this.mineFilter, req.after, EXCHANGE_PAGE);
      if (this.disposed || !this.mine.receive(req.ticket, page)) return;
      this.showError(null);
    } catch (e) {
      if (this.disposed || !this.mine.fail(req.ticket)) return;
      this.onFailure(e);
    }
    this.renderMine();
  }

  private renderMine(): void {
    const p = this.mine;
    const chips = h(
      "div",
      { class: "chips", role: "group", "aria-label": "Show" },
      ...MINE_FILTERS.map((f) => h("button", { type: "button", class: "chip", "aria-pressed": String(this.mineFilter === f.id), onclick: () => ((this.mineFilter = f.id), void this.moveMine("first")) }, f.label)),
    );
    const ul = h("ul", { class: "mine-list", "aria-label": "Your listings" });
    if (!p.loaded) ul.append(h("li", { class: "muted-line" }, p.loading ? "Finding your listings…" : ""));
    else if (!p.items.length) ul.append(h("li", { class: "muted-line" }, this.mineFilter === "active" ? "Nothing on sale. List something at the counter." : "None."));
    for (const l of p.items) ul.append(this.mineRow(l));
    this.minePanel.replaceChildren(chips, ul, p.loaded && (p.hasPrev || p.hasNext) ? pagerNav(p, (m) => void this.moveMine(m), "Your listings") : h("span", {}));
  }

  private mineRow(l: Listing): HTMLElement {
    const active = l.state === "active";
    const confirming = active && this.confirmTakeDown === l.id;
    const status = active ? `${LISTING_STATE_LABELS.active} · ${timeLeft(l.expires_at)} left` : LISTING_STATE_LABELS[l.state];
    const proceeds = l.state === "sold" ? h("span", { class: "muted" }, " (proceeds sent by owl)") : null;
    let action: HTMLElement = h("span", {});
    if (confirming) {
      // An explicit question that stays until answered (or Escape).
      action = h(
        "div",
        { class: "takedown-confirm", role: "group", "aria-label": `Take ${defName(l.item_def)} down?` },
        h("span", { class: "takedown-question" }, "Take it down? The fee is kept; the item comes back by owl."),
        h("button", { type: "button", class: "danger", disabled: this.busy, "data-listing": String(l.id), onclick: () => void this.takeDown(l) }, this.busy ? "Taking down…" : "Take it down"),
        h("button", { type: "button", class: "secondary", onclick: () => this.cancelTakeDown(l.id) }, "Keep it listed"),
      );
    } else if (active) {
      action = h("button", { type: "button", class: "secondary", disabled: this.busy, "data-takedown": String(l.id), onclick: () => this.askTakeDown(l.id) }, "Take down");
    }
    return h(
      "li",
      { class: `mine-row ${l.state}${confirming ? " confirming" : ""}` },
      itemRow({ item_def: l.item_def, dyes: l.dyes, quantity: l.quantity, maker: l.maker, finish: l.finish }),
      crowns(l.price, "crowns price"),
      h("span", { class: "mine-state" }, status, proceeds),
      action,
    );
  }

  /** "Take down" asks first; the question replaces the button until answered. */
  private askTakeDown(id: number): void {
    this.confirmTakeDown = id;
    this.renderMine();
    this.minePanel.querySelector<HTMLButtonElement>(`.takedown-confirm button[data-listing="${id}"]`)?.focus();
  }

  /** "Keep it listed" (or Escape): back to the plain button, focused. */
  private cancelTakeDown(id: number): void {
    if (this.confirmTakeDown !== id) return;
    this.confirmTakeDown = null;
    this.renderMine();
    this.minePanel.querySelector<HTMLButtonElement>(`button[data-takedown="${id}"]`)?.focus();
  }

  private async takeDown(l: Listing): Promise<void> {
    if (this.confirmTakeDown !== l.id) return;
    this.confirmTakeDown = null;
    await this.act(async () => {
      await econApi.takeDown(l.id);
      this.opts.notify(`${defName(l.item_def)} taken down. An owl is bringing it back to your mailbox.`);
      if (this.activeCount !== null) this.activeCount = Math.max(0, this.activeCount - 1);
      void this.moveMine("reload");
    });
  }

  // ---------------------------------------------------------------- helpers

  private async act(fn: () => Promise<void>, onError?: (e: unknown) => void): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.showError(null);
    this.root.setAttribute("aria-busy", "true");
    this.rerender();
    try {
      await fn();
    } catch (e) {
      onError?.(e);
      if (!this.disposed) this.onFailure(e);
    } finally {
      this.busy = false;
      this.root.removeAttribute("aria-busy");
      if (!this.disposed) this.rerender();
    }
  }

  private rerender(): void {
    if (this.tab === "browse") this.renderDetail();
    else if (this.tab === "sell") this.renderQuote();
    else this.renderMine();
  }

  /** Shows a refusal; `not_at_station` means the counter no longer serves us. */
  private onFailure(e: unknown): void {
    if (e instanceof ApiError && e.code === "not_at_station") {
      this.leftCounter(exchangeError(e.code, e.message));
      return;
    }
    this.showError(errorMessage(e, exchangeError));
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  dispose(): void {
    this.disposed = true;
    if (this.searchTimer !== null) clearTimeout(this.searchTimer);
    this.picker?.dispose();
    this.preview.dispose();
  }
}
