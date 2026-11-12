"""Animated fire (docs/architecture.md "Rendering"): the art side of the
client's flames, tools/blender/castle_kit/fire.py, and the pieces and rooms
that burn listing their flames instead of modelling them. Pure Python; run
through tests/run_tests.py."""

import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import dorms  # noqa: E402
import furniture_models as fm  # noqa: E402
import kit_models as km  # noqa: E402
import kit_school  # noqa: E402,F401  (registers the classroom kit models)
import meshkit as mk  # noqa: E402
import school_rooms as sr  # noqa: E402
from castle_kit import fire as FI  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402

TOON = os.path.join(REPO, "data", "art", "toon_palette.json")


class FireDataTests(unittest.TestCase):
    def test_seed_is_deterministic_and_spread(self):
        self.assertEqual(FI.seed_of((1.0, 2.0, 3.0)), FI.seed_of((1.0000001, 2.0, 3.0)))
        seeds = {FI.seed_of((x * 0.7, 0.0, 3.0)) for x in range(50)}
        self.assertGreater(len(seeds), 45)
        self.assertTrue(all(0.0 <= s < 1.0 for s in seeds))

    def test_hearth_tongues_layered_back_to_front(self):
        t = FI.hearth_tongues(2.6, 1.6, depth=0.3, rows=2, count=10, seed=3)
        self.assertEqual(len(t), 10)
        self.assertEqual(t, FI.hearth_tongues(2.6, 1.6, depth=0.3, rows=2, count=10, seed=3))
        back, front = t[:5], t[5:]
        self.assertLess(max(q[2] for q in back), min(q[2] for q in front))  # drawn back to front
        self.assertGreater(max(q[3] for q in back), max(q[3] for q in front))  # the back row is taller
        for x, _, _, h, w in t:
            self.assertLessEqual(abs(x), 1.3 + 1e-9)
            self.assertTrue(0.3 < h <= 1.6 * 1.12 + 1e-9 and 0.1 < w < h)

    def test_placed_turns_tongues_with_the_piece(self):
        meta = {"fires": [FI.fire((0.0, 0.3, -0.36), [(0.5, 0.0, 0.0, 1.0, 0.3)], pool=2.0, embers=4)],
                "candles": [(0.0, 1.0, 0.2, 0.13)]}
        pl = geom.Placement((10.0, 5.0, 20.0), math.pi / 2)
        fires, candles = FI.placed(meta, pl.point)
        self.assertEqual(len(fires), 1)
        f = fires[0]
        self.assertEqual(f["at"], [round(v, 3) for v in pl.point((0.0, 0.3, -0.36))])
        d = f["tongues"][0]
        q = pl.point((0.5, 0.3, -0.36))
        self.assertAlmostEqual(f["at"][0] + d[0], q[0], places=3)
        self.assertAlmostEqual(f["at"][2] + d[2], q[2], places=3)
        self.assertEqual(f["floor"], 5.0)
        self.assertEqual(f["embers"], 4)
        self.assertEqual(candles, [[round(v, 3) for v in pl.point((0.0, 1.0, 0.2))] + [0.13]])

    def test_manifest_and_light_links(self):
        self.assertIsNone(FI.manifest([], [], {}))
        fires, _ = FI.placed({"fires": [FI.fire((0.0, 0.0, 0.0), [(0, 0, 0, 1.0, 0.4)])]}, lambda p: p)
        m = FI.manifest(fires, [[1, 2, 3, 0.1]], {"cell.castle_props.candles_0": [[0, 5, 0, 0.15]], "empty": []})
        self.assertEqual(sorted(m), ["candles", "fires", "groups"])
        self.assertEqual(list(m["groups"]), ["cell.castle_props.candles_0"])
        json.dumps(m)  # plain JSON
        lights = [{"pos": [0.0, 0.9, 0.4]}, {"pos": [9.0, 0.0, 0.0]}]
        self.assertEqual(FI.link_lights(lights, fires), 1)
        self.assertEqual(lights[0]["flicker"], fires[0]["seed"])
        self.assertNotIn("flicker", lights[1])

    def test_notes_on_a_mesh(self):
        m = mk.Mesh()
        self.assertIsNone(FI.noted(m))
        FI.note(m, candles=[(1.0, 2.0, 3.0, 0.1)])
        self.assertEqual(FI.noted(m), {"candles": [[1.0, 2.0, 3.0, 0.1]]})


