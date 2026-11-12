// Magic, synthesized (docs/architecture.md "Sound and music"):
// spells, duels, potions' effects, secrets and the level-up glints.
// Families share a voice so the game sounds of a piece: charms chime (glass
// and bell partials), jinxes and hexes crack and zip (noise bursts and fast
// pitch drops through a little grit), shields hum (detuned low saws), and
// failures sputter. Original recipes throughout.

import type { SoundDefs } from "../types";
import { arp, bell, between, BELL, crackle, echo, filter, gainNode, GLASS, HARP, hz, noise, noiseBuffer, rng, saturate, seed, sparkle, synth, thump, tone, whoosh } from "./kit";
import { addEvent, biquad, blip, loopDef, mix, normalise, shape, white, wobble } from "./dsp";

/** A bolt leaving the wand: a crack, then a zip that drops in pitch. */
function bolt(ctx: BaseAudioContext, out: AudioNode, o: { f: number; f2: number; zipS: number; crack: number; grit: number; peak: number; seed: number; type?: OscillatorType }): void {
  const dirt = saturate(ctx, o.grit, gainNode(ctx, 0.8, out));
  noise(ctx, out, { a: 0.001, d: 0.04, peak: o.crack, type: "highpass", f: 1800, q: 0.7, offset: (o.seed % 97) / 97 });
  tone(ctx, dirt, { f: o.f, f2: o.f2, glideS: o.zipS, a: 0.003, d: o.zipS + 0.08, peak: o.peak, type: o.type ?? "sawtooth", lp: 5000 });
  tone(ctx, out, { f: o.f * 1.5, f2: o.f2 * 1.5, glideS: o.zipS, a: 0.003, d: o.zipS, peak: o.peak * 0.25 });
  noise(ctx, out, { a: 0.01, d: o.zipS + 0.1, peak: o.peak * 0.35, f: o.f * 2, f2: o.f2 * 2, q: 2, offset: ((o.seed >>> 3) % 89) / 89 });
}

/** A shield's hum: two detuned low saws, softened, with a bright edge. */
function hum(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; f: number; a: number; h: number; r: number; peak: number }): void {
  const t = o.t ?? 0;
  const g = ctx.createGain();
  g.gain.setValueAtTime(0, 0);
  g.gain.setValueAtTime(0, t);
  g.gain.linearRampToValueAtTime(o.peak, t + o.a);
  g.gain.setValueAtTime(o.peak, t + o.a + o.h);
  g.gain.linearRampToValueAtTime(0, t + o.a + o.h + o.r);
  g.connect(out);
  const lp = filter(ctx, "lowpass", o.f * 5, 0.8, g);
  for (const det of [-9, 0, 8]) {
    const osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.value = o.f;
    osc.detune.value = det;
    osc.connect(lp);
    osc.start(t);
    osc.stop(t + o.a + o.h + o.r + 0.05);
  }
}

/** Bubbles: sine blips gliding up, scattered (a cauldron, a brew, Mimic Draught). */
function bubbles(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; spanS: number; count: number; peak: number; lo: number; hi: number; seed: number }): void {
  const r = rng(o.seed);
  for (let i = 0; i < o.count; i++) {
    const f = between(r, o.lo, o.hi);
    tone(ctx, out, { f, f2: f * between(r, 1.6, 2.6), glideS: 0.04, t: (o.t ?? 0) + r() * o.spanS, a: 0.002, d: between(r, 0.03, 0.07), peak: o.peak * between(r, 0.5, 1) });
  }
}

/** Stone grinding: low noise, roughened by a fast random tremble. */
export function grind(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; a: number; h: number; r: number; peak: number; f: number; seed: number }): void {
  const t = o.t ?? 0;
  const g = ctx.createGain();
  g.gain.value = 0;
  const r = rng(o.seed);
  const end = t + o.a + o.h + o.r;
  g.gain.setValueAtTime(0, t);
  for (let x = t; x < end; x += 0.035) {
    const env = x < t + o.a ? (x - t) / o.a : x < t + o.a + o.h ? 1 : Math.max(0, 1 - (x - t - o.a - o.h) / o.r);
    g.gain.linearRampToValueAtTime(o.peak * env * between(r, 0.45, 1), x);
  }
  g.gain.linearRampToValueAtTime(0, end + 0.02);
  g.connect(out);
  const lp = filter(ctx, "lowpass", o.f, 0.9, g);
  const bp = filter(ctx, "bandpass", o.f * 3, 0.8, gainNode(ctx, 0.35, g));
  for (const head of [lp, bp]) {
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer(ctx);
    src.loop = true;
    src.connect(head);
    src.start(t, r() * 1.5);
    src.stop(end + 0.05);
  }
}

