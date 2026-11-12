"""The instance rooms (pure Python): the Charms classroom and
the duel arena, as shells like the dorm rooms (`dorms.py`) plus the
walkable data written to `data/world/instances/` (`school_layouts.py`).

Local coordinates, metres, Y up, as every instance layout: x east, z south.

* `class_charms`: the Charms classroom, 20 x 17 m (grown x1.25 in the
  castle kit's scale pass, M1.0; tiers, desks and seats stay human scale).
  Tiered rows of desks
  rise from a teaching floor at the front (north) to an entry gallery at
  the back, like a lecture theatre: the floor where the professor stands
  is at y 0 (`ClassDef.professor.pos` [0, 0, -6.5]), three tiers rise
  0.3 m each and the gallery with the door and the two spawns is 1.2 m up.
  Each tier is three rectangles, the wings brought 0.7 m forward, so the
  rows bend round the professor (walkable rectangles, risers drawn on
  their edges, 0.3 m steps everywhere). Thirty seats at curved desks
  (`SEATS`, the desk_rest clip's bench and desk heights), tall arched
  windows down both sides, a stained-glass rose over the blackboard (with
  the lesson's wand movements in chalk, from data/classes/gestures.json),
  the professor's podium of giant stacked books, a vault painted as a
  starry blue sky, floating candles, floating feathers left over from
  practice, house banners and bookcases.
* `duel_arena`: the Dueling Club stage in an instance, 34 x 20 m (the
  spectators' room grown in the scale pass; the stage, the duelists' marks
  and the ring stay, as spell ranges and duel numbers are tuned to them): the
  stage of `kit_school.duel_stage` (16 x 2 m, 0.9 m up, no steps: the
  duelists are placed on it and cannot leave), a flagstone floor round it
  for spectators with benches and a gold line, a slice of the Great Hall
  (stone walls, tall lancet windows of dusk glass, a vault painted as the
  enchanted night sky, floating candles, club banners, torches) and the
  doorway back to the club. Its walkable data is the contract's
  (`ARENA_LAYOUT`): the generator reproduces it exactly.

Art direction for 1d: clean shapes and saturated, stylized colours, no toon
shader yet. Small parts share one `swatch` material (a grid of flat colours,
their UVs on a swatch centre) to keep draw calls down.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

import dorms
import kit_school as ks
import meshkit as mk
import paint
import shell_kit as sk
from castle_kit import fire as FI
from dorms import FIRE, MatSpec
from meshkit import Mesh

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
GESTURES = os.path.join(REPO, "data", "classes", "gestures.json")

# The desk_rest / desk_practice clips (characters/charlib/anim_spells.py):
# bench top under the hips, desk top, desk front edge ahead of the hips.
SEAT_HEIGHT, DESK_HEIGHT, DESK_FRONT = 0.46, 0.76, 0.22
# Castle D1 (seated characters): the desk's near edge (its lip, top
# and shelf) sits this much further forward, for knee room at 1.88 m; its far
# edge stays, so the desk never overhangs the tier's front.
DESK_KNEE = 0.08

# Swatch colours of the shared `swatch` material (indices are positions).
SWATCHES = [
    "#F2E6C8",  # 0 candle wax / ivory
    "#E8DCC0",  # 1 page edges
    "#2F4FC8",  # 2 royal blue
    "#1C2E7A",  # 3 deep blue
    "#E2B33C",  # 4 gold
    "#C83A3A",  # 5 scarlet
    "#3A9A5A",  # 6 green
    "#7A3FD0",  # 7 violet
    "#E07A2A",  # 8 orange
    "#2A8AB8",  # 9 teal
    "#8A1414",  # 10 house_a cloth
    "#D3A625",  # 11 house_a trim
    "#ECB939",  # 12 house_b cloth
    "#372E29",  # 13 house_b trim
    "#16275E",  # 14 house_c cloth
    "#B07A35",  # 15 house_c trim
    "#1A5A32",  # 16 house_d cloth
    "#B8BCC2",  # 17 house_d trim
    "#5A3420",  # 18 dark wood
    "#8A5634",  # 19 warm wood
    "#3A3B44",  # 20 iron
    "#F6F0FF",  # 21 feather white
    "#6A2A6A",  # 22 plum
    "#B85A2A",  # 23 rust
]
HOUSE_SWATCH = {"house_a": (10, 11), "house_b": (12, 13), "house_c": (14, 15), "house_d": (16, 17)}


@dataclass
class Walk:
    """One walkable rectangle of a room's layout."""

    id: str
    x0: float
    z0: float
    x1: float
    z1: float
    y: float
    ceiling: float


@dataclass
class Room:
    layout_id: str
    cell: str
    name: str
    mesh: Mesh
    materials: dict[str, MatSpec]
    lights: list[dict]  # most important first
    ambient: dict
    layout: dict  # the instance layout (data/world/instances/<layout_id>.json)
    walks: list[Walk] = field(default_factory=list)
    seats: list[dict] = field(default_factory=list)
    views: dict = field(default_factory=dict)  # review renders: name -> (eye, target, lens)


# ---------------------------------------------------------------------------
# Shared helpers


def swatch(m: Mesh, mark, table: dict) -> None:
    """Moves the faces added since `mark` onto the `swatch` material: `table`
    maps (material, zone) or material to a swatch index; faces whose
    material is not in the table keep it (mapped through `rename` later)."""
    for f in m.faces[mark[1]:]:
        idx = table.get((f.material, f.zone), table.get(f.material))
        if idx is None:
            continue
        f.material = "swatch"
        f.uvs = [paint.swatch_uv(idx)] * len(f.idx)
        f.zone = 0


def rename(m: Mesh, mark, table: dict) -> None:
    """Gives faces added since `mark` room materials: (tile, zone) or tile -> material."""
    for f in m.faces[mark[1]:]:
        f.material = table.get((f.material, f.zone), table.get(f.material, f.material))
        f.zone = 0


def kit_part(m: Mesh, kit, offset=(0.0, 0.0, 0.0), yaw: float = 0.0, materials: dict | None = None,
             swatches: dict | None = None) -> None:
    """Places a castle kit model (`kit_models` / `kit_school`) in a room:
    its tiles become room materials (`materials`, keyed (tile, zone) or tile)
    or swatches (`swatches`, same keys)."""
    mark = m.mark()
    m.extend(kit.mesh)
    swatch(m, mark, swatches or {})
    rename(m, mark, materials or {})
    m.place(mark, yaw, offset)


