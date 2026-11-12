"""The clothing pass (docs/architecture.md):
garments sit close, hide what a higher layer covers, keep
inner layers inside outer ones, and drape when seated.

Data checks always run; the measured checks use the exported gear (the
assets root) on the saved body blends (art/characters/<body>.blend, whose
skeletons carry the baked clips) and skip without them."""

import json
import os
import unittest

import bpy
import numpy as np

import check_garment_layers as CL
from charlib import content
from charlib import layers as LY

ASSETS = os.environ.get("SHS_CHARACTER_ASSETS", os.path.join(content.REPO, "client", "public", "assets"))
BLENDS = os.path.join(content.REPO, "art", "characters")
SHAPES = json.load(open(os.path.join(content.REPO, "data", "items", "garment_shapes.json")))["shapes"]


def _glb(asset: str, body: str) -> str:
    return os.path.join(ASSETS, f"{asset}_{body}.glb")


def _offset_at(table, name):
    return dict(table)[name] if not isinstance(table, (int, float)) else table


class LayerDataTests(unittest.TestCase):
    def test_layers_close_to_the_body_and_nested(self):
        """Inner tops a few mm, knits a little more, robes about a centimetre;
        every body item sits outside the legs items at the waist and hips."""
        top = SHAPES["top_fitted"]["params"]["offset"]["torso"]
        knit = SHAPES["top_knit"]["params"]["offset"]["torso"]
        coat = SHAPES["coat_long"]["params"]["offset"]["torso"]
        self.assertLessEqual(_offset_at(top, "chest"), 0.006)
        self.assertLessEqual(_offset_at(knit, "chest"), 0.009)
        self.assertLessEqual(_offset_at(coat, "chest"), 0.012)
        for lm in ("chest", "waist", "hip"):
            self.assertLess(_offset_at(top, lm), _offset_at(knit, lm))
            self.assertLess(_offset_at(knit, lm), _offset_at(coat, lm))
        legs_waist = max(_offset_at(SHAPES[s]["params"]["offset"]["hips"], "waist") for s in ("trousers_fitted", "trousers_wide"))
        yoke_waist = max(_offset_at(SHAPES[s]["params"]["yoke"]["offset"], "waist") for s in ("skirt_short", "skirt_long"))
        for t in (top, knit, coat):
            self.assertGreaterEqual(_offset_at(t, "waist") - max(legs_waist, yoke_waist), 0.005)
        # Inserts (a shirt front under a V or an open robe) inside their shell,
        # and the robe's jumper front outside a skirt's or trousers' waistband.
        jumper, shirt = (s["offset"] for s in SHAPES["coat_long"]["params"]["insert_specs"][:2])
        for lm in ("chest", "waist", "hip"):
            self.assertLess(_offset_at(jumper, lm), _offset_at(coat, lm))
        self.assertLess(_offset_at(shirt, "chest"), _offset_at(jumper, "chest"))
        self.assertGreater(_offset_at(jumper, "waist"), max(legs_waist, yoke_waist))

    def test_boot_shaft_between_tights_and_trousers(self):
        """A boot shaft clears a skirt's tights and the trouser legs cover it
        (the riding boot broke through the trousers behind the knee
        and the tights broke through its top, 2026-11-11)."""
        boot = SHAPES["boot"]["params"]
        tights = max(_offset_at(SHAPES[s]["params"]["offset"]["leg"], "leg") for s in ("skirt_short", "skirt_long"))
        shafts = [boot["shaft"]] + [v["shaft"] for v in boot["variants"]["height"].values() if "shaft" in v and "offset" in v["shaft"]]
        flare = boot.get("collar_flare", 0.004)
        for shaft in shafts:
            lo = min(b for _, b in shaft["offset"])
            hi = max(b for _, b in shaft["offset"])
            self.assertGreaterEqual(lo - tights, 0.004, shaft)
            for s in ("trousers_fitted", "trousers_wide"):
                # The room is the cover that hides the shaft and its rolled
                # top (the client's rule, less one cover step), not a
                # clearance over it.
                room = max(b for _, b in SHAPES[s]["params"]["boot_room"])
                hides = (room - LY.STEP_M) * LY.TOLERANCE_SCALE + LY.TOLERANCE_M
                self.assertGreaterEqual(hides, hi + flare, (s, shaft))
        # Paint bands up the shaft by height fraction, not by ring count.
        for z in ("strap", "shaft_band"):
            self.assertIn("t", SHAPES["boot"]["zones"][z])

    def test_cover_bytes_round_trip(self):
        h = np.array([0.0, 0.001, 0.0123, 0.6])
        back = LY.decode(LY.encode(h))
        self.assertEqual(back[0], 0.0)
        self.assertGreater(back[1], 0.0)  # any cover counts
        self.assertAlmostEqual(back[2], 0.0123, delta=LY.STEP_M)
        self.assertAlmostEqual(back[3], 255 * LY.STEP_M)

    def test_hidden_mask_rule(self):
        cover = np.array([0.02, 0.02, 0.0, 0.003])
        faces = [(0, 1, 0), (0, 1, 2), (3, 3, 3)]
        mask = LY.hidden_mask(cover, np.array([0, 1, 2, 3.0]), np.array([0.01, 0.024, 0.0, 0.009]), faces)
        # Under the cover (within its tolerance); not when a vertex is over an uncovered body vertex;
        # a boot shaft 9 mm out stays over 3 mm tights.
        self.assertEqual(mask.tolist(), [True, False, False])


