// The party list at the HUD's top left (docs/architecture.md "Parties"):
// every member, ourselves included, in the list's fixed order (the leader
// first, marked with a crown), with their level and where they are; members
// elsewhere are dimmed with their place, offline ones greyed. Clicking a row
// offers what can be done: whisper, and for the leader make leader or
// remove; on our own row, leave. Rows come from party-state.ts.

import { houseInfo } from "../content/houses";
import { h } from "./dom";
import { lineIcon } from "./effects-icons";
import type { PartyRow } from "./party-state";
import "./party.css";

/** A small crown: the leader's mark. */
const CROWN_ICON = ["M4 17h16", "M4 17 3 8l5 4 4-6 4 6 5-4-1 9"];

export interface PartyPanelOptions {
  onWhisper: (name: string) => void;
  onLeave: () => void;
  onKick: (characterId: number, name: string) => void;
  onPromote: (characterId: number, name: string) => void;
}

export class PartyPanel {
  readonly root: HTMLElement;
  private readonly list: HTMLOListElement;
  private readonly count: HTMLElement;
  /** The row whose actions are open (by character id). */
  private open: number | null = null;
  private rows: readonly PartyRow[] = [];
  private max = 6;
  private shown = "";

  constructor(private readonly opts: PartyPanelOptions) {
    this.count = h("span", { class: "party-count" });
    this.list = h("ol", { class: "party-members" });
    this.root = h("section", { class: "party-list panel", "aria-label": "Your party", hidden: true }, h("header", { class: "party-head" }, h("span", {}, "Party"), this.count), this.list);
  }

  /** Shows `rows` (none: the list hides). Redraws only when something shown changed. */
  update(rows: readonly PartyRow[], max: number): void {
    const key = JSON.stringify(rows) + `|${this.open}`;
    if (key === this.shown) return;
    this.shown = key;
    this.rows = rows;
    this.max = max;
    if (this.open !== null && !rows.some((r) => r.characterId === this.open)) this.open = null;
    this.root.hidden = rows.length === 0;
    this.count.textContent = `${rows.length}/${max}`;
    const meLeads = rows.some((r) => r.self && r.leader);
    this.list.replaceChildren(...rows.map((r) => this.row(r, meLeads)));
  }

  private row(r: PartyRow, meLeads: boolean): HTMLElement {
    const house = houseInfo(r.house);
    const status = r.status === "offline" ? "Offline" : r.status === "elsewhere" ? r.where : null;
    const title = [r.name, r.leader ? "leader" : null, `level ${r.level}`, house.name, r.status === "offline" ? "offline" : r.where].filter(Boolean).join(" · ");
    const li = h(
      "li",
      { class: `party-member party-${r.status}${r.leader ? " party-leader" : ""}${r.self ? " party-self" : ""}`, style: `--member-house: ${house.colour}` },
      h(
        "button",
        {
          type: "button",
          class: "party-row",
          title,
          "aria-expanded": String(this.open === r.characterId),
          onclick: () => this.toggle(r.characterId),
        },
        r.leader ? lineIcon(CROWN_ICON, "party-crown") : h("span", { class: "party-crown-gap" }),
        h("span", { class: "party-name" }, r.name),
        h("span", { class: "party-level" }, `Lv ${r.level}`),
      ),
      status ? h("span", { class: "party-where" }, status) : null,
    );
    if (this.open === r.characterId) li.append(this.actions(r, meLeads));
    return li;
  }

  private actions(r: PartyRow, meLeads: boolean): HTMLElement {
    const act = (label: string, run: () => void) =>
      h(
        "button",
        {
          type: "button",
          class: "link-button",
          onclick: () => {
            this.open = null;
            this.shown = "";
            run();
            this.update(this.rows, this.max);
          },
        },
        label,
      );
    if (r.self) return h("div", { class: "party-actions" }, act("Leave party", () => this.opts.onLeave()));
    return h(
      "div",
      { class: "party-actions" },
      r.status !== "offline" ? act("Whisper", () => this.opts.onWhisper(r.name)) : null,
      meLeads && r.status !== "offline" ? act("Make leader", () => this.opts.onPromote(r.characterId, r.name)) : null,
      meLeads ? act("Remove", () => this.opts.onKick(r.characterId, r.name)) : null,
    );
  }

  private toggle(characterId: number): void {
    this.open = this.open === characterId ? null : characterId;
    this.shown = "";
    this.update(this.rows, this.max);
  }
}
