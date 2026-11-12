// music.great_hall: the heart of the school. A stately, candlelit dance in D
// major (3/4): a horn call, the tune on flute, the horn in B minor, the strings
// take the tune, an oboe interlude, all together to close. Night: no timpani,
// softer, a celesta descant over the strings' turn.

import type { Score } from "../score";

const TUNE = "fs4:2 a4 | b4:2 d5 | a4:1.5 fs4:.5 g4 | e4:3 | d5:2 cs5 | b4 a4 g4 | g4:1.5 fs4:.5 e4 | e4:2 a4";
const TUNE_HOME = "fs4:2 a4 | b4:2 d5 | a4:1.5 fs4:.5 g4 | e4:3 | d5:2 fs5 | e5:1.5 d5:.5 b4 | a4:1.5 b4:.5 cs5 | d5:3";

const score: Score = {
  id: "music.great_hall",
  title: "The Long Tables",
  style: "orchestral",
  bpm: 90,
  meter: [3, 4],
  space: 3.0,
  level: -0.6,
  sections: {
    intro: {
      name: "Intro: the horn calls the hall in",
      chords: "D | D | G | A",
      parts: [
        { inst: "horn", notes: "d4:2 a3 | d4 fs4 a4 | g4:3 | e4:3", vel: 0.62 },
        { inst: "timpani", notes: "d3:3 | . . . | . . . | a2:3", vel: 0.45 },
      ],
      pad: { inst: "strings", vel: 0.32 },
      bass: { inst: "strings", pattern: "root", vel: 0.4 },
    },
    a: {
      name: "A: the tune on flute",
      chords: "D | G | D/F# | A | Bm | G | Em | A",
      parts: [
        { inst: "flute", notes: TUNE, vel: 0.72 },
        { inst: "timpani", notes: "d3 . . | . . . | . . . | a2 . . | . . . | . . . | . . . | a2 . a2?", vel: 0.4, optional: true },
      ],
      pad: { inst: "strings", vel: 0.3 },
      bass: { inst: "strings", pattern: "root", vel: 0.42 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1", vel: 0.36, optional: true },
    },
    a2: {
      name: "A': the flute again, the oboe answers",
      chords: "D | G | D/F# | A | Bm | G | A | D",
      parts: [
        { inst: "flute", notes: TUNE_HOME, vel: 0.72 },
        { inst: "oboe", notes: ". . . | . . . | . . . | cs5:1.5 d5:.5 e5 | . . . | . . . | . . . | fs4:2 a4", vel: 0.48, optional: true },
      ],
      pad: { inst: "strings", vel: 0.3 },
      bass: { inst: "strings", pattern: "root_fifth", vel: 0.42 },
      arp: { inst: "harp", pattern: "0 1 2 1 2 1", vel: 0.34, optional: true },
    },
    b: {
      name: "B: the horn, in B minor",
      chords: "Bm | F#m | G | D | Em | Bm | G | A",
      parts: [
        { inst: "horn", notes: "b4:2 d5 | cs5:2 a4 | b4 d5 g5 | fs5:3 | e5:2 g5 | fs5:1.5 e5:.5 d5 | d5 e5 g5 | e5:3", vel: 0.68, octave: -1 },
        { inst: "flute", notes: ". . . | . . . | . . . | a5? fs5? d5? | . . . | . . . | . . . | cs5? e5? a5?", vel: 0.42, optional: true },
      ],
      pad: { inst: "strings", vel: 0.34 },
      bass: { inst: "strings", pattern: "root", vel: 0.45 },
      arp: { inst: "harp", pattern: "0 2 1 2 0 2", vel: 0.32, optional: true },
    },
    a3: {
      name: "A'': the strings take the tune",
      chords: "D | G | D/F# | A | Bm | G | Em | A",
      parts: [
        { inst: "strings", notes: TUNE, vel: 0.6, octave: 1 },
        { inst: "horn", notes: "a3:3 | b3:3 | a3:3 | a3:3 | b3:3 | b3:3 | b3:3 | cs4:3", vel: 0.4, optional: true },
        { inst: "celesta", notes: ". . . | d6 b5 g5 | . . . | cs6 a5 e5 | . . . | d6 b5 g5 | . . . | e6 cs6 a5", vel: 0.45, only: "night" },
      ],
      pad: { inst: "strings", vel: 0.26, octave: 3 },
      bass: { inst: "strings", pattern: "root", vel: 0.42 },
    },
    c: {
      name: "C: an oboe interlude",
      chords: "G | A | F#m | Bm | Em | A | D | D",
      parts: [{ inst: "oboe", notes: "d5:1.5 e5:.5 d5 | cs5:2 a4 | fs5:1.5 e5:.5 cs5 | d5:3 | g4:1.5 a4:.5 b4 | cs5:2 e5 | d5:2 a4 | fs4:3", vel: 0.66 }],
      pad: { inst: "strings", vel: 0.28 },
      bass: { inst: "strings", pattern: "root", vel: 0.38 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 3", vel: 0.36 },
    },
    a4: {
      name: "A''': all together",
      chords: "D | G | D/F# | A | Bm | G | A | D",
      parts: [
        { inst: "flute", notes: TUNE_HOME, vel: 0.7, octave: 1 },
        { inst: "strings", notes: TUNE_HOME, vel: 0.5 },
        { inst: "timpani", notes: "d3 . . | . . . | . . . | a2 . . | . . . | . . . | a2 . a2 | d3 . .", vel: 0.45, optional: true },
      ],
      pad: { inst: "strings", vel: 0.28 },
      bass: { inst: "strings", pattern: "root", vel: 0.45 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1", vel: 0.32, optional: true },
    },
    end: {
      name: "Ending",
      chords: "G | A7 | D | D",
      parts: [
        { inst: "horn", notes: "b3:2 d4 | cs4:2 e4 | d4:3 | . . .", vel: 0.55 },
        { inst: "timpani", notes: ". . . | . . . | d3:.25*12 | d3 . .", vel: 0.32 },
      ],
      pad: { inst: "strings", vel: 0.3 },
      bass: { inst: "strings", pattern: "root", vel: 0.4 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 5", vel: 0.3 },
    },
  },
  form: ["intro", "a", "a2", "b", "a3", "c", "a4", "end"],
  loopFrom: 1,
  night: { tempo: 0.92, vel: 0.78, swap: { timpani: null } },
};

export default score;
