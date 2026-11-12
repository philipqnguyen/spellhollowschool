// The duel HUD in the arena (game/dueling/controller.ts drives it): both
// duelists' Poise bars facing each other with the clock between them, the
// countdown, what each duelist is casting (a bar in the spell's colour, so
// a telegraph is readable at a glance), raised shields and disarms, our
// spell bar (keys 1-5, cooldown sweeps, refusals), our cast bar, callouts
// over the duelists' heads ("Blocked!", "-10"), a red flash when we are
// hit, the Forfeit / Leave button and the result card with the XP.
// Protocol v11: status chips under each Poise bar with their time draining
// (Bound, Slowed, Staggered, the Guardian ward), callouts for binds, slows,
// pulls, cleanses and wards, the screen's edge tinted while we are hindered,
// and the Unweaving's key glowing when it would free us.
//
// `render` runs every frame in a duel and only touches the DOM when a shown
// value changed (cached per element), so the HUD costs a few comparisons a
// frame.

import "./duel.css";
import { houseInfo } from "../content/houses";
import { DUEL_RULES, DUEL_SPELLS, duelSpell, kindLabel, spellSummary } from "../game/dueling/content";
import { endText, otherSide, sideIndex, xpText, type DuelView } from "../game/dueling/state";
import type { DuelSide } from "../wasm/sim_wasm";
import { h } from "./dom";

export interface DuelHudOptions {
  /** A spell button was pressed (the same as its key). */
  onCast: (spell: number) => void;
  onForfeit: () => void;
  /** A spectator leaves (or anyone, once the duel is over). */
  onLeave: () => void;
}

/** Callouts per duelist at once; older ones are recycled. */
const CALLOUTS_PER_SIDE = 4;
const CALLOUT_MS = 1100;
/** Callouts this close together (ms) pile up rather than overlap. */
const STACK_MS = 350;
/** A first Forfeit press arms it for this long (ms). */
const FORFEIT_CONFIRM_MS = 3000;

export type CalloutTone = "hit" | "block" | "reflect" | "break" | "info" | "good" | "bind" | "slow" | "pull" | "ward";

/** The status chips under a Poise bar, in the order they show. */
const CHIP_KINDS = ["shield", "ward", "disarmed", "bound", "slowed", "staggered"] as const;
type ChipKind = (typeof CHIP_KINDS)[number];

/** How long each status lasts at most, for its chip's draining bar (ms; the data's numbers). */
const fullMs = (pick: (s: (typeof DUEL_SPELLS)[number]) => number): number => DUEL_SPELLS.reduce((m, s) => Math.max(m, pick(s)), 0) || 1000;
const CHIP_FULL_MS: Record<ChipKind, number> = {
  shield: fullMs((s) => s.shieldMs),
  ward: fullMs((s) => s.wardMs),
  disarmed: fullMs((s) => s.disarmMs),
  bound: fullMs((s) => s.root?.ms ?? 0),
  slowed: fullMs((s) => s.slow?.ms ?? 0),
  staggered: fullMs((s) => s.pull?.staggerMs ?? 0),
};

/** One status chip: its words, and a bar draining to its end (a CSS animation, restarted when the end moves). */
interface ChipEls {
  el: HTMLElement;
  text: HTMLElement;
  drain: HTMLElement;
  shown: { text: string; until: number };
}

/** Seconds left in a chip: tenths under 2 s, whole seconds above. */
export function chipSeconds(ms: number): string {
  const s = Math.max(0, ms) / 1000;
  return s < 2 ? `${s.toFixed(1)} s` : `${Math.ceil(s)} s`;
}

/** A duelist's arena speed while slowed to `pct`% (the stage's own pace times the slow). */
export function slowedSpeedPct(pct: number): number {
  return Math.round((DUEL_RULES.move_speed_pct * pct) / 100);
}

