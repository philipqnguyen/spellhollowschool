// The trace pad of a class: a square where the student traces each beat's
// wand gesture with the mouse, a finger or the keyboard.
//
//   ┌──────────────────────────────┐
//   │ Twirl and Tap      in 1.2 s │  caption, countdown
//   │      ·  ·  ·                  │
//   │   ◯ ·        ·   ↑            │  the gesture's template (dashed, an arrow
//   │ (●)            ·  ·  ↓        │  at its end), the approach ring closing on
//   │                               │  the start dot: start tracing when it lands
//   │            Great! 78          │  feedback
//   └──────────────────────────────┘
//   ● ● ● ◐ ○ ○ ○ ○                    one pip per beat of the round
//
// During a demonstration the template draws itself in the spell's colour.
// For a constellation (Astronomy) the pad is a patch of night
// sky: a faint star field, the constellation's stars bright, its guide dim.
// The keyboard: with the pad focused, the arrow keys move a pen from the
// gesture's start (the first key starts the trace); Enter or Space ends it,
// as does letting go of every key for a moment. The pad never decides
// scores: it hands the recorded trace to its owner (game/classes).

import "./classes.css";
import type { GestureDef } from "../game/spells/data";
import { constellationStars, starField } from "../game/classes/fx/astronomy";
import { padPoint, penStep, TraceRecorder, type PenKeys } from "../game/classes/trace";
import type { Grade } from "../wasm/sim_wasm";
import { h } from "./dom";

export type PipStatus = "upcoming" | "current" | "sent" | "scored" | "missed";

export interface PadPip {
  status: PipStatus;
  grade?: Grade;
  rejected?: boolean;
}

/** What the pad shows this frame. */
export interface PadScene {
  /** `watch`: a demonstration; `practice`: beats to trace; `free`: trace for practice, nothing is sent. */
  mode: "watch" | "practice" | "free";
  gesture: GestureDef | null;
  /** CSS colour of the template and the demonstration. */
  colour: string;
  /** The approach ring: 1 far off, 0 the beat is now (null: no ring). */
  approach: number | null;
  /** The current beat's window is open: a trace starting now counts. */
  open: boolean;
  /** Demonstration progress 0..1 (null: not demonstrating). */
  watch: number | null;
  caption: string;
  countdown: string;
  pips: PadPip[];
  /** Astronomy: the pad shows the night sky, the gesture's vertices as its stars. */
  sky?: boolean;
}

export interface TracePadOptions {
  /**
   * A trace starts at local time `t` (ms): which gesture it is for (its
   * template's longest duration bounds the recording), or null to refuse it.
   */
  onStart: (t: number) => { maxMs: number } | null;
  /** A trace ended: score and send it. */
  onEnd: (trace: TraceRecorder) => void;
}

/** With the keyboard pen, letting go of every key this long ends the trace (ms). */
const PEN_IDLE_END_MS = 450;
/** The margin round the pad square inside the canvas (a fraction of its side), so strokes near the edge stay visible. */
const PAD_MARGIN = 0.08;
const GRADE_WORDS: Record<Grade, string> = { miss: "Miss", good: "Good", great: "Great!", perfect: "Perfect!" };
/** The sky pad's background stars (the same every frame). */
const SKY_STARS = starField(90, 7);
/** The sky pad's colours: palette.json midnight blue and navy, the stars in ivory and sky. */
const SKY_TOP = "#0e1a40";
const SKY_BOTTOM = "#1f2e5a";
const STAR = "#e6dfcf";
const STAR_GLOW = "#8eb5d9";

export class TracePad {
  readonly root: HTMLElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D | null;
  private readonly caption: HTMLElement;
  private readonly countdown: HTMLElement;
  private readonly feedbackEl: HTMLElement;
  private readonly pipsEl: HTMLElement;
  private readonly live: HTMLElement;
  private scene: PadScene | null = null;
  private trace: TraceRecorder | null = null;
  private pointerId: number | null = null;
  /** The stroke as drawn (pad units), for the canvas. */
  private stroke: [number, number][] = [];
  /** The last finished stroke, fading out. */
  private lastStroke: { points: [number, number][]; at: number } | null = null;
  private feedback: { text: string; tone: string; at: number } | null = null;
  private readonly keys: PenKeys = { left: false, right: false, up: false, down: false };
  private pen: [number, number] | null = null;
  private penIdleSince = 0;
  private pipKey = "";
  private size = 0;

