"""Pieces (docs/art/castle-kit.md): the Owl Loft's
tower on its knoll (N10), its perches, straw and owl mail desk; the seventh-floor
east corridor's invented tapestry (N11); the dungeon cellars' barrels and
wine racks (N12).

Frames as geom.py: x along the wall (to the right seen from the room), y
up, z out of the wall face into the room; wall pieces have their back on
the wall plane z = 0, floor pieces their origin at the bottom centre. The
Owl Loft's shell is the exception: its origin is the tower's plan centre at
y = 0 (world heights), x and z as the world's (placed at yaw 0).

Everything is our own invented design (no real tapestry).
Colours: palette names only (grid.palette_tag; the tapestry's three cloth
slots appended under `_doc_p3c_wings_b` in castle_textures.json), the stone
of the cell's own wall family (world UVs) inside, the castle's outside stone
(grid.P3A_EXTERIOR_STONE, put on its `@ext` clone by assemble.py) outside and the slate `castle_roof`; no colour literal here
(PaletteRuleTests)."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall import from_mesh

WALL = "castle_wall|world"
WALL_OUT = f"{G.P3A_EXTERIOR_STONE}|world"  # the outside stone; assemble.py puts it on its @ext clone
ROOF = "castle_roof|world"
FLOOR = "castle_floor|world"

# Zone -> palette name (castle_textures.json `palette`; each [tile, "@name"] a props slot).
OAK, OAK_AGED, IRON, STRAW, STRAW_DARK, BRASS, LETTER, GILT, WINE, BOTTLE, BOTTLE_DARK, SOOT = range(1, 13)
NAVY, OCHRE, CREAM, OWL, OWL_PALE, TWINE = range(13, 19)
ZONES = {OAK: "oak", OAK_AGED: "oak_aged", IRON: "iron", STRAW: "food_bread", STRAW_DARK: "parcel_tan", BRASS: "brass",
         LETTER: "letter_white", GILT: "gilt", WINE: "wine_red", BOTTLE: "map_green", BOTTLE_DARK: "ink_blue",
         SOOT: "soot", NAVY: "tapestry_navy", OCHRE: "tapestry_ochre", CREAM: "tapestry_cream", OWL: "owl_brown",
         OWL_PALE: "owl_pale", TWINE: "tapestry_wine"}


def _finish(p: Piece, m: mk.Mesh) -> Piece:
    p.faces.extend(from_mesh(m, ZONES, walkthrough=p.walkthrough).faces)
    return p


# =====================================================================================
# N10: the Owl Loft's tower (kit_owl_loft_shell)
# =====================================================================================

OWL_LOFT_HALF = 6.0  # the tower's inside half width (the layout: 12 x 12 m)
OWL_LOFT_WALL = 1.0  # the walls' thickness (the layout's owl_loft_wall_* solids)
OWL_LOFT_EAVE = 0.8  # the roof's overhang beyond the outer faces
OWL_LOFT_ROOF = 13.0  # the spire's height over the eaves
DOOR = (4.0, 5.0)  # the east door: width (the layout's 4 m gap), apex height
SLIT = (0.5, 2.2)
LOFT_OPENING = (2.2, 4.0)
LOFT_SILL = 0.9  # the loft's openings' sill above the loft floor
LOFT_PITCH = 3.6  # three openings a side, this far apart
BAND = (0.3, 0.7)  # the plinth and eaves bands: proud, height
ARC_N = 6  # arc segments per side of an opening's pointed head


def _lancet_top(w: float, h: float, n: int = ARC_N) -> list[tuple[float, float]]:
    """The head of a pointed opening `w` wide, its apex `h` over the sill:
    2n + 1 points (t, y) from the left jamb's springing over the apex to the
    right jamb's."""
    return geom.pointed_arc(w, h - G.ARCH_RISE * w, n)


class _Side:
    """One side of the square tower: `t` along it, `c` the plane's distance
    from the centre; `inward` the unit normal into the tower."""

    def __init__(self, name: str):
        self.name = name
        self.axis = "z" if name in ("n", "s") else "x"  # the plane is z = const (n, s) or x = const (w, e)
        self.sign = -1.0 if name in ("n", "w") else 1.0  # the plane's side of the centre

    def pt(self, t: float, y: float, c: float):
        return (t, y, self.sign * c) if self.axis == "z" else (self.sign * c, y, t)

    def normal(self, outward: bool):
        s = self.sign if outward else -self.sign
        return (0.0, 0.0, s) if self.axis == "z" else (s, 0.0, 0.0)

    def tdir(self, dt: float, dy: float):
        return (dt, dy, 0.0) if self.axis == "z" else (0.0, dy, dt)


