// The dueling loadout editor: which learned dueling spells to carry into a
// duel and on which keys (1-5). The level opens slots (3 at level 1, 4 at
// 5, 5 at 10: data/progression.json), never stronger spells. The learned
// spells (up to ten) are grouped by kind, each with its glyph
// (spell-icons.ts), its numbers and a line
// on how it is countered; the ones still to learn say where and from which
// level. Click a spell
// to put it in or take it out, the arrows to change its key, then Save
// (`PUT /api/loadout`; the server answers with a new `Spellbook`). Draft
// and rules: loadout-state.ts.

import "../economy/economy.css";
import "./loadout.css";
import { ApiError, authedRequest } from "../api";
import { counterHint, kindLabel, SLOT_STEPS, spellSummary, venue, type DuelSpellInfo } from "../game/dueling/content";
import { focusOrigin, h, restoreFocus } from "./dom";
import { byKind, LoadoutDraft, taughtWhere, type Spellbook } from "./loadout-state";
import { spellIcon } from "./spell-icons";

export interface LoadoutWindowOptions {
  notify: (text: string) => void;
}

const MAX_KEYS = 5;

export class LoadoutWindow {
  readonly root: HTMLElement;
  readonly button: HTMLButtonElement;
  private readonly draft = new LoadoutDraft();
  private readonly body: HTMLElement;
  private readonly error: HTMLElement;
  private level: number | null = null;
  private busy = false;
  private hasBook = false;
  private opener: HTMLElement | null = null;

