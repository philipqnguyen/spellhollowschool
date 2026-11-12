"""Review stills of the Flying Pitch (docs/architecture.md),
by day and by night, through render_toon.py's toon preview of the
exported world and grounds_stills.py's grounds materials.

    blender -b --factory-startup --python tools/blender/world/pitch_stills.py -- \\
        [--assets client/public/assets] [--out .out/renders] [--views stand,field,air] [--times day,night]

Writes `<out>/pitch_<view>_<day|night>.png`: from the north stand's benches,
from the field by the west hoops, from the air over the lake side, a tower
close by, and from the south over the pitch toward the castle.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import grounds_stills as GS  # noqa: E402
import render_toon as RT  # noqa: E402
from check_layout import script_args  # noqa: E402

REPO = RT.REPO
# name -> (kind, eye, target, lens mm, exterior)
VIEWS = {
    "stand": ("castle", (2.0, -5.9, 179.6), (14.0, -5.0, 214.0), 16, True),
    "field": ("castle", (-22.0, -7.9, 206.0), (40.0, -1.5, 194.0), 18, True),
    "air": ("castle", (-62.0, 24.0, 262.0), (12.0, -8.0, 198.0), 22, True),
    "tower": ("castle", (-30.0, -6.0, 200.0), (-42.0, 2.0, 191.0), 20, True),
    "castle": ("castle", (12.0, 6.0, 262.0), (8.0, -2.0, 150.0), 20, True),
}
HOURS = GS.HOURS


def main() -> None:
    ap = argparse.ArgumentParser(description="Pitch review stills")
    ap.add_argument("--assets", default=os.path.join(REPO, "client", "public", "assets"))
    ap.add_argument("--out", default=os.path.join(REPO, ".out", "renders"))
    ap.add_argument("--views", default=",".join(VIEWS))
    ap.add_argument("--times", default="day,night")
    ap.add_argument("--samples", type=int, default=16)
    args = ap.parse_args(script_args(sys.argv))
    RT.bind_library = GS.bind_library
    with open(os.path.join(REPO, "data", "art", "toon_palette.json"), encoding="utf-8") as f:
        toon = json.load(f)
    with open(os.path.join(REPO, "data", "art", "lighting.json"), encoding="utf-8") as f:
        lighting = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    for t in args.times.split(","):
        ns = argparse.Namespace(assets=args.assets, out=args.out, prefix=f"pitch_{t}", shading="toon",
                                hour=HOURS[t], samples=args.samples, line=1.0, kit_preview=False)
        for name in args.views.split(","):
            RT.render_view(name, ns, toon, lighting, VIEWS)
            src = os.path.join(args.out, f"pitch_{t}_{name}.png")
            dst = os.path.join(args.out, f"pitch_{name}_{t}.png")
            os.replace(src, dst)
            print(f"still {dst}")


if __name__ == "__main__":
    main()
