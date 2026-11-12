"""The painted face texture of a body (docs/art/character-look.md section 3).

Each body gets its own face texture pair, sampled in the one character
material (so a character stays one draw):

* `characters/face_<body>.png`, 1024 x 1024 RGBA, sRGB: RGB the painted face
  (white or a soft multiplier on skin: blush, lip tint, nose shade, lid
  crease, mouth line; the features in their own colours, greyscale where the
  creator tints them), A the skin weight (1 = multiplied by the skin tone);
* `characters/face_mask_<body>.png`, 512 x 512 RGBA, linear: R the eye-colour
  weight (iris), G the hair-colour weight (brows, part of the lashes), B the
  face-shadow threshold (`face_shadow.py`), A the flat weight (eye white,
  iris, pupil, highlights, lashes, mouth line: always lit, no rim, no line)
  and, above it, the glow: A = FLAT_LEVEL x flat + (1 - FLAT_LEVEL) x glow
  (look pass 2, docs/art/character-look.md section 3; the glow only where flat is
  1: the iris, strongest low down, and the catchlights). The client decodes
  flat = min(1, A / FLAT_LEVEL), glow = (A - FLAT_LEVEL) / (1 - FLAT_LEVEL)
  (toon/material.ts `faceFlatGlow`).

Lines on the skin (mouth line, nose shade, lid crease) are multipliers with
R < 1; the soft tints (blush, lip) keep R = 1. The client deepens the lines
on dark skin from that R (toon/material.ts `faceLinePaint`), so they read on
every skin tone.

The client's face albedo (toon/material.ts, the `face` region):

    albedo = c.rgb * mix(1, skin, c.a) * mix(1, eye, m.r) * mix(1, hair, m.g)

**UVs (`FaceMap`)**: the whole `head` primitive, every LOD, maps into the
face texture by a cylindrical projection round the head's vertical axis (the
face frame's, `mh_face.FaceFrame.uv`): u from the angle round the axis,
warped so the face front gets most of the texels (`tanh`), symmetric about
u = 0.5 (+X, the character's left, is u > 0.5), saturating at +-150 degrees
(the back of the head, under the hair, is plain skin at the texture's left
and right edges, so the seam there is invisible); v from the height, warped
so the band from the brows to the mouth gets about twice the density.
The UVs are set per LOD from the vertex positions (`set_head_uvs`), so every
LOD keeps the whole face.

**Painting** happens in 2D in the face projection (metres, `FaceFrame.uv`
coordinates) at twice the texture size and is box-filtered down, so every
edge is anti-aliased in the texture itself. The eye opening and the lash
band use the very polygons cut into the mesh (`mh_face.eye_polys`), so the
blink key (which collapses the opening onto the lower lid and carries the
lash band down rigidly) closes the painted eye exactly.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from dataclasses import dataclass

import numpy as np

from . import mh_face as MF

COLOUR_SIZE = 1024
MASK_SIZE = 512
SUPER = 2  # painted at COLOUR_SIZE * SUPER, box-filtered down
A_MAX = 150.0  # degrees round the axis where u saturates (the back of the head)
A_SOFT = 45.0  # tanh scale of the horizontal warp (degrees)
V_GAIN = 1.0  # extra vertical density in the feature band (1 = twice at its centre)
MARGIN = 0.002  # metres above and below the head primitive's extent

# Built faces of this run (body id -> (FaceFrame, FaceMap)), filled by
# mh_body.finish_lods and read by content.export_body.
FACE_BUILDS: dict[str, tuple] = {}


# ----------------------------------------------------------------------------
# The projection
# ----------------------------------------------------------------------------


@dataclass
class FaceMap:
    """Head positions (Blender world, metres) -> face texture UVs (Blender
    convention: v = 0 at the bottom; glTF stores 1 - v)."""

    axis_y: float
    R0: float
    z0: float
    z1: float
    zc: float
    zw: float

    @staticmethod
    def for_frame(fr, z_lo: float, z_hi: float) -> "FaceMap":
        """The map of a face frame over a head primitive from `z_lo` to `z_hi`."""
        ev, mv = float(fr.eye[1]), float(fr.mouth[1])
        zc = 0.5 * (ev + mv) + 0.15 * (ev - mv)
        zw = 0.75 * (ev - mv) + 0.008
        return FaceMap(float(fr.axis_y), float(fr.R0), float(z_lo) - MARGIN, float(z_hi) + MARGIN, zc, zw)

    # Horizontal: angle (degrees, + toward +X) <-> u.
    def u_of_angle(self, a: np.ndarray) -> np.ndarray:
        x = np.tanh(np.clip(a, -A_MAX, A_MAX) / A_SOFT) / math.tanh(A_MAX / A_SOFT)
        return 0.5 + 0.5 * x

    def angle_of_u(self, u: np.ndarray) -> np.ndarray:
        x = np.clip(2.0 * np.asarray(u, dtype=np.float64) - 1.0, -0.999999, 0.999999) * math.tanh(A_MAX / A_SOFT)
        return A_SOFT * np.arctanh(x)

    # Vertical: height <-> v.
    def _g(self, z):
        return z + V_GAIN * self.zw * np.tanh((z - self.zc) / self.zw)

    def v_of_z(self, z: np.ndarray) -> np.ndarray:
        g0, g1 = self._g(self.z0), self._g(self.z1)
        return np.clip((self._g(np.asarray(z, dtype=np.float64)) - g0) / (g1 - g0), 0.0, 1.0)

    def z_of_v(self, v: np.ndarray) -> np.ndarray:
        zz = np.linspace(self.z0, self.z1, 4096)
        return np.interp(np.asarray(v, dtype=np.float64), self.v_of_z(zz), zz)

    def angle(self, P: np.ndarray) -> np.ndarray:
        return np.degrees(np.arctan2(P[:, 0], self.axis_y - P[:, 1]))

    def uv(self, P: np.ndarray) -> np.ndarray:
        """UVs of points (no seam handling: see `corner_uvs`)."""
        return np.stack([self.u_of_angle(self.angle(P)), self.v_of_z(P[:, 2])], axis=1)

    def corner_uvs(self, P: np.ndarray, loops_of_face: list[np.ndarray]) -> np.ndarray:
        """UV per face corner: a face straddling the back seam (+-180 degrees)
        is unwrapped onto one side first, so it lands at one edge of the
        texture (plain skin) instead of across it."""
        ang = self.angle(P)
        v = self.v_of_z(P[:, 2])
        out = []
        for f in loops_of_face:
            a = ang[f]
            if a.max() - a.min() > 180.0:
                a = np.where(a < 0, a + 360.0, a) if a.mean() > 0 else np.where(a > 0, a - 360.0, a)
            out.append(np.stack([self.u_of_angle(a), v[f]], axis=1))
        return out

    def face_coords(self, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Face-projection coordinates (`FaceFrame.uv`: R0 x angle in
        radians, height) of texture coordinates."""
        return self.R0 * np.radians(self.angle_of_u(u)), self.z_of_v(v)

    def to_json(self) -> dict:
        return {"axis_y": self.axis_y, "R0": self.R0, "z0": self.z0, "z1": self.z1, "zc": self.zc, "zw": self.zw,
                "a_max": A_MAX, "a_soft": A_SOFT, "v_gain": V_GAIN}