def floor_rect(m: Mesh, x0, z0, x1, z1, y, mat) -> None:
    m.poly([(x0, y, z1), (x1, y, z1), (x1, y, z0), (x0, y, z0)], mat, [(x0, -z1), (x1, -z1), (x1, -z0), (x0, -z0)])


def risers(m: Mesh, walks: list[Walk], mat: str, nosing: str | None = None) -> None:
    """Vertical faces where a walkable rectangle stands above its neighbour,
    facing the lower side, with a nosing strip along the top edge."""
    eps = 1e-6
    for s in walks:
        for axis, c, sign in (("z", s.z0, -1), ("z", s.z1, 1), ("x", s.x0, -1), ("x", s.x1, 1)):
            lo_t, hi_t = (s.x0, s.x1) if axis == "z" else (s.z0, s.z1)
            for o in walks:
                if o is s or o.y >= s.y - eps:
                    continue
                oc = (o.z1 if sign < 0 else o.z0) if axis == "z" else (o.x1 if sign < 0 else o.x0)
                if abs(oc - c) > eps:
                    continue
                a, b = (max(lo_t, o.x0), min(hi_t, o.x1)) if axis == "z" else (max(lo_t, o.z0), min(hi_t, o.z1))
                if b - a < 0.01:
                    continue
                if axis == "z":
                    pts = [(a, o.y, c), (b, o.y, c), (b, s.y, c), (a, s.y, c)]
                    want = (0.0, 0.0, float(sign))
                else:
                    pts = [(c, o.y, a), (c, o.y, b), (c, s.y, b), (c, s.y, a)]
                    want = (float(sign), 0.0, 0.0)
                if mk.dot(mk.newell(pts), want) < 0:
                    pts.reverse()
                uvs = [((p[0] if axis == "z" else p[2]), p[1]) for p in pts]
                m.poly(pts, mat, uvs)
                if nosing:
                    if axis == "z":
                        z0, z1 = sorted((c, c + sign * 0.035))
                        mk.box(m, (a, s.y - 0.03, z0), (b, s.y + 0.004, z1), nosing, skip=("-y",))
                    else:
                        x0, x1 = sorted((c, c + sign * 0.035))
                        mk.box(m, (x0, s.y - 0.03, a), (x1, s.y + 0.004, b), nosing, skip=("-y",))


def sub_facet(facet: sk.Facet, u0: float, u1: float) -> sk.Facet:
    """A piece of a facet (for placing hangings off its centre)."""
    return sk.Facet(facet.at(u0, 0.0), facet.at(u1, 0.0), facet.n, facet.bottom, facet.top)


def door_straps(m: Mesh, facet: sk.Facet, o: sk.Opening) -> None:
    """Iron straps and a ring pull on a door leaf whose sill may be above
    the facet's foot (a door onto a raised gallery)."""
    out = o.outline(facet.centre + o.offset)
    u0, u1 = min(p[0] for p in out), max(p[0] for p in out)
    w = -o.depth + 0.01
    for v in (o.sill + 0.35, o.sill + 1.45):
        facet.local_box(m, u0 + 0.04, u1 - 0.04, v, v + 0.07, w, w + 0.015, "iron")
    mark = m.mark()
    mk.lathe(m, [(0.05, 0.0), (0.06, 0.006), (0.05, 0.012)], 8, "iron", cap_top=False)
    m.transform(mark, lambda p: facet.at(u1 - 0.22 + p[0], o.sill + 1.0 - p[2], w + 0.02 + p[1]))


def candle_cloud(m: Mesh, x0, x1, z0, z1, y0, y1, count: int, seed: int) -> None:
    """Floating candles scattered in a box, wax on the swatch, flames lit."""
    s = seed
    for i in range(count):
        vals = []
        for _ in range(4):
            s = (s * 1103515245 + 12345) % 2**31
            vals.append((s >> 8) % 10000 / 10000)
        x, z, y = x0 + (x1 - x0) * vals[0], z0 + (z1 - z0) * vals[1], y0 + (y1 - y0) * vals[2]
        hgt = 0.2 + 0.14 * vals[3]
        mark = m.mark()
        mk.cylinder(m, (x, y, z), 0.03, hgt, 6, "wax", cap_bottom=True)
        swatch(m, mark, {"wax": 0})
        FI.note(m, candles=[(x, y + hgt + 0.004, z, 0.11)])  # the flame: the client's animated fire


def feather(m: Mesh, centre, yaw: float, tilt: float, length: float = 0.32, mat: str = "glow") -> None:
    """A white quill floating in the air (two-sided vane), for the room's
    leftover levitation practice."""
    mark = m.mark()
    n = 6
    for i in range(n):
        t0, t1 = i / n, (i + 1) / n
        w0 = 0.055 * math.sin(math.pi * min(1.0, t0 * 1.15))
        w1 = 0.055 * math.sin(math.pi * min(1.0, t1 * 1.15))
        for sgn in (-1, 1):
            q = [((t0 - 0.5) * length, 0.0, 0.0), ((t1 - 0.5) * length, 0.0, 0.0), ((t1 - 0.5) * length, 0.0, sgn * w1),
                 ((t0 - 0.5) * length, 0.0, sgn * w0)]
            m.poly(q, mat)
            m.poly(list(reversed(q)), mat)
    ct, st = math.cos(tilt), math.sin(tilt)
    m.transform(mark, lambda p: (p[0] * ct - p[1] * st, p[0] * st + p[1] * ct, p[2]))
    m.place(mark, yaw, centre)


# ---------------------------------------------------------------------------
# The Charms classroom


CX0, CX1, CZ0, CZ1 = -10.0, 10.0, -8.25, 8.75
CLASS_SPRING, CLASS_CROWN, CLASS_CEILING = 7.75, 10.75, 8.75
WING_X, WING_FORWARD = 3.0, 0.7  # tiers: centre half width, wings brought forward
TIER_FRONTS = (0.75, 2.25, 3.75, 5.25)  # front (north) edge of tiers 1-3 and the gallery, centre part
CLASS_PIERS = (-2.25, 2.75)  # transverse ribs and pilasters, between the side windows
TIER_RISE = 0.3


