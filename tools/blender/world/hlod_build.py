"""The far castle silhouette's geometry (docs/art/castle-kit.md
"Exterior"). Pure Python (no `bpy`): `world/hlod_export.py` turns the meshes into
Blender objects and writes `hlod.glb`; the lint and the tests read them here.

    from hlod_build import build
    chunks = build(layout, exterior, kit_plan)   # chunk id -> material -> assemble.MeshData

Every chunk of `data/world/exterior.json` is one mesh per material, named
`hlod.<chunk>.<material>` (grid.hlod_node), on the two shared library
materials only: `castle_wall_c@ext` (every outside wall: the
Grand Staircase's pale cool west-range stone) and `castle_roof` (slate).
World-mapped UVs (grid.world_uv at the rows' 4.8 m tiles, as the kit's
outside faces), no texture of its own. COLOR_0 is greyscale: the stone a
little darker at its foot, the window bands' panes dark (no geometry
beyond the face's split), soffits in shade.

- **Stand-in chunks** (with `cells`): each cell's outer walls are the
  Planner's `wall_exterior` faces (the outer shell the kit draws on the
  outside), its flat tops the Planner's `roof` slabs; an outdoor cell (the
  courtyard) adds its walls' inner faces, its gatehouse's towers and (Fixes
  round 1) its paving (`OUTDOOR_FLOORS`). Every
  face sits grid.P3A_HLOD_INSET (0.3 m) inside the real one (walls moved in
  along their normal, tops down), so a partly loaded chunk shows behind the
  real cells, never in front. A cell whose `facade` has `roof` (the
  Entrance Hall, the Great Hall) gets its pitched slate roof, from the same
  frame the facade rule uses (rules._facade_roof), just inside the real
  roof's underside.
- **Exterior-only chunks** (no `cells`): the `masses`, boxes and 12-sided
  drums from `base` to `top` with their roofs (`cone`, `pyramid`,
  `pitched` along `ridge`, `flat` with a 1.5 m crenellated parapet, `none`
  a flat top: a mass up to a playable cell's floor) and
  window bands; caps (`over`) stand on their cell's highest roof (the
  layout's struct top): a roof, or with `rise` a turret's walls first.

The skyline (docs/architecture.md):

- **Lit windows by night:** about `LIT_SHARE` of the panes (chosen by a hash
  of the pane's place, so the same every export) carry `SHADE_LIT` instead
  of `SHADE_PANE` in COLOR_0: a hair darker by day, and the client's
  `hlod_stone` glows them the palette's `window_night` by night
  (client/src/game/castle/hlod.ts). Still greyscale.
- **Steep roofs** (`exterior.json` `steep_roofs`): a cell's facade roof
  redrawn over the real one from its eaves (`STEEP_GROW` outside them) at a
  steeper `pitch`, in an exterior-only chunk so it shows whether or not the
  cell is loaded: the Great Hall's long steep roof reads from the lake. The
  real roof (and the stand-in's, under it) stays inside it.
- **The crag** (`exterior.json` `crag`): its chunk draws the rock faces under
  the outer walls (world/hlod_crag.py) on one material of its own,
  `crag.material` (`backdrop_crag`: the backdrop's toon row, the palette's
  `crag_rock`), so it is the one chunk not on the two library materials.
"""

from __future__ import annotations

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_BLENDER, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import layout_lib as L  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit.assemble import MeshData  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402

STONE = G.ext_name(G.P3A_EXTERIOR_STONE)  # castle_wall_c@ext
ROOF = "castle_roof"
MATERIALS = (STONE, ROOF)
CRAG = "backdrop_crag"  # The crag chunk's rock (exterior.json `crag.material`)
TERRAIN_DIR = os.path.join(os.path.dirname(_BLENDER), "..", "data", "world", "terrain")
TILE_M = 4.8  # castle_wall_c's and castle_roof's tile (castle_textures.json)

# Window bands (low detail: dark panes in COLOR_0 on the wall face's split).
STOREY = 9.0  # the castle's storeys (floors 9 m apart), counted from y = 0
BAND = (3.0, 6.6)  # a storey's window band, above the storey's floor
WINDOW_W = 1.3  # a pane's width
WINDOW_PITCH = 4.6  # pane centres along a wall
WINDOW_MARGIN = 1.2  # a pane keeps this clear of a face's ends
BAND_CLEAR = (1.4, 1.0)  # a band keeps this clear of a face's foot and top

# COLOR_0 (greyscale, r = g = b).
SHADE_FOOT, SHADE_TOP, SHADE_RISE = 0.80, 1.0, 30.0  # stone: darker at the foot, full SHADE_RISE m up
SHADE_PANE = 0.30
SHADE_LIT = 0.22  # A pane lit by night (client castle/hlod.ts LIT_PANE_BELOW sits between the two)
LIT_SHARE = 0.42  # The share of panes lit by night
SHADE_SOFFIT = 0.62
SHADE_ROOF = (0.86, 1.0)  # eave, ridge

