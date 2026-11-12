// The pick tray of a class: the beat's prompt ("Valerian
// Sprigs", "Teacup → giant teacup") in a card with the approach ring
// closing on it, and 2-6 options as buttons with their icons. Pick on the
// beat, when the ring meets the card: click, tap, or press 1-6.
//
//   ┌──────────────────────────────┐
//   │ Round 2 · Brew        in 1.2 s│  caption, countdown
//   │      ◯ [icon] Valerian ◯      │  the prompt, the ring closing on it
//   │  [1 Oak Galls] [2 Ginger]     │  the options
//   │  [3 Valerian ] [4 Madder]     │
//   │            Great! 92          │  feedback
//   └──────────────────────────────┘
//   ● ● ◐ ○ ○                         one pip per beat of the round
//
// Looks (`PickLook`): a question shown large with no icon
// (History of Magic), options as words alone when their icons tell nothing,
// and Divination's close-up: the symbol in a teacup's leaves or in a
// crystal ball's mist.
//
// The tray never knows the answer: it hands the choice to its owner
// (game/classes), which previews the timing locally (`PickScorer`) and
// sends it; the server's verdict comes back as `beatScored`.

import "./classes.css";
import { itemIcon } from "../economy/icons";
import type { PickItem } from "../game/classes/picks";
import type { Grade } from "../wasm/sim_wasm";
import type { PadPip } from "./classes-pad";
import { h } from "./dom";
import { lineIcon } from "./effects-icons";
import { assetShipped, assetUrl } from "../assets";

/** How a class's picks look; every field off is the plain tray. */
export interface PickLook {
  /** The prompt is a question to read: large words, no icon (History of Magic). */
  question?: boolean;
  /** The options are words alone (their icons are all the same). */
  labelOnly?: boolean;
  /** Divination's close-up of the prompt: the symbol in a teacup or a crystal ball. */
  closeup?: "cup" | "ball" | null;
}

/** What the tray shows this frame. */
export interface PickScene {
  prompt: PickItem | null;
  options: readonly PickItem[];
  look?: PickLook;
  /** The approach ring: 1 far off, 0 the beat is now (null: no ring). */
  approach: number | null;
  /** The beat's window is open: a pick now counts. */
  open: boolean;
  /** The ring is meeting the card: the moment to pick (full marks). */
  onBeat?: boolean;
  /** The option already picked for this beat (a position), if any. */
  chosen: number | null;
  caption: string;
  countdown: string;
  pips: PadPip[];
}

export interface PickTrayOptions {
  /** Option `index` (a position in the scene's options) was picked at local time `t` (ms). */
  onChoose: (index: number, t: number) => void;
}

const GRADE_WORDS: Record<Grade, string> = { miss: "Miss", good: "Good", great: "Great!", perfect: "Perfect!" };
/** Icons that failed to load (the generated glyph stays). */
const missing = new Set<string>();

