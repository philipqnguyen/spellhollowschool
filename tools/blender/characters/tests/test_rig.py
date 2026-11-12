"""The Rigify rig and the exported skeleton (needs bpy and the Rigify add-on)."""

import unittest

import bpy

from charlib import rig as RG
from charlib.proportions import BODIES

FINGERS = ("f_index", "f_middle", "f_ring", "f_pinky")


def spec_bones() -> set[str]:
    """Bones listed in docs/art/base-body-spec.md section 3."""
    names = {"root"} | {f"DEF-spine{'' if i == 0 else f'.00{i}'}" for i in range(7)}
    names |= {"DEF-jaw", "DEF-eye.L", "DEF-eye.R"}
    for s in ("L", "R"):
        names |= {f"DEF-pelvis.{s}", f"DEF-breast.{s}", f"DEF-shoulder.{s}"}
        names |= {f"DEF-upper_arm.{s}", f"DEF-upper_arm.{s}.001", f"DEF-forearm.{s}", f"DEF-forearm.{s}.001", f"DEF-hand.{s}"}
        names |= {f"DEF-palm.0{i}.{s}" for i in range(1, 5)}
        names |= {f"DEF-thumb.0{i}.{s}" for i in range(1, 4)}
        names |= {f"DEF-{f}.0{i}.{s}" for f in FINGERS for i in range(1, 4)}
        names |= {f"DEF-thigh.{s}", f"DEF-thigh.{s}.001", f"DEF-shin.{s}", f"DEF-shin.{s}.001", f"DEF-foot.{s}", f"DEF-toe.{s}"}
    names |= set(RG.SOCKETS)
    return names


class RigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        RG.enable_rigify()
        cls.skels = {}
        for bid, body in BODIES.items():
            mr = RG.build_metarig(body, f"metarig_{bid}")
            rig = RG.generate(mr, f"rig_{bid}")
            cls.skels[bid] = RG.build_skeleton(rig, body, f"{bid}_skeleton")

    def test_exact_bone_set(self):
        expected = spec_bones()
        for bid, skel in self.skels.items():
            names = set(RG.exported_bone_names(skel))
            self.assertEqual(names, expected, bid)
            deform = [b for b in skel.data.bones if b.name.startswith("DEF-")]
            self.assertEqual(len(deform), 76, bid)

    def test_same_hierarchy_for_both_bodies(self):
        a = [(b.name, b.parent.name if b.parent else None) for b in self.skels["body_a"].data.bones]
        b = [(b.name, b.parent.name if b.parent else None) for b in self.skels["body_b"].data.bones]
        self.assertEqual(a, b)

    def test_parent_chain(self):
        bones = self.skels["body_a"].data.bones
        self.assertEqual(bones["DEF-spine"].parent.name, "root")
        self.assertEqual(bones["DEF-spine.006"].parent.name, "DEF-spine.005")
        self.assertEqual(bones["DEF-upper_arm.L"].parent.name, "DEF-shoulder.L")
        self.assertEqual(bones["DEF-forearm.L"].parent.name, "DEF-upper_arm.L.001")
        self.assertEqual(bones["DEF-thigh.R"].parent.name, "DEF-spine")
        self.assertEqual(bones["DEF-f_index.01.L"].parent.name, "DEF-palm.01.L")
        self.assertEqual(bones["socket_hand.R"].parent.name, "DEF-hand.R")
        self.assertEqual(bones["DEF-jaw"].parent.name, "DEF-spine.006")

    def test_root_on_floor(self):
        for skel in self.skels.values():
            self.assertLess(skel.data.bones["root"].head_local.length, 1e-6)


if __name__ == "__main__":
    unittest.main()
