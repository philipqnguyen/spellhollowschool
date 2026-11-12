"""Validates data/world/layout.json and walks its connectivity graph.

    blender -b --factory-startup --python tools/blender/check_layout.py -- --layout data/world/layout.json

Breadth-first walks the surfaces from every spawn with the same rules as
sim-core (overlap or shared edge, heights within step height on the contact)
and exits non-zero on unreachable surfaces or any other layout error. Uses no
Blender API, so any Python 3.10+ can run it too.
"""

import argparse
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import layout_lib  # noqa: E402


def script_args(argv: list[str]) -> list[str]:
    """Arguments after Blender's `--` separator (or all of them outside Blender)."""
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    if "--python" in argv or "-P" in argv:
        return []  # run by Blender without script arguments: use the defaults
    return argv[1:]


def run(layout_path: str, verbose: bool = False) -> int:
    try:
        layout = layout_lib.load_layout(layout_path, moving=True)
    except (OSError, ValueError, layout_lib.LayoutError) as e:
        print(f"layout check FAILED: {e}")
        return 1
    rep = layout_lib.validate(layout)
    for w in rep.warnings:
        print(f"warning: {w}")
    for e in rep.errors:
        print(f"error: {e}")
    if verbose:
        adj = layout_lib.adjacency(layout)
        for s in layout.surfaces:
            print(f"  {s.id:24s} -> {', '.join(adj[s.id])}")
    n = len(layout.surfaces)
    if rep.ok:
        print(f"layout check OK: {n} surfaces in {len(layout.cells())} cells, all reachable from spawn")
        return 0
    print(f"layout check FAILED with {len(rep.errors)} error(s)")
    return 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--layout", default="data/world/layout.json")
    ap.add_argument("--verbose", action="store_true", help="print the connectivity graph")
    args = ap.parse_args(script_args(sys.argv))
    sys.exit(run(args.layout, args.verbose))


if __name__ == "__main__":
    main()
