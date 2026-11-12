// The plucked and struck instruments, synthesized in plain JS into sample
// arrays once per note (then played as buffers: two nodes a note, the
// cheapest voice WebAudio has; the sustained ones are in sustain.ts). Pure
// and DOM-free: the tests check their pitch and level under Node.
//
//   harp, pizz          Karplus-Strong strings (a noise burst in a damped delay loop)
//   celesta, musicbox,  additive: a few sine partials with their own decays and
//   glock, timpani      ratios (struck bars, tines, a kettle's membrane)
//   frame, shaker       noise and a falling sine (the drum's skin, the beads)

import { midiToFreq } from "./notation";

export type SampledInstrument = "harp" | "pizz" | "celesta" | "musicbox" | "glock" | "timpani" | "frame" | "shaker";
export const SAMPLED: ReadonlySet<string> = new Set<SampledInstrument>(["harp", "pizz", "celesta", "musicbox", "glock", "timpani", "frame", "shaker"]);

export interface Sample {
  data: Float32Array;
  sampleRate: number;
  /** The playback rate that puts it exactly on pitch (Karplus-Strong's loop is a whole number of samples). */
  rate: number;
  /** A sustained note's seamless loop (s); absent for a note that rings out. */
  loopStart?: number;
  loopEnd?: number;
}

/** A seeded generator (the same note renders the same samples, so renders compare). */
export function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** One-pole lowpass coefficient for a cutoff (Hz). */
function onePole(fc: number, sr: number): number {
  return 1 - Math.exp((-2 * Math.PI * fc) / sr);
}

/** A short fade at the very end, so a buffer never stops on a step. */
function tailFade(d: Float32Array, sr: number, s = 0.04): void {
  const n = Math.min(d.length, Math.floor(sr * s));
  for (let i = 0; i < n; i++) d[d.length - 1 - i]! *= i / n;
}

// ---------------------------------------------------------------- strings

interface KsOpts {
  /** Seconds for the fundamental to fall 60 dB. */
  t60: number;
  seconds: number;
  /** Excitation brightness (one-pole lowpass of the noise, Hz). */
  pickHz: number;
  /** Where along the string it is plucked (fraction): the comb that shapes the tone. */
  position: number;
  /** A gentle lowpass on the output (Hz, as a multiple of f). */
  toneMul: number;
  seed: number;
}

function karplus(sr: number, f: number, o: KsOpts): Sample {
  // The loop averages each sample with the one after it: the period is n - 0.5 samples.
  const n = Math.max(3, Math.floor(sr / f + 0.5));
  const actual = sr / (n - 0.5);
  const len = Math.floor(sr * o.seconds);
  const d = new Float32Array(len);
  const ring = new Float32Array(n);
  const rnd = seeded(o.seed);
  const a = onePole(o.pickHz, sr);
  let lp = 0;
  for (let i = 0; i < n; i++) {
    lp += a * (rnd() * 2 - 1 - lp);
    ring[i] = lp;
  }
  const shift = Math.max(1, Math.round(n * o.position));
  const src = ring.slice();
  for (let i = 0; i < n; i++) ring[i] = src[i]! - 0.85 * src[(i + shift) % n]!;
  // Remove the burst's DC so the string settles on zero.
  let mean = 0;
  for (let i = 0; i < n; i++) mean += ring[i]!;
  mean /= n;
  for (let i = 0; i < n; i++) ring[i]! -= mean;
  const damp = Math.pow(10, -3 / (actual * o.t60));
  const b = onePole(Math.min(f * o.toneMul, sr * 0.45), sr);
  let p = 0;
  let out = 0;
  for (let i = 0; i < len; i++) {
    const cur = ring[p]!;
    const nxt = ring[(p + 1) % n]!;
    ring[p] = (cur + nxt) * 0.5 * damp;
    out += b * (cur - out);
    d[i] = out;
    p = (p + 1) % n;
  }
  normalize(d, 0.7);
  tailFade(d, sr);
  return { data: d, sampleRate: sr, rate: f / actual };
}

// ---------------------------------------------------------------- bars, tines, membranes

/** [ratio to f, amplitude, decay as a multiple of the note's decay] */
type Partial3 = readonly [number, number, number];

