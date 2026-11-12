// Interface sounds, synthesized (docs/architecture.md
// "Sound and music"). Soft, short and warm: wood and glass rather than beeps,
// so a menu clicked a hundred times never grates. Original recipes; the
// coins, cloth and paper are CC0 recordings (files.ts).

import type { SoundDefs } from "../types";
import { arp, bell, GLASS, HARP, hz, noise, seed, sparkle, synth, tone } from "./kit";

export const UI_DEFS: SoundDefs = {
  "ui.click": synth(
    0.09,
    (ctx, out, v) => {
      const f = [1850, 1700, 2000][v]!;
      tone(ctx, out, { f, a: 0.001, d: 0.035, peak: 0.35 });
      tone(ctx, out, { f: f * 0.5, a: 0.001, d: 0.05, peak: 0.25 });
      noise(ctx, out, { a: 0.001, d: 0.012, peak: 0.3, f: 3500, q: 1.2, offset: v * 0.3 });
    },
    { variants: 3 },
  ),

  "ui.open": synth(0.5, (ctx, out) => {
    noise(ctx, out, { a: 0.06, d: 0.18, peak: 0.12, f: 900, f2: 3200, q: 0.9 });
    arp([76, 83], 0.07, 0.02, (f, t) => bell(ctx, out, { f, t, peak: 0.28, d: 0.35, ratios: GLASS, tilt: 1.4 }));
  }),

  "ui.close": synth(0.45, (ctx, out) => {
    noise(ctx, out, { a: 0.03, d: 0.16, peak: 0.1, f: 2800, f2: 800, q: 0.9 });
    arp([83, 76], 0.06, 0.0, (f, t) => bell(ctx, out, { f, t, peak: 0.22, d: 0.25, ratios: GLASS, tilt: 1.6 }));
  }),

  "ui.tab": synth(0.2, (ctx, out) => {
    bell(ctx, out, { f: hz(81), peak: 0.3, d: 0.14, ratios: HARP, tilt: 1.8 });
    noise(ctx, out, { a: 0.001, d: 0.01, peak: 0.12, f: 4000, q: 1 });
  }),

  "ui.toggle": synth(
    0.2,
    (ctx, out, v) => {
      const [a, b] = v === 0 ? [74, 79] : [79, 74];
      tone(ctx, out, { f: hz(a), a: 0.001, d: 0.05, peak: 0.28, type: "triangle" });
      tone(ctx, out, { f: hz(b), t: 0.055, a: 0.001, d: 0.08, peak: 0.28, type: "triangle" });
    },
    { variants: 2 },
  ),

  "ui.slider": synth(0.06, (ctx, out) => {
    tone(ctx, out, { f: 2400, a: 0.001, d: 0.025, peak: 0.3 });
    noise(ctx, out, { a: 0.001, d: 0.008, peak: 0.12, f: 5000, q: 1 });
  }),

  // A soft, low "no": two muted notes stepping down, never a buzzer.
  "ui.error": synth(0.4, (ctx, out) => {
    tone(ctx, out, { f: hz(57), a: 0.005, d: 0.13, peak: 0.4, type: "square", lp: 900 });
    tone(ctx, out, { f: hz(53), t: 0.12, a: 0.005, d: 0.2, peak: 0.4, type: "square", lp: 700 });
  }),

  "ui.notify": synth(0.9, (ctx, out) => {
    arp([79, 84, 86], 0.085, 0, (f, t, i) => bell(ctx, out, { f, t, peak: 0.24 - i * 0.03, d: 0.6, ratios: GLASS, tilt: 1.3 }));
  }),

  // A breath and a faint shimmer: someone close by says something only to you.
  "ui.whisper": synth(0.8, (ctx, out) => {
    noise(ctx, out, { a: 0.12, h: 0.08, d: 0.35, peak: 0.22, f: 2600, f2: 1800, q: 1.6 });
    tone(ctx, out, { f: hz(88), a: 0.1, d: 0.45, peak: 0.05, vib: [6, 12] });
    tone(ctx, out, { f: hz(93), t: 0.05, a: 0.1, d: 0.4, peak: 0.03 });
  }),

  "ui.toast": synth(0.7, (ctx, out) => {
    bell(ctx, out, { f: hz(84), peak: 0.22, d: 0.5, ratios: GLASS, tilt: 1.4 });
    bell(ctx, out, { f: hz(91), t: 0.03, peak: 0.12, d: 0.45, ratios: GLASS, tilt: 1.6 });
  }),

  "ui.invite": synth(1.1, (ctx, out) => {
    for (const [n, t] of [
      [86, 0],
      [81, 0.16],
      [86, 0.32],
    ] as const)
      bell(ctx, out, { f: hz(n), t, peak: 0.2, d: 0.55, ratios: GLASS, tilt: 1.3, spread: 4 });
  }),

  "ui.select": synth(0.18, (ctx, out) => {
    tone(ctx, out, { f: 1200, f2: 1600, glideS: 0.03, a: 0.002, d: 0.1, peak: 0.28 });
    tone(ctx, out, { f: 2400, t: 0.01, a: 0.002, d: 0.06, peak: 0.08 });
  }),

  // A soft magical wash of colour: a rising filtered breath and glitter.
  "ui.dye": synth(1.0, (ctx, out) => {
    noise(ctx, out, { a: 0.25, h: 0.1, d: 0.5, peak: 0.18, type: "lowpass", f: 500, f2: 5000, q: 2 });
    sparkle(ctx, out, { t: 0.15, spanS: 0.6, count: 9, peak: 0.08, lo: 2600, hi: 5200, seed: seed("ui.dye") });
  }),

  "ui.friend_online": synth(0.8, (ctx, out) => {
    arp([79, 86], 0.11, 0, (f, t) => bell(ctx, out, { f, t, peak: 0.22, d: 0.55, ratios: HARP, tilt: 1.5 }));
  }),
};