def _holed_face(faces, side: _Side, c: float, t0: float, t1: float, y0: float, y1: float, holes, tag: str,
                outward: bool) -> None:
    """The plane of `side` at distance c from t0 to t1 and y0 to y1, with
    pointed openings `holes` [(t centre, width, sill y, apex height)] cut
    out (column by column: whole strips between them, the sill's strip and
    the head's strips over each)."""
    n = side.normal(outward)

    def quad(ta, tb, ya, yb):
        if tb - ta > 1e-6 and yb - ya > 1e-6:
            add_face(faces, [side.pt(ta, ya, c), side.pt(tb, ya, c), side.pt(tb, yb, c), side.pt(ta, yb, c)], tag, None, n)

    cur = t0
    for tc, w, sill, h in sorted(holes):
        a, b = tc - w / 2.0, tc + w / 2.0
        quad(cur, a, y0, y1)
        quad(a, b, y0, sill)
        top = [(tc + x, sill + y) for x, y in _lancet_top(w, h)]
        for (ta, ya), (tb, yb) in zip(top, top[1:]):
            pts = [side.pt(ta, ya, c), side.pt(tb, yb, c), side.pt(tb, y1, c), side.pt(ta, y1, c)]
            add_face(faces, pts, tag, None, n)
        cur = b
    quad(cur, t1, y0, y1)


def _reveals(faces, side: _Side, c0: float, c1: float, holes, tag: str, floor: float | None = None) -> None:
    """The openings' reveals through the wall from c0 to c1: jambs, sill and
    the pointed soffit, each facing into the opening (a door's sill at
    `floor` is the floor's: the flagstones)."""
    for tc, w, sill, h in holes:
        a, b = tc - w / 2.0, tc + w / 2.0
        top = [(tc + x, sill + y) for x, y in _lancet_top(w, h)]
        spring = top[0][1]
        for t, d in ((a, 1.0), (b, -1.0)):
            add_face(faces, [side.pt(t, sill, c0), side.pt(t, sill, c1), side.pt(t, spring, c1), side.pt(t, spring, c0)],
                     tag, None, side.tdir(d, 0.0))
        if floor is None or sill > floor + 1e-6:
            add_face(faces, [side.pt(a, sill, c0), side.pt(b, sill, c0), side.pt(b, sill, c1), side.pt(a, sill, c1)],
                     tag, None, (0.0, 1.0, 0.0))
        for (ta, ya), (tb, yb) in zip(top, top[1:]):
            mt, my = (ta + tb) / 2.0, (ya + yb) / 2.0
            add_face(faces, [side.pt(ta, ya, c0), side.pt(tb, yb, c0), side.pt(tb, yb, c1), side.pt(ta, ya, c1)],
                     tag, None, side.tdir(tc - mt, spring - my))


# Per side: the slit windows [(t, sill)] climbing with the stair (on the loft arches' piers,
# never in a column another opening takes) and the loft's open arches' centres.
OWL_LOFT_SLITS = {"n": ((1.8, 9.0),), "w": ((-1.8, 11.0), (1.8, 18.6)), "s": ((1.8, 13.0),), "e": ((5.3, 12.4),)}
OWL_LOFT_ARCHES = {"n": (-LOFT_PITCH, 0.0, LOFT_PITCH), "w": (-LOFT_PITCH, 0.0, LOFT_PITCH),
                 "s": (-LOFT_PITCH, 0.0, LOFT_PITCH), "e": (-LOFT_PITCH, LOFT_PITCH)}  # (the east door below the middle)


def owl_loft_holes(side: str, floor: float, loft: float) -> list[tuple[float, float, float, float]]:
    """The openings of one side [(t, width, sill, apex height)]: the east
    door, slit windows climbing with the stair, the open arches round the
    loft (three a side, two over the door); no two share a column."""
    out = []
    if side == "e":
        out.append((0.0, DOOR[0], floor, DOOR[1]))
    out += [(t, SLIT[0], y, SLIT[1]) for t, y in OWL_LOFT_SLITS[side]]
    out += [(t, LOFT_OPENING[0], loft + LOFT_SILL, LOFT_OPENING[1]) for t in OWL_LOFT_ARCHES[side]]
    spans = sorted((t - w / 2.0, t + w / 2.0) for t, w, _, _ in out)
    if any(b0 < a1 + 0.02 for (_, a1), (b0, _) in zip(spans, spans[1:])):
        raise ValueError(f"owl_loft_holes: two openings of side {side} share a column")
    return out


def _band(faces, h_out: float, y0: float, y1: float, gaps: dict, tag: str) -> None:
    """A square band BAND proud of the outer faces from y0 to y1 (the n and s
    runs full length, the w and e ones between them), broken at `gaps`
    {side: [(t0, t1)]} (the door)."""
    p = BAND[0]
    for name in ("n", "s", "w", "e"):
        side = _Side(name)
        reach = h_out + p if side.axis == "z" else h_out
        spans = [(-reach, reach)]
        for g0, g1 in gaps.get(name, []):
            spans = [s for a, b in spans for s in ((a, min(b, g0)), (max(a, g1), b)) if s[1] - s[0] > 1e-6]
        for a, b in spans:
            lo = side.pt(a, y0, h_out)
            hi = side.pt(b, y1, h_out + p)
            box_lo = tuple(min(u, v) for u, v in zip(lo, hi))
            box_hi = tuple(max(u, v) for u, v in zip(lo, hi))
            skip = ("-x",) if name == "e" else ("+x",) if name == "w" else ("-z",) if name == "s" else ("+z",)
            geom.box(faces, box_lo, box_hi, tag, skip=skip)


