"""`data/world/castle_kit.json`: loading, validation against the layout and
props, and resolution into world-space placements (pure Python, no `bpy`).

The file says which cells are kitted and, per cell, what the rules cannot
derive from the layout: the wall and floor variants, bay overrides, the
arch at every door edge (checked against the layout both ways) and its
open leaves, windows, niches and blind doors by wall run and bay (or over a
door), lanterns, hero placements and the greybox props the kit replaces.
Schema in docs/art/castle-kit.md ("`castle_kit.json`"; later milestones add
bay lines, the wainscot, the sky, candle groups, portrait clusters, spot lanterns, gallery-edge railings and areas kept clear).

    python3 -I tools/blender/castle_kit/schema.py [--kit data/world/castle_kit.json]

prints the resolved placements and exits non-zero on errors.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
if _BLENDER not in sys.path:
    sys.path.insert(0, _BLENDER)
import layout_lib as L  # noqa: E402
from castle_kit import grid as G  # noqa: E402

REPO = os.path.abspath(os.path.join(_BLENDER, "..", ".."))
KIT_PATH = os.path.join(REPO, "data", "world", "castle_kit.json")
LAYOUT_PATH = os.path.join(REPO, "data", "world", "layout.json")
PROPS_PATH = os.path.join(REPO, "data", "world", "props.json")
TEXTURES_PATH = os.path.join(REPO, "data", "art", "castle_textures.json")

VERSION = 1
TOP_KEYS = {"_doc", "version", "camera_max", "cells"}
CAMERA_KEYS = {"default", "low_cap", "cells"}
CELL_KEYS = {"_doc", "build", "budget", "materials", "bay_target", "runs", "floor_border", "springing", "shaft", "arches",
             "openings", "lanterns", "hero", "replaces",
             # castle kit M3 + M4
             "lines", "wainscot", "sky", "candles", "portraits", "railings", "keep_clear",
             # castle D1
             "shade",
             # castle M5c
             "facade",
             # The moving staircase flights this cell draws as swing nodes
             "moving"}
SHADE_KEYS = {"radius", "rays"}
FACADE_KEYS = {"sides", "ext", "roof", "parapet", "buttresses", "pinnacles"}  # castle M5c
ARCH_KEYS = {"door", "surface", "piece", "leaves"}
OPENING_KEYS = {"kind", "piece", "run", "bays", "t", "over", "sill", "line_bays"}
RUN_KEYS = {"bays", "bounds"}
LANTERN_KEYS = {"piece", "every", "y", "runs", "spots"}
SPOT_KEYS = {"run", "t"}
LINES_KEYS = {"axis", "at", "foot", "top", "corbel"}
SKY_KEYS = {"plate", "ridge"}
CANDLE_KEYS = {"id", "rect", "y", "count", "seed", "bob", "lod1"}
BOB_KEYS = {"amp", "period", "phase"}
PORTRAIT_KEYS = {"id", "run", "t", "y", "seed", "mix"}
RAILINGS = ("balustrade", "gallery_edge")
PORTRAIT_GRID = 0.1  # portrait cluster rectangles: t on this grid
SPOT_HALF = 0.3  # a spot lantern's wall plate: this either side of its t
HERO_KEYS = {"id", "piece", "pos", "yaw", "args"}
LANTERN_DROP = 0.6  # a wall lantern hangs at most this below its bracket
CAMERA_RANGE = (4.0, 40.0)
REQUIRED_FAMILIES = ("castle_wall", "castle_floor")


class KitDataError(Exception):
    pass


# -- resolved placements (what rules.py and assemble.py consume) ------------------


@dataclass(frozen=True)
class Opening:
    """A wall opening in world space. Arches stand at door edges (the
    Planner already leaves the hole: width = the contact, apex = the door's
    ceiling); windows, niches and blind doors are holes the Planner cuts
    from the run's wall face. `depth` None = through the wall; `reveal` =
    how far into the door passage this cell's arch geometry reaches."""

    id: str
    cell: str
    surface: str
    run: str | None
    kind: str  # arch | window | niche | blind_door
    piece: str
    axis: str
    c: float
    sign: float
    t0: float
    t1: float
    y0: float
    y1: float
    depth: float | None
    bay: int | None = None
    door: str | None = None  # arches: the door surface; `over` openings: the door they stand over
    leaves: bool = False
    reveal: float | None = None
    # M2: open leaves lie "reveal" (flat against the reveal's jambs) or "face"
    # (flat against this cell's wall face beside the opening); each leaf_width wide.
    leaf_mode: str | None = None
    leaf_width: float | None = None
    over: bool = False  # M2: a window over a door (run None, door = that door)
    wall: float | None = None  # windows, niches, blind doors: the wall mass thickness there (grid.wall_mass)
    line_bay: bool = False  # M3: `bay` numbers the cell's line bays (castle_kit.json line_bays), not the run's

    @property
    def width(self) -> float:
        return self.t1 - self.t0

    @property
    def centre(self) -> float:
        return (self.t0 + self.t1) / 2.0


@dataclass(frozen=True)
class Shaft:
    id: str
    run: str
    k: int  # the bay boundary it stands on (1 .. n-1)
    t: float
    y0: float
    y1: float
    piece: str


@dataclass(frozen=True)
class Lantern:
    id: str
    run: str
    shaft: str
    t: float
    y: float
    piece: str


@dataclass(frozen=True)
class Lines:
    """castle_kit.json `lines` (M3): bay lines across the walls along `axis`."""

    axis: str  # "x": the lines are x = at[i], on the walls of the n and s sides
    at: tuple[float, ...]  # inner lines, increasing
    foot: float  # shafts stand from this height above the local floor (the wainscot's top)
    top: float  # to this height above the cell's lowest floor, where the corbel is
    corbel: str  # the corbel piece on each shaft's top
    ends: tuple[float, float]  # the cell's extent along the axis (its flat surfaces)

    def edges(self) -> tuple[float, ...]:
        return (self.ends[0], *self.at, self.ends[1])


@dataclass(frozen=True)
class LineShaft:
    """A shaft on a bay line (M3), with its corbel. `run` is the wall run
    the line falls in, None over a stair (the dais steps)."""

    id: str
    side: str
    k: int  # 1-based: the line at[k - 1]
    t: float
    axis: str  # the face line axis = c, facing sign (as WallRun)
    c: float
    sign: float
    y0: float
    y1: float
    piece: str
    corbel: str
    run: str | None


@dataclass(frozen=True)
class SkyPanel:
    """One pitch of one line bay of the enchanted ceiling (M3, castle_sky):
    a quad, its corners wound counter-clockwise seen from the hall below,
    `uv` per corner (u along the whole hall, v 0 at the plate, 1 at the ridge)."""

    id: str
    bay: int
    pitch: str  # the side its foot stands on (n / s, or w / e)
    corners: tuple
    uv: tuple
    normal: tuple[float, float, float]


@dataclass(frozen=True)
class CandleGroup:
    """A group of floating candles (M3): one moving node per group."""

    id: str
    rect: tuple[float, float, float, float]  # x0, z0, x1, z1
    y0: float
    y1: float
    count: int
    seed: int
    amp: float
    period: float
    phase: float
    lod1: float


@dataclass(frozen=True)
class PortraitCluster:
    """A wall rectangle filled with framed portraits (M4), seeded."""

    id: str
    run: str
    axis: str
    c: float
    sign: float
    t0: float
    t1: float
    y0: float  # world metres
    y1: float
    seed: int
    mix: str  # grid.PORTRAIT_MIXES
    band: str  # the group its frames go in: l<level of the run's surface>
    material: str  # castle_portraits_a | _b (the cell's castle_portraits variant)


@dataclass(frozen=True)
class Hero:
    id: str
    piece: str
    pos: tuple[float, float, float]
    yaw: float
    args: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class Facade:
    """A facing cell's facade (castle M5c, docs/art/castle-kit.md "Exterior"): the sides
    of its outside the courtyard sees (grid.M5C_FACADE_CELLS) and what the facade
    rules put there: `ext` the exterior material clones on its outside
    faces, a slate `roof`, a crenellated `parapet` on its wall tops, `buttresses`
    and `pinnacles` (pieces/facades.py)."""

    sides: tuple[str, ...]
    ext: bool = True
    roof: bool = False
    parapet: bool = False
    buttresses: bool = False
    pinnacles: bool = False


@dataclass(frozen=True)
class Shade:
    """The vertex-AO bake's numbers for a cell (castle D1, docs/art/castle-kit.md
    "Shading: the AO bake"): the radius and rays of its budget kind (grid.AO_RADIUS, grid.ao_rays)
    unless castle_kit.json `shade` overrides them."""

    radius: float
    rays: int


@dataclass
class CellPlan:
    cell: str
    budget: str
    materials: dict[str, str]  # family -> material name
    floor_border: bool
    springing: float | None = None  # M2: vaulted cells' springing line (cornice top, shaft tops), m above the floor
    shaft: str = "plain"  # M2: the variant of the cell's bay shafts (grid.SHAFT_VARIANTS)
    build: bool = True  # M2: false = validated and resolved, not built yet (KitPlan.planned)
    runs: list[G.WallRun] = field(default_factory=list)
    walled: dict[str, list[tuple[float, float]]] = field(default_factory=dict)  # run id -> floor-to-top wall spans
    bays: dict[str, G.Bays] = field(default_factory=dict)
    openings: list[Opening] = field(default_factory=list)
    shafts: list[Shaft] = field(default_factory=list)
    lanterns: list[Lantern] = field(default_factory=list)
    hero: list[Hero] = field(default_factory=list)
    replaces: list[str] = field(default_factory=list)
    # castle kit M3 + M4
    lines: Lines | None = None
    line_shafts: list[LineShaft] = field(default_factory=list)
    wainscot: float | None = None  # oak wainscot height above the floor (rules.py runs it along every wall)
    railings: str = "balustrade"  # railing runs: the M1 balustrade, or the M4 gallery edge
    sky: list[SkyPanel] = field(default_factory=list)
    sky_plate: float | None = None
    sky_ridge: float | None = None
    candles: list[CandleGroup] = field(default_factory=list)
    portraits: list[PortraitCluster] = field(default_factory=list)
    keep_clear: list[tuple] = field(default_factory=list)  # (area id, (x0, y0, z0), (x1, y1, z1))
    groups: dict[str, G.GroupSpec] = field(default_factory=dict)  # group name -> spec (assemble.py, greybox.py)
    # castle D1
    shade: Shade | None = None  # the bake's radius and rays (always set by resolve)
    # castle M5b: hero id -> the candle group a ring chandelier hangs in (`candles_<k>`)
    hero_groups: dict[str, str] = field(default_factory=dict)
    # castle M5c: the cell's facade (None: not a facing cell)
    facade: Facade | None = None

    def material(self, name: str) -> str:
        """A face tag's material resolved for this cell (families -> variant)."""
        return self.materials.get(name, name)