export const SPELL_DEFS: SoundDefs = {
  "spell.lucerna": synth(1.0, (ctx, out) => {
    noise(ctx, out, { a: 0.02, d: 0.25, peak: 0.12, f: 4200, q: 1.4 });
    bell(ctx, out, { f: hz(91), peak: 0.22, d: 0.7, ratios: GLASS, tilt: 1.2, spread: 5 });
    bell(ctx, out, { f: hz(98), t: 0.04, peak: 0.1, d: 0.5, ratios: GLASS, tilt: 1.5 });
    sparkle(ctx, out, { t: 0.03, spanS: 0.35, count: 6, peak: 0.06, lo: 4000, hi: 8000, seed: seed("lucerna") });
  }),

  "spell.lucerna_off": synth(0.6, (ctx, out) => {
    noise(ctx, out, { a: 0.01, d: 0.35, peak: 0.25, type: "lowpass", f: 3200, f2: 300, q: 0.8 });
    tone(ctx, out, { f: hz(79), f2: hz(67), glideS: 0.25, a: 0.005, d: 0.3, peak: 0.08 });
  }),

  "spell.elevara": synth(1.4, (ctx, out) => {
    noise(ctx, out, { a: 0.5, h: 0.1, d: 0.6, peak: 0.12, f: 600, f2: 4000, q: 1.5 });
    arp([74, 76, 79, 81, 86, 88], 0.09, 0.05, (f, t, i) => bell(ctx, out, { f, t, peak: 0.12 + i * 0.012, d: 0.45, ratios: GLASS, tilt: 1.4 }));
    tone(ctx, out, { f: hz(62), f2: hz(74), glideS: 0.8, a: 0.2, d: 0.9, peak: 0.06, vib: [5, 15] });
  }),

  "spell.levitate_end": synth(0.7, (ctx, out) => {
    tone(ctx, out, { f: 900, f2: 380, glideS: 0.35, a: 0.01, d: 0.35, peak: 0.16, vib: [7, 20] });
    thump(ctx, out, { t: 0.32, f: 160, f2: 80, d: 0.18, peak: 0.35 });
    noise(ctx, out, { t: 0.32, a: 0.002, d: 0.08, peak: 0.12, type: "lowpass", f: 900, q: 0.7 });
  }),

  // The shield blooms (a soft burst) and hums, then fades.
  "spell.scutara": synth(1.3, (ctx, out) => {
    noise(ctx, out, { a: 0.02, d: 0.3, peak: 0.25, f: 1200, f2: 500, q: 0.8 });
    hum(ctx, out, { f: hz(45), a: 0.06, h: 0.35, r: 0.7, peak: 0.12 });
    hum(ctx, out, { f: hz(52), a: 0.08, h: 0.3, r: 0.7, peak: 0.07 });
    bell(ctx, echo(ctx, 0.045, 0.45, 0.4, out), { f: hz(81), t: 0.01, peak: 0.12, d: 0.6, ratios: GLASS, tilt: 1.3 });
  }),

  "spell.torpeo": synth(0.6, (ctx, out) => bolt(ctx, out, { f: 1700, f2: 260, zipS: 0.22, crack: 0.5, grit: 3, peak: 0.25, seed: seed("torpeo") })),

  "spell.dimitto": synth(0.6, (ctx, out) => {
    noise(ctx, out, { a: 0.0008, d: 0.02, peak: 0.7, type: "highpass", f: 2500, q: 0.7 });
    bolt(ctx, out, { f: 3000, f2: 700, zipS: 0.14, crack: 0.3, grit: 2, peak: 0.18, seed: seed("dimitto"), type: "square" });
    bell(ctx, out, { f: 1320, t: 0.02, peak: 0.08, d: 0.35, ratios: BELL, tilt: 1.2 });
  }),

  // A fizzing, tickling bolt: buzzy, wobbly, a little silly.
  "spell.titillara": synth(0.8, (ctx, out) => {
    const trem = ctx.createGain();
    trem.gain.value = 0.5;
    trem.connect(out);
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 28;
    const depth = gainNode(ctx, 0.5, trem.gain);
    lfo.connect(depth);
    lfo.start(0);
    lfo.stop(0.75);
    noise(ctx, trem, { a: 0.01, d: 0.55, peak: 0.45, f: 3500, f2: 1800, q: 2 });
    tone(ctx, trem, { f: 1200, f2: 700, glideS: 0.5, a: 0.01, d: 0.55, peak: 0.18, vib: [11, 120], type: "triangle" });
    crackle(ctx, out, { t: 0.05, spanS: 0.45, count: 10, peak: 0.12, f: 5000, seed: seed("titillara") });
  }),

  "spell.repulsa": synth(0.55, (ctx, out) => {
    thump(ctx, out, { f: 150, f2: 55, d: 0.3, peak: 0.6 });
    noise(ctx, out, { a: 0.005, d: 0.25, peak: 0.35, type: "lowpass", f: 700, f2: 250, q: 0.8 });
    whoosh(ctx, out, { f: 500, f2: 1500, a: 0.04, d: 0.25, peak: 0.12 });
  }),

  "spell.trail": synth(
    0.7,
    (ctx, out, v) => {
      const r = rng(seed("trail", v));
      const f0 = between(r, 900, 1300);
      noise(ctx, out, { a: 0.15, h: 0.05, d: 0.35, peak: 0.16, f: f0, f2: f0 * 3, q: 2.2, offset: r() });
      sparkle(ctx, out, { t: 0.1, spanS: 0.4, count: 4, peak: 0.04, lo: 3000, hi: 6000, seed: seed("trail-s", v) });
    },
    { variants: 3 },
  ),

  "spell.fizzle": synth(0.7, (ctx, out) => {
    crackle(ctx, out, { spanS: 0.5, count: 18, peak: 0.3, f: 2200, seed: seed("fizzle") });
    tone(ctx, out, { f: 620, f2: 180, glideS: 0.45, a: 0.005, d: 0.45, peak: 0.12, type: "triangle", vib: [14, 40] });
    noise(ctx, out, { a: 0.005, d: 0.4, peak: 0.08, type: "lowpass", f: 1500, f2: 400, q: 0.7 });
  }),

  "spell.monstra_viam": synth(1.2, (ctx, out) => {
    arp([81, 86, 88, 93], 0.1, 0.02, (f, t, i) => bell(ctx, out, { f, t, peak: 0.16 - i * 0.015, d: 0.6, ratios: GLASS, tilt: 1.3, spread: 4 }));
    noise(ctx, out, { a: 0.2, d: 0.5, peak: 0.05, f: 3000, f2: 6000, q: 1.5 });
  }),
};