def owl_loft_shell(floor: float = 4.14, loft: float = 22.14, ceiling: float = 29.14, eave: float = 32.14,
                 base: float = 3.2) -> Piece:
    """`kit_owl_loft_shell`: the Owl Loft's square tower round the layout's
    spiral and loft, its origin at the plan centre (y = 0: world heights):
    walls OWL_LOFT_WALL thick (the layout's `owl_loft_wall_*` solids) with their
    inside faces in the cell's stone from the knoll's top (`floor`) to the
    loft's flat ceiling, their outside faces and reveals in the outside stone
    (WALL_OUT) from just under the ground (`base`) to the eaves; the east door (a pointed arch
    in the layout's 4 m gap), slit windows climbing with the stair, three
    open pointed arches a side round the loft (no glass: the owls come and
    go); a plinth band and an eaves band; a steep four-sided slate spire
    (castle_roof) with an iron finial; flagstones on the ground floor and
    over the door's sill. Walk-through for the lint (the layout's solids
    keep walkers out of the walls; the ground floor is the grounds' own
    surface). LOD1 (the cell's outside seen from the grounds): the four
    outer faces unholed and the spire."""
    p = Piece("", walkthrough=True)
    f = p.faces
    h_in, h_out = OWL_LOFT_HALF, OWL_LOFT_HALF + OWL_LOFT_WALL
    for name in ("n", "s", "w", "e"):
        side = _Side(name)
        holes = owl_loft_holes(name, floor, loft)
        _holed_face(f, side, h_in, -h_in, h_in, floor, ceiling, holes, WALL, outward=False)
        _holed_face(f, side, h_out, -h_out, h_out, base, eave, holes, WALL_OUT, outward=True)
        _reveals(f, side, h_in, h_out, holes, WALL_OUT, floor)
    # Bands: the plinth (broken at the door) and under the eaves.
    _band(f, h_out, base, floor + 0.5, {"e": [(-DOOR[0] / 2.0, DOOR[0] / 2.0)]}, WALL_OUT)
    _band(f, h_out, eave - 1.5, eave - 0.8, {}, WALL_OUT)
    # The flagstones: the ground floor (the stair's foot and the space under it) and the door's sill.
    y = floor + 0.02
    add_face(f, L_quad(-h_in, -h_in, h_in, h_in, y), FLOOR, None, (0.0, 1.0, 0.0))
    add_face(f, L_quad(h_in, -DOOR[0] / 2.0, h_out, DOOR[0] / 2.0, y), FLOOR, None, (0.0, 1.0, 0.0))
    # The eaves' soffit and the spire.
    r, apex = h_out + OWL_LOFT_EAVE, eave + OWL_LOFT_ROOF
    corners = [(-r, -r), (r, -r), (r, r), (-r, r)]
    for (xa, za), (xb, zb) in zip(corners, corners[1:] + corners[:1]):
        k = h_out / r
        add_face(f, [(xa, eave, za), (xb, eave, zb), (xb * k, eave, zb * k), (xa * k, eave, za * k)], ROOF, None,
                 (0.0, -1.0, 0.0))
        mx, mz = (xa + xb) / 2.0, (za + zb) / 2.0
        add_face(f, [(xa, eave, za), (xb, eave, zb), (0.0, apex, 0.0)], ROOF, None, (mx, r * r / OWL_LOFT_ROOF, mz))
        # The eaves' fascia, a hand deep.
        add_face(f, [(xa, eave - 0.18, za), (xb, eave - 0.18, zb), (xb, eave, zb), (xa, eave, za)], ROOF, None,
                 (mx, 0.0, mz))
    m = mk.Mesh()
    mk.rod(m, (0.0, apex - 0.4, 0.0), (0.0, apex + 1.6, 0.0), 0.06, 6, "metal", IRON)
    mk.lathe(m, [(0.0, 0.0), (0.16, 0.1), (0.16, 0.22), (0.0, 0.32)], 8, "metal", GILT, (0.0, apex + 1.0, 0.0))
    _finish(p, m)
    # LOD1: the outer faces whole, the spire, the finial dropped.
    lod: list = []
    for name in ("n", "s", "w", "e"):
        _holed_face(lod, _Side(name), h_out, -h_out, h_out, base, eave, [], WALL_OUT, outward=True)
    for (xa, za), (xb, zb) in zip(corners, corners[1:] + corners[:1]):
        mx, mz = (xa + xb) / 2.0, (za + zb) / 2.0
        add_face(lod, [(xa, eave, za), (xb, eave, zb), (0.0, apex, 0.0)], ROOF, None, (mx, r * r / OWL_LOFT_ROOF, mz))
        k = h_out / r
        add_face(lod, [(xa, eave, za), (xb, eave, zb), (xb * k, eave, zb * k), (xa * k, eave, za * k)], ROOF, None,
                 (0.0, -1.0, 0.0))
    p.lod1 = lod
    # The camera's coarse shell: the inside faces (floor to ceiling), the outside faces and the spire.
    col = []
    for name in ("n", "s", "w", "e"):
        sd = _Side(name)
        col.append([sd.pt(-h_in, floor, h_in), sd.pt(h_in, floor, h_in), sd.pt(h_in, ceiling, h_in), sd.pt(-h_in, ceiling, h_in)])
        col.append([sd.pt(-h_out, base, h_out), sd.pt(h_out, base, h_out), sd.pt(h_out, eave, h_out), sd.pt(-h_out, eave, h_out)])
    for (xa, za), (xb, zb) in zip(corners, corners[1:] + corners[:1]):
        col.append([(xa, eave, za), (xb, eave, zb), (0.0, apex, 0.0)])
    p.meta["collider"] = col
    p.meta.update(inside=h_in, outside=h_out, eave=eave, eave_out=OWL_LOFT_EAVE, apex=apex, floor=floor, loft=loft, base=base,
                  holes={n: owl_loft_holes(n, floor, loft) for n in ("n", "s", "w", "e")})
    return p


