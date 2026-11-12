"""The portrait sheets (castle kit M4, docs/art/castle-kit.md "Portraits and tapestries"):
`portraits_a.png` and `portraits_b.png`, 2048 x 2048, an 8 x 8 grid of 256 px
cells. Each cell is 4 px of padding, a 20 px frame ring (the frame's
moulding, painted in the castle_textures.json palette names frame_gilt,
frame_oak or frame_ebony) and a 208 px painting. Kinds by index (grid.py
PORTRAIT_KINDS): 0-31 figures, 32-47 landscapes, 48-63 still lifes; sheet
b's canvas 63 is the guardian portrait of the seventh-floor common-room door.

Everything here is our own invented work, painted by script: invented
people in period dress (busts and half-lengths), invented landscapes
(hills, lakes, towers, skies) and still lifes (fruit, books, candles,
instruments). No real painting or person is copied or traced, there is no text and no name. The guardian is our own design: a
tall elderly keeper in a deep green robe, a lantern raised in one hand and a
ring of keys in the other, under a stone arch.

The look is the castle's anime style (docs/art/style-guide.md): few
values, clear shapes, a cel shadow on the side away from the light (the
light comes from the upper left), a painted edge light, a soft varnish.
Paintings keep their subject in the middle 60 % of the width (the frames
crop tall canvases there, grid.canvas_paint_uv) and landscapes their
horizon at 45-60 % of the height. Seeded and deterministic (numpy only);
the albedo is fitted to 0.04-0.85 linear like every library texture.

Colours: the subjects' colours are the named constants below (painter
data, docs/art/castle-kit.md "Colours come from palette data only"); the frames' colours come from
castle_textures.json `palette`. Arrays follow paint.py: float32 RGBA, sRGB,
row 0 at the BOTTOM of the image.
"""

from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_BLENDER, os.path.join(_BLENDER, "world")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import paint  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import props_atlas  # noqa: E402

N = G.PORTRAIT_PAINT  # 208
CELL = G.PORTRAIT_CELL
PAD = G.PORTRAIT_PAD
RING = G.PORTRAIT_RING
SHEET_SEEDS = {"portraits_a": 8100, "portraits_b": 8300}
FRAME_NAMES = ("frame_gilt", "frame_oak", "frame_ebony")
FRAME_WEIGHTS = (0.6, 0.25, 0.15)

# -- the subjects' colours (sRGB) ------------------------------------------------------------

SKIN = ("#F3D6BF", "#EBC4A4", "#DDAE8A", "#C98F6B", "#A8704F", "#87553A", "#6A402B")
HAIR = ("#2B2420", "#4A3426", "#6E4A2E", "#9A6A3C", "#C99A5B", "#E3C88E", "#A63E24", "#8C8A86", "#C9C6C0", "#ECEAE4")
ROBES = ("#7A1E26", "#9B3A2A", "#2F4F3A", "#1F5A4E", "#24365E", "#3B2E5A", "#5A2348", "#2C2A2E", "#4B3B2C", "#6B5A3A",
         "#3E4A55", "#8A6A28", "#B36A2E", "#1E3A4A")
COLLAR = ("#EFE8DA", "#E6DCC6", "#D9CFB8")
GOLD = "#D9A53F"
BACKS = (("#2E3B2F", "#4C5E44"), ("#3A2C24", "#6A4E36"), ("#22303C", "#3E5568"), ("#3A2436", "#5E3E58"),
         ("#2E2A22", "#5A5140"), ("#1E2A2A", "#3D5652"), ("#40301E", "#7A5A34"), ("#2A2A33", "#4B4B5E"))
CURTAIN = ("#6E1E24", "#2F4A36", "#2A3A62", "#5A3A1E", "#4A2A4A")
SKIES = {
    "day": ("#7FA8D6", "#CFE0EE"),
    "dawn": ("#8C9AC8", "#F2C8A0"),
    "dusk": ("#4B4A7A", "#E89A6A"),
    "storm": ("#55606E", "#A7AEB4"),
    "night": ("#141E3A", "#3A4E7A"),
}
HILLS = ("#5E7FA0", "#6F8F6A", "#4F6E46", "#3C5534", "#7E8C5C")
WATER = ("#5C86B0", "#8FB2CF")
STONE = ("#9C968C", "#C4BDB0", "#6E6A64")
TREE = ("#2E4A2C", "#3D5E36", "#24382A")
WOOD = ("#6B4429", "#8A5A36", "#4E3020")
CLOTH = ("#E9E2D2", "#B33A30", "#2E4E7A", "#3E6A44")
FRUIT = ("#C2342C", "#D9822B", "#E8C64A", "#7FA040", "#5A2A50", "#B8463A")
BOOKS = ("#7A1E26", "#24365E", "#2F4F3A", "#6B4A2A", "#3B2E5A", "#8A6A28")
METAL = ("#B9BDC2", "#D9A53F", "#8C6A3A")
FLAME = ("#FFD27A", "#FF9A3C")
GUARDIAN_ROBE = "#1E4D33"  # deep green
GUARDIAN_TRIM = "#C9A24A"
LIGHT = np.array([-0.62, -0.78])  # light from the upper left (x right, y down)


def rgb(h) -> np.ndarray:
    """A named colour (or an RGB array, passed through) as float32 sRGB."""
    if not isinstance(h, str):
        return np.asarray(h, dtype=np.float32)
    return paint.hex_rgb(h).astype(np.float32)


# -- painting primitives (y down, coordinates in canvas units 0..1) ------------------------


