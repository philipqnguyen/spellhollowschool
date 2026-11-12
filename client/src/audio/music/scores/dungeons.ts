// music.dungeons: cold stone and secrets. A sly minor lilt in G minor (6/8,
// counted in eighths): the bassoon creeps, the clarinet takes it up, a
// furtive turn to E-flat, the cello low over a pedal, celesta glints in the
// dark. Night: the same piece a touch slower, the pizzicato gone.

import type { Score } from "../score";

const CREEP = "g3:2 bf3 d4:2 bf3 | a3:2 g3 fs3:3 | g3:2 c4 ef4:2 c4 | d4:3 .:3 | d4:2 ef4 d4:2 bf3 | c4:2 bf3 g3:3 | a3:2 c4 fs3:2 a3 | g3:3 .:3";

const score: Score = {
  id: "music.dungeons",
  title: "Under the Cold Stone",
  style: "orchestral",
  bpm: 204,
  meter: [6, 8],
  space: 3.2,
  sections: {
    intro: {
      name: "Intro: a drip of celesta over the cello",
      chords: "Gm | Gm | Eb | D",
      parts: [
        { inst: "celesta", notes: ".:3 d6:3 | .:6 | .:3 g5:2 bf5 | a5:3 .:3", vel: 0.42 },
        { inst: "cello", notes: "g2:6 | g2:6 | ef2:6 | d2:6", vel: 0.45 },
      ],
    },
    a: {
      name: "A: the bassoon creeps",
      chords: "Gm | Gm D | Cm | D | Gm | Eb | Cm D | Gm",
      parts: [{ inst: "bassoon", notes: CREEP, vel: 0.72 }],
      bass: { inst: "cello", pattern: "root_fifth", vel: 0.42 },
      arp: { inst: "pizz", pattern: ". 1 2 . 1 2", unit: 1, vel: 0.4, octave: 3, optional: true },
    },
    a2: {
      name: "A': the clarinet takes it, the bassoon below",
      chords: "Gm | Gm D | Cm | D | Gm | Eb | Cm D | Gm",
      parts: [
        { inst: "clarinet", notes: CREEP, vel: 0.66, octave: 1 },
        { inst: "bassoon", notes: "bf2:6 | a2:6 | g2:6 | fs2:6 | bf2:6 | g2:6 | a2:3 fs2:3 | g2:6", vel: 0.45, optional: true },
        { inst: "celesta", notes: ".:6 | .:6 | .:6 | .:3 a5:3 | .:6 | .:6 | .:6 | .:3 d6:2 g5", vel: 0.36, optional: true },
      ],
      bass: { inst: "cello", pattern: "root_fifth", vel: 0.42 },
      arp: { inst: "pizz", pattern: ". 1 2 . 1 2", unit: 1, vel: 0.4, octave: 3, optional: true },
    },
    b: {
      name: "B: a furtive turn to E-flat",
      chords: "Eb | Bb | Cm | G | Ab | Eb | Cm | D7",
      parts: [
        { inst: "clarinet", notes: "g4:3 bf4:3 | f4:3 d4:3 | ef4:2 d4 c4:2 g3 | b3:3 .:3 | c4:3 ef4:3 | bf4:3 g4:3 | af4:2 g4 f4:2 ef4 | d4:3 fs4:3", vel: 0.64 },
        { inst: "pizz", notes: "ef3 .:2 bf2 .:2 | bf2 .:2 f2 .:2 | c3 .:2 g2 .:2 | g2 .:2 d3 .:2 | af2 .:2 ef3 .:2 | ef3 .:2 bf2 .:2 | c3 .:2 g2 .:2 | d3 .:2 a2 .:2", vel: 0.5 },
      ],
      pad: { inst: "cello", vel: 0.26, octave: 3, optional: true },
    },
    c: {
      name: "C: the cello alone in the cellar, celesta answering",
      chords: "Cm | Gm | Ab | D | Cm | Gm | Eb D | Gm",
      parts: [
        { inst: "cello", notes: "c3:3 ef3:3 | d3:2 bf2 g2:3 | af2:3 c3:3 | d3:6 | ef3:2 d3 c3:2 ef3 | d3:3 bf2:3 | g2:2 a2 bf2:2 a2 | g2:6", vel: 0.66 },
        { inst: "celesta", notes: ".:6 | .:3 g5:2 d5 | .:6 | .:3 fs5:2 a5 | .:6 | .:3 bf5:2 g5 | .:6 | .:3 d6:3", vel: 0.4 },
        { inst: "bassoon", notes: "g2:6 | g2:6 | g2:6 | fs2:6 | g2:6 | g2:6 | g2:3 fs2:3 | g2:6", vel: 0.36, optional: true },
      ],
    },
    a3: {
      name: "A'': the bassoon and clarinet together",
      chords: "Gm | Gm D | Cm | D | Gm | Eb | Cm D | Gm",
      parts: [
        { inst: "bassoon", notes: CREEP, vel: 0.62 },
        { inst: "clarinet", notes: CREEP, vel: 0.48, octave: 1, optional: true },
      ],
      bass: { inst: "cello", pattern: "root_fifth", vel: 0.4 },
      arp: { inst: "pizz", pattern: ". 1 2 . 1 2", unit: 1, vel: 0.38, octave: 3, optional: true },
    },
    end: {
      name: "Ending: the drip again",
      chords: "Gm | Eb | D | Gm",
      parts: [
        { inst: "celesta", notes: ".:3 d6:3 | .:3 bf5:2 g5 | fs5:3 a5:3 | g5:6", vel: 0.4 },
        { inst: "cello", notes: "g2:6 | ef2:6 | d2:6 | g2:6", vel: 0.42 },
      ],
    },
  },
  form: ["intro", "a", "a2", "b", "c", "a3", "end"],
  loopFrom: 1,
  night: { tempo: 0.94, vel: 0.85, swap: { pizz: null } },
};

export default score;