class BurningPiecesTests(unittest.TestCase):
    """Every fire lists its flames and models none (no glow cones left)."""

    def test_fire_pieces_list_their_flames(self):
        for name, args, tongues in (("kit_fireplace_hooded_4.4x3.6", {}, 10), ("kit_hearth_kitchen", {"width": 4.0, "top": 6.0}, 8),
                                    ("kit_beast_brazier_a", {"drop": 2.5}, 5), ("kit_torch_wall", {"height": 2.8}, 3)):
            p = P.build(name, **args)
            fires = p.meta.get("fires")
            self.assertEqual(len(fires), 1, name)
            self.assertEqual(len(fires[0]["tongues"]), tongues, name)
        self.assertGreater(P.build("kit_fireplace_hooded_4.4x3.6").meta["fires"][0]["pool"], 1.5)

    def test_fireplace_has_no_glowing_floor(self):
        # A playtest report: a flat, uniformly bright yellow rectangle on the hearth. The glow on the
        # floor is the client's soft radial pool now; the bed under the logs is soot.
        p = P.build("kit_fireplace_hooded_4.4x3.6")
        glow = [f for f in p.faces if ":glow:" in f.tag]
        self.assertEqual(glow, [])

    def test_candle_pieces_list_their_flames(self):
        args = P.CATALOGUE["kit_candles_floating"]
        p = P.build("kit_candles_floating", **args)
        self.assertEqual(len(p.meta["candles"]), args["count"])
        self.assertEqual(sum(len(f.pts) - 2 for f in p.lod1), G.CANDLE_TRIS_LOD1 * args["count"])
        self.assertEqual(len(P.build("kit_chandelier_ring", radius=1.0, count=12, chain=3.0).meta["candles"]), 12)

    def test_kit_models_and_furniture_note_their_flames(self):
        for name in ("classroom_cauldron", "wall_torch"):
            self.assertTrue(FI.noted(km.build(name).mesh), name)
        items = {"furniture/lantern_candle_a": {"footprint": [0.24, 0.24], "height": 0.4},
                 "furniture/candelabra_brass_a": {"footprint": [0.3, 0.12], "height": 0.45},
                 "furniture/cauldron_pewter_a": {"footprint": [0.7, 0.7], "height": 0.8}}
        for asset, f in items.items():
            m = fm.MODELS[asset]({"asset": asset, "id": 0, "furniture": dict(f, surface="floor")})
            self.assertTrue(FI.noted(m), asset)

    def test_rooms_note_their_fires(self):
        for build in (dorms.house_a, dorms.house_b, dorms.house_c, dorms.house_d):
            shell = build()
            room = FI.noted(shell.mesh)
            self.assertTrue(room and room.get("fires"), shell.slot)
        arena = FI.noted(sr.build_all()["duel_arena"].mesh)
        self.assertGreaterEqual(len(arena["candles"]), 80)


class FirePaletteTests(unittest.TestCase):
    def test_fire_colours_bloom(self):
        with open(TOON, encoding="utf-8") as f:
            fire = json.load(f)["fire"]
        for k in ("core", "mid", "edge", "tip", "pool", "ember"):
            v = fire[k]
            self.assertRegex(v, r"^#[0-9A-F]{6}$")
            self.assertGreaterEqual(int(v[1:3], 16) / 255, 0.95, k)  # post.ts's bright pass haloes it
        self.assertLessEqual(fire["lightFlicker"], 0.15)  # subtle: no strobe


if __name__ == "__main__":
    unittest.main()
