"""Small models for the inventory icons of items that have no 3D use in the
game: materials, dyes, finishes, potions and patterns (pure Python, builds
`meshkit.Mesh`; `render_icons.py` renders them).

They are built like furniture (tools/blender/world/furniture_models.py):
faces carry a furniture atlas tile and a dye zone (1 primary, 2 secondary,
3 trim, 0 undyed), and the icon renderer tints them with the item's own
catalog dyes, so an icon shows the item's colours and a dye bottle the
colour of its family. Every model sits on y = 0, centred on the origin,
roughly 0.1-0.3 m across (the renderer frames each one to fill the icon).

Dispatch is by kind, then by keywords of the item's asset name (asset keys
are neutral, e.g. `icons/material/wool_skein`), so every catalog item has a
model and a new item gets a sensible one until it has its own.
"""

from __future__ import annotations

import math

import meshkit as mk
from meshkit import Mesh

P, S, T, U = 1, 2, 3, 0


class _Rand:
    def __init__(self, seed: int):
        self.s = seed * 2654435761 % 2**32 or 1

    def uniform(self, a: float, b: float) -> float:
        self.s ^= (self.s << 13) & 0xFFFFFFFF
        self.s ^= self.s >> 17
        self.s ^= (self.s << 5) & 0xFFFFFFFF
        return a + (b - a) * (self.s / 2**32)


def _seed(item: dict) -> int:
    return int(item["id"])


def _ball(m: Mesh, c, r: float, mat: str, zone: int, seg: int = 8, squash: float = 1.0) -> None:
    prof = [(r * math.sin(math.pi * i / 6), -r * squash * math.cos(math.pi * i / 6)) for i in range(7)]
    mk.lathe(m, prof, seg, mat, zone, c)


def _card(m: Mesh, pts, mat: str, zone: int, uvs=None) -> None:
    m.poly(pts, mat, uvs, zone)
    m.poly(list(reversed(pts)), mat, list(reversed(uvs)) if uvs else None, zone)


def _stem(m: Mesh, a, b, r: float, zone: int = S, mat: str = "leaf") -> None:
    mk.rod(m, a, b, r, 4, mat, zone, caps=False)


def _leaf(m: Mesh, base, direction, length: float, width: float, zone: int, up=(0.0, 1.0, 0.0)) -> None:
    d = mk.normalize(direction)
    side = mk.normalize(mk.cross(d, up))
    if mk.length(side) < 1e-6:
        side = (1.0, 0.0, 0.0)
    tip = mk.add(base, mk.scale(d, length))
    mid = mk.add(base, mk.scale(d, length * 0.45))
    pts = [base, mk.add(mid, mk.scale(side, width / 2)), tip, mk.sub(mid, mk.scale(side, width / 2))]
    _card(m, pts, "leaf", zone, [(0.5, 0.0), (1.0, 0.45), (0.5, 1.0), (0.0, 0.45)])


# ---------------------------------------------------------------------------
# Materials


def skein(item: dict) -> Mesh:
    """A twisted hank of yarn or silk: a loop of twisted strands."""
    m = Mesh()
    n, strands = 22, 3
    for k in range(strands):
        pts = []
        for i in range(n + 1):
            a = 2 * math.pi * i / n
            tw = a * 3 + k * 2 * math.pi / strands
            r = 0.09 + 0.012 * math.cos(tw)
            pts.append((r * math.cos(a), 0.035 + 0.012 * math.sin(tw), r * math.sin(a) * 0.55))
        for p0, p1 in zip(pts, pts[1:]):
            mk.rod(m, p0, p1, 0.018, 5, "fabric", P if k != 1 else S, caps=False)
    mk.rod(m, (-0.02, 0.03, 0.05), (0.02, 0.06, 0.05), 0.012, 5, "fabric", T)
    return m


def spool(item: dict) -> Mesh:
    """A wooden spool wound with thread."""
    m = Mesh()
    mk.lathe(m, [(0.04, 0.0), (0.045, 0.012), (0.03, 0.016)], 12, "wood", U, cap_top=False, cap_bottom=True)
    mk.lathe(m, [(0.062, 0.016), (0.07, 0.03), (0.07, 0.15), (0.062, 0.165)], 14, "fabric", P, cap_top=False)
    mk.lathe(m, [(0.03, 0.165), (0.045, 0.168), (0.04, 0.18)], 12, "wood", U, cap_top=True)
    for i in range(10):
        y = 0.03 + i * 0.013
        mk.lathe(m, [(0.0705, y), (0.0705, y + 0.004)], 14, "fabric", S, cap_top=False)
    mk.rod(m, (0.07, 0.12, 0.0), (0.11, 0.04, 0.03), 0.004, 4, "fabric", P)
    return m


def feather(item: dict) -> Mesh:
    """A long feather lying on its side: quill, barbed vane, curled tip."""
    m = Mesh()
    rng = _Rand(_seed(item))
    n = 12
    spine = [(-0.15 + 0.3 * i / n, 0.02 + 0.03 * math.sin(math.pi * i / n), 0.02 * math.sin(2.5 * i / n)) for i in range(n + 1)]
    mk.rod(m, (-0.2, 0.012, 0.0), spine[0], 0.006, 5, "metal", T)
    for i in range(n):
        a, b = spine[i], spine[i + 1]
        t = (i + 0.5) / n
        w = 0.05 * math.sin(math.pi * min(1.0, t * 1.15)) ** 0.7
        for s in (1, -1):
            notch = rng.uniform(0.0, 0.012) if i % 3 == 0 else 0.0
            pa = (a[0] - 0.01, a[1] - 0.006, a[2] + s * (w - notch))
            pb = (b[0] - 0.01, b[1] - 0.006, b[2] + s * w)
            zone = P if (i + (s > 0)) % 4 else S
            _card(m, [a, b, pb, pa], "fabric", zone)
    return m


def fluff(item: dict) -> Mesh:
    """A soft ball of fluff or a tuft of fur tied with a thread."""
    m = Mesh()
    rng = _Rand(_seed(item))
    for k in range(9):
        a = 2 * math.pi * k / 9
        r = 0.045 + rng.uniform(0.0, 0.02)
        c = (0.04 * math.cos(a), 0.072 + rng.uniform(-0.005, 0.02), 0.04 * math.sin(a))
        _ball(m, c, r, "fabric", P if k % 3 else S, 7)
    _ball(m, (0.0, 0.09, 0.0), 0.06, "fabric", P, 8)
    mk.lathe(m, [(0.05, 0.05), (0.052, 0.058)], 10, "fabric", T, cap_top=False)
    return m


def hide(item: dict) -> Mesh:
    """A folded piece of leather or scaled hide, tied with a strap."""
    m = Mesh()
    scaled = "dragon" in item["asset"]
    w, d = 0.24, 0.16
    for layer in range(3):
        y = 0.012 + layer * 0.016
        inset = layer * 0.008
        mk.box(m, (-w / 2 + inset, y - 0.012, -d / 2 + inset), (w / 2 - inset, y, d / 2 - inset), "leather", P, skip=("-y",))
    if scaled:
        for i in range(6):
            for j in range(4):
                cx = -w / 2 + 0.03 + i * 0.037 + (0.018 if j % 2 else 0.0)
                cz = -d / 2 + 0.03 + j * 0.034
                if cx > w / 2 - 0.02:
                    continue
                pts = [(cx - 0.016, 0.049, cz - 0.01), (cx + 0.016, 0.049, cz - 0.01), (cx, 0.052, cz + 0.016)]
                m.poly(pts if mk.newell(pts)[1] > 0 else list(reversed(pts)), "leather", zone=S if (i + j) % 2 else P)
    mk.box(m, (-0.018, 0.0, -d / 2 - 0.004), (0.018, 0.05, d / 2 + 0.004), "leather", T)
    return m


