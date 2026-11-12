"""The inverted-hull outline layer (charlib/outline_bake.py; docs/art/
character-look.md section 4): smoothed normals across split vertices, the
flip guard for touching layers, the face's feature weights on a synthetic
frame, and on the exported files: every character primitive carries
`_OUTLINE`, its direction agrees with the shading normal, its weights stay in
0..1 and the face's inner features carry none."""

import json
import os
import struct
import unittest

import bpy
import numpy as np

from charlib import outline_bake as OB
from test_sculpt_face import frame, on_cylinder

ASSETS = os.environ.get("SHS_CHARACTER_ASSETS", os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "client", "public", "assets"))
MANIFEST = os.path.join(ASSETS, "characters", "manifest.json")


def split_cube():
    """A unit cube with every face's corners split (24 vertices, flat normals)."""
    P, F = [], []
    for axis in range(3):
        for sign in (-1.0, 1.0):
            u, v = [a for a in range(3) if a != axis]
            quad = []
            for du, dv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = [0.0, 0.0, 0.0]
                p[axis], p[u], p[v] = sign, du, dv
                quad.append(p)
            n = np.cross(np.subtract(quad[1], quad[0]), np.subtract(quad[2], quad[0]))
            if np.dot(n, [sign if a == axis else 0 for a in range(3)]) < 0:
                quad = quad[::-1]
            b = len(P)
            P.extend(quad)
            F.extend([(b, b + 1, b + 2), (b, b + 2, b + 3)])
    return np.array(P), np.array(F)


class SmoothedNormalTests(unittest.TestCase):
    def test_split_corners_share_the_diagonal(self):
        P, F = split_cube()
        N = OB.smoothed_normals(P, F)
        np.testing.assert_allclose(np.linalg.norm(N, axis=1), 1.0, atol=1e-9)
        # Every corner's three split copies point along the corner's diagonal.
        np.testing.assert_allclose(N, P / np.linalg.norm(P, axis=1, keepdims=True), atol=1e-9)

    def test_touching_layers_keep_their_own(self):
        # Two quads at the same place facing opposite ways (a double-sided sheet).
        P = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]] * 2, dtype=float)
        F = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6]])
        N = OB.smoothed_normals(P, F)
        np.testing.assert_allclose(N[:4], [[0, 0, 1]] * 4, atol=1e-9)
        np.testing.assert_allclose(N[4:], [[0, 0, -1]] * 4, atol=1e-9)

    def test_gltf_axes(self):
        np.testing.assert_allclose(OB.to_gltf_axes(np.array([[1.0, 2.0, 3.0]])), [[1.0, 3.0, -2.0]])


class FaceWeightTests(unittest.TestCase):
    def test_features_none_contour_full(self):
        fr = frame()
        lo, hi = OB.feature_box(fr)
        uv = np.array([
            fr.eye,  # an eye
            fr.eye * np.array([-1.0, 1.0]),  # the other
            fr.mouth,
            fr.nose,
            [0.0, fr.chin_z + 0.002],  # the chin
            [hi[0] + OB.FEATURE_PAD + OB.FEATURE_SOFT + 0.004, 0.5 * (lo[1] + hi[1])],  # the cheek contour
        ])
        w = OB.face_weights(fr, on_cylinder(fr, uv))
        np.testing.assert_allclose(w[:4], 0.0, atol=1e-9)
        np.testing.assert_allclose(w[4:], 1.0, atol=1e-9)
        # The box holds the brows too (above the eyes).
        self.assertGreater(hi[1], fr.eye[1] + 0.005)


def _glb(path):
    with open(path, "rb") as f:
        b = f.read()
    n = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + n])
    return j, b, 20 + n + 8


def _floats(j, b, off, i):
    a = j["accessors"][i]
    bv = j["bufferViews"][a["bufferView"]]
    comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    assert a["componentType"] == 5126
    start = off + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    return np.frombuffer(b, dtype="<f4", count=a["count"] * comps, offset=start).reshape(-1, comps)


