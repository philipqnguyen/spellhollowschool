// The crafting window, opened by using a station (`StationOpened`):
//
// - the knitting corner (Charms): recipes for garments from materials and a
//   pattern; the crafter picks the dyes, with a live 3D preview of the piece
//   on their own figure and what the colours cost by the dye rule;
// - the Transfiguration table: pick one of your crafted garments, preview
//   each style of its family on yourself (same dyes and maker's mark), and
//   restyle it;
// - a cauldron (Potions; the classroom's or one placed in your room): brew
//   dyes, finishes and potions, choosing the colour of a Glow Draught or the
//   family of a Chameleon Tonic.
//
// Every recipe shows its inputs as have / need, the skill level it needs and
// the XP it gives; recipes not yet learned say which pattern teaches them
// (and offer to learn it when the pattern is in the bag). Lists are paged.
// Crafting is HTTP (crafting-api.ts); the station grant the server checks
// came with `StationOpened`. A craft plays a short charm (the wand-wave
// emote, sparks over the preview, a progress bar for `craft_secs`) and then
// reveals the result.
//
// The rules (availability, dye costs, filters) are in crafting-content.ts.

import "./crafting.css";
import { ApiError } from "../api";
import { audio } from "../audio";
import { appearanceKey, cloneAppearance } from "../content/appearance";
import { GEAR_SLOTS, PALETTE, bodyCanWear, itemDef, paletteColour, paletteHex, type Dyes } from "../content/catalog";
import { dyeFamilies, familyOf } from "../game/effects/chameleon";
import type { CharacterPreview } from "../game/characters/preview";
import type { Appearance, Finish, GearSlot, StationKind } from "../wasm/sim_wasm";
import * as craftingApi from "./crafting-api";
import type { BagItem, SkillView } from "./crafting-api";
import {
  availability,
  availabilityText,
  craftingError,
  isNightRecipe,
  def1c,
  dyeCost,
  dyeCostAffordable,
  dyesNeeded,
  filterRecipes,
  isBasic,
  inputLines,
  itemName,
  mergeRecipeView,
  pageOf,
  restyleOptions,
  skillLevel,
  stationDiscipline,
  type Availability,
  type DyeCostLine,
  type Discipline,
  type RecipeFilter,
  type RecipeQuery,
  type RecipeView,
} from "./crafting-content";
import { h } from "./dom";
import { wearTag } from "./wear";
import { SLOT_LABELS, DYE_CHANNELS } from "./wardrobe-state";

export interface OpenStation {
  kind: StationKind;
  /** The interactable id, or `item:<items.id>` for a cauldron placed in a dorm. */
  target: string;
  /** What the station is called ("Knitting Corner", "Pewter Cauldron"). */
  title: string;
}

export interface CraftingOptions {
  /** Our appearance (for the preview), null before Welcome. */
  appearance: () => Appearance | null;
  notify: (text: string) => void;
  /** A craft began: play the charm (the wand-wave emote). */
  onCharm: () => void;
  /** A garment we wear changed shape: our look is refreshed. */
  onLook: () => void;
  /** Skill XP changed. */
  onSkill: (skill: SkillView) => void;
  /** A pattern was learned (the skills panel lists patterns). */
  onLearned: () => void;
}

const DISCIPLINE_LABELS: Record<Discipline, string> = { charms: "Charms", transfiguration: "Transfiguration", potions: "Potions" };
const FILTERS: { id: RecipeFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "makeable", label: "Can make" },
  { id: "known", label: "Known" },
  { id: "unknown", label: "To learn" },
];
const GARMENT_PAGE = 6;
/** The result card stays this long unless dismissed (ms). */
const REVEAL_MS = 9000;
/** Sound: a brewing cauldron is stirred this often (ms). */
const STIR_EVERY_MS = 1600;

interface Progress {
  start: number;
  end: number;
  label: string;
}

export class CraftingWindow {
  readonly root: HTMLElement;
  private readonly title: HTMLElement;
  private readonly skillBadge: HTMLElement;
  private readonly stage: HTMLElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly vial: HTMLElement;
  private readonly overlay: HTMLElement;
  private readonly side: HTMLElement;
  private readonly error: HTMLElement;

