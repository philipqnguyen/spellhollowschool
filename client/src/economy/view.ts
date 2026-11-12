// DOM pieces the economy windows share: Crown amounts, Previous / Next
// page controls for keyset pages, a tab bar with arrow-key navigation, the
// item row, and the trunk picker (choose an item from the inventory, a page
// at a time, and how many) used by letters, trades and Exchange listings.

import { ApiError } from "../api";
import { audio } from "../audio";
import { h } from "../ui/dom";
import { wearTag } from "../ui/wear";
import * as econApi from "./api";
import { coinIcon, itemIcon } from "./icons";
import { defName, econDef, itemLine, KIND_LABELS, PICKER_KINDS, stackLimit, type EconItem, type EconKind } from "./items";
import { formatCrowns, crownsText, parseWhole } from "./money";
import { KeysetPager } from "./paging";

/** "🪙 1,234" with the full amount for screen readers. */
export function crowns(n: number, className = "crowns"): HTMLElement {
  return h("span", { class: className, "aria-label": crownsText(n), title: crownsText(n) }, coinIcon(), h("span", { class: "crowns-amount", "aria-hidden": "true" }, formatCrowns(n)));
}

/** Previous / "Page 2" / Next under a paged list. */
export function pagerNav(pager: KeysetPager<unknown>, onMove: (move: "next" | "prev") => void, what = "page"): HTMLElement {
  const turn = (move: "next" | "prev") => {
    audio.play("ui.page");
    onMove(move);
  };
  return h(
    "nav",
    { class: "pager", "aria-label": `${what} pages` },
    h("button", { type: "button", class: "secondary", disabled: !pager.hasPrev || pager.loading, onclick: () => turn("prev") }, "‹ Previous"),
    h("span", { class: "pager-label", "aria-live": "polite" }, pager.loading ? "Loading…" : pager.label()),
    h("button", { type: "button", class: "secondary", disabled: !pager.hasNext || pager.loading, onclick: () => turn("next") }, "Next ›"),
  );
}

/**
 * A row of tabs (`role="tablist"`): click or arrow keys to switch. Returns
 * the bar and a function that marks the selected tab.
 */
export function tabBar<T extends string>(label: string, tabs: readonly { id: T; label: string }[], onPick: (id: T) => void): { el: HTMLElement; select: (id: T) => void } {
  const buttons = new Map<T, HTMLButtonElement>();
  const el = h("div", { class: "econ-tabs", role: "tablist", "aria-label": label });
  const select = (id: T) => {
    for (const [tid, b] of buttons) {
      b.setAttribute("aria-selected", String(tid === id));
      b.tabIndex = tid === id ? 0 : -1;
    }
  };
  tabs.forEach((t, i) => {
    const b = h(
      "button",
      {
        type: "button",
        role: "tab",
        class: "econ-tab",
        onclick: () => {
          audio.play("ui.tab");
          onPick(t.id);
        },
        onkeydown: (e: Event) => {
          const k = (e as KeyboardEvent).key;
          const step = k === "ArrowRight" ? 1 : k === "ArrowLeft" ? -1 : 0;
          if (!step) return;
          e.preventDefault();
          const next = tabs[(i + step + tabs.length) % tabs.length]!;
          onPick(next.id);
          buttons.get(next.id)?.focus();
        },
      },
      t.label,
    );
    buttons.set(t.id, b);
    el.append(b);
  });
  return { el, select };
}

/** An item's icon, name and detail line, for lists and offers. */
export function itemRow(item: Pick<EconItem, "item_def" | "dyes" | "quantity" | "maker" | "finish">, extra?: Node | null): HTMLElement {
  return h(
    "span",
    { class: "item-row" },
    itemIcon(item.item_def, item.dyes, "small", item.finish ?? "none"),
    h("span", { class: "item-row-text" }, h("span", { class: "item-row-name" }, defName(item.item_def), item.quantity > 1 ? h("span", { class: "item-qty" }, ` ×${item.quantity}`) : null, wearTag(item.item_def)), h("span", { class: "item-row-line" }, itemLine(item))),
    extra ?? null,
  );
}

/** A whole-number input (`inputmode=numeric`) that reads back as a number or null. */
export function wholeInput(attrs: { label: string; value?: number; min?: number; max?: number; name?: string; placeholder?: string; oninput?: (n: number | null) => void; onenter?: () => void }): HTMLInputElement {
  const input = h("input", {
    type: "text",
    inputmode: "numeric",
    autocomplete: "off",
    class: "whole-input",
    name: attrs.name,
    placeholder: attrs.placeholder,
    "aria-label": attrs.label,
    value: attrs.value === undefined ? undefined : String(attrs.value),
    oninput: () => attrs.oninput?.(parseWhole(input.value)),
    onkeydown: (e: Event) => {
      if ((e as KeyboardEvent).key === "Enter") {
        e.preventDefault();
        attrs.onenter?.();
      }
    },
  });
  return input;
}

export interface PickerOptions {
  /** Heading read out for the list. */
  label: string;
  /** Kinds to offer as filters (all by default). */
  kinds?: readonly EconKind[];
  /** Units of each item already used elsewhere (offered, attached), by item id. */
  used?: () => ReadonlyMap<number, number>;
  /** The button on a chosen item ("Offer", "Attach", "List this"). */
  action: string;
  onPick: (item: EconItem, quantity: number) => void;
  /** Whether to ask how many (stacks); default true. */
  askQuantity?: boolean;
  pageSize?: number;
}

/**
 * The trunk: items in the inventory (never worn, placed, mailed or listed
 * ones, which cannot move), a page at a time, filtered by kind. Choosing one
 * asks how many for a stack, then calls `onPick`.
 */
