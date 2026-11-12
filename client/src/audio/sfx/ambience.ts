// Ambience (docs/architecture.md "Sound and music"): the
// synthesized indoor tones and outdoor winds (seamless loops, dsp.ts), the synthesized
// sprinkles, the two synthesized emitters, and the beds that combine them
// with the CC0 recordings (files.ts).
//
// A bed's layers are always plain loops (a file or a synth), never another
// bed, so the engine plays a bed as a flat list. The recorded outdoor loops
// double as layers: amb.courtyard_day (birds) and amb.courtyard_night
// (crickets) carry the grounds, loop.lake (lapping water) carries the
// lake by day and night, loop.hearth (a fire) the kitchens and common rooms.

import type { SoundDefs, Sprinkle } from "../types";
import { between, filter, noise, rng, seed, synth } from "./kit";
import { addEvent, biquad, blip, loopDef, mix, onePole, shape, white, wobble, type Signal } from "./dsp";
import { hoot } from "./world";

/** Scale a signal to an RMS level (linear). Loops sit at RMS_LOOP; their definitions' gains set the mix. */
function rmsTo(x: Signal, rms: number): Signal {
  let s = 0;
  for (let i = 0; i < x.length; i++) s += x[i]! * x[i]!;
  const k = rms / Math.sqrt(s / Math.max(1, x.length) || 1e-12);
  for (let i = 0; i < x.length; i++) x[i] = x[i]! * k;
  return x;
}

/** -24 dBFS: every synthesized loop is made at this level. */
const RMS_LOOP = 0.063;

/** Brown-ish noise: white noise under a gentle low-pass (the Owl Loft's straw under its owls). */
function roomTone(n: number, sr: number, s: number, corner: number): Signal {
  return onePole(white(n, s), corner, sr);
}

// Indoors there is no continuous noise at all. A room tone made of noise, however steady
// (white under a low-pass, a band of "air"), reads as surf or wind through the walls: the
// castle's halls, the Great Hall, classrooms, passages and dorms are beds of sprinkles alone
// (BED_DEFS), quiet under their music. Only tones (the arena's drone, the Wishing
// Room's shimmer), deliberate local fires (loop.hearth) and, in the rooms under the
// lake, the water heard through their windows (amb.under_lake: so deep under a steep
// low-pass that nothing of the hiss that reads as surf or wind is left) loop indoors.

