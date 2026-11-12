"""The talking portraits' four painted backdrops (NPCs, docs/art/castle-kit.md
"Portraits and tapestries"), numpy only (no `bpy`): `backdrop_<n>.png`, 512 x 512.

Each is a little painted room the subject stands in, laid out for the
shadow box's UVs (frames.backdrop_faces): the outer RING_PX are a painted
gilt moulding (the LOD1 box's front shows the whole texture), inside it the
opening's square; the back of the box is the middle back_scale() of that
square and the bands round it are the box's inner sides, so each scene
puts its walls, floor and lintel there and keeps the middle calm and a
touch lighter behind the head.

0. drapery: a deep wine curtain swagged to one side with a gilt tie over
   a warm umber ground, a sliver of evening sky beyond;
1. hall: a stone arch on two columns opening onto a pale sky and far blue
   hills, a flagged floor;
2. lake: a lake under a high sky, hills and a far tower on the shore, dark
   pines framing it, grass in front;
3. study: shelves of leather books on both sides, a navy drape, a candle's
   warm glow, a desk's edge in front.

Our own invented scenes; no real painting is copied. Colours are castle
palette names only (data/art/castle_textures.json `palette`, read through
castle_kit/props_atlas.palette()); the painterly look is brushwork (short
strokes jittering the value of what lies under them, following each
area's direction), a canvas weave and a warm varnish, seeded and
deterministic. The albedo is fitted like the castle library's
(paint.fit_albedo). Arrays: float32 RGBA, rows bottom to top (paint.py)."""

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
from castle_kit import props_atlas  # noqa: E402
from castle_kit.portraits import Canvas  # noqa: E402
from npcs import frames as FR  # noqa: E402

N = FR.TEX_PX
COUNT = 4
NAMES = ("drapery", "hall", "lake", "study")
SEEDS = (4101, 4203, 4307, 4409)
# Every colour a backdrop uses, by castle palette name (no hex here).
USED = {
    "drapery": ("tapestry_umber", "soot", "wine_red", "tapestry_wine", "tapestry_rose", "gilt", "frame_gilt",
                "tapestry_sky", "tapestry_ochre", "rug_border"),
    "hall": ("statue_stone", "tapestry_stone", "beast_stone", "compass_dark", "compass_light", "tapestry_sky",
             "lake_mist", "backdrop_rock", "soot", "tapestry_ochre"),
    "lake": ("tapestry_sky", "lake_mist", "tapestry_cream", "backdrop_rock", "map_green", "lake_shallow", "lake_deep",
             "lake_band", "foliage_pine_deep", "backdrop_forest", "crag_rock", "roof_slate", "leaf_light",
             "foliage_hedge", "soot"),
    "study": ("oak_aged", "oak", "frame_ebony", "soot", "book_red", "book_green", "book_blue", "book_brown",
              "book_ochre", "glow_candle", "tapestry_navy", "letter_white", "tapestry_umber"),
    "ring": ("frame_gilt", "gilt", "soot", "frame_ebony"),
}


def _names() -> dict:
    return props_atlas.palette()


def bands() -> tuple[float, float, float, float]:
    """(inner lo, back lo, back hi, inner hi) in canvas units (0..1, y down
    or up alike): the opening's square inside the ring and the box's back."""
    lo, hi = FR.tex_inner()
    mid, half = (lo + hi) / 2, (hi - lo) / 2
    s = FR.back_scale()
    return lo, mid - half * s, mid + half * s, hi


