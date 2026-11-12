// The social hotbar (protocol v16): ten slots at the bottom centre of the
// HUD, keys 1-9 then 0, each a learned spell that does something outside a
// duel. A click or the slot's key casts it the way the spellbook's Cast
// button does (game/spells/controller.ts); slots drag onto each other
// (moving, or swapping with an occupied one); a spell row's glyph drags in
// from the spellbook (`startSpellDrag`); right-click offers "Remove from
// hotbar". Each slot shows its spell's glyph (spell-icons.ts) in its colour,
// the cooldown as a sweep, a glow while a lasting spell is on (a lit wand,
// the guardian out) and dims where it can't be cast. Hidden in a duel, whose
// spell bar has the number keys. The rules are hotbar-state.ts; the layout
// is the server's, sent with every change (`SetHotbar`).

import "./hotbar.css";
import { spellRgb, type SpellDef } from "../game/spells/data";
import { h } from "./dom";
import { HOTBAR_MARGIN, HOTBAR_SLOTS, hotbarPlace, moveSlot, placeSpell, removeSlot, slotOfCode, slotViews, type HotbarContext, type ScreenBox, type SlotView, type Slots } from "./hotbar-state";
import { spellIcon } from "./spell-icons";
import { PlayerMenu } from "./trade";

/** The drag's data type: `{ spell, from }` (`from`: the slot it left, or null from the spellbook). */
const DRAG_TYPE = "application/x-shs-hotbar-spell";

interface DragData {
  spell: number;
  from: number | null;
}

/** Starts dragging `spell` toward the hotbar (a spellbook row's glyph; a slot, `from`). */
export function startSpellDrag(e: DragEvent, spell: SpellDef, from: number | null = null): void {
  if (!e.dataTransfer) return;
  e.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ spell: spell.id, from } satisfies DragData));
  e.dataTransfer.setData("text/plain", spell.name);
  e.dataTransfer.effectAllowed = "move";
}

function dragData(e: DragEvent): DragData | null {
  try {
    const d = JSON.parse(e.dataTransfer?.getData(DRAG_TYPE) ?? "") as Partial<DragData>;
    if (typeof d.spell !== "number") return null;
    return { spell: d.spell, from: typeof d.from === "number" ? d.from : null };
  } catch {
    return null;
  }
}

/** A spell's colour for CSS (`--spell`). */
export function spellColour(s: SpellDef): string {
  const [r, g, b] = spellRgb(s);
  return `rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)})`;
}

export interface HotbarOptions {
  /** Cast `spell` (as the spellbook's Cast button does). */
  onCast: (spell: SpellDef) => void;
  /** The player rearranged the bar: the new layout, to show and send. */
  onChange: (slots: (number | null)[]) => void;
}

interface SlotEls {
  button: HTMLButtonElement;
  sweep: HTMLElement;
  /** What the slot shows now (its spell, on, off, title), to redraw only on a change. */
  shown: string;
  cd: number;
}

export class HotbarBar {
  readonly root: HTMLElement;
  /** "Remove from hotbar" (the player menu's look; appended to the HUD by the owner). */
  readonly menu = new PlayerMenu();
  private readonly els: SlotEls[];
  private slots: Slots = [];
  private views: SlotView[] = [];

  constructor(private readonly opts: HotbarOptions) {
    this.els = Array.from({ length: HOTBAR_SLOTS }, (_, i) => this.slot(i));
    this.root = h("div", { class: "hotbar", role: "toolbar", "aria-label": "Spell hotbar (keys 1 to 0)" }, ...this.els.map((s) => s.button));
  }

  private slot(i: number): SlotEls {
    const sweep = h("span", { class: "hotbar-sweep", "aria-hidden": "true" });
    const button = h("button", {
      type: "button",
      class: "hotbar-slot empty",
      onclick: () => {
        const spell = this.views[i]?.spell;
        if (spell) this.opts.onCast(spell);
        // The keys stay the game's (Space must not press the slot again).
        button.blur();
      },
      oncontextmenu: (e: Event) => {
        const m = e as MouseEvent;
        m.preventDefault();
        const spell = this.views[i]?.spell;
        if (spell) this.menu.open(spell.name, m.clientX, m.clientY, [{ label: "Remove from hotbar", run: () => this.opts.onChange(removeSlot(this.slots, i)) }]);
      },
      ondragstart: (e: Event) => {
        const spell = this.views[i]?.spell;
        if (spell) startSpellDrag(e as DragEvent, spell, i);
        else e.preventDefault();
      },
      ondragenter: (e: Event) => this.dragOver(e as DragEvent, i),
      ondragover: (e: Event) => this.dragOver(e as DragEvent, i),
      ondragleave: () => button.classList.remove("drop"),
      ondrop: (e: Event) => this.drop(e as DragEvent, i),
    });
    return { button, sweep, shown: "", cd: -1 };
  }