export class ItemPicker {
  readonly root: HTMLElement;
  private readonly chips: HTMLElement;
  private readonly list: HTMLElement;
  private readonly nav: HTMLElement;
  private readonly error: HTMLElement;
  private kind: EconKind | null = null;
  private readonly pager = new KeysetPager<EconItem>();
  private chosen: EconItem | null = null;
  private disposed = false;

  constructor(private readonly opts: PickerOptions) {
    this.chips = h("div", { class: "chips", role: "group", "aria-label": "Kind" });
    this.list = h("ul", { class: "picker-list", "aria-label": opts.label });
    this.nav = h("div", {});
    this.error = h("p", { class: "econ-error", role: "alert", hidden: true });
    this.root = h("div", { class: "picker" }, this.chips, this.list, this.nav, this.error);
    this.renderChips();
  }

  /** Loads the first page (call when shown, and after the trunk changed). */
  reload(): void {
    this.chosen = null;
    void this.move("first");
  }

  dispose(): void {
    this.disposed = true;
  }

  private renderChips(): void {
    const kinds = this.opts.kinds ?? PICKER_KINDS;
    const chip = (kind: EconKind | null, label: string) =>
      h(
        "button",
        {
          type: "button",
          class: "chip",
          "aria-pressed": String(this.kind === kind),
          onclick: () => {
            this.kind = kind;
            this.renderChips();
            this.reload();
          },
        },
        label,
      );
    this.chips.replaceChildren(chip(null, "All"), ...kinds.map((k) => chip(k, KIND_LABELS[k])));
  }

  private async move(move: "first" | "next" | "prev" | "reload"): Promise<void> {
    const req = this.pager.begin(move);
    if (!req) return;
    this.render();
    try {
      const page = await econApi.inventory({ kind: this.kind ?? undefined, location: "inventory", limit: this.opts.pageSize ?? 12, after: req.after });
      if (this.disposed || !this.pager.receive(req.ticket, page)) return;
      this.showError(null);
    } catch (e) {
      if (this.disposed || !this.pager.fail(req.ticket)) return;
      this.showError(e instanceof ApiError || e instanceof Error ? e.message : String(e));
    }
    this.render();
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  /** Units of `item` still free to use (its stack minus what is used elsewhere). */
  private free(item: EconItem): number {
    return Math.max(0, item.quantity - (this.opts.used?.().get(item.id) ?? 0));
  }

  render(): void {
    const p = this.pager;
    if (!p.loaded) {
      this.list.replaceChildren(h("li", { class: "muted-line" }, "Opening your trunk…"));
      this.nav.replaceChildren();
      return;
    }
    if (!p.items.length) {
      this.list.replaceChildren(h("li", { class: "muted-line" }, this.kind ? `No ${KIND_LABELS[this.kind].toLowerCase()} in your trunk.` : "Your trunk is empty."));
    } else {
      this.list.replaceChildren(...p.items.map((item) => this.row(item)));
    }
    this.nav.replaceChildren(p.hasPrev || p.hasNext ? pagerNav(p, (m) => void this.move(m), "Trunk") : h("span", {}));
  }

  private row(item: EconItem): HTMLElement {
    const free = this.free(item);
    const chosen = this.chosen?.id === item.id;
    const li = h(
      "li",
      { class: chosen ? "picker-item chosen" : "picker-item" },
      h(
        "button",
        {
          type: "button",
          class: "picker-choose",
          "aria-pressed": String(chosen),
          disabled: free === 0,
          title: free === 0 ? "Already in use" : undefined,
          onclick: () => this.choose(item),
        },
        itemRow(item, free < item.quantity ? h("span", { class: "picker-used" }, free === 0 ? "all in use" : `${free} free`) : null),
      ),
    );
    if (chosen) li.append(this.quantityForm(item, free));
    return li;
  }

  private choose(item: EconItem): void {
    const free = this.free(item);
    if (free === 0) return;
    if (this.opts.askQuantity === false || (free === 1 && stackLimit(econDef(item.item_def)) === 1)) {
      this.opts.onPick(item, 1);
      return;
    }
    this.chosen = this.chosen?.id === item.id ? null : item;
    this.render();
    if (this.chosen) this.list.querySelector<HTMLInputElement>(".picker-item.chosen input")?.focus();
  }

  private quantityForm(item: EconItem, free: number): HTMLElement {
    const qty = wholeInput({ label: `How many of ${defName(item.item_def)} (up to ${free})`, value: free, onenter: () => submit() });
    const submit = () => {
      const n = parseWhole(qty.value);
      if (n === null || n < 1 || n > free) {
        this.showError(`Choose between 1 and ${free}.`);
        qty.focus();
        return;
      }
      this.showError(null);
      this.chosen = null;
      this.opts.onPick(item, n);
      this.render();
    };
    return h(
      "div",
      { class: "picker-quantity" },
      h("label", {}, "How many ", qty, h("span", { class: "muted" }, ` of ${free}`)),
      h("button", { type: "button", class: "primary small", onclick: submit }, this.opts.action),
    );
  }
}

/** The text of a failed request for an error line. */
export function errorMessage(e: unknown, explain?: (code: string, message: string) => string): string {
  if (e instanceof ApiError) return explain ? explain(e.code, e.message) : e.message;
  return e instanceof Error ? e.message : String(e);
}

/** A window header: title and a close button. */
export function windowHead(title: string, onClose: () => void, extra?: Node | null): HTMLElement {
  return h("header", { class: "econ-head" }, h("h2", {}, title), extra ?? null, h("button", { type: "button", class: "icon-button", "aria-label": `Close ${title}`, title: "Close (Esc)", onclick: onClose }, "×"));
}