export const DUEL_DEFS: SoundDefs = {
  "duel.countdown": synth(
    0.35,
    (ctx, out, v) => {
      const f = v === 0 ? 620 : 930;
      bell(ctx, out, { f, peak: 0.35, d: 0.25, ratios: [1, 2.76, 5.4], tilt: 1.6 });
      noise(ctx, out, { a: 0.001, d: 0.02, peak: 0.25, f: 2000, q: 1 });
    },
    { variants: 2 },
  ),

  // A struck gong: the fight begins.
  "duel.fight": synth(3.0, (ctx, out) => {
    bell(ctx, out, { f: 98, peak: 0.35, d: 2.6, ratios: [1, 1.48, 1.98, 2.44, 2.9, 3.47, 4.1], tilt: 0.7, spread: 6, a: 0.01 });
    noise(ctx, out, { a: 0.002, d: 0.15, peak: 0.3, type: "lowpass", f: 1500, q: 0.7 });
    noise(ctx, out, { a: 0.3, d: 1.8, peak: 0.04, f: 3000, q: 3 });
  }),

  "duel.charge": synth(1.0, (ctx, out) => {
    const g = gainNode(ctx, 1, out);
    const lp = filter(ctx, "lowpass", 300, 4, g);
    lp.frequency.setValueAtTime(300, 0);
    lp.frequency.exponentialRampToValueAtTime(3500, 0.8);
    tone(ctx, lp, { f: 180, f2: 560, glideS: 0.8, a: 0.4, d: 0.5, peak: 0.25, type: "sawtooth", vib: [9, 25] });
    crackle(ctx, out, { t: 0.1, spanS: 0.7, count: 12, peak: 0.08, f: 4000, seed: seed("charge") });
  }),

  "duel.blocked": synth(0.7, (ctx, out) => {
    noise(ctx, out, { a: 0.001, d: 0.06, peak: 0.5, f: 1500, q: 0.6 });
    bell(ctx, echo(ctx, 0.03, 0.4, 0.35, out), { f: 610, peak: 0.25, d: 0.5, ratios: BELL, tilt: 0.9, spread: 7 });
    thump(ctx, out, { f: 120, f2: 70, d: 0.15, peak: 0.3 });
  }),

  "duel.reflected": synth(0.7, (ctx, out) => {
    bell(ctx, out, { f: 1320, peak: 0.25, d: 0.45, ratios: GLASS, tilt: 1.1 });
    tone(ctx, saturate(ctx, 2, gainNode(ctx, 0.7, out)), { f: 400, f2: 2600, glideS: 0.2, t: 0.03, a: 0.003, d: 0.28, peak: 0.18, type: "sawtooth", lp: 5000 });
    noise(ctx, out, { t: 0.03, a: 0.01, d: 0.25, peak: 0.12, f: 1000, f2: 5000, q: 2 });
  }),

  "duel.shield_broken": synth(1.1, (ctx, out) => {
    noise(ctx, out, { a: 0.001, d: 0.3, peak: 0.45, type: "highpass", f: 2500, q: 0.7 });
    const r = rng(seed("shatter"));
    for (let i = 0; i < 16; i++) bell(ctx, out, { f: between(r, 1800, 6000), t: r() * 0.45, peak: between(r, 0.03, 0.1), d: between(r, 0.1, 0.35), ratios: GLASS, tilt: 1.5 });
    tone(ctx, out, { f: hz(57), f2: hz(45), glideS: 0.5, a: 0.002, d: 0.6, peak: 0.12, type: "sawtooth", lp: 600 });
  }),

  "duel.hit": synth(0.45, (ctx, out) => {
    thump(ctx, out, { f: 130, f2: 50, d: 0.25, peak: 0.6 });
    noise(ctx, saturate(ctx, 2.5, gainNode(ctx, 0.6, out)), { a: 0.001, d: 0.12, peak: 0.6, f: 900, q: 0.8 });
    noise(ctx, out, { a: 0.001, d: 0.05, peak: 0.25, type: "highpass", f: 3000, q: 0.7 });
  }),

  "duel.disarmed": synth(1.1, (ctx, out) => {
    noise(ctx, out, { a: 0.0008, d: 0.02, peak: 0.6, type: "highpass", f: 2500, q: 0.7 });
    // The wand spinning away: a whistling whirl falling in pitch.
    const g = ctx.createGain();
    g.gain.value = 0.5;
    g.connect(out);
    const lfo = ctx.createOscillator();
    lfo.frequency.setValueAtTime(14, 0);
    lfo.frequency.linearRampToValueAtTime(6, 0.7);
    lfo.connect(gainNode(ctx, 0.5, g.gain));
    lfo.start(0.02);
    lfo.stop(0.8);
    noise(ctx, g, { t: 0.02, a: 0.02, d: 0.65, peak: 0.4, f: 2400, f2: 900, q: 6 });
    // ...and clattering down.
    for (const [t, f] of [
      [0.72, 1900],
      [0.84, 2300],
      [0.92, 1700],
    ] as const)
      bell(ctx, out, { f, t, peak: 0.12, d: 0.06, ratios: [1, 2.3, 3.9], tilt: 1.2 });
  }),

  "duel.interrupted": synth(0.6, (ctx, out) => {
    tone(ctx, out, { f: 200, f2: 420, glideS: 0.18, a: 0.08, d: 0.12, peak: 0.18, type: "sawtooth", lp: 1800 });
    tone(ctx, out, { f: 420, f2: 90, glideS: 0.25, t: 0.2, a: 0.002, d: 0.25, peak: 0.16, type: "sawtooth", lp: 1200 });
    crackle(ctx, out, { t: 0.2, spanS: 0.3, count: 8, peak: 0.15, f: 2500, seed: seed("interrupted") });
  }),

  "duel.missed": synth(0.7, (ctx, out) => {
    noise(ctx, out, { a: 0.18, d: 0.35, peak: 0.25, f: 3000, f2: 700, q: 3 });
    tone(ctx, out, { f: 1400, f2: 600, glideS: 0.5, a: 0.15, d: 0.35, peak: 0.06, vib: [16, 50] });
  }),

  // Spells to level 20 (protocol v11): what the new duel spells do to a duelist.
  // A bind locks with a stony knock and frost; a slow drags a falling tone; a
  // pull is a whip and a body landing; a cleanse rises free; a ward swells
  // silver, rings when it stops a spell and sighs when it fades.
  "duel.rooted": synth(0.8, (ctx, out) => {
    thump(ctx, out, { f: 110, f2: 60, d: 0.2, peak: 0.45 });
    noise(ctx, out, { a: 0.001, d: 0.05, peak: 0.4, type: "highpass", f: 2000, q: 0.7 });
    frost(ctx, out, { t: 0.02, spanS: 0.45, count: 16, peak: 0.16, seed: seed("duel-rooted") });
    bell(ctx, out, { f: hz(88), t: 0.05, peak: 0.12, d: 0.55, ratios: GLASS, tilt: 1.3 });
  }),

  "duel.slowed": synth(1.2, (ctx, out) => {
    slur(ctx, out, { f: 420, f2: 120, s: 0.9, peak: 0.2 });
    noise(ctx, out, { a: 0.15, h: 0.1, d: 0.75, peak: 0.22, type: "lowpass", f: 1400, f2: 250, q: 0.9 });
    thump(ctx, out, { f: 90, f2: 50, d: 0.3, peak: 0.25 });
  }),

  "duel.pulled": synth(0.7, (ctx, out) => {
    yank(ctx, out, { peak: 0.3, seed: seed("duel-pulled") });
    thump(ctx, out, { t: 0.3, f: 140, f2: 60, d: 0.22, peak: 0.45 });
    noise(ctx, out, { t: 0.3, a: 0.002, d: 0.1, peak: 0.2, type: "lowpass", f: 900, q: 0.7 });
  }),

  "duel.cleansed": synth(1.0, (ctx, out) => {
    noise(ctx, out, { a: 0.05, d: 0.45, peak: 0.16, f: 700, f2: 4500, q: 1.4 });
    arp([76, 81, 85, 88], 0.06, 0.02, (f, t, i) => bell(ctx, out, { f, t, peak: 0.16 + i * 0.01, d: 0.5, ratios: GLASS, tilt: 1.3, spread: 4 }));
  }),

  "duel.ward_up": synth(1.5, (ctx, out) => {
    noise(ctx, out, { a: 0.3, h: 0.1, d: 0.8, peak: 0.12, f: 2500, f2: 6000, q: 1.2 });
    hum(ctx, out, { f: hz(50), a: 0.25, h: 0.4, r: 0.7, peak: 0.08 });
    arp([67, 74, 79, 83], 0.1, 0.05, (f, t, i) => bell(ctx, echo(ctx, 0.06, 0.4, 0.35, out), { f, t, peak: 0.1 + i * 0.01, d: 0.7, ratios: GLASS, tilt: 1.2, spread: 5 }));
  }),

  "duel.warded": synth(1.1, (ctx, out) => {
    noise(ctx, out, { a: 0.001, d: 0.15, peak: 0.5, type: "highpass", f: 3000, q: 0.7 });
    thump(ctx, out, { f: 160, f2: 90, d: 0.15, peak: 0.3 });
    bell(ctx, echo(ctx, 0.05, 0.45, 0.4, out), { f: hz(84), peak: 0.22, d: 0.8, ratios: BELL, tilt: 0.9, spread: 8 });
    const r = rng(seed("warded"));
    for (let i = 0; i < 10; i++) bell(ctx, out, { f: between(r, 2400, 6500), t: 0.02 + r() * 0.35, peak: between(r, 0.03, 0.08), d: between(r, 0.1, 0.3), ratios: GLASS, tilt: 1.5 });
  }),

  "duel.ward_faded": synth(1.0, (ctx, out) => {
    noise(ctx, out, { a: 0.05, d: 0.8, peak: 0.12, f: 5000, f2: 1200, q: 1.5 });
    tone(ctx, out, { f: hz(83), f2: hz(71), glideS: 0.7, a: 0.02, d: 0.75, peak: 0.07, vib: [6, 20] });
  }),
};

