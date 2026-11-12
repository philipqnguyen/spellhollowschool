// Crowns on the client: the shipped economy rules (data/economy.json, the
// file the server compiles in) and the money maths the windows show before
// the player commits: the Exchange listing fee and sales tax, what a seller
// receives, the cost of sending a letter, a shop basket's total.
//
// Crowns are whole numbers, always (docs/architecture.md, "Economy and crafting").
// Nothing here uses fractions: tax is rounded down
// with an exact integer division, and every sum is checked to stay a safe
// integer, so the numbers on screen are the ones `sim_core::economy`
// computes. Pure, unit tested in money.test.ts.

import economyJson from "../../../data/economy.json";

/** `sim_core::economy::Economy`, the parts the client uses. */
export interface EconomyRules {
  version: number;
  starting_purse: number;
  exchange: {
    listing_fee: number;
    sales_tax_pct: number;
    listing_secs: number;
    active_listings_max: number;
    price_min: number;
    price_max: number;
    search_burst: number;
    search_refill_secs: number;
  };
  mail: {
    postage: number;
    subject_max_chars: number;
    body_max_chars: number;
    attachments_max: number;
    crowns_max: number;
    send_burst: number;
    send_refill_secs: number;
    /** Days a collected (or empty) letter stays in the mailbox. */
    retention_days?: number;
  };
  trade: { distance_m: number; leave_slack_m: number; crowns_max: number };
}

export const ECONOMY = economyJson as EconomyRules;

/** Whether `n` is a whole number JavaScript represents exactly. */
export function isWhole(n: number): boolean {
  return Number.isSafeInteger(n);
}

/** `a + b`, or null when either is not whole or the sum leaves the safe range. */
export function addChecked(a: number, b: number): number | null {
  if (!isWhole(a) || !isWhole(b)) return null;
  const s = a + b;
  return isWhole(s) ? s : null;
}

/** `a * b`, or null when either is not whole or the product leaves the safe range. */
export function mulChecked(a: number, b: number): number | null {
  if (!isWhole(a) || !isWhole(b)) return null;
  const p = a * b;
  return isWhole(p) ? p : null;
}

/** `floor(a / b)` for whole `a >= 0` and `b > 0`, exactly (no float rounding). */
export function divFloor(a: number, b: number): number {
  return (a - (a % b)) / b;
}

/**
 * The tax on an Exchange sale at `price` (`ExchangeRules::sales_tax`):
 * `price * sales_tax_pct / 100`, rounded down; null for a negative or
 * non-whole price, or on overflow.
 */
export function salesTax(price: number, rules: EconomyRules = ECONOMY): number | null {
  if (!isWhole(price) || price < 0) return null;
  const t = mulChecked(price, rules.exchange.sales_tax_pct);
  return t === null ? null : divFloor(t, 100);
}

/** What the seller receives for a sale at `price` (`ExchangeRules::proceeds`). */
export function proceeds(price: number, rules: EconomyRules = ECONOMY): number | null {
  const tax = salesTax(price, rules);
  return tax === null ? null : price - tax;
}

/** Whether `price` may be asked on the Exchange (`ExchangeRules::price_ok`). */
export function priceOk(price: number, rules: EconomyRules = ECONOMY): boolean {
  return isWhole(price) && price >= rules.exchange.price_min && price <= rules.exchange.price_max;
}

/** What sending a letter carrying `crowns` costs (`MailRules::send_cost`). */
export function sendCost(crowns: number, rules: EconomyRules = ECONOMY): number | null {
  if (!isWhole(crowns) || crowns < 0) return null;
  return addChecked(crowns, rules.mail.postage);
}

/** Everything a listing's numbers say, for the sell form. */
export interface ListingQuote {
  price: number;
  /** Paid now, kept on expiry or take-down. */
  fee: number;
  /** Taken from the price when it sells. */
  tax: number;
  /** What arrives by owl when it sells. */
  proceeds: number;
  /** Proceeds minus the fee: what the sale gains overall (may be negative for tiny prices). */
  net: number;
}

/** The quote for listing at `price`, or null when the price may not be asked. */
export function listingQuote(price: number, rules: EconomyRules = ECONOMY): ListingQuote | null {
  if (!priceOk(price, rules)) return null;
  const tax = salesTax(price, rules);
  const got = proceeds(price, rules);
  if (tax === null || got === null) return null;
  const fee = rules.exchange.listing_fee;
  return { price, fee, tax, proceeds: got, net: got - fee };
}

/**
 * The whole number typed in `text` (digits only, optional thousands
 * separators and surrounding spaces), or null for anything else: no signs,
 * no decimals, no exponents, nothing above the safe range.
 */
export function parseWhole(text: string): number | null {
  const t = text.trim().replace(/[,_\s]/g, "");
  if (!/^\d{1,16}$/.test(t)) return null;
  const n = Number(t);
  return isWhole(n) ? n : null;
}

const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0, useGrouping: true });

/** `1234` → `"1,234"`. */
export function formatCrowns(n: number): string {
  return GROUPED.format(n);
}

/** `1` → `"1 Crown"`, `1234` → `"1,234 Crowns"`, for labels read aloud. */
export function crownsText(n: number): string {
  return `${formatCrowns(n)} ${n === 1 || n === -1 ? "Crown" : "Crowns"}`;
}

/** A balance change for the HUD's flourish: `"+25"` or `"−3"` (a real minus sign). */
export function formatDelta(delta: number): string {
  return delta >= 0 ? `+${formatCrowns(delta)}` : `−${formatCrowns(-delta)}`;
}
