// The synthesizer's building blocks (docs/architecture.md
// "Sound and music"). A synthesized sound's `draw` (types.ts SoundDef
// "synth") builds WebAudio nodes on an OfflineAudioContext from time 0; the
// engine renders it once into a buffer and plays that like a file, so these
// can be generous with nodes. Everything is deterministic: randomness comes
// from a seeded generator (the id and the variant), never Math.random, so a
// sound renders the same every time and in every browser.
//
// Levels: a part's `peak` is linear (1 = full scale). Each sound's overall
// level is then set by its definition's `gain` (levels.ts, measured on the
// preview page so families sit together).

import type { SoundDef } from "../types";

export type Draw = (ctx: BaseAudioContext, out: AudioNode, variant: number) => void;

/** A synthesized definition: `seconds` long, `variants` renders of `draw`. */
export function synth(seconds: number, draw: Draw, o: { variants?: number; gain?: number; channels?: 1 | 2; seamless?: boolean } = {}): SoundDef {
  return { kind: "synth", seconds, draw: draw as (ctx: OfflineAudioContext, out: AudioNode, variant: number) => void, ...o };
}

// ---------------------------------------------------------------- randomness

/** mulberry32: a small, fast, seeded generator in [0, 1). */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A seed from a name and a variant (FNV-1a). */
export function seed(name: string, variant = 0): number {
  let h = 0x811c9dc5;
  const s = `${name}#${variant}`;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** A value in [lo, hi) from a generator. */
export function between(r: () => number, lo: number, hi: number): number {
  return lo + (hi - lo) * r();
}

// ---------------------------------------------------------------- pitch

/** A MIDI note's frequency (69 = A4 = 440 Hz). */
export function hz(midi: number): number {
  return 440 * Math.pow(2, (midi - 69) / 12);
}

// ---------------------------------------------------------------- envelopes

const FLOOR = 1e-4;

/**
 * An attack-decay envelope on `p`: silent until `t`, up to `peak` over `a`
 * (linear), then down to silence over `d` (exponential, the natural decay of
 * a struck or plucked thing), then 0.
 */
export function ad(p: AudioParam, t: number, peak: number, a: number, d: number): void {
  const pk = Math.max(peak, FLOOR * 2);
  p.setValueAtTime(0, 0);
  p.setValueAtTime(0, t);
  p.linearRampToValueAtTime(pk, t + Math.max(a, 0.001));
  p.exponentialRampToValueAtTime(FLOOR, t + Math.max(a, 0.001) + Math.max(d, 0.005));
  p.setValueAtTime(0, t + Math.max(a, 0.001) + Math.max(d, 0.005) + 0.001);
}

/** A swell: up over `a`, held for `h`, down over `r` (all linear). */
export function ahr(p: AudioParam, t: number, peak: number, a: number, h: number, r: number): void {
  p.setValueAtTime(0, 0);
  p.setValueAtTime(0, t);
  p.linearRampToValueAtTime(peak, t + Math.max(a, 0.001));
  p.setValueAtTime(peak, t + Math.max(a, 0.001) + h);
  p.linearRampToValueAtTime(0, t + Math.max(a, 0.001) + h + Math.max(r, 0.005));
}

/** An exponential glide of a frequency param from f to f2 over `s` seconds from t. */
export function glide(p: AudioParam, t: number, f: number, f2: number, s: number): void {
  p.setValueAtTime(f, 0);
  p.setValueAtTime(f, t);
  p.exponentialRampToValueAtTime(Math.max(f2, 1), t + Math.max(s, 0.001));
}

// ---------------------------------------------------------------- nodes

/** A gain into a node, or into a param (an LFO's depth). */
export function gainNode(ctx: BaseAudioContext, value: number, to: AudioNode | AudioParam): GainNode {
  const g = ctx.createGain();
  g.gain.value = value;
  if ("defaultValue" in to) g.connect(to);
  else g.connect(to);
  return g;
}

export function filter(ctx: BaseAudioContext, type: BiquadFilterType, f: number, q: number, to: AudioNode): BiquadFilterNode {
  const b = ctx.createBiquadFilter();
  b.type = type;
  b.frequency.value = f;
  b.Q.value = q;
  b.connect(to);
  return b;
}

/** A soft saturator (tanh), for a little grit on bolts and impacts. */
export function saturate(ctx: BaseAudioContext, drive: number, to: AudioNode): WaveShaperNode {
  const w = ctx.createWaveShaper();
  const n = 1024;
  const curve = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    curve[i] = Math.tanh(x * drive) / Math.tanh(drive);
  }
  w.curve = curve;
  w.connect(to);
  return w;
}