/** Ice-white crackle: brief glassy ticks scattered over `spanS` (a body stiffening). */
function frost(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; spanS: number; count: number; peak: number; seed: number }): void {
  const r = rng(o.seed);
  for (let i = 0; i < o.count; i++) {
    const t = (o.t ?? 0) + r() * o.spanS;
    if (r() < 0.5) noise(ctx, out, { t, a: 0.001, d: between(r, 0.008, 0.02), peak: o.peak * between(r, 0.5, 1), type: "highpass", f: between(r, 3000, 6000), q: 0.8, offset: r() });
    else tone(ctx, out, { f: between(r, 3500, 7500), t, a: 0.001, d: between(r, 0.02, 0.06), peak: o.peak * 0.5 * between(r, 0.5, 1) });
  }
}

/** A slurred, slowing tone: a pitch that sags while its wobble slows (tape running down). */
function slur(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; f: number; f2: number; s: number; peak: number }): void {
  const t = o.t ?? 0;
  const g = ctx.createGain();
  g.gain.value = 0.6;
  g.connect(out);
  const lfo = ctx.createOscillator();
  lfo.frequency.setValueAtTime(9, t);
  lfo.frequency.linearRampToValueAtTime(2.5, t + o.s);
  lfo.connect(gainNode(ctx, 0.4, g.gain));
  lfo.start(t);
  lfo.stop(t + o.s + 0.05);
  tone(ctx, g, { f: o.f, f2: o.f2, glideS: o.s, t, a: 0.03, d: o.s - 0.03, peak: o.peak, type: "triangle", lp: 2400 });
  tone(ctx, g, { f: o.f * 1.5, f2: o.f2 * 1.5, glideS: o.s, t, a: 0.03, d: o.s - 0.03, peak: o.peak * 0.3, lp: 2400 });
}

