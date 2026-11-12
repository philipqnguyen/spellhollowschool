"""Content coverage: every clothing item of the catalog can be built (from
its design in data/items/garment_designs.json, which the default
`--gear-source shapes` build uses, or from a builder in charlib/gear.py),
and the builder registry agrees with the long-robe budget list."""

import unittest

from charlib import content
from charlib import designs
from charlib import gear


def buildable() -> set[str]:
    return set(gear.BUILDERS) | set(designs.designs_data()["designs"])


class ContentTests(unittest.TestCase):
    def test_every_catalog_clothing_item_can_be_built(self):
        can = buildable()
        missing = [i["asset"] for i in content.clothing_items() if i["asset"] not in can]
        self.assertEqual(missing, [])

    def test_long_body_items_are_body_items_in_the_catalog(self):
        assets = {i["asset"]: i for i in content.clothing_items()}
        for asset in gear.LONG_BODY:
            self.assertIn(asset, assets)
            self.assertEqual(assets[asset]["slot"], "body", asset)

    def test_crafted_restyle_families_are_complete(self):
        # Every member of a Transfiguration family is built, so a restyle
        # never turns a garment into an item with no art.
        families: dict[str, list[str]] = {}
        for item in content.clothing_items():
            if item.get("family"):
                families.setdefault(item["family"], []).append(item["asset"])
        self.assertTrue(families)
        can = buildable()
        for fam, members in families.items():
            self.assertGreaterEqual(len(members), 2, fam)
            for asset in members:
                self.assertIn(asset, can, (fam, asset))


if __name__ == "__main__":
    unittest.main()
