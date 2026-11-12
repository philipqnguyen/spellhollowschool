// stinger.duel_won: a short, proud cadence on horn and strings, timpani under it.

import type { Score } from "../score";

const score: Score = {
  id: "stinger.duel_won",
  title: "Bow to Your Opponent",
  style: "energetic",
  bpm: 110,
  meter: [6, 4],
  space: 2.4,
  sections: {
    s: {
      name: "Duel won",
      parts: [
        { inst: "horn", notes: "g4 c5 e5 d5:.75 c5:.25 g5:2", vel: 0.66 },
        { inst: "strings", notes: "c4+e4+g4:3 g3+d4+b4 c4+e4+g4+c5:2", vel: 0.48 },
        { inst: "timpani", notes: "c3 . . g2 c3:2", vel: 0.5 },
      ],
    },
  },
  form: ["s"],
};

export default score;
