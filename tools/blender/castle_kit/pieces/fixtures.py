"""Fixtures: wall and hanging lanterns, banners, newel posts. Lantern iron, glass glow and the banner rods are `castle_props`
faces on slots of the shared props atlas (data/art/castle_textures.json
`props_slots`); banners are `castle_cloth`, newels `castle_trim`."""

from __future__ import annotations

import math

from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece, add_face

# Props-atlas slots the fixtures use: named colours of data/art/castle_textures.json
# `palette` (castle kit M3: no colour literals in piece code, docs/art/castle-kit.md "Colours come from palette data only").
IRON = G.palette_tag("metal", "iron")
# The glow rule (castle kit M2, docs/art/castle-kit.md "Glow"): lantern glass,
# lamp globes and braziers glow `glow_lantern` (warm, never clipping to white
# at the emissive level grid.GLOW_LEVEL); candles `glow_candle`.
GLOW = G.palette_tag("glow", "glow_lantern")
CANDLE = G.palette_tag("glow", "glow_candle")
GILT = G.palette_tag("metal", "gilt")
SLOTS = (("metal", "@iron"), ("glow", "@glow_lantern"), ("metal", "@gilt"), ("glow", "@glow_candle"))


def trim(strip: str) -> str:
    return f"castle_trim|trim:{strip}"


def fit(tile: str) -> str:
    return f"castle_trim|fit:{tile}"


def _hex(r: float, n: int = 6, phase: float = math.pi / 6) -> list[tuple[float, float]]:
    return [(r * math.cos(phase + 2 * math.pi * i / n), r * math.sin(phase + 2 * math.pi * i / n)) for i in range(n)]


def _frustum(faces, centre, r0: float, y0: float, r1: float, y1: float, tag: str, n: int = 6, cap0: bool = False,
             cap1: bool = False) -> None:
    """A hexagonal frustum (or prism, r0 == r1) about a vertical axis; UVs in
    metres (kit tiles keep their density)."""
    cx, cz = centre
    a, b = _hex(r0, n), _hex(r1, n)
    for i in range(n):
        j = (i + 1) % n
        pts = [(cx + a[i][0], y0, cz + a[i][1]), (cx + a[j][0], y0, cz + a[j][1]), (cx + b[j][0], y1, cz + b[j][1]),
               (cx + b[i][0], y1, cz + b[i][1])]
        mx, mz = (a[i][0] + a[j][0]) / 2, (a[i][1] + a[j][1]) / 2
        nn = geom.newell(pts)
        nrm = nn if nn[0] * mx + nn[2] * mz >= 0 else (-nn[0], -nn[1], -nn[2])
        w = math.hypot(a[j][0] - a[i][0], a[j][1] - a[i][1])
        h = math.hypot(y1 - y0, r1 - r0)
        add_face(faces, pts, tag, [(0, 0), (w, 0), (w, h), (0, h)], nrm)
    for cap, r, y, up in ((cap0, r0, y0, False), (cap1, r1, y1, True)):
        if cap and r > 1e-6:
            ring = [(cx + x, y, cz + z) for x, z in _hex(r, n)]
            add_face(faces, ring, tag, [(x, z) for x, _, z in ring], (0.0, 1.0 if up else -1.0, 0.0))


