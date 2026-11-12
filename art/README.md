# SpellHollow School: art

The art sources the Blender build reads that cannot be made from code: the
hair source meshes and the body shape targets, with their credits. Licensed
CC BY-SA 4.0 (see `LICENSE`); the licence covers contributors' own work only
and applies to this `art/` folder only (the rest of the repository is
MIT, see `../LICENSE`).

Only sources are committed here: everything the build regenerates is
gitignored. `docs/art/repo-layout.md` lists every path, what makes it and from
what.

## Committed here (sources)

| Path | What | Made by |
| --- | --- | --- |
| `characters/hair_meshy/<style>_<body>.npz` | The hair source meshes the shipped hair is built from | `tools/blender/characters/prepare_hair_meshy.py`, run by hand from Meshy guides that live only in the gitignored `vendor_assets/`; contributors cannot remake them, so they are committed |
| `characters/shape_targets/<body>.npz` | The bodies' shape targets (the CC BY sculpts' proportions on our mesh) | `tools/blender/characters/wrap_sculpts.py`, run by hand from the sculpts, which are not in the repository; committed for the same reason |
| `CREDITS.md`, `CREDITS-makehuman.md` | Third-party credits: the sculpts, the 11 Meshy hair meshes (each generated model with its SHA-256), the Quaternius clips, MakeHuman, the fonts. `data/credits.json` holds the same credits for the game and the built assets; `client/scripts/credits.test.mjs` checks they agree | by hand |
| `LICENSE`, `README.md`, `.gitignore` | This folder's licence and notes | by hand |

## Generated here (gitignored, never committed)

The build saves these here and some scripts read them back (the character
checks and hair tools open `characters/<body>.blend`); `git status` never
shows them.

| Path | Rebuild |
| --- | --- |
| `characters/body_a.blend`, `characters/body_b.blend` | `tools/blender/build_art_blends.sh characters` |
| `characters/<body>_mh.blend`, `_current.blend`, `_sculpt_<variant>.blend` (comparison builds) | `build_characters.py` with `--body-shape none`, `--body-source current` or `--shape-variant <v>` (the exact commands are in `docs/art/repo-layout.md`; `build_art_blends.sh` does not make these) |
| `world/castle_greybox.blend`, `world/furniture.blend`, `world/dorm_house_{a,b,c,d}.blend`, `world/<room cell>.blend` (`class_charms`, `duel_arena` and the other class rooms) | `tools/blender/build_art_blends.sh world` |
| `vfx/vfx.blend` | `tools/blender/build_art_blends.sh vfx` |
| `renders/**` | the review-render commands in `tools/blender/README.md` (review stills only; nothing reads them) |

`build_art_blends.sh` needs the pinned Blender and, for the characters, the
pinned MPFB: see `tools/blender/README.md`, "Pinned Blender and MPFB". The
exact per-file commands are in `docs/art/repo-layout.md`.

The `.blend` files are for inspection: the scripts are the source, and every
build overwrites them. A change made by hand in a `.blend` is lost on the next
build; make it in the scripts or data instead.