class Painter:
    def __init__(self, seed: int):
        self.cv = Canvas(N)
        self.rng = np.random.default_rng(seed)
        self.pal = _names()

    def c(self, name: str) -> np.ndarray:
        return paint.hex_rgb(self.pal[name]).astype(np.float32)

    def mix(self, a: str, b: str, t) -> np.ndarray:
        t = np.asarray(t, dtype=np.float32)
        return self.c(a) * (1 - t[..., None]) + self.c(b) * t[..., None] if t.ndim else self.c(a) * (1 - t) + self.c(b) * t

    def vgrad(self, top: str, bottom: str, y0=0.0, y1=1.0, mask=None) -> None:
        t = np.clip((self.cv.Y - y0) / max(1e-6, y1 - y0), 0, 1)
        col = self.mix(top, bottom, t)
        if mask is None:
            self.cv.img = col
        else:
            m = mask[..., None]
            self.cv.img = self.cv.img * (1 - m) + col * m

    def put(self, mask, colour) -> None:
        self.cv.put(np.clip(mask, 0, 1), colour)

    def strokes(self, count: int, length: float, width: float, angle: float, jitter: float, spread: float = 0.5,
                region=None, amount: float = 0.09) -> None:
        """Short strokes over the canvas (or `region` (x0, y0, x1, y1)): each
        lightens or darkens what lies under it by up to `amount` (the hue and
        the edges stay), tapering at its ends, at `angle` (radians, y down)
        +- `spread`."""
        cv, rng = self.cv, self.rng
        x0, y0, x1, y1 = region or (0.0, 0.0, 1.0, 1.0)
        n = cv.n
        for _ in range(count):
            cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
            a = angle + rng.uniform(-spread, spread)
            ln = length * rng.uniform(0.6, 1.3)
            dx, dy = np.cos(a) * ln / 2, np.sin(a) * ln / 2
            r = width * rng.uniform(0.7, 1.2)
            bx0 = max(0, int((min(cx - dx, cx + dx) - r) * n) - 1)
            bx1 = min(n, int((max(cx - dx, cx + dx) + r) * n) + 2)
            by0 = max(0, int((min(cy - dy, cy + dy) - r) * n) - 1)
            by1 = min(n, int((max(cy - dy, cy + dy) + r) * n) + 2)
            if bx1 <= bx0 or by1 <= by0:
                continue
            X, Y = cv.X[by0:by1, bx0:bx1], cv.Y[by0:by1, bx0:bx1]
            px, py = X - (cx - dx), Y - (cy - dy)
            ex, ey = 2 * dx, 2 * dy
            h = np.clip((px * ex + py * ey) / max(1e-9, ex * ex + ey * ey), 0, 1)
            d = np.hypot(px - ex * h, py - ey * h) - r
            m = np.clip(0.5 - d * n, 0, 1) * (0.55 + 0.45 * np.sin(h * np.pi))
            k = rng.uniform(-amount, amount) + jitter * rng.normal() * 0.01
            sub = cv.img[by0:by1, bx0:bx1]
            cv.img[by0:by1, bx0:bx1] = np.clip(sub * (1.0 + m[..., None] * k), 0, 1)

    def weave(self, amount: float = 0.025) -> None:
        cv = self.cv
        n = cv.n
        i = np.arange(n, dtype=np.float32)
        wv = (np.sin(i * np.pi * 0.5)[None, :] * 0.5 + np.sin(i * np.pi * 0.5)[:, None] * 0.5)
        cv.img = np.clip(cv.img * (1.0 + wv[..., None] * amount), 0, 1)

    def glow(self, cx: float, cy: float, r: float, colour: str, k: float) -> None:
        g = np.clip(1.0 - np.hypot(self.cv.X - cx, self.cv.Y - cy) / r, 0, 1) ** 2
        self.cv.img = np.clip(self.cv.img + g[..., None] * self.c(colour) * k, 0, 1)

    def shade_band(self, side: str, k: float) -> None:
        """Darkens one of the box's side bands, most at the back's edge (the
        painted shadow inside the box)."""
        lo, b0, b1, hi = bands()
        X, Y = self.cv.X, self.cv.Y
        if side == "top":
            inside, t = Y < b0, (b0 - Y) / (b0 - lo)
        elif side == "bottom":
            inside, t = Y > b1, (Y - b1) / (hi - b1)
        elif side == "left":
            inside, t = X < b0, (b0 - X) / (b0 - lo)
        else:
            inside, t = X > b1, (X - b1) / (hi - b1)
        f = np.where(inside, 1.0 - k * (1.0 - 0.6 * np.clip(t, 0, 1)), 1.0).astype(np.float32)
        self.cv.img = np.clip(self.cv.img * f[..., None], 0, 1)