/** The words on a status chip with `left` ms to go. */
export function chipText(kind: ChipKind, left: number, extra: { perfect?: boolean; pct?: number } = {}): string {
  switch (kind) {
    case "shield":
      return extra.perfect ? "Shield Charm: reflecting" : "Shield Charm";
    case "ward":
      return `Guardian ward ${chipSeconds(left)}`;
    case "disarmed":
      return `Disarmed ${chipSeconds(left)}`;
    case "bound":
      return `Bound ${chipSeconds(left)}`;
    case "slowed":
      return `Slowed ${slowedSpeedPct(extra.pct ?? 100)}% · ${chipSeconds(left)}`;
    case "staggered":
      return "Staggered";
  }
}

interface SideEls {
  root: HTMLElement;
  name: HTMLElement;
  fill: HTMLElement;
  lag: HTMLElement;
  num: HTMLElement;
  cast: HTMLElement;
  castFill: HTMLElement;
  castLabel: HTMLElement;
  chips: HTMLElement;
  chip: Record<ChipKind, ChipEls>;
  anchor: HTMLElement;
  callouts: HTMLElement[];
  next: number;
  /** When the last callout came (performance.now()) and how high the current pile is. */
  lastAt: number;
  stack: number;
  shown: { poise: number; cast: string; castPct: number; anchorX: number; anchorY: number; anchorShown: boolean };
}

interface SlotEls {
  spell: number;
  button: HTMLButtonElement;
  sweep: HTMLElement;
  shown: { cd: number; state: string };
}

export class DuelHud {
  readonly root: HTMLElement;
  private readonly sides: [SideEls, SideEls];
  private readonly clock: HTMLElement;
  private readonly countdown: HTMLElement;
  private readonly bar: HTMLElement;
  private readonly castbar: HTMLElement;
  private readonly castFill: HTMLElement;
  private readonly castLabel: HTMLElement;
  private readonly flashEl: HTMLElement;
  private readonly actionButton: HTMLButtonElement;
  private readonly banner: HTMLElement;
  private readonly result: HTMLElement;
  private slots: SlotEls[] = [];
  private view: DuelView | null = null;
  private shownClock = "";
  private shownCountdown = "";
  private shownCast = { label: "", pct: -1, tone: "" };
  /** The hindrance classes on the root (" self-bound self-slowed"). */
  private shownSelf = "";
  private forfeitArmedUntil = 0;
  private resultFor: number | null = null;
  private returnEl: HTMLElement | null = null;
  private shownReturn = -1;

  constructor(private readonly opts: DuelHudOptions) {
    this.sides = [this.side("a"), this.side("b")];
    this.clock = h("div", { class: "duel-clock", "aria-label": "Time left" }, "1:30");
    this.countdown = h("div", { class: "duel-countdown", "aria-live": "assertive", hidden: true });
    this.bar = h("div", { class: "duel-spells", role: "toolbar", "aria-label": "Your spells" });
    this.castFill = h("div", { class: "duel-castbar-fill" });
    this.castLabel = h("span", { class: "duel-castbar-label" });
    this.castbar = h("div", { class: "duel-castbar", hidden: true }, this.castFill, this.castLabel);
    this.flashEl = h("div", { class: "duel-flash", "aria-hidden": "true" });
    this.actionButton = h("button", { type: "button", class: "secondary duel-action", onclick: () => this.onAction() }, "Forfeit");
    this.banner = h("p", { class: "duel-banner panel", role: "status", hidden: true });
    this.result = h("section", { class: "duel-result panel", role: "dialog", "aria-label": "Duel result", hidden: true });
    const top = h("div", { class: "duel-top" }, this.sides[0].root, this.clock, this.sides[1].root);
    this.root = h(
      "div",
      { class: "duel-hud", hidden: true },
      this.flashEl,
      top,
      this.banner,
      this.countdown,
      this.sides[0].anchor,
      this.sides[1].anchor,
      h("div", { class: "duel-bottom" }, this.castbar, this.bar),
      this.actionButton,
      this.result,
    );
  }

