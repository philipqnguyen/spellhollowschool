// music.castle: the halls, corridors and stairs. Gentle orchestral in F:
// a curious walking tune on flute, taken up by the clarinet, a warm horn
// middle in D minor, the strings bring it home. Night: celesta and strings,
// slower and hushed.

import type { Score } from "../score";

const A = "c5:1.5 a4:.5 f4 g4 | a4:1.5 g4:.5 a4 c5 | d5:2 c5 bf4 | g4:3 . | f5:1.5 e5:.5 d5 a4 | bf4:1.5 a4:.5 bf4 d5 | g5:1.5 f5:.5 d5 bf4 | c5:3 .";
const A_HOME = "c5:1.5 a4:.5 f4 g4 | a4:1.5 g4:.5 a4 c5 | d5:2 c5 bf4 | g4:3 . | f5:1.5 e5:.5 d5 a4 | g4:1.5 a4:.5 bf4 e4 | f4:3 . | . . . .";

const score: Score = {
  id: "music.castle",
  title: "Corridors of Candlelight",
  style: "orchestral",
  bpm: 84,
  meter: [4, 4],
  space: 2.6,
  level: -0.4,
  sections: {
    intro: {
      name: "Intro: harp and strings",
      chords: "F | Bb/F | F | C7",
      parts: [{ inst: "clarinet", notes: ". . . . | . . . . | . . . . | . . g4 bf4", vel: 0.5 }],
      pad: { inst: "strings", vel: 0.35 },
      bass: { inst: "cello", pattern: "root", vel: 0.4 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1 2 1", vel: 0.4 },
    },
    a: {
      name: "A: the walking tune on flute",
      chords: "F | Am | Bb | C | Dm | Bb | Gm | C",
      parts: [{ inst: "flute", notes: A, vel: 0.72 }],
      pad: { inst: "strings", vel: 0.32 },
      bass: { inst: "cello", pattern: "root", vel: 0.45 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1 2 1", vel: 0.38, optional: true },
    },
    a2: {
      name: "A': the clarinet takes it, the flute answers",
      chords: "F | Am | Bb | C | Dm | Gm C7 | F | F",
      parts: [
        { inst: "clarinet", notes: A_HOME, vel: 0.7 },
        { inst: "flute", notes: ". . . . | . . . . | . . . . | . . c5 e5 | . . . . | . . . . | . . a5 g5 | f5:2 e5 c5", vel: 0.5, optional: true },
      ],
      pad: { inst: "strings", vel: 0.32 },
      bass: { inst: "cello", pattern: "root_fifth", vel: 0.45 },
      arp: { inst: "harp", pattern: "0 1 2 1 0 1 2 1", vel: 0.36, optional: true },
    },
    b: {
      name: "B: the horn, in D minor",
      chords: "Dm | Bb | Gm | C | Am | Dm | Gm7 | C7",
      parts: [
        { inst: "horn", notes: "d4:2 f4 a4 | bf4:2 a4 g4 | g4:3 d4 | e4:2 g4 c5 | c5:1.5 b4:.5 a4 e4 | f4:2 a4 d5 | d5:1.5 c5:.5 bf4 a4 | g4:3 .", vel: 0.7 },
        { inst: "clarinet", notes: ". . . . | . . f5? d5? | . . . . | . . . . | . . . . | . . . . | . . . . | . . bf4 c5", vel: 0.45, optional: true },
      ],
      pad: { inst: "strings", vel: 0.36 },
      bass: { inst: "cello", pattern: "root", vel: 0.5 },
      arp: { inst: "harp", pattern: "0 2 1 2 0 2 1 2", vel: 0.32, optional: true },
    },
    a3: {
      name: "A'': the strings bring it home",
      chords: "F | Am | Bb | C | Dm | Gm C7 | F | F",
      parts: [
        { inst: "strings", notes: A_HOME, vel: 0.62, octave: 0 },
        { inst: "flute", notes: A_HOME, vel: 0.4, octave: 1, optional: true },
      ],
      pad: { inst: "strings", vel: 0.28, octave: 3 },
      bass: { inst: "cello", pattern: "root", vel: 0.45 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1 2 1", vel: 0.36, optional: true },
    },
    coda: {
      name: "Coda",
      chords: "Bbmaj7 | Gm7 C7 | F | F",
      parts: [{ inst: "flute", notes: "d5:2 c5 a4 | bf4:2 g4:2 | a4:4 | . . . .", vel: 0.55 }],
      pad: { inst: "strings", vel: 0.3 },
      bass: { inst: "cello", pattern: "root", vel: 0.4 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 5 . .", vel: 0.34 },
    },
  },
  form: ["intro", "a", "a2", "b", "a3", "coda"],
  loopFrom: 1,
  night: { tempo: 0.88, vel: 0.85, swap: { flute: "celesta", clarinet: "celesta", horn: "strings", harp: null } },
};

export default score;