class Canvas:
    """A square painting `n` px, colours RGB float (sRGB); coordinates in
    0..1 with y down (the top of the painting at y = 0)."""

    def __init__(self, n: int = N):
        self.n = n
        c = (np.arange(n, dtype=np.float32) + 0.5) / n
        self.X, self.Y = np.meshgrid(c, c)
        self.img = np.zeros((n, n, 3), dtype=np.float32)
        self.px = 1.0 / n

    def aa(self, d) -> np.ndarray:
        """Coverage of a signed distance field (negative inside), 1 px soft."""
        return np.clip(0.5 - d / self.px, 0.0, 1.0)

    def put(self, mask, colour) -> None:
        col = rgb(colour) if isinstance(colour, str) else np.asarray(colour, dtype=np.float32)
        m = mask[..., None] if mask.ndim == 2 else mask
        self.img = self.img * (1.0 - m) + col * m

    def gradient(self, top: str, bottom: str, y0: float = 0.0, y1: float = 1.0, mask=None) -> None:
        t = np.clip((self.Y - y0) / max(1e-6, y1 - y0), 0, 1)[..., None]
        col = rgb(top) * (1 - t) + rgb(bottom) * t
        if mask is None:
            self.img = col.astype(np.float32)
        else:
            m = mask[..., None]
            self.img = self.img * (1 - m) + col * m

    # signed distances
    def ellipse(self, cx, cy, rx, ry, rot: float = 0.0):
        x, y = self.X - cx, self.Y - cy
        if rot:
            c, s = np.cos(rot), np.sin(rot)
            x, y = x * c + y * s, -x * s + y * c
        k = np.sqrt((x / rx) ** 2 + (y / ry) ** 2)
        return (k - 1.0) * min(rx, ry)

    def box(self, cx, cy, hx, hy, r: float = 0.0):
        qx = np.abs(self.X - cx) - hx + r
        qy = np.abs(self.Y - cy) - hy + r
        return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r

    def poly(self, pts):
        """Signed distance to a convex polygon (counter-clockwise or not)."""
        pts = np.asarray(pts, dtype=np.float32)
        d = np.full(self.X.shape, -1e9, dtype=np.float32)
        area = 0.0
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            area += a[0] * b[1] - b[0] * a[1]
        sgn = 1.0 if area > 0 else -1.0
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            ex, ey = b[0] - a[0], b[1] - a[1]
            ln = max(1e-9, float(np.hypot(ex, ey)))
            # Outward normal for this winding (y down).
            nx, ny = ey / ln * sgn, -ex / ln * sgn
            d = np.maximum(d, (self.X - a[0]) * nx + (self.Y - a[1]) * ny)
        return d

    def capsule(self, x0, y0, x1, y1, r):
        px, py = self.X - x0, self.Y - y0
        bx, by = x1 - x0, y1 - y0
        h = np.clip((px * bx + py * by) / max(1e-9, bx * bx + by * by), 0, 1)
        return np.hypot(px - bx * h, py - by * h) - r

    def shift(self, d_fn, dx: float, dy: float):
        """The same field moved by (dx, dy) (for cel shadows: d_fn(X, Y))."""
        X, Y = self.X, self.Y
        self.X, self.Y = X - dx, Y - dy
        out = d_fn()
        self.X, self.Y = X, Y
        return out

    def shape(self, d_fn, colour, shade: float = 0.22, light: float = 0.10, k: float = 0.035, rim: bool = True):
        """A cel-shaded shape: base colour, a shadow crescent on the side
        away from the light, a thin lit edge on the light side."""
        d = d_fn()
        m = self.aa(d)
        base = rgb(colour)
        self.put(m, base)
        if shade:
            lit = self.aa(self.shift(d_fn, LIGHT[0] * -k, LIGHT[1] * -k))
            sh = m * (1.0 - lit)
            self.put(sh * 0.95, base * (1.0 - shade) + np.array([0.02, 0.0, 0.04], dtype=np.float32) * shade)
        if rim and light:
            inner = self.aa(self.shift(d_fn, LIGHT[0] * 0.012, LIGHT[1] * 0.012))
            edge = m * (1.0 - inner)
            self.put(edge * 0.8, np.clip(base * (1.0 + light * 3) + light * 0.3, 0, 1))
        return m

    def line(self, x0, y0, x1, y1, w, colour, alpha: float = 1.0):
        self.put(self.aa(self.capsule(x0, y0, x1, y1, w)) * alpha, colour)

    def noise(self, rng, amount: float = 0.03, cells=(6, 6)) -> None:
        nz = paint.periodic_noise(self.n, self.n, cells, int(rng.integers(1 << 30)), 3).astype(np.float32)
        self.img = np.clip(self.img * (1.0 + (nz[..., None] - 0.5) * amount * 2), 0, 1)

    def varnish(self, warm: float = 0.06, vignette: float = 0.22) -> None:
        """An old varnish: a warm glaze and a soft vignette."""
        r = np.hypot(self.X - 0.5, self.Y - 0.47)
        v = 1.0 - vignette * np.clip((r - 0.28) / 0.45, 0, 1) ** 1.5
        glaze = np.array([1.0 + warm * 0.4, 1.0, 1.0 - warm], dtype=np.float32)
        self.img = np.clip(self.img * v[..., None] * glaze, 0, 1)


def mix(a, b, t):
    return rgb(a) * (1 - t) + rgb(b) * t if isinstance(a, str) else a * (1 - t) + b * t


def shade_hex(h: str, k: float) -> np.ndarray:
    return np.clip(rgb(h) * k, 0, 1)


# -- figures ------------------------------------------------------------------------------------


def _background(cv: Canvas, rng) -> None:
    dark, light = BACKS[int(rng.integers(len(BACKS)))]
    cv.gradient(light, dark, 0.0, 1.0)
    # A soft glow behind the head.
    g = np.clip(1.0 - np.hypot(cv.X - 0.5 + rng.uniform(-0.1, 0.1), cv.Y - 0.32) / 0.55, 0, 1) ** 2
    cv.img = np.clip(cv.img + g[..., None] * rgb(light) * 0.25, 0, 1)
    kind = rng.random()
    if kind < 0.3:
        # A drawn curtain at one side.
        side = -1 if rng.random() < 0.5 else 1
        x0 = 0.5 + side * rng.uniform(0.22, 0.32)
        col = CURTAIN[int(rng.integers(len(CURTAIN)))]
        d = (cv.X - x0) * -side - 0.03 * np.sin(cv.Y * 20 + rng.uniform(0, 6))
        m = cv.aa(-d * 1.0)
        cv.put(m, col)
        folds = 0.5 + 0.5 * np.sin((cv.X * 40 + np.sin(cv.Y * 6) * 2))
        cv.img = np.clip(cv.img * (1 - m[..., None] * folds[..., None] * 0.25), 0, 1)
    elif kind < 0.45:
        # A stone column behind one shoulder.
        side = -1 if rng.random() < 0.5 else 1
        x0 = 0.5 + side * rng.uniform(0.3, 0.38)
        cv.shape(lambda: cv.box(x0, 0.5, 0.06, 0.7), STONE[0], shade=0.25)
    elif kind < 0.58:
        # A window opening onto a small sky and hills.
        side = -1 if rng.random() < 0.5 else 1
        cx = 0.5 + side * 0.33
        win = cv.box(cx, 0.32, 0.1, 0.16, 0.0)
        m = cv.aa(win)
        sky = SKIES[("day", "dawn", "dusk")[int(rng.integers(3))]]
        tmp = Canvas(cv.n)
        tmp.gradient(sky[0], sky[1], 0.15, 0.45)
        hill = tmp.aa(tmp.Y - (0.42 + 0.03 * np.sin(tmp.X * 30 + rng.uniform(0, 6))))
        tmp.put(1 - hill, HILLS[int(rng.integers(len(HILLS)))])
        cv.img = cv.img * (1 - m[..., None]) + tmp.img * m[..., None]
        frame = cv.aa(cv.box(cx, 0.32, 0.115, 0.175, 0.0)) - m
        cv.put(np.clip(frame, 0, 1), STONE[2])


