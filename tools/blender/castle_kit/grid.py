"""The castle kit's grid: the numbers every kit piece, rule and texture is
built to (after the scale pass), the bay
solver, wall runs and openings read from the layout, the piece naming rule,
face tags and the UV conventions (pure Python, no `bpy`).

In prose: docs/art/castle-kit.md. Everything here is shared by the
textures, the pieces and the rules; change it here first.

Coordinates are the layout's: metres, Y up, x east, z south. A wall run
lies on the line `axis = c` of one side of a layout surface:

    side  line        face normal   t (along the run)
    n     z = z0      +z            x
    s     z = z1      -z            x
    w     x = x0      +x            z
    e     x = x1      -x            z

`sign` is the face normal along the axis, as in `layout_lib.WallPiece`
(the surface is on that side of the line). Bay 0 is at the low-t end.
"""

from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass

_BLENDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BLENDER not in sys.path:
    sys.path.insert(0, _BLENDER)
import layout_lib as L  # noqa: E402

# -- grid and storeys ---------------------------------------------------------

PLAN_GRID = 0.5  # every authored plan coordinate is a multiple of this
STOREY = 9.0  # floor to floor: L-2 -18, L-1 -9, L0 0, L1 9 ... L7 63
CORRIDOR_CEILING = 8.4
SLAB = L.SLAB  # 0.6: corridor ceiling + slab = one storey
LEVELS = tuple(range(-2, 8))


def level_y(level: int) -> float:
    """Floor height of a level (L0 = 0)."""
    return level * STOREY


# -- walls --------------------------------------------------------------------

WALL_THICKNESS = 2.0  # inside, between rooms (the layout's door depth)
WALL_THICKNESS_MIN = 1.5
EXTERIOR_SHELL = L.EXTERIOR_THICKNESS_DEFAULT  # 0.8
WALL_MASS_MAX = L.WALL_MASS_MAX  # 3.2: closer rooms share one wall mass
REVEAL_MARGIN = 0.1  # a recess stops this far short of the wall mass behind it

# Stone courses and flagstones.
COURSE = 0.6  # courses 0.5-0.7 when a texture varies them; 15 per storey
COURSE_RANGE = (0.5, 0.7)
COURSES_PER_STOREY = 15
BLOCK_LENGTH = (0.9, 1.8)
FLAG_COURSES = (0.8, 1.2)
FLAG_SLAB = (1.0, 2.0)

# -- bays ---------------------------------------------------------------------

BAY_TARGETS = {
    "corridor": 6.0,  # castle kit M1: the test-bed corridors
    "eh_vault": 10.0,  # M2: the Entrance Hall's lierne vault bays (hero placements along z)
    "eh_wall": 5.0,  # M2: its wall shafts (the cell's bay_target; runs.<id>.bounds put them on the vault lines)
    "gh": 7.5,
    "gs_gallery": 6.0,
}
BAY_TOLERANCE = 0.15


# -- stairs -------------------------------------------------------------------

RISER = 0.17
TREAD_FLIGHT = 0.30  # Grand Staircase flights
TREAD_MARBLE = 0.34  # the Marble Staircase (26.6 degrees)
TREAD_RAMP_TOLERANCE = 0.02  # the walkable ramp passes within this of every tread's middle


# -- mouldings (height x projection from the wall face) -------------------------


@dataclass(frozen=True)
class Moulding:
    name: str
    height: float  # vertical size (a shaft's: its width)
    proud: float  # projection from the wall face into the room
    strip: str  # trim sheet strip (data/art/castle_textures.json)


PLINTH = Moulding("plinth", 0.6, 0.12, "plinth")  # bottom at the floor
STRING = Moulding("string", 0.3, 0.15, "string")  # top at a window sill
CORNICE = Moulding("cornice", 0.7, 0.35, "cornice")  # top at the ceiling
SHAFT = Moulding("shaft", 0.7, 0.25, "shaft")  # height = shaft width
MOULDINGS = {m.name: m for m in (PLINTH, STRING, CORNICE, SHAFT)}
MIN_RELIEF = 0.1  # the outline pass catches relief of at least this

# Shaft variants (castle kit M2): `plain` (kit_stone_shaft_<h>_plain, at most
# SHAFT_TRIS["plain"] triangles) is the one repeated along walls; the
# clustered shaft of M1 (kit_stone_shaft_<h>, three rolls, 392 triangles)
# stays for hero spots. A cell picks with castle_kit.json `shaft`.
SHAFT_VARIANTS = ("plain", "clustered", "none")  # M4: "none" = no bay shafts (the Grand Staircase's tower walls)
SHAFT_TRIS = {"plain": 200, "clustered": 400}
SHAFT_ON_WALL = 0.43  # a shaft needs wall this far either side of its centre line (half its width + 8 cm)
SPRINGING_MIN = 4.0  # a vaulted cell's springing line (cornice top), metres above its floor, at least


# -- clearances (sim_core::movement::MoveParams and the layout rules) ------------

CHARACTER_RADIUS = L.CHARACTER_RADIUS  # 0.3
INTRUSION_HEIGHT = 2.4  # below this above a walkable surface ...
INTRUSION_MAX = 0.25  # ... kit geometry enters it by at most this (radius - 5 cm)
OVERHEAD_CLEARANCE = L.OVERHEAD_CLEARANCE  # 2.4 under braziers, banners, lanterns
DOOR_CLEAR_WIDTH = 1.0  # through a door's whole depth
DOOR_CLEAR_HEIGHT = 2.4
COVERAGE_TOLERANCE = 0.05  # every Planner wall face of a kitted cell covered within this


def intrusion_ok(depth: float, height: float, walkthrough: bool = False) -> bool:
    """The intrusion rule: may geometry `depth` metres inside a walkable
    rectangle, at `height` metres above it, stay? (`walkthrough` pieces such
    as banners, candles and furniture may.)"""
    return walkthrough or height >= INTRUSION_HEIGHT or depth <= INTRUSION_MAX + 1e-9


# -- openings -----------------------------------------------------------------

ARCH_RISE = 0.55  # two-centred arches: apex this x span above the springing line
ARCH_SIZES = ((3.0, 4.5), (4.0, 5.0), (5.0, 6.0), (6.0, 9.0), (8.0, 7.0), (8.0, 10.0))
ARCH_REVEAL = 0.5  # a cell's arch surround reaches this far into a door it does not own
ARCH_CLEAR_INSET = 0.3  # clear height is measured this far inside each jamb
ARCH_SURROUND = 0.8  # an arch's quoins reach this far beyond each jamb on the wall face
OPENING_MARGIN = 0.6  # an opening keeps this from its bay's ends (half a shaft + 0.25)
OVER_DOOR_CLEAR = 0.6  # an opening over a door keeps one course above the door's apex (hood mould, keystone)

# Open door leaves (castle kit M2): a pair, each half the span, swung fully
# open. They lie in the reveal (flat against its jambs) when the reveal the
# cell builds is at least a leaf wide plus LEAF_REVEAL_CLEAR; otherwise flat
# against the cell's wall face beside the opening, beyond the arch's quoins
# (ARCH_SURROUND). Either way at most LEAF_PROUD from the face they lie on
# (the intrusion rule) and never across the passage.
LEAF_PROUD = INTRUSION_MAX
LEAF_REVEAL_CLEAR = 0.1
LEAF_CLEAR = 0.3  # a leaf on a wall face keeps this from other openings' surrounds, shafts and tells


def leaf_width(span: float) -> float:
    """Width of one leaf of a door `span` wide (always a pair)."""
    return span / 2.0


@dataclass(frozen=True)
class OpeningSpec:
    """A wall opening piece: `kind` window | niche | blind_door, size, and
    how deep it goes into the wall (None: through the wall)."""

    piece: str
    kind: str
    width: float
    height: float
    depth: float | None
    lights: int = 0


OPENING_PIECES = {
    s.piece: s
    for s in (
        OpeningSpec("kit_window_lancet_1.6x5", "window", 1.6, 5.0, None, 1),
        OpeningSpec("kit_window_lancet_2.6x7", "window", 2.6, 7.0, None, 2),
        OpeningSpec("kit_window_lancet_3.2x10", "window", 3.2, 10.0, None, 2),
        OpeningSpec("kit_window_tracery_8x8", "window", 8.0, 8.0, None, 4),  # M2: over the front doors
        OpeningSpec("kit_niche_2x3.5", "niche", 2.0, 3.5, 0.7),
        OpeningSpec("kit_door_blind_3x4.5", "blind_door", 3.0, 4.5, 0.3),
        # Castle kit M3 (the Great Hall): the end window over the
        # dais (6 lights x 4 tiers, through the wall) and the hooded fireplace
        # (a firebox recess from the floor; its hood stands proud above it).
        OpeningSpec("kit_window_end_9x12", "window", 9.0, 12.0, None, 6),
        OpeningSpec("kit_fireplace_hooded_4.4x3.6", "fireplace", 4.4, 3.6, 0.7),
        # Castle M5a (docs/art/castle-kit.md): the Charms class door
        # (a closed oak door with a plaque in a 0.3 m recess, standing on the
        # floor like a blind door), the tower's slit window (one light, through
        # the wall) and the Long Gallery's rose window (through the east end
        # wall, one 10 m bay).
        OpeningSpec("kit_door_class_3x4.5", "blind_door", 3.0, 4.5, 0.3),
        OpeningSpec("kit_window_slit_0.6x2.4", "window", 0.6, 2.4, None, 1),
        OpeningSpec("kit_window_rose_6x6", "window", 6.0, 6.0, None, 1),
    )
}
OPENING_KINDS = ("window", "niche", "blind_door", "fireplace")
FLOOR_OPENINGS = ("blind_door", "fireplace")  # stand on the floor (sill 0)


def arch_springing(span: float, apex: float) -> float:
    """Springing line of a two-centred arch `span` wide, apex at `apex`
    (both relative to the door's floor)."""
    return apex - ARCH_RISE * span


def arch_height(span: float, apex: float, x: float) -> float:
    """Height of the arch's intrados at `x` from the opening's centre line
    (|x| <= span / 2); the springing height at and beyond the jambs."""
    half = span / 2.0
    rise = ARCH_RISE * span
    s = apex - rise
    # Each arc's centre lies on the springing line, on the far side of the
    # centre line, at cx; its radius reaches the near jamb.
    cx = (rise * rise - half * half) / span
    r = cx + half
    ax = abs(x)
    if ax >= half:
        return s
    d = ax + cx
    return s + math.sqrt(max(0.0, r * r - d * d))


def arch_clear_height(span: float, apex: float, inset: float = ARCH_CLEAR_INSET) -> float:
    """Clear height `inset` inside each jamb (4.5 m for a 5 x 6 arch)."""
    return arch_height(span, apex, span / 2.0 - inset)


# -- texel densities and tiles (docs/art/castle-kit.md) --------------------------

STONE_TILE_M = 4.8  # one 1024 px tile: 8 courses of 0.6 m
CEILING_TILE_M = 2.4  # vault_web, 512 px
STONE_PX_PER_M = 1024 / STONE_TILE_M  # 213.3
TRIM_PX_PER_M = (400.0, 600.0)  # the trim sheet's strips stay in this band (balustrade LOD1 strip excepted)


# -- budgets (Medium) -----------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    draws: int  # meshes the cell draws at LOD0 (the collider is not drawn)
    tris: tuple[int, int]  # target triangles at LOD0 (low, high); high is the cap
    drawn: int | None = None  # cap on triangles drawn with LOD1 beyond one storey
    cells: int = 1  # M4: the budget covers this many cells together (their sums are checked)


BUDGETS = {
    # Castle M5a: the corridor cap 30 k -> 35 k for the 54-60 m Long Gallery and
    # Undercroft Walk (per-cell guidelines: M5A_TRIS below).
    "corridor": Budget(8, (10_000, 35_000)),
    "entrance_hall": Budget(11, (80_000, 120_000)),
    # Castle D1: the Great Hall 12 -> 13 (banners per gable, M5a), the
    # Grand Staircase 20 -> 22 (gallery-edge trim band groups, M5a); Low's draws do not rise.
    # + 4 hourglass sand nodes and 2 for the one house's cup banners
    # shown at a time (the lint counts one house's cup banner nodes).
    "great_hall": Budget(19, (130_000, 190_000)),
    "grand_staircase": Budget(22, (250_000, 320_000), 180_000, 2),  # both cells together
    "common_room": Budget(12, (40_000, 70_000)),
    # Castle M5a: the west tower's spiral stair (12 x 12 m, L3-L6, 34 m shaft).
    "tower": Budget(8, (25_000, 45_000)),
}
COLLIDER_TRIS = 6_000  # a cell's camera collider (2-6 k)

# Frame budgets (checked in the browser with the perf probe, tools/loadtest/perf_paths.json).
FRAME_WORLD_DRAWS = 50  # world draw calls in view
FRAME_TIME_GROWTH = 0.15  # frame time with 51 characters in the halls, against the previous milestone's build
GPU_MEMORY_GROWTH_MB = 64  # GPU memory at Medium, against the previous milestone's build


# -- cell visibility (castle kit M2: manifest `sees`) ------------------------------------

SEES_DOORS = 2  # a cell sees the cells joined to it through up to this many doors or open joins
SEES_FACADE = WALL_MASS_MAX  # outdoor cells in the set bring the cells whose bounds come this close (their facades)
OUTDOOR_KINDS = ("courtyard", "grounds")  # a cell with such a surface sees everything (no `sees` written)


# -- glow (castle kit M2: lanterns, lamps, braziers, candles) ----------------------------

# The castle_props emissive image is drawn at this level (castle_textures.json
# emissive_strength; the client maps strength 1.0 to texture level 1.0).
GLOW_LEVEL = 1.0
# Glow colours (sRGB) the props atlas paints for flames and lantern glass:
# the warm band, so the bloom's bright pass (threshold 0.95 on the brightest
# channel) picks them up by their red while green and blue stay low enough
# that bloom and the lit share never push them to white.
GLOW_LANTERN = "#FFA94D"  # lanterns, lamp newels, braziers
GLOW_CANDLE = "#FFAD5C"  # candles
GLOW_BAND = {"r": (0.95, 1.0), "g": (0.55, 0.75), "b": (0.18, 0.45)}  # a glow texel on screen before bloom
GLOW_AFTER_BLOOM = {"g": 0.95, "b": 0.60}  # its core after bloom stays below these (warm, never white)
GLOW_BASE_MAX = 0.08  # a glow texel's base (lit) colour is at most this share of its glow colour