interface StruckOpts {
  partials: readonly Partial3[];
  /** The fundamental's decay time constant (s). */
  tau: number;
  seconds: number;
  attackS: number;
  /** The mallet's click: level, its decay (s), its lowpass (Hz). */
  click: readonly [number, number, number];
  /** A pitch that starts a little sharp and settles (membranes): depth and time (s). */
  glide?: readonly [number, number];
  seed: number;
}

function struck(sr: number, f: number, o: StruckOpts): Sample {
  const len = Math.floor(sr * o.seconds);
  const d = new Float32Array(len);
  const nyq = sr * 0.45;
  for (const [ratio, amp, decay] of o.partials) {
    const pf = f * ratio;
    if (pf >= nyq) continue;
    const k = Math.exp(-1 / (sr * o.tau * decay));
    let env = amp;
    let ph = 0;
    for (let i = 0; i < len && env > 1e-5; i++) {
      const g = o.glide ? 1 + o.glide[0] * Math.exp(-i / (sr * o.glide[1])) : 1;
      ph += (2 * Math.PI * pf * g) / sr;
      d[i]! += env * Math.sin(ph);
      env *= k;
    }
  }
  const [cl, cdec, chz] = o.click;
  if (cl > 0) {
    const rnd = seeded(o.seed);
    const a = onePole(chz, sr);
    let lp = 0;
    const n = Math.min(len, Math.floor(sr * cdec * 6));
    for (let i = 0; i < n; i++) {
      lp += a * (rnd() * 2 - 1 - lp);
      d[i]! += cl * lp * Math.exp(-i / (sr * cdec));
    }
  }
  const att = Math.max(1, Math.floor(sr * o.attackS));
  for (let i = 0; i < att && i < len; i++) d[i]! *= i / att;
  normalize(d, 0.7);
  tailFade(d, sr, 0.08);
  return { data: d, sampleRate: sr, rate: 1 };
}

function normalize(d: Float32Array, peak: number): void {
  let m = 0;
  for (let i = 0; i < d.length; i++) m = Math.max(m, Math.abs(d[i]!));
  if (m > 0) for (let i = 0; i < d.length; i++) d[i]! *= peak / m;
}

// ---------------------------------------------------------------- drums

function frameDrum(sr: number, midi: number): Sample {
  // Low notes are the open centre stroke ("dum"), high ones the edge ("tak").
  const edge = midi >= 52;
  const f = midiToFreq(edge ? midi - 12 : midi);
  const len = Math.floor(sr * (edge ? 0.25 : 0.6));
  const d = new Float32Array(len);
  const rnd = seeded(midi * 31 + 7);
  let ph = 0;
  let ph2 = 0;
  const bp = biquadBandpass(edge ? 1900 : 700, edge ? 1.4 : 0.9, sr);
  for (let i = 0; i < len; i++) {
    const t = i / sr;
    const fr = f * (1 + (edge ? 0.3 : 0.6) * Math.exp(-t / 0.025));
    ph += (2 * Math.PI * fr) / sr;
    ph2 += (2 * Math.PI * fr * 1.59) / sr;
    const body = Math.sin(ph) * Math.exp(-t / (edge ? 0.05 : 0.17)) * (edge ? 0.35 : 1);
    const over = Math.sin(ph2) * Math.exp(-t / 0.06) * 0.25;
    const skin = bp(rnd() * 2 - 1) * Math.exp(-t / (edge ? 0.03 : 0.018)) * (edge ? 1.6 : 0.8);
    d[i] = body + over + skin;
  }
  const att = Math.floor(sr * 0.001);
  for (let i = 0; i < att; i++) d[i]! *= i / att;
  normalize(d, 0.7);
  tailFade(d, sr, 0.03);
  return { data: d, sampleRate: sr, rate: 1 };
}

