// The Owl Exchange without the DOM: browse filters and the query string they
// make (`GET /api/exchange/listings`), the sell form's checks and numbers,
// time left on a listing, and what to say when the server refuses.
// Pure, unit tested in exchange-state.test.ts.

import { CATALOG, isPaletteIndex, type Catalog } from "../content/catalog";
import { HOUSES } from "../content/houses";
import type { Listing } from "./api";
import { queryString } from "./api";
import { isValidCategory, stackLimit, econDef, type EconItem } from "./items";
import { ECONOMY, isWhole, listingQuote, type EconomyRules, type ListingQuote } from "./money";

/** Results per page (the design's page size). */
export const EXCHANGE_PAGE = 25;
/** Player levels (1-20 arrive with classes; items already carry one). */
export const LEVEL_MIN = 1;
export const LEVEL_MAX = 20;
/** Wait this long after typing before searching (searches are rate limited). */
export const SEARCH_DEBOUNCE_MS = 450;

export type ExchangeSort = "price" | "price_desc" | "newest";

export const SORT_LABELS: Record<ExchangeSort, string> = { price: "Price: low to high", price_desc: "Price: high to low", newest: "Newest first" };

export interface BrowseFilters {
  category: string | null;
  subcategory: string | null;
  /** Name search (matched against catalog names on the server). */
  q: string;
  minLevel: number | null;
  maxLevel: number | null;
  /** Primary dye, a palette index. */
  colour: number | null;
  /** House theme, a lower-case house id. */
  house: string | null;
  sort: ExchangeSort;
}

export const defaultFilters = (): BrowseFilters => ({ category: null, subcategory: null, q: "", minLevel: null, maxLevel: null, colour: null, house: null, sort: "newest" });

/** Filters with only valid values: unknown categories, colours or houses and swapped level bounds are dropped or fixed. */
export function normalizeFilters(f: BrowseFilters, catalog: Catalog = CATALOG): BrowseFilters {
  const category = f.category && isValidCategory(f.category, null, catalog) ? f.category : null;
  const subcategory = category && f.subcategory && isValidCategory(category, f.subcategory, catalog) ? f.subcategory : null;
  const level = (n: number | null) => (n !== null && isWhole(n) ? Math.min(LEVEL_MAX, Math.max(LEVEL_MIN, n)) : null);
  let minLevel = level(f.minLevel);
  let maxLevel = level(f.maxLevel);
  if (minLevel !== null && maxLevel !== null && minLevel > maxLevel) [minLevel, maxLevel] = [maxLevel, minLevel];
  return {
    category,
    subcategory,
    q: f.q.trim().slice(0, 64),
    minLevel,
    maxLevel,
    colour: f.colour !== null && isPaletteIndex(f.colour) ? f.colour : null,
    house: f.house && HOUSES.some((h) => h.id === f.house) ? f.house : null,
    sort: f.sort in SORT_LABELS ? f.sort : "newest",
  };
}

/** `?category=…&sort=…&limit=25&after=…` for a browse page. */
export function browseQuery(f: BrowseFilters, after: string | null, limit = EXCHANGE_PAGE): string {
  const n = normalizeFilters(f);
  return queryString({
    category: n.category,
    subcategory: n.subcategory,
    q: n.q,
    min_level: n.minLevel,
    max_level: n.maxLevel,
    colour: n.colour,
    house: n.house,
    sort: n.sort,
    limit,
    after,
  });
}

/** A key of what the filters ask for: equal keys give the same results, so no new search is needed. */
export function filtersKey(f: BrowseFilters): string {
  return browseQuery(f, null);
}

/** How many filters (beyond the category) are set, for the "Clear filters" button. */
export function activeFilterCount(f: BrowseFilters): number {
  const n = normalizeFilters(f);
  return [n.q !== "", n.minLevel !== null, n.maxLevel !== null, n.colour !== null, n.house !== null].filter(Boolean).length;
}

// ---------------------------------------------------------------- selling

