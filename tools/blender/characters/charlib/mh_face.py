"""Anime face for the MakeHuman bodies (docs/art/style-guide.md, anime direction).

The MakeHuman face is realistic; this module turns it into a Genshin-like
anime face in three steps, all on the visible MakeHuman topology:

1. **Shape** (`anime_offset`): an ellipsoid is fitted to the forehead,
   temples and cheeks; the eye sockets, the nose and the lips are blended
   radially onto it (the nose keeps a small tip, the lips almost vanish), so
   the face reads as one smooth egg-like form with flat planes. The result is
   a fixed per-vertex offset added to every shape key, so builds, face sliders
   and expressions keep their deltas.
2. **Painted features as crisp regions** (`cut_features`, `paint_features`):
   eyes (lash line with a wing, sclera with the upper-lid shade, a tall iris
   in three cel bands with a dark rim, pupil, two catch lights, a thin lower
   line), clean brow strokes, a short mouth line and a tiny nose shade are 2D
   shapes in a cylindrical face projection. The mesh is first refined with a
   fine grid around them, then cut exactly along every outline
   (`mh_paint.CutMesh`), and each face takes a flat atlas swatch, so the
   features are vector-sharp at any distance without a larger texture. The
   LOD decimation keeps the outlines (they are paint borders) and collapses
   the refining grid inside flat regions.
3. **Shading** (`face_normals`): the face's normals are taken from the
   fitted ellipsoid (blended out toward the jaw, ears and hairline), exported
   as custom normals, so cel shading gives one clean shadow shape with no
   nose or cheek blotches.

Eye keys (`blink_l`, `blink_r`, `face_eyes_narrow`, `face_eyes_round`) are made
in the same 2D eye frame after the cuts, so the painted eye closes into the
anime closed-eye curve (lash line resting on the lower lid).

On a head wrapped onto a sculpt (`shape_target.Reshaper`) the same machinery
runs on the wrapped mesh with `sculpt_frame`: the eyes are placed in the
sculpt's eye sockets and sized to them (`SCULPT_EYE`: Genshin-like, large,
the whole eye design scaled together), the mouth and nose shade go to the
sculpt's mouth and nose, the brows ride above the eyes, thin strokes get a
finer refining grid, and the face ellipsoid is fitted round the middle of
the new head (`fit_face_ellipsoid`: no search range to run out of). The
style lives on the frame (`FaceFrame.st`, `FaceFrame.k`), so the keys move
the features where they are on that face.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import skeleton as K

# ----------------------------------------------------------------------------
# Style parameters (metres in the face projection; u outward for the left eye)
# ----------------------------------------------------------------------------

STYLE = {
    # Male: narrower, more angular eyes, straight heavier brows set low and
    # angled up toward the temples.
    "body_a": dict(eye_du=0.0010, eye_dv=-0.0020, eye_w=0.0360, eye_up=0.0100, eye_dn=0.0062, corner_in=-0.0014,
                   corner_out=0.0020, lash_in=0.0015, lash_out=0.0040, wing=0.0050, wing_rise=0.0018,
                   lower_th=0.0006, iris_rx=0.0074, iris_ry=0.0094, iris_ds=0.0004, iris_lift=0.86,
                   hl=(0.0024, 0.0011), brow_gap=0.0040, brow_th=0.0029, brow_arch=0.0006, brow_tilt=0.0024,
                   brow_len=1.06, crease=0.0024, mouth_w=0.0190, mouth_th=0.00095, mouth_curve=0.00015,
                   mouth_dv=0.0, nose_size=1.0),
    # Female: rounder, taller opening, thinner arched brows set higher.
    "body_b": dict(eye_du=0.0008, eye_dv=-0.0018, eye_w=0.0370, eye_up=0.0128, eye_dn=0.0080, corner_in=-0.0012,
                   corner_out=0.0016, lash_in=0.0016, lash_out=0.0042, wing=0.0054, wing_rise=0.0019,
                   lower_th=0.00055, iris_rx=0.0082, iris_ry=0.0108, iris_ds=0.0004, iris_lift=0.84,
                   hl=(0.0027, 0.0012), brow_gap=0.0082, brow_th=0.0016, brow_arch=0.0022, brow_tilt=0.0004,
                   brow_len=1.0, crease=0.0028, mouth_w=0.0165, mouth_th=0.0009, mouth_curve=0.0002,
                   mouth_dv=0.0, nose_size=0.85),
}
# How far each zone is pulled onto the face ellipsoid (1 = fully).
ZONE_PULL = {"eye": 0.92, "nose": 0.78, "mouth": 0.72}
GRID = 0.0010  # longest edge (face projection) around the features after refining
INNER_MOUTH_BACK = 0.0006
NORMAL_VERTICAL = 0.35
MOUTH_OPEN = 0.0055  # how far the painted mouth opens (metres at the centre)
CUT_SNAP = 0.00012

SCLERA = "sclera"
NONE = (0.0, 0.0, 0.0)
SKIN = (1.0, 0.0, 0.0)
HAIR = (0.0, 1.0, 0.0)
EYE = (0.0, 0.0, 1.0)  # the iris: body mask B = eye colour (character adoption)


def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ----------------------------------------------------------------------------
# 2D geometry
# ----------------------------------------------------------------------------


def poly_sdf(Q: np.ndarray, V: np.ndarray) -> np.ndarray:
    """Signed distance (negative inside) from points Q (n, 2) to the closed
    polygon V (k, 2)."""
    A = V
    B = np.roll(V, -1, axis=0)
    AB = B - A
    L2 = np.maximum((AB * AB).sum(axis=1), 1e-20)
    AQ = Q[:, None, :] - A[None, :, :]
    t = np.clip((AQ * AB[None]).sum(axis=2) / L2[None], 0.0, 1.0)
    d = AQ - t[..., None] * AB[None]
    dist = np.sqrt((d * d).sum(axis=2)).min(axis=1)
    # Even-odd crossing test.
    yq = Q[:, 1][:, None]
    xq = Q[:, 0][:, None]
    ya, yb = A[None, :, 1], B[None, :, 1]
    xa, xb = A[None, :, 0], B[None, :, 0]
    cond = (ya > yq) != (yb > yq)
    xint = xa + (yq - ya) * (xb - xa) / np.where(np.abs(yb - ya) < 1e-20, 1e-20, yb - ya)
    inside = (cond & (xq < xint)).sum(axis=1) % 2 == 1
    return np.where(inside, -dist, dist)


def ellipse(c, rx, ry, n: int = 64, a0: float = 0.0, a1: float = 2 * math.pi) -> np.ndarray:
    a = np.linspace(a0, a1, n, endpoint=False)
    return np.stack([c[0] + rx * np.cos(a), c[1] + ry * np.sin(a)], axis=1)


@dataclass
class EyeShape:
    """One eye in eye-local coordinates (s outward, t up; metres)."""

    st: dict

    def __post_init__(self):
        p = self.st
        w = p["eye_w"]
        self.s_in, self.s_out = -0.46 * w, 0.54 * w
        self.t_in, self.t_out = p["corner_in"], p["corner_out"]

    def q(self, s):
        return np.clip((s - self.s_in) / (self.s_out - self.s_in), 0.0, 1.0)

    def base(self, s):
        return self.t_in + (self.t_out - self.t_in) * self.q(s)

    def upper(self, s):
        q = self.q(s)
        return self.base(s) + self.st["eye_up"] * np.sin(np.pi * q ** 0.78) ** 0.62

    def lower(self, s):
        q = self.q(s)
        return self.base(s) - self.st["eye_dn"] * np.sin(np.pi * q ** 1.05) ** 0.85

    def lash_th(self, s):
        q = self.q(s)
        p = self.st
        return p["lash_in"] + (p["lash_out"] - p["lash_in"]) * _smooth(q * 1.25)

    def opening(self, n: int = 48) -> np.ndarray:
        s = np.linspace(self.s_in, self.s_out, n)
        top = np.stack([s, self.upper(s)], axis=1)
        bot = np.stack([s[::-1][1:-1], self.lower(s[::-1][1:-1])], axis=1)
        return np.vstack([top, bot])

    def all(self, n: int = 48) -> np.ndarray:
        """Opening plus the upper lash band, its wing and the lower line."""
        p = self.st
        s = np.linspace(self.s_in, self.s_out, n)
        # Upper edge of the lash band (inner corner starts thin, just inside).
        top = np.stack([s, self.upper(s) + self.lash_th(s)], axis=1)
        top[0, 0] -= 0.0006
        wing_tip = np.array([self.s_out + p["wing"], self.t_out + p["wing_rise"] + p["lash_out"] * 0.6])
        wing_low = np.array([self.s_out + p["wing"] * 0.35, self.t_out - 0.0002])
        # Lower line along the outer 45%, tapering into the lower lid.
        sl = np.linspace(self.s_out, self.s_in, n)
        q = self.q(sl)
        th = p["lower_th"] * _smooth((q - 0.5) / 0.25) * (1.0 - _smooth((q - 0.97) / 0.03))
        bot = np.stack([sl, self.lower(sl) - th], axis=1)[1:-1]
        return np.vstack([top, wing_tip[None], wing_low[None], bot])

    def shade(self, n: int = 40) -> np.ndarray:
        """Upper-lid shade band on the sclera."""
        s = np.linspace(self.s_in, self.s_out, n)
        th = 0.0014 + 0.0004 * np.sin(np.pi * self.q(s))
        top = np.stack([s, self.upper(s) + 0.001], axis=1)
        bot = np.stack([s[::-1], (self.upper(s) - th)[::-1]], axis=1)
        return np.vstack([top, bot])

    def iris_centre(self):
        """The iris rests on the lower lid and runs up under the lash line."""
        p = self.st
        s = 0.5 * (self.s_in + self.s_out) + p["iris_ds"]
        return np.array([s, float(self.lower(np.array([s]))[0]) + p["iris_lift"] * p["iris_ry"]])

    def crease(self, n: int = 30) -> np.ndarray:
        """Thin lid crease above the lash line (outer two thirds)."""
        s = np.linspace(self.s_in + 0.3 * (self.s_out - self.s_in), self.s_out + 0.1 * self.st["wing"], n)
        q = self.q(s)
        c = self.upper(s) + self.lash_th(s) + self.st["crease"] * (0.6 + 0.4 * np.sin(np.pi * q))
        th0, th1, p = self.st.get("crease_th", (0.0001, 0.00045, 1.0))  # ends, middle, taper exponent
        th = th1 * np.sin(np.pi * np.clip((s - s[0]) / (s[-1] - s[0]), 0, 1)) ** p + th0
        return np.vstack([np.stack([s, c + th / 2], axis=1), np.stack([s[::-1], (c - th / 2)[::-1]], axis=1)])


def eye_polys(st: dict) -> dict[str, np.ndarray]:
    e = EyeShape(st)
    c = e.iris_centre()
    rx, ry = st["iris_rx"], st["iris_ry"]
    out = {"eye_all": e.all(), "eye_open": e.opening(), "shade": e.shade(), "iris": ellipse(c, rx, ry),
           "iris_in": ellipse(c, rx - 0.0007, ry - 0.0007), "crease": e.crease()}
    # Upper (dark) cap of the iris and the light lower crescent.
    t_top = c[1] + 0.12 * ry
    a = math.asin(0.12)
    out["iris_top"] = ellipse(c, rx * 1.05, ry * 1.05, 40, a, math.pi - a)
    t_low = -0.62
    a2 = math.asin(t_low)
    out["iris_low"] = np.vstack([ellipse(c, rx * 1.05, ry * 1.05, 40, math.pi - a2, 2 * math.pi + a2)])
    del t_top
    out["pupil"] = ellipse(c + np.array([0.0, 0.08 * ry]), 0.36 * rx, 0.44 * ry, 40)
    h1, h2 = st["hl"]
    out["hl1"] = ellipse(c + np.array([-0.38 * rx, 0.36 * ry]), h1 * 1.1, h1, 28)
    out["hl2"] = ellipse(c + np.array([0.40 * rx, -0.42 * ry]), h2, h2, 16)
    # Brow: a tapered stroke above the eye.
    w = st["eye_w"]
    s0, s1 = e.s_in - 0.05 * w, e.s_in + st["brow_len"] * w
    qs = np.linspace(0.0, 1.0, 36)
    s = s0 + (s1 - s0) * qs
    top_eye = float(np.max(e.upper(np.linspace(e.s_in, e.s_out, 50))))
    centre_t = (top_eye + st["lash_out"] + st["brow_gap"] + st["brow_arch"] * np.sin(np.pi * qs ** 0.8)
                + st["brow_tilt"] * (qs - 0.4) * 1.2 - 0.9 * st["brow_arch"] * qs ** 3)
    th = st["brow_th"] * (1.0 - qs ** 2.2) * np.clip((qs + 0.12) / 0.22, 0.0, 1.0)
    th = np.maximum(th, st.get("brow_min", 0.00012))
    up = np.stack([s, centre_t + th * 0.5], axis=1)
    dn = np.stack([s, centre_t - th * 0.5], axis=1)[::-1]
    out["brow"] = np.vstack([up, dn])
    return out


# ----------------------------------------------------------------------------
# The face frame
# ----------------------------------------------------------------------------


@dataclass
class FaceFrame:
    b: object
    axis_y: float
    R0: float
    eye: np.ndarray  # left eye centre (u, v)
    mouth: np.ndarray
    nose: np.ndarray  # nose tip (u, v)
    ell_c: np.ndarray
    ell_r: np.ndarray
    chin_z: float
    hair_z: float
    polys: dict = field(default_factory=dict)
    st: dict | None = None  # style (default STYLE[b.id]; a sculpt head sizes its own, see sculpt_face)
    k: float = 1.0  # feature scale: key and zone radii (metres for the scripted heads) scale with it
    normal_vertical: float = NORMAL_VERTICAL

    def __post_init__(self):
        if self.st is None:
            self.st = STYLE[self.b.id]

    def uv(self, P: np.ndarray) -> np.ndarray:
        a = np.arctan2(P[:, 0], self.axis_y - P[:, 1])
        return np.stack([self.R0 * a, P[:, 2]], axis=1)

    def angle(self, P: np.ndarray) -> np.ndarray:
        return np.degrees(np.arctan2(P[:, 0], self.axis_y - P[:, 1]))

    def front(self, P: np.ndarray) -> np.ndarray:
        return (np.abs(self.angle(P)) < 75.0) & (P[:, 1] < self.axis_y)

    def ell_point(self, P: np.ndarray) -> np.ndarray:
        d = P - self.ell_c
        k = np.sqrt(((d / self.ell_r) ** 2).sum(axis=1))
        return self.ell_c + d / np.maximum(k, 1e-9)[:, None]

    def ell_normal(self, P: np.ndarray) -> np.ndarray:
        """Normals of the face ellipsoid with the vertical tilt damped
        (`NORMAL_VERTICAL`): the face reads almost flat-lit from the front,
        with one clean terminator at the sides instead of a shadowed chin."""
        g = (P - self.ell_c) / self.ell_r ** 2
        g[:, 2] *= self.normal_vertical
        return g / np.maximum(np.linalg.norm(g, axis=1, keepdims=True), 1e-12)

    def eye_local(self, P: np.ndarray, side: int) -> np.ndarray:
        q = self.uv(P)
        c = self.eye * np.array([side, 1.0])
        return np.stack([(q[:, 0] - c[0]) * side, q[:, 1] - c[1]], axis=1)

    def world_polys(self) -> dict[str, np.ndarray]:
        """Every feature polygon in face (u, v) coordinates."""
        out = {}
        st = self.st
        for side, tag in ((1, "L"), (-1, "R")):
            for name, poly in eye_polys(st).items():
                p = poly.copy()
                p[:, 0] = self.eye[0] * side + p[:, 0] * side
                p[:, 1] = self.eye[1] + p[:, 1]
                if side < 0:
                    p = p[::-1]
                out[f"{name}_{tag}"] = p
        mw, mt, mc = st["mouth_w"], st["mouth_th"], st["mouth_curve"]
        uu = np.linspace(-0.5, 0.5, 24)
        cen = self.mouth[1] + mc * (uu * 2) ** 2 + st["mouth_dv"]
        th = mt * (1.0 - np.abs(uu * 2) ** 3) + 0.00012
        top = np.stack([uu * mw, cen + th * 0.5], axis=1)
        bot = np.stack([uu[::-1] * mw, (cen - th * 0.5)[::-1]], axis=1)
        out["mouth"] = np.vstack([top, bot])
        ns = st["nose_size"]
        n = self.nose
        out["nose"] = np.array([[n[0] + 0.0003, n[1] + 0.0022 * ns], [n[0] + 0.0030 * ns, n[1] - 0.0016 * ns],
                                [n[0] + 0.0016 * ns, n[1] - 0.0030 * ns], [n[0] + 0.0002, n[1] - 0.0026 * ns]])
        self.polys = out
        return out


def make_frame(b, co: np.ndarray, skin_ids: np.ndarray, eye_centres, mouth_pt: np.ndarray, nose_pt: np.ndarray,
               chin_z: float, head_w: np.ndarray) -> FaceFrame:
    """Face frame of a stylised (fitted) MakeHuman head: cylinder axis, the
    feature anchors and the face ellipsoid fitted to forehead, temples and
    cheeks (eyes, nose and mouth excluded)."""
    hc = K.head_centre(b)
    axis_y = float(hc[1])
    ec = eye_centres[0]
    # Face radius at the eyes: the skin straight in front of the eye.
    P = co[skin_ids]
    near = P[(np.abs(P[:, 0] - ec[0]) < 0.004) & (np.abs(P[:, 2] - ec[2]) < 0.004) & (P[:, 1] < ec[1])]
    front_y = float(near[:, 1].min()) if len(near) else float(ec[1] - 0.012)
    R0 = math.hypot(ec[0], axis_y - front_y)
    st = STYLE[b.id]
    u_e = R0 * math.atan2(ec[0], axis_y - front_y) + st["eye_du"]
    eye = np.array([u_e, float(ec[2]) + st["eye_dv"]])
    mouth = np.array([0.0, float(mouth_pt[2])])
    nose = np.array([0.0, float(nose_pt[2])])
    fr = FaceFrame(b, axis_y, R0, eye, mouth, nose, np.zeros(3), np.ones(3), chin_z, float(ec[2] + 0.05 * b.head_scale))
    # Ellipsoid fit: x^2 A + (y - yc)^2 B + (z - zc)^2 C = 1 over the face
    # surface outside the feature zones, centre searched on a small grid.
    hp = co[skin_ids[head_w[skin_ids] > 0.5]]
    uvp = fr.uv(hp)
    ang = fr.angle(hp)
    sel = (np.abs(ang) < 60) & (hp[:, 2] > ec[2] - 0.055 * b.head_scale) & (hp[:, 2] < ec[2] + 0.045 * b.head_scale)
    sel &= hp[:, 1] < axis_y
    for side in (1, -1):
        c = eye * np.array([side, 1.0])
        sel &= ((uvp[:, 0] - c[0]) / 0.024) ** 2 + ((uvp[:, 1] - c[1]) / 0.017) ** 2 > 1.0
    sel &= ~((np.abs(uvp[:, 0]) < 0.016) & (uvp[:, 1] < ec[2] + 0.01) & (uvp[:, 1] > mouth[1] - 0.012))
    Q = hp[sel]
    best = None
    for yc in np.linspace(axis_y - 0.02, axis_y + 0.07, 19):
        for zc in np.linspace(ec[2] - 0.06, ec[2] + 0.03, 19):
            M = np.stack([Q[:, 0] ** 2, (Q[:, 1] - yc) ** 2, (Q[:, 2] - zc) ** 2], axis=1)
            sol, *_ = np.linalg.lstsq(M, np.ones(len(Q)), rcond=None)
            if (sol <= 0).any():
                continue
            r = 1.0 / np.sqrt(sol)
            k = np.sqrt((Q[:, 0] / r[0]) ** 2 + ((Q[:, 1] - yc) / r[1]) ** 2 + ((Q[:, 2] - zc) / r[2]) ** 2)
            err = float(np.mean((k - 1.0) ** 2))
            if best is None or err < best[0]:
                best = (err, np.array([0.0, yc, zc]), r)
    if best is None:
        raise RuntimeError(f"face ellipsoid fit failed ({len(Q)} points, z {ec[2]:.3f}, axis {axis_y:.3f})")
    fr.ell_c, fr.ell_r = best[1], best[2]
    print(f"[mh_face] ellipsoid c={np.round(fr.ell_c, 4)} r={np.round(fr.ell_r, 4)} rms={best[0] ** 0.5:.4f} "
          f"n={len(Q)} R0={R0:.4f} eye={np.round(eye, 4)}", flush=True)
    fr.world_polys()
    return fr


# ----------------------------------------------------------------------------
# The face on a sculpt-wrapped head (shape_target.Reshaper)
# ----------------------------------------------------------------------------

# Eye fit to the sculpt's socket (Genshin-like: large, set in the socket):
# `fill` = painted eye width over socket width, `inset` = shift of the eye
# centre toward the nose (share of the socket width), `dv` = shift of the
# opening's centre (share of the socket height, + up), `tall` = extra
# vertical scale of the opening.
SCULPT_EYE = {
    "body_a": dict(fill=0.88, inset=0.07, dv=0.0, tall=1.0),
    # Character look (docs/art/character-look.md section 3.3): the female opening a
    # little lower (tall 1.08 -> 1.02, -5.5 %), toward an almond young-adult eye.
    "body_b": dict(fill=1.02, inset=0.10, dv=0.0, tall=1.02),
}
# Lengths of STYLE that scale with the eye (the rest are ratios or mouth/nose).
EYE_LENGTHS = ("eye_du", "eye_dv", "eye_w", "eye_up", "eye_dn", "corner_in", "corner_out", "lash_in", "lash_out",
               "wing", "wing_rise", "lower_th", "iris_rx", "iris_ry", "iris_ds", "brow_gap", "brow_th", "brow_arch",
               "brow_tilt", "crease")
VERTICAL = ("eye_up", "eye_dn", "iris_ry")
MOUTH_LENGTHS = ("mouth_w", "mouth_th", "mouth_curve", "mouth_dv")
SCRIPTED_HEAD_H = 0.234  # crown to chin of the scripted anime head at head_scale 1 (metres)


def fit_face_ellipsoid(Q: np.ndarray, centre: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Axis-aligned ellipsoid round a fixed `centre` (the middle of the head),
    x^2 A + (y - yc)^2 B + (z - zc)^2 C = 1 fitted linearly to the face-front
    points Q. A face front alone cannot place the centre (the fit is flat in
    depth), so it is the head's own; falls back to a sphere round it."""
    d = Q - centre
    M = d ** 2
    # A flat face front can ask for an infinite radius: coefficients are
    # held at a floor (radius at most 3x the head's size) and the others refitted.
    floor = 1.0 / (3.0 * float(np.max(np.abs(d)))) ** 2
    fixed = np.zeros(3, dtype=bool)
    sol = np.full(3, floor)
    for _ in range(3):
        free = ~fixed
        rhs = np.ones(len(Q)) - M[:, fixed] @ sol[fixed]
        x, *_ = np.linalg.lstsq(M[:, free], rhs, rcond=None)
        sol[free] = x
        low = sol < floor
        if not low.any():
            break
        fixed |= low
        sol[fixed] = floor
    r = 1.0 / np.sqrt(np.maximum(sol, floor))
    k = np.sqrt(((d / r) ** 2).sum(axis=1))
    return centre.copy(), r, float(np.sqrt(np.mean((k - 1.0) ** 2)))


