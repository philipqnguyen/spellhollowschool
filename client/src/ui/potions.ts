// The potions satchel (P, or the HUD's bottle button): the potions in your
// bag a page at a time, what each does and for how long, and Drink. A
// Mimic Draught instead asks a friend nearby to be copied (they must
// agree: `MimicDraughtAsk`, answered by their `MimicDraughtAnswer`).
//
// Drinking is realtime (`Drink { item_id }` → `Drank` / `PotionError`); the
// list itself is the HTTP inventory (`kind=potion`).

import "./crafting.css";
import "./potions.css";
import { ApiError } from "../api";
import { paletteColour, paletteHex } from "../content/catalog";
import { dyeFamilies } from "../game/effects/chameleon";
import type { OwnEffects } from "../game/effects/state";
import { encodeDrink, encodeMimicDraughtAsk, type EffectKind, type PotionErrorCode } from "../wasm/sim_wasm";
import { bagOf } from "./crafting-api";
import { craftingError, def1c, itemName, pageOf } from "./crafting-content";
import { h } from "./dom";
import { lineIcon, POTION_ICON } from "./effects-icons";
import { durationText, effectBlurb, effectName, potionErrorText, potionStacks, type MimicDraughtCandidate, type PotionStack } from "./potions-state";

export interface PotionsOptions {
  send: (frame: Uint8Array) => void;
  notify: (text: string) => void;
  /** Online friends in view close enough to copy, nearest first. */
  candidates: () => MimicDraughtCandidate[];
  own: OwnEffects;
}

const PAGE = 6;

export class PotionsWindow {
  readonly root: HTMLElement;
  readonly button: HTMLButtonElement;
  private readonly list: HTMLElement;
  private readonly error: HTMLElement;
  private stacks: PotionStack[] = [];
  private loaded = false;
  private page = 0;
  /** The Mimic Draught stack whose friend picker is open. */
  private choosing: number | null = null;
  /** A drink sent and not answered yet (item id). */
  private pending: number | null = null;
  private pendingTimer: ReturnType<typeof setTimeout> | null = null;
  private generation = 0;

