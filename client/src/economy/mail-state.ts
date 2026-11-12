// Owl mail without the DOM: what a letter may carry and what it costs, how
// mail is labelled in the mailbox, and the new-mail indicator's rules.
// Pure, unit tested in mail-state.test.ts.

import type { Mail, MailKind, SentMail } from "./api";
import { ECONOMY, isWhole, sendCost, type EconomyRules } from "./money";

/** A letter being written. */
export interface LetterDraft {
  to: string;
  subject: string;
  body: string;
  /** Attached items: id and how many units (a stack may be split). */
  items: { itemId: number; quantity: number }[];
  crowns: number;
}

export const emptyDraft = (): LetterDraft => ({ to: "", subject: "", body: "", items: [], crowns: 0 });

/** Characters as the server counts them (Unicode scalar values, not UTF-16 units). */
export function charCount(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

/**
 * What is wrong with a letter before it is sent, or null: a recipient that
 * is not yourself, a subject of 1-`subject_max_chars`, a body of at most
 * `body_max_chars`, at most `attachments_max` items, whole Crowns up to
 * `crowns_max`, and enough in the purse for the Crowns plus postage.
 */
export function letterProblem(d: LetterDraft, myName: string, balance: number, rules: EconomyRules = ECONOMY): string | null {
  const to = d.to.trim();
  const m = rules.mail;
  if (!to) return "Who is it for? Write a name.";
  if (to.toLowerCase() === myName.toLowerCase()) return "You cannot send an owl to yourself.";
  const subject = d.subject.trim();
  if (!subject) return "Give the letter a subject.";
  if (charCount(subject) > m.subject_max_chars) return `Keep the subject to ${m.subject_max_chars} characters.`;
  if (charCount(d.body) > m.body_max_chars) return `Keep the letter to ${m.body_max_chars.toLocaleString("en-GB")} characters.`;
  if (d.items.length > m.attachments_max) return `An owl can carry at most ${m.attachments_max} items.`;
  if (d.items.some((i) => !isWhole(i.quantity) || i.quantity < 1)) return "Attach at least one of each item.";
  if (!isWhole(d.crowns) || d.crowns < 0) return "Send a whole number of Crowns.";
  if (d.crowns > m.crowns_max) return `An owl can carry at most ${m.crowns_max.toLocaleString("en-GB")} Crowns.`;
  const cost = sendCost(d.crowns, rules);
  if (cost === null || cost > balance) return "You don't have enough Crowns for that and the postage.";
  return null;
}

/** What the sender pays in all: Crowns enclosed plus postage (null for a bad amount). */
export function letterCost(d: Pick<LetterDraft, "crowns">, rules: EconomyRules = ECONOMY): number | null {
  return sendCost(d.crowns, rules);
}

/** The body as the server wants it: omitted when empty. */
export function letterBody(d: LetterDraft): string | undefined {
  return d.body.trim() ? d.body : undefined;
}

export const MAIL_KIND_LABELS: Record<MailKind, string> = {
  letter: "Letter",
  shop: "Owl-order delivery",
  exchange_sold: "Exchange: sold",
  exchange_bought: "Exchange: purchase",
  exchange_returned: "Exchange: returned",
  exchange_expired: "Exchange: unsold",
  system: "Notice",
  // The House Cup reward (the winning house's pennant).
  house_cup: "House Cup",
};

/** Whether a mail still holds something to collect. */
export function hasEnclosures(m: Pick<Mail, "collected" | "crowns" | "attachments">): boolean {
  return !m.collected && (m.crowns > 0 || m.attachments > 0);
}

/** A short line under the subject: what is enclosed. */
export function enclosureText(m: Pick<Mail, "collected" | "crowns" | "attachments">): string {
  if (m.collected) return m.crowns > 0 || m.attachments > 0 ? "Collected" : "";
  const parts: string[] = [];
  if (m.attachments > 0) parts.push(m.attachments === 1 ? "1 item" : `${m.attachments} items`);
  if (m.crowns > 0) parts.push(`${m.crowns.toLocaleString("en-GB")} C`);
  return parts.join(" + ");
}

/** What went with a sent letter ("2 items + 15 C"), whether collected or not. */
export function sentEnclosureText(m: Pick<SentMail, "crowns" | "attachments">): string {
  return enclosureText({ ...m, collected: false });
}

/**
 * Where a sent letter stands, as far as its sender may know: "Delivered"
 * (not opened yet), "Read", and for a letter that carried something,
 * "Read, not collected" until the recipient takes it, then "Collected".
 */
export function sentStatus(m: Pick<SentMail, "read" | "collected" | "crowns" | "attachments">): string {
  const carried = m.crowns > 0 || m.attachments > 0;
  if (carried && m.collected) return "Collected";
  if (!m.read) return "Delivered";
  return carried ? "Read, not collected" : "Read";
}

/** "Today 14:05", "Yesterday", "3 Oct" for the mailbox list. */
export function mailDate(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  if (days === 0) return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}

/**
 * The new-mail indicator: the badge shows the unread count, and an owl flies
 * in when the count goes up (not at sign-in, when the count is just
 * reported, and not when it goes down because a letter was read).
 */
export class MailIndicator {
  unread = 0;
  private known = false;

  /** `MailNotice`: returns true when an owl should fly in. */
  notice(unread: number): boolean {
    const arrived = this.known && unread > this.unread;
    this.unread = unread;
    this.known = true;
    return arrived;
  }

  /** A new connection: the next notice reports, it does not deliver. */
  reconnected(): void {
    this.known = false;
  }

  /** "3", "99+", or "" for none. */
  badge(): string {
    return this.unread <= 0 ? "" : this.unread > 99 ? "99+" : String(this.unread);
  }
}

/** What to tell the player about a refused mail request. */
export function mailError(code: string, message: string): string {
  switch (code) {
    case "character_not_found":
      return "No owl could find anyone by that name.";
    case "self":
      return "You cannot send an owl to yourself.";
    case "invalid_mail":
      return message || "That letter can't be sent as it is.";
    case "item_not_found":
      return "One of the items is no longer yours.";
    case "item_unavailable":
      return "One of the items is worn, placed, listed or already in the post.";
    case "insufficient_crowns":
      return "You don't have enough Crowns for that and the postage.";
    case "already_collected":
      return "You have already collected what came with this letter.";
    case "uncollected":
      return "Collect what came with this letter before throwing it away.";
    case "mail_not_found":
      return "That letter is gone.";
    case "rate_limited":
      return "Your owls need a rest. Try again in a little while.";
    default:
      return message;
  }
}