  private dragOver(e: DragEvent, i: number): void {
    if (!e.dataTransfer?.types.includes(DRAG_TYPE)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    this.els[i]!.button.classList.add("drop");
  }

  private drop(e: DragEvent, i: number): void {
    this.els[i]!.button.classList.remove("drop");
    const d = dragData(e);
    if (!d) return;
    e.preventDefault();
    const next = d.from !== null ? moveSlot(this.slots, d.from, i) : placeSpell(this.slots, d.spell, i);
    this.opts.onChange(next);
  }

  /** Shown (`visible`) with `slots` as the game is now; cheap per frame (only what changed is redrawn). */
  render(slots: Slots, ctx: HotbarContext, visible: boolean): void {
    this.root.hidden = !visible;
    if (!visible) {
      this.menu.close();
      return;
    }
    this.slots = slots;
    this.views = slotViews(slots, ctx);
    this.views.forEach((v, i) => this.draw(this.els[i]!, v));
  }

  private draw(el: SlotEls, v: SlotView): void {
    const s = v.spell;
    const shown = `${s?.id ?? ""}|${v.on}|${v.off}|${v.title}`;
    if (shown !== el.shown) {
      el.shown = shown;
      const b = el.button;
      b.className = `hotbar-slot${s ? "" : " empty"}${v.on ? " on" : ""}${v.off ? " off" : ""}`;
      b.title = v.title;
      b.draggable = !!s;
      b.setAttribute("aria-label", s ? `${s.name}, key ${v.key}${v.on ? ", on" : ""}` : `Empty slot, key ${v.key}`);
      b.setAttribute("aria-keyshortcuts", v.key);
      if (s) b.style.setProperty("--spell", spellColour(s));
      else b.style.removeProperty("--spell");
      b.replaceChildren(el.sweep, ...(s ? [spellIcon(s)] : []), h("kbd", { class: "hotbar-key", "aria-hidden": "true" }, v.key));
    }
    const cd = Math.round(v.cooldown * 100) / 100;
    if (cd !== el.cd) {
      el.cd = cd;
      el.sweep.style.setProperty("--cd", String(cd));
    }
  }

  /**
   * Places the bar clear of `boxes` (the chat, the menu bar, the emote bar:
   * `hotbarPlace`), and says how much of the bottom edge it takes
   * (`--hotbar-clear` on the page: windows like the spellbook stop above it).
   */
  place(boxes: readonly (ScreenBox | null)[]): void {
    const root = document.documentElement;
    if (this.root.hidden) {
      root.style.removeProperty("--hotbar-clear");
      return;
    }
    const w = this.root.offsetWidth;
    const hgt = this.root.offsetHeight;
    const p = hotbarPlace(w, hgt, boxes, window.innerWidth, window.innerHeight);
    const left = `${p.centre}px`;
    const bottom = `${p.bottom}px`;
    if (this.root.style.getPropertyValue("--hotbar-left") !== left) this.root.style.setProperty("--hotbar-left", left);
    if (this.root.style.getPropertyValue("--hotbar-bottom") !== bottom) this.root.style.setProperty("--hotbar-bottom", bottom);
    const clear = `${p.bottom + hgt + HOTBAR_MARGIN}px`;
    if (root.style.getPropertyValue("--hotbar-clear") !== clear) root.style.setProperty("--hotbar-clear", clear);
  }

  /** The slot keys (1-9, 0) cast while the bar shows; false for any other key, or an empty slot. */
  handleKey(e: KeyboardEvent): boolean {
    if (this.root.hidden || e.ctrlKey || e.metaKey || e.altKey) return false;
    const i = slotOfCode(e.code);
    if (i === null) return false;
    const spell = this.views[i]?.spell;
    if (!spell) return false;
    if (!e.repeat) this.opts.onCast(spell);
    return true;
  }

  dispose(): void {
    this.menu.dispose();
    document.documentElement.style.removeProperty("--hotbar-clear");
  }
}