@dataclass
class KitPlan:
    cells: dict[str, CellPlan]  # the kitted cells greybox.py builds
    camera_default: float
    camera_low_cap: float
    camera: dict[str, float]
    planned: dict[str, CellPlan] = field(default_factory=dict)  # `"build": false`: resolved and checked, not built

    def camera_max(self, cell: str) -> float:
        return self.camera.get(cell, self.camera_default)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# -- loading ---------------------------------------------------------------------------


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_texture_set(path: str = TEXTURES_PATH) -> dict:
    return load_json(path)


def material_families(textures: dict) -> dict[str, list[str]]:
    fams: dict[str, list[str]] = {}
    for name, m in textures["materials"].items():
        if "family" in m:
            fams.setdefault(m["family"], []).append(name)
    return fams


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _multiple(v: float, step: float) -> bool:
    return abs(v / step - round(v / step)) < 1e-6


def _props_by_cell(props_path: str):
    props = L.load_props(props_path) if os.path.exists(props_path) else []
    raw = load_json(props_path) if os.path.exists(props_path) else {}
    kit = [k for k in raw.get("kit", []) if isinstance(k, dict)]
    return props, kit


# -- validation and resolution -------------------------------------------------------------


def layout_areas(path: str = LAYOUT_PATH) -> list[dict]:
    """The layout's `areas` (the dueling club's), which layout_lib does not
    parse: castle_kit.json `keep_clear` names them."""
    if not os.path.exists(path):
        return []
    return [a for a in load_json(path).get("areas", []) if isinstance(a, dict)]


def resolve(data: dict, layout: L.Layout, props_path: str = PROPS_PATH, textures: dict | None = None,
            rep: Report | None = None, areas: list | None = None) -> tuple[KitPlan, Report]:
    """Validates `data` (a parsed castle_kit.json) against the layout, the
    props and the texture set, and resolves it. Errors go into the report;
    the plan holds whatever resolved cleanly. `areas`: the layout's areas
    (default: data/world/layout.json's)."""
    rep = rep or Report()
    textures = textures or load_texture_set()
    err = rep.errors.append
    if not isinstance(data, dict):
        err("castle_kit.json must be an object")
        return KitPlan({}, G.CAMERA_MAX_DEFAULT, G.CAMERA_MAX_LOW_CAP, {}), rep
    for k in sorted(set(data) - TOP_KEYS):
        err(f"unknown top-level field {k!r}")
    if data.get("version") != VERSION:
        err(f"version must be {VERSION}")
    cells_in_layout = set(layout.cells())

    # camera.max
    cam = data.get("camera_max", {})
    cam_default, cam_cap, cam_cells = G.CAMERA_MAX_DEFAULT, G.CAMERA_MAX_LOW_CAP, {}
    if not isinstance(cam, dict):
        err("camera_max must be an object")
    else:
        for k in sorted(set(cam) - CAMERA_KEYS):
            err(f"camera_max: unknown field {k!r}")
        cam_default = cam.get("default", G.CAMERA_MAX_DEFAULT)
        cam_cap = cam.get("low_cap", G.CAMERA_MAX_LOW_CAP)
        if cam_default != G.CAMERA_MAX_DEFAULT:
            err(f"camera_max.default must stay {G.CAMERA_MAX_DEFAULT} (camera.ts MAX_DISTANCE)")
        if cam_cap != G.CAMERA_MAX_LOW_CAP:
            err(f"camera_max.low_cap must stay {G.CAMERA_MAX_LOW_CAP} (the client's Low cap)")
        raw_cells = cam.get("cells", {})
        if not isinstance(raw_cells, dict):
            err("camera_max.cells must map cell ids to metres")
            raw_cells = {}
        for cid, v in sorted(raw_cells.items()):
            if cid not in cells_in_layout:
                err(f"camera_max: unknown cell {cid!r}")
            elif not _num(v) or not CAMERA_RANGE[0] <= v <= CAMERA_RANGE[1]:
                err(f"camera_max.{cid}: must be {CAMERA_RANGE[0]}..{CAMERA_RANGE[1]} m")
            else:
                cam_cells[cid] = float(v)

    plan = KitPlan({}, float(cam_default), float(cam_cap), cam_cells)
    cells = data.get("cells", {})
    if not isinstance(cells, dict):
        err("cells must map cell ids to objects")
        return plan, rep
    props, kit_entries = _props_by_cell(props_path)
    fams = material_families(textures)
    # Cells built in this export: a cell with "build": false is resolved as
    # if built (so its data is checked), but its neighbours treat it as
    # greybox (their arches line the whole passage of doors they own).
    built = {cid for cid, spec in cells.items() if isinstance(spec, dict) and spec.get("build", True) is True}
    pieces = G.planner_pieces(layout)
    for cid in sorted(cells):
        spec = cells[cid]
        if cid not in cells_in_layout:
            err(f"cells: unknown cell {cid!r}")
            continue
        if not isinstance(spec, dict):
            err(f"{cid}: must be an object")
            continue
        cp = _resolve_cell(cid, spec, layout, props, kit_entries, fams, rep, built | {cid}, pieces, areas)
        if cp is not None:
            (plan.cells if cp.build else plan.planned)[cid] = cp
    _check_leaf_owners(plan, rep)
    return plan, rep


def _check_leaf_owners(plan: KitPlan, rep: Report) -> None:
    """At most one arch per door carries leaves (the cell that builds them)."""
    owners: dict[str, list[str]] = {}
    for cid, cp in sorted({**plan.cells, **plan.planned}.items()):
        for o in cp.openings:
            if o.kind == "arch" and o.leaves:
                owners.setdefault(o.door, []).append(cid)
    for door, cids in sorted(owners.items()):
        if len(cids) > 1:
            rep.errors.append(f"door {door}: leaves on more than one side ({cids}); one cell builds them")


def _resolve_cell(cid, spec, layout, props, kit_entries, fams, rep, built=frozenset(), pieces=None,
                  areas=None) -> CellPlan | None:
    err = rep.errors.append
    if pieces is None:
        pieces = G.planner_pieces(layout)
    for k in sorted(set(spec) - CELL_KEYS):
        err(f"{cid}: unknown field {k!r}")
    build = spec.get("build", True)
    if not isinstance(build, bool):
        err(f"{cid}: build must be true or false")
        build = True
    budget = spec.get("budget")
    if budget not in G.BUDGETS:
        err(f"{cid}: budget must be one of {sorted(G.BUDGETS)}")
    mats = spec.get("materials", {})
    materials: dict[str, str] = {}
    if not isinstance(mats, dict):
        err(f"{cid}: materials must map families to material names")
        mats = {}
    for fam in REQUIRED_FAMILIES:
        if fam not in mats:
            err(f"{cid}: materials.{fam} is required")
    for fam, name in sorted(mats.items()):
        if fam not in fams:
            err(f"{cid}: materials: {fam!r} is not a material family ({sorted(fams)})")
        elif name not in fams[fam]:
            err(f"{cid}: materials.{fam}: {name!r} is not one of {sorted(fams[fam])}")
        else:
            materials[fam] = name
    floor_border = spec.get("floor_border", True)
    if not isinstance(floor_border, bool):
        err(f"{cid}: floor_border must be true or false")
    target = spec.get("bay_target", G.BAY_TARGETS["corridor"])
    if not _num(target) or target <= 0:
        err(f"{cid}: bay_target must be a positive number")
        target = G.BAY_TARGETS["corridor"]

    shaft = spec.get("shaft", "plain")
    if shaft not in G.SHAFT_VARIANTS:
        err(f"{cid}: shaft must be one of {G.SHAFT_VARIANTS}")
        shaft = "plain"

    cp = CellPlan(cid, budget if budget in G.BUDGETS else "corridor", materials, bool(floor_border), None, shaft, build)
    surfaces = [s for s in layout.surfaces if s.cell == cid]
    # `moving` names every moving flight whose A surface is in this cell.
    moving = spec.get("moving", [])
    own = sorted({s.moving[0] for s in surfaces if getattr(s, "moving", None)})
    if not isinstance(moving, list) or sorted(moving) != own:
        err(f"{cid}: moving must list the moving flights whose A surface is in the cell ({own})")
    for s in surfaces:
        cp.runs.extend(G.wall_runs(layout, s))
    runs = {r.id: r for r in cp.runs}
    for r in cp.runs:
        cp.walled[r.id] = G.walled_spans(pieces, r, "full")

    _resolve_lines(cid, spec, surfaces, cp, rep)
    line_runs = {r.id for r in cp.runs if cp.lines and r.side in G.LINE_SIDES[cp.lines.axis]}

    springing = spec.get("springing")
    if springing is not None:
        lowest = min((r.top - r.floor for r in cp.runs), default=0.0)
        if not _num(springing) or not G.SPRINGING_MIN <= springing <= lowest - G.CORNICE.height:
            err(f"{cid}: springing must be {G.SPRINGING_MIN}..{G.fmt_m(lowest - G.CORNICE.height)} m above the floor "
                f"(walls {G.fmt_m(lowest)} m, cornice {G.CORNICE.height} m)")
        else:
            cp.springing = float(springing)

    # bays per run
    overrides = spec.get("runs", {})
    if not isinstance(overrides, dict):
        err(f"{cid}: runs must map run ids to objects")
        overrides = {}
    fixed: dict[str, list[float]] = {}
    for rid, o in sorted(overrides.items()):
        if rid not in runs:
            err(f"{cid}: runs: unknown run {rid!r} (runs: {sorted(runs)})")
            continue
        if not isinstance(o, dict) or set(o) - RUN_KEYS or len(o) != 1:
            err(f"{cid}: runs.{rid}: must be {{\"bays\": <count >= 1>}} or {{\"bounds\": [<t>, ...]}}")
            continue
        if "bays" in o and (not isinstance(o["bays"], int) or isinstance(o["bays"], bool) or o["bays"] < 1):
            err(f"{cid}: runs.{rid}: bays must be a count >= 1")
        if "bounds" in o:
            r, ts = runs[rid], o["bounds"]
            if (not isinstance(ts, list) or not all(_num(t) and _multiple(t, G.PLAN_GRID) for t in ts)
                    or any(b - a < 1.0 - 1e-9 for a, b in zip([r.t0, *ts], [*ts, r.t1]))):
                err(f"{cid}: runs.{rid}: bounds must be increasing t on the {G.PLAN_GRID} m grid, every bay at least "
                    f"1 m, inside {G.fmt_m(r.t0)}..{G.fmt_m(r.t1)}")
            else:
                fixed[rid] = [float(t) for t in ts]
    for r in cp.runs:
        o = overrides.get(r.id) if isinstance(overrides.get(r.id), dict) else {}
        if r.id in line_runs:
            if r.id in overrides:
                err(f"{cid}: runs.{r.id}: its bays follow the cell's lines (no override)")
            # M3: bays on the lines' walls follow the lines; the line shafts stand on them.
            inner = [t for t in cp.lines.at if r.t0 + 1e-6 < t < r.t1 - 1e-6]
            cp.bays[r.id] = G.fixed_bays(r.t0, r.t1, inner, target)
            continue
        if r.id in fixed:
            b = G.fixed_bays(r.t0, r.t1, fixed[r.id], target)
        else:
            count = o.get("bays") if isinstance(o.get("bays"), int) and o.get("bays") >= 1 else None
            b = G.solve_bays(r.length, target, count=count)
            if not b.within and r.length >= target * (1 - G.BAY_TOLERANCE) and cp.shaft != "none":
                rep.warnings.append(f"{cid}: {r.id}: {b.n} bays of {b.width:.2f} m, {b.deviation:+.0%} from {target} m")
        cp.bays[r.id] = b
        if cp.shaft == "none":
            continue  # M4: no bay shafts (bays still place openings)
        bounds = b.bounds(r.t0)
        top = r.floor + cp.springing if cp.springing is not None else r.top
        for k in range(1, b.n):
            # A shaft needs wall from the floor to the top (not along a railing edge
            # or where the run opens to a lower ceiling): castle kit M2.
            if not any(a - 1e-6 <= bounds[k] - G.SHAFT_ON_WALL and bounds[k] + G.SHAFT_ON_WALL <= z + 1e-6
                       for a, z in cp.walled[r.id]):
                continue
            cp.shafts.append(Shaft(f"{r.id}:shaft{k}", r.id, k, bounds[k], r.floor, top,
                                   G.shaft_piece(top - r.floor, cp.shaft)))

    _line_shafts(cid, surfaces, cp, pieces)
    wainscot = spec.get("wainscot")
    if wainscot is not None:
        if not _num(wainscot) or not G.PLINTH.height <= wainscot <= G.WAINSCOT_MAX:
            err(f"{cid}: wainscot must be {G.PLINTH.height}..{G.WAINSCOT_MAX} m")
        else:
            cp.wainscot = float(wainscot)
    railings = spec.get("railings", "balustrade")
    if railings not in RAILINGS:
        err(f"{cid}: railings must be one of {RAILINGS}")
    else:
        cp.railings = railings

    _resolve_arches(cid, spec, layout, surfaces, cp, rep, built)
    _resolve_openings(cid, spec, layout, runs, cp, rep, surfaces, pieces)
    _resolve_lanterns(cid, spec, runs, cp, rep, pieces)
    _resolve_hero(cid, spec, cp, rep)
    _resolve_sky(cid, spec, surfaces, runs, cp, rep)
    _resolve_candles(cid, spec, surfaces, cp, rep)
    _resolve_chandeliers(cid, surfaces, cp, rep)  # castle M5b
    _resolve_house_cup_p2b(cid, cp, rep)  # the House Cup's groups
    _resolve_portraits(cid, spec, layout, runs, cp, rep, pieces)
    _resolve_keep_clear(cid, spec, cp, rep, areas)
    _resolve_shade(cid, spec, cp, rep)
    _resolve_facade(cid, spec, cp, rep)  # castle M5c
    _check_props(cid, spec, layout, runs, props, kit_entries, cp, rep)
    _check_leaves(cid, layout, runs, props, kit_entries, cp, rep, pieces)
    cp.openings.sort(key=lambda o: o.id)
    return cp


