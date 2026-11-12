"""The four dorm room shells (pure Python): geometry, materials, lights and
the walkable / placeable data written to data/world/instances/.

A shell is the fixed architecture of a player's room: walls, windows, the
door out, fireplace or stove, light fittings and the player's own bed. The
furniture players place comes on top (furniture_models.py).

Rooms are keyed by neutral house slots (`house_a`..`house_d`, the house ids
in sorted order, as in the style guide); their layout ids are
`dorm_<house id>` and their cells `dorm_house_<x>`. Local coordinates: the
room is centred on the origin, floor at y = 0, the way out on the south
(+z) wall, so entering players face north.

* house_a: a round tower room, stone walls, tall lancet windows, a conical
  timber roof, red hangings, an iron stove.
* house_b: a low round burrow room, cream plaster over honey oak panelling,
  round windows at grass level, a round door, a copper-hooded hearth.
* house_c: an airy round tower room, pale stone, tall arched windows, a
  painted starry dome with bronze ribs, blue drapes, a marble fireplace.
* house_d: a vaulted dungeon room in warm honey-grey stone, windows onto
  the moonlit lake (a soft teal-blue view that swells slowly, not a
  backlight), brass lanterns with a candle-warm glow on chains, a carved
  stone fireplace with a big fire, candle sconces on its chimney breast and
  by the door, green silk hangings: green and silver only in the fabrics
  and fittings, warm light.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import furniture_models as fm
import meshkit as mk
import shell_kit as sk
from castle_kit import fire as FI
from meshkit import Mesh

TAU = 2 * math.pi
FACING = {"north": 0.0, "west": math.pi / 2, "south": math.pi, "east": 3 * math.pi / 2}


@dataclass
class MatSpec:
    """How a material role is made: a flat colour or a painted texture
    (`texture = (painter in paint.py, kwargs)`), with `tile_m` metres per
    texture repeat (UVs in metres) or `uv = "fit"` (0..1 over the face)."""

    base: str = "#808080"
    texture: tuple[str, dict] | None = None
    tex_size: int = 512
    tile_m: float = 2.0
    roughness: float = 0.8
    metallic: float = 0.0
    emissive: float = 0.0  # strength; the base colour or texture glows
    uv: str = "metres"


# Flames: a painted gradient (orange edges, pale core) that glows. Flame
# faces carry 0..1 UVs (base 0, tip 1).
FIRE = MatSpec("#FF8A2A", ("flame", {}), 128, 1.0, 0.5, 0.0, 2.0, "fit")


@dataclass
class Shell:
    slot: str
    cell: str
    name: str
    exit_name: str
    mesh: Mesh
    materials: dict[str, MatSpec]
    lights: list[dict]
    ambient: dict
    outline: list[tuple[float, float]]  # inner wall polygon (x, z), for walkable / placeable data
    wall_h: float
    obstacles: list[tuple[float, float, float, float]] = field(default_factory=list)  # (x0, z0, x1, z1)
    wall_areas: list[dict] = field(default_factory=list)
    table_areas: list[dict] = field(default_factory=list)  # sills and mantels (axis-aligned only)
    keep_clear: list[tuple[float, float, float, float]] = field(default_factory=list)  # no furniture (door)
    spawn: tuple[float, float, float, float] = (0.0, 0.0, 3.0, math.pi)
    exit: tuple[float, float, float, float] = (0.0, 0.0, 3.9, 1.5)  # x, y, z, radius


# ---------------------------------------------------------------------------
# Shared pieces


def remap(m: Mesh, mark, table: dict) -> None:
    """Gives the faces added since `mark` shell materials: `table` maps
    (tile, zone) or tile to a material role."""
    for f in m.faces[mark[1]:]:
        f.material = table.get((f.material, f.zone), table.get(f.material, f.material))
        f.zone = 0


def fixed_bed(m: Mesh, pos, yaw: float, table: dict, size=(1.6, 2.3), height: float = 2.5) -> tuple:
    """The player's own four-poster, part of the shell. Returns its floor AABB."""
    mark = m.mark()
    item = {"asset": "furniture/bed_four_poster_a", "id": 0,
            "furniture": {"surface": "floor", "footprint": list(size), "height": height}}
    m.extend(fm.bed_four_poster(item))
    remap(m, mark, table)
    m.place(mark, yaw, pos)
    hx = abs(math.cos(yaw)) * size[0] / 2 + abs(math.sin(yaw)) * size[1] / 2
    hz = abs(math.sin(yaw)) * size[0] / 2 + abs(math.cos(yaw)) * size[1] / 2
    return (pos[0] - hx, pos[2] - hz, pos[0] + hx, pos[2] + hz)


def against(facet: sk.Facet, depth: float, gap: float = 0.03):
    """Position and yaw of an item standing against a facet, facing the room."""
    c = facet.at(facet.centre, 0.0, gap + depth / 2)
    return c, sk.yaw_facing(facet.n)


def skirting(m: Mesh, facet: sk.Facet, height: float, proud: float, material: str) -> None:
    """A skirting board along a facet, broken where a door reaches the floor."""
    spans = [(0.0, facet.length)]
    for o in facet.openings:
        if o.sill < height:
            out = o.outline(facet.centre + o.offset)
            ua = min(p[0] for p in out) - (o.frame_width if o.frame else 0.0)
            ub = max(p[0] for p in out) + (o.frame_width if o.frame else 0.0)
            spans = [s for a, b in spans for s in ((a, min(b, ua)), (max(a, ub), b)) if s[1] - s[0] > 0.02]
    for a, b in spans:
        facet.local_box(m, a, b, 0.0, height, 0.0, proud, material, skip=("-y", "-z"))


def band(m: Mesh, facet: sk.Facet, v0: float, v1: float, proud: float, material: str, avoid_openings: bool = True) -> None:
    """A horizontal band (cornice, rail) along a facet."""
    spans = [(0.0, facet.length)]
    if avoid_openings:
        for o in facet.openings:
            out = o.outline(facet.centre + o.offset)
            top = max(p[1] for p in out) + (o.frame_width if o.frame else 0.0)
            bot = min(p[1] for p in out)
            if bot < v1 and top > v0:
                ua = min(p[0] for p in out) - (o.frame_width if o.frame else 0.0)
                ub = max(p[0] for p in out) + (o.frame_width if o.frame else 0.0)
                spans = [s for a, b in spans for s in ((a, min(b, ua)), (max(a, ub), b)) if s[1] - s[0] > 0.02]
    for a, b in spans:
        facet.local_box(m, a, b, v0, v1, 0.0, proud, material, skip=("-z",))