def sculpt_style(b, sock_w: float, sock_h: float, head_h: float) -> tuple[dict, float, float]:
    """The body's STYLE scaled to a sculpt: eye lengths by the socket width
    (`SCULPT_EYE`), mouth and nose by the head height. Returns (style, eye
    scale, mouth scale)."""
    base = STYLE[b.id]
    fit = SCULPT_EYE[b.id]
    k = fit["fill"] * sock_w / base["eye_w"]
    km = head_h / (SCRIPTED_HEAD_H * 0.98)
    st = dict(base)
    for n in EYE_LENGTHS:
        st[n] = base[n] * k * (fit["tall"] if n in VERTICAL else 1.0)
    st["hl"] = tuple(h * k for h in base["hl"])
    for n in MOUTH_LENGTHS:
        st[n] = base[n] * km
    st["nose_size"] = base["nose_size"] * km
    st["mouth_open"] = MOUTH_OPEN * km
    st["mouth_k"] = km
    # Thin strokes stay above the outline thinning tolerance (no broken
    # brow tails or dotted creases), the closed eye leaves no white sliver
    # and the lid crease travels with the lash line.
    # Thin strokes are refined finer than they are thick (a stroke passing
    # between the vertices of one triangle would not be cut at all).
    st["zone_edge"] = dict(ZONE_EDGE, brow=0.0007 * k, crease=0.00045 * k)
    st["brow_min"] = 0.00045 * k
    st["crease_th"] = (0.0003 * k, 0.00035 * k, 0.5)
    st["blink_keep"] = 0.006
    st["blink_band"] = st["crease"] * 1.05 + 0.0008 * k
    return st, k, km


