// A class on the HUD: the lesson panel (which class, the professor, the
// phase and its countdown, how the practice has gone, the way out) and the
// results card the professor hands out at the end (the grade, XP, house
// points and any spells learned; Crowns, items, recipes taught,
// a lore entry and a hint, a fortune). The trace pad is classes-pad.ts, the
// pick tray classes-pick.ts; the lesson itself is followed in game/classes.

import "./classes.css";
import type { ClassRewards, Grade, LessonPhase } from "../wasm/sim_wasm";
import { coinIcon, itemIcon } from "../economy/icons";
import { rewardLines, type RewardLine } from "./classes-results";
import { focusOrigin, h, restoreFocus } from "./dom";
import { formatCountdown } from "./schedule-state";

/** What the lesson panel shows. */
export interface LessonView {
  className: string;
  professor: string;
  section: number;
  phase: LessonPhase;
  /** Real ms until the phase ends. */
  phaseLeftMs: number;
  roundsTotal: number;
  /** Beats answered (traced or picked). */
  answered: number;
  beats: number;
  /** The lesson's beats are picks (the tray), not gestures (the pad). */
  picking: boolean;
  /** The beats are rings to fly through (the Flying class). */
  flying?: boolean;
  average: number;
}

/** A leave button asks once more within this long (ms). */
const CONFIRM_MS = 3000;

export class LessonPanel {
  readonly root: HTMLElement;
  private readonly title: HTMLElement;
  private readonly sub: HTMLElement;
  private readonly phase: HTMLElement;
  private readonly hint: HTMLElement;
  private readonly tally: HTMLElement;
  private readonly leave: HTMLButtonElement;
  private confirmUntil = 0;
  private key = "";

  constructor(private readonly onLeave: () => void) {
    this.title = h("h2", {}, "");
    this.sub = h("p", { class: "lesson-sub" }, "");
    this.phase = h("p", { class: "lesson-phase", "aria-live": "polite" }, "");
    this.hint = h("p", { class: "lesson-hint" }, "");
    this.tally = h("p", { class: "lesson-tally" }, "");
    this.leave = h("button", { type: "button", class: "secondary lesson-leave", onclick: () => this.leaveClicked() }, "Leave class");
    this.root = h("section", { class: "lesson-panel panel", "aria-label": "Class", hidden: true }, this.title, this.sub, this.phase, this.hint, this.tally, this.leave);
  }

  show(v: LessonView | null): void {
    this.root.hidden = !v;
    if (!v) return;
    const words = phaseWords(v);
    const tally = v.beats > 0 ? (v.flying ? `Flew through ${v.answered} of ${v.beats} rings` : `${v.picking ? "Answered" : "Traced"} ${v.answered} of ${v.beats} beats`) + (v.average ? ` · average ${v.average}` : "") : "";
    const key = `${v.className}|${v.professor}|${v.section}|${words.phase}|${words.hint}|${tally}`;
    if (key === this.key) return;
    this.key = key;
    this.title.textContent = v.className;
    this.sub.textContent = `${v.professor} · section ${v.section}`;
    this.phase.textContent = words.phase;
    this.hint.textContent = words.hint;
    this.tally.textContent = tally;
    this.tally.hidden = !tally;
  }

  private leaveClicked(): void {
    const now = performance.now();
    if (now > this.confirmUntil) {
      this.confirmUntil = now + CONFIRM_MS;
      this.leave.textContent = "Really leave? You can't rejoin this lesson";
      this.leave.classList.add("danger");
      setTimeout(() => {
        if (performance.now() >= this.confirmUntil) this.resetLeave();
      }, CONFIRM_MS + 50);
      return;
    }
    this.resetLeave();
    this.onLeave();
  }

  private resetLeave(): void {
    this.confirmUntil = 0;
    this.leave.textContent = "Leave class";
    this.leave.classList.remove("danger");
  }
}

/** The panel's phase line and hint for `v`. */
export function phaseWords(v: LessonView): { phase: string; hint: string } {
  const left = formatCountdown(v.phaseLeftMs);
  switch (v.phase.kind) {
    case "gathering":
      return { phase: `The lesson begins in ${left}`, hint: "Find a place facing the professor. The rounds start once the door has closed." };
    case "demonstration":
      if (v.flying) return { phase: "Watch the course", hint: "Madam Swift shows the way round the rings. Practise steering while you wait." };
      return v.picking
        ? { phase: "Listen to the professor", hint: "The professor sets the task; the choices come on the beat." }
        : { phase: "Watch the demonstration", hint: "The professor shows the movement; the pad draws it with them." };
    case "practice":
      return {
        phase: v.roundsTotal ? `Round ${v.phase.round} of ${v.roundsTotal}` : `Round ${v.phase.round}`,
        hint: v.flying ? "Fly through each ring as it glows, on the beat. W and S set your speed, A and D, the arrow keys or the mouse steer, Space climbs, Shift dives." : v.picking ? "Choose each answer as the ring closes on the card: click it, or press 1-6." : "Trace each gesture on the pad as the ring closes on its start dot.",
      };
    case "results":
      return { phase: "Results", hint: "The professor is handing out marks. You may stay and chat until the bell." };
    case "ended":
      return { phase: "The lesson is over", hint: "Everyone heads back to the corridor." };
  }
}

