"""The Great Hall's roof and panelling (castle kit M3,
docs/art/castle-kit.md): the arch-braced oak truss, the ridge beam the
two sky pitches meet under, the oak wainscot and its crest panels.

Frames as geom.py. The truss: origin on the hall's centre line at the
corbels' height, local x across the span (the side walls' faces at
x = +-span / 2), y up, z along the hall (its thickness; with `engaged` the
truss stands flat against a gable from z = 0 into the room). The wainscot:
a sweep along local x from 0 to `length`, y = 0 on the floor (rising by
`rise` over its length beside a stair), z out of the wall face.

Colours: oak and iron come from the wood and trim sheets (castle_wood
`beam` / `planks` / `wainscot`, castle_trim `iron`), the corbels from the
trim sheet's corbel tiles: no colour literals."""

from __future__ import annotations

import math

from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece, add_face

BEAM = "castle_wood|trim:beam"
PLANKS = "castle_wood|trim:planks"
PANEL = "castle_wood|trim:wainscot"
IRON = "castle_trim|trim:iron"
CREST = "castle_trim|fit:crest_blank"

TRUSS_SKY_GAP = 0.07  # the rafters' top edge stays this far (vertically) under the sky panels
TRUSS_POST = (0.05, 0.55)  # the wall post: this far from the wall face to this far (on the corbel)
TRUSS_RAFTER = 0.5  # rafter depth, square to its slope
TRUSS_BRACE = 0.45  # arch brace depth
TRUSS_COLLAR = 0.5  # collar depth; the arch's apex is its underside
TRUSS_COLLAR_DROP = 4.5  # the collar's top this far under the ridge
# Half thicknesses along the hall (no two members share a face plane).
T_POST, T_RAFTER, T_BRACE, T_COLLAR, T_KING, T_SPANDREL = 0.2, 0.17, 0.14, 0.12, 0.11, 0.05


def _uv_member(pts, a, d, w: float, metres: float = 0.6):
    """Strip UVs of a member's face: s along the member's axis `d` from its
    start `a`, p across it (0..w scaled into the strip's `metres`)."""
    n = (-d[1], d[0])
    out = []
    for x, y, z in pts:
        s = (x - a[0]) * d[0] + (y - a[1]) * d[1]
        q = (x - a[0]) * n[0] + (y - a[1]) * n[1]
        out.append((s, min(max(q / w, 0.0), 1.0) * min(metres, w)))
    return out


def _prism(f, outline, z0: float, z1: float, tag: str, uv2d, front=True, back=True, edges=True, skip_edges=()):
    """A flat member: the 2D outline [(x, y), ...] (counter-clockwise seen
    from +z) between z0 and z1. `uv2d(x, y)` gives the faces' strip UVs;
    edge faces map s along the edge and p through the thickness."""
    if front:
        pts = [(x, y, z1) for x, y in outline]
        add_face(f, pts, tag, [uv2d(x, y) for x, y in outline], (0.0, 0.0, 1.0))
    if back:
        pts = [(x, y, z0) for x, y in reversed(outline)]
        add_face(f, pts, tag, [uv2d(x, y) for x, y in reversed(outline)], (0.0, 0.0, -1.0))
    if not edges:
        return
    n = len(outline)
    for i in range(n):
        if i in skip_edges:
            continue
        (xa, ya), (xb, yb) = outline[i], outline[(i + 1) % n]
        ln = math.hypot(xb - xa, yb - ya)
        if ln < 1e-6:
            continue
        nrm = ((yb - ya) / ln, -(xb - xa) / ln, 0.0)  # outward for a CCW outline
        s0 = xa + ya
        pts = [(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z1)]
        t = z1 - z0
        add_face(f, pts, tag, [(s0, 0.0), (s0 + ln, 0.0), (s0 + ln, min(t, 0.6)), (s0, min(t, 0.6))], nrm)


def truss_geometry(span: float, plate: float, ridge: float):
    """The truss's key numbers (local frame): half span, the rafters' top
    line, the arch's intrados (two-centred, between the posts' inner faces,
    apex under the collar) and the collar's heights."""
    half = span / 2.0
    k = (ridge - plate) / half  # rafter slope
    gap = TRUSS_SKY_GAP
    dv = TRUSS_RAFTER * math.sqrt(1.0 + k * k)  # the rafter's vertical depth

    def top(x):
        return plate - gap + (half - abs(x)) * k

    def bottom(x):
        return top(x) - dv

    collar_top = ridge - TRUSS_COLLAR_DROP
    apex = collar_top - TRUSS_COLLAR
    inner = half - TRUSS_POST[1]
    spring = apex - G.ARCH_RISE * 2.0 * inner
    return {"half": half, "k": k, "dv": dv, "top": top, "bottom": bottom, "collar": (apex, collar_top),
            "inner": inner, "spring": spring}


