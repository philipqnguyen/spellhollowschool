"""Vegetation look A (picked in review, 2026-11-02; docs/architecture.md
"Rendering", docs/art/style-guide.md section 12): every tree and
hedge the world builds is a planting of one species of a shared foliage kit.
Pure Python plus numpy, no `bpy`.

* The species (`SPECIES`): broadleaf trees (`broadleaf_broad`,
  `broadleaf_round`, `broadleaf_planter`), pines (`pine_a`, `pine_b`) and the
  hedge's pieces (`hedge_a`, `hedge_b`: a 1.5 m middle segment, open at both
  ends; `hedge_end`: the rounded end; `hedge_tall_*` the same 2.6 m tall).
  `species_geo(name, lod)` builds one at
  LOD0-LOD3: crowns of many overlapping painted leaf-cluster cards round a
  dozen and a half clumps (some face out, some stand as fins) over a dark
  core; pines of drooping needle-spray strips per whorl round a solid needle
  cone; trunks tapered and curved with root flare and forked limbs on a bark
  strip; a hedge one continuous rounded mass (a squircle section) covered in
  leaf cards. Normals are bent away from the crown's centre (a proxy
  ellipsoid, or the cone's axis, or the hedge's section), so the toon step
  draws two or three soft bands across a crown instead of facets; COLOR_0
  is a grey shade (inner and lower foliage falls into the shadow band).
  LOD1 and LOD2 are the same generator with fewer, larger cards; a tree's
  LOD3 is an impostor: three crossed quads showing a picture of its LOD0
  (`impostor`, rendered here by `rasterize`), a hedge's its core alone.
* The atlas (`paint_atlas`, `ATLAS_PX` 2048 x 1024): the painted tiles
  (`TILES`: leaf clusters, needle sprays, leaf tufts, needle and leaf
  patterns, bark, hedge leaves) and the five impostors, painted from palette
  colours only (`PALETTE`: castle_textures.json `palette` names), so a
  palette swap is an atlas rebuild. It is the castle library's
  `castle_foliage` (castle_kit/textures.py), alpha MASK at 0.5, double-sided
  with shared normals (both faces light the same; the client keeps Babylon
  from flipping the back face's normal).
* Plantings (`Planting`): a species and an affine transform (the basis
  columns and the translation, glTF axes). The pieces that used to model
  trees and hedges (`kit_tree_round`, `kit_tree_pine`, `kit_hedge_run`,
  `kit_planter_tree`, the garden gate's hedges) now carry
  `meta["foliage"]` (piece-local plantings: `tree`, `hedge_run`); the
  builders place them with the piece (`place`) and the exports list them per
  cell in the manifests (`manifest_rows`); the client draws them as thin
  instances of the kit's meshes, choosing each planting's LOD by its size
  on screen (client/src/game/castle/foliage.ts).

Coordinates are the game's (Y up, metres); UVs Blender's (v up)."""

from __future__ import annotations

import functools
import json
import math
import os
import random
from dataclasses import dataclass

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
TEXTURES_PATH = os.path.join(REPO, "data", "art", "castle_textures.json")

MATERIAL = "castle_foliage"  # the castle library material of the kit's meshes
KIT_FILE = "foliage_kit.glb"
NODE_PREFIX = "foliage."
LODS = (0, 1, 2, 3)

# -- palette ------------------------------------------------------------------------------------

# The atlas's colours: castle_textures.json `palette` names (the art data; no colour in code).
PALETTE_NAMES = ("plant", "leaf_light", "foliage_leaf_deep", "foliage_leaf_sun", "foliage_pine", "foliage_pine_light",
                 "foliage_pine_deep", "foliage_hedge", "foliage_hedge_light", "foliage_bark", "foliage_bark_dark",
                 "foliage_bark_light")


@functools.lru_cache(maxsize=4)
def palette(path: str = TEXTURES_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        pal = json.load(f)["palette"]
    out = {}
    for name in PALETTE_NAMES:
        h = pal[name].lstrip("#")
        out[name] = np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])
    return out


_PAL_PATH = [TEXTURES_PATH]


def rgb(name: str) -> np.ndarray:
    return palette(_PAL_PATH[0])[name]


# -- vectors ------------------------------------------------------------------------------------


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a):
    return math.sqrt(dot(a, a))


def norm(a):
    n = length(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 1.0, 0.0)


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def perp(n):
    a = (1.0, 0.0, 0.0) if abs(n[0]) < 0.8 else (0.0, 0.0, 1.0)
    return norm(cross(n, a))


def rot_axis(v, axis, ang):
    c, s = math.cos(ang), math.sin(ang)
    return add(add(mul(v, c), mul(cross(axis, v), s)), mul(axis, dot(axis, v) * (1 - c)))


def fib_sphere(n, rnd):
    out = []
    off = rnd.random() * 2 * math.pi
    for i in range(n):
        y = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(max(0.0, 1 - y * y))
        a = off + i * math.pi * (3 - math.sqrt(5))
        out.append((r * math.cos(a), y, r * math.sin(a)))
    return out


def rnd_unit(rnd):
    while True:
        v = (rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))
        if 0.01 < dot(v, v) <= 1:
            return norm(v)


# -- geometry container -------------------------------------------------------------------------


class Geo:
    """Triangles with per-vertex position, custom normal, uv, shade."""

    def __init__(self, name: str):
        self.name = name
        self.pos: list = []
        self.nrm: list = []
        self.uv: list = []
        self.shade: list = []
        self.faces: list = []

    def vert(self, p, n, uv, s) -> int:
        self.pos.append(tuple(float(c) for c in p))
        self.nrm.append(norm(n))
        self.uv.append((float(uv[0]), float(uv[1])))
        self.shade.append(round(min(1.0, max(0.3, s)), 4))
        return len(self.pos) - 1

    def tri(self, a, b, c):
        self.faces.append((a, b, c))

    def quad(self, a, b, c, d):
        self.faces.append((a, b, c))
        self.faces.append((a, c, d))

    @property
    def tris(self) -> int:
        return len(self.faces)

    def bounds(self):
        P = np.array(self.pos)
        return tuple(P.min(axis=0)), tuple(P.max(axis=0))

    def drop_below(self, y_min: float, tiles) -> None:
        """Drops the faces whose UVs lie in `tiles` and that reach below y_min (leaves under the
        crown's clearance)."""
        rects = [TILES[t] for t in tiles]

        def in_tiles(i):
            u, v = self.uv[i]
            return any(r[0] <= u <= r[2] and r[1] <= v <= r[3] for r in rects)

        self.faces = [f for f in self.faces if not (all(in_tiles(i) for i in f) and min(self.pos[i][1] for i in f) < y_min)]


# -- atlas layout (Blender UV space, v up) ------------------------------------------------------

ATLAS_PX = (2048, 1024)  # width, height
_W, _H = ATLAS_PX


def _rect(x0, y0, x1, y1):
    return (x0 / _W, y0 / _H, x1 / _W, y1 / _H)


TILES = {
    "solid": _rect(0, 0, 512, 512),  # opaque broadleaf pattern (crown cores)
    "bark": _rect(512, 0, 640, 512),  # bark, periodic in u
    "hedge": _rect(640, 0, 1024, 512),  # opaque small-leaf pattern (hedge mass)
    "clump": _rect(0, 512, 512, 1024),  # broadleaf leaf cluster (alpha)
    "tuft": _rect(512, 512, 768, 768),  # leaf tuft growing up from the bottom edge (alpha)
    "needle": _rect(768, 512, 1024, 768),  # opaque needle pattern (pine cores)
    "spray": _rect(512, 768, 1024, 1024),  # pine needle spray, twig along +u (alpha)
    # The impostors: pictures of the trees' LOD0, side on (their LOD3).
    "imp_broadleaf_broad": _rect(1024, 0, 1536, 512),
    "imp_broadleaf_round": _rect(1536, 0, 2048, 512),
    "imp_broadleaf_planter": _rect(1024, 512, 1536, 1024),
    "imp_pine_a": _rect(1536, 512, 1792, 1024),
    "imp_pine_b": _rect(1792, 512, 2048, 1024),
}
INSET_PX = 3.0  # keeps mips from bleeding across tiles
OPAQUE_TILES = ("solid", "bark", "hedge", "needle")