def _hair_back(cv: Canvas, hx, hy, rx, ry, colour, style) -> None:
    if style == "long":
        cv.shape(lambda: cv.ellipse(hx, hy + ry * 0.75, rx * 1.35, ry * 1.55), colour, shade=0.3)
    elif style == "bun":
        cv.shape(lambda: cv.ellipse(hx, hy - ry * 1.05, rx * 0.55, ry * 0.4), colour, shade=0.3)
    elif style == "curls":
        for k in range(7):
            a = np.pi * (0.95 + k * 0.18)
            cx, cy = hx + np.cos(a) * rx * 1.05, hy + np.sin(a) * ry * 0.95 + ry * 0.15
            cv.shape(lambda cx=cx, cy=cy: cv.ellipse(cx, cy, rx * 0.42, rx * 0.42), colour, shade=0.3)


def _hair_front(cv: Canvas, hx, hy, rx, ry, colour, style, rng) -> None:
    top = hy - ry * 0.32
    if style == "bald":
        for s in (-1, 1):
            cv.shape(lambda s=s: cv.ellipse(hx + s * rx * 0.92, hy - ry * 0.05, rx * 0.22, ry * 0.42), colour, shade=0.3)
        return
    if style in ("short", "long", "bun", "curls"):
        part = rng.uniform(-0.4, 0.4)
        cap = lambda: np.maximum(cv.ellipse(hx, hy - ry * 0.18, rx * 1.08, ry * 0.95), cv.Y - top - 0.01 * np.sin(cv.X * 60))
        cv.shape(cap, colour, shade=0.28)
        # A fringe swept from the parting.
        cv.shape(lambda: cv.poly([(hx + part * rx, hy - ry * 0.95), (hx + rx * 1.05, hy - ry * 0.35),
                                  (hx + rx * 0.4, hy - ry * 0.25), (hx - rx * 1.0, hy - ry * 0.4)]), colour, shade=0.25)


def _hat(cv: Canvas, hx, hy, rx, ry, rng) -> None:
    kind = int(rng.integers(5))
    col = ROBES[int(rng.integers(len(ROBES)))] if rng.random() < 0.6 else "#22201F"
    top = hy - ry * 0.75
    if kind == 0:  # a tall pointed hat with a soft bend
        lean = rng.uniform(-0.08, 0.08)
        cv.shape(lambda: cv.poly([(hx - rx * 1.05, top + 0.02), (hx + rx * 1.05, top + 0.02), (hx + lean, top - ry * 1.9)]),
                 col, shade=0.3)
        cv.shape(lambda: cv.ellipse(hx, top + 0.02, rx * 1.55, ry * 0.18), col, shade=0.35)
    elif kind == 1:  # a soft beret
        cv.shape(lambda: cv.ellipse(hx + rx * 0.15, top, rx * 1.35, ry * 0.42, 0.12), col, shade=0.3)
    elif kind == 2:  # a coif / hood
        cv.shape(lambda: np.maximum(cv.ellipse(hx, hy - ry * 0.05, rx * 1.3, ry * 1.25), -cv.ellipse(hx, hy + ry * 0.12, rx * 0.98, ry * 1.02)),
                 col, shade=0.3)
    elif kind == 3:  # a broad brimmed hat
        cv.shape(lambda: cv.box(hx, top - ry * 0.32, rx * 0.85, ry * 0.4, 0.02), col, shade=0.3)
        cv.shape(lambda: cv.ellipse(hx, top + 0.01, rx * 2.0, ry * 0.16), col, shade=0.35)
    else:  # a circlet with a stone
        cv.shape(lambda: cv.box(hx, top + ry * 0.2, rx * 1.02, 0.012, 0.005), GOLD, shade=0.2)
        cv.shape(lambda: cv.ellipse(hx, top + ry * 0.2, 0.014, 0.016), ROBES[int(rng.integers(len(ROBES)))], shade=0.2)


def _face(cv: Canvas, hx, hy, rx, ry, skin, rng, old: bool, glasses: bool) -> None:
    eye_y = hy + ry * 0.08
    dx = rx * 0.42
    ink = "#2A1E1A"
    look = rng.uniform(-0.25, 0.25) * rx * 0.25
    for s in (-1, 1):
        ex = hx + s * dx
        # Anime eyes: a dark almond with an iris and a white catch light.
        cv.put(cv.aa(cv.ellipse(ex, eye_y, rx * 0.2, ry * 0.085)), "#F4EFE6")
        iris = HAIR[int(rng.integers(4))] if rng.random() < 0.5 else ("#3C5A7A", "#4A6A3A", "#6A4A2A")[int(rng.integers(3))]
        cv.put(cv.aa(cv.ellipse(ex + look, eye_y + ry * 0.01, rx * 0.11, ry * 0.085)), iris)
        cv.put(cv.aa(cv.ellipse(ex + look, eye_y + ry * 0.015, rx * 0.055, ry * 0.05)), ink)
        cv.put(cv.aa(cv.ellipse(ex + look - rx * 0.04, eye_y - ry * 0.025, rx * 0.03, ry * 0.022)), "#FFFFFF")
        # Upper lid line and brow.
        cv.line(ex - rx * 0.21, eye_y - ry * 0.06, ex + rx * 0.21, eye_y - ry * 0.085 * (1 if s > 0 else 0.9), 0.005, ink)
        by = eye_y - ry * (0.3 + rng.uniform(-0.03, 0.04))
        cv.line(ex - rx * 0.2, by + ry * 0.03 * s * 0.5, ex + rx * 0.2, by - ry * 0.03 * s * 0.5, 0.008 if old else 0.007,
                shade_hex(HAIR[0], 1.0) if not old else rgb("#9A948C"))
        if old:
            cv.line(ex - rx * 0.18, eye_y + ry * 0.12, ex + rx * 0.12, eye_y + ry * 0.14, 0.003, shade_hex(skin, 0.75), 0.8)
    # Nose: a small shadow on the side away from the light, mouth a short stroke.
    cv.line(hx + rx * 0.05, hy + ry * 0.2, hx + rx * 0.1, hy + ry * 0.38, 0.006, shade_hex(skin, 0.78), 0.9)
    my = hy + ry * 0.58
    smile = rng.uniform(-0.02, 0.05)
    cv.line(hx - rx * 0.2, my - smile, hx, my + 0.006, 0.006, "#7A3A30")
    cv.line(hx, my + 0.006, hx + rx * 0.2, my - smile, 0.006, "#7A3A30")
    if not old and rng.random() < 0.4:
        for s in (-1, 1):
            cv.put(cv.aa(cv.ellipse(hx + s * rx * 0.55, hy + ry * 0.35, rx * 0.16, ry * 0.06)) * 0.35, "#E08A80")
    if old:
        for s in (-1, 1):
            cv.line(hx + s * rx * 0.3, hy + ry * 0.42, hx + s * rx * 0.42, hy + ry * 0.66, 0.003, shade_hex(skin, 0.7), 0.8)
    if glasses:
        for s in (-1, 1):
            ring = np.abs(cv.ellipse(hx + s * dx, eye_y, rx * 0.27, ry * 0.15)) - 0.004
            cv.put(cv.aa(ring), "#3A3430")
        cv.line(hx - dx + rx * 0.27, eye_y, hx + dx - rx * 0.27, eye_y, 0.004, "#3A3430")


