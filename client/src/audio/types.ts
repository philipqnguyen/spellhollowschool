// The audio contracts (docs/architecture.md "Sound and music"): the API game
// code calls, what an effect's definition looks like (sfx/defs.ts),
// and what the music engine offers the audio engine (music/index.ts). Types
// only.

import type { Quality } from "../settings";
import type { AudioSettings } from "../settings";
import type { AmbienceId, EffectId, MusicId, SoundId, StingerId } from "./catalogue";
import type { AreaKey, AudioPlace, FootSurface } from "./places";

export type Vec3 = readonly [number, number, number];

export interface PlayOpts {
  /** Extra gain, 0..1 (default 1), on top of the definition's own. */
  gain?: number;
  /** Playback rate (1: as made); pitch follows. The engine adds its own small random spread to one-shots. */
  rate?: number;
  /** Where it sounds in the world (metres): placed through a panner. Omitted: flat (2D), or at the listener for a `spatial` id. */
  at?: Vec3;
  /**
   * The entity making it (ourselves, a remote, an NPC): cooldowns are per
   * owner, and our own sounds are never dropped for the voice cap.
   */
  owner?: number;
  /** Start this many seconds from now (class beats line up with the beat clock). */
  delayS?: number;
  /** A fixed variant (default: random, never the same twice running). */
  variant?: number;
}

/** A looping sound started with `loop` (broom wind, a staircase's grinding). */
export interface LoopHandle {
  setGain(gain: number, fadeS?: number): void;
  setRate(rate: number): void;
  /** 0 (dull, low-passed) .. 1 (bright, open): a filter on the loop. */
  setTone(tone: number): void;
  setPosition(at: Vec3): void;
  stop(fadeS?: number): void;
  readonly playing: boolean;
}

/** For the F3 overlay. */
export interface AudioStats {
  state: "locked" | "running" | "suspended" | "closed";
  /** One-shot and loop voices sounding now (music notes not included). */
  voices: number;
  dropped: number;
  banks: string[];
  ambience: AmbienceId | null;
  music: string | null;
  musicVoices: number;
}

/**
 * The game's one audio object (`audio` in audio/index.ts). Every method is
 * safe to call before the first user gesture: one-shots are dropped,
 * place, ambience and music are remembered and start at the unlock.
 */
export interface AudioApi {
  /** Once, as the game starts: the settings, the quality (voice caps) and the unlock listeners (no AudioContext until a gesture). */
  init(settings: AudioSettings, quality: Quality): void;
  readonly unlocked: boolean;
  /** A one-shot; flat unless `opts.at` is given. */
  play(id: SoundId, opts?: PlayOpts): void;
  /** A one-shot at a world position (3D). */
  playAt(id: SoundId, at: Vec3, opts?: PlayOpts): void;
  /** A loop until `stop()` (a looping id: `loop: true` in the catalogue). */
  loop(id: SoundId, opts?: PlayOpts): LoopHandle;
  /** One footstep of `owner` (self or a remote) on `surface` at `at` (feet). */
  footstep(surface: FootSurface, at: Vec3, run: boolean, owner: number): void;
  /** Where the player is (places.ts `placeOf`): picks the ambience bed, the music cue, the file banks and the reverb. Cheap to call every HUD tick. */
  setPlace(place: AudioPlace): void;
  /** A short musical phrase over the music (the loop ducks under it). */
  stinger(id: StingerId): void;
  /** The listener: the camera's position and look direction (every frame). */
  setListener(at: Vec3, forward: Vec3): void;
  /** The emitters' anchors in the zone shown (places.ts `emittersIn`); [] clears them. */
  setEmitters(emitters: readonly PlacedEmitter[]): void;
  setSettings(settings: AudioSettings): void;
  setQuality(quality: Quality): void;
  stats(): AudioStats;
  dispose(): void;
}

/** A looping emitter at a position (places.ts EMITTERS resolved against the layout). */
export interface PlacedEmitter {
  key: string;
  sound: SoundId;
  at: Vec3;
  /** Heard within this distance (m); started and stopped as the listener comes and goes. */
  radius: number;
  /** Heard only while the place (setPlace) is in one of these castle areas (none: anywhere in range). */
  areas?: readonly AreaKey[];
}

// ---------------------------------------------------------------- the sound definitions (sfx/defs.ts)

/** A one-shot a bed scatters round the listener now and then. */
export interface Sprinkle {
  id: SoundId;
  /** Seconds between two, at random in this range. */
  everyS: readonly [number, number];
  gain: number;
  /** How far from the listener (m), at random in this range; [0, 0]: flat. */
  distanceM: readonly [number, number];
}

export type SoundDef =
  /** CC0 recordings: paths under `public/audio/` (`sfx/foot/stone_0.mp3`), one picked per play. Listed in data/audio/sources.json. */
  | { kind: "file"; files: readonly string[]; gain?: number; /** The file loops without a seam (else the engine crossfades its ends). */ seamless?: boolean }
  /**
   * Synthesized in the repo: `draw` builds WebAudio nodes into `out` from time
   * 0 of an OfflineAudioContext `seconds` long; the engine renders each
   * variant once (on first use, or at the unlock for the core bank) and plays
   * the cached buffer like a file.
   */
  | {
      kind: "synth";
      seconds: number;
      variants?: number;
      channels?: 1 | 2;
      gain?: number;
      seamless?: boolean;
      draw: (ctx: OfflineAudioContext, out: AudioNode, variant: number) => void;
    }
  /** An ambience bed: looping layers plus sprinkles (ambience ids only). */
  | { kind: "bed"; layers: readonly { id: SoundId; gain: number }[]; sprinkles: readonly Sprinkle[] }
  /** Another id's sound, changed (a lower rate for a bigger door). */
  | { kind: "alias"; of: SoundId; rate?: number; gain?: number };

/** sfx/defs.ts: a definition for every effect and ambience id. */
export type SoundDefs = Partial<Record<EffectId, SoundDef>>;

// ---------------------------------------------------------------- the music engine (music/index.ts)

/** What the music plays: a piece and its variant. */
export interface MusicCue {
  id: MusicId;
  night: boolean;
}

export interface MusicStats {
  cue: string | null;
  section: string | null;
  /** Notes sounding now. */
  voices: number;
  /** In the silence between two plays of the piece. */
  resting: boolean;
}

export interface MusicEngine {
  /**
   * Fade the current piece out over `fadeS` and start `cue` (null: silence).
   * The same id with another `night` changes variant at the next section
   * boundary, without a gap.
   */
  setCue(cue: MusicCue | null, fadeS: number): void;
  /** A stinger over the loop (ducks it, then lets it back up). */
  stinger(id: StingerId): void;
  /** Low thins the parts marked optional and drops the reverb's size. */
  setQuality(quality: Quality): void;
  stats(): MusicStats;
  dispose(): void;
}

/** music/index.ts exports this: called by the engine at the unlock with the music bus. */
export type CreateMusicEngine = (ctx: AudioContext, out: AudioNode, quality: Quality) => MusicEngine;
