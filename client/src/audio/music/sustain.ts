// The sustained instruments (winds, bowed strings, horn, choir) rendered in
// plain JS into one looping sample per note: an attack, then a sustain that
// loops without a seam. At runtime a note is a buffer source and a gain (the
// cheapest voice WebAudio has), however rich the sound: a section of four
// detuned strings each with its own vibrato costs no more than a flute.
//
// Each voice reads a single-cycle wavetable built from the instrument's
// harmonic spectrum (a body's or a vowel's formants baked in), with vibrato,
// a detuned unison, breath or bow noise and a brightening attack. The loop is
// exact: its length is a whole number of the note's periods, and every
// unison voice and vibrato is tuned to repeat in it too, so the sustain can
// hold for any written length.

import { midiToFreq } from "./notation";
import type { Instrument } from "./score";
import { seeded, type Sample } from "./samples";

export type SustainedInstrument = "flute" | "whistle" | "clarinet" | "oboe" | "bassoon" | "fiddle" | "strings" | "cello" | "bass" | "horn" | "choir";
export const SUSTAINED: ReadonlySet<string> = new Set<SustainedInstrument>(["flute", "whistle", "clarinet", "oboe", "bassoon", "fiddle", "strings", "cello", "bass", "horn", "choir"]);

interface Spec {
  /** Detuned unison voices (cents), each with its own vibrato rate offset. */
  unison: readonly number[];
  /** Vibrato: rate (Hz), depth (cents), onset (s). */
  vibrato?: readonly [number, number, number];
  /** The attack baked into the sample (s); long notes add a slower swell at runtime. */
  attackS: number;
  /** Fall to this share of the peak over `decayS` after the attack (a plucky bow, a bass). */
  sustain?: number;
  decayS?: number;
  /** The tone opens over the attack: the first instants are this much darker (0..1). */
  darkStart?: number;
  /** Breath or bow noise: level (relative), band centre (multiple of f), Q. */
  noise?: readonly [number, number, number];
  /** A short noisy onset (the flute's chiff, the bow's bite): level and length (s). */
  chiff?: readonly [number, number];
}

const SPECS: Readonly<Record<SustainedInstrument, Spec>> = {
  flute: { unison: [0], vibrato: [5.2, 11, 0.25], attackS: 0.06, noise: [0.05, 2.2, 1.4], chiff: [0.25, 0.04] },
  whistle: { unison: [0], vibrato: [5.8, 7, 0.35], attackS: 0.025, noise: [0.07, 2.6, 1.6], chiff: [0.3, 0.025] },
  clarinet: { unison: [0], vibrato: [4.8, 2, 0.4], attackS: 0.045, noise: [0.015, 3, 1] },
  oboe: { unison: [0], vibrato: [5.5, 9, 0.2], attackS: 0.035, noise: [0.01, 3, 1] },
  bassoon: { unison: [0], vibrato: [5, 6, 0.3], attackS: 0.045, noise: [0.012, 3, 1] },
  fiddle: { unison: [0, 3], vibrato: [5.8, 13, 0.16], attackS: 0.035, darkStart: 0.5, noise: [0.02, 6, 0.7], chiff: [0.12, 0.03] },
  cello: { unison: [0, 3], vibrato: [5, 12, 0.22], attackS: 0.05, darkStart: 0.5, noise: [0.018, 6, 0.7], chiff: [0.1, 0.035] },
  strings: { unison: [-13, -4, 5, 12], vibrato: [5.4, 8, 0.25], attackS: 0.07, darkStart: 0.6, noise: [0.012, 8, 0.6] },
  bass: { unison: [0], attackS: 0.018, sustain: 0.55, decayS: 0.45, darkStart: 0.3 },
  horn: { unison: [-4, 4], vibrato: [4.6, 5, 0.4], attackS: 0.06, darkStart: 0.7 },
  choir: { unison: [-9, 0, 8], vibrato: [4.6, 14, 0.3], attackS: 0.16, noise: [0.01, 4, 0.8] },
};

export function isSustained(i: Instrument): i is SustainedInstrument {
  return SUSTAINED.has(i);
}

/** The extra swell a long note gets on top of the baked attack (s): shortest, longest, share of the note. */
export const SWELL: Readonly<Partial<Record<SustainedInstrument, readonly [number, number, number]>>> = {
  strings: [0, 0.4, 0.35],
  choir: [0, 0.35, 0.35],
  horn: [0, 0.12, 0.3],
  cello: [0, 0.12, 0.25],
  flute: [0, 0.06, 0.2],
};

/** Release time (s) after the written end. */
export const RELEASE: Readonly<Record<SustainedInstrument, number>> = {
  flute: 0.2, whistle: 0.12, clarinet: 0.16, oboe: 0.15, bassoon: 0.16, fiddle: 0.16, cello: 0.28, strings: 0.4, bass: 0.22, horn: 0.3, choir: 0.45,
};

// ---------------------------------------------------------------- spectra

