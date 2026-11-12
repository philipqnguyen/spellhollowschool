"""Builds the base bodies, rig, animations, hair and clothing and exports them.

    blender -b --factory-startup --python tools/blender/characters/build_characters.py -- \\
        --assets client/public/assets --art art

Everything is generated from code and the data files (data/items/catalog.json,
data/characters/creator.json); see tools/blender/README.md ("Characters").
Options:

* `--bodies body_a,body_b` (default both)
* `--only bodies,hair,gear,broom` (default all four; the `broom` is
  the broom prop, `characters/broom.glb`, charlib/broom.py: no body is built
  when it is the only one)
* `--items gear/hat/pointed_a,hair/afro` (asset keys; default every item)
* `--no-blend` skips saving art/characters/<body>.blend
* `--fast` skips baking animations (body files then have no clips)

The default is the shipped build (character adoption, 2026-10-25): MakeHuman
bodies wrapped onto the CC BY sculpts' baked shape targets and gear from the
garment shapes, i.e. `--body-source makehuman --body-shape sculpt
--gear-source shapes`. It needs MPFB in the Blender user resources:
`BLENDER_USER_RESOURCES=<worktree>/.blender-user` (see tools/blender/README.md),
at the version pinned in tools/blender/MPFB_VERSION (install it with
tools/blender/install_mpfb.sh); Blender itself must be the major.minor in
tools/blender/BLENDER_VERSION. Both are checked before anything is built.

* `--body-source makehuman|current` (default makehuman): the MakeHuman (MPFB2)
  body fitted to our skeleton (charlib/mh_body.py), or the milestone-1b
  sculpted body (`current`, kept for tests and comparisons; not shipped).
  The pre-wrap MakeHuman body saves as art/characters/<body>_mh.blend, the
  `current` body as <body>_current.blend.
* `--gear-source builders|shapes` (default shapes): `shapes` composes every
  item that has a design in data/items/garment_designs.json from a fitted
  garment shape (docs/art/garment-shapes.md) and also builds the designs not
  in the catalog yet; other items keep their builder. Same output format.
* `--shape-report <file>` writes per-item shape-system stats (JSON)
* `--body-shape none|sculpt` (default sculpt for MakeHuman bodies, none for
  `current`; `none` gives the pre-wrap body, not shipped): `sculpt`
  moves the finished body onto its baked shape target
  (`<shape-dir>/<body>[_<variant>].npz`, made by wrap_sculpts.py from CC BY
  sculpts, see charlib/shape_target.py): same topology, weights, UVs, paint
  and keys, the sculpt's proportions and a skeleton measured off them. The
  shipped build saves art/characters/<body>.blend (a variant:
  <body>_sculpt_<variant>.blend).
* `--shape-dir` (default the repository's art/characters/shape_targets, whatever `--art` is), `--shape-variant`
  (a comparison variant's target, built into a separate assets dir; none ships)

Writes `<assets>/characters/manifest.json` (merged, so partial runs keep the
other entries) and `<assets>/characters/atlas.png`.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from charlib import anim as AN  # noqa: E402
from charlib import atlas as A  # noqa: E402
from charlib import body as BD  # noqa: E402
from charlib import content  # noqa: E402
from charlib import export as EX  # noqa: E402
from charlib import pipeline as PL  # noqa: E402
from charlib import rig as RG  # noqa: E402


def script_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--assets", default="client/public/assets")
    p.add_argument("--art", default="art")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--only", default="bodies,hair,gear,broom")
    p.add_argument("--items", default="")
    p.add_argument("--no-blend", action="store_true")
    p.add_argument("--fast", action="store_true")
    p.add_argument("--body-source", default="makehuman", choices=PL.BODY_SOURCES)
    p.add_argument("--gear-source", choices=("builders", "shapes"), default="shapes")
    p.add_argument("--shape-report", default="")
    p.add_argument("--body-shape", choices=("none", "sculpt"), default=None)
    p.add_argument("--shape-dir", default="")
    p.add_argument("--shape-variant", default="")
    return p.parse_args(argv)


def base_manifest(data: dict, skel) -> dict:
    """Fields that describe the whole set (rewritten on every run)."""
    data.update({
        "version": EX.MANIFEST_VERSION,
        "generator": "tools/blender/characters/build_characters.py",
        "atlas": "characters/atlas.png",
        "tint": {
            "formula": "albedo = texture * ((1 - r - g - b) + r * C0 + g * C1 + b * C2), r/g/b from COLOR_0",
            "body": ["skin_tone", "hair_colour", "eye_colour"],
            "hair": ["hair_colour", None, None],
            "clothing": ["primary", "secondary", "trim"],
        },
        "skeleton": {
            "bones": data.get("skeleton", {}).get("bones") or RG.exported_bone_names(skel),
            "root": "root",
            "sockets": list(RG.SOCKETS),
        },
        "height": {
            "method": "uniform scale of the root bone: scale = height_m / default_height_m",
            "bodies": {b.id: {"default_height_m": b.height, "height_m": list(b.height_range)} for b in content.bodies()},
        },
        "lods": {
            "tiers": ["lod0", "lod1", "lod2", "lod3"],
            "doc": "docs/art/character-adoption.md section 6: every file holds meshes with lod 0-3; the client ranks characters by distance (4 Hz) and a budget governor demotes the farthest",
            "dressed_max_triangles": list(PL.DRESSED_MAX),
            "part_budgets": {k: list(v) for k, v in PL.LOD_BUDGETS.items()},
            "selection": {
                "high": {"lod0": {"rank_max": 8, "distance_m": 12.0}, "lod1_m": 25.0, "lod2": "beyond", "lod3": "budget only"},
                "medium": {"lod0": {"rank_max": 8, "distance_m": 10.0}, "lod1_m": 20.0, "lod2": "beyond", "lod3": "budget only"},
                "low": {"lod0": {"rank_max": 2, "distance_m": 6.0}, "lod1_m": 12.0, "lod2_m": 25.0, "lod3": "beyond"},
            },
            "character_share_triangles": {"high": 1000000, "medium": 650000, "low": 200000},
        },
        "shade": {
            "attribute": "_SHADE",
            "doc": "docs/art/character-adoption.md section 3: VEC4 per vertex (normalized unsigned short in the files), x shade (1 open .. 0.35), y strand (hair), z band phase (hair, 0.5 neutral), w outline weight hint",
        },
        "regions": list(BD.REGIONS),
        "shape_keys": content.shape_key_groups(),
        "animations": AN.clip_meta(),
    })
    return data


def main() -> None:
    sys.path.append(os.path.dirname(HERE))  # tools/blender, for pins
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    args = script_args()
    only = set(args.only.split(","))
    items = set(filter(None, args.items.split(",")))
    assets = os.path.abspath(args.assets)
    art = os.path.abspath(args.art)
    manifest_path = os.path.join(assets, "characters", "manifest.json")
    manifest = EX.read_manifest(manifest_path)
    t0 = time.time()
    if args.body_shape is None:
        args.body_shape = "sculpt" if args.body_source == "makehuman" else "none"
    if args.body_shape != "none" and args.body_source != "makehuman":
        raise SystemExit("--body-shape needs --body-source makehuman")
    if args.body_source == "makehuman" and only & {"bodies", "hair", "gear"}:
        pins.require_mpfb()  # the pinned MPFB (tools/blender/MPFB_VERSION), before minutes of work
    for body_id in (args.bodies.split(",") if only & {"bodies", "hair", "gear"} else []):
        PL.new_scene()
        shape = None
        if args.body_shape == "sculpt":
            from charlib import shape_target as ST

            shape_dir = os.path.abspath(args.shape_dir or os.path.join(content.REPO, ST.DEFAULT_DIR))
            shape = ST.Reshaper(ST.target_path(body_id, shape_dir, args.shape_variant))
        bb = PL.build_body(body_id, bake=not args.fast, source=args.body_source, shape=shape)
        base_manifest(manifest, bb.skel)
        if "bodies" in only:
            content.export_body(bb, assets, manifest)
        if "gear" in only:
            content.build_gear(bb, assets, manifest, items, source=args.gear_source)
        if "hair" in only:
            # After the gear: each hair style measures its fit under every
            # hat (hair_meshy.hat_fits, character-adoption.md section 4.4).
            content.build_hair(bb, assets, manifest, items)
        if not args.no_blend:
            # The shipped build saves <body>.blend (what the checks open by
            # default); the comparison builds keep a suffix.
            if args.body_source == "current":
                suffix = "_current"
            elif args.body_shape != "sculpt":
                suffix = "_mh"
            else:
                suffix = f"_sculpt_{args.shape_variant}" if args.shape_variant else ""
            PL.save_blend(os.path.join(art, "characters", f"{body_id}{suffix}.blend"))
        PL.log(f"{body_id} done ({time.time() - t0:.0f}s)")
    os.makedirs(os.path.join(assets, "characters"), exist_ok=True)
    if "broom" in only:  # The broom prop and its manifest entry
        from charlib import broom as BR

        PL.new_scene()
        entry = BR.export(assets, manifest)
        PL.log(f"exported {entry['file']} ({entry['triangles']} triangles)")
    A.build_image(os.path.join(assets, "characters", "atlas.png"))
    EX.write_manifest(manifest_path, manifest)
    PL.log(f"manifest: {manifest_path} ({len(manifest['assets'])} assets)")
    if args.shape_report:
        import json

        from charlib import designs

        os.makedirs(os.path.dirname(os.path.abspath(args.shape_report)), exist_ok=True)
        with open(args.shape_report, "w", encoding="utf-8") as f:
            json.dump(designs.REPORT, f, indent=1, sort_keys=True)
            f.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
    bpy.ops.wm.quit_blender()
