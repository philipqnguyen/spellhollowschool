"""The tapestry sheet (castle M5a, docs/art/castle-kit.md "Textures"): `tapestries`,
1024 x 1024, four invented tapestries for the fifth-floor west corridor
(`kit_tapestry_2.5x4` x 3 and `kit_tapestry_6x3`, material castle_tapestry).

Tiles (data/art/castle_textures.json `sheets.tapestries`): `tapestry_0..2`
320 x 512 px (2.5 x 4 m), `tapestry_3` 1024 x 512 px (6 x 3 m), each the
piece's aspect, mapped by the face's own 0..1 UVs (`fit`).

Everything here is our own invented work, painted by script: no real
tapestry, prop, crest or emblem is copied, there is no text and no
name. The four designs:

- `tapestry_0` "the night watch": a tower on a green hill under a crescent
  moon and a field of stars, on a deep blue ground;
- `tapestry_1` "the woodland": a medallion with a tree in a ring of gold
  over a millefleur ground of small flowers and dark pines;
- `tapestry_2` "the tree of fruit": a tall tree with gold and rose fruit
  and small birds over a lozenge diaper, on a wine-red ground;
- `tapestry_3` "the lake castle": a castle of towers on a hill above a
  lake, hills behind, a low sun, pines in front.

Every tapestry has a border (a dark rim and a gold band of lozenges), a
darker rod sleeve at the top and a fringe at the bottom; the weave is a
faint row texture and two painted folds (flat value bands, as the banners).

Colours: only castle_textures.json `palette` names `tapestry_*` (palette
data, docs/art/castle-kit.md "Colours come from palette data only", "Textures"); shapes take a palette colour,
or a palette colour times a value (lighter or darker, never another hue).
Arrays follow paint.py: float32 RGBA, sRGB, row 0 at the BOTTOM. Seeded and
deterministic (numpy only).
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
from castle_kit import props_atlas, trim  # noqa: E402

SEED = 9100
# Palette names the painter reads (castle_textures.json `palette`).
NAMES = ("tapestry_navy", "tapestry_forest", "tapestry_wine", "tapestry_sky", "tapestry_ochre", "tapestry_cream",
         "tapestry_umber", "tapestry_leaf", "tapestry_rose", "tapestry_stone")
# Each tile's size in metres (the pieces': grid.TAPESTRY_PIECES).
TILE_M = {"tapestry_0": (2.5, 4.0), "tapestry_1": (2.5, 4.0), "tapestry_2": (2.5, 4.0), "tapestry_3": (6.0, 3.0)}
RIM = 0.07  # the border's dark rim (m)
BAND = 0.16  # its gold band of lozenges
SLEEVE = 0.12  # the rod sleeve at the top
FRINGE = 0.07  # the fringe at the bottom


class _Painter:
    """One tile's canvas in metres: X across (0..w), Y up (0..h)."""

    def __init__(self, A, B, size, col: dict, seed: int):
        self.w, self.h = size
        self.X = A * self.w
        self.Y = B * self.h
        self.aa = self.h / A.shape[0]  # metres per pixel
        self.col = col
        self.rng = np.random.default_rng(seed)
        self.img = np.zeros(A.shape + (3,), dtype=np.float32)

    def c(self, name: str, value: float = 1.0) -> np.ndarray:
        return np.clip(self.col[name] * value, 0.0, 1.0)

    def fill(self, mask, name: str, value: float = 1.0) -> None:
        self.img = trim.mix(self.img, self.c(name, value), np.clip(mask, 0.0, 1.0))

    # Shapes: coverage in 0..1 with a pixel of anti-aliasing.
    def rect(self, x0, y0, x1, y1):
        d = np.minimum(np.minimum(self.X - x0, x1 - self.X), np.minimum(self.Y - y0, y1 - self.Y))
        return np.clip(d / self.aa + 0.5, 0.0, 1.0)

    def disc(self, cx, cy, r):
        return trim.disc(self.X, self.Y, cx, cy, r, self.aa)

    def ellipse(self, cx, cy, rx, ry):
        d = np.sqrt(((self.X - cx) / rx) ** 2 + ((self.Y - cy) / ry) ** 2)
        return np.clip((1.0 - d) * min(rx, ry) / self.aa + 0.5, 0.0, 1.0)

    def tri(self, x0, y0, x1, y1, x2, y2):
        """A triangle's coverage (any winding)."""
        def edge(ax, ay, bx, by):
            nx, ny = by - ay, ax - bx
            n = np.hypot(nx, ny) or 1.0
            return ((self.X - ax) * nx + (self.Y - ay) * ny) / n
        e = np.stack([edge(x0, y0, x1, y1), edge(x1, y1, x2, y2), edge(x2, y2, x0, y0)])
        if (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0) > 0:
            e = -e
        return np.clip(np.min(e, axis=0) / self.aa + 0.5, 0.0, 1.0)

    def lozenge(self, cx, cy, rx, ry):
        d = np.abs(self.X - cx) / rx + np.abs(self.Y - cy) / ry
        return np.clip((1.0 - d) * min(rx, ry) / self.aa + 0.5, 0.0, 1.0)

    def star(self, cx, cy, r):
        """A four-pointed star (two thin lozenges)."""
        return np.maximum(self.lozenge(cx, cy, r, r * 0.28), self.lozenge(cx, cy, r * 0.28, r))

    def hill(self, base, amp, period, phase):
        """Below a gentle sine skyline."""
        line = base + amp * np.sin(self.X / period * 2 * np.pi + phase)
        return np.clip((line - self.Y) / self.aa + 0.5, 0.0, 1.0)

    def pine(self, x, y, h, w):
        """A stylized pine: three stacked triangles on a short trunk."""
        m = self.rect(x - w * 0.08, y, x + w * 0.08, y + h * 0.18)
        for k in range(3):
            yb = y + h * (0.15 + 0.25 * k)
            ww = w * (1.0 - 0.25 * k)
            m = np.maximum(m, self.tri(x - ww / 2, yb, x + ww / 2, yb, x, yb + h * 0.42))
        return m

    def tower(self, x, y, w, h, roof: float):
        """A round-ish tower: a body and a cone roof."""
        return np.maximum(self.rect(x - w / 2, y, x + w / 2, y + h), self.tri(x - w * 0.62, y + h, x + w * 0.62, y + h,
                                                                             x, y + h + roof))

    # The frame every tapestry shares.
    def border(self, ground: str) -> None:
        w, h = self.w, self.h
        inner = self.rect(RIM + BAND, FRINGE + RIM + BAND, w - RIM - BAND, h - SLEEVE - RIM - BAND)
        band = self.rect(RIM, FRINGE + RIM, w - RIM, h - SLEEVE - RIM) * (1 - inner)
        rim = (1 - self.rect(RIM, FRINGE + RIM, w - RIM, h - SLEEVE - RIM)) * self.rect(0, FRINGE, w, h - SLEEVE)
        self.fill(band, "tapestry_ochre")
        self.fill(rim, "tapestry_umber")
        # Lozenges along the band, centred on it, a whole number per side.
        mid = RIM + BAND / 2
        for (a0, a1, fixed, horiz) in ((mid, w - mid, FRINGE + mid, True), (mid, w - mid, h - SLEEVE - mid, True),
                                       (FRINGE + mid, h - SLEEVE - mid, mid, False),
                                       (FRINGE + mid, h - SLEEVE - mid, w - mid, False)):
            n = max(2, int(round((a1 - a0) / 0.32)))
            for i in range(n + 1):
                t = a0 + (a1 - a0) * i / n
                cx, cy = (t, fixed) if horiz else (fixed, t)
                self.fill(self.lozenge(cx, cy, BAND * 0.42, BAND * 0.36) * band, ground, 0.9)
                self.fill(self.disc(cx, cy, BAND * 0.1) * band, "tapestry_cream")
        # The sleeve and the fringe.
        self.fill(self.rect(0, h - SLEEVE, w, h + 1), ground, 0.62)
        self.fill(self.rect(0, h - SLEEVE - 0.012, w, h - SLEEVE + 0.012), "tapestry_ochre", 0.85)
        fringe = self.rect(0, -1, w, FRINGE)
        threads = (np.floor(self.X / 0.035) % 2).astype(np.float32)
        self.fill(fringe, "tapestry_cream", 0.92)
        self.fill(fringe * threads, "tapestry_ochre", 0.8)

    def field(self):
        """The area inside the border (a mask)."""
        return self.rect(RIM + BAND, FRINGE + RIM + BAND, self.w - RIM - BAND, self.h - SLEEVE - RIM - BAND)

    def finish(self) -> np.ndarray:
        """Weave rows and two painted folds (flat value bands)."""
        rows = 1.0 + 0.025 * np.sign(np.sin(self.Y / 0.012 * np.pi))
        f = np.sin((self.X / self.w + 0.03 * np.sin(self.Y / self.h * 7)) * 2 * np.pi * (2 if self.w < 4 else 3))
        v = np.where(f > 0.6, 0.06, np.where(f < -0.65, -0.08, 0.0))
        return self.img * (rows * (1.0 + v))[..., None]


