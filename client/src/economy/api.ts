// The economy's HTTP endpoints (docs/architecture.md, "HTTP API"):
// wallet, owl mail, the owl-order catalogue, the Owl Exchange
// and the inventory pickers. Shapes stay as the server spells them
// (snake_case); every list is a keyset page. Writes the server can retry
// safely take a `request_id` (see paging.ts, `RetryKeys`).

import { authedRequest } from "../api";
import type { Dyes } from "../content/catalog";
import type { Finish } from "../wasm/sim_wasm";
import type { EconItem, EconKind, ItemLocation } from "./items";
import type { Page } from "./paging";

/** `?a=1&b=x` from the defined, non-empty values. */
export function queryString(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

// ------------------------------------------------------------------ wallet

export function wallet(): Promise<{ crowns: number }> {
  return authedRequest("GET", "/wallet");
}

// ------------------------------------------------------------------ inventory

export interface InventoryQuery {
  kind?: EconKind;
  location?: ItemLocation;
  limit?: number;
  after?: string | null;
}

export function inventory(q: InventoryQuery): Promise<Page<EconItem>> {
  return authedRequest("GET", `/inventory${queryString({ kind: q.kind, location: q.location, limit: q.limit, after: q.after })}`);
}

// ------------------------------------------------------------------ mail

export type MailKind = "letter" | "shop" | "exchange_sold" | "exchange_bought" | "exchange_returned" | "exchange_expired" | "system" | "house_cup";

export interface Mail {
  id: number;
  kind: MailKind;
  sender_id: number | null;
  sender_name: string;
  subject: string;
  created_at: string;
  read: boolean;
  collected: boolean;
  crowns: number;
  /** How many items are attached. */
  attachments: number;
}

export interface MailDetail extends Mail {
  body: string;
  items: EconItem[];
}

export function mailbox(after: string | null, limit = 25): Promise<Page<Mail> & { unread: number }> {
  return authedRequest("GET", `/mail${queryString({ limit, after })}`);
}

/** Opens a letter (and marks it read). */
export function readMail(id: number): Promise<MailDetail> {
  return authedRequest("GET", `/mail/${id}`);
}

export interface Letter {
  to: string;
  subject: string;
  body?: string;
  items?: { item_id: number; quantity: number }[];
  crowns?: number;
  request_id?: string;
}

export function sendLetter(letter: Letter): Promise<{ mail: Mail; crowns: number }> {
  return authedRequest("POST", "/mail", letter);
}

/** Collects a letter's enclosures: `items` are the stacks they merged into, `crowns` the balance after. */
export function collectMail(id: number): Promise<{ items: EconItem[]; crowns: number }> {
  return authedRequest("POST", `/mail/${id}/collect`);
}

export function deleteMail(id: number): Promise<void> {
  return authedRequest("DELETE", `/mail/${id}`);
}

/**
 * A letter in its sender's Outbox. `read` and `collected` are the
 * recipient's; `crowns` and `attachments` are what was sent (kept after
 * collection).
 */
export interface SentMail {
  id: number;
  recipient_id: number;
  recipient_name: string;
  subject: string;
  created_at: string;
  read: boolean;
  collected: boolean;
  crowns: number;
  attachments: number;
}

export interface SentMailDetail extends SentMail {
  body: string;
  /** The items as they were attached. */
  items: EconItem[];
}

/** The Outbox: the player's own letters, newest first. */
export function sentMail(after: string | null, limit = 25): Promise<Page<SentMail>> {
  return authedRequest("GET", `/mail/sent${queryString({ limit, after })}`);
}

/** One sent letter, read-only (marks nothing). */
export function readSentMail(id: number): Promise<SentMailDetail> {
  return authedRequest("GET", `/mail/sent/${id}`);
}

// ------------------------------------------------------------------ shop

export interface ShopItem {
  item_def: number;
  price: number;
}

export interface ShopQuery {
  kind?: EconKind;
  category?: string;
  subcategory?: string;
  limit?: number;
  after?: string | null;
}

export function shop(q: ShopQuery): Promise<Page<ShopItem>> {
  return authedRequest("GET", `/shop${queryString({ kind: q.kind, category: q.category, subcategory: q.subcategory, limit: q.limit, after: q.after })}`);
}

export interface OrderLine {
  item_def: number;
  quantity: number;
}

export interface Order {
  id: number;
  total: number;
  lines: OrderLine[];
  mail_id: number;
}

export function placeOrder(lines: OrderLine[], requestId: string): Promise<{ order: Order; crowns: number }> {
  return authedRequest("POST", "/shop/orders", { lines, request_id: requestId });
}

// ------------------------------------------------------------------ exchange

export type ListingState = "active" | "sold" | "cancelled" | "expired";

export interface Listing {
  id: number;
  item_def: number;
  quantity: number;
  price: number;
  dyes: Dyes;
  finish: Finish;
  maker: string | null;
  seller_name: string;
  category: string;
  subcategory: string;
  required_level: number;
  colour: number;
  house_theme: string | null;
  created_at: string;
  expires_at: string;
  state: ListingState;
}

/** Browse at the counter; `query` is built by `exchange-state.ts` (`browseQuery`). */
export function listings(query: string): Promise<Page<Listing>> {
  return authedRequest("GET", `/exchange/listings${query}`);
}

export function myListings(state: ListingState | null, after: string | null, limit = 25): Promise<Page<Listing>> {
  return authedRequest("GET", `/exchange/mine${queryString({ state, limit, after })}`);
}

export function createListing(itemId: number, quantity: number, price: number, requestId: string): Promise<{ listing: Listing; crowns: number }> {
  return authedRequest("POST", "/exchange/listings", { item_id: itemId, quantity, price, request_id: requestId });
}

export function takeDown(listingId: number): Promise<void> {
  return authedRequest("DELETE", `/exchange/listings/${listingId}`);
}

export function buyListing(listingId: number, requestId: string): Promise<{ mail_id: number; crowns: number }> {
  return authedRequest("POST", `/exchange/listings/${listingId}/buy`, { request_id: requestId });
}
