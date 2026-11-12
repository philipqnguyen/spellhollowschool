// music.common_room: fireside, friends, home (the kitchens borrow it too). A
// hearthside waltz in G that visits each house in turn, every one with its
// own small character between returns of the shared refrain:
//   Roarwing     bold and warm: the fiddle leaps up the G major chord
//   Burrowsby    homely, in C: flute over a gently strummed harp
//   Skytalon     airy, in A minor: a high flute and open harp patterns
//   Serpentyne   cool and elegant, in D minor: the cello over pizzicato
// Night: a lullaby tempo, harp and flute, quieter.

import type { Score } from "../score";

const REFRAIN = "e5:2 g5 | d5:2 b4 | c5 e5 a5 | fs5:3 | e5:1.5 fs5:.5 g5 | d5:1.5 c5:.5 b4 | c5 b4 a4 | g4:3";
const REFRAIN_CHORDS = "C | G | Am | D | C | G | D7 | G";

const score: Score = {
  id: "music.common_room",
  title: "By the Hearth",
  style: "folk",
  bpm: 84,
  meter: [3, 4],
  swing: 0.1,
  space: 1.6,
  level: 0.4,
  sections: {
    intro: {
      name: "Intro: the fire is lit",
      chords: "G | C | G | D",
      parts: [{ inst: "flute", notes: ". . . | . . . | . . . | a4 b4 c5", vel: 0.5 }],
      arp: { inst: "harp", pattern: "0 1 2 3 2 1", vel: 0.4 },
      bass: { inst: "cello", pattern: "root", vel: 0.4 },
    },
    roarwing: {
      name: "Roarwing: the fiddle, bold and warm",
      chords: "G | Em | C | D | G | Em | Am D | G",
      parts: [{ inst: "fiddle", notes: "g4 b4 d5 | g5:2 fs5 | e5:1.5 d5:.5 c5 | d5:2 a4 | b4 d5 g5 | b5:2 a5 | a5:1.5 g5:.5 fs5 | g5:3", vel: 0.7 }],
      bass: { inst: "cello", pattern: "root_fifth", vel: 0.45 },
      arp: { inst: "harp", pattern: "0 . 1 2 1 2", vel: 0.36, optional: true },
    },
    burrowsby: {
      name: "Burrowsby: homely, the flute over strummed harp",
      chords: "C | F | C | G | Am | F | G | C",
      parts: [{ inst: "flute", notes: "c5:2 e5 | f5:1.5 e5:.5 d5 | e5:2 c5 | d5:3 | a4:1.5 b4:.5 c5 | a4:2 f4 | g4 b4 d5 | c5:3", vel: 0.7 }],
      bass: { inst: "pizz", pattern: "root_fifth", vel: 0.5 },
      arp: { inst: "harp", pattern: "0 1 2 1 2 1", vel: 0.38 },
    },
    refrain: {
      name: "Refrain: everyone round the fire",
      chords: REFRAIN_CHORDS,
      parts: [
        { inst: "flute", notes: REFRAIN, vel: 0.62 },
        { inst: "fiddle", notes: "c5:2 e5 | b4:2 g4 | a4 c5 e5 | d5:3 | c5:1.5 d5:.5 e5 | b4:1.5 a4:.5 g4 | a4 g4 fs4 | d4:3", vel: 0.5, optional: true },
      ],
      bass: { inst: "cello", pattern: "root", vel: 0.45 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1", vel: 0.34, optional: true },
    },
    skytalon: {
      name: "Skytalon: airy, a high flute, open harp",
      chords: "Am | Em | F | C | Dm | Am | Esus4 E | Am",
      parts: [{ inst: "flute", notes: "e5 a5 b5 | c6:2 b5 | a5 e5 a5 | g5:3 | f5 a5 d6 | c6:1.5 b5:.5 a5 | b5:1.5 a5:.5 gs5 | a5:3", vel: 0.64 }],
      bass: { inst: "cello", pattern: "root", vel: 0.38 },
      arp: { inst: "harp", pattern: "0 2 4 3 4 2", vel: 0.36 },
    },
    serpentyne: {
      name: "Serpentyne: cool and elegant, the cello over pizzicato",
      chords: "Dm | A/C# | Dm/C | G/B | Bbmaj7 | Gm | A7 | Dm",
      parts: [{ inst: "cello", notes: "d3:2 f3 | e3:2 a3 | a3:1.5 g3:.5 f3 | g3:2 d3 | f3:1.5 g3:.5 a3 | bf3:2 g3 | a3:1.5 cs4:.5 e4 | d4:3", vel: 0.7 }],
      bass: { inst: "pizz", pattern: "root", vel: 0.5 },
      arp: { inst: "pizz", pattern: ". . 1 . 2 .", vel: 0.42, octave: 3 },
    },
    refrain2: {
      name: "Refrain again",
      chords: REFRAIN_CHORDS,
      parts: [
        { inst: "fiddle", notes: REFRAIN, vel: 0.62 },
        { inst: "flute", notes: REFRAIN, vel: 0.44, octave: 1, optional: true },
      ],
      bass: { inst: "cello", pattern: "root_fifth", vel: 0.45 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1", vel: 0.34, optional: true },
    },
    end: {
      name: "Ending: embers",
      chords: "C | D7 | G | G",
      parts: [{ inst: "flute", notes: "e5:2 c5 | a4:2 fs4 | g4:3 | . . .", vel: 0.5 }],
      bass: { inst: "cello", pattern: "root", vel: 0.38 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 5", vel: 0.34 },
    },
  },
  form: ["intro", "roarwing", "burrowsby", "refrain", "skytalon", "serpentyne", "refrain2", "end"],
  loopFrom: 1,
  night: { tempo: 0.85, vel: 0.74, swap: { fiddle: "flute", cello: "harp", pizz: "harp" } },
};

export default score;
