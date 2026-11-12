"""Castle M5c (Facades): the pieces of the castle's outside a player in the
courtyard sees (docs/art/castle-kit.md "Exterior").

- `kit_buttress_<h>`: a stepped buttress `h` metres tall against an outer
  face: a plinth, three stages that step back with sloped weatherings, a
  flat top a pinnacle can stand on (`cap` "flat") or a weathered top
  sloping back into the wall.
- `kit_parapet_crenel`: a crenellated parapet `length` metres long on a wall
  top: an oversailing breast wall on a chamfered corbel course, merlons
  with copings and embrasures between them, with thickness (no flat card).
- `kit_pinnacle`: a square shaft on a plinth, four gablets, a crocketed
  spire and a finial knob (our own design).
- `kit_roof_slate_<w>x<l>`: a pitched slate roof over a cell's roof slab,
  `w` across the ridge, `l` along it: two slate slopes with thickness and an
  overhang at the eaves and verges, a ridge roll, a stone corbel cornice
  under each eave and the two gables in wall stone.
- `kit_window_tracery_outer_<w>x<h>`: the outer order of a traceried window
  whose wall mass is shared with the courtyard (the window over the front
  doors): the gabled screen wall that carries the window's head above the
  courtyard's wall top, the soffit through the mass, a moulded surround
  and hood mould on the courtyard face, a weathered sill, stone mullions
  and head tracery matching the inner window's, glass lights behind them,
  small pinnacles on the screen's shoulders and a finial on its apex.
- `kit_window_blind_<w>x<h>` (castle M5c review): a lancet in relief on an
  outer face that takes no cut (the west range's storeys).

Frames as in geom.py (x along the wall to the right of someone facing it,
y up, z out of the wall face toward the viewer): an outside piece's +z is
the outward normal. The roof's frame is its own: x along the ridge from
0 at one gable to `l`, z across it (the ridge at z = 0), y = 0 at the wall
top (the outer planes' top edge). Stone faces are `castle_wall|world`, the
roof `castle_roof|world`; assemble.py turns every face of a facade piece
into its exterior clone (`@ext`) but `castle_glass` and
`castle_roof`. Every piece carries an LOD1 (grid.FACADE_LOD) without its
crockets, mouldings and tracery. Pure Python; no colour literal here
(colours come from the textures: palette data, docs/art/castle-kit.md "Colours come from palette data only").
"""

from __future__ import annotations

import math

from castle_kit import grid as G
from castle_kit.pieces import geom, openings
from castle_kit.pieces.geom import Piece, add_face

WALL = "castle_wall|world"
ROOF = "castle_roof|world"
GLASS_TALL = "castle_glass|fit:light_tall"
GLASS_HEAD = "castle_glass|fit:head_tracery"


def trim(strip: str) -> str:
    return f"castle_trim|trim:{strip}"


def _quad(faces, a, b, c, d, tag=WALL, normal=None) -> None:
    add_face(faces, [a, b, c, d], tag, None, normal)


def _box(faces, lo, hi, tag=WALL, skip=()) -> None:
    geom.box(faces, lo, hi, tag, skip=skip)


def _shift(faces, dx=0.0, dy=0.0, dz=0.0) -> list:
    return [geom.KFace([(x + dx, y + dy, z + dz) for x, y, z in f.pts], f.tag, f.uv, f.anchor) for f in faces]


# -- kit_buttress_<h> ------------------------------------------------------------------------