/** A yank: a whip-crack out, then a fast rising rush back in (Summoning Charm). */
function yank(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; peak: number; seed: number }): void {
  const t = o.t ?? 0;
  noise(ctx, out, { t, a: 0.0008, d: 0.03, peak: o.peak * 1.6, type: "highpass", f: 2500, q: 0.7, offset: (o.seed % 89) / 89 });
  noise(ctx, out, { t: t + 0.02, a: 0.18, h: 0, d: 0.08, peak: o.peak, f: 500, f2: 3500, sweepS: 0.26, q: 2 });
  tone(ctx, saturate(ctx, 2, gainNode(ctx, 0.6, out)), { f: 220, f2: 900, glideS: 0.25, t: t + 0.02, a: 0.15, d: 0.1, peak: o.peak * 0.4, type: "sawtooth", lp: 3000 });
}

/** Wings: band-passed noise beating at `rate` Hz, fading over `spanS` (a small flock). */
function wings(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; spanS: number; rate: number; peak: number; f: number; offset: number }): void {
  const t = o.t ?? 0;
  const g = ctx.createGain();
  g.gain.value = 0.5;
  g.connect(out);
  const lfo = ctx.createOscillator();
  lfo.frequency.setValueAtTime(o.rate, t);
  lfo.frequency.linearRampToValueAtTime(o.rate * 0.8, t + o.spanS);
  lfo.connect(gainNode(ctx, 0.5, g.gain));
  lfo.start(t);
  lfo.stop(t + o.spanS + 0.05);
  noise(ctx, g, { t, a: 0.03, d: o.spanS - 0.03, peak: o.peak, f: o.f, f2: o.f * 0.7, q: 1.1, offset: o.offset });
}

/** A small bird's chirp: two or three quick upward glides. */
function chirp(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; peak: number; seed: number }): void {
  const r = rng(o.seed);
  const n = 2 + Math.floor(r() * 2);
  let t = o.t ?? 0;
  const base = between(r, 2600, 3600);
  for (let i = 0; i < n; i++) {
    const f = base * between(r, 0.9, 1.15);
    tone(ctx, out, { f, f2: f * between(r, 1.25, 1.6), glideS: 0.045, t, a: 0.003, d: 0.05, peak: o.peak, vib: [40, 60] });
    t += between(r, 0.065, 0.09);
  }
}

/**
 * Spells to level 20 (original recipes throughout): the
 * eight new spells' casts in their families (charms chime, jinxes crack and
 * zip, the Guardian swells), and the sounds of what they do outside a duel.
 */
