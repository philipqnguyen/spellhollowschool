// Player-to-player trading: the request toasts, the trade window and the
// player menu (right-click someone in the world: Trade, Whisper, Write).
//
// The window is two offers side by side. Each side adds items and Crowns,
// locks, reviews both, and confirms; any change by either side resets both
// confirmations, and the window says so loudly and marks what changed. The
// trade happens in one transaction on the server when both confirmed the
// same revision. Walking away, changing zone or disconnecting ends it.
// Messages and rules: economy/trade-state.ts; the wire: `encodeTrade*`.

import "../economy/economy.css";
import "./trade.css";
import { defName } from "../economy/items";
import { ECONOMY, formatCrowns, parseWhole } from "../economy/money";
import { lineKey, offerProblem, secondsLeft, TradeSession, withCrowns, withLine, withoutLine, type DraftOffer, type SideState } from "../economy/trade-state";
import { crowns, ItemPicker, itemRow, wholeInput, windowHead } from "../economy/view";
import { encodeTradeCancel, encodeTradeConfirm, encodeTradeLock, encodeTradeRespond, encodeTradeSetOffer, type OfferedItem, type TradeOffer } from "../wasm/sim_wasm";
import { formatCountdown } from "./invites-state";
import { focusOrigin, h, restoreFocus } from "./dom";

export interface TradeWindowOptions {
  session: TradeSession;
  send: (frame: Uint8Array) => void;
  balance: () => number | null;
  itemsMax: number;
  notify: (text: string) => void;
}

const SIDE_LABELS: Record<SideState, string> = { editing: "Still choosing", locked: "Locked", confirmed: "Confirmed" };
/** Typing in the Crowns box sends the new offer after this pause (ms). */
const CROWNS_DEBOUNCE_MS = 700;
/** How long a first Escape waits for the second (ms). */
const ESCAPE_CONFIRM_MS = 3000;

/** The open trade window. */
export class TradeWindow {
  readonly root: HTMLElement;
  private readonly headline: HTMLElement;
  private readonly steps: HTMLElement;
  private readonly reset: HTMLElement;
  private readonly mineCol: HTMLElement;
  private readonly theirsCol: HTMLElement;
  private readonly actions: HTMLElement;
  private readonly error: HTMLElement;
  private readonly title: HTMLElement;
  private picker: ItemPicker | null = null;
  /** Units in each stack we offered from, as the trunk listed them (for checks). */
  private readonly available = new Map<number, number>();
  private crownsTimer: ReturnType<typeof setTimeout> | null = null;
  private shownTrade: number | null = null;
  /** A first Escape arms cancelling until then (performance.now ms). */
  private escapeUntil = 0;