def _x_at(fn, y: float, lo: float, hi: float) -> float:
    """x in lo..hi where the decreasing (in |x|) line fn(x) = y (bisection)."""
    a, b = lo, hi
    for _ in range(60):
        m = (a + b) / 2
        if (fn(m) - y) * (fn(a) - y) <= 0:
            b = m
        else:
            a = m
    return (a + b) / 2


def truss_archbraced(span: float, plate: float = 5.5, ridge: float = 16.5, engaged: float = 0.0) -> Piece:
    """`kit_truss_archbraced_<span>`: an oak arch-braced collar truss.
    Wall posts stand on the corbels (y = 0) up into the principal rafters,
    whose top edge runs TRUSS_SKY_GAP under the sky panels from the wall
    plate (`plate` above the origin, on the wall faces) to the ridge
    (`ridge`, over the middle); a collar ties the rafters, a king post rises
    from it to the apex; two arch braces spring from the posts' feet to a
    pointed arch under the collar, with cusps on their intrados; panelled
    spandrels fill the angles between brace, post and rafter; iron straps
    bind the joints. `engaged` 1: half a truss's depth flat against a gable
    (from z = 0 into the room, no back faces), its posts on two corbels of
    their own. At most 3,000 triangles."""
    p = Piece("")
    f = p.faces
    g = truss_geometry(span, plate, ridge)
    half, top, bottom, inner = g["half"], g["top"], g["bottom"], g["inner"]
    apex, collar_top = g["collar"]
    eng = engaged >= 0.5

    def zr(t):
        return (0.0, 2.0 * t) if eng else (-t, t)

    back = not eng
    for sx in (-1.0, 1.0):
        # The wall post, from the corbel into the rafter.
        x_out, x_in = sx * (half - TRUSS_POST[0]), sx * (half - TRUSS_POST[1])
        post_top = plate - TRUSS_SKY_GAP - 0.1
        a, b = sorted((x_in, x_out))
        outline = [(a, 0.0), (b, 0.0), (b, post_top), (a, post_top)]
        z0, z1 = zr(T_POST)
        # (Its foot stands on the corbel, its head is inside the rafter: neither drawn.)
        _prism(f, outline, z0, z1, BEAM, lambda x, y, a=a, b=b: (y, (x - a) / (b - a) * 0.5), back=back,
               skip_edges={0, 2})
        # The principal rafter, wall to apex.
        xa, xb = sx * half, 0.0
        pts = [(xa, top(xa)), (xb, top(xb)), (xb, bottom(xb)), (xa, bottom(xa))]
        if _area(pts) < 0:
            pts = list(reversed(pts))
        ln = math.hypot(xb - xa, top(xb) - top(xa))
        d = ((xb - xa) / ln, (top(xb) - top(xa)) / ln)
        a0 = (xa, bottom(xa))
        z0, z1 = zr(T_RAFTER)
        # No end faces: one is against the wall face, the other meets its twin at the apex.
        ends = {i for i in range(4) if abs(pts[i][0] - pts[(i + 1) % 4][0]) < 1e-9}
        _prism(f, pts, z0, z1, BEAM, lambda x, y, d=d, a0=a0: _uv_member([(x, y, 0.0)], a0, d, g["dv"])[0], back=back,
               skip_edges=ends)
    # The collar, its ends buried in the rafters.
    xc = _x_at(bottom, apex, 0.0, half) - 0.05
    z0, z1 = zr(T_COLLAR)
    _prism(f, [(-xc, apex), (xc, apex), (xc, collar_top), (-xc, collar_top)], z0, z1, BEAM,
           lambda x, y: (x, (y - apex) / TRUSS_COLLAR * 0.5), back=back, skip_edges={1, 3})
    # The king post from the collar to the apex.
    kp = 0.15
    z0, z1 = zr(T_KING)
    _prism(f, [(-kp, collar_top), (kp, collar_top), (kp, bottom(0.0) + 0.1), (-kp, bottom(0.0) + 0.1)], z0, z1, BEAM,
           lambda x, y: (y, (x + kp) / (2 * kp) * 0.3), back=back, skip_edges={0, 2})
    # The arch braces: a pointed arch from the posts' feet to the collar.
    arc_in = geom.pointed_arc(2.0 * inner, g["spring"], 12)
    arc_out = geom.pointed_arc(2.0 * inner, g["spring"], 12, grow=TRUSS_BRACE)
    z0, z1 = zr(T_BRACE)
    band: list = []
    geom.polyline_band(band, arc_in, arc_out, z0, z1, BEAM, 0.45, caps=(True, True))
    if back:
        back_band: list = []
        geom.polyline_band(back_band, arc_in, arc_out, z1, z0, BEAM, 0.45, edges=False, caps=(False, False))
        for face in back_band:
            face.pts = list(reversed(face.pts))
            face.uv = list(reversed(face.uv))
        band += back_band
    for face in band:
        face.anchor = False
    f.extend(band)
    # Cusps on the intrados: small pointed foils hanging into the arch.
    _cusps(f, arc_in, z0 + 0.03, z1 - 0.03, back)
    # Panelled spandrels between brace, post and rafter (lower) and between
    # brace, rafter and collar (upper), a thin board set back in the frame.
    lo_pts, up_pts = _spandrels(g, arc_out)
    z0, z1 = zr(T_SPANDREL)
    for outline in lo_pts + up_pts:
        ys = [y for _, y in outline]
        y_lo = min(ys)
        _prism(f, outline, z0, z1, PANEL, lambda x, y, y_lo=y_lo: (x, min(max(y - y_lo + 1.2, 0.0), 3.2)), back=back,
               edges=False)
    # Iron straps: round each post above its foot and round the brace's
    # crown under the collar.
    st = 0.025
    for sx in (-1.0, 1.0):
        a, b = sorted((sx * (half - TRUSS_POST[0]), sx * (half - TRUSS_POST[1])))
        for y in (0.35, 1.1):
            _strap(f, a - st, b + st, y, y + 0.1, T_POST + st, eng)
    _strap(f, -0.55, 0.55, apex - 0.35, apex - 0.25, T_BRACE + st, eng)
    if eng:
        # Two corbels of its own under the posts, on the gable.
        from castle_kit.pieces import mouldings

        for sx in (-1.0, 1.0):
            c = mouldings.corbel(0.9)
            for face in c.faces:
                face.pts = [(x + sx * (half - 0.33), y, z) for x, y, z in face.pts]
                f.append(face)
    p.meta.update(span=span, plate=plate, ridge=ridge, engaged=eng, spring=g["spring"], apex=apex,
                  collider=truss_shell(g, eng))
    return p