def door_edges(layout, surfaces) -> dict:
    """(door id, surface id) -> (surface, Contact) for every door surface
    joining one of `surfaces` across an edge. Stairs count (castle kit M2:
    the Marble Staircase reaches the Marble Arch) where their floor is level
    along the edge."""
    out = {}
    for s in surfaces:
        if s.kind == "door":
            continue
        for j in G.contacts(layout, s):
            if j.is_door:
                out[(j.other, s.id)] = (s, j)
    return out


def _edge_floor(s, side: str, t0: float, t1: float) -> tuple[float, float]:
    axis, c, _, _ = G.side_line(s, side)
    if axis == "x":
        return s.height(c, t0), s.height(c, t1)
    return s.height(t0, c), s.height(t1, c)


def _far_cells(layout, door, s) -> set[str]:
    """Cells the door joins other than the one `s` belongs to."""
    out = set()
    for j in G.contacts(layout, door):
        o = layout.by_id(j.other)
        if o.cell != s.cell and o.kind != "door":
            out.add(o.cell)
    return out


def _resolve_arches(cid, spec, layout, surfaces, cp, rep, built=frozenset()) -> None:
    err = rep.errors.append
    arches = spec.get("arches", [])
    if not isinstance(arches, list):
        err(f"{cid}: arches must be a list")
        return
    wanted = door_edges(layout, surfaces)
    seen = set()
    for i, a in enumerate(arches):
        where = f"{cid}: arches[{i}]"
        if not isinstance(a, dict):
            err(f"{where}: must be an object")
            continue
        for k in sorted(set(a) - ARCH_KEYS):
            err(f"{where}: unknown field {k!r}")
        key = (a.get("door"), a.get("surface"))
        if key in seen:
            err(f"{where}: duplicate arch for door {key[0]!r} on {key[1]!r}")
            continue
        seen.add(key)
        if key not in wanted:
            err(f"{where}: door {key[0]!r} does not join surface {key[1]!r} of this cell across an edge")
            continue
        s, j = wanted[key]
        door = layout.by_id(j.other)
        if door.ceiling is None:
            err(f"{where}: door {door.id} has no ceiling (the arch's apex)")
            continue
        span, apex = j.t1 - j.t0, door.ceiling
        piece = G.arch_piece(span, apex)
        if a.get("piece") != piece:
            err(f"{where}: piece must be {piece!r} (contact {G.fmt_m(span)} m, door ceiling {G.fmt_m(apex)} m)")
        leaves = a.get("leaves", False)
        if not isinstance(leaves, bool):
            err(f"{where}: leaves must be true or false")
        if span < G.DOOR_CLEAR_WIDTH + 2 * G.ARCH_CLEAR_INSET or G.arch_clear_height(span, apex) < G.DOOR_CLEAR_HEIGHT:
            err(f"{where}: a {G.fmt_m(span)} x {G.fmt_m(apex)} arch leaves less than "
                f"{G.DOOR_CLEAR_WIDTH} x {G.DOOR_CLEAR_HEIGHT} m clear")
        if s.sloped:
            fa, fb = _edge_floor(s, j.side, j.t0, j.t1)
            if abs(fa - fb) > 1e-6 or abs(fa - door.y) > L.STEP_HEIGHT:
                err(f"{where}: {s.id} is not level with {door.id} along that edge (an arch needs a level sill)")
                continue
        axis, c, sign, _ = G.side_line(s, j.side)
        depth = door.x1 - door.x0 if axis == "x" else door.z1 - door.z0
        share = min(G.ARCH_REVEAL, depth / 2.0)
        if door.cell == cid:
            # The door's own cell lines its passage, less the far side's share when that cell is kitted too.
            reveal = depth - share if _far_cells(layout, door, s) & set(built) else depth
        else:
            reveal = share
        leaf_mode = leaf_w = None
        if leaves is True:
            leaf_w = G.leaf_width(span)
            leaf_mode = "reveal" if reveal >= leaf_w + G.LEAF_REVEAL_CLEAR - 1e-9 else "face"
        cp.openings.append(Opening(f"{s.id}:{j.side}:arch:{door.id}", cid, s.id, None, "arch", piece, axis, c, sign, j.t0,
                                   j.t1, door.y, door.y + apex, depth, None, door.id, leaves is True, reveal, leaf_mode,
                                   leaf_w))
    for key in sorted(set(wanted) - seen):
        err(f"{cid}: no arch for door {key[0]!r} on {key[1]!r} (every door edge of a kitted cell has one)")


