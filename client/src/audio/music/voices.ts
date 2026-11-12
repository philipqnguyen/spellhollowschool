// The music's instruments as WebAudio voices. Every note is a buffer
// rendered once per instrument and pitch in plain JS (samples.ts for the
// plucked and struck ones, sustain.ts for winds, bowed strings, horn and
// choir, which loop seamlessly for as long as the note is held), played by
// one buffer source and one gain: two nodes a note, however rich the sound,
// which keeps the audio thread's work small. Every voice disconnects its
// nodes when it ends.
//
// Works on any BaseAudioContext: the game's AudioContext and the listening
// page's OfflineAudioContext (previews) play the same voices.

import { renderSample, SAMPLED, type Sample, type SampledInstrument } from "./samples";
import type { Instrument } from "./score";
import { isSustained, RELEASE, renderSustain, SWELL } from "./sustain";

export interface InstMix {
  /** Level into the mix (set by measured loudness, each alone on the same line). */
  gain: number;
  /** -1..1. */
  pan: number;
  /** Reverb send. */
  wet: number;
  /** Seconds a note sounds past its written length (the cap counts it). */
  tail: number;
}

export const INST: Readonly<Record<Instrument, InstMix>> = {
  harp: { gain: 0.91, pan: 0.22, wet: 0.45, tail: 1.6 },
  celesta: { gain: 0.37, pan: -0.18, wet: 0.55, tail: 1.6 },
  musicbox: { gain: 0.47, pan: 0.12, wet: 0.5, tail: 1.4 },
  glock: { gain: 0.3, pan: 0.28, wet: 0.55, tail: 1.8 },
  pizz: { gain: 1.09, pan: 0.18, wet: 0.35, tail: 0.5 },
  flute: { gain: 0.224, pan: -0.2, wet: 0.5, tail: 0.2 },
  whistle: { gain: 0.238, pan: -0.12, wet: 0.4, tail: 0.12 },
  clarinet: { gain: 0.188, pan: 0.16, wet: 0.45, tail: 0.16 },
  oboe: { gain: 0.323, pan: -0.08, wet: 0.45, tail: 0.15 },
  bassoon: { gain: 0.374, pan: 0.1, wet: 0.4, tail: 0.16 },
  fiddle: { gain: 0.345, pan: -0.15, wet: 0.4, tail: 0.16 },
  strings: { gain: 0.372, pan: 0.05, wet: 0.65, tail: 0.4 },
  cello: { gain: 0.38, pan: 0.2, wet: 0.45, tail: 0.28 },
  bass: { gain: 0.401, pan: 0, wet: 0.2, tail: 0.22 },
  horn: { gain: 0.394, pan: 0.25, wet: 0.6, tail: 0.3 },
  choir: { gain: 0.368, pan: -0.05, wet: 0.7, tail: 0.45 },
  frame: { gain: 0.667, pan: -0.1, wet: 0.2, tail: 0.4 },
  timpani: { gain: 0.518, pan: -0.15, wet: 0.45, tail: 1.6 },
  shaker: { gain: 0.523, pan: 0.32, wet: 0.15, tail: 0.1 },
};

/** How long a note of `inst` written `dur` long sounds (s): the cap's count and the voice's own stop. */
export function noteLife(inst: Instrument, dur: number): number {
  switch (inst) {
    case "harp": return Math.min(2.6, Math.max(dur, 0.45) + INST.harp.tail);
    case "celesta": case "musicbox": return Math.min(2.4, Math.max(dur, 0.4) + INST[inst].tail);
    case "glock": return Math.min(2.8, Math.max(dur, 0.4) + INST.glock.tail);
    case "pizz": return 0.7;
    case "timpani": return dur < 0.3 ? 0.8 : 2;
    case "frame": return 0.45;
    case "shaker": return 0.15;
    default: return Math.max(dur, 0.06) + INST[inst].tail;
  }
}

/**
 * The rate a note is rendered at (the context resamples): 32 kHz for the
 * bright ones (bells, the music box, high notes), 22.05 kHz below 700 Hz,
 * where these voices have nothing above 11 kHz. Less memory, faster to render.
 */
export function noteRate(inst: Instrument, midi: number): number {
  if (inst === "celesta" || inst === "musicbox" || inst === "glock" || inst === "shaker") return 32000;
  return midi <= 76 ? 22050 : 32000;
}
/** Rendered notes kept per context (least recently used beyond this). */
export const SAMPLE_CACHE_BYTES = 32 * 1024 * 1024;

interface Entry {
  buf: AudioBuffer;
  rate: number;
  loop: readonly [number, number] | null;
  bytes: number;
}
interface Caches {
  notes: Map<string, Entry>;
  bytes: number;
}
const caches = new WeakMap<BaseAudioContext, Caches>();

