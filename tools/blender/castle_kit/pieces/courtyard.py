"""The courtyard pieces (castle M5c, docs/art/castle-kit.md "The kitted cells"): the arcade
bay, the tiered fountain, the gatehouse round the courtyard gate, the lamp
post, the Exchange Yard's market stall and the stone planter with its tree.

Frames as props.json `kit`: origin at the bottom centre, yaw 0 facing +z.
The arcade bay and the gatehouse are wall pieces: their origin lies on the
wall face (the bay's centre, the gate's centre line), the wall mass behind
(z < 0), the courtyard in front (z > 0). Every piece is walk-through (the
server has no prop collision; the bots keep out of their footprints, 30.8);
the big ones carry a coarse camera shell (meta "collider", as the hall's
vaults).

Clearly 3D under the toon ramp and the baked AO (the castle has no
outline): arches cut through a real slab, octagonal piers with bases and
capitals, a lean-to roof with thickness and rafters under it, a basin with
a coping and a lip, towers with corbelled crenellations.

Colours come only from palette data: stone in the cell's wall
stone (`castle_wall|world`) and the trim sheet, oak on the wood sheet, the
slates in `castle_roof`; metal, cloth, foliage and water on the props atlas
by palette name (castle_textures.json, the `_doc_m5c_courtyard` block).
House accents only by the names house_a..d, chosen by `args.house`
(grid.STALL_AWNINGS)."""

from __future__ import annotations

import math

import foliage as FO
import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import geom, hall, openings
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall_furniture import BEAM, PLANKS, wbox

WALL = "castle_wall|world"
ROOF = "castle_roof|world"
SEG = 14  # arc segments per side of an arcade arch at LOD0 (LOD1: SEG_LOD1)
SEG_LOD1 = 4

# Zone -> palette name (castle_textures.json `palette`).
HOUSE0 = 11  # + args.house: STALL_AWNINGS (house_a..d)
IRON, BRASS, GILT, OAK, OAK_AGED, STONE, WATER, CANVAS, LEAF, LEAF_LIGHT, SOOT = 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30
PARCEL, LETTER, COPPER, PEWTER, GLASS, INK, RIBBON, OWL_BROWN, OWL_PALE, CORK, GLOW = 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41
ZONES = {
    **{HOUSE0 + i: name for i, name in enumerate(G.STALL_AWNINGS)},
    IRON: "iron", BRASS: "brass", GILT: "gilt", OAK: "oak", OAK_AGED: "oak_aged", STONE: "statue_stone", WATER: "water",
    CANVAS: "canvas", LEAF: "plant", LEAF_LIGHT: "leaf_light", SOOT: "soot", PARCEL: "parcel_tan", LETTER: "letter_white",
    COPPER: "copper", PEWTER: "pewter", GLASS: "hourglass_glass", INK: "ink_blue", RIBBON: "wine_red", OWL_BROWN: "owl_brown",
    OWL_PALE: "owl_pale", CORK: "cork", GLOW: "glow_lantern",
    90: "brass",  # kit_models._owl's beak
}


def trim(strip: str) -> str:
    return f"castle_trim|trim:{strip}"


def _finish(p: Piece, m: mk.Mesh, lod1: bool = False) -> Piece:
    faces = hall.from_mesh(m, ZONES, walkthrough=True).faces
    (p.lod1 if lod1 else p.faces).extend(faces)
    return p


# -- shared helpers -------------------------------------------------------------------------


def vprism(f: list, outline, y0: float, y1: float, tag: str, metres: float | None = None, top: bool = True,
           bottom: bool = False) -> None:
    """A vertical prism over a convex outline [(x, z), ...]: its sides (world
    UVs, or a trim strip `metres` tall: s round the outline, p up the side),
    a top cap and optionally a bottom one."""
    n = len(outline)
    cx = sum(p[0] for p in outline) / n
    cz = sum(p[1] for p in outline) / n
    s = 0.0
    trimmed = tag.split("|", 1)[1].startswith("trim")
    for i in range(n):
        (xa, za), (xb, zb) = outline[i], outline[(i + 1) % n]
        ln = math.hypot(xb - xa, zb - za)
        pts = [(xa, y0, za), (xb, y0, zb), (xb, y1, zb), (xa, y1, za)]
        uv = [(s, 0.0), (s + ln, 0.0), (s + ln, metres), (s, metres)] if trimmed else None
        add_face(f, pts, tag, uv, ((xa + xb) / 2 - cx, 0.0, (za + zb) / 2 - cz), anchor=trimmed)
        s += ln
    for want, y, nrm in ((top, y1, 1.0), (bottom, y0, -1.0)):
        if not want:
            continue
        pts = [(x, y, z) for x, z in outline]
        uv = [(x, metres * (0.35 + 0.3 * ((z - cz) > 0))) for x, z in outline] if trimmed else None
        add_face(f, pts, tag, uv, (0.0, nrm, 0.0), anchor=trimmed)


def octagon(cx: float, cz: float, half: float, chamfer: float):
    """A square of half side `half` with its corners cut by `chamfer` (x, z)."""
    h, c = half, chamfer
    return [(cx + x, cz + z) for x, z in ((h - c, -h), (h, -h + c), (h, h - c), (h - c, h), (-h + c, h), (-h, h - c),
                                          (-h, -h + c), (-h + c, -h))]


def ring(cx: float, cz: float, r: float, n: int, phase: float = 0.0):
    return [(cx + r * math.cos(phase + 2 * math.pi * k / n), cz + r * math.sin(phase + 2 * math.pi * k / n)) for k in range(n)]


