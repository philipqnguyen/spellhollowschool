// The Dueling Club outside a duel: the badge that shows while we stand in
// the club's area (how to challenge, Watch duels, Loadout, spells the club
// just taught us), and the challenge toasts (a challenge to us with Accept
// and Decline, ours with Withdraw, each with its countdown). State lives in
// game/dueling/challenges.ts.

import "./invites.css";
import "./duel.css";
import { ChallengeBook, secondsLeft } from "../game/dueling/challenges";
import { spellName } from "../game/dueling/content";
import { formatCountdown } from "./invites-state";
import { h } from "./dom";

export interface ClubBadgeOptions {
  onWatch: () => void;
  onLoadout: () => void;
}

/** The "you are at the Dueling Club" panel. */
export class ClubBadge {
  readonly root: HTMLElement;
  private readonly title: HTMLElement;
  private readonly news: HTMLElement;
  private shownName = "";

  constructor(opts: ClubBadgeOptions) {
    this.title = h("h3", {});
    this.news = h("p", { class: "duel-new", hidden: true });
    this.root = h(
      "aside",
      { class: "duel-club panel", "aria-label": "Dueling Club", hidden: true },
      this.title,
      h("p", {}, "Right-click someone here and choose ", h("strong", {}, "Challenge to a duel"), ", or type ", h("code", { class: "duel-cmd" }, "/duel Name"), "."),
      this.news,
      h(
        "div",
        { class: "duel-club-actions" },
        h("button", { type: "button", class: "secondary", onclick: opts.onWatch }, "Watch duels"),
        h("button", { type: "button", class: "secondary", onclick: opts.onLoadout }, "Loadout"),
      ),
    );
  }

  get shown(): boolean {
    return !this.root.hidden;
  }

  /** Shows the badge for the area named `name`, or hides it (null). */
  show(name: string | null): void {
    this.root.hidden = name === null;
    if (name !== null && name !== this.shownName) {
      this.shownName = name;
      this.title.textContent = `⚔ ${name}`;
    }
    if (name === null) this.news.hidden = true;
  }

  /** The club taught us `spells`: say so on the badge. */
  learned(spells: readonly number[]): void {
    if (!spells.length) return;
    this.news.textContent = `New spells: ${spells.map(spellName).join(", ")}. Choose which to carry in your Loadout.`;
    this.news.hidden = false;
  }
}

export interface DuelToastsOptions {
  onAnswer: (challengeId: number, accept: boolean) => void;
  onWithdraw: (challengeId: number) => void;
  /** A challenge lapsed while shown (a line for the chat). */
  onLapse: (text: string) => void;
}

/** The challenge toasts, under the trade and invitation toasts' column. */
export class DuelToasts {
  readonly root: HTMLElement;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly book: ChallengeBook,
    private readonly opts: DuelToastsOptions,
  ) {
    this.root = h("div", { class: "toasts duel-toasts", "aria-live": "polite" });
  }

  render(): void {
    const now = performance.now();
    const els: HTMLElement[] = [];
    for (const c of this.book.incoming) {
      els.push(
        h(
          "div",
          { class: "toast panel duel-toast", role: "dialog", "aria-label": `Duel challenge from ${c.fromName}` },
          h("p", {}, "⚔ ", h("strong", {}, c.fromName), h("span", { class: "duel-level" }, `Lv ${c.fromLevel}`), " challenges you to a duel."),
          h(
            "div",
            { class: "toast-actions" },
            h("button", { type: "button", class: "secondary toast-accept", onclick: () => this.opts.onAnswer(c.challengeId, true) }, "Accept"),
            h("button", { type: "button", class: "secondary", onclick: () => this.opts.onAnswer(c.challengeId, false) }, "Decline"),
            h("span", { class: "toast-clock", "data-until": String(c.until) }, formatCountdown(secondsLeft(c.until, now))),
          ),
        ),
      );
    }
    const o = this.book.outgoing;
    if (o) {
      els.push(
        h(
          "div",
          { class: "toast panel duel-toast waiting", role: "status" },
          h("p", {}, "Waiting for ", h("strong", {}, o.targetName), " to accept your duel…"),
          h(
            "div",
            { class: "toast-actions" },
            // The echo names our challenge; only then can it be withdrawn.
            o.challengeId !== null ? h("button", { type: "button", class: "secondary", onclick: () => this.opts.onWithdraw(o.challengeId!) }, "Withdraw") : null,
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
    const lapsed = this.book.incoming.filter((c) => c.until <= now);
    if (this.book.expire(now)) {
      for (const c of lapsed) this.opts.onLapse(`${c.fromName}'s challenge lapsed.`);
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
