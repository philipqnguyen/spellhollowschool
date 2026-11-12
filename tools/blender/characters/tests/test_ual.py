"""Walk and run from Quaternius's Universal Animation Library
(charlib/ual.py; needs bpy and the Rigify add-on): the extracted source is
the in-place library under its CC0 licence, the clips loop and keep their
timing, and on both bodies the feet stand on the floor, the run leaves it,
the back foot folds up under the hip (as far behind as the source's, both
legs alike), each foot swings along the line of its own footfalls, the
female's feet come in, the face looks ahead and the walk's planted feet
slide less than they did."""

import math
import os
import unittest

import bpy
from mathutils import Vector

from charlib import anim as AN
from charlib import rig as RG
from charlib import skeleton as K
from charlib import ual as U
from test_knees import bodies

CLIPS = {c.name: c for c in AN.CLIPS}
HEAD_TOL_DEG = 4.0
# The walk's planted foot (its lowest sole point) moves back at least this
# fast at the game's walk speed (1.25x and the source's stride at 4.0 m/s:
# 0.74 to 0.78 m/s, a slide of 3.2 m/s).
WALK_CONTACT_MIN_MPS = 0.9
WALK_SLIDE_MAX_MPS = 2.1
RUN_ARM_MAX_DEG = 22.0
# Second pass: the run's arms swing further to the front.
RUN_ARM_FRONT_DEG = 35.0  # forward-most at least (was 26 to 28)
RUN_ARM_BACK_DEG = -25.0  # back-most no further than (was -30 to -32)
RUN_ARM_SWING_DEG = 36.0  # the source sprint's own forward-and-back swing about the chest


def arm_angles(rig, S: str) -> tuple[float, float]:
    """The posed upper arm's lean out of the chest's midplane (degrees, +
    away from the body) and its swing in that plane (degrees from straight
    down, + forward)."""
    pb = rig.pose.bones
    ch = pb["ORG-spine.003"]
    q = ch.matrix.to_quaternion() @ ch.bone.matrix_local.to_quaternion().inverted()
    out = q @ Vector((1.0 if S == "L" else -1.0, 0.0, 0.0))
    fwd, up = q @ Vector((0.0, -1.0, 0.0)), q @ Vector((0.0, 0.0, 1.0))
    ua = pb[f"ORG-upper_arm.{S}"]
    d = (ua.tail - ua.head).normalized()
    return math.degrees(math.asin(max(-1.0, min(1.0, d.dot(out))))), math.degrees(math.atan2(d.dot(fwd), -d.dot(up)))


def sole_low(rig, body, side: str) -> float:
    """Height of the posed foot's lowest sole point (heel, ball, toe tip on
    the floor at rest; the tip moves with the toe bone), metres."""
    fp = K.foot_points(body)
    if side == "R":
        fp = {k: K.mirror(v) for k, v in fp.items()}
    pb = rig.pose.bones
    low = math.inf
    for key, bone in (("heel_back", "ORG-foot"), ("ball", "ORG-foot"), ("tip", "ORG-toe")):
        b = pb[f"{bone}.{side}"]
        p = Vector((fp[key][0], fp[key][1], 0.0))
        low = min(low, (b.matrix @ b.bone.matrix_local.inverted() @ p).z)
    return low


def step_width(rows) -> float:
    """The feet's distance across the walk where they stand: each ankle
    taken at its own mid-stance (the foot low on the floor and under its
    hip), the left one's distance from the midline plus the right one's."""
    out = 0.0
    for s in ("L", "R"):
        zmin = min(r[s]["ankle"].z for r in rows)
        low = [r for r in rows if r[s]["ankle"].z < zmin + 0.02]
        r = min(low, key=lambda r: abs(r[s]["ankle"].y - r[s]["hip"].y))
        out += abs(r[s]["ankle"].x)
    return out


def contact_y(rig, body, side: str) -> float:
    """Forward position (Y, + behind) of the posed foot's lowest sole point."""
    fp = K.foot_points(body)
    if side == "R":
        fp = {k: K.mirror(v) for k, v in fp.items()}
    pb = rig.pose.bones
    best = None
    for key, bone in (("heel_back", "ORG-foot"), ("ball", "ORG-foot"), ("tip", "ORG-toe")):
        b = pb[f"{bone}.{side}"]
        p = b.matrix @ b.bone.matrix_local.inverted() @ Vector((fp[key][0], fp[key][1], 0.0))
        if best is None or p.z < best.z:
            best = p
    return best.y


