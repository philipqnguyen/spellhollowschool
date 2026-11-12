"""The Creature Keeping class icons
(`icons/class/<name>`, data/classes/picks.json `creatures_needs` prompts and
`creatures_care` options), rendered by render_icons.py like the other class
icons (icon_models.CLASS_ICONS merges `CREATURE_ICONS`).

Seven needs a creature shows (an empty dish, a water drop, a startled
zigzag, a mud splat, a going-round-in-circles spiral, a snowflake, a
crossed plaster) and eight kinds of care (a full feed bowl, a bucket of
water, a sung note, an open still hand, a grooming brush, a striped ball, a
folded blanket, a paw print). Simple invented shapes, no text. Each model
rests on y = 0, about 0.1-0.3 m across; faces carry a furniture atlas tile
and a dye zone (1 primary, 2 secondary, 3 trim, 0 undyed), tinted by the
palette.json colour ids beside it. Pure Python (meshkit), self-contained.
"""

from __future__ import annotations

import math

import meshkit as mk
from meshkit import Mesh

P, S, T, U = 1, 2, 3, 0
FLAT_T = 0.016  # half the upright symbols' thickness


def _ball(m: Mesh, c, r: float, mat: str, zone: int, seg: int = 10, squash: float = 1.0) -> None:
    prof = [(r * math.sin(math.pi * i / 6), -r * squash * math.cos(math.pi * i / 6)) for i in range(7)]
    mk.lathe(m, prof, seg, mat, zone, c)


def _ccw(outline):
    a = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]))
    return outline if a > 0 else list(reversed(outline))


def _flat(m: Mesh, outline, zone: int, mat: str = "fabric", t: float = FLAT_T, dz: float = 0.0) -> None:
    """An upright flat shape in the x-y plane (a little thickness in z)."""
    mk.prism(m, _ccw(list(outline)), dz - t, dz + t, mat, zone)