function shaker(sr: number, midi: number): Sample {
  const len = Math.floor(sr * 0.16);
  const d = new Float32Array(len);
  const rnd = seeded(midi * 17 + 3);
  const hp1 = onePole(4200, sr);
  let lp1 = 0;
  let lp2 = 0;
  for (let i = 0; i < len; i++) {
    const t = i / sr;
    // beads: a swell then a fall, roughened by a slow random grain
    const env = (1 - Math.exp(-t / 0.012)) * Math.exp(-t / 0.045) * (0.6 + 0.4 * rnd());
    const x = rnd() * 2 - 1;
    lp1 += hp1 * (x - lp1);
    const h = x - lp1;
    lp2 += hp1 * (h - lp2);
    d[i] = (h - lp2 * 0.5) * env;
  }
  normalize(d, 0.6);
  tailFade(d, sr, 0.02);
  return { data: d, sampleRate: sr, rate: 1 };
}

/** RBJ bandpass (constant peak gain) as a stateful function. */
function biquadBandpass(fc: number, q: number, sr: number): (x: number) => number {
  const w = (2 * Math.PI * fc) / sr;
  const alpha = Math.sin(w) / (2 * q);
  const a0 = 1 + alpha;
  const b0 = alpha / a0;
  const b2 = -alpha / a0;
  const a1 = (-2 * Math.cos(w)) / a0;
  const a2 = (1 - alpha) / a0;
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  return (x) => {
    const y = b0 * x + b2 * x2 - a1 * y1 - a2 * y2;
    x2 = x1; x1 = x; y2 = y1; y1 = y;
    return y;
  };
}

// ---------------------------------------------------------------- the table

/** One note of a sampled instrument at `sr`. */
export function renderSample(inst: SampledInstrument, midi: number, sr: number): Sample {
  const f = midiToFreq(midi);
  switch (inst) {
    case "harp": {
      const t60 = Math.min(5, Math.max(0.9, 3.6 * Math.sqrt(220 / f)));
      return karplus(sr, f, { t60, seconds: Math.min(2.6, t60 * 0.85 + 0.3), pickHz: 2600, position: 0.13, toneMul: 7, seed: midi + 101 });
    }
    case "pizz": {
      const t60 = Math.min(0.9, Math.max(0.3, 0.75 * Math.pow(196 / f, 0.35)));
      return karplus(sr, f, { t60, seconds: t60 * 0.8 + 0.12, pickHz: 1400, position: 0.2, toneMul: 5, seed: midi + 307 });
    }
    case "celesta": {
      const tau = Math.min(1.1, Math.max(0.28, 0.75 * Math.sqrt(523 / f)));
      return struck(sr, f, {
        partials: [[1, 1, 1], [1.0016, 0.22, 0.8], [2, 0.09, 0.35], [4.07, 0.05, 0.12], [9.6, 0.012, 0.04]],
        tau, seconds: Math.min(2.4, tau * 5), attackS: 0.002, click: [0.05, 0.003, 3500], seed: midi + 503,
      });
    }
    case "musicbox": {
      const tau = Math.min(0.9, Math.max(0.22, 0.55 * Math.sqrt(1046 / f)));
      return struck(sr, f, {
        partials: [[1, 1, 1], [1.0031, 0.38, 0.9], [2, 0.05, 0.4], [6.27, 0.16, 0.1], [17.55, 0.05, 0.03]],
        tau, seconds: Math.min(2.4, tau * 5), attackS: 0.0015, click: [0.12, 0.0015, 7000], seed: midi + 709,
      });
    }
    case "glock": {
      const tau = Math.min(1.4, Math.max(0.4, 1.0 * Math.pow(1046 / f, 0.4)));
      return struck(sr, f, {
        partials: [[1, 1, 1], [2.76, 0.28, 0.22], [5.4, 0.12, 0.09], [8.93, 0.04, 0.05]],
        tau, seconds: Math.min(2.8, tau * 5), attackS: 0.001, click: [0.15, 0.0012, 8000], seed: midi + 911,
      });
    }
    case "timpani": {
      return struck(sr, f, {
        partials: [[1, 1, 1], [1.5, 0.45, 0.7], [1.98, 0.28, 0.55], [2.44, 0.16, 0.45], [2.9, 0.08, 0.35], [0.62, 0.35, 0.15]],
        tau: 0.55, seconds: 2.4, attackS: 0.002, click: [0.35, 0.012, 600], glide: [0.025, 0.04], seed: midi + 1103,
      });
    }
    case "frame":
      return frameDrum(sr, midi);
    case "shaker":
      return shaker(sr, midi);
  }
}
