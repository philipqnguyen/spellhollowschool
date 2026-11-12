// stinger.discovery: a secret found. A hushed "oh!": the choir moves from C to
// an unexpected A-flat, the celesta glinting over it.

import type { Score } from "../score";

const score: Score = {
  id: "stinger.discovery",
  title: "Behind the Tapestry",
  style: "celesta",
  bpm: 80,
  meter: [4, 4],
  space: 3.4,
  sections: {
    s: {
      name: "Discovery",
      parts: [
        { inst: "choir", notes: "c4+e4+g4:2 c4+ef4+af4:2", vel: 0.5 },
        { inst: "celesta", notes: "g5:.5 c6:.5 e6 . ef6", vel: 0.56 },
        { inst: "harp", notes: ". . af3:.25 c4:.25 ef4:.25 af4:1.25", vel: 0.44 },
      ],
    },
  },
  form: ["s"],
};

export default score;