def curtains(m: Mesh, facet: sk.Facet, opening: sk.Opening, top: float, material: str, rod: str | None = None,
             width: float = 0.38) -> None:
    """Gathered drapes either side of a window, hung from a rod."""
    out = opening.outline(facet.centre + opening.offset)
    ua = min(p[0] for p in out) - opening.frame_width - 0.02
    ub = max(p[0] for p in out) + opening.frame_width + 0.02
    for a, b in ((ua - width, ua), (ub, ub + width)):
        a, b = max(a, 0.02), min(b, facet.length - 0.02)
        if b - a < 0.1:
            continue
        mark = m.mark()
        fm._pleats(m, a, b, 0.0, 0.25, top - 0.06, 3, 0.05, 0, material)
        m.transform(mark, lambda p: facet.at(p[0], p[1], 0.08 + p[2]))
    if rod:
        mark = m.mark()
        mk.rod(m, (max(ua - width - 0.05, 0.01), top, 0.1), (min(ub + width + 0.05, facet.length - 0.01), top, 0.1),
               0.016, 6, rod)
        m.transform(mark, lambda p: facet.at(p[0], p[1], p[2]))


def tapestry(m: Mesh, facet: sk.Facet, width: float, bottom: float, top: float, cloth: str, trim: str, slot: str) -> None:
    """A house hanging on a facet: a pointed cloth on a rod with finials,
    bearing the same shield and charge as the house banner, twice the size."""
    c, hw = facet.centre, width / 2
    mark = m.mark()
    cloth_outline = [(c - hw, top), (c - hw, bottom + 0.3), (c, bottom), (c + hw, bottom + 0.3), (c + hw, top)]
    mk.prism(m, cloth_outline, 0.02, 0.035, cloth)
    mk.rod(m, (c - hw - 0.08, top + 0.03, 0.05), (c + hw + 0.08, top + 0.03, 0.05), 0.02, 6, trim)
    for s in (-1, 1):
        mk.lathe(m, [(0.03, 0.0), (0.035, 0.02), (0.0, 0.05)], 6, trim, center=(c + s * (hw + 0.1), top + 0.01, 0.05))
    mid = (bottom + top) / 2 + 0.2
    for shape, mat, z in ((fm.SHIELD, trim, 0.036), *((ch, cloth, 0.037) for ch in fm._charge(slot))):
        pts = [(c + x * 2.2, mid + y * 2.2, z) for x, y in shape]
        m.poly(pts if mk.newell(pts)[2] > 0 else list(reversed(pts)), mat)
    m.transform(mark, lambda p: facet.at(p[0], p[1], p[2]))


def sconce(m: Mesh, pos, facing_yaw: float, metal: str, flame: str, wax: str) -> dict:
    """A wall candle sconce; returns its light."""
    mark = m.mark()
    mk.box(m, (-0.05, -0.12, 0.0), (0.05, 0.08, 0.02), metal)
    mk.rod(m, (0.0, -0.05, 0.01), (0.0, 0.0, 0.16), 0.01, 5, metal)
    mk.lathe(m, [(0.0, 0.0), (0.05, 0.01), (0.055, 0.03)], 8, metal, center=(0.0, 0.0, 0.16), cap_top=True)
    mk.cylinder(m, (0.0, 0.03, 0.16), 0.016, 0.11, 6, wax)
    m.place(mark, facing_yaw, pos)
    # The flame on the wick is the client's animated fire (docs/architecture.md "Rendering").
    FI.note(m, candles=[(*mk.add(pos, mk.rot_y((0.0, 0.142, 0.16), facing_yaw)), 0.07)])
    tip = mk.add(pos, mk.rot_y((0.0, 0.17, 0.22), facing_yaw))
    return {"pos": [round(v, 3) for v in tip]}


def hanging_lamp(m: Mesh, pos, ceiling: float, metal: str, glow: str, style: str) -> None:
    """A lamp on a chain from the ceiling: `lantern` (iron, glazed) or
    `copper` (a bell shade over a glowing bulb of glass)."""
    x, y, z = pos
    mk.rod(m, (x, y + 0.3, z), (x, ceiling + 0.05, z), 0.008, 4, metal, caps=False)
    if style == "lantern":
        mk.lathe(m, [(0.02, 0.0), (0.11, 0.04), (0.11, 0.06)], 6, metal, center=(x, y - 0.06, z), cap_bottom=True,
                 cap_top=False)
        mk.lathe(m, [(0.09, 0.0), (0.1, 0.12), (0.085, 0.24)], 6, glow, center=(x, y, z), cap_top=False)
        mk.lathe(m, [(0.11, 0.0), (0.06, 0.08), (0.015, 0.14), (0.0, 0.3)], 6, metal, center=(x, y + 0.22, z),
                 cap_bottom=True)
        for i in range(6):
            a = TAU * i / 6
            mk.rod(m, (x + 0.1 * math.sin(a), y - 0.01, z + 0.1 * math.cos(a)),
                   (x + 0.088 * math.sin(a), y + 0.24, z + 0.088 * math.cos(a)), 0.008, 4, metal, caps=False)
    else:
        mk.lathe(m, [(0.0, 0.0), (0.07, 0.03), (0.07, 0.09), (0.0, 0.12)], 8, glow, center=(x, y - 0.02, z))
        mk.lathe(m, [(0.2, 0.0), (0.17, 0.06), (0.08, 0.16), (0.03, 0.26), (0.0, 0.3)], 12, metal, center=(x, y + 0.02, z),
                 cap_bottom=True)


def fireplace(m: Mesh, facet: sk.Facet, width: float, height: float, depth: float, stone: str, dark: str, fire: str,
              mantel: str, hood: str | None = None, breast_to: float | None = None, blaze: float = 1.0) -> tuple:
    """A fireplace against a facet: hearth slab, jambs, mantel, a dark
    firebox with a glowing fire (`blaze` scales its flames, floor glow and
    sparks: a room whose fire is its focal point), optionally a copper hood
    or a chimney breast up to `breast_to`. Returns its floor AABB and its
    light."""
    c = facet.centre
    hw = width / 2
    facet.local_box(m, c - hw - 0.1, c + hw + 0.1, 0.0, 0.06, 0.0, depth + 0.35, stone, skip=("-y",))
    facet.local_box(m, c - hw, c - hw + 0.22, 0.06, height, 0.0, depth, stone, skip=("-y",))
    facet.local_box(m, c + hw - 0.22, c + hw, 0.06, height, 0.0, depth, stone, skip=("-y",))
    facet.local_box(m, c - hw + 0.22, c + hw - 0.22, height - 0.28, height, 0.0, depth, stone)
    facet.local_box(m, c - hw - 0.08, c + hw + 0.08, height, height + 0.08, 0.0, depth + 0.08, mantel)
    # Firebox: back and sides in soot-dark stone.
    facet.local_box(m, c - hw + 0.22, c + hw - 0.22, 0.06, height - 0.28, 0.0, 0.02, dark, skip=("-y",))
    facet.local_box(m, c - hw + 0.22, c - hw + 0.24, 0.06, height - 0.28, 0.0, depth, dark, skip=("-y",))
    facet.local_box(m, c + hw - 0.24, c + hw - 0.22, 0.06, height - 0.28, 0.0, depth, dark, skip=("-y",))
    # Logs; the flames on them and their glow on the hearth slab are the client's animated fire
    # (docs/architecture.md "Rendering"), noted on the mesh in room metres.
    mark = m.mark()
    for k, (u, r) in enumerate(((-0.12, 0.055), (0.1, 0.05))):
        mk.rod(m, (c - 0.25, 0.1 + k * 0.03, depth * 0.45 + u * 0.4), (c + 0.25, 0.1 + k * 0.03, depth * 0.5 - u * 0.3),
               r, 5, mantel)
    m.transform(mark, lambda p: facet.at(p[0], p[1], p[2]))
    at = facet.at(c, 0.1, depth * 0.45)
    tongues = []
    for dx, _, dz, h, w in FI.hearth_tongues(0.5 * blaze, 0.52 * blaze, depth=0.14, rows=2, count=round(5 * blaze),
                                             seed=int(width * 100)):
        q = facet.at(c + dx, 0.1, depth * 0.45 + dz)
        tongues.append((q[0] - at[0], q[1] - at[1], q[2] - at[2], h, w))
    FI.note(m, fires=[FI.fire(at, tongues, pool=min(0.9, width * 0.6) * blaze, embers=round(4 * blaze), floor=0.06)])
    if hood:
        mark = m.mark()
        mk.lathe(m, [(width * 0.62, 0.0), (width * 0.55, 0.08), (0.22, 0.75), (0.18, 0.95), (0.18, 1.6)], 10, hood,
                 center=(0.0, 0.0, 0.0), cap_top=False, scale_xz=(1.0, 0.55))
        m.transform(mark, lambda p: facet.at(c + p[0], height + 0.08 + p[1], depth * 0.55 + p[2]))
    if breast_to:
        facet.local_box(m, c - hw - 0.05, c + hw + 0.05, height + 0.08, breast_to, 0.0, depth * 0.6, stone, skip=("+y",))
    corners = [facet.at(c - hw - 0.1, 0.0, 0.0), facet.at(c + hw + 0.1, 0.0, 0.0),
               facet.at(c - hw - 0.1, 0.0, depth + 0.35), facet.at(c + hw + 0.1, 0.0, depth + 0.35)]
    box = (min(p[0] for p in corners), min(p[2] for p in corners), max(p[0] for p in corners), max(p[2] for p in corners))
    light = facet.at(c, 0.45, depth + 0.4)
    return box, [round(v, 3) for v in light]