interface Formant { hz: number; bw: number; gain: number }

function formants(fs: readonly Formant[], hz: number, floor: number): number {
  let g = floor;
  for (const f of fs) g += f.gain / (1 + ((hz - f.hz) / f.bw) ** 2);
  return g;
}

/** Harmonic amplitudes (index = harmonic number) of `inst` at fundamental `f`, up to `nyq`. */
export function spectrum(inst: SustainedInstrument, f: number, nyq: number): number[] {
  const n = Math.max(1, Math.min(48, Math.floor((nyq * 0.92) / f)));
  const out: number[] = [0];
  const roll = (hz: number, knee: number, p = 4) => 1 / (1 + (hz / knee) ** p);
  for (let h = 1; h <= n; h++) {
    const hz = h * f;
    let a: number;
    switch (inst) {
      case "flute": a = ([1, 0.24, 0.08, 0.035, 0.015][h - 1] ?? 0) * roll(hz, 5000); break;
      case "whistle": a = ([1, 0.3, 0.12, 0.05, 0.02, 0.01][h - 1] ?? 0) * roll(hz, 7000); break;
      case "clarinet": a = (h % 2 ? 1 / h ** 0.9 : 0.06 / h) * roll(hz, 2300); break;
      case "oboe": a = (1 / h ** 0.5) * formants([{ hz: 1150, bw: 450, gain: 1 }, { hz: 2900, bw: 500, gain: 0.25 }], hz, 0.06) * roll(hz, 4200); break;
      case "bassoon": a = (1 / h ** 0.6) * formants([{ hz: 470, bw: 170, gain: 1 }, { hz: 1150, bw: 260, gain: 0.35 }], hz, 0.05) * roll(hz, 2600); break;
      case "horn": a = (1 / h ** 1.1) * formants([{ hz: 420, bw: 300, gain: 1 }], hz, 0.25) * roll(hz, 1600, 3); break;
      case "fiddle": a = (1 / h) * formants([{ hz: 290, bw: 110, gain: 0.7 }, { hz: 520, bw: 200, gain: 1 }, { hz: 2700, bw: 900, gain: 0.7 }], hz, 0.2) * roll(hz, 5000); break;
      case "cello": a = (1 / h) * formants([{ hz: 210, bw: 90, gain: 0.8 }, { hz: 430, bw: 200, gain: 1 }, { hz: 1500, bw: 700, gain: 0.35 }], hz, 0.15) * roll(hz, 3200); break;
      case "strings": a = (1 / h) * formants([{ hz: 450, bw: 300, gain: 0.5 }, { hz: 2500, bw: 1200, gain: 0.3 }], hz, 0.7) * roll(hz, 3600, 3); break;
      case "bass": a = ([1, 0.5, 0.24, 0.12, 0.06, 0.03][h - 1] ?? 0) * roll(hz, 900); break;
      case "choir": a = (1 / h ** 1.2) * formants([{ hz: 520, bw: 90, gain: 1 }, { hz: 900, bw: 130, gain: 0.55 }, { hz: 2500, bw: 220, gain: 0.12 }], hz, 0.015) * roll(hz, 3500); break;
    }
    out.push(a);
  }
  return out;
}

const TABLE = 2048;

/** One cycle of the spectrum, peak-normalized. */
function table(amps: readonly number[]): Float32Array {
  const t = new Float32Array(TABLE + 1);
  for (let h = 1; h < amps.length; h++) {
    const a = amps[h]!;
    if (a === 0) continue;
    for (let i = 0; i < TABLE; i++) t[i]! += a * Math.sin((2 * Math.PI * h * i) / TABLE);
  }
  let m = 0;
  for (let i = 0; i < TABLE; i++) m = Math.max(m, Math.abs(t[i]!));
  if (m > 0) for (let i = 0; i < TABLE; i++) t[i]! /= m;
  t[TABLE] = t[0]!;
  return t;
}

/** RBJ bandpass as a stateful function (breath and bow noise). */
function bandpass(fc: number, q: number, sr: number): (x: number) => number {
  const w = (2 * Math.PI * Math.min(fc, sr * 0.45)) / sr;
  const alpha = Math.sin(w) / (2 * q);
  const a0 = 1 + alpha;
  const b0 = alpha / a0, b2 = -alpha / a0, a1 = (-2 * Math.cos(w)) / a0, a2 = (1 - alpha) / a0;
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  return (x) => {
    const y = b0 * x + b2 * x2 - a1 * y1 - a2 * y2;
    x2 = x1; x1 = x; y2 = y1; y1 = y;
    return y;
  };
}