def buttress(height: float, proud: float = 1.2, top_proud: float | None = None, width: float = 1.2,
             stages: int = 3, cap: str = "flat", plinth: float | None = None, back: float = 0.0) -> Piece:
    """`kit_buttress_<h>` (grid.BUTTRESS_HEIGHT 6-30 m): its foot `proud`
    metres off the wall (grid.BUTTRESS_PROUD), stepping back in `stages`
    stages with 45-degree weatherings to `top_proud` at the top; a plinth a
    little wider at the foot. `cap` "flat" leaves a flat top (meta "top":
    the centre a pinnacle stands on), "slope" weathers the top back into
    the wall; `plinth` the plinth's height (None: by the height; 0: none,
    standing on a wall top); `back` how far its sides and top run on behind
    the face (a corner pier clasping an outer corner the shell leaves open).
    y = 0 is the foot, x = 0 its centre line."""
    lo_h, hi_h = G.BUTTRESS_HEIGHT
    if not lo_h - 1e-9 <= height <= hi_h + 1e-9:
        raise ValueError(f"kit_buttress: height {height} outside {G.BUTTRESS_HEIGHT}")
    lo_p, hi_p = G.BUTTRESS_PROUD
    if not lo_p - 1e-9 <= proud <= hi_p + 1e-9:
        raise ValueError(f"kit_buttress: proud {proud} outside {G.BUTTRESS_PROUD}")
    top_proud = top_proud if top_proud is not None else max(0.5, proud * 0.6)
    p = Piece("")
    f = p.faces
    hw = width / 2.0
    plinth = min(0.8, height * 0.08) if plinth is None else plinth
    if plinth > 0:
        # The plinth: a block 0.1 wider each side and 0.15 deeper than the first stage.
        _box(f, (-hw - 0.1, 0.0, -back), (hw + 0.1, plinth, proud + 0.15), skip=("-z", "-y"))
    top_cap = 0.0 if cap == "flat" else min(1.2, top_proud)
    body_top = height - top_cap
    ys = [plinth + (body_top - plinth) * k / stages for k in range(stages + 1)]
    ds = [proud + (top_proud - proud) * k / max(1, stages - 1) for k in range(stages)]
    for k in range(stages):
        y0, y1, d = ys[k], ys[k + 1], ds[k]
        front0 = y0
        if k > 0:
            # The weathering from the stage below: a 45-degree slope from its face back to this one's.
            dp = ds[k - 1]
            wh = dp - d
            _quad(f, (-hw, y0, dp), (hw, y0, dp), (hw, y0 + wh, d), (-hw, y0 + wh, d), normal=(0.0, 1.0, 1.0))
            for sx in (-1.0, 1.0):
                add_face(f, [(sx * hw, y0, dp), (sx * hw, y0 + wh, d), (sx * hw, y0, d)], WALL, None, (sx, 0.0, 0.0))
            front0 = y0 + wh
        _quad(f, (-hw, front0, d), (hw, front0, d), (hw, y1, d), (-hw, y1, d), normal=(0.0, 0.0, 1.0))
        for sx in (-1.0, 1.0):
            _quad(f, (sx * hw, y0, -back), (sx * hw, y0, d), (sx * hw, y1, d), (sx * hw, y1, -back), normal=(sx, 0.0, 0.0))
    d = ds[-1]
    if cap == "flat":
        _quad(f, (-hw, height, d), (hw, height, d), (hw, height, -back), (-hw, height, -back), normal=(0.0, 1.0, 0.0))
    else:
        _quad(f, (-hw, body_top, d), (hw, body_top, d), (hw, height, 0.0), (-hw, height, 0.0), normal=(0.0, 1.0, 1.0))
        for sx in (-1.0, 1.0):
            add_face(f, [(sx * hw, body_top, 0.0), (sx * hw, body_top, d), (sx * hw, height, 0.0)], WALL, None, (sx, 0.0, 0.0))
            if back > 0:
                _quad(f, (sx * hw, body_top, -back), (sx * hw, body_top, 0.0), (sx * hw, height, 0.0), (sx * hw, height, -back),
                      normal=(sx, 0.0, 0.0))
        if back > 0:
            _quad(f, (-hw, height, 0.0), (hw, height, 0.0), (hw, height, -back), (-hw, height, -back), normal=(0.0, 1.0, 0.0))
    # LOD1: one stepped-back slab and its top.
    lod: list = []
    _box(lod, (-hw, 0.0, -back), (hw, height, (proud + d) / 2.0), skip=("-z", "-y"))
    p.lod1 = lod
    p.meta.update(top=(0.0, height, d / 2.0), top_depth=d, width=width, proud=proud, back=back)
    return p


# -- kit_parapet_crenel ----------------------------------------------------------------------


def crenels(length: float, merlon: float = 1.0, gap: float = 0.6) -> list[tuple[float, float]]:
    """The merlons' (x0, x1) along a parapet `length` long: merlons at both
    ends, widths within grid.PARAPET_MERLON and embrasures 0.5-0.8 m, as
    close to `merlon` / `gap` as the length allows."""
    lo_m, hi_m = G.PARAPET_MERLON
    n = max(1, round((length + gap) / (merlon + gap)))
    for k in (n, n - 1, n + 1, n - 2, n + 2):
        if k < 1:
            continue
        if k == 1:
            if length < 2 * lo_m + 0.5:  # too short for two: one merlon in the middle of the breast wall
                m = min(hi_m, length)
                return [((length - m) / 2.0, (length + m) / 2.0)]
            continue
        for g in (gap, 0.5, 0.8, 0.65):
            m = (length - (k - 1) * g) / k
            if lo_m - 1e-9 <= m <= hi_m + 1e-9:
                return [(i * (m + g), i * (m + g) + m) for i in range(k)]
        # Solve for the embrasure with the merlon held at its nearest bound.
        for m in (merlon, lo_m, hi_m):
            g = (length - k * m) / (k - 1)
            if 0.5 - 1e-9 <= g <= 0.8 + 1e-9:
                return [(i * (m + g), i * (m + g) + m) for i in range(k)]
    raise ValueError(f"kit_parapet_crenel: no crenel rhythm fits {length} m")


