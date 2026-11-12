// The spellbook (Y, or the level badge in the HUD bar): your level and
// school year, then every spell of data/spells.json by school (Charms,
// Transfiguration, Defence and dueling), learned or locked. A learned spell
// shows its incantation, its wand gesture (drawn from the gesture's
// template), what it does outside a duel and, for dueling spells, in one,
// and a button to cast it (its words from `castLabel`: "Light (L)",
// "Recolour", "Summon guardian"...); spells aimed at a friend say how to
// aim them. A locked one says the level it needs and where it is taught.
// Dueling spells in your loadout are marked; the loadout itself is set at
// the Dueling Club. The model is spellbook-state.ts.
//
// The social hotbar (ui/hotbar.ts): each spell that can go on it has its
// glyph by the Cast button, to drag onto a hotbar slot (or click: the first
// empty slot); a flourish-only or locked spell's glyph won't move. Rows on
// the bar say which key casts them.

import "./spellbook.css";
import type { Progress } from "../wasm/sim_wasm";
import { gestureByKey, spellRgb, type GestureDef, type SpellDef } from "../game/spells/data";
import { focusOrigin, h, restoreFocus } from "./dom";
import { lineIcon, SPELLBOOK_ICON } from "./effects-icons";
import { levelFraction, xpText, yearName } from "./progression-state";
import { spellbookGroups, type SpellRow } from "./spellbook-state";
import { dragRefusal, isHotbarSpell, normalize, slotKey } from "./hotbar-state";
import { startSpellDrag } from "./hotbar";
import { spellIcon } from "./spell-icons";

export interface SpellbookOptions {
  /** Cast `spell` (as outside duels). */
  onCast: (spell: SpellDef) => void;
  /** The hotbar's new layout (a glyph clicked: its spell into the first empty slot). */
  onHotbar: (slots: (number | null)[]) => void;
}

export interface SpellbookView {
  progress: Progress | null;
  learned: readonly number[];
  loadout: readonly number[];
  slots: number;
  /** Our wand is lit (Lantern Charm's button puts it out). */
  lit: boolean;
  /** Our guardian is out (its button dismisses it); absent: not out. */
  guardian?: boolean;
  /** The social hotbar's slots (spell id or null); absent: empty. */
  hotbar?: readonly (number | null)[];
}

const KIND_LABELS: Record<SpellDef["kind"], string> = {
  utility: "Utility",
  attack: "Attack",
  shield: "Shield",
  disarm: "Disarm",
  bind: "Bind",
  slow: "Hinder",
  pull: "Summon",
  counter: "Counter",
  ward: "Ward",
};

export class SpellbookWindow {
  readonly root: HTMLElement;
  /** The top bar's spellbook button (the level badge opens it too). */
  readonly button: HTMLButtonElement;
  private readonly body: HTMLElement;
  private view: SpellbookView = { progress: null, learned: [], loadout: [], slots: 3, lit: false };
  private origin: HTMLElement | null = null;
  private key = "";

