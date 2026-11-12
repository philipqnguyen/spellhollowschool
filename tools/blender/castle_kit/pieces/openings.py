"""Openings: door arches, lancet windows, niches, blind doors, oak door
leaves and thresholds. Frame as in geom.py: x = 0 on
the opening's centre line, y = 0 at the door floor or the sill, z = 0 on
the wall face (the room at z > 0, the wall mass behind).

The Planner (layout_lib) leaves or cuts a rectangular hole: `width` x the
opening's height for windows, niches and blind doors (their castle_kit.json
size), the door's contact width x its ceiling for arches. These pieces
close the pointed head (spandrels in the wall plane, `castle_wall` with
world UVs), line the reveal and add the surround that the outline pass
catches."""

from __future__ import annotations

import math

from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece, add_face

WALL = "castle_wall|world"
FLOOR = "castle_floor|world"
SEG = 16  # arc segments per side of a pointed head


def trim(strip: str) -> str:
    return f"castle_trim|trim:{strip}"


def fit(tile: str) -> str:
    return f"castle_trim|fit:{tile}"


def _spandrels(faces, width: float, top: float, spring: float, z: float, facing: float, n: int = SEG) -> None:
    """The wall between a rectangular hole (|x| <= width / 2, up to `top`)
    and the pointed head springing at `spring`, on the plane z."""
    arc = geom.pointed_arc(width, spring, n)
    half = width / 2.0
    left, right = arc[: n + 1], arc[n:]
    geom.fan(faces, (-half, top, z), [(x, y, z) for x, y in left], WALL, (0.0, 0.0, facing))
    geom.fan(faces, (half, top, z), [(x, y, z) for x, y in right], WALL, (0.0, 0.0, facing))


def _soffit(faces, width: float, spring: float, z0: float, z1: float, tag: str = WALL, n: int = SEG) -> None:
    """The underside of a pointed head between the planes z0 and z1, facing
    into the opening."""
    arc = geom.pointed_arc(width, spring, n)
    cx = 0.0
    for (xa, ya), (xb, yb) in zip(arc, arc[1:]):
        pts = [(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z1)]
        mx, my = (xa + xb) / 2, (ya + yb) / 2
        # Toward the opening: down and toward the centre line.
        ex, ey = xb - xa, yb - ya
        nrm = (ey, -ex, 0.0)
        if nrm[0] * (cx - mx) + nrm[1] * (spring - my) < 0:
            nrm = (-ey, ex, 0.0)
        add_face(faces, pts, tag, None, nrm)


def _jambs(faces, width: float, y0: float, y1: float, z0: float, z1: float, tag: str = WALL) -> None:
    half = width / 2.0
    for sx in (-1.0, 1.0):
        x = sx * half
        pts = [(x, y0, z0), (x, y0, z1), (x, y1, z1), (x, y1, z0)]
        add_face(faces, pts, tag, None, (-sx, 0.0, 0.0))


def _surround(faces, width: float, spring: float, y0: float | None, band: float, proud: float, strip: str,
              metres: float, n: int = SEG, caps=(True, True)) -> None:
    """A moulded band round a pointed opening on the wall face: up the
    jambs from y0 (None: from the springing) and round the head."""
    half = width / 2.0
    inner = geom.pointed_arc(width, spring, n)
    outer = geom.pointed_arc(width, spring, n, grow=band)
    if y0 is not None:
        inner = [(-half, y0)] + inner + [(half, y0)]
        outer = [(-half - band, y0)] + outer + [(half + band, y0)]
    geom.polyline_band(faces, inner, outer, 0.0, proud, trim(strip), metres, caps=caps)


def _lancet_glass(faces, width: float, height: float, y0: float, z: float, tile: str, n: int = SEG) -> None:
    """A glass light (both faces) filling a pointed outline at depth z."""
    outline = [(x, y + y0) for x, y in geom.lancet_outline(width, height, n)]
    uv = geom.bbox_uv(outline)
    pts = [(x, y, z) for x, y in outline]
    add_face(faces, pts, f"castle_glass|fit:{tile}", uv, (0.0, 0.0, 1.0))
    back = list(reversed(pts))
    add_face(faces, back, f"castle_glass|fit:{tile}", [(1.0 - a, b) for a, b in reversed(uv)], (0.0, 0.0, -1.0))