def swatch(item: dict) -> Mesh:
    """A folded fabric scrap with a frayed edge (velvet and the like)."""
    m = Mesh()
    w, d = 0.22, 0.16
    for k, (y, ins) in enumerate(((0.008, 0.0), (0.02, 0.012), (0.032, 0.024))):
        mk.box(m, (-w / 2 + ins, y - 0.008, -d / 2 + ins * 0.6), (w / 2 - ins, y, d / 2 - ins * 0.6), "fabric", P if k != 1 else S,
               skip=("-y",))
    for i in range(8):
        x = -w / 2 + 0.01 + i * (w - 0.02) / 7
        _card(m, [(x, 0.032, d / 2 - 0.014), (x + 0.008, 0.032, d / 2 - 0.014), (x + 0.004, 0.03, d / 2 + 0.006)], "fabric", T)
    return m


def roots(item: dict) -> Mesh:
    """A bundle of gnarled roots (or a knobbly rhizome) tied with twine."""
    m = Mesh()
    rng = _Rand(_seed(item))
    knobbly = "ginger" in item["asset"]
    for k in range(5 if not knobbly else 3):
        a = rng.uniform(-0.4, 0.4)
        y = 0.03 + k * 0.012
        p0 = (-0.13, y, -0.03 + k * 0.015)
        pts = [p0]
        for i in range(1, 6):
            pts.append((p0[0] + 0.26 * i / 5, y + 0.01 * math.sin(i * 1.7 + k), p0[2] + 0.03 * math.sin(i + a)))
        for i, (q0, q1) in enumerate(zip(pts, pts[1:])):
            r = (0.022 if knobbly else 0.012) * (1.0 - 0.12 * i)
            mk.rod(m, q0, q1, r, 6, "wood", P)
            if knobbly and i % 2 == 0:
                _ball(m, q1, r * 1.5, "wood", P, 6)
    mk.lathe(m, [(0.035, 0.0), (0.035, 0.012)], 8, "fabric", T, (-0.03, 0.02, 0.0), cap_top=False)
    return m


def sprig(item: dict) -> Mesh:
    """A cut sprig: a stem with leaves and a head of small flowers (weld,
    valerian, moonflower), or a bunch of broad leaves (woad, moonknot)."""
    m = Mesh()
    name = item["asset"]
    rng = _Rand(_seed(item))
    broad = any(w in name for w in ("woad", "moonknot"))
    stems = 3
    for k in range(stems):
        lean = (k - 1) * 0.05
        base = (-0.03 + 0.03 * k, 0.005, 0.0)
        top = (base[0] + lean, 0.22 - 0.02 * abs(k - 1), 0.0)
        _stem(m, base, top, 0.004)
        for j in range(4):
            t = 0.25 + j * 0.17
            p = mk.lerp(base, top, t)
            yaw = j * 2.2 + k
            d = (math.cos(yaw), 0.6, math.sin(yaw))
            _leaf(m, p, d, 0.07 if broad else 0.045, 0.035 if broad else 0.016, S)
        if broad and k == 1:  # the bunch is tied with a twine of the colour it dyes
            mk.lathe(m, [(0.022, 0.05), (0.024, 0.065)], 8, "fabric", P, (0.0, 0.0, 0.0), cap_top=False)
        if not broad:
            for f in range(7):
                a = 2 * math.pi * f / 7
                c = mk.add(top, (0.014 * math.cos(a), 0.008 + rng.uniform(0.0, 0.012), 0.014 * math.sin(a)))
                _ball(m, c, 0.009, "fabric", P, 5)
    return m


def berries(item: dict) -> Mesh:
    """A cluster of berries, hips, galls or nuts on a twig."""
    m = Mesh()
    name = item["asset"]
    rng = _Rand(_seed(item))
    big = any(w in name for w in ("walnut", "galls", "fig", "rose"))
    _stem(m, (-0.12, 0.03, 0.0), (0.1, 0.06, 0.02), 0.005, S, "wood")
    count = 6 if big else 18
    for k in range(count):
        t = rng.uniform(0.15, 0.95)
        c = (-0.12 + 0.22 * t + rng.uniform(-0.03, 0.03), 0.03 + rng.uniform(0.0, 0.05), rng.uniform(-0.05, 0.05))
        r = rng.uniform(0.022, 0.032) if big else rng.uniform(0.01, 0.014)
        _ball(m, c, r, "fabric" if not big else "leather", P if k % 5 else T, 6, squash=1.25 if "rose" in name else 1.0)
    for k in range(3):
        _leaf(m, (-0.05 + 0.06 * k, 0.05, 0.0), (0.4, 0.5, 0.8 if k % 2 else -0.8), 0.06, 0.03, S)
    return m


def wrinkled_fig(item: dict) -> Mesh:
    """A shrivelled fig: a wrinkled teardrop with a stalk."""
    m = Mesh()
    prof = [(0.0, 0.0), (0.05, 0.015), (0.07, 0.05), (0.06, 0.09), (0.03, 0.12), (0.008, 0.135), (0.0, 0.14)]
    mk.lathe(m, prof, 10, "leather", P)
    for k in range(5):
        a = 2 * math.pi * k / 5
        _card(m, [(0.066 * math.sin(a), 0.03, 0.066 * math.cos(a)), (0.07 * math.sin(a + 0.15), 0.06, 0.07 * math.cos(a + 0.15)),
                  (0.05 * math.sin(a), 0.1, 0.05 * math.cos(a))], "leather", S)
    _stem(m, (0.0, 0.135, 0.0), (0.02, 0.17, 0.0), 0.005, T, "wood")
    return m


def mushroom(item: dict) -> Mesh:
    """A glowing mushroom: pale stalk, a spotted cap (lit in its own colour
    by render_icons.py)."""
    m = Mesh()
    mk.lathe(m, [(0.02, 0.0), (0.018, 0.06), (0.016, 0.1)], 8, "fabric", S, cap_top=False, cap_bottom=True)
    mk.lathe(m, [(0.016, 0.095), (0.07, 0.1), (0.075, 0.115), (0.06, 0.14), (0.03, 0.158), (0.0, 0.162)], 12, "fabric", P)
    for k in range(6):
        a = 2 * math.pi * k / 6
        _ball(m, (0.045 * math.sin(a), 0.14, 0.045 * math.cos(a)), 0.008, "fabric", T, 5)
    return m


def jar(item: dict) -> Mesh:
    """A stoppered jar of something small (flies, eyes): glass, contents,
    cork and a parchment label."""
    m = Mesh()
    name = item["asset"]
    mk.lathe(m, [(0.05, 0.0), (0.055, 0.01), (0.055, 0.13), (0.035, 0.15), (0.035, 0.165)], 12, "glass", U, cap_top=False,
             cap_bottom=True)
    mk.cylinder(m, (0.0, 0.16, 0.0), 0.038, 0.025, 10, "wood", T)
    rng = _Rand(_seed(item))
    for k in range(10):
        c = (rng.uniform(-0.03, 0.03), 0.02 + rng.uniform(0.0, 0.08), 0.056)
        if "eye" in name:
            _ball(m, (c[0], c[1], 0.05), 0.012, "linen", U, 5)
            _ball(m, (c[0], c[1], 0.058), 0.006, "fabric", P, 4)
        else:
            _card(m, [(c[0], c[1], c[2]), (c[0] + 0.012, c[1] + 0.006, c[2]), (c[0] + 0.004, c[1] + 0.012, c[2])], "fabric", P)
    m.poly([(-0.03, 0.06, 0.0565), (0.03, 0.06, 0.0565), (0.03, 0.1, 0.0565), (-0.03, 0.1, 0.0565)], "linen",
           [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], U)
    return m