  constructor(private readonly opts: TracePadOptions) {
    this.caption = h("span", { class: "pad-caption" }, "");
    this.countdown = h("span", { class: "pad-countdown" }, "");
    this.canvas = h("canvas", {
      class: "pad-canvas",
      tabindex: 0,
      role: "application",
      "aria-label": "Trace pad. Trace the gesture with the mouse or a finger when the ring closes on the start dot, or focus here and draw with the arrow keys; Enter ends the stroke.",
    });
    this.ctx = this.canvas.getContext("2d");
    this.feedbackEl = h("p", { class: "pad-feedback", "aria-hidden": "true" });
    this.pipsEl = h("ol", { class: "pad-pips", "aria-label": "Beats of this round" });
    this.live = h("p", { class: "sr-only", "aria-live": "polite" });
    this.root = h(
      "section",
      { class: "trace-pad panel", "aria-label": "Wand practice", hidden: true },
      h("header", { class: "pad-head" }, this.caption, this.countdown),
      h("div", { class: "pad-frame" }, this.canvas, this.feedbackEl),
      this.pipsEl,
      h("p", { class: "pad-help" }, "Trace when the ring meets the dot · keyboard: focus the pad, arrows draw, Enter ends"),
      this.live,
    );
    this.canvas.addEventListener("pointerdown", (e) => this.pointerDown(e));
    this.canvas.addEventListener("pointermove", (e) => this.pointerMove(e));
    this.canvas.addEventListener("pointerup", (e) => this.pointerUp(e));
    this.canvas.addEventListener("pointercancel", (e) => this.pointerUp(e));
    this.canvas.addEventListener("keydown", (e) => this.keyDown(e));
    this.canvas.addEventListener("keyup", (e) => this.keyUp(e));
    this.canvas.addEventListener("blur", () => this.releaseKeys());
  }

  get visible(): boolean {
    return !this.root.hidden;
  }

  /** Shows `scene` (null hides the pad and drops any stroke in progress). */
  show(scene: PadScene | null): void {
    if (!scene) {
      if (!this.root.hidden) {
        this.root.hidden = true;
        this.cancel();
      }
      this.scene = null;
      return;
    }
    this.root.hidden = false;
    this.scene = scene;
    if (this.caption.textContent !== scene.caption) this.caption.textContent = scene.caption;
    if (this.countdown.textContent !== scene.countdown) this.countdown.textContent = scene.countdown;
    this.root.classList.toggle("open", scene.open);
    this.root.classList.toggle("watching", scene.mode === "watch");
    this.renderPips(scene.pips);
  }

  /** A grade (or a refusal) pops up over the pad and is read out. */
  flash(text: string, tone: Grade | "reject" | "info"): void {
    this.feedback = { text, tone, at: performance.now() };
    this.feedbackEl.textContent = text;
    this.feedbackEl.className = `pad-feedback ${tone} pop`;
    this.live.textContent = text;
  }

  /** Words for a grade and score. */
  static gradeText(grade: Grade, score: number): string {
    return grade === "miss" ? `Miss (${score})` : `${GRADE_WORDS[grade]} ${score}`;
  }

  /** Says something to screen readers without a pop-up (the next beat). */
  announce(text: string): void {
    this.live.textContent = text;
  }

  /** Per frame while shown: the keyboard pen and the drawing. */
  frame(now: number, dtMs: number): void {
    if (this.root.hidden) return;
    this.stepPen(now, dtMs);
    this.draw(now);
    if (this.feedback && now - this.feedback.at > 1400) {
      this.feedback = null;
      this.feedbackEl.classList.remove("pop");
    }
  }

  // ---------------------------------------------------------------- input

  private pointerDown(e: PointerEvent): void {
    if (this.trace || e.button > 0) return;
    e.preventDefault();
    this.canvas.focus({ preventScroll: true });
    const [x, y] = padPoint(e.clientX, e.clientY, this.padRect());
    if (!this.begin(performance.now(), x, y)) return;
    this.pointerId = e.pointerId;
    this.canvas.setPointerCapture(e.pointerId);
  }

  private pointerMove(e: PointerEvent): void {
    if (!this.trace || e.pointerId !== this.pointerId) return;
    const [x, y] = padPoint(e.clientX, e.clientY, this.padRect());
    this.extend(performance.now(), x, y);
  }