# -- the four scenes -----------------------------------------------------------------------


def _drapery(p: Painter) -> None:
    cv = p.cv
    lo, b0, b1, hi = bands()
    p.vgrad("tapestry_umber", "soot", 0.1, 1.0)
    p.glow(0.52, 0.38, 0.4, "tapestry_ochre", 0.2)
    # A sliver of evening sky through a tall window at the right of the back.
    wx = b1 - 0.1
    win = cv.box(wx, 0.36, 0.045, 0.14)
    sky = p.mix("tapestry_sky", "tapestry_ochre", np.clip((cv.Y - 0.24) / 0.26, 0, 1))
    m = cv.aa(win)
    cv.img = cv.img * (1 - m[..., None]) + sky * m[..., None]
    p.put(cv.aa(cv.box(wx, 0.36, 0.055, 0.15)) - m, p.c("soot"))
    p.put(cv.aa(cv.box(wx, 0.36, 0.004, 0.14)), p.c("soot"))
    p.strokes(500, 0.06, 0.012, 0.4, 1.0, 1.0, (0.0, 0.0, 1.0, 1.0), 0.05)
    # The curtains: a broad one falling down the left, swagged back to a
    # tie; a narrow one on the right band.
    def curtain(edge, side):
        inside = cv.aa((cv.X - edge) * side)
        ph = cv.X * 52 + np.sin(cv.Y * 5.0) * 2.0
        folds = 0.5 + 0.5 * np.sin(ph)
        col = p.mix("tapestry_wine", "wine_red", folds) * (0.8 + 0.2 * folds[..., None])
        lit = np.clip(1 - np.abs(np.sin(ph + 0.9)) * 5, 0, 1) * 0.3
        col = col * (1 - lit[..., None]) + p.c("tapestry_rose") * lit[..., None]
        cv.img = cv.img * (1 - inside[..., None]) + col * inside[..., None]
    sw = np.clip((cv.Y - 0.3) / 0.38, 0, 1)
    curtain(b0 + 0.13 - 0.1 * sw * sw + 0.012 * np.sin(cv.Y * 30), 1.0)
    curtain(b1 + 0.03 + 0.01 * np.sin(cv.Y * 24), -1.0)
    # The valance across the top band, fringed in gilt.
    wave = b0 + 0.03 + 0.022 * np.cos((cv.X - 0.5) * 22)
    val = cv.aa(cv.Y - wave)
    vcol = p.mix("wine_red", "tapestry_wine", 0.5 + 0.5 * np.cos((cv.X - 0.5) * 22))
    cv.img = cv.img * (1 - val[..., None]) + vcol * val[..., None]
    p.put(cv.aa(np.abs(cv.Y - wave) - 0.005), p.c("gilt"))
    # The gilt tie where the broad curtain is swagged.
    ty = 0.68
    tx = b0 + 0.03
    cv.shape(lambda: cv.ellipse(tx, ty, 0.045, 0.014), p.c("frame_gilt"), shade=0.25)
    # A stone sill across the bottom band.
    p.vgrad("rug_border", "tapestry_umber", b1, hi, cv.aa(b1 + 0.006 - cv.Y))
    p.put(cv.aa(np.abs(cv.Y - (b1 + 0.006)) - 0.003), p.c("soot"))
    p.strokes(1200, 0.05, 0.007, 1.5, 1.0, 0.15, (0.0, 0.0, 1.0, b1), 0.07)
    p.strokes(400, 0.06, 0.008, 0.0, 1.0, 0.2, (0.0, b1, 1.0, 1.0), 0.07)


