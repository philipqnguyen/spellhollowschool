// The score notation (score.ts) read into notes and chords. Pure and DOM-free,
// so the tests and the compiler share it.
//
//   c4  fs4  bf3        a note (s sharp, f flat; c4 is middle C, MIDI 60)
//   c4:2  e4:.5         a length in the part's units
//   .  .:2              a rest
//   ~  ~:2              holds the previous note (or chord) longer
//   c4+e4+g4            a chord
//   c5!  c5?            accented (x1.25) / soft (x0.6)
//   e4:.5*4             the token repeated
//   |                   a bar line: the strict reader checks the bar's length

export interface Note {
  /** Start, in beats from the part's start. */
  beat: number;
  midi: number;
  beats: number;
  /** Accent multiplier (1 unmarked). */
  accent: number;
  /** Index of the token that struck it (humanizing seeds). */
  index: number;
}

const PITCH_CLASS: Readonly<Record<string, number>> = { c: 0, d: 2, e: 4, f: 5, g: 7, a: 9, b: 11 };

/** `cs4` `bf3` `a4` -> MIDI. */
export function noteToMidi(name: string): number {
  const m = /^([a-g])([sf]?)(-?\d)$/.exec(name);
  if (!m) throw new Error(`bad note: ${name}`);
  return 12 * (Number(m[3]) + 1) + PITCH_CLASS[m[1]!]! + (m[2] === "s" ? 1 : m[2] === "f" ? -1 : 0);
}

export function midiToFreq(m: number): number {
  return 440 * Math.pow(2, (m - 69) / 12);
}

//            pitch(+pitch)*                          rest   hold  accent    :len           *repeat
const TOKEN = /^(?:([a-g][sf]?-?\d(?:\+[a-g][sf]?-?\d)*)|(\.)|(~))([!?]?)(?::([0-9.]+))?(?:\*(\d+))?$/;
const ACCENT: Readonly<Record<string, number>> = { "": 1, "!": 1.25, "?": 0.6 };

function tokens(src: string): string[] {
  return src.replace(/\|/g, " | ").trim().split(/\s+/).filter(Boolean);
}

/**
 * A part's notes. `beatsPerBar` with `strict` checks every bar adds up (the
 * tests); without it a bar line resyncs the cursor, so a slip dents one bar
 * instead of shearing the rest of the part.
 */
export function parseNotes(src: string, unit = 1, beatsPerBar = 4, strict = false): Note[] {
  const out: Note[] = [];
  let last: Note[] = [];
  let beat = 0;
  let bar = 0;
  let index = 0;
  for (const tok of tokens(src)) {
    if (tok === "|") {
      bar++;
      const want = bar * beatsPerBar;
      if (strict && Math.abs(beat - want) > 1e-6) throw new Error(`bar ${bar} is ${+(beat - (bar - 1) * beatsPerBar).toFixed(3)} beats, the meter wants ${beatsPerBar}`);
      beat = want;
      continue;
    }
    const m = TOKEN.exec(tok);
    if (!m) throw new Error(`bad token: ${tok}`);
    const len = (m[5] ? Number(m[5]) : 1) * unit;
    if (!(len > 0)) throw new Error(`bad length: ${tok}`);
    const reps = m[6] ? Number(m[6]) : 1;
    for (let r = 0; r < reps; r++) {
      if (m[3]) {
        for (const n of last) n.beats += len;
      } else if (m[1]) {
        const accent = ACCENT[m[4] ?? ""]!;
        last = m[1].split("+").map((p) => ({ beat, midi: noteToMidi(p), beats: len, accent, index }));
        out.push(...last);
      } else {
        last = [];
      }
      beat += len;
      index++;
    }
  }
  return out;
}

/** How many beats a part's notes take (bar lines ignored). */
export function measure(src: string, unit = 1): number {
  let beat = 0;
  for (const tok of tokens(src)) {
    if (tok === "|") continue;
    const m = TOKEN.exec(tok);
    if (!m) throw new Error(`bad token: ${tok}`);
    beat += (m[5] ? Number(m[5]) : 1) * unit * (m[6] ? Number(m[6]) : 1);
  }
  return beat;
}

// ---------------------------------------------------------------- chords

const QUALITY: Readonly<Record<string, readonly number[]>> = {
  "": [0, 4, 7],
  m: [0, 3, 7],
  "5": [0, 7],
  sus2: [0, 2, 7],
  sus4: [0, 5, 7],
  "6": [0, 4, 7, 9],
  m6: [0, 3, 7, 9],
  "7": [0, 4, 7, 10],
  maj7: [0, 4, 7, 11],
  m7: [0, 3, 7, 10],
  m7b5: [0, 3, 6, 10],
  dim: [0, 3, 6],
  dim7: [0, 3, 6, 9],
  aug: [0, 4, 8],
  add9: [0, 4, 7, 14],
  madd9: [0, 3, 7, 14],
  "9": [0, 4, 7, 10, 14],
  maj9: [0, 4, 7, 11, 14],
  m9: [0, 3, 7, 10, 14],
  "7sus4": [0, 5, 7, 10],
};

export interface Chord {
  /** Root pitch class. */
  root: number;
  /** The bass's pitch class (a slash chord's, else the root). */
  bass: number;
  /** Semitones above the root. */
  tones: readonly number[];
}

/** `Am` `F#m7` `Bb` `G/B` `Dsus4`; `_` is no chord (null). */
export function parseChord(name: string): Chord | null {
  if (name === "_") return null;
  const m = /^([A-G])([#b]?)([^/]*)(?:\/([A-G])([#b]?))?$/.exec(name);
  if (!m) throw new Error(`bad chord: ${name}`);
  const pc = (l: string, acc: string | undefined) => (PITCH_CLASS[l.toLowerCase()]! + (acc === "#" ? 1 : acc === "b" ? -1 : 0) + 12) % 12;
  const tones = QUALITY[m[3]!];
  if (!tones) throw new Error(`bad chord quality: ${name}`);
  const root = pc(m[1]!, m[2]);
  return { root, bass: m[4] ? pc(m[4], m[5]) : root, tones };
}

/** A section's chords, one array per bar. */
export function chordBars(chords: string): (Chord | null)[][] {
  return chords
    .split("|")
    .map((g) => g.trim().split(/\s+/).filter(Boolean))
    .filter((g) => g.length > 0)
    .map((g) => g.map(parseChord));
}

/** The lowest MIDI note at or above `floor` with pitch class `pc`. */
export function placeAbove(pc: number, floor: number): number {
  let m = 12 * Math.floor(floor / 12) + (((pc % 12) + 12) % 12);
  while (m < floor) m += 12;
  return m;
}
