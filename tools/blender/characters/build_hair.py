"""Rebuild only the hair of a saved body (no body or gear build).

    blender -b --factory-startup --python tools/blender/characters/build_hair.py -- \\
        --blend art/characters/body_a.blend --assets client/public/assets \\
        [--styles short_tousled,side_swept] [--source meshy|locks] [--save <out .blend>] [--report <json>]

Opens a body saved by build_characters.py, removes the hair it was saved
with, builds that body's styles (creator.json v2: each style belongs to one
body) exactly as `build_characters.py --only hair` would after the body
(from the Meshy-derived source meshes in `art/characters/hair_meshy/`, or
`SHS_HAIR_MESHY=<dir>`, or as locks: charlib/hair_meshy.py `STYLES[...]
["source"]`, `--source` overrides),
exports `hair/<asset>[_cap]_<body>.glb`, merges the entries into
`<assets>/characters/manifest.json` and, with `--save`, writes the body with
its new hair (for check_characters.py, check_hair_hats.py and the renders).
The full build does the same through `content.build_hair`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from charlib import content  # noqa: E402
from charlib import export as EX  # noqa: E402
from charlib import hair as HR  # noqa: E402
from charlib import hair_meshy as HM  # noqa: E402
from charlib import hair_frame as HF  # noqa: E402
from charlib import pipeline as PL  # noqa: E402


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--assets", default="client/public/assets")
    p.add_argument("--styles", default="")
    p.add_argument("--save", default="")
    p.add_argument("--report", default="")
    p.add_argument("--source", default="", choices=("", "meshy", "locks"),
                   help="build every style from its Meshy source mesh or as locks (default: each style's own source)")
    return p.parse_args(argv)


def main() -> None:
    sys.path.append(os.path.dirname(HERE))  # tools/blender, for pins
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    a = args()
    t0 = time.time()
    bb = HF.load_body(os.path.abspath(a.blend))
    assets = os.path.abspath(a.assets)
    manifest_path = os.path.join(assets, "characters", "manifest.json")
    manifest = EX.read_manifest(manifest_path)
    # Entries and files of retired styles go.
    removed = HR.remove_retired(assets, manifest)
    if removed:
        PL.log(f"removed {len(removed)} retired hair files")
    only = set(filter(None, a.styles.split(",")))
    report: dict = {}
    for st in content.hair_styles(bb.id):
        if only and st["asset"].split("/")[-1] not in only:
            continue
        HM.build(bb, st, assets, manifest, report, source=a.source or None)
    EX.write_manifest(manifest_path, manifest)
    if a.save:
        PL.save_blend(os.path.abspath(a.save))
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1, sort_keys=True)
            f.write("\n")
    PL.log(f"{bb.id}: hair built in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
