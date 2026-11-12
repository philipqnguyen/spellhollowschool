"""Clipping and layering check for the garment-shape trial.

    blender -b --factory-startup --python tools/blender/characters/check_garment_shapes.py -- \\
        --art <art dir> [--bodies body_a,body_b] [--tolerance 0.002] [--json out.json]

Opens <art>/characters/<body>.blend (saved by build_characters.py, with baked
clips) and measures, for every clothing item (LOD0), at every build extreme
(neutral, build_slim, build_muscular, build_heavy) and in the rest pose and
frames of walk, run and sit (check_characters.POSES):

* skin: vertices more than `tolerance` inside visible skin (as check_characters.py);
* layering: for each outer/inner pair that overlaps (body over legs, legs
  over shoes, body over shoes; shoes over the tights of skirt designs),
  vertices of the inner item more than
  `tolerance` outside the outer item's surface, where the outer item covers
  them (nearest outer point at least one face away from its open edges, and
  within 25 mm) and the offset runs along the outer face's normal. That is
  the inner layer showing through the outer one. The JSON also keeps
  `layering_any`, the count without the normal test (it also counts inner
  vertices just beyond a folded hem or a lined rim, which are in view, not
  through the cloth).

`--poses full` adds the second walk stride and the wave and bow extremes to
rest, walk, run and sit (FULL_POSES).

Prints tables (worst build per pose) and optionally writes JSON. Exit code 0.
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
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

import check_characters as CC  # noqa: E402

LAYER_PAIRS = (("body", "legs"), ("legs", "shoes"), ("body", "shoes"))
# --poses full: the clips at their extremes (both walk strides, the raised wave
# arm, the deepest bow, the sit). "default" is check_characters.POSES.
FULL_POSES = CC.FULL_POSES


def under_shoes() -> set[str]:
    """Legs items worn under the shoes: skirts over opaque tights (shape
    builder "skirt"). Boots and shoes go over tights, so for these the
    legs/shoes pair is checked the other way round (shoes outer)."""
    from charlib import designs as DS

    shapes = DS.shapes_data()["shapes"]
    return {a for a, d in DS.designs_data()["designs"].items() if shapes[d["shape"]].get("builder") == "skirt"}


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--art", required=True)
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--tolerance", type=float, default=0.002)
    p.add_argument("--items", default="", help="comma-separated asset keys (default: every item in the file)")
    p.add_argument("--json", default="")
    p.add_argument("--suffix", default="", help="blend file suffix: _mh for the MakeHuman bodies")
    p.add_argument("--poses", choices=("default", "full"), default="default",
                   help="default: rest, walk, run, sit (check_characters.POSES); full: adds the second walk stride, wave and bow")
    p.add_argument("--heights", default="default", help="comma-separated: min, default, max (uniform scale)")
    p.add_argument("--blend-dir", default="", help="directory of the <body>.blend files (default <art>/characters)")
    return p.parse_args(argv)


def border_faces(polys) -> set[int]:
    """Faces touching an open edge (one face deep)."""
    count: dict = {}
    for p in polys:
        for i in range(len(p)):
            e = tuple(sorted((p[i], p[(i + 1) % len(p)])))
            count[e] = count.get(e, 0) + 1
    bverts = {v for e, c in count.items() if c == 1 for v in e}
    return {fi for fi, p in enumerate(polys) if any(v in bverts for v in p)}


def outward_faces(pts: np.ndarray, polys, body_tree) -> list[int]:
    """Faces whose normal points away from the body (drops linings and the
    inner side of thickened parts, which face the body)."""
    out = []
    for fi, f in enumerate(polys):
        q = pts[list(f)]
        c = q.mean(axis=0)
        n = np.zeros(3)
        for i in range(len(f)):
            n += np.cross(q[i], q[(i + 1) % len(f)])
        loc, bn, idx, dist = body_tree.find_nearest(Vector(c), 0.5)
        if loc is None or float(n @ (c - np.array(loc))) > 0:
            out.append(fi)
    return out


def outer_trees(outer_pts: np.ndarray, outer_polys, body_tree):
    border = border_faces(outer_polys)
    keep = [fi for fi in outward_faces(outer_pts, outer_polys, body_tree) if fi not in border]
    tree = BVHTree.FromPolygons(outer_pts.tolist(), [outer_polys[fi] for fi in keep]) if keep else None
    border_tree = BVHTree.FromPolygons(outer_pts.tolist(), [outer_polys[fi] for fi in border]) if border else None
    return tree, border_tree


TIGHT = 0.0015  # rest-pose layer margin reported as layering_tight_rest (metres)
COVER_COS = 0.8  # a vertex shows *through* a face only when its offset runs along that face's normal


def layering(inner_pts: np.ndarray, trees, tol: float) -> tuple[int, int]:
    """(through, any): inner vertices more than `tol` outside the outer item
    where it covers them. `through` also asks that the offset from the
    nearest outer point run along the outer normal (within about 37 degrees):
    a vertex whose nearest outer point is on the rim of a hem, a fold or a
    lining (a shoe vamp below a folded trouser hem, a skirt seen past a
    robe's lined edge) lies beyond that edge, in view, not through the cloth.
    `any` is the earlier rule without that test."""
    tree, border_tree = trees
    if tree is None:
        return 0, 0
    n = n_any = 0
    for p in inner_pts:
        loc, nrm, idx, dist = tree.find_nearest(Vector(p), 0.025)
        if loc is None:
            continue
        if border_tree is not None:
            bl, _, _, bd = border_tree.find_nearest(Vector(p), 0.025)
            if bl is not None and bd < dist:
                continue  # nearest to the outer item's open edge: not covered there
        off = p - np.array(loc)
        d = off @ np.array(nrm)
        if d > tol:
            n_any += 1
            if abs(d) > COVER_COS * float(np.linalg.norm(off)):
                n += 1
    return n, n_any


def inward_regions(skel, body_parts) -> set[str]:
    """Body regions whose faces are wound toward the skeleton (the current
    body's arm tubes and hands are). check_characters.py trusts the winding,
    which reads clothing *outside* such a region as inside it and the reverse;
    this check flips those regions' normals instead (fit.inward_votes)."""
    from charlib import fit as FT

    CC.pose(skel, None, 0)
    CC.set_build(body_parts, "")
    verts, polys, regions = [], [], []
    for o in body_parts:
        p, f = CC.evaluated(o)
        off = len(verts)
        verts.extend(p.tolist())
        polys.extend(tuple(i + off for i in poly) for poly in f)
        regions.extend([o["region"]] * len(f))
    votes = FT.inward_votes(np.array(verts), polys, regions, FT.Joints.from_armature(skel))
    return {r for r, (a, n) in votes.items() if a * 2 > n}


def skin_penetration(item_pts: np.ndarray, body_tree: BVHTree, poly_visible: list[bool], poly_inward: list[bool], tol: float,
                     regions=None, where: dict | None = None) -> int:
    """Item vertices more than `tol` inside visible skin (check_characters.penetration
    with the normals of inward-wound regions flipped). `where` collects the
    count per body region the clipping vertices sink into."""
    n = 0
    for p in item_pts:
        loc, nrm, idx, dist = body_tree.find_nearest(Vector(p), 0.05)
        if loc is None or not poly_visible[idx]:
            continue
        d = (p - np.array(loc)) @ np.array(nrm)
        if poly_inward[idx]:
            d = -d
        if d < -tol:
            n += 1
            if where is not None and regions is not None:
                where[regions[idx]] = where.get(regions[idx], 0) + 1
    return n


def check_body(path: str, tol: float, only: set[str], poses=CC.POSES, heights=("default",)) -> dict:
    bpy.ops.wm.open_mainfile(filepath=path)
    body_id = "_".join(os.path.splitext(os.path.basename(path))[0].split("_")[:2])  # body_a_mh, body_a_sculpt -> body_a
    skel = bpy.data.objects[f"{body_id}_skeleton"]
    body_parts = [o for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0]
    items = [o for o in bpy.data.objects if o.type == "MESH" and o.get("asset") and not o.get("region") and o.get("lod") == 0
             and str(o["asset"]).startswith("gear/") and (not only or o["asset"] in only)]
    for o in body_parts + items:
        o.hide_set(False)
        o.hide_viewport = False
    items.sort(key=lambda o: o.name)
    skin = {o["asset"]: {} for o in items}
    detail: dict = {}  # asset -> pose -> {"build": worst build, "regions": {body region: vertices}}
    layers: dict = {}
    layers_any: dict = {}
    tight: dict = {}
    by_slot: dict = {}
    for o in items:
        by_slot.setdefault(o["slot"], []).append(o)
    tights = under_shoes()
    pairs = [(b, a) if (so, si) == ("legs", "shoes") and a["asset"] in tights else (a, b)
             for so, si in LAYER_PAIRS for a in by_slot.get(so, []) for b in by_slot.get(si, [])]
    inward = inward_regions(skel, body_parts)
    print(f"{body_id}: body regions wound inward (their normals are flipped for the skin test): {sorted(inward) or 'none'}")
    poses = [p for p in poses if not p[1] or p[1] in bpy.data.actions]
    for pname, clip, frame in poses:
        CC.pose(skel, clip, frame)
        for build in heights:
            CC.set_height(skel, build)
            verts, polys, regions = [], [], []
            for o in body_parts:
                p, f = CC.evaluated(o)
                off = len(verts)
                verts.extend(p.tolist())
                polys.extend(tuple(i + off for i in poly) for poly in f)
                regions.extend([o["region"]] * len(f))
            tree = BVHTree.FromPolygons(verts, polys)
            ev = {o.name: CC.evaluated(o) for o in items}
            for o in items:
                hides = set(o.get("hides", []))
                vis = [r not in hides for r in regions]
                where: dict = {}
                n = skin_penetration(ev[o.name][0], tree, vis, [r in inward for r in regions], tol, regions, where)
                if n > skin[o["asset"]].get(pname, 0):
                    detail.setdefault(o["asset"], {})[pname] = {"build": build or "base", "regions": where}
                skin[o["asset"]][pname] = max(skin[o["asset"]].get(pname, 0), n)
            trees = {}
            for outer, inner in pairs:
                if outer.name not in trees:
                    trees[outer.name] = outer_trees(*ev[outer.name], tree)
                ip, _ = ev[inner.name]
                key = f"{outer['asset']} > {inner['asset']}"
                n, n_any = layering(ip, trees[outer.name], tol)
                layers.setdefault(key, {})
                layers[key][pname] = max(layers[key].get(pname, 0), n)
                layers_any.setdefault(key, {})
                layers_any[key][pname] = max(layers_any[key].get(pname, 0), n_any)
                if pname == "rest":
                    # Margin: inner vertices closer than TIGHT under the outer
                    # surface (an outline hull or depth precision shows them).
                    tight[key] = max(tight.get(key, 0), layering(ip, trees[outer.name], -TIGHT)[0])
        CC.set_height(skel, "default")
    poses = [p for p, _, _ in poses]
    print(f"\n{body_id}: skin clipping, vertices > {tol * 1000:.0f} mm inside visible skin (worst height) " + " ".join(f"{p:>5}" for p in poses))
    for a, w in skin.items():
        print(f"  {a:44} " + " ".join(f"{w[p]:5d}" for p in poses))
    print(f"{body_id}: layering, inner vertices > {tol * 1000:.0f} mm outside the outer item (worst height)")
    for k, w in sorted(layers.items()):
        print(f"  {k:80} " + " ".join(f"{w[p]:5d}" for p in poses))
    tris = {o["asset"]: len(o.data.polygons) for o in items}
    return {"skin": skin, "skin_detail": detail, "layering": layers, "layering_any": layers_any, "layering_tight_rest": tight, "lod0_faces": tris, "inward_regions": sorted(inward)}


def main():
    a = args()
    only = set(filter(None, a.items.split(",")))
    out = {}
    blend_dir = os.path.abspath(a.blend_dir) if a.blend_dir else os.path.join(os.path.abspath(a.art), "characters")
    for body in a.bodies.split(","):
        out[body] = check_body(os.path.join(blend_dir, f"{body}{a.suffix}.blend"), a.tolerance, only,
                               FULL_POSES if a.poses == "full" else CC.POSES, tuple(a.heights.split(",")))
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, sort_keys=True)
            f.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
