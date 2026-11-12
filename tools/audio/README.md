# Sound effects: sources, vetting and conversion

The game's real-world sounds (footsteps, doors, cloth, coins, paper, gulps,
wings, birds, crickets, water, fire) are CC0 recordings, converted to MP3 and
committed under `client/public/audio/sfx/`. Everything else (interface, magic,
classes, duels, brooms, owls' hoots, potions, bells, room tones, wind, frogs,
drips, the music) is synthesized in the client (`client/src/audio/sfx/`,
`client/src/audio/music/`). See the audio section of
[docs/architecture.md](../../docs/architecture.md).

## Files here

| File | What |
| --- | --- |
| `downloads.json` | Every download fetched: its OpenGameArt page, file URL, author, SHA-256, date. All CC0 (checked on each page; packs dual-licensed CC0 and OGA-BY are taken under CC0). |
| `fetch.sh` | Re-fetches the downloads into `.out/audio-downloads/<name>/` (gitignored), one new folder each, checks the checksums, unpacks archives into `<name>/x/`. |
| `manifest.json` | What each committed file is made of: layers (download, file, cut, rate, filter, delay, gain), loop length, loudness target, notes. |
| `convert.py` | Builds the MP3s from the manifest and writes `data/audio/sources.json` (one record per file: source, author, licence, original, checksum, our edits; `mixedWith` for a second source) and `client/public/audio/CREDITS.md`. Standard library and ffmpeg only. |

To rebuild you need `curl`, `unzip`, `bsdtar`, `ffmpeg` and Python 3. Run
`tools/audio/fetch.sh`, then `python3 -I tools/audio/convert.py` (or
`--only <prefix>`).
`client/src/audio/sources.test.ts` and `sfx/sfx.test.ts` check the records,
the files and the budget (6 MB in all, 700 KB a file; today about 2 MB).

## Processing

- One-shots: layers mixed, silence trimmed, 3 ms fade in and 20 ms+ fade out,
  mono, an active RMS of -20 dBFS (or the entry's `rms`) with the peak held at
  -1 dBFS, MP3 VBR (~70 kbps).
- Loops: cut `loop + xfade` seconds, the tail crossfaded into the head (equal
  power) so the loop has no seam, -20 to -28 LUFS integrated (`lufs`), peak
  at most -1 dBFS; 64 kbps mono (emitters) or 96 kbps stereo (beds). MP3's
  encoder padding is left to the engine's crossfaded loops.
- Inspection: each candidate's duration, peak, RMS, loudness, silences and
  full-scale runs were measured with ffmpeg (`astats`, `ebur128`,
  `silencedetect`) and its spectrogram looked at before choosing; the
  converter reports any clipped source runs.

## Vetting notes

Rejected or not used, and why:

- EZduzziteh "Frog Croaks" and "Ribbit Frog Sounds": very quiet and formant-
  shaped like a voice imitating a frog; the frog sprinkle is synthesized.
- IgnasD "Crows singing": a dense chorus over heavy low rumble; one clean caw
  (zeroisnotnull) is used instead.
- Thimras "park ambience wind", Bashar3A "mild wind", IgnasD "Wind": mostly
  microphone buffeting or a single whistle; tower, sky and broom winds are
  synthesized instead.
- HaelDB "cricket ambience": strong low rumble under the crickets; Wolfgang_'s
  loopable crickets are used.
- qubodup "Liquid Bottle Drink Set" swallows: needed +30 dB, which raised the
  room rumble; OwlishMedia's drinking sounds are used.
- Kenney RPG "doorOpen/doorClose": many full-scale (clipped) samples;
  qubodup's room door is used for wooden doors.
- Thimras "park ambience river" is used for the fountain only after a 150 Hz
  high-pass (the city's rumble); "park ambience birds" after 250 Hz.
- RandomMind "Vistula" waves: the source MP3 touches full scale in places;
  low-passed and brought down 14 dB, the lake loop has no audible clipping.
- No pack with a "royalty-free" or no-redistribution licence, no AI audio,
  no freesound download (login required); freesound-origin sounds appear only
  where the uploader re-released them as CC0 on OpenGameArt with the
  recordist's CC0 or permission (Fantozzi's steps, Corsica_S's gravel steps,
  dave.des's chop behind AntumDeluge's wings).
- Kenney's own site needs a scripted download step; his RPG audio pack is the
  copy he posted on OpenGameArt (same files, CC0).
- Fantozzi "StoneL1-3/StoneR1-3" (formerly the castle stone steps):
  gritty outdoor steps, a broadband scuff for their whole length (above
  -20 dB of the peak for 200-265 ms, spectral flatness 0.10-0.13, centroid
  2.2-2.7 kHz); in the castle they read as running on sand. OwlishMedia's
  "hard-footstep" 1-3 (Sound Effects Pack) replace them: heel and toe
  clicks, above -20 dB for 65-100 ms, flatness 0.01-0.02. His
  "hard-footstep4" is a brighter slap 4 dB softer than the other three, and
  his "step1-4" carry a ~250 Hz floor resonance (hollow, like boards); not
  used. Fantozzi's steps stay for grass and the broom landing (outdoors).
- Other stone candidates: TinyWorlds "stone01" (clean, but a single step);
  GboxMikeFozzy's six subway steps (quiet, with the station's noise 26-27
  dB under the step); Thimras "Metal footsteps on concrete" (CC0, but
  metal soles ring with clear partials); rubberduck's footsteps and stones
  (a broad scuff like the old steps).
