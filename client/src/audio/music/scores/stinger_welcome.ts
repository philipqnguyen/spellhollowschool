// stinger.welcome: entering the game. A door opening onto warm light: a harp
// roll up a D major chord and the strings swelling under it.

import type { Score } from "../score";

const score: Score = {
  id: "stinger.welcome",
  title: "The Doors Open",
  style: "orchestral",
  bpm: 72,
  meter: [4, 4],
  space: 3.0,
  sections: {
    s: {
      name: "Welcome",
      parts: [
        { inst: "harp", notes: "d4:.25 fs4:.25 a4:.25 d5:.25 fs5:.25 a5:.25 d6:2.5", vel: 0.62 },
        { inst: "strings", notes: "d3+a3+fs4:4", vel: 0.5 },
        { inst: "horn", notes: ". a4:3", vel: 0.42 },
        { inst: "celesta", notes: ". . fs6:2", vel: 0.36 },
      ],
    },
  },
  form: ["s"],
};

export default score;
