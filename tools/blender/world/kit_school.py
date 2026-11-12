"""Castle dressing  (pure Python, builds `meshkit.Mesh`;
registered into `kit_models.MODELS`, placed by `data/world/props.json` `kit`
and baked into the cells by `greybox.py` like the rest of the kit).

* The Dueling Club in the Great Hall: `duel_stage` dresses the long raised
  stage of the layout (`gh_duel_stage`, drawn without railings: props.json
  `open_edges`) with a boarded deck, a runner down the ring with the two
  duelists' marks and a centre crest, panelled and gilded skirts, corner
  posts with glowing orbs and carpeted steps; `duel_area_inlay` outlines
  the dueling area on the floor in gold so players see where challenges
  work; `spectator_bench`, `duel_banner` and `floating_candles` finish it.
* The third-floor Charms classroom door: `classroom_door` (a carved
  pointed-arch surround, an oak double door, a plaque and a floating
  feather in a bronze ring, flanking lanterns) and `notice_board` (the
  timetable pinned beside it).

Art direction for 1d: clean shapes and saturated, stylized colours (the
look moves toward cel shading later); no toon shader here, just the kit's
painted tiles tinted by these palettes.
"""

from __future__ import annotations

import math

import layout_lib
import meshkit as mk
from kit_models import Kit, _ball, _disc, _flat
from meshkit import Mesh

# Shared classroom colours (sRGB): saturated accents over warm wood and stone.
ROYAL, ROYAL_DEEP = "#2448CF", "#172D8A"
GOLD, GOLD_PALE = "#E5B22F", "#F5D882"
WOOD_WARM, WOOD_DARK, WOOD_RED = "#8A4F28", "#60361D", "#7C381E"
IVORY, ORB_BLUE, FLAME_WARM = "#F2E6C8", "#7FC8FF", "#FFC46E"
STONE_WARM, STONE_TRIM = "#B2A085", "#CFBD99"
FEATHER = "#F6F0FF"


def _star(m: Mesh, centre, r_out: float, r_in: float, points: int, y: float, mat: str, zone: int,
          phase: float = 0.0) -> None:
    """A flat star on the floor (facing up), as a fan of triangles."""
    cx, _, cz = centre
    ring = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = phase + math.pi * i / points
        ring.append((cx + r * math.sin(a), cz + r * math.cos(a)))
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        _flat(m, [(cx, cz), a, b], y, mat, zone)


def _ring(m: Mesh, centre, r0: float, r1: float, y: float, mat: str, zone: int, n: int = 24) -> None:
    """A flat annulus on the floor (facing up)."""
    cx, _, cz = centre
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        quad = [(cx + r0 * math.sin(a0), cz + r0 * math.cos(a0)), (cx + r1 * math.sin(a0), cz + r1 * math.cos(a0)),
                (cx + r1 * math.sin(a1), cz + r1 * math.cos(a1)), (cx + r0 * math.sin(a1), cz + r0 * math.cos(a1))]
        _flat(m, quad, y, mat, zone)


# ---------------------------------------------------------------------------
# The Dueling Club