def class_walks() -> list[Walk]:
    """The teaching floor, three tiers and the gallery: per level a centre
    rectangle and two wings 0.7 m further forward."""
    out = [Walk("class_floor", CX0, CZ0, CX1, TIER_FRONTS[0] - WING_FORWARD, 0.0, CLASS_CEILING),
           Walk("class_floor_centre", -WING_X, TIER_FRONTS[0] - WING_FORWARD, WING_X, TIER_FRONTS[0], 0.0, CLASS_CEILING)]
    names = ["class_tier_1", "class_tier_2", "class_tier_3", "class_gallery"]
    for k, name in enumerate(names):
        y = round(TIER_RISE * (k + 1), 3)
        a = TIER_FRONTS[k]
        top = k == len(names) - 1
        b = CZ1 if top else TIER_FRONTS[k + 1]
        ceil = round(CLASS_CEILING - y, 3)
        out.append(Walk(name, -WING_X, a, WING_X, b, y, ceil))
        bw = CZ1 if top else b - WING_FORWARD
        out.append(Walk(f"{name}_west", CX0, a - WING_FORWARD, -WING_X, bw, y, ceil))
        out.append(Walk(f"{name}_east", WING_X, a - WING_FORWARD, CX1, bw, y, ceil))
    return out


# A desk bench reaches this far ahead of its hips mark (its front edge) and behind it (desk_row).
BENCH_FRONT, BENCH_BACK = 0.13, 0.23


def class_seats() -> list[dict]:
    """Thirty seats (hips over a bench, facing the front) on tiers 1-3:
    four in the centre, three in each wing, behind their desks; with the
    bench's fit (`front`, `back`, `drop`: client/src/game/castle/seats.ts)."""
    seats = []
    for k in range(3):
        y = round(TIER_RISE * (k + 1), 3)
        for xs, front in (((-1.95, -0.65, 0.65, 1.95), TIER_FRONTS[k]),
                          ((-6.9, -5.5, -4.1), TIER_FRONTS[k] - WING_FORWARD),
                          ((4.1, 5.5, 6.9), TIER_FRONTS[k] - WING_FORWARD)):
            for x in xs:
                seats.append({"pos": [x, y, round(front + 0.77, 3)], "yaw": round(math.pi, 7), "front": BENCH_FRONT,
                              "back": BENCH_BACK, "drop": SEAT_HEIGHT})
    return seats


def desk_row(m: Mesh, x0: float, x1: float, front: float, y: float, seed: int) -> None:
    """A desk with its bench: the desk top's front edge (toward the student)
    DESK_FRONT + DESK_KNEE ahead of the hips, the hips 0.77 m behind the tier's
    front, the desk's far edge DESK_FRONT + 0.5 ahead of them."""
    hips = front + 0.77
    dz1 = hips - DESK_FRONT - DESK_KNEE
    dz0 = hips - DESK_FRONT - 0.5
    top = y + DESK_HEIGHT
    # Desk top with a rounded front lip, a modesty panel facing the row in
    # front, end panels, and a shelf under the top.
    mk.box(m, (x0, top - 0.05, dz0), (x1, top, dz1), "desk")
    mk.box(m, (x0, top - 0.035, dz1), (x1, top - 0.005, dz1 + 0.03), "gold")
    mk.box(m, (x0 + 0.02, y + 0.12, dz0), (x1 - 0.02, top - 0.05, dz0 + 0.04), "desk_dark", skip=("-y",))
    for x in (x0, x1 - 0.05):
        mk.box(m, (x, y, dz0), (x + 0.05, top - 0.05, dz1), "desk_dark", skip=("-y",))
    # A book shelf under the top, shallow and high enough to clear the knees.
    mk.box(m, (x0 + 0.05, top - 0.15, dz0 + 0.04), (x1 - 0.05, top - 0.13, dz0 + 0.25), "desk_dark")
    # Bench (seat 0.36 deep) with a blue cushion and three supports.
    bz0, bz1 = hips - BENCH_FRONT, hips + BENCH_BACK
    mk.box(m, (x0, y + SEAT_HEIGHT - 0.06, bz0), (x1, y + SEAT_HEIGHT - 0.02, bz1), "desk")
    mark = m.mark()
    mk.box(m, (x0 + 0.03, y + SEAT_HEIGHT - 0.02, bz0 + 0.03), (x1 - 0.03, y + SEAT_HEIGHT, bz1 - 0.03), "cushion")
    swatch(m, mark, {"cushion": 2})
    for x in (x0, (x0 + x1) / 2 - 0.03, x1 - 0.06):
        mk.box(m, (x, y, bz0 + 0.03), (x + 0.06, y + SEAT_HEIGHT - 0.06, bz1 - 0.03), "desk_dark", skip=("-y",))
    # Things on the desk: books, inkwells, quills, a few small parcels of
    # practice feathers.
    s = seed
    x = x0 + 0.25
    while x < x1 - 0.3:
        s = (s * 1103515245 + 12345) % 2**31
        kind = s % 4
        mark = m.mark()
        if kind == 0:
            for j in range(1 + (s >> 3) % 3):
                c = 2 + ((s >> (5 + j)) % 8)
                mk.box(m, (x - 0.12 + 0.01 * j, top + 0.04 * j, dz0 + 0.12), (x + 0.1, top + 0.04 * (j + 1), dz0 + 0.38),
                       f"book{c}")
            swatch(m, mark, {f"book{c}": c for c in range(2, 10)})
        elif kind == 1:
            mk.lathe(m, [(0.03, 0.0), (0.035, 0.03), (0.015, 0.05)], 6, "ink", center=(x, top, dz0 + 0.2))
            mk.rod(m, (x + 0.02, top + 0.04, dz0 + 0.2), (x + 0.12, top + 0.22, dz0 + 0.26), 0.006, 3, "quill")
            swatch(m, mark, {"ink": 20, "quill": 21})
        elif kind == 2:
            mk.box(m, (x - 0.14, top, dz0 + 0.1), (x + 0.14, top + 0.004, dz0 + 0.4), "parchment")
            swatch(m, mark, {"parchment": 1})
        x += 0.62