export const SPELL_L20_DEFS: SoundDefs = {
  // A colour wash: a glass arpeggio swirling up and back, glitter on top.
  "spell.tinctura": synth(1.1, (ctx, out) => {
    noise(ctx, out, { a: 0.2, h: 0.05, d: 0.5, peak: 0.1, f: 900, f2: 5000, q: 2 });
    arp([79, 83, 86, 91, 88, 84], 0.07, 0.02, (f, t, i) => bell(ctx, out, { f, t, peak: 0.14 - i * 0.008, d: 0.4, ratios: GLASS, tilt: 1.3, spread: 6 }));
    sparkle(ctx, out, { t: 0.15, spanS: 0.55, count: 12, peak: 0.07, lo: 3500, hi: 8000, seed: seed("tinctura") });
  }),

  // A stiff, chalky crack and a jinx's zip, frost settling after it.
  "spell.rigora": synth(0.8, (ctx, out) => {
    bolt(ctx, out, { f: 1300, f2: 420, zipS: 0.26, crack: 0.55, grit: 2, peak: 0.18, seed: seed("rigora"), type: "square" });
    frost(ctx, out, { t: 0.1, spanS: 0.45, count: 14, peak: 0.12, seed: seed("rigora-frost") });
  }),

  // A soft pop, a rush of feathers and a few chirps.
  "spell.volucra": synth(1.0, (ctx, out) => {
    thump(ctx, out, { f: 420, f2: 180, d: 0.08, peak: 0.3 });
    noise(ctx, out, { a: 0.001, d: 0.05, peak: 0.2, type: "highpass", f: 2200, q: 0.7 });
    wings(ctx, out, { t: 0.03, spanS: 0.6, rate: 16, peak: 0.3, f: 2200, offset: 0.3 });
    chirp(ctx, out, { t: 0.2, peak: 0.08, seed: seed("volucra-1") });
    chirp(ctx, out, { t: 0.5, peak: 0.06, seed: seed("volucra-2") });
  }),

  // A hooked, reeling zip: the line flies out, then the reel ratchets it in.
  "spell.adveni": synth(0.8, (ctx, out) => {
    bolt(ctx, out, { f: 700, f2: 1900, zipS: 0.18, crack: 0.4, grit: 2, peak: 0.16, seed: seed("adveni"), type: "sawtooth" });
    for (let i = 0; i < 8; i++) noise(ctx, out, { t: 0.22 + i * 0.045, a: 0.001, d: 0.014, peak: 0.22 - i * 0.015, f: 2600 + i * 120, q: 3, offset: i / 8 });
  }),

  // An unravelling chime: a glass run tumbling down, a breath going out.
  "spell.dissolvo": synth(1.0, (ctx, out) => {
    arp([93, 88, 84, 81, 76, 72], 0.05, 0.0, (f, t, i) => bell(ctx, out, { f, t, peak: 0.15 - i * 0.012, d: 0.4, ratios: GLASS, tilt: 1.3 }));
    noise(ctx, out, { a: 0.02, d: 0.5, peak: 0.1, f: 4000, f2: 900, q: 1.2 });
  }),

  // A searching ring: a ping that glides up and echoes out.
  "spell.aperio": synth(1.4, (ctx, out) => {
    const room = echo(ctx, 0.11, 0.45, 0.4, out);
    tone(ctx, room, { f: hz(81), f2: hz(88), glideS: 0.35, a: 0.01, d: 0.7, peak: 0.12, vib: [5, 10] });
    bell(ctx, room, { f: hz(93), t: 0.05, peak: 0.12, d: 0.6, ratios: GLASS, tilt: 1.3 });
    noise(ctx, out, { a: 0.25, d: 0.6, peak: 0.06, f: 1500, f2: 6000, q: 2 });
  }),

  // A slow, heavy wave: a low surge rolling forward and a sagging tone.
  "spell.tardara": synth(1.2, (ctx, out) => {
    noise(ctx, out, { a: 0.12, h: 0.1, d: 0.7, peak: 0.3, type: "lowpass", f: 500, f2: 1600, sweepS: 0.4, q: 1.4 });
    slur(ctx, out, { t: 0.05, f: 520, f2: 180, s: 0.9, peak: 0.16 });
    thump(ctx, out, { f: 95, f2: 55, d: 0.35, peak: 0.35 });
  }),

  // A rising silver swell: a soft chord blooming upward, glitter, air.
  "spell.custodio": synth(1.9, (ctx, out) => {
    noise(ctx, out, { a: 0.6, h: 0.2, d: 0.9, peak: 0.08, f: 3000, f2: 7000, q: 1.2 });
    for (const [n, d] of [
      [55, 0],
      [62, 6],
      [67, -5],
    ] as const)
      tone(ctx, out, { f: hz(n), f2: hz(n + 12), glideS: 1.2, a: 0.5, d: 1.2, peak: 0.07, detune: d, vib: [4, 7] });
    arp([79, 83, 86, 91], 0.14, 0.4, (f, t, i) => bell(ctx, echo(ctx, 0.07, 0.4, 0.35, out), { f, t, peak: 0.1 + i * 0.01, d: 0.7, ratios: GLASS, tilt: 1.2, spread: 5 }));
    sparkle(ctx, out, { t: 0.5, spanS: 0.9, count: 14, peak: 0.06, lo: 4000, hi: 8500, seed: seed("guardian-cast") });
  }),

  // Someone bound stiff: frost creeping over a stony knock.
  "spell.bound": synth(0.8, (ctx, out) => {
    thump(ctx, out, { f: 180, f2: 90, d: 0.12, peak: 0.3 });
    frost(ctx, out, { spanS: 0.5, count: 18, peak: 0.16, seed: seed("bound") });
    noise(ctx, out, { a: 0.05, d: 0.35, peak: 0.1, type: "highpass", f: 4000, q: 0.7 });
  }),

  // Someone slowed: a slurred whoosh running down.
  "spell.slowed": synth(1.1, (ctx, out) => {
    slur(ctx, out, { f: 600, f2: 200, s: 0.85, peak: 0.15 });
    noise(ctx, out, { a: 0.12, h: 0.05, d: 0.7, peak: 0.16, f: 2200, f2: 500, q: 1.5 });
  }),

  // Someone yanked toward the caster.
  "spell.pulled": synth(0.6, (ctx, out) => {
    yank(ctx, out, { peak: 0.24, seed: seed("pulled") });
    thump(ctx, out, { t: 0.3, f: 160, f2: 80, d: 0.15, peak: 0.25 });
  }),

  // Spells ended: a quick glass run down, then a bright release.
  "spell.dispelled": synth(0.9, (ctx, out) => {
    arp([91, 86, 83, 79], 0.045, 0, (f, t) => bell(ctx, out, { f, t, peak: 0.12, d: 0.3, ratios: GLASS, tilt: 1.4 }));
    bell(ctx, out, { f: hz(84), t: 0.22, peak: 0.12, d: 0.5, ratios: GLASS, tilt: 1.2, spread: 5 });
  }),

  // A robe changing colour: a bright shimmer up the scale.
  "spell.recoloured": synth(0.9, (ctx, out) => {
    noise(ctx, out, { a: 0.1, d: 0.45, peak: 0.08, f: 2000, f2: 7000, q: 2 });
    sparkle(ctx, out, { spanS: 0.5, count: 14, peak: 0.08, lo: 3000, hi: 8000, seed: seed("recoloured") });
    bell(ctx, out, { f: hz(88), t: 0.05, peak: 0.08, d: 0.5, ratios: GLASS, tilt: 1.3 });
  }),

  // A revealed spot shining: one soft glass ping with a shimmer.
  "spell.reveal_glint": synth(
    0.7,
    (ctx, out, v) => {
      const r = rng(seed("glint", v));
      bell(ctx, out, { f: hz(91 + Math.floor(r() * 5)), peak: 0.12, d: 0.5, ratios: GLASS, tilt: 1.4, spread: 6 });
      sparkle(ctx, out, { t: 0.04, spanS: 0.3, count: 4, peak: 0.04, lo: 5000, hi: 9000, seed: seed("glint-s", v) });
    },
    { variants: 3 },
  ),

  // A small flock's wings.
  "volucra.flutter": synth(
    1.0,
    (ctx, out, v) => {
      const r = rng(seed("flutter", v));
      wings(ctx, out, { spanS: 0.8, rate: between(r, 13, 17), peak: 0.3, f: between(r, 1800, 2600), offset: r() });
      wings(ctx, out, { t: 0.08, spanS: 0.6, rate: between(r, 15, 20), peak: 0.18, f: between(r, 2600, 3400), offset: r() });
    },
    { variants: 2 },
  ),

  // A small bird's chirp.
  "volucra.chirp": synth(0.35, (ctx, out, v) => chirp(ctx, out, { peak: 0.14, seed: seed("chirp", v) }), { variants: 3 }),

  // A guardian bursting out of silver mist.
  "guardian.summon": synth(1.5, (ctx, out) => {
    noise(ctx, out, { a: 0.15, h: 0.1, d: 1.0, peak: 0.14, f: 1800, f2: 7000, q: 1.2 });
    arp([72, 79, 84, 88, 91], 0.07, 0.08, (f, t, i) => bell(ctx, echo(ctx, 0.06, 0.4, 0.35, out), { f, t, peak: 0.12 - i * 0.01, d: 0.7, ratios: GLASS, tilt: 1.2, spread: 6 }));
    sparkle(ctx, out, { t: 0.1, spanS: 1.0, count: 16, peak: 0.07, lo: 4000, hi: 9000, seed: seed("guardian-summon") });
  }),

  // A guardian's soft shimmer while it follows: a high, breathing glass chord and glints (seamless).
  "guardian.ambient": loopDef(6, (sr, n) => {
    const air = biquad(white(n, seed("guardian-ambient")), "bandpass", 5200, 2.5, sr);
    const breath = wobble([1, 2], seed("guardian-breath"));
    const y = mix([shape(air, (p) => 0.4 + 0.6 * breath(p)), 0.5]);
    // Whole cycles per loop: 6 s -> k/6 Hz.
    const chord = [hz(84), hz(88), hz(91), hz(96)].map((f) => Math.round(f * 6) / 6);
    const swell = chord.map((_, i) => wobble([1 + (i % 2)], seed("guardian-swell", i)));
    for (let i = 0; i < n; i++) {
      const p = i / n;
      let s = 0;
      chord.forEach((f, k) => (s += Math.sin((2 * Math.PI * f * i) / sr) * (0.25 + 0.75 * swell[k]!(p))));
      y[i]! += s * 0.06;
    }
    const r = rng(seed("guardian-glints"));
    for (let k = 0; k < 9; k++) addEvent(y, Math.floor(r() * n), Math.floor(0.12 * sr), blip(sr, Math.floor(0.12 * sr), between(r, 4000, 7000), between(r, 4500, 8000), 0.003, 0.5));
    return normalise(y, 0.5);
  }),

  // A guardian dissolving: glitter falling and the mist thinning.
  "guardian.fade": synth(1.3, (ctx, out) => {
    noise(ctx, out, { a: 0.05, d: 1.0, peak: 0.1, f: 6000, f2: 1500, q: 1.3 });
    arp([91, 88, 84, 79], 0.12, 0.05, (f, t, i) => bell(ctx, out, { f, t, peak: 0.09 - i * 0.012, d: 0.5, ratios: GLASS, tilt: 1.4 }));
    sparkle(ctx, out, { t: 0.05, spanS: 0.7, count: 10, peak: 0.05, lo: 3000, hi: 7000, seed: seed("guardian-fade") });
  }),
};

