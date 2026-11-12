// What the open castle's residents say, on this player's screen only
// (docs/architecture.md "NPCs"): the talk card (a real
// conversation, one line per interact), small subtitle lines (a portrait
// noticing us as we pass, a student's bark, a shopkeeper's greeting as the
// shop opens) and the "E Talk to ..." prompt. Text only, no voices; nothing
// here is ever sent.

import "./npc-talk.css";
import { h } from "./dom";

/** The talk card: who speaks (name, title) and the line, with "More (E)" and "Goodbye". */
export class TalkCard {
  readonly root: HTMLElement;
  private readonly name: HTMLElement;
  private readonly title: HTMLElement;
  private readonly line: HTMLElement;
  private readonly more: HTMLButtonElement;
  private npc: string | null = null;

  constructor(private readonly hooks: { onMore: () => void; onClose: () => void }) {
    this.name = h("h2", { class: "npc-talk-name" });
    this.title = h("p", { class: "npc-talk-title" });
    this.line = h("p", { class: "npc-talk-line" });
    this.more = h("button", { type: "button", class: "secondary", onclick: () => this.hooks.onMore() }, h("kbd", {}, "E"), " More") as HTMLButtonElement;
    this.root = h(
      "section",
      { class: "npc-talk panel", role: "region", "aria-label": "Conversation", "aria-live": "polite", hidden: true },
      h("header", {}, this.name, this.title),
      this.line,
      h("div", { class: "npc-talk-actions" }, this.more, h("button", { type: "button", class: "secondary", onclick: () => this.close() }, "Goodbye")),
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** Whom the card shows (null: closed). */
  get speaker(): string | null {
    return this.isOpen ? this.npc : null;
  }

  /** `npc` (display `name` and `title`) says `text`; `last`: the conversation ends with it (no "More"). */
  show(npc: string, name: string, title: string | undefined, text: string, last: boolean): void {
    this.npc = npc;
    this.name.textContent = name;
    this.title.textContent = title ?? "";
    this.title.hidden = !title;
    this.line.textContent = text;
    this.more.hidden = last;
    this.root.hidden = false;
    this.root.classList.remove("pop");
    void this.root.offsetWidth;
    this.root.classList.add("pop");
  }

  close(): void {
    if (this.root.hidden) return;
    this.root.hidden = true;
    this.npc = null;
    this.hooks.onClose();
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.close();
      return true;
    }
    return false;
  }
}

/** Subtitle lines: small text near the bottom of the screen, each up `ms`, the newest last (at most `MAX_SUBTITLES`). */
export const MAX_SUBTITLES = 2;

export class Subtitles {
  readonly root: HTMLElement;
  private readonly lines: { el: HTMLElement; until: number }[] = [];

  constructor() {
    this.root = h("div", { class: "npc-subtitles", "aria-live": "polite" });
  }

  /** `name` says `text` for `ms` (local `performance.now()` time `now`). */
  say(name: string, text: string, ms: number, now: number): void {
    const el = h("p", { class: "npc-subtitle" }, h("span", { class: "npc-subtitle-name" }, name), " ", h("span", {}, text));
    this.root.append(el);
    this.lines.push({ el, until: now + ms });
    while (this.lines.length > MAX_SUBTITLES) this.lines.shift()!.el.remove();
  }

  /** How many lines are up (tests). */
  get count(): number {
    return this.lines.length;
  }

  tick(now: number): void {
    for (let i = this.lines.length - 1; i >= 0; i--) {
      if (now < this.lines[i]!.until) continue;
      this.lines[i]!.el.remove();
      this.lines.splice(i, 1);
    }
  }

  clear(): void {
    for (const l of this.lines) l.el.remove();
    this.lines.length = 0;
  }
}

/** "E Talk to ..." while a resident is in reach and no door or station is (their prompt wins). Clicking it talks. */
export class TalkPrompt {
  readonly root: HTMLButtonElement;
  private readonly label: HTMLElement;
  private shown = "";

  constructor(onTalk: () => void) {
    this.label = h("span", {}, "");
    this.root = h("button", { type: "button", class: "interact-prompt npc-talk-prompt panel", hidden: true, onclick: onTalk }, h("kbd", {}, "E"), this.label) as HTMLButtonElement;
  }

  set(text: string | null): void {
    const t = text ?? "";
    if (t === this.shown) return;
    this.shown = t;
    this.root.hidden = !text;
    this.label.textContent = t;
  }
}
