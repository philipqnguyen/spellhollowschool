"""The wand grip (charlib/wand_grip.py): the handle sits diagonally in the
right fist, the fingers and the thumb close round it without passing
through it, and the pommel and collar are where they show (pure numpy)."""

import math
import unittest

import numpy as np

from charlib import wand_grip as WG
from charlib.proportions import BODIES

# The wand's profile (tools/blender/vfx/vfx_models.py `wand_profile`): the
# collar and the pommel's end along the shaft from the grip (m).
COLLAR = (0.030, 0.043)
POMMEL_END = -0.09


class WandGripTests(unittest.TestCase):
    def test_diagonal_grip_frame(self):
        for bid, body in BODIES.items():
            f = WG.grip_frame(body)
            self.assertAlmostEqual(float(np.linalg.norm(f.wand)), 1.0, places=6)
            off = math.degrees(math.acos(float(f.wand @ f.axis)))
            # Diagonal: well short of a hammer grip (about 70), toward the thumb.
            self.assertAlmostEqual(off, WG.ANGLE_DEG, delta=0.5, msg=bid)
            self.assertGreater(float(f.wand @ f.thumb), 0.0, bid)
            # The handle on the palm side of the hand.
            self.assertGreater(float((f.grip - f.wrist) @ f.palm), 0.02, bid)

    def test_fingers_wrap_the_handle(self):
        for bid, body in BODIES.items():
            f = WG.grip_frame(body)
            reach = WG.HANDLE_R + WG.FINGER_R * f.scale
            curls = WG.grip_curls(body)
            chains = WG.gripped_chains(body)
            for name in ("f_index", "f_middle", "f_ring", "f_pinky"):
                # Closed at the knuckles, not a claw, within the joints' range.
                mcp, pip, dip = curls[name]
                self.assertGreaterEqual(mcp, 20.0, (bid, name))
                self.assertGreaterEqual(pip, 20.0, (bid, name))
                self.assertLessEqual(mcp, WG.MCP_MAX, (bid, name))
                self.assertLessEqual(pip, WG.PIP_MAX, (bid, name))
                d, _ = WG.axis_distance(chains[name][1:], f)
                # Every joint and the tip round the handle: none through it, none far off.
                self.assertGreater(float(d.min()), WG.HANDLE_R + 0.003, (bid, name))
                self.assertLess(float(d.max()), reach + 0.015, (bid, name))
            d, _ = WG.axis_distance(chains["thumb"][1:], f)
            self.assertGreater(float(d.min()), WG.HANDLE_R + 0.003, bid)
            self.assertLess(float(d[-1]), reach + 0.01, bid)  # the thumb's tip on the handle

    def test_pommel_and_collar_show(self):
        for bid, body in BODIES.items():
            f = WG.grip_frame(body)
            along = []
            for name in ("f_index", "f_middle", "f_ring", "f_pinky"):
                _, a = WG.axis_distance(WG.gripped_chains(body)[name][1:], f)
                along.append(a)
            # The collar just past the index finger; the pommel just past the little finger.
            self.assertLess(float(along[0].max()), COLLAR[0] + 0.005, bid)
            self.assertGreater(float(along[0].max()), COLLAR[0] - 0.02, bid)
            self.assertGreater(float(along[3].min()), POMMEL_END, bid)
            self.assertLess(float(along[3].min()), POMMEL_END + 0.03, bid)


if __name__ == "__main__":
    unittest.main()