def brow_anchors(st: dict, k: float) -> None:
    """Brow centre and lowest point and the zone the keys keep still under
    it (eye-local metres), and the band that closes with the lash line,
    which stops well short of a low brow."""
    pol = eye_polys(st)
    st["brow_v"] = float(pol["brow"][:, 1].mean())
    st["brow_low"] = float(pol["brow"][:, 1].min())
    lash_top = float(pol["eye_all"][:, 1].max())
    st["blink_band"] = float(min(st["blink_band"], max(0.45 * (st["brow_low"] - lash_top), 0.0005)))
    st["brow_still"] = lash_top + 0.0012 * k


def sculpt_frame(b, V: np.ndarray, faces, regions, lay: dict) -> FaceFrame:
    """FaceFrame of a head wrapped onto a sculpt: the eyes sized and placed
    in the sculpt's eye sockets, mouth and nose shade at the sculpt's mouth
    and nose, the brows above the eyes, and the face ellipsoid (normals)
    fitted freely to the new face front. `lay`: shape_target.face_layout."""
    hc = K.head_centre(b)
    axis_y = float(hc[1])
    eye3 = lay["eye"]
    R0 = math.hypot(eye3[0], axis_y - eye3[1])

    def u_of(p):
        return R0 * math.atan2(p[0], axis_y - p[1])

    box = lay["box"]  # inner, outer, lower, upper extremes of the left socket
    sock_w = u_of(box[1]) - u_of(box[0])
    sock_h = float(box[3][2] - box[2][2])
    head_h = float(lay["crown"][2] - lay["chin"][2])
    st, k, km = sculpt_style(b, sock_w, sock_h, head_h)
    fit = SCULPT_EYE[b.id]
    e = EyeShape(st)
    ss = np.linspace(e.s_in, e.s_out, 60)
    open_mid = 0.5 * (float(e.upper(ss).max()) + float(e.lower(ss).min()))
    u_c = 0.5 * (u_of(box[0]) + u_of(box[1])) - fit["inset"] * sock_w
    v_c = float(eye3[2]) + fit["dv"] * sock_h - open_mid
    eye = np.array([u_c, v_c])
    mouth = np.array([0.0, float(lay["mouth"][2])])
    nose = np.array([0.0, float(lay["nose"][2])])
    chin_z = float(lay["chin"][2])
    fr = FaceFrame(b, axis_y, R0, eye, mouth, nose, np.zeros(3), np.ones(3), chin_z,
                   float(eye3[2] + 0.05 * b.head_scale), st=st, k=k)
    # Brow centre and the still zone under it (expression keys).
    brow_anchors(st, k)
    # Ellipsoid over the face front (forehead, temples, cheeks; nose and
    # mouth left out) of the new head.
    head = sorted({v for f, r in zip(faces, regions) if r == "head" for v in f})
    P = V[np.array(head, dtype=np.int64)]
    ang = fr.angle(P)
    z = P[:, 2]
    sel = (np.abs(ang) < 58) & (P[:, 1] < axis_y) & (z > chin_z + 0.18 * head_h) & (z < v_c + 0.17 * head_h)
    sel &= ~((np.abs(P[:, 0]) < 0.30 * sock_w + 0.006) & (z < v_c) & (z > mouth[1] - 0.01))
    # Centre: the middle of the head at eye level in depth, a little below
    # the eyes in height (the face's own middle).
    lvl = V[np.abs(V[:, 2] - eye3[2]) < 0.004]
    lvl = lvl[np.abs(lvl[:, 0]) < 0.01]
    y_mid = 0.5 * (float(lvl[:, 1].min()) + float(lvl[:, 1].max())) if len(lvl) > 1 else axis_y
    centre = np.array([0.0, y_mid, float(eye3[2]) - 0.12 * head_h])
    fr.ell_c, fr.ell_r, rms = fit_face_ellipsoid(P[sel], centre)
    fr.world_polys()
    print(f"[mh_face] {b.id}: sculpt face: socket {sock_w * 100:.1f} x {sock_h * 100:.1f} cm, eye scale {k:.2f}, "
          f"mouth scale {km:.2f}, eye {np.round(eye, 4)}, ellipsoid c={np.round(fr.ell_c, 4)} r={np.round(fr.ell_r, 4)} "
          f"rms={rms:.4f} n={int(sel.sum())}", flush=True)
    return fr

