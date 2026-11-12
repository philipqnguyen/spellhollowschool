// Owl mail (N, or the owl on the HUD): the mailbox a page at a time, reading
// a letter (which marks it read), collecting what came with it, throwing it
// away, and writing a letter with items and Crowns for a small postage.
// Mail can be read anywhere: an owl brings it.
//
// The Outbox lists the letters the player sent, newest first, with where
// each stands (delivered, read, collected), and shows one read-only. After a
// letter goes, a banner says so ("Owl Mail is on the way") and the window
// returns to the Mailbox; a refused letter stays in the form with the reason.
//
// The unread count comes from `MailNotice` (after Welcome and whenever it
// changes); when it goes up an owl swoops in to the HUD button. Requests go
// over HTTP (economy/api.ts); the rules are in economy/mail-state.ts.

import "../economy/economy.css";
import "./mail.css";
import { audio } from "../audio";
import * as econApi from "../economy/api";
import type { Mail, MailDetail, SentMail, SentMailDetail } from "../economy/api";
import { owlIcon } from "../economy/icons";
import { defName, econDef, stackLimit, type EconItem } from "../economy/items";
import { charCount, emptyDraft, enclosureText, hasEnclosures, letterBody, letterCost, letterProblem, MailIndicator, mailDate, mailError, MAIL_KIND_LABELS, sentEnclosureText, sentStatus, type LetterDraft } from "../economy/mail-state";
import { ECONOMY, formatCrowns } from "../economy/money";
import { isRetryable, KeysetPager, RetryKeys } from "../economy/paging";
import { errorMessage, crowns, ItemPicker, itemRow, pagerNav, tabBar, wholeInput, windowHead } from "../economy/view";
import { ApiError } from "../api";
import { wearLabel } from "../content/catalog";
import { focusOrigin, h, mount, restoreFocus } from "./dom";

export interface MailWindowOptions {
  myName: string;
  /** The purse as last reported (null before the first report). */
  balance: () => number | null;
  /** A reply carried a new balance. */
  onBalance: (crowns: number) => void;
  /** Names to suggest as recipients (friends, people nearby). */
  knownNames: () => Iterable<string>;
  /** A line for the chat log. */
  notify: (text: string) => void;
  /** Where the owl flies: the HUD layer. */
  layer: HTMLElement;
}

type Tab = "inbox" | "sent" | "write";

const PAGE_SIZE = 25;
/** A destructive button asks once more before acting (ms the second chance lasts). */
const CONFIRM_MS = 3000;
/** How long the "on the way" banner stays (ms). */
const SENT_BANNER_MS = 6000;

export class MailWindow {
  readonly root: HTMLElement;
  /** The HUD button with the unread badge. */
  readonly button: HTMLButtonElement;
  private readonly badge: HTMLElement;
  private readonly announce: HTMLElement;
  private readonly tabs: { el: HTMLElement; select: (t: Tab) => void };
  private readonly listPane: HTMLElement;
  private readonly readPane: HTMLElement;
  private readonly inbox: HTMLElement;
  private readonly compose: HTMLElement;
  private readonly outbox: HTMLElement;
  private readonly sentListPane: HTMLElement;
  private readonly sentReadPane: HTMLElement;
  private readonly error: HTMLElement;
  private readonly banner: HTMLElement;
  private bannerTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly indicator = new MailIndicator();
  private readonly pager = new KeysetPager<Mail>();
  private readonly sentPager = new KeysetPager<SentMail>();
  /** A letter went since the Outbox was last fetched. */
  private sentStale = false;
  private readonly keys = new RetryKeys();
  private tab: Tab = "inbox";
  private open: MailDetail | null = null;
  private opening: number | null = null;
  private sentOpen: SentMailDetail | null = null;
  private sentOpening: number | null = null;
  private confirmDelete: { id: number; until: number } | null = null;
  private draft: LetterDraft = emptyDraft();
  /** Attached items as chosen (for names and icons). */
  private readonly attached = new Map<number, EconItem>();
  private picker: ItemPicker | null = null;
  private busy = false;
  private disposed = false;
  private opener: HTMLElement | null = null;