# -- camera ---------------------------------------------------------------------

CAMERA_MAX_DEFAULT = 16.0  # client/src/game/camera.ts MAX_DISTANCE
CAMERA_MAX_LOW_CAP = 18.0  # Low quality never zooms out further


# -- piece names ----------------------------------------------------------------

PIECE_RE = re.compile(r"^kit_[a-z][a-z0-9]*(_[a-z][a-z0-9]*)*(_\d+(\.\d+)?(x\d+(\.\d+)?)?)?(_[a-z][a-z0-9]*)?$")


def fmt_m(v: float) -> str:
    """A size in a piece name: metres, no trailing zeros (5, 1.6, 8.4)."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s != "-0" else "0"


def piece_name(family: str, piece: str | None = None, w: float | None = None, h: float | None = None,
               variant: str | None = None) -> str:
    """`kit_<family>[_<piece>][_<w>x<h> | _<w>][_<variant>]`: lowercase,
    neutral (no IP), sizes in metres. A single size is a shaft's height, a
    corbel's size or a threshold's width; a family alone names a piece
    with one form (`kit_niche_2x3.5`, `kit_threshold_5`)."""
    parts = ["kit", family] + ([piece] if piece else [])
    if w is not None and h is not None:
        parts.append(f"{fmt_m(w)}x{fmt_m(h)}")
    elif w is not None or h is not None:
        parts.append(fmt_m(w if w is not None else h))
    if variant:
        parts.append(variant)
    name = "_".join(parts)
    if not PIECE_RE.match(name):
        raise ValueError(f"not a valid piece name: {name!r}")
    return name


def arch_piece(span: float, apex: float) -> str:
    return piece_name("stone", "arch", span, apex)


def shaft_piece(height: float, variant: str = "plain") -> str:
    """`kit_stone_shaft_<h>_plain` (repeated wall shafts) or the clustered
    `kit_stone_shaft_<h>` (hero spots)."""
    if variant not in SHAFT_VARIANTS or variant == "none":
        raise ValueError(f"shaft variant must be plain or clustered, not {variant!r}")
    return piece_name("stone", "shaft", h=height, variant="plain" if variant == "plain" else None)


# -- face tags --------------------------------------------------------------------

UV_MODES = ("world", "trim", "fit", "kit")
# kit:<tile>[:#RRGGBB | :@<palette name>]: castle kit pieces name their colours
# (`@`, data/art/castle_textures.json `palette`, castle kit M3); the hex form
# stays for the props.json kit models assemble.py maps onto the atlas.
# Castle M5c: a material may end in `@ext` (its exterior clone).
FACE_TAG_RE = re.compile(
    r"^(castle_[a-z0-9_]+(?:@ext)?)\|(world|trim:[a-z0-9_]+|fit:[a-z0-9_]+|kit:[a-z]+(:#[0-9A-F]{6}|:@[a-z][a-z0-9_]*)?)$")


@dataclass(frozen=True)
class FaceTag:
    material: str  # a castle_* name or a family (castle_wall, castle_floor, castle_portraits)
    mode: str  # world | trim | fit | kit
    arg: str | None  # strip, tile, or "tile" / "tile:#RRGGBB" / "tile:@name" for kit


def face_tag(material: str, uv_mode: str) -> str:
    tag = f"{material}|{uv_mode}"
    parse_face_tag(tag)
    return tag


def parse_face_tag(tag: str) -> FaceTag:
    """`<material>|<uv_mode>`, uv_mode world | trim:<strip> | fit:<tile> |
    kit:<tile>[:#RRGGBB] (docs/art/castle-kit.md, "Face tags and UVs")."""
    m = FACE_TAG_RE.match(tag)
    if not m:
        raise ValueError(f"bad face tag {tag!r}")
    mode, _, arg = m.group(2).partition(":")
    return FaceTag(m.group(1), mode, arg or None)


# -- UV conventions (Blender UVs: origin bottom-left, v up) -------------------------


def world_uv(p, normal, tile_m: float) -> tuple[float, float]:
    """World-space planar UVs: projected along the normal's dominant axis
    (ties: y, then x, then z), u to the right as seen from the front, v up
    on walls; floors u = x, v = -z; ceilings u = x, v = z. Anchored at the
    world origin so courses continue across faces, pieces and cells: v = 0
    is a course line at y = 0, and floors 9 m apart keep the courses."""
    x, y, z = p
    nx, ny, nz = normal
    ax, ay, az = abs(nx), abs(ny), abs(nz)
    if ay >= ax and ay >= az:
        return (x / tile_m, (-z if ny > 0 else z) / tile_m)
    if ax >= az:
        return ((-z if nx > 0 else z) / tile_m, y / tile_m)
    return ((x if nz > 0 else -x) / tile_m, y / tile_m)


def _rows_v(px_h: int, row: float) -> float:
    return 1.0 - row / px_h


def strip_uv(sheet: dict, strip: str, s: float, p: float) -> tuple[float, float]:
    """UV on a sheet strip: `s` metres along the run, `p` metres along the
    moulding's profile from its bottom (0..metres, clamped). Full-width
    strips repeat every u_m (the sampler wraps u); a strip with `cols`
    cannot wrap, so `s` is face-local there (0..u_m, clamped) and longer
    faces are split at every u_m."""
    st = sheet["strips"][strip]
    w, h = sheet["px"]
    pad = sheet["pad"]
    top, bottom = st["rows"]
    c0, c1 = st.get("cols", [0, w])
    q = min(max(p / st["metres"], 0.0), 1.0)
    row = (bottom - pad) - q * (bottom - top - 2 * pad)
    if c0 == 0 and c1 == w:
        u = s / st["u_m"]
    else:
        f = min(max(s / st["u_m"], 0.0), 1.0)
        u = (c0 + pad + f * (c1 - c0 - 2 * pad)) / w
    return (u, _rows_v(h, row))


def tile_uv(sheet: dict, tile: str, a: float, b: float) -> tuple[float, float]:
    """UV of face-local (a, b) in 0..1 (a to the right, b up) inside a tile."""
    x, y, tw, th = sheet["tiles"][tile]
    w, h = sheet["px"]
    pad = sheet["pad"]
    a = min(max(a, 0.0), 1.0)
    b = min(max(b, 0.0), 1.0)
    u = (x + pad + a * (tw - 2 * pad)) / w
    row = (y + th - pad) - b * (th - 2 * pad)
    return (u, _rows_v(h, row))


# -- bay solver ----------------------------------------------------------------------


@dataclass(frozen=True)
class Bays:
    n: int
    width: float  # the bays' width (explicit bounds: their mean)
    deviation: float  # width / target - 1
    within: bool  # |deviation| <= tolerance (forced counts report it too; explicit bounds are always within)
    edges: tuple[float, ...] = ()  # explicit boundaries t0 .. t1 (castle_kit.json runs.<id>.bounds), else ()

    def bounds(self, t0: float) -> list[float]:
        """The n + 1 bay boundaries from `t0`; shafts stand on the inner ones."""
        if self.edges:
            return list(self.edges)
        return [t0 + self.width * i for i in range(self.n + 1)]


def solve_bays(length: float, target: float, tol: float = BAY_TOLERANCE, count: int | None = None) -> Bays:
    """Equal bays along a run: the count whose width is closest to `target`
    (ties: fewer, wider bays), or `count` when given. A run shorter than a
    bay is one short bay."""
    if length <= 0 or target <= 0:
        raise ValueError("bay solver needs a positive length and target")
    if count is not None:
        if count < 1:
            raise ValueError("a run has at least one bay")
        n = count
    else:
        lo = max(1, math.floor(length / target))
        n = min((lo, lo + 1), key=lambda k: (abs(length / k - target), k))
    width = length / n
    dev = width / target - 1.0
    return Bays(n, width, dev, abs(dev) <= tol + 1e-9)


def fixed_bays(t0: float, t1: float, inner, target: float) -> Bays:
    """Bays with explicit inner boundaries `inner` (sorted, strictly inside
    t0..t1): unequal bays, e.g. shafts on a vault's bay lines."""
    edges = (float(t0), *(float(t) for t in inner), float(t1))
    if any(b <= a for a, b in zip(edges, edges[1:])):
        raise ValueError("bay bounds must increase strictly inside the run")
    n = len(edges) - 1
    width = (t1 - t0) / n
    return Bays(n, width, width / target - 1.0, True, edges)


# -- wall runs from the layout ---------------------------------------------------------

SIDES = ("n", "s", "w", "e")


@dataclass(frozen=True)
class Contact:
    """Another surface joining `surface` across one of its edges (an opening)."""

    other: str
    side: str
    t0: float
    t1: float
    is_door: bool


@dataclass(frozen=True)
class WallRun:
    id: str  # "<surface>:<side><k>"
    surface: str
    cell: str
    side: str
    k: int
    axis: str
    c: float
    sign: float
    t0: float
    t1: float
    floor: float  # y of the surface (flat surfaces only)
    top: float  # the wall top (ceiling, or the open wall height)

    @property
    def length(self) -> float:
        return self.t1 - self.t0


def side_line(s: L.Surface, side: str) -> tuple[str, float, float, tuple[float, float]]:
    """(axis, c, sign, (t0, t1)) of one side of a surface."""
    if side == "n":
        return "z", s.z0, 1.0, (s.x0, s.x1)
    if side == "s":
        return "z", s.z1, -1.0, (s.x0, s.x1)
    if side == "w":
        return "x", s.x0, 1.0, (s.z0, s.z1)
    if side == "e":
        return "x", s.x1, -1.0, (s.z0, s.z1)
    raise ValueError(f"unknown side {side!r}")


def contacts(layout: L.Layout, s: L.Surface) -> list[Contact]:
    """Walkable joins across the edges of `s` (sim-core's rule: an edge
    contact with heights within the step height), in side and t order."""
    out = []
    for o in layout.surfaces:
        if o.id == s.id or not L.connected(s, o):
            continue
        c = L.contact(s, o)
        x0, z0, x1, z1 = c
        if x1 - x0 > L.EPS and z1 - z0 > L.EPS:
            continue  # an overlap, not an edge
        for side in SIDES:
            axis, line, _, _ = side_line(s, side)
            if axis == "z" and abs(z0 - line) <= L.EPS and abs(z1 - line) <= L.EPS and x1 - x0 > L.EPS:
                out.append(Contact(o.id, side, x0, x1, o.kind == "door"))
            elif axis == "x" and abs(x0 - line) <= L.EPS and abs(x1 - line) <= L.EPS and z1 - z0 > L.EPS:
                out.append(Contact(o.id, side, z0, z1, o.kind == "door"))
    out.sort(key=lambda c: (SIDES.index(c.side), c.t0, c.other))
    return out


def wall_top(s: L.Surface) -> float:
    if s.roofed:
        return s.ceil_y
    return s.floor_max + (s.wall_height() or 0.0)


def wall_runs(layout: L.Layout, s: L.Surface) -> list[WallRun]:
    """The wall runs of a flat, non-door surface: each side split at its
    contacts (doors, joined floors), in side order n, s, w, e and t order."""
    if s.kind == "door" or s.sloped:
        return []
    joins = contacts(layout, s)
    runs = []
    for side in SIDES:
        axis, c, sign, (a, b) = side_line(s, side)
        cuts = sorted((j.t0, j.t1) for j in joins if j.side == side)
        spans, t = [], a
        for c0, c1 in cuts:
            if c0 - t > L.EPS:
                spans.append((t, c0))
            t = max(t, c1)
        if b - t > L.EPS:
            spans.append((t, b))
        for k, (t0, t1) in enumerate(spans):
            runs.append(WallRun(f"{s.id}:{side}{k}", s.id, s.cell, side, k, axis, c, sign, t0, t1, s.y, wall_top(s)))
    return runs


def face_pieces(pieces, surface: str | None, axis: str, c: float, sign: float, cell: str | None = None) -> list:
    """The Planner's wall pieces (`layout_lib.WallPiece`) on the face
    `axis = c` facing `sign`, owned by `surface` (or by any surface of
    `cell` when `surface` is None)."""
    out = []
    for pc in pieces:
        if pc.axis != axis or abs(pc.c - c) > 1e-6 or pc.sign != sign:
            continue
        if surface is not None and pc.owner.id != surface:
            continue
        if cell is not None and pc.owner.cell != cell:
            continue
        out.append(pc)
    return out


def _merge(spans) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def walled_spans(pieces, run: WallRun, at: str = "floor") -> list[tuple[float, float]]:
    """Spans of a run where the Planner draws its wall (the rule rules.py's
    `Context.walled` uses, castle kit M1): from the floor (`at` "floor"), up
    to the wall top ("top"), or both ("full"). A run along a drop (a railing
    edge) or open to a lower ceiling has none. `pieces`: the Planner's wall
    pieces (`Planner.plan.pieces` after `build_walls`)."""
    spans = []
    for pc in face_pieces(pieces, run.surface, run.axis, run.c, run.sign):
        if at in ("floor", "full") and abs(pc.lo.mid - run.floor) > 0.05:
            continue
        if at in ("top", "full") and abs(pc.hi.mid - run.top) > 0.05:
            continue
        a, b = max(pc.ta, run.t0), min(pc.tb, run.t1)
        if b > a + 1e-6:
            spans.append((a, b))
    return _merge(spans)


def face_covered(pieces, surface: str | None, axis: str, c: float, sign: float, t0: float, t1: float, y0: float,
                 y1: float, cell: str | None = None) -> bool:
    """Does the Planner draw flat wall over the whole rectangle t0..t1 x
    y0..y1 of this face? (Openings and open leaves need wall behind them.)"""
    spans = []
    for pc in face_pieces(pieces, surface, axis, c, sign, cell):
        if abs(pc.lo.a - pc.lo.b) > 1e-6 or abs(pc.hi.a - pc.hi.b) > 1e-6:
            continue  # sloped (beside a stair): no openings
        if pc.lo.mid > y0 + 0.05 or pc.hi.mid < y1 - 0.05:
            continue
        spans.append((pc.ta, pc.tb))
    return any(a <= t0 + 1e-6 and t1 <= b + 1e-6 for a, b in _merge(spans))


def planner_pieces(layout: L.Layout) -> list:
    """Every wall piece the Planner draws for this layout (no holes, no props)."""
    pl = L.Planner(layout, [])
    pl.build_walls()
    return pl.plan.pieces


def run_point(run: WallRun, t: float, y: float, depth: float = 0.0) -> tuple[float, float, float]:
    """World point on (depth > 0: behind) the run's wall face."""
    off = run.c - run.sign * depth
    return (t, y, off) if run.axis == "z" else (off, y, t)


@dataclass(frozen=True)
class WallMass:
    thickness: float  # how deep this run's own wall mass goes (Planner shell offset)
    behind: tuple[str, ...]  # rooms within one wall mass behind the given span
    outside: bool  # nothing roofed behind: windows may open here


def wall_mass(layout: L.Layout, run: WallRun, ta: float, tb: float, y0: float, y1: float) -> WallMass:
    """What lies behind the span [ta, tb] x [y0, y1] of a run's face, the
    way the Planner's exterior shell sees it (`Planner._near`): surfaces
    beyond the line within WALL_MASS_MAX whose height range meets the span.
    The thickness is the shell's: the exterior default, or half the gap to
    the nearest such room over the whole side (rooms that close share one
    wall mass). Courtyards and grounds count as outside."""
    s = layout.by_id(run.surface)
    _, _, _, (sa, sb) = side_line(s, run.side)
    other = "x" if run.axis == "z" else "z"
    thick = L.EXTERIOR_THICKNESS.get(s.kind, L.EXTERIOR_THICKNESS_DEFAULT)
    behind = []
    side_top = run.top + SLAB
    for o in layout.surfaces:
        if o.id == s.id:
            continue
        e0, e1 = o.extent(run.axis)
        d = (run.c - e1) if run.sign > 0 else (e0 - run.c)
        if d <= L.EPS or d > WALL_MASS_MAX:
            continue
        o0, o1 = o.extent(other)
        if o.floor_min - SLAB >= side_top or o.struct_top() <= run.floor:
            continue
        if o0 < sb - L.EPS and o1 > sa + L.EPS:
            thick = min(thick, d / 2.0)
        if o0 < tb - L.EPS and o1 > ta + L.EPS and not (o.floor_min - SLAB >= y1 or o.struct_top() <= y0):
            if o.kind not in ("courtyard", "grounds"):
                behind.append(o.id)
    return WallMass(thick, tuple(sorted(behind)), not behind)


# -- props near walls --------------------------------------------------------------------

# Kit models that sit flat on a wall face (find-spot tells): sweeps below
# INTRUSION_HEIGHT break around them, TELL_GAP beyond their footprint.
WALL_TELLS = ("tell_loose_stone", "tell_tapestry_corner")
TELL_HALF_WIDTH = 0.3
TELL_GAP = 0.15
WALL_TOUCH = 0.1  # a kit entry this close to a wall face is on it


# =====================================================================================
# Castle kit M3 + M4: the Great Hall and the Grand Staircase (docs/art/castle-kit.md).
# Everything below is contract: the pieces and lints read it; change it
# here first.
# =====================================================================================

# -- bay lines (castle_kit.json `lines`) ---------------------------------------------
#
# A cell whose walls along one axis share one rhythm across several wall runs
# (the Great Hall's north and south walls are four runs each, split where the
# layout tiles the floor round the dueling stage and the dais steps): `at`
# lists the inner lines, the bays are the spans between the cell's ends and
# them. A shaft (`kit_stone_shaft_<h>_<variant>`) stands on every line where
# the Planner draws wall on that face from `foot` above the local floor to
# `top` above the cell's lowest floor, with a corbel (`corbel`) on its top; run
# bays on those faces follow the lines (no run shafts, no rhythm warnings).
LINE_AXES = ("x", "z")
LINE_SIDES = {"x": ("n", "s"), "z": ("w", "e")}  # the sides whose runs run along the axis

WAINSCOT_MAX = 4.0  # castle_kit.json `wainscot`: an oak wainscot this high at most (human scale)
WAINSCOT_PROUD = 0.12  # its panels and cap stand at most this far off the wall
WAINSCOT_CLEAR = 0.3  # a window's sill stays this far above the wainscot's top


def face_walled(pieces, axis: str, c: float, sign: float, t0: float, t1: float, y0: float, y1: float) -> bool:
    """Does the Planner draw wall (any owner, sloped bottoms allowed below
    `y0`) over the whole rectangle t0..t1 x y0..y1 of the face `axis = c`
    facing `sign`? Line shafts, portrait clusters and spot lanterns stand
    on such faces whichever surface owns the wall (the Grand Staircase's
    tower walls belong to its ground floor, every gallery hangs on them)."""
    spans = []
    for pc in face_pieces(pieces, None, axis, c, sign):
        if max(pc.lo.a, pc.lo.b) > y0 + 0.05 or min(pc.hi.a, pc.hi.b) < y1 - 0.05:
            continue
        spans.append((pc.ta, pc.tb))
    return any(a <= t0 + 1e-6 and t1 <= b + 1e-6 for a, b in _merge(spans))


# -- the Great Hall (the scale pass's 66 x 22 m at x 34..100, z 9..31) -------

GH_BAY = 7.5  # the truss rhythm: lines at x = 34 + 7.5 k (k 1..8), the dais bay 94..100
GH_CORBEL = 12.0  # the trusses spring from corbels this high (castle_kit.json lines.top)
GH_PLATE = 17.5  # the wall plate: the cornice's top, the sky panels' foot (springing)
GH_RIDGE = 28.5  # the trusses' apex and the sky's ridge (layout ceiling 29)
GH_WINDOW_SILL = 6.6  # the side lancets (3.2 x 10, 11 courses up)
FIREPLACE_HOOD = (3.6, 9.0)  # the hood stands over the firebox from its lintel to this height
FIREPLACE_HOOD_PROUD = 1.2  # and at most this far off the wall (all of it above 2.4 m)
FIREPLACE_HOOD_WIDTH = 5.0  # at most this wide (the 4.4 m firebox plus its jambs)
KEEP_CLEAR_AREAS = ("dueling_club",)  # layout area kinds no kit stands in below 2.4 m (castle_kit.json keep_clear)

# -- the enchanted ceiling (castle_kit.json `sky`, material castle_sky) ------------------
#
# Two pitches per line bay from the wall plate (`plate`, on the side walls'
# face lines) to the ridge (`ridge`, over the cell's middle), one quad each,
# unlit, tinted by the hour on the client ("castle_sky").
# UVs: u = (along - start) / length, continuous over the whole hall; v = 0 at
# the plate, 1 at the ridge, on both pitches.
SKY_CEILING_CLEAR = 0.3  # the ridge stays this far under the layout ceiling
SKY_OPENING_CLEAR = 0.3  # side-wall openings stay this far under the plate

# -- floating candles (castle_kit.json `candles`) -----------------------------------------

CANDLE_GROUPS_MAX = 4  # moving groups per cell: each is one node the client bobs (4 matrix updates a frame)
CANDLE_COUNT = (1, 200)  # candles per group
CANDLE_HEIGHT = 0.5  # a candle with its flame (the overhead rule counts it below the group's y0); review M3+M4: 0.35 -> 0.5 so the candles read from the floor of a 66 m hall
CANDLE_BOB_AMP = 0.25  # metres, at most
CANDLE_BOB_PERIOD = (2.0, 12.0)  # seconds
CANDLE_LOD = (20.0, 80.0)  # allowed `lod1` distances (to the group's bounding box)
LOD_CANDLES = 40.0  # candles beyond 40 m draw their LOD1 (flames only)
CANDLE_TRIS = 40  # LOD0 per candle (wax, wick, flame)
CANDLE_TRIS_LOD1 = 6  # LOD1 per candle (the wax as a three-sided stick; the flame is the client's animated fire)

# -- portraits (castle_kit.json `portraits`; castle_portraits_a / _b) ----------------------
#
# Each sheet is PORTRAIT_SHEET_PX square: an 8 x 8 grid of 256 px cells, 64
# canvases a sheet, 128 over the two. A cell keeps PORTRAIT_PAD px free at its
# edge, then a PORTRAIT_RING px frame ring (the frame's moulding, painted by
# castle_kit/portraits.py), then the PORTRAIT_PAINT px painting. Frames are
# geometry on the ring band; the canvas face shows a centred crop of the
# painting with the frame's inner aspect (canvas_paint_uv). Index i is at
# column i % 8, row i // 8 from the top. Kinds by index (the painter paints to
# them; the cluster generator picks by the frame's aspect): figures for tall
# frames, landscapes for wide ones, still lifes for squarish ones; the last
# canvas of sheet b is the guardian portrait's (the L7 common-room door).
PORTRAIT_SHEET_PX = 2048
PORTRAIT_CELL = 256
PORTRAIT_COLS = PORTRAIT_SHEET_PX // PORTRAIT_CELL  # 8
PORTRAIT_CANVASES = PORTRAIT_COLS * PORTRAIT_COLS  # 64 per sheet
PORTRAIT_PAD = 4
PORTRAIT_RING = 20
PORTRAIT_PAINT = PORTRAIT_CELL - 2 * (PORTRAIT_PAD + PORTRAIT_RING)  # 208
PORTRAIT_KINDS = {"figure": (0, 32), "landscape": (32, 48), "still_life": (48, 64)}  # [first, end)
GUARDIAN_CANVAS = ("castle_portraits_b", 63)  # replaces sheet b's last still life; clusters never pick it
PORTRAIT_SHAPES = ("rect", "arch", "oval")
# The gallery mix: (frame w, h in metres, shape, weight). Human scale.
PORTRAIT_FRAMES = (
    (0.8, 1.0, "rect", 2.0),
    (1.0, 1.2, "rect", 4.0),
    (1.0, 1.2, "oval", 2.0),
    (1.2, 1.6, "rect", 5.0),
    (1.2, 1.6, "arch", 2.0),
    (1.6, 1.2, "rect", 3.0),
    (1.6, 2.0, "rect", 6.0),
    (2.0, 1.4, "rect", 2.0),
    (2.0, 2.4, "rect", 5.0),
    (2.4, 3.2, "rect", 4.0),
    (3.0, 4.0, "rect", 1.5),
)
PORTRAIT_MIXES = {"gallery": PORTRAIT_FRAMES}
PORTRAIT_GAP = 0.15  # at least this between two frames
PORTRAIT_EDGE = 0.3  # frames stay this far inside their cluster's rectangle
PORTRAIT_KEEP_OUT = 0.3  # and this far from openings (arches with their surround and hood), lanterns, tells, heroes
PORTRAIT_FILL = (0.65, 0.85)  # share of a cluster's rectangle the frames cover (castle M5a: PORTRAIT_FILL_M5A, was 0.45-0.7)
PORTRAIT_BORDER = (0.06, 0.16)  # frame border width (m): thin on small frames, wide on big ones
PORTRAIT_PROUD = 0.12  # a frame stands at most this far off the wall
PORTRAIT_Y = (0.9, 7.5)  # a gallery's cluster band above its floor (four tiers; the next gallery's soffit above)
PORTRAIT_TRIS = 40  # LOD0 per frame with its canvas (at most)
PORTRAIT_TRIS_LOD1 = 2  # LOD1: one quad showing the whole cell (frame ring and painting)


def canvas_cell(index: int) -> tuple[int, int]:
    """Top-left pixel (x, y from the top) of canvas `index` on its sheet."""
    if not 0 <= index < PORTRAIT_CANVASES:
        raise ValueError(f"canvas index {index} outside 0..{PORTRAIT_CANVASES - 1}")
    return (index % PORTRAIT_COLS) * PORTRAIT_CELL, (index // PORTRAIT_COLS) * PORTRAIT_CELL


def _sheet_uv(x: float, y_top: float) -> tuple[float, float]:
    return x / PORTRAIT_SHEET_PX, 1.0 - y_top / PORTRAIT_SHEET_PX


def canvas_paint_uv(index: int, a: float, b: float, aspect: float = 1.0) -> tuple[float, float]:
    """UV (Blender convention) of (a, b) in 0..1 across a canvas face whose
    width / height is `aspect`: a centred crop of the painting (full width
    for wide frames, full height for tall ones)."""
    x0, y0 = canvas_cell(index)
    inset = PORTRAIT_PAD + PORTRAIT_RING
    w = h = float(PORTRAIT_PAINT)
    if aspect >= 1.0:
        h = w / aspect
    else:
        w = h * aspect
    cx, cy = x0 + inset + PORTRAIT_PAINT / 2.0, y0 + inset + PORTRAIT_PAINT / 2.0
    a = min(max(a, 0.0), 1.0)
    b = min(max(b, 0.0), 1.0)
    return _sheet_uv(cx - w / 2.0 + a * w, cy + h / 2.0 - b * h)


def canvas_ring_uv(index: int, side: str, s: float, p: float) -> tuple[float, float]:
    """UV on canvas `index`'s frame ring: `side` n (top), s (bottom), w
    (left), e (right) as seen from the front; `s` 0..1 along the side (left
    to right, bottom to top), `p` 0..1 across the ring from its outer edge
    to the painting. Mitred corners use the same band."""
    x0, y0 = canvas_cell(index)
    o = PORTRAIT_PAD
    r = PORTRAIT_RING
    n = PORTRAIT_CELL - 2 * o  # the ring's outer square
    s = min(max(s, 0.0), 1.0)
    p = min(max(p, 0.0), 1.0)
    if side == "n":
        return _sheet_uv(x0 + o + s * n, y0 + o + p * r)
    if side == "s":
        return _sheet_uv(x0 + o + s * n, y0 + o + n - p * r)
    if side == "w":
        return _sheet_uv(x0 + o + p * r, y0 + o + n - s * n)
    if side == "e":
        return _sheet_uv(x0 + o + n - p * r, y0 + o + n - s * n)
    raise ValueError(f"unknown ring side {side!r}")


def canvas_full_uv(index: int, a: float, b: float) -> tuple[float, float]:
    """UV of (a, b) in 0..1 over the whole cell inside its padding (the
    LOD1 quad: frame ring and painting as one picture)."""
    x0, y0 = canvas_cell(index)
    n = PORTRAIT_CELL - 2 * PORTRAIT_PAD
    a = min(max(a, 0.0), 1.0)
    b = min(max(b, 0.0), 1.0)
    return _sheet_uv(x0 + PORTRAIT_PAD + a * n, y0 + PORTRAIT_PAD + n - b * n)


# -- LOD, Low and moving groups (docs/art/castle-kit.md) ------------------------------
#
# A cell's mesh of one material can be split into named groups:
# `<cell>.<material>.<group>` (assemble.py merges faces by (material,
# group); the ungrouped mesh keeps its M1 name `<cell>.<material>`). Groups
# are the Grand Staircase's level bands (`l0` .. `l7`: portraits, LOD1 beyond
# one storey) and the halls' candle groups (`candles_0` .. `candles_3`: moving
# nodes). A group (or a whole material mesh) with LOD has a `<base>_lod1` node.
# The hourglass sand (`hourglass_sand_house_<a-d>`, scaled to fill) and the
# House Cup banners (`cup_banners_house_<a-d>`, hidden unless that house won).
GROUP_RE = re.compile(r"^(l[0-9]|candles_[0-3]|hourglass_sand_house_[a-d]|cup_banners_house_[a-d])$")
LOD_BALUSTRADE = 30.0  # rules.LOD_DISTANCE (M2): balustrades' painted LOD1
LOD_STOREY = STOREY  # portrait bands: LOD1 beyond one storey (to the band's bounding box)
LOW_CANDLE_GROUPS = 1  # Low: only the nearest candle group of a cell is drawn
EXTRAS_ROLE, EXTRAS_LOD, EXTRAS_ANIM = "role", "kit_lod", "kit_anim"


@dataclass(frozen=True)
class GroupSpec:
    """A named sub-mesh of one material: its LOD1 distance (None: no LOD1)
    and how it moves (`anim`: sorted (key, value) pairs, e.g. kind bob, amp,
    period, phase)."""

    name: str
    lod_distance: float | None = None
    anim: tuple = ()


def node_name(cell: str, material: str, group: str | None = None) -> str:
    """`<cell>.<material>` or `<cell>.<material>.<group>`."""
    if group is not None and not GROUP_RE.match(group):
        raise ValueError(f"not a group name: {group!r}")
    return f"{cell}.{material}" if group is None else f"{cell}.{material}.{group}"


def lod1_name(base: str) -> str:
    return f"{base}_lod1"


# -- colours come from palette data only (docs/art/castle-kit.md) ----------------------------------------------
#
# Every stone, floor, trim, wood, cloth and metal colour comes from palette
# data: the painters' base colours (castle_kit/textures.py, trim.py,
# portraits.py), data/art/castle_textures.json (`palette`, `props_slots`)
# and data/art/toon_palette.json. Piece code names its props-atlas colours
# (`kit:<tile>:@<name>`, palette_tag) and writes no colour literal; COLOR_0
# on kit meshes is a grey value ramp (r = g = b). The test
# tests/test_castle_grid.py PaletteRuleTests fails on any literal in the
# files below.
PALETTE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
COLOUR_LITERAL_RE = re.compile(r"#[0-9A-Fa-f]{6}(?![0-9A-Fa-f])")
COLOUR_LINT_DIRS = ("pieces",)  # every module under castle_kit/pieces/ ...
COLOUR_LINT_FILES = ("rules.py", "assemble.py", "schema.py", "lint.py", "visibility.py", "shade.py", "slice.py")  # ... and these (shade, slice: castle D1)
COLOUR_LINT_EXEMPT = ("pieces/preview.py", "pieces/gallery.py")  # review renders and their lights, never exported


def colour_literals(source: str) -> list[tuple[int, str]]:
    """(line number, line) of every colour literal (#RRGGBB) in `source`,
    comments and docstrings included: the colour rule's lint."""
    return [(i, line.strip()) for i, line in enumerate(source.splitlines(), start=1) if COLOUR_LITERAL_RE.search(line)]


def palette_tag(tile: str, name: str) -> str:
    """`castle_props|kit:<tile>:@<name>`: a props-atlas face in a named
    palette colour (castle_textures.json `palette`; a `props_slots` row
    [tile, "@name"] must exist)."""
    if not PALETTE_NAME_RE.match(name):
        raise ValueError(f"not a palette name: {name!r}")
    return face_tag("castle_props", f"kit:{tile}:@{name}")


# -- KTX2 (docs/art/castle-kit.md "Textures") ---------------------------------------------------

KTX2_MODES = ("etc1s", "uastc")  # castle_textures.json materials.<m>.ktx2.mode; no other codec
KTX2_DEFAULT_MODE = "uastc"


# =====================================================================================
# Castle D1: the depth pass (docs/art/castle-kit.md). Everything below is
# contract: the bake (castle_kit/shade.py, slice.py, assemble.py, lint.py) reads it;
# change it here first.
# =====================================================================================

# -- the vertex-AO bake (castle_kit/shade.py; 19.2) ----------------------------------------
#
# A deterministic ray cast (Blender's BVH through the geometry-nodes Raycast node, batched;
# the same cast as mathutils.bvhtree; no Cycles) per drawn
# vertex of every kit mesh: AO_RAYS cosine-weighted Hammersley directions in the
# vertex's tangent frame, from the vertex plus AO_OFFSET along its normal; a hit at
# distance d < R occludes with weight 1 - (d / R)^2; ao = 1 - mean(weight).
# R and the ray count by the cell's budget kind; castle_kit.json `shade` may
# override them per cell (schema: CellPlan.shade).
AO_RADIUS = {  # metres, by budget kind (BUDGETS)
    "corridor": 2.0,
    "entrance_hall": 3.0,
    "great_hall": 3.0,
    "grand_staircase": 4.0,  # soffits under 4.5-7.75 m galleries
    "common_room": 2.5,
    "tower": 2.0,  # castle M5a: 4 m flights between the core and the shell
}
AO_RADIUS_RANGE = (0.5, 6.0)  # castle_kit.json shade.radius
AO_RAYS = {"great_hall": 128, "grand_staircase": 128}  # by budget kind; every other kind AO_RAYS_DEFAULT
AO_RAYS_DEFAULT = 64
AO_RAYS_RANGE = (16, 256)  # castle_kit.json shade.rays
AO_OFFSET = 0.02  # ray origins this far off the surface along the vertex normal (concave edges do not self-hit)
# Occluders: the cell's drawn LOD0 faces but these materials, the candle groups
# (`candles_*`) and the props atlas's glow faces; plus the Planner faces (the
# greybox render polygons) of the cells joined to this one by a door, within R
# of that door's opening (never their kit: each cell's bake depends on the
# layout and its own kit only, whatever is built next door).
AO_NOT_OCCLUDERS = ("castle_glass", "castle_sky", "castle_cloth")
# Receivers: every drawn face (LOD1 nodes bake their own vertices against the
# LOD0 occluders) but these materials and the glow faces, which get x = 1.
AO_NOT_RECEIVERS = ("castle_glass", "castle_sky")
# Smoothing: values per vertex, then averaged over coincident positions whose
# normals are within SHADE_WELD_DEG of each other, then SHADE_SMOOTH_PASSES
# Laplacian passes (weight SHADE_SMOOTH_WEIGHT) along each merged surface.
SHADE_WELD_DEG = 30.0
SHADE_SMOOTH_PASSES = 1
SHADE_SMOOTH_WEIGHT = 0.5
# Combine (the character _SHADE x's form): x = clamp(ao ** AO_EXPONENT * g, SHADE_MIN, 1),
# g the gentle height gradient (shade_gradient below), quantized to a byte.
AO_EXPONENT = 0.8
SHADE_MIN = 0.35
# The gradient g: the greyed value ramp of the face (palette.VALUE_RAMPS, Rec. 709
# luminance, as assemble.grey) with the wall ramp's bottom raised from 0.70 to this,
# because the AO now darkens wall bases (the old ramp would darken them twice).
# D1 review: 0.86 -> 0.80. With the long-edge pre-filter (shade.edge_samples) the
# AO no longer smears up uncut walls, and that smear had been most of the Great
# Hall's floor-to-cornice spread; 0.80 restores it (look probe, >= 0.06).
SHADE_WALL_BOTTOM = 0.80
# Bake time: the greybox step may grow by at most this at -t 3 (all kitted cells).
SHADE_TIME_MAX_S = 180.0


def ao_radius(kind: str) -> float:
    """The bake's radius for a budget kind (castle_kit.json `shade.radius` overrides it)."""
    return AO_RADIUS[kind]


def ao_rays(kind: str) -> int:
    """Rays per vertex for a budget kind (castle_kit.json `shade.rays` overrides it)."""
    return AO_RAYS.get(kind, AO_RAYS_DEFAULT)


def ao_weight(d: float, radius: float) -> float:
    """A hit's occlusion weight at distance `d` (0 at or beyond `radius`)."""
    if d >= radius:
        return 0.0
    return 1.0 - (d / radius) ** 2


def hammersley(n: int) -> list[tuple[float, float, float]]:
    """`n` cosine-weighted directions on the +z hemisphere (Hammersley points,
    radical inverse base 2), fixed by `n`: the bake's ray set in a vertex's
    tangent frame (x, y tangent, z the normal)."""
    out = []
    for i in range(n):
        u = (i + 0.5) / n
        bits, inv, f = i, 0.0, 0.5
        while bits:
            if bits & 1:
                inv += f
            bits >>= 1
            f *= 0.5
        r = math.sqrt(u)
        phi = 2.0 * math.pi * inv
        out.append((r * math.cos(phi), r * math.sin(phi), math.sqrt(max(0.0, 1.0 - u))))
    return out


def shade_value(ao: float, g: float) -> float:
    """The baked shade x from occlusion `ao` (1 open .. 0) and the gradient `g`."""
    ao = min(1.0, max(0.0, ao))
    return min(1.0, max(SHADE_MIN, ao ** AO_EXPONENT * g))


def shade_byte(x: float) -> int:
    """x quantized to the COLOR_0 byte (written r = g = b = it, a = 255)."""
    return int(round(min(1.0, max(0.0, x)) * 255.0))


# -- COLOR_0 carries value only ---------------------------------------------------------
#
# Every COLOR_0 byte of every castle cell file is grey: r == g == b and a == 255.
# The one documented exception: `floor_grounds` keeps its hue ramp
# until the grounds milestone. lint.py and test_castle_kit.py check it.
COLOR0_HUE_EXCEPTIONS = ("floor_grounds",)


def colour0_grey(rgba_bytes) -> bool:
    """Is every RGBA byte quad of a COLOR_0 buffer grey (r = g = b, a = 255)?"""
    b = bytes(rgba_bytes)
    if len(b) % 4:
        return False
    for i in range(0, len(b), 4):
        if not (b[i] == b[i + 1] == b[i + 2] and b[i + 3] == 255):
            return False
    return True


# -- edge loops where the occlusion changes (castle_kit/slice.py; 19.3) ------------------------
#
# Only `world`-mapped faces (walls, floors, ceilings, vault webs, soffits) are
# sliced, before UVs (world UVs follow position, so the mapping does not move).
# Every cut is a world-aligned plane; coplanar neighbours get the same planes.
SLICE_WALL_FROM_FLOOR = (0.6, 1.5, 3.0)  # horizontal cuts above the wall's floor (0.6: the plinth's top, exists)
SLICE_WALL_FROM_TOP = (0.7, 1.5, 3.0)  # and below its ceiling or springing (0.7: the cornice, exists)
SLICE_NEAR = (0.75, 2.0)  # vertical cuts this far from inner corners, door jambs, shafts, piers, heroes against the
# wall; floor cuts parallel to every wall; ceiling and soffit cuts from walls, corbel lines and gallery edges
SLICE_FOOTPRINT = (0.3, 1.0)  # floor rings round every floor-standing hero's footprint (tables, benches, counter, ...)
SLICE_GRID = STONE_TILE_M  # elsewhere at most this apart, on world multiples of it (the stone tile)
SLICE_ARC_MAX = 1.5  # vaults and arch intrados: an extra ring wherever a segment is longer than this
SLICE_MERGE = 0.25  # a cut closer than this to another cut or to the face's edge is dropped (no slivers)
# The loops' budget (lint, per cell; LOD0 triangles the slicing adds): at most
# SLICE_GROWTH of the unsliced LOD0 count or SLICE_GROWTH_FLOOR, whichever is
# larger, and never more than SLICE_ADD_MAX for the cell's budget kind (the
# Grand Staircase's cap covers both cells; its drawn estimate stays within
# Budget.drawn and grows by at most SLICE_DRAWN_GROWTH).
SLICE_GROWTH = 0.20
SLICE_GROWTH_FLOOR = 2_000
SLICE_ADD_MAX = {
    "corridor": 6_000,
    "entrance_hall": 15_000,
    "great_hall": 20_000,
    "grand_staircase": 30_000,
    "common_room": 10_000,
    "tower": 8_000,  # castle M5a: the 34 m shell and the helical soffits
}
SLICE_DRAWN_GROWTH = 15_000


def slice_allowance(kind: str, unsliced_tris: int) -> int:
    """LOD0 triangles the slicing may add to a cell (both cells for the staircase)."""
    return min(SLICE_ADD_MAX[kind], max(SLICE_GROWTH_FLOOR, int(SLICE_GROWTH * unsliced_tris)))


# -- triangles in view (the client's characters/lod.ts) ----------------------------------------
#
# The budgets cover world plus characters; the character governor gives
# the characters what the measured world in view leaves. The castle's own
# guidelines in view (perf check, not lint). Minimum spec (2026-10-26): Low is a
# fallback that draws the Medium castle; no geometry is made for Low alone.
TRIANGLE_BUDGET = {"high": 2_000_000, "medium": 1_200_000, "low": 500_000}
CASTLE_IN_VIEW_TARGET = {"high": 400_000, "medium": 300_000, "low": 300_000}

# -- the A9 look probe (calibrated on the study references) ------------------------------
#
# Measured by perf-probe.ts on the browser framings of 19.10 (Medium, noon): ratios of
# mean linear luminance.
PROBE_CORNER_RATIO = (0.65, 0.85)  # wall pixels 0.3 m above the floor, 0.3 m from an inner corner / mid-wall
PROBE_SOFFIT_RATIO = (0.45, 0.75)  # gallery soffit / gallery floor at the Grand Staircase look-up
PROBE_SOFFIT_GRADE = (0.60, 0.92)  # a soffit's pixels within 1 m of its wall / at its free edge
PROBE_WALL_SPREAD = 0.06  # a flat wall's luminance, floor band to cornice band: (top - bottom) / top at least
# The light on undersides carries no brown: the shading tint t = rendered / albedo
# (per channel, linear, means over the soffit pixels) has HSV saturation below
# PROBE_TINT_SAT or a hue outside PROBE_BROWN_HUE (degrees).
PROBE_TINT_SAT = 0.08
PROBE_BROWN_HUE = (15.0, 50.0)


# =====================================================================================
# Castle M5a: corridors and towers (docs/art/castle-kit.md). Everything below
# is contract: the corridor and tower pieces read it; change it here first.
# Minimum spec: nothing here is made for Low alone.
# =====================================================================================

# -- cells and owners ----------------------------------------------------------------------
#
# The eight cells M5a kits (castle_kit.json entries, `"build": false` until their
# pieces exist) and the part of the kit that dresses each. The N3 vestibules are surfaces
# of the l4 / l5 / l6 corridors; their tower doors (wt_door_l4..l6) are the tower's.
M5A_CELLS = {
    "sf_corridor_west": "corridors",
    "tf_corridor_west": "corridors",
    "l4_corridor_west": "corridors",
    "l5_corridor_west": "corridors",
    "l6_corridor_west": "corridors",
    "gf_undercroft": "corridors",
    "ff_gallery_east": "corridors",
    "west_tower_stair": "tower",
}
# Triangles per cell (Medium LOD0, D1's loops included): guidelines to report
# against, not caps; the lint's cap is the budget kind's (BUDGETS).
M5A_TRIS = {
    "sf_corridor_west": (10_000, 25_000),
    "tf_corridor_west": (10_000, 25_000),
    "l4_corridor_west": (15_000, 30_000),  # + the portrait band
    "l5_corridor_west": (10_000, 25_000),
    "l6_corridor_west": (10_000, 25_000),
    "gf_undercroft": (20_000, 35_000),
    "ff_gallery_east": (20_000, 35_000),
    "west_tower_stair": (25_000, 45_000),
}
# Per-cell camera.max (castle_kit.json camera_max.cells): the two long spaces zoom out
# further; the tower keeps the default (16 m) between its core and shell.
M5A_CAMERA_MAX = {"gf_undercroft": 20.0, "ff_gallery_east": 20.0, "west_tower_stair": 16.0}

# -- the corridor pieces (Corridors) --------------------------------------------------------

# The Charms class door: a closed oak pair in a 0.3 m recess (OPENING_PIECES), its
# plaque centred over the leaves (props atlas, palette names). It replaces the
# props.json kit `charms_door` in the greybox only: the interactable `charms_door`
# (kind class_door) stays in props.json and the kitted door stands on its spot.
CLASS_DOOR = "kit_door_class_3x4.5"
CLASS_DOOR_PLAQUE = (0.9, 0.35)  # w x h (m), its bottom 0.15 m over the door's apex
CLASS_DOOR_TRIS = 1_200
# Robed statues (our own invented figures, no real person), standing in a
# kit_niche_2x3.5 on its sill: inside the niche's 2 x 3.5 m opening less
# STATUE_NICHE_CLEAR on each side and above, at most INTRUSION_MAX proud of the wall
# face. Props atlas (palette names) only: one draw with the cell's props.
STATUE_PIECES = ("kit_statue_robed_a", "kit_statue_robed_b")
STATUE_TRIS = 2_500
STATUE_HEIGHT = (2.4, 3.2)  # figure and its plinth
STATUE_NICHE_CLEAR = 0.15
# Tapestries: flat cloth on the wall with a rod, material castle_tapestry (sheet
# `tapestries`, castle_textures.json), +1 draw in a cell that hangs one. A piece's
# w / h equals its tile's aspect (no stretching); `args.tile` picks the design.
TAPESTRY_PIECES = {"kit_tapestry_2.5x4": ("tapestry_0", "tapestry_1", "tapestry_2"), "kit_tapestry_6x3": ("tapestry_3",)}
TAPESTRY_PROUD = 0.1  # rod and cloth at most this off the wall
TAPESTRY_BOTTOM = 1.2  # the cloth's bottom at least this above the floor
TAPESTRY_TRIS = 200
# The Long Gallery's rose window (castle_trim tracery, castle_glass lights) and the
# Undercroft Well (a dry well: stone kerb, iron winch frame; props atlas and trim).
ROSE_WINDOW = "kit_window_rose_6x6"
ROSE_TRIS = 3_000
WELL = "kit_well_dry"
WELL_TRIS = 1_500
WELL_RADIUS = 1.5  # its footprint's radius (m); the kerb at most 0.9 m high, the winch over 2.4 m

# -- the tower pieces (Tower) -------------------------------------------------------------------

# The arcaded newel core of the spiral (replaces the greybox wt_core_cap): the 4 x 4 m
# column x -102..-98, z 0..4 from the L3 floor (27) to the shaft's top (60.75), with
# its camera collider shell. The slit window (OPENING_PIECES) and the closed door with
# a bronze bird knocker (our own design) in common room C's 4 x 4.5 arch (replaces
# cr_c_door_leaf and cr_c_knocker).
SPIRAL_CORE = "kit_spiral_core_4"
SPIRAL_CORE_RECT = (-102.0, 0.0, -98.0, 4.0)  # x0, z0, x1, z1
SPIRAL_CORE_TRIS = 8_000
SLIT_WINDOW = "kit_window_slit_0.6x2.4"
SLIT_TRIS = 150
KNOCKER_DOOR = "kit_door_knocker_a"
KNOCKER_DOOR_TRIS = 1_500

M5A_PIECES = (CLASS_DOOR, *STATUE_PIECES, *TAPESTRY_PIECES, ROSE_WINDOW, WELL, SPIRAL_CORE, SLIT_WINDOW, KNOCKER_DOOR)
M5A_PIECE_OWNER = {
    **{p: "corridors" for p in (CLASS_DOOR, *STATUE_PIECES, *TAPESTRY_PIECES, ROSE_WINDOW, WELL)},
    **{p: "tower" for p in (SPIRAL_CORE, SLIT_WINDOW, KNOCKER_DOOR)},
}

# -- the Grand Staircase art pass (Corridors; open item 18-4) -----------------------------------
#
# The two Grand Staircase cells switch castle_wall_a -> castle_wall_c, whose
# stone_ashlar_c textures.py repaints a step darker (the
# values are palette data, never here). Portrait clusters cover
# PORTRAIT_FILL_M5A of their rectangles (grid.PORTRAIT_FILL moved to it with the
# corridors) with the drawn estimate still within Budget.drawn.
PORTRAIT_FILL_M5A = (0.65, 0.85)


# =====================================================================================
# Castle M5b: rooms (docs/art/castle-kit.md). Everything below is contract:
# the furniture, dungeon and upper-room pieces read it; change it here first. Minimum spec: nothing here is made for Low alone.
# =====================================================================================

# -- budget kinds (BUDGETS rows added here, Medium LOD0, D1's loops included) -----------------
#
# `common_room` (12 draws, 40-70 k) exists since M1. Two new kinds: the Potions classroom
# (its greybox pc_* kit, about 26 k triangles, stays inside the cap) and the side rooms
# (Trophy Room, Second-Floor Library, Kitchens).
BUDGETS["classroom"] = Budget(10, (40_000, 70_000))
BUDGETS["room"] = Budget(10, (30_000, 60_000))
AO_RADIUS["classroom"] = 2.5
AO_RADIUS["room"] = 2.5
SLICE_ADD_MAX["classroom"] = 10_000
SLICE_ADD_MAX["room"] = 8_000

# -- cells and owners ----------------------------------------------------------------------
#
# The twelve cells M5b kits (castle_kit.json entries, `"build": false` until their pieces
# exist) and the part of the kit that dresses each: seven existing cells, its three
# new rooms (N2, N4, N6) and the layout's two extra loops, each with the
# builder of its level (the Scullery Passage with the dungeons, the Trophy Stair with the
# Trophy Room).
M5B_CELLS = {
    "dungeon_west": "dungeons",
    "basement_east": "dungeons",
    "dungeon_classroom": "dungeons",
    "common_room_house_b": "dungeons",
    "common_room_house_d": "dungeons",
    "kitchens": "dungeons",
    "scullery_passage": "dungeons",
    "common_room_house_a": "upper_rooms",
    "common_room_house_c": "upper_rooms",
    "sf_library": "upper_rooms",
    "trophy_room": "upper_rooms",
    "trophy_stair": "upper_rooms",
}
M5B_BUILDERS = ("furniture", "dungeons", "upper_rooms")  # merge order (Furniture is already on main)
# Budget kind per cell (castle_kit.json `budget`).
M5B_BUDGET = {
    "dungeon_west": "corridor",
    "basement_east": "corridor",
    "scullery_passage": "corridor",
    "trophy_stair": "corridor",
    "dungeon_classroom": "classroom",
    "common_room_house_a": "common_room",
    "common_room_house_b": "common_room",
    "common_room_house_c": "common_room",
    "common_room_house_d": "common_room",
    "sf_library": "room",
    "trophy_room": "room",
    "kitchens": "room",
}
# Triangles per cell (Medium LOD0, D1's loops included): guidelines to report
# against, not caps; the lint's cap is the budget kind's (BUDGETS).
M5B_TRIS = {
    "dungeon_west": (15_000, 30_000),
    "basement_east": (15_000, 35_000),  # + the still-life band
    "scullery_passage": (10_000, 20_000),
    "trophy_stair": (10_000, 25_000),
    "dungeon_classroom": (40_000, 70_000),  # the pc_* kit (about 26 k) included; at most 70 k
    "common_room_house_a": (40_000, 70_000),
    "common_room_house_b": (40_000, 70_000),
    "common_room_house_c": (40_000, 70_000),
    "common_room_house_d": (40_000, 70_000),
    "sf_library": (30_000, 60_000),
    "trophy_room": (30_000, 60_000),
    "kitchens": (30_000, 60_000),
}
# Wall and floor variants per cell (castle_kit.json `materials`): the west basement
# (dungeons, Potions, common room D, the Scullery Passage) in the cool dungeon stone, the
# east basement (the kitchen corridor, the Kitchens, common room B) in the warm hall stone;
# upstairs the warm stone for the hearth rooms and the library, the corridor stone for the
# Trophy Room and its stair. Two textures more in all (castle_wall_d, castle_floor_dungeon).
M5B_MATERIALS = {
    "dungeon_west": ("castle_wall_d", "castle_floor_dungeon"),
    "dungeon_classroom": ("castle_wall_d", "castle_floor_dungeon"),
    "common_room_house_d": ("castle_wall_d", "castle_floor_dungeon"),
    "scullery_passage": ("castle_wall_d", "castle_floor_dungeon"),
    "basement_east": ("castle_wall_b", "castle_floor_long"),
    "kitchens": ("castle_wall_b", "castle_floor_long"),
    "common_room_house_b": ("castle_wall_b", "castle_floor_long"),
    "common_room_house_a": ("castle_wall_b", "castle_floor_long"),
    "sf_library": ("castle_wall_b", "castle_floor_long"),
    "common_room_house_c": ("castle_wall_a", "castle_floor_square"),
    "trophy_room": ("castle_wall_a", "castle_floor_square"),
    "trophy_stair": ("castle_wall_a", "castle_floor_square"),
}
# The bake per cell (castle_kit.json `shade`): the kind's radius, 3 m in the two tall
# common rooms (12 and 14 m ceilings) and the Trophy Stair's 17.4 m stairwell.
M5B_SHADE = {cid: (3.0 if cid in ("common_room_house_a", "common_room_house_c", "trophy_stair")
                   else AO_RADIUS[kind], AO_RAYS_DEFAULT) for cid, kind in M5B_BUDGET.items()}
# Per-cell camera.max (castle_kit.json camera_max.cells): the common rooms' 20 m (since
# M1) and the two big rooms; every other M5b cell the default 16 m.
M5B_CAMERA_MAX = {"common_room_house_a": 20.0, "common_room_house_b": 20.0, "common_room_house_c": 20.0,
                  "common_room_house_d": 20.0, "dungeon_classroom": 20.0, "sf_library": 20.0}
# The Potions classroom's cauldrons are interactable positions: their props.json kit
# entries stay (never in `replaces`, never moved).
M5B_KEEP_KIT = ("pc_cauldron_1", "pc_cauldron_2", "pc_cauldron_3", "pc_cauldron_4")

# -- seats and floor seats (every M5b seat; the character contract's seated clip) -------------
#
# Manifest `seats` rows [x0, z0, x1, z1, y, yaw, front, back, drop] (castle kit M3, 17.4) come
# from a piece's meta "seats" in its own frame (`front` / `back`: the seat's reach ahead of and
# behind the row's centre line, `drop` its top over the floor the feet rest on; the client
# slides the hips along them to fit the sitter's legs, characters/seated.ts). The seated clip puts the root on the seat's top and the
# heels on a floor SEAT_TOP below it at the tallest character (1.88 m); its knees reach
# KNEE_REACH ahead of the root, their top KNEE_TOP above the floor, the legs 0.39 m wide.
SEAT_TOP = 0.46  # a seat's top above the floor (the clip's drop)
SEAT_TOP_RANGE = (0.42, 0.50)
SEAT_ROW_DEPTH = (0.40, 0.55)  # a seat row's rectangle, front to back (the hips' band)
SEAT_WIDTH_MIN = 0.55  # per sitter along the row
SEAT_FRONT_MAX = 0.40  # the seat's front edge at most this ahead of the row's centre line (calves hang free)
KNEE_REACH = 0.50
KNEE_TOP = 0.60
# Knee room: nothing (the piece itself or any other kit) within this box ahead of a seat
# row: from the row's centre line to KNEE_CLEAR_AHEAD ahead, the row's width less
# KNEE_CLEAR_INSET at each end, from the floor to KNEE_CLEAR_TOP.
KNEE_CLEAR_AHEAD = 0.65
KNEE_CLEAR_INSET = 0.1
KNEE_CLEAR_TOP = 0.70
ARM_GAP_MIN = 0.55  # between a chair's or sofa's arms (the legs 0.39 m, the robe over the arms)
# Manifest `floor_seats` (new, export only: no client hookup in M5b, the floor-sit clip
# is the character branch's, plan D-15): rows [x0, z0, x1, z1, y] in world metres, `y` the
# top of the rug or step a character sits on; from a piece's meta "floor_seats" [[x0, z0,
# x1, z1, y], ...] in its own frame (rugs, hearth steps). The cell's rows are sorted.
FLOOR_SEATS = "floor_seats"
FLOOR_SEAT_MIN = 0.6  # each side of a floor-seat rectangle at least this (one sitter)
FLOOR_SEAT_HEIGHT_MAX = 0.35  # its top at most this above the floor it lies on

# -- the room furniture set (Furniture: pieces/room_furniture.py, built first) ----------------
#
# Human scale, frames as props.json `kit`: origin at the bottom centre, yaw 0
# facing +z (a seat faces +z; a wall piece's back on its wall, its face towards +z).
# Walk-through (`walkthrough`, like the hall furniture) but the dorm door and the
# noticeboard (wall pieces, within INTRUSION_MAX of the wall). Oak in castle_wood (the
# hall furniture's strip UVs), cloth, leather, metal and the notices on the props atlas
# by palette name (castle_textures.json, the `_doc_m5b_furniture` block). House accents
# only by the names house_a..d, picked by an `args` index (FURNITURE_COVERS).
# Cover 5, a rose cloth, for the Divination armchairs only (the
# leather read near-black there at Medium); every other room keeps its cover.
FURNITURE_COVERS = ("house_a", "house_b", "house_c", "house_d", "upholstery_leather",
                    "upholstery_rose")  # args.cover 0..5
RUG_FIELDS = ("house_a", "house_b", "house_c", "house_d", "rug_neutral")  # args.field 0..4
SOFA = "kit_sofa"  # sized: kit_sofa_<length> (1.6-3.2 m); seats one row of length // SEAT_WIDTH_MIN sitters
SOFA_LENGTH = (1.6, 3.2)
SOFA_TRIS = 500
ARMCHAIR = "kit_armchair"
ARMCHAIR_TRIS = 350
RUG = "kit_rug"  # sized: kit_rug_<w>x<d> (1.5-8 m each way), 0.02 m thick, a floor_seats rectangle inset 0.1
RUG_SIZE = (1.5, 8.0)
RUG_THICK = 0.02
RUG_TRIS = 60
TABLE_ROUND = "kit_table_round"  # 1.4 m across, top 0.74
TABLE_ROUND_TRIS = 300
TABLE_STUDY = "kit_table_study"  # args.length (1.6-3.2 m, default 2.4), 0.9 deep, top 0.76
TABLE_STUDY_TRIS = 250
CHAIR_STUDY = "kit_chair_study"  # one seat, no arms
CHAIR_STUDY_TRIS = 200
NOTICEBOARD = "kit_noticeboard"  # 1.8 x 1.2 cork board in an oak frame, its foot 1.1 m up, notices pinned
NOTICEBOARD_TRIS = 250
DORM_DOOR = "kit_door_dorm"  # a closed oak leaf 1.6 x 2.6 in a 2 x 2.9 frame on the wall, args.house crest, args.round
DORM_DOOR_TRIS = 500
CHANDELIER = "kit_chandelier_ring"  # an iron ring (args.radius) of args.count candles on a chain of args.chain metres
CHANDELIER_COUNT = (6, 24)
CHANDELIER_RADIUS = (0.6, 1.5)
CHANDELIER_BELOW = 0.3  # its lowest point (the pendant) this below the hero's pos (the ring's plane)
CHANDELIER_TRIS = 1_500
FURNITURE_PIECES = (SOFA, ARMCHAIR, RUG, TABLE_ROUND, TABLE_STUDY, CHAIR_STUDY, NOTICEBOARD, DORM_DOOR, CHANDELIER)
# A ring chandelier hangs in a candle group of its own: its hero entry gives args.group k
# (0..CANDLE_GROUPS_MAX - 1, not a `candles` entry's), and its faces merge into the moving
# node `<cell>.castle_props.candles_<k>` (one draw, bobbing like the Great Hall's: args.amp
# 0..CANDLE_BOB_AMP, default CHANDELIER_BOB_AMP, args.period, args.phase). No LOD1. The
# schema checks its lowest point at the bottom of its bob stays OVERHEAD_CLEARANCE over the
# floor and its chain's top under the ceiling.
CHANDELIER_BOB_AMP = 0.03
CHANDELIER_BOB_PERIOD = 6.0

# -- the dungeon pieces (Dungeons: pieces/dungeon.py) ------------------------------------------
VAULT_BARREL = "kit_vault_barrel"  # sized: kit_vault_barrel_<span>x<length>, round, transverse ribs every bay
VAULT_BARREL_TRIS_PER_M = 300  # per metre of length, ribs included
TORCH_WALL = "kit_torch_wall"  # an iron bracket with a flame (glow slot glow_lantern), bracket 2.6-3.2 m up
TORCH_WALL_TRIS = 200
STONE_DOOR = "kit_door_stone_hidden"  # common room D's wall door: a stone panel in castle_wall faces filling cr_d_wall
STONE_DOOR_TRIS = 600
BARREL_STACK = "kit_barrel_stack"  # common room B's door: barrels stacked in the cr_b_barrels opening
BARREL_STACK_TRIS = 3_000
KITCHEN_HEARTH = "kit_hearth_kitchen"  # an open hearth with a raised step (floor_seats) and a spit
KITCHEN_HEARTH_TRIS = 3_000
PANS_HANGING = "kit_pans_hanging"  # a rack of copper pans over 2.4 m
PANS_HANGING_TRIS = 1_200
POTS_SET = "kit_pots_set"  # pots and a cauldron on a table or the hearth
POTS_SET_TRIS = 800
DUNGEON_PIECES = (VAULT_BARREL, TORCH_WALL, STONE_DOOR, BARREL_STACK, KITCHEN_HEARTH, PANS_HANGING, POTS_SET)

# -- the upper-room pieces (Upper rooms: pieces/rooms_upper.py) --------------------------------
DOME_STARRY = "kit_dome_starry"  # sized: kit_dome_starry_<diameter>; castle_sky panels (stars) in a ribbed dome
DOME_STARRY_TRIS = 3_000
STATUE_SCHOLAR = "kit_statue_scholar"  # an invented free-standing figure on a plinth, 2.6-3.4 m (optional: reuse
STATUE_SCHOLAR_TRIS = 3_000  # kit_statue_robed_a / _b first)
BOOKCASE = "kit_bookcase"  # sized: kit_bookcase_<w>x<h>, against a wall, at most 0.5 m deep, books by palette name
BOOKCASE_TRIS_PER_M = 400  # per metre of width
TROPHY_CASE = "kit_trophy_case"  # an oak case with shelves of invented cups, plates and shields
TROPHY_CASE_TRIS = 2_500
ARMILLARY = "kit_armillary"  # the Hanging Armillary at its landmark, its lowest ring over 2.4 m
ARMILLARY_TRIS = 2_500
FLOOR_MAP = "kit_floor_inlay_map"  # sized: kit_floor_inlay_map_<w>x<d>, flush, an invented plan (as the floor compass)
FLOOR_MAP_TRIS = 3_000
UPPER_PIECES = (DOME_STARRY, STATUE_SCHOLAR, BOOKCASE, TROPHY_CASE, ARMILLARY, FLOOR_MAP)

M5B_PIECE_OWNER = {
    **{p: "furniture" for p in FURNITURE_PIECES},
    **{p: "dungeons" for p in DUNGEON_PIECES},
    **{p: "upper_rooms" for p in UPPER_PIECES},
}

# -- portraits: the still-life mix ----------------------------------------------------------------
#
# castle_kit.json portraits `mix: "still_life"` (the kitchen corridor, the Kitchens): small
# squarish frames, every canvas a still life (PORTRAIT_KINDS["still_life"]); the Dungeons
# builder makes the cluster generator (pieces/frames.py) pick the kind by mix.
PORTRAIT_FRAMES_STILL_LIFE = (
    (0.8, 0.8, "rect", 3.0),
    (1.0, 0.8, "rect", 3.0),
    (1.2, 1.0, "rect", 3.0),
    (1.6, 1.2, "rect", 2.0),
    (1.0, 1.0, "oval", 1.0),
)
PORTRAIT_MIXES["still_life"] = PORTRAIT_FRAMES_STILL_LIFE
PORTRAIT_MIX_KIND = {"gallery": None, "still_life": "still_life"}  # None: by the frame's aspect (kind_for)


# =====================================================================================
# Castle M5c: courtyard, facades and exterior (docs/art/castle-kit.md "Exterior").
# Everything below is contract: the facade and courtyard pieces and the client read it;
# change it here first. Minimum spec: nothing here is made for Low alone.
# =====================================================================================

# -- budget kind (a BUDGETS row added here, Medium LOD0, D1's loops included) ----------------
#
# The courtyard (68 x 48 m, open to the sky, 16 m walls): its walls are its own (the
# Planner draws a courtyard's walls 16 m high), so its arcades, gatehouse and the facade
# pieces on its walls count here. Facade pieces draw an LOD1 beyond FACADE_LOD (one of
# the two drawn at a time; the cap counts LOD0).
BUDGETS["courtyard"] = Budget(12, (60_000, 120_000))
AO_RADIUS["courtyard"] = 3.0  # open sky: most rays escape; 3 m reaches under the arcades
SLICE_ADD_MAX["courtyard"] = 12_000

# -- cells, builders and owners ------------------------------------------------------------
M5C_BUILDERS = ("facades", "courtyard", "client")  # merge order
# The cell M5c kits (castle_kit.json, `"build": false` until its pieces existed).
M5C_CELLS = {"courtyard": "courtyard"}
M5C_TRIS = {"courtyard": (60_000, 120_000)}  # guideline; the kind's cap is the lint's
M5C_SHADE = {"courtyard": (3.0, AO_RAYS_DEFAULT)}
M5C_CAMERA_MAX = {"courtyard": 24.0}  # since M1
M5C_MATERIALS = {"courtyard": ("castle_wall_b", "castle_floor_court")}  # the hall's warm stone outside too

# -- the courtyard's plan (layout.json `courtyard`, x -18..50, z 42..90) -------------
COURT_RECT = (-18.0, 42.0, 50.0, 90.0)  # x0, z0, x1, z1
COURT_WALL_TOP = L.OPEN_WALL_HEIGHT["courtyard"]  # 16 m: the courtyard's walls (the facades it owns)
# The Exchange Yard (landmark exchange_yard, the Owl Gate's corner): the market stalls,
# benches and notice boards stand in it, nowhere else.
EXCHANGE_YARD = (-18.0, 42.0, 8.0, 58.0)
# Lanes no hero, pier or planter stands in below OVERHEAD_CLEARANCE (x0, z0, x1, z1): the
# front doors' apron, the Owl Gate's approach, the courtyard gate's passage inside.
COURT_KEEP_CLEAR = {
    "front_doors": (11.0, 42.0, 21.0, 50.0),
    "owl_gate": (0.5, 42.0, 4.5, 46.0),
    "courtyard_gate": (12.0, 84.0, 20.0, 90.0),
}
# The traceried window over the front doors seen from the courtyard: its
# hole in the courtyard's north wall face (x0, x1, y0, y1) the courtyard kit leaves open.
FRONT_WINDOW_OUTSIDE = (12.0, 20.0, 10.8, COURT_WALL_TOP)  # the 8 x 8 window from its 10.8 m sill

# -- the exterior material clones ---------------------------------------------
#
# High's sun shadow falls on outdoor materials only. A kitted face that is outside (an
# outdoor cell's faces, a facing cell's Planner wall_exterior and roof faces, the facade
# pieces) takes `<material>@ext`: the same castle_* material and textures (one GPU copy),
# a separate client material with the outdoor (sun-shadow) flag, one more draw where used.
EXT_SUFFIX = "@ext"
# Never cloned: glass, the enchanted sky, portraits and tapestries take no sun shadow;
# castle_roof and castle_floor_court are outdoor by name (the client gives them the sun's
# shadow as they are).
EXT_NEVER = ("castle_glass", "castle_sky", "castle_portraits_a", "castle_portraits_b", "castle_tapestry")
OUTDOOR_MATERIALS = ("castle_roof", "castle_floor_court")
EXT_PLANNER_FACES = ("wall_exterior", "roof")  # a facing cell's Planner faces that are outside


def ext_name(material: str) -> str:
    """`castle_wall_b` -> `castle_wall_b@ext` (unchanged if never cloned or already one)."""
    if material.endswith(EXT_SUFFIX) or material in EXT_NEVER or material in OUTDOOR_MATERIALS:
        return material
    return material + EXT_SUFFIX


def ext_base(material: str) -> str:
    """`castle_wall_b@ext` -> `castle_wall_b` (the row, textures and toon ramp it shares)."""
    return material[: -len(EXT_SUFFIX)] if material.endswith(EXT_SUFFIX) else material


def is_outdoor_material(material: str) -> bool:
    """Whether the client gives a castle material the sun's shadow (High)."""
    return material.endswith(EXT_SUFFIX) or material in OUTDOOR_MATERIALS


# -- facades -----------------------------------------------------------------------
#
# The cells whose outside faces a player in the courtyard sees (measured at contracts:
# rays from the courtyard at 1.7 and 10 m to every Planner outside face), with the sides
# that face it: the hall's south face above the courtyard's wall (y 16..20.6), the Great
# Hall's south face (z 31.8, to 29.6 m) and its west gable over the hall, the west range
# (the Grand Staircase stack's south and east faces to 72.6 m, common room A's east face
# beside it). The courtyard's own walls are its facades (the facade rules dress their
# tops: parapet, pinnacles; the courtyard pieces everything below COURT_WALL_TOP). The
# far west (the west tower, the L3-L6 vestibule ends, 90 m off) is left as it is.
M5C_FACADE_CELLS = {
    "entrance_hall": ("s",),
    "great_hall": ("s", "w"),
    "grand_staircase": ("s", "e"),
    "common_room_house_a": ("e",),
    "courtyard": ("n", "s", "w", "e"),
}
FACADE_DRAWS = 2  # draws a facing cell may add over its kind's cap: its @ext wall and castle_roof (+1-2)
FACADE_LOD = 60.0  # facade pieces draw their LOD1 beyond this (kit_lod, 17.6)
FACADE_TRIS = 20_000  # guideline: a facing cell's facade pieces at LOD0 (its roof included)


def draw_cap(cell: str, kind: str) -> int:
    """The lint's draw cap for a kitted cell: its kind's, plus FACADE_DRAWS on a facing cell."""
    extra = FACADE_DRAWS if cell in M5C_FACADE_CELLS and kind != "courtyard" else 0
    return BUDGETS[kind].draws + extra


# The facade pieces (Facades: pieces/facades.py). Outside faces only: face tags
# `castle_wall@ext|world`, `castle_trim@ext|trim:<strip>`, `castle_roof|world`.
BUTTRESS = "kit_buttress"  # sized kit_buttress_<h> (6-30 m): stepped, with weatherings, against an outer face
BUTTRESS_HEIGHT = (6.0, 30.0)
BUTTRESS_PROUD = (0.8, 1.6)  # its foot's depth off the face; it steps back as it rises
BUTTRESS_TRIS = 300
PARAPET_CRENEL = "kit_parapet_crenel"  # args.length (2-60 m): merlons and embrasures on a wall top, coped
PARAPET_HEIGHT = (1.2, 1.8)  # above the wall top
PARAPET_MERLON = (0.8, 1.2)  # merlon width; the embrasures 0.5-0.8 m
PARAPET_TRIS_PER_M = 40
PINNACLE = "kit_pinnacle"  # a square shaft and a crocketed spire (our own design), at corners and on buttresses
PINNACLE_HEIGHT = (3.0, 6.0)
PINNACLE_TRIS = 500
ROOF_SLATE = "kit_roof_slate"  # sized kit_roof_slate_<w>x<l>: a pitched slate roof over a cell's roof slab
ROOF_PITCH_DEG = (45.0, 60.0)
ROOF_TRIS = 1_500
TRACERY_OUTER = "kit_window_tracery_outer_8x8"  # the traceried window's outer order on the courtyard face
TRACERY_OUTER_TRIS = 3_000
# Castle M5c review (castle-kit.md): a lancet in relief on an outer face that cannot be cut,
# the storeys of a facade FACADE_STOREY_BANDS tall (rules.py _facade_windows: the west range).
BLIND_WINDOW = "kit_window_blind_1.6x5"
BLIND_WINDOW_TRIS = 400
FACADE_PIECES = (BUTTRESS, PARAPET_CRENEL, PINNACLE, ROOF_SLATE, TRACERY_OUTER, BLIND_WINDOW)

# -- the courtyard pieces (Courtyard: pieces/courtyard.py) -----------------------------------------
#
# Floor-standing pieces are walk-through (the server has no prop collision; bots keep out
# of their footprints). Stone in castle_wall@ext (world UVs) and
# castle_trim@ext, metal, foliage and cloth on the props atlas by palette name.
ARCADE_BAY = "kit_arcade_bay_6"  # one 6 m bay of an open pointed arcade along a courtyard wall
ARCADE_DEPTH = (2.5, 3.5)  # the pier line's distance from the wall face (the walk under it)
ARCADE_APEX = (5.0, 7.5)  # the arch's apex above the floor
ARCADE_PIER = (0.6, 1.0)  # a pier's width
ARCADE_TRIS = 2_500  # per bay, its lean-to roof (castle_roof) included
FOUNTAIN = "kit_fountain_tiered"  # replaces the greybox fountain_basin and fountain_column
FOUNTAIN_CENTRE = (16.0, 61.2)  # the landmark courtyard_fountain
FOUNTAIN_RADIUS = (2.5, 4.5)  # its basin's outer radius
FOUNTAIN_HEIGHT = (3.0, 5.0)
FOUNTAIN_TRIS = 6_000
GATEHOUSE = "kit_gatehouse_8x10"  # round the courtyard gate (8 x 10): towers either side, a raised portcullis
GATEHOUSE_TOP = (20.0, 26.0)  # above the 16 m wall
PORTCULLIS_CLEAR = 6.0  # the raised portcullis's teeth at least this above the gate's floor
GATEHOUSE_TRIS = 8_000
LAMP_POST = "kit_lamp_post"  # an iron post with a lantern head (glow slot glow_lantern)
LAMP_POST_HEIGHT = (3.5, 4.5)
LAMP_POST_TRIS = 400
MARKET_STALL = "kit_market_stall"  # Exchange Yard: a 3 x 2 m stall, its awning by args.house, wares by args.goods
MARKET_STALL_SIZE = (3.0, 2.0, 3.2)  # w, d, h at most
STALL_AWNINGS = ("house_a", "house_b", "house_c", "house_d")  # args.house 0..3: house accents by name only
STALL_GOODS = 4  # args.goods 0..3: invented wares (parcels, jars, scrolls, quills)
MARKET_STALL_TRIS = 1_500
PLANTER_TREE = "kit_planter_tree"  # a stone planter and its tree (vegetation look A: a foliage kit planting)
PLANTER_SIZE = (1.2, 2.4)  # the planter's side
PLANTER_TREE_HEIGHT = (4.0, 7.0)  # the crown's lowest point at least OVERHEAD_CLEARANCE
PLANTER_TREE_TRIS = 1_200  # the planter alone (its tree is a planting of the foliage kit, world/foliage.py)
COURTYARD_PIECES = (ARCADE_BAY, FOUNTAIN, GATEHOUSE, LAMP_POST, MARKET_STALL, PLANTER_TREE)

# -- the Owl Exchange court in the hall (Courtyard; open items 16-9, 18-6) -----------------------
#
# The court (the exchange scenario's area) gets rugs (kit_rug reused), a notice-board
# wall, crate stacks and a perch tree. The hall stays within its own budget (BUDGETS
# "entrance_hall": 11 draws, 120 k; its facade allowance is the facades').
EH_COURT = (0.5, 22.5, 11.5, 37.5)  # x0, z0, x1, z1
EH_COURT_KEEP_CLEAR = {
    "counter": (0.0, 23.5, 7.0, 36.5),  # the counter and its reach ring (owl_exchange, 3.5 m round (3.5, 30))
    "owl_gate": (0.5, 37.0, 4.5, 40.0),  # the Owl Gate's approach
    "front_leaf": (6.9, 39.4, 11.5, 40.0),  # the front doors' west leaf, flat on the south wall
}
NOTICE_WALL = "kit_notice_wall"  # a free-standing oak screen with notice boards (no readable text)
NOTICE_WALL_TRIS = 800
CRATE_STACK = "kit_crate_stack"  # parcels and crates (invented markings)
CRATE_STACK_TRIS = 600
PERCH_TREE = "kit_perch_tree"  # an oak and iron perch tree with owls (our own design), 2.4-3.4 m
PERCH_TREE_TRIS = 2_500
COURT_DRESSING_PIECES = (NOTICE_WALL, CRATE_STACK, PERCH_TREE)

M5C_PIECES = (*FACADE_PIECES, *COURTYARD_PIECES, *COURT_DRESSING_PIECES)
M5C_PIECE_OWNER = {
    **{p: "facades" for p in FACADE_PIECES},
    **{p: "courtyard" for p in (*COURTYARD_PIECES, *COURT_DRESSING_PIECES)},
}

# -- textures: castle_textures.json rows -------------------------------------
#
# Material -> (texture, the painter that paints it in textures.py PAINTERS). Colours from
# world/palette.py CASTLE (`roof`, `flagstone_courtyard`): the painter values are in 30.6.
M5C_TEXTURES = {"castle_roof": ("roof_slate", "facades"), "castle_floor_court": ("stone_paving_court", "courtyard")}
M5C_MILESTONE = 7


# =====================================================================================
# The instance rooms built by the castle kit (docs/architecture.md;
# tools/blender/world/instance_kit.py, class_rooms.py). They are no layout
# cells and never in castle_kit.json; each takes one M5b builder's rules and lint as
# its castle counterpart does (the Potions vault the dungeons', the classroom's
# bookcases the upper rooms').
# =====================================================================================

INSTANCE_RULES = {"class_potions": "dungeons", "class_classroom": "upper_rooms"}
INSTANCE_RULES.update({"class_defence": "upper_rooms", "class_divination": "upper_rooms"})
INSTANCE_RULES["class_greenhouse"] = "upper_rooms"


def m5b_rules(cell: str) -> str | None:
    """The M5b builder whose rules and lint `cell` takes: grid.M5B_CELLS for
    the castle's cells, INSTANCE_RULES for the kitted instance rooms."""
    return M5B_CELLS.get(cell) or INSTANCE_RULES.get(cell)


# =====================================================================================
# The House Cup in the Great Hall (docs/architecture.md;
# docs/art/castle-kit.md).
# =====================================================================================

HOUSE_SLOTS = ("a", "b", "c", "d")  # House::ALL order: a Roarwing, b Burrowsby, c Skytalon, d Serpentyne
# kit_hourglass_set: each glass's sand (the lower glass, modelled full) is its own moving
# node `<cell>.castle_props.hourglass_sand_house_<x>` (glass i of the set is house
# HOUSE_SLOTS[i % 4]), its origin at the base of the sand, so the client's Y scale 0..1
# fills it: extras.kit_anim {"kind": "fill", "house": "house_<x>"}.
SAND_GROUP = "hourglass_sand_house_{}"
ANIM_FILL = "fill"
# kit_banner_cup_<x>: a house's House Cup banners, long kit_banner_<x> banners floating
# over the long house tables, one group per house (`cup_banners_house_<x>`: the cloth
# in castle_cloth and the gilt rods in castle_props, both nodes with extras.kit_anim
# {"kind": "cup_banners", "house": "house_<x>"}); the client shows a group only for the
# houses that won last week (all hidden by default). Each house hangs on its own x, so
# a tie shows every winner's banners side by side.
CUP_BANNER = "kit_banner_cup"
CUP_BANNER_GROUP = "cup_banners_house_{}"
ANIM_CUP_BANNERS = "cup_banners"
CUP_BANNER_SIZE = (1.8, 7.2)  # width x height (m): kit_banner's 1.6 x 6 at about 1.2 x
CUP_BANNER_CUT = 0.96  # the swallowtail's notch (kit_banner's 0.8 at 1.2 x)
CUP_BANNER_TRIS = 1_500  # per house
CUP_BANNERS_SHOWN = 1  # houses whose cup banners the lint's draw budget counts (one winner)


def house_anim(kind: str, slot: str) -> tuple:
    """A House Cup group's anim pairs (GroupSpec.anim): its kind and house palette name."""
    return (("house", f"house_{slot}"), ("kind", kind))


def is_cup_banner_group(group) -> bool:
    return bool(group) and group.startswith("cup_banners_house_")


def is_sand_group(group) -> bool:
    return bool(group) and group.startswith("hourglass_sand_house_")


# =====================================================================================
# The Astronomy class room, `class_astronomy`, a tower-top platform
# open to the night sky (docs/architecture.md; tools/blender/world/
# school_rooms_astronomy.py, castle_kit/pieces/astronomy.py). Its floor is a `grounds`
# surface with no ceiling, so the Planner walls it with the grounds' low wall (1 m), which
# carries the M5c crenellated parapet; the class door stands in the stair head
# (instance_kit.KitRoom.head_door).
# =====================================================================================

INSTANCE_RULES["class_astronomy"] = "upper_rooms"
STAIR_HEAD = "kit_stair_head"  # the head of the tower stair on the platform's edge, with the class door
STAIR_HEAD_SIZE = ((5.0, 8.0), (2.5, 5.0), (5.6, 8.0))  # width, depth, height (to its top, under the parapet)
STAIR_HEAD_LINTEL = 1.0  # stone kept over the door's hole (the plaque and the arch's head stand in it)
STAIR_HEAD_TRIS = 1_500
TELESCOPE = "kit_telescope"  # a brass refractor on an oak tripod, one per pair of students
TELESCOPE_SCALE = (1.0, 1.6)  # the students' at 1, the professor's larger
TELESCOPE_TILT = (0.3, 1.0)  # radians up from level, before its seed's few degrees
TELESCOPE_TRIS = 600  # at scale 1
PLATFORM_PARAPET = {"height": 1.5, "breast": 0.15, "thick": 0.56, "proud": 0.12}  # on the low wall's top


# =====================================================================================
# The grounds and the exterior (docs/art/castle-kit.md "Exterior"). The numbers
# below: the grounds export (tools/blender/world/grounds_export.py and its grounds*.py) and the
# HLOD export (tools/blender/world/hlod_export.py and its hlod*.py) read them and their
# lints check them. The grounds are not castle_kit.json cells: greybox.py hands them to
# the grounds export.
# =====================================================================================

P3A_GROUNDS_CELLS = [f"grounds_r{r}_c{c}" for r in range(5) for c in range(6)]  # 40 m cells, terrain_layout.py
P3A_GROUNDS_BUDGET = Budget(6, (8_000, 25_000))  # per grounds cell at LOD0 (terrain, paths, dressing; foliage aside)
P3A_GROUNDS_LOD = FACADE_LOD  # every grounds node has a `_lod1` drawn beyond 60 m (kit_lod)
P3A_GROUNDS_LOD1_TRIS = 3_000  # per grounds cell at LOD1
P3A_FOLIAGE_DRAWS = 4  # trees and rocks over the whole grounds (thin instances), LOD0 and LOD1 alike
P3A_FOLIAGE_TRIS = 60_000  # every rock instance on the grounds at LOD0 (a guideline; the trees are plantings)
P3A_TILE_TRIS_CAP = 150_000  # a grounds cell with the vegetation standing in it at LOD0 (style guide section 6)
P3A_HEIGHT_TOLERANCE = 0.02  # a terrain vertex over a walkable rectangle against the heightfield (m)
P3A_BACKDROP_TRIS = 5_000  # the forest edge, the mountains and the castle crag (cell `backdrop`)
P3A_BACKDROP_DRAWS = 2
P3A_WATER_DRAWS = 1  # the lake's water (cell `lake`)
P3A_HLOD_TRIS = 60_000  # every chunk together
P3A_HLOD_CHUNKS = (6, 10)
P3A_HLOD_DRAWS_PER_CHUNK = 2  # castle_wall_c@ext and castle_roof, shared library materials
P3A_HLOD_GPU_MB = 16  # no texture of its own: the shared library's
P3A_HLOD_INSET = 0.3  # a stand-in's walls and roofs sit this far inside the real cells' outer faces
P3A_HLOD_FILE = "hlod.glb"
P3A_EXTERIOR_STONE = "castle_wall_c"  # one outside stone: the Grand Staircase's west-range stone
P3A_RESIDENT_CELLS = tuple(P3A_GROUNDS_CELLS) + ("lake", "backdrop", "foliage")  # manifest `resident: true`


# =====================================================================================
# Flying and the Flying Pitch (docs/architecture.md). The pitch's
# numbers (its site, field, stands, towers, hoops and gate) are data/world/terrain/grounds.json
# `pitch` (written by terrain_layout.py); the pitch export
# (tools/blender/world/pitch_export.py) builds the cell through greybox.py's outdoor hook
# like the grounds (world/outdoor.py) and its lint checks these budgets.
# =====================================================================================

P3B_PITCH_CELL = "pitch"  # the stands' decks and steps are its surfaces; drawn whenever outdoors
P3B_PITCH_BUDGET = Budget(12, (60_000, 120_000))  # the `courtyard` kind's: at most 12 draws, LOD0
P3B_PITCH_LOD1_TRIS = 20_000  # every node has a `_lod1` beyond P3A_GROUNDS_LOD (60 m)
P3B_CLASS_PITCH_DRAWS = 10  # class_pitch as a `classroom`
P3B_RESIDENT_CELLS = (P3B_PITCH_CELL,)  # manifest `resident: true` (seen from the whole grounds)
P3B_BROOM_TRIS = 800  # the broom prop (charlib)


def hlod_node(chunk: str, material: str) -> str:
    """The node name of one chunk's mesh in hlod.glb (`hlod.<chunk>.<material>`)."""
    return f"hlod.{chunk}.{material}"


# =====================================================================================
# The castle grows (docs/architecture.md; docs/art/castle-kit.md).
# The five new spaces' cells (castle_kit.json, `"build": false` until their
# builder flips them), their budget kinds and owners, and the moving staircases' nodes.
# The flights' pivots, angles and schedule and the landing gates are
# data/world/stairs.json (sim_core::stairs); the kit only draws them.
# =====================================================================================

P3C_CELLS = {
    "clock_courtyard": "wings_a",  # N8: the Clock Tower courtyard and its passage from the courtyard
    "covered_bridge": "wings_a",  # N8: the covered timber bridge to the stone circle
    "tf_corridor_east": "wings_a",  # N9: the east wing's hall and the Third-Floor Corridor
    "hospital_wing": "wings_a",  # N9: the Hospital Wing
    "owl_loft": "wings_b",  # N10: the Owl Loft's spiral and loft (the tower on its knoll)
    "l7_corridor_east": "wings_b",  # N11: the seventh-floor east corridor
    "dungeon_cellars": "wings_b",  # N12: the dungeon cellars and their two stairs
}
P3C_BUILDERS = ("wings_a", "wings_b")  # merge order (palette rows appended in this order)
P3C_BUDGET = {
    "clock_courtyard": "courtyard",
    "covered_bridge": "bridge",
    "tf_corridor_east": "corridor",
    "hospital_wing": "room",
    "owl_loft": "tower",
    "l7_corridor_east": "corridor",
    "dungeon_cellars": "corridor",
}
# The covered bridge (a new kind: trusses, a shingled roof, open sides at railing height).
BUDGETS["bridge"] = Budget(6, (10_000, 30_000))
AO_RADIUS["bridge"] = 2.0
SLICE_ADD_MAX["bridge"] = 4_000

# -- the moving staircases (Wings A, the Grand Staircase cells) ------------------------------
#
# Each moving flight is ONE node, modelled at rest in position A (AO baked there), its
# railings and nosings included, in the cell of its A surface (grand_staircase_upper for all
# three), named SWING_NODE, its origin at the flight's pivot (stairs.json `pivot` [x, z], at
# the flight's lowest y), with extras.kit_anim {"kind": "swing", "flight", "pivot": [x, z],
# "angle"} (radians about +y in the layout's right-handed frame: a point (dx, dz) from the
# pivot goes to (dx cos a + dz sin a, -dx sin a + dz cos a)). The client turns the node by
# angle x the swing's progress (sim_core::stairs). The B surface draws nothing static (the
# kit and the greybox see the layout with every flight at A: layout_lib.base_state).
#
# The landing gates are data, not cell geometry: stairs.json lists every gate (its flight,
# position, hinge ends and the landing side). A gate is drawn as a stretch of the staircase's
# own balustrade (kit_balustrade_gothic between the hinges' newels): ONE prop, RAIL_PROP
# (client/public/assets/instances/props/stair_rail.glb, castle_trim, its origin at the first
# hinge, along +x, centred on the gate line), which the client draws for every gate as thin
# instances (one draw in all) and conjures in (closed) and out (open) under a puff of smoke.
ANIM_SWING = "swing"
SWING_NODE = "{cell}.castle_props.swing_{flight}"
# (Wings A) The swing groups' names join the node-name groups (`swing_<flight>`).
GROUP_RE = re.compile(GROUP_RE.pattern[:-2] + r"|swing_flight_[0-9]_[0-9])$")
# (Wings A) The Clock Tower Courtyard faces the grounds on its east side (its facade: the
# @ext clones, the parapet and pinnacles; its south wall is cut to a parapet by assemble.py).
M5C_FACADE_CELLS["clock_courtyard"] = ("e",)
SWING_PIVOT_CLEAR = 2.0  # the flight's first tread is drawn inside this radius of the pivot, so
#                          the swept corners never cross the gallery edge or the pier's top
RAIL_PROP = "stair_rail"
GATE_WIDTH = 4.0  # every gate's hinges are this far apart (m): the rail spans them
RAIL_TRIS = 400
# The far silhouette (exterior.json, contracts): the east wing's cells joined hlod_court and
# the seventh-floor east corridor hlod_west_range; the west range's mass is split round the
# Clock Tower passage; the cellars have no outside. N8 and the Owl Loft stand on their own
# beside the HLOD's masses: Wings A gives N8 a chunk (hlod_clock_court) and Wings B the Owl Loft
# (hlod_owl_loft); each empties its own pending tuple and sets its chunk count to 1 then (the
# chunk test skips pending cells; the chunk range and the 16-draw total grow by the chunks).
P3C_HLOD_PENDING_A = ()  # Wings A: hlod_clock_court stands in for the Clock Tower Courtyard and the bridge
P3C_HLOD_PENDING_B = ()  # Wings B: hlod_owl_loft stands in for the Owl Loft
P3C_HLOD_PENDING = P3C_HLOD_PENDING_A + P3C_HLOD_PENDING_B
P3C_HLOD_CHUNKS_A = 1  # Wings A: hlod_clock_court
P3C_HLOD_CHUNKS_B = 1  # Wings B: hlod_owl_loft
P3A_HLOD_CHUNKS = (P3A_HLOD_CHUNKS[0], P3A_HLOD_CHUNKS[1] + P3C_HLOD_CHUNKS_A + P3C_HLOD_CHUNKS_B)
P3C_HLOD_DRAWS_MAX = 16 + 2 * (P3C_HLOD_CHUNKS_A + P3C_HLOD_CHUNKS_B)  # the whole far castle
# The Owl Loft's owls (Wings B): the cell exports one empty marker node per owl,
# OWL_MARKER, with extras.kit_anim {"kind": "owl", "phase"} (its perch's place and facing);
# the owl itself is one prop, OWL_PROP (client/public/assets/instances/props/owl.glb: two
# meshes, `owl_body` and `owl_head`, the head's origin at the neck, palette colours, at most
# OWL_TRIS), which the client draws as thin instances at the markers (two draws for every owl),
# bobbing and turning its head by each marker's phase.
OWL_MARKER = "{cell}.owl_{k}"
OWL_PROP = "owl"
OWL_TRIS = 600
OWLS = (6, 12)  # owls on the loft's perches
P3C_GS_DRAWS_ADD = 3  # the three swing nodes over the Grand Staircase's budget (gates: 1 draw, client)
BUDGETS["grand_staircase"] = Budget(22 + P3C_GS_DRAWS_ADD, (250_000, 320_000), 180_000, 2)


# =====================================================================================
# Secrets and the map (docs/architecture.md "Secrets and the map"; docs/art/castle-kit.md).
# The five hidden passages (layout surfaces with `secret: true`, their cells
# `"build": false` in castle_kit.json until they were kitted in the new kind
# `passage`) and the walk-through entrances. data/world/secrets.json names each passage,
# its surfaces and its two doors; the kit only draws them.
# =====================================================================================

P3D_CELLS = {
    "passage_tapestry": "secrets",  # P1: L5 corridor's south wall (a tapestry) -> the L4 vestibule
    "passage_panel": "secrets",  # P2: the Trophy Room's west wall (a sliding panel) -> the First-Floor North Corridor
    "passage_painting": "secrets",  # P3: the library's east wall (a painting) -> the Charms corridor
    "passage_statue": "secrets",  # P4: the Long Gallery's south wall (a statue's back) -> the Great Hall
    "passage_dungeon": "secrets",  # P5: the dungeon corridor's east wall -> the dungeon cellars' foot landing
}
P3D_BUDGET = {cell: "passage" for cell in P3D_CELLS}
# A hidden passage: narrow (2 m), low (3.5 m over a landing), rough stone, a few lanterns.
BUDGETS["passage"] = Budget(5, (5_000, 15_000))
AO_RADIUS["passage"] = 1.5
SLICE_ADD_MAX["passage"] = 3_000
# The walk-through entrances: each passage door (a `door` surface with `secret: true`, 2 m
# wide, 3.5 m high) is covered on its public side by one piece (pieces/secrets.py), a
# moving node in the neighbour's cell, `ENTRANCE_NODE`, with extras.kit_anim
# {"kind": "entrance", "door": <door surface id>, "style": one of ENTRANCE_STYLES}: the
# client sways the cloth or slides the panel while a character stands on that door surface
# (no collision: walls stay the absence of ground). The room side never shows an arch.
ANIM_ENTRANCE = "entrance"
ENTRANCE_NODE = "{cell}.castle_props.entrance_{door}"
ENTRANCE_STYLES = ("tapestry", "panel", "painting", "statue", "wall")
# The Wishing Room's door: one node in l7_corridor_east, WISHING_ROOM_DOOR_NODE, with
# extras.kit_anim {"kind": "wishing_room_door"}, hidden until the server opens the door for the
# player (the client fades it in at the `wishing_room_door` interactable on the blank wall).
ANIM_WISHING_ROOM_DOOR = "wishing_room_door"
WISHING_ROOM_DOOR_NODE = "l7_corridor_east.castle_props.wishing_room_door"
# The far silhouette: the passages beside outer walls join their neighbours' chunks (Secrets
# builder: P1 and P3 hlod_west_wing, P2 hlod_court, P4 hlod_great_hall; then this is empty);
# P5 is underground (exterior.json `no_outside`).
P3D_HLOD_PENDING = ()  # (Secrets) the four passages are in their chunks (exterior.json)
# (Secrets) The entrances' and the door's moving nodes are named groups: entrance_<door surface
# id> (one per passage door, in the public room's cell) and wishing_room_door.
GROUP_RE = re.compile(GROUP_RE.pattern[:-2] + r"|entrance_sp_[a-z0-9_]+|wishing_room_door)$")
# The four common-room entrances (the guardian portrait, the knocker door, the
# hidden stone door, the barrel stack: `door` surfaces with a `house`) are moving entrance nodes
# like the passages' (hero id entrance_<door surface id>, ENTRANCE_NODE per material, kit_anim
# {"kind": "entrance", "door", "style"}); the client opens one only for its house's characters.
HOUSE_ENTRANCE_STYLES = {"kit_portrait_guardian_a": "painting", "kit_door_knocker_a": "painting",
                         "kit_door_stone_hidden": "wall", "kit_barrel_stack": "panel"}
GROUP_RE = re.compile(GROUP_RE.pattern[:-2] + r"|entrance_cr_[a-d]_[a-z]+)$")
# The knocker door's node is three meshes (props, trim, wood): the tower (west_tower_stair, the only
# cell of its kind) gets one draw more.
BUDGETS["tower"] = Budget(BUDGETS["tower"].draws + 1, BUDGETS["tower"].tris, BUDGETS["tower"].drawn, BUDGETS["tower"].cells)
# Knocker door opens as a pair: the knocker door is two
# leaves that open from the centre, style "double". Each leaf is its own group, the entrance's
# group plus LEAF_GROUP's suffix (`entrance_cr_c_door_leaf_l` / `_leaf_r`), one node per material,
# kit_anim {"kind": "entrance", "door", "style": "double", "leaf": "l" | "r", "yaw"} and its origin
# on its hinge (the leaf's outer edge, on the back face, at the floor); the client turns each leaf
# about its own origin, both into the passage (away from the corridor), DOUBLE_DOOR_TURN. A
# piece tags a face's sub-group with a leading SUB_GROUP ("+leaf_l"): the placement's group plus
# "_leaf_l" (assemble.piece_faces); with no placement group the face stays in the main mesh.
ENTRANCE_STYLES = ENTRANCE_STYLES + ("double",)
HOUSE_ENTRANCE_STYLES["kit_door_knocker_a"] = "double"
ENTRANCE_LEAVES = ("l", "r")
# How far each leaf turns open (the client's castle/entrances.ts DOUBLE_TURN): the knocker door's
# 2 m leaves hang 0.35 m into a 2 m wall, so turned further than about 51 degrees the tall
# meeting stiles' tops come out through the common room's wall above its 4 x 4.5 arch.
DOUBLE_DOOR_TURN = math.radians(50.0)
SUB_GROUP = "+"
LEAF_TAG = SUB_GROUP + "leaf_{}"  # a piece's face tag for leaf l / r


def sub_group(group: str | None, tag: str | None) -> str | None:
    """A face's group: its own `tag` when absolute, `<group>_<rest>` when the tag is a
    sub-group (SUB_GROUP + rest), else the placement's `group`."""
    if tag is None:
        return group
    if tag.startswith(SUB_GROUP):
        return None if group is None else f"{group}_{tag[len(SUB_GROUP):]}"
    return tag


def leaf_group(group: str, leaf: str) -> str:
    """The group of leaf `leaf` ("l" or "r") of a double entrance's group."""
    return sub_group(group, LEAF_TAG.format(leaf))


GROUP_RE = re.compile(GROUP_RE.pattern[:-2] + r"|entrance_cr_[a-d]_[a-z]+_leaf_[lr])$")
# Two leaves of three meshes each: three draws more than the one node (the door shows only
# where the landing or the common room's wall is in view).
BUDGETS["tower"] = Budget(BUDGETS["tower"].draws + 3, BUDGETS["tower"].tris, BUDGETS["tower"].drawn, BUDGETS["tower"].cells)