DRUM_SIDES = 12
CONE_SIDES_BOX = 8  # a cone over a box: an octagonal spire inscribed in it
EAVE = 0.5  # a spire's eave beyond its drum or box
PARAPET = {"height": 1.5, "thick": 0.5, "merlon": 1.0, "gap": 0.8, "merlon_h": 0.7}


def vec_y(pts):
    return [p[1] for p in pts]


class Acc:
    """One mesh being built: faces with their own corners (flat shading)."""

    def __init__(self, name: str, material: str):
        self.md = MeshData(name, material, [], [], [], [])

    def add(self, pts, shade, normal=None) -> None:
        """A planar polygon; `shade` one grey for every corner or one per
        corner; `normal` (outward) turns the winding to face it."""
        pts = [tuple(float(c) for c in p) for p in pts]
        shades = [shade] * len(pts) if isinstance(shade, (int, float)) else list(shade)
        n = geom.newell(pts)
        if geom.norm(n) < 1e-9:
            return
        if normal is not None and geom.dot(n, normal) < 0:
            pts.reverse()
            shades.reverse()
            n = geom.newell(pts)
        n = geom.unit(n)
        base = len(self.md.verts)
        self.md.verts.extend(pts)
        self.md.faces.append(tuple(range(base, base + len(pts))))
        self.md.uvs.append([G.world_uv(p, n, TILE_M) for p in pts])
        self.md.colors.append([(round(s, 4),) * 3 for s in shades])


def pane_lit(x: float, y: float, z: float) -> bool:
    """Whether the pane centred at (x, y, z) is lit by night (deterministic)."""
    i, j, k = int(round(x * 2.0)), int(round(y * 2.0)), int(round(z * 2.0))
    h = (i * 73856093) ^ (j * 19349663) ^ (k * 83492791)
    h &= 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 1274126177) & 0xFFFFFFFF
    return (h >> 8) / float(1 << 24) < LIT_SHARE


def stone_shade(y: float, foot: float) -> float:
    t = min(1.0, max(0.0, (y - foot) / SHADE_RISE))
    return SHADE_FOOT + (SHADE_TOP - SHADE_FOOT) * t


# -- walls with window bands -------------------------------------------------------------------


def window_columns(length: float) -> list[tuple[float, float]]:
    """Panes (t0, t1) along a face `length` long, evenly spaced, centred."""
    room = length - 2.0 * WINDOW_MARGIN
    if room < WINDOW_W:
        return []
    n = max(1, int((room - WINDOW_W) / WINDOW_PITCH) + 1)
    span = (n - 1) * WINDOW_PITCH
    start = (length - span) / 2.0
    return [(start + i * WINDOW_PITCH - WINDOW_W / 2.0, start + i * WINDOW_PITCH + WINDOW_W / 2.0) for i in range(n)]


def window_bands(y0: float, y1: float) -> list[tuple[float, float]]:
    """The storeys' window bands (b0, b1) that fit between y0 and y1."""
    out = []
    k = math.floor(y0 / STOREY) - 1
    while k * STOREY < y1:
        b0, b1 = k * STOREY + BAND[0], k * STOREY + BAND[1]
        if b0 >= y0 + BAND_CLEAR[0] - 1e-6 and b1 <= y1 - BAND_CLEAR[1] + 1e-6:
            out.append((b0, b1))
        k += 1
    return out


def wall(acc: Acc, a, b, y0: float, y1: float, normal, windows: bool, foot: float, cols=None) -> None:
    """A vertical wall face over the bottom edge a -> b ((x, z) points),
    from y0 to y1, facing `normal`; with `windows`, split into rows at the
    window bands and the bands into panes and piers (the panes dark)."""
    if y1 - y0 < 1e-6:
        return
    ax, az = a
    bx, bz = b
    length = math.hypot(bx - ax, bz - az)
    if length < 1e-6:
        return

    def pt(t, y):
        s = t / length
        return (ax + (bx - ax) * s, y, az + (bz - az) * s)

    def quad(t0, t1, ya, yb, shade=None):
        pts = [pt(t0, ya), pt(t1, ya), pt(t1, yb), pt(t0, yb)]
        sh = shade if shade is not None else [stone_shade(p[1], foot) for p in pts]
        acc.add(pts, sh, normal)

    bands = window_bands(y0, y1) if windows else []
    panes = (cols if cols is not None else window_columns(length)) if bands else []
    if not panes:
        quad(0.0, length, y0, y1)
        return
    y = y0
    for b0, b1 in bands:
        quad(0.0, length, y, b0)
        t = 0.0
        for t0, t1 in panes:
            quad(t, t0, b0, b1)
            c = pt((t0 + t1) / 2.0, (b0 + b1) / 2.0)
            quad(t0, t1, b0, b1, SHADE_LIT if pane_lit(*c) else SHADE_PANE)
            t = t1
        quad(t, length, b0, b1)
        y = b1
    quad(0.0, length, y, y1)


