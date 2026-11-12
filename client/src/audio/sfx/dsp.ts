// Seamless loops computed sample by sample (docs/
// architecture.md "Sound and music"): room tones, wind, grinding stone, a
// simmering cauldron, the Owl Loft. A loop made of WebAudio nodes starts with
// its filters at rest, so its first moments differ from its last and the seam
// clicks; here every filter runs over the loop twice and the second pass is
// kept, so its state at the start is its state at the end, every modulation
// has a whole number of cycles per loop, and events near the end wrap round
// to the start. The result repeats without a seam (`seamless: true`).
//
// Pure functions on Float32Arrays, no WebAudio: unit tested in node
// (sfx.test.ts checks the seam), then copied into an AudioBuffer by `loopDef`.

import type { SoundDef } from "../types";
import { rng } from "./kit";

export type Signal = Float32Array;

/** White noise, seeded. */
export function white(n: number, seed: number): Signal {
  const r = rng(seed);
  const x = new Float32Array(n);
  for (let i = 0; i < n; i++) x[i] = r() * 2 - 1;
  return x;
}

/** RBJ biquad coefficients. */
function coeffs(type: "lowpass" | "highpass" | "bandpass", f: number, q: number, sr: number): [number, number, number, number, number] {
  const w = (2 * Math.PI * Math.min(Math.max(f, 10), sr * 0.45)) / sr;
  const cos = Math.cos(w);
  const alpha = Math.sin(w) / (2 * q);
  let b0: number, b1: number, b2: number;
  if (type === "lowpass") {
    b0 = (1 - cos) / 2;
    b1 = 1 - cos;
    b2 = (1 - cos) / 2;
  } else if (type === "highpass") {
    b0 = (1 + cos) / 2;
    b1 = -(1 + cos);
    b2 = (1 + cos) / 2;
  } else {
    b0 = alpha;
    b1 = 0;
    b2 = -alpha;
  }
  const a0 = 1 + alpha;
  return [b0 / a0, b1 / a0, b2 / a0, (-2 * cos) / a0, (1 - alpha) / a0];
}

/**
 * A biquad over a loop, run twice round so it wraps without a seam. `f` is a
 * constant or a function of the loop's phase (0..1), updated every 64 samples.
 */
export function biquad(x: Signal, type: "lowpass" | "highpass" | "bandpass", f: number | ((phase: number) => number), q: number, sr: number): Signal {
  const n = x.length;
  const y = new Float32Array(n);
  let x1 = 0,
    x2 = 0,
    y1 = 0,
    y2 = 0;
  let c = coeffs(type, typeof f === "number" ? f : f(0), q, sr);
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 0; i < n; i++) {
      if (typeof f !== "number" && (i & 63) === 0) c = coeffs(type, f(i / n), q, sr);
      const xi = x[i]!;
      const yi = c[0] * xi + c[1] * x1 + c[2] * x2 - c[3] * y1 - c[4] * y2;
      x2 = x1;
      x1 = xi;
      y2 = y1;
      y1 = yi;
      if (pass === 1) y[i] = yi;
    }
  }
  return y;
}

/** A one-pole low-pass (brown-ish noise from white), wrapped like `biquad`. */
export function onePole(x: Signal, f: number, sr: number): Signal {
  const k = 1 - Math.exp((-2 * Math.PI * f) / sr);
  const y = new Float32Array(x.length);
  let s = 0;
  for (let pass = 0; pass < 2; pass++)
    for (let i = 0; i < x.length; i++) {
      s += k * (x[i]! - s);
      if (pass === 1) y[i] = s;
    }
  return y;
}

/** Multiply by a gain that is a function of the loop's phase (0..1); make it periodic. */
export function shape(x: Signal, g: (phase: number) => number): Signal {
  const n = x.length;
  const y = new Float32Array(n);
  for (let i = 0; i < n; i++) y[i] = x[i]! * g(i / n);
  return y;
}

/** Sum signals into the first (same length), each scaled. */
export function mix(...parts: [Signal, number][]): Signal {
  const n = parts[0]![0].length;
  const y = new Float32Array(n);
  for (const [x, g] of parts) for (let i = 0; i < n; i++) y[i] += x[i]! * g;
  return y;
}

/** Peak-normalise to `peak` (linear). */
export function normalise(x: Signal, peak: number): Signal {
  let m = 0;
  for (let i = 0; i < x.length; i++) m = Math.max(m, Math.abs(x[i]!));
  if (m === 0) return x;
  const k = peak / m;
  for (let i = 0; i < x.length; i++) x[i]! *= k;
  return x;
}

/** A smooth periodic wobble in [0, 1]: a sum of whole-cycle sines (loop phase in, seeded phases). */
export function wobble(cycles: readonly number[], seed: number): (phase: number) => number {
  const r = rng(seed);
  const ph = cycles.map(() => r() * Math.PI * 2);
  return (p) => {
    let s = 0;
    cycles.forEach((c, i) => (s += Math.sin(2 * Math.PI * c * p + ph[i]!)));
    return 0.5 + (0.5 * s) / cycles.length;
  };
}

/**
 * Add a short event (`len` samples made by `make(i)`) at sample `at`, wrapping
 * past the end round to the start, so events near the seam stay whole.
 */
export function addEvent(y: Signal, at: number, len: number, make: (i: number) => number): void {
  const n = y.length;
  for (let i = 0; i < len; i++) y[(at + i) % n]! += make(i);
}

/** A sine blip whose pitch moves (a bubble, a drip, a coo): f(t) over its length, an attack-decay envelope. */
export function blip(sr: number, len: number, f0: number, f1: number, a: number, curve = 1): (i: number) => number {
  let ph = 0;
  const na = Math.max(1, Math.floor(a * sr));
  return (i) => {
    const u = i / len;
    const f = f0 * Math.pow(f1 / f0, Math.pow(u, curve));
    ph += (2 * Math.PI * f) / sr;
    const env = i < na ? i / na : Math.pow(1 - (i - na) / Math.max(1, len - na), 2);
    return Math.sin(ph) * env;
  };
}

/** A loop's definition: `make(sr, n, channel)` returns each channel's samples (n = seconds x sr). */
export function loopDef(seconds: number, make: (sr: number, n: number, channel: number) => Signal, o: { channels?: 1 | 2; gain?: number } = {}): SoundDef {
  const channels = o.channels ?? 1;
  return {
    kind: "synth",
    seconds,
    channels,
    gain: o.gain,
    seamless: true,
    draw: (ctx, out) => {
      const sr = ctx.sampleRate;
      const n = Math.round(seconds * sr);
      const buf = ctx.createBuffer(channels, n, sr);
      for (let c = 0; c < channels; c++) buf.getChannelData(c).set(make(sr, n, c).subarray(0, n));
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(out);
      src.start(0);
    },
  };
}