def parapet(length: float, height: float = 1.6, breast: float = 0.95, thick: float = 0.45, proud: float = 0.12,
            merlon: float = 1.0, gap: float = 0.6, ends=("cap", "cap")) -> Piece:
    """`kit_parapet_crenel`: `length` metres (2-60) along x from 0, its
    base on the wall top (y = 0), the front `proud` metres in front of the
    wall face (z = 0) on a chamfered corbel course, the back `thick` behind
    the front; a breast wall `breast` high and merlons to `height`
    (grid.PARAPET_HEIGHT) with copings. `ends` "cap" closes an end, "none"
    leaves it to a pinnacle or the next run."""
    if not 2.0 - 1e-9 <= length <= 60.0 + 1e-9:
        raise ValueError(f"kit_parapet_crenel: length {length} outside 2-60 m")
    lo_h, hi_h = G.PARAPET_HEIGHT
    if not lo_h - 1e-9 <= height <= hi_h + 1e-9:
        raise ValueError(f"kit_parapet_crenel: height {height} outside {G.PARAPET_HEIGHT}")
    p = Piece("")
    f = p.faces
    zf, zb = proud, proud - thick
    cope_h, cope_o = 0.12, 0.06
    mer = crenels(length, merlon, gap)
    # The corbel course: a chamfer from the wall face up and out to the front.
    _quad(f, (0.0, -0.3, 0.0), (length, -0.3, 0.0), (length, 0.0, zf), (0.0, 0.0, zf), normal=(0.0, -1.0, 1.0))
    # The breast wall, its front cut at every merlon edge (the shading follows the rhythm).
    cuts = sorted({0.0, length, *[x for m in mer for x in m]})
    for a, b in zip(cuts, cuts[1:]):
        _quad(f, (a, 0.0, zf), (b, 0.0, zf), (b, breast, zf), (a, breast, zf), normal=(0.0, 0.0, 1.0))
        _quad(f, (b, 0.0, zb), (a, 0.0, zb), (a, breast, zb), (b, breast, zb), normal=(0.0, 0.0, -1.0))
    # Embrasure sills: the breast's top wherever no merlon stands on it.
    edges = [0.0] + [x for m in mer for x in m] + [length]
    for a, b in zip(edges[0::2], edges[1::2]):
        if b - a > 1e-6:
            _quad(f, (a, breast, zf), (b, breast, zf), (b, breast, zb), (a, breast, zb), normal=(0.0, 1.0, 0.0))
    for k, (sx, x) in enumerate(((-1.0, 0.0), (1.0, length))):
        end_m = mer[0][0] <= 1e-9 if k == 0 else mer[-1][1] >= length - 1e-9
        if ends[k] == "cap" and not end_m:
            _quad(f, (x, 0.0, zb), (x, 0.0, zf), (x, breast, zf), (x, breast, zb), normal=(sx, 0.0, 0.0))
        if ends[k] == "cap":
            add_face(f, [(x, -0.3, 0.0), (x, 0.0, zf), (x, 0.0, 0.0)], WALL, None, (sx, 0.0, 0.0))
    # Merlons with copings.
    for x0, x1 in mer:
        top = height - cope_h
        _quad(f, (x0, breast, zf), (x1, breast, zf), (x1, top, zf), (x0, top, zf), normal=(0.0, 0.0, 1.0))
        _quad(f, (x1, breast, zb), (x0, breast, zb), (x0, top, zb), (x1, top, zb), normal=(0.0, 0.0, -1.0))
        for sx, x in ((-1.0, x0), (1.0, x1)):
            if (sx < 0 and x0 <= 1e-9 and ends[0] != "cap") or (sx > 0 and x1 >= length - 1e-9 and ends[1] != "cap"):
                continue
            ylo = 0.0 if (x <= 1e-9 or x >= length - 1e-9) else breast
            _quad(f, (x, ylo, zb), (x, ylo, zf), (x, top, zf), (x, top, zb), normal=(sx, 0.0, 0.0))
        lo = (x0 - cope_o, top, zb - cope_o)
        hi = (x1 + cope_o, height, zf + cope_o)
        _box(f, lo, hi)
    lod: list = []
    _quad(lod, (0.0, 0.0, zf), (length, 0.0, zf), (length, breast, zf), (0.0, breast, zf), normal=(0.0, 0.0, 1.0))
    _quad(lod, (length, 0.0, zb), (0.0, 0.0, zb), (0.0, breast, zb), (length, breast, zb), normal=(0.0, 0.0, -1.0))
    for (a0, a1), (b0, _b1) in zip(mer, mer[1:]):
        _quad(lod, (a1, breast, zf), (b0, breast, zf), (b0, breast, zb), (a1, breast, zb), normal=(0.0, 1.0, 0.0))
    for x0, x1 in mer:
        _box(lod, (x0, breast, zb), (x1, height, zf), skip=("-y",))
    p.lod1 = lod
    p.meta.update(merlons=mer, height=height, breast=breast, front=zf, back=zb)
    return p


# -- kit_pinnacle ----------------------------------------------------------------------------


