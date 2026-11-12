"""Pure-Python model of `data/world/layout.json` for the Blender tools.

No `bpy` here: this module loads and validates the layout, walks its
connectivity graph with the same rules as `sim-core`, and plans the greybox
geometry as plain polygons. `greybox.py` turns the plan into Blender meshes and
glTF files; `check_layout.py` and the unit tests use it directly.

Coordinates follow docs/architecture.md: metres, right-handed, Y up, x = east,
z = south. Every polygon this module produces is a list of (x, y, z) tuples in
those coordinates, wound counter-clockwise when seen from its front side.

Geometry model (see tools/blender/README.md for the full rules):

* Each surface contributes "air": the open space above its floor, up to its
  ceiling (roofed) or unbounded (open to the sky).
* Walls are computed per straight line in the plan. Along a line, each side's
  surfaces want enclosure up to their wall top; a face is emitted wherever one
  side wants a wall and the other side has no air. Doors and stairs touching a
  room's edge therefore cut openings automatically, and a header is left above
  any opening lower than the room's ceiling.
* Railings appear where a floor ends at a drop (the other side has a lower
  floor but nothing within step height).

The layout also has house gates (`Surface.house`), interactables and placeable
areas, with the same validation rules as `sim_core::layout::Layout::validate`
plus the checks only a content tool can afford (per-house reachability, thin
walls the server's path sampling could miss).

Grounds surfaces carry heightfields (`Surface.heights`, `sim_core::layout::Heights`):
a grounds surface's ground blends bilinearly between samples every 2 m
(`tools/blender/terrain_layout.py` writes them). The greybox draws such a
floor as one quad per lattice square and gives it no boundary wall (the
terrain export dresses the grounds' edges).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import deque
from dataclasses import dataclass, field

# The castle palette lives with the world art (tools/blender/world/palette.py).
_WORLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world")
if _WORLD not in sys.path:
    sys.path.append(_WORLD)
import palette  # noqa: E402

# Mirrors sim_core::movement::MoveParams::default(). Keep in sync.
STEP_HEIGHT = 0.35
CHARACTER_RADIUS = 0.3
# sim_core::movement::PATH_SAMPLE_SPACING: the server tests a move every 0.1 m,
# so a gap between two walkable surfaces must be wider than this to block.
PATH_SAMPLE_SPACING = 0.1
# sim_core::layout::INTERACT_HEIGHT_TOLERANCE and the spawn tolerance.
INTERACT_HEIGHT_TOLERANCE = 1.5
SPAWN_TOLERANCE = 0.5

# House ids are data (CONTRIBUTING.md): the keys of the palette's house table,
# which are protocol::House::id() spelled the same way.
PALETTE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "items", "palette.json")


def known_houses(path: str = PALETTE_PATH) -> list[str]:
    """Lower-case house ids in slot order (`house_a`..`house_d`): the palette's
    `houses` order, which is `protocol::House::ALL` order (Roarwing, Burrowsby,
    Skytalon, Serpentyne). Not sorted: the ids are not in slot order
    alphabetically."""
    with open(path, encoding="utf-8") as f:
        return list(json.load(f)["houses"])

EPS = 1e-4
INF = math.inf

# Structural thickness between a ceiling and the floor or roof above it. The
# layout uses a 9 m floor-to-floor rhythm with 8.4 m corridor ceilings (the
# castle kit's scale pass, M1.0; it was 6 m and 5.4 m before).
SLAB = 0.6
# Thickness of floors that are visible from below (landings over a stairwell).
UNDER_SLAB = 0.4
# Distance under which two parallel walls are treated as one solid wall mass.
WALL_MASS_MAX = 3.2
# Exterior wall thickness by owner kind (metres).
EXTERIOR_THICKNESS = {"courtyard": 1.0, "grounds": 0.5}
EXTERIOR_THICKNESS_DEFAULT = 0.8
# Height of walls around open-air surfaces (a courtyard's facades, the low
# boundary wall of the grounds). Kinds not listed have no walls when open.
OPEN_WALL_HEIGHT = {"courtyard": 16.0, "grounds": 1.0, "bridge": 1.1, "stair": 1.0}
# Exterior walls of floating upper floors are extended down to this height so
# the castle massing reads as solid from outside.
GROUND_Y = -1.0

RAILING_HEIGHT = 1.0
RAILING_THICKNESS = 0.15
RAILING_INSET = 0.02
# Target riser height of visual steps; the walkable ramp is unchanged.
STEP_RISER = 0.16
STRINGER_DEPTH = 0.3
# Head clearance required between overlapping surfaces.
HEADROOM = 2.0
# What the generator draws under a surface above someone (a landing's slab, a
# floating flight's treads and stringer) must clear the tallest character
# with a hat (1.92 + 0.35 m) and a little air.
OVERHEAD_CLEARANCE = 2.4

KINDS = ("room", "corridor", "door", "stair", "courtyard", "grounds", "bridge")
RISES = ("+x", "-x", "+z", "-z")
CELL_ID_RE = re.compile(r"^[a-z0-9_]+$")

# Greybox materials: name -> (base colour RGB, roughness). Distinct per role so
# the renders read clearly; replaced by real kit materials.
# Greybox castle materials by role: (linear base colour, roughness). The
# colours come from the castle palette (world/palette.py, sRGB, documented
# in docs/art/style-guide.md "Castle palette"); `VALUE_RAMPS` there darkens
# the base of walls through the cells' vertex colours.
_P = palette.CASTLE
_G = palette.GLOW
MATERIALS = {
    "floor_room": (palette.linear(_P["flagstone"]), 0.9),
    "floor_corridor": (palette.linear(_P["flagstone_corridor"]), 0.9),
    "floor_door": (palette.linear(_P["threshold_wood"]), 0.85),
    "floor_stair": (palette.linear(_P["marble"]), 0.6),
    "floor_courtyard": (palette.linear(_P["flagstone_courtyard"]), 0.9),
    "floor_grounds": (palette.linear(_P["grass"]), 0.95),
    "floor_bridge": (palette.linear(_P["flagstone_bridge"]), 0.9),
    "wall": (palette.linear(_P["stone"]), 0.9),
    "wall_dungeon": (palette.linear(_P["stone_dungeon"]), 0.95),
    "floor_dungeon": (palette.linear(_P["flagstone_dungeon"]), 0.95),
    "wall_exterior": (palette.linear(_P["stone_exterior"]), 0.9),
    "wall_low": (palette.linear(_P["stone_low"]), 0.95),
    "ceiling": (palette.linear(_P["ceiling"]), 0.9),
    "roof": (palette.linear(_P["roof"]), 0.8),
    "railing": (palette.linear(_P["oak_rail"]), 0.75),
    "trim": (palette.linear(_P["stone_trim"]), 0.9),
    "prop_wood": (palette.linear(_P["oak"]), 0.75),
    "prop_stone": (palette.linear(_P["stone_trim"]), 0.9),
    "prop_gold": (palette.linear(_P["gold"]), 0.4),
    "prop_glass": (palette.linear(_P["glass"]), 0.2),
    "water": (palette.linear(_P["water"]), 0.15),
    # Common-room dressing. House accents appear only on fabric
    # (banners, upholstery, rugs), never on architecture (style guide).
    "prop_stone_dark": (palette.linear(_P["stone_dark"]), 0.9),
    "prop_marble": (palette.linear(_P["marble"]), 0.4),
    "prop_bronze": (palette.linear(_P["bronze"]), 0.4),
    "prop_copper": (palette.linear(_P["copper"]), 0.4),
    "prop_silver": (palette.linear(_P["silver"]), 0.35),
    "prop_iron": (palette.linear(_P["iron"]), 0.6),
    "prop_leather": (palette.linear(_P["leather"]), 0.65),
    "prop_canvas": (palette.linear(_P["canvas"]), 0.9),
    "prop_plant": (palette.linear(_P["plant"]), 0.9),
    "prop_parchment": (palette.linear(_P["parchment"]), 0.9),
    "fabric_house_a": (palette.linear(_P["house_a"]), 0.85),
    "fabric_house_b": (palette.linear(_P["house_b"]), 0.85),
    "fabric_house_c": (palette.linear(_P["house_c"]), 0.85),
    "fabric_house_d": (palette.linear(_P["house_d"]), 0.85),
    # Emissive (see EMISSIVE): fire, candles and windows that light a room.
    "glow_fire": (palette.linear(_G["fire"]), 0.9),
    "glow_candle": (palette.linear(_G["candle"]), 0.9),
    "glow_lake": (palette.linear(_G["lake"]), 0.2),
    "glow_green": (palette.linear(_G["green"]), 0.5),
    "glow_sky": (palette.linear(_G["sky"]), 0.2),
    "glow_sun": (palette.linear(_G["sun"]), 0.2),
}

# Emission strength of the emissive materials above (their colour glows).
EMISSIVE = {
    "glow_fire": 4.0,
    "glow_candle": 3.0,
    "glow_lake": 1.6,
    "glow_green": 3.0,
    "glow_sky": 1.4,
    "glow_sun": 1.8,
}

# Prop shapes: a box, or a cylinder inscribed in the box along one axis
# (barrels lying down, columns, round tables).
PROP_SHAPES = ("box", "cylinder_x", "cylinder_y", "cylinder_z")
CYLINDER_SEGMENTS = 12


class LayoutError(Exception):
    """The layout file is malformed or violates a rule the game relies on."""


# ---------------------------------------------------------------------------
# Layout model


@dataclass(frozen=True)
class Heights:
    """`sim_core::layout::Heights`: `cols` x `rows` samples every `step`
    metres from `origin` [x, z], row-major (rows run +z, each row +x)."""

    origin: tuple[float, float]
    step: float
    cols: int
    rows: int
    h: tuple[float, ...]

    def height(self, x: float, z: float) -> float:
        """Bilinear between the four samples around (x, z), clamped to the grid
        (the same arithmetic as sim-core's `Heights::height_at`)."""
        fx = min(max((x - self.origin[0]) / self.step, 0.0), self.cols - 1.0)
        fz = min(max((z - self.origin[1]) / self.step, 0.0), self.rows - 1.0)
        i, j = min(int(fx), self.cols - 2), min(int(fz), self.rows - 2)
        tx, tz = fx - i, fz - j
        r = j * self.cols + i
        a = self.h[r] + (self.h[r + 1] - self.h[r]) * tx
        b = self.h[r + self.cols] + (self.h[r + self.cols + 1] - self.h[r + self.cols]) * tx
        return a + (b - a) * tz

    def max_slope(self) -> float:
        c, worst = self.cols, 0.0
        for j in range(self.rows):
            for i in range(c):
                v = self.h[j * c + i]
                if i + 1 < c:
                    worst = max(worst, abs(self.h[j * c + i + 1] - v))
                if j + 1 < self.rows:
                    worst = max(worst, abs(self.h[(j + 1) * c + i] - v))
        return worst / self.step

    def end(self) -> tuple[float, float]:
        return (self.origin[0] + self.step * (self.cols - 1), self.origin[1] + self.step * (self.rows - 1))


# sim_core::layout::MAX_GROUND_SLOPE
MAX_GROUND_SLOPE = 0.58


@dataclass(frozen=True)
class Surface:
    id: str
    kind: str
    name: str
    level: int
    cell: str
    x0: float
    z0: float
    x1: float
    z1: float
    y: float
    y_top: float | None
    rise: str | None
    ceiling: float | None
    index: int
    # House gate (lower-case house id) or None for everyone.
    house: str | None = None
    # The grounds' heightfield (sim_core::layout::Heights), or None.
    heights: Heights | None = field(default=None, compare=False, repr=False)
    # A moving staircase flight's position, (flight id, "a" | "b"), or
    # None for fixed ground (`sim_core::layout::Moving`; data/world/stairs.json).
    moving: tuple[str, str] | None = None
    # Part of a hidden passage (`sim_core::layout::Surface::secret`;
    # data/world/secrets.json): walkable and drawn like any surface, its own
    # cell (`passage_*`); the trail and the map leave it out until found.
    secret: bool = False

    @property
    def sloped(self) -> bool:
        return self.y_top is not None and self.rise is not None

    @property
    def roofed(self) -> bool:
        return self.ceiling is not None

    @property
    def ceil_y(self) -> float:
        """Flat ceiling plane: `ceiling` metres above `y` (the low end for stairs)."""
        return self.y + self.ceiling if self.ceiling is not None else INF

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.z1 - self.z0)

    def height(self, x: float, z: float) -> float:
        """Ground height at (x, z), linear along the rise axis for slopes,
        bilinear between samples for heightfields."""
        if self.heights is not None:
            return self.heights.height(x, z)
        if not self.sloped:
            return self.y
        if self.rise in ("+x", "-x"):
            t = (x - self.x0) / (self.x1 - self.x0)
        else:
            t = (z - self.z0) / (self.z1 - self.z0)
        if self.rise in ("-x", "-z"):
            t = 1.0 - t
        t = min(1.0, max(0.0, t))
        return self.y + (self.y_top - self.y) * t

    @property
    def floor_min(self) -> float:
        if self.heights is not None:
            return min(self.heights.h)
        return min(self.y, self.y_top) if self.sloped else self.y

    @property
    def floor_max(self) -> float:
        if self.heights is not None:
            return max(self.heights.h)
        return max(self.y, self.y_top) if self.sloped else self.y

    def wall_height(self) -> float | None:
        """Wall top above the local floor for open surfaces; None if no walls
        (heightfields: the terrain export dresses the grounds' edges)."""
        if self.heights is not None:
            return None
        return OPEN_WALL_HEIGHT.get(self.kind)

    def struct_top(self) -> float:
        """Highest point of this surface's walls including the roof slab."""
        if self.roofed:
            return self.ceil_y + SLAB
        w = self.wall_height() or 0.0
        return self.floor_max + w

    def contains(self, x: float, z: float, eps: float = EPS) -> bool:
        return self.x0 - eps <= x <= self.x1 + eps and self.z0 - eps <= z <= self.z1 + eps

    def extent(self, axis: str) -> tuple[float, float]:
        return (self.x0, self.x1) if axis == "x" else (self.z0, self.z1)


