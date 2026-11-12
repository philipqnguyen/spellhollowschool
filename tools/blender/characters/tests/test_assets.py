"""Exported character assets against the content data and the spec budgets.

Runs on the assets root given to run_tests.py (`-- --assets <dir>`, default
client/public/assets) and skips when nothing has been exported there yet.
"""

import json
import os
import unittest

from charlib import anim as AN
from charlib import content
from charlib import gltf_inspect as G
from charlib import pipeline as PL

ASSETS = os.environ.get("SHS_CHARACTER_ASSETS", os.path.join(content.REPO, "client", "public", "assets"))
MANIFEST = os.path.join(ASSETS, "characters", "manifest.json")
BODY_IDS = ("body_a", "body_b")
# LOD0 budgets of the garment shapes (data/items/garment_shapes.json `budget`) under the tier budgets
# of docs/art/character-adoption.md section 6 (PL.LOD_BUDGETS: LOD0-LOD3 per part).
BUDGETS = {"body": 16000, "hair": 8000, "hat": 2500, "body_slot": 6000, "long": 8000, "legs": 4000, "shoes": 1500}
LONG = {f"gear/body/robe_school_house_{h}" for h in "abcd"} | {"gear/body/cloak_travelling_a"} | {
    "gear/body/cloak_hooded_wool_a", "gear/body/greatcoat_double_a", "gear/body/robe_dress_embroidered_a",
    "gear/body/robe_formal_velvet_a"}
REQUIRED_ATTRS = {"POSITION", "NORMAL", "TEXCOORD_0", "COLOR_0", "JOINTS_0", "WEIGHTS_0"}
BASE_CLIPS = {"idle", "walk", "run", "wave", "bow", "sit", "sit_down", "floor_rest", "floor_sit",  # floor_sit
              "broom_ride", "broom_hover"}  # On a broom
# Adds the spell, duel and class clips (charlib/anim_spells.py).
CLIPS = {c.name for c in AN.CLIPS}


