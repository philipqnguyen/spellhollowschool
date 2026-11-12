// The movement chip on the HUD (docs/architecture.md "Client"):
// "Auto run · R or S to stop" while auto run is on, and for a
// moment after Z, the new pace ("Walking · Z to run"). It looks like the
// flight chip (flight.css) and sits above it while flying.

import "./flight.css";
import { h } from "./dom";

/** How long Z's "Walking" / "Running" stays up (ms). */
export const PACE_FLASH_MS = 1600;

/** The chip's words: auto run (with the pace when walking), else a recent Z's pace, else nothing. */
export function moveChipText(autoRun: boolean, walking: boolean, flashing: boolean): string | null {
  if (autoRun) return walking ? "Auto run, walking · R or S to stop" : "Auto run · R or S to stop";
  if (flashing) return walking ? "Walking · Z to run" : "Running · Z to walk";
  return null;
}

export class MoveChip {
  readonly root: HTMLElement;
  private readonly text: HTMLElement;
  private shown = "";
  private flashUntil = 0;

  constructor() {
    this.text = h("span", { class: "flight-text" }, "");
    this.root = h("div", { class: "flight-chip move-chip panel", role: "status", "aria-live": "polite", hidden: true }, h("span", { class: "flight-mark", "aria-hidden": "true" }), this.text);
  }

  /** Z was pressed: show the pace for a moment. */
  flashPace(nowMs: number): void {
    this.flashUntil = nowMs + PACE_FLASH_MS;
  }

  /** Per frame (cheap when nothing changes); `stacked` lifts it over the flight chip. */
  update(nowMs: number, autoRun: boolean, walking: boolean, stacked: boolean): void {
    const text = moveChipText(autoRun, walking, nowMs < this.flashUntil);
    const key = `${text ?? ""}|${stacked}`;
    if (key === this.shown) return;
    this.shown = key;
    this.root.hidden = !text;
    this.root.classList.toggle("stacked", stacked);
    if (text) this.text.textContent = text;
  }
}
