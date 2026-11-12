"""Spell effects  (pure Python and numpy, no `bpy`): small
meshes the client drives cheaply, a shared texture atlas, and the wand.

Everything is built per spell from `data/spells.json` (colour, kind and
gesture), so a new spell or a retuned colour regenerates its effects:

* attacks (`kind` attack or disarm; shape from the gesture: `jab` a spike,
  `sweep` a twisting ribbon, `spiral` a corkscrew of orbs, `push` a
  crescent wave): `core` (the projectile, travelling along +Z, origin at
  its centre), `trail` (a tapered streak behind it, along -Z), `impact`
  (a burst of rays, a shock ring and a glow, origin at the hit) and
  `charge` (a small glow and ring for the wand tip while the cast is
  telegraphed); the disarm spell adds `disarm` (a starburst flash for the
  victim's hand);
* the shield: `shield` (a hexagon-patterned bubble cap facing +Z, origin
  at the caster's chest, the cap 0.55 m in front), `flash` (the ring
  burst of a perfectly timed block or a reflection) and `charge`;
* the light: `glow` (crossed soft glows round a white-hot core, origin at
  the wand tip; the client draws it for Lucerna casters without a real light);
* levitation: `sparkle` (a ring of sparkles round radius 0.6 m on the
  floor plane with a few rising stars, origin at its centre; the client
  sets it under the floating furniture and spins it).

All effect meshes share one material and one 256 px atlas (`ATLAS_TILES`,
4 x 4 tiles of soft shapes in white with alpha). Colour comes from the
`COLOR_0` vertex colour (linear RGB, per face: a white-hot inside and the
spell's saturated colour outside); the client draws them unlit and
additive: `colour = atlas.rgb * COLOR_0 * atlas.a`. Faces are single quads
or triangles; the material is double-sided.

The wand (`wand_mesh`) is a separate opaque prop with its own tiny swatch
texture: origin at the grip, shaft along +Y, tip `WAND_TIP` above the grip
(the numbers the character animations solve the arm for).
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), "world")]

import meshkit as mk  # noqa: E402
import numpy as np  # noqa: E402
from meshkit import Mesh  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SPELLS = os.path.join(REPO, "data", "spells.json")

ATLAS_SIZE = 256
ATLAS_GRID = 4
ATLAS_TILES = ["glow", "spike", "streak", "ring", "star", "hex", "wisp", "rays", "solid", "dots", "crescent", "spark"]
# Shared with characters/charlib/anim_spells.py (WAND_TIP, WAND_BUTT).
WAND_TIP, WAND_BUTT = 0.26, 0.08
SHAPES = {"jab": "spike", "sweep": "ribbon", "spiral": "corkscrew", "push": "wave"}
BUDGET = 400  # triangles per effect mesh


# ---------------------------------------------------------------------------
# Colours


def hex_rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (1, 3, 5))


def to_linear(c) -> tuple[float, float, float]:
    return tuple((v / 12.92) if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


def mix(a, b, t: float):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


WHITE = (1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# The atlas


def _tile_px() -> int:
    return ATLAS_SIZE // ATLAS_GRID


def tile_rect(name: str) -> tuple[float, float, float, float]:
    """(u0, v0, u1, v1) of a tile, inset half a texel against bleeding."""
    i = ATLAS_TILES.index(name)
    t = 1.0 / ATLAS_GRID
    inset = 1.5 / ATLAS_SIZE
    u0, v0 = (i % ATLAS_GRID) * t, (i // ATLAS_GRID) * t
    return (u0 + inset, v0 + inset, u0 + t - inset, v0 + t - inset)


def tile_uv(name: str, u: float, v: float) -> tuple[float, float]:
    u0, v0, u1, v1 = tile_rect(name)
    return (u0 + (u1 - u0) * u, v0 + (v1 - v0) * v)


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def hex_lines(u, v, size: float):
    """Coverage of the edges of a pointy-top hexagon grid (cells `size`
    from centre to corner): axial coordinates rounded to the nearest
    centre, then the distance to that cell's edge."""
    q = (math.sqrt(3) / 3 * u - v / 3) / size
    r = (2.0 / 3 * v) / size
    x, z = q, r
    y = -x - z
    rx, ry, rz = np.round(x), np.round(y), np.round(z)
    dx_, dy_, dz_ = np.abs(rx - x), np.abs(ry - y), np.abs(rz - z)
    rx = np.where((dx_ > dy_) & (dx_ > dz_), -ry - rz, rx)
    rz = np.where(~((dx_ > dy_) & (dx_ > dz_)) & ~(dy_ > dz_), -rx - ry, rz)
    cx = size * math.sqrt(3) * (rx + rz / 2)
    cy = size * 1.5 * rz
    ox, oy = np.abs(u - cx), np.abs(v - cy)
    d = np.maximum(ox, 0.5 * ox + math.sqrt(3) / 2 * oy)
    inner = size * math.sqrt(3) / 2
    return _smooth(inner * 0.8, inner * 0.97, d)


