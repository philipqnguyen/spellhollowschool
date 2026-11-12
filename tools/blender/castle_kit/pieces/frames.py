"""Portrait frames, the cluster generator and the guardian portrait (castle
kit M4, docs/art/castle-kit.md "Portraits and tapestries").

Frames are geometry on the frame ring of a canvas cell of the portrait
sheets (castle_kit/portraits.py paints them: 8 x 8 cells of 256 px, a 20 px
ring in the palette's frame colours round a 208 px painting): the front of
the frame and its edges sample the ring, the canvas a centred crop of the
painting with the frame's inner aspect (grid.canvas_paint_uv), LOD1 one
quad showing the whole cell (grid.canvas_full_uv). Faces are tagged
`castle_portraits|fit:canvas_<i>` (the family resolves to the cell's sheet,
castle_portraits_a or _b; assemble.py gives the portrait sheets one fit tile
per canvas: the cell inside its padding, so fit (a, b) in 0..1 is
canvas_full_uv).

Frame: x along the wall (to the right seen from the room), y up, z out of
the wall face (the wall at z = 0); a frame's back lies on the wall and is
not drawn. Pure Python and deterministic (random.Random with the cluster's
seed). No colour here: the ring's colours are the painter's palette names
(frame_gilt, frame_oak, frame_ebony)."""

from __future__ import annotations

import math
import random

from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece, add_face

CELL_IN = G.PORTRAIT_CELL - 2 * G.PORTRAIT_PAD  # 248: the fit tile's span inside the padding
RING = G.PORTRAIT_RING / CELL_IN  # the ring's width in tile units
PAINT0 = (G.PORTRAIT_PAD + G.PORTRAIT_RING - G.PORTRAIT_PAD) / CELL_IN  # the painting's edge in tile units
CANVAS_Z = 0.025  # the canvas stands off the wall (never on its plane)
SHAPE_INDEX = {s: i for i, s in enumerate(G.PORTRAIT_SHAPES)}


def tag(canvas: int) -> str:
    return f"castle_portraits|fit:canvas_{int(canvas)}"


def ring_uv(side: str, s: float, p: float) -> tuple[float, float]:
    """grid.canvas_ring_uv in fit-tile units: `side` n / s / w / e, `s`
    0..1 along it (left to right, bottom to top), `p` 0..1 across the ring
    from its outer edge to the painting."""
    s = min(max(s, 0.0), 1.0)
    q = min(max(p, 0.0), 1.0) * RING
    if side == "n":
        return (s, 1.0 - q)
    if side == "s":
        return (s, q)
    if side == "w":
        return (q, s)
    return (1.0 - q, s)


def paint_uv(a: float, b: float, aspect: float) -> tuple[float, float]:
    """grid.canvas_paint_uv in fit-tile units: (a, b) in 0..1 across a canvas
    face of width / height `aspect`, a centred crop of the painting."""
    w = h = G.PORTRAIT_PAINT / CELL_IN
    if aspect >= 1.0:
        h = w / aspect
    else:
        w = h * aspect
    a = min(max(a, 0.0), 1.0)
    b = min(max(b, 0.0), 1.0)
    return (0.5 - w / 2 + a * w, 0.5 - h / 2 + b * h)


def border_for(w: float, h: float) -> float:
    """Frame border width: thin on small frames, wide on big ones (grid.PORTRAIT_BORDER)."""
    lo, hi = G.PORTRAIT_BORDER
    k = (min(w, h) - 0.8) / (3.0 - 0.8)
    return round(lo + (hi - lo) * min(max(k, 0.0), 1.0), 4)


def depth_for(w: float, h: float) -> float:
    """How far a frame stands off the wall (at most grid.PORTRAIT_PROUD)."""
    k = (min(w, h) - 0.8) / (3.0 - 0.8)
    return round(min(G.PORTRAIT_PROUD, 0.06 + 0.05 * min(max(k, 0.0), 1.0)), 4)


# -- outlines -----------------------------------------------------------------------------


