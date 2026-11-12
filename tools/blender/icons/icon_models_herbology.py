"""Herbology's class icons (docs/architecture.md,
picks.json `herbology_plants` prompts and `herbology_tools`
options), merged into icon_models.CLASS_ICONS and rendered by
render_icons.py like the other class icons.

* `plant_vine`, `plant_root`, `plant_briar`, `plant_fern`: the four class
  plants as the greenhouse shows them (world/greenhouse_plants.py), scaled
  down, their parts in three dye zones.
* `tool_*`: the eight tools of care, our own plain designs: a watering can,
  pruning shears, fluffy earmuffs, a cane stake with its twine, a pair of
  thick gloves, a sack of compost, a little shade parasol and a soft
  pollen duster.

Pure Python (meshkit); faces carry a furniture atlas tile and a dye zone
(1 primary, 2 secondary, 3 trim) like icon_models.py; colours are
palette.json ids. Every model sits on y = 0 (icon_models.build_class_icon
rests it there), under 0.5 m across."""

from __future__ import annotations

import math
import os
import sys

_WORLD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "world")
if _WORLD not in sys.path:
    sys.path.insert(0, _WORLD)

import greenhouse_plants as GP  # noqa: E402
import meshkit as mk  # noqa: E402
from meshkit import Mesh  # noqa: E402

P, S, T = 1, 2, 3


def _plant(pid: str, zones: dict, scale: float):
    """A class plant as an icon: the greenhouse model scaled, its palette
    zones mapped to the icon's dye zones."""

    def build() -> Mesh:
        m, _ = GP.build(pid)
        for f in m.faces:
            f.zone = zones[f.zone]
        m.transform((0, 0), lambda p: (p[0] * scale, p[1] * scale, p[2] * scale))
        return m

    return build


def _ellipsoid(m: Mesh, c, r, tile: str, zone: int, seg: int = 10, rings: int = 6) -> None:
    prof = [(math.sin(math.pi * i / rings), -math.cos(math.pi * i / rings)) for i in range(rings + 1)]
    mark = m.mark()
    mk.lathe(m, prof, seg, tile, zone, cap_top=False)
    m.transform(mark, lambda p: (c[0] + p[0] * r[0], c[1] + p[1] * r[1], c[2] + p[2] * r[2]))


def tool_water() -> Mesh:
    """A watering can: a round body, a long spout with its rose, a hoop handle."""
    m = Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.075, 0.0), (0.08, 0.02), (0.08, 0.13), (0.06, 0.15), (0.03, 0.155)], 14, "metal", P)
    mk.rod(m, (0.06, 0.03, 0.0), (0.2, 0.17, 0.0), 0.011, 6, "metal", P)
    rose = m.mark()  # the rose, built round +y, then turned along the spout (45 degrees up) to its end
    mk.lathe(m, [(0.012, 0.0), (0.03, 0.028), (0.031, 0.034), (0.0, 0.035)], 8, "metal", S)
    c = math.sqrt(0.5)
    m.transform(rose, lambda p: (0.195 + c * (p[0] + p[1]), 0.165 + c * (p[1] - p[0]), p[2]))
    for k in range(9):  # the hoop handle over the top
        a0, a1 = math.pi * k / 9, math.pi * (k + 1) / 9
        mk.rod(m, (-0.06 + 0.06 * (1 - math.cos(a0)), 0.15 + 0.06 * math.sin(a0), 0.0),
               (-0.06 + 0.06 * (1 - math.cos(a1)), 0.15 + 0.06 * math.sin(a1), 0.0), 0.009, 5, "metal", T)
    return m