/** A short feedback echo (a small space, a ringing shield). */
export function echo(ctx: BaseAudioContext, delayS: number, feedback: number, wet: number, to: AudioNode): AudioNode {
  const input = ctx.createGain();
  input.connect(to);
  const d = ctx.createDelay(1);
  d.delayTime.value = delayS;
  const fb = gainNode(ctx, feedback, d);
  const w = gainNode(ctx, wet, to);
  input.connect(d);
  d.connect(fb);
  d.connect(w);
  return input;
}

// ---------------------------------------------------------------- voices

export interface ToneOpts {
  f: number;
  /** Glide to this frequency over `glideS` (exponential). */
  f2?: number;
  glideS?: number;
  type?: OscillatorType;
  t?: number;
  a?: number;
  d: number;
  peak: number;
  /** Vibrato: rate (Hz) and depth (cents). */
  vib?: readonly [number, number];
  /** A low-pass on the voice (Hz). */
  lp?: number;
  detune?: number;
}

/** One oscillator through an attack-decay envelope. */
export function tone(ctx: BaseAudioContext, out: AudioNode, o: ToneOpts): void {
  const t = o.t ?? 0;
  const a = o.a ?? 0.004;
  const g = ctx.createGain();
  ad(g.gain, t, o.peak, a, o.d);
  g.connect(out);
  let head: AudioNode = g;
  if (o.lp) head = filter(ctx, "lowpass", o.lp, 0.7, g);
  const osc = ctx.createOscillator();
  osc.type = o.type ?? "sine";
  if (o.f2 !== undefined) glide(osc.frequency, t, o.f, o.f2, o.glideS ?? a + o.d);
  else osc.frequency.value = o.f;
  if (o.detune) osc.detune.value = o.detune;
  if (o.vib) {
    const lfo = ctx.createOscillator();
    lfo.frequency.value = o.vib[0];
    const depth = gainNode(ctx, o.vib[1], osc.detune);
    lfo.connect(depth);
    lfo.start(t);
    lfo.stop(t + a + o.d + 0.05);
  }
  osc.connect(head);
  osc.start(t);
  osc.stop(t + a + o.d + 0.05);
}

const NOISE = new WeakMap<BaseAudioContext, AudioBuffer>();

/** Two seconds of seeded white noise, made once per context. */
export function noiseBuffer(ctx: BaseAudioContext): AudioBuffer {
  let b = NOISE.get(ctx);
  if (!b) {
    const n = Math.floor(ctx.sampleRate * 2);
    b = ctx.createBuffer(1, n, ctx.sampleRate);
    const d = b.getChannelData(0);
    const r = rng(0x5eed);
    for (let i = 0; i < n; i++) d[i] = r() * 2 - 1;
    NOISE.set(ctx, b);
  }
  return b;
}

export interface NoiseOpts {
  t?: number;
  a?: number;
  d: number;
  peak: number;
  /** The filter (default bandpass) and its centre or corner (Hz). */
  type?: BiquadFilterType;
  f: number;
  /** Sweep the filter to f2 over sweepS. */
  f2?: number;
  sweepS?: number;
  q?: number;
  /** Where in the noise buffer to start (0..1): a different grain each time. */
  offset?: number;
  /** A second, high-pass corner (Hz). */
  hp?: number;
  /** Swell instead of strike: hold `h` at peak, release over d (linear). */
  h?: number;
}

/** Filtered noise through an envelope: air, breath, hiss, rustle, impact. */
export function noise(ctx: BaseAudioContext, out: AudioNode, o: NoiseOpts): void {
  const t = o.t ?? 0;
  const a = o.a ?? 0.003;
  const g = ctx.createGain();
  if (o.h !== undefined) ahr(g.gain, t, o.peak, a, o.h, o.d);
  else ad(g.gain, t, o.peak, a, o.d);
  g.connect(out);
  let head: AudioNode = g;
  if (o.hp) head = filter(ctx, "highpass", o.hp, 0.7, head);
  const f = filter(ctx, o.type ?? "bandpass", o.f, o.q ?? 1, head);
  if (o.f2 !== undefined) glide(f.frequency, t, o.f, o.f2, o.sweepS ?? a + (o.h ?? 0) + o.d);
  const src = ctx.createBufferSource();
  src.buffer = noiseBuffer(ctx);
  src.loop = true;
  src.connect(f);
  const len = a + (o.h ?? 0) + o.d + 0.05;
  src.start(t, ((((o.offset ?? 0) % 1) + 1) % 1) * 1.9);
  src.stop(t + len);
}