def _strap(f, x0, x1, y0, y1, t, eng):
    z0, z1 = (0.0, 2.0 * t) if eng else (-t, t)
    skip = ("-z",) if eng else ()
    geom.box(f, (x0, y0, z0), (x1, y1, z1), IRON, skip=skip,
             uv_fn=lambda side, pts: [(pt[0] + pt[2], (pt[1] - y0) / (y1 - y0) * 0.25) for pt in pts])


def _cusps(f, arc, z0, z1, back):
    """Three pointed cusps per side on the arch's intrados, each a flat
    triangle standing into the opening (front, back and its two edges)."""
    n = len(arc)
    for k in (3, 6, 9, n - 4, n - 7, n - 10):
        if not 0 < k < n - 1:
            continue
        (xa, ya), (xb, yb) = arc[k - 1], arc[k + 1]
        mx, my = arc[k]
        # Toward the arch's inside: the normal of the chord pointing to the centre line / down.
        cx, cy = 0.0, min(ya, yb) - 5.0
        nx, ny = yb - ya, -(xb - xa)
        ln = math.hypot(nx, ny) or 1.0
        nx, ny = nx / ln, ny / ln
        if nx * (cx - mx) + ny * (cy - my) < 0:
            nx, ny = -nx, -ny
        tip = (mx + nx * 0.32, my + ny * 0.32)
        tri = [(xa, ya), (xb, yb), tip]
        if (xb - xa) * (tip[1] - ya) - (yb - ya) * (tip[0] - xa) < 0:
            tri = [tri[1], tri[0], tri[2]]
        _prism(f, tri, z0, z1, BEAM, lambda x, y: (x + y, 0.3), back=back, skip_edges={0})