def _lantern_body(faces, centre, top: float) -> float:
    """A hexagonal lantern hanging from `top`: a pointed cap, glowing panes
    between iron posts, a tray and a drop finial. Returns its lowest y."""
    cx, cz = centre
    _frustum(faces, centre, 0.035, top, 0.035, top - 0.05, IRON)  # hanging ring
    _frustum(faces, centre, 0.035, top - 0.05, 0.21, top - 0.17, IRON, cap0=False)
    _frustum(faces, centre, 0.21, top - 0.17, 0.18, top - 0.2, IRON, cap0=True)
    body0, body1 = top - 0.2, top - 0.47
    _frustum(faces, centre, 0.15, body0, 0.15, body1, GLOW)
    # Iron bands round the top and the middle of the panes.
    _frustum(faces, centre, 0.165, body0, 0.165, body0 - 0.03, IRON)
    mid = (body0 + body1) / 2
    _frustum(faces, centre, 0.16, mid + 0.012, 0.16, mid - 0.012, IRON)
    # Iron posts at the hexagon's corners.
    for x, z in _hex(0.165):
        geom.box(faces, (cx + x - 0.018, body1, cz + z - 0.018), (cx + x + 0.018, body0, cz + z + 0.018), IRON,
                 skip=("+y", "-y"))
    _frustum(faces, centre, 0.19, body1, 0.19, body1 - 0.03, IRON, cap0=True)
    _frustum(faces, centre, 0.19, body1 - 0.03, 0.0, body1 - 0.08, IRON)
    return body1 - 0.08


def lantern_wall(arm: float = 0.45) -> Piece:
    """`kit_lantern_wall`: an iron bracket from a wall plate at y = 0 (the
    bracket height) reaching `arm` into the room, the lantern hanging from
    its end. At most 0.6 m hangs below the bracket (castle_kit/schema.py
    LANTERN_DROP)."""
    p = Piece("")
    f = p.faces
    geom.box(f, (-0.09, -0.32, 0.0), (0.09, 0.14, 0.03), IRON, skip=("-z",))
    geom.box(f, (-0.022, -0.03, 0.03), (0.022, 0.03, arm + 0.03), IRON, skip=("-z",))
    # Diagonal brace under the arm (a flat iron, both sides).
    brace = [(0.0, -0.28, 0.03), (0.0, -0.03, arm * 0.75), (0.0, -0.03, arm * 0.55), (0.0, -0.2, 0.03)]
    add_face(f, [(0.012, y, z) for _, y, z in brace], IRON, [(z, y) for _, y, z in brace], (1, 0, 0))
    add_face(f, [(-0.012, y, z) for _, y, z in brace], IRON, [(z, y) for _, y, z in brace], (-1, 0, 0))
    # A scroll curling under the arm (a flat iron strip of constant width,
    # both sides): points on a spiral, offset toward its centre.
    cz, cy = arm * 0.45, -0.12
    curve = []
    for i in range(10):
        a = math.pi * 1.7 * i / 9
        r = 0.08 * (1 - i / 16)
        curve.append((cz + r * math.sin(a), cy + r * math.cos(a), a))
    inner = [(cz + (math.hypot(z - cz, y - cy) - 0.018) * math.sin(a), cy + (math.hypot(z - cz, y - cy) - 0.018) * math.cos(a))
             for z, y, a in curve]
    for i in range(len(curve) - 1):
        (za, ya, _), (zb, yb, _) = curve[i], curve[i + 1]
        (qa, ra), (qb, rb) = inner[i], inner[i + 1]
        quad = [(za, ya), (zb, yb), (qb, rb), (qa, ra)]
        add_face(f, [(0.02, y, z) for z, y in quad], IRON, [(z, y) for z, y in quad], (1, 0, 0))
        add_face(f, [(-0.02, y, z) for z, y in quad], IRON, [(z, y) for z, y in quad], (-1, 0, 0))
    # A scroll at the arm's end, the lantern below it.
    geom.box(f, (-0.022, -0.03, arm + 0.03), (0.022, 0.09, arm + 0.07), IRON, skip=("-z",))
    low = _lantern_body(f, (0.0, arm), -0.03)
    p.meta["drop"] = -low
    return p


def lantern_hanging(drop: float = 1.5) -> Piece:
    """`kit_lantern_hanging`: a lantern on a chain `drop` metres below its
    hook at y = 0 (a ceiling or a vault boss)."""
    p = Piece("")
    f = p.faces
    # Links of 0.12 m, longer ones on long chains (at most 24 links: a lantern
    # hung 12 m from a vault stays within its triangle budget).
    links = max(1, min(int(drop / 0.12), 24))
    for i in range(links):
        y0 = -i * drop / links
        y1 = y0 - drop / links * 0.85
        if i % 2 == 0:
            geom.box(f, (-0.03, y1, -0.006), (0.03, y0, 0.006), IRON, skip=("+y", "-y"))
        else:
            geom.box(f, (-0.006, y1, -0.03), (0.006, y0, 0.03), IRON, skip=("+y", "-y"))
    low = _lantern_body(f, (0.0, 0.0), -drop)
    p.meta["drop"] = -low
    p.walkthrough = True
    return p