  private pointerUp(e: PointerEvent): void {
    if (!this.trace || e.pointerId !== this.pointerId) return;
    const [x, y] = padPoint(e.clientX, e.clientY, this.padRect());
    this.pointerId = null;
    this.finish(performance.now(), x, y);
  }

  private keyDown(e: KeyboardEvent): void {
    const dir = arrow(e.code);
    // The pad has the keys while focused: they must not walk the player.
    if (dir || e.code === "Enter" || e.code === "Space" || e.code === "NumpadEnter") {
      e.preventDefault();
      e.stopPropagation();
    }
    if (dir) {
      this.keys[dir] = true;
      if (!this.trace && !e.repeat) {
        const start = this.startPoint();
        this.pen = start;
        if (!this.begin(performance.now(), start[0], start[1])) this.pen = null;
      }
      this.penIdleSince = 0;
    } else if ((e.code === "Enter" || e.code === "Space" || e.code === "NumpadEnter") && this.trace && this.pen) {
      this.finish(performance.now(), Math.round(this.pen[0]), Math.round(this.pen[1]));
    } else if (e.key === "Escape") {
      // Hand the keys back to the game.
      e.stopPropagation();
      this.cancel();
      this.canvas.blur();
    }
  }

  private keyUp(e: KeyboardEvent): void {
    const dir = arrow(e.code);
    if (!dir) return;
    e.stopPropagation();
    this.keys[dir] = false;
  }

  /** The part of the canvas the pad's -100..100 square covers (inside the drawing margin), so the stroke lands under the pointer. */
  private padRect(): { left: number; top: number; width: number; height: number } {
    const r = this.canvas.getBoundingClientRect();
    return { left: r.left + r.width * PAD_MARGIN, top: r.top + r.height * PAD_MARGIN, width: r.width * (1 - 2 * PAD_MARGIN), height: r.height * (1 - 2 * PAD_MARGIN) };
  }

  private releaseKeys(): void {
    this.keys.left = this.keys.right = this.keys.up = this.keys.down = false;
    if (this.trace && this.pen) this.finish(performance.now(), Math.round(this.pen[0]), Math.round(this.pen[1]));
  }

  /** The keyboard pen's start: the template's first point, so the shape fits the pad. */
  private startPoint(): [number, number] {
    const p = this.scene?.gesture?.points[0];
    return p ? [p[0], p[1]] : [0, 0];
  }

  private stepPen(now: number, dtMs: number): void {
    if (!this.trace || !this.pen) return;
    const held = this.keys.left || this.keys.right || this.keys.up || this.keys.down;
    if (!held) {
      if (this.penIdleSince === 0) this.penIdleSince = now;
      else if (now - this.penIdleSince > PEN_IDLE_END_MS) this.finish(now, Math.round(this.pen[0]), Math.round(this.pen[1]));
      return;
    }
    this.penIdleSince = 0;
    this.pen = penStep(this.pen[0], this.pen[1], this.keys, Math.min(dtMs, 50));
    this.extend(now, Math.round(this.pen[0]), Math.round(this.pen[1]));
  }

  private begin(t: number, x: number, y: number): boolean {
    const accepted = this.opts.onStart(t);
    if (!accepted) return false;
    this.trace = new TraceRecorder(t, x, y, accepted.maxMs);
    this.stroke = [[x, y]];
    this.lastStroke = null;
    return true;
  }

  private extend(t: number, x: number, y: number): void {
    const tr = this.trace;
    if (!tr) return;
    this.stroke.push([x, y]);
    if (!tr.move(t, x, y)) this.finish(t, x, y);
  }

  private finish(t: number, x: number, y: number): void {
    const tr = this.trace;
    if (!tr) return;
    this.trace = null;
    this.pen = null;
    if (this.pointerId !== null) {
      try {
        this.canvas.releasePointerCapture(this.pointerId);
      } catch {
        // Already released.
      }
      this.pointerId = null;
    }
    tr.end(t, x, y);
    this.stroke.push([x, y]);
    this.lastStroke = { points: this.stroke, at: t };
    this.stroke = [];
    this.opts.onEnd(tr);
  }

  /**
   * Ends the stroke in progress at local time `t` where it is, as if the
   * player had let go (its beat's time on the pad is over): `onEnd` gets it
   * as drawn so far. Returns whether there was a stroke.
   */
  cut(t: number): boolean {
    if (!this.trace) return false;
    const at = this.pen ?? this.stroke[this.stroke.length - 1] ?? [0, 0];
    this.finish(t, Math.round(at[0]), Math.round(at[1]));
    return true;
  }