def paint_tile(name: str, n: int) -> np.ndarray:
    """One tile: white RGB, the shape in alpha (rows bottom to top)."""
    ys, xs = np.mgrid[0:n, 0:n].astype(np.float32)
    u, v = (xs + 0.5) / n, (ys + 0.5) / n
    dx, dy = u - 0.5, v - 0.5
    r = np.sqrt(dx * dx + dy * dy) * 2
    ang = np.arctan2(dy, dx)
    if name == "glow":
        a = np.exp(-(r * 2.2) ** 2) * _smooth(1.0, 0.8, r)
    elif name == "spike":
        a = np.exp(-(dx * 7.5) ** 2) * _smooth(0.5, 0.0, np.abs(dy)) ** 1.5
    elif name == "streak":
        a = np.exp(-(dx * 6.0) ** 2) * v ** 1.6
    elif name == "ring":
        a = np.exp(-((r - 0.78) * 9.0) ** 2) + 0.25 * np.exp(-(r * 1.6) ** 2)
    elif name == "star":
        a = np.maximum(np.exp(-(dx * 18) ** 2) * _smooth(0.5, 0.0, np.abs(dy)),
                       np.exp(-(dy * 18) ** 2) * _smooth(0.5, 0.0, np.abs(dx))) + np.exp(-(r * 5) ** 2)
    elif name == "hex":
        # A honeycomb of thin bright lines inside a disc with a glowing rim.
        line = hex_lines(u, v, 0.075)
        a = (0.16 + 0.6 * line) * _smooth(0.9, 0.84, r) + np.exp(-((r - 0.86) * 18) ** 2) * _smooth(1.0, 0.92, r)
    elif name == "wisp":
        rng = np.random.default_rng(5)
        a = np.zeros_like(u)
        for _ in range(7):
            cx, w, ph = rng.uniform(0.2, 0.8), rng.uniform(0.04, 0.09), rng.uniform(0, 6.28)
            x = cx + 0.08 * np.sin(v * 9 + ph)
            a = np.maximum(a, np.exp(-((u - x) / w) ** 2) * np.sin(np.pi * v) ** 1.5)
    elif name == "rays":
        k = 10
        a = (0.5 + 0.5 * np.cos(ang * k)) ** 6 * _smooth(1.0, 0.1, r) + np.exp(-(r * 4) ** 2)
    elif name == "solid":
        a = np.ones_like(u) * _smooth(1.0, 0.9, np.maximum(np.abs(dx), np.abs(dy)) * 2)
    elif name == "dots":
        a = np.zeros_like(u)
        for cx, cy, s in ((0.3, 0.3, 0.12), (0.7, 0.35, 0.09), (0.45, 0.72, 0.1), (0.75, 0.75, 0.07)):
            a = np.maximum(a, np.exp(-(((u - cx) ** 2 + (v - cy) ** 2) / (s * s * 0.5))))
    elif name == "crescent":
        outer = np.exp(-((r - 0.7) * 6) ** 2)
        a = outer * _smooth(-0.2, 0.6, dy * 2)
    elif name == "spark":
        a = np.maximum(np.exp(-(dx * 30) ** 2) * _smooth(0.5, 0.0, np.abs(dy)),
                       np.exp(-(dy * 30) ** 2) * _smooth(0.5, 0.0, np.abs(dx))) * 0.8
        diag1, diag2 = (dx + dy) / 1.414, (dx - dy) / 1.414
        a = np.maximum(a, 0.5 * np.maximum(np.exp(-(diag1 * 40) ** 2), np.exp(-(diag2 * 40) ** 2)) * _smooth(0.35, 0.0, r / 2))
        a = a + np.exp(-(r * 7) ** 2)
    else:
        raise KeyError(name)
    out = np.ones((n, n, 4), dtype=np.float32)
    out[..., 3] = np.clip(a, 0.0, 1.0)
    return out