def _hall(p: Painter) -> None:
    cv = p.cv
    lo, b0, b1, hi = bands()
    # The sky and far hills through the arch.
    p.vgrad("tapestry_sky", "lake_mist", 0.1, 0.72)
    hills = cv.aa(cv.Y - (0.66 + 0.035 * np.sin(cv.X * 9.0 + 1.3) + 0.015 * np.sin(cv.X * 23.0)))
    p.put(1 - hills, p.c("backdrop_rock"))
    p.glow(0.5, 0.3, 0.45, "tapestry_ochre", 0.12)
    # The arch: the stone wall with a round-headed opening in the back.
    ax, aw, ay = 0.5, 0.25, 0.42
    opening = np.minimum(cv.box(ax, 0.72, aw, 0.3), cv.ellipse(ax, ay, aw, aw * 0.95))
    wall = cv.aa(-opening)
    stone = p.mix("statue_stone", "tapestry_stone", np.clip(cv.Y, 0, 1))
    cv.img = cv.img * (1 - wall[..., None]) + stone * wall[..., None]
    # Voussoirs round the head and a keystone.
    ring = cv.aa(np.abs(cv.ellipse(ax, ay, aw + 0.035, (aw + 0.035) * 0.95)) - 0.022) * (cv.Y < ay + 0.01)
    p.put(ring * 0.8, p.c("beast_stone"))
    for k in range(9):
        a = np.pi * (k + 0.5) / 9
        x, y = ax - np.cos(a) * (aw + 0.035), ay - np.sin(a) * (aw + 0.035) * 0.95
        p.cv.line(x - np.cos(a) * 0.02, y - np.sin(a) * 0.02, x + np.cos(a) * 0.02, y + np.sin(a) * 0.02, 0.0025,
                  p.c("compass_dark"), 0.6)
    # Columns on the side bands.
    for cx in ((lo + b0) / 2 + 0.01, (b1 + hi) / 2 - 0.01):
        cv.shape(lambda cx=cx: cv.box(cx, 0.55, 0.05, 0.6), p.c("statue_stone"), shade=0.3)
        cv.shape(lambda cx=cx: cv.box(cx, b0 + 0.02, 0.07, 0.025), p.c("tapestry_stone"), shade=0.25)
    # The flagged floor across the bottom band.
    floor = cv.aa(b1 + 0.01 - cv.Y)
    p.vgrad("compass_light", "compass_dark", b1, hi, floor)
    for k in range(7):
        x = lo + (hi - lo) * k / 6
        p.cv.line(0.5 + (x - 0.5) * 0.78, b1 + 0.01, x, hi, 0.002, p.c("compass_dark"), 0.5 * floor.max())
    p.strokes(900, 0.06, 0.01, 0.0, 1.0, 0.35, (0.0, 0.0, 1.0, 0.7), 0.05)
    p.strokes(700, 0.05, 0.008, 1.57, 1.0, 0.2, (0.0, 0.1, 1.0, 1.0), 0.06)