# -- outlines, prisms and roofs -----------------------------------------------------------------


def box_outline(x0: float, z0: float, x1: float, z1: float) -> list[tuple[float, float]]:
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def drum_outline(cx: float, cz: float, r: float, n: int = DRUM_SIDES, phase: float = None) -> list[tuple[float, float]]:
    """A regular n-gon round (cx, cz) with its faces' midpoints at r (a flat
    face toward +z, the lake, for n = 12)."""
    phase = math.pi / n if phase is None else phase
    rv = r / math.cos(math.pi / n)  # the vertices' radius
    return [(cx + rv * math.sin(phase + 2 * math.pi * i / n), cz + rv * math.cos(phase + 2 * math.pi * i / n))
            for i in range(n)]


def centroid(outline):
    return (sum(p[0] for p in outline) / len(outline), sum(p[1] for p in outline) / len(outline))


def edges_out(outline):
    """(a, b, outward normal) of each edge of a convex outline."""
    cx, cz = centroid(outline)
    out = []
    for i, a in enumerate(outline):
        b = outline[(i + 1) % len(outline)]
        dx, dz = b[0] - a[0], b[1] - a[1]
        n = (dz, 0.0, -dx)
        mx, mz = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        if n[0] * (mx - cx) + n[2] * (mz - cz) < 0:
            n = (-n[0], 0.0, -n[2])
        out.append((a, b, geom.unit(n)))
    return out


def grow(outline, d: float):
    """A convex outline moved out by d (its edges parallel)."""
    cx, cz = centroid(outline)
    if len(outline) == 4 and all(abs(outline[i][0] - outline[(i + 1) % 4][0]) < 1e-9 or
                                 abs(outline[i][1] - outline[(i + 1) % 4][1]) < 1e-9 for i in range(4)):
        xs, zs = [p[0] for p in outline], [p[1] for p in outline]
        return box_outline(min(xs) - d, min(zs) - d, max(xs) + d, max(zs) + d)
    n = len(outline)
    rv = math.hypot(outline[0][0] - cx, outline[0][1] - cz)
    k = (rv * math.cos(math.pi / n) + d) / (rv * math.cos(math.pi / n))
    return [(cx + (p[0] - cx) * k, cz + (p[1] - cz) * k) for p in outline]


def prism_walls(acc: Acc, outline, y0: float, y1: float, windows: bool, foot: float) -> None:
    round_ = len(outline) > 4
    for a, b, n in edges_out(outline):
        cols = None
        if round_:
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            cols = [((length - 1.0) / 2.0, (length + 1.0) / 2.0)] if length >= 2.2 else []
        wall(acc, a, b, y0, y1, n, windows, foot, cols)


def cap_top(acc: Acc, outline, y: float, foot: float) -> None:
    acc.add([(p[0], y, p[1]) for p in outline], stone_shade(y, foot), (0.0, 1.0, 0.0))


def soffit(acc: Acc, inner, outer, y: float) -> None:
    """The underside ring between two outlines (same vertex count) at y."""
    n = len(inner)
    for i in range(n):
        j = (i + 1) % n
        acc.add([(inner[i][0], y, inner[i][1]), (inner[j][0], y, inner[j][1]), (outer[j][0], y, outer[j][1]),
                 (outer[i][0], y, outer[i][1])], SHADE_SOFFIT, (0.0, -1.0, 0.0))


def cone(roof: Acc, ring, y: float, apex_y: float) -> None:
    """A spire from the ring at y to an apex over its centre."""
    cx, cz = centroid(ring)
    apex = (cx, apex_y, cz)
    for a, b, n in edges_out(ring):
        out = (n[0], 0.6, n[2])
        roof.add([(a[0], y, a[1]), (b[0], y, b[1]), apex], [SHADE_ROOF[0], SHADE_ROOF[0], SHADE_ROOF[1]], out)