/** Generated glyphs for the class icons until their art exists (`icons/class/<name>`). */
const GLYPHS: Record<string, string[]> = {
  teacup: ["M5 9h11v4a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5Z", "M16 10.5h1.5a2 2 0 0 1 0 4H16", "M4 20.5h14"],
  key: ["M8 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z", "M11.5 12H21", "M17.5 12v3", "M20 12v2.5"],
  quill: ["M19.5 3.5C12 5 7.5 10 5.5 18.5", "M19.5 3.5c-1 5-4.5 9-10 11", "M4.5 20.5l1-2"],
  candle: ["M9 9.5h6V21H9Z", "M12 9.5V7.5", "M12 2.5c1.5 1.6 1.5 3.2 0 4.5-1.5-1.3-1.5-2.9 0-4.5Z"],
  spoon: ["M12 3a3 4.5 0 1 0 0 9 3 4.5 0 0 0 0-9Z", "M12 12v9"],
  button: ["M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z", "M10 10h.01M14 10h.01M10 14h.01M14 14h.01"],
  matchbox: ["M4 8h16v9H4Z", "M4 11h16", "M8 8V6h8v2"],
  pincushion: ["M5 13a7 5 0 0 0 14 0Z", "M8 12l-1-5M12 11.5V6M16 12l1-5"],
  change_size: ["M4 20 10 14", "M4 15v5h5", "M20 4l-6 6", "M20 9V4h-5"],
  change_material: ["M12 3 20 7.5v9L12 21l-8-4.5v-9Z", "M4 7.5 12 12l8-4.5", "M12 12v9"],
  change_colour: ["M12 3.5s6 6.5 6 10.5a6 6 0 0 1-12 0c0-4 6-10.5 6-10.5Z"],
  change_shape: ["M3.5 4.5h7v7h-7Z", "M17 13a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z", "M13 6.5h5.5V12"],
  // Divination's symbols, until their art exists.
  sym_key: ["M8 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z", "M11.5 12H21", "M17.5 12v3", "M20 12v2.5"],
  sym_bird: ["M3 11c2.5-2.5 5.5-2.5 9 1 3.5-3.5 6.5-3.5 9-1", "M12 12v3.5"],
  sym_moon: ["M15 4a8 8 0 1 0 5 13.5A7 7 0 0 1 15 4Z"],
  sym_ladder: ["M8 3v18", "M16 3v18", "M8 7h8", "M8 12h8", "M8 17h8"],
  sym_anchor: ["M12 4a2 2 0 1 1 0 4 2 2 0 0 1 0-4Z", "M12 8v12", "M8 11h8", "M5 14c0 4 3 6 7 6s7-2 7-6"],
  sym_lantern: ["M9 7h6l1 3v7l-1 2H9l-1-2v-7Z", "M10 7V4.5h4V7", "M12 11v4"],
  sym_snail: ["M13 17a5 5 0 1 1 5-5c0 2-1.5 3-3 3s-2.5-1-2.5-2.5", "M3 18h16", "M19 12l1.5-3", "M20.5 12.5 22 10"],
  sym_bridge: ["M3 16h18", "M3 16c3-6 15-6 18 0", "M3 12v8", "M21 12v8", "M8 13v3M12 12v4M16 13v3"],
  sym_star: ["M12 3l2.6 5.6 6.1.6-4.6 4.1 1.3 6-5.4-3.1-5.4 3.1 1.3-6-4.6-4.1 6.1-.6Z"],
  sym_acorn: ["M6 10c0-3 3-5 6-5s6 2 6 5Z", "M7 10c0 5 2.5 9 5 9s5-4 5-9", "M12 5V3"],
  // Herbology's plants and tools, the creatures' needs and their care, until their art exists.
  plant_vine: ["M7 21h10l-1-5H8Z", "M12 16c0-4-3-5-3-8s3-4 5-3", "M14 5l3-1-1 3", "M10 11c-2 0-3-1-3-2"],
  plant_root: ["M6 21h12l-1.5-7h-9Z", "M9 14c0-3 1.5-5 3-5s3 2 3 5", "M10.5 11.5h.01M13.5 11.5h.01", "M12 9V6M12 6c-2-1-3-3-2-4M12 6c2-1 3-3 2-4"],
  plant_briar: ["M12 21V9", "M12 13l-4-3M12 11l4-3M12 16l-4-2M12 15l4-2", "M8 10l-1.5-.5M16 8l1.5-.5M8 14l-1.5.5M16 13l1.5.5", "M9 21h6"],
  plant_fern: ["M12 21c0-7 2-12 6-16", "M13 16c-3 0-5-1-6-3M14 12c-3-.5-4.5-2-5-4M16 8.5c-2-.5-3-2-3.5-3.5", "M13.5 15c2 .5 4 0 5-1M15 11c2 0 3.5-.5 4.5-2"],
  tool_water: ["M5 10h10v8a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2Z", "M15 12l5-4", "M20 8l1.5 1M19 6.5l1.5 1", "M6 10c0-3 2-5 4-5s4 2 4 5"],
  tool_shears: ["M4.5 17a2.5 2.5 0 1 0 5 0 2.5 2.5 0 0 0-5 0Z", "M14.5 17a2.5 2.5 0 1 0 5 0 2.5 2.5 0 0 0-5 0Z", "M8.5 15 16 4", "M15.5 15 8 4"],
  tool_earmuffs: ["M5 15a3 3 0 1 0 6 0 3 3 0 0 0-6 0Z", "M13 15a3 3 0 1 0 6 0 3 3 0 0 0-6 0Z", "M6 13c0-5 3-8 6-8s6 3 6 8"],
  tool_stake: ["M12 3v18", "M10 21h4", "M12 8c-3 0-4 2-4 4M12 13c3 0 4 2 4 4", "M10.5 10.5l3-1"],
  tool_gloves: ["M8 21v-6l-2-4 1-1 2 2V5a1 1 0 0 1 2 0v6V4a1 1 0 0 1 2 0v7V5a1 1 0 0 1 2 0v6V7a1 1 0 0 1 2 0v8l-2 6Z", "M8 18h8"],
  tool_compost: ["M5 11h14l-1.5 9h-11Z", "M8 11c0-2 2-3 4-3s4 1 4 3", "M9 15h.01M12 16h.01M15 14.5h.01"],
  tool_shade: ["M3 10c3-4 15-4 18 0Z", "M12 10v10", "M6 10v2M18 10v2", "M9 20h6"],
  tool_duster: ["M12 13v8", "M10 21h4", "M12 13c-4-1-6-5-5-9 2 2 3 4 5 5 2-1 3-3 5-5 1 4-1 8-5 9Z"],
  need_hungry: ["M5 11h14a7 7 0 0 1-14 0Z", "M9 7c0-1.5 1-2 1-3M12 7c0-1.5 1-2 1-3M15 7c0-1.5 1-2 1-3"],
  need_thirsty: ["M12 3.5s6 6.5 6 10.5a6 6 0 0 1-12 0c0-4 6-10.5 6-10.5Z", "M9 14a3 3 0 0 0 3 3"],
  need_scared: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z", "M9 10h.01M15 10h.01", "M8.5 16c1-1 2-1 3.5 0s2.5 1 3.5 0"],
  need_muddy: ["M4 17c2-2 4-2 6 0s4 2 6 0 4-2 4-2", "M4 12a2 2 0 1 0 4 0 2 2 0 0 0-4 0Z", "M9.5 9a2.5 2.5 0 1 0 5 0 2.5 2.5 0 0 0-5 0Z", "M16 12a1.5 1.5 0 1 0 3 0 1.5 1.5 0 0 0-3 0Z"],
  need_bored: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z", "M8 10h3M13 10h3", "M9 15.5h6"],
  need_cold: ["M12 3v18M4.5 7.5l15 9M19.5 7.5l-15 9", "M10 4.5l2 2 2-2M10 19.5l2-2 2 2"],
  need_hurt: ["M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.5-7 10-7 10Z", "M10 12h4M12 10v4"],
  care_feed: ["M4 13h16a8 6 0 0 1-16 0Z", "M8 12c0-1.5 1-2.5 2-2.5s2 1 2 2.5M12 12c0-2 1.5-3 3-3"],
  care_water: ["M4 13h16a8 6 0 0 1-16 0Z", "M12 4.5s3 3.2 3 5a3 3 0 0 1-6 0c0-1.8 3-5 3-5Z"],
  care_hum: ["M9 18V6l10-2v12", "M5 18a2 2 0 1 0 4 0 2 2 0 0 0-4 0Z", "M15 16a2 2 0 1 0 4 0 2 2 0 0 0-4 0Z"],
  care_hand: ["M7 21v-5l-2-4 1-1 2 2V5a1 1 0 0 1 2 0v6V4a1 1 0 0 1 2 0v7V5a1 1 0 0 1 2 0v6V7a1 1 0 0 1 2 0v8l-2 6Z"],
  care_brush: ["M4 9h16v4H4Z", "M6 13v3M9 13v4M12 13v3M15 13v4M18 13v3", "M8 9V6h8v3"],
  care_ball: ["M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z", "M4.5 10c5 2 10 2 15 0", "M6 17c4-3 8-3 12 0"],
  care_blanket: ["M4 6h16v12H4Z", "M4 10h16M4 14h16", "M8 6v12M16 6v12"],
  care_paw: ["M12 20c-3 0-5-2-5-4s2.5-4 5-4 5 2 5 4-2 4-5 4Z", "M5.5 9a1.5 2 0 1 0 3 0 1.5 2 0 0 0-3 0Z", "M9 6.5a1.5 2 0 1 0 3 0 1.5 2 0 0 0-3 0Z", "M12 6.5a1.5 2 0 1 0 3 0 1.5 2 0 0 0-3 0Z", "M15.5 9a1.5 2 0 1 0 3 0 1.5 2 0 0 0-3 0Z"],
};

