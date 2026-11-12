"""Two-sided seats (docs/architecture.md): which
seat rows a sitter may also sit on facing the other way. Pure Python, no
`bpy`, deterministic.

    python3 -I tools/blender/world/seat_sides.py     # report: two-sided and one-way seats per area

A seat row ([x0, z0, x1, z1, y, yaw, front, back, drop], castle/seats.ts)
faces `yaw`; that facing is good by construction (it is how the seat was
placed: at a table, the legs under it). The seat is two-sided when it has no
back or arms (`backed`: decided by whoever has the seat's own geometry,
castle_kit/assemble.py `seat_rows`) and the space the legs take facing the
other way is unobstructed: past the back edge (`back` behind the centre
line), SIDE_CLEAR out along the whole row, from the seat's top down
LEG_DEPTH, there is no wall (no floor at all under a point: the layouts
leave walls as gaps between their surfaces), no floor rising more than
RISE_OK over the feet's floor (the next tier up, a step up), and nothing of
the layout's committed `solids` (other than the seat's own bench) or its
`solid` areas. A drop is fine: the legs hang over a ledge, a stand's front,
a lower tier. So a bench against a wall, a chair, a sofa or a tiered seat
with the next tier behind is one-way; a table bench (the aisle behind it)
and a bench standing free are two-sided.

The legs facing the other way rest on (or hang over) the highest floor under
that band: `flip_drop`, the seat's top over it (FLIP_DROP_MAX when there is
none within it), so the client fits the legs to the floor that is there
(characters/seated.ts lifts the knees only where the feet would go through
it). The exporters write it as the rows' tenth number (0: one-way; the
castle cells' and the pitch's `seats`) or a room seat's `two_sided` and
`flip_drop` (instances.json `seats`). It comes from the layout files'
committed `solids` (solids.py --write), so a changed blocker changes it at
the next export.
"""

