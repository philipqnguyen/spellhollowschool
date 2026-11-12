import { describe, expect, it } from "vitest";
import { FADES, VOICE_CAPS } from "../rules";
import { compile, type Ev } from "./compile";
import { createEngine, START_S } from "./engine";
import type { Score } from "./score";
import { renderSample } from "./samples";
import { midiToFreq } from "./notation";
import { renderNote } from "./voices";

// A stand-in for an AudioContext: every node accepts every call and plays nothing.
function fakeContext(sampleRate = 8000): BaseAudioContext {
  const param = () => {
    const p = { value: 1, setValueAtTime: () => p, linearRampToValueAtTime: () => p, exponentialRampToValueAtTime: () => p, setTargetAtTime: () => p, cancelScheduledValues: () => p };
    return p;
  };
  const node = () => ({
    connect: () => undefined, disconnect: () => undefined, start: () => undefined, stop: () => undefined, setPeriodicWave: () => undefined,
    gain: param(), frequency: param(), detune: param(), Q: param(), pan: param(), playbackRate: param(),
    threshold: param(), knee: param(), ratio: param(), attack: param(), release: param(),
    buffer: null, loop: false, curve: null, normalize: true, type: "", onended: null,
  });
  return {
    sampleRate,
    currentTime: 0,
    createGain: node, createOscillator: node, createBiquadFilter: node, createBufferSource: node, createStereoPanner: node,
    createConvolver: node, createDynamicsCompressor: node, createWaveShaper: node,
    createPeriodicWave: () => ({}),
    createBuffer: (_c: number, length: number, sr: number) => ({ length, sampleRate: sr, duration: length / sr, getChannelData: () => new Float32Array(length) }),
  } as unknown as BaseAudioContext;
}

// Two sections of two bars, 120 bpm (a bar is 2 s): the day plays the flute, the night the clarinet.
const TEST: Score = {
  id: "music.castle",
  title: "Test",
  style: "orchestral",
  bpm: 120,
  meter: [4, 4],
  sections: {
    a: { name: "A", parts: [{ inst: "flute", notes: "c5 d5 e5 f5 | g5:4", only: "day" }, { inst: "clarinet", notes: "c5 d5 e5 f5 | g5:4", only: "night" }] },
    b: { name: "B", parts: [{ inst: "flute", notes: "e5:4 | c5:4", only: "day" }, { inst: "clarinet", notes: "e5:4 | c5:4", only: "night" }] },
  },
  form: ["a", "b"],
  restS: [10, 10],
  night: { vel: 0.8 },
};
const OTHER: Score = { ...TEST, id: "music.lake", title: "Other", sections: { a: { name: "A", parts: [{ inst: "harp", notes: "c4:4 | c4:4" }] } }, form: ["a"] };
const STING: Score = { id: "stinger.level_up", title: "Sting", style: "celesta", bpm: 120, meter: [4, 4], sections: { a: { name: "S", parts: [{ inst: "celesta", notes: "c5 e5 g5 c6" }] } }, form: ["a"] };

function rig() {
  let t = 0;
  const notes: { e: Ev; at: number; take: string }[] = [];
  const lookup = (id: string) => [TEST, OTHER, STING].find((s) => s.id === id);
  const e = createEngine(fakeContext(), {} as AudioNode, "medium", { now: () => t, later: () => undefined, random: () => 0, timer: false, lookup, onNote: (ev, at, take) => notes.push({ e: ev, at, take }) });
  const run = (until: number) => {
    for (; t < until; t += 0.05) e.tick();
  };
  return { e, notes, run, now: () => t };
}

