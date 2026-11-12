// The clubs secretary's desk (docs/architecture.md "Clubs"): a conversation
// card in the residents' style (npc-talk.css) with her line, her three
// options (found a club, tell me about the clubs, goodbye), the charter
// form (name and tag) once she says everything is in order, and the
// school's register when asked. Which line she says is club-state.ts's;
// game/club.ts drives the card. Nothing she says is sent anywhere.

import { h } from "./dom";
import { CLUB_RULES, secretaryLine } from "./club-state";
import "./npc-talk.css";
import "./club.css";

export interface DeskListing {
  name: string;
  tag: string;
  members: number;
}

export interface ClubDeskOptions {
  onFound: () => void;
  onList: () => void;
  /** The charter form was sent: `name` and `tag` as typed. */
  onSeal: (name: string, tag: string) => void;
  onClose: () => void;
}

export class ClubDesk {
  readonly root: HTMLElement;
  private readonly name: HTMLElement;
  private readonly title: HTMLElement;
  private readonly line: HTMLElement;
  private readonly form: HTMLFormElement;
  private readonly nameInput: HTMLInputElement;
  private readonly tagInput: HTMLInputElement;
  private readonly seal: HTMLButtonElement;
  private readonly error: HTMLElement;
  private readonly register: HTMLElement;

  constructor(private readonly opts: ClubDeskOptions) {
    this.name = h("h2", { class: "npc-talk-name" });
    this.title = h("p", { class: "npc-talk-title" });
    this.line = h("p", { class: "npc-talk-line" });
    this.nameInput = h("input", { type: "text", name: "club-name", maxlength: CLUB_RULES.name_chars[1], autocomplete: "off", spellcheck: "false", placeholder: "The Night Owls" }) as HTMLInputElement;
    this.tagInput = h("input", { type: "text", name: "club-tag", class: "club-tag-input", maxlength: CLUB_RULES.tag_chars[1], autocomplete: "off", spellcheck: "false", placeholder: "OWL" }) as HTMLInputElement;
    this.seal = h("button", { type: "submit", class: "secondary" }, `Seal the charter (${CLUB_RULES.found_crowns} Crowns)`) as HTMLButtonElement;
    this.error = h("p", { class: "club-desk-error", role: "alert", hidden: true });
    this.form = h(
      "form",
      { class: "club-desk-form", hidden: true },
      h("label", {}, h("span", {}, `Club name (${CLUB_RULES.name_chars[0]}-${CLUB_RULES.name_chars[1]})`), this.nameInput),
      h("label", {}, h("span", {}, `Tag (${CLUB_RULES.tag_chars[0]}-${CLUB_RULES.tag_chars[1]} letters or digits)`), this.tagInput),
      this.error,
      this.seal,
    ) as HTMLFormElement;
    this.form.addEventListener("submit", (e) => {
      e.preventDefault();
      this.opts.onSeal(this.nameInput.value, this.tagInput.value);
    });
    this.register = h("ul", { class: "club-register", hidden: true, "aria-label": "Clubs on the register" });
    const option = (key: "option_found" | "option_list" | "option_goodbye", run: () => void) => h("button", { type: "button", class: "secondary", onclick: run }, secretaryLine(key));
    this.root = h(
      "section",
      { class: "npc-talk club-desk panel", role: "region", "aria-label": "Clubs and Societies", "aria-live": "polite", hidden: true },
      h("header", {}, this.name, this.title),
      this.line,
      this.form,
      this.register,
      h("div", { class: "npc-talk-actions club-desk-options" }, option("option_found", () => this.opts.onFound()), option("option_list", () => this.opts.onList()), option("option_goodbye", () => this.close())),
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** Opens on her greeting. */
  open(name: string, title: string | undefined): void {
    this.name.textContent = name;
    this.title.textContent = title ?? "";
    this.title.hidden = !title;
    this.root.hidden = false;
    this.say(secretaryLine("greeting"));
  }

  /** She says `text`; the form and the register go away. */
  say(text: string): void {
    this.line.textContent = text;
    this.form.hidden = true;
    this.register.hidden = true;
    this.root.classList.remove("pop");
    void this.root.offsetWidth;
    this.root.classList.add("pop");
  }

  /** The charter form (after her "everything is in order" line). */
  showForm(): void {
    this.form.hidden = false;
    this.error.hidden = true;
    this.seal.disabled = false;
    this.nameInput.focus();
  }

  /** The form was sent: wait for the answer. */
  sealing(): void {
    this.seal.disabled = true;
    this.error.hidden = true;
  }

  /** The charter was refused: why, and the form again. */
  formError(text: string): void {
    this.seal.disabled = false;
    this.error.textContent = text;
    this.error.hidden = false;
  }

  /** Whether the form is up waiting for an answer. */
  get awaitingSeal(): boolean {
    return this.isOpen && !this.form.hidden && this.seal.disabled;
  }

  /** The register, or a note when it is empty or could not be read. */
  showRegister(clubs: readonly DeskListing[] | null): void {
    this.form.hidden = true;
    this.register.hidden = false;
    if (clubs === null) {
      this.register.replaceChildren(h("li", { class: "muted" }, "The register can't be read just now."));
      return;
    }
    if (clubs.length === 0) {
      this.register.replaceChildren(h("li", { class: "muted" }, "No clubs on the register yet."));
      return;
    }
    this.register.replaceChildren(
      ...clubs.map((c) => h("li", {}, h("span", { class: "club-register-name" }, c.name), h("span", { class: "club-tag" }, `[${c.tag}]`), h("span", { class: "muted" }, c.members === 1 ? "1 member" : `${c.members} members`))),
    );
  }

  close(): void {
    if (this.root.hidden) return;
    this.root.hidden = true;
    this.opts.onClose();
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.close();
      return true;
    }
    return false;
  }
}
