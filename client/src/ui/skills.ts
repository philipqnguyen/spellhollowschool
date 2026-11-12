// The skills panel (K, or the HUD's book button): your level and XP in each
// crafting discipline (Charms, Transfiguration, Potions) and in
// the other class subjects (raised only in class), how many of its
// recipes you know, and the patterns and recipe cards in your bag, each
// with what it teaches and a Learn button (`POST /api/recipes/learn`, which
// uses the pattern up). Patterns are a page at a time.

import "./crafting.css";
import scheduleJson from "../../../data/classes/schedule.json";
import "./potions.css";
import { ApiError } from "../api";
import * as craftingApi from "./crafting-api";
import type { BagItem, SkillView } from "./crafting-api";
import { craftingError, DISCIPLINES, itemName, levelProgress, maxSkillLevel, pageOf, patternRecipe, RECIPES, skillLevel, nextLevelXp, type Discipline } from "./crafting-content";
import { h } from "./dom";
import { lineIcon, SKILLS_ICON } from "./effects-icons";

const LABELS: Record<Discipline, { name: string; where: string }> = {
  charms: { name: "Charms", where: "Knit and sew garments at the crafting corner of your common room." },
  transfiguration: { name: "Transfiguration", where: "Restyle crafted garments at the Transfiguration table of your common room." },
  potions: { name: "Potions", where: "Brew dyes, finishes and potions at a cauldron: the Potions classroom in the dungeons, or one in your room." },
};
const PAGE = 5;
/** Every class subject's display name (schedule.json `subjects`), in file order. */
const SUBJECTS = scheduleJson.subjects as Record<string, string>;

/** A skill row's track: `discipline` (the crafting name), or `subject` (the nine class subjects). */
function trackOf(s: { discipline?: string; subject?: string }): string {
  return s.subject ?? s.discipline ?? "";
}

export interface SkillsOptions {
  notify: (text: string) => void;
  /** A pattern was learned here (an open crafting window reloads). */
  onLearned: () => void;
}

export class SkillsPanel {
  readonly root: HTMLElement;
  readonly button: HTMLButtonElement;
  private readonly body: HTMLElement;
  private readonly error: HTMLElement;
  private skills: SkillView[] = [];
  private known = new Map<Discipline, number>();
  private patterns: BagItem[] = [];
  private loaded = false;
  private page = 0;
  private busy = false;
  private generation = 0;