describe("the music engine", () => {
  it("plays the form, rests, then plays again from the loop section", () => {
    const { e, notes, run } = rig();
    e.setCue({ id: "music.castle", night: false }, 0);
    run(30);
    const starts = notes.map((n) => n.at);
    // one play: 8 s; rest 10 s; the second play starts 18 s after the first
    expect(starts[0]).toBeCloseTo(START_S, 1);
    expect(starts.filter((s) => s < 8 + START_S)).toHaveLength(7);
    expect(starts.some((s) => s >= 8 + START_S && s < 18 + START_S - 1e-6)).toBe(false);
    expect(starts.find((s) => s >= 8 + START_S)).toBeCloseTo(18 + START_S, 1);
  });

  it("changes to the night variant at the next section boundary, without a gap", () => {
    const { e, notes, run } = rig();
    e.setCue({ id: "music.castle", night: false }, 0);
    run(1);
    e.setCue({ id: "music.castle", night: true }, 2.5);
    run(8.2);
    // section A (0..4 s) stays the day's flute; B starts on time with the night's clarinet
    expect(notes.filter((n) => n.at < 4 + START_S).every((n) => n.e.inst === "flute")).toBe(true);
    const b = notes.filter((n) => n.at >= 4 + START_S);
    expect(b.length).toBeGreaterThan(0);
    expect(b.every((n) => n.e.inst === "clarinet")).toBe(true);
    expect(b[0]!.at).toBeCloseTo(4 + START_S, 1);
  });

  it("a new piece starts after the gap while the old one fades", () => {
    const { e, notes, run } = rig();
    e.setCue({ id: "music.castle", night: false }, 0);
    run(2);
    e.setCue({ id: "music.lake", night: false }, FADES.musicOut);
    const at = 2;
    run(6);
    const castleAfter = notes.filter((n) => n.take === "music.castle" && n.at > at + 0.45);
    expect(castleAfter).toHaveLength(0);
    const lake = notes.filter((n) => n.take === "music.lake");
    expect(lake[0]!.at).toBeGreaterThanOrEqual(at + FADES.musicGap - 0.06);
    expect(e.stats().cue).toBe("music.lake");
  });

  it("silence on a null cue, and the stats say so", () => {
    const { e, notes, run } = rig();
    e.setCue({ id: "music.castle", night: false }, 0);
    run(1);
    e.setCue(null, 1);
    const n = notes.length;
    run(10);
    expect(notes.filter((x) => x.at > 1 + 0.45)).toHaveLength(0);
    expect(notes.length).toBeLessThanOrEqual(n + 8);
    expect(e.stats().cue).toBeNull();
  });

  it("a stinger plays at once over the loop", () => {
    const { e, notes, run, now } = rig();
    e.setCue({ id: "music.castle", night: false }, 0);
    run(1);
    e.stinger("stinger.level_up");
    const s = notes.filter((n) => n.take === "stinger.level_up");
    expect(s).toHaveLength(4);
    expect(s[0]!.at).toBeCloseTo(now() + 0.05, 2);
  });

  it("thins accompaniment to the cap but never drops a written line", () => {
    const dense: Score = {
      ...TEST,
      sections: { a: { name: "dense", chords: "C | F | G | C", parts: [{ inst: "flute", notes: "c5:.25*16 | c5:.25*16 | d5:.25*16 | e5:.25*16" }], pad: { inst: "strings" }, arp: { inst: "harp", pattern: "0 1 2 3", unit: 0.25 } } },
      form: ["a"],
    };
    let t = 0;
    const notes: Ev[] = [];
    const e = createEngine(fakeContext(), {} as AudioNode, "low", { now: () => t, later: () => undefined, random: () => 0, timer: false, lookup: () => dense, onNote: (ev) => notes.push(ev) });
    e.setCue({ id: "music.castle", night: false }, 0);
    for (; t < 9; t += 0.05) e.tick();
    const written = compile(dense).events.filter((x) => x.prio === 0).length;
    expect(notes.filter((x) => x.prio === 0)).toHaveLength(written);
    expect(notes.length).toBeLessThan(compile(dense).events.length);
    expect(VOICE_CAPS.low.musicNotes).toBeLessThan(VOICE_CAPS.medium.musicNotes);
  });
});

describe("the plucked and struck notes", () => {
  /** The fundamental by autocorrelation over a window after the attack. */
  function pitch(d: Float32Array, sr: number, rate: number, near: number): number {
    const from = Math.floor(sr * 0.05);
    const win = Math.floor(sr * 0.2);
    let best = 0;
    let lag = 0;
    const lo = Math.floor(sr / (near * 1.2));
    const hi = Math.ceil(sr / (near / 1.2));
    for (let l = lo; l <= hi; l++) {
      let s = 0;
      for (let i = from; i < from + win; i++) s += d[i]! * d[i + l]!;
      if (s > best) {
        best = s;
        lag = l;
      }
    }
    // parabolic refinement
    const r = (l: number) => {
      let s = 0;
      for (let i = from; i < from + win; i++) s += d[i]! * d[i + l]!;
      return s;
    };
    const a = r(lag - 1), b = r(lag), c = r(lag + 1);
    const off = (a - c) / (2 * (a - 2 * b + c));
    return (sr / (lag + off)) * rate;
  }

  it("the harp and the pizzicato are in tune", () => {
    for (const inst of ["harp", "pizz"] as const) {
      for (const midi of [43, 55, 67, 79]) {
        const s = renderSample(inst, midi, 44100);
        const f = pitch(s.data, s.sampleRate, s.rate, midiToFreq(midi));
        const cents = 1200 * Math.log2(f / midiToFreq(midi));
        expect(Math.abs(cents), `${inst} ${midi}: ${cents.toFixed(1)} cents`).toBeLessThan(8);
      }
    }
  });

  it("every note is finite, peaks under full scale and ends in silence", () => {
    for (const inst of ["harp", "pizz", "celesta", "musicbox", "glock", "timpani", "frame", "shaker"] as const) {
      for (const midi of [45, 60, 76, 88]) {
        const s = renderSample(inst, midi, 22050);
        // One check for the whole buffer: an expect per sample made this test slow enough to time out under load.
        let peak = 0;
        let finite = true;
        for (const x of s.data) {
          if (!Number.isFinite(x)) finite = false;
          peak = Math.max(peak, Math.abs(x));
        }
        expect(finite, `${inst} ${midi}`).toBe(true);
        expect(peak, `${inst} ${midi}`).toBeGreaterThan(0.1);
        expect(peak, `${inst} ${midi}`).toBeLessThanOrEqual(0.71);
        expect(Math.abs(s.data[s.data.length - 1]!)).toBeLessThan(1e-3);
      }
    }
  });
});