def outline(shape: str, x0: float, y0: float, w: float, h: float) -> list[tuple[float, float]]:
    """A frame's outline, counter-clockwise from the bottom left: rect (4
    points), arch (pointed head, 7 points), oval (8 points)."""
    if shape == "rect":
        return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    cx = x0 + w / 2
    if shape == "arch":
        lan = geom.lancet_outline(w, h, 2)
        return [(cx + x, y0 + y) for x, y in lan]
    if shape == "oval":
        cy = y0 + h / 2
        return [(cx + w / 2 * math.cos(-math.pi / 2 + 2 * math.pi * (i + 0.5) / 8),
                 cy + h / 2 * math.sin(-math.pi / 2 + 2 * math.pi * (i + 0.5) / 8)) for i in range(8)]
    raise ValueError(f"unknown frame shape {shape!r}")


def _side_of(dx: float, dy: float) -> str:
    """Which ring side an outline segment samples, by its outward normal."""
    nx, ny = dy, -dx  # outward for a counter-clockwise outline
    if abs(ny) >= abs(nx):
        return "n" if ny > 0 else "s"
    return "e" if nx > 0 else "w"


def frame_faces(faces: list, lod: list | None, x0: float, y0: float, w: float, h: float, shape: str, canvas: int,
                border: float | None = None, depth: float | None = None) -> None:
    """One framed portrait: LOD0 faces into `faces` (front of the frame on
    the ring, its outer edges, a rect frame's inner lip, the canvas) and its
    LOD1 quad into `lod`."""
    bw = border if border is not None else border_for(w, h)
    dp = depth if depth is not None else depth_for(w, h)
    t = tag(canvas)
    out = outline(shape, x0, y0, w, h)
    inn = outline(shape, x0 + bw, y0 + bw, w - 2 * bw, h - 2 * bw)
    n = len(out)
    # A rect frame's front is flat with an inner lip down to the canvas; the
    # arch and the oval slope their front from the outer edge to the canvas
    # (fewer faces: grid.PORTRAIT_TRIS).
    lip = shape == "rect"
    z_in = dp if lip else CANVAS_Z + 0.02
    ix0, ix1 = min(p[0] for p in inn), max(p[0] for p in inn)
    iy0, iy1 = min(p[1] for p in inn), max(p[1] for p in inn)
    for i in range(n):
        a, b = out[i], out[(i + 1) % n]
        c, d = inn[(i + 1) % n], inn[i]
        side = _side_of(b[0] - a[0], b[1] - a[1])
        if side in ("n", "s"):
            sa, sb = (a[0] - x0) / w, (b[0] - x0) / w
            sc, sd = (c[0] - x0) / w, (d[0] - x0) / w
        else:
            sa, sb = (a[1] - y0) / h, (b[1] - y0) / h
            sc, sd = (c[1] - y0) / h, (d[1] - y0) / h
        pts = [(a[0], a[1], dp), (b[0], b[1], dp), (c[0], c[1], z_in), (d[0], d[1], z_in)]
        uv = [ring_uv(side, sa, 0.0), ring_uv(side, sb, 0.0), ring_uv(side, sc, 0.85), ring_uv(side, sd, 0.85)]
        add_face(faces, pts, t, uv, (0.0, 0.0, 1.0))
        # Outer edge: from the wall to the front, facing out of the frame.
        pts = [(a[0], a[1], 0.0), (b[0], b[1], 0.0), (b[0], b[1], dp), (a[0], a[1], dp)]
        nrm = (b[1] - a[1], -(b[0] - a[0]), 0.0)
        add_face(faces, pts, t, [ring_uv(side, sa, 0.0), ring_uv(side, sb, 0.0), ring_uv(side, sb, 0.12),
                                 ring_uv(side, sa, 0.12)], nrm)
        if lip:
            pts = [(d[0], d[1], dp), (c[0], c[1], dp), (c[0], c[1], CANVAS_Z), (d[0], d[1], CANVAS_Z)]
            nrm = (-(c[1] - d[1]), c[0] - d[0], 0.0)
            add_face(faces, pts, t, [ring_uv(side, sd, 0.88), ring_uv(side, sc, 0.88), ring_uv(side, sc, 1.0),
                                     ring_uv(side, sd, 1.0)], nrm)
    aspect = (ix1 - ix0) / (iy1 - iy0)
    pts = [(p[0], p[1], CANVAS_Z) for p in inn]
    uv = [paint_uv((p[0] - ix0) / (ix1 - ix0), (p[1] - iy0) / (iy1 - iy0), aspect) for p in inn]
    add_face(faces, pts, t, uv, (0.0, 0.0, 1.0))
    if lod is not None:
        q = [(x0, y0, dp), (x0 + w, y0, dp), (x0 + w, y0 + h, dp), (x0, y0 + h, dp)]
        add_face(lod, q, t, [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], (0.0, 0.0, 1.0))