def door_details(m: Mesh, facet: sk.Facet, o: sk.Opening, metal: str, round_door: bool = False) -> None:
    """Iron straps and a ring pull on a door leaf at the back of its reveal."""
    out = o.outline(facet.centre + o.offset)
    u0, u1 = min(p[0] for p in out), max(p[0] for p in out)
    w = -o.depth + 0.01
    # Lathe parts are built around +Y and turned so +Y points into the room:
    # (x, y, z) -> (u, w, -v) keeps the transform proper (no mirroring).
    if round_door:
        mark = m.mark()
        mk.lathe(m, [(0.0, 0.0), (0.06, 0.0), (0.06, 0.03), (0.0, 0.05)], 10, metal)
        m.transform(mark, lambda p: facet.at(facet.centre + o.offset + p[0], o.spring - p[2], w + p[1]))
        return
    for v in (0.35, 1.45):
        facet.local_box(m, u0 + 0.04, u1 - 0.04, v, v + 0.07, w, w + 0.015, metal)
    mark = m.mark()
    mk.lathe(m, [(0.05, 0.0), (0.06, 0.006), (0.05, 0.012)], 8, metal, cap_top=False)
    m.transform(mark, lambda p: facet.at(u1 - 0.22 + p[0], 1.0 - p[2], w + 0.02 + p[1]))


def wall_area(aid: str, facet: sk.Facet, v0: float, v1: float, margin: float = 0.12, u_range=None) -> dict:
    """A placeable wall area on a cardinal facet (its whole width by default)."""
    a, b = u_range or (margin, facet.length - margin)
    p0, p1 = facet.at(a, v0), facet.at(b, v1)
    lo = [round(min(p0[k], p1[k]), 3) for k in range(3)]
    hi = [round(max(p0[k], p1[k]), 3) for k in range(3)]
    facing = sk.yaw_facing(facet.n) % TAU
    return {"id": aid, "surface": "wall", "min": lo, "max": hi, "facing": round(facing, 7)}


def table_area(aid: str, a, b) -> dict:
    """A horizontal table area between two corner points (same height)."""
    lo = [round(min(a[k], b[k]), 3) for k in range(3)]
    hi = [round(max(a[k], b[k]), 3) for k in range(3)]
    return {"id": aid, "surface": "table", "min": lo, "max": hi}


def round_outline(apothem: float, segments: int = 12):
    r = apothem / math.cos(math.pi / segments)
    return [(r * math.sin(math.radians(k * 360 / segments - 180 / segments)),
             -r * math.cos(math.radians(k * 360 / segments - 180 / segments))) for k in range(segments)]


def round_floor_and_walls(m: Mesh, a: float, h: float, floor: str, wall: str, openings: dict, wall_uv: float,
                          floor_uv: float):
    """Floor and the 12 facets of a round room; returns {angle: facet}."""
    facets = {}
    u_off = 0.0
    for theta, facet in sk.round_room_facets(a, h):
        facet.openings = openings.get(int(round(theta)), [])
        facet.build(m, wall, 1.0, u_off)
        facets[int(round(theta))] = facet
        u_off += facet.length
    corners = [(x, 0.0, z) for x, z in round_outline(a)]
    sk.polygon_floor(m, corners, 0.0, floor)
    return facets


# ---------------------------------------------------------------------------
# house_a: the tower room