describe("the sustained notes", () => {
  const SUSTAINED = ["flute", "whistle", "clarinet", "oboe", "bassoon", "fiddle", "strings", "cello", "bass", "horn", "choir"] as const;
  const RANGE: Record<(typeof SUSTAINED)[number], number[]> = {
    flute: [62, 74, 86], whistle: [74, 86, 93], clarinet: [52, 64, 76], oboe: [60, 72, 84], bassoon: [36, 48, 60], fiddle: [55, 67, 81],
    strings: [43, 60, 79], cello: [36, 48, 62], bass: [28, 38, 48], horn: [45, 57, 69], choir: [50, 60, 72],
  };

  it("loop without a seam: the step across the loop point is no bigger than any other step in the loop", () => {
    for (const inst of SUSTAINED) {
      for (const midi of RANGE[inst]) {
        const s = renderNote(inst, midi, 32000);
        expect(s.loopStart, inst).toBeDefined();
        const a = Math.round(s.loopStart! * s.sampleRate);
        const b = Math.round(s.loopEnd! * s.sampleRate);
        expect(b).toBeLessThanOrEqual(s.data.length);
        let biggest = 0;
        for (let i = a; i < b - 1; i++) biggest = Math.max(biggest, Math.abs(s.data[i + 1]! - s.data[i]!));
        const seam = Math.abs(s.data[a]! - s.data[b - 1]!);
        expect(seam, `${inst} ${midi}`).toBeLessThanOrEqual(biggest * 1.2 + 1e-4);
        // and the loop's end is what led into its start the first time (no dip or swell at the loop)
        const rms = (from: number, n: number) => Math.sqrt(s.data.slice(from, from + n).reduce((x, y) => x + y * y, 0) / n);
        const w = Math.floor(s.sampleRate * 0.05);
        expect(rms(b - w, w) / rms(a - w, w), `${inst} ${midi}`).toBeGreaterThan(0.85);
        expect(rms(b - w, w) / rms(a - w, w), `${inst} ${midi}`).toBeLessThan(1.18);
      }
    }
  });

  it("are in tune", () => {
    for (const inst of SUSTAINED) {
      for (const midi of RANGE[inst]) {
        const s = renderNote(inst, midi, 32000);
        const a = Math.round(s.loopStart! * s.sampleRate);
        const d = s.data.slice(a, Math.round(s.loopEnd! * s.sampleRate));
        const f0 = midiToFreq(midi);
        // autocorrelation round the whole loop (it repeats, so the lag wraps): the vibrato averages out
        const win = d.length;
        let best = -Infinity;
        let lag = 0;
        for (let l = Math.floor(s.sampleRate / (f0 * 1.06)); l <= Math.ceil(s.sampleRate / (f0 / 1.06)); l++) {
          let x = 0;
          for (let i = 0; i < win; i++) x += d[i]! * d[(i + l) % win]!;
          if (x > best) {
            best = x;
            lag = l;
          }
        }
        const cents = 1200 * Math.log2(s.sampleRate / lag / f0);
        // a whole-sample lag resolves to within a few cents at these pitches; vibrato averages out
        expect(Math.abs(cents), `${inst} ${midi}: ${cents.toFixed(1)} cents`).toBeLessThan(Math.max(10, 1200 * Math.log2((lag + 1) / lag)));
      }
    }
  });
});