  constructor(private readonly opts: MailWindowOptions) {
    this.badge = h("span", { class: "badge", hidden: true });
    this.button = h("button", { type: "button", class: "icon-button hud-mail", "aria-label": "Owl mail", "aria-expanded": "false", title: "Owl mail (N)", onclick: () => this.toggle() }, owlIcon(), this.badge);
    this.announce = h("span", { class: "sr-only", role: "status", "aria-live": "polite" });
    this.button.append(this.announce);
    this.tabs = tabBar<Tab>("Mail", [
      { id: "inbox", label: "Mailbox" },
      { id: "sent", label: "Outbox" },
      { id: "write", label: "Write a letter" },
    ], (t) => this.showTab(t));
    this.listPane = h("div", { class: "mail-list-pane" });
    this.readPane = h("div", { class: "mail-read", "aria-live": "polite" });
    this.inbox = h("div", { class: "mail-inbox", role: "tabpanel" }, this.listPane, this.readPane);
    this.sentListPane = h("div", { class: "mail-list-pane" });
    this.sentReadPane = h("div", { class: "mail-read", "aria-live": "polite" });
    this.outbox = h("div", { class: "mail-inbox mail-outbox", role: "tabpanel", hidden: true }, this.sentListPane, this.sentReadPane);
    this.compose = h("form", { class: "mail-compose", role: "tabpanel", hidden: true, onsubmit: (e: Event) => (e.preventDefault(), void this.send()) });
    this.error = h("p", { class: "econ-error", role: "alert", hidden: true });
    this.banner = h("div", { class: "mail-sent-banner", role: "status", "aria-live": "polite", hidden: true });
    this.root = h(
      "section",
      { class: "econ-window mail-window panel", role: "dialog", "aria-label": "Owl mail", hidden: true },
      windowHead("Owl mail", () => this.close()),
      this.tabs.el,
      this.banner,
      this.error,
      this.inbox,
      this.outbox,
      this.compose,
    );
    this.tabs.select("inbox");
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = !this.isOpen): void {
    if (open === this.isOpen) return;
    this.root.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    if (open) {
      this.opener = focusOrigin();
      this.showTab(this.tab);
      if (this.tab === "inbox") void this.move("first");
      queueMicrotask(() => this.root.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus());
    } else {
      this.picker?.dispose();
      this.picker = null;
      restoreFocus(this.opener, this.root);
      this.opener = null;
    }
  }

  close(): void {
    this.toggle(false);
  }

