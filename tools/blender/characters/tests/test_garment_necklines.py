"""Necklines sit at the base of the neck and turn with it (a review of the
cream sweater, 2026-11-03: the crew neck stood well off the neck all round,
its dark inside showing, and opened further on the side the head turned to).

Two causes, both in the fitting, so the checks are per body:

* a rib band grown from a neckline went straight up at the neckline's
  radius while the neck narrows above it, and kept the neckline's chest
  weights; it now lies on the skin (`addons.rib_band`, `_hug_skin`) and
  takes the neck's weights toward its top;
* the neck charts (collar stands, turtlenecks) cast from the neck bones,
  which on the wrapped female sit 25-40 mm in front of the throat, so the
  front of every stand fell through; they now cast from the neck's measured
  centre line (`fit.BodyFrame.neck_centre`).

Measured on the exported gear (the assets root) loaded onto the saved body
blends (art/characters/<body>.blend), at rest and with the head turned to
the look limit either way, shared over the look bones as the client does
(client/src/game/characters/look.ts). Skipped when either is missing.

* rim gap: the highest garment vertex in each 15 degree sector round the
  neck, its distance to the neck skin, averaged round the neck, at rest
  (before -> after the change, body_a / body_b): cable knit sweater
  23 / 26 -> 6 / 12 mm, fair isle 18 / 21 -> 6 / 10, turtleneck on body_b
  21 -> 9;
* through: collar vertices (within 6 cm of the neck skin) more than 2 mm
  inside the neck or torso skin, which must stay 0.

The ruffled silk blouse (2026-11-04: its frill stood off the neck with skin
showing under it, and the shirt's inside showed at both sides of her neck)
adds two checks:

* its neck frill lies on the neck (`addons.ruffle` hugs from a neckline):
  rim gap 25 / 22 -> 7 / 9 mm (body_b / body_a);
* inside seen (`InsideTests`): the area of the garment's back faces a camera
  sees round the collar (nothing in front of them; the torso and arms
  hidden, as the client hides them), from the front, the front three-
  quarter and the game camera's height. A collar stand cast at the
  column's azimuth from the neck's centre line sat 20-25 degrees further
  round the neck than the neckline under it on the wrapped female, and the
  twisted faces between showed the inside (`shapes._over_base`): oxford
  shirt and waistcoat 29 -> 1 cm2, turtleneck 36 -> 0, blouse 46 -> 6 (front,
  body_b; from the game camera 34 -> 1)."""

import math
import os
import re
import unittest

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

import check_garment_poke as CP
from charlib import content

ASSETS = os.environ.get("SHS_CHARACTER_ASSETS", os.path.join(content.REPO, "client", "public", "assets"))
BLENDS = os.path.join(content.REPO, "art", "characters")
LOOK_TS = os.path.join(content.REPO, "client", "src", "game", "characters", "look.ts")
# (garment, body, mean rim gap limit in mm)
CASES = [
    ("gear/body/sweater_cable_knit_a", "body_a", 8.0),
    ("gear/body/sweater_cable_knit_a", "body_b", 14.0),
    ("gear/body/jumper_fair_isle_a", "body_a", 8.0),
    ("gear/body/jumper_fair_isle_a", "body_b", 14.0),
    ("gear/body/turtleneck_ribbed_a", "body_b", 12.0),
    ("gear/body/blouse_ruffled_silk_a", "body_a", 13.0),
    ("gear/body/blouse_ruffled_silk_a", "body_b", 11.0),
]
# (garment, body, inside-seen limit in cm2 from each camera)
INSIDE_CASES = [
    ("gear/body/blouse_ruffled_silk_a", "body_a", 10.0),
    ("gear/body/blouse_ruffled_silk_a", "body_b", 8.0),
    ("gear/body/shirt_oxford_tie_a", "body_b", 3.0),
    ("gear/body/waistcoat_brocade_a", "body_b", 3.0),
    ("gear/body/turtleneck_ribbed_a", "body_b", 3.0),
]
# (name, azimuth from the front in degrees, distance m, height over the collar m)
INSIDE_CAMERAS = [("front", 0, 0.6, 0.12), ("front34", 35, 0.6, 0.10), ("game34", 35, 6.5, 2.4)]
INSIDE_REGIONS = ("neck", "head", "scalp", "ears", "hands")
SECTOR = 15
THROUGH_M = 0.002