# Adds find spots, the crafting stations and the Owl Exchange counter
# (`sim_core::layout::InteractableKind`).
INTERACTABLE_KINDS = (
    "dorm_door",
    "dorm_exit",
    "find_spot",
    "craft_charms",
    "craft_transfiguration",
    "cauldron",
    "owl_exchange",
    # A classroom door (castle) and the way out of a class or arena.
    "class_door",
    "instance_exit",
    # A common room's notice board (the client opens the House Cup panel).
    "notice_board",
    # The Owl Loft's owl mail desk (the client opens the mail panel).
    "owl_mail",
    # The Wishing Room's door (secret: shown only once opened).
    "wishing_room_door",
    # The Owl Order Catalogue's stand in each house common room (a station, its house's only).
    "owl_order",
)
PLACE_SURFACES = ("floor", "wall", "table")


@dataclass(frozen=True)
class Interactable:
    """`sim_core::layout::Interactable`: a door used with `ClientMsg::Interact`."""

    id: str
    kind: str
    house: str | None
    pos: tuple[float, float, float]
    yaw: float
    radius: float
    name: str | None = None

    def in_reach(self, pos) -> bool:
        dx, dz = pos[0] - self.pos[0], pos[2] - self.pos[2]
        return dx * dx + dz * dz <= self.radius**2 and abs(pos[1] - self.pos[1]) <= INTERACT_HEIGHT_TOLERANCE


@dataclass(frozen=True)
class PlaceArea:
    """`sim_core::placement::PlaceArea`: where furniture may go in an instance."""

    id: str
    surface: str
    min: tuple[float, float, float]
    max: tuple[float, float, float]
    facing: float | None = None
    # Floor and table areas: `min` / `max` are in this frame, `(origin, yaw)`
    # (`AreaFrame`: an area turned to a diagonal wall). None: room axes.
    frame: tuple[tuple[float, float, float], float] | None = None

    @property
    def wall_on_x_plane(self) -> bool:
        return abs(self.max[0] - self.min[0]) <= 0.01


@dataclass
class Layout:
    version: int
    surfaces: list[Surface]
    spawns: list[dict]
    landmarks: list[dict]
    interactables: list[Interactable] = field(default_factory=list)
    place_areas: list[PlaceArea] = field(default_factory=list)
    # The `solid` areas' boxes, (min, max) as (x, y, z): ground strictly
    # inside one's x and z, at a height within its y span, is not ground
    # (`sim_core::World`, AreaKind::Solid).
    solids: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = field(default_factory=list)

    def solid_blocks(self, x: float, y: float, z: float) -> bool:
        return any(lo[0] < x < hi[0] and lo[2] < z < hi[2] and lo[1] <= y <= hi[1] for lo, hi in self.solids)

    def by_id(self, sid: str) -> Surface:
        for s in self.surfaces:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def cells(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self.surfaces:
            seen.setdefault(s.cell, None)
        return list(seen)

    def surfaces_at(self, x: float, z: float) -> list[Surface]:
        return [s for s in self.surfaces if s.contains(x, z)]

    def interactable(self, iid: str) -> Interactable | None:
        return next((i for i in self.interactables if i.id == iid), None)

    def gated(self) -> list[str]:
        """House ids that gate at least one surface, in file order."""
        seen: dict[str, None] = {}
        for s in self.surfaces:
            if s.house:
                seen.setdefault(s.house, None)
        return list(seen)


def for_house(layout: Layout, house: str) -> Layout:
    """The layout as characters of `house` walk it (`Layout::for_house`).

    Surfaces and interactables gated to other houses are dropped, so their
    doorways become walls. Surface `index` keeps the original file position.
    """
    ok = lambda gate: gate is None or gate == house  # noqa: E731
    return Layout(
        version=layout.version,
        surfaces=[s for s in layout.surfaces if ok(s.house)],
        spawns=layout.spawns,
        landmarks=layout.landmarks,
        interactables=[i for i in layout.interactables if ok(i.house)],
        place_areas=layout.place_areas,
        solids=layout.solids,
    )


def flights(layout: Layout) -> list[str]:
    """The moving staircase flights, in order of first appearance
    (data/world/stairs.json lists them in the same order: bit i of a state)."""
    seen: dict[str, None] = {}
    for s in layout.surfaces:
        if s.moving:
            seen.setdefault(s.moving[0], None)
    return list(seen)


def state_name(n_flights: int, state: int) -> str:
    """A staircase state as one letter per flight ("aba": the second in B)."""
    return "".join("b" if state >> i & 1 else "a" for i in range(n_flights))


def for_state(layout: Layout, state: int) -> Layout:
    """The layout with every moving flight in the position `state`
    gives it (bit i set: flight i at "b"; `sim_core::stairs::StairState`).
    Fixed surfaces are always kept; `index` keeps the file position."""
    order = {f: i for i, f in enumerate(flights(layout))}
    keep = lambda s: s.moving is None or (s.moving[1] == "b") == bool(state >> order[s.moving[0]] & 1)  # noqa: E731
    return Layout(
        version=layout.version,
        surfaces=[s for s in layout.surfaces if keep(s)],
        spawns=layout.spawns,
        landmarks=layout.landmarks,
        interactables=layout.interactables,
        place_areas=layout.place_areas,
        solids=layout.solids,
    )


def base_state(layout: Layout) -> Layout:
    """The layout with every flight at rest in position A (what the greybox and
    the kit draw as static geometry; the B flights are moving nodes)."""
    return for_state(layout, 0)


@dataclass(frozen=True)
class Prop:
    id: str
    cell: str
    material: str
    min: tuple[float, float, float]
    max: tuple[float, float, float]
    shape: str = "box"


@dataclass(frozen=True)
class Light:
    """A light the client places with a cell (fire, lamps, window glow).

    `intensity` is in candela-like units for a point light (Babylon's
    `PointLight.intensity` with physical falloff); `range` bounds its effect.
    """

    id: str
    cell: str
    pos: tuple[float, float, float]
    color: str
    intensity: float
    range: float
    # Animated fire (docs/architecture.md): the seed of the fire it flickers with (None: steady).
    flicker: float | None = None
    # A window onto water (common room D's lake windows): the phase (0..1) of its slow swell, as light
    # through moving water (client game/fire/flicker.ts `ripple`); None: steady.
    ripple: float | None = None

    def to_json(self) -> dict:
        out = {
            "id": self.id,
            "kind": "point",
            "pos": [round(v, 3) for v in self.pos],
            "color": self.color,
            "intensity": self.intensity,
            "range": self.range,
        }
        if self.flicker is not None:
            out["flicker"] = self.flicker
        if self.ripple is not None:
            out["ripple"] = self.ripple
        return out


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise LayoutError(f"{what} must be a number, got {v!r}")
    return float(v)


def _pair(v, what: str) -> tuple[float, float]:
    if not isinstance(v, list) or len(v) != 2:
        raise LayoutError(f"{what} must be [x, z]")
    return _num(v[0], what), _num(v[1], what)


# `house` (optional) gates a surface to one house; the generator builds it like any other.
SURFACE_FIELDS = {"id", "kind", "name", "level", "cell", "min", "max", "y", "y_top", "rise", "ceiling", "house", "heights",
                  "moving", "secret"}
MOVING_POSITIONS = ("a", "b")


def _parse_heights(raw, sid: str) -> Heights:
    if not isinstance(raw, dict) or set(raw) != {"origin", "step", "cols", "rows", "h"}:
        raise LayoutError(f"{sid}: heights must be {{origin, step, cols, rows, h}}")
    ox, oz = _pair(raw["origin"], f"{sid}.heights.origin")
    cols, rows = raw["cols"], raw["rows"]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 2 for v in (cols, rows)):
        raise LayoutError(f"{sid}: heights need cols and rows of at least 2")
    h = raw["h"]
    if not isinstance(h, list) or len(h) != cols * rows:
        raise LayoutError(f"{sid}: heights need cols * rows samples")
    step = _num(raw["step"], f"{sid}.heights.step")
    if step <= 0:
        raise LayoutError(f"{sid}: heights need a positive step")
    return Heights((ox, oz), step, cols, rows, tuple(_num(v, f"{sid}.heights.h") for v in h))


def parse_layout(data: dict) -> Layout:
    """Parse a layout dict with the same field rules as `sim_core::layout::Layout`."""
    if not isinstance(data, dict):
        raise LayoutError("layout must be a JSON object")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise LayoutError("version must be an integer")
    raw = data.get("surfaces")
    if not isinstance(raw, list):
        raise LayoutError("surfaces must be a list")
    surfaces = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise LayoutError(f"surface #{i} must be an object")
        unknown = set(r) - SURFACE_FIELDS
        if unknown:
            raise LayoutError(f"surface #{i} has unknown fields {sorted(unknown)}")
        for key in ("id", "kind", "name", "level", "cell", "min", "max", "y"):
            if key not in r:
                raise LayoutError(f"surface #{i} is missing {key!r}")
        sid = r["id"]
        if not isinstance(sid, str) or not sid:
            raise LayoutError(f"surface #{i} id must be a non-empty string")
        if r["kind"] not in KINDS:
            raise LayoutError(f"{sid}: unknown kind {r['kind']!r}")
        if not isinstance(r["level"], int) or isinstance(r["level"], bool):
            raise LayoutError(f"{sid}: level must be an integer")
        if not isinstance(r["name"], str) or not isinstance(r["cell"], str):
            raise LayoutError(f"{sid}: name and cell must be strings")
        rise = r.get("rise")
        if rise is not None and rise not in RISES:
            raise LayoutError(f"{sid}: rise must be one of {RISES}")
        (x0, z0), (x1, z1) = _pair(r["min"], f"{sid}.min"), _pair(r["max"], f"{sid}.max")
        y_top = r.get("y_top")
        ceiling = r.get("ceiling")
        house = r.get("house")
        if house is not None and not isinstance(house, str):
            raise LayoutError(f"{sid}: house must be a string")
        heights = _parse_heights(r["heights"], sid) if r.get("heights") is not None else None
        moving = r.get("moving")
        if moving is not None:
            if (not isinstance(moving, dict) or set(moving) != {"flight", "position"}
                    or not isinstance(moving["flight"], str) or not moving["flight"]
                    or moving["position"] not in MOVING_POSITIONS):
                raise LayoutError(f"{sid}: moving must be {{flight, position: a | b}}")
            if r["kind"] != "stair":
                raise LayoutError(f"{sid}: only stairs move")
            moving = (moving["flight"], moving["position"])
        secret = r.get("secret", False)
        if not isinstance(secret, bool):
            raise LayoutError(f"{sid}: secret must be true or false")
        if secret and (house is not None or moving is not None):
            raise LayoutError(f"{sid}: a secret is never gated to a house or a moving flight")
        surfaces.append(
            Surface(
                id=sid,
                kind=r["kind"],
                name=r["name"],
                level=r["level"],
                cell=r["cell"],
                x0=x0,
                z0=z0,
                x1=x1,
                z1=z1,
                y=_num(r["y"], f"{sid}.y"),
                y_top=None if y_top is None else _num(y_top, f"{sid}.y_top"),
                rise=rise,
                ceiling=None if ceiling is None else _num(ceiling, f"{sid}.ceiling"),
                index=i,
                house=house,
                heights=heights,
                moving=moving,
                secret=secret,
            )
        )
    spawns = data.get("spawns", [])
    landmarks = data.get("landmarks", [])
    for what, items in (("spawn", spawns), ("landmark", landmarks)):
        if not isinstance(items, list):
            raise LayoutError(f"{what}s must be a list")
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("id"), str):
                raise LayoutError(f"every {what} needs a string id")
            pos = it.get("pos")
            if not isinstance(pos, list) or len(pos) != 3:
                raise LayoutError(f"{what} {it['id']}: pos must be [x, y, z]")
            for c in pos:
                _num(c, f"{what} {it['id']}.pos")
    for sp in spawns:
        _num(sp.get("yaw"), f"spawn {sp['id']}.yaw")
    for lm in landmarks:
        if not isinstance(lm.get("name"), str):
            raise LayoutError(f"landmark {lm['id']}: name must be a string")
    return Layout(
        version=version,
        surfaces=surfaces,
        spawns=spawns,
        landmarks=landmarks,
        interactables=_parse_interactables(data.get("interactables", [])),
        place_areas=_parse_place_areas(data.get("place_areas", [])),
        solids=[(_triple(a.get("min"), f"area {a.get('id')}.min"), _triple(a.get("max"), f"area {a.get('id')}.max"))
                for a in data.get("areas", []) if isinstance(a, dict) and a.get("kind") == "solid"],
    )