def _resolve_openings(cid, spec, layout, runs, cp, rep, surfaces=(), pieces=()) -> None:
    err = rep.errors.append
    openings = spec.get("openings", [])
    if not isinstance(openings, list):
        err(f"{cid}: openings must be a list")
        return
    edges = door_edges(layout, surfaces)
    taken: dict[str, list[Opening]] = {}
    for i, o in enumerate(openings):
        where = f"{cid}: openings[{i}]"
        if not isinstance(o, dict):
            err(f"{where}: must be an object")
            continue
        for k in sorted(set(o) - OPENING_KEYS):
            err(f"{where}: unknown field {k!r}")
        kind, piece = o.get("kind"), o.get("piece")
        if kind not in G.OPENING_KINDS:
            err(f"{where}: kind must be one of {G.OPENING_KINDS}")
            continue
        ps = G.OPENING_PIECES.get(piece)
        if ps is None or ps.kind != kind:
            err(f"{where}: piece must be a {kind} piece ({sorted(p for p, v in G.OPENING_PIECES.items() if v.kind == kind)})")
            continue
        if "over" in o:
            _resolve_over(cid, where, o, ps, layout, edges, cp, rep, pieces)
            continue
        run = runs.get(o.get("run"))
        if run is None:
            err(f"{where}: unknown run {o.get('run')!r} (runs: {sorted(runs)})")
            continue
        sill = o.get("sill")
        if not _num(sill) or sill < 0 or not _multiple(sill, G.COURSE):
            err(f"{where}: sill must be a multiple of the {G.COURSE} m course")
            continue
        if kind in G.FLOOR_OPENINGS and sill != 0:
            err(f"{where}: a {kind} stands on the floor (sill 0)")
            continue
        if kind not in G.FLOOR_OPENINGS and sill < G.PLINTH.height:
            err(f"{where}: sill below the plinth ({G.PLINTH.height} m)")
            continue
        wains = spec.get("wainscot")
        if kind == "window" and _num(wains) and sill < wains + G.WAINSCOT_CLEAR - 1e-6:
            err(f"{where}: a window's sill must clear the {G.fmt_m(wains)} m wainscot by {G.WAINSCOT_CLEAR} m")
            continue
        y0, y1 = run.floor + sill, run.floor + sill + ps.height
        if y1 > run.top - G.CORNICE.height + 1e-6:
            err(f"{where}: top at {G.fmt_m(y1 - run.floor)} m runs into the cornice "
                f"(wall {G.fmt_m(run.top - run.floor)} m, cornice {G.CORNICE.height} m)")
            continue
        bays = cp.bays[run.id]
        bounds = bays.bounds(run.t0)
        has_bays, has_t, has_lines = "bays" in o, "t" in o, "line_bays" in o
        if has_bays + has_t + has_lines != 1:
            err(f"{where}: give one of bays, t or line_bays")
            continue
        centres: list[tuple[int | None, float]] = []
        if has_lines:
            # M3: bays of the cell's lines (cell-wide numbers), inside this run.
            if cp.lines is None or run.side not in G.LINE_SIDES[cp.lines.axis]:
                err(f"{where}: line_bays needs the cell's lines across {run.id}'s wall")
                continue
            edges = cp.lines.edges()
            sel = o["line_bays"]
            if (not isinstance(sel, list) or not sel or not all(isinstance(b, int) and not isinstance(b, bool) for b in sel)
                    or any(not 0 <= b < len(edges) - 1 for b in sel)):
                err(f"{where}: line_bays must list bays 0..{len(edges) - 2} of the cell's lines")
                continue
            bad = False
            for b in sel:
                lo, hi = edges[b], edges[b + 1]
                tc = (lo + hi) / 2.0
                t0, t1 = tc - ps.width / 2.0, tc + ps.width / 2.0
                if t0 < lo + G.OPENING_MARGIN - 1e-6 or t1 > hi - G.OPENING_MARGIN + 1e-6:
                    err(f"{where}: a {G.fmt_m(ps.width)} m {kind} does not fit line bay {b} ({G.fmt_m(lo)}..{G.fmt_m(hi)})")
                    bad = True
                elif t0 < run.t0 - 1e-6 or t1 > run.t1 + 1e-6:
                    err(f"{where}: line bay {b}'s {kind} at {G.fmt_m(t0)}..{G.fmt_m(t1)} is not inside {run.id} "
                        f"({G.fmt_m(run.t0)}..{G.fmt_m(run.t1)})")
                    bad = True
                else:
                    centres.append((b, tc))
            if bad:
                continue
            bays = G.Bays(len(edges) - 1, 0.0, 0.0, True, tuple(edges))
            bounds = list(edges)
        elif has_bays:
            sel = o["bays"]
            if sel == "all":
                sel = list(range(bays.n))
            if not isinstance(sel, list) or not sel or not all(isinstance(b, int) and not isinstance(b, bool) for b in sel):
                err(f"{where}: bays must be \"all\" or a list of bay indices")
                continue
            bad = [b for b in sel if not 0 <= b < bays.n]
            if bad:
                err(f"{where}: {run.id} has bays 0..{bays.n - 1}, not {bad}")
                continue
            for b in sel:
                centres.append((b, (bounds[b] + bounds[b + 1]) / 2.0))
        else:
            t = o["t"]
            if not _num(t) or not _multiple(t, G.PLAN_GRID):
                err(f"{where}: t must be on the {G.PLAN_GRID} m plan grid")
                continue
            b = next((k for k in range(bays.n) if bounds[k] <= t <= bounds[k + 1]), None)
            if b is None:
                err(f"{where}: t {t} is outside {run.id} ({run.t0}..{run.t1})")
                continue
            centres.append((b, float(t)))
        for b, tc in centres:
            t0, t1 = tc - ps.width / 2.0, tc + ps.width / 2.0
            if t0 < bounds[b] + G.OPENING_MARGIN - 1e-6 or t1 > bounds[b + 1] - G.OPENING_MARGIN + 1e-6:
                err(f"{where}: a {G.fmt_m(ps.width)} m {kind} does not fit bay {b} of {run.id} "
                    f"({bays.width:.2f} m, {G.OPENING_MARGIN} m kept at each end)")
                continue
            if not G.face_covered(pieces, run.surface, run.axis, run.c, run.sign, t0, t1,
                                  run.floor if kind != "window" else y0, y1):
                err(f"{where}: no wall behind bay {b} of {run.id} at t {G.fmt_m(t0)}..{G.fmt_m(t1)} "
                    f"(a railing edge, or open to a lower ceiling)")
                continue
            mass = G.wall_mass(layout, run, t0, t1, y0, y1)
            if kind == "window" and not mass.outside:
                err(f"{where}: window in bay {b} of {run.id} would open into {list(mass.behind)}")
                continue
            if ps.depth is not None and ps.depth > mass.thickness - G.REVEAL_MARGIN + 1e-6:
                err(f"{where}: a {ps.depth} m {kind} is deeper than the wall mass allows "
                    f"({mass.thickness:.2f} m - {G.REVEAL_MARGIN} m) in bay {b} of {run.id}")
                continue
            oid = f"{run.id}:{kind}{b}" if has_bays else (f"{run.id}:{kind}L{b}" if has_lines else f"{run.id}:{kind}@{G.fmt_m(tc)}")
            op = Opening(oid, cid, run.surface,
                         run.id, kind, piece, run.axis, run.c, run.sign, t0, t1, y0, y1, ps.depth, b,
                         wall=mass.thickness, line_bay=has_lines)
            clash = next((x for x in taken.get(run.id, []) if x.t0 < op.t1 - 1e-6 and op.t0 < x.t1 - 1e-6), None)
            if clash is not None:
                err(f"{where}: {op.id} overlaps {clash.id}")
                continue
            taken.setdefault(run.id, []).append(op)
            cp.openings.append(op)


def _resolve_over(cid, where, o, ps, layout, edges, cp, rep, pieces) -> None:
    """A window over a door (castle kit M2: the traceried window over the
    front doors): centred on the door edge, at least a course above the
    door's apex, no wider than the door."""
    err = rep.errors.append
    if set(o) & {"run", "bays", "t"}:
        err(f"{where}: an opening over a door takes no run, bays or t")
        return
    if ps.kind != "window":
        err(f"{where}: only windows stand over doors")
        return
    found = [(s, j) for (d, _), (s, j) in sorted(edges.items(), key=lambda kv: kv[0]) if d == o["over"]]
    if len(found) != 1:
        err(f"{where}: over: {o['over']!r} must be a door joining exactly one surface of this cell")
        return
    s, j = found[0]
    door = layout.by_id(j.other)
    if s.sloped:
        err(f"{where}: over: {door.id} meets a stair of this cell (no wall above it to cut)")
        return
    sill = o.get("sill")
    if not _num(sill) or not _multiple(sill, G.COURSE):
        err(f"{where}: sill must be a multiple of the {G.COURSE} m course")
        return
    axis, c, sign, _ = G.side_line(s, j.side)
    floor = s.y
    apex = (door.ceiling or 0.0) + door.y - floor
    if sill < apex + G.OVER_DOOR_CLEAR - 1e-6:
        err(f"{where}: sill {G.fmt_m(sill)} m must be at least {G.fmt_m(apex + G.OVER_DOOR_CLEAR)} m "
            f"(the door's apex {G.fmt_m(apex)} m and one course)")
        return
    top = G.wall_top(s)
    y0, y1 = floor + sill, floor + sill + ps.height
    if y1 > top - G.CORNICE.height + 1e-6:
        err(f"{where}: top at {G.fmt_m(y1 - floor)} m runs into the cornice (wall {G.fmt_m(top - floor)} m)")
        return
    span = j.t1 - j.t0
    if ps.width > span + 1e-6:
        err(f"{where}: a {G.fmt_m(ps.width)} m window is wider than {door.id} ({G.fmt_m(span)} m)")
        return
    tc = (j.t0 + j.t1) / 2.0
    t0, t1 = tc - ps.width / 2.0, tc + ps.width / 2.0
    if not G.face_covered(pieces, s.id, axis, c, sign, t0, t1, y0, y1):
        err(f"{where}: no wall over {door.id} at {G.fmt_m(sill)}..{G.fmt_m(sill + ps.height)} m")
        return
    run = G.WallRun(f"{s.id}:{j.side}:over:{door.id}", s.id, s.cell, j.side, -1, axis, c, sign, j.t0, j.t1, floor, top)
    mass = G.wall_mass(layout, run, t0, t1, y0, y1)
    if not mass.outside:
        err(f"{where}: window over {door.id} would open into {list(mass.behind)}")
        return
    cp.openings.append(Opening(f"{s.id}:{j.side}:{ps.kind}:over:{door.id}", cid, s.id, None, ps.kind, ps.piece, axis, c,
                               sign, t0, t1, y0, y1, ps.depth, None, door.id, over=True, wall=mass.thickness))


def _check_leaves(cid, layout, runs, props, kit_entries, cp, rep, pieces) -> None:
    """The open-leaf rule (castle kit M2): leaves lie in the reveal when it
    is deep enough, else flat against this cell's wall face beside the
    opening, beyond the arch's quoins. There, the face must be wall from the
    door's floor to its apex over both leaves, clear of other openings,
    shafts, lanterns, wall tells and kept props."""
    err = rep.errors.append
    replaced = set(cp.replaces)
    for op in cp.openings:
        if op.kind != "arch" or op.leaf_mode != "face":
            continue
        w = op.leaf_width
        zones = ((op.t0 - G.ARCH_SURROUND - w, op.t0 - G.ARCH_SURROUND),
                 (op.t1 + G.ARCH_SURROUND, op.t1 + G.ARCH_SURROUND + w))
        for za, zb in zones:
            where = f"{cid}: arch {op.door}: open leaf at t {G.fmt_m(za)}..{G.fmt_m(zb)}"
            if not G.face_covered(pieces, None, op.axis, op.c, op.sign, za, zb, op.y0, op.y1, cell=cid):
                err(f"{where} has no wall to lie against (from the floor to {G.fmt_m(op.y1 - op.y0)} m)")
                continue
            lo, hi = za - G.LEAF_CLEAR, zb + G.LEAF_CLEAR
            for x in cp.openings:
                if x is op or x.axis != op.axis or abs(x.c - op.c) > 1e-6 or x.sign != op.sign:
                    continue
                reach = G.ARCH_SURROUND if x.kind == "arch" else 0.0
                if x.t0 - reach < hi and lo < x.t1 + reach and x.y0 < op.y1 and op.y0 < x.y1:
                    err(f"{where} would cover {x.id}")
            on_face = {r.id for r in runs.values() if r.axis == op.axis and abs(r.c - op.c) < 1e-6 and r.sign == op.sign}
            for sh in cp.shafts:
                if sh.run in on_face and lo < sh.t + G.SHAFT.height / 2 and sh.t - G.SHAFT.height / 2 < hi:
                    err(f"{where} would cover {sh.id} (move the bay bounds)")
            for k in kit_entries:
                if k.get("cell") != cid or k.get("id") in replaced:
                    continue
                pos = k.get("pos")
                if not (isinstance(pos, list) and len(pos) == 3 and all(_num(v) for v in pos)):
                    continue
                t = pos[0] if op.axis == "z" else pos[2]
                d = ((pos[2] if op.axis == "z" else pos[0]) - op.c) * op.sign
                if -G.WALL_TOUCH <= d <= 0.6 and lo - G.TELL_HALF_WIDTH < t < hi + G.TELL_HALF_WIDTH \
                        and op.y0 - 0.5 < pos[1] < op.y1:
                    err(f"{where} would cover kit entry {k.get('id')}")
            for p in props:
                if p.cell != cid or p.id in replaced:
                    continue
                lo_t, hi_t = (p.min[0], p.max[0]) if op.axis == "z" else (p.min[2], p.max[2])
                lo_d, hi_d = (p.min[2], p.max[2]) if op.axis == "z" else (p.min[0], p.max[0])
                d0, _ = sorted(((lo_d - op.c) * op.sign, (hi_d - op.c) * op.sign))
                if lo_t < hi and lo < hi_t and d0 < 0.6 and p.min[1] < op.y1:
                    err(f"{where} would cover prop {p.id}")
            for h in cp.hero:
                t = h.pos[0] if op.axis == "z" else h.pos[2]
                d = ((h.pos[2] if op.axis == "z" else h.pos[0]) - op.c) * op.sign
                if -1.0 <= d <= 0.6 and lo < t < hi and h.pos[1] < op.y1:
                    err(f"{where} would cover hero {h.id}")