def house_a() -> Shell:
    a, h, rise = 5.0, 4.4, 4.0
    m = Mesh()
    win = lambda: sk.Opening("window", "pointed", 1.0, 0.95, 2.95, 0.75, pane="glass", frame="stone_trim",  # noqa: E731
                             frame_width=0.12, sill_board="stone_trim")
    door = sk.Opening("door", "pointed", 1.2, 0.0, 1.95, 0.6, pane="door_wood", pane_uv="metres", frame="stone_trim",
                      frame_width=0.14)
    openings = {30: [win()], 120: [win()], 240: [win()], 330: [win()], 180: [door]}
    f = round_floor_and_walls(m, a, h, "floor", "stone", openings, 1.0, 1.0)
    corners = [(x, h, z) for x, z in round_outline(a)]
    sk.cone_roof(m, corners, h, h + rise, "roof_plaster")
    lights = []
    for theta, facet in f.items():
        skirting(m, facet, 0.16, 0.03, "wood_dark")
        band(m, facet, h - 0.22, h, 0.14, "wood_dark", avoid_openings=False)
    # Rafters from every corner to a boss at the apex, and a ring beam.
    apex = (0.0, h + rise, 0.0)
    for x, _, z in corners:
        inner = mk.lerp((x * 0.97, h, z * 0.97), apex, 0.92)
        sk.rafter(m, (x * 0.97, h - 0.05, z * 0.97), inner, 0.14, "wood_dark")
    mk.lathe(m, [(0.0, 0.0), (0.12, 0.05), (0.16, 0.3), (0.1, 0.5)], 8, "wood_dark", center=(0.0, h + rise - 0.75, 0.0),
             cap_bottom=False)
    # Red drapes at the windows.
    for theta in (30, 120, 240, 330):
        curtains(m, f[theta], f[theta].openings[0], 3.85, "velvet", rod="gold")
    door_details(m, f[180], door, "iron")
    tapestry(m, f[210], 1.1, 0.9, 3.7, "velvet", "gold", "house_a")
    # The player's own bed against the north-west facets, the stove to the
    # north-east, a wardrobe south-east.
    bed_table = {("wood", 1): "wood_dark", ("wood", 3): "gold", ("fabric", 2): "velvet", ("fabric", 3): "gold",
                 "linen": "linen", "wood": "wood_dark", "fabric": "velvet", "fabric_cover": "velvet"}
    pos, yaw = against(f[300], 2.3)
    bed = fixed_bed(m, pos, yaw, bed_table)
    stove_c = mk.add(f[60].at(f[60].centre, 0.0, 0.0), mk.scale(f[60].n, 0.75))
    stove(m, stove_c, h + rise * (1 - (a - 0.75) / a), yaw_face=sk.yaw_facing(f[60].n))
    lights.append({"id": "stove", "pos": [round(stove_c[0] + f[60].n[0] * 0.4, 3), 0.45, round(stove_c[2] + f[60].n[2] * 0.4, 3)],
                   "color": "#FF8238", "intensity": 6.0, "range": 5.0})
    wpos, wyaw = against(f[150], 0.62)
    wardrobe(m, wpos, wyaw, "wood_dark", "gold")
    for theta in (105, 195, 255):
        d = sk.direction(theta)
        r = a / math.cos(math.pi / 12) - 0.02
        lt = sconce(m, (d[0] * r, 2.2, d[2] * r), sk.yaw_facing(mk.scale(d, -1.0)), "iron", "flame", "wax")
        lights.append({"id": f"sconce_{theta}", "color": "#FFC46E", "intensity": 2.5, "range": 5.0, **lt})
    for theta in (30, 330):
        d = sk.direction(theta)
        lights.append({"id": f"window_{theta}", "pos": [round(d[0] * 3.6, 3), 3.0, round(d[2] * 3.6, 3)],
                       "color": "#A8C6F0", "intensity": 3.0, "range": 6.0})
    wbox = _aabb_of(wpos, wyaw, (1.3, 0.62))
    stove_box = (stove_c[0] - 0.45, stove_c[2] - 0.45, stove_c[0] + 0.45, stove_c[2] + 0.45)
    mats = {
        "stone": MatSpec("#9A8D79", ("masonry", dict(base="#A69882", mortar="#6E6457", row_px=[224, 256, 288],
                                                      block_px=(320, 640), seed=11, grime=0.15, variation=0.07)), 1024, 2.0, 0.9),
        "floor": MatSpec("#7D4F29", ("planks", dict(base="#8A572D", planks_per_tile=8, seed=12)), 1024, 2.0, 0.7),
        "roof_plaster": MatSpec("#C1AD88", ("plaster", dict(base="#C9B791", seed=13)), 512, 2.0, 0.95),
        "wood_dark": MatSpec("#522D16", ("wood_grain", dict(base="#60351A", seed=14)), 512, 1.0, 0.7),
        "door_wood": MatSpec("#6F3F1D", ("planks", dict(base="#71421F", planks_per_tile=5, seed=15, joint_dark=0.55)), 512, 1.0, 0.7),
        "stone_trim": MatSpec("#C1AD88", ("masonry", dict(base="#BEAD8B", mortar="#8D7E69", row_px=[96, 128], block_px=(180, 360),
                                                          seed=16, joint_px=1.5, variation=0.06)), 512, 1.0, 0.85),
        "velvet": MatSpec("#8E0A0A", ("velvet", dict(base="#981212", seed=17, folds=4)), 512, 1.0, 0.85),
        "gold": MatSpec("#CF9F23", None, 64, 1.0, 0.35, 0.8),
        "iron": MatSpec("#42444B", None, 64, 1.0, 0.6, 0.6),
        "linen": MatSpec("#E9E1CE", ("weave", dict(base="#E9E1CE", seed=18)), 256, 0.5, 0.9),
        "wax": MatSpec("#F1E6C7", None, 64, 1.0, 0.6),
        "flame": MatSpec("#FFC46E", None, 64, 1.0, 0.5, 0.0, 6.0),
        "fire": FIRE,
        "glass": MatSpec("#9FB4C8", ("leaded_glass", dict(glass="#B9CCDD", lead="#2E2F33", seed=19)), 256, 1.0, 0.2, 0.0, 1.6, "fit"),
    }
    return Shell("house_a", "dorm_house_a", "Tower Dormitory", "Spiral Stair Down", m, mats, lights,
                 {"sky": "#8F9CB2", "ground": "#604732", "intensity": 0.35}, round_outline(a), h,
                 obstacles=[bed, stove_box, wbox],
                 wall_areas=[wall_area("wall_north", f[0], 0.3, h - 0.3), wall_area("wall_east", f[90], 0.3, h - 0.3),
                             wall_area("wall_west", f[270], 0.3, h - 0.3)],
                 keep_clear=[(-0.8, a - 1.3, 0.8, a)],
                 spawn=(0.0, 0.0, a - 1.6, math.pi), exit=(0.0, 0.0, a - 0.7, 1.5))


def _aabb_of(pos, yaw: float, size) -> tuple:
    hx = abs(math.cos(yaw)) * size[0] / 2 + abs(math.sin(yaw)) * size[1] / 2
    hz = abs(math.sin(yaw)) * size[0] / 2 + abs(math.cos(yaw)) * size[1] / 2
    return (pos[0] - hx, pos[2] - hz, pos[0] + hx, pos[2] + hz)


def stove(m: Mesh, base, roof_y: float, yaw_face: float) -> None:
    """A cast-iron pot-belly stove with a glowing door and a flue to the roof."""
    x, _, z = base
    prof = [(0.22, 0.0), (0.24, 0.05), (0.2, 0.12), (0.28, 0.3), (0.3, 0.5), (0.26, 0.7), (0.18, 0.8), (0.18, 0.86),
            (0.1, 0.92)]
    mk.lathe(m, prof, 10, "iron", center=(x, 0.0, z), cap_top=False)
    for k in range(4):
        a = TAU * (k + 0.5) / 4
        mk.rod(m, (x + 0.18 * math.sin(a), 0.0, z + 0.18 * math.cos(a)), (x + 0.14 * math.sin(a), 0.1, z + 0.14 * math.cos(a)),
               0.025, 4, "iron")
    mark = m.mark()
    door = mk.arch_outline(0.22, 0.38, "round", 0.24, 6)
    m.poly([(u, v, 0.305) for u, v in door], "fire", [(u / 0.22 + 0.5, (v - 0.24) / 0.25) for u, v in door])
    mk.box(m, (-0.14, 0.22, 0.28), (0.14, 0.24, 0.32), "iron")
    m.place(mark, yaw_face, (x, 0.0, z))
    # Flames licking in the open door and a warm glow on the floor before it (the client's animated fire).
    at = mk.add((x, 0.0, z), mk.rot_y((0.0, 0.25, 0.32), yaw_face))
    FI.note(m, fires=[FI.fire(at, FI.cluster_tongues(0.05, 0.2, count=3, seed=13), pool=0.7, embers=2, floor=0.0)])
    mk.rod(m, (x, 0.9, z), (x, roof_y + 0.1, z), 0.075, 8, "iron", caps=False)
    mk.lathe(m, [(0.09, 0.0), (0.09, 0.05)], 8, "iron", center=(x, 1.4, z), cap_top=False)