def _triple(v, what: str) -> tuple[float, float, float]:
    if not isinstance(v, list) or len(v) != 3:
        raise LayoutError(f"{what} must be [x, y, z]")
    return tuple(_num(c, what) for c in v)


def _parse_interactables(raw) -> list[Interactable]:
    if not isinstance(raw, list):
        raise LayoutError("interactables must be a list")
    out = []
    for r in raw:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            raise LayoutError("every interactable needs a string id")
        iid = r["id"]
        if r.get("kind") not in INTERACTABLE_KINDS:
            raise LayoutError(f"interactable {iid}: kind must be one of {INTERACTABLE_KINDS}")
        house = r.get("house")
        if house is not None and not isinstance(house, str):
            raise LayoutError(f"interactable {iid}: house must be a string")
        name = r.get("name")
        if name is not None and not isinstance(name, str):
            raise LayoutError(f"interactable {iid}: name must be a string")
        out.append(
            Interactable(
                id=iid,
                kind=r["kind"],
                house=house,
                pos=_triple(r.get("pos"), f"interactable {iid}.pos"),
                yaw=_num(r.get("yaw", 0.0), f"interactable {iid}.yaw"),
                radius=_num(r.get("radius"), f"interactable {iid}.radius"),
                name=name,
            )
        )
    return out


def _parse_place_areas(raw) -> list[PlaceArea]:
    if not isinstance(raw, list):
        raise LayoutError("place_areas must be a list")
    out = []
    for r in raw:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            raise LayoutError("every place area needs a string id")
        aid = r["id"]
        if r.get("surface") not in PLACE_SURFACES:
            raise LayoutError(f"place area {aid}: surface must be one of {PLACE_SURFACES}")
        facing = r.get("facing")
        frame = r.get("frame")
        if frame is not None:
            if not isinstance(frame, dict):
                raise LayoutError(f"place area {aid}.frame must be an object")
            frame = (_triple(frame.get("origin"), f"place area {aid}.frame.origin"),
                     _num(frame.get("yaw"), f"place area {aid}.frame.yaw"))
        out.append(
            PlaceArea(
                id=aid,
                surface=r["surface"],
                min=_triple(r.get("min"), f"place area {aid}.min"),
                max=_triple(r.get("max"), f"place area {aid}.max"),
                facing=None if facing is None else _num(facing, f"place area {aid}.facing"),
                frame=frame,
            )
        )
    return out


def load_layout(path: str, moving: bool = False) -> Layout:
    """The layout file as the generators draw it: the moving flights at
    rest in position A (`base_state`; the B flights are moving nodes the kit
    builds on its own). `moving=True` keeps both positions of every flight
    (`check_layout.py` validates every staircase state)."""
    with open(path, encoding="utf-8") as f:
        layout = parse_layout(json.load(f))
    return layout if moving else base_state(layout)


def _expand_repeat(p: dict) -> list[dict]:
    """`repeat: {count, step: [dx, dy, dz]}` copies a prop along a row; the
    copies get ids `<id>_<n>` (n from 0)."""
    rep = p.get("repeat")
    if rep is None:
        return [p]
    count, step = rep.get("count"), rep.get("step")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise LayoutError(f"prop {p.get('id')}: repeat.count must be a positive integer")
    step = _triple(step, f"prop {p.get('id')}.repeat.step")
    out = []
    for n in range(count):
        q = {k: v for k, v in p.items() if k != "repeat"}
        q["id"] = f"{p['id']}_{n}"
        q["min"] = [float(a) + n * d for a, d in zip(p["min"], step)]
        q["max"] = [float(a) + n * d for a, d in zip(p["max"], step)]
        out.append(q)
    return out


def load_props(path: str) -> list[Prop]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    props = []
    for raw in data.get("props", []):
        for p in _expand_repeat(raw):
            if p["material"] not in MATERIALS:
                raise LayoutError(f"prop {p['id']}: unknown material {p['material']!r}")
            lo, hi = tuple(float(v) for v in p["min"]), tuple(float(v) for v in p["max"])
            if any(a >= b for a, b in zip(lo, hi)):
                raise LayoutError(f"prop {p['id']}: min must be below max on every axis")
            if not CELL_ID_RE.match(p["cell"]):
                raise LayoutError(f"prop {p['id']}: invalid cell id {p['cell']!r}")
            shape = p.get("shape", "box")
            if shape not in PROP_SHAPES:
                raise LayoutError(f"prop {p['id']}: shape must be one of {PROP_SHAPES}")
            props.append(Prop(p["id"], p["cell"], p["material"], lo, hi, shape))
    ids = [p.id for p in props]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise LayoutError(f"duplicate prop ids: {sorted(dupes)}")
    return props


def load_open_edges(path: str) -> set[str]:
    """Surface ids listed in `props.json` `open_edges`: drawn without
    railings where they end at a drop (the dueling stage, whose
    edges the castle kit dresses)."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f).get("open_edges", [])
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise LayoutError("open_edges must be a list of surface ids")
    return set(raw)


HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def load_lights(path: str) -> list[Light]:
    """Lights listed next to the props (`props.json` `lights`)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    lights = []
    for r in data.get("lights", []):
        lid = r.get("id")
        if not isinstance(lid, str) or not CELL_ID_RE.match(r.get("cell", "")):
            raise LayoutError(f"light {lid!r}: needs a string id and a valid cell")
        if not HEX_RE.match(r.get("color", "")):
            raise LayoutError(f"light {lid}: color must be #rrggbb")
        intensity, rng = _num(r.get("intensity"), f"light {lid}.intensity"), _num(r.get("range"), f"light {lid}.range")
        if intensity <= 0 or rng <= 0:
            raise LayoutError(f"light {lid}: intensity and range must be positive")
        pos = _triple(r.get("pos"), f"light {lid}.pos")
        ripple = r.get("ripple", False)
        if not isinstance(ripple, bool):
            raise LayoutError(f"light {lid}: ripple must be true or false")
        # The swell's phase from where the light stands (castle_kit/fire.py seed_of: deterministic).
        from castle_kit import fire as _fire  # noqa: PLC0415  (castle_kit imports this module)
        lights.append(Light(lid, r["cell"], pos, r["color"], intensity, rng,
                            ripple=_fire.seed_of(pos) if ripple else None))
    return lights


# ---------------------------------------------------------------------------
# Connectivity (same rules as sim-core: overlap or shared edge, heights within
# step height somewhere on the contact)


def contact(a: Surface, b: Surface) -> tuple[float, float, float, float] | None:
    """Closed intersection of two footprints if it has positive length or area."""
    x0, x1 = max(a.x0, b.x0), min(a.x1, b.x1)
    z0, z1 = max(a.z0, b.z0), min(a.z1, b.z1)
    if x1 < x0 - EPS or z1 < z0 - EPS:
        return None
    if x1 - x0 <= EPS and z1 - z0 <= EPS:
        return None  # corner touch only
    return x0, z0, max(x0, x1), max(z0, z1)


def _samples(lo: float, hi: float, n: int = 9) -> list[float]:
    if hi - lo <= EPS:
        return [lo]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def connected(a: Surface, b: Surface, step: float = STEP_HEIGHT) -> bool:
    c = contact(a, b)
    if c is None:
        return False
    x0, z0, x1, z1 = c
    for x in _samples(x0, x1):
        for z in _samples(z0, z1):
            if abs(a.height(x, z) - b.height(x, z)) <= step + 1e-4:
                return True
    return False


def contact_width(a: Surface, b: Surface) -> float:
    c = contact(a, b)
    if c is None:
        return 0.0
    return max(c[2] - c[0], c[3] - c[1])


def adjacency(layout: Layout) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {s.id: [] for s in layout.surfaces}
    ss = layout.surfaces
    for i, a in enumerate(ss):
        for b in ss[i + 1 :]:
            if connected(a, b):
                adj[a.id].append(b.id)
                adj[b.id].append(a.id)
    return adj


def spawn_surface(layout: Layout, pos) -> Surface | None:
    x, y, z = pos
    best = None
    for s in layout.surfaces_at(x, z):
        d = abs(s.height(x, z) - y)
        if d <= STEP_HEIGHT and (best is None or d < best[0]):
            best = (d, s)
    return best[1] if best else None


def reachable(layout: Layout, start_ids: list[str]) -> set[str]:
    """Breadth-first walk of the connectivity graph from the given surfaces."""
    adj = adjacency(layout)
    seen = set(start_ids)
    queue = deque(start_ids)
    while queue:
        cur = queue.popleft()
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(layout: Layout) -> Report:
    """Checks the layout for everything the generator and the game rely on.

    With moving staircase flights, every flight has exactly one
    surface at "a" and one at "b", and the checks run in every combination of
    positions (`for_state`); a message found in only some states names them."""
    names = flights(layout)
    if not names:
        return _validate_state(layout)
    rep = Report()
    got = {f: sorted(s.moving[1] for s in layout.surfaces if s.moving and s.moving[0] == f) for f in names}
    if all(g == ["a"] for g in got.values()):
        return _validate_state(layout)  # the base state (`load_layout` without `moving`)
    for f, g in got.items():
        if g != ["a", "b"]:
            rep.errors.append(f"flight {f}: needs one surface at a and one at b, found {g}")
    if rep.errors:
        return rep
    states = 1 << len(names)
    seen: dict[tuple[str, str], list[str]] = {}
    for st in range(states):
        sub = _validate_state(for_state(layout, st))
        for kind, msgs in (("e", sub.errors), ("w", sub.warnings)):
            for m in msgs:
                seen.setdefault((kind, m), []).append(state_name(len(names), st))
    for (kind, m), where in seen.items():
        text = m if len(where) == states else f"{m} (staircase states {', '.join(where)})"
        (rep.errors if kind == "e" else rep.warnings).append(text)
    return rep


