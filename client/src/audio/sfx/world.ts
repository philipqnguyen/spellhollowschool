// The world's synthesized sounds (docs/architecture.md
// "Sound and music"): the statue and wall entrances, the moving staircases,
// brooms, crafting, owl mail, the House Cup and the clock tower. Real-world
// doors, coins, paper and footsteps are CC0 recordings (files.ts).

import type { SoundDefs } from "../types";
import { arp, bell, between, crackle, filter, gainNode, GLASS, HARP, hz, noise, rng, seed, sparkle, synth, thump, tone, whoosh } from "./kit";
import { bubbles, grind } from "./magic";
import { biquad, loopDef, mix, normalise, shape, white, wobble } from "./dsp";

/** An owl's soft hoot: a breathy sine that swells, lifts a little and falls away. */
export function hoot(ctx: BaseAudioContext, out: AudioNode, o: { t: number; f: number; len: number; peak: number; seed: number }): void {
  const r = rng(o.seed);
  const g = ctx.createGain();
  g.gain.setValueAtTime(0, 0);
  g.gain.setValueAtTime(0, o.t);
  g.gain.linearRampToValueAtTime(o.peak, o.t + Math.min(0.06, o.len * 0.3));
  g.gain.linearRampToValueAtTime(o.peak * 0.8, o.t + o.len * 0.6);
  g.gain.linearRampToValueAtTime(0, o.t + o.len);
  g.connect(out);
  const lp = filter(ctx, "lowpass", o.f * 2.5, 0.7, g);
  const osc = ctx.createOscillator();
  osc.frequency.setValueAtTime(o.f * 0.94, o.t);
  osc.frequency.linearRampToValueAtTime(o.f * between(r, 1.0, 1.04), o.t + o.len * 0.3);
  osc.frequency.linearRampToValueAtTime(o.f * 0.9, o.t + o.len);
  const h2 = gainNode(ctx, 0.12, lp);
  const osc2 = ctx.createOscillator();
  osc2.frequency.setValueAtTime(o.f * 0.94 * 2, o.t);
  osc2.frequency.linearRampToValueAtTime(o.f * 0.9 * 2, o.t + o.len);
  osc.connect(lp);
  osc2.connect(h2);
  for (const x of [osc, osc2]) {
    x.start(o.t);
    x.stop(o.t + o.len + 0.05);
  }
  noise(ctx, out, { t: o.t, a: 0.04, h: o.len * 0.4, d: o.len * 0.4, peak: o.peak * 0.12, f: o.f * 2, q: 2, offset: r() });
}

/** One wingbeat: a soft low thrum of air. */
function wingbeat(ctx: BaseAudioContext, out: AudioNode, t: number, peak: number, offset: number): void {
  noise(ctx, out, { t, a: 0.015, d: 0.09, peak, type: "lowpass", f: 900, f2: 400, q: 0.8, offset });
}

export const DOOR_SYNTH_DEFS: SoundDefs = {
  "entrance.statue": synth(1.8, (ctx, out) => {
    grind(ctx, out, { a: 0.15, h: 0.9, r: 0.3, peak: 0.45, f: 160, seed: seed("statue") });
    thump(ctx, out, { t: 1.25, f: 85, f2: 45, d: 0.4, peak: 0.45 });
    crackle(ctx, out, { t: 0.1, spanS: 1.1, count: 14, peak: 0.06, f: 3500, seed: seed("statue-dust") });
  }),

  "entrance.wall": synth(2.2, (ctx, out) => {
    grind(ctx, out, { a: 0.3, h: 1.0, r: 0.4, peak: 0.45, f: 120, seed: seed("wall") });
    tone(ctx, out, { f: 55, f2: 38, glideS: 1.5, a: 0.3, d: 1.4, peak: 0.25, type: "sawtooth", lp: 200 });
    thump(ctx, out, { t: 1.65, f: 75, f2: 40, d: 0.45, peak: 0.5 });
    crackle(ctx, out, { t: 0.2, spanS: 1.5, count: 18, peak: 0.05, f: 3000, seed: seed("wall-dust") });
  }),
};