def paint_figure(rng, sheet: str, index: int) -> np.ndarray:
    """A bust or half-length of an invented person in period dress."""
    cv = Canvas()
    _background(cv, rng)
    skin = SKIN[int(rng.integers(len(SKIN)))]
    old = rng.random() < 0.45
    hair = HAIR[int(rng.integers(7, 10))] if old and rng.random() < 0.7 else HAIR[int(rng.integers(0, 7))]
    robe = ROBES[int(rng.integers(len(ROBES)))]
    hx = 0.5 + rng.uniform(-0.03, 0.03)
    hy = rng.uniform(0.33, 0.39)
    rx, ry = rng.uniform(0.12, 0.145), rng.uniform(0.155, 0.18)
    style = ("bald", "short", "long", "bun", "curls")[int(rng.choice(5, p=(0.15, 0.35, 0.22, 0.13, 0.15)))]
    if old and style == "long" and rng.random() < 0.5:
        style = "short"
    _hair_back(cv, hx, hy, rx, ry, hair, style)
    # Body: shoulders and robe, with a centre opening or a sash.
    sw = rng.uniform(0.33, 0.45)
    top = hy + ry * 1.05
    cv.shape(lambda: np.minimum(cv.ellipse(hx, top + 0.42, sw, 0.45), cv.box(hx, 1.0, sw * 0.98, 0.2)), robe, shade=0.3, k=0.05)
    trim = int(rng.integers(5))
    if trim == 0:  # a centre opening with a light shirt
        cv.shape(lambda: cv.poly([(hx - 0.05, top), (hx + 0.05, top), (hx + 0.02, 1.0), (hx - 0.02, 1.0)]), COLLAR[1], shade=0.15)
    elif trim == 1:  # gold buttons
        for k in range(5):
            cv.shape(lambda k=k: cv.ellipse(hx, top + 0.08 + k * 0.08, 0.012, 0.012), GOLD, shade=0.2)
    elif trim == 2:  # a sash across
        sash = ROBES[int(rng.integers(len(ROBES)))]
        cv.shape(lambda: cv.capsule(hx - sw * 0.7, top + 0.05, hx + sw * 0.6, 0.98, 0.035), sash, shade=0.25)
    elif trim == 3:  # a fur stole over the shoulders
        fur = "#E6DED0" if rng.random() < 0.5 else "#7A5A3A"
        for k in range(9):
            a = np.pi * (0.08 + 0.84 * k / 8)
            cv.shape(lambda a=a: cv.ellipse(hx - np.cos(a) * sw * 0.8, top + 0.1 - np.sin(a) * 0.07, 0.05, 0.045), fur,
                     shade=0.2, rim=False)
    else:  # a chain of office
        for k in range(9):
            a = np.pi * (0.15 + 0.7 * k / 8)
            cv.shape(lambda a=a: cv.ellipse(hx - np.cos(a) * sw * 0.55, top + np.sin(a) * 0.16, 0.011, 0.011), GOLD, shade=0.2)
    # Neck and collar.
    cv.shape(lambda: cv.box(hx, top - 0.02, rx * 0.42, 0.05, 0.02), skin, shade=0.3)
    collar = int(rng.integers(4))
    if collar == 0:  # a ruff
        for k in range(11):
            a = np.pi * (k / 10)
            cv.shape(lambda a=a: cv.ellipse(hx - np.cos(a) * rx * 1.15, top + 0.015 + np.sin(a) * 0.03, rx * 0.26, 0.035),
                     COLLAR[0], shade=0.18, rim=False)
    elif collar == 1:  # a falling band collar
        for s in (-1, 1):
            cv.shape(lambda s=s: cv.poly([(hx, top - 0.005), (hx + s * rx * 1.25, top + 0.02), (hx + s * rx * 0.9, top + 0.11),
                                          (hx + s * 0.005, top + 0.06)]), COLLAR[0], shade=0.15)
    elif collar == 2:  # a high collar
        cv.shape(lambda: cv.box(hx, top - 0.005, rx * 0.75, 0.035, 0.01), robe, shade=0.3)
    # Head.
    cv.shape(lambda: cv.ellipse(hx, hy, rx, ry), skin, shade=0.26, k=0.04)
    for s in (-1, 1):  # ears
        cv.shape(lambda s=s: cv.ellipse(hx + s * rx * 0.98, hy + ry * 0.12, rx * 0.15, ry * 0.2), skin, shade=0.3)
    beard = old and rng.random() < 0.55 or (not old and rng.random() < 0.15)
    if beard:
        bl = rng.uniform(0.35, 1.0)
        cv.shape(lambda: np.maximum(cv.ellipse(hx, hy + ry * (0.55 + bl * 0.35), rx * 0.95, ry * (0.5 + bl * 0.45)),
                                    -(cv.Y - (hy + ry * 0.3))), hair, shade=0.25)
    _face(cv, hx, hy, rx, ry, skin, rng, old, rng.random() < 0.15 and old)
    if beard:
        cv.shape(lambda: cv.ellipse(hx, hy + ry * 0.5, rx * 0.45, ry * 0.08), hair, shade=0.2)  # moustache
    _hair_front(cv, hx, hy, rx, ry, hair, style, rng)
    if rng.random() < 0.38:
        _hat(cv, hx, hy, rx, ry, rng)
    # An attribute at the bottom: a book, a scroll, a quill, a wand.
    attr = rng.random()
    if attr < 0.25:
        col = BOOKS[int(rng.integers(len(BOOKS)))]
        cv.shape(lambda: cv.poly([(hx + 0.08, 0.82), (hx + 0.3, 0.78), (hx + 0.32, 0.96), (hx + 0.1, 1.0)]), col, shade=0.25)
        cv.shape(lambda: cv.ellipse(hx + 0.07, 0.9, 0.04, 0.03), skin, shade=0.3)
    elif attr < 0.4:
        cv.shape(lambda: cv.capsule(hx - 0.25, 0.86, hx + 0.05, 0.8, 0.025), COLLAR[2], shade=0.2)
    elif attr < 0.5:
        cv.line(hx + 0.12, 0.98, hx + 0.24, 0.66, 0.007, WOOD[2])
        cv.shape(lambda: cv.ellipse(hx + 0.12, 0.96, 0.04, 0.03), skin, shade=0.3)
    cv.noise(rng, 0.03)
    cv.varnish(0.06, 0.25)
    return cv.img


