# Sound credits

Every recorded sound under `client/public/audio/` is CC0 (public domain
dedication, https://creativecommons.org/publicdomain/zero/1.0/). CC0 asks
for no credit; we give it anyway, with thanks. The per-file record (source,
original file, download checksum, our edits) is `data/audio/sources.json`;
the conversion is `tools/audio/convert.py`. The table below also names the
second sources of mixed sounds, which `sources.json` records only in each
file's `edits`.

The game's Credits panel and `client/public/assets/CREDITS.md` take their
sound credits from the table below (`client/scripts/credits.mjs` reads it:
keep its three columns), and `client/scripts/credits.test.mjs` checks that it
names every file and source in `sources.json`.

All other sounds (interface, spells, classes, duels, brooms, owls' hoots,
potions, bells, the room tones and the music) are synthesized in the game's
own code (`client/src/audio/sfx/`, `client/src/audio/music/`).

| Author | Source (CC0) | Our files |
| --- | --- | --- |
| AntumDeluge (Jordan Irwin) | https://opengameart.org/content/fire-crackling | `sfx/sprinkle/fire_pop_0.mp3`, `sfx/sprinkle/fire_pop_1.mp3`, `sfx/sprinkle/fire_pop_2.mp3` |
| AntumDeluge (Jordan Irwin), from 'fast simple chop 5' by dave.des (freesound 127197, CC0) | https://opengameart.org/content/large-wings-flap | `sfx/mail/flutter_0.mp3`, `sfx/mail/flutter_1.mp3`, `sfx/mail/send_0.mp3`, `sfx/mail/send_1.mp3` |
| Corsica_S (recordings, released as CC0 with permission); cut by qubodup (Iwan Gabovitch) | https://opengameart.org/content/42-snow-and-gravel-footsteps | `sfx/foot/gravel_0.mp3`, `sfx/foot/gravel_1.mp3`, `sfx/foot/gravel_2.mp3`, `sfx/foot/gravel_3.mp3`, `sfx/foot/gravel_4.mp3`, `sfx/foot/gravel_5.mp3` |
| Fantozzi (recordings, freesound.org pack 10338, CC0); cut by qubodup (Iwan Gabovitch) | https://opengameart.org/content/fantozzis-footsteps-grasssand-stone | `sfx/foot/grass_0.mp3`, `sfx/foot/grass_1.mp3`, `sfx/foot/grass_2.mp3`, `sfx/foot/grass_3.mp3`, `sfx/foot/grass_4.mp3`, `sfx/foot/grass_5.mp3`, `sfx/foot/land_0.mp3`, `sfx/foot/land_1.mp3` |
| isaiah658 | https://opengameart.org/content/ambient-bird-sounds | `sfx/sprinkle/bird_0.mp3`, `sfx/sprinkle/bird_1.mp3`, `sfx/sprinkle/bird_2.mp3`, `sfx/sprinkle/bird_3.mp3` |
| Kenney (Kenney Vleugels, kenney.nl) | https://opengameart.org/content/50-rpg-sound-effects | `sfx/craft/search_1.mp3`, `sfx/craft/station_0.mp3`, `sfx/craft/station_1.mp3`, `sfx/door/painting_0.mp3`, `sfx/door/painting_1.mp3`, `sfx/door/portrait_0.mp3`, `sfx/door/tapestry_0.mp3`, `sfx/door/tapestry_1.mp3`, `sfx/foot/carpet_0.mp3`, `sfx/foot/carpet_1.mp3`, `sfx/foot/carpet_2.mp3`, `sfx/foot/carpet_3.mp3`, `sfx/foot/wood_0.mp3`, `sfx/foot/wood_1.mp3`, `sfx/foot/wood_2.mp3`, `sfx/foot/wood_3.mp3`, `sfx/foot/wood_4.mp3`, `sfx/sprinkle/creak_0.mp3`, `sfx/sprinkle/creak_1.mp3`, `sfx/sprinkle/creak_2.mp3`, `sfx/ui/coins_in_2.mp3`, `sfx/ui/coins_out_2.mp3`, `sfx/ui/equip_0.mp3`, `sfx/ui/equip_1.mp3`, `sfx/ui/equip_2.mp3`, `sfx/ui/equip_3.mp3`, `sfx/ui/page_0.mp3`, `sfx/ui/page_1.mp3`, `sfx/ui/page_2.mp3` |
| Luckius | https://opengameart.org/content/various-paper-sound-effects | `sfx/mail/send_0.mp3`, `sfx/mail/send_1.mp3`, `sfx/ui/parchment_close_0.mp3`, `sfx/ui/parchment_close_1.mp3`, `sfx/ui/parchment_open_0.mp3`, `sfx/ui/parchment_open_1.mp3` |
| OwlishMedia | https://opengameart.org/content/sound-effects-pack | `sfx/foot/stone_0.mp3`, `sfx/foot/stone_1.mp3`, `sfx/foot/stone_2.mp3`, `sfx/foot/stone_3.mp3`, `sfx/foot/stone_4.mp3`, `sfx/foot/stone_5.mp3` |
| OwlishMedia | https://opengameart.org/content/202-more-sound-effects | `sfx/potion/drink_0.mp3`, `sfx/potion/drink_1.mp3`, `sfx/potion/drink_2.mp3` |
| PagDev | https://opengameart.org/content/fireplace-sound-loop | `sfx/amb/hearth.mp3` |
| qubodup (Iwan Gabovitch) | https://opengameart.org/content/door-open-door-close-set | `sfx/door/double_0.mp3`, `sfx/door/heavy_0.mp3`, `sfx/door/heavy_1.mp3` |
| qubodup (Iwan Gabovitch) | https://opengameart.org/content/door-open-door-close | `sfx/door/wood_0.mp3`, `sfx/door/wood_1.mp3`, `sfx/door/wood_2.mp3` |
| RandomMind | https://opengameart.org/content/sea-and-river-wave-sounds | `sfx/amb/lake_night.mp3` |
| rubberduck | https://opengameart.org/content/100-cc0-metal-and-wood-sfx | `sfx/door/barrels_0.mp3`, `sfx/door/panel_0.mp3` |
| rubberduck | https://opengameart.org/content/30-cc0-sfx-loops | `sfx/door/barrels_0.mp3` |
| rubberduck | https://opengameart.org/content/80-cc0-rpg-sfx | `sfx/craft/station_0.mp3`, `sfx/ui/coins_in_0.mp3`, `sfx/ui/coins_in_1.mp3`, `sfx/ui/coins_out_0.mp3`, `sfx/ui/coins_out_1.mp3` |
| rubberduck | https://opengameart.org/content/100-cc0-sfx | `sfx/mail/collect_0.mp3`, `sfx/mail/collect_1.mp3` |
| StarNinjas | https://opengameart.org/content/10-book-page-flips | `sfx/ui/page_3.mp3` |
| Thimras | https://opengameart.org/content/park-ambiences | `sfx/amb/courtyard_day.mp3`, `sfx/amb/fountain.mp3` |
| TinyWorlds | https://opengameart.org/content/different-steps-on-wood-stone-leaves-gravel-and-mud | `sfx/craft/search_0.mp3` |
| Wolfgang_ | https://opengameart.org/content/crickets-ambient-noise-loopable | `sfx/amb/courtyard_night.mp3` |
| zeroisnotnull | https://opengameart.org/content/crow-caw | `sfx/sprinkle/crow_0.mp3`, `sfx/sprinkle/crow_1.mp3` |
