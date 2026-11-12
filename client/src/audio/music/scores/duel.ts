// music.duel: a contest of skill, tense and driving but never brutal. C minor
// at a brisk march: pizzicato churning in eighths, the strings' dotted
// challenge, the horn's reply in E-flat, a phrygian standoff with celesta
// glinting over the strings, then the challenge again. Timpani and frame drum
// mark the downbeats. Night: unchanged but a shade softer (duels are indoors).

import type { Score } from "../score";

const CHALLENGE = "c5:1.5 c5:.5 ef5 g5 | af5:1.5 g5:.5 ef5 c5 | d5:1.5 ef5:.5 f5 d5 | b4:2 g4:2 | c5:1.5 c5:.5 ef5 g5 | c6:1.5 bf5:.5 af5 g5 | af5 g5 f5 ef5 | d5:2 b4:2";
const CHALLENGE_CHORDS = "Cm | Ab | Bb | G | Cm | Ab | Fm | G";
const KIT = "c2!:.5 c2:.5 a3?:.5 c2:.5 a3!:.5 c2:.5 a3?:.5 a3?:.5";
const kit8 = Array(8).fill(KIT).join(" | ");
const TIMP = "c3 . . c3:.5 c3:.5 | af2 . . . | bf2 . . . | g2 . g2 . | c3 . . c3:.5 c3:.5 | af2 . . . | f2 . . . | g2 . g2:.5 g2:.5 g2";

const score: Score = {
  id: "music.duel",
  title: "Wands at the Ready",
  style: "energetic",
  bpm: 138,
  meter: [4, 4],
  space: 2.2,
  level: 0.6,
  restS: [5, 12],
  sections: {
    intro: {
      name: "Intro: the bow, the circle, the count",
      chords: "Cm | Cm | Ab | G",
      parts: [
        { inst: "timpani", notes: "c3 . . . | c3 . . . | af2 . . . | g2:.25*16", vel: 0.5 },
        { inst: "horn", notes: "g4:4 | c5:4 | ef5:4 | d5:4", vel: 0.5 },
      ],
      pad: { inst: "strings", vel: 0.32 },
      arp: { inst: "pizz", pattern: "0 0 2 0 1 0 2 1", vel: 0.42, octave: 3 },
    },
    a: {
      name: "A: the strings' challenge",
      chords: CHALLENGE_CHORDS,
      parts: [
        { inst: "strings", notes: CHALLENGE, vel: 0.68 },
        { inst: "timpani", notes: TIMP, vel: 0.42, optional: true },
        { inst: "frame", notes: kit8, vel: 0.42, optional: true },
      ],
      bass: { inst: "pizz", pattern: "root", vel: 0.5 },
      arp: { inst: "pizz", pattern: "0 0 2 0 1 0 2 1", vel: 0.4, octave: 3 },
    },
    b: {
      name: "B: the horn's reply, in E-flat",
      chords: "Eb | Bb | Cm | G | Ab | Eb | F | G",
      parts: [
        { inst: "horn", notes: "g4:2 bf4 ef5 | f5:2 d5:2 | ef5:1.5 d5:.5 c5 g4 | b4:4 | c5:2 af4 c5 | ef5:2 g4:2 | a4:1.5 c5:.5 f5 a4 | b4:2 d5:2", vel: 0.72 },
        { inst: "strings", notes: "ef4:4 | d4:4 | ef4:4 | d4:4 | c4:4 | bf3:4 | a3:4 | b3:4", vel: 0.4, optional: true },
        { inst: "frame", notes: kit8, vel: 0.4, optional: true },
      ],
      bass: { inst: "pizz", pattern: "root_fifth", vel: 0.5 },
      arp: { inst: "pizz", pattern: "0 2 1 2 0 2 1 2", vel: 0.38, octave: 3, optional: true },
    },
    a2: {
      name: "A': the challenge again, the horn doubling",
      chords: CHALLENGE_CHORDS,
      parts: [
        { inst: "strings", notes: CHALLENGE, vel: 0.66 },
        { inst: "horn", notes: CHALLENGE, vel: 0.42, octave: -1, optional: true },
        { inst: "timpani", notes: TIMP, vel: 0.44 },
      ],
      bass: { inst: "pizz", pattern: "root", vel: 0.5 },
      arp: { inst: "pizz", pattern: "0 0 2 0 1 0 2 1", vel: 0.4, octave: 3 },
    },
    c: {
      name: "C: the standoff (phrygian), celesta over the strings",
      chords: "Cm | Db | Cm | Db | Ab | Bb | G | G",
      parts: [
        { inst: "strings", notes: "g4:4 | af4:4 | g4:4 | af4:2 bf4:2 | c5:4 | d5:4 | b4:2 d5 f5 | g5:4", vel: 0.6 },
        { inst: "frame", notes: "c2! . . . | c2! . . . | c2! . . . | c2! . c2 c2 | c2! . . . | c2! . . . | c2! . c2 . | c2!:.5 c2:.5 c2:.5 c2:.5 a3:.5 a3:.5 a3:.5 a3:.5", vel: 0.44 },
      ],
      bass: { inst: "pizz", pattern: "root", vel: 0.46 },
      arp: { inst: "celesta", pattern: "0 2 1 2 3 2 1 2", vel: 0.32, octave: 5, optional: true },
    },
    a3: {
      name: "A'': the challenge, everyone in",
      chords: CHALLENGE_CHORDS,
      parts: [
        { inst: "strings", notes: CHALLENGE, vel: 0.7 },
        { inst: "horn", notes: "c4:4 | c4:4 | d4:4 | d4:4 | ef4:4 | ef4:4 | c4:4 | b3:4", vel: 0.4, optional: true },
        { inst: "timpani", notes: TIMP, vel: 0.46 },
        { inst: "frame", notes: kit8, vel: 0.44, optional: true },
      ],
      bass: { inst: "pizz", pattern: "root", vel: 0.52 },
      arp: { inst: "pizz", pattern: "0 0 2 0 1 0 2 1", vel: 0.4, octave: 3, optional: true },
    },
    tag: {
      name: "Tag: the circle closes",
      chords: "Ab G | Cm",
      parts: [
        { inst: "horn", notes: "c5:2 b4:2 | c5:4", vel: 0.6 },
        { inst: "timpani", notes: "af2 . g2 . | c3:.25*8 c3 .", vel: 0.48 },
      ],
      pad: { inst: "strings", vel: 0.32 },
    },
  },
  form: ["intro", "a", "b", "a2", "c", "a3", "tag"],
  loopFrom: 1,
  night: { vel: 0.9 },
};

export default score;
