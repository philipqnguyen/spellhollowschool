// stinger.class_result: the results card. A pleased little cadence: the
// clarinet turns home over pizzicato.

import type { Score } from "../score";

const score: Score = {
  id: "stinger.class_result",
  title: "Well Done",
  style: "celesta",
  bpm: 110,
  meter: [4, 4],
  space: 1.8,
  sections: {
    s: {
      name: "Class result",
      parts: [
        { inst: "clarinet", notes: "b4:.5 c5:.5 d5:.5 b4:.5 c5:2", vel: 0.62 },
        { inst: "pizz", notes: "g3 g2 c3:2", vel: 0.56 },
        { inst: "celesta", notes: ". . . c6", vel: 0.4 },
      ],
    },
  },
  form: ["s"],
};

export default score;