def _resolve_lanterns(cid, spec, runs, cp, rep, pieces=()) -> None:
    err = rep.errors.append
    lan = spec.get("lanterns")
    if lan is None:
        return
    if not isinstance(lan, dict):
        err(f"{cid}: lanterns must be an object or null")
        return
    for k in sorted(set(lan) - LANTERN_KEYS):
        err(f"{cid}: lanterns: unknown field {k!r}")
    piece, every, y = lan.get("piece"), lan.get("every"), lan.get("y")
    spots = lan.get("spots")
    if not isinstance(piece, str) or not G.PIECE_RE.match(piece) or not piece.startswith("kit_lantern_wall"):
        err(f"{cid}: lanterns.piece must be a kit_lantern_wall piece")
        return
    if (every is not None or spots is None) and (not isinstance(every, int) or isinstance(every, bool) or every < 1):
        err(f"{cid}: lanterns.every must be a whole number >= 1")
        return
    if not _num(y) or y - LANTERN_DROP < G.OVERHEAD_CLEARANCE:
        err(f"{cid}: lanterns.y must leave {G.OVERHEAD_CLEARANCE} m under a lantern ({LANTERN_DROP} m drop)")
        return
    only = lan.get("runs")
    if only is not None:
        if not isinstance(only, list) or any(r not in runs for r in only):
            err(f"{cid}: lanterns.runs must list runs of this cell")
            return
    if every is not None:
        for sh in cp.shafts:
            if sh.k % every == 0 and (only is None or sh.run in only):
                r = runs[sh.run]
                cp.lanterns.append(Lantern(f"{sh.id}:lantern", sh.run, sh.id, sh.t, r.floor + y, piece))
    if spots is None:
        return
    # Castle kit M4: lanterns on walls without bay shafts (the Grand Staircase), at
    # given t on a run's face line (any owner draws the wall); `shaft` is "".
    if not isinstance(spots, list):
        err(f"{cid}: lanterns.spots must be a list of {{\"run\", \"t\": [...]}}")
        return
    seen = set()
    for i, sp in enumerate(spots):
        where = f"{cid}: lanterns.spots[{i}]"
        if not isinstance(sp, dict) or set(sp) - SPOT_KEYS or not SPOT_KEYS <= set(sp):
            err(f"{where}: must be {{\"run\": <run id>, \"t\": [<t>, ...]}}")
            continue
        r = runs.get(sp["run"])
        ts = sp["t"]
        if r is None:
            err(f"{where}: unknown run {sp['run']!r}")
            continue
        if not isinstance(ts, list) or not ts or not all(_num(t) and _multiple(t, G.PLAN_GRID) for t in ts):
            err(f"{where}: t must list positions on the {G.PLAN_GRID} m grid")
            continue
        for t in ts:
            yy = r.floor + y
            if not G.face_walled(pieces, r.axis, r.c, r.sign, t - SPOT_HALF, t + SPOT_HALF, yy - 0.4, yy + 0.2):
                err(f"{where}: no wall for a lantern at t {G.fmt_m(t)}, {G.fmt_m(y)} m above {r.id}'s floor")
                continue
            hit = _opening_near(cp, r.axis, r.c, r.sign, t - SPOT_HALF, t + SPOT_HALF, yy - 0.6, yy + 0.3, 0.3)
            if hit is not None:
                err(f"{where}: the lantern at t {G.fmt_m(t)} stands on {hit.id}")
                continue
            lid = f"{r.id}:lantern@{G.fmt_m(t)}"
            if lid in seen:
                err(f"{where}: two lanterns at t {G.fmt_m(t)} on {r.id}")
                continue
            seen.add(lid)
            cp.lanterns.append(Lantern(lid, r.id, "", float(t), yy, piece))


def _opening_near(cp, axis, c, sign, t0, t1, y0, y1, clear: float):
    """The first opening of the cell on this face within `clear` of the
    rectangle (arches with their quoins and hood mould), or None."""
    for o in cp.openings:
        if o.axis != axis or abs(o.c - c) > 1e-6 or o.sign != sign:
            continue
        reach = G.ARCH_SURROUND if o.kind == "arch" else 0.0
        top = o.y1 + (G.OVER_DOOR_CLEAR if o.kind == "arch" else 0.0)
        if o.t0 - reach - clear < t1 and t0 < o.t1 + reach + clear and o.y0 - clear < y1 and y0 < top + clear:
            return o
    return None


def _flat(surfaces):
    return [s for s in surfaces if s.kind != "door"]


def _resolve_lines(cid, spec, surfaces, cp, rep) -> None:
    """castle_kit.json `lines` (M3): the cell's bay lines along one axis."""
    err = rep.errors.append
    ln = spec.get("lines")
    if ln is None:
        return
    if not isinstance(ln, dict) or set(ln) - LINES_KEYS or not {"axis", "at"} <= set(ln):
        err(f"{cid}: lines must be {{\"axis\", \"at\": [...], \"foot\", \"top\", \"corbel\"}}")
        return
    axis, at = ln["axis"], ln["at"]
    foot, top, corbel = ln.get("foot", 0.0), ln.get("top"), ln.get("corbel", "kit_stone_corbel_1")
    if axis not in G.LINE_AXES:
        err(f"{cid}: lines.axis must be one of {G.LINE_AXES}")
        return
    flat = _flat(surfaces)
    if not flat:
        err(f"{cid}: lines need floors")
        return
    lo = min(s.extent(axis)[0] for s in flat)
    hi = max(s.extent(axis)[1] for s in flat)
    edges = [lo, *at] if isinstance(at, list) else []
    if (not isinstance(at, list) or not at or not all(_num(t) and _multiple(t, G.PLAN_GRID) for t in at)
            or any(b - a < 1.0 - 1e-9 for a, b in zip(edges + [hi], (edges + [hi])[1:]))):
        err(f"{cid}: lines.at must be increasing on the {G.PLAN_GRID} m grid, at least 1 m apart and inside "
            f"{G.fmt_m(lo)}..{G.fmt_m(hi)}")
        return
    base = min(s.y for s in flat if not s.sloped)
    walls = min(r.top - r.floor for r in cp.runs) if cp.runs else 0.0
    if not _num(foot) or foot < 0:
        err(f"{cid}: lines.foot must be >= 0")
        return
    if not _num(top) or top < foot + 1.0 or top > walls - G.CORNICE.height:
        err(f"{cid}: lines.top must be {G.fmt_m(foot + 1.0)}..{G.fmt_m(walls - G.CORNICE.height)} m above the floor")
        return
    if not isinstance(corbel, str) or not corbel.startswith("kit_stone_corbel_") or not G.PIECE_RE.match(corbel):
        err(f"{cid}: lines.corbel must be a kit_stone_corbel_<size> piece")
        return
    cp.lines = Lines(axis, tuple(float(t) for t in at), float(foot), float(base + top), corbel, (float(lo), float(hi)))


def _line_shafts(cid, surfaces, cp, pieces) -> None:
    """A shaft and corbel on every line wherever the face is wall from the
    local floor + foot to the line top (any owner, sloped bottoms allowed)."""
    ln = cp.lines
    if ln is None or cp.shaft == "none":
        return
    flat = _flat(surfaces)
    for side in G.LINE_SIDES[ln.axis]:
        faces = sorted({(r.axis, r.c, r.sign) for r in cp.runs if r.side == side})
        for axis, c, sign in faces:
            for k, t in enumerate(ln.at, start=1):
                heights = []
                for s in flat:
                    s_axis, s_c, _, (a, b) = G.side_line(s, side)
                    if s_axis != axis or abs(s_c - c) > 1e-6:
                        continue
                    for q in (t - G.SHAFT_ON_WALL, t, t + G.SHAFT_ON_WALL):
                        if a - 1e-6 <= q <= b + 1e-6:
                            heights.append(s.height(q, c) if axis == "z" else s.height(c, q))
                if not heights:
                    continue
                y0, y1 = max(heights) + ln.foot, ln.top
                if y1 - y0 < 1.0 or not G.face_walled(pieces, axis, c, sign, t - G.SHAFT_ON_WALL, t + G.SHAFT_ON_WALL,
                                                       y0, y1):
                    continue
                run = next((r.id for r in cp.runs if r.side == side and r.axis == axis and abs(r.c - c) < 1e-6
                            and r.t0 - 1e-6 <= t <= r.t1 + 1e-6), None)
                cp.line_shafts.append(LineShaft(f"{cid}:{side}@{G.fmt_m(c)}:line{k}", side, k, t, axis, c, sign,
                                                round(y0, 4), y1, G.shaft_piece(round(y1 - y0, 2), cp.shaft), ln.corbel,
                                                run))