  private station: OpenStation | null = null;
  private discipline: Discipline = "charms";
  /** Sound: when the brewing cauldron is next stirred (local ms). */
  private nextStir = 0;
  private recipes: RecipeView[] = [];
  private loaded = false;
  private level = 1;
  private xp = 0;
  private have = new Map<number, number>();
  /** A stack id per pattern definition held (to learn from). */
  private patterns = new Map<number, number>();
  private garments: BagItem[] = [];
  private query: RecipeQuery = { filter: "all", text: "", slot: "all" };
  private page = 0;
  private garmentPage = 0;
  private selected: number | null = null;
  private garment: number | null = null;
  private dyes: Dyes | null = null;
  private channel = 0;
  private busy = false;
  private progress: Progress | null = null;
  private progressTimer: ReturnType<typeof setInterval> | null = null;
  private reveal: { item: BagItem | null; outputDef: number; quantity: number; xp: number; level: number; leveledUp: boolean } | null = null;
  private revealTimer: ReturnType<typeof setTimeout> | null = null;
  /** The result of the craft in progress, revealed when its progress bar fills. */
  private pendingReveal: CraftingWindow["reveal"] = null;
  /** The same request id while the same craft is retried after a failure (idempotent retries). */
  private retry: { key: string; id: string } | null = null;
  private preview: CharacterPreview | null = null;
  private previewKey = "";
  private generation = 0;
  private disposed = false;

  constructor(private readonly opts: CraftingOptions) {
    this.title = h("h2", {}, "Crafting");
    this.skillBadge = h("span", { class: "craft-skill" }, "");
    this.canvas = h("canvas", { class: "craft-canvas", "aria-label": "Preview: drag to turn" });
    this.vial = h("div", { class: "craft-cauldron", "aria-hidden": "true" }, h("span", { class: "cauldron-liquid" }), h("span", { class: "cauldron-bubble b1" }), h("span", { class: "cauldron-bubble b2" }), h("span", { class: "cauldron-bubble b3" }));
    this.overlay = h("div", { class: "craft-overlay", "aria-live": "polite" });
    this.stage = h("div", { class: "craft-stage" }, this.canvas, this.vial, this.overlay);
    this.side = h("div", { class: "craft-side" });
    this.error = h("p", { class: "craft-error", role: "alert", hidden: true });
    this.root = h(
      "section",
      { class: "crafting panel", role: "dialog", "aria-label": "Crafting", hidden: true },
      h("header", { class: "craft-head" }, h("div", { class: "craft-title" }, this.title, this.skillBadge), h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.close() }, "×")),
      h("div", { class: "craft-body" }, this.stage, this.side),
      this.error,
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** The station the window is open for. */
  get openStation(): OpenStation | null {
    return this.isOpen ? this.station : null;
  }

  // ---------------------------------------------------------------- open and close

  open(station: OpenStation): void {
    const discipline = stationDiscipline(station.kind);
    if (!discipline) return;
    const same = this.isOpen && this.station?.target === station.target;
    this.station = station;
    this.title.textContent = station.title;
    if (!same) {
      this.discipline = discipline;
      this.query = { filter: "all", text: "", slot: "all" };
      this.page = 0;
      this.garmentPage = 0;
      this.selected = null;
      this.garment = null;
      this.dyes = null;
      this.reveal = null;
    }
    this.root.dataset.discipline = discipline;
    this.root.hidden = false;
    this.canvas.hidden = discipline === "potions";
    this.vial.hidden = discipline !== "potions";
    if (discipline !== "potions") void this.openPreview();
    void this.reload();
  }