def L_quad(x0: float, z0: float, x1: float, z1: float, y: float):  # noqa: N802 (a plan rectangle as a quad)
    return [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]


def owl_loft_core(size: float = 4.0, height: float = 17.4) -> Piece:
    """`kit_owl_loft_core`: the Owl Loft's newel, a plain square pier `size` wide
    in the cell's stone from the knoll (its origin: the plan centre at the
    floor) up `height` to the underside of the loft's slab, which covers
    it. Flush faces only: the stair climbs round it close under the loft,
    where the spiral core's cornice ring would hang in the climbers' heads
    (kit_spiral_core keeps it for the west tower)."""
    p = Piece("")
    h = size / 2.0
    for side in ("n", "s", "w", "e"):
        s = _Side(side)
        add_face(p.faces, [s.pt(-h, 0.0, h), s.pt(h, 0.0, h), s.pt(h, height, h), s.pt(-h, height, h)], WALL, None,
                 s.normal(True))
    p.meta.update(size=size, height=height)
    p.meta["collider"] = [[tuple(q) for q in f.pts] for f in p.faces]
    return p


# =====================================================================================
# N10: perches, straw and the owl mail desk
# =====================================================================================

PERCH_Y = 2.95  # the perch beam top over the floor (its brackets clear of the 2.4 m intrusion height)
PERCH_OFF = 0.35  # the beam's centre off the wall
PERCH_BEAM = 0.09
OWL_SPACING = 1.2  # owls on a perch at least this far apart