def tile_uv(tile: str, u: float, v: float):
    u0, v0, u1, v1 = TILES[tile]
    iu, iv = INSET_PX / _W, INSET_PX / _H
    u = min(1.0, max(0.0, u))
    v = min(1.0, max(0.0, v))
    return (u0 + iu + (u1 - u0 - 2 * iu) * u, v0 + iv + (v1 - v0 - 2 * iv) * v)


def in_tile(uv, tile: str) -> bool:
    u0, v0, u1, v1 = TILES[tile]
    return u0 <= uv[0] <= u1 and v0 <= uv[1] <= v1


def tile_px(tile: str):
    u0, v0, u1, v1 = TILES[tile]
    return int(round(u0 * _W)), int(round(v0 * _H)), int(round((u1 - u0) * _W)), int(round((v1 - v0) * _H))


# -- painting -----------------------------------------------------------------------------------


class Canvas:
    """A tile painted at `ss` x supersampling: colour and coverage (row 0 at the bottom)."""

    def __init__(self, w: int, h: int, ss: int = 2):
        self.w, self.h, self.ss = w, h, ss
        self.rgb = np.zeros((h * ss, w * ss, 3))
        self.a = np.zeros((h * ss, w * ss))

    def leaf(self, cx, cy, ang, ln, wd, col, light=1.12, edge=0.8, tip=0.75):
        """An almond leaf from (cx, cy) (tile pixels) pointing `ang`, `ln` long, `wd` wide: two
        tones split at the midrib (anime leaf), a thin darker edge."""
        ss = self.ss
        cx, cy, ln, wd = cx * ss, cy * ss, ln * ss, wd * ss
        r = ln + 2
        x0, x1 = int(max(0, cx - r)), int(min(self.w * ss, cx + r + 1))
        y0, y1 = int(max(0, cy - r)), int(min(self.h * ss, cy + r + 1))
        if x0 >= x1 or y0 >= y1:
            return
        X, Y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        dx, dy = X - cx, Y - cy
        ca, sa = math.cos(ang), math.sin(ang)
        s = (dx * ca + dy * sa) / ln
        t = (-dx * sa + dy * ca) / ln
        sc = np.clip(s, 0, 1)
        hw = (wd / ln) * 0.5 * np.sin(np.pi * sc) ** tip * (1 - 0.25 * sc)
        inside = (s >= 0) & (s <= 1) & (np.abs(t) < hw)
        if not inside.any():
            return
        col = np.asarray(col, float)
        c = np.where((t > 0)[..., None], np.clip(col * light, 0, 1), col)
        rim = inside & (np.abs(t) > hw - 1.1 * ss / ln)
        c = np.where(rim[..., None], col * edge, c)
        self.rgb[y0:y1, x0:x1][inside] = c[inside]
        self.a[y0:y1, x0:x1][inside] = 1.0

    def stroke(self, x0, y0, x1, y1, w, col):
        ss = self.ss
        x0, y0, x1, y1, w = x0 * ss, y0 * ss, x1 * ss, y1 * ss, w * ss
        bx0, bx1 = int(max(0, min(x0, x1) - w - 1)), int(min(self.w * ss, max(x0, x1) + w + 2))
        by0, by1 = int(max(0, min(y0, y1) - w - 1)), int(min(self.h * ss, max(y0, y1) + w + 2))
        if bx0 >= bx1 or by0 >= by1:
            return
        X, Y = np.meshgrid(np.arange(bx0, bx1) + 0.5, np.arange(by0, by1) + 0.5)
        dx, dy = x1 - x0, y1 - y0
        l2 = dx * dx + dy * dy or 1.0
        t = np.clip(((X - x0) * dx + (Y - y0) * dy) / l2, 0, 1)
        d = np.hypot(X - (x0 + t * dx), Y - (y0 + t * dy))
        inside = d < w * 0.5 * (1 - 0.6 * t)
        self.rgb[by0:by1, bx0:bx1][inside] = np.asarray(col, float)
        self.a[by0:by1, bx0:bx1][inside] = 1.0

    def result(self, opaque=False):
        """(h, w, 4) floats, downsampled; transparent texels take their neighbours' colour."""
        ss, h, w = self.ss, self.h, self.w
        a = self.a.reshape(h, ss, w, ss).mean(axis=(1, 3))
        pm = (self.rgb * self.a[..., None]).reshape(h, ss, w, ss, 3).mean(axis=(1, 3))
        col = np.where(a[..., None] > 1e-6, pm / np.maximum(a[..., None], 1e-6), 0.0)
        if opaque:
            fill = col[a > 0.5].min(axis=0) if (a > 0.5).any() else np.zeros(3)
            col = col * a[..., None] + fill * (1 - a[..., None])
            return np.concatenate([col, np.ones((h, w, 1))], axis=2)
        known = a > 0.02
        c = col.copy()
        for _ in range(24):
            if known.all():
                break
            pad_c = np.pad(c * known[..., None], ((1, 1), (1, 1), (0, 0)))
            pad_k = np.pad(known.astype(float), 1)
            sc = sum(pad_c[1 + dy:1 + dy + h, 1 + dx:1 + dx + w] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
            sk = sum(pad_k[1 + dy:1 + dy + h, 1 + dx:1 + dx + w] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
            grow = (~known) & (sk > 0)
            c[grow] = sc[grow] / sk[grow][..., None]
            known = known | grow
        if not known.all():
            c[~known] = c[known].mean(axis=0) if known.any() else 0.0
        return np.concatenate([c, a[..., None]], axis=2)


def _mix(rnd, names, weights):
    x = rnd.random() * sum(weights)
    for n, w in zip(names, weights):
        x -= w
        if x <= 0:
            return rgb(n)
    return rgb(names[-1])


LEAF = ["foliage_leaf_deep", "plant", "leaf_light", "foliage_leaf_sun"]


def paint_clump(size: int, rnd: random.Random) -> np.ndarray:
    """A round cluster of small leaves with a jagged, leafy outline (alpha)."""
    cv = Canvas(size, size)
    c = size / 2
    leaves = []
    for _ in range(170):
        rr = c * 0.62 * math.sqrt(rnd.random())
        a = rnd.random() * 2 * math.pi
        ln = size * rnd.uniform(0.12, 0.17)
        leaves.append((rr, c + rr * math.cos(a), c + rr * math.sin(a), a + rnd.uniform(-0.6, 0.6), ln,
                       ln * rnd.uniform(0.42, 0.55)))
    leaves.sort(key=lambda q: -q[0])  # outer leaves behind, the centre's in front
    for rr, x, y, ang, ln, wd in leaves:
        outer = rr / (c * 0.62)
        col = _mix(rnd, LEAF, [0.15 + 0.25 * outer, 0.4, 0.33, 0.14 * (1 - outer) + 0.05])
        cv.leaf(x, y, ang, ln, wd, col)
    return cv.result()


def paint_tuft(size: int, rnd: random.Random) -> np.ndarray:
    """Leaves fanning up from the bottom centre (fins sticking out of a mass)."""
    cv = Canvas(size, size)
    for _ in range(46):
        bx = size * (0.5 + rnd.uniform(-0.18, 0.18))
        by = size * rnd.uniform(0.0, 0.38)
        ang = math.pi / 2 + rnd.uniform(-0.95, 0.95)
        ln = size * rnd.uniform(0.26, 0.42)
        cv.leaf(bx, by, ang, ln, ln * rnd.uniform(0.4, 0.52), _mix(rnd, LEAF, [0.2, 0.4, 0.3, 0.1]))
    return cv.result()


PINE = ["foliage_pine_deep", "foliage_pine", "foliage_pine_light"]


def paint_spray(w: int, h: int, rnd: random.Random) -> np.ndarray:
    """A conifer branch spray: a twig along +u, needle tufts angled forward, a ragged edge
    tapering to the tip (alpha). Fuller than a fishbone: the side twigs carry their own needles."""
    cv = Canvas(w, h)
    cy = h / 2
    twig = rgb("foliage_bark_dark")
    cv.stroke(0, cy, w * 0.97, cy + rnd.uniform(-4, 4), 7, twig)
    sides = []
    for k in range(10):
        t = 0.06 + 0.09 * k
        x = w * t
        for sgn in (-1, 1):
            ln = h * 0.44 * (1 - 0.6 * t) * rnd.uniform(0.8, 1.1)
            a = sgn * rnd.uniform(0.55, 0.85)
            sides.append((x, cy, x + ln * math.cos(a) * 1.4, cy + ln * math.sin(a)))
    for x0, y0, x1, y1 in sides:
        cv.stroke(x0, y0, x1, y1, 4, twig)

    def needles(x0, y0, x1, y1, n, ln):
        base = math.atan2(y1 - y0, x1 - x0)
        for i in range(n):
            t = (i + rnd.random()) / n
            px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            for sgn in (-1, 1):
                a = base + sgn * rnd.uniform(0.5, 1.1)
                L = ln * rnd.uniform(0.7, 1.2) * (1 - 0.45 * t)
                col = _mix(rnd, PINE, [0.3 if sgn > 0 else 0.5, 0.45, 0.35 if sgn > 0 else 0.15])
                cv.stroke(px, py, px + L * math.cos(a), py + L * math.sin(a), 3.4, col)

    needles(0, cy, w * 0.97, cy, 80, h * 0.2)
    for x0, y0, x1, y1 in sides:
        needles(x0, y0, x1, y1, 14, h * 0.14)
    return cv.result()


def paint_pattern(w: int, h: int, rnd: random.Random, names, weights, leaf_px, n) -> np.ndarray:
    """An opaque, dense leaf pattern: calm, low contrast, so the toon bands carry the form."""
    cv = Canvas(w, h)
    for _ in range(n):
        x, y = rnd.uniform(-10, w + 10), rnd.uniform(-10, h + 10)
        ln = leaf_px * rnd.uniform(0.8, 1.25)
        cv.leaf(x, y, rnd.random() * 2 * math.pi, ln, ln * rnd.uniform(0.45, 0.6), _mix(rnd, names, weights),
                light=1.07, edge=0.88)
    return cv.result(opaque=True)


def paint_needles(w: int, h: int, rnd: random.Random) -> np.ndarray:
    cv = Canvas(w, h)
    cv.rgb[:] = rgb("foliage_pine")
    cv.a[:] = 1
    for _ in range(1400):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        a = -math.pi / 2 + rnd.uniform(-0.7, 0.7)
        L = rnd.uniform(10, 18)
        cv.stroke(x, y, x + L * math.cos(a), y + L * math.sin(a), 3.4, _mix(rnd, PINE, [0.3, 0.45, 0.25]))
    return cv.result(opaque=True)


def paint_bark(w: int, h: int, rnd: random.Random) -> np.ndarray:
    """Vertical bark plates in three flat tones, periodic in u."""
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    U, V = np.meshgrid(u, v)
    f = np.zeros_like(U)
    for k in range(1, 7):
        ph = rnd.random() * 2 * math.pi
        f += np.sin(2 * math.pi * k * U + ph + 1.3 * np.sin(2 * math.pi * V * rnd.uniform(1, 3) + ph)) / k
    f += 0.35 * np.sin(2 * math.pi * 9 * U + 6 * V)
    q = np.digitize(f, [-0.5, 0.55])
    tones = np.stack([rgb("foliage_bark_dark"), rgb("foliage_bark"), rgb("foliage_bark_light")])
    return np.concatenate([tones[q], np.ones((h, w, 1))], axis=2)


ATLAS_SEED = 7


def paint_tiles() -> np.ndarray:
    """The atlas's painted tiles (the impostors' area left empty): (H, W, 4), row 0 at the bottom."""
    rnd = random.Random(ATLAS_SEED * 31 + ord("A"))
    img = np.zeros((_H, _W, 4))
    img[..., :3] = rgb("plant")

    def put(tile, arr):
        x0, y0, w, h = tile_px(tile)
        img[y0:y0 + h, x0:x0 + w] = arr

    put("clump", paint_clump(512, rnd))
    put("spray", paint_spray(512, 256, rnd))
    put("tuft", paint_tuft(256, rnd))
    put("needle", paint_needles(256, 256, rnd))
    put("solid", paint_pattern(512, 512, rnd, ["foliage_leaf_deep", "plant", "leaf_light"], [0.15, 0.55, 0.3], 64, 900))
    put("bark", paint_bark(128, 512, rnd))
    put("hedge", paint_pattern(384, 512, rnd, ["foliage_hedge", "foliage_hedge_light", "foliage_leaf_deep"],
                               [0.6, 0.25, 0.15], 30, 2200))
    return img


@functools.lru_cache(maxsize=2)
def _atlas(pal_path: str) -> np.ndarray:
    _PAL_PATH[0] = pal_path
    img = paint_tiles()
    for name in SPECIES:
        if SPECIES[name].kind in ("broadleaf", "pine"):
            tile = f"imp_{name}"
            x0, y0, w, h = tile_px(tile)
            img[y0:y0 + h, x0:x0 + w] = rasterize(species_geo(name, 0), img, impostor_frame(name), w, h)
    return img


def paint_atlas(spec_path: str = TEXTURES_PATH) -> np.ndarray:
    """The `castle_foliage` atlas (sRGB RGBA floats, (H, W, 4)) as textures.py writes it: row 0
    of the array is the PNG's first row, and the library's textures are sampled with glTF's v
    (the exported 1 - Blender v) from the image's last row up, so the Blender-UV layout (row 0
    at v = 0) is written as it is."""
    return _atlas(spec_path).copy()


# -- shared pieces ------------------------------------------------------------------------------


def tube(g: Geo, pts, radii, sides, shade_fn, v_ref=8.0, flare=None, cap=False):
    """A tube along `pts` on the bark strip; `flare(i, a)` scales a ring's radius at angle a."""
    n = len(pts)
    T = [norm(sub(pts[min(i + 1, n - 1)], pts[max(i - 1, 0)])) for i in range(n)]
    N = perp(T[0])
    frames = []
    for i in range(n):
        N = norm(sub(N, mul(T[i], dot(N, T[i]))))
        frames.append((N, cross(T[i], N)))
    rings = []
    s = 0.0
    for i in range(n):
        if i:
            s += length(sub(pts[i], pts[i - 1]))
        Ni, Bi = frames[i]
        ring = []
        for k in range(sides + 1):
            a = 2 * math.pi * k / sides
            d = add(mul(Ni, math.cos(a)), mul(Bi, math.sin(a)))
            r = radii[i] * (flare(i, a) if flare else 1.0)
            p = add(pts[i], mul(d, r))
            ring.append(g.vert(p, d, tile_uv("bark", k / sides, s / v_ref), shade_fn(p)))
        rings.append(ring)
    for i in range(n - 1):
        for k in range(sides):
            g.quad(rings[i][k], rings[i + 1][k], rings[i + 1][k + 1], rings[i][k + 1])
    if cap:
        tip = g.vert(pts[-1], T[-1], tile_uv("bark", 0.5, s / v_ref), shade_fn(pts[-1]))
        for k in range(sides):
            g.tri(rings[-1][k], rings[-1][k + 1], tip)


def card(g: Geo, centre, normal, up_hint, w, h, tile, nfn, sfn, spin=0.0, flip=False, anchor=0.5):
    """A quad card in `tile`: plane facing `normal`, its up turned `spin` from `up_hint`; `anchor`
    is where the centre sits along its height. Normals and shade per corner."""
    n = norm(normal)
    up = sub(up_hint, mul(n, dot(up_hint, n)))
    up = norm(up) if length(up) > 1e-4 else perp(n)
    up = rot_axis(up, n, spin)
    right = cross(up, n)
    corners = []
    for cu, cv in ((0, 0), (1, 0), (1, 1), (0, 1)):
        p = add(add(centre, mul(right, (cu - 0.5) * w)), mul(up, (cv - anchor) * h))
        corners.append(g.vert(p, nfn(p), tile_uv(tile, 1 - cu if flip else cu, cv), sfn(p)))
    g.quad(*corners)


def icosphere(sub_n: int):
    t = (1 + math.sqrt(5)) / 2
    vs = [norm(v) for v in [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t),
                             (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]]
    fs = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
          (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
          (8, 6, 7), (9, 8, 1)]
    for _ in range(sub_n):
        cache = {}

        def mid(a, b):
            k = (min(a, b), max(a, b))
            if k not in cache:
                vs.append(norm(mul(add(vs[a], vs[b]), 0.5)))
                cache[k] = len(vs) - 1
            return cache[k]

        nf = []
        for a, b, c in fs:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        fs = nf
    return vs, fs


def solid_blob(g: Geo, centre, radii, sub_n, tile, nfn, sfn, uv_scale=0.45):
    """A closed blob on an opaque `tile` by a per-face cube projection, custom normals."""
    vs, fs = icosphere(sub_n)
    P = [(centre[0] + d[0] * radii[0], centre[1] + d[1] * radii[1], centre[2] + d[2] * radii[2]) for d in vs]
    for a, b, c in fs:
        pts = (P[a], P[b], P[c])
        fn = norm(cross(sub(pts[1], pts[0]), sub(pts[2], pts[0])))
        ax = max(range(3), key=lambda i: abs(fn[i]))
        i0, i1 = [(1, 2), (0, 2), (0, 1)][ax]
        us = [p[i0] * uv_scale for p in pts]
        ws = [p[i1] * uv_scale for p in pts]
        bu = max(math.floor(min(us)), max(us) - 1.0)
        bv = max(math.floor(min(ws)), max(ws) - 1.0)
        g.tri(*[g.vert(p, nfn(p), tile_uv(tile, uu - bu, vv - bv), sfn(p)) for p, uu, vv in zip(pts, us, ws)])


def _strip(g, cen, up_hint, roll, width, tile, nfn, sfn):
    """A strip along the polyline `cen` (a drooping branch), `width` wide tapering to the tip,
    turned `roll` about its axis; u along the twig, v across."""
    n = len(cen)
    tot = sum(length(sub(cen[i + 1], cen[i])) for i in range(n - 1)) or 1.0
    acc = 0.0
    rows = []
    for i in range(n):
        if i:
            acc += length(sub(cen[i], cen[i - 1]))
        T = norm(sub(cen[min(i + 1, n - 1)], cen[max(i - 1, 0)]))
        side = rot_axis(norm(cross(T, up_hint)), T, roll)
        u = acc / tot
        w = width * (1 - 0.5 * u)
        rows.append((g.vert(add(cen[i], mul(side, -w / 2)), nfn(cen[i]), tile_uv(tile, u, 0.0), sfn(cen[i])),
                     g.vert(add(cen[i], mul(side, w / 2)), nfn(cen[i]), tile_uv(tile, u, 1.0), sfn(cen[i]))))
    for i in range(n - 1):
        g.quad(rows[i][0], rows[i + 1][0], rows[i + 1][1], rows[i][1])


# -- species ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Species:
    name: str
    kind: str  # broadleaf | pine | hedge | hedge_end
    height: float = 0.0  # the generator's nominal height (m)
    seed: int = 0
    crown_low: float = 0.0  # no leaf below this (m, at scale 1)
    depth: float = 0.0  # a hedge's depth (its `height` is the section's)


SPECIES = {s.name: s for s in (
    Species("broadleaf_broad", "broadleaf", 7.5, 2, 2.9),  # the grounds' tree_broad (and the field trees)
    Species("broadleaf_round", "broadleaf", 6.5, 1, 2.6),  # tree_round: a rounder, lower crown
    Species("broadleaf_planter", "broadleaf", 4.6, 5, 2.0),  # the courtyards' planter trees (from the soil up)
    Species("pine_a", "pine", 9.0, 1, 2.8),
    Species("pine_b", "pine", 9.0, 4, 2.8),
    # Hedges in two families, so a run's pieces are never stretched much (the leaf tufts would
    # turn to blades): low (the grounds' 1.3 m hedges) and tall (the paddock's 2.6 m).
    Species("hedge_a", "hedge", 1.4, 0, depth=0.85),
    Species("hedge_b", "hedge", 1.4, 1, depth=0.85),
    Species("hedge_end", "hedge_end", 1.4, 0, depth=0.85),
    Species("hedge_tall_a", "hedge", 2.6, 2, depth=1.1),
    Species("hedge_tall_b", "hedge", 2.6, 3, depth=1.1),
    Species("hedge_tall_end", "hedge_end", 2.6, 2, depth=1.1),
)}
HEDGE_FAMILIES = (("", 1.4, 0.85), ("tall_", 2.6, 1.1))  # name infix, section height, depth

HEDGE_SEG = 1.5  # a middle segment's length (m)
HEDGE_H = 1.4  # the low family's height and depth at scale 1
HEDGE_D = 0.85
HEDGE_CAP = 0.42  # the low family's rounded end's length at scale 1 (a family's: this times its depth / HEDGE_D)


def hedge_cap(name: str) -> float:
    """A hedge species' rounded end's length at scale 1 (m)."""
    return HEDGE_CAP * SPECIES[name].depth / HEDGE_D
# The hedge budget (vegetation look A replaces the paddock's 60 triangles a metre for kit hedges): a
# hedge's plantings at LOD0 per metre of run (a 1.5 m segment is about 335 triangles of leaf cards
# over its core; a short run's two rounded ends add more per metre).
HEDGE_TRIS_PER_M = 360


class Canopy:
    """The crown's proxy ellipsoid: normals and shade come from it."""

    def __init__(self, c, r):
        self.c, self.r = c, r

    def depth(self, p):
        d = sub(p, self.c)
        return math.sqrt((d[0] / self.r[0]) ** 2 + (d[1] / self.r[1]) ** 2 + (d[2] / self.r[2]) ** 2)

    def normal(self, p, local=None, k=0.5):
        d = sub(p, self.c)
        e = norm((d[0] / self.r[0] ** 2, d[1] / self.r[1] ** 2, d[2] / self.r[2] ** 2))
        if local is not None:
            e = norm(add(mul(e, 1 - k), mul(norm(sub(p, local)), k)))
        return norm(add(e, (0.0, 0.12, 0.0)))

    def shade(self, p):
        ao = smoothstep(0.2, 1.0, self.depth(p))
        under = smoothstep(-1.0, 0.3, (p[1] - self.c[1]) / self.r[1])
        return 0.62 + 0.38 * ao * (0.75 + 0.25 * under)


def _broadleaf_skeleton(sp: Species, lod: int):
    H = sp.height
    rnd = random.Random(sp.seed * 977 + 13)
    s = H / 7.5
    lean_a = rnd.uniform(0, 2 * math.pi)
    lean = (math.cos(lean_a), 0.0, math.sin(lean_a))
    side = (-lean[2], 0.0, lean[0])
    F = max(2.75 * s, sp.crown_low * 0.95)
    n_tr = 7 if lod < 2 else 4
    trunk = []
    for i in range(n_tr):
        t = i / (n_tr - 1)
        off = add(mul(lean, 0.38 * s * t ** 1.6), mul(side, 0.1 * s * math.sin(math.pi * t * 1.6)))
        trunk.append((off[0], -0.25 + (F + 0.25) * t, off[2]))
    branches = [(trunk, [lerp(0.30, 0.20, i / (n_tr - 1)) * max(s, 0.85) for i in range(n_tr)])]
    P = trunk[-1]
    anchors = []
    seg = 5 if lod < 2 else 3
    for k in range(3):
        az = lean_a + 0.4 + k * 2 * math.pi / 3 + rnd.uniform(-0.35, 0.35)
        el = math.radians(rnd.uniform(32, 48))
        L = 2.6 * s * rnd.uniform(0.88, 1.12)
        d = (math.cos(el) * math.cos(az), math.sin(el), math.cos(el) * math.sin(az))
        pts = [add(add(P, mul(d, L * t)), (0.0, 0.45 * s * t * t, 0.0)) for t in [i / seg for i in range(seg + 1)]]
        branches.append((pts, [lerp(0.16, 0.07, i / seg) * s for i in range(seg + 1)]))
        anchors.append((pts[-1], 1.0))
        if lod < 3:
            base = pts[seg // 2 + 1]
            for sg in (-1, 1):
                a2 = az + sg * rnd.uniform(0.45, 0.75)
                e2 = el + math.radians(rnd.uniform(5, 20))
                d2 = (math.cos(e2) * math.cos(a2), math.sin(e2), math.cos(e2) * math.sin(a2))
                L2 = 1.25 * s * rnd.uniform(0.85, 1.15)
                n2 = 3 if lod < 2 else 2
                p2 = [add(add(base, mul(d2, L2 * t)), (0.0, 0.2 * s * t * t, 0.0)) for t in [i / n2 for i in range(n2 + 1)]]
                branches.append((p2, [lerp(0.075, 0.035, i / n2) * s for i in range(n2 + 1)]))
                anchors.append((p2[-1], 0.85))
    top = add(P, add(mul(lean, 0.3 * s), (0.0, (H - F) * 0.62, 0.0)))
    branches.append(([add(P, mul(sub(top, P), i / seg)) for i in range(seg + 1)],
                     [lerp(0.15, 0.05, i / seg) * s for i in range(seg + 1)]))
    anchors.append((top, 1.1))
    crown_low = sp.crown_low
    xs = [a[0][0] for a in anchors]
    zs = [a[0][2] for a in anchors]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    rxz = max(math.hypot(a[0][0] - cx, a[0][2] - cz) for a in anchors) + 1.05 * s
    canopy = Canopy((cx, (H + crown_low) / 2 + 0.2 * s, cz), (rxz, (H - crown_low) / 2, rxz))
    return branches, anchors, canopy, s, rnd


def _trunk_geo(g: Geo, branches, canopy, s, sides):
    def shade(p):
        base = 0.78 + 0.22 * smoothstep(-0.2, 2.2 * s, p[1])
        inside = canopy.depth(p)
        return base * (0.78 + 0.22 * smoothstep(0.3, 1.0, inside)) if inside < 1.0 else base

    def flare(i, a):
        if i > 1:
            return 1.0
        lobes = 1.0 + 0.32 * max(0.0, math.cos(5 * a + 0.7)) ** 2
        return (1.55 if i == 0 else 1.12) * (lobes if i == 0 else 1 + 0.4 * (lobes - 1))

    for k, (pts, rad) in enumerate(branches):
        tube(g, pts, rad, sides if k == 0 else max(4, sides - 2), shade, flare=flare if k == 0 else None, cap=k > 0)


def _broadleaf(sp: Species, lod: int) -> Geo:
    g = Geo(sp.name)
    branches, anchors, cp, s, rnd = _broadleaf_skeleton(sp, lod)
    _trunk_geo(g, branches, cp, s, [10, 8, 6, 5][lod])
    # A dark inner core: the crown never shows sky through its middle.
    solid_blob(g, cp.c, (cp.r[0] * 0.6, cp.r[1] * 0.6, cp.r[2] * 0.6), 1, "solid", lambda p: cp.normal(p),
               lambda p: cp.shade(p) * 0.8)
    fill = [10, 9, 7, 6][lod]
    cards = [20, 11, 7, 4][lod]
    scale = [1.18, 1.5, 1.85, 2.3][lod]
    clumps = [(p, 1.05 * s * w) for p, w in anchors]
    for d in fib_sphere(fill, rnd):
        if d[1] < -0.45:
            continue
        clumps.append(((cp.c[0] + d[0] * cp.r[0] * 0.62, cp.c[1] + d[1] * cp.r[1] * 0.62, cp.c[2] + d[2] * cp.r[2] * 0.62),
                       1.15 * s * rnd.uniform(0.9, 1.1)))
    for c, rc in clumps:
        for d in fib_sphere(cards, rnd):
            if d[1] < -0.55 and rnd.random() < 0.6:
                continue
            q = add(c, mul(d, rc * rnd.uniform(0.45, 0.75)))
            if cp.depth(q) < 0.5 and rnd.random() < 0.65:
                continue
            if rnd.random() < 0.33:
                nrm = rot_axis(perp(d), d, rnd.random() * math.pi)
            else:
                nrm = norm(add(d, mul(rnd_unit(rnd), 0.55)))
            sz = rc * scale * rnd.uniform(0.85, 1.15)
            card(g, q, nrm, (0.0, 1.0, 0.0), sz, sz, "clump", lambda p, c=c: cp.normal(p, c, 0.5), cp.shade,
                 spin=rnd.uniform(-math.pi, math.pi), flip=rnd.random() < 0.5)
    g.drop_below(sp.crown_low, ("clump", "solid"))
    return g


class Cone:
    def __init__(self, top, low):
        self.top, self.low = top, low

    def normal(self, p, up=0.55):
        return norm(add(norm((p[0], 0.0, p[2])), (0.0, up, 0.0)))

    def shade(self, p, rmax):
        out = smoothstep(0.0, rmax, math.hypot(p[0], p[2]))
        h = smoothstep(self.low - 0.5, self.top, p[1])
        return 0.62 + 0.38 * (0.35 + 0.65 * out) * (0.8 + 0.2 * h)


def _pine(sp: Species, lod: int) -> Geo:
    g = Geo(sp.name)
    H = sp.height
    rnd = random.Random(sp.seed * 131 + 7)
    s = H / 9.0
    n_tr = [8, 6, 4, 3][lod]
    trunk = [(0.0, -0.25 + (H * 0.93 + 0.25) * i / (n_tr - 1), 0.0) for i in range(n_tr)]
    rad = [lerp(0.25, 0.03, (i / (n_tr - 1)) ** 0.8) * s for i in range(n_tr)]
    cone = Cone(H, sp.crown_low)
    rmax = 2.35 * s

    def tshade(p):
        return 0.78 + 0.22 * smoothstep(-0.2, 2.0 * s, p[1]) * (0.85 if p[1] > sp.crown_low else 1.0)

    def flare(i, a):
        return (1.45 * (1 + 0.25 * max(0.0, math.cos(4 * a)) ** 2)) if i == 0 else 1.0

    tube(g, trunk, rad, [8, 6, 5, 4][lod], tshade, flare=flare)
    nfn = cone.normal

    def sfn(p):
        return cone.shade(p, rmax)

    tiers = [11, 9, 7, 4][lod]
    phase = rnd.uniform(0, 6.3)
    r0 = 2.2 * s + 0.5 * s
    droop0 = 0.47 * r0
    # The lowest whorl: its drooping tips and the crossed strips' lower edges stay over the clearance.
    low = sp.crown_low + 0.58 * r0 * 1.08 + 0.5 * s
    top_y = H * 0.96 - 0.7 * s
    # The solid needle cone inside the whorls: no sky or trunk through the middle at any LOD.
    sides = [9, 8, 6, 5][lod]
    ring = []
    for k in range(sides + 1):
        a = 2 * math.pi * k / sides
        ring.append((math.cos(a), math.sin(a), k / sides))
    prof = [(0.42 * r0, low - 0.25 * droop0), (0.3 * r0, lerp(low, top_y, 0.45)), (0.12 * r0, lerp(low, top_y, 0.85)),
            (0.0, H * 0.98)]
    rows = []
    for j, (rr, y) in enumerate(prof):
        rows.append([g.vert((cx * rr, y, cz * rr), cone.normal((cx, 0.0, cz), 0.45), tile_uv("needle", u, j / 3),
                            0.62 + 0.25 * j / 3) for cx, cz, u in ring])
    for j in range(len(prof) - 1):
        for k in range(sides):
            g.quad(rows[j][k], rows[j + 1][k], rows[j + 1][k + 1], rows[j][k + 1])
    bottom = g.vert((0.0, prof[0][1] - 0.1, 0.0), (0.0, -1.0, 0.0), tile_uv("needle", 0.5, 0.5), 0.5)
    for k in range(sides):
        g.tri(rows[0][k + 1], rows[0][k], bottom)
    for i in range(tiers):
        t = i / (tiers - 1)
        y = low + (top_y - low) * t ** 0.95
        r = (2.2 * s * (1 - t) ** 0.92 + 0.5 * s) * rnd.uniform(0.92, 1.08)
        droop = (0.42 + 0.1 * rnd.random()) * r
        nb = max(4, round([12, 10, 8, 5][lod] - 5 * t))
        for k in range(nb):
            az = phase + i * 0.9 + k * 2 * math.pi / nb + rnd.uniform(-0.2, 0.2)
            L = r * rnd.uniform(0.85, 1.12)
            dh = (math.cos(az), 0.0, math.sin(az))
            seg = [3, 2, 2, 1][lod]
            cen = [(dh[0] * (0.05 + L * u), y + 0.1 * s * u - droop * u ** 1.6, dh[2] * (0.05 + L * u))
                   for u in [j / seg for j in range(seg + 1)]]
            w = L * 0.8 * (1 + 0.18 * lod)
            for roll in (0.75, -0.75):  # crossed: a spray never goes edge-on
                _strip(g, cen, (0.0, 1.0, 0.0), roll + rnd.uniform(-0.15, 0.15), w, "spray", nfn, sfn)
            if lod == 0:
                _strip(g, cen, (0.0, 1.0, 0.0), rnd.uniform(-0.15, 0.15), w * 1.1, "spray", nfn, sfn)
    for k in range(4 if lod < 2 else 2):
        a = phase + k * 2 * math.pi / (4 if lod < 2 else 2)
        d = norm((0.32 * math.cos(a), 1.0, 0.32 * math.sin(a)))
        cen = [add((0.0, top_y - 0.35 * s, 0.0), mul(d, (H - top_y + 0.35 * s) * u)) for u in (0.0, 0.5, 1.0)]
        _strip(g, cen, (math.cos(a), 0.0, math.sin(a)), rnd.uniform(-0.3, 0.3), 1.0 * s, "spray",
               lambda p: cone.normal(p, 1.0), lambda p: 0.95)
    return g


def _hedge(sp: Species, lod: int) -> Geo:
    """A hedge middle segment (x 0 to HEDGE_SEG, open ends, the same section at both ends so
    segments join without a seam) or the rounded end (x -HEDGE_CAP to 0, open at 0). Centred on
    z = 0, its foot 0.3 m under y = 0 so it meets sloping ground."""
    g = Geo(sp.name)
    rnd = random.Random(sp.seed * 71 + 5 + (100 if sp.kind == "hedge_end" else 0))
    ex = 3.0
    D, Hh = sp.depth, sp.height
    CAP = hedge_cap(sp.name)
    a0, b0 = D / 2 - 0.12, Hh / 2 - 0.07
    yc = Hh / 2 - 0.03
    nphi = [22, 14, 9, 6][lod]
    sink = 0.3

    def section(phi, a, b):
        c, s_ = math.cos(phi), math.sin(phi)
        z = a * math.copysign(abs(c) ** (2 / ex), c)
        y = b * math.copysign(abs(s_) ** (2 / ex), s_)
        nz = math.copysign(abs(z / a) ** (ex - 1), z) / a
        ny = math.copysign(abs(y / b) ** (ex - 1), y) / b
        return z, y, norm((0.0, ny, nz))

    # The section from the front foot over the top to the back foot (no floor).
    phis = [-math.pi / 2 + 0.55 + (2 * math.pi - 1.1) * j / nphi for j in range(nphi + 1)]
    ph = [rnd.uniform(0, 6.3) for _ in range(2)]

    def bulge(x):  # zero at a segment's ends: neighbours meet exactly
        return 0.04 * math.sin(math.pi * x / HEDGE_SEG) * math.sin(x * 2.3 + ph[0]) if sp.kind == "hedge" else 0.0

    if sp.kind == "hedge":
        xs = [HEDGE_SEG * i / [5, 3, 2, 1][lod] for i in range([5, 3, 2, 1][lod] + 1)]
        scale = [1.0] * len(xs)
    else:
        es = [0.0, 0.45, 0.8, 1.0] if lod < 2 else [0.0, 0.7, 1.0]
        xs = [-CAP * e for e in es]
        scale = [max(0.08, math.sqrt(max(0.0, 1 - e * e))) for e in es]
    rings = []
    for x, e in zip(xs, scale):
        a, b = (a0 + bulge(x)) * e, b0 + bulge(x + 3) * 1.3
        ring = []
        for j, phi in enumerate(phis):
            z, y, n = section(phi, a, b * (0.55 + 0.45 * e))
            p = (x, yc + y, z)
            if sp.kind == "hedge_end" and e < 1.0:
                t = 1 - e
                n = norm(add(mul(n, 1 - t * 0.85), (-t, 0.0, 0.0)))
            n = norm(add(n, (0.0, 0.15, 0.0)))
            ring.append((p, n, 0.68 + 0.32 * smoothstep(-0.1, Hh * 0.95, p[1])))
        # The feet: straight down from the section's ends.
        f0, f1 = ring[0], ring[-1]
        ring.insert(0, ((x, -sink, f0[0][2]), norm((0.0, 0.0, f0[0][2])), 0.62))
        ring.append(((x, -sink, f1[0][2]), norm((0.0, 0.0, f1[0][2])), 0.62))
        rings.append(ring)
    tu, tv = 1.6 * 0.75, 1.6
    arcs = []
    for ring in rings:
        acc, row = 0.0, [0.0]
        for j in range(1, len(ring)):
            acc += length(sub(ring[j][0], ring[j - 1][0]))
            row.append(acc)
        arcs.append(row)
    m = len(rings[0])
    for i in range(len(rings) - 1):
        for j in range(m - 1):
            corners = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            us = [arcs[a][b] / tu for a, b in corners]
            vs = [rings[a][b][0][0] / tv for a, b in corners]
            bu = max(math.floor(min(us)), max(us) - 1.0)
            bv = max(math.floor(min(vs)), max(vs) - 1.0)
            g.quad(*[g.vert(rings[a][b][0], rings[a][b][1], tile_uv("hedge", uu - bu, vv - bv), rings[a][b][2])
                     for (a, b), uu, vv in zip(corners, us, vs)])
    if sp.kind == "hedge_end":
        tip = g.vert((xs[-1] - 0.01, yc, 0.0), (-1.0, 0.25, 0.0), tile_uv("hedge", 0.5, 0.5), 0.8)
        last = rings[-1]
        for j in range(m - 1):
            g.tri(g.vert(last[j][0], last[j][1], tile_uv("hedge", 0.3, 0.3), last[j][2]),
                  g.vert(last[j + 1][0], last[j + 1][1], tile_uv("hedge", 0.4, 0.3), last[j + 1][2]), tip)

    def surf(x, phi):
        e = 1.0
        if sp.kind == "hedge_end":
            e = math.sqrt(max(0.0, 1 - (x / CAP) ** 2))
        z, y, n = section(phi, D / 2 * e, Hh / 2 * (0.55 + 0.45 * e))
        if sp.kind == "hedge_end":
            n = norm(add(mul(n, e), (-(1 - e), 0.0, 0.0)))
        return (x, yc + y, z), norm(add(n, (0.0, 0.15, 0.0)))

    def sfn(p):
        return 0.68 + 0.32 * smoothstep(-0.1, Hh * 0.95, p[1])

    spacing = [0.3, 0.42, 0.62, 0.0][lod]
    if spacing:
        x_lo, x_hi = (0.0, HEDGE_SEG) if sp.kind == "hedge" else (-CAP * 0.95, -0.02)
        arc = 2 * (Hh + D) * 0.9
        nu, nv = max(1, round((x_hi - x_lo) / spacing)), round(arc / spacing)
        for i in range(nu):
            for j in range(nv):
                x = x_lo + (i + rnd.uniform(0.1, 0.9)) / nu * (x_hi - x_lo)
                phi = -math.pi / 2 + 0.3 + (j + rnd.random()) / nv * (2 * math.pi - 0.6)
                p, n = surf(x, phi)
                if p[1] < 0.35:
                    continue
                if rnd.random() < 0.3:
                    nrm, q, anchor, tile, up = rot_axis(perp(n), n, rnd.random() * math.pi), add(p, mul(n, 0.06)), 0.35, "tuft", n
                else:
                    nrm, q, anchor, tile, up = norm(add(n, mul(rnd_unit(rnd), 0.4))), add(p, mul(n, -0.04)), 0.5, "clump", (0.0, 1.0, 0.0)
                sz = spacing * 1.75 * rnd.uniform(0.85, 1.15)
                card(g, q, nrm, up, sz, sz, tile, lambda pp, n=n: n, sfn,
                     spin=rnd.uniform(-math.pi, math.pi) if anchor == 0.5 else rnd.uniform(-0.4, 0.4),
                     flip=rnd.random() < 0.5, anchor=anchor)
    return g


# -- impostors ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ImpostorFrame:
    """What an impostor tile shows: x from -half to +half, y from y0 to y0 + height (m)."""
    half: float
    y0: float
    height: float


@functools.lru_cache(maxsize=16)
def impostor_frame(name: str) -> ImpostorFrame:
    g = species_geo(name, 0)
    P = np.array(g.pos)
    _, _, w, h = tile_px(f"imp_{name}")
    half = float(np.max(np.abs(P[:, [0, 2]]))) * 1.02
    y0, y1 = 0.0, float(P[:, 1].max()) * 1.02
    # Keep the tile's aspect: widen or heighten the frame.
    if (2 * half) / (y1 - y0) > w / h:
        y1 = y0 + 2 * half * h / w
    else:
        half = (y1 - y0) * w / h / 2
    return ImpostorFrame(half, y0, y1 - y0)


def rasterize(g: Geo, atlas: np.ndarray, fr: ImpostorFrame, w: int, h: int, ss: int = 2) -> np.ndarray:
    """An orthographic picture of `g` seen from +z (its albedo from `atlas`, alpha-tested at 0.5,
    darkened a little by its grey shade), (h, w, 4), row 0 at the bottom."""
    W, H = w * ss, h * ss
    col = np.zeros((H, W, 3))
    cov = np.zeros((H, W))
    zbuf = np.full((H, W), -1e9)
    ah, aw = atlas.shape[:2]
    P = np.array(g.pos)
    UV = np.array(g.uv)
    S = np.array(g.shade)
    sx = W / (2 * fr.half)
    sy = H / fr.height
    X = (P[:, 0] + fr.half) * sx
    Y = (P[:, 1] - fr.y0) * sy
    for a, b, c in g.faces:
        xs, ys = X[[a, b, c]], Y[[a, b, c]]
        x0, x1 = int(max(0, math.floor(xs.min()))), int(min(W - 1, math.ceil(xs.max())))
        y0, y1 = int(max(0, math.floor(ys.min()))), int(min(H - 1, math.ceil(ys.max())))
        if x0 > x1 or y0 > y1:
            continue
        den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(den) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        l0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / den
        l1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / den
        l2 = 1 - l0 - l1
        inside = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
        if not inside.any():
            continue
        z = l0 * P[a, 2] + l1 * P[b, 2] + l2 * P[c, 2]
        u = l0 * UV[a, 0] + l1 * UV[b, 0] + l2 * UV[c, 0]
        v = l0 * UV[a, 1] + l1 * UV[b, 1] + l2 * UV[c, 1]
        tx = np.clip((u * aw).astype(int), 0, aw - 1)
        ty = np.clip((v * ah).astype(int), 0, ah - 1)
        tex = atlas[ty, tx]
        sh = l0 * S[a] + l1 * S[b] + l2 * S[c]
        sub_z = zbuf[y0:y1 + 1, x0:x1 + 1]
        ok = inside & (tex[..., 3] >= 0.5) & (z > sub_z)
        if not ok.any():
            continue
        sub_z[ok] = z[ok]
        col[y0:y1 + 1, x0:x1 + 1][ok] = tex[..., :3][ok] * (0.72 + 0.28 * sh[ok])[..., None]
        cov[y0:y1 + 1, x0:x1 + 1][ok] = 1.0
    cv = Canvas(w, h, ss)
    cv.rgb, cv.a = col, cov
    return cv.result()


def _impostor(sp: Species) -> Geo:
    """LOD3: three crossed quads (2 x 3 grids, so the bent normals can roll across them) showing
    the impostor tile."""
    g = Geo(sp.name)
    fr = impostor_frame(sp.name)
    tile = f"imp_{sp.name}"
    nu, nv = 2, 3
    cy = fr.y0 + fr.height * (0.62 if sp.kind == "broadleaf" else 0.5)
    for q in range(3):
        a = q * math.pi / 3
        d = (math.cos(a), 0.0, math.sin(a))
        grid = []
        for j in range(nv + 1):
            row = []
            for i in range(nu + 1):
                u, v = i / nu, j / nv
                x = (u - 0.5) * 2 * fr.half
                y = fr.y0 + v * fr.height
                p = (d[0] * x, y, d[2] * x)
                n = norm((d[0] * (u - 0.5) * 2.2, (y - cy) / fr.height * 1.6 + 0.35, d[2] * (u - 0.5) * 2.2))
                row.append(g.vert(p, n, tile_uv(tile, u, v), 1.0))
            grid.append(row)
        for j in range(nv):
            for i in range(nu):
                g.quad(grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i])
    return g


@functools.lru_cache(maxsize=64)
def species_geo(name: str, lod: int) -> Geo:
    sp = SPECIES[name]
    if lod == 3 and sp.kind in ("broadleaf", "pine"):
        return _impostor(sp)
    if sp.kind == "broadleaf":
        return _broadleaf(sp, lod)
    if sp.kind == "pine":
        return _pine(sp, lod)
    return _hedge(sp, lod)


def node_name(species: str, lod: int) -> str:
    return f"{NODE_PREFIX}{species}" + (f"_lod{lod}" if lod else "")


def build_kit() -> dict:
    """{node name: Geo}: every species at every LOD."""
    return {node_name(n, lod): species_geo(n, lod) for n in SPECIES for lod in LODS}


@functools.lru_cache(maxsize=16)
def species_radius(name: str) -> float:
    """The LOD0 crown's reach from the trunk's axis (m, at scale 1)."""
    return float(max(math.hypot(p[0], p[2]) for p in species_geo(name, 0).pos))


@functools.lru_cache(maxsize=16)
def species_top(name: str) -> float:
    return float(max(p[1] for p in species_geo(name, 0).pos))


def kit_manifest() -> dict:
    """The world manifest's `foliage_kit` entry: the file, and per species its LOD0 top (m, the
    client sizes a planting on screen by it) and triangles per LOD."""
    return {"file": KIT_FILE, "lods": len(LODS),
            "species": {n: {"size": round(species_top(n) if SPECIES[n].kind in ("broadleaf", "pine") else HEDGE_SEG, 3),
                            "tris": [species_geo(n, lod).tris for lod in LODS]} for n in SPECIES}}


# -- plantings ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Planting:
    """A species and its affine transform: basis columns ex, ey, ez and the translation t."""
    species: str
    ex: tuple
    ey: tuple
    ez: tuple
    t: tuple

    def point(self, p):
        return add(add(add(mul(self.ex, p[0]), mul(self.ey, p[1])), mul(self.ez, p[2])), self.t)

    def scale(self) -> float:
        """The vertical scale (sizes it on screen)."""
        return length(self.ey)

    def row(self, index: dict) -> list:
        return [index[self.species], *[round(float(c), 4) for v in (self.ex, self.ey, self.ez, self.t) for c in v]]


def planting(species: str, pos=(0.0, 0.0, 0.0), yaw: float = 0.0, scale=1.0) -> Planting:
    """`species` turned `yaw` about +y (glTF Ry: +x toward -z), scaled (a number, or x, y, z), at `pos`."""
    sx, sy, sz = (scale, scale, scale) if isinstance(scale, (int, float)) else scale
    c, s = math.cos(yaw), math.sin(yaw)
    return Planting(species, (c * sx, 0.0, -s * sx), (0.0, sy, 0.0), (s * sz, 0.0, c * sz), tuple(float(v) for v in pos))


def place(p: Planting, point_fn) -> Planting:
    """`p` moved by an affine map given as a point function (a piece's placement, shear included)."""
    t = point_fn(p.t)
    e = [sub(point_fn(add(p.t, v)), t) for v in (p.ex, p.ey, p.ez)]
    return Planting(p.species, e[0], e[1], e[2], t)


TREE_HEIGHTS = {"broadleaf_broad": 7.5, "broadleaf_round": 6.5, "pine_a": 9.0, "pine_b": 9.0}


def tree(kind: str, height: float, seed: int, pos=(0.0, 0.0, 0.0), yaw: float | None = None, scale: float = 1.0) -> Planting:
    """A tree of the old piece kinds: `round` and `broad` (broadleaf), `pine`, `planter`
    (`height` from the planter's soil to the crown's top). Scaled so its top is where the old
    piece's was; turned by its seed unless `yaw` is given."""
    if kind == "pine":
        name = "pine_a" if seed % 2 == 0 else "pine_b"
    elif kind == "planter":
        name = "broadleaf_planter"
    else:
        name = "broadleaf_round" if kind == "round" else "broadleaf_broad"
    k = height / species_top(name) * scale
    return planting(name, pos, (0.9 * seed) % (2 * math.pi) if yaw is None else yaw, k)


def hedge_run(length_m: float, height: float, depth: float, seed: int = 0) -> list[Planting]:
    """A clipped hedge along +x from 0 to `length_m`, centred on z = 0, `height` tall, `depth`
    deep: a rounded end, middle segments (alternating variants) stretched to fit, the other end,
    from the hedge family nearest in height (low or tall)."""
    infix, fh, fd = min(HEDGE_FAMILIES, key=lambda f: abs(math.log(height / f[1])))
    end = f"hedge_{infix}end"
    sy, sz = height / fh, depth / fd
    cap0 = hedge_cap(end)
    cap = min(cap0 * sz, length_m * 0.3)
    cx = cap / cap0
    inner = length_m - 2 * cap
    n = max(1, round(inner / HEDGE_SEG))
    sx = inner / (n * HEDGE_SEG)
    out = [Planting(end, (cx, 0.0, 0.0), (0.0, sy, 0.0), (0.0, 0.0, sz), (cap, 0.0, 0.0))]
    for i in range(n):
        name = f"hedge_{infix}a" if (seed + i) % 2 == 0 else f"hedge_{infix}b"
        out.append(Planting(name, (sx, 0.0, 0.0), (0.0, sy, 0.0), (0.0, 0.0, sz), (cap + i * HEDGE_SEG * sx, 0.0, 0.0)))
    out.append(Planting(end, (-cx, 0.0, 0.0), (0.0, sy, 0.0), (0.0, 0.0, sz), (length_m - cap, 0.0, 0.0)))
    return out


def manifest_rows(plants: list[Planting], min_lod: int = 0) -> dict:
    """A cell's manifest `foliage` entry: the species it uses and one row per planting,
    [species index, ex, ey, ez, t] (12 numbers, glTF axes)."""
    species = sorted({p.species for p in plants})
    index = {n: i for i, n in enumerate(species)}
    out = {"species": species, "plants": [p.row(index) for p in plants]}
    if min_lod:
        out["min_lod"] = min_lod
    return out


TRUNK_BAND = (0.35, 1.9)  # the walking band over a tree's foot (world/solids.py STEP and HEAD)


@functools.lru_cache(maxsize=16)
def trunk_ring(name: str) -> tuple:
    """(x, z, radius) of a tree species' trunk in the walking band at scale 1: the centre and
    the mean radius of its LOD0 bark rings between TRUNK_BAND (the trunk itself, never the
    root flare's lobes below it, the limbs or the crown above)."""
    g = species_geo(name, 0)
    pts = [q for q, uv in zip(g.pos, g.uv) if in_tile(uv, "bark") and TRUNK_BAND[0] <= q[1] <= TRUNK_BAND[1]]
    if not pts:  # a short trunk's rings straddle the band: the nearest ring above the flare
        ys = sorted({round(q[1], 4) for q, uv in zip(g.pos, g.uv) if in_tile(uv, "bark") and q[1] > 0.1})
        pts = [q for q, uv in zip(g.pos, g.uv) if in_tile(uv, "bark") and round(q[1], 4) == ys[0]]
    pts = sorted({(round(q[0], 5), round(q[1], 5), round(q[2], 5)) for q in pts})  # a ring's seam vertex once
    cx = sum(q[0] for q in pts) / len(pts)
    cz = sum(q[2] for q in pts) / len(pts)
    r = sum(math.hypot(q[0] - cx, q[2] - cz) for q in pts) / len(pts)
    return cx, cz, r


def solid_faces(p: Planting) -> list:
    """World-space polygons of what of a planting blocks walking (world/solids.py cuts them to
    the walking band): a tree's trunk as an upright 16-sided prism at its actual radius in the
    band (`trunk_ring`; never the crown), a hedge piece's box at its full depth and height (the
    leaves a character would walk into)."""
    sp = SPECIES[p.species]
    if sp.kind in ("broadleaf", "pine"):
        cx, cz, r = trunk_ring(p.species)
        n = 16
        ring = [(cx + r * math.cos(2 * math.pi * k / n), cz + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
        lo, hi = 0.0, TRUNK_BAND[1] + 0.2  # from the foot (sunk a little): solids.py takes faces from at most SINK under the floor
        faces = [[p.point((ring[k][0], lo, ring[k][1])), p.point((ring[(k + 1) % n][0], lo, ring[(k + 1) % n][1])),
                  p.point((ring[(k + 1) % n][0], hi, ring[(k + 1) % n][1])), p.point((ring[k][0], hi, ring[k][1]))]
                 for k in range(n)]
        return faces
    x0, x1 = (0.0, HEDGE_SEG) if sp.kind == "hedge" else (-hedge_cap(p.species), 0.0)
    hz, y0, y1 = sp.depth / 2, -0.2, sp.height
    corners = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (-hz, hz)]
    c = [p.point(q) for q in corners]
    quads = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
    return [[c[i] for i in q] for q in quads]


def lod0_tris(plants: list[Planting]) -> int:
    return sum(species_geo(p.species, 0).tris for p in plants)