def parapet(stone: Acc, outline, top: float, foot: float) -> None:
    """A crenellated parapet on a flat top: the walls run on PARAPET height
    over it, an inner face, a coping and merlons on the outer edge."""
    p = PARAPET
    h, t = p["height"] - p["merlon_h"], p["thick"]
    inner = grow(outline, -t)
    for a, b, n in edges_out(outline):
        wall(stone, a, b, top, top + h, n, False, foot)
    for a, b, n in edges_out(inner):
        wall(stone, a, b, top, top + h, (-n[0], 0.0, -n[2]), False, foot)
    # The coping ring.
    for (a, b, _), (ia, ib, _) in zip(edges_out(outline), edges_out(inner)):
        stone.add([(a[0], top + h, a[1]), (b[0], top + h, b[1]), (ib[0], top + h, ib[1]), (ia[0], top + h, ia[1])],
                  stone_shade(top + h, foot), (0.0, 1.0, 0.0))
    cap_top(stone, inner, top, foot)
    # Merlons along each outer edge.
    y0, y1 = top + h, top + p["height"]
    for (a, b, n), (ia, ib, _) in zip(edges_out(outline), edges_out(inner)):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(round((length + p["gap"]) / (p["merlon"] + p["gap"]))))
        mw = (length - (k - 1) * p["gap"]) / k if k > 1 else min(length, p["merlon"])
        if mw <= 0.2:
            continue
        ux, uz = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        for i in range(k):
            s0 = i * (mw + p["gap"]) if k > 1 else (length - mw) / 2.0
            s1 = s0 + mw
            # The merlon's footprint: the outer edge from s0 to s1, t deep inward.
            o0 = (a[0] + ux * s0, a[1] + uz * s0)
            o1 = (a[0] + ux * s1, a[1] + uz * s1)
            i0 = (o0[0] - n[0] * t, o0[1] - n[2] * t)
            i1 = (o1[0] - n[0] * t, o1[1] - n[2] * t)
            sh = stone_shade(y1, foot)
            stone.add([(o0[0], y0, o0[1]), (o1[0], y0, o1[1]), (o1[0], y1, o1[1]), (o0[0], y1, o0[1])], sh, n)
            stone.add([(i0[0], y0, i0[1]), (i1[0], y0, i1[1]), (i1[0], y1, i1[1]), (i0[0], y1, i0[1])], sh,
                      (-n[0], 0.0, -n[2]))
            stone.add([(o0[0], y0, o0[1]), (i0[0], y0, i0[1]), (i0[0], y1, i0[1]), (o0[0], y1, o0[1])], sh,
                      (-ux, 0.0, -uz))
            stone.add([(o1[0], y0, o1[1]), (i1[0], y0, i1[1]), (i1[0], y1, i1[1]), (o1[0], y1, o1[1])], sh, (ux, 0.0, uz))
            stone.add([(o0[0], y1, o0[1]), (o1[0], y1, o1[1]), (i1[0], y1, i1[1]), (i0[0], y1, i0[1])], sh, (0.0, 1.0, 0.0))


def pitched(stone: Acc, roof: Acc, x0: float, z0: float, x1: float, z1: float, eave_y: float, ridge_y: float,
            along_x: bool, foot: float, gables: bool = True) -> None:
    """A pitched roof over the rectangle, the ridge along x (`along_x`) or z,
    the eaves at eave_y on the long sides, stone gables at the ends."""
    if along_x:
        zm = (z0 + z1) / 2.0
        for ze, sgn in ((z0, -1.0), (z1, 1.0)):
            roof.add([(x0, eave_y, ze), (x1, eave_y, ze), (x1, ridge_y, zm), (x0, ridge_y, zm)],
                     [SHADE_ROOF[0], SHADE_ROOF[0], SHADE_ROOF[1], SHADE_ROOF[1]], (0.0, 0.6, sgn))
        if gables:
            for x, sgn in ((x0, -1.0), (x1, 1.0)):
                stone.add([(x, eave_y, z0), (x, eave_y, z1), (x, ridge_y, zm)], stone_shade(ridge_y, foot), (sgn, 0.0, 0.0))
    else:
        xm = (x0 + x1) / 2.0
        for xe, sgn in ((x0, -1.0), (x1, 1.0)):
            roof.add([(xe, eave_y, z0), (xe, eave_y, z1), (xm, ridge_y, z1), (xm, ridge_y, z0)],
                     [SHADE_ROOF[0], SHADE_ROOF[0], SHADE_ROOF[1], SHADE_ROOF[1]], (sgn, 0.6, 0.0))
        if gables:
            for z, sgn in ((z0, -1.0), (z1, 1.0)):
                stone.add([(x0, eave_y, z), (x1, eave_y, z), (xm, ridge_y, z)], stone_shade(ridge_y, foot), (0.0, 0.0, sgn))


# -- the masses (exterior-only chunks) ----------------------------------------------------------


def cell_roof(layout: L.Layout, cell: str) -> float:
    """A cell's highest roof (its surfaces' struct top)."""
    return max(s.struct_top() for s in layout.surfaces if s.cell == cell)