def wardrobe(m: Mesh, pos, yaw: float, wood: str, metal: str) -> None:
    """A tall two-door wardrobe with a cornice and brass handles."""
    mark = m.mark()
    w, d, h = 1.3, 0.62, 2.25
    mk.box(m, (-w / 2, 0.0, -d / 2), (w / 2, h - 0.12, d / 2), wood, skip=("-y",))
    mk.box(m, (-w / 2 - 0.04, h - 0.12, -d / 2 - 0.02), (w / 2 + 0.04, h, d / 2 + 0.04), wood)
    mk.box(m, (-w / 2 - 0.02, 0.0, -d / 2), (w / 2 + 0.02, 0.12, d / 2 + 0.02), wood, skip=("-y",))
    for sx in (-1, 1):
        mk.box(m, (sx * 0.02 if sx > 0 else -w / 2 + 0.05, 0.2, d / 2), (w / 2 - 0.05 if sx > 0 else -0.02, h - 0.2, d / 2 + 0.02), wood)
        mk.box(m, (sx * 0.06 - 0.01, 1.05, d / 2 + 0.02), (sx * 0.06 + 0.01, 1.25, d / 2 + 0.04), metal)
    m.place(mark, yaw, pos)


# ---------------------------------------------------------------------------
# house_b: the burrow room

DADO_B = 0.82  # top of the oak panelling (the rail sits on it)


def house_b() -> Shell:
    a, h, rise = 4.6, 2.9, 0.65
    m = Mesh()
    rwin = lambda: sk.Opening("window", "circle", 0.78, 1.55, 2.05, 0.6, pane="garden", frame="oak",  # noqa: E731
                              frame_width=0.1, frame_proud=0.05)
    # A round door: a circle cut flat by the floor (its centre is below its radius).
    door = sk.Opening("door", "circle", 2.0, 0.0, 0.85, 0.5, pane="door_round", pane_uv="metres", frame="oak",
                      frame_width=0.14, frame_proud=0.05)
    openings = {30: [rwin()], 120: [rwin()], 300: [rwin()], 330: [rwin()], 180: [door]}
    f = round_floor_and_walls(m, a, h, "floor", "plaster", openings, 1.0, 1.0)
    _clip_below_floor(m)  # the round door's outline dips under the floor
    corners = [(x, h, z) for x, z in round_outline(a)]
    sk.cone_roof(m, corners, h, h + rise, "ceiling")
    lights = []
    for theta, facet in f.items():
        if theta == 180:
            continue
        # Honey-oak panelling to dado height with a rail, and a picture rail.
        facet.local_box(m, 0.0, facet.length, 0.0, DADO_B, 0.0, 0.025, "oak", skip=("-y", "-z"))
        facet.local_box(m, 0.0, facet.length, DADO_B, DADO_B + 0.06, 0.0, 0.045, "oak_dark", skip=("-z",))
        band(m, facet, h - 0.14, h, 0.08, "oak_dark", avoid_openings=False)
    # Radial beams meeting at a copper boss; a copper lamp hangs there.
    apex = (0.0, h + rise, 0.0)
    for x, _, z in corners:
        sk.rafter(m, (x * 0.98, h - 0.06, z * 0.98), mk.lerp((x, h, z), apex, 0.93), 0.13, "oak_dark")
    mk.lathe(m, [(0.0, 0.0), (0.14, 0.04), (0.16, 0.12), (0.1, 0.2)], 10, "copper", center=(0.0, h + rise - 0.2, 0.0),
             cap_bottom=False)
    hanging_lamp(m, (0.0, 2.3, 0.0), h + rise - 0.2, "copper", "lamp_glow", "copper")
    lights.append({"id": "lamp", "pos": [0.0, 2.2, 0.0], "color": "#FFC46E", "intensity": 4.0, "range": 6.0})
    door_details(m, f[180], door, "copper", round_door=True)
    # Hearth with a copper hood (east), the bed (south-west), a plant shelf (north-east).
    fbox, flight = fireplace(m, f[90], 1.3, 0.85, 0.5, "hearth_stone", "soot", "fire", "oak_dark", hood="copper")
    lights.append({"id": "hearth", "pos": flight, "color": "#FF8238", "intensity": 6.0, "range": 5.0})
    bed_table = {("wood", 1): "oak_dark", ("wood", 3): "copper", ("fabric", 2): "hangings", ("fabric", 3): "mustard",
                 "linen": "linen", "wood": "oak_dark", "fabric": "hangings", "fabric_cover": "quilt"}
    pos, yaw = against(f[240], 2.1)
    bed = fixed_bed(m, pos, yaw, bed_table, size=(1.5, 2.1), height=2.2)
    shelf_f = f[60]
    for v in (1.2, 1.7):
        shelf_f.local_box(m, 0.25, shelf_f.length - 0.25, v, v + 0.04, 0.0, 0.26, "oak_dark")
        for k, u in enumerate((0.55, 1.2, 1.85)):
            if u > shelf_f.length - 0.3:
                continue
            mark = m.mark()
            mk.lathe(m, [(0.06, 0.0), (0.085, 0.13), (0.09, 0.14)], 8, "copper", cap_top=True)
            mk.lathe(m, [(0.06, 0.0), (0.14, 0.1), (0.11, 0.2), (0.0, 0.25)], 6, "leaves", center=(0.0, 0.13, 0.0))
            m.transform(mark, lambda p, u=u, v=v: shelf_f.at(u + p[0], v + 0.04 + p[1], 0.13 + p[2]))
    for theta in (30, 330, 120, 300):
        d = sk.direction(theta)
        lights.append({"id": f"window_{theta}", "pos": [round(d[0] * 3.4, 3), 1.9, round(d[2] * 3.4, 3)],
                       "color": "#FFE3A0", "intensity": 1.6, "range": 4.0})
    mats = {
        "plaster": MatSpec("#DDCB9D", ("plaster", dict(base="#E0CC9A", seed=21)), 512, 2.0, 0.95),
        "ceiling": MatSpec("#CFB683", ("plaster", dict(base="#CBB07D", seed=22, contrast=0.14)), 512, 2.0, 0.95),
        "floor": MatSpec("#B07C47", ("planks", dict(base="#B2793A", planks_per_tile=7, seed=23, variation=0.06, joint_dark=0.6)),
                         1024, 2.0, 0.7),
        "oak": MatSpec("#B07C47", ("wood_grain", dict(base="#B2763D", seed=24)), 512, 1.0, 0.65),
        "oak_dark": MatSpec("#7D4F23", ("wood_grain", dict(base="#7F4E22", seed=25)), 512, 1.0, 0.7),
        "door_round": MatSpec("#985F28", ("planks", dict(base="#9A5E25", planks_per_tile=6, seed=26)), 512, 1.0, 0.7),
        "copper": MatSpec("#B95B27", None, 64, 1.0, 0.35, 0.9),
        "hearth_stone": MatSpec("#988367", ("masonry", dict(base="#9C8465", mortar="#6E5D48", row_px=[48, 64, 56],
                                                            block_px=(60, 140), seed=27)), 512, 1.0, 0.9),
        "soot": MatSpec("#443A34", None, 64, 1.0, 0.95),
        "fire": FIRE,
        "quilt": MatSpec("#EEB31D", ("patchwork", dict(colours=["#EEB31D", "#C18E17", "#7D4F23", "#EBD47F", "#4F4037", "#CF7419"],
                                                       seed=28)), 512, 1.0, 0.9),
        "mustard": MatSpec("#C18E17", None, 64, 1.0, 0.9),
        "hangings": MatSpec("#DE9E1B", ("velvet", dict(base="#DE9E1B", seed=32, folds=4)), 512, 1.0, 0.85),
        "linen": MatSpec("#E9E1CE", ("weave", dict(base="#EEE5CF", seed=29)), 256, 0.5, 0.9),
        "leaves": MatSpec("#567D39", ("plaster", dict(base="#598A2F", seed=30, contrast=0.3)), 256, 0.5, 0.9),
        "lamp_glow": MatSpec("#FFD9A0", None, 64, 1.0, 0.5, 0.0, 5.0),
        "garden": MatSpec("#BFD8A0", ("garden_view", dict(seed=31)), 256, 1.0, 0.3, 0.0, 1.4, "fit"),
    }
    return Shell("house_b", "dorm_house_b", "Burrow Dormitory", "Round Door", m, mats, lights,
                 {"sky": "#E9D19F", "ground": "#6E4A26", "intensity": 0.45}, round_outline(a), h,
                 obstacles=[bed, fbox],
                 wall_areas=[wall_area("wall_north", f[0], DADO_B + 0.1, h - 0.16),
                             wall_area("wall_west", f[270], DADO_B + 0.1, h - 0.16)],
                 keep_clear=[(-1.0, a - 1.3, 1.0, a)],
                 spawn=(0.0, 0.0, a - 1.5, math.pi), exit=(0.0, 0.0, a - 0.7, 1.5))


