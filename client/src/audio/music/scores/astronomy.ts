// music.astronomy: the night sky from the tower. Slow and turning in E-flat
// lydian (the raised fourth, A natural, is the awe): celesta stars over a
// choir and string bed, a glockenspiel glint now and then, the harp turning
// like the sky. This piece is the night; its day variant adds a flute.

import type { Score } from "../score";

const STARS = "g5:2 bf5 d6 | c6:3 a5 | bf5:2 f5:2 | g5:4 | c6:2 ef6 c6 | d6:3 bf5 | g5:2 a5 bf5 | g5:4";
const STARS_CHORDS = "Ebmaj7 | F/Eb | Gm7 | Cm7 | Abmaj7 | Bb | Ebmaj7 | Ebmaj7";

const score: Score = {
  id: "music.astronomy",
  title: "Slow Wheel of Stars",
  style: "celesta",
  bpm: 60,
  meter: [4, 4],
  space: 3.6,
  level: 0.6,
  restS: [30, 70],
  sections: {
    intro: {
      name: "Intro: the dome opens",
      chords: "Ebmaj7 | F/Eb",
      parts: [{ inst: "glock", notes: ". . . bf6 | . . . a6", vel: 0.36 }],
      pad: { inst: "choir", vel: 0.3, octave: 3 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 3 2 1", vel: 0.3, optional: true },
    },
    a: {
      name: "A: celesta stars",
      chords: STARS_CHORDS,
      parts: [
        { inst: "celesta", notes: STARS, vel: 0.62 },
        { inst: "glock", notes: ". . . . | . . . . | . . . . | . . . d7 | . . . . | . . . . | . . . . | . . . bf6", vel: 0.3, optional: true },
      ],
      pad: { inst: "choir", vel: 0.28, octave: 3 },
      bass: { inst: "strings", pattern: "root", vel: 0.34 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 3 2 1", vel: 0.28, optional: true },
    },
    b: {
      name: "B: the choir turns the sky",
      chords: "Cm | Ab | Eb/G | Bb | Fm7 | Ab | Bbsus4 | Bb",
      parts: [
        { inst: "choir", notes: "c5:4 | c5:2 ef5:2 | bf4:4 | d5:4 | c5:4 | ef5:2 f5:2 | f5:4 | d5:4", vel: 0.5 },
        { inst: "celesta", notes: ". . g6 . | . . af6 . | . . g6 . | . . f6 . | . . af6 . | . . c7 . | . . bf6 . | . . . .", vel: 0.36, optional: true },
        { inst: "flute", notes: "g5:4 | ef5:3 c5 | d5:4 | f5:4 | af5:3 g5 | f5:2 ef5:2 | f5:4 | bf5:4", vel: 0.48, only: "day" },
      ],
      pad: { inst: "strings", vel: 0.26, octave: 3 },
      bass: { inst: "strings", pattern: "root", vel: 0.32 },
      arp: { inst: "harp", pattern: "0 2 1 2 0 2 1 2", vel: 0.26, optional: true },
    },
    a2: {
      name: "A': the stars again, higher",
      chords: STARS_CHORDS,
      parts: [
        { inst: "celesta", notes: STARS, vel: 0.56, octave: 1 },
        { inst: "flute", notes: STARS, vel: 0.5, only: "day" },
        { inst: "harp", notes: STARS, vel: 0.4, only: "night" },
      ],
      pad: { inst: "choir", vel: 0.26, octave: 3 },
      bass: { inst: "strings", pattern: "root", vel: 0.32 },
    },
    end: {
      name: "Ending: the dome closes",
      chords: "Abmaj7 | Ebmaj7",
      parts: [{ inst: "glock", notes: ". . . . | g6:4", vel: 0.32 }],
      pad: { inst: "choir", vel: 0.26, octave: 3 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 5 6 7", vel: 0.26, optional: true },
    },
  },
  form: ["intro", "a", "b", "a2", "end"],
  loopFrom: 1,
  night: { tempo: 0.95, vel: 0.85 },
};

export default score;