def duel_stage(length: float = 18.0, width: float = 3.0, height: float = 0.9, steps: float = 1.5) -> Kit:
    """Dressing for a raised dueling stage `length` x `width` (along x),
    `height` up, with steps `steps` long at both ends (none when `steps` is
    0: the arena's stage, whose short ends get skirts too). Origin: the
    middle of the stage at floor level. The duelists' marks sit 5.5 m
    either side of the centre (data/dueling/venues.json)."""
    m = Mesh()
    hl, hw, h = length / 2, width / 2, height
    top = h + 0.012
    runner = min(0.7, hw * 0.45)  # half width of the runner
    # Deck boards either side of the runner: 1 m x 0.4 m planks in two tones.
    nx = max(1, round(length))
    dx = length / nx
    for side in (-1, 1):
        z_in, z_out = side * (runner + 0.06), side * (hw - 0.08)
        z0, z1 = sorted((z_in, z_out))
        nz = max(1, round((z1 - z0) / 0.4))
        dz = (z1 - z0) / nz
        for i in range(nx):
            for j in range(nz):
                off = 0.5 * dx if j % 2 else 0.0
                xa = -hl + i * dx + off
                xb = min(xa + dx, hl)
                if i == 0 and off:
                    _flat(m, [(-hl, z0 + j * dz), (xa, z0 + j * dz), (xa, z0 + (j + 1) * dz), (-hl, z0 + (j + 1) * dz)],
                          top, "wood", 2)
                _flat(m, [(xa, z0 + j * dz), (xb, z0 + j * dz), (xb, z0 + (j + 1) * dz), (xa, z0 + (j + 1) * dz)], top,
                      "wood", 1 + (i + j) % 2)
    # The runner: royal blue in 1 m pieces (so the cloth keeps its scale),
    # gold borders, the two duelists' marks and a centre crest.
    for i in range(nx):
        xa, xb = -hl + i * dx, -hl + (i + 1) * dx
        _flat(m, [(xa, -runner), (xb, -runner), (xb, runner), (xa, runner)], top + 0.004, "fabric", 3)
    for side in (-1, 1):
        z0, z1 = sorted((side * runner, side * (runner + 0.06)))
        for i in range(nx):
            xa, xb = -hl + i * dx, -hl + (i + 1) * dx
            _flat(m, [(xa, z0), (xb, z0), (xb, z1), (xa, z1)], top + 0.006, "metal", 4)
    for x in (-5.5, 5.5):
        _ring(m, (x, 0.0, 0.0), 0.42, 0.5, top + 0.008, "metal", 4)
        _disc(m, (x, top + 0.008, 0.0), 0.3, "metal", 4, n=16)
        _star(m, (x, 0.0, 0.0), 0.24, 0.1, 4, top + 0.010, "fabric", 5, phase=math.pi / 4)
    _ring(m, (0.0, 0.0, 0.0), 0.55, 0.62, top + 0.008, "metal", 4)
    _star(m, (0.0, 0.0, 0.0), 0.5, 0.2, 8, top + 0.009, "metal", 4)
    _disc(m, (0.0, top + 0.010, 0.0), 0.16, "fabric", 5, n=12)
    # Chevrons pointing from each mark to the other along the runner.
    for x in (-3.0, -1.6, 1.6, 3.0):
        s = 1 if x < 0 else -1
        tip = x + 0.22 * s
        for dz_ in (-1, 1):
            quad = [(x, dz_ * 0.32), (x + 0.08 * s, dz_ * 0.32), (tip + 0.08 * s, 0.0), (tip, 0.0)]
            _flat(m, quad, top + 0.008, "metal", 4)
    # Nosing: a gilded lip round the deck edge.
    for side in (-1, 1):
        mk.box(m, (-hl - 0.03, h - 0.05, side * hw - 0.08 if side > 0 else -hw - 0.03),
               (hl + 0.03, h + 0.03, hw + 0.03 if side > 0 else -hw + 0.08), "metal", 4, skip=("-y",))
    # Skirts on the long sides: a plinth, a top rail, stiles and recessed
    # panels with gilded beads.
    pw = 1.5
    npanel = max(1, round(length / pw))
    pw = length / npanel
    for side in (-1, 1):
        zf = side * (hw + 0.025)  # front face plane, proud of the greybox wall
        z0, z1 = sorted((side * hw, zf))
        mk.box(m, (-hl, 0.0, z0), (hl, 0.14, z1 + side * 0.02 if side > 0 else z1), "wood", 6, skip=("-y",))
        mk.box(m, (-hl, h - 0.17, z0), (hl, h - 0.05, z1), "wood", 6)
        for i in range(npanel + 1):
            x = -hl + i * pw
            mk.box(m, (x - 0.06, 0.14, z0), (x + 0.06, h - 0.17, z1), "wood", 6)
        for i in range(npanel):
            xa, xb = -hl + i * pw + 0.06, -hl + (i + 1) * pw - 0.06
            panel_z = side * (hw + 0.008)
            pts = [(xa, 0.14, panel_z), (xb, 0.14, panel_z), (xb, h - 0.17, panel_z), (xa, h - 0.17, panel_z)]
            m.poly(pts if side > 0 else list(reversed(pts)), "wood", zone=7)
            # Gilded bead inset in the panel, and a lozenge in its middle.
            bz = side * (hw + 0.016)
            for (a, b) in (((xa + 0.06, 0.2), (xb - 0.06, 0.225)), ((xa + 0.06, h - 0.255), (xb - 0.06, h - 0.23)),
                           ((xa + 0.06, 0.2), (xa + 0.085, h - 0.23)), ((xb - 0.085, 0.2), (xb - 0.06, h - 0.23))):
                pts = [(a[0], a[1], bz), (b[0], a[1], bz), (b[0], b[1], bz), (a[0], b[1], bz)]
                m.poly(pts if side > 0 else list(reversed(pts)), "metal", zone=4)
            xm, ym = (xa + xb) / 2, (0.14 + h - 0.17) / 2
            loz = [(xm, ym - 0.16, bz), (xm + 0.16, ym, bz), (xm, ym + 0.16, bz), (xm - 0.16, ym, bz)]
            m.poly(loz if side > 0 else list(reversed(loz)), "fabric", zone=3)
    # Steps: a runner down each flight and brass newel posts with glowing
    # orbs at the four corners of the stage.
    if steps <= 0:
        # Free-standing stage: the short ends get the same skirt and lip.
        for end in (-1, 1):
            x0, x1 = sorted((end * hl, end * (hl + 0.025)))
            mk.box(m, (x0, 0.0, -hw), (x1, 0.14, hw), "wood", 6, skip=("-y",))
            mk.box(m, (x0, h - 0.17, -hw), (x1, h - 0.05, hw), "wood", 6)
            for z in (-hw + 0.06, hw - 0.06):
                mk.box(m, (x0, 0.14, z - 0.06), (x1, h - 0.17, z + 0.06), "wood", 6)
            px = end * (hl + 0.008)
            panel = [(px, 0.14, hw - 0.12), (px, 0.14, -hw + 0.12), (px, h - 0.17, -hw + 0.12), (px, h - 0.17, hw - 0.12)]
            m.poly(panel if mk.newell(panel)[0] * end > 0 else list(reversed(panel)), "wood", zone=7)
            loz = [(end * (hl + 0.016), h / 2 - 0.02 - 0.16, 0.0), (end * (hl + 0.016), h / 2 - 0.02, 0.16),
                   (end * (hl + 0.016), h / 2 - 0.02 + 0.16, 0.0), (end * (hl + 0.016), h / 2 - 0.02, -0.16)]
            m.poly(loz if mk.newell(loz)[0] * end > 0 else list(reversed(loz)), "fabric", zone=3)
            lx0, lx1 = sorted((end * (hl - 0.08), end * (hl + 0.03)))
            mk.box(m, (lx0, h - 0.05, -hw - 0.03), (lx1, h + 0.03, hw + 0.03), "metal", 4, skip=("-y",))
    # Treads as the greybox draws them (layout_lib._build_stair): n steps
    # from the far end, tread i's top half a riser above the ramp's middle.
    n = max(1, round(h / layout_lib.STEP_RISER))
    d, r = steps / n, h / n
    for end in (-1, 1):
        far = end * (hl + steps)
        prev = 0.0
        for i in range(n if steps > 0 else 0):
            xa, xb = far - end * i * d, far - end * (i + 1) * d
            y = (i + 0.5) * r + 0.012
            lo, hi = sorted((xa, xb))
            _flat(m, [(lo, -runner), (hi, -runner), (hi, runner), (lo, runner)], y, "fabric", 3)
            # The wooden step either side of the runner, the stair's full
            # width (the kit draws no flight in the kept-clear area: the floor showed
            # through beside the runner; the Planner draws the stair's sides).
            for za, zb in ((-hw, -runner), (runner, hw)):
                _flat(m, [(lo, za), (hi, za), (hi, zb), (lo, zb)], y, "wood", 6)
            # The riser in front of the tread, and a gilded nosing on it.
            xr = xa + end * 0.012
            for za, zb, mat, zone in ((-hw, -runner, "wood", 6), (-runner, runner, "fabric", 3), (runner, hw, "wood", 6)):
                riser = [(xr, prev, za), (xr, prev, zb), (xr, y, zb), (xr, y, za)]
                if mk.newell(riser)[0] * end < 0:
                    riser.reverse()
                m.poly(riser, mat, zone=zone)
            mk.box(m, (min(xa, xa + end * 0.03), y - 0.025, -runner - 0.04), (max(xa, xa + end * 0.03), y + 0.006, runner + 0.04),
                   "metal", 4, skip=("-y",))
            prev = y
        for side in (-1, 1):
            x, z = end * (hl - 0.12), side * (hw - 0.12)
            mk.lathe(m, [(0.09, 0.0), (0.07, 0.06), (0.045, 0.1), (0.045, 0.62), (0.07, 0.66), (0.07, 0.7)], 8, "metal", 4,
                     (x, h, z), cap_top=True)
            _ball(m, (x, h + 0.83, z), 0.13, "glow", 8, seg=8)
            mk.lathe(m, [(0.06, 0.0), (0.1, 0.05), (0.0, 0.06)], 8, "metal", 4, (x, h + 0.7, z))
    pal = {1: WOOD_WARM, 2: "#985A32", 3: ROYAL, 4: GOLD, 5: GOLD_PALE, 6: WOOD_DARK, 7: WOOD_RED, 8: ORB_BLUE}
    lights = [{"pos": [end * (hl - 0.12), h + 1.0, 0.0], "color": "#8CC8FF", "intensity": 5.0, "range": 9.0}
              for end in (-1, 1)]
    return Kit(m, pal, lights)