def mass(stone: Acc, roof: Acc, m: dict, layout: L.Layout) -> None:
    """One `masses` entry: its walls (none for a cap), its roof."""
    if m["shape"] == "round":
        (cx, cz), r = m["centre"], m["radius"]
        outline = drum_outline(cx, cz, r)
    else:
        (x0, z0), (x1, z1) = m["min"], m["max"]
        outline = box_outline(x0, z0, x1, z1)
    if "over" in m:
        # A cap: on its cell's highest roof, `rise` m of walls first (a turret on the roof) or none.
        base = cell_roof(layout, m["over"])
        top = base + m.get("rise", 0.0)
        foot = base
    else:
        base, top = m["base"], m["top"]
        foot = base
    walled = top - base > 1e-6
    if walled:
        prism_walls(stone, outline, base, top, bool(m.get("windows")), foot)
    kind = m["roof"]
    rh = m.get("roof_height", 0.0)
    if kind == "none":
        # A flat top and no parapet (a mass up to a playable cell's floor).
        if walled:
            cap_top(stone, outline, top, foot)
    elif kind == "flat":
        parapet(stone, outline, top, foot)
    elif kind == "cone":
        if m["shape"] == "round":
            ring = grow(outline, EAVE) if walled else outline
        else:
            (x0, z0), (x1, z1) = m["min"], m["max"]
            r = min(x1 - x0, z1 - z0) / 2.0
            ring = drum_outline((x0 + x1) / 2.0, (z0 + z1) / 2.0, r + (EAVE if walled else 0.0),
                                CONE_SIDES_BOX, math.pi / CONE_SIDES_BOX)
            if walled:
                cap_top(stone, outline, top, foot)
        if walled and m["shape"] == "round":
            soffit(roof, outline, ring, top)
        cone(roof, ring, top, top + rh)
    elif kind == "pyramid":
        ring = grow(outline, EAVE) if walled else outline
        if walled:
            soffit(roof, outline, ring, top)
        cone(roof, ring, top, top + rh)
    elif kind == "pitched":
        (x0, z0), (x1, z1) = m["min"], m["max"]
        pitched(stone, roof, x0, z0, x1, z1, top, top + rh, m["ridge"] == "x", foot)
    else:
        raise ValueError(f"{m['id']}: unknown roof {kind!r}")


# -- the stand-ins (chunks with cells) ----------------------------------------------------------

OUTDOOR_FLOORS = ["floor_courtyard"]  # The Planner's paving an outdoor stand-in keeps


_PLAN: dict = {}


def layout_plan(layout: L.Layout):
    """The Planner's greybox of the layout without props (its outer shell and
    roof slabs), built once per layout."""
    got = _PLAN.get("plan")
    if got is None or got[0] is not layout:
        got = (layout, L.plan_layout(layout))
        _PLAN["plan"] = got
    return got[1]


def _inset(poly, d: float):
    n = geom.unit(geom.newell(poly))
    if abs(n[1]) > 0.5:
        return [(p[0], p[1] - d, p[2]) for p in poly], n
    return [(p[0] - n[0] * d, p[1], p[2] - n[2] * d) for p in poly], n


def _vertical_rect(poly, n):
    """(a, b, y0, y1) of an axis-aligned vertical rectangle facing n (a -> b
    its bottom edge in (x, z)), or None."""
    if len(poly) != 4 or abs(n[1]) > 1e-6:
        return None
    ys = sorted({round(p[1], 6) for p in poly})
    if len(ys) != 2:
        return None
    lo = [p for p in poly if abs(p[1] - ys[0]) < 1e-6]
    if len(lo) != 2:
        return None
    return (lo[0][0], lo[0][2]), (lo[1][0], lo[1][2]), ys[0], ys[1]


def facade_roof_frame(layout: L.Layout, kit_plan, cell: str):
    """The pitched slate roof a cell's facade asks for (`roof`): its outer
    rectangle (x0, z0, x1, z1), eave height and ridge axis, from the facade
    rule's own frame; None for a cell without one."""
    cp = getattr(kit_plan, "cells", {}).get(cell) if kit_plan is not None else None
    if cp is None or cp.facade is None or not cp.facade.roof:
        return None
    from castle_kit import pieces as P
    from castle_kit import rules as R

    got = R._facade_roof(R.context(layout, cp))
    if got is None:
        return None
    use, frame = got
    _, span, length, _ = P.parse(use.piece)
    if frame["along_x"]:
        x0 = use.origin[0]
        rect = (x0, frame["ridge"] - span / 2.0, x0 + length, frame["ridge"] + span / 2.0)
    else:
        z0 = use.origin[2]
        rect = (frame["ridge"] - span / 2.0, z0, frame["ridge"] + span / 2.0, z0 + length)
    return {"rect": rect, "top": frame["top"], "along_x": frame["along_x"], "pitch": use.kwargs()["pitch"]}


