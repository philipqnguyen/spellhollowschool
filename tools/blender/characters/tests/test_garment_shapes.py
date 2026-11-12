"""Garment shapes and designs (docs/art/garment-shapes.md): the data files are
consistent with the code, and every design composes from a built shape."""

import unittest

import numpy as np

from charlib import addons as AD
from charlib import content
from charlib import designs as DS
from charlib import gear as G
from charlib import shapes as SH

SHAPES = DS.shapes_data()["shapes"]
DATA = DS.designs_data()
DESIGNS = DATA["designs"]
SLOTS = {"hat", "body", "legs", "shoes"}
PATTERNS = {None, "stripes", "ribs", "check", "tartan", "cable"}


class ShapeDataTests(unittest.TestCase):
    def test_shape_list_is_small_and_per_slot(self):
        self.assertLessEqual(len(SHAPES), 15)
        by_slot = {}
        for name, s in SHAPES.items():
            self.assertIn(s["slot"], SLOTS, name)
            by_slot.setdefault(s["slot"], []).append(name)
        self.assertEqual(set(by_slot), SLOTS)

    def test_built_shapes_have_builders_and_budgets(self):
        for name, s in SHAPES.items():
            if s["status"] == "built":
                self.assertIn(s["builder"], SH.BUILDERS, name)
                self.assertGreater(s["budget"], 0, name)

    def test_option_defaults_are_allowed_values(self):
        for name, s in SHAPES.items():
            for opt, allowed in s.get("options", {}).items():
                if opt in s.get("params", {}):
                    self.assertIn(s["params"][opt], allowed, f"{name}.{opt}")


class DesignDataTests(unittest.TestCase):
    def test_every_trial_catalog_item_has_a_design(self):
        missing = [i["asset"] for i in content.clothing_items() if i["asset"] not in DESIGNS]
        self.assertEqual(missing, [])

    def test_designs_use_built_shapes_and_allowed_options(self):
        for key, d in DESIGNS.items():
            s = SHAPES[d["shape"]]
            self.assertEqual(s["status"], "built", key)
            self.assertEqual(key.split("/")[1], s["slot"], key)
            for opt, v in d.get("options", {}).items():
                self.assertIn(v, s["options"][opt], f"{key}: {opt}")

    def test_paint_zones_patterns_and_parts_exist(self):
        for key, d in DESIGNS.items():
            zones = SHAPES[d["shape"]].get("zones", {})
            for layer in d.get("paint", []) + d.get("thicken", []):
                if "zone" in layer:
                    self.assertIn(layer["zone"], zones, key)
                self.assertIn(layer.get("pattern"), PATTERNS, key)
            for t in d.get("trims", []):
                self.assertIn(t["type"], AD.TRIMS, key)
            for a in d.get("addons", []):
                self.assertIn(a["type"], AD.ADDONS, key)

    def test_paints_name_atlas_swatches_and_dyes(self):
        from charlib import atlas as A

        def check(spec, key):
            sw, dye = spec
            self.assertIn(sw, A.SWATCHES, key)
            self.assertIn(dye, AD.DYES, key)

        def walk(v, key):
            if isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v) and v[1] in AD.DYES:
                check(v, key)
            elif isinstance(v, list):
                for x in v:
                    walk(x, key)
            elif isinstance(v, dict):
                for x in v.values():
                    walk(x, key)

        for key, d in DESIGNS.items():
            walk(d, key)

    def test_families_group_designs_on_one_shape(self):
        for fam, f in DATA["families"].items():
            for key in f["designs"]:
                self.assertIn(key, DESIGNS, fam)
                self.assertEqual(DESIGNS[key]["shape"], f["shape"], f"{fam}: {key}")

    def test_new_designs_have_preview_dyes(self):
        # A design not in the catalog yet renders in its preview dyes; a
        # catalog item's design takes the item's dyes, so it carries none
        # (and no item id: the item is the one whose asset is its key).
        catalog = {i["asset"] for i in content.clothing_items()}
        for k, d in DESIGNS.items():
            self.assertNotIn("item_id", d, k)
            if k in catalog:
                self.assertNotIn("preview_dyes", d, k)
            else:
                self.assertIn("preview_dyes", d, k)



class HoodArcTests(unittest.TestCase):
    def test_back_arc_is_one_run_of_the_loop(self):
        # A neckline that bulges out over the shoulders (|x| >= 0.075) and
        # comes back inside the band at the side of the neck (y just > 0):
        # only the run at the centre back is the hood's arc, left to right.
        P = np.array([[0.0, -0.06, 0], [0.04, -0.05, 0], [0.065, 0.009, 0], [0.08, 0.02, 0],
                      [0.071, 0.088, 0], [0.04, 0.11, 0], [0.0, 0.12, 0], [-0.04, 0.11, 0],
                      [-0.071, 0.088, 0], [-0.08, 0.02, 0], [-0.065, 0.009, 0], [-0.04, -0.05, 0]])
        ids = [100 + i for i in range(len(P))]
        self.assertEqual(G._back_arc(ids, P), [108, 107, 106, 105, 104])
        # The loop's start inside the run, and the reverse winding, give the same arc.
        r = list(range(6, 12)) + list(range(0, 6))
        self.assertEqual(G._back_arc([ids[i] for i in r], P[r]), [108, 107, 106, 105, 104])
        self.assertEqual(G._back_arc(ids[::-1], P[::-1]), [108, 107, 106, 105, 104])


if __name__ == "__main__":
    unittest.main()