def portrait(w: float, h: float, shape: str | None = None, canvas: int = 0, border: float | None = None,
             depth: float | None = None) -> Piece:
    """`kit_portrait_<w>x<h>_<shape>`: one framed portrait, its bottom
    centre at the origin (gallery and tests; clusters use cluster())."""
    p = Piece("")
    p.lod1 = []
    frame_faces(p.faces, p.lod1, -w / 2, 0.0, w, h, shape or "rect", canvas, border, depth)
    return p


def cluster(frames) -> Piece:
    """`kit_portrait_cluster`: the frames rules.py laid out for one cluster,
    each [x0, y0, w, h, shape index, canvas, border, depth] in the cluster's
    frame (x along the wall from its origin, y world height above it)."""
    p = Piece("")
    p.lod1 = []
    for x0, y0, w, h, si, canvas, bw, dp in frames:
        frame_faces(p.faces, p.lod1, x0, y0, w, h, G.PORTRAIT_SHAPES[int(si)], int(canvas), bw, dp)
    p.meta["frames"] = len(frames)
    return p


# -- the cluster generator ------------------------------------------------------------------


def _kind_range(kind: str, sheet: str) -> range:
    lo, hi = G.PORTRAIT_KINDS[kind]
    if (sheet, hi - 1) == G.GUARDIAN_CANVAS:
        hi -= 1  # the guardian's canvas is never hung in a cluster
    return range(lo, hi)


def kind_for(w: float, h: float, rng: random.Random, shape: str = "rect") -> str:
    """Tall frames take figures, wide ones landscapes, squarish ones still
    lifes or figures (docs/art/castle-kit.md "Portraits and tapestries"). The gallery mix has no
    square frame, so 5 : 6 (1.2) counts as squarish here; ovals and arches
    always frame a figure."""
    r = h / w
    if shape != "rect" or r >= 1.22:
        return "figure"
    if r <= 0.87:
        return "landscape"
    return "still_life" if rng.random() < 0.5 else "figure"


def pick_canvas(kind: str, sheet: str, t: float, y: float, history: list, rng: random.Random, apart: float = 6.0) -> int:
    """A canvas of `kind` not hung within `apart` metres on this face
    (`history`: (canvas, t, y) already placed there); the least recently
    used one when every canvas of the kind is that close."""
    cands = list(_kind_range(kind, sheet))
    near = {c for c, ht, hy in history if math.hypot(ht - t, hy - y) < apart}
    free = [c for c in cands if c not in near]
    if free:
        return free[int(rng.random() * len(free)) % len(free)]
    last = {c: i for i, (c, _, _) in enumerate(history)}
    return min(cands, key=lambda c: (last.get(c, -1), c))


# Castle M5a (grid.PORTRAIT_FILL_M5A, the densely hung walls): the cluster is packed
# by guillotine cuts instead of M4's columns. A frame goes into a corner of the free
# rectangle, the rest splits into two rectangles PORTRAIT_GAP away from it, and each is
# packed the same way. Frames are picked by the mix's weight times their area, among
# those that leave either almost nothing (at most SNUG) or room for another frame
# along each axis, so few slivers stay empty.
SNUG = 0.2  # metres of a free rectangle a frame may leave unused along an axis
ATTEMPTS = 64  # seeded layouts tried per cluster; the one nearest the fill band's middle is kept
ATTEMPTS_MAX = 2000  # ... and while none is inside the band, up to this many (the narrow clusters)