def duel_area_inlay(width: float = 23.5, depth: float = 9.6, gap_x0: float = 0.0, gap_x1: float = 0.0) -> Kit:
    """A gold line inlaid in the floor round the dueling area (origin at
    its centre), with a rosette at each corner. The line is skipped between
    `gap_x0` and `gap_x1` on the long sides (where tables stand)."""
    m = Mesh()
    hw, hd, t, y = width / 2, depth / 2, 0.08, 0.005
    for z in (-hd, hd):
        for xa, xb in ((-hw, hw),) if gap_x1 <= gap_x0 else ((-hw, gap_x0), (gap_x1, hw)):
            n = max(1, round((xb - xa) / 2.0))
            for i in range(n):
                a, b = xa + (xb - xa) * i / n, xa + (xb - xa) * (i + 1) / n
                _flat(m, [(a, z - t / 2), (b, z - t / 2), (b, z + t / 2), (a, z + t / 2)], y, "metal", 1)
    for x in (-hw, hw):
        n = max(1, round(depth / 2.0))
        for i in range(n):
            a, b = -hd + depth * i / n, -hd + depth * (i + 1) / n
            _flat(m, [(x - t / 2, a), (x + t / 2, a), (x + t / 2, b), (x - t / 2, b)], y, "metal", 1)
    for x in (-hw, hw):
        for z in (-hd, hd):
            _ring(m, (x, 0.0, z), 0.26, 0.32, y + 0.001, "metal", 1, n=16)
            _star(m, (x, 0.0, z), 0.24, 0.09, 4, y + 0.002, "fabric", 2)
    return Kit(m, {1: GOLD, 2: ROYAL})