  constructor(private readonly opts: TradeWindowOptions) {
    this.title = h("span", {}, "Trade");
    this.headline = h("p", { class: "trade-headline", role: "status", "aria-live": "polite" });
    this.steps = h("ol", { class: "trade-steps", "aria-label": "Steps" });
    this.reset = h("p", { class: "trade-reset", role: "alert", hidden: true });
    this.mineCol = h("section", { class: "trade-side mine", "aria-label": "You give" });
    this.theirsCol = h("section", { class: "trade-side theirs", "aria-label": "They give" });
    this.actions = h("div", { class: "econ-actions trade-actions" });
    this.error = h("p", { class: "econ-error", role: "alert", hidden: true });
    this.root = h(
      "section",
      { class: "econ-window trade-window panel", role: "dialog", "aria-label": "Trade", hidden: true },
      windowHead("Trade", () => this.cancel()),
      h("h3", { class: "trade-title" }, this.title),
      this.steps,
      this.headline,
      this.reset,
      h("div", { class: "trade-sides" }, this.mineCol, this.theirsCol),
      this.error,
      this.actions,
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** Escape asks to cancel the open trade; a second Escape within a few seconds cancels it. */
  handleKey(e: KeyboardEvent): boolean {
    if (e.key !== "Escape" || !this.isOpen) return false;
    const now = performance.now();
    if (now < this.escapeUntil) {
      this.escapeUntil = 0;
      this.cancel();
    } else {
      this.escapeUntil = now + ESCAPE_CONFIRM_MS;
      this.showError("Press Escape again to cancel the trade.");
    }
    return true;
  }

  /** Redraws from the session (after every trade message). */
  render(): void {
    const s = this.opts.session;
    const v = s.view;
    const status = s.status();
    if (!v || !status) {
      this.hide();
      return;
    }
    const opening = this.root.hidden || this.shownTrade !== v.tradeId;
    if (this.shownTrade !== v.tradeId) {
      this.available.clear();
      this.picker?.dispose();
      this.picker = null;
    }
    this.shownTrade = v.tradeId;
    this.root.hidden = false;
    this.root.dataset.step = status.step;
    this.title.textContent = `Trading with ${v.partnerName}`;
    this.headline.textContent = status.headline;
    this.reset.textContent = s.resetReason ?? "";
    this.reset.hidden = !s.resetReason;
    this.renderSteps(status.step);

    // Keep the Crowns box as typed while it has focus.
    const typing = document.activeElement instanceof HTMLInputElement && this.mineCol.contains(document.activeElement) && document.activeElement.name === "crowns" ? document.activeElement.value : null;
    this.mineCol.replaceChildren(...this.side("You give", v.mine, status.mine, s.changedMine, true, status.canEdit));
    this.theirsCol.replaceChildren(...this.side(`${v.partnerName} gives`, v.theirs, status.theirs, s.changedTheirs, false, false));
    if (typing !== null) {
      const box = this.mineCol.querySelector<HTMLInputElement>('input[name="crowns"]');
      if (box) {
        box.value = typing;
        box.focus();
      }
    }
    if (this.picker && status.canEdit) this.mineCol.append(this.picker.root);
    else if (this.picker) {
      this.picker.dispose();
      this.picker = null;
    }

    const lockButton = status.mine === "editing"
      ? h("button", { type: "button", class: "primary", disabled: !status.canLock, onclick: () => this.lock(true) }, "Lock my offer")
      : h("button", { type: "button", class: "secondary", disabled: !status.canUnlock, onclick: () => this.lock(false) }, "Unlock to change");
    const confirm = h(
      "button",
      { type: "button", class: status.canConfirm ? "primary trade-confirm ready" : "primary trade-confirm", disabled: !status.canConfirm, onclick: () => this.confirm() },
      status.mine === "confirmed" ? "Confirmed ✓" : "Confirm trade",
    );
    this.actions.replaceChildren(lockButton, confirm, h("button", { type: "button", class: "secondary", onclick: () => this.cancel() }, "Cancel trade"));
    if (opening) queueMicrotask(() => this.actions.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus());
  }

  private renderSteps(step: string): void {
    const order = ["editing", "review", "waiting", "completing"];
    const labels = ["Add items", "Lock and review", "Confirm", "Done"];
    const at = order.indexOf(step);
    this.steps.replaceChildren(
      ...labels.map((l, i) => h("li", { class: i < at ? "done" : i === at ? "current" : "", "aria-current": i === at ? "step" : undefined }, l)),
    );
  }

  private side(title: string, offer: TradeOffer, state: SideState, changed: ReadonlySet<string>, mine: boolean, editable: boolean): Node[] {
    const badge = h("span", { class: `trade-badge ${state}` }, state === "locked" ? "🔒 " : state === "confirmed" ? "✓ " : "", SIDE_LABELS[state]);
    const list = h("ul", { class: "trade-offer", "aria-label": title });
    for (const item of offer.items) list.append(this.line(item, changed.has(lineKey(item)), mine && editable));
    if (!offer.items.length) list.append(h("li", { class: "muted-line" }, mine ? "Nothing yet." : "Nothing offered yet."));
    const goldChanged = changed.has("crowns");
    let gold: Node;
    if (mine && editable) {
      const input = wholeInput({ label: "Crowns you give", name: "crowns", value: offer.crowns, oninput: () => this.crownsTyped(input), onenter: () => this.sendCrowns(input) });
      input.addEventListener("blur", () => this.sendCrowns(input));
      gold = h("label", { class: goldChanged ? "trade-gold changed" : "trade-gold" }, "Crowns ", input, h("span", { class: "muted" }, ` of ${formatCrowns(this.opts.balance() ?? 0)}`));
    } else {
      gold = h("p", { class: goldChanged ? "trade-gold changed" : "trade-gold" }, crowns(offer.crowns), goldChanged ? h("span", { class: "changed-tag" }, "changed") : null);
    }
    const nodes: Node[] = [h("header", { class: "trade-side-head" }, h("h4", {}, title), badge), list, gold];
    if (mine && editable) {
      const full = offer.items.length >= this.opts.itemsMax;
      nodes.push(
        h(
          "button",
          { type: "button", class: "secondary", disabled: full, "aria-expanded": String(!!this.picker), onclick: () => this.togglePicker() },
          this.picker ? "Close trunk" : full ? `${this.opts.itemsMax} items is the most` : "Add from trunk…",
        ),
      );
    }
    return nodes;
  }

  private line(item: OfferedItem, changed: boolean, removable: boolean): HTMLElement {
    const row = itemRow({ item_def: item.itemDef, dyes: item.dyes, quantity: item.quantity, maker: item.maker, finish: item.finish }, changed ? h("span", { class: "changed-tag" }, "new") : null);
    return h(
      "li",
      { class: changed ? "changed" : "" },
      row,
      removable ? h("button", { type: "button", class: "link", "aria-label": `Take ${defName(item.itemDef)} back`, onclick: () => this.update(withoutLine(this.opts.session.draft(), item.itemId)) }, "Remove") : null,
    );
  }

  private togglePicker(): void {
    if (this.picker) {
      this.picker.dispose();
      this.picker = null;
    } else {
      this.picker = new ItemPicker({
        label: "Your trunk",
        action: "Offer",
        used: () => new Map(this.opts.session.draft().items.map((l) => [l.itemId, l.quantity])),
        onPick: (item, quantity) => {
          this.available.set(item.id, item.quantity);
          const draft = this.opts.session.draft();
          const already = draft.items.find((l) => l.itemId === item.id)?.quantity ?? 0;
          this.update(withLine(draft, item.id, Math.min(item.quantity, already + quantity)));
        },
      });
      this.picker.reload();
    }
    this.render();
  }

  private crownsTyped(input: HTMLInputElement): void {
    if (this.crownsTimer !== null) clearTimeout(this.crownsTimer);
    this.crownsTimer = setTimeout(() => this.sendCrowns(input), CROWNS_DEBOUNCE_MS);
  }

  private sendCrowns(input: HTMLInputElement): void {
    if (this.crownsTimer !== null) clearTimeout(this.crownsTimer);
    this.crownsTimer = null;
    const text = input.value.trim();
    // Empty means none; anything that is not a whole number is refused by the checks.
    const n = text === "" ? 0 : (parseWhole(text) ?? -1);
    const draft = this.opts.session.draft();
    if (n === draft.crowns) return;
    this.update(withCrowns(draft, n));
  }

  /** Sends our whole offer, if it passes the checks the server makes. */
  private update(draft: DraftOffer): void {
    const s = this.opts.session;
    const v = s.view;
    if (!v) return;
    const problem = offerProblem(draft, this.available, this.opts.balance() ?? 0, this.opts.itemsMax, ECONOMY);
    this.showError(problem);
    if (problem) return;
    this.opts.send(encodeTradeSetOffer(v.tradeId, Float64Array.from(draft.items.map((l) => l.itemId)), Uint32Array.from(draft.items.map((l) => l.quantity)), draft.crowns));
    s.sent("offer");
  }

  private lock(locked: boolean): void {
    const v = this.opts.session.view;
    if (!v) return;
    this.showError(null);
    this.opts.send(encodeTradeLock(v.tradeId, locked));
    this.opts.session.sent(locked ? "lock" : "unlock");
    this.render();
  }

  private confirm(): void {
    const v = this.opts.session.view;
    if (!v) return;
    this.showError(null);
    // Confirms exactly the revision on screen: if it changed meanwhile the server refuses.
    this.opts.send(encodeTradeConfirm(v.tradeId, v.rev));
    this.opts.session.sent("confirm");
    this.render();
  }

  private cancel(): void {
    const v = this.opts.session.view;
    if (!v) {
      this.hide();
      return;
    }
    this.opts.send(encodeTradeCancel(v.tradeId));
  }

  showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  private hide(): void {
    this.root.hidden = true;
    this.shownTrade = null;
    this.picker?.dispose();
    this.picker = null;
    this.showError(null);
    if (this.crownsTimer !== null) clearTimeout(this.crownsTimer);
    this.crownsTimer = null;
  }

  dispose(): void {
    this.hide();
  }
}

/** Trade requests as toasts: theirs (Accept / Decline) and ours (waiting for an answer). */
export class TradeToasts {
  readonly root: HTMLElement;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly session: TradeSession,
    private readonly onAnswer: (tradeId: number, accept: boolean) => void,
    private readonly onLapse: (text: string) => void,
    private readonly onWithdraw: (tradeId: number) => void,
  ) {
    this.root = h("div", { class: "toasts trade-toasts", "aria-live": "polite" });
  }

  /** Redraws the toasts (after a request arrives or ends). */
  render(): void {
    const now = performance.now();
    const s = this.session;
    const els: HTMLElement[] = [];
    for (const r of s.incoming) {
      els.push(
        h(
          "div",
          { class: "toast panel trade-toast", role: "dialog", "aria-label": `Trade request from ${r.fromName}` },
          h("p", {}, h("strong", {}, r.fromName), " would like to trade with you."),
          h(
            "div",
            { class: "toast-actions" },
            h("button", { type: "button", class: "secondary toast-accept", onclick: () => this.onAnswer(r.tradeId, true) }, "Trade"),
            h("button", { type: "button", class: "secondary", onclick: () => this.onAnswer(r.tradeId, false) }, "Decline"),
            h("span", { class: "toast-clock", "data-until": String(r.until) }, formatCountdown(secondsLeft(r.until, now))),
          ),
        ),
      );
    }
    if (s.outgoing) {
      const o = s.outgoing;
      els.push(
        h(
          "div",
          { class: "toast panel trade-toast waiting", role: "status" },
          h("p", {}, "Waiting for ", h("strong", {}, o.targetName), " to answer your trade request…"),
          h(
            "div",
            { class: "toast-actions" },
            // The server echoes our request with its id; only then can it be withdrawn.
            o.tradeId !== null ? h("button", { type: "button", class: "secondary", onclick: () => this.onWithdraw(o.tradeId!) }, "Withdraw") : null,
            h("span", { class: "toast-clock", "data-until": String(o.until) }, formatCountdown(secondsLeft(o.until, now))),
          ),
        ),
      );
    }
    this.root.replaceChildren(...els);
    const any = els.length > 0;
    if (any && this.timer === null) this.timer = setInterval(() => this.tick(), 500);
    if (!any && this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private tick(): void {
    const now = performance.now();
    const gone = this.session.expire(now);
    for (const r of gone.incoming) this.onLapse(`${r.fromName}'s trade request lapsed.`);
    if (gone.outgoing) this.onLapse(`${gone.outgoing.targetName} didn't answer your trade request.`);
    if (gone.incoming.length || gone.outgoing) {
      this.render();
      return;
    }
    for (const c of this.root.querySelectorAll<HTMLElement>(".toast-clock")) c.textContent = formatCountdown(secondsLeft(Number(c.dataset.until), now));
  }

  dispose(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.root.replaceChildren();
  }
}

export interface PlayerMenuAction {
  label: string;
  run: () => void;
}

/** A small menu by the pointer for one player: Trade, Whisper, Write a letter. */
export class PlayerMenu {
  readonly root: HTMLElement;
  private readonly onDocDown = (e: PointerEvent) => {
    if (!this.root.contains(e.target as Node)) this.close();
  };
  private opener: HTMLElement | null = null;

  constructor() {
    this.root = h("div", { class: "player-menu panel", role: "menu", hidden: true, onkeydown: (e: Event) => this.onKey(e as KeyboardEvent) });
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** Opens the menu for `name` at screen point (`x`, `y`), focused on its first item. */
  open(name: string, x: number, y: number, actions: readonly PlayerMenuAction[]): void {
    this.opener = focusOrigin();
    this.root.replaceChildren(
      h("p", { class: "player-menu-name", role: "presentation" }, name),
      ...actions.map((a) =>
        h(
          "button",
          {
            type: "button",
            role: "menuitem",
            class: "player-menu-item",
            onclick: () => {
              this.close();
              a.run();
            },
          },
          a.label,
        ),
      ),
    );
    this.root.hidden = false;
    // Keep it on screen.
    const w = this.root.offsetWidth || 180;
    const hgt = this.root.offsetHeight || 140;
    this.root.style.left = `${Math.max(8, Math.min(x, window.innerWidth - w - 8))}px`;
    this.root.style.top = `${Math.max(8, Math.min(y, window.innerHeight - hgt - 8))}px`;
    this.root.querySelector<HTMLButtonElement>("button")?.focus();
    window.addEventListener("pointerdown", this.onDocDown, true);
  }

  close(): void {
    if (this.root.hidden) return;
    this.root.hidden = true;
    window.removeEventListener("pointerdown", this.onDocDown, true);
    restoreFocus(this.opener, this.root);
    this.opener = null;
  }

  private onKey(e: KeyboardEvent): void {
    const items = [...this.root.querySelectorAll<HTMLButtonElement>("button")];
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      this.close();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      items[(i + step + items.length) % items.length]?.focus();
    }
  }

  dispose(): void {
    this.close();
  }
}

/** Answers a trade request on the wire. */
export function respond(send: (frame: Uint8Array) => void, tradeId: number, accept: boolean): void {
  send(encodeTradeRespond(tradeId, accept));
}
