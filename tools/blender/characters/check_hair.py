"""Hair clipping in motion: every style (and its hat variant) on its body.

    blender -b --factory-startup --python tools/blender/characters/check_hair.py -- \\
        --art art [--bodies body_a,body_b] [--tolerance 0.002] [--json out.json]

Opens art/characters/<body>.blend (build_characters.py or build_hair.py
output) and for every hair LOD0 mesh counts, per pose, the vertices more
than `tolerance` inside

* the body's visible skin (regions the style does not hide), as
  check_characters.py measures it, and
* the garments worn under hair in the shipped outfits (the school robe and
  the oxford shirt, when the file has them): long hair must clear the robe;
* the floor (z = 0), for the lying and fallen poses.

The garment count uses the nearest-face rule, which on open cloth also
counts hair beside a rim (a lock tip above the collar, behind a lapel's
edge face); a second table gives the garment hits under the cloth: those
whose ray straight up and ray straight out from the neck both meet a
garment (default height).

Poses: the rest pose, both walk strides, run, wave, bow, sit and the duel
and class clips (duel_ready, cast, shield_raise, hit_react, knockback,
disarmed, stun_fall, desk_rest, desk_practice, spectator_cheer), each at its
extreme frames. The sway bones are at rest (the .blend hair is rigid on the
head; the client's spring adds motion).

Counts are of hair surface points: the inner layer and the inner cap's
flipped copy (character-look.md section 2) repeat the shell's and the cap's
vertices a few millimetres in, and a vertex and its inner twin inside
together count once (the build's `inner_twin` attribute).

Height is a uniform scale of the whole character (character-adoption.md),
so a penetration depth d at the default height is d x h / h0 at
height h: each count is given at the body's minimum, default and maximum
height (the tolerance divided by the scale). Exits non-zero when any style
penetrates skin in the rest pose or exceeds `--max-moving` (default 25) in a
pose of the contract's list (walk, walk_b, run, sit, wave, bow), or when its
garment hits under the cloth (the second table) exceed `--max-moving` in the
rest pose or a contract pose. The nearest-face garment count is information
only (character-look.md, integration).
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

from check_characters import evaluated, pose, set_build  # noqa: E402
from charlib import content  # noqa: E402

POSES = (("rest", None, 0), ("walk", "walk", 5), ("walk_b", "walk", 15), ("run", "run", 4), ("run_b", "run", 10),
         ("wave", "wave", 30), ("bow", "bow", 28), ("sit", "sit", 10), ("duel_ready", "duel_ready", 30),
         ("cast", "cast", 13), ("shield_raise", "shield_raise", 3), ("hit_react", "hit_react", 9),
         ("knockback", "knockback", 16), ("disarmed", "disarmed", 15), ("stun_fall", "stun_fall", 39),
         ("desk_rest", "desk_rest", 45), ("desk_practice", "desk_practice", 30), ("cheer", "spectator_cheer", 45))
CONTRACT = ("walk", "walk_b", "run", "sit", "wave", "bow")
GARMENTS = ("gear/body/robe_school_house_a", "gear/body/shirt_oxford_tie_a")


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--art", default="art")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--tolerance", type=float, default=0.002)
    p.add_argument("--max-moving", type=int, default=25)
    p.add_argument("--json", default="")
    return p.parse_args(argv)


def depths(pts: np.ndarray, tree: BVHTree, visible=None, reach: float = 0.05) -> np.ndarray:
    """Depth (m, positive = inside) of each point under the nearest surface
    (0 when outside, out of reach or nearest to a hidden face)."""
    out = np.zeros(len(pts))
    for i, p in enumerate(pts):
        loc, nrm, idx, dist = tree.find_nearest(Vector(tuple(p)), reach)
        if loc is None or (visible is not None and not visible[idx]):
            continue
        h = float((p - np.array(loc)) @ np.array(nrm))
        # Inside only when the offset runs along the nearest face's normal:
        # beside a thin part's rim (the ear) the nearest face can be its far
        # side, which check_characters.py counts as a (false) penetration.
        if h < 0 and -h >= 0.5 * dist:
            out[i] = -h
    return out


LIMITS = {"back": 25.0, "front": 25.0, "side": 12.0}  # degrees (character-adoption.md section 4.2)


def covered(pts: np.ndarray, trees: list, axis: np.ndarray) -> np.ndarray:
    """Which of `pts` are under the cloth: the ray straight up and the
    horizontal ray away from the vertical line through `axis` both meet one
    of the garments `trees`."""
    out_m = np.zeros(len(pts), dtype=bool)
    for i, p in enumerate(pts):
        out = p[:2] - axis[:2]
        out = out / max(float(np.linalg.norm(out)), 1e-9)
        o = Vector(tuple(p))
        if all(any(t.ray_cast(o, Vector(d), 1.0)[0] is not None for t in trees)
               for d in ((0.0, 0.0, 1.0), (float(out[0]), float(out[1]), 0.0))):
            out_m[i] = True
    return out_m


def twins(h) -> np.ndarray:
    """Per vertex the hair surface point it belongs to: itself, or for the
    inner layer and the inner cap's flipped copy (character-look.md section 2) the
    shell or cap vertex it copies (the build's `inner_twin` attribute, kept
    in the .blend only). Counts are of surface points: a vertex and its
    inner twin inside together count once."""
    n = len(h.data.vertices)
    a = h.data.attributes.get("inner_twin")
    if a is None:
        return np.arange(n)
    t = np.zeros(n, dtype=np.int64)
    a.data.foreach_get("value", t)
    return t


def fold(mask: np.ndarray, twin: np.ndarray) -> int:
    """How many surface points have a vertex in `mask`."""
    hit = np.zeros(len(mask), dtype=bool)
    np.logical_or.at(hit, twin, mask)
    return int(hit.sum())


def sway_data(h):
    """(per vertex chain name or None, chain fraction 0..1) from the
    `sway:` vertex groups and the stored rest joints."""
    joints = {k: np.array(v) for k, v in dict(h.get("sway_joints", {})).items()}
    n = len(h.data.vertices)
    chain = [None] * n
    frac = np.zeros(n)
    groups = {g.index: g.name[5:] for g in h.vertex_groups if g.name.startswith("sway:hair.")}
    for v in h.data.vertices:
        acc, tot, cname = 0.0, 0.0, None
        for ge in v.groups:
            name = groups.get(ge.group)
            if name is None:
                continue
            parts = name.split(".")  # hair.<chain...>.<k>
            cname = ".".join(parts[1:-1])
            k = int(parts[-1]) - 1
            acc += ge.weight * (k + 0.5)
            tot += ge.weight
        if cname and cname in joints:
            nb = len(joints[cname]) - 1
            chain[v.index] = cname
            frac[v.index] = min(1.0, acc / nb)
    return chain, frac, joints


def settle(skel, pts: np.ndarray, chain, frac, joints) -> np.ndarray:
    """The client's spring at rest under gravity: each chain turns back
    toward its standing direction (as far as its limit), bone fractions
    taking their share."""
    from mathutils import Matrix

    pb = skel.pose.bones.get("DEF-spine.006")
    if pb is None or not joints:
        return pts
    rest = skel.matrix_world @ pb.bone.matrix_local
    posed = skel.matrix_world @ pb.matrix
    M = np.array(posed @ rest.inverted())
    out = pts.copy()
    for cname, J in joints.items():
        root = M[:3, :3] @ J[0] + M[:3, 3]
        d_rigid = M[:3, :3] @ (J[-1] - J[0])
        d_rest = J[-1] - J[0]
        a = d_rigid / np.linalg.norm(d_rigid)
        b = d_rest / np.linalg.norm(d_rest)
        axis = np.cross(a, b)
        sn = np.linalg.norm(axis)
        ang = np.degrees(np.arctan2(sn, float(a @ b)))
        if sn < 1e-6:
            continue
        ang = min(ang, LIMITS[cname.split(".")[0]])
        axis /= sn
        idx = [i for i, c in enumerate(chain) if c == cname]
        for i in idx:
            R = np.array(Matrix.Rotation(np.radians(ang * frac[i]), 3, tuple(axis)))
            out[i] = root + R @ (pts[i] - root)
    return out


def check_body(path: str, tol: float, out: dict, max_moving: int = 25) -> list[str]:
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
    body_id = "_".join(os.path.splitext(os.path.basename(path))[0].split("_")[:2])
    skel = bpy.data.objects[f"{body_id}_skeleton"]
    b = next(x for x in content.creator()["body_types"] if x["id"] == body_id)
    h0 = b["default_height_m"]
    heights = (b["height_m"][0], h0, b["height_m"][1])
    parts = [o for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0]
    hairs = sorted((o for o in bpy.data.objects if o.type == "MESH" and str(o.get("asset", "")).startswith("hair/")
                    and o.get("lod") == 0), key=lambda o: o.name)
    garments = [o for o in bpy.data.objects if o.type == "MESH" and o.get("asset") in GARMENTS and o.get("lod") == 0
                and not o.get("region")]
    for o in parts + hairs + garments:
        o.hide_set(False)
        o.hide_viewport = False
    set_build(parts + hairs + garments, "")  # every shape key off (saved files keep them at 1)
    res = {h.name: {} for h in hairs}
    settled = {h.name: {} for h in hairs}
    under = {h.name: {} for h in hairs}
    sway = {h.name: sway_data(h) for h in hairs}
    twin = {h.name: twins(h) for h in hairs}
    for pname, clip, frame in POSES:
        if clip is not None and clip not in bpy.data.actions:
            continue
        pose(skel, clip, frame)
        verts, polys, regions = [], [], []
        neck = None
        for o in parts:
            p, f = evaluated(o)
            off = len(verts)
            verts.extend(p.tolist())
            polys.extend(tuple(i + off for i in poly) for poly in f)
            regions.extend([o["region"]] * len(f))
            if o["region"] == "neck":
                neck = p.mean(axis=0)
        body = BVHTree.FromPolygons(verts, polys)
        gtrees = []
        for g in garments:
            p, f = evaluated(g)
            gtrees.append(BVHTree.FromPolygons(p.tolist(), f))
        for h in hairs:
            hides = set(h.get("hides", []))
            vis = [r not in hides for r in regions]
            hp, _ = evaluated(h)
            d_skin = depths(hp, body, vis)
            d_garm = np.zeros(len(hp))
            for t in gtrees:
                d_garm = np.maximum(d_garm, depths(hp, t, None, 0.03))
            floor = np.maximum(-hp[:, 2], 0.0)
            tw = twin[h.name]
            row = {}
            for label, hh in zip(("min", "default", "max"), heights):
                k = tol * h0 / hh
                row[label] = {"skin": fold(d_skin > k, tw), "garment": fold(d_garm > k, tw),
                              "floor": fold(floor > k, tw)}
            res[h.name][pname] = row
            if gtrees and neck is not None:
                g = d_garm > tol
                cov = np.zeros(len(hp), dtype=bool)
                cov[g] = covered(hp[g], gtrees, neck)
                under[h.name][pname] = fold(cov, tw)
            # With the client's spring settled under gravity.
            sp = settle(skel, hp, *sway[h.name])
            if not np.array_equal(sp, hp):
                d2 = depths(sp, body, vis)
                settled[h.name][pname] = fold(d2 > tol, tw)
            else:
                settled[h.name][pname] = fold(d_skin > tol, tw)
    failures = []
    print(f"\n{body_id}: hair vertices more than {tol * 1000:.0f} mm inside skin / garment / floor "
          f"(worst of min, default and max height)")
    names = [p for p, c, _ in POSES if c is None or c in bpy.data.actions]
    print("  " + " " * 34 + " ".join(f"{n[:11]:>11}" for n in names))
    for hname, rows in res.items():
        cells = []
        for n in names:
            w = {k: max(rows[n][lab][k] for lab in ("min", "default", "max")) for k in ("skin", "garment", "floor")}
            cells.append(f"{w['skin']:>3}/{w['garment']:>3}/{w['floor']:>2}")
            if n == "rest" and w["skin"] > 0:
                failures.append(f"{hname} rest skin {w['skin']}")
            if n in CONTRACT and w["skin"] > max_moving:
                failures.append(f"{hname} {n} skin {w['skin']}")
        print(f"  {hname:34} " + " ".join(f"{c:>11}" for c in cells))
    for hname, rows in under.items():
        for n in ("rest",) + CONTRACT:
            if rows.get(n, 0) > max_moving:
                failures.append(f"{hname} {n} under the cloth {rows[n]}")
    print("  skin with the sway settled under gravity (default height):")
    for hname, rows in settled.items():
        print(f"  {hname:34} " + " ".join(f"{rows[n]:>11}" for n in names))
    if any(under.values()):
        print("  garment hits under the cloth (up and out from the neck both meet a garment; default height):")
        for hname, rows in under.items():
            print(f"  {hname:34} " + " ".join(f"{rows.get(n, 0):>11}" for n in names))
    out[body_id] = {"rest_sway": res, "settled_sway": settled, "garment_under_cloth": under}
    return failures


def main() -> None:
    a = args()
    out: dict = {}
    failures: list[str] = []
    for body in a.bodies.split(","):
        failures += check_body(os.path.join(os.path.abspath(a.art), "characters", f"{body}.blend"), a.tolerance, out, a.max_moving)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, sort_keys=True)
            f.write("\n")
    print(f"\n{len(failures)} failure(s)" + ("".join(f"\n  {x}" for x in failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