def banner(tile: str, width: float = 1.6, height: float = 6.0, cut: float = 0.8, clips: bool = True) -> Piece:
    """`kit_banner_<slot>`: a swallowtail banner `width` x `height` hanging
    from a gilt rod at y = 0, 0.08 m off the wall (z), cut in geometry (no
    alpha). Double-sided `castle_cloth`. Without `clips` (the
    floating cup banners) the rod has no brackets back to a wall."""
    p = Piece("")
    f = p.faces
    hw = width / 2
    z = 0.08
    outline = [(-hw, -height), (0.0, -height + cut), (hw, -height), (hw, 0.0), (-hw, 0.0)]
    uv = geom.bbox_uv(outline)
    add_face(f, [(x, y, z) for x, y in outline], f"castle_cloth|fit:{tile}", uv, (0.0, 0.0, 1.0))
    # The rod and its finials.
    geom.box(f, (-hw - 0.12, -0.02, z - 0.03), (hw + 0.12, 0.04, z + 0.03), GILT, skip=())
    for sx in (-1, 1):
        geom.box(f, (sx * (hw + 0.12) - 0.05, -0.05, z - 0.05), (sx * (hw + 0.12) + 0.05, 0.07, z + 0.05), GILT)
        # (Its top 5 mm under the rod's, so the two never share a plane; castle kit M3.)
        if clips:
            geom.box(f, (sx * (hw + 0.06) - 0.02, 0.0, 0.0), (sx * (hw + 0.06) + 0.02, 0.035, z), GILT, skip=("-z",))
    p.walkthrough = True
    return p


def cup_banners(slot: str, count: int = 8, pitch: float = 7.5, rows: float = 8.2) -> Piece:
    """`kit_banner_cup_<slot>` (docs/art/castle-kit.md): one
    house's House Cup banners, `kit_banner_<slot>`'s painted banner at
    grid.CUP_BANNER_SIZE, floating (no wall brackets) with the rod's
    finials' tops at y = 0: `count` along local x every `pitch` (centred on the origin) in two
    rows at z = -rows and +rows (over the long house tables), each facing
    -x (the hall's doors when placed with yaw 0) and seen from both sides.
    The faces go into the group grid.CUP_BANNER_GROUP (schema), shown only
    when the house won. At most grid.CUP_BANNER_TRIS triangles."""
    w, h = G.CUP_BANNER_SIZE
    one = banner(f"banner_house_{slot}", w, h, G.CUP_BANNER_CUT, clips=False)
    p = Piece("", walkthrough=True)
    n = int(round(count))
    for i in range(n):
        x = (i - (n - 1) / 2.0) * pitch
        for z in (-rows, rows):
            pl = geom.Placement((x, -0.07, z), -math.pi / 2)  # the finials' tops at y = 0
            for kf in one.faces:
                p.faces.append(geom.KFace([pl.point(q) for q in kf.pts], kf.tag, kf.uv, kf.anchor))
    p.meta.update(count=n, pitch=pitch, rows=rows, height=h + 0.05, width=w)
    return p