  private side(side: DuelSide): SideEls {
    const name = h("span", { class: "duel-name" });
    const fill = h("div", { class: "duel-poise-fill" });
    const lag = h("div", { class: "duel-poise-lag" });
    const num = h("span", { class: "duel-poise-num" });
    const castFill = h("div", { class: "duel-tele-fill" });
    const castLabel = h("span", { class: "duel-tele-label" });
    const cast = h("div", { class: "duel-tele", hidden: true }, castFill, castLabel);
    const chip = {} as Record<ChipKind, ChipEls>;
    for (const kind of CHIP_KINDS) {
      const text = h("span", { class: "duel-chip-text" });
      const drain = h("span", { class: "duel-chip-drain", "aria-hidden": "true" });
      chip[kind] = { el: h("span", { class: `duel-chip ${kind}`, hidden: true }, drain, text), text, drain, shown: { text: "", until: -1 } };
    }
    const chips = h("div", { class: "duel-chips" }, ...CHIP_KINDS.map((k) => chip[k].el));
    const anchor = h("div", { class: `duel-anchor ${side}`, "aria-live": "polite", hidden: true });
    const root = h("div", { class: `duel-side panel ${side}` }, h("div", { class: "duel-who" }, name), h("div", { class: "duel-poise", role: "meter", "aria-label": "Poise" }, lag, fill, num), cast, chips);
    return { root, name, fill, lag, num, cast, castFill, castLabel, chips, chip, anchor, callouts: [], next: 0, lastAt: -1e9, stack: 0, shown: { poise: -1, cast: "", castPct: -1, anchorX: -1, anchorY: -1, anchorShown: false } };
  }

  /** Shows chip `c` with `text` until duel time `until` (null: hides it); its bar drains over what is left of `fullMs`. */
  private setChip(c: ChipEls, text: string | null, until: number, fullMs: number, now: number, extraClass = ""): void {
    const show = text ?? "";
    if (show !== c.shown.text) {
      if (!show) c.el.hidden = true;
      else {
        c.el.hidden = false;
        c.text.textContent = show;
      }
      c.shown.text = show;
    }
    c.el.classList.toggle("perfect", extraClass === "perfect");
    if (!show) {
      c.shown.until = -1;
      return;
    }
    if (until !== c.shown.until) {
      c.shown.until = until;
      const total = Math.max(fullMs, until - now, 1);
      const left = Math.max(0, until - now);
      // A linear drain from full to empty over `total`, started `total - left` ago.
      c.drain.style.animation = "none";
      void c.drain.offsetWidth;
      c.drain.style.animation = `duel-chip-drain ${Math.round(total)}ms linear ${-Math.round(total - left)}ms forwards`;
    }
  }

  get shown(): boolean {
    return !this.root.hidden;
  }

  /** Shows the HUD for a new duel. */
  start(view: DuelView): void {
    this.view = view;
    this.root.hidden = false;
    this.result.hidden = true;
    this.resultFor = null;
    this.returnEl = null;
    this.forfeitArmedUntil = 0;
    this.shownClock = "";
    this.shownCountdown = "";
    this.shownCast = { label: "", pct: -1, tone: "" };
    this.root.classList.remove("self-bound", "self-slowed", "self-staggered");
    this.shownSelf = "";
    this.root.classList.toggle("watching", view.you === null);
    for (const s of ["a", "b"] as const) {
      const els = this.sides[sideIndex(s)];
      const info = view.info(s);
      const house = houseInfo(info.house);
      els.root.style.setProperty("--house", house.colour);
      els.name.replaceChildren(h("span", { class: "duel-crest", "aria-hidden": "true" }), info.name, h("span", { class: "duel-level" }, `Lv ${info.level}`), view.you === s ? h("span", { class: "duel-you" }, "you") : "");
      els.root.classList.toggle("mine", view.you === s);
      els.shown.poise = -1;
      els.shown.cast = "";
      for (const k of CHIP_KINDS) {
        const c = els.chip[k];
        c.el.hidden = true;
        c.shown.text = "";
        c.shown.until = -1;
      }
      for (const c of els.callouts) c.remove();
      els.callouts = [];
    }
    this.banner.hidden = view.you !== null;
    if (view.you === null) this.banner.textContent = `Watching ${view.duelists[0].name} and ${view.duelists[1].name}. You can't cast here; Leave takes you back to the club.`;
    this.actionButton.textContent = view.you ? "Forfeit" : "Leave";
    this.actionButton.classList.remove("armed");
    this.buildSlots(view);
  }