export const AMBIENCE_SYNTH_DEFS: SoundDefs = {
  // The dueling arena: tense, a low steady drone (tones only: no noise, no swell).
  "amb.arena": loopDef(
    10,
    (sr, n, c) => {
      const drone = new Float32Array(n);
      // Whole cycles per loop: 55 and 82.6 Hz over 10 s.
      for (let i = 0; i < n; i++) {
        const t = i / sr;
        drone[i] = Math.sin(2 * Math.PI * 55 * t) + 0.6 * Math.sin(2 * Math.PI * 82.6 * t + c);
      }
      return rmsTo(drone, RMS_LOOP);
    },
    { channels: 2 },
  ),

  // The Wishing Room: a faint shimmer of high, slowly breathing tones (tones only: no noise).
  "amb.wishing_room": loopDef(
    10,
    (sr, n, c) => {
      const shimmer = new Float32Array(n);
      // Whole cycles per 10 s loop (0.1 Hz steps); each partial breathes at its own whole-cycle rate.
      const partials = [
        [1318.5, 1],
        [1760.0, 2],
        [1975.5, 3],
        [2637.0, 1],
        [2217.5, 2],
      ] as const;
      for (let i = 0; i < n; i++) {
        const t = i / sr;
        const p = i / n;
        let s = 0;
        partials.forEach(([f, cyc], k) => {
          const amp = 0.5 + 0.5 * Math.sin(2 * Math.PI * cyc * p + k * 1.7 + c * 0.9);
          s += Math.sin(2 * Math.PI * f * t + k + c) * amp * amp;
        });
        shimmer[i] = s;
      }
      return rmsTo(shimmer, RMS_LOOP);
    },
    { channels: 2 },
  ),

  // The rooms under the lake (common room D, its dorm): the water outside heard through thick
  // glass. A slow, deep wash (noise under a steep low-pass, swelling once or twice a loop) and a
  // few soft bubbles rising, the whole muffled again so nothing above a few hundred hertz is left:
  // calm, never a drip or an echo.
  "amb.under_lake": loopDef(
    16,
    (sr, n, c) => {
      const swell = wobble([1, 2], seed("under-lake-swell"));
      let wash = white(n, seed("under-lake", c));
      for (let k = 0; k < 3; k++) wash = biquad(wash, "lowpass", 240, 0.7, sr);
      const y = mix([shape(rmsTo(wash, 1), (p) => 0.35 + 0.65 * swell(p)), 1]);
      const r = rng(seed("under-lake-bubbles", c));
      for (let k = 0; k < 9; k++) {
        const len = Math.floor(between(r, 0.08, 0.16) * sr);
        const f0 = between(r, 180, 320);
        const b = blip(sr, len, f0, f0 * between(r, 1.4, 1.9), 0.02, 0.7);
        const g = between(r, 0.15, 0.35);
        addEvent(y, Math.floor(r() * n), len, (i) => b(i) * g);
      }
      let out = y;
      for (let k = 0; k < 3; k++) out = biquad(out, "lowpass", 520, 0.7, sr);
      return rmsTo(out, RMS_LOOP);
    },
    { channels: 2 },
  ),

  // High in the open sky: a steady, full wind.
  "amb.sky": loopDef(
    8,
    (sr, n, c) => {
      const gust = wobble([1, 2, 3], seed("sky-gust", c));
      const body = rmsTo(biquad(white(n, seed("sky", c)), "bandpass", (p) => 700 + 500 * gust(p), 0.6, sr), 1);
      const low = rmsTo(biquad(white(n, seed("sky-lo", c)), "lowpass", 200, 0.7, sr), 1);
      const hiss = rmsTo(biquad(white(n, seed("sky-hi", c)), "highpass", 3000, 0.7, sr), 1);
      return rmsTo(shape(mix([body, 1], [low, 0.7], [hiss, 0.12]), (p) => 0.6 + 0.4 * gust(p)), RMS_LOOP);
    },
    { channels: 2 },
  ),

  // The open towers (the Owl Loft, the Astronomy platform): wind through the arches, a soft whistle wandering over a gusty body.
  "amb.tower": loopDef(
    12,
    (sr, n, c) => {
      const gust = wobble([1, 2, 3], seed("tower-gust"));
      const wander = wobble([1, 2], seed("tower-whistle", c));
      const body = rmsTo(biquad(white(n, seed("tower", c)), "bandpass", (p) => 380 + 250 * gust(p), 0.8, sr), 1);
      const whistle = rmsTo(biquad(white(n, seed("tower-w", c)), "bandpass", (p) => 720 + 260 * wander(p), 14, sr), 1);
      return rmsTo(
        mix([shape(body, (p) => 0.25 + 0.75 * gust(p)), 1], [shape(whistle, (p) => Math.pow(gust(p), 2)), 0.35]),
        RMS_LOOP,
      );
    },
    { channels: 2 },
  ),
};

export const EMITTER_SYNTH_DEFS: SoundDefs = {
  // A cauldron simmering: a low seethe and bubbles popping.
  "loop.cauldron": loopDef(8, (sr, n) => {
    const seethe = rmsTo(biquad(white(n, seed("cauldron")), "lowpass", 700, 0.7, sr), 1);
    const y = mix([seethe, 0.25]);
    const r = rng(seed("cauldron-bubbles"));
    for (let k = 0; k < 48; k++) {
      const len = Math.floor(between(r, 0.03, 0.08) * sr);
      const f0 = between(r, 160, 420);
      addEvent(y, Math.floor(r() * n), len, (() => {
        const b = blip(sr, len, f0, f0 * between(r, 1.8, 2.8), 0.004, 0.6);
        const g = between(r, 0.4, 1);
        return (i: number) => b(i) * g;
      })());
    }
    return rmsTo(y, RMS_LOOP * 1.6);
  }),

  // The Owl Loft: feathers rustling and soft, sleepy coos.
  "loop.owls": loopDef(12, (sr, n) => {
    const y = rmsTo(roomTone(n, sr, seed("owls-room"), 300), 0.15);
    const r = rng(seed("owls"));
    const rustle = biquad(white(Math.floor(0.4 * sr), seed("feathers")), "bandpass", 3200, 0.9, sr);
    for (let k = 0; k < 9; k++) {
      const len = Math.floor(between(r, 0.12, 0.35) * sr);
      const o = Math.floor(r() * (rustle.length - len));
      const g = between(r, 0.3, 0.9);
      addEvent(y, Math.floor(r() * n), len, (i) => rustle[o + i]! * g * Math.sin((Math.PI * i) / len));
    }
    for (const at of [1.3, 5.8, 8.9]) {
      const f = between(r, 330, 420);
      const len = Math.floor(between(r, 0.35, 0.55) * sr);
      const coo = blip(sr, len, f * 0.95, f * 0.88, 0.08, 1);
      addEvent(y, Math.floor(at * sr), len, (i) => coo(i) * 0.6);
    }
    return rmsTo(y, RMS_LOOP);
  }),
};