# -- door arches ---------------------------------------------------------------------


def arch(width: float, apex: float, reveal: float = G.ARCH_REVEAL, depth: float = G.WALL_THICKNESS,
         quoins: bool = True, rim: bool = True) -> Piece:
    """`kit_stone_arch_<w>x<h>`: the pointed head of a door opening `width`
    wide with its apex at `apex` (the door's ceiling): spandrels on the
    wall face and at `reveal` metres into the passage, the soffit between,
    a voussoir rim round the head with a keystone, and quoins down the
    jambs. The jambs below the springing are the door's own walls."""
    p = Piece("")
    f = p.faces
    spring = G.arch_springing(width, apex)
    half = width / 2.0
    _spandrels(f, width, apex, spring, 0.0, 1.0)
    _spandrels(f, width, apex, spring, -reveal, -1.0)
    _soffit(f, width, spring, -reveal, 0.0)
    band, proud = 0.3, 0.12
    label, label_proud = 0.12, 0.18
    if rim:
        _surround(f, width, spring, None, band, proud, "arch_rim", 0.3)
        # A hood mould (label) round the rim, and a keystone at the apex.
        inner = geom.pointed_arc(width, spring, SEG, grow=band)
        outer = geom.pointed_arc(width, spring, SEG, grow=band + label)
        geom.polyline_band(f, inner, outer, 0.0, label_proud, trim("string"), 0.3)
        geom.box(f, (-0.22, apex, 0.0), (0.22, apex + band, proud + 0.1), fit("keystone"), skip=("-z",),
                 uv_fn=lambda side, pts: [(0, 0), (1, 0), (1, 1), (0, 1)])
    if quoins:
        for side in (-1, 1):
            q = _quoins(G.PLINTH.height, spring, side)
            for face in q:
                face.pts = [(x + side * half, y, z) for x, y, z in face.pts]
                f.append(face)
    cx, r, _ = geom.arch_geometry(width, spring)
    outer_apex = (spring + ((r + band + label) ** 2 - cx * cx) ** 0.5) if rim else apex
    p.meta.update(spring=spring, outer_apex=outer_apex, outer_half=half + (0.8 if quoins else band + label),
                  reveal=reveal)
    return p


def _quoins(y0: float, y1: float, side: int):
    from castle_kit.pieces import mouldings

    return mouldings.quoin(y0, y1, corner=False, side=side).faces