def _night_watch(p: _Painter) -> None:
    w, h = p.w, p.h
    f = p.field()
    p.fill(f, "tapestry_navy")
    # A darker band toward the top of the sky.
    p.fill(f * p.rect(0, h * 0.62, w, h), "tapestry_navy", 0.8)
    for _ in range(46):
        x = float(p.rng.uniform(0.35, w - 0.35))
        y = float(p.rng.uniform(1.6, h - 0.5))
        r = float(p.rng.choice([0.04, 0.06, 0.08]))
        p.fill(f * p.star(x, y, r), "tapestry_cream" if p.rng.random() < 0.7 else "tapestry_ochre")
    moon = p.disc(w * 0.7, h * 0.78, 0.32) * (1 - p.disc(w * 0.7 + 0.13, h * 0.78 + 0.07, 0.28))
    p.fill(f * moon, "tapestry_ochre")
    # Hills, the tower on the near one, its lit window.
    p.fill(f * p.hill(1.55, 0.18, 2.2, 0.8), "tapestry_forest", 0.85)
    p.fill(f * p.hill(1.15, 0.12, 1.6, 2.4), "tapestry_forest")
    tower = p.tower(w * 0.42, 1.2, 0.36, 1.25, 0.42)
    p.fill(f * tower, "tapestry_stone")
    p.fill(f * p.rect(w * 0.42 - 0.18, 2.36, w * 0.42 + 0.18, 2.45) * tower, "tapestry_stone", 0.75)
    p.fill(f * p.rect(w * 0.42 - 0.05, 1.95, w * 0.42 + 0.05, 2.15), "tapestry_ochre")
    p.fill(f * p.rect(w * 0.42 - 0.07, 1.2, w * 0.42 + 0.07, 1.42), "tapestry_umber")
    for x in (0.55, 0.78, 1.75, 1.95):
        p.fill(f * p.pine(x, 0.95 if x < 1 else 1.0, 0.55, 0.32), "tapestry_leaf", 0.75)
    # Flowers in the foreground grass.
    for _ in range(40):
        x = float(p.rng.uniform(0.3, w - 0.3))
        y = float(p.rng.uniform(0.32, 0.9))
        p.fill(f * p.disc(x, y, 0.03), "tapestry_cream" if p.rng.random() < 0.5 else "tapestry_rose")


