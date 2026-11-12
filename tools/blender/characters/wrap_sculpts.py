"""Bakes the body shape targets: our body wrapped onto third-party sculpts.

    BLENDER_USER_RESOURCES=<worktree>/.blender-user \\
    blender -b --factory-startup --disable-autoexec --python tools/blender/characters/wrap_sculpts.py -- \\
        --sculpt-dir <dir with the sculpt folders> --out art/characters/shape_targets [--bodies body_a,body_b]

Each body is built exactly as `build_characters.py --body-source makehuman`
builds it (MakeHuman body, anime head, body paint) up to the point where the
painted face is cut in; that pre-cut visible mesh is wrapped onto the sculpt
(`charlib/sculpt_wrap.py`) and the result, with the sculpt's face anchors
(eye sockets, nose, mouth, chin), is saved as `<out>/<body>[_<variant>].npz`
(`charlib/shape_target.py`). Normal builds paint the face onto the wrapped
head from those anchors, then decimate to LOD0. Normal
builds read those files (`--body-shape sculpt`); only this tool reads the
sculpts. The sculpts are CC BY 4.0 (art/CREDITS.md) and are never copied into
the repository.

Options:

* `--sculpt-dir` (default $SHS_SCULPT_DIR or this checkout's gitignored vendor_assets/)
* `--bodies body_a,body_b` (default both): a body id with a variant suffix
  uses that variant's sculpt (see sculpt_wrap.SCULPTS; none is listed since
  the adoption: the trial's GreyWax comparison variant is not shipped)
* `--out` the target directory (default art/characters/shape_targets)
* `--debug <dir>` writes intermediate meshes (.npz) and review renders there
* `--cache <dir>` keeps the pre-wrap mesh between runs (development)
"""

from __future__ import annotations

import argparse
import os
import pickle
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
import numpy as np  # noqa: E402

from charlib import pipeline as PL  # noqa: E402
from charlib import sculpt_wrap as SW  # noqa: E402
from charlib import shape_target as ST  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))


def script_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    default_dir = os.environ.get("SHS_SCULPT_DIR") or os.path.join(REPO, "vendor_assets")
    p.add_argument("--sculpt-dir", default=default_dir)
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--out", default=os.path.join(REPO, ST.DEFAULT_DIR))
    p.add_argument("--debug", default="")
    p.add_argument("--cache", default="")
    return p.parse_args(argv)


def prewrap(body_id: str, cache: str) -> dict:
    """The pre-wrap, pre-cut visible mesh (arrays), its body and face frame."""
    path = os.path.join(cache, f"prewrap_{body_id}.pkl") if cache else ""
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    PL.new_scene()
    cap = ST.Capture()
    try:
        PL.build_body(body_id, bake=False, source="makehuman", shape=cap)
    except ST.Captured:
        pass
    if cap.data is None:
        raise RuntimeError(f"{body_id}: the build never reached the face cut")
    out = {"mesh": cap.data, "body": cap.body, "frame": cap.frame}
    if path:
        os.makedirs(cache, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(out, f)
    return out


def main() -> None:
    a = script_args()
    t0 = time.time()
    for spec in a.bodies.split(","):
        parts = spec.split("_")
        body_id = "_".join(parts[:2])
        variant = "_".join(parts[2:])
        pre = prewrap(body_id, a.cache)
        PL.log(f"{spec}: pre-wrap mesh {len(pre['mesh']['verts'])} verts, {len(pre['mesh']['faces'])} faces "
               f"({time.time() - t0:.0f}s)")
        res = SW.wrap(body_id, variant, pre, a.sculpt_dir, debug=os.path.join(a.debug, spec) if a.debug else "")
        path = ST.target_path(body_id, a.out, variant)
        ST.save_target(path, verts0=pre["mesh"]["verts"], verts=res.verts, faces=pre["mesh"]["faces"], body=res.body,
                       meta=res.meta, extra={"faces": ST.triangles(pre["mesh"]["faces"]).astype(np.int32),
                                             "poly_verts": np.array([v for f in pre["mesh"]["faces"] for v in f], dtype=np.int32),
                                             "poly_sizes": np.array([len(f) for f in pre["mesh"]["faces"]], dtype=np.int32),
                                             "regions": np.array([r for f, r in zip(pre["mesh"]["faces"], pre["mesh"]["regions"])
                                                                      for _ in range(len(f) - 2)]),
                                             **{k: np.asarray(v, dtype=np.float32) for k, v in res.face.items()}})
        PL.log(f"{spec}: wrote {path} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
    bpy.ops.wm.quit_blender()
