// The owl-order catalogue: plain basics (a simple wardrobe, materials, dyes,
// furniture) for Crowns, delivered at once by owl mail (readable
// anywhere). It is a place: the Owl Order Catalogue's stand in each house
// common room. Using the stand (E, or a click) gets `StationOpened
// { owl_order }` and opens this window; walking away (or the grant running
// out) closes it, as the Owl Exchange counter does. The server refuses
// browsing and ordering anywhere else ("Visit the Owl Order Catalogue in
// your common room."). Browse by category a page at a time, put things in a
// basket, order. Crafted fashion is never sold here (the data guarantees
// it), so the catalogue is a sink, not a rival.
//
// The basket and its rules are in economy/shop-state.ts; an order carries a
// request id, so a retry after a lost answer cannot charge twice.

import "../economy/economy.css";
import "./shop.css";
import { ApiError } from "../api";
import { CATALOG } from "../content/catalog";
import * as econApi from "../economy/api";
import type { ShopItem } from "../economy/api";
import { itemIcon } from "../economy/icons";
import { defName, econDef, exchangeCategories, KIND_LABELS, SLOT_NAMES, type EconItemDef } from "../economy/items";
import { formatCrowns, mulChecked, parseWhole } from "../economy/money";
import { isRetryable, KeysetPager, RetryKeys } from "../economy/paging";
import { Basket, shopCategories, shopError, unitsMax } from "../economy/shop-state";
import { errorMessage, crowns, pagerNav, tabBar, wholeInput, windowHead } from "../economy/view";
import { focusOrigin, h, restoreFocus } from "./dom";
import { wearTag } from "./wear";

export interface ShopWindowOptions {
  balance: () => number | null;
  onBalance: (crowns: number) => void;
  notify: (text: string) => void;
}

const PAGE_SIZE = 25;

/** The stand's display name (the layout's interactable name). */
export const STAND_NAME = "Owl Order Catalogue";

export class ShopWindow {
  readonly root: HTMLElement;
  /** The stand's keeper's greeting (NPCs), while the catalogue is open at the stand. */
  private readonly greeting: HTMLElement = h("p", { class: "econ-greeting", role: "status", hidden: true });
  private readonly tabs: { el: HTMLElement; select: (id: string) => void };
  private readonly subs: HTMLElement;
  private readonly list: HTMLElement;
  private readonly basketPane: HTMLElement;
  private readonly error: HTMLElement;
  private readonly categories = shopCategories(exchangeCategories(), CATALOG.items as EconItemDef[]);
  private category: string;
  private subcategory: string | null = null;
  private readonly pager = new KeysetPager<ShopItem>();
  private readonly basket = new Basket();
  private readonly keys = new RetryKeys();
  private busy = false;
  private disposed = false;
  private opener: HTMLElement | null = null;
  /** Whether the server's grant at a stand holds (as far as we know). */
  private atStand = false;
  private grantUntil = 0;

  constructor(private readonly opts: ShopWindowOptions) {
    this.category = this.categories[0]?.id ?? "hats";
    this.tabs = tabBar("Catalogue sections", this.categories, (id) => this.pick(id, null));
    this.subs = h("div", { class: "chips", role: "group", "aria-label": "Section" });
    this.list = h("div", { class: "shop-list", role: "tabpanel" });
    this.basketPane = h("aside", { class: "shop-basket", "aria-label": "Basket" });
    this.error = h("p", { class: "econ-error", role: "alert", hidden: true });
    this.root = h(
      "section",
      { class: "econ-window shop-window panel", role: "dialog", "aria-label": STAND_NAME, hidden: true },
      windowHead(STAND_NAME, () => this.close()),
      this.greeting,
      h("p", { class: "econ-intro" }, "Plain, honest basics, delivered by owl to your mailbox. Fine things are made by crafters: look for them on the Owl Exchange."),
      this.tabs.el,
      this.subs,
      this.error,
      h("div", { class: "shop-body" }, this.list, this.basketPane),
    );
    this.tabs.select(this.category);
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** `StationOpened { owl_order }`: we stand at our common room's stand. */
  openAtStand(expiresInSecs: number): void {
    this.atStand = true;
    this.grantUntil = performance.now() + expiresInSecs * 1000;
    this.toggle(true);
  }

  /** The HUD tick: closes the catalogue once we walk away or the grant runs out. */
  update(nearStand: boolean): void {
    if (!this.atStand) return;
    const timeUp = performance.now() > this.grantUntil;
    if (nearStand && !timeUp) return;
    this.leftStand(timeUp ? "Your time at the catalogue is up. Use the stand again to keep browsing." : `You stepped away from the ${STAND_NAME}.`);
  }

  /** The stand no longer serves us (walked away, zone change, reconnect, or the server said so). */
  leftStand(why: string | null): void {
    if (!this.atStand) return;
    this.atStand = false;
    if (!this.isOpen) return;
    if (why) this.opts.notify(why);
    this.close();
  }

  private toggle(open: boolean): void {
    if (open === this.isOpen) return;
    this.root.hidden = !open;
    if (open) {
      this.opener = focusOrigin();
      this.renderSubs();
      this.renderBasket();
      void this.move("first");
      queueMicrotask(() => this.root.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus());
    } else {
      this.greeting.hidden = true;
      restoreFocus(this.opener, this.root);
      this.opener = null;
    }
  }

  close(): void {
    this.toggle(false);
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

  /** Escape closes the catalogue (it has no key of its own: it opens at the stand). */
  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.key === "Escape" && this.isOpen) {
      this.close();
      return true;
    }
    return false;
  }