  private buildSlots(view: DuelView): void {
    this.slots = view.loadout.map((spell, i) => {
      const def = duelSpell(spell);
      const sweep = h("span", { class: "duel-slot-sweep", "aria-hidden": "true" });
      const button = h(
        "button",
        {
          type: "button",
          class: `duel-slot${(def?.name.length ?? 0) > 10 ? " long" : ""}`,
          style: `--spell: ${def?.colour ?? "#fff"}`,
          title: def ? `${def.name} (${kindLabel(def.kind)}): ${spellSummary(def)}` : "",
          "aria-label": `${def?.name ?? "Spell"}, key ${i + 1}`,
          onclick: () => this.opts.onCast(spell),
        },
        sweep,
        h("kbd", { class: "duel-slot-key" }, String(i + 1)),
        h("span", { class: "duel-slot-name" }, def?.name ?? "?"),
        h("span", { class: "duel-slot-kind" }, def ? kindLabel(def.kind) : ""),
      );
      return { spell, button, sweep, shown: { cd: -1, state: "" } };
    });
    this.bar.replaceChildren(...this.slots.map((s) => s.button));
    this.bar.hidden = this.slots.length === 0;
  }

  /** Hides everything (back in the castle). */
  hide(): void {
    this.root.hidden = true;
    this.view = null;
    this.root.classList.remove("self-bound", "self-slowed", "self-staggered");
    this.shownSelf = "";
  }

  /**
   * One frame: duel time `now`; `local` is performance.now() for the result's
   * countdown; `anchors` are where the duelists' heads are on screen (null:
   * off screen).
   */
  render(now: number, local: number, anchors: readonly [{ x: number; y: number } | null, { x: number; y: number } | null]): void {
    const v = this.view;
    if (!v) return;
    // The clock and the countdown.
    const left = v.timeLeft(now);
    const secs = Math.ceil(left / 1000);
    const clock = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
    if (clock !== this.shownClock) {
      this.shownClock = clock;
      this.clock.textContent = clock;
      this.clock.classList.toggle("low", secs <= 10 && !v.ended);
    }
    const cd = v.countdownLeft(now);
    const sinceFight = now - v.fightAtMs;
    const countdown = v.ended ? "" : cd > 0 ? String(Math.ceil(cd / 1000)) : sinceFight < 900 ? "Duel!" : "";
    if (countdown !== this.shownCountdown) {
      this.shownCountdown = countdown;
      this.countdown.textContent = countdown;
      this.countdown.hidden = !countdown;
      this.countdown.classList.toggle("go", countdown === "Duel!");
      // Restart the pop animation for each new number.
      this.countdown.style.animation = "none";
      void this.countdown.offsetWidth;
      this.countdown.style.animation = "";
    }

    for (const s of ["a", "b"] as const) this.renderSide(v, s, now, anchors[sideIndex(s)]);
    this.renderOwn(v, now);
    if (v.outcome) this.renderResult(v, local);
  }