def spectator_bench(length: float = 5.0) -> Kit:
    """A low oak bench for watching duels (origin at its bottom centre, long
    along x), with a blue cushion strip."""
    m = Mesh()
    hl = length / 2
    mk.box(m, (-hl, 0.38, -0.2), (hl, 0.44, 0.2), "wood", 1)
    nseg = max(1, round(length))
    for i in range(nseg):
        a, b = -hl + length * i / nseg + 0.02, -hl + length * (i + 1) / nseg - 0.02
        mk.box(m, (a, 0.44, -0.16), (b, 0.48, 0.16), "fabric", 2)
    for x in (-hl + 0.15, 0.0, hl - 0.15):
        mk.box(m, (x - 0.05, 0.0, -0.17), (x + 0.05, 0.38, 0.17), "wood", 3, skip=("-y",))
    mk.box(m, (-hl + 0.1, 0.1, -0.03), (hl - 0.1, 0.16, 0.03), "wood", 3)
    return Kit(m, {1: WOOD_WARM, 2: ROYAL, 3: WOOD_DARK})


def duel_banner(height: float = 6.0, width: float = 2.0) -> Kit:
    """A tall wall banner of the Dueling Club: royal blue with a gold border,
    swallow-tailed, crossed wands under a star; hung from a gilded rod.
    Origin: the middle of its back at the bottom of the tails (on the wall)."""
    m = Mesh()
    hw = width / 2
    tail = 0.6
    z = 0.04
    outline = [(-hw, 0.0), (0.0, tail), (hw, 0.0), (hw, height), (-hw, height)]
    # Cloth as horizontal strips (the fabric tile keeps its scale).
    nstrip = max(1, round(height / 1.0))
    ys = [tail + (height - tail) * i / nstrip for i in range(nstrip + 1)]
    for y0, y1 in zip(ys, ys[1:]):
        m.poly([(-hw, y0, z), (hw, y0, z), (hw, y1, z), (-hw, y1, z)], "fabric", zone=1)
    m.poly([(-hw, 0.0, z), (0.0, tail, z), (-hw, tail, z)], "fabric", zone=1)
    m.poly([(0.0, tail, z), (hw, 0.0, z), (hw, tail, z)], "fabric", zone=1)
    # Gold border just proud of the cloth.
    b, zb = 0.1, z + 0.004
    edges = [((-hw, height), (hw, height)), ((hw, height), (hw, 0.0)), ((hw, 0.0), (0.0, tail)), ((0.0, tail), (-hw, 0.0)),
             ((-hw, 0.0), (-hw, height))]
    for (xa, ya), (xb, yb) in edges:
        dxe, dye = xb - xa, yb - ya
        ln = math.hypot(dxe, dye)
        nxe, nye = -dye / ln * b, dxe / ln * b  # inward (the outline runs clockwise seen from the front)
        quad = [(xa, ya, zb), (xb, yb, zb), (xb + nxe, yb + nye, zb), (xa + nxe, ya + nye, zb)]
        if mk.newell(quad)[2] < 0:
            quad.reverse()
        m.poly(quad, "metal", zone=2)
    # Emblem: two crossed wands under a four-pointed star.
    cy = height * 0.55
    ze = z + 0.006
    for s in (-1, 1):
        a = (s * -0.55, cy - 0.7)
        c = (s * 0.5, cy + 0.6)
        d = (c[0] - a[0], c[1] - a[1])
        ln = math.hypot(*d)
        nx_, ny_ = -d[1] / ln * 0.045, d[0] / ln * 0.045
        quad = [(a[0] - nx_, a[1] - ny_, ze), (a[0] + nx_, a[1] + ny_, ze), (c[0] + nx_ * 0.5, c[1] + ny_ * 0.5, ze),
                (c[0] - nx_ * 0.5, c[1] - ny_ * 0.5, ze)]
        if mk.newell(quad)[2] < 0:
            quad.reverse()
        m.poly(quad, "wood", zone=3)
        # Sparks at the wand tips.
        tipq = [(c[0], c[1] + 0.09, ze + 0.001), (c[0] + 0.035, c[1], ze + 0.001), (c[0], c[1] - 0.09, ze + 0.001),
                (c[0] - 0.035, c[1], ze + 0.001)]
        m.poly(tipq if mk.newell(tipq)[2] > 0 else list(reversed(tipq)), "metal", zone=2)
    star = []
    for i in range(8):
        r = 0.42 if i % 2 == 0 else 0.13
        a = math.pi * i / 4
        star.append((r * math.sin(a), cy + 1.05 + r * math.cos(a)))
    for i in range(8):
        tri = [(0.0, cy + 1.05, ze), (star[i][0], star[i][1], ze), (star[(i + 1) % 8][0], star[(i + 1) % 8][1], ze)]
        m.poly(tri if mk.newell(tri)[2] > 0 else list(reversed(tri)), "metal", zone=2)
    # Rod and finials.
    mk.rod(m, (-hw - 0.15, height + 0.05, 0.08), (hw + 0.15, height + 0.05, 0.08), 0.03, 8, "metal", 2)
    for s in (-1, 1):
        _ball(m, (s * (hw + 0.2), height + 0.05, 0.08), 0.06, "metal", 2, seg=8)
        mk.rod(m, (s * (hw * 0.6), height + 0.05, 0.08), (s * (hw * 0.6), height + 0.45, 0.01), 0.008, 4, "metal", 2)
    return Kit(m, {1: ROYAL, 2: GOLD, 3: WOOD_RED})


