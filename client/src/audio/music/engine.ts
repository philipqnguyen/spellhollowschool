// The music engine (docs/architecture.md "Sound and music"): plays the
// written scores live on the music bus.
//
//   score (scores/*.ts) -> compile() -> timed notes -> transport -> voices.ts
//
// The transport schedules notes a little ahead of the audio clock from a
// timer, so a frame hitch never stutters the music; a suspended context (the
// tab hidden) stops the clock and the score waits with it. A play of the form
// is followed by a rest of silence (the score's `restS`, else rules.ts
// MUSIC_REST_S), then the form again from `loopFrom`. The night variant and
// the quality's optional parts change at the next section boundary, so a
// switch is never a jump. A new cue fades the old piece out and starts the
// new one FADES.musicGap later. Stingers play over the loop, which ducks.
// The note cap (rules.ts VOICE_CAPS.musicNotes) drops arpeggios, optional
// lines and pads first; a written melody is never dropped.

import type { Quality } from "../../settings";
import { SOUNDS, type MusicId, type StingerId } from "../catalogue";
import { FADES, VOICE_CAPS } from "../rules";
import type { MusicCue, MusicEngine, MusicStats } from "../types";
import { compile, cursorAt, loopCursor, type Compiled, type Ev } from "./compile";
import { scoreFor } from "./library";
import type { Instrument, Score } from "./score";
import { seeded } from "./samples";
import { INST, noteFor, noteLife, noteReady, playNote } from "./voices";

/** How far ahead of the clock notes are scheduled (s), and how often the timer looks. */
const LOOKAHEAD_S = 0.4;
/** A piece starts at least this long after its cue, so its first notes render in the timer's spare time, not on the spot. */
export const START_S = 0.3;
const TICK_MS = 60;
/** Main-thread time spent rendering upcoming notes' buffers per tick (ms). */
const WARM_BUDGET_MS = 6;
/** The reverb's length by quality (s): Low's is shorter and cheaper. */
const REVERB_S: Readonly<Record<Quality, number>> = { low: 1.4, medium: 2.6, high: 2.8 };
/**
 * The engine's output level (the music bus and the player's slider follow).
 * Review: 2 dB down from 1.2 so the loop sits under doors and spells at the
 * default sliders (it measured level with them).
 */
const OUTPUT = 0.95;

export interface EngineDeps {
  /** Seconds on the audio clock (an offline render advances its own). */
  now?: () => number;
  /** Run `fn` after `s` seconds (cleanup); offline renders skip it. */
  later?: (fn: () => void, s: number) => void;
  random?: () => number;
  /** Start the transport's timer (false: the caller ticks, as an offline render does). */
  timer?: boolean;
  /** Where scores come from (default: scores/); the listening page renders ad-hoc ones. */
  lookup?: (id: string) => Score | undefined;
  /** Measuring only: no reverb at all (its cost and its share of the sound). */
  dry?: boolean;
  /** Every note scheduled (tests). */
  onNote?: (e: Ev, at: number, take: string) => void;
}

interface Channel {
  g: GainNode;
  pan: StereoPannerNode;
  send: GainNode;
}

interface Take {
  id: string;
  wetMul: number;
  /** The score's level trim (linear). */
  trim: number;
  channels: Map<Instrument, Channel>;
  fading: boolean;
  /** The loop's duck under a stinger: until then (s), and back up over FADES.stingerRelease. */
  duckUntil: number;
}

interface Playing {
  score: Score;
  take: Take;
  wantNight: boolean;
  wantLow: boolean;
  c: Compiled;
  origin: number;
  cursor: number;
  fi: number;
  restFrom: number;
  restUntil: number;
  warm: Ev[];
}

interface Live {
  start: number;
  end: number;
}

export interface MusicEngineFull extends MusicEngine {
  /** One transport step (the timer calls it; an offline render calls it itself). */
  tick(): void;
}