export const SPRINKLE_SYNTH_DEFS: SoundDefs = {
  // A far owl at night: a hoot heard across the grounds (dull, distant).
  "sprinkle.owl_far": synth(
    1.8,
    (ctx, out, v) => {
      const far = filter(ctx, "lowpass", 900, 0.7, out);
      const f = v === 0 ? 370 : 345;
      hoot(ctx, far, { t: 0, f, len: 0.5, peak: 0.4, seed: seed("owl-far-a", v) });
      hoot(ctx, far, { t: v === 0 ? 0.85 : 0.7, f: f * 0.98, len: 0.6, peak: 0.32, seed: seed("owl-far-b", v) });
    },
    { variants: 2 },
  ),

  // A water drip: a plink with a tiny ring.
  "sprinkle.drip": synth(
    0.4,
    (ctx, out, v) => {
      const f = [900, 1100, 760, 1300][v]!;
      const r = rng(seed("drip", v));
      const node = ctx.createOscillator();
      const g = ctx.createGain();
      g.gain.setValueAtTime(0, 0);
      g.gain.linearRampToValueAtTime(0.5, 0.002);
      g.gain.exponentialRampToValueAtTime(0.0001, 0.12);
      g.gain.setValueAtTime(0, 0.121);
      node.frequency.setValueAtTime(f, 0);
      node.frequency.exponentialRampToValueAtTime(f * between(r, 2.0, 2.6), 0.03);
      node.connect(g);
      g.connect(out);
      node.start(0);
      node.stop(0.15);
      noise(ctx, out, { t: 0.0, a: 0.001, d: 0.01, peak: 0.1, f: 4000, q: 1, offset: r() });
    },
    { variants: 4 },
  ),

  // A gust round a tower.
  "sprinkle.gust": synth(
    2.6,
    (ctx, out, v) => {
      const r = rng(seed("gust", v));
      const f = between(r, 500, 900);
      noise(ctx, out, { a: 0.9, h: 0.2, d: 1.3, peak: 0.4, f, f2: f * between(r, 1.3, 1.8), sweepS: 1.1, q: 0.9, offset: r() });
      noise(ctx, out, { t: 0.3, a: 0.6, h: 0.1, d: 1.0, peak: 0.15, f: f * 2.2, q: 6, offset: r() });
    },
    { variants: 2 },
  ),

  // A frog: a buzzy pulsed croak through two formants, two or three in a row.
  "sprinkle.frog": synth(
    1.0,
    (ctx, out, v) => {
      const r = rng(seed("frog", v));
      const calls = v === 2 ? 3 : 2;
      for (let k = 0; k < calls; k++) {
        const t = k * between(r, 0.22, 0.3);
        const len = between(r, 0.12, 0.2);
        const g = ctx.createGain();
        g.gain.setValueAtTime(0, 0);
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(0.5, t + 0.02);
        g.gain.linearRampToValueAtTime(0, t + len);
        g.connect(out);
        const f1 = filter(ctx, "bandpass", between(r, 380, 520), 5, g);
        const f2 = filter(ctx, "bandpass", between(r, 1100, 1500), 7, g);
        const pulse = ctx.createOscillator();
        pulse.type = "square";
        const rate = between(r, 26, 38);
        pulse.frequency.setValueAtTime(rate, t);
        pulse.frequency.linearRampToValueAtTime(rate * 0.85, t + len);
        pulse.connect(f1);
        pulse.connect(f2);
        pulse.start(t);
        pulse.stop(t + len + 0.02);
      }
    },
    { variants: 3 },
  ),
};

// ---------------------------------------------------------------- beds

const sp = (id: Sprinkle["id"], everyS: readonly [number, number], gain: number, distanceM: readonly [number, number]): Sprinkle => ({ id, everyS, gain, distanceM });