def floating_candles(length: float = 18.0, depth: float = 4.0, count: int = 28, seed: int = 3) -> Kit:
    """Candles floating in a band over the stage (origin at the band's
    centre, at the candles' mean height), each gently tilted, flames lit."""
    m = Mesh()
    s = seed
    for i in range(count):
        s = (s * 1103515245 + 12345) % 2**31
        fx = (i + 0.5) / count + ((s % 1000) / 1000 - 0.5) * 0.6 / count
        s = (s * 1103515245 + 12345) % 2**31
        fz = (s % 1000) / 1000 - 0.5
        s = (s * 1103515245 + 12345) % 2**31
        fy = (s % 1000) / 1000 - 0.5
        x, y, z = -length / 2 + fx * length, fy * 0.9, fz * depth
        hgt = 0.22 + 0.12 * ((s >> 4) % 100) / 100
        mk.cylinder(m, (x, y, z), 0.028, hgt, 6, "wax", 0, cap_bottom=True)
        mk.lathe(m, [(0.0, 0.0), (0.02, 0.02), (0.015, 0.05), (0.0, 0.09)], 5, "flame", 0, (x, y + hgt + 0.005, z))
    lights = [{"pos": [x, 0.3, 0.0], "color": FLAME_WARM, "intensity": 6.0, "range": 12.0} for x in (-length / 4, length / 4)]
    return Kit(m, {}, lights)