def _resolve_sky(cid, spec, surfaces, runs, cp, rep) -> None:
    """castle_kit.json `sky` (M3): the enchanted ceiling's panels, two
    pitches per line bay from the wall plate to the ridge."""
    err = rep.errors.append
    sk = spec.get("sky")
    if sk is None:
        return
    if not isinstance(sk, dict) or set(sk) != SKY_KEYS or not all(_num(v) for v in sk.values()):
        err(f"{cid}: sky must be {{\"plate\": <m>, \"ridge\": <m>}}")
        return
    ln = cp.lines
    if ln is None:
        err(f"{cid}: sky needs the cell's lines (one pair of panels per line bay)")
        return
    flat = _flat(surfaces)
    base = min(s.y for s in flat if not s.sloped)
    plate, ridge = base + float(sk["plate"]), base + float(sk["ridge"])
    ceiling = min(s.ceil_y for s in flat if s.roofed)
    if cp.springing is not None and abs(plate - (base + cp.springing)) > 1e-6:
        err(f"{cid}: sky.plate must be the springing line ({G.fmt_m(cp.springing)} m): the cornice is the wall plate")
        return
    if ridge < plate + 2.0 or ridge > ceiling - G.SKY_CEILING_CLEAR + 1e-6:
        err(f"{cid}: sky.ridge must be {G.fmt_m(plate + 2 - base)}..{G.fmt_m(ceiling - G.SKY_CEILING_CLEAR - base)} m")
        return
    other = "z" if ln.axis == "x" else "x"
    c0 = min(s.extent(other)[0] for s in flat)
    c1 = max(s.extent(other)[1] for s in flat)
    cm = (c0 + c1) / 2.0
    lo, hi = ln.ends
    length = hi - lo

    def pt(along, y, across):
        return (along, y, across) if ln.axis == "x" else (across, y, along)

    def nrm(dy, dacross):
        n = (0.0, dy, dacross) if ln.axis == "x" else (dacross, dy, 0.0)
        m = math.sqrt(sum(v * v for v in n))
        return tuple(v / m for v in n)

    rise, half = ridge - plate, cm - c0
    side_lo, side_hi = G.LINE_SIDES[ln.axis]
    edges = ln.edges()
    for k, (a, b) in enumerate(zip(edges, edges[1:])):
        ua, ub = (a - lo) / length, (b - lo) / length
        cp.sky.append(SkyPanel(f"{cid}:sky{k}{side_lo}", k, side_lo,
                               (pt(a, plate, c0), pt(b, plate, c0), pt(b, ridge, cm), pt(a, ridge, cm)),
                               ((ua, 0.0), (ub, 0.0), (ub, 1.0), (ua, 1.0)), nrm(-half, rise)))
        cp.sky.append(SkyPanel(f"{cid}:sky{k}{side_hi}", k, side_hi,
                               (pt(b, plate, c1), pt(a, plate, c1), pt(a, ridge, cm), pt(b, ridge, cm)),
                               ((ub, 0.0), (ua, 0.0), (ua, 1.0), (ub, 1.0)), nrm(-half, -rise)))
    if ln.axis == "z":
        # pt() swaps the panel winding with the axes: keep every panel facing the hall below.
        cp.sky = [SkyPanel(p.id, p.bay, p.pitch, tuple(reversed(p.corners)), tuple(reversed(p.uv)), p.normal) for p in cp.sky]
    cp.sky_plate, cp.sky_ridge = plate, ridge
    for o in cp.openings:
        if o.kind == "arch":
            continue
        r = runs.get(o.run) if o.run else None
        side = r.side if r is not None else None
        if side in (side_lo, side_hi):
            if o.y1 > plate - G.SKY_OPENING_CLEAR + 1e-6:
                err(f"{cid}: {o.id} rises into the sky's plate ({G.fmt_m(plate - G.SKY_OPENING_CLEAR - base)} m at most)")
        else:
            far = max(abs(o.t0 - cm), abs(o.t1 - cm))
            roof = plate + rise * max(0.0, 1.0 - far / half)
            if o.y1 > roof - G.SKY_OPENING_CLEAR + 1e-6:
                err(f"{cid}: {o.id} rises above the gable's roof line ({G.fmt_m(roof - G.SKY_OPENING_CLEAR)} m there)")


def _resolve_candles(cid, spec, surfaces, cp, rep) -> None:
    """castle_kit.json `candles` (M3): floating candle groups, each one
    moving node (the client bobs it), over the overhead clearance."""
    err = rep.errors.append
    groups = spec.get("candles", [])
    if not isinstance(groups, list):
        err(f"{cid}: candles must be a list")
        return
    if len(groups) > G.CANDLE_GROUPS_MAX:
        err(f"{cid}: at most {G.CANDLE_GROUPS_MAX} candle groups (one moving node each)")
        return
    flat = _flat(surfaces)
    for i, g in enumerate(groups):
        where = f"{cid}: candles[{i}]"
        if not isinstance(g, dict) or set(g) - CANDLE_KEYS or not {"id", "rect", "y", "count", "seed"} <= set(g):
            err(f"{where}: must be {{\"id\", \"rect\", \"y\", \"count\", \"seed\", \"bob\"?, \"lod1\"?}}")
            continue
        if g["id"] != f"candles_{i}":
            err(f"{where}: id must be candles_{i} (the group's node name)")
            continue
        rect, ys, count, seed = g["rect"], g["y"], g["count"], g["seed"]
        bob = g.get("bob", {})
        lod1 = g.get("lod1", G.LOD_CANDLES)
        if (not isinstance(rect, list) or len(rect) != 4 or not all(_num(v) and _multiple(v, G.PLAN_GRID) for v in rect)
                or rect[2] - rect[0] < 1.0 or rect[3] - rect[1] < 1.0):
            err(f"{where}: rect must be [x0, z0, x1, z1] on the {G.PLAN_GRID} m grid, at least 1 m each way")
            continue
        x0, z0, x1, z1 = (float(v) for v in rect)
        under = [s for s in flat if s.x0 < x1 - 1e-6 and x0 < s.x1 - 1e-6 and s.z0 < z1 - 1e-6 and z0 < s.z1 - 1e-6]
        lo_x, hi_x = min(s.x0 for s in flat), max(s.x1 for s in flat)
        lo_z, hi_z = min(s.z0 for s in flat), max(s.z1 for s in flat)
        if not under or not (lo_x + 0.5 <= x0 and x1 <= hi_x - 0.5 and lo_z + 0.5 <= z0 and z1 <= hi_z - 0.5):
            err(f"{where}: rect must lie over the cell's floors, 0.5 m inside its walls")
            continue
        if not isinstance(ys, list) or len(ys) != 2 or not all(_num(v) for v in ys) or ys[1] <= ys[0]:
            err(f"{where}: y must be [low, high] metres")
            continue
        if not isinstance(bob, dict) or set(bob) - BOB_KEYS or not all(_num(v) for v in bob.values()):
            err(f"{where}: bob must be {{\"amp\", \"period\", \"phase\"}}")
            continue
        amp, period, phase = float(bob.get("amp", 0.05)), float(bob.get("period", 5.0)), float(bob.get("phase", 0.0))
        if not 0.0 <= amp <= G.CANDLE_BOB_AMP or not G.CANDLE_BOB_PERIOD[0] <= period <= G.CANDLE_BOB_PERIOD[1]:
            err(f"{where}: bob amp 0..{G.CANDLE_BOB_AMP} m, period {G.CANDLE_BOB_PERIOD[0]}..{G.CANDLE_BOB_PERIOD[1]} s")
            continue
        floor = max(s.floor_max for s in under)
        if ys[0] - amp - G.CANDLE_HEIGHT < floor + G.OVERHEAD_CLEARANCE - 1e-6:
            err(f"{where}: the lowest candle must stay {G.OVERHEAD_CLEARANCE} m over the floor below "
                f"(y0 at least {G.fmt_m(floor + G.OVERHEAD_CLEARANCE + amp + G.CANDLE_HEIGHT)})")
            continue
        roof = cp.sky_plate if cp.sky_plate is not None else min(s.ceil_y for s in under if s.roofed)
        if ys[1] + amp + G.CANDLE_HEIGHT > roof - 1.0 + 1e-6:
            err(f"{where}: candles must stay 1 m under the roof ({G.fmt_m(roof)} m)")
            continue
        if not isinstance(count, int) or isinstance(count, bool) or not G.CANDLE_COUNT[0] <= count <= G.CANDLE_COUNT[1]:
            err(f"{where}: count must be {G.CANDLE_COUNT[0]}..{G.CANDLE_COUNT[1]}")
            continue
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            err(f"{where}: seed must be a whole number >= 0")
            continue
        if not _num(lod1) or not G.CANDLE_LOD[0] <= lod1 <= G.CANDLE_LOD[1]:
            err(f"{where}: lod1 must be {G.CANDLE_LOD[0]}..{G.CANDLE_LOD[1]} m")
            continue
        clash = next((o for o in cp.candles if o.rect[0] < x1 and x0 < o.rect[2] and o.rect[1] < z1 and z0 < o.rect[3]), None)
        if clash is not None:
            err(f"{where}: overlaps {clash.id}")
            continue
        cg = CandleGroup(g["id"], (x0, z0, x1, z1), float(ys[0]), float(ys[1]), count, seed, amp, period, phase,
                         float(lod1))
        cp.candles.append(cg)
        cp.groups[cg.id] = G.GroupSpec(cg.id, cg.lod1,
                                       (("amp", amp), ("kind", "bob"), ("period", period), ("phase", phase)))