# (outer, inner, body, limits: clip -> worst frame's vertices through;
# main before the pass: 4, 11, 44 and 14, 12, 55; after: 7, 10, 33 and 3, 0, 30;
# 2026-11-05, covers from under the skin and 5 mm more at the hip: 6, 9, 19 and 2, 1, 26),
# back limits: clip -> worst frame's vertices through the back above the outer's hem
# (`back_worst_verts`; the pleated skirt's yoke as patches through an untucked
# top's back in the run: blouse and oxford shirt 2, 1, 15 -> 1, 0, 3 and 1, 0, 1)
CASES = [
    ("gear/body/shirt_oxford_tie_a", "gear/legs/skirt_pleated_a", "body_b", {"idle": 10, "walk": 12, "run": 26},
     {"idle": 2, "walk": 1, "run": 5}),
    ("gear/body/blouse_ruffled_silk_a", "gear/legs/skirt_pleated_a", "body_b", {"idle": 10, "walk": 8, "run": 24},
     {"idle": 2, "walk": 1, "run": 5}),
    ("gear/body/robe_school_house_a", "gear/legs/trousers_school_a", "body_a", {"idle": 6, "walk": 6, "run": 40}, {}),
]


class LayerMeasureTests(unittest.TestCase):
    def test_inner_layers_stay_inside(self):
        ran = 0
        for outer, inner, body, limits, back in CASES:
            blend = os.path.join(BLENDS, f"{body}.blend")
            files = [_glb(outer, body), _glb(inner, body)]
            if not os.path.exists(blend) or not all(os.path.exists(f) for f in files):
                continue
            bpy.ops.wm.open_mainfile(filepath=blend)
            stats = CL.measure(files[0], files[1], clips={c: CL.CLIPS[c] for c in limits})
            for clip, worst in limits.items():
                with self.subTest(outer=outer, inner=inner, clip=clip):
                    self.assertLessEqual(stats["clips"][clip]["worst_frame_verts"], worst, stats["clips"][clip])
            for clip, worst in back.items():
                with self.subTest(outer=outer, inner=inner, clip=clip, part="back"):
                    self.assertLessEqual(stats["clips"][clip]["back_worst_verts"], worst, stats["clips"][clip])
            ran += 1
        if not ran:
            self.skipTest("no exported gear or body blends")

    def test_skirts_and_robes_drape_when_seated(self):
        """The drape keys exist on every LOD and keep the fabric near the legs
        in the chair and floor sits (it ballooned 20 to 30 cm off them)."""
        ran = 0
        for asset, body, limit in (("gear/legs/skirt_pleated_a", "body_b", 0.12), ("gear/body/greatcoat_double_a", "body_a", 0.16)):
            blend = os.path.join(BLENDS, f"{body}.blend")
            path = _glb(asset, body)
            if not os.path.exists(blend) or not os.path.exists(path):
                continue
            bpy.ops.wm.open_mainfile(filepath=blend)
            stats = CL.measure(path, None, drape=True, clips={"sit": 15, "floor_sit": 45})
            for clip in ("sit", "floor_sit"):
                with self.subTest(asset=asset, clip=clip):
                    self.assertLessEqual(stats["clips"][clip]["stand_off_p95_m"], limit, stats["clips"][clip])
            keyed = [o for o in bpy.data.objects if o.type == "MESH" and o.data.shape_keys]
            names = {kb.name for o in keyed for kb in o.data.shape_keys.key_blocks}
            self.assertTrue({"drape_chair", "drape_floor", "drape_rest"} <= names, names)
            ran += 1
        if not ran:
            self.skipTest("no exported gear or body blends")
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    unittest.main()
