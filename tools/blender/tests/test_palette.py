"""Unit tests for the anime-look art data: the castle palette and its
painted value ramps (tools/blender/world/palette.py), the lighting data
(data/art/lighting.json) and the per-material toon ramps
(data/art/toon_palette.json). Pure Python; run through tests/run_tests.py."""

import ast
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), "world"), os.path.dirname(HERE)]

import dorms  # noqa: E402
import furniture_models as fm  # noqa: E402
import kit  # noqa: E402
import kit_school  # noqa: E402,F401  (registers the classroom kit models)
import layout_lib as L  # noqa: E402
import palette  # noqa: E402
import school_rooms as sr  # noqa: E402

LIGHTING = os.path.join(REPO, "data", "art", "lighting.json")
TOON = os.path.join(REPO, "data", "art", "toon_palette.json")
FOLIAGE_ROWS = ("castle_foliage",)  # vegetation look A: the one row allowed a wider step
FOLIAGE_SOFTNESS_CAP = 0.15
PROPS = os.path.join(REPO, "data", "world", "props.json")
HEX = re.compile(r"^#[0-9A-F]{6}$")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def toon_entry(toon: dict, name: str):
    """The client's lookup (toon/palette.ts `worldRamp`): exact name, then
    the longest key ending in `*` that prefixes it."""
    mats = toon["materials"]
    if name in mats:
        return mats[name]
    keys = [k for k in mats if k.endswith("*") and name.startswith(k[:-1])]
    return mats[max(keys, key=len)] if keys else None


class PaletteTests(unittest.TestCase):
    def test_castle_colours_stay_in_the_albedo_range(self):
        # Style guide: no pure black or white surfaces (#383838 .. #ECECEC).
        lo, hi = palette.luminance("#383838"), palette.luminance("#ECECEC")
        for name, h in palette.CASTLE.items():
            self.assertTrue(HEX.match(h), name)
            self.assertTrue(lo <= palette.luminance(h) <= hi, (name, h))

    def test_stone_is_warm_and_never_grey(self):
        for name in ("stone", "stone_exterior", "stone_trim", "flagstone", "flagstone_corridor"):
            r, g, b = palette.rgb(palette.CASTLE[name])
            self.assertGreater(r, b, name)
            self.assertGreater(palette.saturation(palette.CASTLE[name]), 0.12, name)

    def test_house_colours_stay_recognisable(self):
        hue = lambda h: __import__("colorsys").rgb_to_hsv(*palette.rgb(h))[0] * 360  # noqa: E731
        self.assertTrue(hue(palette.CASTLE["house_a"]) < 15 or hue(palette.CASTLE["house_a"]) > 345)  # red
        self.assertTrue(35 < hue(palette.CASTLE["house_b"]) < 55)  # yellow
        self.assertTrue(205 < hue(palette.CASTLE["house_c"]) < 235)  # blue
        self.assertTrue(130 < hue(palette.CASTLE["house_d"]) < 160)  # green
        for slot in "abcd":
            self.assertGreater(palette.saturation(palette.CASTLE[f"house_{slot}"]), 0.6, slot)

    def test_greybox_materials_come_from_the_palette(self):
        self.assertEqual(L.MATERIALS["wall"][0], palette.linear(palette.CASTLE["stone"]))
        self.assertEqual(L.MATERIALS["floor_grounds"][0], palette.linear(palette.CASTLE["grass"]))
        self.assertEqual(L.MATERIALS["fabric_house_c"][0], palette.linear(palette.CASTLE["house_c"]))

    def test_grade_lifts_darks_and_saturates_gently(self):
        self.assertGreater(palette.luminance(palette.grade("#101010")), palette.luminance("#101010"))
        self.assertGreater(palette.saturation(palette.grade("#7A5434")), palette.saturation("#7A5434"))
        self.assertLessEqual(palette.saturation(palette.grade("#7F0909")), 0.95)  # no pure primaries
        r, g, b = palette.rgb(palette.grade("#808080"))
        self.assertGreater(r, b)  # a dead grey turns warm
        self.assertEqual(palette.grade("#6B4A2E"), palette.grade("#6B4A2E"))

    def test_value_ramps_darken_wall_bases(self):
        bottom = palette.ramp_factor("wall", 0.0, 0.0, 15.0)
        top = palette.ramp_factor("wall", 15.0, 0.0, 15.0)
        self.assertEqual(top, (1.0, 1.0, 1.0))
        self.assertTrue(all(b < t for b, t in zip(bottom, top)))
        self.assertIsNone(palette.ramp_factor("floor_room", 0.0, 0.0, 1.0))
        for name, (mode, lo, hi, _) in palette.VALUE_RAMPS.items():
            self.assertIn(name, L.MATERIALS)
            self.assertIn(mode, ("cell", "world"))
            self.assertTrue(all(0.5 <= v <= 1.0 for v in lo + hi), name)

    def test_castle_ramps_are_grey(self):
        # Castle D1 (docs/art/castle-kit.md "Shading: the AO bake"): COLOR_0 carries value only;
        # each ramp's bottom is its old bottom's Rec. 709 luminance; floor_grounds
        # keeps its hue ramp (the one documented exception).
        want = {"wall": 0.70, "wall_dungeon": 0.76, "wall_exterior": 0.67, "trim": 0.86, "wall_low": 0.82}
        for name, (_, lo, hi, _) in palette.VALUE_RAMPS.items():
            if name == "floor_grounds":
                self.assertNotEqual(len(set(lo)), 1)
                continue
            self.assertEqual(lo, (want[name],) * 3, name)
            self.assertEqual(hi, (1.0, 1.0, 1.0), name)
            for y in (0.0, 3.0, 7.5):
                r, g, b = palette.ramp_factor(name, y, 0.0, 7.5)
                self.assertTrue(r == g == b, name)
        self.assertEqual(set(want) | {"floor_grounds"}, set(palette.VALUE_RAMPS))

    def test_world_ramps_are_continuous_across_cells(self):
        # The grounds slope over several cells: the same height gets the same
        # colour whatever the cell's own height range.
        a = palette.ramp_factor("floor_grounds", -9.0, -9.0, -4.0)
        b = palette.ramp_factor("floor_grounds", -9.0, -14.0, -9.0)
        self.assertEqual(a, b)

    def test_grounds_stay_grass_down_to_the_lake(self):
        layout = L.load_layout(os.path.join(REPO, "data", "world", "layout.json"))
        low = [s for s in layout.surfaces if s.kind == "grounds" and s.floor_max < L.DUNGEON_BELOW]
        self.assertTrue(low)
        for s in low:
            self.assertEqual(L.floor_material(s), "floor_grounds", s.id)


class LightingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load(LIGHTING)

    def test_castle_keys_cover_the_day(self):
        keys = self.data["castle"]["keys"]
        hours = [k["hour"] for k in keys]
        self.assertEqual(hours, sorted(hours))
        self.assertTrue(all(0 <= h < 24 for h in hours))
        self.assertGreaterEqual(len(keys), 4)
        for k in keys:
            sun = k["sun"]
            self.assertIn(sun["body"], ("sun", "moon"))
            d = sun["direction"]
            self.assertAlmostEqual(sum(c * c for c in d), 1.0, delta=0.02, msg=k["name"])
            self.assertLess(d[1], 0, k["name"])  # always shining down
            self.assertTrue(0 < sun["intensity"] <= 1.5)
            self.assertTrue(0 < k["hemi"]["intensity"] <= 1.5)
            for h in (sun["color"], k["hemi"]["sky"], k["hemi"]["ground"], k["sky"]["zenith"], k["sky"]["horizon"],
                      k["fog"]):
                self.assertTrue(HEX.match(h), (k["name"], h))

    def test_night_reads_by_hue_not_black(self):
        night = next(k for k in self.data["castle"]["keys"] if k["name"] == "night")
        r, g, b = palette.rgb(night["sun"]["color"])
        self.assertGreater(b, r)  # moonlight is blue
        self.assertGreater(palette.luminance(night["hemi"]["sky"]), 0.05)

    def test_every_world_light_uses_the_light_palette(self):
        allowed = {h.upper() for h in self.data["lights"].values()}
        for h in allowed:
            self.assertTrue(HEX.match(h), h)
        used = {}
        for lt in L.load_lights(PROPS):
            used[f"props.json {lt.id}"] = lt.color
        cells, _ = kit.assemble(kit.load_kit(PROPS))
        for cell, ck in cells.items():
            for lt in ck.lights:
                used[f"kit {lt['id']}"] = lt["color"]
        for slot, shell in dorms.build_all().items():
            for lt in shell.lights:
                used[f"dorm {slot} {lt['id']}"] = lt["color"]
        for room in sr.build_all().values():
            for lt in room.lights:
                used[f"{room.cell} {lt['id']}"] = lt["color"]
        for item, lights in fm.ITEM_LIGHTS.items():
            for i, lt in enumerate(lights):
                used[f"{item} {i}"] = lt["color"]
        bad = {k: v for k, v in used.items() if v.upper() not in allowed}
        self.assertEqual(bad, {})