def powder(item: dict) -> Mesh:
    """A small stone bowl heaped with glittering powder."""
    m = Mesh()
    mk.lathe(m, [(0.05, 0.0), (0.08, 0.02), (0.09, 0.045), (0.082, 0.05)], 12, "metal", S, cap_top=False, cap_bottom=True)
    mk.lathe(m, [(0.082, 0.045), (0.06, 0.06), (0.03, 0.075), (0.0, 0.08)], 12, "fabric", P)
    rng = _Rand(_seed(item))
    for k in range(6):
        x, z = rng.uniform(-0.04, 0.04), rng.uniform(-0.04, 0.04)
        y = 0.08 - 0.3 * (x * x + z * z) ** 0.5
        for yaw in (0.0, math.pi / 2):
            pts = [(0.0, 0.008, 0.0), (0.003, 0.0, 0.0), (0.0, -0.008, 0.0), (-0.003, 0.0, 0.0)]
            q = [mk.add(mk.rot_y(p, yaw), (x, y + 0.01, z)) for p in pts]
            _card(m, q, "fabric", T)
    return m


def eggshell(item: dict) -> Mesh:
    """Curved fragments of a pearly eggshell."""
    m = Mesh()
    for k, (x, z, yaw) in enumerate(((0.0, 0.0, 0.0), (0.07, 0.04, 1.2), (-0.06, 0.05, 2.5))):
        mark = m.mark()
        prof = [(0.0, 0.0), (0.04, 0.008), (0.06, 0.03), (0.062, 0.045)]
        mk.lathe(m, prof, 6, "fabric", P if k != 1 else S, cap_top=False)
        # Keep a jagged fragment: drop the far half.
        m.faces = m.faces[:mark[1]] + [f for f in m.faces[mark[1]:] if m.face_points(f)[0][2] > -0.01]
        m.place(mark, yaw, (x, 0.0, z))
    return m


def egg(item: dict) -> Mesh:
    """An egg, glowing from within (the cinderworm's)."""
    m = Mesh()
    prof = [(0.0, 0.0), (0.045, 0.012), (0.058, 0.05), (0.05, 0.095), (0.03, 0.125), (0.0, 0.135)]
    mk.lathe(m, prof, 12, "flame", U)
    for k in range(4):
        a = 2 * math.pi * k / 4
        _card(m, [(0.055 * math.sin(a), 0.03, 0.055 * math.cos(a)), (0.058 * math.sin(a + 0.2), 0.06, 0.058 * math.cos(a + 0.2)),
                  (0.05 * math.sin(a), 0.09, 0.05 * math.cos(a))], "fabric", P)
    mk.lathe(m, [(0.07, 0.0), (0.075, 0.01), (0.06, 0.012)], 12, "dark", U, cap_top=True)
    return m


def hank_hair(item: dict) -> Mesh:
    """A silky hank of fine hair (Veilape), tied in the middle."""
    m = Mesh()
    for k in range(9):
        z = -0.03 + k * 0.0075
        pts = [(-0.14 + 0.28 * i / 8, 0.02 + 0.03 * math.sin(math.pi * i / 8) + 0.004 * math.sin(i + k), z) for i in range(9)]
        for p0, p1 in zip(pts, pts[1:]):
            mk.rod(m, p0, p1, 0.005, 4, "fabric", P if k % 3 else S, caps=False)
    mk.lathe(m, [(0.03, 0.02), (0.03, 0.065)], 8, "fabric", T, (0.0, 0.0, 0.0), cap_top=False)
    return m


def material_model(item: dict) -> Mesh:
    name = item["asset"].rsplit("/", 1)[-1]
    sub = item["exchange"]["subcategory"]
    if sub == "fur" and "hide" not in name:
        return fluff(item)
    if "thread" in name:
        return spool(item)
    if "skein" in name or ("silk" in name and sub == "fibers"):
        return skein(item)
    if "scrap" in name:
        return hide(item) if "dragon" in name else swatch(item)
    if sub == "feathers":
        return feather(item)
    if "hide" in name:
        return hide(item)
    if sub == "fur":
        return fluff(item)
    if "hair" in name:
        return hank_hair(item)
    if "fig" in name:
        return wrinkled_fig(item)
    if "mushroom" in name:
        return mushroom(item)
    if "eggshell" in name:
        return eggshell(item)
    if name.endswith("egg"):
        return egg(item)
    if "dust" in name:
        return powder(item)
    if any(w in name for w in ("flies", "eyes")):
        return jar(item)
    if any(w in name for w in ("root",)):
        return roots(item)
    if any(w in name for w in ("berries", "hulls", "galls", "hips")):
        return berries(item)
    return sprig(item)


# ---------------------------------------------------------------------------
# Dyes, finishes and potions


def _bottle(m: Mesh, prof_glass, liquid_top: float, cork_y: float, cork_r: float, seg: int = 12, liquid=("metal", P),
            glow: bool = False) -> None:
    """A bottle: a lathe profile of glass (undyed), the liquid inside (a
    slightly smaller lathe up to `liquid_top`), and a cork."""
    def radius_at(y: float) -> float:
        for (r0, y0), (r1, y1) in zip(prof_glass, prof_glass[1:]):
            if y0 <= y <= y1 and y1 > y0:
                return r0 + (r1 - r0) * (y - y0) / (y1 - y0)
        return prof_glass[-1][0]

    r_top = radius_at(liquid_top)
    inner = [(max(r - 0.004, 0.0), y) for r, y in prof_glass if y < liquid_top]
    if inner:
        inner.append((max(r_top - 0.004, 0.0), liquid_top))
        mk.lathe(m, inner, seg, "brew" if glow else liquid[0], U if glow else liquid[1], cap_top=True, cap_bottom=True)
    # The glass from the liquid line up (it starts exactly where the liquid stops).
    above = [(r_top, liquid_top)] + [(r, y) for r, y in prof_glass if y > liquid_top + 1e-6]
    if len(above) >= 2:
        mk.lathe(m, above, seg, "glass", U, cap_top=False)
    mk.lathe(m, [(cork_r, cork_y), (cork_r * 1.05, cork_y + 0.02), (cork_r * 0.9, cork_y + 0.03)], 8, "dark", U, cap_top=True)


def dye_bottle(item: dict) -> Mesh:
    """A square-shouldered dye bottle, full of its colour, with a wax-sealed
    cork and a parchment label."""
    m = Mesh()
    prof = [(0.05, 0.0), (0.055, 0.01), (0.055, 0.12), (0.03, 0.15), (0.018, 0.16), (0.018, 0.18)]
    _bottle(m, prof, 0.13, 0.175, 0.02, seg=4)
    # Turn the square bottle so a flat side (and its label) faces front.
    m.transform((0, 0), lambda p: mk.rot_y(p, math.pi / 4))
    m.poly([(-0.034, 0.035, 0.0395), (0.034, 0.035, 0.0395), (0.034, 0.09, 0.0395), (-0.034, 0.09, 0.0395)], "linen",
           [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], U)
    mk.lathe(m, [(0.024, 0.195), (0.026, 0.205), (0.0, 0.21)], 8, "leather", T)
    if "starlight" in item["asset"]:  # The night dye's label carries a star in its trim colour
        _star(m, (0.0, 0.0625, 0.0405), 0.022, T, "metal")
    return m


