// A player's card (Examine in the players list, a
// double-click on a character, or E with a player selected). Name and
// house and level, every subject's skill level, and what they
// wear: their figure in a small 3D view (the wardrobe's mirror,
// `CharacterPreview`) and the pieces by slot, with the wear labels. Add
// friend, Whisper and Challenge to a duel reuse what exists (game.ts). The
// words are in players-state.ts.
//
// Spells to level 20: on the card of a friend, party member or club member,
// a "Cast on <name>" row of the learned spells that can be aimed at them
// (Colour Charm, Binding Curse, Summoning Charm, Unweaving, Slowing Jinx); the
// cast goes through the spells' own rules.

import "./players.css";
import { ApiError, authedRequest } from "../api";
import { houseInfo } from "../content/houses";
import type { CharacterPreview } from "../game/characters/preview";
import { errorMessage, windowHead } from "../economy/view";
import { h } from "./dom";
import { castLabel, type SpellDef } from "../game/spells/data";
import { ACTION_LABELS, cardPath, cardSubtitle, parseCard, playersError, sameName, skillRows, wornRows, type PlayerAction, type PlayerCard } from "./players-state";

export interface PlayerCardOptions {
  myName: string;
  /** A subject's name ("History of Magic"). */
  subjectName: (id: string) => string;
  /** Whether `name` is already a friend (the Add friend button says so). */
  isFriend: (name: string) => boolean;
  /** Spells L20: whether spells may be cast on `name`: a friend, party member or club member (the server decides). */
  isAlly?: (name: string) => boolean;
  /** Whether `name` is in our party (the card's "Invite to party" then says so). */
  isPartyMember?: (name: string) => boolean;
  /** Whether we may invite to our club (an officer or its owner), and whether `name` is in it. */
  canClubInvite?: () => boolean;
  isClubMember?: (name: string) => boolean;
  onAction: (action: PlayerAction, name: string) => void;
  /** Spells L20: the learned spells that can be cast on an ally (none: no row). */
  castable?: () => readonly SpellDef[];
  /** Spells L20: cast `spell` on the character called `name`. */
  onCast?: (spell: SpellDef, name: string) => void;
}

export class PlayerCardWindow {
  readonly root: HTMLElement;
  private readonly title: HTMLElement;
  private readonly body: HTMLElement;
  private readonly error: HTMLElement;
  private card: PlayerCard | null = null;
  private preview: CharacterPreview | null = null;
  /** Bumped on every open, so a slow answer for an earlier name is dropped. */
  private generation = 0;
  private disposed = false;

