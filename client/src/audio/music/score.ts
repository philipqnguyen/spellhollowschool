// The score format (docs/architecture.md "Sound and music"). Our music is written, not
// generated: each piece is one file in music/scores/ exporting a `Score`, played
// at runtime by the music engine with WebAudio voices (no audio files). The
// engine compiles a score to timed notes once, schedules them a fraction of a
// second ahead of the audio clock, and loops the form with a rest between
// plays.
//
// Notation, one string per part (`notes`), tokens separated by spaces:
//
//   c4  fs4  bf3        a note: letter, s (sharp) or f (flat), octave; c4 is middle C
//   c4:2  e4:.5         a duration in units (the part's `unit` beats, default 1)
//   .  .:2              a rest
//   ~:2                 holds the previous note longer
//   c4+e4+g4            a chord struck together
//   c5!  c5?            accented / soft
//   e4:.5*4             repeated
//   |                   a bar line: the engine checks every bar adds up to the meter
//
// A section's `chords` (one bar per `|`, e.g. `Em | C | G D | B7`) feeds the
// generated accompaniment (`pad`, `bass`, `arp`), so a piece can be written
// as melody plus changes and only the lines that matter spelled out.

import type { MusicId, StingerId } from "../catalogue";

/** The palette: soft, plucked and bell-like sounds synthesize well; a full orchestra does not. */
export const INSTRUMENTS = [
  // plucked and struck
  "harp",
  "celesta",
  "musicbox",
  "glock",
  "pizz",
  // woodwinds
  "flute",
  "whistle",
  "clarinet",
  "oboe",
  "bassoon",
  // strings
  "fiddle",
  "strings",
  "cello",
  "bass",
  // soft brass and voices
  "horn",
  "choir",
  // light percussion (folk and duels)
  "frame",
  "timpani",
  "shaker",
] as const;
export type Instrument = (typeof INSTRUMENTS)[number];

export type Style = "orchestral" | "folk" | "celesta" | "energetic";

export interface Part {
  inst: Instrument;
  notes: string;
  /** Beats per bare token (default 1). */
  unit?: number;
  /** 0..1 (default 0.7). */
  vel?: number;
  /** Transpose by octaves. */
  octave?: number;
  /** Dropped on Low quality and first when the note cap is reached (counter-lines, doublings). */
  optional?: boolean;
  /** Plays only in the day or the night variant. */
  only?: "day" | "night";
}

export interface Section {
  /** What the composer calls it ("A: the theme on flute"). */
  name: string;
  /** One bar per `|`; several chords in a bar split it evenly. */
  chords?: string;
  parts: Part[];
  /** A sustained chord bed following the changes. */
  pad?: { inst: Instrument; vel?: number; octave?: number; optional?: boolean };
  bass?: { inst: Instrument; pattern: "root" | "root_fifth" | "walk" | "pedal"; vel?: number };
  /** Chord tones by index from the chord's root at `octave` (index past the chord: an octave up), one per `unit` (default half a beat), `.` a rest: `0 1 2 1`. */
  arp?: { inst: Instrument; pattern: string; unit?: number; vel?: number; optional?: boolean; /** The octave of its lowest root (default 4). */ octave?: number };
}

/** How the night variant differs: slower, quieter, some voices swapped or gone. */
export interface Variant {
  /** Tempo multiplier (night: 0.85..1). */
  tempo?: number;
  /** Velocity multiplier. */
  vel?: number;
  /** Instrument swaps (null: silent): `{ fiddle: "flute", frame: null }`. */
  swap?: Partial<Record<Instrument, Instrument | null>>;
}

export interface Score {
  id: MusicId | StingerId;
  /** Our own title for it. */
  title: string;
  style: Style;
  /**
   * Beats per minute, a beat being the meter's note value: quarters in 4/4
   * and 3/4, eighths in 6/8 (a 6/8 jig at a dotted-quarter pulse of 68 is
   * bpm 204; compile.ts `pulseBpm` gives the felt pulse the brief means).
   */
  bpm: number;
  /** Beats per bar and the beat's note value. */
  meter: readonly [number, 2 | 4 | 8];
  /** 0..0.3: how late off-beat notes fall (folk lilt). */
  swing?: number;
  /** Reverb: the hall the piece wants, as a tail length (s, default 2.4); the engine's one reverb follows it with its send. */
  space?: number;
  /** A level trim (dB) so every piece sits at its loudness target (measured by render.mjs). */
  level?: number;
  sections: Readonly<Record<string, Section>>;
  /** Section names in playing order. */
  form: readonly string[];
  /** The form index the loop returns to (default 0: an intro can be skipped on repeats). */
  loopFrom?: number;
  /** Silence after each full play (s); default rules.ts MUSIC_REST_S. Stingers: none. */
  restS?: readonly [number, number];
  night?: Variant;
}
