"""The instance-room kit adapter (docs/architecture.md).
Pure Python, no `bpy`.

An instance room (a class room, `data/world/instances/<layout id>.json`)
is built by the castle kit exactly as a kitted castle cell is: its layout's
surfaces stand in for `data/world/layout.json`, a `castle_kit.json`-style
cell entry (`KitRoom.spec`: budget, materials, bays, springing, openings,
lanterns, heroes, shade) for the cell's entry in `castle_kit.json`, and
`props.json`-style kit entries (`KitRoom.kit`: the models of
`kit_models.py`, e.g. the Potions classroom's cauldrons and shelves) for the
cell's props kit. `castle_kit.assemble.Builder` places and merges the
pieces (one mesh per castle_* material), the D1 edge loops are cut and,
inside Blender (`build_school.py`), the vertex-AO bake writes the greyscale
`COLOR_0`; `castle_kit.lint` checks the room as it checks a cell.

The props atlas is the castle's (built from `props.json`, so its slots and
the exported `props_atlas.png` agree; `atlas_key` is checked at export).
What differs from a castle cell is only the export (`build_school.py`): an
instance room is loaded whole, without the castle's shared material
library, so its glTF embeds the castle textures it uses
(`client/public/assets/world/textures/`), and it carries no camera collider
and no LOD1 nodes.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
for _p in (BLENDER, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import kit  # noqa: E402
import kit_models as km  # noqa: E402
import layout_lib as L  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import lint as LI  # noqa: E402
from castle_kit import schema as S  # noqa: E402

# The client adds this to a room seat's floor (client/src/game/castle/seats.ts ROOM_SEAT_HEIGHT).
ROOM_SEAT_HEIGHT = 0.46


@dataclass
class KitRoom:
    """An instance room built from castle-kit pieces."""

    layout_id: str
    cell: str
    name: str
    layout: dict  # the instance layout (data/world/instances/<layout_id>.json)
    spec: dict  # a castle_kit.json cell entry (docs/art/castle-kit.md "`castle_kit.json`")
    kit: list = field(default_factory=list)  # props.json-style kit entries: {id, model, pos, yaw, args?}
    lights: list = field(default_factory=list)  # the manifest's point lights, most important first
    ambient: dict = field(default_factory=dict)  # {sky, ground, intensity}
    views: dict = field(default_factory=dict)  # review renders: name -> (eye, target, lens)
    # The class door of a room open to the sky (`head_door`, below).
    head_door: dict | None = None
    # The review renders' world (colour, strength) when not the ambient's
    # (a room open to the sky is reviewed under the night sky's zenith, data/art/lighting.json).
    review_world: tuple | None = None


class KitRoomError(Exception):
    pass


def kit_props(room: KitRoom) -> list[kit.KitProp]:
    """The room's kit entries as castle kit props of its cell."""
    out = []
    for e in room.kit:
        if e.get("model") not in km.MODELS:
            raise KitRoomError(f"{room.layout_id}: kit {e.get('id')!r}: unknown model {e.get('model')!r}")
        args = tuple(sorted((k, float(v)) for k, v in e.get("args", {}).items()))
        out.append(kit.KitProp(e["id"], room.cell, e["model"], tuple(float(v) for v in e["pos"]), float(e.get("yaw", 0.0)),
                               False, args))
    ids = [p.id for p in out]
    if len(set(ids)) != len(ids):
        raise KitRoomError(f"{room.layout_id}: duplicate kit ids")
    return out


def resolve(room: KitRoom):
    """(layout, kit plan, schema report) of the room."""
    layout = L.parse_layout(room.layout)
    bad = sorted({s.cell for s in layout.surfaces} - {room.cell})
    if bad:
        raise KitRoomError(f"{room.layout_id}: surfaces outside the room's cell: {bad}")
    textures = S.load_texture_set()
    plan, rep = S.resolve({"version": S.VERSION, "cells": {room.cell: room.spec}}, layout, S.PROPS_PATH, textures,
                          areas=[])
    if room.head_door is not None and rep.ok and room.cell in plan.cells:
        try:
            plan.cells[room.cell].openings.append(head_door_opening(room, layout, plan.cells[room.cell]))
        except KitRoomError as e:
            rep.errors.append(str(e))
    return layout, plan, rep