def door_oak(width: float, apex: float, open: bool = False, depth: float = G.WALL_THICKNESS, inset: float = 0.3,
             mode: str | None = None, reveal: float | None = None, gap: float = G.ARCH_SURROUND) -> Piece:
    """`kit_door_oak_<w>x<h>[_open]`: a pair of oak leaves, each half the
    span, with iron straps. Closed: filling the pointed outline `inset` into
    the passage (z = -inset). Open (castle kit M2, the open-leaf rule,
    docs/art/castle-kit.md): swung fully open and never across the
    passage, square headed at the springing under a fixed oak tympanum
    `inset` into the reveal; `mode` "reveal" lays them flat against the
    reveal's jambs (it needs a reveal of a leaf plus 0.1 m), "face" flat
    against the wall face beside the opening, `gap` (the arch's quoins)
    beyond each jamb. Either way nothing stands more than grid.LEAF_PROUD
    off the face it lies on. `reveal`: how deep this cell lines the passage
    (default `depth`); `mode` None picks reveal when it is deep enough."""
    p = Piece("")
    f = p.faces
    spring = G.arch_springing(width, apex)
    half = width / 2.0
    leaf = G.leaf_width(width)
    thick = 0.08
    z = -inset
    wood = "castle_wood|trim:door_planks"
    if not open:
        arc = geom.pointed_arc(width, spring, SEG)
        left = [(-half, 0.0), (0.0, 0.0), (0.0, apex)] + list(reversed(arc[: SEG + 1]))[1:]
        right = [(0.0, 0.0), (half, 0.0)] + list(reversed(arc[SEG:]))
        for outline, x0 in ((left, -half), (right, 0.0)):
            uv = [(y, (x - x0) / leaf * 1.2) for x, y in outline]
            add_face(f, [(x, y, z) for x, y in outline], wood, uv, (0.0, 0.0, 1.0))
            add_face(f, [(x, y, z - thick) for x, y in reversed(outline)], wood, [(a, b) for a, b in reversed(uv)],
                     (0.0, 0.0, -1.0))
        for y in (0.5, spring * 0.5, spring - 0.4):
            for sx in (-1.0, 1.0):
                x0, x1 = (-half, -0.15) if sx < 0 else (0.15, half)
                geom.box(f, (x0, y, z), (x1, y + 0.1, z + 0.025), trim("iron"), skip=("-z",),
                         uv_fn=lambda side, pts: [(pt[0], (pt[1] - y) * 2.5) for pt in pts])
        p.meta["clear"] = (0.0, 0.0)
        p.meta.update(spring=spring, inset=inset)
        return p
    reveal = depth if reveal is None else reveal
    if mode is None:
        mode = "reveal" if reveal >= leaf + G.LEAF_REVEAL_CLEAR - 1e-9 else "face"
    if mode not in ("reveal", "face"):
        raise ValueError(f"open leaves lie in the reveal or on the face, not {mode!r}")
    if mode == "reveal" and reveal < leaf + G.LEAF_REVEAL_CLEAR - 1e-9:
        raise ValueError(f"a {G.fmt_m(leaf)} m leaf needs a reveal of {G.fmt_m(leaf + G.LEAF_REVEAL_CLEAR)} m")
    inset = min(inset, max(0.05, reveal - thick - 0.02))
    z = -inset
    for sx in (-1.0, 1.0):
        if mode == "face":
            # Flat on the wall face, its hinge edge `gap` beyond the jamb.
            a, b = sorted((sx * (half + gap), sx * (half + gap + leaf)))
            _leaf(f, (a, b), spring, lambda u, y, d: (u, y, d), (0.0, 0.0, 1.0), wood)
        else:
            # Flat against the jamb, from 0.1 m inside the face back into the reveal.
            x_back = sx * half
            a, b = -(G.LEAF_REVEAL_CLEAR + leaf), -G.LEAF_REVEAL_CLEAR
            _leaf(f, (a, b), spring, lambda u, y, d, sx=sx, xb=x_back: (xb - sx * d, y, u), (-sx, 0.0, 0.0), wood)
    # Tympanum: the pointed head above the springing, closed in oak.
    arc = geom.pointed_arc(width, spring, SEG)
    outline = [(-half, spring), (half, spring)] + list(reversed(arc))[1:-1]
    uv = [(y, (x + half) / width * 1.2) for x, y in outline]
    add_face(f, [(x, y, z) for x, y in outline], wood, uv, (0.0, 0.0, 1.0))
    add_face(f, [(x, y, z - thick) for x, y in reversed(outline)], wood, list(reversed(uv)), (0.0, 0.0, -1.0))
    proud = LEAF_OFF + thick + LEAF_FRAME + LEAF_STRAP
    p.meta["clear"] = (proud, proud) if mode == "reveal" else (0.0, 0.0)
    p.meta.update(spring=spring, inset=inset, mode=mode, leaf=leaf, proud=proud)
    return p


LEAF_OFF = 0.02  # an open leaf's back stands this off the face it lies on
LEAF_FRAME = 0.03  # its stiles and rails stand proud of the boards
LEAF_STRAP = 0.025  # and the iron straps of those