def _lake(p: Painter) -> None:
    cv = p.cv
    lo, b0, b1, hi = bands()
    p.vgrad("tapestry_sky", "lake_mist", 0.05, 0.55)
    # Soft clouds.
    for k in range(5):
        cx, cy = p.rng.uniform(0.15, 0.85), p.rng.uniform(0.12, 0.32)
        cl = cv.aa(cv.ellipse(cx, cy, p.rng.uniform(0.08, 0.16), p.rng.uniform(0.025, 0.045)))
        p.put(cl * 0.6, p.c("tapestry_cream"))
    horizon = 0.56
    far = cv.aa(cv.Y - (horizon - 0.06 - 0.04 * np.sin(cv.X * 6.0 + 2.0) - 0.02 * np.sin(cv.X * 17.0)))
    p.put((1 - far) * (cv.Y < horizon), p.c("backdrop_rock"))
    near = cv.aa(cv.Y - (horizon - 0.02 - 0.03 * np.sin(cv.X * 4.0 + 0.4)))
    p.put((1 - near) * (cv.Y < horizon) * (np.abs(cv.X - 0.62) > 0.08), p.c("map_green"))
    p.strokes(900, 0.06, 0.009, 0.0, 1.0, 0.25, (0.0, 0.0, 1.0, horizon), 0.06)
    # A far tower on the shore.
    tx = 0.66
    cv.shape(lambda: cv.box(tx, horizon - 0.06, 0.016, 0.06), p.c("crag_rock"), shade=0.25, rim=False)
    cv.shape(lambda: cv.box(tx + 0.035, horizon - 0.035, 0.024, 0.035), p.c("crag_rock"), shade=0.25, rim=False)
    cv.shape(lambda: cv.poly([(tx - 0.024, horizon - 0.118), (tx + 0.024, horizon - 0.118), (tx, horizon - 0.17)]),
             p.c("roof_slate"), shade=0.2, rim=False)
    # The lake.
    water = cv.aa(horizon - cv.Y)
    p.vgrad("lake_shallow", "lake_deep", horizon, b1 + 0.05, water)
    for k in range(10):
        y = horizon + 0.012 + k * 0.02
        x = p.rng.uniform(0.25, 0.75)
        p.cv.line(x - 0.05, y, x + 0.05, y, 0.0018, p.c("lake_band"), 0.7)
    # Pines framing the sides.
    for side in (0, 1):
        for k in range(4):
            cx = (lo + b0) / 2 + p.rng.uniform(-0.05, 0.05) if side == 0 else (b1 + hi) / 2 + p.rng.uniform(-0.05, 0.05)
            base = hi - p.rng.uniform(0.0, 0.08)
            top = p.rng.uniform(0.08, 0.3)
            col = "foliage_pine_deep" if k % 2 else "backdrop_forest"
            cv.shape(lambda cx=cx, base=base, top=top: cv.poly([(cx - 0.07, base), (cx + 0.07, base), (cx, top)]),
                     p.c(col), shade=0.25, rim=False)
    # The grass in front.
    grass = cv.aa(cv.Y - (b1 + 0.015 + 0.01 * np.sin(cv.X * 14.0)))
    p.put(1 - grass, np.zeros(3))  # placeholder cleared by the gradient below
    p.vgrad("leaf_light", "foliage_hedge", b1, hi, 1 - grass)
    p.strokes(700, 0.035, 0.005, -1.4, 1.0, 0.4, (0.0, b1, 1.0, 1.0), 0.1)
    p.strokes(500, 0.04, 0.006, -1.2, 1.0, 0.5, (0.0, 0.05, 0.25, 1.0), 0.08)
    p.strokes(500, 0.04, 0.006, -1.9, 1.0, 0.5, (0.75, 0.05, 1.0, 1.0), 0.08)


def _study(p: Painter) -> None:
    cv = p.cv
    lo, b0, b1, hi = bands()
    p.vgrad("tapestry_umber", "frame_ebony", 0.0, 1.0)
    # The navy drape behind, softly folded.
    folds = 0.5 + 0.5 * np.sin(cv.X * 30 + np.sin(cv.Y * 4.0) * 1.5)
    drape = cv.aa(np.abs(cv.X - 0.5) - (b1 - b0) * 0.42)
    col = p.c("tapestry_navy") * (0.62 + 0.38 * folds[..., None])
    cv.img = cv.img * (1 - drape[..., None]) + col * drape[..., None]
    # Shelves of books on the side bands (and over the back's edges).
    books = ("book_red", "book_green", "book_blue", "book_brown", "book_ochre")
    for x0, x1 in ((0.0, b0 + 0.06), (b1 - 0.06, 1.0)):
        shelf = cv.aa(np.maximum(x0 - cv.X, cv.X - x1))
        p.put(shelf, p.c("oak_aged"))
        y = 0.1
        while y < hi:
            row_h = p.rng.uniform(0.11, 0.14)
            x = x0
            while x < x1:
                bw = p.rng.uniform(0.012, 0.024)
                bh = row_h * p.rng.uniform(0.7, 0.92)
                name = books[int(p.rng.integers(len(books)))]
                cv.shape(lambda x=x, bw=bw, bh=bh, y=y: cv.box(x + bw / 2, y + row_h - bh / 2, bw / 2 - 0.001, bh / 2),
                         p.c(name), shade=0.18, rim=False)
                x += bw
            cv.shape(lambda y=y: cv.box((x0 + x1) / 2, y + row_h + 0.007, (x1 - x0) / 2, 0.008), p.c("oak"), shade=0.3,
                     rim=False)
            y += row_h + 0.016
    # The candle's glow and the desk's edge in front.
    p.glow(b1 - 0.06, b1 - 0.07, 0.16, "glow_candle", 0.3)
    p.glow(0.5, 0.36, 0.38, "tapestry_umber", 0.25)
    desk = cv.aa(b1 + 0.012 - cv.Y)
    p.vgrad("oak", "oak_aged", b1, hi, desk)
    p.strokes(900, 0.05, 0.008, 1.57, 1.0, 0.2, (0.0, 0.0, 1.0, 1.0), 0.07)
    p.strokes(500, 0.06, 0.01, 0.0, 1.0, 0.3, (0.0, b1, 1.0, 1.0), 0.06)
    cv.shape(lambda: cv.box(b1 - 0.06, b1 - 0.025, 0.009, 0.035), p.c("letter_white"), shade=0.25, rim=False)
    cv.shape(lambda: cv.ellipse(b1 - 0.06, b1 - 0.072, 0.006, 0.013), p.c("glow_candle"), shade=0.0, rim=False)


