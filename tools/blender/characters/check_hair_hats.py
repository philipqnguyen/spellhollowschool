"""Hats over hair: does any hairstyle poke through any hat?

    blender -b --factory-startup --python tools/blender/characters/check_hair_hats.py -- \\
        --art art [--bodies body_a,body_b] [--suffix _mh] [--tolerance 0.001]

Opens art/characters/<body><suffix>.blend (saved by build_characters.py) and,
for every hat x every hairstyle as worn under hats (its own file, or its
hat variant where its tails change), counts the hair vertices that lie
outside the hat where the hat covers them: a ray from the head centre through
the hair vertex meets the hat closer than the vertex (minus `tolerance`).
The pair's fit is applied first, as the client applies it (the hair's
`hat_fit`, character-adoption.md section 4.4): the hair the hat's crown covers is
dropped, the hat scaled about the head centre, and where the scale is
capped the hair under the crown pressed in to the hat. Counted: the hair left
inside the hat's cover line and above the band line + HAT_FIT_BELOW (a
hood reaching the shoulders is not a hat's band). Hats a style has no fit
for use the old rule: the hair above the band line, the hat as it is.
Checked at every build extreme in the rest pose and a walk and sit frame.
Prints a table and exits non-zero when any pair pokes through in the rest
pose.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

from check_characters import BUILDS, evaluated, pose, set_build  # noqa: E402
from charlib import proportions as P  # noqa: E402
from charlib import skeleton as K  # noqa: E402
from charlib import hair_meshy as HM  # noqa: E402
from charlib.hair_frame import band_phi  # noqa: E402

POSES = (("rest", None, 0), ("walk", "walk", 5), ("sit", "sit", 10))


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--art", default="art")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--suffix", default="")
    p.add_argument("--tolerance", type=float, default=0.001)
    return p.parse_args(argv)


def head_frame(skel, body):
    """Posed head centre and the matrix taking posed points back to the rest head."""
    c = K.head_centre(body)
    pb = skel.pose.bones.get("DEF-spine.006")
    if pb is None:
        return c, None
    rest = skel.matrix_world @ pb.bone.matrix_local
    posed = skel.matrix_world @ pb.matrix
    return np.array(posed @ (rest.inverted() @ Vector(c))), rest @ posed.inverted()


def covered(p_rest: np.ndarray, centre_rest: np.ndarray) -> bool:
    """Above the lowest hat band line (hair_frame.band_phi): the part of the head a
    hat covers. Hair below it (under a wide brim) is outside any hat."""
    d = p_rest - centre_rest
    x, f, u = d[0], -d[1], d[2]
    r = math.sqrt(x * x + f * f + u * u)
    if r < 1e-9:
        return False
    phi = math.degrees(math.acos(max(-1.0, min(1.0, u / r))))
    return phi < band_phi(math.degrees(math.atan2(x, f)) % 360.0)


def fitted(hat_obj, hair_obj):
    """The pair's fit from the hair's `hat_fit` (scale, cover line, cut
    margin, head centre in world axes), or None."""
    import json

    raw = hair_obj.get("hat_fit")
    if not raw:
        return None
    fit = json.loads(raw)
    h = fit.get("hats", {}).get(str(hat_obj.get("asset", "")))
    if h is None:
        return None
    c = fit["centre"]
    return {"scale": float(h["scale"]), "cover": np.array(h["cover"], dtype=np.float64), "step": float(fit["cover_step"]),
            "margin": float(fit["cut_margin"]), "centre": np.array([c[0], -c[2], c[1]]),
            "limit": np.array(h["limit"], dtype=np.float64) if h.get("limit") else None}


def polar(p_rest: np.ndarray, centre_rest: np.ndarray):
    d = p_rest - centre_rest
    x, f, u = d[:, 0], -d[:, 1], d[:, 2]
    r = np.linalg.norm(d, axis=1)
    phi = np.degrees(np.arccos(np.clip(u / np.maximum(r, 1e-12), -1.0, 1.0)))
    return phi, np.degrees(np.arctan2(x, f)) % 360.0


def kept_and_counted(hair_obj, rest_pts: np.ndarray, fit, rest_centre: np.ndarray):
    """Per hair vertex: kept under this hat (in a face the fit does not
    drop, or on the inner cap) and counted (kept, inside the hat's cover,
    above the band line + HAT_FIT_BELOW, not `hat_skip`: twin tails hanging
    out from under the brim)."""
    me = hair_obj.data
    sc = me.attributes.get(HM.SCALP_ATTRIBUTE)
    scalp = np.zeros(len(rest_pts), dtype=bool)
    if sc is not None:
        v = np.zeros(len(rest_pts), dtype=np.float32)
        sc.data.foreach_get("value", v)
        scalp = v > 0.5
    phi, theta = polar(rest_pts, fit["centre"])
    t = np.r_[np.arange(0.0, 360.0, fit["step"]), 360.0]
    cov = np.interp(theta, t, np.r_[fit["cover"], fit["cover"][0]])
    deep = (phi < cov - fit["margin"]) & ~scalp
    me.calc_loop_triangles()
    tri = np.array([tuple(x.vertices) for x in me.loop_triangles], dtype=np.int64)
    kept = np.zeros(len(rest_pts), dtype=bool)
    kept[tri[~deep[tri].all(axis=1)].ravel()] = True
    band = np.array([band_phi(x) for x in theta])
    fit["_kept"] = kept & ~scalp
    fit["_up"] = cov - phi
    skip = np.zeros(len(rest_pts), dtype=bool)
    hs = me.attributes.get("hat_skip")
    if hs is not None:
        v = np.zeros(len(rest_pts), dtype=np.int32)
        hs.data.foreach_get("value", v)
        skip = v > 0
    return kept & ~scalp & ~skip & (phi < cov) & (phi < band + HM.HAT_FIT_BELOW)


def press(pts: np.ndarray, fit, back) -> np.ndarray:
    """The hair under a capped hat's crown pressed in to its limit
    (hair_meshy.hat_press, as the client does it): in the rest frame of the
    head, then back to the pose."""
    if fit["limit"] is None:
        return pts
    M = np.array(back) if back is not None else np.eye(4)
    rest = pts @ M[:3, :3].T + M[:3, 3]
    c = fit["centre"]
    d = rest - c
    r = np.linalg.norm(d, axis=1)
    theta = np.degrees(np.arctan2(d[:, 0], -d[:, 1])) % 360.0
    up = fit["_up"]
    lim = HM.limit_at(fit["limit"], theta, up)
    m = fit["_kept"] & (up > 0.0) & (lim > 0.0) & (r > lim)
    rest[m] = c + d[m] * (lim[m] / r[m])[:, None]
    Mi = np.linalg.inv(M)
    return rest @ Mi[:3, :3].T + Mi[:3, 3]


def poke(hair_pts: np.ndarray, hat: BVHTree, centre: np.ndarray, tol: float, back=None, rest_centre=None,
         only=None) -> int:
    n = 0
    for i, p in enumerate(hair_pts):
        if only is not None:
            if not only[i]:
                continue
        elif back is not None and not covered(np.array(back @ Vector(p)), rest_centre):
            continue
        d = p - centre
        L = float(np.linalg.norm(d))
        if L < 1e-6:
            continue
        loc, nrm, idx, dist = hat.ray_cast(Vector(centre), Vector(d / L), L + 0.05)
        if loc is not None and dist < L - tol:
            n += 1
    return n


def check_body(path: str, tol: float) -> int:
    bpy.ops.wm.open_mainfile(filepath=path)
    body_id = "_".join(os.path.splitext(os.path.basename(path))[0].split("_")[:2])  # body_a_mh, body_a_sculpt -> body_a
    skel = bpy.data.objects[f"{body_id}_skeleton"]
    body = P.of_skeleton(skel, body_id)  # a reshaped body stores its own measurements
    lod0 = [o for o in bpy.data.objects if o.type == "MESH" and o.get("lod") == 0 and not o.get("region")]
    # Hats that cover the hair (`hides` scalp): hair trims (the ribbon bow,
    # the comb) sit on the hair as styled, the plain variant (the client's
    # library.hatCoversHair), and are not checked here.
    hats = sorted((o for o in lod0 if str(o.get("asset", "")).startswith("gear/hat/")
                   and "scalp" in list(o.get("hides", ["scalp"]))), key=lambda o: o.name)
    # The hair worn under hats: the file carrying the hat fits (a style's own
    # file, or its hat variant where its tails change under a hat).
    hairs = sorted((o for o in lod0 if str(o.get("asset", "")).startswith("hair/") and o.get("hat_fit")), key=lambda o: o.name)
    for o in hats + hairs:
        o.hide_set(False)
        o.hide_viewport = False
    print(f"\n{body_id}: {len(hats)} hats x {len(hairs)} hair worn under hats; hair vertices through the hat "
          f"(rest / walk / sit, worst build)")
    worst: dict[tuple[str, str], dict[str, int]] = {}
    scales = {}
    # Which hair vertices each pair keeps and counts: from the rest shape.
    rest_hair = {h.name: np.array([tuple(h.matrix_world @ v.co) for v in h.data.vertices]) for h in hairs}
    only = {}
    for hat in hats:
        for h in hairs:
            fit = fitted(hat, h)
            if fit is not None:
                only[(hat.name, h.name)] = kept_and_counted(h, rest_hair[h.name], fit, K.head_centre(body))
                scales[(hat.name, h.name)] = fit
    for pname, clip, frame in POSES:
        pose(skel, clip, frame)
        centre, back = head_frame(skel, body)
        for b in BUILDS:
            set_build(hats + hairs, b)
            hair_pts = {h.name: evaluated(h)[0] for h in hairs}
            for hat in hats:
                pts, polys = evaluated(hat)
                tree = BVHTree.FromPolygons(pts.tolist(), polys)
                for h in hairs:
                    fit = scales.get((hat.name, h.name))
                    if fit is not None:
                        # The fitted hat: scaled about the (posed) head centre.
                        fc = centre if back is None else np.array(back.inverted() @ Vector(fit["centre"]))
                        sp = fc + (pts - fc) * fit["scale"]
                        t2 = BVHTree.FromPolygons(sp.tolist(), polys)
                        # (Rays from the centre the fit was measured from.)
                        n = poke(press(hair_pts[h.name], fit, back), t2, fc, tol, only=only[(hat.name, h.name)])
                    else:
                        n = poke(hair_pts[h.name], tree, centre, tol, back, K.head_centre(body))
                    w = worst.setdefault((hat.name, h.name), {})
                    w[pname] = max(w.get(pname, 0), n)
    failures = 0
    for (hat, hair), w in sorted(worst.items()):
        bad = w["rest"] > 0
        failures += bad
        if bad or any(w.values()):
            print(f"  {'FAIL' if bad else 'ok':4} {hat:34} {hair:32} " + " ".join(f"{w[p]:5d}" for p, _, _ in POSES))
    clean = sum(1 for w in worst.values() if not any(w.values()))
    print(f"  {clean} of {len(worst)} hat/hair pairs have no hair through the hat in any pose")
    set_build(hats + hairs, "")
    return failures


def main() -> None:
    a = args()
    failures = 0
    for body in a.bodies.split(","):
        failures += check_body(os.path.join(os.path.abspath(a.art), "characters", f"{body}{a.suffix}.blend"), a.tolerance)
    print(f"\n{failures} hat/hair pair(s) poke through in the rest pose")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