def _star(m: Mesh, c, r: float, zone: int, mat: str, flat: bool = False, points: int = 5) -> None:
    """A star of `points` points round `c` (a fan of triangles: it is
    concave), facing +z, or lying flat facing +y when `flat` (on a sheet,
    x right, z down it)."""
    ring = []
    for k in range(2 * points):
        a = math.pi / 2 + k * math.pi / points
        rr = r if k % 2 == 0 else r * 0.45
        ring.append((rr * math.cos(a), rr * math.sin(a)))
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if flat:
            tri = [c, (c[0] + x0, c[1], c[2] - y0), (c[0] + x1, c[1], c[2] - y1)]
            normal_up = mk.newell(tri)[1] > 0
        else:
            tri = [c, (c[0] + x0, c[1] + y0, c[2]), (c[0] + x1, c[1] + y1, c[2])]
            normal_up = mk.newell(tri)[2] > 0
        m.poly(tri if normal_up else list(reversed(tri)), mat, zone=zone)


def finish_jar(item: dict) -> Mesh:
    """A squat lidded jar of lacquer or varnish, its lid swirled with the
    finish's colours."""
    m = Mesh()
    shift = "shift" in item["asset"]
    mk.lathe(m, [(0.06, 0.0), (0.07, 0.01), (0.072, 0.07), (0.06, 0.085)], 12, "glass", U, cap_top=False, cap_bottom=True)
    mk.lathe(m, [(0.066, 0.005), (0.066, 0.068)], 12, "metal", P, cap_top=True)
    mk.lathe(m, [(0.066, 0.085), (0.07, 0.09), (0.07, 0.11), (0.06, 0.115)], 12, "metal", S, cap_top=True)
    for k in range(6 if shift else 4):
        a = 2 * math.pi * k / (6 if shift else 4)
        pts = [(0.0, 0.116, 0.0), (0.06 * math.sin(a), 0.116, 0.06 * math.cos(a)),
               (0.06 * math.sin(a + 0.6), 0.116, 0.06 * math.cos(a + 0.6))]
        m.poly(pts if mk.newell(pts)[1] > 0 else list(reversed(pts)), "metal", zone=T if k % 2 else P)
    for k in range(5):
        a = 1.3 * k
        x, z = 0.05 * math.sin(a), 0.05 * math.cos(a)
        for yaw in (0.0, math.pi / 2):
            pts = [(0.0, 0.012, 0.0), (0.004, 0.0, 0.0), (0.0, -0.012, 0.0), (-0.004, 0.0, 0.0)]
            q = [mk.add(mk.rot_y(p, yaw), (x * 1.5, 0.13 + 0.01 * k, z * 1.5)) for p in pts]
            _card(m, q, "flame", U)
    return m


# Bottle profiles per potion effect: distinct silhouettes at icon size.
POTION_SHAPES = {
    "glow": [(0.0, 0.0), (0.05, 0.005), (0.075, 0.05), (0.07, 0.1), (0.04, 0.13), (0.02, 0.15), (0.02, 0.18)],
    "chameleon": [(0.035, 0.0), (0.04, 0.01), (0.045, 0.06), (0.032, 0.11), (0.045, 0.16), (0.02, 0.2), (0.016, 0.23)],
    "shrinking": [(0.022, 0.0), (0.025, 0.01), (0.025, 0.09), (0.012, 0.11), (0.012, 0.13)],
    "swelling": [(0.0, 0.0), (0.06, 0.005), (0.09, 0.05), (0.092, 0.1), (0.07, 0.14), (0.026, 0.16), (0.024, 0.19)],
    "babbling": [(0.04, 0.0), (0.05, 0.02), (0.05, 0.08), (0.03, 0.11), (0.015, 0.14), (0.015, 0.2)],
    "mimic_draught": [(0.05, 0.0), (0.055, 0.01), (0.055, 0.11), (0.04, 0.14), (0.022, 0.15), (0.022, 0.17)],
    "fortunes_draught": [(0.0, 0.0), (0.03, 0.005), (0.04, 0.03), (0.035, 0.07), (0.015, 0.1), (0.012, 0.13)],
    "pick_me_up_tonic": [(0.05, 0.0), (0.055, 0.01), (0.04, 0.09), (0.02, 0.13), (0.02, 0.16)],
    "invisibility": [(0.04, 0.0), (0.045, 0.01), (0.03, 0.16), (0.015, 0.18), (0.015, 0.2)],
    # A tall,
    # slender flask, light as a feather.
    "featherlight": [(0.0, 0.0), (0.035, 0.005), (0.045, 0.04), (0.03, 0.12), (0.014, 0.17), (0.014, 0.22)],
}


def _feather(m: Mesh, base, length: float) -> None:
    """A feather rising from `base`, leaning a little: a quill
    and a vane of two cards each side (untinted linen)."""
    tip = (base[0] + length * 0.28, base[1] + length, base[2])
    mk.rod(m, base, tip, 0.0025, 4, "dark", U, caps=False)
    n = 6

    def along(t):
        return (base[0] + (tip[0] - base[0]) * t, base[1] + (tip[1] - base[1]) * t, base[2])

    spine = [along(0.15 + 0.85 * i / n) for i in range(n + 1)]
    for side in (-1.0, 1.0):
        edge = []
        for i in range(n + 1):
            t = 0.15 + 0.85 * i / n
            w = 0.022 * math.sin(math.pi * min(1.0, t * 1.1)) * (1.0 if side > 0 else 0.8)
            q = along(t)
            edge.append((q[0] + side * w * 0.96, q[1] - side * w * 0.27, q[2]))
        for i in range(n):
            _card(m, [spine[i], edge[i], edge[i + 1], spine[i + 1]], "linen", U)


