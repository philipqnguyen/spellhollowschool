"""Knees bend forward in every clip (needs bpy and Rigify).

The rig's knee has a forward rest bend (`skeleton.KNEE_LEAD`), so Rigify's
IK legs bend the knee toward the toes; before, the wrapped bodies' straight
rest legs bent it backward in walk, run and the duel stances, and the seated
poses wound the whole leg half a turn to bring it forward again. Checked on
the shipped bodies' measurements (the baked shape targets in the art
repository) and, without them, on the spec bodies."""

import dataclasses
import math
import os
import unittest

import bpy
from mathutils import Vector

from charlib import anim as AN
from charlib import content
from charlib import proportions as P
from charlib import rig as RG
from charlib import shape_target as ST
from charlib import skeleton as K

TARGET_DIR = os.path.join(content.REPO, ST.DEFAULT_DIR)
# A straight leg may sit on the line (the stance leg of a stride); never behind it.
BEHIND_TOL_CM = 0.3
SAMPLES = 6


def bodies() -> dict:
    out = {}
    for bid in P.SPEC:
        path = ST.target_path(bid, TARGET_DIR)
        if os.path.exists(path):
            b = ST.load_target(path)["body"]
            out[bid] = dataclasses.replace(b, height_range=P.SPEC[bid].height_range)
        else:
            out[bid] = P.SPEC[bid]
    return out


def knee_ahead_cm(rig, side: str) -> float:
    """The knee's distance ahead of the hip-ankle line, along the thigh's own
    front (the rest forward turned with the thigh: where the kneecap faces)."""
    pb = rig.pose.bones
    th, sh, ft = pb[f"ORG-thigh.{side}"], pb[f"ORG-shin.{side}"], pb[f"ORG-foot.{side}"]
    front = (th.matrix.to_3x3() @ th.bone.matrix_local.to_3x3().inverted()) @ Vector((0, -1, 0))
    hip, knee, ank = th.head, sh.head, ft.head
    line = ank - hip
    off = knee - (hip + line * ((knee - hip).dot(line) / line.length_squared))
    return off.dot(front) * 100.0


class KneeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        RG.enable_rigify()
        cls.rigs = {bid: (RG.generate(RG.build_metarig(b, f"metarig_{bid}"), f"rig_{bid}"), b) for bid, b in bodies().items()}

    def test_rest_knee_bends_forward(self):
        for bid, b in bodies().items():
            j = K.joint_map(b, rig=True)
            hip, knee, ankle = j["thigh.L"].head, j["shin.L"].head, j["shin.L"].tail
            line = ankle - hip
            off = knee - (hip + line * float((knee - hip) @ line) / float(line @ line))
            self.assertGreaterEqual(-off[1], K.KNEE_LEAD - 1e-6, bid)
            # The mesh's own joints are unchanged (shape targets and weights stay valid).
            self.assertEqual(tuple(K.joint_map(b)["shin.L"].head), tuple(K.fwd(*b.knee)), bid)

    def test_no_clip_bends_a_knee_backward(self):
        for bid, (rig, body) in self.rigs.items():
            for clip in AN.CLIPS:
                step = max(1, clip.frames // SAMPLES)
                for f in range(0, clip.frames + 1, step):
                    AN.pose_rig(rig, body, clip, float(f))
                    bpy.context.view_layer.update()
                    for side in ("L", "R"):
                        self.assertGreater(knee_ahead_cm(rig, side), -BEHIND_TOL_CM, (bid, clip.name, f, side))

    def test_walk_and_run_bend_the_swing_knee_well_forward(self):
        for bid, (rig, body) in self.rigs.items():
            for name in ("walk", "run"):
                clip = next(c for c in AN.CLIPS if c.name == name)
                most = 0.0
                for f in range(clip.frames):
                    AN.pose_rig(rig, body, clip, float(f))
                    bpy.context.view_layer.update()
                    most = max(most, knee_ahead_cm(rig, "L"), knee_ahead_cm(rig, "R"))
                self.assertGreater(most, 10.0, (bid, name))

    def test_seated_legs_are_not_wound(self):
        """The seated knee needs (almost) no twist about the hip-foot line:
        a large one winds the thigh's twist bones and pinches the knee."""
        for bid, (rig, body) in self.rigs.items():
            clip = next(c for c in AN.CLIPS if c.name == "sit")
            AN.pose_rig(rig, body, clip, 0.0)
            bpy.context.view_layer.update()
            for side in ("L", "R"):
                q = rig.pose.bones[f"thigh_ik.{side}"].rotation_quaternion
                twist = abs(math.degrees(2.0 * math.atan2(q.y, q.w)))
                self.assertLess(twist, 30.0, (bid, side))


if __name__ == "__main__":
    unittest.main()