@unittest.skipUnless(os.path.exists(MANIFEST), "no exported characters")
class ExportedOutlineTests(unittest.TestCase):
    def files(self):
        with open(MANIFEST, encoding="utf-8") as f:
            m = json.load(f)
        out = []
        for key, e in m["assets"].items():
            if e["kind"] == "body":
                out.append(e["file"])
            else:
                out.extend(e.get("files", {}).values())
                out.extend(e.get("cap_files", {}).values())
        return sorted(set(out))

    def test_every_primitive_has_the_layer(self):
        files = self.files()
        self.assertGreater(len(files), 20)
        for rel in files:
            j, b, off = _glb(os.path.join(ASSETS, rel))
            for mesh in j["meshes"]:
                for prim in mesh["primitives"]:
                    at = prim["attributes"]
                    self.assertIn(OB.ATTR, at, f"{rel} {mesh.get('name')}")
                    v = _floats(j, b, off, at[OB.ATTR])
                    w = np.linalg.norm(v, axis=1)
                    self.assertLessEqual(float(w.max()), 1.0 + 1e-4, rel)
                    # The hull's direction agrees with the shading normal (same frame and axes).
                    # Hair shades with proxy-blended normals (hair_meshy: 60 % a smoothed
                    # mass), so its geometric hull normals agree less (median about 0.79).
                    nrm = _floats(j, b, off, at["NORMAL"])
                    keep = w > 0.05
                    if keep.sum() > 10:
                        d = (v[keep] / w[keep, None] * nrm[keep]).sum(axis=1)
                        floor = 0.7 if rel.startswith("hair/") else 0.8
                        self.assertGreater(float(np.median(d)), floor, f"{rel} {mesh.get('name')}")

    def test_face_features_carry_no_line(self):
        for body in ("body_a", "body_b"):
            j, b, off = _glb(os.path.join(ASSETS, "characters", f"{body}.glb"))
            heads = [m for m in j["meshes"] if m.get("name", "").endswith("_head_lod0")]
            self.assertTrue(heads, body)
            v = _floats(j, b, off, heads[0]["primitives"][0]["attributes"][OB.ATTR])
            w = np.linalg.norm(v, axis=1)
            # Part of the face (its features) is at 0, the contour and the rest at 1.
            self.assertGreater(float((w < 0.01).mean()), 0.05, body)
            self.assertGreater(float((w > 0.99).mean()), 0.3, body)


class BakeOnObjectTests(unittest.TestCase):
    def test_hair_inner_layer_carries_no_line(self):
        # Look pass 2: vertices copying another (hair_meshy inner_twin) get width 0.
        me = bpy.data.meshes.new("outline_inner")
        P, F = split_cube()
        me.from_pydata(P.tolist(), [], F.tolist())
        me.update()
        n = len(me.vertices)
        twin = np.arange(n, dtype=np.int32)
        twin[n // 2:] = 0
        tw = me.attributes.new("inner_twin", "INT", "POINT")
        tw.data.foreach_set("value", twin)
        ob = bpy.data.objects.new("outline_inner", me)
        try:
            OB.bake([ob])
            w = np.linalg.norm(OB.read(ob), axis=1)
            np.testing.assert_allclose(w[: n // 2], 1.0, atol=1e-5)
            np.testing.assert_allclose(w[n // 2:], 0.0, atol=1e-6)
        finally:
            bpy.data.objects.remove(ob)
            bpy.data.meshes.remove(me)

    def test_bake_writes_unit_vectors(self):
        me = bpy.data.meshes.new("outline_test")
        P, F = split_cube()
        me.from_pydata(P.tolist(), [], F.tolist())
        me.update()
        ob = bpy.data.objects.new("outline_test", me)
        try:
            self.assertEqual(OB.bake([ob]), 24)
            v = OB.read(ob)
            np.testing.assert_allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)
            # glTF axes: Blender +Z is glTF +Y.
            top = P[:, 2] > 0
            self.assertTrue((v[top, 1] > 0).all())
        finally:
            bpy.data.objects.remove(ob)
            bpy.data.meshes.remove(me)
