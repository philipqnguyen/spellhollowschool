// The trade window's state machine on the client (docs/architecture.md,
// "Economy and crafting"). The server owns the trade; this keeps what the window
// shows and what it lets the player do:
//
//   request ──► (they accept) ──► open: both edit ──► both locked: review
//      │                              ▲     │               │
//      └─► declined / expired         │     └─ any change ◄─┤ confirm (rev)
//                                     └──── unlock ◄────────┘     │
//                                                 both confirmed ─┴─► completed
//
// Every `TradeState` carries a revision; any change to either offer or lock
// bumps it and clears both confirmations, so the player only ever confirms
// what they reviewed. When a revision clears a confirmation, the session
// says why ("Basil changed their offer") and marks the lines that changed.
// One action at a time: a lock or confirm in flight disables the buttons
// until the server answers. Pure, unit tested in trade-state.test.ts.

import type { OfferedItem, TradeErrorCode, TradeOffer, TradeOutcome, TradeView } from "../wasm/sim_wasm";
import { ECONOMY, isWhole, type EconomyRules } from "./money";

/** Someone asked us to trade (`TradeRequested`). */
export interface IncomingRequest {
  tradeId: number;
  from: number;
  fromName: string;
  /** Local clock (ms) when it lapses. */
  until: number;
}

/**
 * We asked someone. The server echoes our `TradeRequested` back (`from` =
 * us) with the trade id, which lets us withdraw it; until then it is null.
 */
export interface OutgoingRequest {
  target: number;
  targetName: string;
  tradeId: number | null;
  until: number;
}

/** Where a side of the window stands. */
export type SideState = "editing" | "locked" | "confirmed";

/** The step the window is at, for its headline. */
export type TradeStep = "editing" | "review" | "waiting" | "completing";

export interface TradeStatus {
  step: TradeStep;
  headline: string;
  mine: SideState;
  theirs: SideState;
  canEdit: boolean;
  canLock: boolean;
  canUnlock: boolean;
  canConfirm: boolean;
}

/** One line of an offer as the client sends it (`OfferItem`). */
export interface OfferLine {
  itemId: number;
  quantity: number;
}

/** A whole offer as the client sends it (`TradeSetOffer`). */
export interface DraftOffer {
  items: OfferLine[];
  crowns: number;
}

/** The longest an incoming request toast may be listed. */
export const MAX_INCOMING = 3;

type Pending = "offer" | "lock" | "unlock" | "confirm";

export class TradeSession {
  incoming: IncomingRequest[] = [];
  outgoing: OutgoingRequest | null = null;
  view: TradeView | null = null;
  /** Why the last revision cleared confirmations, until the next one; null when it did not. */
  resetReason: string | null = null;
  /** Offer lines (`lineKey`) that changed with the last revision, per side; `"crowns"` for the purse. */
  changedTheirs = new Set<string>();
  changedMine = new Set<string>();
  /** The action waiting for the server's answer. */
  private pending: Pending | null = null;

  get open(): boolean {
    return this.view !== null;
  }

  get busy(): boolean {
    return this.pending !== null;
  }

  // ---------------------------------------------------------------- requests

  /** Before sending `TradeRequest`: an error to show instead, or null to go ahead. */
  canRequest(target: number, selfId: number): string | null {
    if (target === selfId) return "You cannot trade with yourself.";
    if (this.view) return "Finish the trade you have open first.";
    if (this.outgoing) return `You are already waiting for ${this.outgoing.targetName} to answer.`;
    return null;
  }

  /** We sent `TradeRequest` to `target`. */
  requested(target: number, targetName: string, nowMs: number, secs: number): void {
    this.outgoing = { target, targetName, tradeId: null, until: nowMs + secs * 1000 };
  }

  /** Our own `TradeRequested` echoed back: now we know its id (and can withdraw it). */
  acknowledged(tradeId: number, expiresInSecs: number, nowMs: number): void {
    if (!this.outgoing) return;
    this.outgoing = { ...this.outgoing, tradeId, until: nowMs + expiresInSecs * 1000 };
  }

