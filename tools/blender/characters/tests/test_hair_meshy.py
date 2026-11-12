"""The hairstyles made from the Meshy guides (charlib/hair_meshy.py,
docs/art/character-look.md section 2): all twelve shipped styles.

Data tests always run. The source-mesh tests need the prepared meshes
(`art/characters/hair_meshy/<asset>_<body>.npz`, or `SHS_HAIR_MESHY=<dir>`);
the build tests also need the body's saved build (`art/characters/<body>.blend`
or `SHS_CHARACTER_BLENDS=<dir>`). They skip without them.
"""

import os
import unittest

import numpy as np

from charlib import content
from charlib import hair as HR
from charlib import hair_meshy as HM
from charlib import hair_styles as HS
from charlib import pipeline as PL

BLENDS = os.environ.get("SHS_CHARACTER_BLENDS", os.path.join(content.REPO, "art", "characters"))
SHIPPED = [s for s in content.creator()["hair_styles"] if not s.get("retired")]
BONES = {f"hair.back.{i:02d}" for i in range(1, 5)} | {f"hair.side.{s}.{i:02d}" for s in "LR" for i in range(1, 4)} | {
    f"hair.front.{i:02d}" for i in range(1, 3)}


class MeshyDataTests(unittest.TestCase):
    def test_every_shipped_style_has_meshy_data(self):
        for s in SHIPPED:
            name = s["asset"].split("/")[-1]
            st = HM.STYLES[name]
            self.assertEqual(st["body"], s["body"], name)
            self.assertEqual(st["length"], HS.LENGTH[name], name)
            self.assertIn(st["source"], ("meshy", "locks"), name)
            self.assertTrue(st["guide"].startswith("g_") and st["guide"].endswith(".glb"), name)
        self.assertEqual(sorted(HM.STYLES), sorted(s["asset"].split("/")[-1] for s in SHIPPED))

    def test_guides(self):
        """Every guide of a style names its file, concept and fit; the
        default guide is one of them; a style with a guide merges the
        style's shared data under the guide's own."""
        self.assertEqual(sorted(HM.GUIDES), sorted(HM.STYLES))
        for name, st in HM.STYLES.items():
            self.assertIn(st["guide_id"], HM.GUIDES[name], name)
            for gid, g in HM.GUIDES[name].items():
                self.assertTrue(g["guide"].startswith("g_") and g["guide"].endswith(".glb"), (name, gid))
                self.assertTrue(g["concept"].startswith("c_"), (name, gid))
                self.assertEqual(sorted(g["concept_order"]), [0, 1, 2], (name, gid))
                self.assertGreater(g["registration"]["scale"], 0.0, (name, gid))
                for k in ("body", "length", "chains", "source"):
                    self.assertNotIn(k, g, (name, gid, k))
                full = HM.style(name, gid)
                self.assertEqual(full["guide"], g["guide"])
                self.assertEqual(full["guide_id"], gid)
                self.assertEqual(full["chains"], st["chains"])
                meta = {"source": g["guide"]}
                self.assertEqual(HM.source_style(name, meta)["guide_id"], gid)
                self.assertEqual(HM.source_style(name, {"guide_id": gid})["guide"], g["guide"])

    def test_budgets(self):
        """character-look.md section 2: per length class, shrinking per LOD,
        never below the lock builder's; LOD0 about 20-25 k with the inner
        layer; the dressed tiers rise by exactly the hair's increase."""
        for length, b in HM.BUDGETS.items():
            self.assertEqual(len(b), 4)
            self.assertTrue(all(x > y for x, y in zip(b, b[1:])), length)
            self.assertTrue(all(x >= y for x, y in zip(b, HR.BUDGETS[length])), length)
            self.assertTrue(20000 <= b[0] <= 25000, length)
        self.assertEqual(HM.BUDGETS["short"][0], 20000)
        self.assertEqual(HM.BUDGETS["medium"][0], 22000)
        self.assertEqual(HM.BUDGETS["long"][0], 24000)
        self.assertEqual(PL.DRESSED_MAX, (52000, 17500, 4000, 1350))
        self.assertEqual(PL.LOD_BUDGETS["hair"], HM.BUDGETS["long"])
        pass1_tiers, pass1_long = (40000, 13500, 4000, 1350), (12000, 4000, 1300, 450)
        self.assertEqual(PL.DRESSED_MAX, tuple(t + n - o for t, n, o in zip(pass1_tiers, HM.BUDGETS["long"], pass1_long)))

    def test_chains_use_known_bones(self):
        for name, st in HM.STYLES.items():
            bones = {b for c in st["chains"] for b in HR.chain_bone_names(c)}
            self.assertLessEqual(len(bones), 12, name)
            self.assertTrue(bones <= BONES, name)
            if st["length"] == "long":
                # Long hair sways: down the back, or down its tails (twin
                # tails, a braid over the shoulder).
                self.assertTrue("back" in st["chains"] or any(c.get("along") == "tail" for c in st["chains"].values()),
                                name)

    def test_meshy_only_styles(self):
        """The second set (ids 20-24) have no locks: shipped, Meshy data, never in the
        lock builder; a missing source mesh is an error, not a fallback."""
        names = {s["asset"].split("/")[-1] for s in SHIPPED}
        self.assertEqual(len(HM.MESHY_ONLY), 5)
        self.assertTrue(set(HM.MESHY_ONLY) <= names)
        self.assertTrue(set(HM.MESHY_ONLY) <= set(HM.STYLES))
        self.assertFalse(set(HM.MESHY_ONLY) & set(HS.STYLES))
        self.assertEqual(names, set(HS.STYLES) | set(HM.MESHY_ONLY))
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("SHS_HAIR_MESHY")
            os.environ["SHS_HAIR_MESHY"] = d
            try:
                self.assertFalse(HM.use_meshy("crew_cut", "body_a", "locks"))
                with self.assertRaises(FileNotFoundError):
                    HM.build(type("B", (), {"id": "body_a"})(), {"asset": "hair/crew_cut", "id": 22}, d, {"assets": {}})
            finally:
                if old is None:
                    del os.environ["SHS_HAIR_MESHY"]
                else:
                    os.environ["SHS_HAIR_MESHY"] = old

    def test_source_round_trip(self):
        import tempfile

        V = np.random.default_rng(1).normal(size=(10, 3))
        F = np.array([[0, 1, 2], [2, 3, 4]])
        N = V / np.linalg.norm(V, axis=1, keepdims=True)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x_body_a.npz")
            HM.save_source(p, V, F, N, np.zeros(10), np.linspace(0, 1, 10), {"source": "g_x.glb"})
            src = HM.load_source(p)
        self.assertEqual(src["F"].tolist(), F.tolist())
        self.assertTrue(np.allclose(src["V"], V, atol=1e-6))
        self.assertEqual(src["meta"]["source"], "g_x.glb")