/** The glyph names drawn while an icon's art is missing (tests check every class icon has one). */
export function hasGlyph(icon: string): boolean {
  return (icon.split("/").pop() ?? "") in GLYPHS;
}

/** Divination's close-ups (palette.json colours: ivory cup, chestnut tea, bronze rims; lavender and sky glass). */
const CLOSEUP_SVG: Record<"cup" | "ball", string> = {
  cup: '<svg viewBox="0 0 100 80" aria-hidden="true"><ellipse class="cu-saucer" cx="50" cy="68" rx="44" ry="9"/><path class="cu-body" d="M14 30Q18 66 50 66Q82 66 86 30Z"/><path class="cu-handle" d="M85 36q13 0 13 10t-15 12"/><ellipse class="cu-rim" cx="50" cy="30" rx="36" ry="11"/><ellipse class="cu-tea" cx="50" cy="31" rx="32" ry="8.5"/></svg>',
  ball: '<svg viewBox="0 0 100 80" aria-hidden="true"><path class="cb-stand" d="M28 76h44l-7-12H35Z"/><circle class="cb-glass" cx="50" cy="35" r="30"/><circle class="cb-mist" cx="50" cy="37" r="24"/><ellipse class="cb-shine" cx="39" cy="22" rx="8" ry="4.5"/></svg>',
};

