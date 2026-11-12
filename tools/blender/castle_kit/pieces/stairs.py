"""Stairs and their edges: the generated flight (one per stair surface) and
the gothic balustrade swept along railing runs.

A flight is built in its own frame: x across the stair (0..width), z up
the flight (0..length, the walkable ramp rising from y = 0 at z = 0 to
`rise` at z = length). Every tread's top is at the ramp's height over the
tread's middle, so the ramp passes through each tread centre (grid
TREAD_RAMP_TOLERANCE); `meta["treads"]` lists (z middle, y top) for the
lint.
"""

from __future__ import annotations

from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece, add_face

FLOOR = "castle_floor|world"
WALL = "castle_wall|world"
CEILING = "castle_ceiling|world"
NOSING = 0.03
NOSE_H = 0.04
# Castle kit M2 review: the Marble Staircase's tread tops take the veined
# marble band of their nosing strip (v in strip metres, clear of the strip's
# edge bands), not the hall's flagstones: from the floor and in close-ups
# the flight read as flagstone steps with white stripes.
MARBLE_TREAD_V = (0.05, 0.17)


def trim(strip: str) -> str:
    return f"castle_trim|trim:{strip}"


def _split(x0: float, x1: float, step: float) -> list[tuple[float, float]]:
    """x0..x1 cut every `step` metres (strips with `cols` cannot wrap)."""
    out, x = [], x0
    while x < x1 - 1e-6:
        out.append((x, min(x1, x + step)))
        x += step
    return out


def flight(width: float, length: float, rise: float, solid: bool = True, strip: str = "nosing_stone",
           riser: float = G.RISER) -> Piece:
    """`kit_stair_flight`: treads with a nosing, risers, and for a flight
    that does not rest on the floor beside it (`solid` False) stepped
    stringer sides and a sloping soffit."""
    p = Piece("")
    f = p.faces
    n = max(1, round(rise / riser))
    d, r = length / n, rise / n
    u_m = 2.4  # nosing strips' u repeat (castle_textures.json)
    treads = []
    prev = 0.0
    for i in range(n):
        a, b = i * d, (i + 1) * d
        top = (i + 0.5) * r
        treads.append(((a + b) / 2, top))
        za = a - NOSING if i > 0 else a
        if strip == "nosing_marble":
            v0, v1 = MARBLE_TREAD_V
            for x0, x1 in _split(0.0, width, u_m):
                add_face(f, [(x0, top, za), (x1, top, za), (x1, top, b), (x0, top, b)], trim(strip),
                         [(0.0, v1), (x1 - x0, v1), (x1 - x0, v0), (0.0, v0)], (0.0, 1.0, 0.0))
        else:
            add_face(f, [(0.0, top, za), (width, top, za), (width, top, b), (0.0, top, b)], FLOOR, None, (0.0, 1.0, 0.0))
        for x0, x1 in _split(0.0, width, u_m):
            # Nosing front (the top band of the strip) and the riser below it.
            if i > 0:
                add_face(f, [(x0, top - NOSE_H, za), (x1, top - NOSE_H, za), (x1, top, za), (x0, top, za)], trim(strip),
                         [(x0 - x0, 0.21), (x1 - x0, 0.21), (x1 - x0, 0.25), (x0 - x0, 0.25)], (0.0, 0.0, -1.0))
                add_face(f, [(x0, top - NOSE_H, a), (x1, top - NOSE_H, a), (x1, top - NOSE_H, za), (x0, top - NOSE_H, za)],
                         trim(strip), [(0.0, 0.2), (x1 - x0, 0.2), (x1 - x0, 0.21), (0.0, 0.21)], (0.0, -1.0, 0.0))
            low = prev
            high = top - NOSE_H if i > 0 else top
            if high - low > 1e-4:
                add_face(f, [(x0, low, a), (x1, low, a), (x1, high, a), (x0, high, a)], trim(strip),
                         [(0.0, 0.0), (x1 - x0, 0.0), (x1 - x0, 0.2), (0.0, 0.2)], (0.0, 0.0, -1.0))
        if not solid:
            for x, sx in ((0.0, -1.0), (width, 1.0)):
                bot = top - r - 0.3
                pts = [(x, bot, a), (x, bot + r, b), (x, top, b), (x, top, za)]
                add_face(f, pts, WALL, None, (sx, 0.0, 0.0))
        prev = top
    # The last half riser up to the landing.
    if rise - prev > 1e-4:
        for x0, x1 in _split(0.0, width, u_m):
            add_face(f, [(x0, prev, length), (x1, prev, length), (x1, rise, length), (x0, rise, length)], trim(strip),
                     [(0.0, 0.0), (x1 - x0, 0.0), (x1 - x0, 0.2), (0.0, 0.2)], (0.0, 0.0, -1.0))
    if not solid:
        # The soffit: a slope under the steps, 0.3 m below the tread fronts.
        add_face(f, [(0.0, -0.3 - r / 2, 0.0), (width, -0.3 - r / 2, 0.0), (width, rise - 0.3 - r / 2, length),
                     (0.0, rise - 0.3 - r / 2, length)], CEILING, None, (0.0, -1.0, 0.0))
    p.meta.update(treads=treads, steps=n, riser=r, going=d)
    return p