def look_bones() -> dict[str, float]:
    """The client's look bones and shares, and its yaw limit (degrees)."""
    src = open(LOOK_TS, encoding="utf-8").read()
    names = re.findall(r'"(DEF-[^"]+)"', re.search(r"LOOK_BONES\s*=\s*\[([^\]]*)\]", src).group(1))
    shares = [float(x) for x in re.search(r"LOOK_SHARES[^=]*=\s*\[([^\]]*)\]", src).group(1).split(",") if x.strip()]
    yaw = float(re.search(r"LOOK_YAW_MAX\s*=\s*([\d.]+)\s*\*\s*DEG", src).group(1))
    return dict(zip(names, shares)), yaw


def _evaluated(objs) -> tuple[list, list]:
    dg = bpy.context.evaluated_depsgraph_get()
    P, F = [], []
    for o in objs:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        off = len(P)
        P.extend(o.matrix_world @ v.co for v in me.vertices)
        F.extend(tuple(i + off for i in p.vertices) for p in me.polygons)
        ev.to_mesh_clear()
    return P, F


def measure(skel, gear, neck, trunk, shares: dict, yaw: float) -> dict:
    for pb in skel.pose.bones:
        pb.matrix_basis.identity()
    for name, share in shares.items():
        pb = skel.pose.bones.get(name)
        if pb is not None:
            pb.rotation_mode = "XYZ"
            pb.rotation_euler = (0.0, math.radians(yaw) * share, 0.0)  # about the bone's own (upward) axis
    bpy.context.view_layer.update()
    nP, nF = _evaluated(neck)
    neck_tree = BVHTree.FromPolygons(nP, nF)
    tP, tF = _evaluated(neck + trunk)
    trunk_tree = BVHTree.FromPolygons(tP, tF)
    gP, _ = _evaluated(gear)
    nv = np.array(nP)
    z0 = float(nv[:, 2].min())
    c = nv[:, :2].mean(axis=0)
    pts = np.array([p for p in gP if p.z > z0 - 0.03 and math.hypot(p.x - c[0], p.y - c[1]) < 0.11])
    through = 0
    for p in pts:
        loc, nrm, idx, dist = trunk_tree.find_nearest(Vector(p), 0.06)
        if loc is None:
            continue
        h = float((Vector(p) - loc) @ nrm)
        if h < -THROUGH_M and -h >= 0.5 * dist:
            through += 1
    ang = np.degrees(np.arctan2(pts[:, 0] - c[0], -(pts[:, 1] - c[1]))) % 360
    gaps = []
    for s in range(0, 360, SECTOR):
        m = (ang >= s) & (ang < s + SECTOR)
        if m.any():
            q = pts[m][np.argmax(pts[m][:, 2])]
            loc, nrm, idx, dist = neck_tree.find_nearest(Vector(q), 0.3)
            if loc is not None:
                gaps.append(dist * 1000)
    return {"rim_mm": float(np.mean(gaps)), "through": through}


class NecklineTests(unittest.TestCase):
    def test_necklines_hug_the_neck_and_turn_with_it(self):
        shares, yaw = look_bones()
        ran = 0
        for asset, body, limit in CASES:
            blend = os.path.join(BLENDS, f"{body}.blend")
            glb = os.path.join(ASSETS, f"{asset}_{body}.glb")
            if not os.path.exists(blend) or not os.path.exists(glb):
                continue
            bpy.ops.wm.open_mainfile(filepath=blend)
            skel = CP._skeleton()
            ad = skel.animation_data
            if ad:
                ad.action = None
                for t in ad.nla_tracks:
                    t.mute = True
            body_objs = [o for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0]
            neck = [o for o in body_objs if o.get("region") == "neck"]
            trunk = [o for o in body_objs if o.get("region") == "torso"]
            if not neck or not trunk:
                continue
            gear = CP._load(glb, skel)
            for turn in (0.0, yaw, -yaw):
                r = measure(skel, gear, neck, trunk, shares, turn)
                with self.subTest(asset=asset, body=body, yaw=turn):
                    self.assertEqual(r["through"], 0, r)
                    self.assertLess(r["rim_mm"], limit + (3.0 if turn else 0.0), r)
            ran += 1
        if not ran:
            self.skipTest("no exported gear or body blends")


