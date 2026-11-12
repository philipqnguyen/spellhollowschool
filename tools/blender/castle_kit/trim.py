"""The castle kit's sheet textures (castle kit M1, numpy only, no `bpy`):
the trim sheet `trim_castle` and the other sheets laid out by
data/art/castle_textures.json `sheets` (`wood_sheet`, `glass_sheet`,
`banners`).

Pieces map faces onto the sheets with `grid.strip_uv` (a strip: `s` metres
along the run, `p` metres along the moulding's profile from its bottom) and
`grid.tile_uv` (a tile: the face's own 0..1). This module paints the same
layout from the other side: every strip is painted as a function of
(s, p) in metres, every tile as a function of (a, b) in 0..1, at exactly
the pixels those formulas sample. The pad rows and columns repeat the
strip's edge (p and a, b are clamped there too), so mipmaps do not bleed
between strips. Full-width strips repeat seamlessly in u (every `u_m`
metres); joints fall on whole divisions of `u_m`.

The look (docs/art/style-guide.md, the anime look): flat bands of colour
for the profile's planes (lit on the upper and left faces, shaded on the
lower and right), crisp creases, one tone per stone, no fine noise. Since
the toon converter drops normal maps, the painted bands are what reads as
relief between the outlines. Arrays follow paint.py: float32 RGBA,
sRGB-encoded, row 0 at the BOTTOM of the image; the sheets' `rows` count
from the top, so `Sheet` converts.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORLD = os.path.join(os.path.dirname(_HERE), "world")
if _WORLD not in sys.path:
    sys.path.insert(0, _WORLD)
import paint  # noqa: E402
from paint import band, hex_rgb, smoothstep  # noqa: E402

# Colours (sRGB), matched to world/palette.py CASTLE: cream sandstone
# trims (a touch below `stone_trim` so the lit bands stay inside the
# albedo range), oak, iron, marble, the house slots for the banners.
TRIM = "#DDCBA9"
TRIM_DARK = "#CDB894"  # plinths and bases: the wall's footing
MORTAR = "#A8947A"
MARBLE = "#ECE3D2"
MARBLE_VEIN = "#C9C0B6"
IRON = "#4A4C5E"
GOLD = "#E2B24E"
OAK = "#8E5634"
OAK_PANEL = "#9A5F39"
OAK_RAIL = "#7C4729"
OAK_JOINT = "#4E2C1C"
LEAD = "#3F3B4C"
SHADOW = "#4E423E"  # painted openings (the LOD1 balustrade)
# Castle kit M4 (M2 leftover 4): the painted openings show a mid-tone, the
# floor seen through them in shade (textures.py's stone_flag_square base
# deepened toward SHADOW), not near black: at Low the forced LOD1 read darker
# than the floor seen between the LOD0 piers.
FLOOR_SEEN = "#AA9076"
OPENING_MIX = 0.45  # share of SHADOW in the opening tone
BANNER_M = (1.6, 6.0)  # kit_banner_<slot>: 1.6 x 6 m
HOUSES = {
    "a": ("#B0222A", "#E2B23A"),
    "b": ("#EDB72F", "#463A36"),
    "c": ("#24499A", "#B58542"),
    "d": ("#1F7444", "#C3C8CF"),
}


class Sheet:
    """A sheet image and its layout (one `sheets.<texture>` entry)."""

    def __init__(self, spec: dict, fill: str = TRIM):
        self.spec = spec
        self.w, self.h = spec["px"]
        self.pad = spec["pad"]
        self.img = np.ones((self.h, self.w, 4), dtype=np.float32)
        self.img[..., :3] = hex_rgb(fill)[None, None, :]

    # Strips -------------------------------------------------------------------

    def strip(self, name: str):
        """(rows, cols, S, P, ppm_u, ppm_v): the array slices of a strip and,
        per pixel, the metres along the run (S) and along the profile from
        its bottom (P, clamped to 0..metres), exactly as grid.strip_uv
        samples them; ppm_* are pixels per metre."""
        st = self.spec["strips"][name]
        top, bottom = st["rows"]
        c0, c1 = st.get("cols", [0, self.w])
        n, cw = bottom - top, c1 - c0
        rows = slice(self.h - bottom, self.h - top)
        cols = slice(c0, c1)
        j = np.arange(n, dtype=np.float64) + 0.5
        q = np.clip((j - self.pad) / (n - 2 * self.pad), 0.0, 1.0)
        i = np.arange(cw, dtype=np.float64) + 0.5
        if c0 == 0 and c1 == self.w:
            f = i / cw
            ppm_u = cw / st["u_m"]
        else:
            f = np.clip((i - self.pad) / (cw - 2 * self.pad), 0.0, 1.0)
            ppm_u = (cw - 2 * self.pad) / st["u_m"]
        S = np.broadcast_to((f * st["u_m"])[None, :], (n, cw)).astype(np.float32)
        P = np.broadcast_to((q * st["metres"])[:, None], (n, cw)).astype(np.float32)
        ppm_v = (n - 2 * self.pad) / st["metres"]
        return rows, cols, S, P, ppm_u, ppm_v

    def tile(self, name: str):
        """(rows, cols, A, B): a tile's slices and its face-local 0..1
        coordinates (a to the right, b up), as grid.tile_uv samples them."""
        x, y, tw, th = self.spec["tiles"][name]
        rows = slice(self.h - y - th, self.h - y)
        cols = slice(x, x + tw)
        a = np.clip((np.arange(tw) + 0.5 - self.pad) / (tw - 2 * self.pad), 0.0, 1.0)
        b = np.clip((np.arange(th) + 0.5 - self.pad) / (th - 2 * self.pad), 0.0, 1.0)
        A = np.broadcast_to(a[None, :], (th, tw)).astype(np.float32)
        B = np.broadcast_to(b[:, None], (th, tw)).astype(np.float32)
        return rows, cols, A, B

    def put(self, rows, cols, rgb: np.ndarray) -> None:
        self.img[rows, cols, :3] = np.clip(rgb, 0.0, 1.0)

    def done(self) -> np.ndarray:
        self.seal()
        return paint.fit_albedo(self.img)

    def seal(self) -> None:
        """Makes every strip's and tile's pad an exact copy of its edge
        pixels (strip_uv / tile_uv never sample the pad; mipmaps then blend
        the right colours). Full-width strips keep their u wrap: only their
        rows are padded."""
        for st in self.spec["strips"].values():
            top, bottom = st["rows"]
            c0, c1 = st.get("cols", [0, self.w])
            self._seal(self.h - bottom, self.h - top, c0, c1, not (c0 == 0 and c1 == self.w))
        for x, y, tw, th in self.spec["tiles"].values():
            self._seal(self.h - y - th, self.h - y, x, x + tw, True)

    def _seal(self, r0: int, r1: int, c0: int, c1: int, cols: bool) -> None:
        p, im = self.pad, self.img
        im[r0 : r0 + p, c0:c1] = im[r0 + p, c0:c1][None]
        im[r1 - p : r1, c0:c1] = im[r1 - p - 1, c0:c1][None]
        if cols:
            im[r0:r1, c0 : c0 + p] = im[r0:r1, c0 + p][:, None]
            im[r0:r1, c1 - p : c1] = im[r0:r1, c1 - p - 1][:, None]


# -- shared painting helpers ---------------------------------------------------------


def bands(P: np.ndarray, spec: list[tuple[float, float, float]]) -> np.ndarray:
    """Flat value offsets by profile height: [(p0, p1, value), ...]."""
    out = np.zeros_like(P)
    for p0, p1, v in spec:
        out = np.where((P >= p0) & (P < p1), np.float32(v), out)
    return out


def roll(x: np.ndarray, lit: float = 0.11, mid: float = 0.02, dark: float = -0.11) -> np.ndarray:
    """A round moulding in three flat tones across x in -1..1 (light from
    the upper left: the highlight sits a little toward -x)."""
    return np.where(np.abs(x + 0.15) < 0.38, lit, np.where(np.abs(x) < 0.8, mid, dark)).astype(np.float32)


def joints(S: np.ndarray, every: float, ppm_u: float, seed: int, period: float, var: float = 0.05,
           hue: float = 0.35, joint_px: float = 2.5):
    """Stone joints every `every` metres along S (the strip's period
    `period` holds a whole number of stones): per-stone value and hue
    multipliers (rgb), the mortar coverage, and edge bands (lit left, shaded
    right)."""
    n = int(round(period / every))
    if abs(n * every - period) > 1e-6:
        raise ValueError(f"joints every {every} m do not divide {period} m")
    k = np.floor(S / every).astype(np.int32) % n
    rng = np.random.default_rng(seed)
    tone = 1.0 + np.round(rng.normal(0.0, var, n) / 0.02) * 0.02
    tint = paint.hue_tint(rng.uniform(-hue, hue, n)) * tone[:, None]
    local = S - np.floor(S / every) * every
    dl = local * ppm_u
    dr = (every - local) * ppm_u
    d = np.minimum(dl, dr)
    mortar = band(d, joint_px / 2)
    edge = np.where(dl < dr, band(dl - joint_px / 2, 3.0) * 0.06, band(dr - joint_px / 2, 3.0) * -0.08)
    return tint[k].astype(np.float32), mortar.astype(np.float32), edge.astype(np.float32)


def stone(base: str, value: np.ndarray, tint=None, mortar=None, mortar_colour: str = MORTAR) -> np.ndarray:
    col = hex_rgb(base)[None, None, :] * (1.0 + value)[..., None]
    if tint is not None:
        col = col * tint
    if mortar is not None:
        col = col * (1 - mortar[..., None]) + hex_rgb(mortar_colour)[None, None, :] * mortar[..., None]
    return col


def wash(shape, seed: int, cells=(2, 12), amount: float = 0.03) -> np.ndarray:
    """Flat painted wash patches (stepped, not noisy), periodic across the
    sheet width so full-width strips still wrap."""
    n = paint.periodic_noise(shape[0], shape[1], cells, seed, 2)
    return (smoothstep(0.58, 0.62, n) * -amount).astype(np.float32)


def disc(X, Y, cx, cy, r, aa):
    """Coverage of a disc (coordinates in any unit; aa = one pixel in it)."""
    return np.clip((r - np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)) / aa + 0.5, 0.0, 1.0)


def mix(col, other, t):
    t = np.asarray(t, dtype=np.float32)[..., None]
    o = other if isinstance(other, np.ndarray) and other.ndim == 3 else np.asarray(other, np.float32)[None, None]
    return col * (1 - t) + o * t


# -- the trim sheet ------------------------------------------------------------------


def _moulding(sh: Sheet, name: str, base: str, profile, every: float | None, seed: int, extra=None, var=0.045):
    rows, cols, S, P, ppm_u, _ = sh.strip(name)
    st = sh.spec["strips"][name]
    v = bands(P, profile) + wash(S.shape, seed + 7, amount=0.025)
    if extra is not None:
        v = v + extra(S, P)
    tint = mortar = None
    if every:
        tint, mortar, edge = joints(S, every, ppm_u, seed, st["u_m"], var=var)
        v = v + edge * (P > 0.02)
    sh.put(rows, cols, stone(base, v, tint, mortar))


def paint_trim(spec: dict, seed: int = 7100) -> np.ndarray:
    """trim_castle (2048 x 2048): every strip and fit tile of the spec."""
    sh = Sheet(spec, TRIM)
    strips = spec["strips"]

    # Plinth, 0.6 m: a footing course with a moulded top (roll and chamfer).
    _moulding(sh, "plinth", TRIM_DARK, [(0.0, 0.035, -0.17), (0.035, 0.44, 0.0), (0.44, 0.47, -0.13),
                                        (0.47, 0.53, 0.12), (0.53, 0.565, 0.03), (0.565, 0.6, 0.1)], 1.2, seed + 1)
    # String course, 0.3 m: drip undercut, face, lit fillet and chamfer.
    _moulding(sh, "string", TRIM, [(0.0, 0.045, -0.19), (0.045, 0.19, 0.0), (0.19, 0.215, -0.07),
                                   (0.215, 0.25, 0.1), (0.25, 0.3, 0.14)], 1.6, seed + 2)

    # Cornice, 0.7 m: cavetto shadow, bead, dentils every 0.15 m, fascia, lit crown.
    def dentils(S, P):
        local = (S % 0.15) / 0.15
        zone = (P >= 0.14) & (P < 0.33)
        gap = (local > 0.62)
        lit_side = (local < 0.1)
        v = np.where(gap, -0.2, np.where(lit_side, 0.07, 0.0))
        v = np.where(zone & ~gap & (P >= 0.31), v - 0.06, v)  # the dentils' soffit edge
        return np.where(zone, v, 0.0).astype(np.float32)

    _moulding(sh, "cornice", TRIM, [(0.0, 0.06, -0.16), (0.06, 0.1, 0.12), (0.1, 0.14, -0.07), (0.14, 0.33, 0.0),
                                    (0.33, 0.37, -0.11), (0.37, 0.56, 0.02), (0.56, 0.6, -0.08), (0.6, 0.65, 0.07),
                                    (0.65, 0.7, 0.13)], 1.2, seed + 3, dentils)
    # Arch rim, 0.3 m: voussoirs (joints every joint_m), roll at the intrados.
    _moulding(sh, "arch_rim", TRIM, [(0.0, 0.05, -0.1), (0.05, 0.09, 0.12), (0.09, 0.11, -0.05), (0.11, 0.235, 0.0),
                                     (0.235, 0.255, -0.1), (0.255, 0.3, 0.08)], strips["arch_rim"].get("joint_m", 0.45),
              seed + 4, var=0.06)

    # Window frame, 0.3 m: chamfered jamb with a central roll.
    def central_roll(S, P):
        x = (P - 0.15) / 0.045
        return np.where(np.abs(x) <= 1.0, roll(-x) - 0.0, 0.0).astype(np.float32)

    _moulding(sh, "window_frame", TRIM, [(0.0, 0.05, -0.11), (0.05, 0.1, 0.0), (0.1, 0.2, 0.0), (0.2, 0.25, 0.03),
                                         (0.25, 0.3, 0.08)], 0.6, seed + 5, central_roll)
    # Vault rib, 0.3 m: a pointed roll (shaded flanks, lit arris).
    _moulding(sh, "rib", TRIM, [(0.0, 0.07, -0.12), (0.07, 0.135, -0.03), (0.135, 0.165, 0.15), (0.165, 0.23, 0.05),
                                (0.23, 0.3, -0.05)], 0.6, seed + 6)

    # Shaft, 0.7 m across (rotated: s runs up the shaft): three clustered
    # rolls, symmetric (which edge is p = 0 depends on the piece's rotation).
    def rolls(S, P):
        v = np.full(P.shape, -0.08, dtype=np.float32)
        for c, r in ((0.135, 0.085), (0.35, 0.1), (0.565, 0.085)):
            x = (P - c) / r
            v = np.where(np.abs(x) <= 1.0, np.where(np.abs(x) < 0.4, 0.1, np.where(np.abs(x) < 0.8, 0.02, -0.1)), v)
        hollow = (np.abs(P - 0.2375) < 0.0135) | (np.abs(P - 0.4625) < 0.0135)
        return np.where(hollow, -0.2, v).astype(np.float32)

    _moulding(sh, "shaft", TRIM, [], 0.6, seed + 8, rolls)

    # Capital and base band, 0.3 m: astragal, a bell of rounded leaf lobes
    # every 0.2 m, abacus.
    def lobes(S, P):
        zone = (P >= 0.08) & (P < 0.22)
        lx = ((S % 0.2) / 0.2) * 2 - 1
        ly = (P - 0.15) / 0.06
        r = lx * lx * 1.2 + ly * ly
        leaf = r < 0.85
        lit = leaf & (lx - ly < 0.3) & (r < 0.5)
        v = np.where(leaf, np.where(lit, 0.11, 0.04), -0.09)
        v = np.where(leaf & (r > 0.7), -0.03, v)
        return np.where(zone, v, 0.0).astype(np.float32)

    _moulding(sh, "capital_band", TRIM, [(0.0, 0.035, -0.16), (0.035, 0.08, 0.12), (0.22, 0.25, -0.15),
                                         (0.25, 0.3, 0.1)], 1.2, seed + 9, lobes)

    # Stair nosing and riser, 0.25 m: marble (cols 0-1023) and stone halves.
    for name, base, every in (("nosing_marble", MARBLE, None), ("nosing_stone", TRIM_DARK, 1.2)):
        rows, cols, S, P, ppm_u, _ = sh.strip(name)
        st = strips[name]
        v = bands(P, [(0.0, 0.02, -0.12), (0.02, 0.165, -0.03), (0.165, 0.185, -0.22), (0.185, 0.215, 0.1),
                      (0.215, 0.235, 0.03), (0.235, 0.25, 0.08)])
        tint = mortar = None
        if every:
            tint, mortar, edge = joints(S, every, ppm_u, seed + 10, st["u_m"])
            v = v + edge
        col = stone(base, v, tint, mortar)
        if name == "nosing_marble":
            # Faint veins: two thin, wandering, low-contrast lines on the riser.
            for k, (y0, amp, fr) in enumerate(((0.06, 0.03, 3.0), (0.12, 0.02, 5.0))):
                y = y0 + amp * np.sin(S / st["u_m"] * 2 * np.pi * fr + k)
                vein = band(np.abs(P - y) * 480, 0.8) * (P < 0.165)
                col = mix(col, hex_rgb(MARBLE_VEIN), vein * 0.6)
        sh.put(rows, cols, col)

    # Balustrade arcade (LOD1, painted): base, colonnettes with pointed
    # heads, dark openings, rail.
    rows, cols, S, P, ppm_u, _ = sh.strip("balustrade_arcade")
    u_m = strips["balustrade_arcade"]["u_m"]
    pitch = u_m / round(u_m / 0.3)
    x = ((S % pitch) / pitch) * 2 - 1  # -1..1 between colonnette centres
    half_open = 0.72
    apex, spring = 0.8, 0.68
    head = spring + (apex - spring) * np.sqrt(np.clip(1 - (np.abs(x) / half_open) ** 2, 0, 1))
    opening = (np.abs(x) < half_open) & (P >= 0.12) & (P < head)
    v = bands(P, [(0.0, 0.02, -0.15), (0.02, 0.1, 0.0), (0.1, 0.12, 0.1), (0.12, 0.85, 0.0), (0.85, 0.88, -0.14),
                  (0.88, 0.97, 0.02), (0.97, 1.0, 0.12)])
    v = np.where((P >= 0.12) & (P < 0.85) & ~opening & (x > 0.72) & (x < 0.86), -0.06, v)  # colonnette shade side
    col = stone(TRIM, v)
    seen = hex_rgb(FLOOR_SEEN) * (1 - OPENING_MIX) + hex_rgb(SHADOW) * OPENING_MIX
    col = mix(col, seen, opening.astype(np.float32))
    sh.put(rows, cols, col)

    # Iron, 0.25 m: a strap with round studs every 0.2 m.
    rows, cols, S, P, ppm_u, ppm_v = sh.strip("iron")
    v = bands(P, [(0.0, 0.02, -0.25), (0.02, 0.21, 0.0), (0.21, 0.23, 0.12), (0.23, 0.25, 0.25)])
    col = stone(IRON, v)
    sx = (S % 0.2) - 0.1
    sy = P - 0.115
    stud = disc(sx, sy, 0, 0, 0.032, 1 / ppm_v)
    hi = disc(sx, sy, -0.01, 0.01, 0.014, 1 / ppm_v)
    rim = disc(sx, sy, 0.004, -0.004, 0.032, 1 / ppm_v) - disc(sx, sy, 0.004, -0.004, 0.024, 1 / ppm_v)
    col = mix(col, hex_rgb(IRON) * 1.25, stud)
    col = mix(col, hex_rgb(IRON) * 0.7, np.clip(rim, 0, 1) * stud)
    col = mix(col, hex_rgb("#9A9CB0"), hi)
    sh.put(rows, cols, col)

    # Fit tiles (128 px each) and the reserved block.
    for name, painter in FIT_TILES.items():
        if name in spec["tiles"]:
            rows, cols, A, B = sh.tile(name)
            sh.put(rows, cols, painter(A, B, seed))
    for x0, y0, rw, rh in spec.get("reserved", []):
        rr = slice(sh.h - y0 - rh, sh.h - y0)
        cc = slice(x0, x0 + rw)
        sh.put(rr, cc, stone(TRIM, np.zeros((rh, rw), dtype=np.float32)))
    return sh.done()


# -- fit tiles: (A, B) in 0..1 -> rgb ---------------------------------------------------


def _frame_bevel(A, B, w=0.12, lit=0.1, dark=-0.12):
    """A raised block: lit top and left bevels, shaded bottom and right."""
    d = np.stack([1 - B, A, B, 1 - A])  # top, left, bottom, right
    near = np.argmin(d, axis=0)
    dm = np.min(d, axis=0)
    vals = np.array([lit, lit * 0.7, dark, dark * 0.75], dtype=np.float32)[near]
    return np.where(dm < w, vals, 0.0).astype(np.float32)


def _corbel_front(A, B, seed):
    # Three rounded tiers narrowing downward, each lit on top.
    v = np.full(A.shape, -0.12, dtype=np.float32)
    x = np.abs(A - 0.5) * 2
    for k, (b0, b1, half) in enumerate(((0.68, 1.0, 1.0), (0.38, 0.68, 0.8), (0.0, 0.38, 0.6))):
        inside = (B >= b0) & (B < b1) & (x < half - 0.25 * ((b1 - B) / (b1 - b0)) ** 2 * (k > 1))
        t = (B - b0) / (b1 - b0)
        v = np.where(inside, np.where(t > 0.8, 0.12, np.where(t < 0.18, -0.08, 0.02 - 0.04 * (x / half))), v)
    return stone(TRIM, v)


def _corbel_side(A, B, seed):
    # Side view: a quarter-round curve under a square top.
    curve = B > 0.62 + 0.38 * (1 - np.sqrt(np.clip(1 - A * A, 0, 1))) - 0.62 * (1 - A)
    v = np.where(B > 0.85, 0.1, np.where(curve, 0.0 - 0.08 * A, -0.14))
    return stone(TRIM, v.astype(np.float32))


def _keystone(A, B, seed):
    v = _frame_bevel(A, B, 0.1)
    inner = (np.abs(A - 0.5) < 0.28) & (np.abs(B - 0.5) < 0.34)
    v = np.where(inner, v + _frame_bevel((A - 0.22) / 0.56, (B - 0.16) / 0.68, 0.12, 0.08, -0.1) + 0.03, v)
    return stone(TRIM, v)


def _boss(A, B, seed):
    x, y = A - 0.5, B - 0.5
    r = np.sqrt(x * x + y * y)
    ang = np.arctan2(y, x)
    petal = r < 0.32 + 0.1 * np.cos(ang * 8)
    lit = (x - y) < 0
    v = np.where(petal, np.where(lit, 0.08, -0.04), -0.12)
    v = np.where(petal & (np.cos(ang * 8) < -0.85), -0.1, v)
    v = np.where(r < 0.12, np.where(lit, 0.14, 0.04), v)
    return stone(TRIM, v.astype(np.float32))


def _capital_leaf(A, B, seed):
    v = np.full(A.shape, -0.06, dtype=np.float32)
    for cx in (0.2, 0.5, 0.8):
        x = (A - cx) / 0.16
        y = (B - 0.45) / 0.42
        leaf = x * x + y * y < 1
        curl = ((A - cx) ** 2 + (B - 0.82) ** 2) < 0.012
        v = np.where(leaf, np.where(x < -0.1, 0.1, np.where(x < 0.5, 0.03, -0.06)), v)
        v = np.where(curl, 0.13, v)
        v = np.where(leaf & (np.abs(x) < 0.06) & (B < 0.75), -0.08, v)
    return stone(TRIM, v)


def _base_block(A, B, seed):
    return stone(TRIM_DARK, _frame_bevel(A, B, 0.1))


def _frame_gilt(A, B, seed):
    # A gilt moulding across b: bead, hollow, raised crest, bright edge.
    v = bands(B, [(0.0, 0.12, -0.18), (0.12, 0.25, 0.05), (0.25, 0.4, -0.08), (0.4, 0.62, 0.14), (0.62, 0.75, 0.0),
                  (0.75, 0.88, -0.1), (0.88, 1.01, 0.12)])
    beads = (np.abs(((A * 8) % 1) - 0.5) < 0.3) & (B > 0.12) & (B < 0.25)
    v = np.where(beads, v + 0.08, v)
    return stone(GOLD, v)


def _frame_oak(A, B, seed):
    v = bands(B, [(0.0, 0.15, -0.2), (0.15, 0.45, 0.0), (0.45, 0.55, 0.12), (0.55, 0.85, 0.03), (0.85, 1.01, 0.1)])
    grain = (paint.periodic_noise(A.shape[0], A.shape[1], (12, 2), seed + 31, 2) - 0.5) * 0.08
    return stone(OAK, v + grain.astype(np.float32))


def _finial(A, B, seed):
    x = np.abs(A - 0.5)
    body = x < 0.42 * (1 - B) ** 0.8 + 0.05
    crockets = np.zeros_like(body)
    for b0 in (0.25, 0.5, 0.72):
        crockets |= ((x - 0.42 * (1 - b0) ** 0.8) ** 2 + (B - b0) ** 2) < 0.006
    shape = body | crockets
    lit = A < 0.5
    v = np.where(shape, np.where(lit, 0.09, -0.05), -0.13)
    return stone(TRIM, v.astype(np.float32))


def _crest_blank(A, B, seed):
    x = np.abs(A - 0.5) * 2
    shield = (B > 0.08) & (B < 0.92) & (x < np.where(B > 0.45, 0.8, 0.8 * np.sqrt(np.clip((B - 0.08) / 0.37, 0, 1))))
    inner = (B > 0.16) & (B < 0.86) & (x < np.where(B > 0.47, 0.68, 0.68 * np.sqrt(np.clip((B - 0.16) / 0.31, 0, 1))))
    v = np.where(shield, np.where(inner, 0.03, np.where(A < 0.5, 0.11, -0.06)), -0.1)
    return stone(TRIM, v.astype(np.float32))


def _plaque(A, B, seed):
    v = _frame_bevel(A, B, 0.08)
    inner = (A > 0.14) & (A < 0.86) & (B > 0.2) & (B < 0.8)
    v = np.where(inner, -0.04 + _frame_bevel((A - 0.14) / 0.72, (B - 0.2) / 0.6, 0.06, -0.1, 0.08), v)
    return stone(TRIM, v)


def _newel_cap(A, B, seed):
    # A shallow pyramid seen from above: four triangles, lit from the upper left.
    x, y = A - 0.5, B - 0.5
    tri = np.where(np.abs(x) > np.abs(y), np.where(x < 0, 1, 3), np.where(y > 0, 0, 2))
    v = np.array([0.12, 0.07, -0.12, -0.08], dtype=np.float32)[tri]
    edge = np.maximum(np.abs(x), np.abs(y)) > 0.44
    v = np.where(edge, 0.0, v)
    return stone(TRIM, v)


FIT_TILES = {
    "corbel_front": _corbel_front,
    "corbel_side": _corbel_side,
    "keystone": _keystone,
    "boss": _boss,
    "capital_leaf": _capital_leaf,
    "base_block": _base_block,
    "frame_gilt": _frame_gilt,
    "frame_oak": _frame_oak,
    "finial": _finial,
    "crest_blank": _crest_blank,
    "plaque": _plaque,
    "newel_cap": _newel_cap,
}


# -- the wood sheet --------------------------------------------------------------------


def _grain(shape, seed: int, along_u: bool = True, amount: float = 0.06) -> np.ndarray:
    """Long, calm grain in two or three flat tones (stepped noise)."""
    cells = (max(2, shape[0] // 24), 3) if along_u else (2, max(2, shape[1] // 12))
    n = paint.periodic_noise(shape[0], shape[1], cells, seed, 2)
    return ((smoothstep(0.55, 0.6, n) - smoothstep(0.38, 0.43, 1 - n)) * amount).astype(np.float32)


def paint_wood(spec: dict, seed: int = 7300) -> np.ndarray:
    """wood_sheet (1024 x 1024): wainscot, door planks, planks, beam."""
    sh = Sheet(spec, OAK)

    # Wainscot, 3.2 m high, 6.4 m along: skirting, rails, stiles every 0.8 m
    # and two tiers of fielded panels.
    rows, cols, S, P, ppm_u, ppm_v = sh.strip("wainscot")
    stile_every, stile_w = 0.8, 0.14
    sx = S % stile_every
    stile = (sx < stile_w / 2) | (sx > stile_every - stile_w / 2)
    tiers = [(0.36, 1.0), (1.2, 2.75)]
    panel = np.zeros(S.shape, dtype=bool)
    v = np.zeros(S.shape, dtype=np.float32)
    for p0, p1 in tiers:
        inside = ~stile & (P >= p0) & (P < p1)
        panel |= inside
        # Fielded panel: bevels lit on top and left, shaded bottom and right.
        a = (sx - stile_w / 2) / (stile_every - stile_w)
        b = (P - p0) / (p1 - p0)
        dpx = np.stack([(1 - b) * (p1 - p0) * ppm_v, a * (stile_every - stile_w) * ppm_u, b * (p1 - p0) * ppm_v,
                        (1 - a) * (stile_every - stile_w) * ppm_u])
        near = np.argmin(dpx, axis=0)
        dm = np.min(dpx, axis=0)
        bev = np.where(dm < 9, np.array([0.12, 0.08, -0.14, -0.1], dtype=np.float32)[near], 0.0)
        crease = np.where(dm < 1.5, -0.22, 0.0)
        v = np.where(inside, bev + crease + 0.0, v)
    rail_v = bands(P, [(0.0, 0.16, -0.12), (0.16, 0.18, -0.2), (2.95, 2.98, -0.18), (2.98, 3.1, 0.05),
                       (3.1, 3.2, 0.13)])
    v = np.where(panel, v, v + rail_v)
    # Grain: along v on stiles and panels, along u on rails.
    g_u = _grain(S.shape, seed + 1, True, 0.045)
    g_v = _grain(S.shape, seed + 2, False, 0.045)
    stile_zone = stile & (P >= 0.18) & (P < 2.95) & ~((P >= tiers[0][1]) & (P < tiers[1][0]))
    v = v + np.where(stile_zone | panel, g_v, g_u)
    # Stiles stand slightly proud of the rails: a shade line on their right.
    v = np.where(stile_zone & (np.abs(sx - stile_w / 2) * ppm_u < 2.0), v - 0.14, v)
    base = np.where(panel[..., None], hex_rgb(OAK_PANEL), np.where((P < 0.18)[..., None], hex_rgb(OAK_RAIL),
                                                                    hex_rgb(OAK)))
    # Joint lines where stiles meet rails.
    joint = (stile & (np.abs(P - tiers[0][0]) * ppm_v < 1.0)) | (stile & (np.abs(P - tiers[1][1]) * ppm_v < 1.0))
    col = base * (1.0 + v)[..., None]
    col = mix(col, hex_rgb(OAK_JOINT), joint.astype(np.float32) * 0.6)
    sh.put(rows, cols, col)

    # Door planks (rotated: s up the leaf, p across): six boards of 0.2 m.
    rows, cols, S, P, ppm_u, ppm_v = sh.strip("door_planks")
    k = np.floor(P / 0.2).astype(np.int32)
    rng = np.random.default_rng(seed + 3)
    tone = 1.0 + rng.choice(np.array([-0.06, -0.03, 0.0, 0.03, 0.05], dtype=np.float32), 8)
    lp = (P % 0.2) * ppm_v
    v = np.where(lp < 2.0, -0.35, np.where(lp < 6.0, 0.08, np.where(lp > 0.2 * ppm_v - 4.0, -0.08, 0.0)))
    v = v + _grain(S.shape, seed + 4, True, 0.07)
    col = hex_rgb(OAK)[None, None, :] * (tone[np.clip(k, 0, 7)] * (1.0 + v))[..., None]
    sh.put(rows, cols, col)

    # Planks, 0.6 m: three boards with staggered butt joints (wrapping).
    rows, cols, S, P, ppm_u, ppm_v = sh.strip("planks")
    u_m = spec["strips"]["planks"]["u_m"]
    k = np.clip(np.floor(P / 0.2).astype(np.int32), 0, 2)
    v = np.zeros(S.shape, dtype=np.float32)
    col = np.zeros(S.shape + (3,), dtype=np.float32)
    for b in range(3):
        cuts = np.sort((rng.uniform(0, u_m) + np.array([0.0, u_m / 2])) % u_m)
        d = np.min(np.abs(((S[:, :, None] - cuts[None, None, :]) + u_m / 2) % u_m - u_m / 2), axis=2) * ppm_u
        seg = (np.searchsorted(cuts, S) % 2)
        t = np.where(seg == 0, 1.0 + rng.uniform(-0.06, 0.06), 1.0 + rng.uniform(-0.06, 0.06))
        on = k == b
        vb = np.where(d < 1.5, -0.3, np.where(d < 4.0, 0.06 * np.sign(np.cos(seg * np.pi)), 0.0))
        v = np.where(on, vb, v)
        col = np.where(on[..., None], hex_rgb(OAK)[None, None, :] * np.asarray(t, dtype=np.float32)[..., None], col)
    lp = (P % 0.2) * ppm_v
    v = v + np.where(lp < 2.0, -0.32, np.where(lp < 5.0, 0.07, 0.0)) + _grain(S.shape, seed + 5, True, 0.06)
    sh.put(rows, cols, col * (1.0 + v)[..., None])

    # Beam face, 0.6 m: one timber, chamfers top (lit) and bottom (shaded),
    # a few drying checks.
    rows, cols, S, P, ppm_u, ppm_v = sh.strip("beam")
    v = bands(P, [(0.0, 0.05, -0.15), (0.05, 0.06, -0.25), (0.54, 0.55, -0.2), (0.55, 0.6, 0.12)])
    v = v + _grain(S.shape, seed + 6, True, 0.07)
    u_m = spec["strips"]["beam"]["u_m"]
    for _ in range(5):
        s0, p0, ln = rng.uniform(0, u_m), rng.uniform(0.15, 0.45), rng.uniform(0.3, 0.9)
        ds = ((S - s0) % u_m)
        check = (ds < ln) & (np.abs(P - p0 - 0.01 * np.sin(ds * 9)) * ppm_v < 0.9)
        v = np.where(check, -0.3, v)
    sh.put(rows, cols, hex_rgb(OAK_RAIL)[None, None, :] * (1.0 + v)[..., None])
    return sh.done()


# -- the glass sheet --------------------------------------------------------------------

SKY_LOW, SKY_HIGH = "#D4E6EC", "#8DB9E8"
GLASS_ACCENTS = ("#E2A84A", "#C9667A", "#5FA8B8", "#7C88D0")


def _sky(A, B):
    t = smoothstep(0.0, 1.0, B)[..., None]
    return hex_rgb(SKY_LOW)[None, None, :] * (1 - t) + hex_rgb(SKY_HIGH)[None, None, :] * t


def _lancet_mask(X, Y, width, height):
    """Inside a lancet light `width` x `height` (pointed, equilateral head),
    X across from the left jamb, Y up from the sill (same units)."""
    spring = height - width * math.sqrt(3) / 2
    head = (Y <= spring) | ((((X - width) ** 2 + (Y - spring) ** 2) <= width * width)
                            & ((X ** 2 + (Y - spring) ** 2) <= width * width))
    return head & (X >= 0) & (X <= width) & (Y >= 0)


def _light(A, B, seed, diamonds: bool):
    """One window light filling a 1 x 4 tile: a lead rim, a fillet of
    coloured squares, leaded quarries (diamonds, or squares with a roundel)
    over a day sky with a low cloud bank."""
    aspect = 4.0
    X, Y = A, B * aspect
    px = 1.0 / 248.0  # one pixel in tile-width units

    def lancet(inset):
        s = 1.0 - 2 * inset
        return _lancet_mask((X - inset) / s, (Y - inset) / s, 1.0, (aspect - inset) / s)

    inside, inner, core = lancet(0.0), lancet(0.015), lancet(0.13)
    core_rim = core & ~lancet(0.145)
    col = _sky(A, B)
    clouds = paint.periodic_noise(A.shape[0], A.shape[1], (6, 2), seed, 2)
    bank = smoothstep(0.55, 0.6, clouds + (0.45 - B) * 0.6) * (B < 0.6)
    col = mix(col, hex_rgb("#EEF2F0"), bank * 0.7)
    rng = np.random.default_rng(seed)
    if diamonds:
        u, w = X / 0.25 + Y / 0.36, X / 0.25 - Y / 0.36
        dlead = np.minimum(np.abs(u - np.round(u)), np.abs(w - np.round(w))) * 0.25
        cell = (np.floor(u).astype(np.int64) * 7 + np.floor(w).astype(np.int64) * 13) % 31
    else:
        gx, gy = X / 0.25, Y / 0.3
        dlead = np.minimum(np.abs(gx - np.round(gx)) * 0.25, np.abs(gy - np.round(gy)) * 0.3)
        cell = (np.floor(gx).astype(np.int64) * 7 + np.floor(gy).astype(np.int64) * 13) % 31
    col = col * (1.0 + rng.normal(0, 0.035, 31).astype(np.float32)[cell])[..., None]
    lead = band(dlead / px, 1.2)
    if not diamonds:
        # A roundel at mid height: an amber ring round a pale centre.
        r = np.sqrt((X - 0.5) ** 2 + (Y - 2.0) ** 2)
        col = np.where(((r < 0.3) & (r > 0.2))[..., None], hex_rgb(GLASS_ACCENTS[0])[None, None, :], col)
        col = np.where((r <= 0.2)[..., None], hex_rgb("#F0E6C8")[None, None, :], col)
        lead = np.where(r < 0.32, band(np.minimum(np.abs(r - 0.3), np.abs(r - 0.2)) / px, 1.2), lead)
    # The coloured fillet between the rim and the core: small squares.
    fillet = inside & ~core
    bottom = Y < 0.13
    k = np.where(bottom, np.floor(X / 0.14), np.floor(Y / 0.14)).astype(np.int64) % len(GLASS_ACCENTS)
    col = np.where(fillet[..., None], np.stack([hex_rgb(c) for c in GLASS_ACCENTS])[k], col)
    t = np.where(bottom, X / 0.14, Y / 0.14)
    lead = np.where(fillet, band(np.abs(t - np.round(t)) * 0.14 / px, 1.2), lead)
    lead = np.maximum(lead, (inside & ~inner).astype(np.float32))
    lead = np.maximum(lead, core_rim.astype(np.float32))
    col = mix(col, hex_rgb(LEAD), np.clip(lead, 0, 1))
    return np.where(inside[..., None], col, hex_rgb(LEAD)[None, None, :])


def _tracery(A, B, seed):
    """A tracery head: a quatrefoil in a circle, coloured lobes round a gold
    roundel, sky quarries between, lead everywhere outside the circle."""
    X, Y = A - 0.5, B - 0.5
    px = 1.0 / 504.0
    r = np.sqrt(X * X + Y * Y)
    circle = r < 0.47
    col = _sky(A, B)
    gx, gy = (X + Y) / 0.09, (X - Y) / 0.09
    dlead = np.minimum(np.abs(gx - np.round(gx)), np.abs(gy - np.round(gy))) * 0.09 / math.sqrt(2)
    lead = band(dlead / px, 1.2)
    lobes = np.zeros(A.shape, dtype=bool)
    for k, (cx, cy) in enumerate(((0.2, 0), (0, 0.2), (-0.2, 0), (0, -0.2))):
        d = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
        lobe = d < 0.17
        lobes |= lobe
        c = GLASS_ACCENTS[1] if k % 2 == 0 else GLASS_ACCENTS[3]
        col = np.where(lobe[..., None], hex_rgb(c)[None, None, :] * (1.0 + 0.08 * (Y - X))[..., None], col)
        lead = np.where(lobe, band(np.abs(d - 0.17) / px, 2.0), lead)
    roundel = r < 0.09
    col = np.where(roundel[..., None], hex_rgb(GLASS_ACCENTS[0])[None, None, :], col)
    lead = np.where(r < 0.1, band(np.abs(r - 0.09) / px, 2.0), lead)
    lead = np.maximum(lead, band(np.abs(r - 0.47) / px, 3.0))
    col = mix(col, hex_rgb(LEAD), np.clip(lead, 0, 1))
    return np.where(circle[..., None], col, hex_rgb(LEAD)[None, None, :])


def _quarries(A, B, seed):
    X, Y = A, B
    px = 1.0 / 504.0
    u, w = X * 8 + Y * 6, X * 8 - Y * 6
    du = np.abs(u - np.round(u)) / 8
    dw = np.abs(w - np.round(w)) / 8
    cell = (np.floor(u).astype(np.int64) * 7 + np.floor(w).astype(np.int64) * 13) % 31
    rng = np.random.default_rng(seed)
    col = _sky(A, B) * (1.0 + rng.normal(0, 0.035, 31).astype(np.float32)[cell])[..., None]
    lead = band(np.minimum(du, dw) / px, 1.2)
    return mix(col, hex_rgb(LEAD), lead)


def paint_glass(spec: dict, seed: int = 7500) -> np.ndarray:
    """glass_sheet (1024 x 1024): lancet lights, a tracery head, quarries.
    The same image is the emissive map (tinted by the hour's sky light on
    the client), so everything outside the glass is dark lead."""
    sh = Sheet(spec, LEAD)
    painters = {
        "light_lancet": lambda A, B: _light(A, B, seed + 1, True),
        "light_tall": lambda A, B: _light(A, B, seed + 2, False),
        "head_tracery": lambda A, B: _tracery(A, B, seed + 3),
        "quarries": lambda A, B: _quarries(A, B, seed + 4),
    }
    for name, fn in painters.items():
        if name in spec["tiles"]:
            rows, cols, A, B = sh.tile(name)
            sh.put(rows, cols, fn(A, B))
    return sh.done()


# -- banners -----------------------------------------------------------------------------


def _charge(kind: str, X, Y, aa):
    """A plain heraldic charge centred at (0, 0), about 0.3 wide (X, Y in
    banner widths): neutral geometric shapes only."""
    r = np.sqrt(X * X + Y * Y)
    if kind == "roundel":  # a sun disc with short rays
        ang = np.arctan2(Y, X)
        return np.clip((0.2 + 0.05 * (np.cos(ang * 12) > 0.3) - r) / aa + 0.5, 0, 1)
    if kind == "lozenge":
        return np.clip((0.26 - (np.abs(X) + np.abs(Y) * 0.7)) / aa + 0.5, 0, 1)
    if kind == "star":  # five points, one straight up
        ang = np.arctan2(X, Y)
        k = np.abs(((ang / (2 * np.pi / 5)) % 1.0) - 0.5) * 2  # 1 at a point, 0 between
        rr = 0.1 + 0.15 * k ** 1.6
        return np.clip((rr - r) / aa + 0.5, 0, 1)
    if kind == "crescent":
        outer = np.clip((0.22 - r) / aa + 0.5, 0, 1)
        inner = np.clip((0.18 - np.sqrt((X - 0.0) ** 2 + (Y - 0.08) ** 2)) / aa + 0.5, 0, 1)
        return np.clip(outer - inner, 0, 1)
    raise ValueError(kind)


def _banner(A, B, field: str, trim: str, charge: str | None, aspect: float, seed: int, quarters=None):
    """A hanging banner (the swallowtail is cut in geometry): field, border
    in the second colour, a darker rod sleeve at the top, two painted folds."""
    px = 1.0 / 200.0
    X, Y = (A - 0.5), (B - 0.62) * aspect
    if quarters:
        q = (A > 0.5).astype(np.int32) + 2 * (B < 0.62).astype(np.int32)
        col = np.stack([hex_rgb(c) for c in quarters])[q]
    else:
        col = np.broadcast_to(hex_rgb(field)[None, None, :], A.shape + (3,)).copy()
    if charge:
        m = _charge(charge, X, Y, px)
        col = mix(col, hex_rgb(trim), m)
    elif quarters:  # a plain ring over the quarters
        r = np.sqrt(X * X + Y * Y)
        col = mix(col, hex_rgb(trim), np.clip((0.06 - np.abs(r - 0.24)) / px + 0.5, 0, 1))
    border = (A < 0.08) | (A > 0.92) | (B > 0.955)
    col = np.where(border[..., None], hex_rgb(trim)[None, None, :], col)
    sleeve = B > 0.975
    col = np.where(sleeve[..., None], col * 0.72, col)
    # Folds: flat lit and shaded bands (two soft folds down the cloth).
    f = np.sin((A + 0.04 * np.sin(B * 7)) * 2 * np.pi * 2)
    v = np.where(f > 0.55, 0.07, np.where(f < -0.6, -0.1, 0.0))
    return col * (1.0 + v)[..., None]


def paint_banners(spec: dict, seed: int = 7700) -> np.ndarray:
    """banners (1024 x 1024): the four house slots and the school banner
    (house slots a-d, neutral charges, no names or emblems)."""
    sh = Sheet(spec, "#6A5A50")
    charges = {"a": "roundel", "b": "lozenge", "c": "star", "d": "crescent"}
    for slot in "abcd":
        name = f"banner_house_{slot}"
        if name in spec["tiles"]:
            rows, cols, A, B = sh.tile(name)
            field, second = HOUSES[slot]
            sh.put(rows, cols, _banner(A, B, field, second, charges[slot], BANNER_M[1] / BANNER_M[0], seed))
    if "banner_school" in spec["tiles"]:
        rows, cols, A, B = sh.tile("banner_school")
        sh.put(rows, cols, _banner(A, B, "#5A4A6A", GOLD, None, BANNER_M[1] / BANNER_M[0], seed,
                                   quarters=[HOUSES[s][0] for s in "abcd"]))
    return sh.done()