def _clip_below_floor(m: Mesh) -> None:
    """Flattens geometry below the floor (a round door cut at the threshold)."""
    for i, (x, y, z) in enumerate(m.verts):
        if y < 0.0:
            m.verts[i] = (x, 0.0, z)
    m.faces = [f for f in m.faces if mk.length(mk.newell(m.face_points(f))) > 1e-9]


# ---------------------------------------------------------------------------
# house_c: the airy tower room


def house_c() -> Shell:
    a, h, rise = 5.2, 5.0, 3.6
    m = Mesh()
    win = lambda: sk.Opening("window", "round", 1.25, 0.65, 3.55, 0.7, pane="glass", frame="bronze",  # noqa: E731
                             frame_width=0.07, frame_proud=0.03, sill_board="stone_trim", reveal="stone_trim")
    door = sk.Opening("door", "pointed", 1.2, 0.0, 1.95, 0.6, pane="door_wood", pane_uv="metres", frame="stone_trim",
                      frame_width=0.12)
    openings = {30: [win()], 60: [win()], 300: [win()], 330: [win()], 180: [door]}
    f = round_floor_and_walls(m, a, h, "floor", "stone", openings, 1.0, 1.0)
    corners = [(x, h, z) for x, z in round_outline(a)]
    sk.dome(m, corners, h, rise, 5, "dome", repeats=4.0)
    lights = []
    for theta, facet in f.items():
        skirting(m, facet, 0.14, 0.025, "stone_trim")
        band(m, facet, h - 0.25, h, 0.12, "stone_trim", avoid_openings=False)
        band(m, facet, h - 0.29, h - 0.25, 0.13, "bronze", avoid_openings=False)
    # Bronze ribs from each corner up to a crown ring.
    for x, _, z in corners:
        prev = None
        for i in range(6):
            phi = (math.pi / 2) * i / 5 * 0.92
            p = (x * math.cos(phi) * 0.985, h + rise * math.sin(phi) - 0.03, z * math.cos(phi) * 0.985)
            if prev:
                sk.rafter(m, prev, p, 0.07, "bronze")
            prev = p
    mk.lathe(m, [(0.42, 0.0), (0.36, 0.05), (0.0, 0.08)], 12, "bronze", center=(0.0, h + rise - 0.3, 0.0), cap_bottom=True)
    for theta in (30, 60, 300, 330):
        curtains(m, f[theta], f[theta].openings[0], 4.0, "velvet", rod="bronze", width=0.32)
    door_details(m, f[180], door, "bronze")
    for theta in (150, 210):
        tapestry(m, f[theta], 1.0, 1.0, 4.1, "velvet", "bronze", "house_c")
    fbox, flight = fireplace(m, f[120], 1.4, 1.1, 0.45, "marble", "soot", "fire", "marble_dark", breast_to=h - 0.29)
    lights.append({"id": "fire", "pos": flight, "color": "#FF8238", "intensity": 5.0, "range": 5.0})
    bed_table = {("wood", 1): "wood_pale", ("wood", 3): "bronze", ("fabric", 2): "velvet", ("fabric", 3): "bronze",
                 "linen": "linen", "wood": "wood_pale", "fabric": "velvet", "fabric_cover": "velvet"}
    pos, yaw = against(f[240], 2.3)
    bed = fixed_bed(m, pos, yaw, bed_table)
    for theta in (150, 210, 270, 90):
        d = sk.direction(theta + 15)
        r = a / math.cos(math.pi / 12) - 0.02
        lt = sconce(m, (d[0] * r, 2.4, d[2] * r), sk.yaw_facing(mk.scale(d, -1.0)), "bronze", "flame", "wax")
        lights.append({"id": f"sconce_{theta + 15}", "color": "#FFC46E", "intensity": 2.0, "range": 5.0, **lt})
    for theta in (30, 330, 60, 300):
        d = sk.direction(theta)
        lights.append({"id": f"window_{theta}", "pos": [round(d[0] * 3.8, 3), 3.0, round(d[2] * 3.8, 3)],
                       "color": "#BFD6FF", "intensity": 3.0, "range": 6.5})
    mats = {
        "stone": MatSpec("#C1B7A3", ("masonry", dict(base="#C9C1AC", mortar="#98907E", row_px=[224, 256, 288],
                                                      block_px=(340, 680), seed=41, variation=0.06)), 1024, 2.0, 0.9),
        "floor": MatSpec("#B28D62", ("planks", dict(base="#B98F60", planks_per_tile=8, seed=42)), 1024, 2.0, 0.65),
        "dome": MatSpec("#1F325A", ("star_dome", dict(base="#182C65", star="#DEB345", seed=43)), 1024, 1.0, 0.8, 0.0, 0.25, "fit"),
        "stone_trim": MatSpec("#DED6C7", ("masonry", dict(base="#DBD2C0", mortar="#AEA594", row_px=[96, 128], block_px=(180, 360),
                                                          seed=44, joint_px=1.5, variation=0.05)), 512, 1.0, 0.8),
        "bronze": MatSpec("#A16C1D", None, 64, 1.0, 0.35, 0.85),
        "velvet": MatSpec("#192E6E", ("velvet", dict(base="#19317C", seed=45, folds=4)), 512, 1.0, 0.85),
        "door_wood": MatSpec("#7D4F23", ("planks", dict(base="#7F4E25", planks_per_tile=5, seed=46, joint_dark=0.55)), 512, 1.0, 0.7),
        "wood_pale": MatSpec("#8A5F35", ("wood_grain", dict(base="#8D6036", seed=47)), 512, 1.0, 0.7),
        "marble": MatSpec("#DED6C7", ("plaster", dict(base="#E0D9C9", seed=48, contrast=0.12)), 512, 1.0, 0.3),
        "marble_dark": MatSpec("#9C9791", ("plaster", dict(base="#AAA59F", seed=49, contrast=0.12)), 512, 1.0, 0.3),
        "soot": MatSpec("#443A34", None, 64, 1.0, 0.95),
        "fire": FIRE,
        "linen": MatSpec("#E9E1CE", ("weave", dict(base="#ECE7D9", seed=50)), 256, 0.5, 0.9),
        "wax": MatSpec("#F1E6C7", None, 64, 1.0, 0.6),
        "flame": MatSpec("#FFC46E", None, 64, 1.0, 0.5, 0.0, 6.0),
        "glass": MatSpec("#B9CCDD", ("leaded_glass", dict(glass="#C9DAE8", lead="#5A4A30", seed=51, diamonds=5)), 256, 1.0, 0.2,
                         0.0, 1.8, "fit"),
    }
    return Shell("house_c", "dorm_house_c", "Tower Dormitory", "Spiral Stair Down", m, mats, lights,
                 {"sky": "#B1C2DE", "ground": "#6E6254", "intensity": 0.5}, round_outline(a), h,
                 obstacles=[bed, fbox],
                 wall_areas=[wall_area("wall_north", f[0], 0.3, h - 0.4), wall_area("wall_east", f[90], 0.3, h - 0.4),
                             wall_area("wall_west", f[270], 0.3, h - 0.4)],
                 keep_clear=[(-0.8, a - 1.3, 0.8, a)],
                 spawn=(0.0, 0.0, a - 1.6, math.pi), exit=(0.0, 0.0, a - 0.7, 1.5))