/** Divination's close-up: the symbol (the prompt's icon) in a teacup's leaves or a crystal ball's mist. */
export function closeupEl(kind: "cup" | "ball", symbol: PickItem): HTMLElement {
  const wrap = h("span", { class: `pick-closeup ${kind}`, "aria-hidden": "true" });
  wrap.innerHTML = CLOSEUP_SVG[kind];
  wrap.append(h("span", { class: "pick-closeup-symbol" }, pickIcon(symbol)));
  return wrap;
}

/** The icon of a pick: a catalog item's own icon, the class art when it exists, else a generated glyph. */
export function pickIcon(p: PickItem): HTMLElement {
  if (p.item !== undefined) return itemIcon(p.item);
  const wrap = h("span", { class: "item-icon item-icon-small pick-glyph", "aria-hidden": "true" });
  const name = p.icon.split("/").pop() ?? "";
  wrap.append(lineIcon(GLYPHS[name] ?? GLYPHS.change_shape!, "pick-svg"));
  // Not requested when the build ships no art for it (the glyph stays).
  if (p.icon && !missing.has(p.icon) && assetShipped(`assets/${p.icon}.png`) !== false) {
    const img = document.createElement("img");
    img.alt = "";
    img.decoding = "async";
    // With an Origin header, like every request for a bucket asset (docs/deploy.md, "Assets from a bucket").
    img.crossOrigin = "anonymous";
    img.onload = () => wrap.replaceChildren(img);
    img.onerror = () => missing.add(p.icon);
    img.src = assetUrl(`assets/${p.icon}.png`);
  }
  return wrap;
}