def pinnacle(height: float = 4.5, width: float = 0.8, crockets: int = 4) -> Piece:
    """`kit_pinnacle` (grid.PINNACLE_HEIGHT 3-6 m): a plinth, a square shaft
    `width` wide, a gablet on each side at the shaft's top, a square spire
    with `crockets` crockets up each edge and a finial knob. y = 0 is its
    foot, x = z = 0 its axis (it stands on a wall top or a buttress)."""
    lo_h, hi_h = G.PINNACLE_HEIGHT
    if not lo_h - 1e-9 <= height <= hi_h + 1e-9:
        raise ValueError(f"kit_pinnacle: height {height} outside {G.PINNACLE_HEIGHT}")
    p = Piece("")
    f = p.faces
    h = width / 2.0
    plinth = 0.25 * height / 4.5
    shaft = 0.42 * height
    gable = 0.55 * width
    apex = 0.9 * height
    sh = h * 0.86  # the spire's half width at its foot (inside the gablets)
    _box(f, (-h - 0.06, 0.0, -h - 0.06), (h + 0.06, plinth, h + 0.06), skip=("-y",))
    _box(f, (-h, plinth, -h), (h, shaft, h), skip=("-y",))
    # Gablets: a gable on each face of the shaft, its copings sloping back to the spire.
    for k in range(4):
        yaw = k * math.pi / 2
        local: list = []
        add_face(local, [(-h, shaft, h), (h, shaft, h), (0.0, shaft + gable, h)], WALL, None, (0.0, 0.0, 1.0))
        for sx in (-1.0, 1.0):
            add_face(local, [(sx * h, shaft, h), (0.0, shaft + gable, h), (0.0, shaft + gable, 0.0), (sx * h, shaft, 0.0)],
                     WALL, None, (sx, 1.0, 0.0))
        f.extend(geom.KFace([geom.rot_y(q, yaw) for q in fc.pts], fc.tag, fc.uv) for fc in local)
    # The spire: four faces from the shaft top to the apex, cut in bands (the shading's vertices).
    bands = [shaft + (apex - shaft) * t for t in (0.0, 0.34, 0.66, 1.0)]
    halfs = [sh * (1.0 - (y - shaft) / (apex - shaft)) for y in bands]
    for k in range(4):
        yaw = k * math.pi / 2
        for i in range(3):
            y0, y1, a, b = bands[i], bands[i + 1], halfs[i], halfs[i + 1]
            pts = [(-a, y0, a), (a, y0, a), (b, y1, b), (-b, y1, b)] if b > 1e-6 else [(-a, y0, a), (a, y0, a), (0.0, y1, 0.0)]
            add_face(f, [geom.rot_y(q, yaw) for q in pts], WALL, None, geom.rot_y((0.0, sh, apex - shaft), yaw))
    # Crockets: small leaf hooks up the spire's four edges.
    s = 0.16 * width / 0.8
    for k in range(4):
        yaw = k * math.pi / 2 + math.pi / 4
        for i in range(crockets):
            t = (i + 0.6) / (crockets + 0.4)
            y = shaft + gable * 0.6 + (apex - shaft - gable * 0.6) * t
            r = sh * (1.0 - (y - shaft) / (apex - shaft)) * math.sqrt(2.0)
            # (In this frame the edge runs up the spire at z = r: a small tetrahedron hooks out of it.)
            base = [(-s * 0.7, y, r - 0.01), (s * 0.7, y, r - 0.01), (0.0, y + s * 1.2, r - 0.02)]
            tip = (0.0, y + s * 0.9, r + s * 1.1)
            centre = (0.0, y + s * 0.4, r - 0.015)
            for a, b in ((0, 1), (1, 2), (2, 0)):
                pts = [base[a], base[b], tip]
                mid = tuple((pts[0][k] + pts[1][k] + pts[2][k]) / 3.0 for k in range(3))
                add_face(f, [geom.rot_y(q, yaw) for q in pts], WALL, None, geom.rot_y(geom.sub(mid, centre), yaw))
    # The finial: an octahedral knob on the apex.
    kn = 0.13 * width / 0.8
    top = (0.0, height, 0.0)
    mid = apex + (height - apex) * 0.35
    ring = [(kn * math.cos(a), mid, kn * math.sin(a)) for a in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2)]
    for i in range(4):
        a, b = ring[i], ring[(i + 1) % 4]
        for tip in (top, (0.0, apex - 0.02, 0.0)):
            pts = [a, b, tip]
            out = geom.sub(((a[0] + b[0] + tip[0]) / 3, (a[1] + b[1] + tip[1]) / 3, (a[2] + b[2] + tip[2]) / 3), (0.0, mid, 0.0))
            add_face(f, pts, WALL, None, out)
    lod: list = []
    _box(lod, (-h, 0.0, -h), (h, shaft, h), skip=("-y", "+y"))
    for k in range(4):
        yaw = k * math.pi / 2
        add_face(lod, [geom.rot_y(q, yaw) for q in [(-h, shaft, h), (h, shaft, h), (0.0, height, 0.0)]], WALL, None,
                 geom.rot_y((0.0, h, height - shaft), yaw))
    p.lod1 = lod
    p.meta.update(width=width + 0.12, height=height)
    return p


# -- kit_roof_slate_<w>x<l> ------------------------------------------------------------------


def roof_pitch_rise(span: float, pitch: float) -> float:
    """The ridge's height over the outer planes' top edge for a roof `span` wide."""
    return span / 2.0 * math.tan(math.radians(pitch))