from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
for _p in (BLENDER, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import layout_lib as L  # noqa: E402
import solids as S  # noqa: E402

SIDE_CLEAR = 0.6  # the legs' space reaches this far past the back edge (facing the other way)
LEG_DEPTH = 0.5  # ... from the seat's top down this far
RISE_OK = 0.15  # a floor up to this over the feet's floor there is the feet's floor (more: a riser, the next tier)
ABOVE = 0.5  # floors higher than this over the seat's top are overhead (a gallery), not in the way
FLIP_DROP_MAX = 2.0  # the legs' drop facing the other way when no floor is within it (they hang)
EDGE_GAP = 0.05  # the band starts this far past the edge (the bench's own overhang)
GRID = 0.1  # sample spacing (m) over the band
END_INSET = 0.1  # the band runs the row's length less this at each end
OWN_GROW = 0.05  # blockers holding the row's centre line (grown by this) are the seat's own bench
BACK_RISE = 0.12  # the seat's own piece reaching this far over its top, over the seat: a back or arms
BACK_GROW = 0.1  # ... within the row's rectangle grown by this (and to its backrest's reach)
DEFAULT_DROP = 0.46  # a row without a drop (instance_kit.ROOM_SEAT_HEIGHT)


def blocker_of(d: dict) -> S.Blocker:
    """A layout `solids` entry as a solids.Blocker."""
    return S.Blocker(str(d["id"]), (float(d["at"][0]), float(d["at"][1])), (float(d["half"][0]), float(d["half"][1])),
                     (float(d["y"][0]), float(d["y"][1])), float(d.get("yaw", 0.0)), bool(d.get("round", False)))


def frame(row) -> tuple:
    """(centre x, centre z, facing (fx, fz), along (tx, tz), half length, half depth) of a row."""
    x0, z0, x1, z1, _y, yaw = row[:6]
    fx, fz = math.sin(yaw), math.cos(yaw)
    tx, tz = fz, -fx
    hx, hz = (x1 - x0) / 2.0, (z1 - z0) / 2.0
    return (x0 + x1) / 2.0, (z0 + z1) / 2.0, (fx, fz), (tx, tz), abs(hx * tx) + abs(hz * tz), abs(hx * fx) + abs(hz * fz)


def _fit(row, k: int, default: float) -> float:
    v = row[k] if len(row) > k else None
    return default if v is None or not isinstance(v, (int, float)) or math.isnan(v) else float(v)


class Sides:
    """The two-sided test against one layout (its surfaces, solid areas and committed `solids`)."""

    def __init__(self, layout: L.Layout, solids: list):
        self.layout = layout
        self.floors = S.Floors(layout)
        self.blockers = [blocker_of(d) for d in solids]

    @classmethod
    def from_data(cls, data: dict) -> "Sides":
        return cls(L.parse_layout(data), data.get("solids", []))

    @classmethod
    def from_file(cls, path: str) -> "Sides":
        with open(path, encoding="utf-8") as f:
            return cls.from_data(json.load(f))

    def _own(self, row) -> set:
        """ids of the blockers holding the row's centre line: its own bench (or chair)."""
        cx, cz, _f, (tx, tz), hl, _hd = frame(row)
        y = row[4]
        n = max(1, int(math.ceil(2 * hl / GRID)))
        pts = [(cx + tx * (-hl + 2 * hl * k / n), cz + tz * (-hl + 2 * hl * k / n)) for k in range(n + 1)]
        return {id(b) for b in self.blockers if b.y[0] - 0.5 <= y <= b.y[1] + 1.0
                and any(b.holds(px, pz, OWN_GROW) for px, pz in pts)}

    def flip_drop(self, row, index: S.Index | None = None) -> float | None:
        """The legs' drop facing the row's other way (its top over the highest floor under them,
        FLIP_DROP_MAX when there is none), or None when something is in the way: a wall (no floor at
        all), a floor rising into the leg space, a solid or a solid area."""
        cx, cz, (fx, fz), (tx, tz), hl, hd = frame(row)
        edge = _fit(row, 7, hd)
        top = row[4]
        fy = top - _fit(row, 8, DEFAULT_DROP)
        if index is None:
            own = self._own(row)
            index = S.Index([b for b in self.blockers if id(b) not in own])
        lo = top - LEG_DEPTH
        heights = (lo, (lo + top) / 2.0, top - 0.02)
        half = max(0.0, hl - END_INSET)
        na = max(1, int(math.ceil(2 * half / GRID)))
        nd = max(1, int(math.ceil((SIDE_CLEAR - EDGE_GAP) / GRID)))
        floor = None
        for i in range(na + 1):
            a = -half + 2 * half * i / na
            for k in range(nd + 1):
                d = -(edge + EDGE_GAP + (SIDE_CLEAR - EDGE_GAP) * k / nd)
                x, z = cx + tx * a + fx * d, cz + tz * a + fz * d
                hs = [sf.height(x, z) for sf in self.floors.at(x, z) if sf.contains(x, z, 1e-4)]
                hs = [h for h in hs if h <= top + ABOVE]
                if not hs or any(h > fy + RISE_OK for h in hs):
                    return None
                if any(index.blocked(x, y, z) or self.layout.solid_blocks(x, y, z) for y in heights):
                    return None
                h = max(hs)
                floor = h if floor is None else max(floor, h)
        return round(min(FLIP_DROP_MAX, top - floor), 3) if floor is not None else FLIP_DROP_MAX

    def two_sided(self, row) -> bool:
        return self.flip_drop(row) is not None

    def mark(self, rows: list, backed=()) -> list:
        """`rows` with a tenth number: the legs' drop facing the other way on a two-sided seat
        (`flip_drop`), 0 on a one-way one; a row whose tuple is in `backed` (a back or arms over
        it) is one-way."""
        backs = {tuple(r) for r in backed}
        out = []
        for r in rows:
            got = None if tuple(r[:9]) in backs else self.flip_drop(r)
            out.append(list(r[:9]) + [got or 0])
        return out

    def mark_room(self, seats: list, height: float = DEFAULT_DROP) -> list:
        """Room seats ({pos, yaw, front?, back?, drop?}: the hips' floor point, instances.json) with
        `two_sided` (and on those, `flip_drop`): the row each stands for is the client's rectangle round the hips
        (castle/seats.ts ROOM_SEAT_HALF), its top `height` over `pos`."""
        out = []
        for s in seats:
            x, y, z = s["pos"]
            drop = s.get("drop", height)
            row = [x - 0.3, z - 0.15, x + 0.3, z + 0.15, y + height, s["yaw"], s.get("front", float("nan")),
                   s.get("back", float("nan")), drop]
            if abs(math.sin(s["yaw"])) > 0.7:  # facing along x: the hips' band runs along z
                row[:4] = [x - 0.15, z - 0.3, x + 0.15, z + 0.3]
            got = self.flip_drop(row)
            out.append(dict(s, two_sided=True, flip_drop=got) if got is not None else dict(s, two_sided=False))
        return out


def backed(faces_pts, row) -> bool:
    """Whether a seat has a back or arms: any of its piece's points (`faces_pts`, in the piece's
    frame, as its meta row `row`) over the row's rectangle, grown by BACK_GROW and as far again
    as its `back` reaches past it, higher than BACK_RISE over its top."""
    x0, z0, x1, z1, y = row[:5]
    grow = BACK_GROW + max(0.0, _fit(row, 7, 0.0) - min(x1 - x0, z1 - z0) / 2.0)
    for p in faces_pts:
        if p[1] > y + BACK_RISE and x0 - grow <= p[0] <= x1 + grow and z0 - grow <= p[2] <= z1 + grow:
            return True
    return False


# -- report ------------------------------------------------------------------------------------


def report() -> dict:
    """{area: (two-sided, one-way)} over the castle cells, the pitch and the instance rooms."""
    out = {}
    b = S.castle_builder()
    sides = Sides.from_file(S.LAYOUT_PATH)
    floors = S.Floors(b.layout)
    for cell, cp in sorted(b.kit_plan.cells.items()):
        if not cp.build:
            continue
        rows, backs = cell_rows(b, cell, floors)
        marked = sides.mark(rows, backs)
        if marked:
            out[cell] = _count(marked)
    import grounds_terrain as GT
    import grounds_dressing as D
    import pitch_build as PB

    g = GT.Grounds.load(D.TERRAIN_DIR, b.layout)
    _placed, pitch = PB.placements(g.meta["pitch"])
    out["pitch"] = _count(sides.mark(pitch))
    for lid, fn in S.kit_rooms().items():
        import instance_kit as IK

        room = fn()
        rb = IK.builder(room)
        rows, backs = cell_rows(rb, room.cell, S.Floors(rb.layout))
        rs = Sides.from_file(S.layout_path(lid))
        marked = rs.mark(rows, backs)
        if marked:
            out[lid] = _count(marked)
    for lid, fn in S.mesh_rooms().items():
        room = fn()
        if room.seats:
            got = Sides.from_file(S.layout_path(lid)).mark_room(room.seats)
            out[lid] = (sum(1 for s in got if s["two_sided"]), sum(1 for s in got if not s["two_sided"]))
    return out


def _count(marked: list) -> tuple[int, int]:
    two = sum(1 for r in marked if r[9] > 0)
    return two, len(marked) - two


def cell_rows(b, cell: str, floors) -> tuple[list, set]:
    """A kitted cell's seat rows and the backed ones, without building the cell (solids.py's way)."""
    from castle_kit import assemble as A
    from castle_kit import pieces as P
    from castle_kit import rules as R

    cp = b.kit_plan.cells[cell]
    ctx = R.context(b.layout, cp, b.kit_entries, b.open_edges)
    uses = R.placements(ctx)
    built = {u.id: P.build(u.piece, **u.kwargs()) for u in uses if u.role in S.ROLES}
    kits = [e for e in b.kit_entries if e.cell == cell and e.id not in cp.replaces]
    stub = A.CellBuild.__new__(A.CellBuild)
    stub.uses, stub.pieces = uses, built
    pairs = A.seat_rows(stub, kits)
    return sorted(r for r, _ in pairs), {tuple(r) for r, bk in pairs if bk}


def main() -> None:
    total = [0, 0]
    for area, (two, one) in report().items():
        print(f"{area}: {two} two-sided, {one} one-way")
        total[0] += two
        total[1] += one
    print(f"all: {total[0]} two-sided, {total[1]} one-way")


if __name__ == "__main__":
    main()
