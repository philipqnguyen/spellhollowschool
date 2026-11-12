// The audio engine's numbers (docs/architecture.md "Sound and music"): voice
// caps by quality, hearing distances, footstep strides, fades and the music's
// pacing. The music engine reads MUSIC_*.

import type { Quality } from "../settings";

export interface VoiceCaps {
  /** One-shots and loops placed in the world (3D) at once; ours never count against it. */
  world: number;
  /** Flat interface sounds at once. */
  ui: number;
  /** Remote characters whose footsteps are heard (the nearest moving ones). */
  footstepSources: number;
  /** A bed's sprinkles sounding at once. */
  sprinkles: number;
  /** Music notes sounding at once (the music engine thins its optional parts to stay under). */
  musicNotes: number;
  /** The effects bus's convolution reverb (one shared convolver); Low goes dry. */
  reverb: boolean;
}

export const VOICE_CAPS: Readonly<Record<Quality, VoiceCaps>> = {
  low: { world: 8, ui: 3, footstepSources: 3, sprinkles: 1, musicNotes: 14, reverb: false },
  medium: { world: 14, ui: 4, footstepSources: 5, sprinkles: 2, musicNotes: 24, reverb: true },
  high: { world: 20, ui: 4, footstepSources: 8, sprinkles: 3, musicNotes: 32, reverb: true },
};

/** Panner distances (m): full volume within REF, the "inverse" model's rolloff, silent (not started) beyond MAX. */
export const HEAR = { refM: 2, rolloff: 1.1, maxM: 35, footstepsM: 18, spellsM: 35, broomFlybyM: 14, stairsM: 60, stairsRefM: 4 } as const;

/**
 * Metres between two footsteps, matching the characters' walk and run clips
 * (docs/architecture.md, the gait pass): the walk loops every 25 frames
 * (0.83 s) at 3.0 m/s, two steps a loop, so a step each 1.25 m (144 steps a
 * minute); the run every 20 frames (0.67 s) at 6.5 m/s, a step each 2.17 m.
 * The clips play at speed / clip speed, so the metres per step hold at any
 * speed (a speed boost too) and any size.
 */
export const STRIDE_M = { walk: 1.25, run: 2.17 } as const;

/** Seconds. */
export const FADES = {
  /** Ambience beds crossfade as the area changes. */
  ambience: 1.5,
  /** The old piece fades out over this... */
  musicOut: 2.5,
  /** ...and the new one starts this long after the fade began. */
  musicGap: 1.0,
  /** The area must hold this long before the music follows it (doorways and short visits don't flap it). */
  musicSettle: 3.0,
  /** The tab hidden: everything fades out over this, then the context suspends. */
  hide: 0.25,
  /** The tab shown again: back up over this. */
  show: 0.5,
  /** The loop's dip under a stinger (dB) and how fast it comes back (s). */
  stingerDuckDb: -9,
  stingerRelease: 1.5,
} as const;

/** The music's pacing: after a full play of a piece, rest this long (s, at random in the range; a score may set its own) before it plays again. */
export const MUSIC_REST_S: readonly [number, number] = [25, 70];

/** Footstep, door and spell one-shots get a random rate spread of ± this (pitch variety). */
export const RATE_SPREAD = 0.06;

/** CC0 files: the total committed under client/public/audio, and each file (bytes). Music and synthesized sounds download nothing. */
export const FILE_BUDGET = { totalBytes: 6 * 1024 * 1024, perFileBytes: 700 * 1024 } as const;