/** Partials of a bell (inharmonic) or a glass (`glass`: brighter, purer). */
export const BELL = [1, 2.0, 2.76, 3.0, 4.07, 5.43] as const;
export const GLASS = [1, 2.32, 4.25, 6.63] as const;
export const HARP = [1, 2, 3, 4, 5] as const;
export const GONG = [1, 1.48, 1.98, 2.44, 2.9, 3.47, 4.1] as const;

export interface BellOpts {
  f: number;
  t?: number;
  peak: number;
  /** The fundamental's decay (s); higher partials die faster. */
  d: number;
  ratios?: readonly number[];
  /** How fast the partials fall in level (higher: darker). */
  tilt?: number;
  a?: number;
  /** Slight detuning per partial (cents) for shimmer. */
  spread?: number;
}

/** Additive struck tone: chimes, bells, glass, a harp-like pluck (HARP), a gong (GONG). */
export function bell(ctx: BaseAudioContext, out: AudioNode, o: BellOpts): void {
  const ratios = o.ratios ?? BELL;
  const tilt = o.tilt ?? 1;
  ratios.forEach((ratio, i) => {
    const f = o.f * ratio;
    if (f > ctx.sampleRate * 0.45) return;
    tone(ctx, out, {
      f,
      t: o.t,
      a: o.a ?? 0.002,
      d: o.d / (1 + i * 0.6),
      peak: o.peak / Math.pow(i + 1, tilt),
      detune: o.spread ? (i % 2 ? 1 : -1) * o.spread : 0,
    });
  });
}

/** A run of notes: `notes` are MIDI numbers, `step` seconds apart, each struck by `voice`. */
export function arp(notes: readonly number[], step: number, t0: number, voice: (f: number, t: number, i: number) => void): void {
  notes.forEach((n, i) => voice(hz(n), t0 + i * step, i));
}

/** Glitter: `count` tiny high pings scattered over `spanS` from t (seeded). */
export function sparkle(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; spanS: number; count: number; peak: number; lo?: number; hi?: number; seed: number; d?: number }): void {
  const r = rng(o.seed);
  for (let i = 0; i < o.count; i++) {
    const t = (o.t ?? 0) + r() * o.spanS;
    const f = between(r, o.lo ?? 2500, o.hi ?? 6500);
    tone(ctx, out, { f, t, a: 0.002, d: o.d ?? between(r, 0.08, 0.25), peak: o.peak * between(r, 0.4, 1) });
  }
}

/** Crackle: short bright clicks scattered over `spanS` (fire, sputter, dust). */
export function crackle(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; spanS: number; count: number; peak: number; f?: number; seed: number }): void {
  const r = rng(o.seed);
  for (let i = 0; i < o.count; i++) {
    noise(ctx, out, {
      t: (o.t ?? 0) + r() * o.spanS,
      a: 0.001,
      d: between(r, 0.006, 0.03),
      peak: o.peak * between(r, 0.3, 1),
      f: (o.f ?? 3000) * between(r, 0.6, 1.6),
      q: 1.5,
      offset: r(),
    });
  }
}

/** A low thump: a sine dropping in pitch (a body, a stone, a cauldron lid). */
export function thump(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; f: number; f2?: number; d: number; peak: number }): void {
  tone(ctx, out, { f: o.f, f2: o.f2 ?? o.f * 0.5, glideS: o.d * 0.6, t: o.t, a: 0.002, d: o.d, peak: o.peak });
}

/** A whoosh: band-passed noise swelling and sweeping (air moved past). */
export function whoosh(ctx: BaseAudioContext, out: AudioNode, o: { t?: number; f: number; f2: number; a: number; d: number; peak: number; q?: number; offset?: number }): void {
  noise(ctx, out, { t: o.t, a: o.a, h: 0, d: o.d, peak: o.peak, f: o.f, f2: o.f2, sweepS: o.a + o.d, q: o.q ?? 1.2, offset: o.offset });
}