def gatehouse_boxes(kit_plan, cell: str, d: float) -> list[tuple]:
    """Stand-in boxes ((x0, z0, x1, z1), y0, y1) of the gatehouse heroes of
    an outdoor cell: each tower's body and its part over the wall, the
    gallery over the gate, d inside the piece's faces."""
    cp = getattr(kit_plan, "cells", {}).get(cell) if kit_plan is not None else None
    if cp is None:
        return []
    from castle_kit.pieces import courtyard as CY

    out = []
    for h in cp.hero:
        if not h.piece.startswith(G.GATEHOUSE):
            continue
        top = dict(h.args).get("top", 23.0)
        pl = geom.Placement(h.pos, h.yaw)
        wall_top = G.COURT_WALL_TOP
        local = []
        for side in (-1.0, 1.0):
            xa, xb = sorted((side * CY.GATE_HALF, side * (CY.GATE_HALF + CY.TOWER_W)))
            # The body: from the floor to its parapet's walk (the merlons, 1.3 m, left
            # out), in front of the wall face (the courtyard side) ...
            local.append(((xa + d, d, xb - d, CY.TOWER_D - d), 0.0, top - 1.3 - d))
            # ... and over the wall, from the wall top back to its overhang.
            local.append(((xa + d, -CY.TOWER_BACK + d, xb - d, d), wall_top, top - 1.3 - d))
        local.append(((-CY.GATE_HALF, -CY.TOWER_BACK + d, CY.GATE_HALF, CY.GALLERY_D - d), wall_top, CY.GALLERY_TOP - d))
        for (lx0, lz0, lx1, lz1), y0, y1 in local:
            pts = [pl.point((x, 0.0, z)) for x, z in ((lx0, lz0), (lx1, lz1))]
            xs, zs = [p[0] for p in pts], [p[2] for p in pts]
            out.append(((min(xs), min(zs), max(xs), max(zs)), pl.point((0.0, y0, 0.0))[1], pl.point((0.0, y1, 0.0))[1]))
    return out


def stand_in(stone: Acc, roof: Acc, layout: L.Layout, kit_plan, cell: str) -> None:
    d = G.P3A_HLOD_INSET
    plan = layout_plan(layout)
    frame = facade_roof_frame(layout, kit_plan, cell)
    outdoor = any(s.kind in G.OUTDOOR_KINDS for s in layout.surfaces if s.cell == cell)
    mats = ["wall_exterior", "roof"] + (["wall"] if outdoor else [])
    # An outdoor cell's paving too (through the courtyard's gate the far view
    # showed the sky under the castle where the courtyard's floor was missing).
    mats += OUTDOOR_FLOORS if outdoor else []
    # An outdoor cell's inner faces only within its outer shell's box (the
    # Clock Tower Courtyard's door passage runs on through the Entrance Courtyard's wall).
    shell = [p for poly in plan.polys.get((cell, "wall_exterior"), []) for p in poly]
    box = ([min(p[k] for p in shell) for k in (0, 2)], [max(p[k] for p in shell) for k in (0, 2)]) if shell else None
    for mat in mats:
        for poly in plan.polys.get((cell, mat), []):
            if mat == "roof" and frame is not None:
                continue  # under the pitched roof
            if mat == "wall" and box is not None and not all(
                    box[0][0] - 1e-6 <= p[0] <= box[1][0] + 1e-6 and box[0][1] - 1e-6 <= p[2] <= box[1][1] + 1e-6 for p in poly):
                continue
            pts, n = _inset(poly, d)
            rect = _vertical_rect(pts, n)
            if rect is not None:
                a, b, y0, y1 = rect
                wall(stone, a, b, y0, y1, n, mat == "wall_exterior" and not outdoor, 0.0)
            else:
                stone.add(pts, [stone_shade(p[1], 0.0) for p in pts], n)
    if frame is not None:
        x0, z0, x1, z1 = frame["rect"]
        tan = math.tan(math.radians(frame["pitch"]))
        if frame["along_x"]:
            hw = (z1 - z0) / 2.0 - d
            rx = (x0 + d, z0 + d, x1 - d, z1 - d)
        else:
            hw = (x1 - x0) / 2.0 - d
            rx = (x0 + d, z0 + d, x1 - d, z1 - d)
        pitched(stone, roof, *rx, frame["top"], frame["top"] + hw * tan, frame["along_x"], 0.0)
    for (bx0, bz0, bx1, bz1), y0, y1 in gatehouse_boxes(kit_plan, cell, d):
        outline = box_outline(bx0, bz0, bx1, bz1)
        prism_walls(stone, outline, y0, y1, False, 0.0)
        cap_top(stone, outline, y1, 0.0)
    shell_stand_in_p3c(stone, roof, kit_plan, cell, d)  # A shell the kit draws (the Owl Loft)


# A cell whose outer shell is a kit hero, not
# the Planner's (the Owl Loft: its walls are the layout's solids, so the Planner draws no outer
# wall or roof): its stand-in is that shell's outer box and spire from the hero's meta, d inside.
SHELL_STAND_INS_P3C = ("kit_owl_loft_shell",)


def shell_stand_in_p3c(stone: Acc, roof: Acc, kit_plan, cell: str, d: float) -> None:
    cp = getattr(kit_plan, "cells", {}).get(cell) if kit_plan is not None else None
    if cp is None:
        return
    from castle_kit import pieces as P

    for h in cp.hero:
        if h.piece not in SHELL_STAND_INS_P3C:
            continue
        meta = P.build(h.piece, **dict(h.args)).meta
        cx, _, cz = h.pos
        r = meta["outside"] - d
        top = meta["eave"] - d
        outline = box_outline(cx - r, cz - r, cx + r, cz + r)
        prism_walls(stone, outline, meta["base"], top, False, meta["base"])
        # The spire at the real one's pitch, its eaves pulled in to the walls: inside it everywhere.
        reach = meta["outside"] + meta["eave_out"]
        cone(roof, outline, top, top + (meta["apex"] - meta["eave"]) * r / reach)