# ---------------------------------------------------------------------------
# house_d: the dungeon room


def house_d() -> Shell:
    hx, hz, spring, crown = 5.5, 4.0, 2.9, 4.0
    pil = 1.83  # x of the transverse ribs and their pilasters (three bays)
    m = Mesh()
    lake = lambda off: sk.Opening("window", "round", 1.3, 0.8, 2.0, 0.95, offset=off, pane="lake", frame="stone_trim",  # noqa: E731
                                  frame_width=0.14, sill_board="stone_trim")
    door = sk.Opening("door", "round", 1.4, 0.0, 1.75, 0.8, pane="door_wood", pane_uv="metres", frame="stone_trim",
                      frame_width=0.16)
    north = sk.Facet((-hx, 0.0, -hz), (hx, 0.0, -hz), (0.0, 0.0, 1.0), 0.0, spring, [lake(-3.6), lake(3.6)])
    south = sk.Facet((hx, 0.0, hz), (-hx, 0.0, hz), (0.0, 0.0, -1.0), 0.0, spring, [door])
    east = sk.Facet((hx, 0.0, -hz), (hx, 0.0, hz), (-1.0, 0.0, 0.0), 0.0, spring)
    west = sk.Facet((-hx, 0.0, hz), (-hx, 0.0, -hz), (1.0, 0.0, 0.0), 0.0, spring)
    u = 0.0
    for facet in (north, east, south, west):
        facet.build(m, "stone", 1.0, u)
        u += facet.length
    arc = sk.barrel_vault(m, -hx, hx, hz, spring, crown, 10, "vault")
    # End walls rise into the vault (lunettes).
    for x, sign in ((hx, -1.0), (-hx, 1.0)):
        for (za, ya), (zb, yb) in zip(arc, arc[1:]):
            pts = [(x, spring, za), (x, spring, zb), (x, yb, zb), (x, ya, za)]
            if abs(ya - spring) < 1e-6:
                pts = [pts[0], pts[1], pts[2]]
            m.poly(sk.orient_toward(pts, (0.0, spring, 0.0)), "stone",
                   [(p[2] * -sign, p[1]) for p in sk.orient_toward(pts, (0.0, spring, 0.0))])
    sk.polygon_floor(m, [(-hx, 0.0, -hz), (hx, 0.0, -hz), (hx, 0.0, hz), (-hx, 0.0, hz)], 0.0, "floor")
    # Transverse ribs and the pilasters that carry them.
    for x in (-pil, pil):
        _rib(m, x, arc, 0.32, 0.12, "stone_trim")
        for z, nz in ((-hz, 1.0), (hz, -1.0)):
            z0, z1 = sorted((z, z + nz * 0.14))
            mk.box(m, (x - 0.16, 0.0, z0), (x + 0.16, spring, z1), "stone_trim", skip=("-y",))
    for facet in (north, east, south, west):
        skirting(m, facet, 0.2, 0.04, "stone_trim")
    door_details(m, south, door, "silver")
    lights = []
    # Brass lanterns with a candle-warm glow: the room's key light is warm (skin, faces), green stays in the fabrics.
    for x in (-3.67, 0.0, 3.67):
        hanging_lamp(m, (x, 2.55, 0.0), crown, "brass", "lantern_glow", "lantern")
        lights.append({"id": f"lamp_{x:+.1f}", "pos": [x, 2.65, 0.0], "color": "#FFAE5C", "intensity": 6.0, "range": 7.5})
    # The fire is the room's focal point: a bigger blaze, the strongest light, reaching the bed and the rug.
    fbox, flight = fireplace(m, east, 1.6, 1.05, 0.55, "stone_trim", "soot", "fire", "stone_dark", breast_to=spring,
                             blaze=1.25)
    lights.append({"id": "fire", "pos": flight, "color": "#FF8238", "intensity": 10.0, "range": 9.5})
    # Candle sconces either side of the chimney breast, over the mantel (flames only: the fire's light is beside them).
    for du in (-0.62, 0.62):
        sconce(m, east.at(east.centre + du, 1.62, 0.55 * 0.6), sk.yaw_facing(east.n), "brass", "flame", "wax")
    # Candle sconces either side of the door (clear of the wall areas), sharing one warm light: the door end of the
    # room is lit as warmly as the hearth end.
    for du in (-1.05, 1.05):
        sconce(m, south.at(south.centre + du, 1.85, 0.0), sk.yaw_facing(south.n), "brass", "flame", "wax")
    lights.append({"id": "sconces_door", "pos": [0.0, 2.1, hz - 0.45], "color": "#FFC46E", "intensity": 5.0, "range": 6.5})
    bed_table = {("wood", 1): "wood_black", ("wood", 3): "silver", ("fabric", 2): "silk", ("fabric", 3): "silver",
                 "linen": "linen", "wood": "wood_black", "fabric": "silk", "fabric_cover": "silk"}
    bed = fixed_bed(m, (-hx + 1.15 + 0.03, 0.0, 0.0), math.pi / 2, bed_table)
    # Moonlit water: one soft, dim light between the two lake windows that swells slowly (the client's `ripple`),
    # never the room's key light (the room's six lights: the fire, three lanterns, the door sconces, the lake).
    pos = [0.0, 1.6, -hz + 0.9]
    lights.append({"id": "window_lake", "pos": pos, "color": "#78AEC8", "intensity": 3.0, "range": 6.5,
                   "ripple": FI.seed_of(pos)})
    for x in (-3.6, 3.6):
        curtains(m, north, north.openings[0 if x < 0 else 1], 2.84, "silk", rod="silver", width=0.3)
    mats = {
        "stone": MatSpec("#7A7064", ("masonry", dict(base="#8A7F6E", mortar="#564D43", row_px=[192, 224, 256],
                                                      block_px=(300, 600), seed=61, grime=0.25, variation=0.08)), 1024, 2.0, 0.92),
        "vault": MatSpec("#71685D", ("masonry", dict(base="#80766A", mortar="#4F473F", row_px=[144, 176], block_px=(240, 480),
                                                      seed=62, grime=0.2, variation=0.07)), 1024, 2.0, 0.92),
        "floor": MatSpec("#5E554C", ("masonry", dict(base="#6A6056", mortar="#423A33", row_px=[256, 192, 320, 256],
                                                      block_px=(200, 420), seed=63, variation=0.1)), 1024, 2.0, 0.85),
        "stone_trim": MatSpec("#8E8473", ("masonry", dict(base="#9D917E", mortar="#61584C", row_px=[96, 128], block_px=(180, 360),
                                                          seed=64, joint_px=1.5, variation=0.06)), 512, 1.0, 0.88),
        "stone_dark": MatSpec("#59514A", None, 64, 1.0, 0.9),
        "door_wood": MatSpec("#522D16", ("planks", dict(base="#552E18", planks_per_tile=5, seed=65, joint_dark=0.5)), 512, 1.0, 0.7),
        "wood_black": MatSpec("#44291A", ("wood_grain", dict(base="#472A19", seed=66)), 512, 1.0, 0.6),
        "silk": MatSpec("#175D30", ("velvet", dict(base="#1A6737", seed=67, folds=5, depth=0.34)), 512, 1.0, 0.55),
        "silver": MatSpec("#B4B1AE", None, 64, 1.0, 0.3, 0.9),
        "brass": MatSpec("#9C7E4E", None, 64, 1.0, 0.45, 0.7),
        "lantern_glow": MatSpec("#FFB45A", None, 64, 1.0, 0.4, 0.0, 3.0),
        "wax": MatSpec("#F1E6C7", None, 64, 1.0, 0.6),
        "flame": MatSpec("#FFC46E", None, 64, 1.0, 0.5, 0.0, 6.0),
        "soot": MatSpec("#393530", None, 64, 1.0, 0.95),
        "fire": FIRE,
        "linen": MatSpec("#D5D1C9", ("weave", dict(base="#D7D2C8", seed=68)), 256, 0.5, 0.9),
        "lake": MatSpec("#34505C", ("lake_view", dict(seed=69)), 256, 1.0, 0.2, 0.0, 0.4, "fit"),
    }
    outline = [(-hx, -hz), (hx, -hz), (hx, hz), (-hx, hz)]
    # Wall areas: between the pilasters on the north wall, either side of
    # the door on the south wall (beyond the pilasters), and either side of
    # the fireplace on the east wall. North and south u run from -x / +x.
    p0, p1 = pil - 0.16 - 0.05, pil + 0.16 + 0.05  # clear of a pilaster
    top = spring - 0.1
    areas = [
        wall_area("wall_north", north, 0.3, top, u_range=(hx - p0, hx + p0)),
        wall_area("wall_south_west", south, 0.3, top, u_range=(hx + p1, 2 * hx - 0.15)),
        wall_area("wall_south_east", south, 0.3, top, u_range=(0.15, hx - p1)),
        wall_area("wall_east_north", east, 0.3, top, u_range=(0.15, hz - 1.0)),
        wall_area("wall_east_south", east, 0.3, top, u_range=(hz + 1.0, 2 * hz - 0.15)),
    ]
    pilasters = [(sx * pil - 0.16, sz * hz - (0.15 if sz > 0 else 0.0), sx * pil + 0.16, sz * hz + (0.0 if sz > 0 else 0.15))
                 for sx in (-1, 1) for sz in (-1, 1)]
    # Table areas: the deep lake-window sills and the mantelpiece.
    tables = []
    for name, o in zip(("sill_west", "sill_east"), north.openings):
        out = o.outline(north.centre + o.offset)
        u0, u1 = min(p[0] for p in out) - 0.06, max(p[0] for p in out) + 0.06
        tables.append(table_area(name, north.at(u0, o.sill, -o.depth + 0.02), north.at(u1, o.sill, 0.12)))
    tables.append(table_area("mantel", east.at(east.centre - 0.86, 1.13, 0.02), east.at(east.centre + 0.86, 1.13, 0.6)))
    return Shell("house_d", "dorm_house_d", "Dungeon Dormitory", "Stone Archway", m, mats, lights,
                 {"sky": "#A08670", "ground": "#4C3E32", "intensity": 0.75}, outline, spring,
                 obstacles=[bed, fbox, *pilasters],
                 wall_areas=areas, table_areas=tables, keep_clear=[(-0.9, hz - 1.3, 0.9, hz)],
                 spawn=(0.0, 0.0, hz - 1.5, math.pi), exit=(0.0, 0.0, hz - 0.7, 1.5))