  /** The purse changed: the basket's checks follow. */
  balanceChanged(): void {
    if (this.isOpen) this.renderBasket();
  }

  private pick(category: string, subcategory: string | null): void {
    if (category === this.category && subcategory === this.subcategory) return;
    this.category = category;
    this.subcategory = subcategory;
    this.tabs.select(category);
    this.renderSubs();
    void this.move("first");
  }

  private renderSubs(): void {
    const cat = exchangeCategories().find((c) => c.id === this.category);
    // Only the sections that sell something.
    const sold = new Set((CATALOG.items as EconItemDef[]).filter((i) => typeof i.price === "number" && i.exchange.category === this.category).map((i) => i.exchange.subcategory));
    const subs = cat?.subs.filter((s) => sold.has(s.id)) ?? [];
    const chip = (id: string | null, label: string) => h("button", { type: "button", class: "chip", "aria-pressed": String(this.subcategory === id), onclick: () => this.pick(this.category, id) }, label);
    this.subs.replaceChildren(...(subs.length > 1 ? [chip(null, "All"), ...subs.map((s) => chip(s.id, s.label))] : []));
  }

  private async move(move: "first" | "next" | "prev"): Promise<void> {
    const req = this.pager.begin(move);
    if (!req) return;
    this.renderList();
    try {
      const page = await econApi.shop({ category: this.category, subcategory: this.subcategory ?? undefined, limit: PAGE_SIZE, after: req.after });
      if (this.disposed || !this.pager.receive(req.ticket, page)) return;
      this.showError(null);
    } catch (e) {
      if (this.disposed || !this.pager.fail(req.ticket)) return;
      this.failed(e);
    }
    this.renderList();
  }

  private renderList(): void {
    const p = this.pager;
    if (!p.loaded) {
      this.list.replaceChildren(h("p", { class: "muted-line" }, p.loading ? "Turning the pages…" : ""));
      return;
    }
    const ul = h("ul", { class: "shop-items", "aria-label": "For sale" });
    for (const item of p.items) ul.append(this.row(item));
    if (!p.items.length) ul.append(h("li", { class: "muted-line" }, "Nothing in this section."));
    this.list.replaceChildren(ul, p.hasPrev || p.hasNext ? pagerNav(p, (m) => void this.move(m), "Catalogue") : h("span", {}));
  }

  private row(item: ShopItem): HTMLElement {
    const def = econDef(item.item_def);
    const name = defName(item.item_def);
    const max = unitsMax(item.item_def);
    const qty = wholeInput({ label: `How many ${name} (1 to ${max})`, value: 1, onenter: () => add() });
    const add = () => {
      const n = parseWhole(qty.value);
      if (n === null || n < 1 || n > max) {
        this.showError(`Choose between 1 and ${max}.`);
        qty.focus();
        return;
      }
      const problem = this.basket.add(item, n);
      this.showError(problem);
      if (!problem) {
        this.renderBasket();
      }
    };
    return h(
      "li",
      { class: "shop-item" },
      itemIcon(item.item_def),
      h("span", { class: "shop-item-text" }, h("span", { class: "shop-item-name" }, name, wearTag(item.item_def)), h("span", { class: "shop-item-kind" }, kindText(def))),
      crowns(item.price, "crowns price"),
      h("span", { class: "shop-item-buy" }, qty, h("button", { type: "button", class: "secondary", "aria-label": `Add ${name} to the basket`, onclick: add }, "Add")),
    );
  }