def tbox(f: list, lo, hi, strip: str, metres: float, skip=()) -> None:
    """An axis-aligned box on a trim strip: s along x (along z on the x
    faces), p up the side faces 0..metres."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi

    def uv(side, pts):
        if side in ("+y", "-y"):
            return [(p[0], metres * (0.4 + 0.2 * (p[2] > (z0 + z1) / 2))) for p in pts]
        along = 2 if side in ("+x", "-x") else 0
        return [(p[along], (p[1] - y0) / ((y1 - y0) or 1.0) * metres) for p in pts]

    geom.box(f, lo, hi, trim(strip), skip=skip, uv_fn=uv)


def fit_box(f: list, lo, hi, tile: str, skip=()) -> None:
    geom.box(f, lo, hi, f"castle_trim|fit:{tile}", skip=skip, uv_fn=lambda side, pts: [(0, 0), (1, 0), (1, 1), (0, 1)])


def beam(f: list, p0, p1, w: float, h: float, tag: str = BEAM, metres: float = 0.6, ends=(True, True)) -> None:
    """A rectangular beam from p0 to p1 (its top edge's centre line), `w`
    wide and `h` deep below that line, on a wood strip (s along it)."""
    d = geom.unit(geom.sub(p1, p0))
    side = geom.unit(geom.cross(d, (0.0, 1.0, 0.0)))
    if geom.norm(side) < 1e-6:
        side = (1.0, 0.0, 0.0)
    up = geom.cross(side, d)
    ln = geom.norm(geom.sub(p1, p0))

    def at(p, a, b):
        return tuple(p[k] + side[k] * a + up[k] * b for k in range(3))

    corners = [(-w / 2, 0.0), (w / 2, 0.0), (w / 2, -h), (-w / 2, -h)]
    for i in range(4):
        (a0, b0), (a1, b1) = corners[i], corners[(i + 1) % 4]
        pts = [at(p0, a0, b0), at(p1, a0, b0), at(p1, a1, b1), at(p0, a1, b1)]
        mid = ((a0 + a1) / 2, (b0 + b1) / 2 + h / 2)
        nrm = tuple(side[k] * mid[0] + up[k] * mid[1] for k in range(3))
        add_face(f, pts, tag, [(0.0, 0.0), (ln, 0.0), (ln, metres * 0.5), (0.0, metres * 0.5)], nrm, anchor=False)
    for want, p, sg in ((ends[0], p0, -1.0), (ends[1], p1, 1.0)):
        if want:
            add_face(f, [at(p, a, b) for a, b in corners], tag, [(0, 0), (w, 0), (w, h), (0, h)],
                     tuple(sg * d[k] for k in range(3)))


def slope_quad(f: list, x0: float, x1: float, za: float, ya: float, zb: float, yb: float, tag: str, up: bool,
               uv=None) -> None:
    """A quad from the line (z = za, y = ya) to (z = zb, y = yb) across x0..x1,
    facing up (`up`) or down."""
    pts = [(x0, ya, za), (x1, ya, za), (x1, yb, zb), (x0, yb, zb)]
    dz, dy = zb - za, yb - ya
    nrm = (0.0, dz, -dy) if dz * 1.0 >= 0 else (0.0, -dz, dy)
    if (nrm[1] > 0) != up:
        nrm = (0.0, -nrm[1], -nrm[2])
    add_face(f, pts, tag, uv, nrm)


# =====================================================================================
# kit_arcade_bay_6: one 6 m bay of an open pointed arcade along a courtyard wall
# =====================================================================================

BAY = 6.0
ARCADE_SLAB = 0.6  # the arch wall's thickness (the pier line at its middle)
ARCADE_PITCH = 25.0  # the lean-to roof's pitch (degrees)
ARCADE_ROOF_T = 0.28  # the roof's thickness, measured vertically
ARCADE_OVERHANG = 0.35  # the eave beyond the arch wall's face
ARCADE_END_OVERHANG = 0.35  # the roof beyond a free end pier
ARCADE_OVER_APEX = 0.75  # the arch wall's top (the eave) above the apex
ORDER_STEP = 0.12  # the inner order steps this far into the opening on each side
ORDER_INSET = 0.15  # and stands this far in from both faces of the arch wall
ROUNDEL_R = 0.42  # the spandrel roundels' radius
ROUNDEL_UP = 2.45  # their centre over the springing (clear of the rims, under the eave)


def arcade_bay(depth: float = 3.0, apex: float = 6.0, pier: float = 0.8, pier0: float = 1, pier1: float = 0,
               end0: float = 0, end1: float = 0) -> Piece:
    """`kit_arcade_bay_6`: a 6 m bay (local x -3..3) of an open arcade, its
    pier line `depth` (2.5-3.5 m) off the wall face, a two-centred arch
    between octagonal piers `pier` (0.6-1.0 m) wide with its apex at `apex`
    (5-7.5 m) cut through a 0.6 m stone wall, and a lean-to slate roof
    (castle_roof, ARCADE_PITCH) from its eave to the wall, boarded under
    (castle_wood) on oak rafters, a moulded wall plate over it.

    `pier0` / `pier1` draw the pier at x = -3 / +3 (a run of bays: each its
    left pier, the last one its right too; two bays meeting at a corner
    share one pier). `end0` / `end1` close the arch wall at that end: 1 a
    free end (the roof runs on past the pier), 2 a corner end (the roof
    stops over the pier, the other arm's roof meets it). The walk under it
    is the courtyard's floor (walk-through: piers like the hall's furniture).
    At most grid.ARCADE_TRIS; LOD1 (meta "lod1") without the rim, rafters,
    boards, plate, bases and capitals."""
    lo, hi = G.ARCADE_DEPTH
    if not lo - 1e-9 <= depth <= hi + 1e-9:
        raise ValueError(f"kit_arcade_bay_6: depth must be {lo}..{hi} m")
    if not G.ARCADE_APEX[0] - 1e-9 <= apex <= G.ARCADE_APEX[1] + 1e-9:
        raise ValueError(f"kit_arcade_bay_6: apex must be {G.ARCADE_APEX[0]}..{G.ARCADE_APEX[1]} m")
    if not G.ARCADE_PIER[0] - 1e-9 <= pier <= G.ARCADE_PIER[1] + 1e-9:
        raise ValueError(f"kit_arcade_bay_6: pier must be {G.ARCADE_PIER[0]}..{G.ARCADE_PIER[1]} m")
    p = Piece("", walkthrough=True)
    p.lod1 = []
    hb = BAY / 2.0
    span = BAY - pier
    half = span / 2.0
    spring = apex - G.ARCH_RISE * span
    eave = apex + ARCADE_OVER_APEX
    th = ARCADE_SLAB
    zf, zb = depth + th / 2, depth - th / 2
    tanp = math.tan(math.radians(ARCADE_PITCH))
    zr = zf + ARCADE_OVERHANG  # the eave's edge

    def yu(z):  # the roof's underside over z (through the arch wall's front top edge)
        return eave + (zf - z) * tanp

    def yt(z):
        return yu(z) + ARCADE_ROOF_T

    ends = (int(round(end0)), int(round(end1)))
    piers = [x for x, want in ((-hb, pier0), (hb, pier1)) if want >= 0.5]
    # The arch wall and roof extents along x (a free or corner end closes them over the end pier).
    sx0 = -hb - (pier / 2 if ends[0] else 0.0)
    sx1 = hb + (pier / 2 if ends[1] else 0.0)
    rx0 = sx0 - (ARCADE_END_OVERHANG if ends[0] == 1 else 0.0)
    rx1 = sx1 + (ARCADE_END_OVERHANG if ends[1] == 1 else 0.0)
    yb_top = yu(zb)

    for lod, f, seg in ((0, p.faces, SEG), (1, p.lod1, SEG_LOD1)):
        # The arch wall: strips over the piers, spandrels round the head, the soffit.
        for z, top, facing in ((zf, eave, 1.0), (zb, yb_top, -1.0)):
            geom.rect_z(f, sx0, -half, spring, top, z, WALL, facing)
            geom.rect_z(f, half, sx1, spring, top, z, WALL, facing)
            openings._spandrels(f, span, top, spring, z, facing, n=seg)
        openings._soffit(f, span, spring, zb, zf, WALL, n=seg)
        for e, x, sg in ((ends[0], sx0, -1.0), (ends[1], sx1, 1.0)):
            if e:
                add_face(f, [(x, spring, zb), (x, spring, zf), (x, eave, zf), (x, yb_top, zb)], WALL, None, (sg, 0.0, 0.0))
        # The roof: slates on top, the fascia at the eave, its ends.
        slope_quad(f, rx0, rx1, zr, yt(zr), 0.0, yt(0.0), ROOF, True)
        geom.rect_z(f, rx0, rx1, yu(zr), yt(zr), zr, BEAM if lod == 0 else ROOF, 1.0,
                    uv=[(rx0, 0.0), (rx1, 0.0), (rx1, 0.3), (rx0, 0.3)] if lod == 0 else None, anchor=lod == 0)
        for e, x, sg in ((ends[0], rx0, -1.0), (ends[1], rx1, 1.0)):
            if e:
                add_face(f, [(x, yu(zr), zr), (x, yu(0.0), 0.0), (x, yt(0.0), 0.0), (x, yt(zr), zr)], ROOF, None,
                         (sg, 0.0, 0.0))
        if lod == 1:
            slope_quad(f, rx0, rx1, zr, yu(zr), 0.0, yu(0.0), PLANKS, False,
                       uv=[(rx0, 0.0), (rx1, 0.0), (rx1, 0.6), (rx0, 0.6)])
            for x in piers:
                vprism(f, octagon(x, depth, pier / 2, pier * 0.15), 0.0, spring, WALL)
            continue
        # LOD0: the boards under the roof in 0.6 m bands down the slope (the plank strip).
        slope_len = math.hypot(zr, yu(0.0) - yu(zr))
        bands = max(1, int(math.ceil(slope_len / 0.6 - 1e-6)))
        for i in range(bands):
            a, b = i / bands, (i + 1) / bands
            za, zb_ = zr * (1 - a), zr * (1 - b)
            slope_quad(f, rx0, rx1, za, yu(za), zb_, yu(zb_), PLANKS, False,
                       uv=[(rx0, 0.0), (rx1, 0.0), (rx1, 0.6), (rx0, 0.6)])
        # Rafters under the boards: over each pier line and two between.
        for x in [x for x in (-hb, -hb / 2, hb / 2, hb) if (x != -hb or pier0 >= 0.5) and (x != hb or pier1 >= 0.5)]:
            beam(f, (x, yu(zb) - 0.001, zb), (x, yu(0.0) - 0.001, 0.0), 0.16, 0.2, ends=(True, False))
        # The wall plate: a moulded ledge over the roof's top edge on the wall.
        px0, px1 = rx0 + (0.06 if ends[0] else 0.0), rx1 - (0.06 if ends[1] else 0.0)  # clear of the roof's end faces
        tbox(f, (px0, yt(0.0) - 0.05, 0.0), (px1, yt(0.0) + 0.3, 0.22), "string", 0.3, skip=("-z", "-y"))
        # The arch's rim round the head on the courtyard face, and a hood mould.
        rim = []
        # (No end caps: they sit on the capitals.)
        openings._surround(rim, span, spring, None, 0.28, 0.1, "arch_rim", 0.3, n=seg, caps=(False, False))
        inner = geom.pointed_arc(span, spring, seg, grow=0.28)
        outer = geom.pointed_arc(span, spring, seg, grow=0.4)
        geom.polyline_band(rim, inner, outer, 0.0, 0.16, trim("string"), 0.3, caps=(False, False))
        for face in rim:
            face.pts = [(x, y, z + zf) for x, y, z in face.pts]
            f.append(face)
        # An inner order: a narrower arch stepped 0.12 m into the opening, 0.15 m in from both faces.
        a_in = geom.pointed_arc(span - 2 * ORDER_STEP, spring, seg)
        a_out = geom.pointed_arc(span, spring, seg)
        z0o, z1o = zb + ORDER_INSET, zf - ORDER_INSET
        geom.polyline_band(f, a_in, a_out, z0o, z1o, trim("arch_rim"), 0.3, caps=(False, False))
        for (xa, ya), (xb_, yb_), (xc, yc), (xd, yd) in zip(a_in, a_in[1:], a_out[1:], a_out):
            add_face(f, [(xa, ya, z0o), (xb_, yb_, z0o), (xc, yc, z0o), (xd, yd, z0o)], trim("arch_rim"),
                     [(0.0, 0.0), (0.3, 0.0), (0.3, 0.12), (0.0, 0.12)], (0.0, 0.0, -1.0))
        # Carved roundels in the spandrels over the inner piers (a ring and a boss).
        for x, e in ((-hb, ends[0]), (hb, ends[1])):
            if e or (x < 0 and pier0 < 0.5) or (x > 0 and pier1 < 0.5):
                continue
            yc = spring + ROUNDEL_UP
            ring_o = [(x + ROUNDEL_R * math.sin(2 * math.pi * k / 16), yc + ROUNDEL_R * math.cos(2 * math.pi * k / 16))
                      for k in range(17)]
            ring_i = [(x + (ROUNDEL_R - 0.11) * math.sin(2 * math.pi * k / 16),
                       yc + (ROUNDEL_R - 0.11) * math.cos(2 * math.pi * k / 16)) for k in range(17)]
            band = []
            geom.polyline_band(band, ring_i, ring_o, 0.0, 0.07, trim("arch_rim"), 0.3, caps=(False, False))
            fit_box(band, (x - 0.09, yc - 0.09, 0.0), (x + 0.09, yc + 0.09, 0.09), "boss", skip=("-z",))
            for face in band:
                face.pts = [(px, py, pz + zf) for px, py, pz in face.pts]
                f.append(face)
        # Responds on the wall face opposite the piers: a flat pilaster above the plinth under a capital.
        for x in piers:
            geom.box(f, (x - 0.25, G.PLINTH.height + 0.02, 0.0), (x + 0.25, spring - 0.32, 0.12), WALL, skip=("-z", "-y", "+y"))
            tbox(f, (x - 0.32, spring - 0.32, 0.0), (x + 0.32, spring, 0.2), "capital_band", 0.3, skip=("-z",))
        # The piers: octagonal shafts on moulded bases under capitals.
        for x in piers:
            c = pier * 0.15
            vprism(f, octagon(x, depth, pier / 2 + 0.1, c + 0.05), 0.0, 0.4, trim("plinth"), 0.6)
            vprism(f, octagon(x, depth, pier / 2, c), 0.4, spring - 0.32, WALL, top=False)
            vprism(f, octagon(x, depth, pier / 2 + 0.07, c + 0.04), spring - 0.32, spring, trim("capital_band"), 0.3)
    p.meta.update(depth=depth, apex=apex, spring=round(spring, 4), eave=round(eave, 4), pier=pier, piers=piers,
                  top=round(yt(0.0) + 0.3, 4), roof=(rx0, rx1, round(zr, 4)), ends=ends)
    # A coarse camera shell: the roof's underside and the arch wall's face over the springing.
    p.meta["collider"] = [
        [(rx0, yu(zr), zr), (rx1, yu(zr), zr), (rx1, yu(0.0), 0.0), (rx0, yu(0.0), 0.0)],
        [(sx0, spring, zf), (sx1, spring, zf), (sx1, eave, zf), (sx0, eave, zf)],
    ]
    return p


# =====================================================================================
# kit_fountain_tiered: the Courtyard Fountain
# =====================================================================================

FOUNTAIN_SIDES = 8
FOUNTAIN_SEGMENTS = 20


def fountain_tiered(radius: float = 4.0, height: float = 4.3) -> Piece:
    """`kit_fountain_tiered`: an octagonal basin `radius` (its circumradius,
    2.5-4.5 m with the step) with a moulded coping at seat height
    (grid.SEAT_TOP: each of its four square sides is a seat row facing out)
    over still water, a pedestal carrying two bowls of water and a bud
    finial `height` (3-5 m) up. Stone in the cell's wall stone and trim, the
    carved tiers on the props atlas (statue_stone), the water a flat sheet
    (`water`). At most grid.FOUNTAIN_TRIS."""
    lo, hi = G.FOUNTAIN_RADIUS
    if not lo - 1e-9 <= radius + 0.3 <= hi + 1e-9:
        raise ValueError(f"kit_fountain_tiered: radius must leave the step within {lo}..{hi} m")
    if not G.FOUNTAIN_HEIGHT[0] - 1e-9 <= height <= G.FOUNTAIN_HEIGHT[1] + 1e-9:
        raise ValueError("kit_fountain_tiered: height must be within grid.FOUNTAIN_HEIGHT")
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    k = FOUNTAIN_SIDES
    ph = math.pi / k  # flat sides facing +-x and +-z
    ap = radius * math.cos(ph)  # the basin's apothem

    def octo(a):
        r = a / math.cos(ph)
        return ring(0.0, 0.0, r, k, ph)

    seat = G.SEAT_TOP
    water = 0.3
    # The step round the foot, the basin wall, the coping (seat height), the inner lip down to the water.
    vprism(f, octo(ap + 0.3), 0.0, 0.12, trim("plinth"), 0.6)
    vprism(f, octo(ap), 0.12, seat - 0.1, WALL, top=False)
    vprism(f, octo(ap + 0.06), seat - 0.1, seat, trim("string"), 0.3, top=False)
    # The coping's top: a ring between the outer and the inner edge.
    outer, inner = octo(ap + 0.06), octo(ap - 0.42)
    for i in range(k):
        j = (i + 1) % k
        pts = [(outer[i][0], seat, outer[i][1]), (outer[j][0], seat, outer[j][1]), (inner[j][0], seat, inner[j][1]),
               (inner[i][0], seat, inner[i][1])]
        add_face(f, pts, trim("string"), [(0.0, 0.25), (1.0, 0.25), (1.0, 0.05), (0.0, 0.05)], (0.0, 1.0, 0.0))
    # The inner face (facing the water) from the coping down to the water.
    lip = octo(ap - 0.42)
    for i in range(k):
        (xa, za), (xb, zb) = lip[i], lip[(i + 1) % k]
        mx, mz = (xa + xb) / 2, (za + zb) / 2
        add_face(f, [(xa, water, za), (xb, water, zb), (xb, seat, zb), (xa, seat, za)], WALL, None, (-mx, 0.0, -mz))
    # The water: a flat sheet inside the lip.
    m.poly([(x, water, z) for x, z in reversed(lip)], "glass", zone=WATER)
    # The pedestal and its two bowls (carved: the props atlas's pale stone), turned.
    n = FOUNTAIN_SEGMENTS
    mk.lathe(m, [(0.62, water), (0.62, water + 0.25), (0.5, water + 0.35), (0.42, 0.9), (0.38, 1.4), (0.5, 1.55)], n,
             "metal", STONE, cap_top=False)
    b1, r1 = 1.9, 1.75
    mk.lathe(m, [(0.5, 1.55), (1.0, 1.66), (1.6, b1 - 0.08), (r1, b1), (r1 + 0.08, b1 + 0.12), (r1 - 0.06, b1 + 0.16),
                 (r1 - 0.06, b1 + 0.12)], n,
             "metal", STONE, cap_top=False)
    m.poly([(x, b1 + 0.12, z) for x, z in reversed(ring(0.0, 0.0, r1 - 0.06, n))], "glass", zone=WATER)
    b2, r2 = 3.15, 0.95
    mk.lathe(m, [(0.24, b1 + 0.12), (0.22, 2.4), (0.32, 2.48), (0.22, 2.56), (0.2, 2.9), (0.55, b2 - 0.04), (r2, b2 + 0.06),
                 (r2 + 0.05, b2 + 0.16), (r2 - 0.05, b2 + 0.2),
                 (r2 - 0.05, b2 + 0.17)], n, "metal", STONE, cap_top=False)
    m.poly([(x, b2 + 0.17, z) for x, z in reversed(ring(0.0, 0.0, r2 - 0.05, n))], "glass", zone=WATER)
    # The finial: a stone bud.
    top = height
    mk.lathe(m, [(0.16, b2 + 0.17), (0.2, b2 + 0.35), (0.12, b2 + 0.55), (0.24, top - 0.55), (0.2, top - 0.3),
                 (0.08, top - 0.08), (0.0, top)], 12, "metal", STONE, cap_top=False)
    # Seats: the four square sides of the coping (axis-aligned rows), facing out.
    rows = []
    sl = 2 * ap * math.tan(ph) - 0.5  # the side's length less its corners
    c = ap - 0.2  # the row's centre line, 0.2 m inside the outer face (the front edge 0.26 ahead of it)
    hd = 0.22
    # The fit (hall_furniture.py): the coping's edge 0.26 ahead, the inner lip 0.22 behind, the feet on the step.
    fit = [0.26, 0.22, round(seat - 0.12, 4)]
    rows.append([round(-sl / 2, 4), round(c - hd, 4), round(sl / 2, 4), round(c + hd, 4), seat, 0.0] + fit)  # +z side
    rows.append([round(-sl / 2, 4), round(-c - hd, 4), round(sl / 2, 4), round(-c + hd, 4), seat, round(math.pi, 4)] + fit)
    rows.append([round(c - hd, 4), round(-sl / 2, 4), round(c + hd, 4), round(sl / 2, 4), seat, round(math.pi / 2, 4)] + fit)
    rows.append([round(-c - hd, 4), round(-sl / 2, 4), round(-c + hd, 4), round(sl / 2, 4), seat, round(-math.pi / 2, 4)] + fit)
    p.meta.update(radius=round((ap + 0.3) / math.cos(ph), 4), height=height, water=water, seats=rows)
    return _finish(p, m)


# =====================================================================================
# kit_gatehouse_8x10: the gatehouse round the courtyard gate
# =====================================================================================

GATE_HALF = 4.0  # the gate's half width (8 m)
GATE_H = 10.0  # the gate passage's ceiling
TOWER_W = 5.0  # each tower's width along the wall
TOWER_D = 3.5  # its depth into the courtyard
TOWER_BACK = 2.3  # above the wall top it runs back over the wall (WALL_T) to this behind the face, overhanging outside
TOWER_TOP = 21.3  # the corbelled parapet's foot
GALLERY_D = 1.2  # the gallery over the gate: its depth off the wall face
GALLERY_FOOT = 12.5
GALLERY_TOP = 19.0
PORTCULLIS_Z = -0.9  # the portcullis's plane in the passage (behind the arch's reveal)
PORTCULLIS_TEETH = 6.4
WALL_T = G.L.EXTERIOR_THICKNESS["courtyard"]  # the courtyard's own walls: their outer face this far behind the inner (1 m)


def _crenels(f: list, x0: float, x1: float, z0: float, z1: float, y: float, h: float = 1.3, merlon: float = 1.0,
             gap: float = 0.6, t: float = 0.45) -> None:
    """Merlons on the ring x0..x1, z0..z1 (outer edge) at y, `t` thick: one
    run per side, a merlon at each corner, embrasures between."""
    for (a0, a1, fixed, along_x, sign) in ((x0, x1, z1, True, 1.0), (x0, x1, z0, True, -1.0), (z0, z1, x0, False, -1.0),
                                          (z0, z1, x1, False, 1.0)):
        ln = a1 - a0
        n = max(2, int(round((ln + gap) / (merlon + gap))))
        mw = (ln - (n - 1) * gap) / n
        for i in range(n):
            s0 = a0 + i * (mw + gap)
            s1 = s0 + mw
            if not along_x:
                # The corner merlons belong to the x runs.
                s0, s1 = max(s0, a0 + t), min(s1, a1 - t)
                if s1 - s0 < 0.2:
                    continue
            if along_x:
                lo = (s0, y, fixed - t if sign > 0 else fixed)
                hi = (s1, y + h, fixed if sign > 0 else fixed + t)
            else:
                lo = (fixed - t if sign > 0 else fixed, y, s0)
                hi = (fixed if sign > 0 else fixed + t, y + h, s1)
            geom.box(f, lo, hi, WALL, skip=("-y",))


def _slit(f: list, m: mk.Mesh, x: float, y: float, z: float, axis: str, sign: float, w: float = 0.18, h: float = 1.4) -> None:
    """An arrow slit on a face (x = const or z = const): a moulded frame 0.12
    proud round a dark recess."""
    fr, pr = 0.12, 0.12
    if axis == "z":
        zz = (z, z + sign * pr)
        lo_z, hi_z = min(zz), max(zz)
        for lo, hi in (((x - w / 2 - fr, y - h / 2 - fr, lo_z), (x + w / 2 + fr, y - h / 2, hi_z)),
                       ((x - w / 2 - fr, y + h / 2, lo_z), (x + w / 2 + fr, y + h / 2 + fr, hi_z)),
                       ((x - w / 2 - fr, y - h / 2, lo_z), (x - w / 2, y + h / 2, hi_z)),
                       ((x + w / 2, y - h / 2, lo_z), (x + w / 2 + fr, y + h / 2, hi_z))):
            tbox(f, lo, hi, "window_frame", 0.3, skip=("-z",) if sign > 0 else ("+z",))
        pts = [(x - w / 2, y - h / 2, z + sign * 0.03), (x + w / 2, y - h / 2, z + sign * 0.03),
               (x + w / 2, y + h / 2, z + sign * 0.03), (x - w / 2, y + h / 2, z + sign * 0.03)]
    else:
        xx = (x, x + sign * pr)
        lo_x, hi_x = min(xx), max(xx)
        for lo, hi in (((lo_x, y - h / 2 - fr, z - w / 2 - fr), (hi_x, y - h / 2, z + w / 2 + fr)),
                       ((lo_x, y + h / 2, z - w / 2 - fr), (hi_x, y + h / 2 + fr, z + w / 2 + fr)),
                       ((lo_x, y - h / 2, z - w / 2 - fr), (hi_x, y + h / 2, z - w / 2)),
                       ((lo_x, y - h / 2, z + w / 2), (hi_x, y + h / 2, z + w / 2 + fr))):
            tbox(f, lo, hi, "window_frame", 0.3, skip=("-x",) if sign > 0 else ("+x",))
        pts = [(x + sign * 0.03, y - h / 2, z - w / 2), (x + sign * 0.03, y - h / 2, z + w / 2),
               (x + sign * 0.03, y + h / 2, z + w / 2), (x + sign * 0.03, y + h / 2, z - w / 2)]
    nrm = (0.0, 0.0, sign) if axis == "z" else (sign, 0.0, 0.0)
    if geom.dot(geom.newell(pts), nrm) < 0:
        pts.reverse()
    m.poly(pts, "dark", zone=0)


def gatehouse(top: float = 23.0) -> Piece:
    """`kit_gatehouse_8x10`: round the courtyard gate (8 x 10): two square
    towers (TOWER_W x TOWER_D) flanking the passage on the courtyard side,
    rising over the 16 m wall (and back over it) to a corbelled parapet with
    merlons, their tops `top` (grid.GATEHOUSE_TOP) up; a machicolated gallery
    over the gate between them with an invented carved plaque; arrow slits;
    the raised portcullis in the passage, its iron-shod teeth
    PORTCULLIS_TEETH (at least grid.PORTCULLIS_CLEAR) over the floor.
    Origin on the gate's centre line on the wall face (the courtyard at
    z > 0). Walk-through (the towers carry a camera shell). At most
    grid.GATEHOUSE_TRIS."""
    lo, hi = G.GATEHOUSE_TOP
    if not lo - 1e-9 <= top <= hi + 1e-9:
        raise ValueError(f"kit_gatehouse_8x10: top must be {lo}..{hi} m")
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    wall_top = G.COURT_WALL_TOP
    para0 = TOWER_TOP  # the parapet's foot
    para1 = top - 1.3  # its walk (the merlons on it reach `top`)
    shell = []
    for side in (-1.0, 1.0):
        xa, xb = sorted((side * GATE_HALF, side * (GATE_HALF + TOWER_W)))
        xin, xout = (xa, xb) if side > 0 else (xb, xa)  # the passage side, the outer side
        pb = para0 + 0.4  # the body's top: the parapet ring's foot (corbels in front of the body below it)
        # The body: front, the outer side, the passage side (below the wall top: in front of the wall),
        # and above the wall top all round, back over the wall.
        geom.rect_z(f, xa, xb, 0.0, pb, TOWER_D, WALL, 1.0)
        for x, sg in ((xout, side), (xin, -side)):
            pts = [(x, 0.0, 0.0), (x, 0.0, TOWER_D), (x, wall_top, TOWER_D), (x, wall_top, 0.0)]
            add_face(f, pts, WALL, None, (sg, 0.0, 0.0))
        # Above the wall top: the outer side all the way back; the passage side beside and above the gallery.
        add_face(f, [(xout, wall_top, -TOWER_BACK), (xout, wall_top, TOWER_D), (xout, pb, TOWER_D),
                     (xout, pb, -TOWER_BACK)], WALL, None, (side, 0.0, 0.0))
        add_face(f, [(xin, wall_top, GALLERY_D), (xin, wall_top, TOWER_D), (xin, pb, TOWER_D), (xin, pb, GALLERY_D)],
                 WALL, None, (-side, 0.0, 0.0))
        add_face(f, [(xin, GALLERY_TOP, -TOWER_BACK), (xin, GALLERY_TOP, GALLERY_D), (xin, pb, GALLERY_D),
                     (xin, pb, -TOWER_BACK)], WALL, None, (-side, 0.0, 0.0))
        geom.rect_z(f, xa, xb, wall_top, pb, -TOWER_BACK, WALL, -1.0)
        # Its underside where it runs out beyond the wall's outer face (z -WALL_T: the review closed the slot it left).
        add_face(f, [(xa, wall_top, -TOWER_BACK), (xb, wall_top, -TOWER_BACK), (xb, wall_top, -WALL_T), (xa, wall_top, -WALL_T)],
                 WALL, None, (0.0, -1.0, 0.0))
        # A plinth round the foot: on the front, and on the outer side (round the corner).
        o0, o1 = (xout, xout + 0.3) if side > 0 else (xout - 0.3, xout)
        tbox(f, (xa, 0.0, TOWER_D), (xb, 0.5, TOWER_D + 0.3), "plinth", 0.6,
             skip=("-z", "-y", "+x") if side > 0 else ("-z", "-y", "-x"))
        tbox(f, (o0, 0.0, 0.0), (o1, 0.5, TOWER_D + 0.3), "plinth", 0.6, skip=("-y", "-z", "-x" if side > 0 else "+x"))
        # A string course at the wall top on the front.
        tbox(f, (xa, wall_top - 0.3, TOWER_D), (xb, wall_top, TOWER_D + 0.15), "string", 0.3, skip=("-z",))
        # The corbelled parapet: a ring stepping out 0.3 m on corbels, merlons on it.
        r0x, r1x, r0z, r1z = xa - 0.3, xb + 0.3, -TOWER_BACK - 0.3, TOWER_D + 0.3
        geom.box(f, (r0x, pb, r0z), (r1x, para1, r1z), WALL)
        _crenels(f, r0x, r1x, r0z, r1z, para1)
        for i in range(6):
            x = xa + 0.45 + i * (TOWER_W - 0.9) / 5
            fit_box(f, (x - 0.14, para0, TOWER_D), (x + 0.14, pb, TOWER_D + 0.3), "corbel_front", skip=("-z", "+y"))
        for i in range(7):
            z = -TOWER_BACK + 0.4 + i * (TOWER_D + TOWER_BACK - 0.8) / 6
            fit_box(f, (o0, para0, z - 0.14), (o1, pb, z + 0.14), "corbel_side", skip=("-x" if side > 0 else "+x", "+y"))
        # Arrow slits: up the front and up the outer side.
        xc = (xa + xb) / 2
        for y in (6.0, 12.5):
            _slit(f, m, xc, y, TOWER_D, "z", 1.0)
            _slit(f, m, xout, y + 1.6, TOWER_D / 2 - 0.4, "x", side)
        _slit(f, m, xc, 18.5, TOWER_D, "z", 1.0)
        shell += [
            [(xa, 0.0, TOWER_D), (xb, 0.0, TOWER_D), (xb, top, TOWER_D), (xa, top, TOWER_D)],
            [(xout, 0.0, 0.0), (xout, 0.0, TOWER_D), (xout, top, TOWER_D), (xout, top, 0.0)],
            [(xin, 0.0, 0.0), (xin, 0.0, TOWER_D), (xin, top, TOWER_D), (xin, top, 0.0)],
            [(xa, para1, -TOWER_BACK), (xb, para1, -TOWER_BACK), (xb, para1, TOWER_D), (xa, para1, TOWER_D)],
        ]
    # The gallery over the gate: a machicolated front on corbels, back over the wall, crenellated.
    g = GATE_HALF
    geom.rect_z(f, -g, g, GALLERY_FOOT, GALLERY_TOP, GALLERY_D, WALL, 1.0)
    add_face(f, [(-g, GALLERY_FOOT, 0.0), (g, GALLERY_FOOT, 0.0), (g, GALLERY_FOOT, GALLERY_D), (-g, GALLERY_FOOT, GALLERY_D)],
             WALL, None, (0.0, -1.0, 0.0))
    geom.rect_z(f, -g, g, wall_top, GALLERY_TOP, -TOWER_BACK, WALL, -1.0)
    add_face(f, [(-g, wall_top, -TOWER_BACK), (g, wall_top, -TOWER_BACK), (g, wall_top, -WALL_T), (-g, wall_top, -WALL_T)], WALL,
             None, (0.0, -1.0, 0.0))
    add_face(f, [(-g, GALLERY_TOP, GALLERY_D), (g, GALLERY_TOP, GALLERY_D), (g, GALLERY_TOP, -TOWER_BACK),
                 (-g, GALLERY_TOP, -TOWER_BACK)], WALL, None, (0.0, 1.0, 0.0))
    for i in range(5):
        x = -g + 0.8 + i * (2 * g - 1.6) / 4
        fit_box(f, (x - 0.18, GALLERY_FOOT - 0.9, 0.0), (x + 0.18, GALLERY_FOOT, GALLERY_D), "corbel_front", skip=("-z", "+y"))
    for zz, sg in ((GALLERY_D, 1.0), (-TOWER_BACK, -1.0)):
        n = 5
        mw, gap = 1.0, (2 * g - 5 * 1.0) / 4
        for i in range(n):
            x0_ = -g + i * (mw + gap)
            lo_ = (x0_, GALLERY_TOP, zz - 0.45 if sg > 0 else zz)
            hi_ = (x0_ + mw, GALLERY_TOP + 1.2, zz if sg > 0 else zz + 0.45)
            geom.box(f, lo_, hi_, WALL, skip=("-y",))
    tbox(f, (-g, GALLERY_FOOT, GALLERY_D), (g, GALLERY_FOOT + 0.3, GALLERY_D + 0.12), "string", 0.3, skip=("-z",))
    # An invented carved plaque on the gallery: a blank shield in a moulded panel (no heraldry).
    fit_box(f, (-1.0, 14.6, GALLERY_D), (1.0, 16.4, GALLERY_D + 0.12), "plaque", skip=("-z",))
    shield = [(-0.45, 16.2), (-0.45, 15.3), (-0.22, 14.95), (0.0, 14.8), (0.22, 14.95), (0.45, 15.3), (0.45, 16.2)]
    mk.prism(m, shield if mk.polygon_area(shield) > 0 else list(reversed(shield)), GALLERY_D + 0.12, GALLERY_D + 0.2,
             "metal", GILT, back=False)
    shell.append([(-g, GALLERY_FOOT, GALLERY_D), (g, GALLERY_FOOT, GALLERY_D), (g, GALLERY_TOP, GALLERY_D),
                  (-g, GALLERY_TOP, GALLERY_D)])
    # The raised portcullis in the passage: oak bars on a lattice, iron-shod teeth.
    z = PORTCULLIS_Z
    n_bars = 15
    for i in range(n_bars):
        x = -g + 0.35 + i * (2 * g - 0.7) / (n_bars - 1)
        wbox(f, (x - 0.06, PORTCULLIS_TEETH + 0.22, z - 0.06), (x + 0.06, GATE_H, z + 0.06), skip=("+y",), axis=1)
        tooth = [(x - 0.06, PORTCULLIS_TEETH + 0.22), (x + 0.06, PORTCULLIS_TEETH + 0.22), (x, PORTCULLIS_TEETH)]
        mk.prism(m, [(a, b) for a, b in reversed(tooth)] if mk.polygon_area(tooth) < 0 else tooth, z - 0.06, z + 0.06,
                 "metal", IRON)
    for y in (PORTCULLIS_TEETH + 0.9, PORTCULLIS_TEETH + 2.1, PORTCULLIS_TEETH + 3.2):
        wbox(f, (-g + 0.1, y, z + 0.06), (g - 0.1, y + 0.14, z + 0.16))
        wbox(f, (-g + 0.1, y, z - 0.16), (g - 0.1, y + 0.14, z - 0.06))
    # Its span on the wall face (the plinth and the string courses break round it, as round the counter).
    p.meta["wall"] = (-GATE_HALF - TOWER_W - 0.3, GATE_HALF + TOWER_W + 0.3, 0.0, top)
    p.meta.update(top=top, teeth=PORTCULLIS_TEETH, towers=[(-GATE_HALF - TOWER_W, -GATE_HALF), (GATE_HALF, GATE_HALF + TOWER_W)],
                  depth=TOWER_D, collider=shell)
    return _finish(p, m)


# =====================================================================================
# kit_lamp_post, kit_market_stall, kit_planter_tree
# =====================================================================================


def lamp_post(height: float = 4.0, light: float = 1) -> Piece:
    """`kit_lamp_post`: an iron post `height` (3.5-4.5 m) on a stone foot, a
    lantern on top (a glowing core in an iron cage under a pointed cap; the
    glow slot `glow_lantern`); `light` 1 brings a warm point light (meta
    "lights"; at most kit.LIGHTS_PER_CELL in the cell). At most
    grid.LAMP_POST_TRIS."""
    lo, hi = G.LAMP_POST_HEIGHT
    if not lo - 1e-9 <= height <= hi + 1e-9:
        raise ValueError(f"kit_lamp_post: height must be {lo}..{hi} m")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    h = height
    head = h - 0.75  # the lantern's foot
    mk.lathe(m, [(0.32, 0.0), (0.32, 0.3), (0.24, 0.42), (0.16, 0.5)], 8, "metal", STONE, cap_top=True)
    mk.lathe(m, [(0.075, 0.5), (0.06, 0.62), (0.05, head - 0.3), (0.075, head - 0.15), (0.12, head)], 8, "metal", IRON,
             cap_top=True)
    # The lantern: a tray, the glowing core, four corner bars, the cap and a ring.
    mk.lathe(m, [(0.12, head), (0.2, head + 0.04), (0.2, head + 0.08)], 4, "metal", IRON, cap_top=True, phase=math.pi / 4)
    mk.lathe(m, [(0.13, head + 0.08), (0.15, head + 0.45), (0.13, head + 0.5)], 4, "glow", GLOW, cap_top=True,
             phase=math.pi / 4)
    for k in range(4):
        a = math.pi / 4 + k * math.pi / 2
        x, z = 0.2 * math.cos(a), 0.2 * math.sin(a)
        mk.box(m, (x - 0.02, head + 0.08, z - 0.02), (x + 0.02, head + 0.52, z + 0.02), "metal", IRON, skip=("-y", "+y"))
    mk.lathe(m, [(0.25, head + 0.52), (0.2, head + 0.6), (0.06, h - 0.08), (0.0, h - 0.05)], 4, "metal", IRON,
             cap_top=False, phase=math.pi / 4)
    mk.lathe(m, [(0.0, h - 0.08), (0.04, h - 0.04), (0.0, h)], 6, "metal", IRON, cap_top=False)
    p.meta.update(height=h, lantern=head)
    if light >= 0.5:
        p.meta["lights"] = [{"pos": [0.0, head + 0.3, 0.0], "glow": "candle", "intensity": 12, "range": 9}]
    return _finish(p, m)


STALL_W, STALL_D, STALL_H = G.MARKET_STALL_SIZE


def market_stall(house: float = 0, goods: float = 0) -> Piece:
    """`kit_market_stall`: a 3 x 2 m oak stall facing +z: four posts, a
    counter hung with a house-coloured cloth, shelves at the back under a
    canvas, a striped awning (args.house 0-3 = house_a..d with `canvas`)
    sloping to a scalloped valance; invented wares by args.goods (0
    parcels, 1 jars, 2 scrolls, 3 quills and ink). Within
    grid.MARKET_STALL_SIZE, walk-through. At most grid.MARKET_STALL_TRIS."""
    h = int(round(house))
    gds = int(round(goods))
    if not 0 <= h < len(G.STALL_AWNINGS):
        raise ValueError("kit_market_stall: house must be 0..3")
    if not 0 <= gds < G.STALL_GOODS:
        raise ValueError(f"kit_market_stall: goods must be 0..{G.STALL_GOODS - 1}")
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    hw, hd = STALL_W / 2, STALL_D / 2
    front_h, back_h = 2.55, 3.0
    # Posts.
    for x in (-hw + 0.08, hw - 0.08):
        wbox(f, (x - 0.06, 0.0, hd - 0.2), (x + 0.06, front_h, hd - 0.08), skip=("-y",), axis=1)
        wbox(f, (x - 0.06, 0.0, -hd + 0.08), (x + 0.06, back_h, -hd + 0.2), skip=("-y",), axis=1)
    # The counter: an oak top on a boarded front, a cloth hung over it.
    top = 0.92
    wbox(f, (-hw + 0.15, top - 0.06, 0.25), (hw - 0.15, top, hd - 0.02))
    wbox(f, (-hw + 0.2, 0.0, hd - 0.2), (hw - 0.2, top - 0.06, hd - 0.12), tag=PLANKS, skip=("-y", "+y"))
    for x in (-hw + 0.2, hw - 0.2):
        wbox(f, (x - 0.02, 0.0, 0.3), (x + 0.02, top - 0.06, hd - 0.2), tag=PLANKS, skip=("-y", "+y"), axis=2)
    mk.box(m, (-hw + 0.3, 0.38, hd - 0.12), (hw - 0.3, top - 0.08, hd - 0.1), "fabric", HOUSE0 + h, skip=("-z",))
    mk.box(m, (-hw + 0.3, 0.38, hd - 0.1), (hw - 0.3, 0.46, hd - 0.085), "fabric", CANVAS, skip=("-z",))
    # The back: two shelves on the back posts, a canvas behind.
    for y in (1.2, 1.75):
        wbox(f, (-hw + 0.15, y - 0.04, -hd + 0.2), (hw - 0.15, y, -hd + 0.5))
    m.poly([(-hw + 0.15, 0.6, -hd + 0.12), (hw - 0.15, 0.6, -hd + 0.12), (hw - 0.15, back_h - 0.1, -hd + 0.12),
            (-hw + 0.15, back_h - 0.1, -hd + 0.12)], "fabric", zone=CANVAS)
    m.poly([(hw - 0.15, 0.6, -hd + 0.11), (-hw + 0.15, 0.6, -hd + 0.11), (-hw + 0.15, back_h - 0.1, -hd + 0.11),
            (hw - 0.15, back_h - 0.1, -hd + 0.11)], "fabric", zone=CANVAS)
    # The awning: stripes from the back posts' tops down past the front posts, both faces.
    za, ya = -hd + 0.05, back_h + 0.05
    zb, yb = hd + 0.35, front_h - 0.12
    stripes = 6
    for i in range(stripes):
        x0, x1 = -hw - 0.1 + i * (STALL_W + 0.2) / stripes, -hw - 0.1 + (i + 1) * (STALL_W + 0.2) / stripes
        zone = HOUSE0 + h if i % 2 == 0 else CANVAS
        m.poly([(x0, yb, zb), (x1, yb, zb), (x1, ya, za), (x0, ya, za)], "fabric", zone=zone)
        m.poly([(x1, yb - 0.02, zb), (x0, yb - 0.02, zb), (x0, ya - 0.02, za), (x1, ya - 0.02, za)], "fabric", zone=zone)
        # The valance: a scalloped flap under the stripe, both faces.
        xm = (x0 + x1) / 2
        flap = [(x0, yb, zb), (x1, yb, zb), (x1, yb - 0.2, zb), (xm, yb - 0.32, zb), (x0, yb - 0.2, zb)]
        m.poly(flap[::-1], "fabric", zone=zone)
        m.poly([(x, y, z - 0.01) for x, y, z in flap], "fabric", zone=zone)
    # Wares on the counter and the shelves.
    _wares(m, gds, top, hw, hd)
    p.meta.update(size=(STALL_W, STALL_D), height=back_h + 0.1, house=G.STALL_AWNINGS[h], goods=gds)
    return _finish(p, m)


def _wares(m: mk.Mesh, goods: int, top: float, hw: float, hd: float) -> None:
    """Invented wares: on the counter (z 0.3..0.95) and the two shelves."""
    shelf_z = -hd + 0.35
    if goods == 0:  # parcels tied with string
        for x, y, z, w, hh, d, zone in ((-0.9, top, 0.62, 0.42, 0.26, 0.34, PARCEL), (-0.35, top, 0.6, 0.3, 0.2, 0.3, LETTER),
                                        (0.5, top, 0.62, 0.5, 0.3, 0.36, PARCEL), (-0.8, 1.2, shelf_z, 0.5, 0.36, 0.26, PARCEL),
                                        (0.1, 1.2, shelf_z, 0.4, 0.3, 0.24, PARCEL), (0.8, 1.75, shelf_z, 0.46, 0.3, 0.26, LETTER),
                                        (-0.4, 1.75, shelf_z, 0.6, 0.28, 0.24, PARCEL)):
            mk.box(m, (x - w / 2, y, z - d / 2), (x + w / 2, y + hh, z + d / 2), "fabric", zone, skip=("-y",))
            mk.box(m, (x - 0.015, y + hh, z - d / 2 - 0.005), (x + 0.015, y + hh + 0.01, z + d / 2 + 0.005),
                   "fabric" if zone == PARCEL else "leather", LETTER if zone == PARCEL else RIBBON, skip=("-y",))
    elif goods == 1:  # jars and pots
        for i, (x, y, z, r, hh, zone) in enumerate(((-1.0, top, 0.6, 0.12, 0.3, COPPER), (-0.6, top, 0.65, 0.1, 0.24, GLASS),
                                                     (-0.2, top, 0.6, 0.13, 0.34, PEWTER), (0.3, top, 0.62, 0.1, 0.22, GLASS),
                                                     (0.75, top, 0.6, 0.14, 0.28, COPPER), (-0.9, 1.2, shelf_z, 0.1, 0.26, PEWTER),
                                                     (-0.3, 1.2, shelf_z, 0.12, 0.3, GLASS), (0.4, 1.2, shelf_z, 0.11, 0.24, COPPER),
                                                     (-0.5, 1.75, shelf_z, 0.1, 0.22, GLASS), (0.6, 1.75, shelf_z, 0.12, 0.28, PEWTER))):
            mk.lathe(m, [(r * 0.7, 0.0), (r, hh * 0.25), (r, hh * 0.7), (r * 0.55, hh * 0.85), (r * 0.6, hh)], 6, "metal"
                     if zone != GLASS else "glass", zone, (x, y, z), cap_top=True)
    elif goods == 2:  # scrolls on the counter and racks of them on the shelves
        for i in range(6):
            x = -1.0 + i * 0.38
            mk.rod(m, (x - 0.14, top + 0.05, 0.5 + 0.1 * (i % 2)), (x + 0.14, top + 0.05, 0.62 + 0.1 * (i % 2)), 0.045, 6,
                   "fabric", LETTER)
            mk.box(m, (x - 0.02, top + 0.0, 0.55 + 0.1 * (i % 2) - 0.05), (x + 0.02, top + 0.1, 0.57 + 0.1 * (i % 2) + 0.05),
                   "leather", RIBBON, skip=("-y",))
        for y in (1.2, 1.75):
            for i in range(8):
                x = -1.1 + i * 0.31
                mk.rod(m, (x, y + 0.05, shelf_z - 0.12), (x, y + 0.05, shelf_z + 0.12), 0.05, 6, "fabric", LETTER)
    else:  # quills in pots, ink bottles
        for i, x in enumerate((-1.0, -0.4, 0.2, 0.8)):
            mk.lathe(m, [(0.07, 0.0), (0.08, 0.12), (0.06, 0.14)], 6, "glass", INK, (x, top, 0.6), cap_top=True)
            mk.lathe(m, [(0.09, 0.0), (0.1, 0.16)], 6, "metal", PEWTER, (x + 0.25, top, 0.68), cap_top=True)
            for q in range(3):
                a = q * 2.1 + i
                tip = (x + 0.25 + 0.12 * math.cos(a), top + 0.5, 0.68 + 0.12 * math.sin(a))
                mk.rod(m, (x + 0.25, top + 0.1, 0.68), tip, 0.025, 3, "fabric", OWL_PALE)
        for y in (1.2, 1.75):
            for i in range(7):
                mk.lathe(m, [(0.05, 0.0), (0.06, 0.1), (0.03, 0.14)], 6, "glass", INK if i % 2 else GLASS,
                         (-1.0 + i * 0.33, y, shelf_z), cap_top=True)


SOIL_Y = 0.68  # the planter's soil, where the tree stands


def planter_tree(size: float = 1.8, height: float = 5.2, seed: float = 0) -> Piece:
    """`kit_planter_tree`: a square stone planter `size` (1.2-2.4 m) side,
    0.75 m high with a moulded coping, and its tree (vegetation look A: a
    planting of the foliage kit's `broadleaf_planter` in meta["foliage"],
    world/foliage.py), its crown's underside at least OVERHEAD_CLEARANCE up,
    its top `height` (4-7 m). `seed` turns it. The planter itself at most
    grid.PLANTER_TREE_TRIS."""
    lo, hi = G.PLANTER_SIZE
    if not lo - 1e-9 <= size <= hi + 1e-9:
        raise ValueError(f"kit_planter_tree: size must be {lo}..{hi} m")
    if not G.PLANTER_TREE_HEIGHT[0] - 1e-9 <= height <= G.PLANTER_TREE_HEIGHT[1] + 1e-9:
        raise ValueError("kit_planter_tree: height must be within grid.PLANTER_TREE_HEIGHT")
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    s = size / 2
    vprism(f, octagon(0.0, 0.0, s, 0.12), 0.0, 0.62, WALL, top=False)
    vprism(f, octagon(0.0, 0.0, s + 0.06, 0.15), 0.62, 0.75, trim("string"), 0.3, top=False)
    outer, inner = octagon(0.0, 0.0, s + 0.06, 0.15), octagon(0.0, 0.0, s - 0.12, 0.08)
    for i in range(8):
        j = (i + 1) % 8
        add_face(f, [(outer[i][0], 0.75, outer[i][1]), (outer[j][0], 0.75, outer[j][1]), (inner[j][0], 0.75, inner[j][1]),
                     (inner[i][0], 0.75, inner[i][1])], trim("string"), [(0, 0.25), (1, 0.25), (1, 0.05), (0, 0.05)],
                 (0.0, 1.0, 0.0))
    m.poly([(x, SOIL_Y, z) for x, z in reversed(inner)], "stone", zone=SOOT)
    for i in range(8):
        (xa, za), (xb, zb) = inner[i], inner[(i + 1) % 8]
        add_face(f, [(xa, 0.68, za), (xb, 0.68, zb), (xb, 0.75, zb), (xa, 0.75, za)], WALL, None,
                 (-(xa + xb) / 2, 0.0, -(za + zb) / 2))
    # The tree (vegetation look A, world/foliage.py): a planting of the kit's planter broadleaf
    # standing on the soil, its top at `height`, turned by seed.
    k = int(round(seed))
    pl = FO.tree("planter", height - SOIL_Y, k, pos=(0.0, SOIL_Y, 0.0), yaw=(0.7 * k) % (2 * math.pi))
    low = SOIL_Y + FO.SPECIES[pl.species].crown_low * pl.scale()
    p.meta.update(size=size, height=round(height, 4), crown_low=round(low, 4), foliage=[pl])
    return _finish(p, m)