def _rib(m: Mesh, x: float, arc, width: float, depth: float, material: str) -> None:
    """A transverse arch rib under a barrel vault at x: the vault's arc
    pulled `depth` toward its centre of curvature, with flat sides."""
    half, spring = abs(arc[0][0]), arc[0][1]
    crown = max(y for _, y in arc)
    sag = crown - spring
    cy = crown - (half * half + sag * sag) / (2 * sag)
    inner = []
    for z, y in arc:
        d = mk.normalize((0.0, cy - y, -z))
        inner.append((z + d[2] * depth, y + d[1] * depth))
    x0, x1 = x - width / 2, x + width / 2
    for (za, ya), (zb, yb), (ia, ja), (ib, jb) in zip(arc, arc[1:], inner, inner[1:]):
        m.poly(sk.orient_toward([(x0, ja, ia), (x1, ja, ia), (x1, jb, ib), (x0, jb, ib)], (x, 0.0, 0.0)), material)
        for xs, nx in ((x0, -1.0), (x1, 1.0)):
            side = [(xs, ya, za), (xs, yb, zb), (xs, jb, ib), (xs, ja, ia)]
            m.poly(sk.orient_toward(side, (xs + nx, (ya + ja) / 2, (za + ia) / 2)), material)


SHELLS = {"house_a": house_a, "house_b": house_b, "house_c": house_c, "house_d": house_d}


def build_all() -> dict[str, Shell]:
    return {slot: fn() for slot, fn in SHELLS.items()}
