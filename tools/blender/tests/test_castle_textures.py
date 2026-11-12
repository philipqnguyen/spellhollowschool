"""Unit tests for the castle texture library (castle kit M1):
castle_kit/textures.py, castle_kit/trim.py and the castle painters of
world/paint.py, against data/art/castle_textures.json and the textures.json
contract (docs/art/castle-kit.md "Textures", "Face tags and UVs"). Pure Python + numpy; run
through tests/run_tests.py. The library is built once into a temporary
directory (about 20 s)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

from castle_kit import grid as G  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit import textures as T  # noqa: E402
from castle_kit import trim  # noqa: E402
import paint  # noqa: E402

SPEC_PATH = os.path.join(REPO, "data", "art", "castle_textures.json")
DATA_TEXTURES = ("sky_clouds",)
MASK_TEXTURES = ("foliage_atlas", "foliage_atlas_lo")  # castle_foliage: alpha mask at 0.5  # castle kit M3: R = G = B cloud cover, A stars (docs/art/castle-kit.md)
ENTRY_KEYS = {"base", "lo", "px", "emissive", "emissive_lo", "emissive_px", "medium", "emissive_strength", "unlit", "alpha", "alpha_cutoff",
              "double_sided", "shared_normals", "tint", "key", *T.KTX2_KEYS}
BASISU = T.basisu_binary()


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def quantised(img):
    """What the PNG stores, back as floats (rows bottom to top)."""
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8).astype(np.float32) / 255.0


def seam(img, axis):
    """Mean colour step across the wrap of `axis` (0 = rows, 1 = columns)
    and the mean step between neighbours inside the image."""
    a = img[..., :3]
    if axis == 0:
        wrap = np.abs(a[0] - a[-1]).mean()
        inner = np.abs(np.diff(a, axis=0)).mean()
    else:
        wrap = np.abs(a[:, 0] - a[:, -1]).mean()
        inner = np.abs(np.diff(a, axis=1)).mean()
    return wrap, inner


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="castle_textures_")
        cls.out = os.path.join(cls.tmp, "textures")
        os.makedirs(cls.out)
        with open(os.path.join(cls.out, "stale.png"), "wb") as f:
            f.write(b"old")
        cls.result = T.build(cls.out, quiet=True)
        cls.images = cls.result["images"]
        cls.spec = load(SPEC_PATH)
        cls.manifest = load(os.path.join(cls.out, "textures.json"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def m1(self):
        """The materials the library paints: up to textures.MILESTONE, those
        whose painter exists (castle kit M4: a later milestone's texture
        whose painter has not merged yet is left out)."""
        return {n: m for n, m in self.spec["materials"].items() if m["milestone"] <= T.MILESTONE
                and (m["map"] == "atlas" or m["texture"] in T.PAINTERS)}

    def built(self):
        """The materials of the milestones built so far (castle kit M3: castle_sky)."""
        return {n: m for n, m in self.spec["materials"].items() if m["milestone"] <= T.MILESTONE}

    def test_every_milestone_1_material_is_listed_and_nothing_else(self):
        self.assertEqual(self.manifest["version"], 1)
        # Castle M5c: a milestone-7 texture whose painter has not merged is left out.
        pending = {n for n, (tex, _) in G.M5C_TEXTURES.items() if tex not in T.PAINTERS}
        self.assertEqual(set(self.manifest["materials"]), set(self.built()) - pending)
        self.assertEqual(set(self.manifest), {"version", "materials"})

    def test_entries_follow_the_contract(self):
        for name, e in self.manifest["materials"].items():
            m = self.spec["materials"][name]
            self.assertLessEqual(set(e), ENTRY_KEYS, name)
            self.assertEqual(e["base"], m["texture"] + ".png", name)
            self.assertEqual(e["lo"], m["texture"] + "_lo.png", name)
            self.assertEqual(e["px"], m["px"], name)
            self.assertEqual(e.get("unlit", False), m.get("unlit", False), name)
            self.assertEqual(e.get("double_sided", False), m.get("double_sided", False), name)
            self.assertEqual(e.get("tint"), m.get("tint"), name)
            self.assertEqual(e.get("alpha", "opaque"), m.get("alpha", "opaque"), name)
            if m.get("alpha") == "mask":  # vegetation look A: castle_foliage
                self.assertEqual(e["alpha_cutoff"], m.get("alpha_cutoff", 0.5), name)
            else:
                self.assertNotIn("alpha_cutoff", e, name)  # the rest opaque
            if "emissive" in m:
                self.assertIn("emissive", e, name)
                self.assertIn("emissive_lo", e, name)
                self.assertEqual(e.get("emissive_strength"), m.get("emissive_strength"), name)
            else:
                self.assertNotIn("emissive", e, name)
        mats = self.manifest["materials"]
        self.assertEqual(mats["castle_glass"]["emissive"], mats["castle_glass"]["base"])
        self.assertEqual(mats["castle_glass"]["emissive_lo"], mats["castle_glass"]["lo"])
        self.assertEqual(mats["castle_props"]["emissive"], "props_atlas_e.png")
        self.assertEqual(mats["castle_props"]["emissive_lo"], "props_atlas_e_lo.png")
        # The props glow mask is stored at half the atlas (castle-kit.md "Textures").
        self.assertEqual(mats["castle_props"]["emissive_px"], [512, 512])
        self.assertNotIn("emissive_px", mats["castle_glass"])
        # Castle D1 (castle-kit.md): no material draws its half-size
        # file at Medium any more; the trim sheet is full size there again (it was `medium: "lo"`).
        self.assertEqual(sorted(n for n, e in mats.items() if e.get("medium") == "lo"), [])
        self.assertTrue(mats["castle_cloth"]["double_sided"])
        self.assertEqual(sorted(n for n, e in mats.items() if "key" in e), ["castle_props"])

    def test_props_key_matches_the_shared_slot_table(self):
        self.assertEqual(self.manifest["materials"]["castle_props"]["key"], PA.build()[1].key())
        self.assertRegex(self.manifest["materials"]["castle_props"]["key"], r"^[0-9a-f]{12}$")

    def test_files_sizes_and_lo_halves(self):
        files = set()
        for e in self.manifest["materials"].values():
            for key, scale in (("base", 1), ("lo", 2), ("emissive", 1), ("emissive_lo", 2)):
                if key not in e:
                    continue
                w, h = e.get("emissive_px", e["px"]) if key.startswith("emissive") else e["px"]
                path = os.path.join(self.out, e[key])
                with open(path, "rb") as f:
                    data = f.read()
                self.assertEqual(T.png_info(data), (w // scale, h // scale, 8, 6), e[key])
                files.add(e[key])
        self.assertEqual(files, {n for n in os.listdir(self.out) if n.endswith(".png")})  # stale.png removed
        self.assertEqual(set(self.result["sizes"]), files)

    def test_png_content_is_the_painted_image(self):
        # Every row is Paeth-filtered, so the stored scanlines can be checked
        # against the painted arrays without a full decoder.
        for stem, img in self.images.items():
            for name, arr in ((f"{stem}.png", img), (f"{stem}_lo.png", paint.half(img))):
                with open(os.path.join(self.out, name), "rb") as f:
                    data = f.read()
                a = (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)[::-1]
                idat, pos = b"", 8
                while pos < len(data):
                    n = int.from_bytes(data[pos : pos + 4], "big")
                    if data[pos + 4 : pos + 8] == b"IDAT":
                        idat += data[pos + 8 : pos + 8 + n]
                    pos += 12 + n
                self.assertEqual(zlib.decompress(idat), T._paeth_rows(np.ascontiguousarray(a)), name)

    def test_png_round_trip(self):
        rng = np.random.default_rng(3)
        img = rng.random((12, 10, 4)).astype(np.float32)
        img[..., 3] = 1.0
        back = T.decode_png(T.png_bytes(img))
        expect = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)[::-1]
        np.testing.assert_array_equal(back, expect)

    def glow_mask(self, half: bool = False):
        """The props atlas's glowing slots (castle kit M2: their base is the
        glow rule's 8 %, below the albedo floor on purpose)."""
        import kit

        table = kit.Atlas()
        table.slots = PA.build()[1].slots
        mask = table.glow_mask()
        return mask[::2, ::2] | mask[1::2, ::2] | mask[::2, 1::2] | mask[1::2, 1::2] if half else mask

    def test_albedo_range(self):
        lo, hi = paint.ALBEDO_RANGE
        for e in self.manifest["materials"].values():
            stem = e["base"][:-4]
            if stem in DATA_TEXTURES:
                continue  # (castle kit M3: data, not albedo; SkyTests check it)
            for half, img in ((False, self.images[stem]), (True, paint.half(self.images[stem]))):
                y = paint.luminance(quantised(img))
                if stem == "props_atlas":
                    y = y[~self.glow_mask(half)]
                self.assertGreaterEqual(float(y.min()), lo, stem)
                self.assertLessEqual(float(y.max()), hi, stem)

    def test_glow_tiles_follow_the_glow_rule(self):
        """Castle kit M2 (docs/art/castle-kit.md "Glow"): the lantern and candle
        glow slots exist, glow their colour, and their base (lit) copy is at
        most GLOW_BASE_MAX of the glow in linear light."""
        _, atlas = PA.build()
        for colour in (G.GLOW_LANTERN, G.GLOW_CANDLE):
            atlas.index("glow", colour)
        base, glow, _ = T.paint_props()
        mask = self.glow_mask()
        lin_b = paint.srgb_to_linear(np.clip(base[..., :3], 0, 1))[mask]
        lin_g = paint.srgb_to_linear(np.clip(glow[..., :3], 0, 1))[mask]
        self.assertTrue(np.all(lin_b <= lin_g * G.GLOW_BASE_MAX + 1e-4))
        g = max(4, atlas._atlas.grid) if hasattr(atlas, "_atlas") else 16
        for colour in (G.GLOW_LANTERN, G.GLOW_CANDLE):
            i = atlas.index("glow", colour)
            x, y = (i % g) * 64, (i // g) * 64
            mean = glow[y + 8:y + 56, x + 8:x + 56, :3].reshape(-1, 3).mean(axis=0)
            np.testing.assert_allclose(mean, paint.hex_rgb(colour), atol=0.02)
        # The level the client draws them at.
        self.assertEqual(self.spec["materials"]["castle_props"]["emissive_strength"], G.GLOW_LEVEL)

    def test_opaque(self):
        for stem, img in self.images.items():
            if stem in DATA_TEXTURES:
                continue  # its alpha is the star field (castle kit M3)
            if stem in MASK_TEXTURES:
                continue  # vegetation look A: the foliage atlas's alpha is its leaves' outline (alpha mask)
            self.assertTrue(np.all(img[..., 3] == 1.0), stem)

    def test_world_textures_tile(self):
        for name, m in self.m1().items():
            if m["map"] != "world":
                continue
            img = quantised(self.images[m["texture"]])
            for axis in (0, 1):
                wrap, inner = seam(img, axis)
                self.assertLessEqual(wrap, inner * 1.6 + 0.002, (name, axis, wrap, inner))

    def test_full_width_strips_wrap_in_u(self):
        for tex in ("trim_castle", "wood_sheet"):
            sheet = self.spec["sheets"][tex]
            img = quantised(self.images[tex])
            h = sheet["px"][1]
            for sname, st in sheet["strips"].items():
                if "cols" in st:
                    continue
                top, bottom = st["rows"]
                band = img[h - bottom : h - top]
                wrap, inner = seam(band, 1)
                self.assertLessEqual(wrap, inner * 1.6 + 0.002, (tex, sname, wrap, inner))

    def test_sheet_pads_repeat_the_edges(self):
        for tex in ("trim_castle", "wood_sheet", "glass_sheet", "banners"):
            sheet = self.spec["sheets"][tex]
            img, h, p = self.images[tex], sheet["px"][1], sheet["pad"]
            rects = [(st["rows"][0], st["rows"][1], *st.get("cols", [0, sheet["px"][0]]), "cols" in st)
                     for st in sheet["strips"].values()]
            rects += [(y, y + th, x, x + tw, True) for x, y, tw, th in sheet["tiles"].values()]
            for top, bottom, c0, c1, cols in rects:
                r0, r1 = h - bottom, h - top
                block = img[r0:r1, c0:c1]
                np.testing.assert_array_equal(block[:p], np.repeat(block[p : p + 1], p, axis=0))
                np.testing.assert_array_equal(block[-p:], np.repeat(block[-p - 1 : -p], p, axis=0))
                if cols:
                    np.testing.assert_array_equal(block[:, :p], np.repeat(block[:, p : p + 1], p, axis=1))
                    np.testing.assert_array_equal(block[:, -p:], np.repeat(block[:, -p - 1 : -p], p, axis=1))

    def test_mortar_registers_with_world_height(self):
        # v = 0 is a course line at y = 0: on the ashlar, the rows either side
        # of every course boundary are mortar (darker than the course middles).
        for name, m in self.m1().items():
            if m.get("family") != "castle_wall" and name != "castle_ceiling":
                continue
            img = quantised(self.images[m["texture"]])
            n = img.shape[0]
            course = G.COURSE if name != "castle_ceiling" else 0.3
            step = n * course / m["tile_m"]
            self.assertAlmostEqual(step, round(step), msg=name)
            step = int(round(step))
            y = paint.luminance(img).mean(axis=1)
            for k in range(n // step):
                joint = (y[(k * step - 1) % n] + y[k * step]) / 2
                middle = y[k * step + step // 2]
                self.assertLess(joint, middle * 0.85, (name, k))

    def test_lo_is_the_half_of_the_base(self):
        for stem, img in self.images.items():
            lo = paint.half(img)
            self.assertEqual(lo.shape[:2], (img.shape[0] // 2, img.shape[1] // 2))
            np.testing.assert_allclose(paint.srgb_to_linear(lo[..., :3]).astype(np.float64).mean(axis=(0, 1)),
                                       paint.srgb_to_linear(img[..., :3]).astype(np.float64).mean(axis=(0, 1)),
                                       atol=1e-6, err_msg=stem)

    def test_deterministic(self):
        spec = T.load_spec(SPEC_PATH)
        spec["_path"] = SPEC_PATH
        images, entries = T.library(spec)
        if self.result["ktx2_sizes"]:
            entries = T.ktx2_entries(spec, entries)
        self.assertEqual(entries, self.manifest["materials"])
        self.assertEqual(set(images), set(self.images))
        for stem in images:
            np.testing.assert_array_equal(images[stem], self.images[stem], stem)
        for stem in ("vault_web", "stone_ashlar_a"):
            with open(os.path.join(self.out, f"{stem}.png"), "rb") as f:
                self.assertEqual(f.read(), T.png_bytes(images[stem]), stem)

    # -- KTX2 (castle kit M4, docs/art/castle-kit.md "Textures") ----------------------------------

    def ktx2(self, name):
        with open(os.path.join(self.out, name), "rb") as f:
            return f.read()

    def unpacked(self, name, code):
        """Level 0 of a KTX2 transcoded by basisu to the GPU format `code` (0 ETC1 RGB, 10 ASTC 4x4:
        what an Apple GPU samples) and decoded, uint8 [h, w, 4], top row first."""
        d = tempfile.mkdtemp(prefix="unpack_", dir=self.tmp)
        r = subprocess.run([BASISU, "-unpack", "-no_ktx", "-format_only", str(code), "-output_path", d,
                            os.path.join(self.out, name)], cwd=d, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-500:])
        f = sorted(n for n in os.listdir(d) if ("_rgba_" in n or "_rgb_" in n) and "level_0_" in n and n.endswith(".png"))
        self.assertTrue(f, os.listdir(d)[:5])
        with open(os.path.join(d, f[0]), "rb") as fh:
            return T.decode_png(fh.read())

    def png(self, name):
        with open(os.path.join(self.out, name), "rb") as f:
            return T.decode_png(f.read())

    @unittest.skipUnless(BASISU, "no basisu")
    def test_ktx2_files_and_fields(self):
        mats = self.manifest["materials"]
        listed = set()
        for name, e in mats.items():
            mode = self.spec["materials"][name]["ktx2"]["mode"]
            self.assertIn(mode, G.KTX2_MODES)
            self.assertEqual(e["ktx2_mode"], mode, name)
            self.assertEqual(e["ktx2"], e["base"][:-4] + ".ktx2", name)
            self.assertEqual(e["ktx2_lo"], e["lo"][:-4] + ".ktx2", name)
            if "emissive" in e and e["emissive"] != e["base"]:
                self.assertEqual(e["emissive_ktx2"], e["emissive"][:-4] + ".ktx2", name)
                self.assertEqual(e["emissive_ktx2_lo"], e["emissive_lo"][:-4] + ".ktx2", name)
            else:
                self.assertNotIn("emissive_ktx2", e, name)  # castle_glass: its base again
                self.assertNotIn("emissive_ktx2_lo", e, name)
            listed |= {e[k] for k in T.KTX2_KEYS if k != "ktx2_mode" and k in e}
        self.assertEqual(listed, {n for n in os.listdir(self.out) if n.endswith(".ktx2")})
        self.assertEqual(set(self.result["ktx2_sizes"]), listed)
        self.assertIn("emissive_ktx2", mats["castle_props"])

    @unittest.skipUnless(BASISU, "no basisu")
    def test_ktx2_headers(self):
        # Full mip chains, the PNG's size, ETC1S as BasisLZ and UASTC with
        # Zstandard, sRGB transfer (sampled as stored values, like the PNGs:
        # the client's decoder transcodes to UNORM formats), no alpha on
        # opaque images.
        for name, e in self.manifest["materials"].items():
            for key, pkey in (("ktx2", "base"), ("ktx2_lo", "lo"), ("emissive_ktx2", "emissive"),
                              ("emissive_ktx2_lo", "emissive_lo")):
                if key not in e:
                    continue
                info = T.ktx2_info(self.ktx2(e[key]))
                w, h, _, _ = T.png_info(self.ktx2(e[pkey]))
                self.assertEqual((info["width"], info["height"]), (w, h), e[key])
                self.assertEqual(info["levels"], max(w, h).bit_length(), e[key])
                self.assertEqual((info["layers"], info["faces"], info["vk_format"]), (0, 1, 0), e[key])
                if e["ktx2_mode"] == "etc1s":
                    self.assertEqual((info["supercompression"], info["model"]), (1, 163), e[key])
                else:
                    self.assertEqual((info["supercompression"], info["model"]), (2, 166), e[key])
                self.assertEqual(info["transfer"], 2, e[key])
                stem = e[pkey][:-4].removesuffix("_lo")
                self.assertEqual(info["alpha"], bool(np.any(self.images[stem][..., 3] < 1.0)), e[key])

    @unittest.skipUnless(BASISU, "no basisu")
    def test_ktx2_rows_top_first_and_close_to_the_png(self):
        # basisu -unpack's top row is the PNG's top row (never flipped), and
        # the pixels stay close: UASTC within about 1/255 on average, ETC1S
        # within a few (its 4x4 blocks share one hue); means within 4 / 8.
        for name, mode, limit in (("vault_web_lo", "etc1s", 4.0), ("props_atlas_e_lo", "uastc", 1.0),
                                  ("stone_flag_lo", "etc1s", 4.0)):
            self.assertEqual(T.ktx2_info(self.ktx2(f"{name}.ktx2"))["model"], 163 if mode == "etc1s" else 166)
            k = self.unpacked(f"{name}.ktx2", 0 if mode == "etc1s" else 10).astype(np.float64)
            p = self.png(f"{name}.png").astype(np.float64)
            self.assertEqual(k.shape, p.shape, name)
            mae = np.abs(k[..., :3] - p[..., :3]).mean()
            flipped = np.abs(k[::-1, :, :3] - p[..., :3]).mean()
            self.assertLess(mae, limit, name)
            self.assertLess(mae * 2, flipped, name)
            self.assertLess(np.abs(k[0, :, :3] - p[0, :, :3]).mean(), limit * 2, name)
            bias = np.abs((k[..., :3] - p[..., :3]).mean(axis=(0, 1)))
            self.assertTrue(np.all(bias <= (8.0 if mode == "etc1s" else 4.0)), (name, bias))

    @unittest.skipUnless(BASISU, "no basisu")
    def test_ktx2_deterministic(self):
        spec = T.load_spec(SPEC_PATH)
        for stem, name in (("vault_web_lo", "castle_ceiling"), ("props_atlas_e_lo", "castle_props")):
            mode, tuning = T.ktx2_tuning(spec["materials"][name])
            out = os.path.join(self.tmp, f"again_{stem}.ktx2")
            T.encode_ktx2(BASISU, os.path.join(self.out, f"{stem}.png"), out, mode, tuning)
            with open(out, "rb") as f:
                self.assertEqual(f.read(), self.ktx2(f"{stem}.ktx2"), stem)

    def test_ktx2_options(self):
        spec = T.load_spec(SPEC_PATH)
        for name, m in spec["materials"].items():
            mode, tuning = T.ktx2_tuning(m)
            args = T.ktx2_args(mode, tuning)
            self.assertNotIn("-y_flip", args)
            for a in ("-ktx2", "-mipmap", "-mip_linear"):
                self.assertIn(a, args)
            self.assertEqual(args[args.index("-mip_filter") + 1], "box")
            self.assertEqual(args[args.index("-max_threads") + 1], str(T.KTX2_THREADS))
            self.assertIn("-" + mode, args, name)
        self.assertEqual(T.ktx2_tuning({"ktx2": {"mode": "etc1s"}})[0], "etc1s")
        self.assertEqual(T.ktx2_tuning({})[0], G.KTX2_DEFAULT_MODE)
        with self.assertRaises(ValueError):
            T.ktx2_tuning({"ktx2": {"mode": "astc"}})
        with self.assertRaises(ValueError):
            T.ktx2_tuning({"ktx2": {"mode": "etc1s", "rdo": 1}})  # an RDO lambda is UASTC's
        rdo = T.ktx2_args(*T.ktx2_tuning({"ktx2": {"mode": "uastc", "rdo": 0.5}}))
        self.assertIn("-uastc_rdo_m", rdo)  # single-threaded RDO: deterministic
        self.assertNotIn("-uastc_rdo_m", T.ktx2_args(*T.ktx2_tuning({"ktx2": {"mode": "uastc"}})))

    def test_without_basisu_the_library_is_png_only(self):
        out = os.path.join(self.tmp, "no_basisu")
        os.makedirs(out)
        with open(os.path.join(out, "stale.ktx2"), "wb") as f:
            f.write(b"old")
        old = os.environ.get("BASISU")
        os.environ["BASISU"] = os.path.join(self.tmp, "no-such-basisu")
        try:
            self.assertIsNone(T.basisu_binary())
            result = T.build(out, quiet=True)
        finally:
            if old is None:
                del os.environ["BASISU"]
            else:
                os.environ["BASISU"] = old
        self.assertEqual(result["ktx2_sizes"], {})
        self.assertFalse([n for n in os.listdir(out) if n.endswith(".ktx2")])
        manifest = load(os.path.join(out, "textures.json"))
        for e in manifest["materials"].values():
            self.assertFalse(set(e) & set(T.KTX2_KEYS))


class PainterTests(unittest.TestCase):
    def test_bond_rows_wrap_and_stagger(self):
        # Every course keeps 0.3 m from its neighbours where it can; 0.25 m
        # (still a clear stagger) is what this seed reaches.
        rng = np.random.default_rng(5)
        rows = paint.bond_rows(rng, 4.8, (0.9, 1.8), 8, 0.3)
        for r, j in enumerate(rows):
            lens = np.diff(np.concatenate([j, [j[0] + 4.8]]))
            self.assertTrue(np.all(lens >= 0.9 - 1e-6) and np.all(lens <= 1.8 + 1e-6), lens)
            other = rows[(r + 1) % len(rows)]
            d = np.abs(j[:, None] - other[None, :]) % 4.8
            self.assertGreaterEqual(float(np.minimum(d, 4.8 - d).min()), 0.25)

    def test_block_map_keeps_blocks_across_the_wrap(self):
        rows = [(0.0, 0.6), (0.6, 1.2)]
        joints = [np.array([0.5, 1.4]), np.array([0.2, 1.0])]
        bm = paint.block_map(64, 64, 64 / 1.2, rows, joints, 1.2)
        self.assertEqual(bm["n"], 4)
        # The block straddling x = 0 has one id on both edges of the image.
        np.testing.assert_array_equal(bm["id"][:, 0], bm["id"][:, -1])
        self.assertAlmostEqual(float(bm["db"][0, 10]), 0.5)
        self.assertAlmostEqual(float(bm["dt"][-1, 10]), 0.5)

    def test_fit_albedo(self):
        img = np.ones((2, 3, 4), dtype=np.float32)
        img[0, 0, :3] = 0.0
        img[0, 1, :3] = (0.05, 0.0, 0.3)
        img[0, 2, :3] = 1.0
        out = paint.fit_albedo(img)
        y = paint.luminance(out)
        self.assertTrue(np.all(y >= 0.045 - 1e-4) and np.all(y <= 0.835 + 1e-4), y)
        mid = np.full((1, 1, 4), 0.5, dtype=np.float32)
        np.testing.assert_allclose(paint.fit_albedo(mid), mid, atol=1e-6)

    def test_half_is_exact(self):
        img = np.zeros((4, 4, 4), dtype=np.float32)
        img[..., :3] = 0.25
        img[..., 3] = 1
        out = paint.half(img)
        self.assertEqual(out.shape, (2, 2, 4))
        np.testing.assert_allclose(out, img[:2, :2], atol=1e-6)
        with self.assertRaises(ValueError):
            paint.half(np.zeros((3, 4, 4), dtype=np.float32))

    def test_sheet_coordinates_match_grid_uvs(self):
        # The painter evaluates each strip at the pixels grid.strip_uv samples.
        sheet = load(SPEC_PATH)["sheets"]["trim_castle"]
        sh = trim.Sheet(sheet)
        for name in ("cornice", "nosing_stone", "shaft"):
            rows, cols, S, P, _, _ = sh.strip(name)
            st = sheet["strips"][name]
            w, h = sheet["px"]
            for j, i in ((sheet["pad"], 10), (rows.stop - rows.start - sheet["pad"] - 1, 200)):
                u, v = G.strip_uv(sheet, name, float(S[j, i]), float(P[j, i]))
                px_x = (u % 1.0) * w if "cols" not in st else u * w
                px_y = v * h  # Blender v: up from the bottom row
                self.assertAlmostEqual(px_x, cols.start + i + 0.5, delta=0.51, msg=name)
                self.assertAlmostEqual(px_y, rows.start + j + 0.5, delta=0.51, msg=name)
        rows, cols, A, B = sh.tile("keystone")
        u, v = G.tile_uv(sheet, "keystone", float(A[20, 30]), float(B[20, 30]))
        self.assertAlmostEqual(u * sheet["px"][0], cols.start + 30 + 0.5, delta=0.51)
        self.assertAlmostEqual(v * sheet["px"][1], rows.start + 20 + 0.5, delta=0.51)

    def test_every_painted_texture_has_a_spec(self):
        spec = load(SPEC_PATH)
        textures = {m["texture"] for m in spec["materials"].values() if m["milestone"] <= T.MILESTONE and m["map"] != "atlas"}
        # Castle M5c: each M5c builder paints one milestone-7 texture, so on a
        # builder's branch the other's painter may not have merged yet (a reserved name).
        pending = {tex for tex, _ in G.M5C_TEXTURES.values()} - set(T.PAINTERS)
        self.assertEqual(set(T.PAINTERS), textures - pending)
        self.assertEqual(set(trim.FIT_TILES), set(spec["sheets"]["trim_castle"]["tiles"]))

    def test_gpu_estimate(self):
        entries = {"a": {"base": "a.png", "lo": "a_lo.png", "px": [1024, 1024]},
                   "g": {"base": "g.png", "lo": "g_lo.png", "px": [1024, 1024], "emissive": "g.png",
                         "emissive_lo": "g_lo.png"},
                   "p": {"base": "p.png", "lo": "p_lo.png", "px": [1024, 1024], "emissive": "p_e.png",
                         "emissive_lo": "p_e_lo.png"}}
        one = 1024 * 1024 * 4 * 4 // 3
        self.assertEqual(T.gpu_bytes(entries), 4 * one)
        self.assertEqual(T.gpu_bytes(entries, lo=True), 4 * (512 * 512 * 4 * 4 // 3))
        # KTX2: the transcoded format, mips by whole 4x4 blocks.
        blocks = sum(((1024 >> k) + 3) // 4 * (((1024 >> k) + 3) // 4) for k in range(11))
        self.assertEqual(T.mip_bytes(1024, 1024, 16), blocks * 16)
        self.assertEqual(T.mip_bytes(4, 4, None), 4 * 4 * 4 + 2 * 2 * 4 + 4)
        k = {n: dict(e, ktx2_mode="uastc") for n, e in entries.items()}
        k["a"]["ktx2_mode"] = "etc1s"
        self.assertEqual(T.gpu_bytes(k, ktx2=True), 3 * blocks * 16 + blocks * 8)  # a: ETC2 RGB / BC1
        self.assertEqual(T.gpu_bytes(k, ktx2=True, alpha=frozenset({"a"})), 4 * blocks * 16)
        self.assertEqual(T.gpu_bytes(entries, ktx2=True), 4 * one)  # no KTX2 fields: counted as RGBA8
        self.assertLess(T.gpu_bytes(k, ktx2=True) / T.gpu_bytes(entries), 0.25)


class SkyTests(unittest.TestCase):
    """Castle kit M3 (the Great Hall): castle_sky's sky_clouds data
    texture (docs/art/castle-kit.md)."""

    @classmethod
    def setUpClass(cls):
        cls.spec = load(SPEC_PATH)
        cls.img = T.paint_sky_clouds(cls.spec["materials"]["castle_sky"]["px"])

    def test_listed_with_its_flags(self):
        spec = T.load_spec(SPEC_PATH)
        spec["_path"] = SPEC_PATH
        _, entries = T.library(spec)
        e = entries["castle_sky"]
        self.assertEqual((e["base"], e["lo"], e["px"]), ("sky_clouds.png", "sky_clouds_lo.png", [1024, 512]))
        self.assertTrue(e["unlit"])
        self.assertEqual(e["tint"], "sky_dome")
        self.assertNotIn("emissive", e)
        self.assertGreaterEqual(T.MILESTONE, 3)

    def test_greyscale_cover_and_star_alpha(self):
        img = self.img
        self.assertEqual(img.shape, (512, 1024, 4))
        np.testing.assert_array_equal(img[..., 0], img[..., 1])
        np.testing.assert_array_equal(img[..., 0], img[..., 2])
        self.assertGreaterEqual(float(img.min()), 0.0)
        self.assertLessEqual(float(img.max()), 1.0)
        cover = float(img[..., 0].mean())
        self.assertTrue(0.15 < cover < 0.5, cover)  # cloudy, not overcast
        stars = img[..., 3]
        self.assertGreater(int((stars > 0.4).sum()), 300)
        # More stars toward the ridge (v = 1, the last rows: paint order is bottom to top).
        self.assertGreater(float(stars[256:].mean()), float(stars[:256].mean()))

    def test_ridge_rows_are_quiet_and_u_tiles(self):
        img = self.img
        self.assertLess(float(img[-8:, :, 0].max()), 0.1)  # the two pitches meet in clear sky
        wrap, inner = seam(img, 1)
        self.assertLessEqual(wrap, inner * 1.6 + 0.002)

    def test_deterministic(self):
        np.testing.assert_array_equal(self.img, T.paint_sky_clouds([1024, 512]))


class PortraitSheetTests(unittest.TestCase):
    """Castle kit M4 (docs/art/castle-kit.md "Portraits and tapestries"): the two portrait sheets,
    painted by castle_kit/portraits.py: 8 x 8 cells of 256 px, each ring in a
    palette frame colour, kinds by index, the guardian on sheet b's canvas 63,
    seeded and deterministic, the albedo in range."""

    @classmethod
    def setUpClass(cls):
        from castle_kit import portraits as PT

        cls.PT = PT
        cls.sheets = {k: PT.paint_sheet(k)[::-1] for k in ("portraits_a", "portraits_b")}  # top row first
        cls.names = PA.palette(SPEC_PATH)

    def cell(self, sheet, i):
        x, y = G.canvas_cell(i)
        return self.sheets[sheet][y:y + G.PORTRAIT_CELL, x:x + G.PORTRAIT_CELL, :3]

    def test_size_and_spec(self):
        spec = load(SPEC_PATH)
        for k, img in self.sheets.items():
            self.assertEqual(img.shape, (G.PORTRAIT_SHEET_PX, G.PORTRAIT_SHEET_PX, 4))
            self.assertEqual(len(spec["sheets"][k]["tiles"]), G.PORTRAIT_CANVASES)
        for name in self.PT.FRAME_NAMES:
            self.assertIn(name, self.names)

    def test_rings_take_the_palette_frame_colours(self):
        frames = {n: paint.hex_rgb(self.names[n]) for n in self.PT.FRAME_NAMES}
        o, r = G.PORTRAIT_PAD, G.PORTRAIT_RING
        for sheet in self.sheets:
            for i in range(G.PORTRAIT_CANVASES):
                c = self.cell(sheet, i)
                band = c[o + int(r * 0.55), 40:216]  # the crest of the top side
                mean = band.mean(axis=0)
                hue = mean / max(1e-6, mean.sum())
                best = min(frames, key=lambda n: np.abs(frames[n] / frames[n].sum() - hue).sum())
                self.assertLess(np.abs(frames[best] / frames[best].sum() - hue).sum(), 0.08, (sheet, i))

    def test_kinds(self):
        for sheet in self.sheets:
            for i in range(*G.PORTRAIT_KINDS["landscape"]):
                p = self.cell(sheet, i)[24:232, 24:232]
                sky, ground = p[:60].mean(), p[170:].mean()
                self.assertGreater(sky, ground * 0.9, (sheet, i))  # a sky above, the land below

    def test_guardian(self):
        g = self.cell("portraits_b", G.GUARDIAN_CANVAS[1])[24:232, 24:232]
        robe = g[120:180, 84:124].reshape(-1, 3).mean(axis=0)  # the robe's middle, beside its gold edging
        self.assertGreater(robe[1], robe[0])  # deep green, not pink or rose
        self.assertGreater(robe[1], robe[2])

    def test_albedo_and_lo(self):
        for k, img in self.sheets.items():
            lum = paint.srgb_to_linear(quantised(img[::-1])[..., :3]) @ np.array([0.2126, 0.7152, 0.0722])
            self.assertGreaterEqual(lum.min(), 0.04 - 1e-3, k)
            self.assertLessEqual(lum.max(), 0.85 + 1e-3, k)
            self.assertEqual(paint.half(img).shape[:2], (1024, 1024))

    def test_deterministic(self):
        again = self.PT.paint_sheet("portraits_a")[::-1]
        self.assertTrue(np.array_equal(again, self.sheets["portraits_a"]))
        self.assertFalse(np.array_equal(self.sheets["portraits_a"], self.sheets["portraits_b"]))


# =====================================================================================
# Castle M5a (Corridors): the tapestry sheet and the darker stone_ashlar_c
# =====================================================================================


class M5aCorridorsTapestryTests(unittest.TestCase):
    """castle_tapestry: the `tapestries` sheet painted by
    castle_kit/tapestries.py from the palette's tapestry_* names only, its
    tiles each the piece's aspect, seeded and deterministic; the darker
    stone_ashlar_c of the Grand Staircase."""

    @classmethod
    def setUpClass(cls):
        from castle_kit import tapestries as TP

        cls.TP = TP
        cls.spec = load(SPEC_PATH)
        cls.sheet = cls.spec["sheets"]["tapestries"]
        cls.img = TP.paint_sheet(cls.sheet, SPEC_PATH)

    def tile(self, img, name):
        x, y, tw, th = self.sheet["tiles"][name]
        top = img[::-1]
        return top[y:y + th, x:x + tw, :3]

    def test_listed_at_milestone_5(self):
        self.assertGreaterEqual(T.MILESTONE, 5)  # (castle M5b: 6)
        self.assertIn("tapestries", T.PAINTERS)
        m = self.spec["materials"]["castle_tapestry"]
        self.assertEqual(m["milestone"], 5)
        self.assertEqual(self.img.shape, (1024, 1024, 4))
        for n in self.TP.NAMES:
            self.assertIn(n, PA.palette(SPEC_PATH))
        self.assertEqual(set(self.TP.DESIGNS), set(self.sheet["tiles"]))

    def test_tiles_differ_and_show_their_ground(self):
        names = PA.palette(SPEC_PATH)
        means = {}
        for name, (ground, _) in self.TP.DESIGNS.items():
            t = self.tile(self.img, name)
            h, w = t.shape[:2]
            means[name] = t.reshape(-1, 3).mean(axis=0)
            # The field's most common colour is its ground's hue (a palette name).
            core = t[h // 4: 3 * h // 4, w // 8: 7 * w // 8].reshape(-1, 3)
            g = paint.hex_rgb(names[ground])
            near = np.abs(core / np.maximum(core.sum(axis=1, keepdims=True), 1e-6) - g / g.sum()).sum(axis=1) < 0.06
            self.assertGreater(near.mean(), 0.15, name)
        vals = list(means.values())
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                self.assertGreater(np.abs(vals[i] - vals[j]).sum(), 0.05)

    def test_colours_come_from_the_palette(self):
        """Changing a tapestry name's colour repaints the sheet; changing a name it does
        not read changes nothing."""
        tmp = tempfile.mkdtemp()
        try:
            data = load(SPEC_PATH)
            data["palette"]["tapestry_wine"] = "#205020"
            data["palette"]["iron"] = "#FF00FF"
            path = os.path.join(tmp, "castle_textures.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            other = self.TP.paint_sheet(self.sheet, path)
            changed = np.any(np.abs(other - self.img) > 1e-6, axis=2)[::-1]
            x, y, tw, th = self.sheet["tiles"]["tapestry_2"]
            self.assertGreater(changed[y:y + th, x:x + tw].mean(), 0.3)  # the wine ground
            x, y, tw, th = self.sheet["tiles"]["tapestry_0"]
            self.assertFalse(changed[y + 8:y + th - 8, x + 8:x + tw - 8].any())  # no wine there; iron unread
        finally:
            shutil.rmtree(tmp)

    def test_albedo_pads_and_deterministic(self):
        lum = paint.srgb_to_linear(quantised(self.img)[..., :3]) @ np.array([0.2126, 0.7152, 0.0722])
        self.assertGreaterEqual(lum.min(), 0.04 - 1e-3)
        self.assertLessEqual(lum.max(), 0.85 + 1e-3)
        again = self.TP.paint_sheet(self.sheet, SPEC_PATH)
        self.assertTrue(np.array_equal(again, self.img))
        top = self.img[::-1]
        pad = self.sheet["pad"]
        for x, y, tw, th in self.sheet["tiles"].values():
            t = top[y:y + th, x:x + tw]
            self.assertTrue(np.array_equal(t[:pad, pad:-pad], np.repeat(t[pad:pad + 1, pad:-pad], pad, axis=0)))

    def test_stone_ashlar_c_a_step_darker(self):
        a = T.PAINTERS["stone_ashlar_a"](self.spec)
        c = T.PAINTERS["stone_ashlar_c"](self.spec)
        la = (paint.srgb_to_linear(a[..., :3]) @ np.array([0.2126, 0.7152, 0.0722])).mean()
        lc = (paint.srgb_to_linear(c[..., :3]) @ np.array([0.2126, 0.7152, 0.0722])).mean()
        self.assertLess(lc, la * 0.85)
        self.assertGreater(lc, 0.3)
        # Same hue family as before (cream stone): red > green > blue on average.
        r, g, b = c[..., :3].reshape(-1, 3).mean(axis=0)
        self.assertGreater(r, g)
        self.assertGreater(g, b)



# =====================================================================================
# Castle M5b (Dungeons): the dungeon wall and floor (docs/art/castle-kit.md "Textures")
# =====================================================================================


class M5bDungeonsTextureTests(unittest.TestCase):
    """castle_wall_d (stone_ashlar_dungeon) and castle_floor_dungeon
    (stone_flag_dungeon): painted from the painters' base colours
    (world/palette.py CASTLE stone_dungeon, flagstone_dungeon), the cool
    green-grey, dark but never black, tiling, deterministic."""

    @classmethod
    def setUpClass(cls):
        cls.spec = load(SPEC_PATH)
        cls.wall = T.PAINTERS["stone_ashlar_dungeon"](cls.spec)
        cls.floor = T.PAINTERS["stone_flag_dungeon"](cls.spec)

    @staticmethod
    def lum(img):
        return paint.srgb_to_linear(quantised(img)[..., :3]) @ np.array([0.2126, 0.7152, 0.0722])

    def test_milestone_6_and_listed(self):
        self.assertGreaterEqual(T.MILESTONE, 6)  # castle M5c: the M5c builders move it to 7
        for name, tex in (("castle_wall_d", "stone_ashlar_dungeon"), ("castle_floor_dungeon", "stone_flag_dungeon")):
            m = self.spec["materials"][name]
            self.assertEqual((m["texture"], m["milestone"], m["px"]), (tex, 6, [1024, 1024]))
            self.assertIn(tex, T.PAINTERS)
        self.assertEqual(self.wall.shape, (1024, 1024, 4))
        self.assertEqual(self.floor.shape, (1024, 1024, 4))

    def test_dark_cool_green_grey(self):
        lw, lf = self.lum(self.wall).mean(), self.lum(self.floor).mean()
        # Section 26.6: the wall base at linear luminance 0.20-0.30, the floor's 0.12-0.20 (the
        # painted texture spreads round its base: mortar, block tones, edge bands).
        self.assertTrue(0.15 <= lw <= 0.32, lw)
        self.assertTrue(0.09 <= lf <= 0.22, lf)
        self.assertLess(lf, lw)
        self.assertGreater(self.lum(self.wall).min(), 0.02)  # never black
        # The cool green-grey: green over red and blue on average (the warm stones are red-first).
        r, g, b = self.wall[..., :3].reshape(-1, 3).mean(axis=0)
        self.assertGreater(g, r)
        self.assertGreater(g, b)
        a = T.PAINTERS["stone_ashlar_a"](self.spec)
        self.assertLess(lw, self.lum(a).mean() * 0.5)

    def test_tiles_and_deterministic(self):
        for img in (self.wall, self.floor):
            for axis in (0, 1):
                wrap, inner = seam(img, axis)
                self.assertLess(wrap, inner * 3 + 0.02)
        self.assertTrue(np.array_equal(T.PAINTERS["stone_ashlar_dungeon"](self.spec), self.wall))
        self.assertTrue(np.array_equal(T.PAINTERS["stone_flag_dungeon"](self.spec), self.floor))

    def test_colours_are_the_painters_data(self):
        """The painters take their colours from their base values (palette data in
        textures.py): no colour of the props atlas or the palette names feeds them."""
        import inspect

        src = inspect.getsource(T)
        self.assertIn('"stone_ashlar_dungeon": _ashlar("#7E8C84", "#5F6B64", 1401', src)
        self.assertIn('paint.flagstones(1024, "#646B66", "#454A47", 2401)', src)


# =====================================================================================
# Castle M5c (Facades): the roofs' slate (docs/art/castle-kit.md "Textures")
# =====================================================================================


class M5cFacadesTextureTests(unittest.TestCase):
    """castle_roof (roof_slate): painted from the painter's base
    colours (world/palette.py CASTLE roof, the clear slate blue), coursed slates
    whose courses register with world height, linear luminance 0.10-0.16,
    tiling, deterministic."""

    @classmethod
    def setUpClass(cls):
        cls.spec = load(SPEC_PATH)
        cls.img = T.PAINTERS["roof_slate"](cls.spec)

    @staticmethod
    def lum(img):
        return paint.srgb_to_linear(quantised(img)[..., :3]) @ np.array([0.2126, 0.7152, 0.0722])

    def test_milestone_7_and_listed(self):
        self.assertEqual(T.MILESTONE, G.M5C_MILESTONE)
        m = self.spec["materials"]["castle_roof"]
        self.assertEqual((m["texture"], m["milestone"], m["px"], m["map"]), ("roof_slate", 7, [1024, 1024], "world"))
        self.assertEqual(G.M5C_TEXTURES["castle_roof"], ("roof_slate", "facades"))
        self.assertEqual(self.img.shape, (1024, 1024, 4))

    def test_slate_blue_at_its_luminance(self):
        lum = self.lum(self.img)
        self.assertTrue(0.10 <= lum.mean() <= 0.16, lum.mean())
        self.assertGreater(lum.min(), 0.04 - 1e-3)
        r, g, b = self.img[..., :3].reshape(-1, 3).mean(axis=0)
        self.assertGreater(b, g)
        self.assertGreater(g, r)

    def test_courses_register_with_world_height(self):
        """A course every SLATE_COURSE_M (16 to the 4.8 m repeat): each row of
        pixels at a course line is darker (the joint and the overlap's shadow)
        than the rows between."""
        rows = self.lum(self.img).mean(axis=1)[::-1]  # bottom row first: v = 0 at y = 0
        per = 1024 / (4.8 / T.SLATE_COURSE_M)
        self.assertEqual(4.8 / T.SLATE_COURSE_M, 16)
        lines = [rows[int(round(k * per)) % 1024] for k in range(16)]
        mids = [rows[int(round((k + 0.5) * per))] for k in range(16)]
        self.assertLess(np.mean(lines), np.mean(mids) * 0.9)

    def test_tiles_and_deterministic(self):
        for axis in (0, 1):
            wrap, inner = seam(self.img, axis)
            self.assertLess(wrap, inner * 3 + 0.02)
        self.assertTrue(np.array_equal(T.PAINTERS["roof_slate"](self.spec), self.img))

    def test_colours_are_the_painters_data(self):
        import inspect

        src = inspect.getsource(T)
        self.assertIn('"roof_slate": lambda spec: paint_roof_slate("#4C6596", "#384B6F", 5101)', src)



# =====================================================================================
# Castle M5c (Courtyard): the courtyard's paving (docs/art/castle-kit.md "Textures")
# =====================================================================================


class M5cCourtyardTextureTests(unittest.TestCase):
    """castle_floor_court (stone_paving_court, family castle_floor), milestone
    7: setts painted from the painter's base colours (world/palette.py
    CASTLE flagstone_courtyard), the warm pale grey of the paving, lighter
    than the halls' flags, tiling, deterministic."""

    @classmethod
    def setUpClass(cls):
        cls.spec = load(SPEC_PATH)
        cls.img = T.PAINTERS["stone_paving_court"](cls.spec)

    @staticmethod
    def lum(img):
        return paint.srgb_to_linear(quantised(img)[..., :3]) @ np.array([0.2126, 0.7152, 0.0722])

    def test_milestone_7_and_listed(self):
        self.assertEqual(T.MILESTONE, 7)
        m = self.spec["materials"]["castle_floor_court"]
        self.assertEqual((m["texture"], m["family"], m["milestone"], m["px"], m["map"], m["tile_m"]),
                         ("stone_paving_court", "castle_floor", 7, [1024, 1024], "world", 4.8))
        self.assertEqual(m["ktx2"]["mode"], "etc1s")
        self.assertEqual(self.img.shape, (1024, 1024, 4))

    def test_pale_warm_paving(self):
        lum = self.lum(self.img)
        # Section 30.6: the base at linear luminance 0.40-0.55 (0.48); the painted setts spread round it.
        self.assertTrue(0.38 <= lum.mean() <= 0.55, lum.mean())
        self.assertGreater(lum.min(), 0.05)
        r, g, b = self.img[..., :3].reshape(-1, 3).mean(axis=0)
        self.assertGreater(r, b)  # warm
        flag = T.PAINTERS["stone_flag"](self.spec)
        self.assertGreater(lum.mean(), self.lum(flag).mean())  # the outside paving reads lighter than the halls' flags

    def test_tiles_and_deterministic(self):
        for axis in (0, 1):
            wrap, inner = seam(self.img, axis)
            self.assertLess(wrap, inner * 3 + 0.02)
        self.assertTrue(np.array_equal(T.PAINTERS["stone_paving_court"](self.spec), self.img))

    def test_colours_are_the_painters_data(self):
        import inspect

        src = inspect.getsource(T)
        self.assertIn('paint.flagstones(1024, "#C4B79E", "#89806F", 2501', src)


if __name__ == "__main__":
    unittest.main()