export const STAIRS_DEFS: SoundDefs = {
  // The flight shudders: a low tremble and dust trickling.
  "stairs.warning": synth(1.6, (ctx, out) => {
    const g = ctx.createGain();
    g.gain.value = 0.5;
    g.connect(out);
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 13;
    lfo.connect(gainNode(ctx, 0.5, g.gain));
    lfo.start(0);
    lfo.stop(1.3);
    noise(ctx, g, { a: 0.15, h: 0.5, d: 0.6, peak: 0.6, type: "lowpass", f: 140, q: 0.9 });
    tone(ctx, g, { f: 48, a: 0.15, d: 1.1, peak: 0.3 });
    crackle(ctx, out, { t: 0.3, spanS: 1.1, count: 22, peak: 0.07, f: 4500, seed: seed("stairs-dust") });
  }),

  // Stone grinding while a flight swings (a seamless loop; the engine fades it).
  "stairs.swing": loopDef(4, (sr, n) => {
    const sway = wobble([3, 5], 7);
    const low = biquad(white(n, seed("swing-a")), "lowpass", 160, 0.8, sr);
    const mid = biquad(white(n, seed("swing-b")), "bandpass", (p) => 420 + 160 * sway(p), 1.2, sr);
    const rough = wobble([7, 11, 13, 17, 23], seed("swing-rough"));
    return normalise(
      shape(mix([low, 1], [mid, 0.45]), (p) => 0.45 + 0.55 * rough(p)),
      0.5,
    );
  }),

  "stairs.dock": synth(1.3, (ctx, out) => {
    thump(ctx, out, { f: 70, f2: 38, d: 0.6, peak: 0.6 });
    noise(ctx, out, { a: 0.002, d: 0.5, peak: 0.35, type: "lowpass", f: 400, q: 0.8 });
    crackle(ctx, out, { t: 0.05, spanS: 0.6, count: 12, peak: 0.06, f: 4000, seed: seed("dock-dust") });
  }),

  // A landing rail conjured in or vanishing: a soft airy poof of smoke with a faint glint in it.
  "stairs.gate": synth(1.0, (ctx, out) => {
    noise(ctx, out, { a: 0.015, d: 0.55, peak: 0.4, f: 1400, f2: 350, q: 0.9 });
    noise(ctx, out, { a: 0.03, d: 0.7, peak: 0.12, type: "lowpass", f: 500, q: 0.7 });
    sparkle(ctx, out, { t: 0.04, spanS: 0.45, count: 6, peak: 0.035, lo: 2500, hi: 6000, seed: seed("rail-poof") });
  }),
};

export const FLIGHT_DEFS: SoundDefs = {
  "broom.mount": synth(1.1, (ctx, out) => {
    whoosh(ctx, out, { f: 350, f2: 2600, a: 0.25, d: 0.7, peak: 0.4, q: 1.1 });
    whoosh(ctx, out, { t: 0.05, f: 200, f2: 900, a: 0.2, d: 0.6, peak: 0.25, q: 0.8, offset: 0.4 });
  }),

  "broom.land": synth(0.9, (ctx, out) => {
    whoosh(ctx, out, { f: 2200, f2: 300, a: 0.06, d: 0.55, peak: 0.35 });
    thump(ctx, out, { t: 0.45, f: 120, f2: 70, d: 0.15, peak: 0.25 });
  }),

  // The rush of air in flight: a seamless loop; the engine sets its rate and tone by speed.
  "broom.wind": loopDef(
    6,
    (sr, n, c) => {
      const gust = wobble([1, 2, 3], seed("wind-gust", c));
      const body = biquad(white(n, seed("wind", c)), "bandpass", (p) => 500 + 600 * gust(p), 0.7, sr);
      const hiss = biquad(white(n, seed("wind-hi", c)), "highpass", 2500, 0.7, sr);
      const low = biquad(white(n, seed("wind-lo", c)), "lowpass", 180, 0.7, sr);
      return normalise(
        shape(mix([body, 1], [hiss, 0.12], [low, 0.6]), (p) => 0.55 + 0.45 * gust(p)),
        0.6,
      );
    },
    { channels: 2 },
  ),

  // Another rider passing close: a swelling, falling whoosh (a little Doppler).
  "broom.flyby": synth(1.3, (ctx, out) => {
    noise(ctx, out, { a: 0.45, d: 0.7, peak: 0.45, f: 2600, f2: 500, sweepS: 1.15, q: 1.4 });
    noise(ctx, out, { a: 0.45, d: 0.6, peak: 0.25, type: "lowpass", f: 600, f2: 250, q: 0.7, offset: 0.5 });
  }),
};