def newel(kind: str = "ball", height: float = 1.3, standard: float = 0.0) -> Piece:
    """`kit_newel_ball` / `kit_newel_lamp`: a stone post 0.45 m square,
    `height` to its cap, with a ball finial or a glowing lamp globe. A lamp
    with `standard` > 0 (castle kit M2 review: the Marble Staircase's lamp
    standards) carries an iron post `standard` metres tall with a
    lantern on it instead of the globe; keep the lantern's foot (`height` +
    `standard`) at least 2.4 m above the stair (the overhead rule)."""
    p = Piece("")
    f = p.faces
    h = 0.225
    band = trim("capital_band")
    # Its underside is drawn: a newel on a stair's edge overhangs the drop (castle kit M2).
    geom.box(f, (-h, 0.0, -h), (h, 0.25, h), band, skip=(),
             uv_fn=lambda side, pts: [(pt[0] + pt[2], pt[1] / 0.25 * 0.3) for pt in pts])
    s = 0.18
    geom.box(f, (-s, 0.25, -s), (s, height - 0.16, s), trim("shaft"), skip=("-y", "+y"),
             uv_fn=lambda side, pts: [(pt[1], (pt[0] + pt[2] + 2 * s) / (4 * s) * 0.7) for pt in pts])
    c = 0.24
    geom.box(f, (-c, height - 0.16, -c), (c, height, c), band, skip=("+y",),
             uv_fn=lambda side, pts: [(pt[0] + pt[2], (pt[1] - height + 0.16) / 0.16 * 0.3) for pt in pts])
    add_face(f, [(-c, height, c), (c, height, c), (c, height, -c), (-c, height, -c)], fit("newel_cap"),
             [(0, 0), (1, 0), (1, 1), (0, 1)], (0, 1, 0))
    # Castle kit M4 (M2 leftover 3): a coarse camera shell round the post.
    c2 = 0.24
    p.meta["collider"] = [
        [(-c2, 0.0, c2), (c2, 0.0, c2), (c2, height, c2), (-c2, height, c2)],
        [(c2, 0.0, -c2), (-c2, 0.0, -c2), (-c2, height, -c2), (c2, height, -c2)],
        [(c2, 0.0, c2), (c2, 0.0, -c2), (c2, height, -c2), (c2, height, c2)],
        [(-c2, 0.0, -c2), (-c2, 0.0, c2), (-c2, height, c2), (-c2, height, -c2)],
        [(-c2, height, c2), (c2, height, c2), (c2, height, -c2), (-c2, height, -c2)],
    ]
    if kind == "lamp" and standard > 0:
        # Iron collar, post and a lantern sitting on its top.
        _frustum(f, (0.0, 0.0), 0.12, height, 0.06, height + 0.12, IRON)
        _frustum(f, (0.0, 0.0), 0.04, height + 0.12, 0.04, height + standard, IRON)
        _frustum(f, (0.0, 0.0), 0.04, height + standard, 0.1, height + standard + 0.08, IRON)
        _lantern_body(f, (0.0, 0.0), height + standard + 0.55)
        _frustum(f, (0.0, 0.0), 0.05, height + standard + 0.55, 0.0, height + standard + 0.72, IRON)
        p.meta["height"] = height
        p.meta["standard"] = standard
        return p
    if kind == "ball":
        rings = [(0.06, 0.0), (0.06, 0.05), (0.13, 0.1), (0.16, 0.19), (0.13, 0.28), (0.06, 0.33), (0.0, 0.35)]
        tag = fit("finial")
    else:
        rings = [(0.08, 0.0), (0.08, 0.06), (0.14, 0.12), (0.17, 0.24), (0.14, 0.36), (0.07, 0.42), (0.0, 0.44)]
        tag = GLOW
        _frustum(f, (0.0, 0.0), 0.1, height, 0.1, height + 0.06, IRON)
    for (r0, y0), (r1, y1) in zip(rings, rings[1:]):
        _frustum(f, (0.0, 0.0), r0, height + y0, r1, height + y1, tag if tag == GLOW else tag, n=8)
    if tag != GLOW:
        # Fit-tile UVs for the ball: replace the metre UVs by 0..1.
        for face in f[-8 * (len(rings) - 1):]:
            face.uv = [(min(1.0, max(0.0, a / 0.2)), min(1.0, max(0.0, b / 0.15))) for a, b in face.uv]
    p.meta["height"] = height
    return p