def _pack(rect, mix, rng: random.Random, out: list, depth: int = 0) -> None:
    """Guillotine packing of the free rectangle (x0, x1, y0, y1) into `out`
    [(x, y, w, h, shape)]."""
    gap = G.PORTRAIT_GAP
    x0, x1, y0, y1 = rect
    rw, rh = x1 - x0, y1 - y0
    fits = [m for m in mix if m[0] <= rw + 1e-9 and m[1] <= rh + 1e-9]
    if not fits or depth > 64:
        return
    min_w = min(m[0] for m in mix)
    min_h = min(m[1] for m in mix)

    def tidy(m) -> bool:
        lw, lh = rw - m[0], rh - m[1]
        return (lw <= SNUG + 1e-9 or lw >= gap + min_w - 1e-9) and (lh <= SNUG + 1e-9 or lh >= gap + min_h - 1e-9)

    cands = [m for m in fits if tidy(m)] or fits
    m = rng.choices(cands, weights=[c[3] * c[0] * c[1] for c in cands])[0]
    w, h = m[0], m[1]
    # The corner: bottom or top, left or right (by seed), so the stacks do not all
    # rest on one line.
    left = rng.random() < 0.5
    bottom = rng.random() < 0.6
    fx = x0 if left else x1 - w
    fy = y0 if bottom else y1 - h
    out.append((round(fx, 4), round(fy, 4), w, h, m[2]))
    # The two rests: split along the axis with the longer leftover first (the
    # bigger rest stays whole).
    sx0, sx1 = (x0 + w + gap, x1) if left else (x0, x1 - w - gap)
    sy0, sy1 = (y0 + h + gap, y1) if bottom else (y0, y1 - h - gap)
    fx0, fx1 = (x0, x0 + w) if left else (x1 - w, x1)
    fy0, fy1 = (y0, y0 + h) if bottom else (y1 - h, y1)
    if rw - w >= rh - h:
        side = (sx0, sx1, y0, y1)  # full height beside the frame
        cap = (fx0, fx1, sy0, sy1)  # over or under the frame, its width
    else:
        side = (sx0, sx1, fy0, fy1)  # beside the frame, its height
        cap = (x0, x1, sy0, sy1)  # full width over or under it
    for r in (side, cap):
        if r[1] - r[0] >= min_w - 1e-9 and r[3] - r[2] >= min_h - 1e-9:
            _pack(r, mix, rng, out, depth + 1)