export const CRAFT_DEFS: SoundDefs = {
  "craft.add": synth(
    0.35,
    (ctx, out, v) => {
      const f = [820, 700, 950][v]!;
      tone(ctx, out, { f, f2: f * 0.3, glideS: 0.06, a: 0.002, d: 0.09, peak: 0.45 });
      noise(ctx, out, { t: 0.03, a: 0.005, d: 0.15, peak: 0.12, f: 1800, q: 1, offset: v * 0.3 });
      bubbles(ctx, out, { t: 0.08, spanS: 0.15, count: 3, peak: 0.08, lo: 400, hi: 800, seed: seed("add", v) });
    },
    { variants: 3 },
  ),

  "craft.stir": synth(1.1, (ctx, out) => {
    const g = ctx.createGain();
    g.gain.value = 0.5;
    g.connect(out);
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 2.2;
    lfo.connect(gainNode(ctx, 0.4, g.gain));
    lfo.start(0);
    lfo.stop(1.05);
    noise(ctx, g, { a: 0.1, h: 0.5, d: 0.4, peak: 0.4, f: 650, q: 1.6 });
    bubbles(ctx, out, { spanS: 0.9, count: 8, peak: 0.08, lo: 200, hi: 450, seed: seed("stir") });
  }),

  "craft.success": synth(1.2, (ctx, out) => {
    arp([79, 84, 88, 91], 0.07, 0, (f, t) => bell(ctx, out, { f, t, peak: 0.14, d: 0.7, ratios: GLASS, tilt: 1.3 }));
    sparkle(ctx, out, { t: 0.2, spanS: 0.7, count: 10, peak: 0.06, seed: seed("craft-ok") });
  }),

  "craft.fail": synth(0.8, (ctx, out) => {
    noise(ctx, out, { a: 0.01, d: 0.45, peak: 0.45, type: "lowpass", f: 600, f2: 200, q: 0.7 });
    tone(ctx, out, { f: hz(55), f2: hz(48), glideS: 0.4, a: 0.01, d: 0.45, peak: 0.15, type: "triangle", lp: 900 });
  }),

  "find.found": synth(0.8, (ctx, out) => {
    arp([81, 88], 0.08, 0, (f, t) => bell(ctx, out, { f, t, peak: 0.16, d: 0.5, ratios: GLASS, tilt: 1.3 }));
    bell(ctx, out, { f: hz(69), peak: 0.1, d: 0.4, ratios: HARP, tilt: 1.5 });
  }),

  "find.nothing": synth(0.5, (ctx, out) => {
    arp([64, 60], 0.12, 0, (f, t) => bell(ctx, out, { f, t, peak: 0.16, d: 0.25, ratios: HARP, tilt: 1.8 }));
  }),
};

export const MAIL_DEFS: SoundDefs = {
  // Post: a hoot and a few wingbeats.
  "mail.arrive": synth(1.7, (ctx, out) => {
    hoot(ctx, out, { t: 0, f: 400, len: 0.45, peak: 0.3, seed: seed("arrive") });
    for (let i = 0; i < 5; i++) wingbeat(ctx, out, 0.55 + i * 0.13, 0.35 - i * 0.03, i * 0.17);
  }),

  "owl.hoot": synth(
    1.8,
    (ctx, out, v) => {
      const f = v === 0 ? 390 : 360;
      hoot(ctx, out, { t: 0, f, len: 0.5, peak: 0.35, seed: seed("hoot-a", v) });
      if (v === 0) {
        hoot(ctx, out, { t: 0.9, f: f * 1.02, len: 0.14, peak: 0.25, seed: seed("hoot-b", v) });
        hoot(ctx, out, { t: 1.08, f, len: 0.6, peak: 0.3, seed: seed("hoot-c", v) });
      } else hoot(ctx, out, { t: 0.7, f: f * 0.97, len: 0.55, peak: 0.28, seed: seed("hoot-d", v) });
    },
    { variants: 2 },
  ),

  "trade.complete": synth(0.9, (ctx, out) => {
    crackle(ctx, out, { spanS: 0.2, count: 8, peak: 0.12, f: 6000, seed: seed("trade") });
    arp([84, 91], 0.1, 0.12, (f, t) => bell(ctx, out, { f, t, peak: 0.15, d: 0.5, ratios: GLASS, tilt: 1.3 }));
  }),
};

export const CUP_DEFS: SoundDefs = {
  // Gems pouring into the house's hourglass: many tiny pings thinning out.
  "cup.points": synth(1.6, (ctx, out) => {
    const r = rng(seed("cup"));
    for (let i = 0; i < 28; i++) {
      const u = Math.pow(r(), 1.8);
      bell(ctx, out, { f: between(r, 2200, 5200), t: u * 1.2, peak: 0.09 * (1 - u * 0.6), d: between(r, 0.08, 0.2), ratios: GLASS, tilt: 1.6 });
    }
    noise(ctx, out, { a: 0.05, d: 0.9, peak: 0.05, f: 5000, q: 1 });
  }),
};

export const WORLD_DEFS: SoundDefs = {
  // The clock tower's bell: one toll (the game plays one per hour struck).
  "world.clock_bell": synth(6, (ctx, out) => {
    bell(ctx, out, { f: 196, peak: 0.3, d: 5, ratios: [0.5, 1, 1.19, 1.5, 2, 2.52, 3.01], tilt: 0.8, spread: 3, a: 0.004 });
    noise(ctx, out, { a: 0.002, d: 0.08, peak: 0.15, f: 1200, q: 0.8 });
  }),
};