function cachesOf(c: BaseAudioContext): Caches {
  let k = caches.get(c);
  if (!k) {
    k = { notes: new Map(), bytes: 0 };
    caches.set(c, k);
  }
  return k;
}

/** Renders one note's samples (pure JS). */
export function renderNote(inst: Instrument, midi: number, sr = noteRate(inst, midi)): Sample {
  if (isSustained(inst)) return renderSustain(inst, midi, sr);
  if (SAMPLED.has(inst)) return renderSample(inst as SampledInstrument, midi, sr);
  throw new Error(`no voice for ${inst}`);
}

/**
 * One rendering serves two semitones: a note renders on the even MIDI number
 * at or below it and an odd one plays it a semitone faster (the timbre moves
 * by 6%, which these voices never show). Half the notes to render and hold.
 */
export function anchorOf(midi: number): number {
  return midi - (((midi % 2) + 2) % 2);
}

/** The buffer that plays `midi` and the playback rate that puts it on pitch. */
export function noteFor(c: BaseAudioContext, inst: Instrument, midi: number): { e: Entry; rate: number } {
  const a = anchorOf(midi);
  const e = noteBuffer(c, inst, a);
  return { e, rate: e.rate * Math.pow(2, (midi - a) / 12) };
}

/** The buffer for one rendered note (on first use; least recently used evicted past the cap). */
export function noteBuffer(c: BaseAudioContext, inst: Instrument, midi: number): Entry {
  const k = cachesOf(c);
  const key = `${inst}:${midi}`;
  const hit = k.notes.get(key);
  if (hit) {
    k.notes.delete(key);
    k.notes.set(key, hit);
    return hit;
  }
  const s = renderNote(inst, midi);
  const buf = c.createBuffer(1, s.data.length, s.sampleRate);
  buf.getChannelData(0).set(s.data);
  const entry: Entry = { buf, rate: s.rate, loop: s.loopStart !== undefined ? [s.loopStart, s.loopEnd!] : null, bytes: s.data.length * 4 };
  k.notes.set(key, entry);
  k.bytes += entry.bytes;
  for (const [old, e] of k.notes) {
    if (k.bytes <= SAMPLE_CACHE_BYTES) break;
    if (old === key) continue;
    k.notes.delete(old);
    k.bytes -= e.bytes;
  }
  return entry;
}

/** Whether a note's buffer is ready (the engine warms a piece's notes before it needs them). */
export function noteReady(c: BaseAudioContext, inst: Instrument, midi: number): boolean {
  return cachesOf(c).notes.has(`${inst}:${anchorOf(midi)}`);
}

/** Bytes of rendered notes held for this context. */
export function cachedBytes(c: BaseAudioContext): number {
  return cachesOf(c).bytes;
}

/** Velocity to level: a little steeper than linear, so soft is soft. */
function level(vel: number): number {
  return Math.pow(Math.max(0, Math.min(1, vel)), 1.5);
}

/** One note into `out` at time `t` (context seconds). Returns when it stops sounding. */
export function playNote(c: BaseAudioContext, out: AudioNode, inst: Instrument, midi: number, t: number, dur: number, vel: number): number {
  const { e, rate } = noteFor(c, inst, midi);
  const src = c.createBufferSource();
  src.buffer = e.buf;
  src.playbackRate.value = rate;
  const g = c.createGain();
  const lv = level(vel);
  let stop: number;
  if (e.loop && isSustained(inst)) {
    src.loop = true;
    src.loopStart = e.loop[0];
    src.loopEnd = e.loop[1];
    const sw = SWELL[inst];
    const swell = sw ? Math.min(sw[1], Math.max(sw[0], dur * sw[2])) : 0;
    const end = t + Math.max(dur, 0.06);
    const rel = RELEASE[inst];
    g.gain.setValueAtTime(swell > 0.01 ? 0 : lv, t);
    if (swell > 0.01) g.gain.linearRampToValueAtTime(lv, t + swell);
    g.gain.setValueAtTime(lv, end);
    g.gain.setTargetAtTime(0, end, rel / 5);
    stop = end + rel;
  } else {
    const life = Math.min(noteLife(inst, dur), e.buf.duration / rate);
    g.gain.setValueAtTime(lv, t);
    // a damped note: a struck bar or a harp string let go of early
    g.gain.setValueAtTime(lv, t + Math.max(0.01, life - 0.12));
    g.gain.linearRampToValueAtTime(0, t + life);
    stop = t + life;
  }
  src.connect(g);
  g.connect(out);
  src.start(t);
  src.stop(stop + 0.02);
  src.onended = () => {
    src.disconnect();
    g.disconnect();
  };
  return stop;
}