def _validate_state(layout: Layout) -> Report:
    """`validate` for one layout with no flight in two places."""
    rep = Report()
    err, warn = rep.errors.append, rep.warnings.append
    if layout.version != 1:
        err(f"unsupported layout version {layout.version}")
    ids = [s.id for s in layout.surfaces]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        err(f"duplicate surface ids: {sorted(dupes)}")
    for s in layout.surfaces:
        if s.x1 - s.x0 <= EPS or s.z1 - s.z0 <= EPS:
            err(f"{s.id}: min must be strictly below max")
        if not CELL_ID_RE.match(s.cell):
            err(f"{s.id}: cell id {s.cell!r} must match {CELL_ID_RE.pattern}")
        if s.kind == "stair" and not s.sloped:
            err(f"{s.id}: stairs need y_top and rise")
        if (s.y_top is None) != (s.rise is None):
            err(f"{s.id}: y_top and rise must be given together")
        if s.sloped and s.kind not in ("stair", "grounds", "bridge"):
            warn(f"{s.id}: only stairs, grounds and bridges are expected to slope")
        if s.sloped and s.y_top <= s.y:
            err(f"{s.id}: y_top must be above y (y is the low end)")
        if s.sloped and s.kind == "stair":
            run = (s.x1 - s.x0) if s.rise in ("+x", "-x") else (s.z1 - s.z0)
            if abs(s.y_top - s.y) / run > 1.0:
                err(f"{s.id}: stair steeper than 45 degrees")
        if s.ceiling is not None and s.ceiling <= 2.0:
            err(f"{s.id}: ceiling {s.ceiling} m is too low to walk under")
        if s.roofed and s.sloped and s.ceil_y - s.floor_max < HEADROOM:
            err(f"{s.id}: flat ceiling leaves no headroom at the top of the slope")
        if s.kind in ("room", "corridor", "door") and not s.roofed:
            warn(f"{s.id}: {s.kind} without a ceiling")
        if s.heights is not None:
            hf = s.heights
            if s.kind != "grounds" or s.sloped or s.y_top is not None:
                err(f"{s.id}: only grounds surfaces carry heights, never with y_top or rise")
            (ex, ez) = hf.end()
            if hf.origin[0] > s.x0 + EPS or hf.origin[1] > s.z0 + EPS or ex < s.x1 - EPS or ez < s.z1 - EPS:
                err(f"{s.id}: its heights do not cover its rectangle")
            if abs(s.y - min(hf.h)) > 0.01:
                err(f"{s.id}: y {s.y} is not its lowest height {min(hf.h)}")
            if hf.max_slope() > MAX_GROUND_SLOPE + 1e-6:
                err(f"{s.id}: heights rise {hf.max_slope():.2f} per metre somewhere (at most {MAX_GROUND_SLOPE})")
    # Overlapping surfaces must be vertically separated (stacked floors), or a
    # player could walk "through" the one above.
    ss = layout.surfaces
    for i, a in enumerate(ss):
        for b in ss[i + 1 :]:
            x0, x1 = max(a.x0, b.x0), min(a.x1, b.x1)
            z0, z1 = max(a.z0, b.z0), min(a.z1, b.z1)
            if x1 - x0 <= EPS or z1 - z0 <= EPS:
                continue
            # Where a solid takes away either one's ground (the field under a
            # pitch stand) they do not stack; where a stair meets the ground under it
            # within a step (its foot) the walker steps onto it.
            pts = [(x, z) for x in _samples(x0, x1, 5) for z in _samples(z0, z1, 5)
                   if not (layout.solid_blocks(x, a.height(x, z), z) or layout.solid_blocks(x, b.height(x, z), z))]
            if "stair" in (a.kind, b.kind):
                pts = [(x, z) for x, z in pts if abs(a.height(x, z) - b.height(x, z)) > STEP_HEIGHT + EPS]
            if not pts:
                continue
            gap = min(abs(a.height(x, z) - b.height(x, z)) for x, z in pts)
            if gap < HEADROOM:
                err(f"{a.id} and {b.id} overlap with only {gap:.2f} m between them")
            else:
                clear = _overhead_clearance(a, b, x0, x1, z0, z1, layout)
                if clear < OVERHEAD_CLEARANCE:
                    err(f"{a.id} and {b.id}: only {clear:.2f} m under the one above (needs {OVERHEAD_CLEARANCE} m)")
            # A roof slab must not poke through the floor stacked above it.
            # (A floor inside a taller room's air, like a landing in a
            # stairwell, is not stacked and is fine.)
            for lo, hi in ((a, b), (b, a)):
                stacked = lo.roofed and lo.ceil_y <= hi.floor_min + EPS
                if stacked and lo.ceil_y + SLAB > hi.floor_min + 0.01:
                    err(f"{lo.id}: ceiling + {SLAB} m slab rises above the floor of {hi.id}")
    # Doors must bridge at least two surfaces, each wide enough to walk through.
    adj = adjacency(layout)
    for s in layout.surfaces:
        if s.kind == "door":
            if len(adj[s.id]) < 2:
                err(f"{s.id}: door connects {len(adj[s.id])} surface(s), expected at least 2")
            for n in adj[s.id]:
                w = contact_width(s, layout.by_id(n))
                if w < 2 * CHARACTER_RADIUS + 0.4:
                    err(f"{s.id}: opening into {n} is only {w:.2f} m wide")
    if not layout.spawns:
        err("no spawns")
    starts = []
    for sp in layout.spawns:
        surf = spawn_surface(layout, sp["pos"])
        if surf is None:
            err(f"spawn {sp['id']} is not on a walkable surface")
        elif surf.house is not None:
            err(f"spawn {sp['id']} stands behind a house gate ({surf.house})")
        else:
            starts.append(surf.id)
    for lm in layout.landmarks:
        if spawn_surface(layout, lm["pos"]) is None:
            warn(f"landmark {lm['id']} is not on a walkable surface")
    if starts:
        seen = reachable(layout, starts)
        missing = [s.id for s in layout.surfaces if s.id not in seen]
        if missing:
            err(f"unreachable from spawn: {missing}")
    _validate_gates(layout, starts, adj, rep)
    _validate_interactables(layout, rep)
    _validate_place_areas(layout, rep)
    _validate_thin_walls(layout, adj, rep)
    return rep


def _underside_depth(s: Surface) -> float:
    """How far the geometry drawn under a walkable surface hangs below it:
    a landing's slab, or a floating flight's tread plus stringer (`Planner`)."""
    if s.kind == "stair" and s.sloped:
        steps = max(1, round(abs(s.y_top - s.y) / STEP_RISER))
        return 1.5 * abs(s.y_top - s.y) / steps + STRINGER_DEPTH
    return UNDER_SLAB


def _overhead_clearance(a: Surface, b: Surface, x0: float, x1: float, z0: float, z1: float,
                        layout: Layout | None = None) -> float:
    """Least height between the lower surface and the underside of the upper
    one where they overlap (inset by the character radius: a footprint's
    centre never stands closer to an edge than that), leaving out ground a
    solid takes away."""
    def axis(lo, hi):
        lo2, hi2 = lo + CHARACTER_RADIUS, hi - CHARACTER_RADIUS
        return _samples(lo2, hi2, 9) if hi2 > lo2 else [(lo + hi) / 2]
    best = INF
    for x in axis(x0, x1):
        for z in axis(z0, z1):
            ha, hb = a.height(x, z), b.height(x, z)
            if layout is not None and layout.solid_blocks(x, min(ha, hb), z):
                continue
            best = min(best, abs(hb - ha) - _underside_depth(b if hb > ha else a))
    return best


def _validate_gates(layout: Layout, starts: list[str], adj, rep: Report) -> None:
    """House gates: known houses, never two houses' rooms touching, public
    space reachable without crossing a gate, each house reaching its rooms."""
    gated = layout.gated()
    if not gated:
        return
    try:
        houses = set(known_houses())
    except OSError:
        houses = None  # palette not available (a stand-alone checkout of tools)
    for h in gated:
        if houses is not None and h not in houses:
            rep.errors.append(f"unknown house {h!r} on a surface gate")
    for s in layout.surfaces:
        for n in adj[s.id]:
            o = layout.by_id(n)
            if s.house and o.house and s.house != o.house and s.index < o.index:
                rep.errors.append(f"{s.id} ({s.house}) touches {o.id} ({o.house}): houses' rooms must not connect")
    if not starts:
        return
    public = for_house(layout, "\0")  # a house that matches no gate
    seen = reachable(public, [i for i in starts if public_has(public, i)])
    cut_off = [s.id for s in public.surfaces if s.id not in seen]
    if cut_off:
        rep.errors.append(f"public surfaces reachable only through a house gate: {cut_off}")
    for h in gated:
        view = for_house(layout, h)
        seen = reachable(view, starts)
        missing = [s.id for s in view.surfaces if s.house == h and s.id not in seen]
        if missing:
            rep.errors.append(f"{h}: gated surfaces unreachable for their own house: {missing}")


def public_has(layout: Layout, sid: str) -> bool:
    return any(s.id == sid for s in layout.surfaces)


def interactable_surface(layout: Layout, it: Interactable) -> Surface | None:
    """The surface an interactable stands on, among those its users may walk."""
    x, y, z = it.pos
    best = None
    for s in layout.surfaces:
        if s.house is not None and s.house != it.house:
            continue
        if s.contains(x, z) and abs(s.height(x, z) - y) <= SPAWN_TOLERANCE:
            d = abs(s.height(x, z) - y)
            if best is None or d < best[0]:
                best = (d, s)
    return best[1] if best else None


def _validate_interactables(layout: Layout, rep: Report) -> None:
    err = rep.errors.append
    ids = [i.id for i in layout.interactables]
    dupes = {i for i in ids if ids.count(i) > 1 or not i}
    if dupes:
        err(f"interactable ids empty or not unique: {sorted(dupes)}")
    for it in layout.interactables:
        if it.kind == "dorm_door" and it.house is None:
            err(f"dorm door {it.id} needs a house")
        if it.kind == "owl_order" and it.house is None:
            err(f"owl order stand {it.id} needs a house")
        if not (it.radius > 0 and math.isfinite(it.radius) and math.isfinite(it.yaw)):
            err(f"interactable {it.id}: bad radius or yaw")
        surf = interactable_surface(layout, it)
        if surf is None:
            err(f"interactable {it.id} is not on ground its users can walk on")
            continue
        # Its users must be able to walk there from a spawn (or, in an
        # instance layout, from the instance's own spawn).
        view = for_house(layout, it.house) if it.house else for_house(layout, "\0")
        starts = [s.id for s in (spawn_surface(view, sp["pos"]) for sp in layout.spawns) if s is not None]
        if starts and surf.id not in reachable(view, starts):
            err(f"interactable {it.id} on {surf.id} is unreachable for its users")
        if not any(_footprint_fits(view, it.pos[0] + dx, it.pos[2] + dz, surf.height(it.pos[0], it.pos[2]))
                   for dx, dz in _reach_probes(it.radius)):
            err(f"interactable {it.id}: no standing spot within its radius")


def _reach_probes(radius: float):
    """Candidate standing spots within an interactable's reach."""
    yield 0.0, 0.0
    for r in (radius * 0.5, radius * 0.9):
        for k in range(8):
            a = k * math.pi / 4
            yield r * math.sin(a), r * math.cos(a)


def _footprint_fits(layout: Layout, x: float, z: float, y: float) -> bool:
    """A character (radius 0.3) can stand at (x, z), like sim-core's footprint test."""
    pts = [(0.0, 0.0)] + [
        (CHARACTER_RADIUS * math.cos(k * math.pi / 4), CHARACTER_RADIUS * math.sin(k * math.pi / 4)) for k in range(8)
    ]
    return all(
        any(s.contains(x + px, z + pz, 1e-3) and abs(s.height(x + px, z + pz) - y) <= STEP_HEIGHT for s in layout.surfaces)
        for px, pz in pts
    )


def _validate_place_areas(layout: Layout, rep: Report) -> None:
    """The shape rules of `PlaceArea::validate`."""
    err = rep.errors.append
    ids = [a.id for a in layout.place_areas]
    dupes = {i for i in ids if ids.count(i) > 1 or not i}
    if dupes:
        err(f"place area ids empty or not unique: {sorted(dupes)}")
    for a in layout.place_areas:
        flat = [a.max[i] - a.min[i] <= 0.01 for i in range(3)]
        if any(a.min[i] > a.max[i] for i in range(3)):
            err(f"place area {a.id}: min must be <= max")
        elif a.surface in ("floor", "table"):
            if not flat[1] or flat[0] or flat[2] or a.facing is not None:
                err(f"place area {a.id}: floor and table areas are horizontal rectangles without a facing")
        elif a.frame is not None:
            err(f"place area {a.id}: wall areas have no frame")
        else:
            if flat[0] == flat[2] or flat[1]:
                err(f"place area {a.id}: wall areas are vertical and flat along exactly one of x, z")
            elif a.facing is None:
                err(f"place area {a.id}: wall areas need a facing")
            else:
                options = (math.pi / 2, 3 * math.pi / 2) if a.wall_on_x_plane else (0.0, math.pi)
                d = lambda u, v: abs((u - v + math.pi) % (2 * math.pi) - math.pi)  # noqa: E731
                if not any(d(a.facing, o) <= 1e-3 for o in options):
                    err(f"place area {a.id}: facing must be perpendicular to the wall")


def _validate_thin_walls(layout: Layout, adj, rep: Report) -> None:
    """Unconnected surfaces at walking height must be more than one path
    sample apart, or a move could hop the wall between them unnoticed."""
    ss = layout.surfaces
    for i, a in enumerate(ss):
        for b in ss[i + 1 :]:
            if b.id in adj[a.id]:
                continue
            gx = max(a.x0, b.x0) - min(a.x1, b.x1)
            gz = max(a.z0, b.z0) - min(a.z1, b.z1)
            if max(gx, gz) <= EPS:
                continue  # touching or overlapping: handled by the stacking rules
            if gx > PATH_SAMPLE_SPACING + EPS or gz > PATH_SAMPLE_SPACING + EPS:
                continue
            if a.floor_min - STEP_HEIGHT <= b.floor_max and b.floor_min - STEP_HEIGHT <= a.floor_max:
                rep.errors.append(f"{a.id} and {b.id} are only {max(gx, gz):.2f} m apart: a wall needs more than {PATH_SAMPLE_SPACING} m")


# ---------------------------------------------------------------------------
# Small geometry helpers


def newell_normal(poly) -> tuple[float, float, float]:
    nx = ny = nz = 0.0
    for i, (x, y, z) in enumerate(poly):
        x2, y2, z2 = poly[(i + 1) % len(poly)]
        nx += (y - y2) * (z + z2)
        ny += (z - z2) * (x + x2)
        nz += (x - x2) * (y + y2)
    return nx, ny, nz


def oriented(poly, normal) -> list[tuple[float, float, float]]:
    """Returns `poly` wound so its front faces along `normal`."""
    n = newell_normal(poly)
    if n[0] * normal[0] + n[1] * normal[1] + n[2] * normal[2] < 0:
        return list(reversed(poly))
    return list(poly)