  constructor(private readonly opts: LoadoutWindowOptions) {
    this.button = h("button", { type: "button", class: "icon-button loadout-button", "aria-label": "Dueling loadout", "aria-expanded": "false", title: "Dueling loadout (/loadout)", onclick: () => this.toggle() }, "⚔");
    this.body = h("div", { class: "loadout-body" });
    this.error = h("p", { class: "econ-error", role: "alert", hidden: true });
    this.root = h(
      "section",
      { class: "econ-window loadout-window panel", role: "dialog", "aria-label": "Dueling loadout", hidden: true },
      h("header", { class: "econ-head" }, h("h2", {}, "Dueling loadout"), h("button", { type: "button", class: "icon-button", "aria-label": "Close the loadout", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
      this.body,
      this.error,
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = !this.isOpen): void {
    if (open === this.isOpen) return;
    if (open) this.opener = focusOrigin();
    this.root.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    this.showError(null);
    if (open) this.render();
    else {
      // Unsaved changes are dropped on closing (the spellbook stays the truth).
      this.draft.revert();
      restoreFocus(this.opener, this.root);
      this.opener = null;
    }
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  /** `Spellbook`: what we know, our loadout and slots. */
  spellbook(book: Spellbook): void {
    this.hasBook = true;
    this.draft.adopt(book);
    if (this.isOpen) this.render();
  }

  /** `Progress`: our level (for the slot steps). */
  setLevel(level: number): void {
    if (level === this.level) return;
    this.level = level;
    if (this.isOpen) this.render();
  }

  /** The spells we carry, in key order (the server's word). */
  get loadout(): readonly number[] {
    return this.draft.saved;
  }

  private render(): void {
    const d = this.draft;
    if (!this.hasBook) {
      this.body.replaceChildren(h("p", { class: "muted" }, "Your spellbook is on its way…"));
      return;
    }
    const next = SLOT_STEPS.find((s) => s.slots > d.slots);
    const club = venue("dueling_club")?.name ?? "the Dueling Club";
    const head = h(
      "p",
      { class: "loadout-intro" },
      this.level !== null ? `Level ${this.level}: ` : "",
      `${d.slots} slot${d.slots === 1 ? "" : "s"}. `,
      next ? `${next.slots} at level ${next.level}. ` : "",
      "Levels open slots; spells never get stronger.",
    );

    const slotEls: HTMLElement[] = [];
    const shownSlots = Math.max(d.slots, Math.min(MAX_KEYS, SLOT_STEPS.reduce((m, s) => Math.max(m, s.slots), 0)));
    for (let i = 0; i < shownSlots; i++) {
      if (i >= d.slots) {
        const opens = SLOT_STEPS.find((s) => s.slots > i);
        slotEls.push(h("li", { class: "loadout-slot locked" }, h("kbd", { class: "loadout-key" }, String(i + 1)), h("span", { class: "loadout-slot-name" }, opens ? `Opens at level ${opens.level}` : "Locked")));
        continue;
      }
      const id = d.spells[i];
      const def = id !== undefined ? d.known().find((s) => s.id === id) : undefined;
      if (!def) {
        slotEls.push(h("li", { class: "loadout-slot empty" }, h("kbd", { class: "loadout-key" }, String(i + 1)), h("span", { class: "loadout-slot-name muted" }, "Empty: pick a spell below")));
        continue;
      }
      slotEls.push(
        h(
          "li",
          { class: "loadout-slot", style: `--spell: ${def.colour}` },
          h("kbd", { class: "loadout-key" }, String(i + 1)),
          spellIcon(def),
          h("span", { class: "loadout-slot-name" }, def.name),
          h(
            "span",
            { class: "loadout-slot-tools" },
            h("button", { type: "button", class: "icon-button", "aria-label": `Move ${def.name} to key ${i}`, disabled: i === 0, onclick: () => this.edit(() => d.move(i, -1)) }, "‹"),
            h("button", { type: "button", class: "icon-button", "aria-label": `Move ${def.name} to key ${i + 2}`, disabled: i >= d.spells.length - 1, onclick: () => this.edit(() => d.move(i, 1)) }, "›"),
            h("button", { type: "button", class: "icon-button", "aria-label": `Take ${def.name} out`, onclick: () => this.edit(() => d.remove(def.id)) }, "×"),
          ),
        ),
      );
    }

    const card = (s: DuelSpellInfo, known: boolean) => {
      const inIt = d.has(s.id);
      return h(
        "li",
        {},
        h(
          "button",
          {
            type: "button",
            class: `loadout-spell${inIt ? " chosen" : ""}${known ? "" : " unknown"}`,
            style: `--spell: ${s.colour}`,
            disabled: !known || this.busy,
            "aria-pressed": known ? String(inIt) : undefined,
            onclick: () => this.edit(() => this.showError(d.toggle(s.id))),
          },
          h("span", { class: "loadout-spell-head" }, spellIcon(s), h("strong", {}, s.name), h("span", { class: "loadout-kind" }, kindLabel(s.kind)), inIt ? h("span", { class: "loadout-in" }, `key ${d.spells.indexOf(s.id) + 1}`) : null),
          h("span", { class: "loadout-spell-line" }, known ? spellSummary(s) : `Taught ${taughtWhere(s.unlockAt)} from level ${s.unlockLevel}.`),
          known && counterHint(s) ? h("span", { class: "loadout-spell-hint" }, counterHint(s)) : null,
        ),
      );
    };
    // Grouped by kind (Shield, Attack, Disarm, Bind, Hinder, Summon, Counter, Ward).
    const grouped = (spells: readonly DuelSpellInfo[], known: boolean) =>
      h(
        "div",
        { class: "loadout-groups" },
        ...byKind(spells).map((g) => h("section", { class: "loadout-group", "aria-label": g.label }, h("h4", { class: "loadout-group-h" }, g.label), h("ul", { class: "loadout-spells" }, ...g.spells.map((s) => card(s, known))))),
      );

    const known = d.known();
    const unknown = d.unknown();
    const problem = d.problem();
    const warning = d.warning();
    this.body.replaceChildren(
      head,
      h("ol", { class: "loadout-slots", "aria-label": "Your loadout, in key order" }, ...slotEls),
      h("h3", { class: "loadout-h" }, `Your dueling spells (${known.length})`),
      known.length ? grouped(known, true) : h("p", { class: "muted" }, `You haven't learned any dueling spells yet. Step into ${club} (the stage in the Great Hall) to learn them.`),
      unknown.length ? h("h3", { class: "loadout-h" }, "Not learned yet") : "",
      unknown.length ? h("ul", { class: "loadout-spells loadout-unknown" }, ...unknown.map((s) => card(s, false))) : "",
      problem ? h("p", { class: "econ-error" }, problem) : warning ? h("p", { class: "loadout-warning" }, warning) : "",
      h("p", { class: "muted loadout-note" }, "A duel already under way keeps the loadout it started with."),
      h(
        "div",
        { class: "econ-actions" },
        h("button", { type: "button", class: "secondary", disabled: !d.dirty || this.busy, onclick: () => this.edit(() => d.revert()) }, "Undo changes"),
        h("button", { type: "button", class: "primary", disabled: !d.dirty || !!problem || this.busy, onclick: () => void this.save() }, this.busy ? "Saving…" : "Save loadout"),
      ),
    );
  }

  private edit(change: () => void): void {
    if (this.busy) return;
    this.showError(null);
    change();
    this.render();
  }

  private async save(): Promise<void> {
    const d = this.draft;
    if (this.busy || d.problem()) return;
    this.busy = true;
    this.render();
    const spells = [...d.spells];
    try {
      const r = await authedRequest<{ loadout: number[]; slots: number }>("PUT", "/loadout", { spells });
      // The Spellbook message follows; take the answer now so the window settles at once.
      d.adopt({ learned: d.learned, loadout: r.loadout, slots: r.slots });
      d.revert();
      this.opts.notify("Loadout saved.");
    } catch (e) {
      this.showError(e instanceof ApiError ? (e.code === "invalid_loadout" ? e.message : e.code === "not_implemented" ? "Loadouts can't be saved on this server yet." : e.message) : "Couldn't reach the castle. Please try again.");
    } finally {
      this.busy = false;
      if (this.isOpen) this.render();
    }
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  dispose(): void {
    this.root.remove();
    this.button.remove();
  }
}
