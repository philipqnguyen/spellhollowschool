// The music engine (docs/architecture.md "Sound and music").
// The audio engine calls `createMusicEngine` at the unlock with
// the music bus and hands it a cue (places.ts `musicFor`) as the player's
// area settles, and stingers.
//
//   score.ts      the notation and the Score type
//   scores/       one written piece per music and stinger id (our own)
//   compile.ts    a score to timed notes (variants, quality, accompaniment)
//   voices.ts     the instruments (WebAudio), samples.ts the plucked and struck ones' notes
//   engine.ts     the transport: cues, crossfades, rests, stingers, the note cap
//   motifs.ts     the originality guard (tested over every score)
//   listen.html   a dev-only page to hear every piece (and render previews)

import type { Quality } from "../../settings";
import type { CreateMusicEngine, MusicEngine } from "../types";
import { createEngine } from "./engine";

export const createMusicEngine: CreateMusicEngine = (ctx: AudioContext, out: AudioNode, quality: Quality): MusicEngine => createEngine(ctx, out, quality);
