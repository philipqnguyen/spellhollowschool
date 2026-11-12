// The wardrobe (I, or the HUD's wardrobe button): your clothes and furniture
// a page at a time, filtered by kind and slot; wear and take off; dye each
// item's primary, secondary and trim from the palette; saved outfits (save
// what you wear, wear, rename, delete); and a mirror, a live 3D view of your
// character that also lets you try a piece on, in unsaved dyes, before
// wearing it.
//
// Requests go over HTTP (wardrobe-api.ts); the rules for what the window
// shows are in wardrobe-state.ts. What you wear is shown to everyone by the
// server (AppearanceChanged), and to you at once from the reply.

import "./wardrobe.css";
import { ApiError, authedRequest } from "../api";
import { audio } from "../audio";
import { appearanceKey, fromWire, type WireAppearance } from "../content/appearance";
import { GEAR_SLOTS, PALETTE, paletteHex, type Dyes } from "../content/catalog";
import type { CharacterPreview } from "../game/characters/preview";
import type { Appearance, GearSlot, House } from "../wasm/sim_wasm";
import { craftingError } from "./crafting-content";
import { WardrobeDyes } from "./crafting-dye";
import { h } from "./dom";
import { wearTag } from "./wear";
import * as wardrobeApi from "./wardrobe-api";
import type { Item, Outfit } from "./wardrobe-api";
import {
  afterEquip,
  defOf,
  DYE_CHANNELS,
  emptyPaged,
  inventoryQuery,
  isTryingOn,
  itemSubtitle,
  levelNeeded,
  mergePage,
  mirrorAppearance,
  missingText,
  nothingWorn,
  outfitNameProblem,
  outfitPieces,
  OUTFIT_NAME_MAX,
  removeItem,
  replaceItem,
  sameDyes,
  SLOT_LABELS,
  slotOf,
  validDyes,
  wardrobeError,
  wearProblem,
  withDye,
  type Paged,
  type SlotFilter,
  type WardrobeTab,
  type Worn,
} from "./wardrobe-state";

export interface WardrobeOptions {
  /** Our appearance as last known (null before Welcome). */
  appearance: () => Appearance | null;
  /** A wardrobe reply changed our look. */
  onAppearance: (a: Appearance, version: number) => void;
  /** A line for the chat log. */
  notify: (text: string) => void;
  /** Our house: an item's own colours (free to go back to) may be its house colours. */
  house?: House | null;
}

const TABS: { id: WardrobeTab; label: string }[] = [
  { id: "clothing", label: "Clothing" },
  { id: "furniture", label: "Furniture" },
  { id: "outfits", label: "Outfits" },
];
const SLOT_FILTERS: { id: SlotFilter; label: string }[] = [{ id: "all", label: "All" }, ...GEAR_SLOTS.map((s) => ({ id: s, label: SLOT_LABELS[s] }))];
/** A destructive button asks once more before acting (ms the second chance lasts). */
const CONFIRM_MS = 3000;

export class WardrobeWindow {
  readonly root: HTMLElement;
  readonly button: HTMLButtonElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly mirrorNote: HTMLElement;
  private readonly wornStrip: HTMLElement;
  private readonly tabBar: HTMLElement;
  private readonly filters: HTMLElement;
  private readonly listArea: HTMLElement;
  private readonly detail: HTMLElement;
  private readonly error: HTMLElement;

  private tab: WardrobeTab = "clothing";
  private slot: SlotFilter = "all";
  private items: Paged<Item> = emptyPaged();
  private outfits: Paged<Outfit> = emptyPaged();
  private worn: Worn = nothingWorn();
  /** Every item instance we have seen, for outfit piece names. */
  private readonly known = new Map<number, Item>();
  private selected: Item | null = null;
  private dyes: Dyes | null = null;
  private channel = 0;
  private renaming: number | null = null;
  private confirmDelete: { id: number; until: number } | null = null;
  private busy = false;
  /** Bumped on every list reset, so a stale page reply is dropped. */
  private generation = 0;
  private mirror: CharacterPreview | null = null;
  private mirrorKey = "";
  private disposed = false;
  /** Dyes and finishes in the bag: what a recolour costs. */
  private readonly dyeBag: WardrobeDyes;
  /** Our player level: pieces above it are marked and cannot be put on yet. */
  private level = 1;