def _resolve_chandeliers(cid, surfaces, cp, rep) -> None:
    """Castle M5b: a ring chandelier (grid.CHANDELIER) hero hangs
    in a moving candle group of its own: args.group k (0..CANDLE_GROUPS_MAX - 1,
    not a `candles` entry's nor another chandelier's) makes the node
    `<cell>.castle_props.candles_<k>`, bobbing by args.amp (0..CANDLE_BOB_AMP,
    default CHANDELIER_BOB_AMP), args.period (CANDLE_BOB_PERIOD, default
    CHANDELIER_BOB_PERIOD) and args.phase; no LOD1. Its lowest point
    (CHANDELIER_BELOW under pos) at the bottom of its bob stays
    OVERHEAD_CLEARANCE over the floor below, and its chain (args.chain metres
    up from pos) ends at or under the roof there."""
    err = rep.errors.append
    used = {cg.id for cg in cp.candles}
    flat = _flat(surfaces)
    for h in cp.hero:
        if h.piece != G.CHANDELIER:
            continue
        where = f"{cid}: hero {h.id}"
        args = dict(h.args)
        k = args.get("group")
        if k is None or k != int(k) or not 0 <= k < G.CANDLE_GROUPS_MAX:
            err(f"{where}: a ring chandelier needs args.group 0..{G.CANDLE_GROUPS_MAX - 1} (its candle group)")
            continue
        gid = f"candles_{int(k)}"
        if gid in used:
            err(f"{where}: candle group {gid} is taken")
            continue
        amp = args.get("amp", G.CHANDELIER_BOB_AMP)
        period = args.get("period", G.CHANDELIER_BOB_PERIOD)
        phase = args.get("phase", 0.0)
        if not 0.0 <= amp <= G.CANDLE_BOB_AMP or not G.CANDLE_BOB_PERIOD[0] <= period <= G.CANDLE_BOB_PERIOD[1]:
            err(f"{where}: bob amp 0..{G.CANDLE_BOB_AMP} m, period {G.CANDLE_BOB_PERIOD[0]}..{G.CANDLE_BOB_PERIOD[1]} s")
            continue
        x, y, z = h.pos
        under = [s for s in flat if s.x0 - 1e-6 <= x <= s.x1 + 1e-6 and s.z0 - 1e-6 <= z <= s.z1 + 1e-6]
        if not under:
            err(f"{where}: must hang over the cell's floor")
            continue
        floor = max(s.floor_max for s in under)
        if y - G.CHANDELIER_BELOW - amp < floor + G.OVERHEAD_CLEARANCE - 1e-6:
            err(f"{where}: its lowest point must stay {G.OVERHEAD_CLEARANCE} m over the floor "
                f"(pos y at least {G.fmt_m(floor + G.OVERHEAD_CLEARANCE + G.CHANDELIER_BELOW + amp)})")
            continue
        roofs = [s.ceil_y for s in under if s.roofed]
        if roofs and y + args.get("chain", 0.0) > min(roofs) + 1e-6:
            err(f"{where}: its chain must end at or under the roof ({G.fmt_m(min(roofs))} m)")
            continue
        if cid in G.INSTANCE_RULES:
            continue  # An instance room has no moving nodes, so its chandelier hangs still
        used.add(gid)
        cp.hero_groups[h.id] = gid
        cp.groups[gid] = G.GroupSpec(gid, None, (("amp", float(amp)), ("kind", "bob"), ("period", float(period)),
                                                 ("phase", float(phase))))


def _resolve_house_cup_p2b(cid, cp, rep) -> None:
    """The hourglass set's sand groups
    (`hourglass_sand_house_<x>`, kit_anim kind `fill`: the piece tags its
    sand faces) and each `kit_banner_cup_<x>` hero's group
    (`cup_banners_house_<x>`, kind `cup_banners`; one hero per house)."""
    err = rep.errors.append
    sets = [h for h in cp.hero if h.piece == "kit_hourglass_set"]
    if len(sets) > 1:
        err(f"{cid}: at most one kit_hourglass_set (its sand groups are per house)")
    for h in sets[:1]:
        n = int(round(dict(h.args).get("count", 4)))
        for slot in G.HOUSE_SLOTS[:max(0, min(n, len(G.HOUSE_SLOTS)))]:
            gid = G.SAND_GROUP.format(slot)
            cp.groups[gid] = G.GroupSpec(gid, None, G.house_anim(G.ANIM_FILL, slot))
    for h in cp.hero:
        if not h.piece.startswith(G.CUP_BANNER + "_"):
            continue
        slot = h.piece[len(G.CUP_BANNER) + 1:]
        gid = G.CUP_BANNER_GROUP.format(slot)
        if slot not in G.HOUSE_SLOTS:
            err(f"{cid}: hero {h.id}: {h.piece} is not a house's cup banners")
            continue
        if gid in cp.groups:
            err(f"{cid}: hero {h.id}: {gid} is taken (one {G.CUP_BANNER}_{slot} a cell)")
            continue
        cp.hero_groups[h.id] = gid
        cp.groups[gid] = G.GroupSpec(gid, None, G.house_anim(G.ANIM_CUP_BANNERS, slot))


def _resolve_portraits(cid, spec, layout, runs, cp, rep, pieces) -> None:
    """castle_kit.json `portraits` (M4): wall rectangles the cluster
    generator fills with framed portraits; each goes in its level's band."""
    err = rep.errors.append
    clusters = spec.get("portraits", [])
    if not isinstance(clusters, list):
        err(f"{cid}: portraits must be a list")
        return
    if clusters and "castle_portraits" not in cp.materials:
        err(f"{cid}: portraits need materials.castle_portraits (castle_portraits_a or _b)")
        return
    ids = set()
    for i, pc in enumerate(clusters):
        where = f"{cid}: portraits[{i}]"
        if not isinstance(pc, dict) or set(pc) - PORTRAIT_KEYS or not {"id", "run", "t", "seed"} <= set(pc):
            err(f"{where}: must be {{\"id\", \"run\", \"t\": [t0, t1], \"y\"?: [y0, y1], \"seed\", \"mix\"?}}")
            continue
        pid, rid, ts, seed = pc["id"], pc["run"], pc["t"], pc["seed"]
        ys, mix = pc.get("y", list(G.PORTRAIT_Y)), pc.get("mix", "gallery")
        if not isinstance(pid, str) or not L.CELL_ID_RE.match(pid) or pid in ids:
            err(f"{where}: id must be a unique [a-z0-9_]+ string")
            continue
        ids.add(pid)
        r = runs.get(rid)
        if r is None:
            err(f"{where}: unknown run {rid!r}")
            continue
        if (not isinstance(ts, list) or len(ts) != 2 or not all(_num(t) and _multiple(t, PORTRAIT_GRID) for t in ts)
                or ts[1] - ts[0] < 1.0):
            err(f"{where}: t must be [t0, t1] on the {PORTRAIT_GRID} m grid, at least 1 m long")
            continue
        if (not isinstance(ys, list) or len(ys) != 2 or not all(_num(v) for v in ys) or ys[1] - ys[0] < 1.0
                or ys[0] < 0.5 or ys[1] > G.PORTRAIT_Y[1] + 1e-9):
            err(f"{where}: y must be [y0, y1] above the floor, within 0.5..{G.PORTRAIT_Y[1]} m and at least 1 m tall")
            continue
        if mix not in G.PORTRAIT_MIXES:
            err(f"{where}: mix must be one of {sorted(G.PORTRAIT_MIXES)}")
            continue
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            err(f"{where}: seed must be a whole number >= 0")
            continue
        t0, t1 = float(ts[0]), float(ts[1])
        y0, y1 = r.floor + float(ys[0]), r.floor + float(ys[1])
        if not G.face_walled(pieces, r.axis, r.c, r.sign, t0, t1, y0, y1):
            err(f"{where}: no wall over t {G.fmt_m(t0)}..{G.fmt_m(t1)}, y {G.fmt_m(y0)}..{G.fmt_m(y1)} of {rid}'s face")
            continue
        hit = _opening_near(cp, r.axis, r.c, r.sign, t0, t1, y0, y1, G.PORTRAIT_KEEP_OUT)
        if hit is not None:
            err(f"{where}: reaches {hit.id} (keep {G.PORTRAIT_KEEP_OUT} m from openings, arch quoins and hoods)")
            continue
        clash = next((o for o in cp.portraits if o.axis == r.axis and abs(o.c - r.c) < 1e-6 and o.sign == r.sign
                      and o.t0 < t1 and t0 < o.t1 and o.y0 < y1 and y0 < o.y1), None)
        if clash is not None:
            err(f"{where}: overlaps {clash.id}")
            continue
        level = layout.by_id(r.surface).level
        band = f"l{level}"
        if not G.GROUP_RE.match(band):
            err(f"{where}: {r.surface} is on level {level}; bands are l0..l9")
            continue
        cp.portraits.append(PortraitCluster(pid, rid, r.axis, r.c, r.sign, t0, t1, y0, y1, seed, mix, band,
                                            cp.materials["castle_portraits"]))
        cp.groups.setdefault(band, G.GroupSpec(band, G.LOD_STOREY))


def _resolve_shade(cid, spec, cp, rep) -> None:
    """castle_kit.json `shade` (castle D1, optional): `{"radius"?: m, "rays"?: n}`
    overrides the bake's numbers of the cell's budget kind (grid.AO_RADIUS_RANGE,
    grid.AO_RAYS_RANGE)."""
    err = rep.errors.append
    radius, rays = G.ao_radius(cp.budget), G.ao_rays(cp.budget)
    sh = spec.get("shade", {})
    if not isinstance(sh, dict):
        err(f"{cid}: shade must be an object {{\"radius\"?, \"rays\"?}}")
        sh = {}
    for k in sorted(set(sh) - SHADE_KEYS):
        err(f"{cid}: shade: unknown field {k!r}")
    if "radius" in sh:
        lo, hi = G.AO_RADIUS_RANGE
        if not _num(sh["radius"]) or not lo <= sh["radius"] <= hi:
            err(f"{cid}: shade.radius must be {lo}..{hi} m")
        else:
            radius = float(sh["radius"])
    if "rays" in sh:
        lo, hi = G.AO_RAYS_RANGE
        n = sh["rays"]
        if not isinstance(n, int) or isinstance(n, bool) or not lo <= n <= hi:
            err(f"{cid}: shade.rays must be a whole number {lo}..{hi}")
        else:
            rays = n
    cp.shade = Shade(radius, rays)


def _resolve_facade(cid, spec, cp, rep) -> None:
    """castle_kit.json `facade` (castle M5c, optional): `{"sides": [...], "ext"?,
    "roof"?, "parapet"?, "buttresses"?, "pinnacles"?}` on a facing cell
    (grid.M5C_FACADE_CELLS), its sides among that cell's; the flags are booleans
    (`ext` default true, the others false). An outdoor cell has no roof slab."""
    if "facade" not in spec:
        return
    err = rep.errors.append
    fa = spec["facade"]
    if not isinstance(fa, dict):
        err(f"{cid}: facade must be an object")
        return
    for k in sorted(set(fa) - FACADE_KEYS):
        err(f"{cid}: facade: unknown field {k!r}")
    allowed = G.M5C_FACADE_CELLS.get(cid)
    if allowed is None:
        err(f"{cid}: facade: not a facing cell (grid.M5C_FACADE_CELLS)")
        return
    sides = fa.get("sides")
    if not isinstance(sides, list) or not sides or len(set(sides)) != len(sides) \
            or any(x not in allowed for x in sides):
        err(f"{cid}: facade.sides must list sides among {list(allowed)}")
        return
    flags = {}
    for k, default in (("ext", True), ("roof", False), ("parapet", False), ("buttresses", False), ("pinnacles", False)):
        v = fa.get(k, default)
        if not isinstance(v, bool):
            err(f"{cid}: facade.{k} must be true or false")
            v = default
        flags[k] = v
    if flags["roof"] and cp.budget == "courtyard":
        err(f"{cid}: facade.roof: an outdoor cell has no roof slab")
    cp.facade = Facade(tuple(sides), **flags)


