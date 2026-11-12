// music.dorm: a music box on the bedside table. A lullaby in B-flat major
// (3/4): the music box sings it, the celesta takes the middle strain over a
// harp, the box comes back. Night: the music box alone over a faint string pad.

import type { Score } from "../score";

const LULLABY = "f5:2 d5 | ef5:2 g5 | f5 d5 bf4 | c5:3 | d5:2 g5 | f5 ef5 c5 | d5:1.5 ef5:.5 c5 | bf4:3";
const LULLABY_CHORDS = "Bb | Eb | Bb | F | Gm | Eb | F | Bb";

const score: Score = {
  id: "music.dorm",
  title: "Lantern Out",
  style: "celesta",
  bpm: 69,
  meter: [3, 4],
  space: 2.2,
  level: 1.6,
  restS: [35, 80],
  sections: {
    intro: {
      name: "Intro: the box is wound",
      chords: "Bb | F",
      parts: [{ inst: "musicbox", notes: "bf5 d6 f6 | a5:2 c6", vel: 0.45 }],
      pad: { inst: "strings", vel: 0.2, octave: 3 },
    },
    a: {
      name: "A: the music box",
      chords: LULLABY_CHORDS,
      parts: [{ inst: "musicbox", notes: LULLABY, vel: 0.66, octave: 1 }],
      pad: { inst: "strings", vel: 0.2, octave: 3 },
      arp: { inst: "harp", pattern: "0 2 1 2 1 2", vel: 0.3, octave: 3, optional: true },
    },
    a2: {
      name: "A': the box again, a celesta echo",
      chords: LULLABY_CHORDS,
      parts: [
        { inst: "musicbox", notes: LULLABY, vel: 0.62, octave: 1 },
        { inst: "celesta", notes: ". . . | . . bf5 | . . . | . . a5 | . . . | . . g5 | . . . | . d5 f5", vel: 0.4, optional: true },
      ],
      pad: { inst: "strings", vel: 0.2, octave: 3 },
      arp: { inst: "harp", pattern: "0 2 1 2 1 2", vel: 0.3, octave: 3, optional: true },
    },
    b: {
      name: "B: the celesta's middle strain",
      chords: "Gm | Dm | Eb | Bb | Cm | Gm | Eb | F7",
      parts: [{ inst: "celesta", notes: "bf5:2 a5 | f5:3 | g5:2 ef5 | d5:3 | ef5:2 g5 | bf5:2 d5 | ef5 g5 bf5 | a5:2 c6", vel: 0.62 }],
      pad: { inst: "strings", vel: 0.22, octave: 3 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1", vel: 0.32, octave: 3 },
    },
    a3: {
      name: "A'': the box, slowing",
      chords: LULLABY_CHORDS,
      parts: [{ inst: "musicbox", notes: LULLABY, vel: 0.58, octave: 1 }],
      pad: { inst: "strings", vel: 0.2, octave: 3 },
      arp: { inst: "harp", pattern: "0 2 1 2 1 2", vel: 0.28, octave: 3, optional: true },
    },
    end: {
      name: "Ending: the spring runs down",
      chords: "Eb | Bb",
      parts: [{ inst: "musicbox", notes: "g5 ef5 c5 | bf4:3", vel: 0.46, octave: 1 }],
      pad: { inst: "strings", vel: 0.18, octave: 3 },
    },
  },
  form: ["intro", "a", "a2", "b", "a3", "end"],
  loopFrom: 1,
  night: { tempo: 0.92, vel: 0.85, swap: { celesta: "musicbox", harp: null } },
};

export default score;
