# Third-party credits

Works by other people that parts of this repository are adapted from. Our own
work stays under this repository's licence (CC BY-SA 4.0, see `LICENSE`); the
adapted parts keep their authors' licence and attribution below. MakeHuman
(CC0) is recorded separately in `CREDITS-makehuman.md`.

`data/credits.json` is the short form of these credits that the game shows
(its Credits panel) and ships beside the built assets
(`client/public/assets/CREDITS.md`, written by `client/scripts/credits.mjs`).
Change both together; `client/scripts/credits.test.mjs` checks that they
name the same works and every committed hair source mesh.

## Base body shapes (`characters/shape_targets/`)

The body trial's base bodies keep our own mesh (topology, skeleton, weights,
UVs, dye mask, regions and shape keys) but take their shape from these
sculpts: `tools/blender/characters/wrap_sculpts.py` wraps our mesh onto each
sculpt and bakes the result as a *shape target* (`<body>.npz`: our vertices'
rest positions, the measurements derived from them and the positions of the
sculpt's eye sockets, nose, mouth, chin and crown, which place and size our
painted face on it). The sculpt files
themselves are not in the repository; the wrap reads them from a local
folder (`--sculpt-dir`).

| File | Work | Author | Link | Licence | Changes |
| --- | --- | --- | --- | --- | --- |
| `characters/shape_targets/body_a.npz` | "Anime boy male model T-pose base basic FREE" | ALexzart | https://skfb.ly/oxIPp | CC BY 4.0 | Adapted: retopologised onto our base mesh, posed, smoothed. Mirrored to exact symmetry, voxel-remeshed and smoothed; groin, chest and gluteal cleft smoothed into neutral forms; eye sockets, lips and ears softened under our painted face and our ear shells; posed from its T-pose into our A-pose and scaled uniformly to 1.78 m. |
| `characters/shape_targets/body_b.npz` | "Anime BaseMesh Sculpt 01" | DJP3D | https://skfb.ly/oPv6y | CC BY 4.0 | Adapted: retopologised onto our base mesh, posed, smoothed. Its four pieces merged, mirrored to exact symmetry, voxel-remeshed and smoothed (facets removed, the face most); groin, chest and gluteal cleft smoothed into neutral forms; ears replaced by our ear shells; posed into our A-pose and scaled uniformly to 1.68 m. |

Attribution text, as the authors' licence asks:

