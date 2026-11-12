// The club window (J, or the club button on the menu bar; docs/architecture.md
// "Clubs"): the club's name and tag, who is online of how many, the pinned
// message of the day (officers and the owner edit it), inviting by name
// (officers and the owner), and the roster: every member with their rank,
// level and whether they are online. Clicking a row offers what our rank
// allows: whisper, appoint or dismiss an officer, hand the club over, remove;
// on our own row, leave. The owner renames and disbands the club; handing
// it over, disbanding and the owner's leaving ask once more. Rows and
// permissions come from club-state.ts; game/club.ts sends what is asked.

import { houseInfo } from "../content/houses";
import { windowHead } from "../economy/view";
import type { ClubView } from "../wasm/sim_wasm";
import { h } from "./dom";
import { canInvite, canSetMotd, CLUB_RULES, clubRows, isOwner, myRole, onlineCount, ROLE_LABELS, rowActions, type ClubRow } from "./club-state";
import "./club.css";

export interface ClubPanelOptions {
  myCharacterId: number;
  /** Who founds clubs, and where (the empty window says so). */
  secretary: string;
  onInvite: (name: string) => void;
  onWhisper: (name: string) => void;
  onLeave: () => void;
  onKick: (characterId: number, name: string) => void;
  onSetRole: (characterId: number, name: string, role: "officer" | "member") => void;
  onTransfer: (characterId: number, name: string) => void;
  onRename: (name: string, tag: string) => void;
  onSetMotd: (motd: string) => void;
  onDisband: () => void;
}

/** What waits for a second click: handing over to someone, disbanding, leaving. */
type Confirm = { kind: "transfer"; characterId: number; name: string } | { kind: "disband" } | { kind: "leave" } | null;

export class ClubPanel {
  readonly root: HTMLElement;
  /** The menu bar's button. */
  readonly button: HTMLButtonElement;
  private readonly body: HTMLElement;
  private view: ClubView | null = null;
  /** The row whose actions are open (by character id). */
  private open: number | null = null;
  private confirm: Confirm = null;
  private editingMotd = false;
  private renaming = false;
  private shown = "";

  constructor(private readonly opts: ClubPanelOptions) {
    this.button = h("button", { type: "button", class: "icon-button hud-club", "aria-label": "Club", "aria-expanded": "false", title: "Club (J)", onclick: () => this.toggle() }, "⚑") as HTMLButtonElement;
    this.body = h("div", { class: "club-body" });
    this.root = h("section", { class: "econ-window club-window panel", role: "dialog", "aria-label": "Club", hidden: true }, windowHead("Club", () => this.toggle(false)), this.body);
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = this.root.hidden): void {
    this.root.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    if (open) this.render(true);
  }

  /** J opens and closes the window. */
  handleKey(e: KeyboardEvent): boolean {
    if (e.code !== "KeyJ" || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return false;
    this.toggle();
    return true;
  }

  /** The club as it is now (null: in no club). */
  update(view: ClubView | null): void {
    if (this.view?.id !== view?.id) {
      this.open = null;
      this.confirm = null;
      this.editingMotd = false;
      this.renaming = false;
    }
    this.view = view;
    this.button.classList.toggle("in-club", view !== null);
    this.render();
  }

  private render(force = false): void {
    if (this.root.hidden) return;
    // Never redraw under a field being typed in.
    const key = JSON.stringify([this.view, this.open, this.confirm, this.editingMotd, this.renaming]);
    if (!force && key === this.shown) return;
    if (!force && (this.editingMotd || this.renaming) && this.root.contains(document.activeElement) && document.activeElement instanceof HTMLInputElement) return;
    this.shown = key;
    const view = this.view;
    if (!view) {
      this.body.replaceChildren(
        h("p", { class: "club-empty" }, "You're not in a club."),
        h("p", { class: "muted" }, `Clubs are founded at the desk of ${this.opts.secretary}, in the Entrance Hall (level ${CLUB_RULES.found_level}, ${CLUB_RULES.found_crowns} Crowns), or join one when an officer invites you.`),
      );
      return;
    }
    const me = myRole(view, this.opts.myCharacterId);
    const { online, total } = onlineCount(view);
    const parts: (HTMLElement | null)[] = [
      h("div", { class: "club-title" }, h("h3", {}, view.name), h("span", { class: "club-tag" }, `[${view.tag}]`), h("span", { class: "club-count muted" }, `${online} online · ${total}/${CLUB_RULES.members_max} members`)),
      this.motd(view.motd, canSetMotd(me)),
      canInvite(me) ? this.inviteRow() : null,
      h("ol", { class: "club-roster", "aria-label": "Members" }, ...clubRows(view, this.opts.myCharacterId).map((r) => this.row(r, me))),
      isOwner(me) ? this.ownerTools(view) : null,
      this.confirmBar(),
    ];
    this.body.replaceChildren(...parts.filter((p): p is HTMLElement => p !== null));
  }

  private motd(text: string, editable: boolean): HTMLElement {
    if (this.editingMotd && editable) {
      const input = h("input", { type: "text", class: "club-motd-input", maxlength: CLUB_RULES.motd_chars_max, value: text, "aria-label": "Message of the day", placeholder: "Pinned for every member" }) as HTMLInputElement;
      const save = () => {
        this.editingMotd = false;
        this.opts.onSetMotd(input.value.trim());
        this.render(true);
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") save();
      });
      queueMicrotask(() => input.focus());
      return h(
        "div",
        { class: "club-motd editing" },
        input,
        h("button", { type: "button", class: "secondary", onclick: save }, "Pin"),
        h("button", { type: "button", class: "link-button", onclick: () => ((this.editingMotd = false), this.render(true)) }, "Cancel"),
      );
    }
    return h(
      "div",
      { class: "club-motd" },
      h("span", { class: "club-motd-label" }, "Message of the day"),
      h("p", { class: text ? "club-motd-text" : "club-motd-text muted" }, text || "None pinned."),
      editable ? h("button", { type: "button", class: "link-button", onclick: () => ((this.editingMotd = true), this.render(true)) }, text ? "Edit" : "Pin a message") : null,
    );
  }

