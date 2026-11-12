// A score (score.ts) compiled to timed notes: the written parts read, the
// generated accompaniment (pad, bass, arp) built on the chord changes, the
// night variant and the quality applied. Pure and DOM-free: the engine plays
// what it returns, and the tests measure it.

import { MUSIC_REST_S } from "../rules";
import { chordBars, measure, parseNotes, placeAbove, type Chord } from "./notation";
import type { Instrument, Part, Score, Section } from "./score";

export interface Ev {
  /** Seconds from the start of the form. */
  t: number;
  inst: Instrument;
  midi: number;
  /** Written length (s). */
  dur: number;
  /** 0..1. */
  vel: number;
  /** The form index it belongs to. */
  fi: number;
  /** 0: written line; 1: bass; 2: pad; 3: arpeggio or optional line. Higher goes first under the note cap. */
  prio: number;
}

/** Where each entry of the form starts. */
export interface Mark {
  t: number;
  fi: number;
  key: string;
  name: string;
  bars: number;
}

export interface Compiled {
  id: string;
  night: boolean;
  low: boolean;
  events: Ev[];
  marks: Mark[];
  /** Length of one play of the form (s). */
  end: number;
  /** The form index the next play starts from, and its time. */
  loopFrom: number;
  loopAt: number;
  /** Seconds per bar. */
  bar: number;
  restS: readonly [number, number];
  space: number;
}

export interface CompileOpts {
  night?: boolean;
  /** Low quality: optional parts dropped. */
  low?: boolean;
}

/** The pulse a listener feels (compound meters count dotted beats): what the brief's bpm means. */
export function pulseBpm(s: Score): number {
  const [n, d] = s.meter;
  return d === 8 && n % 3 === 0 ? s.bpm / 3 : s.bpm;
}

/** Beats a section lasts: its chord bars, or its longest part. */
export function sectionBeats(s: Score, sec: Section): number {
  if (sec.chords) return chordBars(sec.chords).length * s.meter[0];
  return Math.max(0, ...sec.parts.map((p) => measure(p.notes, p.unit ?? 1)));
}

/** Deterministic 0..1 from integers (humanizing, never Math.random: renders repeat). */
function hash(a: number, b: number, c: number): number {
  const x = Math.sin(a * 127.1 + b * 311.7 + c * 74.7) * 43758.5453;
  return x - Math.floor(x);
}

/** A chord voiced compactly round `center` (at most four tones, the fifth dropped first). */
export function voice(ch: Chord, center: number): number[] {
  let tones = [...ch.tones];
  if (tones.length > 4) tones = tones.filter((x) => x !== 7);
  const lo = center - 5;
  return tones.map((s) => placeAbove(ch.root + s, lo)).sort((a, b) => a - b);
}

function sameChord(a: Chord | null, b: Chord | null): boolean {
  if (!a || !b) return a === b;
  return a.root === b.root && a.bass === b.bass && a.tones.join() === b.tones.join();
}

const BASS_FLOOR = 36; // C2