def _leaf(f, span, height: float, to3, normal, wood: str) -> None:
    """One open leaf in its own plane: u across (span), y up, d out of the
    face it lies on; `to3(u, y, d)` places it. Boards, a raised frame of
    stiles and rails, two iron straps; its back (against the face) and
    bottom (on the floor) are not drawn."""
    u0, u1 = span
    w = u1 - u0
    d0, d1 = LEAF_OFF, LEAF_OFF + 0.08
    nx = normal

    def face(pts, tag, uv, n):
        add_face(f, [to3(*q) for q in pts], tag, uv, n)

    def side_normal(du, dy, dd):
        """A leaf-local direction (u, y, d) in the world, from to3's differences."""
        o = to3(0.0, 0.0, 0.0)
        q = to3(du, dy, dd)
        return (q[0] - o[0], q[1] - o[1], q[2] - o[2])

    def slab(ua, ub, ya, yb, da, db, tag, uvf):
        for pts, n in ((((ua, ya, db), (ub, ya, db), (ub, yb, db), (ua, yb, db)), side_normal(0, 0, 1)),
                       (((ua, ya, da), (ua, ya, db), (ua, yb, db), (ua, yb, da)), side_normal(-1, 0, 0)),
                       (((ub, ya, db), (ub, ya, da), (ub, yb, da), (ub, yb, db)), side_normal(1, 0, 0)),
                       (((ua, yb, db), (ub, yb, db), (ub, yb, da), (ua, yb, da)), side_normal(0, 1, 0)),
                       (((ua, ya, da), (ub, ya, da), (ub, ya, db), (ua, ya, db)), side_normal(0, -1, 0))):
            if ya <= 1e-9 and n == side_normal(0, -1, 0):
                continue
            face(pts, tag, uvf(pts), n)

    # The boards: front, both edges and the top.
    slab(u0, u1, 0.0, height, d0, d1, wood, lambda pts: [(q[1], (q[0] - u0) / w * 1.2) for q in pts])
    # The frame: two stiles, a bottom, two middle and a top rail.
    frame = "castle_wood|trim:beam"
    st = min(0.22, w * 0.08)
    fa, fb = d1, d1 + LEAF_FRAME
    uvb = lambda pts: [(q[1] + q[0], 0.05 + 0.5 * ((q[2] - fa) / LEAF_FRAME)) for q in pts]  # noqa: E731
    slab(u0, u0 + st, 0.0, height, fa, fb, frame, uvb)
    slab(u1 - st, u1, 0.0, height, fa, fb, frame, uvb)
    for ya, yb in ((0.0, 0.25), (height * 0.36, height * 0.36 + 0.16), (height * 0.68, height * 0.68 + 0.16),
                   (height - 0.22, height)):
        slab(u0 + st, u1 - st, ya, yb, fa, fb, frame, uvb)
    # Iron straps across the leaf, over the frame.
    for y in (0.5, height - 0.7):
        slab(u0 + 0.04, u1 - 0.04, y, y + 0.1, fb, fb + LEAF_STRAP, trim("iron"),
             lambda pts, y=y: [(q[0], (q[1] - y) * 2.5) for q in pts])
    _ = nx


def threshold(width: float, depth: float = G.WALL_THICKNESS, band: float = 0.25) -> Piece:
    """`kit_threshold_<w>`: the floor of a door passage, flush (it replaces
    the door surface's floor): stone sill bands at both faces and flags
    between. z = 0 is the room face, the passage runs to z = -depth."""
    p = Piece("")
    half = width / 2.0
    for za, zb in ((-band, 0.0), (-depth, -depth + band)):
        pts = [(-half, 0.0, za), (half, 0.0, za), (half, 0.0, zb), (-half, 0.0, zb)]
        uv = [(x, (z - za) / band * 0.3) for x, _, z in pts]
        add_face(p.faces, pts, trim("string"), uv, (0.0, 1.0, 0.0), anchor=True)
    pts = [(-half, 0.0, -depth + band), (half, 0.0, -depth + band), (half, 0.0, -band), (-half, 0.0, -band)]
    add_face(p.faces, pts, FLOOR, None, (0.0, 1.0, 0.0))
    return p


# -- wall openings ---------------------------------------------------------------------