def tool_shears() -> Mesh:
    """Pruning shears, open: two curved blades crossing at a pivot, two
    handles with grips."""
    m = Mesh()
    for s in (-1.0, 1.0):
        a = s * 0.22
        ca, sa = math.cos(a), math.sin(a)

        def rot(p, ca=ca, sa=sa):
            return (p[0] * ca - p[1] * sa, p[0] * sa + p[1] * ca, p[2])

        mark = m.mark()
        mk.prism(m, [(0.0, -0.012), (0.11, -0.006), (0.15, 0.012), (0.1, 0.02), (0.0, 0.016)], -0.004 * s - 0.004,
                 -0.004 * s + 0.004, "metal", P)  # a blade
        mk.rod(m, (0.0, 0.0, 0.0), (-0.07, 0.0, 0.0), 0.007, 5, "metal", P)
        mk.rod(m, (-0.07, 0.0, 0.0), (-0.17, -s * 0.02, 0.0), 0.016, 6, "fabric", S)  # a grip
        m.transform(mark, lambda p, rot=rot: rot(p))
    mk.cylinder(m, (0.0, 0.0, -0.012), 0.014, 0.024, 8, "metal", T)
    m.transform((0, 0), lambda p: (p[0], p[2] + 0.02, -p[1]))  # lay them flat
    return m


def tool_earmuffs() -> Mesh:
    """Fluffy earmuffs on a curved band."""
    m = Mesh()
    for s in (-1.0, 1.0):
        _ellipsoid(m, (s * 0.11, 0.07, 0.0), (0.035, 0.07, 0.065), "fabric", P)
        _ellipsoid(m, (s * 0.085, 0.07, 0.0), (0.02, 0.045, 0.04), "fabric", S)
    n = 12
    for k in range(n):
        a0, a1 = math.pi * k / n, math.pi * (k + 1) / n
        mk.rod(m, (0.12 * math.cos(a0), 0.07 + 0.15 * math.sin(a0), 0.0),
               (0.12 * math.cos(a1), 0.07 + 0.15 * math.sin(a1), 0.0), 0.012, 6, "metal", T)
    return m


def tool_stake() -> Mesh:
    """A cane stake with a pointed foot, a twine tie and a slip of a label."""
    m = Mesh()
    mk.rod(m, (0.0, 0.04, 0.0), (0.0, 0.34, 0.0), 0.012, 6, "wood", P)
    mk.lathe(m, [(0.012, 0.04), (0.0, 0.0)], 6, "wood", P, cap_top=False)
    for y in (0.17, 0.26):
        mk.lathe(m, [(0.016, y), (0.018, y + 0.008), (0.016, y + 0.016)], 8, "linen", S, cap_top=False)
    mk.rod(m, (0.0, 0.262, 0.0), (0.05, 0.2, 0.02), 0.004, 4, "linen", S)
    mk.box(m, (0.03, 0.17, 0.018), (0.09, 0.2, 0.024), "linen", T)
    return m


def _glove(m: Mesh, x: float, turn: float) -> None:
    mark = m.mark()
    mk.box(m, (-0.04, 0.0, -0.012), (0.04, 0.09, 0.012), "leather", P)  # the palm
    for k, (fx, fl) in enumerate(((-0.03, 0.06), (-0.01, 0.07), (0.01, 0.068), (0.03, 0.055))):
        mk.rod(m, (fx, 0.085, 0.0), (fx, 0.085 + fl, 0.0), 0.0105, 6, "leather", P)
    mk.rod(m, (0.04, 0.03, 0.0), (0.075, 0.075, 0.0), 0.012, 6, "leather", P)  # the thumb
    mk.box(m, (-0.048, -0.05, -0.016), (0.048, 0.0, 0.016), "leather", S)  # the cuff
    mk.box(m, (-0.049, -0.006, -0.017), (0.049, 0.004, 0.017), "leather", T)  # the stitched seam
    c, s = math.cos(turn), math.sin(turn)
    m.transform(mark, lambda p: (x + p[0] * c - p[1] * s, p[2] + 0.017, -(p[0] * s + p[1] * c)))


def tool_gloves() -> Mesh:
    """A pair of thick gloves lying side by side."""
    m = Mesh()
    _glove(m, -0.06, 0.15)
    _glove(m, 0.07, -0.2)
    return m


def tool_compost() -> Mesh:
    """A sack of compost, tied at the neck, with a heap spilled before it."""
    m = Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.09, 0.0), (0.11, 0.06), (0.1, 0.16), (0.05, 0.21), (0.035, 0.22)], 12, "linen", P,
             cap_top=False)
    mk.lathe(m, [(0.035, 0.22), (0.045, 0.24), (0.05, 0.27), (0.0, 0.265)], 10, "linen", P, cap_top=False)
    mk.lathe(m, [(0.038, 0.215), (0.04, 0.225), (0.037, 0.235)], 10, "fabric", T, cap_top=False)  # the tie
    _ellipsoid(m, (0.0, 0.0, 0.12), (0.09, 0.05, 0.06), "fabric", S, 10, 4)  # the spilled heap
    _ellipsoid(m, (0.06, 0.0, 0.15), (0.04, 0.025, 0.035), "fabric", S, 8, 4)
    return m


