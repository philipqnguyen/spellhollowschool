// The CC0 recordings' definitions (docs/architecture.md
// "Sound and music"): which committed files (client/public/audio/, served as
// /audio/...) play for each id, one picked per play. Every file is recorded in
// data/audio/sources.json with its source, author, licence and our edits
// (written by tools/audio/convert.py from tools/audio/manifest.json); the
// tests check the two agree.
//
// Levels: the converter leaves one-shots at an active RMS of -20 dBFS (peak
// <= -1 dBFS) and loops at -20 to -28 LUFS, so the gains here are the mix:
// footsteps well under everything, doors and entrances clear, the interface's
// coins and paper modest (the review brought them within 3 dB of the
// synthesized interface sounds, and grass and gravel within 3 dB of stone).
// MP3 files carry encoder padding, so none is marked seamless (the engine
// crossfades a looping file's ends).

import type { SoundDef, SoundDefs } from "../types";

const file = (files: readonly string[], gain = 1): SoundDef => ({ kind: "file", files, gain });
const n = (stem: string, count: number): string[] => Array.from({ length: count }, (_, i) => `sfx/${stem}_${i}.mp3`);

export const FILE_DEFS: SoundDefs = {
  // Clean heel-toe clicks, peakier than the gritty steps they replaced: the
  // higher gain brings them level with boards (about -32 LUFS momentary).
  "foot.stone": file(n("foot/stone", 6), 0.6),
  "foot.wood": file(n("foot/wood", 5), 0.42),
  "foot.carpet": file(n("foot/carpet", 4), 0.45),
  "foot.grass": file(n("foot/grass", 6), 0.55),
  "foot.gravel": file(n("foot/gravel", 6), 0.48),
  "foot.land": file(n("foot/land", 2), 0.6),

  "door.heavy": file(n("door/heavy", 2), 0.8),
  "door.wood": file(n("door/wood", 3), 0.75),
  "entrance.portrait": file(n("door/portrait", 1), 0.8),
  "entrance.double": file(n("door/double", 1), 0.8),
  "entrance.tapestry": file(n("door/tapestry", 2), 0.7),
  "entrance.panel": file(n("door/panel", 1), 0.75),
  "entrance.painting": file(n("door/painting", 2), 0.75),
  "entrance.barrels": file(n("door/barrels", 1), 0.75),

  "ui.coins_in": file(n("ui/coins_in", 3), 0.75),
  "ui.coins_out": file(n("ui/coins_out", 3), 0.75),
  "ui.equip": file(n("ui/equip", 4), 0.6),
  "ui.parchment_open": file(n("ui/parchment_open", 2), 0.6),
  "ui.parchment_close": file(n("ui/parchment_close", 2), 0.7),
  "ui.page": file(n("ui/page", 4), 0.7),

  "craft.station_open": file(n("craft/station", 2), 0.6),
  "find.search": file(n("craft/search", 2), 0.55),
  "potion.drink": file(n("potion/drink", 3), 0.6),
  "mail.send": file(n("mail/send", 2), 0.55),
  "mail.collect": file(n("mail/collect", 2), 0.55),
  "owl.flutter": file(n("mail/flutter", 2), 0.6),

  // Sprinkles: a bed scales them again by its own sprinkle gain.
  "sprinkle.page": file(n("ui/page", 4), 0.8),
  "sprinkle.bird": file(n("sprinkle/bird", 4), 0.8),
  "sprinkle.crow": file(n("sprinkle/crow", 2), 0.8),
  "sprinkle.fire_pop": file(n("sprinkle/fire_pop", 3), 0.8),
  "sprinkle.creak": file(n("sprinkle/creak", 3), 0.8),

  // Loops: emitters (3D, at -20 LUFS) and the recorded outdoor beds (-26 to -28 LUFS).
  "loop.fountain": file(["sfx/amb/fountain.mp3"], 0.8),
  "loop.hearth": file(["sfx/amb/hearth.mp3"], 0.8),
  "amb.courtyard_day": file(["sfx/amb/courtyard_day.mp3"], 1),
  "amb.courtyard_night": file(["sfx/amb/courtyard_night.mp3"], 1),
  "loop.lake": file(["sfx/amb/lake_night.mp3"], 1),
};