  /** Drops the stroke in progress without sending anything. */
  cancel(): void {
    this.trace = null;
    this.pen = null;
    this.stroke = [];
    this.pointerId = null;
    this.keys.left = this.keys.right = this.keys.up = this.keys.down = false;
  }

  // ---------------------------------------------------------------- drawing

  private renderPips(pips: readonly PadPip[]): void {
    const key = pips.map((p) => `${p.status}${p.grade ?? ""}${p.rejected ? "x" : ""}`).join(",");
    if (key === this.pipKey) return;
    this.pipKey = key;
    this.pipsEl.replaceChildren(
      ...pips.map((p, i) => {
        const label = p.status === "scored" ? (p.rejected ? "not counted" : GRADE_WORDS[p.grade ?? "miss"]) : p.status === "missed" ? "missed" : p.status === "sent" ? "traced" : p.status === "current" ? "now" : "to come";
        return h("li", { class: `pip ${p.status} ${p.grade ?? ""}${p.rejected ? " rejected" : ""}`, title: `Beat ${i + 1}: ${label}`, "aria-label": `Beat ${i + 1}: ${label}` });
      }),
    );
  }

  private fit(): number {
    const css = this.canvas.clientWidth;
    const px = Math.max(1, Math.round(css * Math.min(window.devicePixelRatio || 1, 2)));
    if (px !== this.size) {
      this.size = px;
      this.canvas.width = px;
      this.canvas.height = px;
    }
    return px;
  }