def _pt(axis: str, c: float, t: float, y: float) -> tuple[float, float, float]:
    """Point on the vertical plane `axis = c` at along-coordinate t and height y."""
    return (c, y, t) if axis == "x" else (t, y, c)


def _axis_normal(axis: str, sign: float) -> tuple[float, float, float]:
    return (sign, 0.0, 0.0) if axis == "x" else (0.0, 0.0, sign)


# Grounds edges: an outer face over open ground runs this far under the ground's lowest point along it.
GROUNDS_SINK = 0.5
# ... when its foot stands at most this far over that ground (a storey higher up keeps its own).
GROUNDS_REACH = 2.0


def _ground_along(s: "Surface", axis: str, c: float, ta: float, tb: float, every: float = 0.5) -> float:
    """The lowest ground of grounds surface `s` along its edge nearest the wall line
    `axis` = `c`, from `ta` to `tb` along the line (sampled every `every` m, both ends;
    a line beside the surface takes its nearest corner)."""
    e0, e1 = s.extent(axis)
    a = min(max(c, e0), e1)
    o0, o1 = s.extent("z" if axis == "x" else "x")
    lo, hi = max(ta, o0), min(tb, o1)
    if lo > hi:  # beside the surface: its nearest corner
        lo = hi = o0 if tb < o0 else o1
    n = max(1, int(math.ceil((hi - lo) / every - 1e-9)))
    ts = [lo + (hi - lo) * k / n for k in range(n + 1)]
    return min(s.height(a, t) if axis == "x" else s.height(t, a) for t in ts)


def vertical_quad(axis, c, ta, tb, bot, top, sign):
    """Quad on plane axis=c between t=ta..tb; bot/top are (at ta, at tb) heights."""
    poly = [_pt(axis, c, ta, bot[0]), _pt(axis, c, tb, bot[1]), _pt(axis, c, tb, top[1]), _pt(axis, c, ta, top[0])]
    if abs(top[0] - bot[0]) < 1e-4:
        poly.pop(3)
    elif abs(top[1] - bot[1]) < 1e-4:
        poly.pop(2)
    if len(poly) < 3:
        return None
    return oriented(poly, _axis_normal(axis, sign))


def horizontal_quad(x0, z0, x1, z1, hfun, up: bool):
    poly = [(x0, hfun(x0, z0), z0), (x1, hfun(x1, z0), z0), (x1, hfun(x1, z1), z1), (x0, hfun(x0, z1), z1)]
    return oriented(poly, (0.0, 1.0 if up else -1.0, 0.0))


