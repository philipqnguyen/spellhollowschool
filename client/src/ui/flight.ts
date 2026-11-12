// Brooms on the HUD: the flight chip ("Flying, 34 m up, land to
// dismount") while mounted, and the Flying class's ring cue ("Ring 3: in
// 1.2 s", "Now!", the mark of the last ring). The words are chosen in
// game/flight/flight.ts and game/classes/controller.ts; these only show them.

import "./flight.css";
import { h } from "./dom";

export class FlightChip {
  readonly root: HTMLElement;
  private readonly text: HTMLElement;
  private shown = "";

  constructor() {
    this.text = h("span", { class: "flight-text" }, "");
    this.root = h("div", { class: "flight-chip panel", role: "status", "aria-live": "polite", hidden: true }, h("span", { class: "flight-mark", "aria-hidden": "true" }), this.text);
  }

  /** The chip's words, or null to hide it. */
  show(text: string | null): void {
    const key = text ?? "";
    if (key === this.shown) return;
    this.shown = key;
    this.root.hidden = !text;
    if (text) this.text.textContent = text;
  }
}

/** What the ring cue shows: a caption, a countdown or "Now!", and a result flash. */
export interface RingCueView {
  caption: string;
  countdown: string;
  /** The ring's window is open. */
  open: boolean;
  /** The last ring's mark ("Perfect!", "Missed"), with its grade class, or null. */
  flash: { text: string; kind: string } | null;
}

export class RingCue {
  readonly root: HTMLElement;
  private readonly caption = h("span", { class: "ring-cue-caption" }, "");
  private readonly countdown = h("strong", { class: "ring-cue-count" }, "");
  private readonly flash = h("span", { class: "ring-cue-flash" }, "");
  private key = "";

  constructor() {
    this.root = h("div", { class: "ring-cue panel", role: "status", "aria-live": "polite", hidden: true }, this.caption, this.countdown, this.flash);
  }

  show(v: RingCueView | null): void {
    const key = v ? `${v.caption}|${v.countdown}|${v.open}|${v.flash?.text ?? ""}|${v.flash?.kind ?? ""}` : "";
    if (key === this.key) return;
    this.key = key;
    this.root.hidden = !v;
    if (!v) return;
    this.caption.textContent = v.caption;
    this.countdown.textContent = v.countdown;
    this.countdown.hidden = !v.countdown;
    this.root.classList.toggle("open", v.open);
    this.flash.textContent = v.flash?.text ?? "";
    this.flash.hidden = !v.flash;
    this.flash.className = `ring-cue-flash${v.flash ? ` ${v.flash.kind}` : ""}`;
  }
}
