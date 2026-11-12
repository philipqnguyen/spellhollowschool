// music.class: lessons indoors, under the professor's words: playful,
// inquisitive, light on its feet. C major with a lydian sharp fourth for the
// spark of curiosity: the clarinet's question, the celesta's answer in A
// minor, a music-box sequence in thirds. Pizzicato underneath throughout.
// Night: celesta and pizzicato only.

import type { Score } from "../score";

const QUESTION = "e4:.5 g4:.5 c5 b4:.5 a4:.5 g4 | fs4:.5 a4:.5 d5 c5 a4 | b4 d5 g4:.5 a4:.5 b4 | c5:2 .:2 | e5:.5 d5:.5 c5 a4:.5 b4:.5 c5 | d5 fs4 a4 c5 | b4:.5 a4:.5 g4 d5 b4 | g4:2 f4 d4";
const QUESTION_CHORDS = "C | D/C | G/B | C | Am | D7 | G | G7";

const score: Score = {
  id: "music.class",
  title: "Chalk and Quills",
  style: "celesta",
  bpm: 104,
  meter: [4, 4],
  swing: 0.08,
  space: 1.8,
  sections: {
    intro: {
      name: "Intro: pizzicato steps in",
      chords: "C | G7",
      parts: [{ inst: "glock", notes: ". . . . | . . g5 b5", vel: 0.4 }],
      bass: { inst: "pizz", pattern: "walk", vel: 0.5 },
    },
    a: {
      name: "A: the clarinet's question",
      chords: QUESTION_CHORDS,
      parts: [
        { inst: "clarinet", notes: QUESTION, vel: 0.66 },
        { inst: "glock", notes: ". . . . | . . . . | . . . . | . g5 e5 . | . . . . | . . . . | . . . . | . . . .", vel: 0.36, optional: true },
      ],
      bass: { inst: "pizz", pattern: "root_fifth", vel: 0.5 },
      arp: { inst: "celesta", pattern: ". 2 . 1 . 2 . 1", vel: 0.3, optional: true },
    },
    b: {
      name: "B: the celesta's answer, in A minor",
      chords: "Am | E/G# | C/G | D/F# | F | C/E | Dm7 | G7",
      parts: [{ inst: "celesta", notes: "a5:.5 b5:.5 c6 e5 a5 | b5:.5 a5:.5 gs5 e5:2 | g5:.5 a5:.5 c6 e5 g5 | fs5 a5 d5:2 | f5:.5 g5:.5 a5 f5 c6 | g5 e5 c5:2 | d5:.5 e5:.5 f5 a5 c6 | b5:2 .:2", vel: 0.64 }],
      bass: { inst: "pizz", pattern: "root", vel: 0.5 },
      arp: { inst: "pizz", pattern: ". 1 . 2 . 1 . 2", vel: 0.32, octave: 3, optional: true },
    },
    a2: {
      name: "A': the question again, the celesta joining",
      chords: QUESTION_CHORDS,
      parts: [
        { inst: "clarinet", notes: QUESTION, vel: 0.62 },
        { inst: "celesta", notes: QUESTION, vel: 0.36, octave: 1, optional: true },
      ],
      bass: { inst: "pizz", pattern: "root_fifth", vel: 0.5 },
    },
    c: {
      name: "C: a music-box sequence in thirds",
      chords: "F | G | Em | Am | Dm | G | C | C",
      parts: [
        { inst: "musicbox", notes: "c6:1.5 a5:.5 f5 a5 | b5:1.5 g5:.5 d5 g5 | b5 g5 e5 g5 | c6:2 a5:2 | a5 f5 d5 f5 | g5 b5 d6 b5 | c6:2 g5 e5 | c5:2 .:2", vel: 0.6 },
        { inst: "clarinet", notes: "a4:4 | b4:4 | g4:4 | e4:4 | f4:4 | d4:4 | e4:2 g4:2 | e4:2 .:2", vel: 0.38, optional: true },
      ],
      bass: { inst: "pizz", pattern: "root", vel: 0.48 },
    },
    a3: {
      name: "A'': the question, answered",
      chords: "C | D/C | G/B | C | Am | D7 | G7 | C",
      parts: [
        { inst: "clarinet", notes: "e4:.5 g4:.5 c5 b4:.5 a4:.5 g4 | fs4:.5 a4:.5 d5 c5 a4 | b4 d5 g4:.5 a4:.5 b4 | c5:2 .:2 | e5:.5 d5:.5 c5 a4:.5 b4:.5 c5 | d5 fs4 a4 c5 | b4 d5 f4 d4 | c4:2 .:2", vel: 0.62 },
        { inst: "glock", notes: ". . . . | . . . . | . . . . | . g5 e5 . | . . . . | . . . . | . . . . | . e6 c6 .", vel: 0.34, optional: true },
      ],
      bass: { inst: "pizz", pattern: "root_fifth", vel: 0.5 },
      arp: { inst: "celesta", pattern: ". 2 . 1 . 2 . 1", vel: 0.3, optional: true },
    },
    end: {
      name: "Tag",
      chords: "F G | C",
      parts: [{ inst: "celesta", notes: "a5:.5 b5:.5 c6 d6 b5 | c6:2 .:2", vel: 0.5 }],
      bass: { inst: "pizz", pattern: "root", vel: 0.48 },
    },
  },
  form: ["intro", "a", "b", "a2", "c", "a3", "end"],
  loopFrom: 1,
  night: { tempo: 0.9, vel: 0.78, swap: { clarinet: "celesta", musicbox: "celesta", glock: null } },
};

export default score;