export class PickTray {
  readonly root: HTMLElement;
  private readonly caption: HTMLElement;
  private readonly countdown: HTMLElement;
  private readonly card: HTMLElement;
  private readonly ring: HTMLElement;
  private readonly optionsEl: HTMLElement;
  private readonly feedbackEl: HTMLElement;
  private readonly pipsEl: HTMLElement;
  private readonly live: HTMLElement;
  private buttons: HTMLButtonElement[] = [];
  private beatKey = "";
  private pipKey = "";
  private scene: PickScene | null = null;
  private feedbackAt = 0;

  constructor(private readonly opts: PickTrayOptions) {
    this.caption = h("span", { class: "pad-caption" }, "");
    this.countdown = h("span", { class: "pad-countdown" }, "");
    this.ring = h("span", { class: "pick-ring", "aria-hidden": "true" });
    this.card = h("div", { class: "pick-card" });
    this.optionsEl = h("div", { class: "pick-options", role: "group", "aria-label": "Choices" });
    this.feedbackEl = h("p", { class: "pad-feedback", "aria-hidden": "true" });
    this.pipsEl = h("ol", { class: "pad-pips", "aria-label": "Beats of this round" });
    this.live = h("p", { class: "sr-only", "aria-live": "polite" });
    this.root = h(
      "section",
      { class: "trace-pad pick-tray panel", "aria-label": "Lesson choices", hidden: true },
      h("header", { class: "pad-head" }, this.caption, this.countdown),
      h("div", { class: "pick-stage" }, this.ring, this.card, this.feedbackEl),
      this.optionsEl,
      this.pipsEl,
      h("p", { class: "pad-help" }, "Choose when the ring meets the card · keys 1-6"),
      this.live,
    );
  }

  get visible(): boolean {
    return !this.root.hidden;
  }

  /** Shows `scene` (null hides the tray). */
  show(scene: PickScene | null): void {
    this.scene = scene;
    if (!scene) {
      this.root.hidden = true;
      this.beatKey = "";
      return;
    }
    this.root.hidden = false;
    if (this.caption.textContent !== scene.caption) this.caption.textContent = scene.caption;
    if (this.countdown.textContent !== scene.countdown) this.countdown.textContent = scene.countdown;
    this.root.classList.toggle("open", scene.open);
    this.root.classList.toggle("now", !!scene.onBeat);
    const look = scene.look;
    const key = `${scene.prompt?.key ?? ""}|${scene.options.map((o) => o.key).join(",")}|${look?.question ? "q" : ""}${look?.labelOnly ? "l" : ""}${look?.closeup ?? ""}`;
    if (key !== this.beatKey) {
      this.beatKey = key;
      this.renderBeat(scene);
    }
    this.buttons.forEach((b, i) => {
      b.classList.toggle("chosen", scene.chosen === i);
      b.disabled = scene.chosen !== null || !scene.prompt;
    });
    const a = scene.approach;
    if (a === null || !scene.prompt) {
      this.ring.style.opacity = "0";
    } else {
      this.ring.style.opacity = String(scene.onBeat ? 1 : 0.35 + 0.65 * (1 - a));
      this.ring.style.transform = `translate(-50%, -50%) scale(${(1 + a * 1.6).toFixed(3)})`;
    }
    this.renderPips(scene.pips);
    if (this.feedbackAt && performance.now() - this.feedbackAt > 1400) {
      this.feedbackAt = 0;
      this.feedbackEl.classList.remove("pop");
    }
  }

