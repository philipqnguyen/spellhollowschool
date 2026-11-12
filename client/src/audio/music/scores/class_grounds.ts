// music.class_grounds: Herbology and Creature Keeping, lessons out
// of doors: earthy and cheerful in F major, a whistle tune with a playful
// turn, the fiddle's strain in D minor, a shaker keeping time and a sparkle
// of celesta. Night: harp and celesta, no shaker.

import type { Score } from "../score";

const TUNE = "f5:.5 e5:.5 f5 c5 a4 | bf4 d5 f5:2 | a5:.5 g5:.5 f5 c5 a4 | g4:.5 a4:.5 bf4 c5 e5 | f5:.5 e5:.5 f5 a5 f5 | d5 f5 bf5:2 | a5:.5 g5:.5 f5 e5 g5 | f5:2 .:2";
const TUNE_CHORDS = "F | Bb | F | C | F | Bb | C | F";
const SHAKE = "a4!:.5 a4?:.5 a4:.5 a4?:.5 a4!:.5 a4?:.5 a4:.5 a4?:.5";

const score: Score = {
  id: "music.class_grounds",
  title: "Mud on the Boots",
  style: "folk",
  bpm: 100,
  meter: [4, 4],
  swing: 0.15,
  space: 1.4,
  level: -0.4,
  sections: {
    intro: {
      name: "Intro: harp and shaker",
      chords: "F | C",
      parts: [{ inst: "shaker", notes: `${SHAKE} | ${SHAKE}`, vel: 0.5 }],
      arp: { inst: "harp", pattern: "0 1 2 1 3 1 2 1", vel: 0.4 },
      bass: { inst: "harp", pattern: "root_fifth", vel: 0.42 },
    },
    a: {
      name: "A: the whistle's tune",
      chords: TUNE_CHORDS,
      parts: [
        { inst: "whistle", notes: TUNE, vel: 0.7 },
        { inst: "shaker", notes: Array(8).fill(SHAKE).join(" | "), vel: 0.48, optional: true },
      ],
      bass: { inst: "harp", pattern: "root_fifth", vel: 0.44 },
      arp: { inst: "celesta", pattern: ". . . . 2 3 4 .", vel: 0.28, optional: true },
    },
    a2: {
      name: "A again: the fiddle has it, the whistle above",
      chords: TUNE_CHORDS,
      parts: [
        { inst: "fiddle", notes: TUNE, vel: 0.66, octave: -1 },
        { inst: "whistle", notes: "a5:2 c6:2 | d6:2 bf5:2 | c6:2 a5:2 | bf5:2 g5:2 | a5:2 c6:2 | bf5:2 d6:2 | c6:2 bf5:2 | a5:2 .:2", vel: 0.38, optional: true },
        { inst: "shaker", notes: Array(8).fill(SHAKE).join(" | "), vel: 0.48, optional: true },
      ],
      bass: { inst: "harp", pattern: "root_fifth", vel: 0.44 },
      arp: { inst: "harp", pattern: "0 1 2 1 0 1 2 1", vel: 0.3, optional: true },
    },
    b: {
      name: "B: the fiddle's strain, in D minor",
      chords: "Dm | C | Bb | F | Gm | Dm | C | C7",
      parts: [
        { inst: "fiddle", notes: "d5 f5 a5:.5 g5:.5 f5 | e5 g5 c5:2 | d5:.5 c5:.5 bf4 d5 f5 | a4:2 f4:2 | g4:.5 a4:.5 bf4 d5 g5 | f5 e5 d5 a4 | g4 c5 e5 g5 | c5:2 bf4:2", vel: 0.68 },
        { inst: "celesta", notes: ". . . . | . . e6 g6 | . . . . | . . a5 c6 | . . . . | . . f6 d6 | . . . . | . . bf5 g5", vel: 0.36, optional: true },
      ],
      bass: { inst: "harp", pattern: "root", vel: 0.44 },
      arp: { inst: "harp", pattern: "0 1 2 1 0 1 2 1", vel: 0.32, optional: true },
    },
    a3: {
      name: "A to finish: whistle and fiddle together",
      chords: TUNE_CHORDS,
      parts: [
        { inst: "whistle", notes: TUNE, vel: 0.64 },
        { inst: "fiddle", notes: TUNE, vel: 0.5, octave: -1, optional: true },
        { inst: "shaker", notes: Array(8).fill(SHAKE).join(" | "), vel: 0.5, optional: true },
      ],
      bass: { inst: "harp", pattern: "root_fifth", vel: 0.44 },
      arp: { inst: "celesta", pattern: ". . . . 2 3 4 .", vel: 0.28, optional: true },
    },
    end: {
      name: "Ending",
      chords: "Bb C | F",
      parts: [
        { inst: "whistle", notes: "d5 e5 f5:.5 g5:.5 a5 | f5:2 .:2", vel: 0.55 },
        { inst: "celesta", notes: ". . . . | f6:2 .:2", vel: 0.4 },
      ],
      bass: { inst: "harp", pattern: "root", vel: 0.42 },
    },
  },
  form: ["intro", "a", "a2", "b", "a3", "end"],
  loopFrom: 1,
  night: { tempo: 0.9, vel: 0.76, swap: { whistle: "celesta", fiddle: "harp", shaker: null } },
};

export default score;