def box_faces(lo, hi, skip=()) -> list[tuple[str, list]]:
    """Faces of an axis-aligned box as (side, polygon); side in +x -x +y -y +z -z."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    faces = {
        "+x": [(x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)],
        "-x": [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
        "+y": [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],
        "-y": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
        "+z": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        "-z": [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)],
    }
    normals = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}
    return [(k, oriented(p, normals[k])) for k, p in faces.items() if k not in skip]


def rect_subtract(rect, holes) -> list[tuple[float, float, float, float]]:
    """Rectangle minus a union of rectangles, as a short list of rectangles."""
    x0, z0, x1, z1 = rect
    clipped = []
    for hx0, hz0, hx1, hz1 in holes:
        cx0, cz0, cx1, cz1 = max(x0, hx0), max(z0, hz0), min(x1, hx1), min(z1, hz1)
        if cx1 - cx0 > EPS and cz1 - cz0 > EPS:
            clipped.append((cx0, cz0, cx1, cz1))
    if not clipped:
        return [rect]
    xs = sorted({x0, x1, *[h[0] for h in clipped], *[h[2] for h in clipped]})
    zs = sorted({z0, z1, *[h[1] for h in clipped], *[h[3] for h in clipped]})
    keep = [[False] * (len(zs) - 1) for _ in range(len(xs) - 1)]
    for i in range(len(xs) - 1):
        for j in range(len(zs) - 1):
            mx, mz = (xs[i] + xs[i + 1]) / 2, (zs[j] + zs[j + 1]) / 2
            keep[i][j] = not any(h[0] < mx < h[2] and h[1] < mz < h[3] for h in clipped)
    # Merge each column's runs, then merge identical runs across columns.
    out: list[list[float]] = []
    open_runs: dict[tuple[int, int], list[float]] = {}
    for i in range(len(xs) - 1):
        runs = []
        j = 0
        while j < len(zs) - 1:
            if keep[i][j]:
                k = j
                while k + 1 < len(zs) - 1 and keep[i][k + 1]:
                    k += 1
                runs.append((j, k + 1))
                j = k + 1
            else:
                j += 1
        nxt: dict[tuple[int, int], list[float]] = {}
        for run in runs:
            if run in open_runs:
                r = open_runs[run]
                r[2] = xs[i + 1]
            else:
                r = [xs[i], zs[run[0]], xs[i + 1], zs[run[1]]]
                out.append(r)
            nxt[run] = r
        open_runs = nxt
    return [tuple(r) for r in out]


# ---------------------------------------------------------------------------
# Linear height functions along a wall line, and interval sets built from them


@dataclass(frozen=True)
class Lin:
    """A height that varies linearly from `a` (segment start) to `b` (segment end)."""

    a: float
    b: float

    @property
    def mid(self) -> float:
        return (self.a + self.b) / 2

    def at(self, f: float) -> float:
        if math.isinf(self.a):
            return self.a
        return self.a + (self.b - self.a) * f

    def sub(self, fa: float, fb: float) -> "Lin":
        return Lin(self.at(fa), self.at(fb))

    def plus(self, d: float) -> "Lin":
        return Lin(self.a + d, self.b + d)

    @property
    def finite(self) -> bool:
        return not (math.isinf(self.a) or math.isinf(self.b))


LIN_INF = Lin(INF, INF)


def lin_crossings(lins) -> list[float]:
    """Fractions in (0, 1) where any two finite linear functions cross."""
    fs = set()
    fin = [l for l in lins if l.finite]
    for i, p in enumerate(fin):
        for q in fin[i + 1 :]:
            d0, d1 = p.a - q.a, p.b - q.b
            if d0 * d1 < 0:
                f = d0 / (d0 - d1)
                if 1e-6 < f < 1 - 1e-6:
                    fs.add(round(f, 9))
    return sorted(fs)


def iv_union(ivs):
    """Union of (lo, hi) Lin intervals; ordering is decided at the midpoint."""
    ivs = sorted((iv for iv in ivs if iv[1].mid - iv[0].mid > 1e-6), key=lambda iv: iv[0].mid)
    out = []
    for lo, hi in ivs:
        if out and lo.mid <= out[-1][1].mid + 1e-6:
            if hi.mid > out[-1][1].mid:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return out


def iv_subtract(ivs, holes):
    out = list(iv_union(ivs))
    for hlo, hhi in iv_union(holes):
        nxt = []
        for lo, hi in out:
            if hhi.mid <= lo.mid + 1e-6 or hlo.mid >= hi.mid - 1e-6:
                nxt.append((lo, hi))
                continue
            if hlo.mid > lo.mid + 1e-6:
                nxt.append((lo, hlo))
            if hhi.mid < hi.mid - 1e-6:
                nxt.append((hhi, hi))
        out = nxt
    return out


def iv_covers(ivs, y: float) -> bool:
    return any(lo.mid - 1e-6 <= y <= hi.mid + 1e-6 for lo, hi in ivs)


# ---------------------------------------------------------------------------
# Geometry plan


@dataclass
class Plan:
    """Polygons grouped by (cell, material), plus the wall pieces for debugging."""

    polys: dict[tuple[str, str], list[list[tuple[float, float, float]]]] = field(default_factory=dict)
    pieces: list["WallPiece"] = field(default_factory=list)

    def add(self, cell: str, material: str, poly) -> None:
        if poly is not None and len(poly) >= 3:
            self.polys.setdefault((cell, material), []).append(poly)

    def cells(self) -> list[str]:
        seen: dict[str, None] = {}
        for cell, _ in self.polys:
            seen.setdefault(cell, None)
        return list(seen)

    def bounds(self, cell: str):
        lo = [INF, INF, INF]
        hi = [-INF, -INF, -INF]
        for (c, _), polys in self.polys.items():
            if c != cell:
                continue
            for poly in polys:
                for p in poly:
                    for k in range(3):
                        lo[k] = min(lo[k], p[k])
                        hi[k] = max(hi[k], p[k])
        return lo, hi

    def triangle_count(self) -> int:
        return sum(len(p) - 2 for polys in self.polys.values() for p in polys)


@dataclass(frozen=True)
class WallHole:
    """A rectangular hole the castle kit cuts from a wall face (castle kit
    M1: windows, niches, blind doors; arches need none, the Planner leaves
    door openings by itself). The face is the one `surface` owns on the line
    `axis = c` facing `sign`; the hole spans t0..t1 along the line and
    y0..y1. `through` holes (windows) are also cut from that wall's outer
    shell face, so they open to the outside."""

    surface: str
    axis: str
    c: float
    sign: float
    t0: float
    t1: float
    y0: float
    y1: float
    through: bool = False


def holed_quads(axis, c, ta, tb, y0, y1, sign, holes) -> list:
    """The flat wall quad ta..tb x y0..y1 on `axis = c` facing `sign` with
    rectangular `holes` (t0, y0, t1, y1) removed, as a few quads."""
    rects = rect_subtract((ta, y0, tb, y1), holes)
    return [vertical_quad(axis, c, a, b, (lo, lo), (hi, hi), sign) for a, lo, b, hi in rects]


@dataclass
class WallPiece:
    owner: Surface
    axis: str  # the line is `axis = c`
    c: float
    sign: float  # face normal along the axis (+1 or -1); the owner is on that side
    ta: float
    tb: float
    lo: Lin
    hi: Lin


# Floors this far below the ground floor are dungeons: darker, damper stone.
DUNGEON_BELOW = -0.5


def floor_material(s: Surface) -> str:
    # The grounds slope down to the lake: grass all the way, never dungeon stone.
    if s.floor_max < DUNGEON_BELOW and s.kind not in ("stair", "grounds"):
        return "floor_dungeon"
    return f"floor_{s.kind}" if f"floor_{s.kind}" in MATERIALS else "floor_room"


def wall_material(s: Surface) -> str:
    if s.kind == "grounds":
        return "wall_low"
    return "wall_dungeon" if s.floor_max < DUNGEON_BELOW else "wall"


class Planner:
    def __init__(self, layout: Layout, props: list[Prop] | None = None, open_edges=(), holes=()):
        self.layout = layout
        self.props = props or []
        # Surfaces drawn without railings at their drops (the kit dresses
        # their edges instead, e.g. the Dueling Club stage).
        self.open_edges = set(open_edges)
        self.plan = Plan()
        self.ss = layout.surfaces
        # Railing runs keyed by (surface id, axis, c, sign) -> list of [ta, tb].
        self.railings: dict[tuple[str, str, float, float], list[list[float]]] = {}
        # Castle kit wall openings (WallHole) by the face they are cut from.
        self.holes: dict[tuple[str, str, float, float], list[WallHole]] = {}
        for h in holes:
            self.holes.setdefault((h.surface, h.axis, round(h.c, 4), h.sign), []).append(h)

    def _face_holes(self, owner: Surface, axis: str, c: float, sign: float, lo: Lin, hi: Lin, through_only: bool):
        """Hole rectangles (t0, y0, t1, y1) cut from this face, or [] (holes
        need a flat face: a wall of a flat floor under a flat ceiling)."""
        hs = self.holes.get((owner.id, axis, round(c, 4), sign), [])
        if not hs:
            return []
        if abs(lo.a - lo.b) > 1e-6 or abs(hi.a - hi.b) > 1e-6:
            raise LayoutError(f"{owner.id}: wall openings need a flat wall face on {axis} = {c}")
        return [(h.t0, h.y0, h.t1, h.y1) for h in hs if h.through or not through_only]

    # -- per-line functions -------------------------------------------------

    def _floor(self, s: Surface, axis: str, c: float, ta: float, tb: float) -> Lin:
        if axis == "x":
            return Lin(s.height(c, ta), s.height(c, tb))
        return Lin(s.height(ta, c), s.height(tb, c))

    def _air(self, s, axis, c, ta, tb):
        f = self._floor(s, axis, c, ta, tb)
        return f, (Lin(s.ceil_y, s.ceil_y) if s.roofed else LIN_INF)

    def _wall(self, s, axis, c, ta, tb):
        """Span a surface wants enclosed along this line, or None."""
        f = self._floor(s, axis, c, ta, tb)
        if s.roofed:
            return f, Lin(s.ceil_y, s.ceil_y)
        w = s.wall_height()
        if not w or s.kind in ("stair", "bridge"):
            return None  # open stairs and bridges get railings instead
        return f, f.plus(w)

    # -- walls --------------------------------------------------------------

    def build_walls(self) -> None:
        for axis in ("x", "z"):
            lines = sorted({v for s in self.ss for v in s.extent(axis)})
            for c in lines:
                self._build_line(axis, c)

    def _build_line(self, axis: str, c: float) -> None:
        other = "z" if axis == "x" else "x"
        neg = [s for s in self.ss if abs(s.extent(axis)[1] - c) < EPS]
        pos = [s for s in self.ss if abs(s.extent(axis)[0] - c) < EPS]
        cross = [s for s in self.ss if s.extent(axis)[0] < c - EPS and s.extent(axis)[1] > c + EPS]
        if not neg and not pos:
            return
        ts = sorted({v for s in neg + pos + cross for v in s.extent(other)})
        for ta, tb in zip(ts, ts[1:]):
            if tb - ta <= EPS:
                continue

            def covering(group):
                return [s for s in group if s.extent(other)[0] <= ta + EPS and s.extent(other)[1] >= tb - EPS]

            n, p, x = covering(neg), covering(pos), covering(cross)
            if n or p:
                self._build_segment(axis, c, ta, tb, n, p, x)

    def _build_segment(self, axis, c, ta, tb, neg, pos, cross) -> None:
        # Split where any two height functions cross so interval topology is
        # constant on each piece and every bound stays linear.
        lins = []
        for s in neg + pos + cross:
            f = self._floor(s, axis, c, ta, tb)
            lins += [f, f.plus(STEP_HEIGHT), f.plus(-STEP_HEIGHT)]
            if s.roofed:
                lins.append(Lin(s.ceil_y, s.ceil_y))
            elif s.wall_height():
                lins.append(f.plus(s.wall_height()))
        fracs = [0.0, *lin_crossings(lins), 1.0]
        for fa, fb in zip(fracs, fracs[1:]):
            sa, sb = ta + (tb - ta) * fa, ta + (tb - ta) * fb

            def air(group):
                return [self._air(s, axis, c, sa, sb) for s in group]

            def walls(group):
                return [w for w in (self._wall(s, axis, c, sa, sb) for s in group) if w is not None]

            # Faces with normal -axis are seen from the negative side.
            face_neg = iv_subtract(walls(neg), air(pos) + air(cross))
            face_pos = iv_subtract(walls(pos), air(neg) + air(cross))
            for sign, faces, owners in ((-1.0, face_neg, neg), (1.0, face_pos, pos)):
                for lo, hi in faces:
                    owner = self._owner(owners, axis, c, sa, sb, (lo.mid + hi.mid) / 2)
                    piece = WallPiece(owner, axis, c, sign, sa, sb, lo, hi)
                    self.plan.pieces.append(piece)
                    cut = self._face_holes(owner, axis, c, sign, lo, hi, False)
                    if cut:
                        for q in holed_quads(axis, c, sa, sb, lo.a, hi.a, sign, cut):
                            self.plan.add(owner.cell, wall_material(owner), q)
                        continue
                    self.plan.add(owner.cell, wall_material(owner), vertical_quad(axis, c, sa, sb, (lo.a, lo.b), (hi.a, hi.b), sign))
            # Railings and slab edges where a floor ends at a drop. `sign`
            # points from the line into the surfaces in `mine`.
            for sign, mine, theirs, own_faces, their_faces in (
                (-1.0, neg, pos + cross, face_neg, face_pos),
                (1.0, pos, neg + cross, face_pos, face_neg),
            ):
                for s in mine:
                    self._edge_drop(s, axis, c, sa, sb, sign, theirs, own_faces, their_faces)

    def _owner(self, owners, axis, c, sa, sb, ym) -> Surface:
        cands = []
        for s in owners:
            w = self._wall(s, axis, c, sa, sb)
            if w and w[0].mid - 1e-6 <= ym <= w[1].mid + 1e-6:
                cands.append(s)
        cands = cands or owners
        return sorted(cands, key=lambda s: (not s.roofed, -s.struct_top(), s.index))[0]

    def _edge_drop(self, s, axis, c, sa, sb, sign, theirs, own_faces, their_faces) -> None:
        if s.kind == "door":
            return
        h = self._floor(s, axis, c, sa, sb).mid
        floors = [self._floor(o, axis, c, sa, sb).mid for o in theirs if o is not s]
        if any(abs(f - h) <= STEP_HEIGHT + 0.01 for f in floors):
            return  # walkable continuation
        if not any(f < h - STEP_HEIGHT for f in floors):
            return  # nothing below: walls handle the edge
        if not iv_covers(own_faces, h + RAILING_HEIGHT / 2):
            key = (s.id, axis, c, sign)
            runs = self.railings.setdefault(key, [])
            if runs and abs(runs[-1][1] - sa) < 1e-6:
                runs[-1][1] = sb
            else:
                runs.append([sa, sb])
        if s.kind != "stair" and not iv_covers(their_faces, h - UNDER_SLAB / 2):
            f = self._floor(s, axis, c, sa, sb)
            q = vertical_quad(axis, c, sa, sb, (f.a - UNDER_SLAB, f.b - UNDER_SLAB), (f.a, f.b), -sign)
            self.plan.add(s.cell, "trim", q)

    def build_railings(self) -> None:
        for (sid, axis, c, sign), runs in self.railings.items():
            if sid in self.open_edges:
                continue
            s = self.layout.by_id(sid)
            # The railing sits just inside the owner's edge (sign points into it).
            inner = c + sign * (RAILING_INSET + RAILING_THICKNESS)
            outer = c + sign * RAILING_INSET
            other = "z" if axis == "x" else "x"
            e0, e1 = s.extent(other)
            for ta, tb in runs:
                # Overlap the railing meeting this one at an inside corner. At
                # the owner's own (convex) corners the z-line railings run to the
                # end and the x-line ones stop short, so caps never overlap.
                ext = RAILING_INSET + RAILING_THICKNESS
                ta, tb = max(e0, ta - ext), min(e1, tb + ext)
                if axis == "x":
                    if ta - e0 < EPS:
                        ta = e0 + ext
                    if e1 - tb < EPS:
                        tb = e1 - ext
                    if tb - ta < EPS:
                        continue
                # On stairs the railing starts at the tread nosings (half a
                # riser above the ramp) so it never cuts into a step.
                f = self._floor(s, axis, c, ta, tb).plus(self._riser(s) / 2)
                bot, top = (f.a, f.b), (f.a + RAILING_HEIGHT, f.b + RAILING_HEIGHT)
                for plane, nsign in ((inner, sign), (outer, -sign)):
                    self.plan.add(s.cell, "railing", vertical_quad(axis, plane, ta, tb, bot, top, nsign))
                # Top and end caps.
                a, b = sorted((inner, outer))
                if axis == "x":
                    cap = [(a, top[0], ta), (b, top[0], ta), (b, top[1], tb), (a, top[1], tb)]
                else:
                    cap = [(ta, top[0], a), (tb, top[1], a), (tb, top[1], b), (ta, top[0], b)]
                self.plan.add(s.cell, "railing", oriented(cap, (0, 1, 0)))
                for t, h, nsg in ((ta, f.a, -1.0), (tb, f.b, 1.0)):
                    q = vertical_quad(other, t, a, b, (h, h), (h + RAILING_HEIGHT, h + RAILING_HEIGHT), nsg)
                    self.plan.add(s.cell, "railing", q)

    # -- exterior shell -----------------------------------------------------

    def _near(self, pc: WallPiece, lo_y: float, hi_y: float) -> list[tuple[float, Surface]]:
        """Surfaces just beyond a wall piece (within one wall mass), with distances."""
        out_dir = -pc.sign
        other = "z" if pc.axis == "x" else "x"
        near = []
        for s in self.ss:
            e0, e1 = s.extent(pc.axis)
            d = (e0 - pc.c) if out_dir > 0 else (pc.c - e1)
            if d <= EPS or d > WALL_MASS_MAX:
                continue
            if s.extent(other)[0] >= pc.tb - EPS or s.extent(other)[1] <= pc.ta + EPS:
                continue
            if s.floor_min - SLAB >= hi_y or s.struct_top() <= lo_y:
                continue
            near.append((d, s))
        return near

    def _run_thickness(self) -> dict[int, float]:
        """One outer-face offset per contiguous run of wall pieces on a line side.

        Neighbouring pieces with different offsets would leave visible slits
        between their outer faces, so a run uses the thinnest offset any of its
        pieces needs (half the gap to the nearest room beyond, at most the
        exterior thickness).
        """
        groups: dict[tuple[str, float, float], list[WallPiece]] = {}
        for pc in self.plan.pieces:
            if pc.owner.kind != "door":
                groups.setdefault((pc.axis, round(pc.c, 4), pc.sign), []).append(pc)
        out: dict[int, float] = {}
        for pieces in groups.values():
            pieces.sort(key=lambda p: p.ta)
            run: list[WallPiece] = []
            for pc in pieces + [None]:
                if pc is not None and (not run or pc.ta <= max(p.tb for p in run) + EPS):
                    run.append(pc)
                    continue
                t = INF
                for p in run:
                    t = min(t, EXTERIOR_THICKNESS.get(p.owner.kind, EXTERIOR_THICKNESS_DEFAULT))
                    lo_y, hi_y = min(p.lo.a, p.lo.b), max(p.hi.a, p.hi.b) + SLAB
                    for d, _ in self._near(p, lo_y, hi_y):
                        t = min(t, d / 2)
                for p in run:
                    out[id(p)] = t
                run = [pc] if pc is not None else []
        return out

    def build_shell(self) -> None:
        """Outer faces and wall-top caps so the massing reads from outside."""
        thickness = self._run_thickness()
        flat_caps: list[tuple[str, str, tuple[float, float, float, float], float]] = []
        for pc in self.plan.pieces:
            o = pc.owner
            if o.kind == "door":
                continue  # door reveals always sit inside a wall mass
            out_dir = -pc.sign
            other = "z" if pc.axis == "x" else "x"
            thick = thickness[id(pc)]
            across = [
                s
                for s in self.ss
                if abs((s.extent(pc.axis)[0] if out_dir > 0 else s.extent(pc.axis)[1]) - pc.c) < EPS
                and s.extent(other)[0] < pc.tb - EPS
                and s.extent(other)[1] > pc.ta + EPS
            ]
            # A riser directly under a neighbouring floor needs no outer face.
            if any(abs(self._floor(s, pc.axis, pc.c, pc.ta, pc.tb).mid - pc.hi.mid) < 0.05 for s in across):
                continue
            o_top = self._wall(o, pc.axis, pc.c, pc.ta, pc.tb)[1]
            reaches_top = abs(pc.hi.a - o_top.a) < 0.01 and abs(pc.hi.b - o_top.b) < 0.01
            top_ext = SLAB if (o.roofed and reaches_top) else 0.0
            lo_y, hi_y = min(pc.lo.a, pc.lo.b), max(pc.hi.a, pc.hi.b) + top_ext
            near = self._near(pc, lo_y, hi_y)
            cuts = sorted({pc.ta, pc.tb, *[v for _, s in near for v in s.extent(other) if pc.ta < v < pc.tb]})
            for ua, ub in zip(cuts, cuts[1:]):
                fa, fb = (ua - pc.ta) / (pc.tb - pc.ta), (ub - pc.ta) / (pc.tb - pc.ta)
                lo, hi = pc.lo.sub(fa, fb), pc.hi.sub(fa, fb).plus(top_ext)
                um = (ua + ub) / 2
                here = [(d, s) for d, s in near if s.extent(other)[0] <= um <= s.extent(other)[1]]
                # Grounds edges (docs/architecture.md): open ground beyond the wall is no room
                # sharing its mass. Taken as one, its walls' top (the rectangle's highest
                # ground, 0.58 m south of the courtyard) hid the outer face below it, so the
                # wall hung over the grass by up to that much, the courtyard's paving showing
                # under it. The face now runs down past the lowest ground near it (below).
                here = [(d, s) for d, s in here if s.kind != "grounds"]
                # Close convex corners of the owner's rectangle.
                ea, eb = ua, ub
                if abs(ua - o.extent(other)[0]) < EPS:
                    ea = ua - self._corner_extension(o, pc, ua, -1, thick)
                if abs(ub - o.extent(other)[1]) < EPS:
                    eb = ub + self._corner_extension(o, pc, ub, 1, thick)
                plane = pc.c + out_dir * thick
                if here:
                    # The wall mass is shared with the room beyond: only the part
                    # rising above that room's walls is visible from outside.
                    hidden = max(s.struct_top() for _, s in here)
                    bot = (max(lo.a, hidden), max(lo.b, hidden))
                else:
                    on_floor = abs(lo.mid - self._floor(o, pc.axis, pc.c, ua, ub).mid) < 0.01
                    stacked = self._has_floor_below(o)
                    if on_floor and stacked:
                        bot = (lo.a, lo.b)  # the storey below's shell reaches up to here
                    elif on_floor and o.floor_min > 1.0:
                        bot = (GROUND_Y, GROUND_Y)  # floating upper floor: solid to the ground
                    else:
                        # Sink below the floor to hide the slab edge.
                        sink = SLAB if o.roofed else 0.5
                        bot = (lo.a - sink, lo.b - sink)
                    foot = self._grounds_foot(pc.axis, plane, out_dir, ea, eb)
                    if foot is not None and min(bot) <= foot + GROUNDS_SINK + GROUNDS_REACH:
                        bot = (min(bot[0], foot), min(bot[1], foot))
                if hi.a - bot[0] > 0.01 or hi.b - bot[1] > 0.01:
                    top_pair = (hi.a, hi.b)
                    bot_pair = (min(bot[0], hi.a), min(bot[1], hi.b))
                    cut = self._face_holes(o, pc.axis, pc.c, pc.sign, pc.lo, pc.hi, True) if not here else []
                    if cut and abs(bot_pair[0] - bot_pair[1]) < 1e-6 and abs(top_pair[0] - top_pair[1]) < 1e-6:
                        for q in holed_quads(pc.axis, plane, ea, eb, bot_pair[0], top_pair[0], out_dir, cut):
                            self.plan.add(o.cell, "wall_exterior", q)
                    else:
                        self.plan.add(o.cell, "wall_exterior", vertical_quad(pc.axis, plane, ea, eb, bot_pair, top_pair, out_dir))
                if reaches_top:
                    # x-lines leave corner squares to the z-lines' caps.
                    ca, cb = (ua, ub) if pc.axis == "x" else (ea, eb)
                    a, b = sorted((pc.c, plane))
                    if abs(hi.a - hi.b) > 1e-4:
                        if pc.axis == "x":
                            cap = [(a, hi.a, ca), (b, hi.a, ca), (b, hi.b, cb), (a, hi.b, cb)]
                        else:
                            cap = [(ca, hi.a, a), (cb, hi.b, a), (cb, hi.b, b), (ca, hi.a, b)]
                        self.plan.add(o.cell, "wall_exterior", oriented(cap, (0, 1, 0)))
                    else:
                        rect = (a, ca, b, cb) if pc.axis == "x" else (ca, a, cb, b)
                        flat_caps.append((pc.axis, o.cell, rect, hi.a, self._stacked_walls(o, pc, a, b, hi.a)))
        # A flat cap must leave room for floors at the same height (a door of
        # the storey above crossing the wall mass), and is not drawn under the
        # wall of a storey standing on it (castle M5: inside that wall mass it
        # would show through a kit recess at the upper floor). Where two walls'
        # masses meet at an inside corner (around a stair's core) the z-line
        # cap owns the corner square and the x-line cap is trimmed by it.
        z_caps = [(rect, y) for axis, _, rect, y, _ in flat_caps if axis == "z"]
        for axis, cell, rect, y, stacked in flat_caps:
            holes = [(s.x0, s.z0, s.x1, s.z1) for s in self.ss if abs(s.floor_max - y) < 0.01] + stacked
            if axis == "x":
                holes += [r for r, zy in z_caps if abs(zy - y) < 1e-4]
            for r in rect_subtract(rect, holes):
                self.plan.add(cell, "wall_exterior", horizontal_quad(*r, lambda x, z, y=y: y, True))
        outer_cut_m5c(self)  # Castle M5c (Facades): windows over doors cut through a mass shared with the courtyard

    def _grounds_foot(self, axis: str, plane: float, out_dir: float, ta: float, tb: float):
        """Grounds edges: the lowest ground of the grounds surfaces within a wall mass
        (`WALL_MASS_MAX`) in front of an outer face (on `axis` = `plane`, from `ta` to
        `tb` along it, facing `out_dir`), less `GROUNDS_SINK`: how low the face must
        reach so the drawn ground (the terrain and its castle lip) meets it; None
        with no grounds there. A face beside one, round a corner (the courtyard's west
        face over the lip's end), counts too."""
        other = "z" if axis == "x" else "x"
        lows = []
        for s in self.ss:
            if s.kind != "grounds":
                continue
            e0, e1 = s.extent(axis)
            if (e1 <= plane + EPS) if out_dir > 0 else (e0 >= plane - EPS):
                continue  # behind the face
            across = max((e0 - plane) if out_dir > 0 else (plane - e1), 0.0)
            o0, o1 = s.extent(other)
            along = max(o0 - tb, 0.0, ta - o1)
            if math.hypot(across, along) > WALL_MASS_MAX:
                continue
            lows.append(_ground_along(s, axis, plane, ta, tb))
        return min(lows) - GROUNDS_SINK if lows else None

    def _corner_extension(self, o: Surface, pc: WallPiece, t: float, direction: int, thick: float) -> float:
        """How far an outer face may run past the owner's corner to close it.

        Zero if another surface touches just beyond the corner (the wall
        continues there); otherwise the wall thickness, but at most half the
        gap to the next room along the same facade so two neighbours' outer
        faces meet in the middle instead of overlapping.
        """
        out_dir = -pc.sign
        other = "z" if pc.axis == "x" else "x"
        probe_t = t + direction * 0.01
        for probe_c in (pc.c + out_dir * 0.01, pc.c - out_dir * 0.01):
            x, z = (probe_c, probe_t) if pc.axis == "x" else (probe_t, probe_c)
            if any(s is not o and s.contains(x, z, 0) for s in self.ss):
                return 0.0
        band = sorted((pc.c - out_dir * 1.0, pc.c + out_dir * thick))
        ext = thick
        for s in self.ss:
            if s is o:
                continue
            a0, a1 = s.extent(pc.axis)
            if a1 <= band[0] + EPS or a0 >= band[1] - EPS:
                continue
            s0, s1 = s.extent(other)
            gap = (s0 - t) if direction > 0 else (t - s1)
            if gap > -EPS:
                ext = min(ext, max(0.0, gap / 2))
        return ext

    def _stacked_walls(self, o: Surface, pc: WallPiece, a: float, b: float, y: float) -> list:
        """Plan rectangles of a wall-top cap (wall mass a..b across the line)
        that lie under the wall of a storey standing on `o`: a surface whose
        floor is at the cap's height, overlapping `o` in plan, with its edge
        on the same wall line (its shell starts at its floor there)."""
        other = "z" if pc.axis == "x" else "x"
        side = 1 if pc.sign < 0 else 0  # the owner's edge on this line: max when the wall faces -axis
        out = []
        for s in self.ss:
            if s is o or abs(s.floor_min - y) >= 0.01 or abs(s.extent(pc.axis)[side] - pc.c) >= EPS:
                continue
            if min(s.x1, o.x1) - max(s.x0, o.x0) <= EPS or min(s.z1, o.z1) - max(s.z0, o.z0) <= EPS:
                continue
            t0, t1 = s.extent(other)
            out.append((a, t0, b, t1) if pc.axis == "x" else (t0, a, t1, b))
        return out

    def _has_floor_below(self, o: Surface) -> bool:
        for s in self.ss:
            if s is o or s.floor_max >= o.floor_min - EPS:
                continue
            if min(s.x1, o.x1) - max(s.x0, o.x0) > EPS and min(s.z1, o.z1) - max(s.z0, o.z0) > EPS:
                return True
        return False

    # -- floors, ceilings, roofs, stairs ------------------------------------

    def build_horizontals(self) -> None:
        roofed = sorted((s for s in self.ss if s.roofed), key=lambda s: (s.floor_min, s.index))
        for i, s in enumerate(self.ss):
            if s.kind == "stair":
                self._build_stair(s)
            elif s.heights is not None:
                # One quad per lattice square, on the samples.
                hf = s.heights
                xs = [x for x in (hf.origin[0] + hf.step * i for i in range(hf.cols)) if s.x0 - EPS <= x <= s.x1 + EPS]
                zs = [z for z in (hf.origin[1] + hf.step * j for j in range(hf.rows)) if s.z0 - EPS <= z <= s.z1 + EPS]
                xs = sorted({s.x0, s.x1, *xs})
                zs = sorted({s.z0, s.z1, *zs})
                for za, zb in zip(zs, zs[1:]):
                    for xa, xb in zip(xs, xs[1:]):
                        self.plan.add(s.cell, floor_material(s), horizontal_quad(xa, za, xb, zb, s.height, True))
            else:
                self.plan.add(s.cell, floor_material(s), horizontal_quad(s.x0, s.z0, s.x1, s.z1, s.height, True))
                if self._visible_from_below(s):
                    h = lambda x, z, s=s: s.height(x, z) - UNDER_SLAB
                    self.plan.add(s.cell, "trim", horizontal_quad(s.x0, s.z0, s.x1, s.z1, h, False))
        for rank, s in enumerate(roofed):
            rect = (s.x0, s.z0, s.x1, s.z1)
            earlier = roofed[:rank]
            same_ceil = [(e.x0, e.z0, e.x1, e.z1) for e in earlier if abs(e.ceil_y - s.ceil_y) < 0.01]
            for r in rect_subtract(rect, same_ceil):
                self.plan.add(s.cell, "ceiling", horizontal_quad(*r, lambda x, z, y=s.ceil_y: y, False))
            if s.kind == "door":
                continue
            roof_y = s.ceil_y + SLAB
            holes = list(same_ceil)
            holes += [
                (o.x0, o.z0, o.x1, o.z1)
                for o in self.ss
                if o is not s and o.floor_min <= roof_y + 0.3 and o.floor_max >= roof_y - 0.05
            ]
            for r in rect_subtract(rect, holes):
                self.plan.add(s.cell, "roof", horizontal_quad(*r, lambda x, z, y=roof_y: y, True))

    def _visible_from_below(self, s: Surface) -> bool:
        for u in self.ss:
            if u is s or u.floor_max >= s.floor_min - STEP_HEIGHT:
                continue
            if min(s.x1, u.x1) - max(s.x0, u.x0) <= EPS or min(s.z1, u.z1) - max(s.z0, u.z0) <= EPS:
                continue
            if u.ceil_y > s.floor_min - UNDER_SLAB - 0.01:
                return True
        return False

    def _stair_is_solid(self, s: Surface) -> bool:
        """A stair rests on the floor beside it (or is short); else it floats on a stringer."""
        if abs(s.y_top - s.y) <= 1.0:
            return True
        side_axis = "z" if s.rise in ("+x", "-x") else "x"
        for o in self.ss:
            if o is s or abs(o.floor_min - s.y) > STEP_HEIGHT:
                continue
            e0, e1 = o.extent(side_axis)
            s0, s1 = s.extent(side_axis)
            if abs(e1 - s0) < EPS or abs(e0 - s1) < EPS:
                if connected(o, s):
                    return True
        return False

    @staticmethod
    def _step_count(s: Surface) -> int:
        return max(1, round(abs(s.y_top - s.y) / STEP_RISER))

    def _riser(self, s: Surface) -> float:
        """Visual riser height of a stair (0 for anything else)."""
        if s.kind != "stair" or not s.sloped:
            return 0.0
        return (s.y_top - s.y) / self._step_count(s)

    def _build_stair(self, s: Surface) -> None:
        rise = s.y_top - s.y
        along_x = s.rise in ("+x", "-x")
        length = (s.x1 - s.x0) if along_x else (s.z1 - s.z0)
        n = self._step_count(s)
        d, r = length / n, rise / n
        solid = self._stair_is_solid(s)
        forward = 1.0 if s.rise in ("+x", "+z") else -1.0
        start = (s.x0 if forward > 0 else s.x1) if along_x else (s.z0 if forward > 0 else s.z1)
        ax = "x" if along_x else "z"
        front = f"-{ax}" if forward > 0 else f"+{ax}"
        back = f"+{ax}" if forward > 0 else f"-{ax}"
        sides = {"+z", "-z"} if along_x else {"+x", "-x"}
        # Solid stairs show only treads and risers: their sides are drawn by the
        # wall pass (as risers seen from the floor beside them) or sit in walls.
        skip = ({"-y", back} | sides) if solid else set()
        prev_top = None
        for i in range(n):
            a, b = start + forward * i * d, start + forward * (i + 1) * d
            lo_t, hi_t = min(a, b), max(a, b)
            # Tread tops straddle the walkable ramp: it passes through each
            # tread's midpoint, so feet are never more than half a riser off.
            top = s.y + (i + 0.5) * r
            bot = s.y - 0.05 if solid else top - r - STRINGER_DEPTH
            if along_x:
                lo, hi = (lo_t, bot, s.z0), (hi_t, top, s.z1)
            else:
                lo, hi = (s.x0, bot, lo_t), (s.x1, top, hi_t)
            for side, poly in box_faces(lo, hi, skip=skip):
                if side == back and i < n - 1:
                    # Only the part below the next step's stringer shows.
                    poly = [(p[0], min(p[1], top - STRINGER_DEPTH), p[2]) for p in poly]
                elif side == front and prev_top is not None:
                    # The part below the previous tread is inside that step.
                    poly = [(p[0], max(p[1], prev_top), p[2]) for p in poly]
                self.plan.add(s.cell, "floor_stair", poly)
            prev_top = top

    def _rests_on_floor(self, p: Prop) -> bool:
        """True if a prop stands on a walkable floor (its underside is hidden)."""
        cx, cz = (p.min[0] + p.max[0]) / 2, (p.min[2] + p.max[2]) / 2
        return any(s.contains(cx, cz) and abs(s.height(cx, cz) - p.min[1]) < 0.05 for s in self.ss)

    def build_props(self) -> None:
        for p in self.props:
            # Hanging and wall-mounted props keep their underside.
            on_floor = self._rests_on_floor(p)
            if p.shape == "box":
                for _, poly in box_faces(p.min, p.max, skip={"-y"} if on_floor else ()):
                    self.plan.add(p.cell, p.material, poly)
            else:
                for poly in cylinder_faces(p.min, p.max, p.shape[-1], bottom=not on_floor):
                    self.plan.add(p.cell, p.material, poly)


def cylinder_faces(lo, hi, axis: str, segments: int = CYLINDER_SEGMENTS, bottom: bool = False) -> list[list]:
    """An elliptic cylinder inscribed in the box lo..hi along `axis` (x, y or z):
    side quads plus both caps (an upright cylinder resting on a floor, the
    default, skips its bottom cap)."""
    k = "xyz".index(axis)
    u, v = [i for i in range(3) if i != k]
    cu, cv = (lo[u] + hi[u]) / 2, (lo[v] + hi[v]) / 2
    ru, rv = (hi[u] - lo[u]) / 2, (hi[v] - lo[v]) / 2
    ring = []
    for i in range(segments):
        a = 2 * math.pi * (i + 0.5) / segments
        ring.append((cu + ru * math.cos(a), cv + rv * math.sin(a)))

    def pt(a, b, w):
        p = [0.0, 0.0, 0.0]
        p[u], p[v], p[k] = a, b, w
        return tuple(p)

    out = []
    for i in range(segments):
        (a0, b0), (a1, b1) = ring[i], ring[(i + 1) % segments]
        quad = [pt(a0, b0, lo[k]), pt(a1, b1, lo[k]), pt(a1, b1, hi[k]), pt(a0, b0, hi[k])]
        mid = ((a0 + a1) / 2 - cu, (b0 + b1) / 2 - cv)
        n = [0.0, 0.0, 0.0]
        n[u], n[v] = mid
        out.append(oriented(quad, n))
    for end, sign in ((lo[k], -1.0), (hi[k], 1.0)):
        if axis == "y" and sign < 0 and not bottom:
            continue  # resting on the floor
        n = [0.0, 0.0, 0.0]
        n[k] = sign
        out.append(oriented([pt(a, b, end) for a, b in ring], n))
    return out


def plan_layout(layout: Layout, props: list[Prop] | None = None, open_edges=(), holes=()) -> Plan:
    """The greybox geometry of a layout. `holes` (WallHole, castle kit M1)
    are cut from the wall faces they name; without them the plan is the
    M1.0 greybox exactly."""
    pl = Planner(layout, props, open_edges, holes)
    pl.build_walls()
    pl.build_railings()
    pl.build_shell()
    pl.build_horizontals()
    pl.build_props()
    return pl.plan


def _sat_overlap(a, b, tol: float = 1e-3) -> bool:
    """True if two convex 2D polygons overlap by more than `tol` (separating axes)."""
    for poly in (a, b):
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
            nx, ny = y1 - y2, x2 - x1
            ln = math.hypot(nx, ny)
            if ln < 1e-9:
                continue
            nx, ny = nx / ln, ny / ln
            pa = [x * nx + y * ny for x, y in a]
            pb = [x * nx + y * ny for x, y in b]
            if min(max(pa), max(pb)) - max(min(pa), min(pb)) <= tol:
                return False
    return True


def find_z_fighting(plan: Plan, limit: int = 20) -> list[str]:
    """Coplanar, same-facing, overlapping polygons (they would flicker).

    Only axis-aligned polygons are checked; sloped ones (ramps, railing caps on
    stairs) are rare and never share a plane by construction.
    """
    groups: dict[tuple, list[tuple[str, list]]] = {}
    for (cell, mat), polys in plan.polys.items():
        for poly in polys:
            n = newell_normal(poly)
            ln = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
            if ln < 1e-9:
                continue
            ax = max(range(3), key=lambda k: abs(n[k]))
            if abs(n[ax]) / ln < 0.9999:
                continue
            key = (ax, round(poly[0][ax], 3), n[ax] > 0)
            flat = [tuple(p[k] for k in range(3) if k != ax) for p in poly]
            groups.setdefault(key, []).append((f"{cell}.{mat}", flat))
    issues = []
    for key, items in groups.items():
        for i, (na, pa) in enumerate(items):
            for nb, pb in items[i + 1 :]:
                if _sat_overlap(pa, pb):
                    axis = "xyz"[key[0]]
                    issues.append(f"{na} and {nb} overlap on plane {axis}={key[1]}")
                    if len(issues) >= limit:
                        return issues
    return issues


def degenerate_polygons(plan: Plan) -> list[str]:
    out = []
    for (cell, mat), polys in plan.polys.items():
        for poly in polys:
            n = newell_normal(poly)
            if math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) < 1e-6:
                out.append(f"{cell}.{mat}: zero-area polygon {poly[:2]}")
    return out