# -- the whole silhouette -----------------------------------------------------------------------


STEEP_GROW = 0.15  # a steep roof's eaves and verges beyond the real roof's
STEEP_LIFT = 0.08  # ... and its eaves over the real ones


def steep_roof(stone: Acc, roof: Acc, layout: L.Layout, kit_plan, spec: dict) -> None:
    """`steep_roofs` entry `spec`: the cell's facade roof at `pitch` degrees
    from its own eaves (see the top of the file)."""
    frame = facade_roof_frame(layout, kit_plan, spec["cell"])
    if frame is None:
        return
    x0, z0, x1, z1 = frame["rect"]
    g = STEEP_GROW
    x0, z0, x1, z1 = x0 - g, z0 - g, x1 + g, z1 + g
    hw = ((z1 - z0) if frame["along_x"] else (x1 - x0)) / 2.0
    eave = frame["top"] + STEEP_LIFT
    pitched(stone, roof, x0, z0, x1, z1, eave, eave + hw * math.tan(math.radians(spec["pitch"])), frame["along_x"], 0.0)


def crag(acc: Acc, spec: dict, terrain_dir: str = TERRAIN_DIR) -> None:
    """The crag's rock faces (world/hlod_crag.py) into `acc`."""
    import hlod_crag as HC

    ground, shelf = HC.backdrop_ground(os.path.abspath(terrain_dir))
    for pts, shade, hint in HC.triangles(spec["runs"], ground, shelf):
        acc.add(pts, shade, hint)


def build(layout: L.Layout, exterior: dict, kit_plan=None) -> dict[str, dict[str, MeshData]]:
    """chunk id -> material -> MeshData (empty meshes left out), in
    exterior.json's chunk order."""
    out: dict[str, dict[str, MeshData]] = {}
    spec = exterior.get("crag")
    for ch in exterior["chunks"]:
        cid = ch["id"]
        stone, roof = Acc(G.hlod_node(cid, STONE), STONE), Acc(G.hlod_node(cid, ROOF), ROOF)
        accs = [stone, roof]
        for cell in ch["cells"]:
            stand_in(stone, roof, layout, kit_plan, cell)
        for m in exterior["masses"]:
            if m["chunk"] == cid:
                mass(stone, roof, m, layout)
        for r in exterior.get("steep_roofs", []):
            if r["chunk"] == cid:
                steep_roof(stone, roof, layout, kit_plan, r)
        if spec and spec["chunk"] == cid:
            rock = Acc(G.hlod_node(cid, spec["material"]), spec["material"])
            crag(rock, spec)
            accs.append(rock)
        out[cid] = {a.md.material: a.md for a in accs if a.md.faces}
    return out


def bounds(meshes) -> tuple[list, list]:
    pts = [v for md in meshes for v in md.verts]
    return ([round(min(p[k] for p in pts), 3) for k in range(3)], [round(max(p[k] for p in pts), 3) for k in range(3)])


def gpu_bytes(meshes) -> int:
    """The GPU memory the meshes take as loaded: per vertex a position, a
    normal (3 floats each), a UV (2 floats) and COLOR_0 (4 bytes); 32-bit
    indices. No texture of its own."""
    verts = sum(len(md.verts) for md in meshes)
    tris = sum(md.triangles() for md in meshes)
    return verts * (12 + 12 + 8 + 4) + tris * 3 * 4


def manifest_entry(chunks: dict, exterior: dict) -> dict:
    """The manifest's `hlod` entry (docs/architecture.md)."""
    rows = []
    for ch in exterior["chunks"]:
        meshes = list(chunks.get(ch["id"], {}).values())
        if not meshes:
            continue
        lo, hi = bounds(meshes)
        rows.append({"id": ch["id"], "name": ch["name"], "cells": list(ch["cells"]), "bounds": {"min": lo, "max": hi},
                     "tris": sum(md.triangles() for md in meshes), "draws": len(meshes),
                     "library": sorted(md.material for md in meshes)})
    return {"file": G.P3A_HLOD_FILE, "tris": sum(r["tris"] for r in rows), "chunks": rows}


def _vertical_face(pts):
    """(outward normal, plane offset, (t0, t1) along the face's horizontal
    tangent, (y0, y1)) of a vertical polygon; None for any other."""
    n = geom.newell(pts)
    if geom.norm(n) < 1e-9:
        return None
    n = geom.unit(n)
    if abs(n[1]) > 1e-3:
        return None
    t = (-n[2], 0.0, n[0])
    ts = [geom.dot(t, p) for p in pts]
    ys = [p[1] for p in pts]
    return n, geom.dot(n, pts[0]), (min(ts), max(ts)), (min(ys), max(ys))