def window(width: float, height: float, depth: float = G.EXTERIOR_SHELL, glass_at: float = 0.6, outer: bool = True) -> Piece:
    """`kit_window_lancet_<w>x<h>`: a lancet window through a wall `depth`
    thick: spandrels on both faces, reveals and sill, the glass at
    `glass_at` x depth, a moulded frame on the inside face. Two-light
    windows (grid.OPENING_PIECES) get a mullion and two sub-lancet heads
    of stone tracery in front of the glass, whose head shows the painted
    tracery tile. y = 0 is the sill."""
    p = Piece("")
    f = p.faces
    spec = next((s for s in G.OPENING_PIECES.values() if s.kind == "window" and abs(s.width - width) < 1e-6
                 and abs(s.height - height) < 1e-6), None)
    lights = spec.lights if spec else (2 if width >= 2.6 else 1)
    spring = height - G.ARCH_RISE * width
    half = width / 2.0
    zg = -depth * glass_at
    _spandrels(f, width, height, spring, 0.0, 1.0)
    if outer:  # (False: the Planner leaves the outer shell whole there, castle kit M2)
        _spandrels(f, width, height, spring, -depth, -1.0)
    _soffit(f, width, spring, -depth, 0.0)
    _jambs(f, width, 0.0, spring, -depth, 0.0)
    add_face(f, [(-half, 0.0, 0.0), (half, 0.0, 0.0), (half, 0.0, -depth), (-half, 0.0, -depth)], WALL, None,
             (0.0, 1.0, 0.0))
    if lights == 1:
        _lancet_glass(f, width, height, 0.0, zg, "light_lancet")
    else:
        # Glass: the lights below the springing, the tracery tile in the head.
        rect = [(-half, 0.0), (half, 0.0), (half, spring), (-half, spring)]
        head = [(-half, spring), (half, spring)] + list(reversed(geom.pointed_arc(width, spring, SEG)))[1:-1]
        for outline, tile in ((rect, "light_tall"), (head, "head_tracery")):
            uv = geom.bbox_uv(outline)
            add_face(f, [(x, y, zg) for x, y in outline], f"castle_glass|fit:{tile}", uv, (0.0, 0.0, 1.0))
            add_face(f, [(x, y, zg) for x, y in reversed(outline)], f"castle_glass|fit:{tile}",
                     [(1 - a, b) for a, b in reversed(uv)], (0.0, 0.0, -1.0))
        # Stone tracery in front of the glass: a mullion and two sub-lancet heads.
        mull = 0.2
        sub_w = (width - mull) / 2.0
        sub_apex = spring + 0.35 * G.ARCH_RISE * width
        sub_spring = sub_apex - G.ARCH_RISE * sub_w
        zf = zg + 0.08
        geom.box(f, (-mull / 2, 0.0, zg), (mull / 2, sub_spring, zf), trim("window_frame"), skip=("-z", "-y"),
                 uv_fn=lambda side, pts: [(pt[1], (pt[0] + mull / 2) / mull * 0.3) for pt in pts])
        for sx in (-1.0, 1.0):
            cx = sx * (mull / 2 + sub_w / 2)
            inner = [(x + cx, y) for x, y in geom.pointed_arc(sub_w, sub_spring, 8)]
            outer = [(x + cx, y) for x, y in geom.pointed_arc(sub_w, sub_spring, 8, grow=0.1)]
            band: list = []
            geom.polyline_band(band, inner, outer, 0.0, 0.08, trim("window_frame"), 0.3, caps=(False, False))
            for face in band:
                face.pts = [(x, y, z + zg) for x, y, z in face.pts]
            # Keep the band inside the window's own outline.
            f.extend(face for face in band if all(abs(x) <= half + 1e-6 for x, _, _ in face.pts))
    # Frame round the inside face, in two orders: a fillet at the opening's
    # edge and a broader band, up the jambs from the sill and round the head.
    _surround(f, width, spring, 0.0, 0.1, 0.06, "window_frame", 0.3)
    inner = [(-half - 0.1, 0.0)] + geom.pointed_arc(width, spring, SEG, grow=0.1) + [(half + 0.1, 0.0)]
    outer = [(-half - 0.26, 0.0)] + geom.pointed_arc(width, spring, SEG, grow=0.26) + [(half + 0.26, 0.0)]
    geom.polyline_band(f, inner, outer, 0.0, 0.1, trim("window_frame"), 0.3)
    p.meta.update(spring=spring, lights=lights, glass=zg)
    return p


def _band(f, inner, outer, z: float, depth: float = 0.08, caps=(False, False), clip_half: float | None = None) -> None:
    """A stone tracery band (window_frame strip) between two curves at z,
    standing `depth` toward the room; faces outside |x| <= clip_half dropped."""
    band: list = []
    geom.polyline_band(band, inner, outer, 0.0, depth, trim("window_frame"), 0.3, caps=caps)
    for face in band:
        face.pts = [(x, y, zz + z) for x, y, zz in face.pts]
    f.extend(face for face in band if clip_half is None or all(abs(x) <= clip_half + 1e-6 for x, _, _ in face.pts))