def layout_cluster(t0: float, t1: float, y0: float, y1: float, seed: int, mix=G.PORTRAIT_FRAMES, keep_out=(),
                   sheet: str = "castle_portraits_a", history: list | None = None):
    """Frames filling the wall rectangle t0..t1 x y0..y1 (t along the face,
    y world heights), packed by seed: grid.PORTRAIT_GAP apart,
    grid.PORTRAIT_EDGE inside the rectangle, covering grid.PORTRAIT_FILL of
    it, clear of `keep_out` rectangles (t0, t1, y0, y1; already grown by
    grid.PORTRAIT_KEEP_OUT). Returns [(ta, yb, w, h, shape, canvas)] sorted
    by t then y; `history` (this face's canvases so far) grows.

    Castle M5a: guillotine packing (_pack) over the band, split first at
    keep-outs that stand over its whole height (bay shafts: the fill is then
    measured over the wall they leave, `free_spans`); a frame that would hit
    another keep-out (a lantern) gives way to the largest smaller frame that
    fits its place clear of it, or to nothing."""
    history = history if history is not None else []
    edge = G.PORTRAIT_EDGE
    a0, a1, b0, b1 = t0 + edge, t1 - edge, y0 + edge, y1 - edge
    spans = free_spans(t0, t1, y0, y1, keep_out)
    area = sum(b - a for a, b in spans) * (y1 - y0)
    if area <= 0.0:
        return []
    lo, hi = G.PORTRAIT_FILL
    best = None

    def hits(r):
        return any(r[0] < k[1] and k[0] < r[1] and r[2] < k[3] and k[2] < r[3] for k in keep_out)

    by_area = sorted(mix, key=lambda q: (-q[0] * q[1], q[0], q[1], q[2]))
    for attempt in range(ATTEMPTS_MAX):
        if attempt >= ATTEMPTS and best[0] < 1.0:
            break
        rng = random.Random(seed * 1000 + attempt)
        packed: list = []
        for sa, sb in spans:
            _pack((max(a0, sa), min(a1, sb), b0, b1), mix, rng, packed)
        frames = []
        for x, y, w, h, shape in packed:
            if not hits((x, x + w, y, y + h)):
                frames.append((x, y, w, h, shape))
                continue
            for alt in by_area:
                if alt[0] > w + 1e-9 or alt[1] > h + 1e-9:
                    continue
                spot = next(((ax, ay) for ax in (x, x + w - alt[0]) for ay in (y, y + h - alt[1])
                             if not hits((ax, ax + alt[0], ay, ay + alt[1]))), None)
                if spot is not None:
                    frames.append((round(spot[0], 4), round(spot[1], 4), alt[0], alt[1], alt[2]))
                    break
        cover = sum(f[2] * f[3] for f in frames) / area
        # Too full: drop small frames until inside the band.
        while cover > hi and frames:
            k = min(range(len(frames)), key=lambda i: (frames[i][2] * frames[i][3], rng.random()))
            cover -= frames[k][2] * frames[k][3] / area
            frames.pop(k)
        # Inside the band (score below 1), the nearer its middle the better.
        score = abs(cover - (lo + hi) / 2) if lo <= cover <= hi else 1.0 + min(abs(cover - lo), abs(cover - hi))
        if best is None or score < best[0]:
            best = (score, frames, rng)
        if score <= 0.04:
            break
    _, frames, rng = best
    frames.sort(key=lambda f: (f[0], f[1]))
    out = []
    for ta, yb, w, h, shape in frames:
        kind = kind_for(w, h, rng, shape)
        kind = mix_kind(mix) or kind  # Castle M5b (Dungeons): the still-life mix hangs still lifes only
        c = pick_canvas(kind, sheet, ta + w / 2, yb + h / 2, history, rng)
        history.append((c, ta + w / 2, yb + h / 2))
        out.append((ta, yb, w, h, shape, c))
    return out


# Castle M5b (Dungeons): docs/art/castle-kit.md "Pieces". A mix may fix its canvases' kind
# (grid.PORTRAIT_MIX_KIND: the still_life mix hangs still lifes only); the gallery mix
# (None) keeps choosing by the frame's aspect (kind_for), so its clusters come out as
# before.


def mix_kind(mix) -> str | None:
    """The canvas kind grid.PORTRAIT_MIX_KIND fixes for a mix (its frames
    tuple, as layout_cluster gets it), or None (choose by the frame)."""
    for name, frames in G.PORTRAIT_MIXES.items():
        if frames is mix or tuple(frames) == tuple(mix):
            return G.PORTRAIT_MIX_KIND.get(name)
    return None


def free_spans(t0: float, t1: float, y0: float, y1: float, keep_out=()) -> list[tuple[float, float]]:
    """The parts of t0..t1 that no keep-out covers over the band's whole
    height y0..y1 (castle M5a: the bay shafts of the Portrait Gallery)."""
    walls = sorted((k[0], k[1]) for k in keep_out if k[2] <= y0 + 1e-9 and k[3] >= y1 - 1e-9 and k[1] > t0 and k[0] < t1)
    out, t = [], t0
    for a, b in walls:
        if a > t + 1e-9:
            out.append((t, a))
        t = max(t, b)
    if t1 > t + 1e-9:
        out.append((t, t1))
    return out


def coverage(frames, t0: float, t1: float, y0: float, y1: float) -> float:
    return sum(f[2] * f[3] for f in frames) / ((t1 - t0) * (y1 - y0))