export interface SellForm {
  quantity: number;
  price: number;
}

/**
 * What is wrong with listing `quantity` of `item` at `price`, or null: an
 * item in the trunk (not worn, placed, mailed or listed), whole units within
 * its stack, a price the Exchange allows, a free listing slot and the fee in
 * the purse.
 */
export function sellProblem(item: EconItem, form: SellForm, balance: number, activeListings: number | null, rules: EconomyRules = ECONOMY): string | null {
  if (item.location !== "inventory") return "Only items in your trunk can be listed (take it off or out of your room first).";
  if (!isWhole(form.quantity) || form.quantity < 1) return "List at least one.";
  if (form.quantity > item.quantity) return `You only have ${item.quantity}.`;
  const limit = stackLimit(econDef(item.item_def));
  if (form.quantity > limit) return `At most ${limit} in one listing.`;
  const ex = rules.exchange;
  if (!isWhole(form.price) || form.price < ex.price_min || form.price > ex.price_max) return `Ask between ${ex.price_min.toLocaleString("en-GB")} and ${ex.price_max.toLocaleString("en-GB")} Crowns.`;
  if (activeListings !== null && activeListings >= ex.active_listings_max) return `You already have ${ex.active_listings_max} listings up. Take one down or wait for a sale.`;
  if (balance < ex.listing_fee) return `Listing costs a ${ex.listing_fee} Crown fee, and you have ${balance}.`;
  return null;
}

/** The sell form's numbers (fee now, tax and proceeds at sale), or null for a price that may not be asked. */
export function sellQuote(form: SellForm, rules: EconomyRules = ECONOMY): ListingQuote | null {
  return listingQuote(form.price, rules);
}

/** "12 each" when the price splits evenly over the units, else "" (no fractions of a Crown). */
export function eachText(price: number, quantity: number): string {
  if (quantity <= 1 || !isWhole(price) || price % quantity !== 0) return "";
  return `${(price / quantity).toLocaleString("en-GB")} each`;
}

// ---------------------------------------------------------------- time

/** Time left on a listing, coarse: "4 d 23 h", "5 h 12 min", "12 min", "under a minute", "ended". */
export function timeLeft(expiresAt: string, nowMs: number = Date.now()): string {
  const end = Date.parse(expiresAt);
  if (Number.isNaN(end)) return "";
  const secs = Math.floor((end - nowMs) / 1000);
  if (secs <= 0) return "ended";
  const d = Math.floor(secs / 86_400);
  const h = Math.floor((secs % 86_400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${m} min`;
  if (m > 0) return `${m} min`;
  return "under a minute";
}

export const LISTING_STATE_LABELS: Record<Listing["state"], string> = { active: "On sale", sold: "Sold", cancelled: "Taken down", expired: "Unsold" };

/** What to tell the player about a refused Exchange request. */
export function exchangeError(code: string, message: string, rules: EconomyRules = ECONOMY): string {
  switch (code) {
    case "not_at_station":
      return "Step up to the Owl Exchange counter in the Entrance Hall to trade here.";
    case "rate_limited":
      return "The clerk needs a moment between searches. Try again shortly.";
    case "invalid_price":
      return `Ask between ${rules.exchange.price_min.toLocaleString("en-GB")} and ${rules.exchange.price_max.toLocaleString("en-GB")} Crowns.`;
    case "item_not_found":
      return "That item is no longer yours.";
    case "item_unavailable":
      return "That item is worn, placed, listed or in the post.";
    case "listing_limit":
      return `You already have ${rules.exchange.active_listings_max} listings up.`;
    case "insufficient_crowns":
      return "You don't have enough Crowns.";
    case "listing_not_found":
      return "That listing is gone.";
    case "listing_closed":
      return "Too late: that listing has already been sold or taken down.";
    case "own_listing":
      return "That's your own listing.";
    case "invalid_query":
      return "Those filters don't make sense together.";
    default:
      return message;
  }
}