  private renderBasket(): void {
    const b = this.basket;
    const balance = this.opts.balance() ?? 0;
    const total = b.total();
    const problem = b.empty ? null : b.problem(balance);
    const lines = h("ul", { class: "basket-lines" });
    for (const l of b.lines) {
      const name = defName(l.itemDef);
      const qty = wholeInput({
        label: `Quantity of ${name}`,
        value: l.quantity,
        oninput: (n) => {
          if (n === null || n < 1) return;
          b.set(l.itemDef, n);
          this.renderTotals();
        },
      });
      qty.addEventListener("change", () => this.renderBasket());
      const lineTotal = mulChecked(l.price, l.quantity);
      lines.append(
        h(
          "li",
          { class: "basket-line" },
          itemIcon(l.itemDef),
          h("span", { class: "basket-name" }, name, wearTag(l.itemDef)),
          qty,
          h("span", { class: "basket-line-total", "data-item": String(l.itemDef) }, lineTotal === null ? "" : `${formatCrowns(lineTotal)} C`),
          h("button", { type: "button", class: "link", "aria-label": `Remove ${name}`, onclick: () => (b.remove(l.itemDef), this.renderBasket()) }, "×"),
        ),
      );
    }
    this.basketPane.replaceChildren(
      h("h3", {}, "Basket"),
      b.empty ? h("p", { class: "muted-line" }, "Empty. Add something from the catalogue.") : lines,
      h(
        "div",
        { class: "basket-totals", "aria-live": "polite" },
        h("p", {}, "Total ", h("strong", { class: "basket-total" }, total === null ? "–" : `${formatCrowns(total)} C`)),
        h("p", { class: "muted" }, `You have ${formatCrowns(balance)} C`),
        h("p", { class: "econ-hint basket-problem", hidden: !problem }, problem ?? ""),
      ),
      h("button", { type: "button", class: "primary", disabled: this.busy || b.empty || !!problem, onclick: () => void this.order() }, this.busy ? "Sending the owl…" : "Order by owl"),
    );
  }

  /** Totals only, so a quantity box keeps focus while typing. */
  private renderTotals(): void {
    const b = this.basket;
    const total = b.total();
    const balance = this.opts.balance() ?? 0;
    const problem = b.problem(balance);
    for (const l of b.lines) {
      const cell = this.basketPane.querySelector<HTMLElement>(`.basket-line-total[data-item="${l.itemDef}"]`);
      const t = mulChecked(l.price, l.quantity);
      if (cell && t !== null) cell.textContent = `${formatCrowns(t)} C`;
    }
    const totalEl = this.basketPane.querySelector<HTMLElement>(".basket-total");
    if (totalEl) totalEl.textContent = total === null ? "–" : `${formatCrowns(total)} C`;
    const p = this.basketPane.querySelector<HTMLElement>(".basket-problem");
    if (p) {
      p.textContent = problem ?? "";
      p.hidden = !problem;
    }
    const order = this.basketPane.querySelector<HTMLButtonElement>("button.primary");
    if (order) order.disabled = this.busy || b.empty || !!problem;
  }

  private async order(): Promise<void> {
    const b = this.basket;
    const problem = b.problem(this.opts.balance() ?? 0);
    if (problem || this.busy) {
      this.showError(problem);
      return;
    }
    this.busy = true;
    this.showError(null);
    this.renderBasket();
    // The same basket keeps its request id until the order is placed or refused.
    const action = `order:${b.key()}`;
    try {
      const r = await econApi.placeOrder(b.orderLines(), this.keys.get(action));
      this.keys.settle(action);
      this.opts.onBalance(r.crowns);
      this.opts.notify(`Order placed for ${formatCrowns(r.order.total)} Crowns. An owl is bringing it to your mailbox.`);
      b.clear();
    } catch (e) {
      if (!(e instanceof ApiError && isRetryable(e.status))) this.keys.settle(action);
      this.failed(e);
    } finally {
      this.busy = false;
      if (!this.disposed) this.renderBasket();
    }
  }

  /** A refused request: away from the stand (the server says so) closes the catalogue. */
  private failed(e: unknown): void {
    const text = errorMessage(e, shopError);
    if (e instanceof ApiError && e.code === "not_at_station") {
      this.atStand = true; // so leftStand tells the player why
      this.leftStand(text);
      return;
    }
    this.showError(text);
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  dispose(): void {
    this.disposed = true;
  }
}

function kindText(def: EconItemDef | undefined): string {
  if (!def) return "";
  return def.kind === "clothing" && def.slot ? `Clothing · ${SLOT_NAMES[def.slot]}` : KIND_LABELS[def.kind];
}