def potion_bottle(item: dict) -> Mesh:
    """A potion in the bottle shape of its effect (round flask, twisted
    tonic, tiny vial, bulbous jar, curly-necked bottle, hexagonal crock,
    golden teardrop, steaming flask, near-empty clear bottle)."""
    m = Mesh()
    effect = item["potion"]["effect"]
    prof = POTION_SHAPES.get(effect, POTION_SHAPES["mimic_draught"])
    top = prof[-1][1]
    seg = 6 if effect == "mimic_draught" else 12
    level = {"invisibility": 0.4, "fortunes_draught": 0.72}.get(effect, 0.62)
    # (Glowing potions glow in their own colour: render_icons.py lights them.)
    _bottle(m, prof, top * level, top - 0.008, prof[-1][0] * 1.1, seg=seg)
    if effect == "babbling":  # a curly neck
        pts = [(0.015 * math.cos(a * 2), top + 0.01 + 0.012 * a, 0.015 * math.sin(a * 2)) for a in [i * 0.4 for i in range(9)]]
        for p0, p1 in zip(pts, pts[1:]):
            mk.rod(m, p0, p1, 0.007, 5, "glass", U, caps=False)
    asset = item["asset"]
    if effect == "pick_me_up_tonic" and "comet" in asset:  # A night brew: a little comet streaming from the cork
        head = (-0.012, top + 0.045, 0.0)
        _ball(m, head, 0.012, "metal", T, 8)
        for tip, w in (((0.07, top + 0.012, 0.0), 0.012), ((0.06, top + 0.04, 0.0), 0.007)):
            d = mk.normalize(mk.sub(tip, head))
            side = (-d[1] * w, d[0] * w, 0.0)
            _card(m, [mk.add(head, side), mk.sub(head, side), tip], "linen", U)
    elif effect == "pick_me_up_tonic":  # puffs of steam above the cork
        for k, (dx, dy, r) in enumerate(((0.0, 0.03, 0.018), (0.015, 0.055, 0.014), (-0.01, 0.075, 0.011))):
            _ball(m, (dx, top + dy, 0.0), r, "linen", U, 6)
    if "moonlit" in asset:  # A night brew: a crescent moon charm on the flask's shoulder
        c, rr, ri, cx = (0.0, top * 0.62, 0.066), 0.03, 0.022, 0.011
        angles = [math.radians(55 + 250 * i / 10) for i in range(11)]
        for a0, a1 in zip(angles, angles[1:]):
            q = [(c[0] + rr * math.cos(a0), c[1] + rr * math.sin(a0), c[2]),
                 (c[0] + cx + ri * math.cos(a0), c[1] + ri * math.sin(a0), c[2]),
                 (c[0] + cx + ri * math.cos(a1), c[1] + ri * math.sin(a1), c[2]),
                 (c[0] + rr * math.cos(a1), c[1] + rr * math.sin(a1), c[2])]
            _card(m, q, "metal", S)
    if effect == "featherlight":  # A white feather standing in the cork
        _feather(m, (0.004, top + 0.02, 0.0), 0.11)
    if effect == "fortunes_draught":  # a golden glint in the stopper
        mk.lathe(m, [(0.014, top + 0.022), (0.018, top + 0.03), (0.0, top + 0.05)], 6, "metal", S)
    if effect == "chameleon":
        part = item["potion"].get("part", "hair")
        if part == "hair":  # a little ribbon round the neck
            mk.lathe(m, [(0.034, 0.12), (0.034, 0.13)], 10, "fabric", S, cap_top=False)
        else:  # a label shaped like a tiny robe
            pts = [(-0.02, 0.02, 0.046), (0.02, 0.02, 0.046), (0.012, 0.07, 0.044), (-0.012, 0.07, 0.044)]
            m.poly(pts, "fabric", zone=S)
    if effect in ("shrinking", "swelling"):  # a tag tied to the neck
        neck = prof[-2]
        m.poly([(neck[0] + 0.004, neck[1] - 0.04, 0.0), (neck[0] + 0.04, neck[1] - 0.05, 0.0),
                (neck[0] + 0.04, neck[1] - 0.02, 0.0), (neck[0] + 0.004, neck[1] - 0.01, 0.0)], "linen",
               [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], U)
    return m


# ---------------------------------------------------------------------------
# Patterns and recipe cards


def _strokes(m: Mesh, lines, y: float, width: float = 0.004, zone: int = U, mat: str = "dark") -> None:
    """Pen strokes lying on a sheet: each line a polyline of (x, z)."""
    for line in lines:
        for (x0, z0), (x1, z1) in zip(line, line[1:]):
            dx, dz = x1 - x0, z1 - z0
            n = math.hypot(dx, dz) or 1.0
            ox, oz = -dz / n * width / 2, dx / n * width / 2
            pts = [(x0 - ox, y, z0 - oz), (x1 - ox, y, z1 - oz), (x1 + ox, y, z1 + oz), (x0 + ox, y, z0 + oz)]
            m.poly(pts if mk.newell(pts)[1] > 0 else list(reversed(pts)), mat, zone=zone)


SKETCHES = {
    # Outline sketches (x right, z down the sheet), roughly in a 0.16 m box.
    "hat": [[(-0.08, 0.04), (0.08, 0.04)], [(-0.05, 0.04), (0.0, -0.07), (0.05, 0.04)], [(-0.035, 0.015), (0.035, 0.015)]],
    "body": [[(-0.04, -0.06), (-0.075, -0.03), (-0.06, 0.0), (-0.04, -0.015), (-0.045, 0.07), (0.045, 0.07), (0.04, -0.015),
              (0.06, 0.0), (0.075, -0.03), (0.04, -0.06), (0.015, -0.05), (0.0, -0.03), (-0.015, -0.05), (-0.04, -0.06)]],
    "legs": [[(-0.04, -0.06), (0.04, -0.06), (0.05, 0.07), (0.012, 0.07), (0.0, -0.02), (-0.012, 0.07), (-0.05, 0.07),
              (-0.04, -0.06)]],
    "shoes": [[(-0.03, -0.06), (-0.03, 0.03), (0.06, 0.04), (0.07, 0.06), (-0.04, 0.06), (-0.045, -0.06), (-0.03, -0.06)]],
    "potion": [[(-0.012, -0.07), (-0.012, -0.04), (-0.045, 0.0), (-0.04, 0.05), (0.04, 0.05), (0.045, 0.0), (0.012, -0.04),
                (0.012, -0.07)], [(-0.035, 0.01), (0.035, 0.01)]],
    # The Astronomy class's recipe cards, an invented constellation in a chart's
    # circle (its stars drawn by pattern_sheet at STAR_CHART_STARS).
    "star_chart": [[(-0.055, 0.045), (-0.025, 0.012), (0.004, 0.028), (0.032, -0.018), (0.06, -0.04)],
                   [(0.004, 0.028), (0.018, 0.06)], [(-0.025, 0.012), (-0.04, -0.035), (-0.005, -0.06)]],
}
STAR_CHART_STARS = [(-0.055, 0.045, 0.009), (-0.025, 0.012, 0.007), (0.004, 0.028, 0.011), (0.032, -0.018, 0.008),
                    (0.06, -0.04, 0.01), (0.018, 0.06, 0.007), (-0.04, -0.035, 0.008), (-0.005, -0.06, 0.009)]
STAR_CHART_RING = 0.088  # the chart's circle round the sketch


def pattern_sheet(item: dict) -> Mesh:
    """A sheet of parchment with curled ends, a pen sketch of what it
    teaches (garment outline, or a bottle for recipe cards), measurement
    ticks and a wax seal in the item's trim colour."""
    m = Mesh()
    w, h = 0.2, 0.26
    n = 8
    # The sheet, curling up at the top and bottom edges.
    rows = []
    for i in range(n + 1):
        t = i / n
        z = -h / 2 + h * t
        curl = 0.03 * (max(0.0, abs(t - 0.5) - 0.38) / 0.12) ** 2
        rows.append((z, 0.004 + curl))
    for (z0, y0), (z1, y1) in zip(rows, rows[1:]):
        pts = [(-w / 2, y0, z0), (w / 2, y0, z0), (w / 2, y1, z1), (-w / 2, y1, z1)]
        if mk.newell(pts)[1] < 0:
            pts.reverse()
        m.poly(pts, "metal", zone=P)
        m.poly(list(reversed(pts)), "metal", zone=P)
    slot = item["asset"].rsplit("/", 1)[-1]
    sketch = SKETCHES.get(slot, SKETCHES["body"])
    if slot == "star_chart":  # Thin constellation lines, stars and the chart's circle
        _strokes(m, sketch, 0.0055, 0.0025, S, "metal")
        for x, z, r in STAR_CHART_STARS:
            _star(m, (x, 0.0062, z), r, T, "metal", flat=True, points=4)
        k = 24
        ring = [(STAR_CHART_RING * math.cos(2 * math.pi * i / k), STAR_CHART_RING * math.sin(2 * math.pi * i / k))
                for i in range(k + 1)]
        _strokes(m, [ring], 0.0055, 0.002, S, "metal")
    else:
        _strokes(m, sketch, 0.0055, 0.004, S, "metal")
    ticks = [[(-w / 2 + 0.02, -h / 2 + 0.05 + 0.02 * k), (-w / 2 + 0.035, -h / 2 + 0.05 + 0.02 * k)] for k in range(6)]
    _strokes(m, ticks, 0.0055, 0.002, S, "metal")
    lines = [[(0.0, 0.085 + 0.012 * k), (0.07 - 0.015 * (k % 2), 0.085 + 0.012 * k)] for k in range(3)]
    _strokes(m, lines, 0.0055, 0.002, S, "metal")
    mk.lathe(m, [(0.02, 0.0), (0.022, 0.004), (0.016, 0.008), (0.0, 0.009)], 10, "leather", T, (0.06, 0.004, 0.09))
    return m