def segment_gap(a0, a1, b0, b1, n: int = 12) -> float:
    """Closest distance between two segments (sampled)."""
    return min((a0.lerp(a1, i / n) - b0.lerp(b1, j / n)).length for i in range(n + 1) for j in range(n + 1))


class SourceTests(unittest.TestCase):
    def test_in_place_cc0_source(self):
        src = U.source()
        self.assertEqual(src.meta["file"], "UAL1.glb")
        self.assertEqual(src.meta["licence"], "CC0 1.0")
        self.assertEqual(src.meta["author"], "Quaternius")
        with open(os.path.join(os.path.dirname(U.SOURCE), "License.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("CC0 1.0 Universal", text)
        self.assertIn("@Quaternius", text)
        self.assertEqual(set(src.clips), {"Walk_Loop", "Sprint_Loop"})
        # In place: the pelvis never travels forward more than a few centimetres.
        for clip in src.clips:
            ys = [src.pose(clip, t)["pelvis"][0].y for t in src.clips[clip]["times"]]
            self.assertLess(max(ys) - min(ys), 0.03, clip)

    def test_loops_close(self):
        src = U.source()
        for clip in src.clips:
            a, b = src.pose(clip, 0.0), src.pose(clip, src.duration(clip) - 1e-6)
            for name in ("pelvis", "foot_l", "hand_r"):
                self.assertLess((a[name][0] - b[name][0]).length, 0.005, (clip, name))

    def test_clip_timing(self):
        # The walk plays WALK_RATE times faster than the source (1.333 s), the run at its own rate (0.667 s).
        self.assertEqual(CLIPS["walk"].frames, round(40 / U.WALK_RATE))
        self.assertEqual(CLIPS["run"].frames, 20)
        # A faster walk (1.25 before), still a walk, not a jog.
        self.assertTrue(1.5 <= U.WALK_RATE <= 1.6)
        self.assertEqual(CLIPS["walk"].speed, AN.WALK_SPEED)
        self.assertEqual(CLIPS["run"].speed, AN.RUN_SPEED)
        meta = AN.clip_meta()
        self.assertIn("Walk_Loop", meta["walk"]["source"])
        self.assertIn("Sprint_Loop", meta["run"]["source"])


class RetargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        RG.enable_rigify()
        cls.rigs = {bid: (RG.generate(RG.build_metarig(b, f"metarig_{bid}"), f"rig_{bid}"), b) for bid, b in bodies().items()}
        cls.track = {}
        for bid, (rig, body) in cls.rigs.items():
            for name in ("walk", "run"):
                clip = CLIPS[name]
                rows = []
                for f in range(clip.frames):
                    AN.pose_rig(rig, body, clip, float(f))
                    bpy.context.view_layer.update()
                    pb = rig.pose.bones
                    row = {}
                    for s in ("L", "R"):
                        row[s] = {"low": sole_low(rig, body, s), "ankle": pb[f"ORG-foot.{s}"].head.copy(),
                                  "miss": (pb[f"ORG-foot.{s}"].head - pb[f"foot_ik.{s}"].head).length,
                                  "hip": pb[f"ORG-thigh.{s}"].head.copy(), "knee": pb[f"ORG-shin.{s}"].head.copy(),
                                  "toe": pb[f"ORG-toe.{s}"].tail.copy(), "contact": contact_y(rig, body, s)}
                    rows.append(row)
                cls.track[bid, name] = rows

    def test_feet_stand_on_the_floor(self):
        """Each foot spends part of every stride on the floor, and never sinks into it."""
        for (bid, name), rows in self.track.items():
            for s in ("L", "R"):
                lows = [r[s]["low"] for r in rows]
                self.assertGreater(min(lows), -0.015, (bid, name, s))
                self.assertLess(min(lows), 0.015, (bid, name, s))

    def test_planted_feet_are_reached(self):
        """Where the source foot is on the floor our leg reaches it (the hips come down if needed)."""
        for (bid, name), rows in self.track.items():
            for s in ("L", "R"):
                for f, r in enumerate(rows):
                    if r[s]["low"] < 0.01:
                        self.assertLess(r[s]["miss"], 0.005, (bid, name, s, f))

    def test_the_run_leaves_the_floor(self):
        for bid in self.rigs:
            rows = self.track[bid, "run"]
            flight = [f for f, r in enumerate(rows) if min(r["L"]["low"], r["R"]["low"]) > 0.03]
            self.assertGreaterEqual(len(flight), 2, bid)
            walk = self.track[bid, "walk"]
            self.assertFalse([f for f, r in enumerate(walk) if min(r["L"]["low"], r["R"]["low"]) > 0.03], bid)

    def test_the_run_folds_the_back_foot_under_the_hip(self):
        """In the swing the running heel comes up high behind, under the hip, not left stretched out behind."""
        for bid, (rig, body) in self.rigs.items():
            rows = self.track[bid, "run"]
            for s in ("L", "R"):
                top = max(rows, key=lambda r: r[s]["ankle"].z)
                hip_h = top[s]["hip"].z
                self.assertGreater(top[s]["ankle"].z, 0.35 * hip_h, (bid, s))
                behind = top[s]["ankle"].y - top[s]["hip"].y  # +Y is behind
                self.assertLess(behind, 0.75, (bid, s))
                self.assertGreater(behind, 0.0, (bid, s))

    def test_female_feet_land_near_one_line(self):
        """Required: body_b's feet land close to one line under
        the body in walk and run (a step width of 7 to 10 cm); body_a keeps
        the source's spacing (its rest stance)."""
        self.assertNotIn("body_a", U.NARROW_M)
        stance = {bid: self.rigs[bid][0].data.bones["ORG-foot.L"].head_local.x * 2 for bid in self.rigs}
        for name in ("walk", "run"):
            w = step_width(self.track["body_b", name])
            self.assertTrue(0.07 <= w <= 0.10, (name, w))
            self.assertAlmostEqual(w, stance["body_b"] - 2 * U.NARROW_M["body_b"], delta=0.01)
            self.assertAlmostEqual(step_width(self.track["body_a", name]), stance["body_a"], delta=0.01)

    def test_swing_foot_stays_on_its_line(self):
        """A swinging foot (the run's trailing foot, the walk's
        lifting foot) never swings out to the side. While it is off the floor
        each ankle stays within 5 mm of where that foot stands, the toes
        within 1 cm (the source's ankles do not move sideways), and the knee
        bends straight ahead, between the hip and the ankle seen from the
        front (within 1 cm)."""
        for (bid, name), rows in self.track.items():
            for s in ("L", "R"):
                stand = [abs(r[s]["ankle"].x) for r in rows if r[s]["low"] < 0.01]
                line = sum(stand) / len(stand)
                swing = [r[s] for r in rows if r[s]["low"] > 0.02]
                self.assertTrue(swing, (bid, name, s))
                for r in swing:
                    self.assertLess(abs(abs(r["ankle"].x) - line), 0.005, (bid, name, s))
                    self.assertLess(abs(r["toe"].x), line + 0.01, (bid, name, s))
                    lo, hi = sorted((abs(r["hip"].x), abs(r["ankle"].x)))
                    self.assertTrue(lo - 0.01 < abs(r["knee"].x) < hi + 0.01, (bid, name, s, r["knee"].x))

    def test_run_trailing_leg_reaches_as_far_as_the_source(self):
        """Review: the trailing leg extended too little behind before the
        foot picked up. Its ankle now reaches the source's (scaled) distance
        behind the hip, and both legs fold alike (the source's right leg
        folds 13 cm lower than its left; ours were 0.39 and 0.53 m)."""
        src = U.source()
        times = src.clips["Sprint_Loop"]["times"]
        for bid, (rig, body) in self.rigs.items():
            rows = self.track[bid, "run"]
            k = rig.data.bones["ORG-thigh.L"].head_local.z / src.rest["thigh_l"][0].z
            reach = max((w["foot_l"][0].y - w["thigh_l"][0].y) * k for w in (src.pose("Sprint_Loop", t) for t in times))
            tops = []
            for s in ("L", "R"):
                behind = max(r[s]["ankle"].y - r[s]["hip"].y for r in rows)
                self.assertGreater(behind, reach - 0.01, (bid, s, behind, reach))
                tops.append(max(r[s]["ankle"].z for r in rows))
            self.assertLess(abs(tops[0] - tops[1]), 0.01, (bid, tops))

    def test_walk_feet_slide_less(self):
        """Less foot slide in the walk. At sim-core's walk speed the
        planted foot's lowest point moves back at least WALK_CONTACT_MIN_MPS
        (its slide over the ground at most WALK_SLIDE_MAX_MPS)."""
        for bid in self.rigs:
            rows = self.track[bid, "walk"]
            n = len(rows)
            for s in ("L", "R"):
                v = [(rows[(i + 1) % n][s]["contact"] - rows[i - 1][s]["contact"]) * AN.FPS / 2 for i in range(n)
                     if rows[i][s]["low"] < 0.01]
                mean = sum(v) / len(v)
                self.assertGreater(mean, WALK_CONTACT_MIN_MPS, (bid, s))
                self.assertLess(sum(abs(AN.WALK_SPEED - x) for x in v) / len(v), WALK_SLIDE_MAX_MPS, (bid, s))

    def test_narrow_legs_keep_clear(self):
        """Narrower is not crossed: across walk and run the two legs' bone
        lines stay further apart than a shin's or a knee's thickness."""
        for (bid, name), rows in self.track.items():
            for f, r in enumerate(rows):
                L, R = r["L"], r["R"]
                self.assertGreater(L["ankle"].x - R["ankle"].x, 0.07, (bid, name, f))  # never crossing the midline
                self.assertGreater(segment_gap(L["knee"], L["ankle"], R["knee"], R["ankle"]), 0.095, (bid, name, f))
                self.assertGreater(segment_gap(L["ankle"], L["toe"], R["ankle"], R["toe"]), 0.08, (bid, name, f))
                self.assertGreater((L["knee"] - R["knee"]).length, 0.105, (bid, name, f))

    def test_face_looks_ahead(self):
        """The source's head always faces downward (15 degrees
        walking, 23 running); the face's forward stays within HEAD_TOL_DEG of
        the horizon through both loops, still bobbing a little."""
        for bid, (rig, body) in self.rigs.items():
            for name in ("walk", "run"):
                clip = CLIPS[name]
                pitches = []
                for f in range(clip.frames):
                    AN.pose_rig(rig, body, clip, float(f))
                    bpy.context.view_layer.update()
                    pb = rig.pose.bones["ORG-spine.006"]
                    q = pb.matrix.to_quaternion() @ pb.bone.matrix_local.to_quaternion().inverted()
                    pitches.append(math.degrees(U.pitch(q)))
                self.assertLess(max(abs(p) for p in pitches), HEAD_TOL_DEG, (bid, name, pitches))
                self.assertGreater(max(pitches) - min(pitches), 0.5, (bid, name))  # not frozen

    def test_run_arms_pump_close_to_the_body(self):
        """The sprint's arms swung 25 to 43 degrees out from the
        body; they pump forward and back close to the torso now, at most
        RUN_ARM_MAX_DEG out of the chest's midplane (never inward past it),
        and still swing well forward and back."""
        for bid, (rig, body) in self.rigs.items():
            for S in ("L", "R"):
                out, swing = [], []
                for f in range(CLIPS["run"].frames):
                    AN.pose_rig(rig, body, CLIPS["run"], float(f))
                    bpy.context.view_layer.update()
                    a, s = arm_angles(rig, S)
                    out.append(a)
                    swing.append(s)
                self.assertLessEqual(max(out), RUN_ARM_MAX_DEG, (bid, S, out))
                self.assertGreater(min(out), 0.0, (bid, S, out))
                self.assertGreater(max(swing) - min(swing), RUN_ARM_SWING_DEG, (bid, S, swing))
                self.assertGreater(max(swing), RUN_ARM_FRONT_DEG, (bid, S, swing))
                self.assertGreater(min(swing), RUN_ARM_BACK_DEG, (bid, S, swing))

    def test_arms_swing_opposite_the_legs(self):
        """When the left foot is forward the left hand is back (the source's arm swing, retargeted)."""
        for bid, (rig, body) in self.rigs.items():
            for name in ("walk", "run"):
                clip = CLIPS[name]
                rows = self.track[bid, name]
                f = min(range(clip.frames), key=lambda i: rows[i]["L"]["ankle"].y)  # left foot furthest forward
                AN.pose_rig(rig, body, clip, float(f))
                bpy.context.view_layer.update()
                pb = rig.pose.bones
                self.assertGreater(pb["ORG-hand.L"].head.y, pb["ORG-hand.R"].head.y, (bid, name))
                # The hands hang by the sides (not raised into the source's T-pose).
                for s in ("L", "R"):
                    self.assertLess(pb[f"ORG-hand.{s}"].head.z, pb[f"ORG-upper_arm.{s}"].head.z - 0.2, (bid, name, s))


if __name__ == "__main__":
    unittest.main()