def tracery_window(width: float, height: float, depth: float = G.EXTERIOR_SHELL, glass_at: float = 0.6,
                   lights: int = 4, outer: bool = True) -> Piece:
    """`kit_window_tracery_<w>x<h>` (castle kit M2): a great traceried
    window through a wall `depth` thick, like the lancets (spandrels on both
    faces, reveals and sill, a moulded frame on the inside face) with
    `lights` lights between mullions; in the head two sub-arches over pairs
    of lights, each with a lancet head over every light, and an oculus
    above them, in stone in front of the glass (the head shows the painted
    tracery tile); at most 2,500 triangles at 8 x 8. y = 0 is the sill.
    `outer` False leaves out the spandrels of the outer face where the
    Planner does not cut the outer shell (another room within one wall
    mass behind it: the courtyard beyond the front doors)."""
    p = Piece("")
    f = p.faces
    spring = height - G.ARCH_RISE * width
    half = width / 2.0
    zg = -depth * glass_at
    zf = zg + 0.1
    _spandrels(f, width, height, spring, 0.0, 1.0)
    if outer:
        _spandrels(f, width, height, spring, -depth, -1.0)
    _soffit(f, width, spring, -depth, 0.0)
    _jambs(f, width, 0.0, spring, -depth, 0.0)
    add_face(f, [(-half, 0.0, 0.0), (half, 0.0, 0.0), (half, 0.0, -depth), (-half, 0.0, -depth)], WALL, None,
             (0.0, 1.0, 0.0))
    mull = 0.22
    light_w = (width - (lights - 1) * mull) / lights
    # Glass: one light per bay below the springing, the tracery tile in the head.
    for i in range(lights):
        x0 = -half + i * (light_w + mull)
        rect = [(x0, 0.0), (x0 + light_w, 0.0), (x0 + light_w, spring), (x0, spring)]
        _glass(f, rect, zg, "light_tall")
    head = [(-half, spring), (half, spring)] + list(reversed(geom.pointed_arc(width, spring, SEG)))[1:-1]
    _glass(f, head, zg, "head_tracery")
    # Mullions up to the springing, a transom bar along it.
    for i in range(1, lights):
        xm = -half + i * (light_w + mull) - mull / 2
        geom.box(f, (xm - mull / 2, 0.0, zg), (xm + mull / 2, spring, zf), trim("window_frame"), skip=("-z", "-y", "+y"),
                 uv_fn=lambda side, pts, xm=xm: [(pt[1], (pt[0] - xm + mull / 2) / mull * 0.3) for pt in pts])
    # Head tracery: two sub-arches over pairs of lights, a lancet head over
    # each light, an oculus above.
    pair_w = (width - mull) / 2.0
    for sx in (-1.0, 1.0):
        cx = sx * (mull / 2 + pair_w / 2)
        inner = [(x + cx, y) for x, y in geom.pointed_arc(pair_w, spring, 10)]
        # (0.1 wide, so the two sub-arches' bands do not meet over the middle mullion.)
        outer = [(x + cx, y) for x, y in geom.pointed_arc(pair_w, spring, 10, grow=0.1)]
        _band(f, inner, outer, zg, 0.1, clip_half=half)
        for j in (-1.0, 1.0):
            lx = cx + j * (mull / 2 + light_w / 2)
            inner = [(x + lx, y) for x, y in geom.pointed_arc(light_w, spring, 6)]
            outer = [(x + lx, y) for x, y in geom.pointed_arc(light_w, spring, 6, grow=0.1)]
            _band(f, inner, outer, zg, 0.08, clip_half=half)
    pair_apex = spring + G.ARCH_RISE * pair_w
    top = G.arch_height(width, height, 0.0)
    r = max(0.3, min((top - pair_apex) * 0.42, pair_w * 0.3))
    cy = (top + pair_apex) / 2.0 + 0.05
    n = 16
    inner = [(r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]
    outer = [((r + 0.14) * math.cos(2 * math.pi * k / n), cy + (r + 0.14) * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]
    _band(f, inner, outer, zg, 0.1)
    # Frame round the inside face, as the lancets.
    _surround(f, width, spring, 0.0, 0.12, 0.07, "window_frame", 0.3)
    inner = [(-half - 0.12, 0.0)] + geom.pointed_arc(width, spring, SEG, grow=0.12) + [(half + 0.12, 0.0)]
    outer = [(-half - 0.32, 0.0)] + geom.pointed_arc(width, spring, SEG, grow=0.32) + [(half + 0.32, 0.0)]
    geom.polyline_band(f, inner, outer, 0.0, 0.12, trim("window_frame"), 0.3)
    p.meta.update(spring=spring, lights=lights, glass=zg, oculus=(cy, r))
    return p


def _glass(f, outline, zg: float, tile: str) -> None:
    uv = geom.bbox_uv(outline)
    add_face(f, [(x, y, zg) for x, y in outline], f"castle_glass|fit:{tile}", uv, (0.0, 0.0, 1.0))
    add_face(f, [(x, y, zg) for x, y in reversed(outline)], f"castle_glass|fit:{tile}", [(1 - a, b) for a, b in reversed(uv)],
             (0.0, 0.0, -1.0))


def niche(width: float, height: float, depth: float = 0.7) -> Piece:
    """`kit_niche_<w>x<h>`: a pointed recess `depth` deep with a moulded
    surround and a projecting sill; y = 0 is the sill."""
    p = Piece("")
    f = p.faces
    spring = height - G.ARCH_RISE * width
    half = width / 2.0
    _spandrels(f, width, height, spring, 0.0, 1.0)
    _soffit(f, width, spring, -depth, 0.0)
    _jambs(f, width, 0.0, spring, -depth, 0.0)
    back = [(x, y, -depth) for x, y in geom.lancet_outline(width, height, SEG)]
    add_face(f, back, WALL, None, (0.0, 0.0, 1.0))
    add_face(f, [(-half, 0.0, 0.0), (half, 0.0, 0.0), (half, 0.0, -depth), (-half, 0.0, -depth)], WALL, None, (0.0, 1.0, 0.0))
    # A sill slab projecting 0.1 m under the opening.
    s0, s1 = -0.1, 0.0
    geom.box(f, (-half - 0.12, s0, 0.0), (half + 0.12, s1, 0.14), trim("string"), skip=("-z",),
             uv_fn=lambda side, pts: [(pt[0], (pt[1] - s0) / 0.1 * 0.3) for pt in pts])
    # 0.11 proud: never coplanar with an arch's quoins (0.1) beside it (castle kit M2).
    _surround(f, width, spring, 0.0, 0.22, 0.11, "arch_rim", 0.3, caps=(False, False))  # (its feet stand on the sill)
    p.meta.update(spring=spring, depth=depth)
    return p


def blind_door(width: float, height: float, depth: float = 0.3) -> Piece:
    """`kit_door_blind_<w>x<h>`: a closed oak door in a pointed recess
    `depth` deep (a classroom door that does not open), with iron straps,
    ring handles and a voussoir surround; y = 0 is the floor."""
    p = Piece("")
    f = p.faces
    spring = height - G.ARCH_RISE * width
    half = width / 2.0
    _spandrels(f, width, height, spring, 0.0, 1.0)
    _soffit(f, width, spring, -depth, 0.0)
    _jambs(f, width, 0.0, spring, -depth, 0.0)
    leaves = door_oak(width, height, open=False, inset=depth)
    # Only the leaves' room-facing side shows in a blind recess.
    f.extend(face for face in leaves.faces if face.normal()[2] > -0.5)
    # Ring handles either side of the meeting stile.
    for sx in (-1.0, 1.0):
        x = sx * 0.28
        geom.box(f, (x - 0.07, 1.0, -depth + 0.025), (x + 0.07, 1.14, -depth + 0.06), trim("iron"), skip=("-z",),
                 uv_fn=lambda side, pts: [(pt[0], (pt[1] - 1.0) * 1.8) for pt in pts])
    # The recess's floor, level with the room's.
    add_face(f, [(-half, 0.0, 0.0), (half, 0.0, 0.0), (half, 0.0, -depth), (-half, 0.0, -depth)], FLOOR, None,
             (0.0, 1.0, 0.0))
    _surround(f, width, spring, 0.0, 0.3, 0.12, "arch_rim", 0.3)
    p.meta.update(spring=spring, depth=depth)
    return p