def _spandrels(g, arc_out):
    """2D outlines of the lower and upper spandrel panels on both sides."""
    half, top, bottom, inner = g["half"], g["top"], g["bottom"], g["inner"]
    apex, collar_top = g["collar"]
    lower, upper = [], []
    right = [pt for pt in arc_out if pt[0] > 1e-6]  # the right arc, apex excluded
    right.sort(key=lambda pt: -pt[0])  # from the post toward the middle
    # Where the outer curve runs above the rafter's underside (it may meet it twice).
    inside = [pt[1] > bottom(pt[0]) for pt in right]
    for sx in (-1.0, 1.0):
        # Lower: from the post face up the curve to where it meets the rafter.
        pts = [(inner, None)]
        curve = []
        for (x, y), above in zip(right, inside):
            if x > inner:
                continue
            if above:
                break
            curve.append((x, y))
        if curve:
            hit_x = curve[-1][0]
            outline = [(inner, _arc_y(right, inner))] + curve + [(hit_x, bottom(hit_x)), (inner, bottom(inner))]
            outline = [(sx * x, y) for x, y in outline]
            if sx < 0:
                outline.reverse()
            lower.append(_clean(outline))
        # Upper: between the rafter, the collar and the curve's top part.
        tail = []
        seen_above = False
        for (x, y), above in zip(right, inside):
            if above:
                seen_above = True
                continue
            if seen_above and y <= apex:
                tail.append((x, y))
        if tail:
            x0 = tail[0][0]
            xc = _x_at(bottom, apex, 0.0, half)
            x_top = tail[-1][0]
            outline = [(x0, bottom(x0)), (x0, tail[0][1])] + tail[1:] + [(x_top, apex), (xc, apex)]
            # Wound counter-clockwise for sx = 1: rafter edge, down to the curve, along it, then the collar.
            outline = [(sx * x, y) for x, y in outline]
            if _area(outline) < 0:
                outline.reverse()
            upper.append(_clean(outline))
        _ = pts
    return lower, upper


def _arc_y(curve, x):
    for (xa, ya), (xb, yb) in zip(curve, curve[1:]):
        if min(xa, xb) - 1e-9 <= x <= max(xa, xb) + 1e-9 and abs(xb - xa) > 1e-9:
            return ya + (yb - ya) * (x - xa) / (xb - xa)
    return curve[0][1]


def _area(outline) -> float:
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]))


def _clean(outline):
    out = []
    for p in outline:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-4:
            out.append(p)
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) < 1e-4:
        out.pop()
    if _area(out) < 0:
        out.reverse()
    return out


def truss_shell(g, eng: bool) -> list:
    """A coarse camera shell (assemble.py adds it to the collider): the
    region between the arch braces and the rafters on each side, front and
    back, as triangles (camera rays stop short of the truss)."""
    half, top, inner = g["half"], g["top"], g["inner"]
    apex = g["collar"][0]
    arc = geom.pointed_arc(2.0 * inner, g["spring"], 4)
    out = []
    for sx in (-1.0, 1.0):
        side = [pt for pt in arc if pt[0] * sx > 1e-6]
        side.sort(key=lambda pt: -abs(pt[0]))
        outline = [(sx * half, 0.0)] + side + [(0.0, apex), (0.0, top(0.0)), (sx * half, top(sx * half))]
        if _area(outline) < 0:
            outline.reverse()
        for z, nz in (((0.25 if eng else T_POST), 1.0), ((None if eng else -T_POST), -1.0)):
            if z is None:
                continue
            faces: list = []
            add_face(faces, [(x, y, z) for x, y in outline], "castle_wall|world", None, (0.0, 0.0, nz))
            out += [list(face.pts) for face in faces]
    return out


def beam_ridge(length: float, depth: float = 0.6, width: float = 0.4, slope: float = 1.0, gap: float = 0.06) -> Piece:
    """`kit_beam_ridge`: the ridge beam along local x from 0 to `length`,
    its top a ridge `gap` under the origin (the sky's ridge line) with its
    two upper faces parallel to the sky pitches (`slope`), `width` wide and
    `depth` deep. Its ends are not drawn (they stand against the gables)."""
    p = Piece("")
    hw = width / 2.0
    prof = [(-hw, -depth), (hw, -depth), (hw, -gap - hw * slope), (0.0, -gap), (-hw, -gap - hw * slope)]
    # Profile in (z, y); faces between consecutive profile points along x.
    lens = [0.0]
    for (za, ya), (zb, yb) in zip(prof, prof[1:] + prof[:1]):
        lens.append(lens[-1] + math.hypot(zb - za, yb - ya))
    total = lens[-1]
    for i, ((za, ya), (zb, yb)) in enumerate(zip(prof, prof[1:] + prof[:1])):
        pts = [(0.0, ya, za), (length, ya, za), (length, yb, zb), (0.0, yb, zb)]
        pa, pb = lens[i] / total * 0.6, lens[i + 1] / total * 0.6
        nrm = (0.0, (zb - za), -(yb - ya))
        # Outward: away from the profile's centre.
        cz, cy = 0.0, -depth / 2.0
        mz, my = (za + zb) / 2, (ya + yb) / 2
        if nrm[2] * (mz - cz) + nrm[1] * (my - cy) < 0:
            nrm = (0.0, -nrm[1], -nrm[2])
        add_face(p.faces, pts, BEAM, [(0.0, pa), (length, pa), (length, pb), (0.0, pb)], nrm, anchor=True)
    p.meta.update(length=length)
    return p