# -- landscapes ---------------------------------------------------------------------------------


def _ridge(cv: Canvas, rng, base: float, amp: float, freq: float):
    x = cv.X[0]
    y = np.zeros_like(x) + base
    for k in range(4):
        f = freq * (2 ** k)
        y += amp / (1.6 ** k) * np.sin(x * f * np.pi * 2 + rng.uniform(0, 6.28))
    return y


def paint_landscape(rng, sheet: str, index: int) -> np.ndarray:
    """Invented country: a sky, far hills, a lake or a valley, a tower on a
    rise, trees in the foreground; the horizon at 45-60 % of the height."""
    cv = Canvas()
    mood = ("day", "day", "dawn", "dusk", "storm", "night")[int(rng.integers(6))]
    top, low = SKIES[mood]
    horizon = rng.uniform(0.47, 0.58)
    cv.gradient(top, low, 0.0, horizon)
    if mood in ("day", "dawn", "dusk"):
        sx, sy = rng.uniform(0.2, 0.8), rng.uniform(0.15, horizon - 0.12)
        glow = np.clip(1 - np.hypot(cv.X - sx, cv.Y - sy) / 0.35, 0, 1) ** 2
        cv.img = np.clip(cv.img + glow[..., None] * np.array([0.25, 0.2, 0.1]), 0, 1)
        cv.put(cv.aa(cv.ellipse(sx, sy, 0.035, 0.035)), "#FFF1C9" if mood == "day" else "#FFD4A0")
    elif mood == "night":
        stars = rng.random((cv.n, cv.n)) > 0.996
        cv.img[stars & (cv.Y < horizon)] = rgb("#F2F0E0")
        cv.put(cv.aa(cv.ellipse(rng.uniform(0.2, 0.8), rng.uniform(0.12, 0.25), 0.03, 0.03)), "#EDE8D0")
    # Clouds: soft clusters with a lit top edge.
    for _ in range(int(rng.integers(2, 6))):
        cx, cy = rng.uniform(0.0, 1.0), rng.uniform(0.08, horizon - 0.1)
        base = rgb(low) * 0.5 + rgb("#F4F2EE") * 0.5 if mood != "night" else rgb("#4A5878")
        for k in range(5):
            m = cv.aa(cv.ellipse(cx + (k - 2) * 0.05 + rng.uniform(-0.02, 0.02), cy + rng.uniform(-0.015, 0.02),
                                 rng.uniform(0.04, 0.08), rng.uniform(0.02, 0.035)))
            cv.put(m * 0.85, base)
    # Far mountains, nearer hills.
    far = _ridge(cv, rng, horizon - rng.uniform(0.04, 0.12), 0.035, 1.2)
    cv.put(cv.aa(-(cv.Y - far[None, :])), mix(HILLS[0], top, 0.35))
    water = rng.random() < 0.6
    if water:
        cv.put(cv.aa(-(cv.Y - horizon)), WATER[0])
        refl = np.clip((cv.Y - horizon) / 0.3, 0, 1)
        cv.img = np.where((cv.Y > horizon)[..., None], cv.img * (1 - refl[..., None] * 0.3) + rgb(WATER[1]) * refl[..., None] * 0.15, cv.img)
        for k in range(10):
            yy = horizon + 0.02 + k * 0.025
            xx = rng.uniform(0.1, 0.9)
            cv.line(xx - 0.06, yy, xx + 0.06, yy, 0.0025, "#D8E4EE", 0.5)
    near = _ridge(cv, rng, horizon + rng.uniform(0.04, 0.1), 0.05, 0.8)
    hill_col = HILLS[int(rng.integers(1, len(HILLS)))]
    m = cv.aa(-(cv.Y - near[None, :]))
    cv.put(m, hill_col)
    shade = cv.aa(-(cv.Y - near[None, :] - 0.06)) * (cv.X > 0.5)
    cv.put(shade * 0.3, shade_hex(hill_col, 0.75))
    # A tower on a rise.
    if rng.random() < 0.7:
        tx = rng.uniform(0.25, 0.75)
        ty = float(near[int(tx * (cv.n - 1))])
        tw, th = rng.uniform(0.025, 0.04), rng.uniform(0.12, 0.2)
        cv.shape(lambda: cv.box(tx, ty - th / 2, tw, th / 2), STONE[0], shade=0.3, k=0.02)
        cv.shape(lambda: cv.poly([(tx - tw * 1.4, ty - th), (tx + tw * 1.4, ty - th), (tx, ty - th - tw * 3.5)]),
                 ("#6A3A2E", "#3E4A5E", "#5A5E64")[int(rng.integers(3))], shade=0.3, k=0.02)
        if rng.random() < 0.6:
            tx2 = tx + rng.choice((-1, 1)) * tw * 3
            cv.shape(lambda: cv.box(tx2, ty - th * 0.3, tw * 1.6, th * 0.3), STONE[1], shade=0.3, k=0.02)
        cv.put(cv.aa(cv.box(tx, ty - th * 0.6, 0.006, 0.012)), "#2A2420" if mood != "night" else "#FFC870")
    # Foreground: a bank and trees.
    fg = _ridge(cv, rng, rng.uniform(0.84, 0.92), 0.03, 0.6)
    cv.put(cv.aa(-(cv.Y - fg[None, :])), shade_hex(TREE[0], 1.1))
    for _ in range(int(rng.integers(1, 4))):
        x = rng.choice((rng.uniform(0.02, 0.25), rng.uniform(0.75, 0.98)))
        y = float(fg[int(x * (cv.n - 1))])
        cv.line(x, y, x, y - 0.12, 0.008, WOOD[2])
        col = TREE[int(rng.integers(len(TREE)))]
        for k in range(4):
            cv.shape(lambda k=k: cv.ellipse(x + rng.uniform(-0.04, 0.04), y - 0.15 - rng.uniform(0, 0.1), rng.uniform(0.04, 0.07),
                                            rng.uniform(0.05, 0.08)), col, shade=0.3, k=0.03)
    if mood == "night":
        cv.img = np.clip(cv.img * np.array([0.55, 0.62, 0.85]), 0, 1)
    cv.noise(rng, 0.03)
    cv.varnish(0.05, 0.2)
    return cv.img


