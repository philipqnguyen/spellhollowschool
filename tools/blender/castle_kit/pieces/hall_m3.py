"""The Great Hall's carved and built heroes (castle kit M3,
docs/art/castle-kit.md): the four beast braziers, the hooded
fireplace and the end window over the dais.

The beasts are our own neutral designs, never the houses' animals: a hart
(a), a bear (b), a boar (c) and a hound (d), each sitting upright on a
carved bracket on a bay shaft and holding an iron arm from which a fire
basket hangs on three chains. Frames as geom.py: the brazier's origin is
the bracket's back on the shaft face at the beast's seat (y = 0), +z into
the hall; the fireplace and the end window are openings (x = 0 on the
centre line, y = 0 on the floor or sill, z = 0 on the wall face).

Colours come only from palette data: the beasts' stone, the iron and
the fire are props-atlas names (`beast_stone`, `iron`, `glow_lantern`,
`soot`, `oak_aged`), the fireplace's masonry is the cell's wall stone and
trim sheet, the window's tracery the trim sheet, its glass the glass sheet."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import fire
from castle_kit import grid as G
from castle_kit.pieces import geom, hall, openings
from castle_kit.pieces.geom import Piece, add_face

STONE_ZONE, IRON_ZONE, FIRE_ZONE, EYE_ZONE, SOOT_ZONE, LOG_ZONE = 1, 2, 3, 4, 5, 6
ZONES = {STONE_ZONE: "beast_stone", IRON_ZONE: "iron", FIRE_ZONE: "glow_lantern", EYE_ZONE: "iron",
         SOOT_ZONE: "soot", LOG_ZONE: "oak_aged"}
WALL = "castle_wall|world"
FLOOR = "castle_floor|world"
BEASTS = {"a": "hart", "b": "bear", "c": "boar", "d": "hound"}

BRAZIER_ARM = 1.5  # the basket hangs this far out from the shaft face (clear of the shaft and the bracket)
BEAST_SCALE = 1.35  # the beasts and their brackets are modelled at 1.5 m and drawn larger (they sit 8 m up)
BASKET_R = 0.5


def trim(strip: str) -> str:
    return f"castle_trim|trim:{strip}"


def fit(tile: str) -> str:
    return f"castle_trim|fit:{tile}"


def _ellipsoid(m, centre, rx, ry, rz, zone, seg=8, rings=5, tile="metal", tilt=0.0):
    """An ellipsoid about a vertical axis (optionally tilted forward by
    `tilt` radians about x): a lathe of `rings` bands."""
    prof = [(math.sin(math.pi * i / rings), -math.cos(math.pi * i / rings)) for i in range(rings + 1)]
    mark = m.mark()
    mk.lathe(m, [(r * rx, y * ry) for r, y in prof], seg, tile, zone, scale_xz=(1.0, rz / rx))
    c, s = math.cos(tilt), math.sin(tilt)

    def tf(p):
        x, y, z = p
        y2, z2 = y * c - z * s, y * s + z * c
        return (x + centre[0], y2 + centre[1], z2 + centre[2])

    m.transform(mark, tf)


def _frustum_x(m, p0, p1, r0, r1, zone, seg=6, tile="metal"):
    """A tapered rod from p0 to p1 (radius r0 to r1)."""
    mark = m.mark()
    h = math.dist(p0, p1)
    mk.lathe(m, [(r0, 0.0), (r1, h)], seg, tile, zone, cap_top=True, cap_bottom=False)
    d = mk.normalize(mk.sub(p1, p0))
    up = (0.0, 1.0, 0.0)
    c = mk.dot(up, d)
    if c > 1 - 1e-9:
        rot = lambda p: p  # noqa: E731
    elif c < -1 + 1e-9:
        rot = lambda p: (p[0], -p[1], -p[2])  # noqa: E731
    else:
        k = mk.normalize(mk.cross(up, d))
        s = math.sqrt(1 - c * c)

        def rot(p):
            kxp = mk.cross(k, p)
            kdp = mk.dot(k, p)
            return tuple(p[i] * c + kxp[i] * s + k[i] * kdp * (1 - c) for i in range(3))

    m.transform(mark, lambda p: mk.add(rot(p), p0))


def _flat_quad(m, pts, zone, tile="metal"):
    """A thin double-sided plate (ears, tines): the quad and its reverse."""
    m.poly(pts, tile, zone=zone)
    m.poly(list(reversed(pts)), tile, zone=zone)


def _bracket(m) -> None:
    """The carved bracket the beast sits on: three stepped courses tapering
    down the shaft face to a pendant."""
    steps = [(0.0, -0.22, 0.42, 0.78), (-0.22, -0.46, 0.36, 0.62), (-0.46, -0.7, 0.3, 0.44)]
    for y1, y0, hw, d in [(a, b, c, e) for a, b, c, e in steps]:
        mk.box(m, (-hw, y0, 0.0), (hw, y1, d), "stone", STONE_ZONE, skip=("-z",))
    mk.lathe(m, [(0.0, -1.0), (0.12, -0.9), (0.2, -0.78), (0.24, -0.7)], 6, "stone", STONE_ZONE, (0.0, 0.0, 0.26),
             cap_top=False)


def _basket(m, drop: float) -> None:
    """The iron arm from under the beast's forepaws out to BRAZIER_ARM, a
    ring, three chains down to a shallow fire basket whose bottom is `drop`
    under the seat, a bed of glowing coals and flames."""
    arm_y = -0.32
    z_end = BRAZIER_ARM
    mk.box(m, (-0.035, arm_y - 0.04, 0.3), (0.035, arm_y + 0.04, z_end + 0.04), "metal", IRON_ZONE, skip=("-z",))
    # A curled brace under the arm, back to the bracket.
    _frustum_x(m, (0.0, -0.66, 0.42), (0.0, arm_y - 0.03, 0.95), 0.025, 0.025, IRON_ZONE, 4, "metal")
    mk.lathe(m, [(0.0, 0.0), (0.05, 0.03), (0.05, 0.08), (0.0, 0.11)], 6, "metal", IRON_ZONE, (0.0, arm_y - 0.15, z_end))
    rim_y = -drop + 0.28
    hook = (0.0, arm_y - 0.15, z_end)
    for k in range(3):
        a = 2 * math.pi * k / 3 + math.pi / 6
        foot = (BASKET_R * 0.92 * math.cos(a), rim_y, z_end + BASKET_R * 0.92 * math.sin(a))
        _chain(m, hook, foot)
    # The basket: a shallow iron bowl with a rolled rim and a foot.
    prof = [(0.0, -drop), (0.18, -drop), (0.3, -drop + 0.06), (0.43, -drop + 0.16), (BASKET_R, rim_y),
            (BASKET_R + 0.04, rim_y + 0.03)]
    mk.lathe(m, prof, 10, "metal", IRON_ZONE, (0.0, 0.0, z_end), cap_top=False, cap_bottom=True)
    # The inside of the bowl (seen from above, from the galleries' height) and the coal bed.
    mk.lathe(m, [(BASKET_R + 0.04, rim_y + 0.03), (BASKET_R - 0.03, rim_y + 0.01), (0.32, rim_y - 0.04)], 10, "metal",
             IRON_ZONE, (0.0, 0.0, z_end), cap_top=False)
    ring = [(0.33 * math.sin(2 * math.pi * i / 10), rim_y - 0.04, z_end + 0.33 * math.cos(2 * math.pi * i / 10))
            for i in range(10)]
    pts = list(reversed(ring)) if mk.newell(ring)[1] < 0 else ring
    m.poly(pts, "glow", zone=FIRE_ZONE)
    # The flames rising from the coals are the client's animated fire (beast_brazier's meta "fires").


def _chain(m, a, b, links: int = 8) -> None:
    """A chain of flat links from a to b (alternating planes)."""
    d = mk.sub(b, a)
    ln = mk.length(d)
    u = mk.normalize(d)
    side = mk.normalize(mk.cross(u, (0.0, 0.0, 1.0))) if abs(u[2]) < 0.9 else (1.0, 0.0, 0.0)
    side2 = mk.normalize(mk.cross(u, side))
    for i in range(links):
        p0 = mk.add(a, mk.scale(u, ln * i / links))
        p1 = mk.add(a, mk.scale(u, ln * (i + 0.85) / links))
        w = mk.scale(side if i % 2 == 0 else side2, 0.025)
        quad = [mk.sub(p0, w), mk.add(p0, w), mk.add(p1, w), mk.sub(p1, w)]
        _flat_quad(m, quad, IRON_ZONE, "metal")


def _eyes(m, x, y, z, spread):
    for sx in (-1.0, 1.0):
        mk.centred_box(m, (sx * spread + x, y, z), (0.05, 0.04, 0.02), "metal", EYE_ZONE)


def _hart(m) -> None:
    """A hart sitting upright: slim chest, long neck, narrow head with
    branching antlers."""
    _ellipsoid(m, (0.0, 0.42, 0.34), 0.3, 0.42, 0.26, STONE_ZONE, tilt=-0.15)  # haunches and chest
    _frustum_x(m, (0.0, 0.7, 0.42), (0.0, 1.18, 0.58), 0.15, 0.1, STONE_ZONE, 7)  # neck
    _ellipsoid(m, (0.0, 1.24, 0.66), 0.12, 0.13, 0.17, STONE_ZONE, tilt=0.5)  # head
    _frustum_x(m, (0.0, 1.2, 0.74), (0.0, 1.13, 0.92), 0.08, 0.05, STONE_ZONE, 6)  # muzzle
    _eyes(m, 0.0, 1.29, 0.75, 0.075)
    for sx in (-1.0, 1.0):
        _flat_quad(m, [(sx * 0.1, 1.3, 0.58), (sx * 0.24, 1.37, 0.52), (sx * 0.22, 1.42, 0.6), (sx * 0.1, 1.36, 0.63)],
                   STONE_ZONE)  # ear
        base = (sx * 0.06, 1.36, 0.62)
        beam = [(sx * 0.16, 1.62, 0.56), (sx * 0.3, 1.86, 0.5), (sx * 0.36, 2.06, 0.56)]
        prev = base
        for i, q in enumerate(beam):
            _frustum_x(m, prev, q, 0.035 - 0.007 * i, 0.03 - 0.007 * i, STONE_ZONE, 4)
            prev = q
        for q, tip in ((beam[0], (sx * 0.14, 1.78, 0.74)), (beam[1], (sx * 0.4, 2.0, 0.36)), (beam[1], (sx * 0.22, 2.02, 0.68))):
            _frustum_x(m, q, tip, 0.022, 0.01, STONE_ZONE, 4)
        # Forelegs down to the bracket's edge, the hind haunch.
        _frustum_x(m, (sx * 0.12, 0.62, 0.48), (sx * 0.1, 0.02, 0.62), 0.06, 0.04, STONE_ZONE, 5)
        _ellipsoid(m, (sx * 0.2, 0.2, 0.28), 0.12, 0.2, 0.22, STONE_ZONE, seg=6, rings=4)


def _bear(m) -> None:
    """A bear sitting upright: a heavy round body, a broad head with a short
    muzzle and small round ears, thick forelegs."""
    _ellipsoid(m, (0.0, 0.5, 0.36), 0.38, 0.52, 0.33, STONE_ZONE, tilt=-0.1)
    _ellipsoid(m, (0.0, 1.12, 0.48), 0.22, 0.2, 0.22, STONE_ZONE)
    _frustum_x(m, (0.0, 1.08, 0.6), (0.0, 1.04, 0.78), 0.11, 0.07, STONE_ZONE, 7)
    _ellipsoid(m, (0.0, 1.06, 0.79), 0.05, 0.04, 0.03, EYE_ZONE, seg=6, rings=3, tile="metal")  # nose
    _eyes(m, 0.0, 1.18, 0.66, 0.09)
    for sx in (-1.0, 1.0):
        _ellipsoid(m, (sx * 0.16, 1.3, 0.44), 0.07, 0.07, 0.04, STONE_ZONE, seg=6, rings=3)  # ear
        _frustum_x(m, (sx * 0.2, 0.8, 0.5), (sx * 0.16, 0.06, 0.66), 0.11, 0.09, STONE_ZONE, 6)
        _ellipsoid(m, (sx * 0.16, 0.06, 0.7), 0.1, 0.06, 0.12, STONE_ZONE, seg=6, rings=3)  # paw
        _ellipsoid(m, (sx * 0.26, 0.22, 0.32), 0.15, 0.22, 0.25, STONE_ZONE, seg=6, rings=4)


def _boar(m) -> None:
    """A boar sitting up on its haunches: a deep wedge of a body with a
    bristle crest along the neck, a long snout with tusks, pointed ears."""
    _ellipsoid(m, (0.0, 0.46, 0.36), 0.32, 0.48, 0.3, STONE_ZONE, tilt=-0.25)
    _ellipsoid(m, (0.0, 0.98, 0.56), 0.2, 0.2, 0.24, STONE_ZONE, tilt=0.6)
    _frustum_x(m, (0.0, 0.94, 0.7), (0.0, 0.8, 1.08), 0.13, 0.085, STONE_ZONE, 6)  # the long wedge snout
    mk.lathe(m, [(0.09, 0.0), (0.09, 0.02)], 6, "metal", EYE_ZONE, (0.0, 0.79, 1.08), cap_top=True)  # its flat end
    _eyes(m, 0.0, 1.06, 0.7, 0.095)
    # Bristle crest: a fin along the neck and back.
    crest = [(0.0, 0.66, 0.12), (0.0, 1.1, 0.4), (0.0, 1.22, 0.5), (0.0, 1.08, 0.52), (0.0, 0.7, 0.24)]
    _flat_quad(m, crest[:4], STONE_ZONE)
    _flat_quad(m, [crest[0], crest[3], crest[4]], STONE_ZONE)
    for sx in (-1.0, 1.0):
        _frustum_x(m, (sx * 0.08, 0.84, 0.98), (sx * 0.17, 1.0, 1.02), 0.035, 0.006, STONE_ZONE, 4)  # tusk
        _flat_quad(m, [(sx * 0.1, 1.12, 0.5), (sx * 0.22, 1.3, 0.46), (sx * 0.17, 1.14, 0.58), (sx * 0.1, 1.12, 0.56)],
                   STONE_ZONE)  # ear
        _frustum_x(m, (sx * 0.14, 0.64, 0.5), (sx * 0.12, 0.02, 0.62), 0.08, 0.05, STONE_ZONE, 5)
        _ellipsoid(m, (sx * 0.22, 0.22, 0.3), 0.14, 0.21, 0.24, STONE_ZONE, seg=6, rings=4)


def _hound(m) -> None:
    """A hound sitting upright: a lean deep chest, a long narrow head with
    long hanging ears, a collar."""
    _ellipsoid(m, (0.0, 0.44, 0.32), 0.26, 0.46, 0.25, STONE_ZONE, tilt=-0.2)
    _frustum_x(m, (0.0, 0.72, 0.38), (0.0, 0.98, 0.5), 0.14, 0.11, STONE_ZONE, 7)
    mk.lathe(m, [(0.145, 0.0), (0.145, 0.06)], 8, "metal", IRON_ZONE, (0.0, 0.8, 0.42))  # collar
    _ellipsoid(m, (0.0, 1.05, 0.56), 0.13, 0.13, 0.18, STONE_ZONE, tilt=0.25)
    _frustum_x(m, (0.0, 1.02, 0.7), (0.0, 0.95, 0.96), 0.085, 0.05, STONE_ZONE, 6)  # long muzzle
    _ellipsoid(m, (0.0, 0.97, 0.97), 0.04, 0.035, 0.03, EYE_ZONE, seg=6, rings=3)  # nose
    _eyes(m, 0.0, 1.11, 0.68, 0.07)
    for sx in (-1.0, 1.0):
        _flat_quad(m, [(sx * 0.1, 1.14, 0.5), (sx * 0.2, 1.1, 0.48), (sx * 0.24, 0.84, 0.54), (sx * 0.15, 0.82, 0.6)],
                   STONE_ZONE)  # long hanging ear
        _frustum_x(m, (sx * 0.1, 0.62, 0.44), (sx * 0.09, 0.02, 0.58), 0.05, 0.035, STONE_ZONE, 5)
        _ellipsoid(m, (sx * 0.18, 0.2, 0.26), 0.11, 0.2, 0.2, STONE_ZONE, seg=6, rings=4)


BUILD_BEAST = {"hart": _hart, "bear": _bear, "boar": _boar, "hound": _hound}


def beast_brazier(kind: str, drop: float = 2.5) -> Piece:
    """`kit_beast_brazier_<a-d>`: one of the four neutral beasts (a hart, b
    bear, c boar, d hound) sitting upright on a carved bracket on a bay
    shaft, an iron arm under its forepaws carrying a fire basket on three
    chains, the basket's bottom `drop` under the seat (the overhead rule:
    keep it 2.4 m over the floor). At most 1,500 triangles."""
    animal = BEASTS[kind] if kind in BEASTS else kind
    m = mk.Mesh()
    mark = m.mark()
    _bracket(m)
    BUILD_BEAST[animal](m)
    m.transform(mark, lambda p: (p[0] * BEAST_SCALE, p[1] * BEAST_SCALE, p[2] * BEAST_SCALE))
    _basket(m, drop)
    p = hall.from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(animal=animal, drop=drop, basket=(0.0, -drop, BRAZIER_ARM))
    # The basket's flames (docs/architecture.md "Rendering"), on the coals 0.04 under the rim.
    rim_y = -drop + 0.28
    p.meta["fires"] = [fire.fire((0.0, rim_y - 0.06, BRAZIER_ARM), fire.cluster_tongues(0.2, 0.9, count=5, seed=7),
                                 embers=5)]
    # A coarse camera shell (integration): the box round bracket, beast and
    # basket, open at the wall, so the follow camera at full zoom along the
    # side walls stops in front of the brazier instead of inside it.
    pts = [q for f in p.faces for q in f.pts]
    x0, x1 = min(q[0] for q in pts), max(q[0] for q in pts)
    y0, y1 = min(q[1] for q in pts), max(q[1] for q in pts)
    z1 = max(q[2] for q in pts)
    p.meta["collider"] = [
        [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        [(x0, y0, 0.0), (x0, y0, z1), (x0, y1, z1), (x0, y1, 0.0)],
        [(x1, y0, z1), (x1, y0, 0.0), (x1, y1, 0.0), (x1, y1, z1)],
        [(x0, y1, z1), (x1, y1, z1), (x1, y1, 0.0), (x0, y1, 0.0)],
        [(x0, y0, 0.0), (x1, y0, 0.0), (x1, y0, z1), (x0, y0, z1)],
    ]
    return p


# -- the hooded fireplace ---------------------------------------------------------------------

FIRE_JAMB = (0.25, 0.22)  # the jambs' width beside the opening and projection (<= 0.25 below 2.4 m)
FIRE_MANTEL = (0.6, 0.4)  # the lintel's height over the opening and projection
FIRE_HOOD_BASE = (2.4, 1.1)  # half width and projection of the hood's foot (over its moulded band)
FIRE_HOOD_TOP = (1.25, 0.45)  # ... at its top


def fireplace_hooded(width: float = 4.4, height: float = 3.6, depth: float = 0.7, hood_top: float = 9.0,
                     hood: str = "wall", blaze: float = 1.0) -> Piece:
    """`kit_fireplace_hooded_<w>x<h>`: a firebox recess `width` x `height`,
    `depth` deep from the floor (its hearth flush with the room's floor, so
    a find-spot glint in front of it stays visible), sooty inside, with a
    fire of logs on iron firedogs; stone jambs (at most 0.22 proud, the
    intrusion rule), a moulded lintel and a tapering stone hood from the
    lintel to `hood_top` (at most grid.FIREPLACE_HOOD_WIDTH wide and
    FIREPLACE_HOOD_PROUD off the wall, all above 2.4 m) carrying a carved
    shield. Brings one warm light (meta "lights") and a coarse camera shell
    of the hood (meta "collider"). At most 2,500 triangles. `hood` "wall"
    faces the hood in the wall stone (its courses run on from the wall
    behind); "web" in the vault web's smaller stone (castle_ceiling),
    for a dark wall where the wall stone would hide it head-on (common
    room D). `blaze` (1-1.4) scales the fire's flames,
    the warm glow on the hearth and its sparks, for a room whose fire is
    its focal point (common room D)."""
    if hood not in ("wall", "web"):
        raise ValueError(f"hood must be wall or web, not {hood!r}")
    if not 1.0 <= blaze <= 1.4:
        raise ValueError(f"blaze must be 1-1.4, not {blaze}")
    hood_tag = WALL if hood == "wall" else "castle_ceiling|world"
    p = Piece("")
    f = p.faces
    hw = width / 2.0
    jw, jp = FIRE_JAMB
    mh, mp = FIRE_MANTEL
    soot = G.palette_tag("stone", "soot")
    # The firebox: back, sides, its lintel's underside, the hearth.
    add_face(f, [(-hw, 0.0, -depth), (hw, 0.0, -depth), (hw, height, -depth), (-hw, height, -depth)], soot, None,
             (0.0, 0.0, 1.0))
    for sx in (-1.0, 1.0):
        x = sx * hw
        add_face(f, [(x, 0.0, 0.0), (x, 0.0, -depth), (x, height, -depth), (x, height, 0.0)], soot, None, (-sx, 0.0, 0.0))
    add_face(f, [(-hw, height, 0.0), (hw, height, 0.0), (hw, height, -depth), (-hw, height, -depth)], soot, None,
             (0.0, -1.0, 0.0))
    add_face(f, [(-hw, 0.0, 0.0), (hw, 0.0, 0.0), (hw, 0.0, -depth), (-hw, 0.0, -depth)], FLOOR, None, (0.0, 1.0, 0.0))
    # Jambs: stone pilasters with a base and a capital band.
    for sx in (-1.0, 1.0):
        x0, x1 = sorted((sx * hw, sx * (hw + jw)))
        geom.box(f, (x0, 0.0, 0.0), (x1, height, jp), trim("shaft"), skip=("-z", "-y", "+y"),
                 uv_fn=lambda side, pts: [(pt[1], min(0.7, (abs(pt[0]) - hw) / jw * 0.35 + pt[2])) for pt in pts])
        geom.box(f, (x0 - 0.02, 0.0, 0.0), (x1 + 0.02, 0.3, jp + 0.02), trim("capital_band"), skip=("-z", "-y"),
                 uv_fn=lambda side, pts: [(pt[0] + pt[2], pt[1] / 0.3 * 0.3) for pt in pts])
    # The lintel: a moulded beam over the opening and the jambs.
    lx = hw + jw + 0.03  # (the lintel and the hood's band stay within grid.FIREPLACE_HOOD_WIDTH)
    geom.box(f, (-lx, height, 0.0), (lx, height + mh, mp), trim("cornice"), skip=("-z",),
             uv_fn=lambda side, pts: [(pt[0] + pt[2], min(0.7, (pt[1] - height) / mh * 0.7)) for pt in pts])
    # The hood: a moulded band, then a tapering stone hood up to hood_top.
    y0 = height + mh
    bx, bp = FIRE_HOOD_BASE
    tx, tp = FIRE_HOOD_TOP
    band = 0.4
    geom.box(f, (-bx - 0.1, y0, 0.0), (bx + 0.1, y0 + band, bp + 0.1), trim("string"), skip=("-z",),
             uv_fn=lambda side, pts: [(pt[0] + pt[2], (pt[1] - y0) / band * 0.3) for pt in pts])
    yb, yt = y0 + band, hood_top
    front = [(-bx, yb, bp), (bx, yb, bp), (tx, yt, tp), (-tx, yt, tp)]
    add_face(f, front, hood_tag, None, (0.0, bp - tp, yt - yb))
    for sx in (-1.0, 1.0):
        side = [(sx * bx, yb, 0.0), (sx * bx, yb, bp), (sx * tx, yt, tp), (sx * tx, yt, 0.0)]
        add_face(f, side, hood_tag, None, (sx * (yt - yb), sx * (bx - tx), 0.0))
    # Rolls up the hood's front corners and a band across it.
    k = (yt - 0.3 - yb) / (yt - yb)  # up to the capping band
    for sx in (-1.0, 1.0):
        _edge_roll(f, (sx * bx, yb, bp), (sx * (bx + (tx - bx) * k), yt - 0.3, bp + (tp - bp) * k), 0.09)
    ym = yb + (yt - yb) * 0.12
    xm = bx + (tx - bx) * 0.12
    zm = bp + (tp - bp) * 0.12
    geom.box(f, (-xm - 0.04, ym - 0.14, 0.0), (xm + 0.04, ym + 0.14, zm + 0.06), trim("string"), skip=("-z",),
             uv_fn=lambda side, pts: [(pt[0] + pt[2], (pt[1] - ym + 0.14) / 0.28 * 0.3) for pt in pts])
    # A capping band (its top closes the hood) at the top of the hood.
    geom.box(f, (-tx - 0.08, yt - 0.3, 0.0), (tx + 0.08, yt, tp + 0.08), trim("string"), skip=("-z",),
             uv_fn=lambda side, pts: [(pt[0] + pt[2], (pt[1] - yt + 0.3) / 0.3 * 0.3) for pt in pts])
    # A carved shield on the hood's face, just proud of its slope.
    sh_w, sh_h = 1.3, 1.5
    sy0 = yb + (yt - yb) * 0.28
    _shield_on_slope(f, sh_w, sh_h, sy0, (yb, bp), (yt, tp))
    # The fire: two iron firedogs, three logs, flames and the coal bed.
    m = mk.Mesh()
    for sx in (-1.0, 1.0):
        x = sx * 0.9
        mk.box(m, (x - 0.04, 0.0, -0.55), (x + 0.04, 0.32, -0.12), "metal", IRON_ZONE, skip=("-y",))
        mk.box(m, (x - 0.05, 0.32, -0.16), (x + 0.05, 0.62, -0.08), "metal", IRON_ZONE, skip=("-y",))
        mk.lathe(m, [(0.0, 0.0), (0.06, 0.04), (0.0, 0.1)], 6, "metal", IRON_ZONE, (x, 0.62, -0.12))
    for (xa, za), (xb, zb), yy in (((-1.3, -0.42), (1.3, -0.38), 0.36), ((-1.1, -0.26), (1.2, -0.3), 0.38),
                                    ((-0.9, -0.5), (1.0, -0.2), 0.56)):
        mk.rod(m, (xa, yy, za), (xb, yy, zb), 0.11, 6, "wood", LOG_ZONE)
    # The coal bed: dark ash under the logs (the flames and the warm glow on the hearth are the
    # client's animated fire, meta "fires"; docs/architecture.md "Rendering").
    bed = [(-1.4, 0.02, -0.62), (1.4, 0.02, -0.62), (1.4, 0.02, -0.1), (-1.4, 0.02, -0.1)]
    m.poly(bed if mk.newell(bed)[1] > 0 else list(reversed(bed)), "stone", zone=SOOT_ZONE)
    f.extend(hall.from_mesh(m, ZONES).faces)
    fires = [fire.fire((0.0, 0.3, -0.36), fire.hearth_tongues(2.6 * min(blaze, 1.15), 1.6 * blaze, depth=0.3, rows=2,
                                                              count=round(10 * min(blaze, 1.2)), seed=3),
                       pool=round(2.2 * blaze, 3), embers=round(10 * blaze))]
    p.meta.update(width=width, height=height, depth=depth, hood=(height, hood_top, 2 * (bx + 0.1), bp + 0.1),
                  wall=(-(hw + jw), hw + jw, 0.0, height), fires=fires,
                  lights=[{"id": "fire", "pos": [0.0, 0.9, 0.4], "glow": "fire", "intensity": 30.0, "range": 12.0}],
                  collider=_hood_shell(height, hood_top, bx + 0.1, bp + 0.1, tx, tp))
    return p


def _edge_roll(f, a, b, r: float) -> None:
    """A square roll moulding along the hood's sloping front corner from a
    to b (castle_trim `shaft` strip, rotated: s up the edge)."""
    ln = math.dist(a, b)
    d = tuple((b[k] - a[k]) / ln for k in range(3))
    side = (1.0 if a[0] > 0 else -1.0, 0.0, 0.0)
    out = (0.0, -d[2], d[1])  # square to the edge in the y-z plane, out of the face
    n = math.hypot(out[1], out[2])
    out = (0.0, out[1] / n, out[2] / n)
    if out[2] < 0:
        out = (0.0, -out[1], -out[2])
    corners = []
    for su, so in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
        corners.append(tuple(side[k] * su * r * 0.5 + out[k] * (so + 1.0) * r * 0.5 for k in range(3)))
    for i in range(4):
        c0, c1 = corners[i], corners[(i + 1) % 4]
        pts = [tuple(a[k] + c0[k] for k in range(3)), tuple(a[k] + c1[k] for k in range(3)),
               tuple(b[k] + c1[k] for k in range(3)), tuple(b[k] + c0[k] for k in range(3))]
        mid = tuple((c0[k] + c1[k]) / 2 - (side[k] * 0.0 + out[k] * r * 0.5) for k in range(3))
        add_face(f, pts, trim("shaft"), [(0.0, i * 0.17), (0.0, (i + 1) * 0.17), (ln, (i + 1) * 0.17), (ln, i * 0.17)],
                 mid)


def _shield_on_slope(f, w, h, y0, bot, topp) -> None:
    """A shield on the hood's sloped front: z follows the slope, 0.04 proud."""
    (yb, zb), (yt, zt) = bot, topp

    def z_at(y):
        return zb + (zt - zb) * (y - yb) / (yt - yb) + 0.04

    hw = w / 2.0
    n = 5
    pts = [(-hw, h), (-hw, h * 0.5)]
    for i in range(1, n):
        t = i / n
        pts.append((-hw * math.cos(t * math.pi / 2), h * 0.5 * (1 - math.sin(t * math.pi / 2))))
    pts.append((0.0, 0.0))
    for i in range(n - 1, 0, -1):
        t = i / n
        pts.append((hw * math.cos(t * math.pi / 2), h * 0.5 * (1 - math.sin(t * math.pi / 2))))
    pts += [(hw, h * 0.5), (hw, h)]
    outline = list(reversed(pts))
    uv = geom.bbox_uv(outline)
    nrm = (0.0, zb - zt, yt - yb)
    add_face(f, [(x, y0 + y, z_at(y0 + y)) for x, y in outline], fit("crest_blank"), uv, nrm)
    m = len(outline)
    for i in range(m):
        (xa, ya), (xb, yb2) = outline[i], outline[(i + 1) % m]
        a0, b0 = (xa, y0 + ya), (xb, y0 + yb2)
        q = [(a0[0], a0[1], z_at(a0[1]) - 0.04), (b0[0], b0[1], z_at(b0[1]) - 0.04), (b0[0], b0[1], z_at(b0[1])),
             (a0[0], a0[1], z_at(a0[1]))]
        add_face(f, q, fit("crest_blank"), [(0.02, 0.02), (0.04, 0.02), (0.04, 0.04), (0.02, 0.04)],
                 (yb2 - ya, -(xb - xa), 0.0))


