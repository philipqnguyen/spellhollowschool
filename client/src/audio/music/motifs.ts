// Originality guard (docs/architecture.md "Sound and music"): our music is
// our own, written from scratch, never a quotation of a well-known theme. A
// melody line fails the music tests when it follows one of the famous
// openings below (FAMOUS, stored only as interval patterns), in any key and
// any rhythm. Repeated notes count once (a tie, a restrike).
//
// This is a floor, not the whole duty: the composer writes new tunes from
// scratch and never works from an existing theme.

/** Notes in a run compared exactly (so RUN - 1 intervals). */
export const RUN = 6;

const PITCH_CLASS: Readonly<Record<string, number>> = { c: 0, d: 2, e: 4, f: 5, g: 7, a: 9, b: 11 };
const NOTE = /^([a-g])([sf]?)(-?\d)$/;

/**
 * The pitches a part's `notes` (score.ts notation) sounds in order, as MIDI:
 * a chord counts as its top note (the tune), rests and holds as nothing.
 */
export function pitchesOf(notes: string, octave = 0): number[] {
  const out: number[] = [];
  for (const raw of notes.split(/\s+/)) {
    const head = raw.replace(/[!?]?(?::[0-9.]+)?(?:\*\d+)?$/, "");
    const times = Number(/\*(\d+)$/.exec(raw)?.[1] ?? 1);
    let top: number | null = null;
    for (const n of head.split("+")) {
      const m = NOTE.exec(n);
      if (!m) continue;
      const midi = 12 * (Number(m[3]) + 1 + octave) + PITCH_CLASS[m[1]!]! + (m[2] === "s" ? 1 : m[2] === "f" ? -1 : 0);
      top = top === null ? midi : Math.max(top, midi);
    }
    if (top !== null) for (let i = 0; i < times; i++) out.push(top);
  }
  return out;
}

/** The intervals between successive distinct pitches (repeats merged). */
export function contour(midi: readonly number[]): number[] {
  const out: number[] = [];
  let prev: number | undefined;
  for (const m of midi) {
    if (prev !== undefined && m !== prev) out.push(m - prev);
    if (m !== prev) prev = m;
  }
  return out;
}

// ---------------------------------------------------------------- famous openings
//
// The openings of famous film (and a few game) themes, stored ONLY as
// interval patterns (semitones between successive distinct notes), never as
// the pieces. A score's melodic line
// fails if any run of it follows one of these exactly (RUN notes, or the whole
// pattern when it is shorter), or closely (CLOSE_RUN notes, each interval
// within a semitone and in the same direction). A stretch of a famous tune
// that only walks the scale (every interval a step) is any scale, not that
// tune, so it is not compared; a close comparison needs at least two leaps
// (a third or more) in the famous stretch, the shapes that make a tune its own.

export interface FamousOpening {
  name: string;
  intervals: readonly number[];
}

export const FAMOUS: readonly FamousOpening[] = [
  { name: "Star Wars (Main Title)", intervals: [5, 7, -2, -1, -2, 10, -5, -2, -1, -2, 10, -5, -2, -1, 1, -3] },
  { name: "The Imperial March", intervals: [-4, 7, -3, -4, 7, -3] },
  { name: "Raiders March", intervals: [1, 2, 5, -10, 2, 1, 2, 2, 2, 6, -8, 2, 1, 2, 2] },
  { name: "Jurassic Park (Theme)", intervals: [-1, 1, -5, -2, 7, -1, 1, -5, -2, 7, -1, 1, 2, -7] },
  { name: "Close Encounters (five tones)", intervals: [2, -4, -12, 7] },
  { name: "He's a Pirate", intervals: [3, 2, 2, 1, 2, -3, -2, -2, 2] },
  { name: "Concerning Hobbits", intervals: [2, 2, 3, -3, -2, 2, -2, -2] },
  { name: "Over the Rainbow", intervals: [12, -1, -4, 2, 2, 1] },
  { name: "The Godfather (Love Theme)", intervals: [5, 3, -1, -2, 3, -3, 2, -2, -4, 2, -3] },
  { name: "Game of Thrones (Main Title)", intervals: [-7, 3, 2, 2, -7, 3, 2] },
  { name: "Mission: Impossible", intervals: [3, 2, -5, -2, 1] },
  { name: "Zelda's Lullaby", intervals: [3, -5, -2, 2, 2, 3, -5] },
];

/** Notes compared for a close (within a semitone) imitation. */
export const CLOSE_RUN = 8;

/** The first famous opening `melody` follows exactly or closely, or null. */
export function imitates(melody: readonly number[], tunes: readonly FamousOpening[] = FAMOUS): { tune: string; at: number; how: "exact" | "close" } | null {
  const mine = contour(melody);
  for (const t of tunes) {
    const theirs = t.intervals;
    const n = Math.min(RUN - 1, theirs.length);
    for (let j = 0; j + n <= theirs.length; j++) {
      const w = theirs.slice(j, j + n);
      if (w.every((x) => Math.abs(x) <= 2)) continue;
      for (let i = 0; i + n <= mine.length; i++) {
        let same = true;
        for (let k = 0; k < n && same; k++) same = mine[i + k] === w[k];
        if (same) return { tune: t.name, at: i, how: "exact" };
      }
    }
    const c = CLOSE_RUN - 1;
    if (theirs.length < c) continue;
    for (let j = 0; j + c <= theirs.length; j++) {
      if (theirs.slice(j, j + c).filter((x) => Math.abs(x) >= 3).length < 2) continue;
      for (let i = 0; i + c <= mine.length; i++) {
        let near = true;
        for (let k = 0; k < c && near; k++) {
          const a = mine[i + k]!;
          const b = theirs[j + k]!;
          near = Math.sign(a) === Math.sign(b) && Math.abs(a - b) <= 1;
        }
        if (near) return { tune: t.name, at: i, how: "close" };
      }
    }
  }
  return null;
}