# -- still lifes --------------------------------------------------------------------------------


def paint_still_life(rng, sheet: str, index: int) -> np.ndarray:
    """Fruit, books, candles, jugs and instruments on a table before a dark
    ground."""
    cv = Canvas()
    dark, light = BACKS[int(rng.integers(len(BACKS)))]
    cv.gradient(shade_hex(dark, 1.1), shade_hex(dark, 0.6), 0.0, 1.0)
    # The objects are laid out at table scale, then seen closer (x 1.7 about
    # the table's middle), so they fill the frame's crop.
    zoom = 1.7
    cv.X = 0.5 + (cv.X - 0.5) / zoom
    cv.Y = 0.7 + (cv.Y - 0.72) / zoom
    cv.px /= zoom
    g = np.clip(1.0 - np.hypot(cv.X - 0.35, cv.Y - 0.3) / 0.7, 0, 1) ** 2
    cv.img = np.clip(cv.img + g[..., None] * rgb(light) * 0.3, 0, 1)
    table = rng.uniform(0.66, 0.74)
    wood = WOOD[int(rng.integers(len(WOOD)))]
    cv.shape(lambda: cv.box(0.5, table + 0.2, 0.6, 0.2), wood, shade=0.0)
    cv.put(cv.aa(cv.box(0.5, table + 0.005, 0.6, 0.006)), shade_hex(wood, 1.3))
    if rng.random() < 0.6:  # a cloth over the table
        col = CLOTH[int(rng.integers(len(CLOTH)))]
        x0 = rng.uniform(0.1, 0.4)
        cv.shape(lambda: cv.poly([(x0, table - 0.01), (x0 + 0.45, table - 0.01), (x0 + 0.5, 1.0), (x0 - 0.08, 1.0)]), col,
                 shade=0.2, k=0.05)
        for k in range(3):
            xx = x0 + 0.1 + k * 0.12
            cv.line(xx, table + 0.02, xx - 0.03, 0.98, 0.004, shade_hex(col, 0.8), 0.7)
    items = list(rng.permutation(["fruit", "books", "candle", "jug", "lute", "goblet", "flowers"]))[: int(rng.integers(3, 5))]
    xs = sorted(0.31 + (k + rng.uniform(0.15, 0.85)) * 0.38 / len(items) for k in range(len(items)))
    for item, x in zip(items, xs):
        base = table + rng.uniform(0.0, 0.05)
        if item == "fruit":
            cv.shape(lambda: np.maximum(cv.ellipse(x, base - 0.02, 0.13, 0.06), cv.Y - base + 0.0), METAL[int(rng.integers(3))],
                     shade=0.3)
            for k in range(int(rng.integers(3, 6))):
                fx, fy = x + rng.uniform(-0.09, 0.09), base - 0.06 - rng.uniform(0, 0.06)
                r = rng.uniform(0.028, 0.042)
                col = FRUIT[int(rng.integers(len(FRUIT)))]
                cv.shape(lambda fx=fx, fy=fy, r=r: cv.ellipse(fx, fy, r, r * rng.uniform(0.85, 1.05)), col, shade=0.3, k=0.03)
                cv.put(cv.aa(cv.ellipse(fx - r * 0.35, fy - r * 0.4, r * 0.25, r * 0.18)) * 0.7, "#FFF6E6")
        elif item == "books":
            y = base
            for k in range(int(rng.integers(2, 5))):
                h = rng.uniform(0.025, 0.04)
                w = rng.uniform(0.09, 0.14)
                col = BOOKS[int(rng.integers(len(BOOKS)))]
                off = rng.uniform(-0.02, 0.02)
                cv.shape(lambda y=y, h=h, w=w, off=off: cv.box(x + off, y - h / 2, w, h / 2, 0.004), col, shade=0.25, k=0.02)
                cv.put(cv.aa(cv.box(x + off + w - 0.012, y - h / 2, 0.006, h / 2 * 0.8)), "#E8DFC8")
                y -= h
        elif item == "candle":
            hgt = rng.uniform(0.12, 0.22)
            cv.shape(lambda: cv.ellipse(x, base - 0.008, 0.045, 0.012), METAL[1], shade=0.25)
            cv.shape(lambda: cv.box(x, base - hgt / 2, 0.016, hgt / 2, 0.004), "#EEE6D2", shade=0.2)
            glow = np.clip(1 - np.hypot(cv.X - x, cv.Y - (base - hgt - 0.03)) / 0.18, 0, 1) ** 2
            cv.img = np.clip(cv.img + glow[..., None] * np.array([0.35, 0.22, 0.08]), 0, 1)
            cv.put(cv.aa(cv.ellipse(x, base - hgt - 0.025, 0.012, 0.026)), FLAME[1])
            cv.put(cv.aa(cv.ellipse(x, base - hgt - 0.02, 0.006, 0.015)), FLAME[0])
        elif item == "jug":
            col = ("#8A5A3A", "#3E5A6E", "#B9B2A4", "#6E3A2A")[int(rng.integers(4))]
            cv.shape(lambda: cv.ellipse(x, base - 0.08, 0.065, 0.08), col, shade=0.3, k=0.04)
            cv.shape(lambda: cv.box(x, base - 0.17, 0.028, 0.03, 0.006), col, shade=0.3)
            cv.put(cv.aa(np.abs(cv.ellipse(x + 0.07, base - 0.1, 0.025, 0.04)) - 0.006), shade_hex(col, 0.8))
        elif item == "lute":
            col = WOOD[1]
            cv.shape(lambda: cv.ellipse(x, base - 0.07, 0.08, 0.06, -0.5), col, shade=0.3, k=0.04)
            cv.shape(lambda: cv.capsule(x + 0.04, base - 0.1, x + 0.16, base - 0.22, 0.012), WOOD[2], shade=0.2)
            cv.put(cv.aa(cv.ellipse(x - 0.005, base - 0.075, 0.018, 0.018)), "#2A1E16")
        elif item == "goblet":
            col = METAL[int(rng.integers(2))]
            cv.shape(lambda: cv.ellipse(x, base - 0.004, 0.03, 0.008), col, shade=0.25)
            cv.shape(lambda: cv.box(x, base - 0.04, 0.006, 0.035), col, shade=0.25)
            cv.shape(lambda: np.maximum(cv.ellipse(x, base - 0.1, 0.035, 0.05), cv.Y - (base - 0.075)), col, shade=0.3)
        else:  # flowers in a vase
            col = ("#3E5A6E", "#B9B2A4", "#8A5A3A")[int(rng.integers(3))]
            for k in range(6):
                fx, fy = x + rng.uniform(-0.07, 0.07), base - 0.18 - rng.uniform(0, 0.1)
                cv.line(x, base - 0.08, fx, fy, 0.004, "#3D5E36")
                cv.shape(lambda fx=fx, fy=fy: cv.ellipse(fx, fy, 0.022, 0.02), FRUIT[int(rng.integers(len(FRUIT)))], shade=0.25,
                         k=0.02)
            cv.shape(lambda: cv.ellipse(x, base - 0.05, 0.04, 0.055), col, shade=0.3, k=0.03)
    cv.noise(rng, 0.03)
    cv.varnish(0.07, 0.25)
    return cv.img