# -- The class door of a room open to the sky -----------------------------
#
# A room open to the sky (class_astronomy: a `grounds` floor with no ceiling) has the
# grounds' low walls (layout_lib OPEN_WALL_HEIGHT, 1 m), too low for the class door, so the
# schema cannot place it. `KitRoom.head_door` = {"run", "t", "piece", "span": [t0, t1], "top"}:
# a hero piece built around the door (the stair head over the tower's stair) stands on the run
# over `span`, its face `top` metres tall; resolve() adds the door to the plan as the schema
# would (the rules then place the door piece, the plinth breaks round it, the Planner cuts its
# hole), and builder() carves the run's low wall out under the span (its inner face, its cap
# and its outer face), so the hero piece stands in for it there (for the lint too).


def head_door_opening(room: KitRoom, layout: L.Layout, cp) -> S.Opening:
    hd = room.head_door
    run = next((r for r in cp.runs if r.id == hd.get("run")), None)
    if run is None:
        raise KitRoomError(f"{room.layout_id}: head_door: unknown run {hd.get('run')!r}")
    ps = G.OPENING_PIECES.get(hd.get("piece"))
    if ps is None or ps.kind != "blind_door":
        raise KitRoomError(f"{room.layout_id}: head_door: {hd.get('piece')!r} is not a blind door piece")
    t = float(hd["t"])
    t0, t1 = t - ps.width / 2.0, t + ps.width / 2.0
    s0, s1 = (float(v) for v in hd["span"])
    if not (run.t0 - 1e-6 <= s0 < t0 - G.OPENING_MARGIN + 1e-6 and t1 + G.OPENING_MARGIN - 1e-6 <= s1 <= run.t1 + 1e-6):
        raise KitRoomError(f"{room.layout_id}: head_door: the span {s0}..{s1} must hold the door and its margins "
                           f"inside {run.id}")
    if float(hd.get("top", 0.0)) < ps.height + G.CORNICE.height:
        raise KitRoomError(f"{room.layout_id}: head_door: its face must rise {ps.height + G.CORNICE.height} m or more "
                           f"(the door and a cornice's height over it)")
    bounds = cp.bays[run.id].bounds(run.t0)
    bay = next(k for k in range(len(bounds) - 1) if bounds[k] <= t <= bounds[k + 1])
    mass = G.wall_mass(layout, run, t0, t1, run.floor, run.floor + ps.height)
    return S.Opening(f"{run.id}:blind_door@{G.fmt_m(t)}", cp.cell, run.surface, run.id, "blind_door", ps.piece, run.axis,
                     run.c, run.sign, t0, t1, run.floor, run.floor + ps.height, ps.depth, bay, wall=mass.thickness)


def carve_head_span(room: KitRoom, b: A.Builder) -> None:
    """The run's low wall taken out under the head door's span (b.plan1)."""
    cp = b.kit_plan.cells[room.cell]
    hd = room.head_door
    run = next(r for r in cp.runs if r.id == hd["run"])
    s0, s1 = (float(v) for v in hd["span"])
    owner = b.layout.by_id(run.surface)
    thick = G.wall_mass(b.layout, run, s0, s1, run.floor, run.top).thickness
    out_c = run.c - run.sign * thick
    polys = b.plan1.polys
    big = 1e3
    key_in = (room.cell, L.wall_material(owner))
    polys[key_in] = L._cut_vertical(polys.get(key_in, []), run.axis, run.c, run.sign, (s0, -big, s1, big))
    key_x = (room.cell, "wall_exterior")
    polys[key_x] = L._cut_vertical(polys.get(key_x, []), run.axis, out_c, -run.sign, (s0, -big, s1, big))
    a, c = sorted((run.c, out_c))
    rect = (s0, a, s1, c) if run.axis == "z" else (a, s0, c, s1)
    polys[key_x] = L._cut_horizontal(polys[key_x], run.top, rect)
    # The lint's wall pieces (Builder.wall_pieces: what openings and lanterns stand on): the
    # hero's face stands in for the low wall over the span, from the floor to `top`.
    pieces = []
    for pc in G.planner_pieces(b.layout):
        on_run = pc.owner.id == run.surface and pc.axis == run.axis and abs(pc.c - run.c) < 1e-6 and pc.sign == run.sign
        if not on_run or pc.tb <= s0 + 1e-6 or pc.ta >= s1 - 1e-6:
            pieces.append(pc)
            continue
        for ta, tb in ((pc.ta, min(pc.tb, s0)), (max(pc.ta, s1), pc.tb)):
            if tb - ta > 1e-6:
                fa, fb = (ta - pc.ta) / (pc.tb - pc.ta), (tb - pc.ta) / (pc.tb - pc.ta)
                pieces.append(L.WallPiece(pc.owner, pc.axis, pc.c, pc.sign, ta, tb, pc.lo.sub(fa, fb), pc.hi.sub(fa, fb)))
    top = run.floor + float(hd["top"])
    pieces.append(L.WallPiece(owner, run.axis, run.c, run.sign, s0, s1, L.Lin(run.floor, run.floor), L.Lin(top, top)))
    b._wall_pieces = pieces


