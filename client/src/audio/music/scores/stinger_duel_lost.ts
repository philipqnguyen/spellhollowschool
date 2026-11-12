// stinger.duel_lost: a duel lost or drawn. A gentle falling phrase that still
// ends in the major: not mournful, just "next time".

import type { Score } from "../score";

const score: Score = {
  id: "stinger.duel_lost",
  title: "Next Time",
  style: "orchestral",
  bpm: 90,
  meter: [5, 4],
  space: 2.6,
  sections: {
    s: {
      name: "Duel lost",
      parts: [
        { inst: "clarinet", notes: "e5 d5 c5 d5:.5 b4:.5 c5", vel: 0.58 },
        { inst: "strings", notes: "f3+a3+c4:2 g3+b3+d4:2 g3+c4+e4", vel: 0.42 },
      ],
    },
  },
  form: ["s"],
};

export default score;