  /** `TradeRequested`: a newer request from the same person replaces theirs. */
  onRequested(tradeId: number, from: number, fromName: string, expiresInSecs: number, nowMs: number): IncomingRequest {
    const req = { tradeId, from, fromName, until: nowMs + expiresInSecs * 1000 };
    this.incoming = this.incoming.filter((r) => r.from !== from && r.tradeId !== tradeId);
    this.incoming.push(req);
    if (this.incoming.length > MAX_INCOMING) this.incoming.splice(0, this.incoming.length - MAX_INCOMING);
    return req;
  }

  /** We answered a request (`TradeRespond`); it leaves the list either way. */
  responded(tradeId: number): IncomingRequest | undefined {
    const req = this.incoming.find((r) => r.tradeId === tradeId);
    this.incoming = this.incoming.filter((r) => r.tradeId !== tradeId);
    return req;
  }

  /** Drops lapsed requests (ours and theirs); returns a line about ours if it lapsed. */
  expire(nowMs: number): { incoming: IncomingRequest[]; outgoing: OutgoingRequest | null } {
    const gone = this.incoming.filter((r) => r.until <= nowMs);
    if (gone.length) this.incoming = this.incoming.filter((r) => r.until > nowMs);
    let outgoing: OutgoingRequest | null = null;
    // A little grace on ours: the server's expiry message should get here first.
    if (this.outgoing && this.outgoing.until + 2000 <= nowMs) {
      outgoing = this.outgoing;
      this.outgoing = null;
    }
    return { incoming: gone, outgoing };
  }

  // ---------------------------------------------------------------- the window

  /** We sent an action; buttons wait for the answer. */
  sent(action: Pending): void {
    this.pending = action;
  }

  /** `TradeState`: the window as the server has it now. */
  onState(view: TradeView): void {
    const prev = this.view && this.view.tradeId === view.tradeId ? this.view : null;
    this.pending = null;
    this.outgoing = null;
    this.incoming = this.incoming.filter((r) => r.tradeId !== view.tradeId);
    this.view = view;
    if (!prev) {
      this.resetReason = null;
      this.changedMine.clear();
      this.changedTheirs.clear();
      return;
    }
    // Confirming does not change the revision (both confirm the same one);
    // everything else that changes the trade does.
    if (prev.rev !== view.rev) {
      // Change marks show what moved since the offers last changed (a lock
      // alone keeps them), until we confirm what we saw.
      const theirs = changedLines(prev.theirs, view.theirs);
      const mine = changedLines(prev.mine, view.mine);
      if (theirs.size) this.changedTheirs = theirs;
      if (mine.size) this.changedMine = mine;
      if ((prev.mineConfirmed || prev.theirsConfirmed) && !view.mineConfirmed && !view.theirsConfirmed) {
        // The reason stays up until we confirm again (or a newer reset replaces it).
        this.resetReason = resetCause(prev, view);
      }
    }
    if (view.mineConfirmed) {
      this.resetReason = null;
      this.changedTheirs.clear();
      this.changedMine.clear();
    }
  }

  /** `TradeResult`: the window or a request ended. Returns the line to show, or null if it was not ours. */
  onResult(tradeId: number, outcome: TradeOutcome): string | null {
    const view = this.view && this.view.tradeId === tradeId ? this.view : null;
    const incoming = this.incoming.find((r) => r.tradeId === tradeId);
    // Ours: by its id once acknowledged, else the only request we have out.
    const outgoing = !view && !incoming && this.outgoing && (this.outgoing.tradeId === null || this.outgoing.tradeId === tradeId) ? this.outgoing : null;
    if (!view && !incoming && !outgoing) return null;
    const partner = view?.partnerName ?? incoming?.fromName ?? outgoing?.targetName ?? "they";
    if (view) this.closeWindow();
    if (incoming) this.incoming = this.incoming.filter((r) => r.tradeId !== tradeId);
    if (outgoing) this.outgoing = null;
    return outcomeText(outcome, partner, !!outgoing);
  }

  /** `TradeError`: the action was refused, the window unchanged. Returns the line to show. */
  onError(tradeId: number | undefined, code: TradeErrorCode): string {
    this.pending = null;
    // A refused request: we are not waiting for anyone after all.
    if (tradeId === undefined) {
      const who = this.outgoing?.targetName;
      this.outgoing = null;
      return errorText(code, who);
    }
    return errorText(code, this.view?.partnerName);
  }