# ----------------------------------------------------------------------------
# 1. Shape
# ----------------------------------------------------------------------------


def zone_weights(fr: FaceFrame, P: np.ndarray) -> np.ndarray:
    q = fr.uv(P)
    w = np.zeros(len(P))
    for side in (1, -1):
        c = fr.eye * np.array([side, 1.0])
        r = np.sqrt(((q[:, 0] - c[0]) / 0.025) ** 2 + ((q[:, 1] - c[1]) / 0.016) ** 2)
        w = np.maximum(w, ZONE_PULL["eye"] * (1.0 - _smooth((r - 1.0) / 0.55)))
    # Nose: from the bridge between the eyes to just under the tip.
    v_top, v_bot = fr.eye[1] + 0.004, fr.nose[1] - 0.009
    inside_v = 1.0 - _smooth((q[:, 1] - v_top) / 0.012) - _smooth((v_bot - q[:, 1]) / 0.006)
    nose = np.clip(inside_v, 0, 1) * (1.0 - _smooth((np.abs(q[:, 0]) - 0.011) / 0.010))
    w = np.maximum(w, ZONE_PULL["nose"] * nose)
    rm = np.sqrt((q[:, 0] / 0.021) ** 2 + ((q[:, 1] - fr.mouth[1]) / 0.0085) ** 2)
    w = np.maximum(w, ZONE_PULL["mouth"] * (1.0 - _smooth((rm - 1.0) / 0.7)))
    return w * fr.front(P)


def membrane(co: np.ndarray, faces, free: np.ndarray, exclude: np.ndarray, iters: int = 400) -> np.ndarray:
    """Harmonic fill: the `free` vertices relaxed to the mean of their
    neighbours (the others fixed), as if the nose, lips and eye sockets were
    a stretched membrane spanning the surrounding face. `exclude` vertices
    (inside of the mouth) are not neighbours."""
    n = len(co)
    ex = np.zeros(n, dtype=bool)
    ex[exclude] = True
    nb: dict[int, set] = {}
    is_free = np.zeros(n, dtype=bool)
    is_free[free] = True
    for f in faces:
        for i in range(len(f)):
            a, c = f[i], f[(i + 1) % len(f)]
            if is_free[a] and not ex[c]:
                nb.setdefault(a, set()).add(c)
            if is_free[c] and not ex[a]:
                nb.setdefault(c, set()).add(a)
    ids = np.array([i for i in free if i in nb and not ex[i]], dtype=np.int64)
    deg = max((len(nb[i]) for i in ids), default=1)
    tab = np.zeros((len(ids), deg), dtype=np.int64)
    msk = np.zeros((len(ids), deg))
    for r, i in enumerate(ids):
        nn = sorted(nb[i])
        tab[r, : len(nn)] = nn
        msk[r, : len(nn)] = 1.0
    msk /= np.maximum(msk.sum(axis=1, keepdims=True), 1.0)
    X = co.copy()
    for _ in range(iters):
        X[ids] = np.einsum("nk,nkd->nd", msk, X[tab])
    return X