  private renderBeat(scene: PickScene): void {
    const p = scene.prompt;
    const look = scene.look ?? {};
    const closeup = p && look.closeup ? closeupEl(look.closeup, p) : null;
    // A new beat's card puts the last grade away: it popped over its own card, and over the next
    // prompt (Divination's symbol line, History's question) it hid the words to read.
    this.feedbackAt = 0;
    this.feedbackEl.classList.remove("pop");
    const art = !p ? [] : closeup ? [closeup] : look.question ? [] : [pickIcon(p)];
    this.card.replaceChildren(...(p ? [...art, h("span", { class: "pick-prompt" }, p.label)] : [h("span", { class: "pick-prompt muted" }, "Wait for the next round")]));
    this.card.classList.remove("swapped");
    this.card.classList.toggle("question", !!p && !!look.question);
    this.card.classList.toggle("closeup", !!closeup);
    this.root.classList.toggle("question", !!look.question);
    this.optionsEl.classList.toggle("label-only", !!look.labelOnly);
    this.buttons = scene.options.map((o, i) =>
      h(
        "button",
        { type: "button", class: "pick-option", "aria-keyshortcuts": String(i + 1), onclick: () => this.choose(i) },
        h("span", { class: "pick-key", "aria-hidden": "true" }, String(i + 1)),
        look.labelOnly ? null : pickIcon(o),
        h("span", { class: "pick-label" }, o.label),
      ),
    );
    this.optionsEl.replaceChildren(...this.buttons);
    this.optionsEl.classList.toggle("wide", scene.options.length > 4);
    if (p) this.live.textContent = `${p.label}. ${scene.options.map((o, i) => `${i + 1}: ${o.label}`).join(", ")}.`;
  }

  private choose(index: number): void {
    const s = this.scene;
    if (!s || !s.prompt || s.chosen !== null || index >= s.options.length) return;
    this.opts.onChoose(index, performance.now());
  }

  /** Keys 1-6 pick while the tray shows. */
  handleKey(e: KeyboardEvent): boolean {
    if (this.root.hidden || e.ctrlKey || e.metaKey || e.altKey || e.repeat) return false;
    const m = /^(?:Digit|Numpad)([1-6])$/.exec(e.code);
    if (!m) return false;
    this.choose(Number(m[1]) - 1);
    return true;
  }

  /** A grade (or a refusal) pops up over the card and is read out. */
  flash(text: string, tone: Grade | "reject" | "info"): void {
    this.feedbackAt = performance.now();
    this.feedbackEl.textContent = text;
    this.feedbackEl.className = `pad-feedback ${tone} pop`;
    this.live.textContent = text;
  }

  /** Says something to screen readers without a pop-up (the next beat). */
  announce(text: string): void {
    this.live.textContent = text;
  }

  /**
   * The prompt card shows its change made (Transfiguration's object swapping shape). `promptKey`
   * names the beat's prompt: the verdict can arrive after the card has moved on to the next beat,
   * whose prompt must not be overwritten.
   */
  swap(text: string, promptKey?: string): void {
    if (promptKey !== undefined && !this.beatKey.startsWith(`${promptKey}|`)) return;
    this.card.classList.remove("swapped");
    // Restart the animation.
    void this.card.offsetWidth;
    this.card.classList.add("swapped");
    const label = this.card.querySelector(".pick-prompt");
    if (label && text) label.textContent = text;
  }

  static gradeText(grade: Grade, score: number): string {
    return grade === "miss" ? `Miss (${score})` : `${GRADE_WORDS[grade]} ${score}`;
  }

  private renderPips(pips: readonly PadPip[]): void {
    const key = pips.map((p) => `${p.status}${p.grade ?? ""}${p.rejected ? "r" : ""}`).join(",");
    if (key === this.pipKey) return;
    this.pipKey = key;
    this.pipsEl.replaceChildren(...pips.map((p) => h("li", { class: `pip ${p.status}${p.grade ? ` ${p.grade}` : ""}${p.rejected ? " rejected" : ""}`, "aria-label": p.status })));
  }
}