  /** N toggles the window (M is the map); Escape closes it. */
  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.code === "KeyN") {
      this.toggle();
      return true;
    }
    if (e.key === "Escape" && this.isOpen) {
      this.close();
      return true;
    }
    return false;
  }

  /** Opens the letter form addressed to `name` (a reply, or the player menu). */
  writeTo(name: string, subject = ""): void {
    this.draft = { ...this.draft, to: name, subject: subject || this.draft.subject };
    this.tab = "write";
    this.toggle(true);
    this.showTab("write");
  }

  // ---------------------------------------------------------------- the indicator

  /** `MailNotice`. */
  notice(unread: number): void {
    const arrived = this.indicator.notice(unread);
    const badge = this.indicator.badge();
    this.badge.textContent = badge;
    this.badge.hidden = !badge;
    this.button.setAttribute("aria-label", unread > 0 ? `Owl mail, ${unread} unread` : "Owl mail");
    if (arrived) {
      this.announce.textContent = "An owl has arrived with new mail.";
      this.flyOwl();
      // Keep the open mailbox current.
      if (this.isOpen && this.tab === "inbox" && this.pager.page === 0) void this.move("reload");
    }
  }

  /** A new connection: its first `MailNotice` reports the count again. */
  reconnected(): void {
    this.indicator.reconnected();
  }

  /** An owl swoops in from the right and lands on the mail button. */
  private flyOwl(): void {
    const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    const land = () => {
      this.button.classList.remove("owl-landed");
      void this.button.offsetWidth;
      this.button.classList.add("owl-landed");
    };
    const target = this.button.getBoundingClientRect();
    if (reduced || target.width === 0 || typeof this.button.animate !== "function") {
      land();
      return;
    }
    const owl = h("div", { class: "owl-flight", "aria-hidden": "true" }, owlIcon("owl-flight-icon"));
    this.opts.layer.append(owl);
    const w = window.innerWidth;
    const hgt = window.innerHeight;
    const tx = target.left + target.width / 2 - 20;
    const ty = target.top + target.height / 2 - 20;
    const anim = owl.animate(
      [
        { transform: `translate(${w + 40}px, ${hgt * 0.45}px) scale(1.5) rotate(-8deg)`, opacity: 0 },
        { transform: `translate(${w * 0.72}px, ${hgt * 0.22}px) scale(1.4) rotate(6deg)`, opacity: 1, offset: 0.35 },
        { transform: `translate(${tx + 60}px, ${ty + 40}px) scale(1.1) rotate(-4deg)`, opacity: 1, offset: 0.75 },
        { transform: `translate(${tx}px, ${ty}px) scale(0.55) rotate(0deg)`, opacity: 0.2 },
      ],
      { duration: 1700, easing: "cubic-bezier(.45,.05,.4,1)" },
    );
    anim.onfinish = () => {
      owl.remove();
      land();
    };
  }

  // ---------------------------------------------------------------- inbox

  private showTab(tab: Tab): void {
    this.tab = tab;
    this.tabs.select(tab);
    this.inbox.hidden = tab !== "inbox";
    this.outbox.hidden = tab !== "sent";
    this.compose.hidden = tab !== "write";
    this.showError(null);
    if (tab === "inbox") {
      if (!this.pager.loaded && this.isOpen) void this.move("first");
      this.renderList();
      this.renderRead();
    } else if (tab === "sent") {
      // Fetched each time it is shown: the recipients' read and collect change it.
      if (this.isOpen) void this.moveSent(!this.sentPager.loaded || this.sentStale ? "first" : "reload");
      this.renderSentList();
      this.renderSentRead();
    } else {
      this.renderCompose();
    }
  }

  private async move(move: "first" | "next" | "prev" | "reload"): Promise<void> {
    const req = this.pager.begin(move);
    if (!req) return;
    this.renderList();
    try {
      const page = await econApi.mailbox(req.after, PAGE_SIZE);
      if (this.disposed || !this.pager.receive(req.ticket, page)) return;
      this.notice(page.unread);
    } catch (e) {
      if (this.disposed || !this.pager.fail(req.ticket)) return;
      this.showError(errorMessage(e, mailError));
    }
    this.renderList();
  }

  private renderList(): void {
    const p = this.pager;
    const list = h("ul", { class: "mail-list", "aria-label": "Letters" });
    if (!p.loaded) list.append(h("li", { class: "muted-line" }, p.loading ? "The owls are sorting your post…" : ""));
    else if (!p.items.length) list.append(h("li", { class: "muted-line" }, "No letters. Owls bring purchases, sales and letters from friends here."));
    for (const m of p.items) {
      const selected = this.open?.id === m.id || this.opening === m.id;
      const enclosed = enclosureText(m);
      list.append(
        h(
          "li",
          {},
          h(
            "button",
            {
              type: "button",
              class: m.read ? "mail-row" : "mail-row unread",
              "aria-pressed": String(selected),
              "aria-label": `${m.read ? "" : "Unread. "}${MAIL_KIND_LABELS[m.kind]} from ${m.sender_name}: ${m.subject}. ${enclosed}`,
              onclick: () => void this.openMail(m),
            },
            h("span", { class: "mail-dot", "aria-hidden": "true" }),
            h("span", { class: "mail-from" }, m.sender_name),
            h("span", { class: "mail-date" }, mailDate(m.created_at)),
            h("span", { class: "mail-subject" }, m.subject),
            h("span", { class: "mail-kind" }, MAIL_KIND_LABELS[m.kind], enclosed ? h("span", { class: hasEnclosures(m) ? "mail-enclosed" : "mail-collected" }, ` · ${enclosed}`) : null),
          ),
        ),
      );
    }
    this.listPane.replaceChildren(list, p.hasPrev || p.hasNext ? pagerNav(p, (mv) => void this.move(mv), "Mailbox") : h("span", {}));
  }

  private async openMail(m: Mail): Promise<void> {
    this.opening = m.id;
    this.confirmDelete = null;
    this.renderList();
    this.renderRead();
    try {
      const detail = await econApi.readMail(m.id);
      if (this.disposed || this.opening !== m.id) return;
      this.open = detail;
      this.pager.update((x) => x.id === m.id, (x) => ({ ...x, read: true }));
    } catch (e) {
      if (this.opening === m.id) this.showError(errorMessage(e, mailError));
    } finally {
      if (this.opening === m.id) this.opening = null;
      this.renderList();
      this.renderRead();
    }
  }

  private renderRead(): void {
    const m = this.open;
    if (this.opening !== null) {
      this.readPane.replaceChildren(h("p", { class: "muted-line" }, "Unfolding the letter…"));
      return;
    }
    if (!m) {
      this.readPane.replaceChildren(h("p", { class: "muted-line mail-placeholder" }, "Choose a letter to read it."));
      return;
    }
    const enclosed = hasEnclosures(m);
    const confirming = this.confirmDelete?.id === m.id && performance.now() < this.confirmDelete.until;
    const items = m.items.length ? h("ul", { class: "mail-items", "aria-label": "Enclosed items" }, ...m.items.map((i) => h("li", {}, itemRow(i)))) : null;
    const actions = h(
      "div",
      { class: "econ-actions" },
      enclosed ? h("button", { type: "button", class: "primary", disabled: this.busy, onclick: () => void this.collect(m) }, "Collect") : null,
      m.kind === "letter" && m.sender_id !== null ? h("button", { type: "button", class: "secondary", onclick: () => this.writeTo(m.sender_name, m.subject.startsWith("Re: ") ? m.subject : `Re: ${m.subject}`.slice(0, ECONOMY.mail.subject_max_chars)) }, "Reply") : null,
      h(
        "button",
        { type: "button", class: confirming ? "danger" : "secondary", disabled: this.busy || enclosed, title: enclosed ? "Collect what came with it first" : undefined, onclick: () => void this.remove(m) },
        confirming ? "Really throw away?" : "Throw away",
      ),
    );
    mount(
      this.readPane,
      h("h3", { class: "mail-title" }, m.subject),
      h("p", { class: "mail-meta" }, `${MAIL_KIND_LABELS[m.kind]} from `, h("strong", {}, m.sender_name), ` · ${new Date(m.created_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}`),
      m.body ? h("div", { class: "mail-body" }, m.body) : null,
      items || m.crowns > 0 ? h("div", { class: enclosed ? "mail-enclosure" : "mail-enclosure collected" }, h("p", { class: "detail-label" }, enclosed ? "Enclosed" : "Collected"), items, m.crowns > 0 ? crowns(m.crowns) : null) : null,
      actions,
    );
  }

  private async collect(m: MailDetail): Promise<void> {
    await this.act(async () => {
      // The reply lists the stacks the items merged into and the balance
      // after; what came with the letter is what the letter says.
      const r = await econApi.collectMail(m.id);
      audio.play("mail.collect");
      this.opts.onBalance(r.crowns);
      const names = m.items.map((i) => `${i.quantity > 1 ? `${defName(i.item_def)} ×${i.quantity}` : defName(i.item_def)}${wearLabel(i.item_def) ? ` (${wearLabel(i.item_def)})` : ""}`);
      if (m.crowns > 0) names.push(`${formatCrowns(m.crowns)} Crowns`);
      this.opts.notify(names.length ? `Collected: ${names.join(", ")}.` : "Collected.");
      if (this.open?.id === m.id) this.open = { ...this.open, collected: true };
      this.pager.update((x) => x.id === m.id, (x) => ({ ...x, collected: true }));
    });
  }

  private async remove(m: MailDetail): Promise<void> {
    const now = performance.now();
    if (!this.confirmDelete || this.confirmDelete.id !== m.id || now > this.confirmDelete.until) {
      this.confirmDelete = { id: m.id, until: now + CONFIRM_MS };
      this.renderRead();
      return;
    }
    this.confirmDelete = null;
    await this.act(async () => {
      await econApi.deleteMail(m.id);
      this.open = null;
      this.pager.remove((x) => x.id === m.id);
      // The page may now be short; ask for it again so the list stays full.
      void this.move("reload");
    });
  }

  // ---------------------------------------------------------------- outbox

  private async moveSent(move: "first" | "next" | "prev" | "reload"): Promise<void> {
    const req = this.sentPager.begin(move);
    if (!req) return;
    if (move === "first") this.sentStale = false;
    this.renderSentList();
    try {
      const page = await econApi.sentMail(req.after, PAGE_SIZE);
      if (this.disposed || !this.sentPager.receive(req.ticket, page)) return;
      // The letter open keeps its text and items; its status is the list's.
      const open = this.sentOpen;
      const fresh = open && page.items.find((x) => x.id === open.id);
      if (open && fresh) {
        this.sentOpen = { ...open, ...fresh };
        this.renderSentRead();
      }
    } catch (e) {
      if (this.disposed || !this.sentPager.fail(req.ticket)) return;
      this.showError(errorMessage(e, mailError));
    }
    this.renderSentList();
  }

  private renderSentList(): void {
    const p = this.sentPager;
    const list = h("ul", { class: "mail-list", "aria-label": "Sent letters" });
    if (!p.loaded) list.append(h("li", { class: "muted-line" }, p.loading ? "Looking through your sent letters…" : ""));
    else if (!p.items.length) list.append(h("li", { class: "muted-line" }, "No letters sent yet. Letters you send by owl are listed here."));
    for (const m of p.items) {
      const selected = this.sentOpen?.id === m.id || this.sentOpening === m.id;
      const enclosed = sentEnclosureText(m);
      const status = sentStatus(m);
      list.append(
        h(
          "li",
          {},
          h(
            "button",
            {
              type: "button",
              class: "mail-row sent",
              "aria-pressed": String(selected),
              "aria-label": `To ${m.recipient_name}: ${m.subject}. ${enclosed ? `Sent with ${enclosed}. ` : ""}${status}.`,
              onclick: () => void this.openSent(m),
            },
            h("span", { class: "mail-dot", "aria-hidden": "true" }),
            h("span", { class: "mail-from" }, `To ${m.recipient_name}`),
            h("span", { class: "mail-date" }, mailDate(m.created_at)),
            h("span", { class: "mail-subject" }, m.subject),
            h("span", { class: "mail-kind" }, h("span", { class: `mail-status ${statusClass(status)}` }, status), enclosed ? h("span", { class: "mail-enclosed" }, ` · ${enclosed}`) : null),
          ),
        ),
      );
    }
    this.sentListPane.replaceChildren(list, p.hasPrev || p.hasNext ? pagerNav(p, (mv) => void this.moveSent(mv), "Outbox") : h("span", {}));
  }

  private async openSent(m: SentMail): Promise<void> {
    this.sentOpening = m.id;
    this.renderSentList();
    this.renderSentRead();
    try {
      const detail = await econApi.readSentMail(m.id);
      if (this.disposed || this.sentOpening !== m.id) return;
      this.sentOpen = detail;
    } catch (e) {
      if (this.sentOpening === m.id) this.showError(errorMessage(e, mailError));
    } finally {
      if (this.sentOpening === m.id) this.sentOpening = null;
      this.renderSentList();
      this.renderSentRead();
    }
  }

  private renderSentRead(): void {
    const m = this.sentOpen;
    if (this.sentOpening !== null) {
      this.sentReadPane.replaceChildren(h("p", { class: "muted-line" }, "Unfolding the letter…"));
      return;
    }
    if (!m) {
      this.sentReadPane.replaceChildren(h("p", { class: "muted-line mail-placeholder" }, "Choose a sent letter to see it."));
      return;
    }
    const status = sentStatus(m);
    const items = m.items.length ? h("ul", { class: "mail-items", "aria-label": "Items sent" }, ...m.items.map((i) => h("li", {}, itemRow(i)))) : null;
    mount(
      this.sentReadPane,
      h("h3", { class: "mail-title" }, m.subject),
      h(
        "p",
        { class: "mail-meta" },
        "To ",
        h("strong", {}, m.recipient_name),
        ` · ${new Date(m.created_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })} · `,
        h("span", { class: `mail-status ${statusClass(status)}` }, status),
      ),
      m.body ? h("div", { class: "mail-body" }, m.body) : null,
      items || m.crowns > 0 ? h("div", { class: "mail-enclosure collected" }, h("p", { class: "detail-label" }, "Sent with it"), items, m.crowns > 0 ? crowns(m.crowns) : null) : null,
      h("div", { class: "econ-actions" }, h("button", { type: "button", class: "secondary", onclick: () => this.writeTo(m.recipient_name, m.subject) }, "Write again")),
    );
  }

  // ---------------------------------------------------------------- the sent banner

  /** "Owl Mail is on the way", for a while, whichever tab is shown. */
  private showSentBanner(to: string): void {
    if (this.bannerTimer !== null) clearTimeout(this.bannerTimer);
    mount(
      this.banner,
      owlIcon("mail-sent-owl"),
      h("div", { class: "mail-sent-text" }, h("strong", {}, "Owl Mail is on the way"), h("span", {}, `Your letter to ${to} has flown.`)),
      h("button", { type: "button", class: "link", onclick: () => (this.hideSentBanner(), this.showTab("sent")) }, "See it in the Outbox"),
    );
    this.banner.hidden = false;
    this.bannerTimer = setTimeout(() => this.hideSentBanner(), SENT_BANNER_MS);
  }

  private hideSentBanner(): void {
    if (this.bannerTimer !== null) clearTimeout(this.bannerTimer);
    this.bannerTimer = null;
    this.banner.hidden = true;
  }

  // ---------------------------------------------------------------- writing

  private renderCompose(): void {
    const d = this.draft;
    const rules = ECONOMY.mail;
    const balance = this.opts.balance() ?? 0;
    const names = h("datalist", { id: "mail-names" }, ...[...new Set(this.opts.knownNames())].slice(0, 50).map((n) => h("option", { value: n })));
    const to = h("input", { type: "text", name: "to", autocomplete: "off", list: "mail-names", maxlength: 64, value: d.to, required: true, oninput: () => ((this.draft.to = to.value), this.renderSummary()) });
    const subject = h("input", { type: "text", name: "subject", autocomplete: "off", maxlength: rules.subject_max_chars * 2, value: d.subject, required: true, oninput: () => ((this.draft.subject = subject.value), this.renderSummary()) });
    const bodyCount = h("span", { class: "field-count", "aria-live": "off" }, `${charCount(d.body)} / ${rules.body_max_chars}`);
    const body = h("textarea", {
      name: "body",
      rows: 6,
      oninput: () => {
        this.draft.body = body.value;
        bodyCount.textContent = `${charCount(body.value)} / ${rules.body_max_chars}`;
        this.renderSummary();
      },
    });
    body.value = d.body;
    const gold = wholeInput({ label: "Crowns to enclose", name: "crowns", value: d.crowns || undefined, placeholder: "0", oninput: (n) => {
        // Empty means none; anything else that is not a whole number shows as a problem.
        this.draft.crowns = n ?? (gold.value.trim() === "" ? 0 : -1);
        this.renderSummary();
      },
    });

    const attachments = h("ul", { class: "mail-attachments", "aria-label": "Attached items" });
    for (const line of d.items) {
      const item = this.attached.get(line.itemId);
      if (!item) continue;
      attachments.append(
        h(
          "li",
          {},
          itemRow({ ...item, quantity: line.quantity }),
          h("button", { type: "button", class: "link", "aria-label": `Remove ${defName(item.item_def)}`, onclick: () => this.detach(line.itemId) }, "Remove"),
        ),
      );
    }
    const full = d.items.length >= rules.attachments_max;
    const pickerSlot = h("div", { class: "mail-picker" });
    if (this.picker) pickerSlot.append(this.picker.root);
    const attach = h(
      "button",
      { type: "button", class: "secondary", disabled: full, "aria-expanded": String(!!this.picker), onclick: () => this.togglePicker() },
      this.picker ? "Close trunk" : full ? `${rules.attachments_max} items attached (the most an owl carries)` : "Attach from trunk…",
    );

    this.compose.replaceChildren(
      names,
      h("label", { class: "econ-field" }, h("span", {}, "To"), to),
      h("label", { class: "econ-field" }, h("span", {}, "Subject"), subject),
      h("label", { class: "econ-field" }, h("span", {}, "Letter ", bodyCount), body),
      h("div", { class: "econ-field" }, h("span", {}, `Enclosures (up to ${rules.attachments_max} items)`), attachments, attach, pickerSlot),
      h("label", { class: "econ-field inline" }, h("span", {}, "Crowns"), gold, h("span", { class: "muted" }, ` you have ${formatCrowns(balance)}`)),
      h("div", { class: "mail-summary", "aria-live": "polite" }),
      h("div", { class: "econ-actions" }, h("button", { type: "submit", class: "primary", disabled: this.busy }, "Send by owl"), h("button", { type: "button", class: "secondary", onclick: () => this.clearDraft() }, "Clear")),
    );
    this.renderSummary();
  }

  /** The cost line and what is wrong, updated as the player types. */
  private renderSummary(): void {
    const slot = this.compose.querySelector<HTMLElement>(".mail-summary");
    if (!slot) return;
    const balance = this.opts.balance() ?? 0;
    const cost = letterCost({ crowns: Math.max(0, this.draft.crowns) });
    const problem = letterProblem(this.draft, this.opts.myName, balance);
    const postage = ECONOMY.mail.postage;
    mount(
      slot,
      h(
        "p",
        {},
        `Postage ${formatCrowns(postage)} C`,
        this.draft.crowns > 0 ? ` + ${formatCrowns(this.draft.crowns)} C enclosed` : "",
        cost !== null ? ` = ${formatCrowns(cost)} C` : "",
        cost !== null && cost <= balance ? h("span", { class: "muted" }, ` · you'll have ${formatCrowns(balance - cost)} C left`) : null,
      ),
      problem && (this.draft.to || this.draft.subject) ? h("p", { class: "econ-hint" }, problem) : null,
    );
  }

  private togglePicker(): void {
    if (this.picker) {
      this.picker.dispose();
      this.picker = null;
    } else {
      this.picker = new ItemPicker({
        label: "Your trunk",
        action: "Attach",
        used: () => new Map(this.draft.items.map((l) => [l.itemId, l.quantity])),
        onPick: (item, quantity) => this.attach(item, quantity),
      });
      this.picker.reload();
    }
    this.renderCompose();
  }

  private attach(item: EconItem, quantity: number): void {
    const max = ECONOMY.mail.attachments_max;
    const lines = this.draft.items.filter((l) => l.itemId !== item.id);
    if (lines.length >= max) {
      this.showError(`An owl can carry at most ${max} items.`);
      return;
    }
    const q = Math.min(quantity, item.quantity, stackLimit(econDef(item.item_def)));
    this.attached.set(item.id, item);
    this.draft = { ...this.draft, items: [...lines, { itemId: item.id, quantity: q }] };
    if (this.draft.items.length >= max) {
      this.picker?.dispose();
      this.picker = null;
    }
    this.renderCompose();
    this.picker?.render();
  }

  private detach(itemId: number): void {
    this.draft = { ...this.draft, items: this.draft.items.filter((l) => l.itemId !== itemId) };
    this.renderCompose();
    this.picker?.render();
  }

  private clearDraft(): void {
    this.draft = emptyDraft();
    this.attached.clear();
    this.keys.settle("letter");
    this.picker?.dispose();
    this.picker = null;
    this.showError(null);
    this.renderCompose();
  }

  private async send(): Promise<void> {
    const d = this.draft;
    const problem = letterProblem(d, this.opts.myName, this.opts.balance() ?? 0);
    if (problem) {
      this.showError(problem);
      return;
    }
    await this.act(
      async () => {
        const r = await econApi.sendLetter({
          to: d.to.trim(),
          subject: d.subject.trim(),
          body: letterBody(d),
          items: d.items.length ? d.items.map((l) => ({ item_id: l.itemId, quantity: l.quantity })) : undefined,
          crowns: d.crowns > 0 ? d.crowns : undefined,
          request_id: this.keys.get("letter"),
        });
        this.keys.settle("letter");
        audio.play("mail.send");
        this.opts.onBalance(r.crowns);
        const to = d.to.trim();
        this.opts.notify(`Your owl is on its way to ${to}.`);
        this.clearDraft();
        if (this.disposed) return;
        // Back to the Mailbox, with the banner; the Outbox fetches again when next shown.
        this.sentStale = true;
        this.showTab("inbox");
        this.showSentBanner(to);
      },
      (e) => {
        // A refusal is final: the next try is a new letter. A lost answer is retried as the same one.
        if (!(e instanceof ApiError && isRetryable(e.status))) this.keys.settle("letter");
        // The form keeps what was written; the reason shows above it.
        audio.play("ui.error");
      },
    );
  }

  // ---------------------------------------------------------------- helpers

  private async act(fn: () => Promise<void>, onError?: (e: unknown) => void): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.showError(null);
    this.root.setAttribute("aria-busy", "true");
    this.rerender();
    try {
      await fn();
    } catch (e) {
      onError?.(e);
      if (!this.disposed) this.showError(errorMessage(e, mailError));
    } finally {
      this.busy = false;
      this.root.removeAttribute("aria-busy");
      if (!this.disposed) this.rerender();
    }
  }

  private rerender(): void {
    if (this.tab === "inbox") {
      this.renderList();
      this.renderRead();
    } else if (this.tab === "sent") {
      this.renderSentList();
      this.renderSentRead();
    } else {
      // Keep focus and typed text: only the buttons' state changes.
      for (const b of this.compose.querySelectorAll<HTMLButtonElement>('button[type="submit"]')) b.disabled = this.busy;
    }
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  dispose(): void {
    this.disposed = true;
    this.picker?.dispose();
    if (this.bannerTimer !== null) clearTimeout(this.bannerTimer);
  }
}

/** A CSS class for a sent letter's status ("Read, not collected" → `read-not-collected`). */
function statusClass(status: string): string {
  return status.toLowerCase().replace(/[^a-z]+/g, "-");
}