// ------------------------------------------------------------------ results

export interface ResultView {
  className: string;
  scorePct: number;
  grade: Grade;
  rewards: ClassRewards;
  /** The discipline whose skill XP grew ("Charms"). */
  discipline: string;
  houseName: string;
  /** Names of the spells learned, with how to cast them. */
  spells: { name: string; how: string }[];
  /** Items handed out, with their names. */
  items: { itemDef: number; name: string; quantity: number }[];
  /** Recipes taught, by name. */
  recipes: string[];
  /** The lore entry unlocked: words for it until the server sends its title (`setLore`); null: none. */
  lore: string | null;
  /** A fortune drawn: its name and what it does. */
  fortune: { name: string; blurb: string } | null;
  /** Whether a lesson that needs answers got too few to earn marks. */
  picking: boolean;
  /** The lesson's beats were rings to fly through. */
  flying?: boolean;
}

const GRADE_TITLES: Record<Grade, string> = { perfect: "Excellent!", great: "Very good", good: "Passed", miss: "Keep practising" };

export class ResultsCard {
  readonly root: HTMLElement;
  private origin: HTMLElement | null = null;
  private readonly loreTitle = h("span", { class: "results-lore-title" }, "");

  /** `onOpen`: the card opened (true) or closed (false); the level-up flourish waits for it. */
  constructor(
    private readonly onLeave: () => void,
    private readonly onOpen: (open: boolean) => void = () => undefined,
  ) {
    this.root = h("section", { class: "class-results panel", role: "dialog", "aria-label": "Lesson results", hidden: true });
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  show(r: ResultView): void {
    this.origin = focusOrigin();
    this.loreTitle.textContent = r.lore ?? "";
    // The same lines for a retried reward as for the first (classes-results.ts).
    const rewards = h("ul", { class: "results-rewards" }, ...rewardLines(r).map((l) => this.line(l)));
    const stay = h("button", { type: "button", class: "primary", onclick: () => this.close() }, "Stay a while");
    this.root.replaceChildren(
      h("p", { class: "results-kicker" }, `${r.className} · lesson results`),
      h("strong", { class: `results-grade ${r.grade}` }, GRADE_TITLES[r.grade]),
      h("p", { class: "results-score" }, `${r.scorePct}% of the marks`),
      rewards.childElementCount ? rewards : h("p", { class: "muted-line" }, r.flying ? "Fly through a few more rings next time to earn your marks." : `${r.picking ? "Answer" : "Trace"} a few more beats next time to earn your marks.`),
      h("div", { class: "results-actions" }, stay, h("button", { type: "button", class: "secondary", onclick: () => (this.close(), this.onLeave()) }, "Leave class")),
    );
    this.root.hidden = false;
    stay.focus({ preventScroll: true });
    this.onOpen(true);
  }

  private line(l: RewardLine): HTMLElement {
    switch (l.kind) {
      case "xp":
        return h("li", {}, h("strong", {}, l.text));
      case "cap":
      case "hint":
        return h("li", { class: "results-note" }, l.text);
      case "skill":
        return h("li", {}, l.text);
      case "points":
        return h("li", { class: "results-points" }, l.text);
      case "crowns":
        return h("li", { class: "results-crowns results-items" }, coinIcon(), l.text);
      case "item":
        return h("li", { class: "results-items" }, itemIcon(l.itemDef ?? 0), l.text);
      case "recipe":
        return h("li", { class: "results-recipe" }, h("strong", {}, l.text), l.note ?? "");
      case "spell":
        return h("li", { class: "results-spell" }, h("strong", {}, l.text), h("span", {}, l.note ?? ""));
      case "lore":
        return h("li", { class: "results-lore" }, h("strong", {}, l.text), this.loreTitle, h("span", { class: "results-note" }, " (your lore book, /lore)"));
      case "fortune":
        return h("li", { class: "results-fortune" }, h("strong", {}, l.text), h("span", { class: "results-note" }, l.note ?? ""));
    }
  }

  /** The new lore entry's title, once the server has sent it (the card first says "a new page"). */
  setLore(title: string): void {
    if (title) this.loreTitle.textContent = title;
  }

  close(): void {
    if (this.root.hidden) return;
    this.root.hidden = true;
    restoreFocus(this.origin, this.root);
    this.onOpen(false);
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.close();
      return true;
    }
    return false;
  }
}
