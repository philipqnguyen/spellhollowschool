# MakeHuman / MPFB credits and licence findings

The character build (`tools/blender/characters/charlib/mh_source.py`,
`mh_body.py`) builds the base bodies from the MakeHuman base mesh and its
targets, rig definition, weights and skin masks, generated with MPFB 2
(MakeHuman's Blender add-on) and then restyled and re-rigged by our own
scripts. This file records what licence each part carries, checked on
2026-10-24 against primary sources; the "What we use" table was corrected on
2026-11-10 against what the build reads (`mh_source.data_path` calls).

## What we use

| Part | Where it comes from | Licence | Used? |
| --- | --- | --- | --- |
| Base mesh (`data/3dobjs/base.obj`) and mesh metadata bundled with MPFB | MPFB 2.0.18 from extensions.blender.org | CC0 1.0 | Yes |
| Targets and modifiers bundled with MPFB (`data/targets/**`, incl. macrodetails, the detail targets of the face sliders and the expression units under `targets/expression/units/`) | same | CC0 1.0 | Yes |
| Rigify rig definitions and skin weights bundled with MPFB (`data/rigs/rigify/rig.<name>.json`, `weights.<name>.json`) | same | CC0 1.0 | Yes: the rig locates MakeHuman's joints on the mesh so it can be posed onto our skeleton, and the weights pose it and become our bodies' skin weights (renamed onto our bones). Our own skeleton replaces the rig itself |
| Skin masks bundled with MPFB (`data/textures/mpfb_<name>.jpg`) | same | CC0 1.0 | Yes, as masks only: the lips, mouth, nails and ears regions of our painted atlas are cut from them. No MakeHuman skin texture is shipped |
| Humans generated with MPFB (meshes, exports, saved files) | our script plus the CC0 assets | Ours. MakeHuman claims no rights in output | Yes |
| MPFB add-on code (Python) | same | GPL-3.0-or-later | Run as a tool only. It is not copied into the repository |
| MPFB bundled poses, expression presets, skin materials, proxies | same | CC0 1.0 | No. We use our own skeleton, poses, materials and atlas |
| MakeHuman clothes, hair, skins, eyebrows and other MHCLO/MHMAT assets | MakeHuman / asset packs | CC0 or CC-BY depending on the pack | Never used |
| Third-party asset-repository / community assets | asset packs, community uploads | Per-asset: CC0, CC-BY or AGPL. Some packs are CC-BY, e.g. Animal 02-04 targets by JALdMIC, Bodyparts 02/03/06, Dress 02/03, Equipment 02 | Never used |

## Findings from the sources

1. **Bundled assets are CC0.** The MPFB repository `LICENSE.md`, section C,
   lists "the base mesh and proxies; targets and modifiers; textures; clothes
   (any MHCLO-based asset); rigs, poses and expressions; JSON data with mesh
   information" as released under CC0 1.0 Universal. The full CC0 text is in
   `LICENSE.ASSETS.md`.
   - https://github.com/makehumancommunity/mpfb2/blob/master/LICENSE.md
   - https://github.com/makehumancommunity/mpfb2/blob/master/LICENSE.ASSETS.md
2. **Add-on code is GPL.** The same file, section B: "The MPFB source ... is
   released under GPLv3" (full text in `LICENSE.CODE.md`). The Blender extensions
   platform lists MPFB 2.0.18 as `SPDX:GPL-3.0-or-later`, and so does its
   `blender_manifest.toml`.
   - https://github.com/makehumancommunity/mpfb2/blob/master/LICENSE.CODE.md
   - https://extensions.blender.org/add-ons/mpfb/
3. **Output is not covered by the GPL.** Section D: "no output from MPFB
   contains any trace of program logic ... regardless of whether you use the UI
   as such or if you call functions of MPFB via a script ... the MakeHuman team
   makes no claim whatsoever over output such as exports to files, graphical
   data generated via scripting or plugins, renderings, screenshots, saved model
   files." The MPFB FAQ says the same: "MPFB-generated characters can ship in
   closed-source games: the GPL applies to the Blender addon code, not to
   characters or assets you export."
   - https://github.com/makehumancommunity/mpfb2/blob/master/LICENSE.md (section D)
   - http://static.makehumancommunity.org/mpfb/faq/use_in_closed_source.html
4. **MakeHuman uses the same split.** MakeHuman's code is AGPL and its bundled
   assets are CC0. Its `LICENSE.md` also warns: "If you use a third part asset,
   such as one downloaded from the asset repositories, it is your own
   responsibility to make sure you abide by its specific license."
   - https://github.com/makehumancommunity/makehuman/blob/master/LICENSE.md
   - https://github.com/makehumancommunity/makehuman/blob/master/LICENSE.ASSETS.md
5. **Community licence page and FAQ.** "All core assets are shared under
   Creative Commons, CC0 ... You do not need to pay anything, include any
   copyright notices nor give attribution." The FAQ adds that the core assets
   may be used "through MakeHuman or copied through some other means", including
   to "build a character generator of your own".
   - http://static.makehumancommunity.org/about/license.html
   - http://static.makehumancommunity.org/makehuman/faq/are_makehuman_files_free.html
6. **Asset packs do not all share one licence.** The asset-pack page lists
   "Target assets shared under CC-BY" and "Mesh assets shared under CC-BY" next
   to the CC0 packs. We do not download or use any asset pack, including the CC0
   "MakeHuman system assets" pack (proxies, eyes, teeth). The MPFB built-in data
   is enough for the base body.
   - http://static.makehumancommunity.org/assets/assetpacks.html

## Compatibility with our licences

- The art (`art/`) is CC BY-SA 4.0. CC0 material can be included in it and
  relicensed as part of a derivative work. No attribution is required, but we
  give it here anyway.
- The code (everything outside `art/`) is MIT. Our build scripts import MPFB (GPL-3.0-or-later)
  at build time inside Blender, which is itself GPL. MIT is GPL-compatible, so
  the scripts may run together with MPFB; the GPL covers MPFB, not our scripts
  or the assets they export. The MPFB code is not vendored into
  the repository. Each developer installs it into the isolated Blender user
  directory (`tools/blender/install_mpfb.sh`, pinned in
  `tools/blender/MPFB_VERSION`).

## Provenance of the copy we use

- MPFB 2.0.18, build 20261010, downloaded from
  https://extensions.blender.org/download/sha256:c075b3e9b94b8e5556a5f03f4653602d02ea306de08126e8f55ed6dee28a2dad/add-on-mpfb-v2.0.18.zip
  (sha256 `c075b3e9b94b8e5556a5f03f4653602d02ea306de08126e8f55ed6dee28a2dad`, checked against the platform's API listing).
- Pinned in `tools/blender/MPFB_VERSION` (version, sha256 and URL) and
  installed by `tools/blender/install_mpfb.sh` only into the gitignored
  `.blender-user/extensions/user_default/mpfb` at the repository root (or
  `$BLENDER_USER_RESOURCES`), never into the user's normal Blender
  configuration.

## Credit

The base body topology, shape keys, expressions and skin weights are derived
from MakeHuman (www.makehumancommunity.org) by the MakeHuman team, released
under CC0 1.0.