  private renderSide(v: DuelView, side: DuelSide, now: number, anchor: { x: number; y: number } | null): void {
    const els = this.sides[sideIndex(side)];
    const sh = els.shown;
    const poise = v.side(side).poise;
    if (poise !== sh.poise) {
      const pct = Math.max(0, Math.min(100, (poise / Math.max(1, v.poiseMax)) * 100));
      els.fill.style.width = `${pct}%`;
      // The pale "lag" bar trails the fill (CSS transition), so damage reads as a chunk.
      els.lag.style.width = `${pct}%`;
      els.num.textContent = String(poise);
      els.root.classList.toggle("low", pct <= 25);
      if (sh.poise >= 0 && poise < sh.poise) {
        els.root.classList.remove("struck");
        void els.root.offsetWidth;
        els.root.classList.add("struck");
      }
      sh.poise = poise;
    }
    // The telegraph: what they are casting, filling toward its release (not for our own: the cast bar shows it).
    const cast = side === v.you ? null : v.castOf(side);
    const def = cast ? duelSpell(cast.spell) : undefined;
    const key = cast && def && now < cast.releaseMs ? `${cast.spell}:${cast.releaseMs}` : "";
    if (key !== sh.cast) {
      sh.cast = key;
      els.cast.hidden = !key;
      if (key && def) {
        els.cast.style.setProperty("--spell", def.colour);
        els.castLabel.textContent = def.name;
      }
    }
    if (key && cast) {
      const pct = Math.round(Math.max(0, Math.min(1, (now - cast.startMs) / Math.max(1, cast.releaseMs - cast.startMs))) * 100);
      if (pct !== sh.castPct) {
        sh.castPct = pct;
        els.castFill.style.width = `${pct}%`;
      }
    }
    // Status chips: shield, ward, disarm, bind, slow, stagger, each draining to its end.
    const st = v.side(side);
    const chip = els.chip;
    const shield = v.shieldAt(side, now);
    const perfect = !!shield && now < shield.perfectUntilMs;
    this.setChip(chip.shield, shield ? chipText("shield", 0, { perfect }) : null, shield?.untilMs ?? 0, CHIP_FULL_MS.shield, now, perfect ? "perfect" : "");
    this.setChip(chip.ward, v.wardAt(side, now) ? chipText("ward", st.wardUntil - now) : null, st.wardUntil, CHIP_FULL_MS.ward, now);
    this.setChip(chip.disarmed, v.disarmedAt(side, now) ? chipText("disarmed", st.disarmedUntil - now) : null, st.disarmedUntil, CHIP_FULL_MS.disarmed, now);
    this.setChip(chip.bound, v.rootedAt(side, now) ? chipText("bound", st.rootedUntil - now) : null, st.rootedUntil, CHIP_FULL_MS.bound, now);
    const slow = v.slowedAt(side, now);
    this.setChip(chip.slowed, slow ? chipText("slowed", slow.untilMs - now, { pct: slow.pct }) : null, slow?.untilMs ?? 0, CHIP_FULL_MS.slowed, now);
    this.setChip(chip.staggered, v.staggeredAt(side, now) ? chipText("staggered", st.staggeredUntil - now) : null, st.staggeredUntil, CHIP_FULL_MS.staggered, now);
    // Where callouts float.
    const showAnchor = anchor !== null;
    if (showAnchor !== sh.anchorShown) {
      sh.anchorShown = showAnchor;
      els.anchor.hidden = !showAnchor;
    }
    if (anchor) {
      const x = Math.round(anchor.x);
      const y = Math.round(anchor.y);
      if (x !== sh.anchorX || y !== sh.anchorY) {
        sh.anchorX = x;
        sh.anchorY = y;
        els.anchor.style.transform = `translate(${x}px, ${y}px)`;
      }
    }
  }