  /** Closes the window; `why` is said in the chat (e.g. walked away). */
  close(why?: string): void {
    if (!this.isOpen) return;
    this.root.hidden = true;
    this.preview?.dispose();
    this.preview = null;
    this.previewKey = "";
    this.showError(null);
    if (why) this.opts.notify(why);
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.close();
      return true;
    }
    return false;
  }

  /** Something elsewhere changed what we know (a pattern learned in the skills panel): reload if open. */
  refresh(): void {
    if (this.isOpen) void this.reload();
  }

  /** Our look changed: the preview follows. */
  appearanceChanged(): void {
    if (this.isOpen) this.updatePreview();
  }

  private async openPreview(): Promise<void> {
    const a = this.opts.appearance();
    if (!a || this.preview) return;
    try {
      const { CharacterPreview } = await import("../game/characters/preview");
      if (!this.isOpen || this.preview || this.disposed || this.discipline === "potions") return;
      this.preview = new CharacterPreview(this.canvas, a);
      this.previewKey = appearanceKey(a);
      this.updatePreview();
    } catch (e) {
      console.error(e);
    }
  }

  // ---------------------------------------------------------------- loading

  private async reload(): Promise<void> {
    const gen = ++this.generation;
    this.loaded = false;
    this.render();
    try {
      const [recipes, skills, materials, dyes, patterns, garments] = await Promise.all([
        craftingApi.allRecipes(this.discipline),
        craftingApi.skills(),
        craftingApi.bagOf("material"),
        craftingApi.bagOf("dye"),
        craftingApi.bagOf("pattern"),
        this.discipline === "transfiguration" ? craftingApi.allPages((after) => craftingApi.inventoryPage("clothing", after, 100, null)) : Promise.resolve([] as BagItem[]),
      ]);
      if (gen !== this.generation || this.disposed) return;
      this.recipes = recipes.map((r) => mergeRecipeView(r)).filter((r): r is RecipeView => !!r && r.discipline === this.discipline);
      this.applySkills(skills);
      this.have = craftingApi.countByDef([...materials, ...dyes, ...patterns]);
      this.patterns = new Map();
      for (const p of patterns) if (p.location === "inventory" && !this.patterns.has(p.item_def)) this.patterns.set(p.item_def, p.id);
      this.garments = garments.filter((g) => (g.location === "inventory" || g.location === "equipped") && restyleOptions(g.item_def).length > 0);
      this.loaded = true;
      this.showError(null);
    } catch (e) {
      if (gen !== this.generation) return;
      this.fail(e);
    }
    this.render();
  }

  private applySkills(skills: readonly SkillView[]): void {
    const s = skills.find((x) => x.discipline === this.discipline);
    this.xp = s?.xp ?? 0;
    this.level = s?.level ?? skillLevel(this.xp);
    const next = s?.next_level_xp ?? null;
    this.skillBadge.textContent = `${DISCIPLINE_LABELS[this.discipline]} level ${this.level}${next !== null ? ` · ${this.xp} / ${next} XP` : " · mastered"}`;
  }

  // ---------------------------------------------------------------- rules

  private recipe(): RecipeView | null {
    return this.recipes.find((r) => r.id === this.selected) ?? null;
  }

  private selectedGarment(): BagItem | null {
    return this.garments.find((g) => g.id === this.garment) ?? null;
  }

  /** The output's own dyes (what crafting starts from). */
  private defaults(r: RecipeView): Dyes {
    const d = itemDef(r.output.item);
    return d ? [d.dyes.primary, d.dyes.secondary, d.dyes.trim] : [0, 0, 0];
  }

  /** The dyes this craft would use and their cost, for dyeable and tinted recipes. */
  private dyePlan(r: RecipeView): { dyes: Dyes; cost: DyeCostLine[] } | null {
    if (!r.dyeable && !r.tint) return null;
    const defaults = this.defaults(r);
    // The colours picked belong to the selected recipe; every other one is priced at its own.
    const dyes = (r.id === this.selected ? this.dyes : null) ?? defaults;
    const wanted: Dyes = r.tint ? [dyes[0], defaults[1], defaults[2]] : dyes;
    const need = dyesNeeded(defaults, defaults, wanted);
    return { dyes: wanted, cost: need ? dyeCost(need, this.have) : [] };
  }

  private availabilityOf(r: RecipeView): Availability {
    const plan = this.dyePlan(r);
    const garment = this.selectedGarment();
    return availability({
      recipe: r,
      known: r.known,
      level: this.level,
      have: this.have,
      hasGarment: r.kind === "restyle" ? !!garment && garment.item_def !== r.output.item && restyleOptions(garment.item_def).some((o) => o.recipe.id === r.id) : undefined,
      dyes: plan?.cost,
    });
  }

  // ---------------------------------------------------------------- actions

  private select(id: number | null): void {
    this.selected = id;
    this.dyes = null;
    this.channel = 0;
    this.reveal = null;
    this.render();
    // The detail sits under the list: bring it into view.
    if (id !== null) this.side.querySelector(".craft-detail")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  private async learn(patternDef: number): Promise<void> {
    const stack = this.patterns.get(patternDef);
    if (stack === undefined || this.busy) return;
    await this.run(async () => {
      await craftingApi.learn(stack);
      this.opts.notify(`You study the ${itemName(patternDef)} and learn it by heart.`);
      this.opts.onLearned();
      await this.reload();
    });
  }

  private async craftSelected(): Promise<void> {
    const r = this.recipe();
    if (!r || this.busy || this.progress) return;
    const plan = this.dyePlan(r);
    const garment = r.kind === "restyle" ? this.selectedGarment() : null;
    const req: craftingApi.CraftRequest = { recipe_id: r.id };
    if (plan) req.dyes = plan.dyes;
    if (garment) req.item_id = garment.id;
    const key = JSON.stringify(req);
    if (!this.retry || this.retry.key !== key) this.retry = { key, id: craftingApi.requestId("craft") };
    req.request_id = this.retry.id;
    await this.run(async () => {
      const res = await craftingApi.craft(req);
      this.retry = null;
      this.opts.onCharm();
      this.opts.onSkill(res.skill);
      const before = this.level;
      this.applySkills([res.skill]);
      const now = performance.now();
      audio.play("craft.add");
      this.nextStir = now + STIR_EVERY_MS / 2;
      this.startProgress({ start: now, end: now + Math.max(1, r.craft_secs) * 1000, label: r.name });
      this.pendingReveal = { item: res.item, outputDef: r.output.item, quantity: r.output.quantity, xp: r.xp, level: res.skill.level, leveledUp: res.skill.level > before };
      if (garment?.location === "equipped") this.opts.onLook();
      // A restyled garment now is the target style: offer the others, not this one again.
      if (r.kind === "restyle") this.selected = null;
      // The bag changed (inputs used, output made): count again quietly.
      void this.refreshBag();
    });
  }

  private async refreshBag(): Promise<void> {
    try {
      const [materials, dyes, patterns, garments] = await Promise.all([
        craftingApi.bagOf("material"),
        craftingApi.bagOf("dye"),
        craftingApi.bagOf("pattern"),
        this.discipline === "transfiguration" ? craftingApi.allPages((after) => craftingApi.inventoryPage("clothing", after, 100, null)) : Promise.resolve(null),
      ]);
      this.have = craftingApi.countByDef([...materials, ...dyes, ...patterns]);
      if (garments) this.garments = garments.filter((g) => (g.location === "inventory" || g.location === "equipped") && restyleOptions(g.item_def).length > 0);
      if (!this.disposed) this.render();
    } catch (e) {
      console.warn("[crafting] could not recount the bag:", e);
    }
  }

  private startProgress(p: Progress): void {
    this.progress = p;
    if (this.progressTimer !== null) clearInterval(this.progressTimer);
    this.progressTimer = setInterval(() => this.tickProgress(), 100);
    this.render();
  }

  private tickProgress(): void {
    const p = this.progress;
    if (!p) return;
    const now = performance.now();
    if (now >= p.end) {
      if (this.progressTimer !== null) clearInterval(this.progressTimer);
      this.progressTimer = null;
      this.progress = null;
      this.reveal = this.pendingReveal;
      this.pendingReveal = null;
      if (this.reveal) {
        const r = this.reveal;
        audio.play("craft.success");
        this.opts.notify(`You made ${r.quantity > 1 ? `${r.quantity} × ` : "a "}${itemName(r.outputDef)} (+${r.xp} ${DISCIPLINE_LABELS[this.discipline]} XP).${r.leveledUp ? ` Your ${DISCIPLINE_LABELS[this.discipline]} is now level ${r.level}!` : ""}`);
        if (this.revealTimer !== null) clearTimeout(this.revealTimer);
        this.revealTimer = setTimeout(() => {
          this.reveal = null;
          this.renderStage();
        }, REVEAL_MS);
      }
      this.render();
      return;
    }
    // The cauldron is stirred now and then while it brews.
    if (this.discipline === "potions" && now >= this.nextStir) {
      this.nextStir = now + STIR_EVERY_MS;
      audio.play("craft.stir");
    }
    this.renderStage();
  }

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
      if (!this.disposed) this.render();
    }
  }

  private fail(e: unknown): void {
    audio.play("ui.error");
    if (e instanceof ApiError) {
      // A definite answer: the next attempt is a new craft.
      if (e.status !== 0 && e.status < 500) this.retry = null;
      this.showError(craftingError(e.code, e.message));
    } else this.showError(e instanceof Error ? e.message : String(e));
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  // ---------------------------------------------------------------- preview

  /** What the preview shows: our figure wearing the piece being made or restyled. */
  private previewAppearance(): Appearance | null {
    const base = this.opts.appearance();
    if (!base) return null;
    const r = this.recipe();
    const out = (id: number, dyes: Dyes, slot: GearSlot | undefined, finish: Finish = "none") => {
      // A piece this body may not wear is made, not tried on.
      if (!slot || !bodyCanWear(base.bodyType, itemDef(id))) return base;
      const a = cloneAppearance(base);
      a.outfit[slot] = { itemDef: id, dyes: [dyes[0], dyes[1], dyes[2]], ...(finish !== "none" ? { finish } : {}) };
      return a;
    };
    if (this.discipline === "transfiguration") {
      // A restyle keeps the garment's dyes and finish.
      const g = this.selectedGarment();
      if (!g) return base;
      const target = r && r.kind === "restyle" ? r.output.item : g.item_def;
      return out(target, g.dyes, itemDef(target)?.slot, g.finish);
    }
    if (!r) return base;
    const d = itemDef(r.output.item);
    if (d?.kind !== "clothing") return base;
    return out(r.output.item, this.dyePlan(r)?.dyes ?? this.defaults(r), d.slot);
  }

  private updatePreview(): void {
    if (this.discipline === "potions") {
      const r = this.recipe();
      const colour = r ? (this.dyePlan(r)?.dyes[0] ?? this.defaults(r)[0]) : 34;
      this.vial.style.setProperty("--liquid", paletteHex(colour));
      return;
    }
    const a = this.previewAppearance();
    if (!a || !this.preview) return;
    const key = appearanceKey(a);
    if (key !== this.previewKey) {
      this.preview.setAppearance(a);
      this.previewKey = key;
    }
  }

  // ---------------------------------------------------------------- rendering

  private render(): void {
    if (!this.isOpen) return;
    this.renderStage();
    if (!this.loaded) {
      this.side.replaceChildren(h("p", { class: "muted-line" }, this.discipline === "potions" ? "Stoking the fire…" : "Laying out the needles…"));
      return;
    }
    if (this.discipline === "transfiguration") this.renderTransfiguration();
    else this.renderRecipes();
    this.updatePreview();
  }

  /** The progress bar and the result card over the preview. */
  private renderStage(): void {
    const p = this.progress;
    this.stage.classList.toggle("casting", !!p);
    if (p) {
      const t = Math.min(1, (performance.now() - p.start) / Math.max(1, p.end - p.start));
      const bar = this.overlay.querySelector<HTMLElement>(".craft-progress-fill");
      if (bar) {
        bar.style.width = `${(t * 100).toFixed(1)}%`;
        return;
      }
      this.overlay.replaceChildren(
        h("div", { class: "craft-sparks", "aria-hidden": "true" }, ...[0, 1, 2, 3, 4, 5].map((i) => h("span", { class: `spark s${i}` }))),
        h("div", { class: "craft-progress" }, h("span", { class: "craft-progress-label" }, this.discipline === "potions" ? "Brewing…" : this.discipline === "transfiguration" ? "Transfiguring…" : "Enchanting the needles…"), h("span", { class: "craft-progress-bar" }, h("span", { class: "craft-progress-fill", style: `width: ${(t * 100).toFixed(1)}%` }))),
      );
      return;
    }
    const r = this.reveal;
    if (r) {
      const item = r.item;
      const def = itemDef(r.outputDef);
      this.overlay.replaceChildren(
        h(
          "div",
          { class: "craft-reveal" },
          h("span", { class: "detail-label" }, "You made"),
          h("strong", {}, `${r.quantity > 1 ? `${r.quantity} × ` : ""}${def?.name ?? "Something"}`),
          item ? swatches(item.dyes) : null,
          item?.maker ? h("span", { class: "craft-maker" }, `Maker's mark: ${item.maker}`) : null,
          h("span", { class: "craft-xp" }, `+${r.xp} XP${r.leveledUp ? ` · level ${r.level}!` : ""}`),
          h("button", { type: "button", class: "link", onclick: () => ((this.reveal = null), this.renderStage()) }, "Lovely"),
        ),
      );
      return;
    }
    this.overlay.replaceChildren();
  }

  private renderRecipes(): void {
    const isClothing = this.discipline === "charms";
    const search = h("input", {
      type: "search",
      class: "craft-search",
      placeholder: this.discipline === "potions" ? "Search brews" : "Search patterns",
      "aria-label": "Search recipes",
      value: this.query.text,
      autocomplete: "off",
      oninput: (e: Event) => {
        this.query = { ...this.query, text: (e.target as HTMLInputElement).value };
        this.page = 0;
        this.renderList(list);
      },
    });
    const chips = h(
      "div",
      { class: "wardrobe-filters", role: "group", "aria-label": "Show" },
      ...FILTERS.map((f) => h("button", { type: "button", class: "chip", "aria-pressed": String(this.query.filter === f.id), onclick: () => ((this.query = { ...this.query, filter: f.id }), (this.page = 0), this.render()) }, f.label)),
    );
    const slots = isClothing
      ? h(
          "div",
          { class: "wardrobe-filters", role: "group", "aria-label": "Slot" },
          h("button", { type: "button", class: "chip", "aria-pressed": String(this.query.slot === "all"), onclick: () => ((this.query = { ...this.query, slot: "all" }), (this.page = 0), this.render()) }, "Every slot"),
          ...GEAR_SLOTS.map((s) => h("button", { type: "button", class: "chip", "aria-pressed": String(this.query.slot === s), onclick: () => ((this.query = { ...this.query, slot: s }), (this.page = 0), this.render()) }, SLOT_LABELS[s])),
        )
      : null;
    const list = h("div", { class: "craft-list" });
    this.renderList(list);
    const detail = this.renderDetail();
    this.side.replaceChildren(h("div", { class: "craft-filters" }, search, chips, slots), list, ...(detail ? [detail] : []));
  }

  private renderList(list: HTMLElement): void {
    const all = filterRecipes(this.recipes, this.query, (r) => this.availabilityOf(r).kind === "ready");
    const page = pageOf(all, this.page);
    this.page = page.page;
    if (!all.length) {
      list.replaceChildren(h("p", { class: "muted-line" }, this.recipes.length ? "Nothing matches." : "No recipes here yet."));
      return;
    }
    const ul = h("ul", { class: "recipe-rows" });
    for (const r of page.items) {
      const av = this.availabilityOf(r);
      ul.append(
        h(
          "li",
          {},
          h(
            "button",
            { type: "button", class: `recipe-row ${av.kind}`, "aria-pressed": String(this.selected === r.id), onclick: () => this.select(this.selected === r.id ? null : r.id) },
            h("span", { class: "recipe-status", title: availabilityText(av) }),
            h("span", { class: "recipe-name" }, r.name, wearTag(r.output.item)),
            h("span", { class: "recipe-meta" }, `lvl ${r.skill} · ${r.xp} XP${isNightRecipe(r) ? " · night only" : ""}`),
          ),
        ),
      );
    }
    list.replaceChildren(ul, this.pager(page.page, page.pages, (p) => ((this.page = p), this.render())));
  }

  private pager(page: number, pages: number, go: (p: number) => void): HTMLElement {
    return h(
      "div",
      { class: "craft-pager", hidden: pages <= 1 },
      h("button", { type: "button", class: "secondary", disabled: page === 0, onclick: () => go(page - 1) }, "‹ Prev"),
      h("span", {}, `${page + 1} / ${pages}`),
      h("button", { type: "button", class: "secondary", disabled: page >= pages - 1, onclick: () => go(page + 1) }, "Next ›"),
    );
  }

  /** The selected recipe: output, skill, inputs (have / need), dyes, and the button. */
  private renderDetail(): HTMLElement | null {
    const r = this.recipe();
    if (!r) return null;
    const av = this.availabilityOf(r);
    const out = def1c(r.output.item);
    const inputs = h(
      "ul",
      { class: "craft-inputs" },
      ...(r.kind === "restyle" ? [h("li", { class: "have" }, h("span", {}, "The garment itself"), h("span", { class: "count" }, this.selectedGarment() ? "✓" : "–"))] : []),
      ...inputLines(r, this.have).map((l) => h("li", { class: l.have >= l.need ? "have" : "short" }, h("span", {}, itemName(l.item)), h("span", { class: "count" }, `${Math.min(l.have, 999)} / ${l.need}`))),
    );
    const plan = this.dyePlan(r);
    const dyePicker = plan && r.known ? this.dyePicker(r, plan) : null;
    const learnable = av.kind === "unknown" && av.held && av.pattern !== null;
    const action = learnable
      ? h("button", { type: "button", class: "primary", disabled: this.busy, onclick: () => void this.learn(av.pattern!) }, `Learn from ${itemName(av.pattern!)}`)
      : h(
          "button",
          { type: "button", class: "primary", "data-sound": "own", disabled: this.busy || !!this.progress || av.kind !== "ready" },
          this.discipline === "potions" ? "Brew" : r.kind === "restyle" ? "Transfigure" : "Craft",
        );
    if (!learnable) action.addEventListener("click", () => void this.craftSelected());
    return h(
      "div",
      { class: "craft-detail" },
      h("h3", {}, out?.name ?? r.name, r.output.quantity > 1 ? h("span", { class: "muted" }, ` × ${r.output.quantity}`) : null, wearTag(r.output.item)),
      h("p", { class: "detail-sub" }, `Needs ${DISCIPLINE_LABELS[r.discipline]} level ${r.skill} · gives ${r.xp} XP · ${r.craft_secs} s${out?.required_level && out.required_level > 1 ? ` · wearer level ${out.required_level}` : ""}`),
      h("p", { class: "detail-label" }, "Needs"),
      inputs,
      dyePicker,
      h("p", { class: `craft-status ${av.kind}` }, availabilityText(av)),
      h("div", { class: "detail-actions" }, action),
    );
  }

  /** Dyes for a garment (three channels), or the colour of a tinted brew. */
  private dyePicker(r: RecipeView, plan: { dyes: Dyes; cost: DyeCostLine[] }): HTMLElement {
    const defaults = this.defaults(r);
    const dyes = plan.dyes;
    const channels = r.tint ? [0] : [0, 1, 2];
    const channel = r.tint ? 0 : this.channel;
    const isChameleon = out1cEffect(r.output.item) === "chameleon";
    const label = r.tint ? (isChameleon ? "Colour family" : "Colour") : "Dyes";
    const tabs = r.tint
      ? null
      : h(
          "div",
          { class: "dye-channels", role: "radiogroup", "aria-label": "Dye channel" },
          ...channels.map((i) =>
            h(
              "button",
              { type: "button", role: "radio", class: "dye-channel", "aria-checked": String(i === channel), onclick: () => ((this.channel = i), this.render()) },
              h("span", { class: "swatch", style: `--swatch: ${paletteHex(dyes[i]!)}` }),
              h("span", {}, DYE_CHANNELS[i]!),
            ),
          ),
        );
    const palette = h("div", { class: "palette", role: "listbox", "aria-label": `${label}` });
    for (const c of PALETTE.colours) {
      const free = c.index === defaults[channel] || isBasic(c.index);
      palette.append(
        h("button", {
          type: "button",
          role: "option",
          class: free ? "palette-colour free" : "palette-colour",
          style: `--swatch: ${c.hex}`,
          title: isChameleon ? `${dyeFamilies()[familyOf(c.index)]?.name ?? c.name}${free ? " (free)" : ""}` : `${c.name}${free ? " (free)" : ""}`,
          "aria-label": c.name,
          "aria-selected": String(dyes[channel] === c.index),
          onclick: () => {
            const next: Dyes = [dyes[0], dyes[1], dyes[2]];
            next[channel] = c.index;
            this.dyes = next;
            this.render();
          },
        }),
      );
    }
    const affordable = dyeCostAffordable(plan.cost);
    const cost = plan.cost.length
      ? h("p", { class: `dye-cost ${affordable ? "" : "short"}` }, "Uses ", plan.cost.map((l) => `${l.need} × ${l.item === null ? "an unknown dye" : itemName(l.item)} (you have ${l.have})`).join(", "), ".")
      : h("p", { class: "dye-cost free" }, r.tint ? "Its own colour and the plain basics (dotted) are free; another uses up one dye." : "Its own colours and the plain basics (dotted) are free; each other colour uses up one dye of its family.");
    const chosen = isChameleon ? dyeFamilies()[familyOf(dyes[0])]?.name : r.tint ? paletteColour(dyes[0])?.name : null;
    return h("div", { class: "craft-dyes" }, h("p", { class: "detail-label" }, label, chosen ? h("span", { class: "craft-chosen" }, ` · ${chosen}`) : null), tabs, palette, cost);
  }

  /** Transfiguration: your garments, then the styles each can take. */
  private renderTransfiguration(): void {
    if (!this.garments.length) {
      this.side.replaceChildren(h("p", { class: "muted-line" }, "Transfiguration restyles crafted garments within their family (a beret into a cloche, a cloak into a jacket), keeping their dyes and maker's mark. Craft one at a knitting corner first."));
      return;
    }
    const page = pageOf(this.garments, this.garmentPage, GARMENT_PAGE);
    this.garmentPage = page.page;
    const grid = h("ul", { class: "item-grid craft-garments" });
    for (const g of page.items) {
      const def = itemDef(g.item_def);
      grid.append(
        h(
          "li",
          {},
          h(
            "button",
            {
              type: "button",
              class: "item-card",
              "aria-pressed": String(this.garment === g.id),
              title: def?.name ?? "Garment",
              onclick: () => {
                this.garment = this.garment === g.id ? null : g.id;
                this.selected = null;
                this.reveal = null;
                this.render();
              },
            },
            swatches(g.dyes),
            h("span", { class: "item-name" }, def?.name ?? "Garment"),
            g.location === "equipped" ? h("span", { class: "badge-worn" }, "Worn") : null,
          ),
        ),
      );
    }
    const parts: (HTMLElement | null)[] = [h("p", { class: "detail-label" }, "Your garments"), grid, this.pager(page.page, page.pages, (p) => ((this.garmentPage = p), this.render()))];
    const g = this.selectedGarment();
    if (g) {
      const options = restyleOptions(g.item_def);
      const known = new Map(this.recipes.map((r) => [r.id, r]));
      parts.push(
        h("p", { class: "detail-label" }, "Restyle into"),
        h(
          "div",
          { class: "restyle-options", role: "listbox", "aria-label": "Styles" },
          ...options.map((o) => {
            const view = known.get(o.recipe.id) ?? mergeRecipeView({ id: o.recipe.id, known: false, can_make: false });
            const av = view ? this.availabilityOf(view) : null;
            return h(
              "button",
              {
                type: "button",
                role: "option",
                class: `chip restyle-option ${av?.kind ?? ""}`,
                "aria-selected": String(this.selected === o.recipe.id),
                title: av ? availabilityText(av) : "",
                onmouseenter: () => this.hoverPreview(g, o.target),
                onmouseleave: () => this.updatePreview(),
                onclick: () => this.select(o.recipe.id),
              },
              itemDef(o.target)?.name ?? "A style",
              wearTag(o.target),
            );
          }),
        ),
      );
      const detail = this.renderDetail();
      if (detail) parts.push(detail);
    }
    this.side.replaceChildren(...parts.filter((p): p is HTMLElement => !!p));
  }

  /** Hovering a style tries it on in the preview. */
  private hoverPreview(g: BagItem, target: number): void {
    const base = this.opts.appearance();
    const slot = itemDef(target)?.slot;
    if (!base || !slot || !this.preview || !bodyCanWear(base.bodyType, itemDef(target))) return;
    const a = cloneAppearance(base);
    a.outfit[slot] = { itemDef: target, dyes: [g.dyes[0], g.dyes[1], g.dyes[2]], ...(g.finish !== "none" ? { finish: g.finish } : {}) };
    const key = appearanceKey(a);
    if (key !== this.previewKey) {
      this.preview.setAppearance(a);
      this.previewKey = key;
    }
  }

  dispose(): void {
    this.disposed = true;
    if (this.progressTimer !== null) clearInterval(this.progressTimer);
    if (this.revealTimer !== null) clearTimeout(this.revealTimer);
    this.preview?.dispose();
    this.preview = null;
  }
}

function out1cEffect(itemDefId: number): string | null {
  return def1c(itemDefId)?.potion?.effect ?? null;
}

function swatches(dyes: readonly number[]): HTMLElement {
  return h("span", { class: "swatches", "aria-hidden": "true" }, ...dyes.slice(0, 3).map((d) => h("span", { class: "swatch", style: `--swatch: ${paletteHex(d)}` })));
}