BUILDERS = {"material": material_model, "dye": dye_bottle, "finish": finish_jar, "potion": potion_bottle,
            "pattern": pattern_sheet}


def build(item: dict) -> Mesh:
    """The icon model for a catalog item of a kind with no 3D asset, resting
    on y = 0."""
    fn = BUILDERS.get(item["kind"])
    if fn is None:
        raise KeyError(f"no icon model for {item['kind']} item {item['id']}")
    m = fn(item)
    low = m.bounds()[0][1]
    if abs(low) > 1e-9:
        m.transform((0, 0), lambda p: (p[0], p[1] - low, p[2]))
    return m


KINDS = ("material", "dye", "finish", "potion", "pattern")


# ---------------------------------------------------------------------------
# The Transfiguration class icons (`icons/class/<name>`,
# data/classes/picks.json): the lesson's everyday objects and its four kinds
# of change. Not catalog items: each has its own dyes, palette.json colour
# ids (primary, secondary, trim), so the icon renderer tints them as it
# tints an item. Invented, plain objects; the change icons are simple
# symbols (two shapes and an arrow), no text.


def _arrow(m: Mesh, x0: float, x1: float, y: float, zone: int = T, w: float = 0.022) -> None:
    """A flat arrow pointing +x from x0 to x1 at height y (both faces)."""
    head = min(0.05, (x1 - x0) * 0.45)
    pts = [(x0, y - w / 2, 0.0), (x1 - head, y - w / 2, 0.0), (x1 - head, y - w * 1.6, 0.0), (x1, y, 0.0),
           (x1 - head, y + w * 1.6, 0.0), (x1 - head, y + w / 2, 0.0), (x0, y + w / 2, 0.0)]
    mk.prism(m, [(p[0], p[1]) for p in pts], -0.006, 0.006, "metal", zone)


def class_teacup() -> Mesh:
    m = Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.1, 0.0), (0.11, 0.012), (0.09, 0.014), (0.0, 0.01)], 16, "linen", P, cap_top=False)
    mk.lathe(m, [(0.0, 0.014), (0.035, 0.014), (0.06, 0.04), (0.07, 0.085), (0.072, 0.09)], 16, "linen", P, cap_top=False)
    mk.lathe(m, [(0.072, 0.09), (0.064, 0.086), (0.0, 0.078)], 16, "fabric", S, cap_top=False)
    mk.lathe(m, [(0.071, 0.084), (0.073, 0.088), (0.073, 0.091), (0.071, 0.092)], 16, "metal", T, cap_top=False)
    for k in range(7):
        a0, a1 = math.pi * (k - 3) / 7, math.pi * (k - 2) / 7
        p0 = (0.07 + 0.03 * math.cos(a0), 0.055 + 0.022 * math.sin(a0), 0.0)
        p1 = (0.07 + 0.03 * math.cos(a1), 0.055 + 0.022 * math.sin(a1), 0.0)
        mk.rod(m, p0, p1, 0.006, 5, "linen", P)
    return m


def class_key() -> Mesh:
    m = Mesh()
    mk.box(m, (-0.11, 0.0, -0.008), (0.05, 0.016, 0.008), "metal", P)
    for x0, depth in ((-0.11, 0.045), (-0.08, 0.03)):
        mk.box(m, (x0, 0.0, 0.008), (x0 + 0.02, 0.016, 0.008 + depth), "metal", P)
    seg = 12
    for k in range(seg):
        a0, a1 = 2 * math.pi * k / seg, 2 * math.pi * (k + 1) / seg
        c = (0.09, 0.008, 0.0)
        mk.rod(m, (c[0] + 0.04 * math.cos(a0), c[1], 0.04 * math.sin(a0)), (c[0] + 0.04 * math.cos(a1), c[1], 0.04 * math.sin(a1)),
               0.011, 5, "metal", P)
    return m


def class_quill() -> Mesh:
    m = Mesh()
    def on_shaft(x: float) -> float:  # the shaft's height at x
        return 0.2 * (x + 0.12)

    mk.rod(m, (-0.12, 0.0, 0.0), (-0.08, on_shaft(-0.08), 0.0), 0.005, 6, "metal", T)  # the nib
    mk.rod(m, (-0.08, on_shaft(-0.08), 0.0), (0.13, on_shaft(0.13), 0.0), 0.004, 6, "metal", S)
    n = 10
    for i in range(n):
        t0, t1 = i / n, (i + 1) / n
        x0, x1 = -0.07 + 0.2 * t0, -0.07 + 0.2 * t1
        w0 = 0.07 * math.sin(math.pi * min(1.0, 0.08 + t0 * 0.95))
        w1 = 0.07 * math.sin(math.pi * min(1.0, 0.08 + t1 * 0.95))
        for sgn in (-1, 1):
            q = [(x0, on_shaft(x0), 0.0), (x1, on_shaft(x1), 0.0), (x1 - 0.015, on_shaft(x1) + 0.12 * w1, sgn * w1),
                 (x0 - 0.015, on_shaft(x0) + 0.12 * w0, sgn * w0)]
            _card(m, q, "fabric", P)
    return m


def class_candle() -> Mesh:
    m = Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.08, 0.0), (0.085, 0.012), (0.03, 0.02), (0.025, 0.035)], 14, "metal", S, cap_top=True)
    mk.cylinder(m, (0.0, 0.035, 0.0), 0.022, 0.16, 12, "wax", P)
    mk.lathe(m, [(0.0, 0.0), (0.014, 0.014), (0.01, 0.035), (0.0, 0.055)], 8, "flame", U, (0.0, 0.198, 0.0), cap_top=False)
    return m


def class_spoon() -> Mesh:
    m = Mesh()
    mark = m.mark()
    mk.lathe(m, [(0.0, 0.0), (0.03, 0.004), (0.04, 0.014), (0.042, 0.018)], 12, "metal", P, cap_top=False)
    m.transform(mark, lambda p: (p[0] * 1.35, p[1], p[2]))
    mk.box(m, (0.05, 0.012, -0.008), (0.2, 0.02, 0.008), "metal", P)
    mk.box(m, (0.17, 0.012, -0.012), (0.21, 0.02, 0.012), "metal", P)
    return m


def class_button() -> Mesh:
    m = Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.08, 0.0), (0.085, 0.012), (0.075, 0.02), (0.05, 0.016), (0.0, 0.016)], 18, "leather", P,
             cap_top=False)
    for dx, dz in ((-0.018, -0.018), (0.018, -0.018), (-0.018, 0.018), (0.018, 0.018)):
        mk.cylinder(m, (dx, 0.0165, dz), 0.008, 0.001, 8, "dark", U)
    return m