def tool_shade() -> Mesh:
    """A little shade parasol: a ribbed canopy on a stick, a trim at its rim."""
    m = Mesh()
    mk.rod(m, (0.0, 0.0, 0.0), (0.0, 0.3, 0.0), 0.007, 6, "wood", T)
    n = 8
    rim = [(0.15 * math.sin(2 * math.pi * k / n), 0.2, 0.15 * math.cos(2 * math.pi * k / n)) for k in range(n)]
    top = (0.0, 0.29, 0.0)
    for k in range(n):
        a, b = rim[k], rim[(k + 1) % n]
        sag = ((a[0] + b[0]) * 0.47, 0.215, (a[2] + b[2]) * 0.47)
        m.poly([a, b, top], "fabric", zone=P)
        m.poly([top, b, a], "fabric", zone=P)
        m.poly([a, sag, b], "fabric", zone=S)
        m.poly([b, sag, a], "fabric", zone=S)
    mk.lathe(m, [(0.0, 0.29), (0.012, 0.3), (0.0, 0.32)], 6, "wood", T, cap_top=False)
    return m


def tool_duster() -> Mesh:
    """A soft pollen duster: a round fluffy head on a turned handle."""
    m = Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.016, 0.0), (0.014, 0.08), (0.018, 0.14), (0.02, 0.16)], 8, "wood", S, cap_top=False)
    mk.lathe(m, [(0.02, 0.16), (0.026, 0.165), (0.026, 0.185), (0.02, 0.19)], 10, "metal", T, cap_top=False)
    _ellipsoid(m, (0.0, 0.25, 0.0), (0.075, 0.075, 0.075), "fabric", P, 12, 7)
    for k in range(10):  # tufts
        a = k * 2.39996
        d = (math.cos(a) * math.sin(0.4 + k * 0.25), math.cos(0.4 + k * 0.25), math.sin(a) * math.sin(0.4 + k * 0.25))
        _ellipsoid(m, (0.075 * d[0], 0.25 + 0.075 * d[1], 0.075 * d[2]), (0.025, 0.025, 0.025), "fabric", P, 6, 3)
    return m


# name -> (model, (primary, secondary, trim) palette.json colour ids)
HERBOLOGY_ICONS = {
    "plant_vine": (_plant("plant_vine", {GP.VINE: P, GP.LEAF: P, GP.MAW: S, GP.TOOTH: T}, 0.3),
                   ("forest", "burgundy", "ivory")),
    "plant_root": (_plant("plant_root", {GP.ROOT: P, GP.LEAF: S, GP.LEAF_LIGHT: S, GP.DARK: T, GP.MAW: T}, 0.45),
                   ("tan", "moss", "umber_black")),
    "plant_briar": (_plant("plant_briar", {GP.BRIAR: P, GP.THORN: S, GP.STEM: T}, 0.4),
                    ("forest", "cream", "oak_brown")),
    "plant_fern": (_plant("plant_fern", {GP.FERN: P, GP.LEAF_LIGHT: S, GP.STEM: T}, 0.32),
                   ("sage", "mint", "chestnut")),
    "tool_water": (tool_water, ("copper", "bronze", "pewter")),
    "tool_shears": (tool_shears, ("silver", "crimson", "pewter")),
    "tool_earmuffs": (tool_earmuffs, ("dusty_rose", "blush", "silver")),
    "tool_stake": (tool_stake, ("camel", "parchment", "linen_white")),
    "tool_gloves": (tool_gloves, ("olive", "oak_brown", "gold")),
    "tool_compost": (tool_compost, ("tan", "umber_black", "oak_brown")),
    "tool_shade": (tool_shade, ("sage", "linen_white", "oak_brown")),
    "tool_duster": (tool_duster, ("butter", "oak_brown", "gold")),
}