def anime_offset(fr: FaceFrame, co: np.ndarray, skin_ids: np.ndarray, inner_ids: np.ndarray,
                 faces=None, seam: np.ndarray | None = None) -> np.ndarray:
    """Per-vertex offset (len(co), 3) flattening the eye sockets, the nose
    and the lips: each zone is blended onto a harmonic membrane spanning the
    face around it (or onto the face ellipsoid without `faces`).
    `inner_ids` (inside of the mouth) follow the nearest outer vertex so the
    cavity stays behind the lips."""
    D = np.zeros_like(co)
    P = co[skin_ids]
    w = zone_weights(fr, P)
    if faces is not None:
        free = skin_ids[w > 0.0]
        target = membrane(co, faces, free, np.asarray(inner_ids, dtype=np.int64))[skin_ids]
    else:
        target = fr.ell_point(P)
    D[skin_ids] = w[:, None] * (target - P)
    inner = np.asarray(inner_ids, dtype=np.int64)
    if len(inner):
        outer = np.asarray(seam, dtype=np.int64) if seam is not None and len(seam) else \
            np.setdiff1d(skin_ids[w > 0.05], inner)
        if len(outer):
            from mathutils import Vector
            from mathutils.kdtree import KDTree

            kd = KDTree(len(outer))
            for i, vid in enumerate(outer):
                kd.insert(Vector(co[vid]), i)
            kd.balance()
            outer_set = set(int(v) for v in outer)
            for vid in inner:
                if int(vid) in outer_set:
                    continue  # the seam itself stays with the lips
                _, j, _ = kd.find(Vector(co[vid]))
                # The closed mouth's inside collapses onto the nearest lip-seam
                # vertex (just behind it), so nothing shows between the
                # flattened lips; mouth_open still moves it by its own delta.
                o = outer[j]
                D[vid] = co[o] + D[o] + np.array([0.0, INNER_MOUTH_BACK, 0.0]) - co[vid]
    return D


# ----------------------------------------------------------------------------
# 2. Features: refine, cut, paint
# ----------------------------------------------------------------------------


# The feature outlines cut into the mesh (character look: the rest is texture).
CUT_POLYS = ("eye_all", "eye_open", "mouth")
OUTLINE_TOL = 0.00018  # outline points closer than this to the chord of their neighbours are merged away
ZONE_EDGE = {"eye": 0.0017, "brow": 0.0019, "mouth": 0.0009, "nose": 0.0022}


def _boxes(fr: FaceFrame) -> list[tuple[float, float, float, float, float]]:
    """(u0, u1, v0, v1, longest edge) areas refined before cutting: the
    bounding box of each feature group plus a small margin."""
    groups: dict[str, list[np.ndarray]] = {}
    zone = fr.st.get("zone_edge", ZONE_EDGE)
    for name, poly in fr.polys.items():
        base = name.rsplit("_", 1)
        if name in ("mouth", "nose"):
            key = name
        elif base[0] == "brow":
            key = "brow_" + base[1]
        elif base[0] == "crease" and "crease" in zone:
            key = "crease_" + base[1]  # a thin stroke: refined on its own, finer
        else:
            key = "eye_" + base[1]
        groups.setdefault(key, []).append(poly)
    out = []
    m = 0.0008
    for key, polys in sorted(groups.items()):
        P = np.vstack(polys)
        out.append((float(P[:, 0].min()) - m, float(P[:, 0].max()) + m, float(P[:, 1].min()) - m,
                    float(P[:, 1].max()) + m, zone[key.split("_")[0]], polys))
    return out


def _earclip(Q: np.ndarray) -> list[tuple[int, int, int]]:
    """Ear-clipping triangulation of a simple polygon (n, 2), either winding."""
    n = len(Q)
    if n == 3:
        return [(0, 1, 2)]
    area = 0.5 * float(np.sum(Q[:, 0] * np.roll(Q[:, 1], -1) - np.roll(Q[:, 0], -1) * Q[:, 1]))
    sgn = 1.0 if area >= 0 else -1.0
    idx = list(range(n))
    tris = []
    guard = 0

    def flat(k: int, ids: list[int]) -> bool:
        a, b, c = Q[ids[k - 1]], Q[ids[k]], Q[ids[(k + 1) % len(ids)]]
        cr = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        e2 = max(np.sum((b - a) ** 2), np.sum((c - b) ** 2), np.sum((a - c) ** 2))
        return cr <= 1e-3 * e2
    while len(idx) > 3 and guard < 2 * n:
        guard += 1
        m = len(idx)
        best, best_q = None, -1.0
        for k in range(m):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = Q[i0], Q[i1], Q[i2]
            cr = sgn * ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
            e2 = max(np.sum((b - a) ** 2), np.sum((c - b) ** 2), np.sum((a - c) ** 2))
            if cr <= 1e-3 * e2:
                continue  # (nearly) collinear: an inserted midpoint is never an ear tip
            ok = True
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                p = Q[j]
                d1 = sgn * ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]))
                d2 = sgn * ((c[0] - b[0]) * (p[1] - b[1]) - (c[1] - b[1]) * (p[0] - b[0]))
                d3 = sgn * ((a[0] - c[0]) * (p[1] - c[1]) - (a[1] - c[1]) * (p[0] - c[0]))
                if d1 >= 0 and d2 >= 0 and d3 >= 0:
                    ok = False
                    break
            if not ok:
                continue
            # Prefer well-shaped ears (area over squared longest edge).
            e = max(np.sum((b - a) ** 2), np.sum((c - b) ** 2), np.sum((a - c) ** 2))
            q = cr / max(e, 1e-20)
            # Ears that consume a flat (inserted) neighbour first, so none is
            # left over in a degenerate last triangle.
            if flat(k - 1, idx) or flat((k + 1) % m, idx):
                q += 10.0
            if q > best_q:
                best, best_q = k, q
        if best is None:
            break
        m = len(idx)
        tris.append((idx[best - 1], idx[best], idx[(best + 1) % m]))
        idx.pop(best)
    if len(idx) == 3:
        tris.append(tuple(idx))
    elif len(idx) > 3:  # degenerate leftovers: fan
        tris += [(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)]
    return tris


def triangulate(cm, fr: FaceFrame, sel: np.ndarray) -> None:
    """Replace the selected faces of `cm` by triangles (ear clipping in the
    face projection, so collinear inserted vertices never make slivers)."""
    sel = set(np.nonzero(sel)[0].tolist())
    if not sel:
        return
    P = cm.base
    faces, attrs = [], {k: [] for k in cm.attrs}
    for fi, f in enumerate(cm.faces):
        if fi in sel and len(f) == 4:
            q = fr.uv(P[list(f)])

            def cr(a, b, c):
                return (q[b, 0] - q[a, 0]) * (q[c, 1] - q[a, 1]) - (q[b, 1] - q[a, 1]) * (q[c, 0] - q[a, 0])

            s02 = cr(0, 1, 2) * cr(0, 2, 3) > 0
            s13 = cr(1, 2, 3) * cr(1, 3, 0) > 0
            if s02 and (not s13 or np.sum((q[0] - q[2]) ** 2) <= np.sum((q[1] - q[3]) ** 2)):
                parts = [(f[0], f[1], f[2]), (f[0], f[2], f[3])]
            else:
                parts = [(f[1], f[2], f[3]), (f[1], f[3], f[0])]
        elif fi in sel and len(f) > 4:
            q = fr.uv(P[list(f)])
            parts = [tuple(f[i] for i in t) for t in _earclip(q)]
        else:
            parts = [f]
        for t in parts:
            faces.append(t)
            for k in attrs:
                attrs[k].append(cm.attrs[k][fi])
    cm.faces, cm.attrs = faces, attrs