def class_matchbox() -> Mesh:
    m = Mesh()
    mk.box(m, (-0.1, 0.0, -0.06), (0.1, 0.05, 0.06), "fabric", P)
    mk.box(m, (-0.104, 0.012, -0.064), (0.104, 0.038, 0.064), "linen", S, skip=("-y", "+y"))
    mk.box(m, (0.1, 0.004, -0.055), (0.17, 0.046, 0.055), "wood", T)
    for k in range(4):
        z = -0.04 + k * 0.026
        mk.box(m, (0.12, 0.046, z - 0.004), (0.165, 0.054, z + 0.004), "wood", T)
        _ball(m, (0.165, 0.05, z), 0.007, "fabric", S, 6)
    return m


def class_pincushion() -> Mesh:
    m = Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.075, 0.0), (0.08, 0.02)], 14, "metal", T, cap_top=False)
    _ball(m, (0.0, 0.05, 0.0), 0.075, "fabric", P, 14, squash=0.6)
    for k in range(7):
        a = 2.4 * k
        r = 0.03 + 0.01 * (k % 3)
        base = (r * math.cos(a), 0.07, r * math.sin(a))
        tip = (1.5 * r * math.cos(a), 0.12, 1.5 * r * math.sin(a))
        mk.rod(m, base, tip, 0.002, 4, "metal", S)
        _ball(m, tip, 0.008, "fabric", T, 6)
    return m


def class_change_size() -> Mesh:
    m = Mesh()
    mk.box(m, (-0.2, 0.0, -0.03), (-0.14, 0.06, 0.03), "fabric", P)
    mk.box(m, (0.04, 0.0, -0.08), (0.2, 0.16, 0.08), "fabric", P)
    _arrow(m, -0.12, 0.02, 0.05)
    return m


def class_change_material() -> Mesh:
    m = Mesh()
    mk.box(m, (-0.2, 0.0, -0.06), (-0.07, 0.12, 0.06), "wood", P)
    mk.box(m, (0.07, 0.0, -0.06), (0.2, 0.12, 0.06), "metal", S)
    _arrow(m, -0.055, 0.055, 0.06)
    return m


def class_change_colour() -> Mesh:
    m = Mesh()
    _ball(m, (-0.13, 0.065, 0.0), 0.065, "fabric", P, 14)
    _ball(m, (0.13, 0.065, 0.0), 0.065, "fabric", S, 14)
    _arrow(m, -0.055, 0.055, 0.065)
    return m


def class_change_shape() -> Mesh:
    m = Mesh()
    mk.box(m, (-0.2, 0.0, -0.06), (-0.08, 0.12, 0.06), "fabric", P)
    _ball(m, (0.14, 0.065, 0.0), 0.065, "fabric", P, 14)
    _arrow(m, -0.065, 0.065, 0.06)
    return m


# ---------------------------------------------------------------------------
# Divination's ten symbols (`icons/class/sym_<symbol>`,
# picks.json `divination_symbols`, the same icon in the cup and the ball):
# simple upright silhouettes of clumped tea leaves (flat shapes and rods in
# the x-y plane, a little thickness in z, standing on y = 0, seen from the
# front like the change icons' arrows). Invented shapes; no text.

SYM_T = 0.014  # half the silhouettes' thickness


def _flat(m: Mesh, outline, zone: int = P, t: float = SYM_T) -> None:
    """A convex outline [(x, y), ...] (counter-clockwise seen from +z) as a thin slab."""
    mk.prism(m, outline, -t, t, "fabric", zone)


def _arc(m: Mesh, cx: float, cy: float, r: float, a0: float, a1: float, n: int, rad0: float, rad1: float | None = None,
         zone: int = P) -> None:
    """Rods along an arc in the x-y plane (angles in radians from +x), the
    rods' radius going from rad0 to rad1."""
    rad1 = rad0 if rad1 is None else rad1
    for k in range(n):
        t0, t1 = k / n, (k + 1) / n
        a, b = a0 + (a1 - a0) * t0, a0 + (a1 - a0) * t1
        mk.rod(m, (cx + r * math.cos(a), cy + r * math.sin(a), 0.0), (cx + r * math.cos(b), cy + r * math.sin(b), 0.0),
               rad0 + (rad1 - rad0) * (t0 + t1) / 2, 6, "fabric", zone)


def sym_key() -> Mesh:
    m = Mesh()
    _arc(m, 0.0, 0.25, 0.055, 0.0, 2 * math.pi, 12, 0.016)  # the bow
    mk.box(m, (-0.014, 0.0, -SYM_T), (0.014, 0.195, SYM_T), "fabric", P)  # the shank
    for y, w in ((0.0, 0.07), (0.045, 0.05)):  # the bit
        mk.box(m, (0.014, y, -SYM_T), (0.014 + w, y + 0.028, SYM_T), "fabric", P)
    return m


def sym_bird() -> Mesh:
    m = Mesh()  # a bird in flight seen from below: a gull's bent wings either side of the body
    _flat(m, [(-0.05, 0.13), (0.05, 0.13), (0.06, 0.15), (-0.04, 0.16)])  # the body
    _flat(m, [(0.05, 0.13), (0.1, 0.145), (0.06, 0.155)], S)  # the head
    _flat(m, [(-0.1, 0.13), (-0.05, 0.13), (-0.04, 0.16), (-0.1, 0.155)], S)  # the tail
    inner = [(-0.03, 0.13), (0.0, 0.16), (-0.1, 0.27), (-0.12, 0.22)]
    outer = [(-0.12, 0.22), (-0.1, 0.27), (-0.2, 0.31), (-0.24, 0.29)]
    for wing in (inner, outer):
        _flat(m, wing)  # the left wing
        _flat(m, [(-x, y) for x, y in reversed(wing)])  # the right wing (mirrored, wound back)
    return m


def sym_moon() -> Mesh:
    m = Mesh()  # a crescent: rods along an arc, thick in the middle, thin at the horns
    _arc(m, 0.0, 0.16, 0.13, math.radians(60), math.radians(150), 6, 0.012, 0.04)
    _arc(m, 0.0, 0.16, 0.13, math.radians(150), math.radians(210), 4, 0.04, 0.04)
    _arc(m, 0.0, 0.16, 0.13, math.radians(210), math.radians(300), 6, 0.04, 0.012)
    return m


def sym_ladder() -> Mesh:
    m = Mesh()
    for x in (-0.06, 0.06):
        mk.rod(m, (x, 0.0, 0.0), (x * 0.8, 0.32, 0.0), 0.014, 6, "fabric", P)
    for k in range(5):
        y = 0.04 + 0.065 * k
        hw = 0.06 - 0.012 * y / 0.32
        mk.rod(m, (-hw, y, 0.0), (hw, y, 0.0), 0.01, 5, "fabric", S)
    return m


def sym_anchor() -> Mesh:
    m = Mesh()
    _arc(m, 0.0, 0.31, 0.03, 0.0, 2 * math.pi, 10, 0.01)  # the ring
    mk.box(m, (-0.014, 0.05, -SYM_T), (0.014, 0.28, SYM_T), "fabric", P)  # the shank
    mk.box(m, (-0.08, 0.22, -SYM_T), (0.08, 0.245, SYM_T), "fabric", P)  # the stock
    _arc(m, 0.0, 0.13, 0.11, math.radians(195), math.radians(345), 9, 0.016)  # the arms
    for x in (-0.106, 0.106):  # the flukes
        _flat(m, [(x - 0.035, 0.1), (x + 0.035, 0.1), (x, 0.17)], S)
    return m