  constructor(private readonly opts: WardrobeOptions) {
    this.dyeBag = new WardrobeDyes(opts.house ?? null);
    this.button = h("button", { type: "button", class: "icon-button hud-wardrobe", "aria-label": "Wardrobe", "aria-expanded": "false", title: "Wardrobe (I)", onclick: () => this.toggle() }, wardrobeIcon());
    this.canvas = h("canvas", { class: "wardrobe-canvas", "aria-label": "Mirror: drag to turn, scroll to step closer" });
    this.mirrorNote = h("p", { class: "mirror-note", "aria-live": "polite" }, "");
    this.wornStrip = h("div", { class: "worn-strip" });
    this.tabBar = h("div", { class: "wardrobe-tabs", role: "tablist" });
    for (const t of TABS) {
      this.tabBar.append(h("button", { type: "button", role: "tab", class: "wardrobe-tab", "data-tab": t.id, onclick: () => this.showTab(t.id) }, t.label));
    }
    this.filters = h("div", { class: "wardrobe-filters", role: "group", "aria-label": "Slot" });
    this.listArea = h("div", { class: "wardrobe-list" });
    this.detail = h("div", { class: "wardrobe-detail" });
    this.error = h("p", { class: "wardrobe-error", role: "alert", hidden: true });
    this.root = h(
      "section",
      { class: "wardrobe panel", role: "dialog", "aria-label": "Wardrobe", hidden: true },
      h("header", { class: "wardrobe-head" }, h("h2", {}, "Wardrobe"), h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.close() }, "×")),
      h(
        "div",
        { class: "wardrobe-body" },
        h("div", { class: "wardrobe-mirror" }, h("div", { class: "mirror-frame" }, this.canvas, this.mirrorNote), this.wornStrip),
        h("div", { class: "wardrobe-side" }, this.tabBar, this.filters, this.listArea, this.detail),
      ),
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
    if (open) {
      void this.openMirror();
      void this.reload();
    } else {
      this.mirror?.dispose();
      this.mirror = null;
      this.mirrorKey = "";
      this.selected = null;
      this.dyes = null;
    }
  }

  close(): void {
    this.toggle(false);
  }

  /** I toggles the window; Escape closes it. Returns whether the key was taken. */
  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.code === "KeyI") {
      this.toggle();
      return true;
    }
    if (e.key === "Escape" && this.isOpen) {
      this.close();
      return true;
    }
    return false;
  }

  /** Our look changed (Welcome, AppearanceChanged, a reply): refresh the mirror. */
  appearanceChanged(_a: Appearance): void {
    if (this.isOpen) this.updateMirror();
  }

  /** Our player level changed (`Progress`): required levels are shown against it. */
  setLevel(level: number): void {
    if (level === this.level) return;
    this.level = level;
    if (this.isOpen) this.render();
  }

  // ---------------------------------------------------------------- loading

  private async openMirror(): Promise<void> {
    const a = this.opts.appearance();
    if (!a || this.mirror) return;
    try {
      const { CharacterPreview } = await import("../game/characters/preview");
      if (!this.isOpen || this.mirror || this.disposed) return;
      this.mirror = new CharacterPreview(this.canvas, a);
      this.mirrorKey = appearanceKey(a);
      this.updateMirror();
    } catch (e) {
      console.error(e);
      this.mirrorNote.textContent = "The mirror is clouded (no 3D view).";
    }
  }

  private async reload(): Promise<void> {
    const gen = ++this.generation;
    this.items = emptyPaged();
    this.outfits = emptyPaged();
    this.render();
    if (this.tab === "clothing") this.reloadDyes();
    try {
      const eq = await wardrobeApi.equipment();
      if (gen !== this.generation) return;
      this.worn = { hat: eq.hat, body: eq.body, legs: eq.legs, shoes: eq.shoes };
      for (const i of Object.values(this.worn)) if (i) this.known.set(i.id, i);
      this.renderWorn();
    } catch (e) {
      this.fail(e);
    }
    await this.loadMore();
  }

  private async loadMore(): Promise<void> {
    const gen = this.generation;
    try {
      if (this.tab === "outfits") {
        if (this.outfits.loaded && !this.outfits.next) return;
        const page = await wardrobeApi.outfits(this.outfits.next);
        if (gen !== this.generation) return;
        this.outfits = mergePage(this.outfits, page);
      } else {
        if (this.items.loaded && !this.items.next) return;
        const page = await wardrobeApi.inventory(inventoryQuery(this.tab, this.slot, this.items.next));
        if (gen !== this.generation) return;
        for (const i of page.items) this.known.set(i.id, i);
        this.items = mergePage(this.items, page);
      }
      this.render();
    } catch (e) {
      this.fail(e);
    }
  }

  private showTab(tab: WardrobeTab): void {
    if (tab === this.tab) return;
    this.tab = tab;
    this.select(null);
    void this.reload();
  }

  private setSlot(slot: SlotFilter): void {
    if (slot === this.slot) return;
    this.slot = slot;
    void this.reload();
  }

  // ---------------------------------------------------------------- actions

  /** Runs one request at a time, showing its error. */
  private async run(action: () => Promise<void>): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.showError(null);
    this.render();
    try {
      await action();
    } catch (e) {
      this.fail(e);
    } finally {
      this.busy = false;
      if (!this.disposed) {
        this.render();
        this.updateMirror();
      }
    }
  }

  private fail(e: unknown): void {
    if (e instanceof ApiError) this.showError(wardrobeError(e.code, e.message));
    else this.showError(e instanceof Error ? e.message : String(e));
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  private select(item: Item | null): void {
    this.selected = item;
    this.dyes = item ? [item.dyes[0], item.dyes[1], item.dyes[2]] : null;
    this.channel = 0;
    this.render();
    this.updateMirror();
  }

  private wear(item: Item, slot: GearSlot): Promise<void> {
    return this.run(async () => {
      const look = await wardrobeApi.equip(slot, item.id);
      audio.play("ui.equip");
      ({ list: this.items, worn: this.worn } = afterEquip(this.items, this.worn, slot, item));
      this.opts.onAppearance(look.appearance, look.appearanceVersion);
      if (this.selected?.id === item.id) this.selected = this.items.items.find((i) => i.id === item.id) ?? this.selected;
    });
  }

  private takeOff(slot: GearSlot): Promise<void> {
    return this.run(async () => {
      const look = await wardrobeApi.equip(slot, null);
      audio.play("ui.equip");
      const taken = this.worn[slot];
      ({ list: this.items, worn: this.worn } = afterEquip(this.items, this.worn, slot, null));
      this.opts.onAppearance(look.appearance, look.appearanceVersion);
      if (taken && this.selected?.id === taken.id) this.selected = { ...taken, location: "inventory", slot: null };
    });
  }

  private applyDyes(item: Item, dyes: Dyes): Promise<void> {
    return this.run(async () => {
      const updated = await wardrobeApi.dye(item.id, dyes);
      audio.play("ui.dye");
      this.items = replaceItem(this.items, updated);
      this.known.set(updated.id, updated);
      const slot = updated.slot;
      if (slot && this.worn[slot]?.id === updated.id) {
        this.worn = { ...this.worn, [slot]: updated };
        // A worn item's new colours change our look: the server tells the
        // world; take the new version from it straight away for ourselves.
        await this.refreshLook();
      }
      this.selected = updated;
      this.dyes = [updated.dyes[0], updated.dyes[1], updated.dyes[2]];
      this.reloadDyes();
    });
  }

  /** Re-reads the dyes and finishes in the bag (after they were used), then re-renders. */
  private reloadDyes(): void {
    this.dyeBag
      .load()
      .then(() => !this.disposed && this.renderDetail())
      .catch((e: unknown) => console.warn("[wardrobe] could not count dyes:", e));
  }

  private applyFinish(item: Item, finishStack: number): Promise<void> {
    return this.run(async () => {
      let updated: Item;
      try {
        updated = await wardrobeApi.applyFinish(item.id, finishStack);
      } catch (e) {
        if (e instanceof ApiError) throw new ApiError(e.status, e.code, craftingError(e.code, e.message));
        throw e;
      }
      this.items = replaceItem(this.items, updated);
      this.known.set(updated.id, updated);
      const slot = updated.slot;
      if (slot && this.worn[slot]?.id === updated.id) {
        this.worn = { ...this.worn, [slot]: updated };
        await this.refreshLook();
      }
      this.selected = updated;
      this.reloadDyes();
    });
  }

  private async refreshLook(): Promise<void> {
    const me = await authedRequest<{ appearance: WireAppearance; appearance_version: number }>("GET", "/characters/me");
    this.opts.onAppearance(fromWire(me.appearance), me.appearance_version);
  }

  private saveOutfit(name: string): Promise<void> {
    return this.run(async () => {
      const outfit = await wardrobeApi.saveOutfit(name.trim());
      this.outfits = { ...this.outfits, items: [outfit, ...this.outfits.items] };
      this.opts.notify(`Outfit “${outfit.name}” saved.`);
    });
  }

  private wearOutfit(o: Outfit): Promise<void> {
    return this.run(async () => {
      const r = await wardrobeApi.wearOutfit(o.id);
      audio.play("ui.equip");
      this.opts.onAppearance(r.appearance, r.appearanceVersion);
      const missing = missingText(r.missing);
      if (missing) this.showError(missing);
      const eq = await wardrobeApi.equipment();
      this.worn = { hat: eq.hat, body: eq.body, legs: eq.legs, shoes: eq.shoes };
    });
  }

  private renameOutfit(o: Outfit, name: string): Promise<void> {
    return this.run(async () => {
      const updated = await wardrobeApi.renameOutfit(o.id, name.trim());
      this.outfits = replaceItem(this.outfits, updated);
      this.renaming = null;
    });
  }

  private deleteOutfit(o: Outfit): Promise<void> {
    const now = performance.now();
    if (!this.confirmDelete || this.confirmDelete.id !== o.id || now > this.confirmDelete.until) {
      this.confirmDelete = { id: o.id, until: now + CONFIRM_MS };
      this.render();
      return Promise.resolve();
    }
    this.confirmDelete = null;
    return this.run(async () => {
      await wardrobeApi.deleteOutfit(o.id);
      this.outfits = removeItem(this.outfits, o.id);
    });
  }

  // ---------------------------------------------------------------- mirror

  private updateMirror(): void {
    const base = this.opts.appearance();
    if (!base) return;
    const shown = mirrorAppearance(base, this.selected, this.dyes);
    const key = appearanceKey(shown);
    if (this.mirror && key !== this.mirrorKey) {
      this.mirror.setAppearance(shown);
      this.mirrorKey = key;
    }
    // A piece the body may not wear is not tried on (mirrorAppearance leaves it off).
    const trying = isTryingOn(this.worn, this.selected, this.dyes) && shown !== base;
    const def = this.selected ? defOf(this.selected) : undefined;
    this.mirrorNote.textContent = trying && def ? `Trying on: ${def.name}` : "";
    this.renderWorn();
  }

  // ---------------------------------------------------------------- rendering

  private render(): void {
    for (const b of this.tabBar.querySelectorAll<HTMLButtonElement>("button")) {
      const on = b.dataset.tab === this.tab;
      b.setAttribute("aria-selected", String(on));
    }
    this.renderFilters();
    if (this.tab === "outfits") this.renderOutfits();
    else this.renderItems();
    this.renderDetail();
  }

  private renderFilters(): void {
    this.filters.hidden = this.tab !== "clothing";
    if (this.filters.hidden) return;
    this.filters.replaceChildren(
      ...SLOT_FILTERS.map((f) =>
        h("button", { type: "button", class: "chip", "aria-pressed": String(f.id === this.slot), onclick: () => this.setSlot(f.id) }, f.label),
      ),
    );
  }

  private renderItems(): void {
    const list = this.items;
    if (!list.loaded) {
      this.listArea.replaceChildren(h("p", { class: "muted-line" }, "Opening your trunk…"));
      return;
    }
    if (!list.items.length) {
      this.listArea.replaceChildren(h("p", { class: "muted-line" }, this.tab === "furniture" ? "No furniture here." : "Nothing in this drawer."));
      return;
    }
    const grid = h("ul", { class: "item-grid" });
    const body = this.opts.appearance()?.bodyType ?? null;
    for (const item of list.items) {
      const def = defOf(item);
      const need = item.location === "equipped" ? null : levelNeeded(item, this.level);
      // What this body may not wear is greyed out (it can still be kept, dyed and traded).
      const refusal = body === null || item.location === "equipped" ? null : wearProblem(item, body);
      const badge =
        item.location === "equipped"
          ? h("span", { class: "badge-worn" }, "Worn")
          : item.location === "dorm"
            ? h("span", { class: "badge-room" }, "In your room")
            : need !== null
              ? h("span", { class: "badge-room badge-level", title: `Needs level ${need} to wear` }, `Level ${need}`)
              : null;
      grid.append(
        h(
          "li",
          {},
          h(
            "button",
            {
              type: "button",
              class: refusal ? "item-card unwearable" : "item-card",
              "aria-pressed": String(this.selected?.id === item.id),
              title: refusal ? `${def?.name ?? "Unknown item"}: ${refusal}` : (def?.name ?? "Unknown item"),
              onclick: () => this.select(this.selected?.id === item.id ? null : item),
            },
            swatches(item.dyes),
            h("span", { class: "item-name" }, def?.name ?? "Unknown item"),
            wearTag(def),
            badge,
          ),
        ),
      );
    }
    const more = list.next ? h("button", { type: "button", class: "secondary load-more", disabled: this.busy, onclick: () => void this.loadMore() }, "Show more") : null;
    this.listArea.replaceChildren(grid, ...(more ? [more] : []));
  }

  private renderDetail(): void {
    const item = this.tab === "outfits" ? null : this.selected;
    this.detail.hidden = !item;
    if (!item) {
      this.detail.replaceChildren();
      return;
    }
    const def = defOf(item);
    const slot = slotOf(item);
    const worn = slot ? this.worn[slot]?.id === item.id : false;
    const placed = item.location === "dorm";
    const dyes = this.dyes ?? item.dyes;
    const changed = !sameDyes(dyes, item.dyes);

    const actions = h("div", { class: "detail-actions" });
    // A piece above our level can be kept, dyed and traded, not put on yet.
    const need = slot && !worn ? levelNeeded(item, this.level) : null;
    // A piece this body may not wear says why (the server refuses it too).
    const body = this.opts.appearance()?.bodyType;
    const refusal = slot && !worn && body !== undefined ? wearProblem(item, body) : null;
    if (slot) {
      actions.append(
        worn
          ? h("button", { type: "button", class: "secondary", disabled: this.busy, onclick: () => void this.takeOff(slot) }, "Take off")
          : h(
              "button",
              { type: "button", class: "primary", disabled: this.busy || placed || need !== null || refusal !== null, title: refusal ?? undefined, onclick: () => void this.wear(item, slot) },
              `Wear (${SLOT_LABELS[slot].toLowerCase()})`,
            ),
      );
    }

    const channels = h(
      "div",
      { class: "dye-channels", role: "radiogroup", "aria-label": "Dye channel" },
      ...DYE_CHANNELS.map((label, i) =>
        h(
          "button",
          { type: "button", role: "radio", class: "dye-channel", "aria-checked": String(i === this.channel), onclick: () => ((this.channel = i), this.renderDetail()) },
          h("span", { class: "swatch", style: `--swatch: ${paletteHex(dyes[i]!)}` }),
          h("span", {}, label),
        ),
      ),
    );
    const palette = h("div", { class: "palette", role: "listbox", "aria-label": `${DYE_CHANNELS[this.channel]} colour` });
    for (const c of PALETTE.colours) {
      palette.append(
        h("button", {
          type: "button",
          role: "option",
          class: slot && this.dyeBag.isFree(item, this.channel, c.index) ? "palette-colour free" : "palette-colour",
          style: `--swatch: ${c.hex}`,
          title: slot && this.dyeBag.isFree(item, this.channel, c.index) ? `${c.name} (free)` : c.name,
          "aria-label": c.name,
          "aria-selected": String(dyes[this.channel] === c.index),
          disabled: placed,
          onclick: () => {
            this.dyes = withDye(dyes, this.channel, c.index);
            this.renderDetail();
            this.updateMirror();
          },
        }),
      );
    }
    const dyeActions = h(
      "div",
      { class: "detail-actions" },
      h(
        "button",
        { type: "button", class: "primary", disabled: this.busy || !changed || placed || !validDyes(dyes) || (!!slot && !this.dyeBag.affordable(item, dyes)), onclick: () => void this.applyDyes(item, dyes) },
        "Dye it",
      ),
      h("button", { type: "button", class: "secondary", disabled: !changed, onclick: () => this.select(item) }, "Undo"),
    );
    // What the colours cost, and brewed finishes (clothing only).
    const cost = slot && !placed ? this.dyeBag.costLine(item, dyes) : null;
    const finish = slot && !placed ? this.dyeBag.finishSection(item, this.busy, (stack) => void this.applyFinish(item, stack)) : null;
    this.detail.replaceChildren(
      h("h3", {}, def?.name ?? "Unknown item", wearTag(def)),
      h("p", { class: "detail-sub" }, itemSubtitle(item)),
      ...(refusal ? [h("p", { class: "detail-note wear-refusal", role: "note" }, `${refusal} You can still keep it, dye it, trade it or send it by owl.`)] : []),
      // Crafted pieces carry their maker's mark.
      ...(item.maker ? [h("p", { class: "detail-note detail-maker" }, `Maker's mark: ${item.maker}`)] : []),
      ...(placed ? [h("p", { class: "detail-note" }, "This piece is placed in your room. Take it back to your trunk to wear or dye it.")] : []),
      ...(need !== null && !placed ? [h("p", { class: "detail-note" }, `You can wear this from level ${need} (you are level ${this.level}). Until then you can keep it, dye it or trade it.`)] : []),
      actions,
      h("p", { class: "detail-label" }, "Dyes"),
      channels,
      palette,
      ...(cost ? [cost] : []),
      dyeActions,
      ...(finish ? [finish] : []),
    );
  }

  private renderOutfits(): void {
    const list = this.outfits;
    const name = h("input", { type: "text", maxlength: OUTFIT_NAME_MAX, placeholder: "Name this outfit", "aria-label": "Outfit name", autocomplete: "off" });
    const save = h(
      "form",
      {
        class: "outfit-save",
        onsubmit: (e: Event) => {
          e.preventDefault();
          const problem = outfitNameProblem(name.value);
          if (problem) return this.showError(problem);
          void this.saveOutfit(name.value);
        },
      },
      name,
      h("button", { type: "submit", class: "primary", disabled: this.busy }, "Save what I wear"),
    );
    if (!list.loaded) {
      this.listArea.replaceChildren(save, h("p", { class: "muted-line" }, "Fetching your outfits…"));
      return;
    }
    const ul = h("ul", { class: "outfit-list" });
    for (const o of list.items) {
      const pieces = outfitPieces(o, this.known)
        .filter((p) => p.name)
        .map((p) => p.name)
        .join(" · ");
      let title: HTMLElement;
      if (this.renaming === o.id) {
        const input = h("input", { type: "text", maxlength: OUTFIT_NAME_MAX, value: o.name, "aria-label": "New name", autocomplete: "off" });
        title = h(
          "form",
          {
            class: "outfit-rename",
            onsubmit: (e: Event) => {
              e.preventDefault();
              const problem = outfitNameProblem(input.value);
              if (problem) return this.showError(problem);
              void this.renameOutfit(o, input.value);
            },
          },
          input,
          h("button", { type: "submit", class: "secondary", disabled: this.busy }, "Save"),
          h("button", { type: "button", class: "link", onclick: () => ((this.renaming = null), this.render()) }, "Cancel"),
        );
        queueMicrotask(() => input.focus());
      } else {
        title = h("strong", { class: "outfit-name" }, o.name);
      }
      const confirming = this.confirmDelete?.id === o.id && performance.now() < this.confirmDelete.until;
      ul.append(
        h(
          "li",
          { class: "outfit" },
          title,
          h("span", { class: "outfit-pieces" }, pieces || "Nothing worn"),
          h(
            "div",
            { class: "detail-actions" },
            h("button", { type: "button", class: "primary", disabled: this.busy, onclick: () => void this.wearOutfit(o) }, "Wear"),
            h("button", { type: "button", class: "secondary", disabled: this.busy, onclick: () => ((this.renaming = o.id), this.render()) }, "Rename"),
            h("button", { type: "button", class: confirming ? "danger" : "secondary", disabled: this.busy, onclick: () => void this.deleteOutfit(o) }, confirming ? "Really delete?" : "Delete"),
          ),
        ),
      );
    }
    const empty = list.items.length ? null : h("p", { class: "muted-line" }, "No saved outfits yet. Dress up, then save the look here (up to 10).");
    const more = list.next ? h("button", { type: "button", class: "secondary load-more", disabled: this.busy, onclick: () => void this.loadMore() }, "Show more") : null;
    this.listArea.replaceChildren(save, ...(empty ? [empty] : []), ul, ...(more ? [more] : []));
  }

  private renderWorn(): void {
    const tiles = GEAR_SLOTS.map((slot) => {
      const item = this.worn[slot];
      const def = item ? defOf(item) : undefined;
      return h(
        "button",
        {
          type: "button",
          class: "worn-tile",
          title: item ? `${def?.name ?? "Item"}: select to dye or take off` : `${SLOT_LABELS[slot]}: nothing worn`,
          disabled: !item,
          onclick: () => {
            if (!item) return;
            if (this.tab === "outfits") this.tab = "clothing";
            this.select(this.items.items.find((i) => i.id === item.id) ?? item);
          },
        },
        h("span", { class: "worn-slot" }, SLOT_LABELS[slot]),
        h("span", { class: "worn-name" }, def?.name ?? "—"),
        item ? swatches(item.dyes) : null,
      );
    });
    this.wornStrip.replaceChildren(...tiles);
  }

  dispose(): void {
    this.disposed = true;
    this.mirror?.dispose();
    this.mirror = null;
  }
}

/** Three little dye swatches. */
function swatches(dyes: readonly number[]): HTMLElement {
  return h("span", { class: "swatches", "aria-hidden": "true" }, ...dyes.slice(0, 3).map((d) => h("span", { class: "swatch", style: `--swatch: ${paletteHex(d)}` })));
}

/** A coat hanger, drawn for the HUD button. */
function wardrobeIcon(): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("hud-icon");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M12 7.2V6.6a1.9 1.9 0 1 1 1.9-1.9 M12 7.2 2.6 15.4a1.2 1.2 0 0 0 .8 2.1h17.2a1.2 1.2 0 0 0 .8-2.1Z");
  svg.append(path);
  return svg;
}
