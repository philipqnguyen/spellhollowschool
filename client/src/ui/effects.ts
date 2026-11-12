// Our own potion effects on the HUD: one chip per active effect (its icon,
// a ring that empties as it runs down, the time left; hover for the potion's
// name and what it does), below the top bar. Babbling and Invisibility have
// nothing to see on yourself, so this is where you notice them.
//
// Spells to level 20: the spells cast on us show here too, named for
// themselves ("Petrified", "Slowed", "Colour Charm", "Your guardian").
//
// Also the Mimic Draught consent prompt: a friend asks to copy your look; you
// allow it or not before it lapses (MIMIC_DRAUGHT_REQUEST_SECS).

import "./effects.css";
import { formatRemaining, SPELL_EFFECT_KINDS, type OwnEffects } from "../game/effects/state";
import { h } from "./dom";
import { EFFECT_ICONS, lineIcon } from "./effects-icons";
import { ConsentQueue, effectBlurb, effectName, fortuneBlurb, fortuneName } from "./potions-state";
import type { EffectKind, FortuneKind } from "../wasm/sim_wasm";
import { formatCountdown } from "./invites-state";
import "./invites.css";

interface Chip {
  el: HTMLElement;
  time: HTMLElement;
  ring: SVGCircleElement;
  shown: string;
}

const RING = 2 * Math.PI * 10;

/** Spells L20: what a spell cast on us is called on its chip, and what it does. */
const SPELL_WORDS: Partial<Record<EffectKind, [string, string]>> = {
  petrified: ["Petrified", "Stiff as a board: you cannot walk until it wears off (the Unweaving ends it)."],
  slowed: ["Slowed", "Slowing Jinx: you move in slow motion for a moment."],
  recoloured: ["Colour Charm", "Your robe shows another colour for a while."],
  guardian: ["Your guardian", "Your silver guardian is at your side. Cast it again to dismiss it."],
};

/** Spells L20: the duel ward's guardian (guarding): it cannot be dismissed. */
const WARD_WORDS: [string, string] = ["Guardian ward", "Your guardian guards you: it stops the next spell that would hit you, then it is gone."];

/** The chip's name and words for `kind` (a spell cast on us, else the potion's); `guarding`: our guardian is a duel ward. */
export function chipWords(kind: EffectKind, guarding = false): [string, string] {
  if (kind === "guardian" && guarding) return WARD_WORDS;
  return SPELL_WORDS[kind] ?? [effectName(kind), effectBlurb(kind)];
}

export class EffectTimers {
  readonly root: HTMLElement;
  private readonly chips = new Map<string, Chip>();
  private revision = -1;
  /** Spells L20: whether the chips were made for a guarding (duel ward) guardian. */
  private guarded = false;

  /** `guarding`: whether our guardian is a duel ward now (its chip says so). */
  constructor(
    private readonly own: OwnEffects,
    private readonly guarding: () => boolean = () => false,
  ) {
    this.root = h("div", { class: "effect-timers", role: "status", "aria-label": "Your effects" });
  }

  /** A few times a second: chips follow our timers. */
  update(nowMs: number): void {
    const view = this.own.view(nowMs);
    const guarded = this.guarding();
    if (this.revision !== this.own.revision || guarded !== this.guarded) {
      this.revision = this.own.revision;
      if (guarded !== this.guarded) {
        // Its words changed: the guardian chip is made again.
        this.guarded = guarded;
        this.chips.get("guardian")?.el.remove();
        this.chips.delete("guardian");
      }
      // A fortune's chip is keyed by which fortune it is (a new one replaces it).
      const keys = new Set(view.map((v) => chipKey(v.kind, v.fortune)));
      for (const [key, chip] of this.chips) {
        if (!keys.has(key)) {
          chip.el.remove();
          this.chips.delete(key);
        }
      }
      for (const v of view) {
        const key = chipKey(v.kind, v.fortune);
        if (!this.chips.has(key)) this.chips.set(key, this.chip(v.kind, v.fortune));
      }
      // Display order (the view is sorted).
      this.root.replaceChildren(...view.map((v) => this.chips.get(chipKey(v.kind, v.fortune))!.el));
    }
    for (const v of view) {
      const chip = this.chips.get(chipKey(v.kind, v.fortune));
      if (!chip) continue;
      const text = formatRemaining(v.remainingSecs);
      if (chip.shown !== text) {
        chip.shown = text;
        chip.time.textContent = text;
        chip.ring.style.strokeDashoffset = String(RING * (1 - v.fraction));
        // A spell's whole run is short: only a potion's last half minute warns.
        chip.el.classList.toggle("ending", v.remainingSecs <= 30 && !SPELL_EFFECT_KINDS.includes(v.kind));
      }
    }
    this.root.hidden = view.length === 0;
  }