/** One note of a sustained instrument: an attack, then a seamless loop from `loopStart` to `loopEnd` (s). */
export function renderSustain(inst: SustainedInstrument, midi: number, sr: number): Sample {
  const spec = SPECS[inst];
  const f = midiToFreq(midi);
  // The loop: a whole number of periods, close to a second.
  const periods = Math.max(1, Math.round(1.0 * f));
  const loopS = periods / f;
  const vib = spec.vibrato;
  const settle = Math.max(spec.attackS * 4, vib ? vib[2] + 0.45 : 0, (spec.decayS ?? 0) * 2.5, 0.25);
  const loopStart = Math.ceil(settle * sr) / sr;
  const total = loopStart + loopS;
  const len = Math.ceil(total * sr) + 2;
  const d = new Float32Array(len);

  const nyq = sr / 2;
  const bright = table(spectrum(inst, f, nyq));
  const dark = spec.darkStart ? table(spectrum(inst, f, nyq).map((a, h) => a / (1 + ((h * f) / (f * 2.5)) ** 2))) : bright;

  // Every unison voice and vibrato repeats a whole number of times in the loop.
  const q = (hz: number) => Math.max(1, Math.round(hz * loopS)) / loopS;
  // A detuned voice whose nearest repeating pitch is far from its detune (low
  // notes, where one cycle more per loop is many cents) is left out.
  const voices = spec.unison
    .map((cents, k) => {
      let fk = q(f * Math.pow(2, cents / 1200));
      if (cents !== 0 && Math.abs(fk - f) < 1e-9) fk = f + (cents > 0 ? 1 : -1) / loopS;
      const off = Math.abs(1200 * Math.log2(fk / f));
      const vr = vib ? q(vib[0] * (1 + 0.07 * (k - (spec.unison.length - 1) / 2))) : 0;
      return { f: fk, vr, phase: (k * 0.37) % 1, vphase: (k * 0.29) % 1, keep: cents === 0 || off <= 2.5 * Math.abs(cents) + 4 };
    })
    .filter((v, _, all) => v.keep || all.every((x) => !x.keep));
  const depth = vib ? (vib[1] * Math.LN2) / 1200 : 0;
  const gainPer = 1 / Math.sqrt(voices.length);

  for (let i = 0; i < len; i++) {
    const t = i / sr;
    const vd = vib ? Math.min(1, Math.max(0, (t - vib[2]) / 0.4)) * depth : 0;
    const mix = spec.darkStart ? Math.min(1, t / (spec.attackS * 3)) : 1;
    let s = 0;
    for (const v of voices) {
      const fi = v.f * (1 + vd * Math.sin(2 * Math.PI * v.vphase));
      v.vphase += v.vr / sr;
      if (v.vphase >= 1) v.vphase -= 1;
      const x = v.phase * TABLE;
      const j = x | 0;
      const fr = x - j;
      const b = bright[j]! + (bright[j + 1]! - bright[j]!) * fr;
      const dk = dark === bright ? b : dark[j]! + (dark[j + 1]! - dark[j]!) * fr;
      s += (dk + (b - dk) * mix) * gainPer;
      v.phase += fi / sr;
      if (v.phase >= 1) v.phase -= 1;
    }
    d[i] = s;
  }

  // breath or bow noise, and the onset's chiff
  if (spec.noise || spec.chiff) {
    const rnd = seeded(midi * 13 + inst.length * 101);
    const [nl, nc, nq] = spec.noise ?? [0, 2, 1];
    const bp = bandpass(f * nc, nq, sr);
    const bp2 = bandpass(Math.min(f * 4, 6000), 0.8, sr);
    const [cl, cs] = spec.chiff ?? [0, 0.01];
    for (let i = 0; i < len; i++) {
      const t = i / sr;
      const w = rnd() * 2 - 1;
      d[i]! += bp(w) * nl * 4;
      if (cl > 0 && t < cs * 4) d[i]! += bp2(w) * cl * Math.exp(-t / cs);
    }
    // the noise does not repeat: blend the loop's end into what precedes its start
    const x = Math.floor(sr * 0.05);
    const s0 = Math.round(loopStart * sr);
    const e0 = s0 + Math.round(loopS * sr);
    for (let i = 0; i < x; i++) {
      const w = i / x;
      const a = e0 - x + i;
      const b = s0 - x + i;
      if (b >= 0 && a < len) d[a] = d[a]! * (1 - w) + d[b]! * w;
    }
  }

  // the baked envelope: attack, then the decay to the sustain level
  const att = Math.max(1, Math.floor(spec.attackS * sr));
  const sus = spec.sustain ?? 1;
  const dec = spec.decayS ?? 0.1;
  for (let i = 0; i < len; i++) {
    const t = i / sr;
    let g = i < att ? Math.sin(((i / att) * Math.PI) / 2) : 1;
    if (sus < 1) g *= sus + (1 - sus) * Math.exp(-Math.max(0, t - spec.attackS) / (dec / 3));
    d[i]! *= g;
  }

  let m = 0;
  for (let i = 0; i < len; i++) m = Math.max(m, Math.abs(d[i]!));
  if (m > 0) for (let i = 0; i < len; i++) d[i]! *= 0.7 / m;
  return { data: d, sampleRate: sr, rate: 1, loopStart, loopEnd: loopStart + Math.round(loopS * sr) / sr };
}