  constructor(private readonly opts: SkillsOptions) {
    this.button = h("button", { type: "button", class: "icon-button", "aria-label": "Skills", "aria-expanded": "false", title: "Skills (K)", onclick: () => this.toggle() }, lineIcon(SKILLS_ICON));
    this.body = h("div", { class: "skills-body" });
    this.error = h("p", { class: "craft-error", role: "alert", hidden: true });
    this.root = h(
      "section",
      { class: "skills panel", role: "dialog", "aria-label": "Skills", hidden: true },
      h("header", { class: "craft-head" }, h("h2", {}, "Skills"), h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
      this.body,
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
    if (open) void this.reload();
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.code === "KeyK") {
      this.toggle();
      return true;
    }
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  /** A craft elsewhere changed a skill: show it without a reload. */
  skillChanged(s: SkillView): void {
    const i = this.skills.findIndex((x) => x.discipline === s.discipline);
    if (i >= 0) this.skills[i] = s;
    else this.skills.push(s);
    this.render();
  }

  async reload(): Promise<void> {
    if (!this.isOpen) return;
    const gen = ++this.generation;
    try {
      const [skills, patterns, ...known] = await Promise.all([
        craftingApi.skills(),
        craftingApi.bagOf("pattern"),
        ...DISCIPLINES.map((d) => craftingApi.allPages((after) => craftingApi.recipesPage(d, after, true))),
      ]);
      if (gen !== this.generation) return;
      this.skills = skills;
      this.patterns = patterns.filter((p) => p.location === "inventory");
      DISCIPLINES.forEach((d, i) => this.known.set(d, known[i]!.length));
      this.loaded = true;
      this.showError(null);
    } catch (e) {
      if (gen !== this.generation) return;
      this.showError(e instanceof ApiError ? craftingError(e.code, e.message) : String(e));
    }
    this.render();
  }

  private async learn(p: BagItem): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.render();
    try {
      await craftingApi.learn(p.id);
      this.opts.notify(`You study the ${itemName(p.item_def)} and learn it by heart.`);
      this.opts.onLearned();
      await this.reload();
    } catch (e) {
      this.showError(e instanceof ApiError ? craftingError(e.code, e.message) : String(e));
    } finally {
      this.busy = false;
      this.render();
    }
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  private render(): void {
    if (!this.isOpen) return;
    if (!this.loaded) {
      this.body.replaceChildren(h("p", { class: "muted-line" }, "Leafing through your notes…"));
      return;
    }
    const total = (d: Discipline) => RECIPES.recipes.filter((r) => r.discipline === d).length;
    const rows = DISCIPLINES.map((d) => {
      const s = this.skills.find((x) => trackOf(x) === d);
      const xp = s?.xp ?? 0;
      const level = s?.level ?? skillLevel(xp);
      const next = s ? s.next_level_xp : nextLevelXp(xp);
      const pct = Math.round(levelProgress(xp) * 100);
      return h(
        "li",
        { class: "skill-row" },
        h("div", { class: "skill-line" }, h("strong", {}, LABELS[d].name), h("span", { class: "skill-level" }, `Level ${level} of ${maxSkillLevel()}`)),
        h("span", { class: "skill-bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct, "aria-label": `${LABELS[d].name} progress` }, h("span", { class: "skill-fill", style: `width: ${pct}%` })),
        h("span", { class: "detail-sub" }, `${xp} XP${next !== null ? `, ${next - xp} to the next level` : ", mastered"} · ${this.known.get(d) ?? 0} of ${total(d)} recipes known`),
        h("span", { class: "detail-sub skill-where" }, LABELS[d].where),
      );
    });
    // The other class subjects: raised in class only (no recipes of their own).
    const classRows = Object.entries(SUBJECTS)
      .filter(([id]) => !(DISCIPLINES as readonly string[]).includes(id))
      .map(([id, name]) => {
        const s = this.skills.find((x) => trackOf(x) === id);
        const xp = s?.xp ?? 0;
        return h("li", { class: "skill-row" }, h("div", { class: "skill-line" }, h("strong", {}, name), h("span", { class: "skill-level" }, `Level ${s?.level ?? skillLevel(xp)}`)), h("span", { class: "detail-sub" }, `${xp} XP · raised in class`));
      });
    const page = pageOf(this.patterns, this.page, PAGE);
    this.page = page.page;
    const patternRows = page.items.map((p) => {
      const recipe = patternRecipe(p.item_def);
      return h(
        "li",
        { class: "pattern-row" },
        h("div", { class: "potion-text" }, h("strong", {}, itemName(p.item_def), p.quantity > 1 ? h("span", { class: "potion-count" }, ` × ${p.quantity}`) : null), h("span", { class: "detail-sub" }, recipe ? `Teaches ${recipe.name} (${LABELS[recipe.discipline].name} level ${recipe.skill})` : "Teaches nothing you can use.")),
        h("button", { type: "button", class: "secondary", disabled: this.busy || !recipe, onclick: () => void this.learn(p) }, "Learn"),
      );
    });
    this.body.replaceChildren(
      h("ul", { class: "skill-rows" }, ...rows),
      h("p", { class: "detail-label" }, "Class subjects"),
      h("ul", { class: "skill-rows" }, ...classRows),
      h("p", { class: "detail-label" }, "Patterns and recipe cards"),
      patternRows.length
        ? h("ul", { class: "pattern-rows" }, ...patternRows)
        : h("p", { class: "muted-line" }, "None in your bag. Find them while exploring the castle, order them from the Owl Order Catalogue in your common room, or trade for them."),
      h(
        "div",
        { class: "craft-pager", hidden: page.pages <= 1 },
        h("button", { type: "button", class: "secondary", disabled: page.page === 0, onclick: () => ((this.page--, this.render())) }, "‹ Prev"),
        h("span", {}, `${page.page + 1} / ${page.pages}`),
        h("button", { type: "button", class: "secondary", disabled: page.page >= page.pages - 1, onclick: () => ((this.page++, this.render())) }, "Next ›"),
      ),
    );
  }
}
