// music.grounds: open air and green hills, the courtyards and the pitch too.
// A walking tune in D, folk-style: two eight-bar strains, each played twice
// (AABB), the whistle, fiddle and flute taking turns over harp, bass and a
// bodhran-like frame drum. Night: a slow air, harp and flute alone.

import type { Score } from "../score";

const TUNE_A = "a4 d5 d5:.5 e5:.5 fs5 | e5 d5 b4 a4 | b4 d5 g5:.5 fs5:.5 e5 | fs5 e5 cs5 a4 | a4 d5 d5:.5 e5:.5 fs5 | g5 fs5 e5:.5 d5:.5 b4 | a4 fs5 e5:.5 d5:.5 cs5 | d5:3 .";
const TUNE_B = "b4 d5 g5 g5:.5 a5:.5 | fs5 d5 a4 d5 | e5 g5 b5:.5 a5:.5 g5 | fs5:2 e5 cs5 | d5 fs5 b5 a5 | g5 e5 d5:.5 e5:.5 g5 | fs5 e5 d5 cs5 | d5:2 a4:2";
const CHORDS_A = "D | D | G | A | D | G | A | D";
const CHORDS_B = "G | D | Em | A | Bm | G | A | D";
const DRUM = "d2! a3? d2 a3? | d2! a3? d2 a3:.5 a3?:.5 | d2! a3? d2 a3? | d2! a3? d2 a3:.5 a3?:.5 | d2! a3? d2 a3? | d2! a3? d2 a3:.5 a3?:.5 | d2! a3? d2 a3? | d2! a3? d2:.5 d2:.5 a3";

const score: Score = {
  id: "music.grounds",
  title: "Over the Green Hills",
  style: "folk",
  bpm: 100,
  meter: [4, 4],
  swing: 0.18,
  space: 1.8,
  restS: [30, 70],
  sections: {
    intro: {
      name: "Intro: harp and drum",
      chords: "D | A",
      parts: [{ inst: "frame", notes: "d2! a3? d2 a3? | d2! a3? d2:.5 d2:.5 a3", vel: 0.45 }],
      arp: { inst: "harp", pattern: "0 1 2 1 3 1 2 1", vel: 0.42 },
      bass: { inst: "bass", pattern: "root", vel: 0.45 },
    },
    a1: {
      name: "A: the whistle",
      chords: CHORDS_A,
      parts: [
        { inst: "whistle", notes: TUNE_A, vel: 0.72 },
        { inst: "frame", notes: DRUM, vel: 0.42, optional: true },
      ],
      arp: { inst: "harp", pattern: "0 1 2 1 0 1 2 1", vel: 0.36, optional: true },
      bass: { inst: "bass", pattern: "root_fifth", vel: 0.48 },
    },
    a2: {
      name: "A again: the fiddle, the whistle a third above",
      chords: CHORDS_A,
      parts: [
        { inst: "fiddle", notes: TUNE_A, vel: 0.7 },
        { inst: "flute", notes: "fs5:2 a5:2 | g5:2 fs5:2 | g5:2 b5:2 | a5:2 e5:2 | fs5:2 a5:2 | b5:2 g5:2 | a5:2 g5:2 | fs5:3 .", vel: 0.4, optional: true },
        { inst: "frame", notes: DRUM, vel: 0.45, optional: true },
      ],
      arp: { inst: "harp", pattern: "0 1 2 1 0 1 2 1", vel: 0.34, optional: true },
      bass: { inst: "bass", pattern: "root_fifth", vel: 0.48 },
    },
    b1: {
      name: "B: the flute",
      chords: CHORDS_B,
      parts: [
        { inst: "flute", notes: TUNE_B, vel: 0.72 },
        { inst: "frame", notes: DRUM, vel: 0.4, optional: true },
      ],
      arp: { inst: "harp", pattern: "0 2 1 2 0 2 1 2", vel: 0.36, optional: true },
      bass: { inst: "bass", pattern: "root_fifth", vel: 0.46 },
    },
    b2: {
      name: "B again: fiddle and flute together",
      chords: CHORDS_B,
      parts: [
        { inst: "fiddle", notes: TUNE_B, vel: 0.66 },
        { inst: "flute", notes: TUNE_B, vel: 0.46, optional: true },
        { inst: "frame", notes: DRUM, vel: 0.46, optional: true },
      ],
      arp: { inst: "harp", pattern: "0 1 2 3 2 1 2 1", vel: 0.34, optional: true },
      bass: { inst: "bass", pattern: "root_fifth", vel: 0.48 },
    },
    a3: {
      name: "A on the harp, the whistle answering",
      chords: CHORDS_A,
      parts: [
        { inst: "harp", notes: TUNE_A, vel: 0.7 },
        { inst: "whistle", notes: ". . . . | . . e5:.5 fs5:.5 a5 | . . . . | . . e5:.5 fs5:.5 a5 | . . . . | . . d5:.5 e5:.5 fs5 | . . . . | . . . .", vel: 0.46 },
      ],
      bass: { inst: "bass", pattern: "root", vel: 0.42 },
    },
    b3: {
      name: "B to finish: everyone",
      chords: CHORDS_B,
      parts: [
        { inst: "whistle", notes: TUNE_B, vel: 0.62 },
        { inst: "fiddle", notes: TUNE_B, vel: 0.6, octave: -1 },
        { inst: "frame", notes: DRUM, vel: 0.48, optional: true },
      ],
      arp: { inst: "harp", pattern: "0 1 2 1 0 1 2 1", vel: 0.34, optional: true },
      bass: { inst: "bass", pattern: "root_fifth", vel: 0.5 },
    },
    end: {
      name: "Ending",
      chords: "G A | D",
      parts: [
        { inst: "fiddle", notes: "b4 cs5 d5:2 | d5:4", vel: 0.55 },
        { inst: "frame", notes: "d2 a3? d2 a3 | d2! . . .", vel: 0.45 },
      ],
      arp: { inst: "harp", pattern: "0 1 2 3 4 5 6 7", vel: 0.34 },
      bass: { inst: "bass", pattern: "root", vel: 0.45 },
    },
  },
  form: ["intro", "a1", "a2", "b1", "b2", "a3", "b3", "end"],
  loopFrom: 1,
  night: { tempo: 0.85, vel: 0.8, swap: { fiddle: "flute", whistle: "flute", frame: null, bass: "harp" } },
};

export default score;