export function createEngine(ctx: BaseAudioContext, out: AudioNode, quality: Quality, deps: EngineDeps = {}): MusicEngineFull {
  const now = deps.now ?? (() => ctx.currentTime);
  const later = deps.later ?? ((fn: () => void, s: number) => void setTimeout(fn, s * 1000));
  const random = deps.random ?? Math.random;
  const lookup = deps.lookup ?? ((id: string) => scoreFor(id as MusicId | StingerId));
  let q = quality;

  // ---- the graph: dry + reverb -> glue compressor -> fader -> soft safety -> bus
  const shaper = ctx.createWaveShaper();
  shaper.curve = safetyCurve();
  const pre = ctx.createGain();
  pre.gain.value = 0.5;
  const post = ctx.createGain();
  post.gain.value = 2;
  pre.connect(shaper);
  shaper.connect(post);
  post.connect(out);
  const master = ctx.createGain();
  master.gain.value = OUTPUT;
  master.connect(pre);
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -16;
  comp.knee.value = 14;
  comp.ratio.value = 2.5;
  comp.attack.value = 0.02;
  comp.release.value = 0.3;
  comp.connect(master);
  const dry = ctx.createGain();
  dry.connect(comp);
  const reverbIn = ctx.createGain();
  let reverb = makeReverb(REVERB_S[q]);

  function makeReverb(seconds: number): { conv: ConvolverNode; ret: GainNode } {
    const conv = ctx.createConvolver();
    conv.normalize = false;
    conv.buffer = impulse(ctx, seconds);
    const ret = ctx.createGain();
    ret.gain.value = 0.5;
    if (deps.dry) return { conv, ret };
    reverbIn.connect(conv);
    conv.connect(ret);
    ret.connect(comp);
    return { conv, ret };
  }

  // ---- state
  let playing: Playing | null = null;
  const takes = new Set<Take>();
  const live: Live[] = [];
  let section: string | null = null;
  let disposed = false;
  const compiled = new Map<string, Compiled>();

  function compiledFor(s: Score, night: boolean, low: boolean): Compiled {
    const key = `${s.id}:${night ? "n" : "d"}:${low ? "l" : "f"}`;
    let c = compiled.get(key);
    if (!c) {
      c = compile(s, { night, low });
      compiled.set(key, c);
    }
    return c;
  }

  function openTake(s: Score): Take {
    const t: Take = { id: s.id, wetMul: Math.max(0.4, Math.min(1.6, (s.space ?? 2.4) / 2.4)), trim: Math.pow(10, (s.level ?? 0) / 20), channels: new Map(), fading: false, duckUntil: 0 };
    takes.add(t);
    return t;
  }

  const DUCK = Math.pow(10, FADES.stingerDuckDb / 20);

  function channel(t: Take, inst: Instrument): Channel {
    let ch = t.channels.get(inst);
    if (ch) return ch;
    const mix = INST[inst];
    const full = mix.gain * t.trim;
    const g = ctx.createGain();
    g.gain.value = full;
    const n = now();
    if (t.duckUntil > n) {
      g.gain.setValueAtTime(full * DUCK, n);
      g.gain.setValueAtTime(full * DUCK, t.duckUntil);
      g.gain.linearRampToValueAtTime(full, t.duckUntil + FADES.stingerRelease);
    }
    const pan = ctx.createStereoPanner();
    pan.pan.value = mix.pan;
    const send = ctx.createGain();
    send.gain.value = mix.wet * t.wetMul;
    g.connect(pan);
    pan.connect(dry);
    pan.connect(send);
    send.connect(reverbIn);
    ch = { g, pan, send };
    t.channels.set(inst, ch);
    return ch;
  }

  function releaseTake(t: Take, fadeS: number): void {
    if (t.fading) return;
    t.fading = true;
    const n = now();
    for (const ch of t.channels.values()) {
      const p = ch.g.gain;
      p.cancelScheduledValues(n);
      p.setValueAtTime(p.value, n);
      p.linearRampToValueAtTime(0, n + Math.max(0.05, fadeS));
    }
    later(() => {
      for (const ch of t.channels.values()) {
        ch.g.disconnect();
        ch.pan.disconnect();
        ch.send.disconnect();
      }
      takes.delete(t);
    }, Math.max(0.05, fadeS) + 3.5);
  }

  function start(cue: MusicCue, at: number): void {
    const s = lookup(cue.id);
    if (!s) return;
    const c = compiledFor(s, cue.night, q === "low");
    playing = { score: s, take: openTake(s), wantNight: cue.night, wantLow: q === "low", c, origin: at, cursor: 0, fi: -1, restFrom: 0, restUntil: 0, warm: firstNotes(c) };
  }

  /** The piece's notes in playing order, distinct: their buffers are rendered ahead of need. */
  function firstNotes(c: Compiled): Ev[] {
    const seen = new Set<string>();
    const out: Ev[] = [];
    for (const e of c.events) {
      const k = `${e.inst}:${e.midi}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(e);
    }
    return out;
  }

  // The stingers' notes are rendered when nothing else is waiting, so a level
  // up or a discovery never stalls a frame rendering its notes on the spot.
  let stingerWarm: Ev[] | null = null;

  function warmStep(p: Playing | null): void {
    let queue = p?.warm;
    if (!queue?.length) {
      stingerWarm ??= SOUNDS.filter((x) => x.group === "stingers").flatMap((x) => {
        const s = lookup(x.id);
        return s ? firstNotes(compiledFor(s, false, false)) : [];
      });
      queue = stingerWarm;
    }
    const t0 = performance.now();
    while (queue.length && performance.now() - t0 < WARM_BUDGET_MS) {
      const e = queue.shift()!;
      if (!noteReady(ctx, e.inst, e.midi)) noteFor(ctx, e.inst, e.midi);
    }
  }

  function cap(): number {
    return VOICE_CAPS[q].musicNotes;
  }

  function sounding(at: number): number {
    let n = 0;
    for (const l of live) if (l.start <= at && at < l.end) n++;
    return n;
  }

  function schedule(t: Take, e: Ev, at: number, capped: boolean): void {
    const life = noteLife(e.inst, e.dur);
    if (capped) {
      const n = sounding(at);
      const limit = cap();
      // arpeggios, optional lines and pads go first; the bass a little later; a written line always plays
      if (n >= limit && e.prio >= 2) return;
      if (n >= limit + 4 && e.prio >= 1) return;
    }
    playNote(ctx, channel(t, e.inst).g, e.inst, e.midi, at, e.dur, e.vel);
    live.push({ start: at, end: at + life });
    deps.onNote?.(e, at, t.id);
  }

  function restLength(c: Compiled): number {
    const [a, b] = c.restS;
    return a + (b - a) * random();
  }

  function advance(p: Playing, n: number): void {
    const horizon = n + LOOKAHEAD_S;
    for (let guard = 0; guard < 10000; guard++) {
      if (p.restUntil > 0) {
        if (horizon < p.restUntil) return;
        p.c = compiledFor(p.score, p.wantNight, p.wantLow);
        p.origin = p.restUntil - p.c.loopAt;
        p.cursor = loopCursor(p.c);
        p.fi = p.c.loopFrom;
        p.restUntil = 0;
        p.warm = firstNotes(p.c);
        continue;
      }
      const ev = p.c.events[p.cursor];
      if (!ev) {
        p.restFrom = p.origin + p.c.end;
        p.restUntil = p.restFrom + Math.max(0.05, restLength(p.c));
        continue;
      }
      const at = p.origin + ev.t;
      if (at >= horizon) return;
      if (ev.fi !== p.fi) {
        // a section boundary: the variant and the quality may change here
        const first = p.fi === -1;
        p.fi = ev.fi;
        const want = compiledFor(p.score, p.wantNight, p.wantLow);
        if (!first && want !== p.c) {
          p.origin = p.origin + p.c.marks[ev.fi]!.t - want.marks[ev.fi]!.t;
          p.c = want;
          p.cursor = cursorAt(want, ev.fi);
          p.warm = firstNotes(want);
          continue;
        }
      }
      p.cursor++;
      if (at < n - 0.03) continue; // the timer was late: skip rather than pile up
      schedule(p.take, ev, at, true);
    }
  }

  function tick(): void {
    if (disposed) return;
    const n = now();
    for (let i = live.length - 1; i >= 0; i--) if (live[i]!.end < n) live.splice(i, 1);
    const p = playing;
    if (deps.timer !== false) warmStep(p);
    if (!p) return;
    advance(p, n);
    const m = [...p.c.marks].reverse().find((x) => p.origin + x.t <= n);
    section = p.restUntil > 0 && n >= p.restFrom ? null : (m?.name ?? null);
  }

  const timer = deps.timer === false ? null : setInterval(tick, TICK_MS);

  function setCue(cue: MusicCue | null, fadeS: number): void {
    if (disposed) return;
    if (cue && playing && playing.score.id === cue.id) {
      playing.wantNight = cue.night;
      return;
    }
    const n = now();
    // The new piece starts FADES.musicGap into the old one's fade (its notes
    // render meanwhile); replacing a piece that had not begun keeps its start.
    let at = n + START_S;
    if (playing) {
      if (playing.origin > n) at = Math.max(at, playing.origin);
      else if (fadeS > 0) at = n + Math.min(FADES.musicGap, fadeS);
      releaseTake(playing.take, fadeS);
      playing = null;
      section = null;
    }
    if (cue) start(cue, at);
  }

  function stinger(id: StingerId): void {
    if (disposed) return;
    const s = lookup(id);
    if (!s) return;
    const c = compiledFor(s, false, q === "low");
    const t = openTake(s);
    const n = now() + 0.05;
    for (const e of c.events) schedule(t, e, n + e.t, false);
    const end = n + c.end;
    // the loop dips under it, unless it is resting anyway
    const p = playing;
    if (p && !p.take.fading && !(p.restUntil > 0 && n >= p.restFrom)) {
      p.take.duckUntil = end;
      for (const [inst, ch] of p.take.channels) {
        const g = ch.g.gain;
        const full = INST[inst].gain * p.take.trim;
        g.cancelScheduledValues(n);
        g.setValueAtTime(g.value, n);
        g.linearRampToValueAtTime(full * DUCK, n + 0.12);
        g.setValueAtTime(full * DUCK, end);
        g.linearRampToValueAtTime(full, end + FADES.stingerRelease);
      }
    }
    t.fading = true;
    later(() => {
      for (const ch of t.channels.values()) {
        ch.g.disconnect();
        ch.pan.disconnect();
        ch.send.disconnect();
      }
      takes.delete(t);
    }, c.end + 4);
  }

  function setQuality(next: Quality): void {
    if (next === q) return;
    const swapReverb = REVERB_S[next] !== REVERB_S[q];
    q = next;
    if (playing) playing.wantLow = q === "low";
    if (swapReverb && !disposed) {
      const old = reverb;
      const n = now();
      reverb = makeReverb(REVERB_S[q]);
      reverb.ret.gain.setValueAtTime(0, n);
      reverb.ret.gain.linearRampToValueAtTime(0.5, n + 0.5);
      old.ret.gain.setValueAtTime(old.ret.gain.value, n);
      old.ret.gain.linearRampToValueAtTime(0, n + 0.5);
      later(() => {
        reverbIn.disconnect(old.conv);
        old.conv.disconnect();
        old.ret.disconnect();
      }, 3.5);
    }
  }

  function stats(): MusicStats {
    const n = now();
    const p = playing;
    return {
      cue: p ? `${p.score.id}${p.wantNight ? " (night)" : ""}${p.origin > n ? " (starting)" : ""}` : null,
      section,
      voices: sounding(n),
      resting: !!p && p.restUntil > 0 && n >= p.restFrom,
    };
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    if (timer !== null) clearInterval(timer);
    const n = now();
    master.gain.setValueAtTime(master.gain.value, n);
    master.gain.linearRampToValueAtTime(0, n + 0.1);
    later(() => {
      for (const t of takes) for (const ch of t.channels.values()) { ch.g.disconnect(); ch.pan.disconnect(); ch.send.disconnect(); }
      takes.clear();
      for (const node of [dry, reverbIn, reverb.conv, reverb.ret, comp, pre, shaper, post, master]) node.disconnect();
    }, 0.2);
    playing = null;
  }

  return { setCue, stinger, setQuality, stats, dispose, tick };
}

// ---------------------------------------------------------------- the reverb and the safety curve

/** A synthesized hall: decorrelated noise under an exponential decay that darkens as it fades. */
function impulse(ctx: BaseAudioContext, seconds: number): AudioBuffer {
  const sr = ctx.sampleRate;
  const pre = Math.floor(sr * 0.018);
  const len = pre + Math.floor(sr * seconds);
  const buf = ctx.createBuffer(2, len, sr);
  for (let chn = 0; chn < 2; chn++) {
    const d = buf.getChannelData(chn);
    const rnd = seeded(1000 + chn * 77 + Math.round(seconds * 10));
    let lp = 0;
    let energy = 0;
    let a = 0;
    let env = 1;
    const fall = Math.exp(-6.9 / (seconds * sr));
    const onset = Math.floor(sr * 0.01);
    for (let i = pre; i < len; i++) {
      const k = i - pre;
      // brightness falls from ~7 kHz to ~1.2 kHz across the tail (the filter retuned every 64 samples)
      if ((k & 63) === 0) a = 1 - Math.exp((-2 * Math.PI * 7000 * Math.pow(1200 / 7000, k / sr / seconds)) / sr);
      lp += a * (rnd() * 2 - 1 - lp);
      d[i] = lp * env * (k < onset ? k / onset : 1);
      env *= fall;
      energy += d[i]! * d[i]!;
    }
    // a few early reflections
    [0.009, 0.017, 0.029, 0.041].forEach((s, k) => {
      const i = pre + Math.floor(s * sr) + (chn ? 23 : 0);
      if (i < len) d[i]! += (k % 2 ? -0.35 : 0.35) / (k + 1);
    });
    // unit energy: a noise-like input comes out of the hall at its own level
    const norm = 1 / Math.sqrt(energy + 1e-9);
    for (let i = 0; i < len; i++) d[i]! *= norm;
  }
  return buf;
}

let curve: Float32Array<ArrayBuffer> | null = null;
/** Straight below -1.5 dBFS, a tanh knee into full scale above (inputs of +-2 over the table's +-1). */
function safetyCurve(): Float32Array<ArrayBuffer> {
  if (curve) return curve;
  const K = 0.84;
  const n = 4097;
  const c = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = 2 * ((2 * i) / (n - 1) - 1);
    const a = Math.abs(x);
    const y = a <= K ? a : K + (1 - K) * Math.tanh((a - K) / (1 - K));
    c[i] = (Math.sign(x) * y) / 2;
  }
  curve = c;
  return c;
}