  private chip(kind: keyof typeof EFFECT_ICONS, fortune: FortuneKind | null = null): Chip {
    const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    ring.setAttribute("cx", "12");
    ring.setAttribute("cy", "12");
    ring.setAttribute("r", "10");
    ring.setAttribute("class", "effect-ring");
    ring.style.strokeDasharray = String(RING);
    const ringSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    ringSvg.setAttribute("viewBox", "0 0 24 24");
    ringSvg.setAttribute("aria-hidden", "true");
    ringSvg.classList.add("effect-ring-svg");
    ringSvg.append(ring);
    const time = h("span", { class: "effect-time" }, "");
    // A Divination fortune is named for itself, not a potion.
    const [ownName, ownBlurb] = chipWords(kind, this.guarded);
    const name = fortune ? `Fortune: ${fortuneName(fortune)}` : ownName;
    const blurb = fortune ? `${fortuneBlurb(fortune)} Only you can see it.` : ownBlurb;
    const el = h("div", { class: `effect-chip panel effect-${kind}${fortune ? ` fortune-${fortune}` : ""}`, title: `${name}: ${blurb}`, "aria-label": name }, h("span", { class: "effect-icon" }, ringSvg, lineIcon(EFFECT_ICONS[kind], "effect-glyph")), time);
    return { el, time, ring, shown: "" };
  }
}

function chipKey(kind: string, fortune: FortuneKind | null): string {
  return fortune ? `${kind}:${fortune}` : kind;
}

export interface ConsentOptions {
  onAnswer: (requestId: number, accept: boolean) => void;
}

/** Mimic Draught consent prompts, as toasts with a countdown. */
export class MimicDraughtConsent {
  readonly root: HTMLElement;
  private readonly queue = new ConsentQueue();
  private readonly views = new Map<number, { el: HTMLElement; clock: HTMLElement }>();
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly opts: ConsentOptions) {
    this.root = h("div", { class: "toasts consent-toasts", "aria-live": "polite" });
  }

  add(requestId: number, fromName: string, expiresInSecs: number): void {
    const now = performance.now();
    for (const old of this.queue.items) if (old.fromName === fromName) this.drop(old.requestId);
    const r = this.queue.add(requestId, fromName, expiresInSecs, now);
    const clock = h("span", { class: "toast-clock", title: "Time left" }, formatCountdown(Math.ceil((r.expiresAt - now) / 1000)));
    const answer = (accept: boolean) => {
      this.drop(requestId);
      this.opts.onAnswer(requestId, accept);
    };
    const el = h(
      "div",
      { class: "toast panel consent", role: "dialog", "aria-label": `${fromName} asks to copy your look` },
      h("p", {}, h("strong", {}, fromName), " wants to drink a potion that makes them look exactly like you for a while. Your name stays above your own head."),
      h(
        "div",
        { class: "toast-actions" },
        h("button", { type: "button", class: "secondary toast-accept", onclick: () => answer(true) }, "Allow"),
        h("button", { type: "button", class: "secondary", onclick: () => answer(false) }, "No thanks"),
        clock,
      ),
    );
    this.views.set(requestId, { el, clock });
    this.root.append(el);
    this.timer ??= setInterval(() => this.tick(), 500);
  }

  private tick(): void {
    const now = performance.now();
    for (const gone of this.queue.expire(now)) this.drop(gone.requestId);
    for (const r of this.queue.items) {
      const v = this.views.get(r.requestId);
      if (v) v.clock.textContent = formatCountdown(Math.max(0, Math.ceil((r.expiresAt - now) / 1000)));
    }
    if (!this.queue.items.length && this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private drop(requestId: number): void {
    this.queue.remove(requestId);
    this.views.get(requestId)?.el.remove();
    this.views.delete(requestId);
  }

  dispose(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.views.clear();
    this.root.replaceChildren();
  }
}
