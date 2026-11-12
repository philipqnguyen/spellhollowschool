"""Where the talking portraits hang (NPCs, docs/art/castle-kit.md "Portraits and tapestries";
docs/architecture.md "NPCs"), in pure Python (no `bpy`).

`resolve()` is sim_core::npcs::PortraitsFile::resolve's twin: each entry of
data/npcs/portraits.json on its surface's wall edge, the canvas centre on
the wall face. `keep_outs()` gives the rectangles on a wall face that the
decorative portrait clusters keep clear of (castle_kit/rules.py
portrait_keep_out): each talking frame's outer extent (frame, crest and
plaque, `frames.outer_extent`) grown by grid.PORTRAIT_KEEP_OUT.

A wall face is the castle kit's (axis, c, sign): the line axis = c the face
lies on and the direction it faces (castle_kit.grid.side_line). A
surface's z_min edge is its "n" side (axis z, c = z0, facing +z), z_max
"s", x_min "w", x_max "e". `t` runs along the face in world metres (x on z
faces, z on x faces); the portrait's own `t` counts from the edge's
lower-coordinate end."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
PORTRAITS_PATH = os.path.join(REPO, "data", "npcs", "portraits.json")
ROSTER_PATH = os.path.join(REPO, "data", "npcs", "roster.json")

EDGES = ("x_min", "x_max", "z_min", "z_max")
# sim_core::npcs limits (PortraitsFile::resolve)
SIZE_W = (0.6, 1.6)
SIZE_H = (0.8, 2.0)
Y_RANGE = (1.2, 4.0)
INSET_RANGE = (-0.5, 1.0)
BACKDROPS = 4
APART_M = 3.0  # no two portraits closer on one floor
END_CLEAR = 0.2  # the canvas's half width plus this stays on the wall


@dataclass(frozen=True)
class Placed:
    """One placement resolved (sim_core::npcs::PlacedPortrait plus the face)."""

    npc: str
    surface: str
    edge: str
    pos: tuple[float, float, float]  # the canvas centre on the wall face (world)
    yaw: float  # facing into the room (yaw 0 faces +z)
    size: tuple[float, float]
    backdrop: int
    level: int
    cell: str
    axis: str  # the face: castle_kit (axis, c, sign)
    c: float
    sign: float
    t: float  # the canvas centre along the face, world metres
    floor: float  # the floor height at the wall foot

    @property
    def size_key(self) -> str:
        return size_key(self.size)


def size_key(size) -> str:
    """'<w>x<h>' in centimetres (the frame files' names)."""
    return f"{round(size[0] * 100)}x{round(size[1] * 100)}"


def edge_face(edge: str, s) -> tuple[str, float, float]:
    """(axis, c, sign) of a surface's edge (castle_kit.grid.side_line)."""
    if edge == "z_min":
        return "z", s.z0, 1.0
    if edge == "z_max":
        return "z", s.z1, -1.0
    if edge == "x_min":
        return "x", s.x0, 1.0
    if edge == "x_max":
        return "x", s.x1, -1.0
    raise ValueError(f"unknown edge {edge!r}")


EDGE_YAW = {"x_min": 1.5707963267948966, "x_max": -1.5707963267948966, "z_min": 0.0, "z_max": 3.141592653589793}


def load(path: str = PORTRAITS_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != 1:
        raise ValueError("portraits.json: version 1")
    return data["portraits"]


def roster_portraits(path: str = ROSTER_PATH) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [n["id"] for n in json.load(f)["npcs"] if n["kind"] == "portrait"]


def resolve(layout, entries: list[dict] | None = None, roster: list[str] | None = None) -> list[Placed]:
    """Every placement on its wall, in file order, with sim-core's checks
    (raises ValueError)."""
    entries = load() if entries is None else entries
    roster = roster_portraits() if roster is None else roster
    by_id = {s.id: s for s in layout.surfaces}
    out: list[Placed] = []
    for p in entries:
        npc = p["npc"]
        if npc not in roster:
            raise ValueError(f"portraits.json: {npc} is not a roster portrait")
        if any(o.npc == npc for o in out):
            raise ValueError(f"portraits.json: {npc} twice")
        s = by_id.get(p["surface"])
        if s is None:
            raise ValueError(f"portraits.json {npc}: no surface {p['surface']}")
        if s.secret or s.moving is not None:
            raise ValueError(f"portraits.json {npc}: a plain surface")
        w, h = (float(v) for v in p.get("size", (1.0, 1.3)))
        y = float(p.get("y", 2.0))
        inset = float(p.get("inset", 0.0))
        backdrop = int(p.get("backdrop", 0))
        if not (SIZE_W[0] <= w <= SIZE_W[1] and SIZE_H[0] <= h <= SIZE_H[1]):
            raise ValueError(f"portraits.json {npc}: size 0.6-1.6 x 0.8-2.0")
        if not (Y_RANGE[0] <= y <= Y_RANGE[1] and INSET_RANGE[0] <= inset <= INSET_RANGE[1]):
            raise ValueError(f"portraits.json {npc}: y 1.2-4, inset -0.5-1")
        if not 0 <= backdrop < BACKDROPS:
            raise ValueError(f"portraits.json {npc}: backdrop 0-3")
        edge = p["edge"]
        along_x = edge in ("z_min", "z_max")
        lo, hi = (s.x0, s.x1) if along_x else (s.z0, s.z1)
        t = float(p["t"])
        half = w / 2 + END_CLEAR
        if not (half <= t <= hi - lo - half):
            raise ValueError(f"portraits.json {npc}: t {t} is off its {hi - lo:.1f} m wall")
        a = lo + t
        if edge == "x_min":
            x, z, fx, fz = s.x0 - inset, a, s.x0 + 0.05, a
        elif edge == "x_max":
            x, z, fx, fz = s.x1 + inset, a, s.x1 - 0.05, a
        elif edge == "z_min":
            x, z, fx, fz = a, s.z0 - inset, a, s.z0 + 0.05
        else:
            x, z, fx, fz = a, s.z1 + inset, a, s.z1 - 0.05
        floor = s.height(fx, fz)
        pos = (x, floor + y, z)
        for o in out:
            if o.level == s.level and ((o.pos[0] - x) ** 2 + (o.pos[2] - z) ** 2) ** 0.5 < APART_M:
                raise ValueError(f"portraits.json: {npc} and {o.npc} are closer than 3 m")
        axis, c, sign = edge_face(edge, s)
        c = c - sign * inset
        out.append(Placed(npc, s.id, edge, pos, EDGE_YAW[edge], (w, h), backdrop, s.level, s.cell, axis, c, sign, a,
                          floor))
    missing = [n for n in roster if not any(o.npc == n for o in out)]
    if missing:
        raise ValueError(f"portraits.json: {missing[0]} hangs nowhere")
    return out


_CACHE: dict = {}


def placed_cached(layout) -> list[Placed]:
    """resolve() once per layout object (the castle kit asks per face)."""
    key = id(layout)
    if key not in _CACHE:
        _CACHE.clear()
        try:
            _CACHE[key] = resolve(layout)
        except (ValueError, KeyError, OSError):
            # A layout that is not the shipped castle (unit tests' small
            # layouts): no talking portraits to keep clear of.
            _CACHE[key] = []
    return _CACHE[key]


def keep_outs(layout, axis: str, c: float, sign: float, grow: float) -> list[tuple[float, float, float, float]]:
    """(t0, t1, y0, y1) world rectangles on the face (axis, c, sign) that
    a talking frame covers, each grown by `grow` (grid.PORTRAIT_KEEP_OUT)."""
    from npcs import frames as FR

    out = []
    for p in placed_cached(layout):
        if p.axis != axis or p.sign != sign or abs(p.c - c) > 0.05:
            continue
        x0, x1, y0, y1 = FR.outer_extent(p.size)
        out.append((p.t - (x1 if _flip(axis, sign) else -x0) - grow, p.t + (-x0 if _flip(axis, sign) else x1) + grow,
                    p.pos[1] + y0 - grow, p.pos[1] + y1 + grow))
    return out


def _flip(axis: str, sign: float) -> bool:
    """True when the frame's local +x (to the right seen from the room) runs
    against world t on this face (castle_kit.rules.tdir is -1)."""
    if axis == "z":
        return sign < 0
    return sign > 0