def cell_levels(layout: Layout, props: list[Prop] | None = None) -> dict[str, int]:
    levels: dict[str, int] = {}
    for s in layout.surfaces:
        levels[s.cell] = min(levels.get(s.cell, s.level), s.level)
    for p in props or []:
        levels.setdefault(p.cell, 0)
    return levels


# ---------------------------------------------------------------------------
# Castle M5c (Facades): the outer-shell cut (docs/art/castle-kit.md "Exterior").
#
# A through hole (WallHole.through: a window) over a door of its own cell, on
# a wall whose mass is shared with an outdoor surface beyond (a courtyard:
# the hall's south wall and the courtyard's north wall, 2 m apart), is cut
# through the shared mass: from the owner's outer face above the outdoor
# surface's wall top, from the outdoor surface's own wall face below it and
# from that wall's top cap, and the hole's reveal across the mass (between
# the two faces) is closed by its jambs, its sill and, under the wall top, its
# head, in the owner's cell (`wall_exterior`: the outside stone). Only the
# Planner with holes (the kitted export's) cuts; without holes the plan is
# unchanged.

M5C_OUTDOOR_KINDS = ("courtyard", "grounds")  # castle_kit/grid.py OUTDOOR_KINDS


def _flat_rect(poly, axis: str):
    """(t0, y0, t1, y1, c, sign) of a flat vertical rectangle on a plane
    `axis = c`, or None."""
    k = 0 if axis == "x" else 2
    if len(poly) != 4 or any(abs(p[k] - poly[0][k]) > 1e-6 for p in poly):
        return None
    n = newell_normal(poly)
    size = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
    if size < 1e-9 or abs(abs(n[k]) / size - 1.0) > 1e-6:
        return None
    ts = [p[2] if axis == "x" else p[0] for p in poly]
    ys = [p[1] for p in poly]
    corners = {(round(t, 6), round(y, 6)) for t, y in zip(ts, ys)}
    if len(corners) != 4 or len({c[0] for c in corners}) != 2 or len({c[1] for c in corners}) != 2:
        return None
    return min(ts), min(ys), max(ts), max(ys), poly[0][k], (1.0 if n[k] > 0 else -1.0)


