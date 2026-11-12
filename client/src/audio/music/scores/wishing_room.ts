// music.wishing_room: a room that answers a wish. Wonder, hush, kindness
// in D-flat major: the celesta's gentle tune over a choir, a middle strain
// in B-flat minor where the music box answers the harp, the tune again with
// the strings. Night: the same, a little slower.

import type { Score } from "../score";

const KIND = "f5:2 af5 df6 | bf5:3 af5 | f5:1.5 gf5:.5 f5 ef5 | ef5:4 | bf4:2 df5 gf5 | f5:2 ef5 df5 | ef5:2 af4 c5 | df5:4";
const KIND_CHORDS = "Db | Gb/Db | Bbm | Ab/C | Gb | Ebm7 | Absus4 Ab | Db";

const score: Score = {
  id: "music.wishing_room",
  title: "The Room That Listens",
  style: "celesta",
  bpm: 66,
  meter: [4, 4],
  space: 3.4,
  level: 1.9,
  sections: {
    intro: {
      name: "Intro: a door that was not there",
      chords: "Db | Gb/Db",
      parts: [{ inst: "musicbox", notes: ". . af5 df6 | . . bf5 gf5", vel: 0.42 }],
      pad: { inst: "choir", vel: 0.3, octave: 3 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1 2 1", vel: 0.3 },
    },
    a: {
      name: "A: the celesta's tune",
      chords: KIND_CHORDS,
      parts: [{ inst: "celesta", notes: KIND, vel: 0.64 }],
      pad: { inst: "choir", vel: 0.28, octave: 3 },
      bass: { inst: "strings", pattern: "root", vel: 0.32 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1 2 1", vel: 0.28, optional: true },
    },
    b: {
      name: "B: the harp asks, the music box answers (B-flat minor)",
      chords: "Bbm | Gb | Db | Ab | Bbm | Gb | Ebm7 | Ab7",
      parts: [
        { inst: "harp", notes: "df5:2 f5 bf5 | af5:2 gf5:2 | f5:2 af5 df6 | c6:4 | bf5:2 df6 f6 | ef6:2 df6:2 | gf5:2 bf5 ef6 | c6:4", vel: 0.58 },
        { inst: "musicbox", notes: ". . . . | . . bf5 df6 | . . . . | . . ef6 c6 | . . . . | . . gf6 ef6 | . . . . | . . gf6 ef6", vel: 0.44 },
      ],
      pad: { inst: "strings", vel: 0.26, octave: 3 },
      bass: { inst: "strings", pattern: "root", vel: 0.3 },
    },
    a2: {
      name: "A': the tune with the strings beneath, the choir closer",
      chords: KIND_CHORDS,
      parts: [
        { inst: "celesta", notes: KIND, vel: 0.6 },
        { inst: "strings", notes: "af4:4 | gf4:4 | f4:4 | ef4:4 | df4:4 | gf4:4 | ef4:2 gf4:2 | f4:4", vel: 0.4, optional: true },
      ],
      pad: { inst: "choir", vel: 0.3, octave: 3 },
      bass: { inst: "strings", pattern: "root", vel: 0.32 },
      arp: { inst: "harp", pattern: "0 1 2 3 2 1 2 1", vel: 0.26, optional: true },
    },
    end: {
      name: "Ending: the room settles",
      chords: "Gb | Db",
      parts: [{ inst: "musicbox", notes: ". . bf5 af5 | f5:4", vel: 0.4 }],
      pad: { inst: "choir", vel: 0.26, octave: 3 },
      arp: { inst: "harp", pattern: "0 1 2 3 4 5 6 .", vel: 0.26 },
    },
  },
  form: ["intro", "a", "b", "a2", "end"],
  loopFrom: 1,
  night: { tempo: 0.9, vel: 0.85 },
};

export default score;