  constructor(private readonly opts: PotionsOptions) {
    this.button = h("button", { type: "button", class: "icon-button", "aria-label": "Potions", "aria-expanded": "false", title: "Potions (P)", onclick: () => this.toggle() }, lineIcon(POTION_ICON));
    this.list = h("div", { class: "potion-list" });
    this.error = h("p", { class: "craft-error", role: "alert", hidden: true });
    this.root = h(
      "section",
      { class: "potions panel", role: "dialog", "aria-label": "Potions", hidden: true },
      h("header", { class: "craft-head" }, h("h2", {}, "Potions"), h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
      this.list,
      this.error,
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = !this.isOpen): void {
    if (open === this.isOpen) return;
    this.root.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    this.choosing = null;
    if (open) void this.reload();
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.code === "KeyP") {
      this.toggle();
      return true;
    }
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  async reload(): Promise<void> {
    const gen = ++this.generation;
    try {
      const items = await bagOf("potion");
      if (gen !== this.generation) return;
      this.stacks = potionStacks(items);
      this.loaded = true;
      this.showError(null);
    } catch (e) {
      if (gen !== this.generation) return;
      this.showError(e instanceof ApiError ? craftingError(e.code, e.message) : String(e));
    }
    this.render();
  }

  // ---------------------------------------------------------------- server replies

  /** `Drank`: the potion took. */
  drank(effect: EffectKind): void {
    this.clearPending();
    this.opts.notify(`You drink the ${effectName(effect)}. ${effectBlurb(effect)}`);
    if (this.isOpen) void this.reload();
  }

  potionError(code: PotionErrorCode): void {
    this.clearPending();
    const text = potionErrorText(code);
    if (this.isOpen) this.showError(text);
    this.opts.notify(text);
    this.render();
  }

  /** The friend declined (the potion is kept). */
  declined(byName: string): void {
    this.clearPending();
    this.opts.notify(`${byName} would rather not be copied. You keep your potion.`);
    this.render();
  }

  // ---------------------------------------------------------------- actions

  private drink(s: PotionStack): void {
    if (this.pending !== null) return;
    this.setPending(s.itemId);
    this.opts.send(encodeDrink(s.itemId));
  }

  private ask(s: PotionStack, c: MimicDraughtCandidate): void {
    if (this.pending !== null) return;
    this.setPending(s.itemId);
    this.choosing = null;
    this.opts.send(encodeMimicDraughtAsk(s.itemId, c.id));
    this.opts.notify(`You ask ${c.name} whether you may borrow their look.`);
  }

  private setPending(itemId: number): void {
    this.pending = itemId;
    this.showError(null);
    // No answer at all (a lost frame, a reconnect): let the player try again.
    this.pendingTimer = setTimeout(() => this.clearPending(), 35_000);
    this.render();
  }

  private clearPending(): void {
    if (this.pendingTimer !== null) clearTimeout(this.pendingTimer);
    this.pendingTimer = null;
    this.pending = null;
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  // ---------------------------------------------------------------- rendering

  private render(): void {
    if (!this.isOpen) return;
    if (!this.loaded) {
      this.list.replaceChildren(h("p", { class: "muted-line" }, "Unstopping your satchel…"));
      return;
    }
    if (!this.stacks.length) {
      this.list.replaceChildren(h("p", { class: "muted-line" }, "No potions. Brew them at a cauldron (the Potions classroom in the dungeons, or one in your room), or buy them from other students."));
      return;
    }
    const page = pageOf(this.stacks, this.page, PAGE);
    this.page = page.page;
    const ul = h("ul", { class: "potion-rows" });
    for (const s of page.items) ul.append(this.row(s));
    const pager =
      page.pages > 1
        ? h(
            "div",
            { class: "craft-pager" },
            h("button", { type: "button", class: "secondary", disabled: page.page === 0, onclick: () => ((this.page--, this.render())) }, "‹ Prev"),
            h("span", {}, `${page.page + 1} / ${page.pages}`),
            h("button", { type: "button", class: "secondary", disabled: page.page >= page.pages - 1, onclick: () => ((this.page++, this.render())) }, "Next ›"),
          )
        : null;
    this.list.replaceChildren(ul, ...(pager ? [pager] : []));
  }

  private row(s: PotionStack): HTMLElement {
    const def = def1c(s.itemDef);
    const tintName = s.effect === "glow" ? paletteColour(s.colour)?.name : s.effect === "chameleon" ? dyeFamilies()[familyIndex(s.colour)]?.name : undefined;
    const active = this.opts.own.has(s.effect);
    const busy = this.pending !== null;
    const actions = h("div", { class: "detail-actions" });
    if (s.effect === "mimic_draught") {
      if (this.choosing === s.itemId) {
        const friends = this.opts.candidates();
        actions.append(
          ...(friends.length
            ? friends.map((c) => h("button", { type: "button", class: "secondary", disabled: busy, onclick: () => this.ask(s, c) }, `Copy ${c.name}`))
            : [h("span", { class: "muted-line inline" }, "No friends close by. Stand near a friend who is online.")]),
          h("button", { type: "button", class: "link", onclick: () => ((this.choosing = null), this.render()) }, "Cancel"),
        );
      } else {
        actions.append(h("button", { type: "button", class: "primary", disabled: busy, onclick: () => ((this.choosing = s.itemId), this.render()) }, "Choose a friend…"));
      }
    } else {
      actions.append(h("button", { type: "button", class: "primary", "data-sound": "own", disabled: busy, onclick: () => this.drink(s) }, this.pending === s.itemId ? "Drinking…" : "Drink"));
    }
    return h(
      "li",
      { class: "potion-row" },
      h("span", { class: "vial", style: `--liquid: ${paletteHex(s.effect === "glow" || s.effect === "chameleon" ? s.colour : (def?.dyes.primary ?? 0))}` }),
      h(
        "div",
        { class: "potion-text" },
        h("strong", {}, itemName(s.itemDef), tintName ? h("span", { class: "potion-tint" }, ` · ${tintName}`) : null, h("span", { class: "potion-count" }, ` × ${s.count}`)),
        h("span", { class: "detail-sub" }, `${effectBlurb(s.effect)} Lasts ${durationText(s.effect)}.${active ? " Drinking another restarts it." : ""}`),
        actions,
      ),
    );
  }

  dispose(): void {
    this.clearPending();
  }
}

function familyIndex(colour: number): number {
  return dyeFamilies().findIndex((f) => f.colours.includes(colour));
}
