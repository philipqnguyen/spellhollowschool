"""Unit tests for the inventory icons (tools/blender/icons): every catalog
item has an icon recipe, the icon models fit the atlas and the budgets, and,
once render_icons.py has run, every catalog id has a 128 x 128 RGBA icon in
client/public/assets/icons (those checks skip until then). Pure Python; run
through tests/run_tests.py."""

import json
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(TOOLS, "..", ".."))
sys.path[:0] = [os.path.join(TOOLS, "icons"), os.path.join(TOOLS, "world"), TOOLS]

import atlas  # noqa: E402
import furniture_models as fm  # noqa: E402
import icon_models  # noqa: E402

CATALOG = os.path.join(REPO, "data", "items", "catalog.json")
ICONS = os.environ.get("SHS_ICON_ASSETS", os.path.join(REPO, "client", "public", "assets", "icons"))
MANIFEST = os.path.join(ICONS, "manifest.json")


def catalog_items():
    with open(CATALOG, encoding="utf-8") as f:
        return [i for i in json.load(f)["items"] if not i.get("retired")]


def png_header(path: str):
    """(width, height, colour type) from a PNG's IHDR chunk."""
    with open(path, "rb") as f:
        head = f.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG")
    w, h = struct.unpack(">II", head[16:24])
    return w, h, head[25]


class IconModelTests(unittest.TestCase):
    def test_every_catalog_item_has_an_icon_recipe(self):
        for item in catalog_items():
            if item["kind"] == "clothing":
                continue  # rendered from the fitted character files (checked below)
            if item["kind"] == "furniture":
                self.assertIn(item["asset"], fm.MODELS, item["id"])
            else:
                self.assertIn(item["kind"], icon_models.KINDS, item["id"])
                icon_models.build(item)

    def test_icon_models_fit_the_atlas_and_budget(self):
        for item in catalog_items():
            if item["kind"] not in icon_models.KINDS:
                continue
            m = icon_models.build(item)
            self.assertGreater(m.triangle_count(), 0, item["id"])
            self.assertLessEqual(m.triangle_count(), 1500, item["id"])
            self.assertIn(1, m.zones() | {0}, item["id"])
            self.assertTrue(m.zones() <= {0, 1, 2, 3}, item["id"])
            for mat in m.materials():
                self.assertIn(atlas.ALIASES.get(mat, mat), atlas.TILES, (item["id"], mat))
            lo, hi = m.bounds()
            self.assertGreaterEqual(lo[1], -1e-6, item["id"])
            self.assertLess(max(hi[k] - lo[k] for k in range(3)), 0.6, item["id"])

    def test_potions_get_a_bottle_per_effect(self):
        effects = {i["potion"]["effect"] for i in catalog_items() if i["kind"] == "potion"}
        self.assertTrue(effects <= set(icon_models.POTION_SHAPES), effects - set(icon_models.POTION_SHAPES))
        shapes = [tuple(icon_models.POTION_SHAPES[e]) for e in effects]
        self.assertEqual(len(shapes), len(set(shapes)))


@unittest.skipUnless(os.path.exists(MANIFEST), f"no rendered icons in {ICONS}")
class IconFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(MANIFEST, encoding="utf-8") as f:
            cls.manifest = json.load(f)

    def test_every_catalog_id_has_an_icon(self):
        items = self.manifest["items"]
        for item in catalog_items():
            entry = items.get(str(item["id"]))
            self.assertIsNotNone(entry, f"no icon for {item['id']} {item['asset']}")
            self.assertEqual(entry["asset"], item["asset"])
            self.assertTrue(os.path.exists(os.path.join(ICONS, entry["file"])), entry["file"])

    def test_every_icon_key_is_listed_and_where_the_client_looks(self):
        assets = os.path.dirname(ICONS)
        listed = set(self.manifest["icons"])
        for item in catalog_items():
            key = item["asset"] if item["asset"].startswith("icons/") else f"icons/{item['asset']}"
            self.assertIn(key, listed, item["id"])
            self.assertTrue(os.path.exists(os.path.join(assets, f"{key}.png")), key)

    def test_icons_are_128_px_with_alpha(self):
        self.assertEqual(self.manifest["size"], 128)
        for entry in self.manifest["items"].values():
            w, h, colour_type = png_header(os.path.join(ICONS, entry["file"]))
            self.assertEqual((w, h), (128, 128), entry["file"])
            self.assertIn(colour_type, (4, 6), entry["file"])  # grey + alpha or RGBA


if __name__ == "__main__":
    unittest.main()