# -- the guardian portrait (kit_portrait_guardian_a) -------------------------------------------
#
# Frame: the door's centre on the corridor's wall face at the door's floor,
# +z into the corridor. It hangs inside the 3 x 4 arch of the common-room
# door (the arch piece lines its reveal 0.5 m deep): a pointed gilt frame
# (castle_trim frame_gilt) round sheet b's canvas 63, our own design (a tall
# elderly keeper in a deep green robe with a lantern and a ring of keys
# under a stone arch; castle_kit/portraits.py), the canvas filling the
# opening. Walk-through: the painting is the door (as the greybox canvas
# was), so it has no collider and the door's clear box ignores it.
GUARDIAN_INSET = 0.03  # clear of the arch's jambs and soffit
GUARDIAN_BAND = 0.22  # the frame's width
GUARDIAN_Z = (-0.32, -0.12)  # canvas plane and frame front, inside the reveal
GUARDIAN_SILL = 0.24  # the frame's bottom rail


def guardian(width: float = 3.0, apex: float = 4.0, canvas: int = G.GUARDIAN_CANVAS[1], segs: int = 8) -> Piece:
    """`kit_portrait_guardian_a`: (width, apex) of the arch it hangs in."""
    p = Piece("")
    f = p.faces
    gilt = "castle_trim|fit:frame_gilt"
    zc, zf = GUARDIAN_Z
    ow, oa = width - 2 * GUARDIAN_INSET, apex - GUARDIAN_INSET
    iw, ia = ow - 2 * GUARDIAN_BAND, oa - GUARDIAN_BAND
    out = geom.lancet_outline(ow, oa, segs)
    inn = [(x, y + GUARDIAN_SILL) for x, y in geom.lancet_outline(iw, ia - GUARDIAN_SILL, segs)]
    n = len(out)
    for i in range(n):
        a, b = out[i], out[(i + 1) % n]
        c, d = inn[(i + 1) % n], inn[i]
        add_face(f, [(a[0], a[1], zf), (b[0], b[1], zf), (c[0], c[1], zf), (d[0], d[1], zf)], gilt,
                 [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], (0.0, 0.0, 1.0))
        add_face(f, [(d[0], d[1], zf), (c[0], c[1], zf), (c[0], c[1], zc), (d[0], d[1], zc)], gilt,
                 [(0.0, 1.0), (1.0, 1.0), (1.0, 0.6), (0.0, 0.6)], (-(c[1] - d[1]), c[0] - d[0], 0.0))
    # The frame's outer rim, from its back to its front (the portrait-door back: the whole
    # depth, so the swung door reads as a solid slab), and a plaque on the sill rail.
    zb, zr = GUARDIAN_BACK
    for i in range(n):
        a, b = out[i], out[(i + 1) % n]
        add_face(f, [(a[0], a[1], zr), (b[0], b[1], zr), (b[0], b[1], zf), (a[0], a[1], zf)], gilt,
                 [(0.0, 0.0), (1.0, 0.0), (1.0, 0.25), (0.0, 0.25)], (b[1] - a[1], -(b[0] - a[0]), 0.0))
    add_face(f, [(-0.35, 0.05, zf + 0.01), (0.35, 0.05, zf + 0.01), (0.35, 0.19, zf + 0.01), (-0.35, 0.19, zf + 0.01)],
             "castle_trim|fit:plaque", [(0, 0), (1, 0), (1, 1), (0, 1)], (0.0, 0.0, 1.0))
    xs = [q[0] for q in inn]
    ys = [q[1] for q in inn]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    aspect = (x1 - x0) / (y1 - y0)
    add_face(f, [(x, y, zc) for x, y in inn], tag(canvas),
             [paint_uv((x - x0) / (x1 - x0), (y - y0) / (y1 - y0), aspect) for x, y in inn], (0.0, 0.0, 1.0))
    guardian_back(f, out, inn, zb, zr)
    p.walkthrough = True
    p.meta.update(canvas=canvas, aspect=aspect, opening=(width, apex), back=zr)
    return p