def roof_slate(span: float, length: float, pitch: float = 50.0, overhang: float = 0.3, verge: float = 0.3,
               thick: float = 0.3, gables=(1, 1), cornice: float = 0.6) -> Piece:
    """`kit_roof_slate_<w>x<l>`: a pitched roof (grid.ROOF_PITCH_DEG) over a
    `span` x `length` slab (the outer planes' rectangle): its underside meets
    the outer planes' top edge (y = 0 at z = +-span / 2), the slopes `thick`
    thick (slate on top, `castle_roof`), `overhang` beyond the eaves and
    `verge` beyond the gables; a ridge roll; under each eave a stone corbel
    cornice `cornice` deep on the wall face (0: none); each gable (`gables`
    at x = 0 and x = length, 1 to draw) a wall-stone triangle on its outer
    plane. Pitches over 45 degrees course the world-mapped slates along the
    ridge (the world UVs project on the slopes' dominant vertical axis)."""
    lo_p, hi_p = G.ROOF_PITCH_DEG
    if not lo_p - 1e-9 <= pitch <= hi_p + 1e-9:
        raise ValueError(f"kit_roof_slate: pitch {pitch} outside {G.ROOF_PITCH_DEG}")
    p = Piece("")
    f = p.faces
    lod: list = []
    tan = math.tan(math.radians(pitch))
    hw = span / 2.0
    rise = hw * tan
    tv = thick / math.cos(math.radians(pitch))  # the slab's vertical thickness
    ze = hw + overhang
    ye = -overhang * tan  # the underside at the eave's edge
    x0, x1 = -verge, length + verge
    for sz in (-1.0, 1.0):
        out = (0.0, math.cos(math.radians(pitch)), sz * math.sin(math.radians(pitch)))
        # The slate slope (top).
        top = [(x0, ye + tv, sz * ze), (x1, ye + tv, sz * ze), (x1, rise + tv, 0.0), (x0, rise + tv, 0.0)]
        for faces in (f, lod):
            add_face(faces, top, ROOF, None, out)
        # The eave's fascia (the slab's edge) and its soffit under the overhang.
        fascia = [(x0, ye, sz * ze), (x1, ye, sz * ze), (x1, ye + tv, sz * ze), (x0, ye + tv, sz * ze)]
        for faces in (f, lod):
            add_face(faces, fascia, ROOF, None, (0.0, 0.0, sz))
        # (Between the gables: the verges' undersides cover the corners beyond them.)
        add_face(f, [(0.0, ye, sz * ze), (length, ye, sz * ze), (length, 0.0, sz * hw), (0.0, 0.0, sz * hw)], ROOF, None,
                 (0.0, -1.0, -sz * tan))
        # The verges: the slab's end edge and the underside beyond each gable.
        for sx, x in ((-1.0, x0), (1.0, x1)):
            edge = [(x, ye, sz * ze), (x, ye + tv, sz * ze), (x, rise + tv, 0.0), (x, rise, 0.0)]
            for faces in (f, lod):
                add_face(faces, edge, ROOF, None, (sx, 0.0, 0.0))
            xg = 0.0 if sx < 0 else length
            add_face(f, [(x, ye, sz * ze), (x, rise, 0.0), (xg, rise, 0.0), (xg, ye, sz * ze)], ROOF, None,
                     (0.0, -1.0, -sz * tan))
        # The corbel cornice under the eave: a chamfer out from the wall face and a band up to
        # the eave's soffit (which slopes down and out from the wall's top edge).
        if cornice > 0:
            zc = sz * hw
            out_c = 0.16
            y_band = -out_c * tan - 0.005  # the soffit's height over the band's face
            add_face(f, [(0.0, -cornice, zc), (length, -cornice, zc), (length, -cornice * 0.5, zc + sz * out_c),
                         (0.0, -cornice * 0.5, zc + sz * out_c)], WALL, None, (0.0, -1.0, sz * 1.0))
            add_face(f, [(0.0, -cornice * 0.5, zc + sz * out_c), (length, -cornice * 0.5, zc + sz * out_c),
                         (length, y_band, zc + sz * out_c), (0.0, y_band, zc + sz * out_c)], WALL, None, (0.0, 0.0, sz))
            for sx, x in ((-1.0, 0.0), (1.0, length)):
                add_face(f, [(x, -cornice, zc), (x, -cornice * 0.5, zc + sz * out_c), (x, y_band, zc + sz * out_c),
                             (x, 0.0, zc)], WALL, None, (sx, 0.0, 0.0))
    # The underside over the footprint, between the gables (the slab's inside: an outer corner
    # the shell leaves open, or a gap beside the roof, never shows the sky through the roof).
    for sz in (-1.0, 1.0):
        under = [(0.0, 0.0, sz * hw), (length, 0.0, sz * hw), (length, rise, 0.0), (0.0, rise, 0.0)]
        for faces in (f, lod):
            add_face(faces, under, ROOF, None, (0.0, -1.0, -sz * tan))
    # The ridge roll: a small capped box along the ridge.
    rr = 0.16
    yr = rise + tv - rr * tan - 0.02  # its sides reach down to the slopes
    _box(f, (x0 - 0.06, yr, -rr), (x1 + 0.06, rise + tv + rr, rr), tag=ROOF, skip=("-y",))
    # The gables: wall-stone triangles on the outer planes at x = 0 and x = length.
    for flag, sx, x in ((gables[0], -1.0, 0.0), (gables[1], 1.0, length)):
        if not flag:
            continue
        tri = [(x, 0.0, -hw), (x, 0.0, hw), (x, rise, 0.0)]
        for faces in (f, lod):
            add_face(faces, tri, WALL, None, (sx, 0.0, 0.0))
    p.lod1 = lod
    p.meta.update(rise=rise, ridge=rise + tv + rr, pitch=pitch, eave=ye, overhang=overhang, verge=verge)
    return p