# ---------------------------------------------------------------------------
# The Charms classroom door (third floor)


def classroom_door(width: float = 2.4, height: float = 3.6) -> Kit:
    """A classroom entrance on a corridor wall: a carved stone surround with
    a pointed arch, an oak double door with iron straps and studs, a brass
    plaque, and above the arch a floating white feather in a bronze ring
    (the Charms sign). Two lanterns flank it. Origin: the middle of the
    door's foot on the wall face; front +Z."""
    m = Mesh()
    hw = width / 2
    spring = height - 0.55 * width
    outline = mk.arch_outline(width, spring, "pointed", 0.0, 8)
    # Door leaves: the arch outline split down the middle, in plank strips.
    zl = 0.05
    nstrip = 8
    for i in range(nstrip):
        xa, xb = -hw + width * i / nstrip, -hw + width * (i + 1) / nstrip

        def top_at(x):
            return max(y for y in _arch_span(outline, x))
        ya, yb = top_at(xa + 1e-4), top_at(xb - 1e-4)
        quad = [(xa, 0.0, zl), (xb, 0.0, zl), (xb, yb, zl), (xa, ya, zl)]
        m.poly(quad, "wood", zone=1 + i % 2)
    # The meeting stile, iron straps with studs, ring handles.
    mk.box(m, (-0.03, 0.0, zl), (0.03, height - 0.05, zl + 0.03), "wood", 3)
    for y in (0.5, 1.6, 2.5):
        for s in (-1, 1):
            x0, x1 = sorted((s * 0.06, s * (hw - 0.06)))
            mk.box(m, (x0, y, zl), (x1, y + 0.09, zl + 0.02), "metal", 4)
            for k in range(4):
                xs = x0 + (x1 - x0) * (k + 0.5) / 4
                _ball(m, (xs, y + 0.045, zl + 0.025), 0.018, "metal", 4, seg=5)
    for s in (-1, 1):
        ring_c = (s * 0.18, 1.15, zl + 0.04)
        for k in range(10):
            a0, a1 = 2 * math.pi * k / 10, 2 * math.pi * (k + 1) / 10
            mk.rod(m, (ring_c[0] + 0.07 * math.sin(a0), ring_c[1] + 0.07 * math.cos(a0), ring_c[2]),
                   (ring_c[0] + 0.07 * math.sin(a1), ring_c[1] + 0.07 * math.cos(a1), ring_c[2]), 0.01, 4, "metal", 5,
                   caps=False)
    # Stone surround: voussoir blocks round the arch, jamb blocks, a plinth.
    sw = 0.32
    outer = mk.offset_convex(outline, sw)
    n = len(outline)
    zs = 0.16
    for i in range(n):
        j = (i + 1) % n
        (ua, va), (ub, vb) = outline[i], outline[j]
        (oa, pa), (ob, pb) = outer[i], outer[j]
        if va < 1e-6 and vb < 1e-6:
            continue  # threshold
        pa, pb = max(pa, 0.0), max(pb, 0.0)
        face = [(ua, va, zs), (ub, vb, zs), (ob, pb, zs), (oa, pa, zs)]
        if mk.newell(face)[2] < 0:
            face.reverse()
        m.poly(face, "stone", zone=6 + i % 2)
        # Reveal into the door and the outer return to the wall.
        for (p, q, za, zb) in (((ua, va), (ub, vb), zl, zs), ((oa, pa), (ob, pb), 0.0, zs)):
            side = [(p[0], p[1], za), (q[0], q[1], za), (q[0], q[1], zb), (p[0], p[1], zb)]
            mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
            centre = (0.0, spring * 0.6)
            outward = (mid[0] - centre[0], mid[1] - centre[1])
            nrm = mk.newell(side)
            want = 1 if (p, q) == ((ua, va), (ub, vb)) else -1
            if (nrm[0] * outward[0] + nrm[1] * outward[1]) * want > 0:
                side.reverse()
            m.poly(side, "stone", zone=6)
    # Keystone with the plaque, and a moulded hood over the arch.
    apex = height
    mk.box(m, (-0.2, apex - 0.1, 0.0), (0.2, apex + 0.42, zs + 0.06), "stone", 7)
    mk.box(m, (-0.55, apex + 0.52, 0.0), (0.55, apex + 0.82, 0.07), "metal", 5)
    mk.box(m, (-0.5, apex + 0.56, 0.07), (0.5, apex + 0.78, 0.075), "dark", 0)
    # The sign: a white feather floating in a bronze ring above the plaque.
    cy = apex + 1.35
    for k in range(20):
        a0, a1 = 2 * math.pi * k / 20, 2 * math.pi * (k + 1) / 20
        mk.rod(m, (0.36 * math.sin(a0), cy + 0.36 * math.cos(a0), 0.2), (0.36 * math.sin(a1), cy + 0.36 * math.cos(a1), 0.2),
               0.025, 5, "metal", 5, caps=False)
    vane = []
    for i in range(9):
        t = i / 8
        w_ = 0.09 * math.sin(math.pi * min(1.0, t * 1.1))
        vane.append((t, w_))
    tilt = math.radians(35)
    ca, sa = math.cos(tilt), math.sin(tilt)

    def fpt(t, off):
        x, y = (t - 0.5) * 0.55, off
        return (x * ca - y * sa, cy + x * sa + y * ca, 0.22)
    for (t0, w0), (t1, w1) in zip(vane, vane[1:]):
        for sgn in (-1, 1):
            quad = [fpt(t0, 0.0), fpt(t1, 0.0), fpt(t1, sgn * w1), fpt(t0, sgn * w0)]
            if mk.newell(quad)[2] < 0:
                quad.reverse()
            m.poly(quad, "glow", zone=8)
    mk.rod(m, fpt(-0.05, 0.0), fpt(1.0, 0.0), 0.008, 4, "metal", 5)
    # Sparkles round the feather.
    for (x, y, sz) in ((-0.22, cy + 0.12, 0.05), (0.2, cy - 0.16, 0.04), (0.05, cy + 0.26, 0.03)):
        q = [(x, y + sz, 0.24), (x + sz * 0.35, y, 0.24), (x, y - sz, 0.24), (x - sz * 0.35, y, 0.24)]
        m.poly(q if mk.newell(q)[2] > 0 else list(reversed(q)), "glow", zone=9)
    # Lanterns on brackets either side.
    for s in (-1, 1):
        x = s * (hw + 0.75)
        mk.box(m, (x - 0.07, 2.25, 0.0), (x + 0.07, 2.55, 0.03), "metal", 4)
        mk.rod(m, (x, 2.45, 0.03), (x, 2.45, 0.28), 0.014, 4, "metal", 4)
        mk.lathe(m, [(0.02, 0.0), (0.1, 0.03), (0.1, 0.05)], 6, "metal", 4, (x, 2.1, 0.3), cap_bottom=True, cap_top=False)
        mk.lathe(m, [(0.085, 0.0), (0.09, 0.2), (0.08, 0.3)], 6, "glow", 10, (x, 2.15, 0.3), cap_top=False)
        mk.lathe(m, [(0.1, 0.0), (0.05, 0.08), (0.0, 0.2)], 6, "metal", 4, (x, 2.45, 0.3), cap_bottom=True)
        mk.rod(m, (x, 2.45, 0.28), (x, 2.45, 0.3), 0.01, 4, "metal", 4)
    pal = {1: "#8A4A26", 2: "#7F4320", 3: WOOD_DARK, 4: "#4C4E5A", 5: "#C88B31", 6: STONE_WARM, 7: STONE_TRIM,
           8: FEATHER, 9: GOLD_PALE, 10: FLAME_WARM}
    lights = [{"pos": [s * (hw + 0.75), 2.3, 0.45], "color": FLAME_WARM, "intensity": 3.5, "range": 6.0} for s in (-1, 1)]
    return Kit(m, pal, lights)