def inside_seen(skel, gear, visible) -> dict:
    """cm2 of garment back faces in view round the collar, per camera (rest pose)."""
    for pb in skel.pose.bones:
        pb.matrix_basis.identity()
    bpy.context.view_layer.update()
    gP, gF = _evaluated(gear)
    bP, bF = _evaluated(visible)
    tree = BVHTree.FromPolygons(gP + bP, gF + [tuple(i + len(gP) for i in f) for f in bF])
    target = skel.matrix_world @ skel.pose.bones["DEF-spine.004"].head - Vector((0.0, 0.0, 0.03))
    out = {}
    for name, ang, dist, up in INSIDE_CAMERAS:
        a = math.radians(ang)
        cam = target + Vector((math.sin(a) * dist, -math.cos(a) * dist, up))
        area = 0.0
        for fi, f in enumerate(gF):
            P = [Vector(gP[i]) for i in f]
            c = sum(P, Vector()) / len(P)
            if (c - target).length > 0.12:
                continue
            n = (P[1] - P[0]).cross(P[2] - P[0])
            if n.length < 1e-12 or n.dot(cam - c) >= 0:
                continue
            hit = tree.ray_cast(cam, (c - cam).normalized(), (c - cam).length + 1e-4)
            if hit[2] == fi:
                area += 0.5 * n.length
        out[name] = round(area * 1e4, 2)
    return out


class InsideTests(unittest.TestCase):
    def test_collars_do_not_show_the_inside(self):
        ran = 0
        for asset, body, limit in INSIDE_CASES:
            blend = os.path.join(BLENDS, f"{body}.blend")
            glb = os.path.join(ASSETS, f"{asset}_{body}.glb")
            if not os.path.exists(blend) or not os.path.exists(glb):
                continue
            bpy.ops.wm.open_mainfile(filepath=blend)
            skel = CP._skeleton()
            ad = skel.animation_data
            if ad:
                ad.action = None
                for t in ad.nla_tracks:
                    t.mute = True
            visible = [o for o in bpy.data.objects
                       if o.type == "MESH" and o.get("lod") == 0 and o.get("region") in INSIDE_REGIONS]
            r = inside_seen(skel, CP._load(glb, skel), visible)
            for cam, cm2 in r.items():
                with self.subTest(asset=asset, body=body, camera=cam):
                    self.assertLess(cm2, limit, r)
            ran += 1
        if not ran:
            self.skipTest("no exported gear or body blends")


if __name__ == "__main__":
    unittest.main()


class NeckPivotTests(unittest.TestCase):
    """The neck and head bones (the look turn's pivots) run through the
    neck: on the wrapped female they sat 25-40 mm in front of her throat
    (spine.004 at y 0.010 with the neck skin at y 0.035-0.092), so a head
    turn swung her neck sideways and collars sheared with it
    (shape_target.neck_line moves them onto the measured neck line)."""

    TOL = 0.012

    def test_neck_bones_run_through_the_neck(self):
        ran = 0
        for body in ("body_a", "body_b"):
            blend = os.path.join(BLENDS, f"{body}.blend")
            if not os.path.exists(blend):
                continue
            bpy.ops.wm.open_mainfile(filepath=blend)
            skel = CP._skeleton()
            neck = next((o for o in bpy.data.objects if o.type == "MESH" and o.get("region") == "neck" and o.get("lod") == 0), None)
            if neck is None:
                continue
            V = np.array([neck.matrix_world @ v.co for v in neck.data.vertices])
            for name in ("DEF-spine.004", "DEF-spine.005", "DEF-spine.006"):
                h = skel.matrix_world @ skel.data.bones[name].head_local
                q = V[np.abs(V[:, 2] - h.z) < 0.006]
                with self.subTest(body=body, bone=name):
                    self.assertGreaterEqual(len(q), 4)
                    centre = 0.5 * (q[:, 1].min() + q[:, 1].max())
                    self.assertLess(abs(h.y - centre), self.TOL, (round(h.y, 4), round(float(centre), 4)))
            ran += 1
        if not ran:
            self.skipTest("no body blends")
