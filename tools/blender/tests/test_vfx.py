"""Unit tests for the spell effects and the wand (tools/blender/vfx/
vfx_models.py): every spell of data/spells.json gets the effects its kind
needs, small and cheap (one material, atlas UVs inside their tile, one
colour per face), and the wand matches the numbers the character clips
use. Pure Python and numpy; run through tests/run_tests.py."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), "vfx"), os.path.join(os.path.dirname(HERE), "world")]

import numpy as np  # noqa: E402
import vfx_models as vm  # noqa: E402

SPELLS = os.path.join(REPO, "data", "spells.json")
ANIM_SPELLS = os.path.join(os.path.dirname(HERE), "characters", "charlib", "anim_spells.py")


class EffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SPELLS, encoding="utf-8") as f:
            cls.spells = json.load(f)["spells"]
        cls.meshes, cls.table = vm.effects(cls.spells)

    def test_roles_per_spell_kind(self):
        for sp in self.spells:
            entry = self.table[str(sp["id"])]
            self.assertEqual(entry["colour"], sp["colour"])
            effect = (sp.get("duel") or {}).get("effect", {}).get("kind")
            if effect == "projectile":
                for role in ("core", "trail", "impact", "charge"):
                    self.assertIn(role, entry, (sp["key"], role))
                self.assertEqual(entry["shape"], vm.SHAPES.get(sp["gesture"], "spike"))
            if effect == "shield":
                for role in ("shield", "flash", "charge"):
                    self.assertIn(role, entry, (sp["key"], role))
            if sp["kind"] == "disarm":
                self.assertIn("disarm", entry)
            social = sp["social"]["kind"]
            if social == "light":
                self.assertIn("glow", entry)
            if social == "levitate":
                self.assertIn("sparkle", entry)
        # Node names carry ids only (no spell names).
        for name in self.meshes:
            self.assertRegex(name, r"^spell_\d+_[a-z]+$")

    def test_cheap_meshes_one_colour_per_face(self):
        for name, fx in self.meshes.items():
            self.assertGreater(fx.triangle_count(), 0, name)
            self.assertLessEqual(fx.triangle_count(), vm.BUDGET, name)
            self.assertEqual(len(fx.colours), len(fx.faces), name)
            self.assertEqual(set(fx.materials()), {"fx"}, name)
            for c in fx.colours:
                self.assertTrue(all(0.0 <= v <= 1.0 for v in c), name)

    def test_uvs_stay_inside_one_tile(self):
        rects = [vm.tile_rect(t) for t in vm.ATLAS_TILES]
        for name, fx in self.meshes.items():
            for f in fx.faces:
                inside = [r for r in rects if all(r[0] - 1e-6 <= u <= r[2] + 1e-6 and r[1] - 1e-6 <= v <= r[3] + 1e-6
                                                  for u, v in f.uvs)]
                self.assertEqual(len(inside), 1, name)

    def test_projectiles_travel_along_plus_z(self):
        for sid, entry in self.table.items():
            if "trail" in entry:
                lo, hi = self.meshes[entry["trail"]].bounds()
                self.assertLess(lo[2], -1.0, sid)
                self.assertLessEqual(hi[2], 1e-6, sid)
            if "shield" in entry:
                lo, hi = self.meshes[entry["shield"]].bounds()
                self.assertAlmostEqual(hi[2], 0.55, places=3)
                self.assertGreater(hi[0] - lo[0], 1.0)

    def test_atlas_tiles(self):
        img = vm.paint_atlas()
        self.assertEqual(img.shape, (vm.ATLAS_SIZE, vm.ATLAS_SIZE, 4))
        self.assertLessEqual(len(vm.ATLAS_TILES), vm.ATLAS_GRID ** 2)
        n = vm.ATLAS_SIZE // vm.ATLAS_GRID
        for i, name in enumerate(vm.ATLAS_TILES):
            x, y = (i % vm.ATLAS_GRID) * n, (i // vm.ATLAS_GRID) * n
            alpha = img[y : y + n, x : x + n, 3]
            self.assertGreater(alpha.max(), 0.5, name)
            # Soft shapes fade out at the tile border (no hard square edges).
            border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
            if name not in ("solid", "streak", "spike", "wisp"):
                self.assertLess(border.max(), 0.2, name)


class WandTests(unittest.TestCase):
    def test_wand_matches_the_clips(self):
        lo, hi = vm.wand_mesh().bounds()
        self.assertAlmostEqual(hi[1], vm.WAND_TIP, places=4)
        self.assertLess(lo[1], -vm.WAND_BUTT + 1e-6)
        self.assertLess(max(abs(lo[0]), abs(hi[0]), abs(lo[2]), abs(hi[2])), 0.02)
        # The character clips solve the arm for the same grip-to-tip length.
        with open(ANIM_SPELLS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn(f"WAND_TIP = {vm.WAND_TIP}", src)
        self.assertIn(f"WAND_BUTT = {vm.WAND_BUTT}", src)

    def test_wand_size_and_tip(self):
        # About 30-35 cm overall, slim, the tip on the shaft's axis at WAND_TIP above the grip.
        m = vm.wand_mesh()
        lo, hi = m.bounds()
        self.assertGreaterEqual(hi[1] - lo[1], 0.30)
        self.assertLessEqual(hi[1] - lo[1], 0.355)
        self.assertLess(max(hi[0] - lo[0], hi[2] - lo[2]), 0.03)
        top = [v for v in m.verts if abs(v[1] - hi[1]) < 1e-9]
        self.assertTrue(top)
        for v in top:
            self.assertAlmostEqual(v[0], 0.0, places=6)
            self.assertAlmostEqual(v[2], 0.0, places=6)
        # The grip (origin) is on the handle, which is thicker than the shaft and tapers toward the tip.
        parts = vm.wand_profile()
        ys = [y for prof, _ in parts for _, y in prof]
        self.assertEqual(ys, sorted(ys))
        handle = max(r for prof, sw in parts if sw == 0 for r, _ in prof)
        shaft = [r for prof, sw in parts if sw == 1 for r, _ in prof]
        self.assertGreater(handle, max(shaft))
        self.assertEqual(shaft, sorted(shaft, reverse=True))
        grip = [y for prof, sw in parts if sw == 0 for _, y in prof]
        self.assertLess(min(grip), 0.0)
        self.assertGreater(max(grip), 0.0)

    def test_wand_poly_budget(self):
        # A small prop (docs/art/style-guide.md): one mesh, one material, within budget.
        m = vm.wand_mesh()
        self.assertLessEqual(m.triangle_count(), vm.WAND_BUDGET)
        self.assertGreater(m.triangle_count(), 50)
        self.assertEqual(set(m.materials()), {"wand"})

    def test_wand_texture_swatches(self):
        img = vm.wand_texture()
        self.assertEqual(img.shape[2], 4)
        for f in vm.wand_mesh().faces:
            u, v = f.uvs[0]
            self.assertTrue(0 < u < 1 and 0 < v < 1)


if __name__ == "__main__":
    unittest.main()
