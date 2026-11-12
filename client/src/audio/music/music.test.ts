import { describe, expect, it } from "vitest";
import { SOUNDS } from "../catalogue";
import { VOICE_CAPS } from "../rules";
import { MUSIC_BRIEF } from "./brief";
import { compile, pulseBpm, sectionBeats, type Compiled } from "./compile";
import { SCORES } from "./library";
import { imitates, pitchesOf } from "./motifs";
import { chordBars, measure, parseChord, parseNotes } from "./notation";
import { noteLife } from "./voices";

const musicIds = SOUNDS.filter((s) => s.group === "music").map((s) => s.id);
const stingerIds = SOUNDS.filter((s) => s.group === "stingers").map((s) => s.id);
const pieces = musicIds.map((id) => SCORES.get(id)!).filter(Boolean);
const all = [...SCORES.values()];

/** Most notes sounding at once (each for its voice's life), of those with a priority up to `prio`. */
function peakNotes(c: Compiled, prio = 3): number {
  const edges: [number, number][] = [];
  for (const e of c.events) if (e.prio <= prio) edges.push([e.t, 1], [e.t + noteLife(e.inst, e.dur), -1]);
  edges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let n = 0;
  let peak = 0;
  for (const [, d] of edges) peak = Math.max(peak, (n += d));
  return peak;
}

describe("the notation", () => {
  it("reads notes, rests, holds, chords, accents and repeats", () => {
    const n = parseNotes("c4 e4:2 . g4+c5 ~:2 a4!:.5*3 bf3?", 1, 4);
    expect(n.map((x) => [x.beat, x.midi, x.beats])).toEqual([
      [0, 60, 1], [1, 64, 2], [4, 67, 3], [4, 72, 3], [7, 69, 0.5], [7.5, 69, 0.5], [8, 69, 0.5], [8.5, 58, 1],
    ]);
    expect(n[4]!.accent).toBeGreaterThan(1);
    expect(n[7]!.accent).toBeLessThan(1);
    expect(measure("c4 e4:2 . | g4:.5*2", 1)).toBe(5);
  });

  it("checks bars in strict mode", () => {
    expect(() => parseNotes("c4 d4 e4 | f4", 1, 4, true)).toThrow(/bar 1/);
    expect(() => parseNotes("c4 d4 e4 f4 | g4:4 |", 1, 4, true)).not.toThrow();
  });

  it("reads chords with qualities and slash basses", () => {
    expect(parseChord("F#m7")).toEqual({ root: 6, bass: 6, tones: [0, 3, 7, 10] });
    expect(parseChord("G/B")).toEqual({ root: 7, bass: 11, tones: [0, 4, 7] });
    expect(parseChord("Bbmaj7")!.root).toBe(10);
    expect(parseChord("_")).toBeNull();
    expect(chordBars("C | Am F | G").map((b) => b.length)).toEqual([1, 2, 1]);
  });
});