  constructor(private readonly opts: SpellbookOptions) {
    this.button = h("button", { type: "button", class: "icon-button", "aria-label": "Spellbook", "aria-expanded": "false", title: "Spellbook (Y)", onclick: () => this.toggle() }, lineIcon(SPELLBOOK_ICON));
    this.body = h("div", { class: "spellbook-body" });
    this.root = h(
      "section",
      { class: "spellbook panel", role: "dialog", "aria-label": "Spellbook", hidden: true },
      h("header", { class: "spellbook-head" }, h("h2", {}, "Spellbook"), h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
      this.body,
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = !this.isOpen): void {
    if (open === this.isOpen) return;
    if (open) this.origin = focusOrigin();
    this.root.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    if (open) {
      this.key = "";
      this.render();
    } else {
      restoreFocus(this.origin, this.root);
    }
  }

  /** Y toggles the window; Escape closes it. */
  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.code === "KeyY") {
      this.toggle();
      return true;
    }
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  update(v: SpellbookView): void {
    this.view = v;
    if (this.isOpen) this.render();
  }

  private render(): void {
    const v = this.view;
    const p = v.progress;
    const guardian = v.guardian ?? false;
    const key = `${p?.level}|${p?.xp}|${v.learned.join(",")}|${v.loadout.join(",")}|${v.slots}|${v.lit}|${guardian}|${(v.hotbar ?? []).join(",")}`;
    if (key === this.key) return;
    this.key = key;
    const level = p?.level ?? 1;
    const pct = p ? Math.round(levelFraction(p) * 100) : 0;
    const summary = h(
      "div",
      { class: "spellbook-me" },
      h("strong", { class: "spellbook-level" }, `Level ${level}`, p ? h("span", { class: "spellbook-year" }, ` · ${yearName(p.year)}`) : null),
      h("span", { class: "sb-bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct, "aria-label": "Progress to the next level" }, h("span", { class: "sb-fill", style: `width: ${pct}%` })),
      h("span", { class: "detail-sub" }, `${p ? xpText(p) : "–"} · ${v.learned.length} spell${v.learned.length === 1 ? "" : "s"} learned · dueling loadout: ${v.loadout.length} of ${v.slots} slots`),
    );
    const groups = spellbookGroups(level, { learned: v.learned, loadout: v.loadout, lit: v.lit, guardian }).map((g) =>
      h("section", { class: "spell-group", "aria-label": g.title }, h("h3", {}, g.title), h("p", { class: "detail-sub" }, g.blurb), h("ul", { class: "spell-rows" }, ...g.rows.map((r) => this.row(r)))),
    );
    this.body.replaceChildren(summary, ...groups, h("p", { class: "spellbook-foot" }, "A spell is as strong at level 1 as at level 20: levels open new spells and more loadout slots, never more power."));
  }

  private row(r: SpellRow): HTMLElement {
    const s = r.spell;
    const g = gestureByKey(s.gesture);
    const [red, green, blue] = spellRgb(s);
    const colour = `rgb(${Math.round(red * 255)} ${Math.round(green * 255)} ${Math.round(blue * 255)})`;
    const cast = r.button ? h("button", { type: "button", class: "secondary spell-cast", title: r.aim ?? undefined, onclick: () => this.opts.onCast(s) }, r.button) : null;
    const slot = (this.view.hotbar ?? []).indexOf(s.id);
    const action = h("div", { class: "spell-actions" }, this.hotbarHandle(s), cast);
    return h(
      "li",
      { class: `spell-row${r.learned ? " learned" : " locked"}`, style: `--spell: ${colour}` },
      h("span", { class: "spell-glyph", "aria-hidden": "true" }, g ? gestureSvg(g) : null),
      h(
        "div",
        { class: "spell-text" },
        h(
          "div",
          { class: "spell-line" },
          h("strong", {}, s.name),
          h("span", { class: "spell-kind" }, KIND_LABELS[s.kind]),
          h("span", { class: "spell-level", title: r.where }, r.tag),
          r.inLoadout ? h("span", { class: "spell-chip" }, "In your loadout") : null,
          slot >= 0 ? h("span", { class: "spell-chip", title: "On your hotbar" }, `Hotbar key ${slotKey(slot)}`) : null,
        ),
        r.learned ? h("span", { class: "spell-incant" }, `“${s.incantation}” · ${g?.name ?? "a wand movement"}`) : null,
        h("span", { class: "spell-what" }, r.outside),
        r.duel ? h("span", { class: "spell-duel" }, r.duel) : null,
        r.learned && r.aim ? h("span", { class: "spell-aim" }, r.aim) : null,
        // Learned: the tag says where it was taught; locked: what it takes.
        r.learned ? null : h("span", { class: "detail-sub spell-where locked" }, r.where),
      ),
      action,
    );
  }

  /** The row's hotbar glyph: dragged onto a slot, or clicked into the first empty one; locked ones stay put. None for a flourish-only spell. */
  private hotbarHandle(s: SpellDef): HTMLElement | null {
    if (!isHotbarSpell(s)) return null;
    const refusal = dragRefusal(s, this.view.learned);
    if (refusal) return h("span", { class: "spell-hotbar locked", title: refusal, "aria-hidden": "true" }, spellIcon(s));
    const bar = normalize(this.view.hotbar ?? []);
    const at = bar.indexOf(s.id);
    const title =
      at >= 0
        ? `On your hotbar (key ${slotKey(at)}): drag it to another slot`
        : bar.includes(null)
          ? "Drag onto a hotbar slot, or click to put it in the first empty one"
          : "Your hotbar is full: drag this onto a slot to replace its spell";
    return h(
      "button",
      {
        type: "button",
        class: "spell-hotbar",
        draggable: "true",
        title,
        "aria-label": at >= 0 ? `${s.name} is on your hotbar, key ${slotKey(at)}` : `Put ${s.name} on your hotbar`,
        ondragstart: (e: Event) => startSpellDrag(e as DragEvent, s),
        onclick: () => {
          if (at >= 0) return;
          const free = bar.indexOf(null);
          if (free < 0) return;
          bar[free] = s.id;
          this.opts.onHotbar(bar);
        },
      },
      spellIcon(s),
    );
  }
}

/** A gesture's template as a little stroke with a dot at its start (y up on the pad, down in SVG). */
function gestureSvg(g: GestureDef): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "-110 -110 220 220");
  const path = document.createElementNS(ns, "polyline");
  path.setAttribute("points", g.points.map(([x, y]) => `${x},${-y}`).join(" "));
  path.setAttribute("class", "glyph-stroke");
  const dot = document.createElementNS(ns, "circle");
  const [sx, sy] = g.points[0] ?? [0, 0];
  dot.setAttribute("cx", String(sx));
  dot.setAttribute("cy", String(-sy));
  dot.setAttribute("r", "14");
  dot.setAttribute("class", "glyph-start");
  svg.append(path, dot);
  return svg;
}