def _circle(cx: float, cy: float, r: float, n: int = 12, sx: float = 1.0):
    return [(cx + r * sx * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _band(m: Mesh, pts, w: float, zone: int, mat: str = "fabric", t: float = FLAT_T, dz: float = 0.0,
          joints: bool = True) -> None:
    """A flat stroke of width `w` along the 2D polyline `pts` (quads per segment)."""
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * w / 2, dx / ln * w / 2
        _flat(m, [(x0 + nx, y0 + ny), (x0 - nx, y0 - ny), (x1 - nx, y1 - ny), (x1 + nx, y1 + ny)], zone, mat, t, dz)
    for x, y in pts[1:-1] if joints else ():
        _flat(m, _circle(x, y, w / 2, 8), zone, mat, t * 0.98, dz)


def _bowl(m: Mesh, r: float, h: float, zone: int, inner: int) -> None:
    mk.lathe(m, [(0.0, 0.0), (r * 0.62, 0.0), (r * 0.66, 0.012), (r * 0.94, h * 0.8), (r, h)], 18, "metal", zone,
             cap_top=False)
    mk.lathe(m, [(r, h), (r * 0.93, h * 0.97), (r * 0.6, h * 0.25), (0.0, h * 0.2)], 18, "metal", inner, cap_top=False)


# -- the needs ---------------------------------------------------------------------------------


def need_hungry() -> Mesh:
    """An empty feeding dish, a few crumbs in the bottom."""
    m = Mesh()
    _bowl(m, 0.12, 0.06, P, S)
    for x, z in ((0.02, 0.01), (-0.025, -0.015), (0.0, -0.03)):
        _ball(m, (x, 0.016, z), 0.007, "fabric", T, 6)
    return m


def need_thirsty() -> Mesh:
    """A water drop, standing on its round end, with a highlight."""
    m = Mesh()
    prof = [(0.0, 0.0)] + [(0.07 * math.sin(math.pi * i / 8), 0.07 - 0.07 * math.cos(math.pi * i / 8)) for i in range(1, 6)]
    prof += [(0.07 * (1 - k / 5) ** 1.4 * 0.95, 0.08 + 0.11 * k / 5) for k in range(1, 6)]
    prof[-1] = (0.0, prof[-1][1])
    mk.lathe(m, prof, 16, "glass", P, cap_top=False)
    _ball(m, (-0.03, 0.09, 0.05), 0.012, "glass", T, 6)
    return m


def need_scared() -> Mesh:
    """A startled zigzag, like a jolt."""
    m = Mesh()
    _flat(m, [(-0.02, 0.24), (0.07, 0.24), (0.025, 0.14), (0.08, 0.14), (-0.04, 0.0), (0.0, 0.11), (-0.06, 0.11)], P)
    _flat(m, [(0.1, 0.2), (0.14, 0.23), (0.13, 0.17)], S, dz=0.0)
    _flat(m, [(-0.09, 0.18), (-0.13, 0.2), (-0.12, 0.14)], S, dz=0.0)
    return m


def need_muddy() -> Mesh:
    """A splat of mud with drops round it."""
    m = Mesh()
    _ball(m, (0.0, 0.0, 0.0), 0.1, "leather", P, 14, squash=0.28)
    for k in range(6):
        a = 2 * math.pi * k / 6 + 0.3
        r = 0.1 + 0.02 * (k % 2)
        _ball(m, (r * math.cos(a), 0.0, r * math.sin(a)), 0.035, "leather", P, 8, squash=0.45)
    for x, z, r in ((0.17, 0.05, 0.018), (-0.15, -0.08, 0.014), (0.05, -0.17, 0.016)):
        _ball(m, (x, 0.0, z), r, "leather", S, 6, squash=0.6)
    _ball(m, (-0.03, 0.022, 0.03), 0.03, "leather", S, 8, squash=0.4)
    return m


def need_bored() -> Mesh:
    """A spiral: going round in circles."""
    m = Mesh()
    pts = []
    for i in range(40):
        a = i * 0.32
        r = 0.012 + 0.0032 * i
        pts.append((r * math.cos(a), 0.15 + r * math.sin(a)))
    _band(m, pts, 0.018, P, joints=False)
    _flat(m, _circle(pts[-1][0], pts[-1][1], 0.016, 8), S, dz=0.002)
    return m


def need_cold() -> Mesh:
    """A six-armed snowflake."""
    m = Mesh()
    c = (0.0, 0.14)
    for k in range(6):
        a = math.pi / 2 + k * math.pi / 3
        d = (math.cos(a), math.sin(a))
        tip = (c[0] + 0.13 * d[0], c[1] + 0.13 * d[1])
        _band(m, [c, tip], 0.018, P)
        for f in (0.5, 0.75):
            b = (c[0] + 0.13 * f * d[0], c[1] + 0.13 * f * d[1])
            for side in (-1.0, 1.0):
                aa = a + side * 0.75
                _band(m, [b, (b[0] + 0.04 * math.cos(aa), b[1] + 0.04 * math.sin(aa))], 0.012, S, dz=0.001)
    _flat(m, _circle(c[0], c[1], 0.025, 6), T, dz=0.003)
    return m


def need_hurt() -> Mesh:
    """Two sticking plasters crossed, their pads in the middle."""
    m = Mesh()
    for k, a in enumerate((math.pi / 4, -math.pi / 4)):
        c, s = math.cos(a), math.sin(a)
        L, W = 0.13, 0.04
        pts = []
        for i in range(8):  # a rounded strip
            t = math.pi / 2 + math.pi * i / 7
            pts.append((-L + W * math.cos(t), W * math.sin(t)))
        for i in range(8):
            t = -math.pi / 2 + math.pi * i / 7
            pts.append((L + W * math.cos(t), W * math.sin(t)))
        out = [(0.0 + x * c - y * s, 0.15 + x * s + y * c) for x, y in pts]
        _flat(m, out, P, dz=0.004 * k)
    _flat(m, [(-0.035, 0.115), (0.035, 0.115), (0.035, 0.185), (-0.035, 0.185)], S, dz=0.012)
    for x, y in ((-0.09, 0.06), (0.09, 0.24), (-0.09, 0.24), (0.09, 0.06)):
        _flat(m, _circle(x, y, 0.005, 6), T, dz=0.012)
    return m


# -- the kinds of care -------------------------------------------------------------------------


def care_feed() -> Mesh:
    """A feeding bowl heaped with food."""
    m = Mesh()
    _bowl(m, 0.12, 0.06, P, P)
    mk.lathe(m, [(0.105, 0.05), (0.08, 0.075), (0.04, 0.092), (0.0, 0.098)], 14, "fabric", S, cap_top=False)
    for i in range(9):
        a = 2.39996 * i
        r = 0.085 * math.sqrt((i + 0.5) / 9)
        _ball(m, (r * math.cos(a), 0.098 - 0.4 * r * r / 0.085 + 0.004, r * math.sin(a)), 0.016, "fabric", S, 6)
    return m


def care_water() -> Mesh:
    """A wooden bucket of water with an iron handle."""
    m = Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.075, 0.0), (0.09, 0.15)], 16, "wood", P, cap_top=False)
    mk.lathe(m, [(0.09, 0.15), (0.083, 0.148), (0.07, 0.01)], 16, "wood", P, cap_top=False)
    mk.cylinder(m, (0.0, 0.12, 0.0), 0.085, 0.0, 16, "glass", S, cap_top=True)
    for y in (0.03, 0.12):
        r = 0.075 + 0.015 * y / 0.15 + 0.003
        mk.lathe(m, [(r, y - 0.008), (r, y + 0.008)], 16, "metal", T, cap_top=False)
    pts = [(0.09 * math.cos(math.pi * i / 10), 0.15 + 0.06 * math.sin(math.pi * i / 10), 0.0) for i in range(11)]
    for a, b in zip(pts, pts[1:]):
        mk.rod(m, a, b, 0.005, 4, "metal", T, caps=False)
    return m