describe("the pieces", () => {
  it("every music and stinger id has exactly one score", () => {
    expect([...SCORES.keys()].sort()).toEqual([...musicIds, ...stingerIds].sort());
  });

  it("every bar of every part adds up to the meter, and every part fills its section", () => {
    for (const s of all) {
      for (const [key, sec] of Object.entries(s.sections)) {
        const beats = sectionBeats(s, sec);
        expect(beats, `${s.id} ${key}`).toBeGreaterThan(0);
        expect(beats % s.meter[0], `${s.id} ${key}: whole bars`).toBe(0);
        sec.parts.forEach((p, i) => {
          const where = `${s.id} ${key} part ${i} (${p.inst})`;
          expect(() => parseNotes(p.notes, p.unit ?? 1, s.meter[0], true), where).not.toThrow();
          expect(measure(p.notes, p.unit ?? 1), where).toBeCloseTo(beats, 6);
        });
      }
    }
  });

  it("every piece has a night variant that is softer or sparer, with the same sections", () => {
    for (const s of pieces) {
      expect(s.night, `${s.id} has no night variant`).toBeDefined();
      const day = compile(s);
      const night = compile(s, { night: true });
      expect(night.marks.map((m) => m.bars)).toEqual(day.marks.map((m) => m.bars));
      const changed =
        night.end !== day.end ||
        night.events.length !== day.events.length ||
        night.events.some((e, i) => e.inst !== day.events[i]!.inst || e.vel !== day.events[i]!.vel);
      expect(changed, `${s.id}: the night variant changes nothing`).toBe(true);
      const loud = (c: Compiled) => c.events.reduce((a, e) => a + e.vel, 0) / c.end;
      expect(loud(night), `${s.id}: night is not softer`).toBeLessThanOrEqual(loud(day) * 1.02);
    }
  });

  it("loops are seamless: whole bars, nothing past the end, the loop section is where the next play starts", () => {
    for (const s of all) {
      for (const night of [false, true]) {
        const c = compile(s, { night });
        const total = c.marks.reduce((a, m) => a + m.bars, 0) * c.bar;
        expect(c.end, s.id).toBeCloseTo(total, 6);
        for (let i = 1; i < c.marks.length; i++) expect(c.marks[i]!.t).toBeCloseTo(c.marks[i - 1]!.t + c.marks[i - 1]!.bars * c.bar, 6);
        expect(c.events.every((e) => e.t >= 0 && e.t < c.end), `${s.id}: a note outside the form`).toBe(true);
        // every note starts inside its own section
        for (const e of c.events) {
          const m = c.marks[e.fi]!;
          expect(e.t >= m.t - 1e-9 && e.t < m.t + m.bars * c.bar, `${s.id}: a note outside section ${m.key}`).toBe(true);
        }
        expect(c.loopAt).toBeCloseTo(c.marks[c.loopFrom]!.t, 9);
        // the next play starts on a downbeat of a written section with notes in its first bar
        expect(c.events.some((e) => e.fi === c.loopFrom && e.t < c.loopAt + c.bar), `${s.id}: the loop section starts silent`).toBe(true);
      }
    }
  });

  it("each piece keeps to its brief: tempo, length and palette", () => {
    for (const s of pieces) {
      const b = MUSIC_BRIEF[s.id as keyof typeof MUSIC_BRIEF];
      expect(s.style, s.id).toBe(b.style);
      const bpm = pulseBpm(s);
      expect(bpm, `${s.id} tempo`).toBeGreaterThanOrEqual(b.bpm[0]);
      expect(bpm, `${s.id} tempo`).toBeLessThanOrEqual(b.bpm[1]);
      const day = compile(s);
      expect(day.end, `${s.id} length`).toBeGreaterThanOrEqual(b.lengthS[0] * 0.9);
      expect(day.end, `${s.id} length`).toBeLessThanOrEqual(b.lengthS[1] * 1.1);
      for (const e of day.events) expect(b.palette, `${s.id} uses ${e.inst}`).toContain(e.inst);
    }
  });

  it("stingers are short phrases", () => {
    for (const id of stingerIds) {
      const c = compile(SCORES.get(id)!);
      expect(c.end, id).toBeGreaterThanOrEqual(1.8);
      expect(c.end, id).toBeLessThanOrEqual(6.5);
      expect(c.marks.length).toBe(1);
    }
  });

  it("stays under the note cap: everything on Medium; on Low the cap only ever thins pads and arpeggios", () => {
    for (const s of pieces) {
      for (const night of [false, true]) {
        const tag = `${s.id}${night ? " night" : ""}`;
        expect(peakNotes(compile(s, { night })), `${tag} on Medium`).toBeLessThanOrEqual(VOICE_CAPS.medium.musicNotes);
        // the written lines and the bass fit under Low's cap, so it never drops a tune note or a bass note
        expect(peakNotes(compile(s, { night, low: true }), 1), `${tag} on Low`).toBeLessThanOrEqual(VOICE_CAPS.low.musicNotes);
      }
    }
  });
});

describe("originality", () => {
  it("knows the famous openings in any key", () => {
    // the Imperial March's opening, transposed and with repeated notes
    expect(imitates(pitchesOf("a3 a3 a3 f3 c4 a3 f3 c4 a3"))?.tune).toBe("The Imperial March");
    expect(imitates(pitchesOf("f4 f5 e5 c5 d5 e5 f5"))?.tune).toBe("Over the Rainbow");
    // a close imitation: one interval a semitone off, no exact run of six
    expect(imitates(pitchesOf("g3 c4 g4 f4 ef4 df4 b4 fs4"))).toEqual({ tune: "Star Wars (Main Title)", at: 0, how: "close" });
    expect(imitates(pitchesOf("c4 d4 e4 f4 g4 a4 b4 c5"))).toBeNull();
  });

  it("no melodic line in any score quotes or closely imitates a famous theme", () => {
    for (const s of all) {
      for (const [key, sec] of Object.entries(s.sections)) {
        for (const p of sec.parts) {
          const line = pitchesOf(p.notes, p.octave ?? 0);
          const hit = imitates(line);
          expect(hit, `${s.id} ${key} (${p.inst}) ${hit?.how}ly follows ${hit?.tune} at note ${hit?.at}`).toBeNull();
        }
      }
    }
  });

  it("no two pieces share an opening (each place has its own tune)", () => {
    const openings = new Map<string, string>();
    for (const s of pieces) {
      const first = s.sections[s.form[s.loopFrom ?? 0]!]!;
      const lead = first.parts.find((p) => !p.optional && !p.only);
      if (!lead) continue;
      const key = pitchesOf(lead.notes).slice(0, 6).map((m, i, a) => (i ? m - a[i - 1]! : 0)).join(",");
      expect(openings.get(key), `${s.id} opens like ${openings.get(key)}`).toBeUndefined();
      openings.set(key, s.id);
    }
  });
});