def paint_atlas() -> np.ndarray:
    n = _tile_px()
    img = np.zeros((ATLAS_SIZE, ATLAS_SIZE, 4), dtype=np.float32)
    for i, name in enumerate(ATLAS_TILES):
        x, y = (i % ATLAS_GRID) * n, (i // ATLAS_GRID) * n
        img[y : y + n, x : x + n] = paint_tile(name, n)
    return img


# ---------------------------------------------------------------------------
# Effect meshes: polygons with atlas UVs and a colour per face


@dataclass
class Fx(Mesh):
    colours: list = field(default_factory=list)  # linear RGB per face

    def card(self, corners, tile: str, colour, uvs01=((0, 0), (1, 0), (1, 1), (0, 1))) -> None:
        """A textured quad (or triangle) on an atlas tile, in one colour."""
        n = len(self.faces)
        self.poly(list(corners), "fx", [tile_uv(tile, u, v) for u, v in uvs01[: len(corners)]])
        if len(self.faces) > n:
            self.colours.append(tuple(colour))


def _orth(d):
    d = mk.normalize(d)
    a = (0.0, 1.0, 0.0) if abs(d[1]) < 0.9 else (1.0, 0.0, 0.0)
    u = mk.normalize(mk.cross(a, d))
    return u, mk.cross(d, u)


def billboard_cross(m: Fx, centre, size: float, tile: str, colour, planes: int = 3) -> None:
    """Soft shapes visible from any side: `planes` cards through a point
    (the three axis planes)."""
    c = centre
    h = size / 2
    axes = [((h, 0, 0), (0, h, 0)), ((0, 0, h), (0, h, 0)), ((h, 0, 0), (0, 0, h))][:planes]
    for a, b in axes:
        corners = [mk.sub(mk.sub(c, a), b), mk.sub(mk.add(c, a), b), mk.add(mk.add(c, a), b), mk.add(mk.sub(c, a), b)]
        m.card(corners, tile, colour)


def beam_cards(m: Fx, a, b, width: float, tile: str, colour, rolls: int = 3) -> None:
    """Cards along a segment a -> b (the tile's v runs from a to b), turned
    about the segment so it reads from the side at any angle."""
    d = mk.sub(b, a)
    u, w = _orth(d)
    for k in range(rolls):
        ang = math.pi * k / rolls
        side = mk.add(mk.scale(u, math.cos(ang) * width / 2), mk.scale(w, math.sin(ang) * width / 2))
        m.card([mk.sub(a, side), mk.add(a, side), mk.add(b, side), mk.sub(b, side)], tile, colour)


def core_spike(colour) -> Fx:
    """A stun bolt: a long white-hot spike in a coloured sheath."""
    m = Fx()
    hot = to_linear(mix(colour, WHITE, 0.75))
    col = to_linear(colour)
    beam_cards(m, (0, 0, -0.45), (0, 0, 0.45), 0.36, "spike", col)
    beam_cards(m, (0, 0, -0.3), (0, 0, 0.35), 0.14, "spike", hot)
    billboard_cross(m, (0, 0, 0.12), 0.5, "glow", col)
    billboard_cross(m, (0, 0, 0.12), 0.2, "glow", hot)
    return m


def core_ribbon(colour) -> Fx:
    """A disarming bolt: two ribbons twisting round a bright head."""
    m = Fx()
    hot = to_linear(mix(colour, WHITE, 0.7))
    col = to_linear(colour)
    n = 10
    for strand in range(2):
        for i in range(n):
            t0, t1 = i / n, (i + 1) / n
            z0, z1 = 0.2 - t0 * 0.9, 0.2 - t1 * 0.9
            a0, a1 = strand * math.pi + t0 * 2.6 * math.pi, strand * math.pi + t1 * 2.6 * math.pi
            r0, r1 = 0.16 * (1 - t0 * 0.5), 0.16 * (1 - t1 * 0.5)
            w0, w1 = 0.1 * (1 - t0 * 0.8), 0.1 * (1 - t1 * 0.8)
            p0 = (r0 * math.cos(a0), r0 * math.sin(a0), z0)
            p1 = (r1 * math.cos(a1), r1 * math.sin(a1), z1)
            n0 = (math.cos(a0) * w0, math.sin(a0) * w0, 0.0)
            n1 = (math.cos(a1) * w1, math.sin(a1) * w1, 0.0)
            m.card([mk.sub(p0, n0), mk.add(p0, n0), mk.add(p1, n1), mk.sub(p1, n1)], "streak", col,
                   ((0, 1 - t0), (1, 1 - t0), (1, 1 - t1), (0, 1 - t1)))
    billboard_cross(m, (0, 0, 0.18), 0.55, "glow", col)
    billboard_cross(m, (0, 0, 0.18), 0.26, "star", hot)
    return m


def core_corkscrew(colour) -> Fx:
    """A tickling charm: three sparkling orbs chasing each other round the
    line of flight."""
    m = Fx()
    hot = to_linear(mix(colour, WHITE, 0.7))
    col = to_linear(colour)
    for k in range(3):
        a = 2 * math.pi * k / 3
        z = 0.1 - 0.18 * k
        c = (0.16 * math.cos(a), 0.16 * math.sin(a), z)
        billboard_cross(m, c, 0.3 - 0.05 * k, "glow", col, planes=2)
        billboard_cross(m, c, 0.18 - 0.03 * k, "spark", hot, planes=2)
    billboard_cross(m, (0, 0, 0.0), 0.4, "ring", col, planes=1)
    return m


def core_wave(colour) -> Fx:
    """A knockback jinx: a crescent shock wave bowed toward its target."""
    m = Fx()
    hot = to_linear(mix(colour, WHITE, 0.6))
    col = to_linear(colour)
    rings, segs = 3, 10
    for j in range(rings):
        r = 0.5 - 0.12 * j
        depth = 0.18 - 0.05 * j
        for i in range(segs):
            a0, a1 = -math.pi / 2 + math.pi * i / segs, -math.pi / 2 + math.pi * (i + 1) / segs
            def pt(a, rr, dz):
                return (rr * math.sin(a), rr * 0.55 * math.cos(a) - 0.05, dz - depth * math.cos(a) ** 2)
            p = [pt(a0, r, 0.0), pt(a1, r, 0.0), pt(a1, r * 0.75, -0.25), pt(a0, r * 0.75, -0.25)]
            m.card(p, "wisp", col if j else hot, ((i / segs, 1), ((i + 1) / segs, 1), ((i + 1) / segs, 0), (i / segs, 0)))
    billboard_cross(m, (0, 0, -0.05), 0.6, "glow", col)
    return m


def trail(colour, length: float = 1.6, width: float = 0.26) -> Fx:
    """A streak fading behind the projectile (along -Z from the origin)."""
    m = Fx()
    beam_cards(m, (0, 0, -length), (0, 0, 0.0), width, "streak", to_linear(colour))
    beam_cards(m, (0, 0, -length * 0.6), (0, 0, 0.0), width * 0.35, "streak", to_linear(mix(colour, WHITE, 0.7)))
    return m


def impact(colour, seed: int) -> Fx:
    """A hit: rays bursting in every direction, a shock ring and a glow."""
    m = Fx()
    hot = to_linear(mix(colour, WHITE, 0.7))
    col = to_linear(colour)
    rng = np.random.default_rng(seed)
    for _ in range(9):
        d = rng.normal(size=3)
        d = d / np.linalg.norm(d)
        length = rng.uniform(0.35, 0.6)
        beam_cards(m, (0, 0, 0), tuple(d * length), 0.12, "spike", col, rolls=2)
    billboard_cross(m, (0, 0, 0), 0.9, "ring", col)
    billboard_cross(m, (0, 0, 0), 0.7, "glow", col)
    billboard_cross(m, (0, 0, 0), 0.35, "star", hot)
    return m


def charge(colour) -> Fx:
    """At the wand tip while a spell is cast: a small glow and a ring."""
    m = Fx()
    billboard_cross(m, (0, 0, 0), 0.24, "glow", to_linear(colour))
    billboard_cross(m, (0, 0, 0), 0.1, "star", to_linear(mix(colour, WHITE, 0.8)))
    billboard_cross(m, (0, 0, 0), 0.3, "ring", to_linear(colour), planes=1)
    return m


def shield(colour) -> Fx:
    """A shield bubble: a hexagon-patterned spherical cap facing +Z, 1.1 m
    across, 0.55 m in front of the origin, with a bright rim."""
    m = Fx()
    col = to_linear(colour)
    rim = to_linear(mix(colour, WHITE, 0.5))
    R, cap = 1.2, 0.62  # sphere radius, cap half angle
    rings, segs = 4, 16
    cz = 0.55 - R  # sphere centre so the cap's crown sits at z 0.55
    def pt(i, j):
        th = cap * i / rings
        ph = 2 * math.pi * j / segs
        return (R * math.sin(th) * math.cos(ph), R * math.sin(th) * math.sin(ph) + 0.0, cz + R * math.cos(th))
    def uv(i, j):
        th = i / rings
        ph = 2 * math.pi * j / segs
        return (0.5 + 0.43 * th * math.cos(ph), 0.5 + 0.43 * th * math.sin(ph))
    for i in range(rings):
        for j in range(segs):
            if i == 0:
                corners = [pt(0, 0), pt(1, j), pt(1, j + 1)]
                uvs = [uv(0, 0), uv(1, j), uv(1, j + 1)]
            else:
                corners = [pt(i, j), pt(i + 1, j), pt(i + 1, j + 1), pt(i, j + 1)]
                uvs = [uv(i, j), uv(i + 1, j), uv(i + 1, j + 1), uv(i, j + 1)]
            m.card(corners, "hex", col, uvs)
    # The rim: a ring of cards round the cap's edge, facing out.
    for j in range(segs):
        a, b = pt(rings, j), pt(rings, j + 1)
        out_a = mk.scale(mk.normalize((a[0], a[1], 0.0)), 0.08)
        out_b = mk.scale(mk.normalize((b[0], b[1], 0.0)), 0.08)
        m.card([mk.sub(a, out_a), mk.sub(b, out_b), mk.add(b, out_b), mk.add(a, out_a)], "spike", rim,
               ((0, 0.5), (1, 0.5), (1, 1), (0, 1)))
    return m


def flash(colour) -> Fx:
    """A perfect block or reflection: a wide ring and rays in the shield's plane."""
    m = Fx()
    col = to_linear(colour)
    hot = to_linear(mix(colour, WHITE, 0.7))
    s = 1.4
    m.card([(-s, -s, 0.55), (s, -s, 0.55), (s, s, 0.55), (-s, s, 0.55)], "ring", col)
    m.card([(-s * 0.8, -s * 0.8, 0.56), (s * 0.8, -s * 0.8, 0.56), (s * 0.8, s * 0.8, 0.56), (-s * 0.8, s * 0.8, 0.56)],
           "rays", hot)
    billboard_cross(m, (0, 0, 0.55), 0.8, "star", hot)
    return m


def glow(colour) -> Fx:
    """A wand light: crossed soft glows round a white-hot core and a few
    floating motes (origin at the wand tip)."""
    m = Fx()
    col = to_linear(colour)
    billboard_cross(m, (0, 0, 0), 0.9, "glow", col)
    billboard_cross(m, (0, 0, 0), 0.32, "glow", to_linear(mix(colour, WHITE, 0.85)))
    billboard_cross(m, (0, 0, 0), 0.22, "star", WHITE)
    for k in range(4):
        a = 2 * math.pi * k / 4 + 0.4
        billboard_cross(m, (0.16 * math.cos(a), 0.05 * (k - 1.5), 0.16 * math.sin(a)), 0.06, "glow", col, planes=2)
    return m


def sparkle(colour) -> Fx:
    """Levitation: a ring of sparkles round radius 0.6 on the floor plane
    and a few stars rising inside it."""
    m = Fx()
    col = to_linear(colour)
    hot = to_linear(mix(colour, WHITE, 0.7))
    s = 0.72
    m.card([(-s, 0.0, s), (s, 0.0, s), (s, 0.0, -s), (-s, 0.0, -s)], "ring", col)
    for k in range(10):
        a = 2 * math.pi * k / 10
        c = (0.6 * math.cos(a), 0.04 + 0.05 * (k % 3), 0.6 * math.sin(a))
        billboard_cross(m, c, 0.14 if k % 2 else 0.1, "spark", hot, planes=2)
    for k in range(5):
        a = 2 * math.pi * k / 5 + 0.3
        c = (0.3 * math.cos(a), 0.25 + 0.12 * k, 0.3 * math.sin(a))
        billboard_cross(m, c, 0.12, "star", col, planes=2)
    return m


def disarm_flash(colour) -> Fx:
    """The disarming hit at the victim's hand: a starburst and a ring."""
    m = Fx()
    col = to_linear(colour)
    hot = to_linear(mix(colour, WHITE, 0.8))
    billboard_cross(m, (0, 0, 0), 0.8, "rays", hot)
    billboard_cross(m, (0, 0, 0), 0.6, "ring", col)
    billboard_cross(m, (0, 0, 0), 0.3, "star", WHITE)
    return m


CORES = {"spike": core_spike, "ribbon": core_ribbon, "corkscrew": core_corkscrew, "wave": core_wave}


def load_spells(path: str = SPELLS) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["spells"]


def effects(spells: list[dict] | None = None) -> tuple[dict[str, Fx], dict]:
    """Every effect mesh by node name, and the manifest's `spells` table
    (spell id -> {role: node name, colour, shape?})."""
    spells = load_spells() if spells is None else spells
    meshes: dict[str, Fx] = {}
    table: dict[str, dict] = {}
    for sp in spells:
        sid, colour = sp["id"], hex_rgb(sp["colour"])
        entry: dict = {"colour": sp["colour"]}
        effect = (sp.get("duel") or {}).get("effect", {})
        kind = sp["kind"]
        roles: dict[str, Fx] = {}
        if effect.get("kind") == "projectile":
            shape = SHAPES.get(sp["gesture"], "spike")
            entry["shape"] = shape
            roles["core"] = CORES[shape](colour)
            roles["trail"] = trail(colour)
            roles["impact"] = impact(colour, sid)
            roles["charge"] = charge(colour)
            if kind == "disarm" or effect.get("disarm_ms"):
                roles["disarm"] = disarm_flash(colour)
        elif effect.get("kind") == "shield":
            roles["shield"] = shield(colour)
            roles["flash"] = flash(colour)
            roles["charge"] = charge(colour)
        social = sp.get("social", {}).get("kind")
        if social == "light":
            roles["glow"] = glow(colour)
        elif social == "levitate":
            roles["sparkle"] = sparkle(colour)
        for role, fx in roles.items():
            name = f"spell_{sid}_{role}"
            meshes[name] = fx
            entry[role] = name
        table[str(sid)] = entry
    return meshes, table


# ---------------------------------------------------------------------------
# The wand


WAND_SWATCHES = ["#3A2216", "#7A4529", "#E2B33C", "#F2E6C8"]  # dark wood, warm wood (castle `oak_rail`), gold collar, pale tip
# A small prop (docs/art/style-guide.md, triangle budgets: 50-400).
WAND_BUDGET = 400
WAND_SIDES = 8


def wand_swatch_uv(i: int) -> tuple[float, float]:
    return ((i % 2 + 0.5) / 2, (i // 2 + 0.5) / 2)


def wand_profile() -> list[tuple[list[tuple[float, float]], int]]:
    """The wand's lathe profile, butt to tip, as parts `([(radius, y)], swatch)`:
    a round pommel, a handle carved into three beads (the fist closes round
    the grip at y 0), a gold collar just past the fist, then a warm shaft
    tapering to a pale tip at `WAND_TIP`."""
    b = -WAND_BUTT - 0.01  # the end of the pommel (35 cm overall)
    return [
        ([(0.0, b), (0.0085, b + 0.003), (0.0125, b + 0.01), (0.0112, b + 0.017), (0.0084, b + 0.021)], 0),
        ([(0.0084, b + 0.021), (0.0106, b + 0.028), (0.0108, -0.046), (0.0090, -0.040), (0.0107, -0.034),
          (0.0107, -0.021), (0.0090, -0.015), (0.0104, -0.009), (0.0102, 0.018), (0.0088, 0.030)], 0),
        ([(0.0088, 0.030), (0.0104, 0.033), (0.0104, 0.040), (0.0084, 0.043)], 2),
        ([(0.0084, 0.043), (0.0076, 0.08), (0.0064, 0.14), (0.0052, 0.20), (0.0042, WAND_TIP - 0.012)], 1),
        ([(0.0042, WAND_TIP - 0.012), (0.0034, WAND_TIP - 0.004), (0.0, WAND_TIP)], 3),
    ]


def wand_mesh() -> Mesh:
    """A stylized wand: a carved dark handle with a pommel, a gold collar,
    a warm tapering shaft and a pale tip. Origin at the grip, +Y to the tip."""
    m = Mesh()
    for prof, sw in wand_profile():
        n = len(m.faces)
        mk.lathe(m, prof, WAND_SIDES, "wand", smooth=True, cap_top=False, cap_bottom=False)
        uv = wand_swatch_uv(sw)
        for f in m.faces[n:]:
            f.uvs = [uv] * len(f.idx)
    return m


def wand_texture(size: int = 64) -> np.ndarray:
    img = np.ones((size, size, 4), dtype=np.float32)
    h = size // 2
    for i, hexc in enumerate(WAND_SWATCHES):
        x, y = (i % 2) * h, (i // 2) * h
        img[y : y + h, x : x + h, :3] = hex_rgb(hexc)
    return img