def _resolve_keep_clear(cid, spec, cp, rep, areas) -> None:
    """castle_kit.json `keep_clear` (M3): layout areas (the Dueling Club's)
    no hero stands in below the overhead height."""
    err = rep.errors.append
    keep = spec.get("keep_clear", [])
    if not isinstance(keep, list) or not all(isinstance(a, str) for a in keep):
        err(f"{cid}: keep_clear must list layout area ids")
        return
    if not keep:
        return
    known = {a.get("id"): a for a in (areas if areas is not None else layout_areas())}
    for aid in keep:
        a = known.get(aid)
        if a is None or a.get("kind") not in G.KEEP_CLEAR_AREAS:
            err(f"{cid}: keep_clear: {aid!r} is not a layout area of kind {G.KEEP_CLEAR_AREAS}")
            continue
        lo, hi = tuple(float(v) for v in a["min"]), tuple(float(v) for v in a["max"])
        cp.keep_clear.append((aid, lo, hi))
        for h in cp.hero:
            x, y, z = h.pos
            if lo[0] < x < hi[0] and lo[2] < z < hi[2] and y < lo[1] + 0.5 + G.INTRUSION_HEIGHT:
                err(f"{cid}: hero {h.id} stands in area {aid} (keep it clear below {G.INTRUSION_HEIGHT} m)")


def _resolve_hero(cid, spec, cp, rep) -> None:
    err = rep.errors.append
    hero = spec.get("hero", [])
    if not isinstance(hero, list):
        err(f"{cid}: hero must be a list")
        return
    ids = set()
    for i, h in enumerate(hero):
        where = f"{cid}: hero[{i}]"
        if not isinstance(h, dict):
            err(f"{where}: must be an object")
            continue
        for k in sorted(set(h) - HERO_KEYS):
            err(f"{where}: unknown field {k!r}")
        hid, piece, pos, yaw = h.get("id"), h.get("piece"), h.get("pos"), h.get("yaw", 0.0)
        args = h.get("args", {})
        if not isinstance(hid, str) or not L.CELL_ID_RE.match(hid) or hid in ids:
            err(f"{where}: id must be a unique [a-z0-9_]+ string")
            continue
        ids.add(hid)
        if not isinstance(piece, str) or not G.PIECE_RE.match(piece):
            err(f"{where}: piece {piece!r} breaks the naming rule kit_<family>_<piece>[_<w>x<h>][_<variant>]")
            continue
        if not isinstance(pos, list) or len(pos) != 3 or not all(_num(v) for v in pos) or not _num(yaw):
            err(f"{where}: pos must be [x, y, z] and yaw a number")
            continue
        if not isinstance(args, dict) or not all(_num(v) for v in args.values()):
            err(f"{where}: args must map names to numbers")
            continue
        cp.hero.append(Hero(hid, piece, tuple(float(v) for v in pos), float(yaw),
                            tuple(sorted((k, float(v)) for k, v in args.items()))))


def _check_props(cid, spec, layout, runs, props, kit_entries, cp, rep) -> None:
    """Replaced props exist in the cell; openings do not cut into props or
    kit entries that stay."""
    err = rep.errors.append
    replaces = spec.get("replaces", [])
    if not isinstance(replaces, list) or not all(isinstance(r, str) for r in replaces):
        err(f"{cid}: replaces must list prop or kit ids")
        return
    own = {p.id: p for p in props if p.cell == cid}
    own_kit = {k.get("id"): k for k in kit_entries if k.get("cell") == cid}
    # A prop with `repeat` (copies `<id>_<n>`) is replaced by its own id: all its copies (castle kit M4).
    rows: dict[str, list[str]] = {}
    for pid in own:
        base, _, n = pid.rpartition("_")
        if base and n.isdigit():
            rows.setdefault(base, []).append(pid)
    expanded = set()
    for r in replaces:
        if r in own or r in own_kit:
            expanded.add(r)
        elif r in rows:
            expanded.update(rows[r])
        else:
            err(f"{cid}: replaces: {r!r} is not a prop or kit entry of this cell")
    cp.replaces = sorted(expanded)
    keep = [p for p in own.values() if p.id not in cp.replaces]
    keep_kit = [k for kid, k in own_kit.items() if kid not in cp.replaces]
    for op in cp.openings:
        if op.kind == "arch":
            continue
        depth_in = 0.5  # metres in front of the face that an opening's surround and reveal use
        for p in keep:
            lo_t, hi_t = (p.min[0], p.max[0]) if op.axis == "z" else (p.min[2], p.max[2])
            lo_d, hi_d = (p.min[2], p.max[2]) if op.axis == "z" else (p.min[0], p.max[0])
            # distance into the room from the face, signed
            d0, d1 = sorted(((lo_d - op.c) * op.sign, (hi_d - op.c) * op.sign))
            if lo_t < op.t1 and op.t0 < hi_t and p.min[1] < op.y1 and op.y0 < p.max[1] and d0 < depth_in and d1 > -1.0:
                err(f"{cid}: {op.id} cuts into prop {p.id} (replace it or move the opening)")
        for k in keep_kit:
            pos = k.get("pos")
            if not (isinstance(pos, list) and len(pos) == 3 and all(_num(v) for v in pos)):
                continue
            t = pos[0] if op.axis == "z" else pos[2]
            d = ((pos[2] if op.axis == "z" else pos[0]) - op.c) * op.sign
            half = G.TELL_HALF_WIDTH
            if op.t0 - half < t < op.t1 + half and -1.0 < d < depth_in and op.y0 - 1.0 < pos[1] < op.y1:
                err(f"{cid}: {op.id} covers kit entry {k.get('id')} at t {t:.2f}")


def load(path: str = KIT_PATH, layout_path: str = LAYOUT_PATH, props_path: str = PROPS_PATH) -> KitPlan:
    """The resolved castle kit, or KitDataError listing every problem."""
    layout = L.load_layout(layout_path)
    plan, rep = resolve(load_json(path), layout, props_path)
    if not rep.ok:
        raise KitDataError("castle_kit.json:\n  " + "\n  ".join(rep.errors))
    return plan


def kitted_cells(path: str = KIT_PATH) -> list[str]:
    """Cell ids the kit dresses (an absent file: none); cells with
    `"build": false` are planned, not dressed yet."""
    if not os.path.exists(path):
        return []
    cells = load_json(path).get("cells", {})
    return sorted(c for c, spec in cells.items() if not (isinstance(spec, dict) and spec.get("build", True) is False))


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Validate and print data/world/castle_kit.json")
    ap.add_argument("--kit", default=KIT_PATH)
    ap.add_argument("--layout", default=LAYOUT_PATH)
    ap.add_argument("--props", default=PROPS_PATH)
    args = ap.parse_args(argv)
    plan, rep = resolve(load_json(args.kit), L.load_layout(args.layout), args.props)
    for w in rep.warnings:
        print(f"warning: {w}")
    for e in rep.errors:
        print(f"error: {e}")
    for cid, cp in sorted({**plan.cells, **plan.planned}.items()):
        state = "" if cp.build else " (planned: build false)"
        print(f"{cid}{state}: budget {cp.budget}, materials {cp.materials}, camera max {plan.camera_max(cid)} m"
              + (f", springing {G.fmt_m(cp.springing)} m" if cp.springing is not None else "") + f", shafts {cp.shaft}")
        for r in cp.runs:
            b = cp.bays[r.id]
            print(f"  run {r.id:28s} t {r.t0:7.2f}..{r.t1:7.2f} ({r.length:5.2f} m) {b.n:2d} bays of {b.width:.3f} m")
        for o in cp.openings:
            extra = ""
            if o.kind == "arch":
                extra = f" reveal {G.fmt_m(o.reveal)}" + (f" leaves {o.leaf_mode} {G.fmt_m(o.leaf_width)}" if o.leaves else "")
            print(f"  {o.kind:10s} {o.piece:26s} {o.id:44s} t {o.t0:7.2f}..{o.t1:7.2f} y {o.y0:5.2f}..{o.y1:5.2f}{extra}")
        for sh in cp.shafts:
            print(f"  shaft      {sh.piece:26s} {sh.id:44s} t {sh.t:7.2f} y {sh.y0:5.2f}..{sh.y1:5.2f}")
        for h in cp.hero:
            print(f"  hero       {h.piece:26s} {h.id:44s} pos {list(h.pos)} yaw {h.yaw:.4f} {dict(h.args)}")
        for ls in cp.line_shafts:
            print(f"  line shaft {ls.piece:26s} {ls.id:44s} t {ls.t:7.2f} y {ls.y0:5.2f}..{ls.y1:5.2f} + {ls.corbel}")
        for cg in cp.candles:
            print(f"  candles    {cg.id:26s} rect {list(cg.rect)} y {cg.y0:g}..{cg.y1:g} x{cg.count} bob {cg.amp:g} m / "
                  f"{cg.period:g} s, LOD1 beyond {cg.lod1:g} m")
        for pc in cp.portraits:
            print(f"  portraits  {pc.material:26s} {pc.id:44s} {pc.run} t {pc.t0:g}..{pc.t1:g} y {pc.y0:g}..{pc.y1:g} "
                  f"band {pc.band}")
        if cp.sky:
            print(f"  sky        {len(cp.sky)} panels, plate {G.fmt_m(cp.sky_plate)} m, ridge {G.fmt_m(cp.sky_ridge)} m")
        extra = []
        if cp.wainscot:
            extra.append(f"wainscot {G.fmt_m(cp.wainscot)} m")
        if cp.railings != "balustrade":
            extra.append(f"railings {cp.railings}")
        if cp.keep_clear:
            extra.append(f"keep clear {[a for a, _, _ in cp.keep_clear]}")
        if cp.groups:
            extra.append(f"groups {sorted(cp.groups)}")
        if cp.shade is not None:
            extra.append(f"shade R {G.fmt_m(cp.shade.radius)} m x{cp.shade.rays}")
        print(f"  {len(cp.shafts)} shafts, {len(cp.line_shafts)} line shafts, {len(cp.lanterns)} lanterns, "
              f"{len(cp.hero)} hero, replaces {cp.replaces}" + (", " + ", ".join(extra) if extra else ""))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