class ToonPaletteTests(unittest.TestCase):
    # The client's ToonRamp fields (client/src/game/toon/palette.ts).
    FIELDS = {"threshold", "softness", "shadowTint", "shadowLight", "rim", "specular", "saturation", "outline",
              "farStart", "farEnd"}
    # The character rows' extra fields (character adoption, docs/art/character-adoption.md section 5).
    CHARACTER_FIELDS = {"shadeShift", "aoFloor", "skyLift", "rimWidth", "band", "bandCentre", "bandWidth", "faceSdfSoftness",
                        "faceLineSkin", "faceLineFloor", "faceIrisGlow"}  # the last three: look pass 2 (character-look.md section 3)
    # Castle D1 (docs/art/castle-kit.md "Shading: the AO bake"): the world rows' shade fields.
    WORLD_SHADE_FIELDS = {"shadeShift", "aoFloor", "skyLift", "rimWidth", "valueFloor", "nearFade"}
    SSAO_FIELDS = {"tint", "strength", "radius", "fadeEnd", "characters"}

    @classmethod
    def setUpClass(cls):
        cls.toon = load(TOON)

    def rows(self):
        yield "default", self.toon["default"]
        yield from self.toon["materials"].items()

    def test_rows_are_partial_toon_ramps(self):
        # `outline`: the characters' inverted-hull outlines (client toon/outline.ts, character-look.md section 4).
        # `fire`: the animated fire's colours (client game/fire/, docs/architecture.md "Rendering").
        # `npcs`: the ghost material and the talking portraits' painterly look (docs/architecture.md "NPCs").
        self.assertEqual(set(self.toon) - {"_doc"}, {"default", "materials", "post", "characters", "outline", "fire", "npcs"})
        npcs = self.toon["npcs"]
        for k in ("tint", "rim"):
            self.assertTrue(len(npcs["ghost"][k]) == 3 and all(0 <= c <= 1 for c in npcs["ghost"][k]), k)
        self.assertTrue(0 < npcs["ghost"]["alpha"] < 1)
        self.assertTrue(len(npcs["portrait"]["tint"]) == 3 and all(0 <= c <= 1 for c in npcs["portrait"]["tint"]))
        characters = {f"characters.{k}": v for k, v in self.toon["characters"].items() if k != "_doc"}
        for name, r in [*self.rows(), *characters.items()]:
            fields = self.FIELDS | (self.CHARACTER_FIELDS if name.startswith("characters.") else self.WORLD_SHADE_FIELDS)
            self.assertTrue(set(r) <= fields, name)
            for k in ("aoFloor", "valueFloor"):
                self.assertTrue(0 <= r.get(k, 1) <= 1, (name, k))
            self.assertTrue(0 <= r.get("shadeShift", 0) <= 2, name)
            self.assertTrue(-1 <= r.get("skyLift", 0) <= 1, name)
            if "nearFade" in r:
                near, far = r["nearFade"]
                self.assertTrue(0 < near < far <= 5, name)
            if "shadowTint" in r:
                self.assertEqual(len(r["shadowTint"]), 3, name)
                self.assertTrue(all(0 <= c <= 1 for c in r["shadowTint"]), name)
            self.assertTrue(-1 <= r.get("threshold", 0) <= 1, name)
            # A narrow step (brief: 0.05-0.1 in all). Vegetation look A's documented exception
            # (docs/art/style-guide.md section 12): castle_foliage's soft bands over bent normals.
            cap = FOLIAGE_SOFTNESS_CAP if name in FOLIAGE_ROWS else 0.06
            self.assertTrue(0 < r.get("softness", 0.04) <= cap, name)
            for k in ("shadowLight", "saturation", "outline"):
                self.assertTrue(0 <= r.get(k, 0) <= 1, (name, k))
            for k in ("rim", "specular"):
                self.assertTrue(0 <= r.get(k, 0) <= 2, (name, k))

    def test_world_shade_rows(self):
        # Castle D1: the kitted castle's rows read the baked shade; glass,
        # sky and glows never do; floor_grounds keeps its hue ramp (COLOR_0 is
        # colour there), so its row has no shade path.
        m = self.toon["materials"]
        for name in ("castle_wall*", "castle_floor*", "castle_ceiling", "castle_trim", "castle_wood", "castle_props",
                     "castle_portraits*", "castle_cloth", "wall", "wall_dungeon", "wall_exterior", "trim"):
            self.assertGreater(m[name].get("shadeShift", 0), 0, name)
        for name in ("castle_glass", "castle_sky", "glow_*", "floor_grounds"):
            self.assertFalse(set(m[name]) & {"shadeShift", "aoFloor", "valueFloor"}, name)
        ssao = self.toon["post"]["ssao"]
        self.assertEqual(set(ssao), self.SSAO_FIELDS)
        t = ssao["tint"]
        self.assertEqual(len(t), 3)
        self.assertGreater(max(t) - min(t), 0.05)  # a tint, never grey
        self.assertTrue(0 < ssao["strength"] <= 1 and 0 < ssao["radius"] <= 2 and 0 <= ssao["characters"] <= 1)

    def test_skin_and_face_shadows_are_peach_rose_at_pass_one_brightness(self):
        # Look pass 2 (docs/art/character-look.md section 3): warmer than pass 1's
        # grey-pink at the same luma, so no face gets brighter or darker.
        rows = self.toon["characters"]
        for name in ("skin", "face"):
            r, g, b = rows[name]["shadowTint"]
            self.assertGreaterEqual(r, 0.98, name)
            self.assertTrue(r > g > b, name)
            self.assertGreaterEqual(r - b, 0.2, name)
            self.assertAlmostEqual(0.3 * r + 0.59 * g + 0.11 * b, 0.85, delta=0.01, msg=name)
        face = rows["face"]
        self.assertTrue(0.4 <= face["faceLineSkin"] <= 1 and 0.2 <= face["faceLineFloor"] <= 0.8, face)
        self.assertTrue(0 < face["faceIrisGlow"] <= 0.5, face)

    def test_shadows_are_warm_tints_not_grey(self):
        for name, r in self.rows():
            t = r.get("shadowTint")
            if t is None or t == [1, 1, 1]:
                continue  # emissive: drawn lit
            self.assertGreater(max(t) - min(t), 0.05, name)

    def test_every_world_material_has_a_ramp(self):
        names = set(L.MATERIALS) | {"kit"}
        for shell in dorms.build_all().values():
            names |= {f"{shell.cell}_{role}" for role in shell.materials}
        for room in sr.build_all().values():
            names |= {f"{room.cell}_{role}" for role in room.materials}
        # Furniture: build_furniture.py names each model's material
        # furniture_<asset>; the client's stand-in block uses "furniture".
        names |= {f"furniture_{key.split('/', 1)[1]}" for key in fm.MODELS} | {"furniture"}
        missing = sorted(n for n in names if toon_entry(self.toon, n) is None)
        self.assertEqual(missing, [])

    def test_preview_base_ramp_matches_the_client(self):
        # render_toon.py resolves rows over a copy of the client's BASE_RAMP
        # (client/src/game/toon/palette.ts); the two must agree, or the review
        # renders shade rows differently from the game.
        with open(os.path.join(REPO, "client", "src", "game", "toon", "palette.ts"), encoding="utf-8") as f:
            ts = f.read()
        block = re.search(r"export const BASE_RAMP: ToonRamp = \{(.*?)\};", ts, re.S).group(1)
        client = {k: json.loads(v) for k, v in re.findall(r"(\w+): (\[[^\]]*\]|-?[\d.]+)", block)}
        with open(os.path.join(os.path.dirname(HERE), "world", "render_toon.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        preview = next(ast.literal_eval(n.value) for n in tree.body
                       if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "BASE_RAMP")
        # The client's base ramp also carries the character fields (unused by the world)
        # and, from castle D1, the world shade fields (neutral defaults).
        self.assertEqual(set(client) - self.CHARACTER_FIELDS - self.WORLD_SHADE_FIELDS, self.FIELDS)
        self.assertEqual({k: preview[k] for k in self.FIELDS}, {k: client[k] for k in self.FIELDS})
        shared = (self.WORLD_SHADE_FIELDS & set(preview)) & set(client)
        self.assertEqual({k: preview[k] for k in shared}, {k: client[k] for k in shared})
        # Castle D1: the preview's base carries every world shade field, neutral.
        self.assertTrue(self.WORLD_SHADE_FIELDS <= set(preview))
        self.assertEqual((preview["shadeShift"], preview["aoFloor"], preview["skyLift"], preview["valueFloor"],
                          preview["nearFade"]), (0, 1, 0, 1, [0, 0]))

    def test_glows_are_drawn_lit(self):
        for name in ("glow_fire", "dorm_house_a_fire", "class_charms_glass"):
            self.assertEqual(toon_entry(self.toon, name).get("threshold"), -1, name)


if __name__ == "__main__":
    unittest.main()
