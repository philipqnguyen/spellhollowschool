// The owl-order catalogue's basket without the DOM: lines of catalogue items
// and quantities within what one order allows (1-10 lines; 1-99 units a
// line, 1-10 for clothing and furniture), the checked total and whether the
// purse covers it. Prices come from the catalogue page the server sent.
// Pure, unit tested in shop-state.test.ts.

import type { OrderLine, ShopItem } from "./api";
import { econDef } from "./items";
import { addChecked, isWhole, mulChecked } from "./money";

export const ORDER_LINES_MAX = 10;
export const UNITS_MAX = 99;
/** Clothing and furniture are one row per item, so fewer per line. */
export const UNITS_MAX_SINGLE = 10;

export interface BasketLine {
  itemDef: number;
  price: number;
  quantity: number;
}

/** Most units of `itemDef` one order line may carry. */
export function unitsMax(itemDef: number): number {
  const kind = econDef(itemDef)?.kind;
  return kind === "clothing" || kind === "furniture" ? UNITS_MAX_SINGLE : UNITS_MAX;
}

export class Basket {
  lines: BasketLine[] = [];

  /**
   * Adds `quantity` of a catalogue item (topping up its line). Returns an
   * error to show, or null.
   */
  add(item: ShopItem, quantity = 1): string | null {
    if (!isWhole(quantity) || quantity < 1) return "Order at least one.";
    const line = this.lines.find((l) => l.itemDef === item.item_def);
    const max = unitsMax(item.item_def);
    if (!line) {
      if (this.lines.length >= ORDER_LINES_MAX) return `One order holds at most ${ORDER_LINES_MAX} different items.`;
      if (quantity > max) return `At most ${max} of that in one order.`;
      this.lines.push({ itemDef: item.item_def, price: item.price, quantity });
      return null;
    }
    if (line.quantity + quantity > max) return `At most ${max} of that in one order.`;
    line.quantity += quantity;
    line.price = item.price;
    return null;
  }

  /** Sets a line's quantity (0 removes it); quantities are clamped to what a line allows. */
  set(itemDef: number, quantity: number): void {
    const line = this.lines.find((l) => l.itemDef === itemDef);
    if (!line) return;
    if (!isWhole(quantity) || quantity <= 0) {
      this.remove(itemDef);
      return;
    }
    line.quantity = Math.min(quantity, unitsMax(itemDef));
  }

  remove(itemDef: number): void {
    this.lines = this.lines.filter((l) => l.itemDef !== itemDef);
  }

  clear(): void {
    this.lines = [];
  }

  get empty(): boolean {
    return this.lines.length === 0;
  }

  /** Units across every line. */
  get units(): number {
    return this.lines.reduce((n, l) => n + l.quantity, 0);
  }

  /** The order's total in Crowns; null if it overflows (it cannot with the shipped limits). */
  total(): number | null {
    let total = 0;
    for (const l of this.lines) {
      const cost = mulChecked(l.price, l.quantity);
      const sum = cost === null ? null : addChecked(total, cost);
      if (sum === null) return null;
      total = sum;
    }
    return total;
  }

  /** What is wrong with ordering now, or null. */
  problem(balance: number): string | null {
    if (this.empty) return "Your basket is empty.";
    const total = this.total();
    if (total === null) return "That order is too large.";
    if (total > balance) return "You don't have enough Crowns for this order.";
    return null;
  }

  /** The order as `POST /api/shop/orders` takes it. */
  orderLines(): OrderLine[] {
    return this.lines.map((l) => ({ item_def: l.itemDef, quantity: l.quantity }));
  }

  /** A stable key of the basket's contents, so a changed basket is a new order (new request id). */
  key(): string {
    return this.lines
      .map((l) => `${l.itemDef}x${l.quantity}`)
      .sort()
      .join(",");
  }
}

/** Shop tabs: the Exchange categories that have something for sale, in table order. */
export function shopCategories(categories: readonly { id: string; label: string }[], items: readonly { exchange: { category: string }; price?: number }[]): { id: string; label: string }[] {
  const sold = new Set(items.filter((i) => typeof i.price === "number").map((i) => i.exchange.category));
  return categories.filter((c) => sold.has(c.id));
}

export function shopError(code: string, message: string): string {
  switch (code) {
    case "invalid_order":
      return "Something in that order can't be sold. Check the basket and try again.";
    case "insufficient_crowns":
      return "You don't have enough Crowns for this order.";
    case "rate_limited":
      return "The owls are all out. Try again in a moment.";
    case "not_at_station":
      return "Visit the Owl Order Catalogue in your common room.";
    default:
      return message;
  }
}