def _woodland(p: _Painter) -> None:
    w, h = p.w, p.h
    f = p.field()
    p.fill(f, "tapestry_forest")
    # Millefleurs: small four-petal flowers and leaf sprigs on the ground.
    for _ in range(150):
        x = float(p.rng.uniform(0.3, w - 0.3))
        y = float(p.rng.uniform(0.35, h - 0.45))
        k = p.rng.random()
        if k < 0.35:
            p.fill(f * p.lozenge(x, y, 0.045, 0.08), "tapestry_leaf")
        else:
            name = "tapestry_cream" if k < 0.6 else ("tapestry_rose" if k < 0.82 else "tapestry_ochre")
            p.fill(f * p.star(x, y, 0.045), name)
            p.fill(f * p.disc(x, y, 0.014), "tapestry_ochre", 0.8)
    for x, y in ((0.55, 0.45), (1.95, 0.45), (0.5, 3.0), (2.0, 3.0)):
        p.fill(f * p.pine(x, y, 0.75, 0.42), "tapestry_forest", 0.65)
    # The medallion: a gold ring round a dark disc with a tree.
    cx, cy, r = w / 2, h * 0.52, 0.86
    p.fill(f * p.disc(cx, cy, r + 0.07), "tapestry_ochre")
    p.fill(f * p.disc(cx, cy, r), "tapestry_navy", 0.9)
    p.fill(f * p.disc(cx, cy, r) * p.hill(cy - 0.35, 0.05, 1.0, 0.0), "tapestry_forest", 0.9)
    p.fill(f * p.rect(cx - 0.06, cy - 0.45, cx + 0.06, cy + 0.05), "tapestry_umber")
    for dx, dy, rr in ((0, 0.28, 0.32), (-0.24, 0.12, 0.22), (0.24, 0.12, 0.22), (-0.14, 0.4, 0.2), (0.15, 0.42, 0.2)):
        p.fill(f * p.disc(cx + dx, cy + dy, rr), "tapestry_leaf")
    for dx, dy in ((-0.2, 0.3), (0.12, 0.18), (0.02, 0.5), (0.25, 0.36), (-0.3, 0.08)):
        p.fill(f * p.disc(cx + dx, cy + dy, 0.04), "tapestry_ochre")
    for k in range(12):  # small stars in the ring's sky
        a = np.pi * (0.15 + 0.7 * k / 11)
        p.fill(f * p.star(cx + 0.68 * np.cos(a), cy + 0.68 * np.sin(a) * 0.9, 0.035), "tapestry_cream")