def _hood_shell(y0, y1, bx, bp, tx, tp) -> list:
    """The hood's coarse camera shell: front, sides, top and bottom."""
    return [
        [(-bx, y0, bp), (bx, y0, bp), (tx, y1, tp), (-tx, y1, tp)],
        [(-bx, y0, 0.0), (-bx, y0, bp), (-tx, y1, tp), (-tx, y1, 0.0)],
        [(bx, y0, bp), (bx, y0, 0.0), (tx, y1, 0.0), (tx, y1, tp)],
        [(-tx, y1, 0.0), (-tx, y1, tp), (tx, y1, tp), (tx, y1, 0.0)],
        [(-bx, y0, 0.0), (bx, y0, 0.0), (bx, y0, bp), (-bx, y0, bp)],
    ]


# -- the end window -------------------------------------------------------------------------


def end_window(width: float = 9.0, height: float = 12.0, depth: float = G.EXTERIOR_SHELL, glass_at: float = 0.6,
               lights: int = 6, tiers: int = 4, outer: bool = True) -> Piece:
    """`kit_window_end_<w>x<h>`: the great end window over the dais, through
    a wall `depth` thick (spandrels on both faces, reveals and sill, a
    moulded frame on the inside face, as the lancets): `lights` lights
    between mullions, `tiers` tiers between transoms below the springing;
    in the pointed head three sub-arches over pairs of lights, a lancet head
    over every light and an oculus over each gap between the sub-arches and
    over the middle one, in stone in front of the glass (the head shows the
    painted tracery tile). y = 0 is the sill. At most 4,000 triangles."""
    p = Piece("")
    f = p.faces
    spring = height - G.ARCH_RISE * width
    half = width / 2.0
    zg = -depth * glass_at
    zf = zg + 0.1
    seg = openings.SEG
    openings._spandrels(f, width, height, spring, 0.0, 1.0)
    if outer:
        openings._spandrels(f, width, height, spring, -depth, -1.0)
    openings._soffit(f, width, spring, -depth, 0.0)
    openings._jambs(f, width, 0.0, spring, -depth, 0.0)
    add_face(f, [(-half, 0.0, 0.0), (half, 0.0, 0.0), (half, 0.0, -depth), (-half, 0.0, -depth)], WALL, None,
             (0.0, 1.0, 0.0))
    mull = 0.22
    tran = 0.18
    light_w = (width - (lights - 1) * mull) / lights
    tier_h = (spring - (tiers - 1) * tran) / tiers
    # Glass: one pane per light and tier, the tracery tile in the head.
    for i in range(lights):
        x0 = -half + i * (light_w + mull)
        for j in range(tiers):
            y0 = j * (tier_h + tran)
            openings._glass(f, [(x0, y0), (x0 + light_w, y0), (x0 + light_w, y0 + tier_h), (x0, y0 + tier_h)], zg,
                            "light_tall")
    head = [(-half, spring), (half, spring)] + list(reversed(geom.pointed_arc(width, spring, seg)))[1:-1]
    openings._glass(f, head, zg, "head_tracery")
    frame = trim("window_frame")
    # Mullions to the springing and transom bars between the tiers.
    for i in range(1, lights):
        xm = -half + i * (light_w + mull) - mull / 2
        geom.box(f, (xm - mull / 2, 0.0, zg), (xm + mull / 2, spring, zf), frame, skip=("-z", "-y", "+y"),
                 uv_fn=lambda side, pts, xm=xm: [(pt[1], (pt[0] - xm + mull / 2) / mull * 0.3) for pt in pts])
    for j in range(1, tiers):
        yt = j * (tier_h + tran) - tran
        for i in range(lights):
            x0 = -half + i * (light_w + mull)
            geom.box(f, (x0, yt, zg), (x0 + light_w, yt + tran, zf - 0.02), frame, skip=("-z", "-x", "+x"),
                     uv_fn=lambda side, pts, yt=yt: [(pt[0], (pt[1] - yt) / tran * 0.3) for pt in pts])
    # Head tracery.
    pair_w = (width - 2 * mull) / 3.0
    centres = [-(pair_w + mull), 0.0, pair_w + mull]
    for cx in centres:
        inner = [(x + cx, y) for x, y in geom.pointed_arc(pair_w, spring, 10)]
        outer = [(x + cx, y) for x, y in geom.pointed_arc(pair_w, spring, 10, grow=0.1)]
        openings._band(f, inner, outer, zg, 0.1, clip_half=half)
        for j in (-1.0, 1.0):
            lx = cx + j * (mull / 2 + light_w / 2)
            inner = [(x + lx, y) for x, y in geom.pointed_arc(light_w, spring, 6)]
            outer = [(x + lx, y) for x, y in geom.pointed_arc(light_w, spring, 6, grow=0.08)]
            openings._band(f, inner, outer, zg, 0.08, clip_half=half)
    pair_apex = spring + G.ARCH_RISE * pair_w
    top = G.arch_height(width, height, 0.0)
    oculi = []
    wanted = [(0.0, (top + pair_apex) / 2.0, min((top - pair_apex) * 0.38, pair_w * 0.4))]
    wanted += [(sx * (pair_w + mull), None, 0.4) for sx in (-1.0, 1.0)]  # over the side sub-arches
    for cx, cy, r in wanted:
        # Shrink it until it clears the main arch's intrados by 0.15 m (and
        # a side one sits clear above its sub-arch's band).
        while r > 0.2:
            y = cy if cy is not None else pair_apex + 0.1 + 0.13 + r + 0.04
            if G.arch_height(width, height, min(abs(cx) + r + 0.2, half)) >= y + r + 0.15:
                break
            r -= 0.02
        if r > 0.2:
            oculi.append((cx, y, round(r, 3)))
    for cx, cy, r in oculi:
        n = 16
        inner = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]
        outer = [(cx + (r + 0.13) * math.cos(2 * math.pi * k / n), cy + (r + 0.13) * math.sin(2 * math.pi * k / n))
                 for k in range(n + 1)]
        openings._band(f, inner, outer, zg, 0.1)
    # The frame round the inside face, as the lancets (its reach: half + 0.26).
    openings._surround(f, width, spring, 0.0, 0.12, 0.07, "window_frame", 0.3)
    inner = [(-half - 0.12, 0.0)] + geom.pointed_arc(width, spring, seg, grow=0.12) + [(half + 0.12, 0.0)]
    outer_c = [(-half - 0.26, 0.0)] + geom.pointed_arc(width, spring, seg, grow=0.26) + [(half + 0.26, 0.0)]
    geom.polyline_band(f, inner, outer_c, 0.0, 0.12, frame, 0.3)
    p.meta.update(spring=spring, lights=lights, tiers=tiers, glass=zg, oculi=oculi)
    return p
