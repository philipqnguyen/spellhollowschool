// music.lake: wide still water, mist, distance. A slow air in A dorian (the
// raised sixth is the mist lifting): the whistle alone over harp and a low
// string bed, the cello answers, the harp and strings take it in turn. Night:
// harp and celesta, very sparse.

import type { Score } from "../score";

const AIR = "e5:3 d5 | fs5:2 e5 d5 | e5:4 | d5:2 b4 g4 | a4:3 c5 | e5:2 g5 e5 | d5:3 b4 | a4:4";
const AIR_CHORDS = "Am | D | Am | G | F | C | G | Am";

const score: Score = {
  id: "music.lake",
  title: "Mist on the Water",
  style: "folk",
  bpm: 72,
  meter: [4, 4],
  space: 3.4,
  level: 0.4,
  restS: [35, 75],
  sections: {
    intro: {
      name: "Intro: harp ripples",
      chords: "Am | Am",
      parts: [{ inst: "cello", notes: "a2:4 | e3:4", vel: 0.4 }],
      arp: { inst: "harp", pattern: "0 2 4 2 3 2 1 2", vel: 0.36 },
    },
    a: {
      name: "A: the whistle's slow air",
      chords: AIR_CHORDS,
      parts: [{ inst: "whistle", notes: AIR, vel: 0.66 }],
      pad: { inst: "strings", vel: 0.26, octave: 3 },
      bass: { inst: "cello", pattern: "root", vel: 0.36 },
      arp: { inst: "harp", pattern: "0 2 4 2 3 2 1 2", vel: 0.32, optional: true },
    },
    b: {
      name: "B: the cello answers",
      chords: "F | G | Em | Am | Dm | G | C | E",
      parts: [
        { inst: "cello", notes: "a3:2 c4 f4 | d4:3 b3 | e4:2 g4 e4 | c4:4 | d4:2 f4 a4 | g4:3 d4 | e4:2 c4 g3 | gs3:4", vel: 0.66 },
        { inst: "whistle", notes: ". . . . | . . . . | . . . . | . . e5? a5? | . . . . | . . . . | . . . . | b5:2 gs5:2", vel: 0.4, optional: true },
      ],
      pad: { inst: "strings", vel: 0.28, octave: 3 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 3 2 1", vel: 0.3, optional: true },
    },
    a2: {
      name: "A': the harp sings it, the whistle a long way off",
      chords: AIR_CHORDS,
      parts: [
        { inst: "harp", notes: AIR, vel: 0.68 },
        { inst: "whistle", notes: "a5:4 | a5:4 | . . . . | b5:2 g5:2 | c6:4 | . . . . | b5:4 | e5:4", vel: 0.34, optional: true },
      ],
      pad: { inst: "strings", vel: 0.26, octave: 3 },
      bass: { inst: "cello", pattern: "root", vel: 0.36 },
    },
    b2: {
      name: "B': the strings swell",
      chords: "F | G | Em | Am | Dm | G | C | E",
      parts: [
        { inst: "strings", notes: "a4:2 c5 f5 | d5:3 b4 | e5:2 g5 e5 | c5:4 | d5:2 f5 a5 | g5:3 d5 | e5:2 c5 g4 | gs4:4", vel: 0.58 },
        { inst: "cello", notes: "f2:4 | g2:4 | e2:4 | a2:4 | d2:4 | g2:4 | c3:4 | e2:4", vel: 0.42 },
        { inst: "celesta", notes: "a4:2 c5 f5 | d5:3 b4 | e5:2 g5 e5 | c5:4 | d5:2 f5 a5 | g5:3 d5 | e5:2 c5 g4 | gs4:4", vel: 0.5, octave: 1, only: "night" },
      ],
      arp: { inst: "harp", pattern: "0 2 4 2 3 2 1 2", vel: 0.3, optional: true },
    },
    a3: {
      name: "A'': the whistle once more, the mist closing",
      chords: AIR_CHORDS,
      parts: [{ inst: "whistle", notes: AIR, vel: 0.6 }],
      pad: { inst: "strings", vel: 0.24, octave: 3 },
      bass: { inst: "cello", pattern: "root", vel: 0.34 },
      arp: { inst: "harp", pattern: "0 2 4 2 3 2 1 2", vel: 0.3, optional: true },
    },
    end: {
      name: "Ending",
      chords: "F | Am",
      parts: [{ inst: "harp", notes: "c5 e5 a5 c6 | a5:4", vel: 0.5 }],
      pad: { inst: "strings", vel: 0.22, octave: 3 },
      bass: { inst: "cello", pattern: "root", vel: 0.32 },
    },
  },
  form: ["intro", "a", "b", "a2", "b2", "a3", "end"],
  loopFrom: 1,
  night: { tempo: 0.9, vel: 0.9, swap: { whistle: "celesta", strings: null, cello: "harp" } },
};

export default score;