# -- the guardian (sheet b, canvas 63) -----------------------------------------------------------


def paint_guardian(rng) -> np.ndarray:
    """Our own design: a tall elderly keeper in a deep green robe with gold
    edging, a long grey beard, a lantern raised in one hand and a ring of
    keys in the other, standing under a pointed stone arch. Kept in the
    middle 71 % of the width (the frame's crop, kit_portrait_guardian_a)."""
    cv = Canvas()
    cv.gradient("#2A2E36", "#1A1C22", 0.0, 1.0)
    # The stone arch round the figure: blocks in two tones, dark beyond.
    outer = cv.aa(np.minimum(cv.box(0.5, 0.62, 0.33, 0.4), cv.ellipse(0.5, 0.3, 0.33, 0.28)))
    inner_d = np.minimum(cv.box(0.5, 0.66, 0.24, 0.36), cv.ellipse(0.5, 0.34, 0.24, 0.22))
    inner = cv.aa(inner_d)
    ring = np.clip(outer - inner, 0, 1)
    blocks = (np.floor(np.arctan2(cv.Y - 0.34, cv.X - 0.5) * 5) + np.floor(cv.Y * 14)) % 2
    stone = rgb(STONE[0]) * (1 - blocks[..., None] * 0.12)
    cv.img = cv.img * (1 - ring[..., None]) + stone * ring[..., None]
    joints = np.abs(np.sin(cv.Y * 14 * np.pi)) < 0.06
    cv.img[joints & (ring > 0.5)] *= 0.8
    # Beyond the arch: a warm, dim hall.
    cv.put(inner, "#3A3024")
    floor = cv.aa(-(cv.Y - 0.86)) * inner
    cv.put(floor, "#4A3E30")
    # The keeper: a long robe from the shoulders to the floor.
    hx, hy = 0.5, 0.25
    robe = GUARDIAN_ROBE
    cv.shape(lambda: cv.poly([(hx - 0.11, 0.36), (hx + 0.11, 0.36), (hx + 0.19, 0.95), (hx - 0.19, 0.95)]), robe, shade=0.32, k=0.05)
    cv.shape(lambda: cv.ellipse(hx, 0.38, 0.13, 0.06), robe, shade=0.3)
    # Gold edging down the front and at the hem, a belt.
    cv.shape(lambda: cv.poly([(hx - 0.02, 0.38), (hx + 0.02, 0.38), (hx + 0.03, 0.95), (hx - 0.03, 0.95)]), GUARDIAN_TRIM, shade=0.2)
    cv.shape(lambda: cv.box(hx, 0.94, 0.19, 0.012), GUARDIAN_TRIM, shade=0.2)
    cv.shape(lambda: cv.box(hx, 0.58, 0.135, 0.012), "#4A3020", shade=0.2)
    # Sleeves: the left arm raised with the lantern, the right holding keys low.
    cv.shape(lambda: cv.capsule(hx - 0.1, 0.42, hx - 0.2, 0.3, 0.04), robe, shade=0.3)
    cv.shape(lambda: cv.capsule(hx + 0.1, 0.42, hx + 0.16, 0.62, 0.038), robe, shade=0.3)
    skin = SKIN[1]
    cv.shape(lambda: cv.ellipse(hx - 0.21, 0.28, 0.025, 0.022), skin, shade=0.25)
    cv.shape(lambda: cv.ellipse(hx + 0.165, 0.645, 0.024, 0.022), skin, shade=0.25)
    # The lantern: iron cap and frame, warm glass, its glow.
    lx, ly = hx - 0.21, 0.2
    glow = np.clip(1 - np.hypot(cv.X - lx, cv.Y - ly) / 0.3, 0, 1) ** 2
    cv.img = np.clip(cv.img + glow[..., None] * np.array([0.4, 0.26, 0.09]), 0, 1)
    cv.line(lx, 0.27, lx, 0.235, 0.004, "#2A2626")
    cv.shape(lambda: cv.box(lx, ly, 0.03, 0.04, 0.004), "#FFC46A", shade=0.15, rim=False)
    cv.shape(lambda: cv.poly([(lx - 0.038, ly - 0.04), (lx + 0.038, ly - 0.04), (lx, ly - 0.075)]), "#2E2B2E", shade=0.2)
    cv.shape(lambda: cv.box(lx, ly + 0.045, 0.036, 0.007), "#2E2B2E", shade=0.2)
    for s in (-1, 1):
        cv.line(lx + s * 0.03, ly - 0.04, lx + s * 0.03, ly + 0.04, 0.003, "#2E2B2E")
    # The ring of keys.
    kx, ky = hx + 0.175, 0.7
    cv.put(cv.aa(np.abs(cv.ellipse(kx, ky, 0.028, 0.028)) - 0.004), GOLD)
    for k, a in enumerate((0.3, 0.9, 1.6, 2.3)):
        x0, y0 = kx + np.cos(a) * 0.028, ky + np.sin(a) * 0.028
        x1, y1 = kx + np.cos(a) * 0.07, ky + np.sin(a) * 0.07 + 0.02
        cv.line(x0, y0, x1, y1, 0.0035, GOLD)
        cv.put(cv.aa(cv.box(x1, y1, 0.006, 0.004)), GOLD)
    # Head: an elderly face, long grey hair and beard.
    hair = HAIR[8]
    rx, ry = 0.06, 0.075
    cv.shape(lambda: cv.ellipse(hx, hy + 0.04, rx * 1.4, ry * 1.5), hair, shade=0.3)
    cv.shape(lambda: cv.ellipse(hx, hy, rx, ry), skin, shade=0.26, k=0.025)
    cv.shape(lambda: np.maximum(cv.ellipse(hx, hy + ry * 1.25, rx * 0.95, ry * 1.35), -(cv.Y - (hy + ry * 0.35))), hair, shade=0.25)
    _face(cv, hx, hy, rx, ry, skin, rng, True, False)
    cv.shape(lambda: cv.ellipse(hx, hy + ry * 0.5, rx * 0.5, ry * 0.09), hair, shade=0.2)
    cv.shape(lambda: np.maximum(cv.ellipse(hx, hy - ry * 0.2, rx * 1.08, ry * 0.9), cv.Y - (hy - ry * 0.35)), hair, shade=0.28)
    # A plain deep green hood resting on the shoulders.
    cv.shape(lambda: np.maximum(cv.ellipse(hx, hy + 0.06, 0.1, 0.12), -cv.ellipse(hx, hy + 0.03, 0.075, 0.105)), robe, shade=0.3)
    cv.noise(rng, 0.025)
    cv.varnish(0.05, 0.2)
    return cv.img


