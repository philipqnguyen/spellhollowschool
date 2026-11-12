// Sound and music (docs/architecture.md "Sound and music"): the one audio
// object game code calls. Import it anywhere:
//
//   import { audio } from "../audio";
//   audio.play("ui.open");
//   audio.playAt("spell.torpeo", [x, y, z], { owner: casterId });
//
// Every sound is named by an id of the catalogue (catalogue.ts); where the
// player is goes in once per HUD tick (`audio.setPlace(placeOf(...))`) and the
// engine picks the ambience, the music, the file banks and the reverb from it
// (places.ts). Nothing plays before the first user gesture.

import { createAudio, type GameAudio } from "./engine";

export const audio: GameAudio = createAudio();

export type { GameAudio } from "./engine";
export type { AudioApi, AudioStats, LoopHandle, MusicCue, PlayOpts, PlacedEmitter, Vec3 } from "./types";
export type { AmbienceId, MusicId, SoundId, StingerId } from "./catalogue";
export { isSoundId } from "./catalogue";
export { VOICE_CAPS, HEAR, STRIDE_M } from "./rules";
export { Strides } from "./engine/logic";
export { footSurface, placeOf, emittersIn, isNight, entranceSound, ZONE_DOOR_SOUND, CLOCK_BELL, type AudioPlace, type FootSurface, type PlaceInput } from "./places";