def class_charms() -> Room:
    m = Mesh()
    walks = class_walks()
    # Floors: the teaching floor in polished parquet, the tiers in boards.
    for w in walks:
        floor_rect(m, w.x0, w.z0, w.x1, w.z1, w.y, "floor" if w.y == 0 else "tier")
    risers(m, walks, "desk_dark", nosing="gold")
    # A gold-inlaid circle on the teaching floor where the professor stands.
    mark = m.mark()
    ks._ring(m, (0.0, 0.0, -5.6), 1.5, 1.58, 0.006, "inlay", 0, n=32)
    ks._star(m, (0.0, 0.0, -5.6), 0.9, 0.3, 8, 0.005, "inlay_blue", 0)
    swatch(m, mark, {"inlay": 4, "inlay_blue": 2})
    # Walls: plaster over oak panelling, stone window surrounds.
    win = lambda off: sk.Opening("window", "pointed", 1.8, 3.4, 6.0, 0.7, offset=off, pane="glass", frame="stone",  # noqa: E731
                                 frame_width=0.14, sill_board="stone")
    north = sk.Facet((CX0, 0.0, CZ0), (CX1, 0.0, CZ0), (0.0, 0.0, 1.0), 0.0, CLASS_SPRING,
                     [sk.Opening("window", "circle", 3.5, 0.0, 5.6, 0.6, pane="rose", frame="stone", frame_width=0.18)])
    door = sk.Opening("door", "pointed", 2.2, 1.2, 4.0, 0.6, pane="door_wood", pane_uv="metres", frame="stone",
                      frame_width=0.16)
    south = sk.Facet((CX1, 0.0, CZ1), (CX0, 0.0, CZ1), (0.0, 0.0, -1.0), 0.0, CLASS_SPRING, [door])
    east = sk.Facet((CX1, 0.0, CZ0), (CX1, 0.0, CZ1), (-1.0, 0.0, 0.0), 0.0, CLASS_SPRING, [win(-5.0), win(0.0), win(5.0)])
    west = sk.Facet((CX0, 0.0, CZ1), (CX0, 0.0, CZ0), (1.0, 0.0, 0.0), 0.0, CLASS_SPRING, [win(-5.0), win(0.0), win(5.0)])
    u = 0.0
    for facet in (north, east, south, west):
        facet.build(m, "plaster", 1.0, u)
        u += facet.length
    for facet in (north, east, south, west):
        dorms.band(m, facet, 0.0, 2.55, 0.04, "panel")
        dorms.band(m, facet, 2.55, 2.68, 0.08, "gold")
        dorms.band(m, facet, CLASS_SPRING - 0.3, CLASS_SPRING, 0.12, "stone", avoid_openings=False)
        dorms.skirting(m, facet, 0.18, 0.07, "desk_dark")
    door_straps(m, south, door)
    # Panel stiles every 1.2 m along the panelling.
    for facet in (north, east, south, west):
        n = int(facet.length / 1.2)
        for i in range(1, n):
            uu = facet.length * i / n
            blocked = False
            for o in facet.openings:
                out = o.outline(facet.centre + o.offset)
                if min(p[0] for p in out) - 0.3 < uu < max(p[0] for p in out) + 0.3 and min(p[1] for p in out) < 2.55:
                    blocked = True
            if not blocked:
                facet.local_box(m, uu - 0.04, uu + 0.04, 0.18, 2.55, 0.04, 0.07, "desk_dark", skip=("-z",))
    # Vault along the room (north-south), its lunettes on the end walls,
    # transverse ribs over stone pilasters between the windows.
    mark = m.mark()
    arc = sk.barrel_vault(m, CZ0, CZ1, -CX0, CLASS_SPRING, CLASS_CROWN, 12, "vault")
    for zr in CLASS_PIERS:
        dorms._rib(m, zr, arc, 0.4, 0.16, "stone")
    m.transform(mark, lambda p: (-p[2], p[1], p[0]))
    for z, toward in ((CZ0, 1.0), (CZ1, -1.0)):
        for (za, ya), (zb, yb) in zip(arc, arc[1:]):
            xa, xb = -za, -zb
            pts = [(xa, CLASS_SPRING, z), (xb, CLASS_SPRING, z), (xb, yb, z), (xa, ya, z)]
            if abs(ya - CLASS_SPRING) < 1e-6:
                pts = [pts[0], pts[1], pts[2]]
            pts = sk.orient_toward(pts, (0.0, CLASS_SPRING, z + toward))
            m.poly(pts, "plaster", [(p[0], p[1]) for p in pts])
    for z in CLASS_PIERS:
        for x, nx in ((CX0, 1.0), (CX1, -1.0)):
            x0, x1 = sorted((x, x + nx * 0.16))
            mk.box(m, (x0, 0.0, z - 0.22), (x1, CLASS_SPRING, z + 0.22), "stone", skip=("-y",))
    # The front: blackboard with the lesson's gestures, bookcases, the
    # professor's podium of stacked books, a desk behind.
    zb = CZ0 + 0.06
    m.poly([(-2.1, 1.05, zb), (2.1, 1.05, zb), (2.1, 2.75, zb), (-2.1, 2.75, zb)], "chalk",
           [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    for (a, b) in (((-2.18, 0.97), (2.18, 1.05)), ((-2.18, 2.75), (2.18, 2.83)), ((-2.18, 1.05), (-2.1, 2.75)),
                   ((2.1, 1.05), (2.18, 2.75))):
        mk.box(m, (a[0], a[1], CZ0 + 0.02), (b[0], b[1], CZ0 + 0.1), "gold")
    mk.box(m, (-2.1, 0.92, CZ0 + 0.02), (2.1, 0.97, CZ0 + 0.18), "desk_dark")
    for side in (-1, 1):
        bookcase(m, side * 6.1, CZ0, 4.4, 2.5, seed=31 + side)
    book_podium(m, (0.0, 0.0, -5.65))
    teacher_desk(m, (0.0, 0.0, CZ0 + 0.55))
    # Desks on the tiers.
    for k in range(3):
        y = TIER_RISE * (k + 1)
        desk_row(m, -2.6, 2.6, TIER_FRONTS[k], y, 7 + k)
        for side in (-1, 1):
            x0, x1 = sorted((side * 3.4, side * 7.6))
            desk_row(m, x0, x1, TIER_FRONTS[k] - WING_FORWARD, y, 17 + k * 3 + side)
    # Banners: the four houses on the back wall either side of the door,
    # and the class's own (a feather on royal blue) high on the side walls.
    for slot, u0 in (("house_a", 2.9), ("house_b", 5.0), ("house_c", 13.1), ("house_d", 15.2)):
        mark = m.mark()
        dorms.tapestry(m, sub_facet(south, u0, u0 + 1.9), 1.2, 3.0, 5.4, "cloth", "trim", slot)
        cloth, trim = HOUSE_SWATCH[slot]
        swatch(m, mark, {"cloth": cloth, "trim": trim})
    for facet, u0 in ((east, 5.25), (west, 5.25), (east, 10.25), (west, 10.25)):
        mark = m.mark()
        kit_part(m, ks.duel_banner(height=2.6, width=1.0), materials={}, swatches={("fabric", 1): 2, ("metal", 2): 4,
                                                                                    ("wood", 3): 21})
        f = sub_facet(facet, u0, u0 + 1.5)
        m.transform(mark, lambda p, f=f: f.at(f.centre + p[0], 3.1 + p[1], p[2] + 0.16))  # in front of the pilaster
    # Floating candles and feathers.
    candle_cloud(m, -9.0, 9.0, -7.25, 7.75, 5.5, 7.0, 60, 11)
    s = 5
    for k in range(3):
        for i in range(5):
            s = (s * 1103515245 + 12345) % 2**31
            x = -7.0 + 14.0 * ((s >> 8) % 1000) / 1000
            front = TIER_FRONTS[k] - (WING_FORWARD if abs(x) > WING_X else 0.0)
            feather(m, (x, TIER_RISE * (k + 1) + 1.5 + 0.5 * ((s >> 4) % 100) / 100, front + 0.3), (s % 628) / 100.0,
                    math.radians(-25 + (s % 50)))
    seats = class_seats()
    mats = {
        "floor": MatSpec("#C17A42", ("planks", dict(base="#C8824A", planks_per_tile=10, seed=71, variation=0.08)), 1024, 2.0, 0.45),
        "tier": MatSpec("#A66334", ("planks", dict(base="#B26937", planks_per_tile=8, seed=72, variation=0.09)), 1024, 2.0, 0.6),
        "desk": MatSpec("#98582F", ("wood_grain", dict(base="#A66032", seed=73, contrast=0.14)), 512, 1.0, 0.5),
        "desk_dark": MatSpec("#60331A", ("wood_grain", dict(base="#69381D", seed=74, contrast=0.14)), 512, 1.0, 0.55),
        "panel": MatSpec("#8A3C21", ("planks", dict(base="#933F22", planks_per_tile=6, seed=75, variation=0.06)), 512, 1.2, 0.5),
        "plaster": MatSpec("#EDDCB6", ("plaster", dict(base="#F2E3C0", seed=76, contrast=0.06)), 512, 2.0, 0.9),
        "stone": MatSpec("#CFB89B", ("masonry", dict(base="#D7C1A2", mortar="#A68F75", row_px=[96, 128], block_px=(180, 360),
                                                     seed=77, joint_px=1.5, variation=0.05)), 512, 1.0, 0.85),
        "vault": MatSpec("#3156C8", ("star_dome", dict(base="#2B52CF", star="#FFDE81", seed=78, count=260)), 1024, 5.0, 0.8,
                         0.0, 0.35),
        "gold": MatSpec("#E5B22F", None, 64, 1.0, 0.3, 0.85),
        "iron": MatSpec("#4C4E5A", None, 64, 1.0, 0.55, 0.6),
        "glass": MatSpec("#9CCBFF", ("sky_view", dict(seed=79)), 256, 1.0, 0.15, 0.0, 1.5, "fit"),
        "rose": MatSpec("#8A6AE0", ("stained_rose", dict(seed=80)), 512, 1.0, 0.15, 0.0, 1.8, "fit"),
        "chalk": MatSpec("#26443A", ("chalk_gestures", dict(gestures=lesson_gestures(), seed=81, aspect=4.2 / 1.7)), 1024, 1.0,
                         0.9, 0.0, 0.0, "fit"),
        "door_wood": MatSpec("#7F4320", ("planks", dict(base="#8A4A26", planks_per_tile=5, seed=82, joint_dark=0.55)), 512, 1.0, 0.7),
        "books": MatSpec("#8A4A3A", ("book_spines", dict(h=512, seed=83)), 512, 1.0, 0.8, 0.0, 0.0, "fit"),
        "swatch": MatSpec("#808080", ("swatches", dict(colours=SWATCHES)), 256, 1.0, 0.7, 0.0, 0.0, "fit"),
        "glow": MatSpec("#F6F0FF", None, 64, 1.0, 0.4, 0.0, 1.6),
        "fire": FIRE,
    }
    lights = [
        {"id": "candles_front", "kind": "point", "pos": [0.0, 6.2, -3.75], "color": "#FFC46E", "intensity": 8.0, "range": 14.0},
        {"id": "window_east", "kind": "point", "pos": [8.0, 5.0, 0.25], "color": "#D6E8FF", "intensity": 7.0, "range": 15.0},
        {"id": "window_west", "kind": "point", "pos": [-8.0, 5.0, 0.25], "color": "#D6E8FF", "intensity": 7.0, "range": 15.0},
        {"id": "candles_back", "kind": "point", "pos": [0.0, 6.5, 4.4], "color": "#FFC46E", "intensity": 7.0, "range": 12.5},
        {"id": "rose", "kind": "point", "pos": [0.0, 5.6, -7.35], "color": "#B49CFF", "intensity": 5.0, "range": 10.0},
        {"id": "board", "kind": "point", "pos": [0.0, 2.6, -7.15], "color": "#FFD596", "intensity": 3.0, "range": 7.0},
    ]
    layout = {
        "version": 1,
        "surfaces": [{"id": w.id, "kind": "room", "name": "Charms Classroom", "level": 0, "cell": "class_charms",
                      "min": [round(w.x0, 3), round(w.z0, 3)], "max": [round(w.x1, 3), round(w.z1, 3)], "y": round(w.y, 3),
                      "ceiling": round(w.ceiling, 3)} for w in walks],
        "spawns": [{"id": "class_entry_a", "pos": [-1.0, 1.2, 5.9], "yaw": 3.1415927},
                   {"id": "class_entry_b", "pos": [1.0, 1.2, 5.9], "yaw": 3.1415927}],
        "landmarks": [{"id": "class_professor", "name": "Professor's Desk", "pos": [0.0, 0.0, -6.5]}],
        "interactables": [{"id": "class_exit", "kind": "instance_exit", "name": "Classroom Door", "pos": [0.0, 1.2, 8.15],
                           "yaw": 3.1415927, "radius": 1.5}],
    }
    views = {
        "entry": ((0.0, 3.0, 8.5), (0.0, 1.0, -6.25), 15),
        "front": ((-1.5, 1.7, -6.75), (1.5, 1.6, 7.5), 14),
        "side": ((8.75, 3.5, -7.0), (-5.0, 1.0, 5.0), 15),
        "tiers": ((-8.25, 1.65, 2.6), (6.25, 1.2, -1.25), 14),
        "up": ((0.0, 1.6, 2.5), (0.0, 10.0, -3.75), 12),
    }
    return Room("class_charms", "class_charms", "Charms Classroom", m, mats, lights,
                {"sky": "#CDDFF6", "ground": "#8A5E39", "intensity": 0.75}, layout, walks, seats, views)


def lesson_gestures() -> list:
    """The four class gestures' templates for the blackboard, in lesson order."""
    with open(GESTURES, encoding="utf-8") as f:
        by_key = {g["key"]: g["points"] for g in json.load(f)["gestures"]}
    return [by_key[k] for k in ("twirl_and_tap", "lucerna_flick", "circle", "zigzag") if k in by_key]


def bookcase(m: Mesh, cx: float, wall_z: float, width: float, height: float, seed: int) -> None:
    """A tall bookcase against the north wall, its shelves full of books."""
    hw, d = width / 2, 0.42
    z0, z1 = wall_z + 0.02, wall_z + 0.02 + d
    mk.box(m, (cx - hw, 0.0, z0), (cx - hw + 0.06, height, z1), "desk_dark", skip=("-y",))
    mk.box(m, (cx + hw - 0.06, 0.0, z0), (cx + hw, height, z1), "desk_dark", skip=("-y",))
    mk.box(m, (cx - hw - 0.04, height, z0), (cx + hw + 0.04, height + 0.12, z1 + 0.04), "desk_dark")
    mk.box(m, (cx - hw, 0.0, z0), (cx + hw, height, z0 + 0.02), "desk_dark", skip=("-y",))
    shelves = 5
    for i in range(shelves + 1):
        y = 0.08 + i * (height - 0.16) / shelves
        mk.box(m, (cx - hw + 0.06, y - 0.03, z0), (cx + hw - 0.06, y, z1), "desk", skip=("-y",) if i == 0 else ())
        if i < shelves:
            top = y + (height - 0.16) / shelves - 0.06
            row = [(cx - hw + 0.08, y, z1 - 0.04), (cx + hw - 0.08, y, z1 - 0.04), (cx + hw - 0.08, top, z1 - 0.04),
                   (cx - hw + 0.08, top, z1 - 0.04)]
            u0 = ((seed * 7 + i * 3) % 4) / 4
            m.poly(row, "books", [(u0, 0.0), (u0 + 0.5, 0.0), (u0 + 0.5, 1.0), (u0, 1.0)])


def book_podium(m: Mesh, base) -> None:
    """The professor's podium: giant books stacked to a reading height,
    each turned a little, an open book on top with a quill stand."""
    bx, _, bz = base
    y = 0.0
    sizes = [(1.3, 0.2, 0.95, 5), (1.2, 0.18, 0.9, 7), (1.25, 0.22, 0.92, 3), (1.1, 0.17, 0.82, 6), (1.05, 0.16, 0.8, 23)]
    for i, (w, h, d, colour) in enumerate(sizes):
        yaw = math.radians((-4, 5, -2, 7, -5)[i])
        mark = m.mark()
        # Cover boards top and bottom, a page block between, the spine.
        mk.box(m, (-w / 2, 0.0, -d / 2), (w / 2, 0.025, d / 2), "cover")
        mk.box(m, (-w / 2, h - 0.025, -d / 2), (w / 2, h, d / 2), "cover")
        mk.box(m, (-w / 2 + 0.02, 0.025, -d / 2 + 0.03), (w / 2 - 0.03, h - 0.025, d / 2 - 0.02), "pages")
        mk.box(m, (-w / 2, 0.0, -d / 2), (-w / 2 + 0.04, h, d / 2), "cover", skip=())
        for zz in (-d / 3, 0.0, d / 3):
            mk.box(m, (-w / 2 - 0.005, h * 0.15, zz - 0.02), (-w / 2 + 0.04, h * 0.85, zz + 0.02), "band")
        swatch(m, mark, {"cover": colour, "pages": 1, "band": 4})
        m.place(mark, yaw, (bx, y, bz))
        y += h
    # The open book on top, tilted toward the professor (who stands north).
    mark = m.mark()
    for sgn in (-1, 1):
        page = [(0.0, 0.03, -0.25), (sgn * 0.36, 0.06, -0.25), (sgn * 0.36, 0.06, 0.25), (0.0, 0.03, 0.25)]
        m.poly(page if mk.newell(page)[1] > 0 else list(reversed(page)), "pages")
    mk.box(m, (-0.38, 0.0, -0.27), (0.38, 0.03, 0.27), "cover")
    swatch(m, mark, {"pages": 1, "cover": 2})
    m.transform(mark, lambda p: (p[0], p[1] * math.cos(0.15) + p[2] * math.sin(0.15), -p[1] * math.sin(0.15) + p[2] * math.cos(0.15)))
    m.place(mark, 0.0, (bx, y, bz))
    mark = m.mark()
    mk.lathe(m, [(0.04, 0.0), (0.045, 0.04), (0.02, 0.06)], 6, "ink", center=(bx + 0.45, y, bz + 0.1))
    mk.rod(m, (bx + 0.46, y + 0.05, bz + 0.1), (bx + 0.55, y + 0.35, bz + 0.18), 0.008, 3, "quill")
    swatch(m, mark, {"ink": 20, "quill": 21})


def teacher_desk(m: Mesh, base) -> None:
    """A long desk against the front wall behind the professor: candles, a
    globe of the heavens, piles of books."""
    bx, _, bz = base
    w, d, h = 2.6, 0.7, 0.85
    mk.box(m, (bx - w / 2, h - 0.06, bz - d / 2), (bx + w / 2, h, bz + d / 2), "desk")
    for sx in (-1, 1):
        x0, x1 = sorted((bx + sx * (w / 2 - 0.04), bx + sx * (w / 2 - 0.55)))
        mk.box(m, (x0, 0.0, bz - d / 2 + 0.04), (x1, h - 0.06, bz + d / 2 - 0.04), "desk_dark", skip=("-y",))
    mark = m.mark()
    for i, (dx, c) in enumerate(((-0.95, 5), (-0.7, 9), (0.8, 6))):
        mk.box(m, (bx + dx - 0.15, h + 0.0, bz - 0.15), (bx + dx + 0.12, h + 0.07 + 0.03 * i, bz + 0.15), f"b{c}")
    swatch(m, mark, {"b5": 5, "b9": 9, "b6": 6})
    # Globe on a brass stand.
    mark = m.mark()
    mk.lathe(m, [(0.1, 0.0), (0.03, 0.04), (0.02, 0.2)], 8, "brass", center=(bx + 0.3, h, bz))
    prof = [(0.17 * math.sin(math.pi * i / 6), -0.17 * math.cos(math.pi * i / 6)) for i in range(7)]
    mk.lathe(m, prof, 10, "globe", center=(bx + 0.3, h + 0.38, bz))
    swatch(m, mark, {"brass": 4, "globe": 3})
    for dx in (-0.35, -0.2):
        mark = m.mark()
        mk.cylinder(m, (bx + dx, h, bz + 0.1), 0.025, 0.25 + 0.1 * (dx > -0.3), 6, "wax")
        swatch(m, mark, {"wax": 0})
        # The flame: the client's animated fire (docs/architecture.md "Rendering").
        FI.note(m, candles=[(bx + dx, h + 0.26 + 0.1 * (dx > -0.3), bz + 0.1, 0.1)])


# ---------------------------------------------------------------------------
# The duel arena


AX, AZ = 17.0, 10.0
ARENA_SPRING, ARENA_CROWN = 10.0, 14.0
# The contract's walkable data (data/world/instances/duel_arena.json), kept
# exactly: the stage the duelists stand on and the floor round it.
ARENA_LAYOUT = {
    "version": 1,
    "surfaces": [
        {"id": "arena_stage", "kind": "room", "name": "Dueling Stage", "level": 0, "cell": "duel_arena", "min": [-8.0, -1.0],
         "max": [8.0, 1.0], "y": 0.9, "ceiling": 11.1},
        {"id": "arena_floor_north", "kind": "room", "name": "Dueling Club", "level": 0, "cell": "duel_arena",
         "min": [-17.0, -10.0], "max": [17.0, -1.0], "y": 0, "ceiling": 12.0},
        {"id": "arena_floor_south", "kind": "room", "name": "Dueling Club", "level": 0, "cell": "duel_arena",
         "min": [-17.0, 1.0], "max": [17.0, 10.0], "y": 0, "ceiling": 12.0},
        {"id": "arena_floor_west", "kind": "room", "name": "Dueling Club", "level": 0, "cell": "duel_arena",
         "min": [-17.0, -1.0], "max": [-8.0, 1.0], "y": 0, "ceiling": 12.0},
        {"id": "arena_floor_east", "kind": "room", "name": "Dueling Club", "level": 0, "cell": "duel_arena",
         "min": [8.0, -1.0], "max": [17.0, 1.0], "y": 0, "ceiling": 12.0},
    ],
    "spawns": [
        {"id": "arena_spectators_north", "pos": [0.0, 0.0, -4.0], "yaw": 0.0},
        {"id": "arena_spectators_south", "pos": [0.0, 0.0, 4.0], "yaw": 3.1415927},
        {"id": "arena_duelist_a", "pos": [-5.5, 0.9, 0.0], "yaw": 1.5707964},
        {"id": "arena_duelist_b", "pos": [5.5, 0.9, 0.0], "yaw": 4.712389},
    ],
    "landmarks": [{"id": "arena_stage", "name": "Dueling Stage", "pos": [0.0, 0.9, 0.0]}],
    "interactables": [{"id": "arena_exit", "kind": "instance_exit", "name": "Back to the Club", "pos": [0.0, 0.0, -9.4],
                       "yaw": 0.0, "radius": 1.5}],
}

# Kit tiles of the stage and its dressing -> arena materials.
STAGE_MATERIALS = {("wood", 1): "deck", ("wood", 2): "deck", ("wood", 6): "wood_dark", ("wood", 7): "panel_red",
                   ("fabric", 3): "royal", ("fabric", 5): "gold", ("metal", 4): "gold", ("glow", 8): "glow_blue"}


def duel_arena() -> Room:
    m = Mesh()
    floor_rect(m, -AX, -AZ, AX, AZ, 0.0, "floor")
    kit_part(m, ks.duel_stage(length=16.0, width=2.0, height=0.9, steps=0.0), materials=STAGE_MATERIALS)
    # Gold line round the stage, crests on the floor at both ends.
    kit_part(m, ks.duel_area_inlay(width=18.0, depth=4.0), materials={("metal", 1): "gold", ("fabric", 2): "royal"})
    for x in (-10.6, 10.6):
        ks._ring(m, (x, 0.0, 0.0), 0.8, 0.9, 0.006, "gold", 0, n=32)
        ks._star(m, (x, 0.0, 0.0), 0.7, 0.25, 8, 0.005, "royal", 0)
    # Walls: stone, lancet windows of dusk glass, the doorway north.
    lancet = lambda off: sk.Opening("window", "pointed", 2.0, 3.6, 7.8, 0.8, offset=off, pane="glass",  # noqa: E731
                                    frame="stone_trim", frame_width=0.16, sill_board="stone_trim")
    door = sk.Opening("door", "pointed", 3.0, 0.0, 3.8, 0.8, pane="door_wood", pane_uv="metres", frame="stone_trim",
                      frame_width=0.2)
    north = sk.Facet((-AX, 0.0, -AZ), (AX, 0.0, -AZ), (0.0, 0.0, 1.0), 0.0, ARENA_SPRING,
                     [lancet(-11.5), lancet(-5.75), door, lancet(5.75), lancet(11.5)])
    south = sk.Facet((AX, 0.0, AZ), (-AX, 0.0, AZ), (0.0, 0.0, -1.0), 0.0, ARENA_SPRING,
                     [lancet(-11.5), lancet(-5.75), lancet(5.75), lancet(11.5)])
    east = sk.Facet((AX, 0.0, -AZ), (AX, 0.0, AZ), (-1.0, 0.0, 0.0), 0.0, ARENA_SPRING)
    west = sk.Facet((-AX, 0.0, AZ), (-AX, 0.0, -AZ), (1.0, 0.0, 0.0), 0.0, ARENA_SPRING)
    u = 0.0
    for facet in (north, east, south, west):
        facet.build(m, "stone", 1.0, u)
        u += facet.length
        dorms.skirting(m, facet, 0.3, 0.06, "stone_trim")
        dorms.band(m, facet, 2.6, 2.75, 0.08, "stone_trim")
        dorms.band(m, facet, ARENA_SPRING - 0.35, ARENA_SPRING, 0.14, "stone_trim", avoid_openings=False)
    dorms.door_details(m, north, door, "iron")
    arc = sk.barrel_vault(m, -AX, AX, AZ, ARENA_SPRING, ARENA_CROWN, 14, "vault")
    ribs = (-14.375, -8.625, -2.875, 2.875, 8.625, 14.375)
    for x in ribs:
        dorms._rib(m, x, arc, 0.42, 0.2, "wood_dark")
        for z, nz in ((-AZ, 1.0), (AZ, -1.0)):
            z0, z1 = sorted((z, z + nz * 0.2))
            mk.box(m, (x - 0.24, 0.0, z0), (x + 0.24, ARENA_SPRING, z1), "stone_trim", skip=("-y",))
    for x, sign in ((AX, -1.0), (-AX, 1.0)):
        for (za, ya), (zb, yb) in zip(arc, arc[1:]):
            pts = [(x, ARENA_SPRING, za), (x, ARENA_SPRING, zb), (x, yb, zb), (x, ya, za)]
            if abs(ya - ARENA_SPRING) < 1e-6:
                pts = [pts[0], pts[1], pts[2]]
            pts = sk.orient_toward(pts, (0.0, ARENA_SPRING, 0.0))
            m.poly(pts, "stone", [(p[2] * -sign, p[1]) for p in pts])
    # Club banners: the end walls and over the south spawn.
    banner_mats = {("fabric", 1): "royal", ("metal", 2): "gold", ("wood", 3): "panel_red"}
    for facet, w_, h_ in ((east, 3.0, 6.0), (west, 3.0, 6.0), (south, 2.0, 4.0)):
        mark = m.mark()
        kit_part(m, ks.duel_banner(height=h_, width=w_ * 0.8), materials=banner_mats)
        m.transform(mark, lambda p, f=facet: f.at(f.centre + p[0], 2.8 + p[1], p[2]))
    # Torches either side of the end banners; benches for spectators.
    lights = []
    for facet in (east, west):
        for du in (-4.0, 4.0):
            pos = facet.at(facet.centre + du, 0.0, 0.0)
            mark = m.mark()
            lt = dorms.sconce(m, (pos[0], 2.4, pos[2]), sk.yaw_facing(facet.n), "iron", "fire", "wax")
            swatch(m, mark, {"wax": 0})
            if du > 0:
                continue  # one light per end wall: the room stays within six
            lights.append({"id": f"torch_{'e' if facet is east else 'w'}{'n' if du < 0 else 's'}", "kind": "point",
                           "pos": lt["pos"], "color": "#FFAE5C", "intensity": 3.0, "range": 7.0})
    for z, yaw in ((-7.5, 0.0), (7.5, math.pi)):
        for x in (-6.5, 6.5):
            kit_part(m, ks.spectator_bench(6.0), (x, 0.0, z), yaw,
                     materials={("wood", 1): "deck", ("fabric", 2): "royal", ("wood", 3): "wood_dark"})
    candle_cloud(m, -15.0, 15.0, -8.0, 8.0, 6.6, 8.4, 80, 23)
    mats = {
        "floor": MatSpec("#A88C6E", ("masonry", dict(base="#B0946F", mortar="#7A6450", row_px=[256, 192, 320, 256],
                                                     block_px=(200, 420), seed=91, variation=0.08)), 1024, 2.0, 0.8),
        "stone": MatSpec("#CDB894", ("masonry", dict(base="#D4BF9A", mortar="#9A8466", row_px=[224, 256, 288],
                                                     block_px=(320, 640), seed=92, grime=0.08, variation=0.06)), 1024, 2.0, 0.88),
        "stone_trim": MatSpec("#EADBBE", ("masonry", dict(base="#EEDFC2", mortar="#B5A080", row_px=[96, 128],
                                                          block_px=(180, 360), seed=93, joint_px=1.5, variation=0.05)), 512, 1.0, 0.85),
        "vault": MatSpec("#1C2B7C", ("star_dome", dict(base="#1E308A", star="#FFDE81", seed=94, count=320)), 1024, 6.0, 0.8,
                         0.0, 0.45),
        "glass": MatSpec("#7A5AC8", ("sky_view", dict(seed=95, night=True)), 256, 1.0, 0.15, 0.0, 1.5, "fit"),
        "door_wood": MatSpec("#714223", ("planks", dict(base="#7C4726", planks_per_tile=5, seed=96, joint_dark=0.55)), 512, 1.0, 0.7),
        "deck": MatSpec("#985A32", ("planks", dict(base="#A16437", planks_per_tile=8, seed=97, variation=0.08)), 512, 1.0, 0.5),
        "wood_dark": MatSpec("#60361D", ("wood_grain", dict(base="#69381D", seed=98, contrast=0.14)), 512, 1.0, 0.55),
        "panel_red": MatSpec("#7C381E", ("wood_grain", dict(base="#853B1C", seed=99, contrast=0.12)), 512, 1.0, 0.5),
        "royal": MatSpec("#2448CF", ("velvet", dict(base="#274CD6", seed=100, folds=3, depth=0.12)), 256, 1.0, 0.75),
        "gold": MatSpec("#E5B22F", None, 64, 1.0, 0.3, 0.85),
        "iron": MatSpec("#4C4E5A", None, 64, 1.0, 0.55, 0.6),
        "glow_blue": MatSpec("#7FC8FF", None, 64, 1.0, 0.3, 0.0, 3.0),
        "swatch": MatSpec("#808080", ("swatches", dict(colours=SWATCHES)), 256, 1.0, 0.7, 0.0, 0.0, "fit"),
        "fire": FIRE,
    }
    lights = [
        {"id": "stage_west", "kind": "point", "pos": [-7.9, 2.0, 0.0], "color": "#8CC8FF", "intensity": 10.0, "range": 11.0},
        {"id": "stage_east", "kind": "point", "pos": [7.9, 2.0, 0.0], "color": "#8CC8FF", "intensity": 10.0, "range": 11.0},
        {"id": "candles_west", "kind": "point", "pos": [-8.0, 7.2, 0.0], "color": "#FFC46E", "intensity": 8.0, "range": 15.0},
        {"id": "candles_east", "kind": "point", "pos": [8.0, 7.2, 0.0], "color": "#FFC46E", "intensity": 8.0, "range": 15.0},
    ] + lights
    views = {
        "entry": ((0.0, 1.7, -8.6), (0.0, 1.2, 5.0), 14),
        "stage": ((-7.6, 2.4, 0.0), (8.0, 1.4, 0.0), 16),
        "side": ((12.5, 3.5, 9.0), (-6.0, 1.0, -2.0), 14),
        "spectator": ((-3.0, 1.65, 7.0), (3.0, 1.4, -1.0), 16),
        "up": ((0.0, 1.6, 4.0), (0.0, 13.0, -3.0), 12),
    }
    walks = [Walk(s["id"], s["min"][0], s["min"][1], s["max"][0], s["max"][1], float(s["y"]), s["ceiling"])
             for s in ARENA_LAYOUT["surfaces"]]
    return Room("duel_arena", "duel_arena", "Dueling Club", m, mats, lights,
                {"sky": "#6A58B9", "ground": "#60493B", "intensity": 0.85}, json.loads(json.dumps(ARENA_LAYOUT)), walks, [],
                views)


ROOMS = {"class_charms": class_charms, "duel_arena": duel_arena}


def build_all() -> dict[str, Room]:
    return {lid: fn() for lid, fn in ROOMS.items()}