# -- frames and sheets ---------------------------------------------------------------------------


def frame_ring(colour: np.ndarray, kind: str, rng) -> np.ndarray:
    """The 256 px cell's frame ring and padding: a moulding across the ring
    (outer bead, raised band, hollow, bright crest, inner lip), lit on the top
    and left sides, shaded on the bottom and right, mitred at the corners;
    gilt frames carry a running leaf ornament. RGB float [256, 256, 3]."""
    n = CELL - 2 * PAD
    c = np.arange(CELL, dtype=np.float32) + 0.5
    X, Y = np.meshgrid(c, c)
    d_left, d_top = X - PAD, Y - PAD
    d_right, d_bottom = PAD + n - X, PAD + n - Y
    dist = np.minimum(np.minimum(d_left, d_right), np.minimum(d_top, d_bottom))
    p = np.clip(dist / RING, 0, 1)  # 0 at the outer edge .. 1 at the painting
    side_top = (d_top <= d_left) & (d_top <= d_right) & (d_top <= d_bottom)
    side_left = (d_left < d_top) & (d_left <= d_right) & (d_left <= d_bottom)
    side_bottom = (d_bottom < d_top) & (d_bottom < d_left) & (d_bottom <= d_right)
    light = np.where(side_top, 0.12, np.where(side_left, 0.06, np.where(side_bottom, -0.12, -0.06)))
    prof = np.select([p < 0.12, p < 0.3, p < 0.45, p < 0.7, p < 0.85, p <= 1.0],
                     [-0.25, 0.1, -0.15, 0.18, 0.0, -0.3], default=-0.3)
    along = np.where(side_top | side_bottom, X, Y)
    v = prof + light
    if kind == "frame_gilt":
        orn = (np.sin(along / 5.0) * np.sin(along / 2.5 + 1.0)) * ((p > 0.45) & (p < 0.7))
        v = v + orn * 0.12
    elif kind == "frame_oak":
        v = v + (np.sin(along / 3.1 + p * 4) * 0.03)
    k = np.clip(1.0 + v, 0.35, 1.5)[..., None]
    col = np.clip(colour[None, None, :] * k + np.maximum(v, 0)[..., None] * 0.15, 0, 1)
    pad = dist < 0
    col[pad] = colour * 0.55
    return col.astype(np.float32)


def canvas_kind(index: int) -> str:
    for kind, (lo, hi) in G.PORTRAIT_KINDS.items():
        if lo <= index < hi:
            return kind
    raise ValueError(index)


def paint_sheet(sheet: str, spec_path: str | None = None) -> np.ndarray:
    """One portrait sheet (`portraits_a` / `portraits_b`): float32 RGBA,
    rows bottom to top (paint.py order), albedo fitted."""
    names = props_atlas.palette(spec_path) if spec_path else props_atlas.palette()
    frames = {k: rgb(names[k]) for k in FRAME_NAMES}
    material = "castle_portraits_" + sheet.split("_")[-1]
    img = np.zeros((G.PORTRAIT_SHEET_PX, G.PORTRAIT_SHEET_PX, 4), dtype=np.float32)
    img[..., 3] = 1.0
    seed0 = SHEET_SEEDS[sheet]
    for i in range(G.PORTRAIT_CANVASES):
        rng = np.random.default_rng(seed0 + i * 7919)
        guardian = (material, i) == G.GUARDIAN_CANVAS
        frame = "frame_gilt" if guardian else FRAME_NAMES[int(rng.choice(3, p=FRAME_WEIGHTS))]
        if guardian:
            painting = paint_guardian(rng)
        else:
            kind = canvas_kind(i)
            painting = {"figure": paint_figure, "landscape": paint_landscape, "still_life": paint_still_life}[kind](rng, sheet, i)
        cell = frame_ring(frames[frame], frame, rng)
        o = PAD + RING
        cell[o:o + N, o:o + N] = painting
        x0, y0 = G.canvas_cell(i)
        img[y0:y0 + CELL, x0:x0 + CELL, :3] = cell
    out = img[::-1].copy()  # top-first painting -> paint.py rows (bottom first)
    return paint.fit_albedo(out)


def contact_sheet(out_path: str, spec_path: str | None = None) -> None:
    """Both sheets side by side, halved (a review image; not shipped)."""
    from castle_kit import textures as T

    a, b = paint_sheet("portraits_a", spec_path), paint_sheet("portraits_b", spec_path)
    board = np.concatenate([paint.half(a), np.ones((1024, 16, 4), dtype=np.float32) * 0.2, paint.half(b)], axis=1)
    board[..., 3] = 1.0
    with open(out_path, "wb") as f:
        f.write(T.png_bytes(board))


if __name__ == "__main__":
    contact_sheet(sys.argv[1] if len(sys.argv) > 1 else "portraits_contact.png")