  private inviteRow(): HTMLElement {
    const input = h("input", { type: "text", class: "club-invite-input", maxlength: 24, placeholder: "Character name", "aria-label": "Invite a character by name" }) as HTMLInputElement;
    const send = () => {
      const name = input.value.trim();
      if (!name) return;
      this.opts.onInvite(name);
      input.value = "";
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") send();
    });
    return h("div", { class: "club-invite" }, input, h("button", { type: "button", class: "secondary", onclick: send }, "Invite"));
  }

  private row(r: ClubRow, me: ReturnType<typeof myRole>): HTMLElement {
    const house = houseInfo(r.house);
    const li = h(
      "li",
      { class: `club-member${r.online ? "" : " club-offline"}${r.self ? " club-self" : ""}`, style: `--member-house: ${house.colour}` },
      h(
        "button",
        { type: "button", class: "club-row", "aria-expanded": String(this.open === r.characterId), title: `${r.name} · ${ROLE_LABELS[r.role]} · level ${r.level} · ${house.name} · ${r.online ? "online" : "offline"}`, onclick: () => this.toggleRow(r.characterId) },
        h("span", { class: `club-dot${r.online ? " on" : ""}`, "aria-hidden": "true" }),
        h("span", { class: "club-name" }, r.name),
        h("span", { class: `club-role club-role-${r.role}` }, ROLE_LABELS[r.role]),
        h("span", { class: "club-level" }, `Lv ${r.level}`),
      ),
    );
    if (this.open === r.characterId) li.append(this.actions(r, me));
    return li;
  }

  private actions(r: ClubRow, me: ReturnType<typeof myRole>): HTMLElement {
    const act = (label: string, run: () => void) =>
      h(
        "button",
        {
          type: "button",
          class: "link-button",
          onclick: () => {
            this.open = null;
            run();
            this.render(true);
          },
        },
        label,
      );
    const labels = {
      whisper: () => act("Whisper", () => this.opts.onWhisper(r.name)),
      promote: () => act("Make officer", () => this.opts.onSetRole(r.characterId, r.name, "officer")),
      demote: () => act("Dismiss as officer", () => this.opts.onSetRole(r.characterId, r.name, "member")),
      transfer: () => act("Make president", () => (this.confirm = { kind: "transfer", characterId: r.characterId, name: r.name })),
      kick: () => act("Remove from club", () => this.opts.onKick(r.characterId, r.name)),
      leave: () => act("Leave club", () => (isOwner(me) ? (this.confirm = { kind: "leave" }) : this.opts.onLeave())),
    };
    return h("div", { class: "club-actions" }, ...rowActions(r, me).map((a) => labels[a]()));
  }

  private ownerTools(view: ClubView): HTMLElement {
    if (this.renaming) {
      const name = h("input", { type: "text", maxlength: CLUB_RULES.name_chars[1], value: view.name, "aria-label": "Club name" }) as HTMLInputElement;
      const tag = h("input", { type: "text", class: "club-tag-input", maxlength: CLUB_RULES.tag_chars[1], value: view.tag, "aria-label": "Tag" }) as HTMLInputElement;
      const save = () => {
        this.renaming = false;
        this.opts.onRename(name.value, tag.value);
        this.render(true);
      };
      return h(
        "div",
        { class: "club-owner renaming" },
        h("label", {}, h("span", {}, "Name"), name),
        h("label", {}, h("span", {}, "Tag"), tag),
        h("button", { type: "button", class: "secondary", onclick: save }, "Rename"),
        h("button", { type: "button", class: "link-button", onclick: () => ((this.renaming = false), this.render(true)) }, "Cancel"),
      );
    }
    return h(
      "div",
      { class: "club-owner" },
      h("button", { type: "button", class: "secondary", onclick: () => ((this.renaming = true), this.render(true)) }, "Rename club"),
      h("button", { type: "button", class: "secondary danger", onclick: () => ((this.confirm = { kind: "disband" }), this.render(true)) }, "Disband club"),
    );
  }

  private confirmBar(): HTMLElement | null {
    const c = this.confirm;
    if (!c) return null;
    const text =
      c.kind === "transfer"
        ? `Make ${c.name} the club's president? You will be an officer.`
        : c.kind === "disband"
          ? "Disband the club for good? Every member leaves it, and its name and tag are freed."
          : "Leave the club? The longest-serving officer (or member) becomes its president.";
    const yes = c.kind === "transfer" ? "Yes, hand it over" : c.kind === "disband" ? "Yes, disband" : "Yes, leave";
    const run = () => {
      this.confirm = null;
      if (c.kind === "transfer") this.opts.onTransfer(c.characterId, c.name);
      else if (c.kind === "disband") this.opts.onDisband();
      else this.opts.onLeave();
      this.render(true);
    };
    return h(
      "div",
      { class: "club-confirm", role: "alertdialog", "aria-label": "Confirm" },
      h("p", {}, text),
      h("button", { type: "button", class: "secondary danger", onclick: run }, yes),
      h("button", { type: "button", class: "link-button", onclick: () => ((this.confirm = null), this.render(true)) }, "Cancel"),
    );
  }

  private toggleRow(characterId: number): void {
    this.open = this.open === characterId ? null : characterId;
    this.render(true);
  }
}
