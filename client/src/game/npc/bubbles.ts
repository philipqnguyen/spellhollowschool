// Speech bubbles over NPCs: a professor's lines (`NpcEvent { Say }`) float
// above their head for a few seconds, long enough to read, and also go to
// the chat log so nobody misses one. A bubble follows its speaker on
// screen like a nameplate (projected once a frame while it shows; there is
// at most one per NPC, so a handful of style writes). Like a nameplate, a
// bubble shows only while its speaker's head could be seen (ui/plate-sight.ts):
// never through a wall, a floor or a ceiling.

import "./npc.css";
import { h } from "../../ui/dom";
import { PlateSight, SIGHT_DROP, type SightTarget, type SightTest } from "../../ui/plate-sight";

/** Shortest and longest time a line stays up (ms); in between, by its length. */
const MIN_MS = 3500;
const MAX_MS = 9000;
const MS_PER_CHAR = 65;
/** Farther than this (m) the bubble fades out: the chat log still has it. */
const MAX_DISTANCE = 30;

/** A bubble's anchor stands this far over its speaker's nameplate anchor (m). */
export const BUBBLE_ABOVE_PLATE = 0.25;
/** So its line of sight aims this far under the bubble's anchor (m): the middle of the head, as a plate's does. */
export const BUBBLE_SIGHT_DROP = SIGHT_DROP + BUBBLE_ABOVE_PLATE;

/** A bubble's height (px) until it is measured once shown. */
const GUESS_HEIGHT = 64;
/** The tail below a bubble (px). */
const TAIL = 8;

interface Bubble {
  el: HTMLDivElement;
  text: HTMLSpanElement;
  until: number;
  shown: boolean;
  /** Its height (px) as laid out, 0 until measured (once per line). */
  height: number;
  /** Placed on screen this frame (in view, in sight). */
  placed: boolean;
}

/**
 * The anchor's screen y (bottom of the bubble, px) kept on screen and below
 * `minTop` (the HUD's top bar and the strip under it, a bubble
 * near the top of the view used to cover them): the bubble's top edge
 * stays at or under `minTop`.
 */
export function bubbleY(sy: number, height: number, screenH: number, minTop: number): number {
  return Math.min(screenH - 12, Math.max(minTop + height + TAIL, sy));
}

/** Where a speaker's bubble anchors (above the head), or null when not drawn. */
export type Anchor = (id: number) => { x: number; y: number; z: number } | null;

export class SpeechBubbles {
  private readonly root: HTMLDivElement;
  private readonly bubbles = new Map<number, Bubble>();
  /** Line of sight to each speaker's head: a bubble behind level geometry stays hidden. */
  readonly sight = new PlateSight(null, BUBBLE_SIGHT_DROP);
  // Per-frame scratch (pooled): the bubbles on screen this frame, their screen spots and sight.
  private readonly onScreen: Bubble[] = [];
  private readonly spots: { x: number; y: number; fade: number }[] = [];
  private readonly targets: SightTarget[] = [];
  private readonly targetPool: SightTarget[] = [];
  private readonly alpha: number[] = [];

  constructor(parent: HTMLElement) {
    this.root = h("div", { class: "npc-bubbles", "aria-hidden": "true" });
    parent.append(this.root);
  }

  /** `id` says `text` (local time `now`, ms). */
  say(id: number, name: string, text: string, now: number): void {
    let b = this.bubbles.get(id);
    if (!b) {
      const label = h("span", { class: "npc-bubble-name" }, name);
      const body = h("span", { class: "npc-bubble-text" });
      const el = h("div", { class: "npc-bubble", hidden: true }, label, body);
      this.root.append(el);
      b = { el, text: body, until: 0, shown: false, height: 0, placed: false };
      this.bubbles.set(id, b);
    }
    b.text.textContent = text;
    b.height = 0;
    b.until = now + Math.min(MAX_MS, Math.max(MIN_MS, text.length * MS_PER_CHAR));
    // Restart the pop-in.
    b.el.classList.remove("pop");
    void b.el.offsetWidth;
    b.el.classList.add("pop");
  }

  /** The level a speaker's head must be seen past (null: nothing hides a bubble). */
  setSightTest(test: SightTest | null): void {
    this.sight.setTest(test);
  }

  /** Per frame after rendering: places each bubble over its speaker, its top below `minTop` (px). */
  update(anchor: Anchor, viewProj: ArrayLike<number>, eye: { x: number; y: number; z: number }, width: number, height: number, now: number, minTop = 80): void {
    if (this.bubbles.size === 0) return;
    const m = viewProj;
    const onScreen = this.onScreen;
    const targets = this.targets;
    onScreen.length = 0;
    targets.length = 0;
    for (const [id, b] of this.bubbles) {
      if (now > b.until) {
        b.el.remove();
        this.bubbles.delete(id);
        continue;
      }
      const p = anchor(id);
      if (!p) continue;
      const w = p.x * m[3]! + p.y * m[7]! + p.z * m[11]! + m[15]!;
      const d = Math.hypot(p.x - eye.x, p.y - eye.y, p.z - eye.z);
      if (!(w > 0.1 && d < MAX_DISTANCE)) continue;
      const sx = ((p.x * m[0]! + p.y * m[4]! + p.z * m[8]! + m[12]!) / w) * 0.5 + 0.5;
      const sy = 0.5 - ((p.x * m[1]! + p.y * m[5]! + p.z * m[9]! + m[13]!) / w) * 0.5;
      if (!(sx > -0.2 && sx < 1.2 && sy > -0.2 && sy < 1.2)) continue;
      const i = onScreen.length;
      onScreen.push(b);
      const spot = (this.spots[i] ??= { x: 0, y: 0, fade: 0 });
      spot.x = sx;
      spot.y = sy;
      spot.fade = Math.min(1, (b.until - now) / 400) * (d > MAX_DISTANCE - 8 ? (MAX_DISTANCE - d) / 8 : 1);
      const t = (this.targetPool[i] ??= { id: 0, x: 0, y: 0, z: 0 });
      t.id = id;
      t.x = p.x;
      t.y = p.y;
      t.z = p.z;
      targets.push(t);
    }
    const ex = this.eyeScratch;
    ex[0] = eye.x;
    ex[1] = eye.y;
    ex[2] = eye.z;
    const alpha = this.sight.update(targets, ex, now, this.alpha);
    for (const b of this.bubbles.values()) b.placed = false;
    for (let i = 0; i < onScreen.length; i++) {
      const seen = alpha[i]!;
      if (seen <= 0) continue;
      const b = onScreen[i]!;
      const spot = this.spots[i]!;
      b.placed = true;
      // Clamped on screen, so a line said just off the edge still shows.
      const x = Math.min(width - 12, Math.max(12, spot.x * width));
      const y = bubbleY(spot.y * height, b.height || GUESS_HEIGHT, height, minTop);
      b.el.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) translate(-50%, -100%)`;
      b.el.style.opacity = (spot.fade * seen).toFixed(2);
    }
    for (const b of this.bubbles.values()) {
      const visible = b.placed;
      if (visible !== b.shown) {
        b.el.hidden = !visible;
        b.shown = visible;
      }
      // Measured once a line shows (one layout read per line), placed by it from the next frame.
      if (visible && b.height === 0) b.height = b.el.offsetHeight;
    }
  }

  private readonly eyeScratch: [number, number, number] = [0, 0, 0];

  /** A zone change: every bubble goes. */
  clear(): void {
    for (const b of this.bubbles.values()) b.el.remove();
    this.bubbles.clear();
  }

  dispose(): void {
    this.clear();
    this.root.remove();
  }
}
