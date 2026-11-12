// Classes, synthesized (docs/architecture.md "Sound and music"):
// the school bell, the practice beats (ticks timed to the beat
// clock, the window opening, the grades), the Flying rings and each class's
// own small reaction to a passed trace or a right pick. The grades climb as
// they get better (one note, two, a sparkling three), all in one key so a
// run of beats sounds like a tune rather than a slot machine.

import type { SoundDefs } from "../types";
import { arp, bell, between, BELL, crackle, GLASS, HARP, hz, noise, rng, seed, sparkle, synth, thump, tone, whoosh } from "./kit";
import { bubbles } from "./magic";

export const CLASS_DEFS: SoundDefs = {
  // A handbell rung three times.
  "class.bell": synth(3.4, (ctx, out) => {
    for (const t of [0, 0.42, 0.84]) bell(ctx, out, { f: hz(81), t, peak: 0.2, d: 1.6, ratios: BELL, tilt: 0.9, spread: 5 });
  }),

  // A soft rolled chord: welcome to class.
  "class.join": synth(1.5, (ctx, out) => {
    arp([60, 67, 72, 74, 79], 0.06, 0, (f, t) => bell(ctx, out, { f, t, peak: 0.12, d: 1.1, ratios: HARP, tilt: 1.4 }));
  }),

  "class.beat_tick": synth(
    0.1,
    (ctx, out, v) => {
      const f = v === 0 ? 1000 : 1500;
      bell(ctx, out, { f, peak: 0.35, d: 0.05, ratios: [1, 2.6], tilt: 1.5 });
      noise(ctx, out, { a: 0.001, d: 0.01, peak: 0.15, f: 3000, q: 1 });
    },
    { variants: 2 },
  ),

  "class.beat_open": synth(0.35, (ctx, out) => {
    bell(ctx, out, { f: hz(88), peak: 0.25, d: 0.25, ratios: GLASS, tilt: 1.2 });
    tone(ctx, out, { f: hz(76), a: 0.002, d: 0.15, peak: 0.1 });
  }),

  "class.grade_perfect": synth(1.0, (ctx, out) => {
    arp([84, 88, 91, 96], 0.06, 0, (f, t, i) => bell(ctx, out, { f, t, peak: 0.15 + i * 0.01, d: 0.6, ratios: GLASS, tilt: 1.3 }));
    sparkle(ctx, out, { t: 0.15, spanS: 0.5, count: 8, peak: 0.05, lo: 4000, hi: 8000, seed: seed("perfect") });
  }),

  "class.grade_great": synth(0.7, (ctx, out) => {
    arp([84, 91], 0.07, 0, (f, t) => bell(ctx, out, { f, t, peak: 0.16, d: 0.45, ratios: GLASS, tilt: 1.3 }));
  }),

  "class.grade_good": synth(0.5, (ctx, out) => {
    bell(ctx, out, { f: hz(88), peak: 0.16, d: 0.35, ratios: GLASS, tilt: 1.4 });
  }),

  "class.miss": synth(0.4, (ctx, out) => {
    thump(ctx, out, { f: 160, f2: 100, d: 0.15, peak: 0.25 });
    tone(ctx, out, { f: hz(67), f2: hz(64), glideS: 0.15, a: 0.005, d: 0.22, peak: 0.12, type: "triangle", lp: 1200 });
  }),

  "class.reject": synth(0.25, (ctx, out) => {
    for (const t of [0, 0.08]) tone(ctx, out, { f: 240, t, a: 0.002, d: 0.06, peak: 0.25, type: "square", lp: 800 });
  }),

  "class.pick": synth(0.15, (ctx, out) => {
    bell(ctx, out, { f: 900, peak: 0.3, d: 0.08, ratios: HARP, tilt: 1.6 });
    noise(ctx, out, { a: 0.001, d: 0.012, peak: 0.12, f: 2500, q: 1 });
  }),

  "class.ring_glow": synth(0.8, (ctx, out) => {
    tone(ctx, out, { f: hz(76), f2: hz(81), glideS: 0.4, a: 0.2, d: 0.55, peak: 0.1, vib: [5, 10] });
    noise(ctx, out, { a: 0.25, d: 0.45, peak: 0.06, f: 2000, f2: 4500, q: 2 });
  }),

  "class.ring_through": synth(0.9, (ctx, out) => {
    whoosh(ctx, out, { f: 600, f2: 2400, a: 0.08, d: 0.45, peak: 0.25 });
    arp([84, 91], 0.05, 0.05, (f, t) => bell(ctx, out, { f, t, peak: 0.14, d: 0.55, ratios: GLASS, tilt: 1.3 }));
  }),

  // ---------------------------------------------------------------- each class's reaction (classes/fx/<class>.ts)

  "class.fx.charms": synth(1.1, (ctx, out) => {
    tone(ctx, out, { f: hz(79), f2: hz(91), glideS: 0.6, a: 0.1, d: 0.7, peak: 0.08, vib: [6, 15] });
    noise(ctx, out, { a: 0.3, d: 0.5, peak: 0.08, f: 1500, f2: 5000, q: 2 });
    sparkle(ctx, out, { t: 0.3, spanS: 0.6, count: 8, peak: 0.06, seed: seed("fx.charms") });
  }),

  "class.fx.potions": synth(1.3, (ctx, out) => {
    bubbles(ctx, out, { spanS: 0.7, count: 14, peak: 0.14, lo: 250, hi: 650, seed: seed("fx.potions") });
    arp([79, 86], 0.08, 0.75, (f, t) => bell(ctx, out, { f, t, peak: 0.12, d: 0.45, ratios: GLASS, tilt: 1.3 }));
  }),

  "class.fx.transfiguration": synth(0.9, (ctx, out) => {
    noise(ctx, out, { a: 0.001, d: 0.05, peak: 0.35, f: 1200, q: 0.8 });
    tone(ctx, out, { f: 300, f2: 900, glideS: 0.05, a: 0.002, d: 0.08, peak: 0.25 });
    bell(ctx, out, { f: hz(84), t: 0.06, peak: 0.12, d: 0.6, ratios: GLASS, tilt: 1.3, spread: 5 });
    sparkle(ctx, out, { t: 0.08, spanS: 0.4, count: 6, peak: 0.05, seed: seed("fx.transfig") });
  }),

  "class.fx.defence": synth(0.7, (ctx, out) => {
    thump(ctx, out, { f: 140, f2: 60, d: 0.2, peak: 0.45 });
    tone(ctx, out, { f: 2400, f2: 900, glideS: 0.12, a: 0.001, d: 0.15, peak: 0.12, type: "square", lp: 4000 });
    noise(ctx, out, { a: 0.001, d: 0.1, peak: 0.25, f: 2000, q: 0.8 });
  }),

  // A quill's three scratches and a page laid over.
  "class.fx.history": synth(1.1, (ctx, out) => {
    const r = rng(seed("fx.history"));
    for (const t of [0, 0.14, 0.3]) noise(ctx, out, { t, a: 0.01, d: 0.09, peak: 0.25, f: between(r, 3000, 4200), f2: between(r, 2200, 3000), q: 4, offset: r() });
    whoosh(ctx, out, { t: 0.5, f: 1500, f2: 600, a: 0.08, d: 0.35, peak: 0.12 });
  }),

  "class.fx.divination": synth(1.5, (ctx, out) => {
    tone(ctx, out, { f: 220, a: 0.35, d: 1.0, peak: 0.1, vib: [2.5, 14] });
    tone(ctx, out, { f: 330, a: 0.4, d: 0.95, peak: 0.07, vib: [3, 14] });
    bell(ctx, out, { f: hz(93), t: 0.5, peak: 0.1, d: 0.8, ratios: GLASS, tilt: 1.2, spread: 5 });
  }),

  "class.fx.astronomy": synth(1.1, (ctx, out) => {
    const r = rng(seed("fx.astronomy"));
    for (let i = 0; i < 3; i++) bell(ctx, out, { f: between(r, 2500, 4200), t: i * 0.16 + r() * 0.05, peak: 0.1, d: 0.6, ratios: GLASS, tilt: 1.4, spread: 8 });
    noise(ctx, out, { a: 0.3, d: 0.6, peak: 0.03, f: 6000, q: 2 });
  }),

  "class.fx.herbology": synth(1.1, (ctx, out) => {
    crackle(ctx, out, { spanS: 0.5, count: 26, peak: 0.12, f: 5000, seed: seed("fx.herbology") });
    noise(ctx, out, { a: 0.1, d: 0.35, peak: 0.08, f: 4000, q: 1 });
    tone(ctx, out, { f: 300, f2: 700, glideS: 0.15, t: 0.5, a: 0.01, d: 0.25, peak: 0.15 });
    bell(ctx, out, { f: hz(84), t: 0.62, peak: 0.08, d: 0.35, ratios: HARP, tilt: 1.5 });
  }),

  // A small creature's happy chirps (not any real animal's call).
  "class.fx.creatures": synth(0.7, (ctx, out) => {
    for (const [t, f] of [
      [0, 1800],
      [0.13, 2100],
      [0.24, 2500],
    ] as const)
      tone(ctx, out, { f, f2: f * 1.35, glideS: 0.06, t, a: 0.005, d: 0.08, peak: 0.2, vib: [30, 60] });
  }),
};
