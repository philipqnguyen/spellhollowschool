"""Solid furniture and props (docs/architecture.md "Layouts"):
the blockers a character cannot walk through, measured off the pieces' own
geometry. Pure Python, no `bpy`, deterministic.

    python3 -I tools/blender/world/solids.py            # report
    python3 -I tools/blender/world/solids.py --write    # rewrite the layouts' "solids"
    python3 -I tools/blender/world/solids.py --check    # exit 1 if a layout's "solids" is stale

Every placed piece of furniture or dressing (the castle kit's hero pieces and
props.json kit models in `data/world/castle_kit.json` / `props.json`, the
instance rooms' kits, the grounds' dressing and foliage, the pitch's hoops
and benches) is built the way the exporters build it, and its faces are cut
to the band a walking character occupies: from `STEP` (sim-core's step
height: anything lower is stepped over) to `HEAD` above the floor under it
(anything higher hangs overhead). What is left, in plan, is the blocker: one
box per part of the piece (parts closer than `GAP` are one: no character fits
between them), in the piece's own axes (an oriented box when it stands at an
angle that is not a quarter turn), or the ellipse inscribed in that box when
the part is round (a fountain's basin, a round table, a tree's trunk). The
boxes go in each layout's `solids` list (`sim_core::layout::Blocker`, one per
line, ids `<cell or room>/<piece id>[.<part>]`), where sim-core takes the
ground under them away for movement and the Monstra Viam trail.

What is never solid (`SKIP`): doors, gates and secret entrances (they open),
arches, windows and other architecture already walled by the layout,
overhead pieces, rugs and floor inlays, things on tables, and the buildings
the grounds already leave out of their walkable ground. A seat's bench or
chair stays solid unless that leaves one of its seats with nowhere to stand
within the client's sitting reach (`SEAT_REACH`): then it is left walkable
and listed in the report.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
for _p in (BLENDER, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import foliage as FO  # noqa: E402
import layout_lib as L  # noqa: E402

ROOT = os.path.abspath(os.path.join(BLENDER, "..", ".."))
LAYOUT_PATH = os.path.join(ROOT, "data", "world", "layout.json")
INSTANCES_DIR = os.path.join(ROOT, "data", "world", "instances")

STEP = 0.35  # sim_core::MoveParams::step_height: lower parts are stepped over
HEAD = 1.9  # parts higher than this over their floor hang overhead
GAP = 0.6  # parts of one piece closer than this (a character's width) are one blocker
SINK = 0.25  # a face may start this far below the floor it stands on (pieces sunk into the ground)
INSIDE = 0.1  # a face counts when walkable ground of its floor lies this far all round its centre (walls and their trim do not)
ROUND_TOL = 1.1  # a part is round when no point lies further than this times its larger half from its middle
ROUND_ASPECT = 0.8  # ... and its box is about as wide as deep
QUARTER = 1e-3  # yaw within this (radians) of a quarter turn: an axis-aligned box
MIN_HALF = 0.04  # thinner parts (a pole's shadow on a wall) are dropped
MIN_SOLID_HALF = 0.15  # sim_core::layout::MIN_SOLID_HALF: thinner pieces block as this thick
RADIUS = 0.3  # sim_core::MoveParams::radius
SEAT_REACH = 0.6  # client/src/game/castle/seats.ts SEAT_REACH
Y_BELOW = 0.3  # a blocker's y span starts this far below its floor


@dataclass
class Blocker:
    """`sim_core::layout::Blocker`: a box (or the ellipse in it) centred at
    `at` [x, z] with half extents `half` along its own x and z, turned by
    `yaw` (local +z to (sin yaw, cos yaw), as the pieces), over ground heights
    `y` [bottom, top]."""

    id: str
    at: tuple[float, float]
    half: tuple[float, float]
    y: tuple[float, float]
    yaw: float = 0.0
    round: bool = False
    piece: str = ""  # what it is (reports)
    seat: bool = False  # a bench or chair (kept only while its seats stay usable)

    def to_json(self) -> dict:
        out = {"id": self.id, "at": [_n(self.at[0]), _n(self.at[1])], "half": [_n(self.half[0]), _n(self.half[1])],
               "y": [_n(self.y[0]), _n(self.y[1])]}
        if self.yaw:
            out["yaw"] = round(self.yaw, 4)
        if self.round:
            out["round"] = True
        return out

    def local(self, x: float, z: float) -> tuple[float, float]:
        dx, dz = x - self.at[0], z - self.at[1]
        s, c = math.sin(self.yaw), math.cos(self.yaw)
        return dx * c - dz * s, dx * s + dz * c

    def holds(self, x: float, z: float, grow: float = 0.0) -> bool:
        """(x, z) strictly inside, the shape grown by `grow` (sim-core's rule for grow 0)."""
        lx, lz = self.local(x, z)
        hx, hz = max(self.half[0], MIN_SOLID_HALF) + grow, max(self.half[1], MIN_SOLID_HALF) + grow
        if self.round:
            return (lx / hx) ** 2 + (lz / hz) ** 2 < 1.0
        return abs(lx) < hx and abs(lz) < hz

    def blocks(self, x: float, y: float, z: float) -> bool:
        return self.y[0] <= y <= self.y[1] and self.holds(x, z)

    def aabb(self) -> tuple[float, float, float, float]:
        s, c = abs(math.sin(self.yaw)), abs(math.cos(self.yaw))
        hx, hz = max(self.half[0], MIN_SOLID_HALF), max(self.half[1], MIN_SOLID_HALF)
        ex, ez = hx * c + hz * s, hx * s + hz * c
        return self.at[0] - ex, self.at[1] - ez, self.at[0] + ex, self.at[1] + ez


def _n(v: float) -> float:
    r = round(v, 3) + 0.0
    return int(r) if r == int(r) else r


# -- floors ------------------------------------------------------------------------------------


class Floors:
    """The layout's surfaces in a coarse grid: the floor a face stands on."""

    CELL = 4.0

    def __init__(self, layout: L.Layout):
        self.layout = layout
        self.grid: dict[tuple[int, int], list] = {}
        for s in layout.surfaces:
            for i in range(int(math.floor(s.x0 / self.CELL)), int(math.floor(s.x1 / self.CELL)) + 1):
                for k in range(int(math.floor(s.z0 / self.CELL)), int(math.floor(s.z1 / self.CELL)) + 1):
                    self.grid.setdefault((i, k), []).append(s)

    def at(self, x: float, z: float) -> list:
        return self.grid.get((int(math.floor(x / self.CELL)), int(math.floor(z / self.CELL))), [])

    def floor(self, x: float, z: float, bottom: float, inside: float = INSIDE) -> float | None:
        """The highest floor at (x, z) that a face starting at height `bottom`
        stands on (no more than SINK above it), with ground of that floor
        `inside` metres round (x, z) too: a face on a wall's plane, or in it,
        stands on none."""
        best = None
        for s in self.at(x, z):
            if not s.contains(x, z, 1e-6):
                continue
            h = s.height(x, z)
            if h <= bottom + SINK and (best is None or h > best) and self._round(x, z, h, inside):
                best = h
        return best

    def _round(self, x: float, z: float, h: float, inside: float) -> bool:
        for dx, dz in ((inside, 0.0), (-inside, 0.0), (0.0, inside), (0.0, -inside)):
            if self.ground(x + dx, z + dz, h, 0.1) is None:
                return False
        return True

    def ground(self, x: float, z: float, near: float, tol: float = 0.4) -> float | None:
        """Ground within `tol` of `near` at (x, z) (edges included), the nearest."""
        best = None
        for s in self.at(x, z):
            if s.contains(x, z, 1e-4):
                h = s.height(x, z)
                if abs(h - near) <= tol and (best is None or abs(h - near) < abs(best - near)):
                    best = h
        return best


# -- the cut -----------------------------------------------------------------------------------


def _clip(pts: list, lo: float, hi: float) -> list:
    """The polygon `pts` (x, y, z) cut to lo <= y <= hi."""
    def cut(poly, keep, at):
        out = []
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            ka, kb = keep(a[1]), keep(b[1])
            if ka:
                out.append(a)
            if ka != kb:
                t = (at - a[1]) / (b[1] - a[1])
                out.append((a[0] + (b[0] - a[0]) * t, at, a[2] + (b[2] - a[2]) * t))
        return out

    poly = cut(list(pts), lambda y: y >= lo, lo)
    if len(poly) < 2:
        return []
    return cut(poly, lambda y: y <= hi, hi)


@dataclass
class Part:
    """A clipped face in the piece's own frame: its (x, z) points, the floor under it."""

    pts: list
    floor: float
    top: float
    box: tuple = ()

    def __post_init__(self):
        xs = [p[0] for p in self.pts]
        zs = [p[1] for p in self.pts]
        self.box = (min(xs), min(zs), max(xs), max(zs))


def band_parts(faces, origin, yaw: float, floors: Floors) -> list[Part]:
    """The faces (world points) of a piece at `origin`, `yaw`, cut to the band
    over the floor each stands on, in the piece's frame."""
    s, c = math.sin(yaw), math.cos(yaw)
    out = []
    for pts in faces:
        if len(pts) < 3:
            continue
        ys = [p[1] for p in pts]
        cx = sum(p[0] for p in pts) / len(pts)
        cz = sum(p[2] for p in pts) / len(pts)
        fl = floors.floor(cx, cz, min(ys))
        if fl is None:
            continue
        lo, hi = fl + STEP, fl + HEAD
        if max(ys) < lo or min(ys) > hi:
            continue
        cut = _clip(pts, lo, hi)
        if len(cut) < 2:
            continue
        loc = []
        for x, _, z in cut:
            dx, dz = x - origin[0], z - origin[2]
            loc.append((dx * c - dz * s, dx * s + dz * c))
        out.append(Part(loc, fl, min(max(ys), hi) - fl))
    return out


def clusters(parts: list[Part], gap: float = GAP) -> list[list[Part]]:
    """Parts whose boxes come closer than `gap`, joined (sort and sweep)."""
    n = len(parts)
    up = list(range(n))

    def find(i):
        while up[i] != i:
            up[i] = up[up[i]]
            i = up[i]
        return i

    order = sorted(range(n), key=lambda i: parts[i].box[0])
    active: list[int] = []
    for i in order:
        b = parts[i].box
        active = [j for j in active if parts[j].box[2] + gap > b[0]]
        for j in active:
            o = parts[j].box
            if o[1] - gap < b[3] and b[1] - gap < o[3]:
                ri, rj = find(i), find(j)
                if ri != rj:
                    up[ri] = rj
        active.append(i)
    groups: dict[int, list[Part]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(parts[i])
    return sorted(groups.values(), key=lambda g: (min(p.box[0] for p in g), min(p.box[1] for p in g)))


def blockers_of(pid: str, piece: str, faces, origin, yaw: float, floors: Floors, *, gap: float = GAP,
                seat: bool = False, roundable: bool = True) -> list[Blocker]:
    """The blockers of one placed piece (its faces in world coordinates)."""
    parts = band_parts(faces, origin, yaw, floors)
    groups = clusters(parts, gap) if parts else []
    out = []
    s, c = math.sin(yaw), math.cos(yaw)
    for k, g in enumerate(groups):
        x0 = min(p.box[0] for p in g)
        z0 = min(p.box[1] for p in g)
        x1 = max(p.box[2] for p in g)
        z1 = max(p.box[3] for p in g)
        hx, hz = (x1 - x0) / 2.0, (z1 - z0) / 2.0
        if hx < MIN_HALF or hz < MIN_HALF:
            continue
        lx, lz = (x0 + x1) / 2.0, (z0 + z1) / 2.0
        pts = [q for p in g for q in p.pts]
        # Round: about as wide as deep, no point further from the middle than ROUND_TOL times
        # the larger half (a square's corners are 1.41 times out): the circle through the furthest.
        r = max(math.hypot(q[0] - lx, q[1] - lz) for q in pts)
        rnd = (roundable and len(pts) >= 12 and min(hx, hz) >= ROUND_ASPECT * max(hx, hz)
               and r <= ROUND_TOL * max(hx, hz))
        # Back to the world: the local centre turned by yaw.
        wx = origin[0] + lx * c + lz * s
        wz = origin[2] - lx * s + lz * c
        fl0 = min(p.floor for p in g)
        fl1 = max(p.floor for p in g)
        top = max(p.top for p in g)
        by = _axis_yaw(yaw)
        if rnd:
            by, hx, hz = 0.0, r, r
        elif by is not None:
            if by:  # a quarter turn: swap the halves
                hx, hz = hz, hx
            by = 0.0
        else:
            by = _norm_yaw(yaw)
        bid = pid if len(groups) == 1 else f"{pid}.{k}"
        out.append(Blocker(bid, (wx, wz), (hx, hz), (fl0 - Y_BELOW, fl1 + max(top, STEP)), by, rnd, piece, seat))
    return out


def _axis_yaw(yaw: float):
    """0 for a yaw that is a whole number of half turns, 1 for an odd quarter turn, None otherwise."""
    q = yaw / (math.pi / 2)
    k = round(q)
    if abs(q - k) * (math.pi / 2) > QUARTER:
        return None
    return k % 2


def _norm_yaw(yaw: float) -> float:
    """The yaw folded into (-pi/2, pi/2] (a box looks the same half a turn round)."""
    y = math.fmod(yaw, math.pi)
    if y <= -math.pi / 2:
        y += math.pi
    elif y > math.pi / 2:
        y -= math.pi
    return y


# -- what is solid -----------------------------------------------------------------------------

# Pieces and models that are never solid, by name (a regular expression on the castle-kit
# piece or kit model): they open (doors, gates, secret entrances), are architecture the layout
# already walls (arches, vaults, the Owl Loft and the bridge, the stair cores), hang overhead,
# lie flat (rugs, inlays) or stand on a table (settings), or are buildings the grounds leave
# out of their walkable ground (the hut, the glasshouses, the boathouse), or already solid
# areas of the layout (the gatehouse, the pitch's stands and towers).
SKIP = re.compile(
    r"^(kit_door_.*|kit_entrance_.*|kit_wishing_room_door|kit_stone_arch_.*|kit_window_.*|kit_vault_.*"
    r"|kit_dome_.*|kit_landing_corbel_.*|kit_corbel_table_.*|kit_beam_ridge|kit_truss_.*|kit_owl_loft_.*"
    r"|kit_bridge_covered|kit_spiral_core_.*|kit_passage_ceiling|kit_gatehouse_.*|kit_banner_.*"
    r"|kit_chandelier_.*|kit_lamp_hanging|kit_lantern_.*|kit_candles_.*|kit_pans_hanging|kit_skylight_.*"
    r"|kit_clock_face_.*|kit_portrait_.*|kit_tapestry_.*|kit_drapes_.*|kit_class_plaque|kit_door_knocker_.*"
    r"|kit_torch_wall|kit_rug_.*|kit_floor_.*|kit_table_setting_.*|kit_ravine"
    r"|kit_grounds_hut|kit_glasshouse.*|kit_boathouse|kit_pitch_stand|kit_pitch_tower|kit_pitch_gate"
    r"|kit_garden_gate.*|kit_field_gate.*|kit_greenhouse_gate.*|kit_paddock_gate.*"
    # props.json kit models
    r"|classroom_door|duel_area_inlay|duel_banner|duel_stage|dungeon_floor|dungeon_wall|floating_candles"
    r"|iron_chandelier|wall_torch|tell_.*|field_gate|garden_gate)$")

# Seats: benches and chairs (solid while their seats stay usable).
SEATS = re.compile(r"^(kit_bench_.*|kit_chair_.*|kit_sofa_.*|kit_armchair.*|spectator_bench|kit_pitch_bench.*)$")

# The castle roles placed pieces stand in that may be furniture: the hand placements (`hero`).
ROLES = ("hero",)


def solid_piece(name: str) -> bool:
    return not SKIP.match(name)


# -- the castle and the kitted rooms -------------------------------------------------------------


def _piece_faces(piece, origin, yaw: float, plantings: bool = True):
    """A placed piece's faces in world metres, with (vegetation look A) the solid parts of its
    plantings of the foliage kit: a tree's trunk, a hedge's mass (foliage.solid_faces)."""
    from castle_kit.pieces import geom

    pl = geom.Placement(tuple(origin), yaw)
    faces = [[pl.point(p) for p in f.pts] for f in piece.faces]
    if plantings:
        faces += _planting_faces(FO.place(f, pl.point) for f in piece.meta.get("foliage", []))
    return faces


def _planting_faces(plants) -> list:
    """The solid parts of placed plantings (world metres): never a crown, only what stands in
    the walking band (a trunk, a hedge's mass)."""
    return [face for p in plants for face in FO.solid_faces(p)]


def _kit_faces(model, e):
    import kit

    faces = [[kit.place(model.mesh.verts[i], e) for i in f.idx] for f in model.mesh.faces]
    return faces + _planting_faces(FO.place(f, lambda q: kit.place(q, e)) for f in getattr(model, "foliage", []))


def builder_blockers(b, cell: str, prefix: str, floors: Floors) -> tuple[list[Blocker], list]:
    """The blockers of one kitted cell (or instance room) of castle-kit Builder `b`,
    and its seat rows (`assemble.seats_m3`'s, without building the cell)."""
    from castle_kit import assemble as A
    from castle_kit import pieces as P
    from castle_kit import rules as R
    import kit_models as km

    cp = b.kit_plan.cells[cell]
    ctx = R.context(b.layout, cp, b.kit_entries, b.open_edges)
    uses = R.placements(ctx)
    built = {}
    out = []
    for u in uses:
        # A house's or a passage's entrance (`entrance_*`: the Burrowsby barrels) opens.
        if u.role not in ROLES or not solid_piece(u.piece) or u.id.split(":")[-1].startswith("entrance_"):
            continue
        piece = built[u.id] = P.build(u.piece, **u.kwargs())
        out += blockers_of(f"{prefix}/{u.id.split(':')[-1]}", u.piece, _piece_faces(piece, u.origin, u.yaw), u.origin,
                           u.yaw, floors, seat=bool(SEATS.match(u.piece)))
    kits = [e for e in b.kit_entries if e.cell == cell and e.id not in cp.replaces]
    for e in kits:
        if not solid_piece(e.model):
            continue
        model = km.build(e.model, **dict(e.args))
        out += blockers_of(f"{prefix}/{e.id}", e.model, _kit_faces(model, e), e.pos, e.yaw, floors,
                           seat=bool(SEATS.match(e.model)))
    stub = A.CellBuild.__new__(A.CellBuild)
    stub.uses, stub.pieces = uses, built
    return out, A.seats_m3(stub, kits)


def castle_builder():
    from castle_kit import assemble as A

    return A.Builder.from_files()


def castle(b=None) -> tuple[list[Blocker], list]:
    """Every kitted castle cell's blockers and seat rows."""
    b = b or castle_builder()
    floors = Floors(b.layout)
    out, seats = [], []
    for cell, cp in sorted(b.kit_plan.cells.items()):
        if not cp.build:
            continue
        got, rows = builder_blockers(b, cell, cell, floors)
        out += got
        seats += rows
    return out, seats


# -- the instance rooms ------------------------------------------------------------------------


def kit_room(room) -> tuple[list[Blocker], list]:
    """A castle-kit instance room's blockers and seat rows (instance_kit.KitRoom)."""
    import instance_kit as IK

    b = IK.builder(room)
    return builder_blockers(b, room.cell, room.layout_id, Floors(b.layout))


MESH_GAP = 0.1  # a mesh room's parts are one blocker only when they (nearly) touch


def mesh_room(room) -> tuple[list[Blocker], list]:
    """A meshkit room's (school_rooms.Room: the Charms classroom, the duel arena)
    blockers: its whole mesh, cut and joined like one piece's, and its seats."""
    layout = L.parse_layout(room.layout)
    floors = Floors(layout)
    m = room.mesh
    faces = [[m.verts[i] for i in f.idx] for f in m.faces]
    out = blockers_of(room.layout_id, "room", faces, (0.0, 0.0, 0.0), 0.0, floors, gap=MESH_GAP)
    for k, bl in enumerate(out):
        bl.id = f"{room.layout_id}/part_{k}"
    seats = []
    for s in room.seats:
        x, y, z = s["pos"]
        from instance_kit import ROOM_SEAT_HEIGHT

        seats.append([x - 0.3, z - 0.15, x + 0.3, z + 0.15, y + ROOM_SEAT_HEIGHT, s["yaw"]])
    return out, seats


# -- the grounds and the pitch -----------------------------------------------------------------


def grounds(layout: L.Layout, terrain_dir: str | None = None) -> tuple[list[Blocker], list]:
    """The grounds' dressing and foliage, and the pitch's pieces, with the pitch's seat rows."""
    import grounds_build as GB
    import grounds_dressing as D
    import grounds_terrain as GT
    import pitch_build as PB
    from castle_kit import pieces as P

    terrain_dir = terrain_dir or D.TERRAIN_DIR
    g = GT.Grounds.load(terrain_dir, layout)
    ctx = D.context(g, layout)
    floors = Floors(layout)
    out = []
    for p in D.placements(ctx):
        if not solid_piece(p.piece):
            continue
        raw = P.build(p.piece, **p.args)
        piece = _shear_piece(raw, p.rise)
        # Vegetation look A: a hedge run's plantings, sheared with its rise like its faces.
        faces = _piece_faces(piece, p.pos, p.yaw, plantings=False) + _planting_faces(GB.placed_plantings(raw, p))
        out += blockers_of(f"grounds/{p.id}", p.piece, faces, p.pos, p.yaw, floors)
    shapes = {}
    for i in D.foliage(ctx):
        piece_name, args = D.SHAPES[i.shape]
        if i.shape in GB.TREE_SHAPES:
            # Vegetation look A: the tree's trunk from the planting the grounds export draws.
            faces = _planting_faces([GB.tree_planting(i)])
        else:
            if i.shape not in shapes:
                shapes[i.shape] = P.build(piece_name, **args)
            sc = i.scale
            faces = [[_inst_point(p, sc, i.yaw, i.pos) for p in f.pts] for f in shapes[i.shape].faces]
        out += blockers_of(f"grounds/{i.id}", piece_name, faces, i.pos, i.yaw, floors)
    placed, seats = PB.placements(g.meta["pitch"])
    for p in placed:
        if not solid_piece(p.piece):
            continue
        piece = P.build(p.piece, **p.args)
        out += blockers_of(f"pitch/{p.id}", p.piece, _piece_faces(piece, p.pos, p.yaw), p.pos, p.yaw, floors)
    return out, seats


def _inst_point(p, scale: float, yaw: float, pos) -> tuple:
    s, c = math.sin(yaw), math.cos(yaw)
    x, y, z = p[0] * scale, p[1] * scale, p[2] * scale
    return (x * c + z * s + pos[0], y + pos[1], -x * s + z * c + pos[2])


def _shear_piece(piece, rise: float):
    import grounds_build as GB

    return GB._shear(piece, rise)


# -- seats -------------------------------------------------------------------------------------


def _ring(radius: float):
    return [(0.0, 0.0)] + [(radius * math.cos(k * math.pi / 4), radius * math.sin(k * math.pi / 4)) for k in range(8)]


FOOT = _ring(RADIUS)


class Index:
    """Blockers in a coarse grid, for the standing tests."""

    CELL = 4.0

    def __init__(self, blockers: list[Blocker]):
        self.grid: dict[tuple[int, int], list[Blocker]] = {}
        self.add(blockers)

    def add(self, blockers) -> None:
        for b in blockers:
            x0, z0, x1, z1 = b.aabb()
            for i in range(int(math.floor(x0 / self.CELL)), int(math.floor(x1 / self.CELL)) + 1):
                for k in range(int(math.floor(z0 / self.CELL)), int(math.floor(z1 / self.CELL)) + 1):
                    self.grid.setdefault((i, k), []).append(b)

    def remove(self, b: Blocker) -> None:
        for v in self.grid.values():
            if b in v:
                v.remove(b)

    def blocked(self, x: float, y: float, z: float) -> bool:
        return any(b.blocks(x, y, z) for b in self.grid.get((int(math.floor(x / self.CELL)), int(math.floor(z / self.CELL))), ()))


def can_stand(floors: Floors, index: Index, x: float, z: float, y: float) -> bool:
    """A character stands at (x, z) on ground near `y`: its centre and footprint on
    ground within a step (sim-core's footprint test), none of it in a blocker."""
    g = floors.ground(x, z, y, STEP)
    if g is None or index.blocked(x, g, z):
        return False
    for dx, dz in FOOT[1:]:
        h = floors.ground(x + dx, z + dz, g, STEP)
        if h is None or index.blocked(x + dx, h, z + dz):
            return False
    return True


def seat_spot(floors: Floors, index: Index, row, reach: float = SEAT_REACH - 0.05, step: float = 0.1):
    """A free standing point within `reach` (in plan) of seat row `row`'s rectangle, on the floor its
    sitter's feet rest on (the row's top less its drop), or None."""
    x0, z0, x1, z1, y = row[:5]
    drop = row[8] if len(row) > 8 else 0.46
    fy = y - drop
    best = None
    n = int(math.ceil(reach / step))
    xs = [x0 - reach + k * step for k in range(int((x1 - x0 + 2 * reach) / step) + 1)]
    zs = [z0 - reach + k * step for k in range(int((z1 - z0 + 2 * reach) / step) + 1)]
    del n
    for x in xs:
        for z in zs:
            dx = max(x0 - x, 0.0, x - x1)
            dz = max(z0 - z, 0.0, z - z1)
            d = math.hypot(dx, dz)
            if d > reach or (best is not None and d >= best[0]):
                continue
            if can_stand(floors, index, x, z, fy):
                best = (d, x, z)
    return None if best is None else (best[1], best[2])


# Interactables characters arrive at (leaving a dorm, a class, a room): their point stays free.
ARRIVALS = ("dorm_door", "dorm_exit", "class_door", "instance_exit", "wishing_room_door")


def keep_clear(blockers: list[Blocker], layout: L.Layout, report: list) -> list[Blocker]:
    """`blockers` less those standing in a doorway (on a `door` surface: an
    entrance's barrels, an open gate) or on a point characters arrive at (a
    spawn, a door they come out of: a character's width round it)."""
    points = [(sp["id"], tuple(sp["pos"])) for sp in layout.spawns]
    points += [(it.id, it.pos) for it in layout.interactables if it.kind in ARRIVALS]
    doors = [s for s in layout.surfaces if s.kind == "door"]
    out = []
    for b in blockers:
        x0, z0, x1, z1 = b.aabb()
        door = next((s for s in doors if s.x0 + 0.05 < x1 and x0 < s.x1 - 0.05 and s.z0 + 0.05 < z1 and z0 < s.z1 - 0.05
                     and b.y[0] - 0.5 <= s.floor_max and s.floor_min <= b.y[1] and _overlaps(b, s)), None)
        if door is not None:
            report.append(f"{b.id} ({b.piece}): walkable, it stands in the doorway {door.id}")
            continue
        hit = next((pid for pid, (x, y, z) in points if b.y[0] - 0.5 <= y <= b.y[1] and b.holds(x, z, RADIUS)), None)
        if hit is not None:
            report.append(f"{b.id} ({b.piece}): walkable, it stands where characters arrive ({hit})")
            continue
        out.append(b)
    return out


def _overlaps(b: Blocker, s) -> bool:
    """Whether the blocker's outline reaches into surface `s` (more than 5 cm)."""
    step = 0.05
    x0, z0, x1, z1 = max(s.x0 + step, b.aabb()[0]), max(s.z0 + step, b.aabb()[1]), min(s.x1 - step, b.aabb()[2]), \
        min(s.z1 - step, b.aabb()[3])
    nx, nz = int((x1 - x0) / step) + 1, int((z1 - z0) / step) + 1
    return any(b.holds(x0 + i * step, z0 + k * step) for i in range(nx) for k in range(nz))


def free_seats(blockers: list[Blocker], seats: list, floors: Floors, report: list) -> list[Blocker]:
    """`blockers` less those that leave a seat with nowhere to stand within reach: the
    blockers holding the seat's middle (its bench or chair) go, when that frees it."""
    index = Index(blockers)
    dropped: set[int] = set()
    for row in seats:
        if seat_spot(floors, index, row) is not None:
            continue
        if seat_spot(floors, Index([]), row) is None:
            continue  # out of reach anyway (a stand's benches behind a wall, for the look)
        x0, z0, x1, z1, y = row[:5]
        cx, cz = (x0 + x1) / 2.0, (z0 + z1) / 2.0
        mine = [b for b in blockers if id(b) not in dropped and b.y[0] - 0.5 <= y <= b.y[1] + 1.0
                and b.holds(cx, cz, 0.05)]
        for b in mine:
            index.remove(b)
        if mine and seat_spot(floors, index, row) is not None:
            for b in mine:
                dropped.add(id(b))
                report.append(f"{b.id} ({b.piece}): walkable, so its seat at ({cx:.2f}, {cz:.2f}) can be reached")
        else:
            index.add(mine)
            report.append(f"seat at ({cx:.2f}, {y:.2f}, {cz:.2f}): no standing spot within reach")
    return [b for b in blockers if id(b) not in dropped]


# -- every layout ------------------------------------------------------------------------------

CASTLE = "castle"  # data/world/layout.json


def castle_layout() -> tuple[list[Blocker], list[str]]:
    """The castle's and the grounds' blockers (data/world/layout.json), with the report."""
    b = castle_builder()
    got, seats = castle(b)
    more, pitch_seats = grounds(b.layout)
    report: list[str] = []
    out = free_seats(keep_clear(got + more, b.layout, report), seats + pitch_seats, Floors(b.layout), report)
    return out, report


def kit_rooms() -> dict:
    """The castle-kit instance rooms by layout id (class rooms, the Wishing Rooms)."""
    import class_rooms
    import rooms_wishing_room

    rooms = dict(class_rooms.KIT_ROOMS)
    rooms.update(rooms_wishing_room.KIT_ROOMS)
    return {lid: fn for lid, fn in sorted(rooms.items())}


def mesh_rooms() -> dict:
    import school_rooms

    return dict(sorted(school_rooms.ROOMS.items()))


def room_layout(lid: str, room=None) -> tuple[list[Blocker], list[str]]:
    """An instance room's blockers (`room`: its built KitRoom or Room, else built here)."""
    if lid in kit_rooms():
        room = room or kit_rooms()[lid]()
        got, seats = kit_room(room)
    elif lid in mesh_rooms():
        room = room or mesh_rooms()[lid]()
        got, seats = mesh_room(room)
    else:
        return [], []
    report: list[str] = []
    layout = L.parse_layout(room.layout)
    floors = Floors(layout)
    out = free_seats(keep_clear(got, layout, report), seats, floors, report)
    return open_pockets(out, layout, floors, report, ENCLOSED_ON_PURPOSE.get(lid, ())), report


# -- pockets -----------------------------------------------------------------------------------

POCKET_STEP = 0.2  # the rooms' flood grid (sim-core's solid furniture test floods the same way)
POCKET_M2 = 0.5  # walled-in ground beyond this is opened up
# Ground a room walls in on purpose, as (x0, z0, x1, z1): the paddock's creature pen, a closed
# fence the class watches over (school_rooms_paddock.PEN; the Rust test allows the same).
ENCLOSED_ON_PURPOSE = {"class_paddock": ((-5.4, -5.4, 5.4, -0.8),)}


def flood(floors: Floors, index: Index, starts, step: float = POCKET_STEP) -> set:
    """The grid points ((i, k): x = i * step, z = k * step) a character reaches from `starts`
    (single-floor rooms: the floor the start stands on, a step up or down per move)."""
    seen: dict = {}
    todo = []
    for x, y, z in starts:
        key = (round(x / step), round(z / step))
        g = floors.ground(key[0] * step, key[1] * step, y, STEP)
        if g is not None and key not in seen and can_stand(floors, index, key[0] * step, key[1] * step, g):
            seen[key] = g
            todo.append(key)
    while todo:
        i, k = todo.pop()
        y = seen[(i, k)]
        for di, dk in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (i + di, k + dk)
            if n in seen:
                continue
            x, z = n[0] * step, n[1] * step
            g = floors.ground(x, z, y, STEP)
            mid = floors.ground((i + n[0]) * step / 2, (k + n[1]) * step / 2, y, STEP)
            if g is not None and mid is not None and can_stand(floors, index, x, z, g):
                seen[n] = g
                todo.append(n)
    return set(seen)


def open_pockets(blockers: list[Blocker], layout: L.Layout, floors: Floors, report: list, allowed=()) -> list[Blocker]:
    """`blockers` less those walling in ground a character reached without them (a corner
    behind two heaps): while a pocket bigger than POCKET_M2 is left, the smallest blocker
    beside it goes."""
    starts = [tuple(sp["pos"]) for sp in layout.spawns]
    before = flood(floors, Index([]), starts)
    out = list(blockers)
    while True:
        index = Index(out)
        now = flood(floors, index, starts)
        lost = {c for c in before - now if can_stand(floors, index, c[0] * POCKET_STEP, c[1] * POCKET_STEP,
                                                     floors.ground(c[0] * POCKET_STEP, c[1] * POCKET_STEP, 0.0, 50.0) or 0.0)
                and not any(x0 <= c[0] * POCKET_STEP <= x1 and z0 <= c[1] * POCKET_STEP <= z1 for x0, z0, x1, z1 in allowed)}
        pocket = _largest_pocket(lost)
        if len(pocket) * POCKET_STEP ** 2 <= POCKET_M2:
            return out
        near = [b for b in out if any(b.holds(i * POCKET_STEP, k * POCKET_STEP, RADIUS + 2 * POCKET_STEP)
                                      for i, k in pocket)]
        if not near:
            return out
        near.sort(key=lambda b: (b.half[0] * b.half[1], b.id))
        # The smallest whose going opens the pocket, else the smallest.
        drop = next((b for b in near if pocket & flood(floors, Index([o for o in out if o is not b]), starts)), near[0])
        out.remove(drop)
        i, k = min(pocket)
        report.append(f"{drop.id} ({drop.piece}): walkable, it walled in {len(pocket) * POCKET_STEP ** 2:.1f} m² "
                      f"at ({i * POCKET_STEP:.1f}, {k * POCKET_STEP:.1f})")


def _largest_pocket(cells: set) -> set:
    best: set = set()
    left = set(cells)
    while left:
        start = left.pop()
        group, todo = {start}, [start]
        while todo:
            i, k = todo.pop()
            for n in ((i + 1, k), (i - 1, k), (i, k + 1), (i, k - 1)):
                if n in left:
                    left.remove(n)
                    group.add(n)
                    todo.append(n)
        if len(group) > len(best):
            best = group
    return best


def generate(only=None) -> dict[str, tuple[list[Blocker], list[str]]]:
    """Layout id ("castle" or an instance layout id) -> (blockers, report)."""
    out = {}
    if only is None or CASTLE in only:
        out[CASTLE] = castle_layout()
    for lid in list(kit_rooms()) + list(mesh_rooms()):
        if only is None or lid in only:
            out[lid] = room_layout(lid)
    return out


def layout_path(lid: str) -> str:
    return LAYOUT_PATH if lid == CASTLE else os.path.join(INSTANCES_DIR, f"{lid}.json")


def solid_line(b: Blocker, house_style: bool) -> str:
    """One blocker as a line of the layout file (layout.json's spaced style or the instances')."""
    body = json.dumps(b.to_json(), separators=(", ", ": "))
    return "    " + ("{ " + body[1:-1] + " }" if house_style else body)


def patch(text: str, blockers: list[Blocker]) -> str:
    """The layout file `text` with its `solids` list replaced by `blockers` (inserted
    before `areas`, or as the last list, when it has none; dropped when empty)."""
    house_style = text.startswith("{\n  \"version\": 1,\n  \"surfaces\": [\n    { ")
    lines = text.rstrip("\n").split("\n")
    if '  "solids": [' in lines:
        a = lines.index('  "solids": [')
        b = next(i for i in range(a, len(lines)) if lines[i] in ("  ]", "  ],"))
        last = lines[b] == "  ]"
        del lines[a:b + 1]
        if last:  # the list before it ends the file now
            lines[a - 1] = lines[a - 1].rstrip(",")
    if blockers:
        rows = [solid_line(b, house_style) for b in sorted(blockers, key=lambda b: b.id)]
        block = ['  "solids": ['] + [r + "," for r in rows[:-1]] + rows[-1:]
        if '  "areas": [' in lines:
            at = lines.index('  "areas": [')
            lines[at:at] = block + ["  ],"]
        else:
            end = len(lines) - 1  # the closing brace
            lines[end - 1] = lines[end - 1] + ","
            lines[end:end] = block + ["  ]"]
    return "\n".join(lines) + "\n"


def check_ids(blockers: list[Blocker]) -> list[str]:
    ids = [b.id for b in blockers]
    return sorted({i for i in ids if ids.count(i) > 1})


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Generate or check the layouts' solid furniture and props")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated layout ids (castle, class_potions, ...)")
    ap.add_argument("--json", default="", help="also dump every blocker with its piece to this file (review)")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None
    bad = 0
    dump = []
    for lid, (blockers, report) in generate(only).items():
        dupes = check_ids(blockers)
        if dupes:
            print(f"{lid}: duplicate blocker ids {dupes[:5]}")
            bad += 1
        path = layout_path(lid)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new = patch(text, blockers)
        kinds = {}
        for b in blockers:
            kinds[b.piece] = kinds.get(b.piece, 0) + 1
        print(f"{lid}: {len(blockers)} blockers ({sum(1 for b in blockers if b.round)} round, "
              f"{sum(1 for b in blockers if b.yaw)} turned)")
        for line in report:
            print(f"  {line}")
        dump += [dict(b.to_json(), piece=b.piece, seat=b.seat, layout=lid) for b in blockers]
        if args.check and new != text:
            print(f"{path} is out of date: run solids.py --write")
            bad += 1
        if args.write and new != text:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
            print(f"  wrote {path}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(dump, f)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