def refine(cm, fr: FaceFrame, sel_fn, max_edge: float, rounds: int = 4) -> int:
    """Split every edge longer than `max_edge` (face projection) among the
    faces `sel_fn(centroid_uv, face_index)` picks, inserting the midpoint into
    every face that shares the edge (no T-junctions), and re-triangulate."""
    added = 0
    for _ in range(rounds):
        C = cm.centroids()
        sel = sel_fn(fr.uv(C))
        triangulate(cm, fr, sel)
        C = cm.centroids()
        sel = np.nonzero(sel_fn(fr.uv(C)))[0]
        Q = fr.uv(cm.base)
        long_edges = {}
        for fi in sel:
            f = cm.faces[fi]
            for i in range(len(f)):
                a, b = f[i], f[(i + 1) % len(f)]
                key = (min(a, b), max(a, b))
                if key not in long_edges and np.sum((Q[a] - Q[b]) ** 2) > max_edge * max_edge:
                    long_edges[key] = (a, b, 0.5)
        if not long_edges:
            break
        added += _insert(cm, long_edges)
    C = cm.centroids()
    triangulate(cm, fr, sel_fn(fr.uv(C)))
    return added


def _insert(cm, edges: dict) -> int:
    """New vertices on `edges` ({key: (a, b, t)}), inserted into every face ring."""
    items = list(edges.items())
    ia = np.array([c[0] for _, c in items], dtype=np.int64)
    ib = np.array([c[1] for _, c in items], dtype=np.int64)
    tt = np.array([c[2] for _, c in items])[:, None]
    n0 = len(cm.weights)
    for k, v in cm.shapes.items():
        cm.shapes[k] = np.vstack([v, v[ia] + (v[ib] - v[ia]) * tt])
    for _, (a, b, t) in items:
        wa, wb = cm.weights[a], cm.weights[b]
        cm.weights.append({n: wa.get(n, 0.0) * (1 - t) + wb.get(n, 0.0) * t for n in set(wa) | set(wb)})
    new = {key: n0 + i for i, (key, _) in enumerate(items)}
    if not hasattr(cm, "splits"):
        cm.splits = {}
    cm.splits.update(new)
    touched = set(ia.tolist()) | set(ib.tolist())
    faces = []
    for f in cm.faces:
        if touched.isdisjoint(f):
            faces.append(f)
            continue
        ring = []
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            ring.append(a)
            v = new.get((min(a, b), max(a, b)))
            if v is not None:
                ring.append(v)
        faces.append(tuple(ring))
    cm.faces = faces
    return len(items)


def _zone(fr: FaceFrame, q: np.ndarray, margin: float = 0.0) -> np.ndarray:
    """Face-uv points near any feature (the refining zone)."""
    out = np.zeros(len(q), dtype=bool)
    for (u0, u1, v0, v1, g, _) in _boxes(fr):
        out |= (q[:, 0] > u0 - margin) & (q[:, 0] < u1 + margin) & (q[:, 1] > v0 - margin) & (q[:, 1] < v1 + margin)
    return out


def _padded(faces) -> np.ndarray:
    """Faces as an (n, k) index array, short rings padded with their first vertex."""
    k = max(len(f) for f in faces)
    return np.array([tuple(f) + (f[0],) * (k - len(f)) for f in faces], dtype=np.int64)


def _face_bounds(cm, fr: FaceFrame) -> tuple[np.ndarray, np.ndarray]:
    Q = fr.uv(cm.base)[_padded(cm.faces)]
    return Q.min(axis=1), Q.max(axis=1)


def cut_features(cm, fr: FaceFrame, head_ok="head") -> int:
    """Refine around every feature, then cut each outline into `cm`
    (mh_paint.CutMesh). `head_ok`: "head" (faces of that fit part) or a
    function of the face index. The work happens on the front of the head
    only (a sub-mesh, merged back), whose border is never touched."""
    from .mh_paint import CutMesh

    part = np.asarray(cm.attrs["part"], dtype=object)
    ok = (part == "head") if head_ok == "head" else np.array([head_ok(i) for i in range(len(cm.faces))])
    ok &= fr.front(cm.centroids())
    if "flags" in cm.attrs:  # the inside of the mouth never carries paint
        ok &= ~np.array([bool(f.get("inside-mouth")) for f in cm.attrs["flags"]])
    sel = np.nonzero(ok)[0]
    used = np.array(sorted({v for i in sel for v in cm.faces[i]}), dtype=np.int64)
    outside = {v for i in np.nonzero(~ok)[0] for v in cm.faces[i]}
    remap = {int(v): k for k, v in enumerate(used)}
    sub = CutMesh({k: v[used] for k, v in cm.shapes.items()}, [cm.weights[v] for v in used],
                  [tuple(remap[v] for v in cm.faces[i]) for i in sel], {k: [v[i] for i in sel] for k, v in cm.attrs.items()})
    frozen = np.zeros(len(used), dtype=bool)  # border edges may split: merged into the outside faces below
    del outside
    sub.splits = {}
    # Only the outlines the expression keys need are cut (the eye opening and
    # the lash band: the blink carries the band down onto the lower lid; the
    # mouth line: mouth_open stretches it); every other feature is painted in
    # the face texture (face_paint.py).
    full = fr.polys
    fr.polys = {k: v for k, v in full.items() if k.rsplit("_", 1)[0] in CUT_POLYS or k in CUT_POLYS}
    try:
        total = _cut_sub(sub, fr, frozen)
    finally:
        fr.polys = full
    # Merge back: kept vertices map to their old index, new ones are appended.
    n_main, n_used = len(cm.base), len(used)
    back = np.concatenate([used, n_main + np.arange(len(sub.base) - n_used)])
    for k in cm.shapes:
        cm.shapes[k] = np.vstack([cm.shapes[k], sub.shapes[k][n_used:]])
    cm.weights.extend(sub.weights[n_used:])
    keep = np.nonzero(~ok)[0]
    splits = {(min(int(back[a]), int(back[b])), max(int(back[a]), int(back[b]))): int(back[m])
              for (a, b), m in sub.splits.items()}

    def expand(a, b):
        m = splits.get((min(a, b), max(a, b)))
        return [] if m is None else expand(a, m) + [m] + expand(m, b)

    outer = []
    for i in keep:
        f = cm.faces[i]
        ring = []
        for j in range(len(f)):
            ring.append(f[j])
            ring += expand(f[j], f[(j + 1) % len(f)])
        outer.append(tuple(ring))
    cm.faces = outer + [tuple(int(back[v]) for v in f) for f in sub.faces]
    cm.attrs = {k: [v[i] for i in keep] + sub.attrs[k] for k, v in cm.attrs.items()}
    return total


def _cut_sub(cm, fr: FaceFrame, frozen: np.ndarray) -> int:
    total = 0

    def base_ok():
        n = len(cm.base)
        fz = np.zeros(n, dtype=bool)
        fz[: len(frozen)] = frozen
        P = _padded(cm.faces)
        return ~fz[P].any(axis=1)

    for (u0, u1, v0, v1, edge, polys) in _boxes(fr):
        def sel_fn(q, u0=u0, u1=u1, v0=v0, v1=v1, polys=polys):
            # Faces overlapping the box (a small box may sit inside one large
            # face) and close to one of its outlines: flat interiors stay
            # coarse.
            lo, hi = _face_bounds(cm, fr)
            out = base_ok() & (hi[:, 0] > u0) & (lo[:, 0] < u1) & (hi[:, 1] > v0) & (lo[:, 1] < v1)
            idx = np.nonzero(out)[0]
            if len(idx):
                diag = np.linalg.norm(hi[idx] - lo[idx], axis=1)
                d = np.min([np.abs(poly_sdf(q[idx], pl)) for pl in polys], axis=0)
                out[idx] = d < 0.6 * diag + 0.0005
            return out

        total += refine(cm, fr, sel_fn, edge, rounds=6)
    for name in sorted(fr.polys):
        poly = fr.polys[name]
        C = cm.centroids()
        d = poly_sdf(fr.uv(C), poly)
        ok = base_ok() & (np.abs(d) < 0.004)
        triangulate(cm, fr, ok)
        C = cm.centroids()
        d = poly_sdf(fr.uv(C), poly)
        ok = base_ok() & (np.abs(d) < 0.004)

        def field(P, poly=poly):
            out = np.full(len(P), 1.0)
            f = fr.front(P)
            out[f] = poly_sdf(fr.uv(P[f]), poly)
            return out

        total += cm.cut(field, ok, snap=CUT_SNAP)
    C = cm.centroids()
    triangulate(cm, fr, base_ok() & _zone(fr, fr.uv(C)))
    _drop_degenerate(cm)
    return total