  constructor(private readonly opts: PlayerCardOptions) {
    this.title = h("span", {}, "Player");
    this.body = h("div", { class: "card-body" });
    this.error = h("p", { class: "econ-error", role: "alert", hidden: true });
    const head = windowHead("Player", () => this.close());
    head.querySelector("h2")?.replaceChildren(this.title);
    this.root = h("section", { class: "econ-window player-card panel", role: "dialog", "aria-label": "Player card", hidden: true }, head, this.error, this.body);
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** The name shown (null while closed). */
  get shownName(): string | null {
    return this.isOpen ? (this.card?.name ?? this.title.textContent) : null;
  }

  /** Opens `name`'s card (reloads it when it is open already). */
  async open(name: string): Promise<void> {
    const gen = ++this.generation;
    this.root.hidden = false;
    this.title.textContent = name;
    this.setError(null);
    if (!this.card || !sameName(this.card.name, name)) {
      this.stopPreview();
      this.card = null;
      this.body.replaceChildren(h("p", { class: "muted-line" }, "Asking after them…"));
    }
    try {
      const card = parseCard(await authedRequest<unknown>("GET", cardPath(name)));
      if (gen !== this.generation || this.disposed || !this.isOpen) return;
      this.card = card;
      this.render();
    } catch (e) {
      if (gen !== this.generation || this.disposed) return;
      this.body.replaceChildren();
      this.setError(e instanceof ApiError ? playersError(e.code, e.message) : errorMessage(e));
    }
  }

  close(): void {
    if (!this.isOpen) return;
    this.generation++;
    this.root.hidden = true;
    this.stopPreview();
    this.card = null;
  }

  /** Esc closes the card. */
  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.close();
      return true;
    }
    return false;
  }

  /** The friend list changed (Add friend done): redraw the buttons. */
  refresh(): void {
    if (this.isOpen && this.card) this.renderActions();
  }

  private setError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  private actions = h("div", { class: "econ-actions card-actions" });

  private render(): void {
    const c = this.card;
    if (!c) return;
    const house = houseInfo(c.house);
    this.title.textContent = c.name;
    this.root.style.setProperty("--house", house.colour);
    const skills = skillRows(c, this.opts.subjectName);
    const worn = wornRows(c.appearance);
    const canvas = h("canvas", { class: "card-canvas", "aria-label": `${c.name}'s outfit: drag to turn` });
    const note = h("p", { class: "card-note muted" });
    this.body.replaceChildren(
      h("p", { class: "card-sub" }, h("span", { class: "players-crest", "aria-hidden": "true" }), cardSubtitle(c)),
      ...(c.club ? [h("p", { class: "card-club" }, h("span", { class: "club-tag" }, `[${c.club.tag}]`), " ", c.club.name)] : []),
      h(
        "div",
        { class: "card-cols" },
        h("div", { class: "card-figure" }, c.appearance ? canvas : null, note),
        h(
          "div",
          { class: "card-facts" },
          h("h3", {}, "Skills"),
          skills.length ? h("ul", { class: "card-skills" }, ...skills.map((s) => h("li", {}, h("span", {}, s.name), h("strong", {}, `Level ${s.level}`)))) : h("p", { class: "muted" }, "No skills yet."),
          h("h3", {}, "Wearing"),
          worn.length ? h("ul", { class: "card-worn" }, ...worn.map((w) => h("li", {}, h("span", { class: "card-slot" }, w.label), h("span", {}, w.name, w.wear ? h("span", { class: "wear-tag" }, w.wear) : null)))) : h("p", { class: "muted" }, "Nothing to see."),
        ),
      ),
      this.actions,
    );
    this.renderActions();
    if (c.appearance) void this.startPreview(canvas, note, c);
    else note.textContent = "No outfit to show.";
  }

  private renderActions(): void {
    const c = this.card;
    if (!c) return;
    if (sameName(c.name, this.opts.myName)) {
      this.actions.replaceChildren(h("p", { class: "muted" }, "This is you."));
      return;
    }
    const friend = this.opts.isFriend(c.name);
    const button = (a: PlayerAction, cls: string, disabled = false, label = ACTION_LABELS[a]) => h("button", { type: "button", class: cls, disabled, onclick: () => this.opts.onAction(a, c.name) }, label);
    const member = this.opts.isPartyMember?.(c.name) ?? false;
    this.actions.replaceChildren(
      button("add_friend", "secondary", friend, friend ? "Friends" : ACTION_LABELS.add_friend),
      button("whisper", "secondary"),
      button("party_invite", "secondary", member, member ? "In your party" : ACTION_LABELS.party_invite),
      ...(this.opts.canClubInvite?.() ? [this.clubButton(button, c)] : []),
      button("challenge", "secondary"),
    );
    // Spells L20: friends, party and club members only (the spells aimed at someone else are).
    const ally = this.opts.isAlly?.(c.name) ?? friend;
    const spells = ally ? (this.opts.castable?.() ?? []) : [];
    if (spells.length && this.opts.onCast) {
      const cast = this.opts.onCast;
      this.actions.append(
        h(
          "div",
          { class: "card-cast", role: "group", "aria-label": `Cast on ${c.name}`, style: "flex-basis: 100%; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 4px" },
          h("span", { class: "card-cast-label muted" }, `Cast on ${c.name}`),
          ...spells.map((sp) => h("button", { type: "button", class: "secondary", title: `${castLabel(sp, { lit: false, guardian: false })}: ${sp.incantation}`, onclick: () => cast(sp, c.name) }, sp.name)),
        ),
      );
    }
  }

  /** "Invite to club", or why not (already in ours, in another). */
  private clubButton(button: (a: PlayerAction, cls: string, disabled?: boolean, label?: string) => HTMLElement, c: PlayerCard): HTMLElement {
    if (this.opts.isClubMember?.(c.name)) return button("club_invite", "secondary", true, "In your club");
    if (c.club) return button("club_invite", "secondary", true, `In ${c.club.name}`);
    return button("club_invite", "secondary");
  }

  private async startPreview(canvas: HTMLCanvasElement, note: HTMLElement, c: PlayerCard): Promise<void> {
    const gen = this.generation;
    try {
      const { CharacterPreview } = await import("../game/characters/preview");
      if (gen !== this.generation || this.disposed || !this.isOpen || !c.appearance) return;
      this.stopPreview();
      this.preview = new CharacterPreview(canvas, c.appearance);
    } catch (e) {
      console.error(e);
      canvas.remove();
      note.textContent = "Their outfit is listed on the right (no 3D view here).";
    }
  }

  private stopPreview(): void {
    this.preview?.dispose();
    this.preview = null;
  }

  dispose(): void {
    this.disposed = true;
    this.stopPreview();
  }
}
