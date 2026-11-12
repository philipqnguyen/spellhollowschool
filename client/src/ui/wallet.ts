// The purse on the HUD: your Crowns, kept current by `Wallet` messages
// (after Welcome and on every change) and by the balances HTTP replies
// carry. A change shows briefly as "+25" or "−3" beside the amount and is
// announced to screen readers.

import "./wallet.css";
import { coinIcon } from "../economy/icons";
import { formatDelta, formatCrowns, crownsText } from "../economy/money";
import { h } from "./dom";

/** How long the change flourish stays (ms). */
const DELTA_MS = 2400;

export class WalletDisplay {
  readonly root: HTMLElement;
  private readonly amount: HTMLElement;
  private readonly delta: HTMLElement;
  private readonly announce: HTMLElement;
  private value: number | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly listeners = new Set<(crowns: number) => void>();

  constructor() {
    this.amount = h("span", { class: "wallet-amount" }, "–");
    this.delta = h("span", { class: "wallet-delta", "aria-hidden": "true", hidden: true });
    this.announce = h("span", { class: "sr-only", role: "status", "aria-live": "polite" });
    this.root = h("div", { class: "wallet", title: "Your Crowns" }, coinIcon(), this.amount, this.delta, this.announce);
    this.root.setAttribute("aria-label", "Crowns: unknown");
  }

  /** The balance as last reported, or null before the first report. */
  get crowns(): number | null {
    return this.value;
  }

  /** Called with every new balance (windows keep their checks current). */
  subscribe(fn: (crowns: number) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /**
   * A new balance. The first report after sign-in only sets it; later ones
   * show the change.
   */
  set(crowns: number): void {
    if (!Number.isSafeInteger(crowns)) return;
    const before = this.value;
    this.value = crowns;
    this.amount.textContent = formatCrowns(crowns);
    this.root.setAttribute("aria-label", `Crowns: ${crownsText(crowns)}`);
    if (before !== null && before !== crowns) this.flash(crowns - before);
    for (const fn of this.listeners) fn(crowns);
  }

  /** A new connection: its first `Wallet` reports the balance again rather than a change. */
  reconnected(): void {
    this.value = null;
  }

  private flash(delta: number): void {
    this.delta.textContent = formatDelta(delta);
    this.delta.classList.toggle("gain", delta > 0);
    this.delta.classList.toggle("loss", delta < 0);
    this.delta.hidden = false;
    // Restart the animation for back-to-back changes.
    this.delta.style.animation = "none";
    void this.delta.offsetWidth;
    this.delta.style.animation = "";
    this.announce.textContent = delta > 0 ? `${crownsText(delta)} received.` : `${crownsText(-delta)} spent.`;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.delta.hidden = true;
      this.timer = null;
    }, DELTA_MS);
  }

  dispose(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.listeners.clear();
  }
}
