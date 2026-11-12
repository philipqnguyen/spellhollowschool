"""Clipping check: does any item sink into visible skin?

    blender -b --factory-startup --python tools/blender/characters/check_characters.py -- \\
        --art art [--bodies body_a,body_b] [--tolerance 0.002] [--poses full] [--json out.json]

Opens art/characters/<body>.blend (saved by build_characters.py), and for every
clothing item and hair style (LOD0) measures, at each build extreme and in the
rest pose plus frames of the walk, run and sit clips, how many of its vertices
lie more than `tolerance` inside the body's visible skin (the regions the item
does not hide). Hidden regions are not drawn, so they cannot show through.
`--poses full` adds the second walk stride and the wave and bow extremes
(FULL_POSES, as check_garment_shapes.py). Prints a table (optionally JSON:
item -> pose -> worst-build count) and exits non-zero when any item penetrates
in the rest pose.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

# Every body is the neutral build (character adoption): no build keys. The
# checks run at the body's minimum, default and maximum height instead
# (`--heights`): a uniform scale of the skeleton object, as the client does.
BUILDS = ("",)
HEIGHTS = ("min", "default", "max")
POSES = (("rest", None, 0), ("walk", "walk", 5), ("run", "run", 4), ("sit", "sit", 10))
# The clips at their extremes (both walk strides, the raised wave arm, the
# deepest bow, the sit).
FULL_POSES = (("rest", None, 0), ("walk", "walk", 5), ("walk_b", "walk", 15), ("run", "run", 4),
              ("wave", "wave", 30), ("bow", "bow", 28), ("sit", "sit", 10))


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--art", default="art")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--tolerance", type=float, default=0.002)
    p.add_argument("--suffix", default="", help="blend file suffix: _mh for the MakeHuman bodies")
    p.add_argument("--poses", choices=("default", "full"), default="default")
    p.add_argument("--heights", default="default", help="comma-separated: min, default, max (uniform scale)")
    p.add_argument("--blend-dir", default="", help="directory of the <body>.blend files (default <art>/characters)")
    p.add_argument("--json", default="")
    return p.parse_args(argv)


def height_scale(skel, which: str) -> float:
    """Uniform scale of the skeleton for the body's min / default / max height
    (the skeleton stores the measurements it was built from)."""
    if which == "default":
        return 1.0
    params = json.loads(skel.get("body_params", "{}")) if skel.get("body_params") else {}
    body_id = skel.name.replace("_skeleton", "")
    from charlib import proportions as P

    b = P.get(body_id)
    default = float(params.get("height", b.height))
    lo, hi = b.height_range
    return (lo if which == "min" else hi) / default


def set_height(skel, which: str) -> float:
    s = height_scale(skel, which)
    skel.scale = (s, s, s)
    bpy.context.view_layer.update()
    return s


def evaluated(obj) -> tuple[np.ndarray, list]:
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    pts = np.array([obj.matrix_world @ v.co for v in me.vertices])
    polys = [tuple(p.vertices) for p in me.polygons]
    ev.to_mesh_clear()
    return pts, polys


def set_build(objs, key: str) -> None:
    for o in objs:
        sk = o.data.shape_keys
        if not sk:
            continue
        for kb in sk.key_blocks[1:]:
            kb.value = 1.0 if kb.name == key else 0.0


def pose(skel, clip, frame) -> None:
    ad = skel.animation_data
    for t in ad.nla_tracks:
        t.mute = True
        t.is_solo = False
    if clip is None:
        for t in ad.nla_tracks:
            t.mute = True
        ad.action = None
        for pb in skel.pose.bones:
            pb.matrix_basis.identity()
    else:
        ad.action = bpy.data.actions[clip]
    bpy.context.scene.frame_set(frame)


def penetration(item_pts: np.ndarray, body_tree: BVHTree, poly_visible: list[bool], tol: float, samples: list) -> int:
    """Item vertices more than `tol` inside the (closed) body whose nearest skin
    point is on a visible face. Inside only when the offset runs along the
    nearest face's normal (`-h >= 0.5 * dist`, check_hair.py's rule): beside
    a thin part's rim (the ear) the nearest face can be its far side, which
    would count a point 2-4 cm outside the ear as a penetration."""
    n = 0
    for p in item_pts:
        loc, nrm, idx, dist = body_tree.find_nearest(p, 0.05)
        if loc is None or not poly_visible[idx]:
            continue
        h = float((p - np.array(loc)) @ np.array(nrm))
        if h < -tol and -h >= 0.5 * dist:
            n += 1
            if len(samples) < 3:
                samples.append(tuple(round(float(x), 3) for x in p))
    return n


def check_body(path: str, tol: float, poses=POSES, out: dict | None = None, heights=("default",)) -> int:
    bpy.ops.wm.open_mainfile(filepath=path)
    body_id = "_".join(os.path.splitext(os.path.basename(path))[0].split("_")[:2])  # body_a_mh, body_a_sculpt -> body_a
    skel = bpy.data.objects[f"{body_id}_skeleton"]
    body_parts = [o for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0]
    items = [o for o in bpy.data.objects if o.type == "MESH" and o.get("asset") and not o.get("region") and o.get("lod") == 0]
    for o in body_parts + items:
        o.hide_set(False)
        o.hide_viewport = False
    failures = 0
    print(f"\n{body_id}: vertices more than {tol * 1000:.0f} mm inside visible skin "
          f"({' / '.join(p for p, _, _ in poses)}, worst of heights {', '.join(heights)})")
    items = sorted(items, key=lambda o: o.name)
    worst = {o.name: {} for o in items}
    samples = {o.name: [] for o in items}
    for pname, clip, frame in poses:
        pose(skel, clip, frame)
        for b in heights:
            set_height(skel, b)
            # The whole (closed) body, remembering each face's region.
            verts, polys, regions = [], [], []
            for o in body_parts:
                p, f = evaluated(o)
                off = len(verts)
                verts.extend(p.tolist())
                polys.extend(tuple(i + off for i in poly) for poly in f)
                regions.extend([o["region"]] * len(f))
            tree = BVHTree.FromPolygons(verts, polys)
            for item in items:
                hides = set(item.get("hides", []))
                vis = [r not in hides for r in regions]
                ip, _ = evaluated(item)
                n = penetration(ip, tree, vis, tol, samples[item.name] if pname == "rest" else [])
                worst[item.name][pname] = max(worst[item.name].get(pname, 0), n)
        set_height(skel, "default")
    for item in items:
        w = worst[item.name]
        flag = "FAIL" if w["rest"] else "ok"
        failures += w["rest"] > 0
        print(f"  {flag:4} {item.name:40} " + " ".join(f"{w[p]:5d}" for p, _, _ in poses) +
              (f"   e.g. {samples[item.name][:2]}" if w["rest"] else ""))
    if out is not None:
        out[body_id] = {item.name: {"asset": str(item.get("asset")), **worst[item.name]} for item in items}
    return failures


def main() -> None:
    a = args()
    failures = 0
    out: dict = {}
    poses = FULL_POSES if a.poses == "full" else POSES
    blend_dir = os.path.abspath(a.blend_dir) if a.blend_dir else os.path.join(os.path.abspath(a.art), "characters")
    for body in a.bodies.split(","):
        failures += check_body(os.path.join(blend_dir, f"{body}{a.suffix}.blend"), a.tolerance,
                               poses, out, tuple(a.heights.split(",")))
    print(f"\n{failures} item(s) clip in the rest pose")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, sort_keys=True)
            f.write("\n")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