def care_hum() -> Mesh:
    """Two joined musical notes: a soft song."""
    m = Mesh()
    for x in (-0.05, 0.06):
        _flat(m, [(x - 0.035 + 0.035 * math.cos(a) * 1.25, 0.04 + 0.025 * math.sin(a)) for a in
                  (2 * math.pi * i / 12 for i in range(12))], P)
        _flat(m, [(x - 0.004, 0.04), (x + 0.006, 0.04), (x + 0.006, 0.22), (x - 0.004, 0.22)], P)
    _flat(m, [(-0.054, 0.2), (0.066, 0.22), (0.066, 0.25), (-0.054, 0.23)], S)
    return m


def care_hand() -> Mesh:
    """An open hand held out still, palm forward."""
    m = Mesh()
    palm = [(-0.055, 0.02), (0.055, 0.02), (0.065, 0.1), (0.06, 0.14), (-0.06, 0.14), (-0.065, 0.1)]
    _flat(m, palm, P)
    for x, top in ((-0.045, 0.235), (-0.015, 0.255), (0.015, 0.25), (0.045, 0.225)):
        _band(m, [(x, 0.13), (x * 1.08, top)], 0.026, P)
        _flat(m, _circle(x * 1.08, top, 0.013, 8), P)
    _band(m, [(0.055, 0.07), (0.1, 0.12), (0.115, 0.155)], 0.028, P)
    _flat(m, _circle(0.115, 0.155, 0.014, 8), P)
    _band(m, [(-0.05, 0.0), (0.05, 0.0)], 0.04, S, dz=0.0)  # the cuff
    return m


def care_brush() -> Mesh:
    """A grooming brush, bristles down, a strap over its back."""
    m = Mesh()
    mk.lathe(m, [(0.0, 0.035), (0.1, 0.035), (0.105, 0.05), (0.09, 0.07), (0.0, 0.075)], 16, "wood", P,
             cap_top=False, cap_bottom=True, scale_xz=(1.0, 0.55))
    for i in range(-3, 4):
        for j in range(-1, 2):
            x, z = i * 0.025, j * 0.02
            if (x / 0.095) ** 2 + (z / 0.05) ** 2 > 1:
                continue
            mk.rod(m, (x, 0.0, z), (x, 0.036, z), 0.007, 4, "fabric", S, caps=True)
    mk.box(m, (-0.07, 0.072, -0.015), (0.07, 0.085, 0.015), "leather", T)
    return m