def sym_lantern() -> Mesh:
    m = Mesh()
    _flat(m, [(-0.07, 0.0), (0.07, 0.0), (0.07, 0.03), (-0.07, 0.03)])  # the base
    for x in (-0.055, 0.055):
        mk.box(m, (x - 0.012, 0.03, -SYM_T), (x + 0.012, 0.22, SYM_T), "fabric", P)  # the posts
    _flat(m, [(-0.04, 0.05), (0.04, 0.05), (0.04, 0.2), (-0.04, 0.2)], S, SYM_T * 0.6)  # the light
    _flat(m, [(-0.08, 0.22), (0.08, 0.22), (0.0, 0.29)])  # the roof
    _arc(m, 0.0, 0.31, 0.035, 0.0, math.pi, 6, 0.009)  # the handle
    return m


def sym_snail() -> Mesh:
    m = Mesh()
    n = 18
    for k in range(n):  # the shell: a spiral of rods thinning inward
        t0, t1 = k / n, (k + 1) / n
        pa = [(0.02 + 0.1 * (1 - t) * math.cos(4.2 * math.pi * t), 0.14 + 0.1 * (1 - t) * math.sin(4.2 * math.pi * t))
              for t in (t0, t1)]
        mk.rod(m, (pa[0][0], pa[0][1], 0.0), (pa[1][0], pa[1][1], 0.0), 0.022 * (1 - 0.6 * t0), 6, "fabric", P)
    _flat(m, [(-0.16, 0.0), (0.16, 0.0), (0.16, 0.04), (-0.13, 0.035)], S)  # the body
    _flat(m, [(-0.16, 0.0), (-0.13, 0.035), (-0.19, 0.07), (-0.21, 0.04)], S)  # the head
    for dx in (-0.01, 0.015):  # the horns
        mk.rod(m, (-0.19 + dx, 0.06, 0.0), (-0.22 + dx * 2, 0.13, 0.0), 0.006, 4, "fabric", S)
    return m


def sym_bridge() -> Mesh:
    m = Mesh()
    _flat(m, [(-0.2, 0.15), (0.2, 0.15), (0.2, 0.18), (-0.2, 0.18)])  # the deck
    _arc(m, 0.0, 0.0, 0.14, math.radians(10), math.radians(170), 10, 0.016)  # the arch
    for x in (-0.18, 0.18):  # the piers
        mk.box(m, (x - 0.02, 0.0, -SYM_T), (x + 0.02, 0.15, SYM_T), "fabric", P)
    for k in range(5):  # the railing
        x = -0.16 + 0.08 * k
        mk.rod(m, (x, 0.18, 0.0), (x, 0.23, 0.0), 0.007, 4, "fabric", S)
    mk.rod(m, (-0.18, 0.23, 0.0), (0.18, 0.23, 0.0), 0.008, 4, "fabric", S)
    return m


def sym_star() -> Mesh:
    m = Mesh()
    cy, ro, ri = 0.15, 0.15, 0.06
    # inner[k] lies between the tips k and k + 1 (counter-clockwise).
    angles = [math.pi / 2 + 2 * math.pi * (k + 0.5) / 5 for k in range(5)]
    inner = [(ri * math.cos(a), cy + ri * math.sin(a)) for a in angles]
    _flat(m, inner)  # the middle (a pentagon)
    for k in range(5):
        a = math.pi / 2 + 2 * math.pi * k / 5
        _flat(m, [inner[k - 1], (ro * math.cos(a), cy + ro * math.sin(a)), inner[k]])  # a point
    return m


def sym_acorn() -> Mesh:
    m = Mesh()
    _ball(m, (0.0, 0.1, 0.0), 0.075, "fabric", P, 12, squash=1.3)  # the nut
    mk.lathe(m, [(0.0, 0.145), (0.085, 0.15), (0.088, 0.17), (0.06, 0.195), (0.0, 0.205)], 12, "fabric", S, cap_top=False)
    mk.rod(m, (0.0, 0.2, 0.0), (0.012, 0.245, 0.0), 0.009, 5, "fabric", S)  # the stalk
    return m


# name -> (model, (primary, secondary, trim) palette.json colour ids)
CLASS_ICONS = {
    "teacup": (class_teacup, ("ivory", "chestnut", "gold")),
    "key": (class_key, ("gold", "gold", "gold")),
    "quill": (class_quill, ("linen_white", "pearl", "umber_black")),
    "candle": (class_candle, ("cream", "bronze", "bronze")),
    "spoon": (class_spoon, ("silver", "silver", "silver")),
    "button": (class_button, ("crimson", "crimson", "crimson")),
    "matchbox": (class_matchbox, ("cobalt", "parchment", "tan")),
    "pincushion": (class_pincushion, ("scarlet", "silver", "gold")),
    "change_size": (class_change_size, ("sky", "sky", "gold")),
    "change_material": (class_change_material, ("tan", "silver", "gold")),
    "change_colour": (class_change_colour, ("scarlet", "cobalt", "gold")),
    "change_shape": (class_change_shape, ("emerald", "emerald", "gold")),
    # Divination's ten symbols (`divination_symbols`, shown in the
    # cup and the ball): Rooms A's silhouettes (sym_* above) in tea-leaf browns.
    "sym_key": (sym_key, ("chestnut", "umber_black", "umber_black")),
    "sym_bird": (sym_bird, ("chestnut", "umber_black", "umber_black")),
    "sym_moon": (sym_moon, ("chestnut", "umber_black", "umber_black")),
    "sym_ladder": (sym_ladder, ("chestnut", "umber_black", "umber_black")),
    "sym_anchor": (sym_anchor, ("chestnut", "umber_black", "umber_black")),
    "sym_lantern": (sym_lantern, ("chestnut", "umber_black", "umber_black")),
    "sym_snail": (sym_snail, ("chestnut", "umber_black", "umber_black")),
    "sym_bridge": (sym_bridge, ("chestnut", "umber_black", "umber_black")),
    "sym_star": (sym_star, ("chestnut", "umber_black", "umber_black")),
    "sym_acorn": (sym_acorn, ("chestnut", "umber_black", "umber_black")),
}

# ---- Herbology's four plants (`herbology_plants`
# prompts) and eight tools (`herbology_tools` options); their models are in
# icon_models_herbology.py.
from icon_models_herbology import HERBOLOGY_ICONS  # noqa: E402

CLASS_ICONS.update(HERBOLOGY_ICONS)


def build_class_icon(name: str) -> Mesh:
    """A class icon's model resting on y = 0."""
    m = (CLASS_ICONS.get(name) or FLYING_ICONS[name])[0]()  # The Flying icons too
    low = m.bounds()[0][1]
    if abs(low) > 1e-9:
        m.transform((0, 0), lambda p: (p[0], p[1] - low, p[2]))
    return m


# ---- Creature Keeping's seven needs
# (`creatures_needs` prompt icons) and eight kinds of care
# (`creatures_care` options): their own models, in creature_icons.py.
from creature_icons import CREATURE_ICONS  # noqa: E402

CLASS_ICONS.update(CREATURE_ICONS)


# ---- The Flying class's icons (`ring`, `broom`; no picks, so not in
# CLASS_ICONS: render_icons.py renders FLYING_ICONS beside them), in icon_models_flying.py.
from icon_models_flying import FLYING_ICONS  # noqa: E402