def _source(name, body):
    p = HM.source_path(name, body)
    return p if os.path.exists(p) else None


def _blend(body):
    p = os.path.join(BLENDS, f"{body}.blend")
    return p if os.path.exists(p) else None


class SourceMeshTests(unittest.TestCase):
    """The prepared meshes (character-look.md section 2.2 format)."""

    def test_sources(self):
        found = 0
        for name, st in HM.STYLES.items():
            p = _source(name, st["body"])
            if p is None:
                continue
            found += 1
            src = HM.load_source(p)
            st = HM.source_style(name, src["meta"])
            V, F, N = src["V"], src["F"], src["N"]
            self.assertLessEqual(len(F), HM.SOURCE_MAX_TRIS, name)
            # More than the shell takes of LOD0 (the inner layer doubles it).
            self.assertGreater(len(F), HM.BUDGETS[st["length"]][0] // 2, name)
            self.assertTrue(np.isfinite(V).all(), name)
            self.assertTrue((F >= 0).all() and (F < len(V)).all(), name)
            self.assertTrue(np.allclose(np.linalg.norm(N, axis=1), 1.0, atol=1e-3), name)
            self.assertTrue((src["part"] < len(HM.PARTS)).all(), name)
            r = src["root_to_tip"]
            self.assertTrue(((r >= 0) & (r <= 1)).all(), name)
            for key in ("source", "sha256", "registration", "tool_version"):
                self.assertIn(key, src["meta"], name)
            self.assertEqual(src["meta"]["source"], st["guide"], name)
            tail = src["part"] == HM.TAIL
            x = V[:, 0]
            if name == "twin_tails":
                # Two tails, one each side, about the same size.
                n_l, n_r = int((tail & (x > 0)).sum()), int((tail & (x < 0)).sum())
                self.assertGreater(min(n_l, n_r), 0.6 * max(n_l, n_r), name)
            if name == "side_braid":
                # The plait over the left shoulder, tied at its end,
                # starting at the base of the skull just left of centre
                # (round 4), with the head's hair from the right side of the
                # back running into its root.
                plait = tail & (src["root_to_tip"] > 0.01)
                self.assertGreater(int(plait.sum()), 1000, name)
                self.assertGreater(float((x[plait] > 0).mean()), 0.9, name)
                root = V[plait][np.argmin(src["root_to_tip"][plait])]
                # (The body stands on x = 0; 1 head cm is about 12 mm.)
                self.assertTrue(-0.006 < root[0] < 0.05, f"{name} root x {root[0]:.3f} m")
                near = (~tail) & (x < root[0] - 0.01) & (np.linalg.norm(V - root, axis=1) < 0.04)
                self.assertTrue(near.any(), name)
                self.assertTrue((src["part"] == HM.TIE).any(), name)
            # Within a head and a half of the body's head (no stray pieces).
            ext = V.max(axis=0) - V.min(axis=0)
            self.assertLess(float(ext[0]), 0.5, name)
            if name == "ponytail_locks":
                self.assertTrue((src["part"] == HM.TAIL).any() and (src["part"] == HM.TIE).any())
        if not found:
            self.skipTest(f"no source meshes in {HM.source_dir()}")


class _Built:
    cache = {}

    @classmethod
    def get(cls, body):
        if body not in cls.cache:
            from charlib import hair_frame as HF

            bb = HF.load_body(_blend(body))
            fr = HF.HeadFrame(bb)
            occ = [(fr.parts[r][0], fr.parts[r][2]) for r in ("head", "scalp", "neck", "torso") if r in fr.parts]
            out = {}
            for name, st in HM.STYLES.items():
                if st["body"] != body or _source(name, body) is None:
                    continue
                src0 = HM.load_source(HM.source_path(name, body))
                st = HM.source_style(name, src0["meta"])
                # As build_style: the clean-up first (character-look.md section 2).
                src0 = HM.clean_source(fr, src0, st)
                for cap in (False, True):
                    src = HM.cap_source(fr, src0, st) if cap else src0
                    lods = HM.build_lods(fr, src, st, lambda th: fr.scalp_border(th) + 3.0, cap, occ)
                    out[(name, cap)] = (st, lods)
            cls.cache[body] = (bb, fr, out)
        return cls.cache[body]


def _main(lod):
    """The shell and inner cap of a LOD (its own vertices, not the inner
    layer's copies): (V, part, rtt, mask, faces among them)."""
    V, F, _, _, _, rtt, part, twin = lod
    main = twin == np.arange(len(V))
    return V, part, rtt, main, F[main[F].all(axis=1)]


class _BuildMixin:
    body = ""

    @classmethod
    def setUpClass(cls):
        if _blend(cls.body) is None or not any(_source(n, cls.body) for n, s in HM.STYLES.items() if s["body"] == cls.body):
            raise unittest.SkipTest(f"no {cls.body}.blend or source meshes")
        cls.bb, cls.fr, cls.built = _Built.get(cls.body)

    def test_budgets_and_lods(self):
        for (name, cap), (st, lods) in self.built.items():
            tris = [len(l[1]) for l in lods]
            for lod, (t, b) in enumerate(zip(tris, HM.BUDGETS[st["length"]])):
                self.assertLessEqual(t, b, f"{name} cap={cap} lod{lod}")
            self.assertTrue(all(x > y for x, y in zip(tris, tris[1:])), f"{name} {tris}")
            # Most of LOD0's budget is used (the clean shapes need it); the
            # hat variant has the hair its hats cover cut away.
            if not cap:
                self.assertGreater(tris[0], 0.9 * HM.BUDGETS[st["length"]][0], name)

    def test_inner_layer(self):
        """character-look.md section 2: LOD0 and LOD1 carry the inner layer (every
        shell and inner-cap vertex again, at most INNER_DEPTH in, its faces
        flipped, darker, no ring); LOD2 and LOD3 do not."""
        for (name, cap), (st, lods) in self.built.items():
            for lod, (V, F, kinds, N, sh, rtt, part, twin) in enumerate(lods):
                own = twin == np.arange(len(V))
                if lod >= HM.INNER_LODS:
                    self.assertTrue(own.all(), f"{name} lod{lod}")
                    continue
                n = int(own.sum())
                self.assertEqual(len(V), 2 * n, f"{name} lod{lod}")
                self.assertTrue(own[:n].all() and (twin[n:] == np.arange(n)).all(), f"{name} lod{lod}")
                self.assertEqual(len(F) % 2, 0)
                h = len(F) // 2
                self.assertTrue(np.array_equal(F[h:] - n, F[:h][:, [0, 2, 1]]), f"{name} lod{lod}: inner faces flipped")
                d = np.linalg.norm(V[n:] - V[:n], axis=1)
                self.assertLessEqual(float(d.max()), HM.INNER_DEPTH + 0.004, f"{name} lod{lod}")
                self.assertTrue(np.allclose(N[n:], N[:n], atol=1e-6), f"{name} lod{lod}")
                self.assertTrue((sh[n:, 0] <= HM.INNER_SHADE + 1e-6).all(), f"{name} lod{lod}")
                self.assertTrue(np.allclose(sh[n:, 2], HM.INNER_Z), f"{name} lod{lod}")

    def test_shade_and_normals(self):
        for (name, cap), (st, lods) in self.built.items():
            for V, F, kinds, N, sh, rtt, part, twin in lods:
                self.assertEqual(sh.shape, (len(V), 4))
                self.assertTrue((sh[:, 0] >= HR.SHADE_FLOOR - 1e-6).all() and (sh[:, 0] <= 1 + 1e-6).all(), name)
                self.assertTrue(((sh[:, 1] >= 0) & (sh[:, 1] <= 1)).all(), name)
                # The band phase on the open hair; the inner surfaces (inner
                # cap, inner layer) carry INNER_Z (no ring).
                ring = np.abs(sh[:, 2] - 0.5) <= HR.BAND_PHASE + 1e-6
                self.assertTrue((ring | np.isclose(sh[:, 2], HM.INNER_Z)).all(), name)
                if st.get("no_ring"):
                    # A matte style: no angel ring anywhere.
                    self.assertFalse(ring.any(), name)
                else:
                    self.assertGreater(float(ring.mean()), 0.3, name)
                self.assertTrue(np.allclose(sh[:, 3], HR.OUTLINE_W), name)
                self.assertTrue(np.allclose(np.linalg.norm(N, axis=1), 1.0, atol=1e-3), name)
                self.assertEqual(len(kinds), len(F))

    def test_weights_and_chains(self):
        for (name, cap), (st, lods) in self.built.items():
            V, part, rtt, main, _ = _main(lods[0])
            joints = HM.chain_joints(self.fr, st["chains"], V[main], part[main], rtt[main])
            self.assertEqual(sorted(joints), sorted(st["chains"]), name)
            bones = {b for c in joints for b in HR.chain_bone_names(c)}
            follow = set(HM.FOLLOW_BONES) if st.get("follow") else set()
            for V, F, _, _, _, rtt, part, twin in lods:
                ws = HM.lod_weights(self.fr, st["chains"], joints, V, F, part, rtt, twin, st.get("follow"))
                self.assertEqual(len(ws), len(V))
                for w in ws:
                    self.assertLessEqual(len(w), 4)
                    self.assertAlmostEqual(sum(w.values()), 1.0, places=5)
                    self.assertTrue(set(w) <= bones | {HR.HEAD_BONE} | follow, name)
                # The inner layer sways with its shell exactly.
                for i, t in enumerate(twin):
                    if t != i:
                        self.assertEqual(ws[i], ws[t], name)

    def test_follow_rests_on_the_body(self):
        """Styles that rest on the body (`follow`): the hair down the chest
        and back moves with the body under it; the hair on the head stays
        on the head."""
        seen = 0
        for (name, cap), (st, lods) in self.built.items():
            if not st.get("follow") or cap:
                continue
            seen += 1
            V, part, rtt, main, _ = _main(lods[0])
            V0, F0, _, _, _, rtt0, part0, twin0 = lods[0]
            joints = HM.chain_joints(self.fr, st["chains"], V[main], part[main], rtt[main])
            ws = HM.lod_weights(self.fr, st["chains"], joints, V0, F0, part0, rtt0, twin0, st["follow"])
            body = np.array([sum(v for k, v in w.items() if k in HM.FOLLOW_BONES) for w in ws])
            low = V0[:, 2] < HM.shoulder_line(self.fr) - 0.06
            high = V0[:, 2] > self.fr.chin_z + 0.01
            if low.any():
                # (Locks ending at the collarbones have nothing this low.)
                self.assertGreater(float(np.median(body[low])), 0.6, name)
            mid = V0[:, 2] < HM.shoulder_line(self.fr)
            self.assertTrue(mid.any(), name)
            self.assertGreater(float(body[mid].max()), 0.3, name)
            self.assertLess(float(body[high].max()), 0.05, name)
        if not seen:
            self.skipTest("no built style follows the body")

    def test_cap_variant_keeps_the_full_hair(self):
        """Round 4 of the hat rule (2026-11-07): the hat variant is the
        style at its full shape (tails aside: a ponytail's tie moves to the
        nape, twin tails' roots press under the band); each hat drops what
        its crown covers and is fitted over the rest (the next test)."""
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree

        for (name, cap), (st, lods) in self.built.items():
            if not cap:
                continue
            V, F, _, _, _, rtt, part, twin = lods[0]
            main = twin == np.arange(len(V))
            shell = main & ~lods.scalp[0] & (part != HM.TAIL) & (part != HM.TIE)
            plain = self.built[(name, False)][1][0]
            tree = BVHTree.FromPolygons(plain[0].tolist(), plain[1].tolist(), all_triangles=True)
            d = np.array([tree.find_nearest(Vector(tuple(p)), 0.1)[3] for p in V[shell]])
            self.assertLess(float(np.percentile(d, 95)), 0.004, name)
            # The inner cap is marked (no hat drops it).
            self.assertGreater(int(lods.scalp[0].sum()), 50, name)

    def test_hat_fits(self):
        """Per hat a cover line and a scale: with the hair the crown covers
        dropped and the hat scaled about the head centre, the hair left
        under the crown is inside the hat (as check_hair_hats.py counts),
        where the scale is capped at HAT_FIT_MAX once the hair under the
        crown is pressed in to the hat (hat_press)."""
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree

        from charlib.hair_frame import band_phi

        hats = HM.hat_meshes()
        if not hats:
            self.skipTest("no hats in the body file")
        n = int(round(360.0 / HM.HAT_COVER_STEP))
        for (name, cap), (st, lods) in self.built.items():
            if not cap or name not in ("crew_cut", "shaggy_layers", "long_bangs", "side_braid"):
                continue
            V, F = lods[0][0], lods[0][1]
            fits = HM.hat_fits(self.fr, V, F, lods.scalp[0])
            self.assertEqual(len(fits["centre"]), 3)
            for asset, hv, ht in hats:
                fit = fits["hats"][asset]
                self.assertEqual(len(fit["cover"]), n, asset)
                self.assertTrue(1.0 <= fit["scale"] <= HM.HAT_FIT_MAX + 1e-6, f"{name} {asset} {fit['scale']}")
                line = np.array(fit["cover"])
                if fit["scale"] >= HM.HAT_FIT_MAX - 1e-6:
                    # Capped: the hair under the crown pressed in instead.
                    self.assertIn("limit", fit, f"{name} {asset}")
                    V = HM.hat_press(self.fr, lods[0][0], F, line, np.array(fit["limit"]), lods.scalp[0])
                else:
                    V = lods[0][0]
                kf = HM.hat_cut_faces(self.fr, V, F, line, lods.scalp[0])
                used = np.zeros(len(V), dtype=bool)
                used[F[kf].ravel()] = True
                phi, theta, r = self.fr.polar(V)
                band = np.array([band_phi(t) for t in theta])
                under = used & ~lods.scalp[0] & (phi < HM.cover_at(line, theta)) & (phi < band + HM.HAT_FIT_BELOW)
                c = self.fr.centre
                hs = c + (hv - c) * fit["scale"]
                tree = BVHTree.FromPolygons(hs.tolist(), ht.tolist(), all_triangles=True)
                out = 0
                for i in np.nonzero(under)[0]:
                    dd = (V[i] - c) / r[i]
                    hit = tree.ray_cast(Vector(tuple(c)), Vector(tuple(dd)), 0.4)
                    out += hit[0] is not None and hit[3] < r[i] - 0.001
                self.assertEqual(out, 0, f"{name} {asset}")
            # Never more than HAT_FIT_MAX; short hair needs little.
            self.assertLessEqual(max(f["scale"] for f in fits["hats"].values()), HM.HAT_FIT_MAX + 1e-6)

    def test_scalp_covered(self):
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree

        scalp = self.fr.parts["scalp"][0]
        c = Vector(tuple(self.fr.centre))
        for (name, cap), (st, lods) in self.built.items():
            V, _, _, _, F = _main(lods[0])
            tree = BVHTree.FromPolygons(V.tolist(), F.tolist(), all_triangles=True)
            bare = 0
            for p in scalp:
                d = Vector(tuple(p)) - c
                bare += tree.ray_cast(c + d * 0.98, d.normalized(), 0.3)[0] is None
            self.assertEqual(bare, 0, f"{name} cap={cap}: {bare} scalp vertices uncovered")

    def test_fringe_hem(self):
        for (name, cap), (st, lods) in self.built.items():
            V, part, _, main, _ = _main(lods[0])
            hem = HM.fringe_hem(self.fr, V[main], part[main])
            if st.get("no_fringe"):
                # Combed back or cropped: nothing over the forehead.
                self.assertEqual(hem, [], name)
                continue
            self.assertTrue(8 <= len(hem) <= 24, f"{name}: {len(hem)}")
            xs = [p[0] for p in hem]
            self.assertEqual(xs, sorted(xs))

    def test_style_edits(self):
        """character-look.md section 2: F2 keeps no side strand behind the ears;
        M3's side layers end at most `span` below `u0`; F1's temple strand
        box is empty."""
        for (name, cap), (st, lods) in self.built.items():
            if cap:
                continue
            V, part, _, main, _ = _main(lods[0])
            H = HM.head_coords(self.fr, V[main])
            p = part[main]
            for rule in st.get("drop", []):
                hit = HM.in_box(H, rule["box"]) & np.isin(p, rule["parts"])
                self.assertEqual(int(hit.sum()), 0, f"{name}: {rule}")
            for rule in st.get("shorten", []):
                low = np.isin(p, rule["parts"])
                # A centimetre of slack: the LOD's push out of the body.
                self.assertGreaterEqual(float(H[low, 2].min()), rule["u0"] - rule["span"] - 1.0, name)

    def test_clipped_band(self):
        """character-look.md section 2: a style's `clip` band lies on the skull
        (between its bottom and its top height plus a comb ridge) at LOD0-2,
        none at LOD3; it is hair like the rest (the hair cell, full hair
        colour: no face kind of its own)."""
        for (name, cap), (st, lods) in self.built.items():
            for lod in lods:
                self.assertTrue(set(lod[2]) <= {"hair", "tie"}, name)
            spec = st.get("clip")
            if not spec or cap:
                continue
            src = HM.clean_source(self.fr, HM.load_source(HM.source_path(name, self.body)), st)
            lines = HM.clip_lines(self.fr, src, spec)
            bands = [HM.clip_band_mesh(self.fr, lines, spec, lod) for lod in range(4)]
            self.assertTrue(all(b is not None for b in bands[:3]) and bands[3] is None, name)
            for V, F, valley, down in bands[:3]:
                phi, theta, r = self.fr.polar(V)
                h = (r - self.fr.skull_r(phi, theta)) * 1000
                self.assertLessEqual(float(h.max()), spec.get("h_top_mm", 4.0) + spec.get("ridge_mm", 0.5) + 1e-3, name)
                self.assertGreaterEqual(float(h.min()), spec.get("h_bot_mm", 2.5) - 1e-3, name)

    def test_hugged_hem(self):
        """character-look.md section 2: a hugged short style's shell meets the
        scalp at its hem (no helmet standing off the head)."""
        for (name, cap), (st, lods) in self.built.items():
            if not st.get("hug") or cap:
                continue
            V, F, kinds, sh = lods[0][0], lods[0][1], lods[0][2], lods[0][4]
            twin = lods[0][7]
            main = twin == np.arange(len(V))
            # The shell's own faces: not the inner layer's, not the cap's or
            # the band's (no ring phase on those).
            ring = np.abs(sh[:, 2] - 0.5) <= HR.BAND_PHASE + 1e-6
            Fs = F[(main & ring)[F].all(axis=1)]
            loops = [lp for lp in HM.boundary_loops(Fs) if len(lp) > 40]
            self.assertTrue(loops, name)
            hem = np.concatenate(loops)
            phi, theta, r = self.fr.polar(V[hem])
            h = (r - self.fr.skull_r(phi, theta)) * 1000
            self.assertLessEqual(float(np.median(h)), 10.0, name)

    def test_deterministic(self):
        (name, cap), (st, lods) = next(iter(self.built.items()))
        src = HM.load_source(HM.source_path(name, self.body))
        V1, F1 = HM.decimate(src["V"], src["F"], 3000)
        V2, F2 = HM.decimate(src["V"], src["F"], 3000)
        self.assertTrue(np.array_equal(V1, V2))
        self.assertTrue(np.array_equal(F1, F2))


def _grid(nx: int, ny: int, step: float = 0.001):
    """A flat grid in the XY plane (normals +Z), nx x ny vertices."""
    xs, ys = np.meshgrid(np.arange(nx) * step, np.arange(ny) * step)
    V = np.stack([xs.ravel(), ys.ravel(), np.zeros(nx * ny)], axis=1)
    F = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i
            F += [(a, a + 1, a + nx + 1), (a, a + nx + 1, a + nx)]
    return V, np.array(F, dtype=np.int64)


def _src(V, F):
    return {"V": V, "F": F, "N": np.tile([0.0, 0.0, 1.0], (len(V), 1)), "part": np.zeros(len(V), dtype=np.uint8),
            "root_to_tip": np.zeros(len(V)), "meta": {}}


class CleanUpTests(unittest.TestCase):
    """The build's source clean-up (character-look.md section 2) on small meshes."""

    def test_boundary_loops(self):
        V, F = _grid(4, 4)
        loops = HM.boundary_loops(F)
        self.assertEqual(len(loops), 1)
        self.assertEqual(sorted(loops[0].tolist()), [0, 1, 2, 3, 4, 7, 8, 11, 12, 13, 14, 15])

    def test_fill_holes(self):
        V, F = _grid(6, 6, 0.002)
        # Cut the two triangles of the middle square: a hole of perimeter 8 mm.
        a = 2 * 6 + 2
        keep = ~((F[:, 0] == a) & ((F[:, 1] == a + 1) | (F[:, 1] == a + 7)))
        src = _src(V, F[keep])
        self.assertEqual(len(HM.boundary_loops(src["F"])), 2)
        out = HM.fill_holes(src)
        self.assertEqual(len(HM.boundary_loops(out["F"])), 1)
        self.assertEqual(len(out["V"]), len(V) + 1)
        # Same winding as the sheet: the fan's normals point +Z.
        from charlib import hair_meshops as MO

        self.assertTrue((MO.face_normals(out["V"], out["F"])[:, 2] > 0.99).all())

    def test_drop_pieces(self):
        V1, F1 = _grid(20, 20)  # 3.6 cm^2
        V2, F2 = _grid(3, 3)  # 4 mm^2: a flyaway
        V = np.vstack([V1, V2 + [0.05, 0.0, 0.0]])
        F = np.vstack([F1, F2 + len(V1)])
        out = HM.drop_pieces(_src(V, F))
        self.assertEqual(len(out["F"]), len(F1))
        self.assertEqual(len(out["V"]), len(V1))

    def test_widen_strands(self):
        # A strip 1 mm wide and 3 cm long: widened toward STRAND_MIN_W.
        V, F = _grid(31, 2)
        out, n = HM.widen_strands(_src(V, F))
        self.assertGreater(n, 0)
        mid = np.abs(out["V"][:, 0] - 0.015) < 0.004
        w = out["V"][mid, 1].max() - out["V"][mid, 1].min()
        self.assertGreater(w, 0.001 + 1e-4)
        self.assertLessEqual(w, 0.001 + 2 * HM.WIDEN_MAX + 1e-6)
        # A wide sheet is left alone.
        V, F = _grid(10, 10, 0.002)
        out, n = HM.widen_strands(_src(V, F))
        self.assertEqual(n, 0)


class MaleMeshyTests(_BuildMixin, unittest.TestCase):
    body = "body_a"


class FemaleMeshyTests(_BuildMixin, unittest.TestCase):
    body = "body_b"


if __name__ == "__main__":
    unittest.main()