def roof_underside(span: float, pitch: float, z: float) -> float:
    """The roof's underside height over the outer planes' top edge at z
    across the ridge (|z| <= span / 2): on a gable, its outline."""
    return (span / 2.0 - abs(z)) * math.tan(math.radians(pitch))


# -- kit_window_tracery_outer_<w>x<h> ------------------------------------------------------------


def tracery_outer(width: float, height: float, depth: float = 1.2, top: float = 5.2, lights: int = 4,
                  glass_at: float = 0.35, margin: float = 1.0, shoulder: float = 1.8, gable: float = 3.4) -> Piece:
    """`kit_window_tracery_outer_<w>x<h>` (castle M5c, 30.5): the outer
    order of a `width` x `height` traceried window (sill at y = 0, the
    courtyard's wall face at z = 0, the owner's outer face `depth` behind
    it). The Planner cuts the hole through the shared mass (layout_lib,
    castle M5c) and closes its rectangle's jambs and sill below the
    courtyard's wall top (`top` above the sill); this piece draws the
    pointed head's soffit through the mass, a gabled screen wall that
    carries the head above `top` (`margin` beyond the opening each side,
    its shoulders `shoulder` over `top`, its apex `gable` over the
    shoulders), a moulded surround and a hood mould on the face, a
    weathered sill, the stone mullions and head tracery of the inner window
    (openings.tracery_window: the same lights) `glass_at` behind the face
    with the glass lights behind them, small pinnacles on the shoulders
    and a finial on the apex. At most grid.TRACERY_OUTER_TRIS triangles;
    nothing proud below 2.4 m (it stands at the window's sill)."""
    p = Piece("")
    f = p.faces
    lod: list = []
    spring = height - G.ARCH_RISE * width
    half = width / 2.0
    sw = half + margin  # the screen's half width
    ys, ya = top + shoulder, top + shoulder + gable  # shoulders, apex
    if top >= height or top <= 0:
        raise ValueError("kit_window_tracery_outer: the wall top must cross the window")
    arc = geom.pointed_arc(width, spring, openings.SEG)
    # The head's soffit through the mass (both orders: the face to the owner's outer face).
    for faces in (f, lod):
        openings._soffit(faces, width, spring, -depth, 0.0)
    # Above the springing, where the courtyard's wall top cuts the rectangle (the Planner's
    # hole is square there), the jamb runs on up to the arc: the screen's opening outline.
    clip = [(x, y) for x, y in arc if y >= top - 1e-9]
    if top > spring:
        # The arc's points at the wall top (left and right).
        cx, r, _ = geom.arch_geometry(width, spring)
        xt = -cx + math.sqrt(max(0.0, r * r - (top - spring) ** 2))
        clip = [(-xt, top)] + [(x, y) for x, y in arc if y > top + 1e-9] + [(xt, top)]
    else:
        clip = [(-half, top), (-half, spring)] + arc[1:-1] + [(half, spring), (half, top)]
    apex_i = len(clip) // 2
    left, right = clip[: apex_i + 1], clip[apex_i:]
    # The screen's front: two halves, each an outline round its side of the opening.
    ap = clip[apex_i]
    lpoly = [(0.0, ya), (-sw, ys), (-sw, top)] + left[:-1] + [ap]
    rpoly = [(0.0, ya), ap] + right[1:] + [(sw, top), (sw, ys)]
    for poly in (lpoly, rpoly):
        pts = [(x, y, 0.0) for x, y in poly]
        for faces in (f, lod):
            add_face(faces, pts, WALL, None, (0.0, 0.0, 1.0))
    # The screen's sides and its coped gable.
    for faces in (f, lod):
        for sx in (-1.0, 1.0):
            _quad(faces, (sx * sw, top, -depth), (sx * sw, top, 0.0), (sx * sw, ys, 0.0), (sx * sw, ys, -depth),
                  normal=(sx, 0.0, 0.0))
    cope_t, cope_o = 0.22, 0.1
    for sx in (-1.0, 1.0):
        a, b = (sx * sw, ys), (0.0, ya)
        up = (0.0, cope_t)
        n = (sx * (ya - ys), sw, 0.0)
        _quad(f, (a[0] + sx * cope_o, a[1], cope_o), (b[0], b[1], cope_o), (b[0], b[1] + up[1], cope_o),
              (a[0] + sx * cope_o, a[1] + up[1], cope_o), normal=(0.0, 0.0, 1.0))
        _quad(f, (a[0] + sx * cope_o, a[1] + up[1], cope_o), (b[0], b[1] + up[1], cope_o), (b[0], b[1] + up[1], -depth),
              (a[0] + sx * cope_o, a[1] + up[1], -depth), normal=n)
        _quad(f, (a[0] + sx * cope_o, a[1], -depth), (a[0] + sx * cope_o, a[1], cope_o),
              (a[0] + sx * cope_o, a[1] + up[1], cope_o), (a[0] + sx * cope_o, a[1] + up[1], -depth), normal=(sx, 0.0, 0.0))
        _quad(f, (a[0] + sx * cope_o, a[1], cope_o), (a[0] + sx * cope_o, a[1], -depth), (b[0], b[1], -depth),
              (b[0], b[1], cope_o), normal=(sx * -(ya - ys), -sw, 0.0))
        _quad(lod, (a[0], a[1], 0.0), (b[0], b[1], 0.0), (b[0], b[1], -depth), (a[0], a[1], -depth), normal=n)
    # The moulded surround (up the jambs from the sill and round the head) and a hood mould.
    openings._surround(f, width, spring, 0.0, 0.3, 0.15, "window_frame", 0.3)
    inner = geom.pointed_arc(width, spring, openings.SEG, grow=0.3)
    outer = geom.pointed_arc(width, spring, openings.SEG, grow=0.46)
    geom.polyline_band(f, inner, outer, 0.0, 0.24, trim("string"), 0.3)
    # Label stops: the hood mould's ends at the springing turn out level.
    for sx in (-1.0, 1.0):
        x0, x1 = sx * (half + 0.3), sx * (half + 0.75)
        a, b = sorted((x0, x1))
        geom.box(f, (a, spring - 0.16, 0.0), (b, spring, 0.24), trim("string"), skip=("-z",),
                 uv_fn=lambda side, pts: [(pt[0] if side in ("+z", "-z", "+y", "-y") else pt[2], (pt[1] - spring + 0.16) / 0.16 * 0.3)
                                          for pt in pts])
    # The weathered sill.
    sl = half + 0.42
    _quad(f, (-sl, -0.04, 0.34), (sl, -0.04, 0.34), (sl, 0.0, 0.0), (-sl, 0.0, 0.0), normal=(0.0, 1.0, 0.1))
    _quad(f, (-sl, -0.28, 0.34), (sl, -0.28, 0.34), (sl, -0.04, 0.34), (-sl, -0.04, 0.34), normal=(0.0, 0.0, 1.0))
    _quad(f, (-sl, -0.28, 0.0), (sl, -0.28, 0.0), (sl, -0.28, 0.34), (-sl, -0.28, 0.34), normal=(0.0, -1.0, 0.0))
    for sx in (-1.0, 1.0):
        add_face(f, [(sx * sl, -0.28, 0.0), (sx * sl, -0.28, 0.34), (sx * sl, -0.04, 0.34), (sx * sl, 0.0, 0.0)], WALL,
                 None, (sx, 0.0, 0.0))
    # Glass lights behind the tracery (the inner window's tiles), facing out.
    zg = -glass_at
    zf = zg + 0.1
    mull = 0.22
    light_w = (width - (lights - 1) * mull) / lights
    for faces in (f, lod):
        for i in range(lights):
            xa = -half + i * (light_w + mull)
            rect = [(xa, 0.0), (xa + light_w, 0.0), (xa + light_w, spring), (xa, spring)]
            add_face(faces, [(x, y, zg) for x, y in rect], GLASS_TALL, geom.bbox_uv(rect), (0.0, 0.0, 1.0))
        head = [(-half, spring), (half, spring)] + list(reversed(arc))[1:-1]
        add_face(faces, [(x, y, zg) for x, y in head], GLASS_HEAD, geom.bbox_uv(head), (0.0, 0.0, 1.0))
    # Mullions and head tracery in stone in front of the glass (as the inner window's).
    for i in range(1, lights):
        xm = -half + i * (light_w + mull) - mull / 2
        geom.box(f, (xm - mull / 2, 0.0, zg), (xm + mull / 2, spring, zf), trim("window_frame"), skip=("-z", "-y", "+y"),
                 uv_fn=lambda side, pts, xm=xm: [(pt[1], (pt[0] - xm + mull / 2) / mull * 0.3) for pt in pts])
    pair_w = (width - mull) / 2.0
    for sx in (-1.0, 1.0):
        cx = sx * (mull / 2 + pair_w / 2)
        inner = [(x + cx, y) for x, y in geom.pointed_arc(pair_w, spring, 10)]
        outer = [(x + cx, y) for x, y in geom.pointed_arc(pair_w, spring, 10, grow=0.1)]
        openings._band(f, inner, outer, zg, 0.1, clip_half=half)
        for j in (-1.0, 1.0):
            lx = cx + j * (mull / 2 + light_w / 2)
            inner = [(x + lx, y) for x, y in geom.pointed_arc(light_w, spring, 6)]
            outer = [(x + lx, y) for x, y in geom.pointed_arc(light_w, spring, 6, grow=0.1)]
            openings._band(f, inner, outer, zg, 0.08, clip_half=half)
    pair_apex = spring + G.ARCH_RISE * pair_w
    tip = G.arch_height(width, height, 0.0)
    r = max(0.3, min((tip - pair_apex) * 0.42, pair_w * 0.3))
    cy = (tip + pair_apex) / 2.0 + 0.05
    n = 16
    inner = [(r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]
    outer = [((r + 0.14) * math.cos(2 * math.pi * k / n), cy + (r + 0.14) * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]
    openings._band(f, inner, outer, zg, 0.1)
    # Pinnacles on the shoulders and a finial on the apex.
    small = pinnacle(3.0, 0.5, crockets=3)
    for sx in (-1.0, 1.0):
        f.extend(_shift(small.faces, sx * (sw - 0.25), ys, -0.4))
        lod.extend(_shift(small.lod1, sx * (sw - 0.25), ys, -0.4))
    fin = pinnacle(3.0, 0.45, crockets=3)
    f.extend(_shift(fin.faces, 0.0, ya - 0.3, -0.4))
    lod.extend(_shift(fin.lod1, 0.0, ya - 0.3, -0.4))
    p.lod1 = lod
    p.meta.update(spring=spring, lights=lights, glass=zg, screen=(-sw, sw, top, ya + 2.7), depth=depth)
    return p


# -- kit_window_blind_<w>x<h> (castle M5c review) -----------------------------------------------


def blind_window(width: float, height: float, proud: float = 0.22, glass_off: float = 0.06, n: int = 8) -> Piece:
    """`kit_window_blind_<w>x<h>` (castle M5c review, castle-kit.md): a
    lancet in relief on an outer face whose wall cannot be cut (the west
    range's 72 m stack: portraits line every storey inside), so the stack
    reads as a lit, windowed tower from the courtyard instead of a blank
    block. A moulded surround `proud` off the face (its inner edges the
    reveal), the glass `glass_off` off the face inside it (the single
    lancet's tile, as the castle's other windows), a weathered
    sill and a hood mould with level label stops. The sill at y = 0, the
    wall face at z = 0, +z out. LOD1 (grid.FACADE_LOD): the glass and the
    surround's front. At most grid.BLIND_WINDOW_TRIS."""
    p = Piece("")
    f = p.faces
    lod: list = []
    half = width / 2.0
    spring = height - G.ARCH_RISE * width
    tile = "castle_glass|fit:light_lancet"
    for faces, seg in ((f, n), (lod, 3)):
        outline = geom.lancet_outline(width, height, seg)
        add_face(faces, [(x, y, glass_off) for x, y in outline], tile, geom.bbox_uv(outline), (0.0, 0.0, 1.0))
    band = 0.26
    for faces, seg, edges in ((f, n, True), (lod, 3, False)):
        inner = [(-half, 0.0)] + geom.pointed_arc(width, spring, seg) + [(half, 0.0)]
        outer = [(-half - band, 0.0)] + geom.pointed_arc(width, spring, seg, grow=band) + [(half + band, 0.0)]
        geom.polyline_band(faces, inner, outer, 0.0, proud, trim("window_frame"), 0.3, edges=edges, caps=(False, False))
    # The hood mould round the head, its label stops turned out level at the springing.
    inner = geom.pointed_arc(width, spring, n, grow=band + 0.06)
    outer = geom.pointed_arc(width, spring, n, grow=band + 0.22)
    geom.polyline_band(f, inner, outer, 0.0, proud * 0.8, trim("string"), 0.3)
    for sx in (-1.0, 1.0):
        a, b = sorted((sx * (half + band + 0.06), sx * (half + band + 0.42)))
        geom.box(f, (a, spring - 0.16, 0.0), (b, spring, proud * 0.8), trim("string"), skip=("-z",),
                 uv_fn=lambda side, pts: [(pt[0] if side in ("+z", "-z", "+y", "-y") else pt[2],
                                           (pt[1] - spring + 0.16) / 0.16 * 0.3) for pt in pts])
    # The weathered sill.
    sl, sd = half + band + 0.12, proud + 0.12
    _quad(f, (-sl, -0.04, sd), (sl, -0.04, sd), (sl, 0.0, 0.0), (-sl, 0.0, 0.0), normal=(0.0, 1.0, 0.1))
    _quad(f, (-sl, -0.26, sd), (sl, -0.26, sd), (sl, -0.04, sd), (-sl, -0.04, sd), normal=(0.0, 0.0, 1.0))
    _quad(f, (-sl, -0.26, 0.0), (sl, -0.26, 0.0), (sl, -0.26, sd), (-sl, -0.26, sd), normal=(0.0, -1.0, 0.0))
    for sx in (-1.0, 1.0):
        add_face(f, [(sx * sl, -0.26, 0.0), (sx * sl, -0.26, sd), (sx * sl, -0.04, sd), (sx * sl, 0.0, 0.0)], WALL,
                 None, (sx, 0.0, 0.0))
    _quad(lod, (-sl, -0.26, sd), (sl, -0.26, sd), (sl, 0.0, sd), (-sl, 0.0, sd), normal=(0.0, 0.0, 1.0))
    p.lod1 = lod
    p.meta.update(spring=spring, proud=proud, glass=glass_off, hood=height + band + 0.22)
    return p