# Portrait back (a playtest found that from inside common room A the portrait door showed
# only a gilt outline, the corridor and the Grand Staircase through it: every face above
# faces +z, the corridor). Seen from the common room (-z) the door is the back of a hung
# painting: an oak backing board of vertical planks GUARDIAN_BACK[0] behind the canvas,
# the frame's back (an oak band round it, proud of the board) and a stretcher cross (a
# rail and a centre stile) on the board, flush with the band. All castle_trim's frame_oak
# tile (the palette's frame_oak; castle_trim is already one of the door's nodes, so the
# seventh-floor corridor keeps its 8 draws). Same group, so it swings with the painting.
GUARDIAN_BACK = (-0.36, -0.39)  # the board's plane, the frame back's and the stretchers' (z)
GUARDIAN_PLANK = 0.42  # the board's planks, at most this wide
GUARDIAN_STRETCHER = 0.09  # the stretchers' width
GUARDIAN_RAIL = 0.45  # the cross rail at this fraction of the board's straight sides
GUARDIAN_RAIL_SHY = 0.008  # the rail's face this much less proud than the stile's


def _clip_x(poly, x0: float, x1: float) -> list[tuple[float, float]]:
    """The convex polygon `poly` [(x, y), ...] clipped to x0 <= x <= x1."""
    out = list(poly)
    for keep in (lambda q: q[0] - x0, lambda q: x1 - q[0]):
        cur, out = out, []
        for i, a in enumerate(cur):
            b = cur[(i + 1) % len(cur)]
            da, db = keep(a), keep(b)
            if da >= 0.0:
                out.append(a)
            if (da < 0.0 < db) or (db < 0.0 < da):
                t = da / (da - db)
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def guardian_back(f: list, out, inn, zb: float, zr: float) -> None:
    """The guardian portrait's back, facing -z (see GUARDIAN_BACK): the frame's back
    from `out` to `inn` at zr with its inner lip down to the board, the board inside
    `inn` at zb in planks, the stretcher cross on it."""
    oak = "castle_trim|fit:frame_oak"
    n = len(out)
    for i in range(n):
        a, b = out[i], out[(i + 1) % n]
        c, d = inn[(i + 1) % n], inn[i]
        add_face(f, [(a[0], a[1], zr), (b[0], b[1], zr), (c[0], c[1], zr), (d[0], d[1], zr)], oak,
                 [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], (0.0, 0.0, -1.0))
        add_face(f, [(d[0], d[1], zr), (c[0], c[1], zr), (c[0], c[1], zb), (d[0], d[1], zb)], oak,
                 [(0.0, 0.85), (1.0, 0.85), (1.0, 1.0), (0.0, 1.0)], (-(c[1] - d[1]), c[0] - d[0], 0.0))
    xs = [q[0] for q in inn]
    ys = [q[1] for q in inn]
    ix0, ix1, iy0, iy1 = min(xs), max(xs), min(ys), max(ys)
    planks = max(1, math.ceil((ix1 - ix0) / GUARDIAN_PLANK - 1e-9))
    pw = (ix1 - ix0) / planks
    for k in range(planks):
        xa, xb = ix0 + k * pw, ix0 + (k + 1) * pw
        poly = _clip_x(inn, xa, xb)
        if len(poly) >= 3:
            add_face(f, [(x, y, zb) for x, y in poly], oak,
                     [((y - iy0) / (iy1 - iy0), (x - xa) / pw) for x, y in poly], (0.0, 0.0, -1.0))
    # The stretcher cross: a rail across the straight part and a stile up the middle,
    # each a bar from the board out toward the frame back's plane (its board side not
    # drawn); the rail stops GUARDIAN_RAIL_SHY short of the stile's face (no z-fighting).
    s = GUARDIAN_STRETCHER / 2.0
    spring = inn[2][1]  # lancet_outline: the right jamb's top, where the head springs
    yr = iy0 + (spring - iy0) * GUARDIAN_RAIL
    geom.box(f, (ix0, yr - s, zr + GUARDIAN_RAIL_SHY), (ix1, yr + s, zb), oak, skip=("+z", "-x", "+x"),
             uv_fn=lambda side, pts: [((pt[0] - ix0) / (ix1 - ix0), _bar_b(side)) for pt in pts])
    top = iy1 - 0.3
    geom.box(f, (-s, iy0, zr), (s, top, zb), oak, skip=("+z", "-y"),
             uv_fn=lambda side, pts: [((pt[1] - iy0) / (top - iy0), _bar_b(side)) for pt in pts])


