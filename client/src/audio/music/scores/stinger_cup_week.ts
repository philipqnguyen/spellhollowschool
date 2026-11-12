// stinger.cup_week: the House Cup week has closed. A small fanfare on soft
// brass, strings under it, a timpani roll into the last chord.

import type { Score } from "../score";

const score: Score = {
  id: "stinger.cup_week",
  title: "The Cup Is Counted",
  style: "orchestral",
  bpm: 96,
  meter: [4, 4],
  space: 2.8,
  sections: {
    s: {
      name: "Cup week",
      parts: [
        { inst: "horn", notes: "c4:.5 c4:.5 g4 e4:.5 g4:.5 c5 | b4:.5 c5:.5 d5 c5:2", vel: 0.64 },
        { inst: "strings", notes: "c3+g3+e4:2 f3+c4+a4:2 | g3+d4+b4:2 c3+g3+e4+c5:2", vel: 0.42 },
        { inst: "timpani", notes: "c3:.25*8 g2 c3 | g2:.25*8 c3:2", vel: 0.42 },
      ],
    },
  },
  form: ["s"],
};

export default score;