export const POTION_DEFS: SoundDefs = {
  "potion.wear_off": synth(0.9, (ctx, out) => {
    arp([88, 84, 79, 76], 0.09, 0, (f, t, i) => bell(ctx, out, { f, t, peak: 0.12 - i * 0.02, d: 0.4, ratios: GLASS, tilt: 1.5 }));
    noise(ctx, out, { a: 0.02, d: 0.6, peak: 0.06, f: 4000, f2: 1200, q: 1.5 });
  }),

  "potion.glow": synth(1.2, (ctx, out) => {
    tone(ctx, out, { f: hz(64), a: 0.3, d: 0.8, peak: 0.12, vib: [4, 10] });
    tone(ctx, out, { f: hz(71), a: 0.35, d: 0.75, peak: 0.08, vib: [4.5, 10] });
    sparkle(ctx, out, { t: 0.2, spanS: 0.7, count: 8, peak: 0.07, seed: seed("glow") });
  }),

  "potion.chameleon": synth(1.1, (ctx, out) => {
    for (const [f, d] of [
      [hz(72), 0],
      [hz(76), 0.12],
      [hz(79), 0.24],
    ] as const)
      tone(ctx, out, { f, f2: f * 1.26, glideS: 0.7, t: d, a: 0.05, d: 0.7, peak: 0.1, vib: [3 + d * 10, 60], type: "triangle" });
  }),

  "potion.shrinking": synth(0.8, (ctx, out) => {
    tone(ctx, out, { f: 1700, f2: 420, glideS: 0.6, a: 0.01, d: 0.6, peak: 0.18, vib: [12, 45], type: "triangle" });
    sparkle(ctx, out, { spanS: 0.3, count: 4, peak: 0.05, seed: seed("shrink") });
  }),

  "potion.swelling": synth(0.9, (ctx, out) => {
    tone(ctx, out, { f: 75, f2: 210, glideS: 0.75, a: 0.05, d: 0.75, peak: 0.35, vib: [7, 60], type: "sawtooth", lp: 900 });
    tone(ctx, out, { f: 150, f2: 420, glideS: 0.75, a: 0.05, d: 0.7, peak: 0.08 });
  }),

  // Garbled, wordless syllables: a buzzing voice through vowel formants that jump about.
  "potion.babbling": synth(
    1.5,
    (ctx, out, v) => {
      const r = rng(seed("babble", v));
      const VOWELS = [
        [730, 1090],
        [270, 2290],
        [530, 1840],
        [570, 840],
        [300, 870],
        [660, 1720],
      ] as const;
      let t = 0.02;
      while (t < 1.3) {
        const len = between(r, 0.07, 0.16);
        const [f1, f2] = VOWELS[Math.floor(r() * VOWELS.length)]!;
        const pitch = between(r, 150, 230) * (v === 2 ? 1.4 : 1);
        const g = ctx.createGain();
        g.gain.setValueAtTime(0, 0);
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(0.3, t + 0.015);
        g.gain.linearRampToValueAtTime(0, t + len);
        g.connect(out);
        const b1 = filter(ctx, "bandpass", f1, 6, g);
        const b2 = filter(ctx, "bandpass", f2, 8, gainNode(ctx, 0.6, g));
        const osc = ctx.createOscillator();
        osc.type = "sawtooth";
        osc.frequency.setValueAtTime(pitch, t);
        osc.frequency.linearRampToValueAtTime(pitch * between(r, 0.85, 1.2), t + len);
        osc.connect(b1);
        osc.connect(b2);
        osc.start(t);
        osc.stop(t + len + 0.02);
        t += len + between(r, 0.01, 0.06);
      }
    },
    { variants: 3 },
  ),

  "potion.mimic_draught": synth(1.5, (ctx, out) => {
    bubbles(ctx, out, { spanS: 1.2, count: 22, peak: 0.12, lo: 180, hi: 520, seed: seed("mimic_draught") });
    tone(ctx, out, { f: 110, f2: 70, glideS: 1.2, a: 0.2, d: 1.1, peak: 0.18, vib: [5, 90], type: "sawtooth", lp: 500 });
  }),

  "potion.fortunes_draught": synth(1.5, (ctx, out) => {
    arp([84, 88, 91, 96, 100], 0.07, 0, (f, t) => bell(ctx, out, { f, t, peak: 0.1, d: 0.8, ratios: GLASS, tilt: 1.3, spread: 6 }));
    sparkle(ctx, out, { t: 0.1, spanS: 1.0, count: 16, peak: 0.06, lo: 3500, hi: 8000, seed: seed("fortunes_draught") });
    noise(ctx, out, { a: 0.4, d: 0.9, peak: 0.04, f: 6000, q: 1.5 });
  }),

  "potion.pick_me_up_tonic": synth(1.0, (ctx, out) => {
    noise(ctx, out, { a: 0.01, h: 0.15, d: 0.65, peak: 0.35, type: "highpass", f: 2500, q: 0.7 });
    tone(ctx, out, { f: 2200, f2: 2600, glideS: 0.3, a: 0.02, d: 0.3, peak: 0.05 });
    thump(ctx, out, { f: 90, f2: 60, d: 0.12, peak: 0.15 });
  }),

  "potion.invisibility": synth(1.3, (ctx, out) => {
    for (const n of [96, 91, 88])
      tone(ctx, out, { f: hz(n), f2: hz(n - 12), glideS: 1.1, a: 0.05, d: 1.1, peak: 0.06, vib: [6, 15] });
    noise(ctx, out, { a: 0.05, d: 1.1, peak: 0.12, f: 5000, f2: 900, q: 1.2 });
  }),

  "potion.fortune": synth(1.4, (ctx, out) => {
    bell(ctx, out, { f: hz(86), peak: 0.18, d: 1.1, ratios: GLASS, tilt: 1.2, spread: 5 });
    bell(ctx, out, { f: hz(81), t: 0.25, peak: 0.14, d: 1.0, ratios: GLASS, tilt: 1.2, spread: 5 });
    tone(ctx, out, { f: hz(57), a: 0.3, d: 1.0, peak: 0.05, vib: [3, 8] });
  }),
};