# Portrait back, round 2: the other front-only pieces seen from
# behind (the passage entrances' painting and panel from inside their passages, the
# Fruit Bowl Painting from inside the kitchens) get a flat ledged-and-braced board on
# the props atlas: vertical oak_aged planks with soot seams BOARD_Z off the wall face
# (inside the piece's own depth, so it never shows from the front and slides or swings
# with it), and two oak ledges with a brace between them BOARD_LAYER nearer the viewer.
# Flat (no relief behind the wall face), so a sliding panel's back never cuts through
# the jamb; colours are palette names only.
BOARD_Z = 0.012  # the planks' plane (z, facing -z)
BOARD_LAYER = 0.006  # the ledges and the brace this much nearer the back (more than the lint's 2 mm bins)
BOARD_PLANK = 0.3  # planks at most this wide
BOARD_SEAM = 0.02  # the dark seam between planks
BOARD_LEDGE = 0.12  # the ledges' height
BOARD_LEDGES = (0.2, 0.62)  # the ledges' bottoms, as fractions of the board's height
PLANK_TAG = G.palette_tag("wood", "oak_aged")
SEAM_TAG = G.palette_tag("stone", "soot")
LEDGE_TAG = G.palette_tag("wood", "oak")


def board_back(faces: list, x0: float, x1: float, y0: float, y1: float, z: float = BOARD_Z) -> None:
    """A flat board facing -z over x0..x1 x y0..y1 at `z` (see BOARD_Z)."""
    n = max(1, math.ceil((x1 - x0) / BOARD_PLANK - 1e-9))
    pw = (x1 - x0) / n
    s = BOARD_SEAM / 2.0
    down = (0.0, 0.0, -1.0)
    for k in range(n):
        a = x0 + k * pw + (s if k else 0.0)
        b = x0 + (k + 1) * pw - (s if k < n - 1 else 0.0)
        add_face(faces, [(a, y0, z), (b, y0, z), (b, y1, z), (a, y1, z)], PLANK_TAG, None, down)
        if k < n - 1:
            c = x0 + (k + 1) * pw
            add_face(faces, [(c - s, y0, z), (c + s, y0, z), (c + s, y1, z), (c - s, y1, z)], SEAM_TAG, None, down)
    zl = z - BOARD_LAYER
    lx0, lx1 = x0 + 0.06, x1 - 0.06
    ya, yb = (y0 + (y1 - y0) * f for f in BOARD_LEDGES)
    for yl in (ya, yb):
        add_face(faces, [(lx0, yl, zl), (lx1, yl, zl), (lx1, yl + BOARD_LEDGE, zl), (lx0, yl + BOARD_LEDGE, zl)], LEDGE_TAG,
                 None, down)
    # The brace: from the lower ledge's hinge end up to the upper ledge's far end, cut square
    # against both ledges (a parallelogram BOARD_LEDGE wide across).
    lo, hi = ya + BOARD_LEDGE, yb
    add_face(faces, [(lx0, lo, zl), (lx0 + BOARD_LEDGE * 1.4, lo, zl), (lx1, hi, zl), (lx1 - BOARD_LEDGE * 1.4, hi, zl)],
             LEDGE_TAG, None, down)


def _bar_b(side: str) -> float:
    """A stretcher's place across the frame_oak tile: its face on the tile's light
    ridge, its sides in the dark edge band, so the cross reads on the darker planks."""
    return 0.5 if side == "-z" else 0.05