  /** A new connection or zone: every window and request is gone on the server too. */
  reset(): void {
    this.incoming = [];
    this.outgoing = null;
    this.closeWindow();
  }

  private closeWindow(): void {
    this.view = null;
    this.pending = null;
    this.resetReason = null;
    this.changedMine.clear();
    this.changedTheirs.clear();
  }

  /** What the window shows and allows now (null when no window is open). */
  status(): TradeStatus | null {
    const v = this.view;
    if (!v) return null;
    const side = (locked: boolean, confirmed: boolean): SideState => (confirmed ? "confirmed" : locked ? "locked" : "editing");
    const mine = side(v.mineLocked, v.mineConfirmed);
    const theirs = side(v.theirsLocked, v.theirsConfirmed);
    const busy = this.pending !== null;
    let step: TradeStep;
    let headline: string;
    if (v.mineConfirmed && v.theirsConfirmed) {
      step = "completing";
      headline = "Both confirmed. The owls are carrying everything across…";
    } else if (v.mineConfirmed) {
      step = "waiting";
      headline = `You confirmed. Waiting for ${v.partnerName} to confirm.`;
    } else if (v.mineLocked && v.theirsLocked) {
      step = "review";
      headline = v.theirsConfirmed ? `${v.partnerName} confirmed. Review both offers, then confirm.` : "Both offers are locked. Review them, then confirm.";
    } else {
      step = "editing";
      headline = v.mineLocked ? `Your offer is locked. Waiting for ${v.partnerName} to lock theirs.` : v.theirsLocked ? `${v.partnerName} is ready. Add what you'll give, then lock your offer.` : "Add what you'll give, then lock your offer.";
    }
    return {
      step,
      headline,
      mine,
      theirs,
      canEdit: !v.mineLocked && !busy,
      canLock: !v.mineLocked && !busy,
      canUnlock: v.mineLocked && !(v.mineConfirmed && v.theirsConfirmed) && !busy,
      canConfirm: v.mineLocked && v.theirsLocked && !v.mineConfirmed && !busy,
    };
  }

  /** Our offer as it stands, in the form `TradeSetOffer` takes. */
  draft(): DraftOffer {
    const mine = this.view?.mine;
    return { items: mine ? mine.items.map((i) => ({ itemId: i.itemId, quantity: i.quantity })) : [], crowns: mine?.crowns ?? 0 };
  }
}

/** A line's identity for change marks: the item and how many. */
export function lineKey(item: Pick<OfferedItem, "itemId" | "quantity">): string {
  return `${item.itemId}:${item.quantity}`;
}

/** Lines of `after` that were not in `before` (same item and quantity), plus `"crowns"` if that changed. */
export function changedLines(before: TradeOffer, after: TradeOffer): Set<string> {
  const old = new Set(before.items.map(lineKey));
  const out = new Set<string>();
  for (const i of after.items) if (!old.has(lineKey(i))) out.add(lineKey(i));
  if (before.crowns !== after.crowns) out.add("crowns");
  // Something was taken out: nothing new to mark, but the offer changed.
  if (!out.size && before.items.length !== after.items.length) out.add("removed");
  return out;
}

export function sameOffer(a: TradeOffer, b: TradeOffer): boolean {
  if (a.crowns !== b.crowns || a.items.length !== b.items.length) return false;
  const keys = new Set(a.items.map(lineKey));
  return b.items.every((i) => keys.has(lineKey(i)));
}

/** Why a revision cleared the confirmations. */
function resetCause(prev: TradeView, view: TradeView): string {
  const tail = "Both confirmations were reset: review the trade again.";
  if (!sameOffer(prev.theirs, view.theirs)) return `${view.partnerName} changed their offer. ${tail}`;
  if (!sameOffer(prev.mine, view.mine)) return `Your offer changed. ${tail}`;
  if (prev.theirsLocked && !view.theirsLocked) return `${view.partnerName} unlocked their offer. ${tail}`;
  if (prev.mineLocked && !view.mineLocked) return `You unlocked your offer. ${tail}`;
  return `The trade changed. ${tail}`;
}

// ---------------------------------------------------------------- editing offers

/** Our offer with `quantity` of `itemId` (replacing that item's line). */
export function withLine(draft: DraftOffer, itemId: number, quantity: number): DraftOffer {
  const items = draft.items.filter((l) => l.itemId !== itemId);
  if (quantity > 0) items.push({ itemId, quantity });
  return { items, crowns: draft.crowns };
}