  private draw(now: number): void {
    const ctx = this.ctx;
    const s = this.scene;
    if (!ctx || !s) return;
    const W = this.fit();
    const k = W / 230; // line widths are tuned for a 230 px pad
    const inner = W * (1 - 2 * PAD_MARGIN);
    const map = (p: readonly [number, number]): [number, number] => [((p[0] + 100) / 200) * inner + W * PAD_MARGIN, ((100 - p[1]) / 200) * inner + W * PAD_MARGIN];
    ctx.clearRect(0, 0, W, W);
    if (s.sky) this.drawSky(ctx, W, k, map, now);

    const g = s.gesture;
    if (g && g.points.length >= 2) {
      const pts = g.points.map(map);
      if (s.sky) {
        // The constellation's own stars, bright, under the guide.
        ctx.save();
        for (const [i, star] of constellationStars(g.points).entries()) {
          const [x, y] = map(star);
          const tw = 0.85 + 0.15 * Math.sin(now / 240 + i * 1.7);
          ctx.fillStyle = STAR_GLOW;
          ctx.globalAlpha = 0.35 * tw;
          ctx.beginPath();
          ctx.arc(x, y, 9 * k, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = STAR;
          ctx.globalAlpha = tw;
          ctx.beginPath();
          ctx.arc(x, y, 3.6 * k, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.restore();
      }
      // The template: a soft dashed guide, an arrow at its end.
      ctx.save();
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.strokeStyle = s.colour;
      ctx.globalAlpha = (s.mode === "watch" ? 0.18 : s.open ? 0.55 : 0.32) * (s.sky ? 0.6 : 1);
      ctx.lineWidth = 9 * k;
      ctx.setLineDash([2 * k, 14 * k]);
      polyline(ctx, pts);
      ctx.setLineDash([]);
      ctx.globalAlpha = s.mode === "watch" ? 0.3 : 0.7;
      arrowHead(ctx, pts[pts.length - 2]!, pts[pts.length - 1]!, 12 * k);
      ctx.restore();

      // The demonstration draws the stroke in the spell's colour, a spark at its head.
      if (s.watch !== null) {
        const part = partial(pts, Math.min(1, Math.max(0, s.watch)));
        ctx.save();
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.strokeStyle = s.colour;
        ctx.shadowColor = s.colour;
        ctx.shadowBlur = 14 * k;
        ctx.lineWidth = 6 * k;
        polyline(ctx, part);
        const head = part[part.length - 1]!;
        ctx.fillStyle = "#fffbe8";
        ctx.beginPath();
        ctx.arc(head[0], head[1], 5 * k * (1 + 0.25 * Math.sin(now / 60)), 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      // The start dot, and the approach ring closing on it.
      const [sx, sy] = pts[0]!;
      ctx.save();
      ctx.fillStyle = s.open ? "#7fe0b0" : s.colour;
      ctx.globalAlpha = s.mode === "watch" ? 0.5 : 1;
      ctx.beginPath();
      ctx.arc(sx, sy, 7 * k, 0, Math.PI * 2);
      ctx.fill();
      if (s.approach !== null && s.mode === "practice") {
        const r = 9 * k + Math.max(0, s.approach) * 70 * k;
        ctx.globalAlpha = 0.35 + 0.65 * (1 - Math.min(1, s.approach));
        ctx.strokeStyle = s.open ? "#7fe0b0" : "#f0c36a";
        ctx.lineWidth = (s.open ? 4 : 2.5) * k;
        ctx.beginPath();
        ctx.arc(sx, sy, r, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }

    // The player's stroke: bright while drawing, fading after.
    const live = this.stroke.length > 1 ? this.stroke : null;
    const last = this.lastStroke;
    const fade = last ? 1 - (now - last.at) / 900 : 0;
    const drawn = live ?? (last && fade > 0 ? last.points : null);
    if (drawn && drawn.length > 1) {
      ctx.save();
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.globalAlpha = live ? 1 : Math.max(0, fade);
      ctx.strokeStyle = "#ffe9a8";
      ctx.shadowColor = "#ffcf5a";
      ctx.shadowBlur = 10 * k;
      ctx.lineWidth = 5 * k;
      polyline(ctx, drawn.map(map));
      ctx.restore();
    }
    if (!last || fade <= 0) this.lastStroke = null;
  }

  /** The night sky behind a constellation: a gradient and a fixed field of faint stars. */
  private drawSky(ctx: CanvasRenderingContext2D, W: number, k: number, map: (p: readonly [number, number]) => [number, number], now: number): void {
    ctx.save();
    const grad = ctx.createLinearGradient(0, 0, 0, W);
    grad.addColorStop(0, SKY_TOP);
    grad.addColorStop(1, SKY_BOTTOM);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, W);
    ctx.fillStyle = STAR;
    for (const [i, st] of SKY_STARS.entries()) {
      const [x, y] = map([st.x, st.y]);
      ctx.globalAlpha = st.bright * (0.8 + 0.2 * Math.sin(now / 700 + i));
      ctx.beginPath();
      ctx.arc(x, y, (0.6 + 1.2 * st.size) * k, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  dispose(): void {
    this.cancel();
    this.root.remove();
  }
}

function arrow(code: string): keyof PenKeys | null {
  switch (code) {
    case "ArrowLeft":
    case "Numpad4":
      return "left";
    case "ArrowRight":
    case "Numpad6":
      return "right";
    case "ArrowUp":
    case "Numpad8":
      return "up";
    case "ArrowDown":
    case "Numpad2":
      return "down";
    default:
      return null;
  }
}

function polyline(ctx: CanvasRenderingContext2D, pts: readonly (readonly [number, number])[]): void {
  if (pts.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(pts[0]![0], pts[0]![1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i]![0], pts[i]![1]);
  ctx.stroke();
}

function arrowHead(ctx: CanvasRenderingContext2D, from: readonly [number, number], to: readonly [number, number], size: number): void {
  const a = Math.atan2(to[1] - from[1], to[0] - from[0]);
  ctx.fillStyle = ctx.strokeStyle;
  ctx.beginPath();
  ctx.moveTo(to[0] + Math.cos(a) * size * 0.6, to[1] + Math.sin(a) * size * 0.6);
  ctx.lineTo(to[0] + Math.cos(a + 2.5) * size, to[1] + Math.sin(a + 2.5) * size);
  ctx.lineTo(to[0] + Math.cos(a - 2.5) * size, to[1] + Math.sin(a - 2.5) * size);
  ctx.closePath();
  ctx.fill();
}

/** The first fraction `t` of a polyline, by length. */
export function partial(pts: readonly [number, number][], t: number): [number, number][] {
  if (pts.length < 2) return [...pts];
  let total = 0;
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i]![0] - pts[i - 1]![0], pts[i]![1] - pts[i - 1]![1]);
  let want = total * t;
  const out: [number, number][] = [pts[0]!];
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]!;
    const b = pts[i]!;
    const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (d >= want) {
      const f = d > 0 ? want / d : 0;
      out.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
      return out;
    }
    want -= d;
    out.push(b);
  }
  return out;
}
