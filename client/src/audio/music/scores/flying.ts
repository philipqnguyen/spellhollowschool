// music.flying: on a broom and in the Flying class. Wind, speed, joy in the
// air: a quick reel in A major on the fiddle, the whistle takes it, a horn
// strain in F-sharp minor with the fiddle churning underneath, a high whistle
// lift, then all together. The frame drum drives it. Night: the same drive on
// strings and harp, the drum softer.

import type { Score } from "../score";

const REEL = "e5:.5 a5:.5 cs6:.5 a5:.5 e5 cs5 | d5:.5 fs5:.5 a5:.5 fs5:.5 d5 a4 | cs5:.5 e5:.5 a5 gs5:.5 fs5:.5 e5 | b4:.5 cs5:.5 d5:.5 e5:.5 gs5:2 | e5:.5 a5:.5 cs6:.5 a5:.5 e5 cs5 | fs5:.5 a5:.5 d6:.5 a5:.5 fs5 d5 | e5:.5 fs5:.5 gs5:.5 b5:.5 a5:.5 gs5:.5 fs5:.5 e5:.5 | a5:2 .:2";
const REEL_CHORDS = "A | D | A | E | A | D | E | A";
const DRIVE = "a2!:.5 a3?:.5 a3:.5 a2:.5 a2!:.5 a3?:.5 a3:.5 a3?:.5";
const drive8 = Array(8).fill(DRIVE).join(" | ");

const score: Score = {
  id: "music.flying",
  title: "Above the Towers",
  style: "energetic",
  bpm: 132,
  meter: [4, 4],
  space: 2.0,
  level: 0.4,
  restS: [8, 20],
  sections: {
    intro: {
      name: "Intro: the drum, the strings lift off",
      chords: "A | D | A | E",
      parts: [
        { inst: "frame", notes: Array(4).fill(DRIVE).join(" | "), vel: 0.48 },
        { inst: "fiddle", notes: ". . . . | . . . . | . . . . | e4:.5 fs4:.5 gs4:.5 a4:.5 b4:.5 cs5:.5 d5", vel: 0.6 },
      ],
      pad: { inst: "strings", vel: 0.34 },
      bass: { inst: "strings", pattern: "root_fifth", vel: 0.42 },
    },
    a: {
      name: "A: the fiddle's reel",
      chords: REEL_CHORDS,
      parts: [
        { inst: "fiddle", notes: REEL, vel: 0.72 },
        { inst: "frame", notes: drive8, vel: 0.46, optional: true },
      ],
      pad: { inst: "strings", vel: 0.3 },
      bass: { inst: "strings", pattern: "root_fifth", vel: 0.44 },
      arp: { inst: "harp", pattern: "0 1 2 1", unit: 1, vel: 0.34, optional: true },
    },
    a2: {
      name: "A': the whistle takes it, the fiddle holds below",
      chords: REEL_CHORDS,
      parts: [
        { inst: "whistle", notes: REEL, vel: 0.68 },
        { inst: "fiddle", notes: "cs5:2 e5:2 | d5:2 fs5:2 | e5:2 cs5:2 | b4:2 e5:2 | cs5:2 e5:2 | d5:2 a5:2 | gs5:2 e5:2 | e5:2 .:2", vel: 0.45, optional: true },
        { inst: "frame", notes: drive8, vel: 0.46, optional: true },
      ],
      pad: { inst: "strings", vel: 0.3 },
      bass: { inst: "strings", pattern: "root_fifth", vel: 0.44 },
      arp: { inst: "harp", pattern: "0 1 2 1", unit: 1, vel: 0.34, optional: true },
    },
    b: {
      name: "B: the horn in F-sharp minor, the fiddle churning",
      chords: "F#m | D | A | E | F#m | D | B7 | E",
      parts: [
        { inst: "horn", notes: "fs4:2 a4 cs5 | d5:3 a4 | cs5:2 e5 cs5 | b4:4 | a4:2 cs5 fs5 | fs5:2 e5 d5 | ds5:2 b4:2 | e5:4", vel: 0.7 },
        { inst: "frame", notes: drive8, vel: 0.44, optional: true },
      ],
      pad: { inst: "strings", vel: 0.3 },
      bass: { inst: "strings", pattern: "root", vel: 0.44 },
      arp: { inst: "fiddle", pattern: "2 1 0 1 2 1 0 1", vel: 0.4, optional: true },
    },
    c: {
      name: "C: the whistle climbs",
      chords: "D | E | C#m | F#m | Bm | E | A | A",
      parts: [
        { inst: "whistle", notes: "a5 fs5 d5 fs5 | e5:.5 fs5:.5 gs5 b5:2 | gs5 e5 cs5 e5 | fs5:2 cs6:2 | b5 a5 fs5 d5 | e5:.5 fs5:.5 gs5:.5 a5:.5 b5:2 | a5:2 e5 cs5 | a4:2 .:2", vel: 0.68 },
        { inst: "horn", notes: "fs4:4 | gs4:4 | e4:4 | a4:4 | fs4:4 | gs4:4 | e4:4 | e4:4", vel: 0.4, optional: true },
        { inst: "frame", notes: drive8, vel: 0.44, optional: true },
      ],
      pad: { inst: "strings", vel: 0.28 },
      bass: { inst: "strings", pattern: "root_fifth", vel: 0.42 },
      arp: { inst: "harp", pattern: "0 2 1 2", unit: 1, vel: 0.32, optional: true },
    },
    a3: {
      name: "A'': all together",
      chords: REEL_CHORDS,
      parts: [
        { inst: "fiddle", notes: REEL, vel: 0.68 },
        { inst: "whistle", notes: REEL, vel: 0.5, optional: true },
        { inst: "horn", notes: "a4:4 | a4:4 | a4:4 | gs4:4 | a4:4 | a4:4 | b4:2 gs4:2 | a4:4", vel: 0.38, optional: true },
        { inst: "frame", notes: drive8, vel: 0.5 },
      ],
      pad: { inst: "strings", vel: 0.3 },
      bass: { inst: "strings", pattern: "root_fifth", vel: 0.46 },
    },
    end: {
      name: "Landing",
      chords: "D E | A",
      parts: [
        { inst: "fiddle", notes: "fs5 gs5 a5:2 | a5:4", vel: 0.6 },
        { inst: "frame", notes: "a2:.5 a3:.5 a3:.5 a2:.5 a2:.5 a3:.5 a3:.5 a3:.5 | a2! . . .", vel: 0.5 },
      ],
      pad: { inst: "strings", vel: 0.3 },
      bass: { inst: "strings", pattern: "root", vel: 0.42 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 5 6 7", vel: 0.32 },
    },
  },
  form: ["intro", "a", "a2", "b", "c", "a3", "end"],
  loopFrom: 1,
  night: { tempo: 0.95, vel: 0.78, swap: { fiddle: "strings", whistle: "strings", horn: "strings" } },
};

export default score;