def _fruit_tree(p: _Painter) -> None:
    w, h = p.w, p.h
    f = p.field()
    p.fill(f, "tapestry_wine")
    # A lozenge diaper a value darker.
    gx, gy = 0.3, 0.42
    lx = np.abs(((p.X / gx) % 1.0) - 0.5) * 2
    ly = np.abs(((p.Y / gy) % 1.0) - 0.5) * 2
    lines = np.clip((0.07 - np.abs(lx + ly - 1.0)) / (p.aa / gx) + 0.5, 0, 1)
    p.fill(f * lines, "tapestry_wine", 0.78)
    # The ground and the tree.
    p.fill(f * p.hill(0.6, 0.05, 1.3, 1.0), "tapestry_forest")
    cx = w / 2
    p.fill(f * p.rect(cx - 0.09, 0.5, cx + 0.09, 2.2), "tapestry_umber")
    for side in (-1, 1):
        for k, y in enumerate((1.5, 2.0, 2.5)):
            x1 = cx + side * (0.55 - 0.1 * k)
            p.fill(f * p.tri(cx, y - 0.06, cx, y + 0.06, x1, y + 0.35), "tapestry_umber")
    crown = np.zeros_like(p.X)
    for dx, dy, rr in ((0, 2.7, 0.62), (-0.48, 2.35, 0.42), (0.48, 2.35, 0.42), (-0.3, 3.05, 0.4), (0.3, 3.05, 0.4),
                       (0, 3.25, 0.38)):
        crown = np.maximum(crown, p.disc(cx + dx, dy, rr))
    p.fill(f * crown, "tapestry_leaf")
    p.fill(f * crown * p.hill(2.45, 0.08, 0.5, 0.0), "tapestry_leaf", 0.82)
    for _ in range(24):
        a = float(p.rng.uniform(0, 2 * np.pi))
        rr = float(np.sqrt(p.rng.random()) * 0.62)
        x, y = cx + rr * np.cos(a) * 1.15, 2.75 + rr * np.sin(a) * 0.85
        p.fill(f * p.disc(x, y, 0.055), "tapestry_ochre" if p.rng.random() < 0.6 else "tapestry_rose")
    # Small birds (chevrons) round the crown.
    for x, y in ((0.55, 3.45), (1.95, 3.3), (0.45, 1.75), (2.05, 1.9)):
        b = np.maximum(p.tri(x - 0.12, y + 0.05, x, y - 0.02, x - 0.02, y + 0.03),
                       p.tri(x + 0.12, y + 0.05, x, y - 0.02, x + 0.02, y + 0.03))
        p.fill(f * b, "tapestry_cream")
    for x in np.linspace(0.45, w - 0.45, 7):
        p.fill(f * p.star(float(x), 0.42, 0.05), "tapestry_cream")


