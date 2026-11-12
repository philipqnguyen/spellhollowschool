// stinger.level_up: our level up. A rising celesta figure that lands on a
// bright chord with a glockenspiel glint.

import type { Score } from "../score";

const score: Score = {
  id: "stinger.level_up",
  title: "A Step Higher",
  style: "celesta",
  bpm: 120,
  meter: [4, 4],
  space: 2.4,
  sections: {
    s: {
      name: "Level up",
      parts: [
        { inst: "celesta", notes: "g4:.25 b4:.25 d5:.25 g5:.25 a5:.25 d6:.25 b5+d6+g6:2.5", vel: 0.56 },
        { inst: "glock", notes: ". . g6:2", vel: 0.4 },
        { inst: "harp", notes: ". . g3+d4+g4+b4:2", vel: 0.5 },
        { inst: "strings", notes: ". . g3+b3+d4:2", vel: 0.36 },
      ],
    },
  },
  form: ["s"],
};

export default score;