def owl_perch(width: float = 3.0, height: float = PERCH_Y) -> Piece:
    """`kit_owl_perch`: a wall perch for the owls: an oak beam `width` long
    PERCH_OFF off the wall, its top `height` over the floor, on iron
    brackets (an arm and a strut each) every metre and a half; droppings of
    straw are the floor's. Everything stands over 2.4 m. Meta "owls": the
    places an owl sits on it [(x, y, z)], OWL_SPACING apart."""
    if height - 0.5 < G.INTRUSION_HEIGHT - 1e-9:
        raise ValueError("kit_owl_perch: the perch stands clear of the 2.4 m intrusion height")
    p = Piece("")
    m = mk.Mesh()
    hw, b = width / 2.0, PERCH_BEAM
    top = height
    mk.box(m, (-hw, top - b, PERCH_OFF - b / 2), (hw, top, PERCH_OFF + b / 2), "wood", OAK_AGED)
    n = max(2, int(round(width / 1.5)) + 1)
    for i in range(n):
        x = -hw + 0.2 + (width - 0.4) * i / (n - 1)
        mk.box(m, (x - 0.025, top - b - 0.05, 0.0), (x + 0.025, top - b, PERCH_OFF + 0.05), "metal", IRON, skip=("-z",))
        mk.rod(m, (x, top - b - 0.04, PERCH_OFF - 0.02), (x, top - 0.45, 0.01), 0.018, 4, "metal", IRON)
        mk.box(m, (x - 0.05, top - 0.5, 0.0), (x + 0.05, top - b - 0.02, 0.02), "metal", IRON, skip=("-z",))
    k = max(1, int(width // OWL_SPACING))
    owls = [(round(-hw + width * (i + 0.5) / k, 3), round(top, 3), PERCH_OFF) for i in range(k)]
    p.meta.update(owls=owls, top=top, proud=PERCH_OFF + b / 2)
    return _finish(p, m)


def straw(radius: float = 1.0, seed: float = 1) -> Piece:
    """`kit_straw`: a scatter of straw on the floor: a low irregular mat (its
    top 4 cm up, a sloping edge to the floor) in two straw colours with a
    few loose stalks lying on it. Walk-through (it is the floor's)."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    s = int(round(seed))
    k = 11
    rim, mat = [], []
    for i in range(k):
        a = 2 * math.pi * i / k
        rr = radius * (0.75 + 0.25 * math.sin(a * 3 + s * 1.7) * math.cos(a * 2 - s))
        rim.append((rr * math.cos(a), 0.0, rr * math.sin(a)))
        mat.append((rr * 0.78 * math.cos(a), 0.04, rr * 0.78 * math.sin(a)))
    m.poly(list(reversed(mat)), "fabric", zone=STRAW)
    for i in range(k):
        j = (i + 1) % k
        m.poly([rim[i], mat[i], mat[j], rim[j]], "fabric", zone=STRAW_DARK if (i + s) % 3 == 0 else STRAW)
    for i in range(6):
        a = 2.39996 * (i + s)
        rr = radius * 0.5 * ((i * 0.37 + 0.2 * s) % 1.0)
        cx, cz = rr * math.cos(a), rr * math.sin(a)
        d = a * 1.3 + i
        dx, dz = 0.3 * math.cos(d), 0.3 * math.sin(d)
        mk.box(m, (cx - 0.012, 0.04, cz - 0.012), (cx + 0.012, 0.058, cz + 0.012), "fabric", STRAW_DARK)
        m.poly([(cx - dx, 0.045, cz - dz), (cx + dx, 0.045, cz + dz), (cx + dx - 0.02 * dz, 0.05, cz + dz + 0.02 * dx)],
               "fabric", zone=STRAW_DARK)
    p.meta.update(radius=radius, height=0.06)
    return _finish(p, m)


OWL_MAIL_DEPTH = 0.62  # the cabinet's depth out from the wall


def owl_mail() -> Piece:
    """`kit_owl_mail`: the Owl Loft's owl mail desk (our own design): an oak
    lectern-cabinet against the wall, its writing slope at a standing
    height, a brass-lipped letter slot in its front, a bundle of letters on
    the slope and a small carved owl on the cabinet's back corner. Origin at
    the floor on the wall face. Walk-through (as furniture; it has a
    keep-out). The interactable opens the mail panel (the client)."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    d = OWL_MAIL_DEPTH
    mk.box(m, (-0.42, 0.0, 0.0), (0.42, 0.08, d + 0.04), "wood", OAK_AGED, skip=("-z", "-y"))
    mk.box(m, (-0.36, 0.08, 0.02), (0.36, 0.98, d), "wood", OAK, skip=("-z", "-y"))
    # The writing slope: a board from the back at 1.25 m down to the front at 1.02 m.
    back, front = (1.25, 0.04), (1.02, d + 0.06)
    pts = [(-0.4, back[0], back[1]), (0.4, back[0], back[1]), (0.4, front[0], front[1]), (-0.4, front[0], front[1])]
    m.poly(list(reversed(pts)), "wood", zone=OAK_AGED)
    m.poly([(-0.4, front[0] - 0.04, front[1]), (0.4, front[0] - 0.04, front[1]), (0.4, front[0], front[1]),
            (-0.4, front[0], front[1])], "wood", zone=OAK_AGED)
    for sx in (-1.0, 1.0):
        x = sx * 0.4
        side = [(x, 0.98, 0.02), (x, 0.98, d + 0.06), (x, front[0], front[1]), (x, back[0], back[1]), (x, 0.98, back[1])]
        side = side if sx > 0 else list(reversed(side))
        m.poly(side, "wood", zone=OAK_AGED)
    mk.box(m, (-0.4, 0.98, 0.0), (0.4, back[0], 0.04), "wood", OAK_AGED, skip=("-z",))
    # The letter slot: a dark slit in a brass plate on the front.
    mk.box(m, (-0.22, 0.66, d), (0.22, 0.84, d + 0.012), "metal", BRASS, skip=("-z",))
    mk.box(m, (-0.17, 0.73, d + 0.012), (0.17, 0.77, d + 0.016), "stone", SOOT, skip=("-z",))
    # Letters tied in a bundle on the slope, and two loose.
    t = (front[0] - back[0]) / (front[1] - back[1])

    def on_slope(z: float) -> float:
        return back[0] + (z - back[1]) * t

    for i, (x, z, w, l) in enumerate(((-0.18, 0.3, 0.24, 0.16), (0.12, 0.42, 0.22, 0.15), (0.2, 0.2, 0.2, 0.14))):
        lift = 0.006 + 0.012 * (i == 0)
        za, zb = z - l / 2, z + l / 2
        pts = [(x - w / 2, on_slope(za) + lift, za), (x - w / 2, on_slope(zb) + lift, zb),
               (x + w / 2, on_slope(zb) + lift, zb), (x + w / 2, on_slope(za) + lift, za)]
        m.poly(pts if mk.newell(pts)[1] > 0 else list(reversed(pts)), "fabric", zone=LETTER)
    # A small carved owl on the back corner, in the cabinet's oak.
    mark = m.mark()
    mk.lathe(m, [(0.0, 0.0), (0.07, 0.02), (0.085, 0.09), (0.07, 0.16), (0.0, 0.19)], 8, "wood", OAK, scale_xz=(1.0, 0.85))
    mk.lathe(m, [(0.0, 0.0), (0.065, 0.02), (0.068, 0.07), (0.0, 0.11)], 8, "wood", OAK, (0.0, 0.17, 0.0))
    for sx in (-1.0, 1.0):
        mk.centred_box(m, (sx * 0.022, 0.23, 0.06), (0.024, 0.024, 0.012), "metal", GILT)
    m.transform(mark, lambda q: (q[0] + 0.3, q[1] + back[0], q[2] + 0.14))
    p.meta.update(depth=d + 0.06, width=0.84, height=back[0] + 0.32)
    return _finish(p, m)


# =====================================================================================
# N11: the Tapestry of the Patient Weaver (kit_tapestry_weaver)
# =====================================================================================

WEAVER_SIZE = (3.0, 4.5)  # the cloth: width x height
WEAVER_BOTTOM = 1.5  # its bottom above the floor


def tapestry_weaver(width: float = WEAVER_SIZE[0], height: float = WEAVER_SIZE[1]) -> Piece:
    """`kit_tapestry_weaver`: "the Tapestry of the Patient Weaver", our own
    invented design, flat on the props atlas's cloth: a night-blue field in
    a gold border set with cream lozenges; an upright loom stands in it, its
    cream warp strung top to bottom and half woven from the foot in wine
    bands of gold lozenges, its shuttle resting on the cloth's edge; over it
    a crescent moon and a scatter of stars (the weaver works on through the
    night). A short fringe below, an iron rod with gilt finials above.
    Origin: the cloth's bottom centre on the wall face; at most 0.1 m off
    the wall (grid.TAPESTRY_PROUD)."""
    p = Piece("")
    m = mk.Mesh()
    hw, h = width / 2.0, height
    z0 = 0.03

    def rect(x0, y0, x1, y1, z, zone, tile="fabric"):
        m.poly([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], tile, zone=zone)

    def lozenge(cx, cy, rx, ry, z, zone):
        m.poly([(cx, cy - ry, z), (cx + rx, cy, z), (cx, cy + ry, z), (cx - rx, cy, z)], "fabric", zone=zone)

    # The cloth (its back face too, so the hanging edge reads from the side) and the border.
    rect(-hw, 0.0, hw, h, z0, NAVY)
    m.poly([(-hw, 0.0, z0 - 0.005), (-hw, h, z0 - 0.005), (hw, h, z0 - 0.005), (hw, 0.0, z0 - 0.005)], "fabric", zone=NAVY)
    bw, z1 = 0.18, z0 + 0.004
    for x0, y0, x1, y1 in ((-hw, 0.0, hw, bw), (-hw, h - bw, hw, h), (-hw, bw, -hw + bw, h - bw), (hw - bw, bw, hw, h - bw)):
        rect(x0, y0, x1, y1, z1, OCHRE)
    z2 = z1 + 0.003
    for k in range(9):
        lozenge(-hw + bw / 2, bw + (h - 2 * bw) * (k + 0.5) / 9, 0.05, 0.08, z2, CREAM)
        lozenge(hw - bw / 2, bw + (h - 2 * bw) * (k + 0.5) / 9, 0.05, 0.08, z2, CREAM)
    for k in range(6):
        lozenge(-hw + bw + (width - 2 * bw) * (k + 0.5) / 6, bw / 2, 0.08, 0.05, z2, CREAM)
        lozenge(-hw + bw + (width - 2 * bw) * (k + 0.5) / 6, h - bw / 2, 0.08, 0.05, z2, CREAM)
    # The loom: two posts, the beams top and bottom, the warp, the woven cloth rising from the foot.
    lx0, lx1, ly0, ly1 = -0.75, 0.75, 0.42, 2.9
    for x in (lx0 - 0.08, lx1):
        rect(x, ly0 - 0.12, x + 0.08, ly1 + 0.12, z1, OAK_AGED, "wood")
    rect(lx0 - 0.16, ly1, lx1 + 0.16, ly1 + 0.1, z2, OAK_AGED, "wood")
    rect(lx0 - 0.1, ly0 - 0.02, lx1 + 0.1, ly0 + 0.08, z2, OAK_AGED, "wood")
    woven = 1.35
    for i in range(9):
        x = lx0 + 0.1 + (lx1 - lx0 - 0.2) * i / 8
        rect(x - 0.012, woven, x + 0.012, ly1, z1, CREAM)
    rect(lx0, ly0 + 0.08, lx1, woven, z1, TWINE)
    for row, y in enumerate((0.62, 0.86, 1.1)):
        for i in range(4 + row % 2):
            cx = lx0 + 0.19 + (lx1 - lx0 - 0.38) * (i / (3 + row % 2) if 3 + row % 2 else 0.5)
            lozenge(cx, y, 0.1, 0.09, z2, OCHRE)
    rect(lx0, woven - 0.03, lx1, woven + 0.02, z2, CREAM)
    # The shuttle at rest on the woven edge.
    m.poly([(lx1 - 0.6, woven + 0.05, z2), (lx1 - 0.2, woven + 0.05, z2), (lx1 - 0.08, woven + 0.1, z2),
            (lx1 - 0.2, woven + 0.15, z2), (lx1 - 0.6, woven + 0.15, z2), (lx1 - 0.72, woven + 0.1, z2)], "wood",
           zone=OAK)
    # The moon and the stars.
    # The crescent: the outer arc from 60 to 300 degrees round the left, back along a
    # shallower curve (a quadratic through a control point inside the disc), one polygon.
    cx, cy, r = 0.55, 3.55, 0.34
    n = 12
    outer = [(cx + r * math.cos(math.radians(60 + 240 * i / n)), cy + r * math.sin(math.radians(60 + 240 * i / n)))
             for i in range(n + 1)]
    (x0, y0), (x1, y1), (kx, ky) = outer[-1], outer[0], (cx - 0.55 * r, cy)
    inner = [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * kx + t * t * x1, (1 - t) ** 2 * y0 + 2 * (1 - t) * t * ky + t * t * y1)
             for t in (i / n for i in range(1, n))]
    m.poly([(x, y, z1) for x, y in outer + inner], "fabric", zone=CREAM)
    z3 = z2 + 0.003
    for k, (sx, sy, sr) in enumerate(((-0.8, 3.75, 0.09), (-0.35, 3.35, 0.06), (-0.95, 3.25, 0.05), (-0.1, 3.95, 0.07),
                                       (1.05, 3.25, 0.06), (1.0, 3.98, 0.05), (-0.55, 3.15, 0.045), (0.12, 3.12, 0.05))):
        lozenge(sx, sy, sr, sr * 1.6, z2, OCHRE if k % 3 else CREAM)
        lozenge(sx, sy, sr * 1.6, sr * 0.35, z3, OCHRE if k % 3 else CREAM)
    # The fringe and the rod.
    for i in range(int(width / 0.06)):
        x = -hw + 0.03 + 0.06 * i
        rect(x - 0.012, -0.09, x + 0.012, 0.0, z0, CREAM if i % 2 else OCHRE)
    ry, rz = h + 0.03, 0.05
    mk.rod(m, (-hw - 0.12, ry, rz), (hw + 0.12, ry, rz), 0.022, 6, "metal", IRON)
    for sx in (-1.0, 1.0):
        mk.lathe(m, [(0.0, -0.05), (0.045, -0.02), (0.045, 0.02), (0.0, 0.05)], 6, "metal", GILT, (sx * (hw + 0.16), ry, rz))
        mk.box(m, (sx * (hw - 0.1) - 0.025, ry - 0.08, 0.0), (sx * (hw - 0.1) + 0.025, ry, rz), "metal", IRON,
               skip=("-z",))
    p.meta.update(width=width, height=height, proud=rz + 0.045)
    return _finish(p, m)


# =====================================================================================
# N12: the cellars' barrels and wine racks
# =====================================================================================

CELLAR_BARREL_D = 0.9
CELLAR_BARREL_L = 1.15


def _cellar_barrel(m: mk.Mesh, cx: float, cy: float) -> None:
    """A barrel lying out from the wall (along +z from 0.05), its axis at
    (cx, cy): bulged oak staves, two iron hoops, its head toward the room."""
    seg = 8
    r1, r0, L = CELLAR_BARREL_D / 2.0, CELLAR_BARREL_D / 2.0 - 0.06, CELLAR_BARREL_L
    mark = m.mark()
    mk.lathe(m, [(r0, 0.0), (r1, 0.3), (r1, L - 0.3), (r0, L)], seg, "wood", OAK_AGED, cap_top=True)
    for y in (0.16, L - 0.24):
        rr = r0 + (r1 - r0) * min(1.0, y / 0.3) + 0.012
        mk.lathe(m, [(rr, y), (rr, y + 0.08)], seg, "metal", IRON, cap_top=False)
    mk.lathe(m, [(r0 - 0.05, L), (r0 - 0.05, L + 0.01)], seg, "wood", OAK, cap_top=True)
    m.transform(mark, lambda p: (p[0] + cx, -p[2] + cy, p[1] + 0.05))


def cellar_barrels(count: float = 3, tiers: float = 2) -> Piece:
    """`kit_cellar_barrels`: barrels lying on an oak cradle against a wall,
    `count` side by side and `tiers` high (each tier on its own pair of
    chocked beams), their heads toward the room. Origin at the floor on the
    wall face; walk-through as furniture (it has a keep-out)."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    n, t = int(round(count)), int(round(tiers))
    pitch = CELLAR_BARREL_D + 0.08
    w = n * pitch
    for k in range(t):
        y0 = k * (CELLAR_BARREL_D + 0.16)
        for z in (0.25, CELLAR_BARREL_L - 0.15):
            mk.box(m, (-w / 2, y0, z - 0.08), (w / 2, y0 + 0.14, z + 0.08), "wood", OAK_AGED)
        for i in range(n):
            _cellar_barrel(m, -w / 2 + pitch * (i + 0.5), y0 + 0.14 + CELLAR_BARREL_D / 2.0)
    if t > 1:
        for x in (-w / 2, w / 2 - 0.12):
            mk.box(m, (x, 0.0, 0.05), (x + 0.12, t * (CELLAR_BARREL_D + 0.16), 0.17), "wood", OAK_AGED, skip=("-z",))
    p.meta.update(width=w, depth=CELLAR_BARREL_L + 0.06, height=t * (CELLAR_BARREL_D + 0.16))
    return _finish(p, m)


WINE_RACK_DEPTH = 0.45


def wine_rack(width: float = 3.0, height: float = 2.2) -> Piece:
    """`kit_wine_rack`: an oak rack of square bins against a wall, every bin
    holding bottles laid neck-in (their dark green, blue-black and wine-red
    bottoms showing in rows), a few bins empty. Origin at the floor on the
    wall face; walk-through as furniture (it has a keep-out)."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    d = WINE_RACK_DEPTH
    hw = width / 2.0
    cols = max(2, int(round(width / 0.5)))
    rows = max(2, int(round((height - 0.1) / 0.5)))
    cw, rh = width / cols, (height - 0.1) / rows
    mk.box(m, (-hw, 0.0, 0.0), (hw, 0.1, d), "wood", OAK_AGED, skip=("-z",))
    for i in range(cols + 1):
        x = -hw + cw * i
        mk.box(m, (x - 0.03, 0.1, 0.0), (x + 0.03, height, d), "wood", OAK, skip=("-z",))
    for j in range(rows + 1):
        y = 0.1 + rh * j
        mk.box(m, (-hw + 0.01, y - 0.025, 0.0), (hw - 0.01, y + 0.025, d - 0.02), "wood", OAK, skip=("-z",))
    m.poly([(-hw, 0.1, 0.01), (hw, 0.1, 0.01), (hw, height, 0.01), (-hw, height, 0.01)], "wood", zone=OAK_AGED)
    zones = (BOTTLE, BOTTLE_DARK, WINE)
    for j in range(rows):
        for i in range(cols):
            if (i * 5 + j * 3) % 7 == 2:
                continue
            zone = zones[(i + 2 * j) % 3]
            yb = 0.1 + rh * j + 0.025
            for a in range(2):
                for b in range(2):
                    cx = -hw + cw * i + cw * (0.3 + 0.4 * a)
                    cy = yb + (rh - 0.05) * (0.3 + 0.4 * b)
                    ring = [(cx + 0.075 * math.cos(2 * math.pi * k / 6), cy + 0.075 * math.sin(2 * math.pi * k / 6),
                             d - 0.06) for k in range(6)]
                    m.poly(ring, "glass" if zone != WINE else "leather", zone=zone)
    p.meta.update(width=width, depth=d, height=height)
    return _finish(p, m)


# Registry (pieces/__init__.py).
BUILDERS = {
    "kit_owl_loft_shell": owl_loft_shell,
    "kit_owl_loft_core": owl_loft_core,
    "kit_owl_perch": owl_perch,
    "kit_straw": straw,
    "kit_owl_mail": owl_mail,
    "kit_tapestry_weaver": tapestry_weaver,
    "kit_cellar_barrels": cellar_barrels,
    "kit_wine_rack": wine_rack,
}
CATALOGUE = {
    "kit_owl_loft_shell": {},
    "kit_owl_loft_core": {},
    "kit_owl_perch": {"width": 3.0},
    "kit_straw": {"radius": 1.0, "seed": 1},
    "kit_owl_mail": {},
    "kit_tapestry_weaver": {},
    "kit_cellar_barrels": {"count": 3, "tiers": 2},
    "kit_wine_rack": {"width": 3.0, "height": 2.2},
}