export const SECRET_DEFS: SoundDefs = {
  "secret.reveal": synth(2.0, (ctx, out) => {
    grind(ctx, out, { a: 0.2, h: 0.6, r: 0.5, peak: 0.35, f: 180, seed: seed("reveal") });
    thump(ctx, out, { t: 1.2, f: 90, f2: 45, d: 0.4, peak: 0.4 });
    sparkle(ctx, out, { t: 0.4, spanS: 1.2, count: 10, peak: 0.05, seed: seed("reveal-s") });
  }),

  "secret.pace": synth(0.5, (ctx, out) => {
    thump(ctx, out, { f: 110, f2: 70, d: 0.18, peak: 0.35 });
    bell(ctx, out, { f: hz(88), t: 0.02, peak: 0.05, d: 0.35, ratios: GLASS, tilt: 1.6 });
  }),

  "secret.door_appear": synth(2.2, (ctx, out) => {
    noise(ctx, out, { a: 1.0, h: 0.1, d: 0.5, peak: 0.15, f: 400, f2: 3000, q: 2 });
    for (const n of [69, 76, 81]) tone(ctx, out, { f: hz(n), a: 0.9, d: 0.9, peak: 0.05, vib: [5, 12] });
    grind(ctx, out, { t: 0.9, a: 0.1, h: 0.3, r: 0.3, peak: 0.25, f: 220, seed: seed("appear") });
    thump(ctx, out, { t: 1.55, f: 100, f2: 50, d: 0.4, peak: 0.4 });
  }),

  "secret.door_vanish": synth(1.8, (ctx, out) => {
    for (const n of [81, 76, 69]) tone(ctx, out, { f: hz(n), f2: hz(n - 7), glideS: 1.2, a: 0.05, d: 1.3, peak: 0.05, vib: [5, 12] });
    noise(ctx, out, { a: 0.05, d: 1.3, peak: 0.15, f: 3000, f2: 300, q: 1.5 });
    grind(ctx, out, { a: 0.1, h: 0.2, r: 0.4, peak: 0.15, f: 220, seed: seed("vanish") });
  }),
};

export const LEVEL_DEFS: SoundDefs = {
  "level.up": synth(1.5, (ctx, out) => {
    arp([72, 76, 79, 84, 88], 0.07, 0, (f, t, i) => bell(ctx, out, { f, t, peak: 0.12 + i * 0.01, d: 0.9, ratios: HARP, tilt: 1.2 }));
    sparkle(ctx, out, { t: 0.25, spanS: 0.9, count: 14, peak: 0.07, lo: 3000, hi: 8000, seed: seed("level") });
    noise(ctx, out, { a: 0.3, d: 0.8, peak: 0.05, f: 2000, f2: 7000, q: 1.2 });
  }),
};

export { bubbles };
