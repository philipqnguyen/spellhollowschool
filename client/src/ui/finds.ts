// The discovery toast for exploration finds (`FindResult`): what you found
// behind the tapestry, with a little burst of light, or a quieter line when
// there was nothing (or you had already searched there today). Toasts stack
// at the bottom centre and fade on their own.

import "./finds.css";
import type { FindOutcome } from "../wasm/sim_wasm";
import { h } from "./dom";
import { findMessage } from "./finds-state";

const SHOW_MS = { found: 6500, nothing: 3500, cooldown: 3500, failed: 3500 } as const;
const MAX_TOASTS = 3;

export class FindToasts {
  readonly root: HTMLElement;
  private readonly timers = new Set<ReturnType<typeof setTimeout>>();

  constructor() {
    this.root = h("div", { class: "find-toasts", "aria-live": "polite" });
  }

  show(outcome: FindOutcome, placeName: string | null): void {
    const m = findMessage(outcome);
    const el = h(
      "div",
      { class: `find-toast panel ${m.tone}`, role: "status" },
      m.tone === "found" ? h("span", { class: "find-burst", "aria-hidden": "true" }) : null,
      h("strong", {}, m.title),
      placeName && m.tone === "found" ? h("span", { class: "find-place" }, placeName) : null,
      h("ul", {}, ...m.lines.map((l) => h("li", {}, l))),
    );
    this.root.append(el);
    while (this.root.children.length > MAX_TOASTS) this.root.firstElementChild?.remove();
    const t = setTimeout(() => {
      this.timers.delete(t);
      el.classList.add("leaving");
      const t2 = setTimeout(() => {
        this.timers.delete(t2);
        el.remove();
      }, 400);
      this.timers.add(t2);
    }, SHOW_MS[m.tone]);
    this.timers.add(t);
  }

  dispose(): void {
    for (const t of this.timers) clearTimeout(t);
    this.timers.clear();
    this.root.replaceChildren();
  }
}