export function withoutLine(draft: DraftOffer, itemId: number): DraftOffer {
  return { items: draft.items.filter((l) => l.itemId !== itemId), crowns: draft.crowns };
}

export function withCrowns(draft: DraftOffer, crowns: number): DraftOffer {
  return { items: draft.items, crowns };
}

/**
 * What is wrong with an offer before it is sent, or null: at most
 * `itemsMax` lines, each item once, whole quantities within what the stack
 * holds (`available`, by item id), Crowns whole, at least 0, within the
 * trade limit and the balance.
 */
export function offerProblem(draft: DraftOffer, available: ReadonlyMap<number, number>, balance: number, itemsMax: number, rules: EconomyRules = ECONOMY): string | null {
  if (draft.items.length > itemsMax) return `At most ${itemsMax} different items in one trade.`;
  const seen = new Set<number>();
  for (const l of draft.items) {
    if (seen.has(l.itemId)) return "That item is already in your offer.";
    seen.add(l.itemId);
    if (!isWhole(l.quantity) || l.quantity < 1) return "Offer at least one of each item.";
    const have = available.get(l.itemId);
    if (have !== undefined && l.quantity > have) return `You only have ${have} of that.`;
  }
  if (!isWhole(draft.crowns) || draft.crowns < 0) return "Offer a whole number of Crowns.";
  if (draft.crowns > rules.trade.crowns_max) return `At most ${rules.trade.crowns_max.toLocaleString("en-GB")} Crowns in one trade.`;
  if (draft.crowns > balance) return "You don't have that many Crowns.";
  return null;
}

// ---------------------------------------------------------------- words

export function errorText(code: TradeErrorCode, partner?: string): string {
  switch (code) {
    case "unknown_target":
      return "That player can't be asked to trade right now.";
    case "self_trade":
      return "You cannot trade with yourself.";
    case "too_far":
      return partner ? `Walk closer to ${partner} to trade (within a few steps).` : "Walk closer to trade (within a few steps).";
    case "busy":
      return partner ? `${partner} is busy with another trade.` : "One of you is already trading or waiting for an answer.";
    case "unknown_trade":
      return "That trade has already ended.";
    case "expired":
      return "That trade request has lapsed.";
    case "locked":
      return "Unlock your offer to change it.";
    case "not_locked":
      return "Both offers must be locked before confirming.";
    case "stale_revision":
      return "The trade changed while you were confirming. Review it again.";
    case "invalid_offer":
      return "That offer isn't allowed (too many items, or more than you have).";
    case "item_unavailable":
      return "One of those items is no longer in your trunk (worn, placed, mailed or listed).";
    case "insufficient_crowns":
      return "You don't have that many Crowns.";
    case "rate_limited":
      return "Slow down a little, then try again.";
    case "failed":
      return partner ? `The trade with ${partner} could not go ahead. Nothing was exchanged.` : "The trade could not go ahead. Nothing was exchanged.";
  }
}

export function outcomeText(outcome: TradeOutcome, partner: string, ourRequest: boolean): string {
  switch (outcome.kind) {
    case "completed":
      return `Trade with ${partner} complete.`;
    case "cancelled":
      return ourRequest ? `Your trade request to ${partner} was withdrawn.` : `The trade with ${partner} was cancelled.`;
    case "declined":
      return ourRequest ? `${partner} declined to trade.` : `You declined ${partner}'s trade.`;
    case "expired":
      return ourRequest ? `${partner} didn't answer your trade request.` : `${partner}'s trade request lapsed.`;
    case "walked_away":
      return `The trade with ${partner} ended: you walked too far apart.`;
    case "disconnected":
      return `The trade with ${partner} ended: someone left the castle.`;
    case "failed":
      return outcome.code === "failed" ? `The trade with ${partner} failed. Nothing was exchanged.` : `The trade with ${partner} failed. Nothing was exchanged. ${errorText(outcome.code, partner)}`;
  }
}

/** Whole seconds left on a request (rounded up). */
export function secondsLeft(untilMs: number, nowMs: number): number {
  return Math.max(0, Math.ceil((untilMs - nowMs) / 1000));
}