SCENES = (_drapery, _hall, _lake, _study)


# -- the ring (LOD1's moulding) ----------------------------------------------------------------


def ring(img: np.ndarray, pal: dict) -> None:
    """Paints the outer RING_PX as a gilt moulding (top-first image, in place):
    a dark outer edge, a lit crest, a hollow, a bright sight edge, a dark
    crease against the painting; the top and left rails lit, the others in
    shade (the light from the upper left)."""
    n = img.shape[0]
    r = FR.RING_PX
    i = np.arange(n, dtype=np.float32)
    dist = np.stack([np.broadcast_to(i[:, None], (n, n)),  # to the top
                     np.broadcast_to(i[None, :], (n, n)),  # to the left
                     np.broadcast_to((n - 1 - i)[:, None], (n, n)),  # to the bottom
                     np.broadcast_to((n - 1 - i)[None, :], (n, n))])  # to the right
    rail = np.argmin(dist, axis=0)
    t = np.min(dist, axis=0) / r
    gilt, frame, soot = (paint.hex_rgb(pal[k]).astype(np.float32) for k in ("gilt", "frame_gilt", "soot"))
    bands_ = [(0.12, frame * 0.62), (0.45, gilt), (0.7, frame * 0.82), (0.9, gilt), (1.0, soot)]
    col = np.zeros((n, n, 3), dtype=np.float32)
    for edge, c in reversed(bands_):
        col = np.where((t < edge)[..., None], c, col)
    lit = np.where(rail < 2, 1.0, 0.82).astype(np.float32)
    col = np.clip(col * lit[..., None], 0, 1)
    img[:] = np.where((t < 1.0)[..., None], col, img)


def paint_backdrop(index: int) -> np.ndarray:
    """One backdrop, float32 RGBA, rows bottom to top, albedo fitted."""
    p = Painter(SEEDS[index])
    SCENES[index](p)
    # Each scene's box sides a little in shade (the lintel most).
    p.shade_band("top", 0.28)
    p.shade_band("left", 0.12)
    p.shade_band("right", 0.12)
    p.weave(0.03)
    p.cv.varnish(warm=0.05, vignette=0.12)
    img = p.cv.img
    ring(img, p.pal)
    out = np.ones((N, N, 4), dtype=np.float32)
    out[..., :3] = img[::-1]
    return paint.fit_albedo(out)


def colours_used() -> set[str]:
    return {n for v in USED.values() for n in v}


def contact_sheet(out_path: str) -> None:
    """The four side by side (a review image)."""
    from castle_kit import textures as T

    imgs = [paint_backdrop(i) for i in range(COUNT)]
    gap = np.ones((N, 12, 4), dtype=np.float32) * 0.2
    gap[..., 3] = 1
    row = [imgs[0]]
    for im in imgs[1:]:
        row += [gap, im]
    board = np.concatenate(row, axis=1)
    with open(out_path, "wb") as f:
        f.write(T.png_bytes(board))


if __name__ == "__main__":
    contact_sheet(sys.argv[1] if len(sys.argv) > 1 else "backdrops_contact.png")