def _drop_degenerate(cm) -> None:
    """Remove repeated ring vertices, faces left with fewer than three, and
    zero-area pockets (the same triangle from both sides of a split edge)."""
    from collections import Counter

    count = Counter(tuple(sorted(f)) for f in cm.faces)
    faces, attrs = [], {k: [] for k in cm.attrs}
    for fi, f in enumerate(cm.faces):
        ring = [v for i, v in enumerate(f) if v != f[i - 1]]
        if len(set(ring)) != len(ring) or len(ring) < 3 or count[tuple(sorted(f))] > 1:
            continue
        faces.append(tuple(ring))
        for k in attrs:
            attrs[k].append(cm.attrs[k][fi])
    cm.faces, cm.attrs = faces, attrs


def feature_of(fr: FaceFrame, q: np.ndarray) -> list[tuple[str, tuple] | None]:
    """Swatch and mask of face centroids `q` (face uv) inside a feature."""
    P = fr.polys
    ins = {k: poly_sdf(q, v) < 0 for k, v in P.items()}
    out: list = [None] * len(q)
    for i in range(len(q)):
        for tag in ("L", "R"):
            if ins[f"eye_open_{tag}"][i]:
                if ins[f"hl1_{tag}"][i] or ins[f"hl2_{tag}"][i]:
                    out[i] = ("eye_hl", NONE)
                elif ins[f"pupil_{tag}"][i]:
                    out[i] = ("pupil", NONE)
                elif ins[f"iris_{tag}"][i]:
                    if not ins[f"iris_in_{tag}"][i]:
                        out[i] = ("iris_rim", NONE)
                    elif ins[f"iris_top_{tag}"][i]:
                        out[i] = ("iris_dark", EYE)
                    elif ins[f"iris_low_{tag}"][i]:
                        out[i] = ("iris_light", EYE)
                    else:
                        out[i] = ("iris", EYE)
                elif ins[f"shade_{tag}"][i]:
                    out[i] = ("sclera_shade", NONE)
                else:
                    out[i] = (SCLERA, NONE)
                break
            if ins[f"eye_all_{tag}"][i]:
                out[i] = ("lash", NONE)
                break
            if ins[f"brow_{tag}"][i]:
                out[i] = ("brow", HAIR)
                break
            if ins[f"crease_{tag}"][i]:
                out[i] = ("skin_deep", SKIN)
                break
        if out[i] is None:
            if ins["mouth"][i]:
                out[i] = ("mouth_line", NONE)
            elif ins["nose"][i]:
                out[i] = ("skin_deep", SKIN)
    return out


# Face-texture paint: the cut areas keep a swatch of their own (so their
# outlines stay paint borders the clean-up and the decimation keep) but every
# head face carries the skin mask; the colours come from the face texture.
TEXTURE_PAINT = {"eye_open": "sclera", "eye_all": "lash", "mouth": "mouth_line"}


def texture_feature_of(fr: FaceFrame, q: np.ndarray) -> list[str | None]:
    """The cut area of face centroids `q` (face uv), or None."""
    P = fr.polys
    out: list = [None] * len(q)
    for tag in ("L", "R"):
        o = poly_sdf(q, P[f"eye_open_{tag}"]) < 0
        a = poly_sdf(q, P[f"eye_all_{tag}"]) < 0
        for i in np.nonzero(a)[0]:
            out[i] = TEXTURE_PAINT["eye_open"] if o[i] else TEXTURE_PAINT["eye_all"]
    m = poly_sdf(q, P["mouth"]) < 0
    for i in np.nonzero(m)[0]:
        if out[i] is None:
            out[i] = TEXTURE_PAINT["mouth"]
    return out


def paint_features(cm, fr: FaceFrame, regions: list[str], swatches: list[str], masks: list[tuple]) -> None:
    """Every head face takes the skin mask (the face texture paints the
    features, face_paint.py); the faces inside the cut outlines keep a swatch
    of their own so the outlines survive as paint borders."""
    C = cm.centroids()
    head = np.array([r == "head" for r in regions]) & fr.front(C)
    idx = np.nonzero(head)[0]
    feats = texture_feature_of(fr, fr.uv(C[idx]))
    for i, ft in zip(idx, feats):
        swatches[i], masks[i] = (ft if ft is not None else "skin"), SKIN
    for i, r in enumerate(regions):
        if r == "head" and not head[i]:
            swatches[i], masks[i] = "skin", SKIN


# ----------------------------------------------------------------------------
# Eye keys in the eye frame
# ----------------------------------------------------------------------------


def _vertex_normals(P: np.ndarray, faces) -> np.ndarray:
    n = np.zeros_like(P)
    for f in faces:
        p = P[list(f)]
        fn = np.zeros(3)
        for i in range(len(f)):
            fn += np.cross(p[i], p[(i + 1) % len(f)])
        n[list(f)] += fn
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def _move_vertical(P: np.ndarray, N: np.ndarray, dz: np.ndarray) -> np.ndarray:
    """Move points by dz along the surface (vertical plane, tangent)."""
    ny = np.where(np.abs(N[:, 1]) < 0.2, -0.2, N[:, 1])
    dy = N[:, 2] * dz / (-ny)
    out = P.copy()
    out[:, 1] += np.clip(dy, -np.abs(dz) * 1.5, np.abs(dz) * 1.5)
    out[:, 2] += dz
    return out


def eye_keys(fr: FaceFrame, P: np.ndarray, faces, movable: np.ndarray) -> dict[str, np.ndarray]:
    """blink_l / blink_r (painted eye closes onto the lower lid), and
    face_eyes_narrow / face_eyes_round (eye opening scaled vertically)."""
    st = fr.st
    k = fr.k
    e = EyeShape(st)
    # Smooth surface normals (the face ellipsoid's): the cut mesh's own
    # vertex normals are noisy around the slivers of the outlines.
    g = (P - fr.ell_c) / fr.ell_r ** 2
    N = g / np.maximum(np.linalg.norm(g, axis=1, keepdims=True), 1e-12)
    out = {}
    for side, tag in ((1, "l"), (-1, "r")):
        loc = fr.eye_local(P, side)
        s, t = loc[:, 0], loc[:, 1]
        sc = np.clip(s, e.s_in, e.s_out)
        lo = e.lower(sc)
        up = e.upper(sc)
        lash_top = up + e.lash_th(sc)
        # Horizontal falloff past the corners (and the wing).
        fs = 1.0 - _smooth((np.maximum(e.s_in - s, s - (e.s_out + st["wing"])) ) / (0.004 * k))
        new_t = t.copy()
        keep = st.get("blink_keep", 0.04)  # share of the opening left between the closed lids
        band = lash_top + st.get("blink_band", 0.0005)  # moves rigidly with the lash line
        a = (t > lo) & (t <= up)
        new_t[a] = lo[a] + (t[a] - lo[a]) * keep
        b = (t > up) & (t <= band)
        new_t[b] = lo[b] + (up[b] - lo[b]) * keep + (t[b] - up[b])
        c = t > band
        drop = (up - lo) * (1.0 - keep)
        flen = 0.0045 * k
        if "brow_low" in st:  # fade out below the painted brow, whatever the brow gap
            flen = np.clip((st["brow_low"] - band) * 0.75, 0.0006, flen)
        fade = 1.0 - _smooth((t - band) / flen)  # the brow (4-8 mm up) stays put
        new_t[c] = t[c] - (drop * fade)[c]
        dz = (new_t - t) * fs * movable * (t > lo - 0.0001) * fr.front(P)
        # Never lift: below the lower lid nothing moves.
        out[f"blink_{tag}"] = _move_vertical(P, N, dz)
    for name, k in (("face_eyes_narrow", 0.78), ("face_eyes_round", 1.16)):
        Q = P.copy()
        for side in (1, -1):
            loc = fr.eye_local(Q, side)
            s, t = loc[:, 0], loc[:, 1]
            r = np.sqrt((s / (0.024 * k)) ** 2 + (t / (0.016 * k)) ** 2)
            w = (1.0 - _smooth((r - 0.75) / 0.6)) * movable * fr.front(Q)
            dz = (t * (k - 1.0)) * w
            Q = _move_vertical(Q, N, dz)
        out[name] = Q
    # Open mouth: the painted mouth line's lower edge drops into a dark oval
    # (the line's faces stretch), the lower lip and chin follow.
    mw, mt = st["mouth_w"], st["mouth_th"]
    q = fr.uv(P)
    u, v = q[:, 0], q[:, 1]
    mc = fr.mouth[1] + st["mouth_curve"] * (2 * u / mw) ** 2 + st["mouth_dv"]
    shape = np.clip(1.0 - (2 * u / (mw * 1.05)) ** 2, 0.0, 1.0) ** 0.7
    side = 1.0 - _smooth((np.abs(u) - 0.5 * mw) / 0.008)
    below = (v < mc - 0.00005)
    depth = mc - v
    g = 1.0 - _smooth((depth - 0.006) / 0.03)
    amount = st.get("mouth_open", MOUTH_OPEN) * np.maximum(shape, 0.35 * side) * g * below * movable * fr.front(P)
    out["mouth_open"] = _move_vertical(P, N, -amount)
    return out