def _cut_vertical(polys, axis: str, c: float, sign: float, hole):
    """The polygons with the rectangle `hole` (t0, y0, t1, y1) cut from the
    flat rectangles on the plane `axis = c` facing `sign`; others unchanged."""
    out = []
    for poly in polys:
        r = _flat_rect(poly, axis)
        if r is None or abs(r[4] - c) > 1e-6 or r[5] != sign:
            out.append(poly)
            continue
        t0, y0, t1, y1 = r[:4]
        if min(t1, hole[2]) - max(t0, hole[0]) <= EPS or min(y1, hole[3]) - max(y0, hole[1]) <= EPS:
            out.append(poly)
            continue
        for a, lo, b, hi in rect_subtract((t0, y0, t1, y1), [hole]):
            q = vertical_quad(axis, c, a, b, (lo, lo), (hi, hi), sign)
            if q is not None:
                out.append(q)
    return out


def _cut_horizontal(polys, y: float, rect):
    """The polygons with the plan rectangle `rect` cut from the flat
    horizontal rectangles at height y facing up."""
    out = []
    for poly in polys:
        if len(poly) != 4 or any(abs(p[1] - y) > 1e-6 for p in poly) or newell_normal(poly)[1] <= 0:
            out.append(poly)
            continue
        xs, zs = [p[0] for p in poly], [p[2] for p in poly]
        box = (min(xs), min(zs), max(xs), max(zs))
        if min(box[2], rect[2]) - max(box[0], rect[0]) <= EPS or min(box[3], rect[3]) - max(box[1], rect[1]) <= EPS:
            out.append(poly)
            continue
        for r in rect_subtract(box, [rect]):
            out.append(horizontal_quad(*r, lambda x, z, y=y: y, True))
    return out


def outer_cut_m5c(pl: "Planner") -> None:
    """The outer-shell cut of every qualifying through hole (see above)."""
    if not pl.holes:
        return
    thickness = None
    for key in sorted(pl.holes):
        for h in pl.holes[key]:
            if not h.through:
                continue
            owner = next(s for s in pl.ss if s.id == h.surface)
            other = "z" if h.axis == "x" else "x"
            out_dir = -h.sign
            probe = WallPiece(owner, h.axis, h.c, h.sign, h.t0, h.t1, Lin(h.y0, h.y0), Lin(h.y1, h.y1))
            near = pl._near(probe, h.y0, h.y1)
            if not near or any(s.kind not in M5C_OUTDOOR_KINDS for _, s in near):
                continue
            # Over a door of the owner's cell crossing the same wall, below the hole.
            over = any(s.kind == "door" and s.cell == owner.cell and s.struct_top() <= h.y0 + EPS
                       and min(s.extent(other)[1], h.t1) - max(s.extent(other)[0], h.t0) > EPS
                       and abs((s.extent(h.axis)[0] if out_dir > 0 else s.extent(h.axis)[1]) - h.c) < EPS
                       for s in pl.ss)
            if not over:
                continue
            d_nb, nb = min(near, key=lambda ds: ds[0])
            n_top = max(s.struct_top() for _, s in near)
            if thickness is None:
                thickness = pl._run_thickness()
            # (The run's pieces share one outer-face offset: any piece the hole crosses gives it.)
            wall = next((pc for pc in pl.plan.pieces if pc.owner is owner and pc.axis == h.axis
                         and abs(pc.c - h.c) < EPS and pc.sign == h.sign and pc.tb > h.t0 + EPS and pc.ta < h.t1 - EPS),
                        None)
            if wall is None:
                continue
            plane_o = h.c + out_dir * thickness[id(wall)]
            plane_n = h.c + out_dir * d_nb
            top = min(h.y1, n_top)
            polys = pl.plan.polys
            # The owner's outer face above the outdoor wall's top.
            if h.y1 > n_top:
                key_o = (owner.cell, "wall_exterior")
                polys[key_o] = _cut_vertical(polys.get(key_o, []), h.axis, plane_o, out_dir,
                                             (h.t0, max(h.y0, n_top), h.t1, h.y1))
            # The outdoor surface's own wall face below its top, and its wall-top cap.
            key_n = (nb.cell, wall_material(nb))
            polys[key_n] = _cut_vertical(polys.get(key_n, []), h.axis, plane_n, out_dir, (h.t0, h.y0, h.t1, top))
            if h.y0 < n_top < h.y1:
                a, b = sorted((plane_o, plane_n))
                rect = (h.t0, a, h.t1, b) if h.axis == "z" else (a, h.t0, b, h.t1)
                key_c = (nb.cell, "wall_exterior")
                polys[key_c] = _cut_horizontal(polys.get(key_c, []), n_top, rect)
            # The reveal across the mass, in the owner's cell.
            a, b = sorted((plane_o, plane_n))
            for t, sgn in ((h.t0, 1.0), (h.t1, -1.0)):
                q = vertical_quad(other, t, a, b, (h.y0, h.y0), (top, top), sgn)
                pl.plan.add(owner.cell, "wall_exterior", q)
            rect = (h.t0, a, h.t1, b) if h.axis == "z" else (a, h.t0, b, h.t1)
            pl.plan.add(owner.cell, "wall_exterior", horizontal_quad(*rect, lambda x, z, y=h.y0: y, True))
            if h.y1 <= n_top:
                pl.plan.add(owner.cell, "wall_exterior", horizontal_quad(*rect, lambda x, z, y=h.y1: y, False))