# -- the wainscot -----------------------------------------------------------------------------

WAINSCOT_SKIRT = (0.18, 0.08)  # skirting height and projection
WAINSCOT_FIELD = 0.05  # the panelled face's projection
WAINSCOT_CAP = (0.15, G.WAINSCOT_PROUD)  # the cap rail's height and projection (the deepest part)


def wainscot(length: float, height: float = 3.2, ends=("cap", "cap"), rise: float = 0.0) -> Piece:
    """`kit_wainscot`: oak panelling `height` high along local x (0 ..
    `length`), skirting, a panelled face (the wood sheet's `wainscot` strip:
    stiles every 0.8 m, two tiers of fielded panels, mapped by height so the
    painted rails fall where they are drawn) and a cap rail at most
    grid.WAINSCOT_PROUD off the wall. `rise`: its bottom and top climb that
    much over its length (beside a stair). Ends as geom.sweep (cap, miter,
    outer, none)."""
    p = Piece("")
    sh, sp = WAINSCOT_SKIRT
    ch, cpr = WAINSCOT_CAP
    fz = WAINSCOT_FIELD
    prof = [(0.0, 0.0), (sp, 0.0), (sp, sh), (fz, sh + 0.02), (fz, height - ch - 0.03), (cpr, height - ch),
            (cpr, height), (0.0, height)]
    faces: list = []
    y_at = (lambda x: rise * x / length) if abs(rise) > 1e-9 else None
    geom.sweep(faces, prof, 0.0, length, PANEL, 3.2, ends=tuple(ends), y_at=y_at)
    scale = 3.2 / height
    for face in faces:
        uv = []
        for (u, _), (x, y, z) in zip(face.uv, face.pts):
            base = rise * x / length if y_at else 0.0
            if face.anchor:
                uv.append((u, min(max((y - base) * scale, 0.0), 3.2)))
            else:
                uv.append((z * 4.0, min(max((y - base) * scale, 0.0), 3.2)))
        face.uv = uv
    p.faces = faces
    p.meta.update(height=height, proud=cpr, rise=rise)
    return p


def wainscot_crest(width: float = 0.72, height: float = 0.86, proud: float = 0.035) -> Piece:
    """`kit_wainscot_crest`: a carved shield on the wainscot's upper tier,
    origin at its bottom point's foot on the wainscot face plane (y = 0 its
    lowest point), the trim sheet's `crest_blank` tile, `proud` in front of
    the panelled face (its back at grid.WAINSCOT_PROUD - its depth)."""
    p = Piece("")
    hw = width / 2.0
    z0 = WAINSCOT_FIELD + 0.004
    z1 = z0 + proud
    n = 5
    # Outline: flat top, straight sides to 45 % of the height, curving to the point.
    pts = [(-hw, height), (-hw, height * 0.5)]
    for i in range(1, n):
        t = i / n
        pts.append((-hw * math.cos(t * math.pi / 2), height * 0.5 * (1 - math.sin(t * math.pi / 2))))
    pts.append((0.0, 0.0))
    for i in range(n - 1, 0, -1):
        t = i / n
        pts.append((hw * math.cos(t * math.pi / 2), height * 0.5 * (1 - math.sin(t * math.pi / 2))))
    pts += [(hw, height * 0.5), (hw, height)]
    outline = list(reversed(pts))  # counter-clockwise seen from the front
    uv = geom.bbox_uv(outline)
    add_face(p.faces, [(x, y, z1) for x, y in outline], CREST, uv, (0.0, 0.0, 1.0))
    m = len(outline)
    for i in range(m):
        (xa, ya), (xb, yb) = outline[i], outline[(i + 1) % m]
        nrm = (yb - ya, -(xb - xa), 0.0)
        add_face(p.faces, [(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z1)], CREST,
                 [(0.02, 0.02), (0.04, 0.02), (0.04, 0.04), (0.02, 0.04)], nrm)
    p.walkthrough = False
    p.meta.update(width=width, height=height, proud=z1)
    return p