- "Anime boy male model T-pose base basic FREE" (https://skfb.ly/oxIPp) by
  ALexzart is licensed under Creative Commons Attribution 4.0
  (http://creativecommons.org/licenses/by/4.0/). Adapted.
- "Anime BaseMesh Sculpt 01" (https://skfb.ly/oPv6y) by DJP3D is licensed
  under Creative Commons Attribution 4.0
  (http://creativecommons.org/licenses/by/4.0/). Adapted.

## Review renders (formerly `renders/body-trial/wrap/`)

These review renders are not in the repository (`docs/art/repo-layout.md`);
the credit stays for anyone who remakes them.


`compare_body_a.png` and `compare_body_b.png` show the sculpts above (grey, as
imported, scaled to our heights) next to our wrapped bodies. `compare_body_b.png`
also shows a third sculpt and our body wrapped onto it, for comparison only (no
geometry of it is committed); so do the sheets in `renders/body-trial/face/greywax/`
(our head and face on that wrap):

- "Anime sculpt Base Body" (https://skfb.ly/6URQC) by GreyWax is licensed
  under Creative Commons Attribution 4.0
  (http://creativecommons.org/licenses/by/4.0/). Shown in a render, and adapted
  (retopologised onto our base mesh, posed, smoothed) in that render only.

## Hair (the eleven hairstyles)

Every hairstyle in the game (`data/characters/creator.json` ids 14-24) is
built from a hair source mesh in `characters/hair_meshy/`, derived from a
Meshy-generated model (meshy.ai), generated for this project by Philip
Nguyen. Each source mesh is the hair of one generated bust: segmented from
it, fitted to the body's own head, cleaned, re-topologised and cleared of the
body by `tools/blender/characters/prepare_hair_meshy.py`, from which
`tools/blender/characters/charlib/hair_meshy.py` builds the shipped files.
The generated models themselves (and the concept images they were generated
from) are not in the repository. The meta of each source mesh records the
generated model it came from and that file's SHA-256 (below, checked against
the files on 2026-11-10). The first six styles also keep the lock meshes of
`charlib/hair.py` (our own, shaped by eye against the same models) as their
fallback; the last five have none.

| Source mesh (`characters/hair_meshy/`) | Style (creator id) | Generated model | SHA-256 of the generated model | Changes |
| --- | --- | --- | --- | --- |
| `short_tousled_body_a.npz` | Short Tousled (14) | `g_m1_b.glb` | `276af378b4f23ebc4ec7104eedd55de9521a0debe7fd6b7401b0513e99950302` | Segmented, fitted, cleaned, re-topologised (all styles) |
| `side_swept_body_a.npz` | Side-Swept Fringe (15) | `g_m2_r.glb` | `1762a005b64279626978f73e731ac0675a1c977b3bf1fdeb2be074e1ea1b9db2` | As above |
| `shaggy_layers_body_a.npz` | Shaggy Layers (16) | `g_m3_a.glb` | `673eafd798936717c3279b746836a47ee982c15d55ab8db47e4f5352eb6354f3` | As above |
| `long_bangs_body_b.npz` | Long with Bangs (17) | `g_f1_r.glb` | `0bc5a27c9d4983b36d9a356f47a3e5eec7eb7794021a50953be2cf8ec1816926` | As above |
| `ponytail_locks_body_b.npz` | High Ponytail (18) | `g_f2_r.glb` | `e4922da3a7c3e526ab768a92bca5a2540dfaaba6c85539065625e8de6ac76e25` | As above |
| `bob_locks_body_b.npz` | Bob with Front Locks (19) | `g_f3_a.glb` | `702076edbe160592ac7538d6e3aa608dadeb187eb2409fad48395ab19eb25706` | As above |
| `swept_undercut_body_a.npz` | Swept-Back Undercut (20) | `g_m4_a.glb` | `bba72f095741a91eac95da137d124681641972b38409d843780cad35a4dfbb87` | As above; the clipped sides dropped |
| `low_tail_body_a.npz` | Tied-Back Ponytail (21) | `g_m5_a.glb` | `4fcc683fe293b685a3229235a18aad0cd5e27cce8a84142ebcac79e119e4c3c3` | As above |
| `crew_cut_body_a.npz` | Crew Cut (22) | `g_m6_a.glb` | `c0c2a2f4dc6e42c789471b03cbbc3289ed679d8afaf4eef4f7918553cb328e09` | As above |
| `twin_tails_body_b.npz` | Twin Tails (23) | `g_f4_a.glb` | `370553e858832798c7bccf901c7d417c5ed6c64a2fa5d9db655b5fd3c7b22d30` | As above |
| `side_braid_body_b.npz` | Low Side Braid (24) | `g_f5_b.glb` | `f756d76d5977d5d4b53bc288a11741e1e8b5890821eb9a262c7153673bef659c` | As above; the plait is our own built geometry |

How they were made (`docs/art/character-look.md`): the
concepts with Meshy `image_to_image` and `generate_multi_view` on a bald
mannequin bust, referenced on a 2D concept sheet; the models with Meshy
`multi_image_to_3d`, untextured. The source meshes are licensed with the rest
of `art/` (CC BY-SA 4.0).

All of it (the 2D concept sheet, the concepts, including those made with the
"nano-banana" image model offered through Meshy, and the models) was
generated on the maintainer's paid Meshy account, whose terms give the account holder
ownership of the outputs; no attribution to Meshy is required.

## Walk and run (the bodies' `walk` and `run` clips)

The `walk` and `run` clips of both bodies (`characters/body_a.blend`,
`characters/body_b.blend` and the exported bodies) are retargeted from two
clips of the Universal Animation Library by Quaternius. The repository
keeps only those two clips' keys and the rest pose of the bones they move
(`tools/blender/characters/ual/ual1_walk_sprint.json`, extracted by
`tools/blender/characters/extract_ual.py`), with the pack's licence text
unchanged beside them (`tools/blender/characters/ual/License.txt`); no mesh or
other clip of the pack is in the repository.

| Clip | Work | Author | Link | Licence | Changes |
| --- | --- | --- | --- | --- | --- |
| `walk` | `Walk_Loop` (in place), Universal Animation Library 1 (Pro), `UAL1.glb` | Quaternius (@Quaternius) | https://quaternius.com | CC0 1.0 Universal | Retargeted onto our Rigify rig (`tools/blender/characters/charlib/ual.py`) and played 1.25 times faster; the clavicles take half the source's turn; the female body's feet come 4 cm each toward the midline (in both clips) |
| `run` | `Sprint_Loop` (in place), same pack | Quaternius (@Quaternius) | https://quaternius.com | CC0 1.0 Universal | Retargeted as above, at its own rate; the hips come down a little where a planted leg cannot reach |

CC0 asks for no attribution; we give it anyway: "Animations by Quaternius
(https://quaternius.com), CC0 1.0 Universal
(https://creativecommons.org/publicdomain/zero/1.0/)."

## Fonts (the game client)

The client ships two typefaces, unmodified, from the Fontsource packages (`client/package.json`: `@fontsource/cinzel` 5.3.0, `@fontsource/eb-garamond` 5.3.0), served from the game's own host (`client/src/fonts.css`); the licence texts ship as `client/public/fonts/*-OFL.txt`.

| Font | Weights | Copyright | Link | Licence |
| --- | --- | --- | --- | --- |
| Cinzel | 500, 700 | Copyright 2020 The Cinzel Project Authors | https://github.com/NDISCOVER/Cinzel | SIL Open Font License 1.1 |
| EB Garamond | 400, 600, 400 italic | Copyright 2017 The EB Garamond Project Authors | https://github.com/octaviopardo/EBGaramond12 | SIL Open Font License 1.1 |