def coplanar_overlaps(chunks: dict, planner, clear: float = G.P3A_HLOD_INSET, min_area: float = 0.05) -> list[tuple]:
    """The silhouette's wall faces that face the same way as a playable cell's
    wall face (the Planner's `wall*` polygons: the faces the kit draws on) and
    lie less than `clear` in front of or behind its plane, overlapping it by
    more than `min_area` m²: wherever both are drawn (an exterior-only chunk
    is drawn whenever the viewer stands outdoors, a stand-in while one of its
    cells is missing) the two fight for depth, and the far castle's pale stone
    flickers through the real wall in blocks as the camera moves (the Clock
    Tower Courtyard's arcade, 2026-11). Each face must sit at least `clear`
    behind (P3A_HLOD_INSET, as the stand-ins do). Rows:
    (chunk, cell, planner material, normal, gap m, overlap m², the wall's plane
    offset along the normal, the overlap's tangent range and its height range),
    largest overlap first."""
    index: dict[tuple, list] = {}
    for (cell, kind), polys in planner.polys.items():
        if not kind.startswith("wall"):
            continue
        for poly in polys:
            f = _vertical_face(poly)
            if f is None:
                continue
            n, d = f[0], f[1]
            index.setdefault((round(n[0], 2), round(n[2], 2), math.floor(d)), []).append((cell, kind) + f)
    rows = []
    for cid, meshes in chunks.items():
        md = meshes.get(STONE)
        if md is None:
            continue
        seen = set()
        for face in md.faces:
            f = _vertical_face([md.verts[i] for i in face])
            if f is None:
                continue
            n, d, (t0, t1), (y0, y1) = f
            key = (round(n[0], 2), round(n[2], 2))
            for b in range(math.floor(d - clear) , math.floor(d + clear) + 1):
                for cell, kind, rn, rd, (rt0, rt1), (ry0, ry1) in index.get(key + (b,), []):
                    gap = d - rd
                    if abs(gap) >= clear - 1e-4 or geom.dot(n, rn) < 0.999:
                        continue
                    area = max(0.0, min(t1, rt1) - max(t0, rt0)) * max(0.0, min(y1, ry1) - max(y0, ry0))
                    if area <= min_area:
                        continue
                    k = (cid, cell, kind, round(rd, 2), tuple(round(c, 2) for c in rn))
                    if k in seen:
                        continue
                    seen.add(k)
                    rows.append((cid, cell, kind, tuple(round(c, 3) for c in rn), round(gap, 3), round(area, 2), round(rd, 3),
                                 (round(max(t0, rt0), 2), round(min(t1, rt1), 2)), (round(max(y0, ry0), 2), round(min(y1, ry1), 2))))
    rows.sort(key=lambda r: -r[5])
    return rows


def report(chunks: dict, exterior: dict) -> list[str]:
    """Budget problems (grid's P3A_* numbers): an empty list when the
    silhouette is within them."""
    problems = []
    ids = [c["id"] for c in exterior["chunks"]]
    lo, hi = G.P3A_HLOD_CHUNKS
    if not lo <= len(ids) <= hi:
        problems.append(f"{len(ids)} chunks, not {lo}-{hi}")
    total = 0
    for cid in ids:
        meshes = chunks.get(cid, {})
        if not meshes:
            problems.append(f"{cid}: empty")
        if len(meshes) > G.P3A_HLOD_DRAWS_PER_CHUNK:
            problems.append(f"{cid}: {len(meshes)} draws (cap {G.P3A_HLOD_DRAWS_PER_CHUNK})")
        crag_mat = (exterior.get("crag") or {}).get("material") if (exterior.get("crag") or {}).get("chunk") == cid else None
        for mat, md in meshes.items():
            if mat not in MATERIALS and mat != crag_mat:
                problems.append(f"{cid}: material {mat} (only {MATERIALS})")
            if md.name != G.hlod_node(cid, mat):
                problems.append(f"{cid}: node {md.name}")
            if any(not (len(set(c)) == 1 and 0.0 <= c[0] <= 1.0) for face in md.colors for c in face):
                problems.append(f"{cid}: {mat} COLOR_0 not grey")
        total += sum(md.triangles() for md in meshes.values())
    if total > G.P3A_HLOD_TRIS:
        problems.append(f"{total} triangles (cap {G.P3A_HLOD_TRIS})")
    mb = gpu_bytes([md for m in chunks.values() for md in m.values()]) / (1024 * 1024)
    if mb > G.P3A_HLOD_GPU_MB:
        problems.append(f"{mb:.1f} MB of GPU memory (cap {G.P3A_HLOD_GPU_MB})")
    return problems