export function compile(score: Score, opts: CompileOpts = {}): Compiled {
  const night = !!opts.night;
  const low = !!opts.low;
  const v = night ? (score.night ?? {}) : {};
  const spb = 60 / (score.bpm * (v.tempo ?? 1));
  const velMul = v.vel ?? 1;
  const beatsPerBar = score.meter[0];
  const swing = score.swing ?? 0;
  const inst = (i: Instrument): Instrument | null => {
    if (!v.swap || !(i in v.swap)) return i;
    return v.swap[i] ?? null;
  };

  const events: Ev[] = [];
  const marks: Mark[] = [];
  let t0 = 0;
  let loopAt = 0;
  const loopFrom = score.loopFrom ?? 0;

  score.form.forEach((key, fi) => {
    const sec = score.sections[key];
    if (!sec) throw new Error(`${score.id}: the form names a missing section ${key}`);
    if (fi === loopFrom) loopAt = t0;
    const beats = sectionBeats(score, sec);
    const bars = Math.round(beats / beatsPerBar);
    marks.push({ t: t0, fi, key, name: sec.name, bars });

    const push = (beat: number, i: Instrument, midi: number, lenBeats: number, vel: number, prio: number, jitter = 0) => {
      const frac = beat % 1;
      const late = swing > 0 && Math.abs(frac - 0.5) < 1e-6 ? swing * spb * 0.5 : 0;
      events.push({ t: t0 + beat * spb + late + jitter, inst: i, midi, dur: lenBeats * spb, vel: Math.max(0.02, Math.min(1, vel * velMul)), fi, prio });
    };

    // ---- the generated accompaniment
    const grid = sec.chords ? chordBars(sec.chords) : [];
    // One entry per chord slice: start beat, length, chord.
    const slices: { beat: number; len: number; ch: Chord | null; bar: number }[] = [];
    grid.forEach((group, bi) => {
      const len = beatsPerBar / group.length;
      group.forEach((ch, gi) => slices.push({ beat: bi * beatsPerBar + gi * len, len, ch, bar: bi }));
    });

    if (sec.pad && !(low && sec.pad.optional)) {
      const i = inst(sec.pad.inst);
      if (i) {
        const center = 12 * ((sec.pad.octave ?? 3) + 1) + 7;
        // A held chord carries over repeated slices: fewer, longer notes.
        for (let k = 0; k < slices.length; k++) {
          const s = slices[k]!;
          if (!s.ch) continue;
          let len = s.len;
          while (k + 1 < slices.length && sameChord(slices[k + 1]!.ch, s.ch)) len += slices[++k]!.len;
          // Low voices every pad chord in three notes (the seventh or ninth goes)
          const tones = voice(low ? { ...s.ch, tones: s.ch.tones.slice(0, 3) } : s.ch, center);
          for (const m of tones) push(s.beat, i, m, len, sec.pad.vel ?? 0.4, 2);
        }
      }
    }

    if (sec.bass) {
      const i = inst(sec.bass.inst);
      if (i) {
        const vel = sec.bass.vel ?? 0.55;
        const first = slices.find((s) => s.ch)?.ch ?? null;
        slices.forEach((s, k) => {
          if (!s.ch) return;
          const root = placeAbove(s.ch.bass, BASS_FLOOR);
          const fifthTone = s.ch.tones.includes(7) ? 7 : s.ch.tones.includes(6) ? 6 : s.ch.tones.includes(8) ? 8 : 7;
          const fifth = root + fifthTone >= BASS_FLOOR + 14 ? root + fifthTone - 12 : root + fifthTone;
          switch (sec.bass!.pattern) {
            case "root":
              push(s.beat, i, root, s.len * 0.96, vel, 1);
              break;
            case "pedal": {
              const p = placeAbove(first!.bass, BASS_FLOOR);
              // once per bar, whatever the changes above it
              if (k === 0 || slices[k - 1]!.bar !== s.bar) push(s.bar * beatsPerBar, i, p, beatsPerBar * 0.96, vel, 1);
              break;
            }
            case "root_fifth": {
              const half = s.len >= 2 ? Math.ceil(s.len / 2 - 1e-9) : s.len;
              push(s.beat, i, root, half * 0.94, vel, 1);
              if (half < s.len) push(s.beat + half, i, fifth, (s.len - half) * 0.94, vel * 0.85, 1);
              break;
            }
            case "walk": {
              const next = slices[k + 1]?.ch ?? first;
              const third = root + (s.ch.tones[1] ?? 4);
              const target = next ? placeAbove(next.bass, BASS_FLOOR) : root;
              const approach = target > fifth ? target - 1 : target + 1;
              const steps = [root, third, fifth, approach];
              const n = Math.max(1, Math.round(s.len));
              const each = s.len / n;
              for (let j = 0; j < n; j++) push(s.beat + j * each, i, steps[j % 4]!, each * 0.9, vel * (j === 0 ? 1 : 0.8), 1);
              break;
            }
          }
        });
      }
    }

    if (sec.arp && !(low && sec.arp.optional)) {
      const i = inst(sec.arp.inst);
      if (i) {
        const pat = sec.arp.pattern.trim().split(/\s+/);
        const unit = sec.arp.unit ?? 0.5;
        const floor = 12 * ((sec.arp.octave ?? 4) + 1);
        const perBar = Math.round(beatsPerBar / unit);
        for (let b = 0; b < grid.length; b++) {
          for (let step = 0; step < perBar; step++) {
            const tok = pat[step % pat.length]!;
            if (tok === ".") continue;
            const k = Number(tok);
            if (!Number.isInteger(k)) throw new Error(`${score.id}: bad arp step ${tok}`);
            const beat = b * beatsPerBar + step * unit;
            const s = slices.find((x) => x.beat <= beat + 1e-9 && beat < x.beat + x.len - 1e-9);
            if (!s?.ch) continue;
            const root = placeAbove(s.ch.root, floor);
            const tones = s.ch.tones;
            const midi = root + tones[k % tones.length]! + 12 * Math.floor(k / tones.length);
            push(beat, i, midi, unit * 2, (sec.arp.vel ?? 0.45) * (step === 0 ? 1.1 : 1), 3);
          }
        }
      }
    }

    // ---- the written parts
    sec.parts.forEach((p: Part, pi) => {
      if (p.only && p.only !== (night ? "night" : "day")) return;
      if (low && p.optional) return;
      const i = inst(p.inst);
      if (!i) return;
      const base = p.vel ?? 0.7;
      for (const n of parseNotes(p.notes, p.unit ?? 1, beatsPerBar)) {
        const h = hash(fi + 1, pi + 1, n.index + 1);
        const jitter = PERCUSSION.has(i) ? 0 : h * 0.008;
        push(n.beat, i, n.midi + 12 * (p.octave ?? 0), n.beats, base * n.accent * (0.95 + 0.1 * hash(pi + 7, n.index, fi)), p.optional ? 3 : 0, jitter);
      }
    });

    t0 += beats * spb;
  });

  events.sort((a, b) => a.t - b.t || a.prio - b.prio);
  return {
    id: score.id,
    night,
    low,
    events,
    marks,
    end: t0,
    loopFrom,
    loopAt,
    bar: beatsPerBar * spb,
    restS: score.restS ?? MUSIC_REST_S,
    space: score.space ?? 2.4,
  };
}

export const PERCUSSION: ReadonlySet<Instrument> = new Set(["frame", "timpani", "shaker"]);

/** Where the next play starts once a play ends: the first event index of the loop section. */
export function loopCursor(c: Compiled): number {
  const i = c.events.findIndex((e) => e.fi >= c.loopFrom);
  return i < 0 ? c.events.length : i;
}

/** The first event index of form entry `fi` (or the end). */
export function cursorAt(c: Compiled, fi: number): number {
  const i = c.events.findIndex((e) => e.fi >= fi);
  return i < 0 ? c.events.length : i;
}