def _lake_castle(p: _Painter) -> None:
    w, h = p.w, p.h
    f = p.field()
    p.fill(f, "tapestry_sky")
    p.fill(f * p.rect(0, 1.45, w, 1.9), "tapestry_cream", 0.95)
    p.fill(f * p.rect(0, 2.35, w, h), "tapestry_sky", 0.85)
    p.fill(f * p.disc(w * 0.8, 2.05, 0.26), "tapestry_ochre")
    # Hills behind, the castle's hill, the lake.
    p.fill(f * p.hill(1.75, 0.16, 2.6, 0.4), "tapestry_stone", 0.85)
    p.fill(f * p.hill(1.5, 0.12, 1.9, 2.0), "tapestry_leaf", 0.85)
    p.fill(f * p.ellipse(w * 0.36, 1.05, 1.5, 0.75), "tapestry_forest")
    castle = np.zeros_like(p.X)
    for x, y, tw, th, roof in ((1.5, 1.45, 0.26, 0.85, 0.34), (1.92, 1.55, 0.34, 0.72, 0.34), (2.36, 1.55, 0.24, 0.7, 0.3),
                               (2.72, 1.42, 0.22, 0.55, 0.26)):
        castle = np.maximum(castle, p.tower(x, y, tw, th, roof))
    castle = np.maximum(castle, p.rect(1.45, 1.45, 2.8, 2.05))
    p.fill(f * castle, "tapestry_stone")
    for x, y, tw, th, roof in ((1.5, 1.45, 0.26, 0.85, 0.34), (1.92, 1.55, 0.34, 0.72, 0.34), (2.36, 1.55, 0.24, 0.7, 0.3),
                               (2.72, 1.42, 0.22, 0.55, 0.26)):
        p.fill(f * p.tri(x - tw * 0.62, y + th, x + tw * 0.62, y + th, x, y + th + roof), "tapestry_navy")
        p.fill(f * p.rect(x - 0.035, y + th * 0.55, x + 0.035, y + th * 0.75), "tapestry_ochre")
    p.fill(f * p.rect(2.0, 1.45, 2.2, 1.7), "tapestry_umber")
    lake = p.rect(0, 0.35, w, 0.95) * (1 - p.ellipse(w * 0.36, 1.05, 1.5, 0.75))
    p.fill(f * lake, "tapestry_navy", 0.9)
    ripples = (np.abs(((p.Y / 0.11) % 1.0) - 0.5) < 0.08).astype(np.float32) * (np.sin(p.X * 3.1) > 0.2)
    p.fill(f * lake * ripples, "tapestry_sky", 0.9)
    # A boat with a sail, pines on the near shore.
    p.fill(f * p.rect(3.9, 0.55, 4.5, 0.64), "tapestry_umber")
    p.fill(f * p.tri(4.18, 0.66, 4.18, 1.05, 4.42, 0.66), "tapestry_cream")
    for x in (0.45, 0.75, 4.9, 5.2, 5.5):
        p.fill(f * p.pine(x, 0.3, 0.7, 0.36), "tapestry_forest", 0.7)
    for _ in range(30):
        x = float(p.rng.uniform(0.3, w - 0.3))
        p.fill(f * p.disc(x, float(p.rng.uniform(0.3, 0.38)), 0.025), "tapestry_rose")


DESIGNS = {
    "tapestry_0": ("tapestry_navy", _night_watch),
    "tapestry_1": ("tapestry_forest", _woodland),
    "tapestry_2": ("tapestry_wine", _fruit_tree),
    "tapestry_3": ("tapestry_sky", _lake_castle),
}


def colours(spec_path: str) -> dict:
    """The painter's palette: {name: sRGB rgb} for NAMES, from castle_textures.json."""
    pal = props_atlas.palette(spec_path)
    missing = [n for n in NAMES if n not in pal]
    if missing:
        raise KeyError(f"castle_textures.json palette lacks {missing}")
    return {n: paint.hex_rgb(pal[n]) for n in NAMES}


def paint_sheet(spec: dict, spec_path: str, seed: int = SEED) -> np.ndarray:
    """tapestries (1024 x 1024): every tile of `spec` (the sheet) painted, the
    padding sealed, the albedo fitted."""
    col = colours(spec_path)
    sh = trim.Sheet(spec, props_atlas.palette(spec_path)["tapestry_umber"])
    for i, (name, (ground, design)) in enumerate(sorted(DESIGNS.items())):
        if name not in spec["tiles"]:
            continue
        rows, cols, A, B = sh.tile(name)
        p = _Painter(A, B, TILE_M[name], col, seed + i)
        p.img[...] = col[ground][None, None, :]
        design(p)
        p.border(ground)
        sh.put(rows, cols, p.finish())
    return sh.done()