def balustrade(length: float, rise: float = 0.0, height: float = 1.0, rail: float = 0.25, pitch: float = 0.3) -> Piece:
    """`kit_balustrade_gothic`: a pierced stone parapet along x (0..length),
    z = 0 at the floor's edge and the rail 0.25 m into the walkable side
    (the intrusion limit), rising `rise` over its length on a stair. LOD0:
    base, coping, slim piers every `pitch` with pointed heads; LOD1: base,
    coping and a flat panel painted with the arcade (balustrade_arcade)."""
    p = Piece("")
    k = rise / length if length > 0 else 0.0

    def y(x):
        return k * x

    base_h, cope_h = 0.18, 0.15
    z0, z1 = 0.0, rail
    rails: list = []
    for prof, tag, metres in (
        ([(z1, 0.0), (z1, base_h), (z0, base_h), (z0, 0.0)], trim("string"), 0.3),
        ([(z1, height - cope_h), (z1, height), (z0 - 0.03, height), (z0 - 0.03, height - cope_h + 0.03),
          (z0, height - cope_h)], trim("string"), 0.3),
    ):
        # Closed loops in (z, y), counter-clockwise like geom.sweep's open
        # profiles; the coping overhangs the drop side only (z < 0).
        loop: list = []
        geom.sweep(loop, prof, 0.0, length, tag, metres, ends=("cap", "cap"), y_at=y, closed=True)
        rails.extend(loop)
    p.faces.extend(rails)
    # LOD0: piers and pointed heads.
    mid0, mid1 = 0.08, rail - 0.08
    bays = max(1, round(length / pitch))
    step = length / bays
    for i in range(bays + 1):
        x = i * step
        xa, xb = max(0.0, x - 0.035), min(length, x + 0.035)
        ya, yb = y(x) + base_h, y(x) + height - cope_h
        geom.box(p.faces, (xa, ya, mid0), (xb, yb, mid1), trim("window_frame"), skip=("+y", "-y"),
                 uv_fn=lambda side, pts: [(pt[1], (pt[0] - xa) / 0.07 * 0.3) for pt in pts])
    for i in range(bays):
        xa, xb = i * step + 0.035, (i + 1) * step - 0.035
        if xb - xa < 0.05:
            continue
        top = height - cope_h
        spring = top - 0.12
        xm = (xa + xb) / 2
        for zz, sgn in ((mid1, 1.0), (mid0, -1.0)):
            pts_l = [(xa, y(xa) + spring, zz), (xm, y(xm) + top - 0.01, zz), (xa, y(xa) + top, zz)]
            pts_r = [(xb, y(xb) + spring, zz), (xb, y(xb) + top, zz), (xm, y(xm) + top - 0.01, zz)]
            for pts in (pts_l, pts_r):
                add_face(p.faces, pts, trim("window_frame"), [(pt[0], 0.15) for pt in pts], (0.0, 0.0, sgn), anchor=True)
    # LOD1: the rails and a painted panel.
    lod = list(rails)
    for zz, sgn in ((mid1, 1.0), (mid0, -1.0)):
        pts = [(0.0, y(0.0) + base_h, zz), (length, y(length) + base_h, zz), (length, y(length) + height - cope_h, zz),
               (0.0, y(0.0) + height - cope_h, zz)]
        uv = [(pt[0], (pt[1] - y(pt[0])) / height) for pt in pts]
        add_face(lod, pts, trim("balustrade_arcade"), uv, (0.0, 0.0, sgn), anchor=True)
    p.lod1 = lod
    # Castle kit M4 (M2 leftover 3): a coarse camera shell, front, back and
    # top, so the camera cannot settle in or behind the parapet.
    zb = z0 - 0.03
    p.meta["collider"] = [
        [(0.0, y(0.0), z1), (length, y(length), z1), (length, y(length) + height, z1), (0.0, y(0.0) + height, z1)],
        [(length, y(length), zb), (0.0, y(0.0), zb), (0.0, y(0.0) + height, zb), (length, y(length) + height, zb)],
        [(0.0, y(0.0) + height, z1), (length, y(length) + height, z1), (length, y(length) + height, zb),
         (0.0, y(0.0) + height, zb)],
    ]
    p.meta.update(rail=rail, height=height)
    return p