export const BED_DEFS: SoundDefs = {
  // Indoors: sprinkles over silence (no noise bed: see AMBIENCE_SYNTH_DEFS). The halls, the Great Hall
  // and the classrooms are quiet under their music.
  "amb.castle": { kind: "bed", layers: [], sprinkles: [] },
  "amb.great_hall": { kind: "bed", layers: [], sprinkles: [] },
  "amb.classroom": { kind: "bed", layers: [], sprinkles: [] },
  "amb.passage": { kind: "bed", layers: [], sprinkles: [sp("sprinkle.drip", [8, 20], 0.2, [2, 6])] },
  // A dormitory: a soft fire's rare pops.
  "amb.dorm": { kind: "bed", layers: [], sprinkles: [sp("sprinkle.fire_pop", [8, 20], 0.15, [3, 7])] },
  "amb.library": { kind: "bed", layers: [], sprinkles: [sp("sprinkle.page", [7, 16], 0.25, [4, 12]), sp("sprinkle.creak", [18, 40], 0.2, [5, 14])] },
  "amb.dungeon": { kind: "bed", layers: [], sprinkles: [sp("sprinkle.drip", [2.5, 7], 0.35, [3, 12])] },
  "amb.owl_loft": { kind: "bed", layers: [{ id: "amb.tower", gain: 0.85 }], sprinkles: [sp("sprinkle.gust", [9, 20], 0.35, [4, 10]), sp("sprinkle.creak", [20, 45], 0.15, [3, 8])] },
  "amb.kitchens": { kind: "bed", layers: [{ id: "loop.hearth", gain: 0.7 }], sprinkles: [sp("sprinkle.fire_pop", [3, 8], 0.35, [4, 10]), sp("sprinkle.creak", [25, 50], 0.15, [4, 10])] },
  // The tower common room has no fireplace (castle_kit common_room_house_c): no hearth, no pops,
  // and no wind (indoors: the windows are glazed).
  "amb.common_room_tower": { kind: "bed", layers: [], sprinkles: [sp("sprinkle.page", [9, 20], 0.2, [3, 9]), sp("sprinkle.creak", [25, 55], 0.15, [3, 9])] },
  // Under the lake (common room D and its dorm): a cosy bed, never the dungeons' drips. The fire
  // crackling and popping, the lake muffled through the windows, now and then a page turned.
  "amb.common_room_lake": { kind: "bed", layers: [{ id: "loop.hearth", gain: 0.4 }, { id: "amb.under_lake", gain: 0.8 }], sprinkles: [sp("sprinkle.fire_pop", [4, 10], 0.3, [3, 9]), sp("sprinkle.page", [10, 24], 0.2, [3, 9])] },
  "amb.dorm_lake": { kind: "bed", layers: [{ id: "loop.hearth", gain: 0.2 }, { id: "amb.under_lake", gain: 0.6 }], sprinkles: [sp("sprinkle.fire_pop", [6, 14], 0.2, [2, 5]), sp("sprinkle.page", [14, 30], 0.15, [2, 5])] },
  "amb.common_room": { kind: "bed", layers: [{ id: "loop.hearth", gain: 0.3 }], sprinkles: [sp("sprinkle.fire_pop", [5, 12], 0.25, [3, 9]), sp("sprinkle.creak", [25, 55], 0.15, [3, 9])] },
  "amb.grounds_day": { kind: "bed", layers: [{ id: "amb.courtyard_day", gain: 0.9 }, { id: "amb.sky", gain: 0.12 }], sprinkles: [sp("sprinkle.bird", [3, 9], 0.4, [8, 25]), sp("sprinkle.crow", [20, 45], 0.3, [20, 35]), sp("sprinkle.gust", [15, 35], 0.2, [5, 15])] },
  "amb.grounds_night": { kind: "bed", layers: [{ id: "amb.courtyard_night", gain: 1 }, { id: "amb.sky", gain: 0.1 }], sprinkles: [sp("sprinkle.owl_far", [18, 40], 0.35, [20, 35]), sp("sprinkle.gust", [15, 35], 0.18, [5, 15])] },
  "amb.lake_day": { kind: "bed", layers: [{ id: "loop.lake", gain: 1 }, { id: "amb.courtyard_day", gain: 0.45 }], sprinkles: [sp("sprinkle.bird", [5, 12], 0.35, [10, 30]), sp("sprinkle.crow", [25, 50], 0.25, [20, 35])] },
  "amb.lake_night": { kind: "bed", layers: [{ id: "loop.lake", gain: 1 }, { id: "amb.courtyard_night", gain: 0.55 }], sprinkles: [sp("sprinkle.frog", [6, 16], 0.3, [6, 18]), sp("sprinkle.owl_far", [25, 55], 0.25, [20, 35])] },
  "amb.greenhouse": { kind: "bed", layers: [{ id: "amb.courtyard_day", gain: 0.25 }], sprinkles: [sp("sprinkle.bird", [8, 18], 0.15, [10, 20])] },
};