def care_ball() -> Mesh:
    """A soft play ball with a band round it and a star."""
    m = Mesh()
    _ball(m, (0.0, 0.09, 0.0), 0.09, "fabric", P, 16)
    mk.lathe(m, [(0.0915, 0.075), (0.0915, 0.105)], 16, "fabric", S, (0.0, 0.0, 0.0), cap_top=False)
    _flat(m, [(0.025 * math.cos(math.pi / 2 + i * math.pi / 5) * (1 if i % 2 == 0 else 0.45),
               0.15 + 0.025 * math.sin(math.pi / 2 + i * math.pi / 5) * (1 if i % 2 == 0 else 0.45)) for i in range(10)],
          T, t=0.004, dz=0.07)
    return m


def care_blanket() -> Mesh:
    """A folded blanket: three soft layers, stripes at the edge."""
    m = Mesh()
    for k in range(3):
        y = k * 0.035
        mk.box(m, (-0.13, y, -0.08), (0.13, y + 0.032, 0.08), "fabric", P, skip=() if k == 0 else ("-y",))
        mk.lathe(m, [(0.016, y), (0.016, y + 0.032)], 8, "fabric", P, (0.13, 0.0, 0.0), cap_top=False)
        mk.box(m, (-0.09, y + 0.001, 0.0801), (-0.07, y + 0.031, 0.0811), "fabric", S)
        mk.box(m, (0.07, y + 0.001, 0.0801), (0.09, y + 0.031, 0.0811), "fabric", S)
    return m


def care_paw() -> Mesh:
    """A paw print lying on the ground: a big pad and four toe pads."""
    m = Mesh()
    pad = [(0.0 + 0.06 * math.cos(a) * 1.2, 0.07 + 0.05 * math.sin(a) * (0.8 if math.sin(a) > 0 else 1.0))
           for a in (2 * math.pi * i / 16 for i in range(16))]
    _flat(m, pad, P)
    for x, y in ((-0.075, 0.15), (-0.028, 0.18), (0.028, 0.18), (0.075, 0.15)):
        _flat(m, _circle(x, y, 0.024, 12, sx=0.85), P)
    _flat(m, _circle(0.0, 0.07, 0.025, 10), S, dz=0.004)
    # Lay it on the ground, the toes away from the viewer, so the icon's raised view reads the print.
    m.transform((0, 0), lambda q: (q[0], q[2] + FLAT_T, -(q[1] - 0.12)))
    return m


# name -> (model, (primary, secondary, trim) palette.json colour ids)
CREATURE_ICONS = {
    "need_hungry": (need_hungry, ("tan", "chestnut", "camel")),
    "need_thirsty": (need_thirsty, ("sky", "sky", "linen_white")),
    "need_scared": (need_scared, ("canary", "mustard", "mustard")),
    "need_muddy": (need_muddy, ("oak_brown", "chestnut", "chestnut")),
    "need_bored": (need_bored, ("lavender", "heather", "heather")),
    "need_cold": (need_cold, ("sky", "mint", "linen_white")),
    "need_hurt": (need_hurt, ("blush", "cream", "crimson")),
    "care_feed": (care_feed, ("copper", "chestnut", "chestnut")),
    "care_water": (care_water, ("tan", "cobalt", "pewter")),
    "care_hum": (care_hum, ("gold", "gold", "gold")),
    "care_hand": (care_hand, ("camel", "sage", "sage")),
    "care_brush": (care_brush, ("oak_brown", "cream", "chestnut")),
    "care_ball": (care_ball, ("scarlet", "gold", "gold")),
    "care_blanket": (care_blanket, ("brick", "cream", "cream")),
    "care_paw": (care_paw, ("chestnut", "blush", "blush")),
}