  private renderOwn(v: DuelView, now: number): void {
    const you = v.you;
    if (!you) {
      this.castbar.hidden = true;
      return;
    }
    const disarmed = v.disarmedAt(you, now);
    const staggered = v.staggeredAt(you, now);
    const hindered = v.rootedAt(you, now) || v.slowedAt(you, now) !== null;
    // The screen's edge while we are bound, slowed or staggered.
    const self = v.ended ? "" : `${v.rootedAt(you, now) ? " self-bound" : ""}${v.slowedAt(you, now) ? " self-slowed" : ""}${staggered ? " self-staggered" : ""}`;
    if (self !== this.shownSelf) {
      for (const c of this.shownSelf.split(" ")) if (c) this.root.classList.remove(c);
      for (const c of self.split(" ")) if (c) this.root.classList.add(c);
      this.shownSelf = self;
    }
    const casting = v.castOf(you);
    for (const s of this.slots) {
      const def = duelSpell(s.spell);
      const ready = v.readyAt(you, s.spell);
      const total = def?.cooldownMs ?? 1;
      const frac = ready > now ? Math.min(1, (ready - now) / total) : 0;
      const cd = Math.round(frac * 100);
      if (cd !== s.shown.cd) {
        s.shown.cd = cd;
        s.sweep.style.setProperty("--cd", String(cd / 100));
      }
      const state = v.ended
        ? "off"
        : disarmed
          ? "disarmed"
          : staggered
            ? "staggered"
            : casting?.spell === s.spell && now < casting.releaseMs
              ? "casting"
              : frac > 0
                ? "cooling"
                : !v.fighting(now)
                  ? "waiting"
                  : hindered && def?.dispel && !casting
                    ? "suggest"
                    : "ready";
      if (state !== s.shown.state) {
        s.button.classList.remove(`is-${s.shown.state}`);
        s.button.classList.add(`is-${state}`);
        s.shown.state = state;
      }
    }
    // Our cast bar.
    const def = casting ? duelSpell(casting.spell) : undefined;
    const live = casting && def && now < casting.releaseMs + 80;
    const label = live && def ? def.name : "";
    const tone = live && v.pending ? "predicted" : "";
    if (label !== this.shownCast.label || tone !== this.shownCast.tone) {
      this.shownCast.label = label;
      this.shownCast.tone = tone;
      this.castbar.hidden = !label;
      this.castLabel.textContent = label;
      if (def) this.castbar.style.setProperty("--spell", def.colour);
    }
    if (live && casting) {
      const pct = Math.round(Math.max(0, Math.min(1, (now - casting.startMs) / Math.max(1, casting.releaseMs - casting.startMs))) * 100);
      if (pct !== this.shownCast.pct) {
        this.shownCast.pct = pct;
        this.castFill.style.width = `${pct}%`;
      }
    }
  }

  /** A short word over a duelist's head ("Blocked!", "-10"). */
  callout(side: DuelSide, text: string, tone: CalloutTone): void {
    const els = this.sides[sideIndex(side)];
    let el = els.callouts[els.next % CALLOUTS_PER_SIDE];
    if (!el) {
      el = h("span", { class: "duel-callout" });
      els.callouts.push(el);
      els.anchor.append(el);
    }
    els.next++;
    // Words arriving together (a hit, then "Bound!" and "Adveni!" in one tick) stack upward instead of overlapping.
    const t = performance.now();
    els.stack = t - els.lastAt < STACK_MS ? Math.min(els.stack + 1, CALLOUTS_PER_SIDE - 1) : 0;
    els.lastAt = t;
    el.textContent = text;
    el.className = `duel-callout ${tone}`;
    el.style.setProperty("--lane", String((els.next % 3) - 1));
    el.style.setProperty("--stack", String(els.stack));
    // Restart its rise-and-fade animation.
    el.style.animation = "none";
    void el.offsetWidth;
    el.style.animation = `duel-callout ${CALLOUT_MS}ms ease-out forwards`;
  }

  /** The screen flashes red at the edges: we were hit. */
  flash(): void {
    this.flashEl.classList.remove("on");
    void this.flashEl.offsetWidth;
    this.flashEl.classList.add("on");
  }