def _arch_span(outline, x):
    """Heights where a vertical line at x crosses an arch outline."""
    out = []
    n = len(outline)
    for i in range(n):
        (xa, ya), (xb, yb) = outline[i], outline[(i + 1) % n]
        lo, hi = min(xa, xb), max(xa, xb)
        if lo - 1e-9 <= x <= hi + 1e-9 and abs(xb - xa) > 1e-9:
            out.append(ya + (yb - ya) * (x - xa) / (xb - xa))
    return out or [0.0]


def notice_board(width: float = 1.3, height: float = 1.0) -> Kit:
    """A cork notice board in an oak frame with the class timetable and a
    few notices pinned to it (origin: the middle of its foot on the wall;
    the board hangs 1.1 m up)."""
    m = Mesh()
    hw, y0 = width / 2, 1.1
    y1 = y0 + height
    m.poly([(-hw, y0, 0.03), (hw, y0, 0.03), (hw, y1, 0.03), (-hw, y1, 0.03)], "leather", zone=1)
    for (a, b) in (((-hw - 0.06, y0 - 0.06), (hw + 0.06, y0)), ((-hw - 0.06, y1), (hw + 0.06, y1 + 0.06)),
                   ((-hw - 0.06, y0), (-hw, y1)), ((hw, y0), (hw + 0.06, y1))):
        mk.box(m, (a[0], a[1], 0.0), (b[0], b[1], 0.05), "wood", 2)
    # Header plate.
    mk.box(m, (-0.32, y1 + 0.08, 0.0), (0.32, y1 + 0.2, 0.03), "metal", 3)
    sheets = [(-0.5, 0.15, 0.42, 0.62, 0.0), (0.0, 0.5, 0.3, 0.38, 3.0), (0.05, 0.08, 0.26, 0.3, -4.0),
              (0.4, 0.45, 0.2, 0.28, 6.0)]
    for i, (x, y, w, h, deg) in enumerate(sheets):
        a = math.radians(deg)
        cx, cy = x + w / 2, y0 + y + h / 2
        pts = []
        for px, py in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
            pts.append((cx + px * math.cos(a) - py * math.sin(a), cy + px * math.sin(a) + py * math.cos(a), 0.034 + 0.002 * i))
        m.poly(pts, "parchment", zone=0)
        _ball(m, (cx, cy + h / 2 - 0.03, 0.042 + 0.002 * i), 0.012, "metal", 4 + i % 2, seg=5)
    # Ruled lines on the timetable (thin dark strips).
    for k in range(6):
        y = y0 + 0.24 + k * 0.08
        m.poly([(-0.47, y, 0.0365), (-0.12, y, 0.0365), (-0.12, y + 0.012, 0.0365), (-0.47, y + 0.012, 0.0365)], "dark", zone=0)
    return Kit(m, {1: "#B97C43", 2: WOOD_WARM, 3: "#C88B31", 4: "#CF3030", 5: ROYAL})


MODELS = {
    "duel_stage": duel_stage,
    "duel_area_inlay": duel_area_inlay,
    "spectator_bench": spectator_bench,
    "duel_banner": duel_banner,
    "floating_candles": floating_candles,
    "classroom_door": classroom_door,
    "notice_board": notice_board,
}

# Triangle budgets (style guide section 6).
BUDGETS = {"duel_stage": 8000, "classroom_door": 3000, "duel_banner": 600, "floating_candles": 1500,
           "duel_area_inlay": 800, "spectator_bench": 400, "notice_board": 600}

# Register with the castle kit (whichever of the two modules is imported
# first, both end up complete).
import kit_models as _km  # noqa: E402

_km.MODELS.update(MODELS)
_km.BUDGETS.update(BUDGETS)