def builder(room: KitRoom, cast_ao: bool = False) -> A.Builder:
    """The castle kit's Builder for the room alone (`cast_ao`: the D1 rays,
    inside Blender only)."""
    layout, plan, rep = resolve(room)
    if not rep.ok or room.cell not in plan.cells:
        raise KitRoomError(f"{room.layout_id}: kit spec:\n  " + "\n  ".join(rep.errors or ["not built"]))
    b = A.Builder(layout, [], kit_props(room), set(), plan, S.load_texture_set(), S.PROPS_PATH, S.TEXTURES_PATH)
    if room.head_door is not None:
        carve_head_span(room, b)
    b.cast_ao = cast_ao
    return b


def build(room: KitRoom, cast_ao: bool = False) -> tuple[A.CellBuild, A.Builder]:
    b = builder(room, cast_ao)
    return b.build(room.cell), b


def lint(room: KitRoom, built=None) -> LI.Report:
    """The castle kit lint on the room (draws, the classroom budget, collider,
    greyscale COLOR_0, loops, coverage, intrusion, knee room, ...)."""
    cb, b = built or build(room)
    return LI.lint_cell(cb, b)


def seats(cb: A.CellBuild, sides=None) -> list[dict]:
    """The manifest's room seats ({pos, yaw, front, back, drop}: the hips'
    floor point, as the Charms classroom's, and the seat's fit) from the
    castle seat rows [x0, z0, x1, z1, y, yaw, front, back, drop]; with
    `sides` (world/seat_sides.Sides of the room's layout) each also says
    `two_sided`."""
    out = []
    rows = sides.mark(cb.seats, cb.seat_backs) if sides is not None else [list(r) + [None] for r in cb.seats]
    for x0, z0, x1, z1, y, yaw, front, back, drop, two in rows:
        seat = {"pos": [round((x0 + x1) / 2, 3), round(y - ROOM_SEAT_HEIGHT, 3), round((z0 + z1) / 2, 3)],
                "yaw": round(yaw, 7), "front": front, "back": back, "drop": drop}
        if two is not None:
            seat["two_sided"] = bool(two)
        out.append(seat)
    return out


def texture_files(textures_dir: str, materials) -> dict[str, dict]:
    """material -> {base, emissive?, emissive_strength, double_sided} from the
    exported texture library (`<textures_dir>/textures.json`, the prepare
    step's castle textures). Raises if one is missing."""
    path = os.path.join(textures_dir, "textures.json")
    if not os.path.exists(path):
        raise KitRoomError(f"{path} missing: export the castle textures first (castle_kit/textures.py)")
    with open(path, encoding="utf-8") as f:
        lib = json.load(f)["materials"]
    out = {}
    for m in sorted(set(materials)):
        spec = lib.get(m)
        if spec is None:
            raise KitRoomError(f"{m}: not in {path}")
        e = {"base": os.path.join(textures_dir, spec["base"]), "double_sided": bool(spec.get("double_sided", False)),
             "emissive_strength": float(spec.get("emissive_strength", 1.0)), "key": spec.get("key")}
        if spec.get("emissive"):
            e["emissive"] = os.path.join(textures_dir, spec["emissive"])
        for k in ("base", "emissive"):
            if k in e and not os.path.exists(e[k]):
                raise KitRoomError(f"{e[k]} missing: export the castle textures first")
        out[m] = e
    return out


def layout_json(room: KitRoom) -> dict:
    return json.loads(json.dumps(room.layout))