  /** Spell `spell` was refused (shakes its button, shows why). */
  refused(spell: number, text: string): void {
    const slot = this.slots.find((s) => s.spell === spell);
    if (slot) {
      slot.button.classList.remove("refused");
      void slot.button.offsetWidth;
      slot.button.classList.add("refused");
    }
    if (this.view?.you) this.callout(this.view.you, text, "info");
  }

  private onAction(): void {
    const v = this.view;
    if (!v || !v.you || v.ended) {
      this.opts.onLeave();
      return;
    }
    const now = performance.now();
    if (now < this.forfeitArmedUntil) {
      this.forfeitArmedUntil = 0;
      this.actionButton.classList.remove("armed");
      this.opts.onForfeit();
      return;
    }
    this.forfeitArmedUntil = now + FORFEIT_CONFIRM_MS;
    this.actionButton.classList.add("armed");
    this.actionButton.textContent = "Forfeit? Press again";
    setTimeout(() => {
      if (performance.now() >= this.forfeitArmedUntil && this.view === v && !v.ended) {
        this.actionButton.classList.remove("armed");
        this.actionButton.textContent = "Forfeit";
      }
    }, FORFEIT_CONFIRM_MS + 50);
  }

  private renderResult(v: DuelView, local: number): void {
    const o = v.outcome!;
    if (this.resultFor !== v.duelId) {
      this.resultFor = v.duelId;
      const verdict = v.verdict();
      const title = verdict === "win" ? "Victory!" : verdict === "loss" ? "Defeated" : verdict === "draw" ? "A draw" : o.winner ? `${v.info(o.winner).name} wins` : "A draw";
      const tone = verdict ?? (o.winner ? "win" : "draw");
      const rewards: (HTMLElement | null)[] = [];
      if (o.rewards) {
        rewards.push(h("li", { class: `duel-reward ${o.rewards.xp > 0 ? "xp" : "none"}` }, xpText(o.rewards)));
        if (o.rewards.housePoints > 0) {
          const house = houseInfo(v.info(v.you!).house);
          rewards.push(h("li", { class: "duel-reward points", style: `--house: ${house.colour}` }, `+${o.rewards.housePoints} points for ${house.name}`));
        }
      }
      const poise = (s: DuelSide) => h("span", { class: `duel-final ${s}`, style: `--house: ${houseInfo(v.info(s).house).colour}` }, `${v.info(s).name} `, h("strong", {}, String(o.poise[sideIndex(s)])));
      this.returnEl = h("p", { class: "duel-return" });
      this.shownReturn = -1;
      this.result.className = `duel-result panel ${tone}`;
      this.result.replaceChildren(
        h("h2", {}, title),
        h("p", { class: "duel-reason" }, endText(v, o.winner, o.reason)),
        h("p", { class: "duel-finals" }, poise("a"), h("span", { class: "muted" }, " Poise "), poise(otherSide("a"))),
        rewards.length ? h("ul", { class: "duel-rewards" }, ...rewards) : "",
        v.you && !o.rewards ? h("p", { class: "muted" }, "No rewards this time.") : "",
        this.returnEl,
        h("button", { type: "button", class: "secondary", onclick: () => this.opts.onLeave() }, "Back to the club now"),
      );
      this.result.hidden = false;
      this.actionButton.hidden = true;
      this.bar.hidden = true;
      this.castbar.hidden = true;
    }
    const secs = Math.max(0, Math.ceil((o.returnAtLocal - local) / 1000));
    if (secs !== this.shownReturn && this.returnEl) {
      this.shownReturn = secs;
      this.returnEl.textContent = secs > 0 ? `Back to the club in ${secs} s…` : "Returning to the club…";
    }
  }

  /** Shows the action button and the bar again (a new duel). */
  reset(): void {
    this.actionButton.hidden = false;
    this.result.hidden = true;
  }

  dispose(): void {
    this.root.remove();
  }
}
