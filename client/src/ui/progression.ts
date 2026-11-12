// Your level on the HUD: a badge in the top bar with the level, the school
// year and an XP bar (it opens the spellbook), little "+80 XP" notes rising
// from it after each grant, and the level-up flourish across the screen.
// The numbers come from the server's `Progress`; the words from
// progression-state.ts.

import "./progression.css";
import type { Progress, XpGain } from "../wasm/sim_wasm";
import { h } from "./dom";
import { FlourishGate, gainText, levelFraction, xpText, yearName, type LevelUp } from "./progression-state";

/** How long a gain note and the flourish stay up (ms). */
const GAIN_MS = 2600;
const FLOURISH_MS = 4200;
const GAINS_MAX = 3;

export class LevelBadge {
  readonly root: HTMLButtonElement;
  private readonly level: HTMLElement;
  private readonly year: HTMLElement;
  private readonly fill: HTMLElement;
  private readonly bar: HTMLElement;
  private readonly gains: HTMLElement;
  private key = "";

  constructor(onOpen: () => void) {
    this.level = h("span", { class: "lvl-num" }, "1");
    this.year = h("span", { class: "lvl-year" }, "");
    this.fill = h("span", { class: "lvl-fill" });
    this.bar = h("span", { class: "lvl-bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": 0, "aria-label": "Progress to the next level" }, this.fill);
    this.gains = h("span", { class: "lvl-gains", "aria-live": "polite" });
    this.root = h(
      "button",
      { type: "button", class: "lvl-badge", title: "Your level and spells (Y)", "aria-label": "Level and spellbook", onclick: onOpen },
      this.level,
      h("span", { class: "lvl-text" }, this.year, this.bar),
      this.gains,
    );
    this.root.hidden = true;
  }

  /** The latest `Progress`. */
  update(p: Progress): void {
    const pct = Math.round(levelFraction(p) * 100);
    const key = `${p.level}|${p.year}|${p.xp}|${p.nextLevelXp}`;
    if (key === this.key) return;
    this.key = key;
    this.root.hidden = false;
    this.level.textContent = String(p.level);
    this.year.textContent = yearName(p.year);
    this.fill.style.width = `${pct}%`;
    this.bar.setAttribute("aria-valuenow", String(pct));
    const words = `Level ${p.level}, ${yearName(p.year)}: ${xpText(p)}`;
    this.root.title = `${words}. Your spells (Y).`;
    this.root.setAttribute("aria-label", `${words}. Open the spellbook.`);
  }

  /** A grant: a note rises from the badge. */
  gained(g: XpGain): void {
    const text = gainText(g);
    if (!text) return;
    const note = h("span", { class: `lvl-gain${g.capped ? " capped" : ""}` }, text);
    this.gains.append(note);
    while (this.gains.childElementCount > GAINS_MAX) this.gains.firstElementChild?.remove();
    setTimeout(() => note.remove(), GAIN_MS);
  }
}

/**
 * The level-up flourish: a gilt banner across the top third, gone after a
 * few seconds. It never covers the class results card (`hold`): a level-up
 * while the card is open waits until it closes.
 */
export class LevelUpFlourish {
  readonly root: HTMLElement;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly gate = new FlourishGate();
  /** The level-up on screen now. */
  private playing: LevelUp | null = null;

  constructor() {
    this.root = h("div", { class: "levelup", role: "status", "aria-live": "assertive", hidden: true });
  }

  show(up: LevelUp): void {
    const now = this.gate.offer(up);
    if (now) this.play(now);
  }

  /** The results card opened (the flourish waits; one on screen is taken down to wait too) or closed (a waiting one plays). */
  hold(open: boolean): void {
    if (open) {
      this.gate.hold(true, this.playing);
      this.stop();
      return;
    }
    const up = this.gate.hold(false);
    if (up) this.play(up);
  }

  private stop(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.playing = null;
    this.root.hidden = true;
    this.root.classList.remove("play");
  }

  private play(up: LevelUp): void {
    this.playing = up;
    const lines: (HTMLElement | null)[] = [
      h("span", { class: "levelup-kicker" }, "Level up"),
      h("strong", { class: "levelup-level" }, `Level ${up.level}`),
      h("span", { class: "levelup-year" }, up.newYear ? `You are now a ${yearName(up.year)}!` : yearName(up.year)),
      up.slots !== null ? h("span", { class: "levelup-note" }, `Your dueling loadout now holds ${up.slots} spells.`) : null,
    ];
    this.root.replaceChildren(h("div", { class: "levelup-card" }, h("span", { class: "levelup-rays", "aria-hidden": "true" }), ...lines));
    this.root.hidden = false;
    // Restart the animation when two level-ups arrive back to back.
    this.root.classList.remove("play");
    void this.root.offsetWidth;
    this.root.classList.add("play");
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.root.hidden = true;
      this.timer = null;
      this.playing = null;
    }, FLOURISH_MS);
  }

  dispose(): void {
    if (this.timer !== null) clearTimeout(this.timer);
  }
}