def set_head_uvs(obj, fmap: FaceMap) -> None:
    """Every corner of the head object's UV layer from the face projection."""
    me = obj.data
    n = len(me.vertices)
    co = np.zeros(n * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(obj.matrix_world)
    P = co @ mw[:3, :3].T + mw[:3, 3]
    vi = np.zeros(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("vertex_index", vi)
    starts = np.zeros(len(me.polygons), dtype=np.int64)
    totals = np.zeros(len(me.polygons), dtype=np.int64)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", totals)
    faces = [vi[s:s + t] for s, t in zip(starts, totals)]
    per = fmap.corner_uvs(P, faces)
    uv = np.zeros((len(me.loops), 2), dtype=np.float32)
    for s, t, q in zip(starts, totals, per):
        uv[s:s + t] = q
    layer = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
    layer.data.foreach_set("uv", uv.ravel())
    obj["face_texture"] = True


def head_mesh(obj) -> dict:
    """The head object's corner data for the bakes: positions (world),
    UVs and corner normals per loop triangle."""
    me = obj.data
    me.calc_loop_triangles()
    n = len(me.vertices)
    co = np.zeros(n * 3)
    me.vertices.foreach_get("co", co)
    mw = np.array(obj.matrix_world)
    P = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    nl = len(me.loops)
    uv = np.zeros(nl * 2, dtype=np.float32)
    me.uv_layers["UVMap"].data.foreach_get("uv", uv)
    nrm = np.zeros(nl * 3, dtype=np.float32)
    me.corner_normals.foreach_get("vector", nrm)
    vi = np.zeros(nl, dtype=np.int64)
    me.loops.foreach_get("vertex_index", vi)
    tl = np.zeros(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("loops", tl)
    nrm = nrm.reshape(-1, 3).astype(np.float64) @ mw[:3, :3].T
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    return {"P": P[vi], "uv": uv.reshape(-1, 2).astype(np.float64), "N": nrm, "tris": tl.reshape(-1, 3)}


# ----------------------------------------------------------------------------
# Painting
# ----------------------------------------------------------------------------


def _ss(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hex(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


# Colours (sRGB) and weights of the paint (docs/art/character-look.md section 3.2).
PAINT = {
    "sclera": _hex("#F6F4F2"),
    "sclera_shade": _hex("#C3C7D6"),
    "lash": _hex("#3A2B28"),  # times mix(1, hair, LASH_HAIR): blonde hair, lighter lashes
    "lash_edge": _hex("#211715"),
    "lash_lower": _hex("#7A5C56"),
    "pupil": 0.10,
    "brow": 0.55,
    "iris_top": 0.40,  # look pass 2: a lighter, more luminous iris (pass 1: 0.36 .. 0.95)
    "iris_bottom": 1.0,
    "iris_rim": 0.14,
    "lip": np.array([1.0, 0.90, 0.90]),
    "blush": np.array([1.0, 0.93, 0.91]),
    "crease": np.array([0.90, 0.83, 0.81]),
    "nose": np.array([0.90, 0.82, 0.80]),
}
# The mouth line and the mark under the nose tip (lines: they multiply the
# skin tone, R < 1 and R the lightest ratio, so they stay warm), in options
# (user, 2026-11-01: at a distance the nose and mouth are hard to make out;
# darker paint, closer to black): "current" the first painted nose, "A" dark
# brown-black, "B" near-black and 1.3x thicker (`thick` scales both lines'
# thickness). LINE_OPTION picks the built one: B, chosen in review
# (2026-11-02).
LINE_OPTIONS = {
    "current": {"mouth": (0.58, 0.40, 0.38), "nose_tip": (0.72, 0.56, 0.53), "thick": 1.0},
    "A": {"mouth": (0.30, 0.21, 0.19), "nose_tip": (0.34, 0.24, 0.22), "thick": 1.0},
    "B": {"mouth": (0.15, 0.105, 0.095), "nose_tip": (0.17, 0.12, 0.105), "thick": 1.3},
}
LINE_OPTION = "B"


def use_lines(option: str) -> None:
    """Sets the line option (LINE_OPTIONS) the paint uses: PAINT's `mouth`
    and `nose_tip` rows and LINE_THICK."""
    global LINE_OPTION, LINE_THICK
    o = LINE_OPTIONS[option]
    LINE_OPTION = option
    PAINT["mouth"] = np.array(o["mouth"])
    PAINT["nose_tip"] = np.array(o["nose_tip"])
    LINE_THICK = float(o["thick"])


LINE_THICK = 1.0
use_lines(LINE_OPTION)
LASH_HAIR = 0.45
LASH_EXTRA = 0.0008  # metres (x the eye scale) of painted lash above the cut band at its thickest
BLUSH = {"body_a": 0.4, "body_b": 1.0}

# The mouth as painted (user, 2026-11-01: both mouths a little wider, the
# male's lower; paint only): `w` scales the style's mouth width, `dv` moves
# it (metres x the mouth scale `mouth_k`, + up). Only the texture moves: the
# mouth line cut into the mesh (mh_face.CUT_POLYS, which the unused
# `mouth_open` key stretches) stays where the style puts it.
MOUTH_PAINT = {"body_a": {"w": 1.12, "dv": -0.0096}, "body_b": {"w": 1.12, "dv": 0.0}}
# The nose (user, 2026-11-01: more obvious, by paint only): the soft shade
# under the tip (PAINT["nose"]) and over it a short, crisp tapered mark
# (PAINT["nose_tip"]) following the tip's underside, its ends drooping a
# little. Metres x the mouth scale: the mark's centre below the nose point,
# its half-width, its thickness at the middle, the droop of its ends. The
# bridge's shadow stroke is in the face-shadow map (face_shadow.BRIDGE), so
# it falls on the side away from the key light.
NOSE_MARK = {"dv": -0.0023, "half_w": 0.0026, "th": 0.00095, "droop": 0.00055}


def mouth_poly(fr, body_id: str) -> np.ndarray:
    """The painted mouth line's polygon (face coordinates): the frame's cut
    mouth polygon (`FaceFrame.world_polys`) widened and moved by MOUTH_PAINT."""
    mp = fr.world_polys()["mouth"].copy()
    adj = MOUTH_PAINT.get(body_id, {"w": 1.0, "dv": 0.0})
    mp[:, 0] *= adj["w"]
    mp[:, 1] += adj["dv"] * fr.st.get("mouth_k", 1.0)
    return mp


def nose_mark_centre(fr) -> tuple[float, float]:
    """The middle of the mark under the nose tip (face coordinates)."""
    return 0.0, float(fr.nose[1]) + NOSE_MARK["dv"] * fr.st.get("mouth_k", 1.0)


# Look pass 2 (docs/art/character-look.md section 3): the mask's A carries the flat
# weight up to FLAT_LEVEL and the glow above it (192 / 255, exact in 8 bits).
FLAT_LEVEL = 192.0 / 255.0
# The iris: its light lower gradient (eye-local y over the iris radius, -1
# bottom .. 1 top: from where it starts darkening to where it is darkest), the
# light crescent's lift and how far it lightens toward white, the glow (at
# the top, at the bottom) and the pupil's glow.
IRIS_GRADIENT = (-0.2, 0.85)
IRIS_CRESCENT = 0.22
IRIS_CRESCENT_WHITE = 0.3
IRIS_GLOW = (0.35, 1.0)
PUPIL_GLOW = 0.15
# The catchlights, larger and crisper than pass 1: (radius factor, edge softness in texels).
CATCHLIGHTS = ((1.12, 1.1), (1.3, 1.0))

# The scalp (review fix): above the hairline the head is painted in the hair
# colour, a shade darker than the hair (`SCALP_TONE`, like hair in its own
# shadow), so skin never shows between locks, under short sides (M2) or at
# the temples. The hairline round the head, as (degrees round the head from
# the front, height above the eye in units of the frame's hair_z - eye
# height): the forehead line, the temples, a sideburn in front of the ear,
# over the ear, and down behind it to the nape. Linear between the knots,
# softened over SCALP_SOFT (same units).
SCALP_LINE = ((0.0, 1.0), (40.0, 1.0), (62.0, 0.5), (74.0, 0.05), (82.0, 0.0), (88.0, 0.85),
              (106.0, 0.85), (118.0, 0.0), (135.0, -0.6), (150.0, -0.9), (180.0, -0.9))
SCALP_SOFT = 0.04
SCALP_TONE = 0.6


def scalp_cover(fr, qu: np.ndarray, qv: np.ndarray) -> np.ndarray:
    """The scalp's coverage (0..1) at face-projection coordinates (qu, qv)."""
    a = np.abs(np.degrees(qu / fr.R0))
    knots = np.array(SCALP_LINE)
    H = max(float(fr.hair_z) - float(fr.eye[1]), 1e-3)
    line = float(fr.eye[1]) + H * np.interp(a, knots[:, 0], knots[:, 1])
    return _ss(line - SCALP_SOFT * H, line + SCALP_SOFT * H, qv)


class Canvas:
    """Float layers over the texture: colour (RGB), skin weight, mask R, G, A."""

    def __init__(self, fmap: FaceMap, size: int):
        self.size = size
        c = (np.arange(size) + 0.5) / size
        u = c[None, :].repeat(size, axis=0)
        v = 1.0 - c[:, None].repeat(size, axis=1)  # row 0 is the top of the image (v = 1)
        self.qu, self.qv = fmap.face_coords(u, v)
        self.rgb = np.ones((size, size, 3))
        self.skin = np.ones((size, size))
        self.mr = np.zeros((size, size))
        self.mg = np.zeros((size, size))
        self.ma = np.zeros((size, size))
        self.glow = np.zeros((size, size))
        # Face-projection size of one texel near the face centre (anti-aliasing width).
        cu = size // 2
        cv = int(np.argmin(np.abs(self.qv[:, cu] - fmap.zc)))
        self.texel = float(max(abs(self.qu[cv, cu + 1] - self.qu[cv, cu]), abs(self.qv[cv + 1, cu] - self.qv[cv, cu])))

    def box(self, u0, u1, v0, v1):
        """Row and column slices covering face-projection box (u0..u1, v0..v1)."""
        cols = np.nonzero((self.qu[self.size // 2] >= u0) & (self.qu[self.size // 2] <= u1))[0]
        rows = np.nonzero((self.qv[:, self.size // 2] >= v0) & (self.qv[:, self.size // 2] <= v1))[0]
        if not len(cols) or not len(rows):
            return None
        return slice(int(rows.min()), int(rows.max()) + 1), slice(int(cols.min()), int(cols.max()) + 1)

    def over(self, sl, cov, rgb=None, skin=None, mr=None, mg=None, ma=None, glow=None):
        """Lays a layer over the box `sl` with coverage `cov` (0..1)."""
        c = cov[..., None]
        if rgb is not None:
            self.rgb[sl] = self.rgb[sl] * (1 - c) + np.broadcast_to(rgb, cov.shape + (3,)) * c
        for name, val in (("skin", skin), ("mr", mr), ("mg", mg), ("ma", ma), ("glow", glow)):
            if val is not None:
                arr = getattr(self, name)
                arr[sl] = arr[sl] * (1 - cov) + val * cov

    def multiply(self, sl, amount, mult):
        """Multiplies the colour by `mult` (RGB) at `amount` (0..1) in box `sl`."""
        a = amount[..., None]
        self.rgb[sl] = self.rgb[sl] * (1 - a + a * np.asarray(mult)[None, None, :])


def _poly_sdf_box(cv: Canvas, sl, poly: np.ndarray) -> np.ndarray:
    Q = np.stack([cv.qu[sl].ravel(), cv.qv[sl].ravel()], axis=1)
    out = np.empty(len(Q))
    step = 20000
    for i in range(0, len(Q), step):
        out[i:i + step] = MF.poly_sdf(Q[i:i + step], poly)
    return out.reshape(cv.qu[sl].shape)


def paint(fr, fmap: FaceMap, body_id: str, size: int = COLOUR_SIZE * SUPER) -> Canvas:
    """The face on a canvas (`size` square, face projection of `fmap`)."""
    cv = Canvas(fmap, size)
    st = fr.st
    k = fr.k
    km = st.get("mouth_k", 1.0)
    aa = cv.texel
    polys = fr.world_polys()
    e = MF.EyeShape(st)
    w = st["eye_w"]

    def cover(d, soft=1.0):
        return np.clip(0.5 - d / (aa * soft), 0.0, 1.0)

    # --- the scalp: hair colour above the hairline (SCALP_LINE) -------------
    sc = scalp_cover(fr, cv.qu, cv.qv)
    full = (slice(None), slice(None))
    cv.over(full, sc, rgb=np.full(3, SCALP_TONE), skin=0.0, mr=0.0, mg=1.0, ma=0.0)

    # --- skin multipliers first (they sit under the features) -------------
    for side in (1, -1):
        tag = "L" if side > 0 else "R"
        eu, ev = fr.eye[0] * side, fr.eye[1]
        # Blush: a soft ellipse under the eye, toward the outer corner.
        bc_u = eu + side * 0.06 * w
        bc_v = ev + float(e.lower(np.array([0.0]))[0]) - 0.0068 * k
        rx, ry = 0.50 * w, 0.0052 * k
        sl = cv.box(bc_u - 2 * rx, bc_u + 2 * rx, bc_v - 2 * ry, bc_v + 2 * ry)
        if sl:
            r2 = ((cv.qu[sl] - bc_u) / rx) ** 2 + ((cv.qv[sl] - bc_v) / ry) ** 2
            cv.multiply(sl, 0.85 * BLUSH.get(body_id, 1.0) * np.exp(-1.6 * r2), PAINT["blush"])
        # Lid crease: a thin soft line above the lash band, outer two thirds.
        cp = polys[f"crease_{tag}"]
        sl = cv.box(cp[:, 0].min() - 0.002, cp[:, 0].max() + 0.002, cp[:, 1].min() - 0.002, cp[:, 1].max() + 0.002)
        if sl:
            d = _poly_sdf_box(cv, sl, cp)
            cv.multiply(sl, 0.75 * cover(d, 3.0), PAINT["crease"])
    # Nose: a small soft shade under the tip.
    nu, nv = 0.0, float(fr.nose[1]) - 0.0026 * km
    rx, ry = 0.0030 * km, 0.0013 * km
    sl = cv.box(nu - 3 * rx, nu + 3 * rx, nv - 3 * ry, nv + 3 * ry)
    if sl:
        r2 = ((cv.qu[sl] - nu) / rx) ** 2 + ((cv.qv[sl] - nv) / ry) ** 2
        cv.multiply(sl, 0.8 * np.exp(-1.4 * r2), PAINT["nose"])
    # ... and the crisp mark under the tip (NOSE_MARK): tapered, its ends drooping.
    mu, mv = nose_mark_centre(fr)
    hw, th, droop = NOSE_MARK["half_w"] * km, NOSE_MARK["th"] * LINE_THICK * km, NOSE_MARK["droop"] * km
    sl = cv.box(mu - hw - 0.001, mu + hw + 0.001, mv - droop - th - 0.001, mv + th + 0.001)
    if sl:
        s = np.clip(np.abs(cv.qu[sl] - mu) / hw, 0.0, 1.0)
        half = 0.5 * th * np.sqrt(np.clip(1.0 - s ** 2, 0.0, 1.0))  # round ends
        dv = np.abs(cv.qv[sl] - (mv - droop * s ** 2))
        cov = cover(dv - half, 1.2) * cover((np.abs(cv.qu[sl] - mu) - hw), 1.2)
        cv.multiply(sl, cov, PAINT["nose_tip"])
    # Lips: a faint tint under the mouth line (the painted one, MOUTH_PAINT).
    adj = MOUTH_PAINT.get(body_id, {"w": 1.0, "dv": 0.0})
    mw = st["mouth_w"] * adj["w"]
    lu, lv = 0.0, float(fr.mouth[1]) + st["mouth_dv"] + adj["dv"] * km - 0.0024 * km
    rx, ry = 0.36 * mw, 0.0018 * km
    sl = cv.box(lu - 3 * rx, lu + 3 * rx, lv - 3 * ry, lv + 3 * ry)
    if sl:
        r2 = ((cv.qu[sl] - lu) / rx) ** 2 + ((cv.qv[sl] - lv) / ry) ** 2
        cv.multiply(sl, 0.9 * np.exp(-1.3 * r2), PAINT["lip"])
    # Mouth line (the cut polygon widened and moved by MOUTH_PAINT), softened ends; flat.
    mp = mouth_poly(fr, body_id)
    sl = cv.box(mp[:, 0].min() - 0.002, mp[:, 0].max() + 0.002, mp[:, 1].min() - 0.002, mp[:, 1].max() + 0.002)
    if sl:
        # Thicker by LINE_THICK: the outline pushed out by half the extra thickness.
        d = _poly_sdf_box(cv, sl, mp) - 0.5 * (LINE_THICK - 1.0) * st["mouth_th"]
        end = 1.0 - 0.55 * _ss(0.25, 0.5, np.abs(cv.qu[sl]) / max(mw, 1e-6))
        cov = cover(d, 1.5)
        cv.multiply(sl, cov * end, PAINT["mouth"])
        cv.over(sl, cov, ma=1.0)

    # --- eyes ---------------------------------------------------------------
    for side in (1, -1):
        tag = "L" if side > 0 else "R"
        allp = polys[f"eye_all_{tag}"]
        sl = cv.box(allp[:, 0].min() - 0.003, allp[:, 0].max() + 0.003, allp[:, 1].min() - 0.003, allp[:, 1].max() + 0.003)
        if not sl:
            continue
        d_all = _poly_sdf_box(cv, sl, allp)
        d_open = _poly_sdf_box(cv, sl, polys[f"eye_open_{tag}"])
        # Eye-local coordinates (s outward, t up).
        s = (cv.qu[sl] - fr.eye[0] * side) * side
        t = cv.qv[sl] - fr.eye[1]
        sc = np.clip(s, e.s_in, e.s_out)
        up, lo = e.upper(sc), e.lower(sc)
        h = np.maximum(up - lo, 1e-5)
        c_all = cover(d_all)
        c_open = cover(d_open)
        # Lash band (and the lower line): the eye_all polygon outside the opening.
        lash = c_all * (1.0 - c_open)
        lower = t < 0.5 * (up + lo)
        edge = 1.0 - _ss(0.0, 0.0011 * k, np.maximum(d_open, 0.0))  # darkest against the eye
        col = PAINT["lash"][None, None, :] * (1 - edge[..., None]) + PAINT["lash_edge"][None, None, :] * edge[..., None]
        col = np.where(lower[..., None], PAINT["lash_lower"][None, None, :], col)
        cv.over(sl, lash, rgb=col, skin=0.0, mr=0.0, mg=np.where(lower, 0.25, LASH_HAIR), ma=1.0, glow=0.0)
        # A bolder upper lash line: painted a little above the cut band, inside
        # the zone the blink carries down with the lash line (mh_face.eye_keys).
        top = up + e.lash_th(sc)
        qq = np.clip((s - e.s_in) / (e.s_out - e.s_in), 0.0, 1.0)
        extra = LASH_EXTRA * k * _ss(0.05, 0.45, qq) * (1.0 - _ss(0.82, 1.0, qq))
        extra = np.minimum(extra, 0.8 * st.get("blink_band", 1.0))
        c_extra = np.clip(0.5 - (t - (top + extra)) / aa, 0.0, 1.0) * (t > up) * (extra > 1e-6) * (1.0 - c_open)
        c_extra = np.maximum(c_extra - lash, 0.0)
        cv.over(sl, c_extra, rgb=PAINT["lash"], skin=0.0, mr=0.0, mg=LASH_HAIR, ma=1.0, glow=0.0)
        # Eye white with the cool shadow under the upper lid, a little deeper at the corners.
        d_top = up - t
        shade = 1.0 - _ss(0.0, 0.48 * h, d_top)
        corner = _ss(0.55, 1.0, np.abs((s - 0.5 * (e.s_in + e.s_out)) / (0.5 * (e.s_out - e.s_in))))
        amt = np.clip(0.95 * shade + 0.35 * corner, 0.0, 1.0)
        white = PAINT["sclera"][None, None, :] * (1 - amt[..., None]) + PAINT["sclera_shade"][None, None, :] * amt[..., None]
        cv.over(sl, c_open, rgb=white, skin=0.0, mr=0.0, mg=0.0, ma=1.0, glow=0.0)
        # Iris (greyscale, tinted by the eye colour): dark top to light bottom,
        # a dark rim, a light crescent low down, faint radial variation.
        cc = e.iris_centre()
        rx, ry = st["iris_rx"], st["iris_ry"]
        xs, ys = (s - cc[0]) / rx, (t - cc[1]) / ry
        re = np.sqrt(xs ** 2 + ys ** 2)
        c_iris = np.clip((1.0 - re) * min(rx, ry) / aa + 0.5, 0.0, 1.0) * c_open
        g = PAINT["iris_bottom"] + (PAINT["iris_top"] - PAINT["iris_bottom"]) * _ss(IRIS_GRADIENT[0], IRIS_GRADIENT[1], ys)
        ang = np.arctan2(ys, xs)
        g = g * (1.0 + 0.06 * np.sin(ang * 11.0 + 0.7) * _ss(0.35, 0.75, re) * (1 - _ss(0.8, 0.9, re)))
        cres = _ss(0.38, 0.62, re) * (1.0 - _ss(0.74, 0.86, re)) * _ss(-0.15, -0.6, ys)
        g = g + IRIS_CRESCENT * cres
        g = g * (0.62 + 0.38 * _ss(0.0, 0.32 * h, d_top))  # the lid's shadow over the top
        rim = _ss(0.80, 0.95, re)
        g = g * (1 - rim) + PAINT["iris_rim"] * rim
        mr = 1.0 - IRIS_CRESCENT_WHITE * cres
        # The glow (look pass 2): strongest in the light lower part, none on the rim.
        glow = (IRIS_GLOW[0] + (IRIS_GLOW[1] - IRIS_GLOW[0]) * _ss(0.3, -0.7, ys)) * (1 - rim)
        cv.over(sl, c_iris, rgb=np.clip(g, 0.0, 1.0)[..., None].repeat(3, axis=2), mr=mr, mg=0.0, ma=1.0, glow=glow)
        # Pupil: a dark tone of the eye colour.
        pr = np.sqrt((xs / 0.30) ** 2 + ((ys - 0.10) / 0.42) ** 2)
        c_pupil = np.clip((1.0 - pr) * 0.30 * rx / (1.6 * aa) + 0.5, 0.0, 1.0) * c_open
        cv.over(sl, c_pupil, rgb=np.array([PAINT["pupil"]] * 3), mr=0.65, glow=PUPIL_GLOW)
        # Two highlights: a larger soft oval upper outer, a small one lower inner.
        h1, h2 = st["hl"]
        (k1, s1), (k2, s2) = CATCHLIGHTS
        for (dx, dy, r, ax, soft) in ((0.30 * rx, 0.40 * ry, h1 * k1, 1.2, s1), (-0.36 * rx, -0.50 * ry, h2 * 1.15 * k2, 1.0, s2)):
            hr = np.sqrt(((s - cc[0] - dx) / (r * ax)) ** 2 + ((t - cc[1] - dy) / r) ** 2)
            c_hl = np.clip((1.0 - hr) * r / (soft * aa) + 0.5, 0.0, 1.0) * c_open
            cv.over(sl, c_hl, rgb=np.ones(3), mr=0.0, glow=1.0)
        # Brow: a clean tapered stroke in the hair colour.
        bp = polys[f"brow_{tag}"]
        bsl = cv.box(bp[:, 0].min() - 0.002, bp[:, 0].max() + 0.002, bp[:, 1].min() - 0.002, bp[:, 1].max() + 0.002)
        if bsl:
            d = _poly_sdf_box(cv, bsl, bp)
            cv.over(bsl, cover(d, 1.6), rgb=np.array([PAINT["brow"]] * 3), skin=0.0, mr=0.0, mg=1.0, ma=0.0)
    return cv


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------


def downsample(a: np.ndarray, f: int) -> np.ndarray:
    if f == 1:
        return a
    h, w = a.shape[:2]
    return a.reshape(h // f, f, w // f, f, *a.shape[2:]).mean(axis=(1, 3))


def colour_image(cv: Canvas) -> np.ndarray:
    """(1024, 1024, 4) floats: RGB, A = skin weight."""
    img = np.concatenate([cv.rgb, cv.skin[..., None]], axis=2)
    return downsample(img, cv.size // COLOUR_SIZE)


def mask_image(cv: Canvas, threshold: np.ndarray | None) -> np.ndarray:
    """(512, 512, 4) floats: eye weight, hair weight, threshold, flat weight."""
    f = cv.size // MASK_SIZE
    a = FLAT_LEVEL * cv.ma + (1.0 - FLAT_LEVEL) * cv.ma * cv.glow
    m = np.stack([downsample(cv.mr, f), downsample(cv.mg, f),
                  threshold if threshold is not None else np.full((MASK_SIZE, MASK_SIZE), 0.5), downsample(a, f)], axis=2)
    return m


def write_png(path: str, img: np.ndarray) -> None:
    """RGBA (or RGB) floats 0..1, row 0 at the top, as an 8-bit PNG."""
    a = np.clip(np.round(img * 255.0), 0, 255).astype(np.uint8)
    h, w, c = a.shape
    raw = b"".join(b"\x00" + a[r].tobytes() for r in range(h))
    ctype = {3: 2, 4: 6}[c]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


def read_png(path: str) -> np.ndarray:
    """An 8-bit RGB(A) PNG written by `write_png` (filter 0 rows), as uint8 (h, w, c)."""
    with open(path, "rb") as fh:
        data = fh.read()
    pos = 8
    idat = b""
    w = h = c = 0
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, _, ct = struct.unpack(">IIBB", body[:10])
            c = {2: 3, 6: 4}[ct]
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    rows = [raw[r * (w * c + 1) + 1:(r + 1) * (w * c + 1)] for r in range(h)]
    return np.frombuffer(b"".join(rows), dtype=np.uint8).reshape(h, w, c)


def gpu_bytes(size: int) -> int:
    """RGBA8 with a full mip chain."""
    return int(size * size * 4 * 4 / 3)


def export(body_id: str, head_obj, assets: str) -> dict:
    """Paints and bakes the face textures of a built body (its head LOD0
    object) into `assets/characters/`; returns the manifest's `face` entry."""
    from . import face_shadow as FS

    fr, fmap = FACE_BUILDS[body_id]
    mesh = head_mesh(head_obj)
    dump = os.environ.get("FACE_DUMP")
    if dump:  # iteration on the paint without a body build (tools: face_iterate.py)
        import pickle

        os.makedirs(dump, exist_ok=True)
        with open(os.path.join(dump, f"{body_id}.pkl"), "wb") as fh:
            pickle.dump({"frame": fr, "fmap": fmap, "mesh": mesh}, fh)
    cv = paint(fr, fmap, body_id)
    thr = FS.bake(mesh, fmap, fr, MASK_SIZE)
    col_rel = f"characters/face_{body_id}.png"
    mask_rel = f"characters/face_mask_{body_id}.png"
    write_png(os.path.join(assets, col_rel), colour_image(cv))
    write_png(os.path.join(assets, mask_rel), mask_image(cv, thr))
    return {
        "colour": col_rel,
        "mask": mask_rel,
        "size": [COLOUR_SIZE, MASK_SIZE],
        "gpu_bytes": gpu_bytes(COLOUR_SIZE) + gpu_bytes(MASK_SIZE),
        "map": fmap.to_json(),
        "shadow": FS.meta(),
        "doc": "docs/art/character-look.md section 3: albedo = c.rgb * mix(1, skin, c.a) * mix(1, eye, m.r) * mix(1, hair, m.g); m.b = face-shadow threshold (light angle / 180 at which the texel goes dark, light on +X; mirror u for -X); m.a = flat",
    }