# ----------------------------------------------------------------------------
# 3. Face normals
# ----------------------------------------------------------------------------


def normal_weight(fr: FaceFrame, P: np.ndarray) -> np.ndarray:
    """1 over the face (forehead to chin, inside the cheeks), 0 toward the
    jaw line, ears, hairline and neck."""
    ang = np.abs(fr.angle(P))
    b = fr.b
    z = P[:, 2]
    w = 1.0 - _smooth((ang - 62.0) / 25.0)
    w *= 1.0 - _smooth((fr.chin_z + 0.012 * b.head_scale - z) / (0.025 * b.head_scale))
    w *= 1.0 - _smooth((z - (fr.eye[1] + 0.045 * b.head_scale)) / (0.03 * b.head_scale))
    return np.clip(w, 0.0, 1.0) * (P[:, 1] < fr.axis_y)


def face_normals(obj, fr: FaceFrame, strength: float = 1.0) -> None:
    """Blend the head object's normals toward the face ellipsoid's
    (custom split normals, exported by glTF)."""
    me = obj.data
    nl = len(me.loops)
    if not nl:
        return
    co = np.zeros(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(obj.matrix_world)
    co_w = co @ mw[:3, :3].T + mw[:3, 3]
    cur = np.zeros(nl * 3, dtype=np.float32)
    me.corner_normals.foreach_get("vector", cur)
    cur = cur.reshape(-1, 3).astype(np.float64)
    vi = np.zeros(nl, dtype=np.int64)
    me.loops.foreach_get("vertex_index", vi)
    en = fr.ell_normal(co_w)
    w = normal_weight(fr, co_w) * strength
    n = cur * (1 - w[vi, None]) + en[vi] * w[vi, None]
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    me.normals_split_custom_set(n.tolist())


# ----------------------------------------------------------------------------
# Clean-up after painting: drop the refining vertices again
# ----------------------------------------------------------------------------


def simplify(verts: np.ndarray, faces: list[tuple[int, ...]], paint: list, removable: np.ndarray,
             fr: "FaceFrame | None" = None, movable: np.ndarray | None = None, tol: float = OUTLINE_TOL
             ) -> tuple[list[tuple[int, ...]], list[int]]:
    """Dissolve the `removable` vertices (refining points that ended up on no
    paint or region border, so every face around each has the same paint),
    then triangulate. The refining points lie on the original faces, so the
    shape is unchanged; only the outline points stay. Returns the new faces
    (old vertex ids) and, per new face, the index of an old face it came from
    (for its paint)."""
    import bmesh

    bm = bmesh.new()
    vid = bm.verts.layers.int.new("vid")
    fid = bm.faces.layers.int.new("fid")
    bv = []
    for i, p in enumerate(verts):
        v = bm.verts.new(tuple(p))
        v[vid] = i
        bv.append(v)
    bm.verts.ensure_lookup_table()
    for i, f in enumerate(faces):
        try:
            nf = bm.faces.new([bv[j] for j in f])
        except ValueError:
            continue
        nf[fid] = i
    # Only dissolve where the surrounding faces share one paint (safety).
    kill = []
    for i in np.nonzero(removable)[0]:
        v = bv[int(i)]
        if not v.is_valid or not v.link_faces or v.is_boundary:
            continue
        keys = {paint[f[fid]] for f in v.link_faces}
        if len(keys) == 1:
            kill.append(v)
    if kill:
        bmesh.ops.dissolve_verts(bm, verts=kill, use_face_split=False, use_boundary_tear=False)
    ngons = [f for f in bm.faces if len(f.verts) > 3]
    if ngons:
        bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")
    if fr is not None and movable is not None:
        _thin_outlines(bm, vid, fid, paint, fr, movable, tol)
    out_f, out_src = [], []
    for f in bm.faces:
        if len(f.verts) < 3:
            continue
        out_f.append(tuple(v[vid] for v in f.verts))
        out_src.append(f[fid])
    bm.free()
    return out_f, out_src


def _thin_outlines(bm, vid, fid, paint, fr: FaceFrame, movable: np.ndarray, tol: float) -> int:
    """Douglas-Peucker-like thinning of the paint outlines: an outline point
    whose two outline neighbours it barely deviates from (`tol`, face
    projection) is merged into one of them, unless that would fold a
    triangle. Only `movable` points (made by the cuts) are merged."""
    import bmesh

    def key(f):
        return paint[f[fid]]

    def uvp(v):
        return fr.uv(np.array([tuple(v.co)]))[0]

    merged = 0
    for _ in range(6):
        cands = []
        for v in bm.verts:
            if not v.is_valid or not movable[v[vid]] or v.is_boundary:
                continue
            bes = [e for e in v.link_edges if len(e.link_faces) == 2 and key(e.link_faces[0]) != key(e.link_faces[1])]
            if len(bes) != 2:
                continue
            a, b = (e.other_vert(v) for e in bes)
            pa, pb, pv = uvp(a), uvp(b), uvp(v)
            ab = pb - pa
            L = float(np.linalg.norm(ab))
            if L < 1e-9 or L > 0.004:
                continue
            dev = abs(ab[0] * (pv[1] - pa[1]) - ab[1] * (pv[0] - pa[0])) / L
            if dev < tol:
                cands.append((dev, v, a))
        cands.sort(key=lambda c: c[0])
        touched = set()
        done = 0
        for dev, v, a in cands:
            if not (v.is_valid and a.is_valid) or v.index in touched or a.index in touched:
                continue
            # Would moving v onto a fold any of v's other triangles?
            pa = uvp(a)
            ok = True
            for f in v.link_faces:
                if a in f.verts:
                    continue
                pts = [uvp(x) for x in f.verts]
                new = [pa if x == v else p for x, p in zip(f.verts, pts)]

                def area(q):
                    return sum(q[i][0] * q[(i + 1) % len(q)][1] - q[(i + 1) % len(q)][0] * q[i][1] for i in range(len(q)))

                a0, a1 = area(pts), area(new)
                if a0 * a1 <= 0 or abs(a1) < 1e-11:
                    ok = False
                    break
            if not ok:
                continue
            for x in [v, a] + [e.other_vert(v) for e in v.link_edges]:
                touched.add(x.index)
            keep_id = a[vid]
            bmesh.ops.pointmerge(bm, verts=[v, a], merge_co=a.co.copy())
            for x in (v, a):
                if x.is_valid:
                    x[vid] = keep_id  # the survivor is a (its position is a's in every shape)
            done += 1
        bm.verts.index_update()
        merged += done
        if not done:
            break
    # Faces that collapsed to lines.
    bad = [f for f in bm.faces if f.calc_area() < 1e-12]
    if bad:
        bmesh.ops.delete(bm, geom=bad, context="FACES_ONLY")
    return merged