@unittest.skipUnless(os.path.exists(MANIFEST), f"no exported characters in {ASSETS}")
class AssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(MANIFEST, encoding="utf-8") as f:
            cls.manifest = json.load(f)
        cls.assets = cls.manifest["assets"]

    def files(self):
        for key, entry in self.assets.items():
            if entry["kind"] == "body":
                yield key, entry, entry["file"]
            else:
                for rel in list(entry.get("files", {}).values()) + list(entry.get("cap_files", {}).values()):
                    yield key, entry, rel

    def test_every_content_asset_exported_for_both_bodies(self):
        for body in BODY_IDS:
            self.assertIn(f"characters/{body}", self.assets)
        for item in content.clothing_items():
            entry = self.assets.get(item["asset"])
            self.assertIsNotNone(entry, item["asset"])
            self.assertEqual(sorted(entry["files"]), list(BODY_IDS), item["asset"])
            self.assertIn(item["id"], entry["item_ids"])
        for style in content.hair_styles():
            # Each hair style belongs to one body (creator.json v2): files for it only.
            entry = self.assets.get(style["asset"])
            self.assertIsNotNone(entry, style["asset"])
            self.assertEqual(sorted(entry["files"]), [style["body"]], style["asset"])
            # A hat variant only where the tails change under a hat (round 4
            # of 2026-11-07); every style carries its hat fits.
            self.assertIn(sorted(entry.get("cap_files", {})), ([], [style["body"]]), style["asset"])
            self.assertEqual(sorted(entry["hat_fit"]), [style["body"]], style["asset"])
            self.assertEqual(entry["body"], style["body"])
        shipped = {s["asset"] for s in content.hair_styles()}
        for key, entry in self.assets.items():
            if entry["kind"] == "hair":
                self.assertIn(key, shipped, "retired hair styles are not exported")

    def test_file_paths_follow_architecture(self):
        for key, entry, rel in self.files():
            self.assertTrue(os.path.exists(os.path.join(ASSETS, rel)), rel)
            self.assertTrue(rel.startswith(key), rel)

    def test_one_skeleton_everywhere(self):
        bones = self.manifest["skeleton"]["bones"]
        self.assertEqual(len([b for b in bones if b.startswith("DEF-")]), 76)
        for key, entry, rel in self.files():
            joints = G.joint_names(os.path.join(ASSETS, rel))
            if entry["kind"] == "hair":
                # Hair files add their sway joints (hair.*, children of the head).
                extra = [j for j in joints if j.startswith("hair.")]
                self.assertLessEqual(len(extra), 12, rel)
                self.assertEqual(sorted(x.split(".")[1] for x in extra if x.endswith(".01")),
                                 sorted(c.split(".")[0] for c in entry.get("sway", [])), rel)
                joints = [j for j in joints if not j.startswith("hair.")]
            self.assertEqual(joints, bones, rel)

    def test_budgets_and_lods(self):
        for key, entry in self.assets.items():
            if entry["kind"] == "body":
                tri_sets = {"": entry["triangles"]}
                budget = BUDGETS["body"]
            else:
                tri_sets = dict(entry["triangles"])
                tri_sets.update({f"{b} cap": t for b, t in entry.get("cap_triangles", {}).items()})
                if entry["kind"] == "hair":
                    # Per length class and LOD (character-adoption.md section 4).
                    for label, t in tri_sets.items():
                        b = entry["budgets"]
                        for lod in ("lod0", "lod1", "lod2", "lod3"):
                            self.assertLessEqual(t[lod], b[lod], f"{key} {label} {lod}")
                        self.assertTrue(t["lod0"] > t["lod1"] > t["lod2"] > t["lod3"], f"{key} {label}")
                    continue
                elif entry["slot"] == "body":
                    budget = BUDGETS["long" if key in LONG else "body_slot"]
                else:
                    budget = BUDGETS[entry["slot"]]
            tiers = PL.LOD_BUDGETS["body" if entry["kind"] == "body" else ("hair" if entry["kind"] == "hair" else PL.SLOT_PART[entry["slot"]])]
            for label, t in tri_sets.items():
                self.assertLessEqual(t["lod0"], budget, f"{key} {label}")
                self.assertLess(t["lod1"], t["lod0"] * 0.6, f"{key} {label}")
                if not self.adopted(key, entry):
                    continue
                for lvl in range(4):
                    self.assertIn(f"lod{lvl}", t, f"{key} {label}")
                    self.assertLessEqual(t[f"lod{lvl}"], tiers[lvl], f"{key} {label} lod{lvl}")

    def adopted(self, key, entry) -> bool:
        """Assets in the adoption format (LOD0-LOD3, _SHADE): bodies, clothing and
        the shipped hair styles (the retired styles' old files may linger)."""
        if entry["kind"] != "hair":
            return True
        return key in {s["asset"] for s in content.hair_styles()}

    def test_meshes_are_skinned_and_masked(self):
        for key, entry, rel in self.files():
            s = G.summary(os.path.join(ASSETS, rel))
            self.assertEqual(len(s["skins"]), 1, rel)
            self.assertEqual(s["materials"], ["character"], rel)
            lods = set()
            for m in s["meshes"]:
                self.assertTrue(REQUIRED_ATTRS <= set(m["attributes"]), f"{rel} {m['node']}")
                self.assertEqual(m["primitives"], 1)
                lods.add(m["extras"]["lod"])
                if entry["kind"] == "clothing":
                    self.assertTrue(set(m["extras"]["hides"]) <= set(self.manifest["regions"]))
                if self.adopted(key, entry):
                    self.assertIn("_SHADE", m["attributes"], f"{rel} {m['node']}: the shade layer")
                if entry["kind"] == "hair":
                    self.assertEqual(m["extras"].get("toon"), "hair", rel)
                    if m["extras"]["lod"] == 0:
                        hem = m["extras"]["fringe_hem"]
                        from charlib import hair_meshy as HM

                        if HM.STYLES.get(key.split("/")[-1], {}).get("no_fringe"):
                            # Combed back or cropped: one placeholder point,
                            # which the client reads as no fringe shadow.
                            self.assertEqual(len(hem), 1, rel)
                        else:
                            self.assertTrue(8 <= len(hem) <= 24, rel)
                        self.assertEqual(set(m["extras"]["fringe_shadow"]), {"depth_m", "soft_m", "strength"}, rel)
            self.assertEqual(lods, {0, 1, 2, 3} if self.adopted(key, entry) else {0, 1}, rel)

    def test_bodies_have_all_clips_and_regions(self):
        for body in BODY_IDS:
            rel = self.assets[f"characters/{body}"]["file"]
            s = G.summary(os.path.join(ASSETS, rel))
            self.assertEqual(set(s["animations"]), CLIPS)
            self.assertTrue(BASE_CLIPS < CLIPS)
            regions = {m["extras"]["region"] for m in s["meshes"]}
            self.assertEqual(regions, set(self.manifest["regions"]))
        self.assertEqual(set(self.manifest["animations"]), CLIPS)

    def test_no_twist_bone_flips(self):
        """No clip twists a limb's twist bone past AN.TWIST_FLIP_DEG about its own
        axis (the wrapped female's shins flipped by half a turn mid-stride:
        the trouser legs wrung into knots and the hems stretched to the
        other foot)."""
        import math
        import struct

        names = {p.format(s=s) for p in AN.TWIST_BONES for s in "LR"}
        for body in BODY_IDS:
            path = os.path.join(ASSETS, self.assets[f"characters/{body}"]["file"])
            with open(path, "rb") as fh:
                data = fh.read()
            jlen = struct.unpack_from("<I", data, 12)[0]
            j = json.loads(data[20:20 + jlen])
            binary = data[20 + jlen + 8:]

            def floats(acc_index):
                a = j["accessors"][acc_index]
                bv = j["bufferViews"][a["bufferView"]]
                self.assertEqual(a["componentType"], 5126)
                off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
                return struct.unpack_from(f"<{a['count'] * 4}f", binary, off)

            worst = []
            for anim in j["animations"]:
                for ch in anim["channels"]:
                    node = j["nodes"][ch["target"]["node"]]
                    if node.get("name") not in names or ch["target"]["path"] != "rotation":
                        continue
                    rx, ry, rz, rw = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
                    q = floats(anim["samplers"][ch["sampler"]]["output"])
                    for k in range(0, len(q), 4):
                        x, y, z, w = q[k:k + 4]
                        # Twist about the bone's own Y axis, relative to its rest.
                        dy = rw * y - ry * w - rz * x + rx * z
                        dw = rw * w + rx * x + ry * y + rz * z
                        t = abs(math.degrees(2.0 * math.atan2(abs(dy), abs(dw))))
                        if t > AN.TWIST_FLIP_DEG + 1.0:
                            worst.append((anim["name"], node["name"], round(t)))
            self.assertEqual(worst[:8], [], body)

    def test_no_build_keys(self):
        """Every body is the neutral build (character adoption): no file carries a build key."""
        self.assertNotIn("build", self.manifest["shape_keys"])
        for key, entry, rel in self.files():
            if not self.adopted(key, entry):
                continue
            s = G.summary(os.path.join(ASSETS, rel))
            names = set().union(*[set(m["target_names"]) for m in s["meshes"]])
            self.assertFalse({n for n in names if n.startswith(("build_", "height_"))}, rel)

    def test_every_catalog_item_is_a_design(self):
        from charlib import designs

        d = designs.designs_data()["designs"]
        missing = [i["asset"] for i in content.clothing_items() if i["asset"] not in d]
        self.assertEqual(missing, [])

    def test_height_follows_creator(self):
        cr = content.creator()
        for bt in cr["body_types"]:
            h = self.manifest["height"]["bodies"][bt["id"]]
            self.assertAlmostEqual(h["default_height_m"], bt["default_height_m"], places=3)
            self.assertEqual([round(x, 3) for x in h["height_m"]], [round(x, 3) for x in bt["height_m"]])

    def test_iris_takes_the_eye_colour(self):
        self.assertEqual(self.manifest["tint"]["body"], ["skin_tone", "hair_colour", "eye_colour"])


if __name__ == "__main__":
    unittest.main()
