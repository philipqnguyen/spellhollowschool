"""Castle M5b upper-room pieces (docs/art/castle-kit.md "Pieces"; the Upper rooms
builder): common room C's starry dome, the scholar statue, the bookcases,
the trophy case with its shields and plaques, the Hanging Armillary and the
library's floor map; and two small dressing pieces the rooms need, the
window drapes and the reading lamp.

Frames as geom.py and props.json `kit`: x along the wall (to the right seen
from the room), y up, z out of the wall face into the room; origin at the
bottom centre (a wall piece's back on the wall plane z = 0). The dome's
origin is the room's centre on its springing line; the armillary's the
floor under its landmark; the floor map's its centre on the floor.

Everything is our own invented content: the statue is the M5a robed scholar
(no real person) on a pedestal, the trophies are plain
cups, plates and shields with simple invented devices (a chevron, a bar, a
star), the floor map is an invented island plan made by a few formulas, the
books are coloured spines (no titles). Colours: palette names only
(grid.palette_tag, the `_doc_m5b_upper` block of castle_textures.json and
older names), the stone and wood of the trim and wood sheets, castle_sky for
the dome's panels; no colour literal here (PaletteRuleTests)."""

from __future__ import annotations

import math
import random

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import corridors, geom, openings
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall import from_mesh
from castle_kit.pieces.hall_furniture import PANEL, wbox

trim = openings.trim

# Zone -> palette name (castle_textures.json `palette`). Books on the leather tile,
# metals on the metal tile, house fields on the fabric tile, the map on the stone tile.
BOOKS = (1, 2, 3, 4, 5)
GILT, BRASS, BRONZE, STEEL, IRON, OAK, PAPER = 6, 7, 8, 9, 10, 11, 12
HOUSE0 = 13  # + 0..3: house_a..d
STATUE, PLINTH, MAP_DARK, MAP_LIGHT, MAP_SEA, MAP_GREEN, GLOW, OAK_AGED, STEEL_DARK = range(17, 26)
WINE_CLOTH = 26  # The drapes' non-house cloth (args.cloth 1; the Divination tower room)
ZONES = {
    1: "book_red", 2: "book_green", 3: "book_blue", 4: "book_brown", 5: "book_ochre",
    GILT: "gilt", BRASS: "brass", BRONZE: "bronze", STEEL: "steel", IRON: "iron", OAK: "oak", PAPER: "letter_white",
    **{HOUSE0 + i: f"house_{'abcd'[i]}" for i in range(4)},
    STATUE: "statue_stone", PLINTH: "beast_stone", MAP_DARK: "compass_dark", MAP_LIGHT: "compass_light",
    MAP_SEA: "map_sea", MAP_GREEN: "map_green", GLOW: "glow_lantern", OAK_AGED: "oak_aged", STEEL_DARK: "steel_dark",
    WINE_CLOTH: "tapestry_wine",
}
METALS = (GILT, BRASS, BRONZE, STEEL)

# Caps of the two dressing pieces (the contract's pieces have theirs in grid.py).
DRAPES = "kit_drapes"  # sized: kit_drapes_<window w>x<rod height>, a pair of curtains beside a window
DRAPES_TRIS = 400
LAMP = "kit_lamp_reading"  # a brass oil lamp with a lit chimney on a table; brings one light
LAMP_TRIS = 200


def _tile(zone: int) -> str:
    if zone in BOOKS:
        return "leather"
    if HOUSE0 <= zone < HOUSE0 + 4 or zone == WINE_CLOTH:
        return "fabric"
    if zone in (MAP_DARK, MAP_LIGHT, MAP_SEA, MAP_GREEN):
        return "glass"  # the floor map: flat inlaid colours (the stone tile's joints break up into stripes)
    if zone == PLINTH:
        return "stone"
    if zone == GLOW:
        return "glow"
    if zone in (OAK, OAK_AGED):
        return "wood"
    return "metal"


def _box(m: mk.Mesh, lo, hi, zone: int, skip=()) -> None:
    mk.box(m, lo, hi, _tile(zone), zone, skip=skip)


def _face(m: mk.Mesh, pts, zone: int, facing) -> None:
    """A polygon of the props atlas, wound to face `facing`."""
    pts = [tuple(float(c) for c in p) for p in pts]
    if geom.dot(geom.newell(pts), facing) < 0:
        pts = list(reversed(pts))
    m.poly(pts, _tile(zone), zone=zone)


def _finish(p: Piece, m: mk.Mesh) -> Piece:
    p.faces.extend(from_mesh(m, ZONES, walkthrough=p.walkthrough).faces)
    return p


def _panel_uv(x0: float, y0: float, y1: float):
    """Wood-sheet `wainscot` strip UVs for a flat panel on z = const (s = x, p up the panel)."""
    span = (y1 - y0) or 1.0
    k = min(3.2, span) / span
    return lambda pts: [(p[0] - x0, (p[1] - y0) * k) for p in pts]


def _wall_panel(f: list, x0: float, x1: float, y0: float, y1: float, z: float) -> None:
    pts = [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]
    add_face(f, pts, PANEL, _panel_uv(x0, y0, y1)(pts), (0.0, 0.0, 1.0))


# =====================================================================================
# Bookcases (kit_bookcase_<w>x<h>)
# =====================================================================================

BOOKCASE_DEPTH = 0.45  # carcass depth (grid: at most 0.5 m)
BOOKCASE_WIDTH = (1.0, 6.0)
BOOKCASE_HEIGHT = (2.0, 6.0)
CASE_SIDE = 0.06
CASE_BASE = 0.14
CASE_TOP = 0.16
CUPBOARD = 0.78  # the closed cupboard under the shelves (its top a shelf)
SHELF_PITCH = 0.46
SHELF_ROWS_MAX = 7  # taller cases get taller shelves (folios), so the triangles go with the width
SECTION_MAX = 1.3


def _book_row(m: mk.Mesh, rng: random.Random, xa: float, xb: float, y: float, clear: float, z0: float, z1: float) -> None:
    """Books standing on a shelf from xa to xb: blocks of two to five spines
    of one height (each spine its own colour), with a gap now and then and a
    few lying flat in a pile. A block's side is drawn only where it shows
    (above a shorter neighbour it touches, or beside a gap)."""
    items = []  # (kind, x, w, h, zones)
    x = xa + 0.02
    while x < xb - 0.08:
        r = rng.random()
        if r < 0.1:  # a gap
            x += rng.uniform(0.06, 0.18)
            continue
        if r < 0.18 and xb - x > 0.3:  # a pile lying flat
            w = rng.uniform(0.22, 0.3)
            items.append(("pile", x, w, clear * rng.uniform(0.25, 0.4), (rng.choice(BOOKS),)))
            x += w + 0.02
            continue
        n = rng.randint(2, 5)
        w = min(xb - x - 0.01, n * rng.uniform(0.05, 0.075))
        if w < 0.06:
            break
        h = clear * rng.uniform(0.62, 0.92)
        items.append(("block", x, w, h, tuple(rng.choice(BOOKS) for _ in range(n + 1))))
        x += w + rng.choice((0.0, 0.0, 0.01))
    for k, (kind, x, w, h, zones) in enumerate(items):
        top = y + h
        if kind == "pile":
            _box(m, (x, y, z0 + 0.02), (x + w, top, z1 - 0.01), zones[0], skip=("-y", "-z"))
            continue
        n = len(zones) - 1
        for i in range(n):  # the spines: one face each across the block's front
            a, b = x + w * i / n, x + w * (i + 1) / n
            _face(m, [(a, y, z1), (b, y, z1), (b, top, z1), (a, top, z1)], zones[i], (0.0, 0.0, 1.0))
        _face(m, [(x, top, z0), (x + w, top, z0), (x + w, top, z1), (x, top, z1)], zones[n], (0.0, 1.0, 0.0))
        for xs, sx, nb in ((x, -1.0, items[k - 1] if k > 0 else None), (x + w, 1.0, items[k + 1] if k + 1 < len(items) else None)):
            low = y
            if nb is not None and nb[0] == "block" and abs((nb[1] + nb[2] if sx < 0 else nb[1]) - xs) < 1e-9:
                low = y + nb[3]  # touching a neighbour: only the part above it shows
            if top - low > 1e-6:
                _face(m, [(xs, low, z0), (xs, low, z1), (xs, top, z1), (xs, top, z0)], zones[n], (sx, 0.0, 0.0))


def bookcase(width: float, height: float, seed: float = 0, cupboard: float = 1) -> Piece:
    """`kit_bookcase_<w>x<h>`: an oak bookcase against a wall, BOOKCASE_DEPTH
    deep: a plinth, side panels, a moulded cornice, a dark panelled back,
    uprights every SECTION_MAX at most, shelves every SHELF_PITCH filled with
    books (palette names book_*; `seed` varies them) over a closed cupboard
    (`cupboard` 1) or shelves to the floor. Walk-through like the Owl
    Exchange counter (furniture); meta "wall" breaks the plinth behind it.
    At most grid.BOOKCASE_TRIS_PER_M per metre of width."""
    if not (BOOKCASE_WIDTH[0] - 1e-9 <= width <= BOOKCASE_WIDTH[1] + 1e-9
            and BOOKCASE_HEIGHT[0] - 1e-9 <= height <= BOOKCASE_HEIGHT[1] + 1e-9):
        raise ValueError(f"kit_bookcase: width {BOOKCASE_WIDTH}, height {BOOKCASE_HEIGHT} m")
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    hw, d = width / 2.0, BOOKCASE_DEPTH
    x0, x1 = -hw + CASE_SIDE, hw - CASE_SIDE
    y0, y1 = CASE_BASE, height - CASE_TOP
    # Plinth (a little wider and deeper than the carcass), sides, cornice.
    wbox(f, (-hw - 0.02, 0.0, 0.0), (hw + 0.02, CASE_BASE, d + 0.03), skip=("-z", "-y"))
    for sx in (-1.0, 1.0):
        a, b = sorted((sx * hw, sx * (hw - CASE_SIDE)))
        wbox(f, (a, y0, 0.0), (b, y1, d), skip=("-z", "-y", "+y"), axis=2)
    wbox(f, (-hw - 0.05, y1, 0.0), (hw + 0.05, y1 + 0.06, d + 0.02), skip=("-z",))
    wbox(f, (-hw - 0.08, y1 + 0.06, 0.0), (hw + 0.08, height, d + 0.05), skip=("-z", "-y"))
    # The back: one dark panel.
    _wall_panel(f, x0, x1, y0, y1, 0.02)
    # Uprights between the sections (from the cupboard's top; a little behind the shelves' fronts).
    n = max(1, math.ceil((x1 - x0) / SECTION_MAX))
    xs = [x0 + (x1 - x0) * i / n for i in range(n + 1)]
    base = y0 + (CUPBOARD if cupboard >= 0.5 else 0.0)
    for x in xs[1:-1]:
        wbox(f, (x - 0.02, base, 0.02), (x + 0.02, y1, d - 0.02), skip=("-z", "-y", "+y"), axis=2)
    # Shelves: the cupboard's top (or the plinth's) and every pitch above.
    if cupboard >= 0.5:
        # Closed doors (a panel face) and the cupboard's top board.
        for a, b in zip(xs, xs[1:]):
            _wall_panel(f, a + 0.03, b - 0.03, y0 + 0.04, base - 0.04, d - 0.02)
        wbox(f, (x0, base - 0.04, 0.02), (x1, base, d + 0.01), skip=("-z", "-x", "+x"))
        for a, b in zip(xs, xs[1:]):  # door frames' stiles
            for x in (a + 0.015, b - 0.015):
                wbox(f, (x - 0.015, y0, d - 0.04), (x + 0.015, base - 0.04, d - 0.01), skip=("-z", "-y", "+y"), axis=2)
    rows = min(SHELF_ROWS_MAX, max(1, round((y1 - base) / SHELF_PITCH)))
    pitch = (y1 - base) / rows
    rng = random.Random(int(round(seed)) * 7919 + int(round(width * 100)) * 31 + int(round(height * 100)))
    for k in range(rows):
        yb = base + k * pitch
        if k > 0:
            wbox(f, (x0, yb - 0.03, 0.02), (x1, yb, d - 0.01), skip=("-z", "-x", "+x"))
        for a, b in zip(xs, xs[1:]):
            _book_row(m, rng, a + 0.02, b - 0.02, yb, pitch - 0.05, 0.08, d - 0.06)
    p.meta.update(width=width, height=height, depth=d + 0.05, wall=(-hw - 0.08, hw + 0.08, 0.0, height), rows=rows)
    return _finish(p, m)


# =====================================================================================
# The trophy case (kit_trophy_case) with its shields and plaques
# =====================================================================================

TROPHY_WIDTH = (1.6, 3.0)
TROPHY_DEPTH = (0.5, 0.42)  # the cupboard's and the display's depth
TROPHY_COUNTER = 0.92
TROPHY_TOP = 2.6
TROPHY_SHELVES = (TROPHY_COUNTER, 1.48, 2.04)
CREST = (2.8, 4.1)  # the shield board's foot and head over the floor


def _shield_outline(cx: float, cy: float, w: float, h: float) -> list[tuple[float, float]]:
    """A heater shield (flat top, pointed foot), counter-clockwise."""
    hw = w / 2.0
    return [(cx - hw, cy + h * 0.5), (cx - hw, cy - h * 0.05), (cx - hw * 0.55, cy - h * 0.38), (cx, cy - h * 0.5),
            (cx + hw * 0.55, cy - h * 0.38), (cx + hw, cy - h * 0.05), (cx + hw, cy + h * 0.5)]


def _shield(m: mk.Mesh, cx: float, cy: float, w: float, h: float, z: float, field: int, device: int, metal: int) -> None:
    """A wall shield: a metal rim, a field (house colour) a little proud,
    and an invented device on it: 0 a chevron, 1 a bar, 2 a five-point star."""
    rim = _shield_outline(cx, cy, w, h)
    mk.prism(m, rim, z, z + 0.04, "metal", metal, back=False)
    inner = _shield_outline(cx, cy + 0.01, w * 0.84, h * 0.84)
    mk.prism(m, inner, z + 0.04, z + 0.055, _tile(field), field, back=False, sides=False)
    zf = z + 0.06
    if device == 0:
        t = h * 0.12
        pts = [(cx - w * 0.38, cy - h * 0.12), (cx, cy + h * 0.2), (cx + w * 0.38, cy - h * 0.12),
               (cx + w * 0.38, cy - h * 0.12 + t), (cx, cy + h * 0.2 + t), (cx - w * 0.38, cy - h * 0.12 + t)]
        for a, b in ((0, 1), (1, 2)):
            q = [pts[a], pts[b], pts[5 - b], pts[5 - a]]
            _face(m, [(x, y, zf) for x, y in q], metal, (0.0, 0.0, 1.0))
    elif device == 1:
        _face(m, [(cx - w * 0.4, cy - h * 0.06, zf), (cx + w * 0.4, cy - h * 0.06, zf), (cx + w * 0.4, cy + h * 0.08, zf),
                  (cx - w * 0.4, cy + h * 0.08, zf)], metal, (0.0, 0.0, 1.0))
    else:
        star = []
        for k in range(10):
            a = math.pi / 2 + k * math.pi / 5
            r = (0.3 if k % 2 == 0 else 0.13) * w
            star.append((cx + r * math.cos(a), cy + 0.04 * h + r * math.sin(a)))
        for k in range(10):
            a, b = star[k], star[(k + 1) % 10]
            _face(m, [(cx, cy + 0.04 * h, zf), (a[0], a[1], zf), (b[0], b[1], zf)], metal, (0.0, 0.0, 1.0))


def _cup(m: mk.Mesh, x: float, y: float, z: float, s: float, zone: int) -> None:
    """A two-handled cup on a foot, `s` its scale (1: 0.36 m tall)."""
    prof = [(0.07, 0.0), (0.075, 0.025), (0.03, 0.05), (0.022, 0.13), (0.06, 0.16), (0.1, 0.27), (0.095, 0.33)]
    mk.lathe(m, [(r * s, h * s) for r, h in prof], 8, _tile(zone), zone, (x, y, z), cap_top=True)
    for sx in (-1.0, 1.0):
        mk.rod(m, (x + sx * 0.085 * s, y + 0.27 * s, z), (x + sx * 0.14 * s, y + 0.2 * s, z), 0.012, 4, _tile(zone), zone)
        mk.rod(m, (x + sx * 0.14 * s, y + 0.2 * s, z), (x + sx * 0.06 * s, y + 0.14 * s, z), 0.012, 4, _tile(zone), zone)


def _plate(m: mk.Mesh, x: float, y: float, z: float, r: float, zone: int) -> None:
    """A presentation plate standing on edge against the back, a boss in its middle."""
    n = 12
    ring = [(x + r * math.cos(2 * math.pi * k / n), y + r + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    mk.prism(m, ring, z, z + 0.03, _tile(zone), zone, back=False)
    boss = [(x + r * 0.35 * math.cos(2 * math.pi * k / 6), y + r + r * 0.35 * math.sin(2 * math.pi * k / 6)) for k in range(6)]
    mk.prism(m, boss, z + 0.03, z + 0.05, _tile(zone), zone, back=False)


def _orb(m: mk.Mesh, x: float, y: float, z: float, zone: int, stand: int) -> None:
    """An orb on a turned stand."""
    mk.lathe(m, [(0.06, 0.0), (0.06, 0.02), (0.025, 0.05), (0.03, 0.12)], 6, _tile(stand), stand, (x, y, z), cap_top=True)
    mk.lathe(m, [(0.0, 0.0), (0.06, 0.03), (0.08, 0.09), (0.06, 0.15), (0.0, 0.18)], 8, _tile(zone), zone, (x, y + 0.11, z))


def _obelisk(m: mk.Mesh, x: float, y: float, z: float, zone: int, base: int) -> None:
    """A small obelisk award on a block."""
    _box(m, (x - 0.07, y, z - 0.07), (x + 0.07, y + 0.06, z + 0.07), base, skip=("-y",))
    mk.lathe(m, [(0.045, 0.0), (0.03, 0.26), (0.0, 0.31)], 4, _tile(zone), zone, (x, y + 0.06, z), phase=math.pi / 4)


def trophy_case(width: float = 2.4, seed: float = 0, house: float = 0, crest: float = 1) -> Piece:
    """`kit_trophy_case`: an oak case `width` wide (TROPHY_WIDTH) against the
    wall: a closed cupboard to TROPHY_COUNTER, open display shelves to
    TROPHY_TOP with cups, presentation plates, orbs and obelisks in gilt,
    brass, bronze and steel (`seed` arranges them), a moulded cornice, and
    (`crest` 1) a shield board above it (CREST): a house shield (`house`
    0-3: house_a..d) with an invented device between two brass plaques.
    Walk-through like the bookcases; meta "wall". At most
    grid.TROPHY_CASE_TRIS."""
    if not TROPHY_WIDTH[0] - 1e-9 <= width <= TROPHY_WIDTH[1] + 1e-9:
        raise ValueError(f"kit_trophy_case: width {TROPHY_WIDTH} m")
    hcol = int(round(house))
    if not 0 <= hcol <= 3:
        raise ValueError("kit_trophy_case: house 0..3")
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    hw = width / 2.0
    dl, du = TROPHY_DEPTH
    side = 0.06
    rng = random.Random(int(round(seed)) * 104729 + int(round(width * 100)))
    # The cupboard: plinth, body, panel doors, the counter top.
    wbox(f, (-hw - 0.02, 0.0, 0.0), (hw + 0.02, 0.1, dl + 0.03), skip=("-z", "-y"))
    wbox(f, (-hw, 0.1, 0.0), (hw, TROPHY_COUNTER - 0.05, dl), skip=("-z", "-y", "+y", "+z"))
    doors = max(2, round(width / 0.8))
    for i in range(doors):
        a = -hw + width * i / doors
        b = a + width / doors
        _wall_panel(f, a + 0.03, b - 0.03, 0.14, TROPHY_COUNTER - 0.09, dl)
        mk.rod(m, (b - 0.12 if i % 2 == 0 else a + 0.12, 0.55, dl), (b - 0.12 if i % 2 == 0 else a + 0.12, 0.55, dl + 0.04),
               0.015, 4, "metal", BRASS)
    # The frame of the doors: rails and stiles on the front.
    wbox(f, (-hw, 0.1, dl), (hw, 0.14, dl + 0.02), skip=("-z", "-y"))
    wbox(f, (-hw, TROPHY_COUNTER - 0.09, dl), (hw, TROPHY_COUNTER - 0.05, dl + 0.02), skip=("-z",))
    for i in range(doors + 1):
        x = -hw + width * i / doors
        xa, xb = max(-hw, x - 0.03), min(hw, x + 0.03)
        wbox(f, (xa, 0.14, dl), (xb, TROPHY_COUNTER - 0.09, dl + 0.02), skip=("-z", "-y", "+y"), axis=2)
    wbox(f, (-hw - 0.03, TROPHY_COUNTER - 0.05, 0.0), (hw + 0.03, TROPHY_COUNTER, dl + 0.04), skip=("-z",))
    # The display: back panel, sides, two shelves, the cornice.
    _wall_panel(f, -hw + side, hw - side, TROPHY_COUNTER, TROPHY_TOP, 0.02)
    for sx in (-1.0, 1.0):
        a, b = sorted((sx * hw, sx * (hw - side)))
        wbox(f, (a, TROPHY_COUNTER, 0.0), (b, TROPHY_TOP, du), skip=("-z", "-y", "+y"), axis=2)
    for y in TROPHY_SHELVES[1:]:
        wbox(f, (-hw + side, y - 0.03, 0.02), (hw - side, y, du - 0.01), skip=("-z", "-x", "+x"))
        _box(m, (-hw + side, y - 0.035, du - 0.012), (hw - side, y - 0.025, du - 0.002), BRASS, skip=("-z", "-x", "+x"))
    wbox(f, (-hw - 0.04, TROPHY_TOP, 0.0), (hw + 0.04, TROPHY_TOP + 0.06, du + 0.03), skip=("-z", "-y"))
    wbox(f, (-hw - 0.08, TROPHY_TOP + 0.06, 0.0), (hw + 0.08, TROPHY_TOP + 0.16, du + 0.07), skip=("-z",))
    # The trophies on the counter and the two shelves.
    slots = max(3, int((width - 2 * side) / 0.42))
    for y in TROPHY_SHELVES:
        for i in range(slots):
            x = -hw + side + (width - 2 * side) * (i + 0.5) / slots
            kind = rng.random()
            metal = rng.choice(METALS)
            z = du * 0.55
            if kind < 0.42:
                _cup(m, x, y, z, rng.uniform(0.8, 1.15), metal)
            elif kind < 0.64:
                _plate(m, x, y, 0.07, rng.uniform(0.13, 0.17), metal)
            elif kind < 0.8:
                _orb(m, x, y, z, rng.choice((GILT, STEEL, BRONZE)), BRASS if metal != BRASS else BRONZE)
            elif kind < 0.92:
                _obelisk(m, x, y, z, metal, OAK_AGED)
            else:
                _shield(m, x, y + 0.24, 0.28, 0.34, 0.04, HOUSE0 + rng.randint(0, 3), rng.randint(0, 2), GILT)
    top = TROPHY_TOP + 0.16
    if crest >= 0.5:
        # The shield board: an oak panel with a house shield and two plaques.
        c0, c1 = CREST
        bw = min(width - 0.2, 2.6) / 2.0
        wbox(f, (-bw, c0, 0.0), (bw, c0 + 0.06, 0.06), skip=("-z",))
        wbox(f, (-bw, c1 - 0.06, 0.0), (bw, c1, 0.06), skip=("-z",))
        for sx in (-1.0, 1.0):
            a, b = sorted((sx * bw, sx * (bw - 0.06)))
            wbox(f, (a, c0 + 0.06, 0.0), (b, c1 - 0.06, 0.06), skip=("-z", "-y", "+y"), axis=2)
        _wall_panel(f, -bw + 0.06, bw - 0.06, c0 + 0.06, c1 - 0.06, 0.03)
        cy = (c0 + c1) / 2.0
        _shield(m, 0.0, cy, 0.78, 0.98, 0.03, HOUSE0 + hcol, rng.randint(0, 2), GILT)
        for sx in (-1.0, 1.0):
            px = sx * bw * 0.6
            _box(m, (px - 0.24, cy - 0.17, 0.03), (px + 0.24, cy + 0.17, 0.05), BRASS, skip=("-z",))
            _box(m, (px - 0.19, cy - 0.12, 0.05), (px + 0.19, cy + 0.12, 0.055), BRONZE, skip=("-z", "-y", "+y", "-x", "+x"))
        top = c1
    p.meta.update(width=width, depth=dl + 0.07, height=top, wall=(-hw - 0.08, hw + 0.08, 0.0, top))
    return _finish(p, m)


# =====================================================================================
# The Hanging Armillary (kit_armillary)
# =====================================================================================

ARMILLARY_R = 1.6
TUBE = 0.045


def _ring(m: mk.Mesh, c, u, v, r: float, t: float, zone: int, segs: int = 24, sides: int = 4, band: float = 0.0) -> None:
    """A ring of radius r in the plane of the unit vectors u, v round c: a
    tube `t` thick (or, `band` > 0, a flat band that wide across the plane)."""
    w = geom.unit(geom.cross(u, v))
    for i in range(segs):
        a0, a1 = 2 * math.pi * i / segs, 2 * math.pi * (i + 1) / segs

        def at(a, rr, off):
            d = (u[0] * math.cos(a) + v[0] * math.sin(a), u[1] * math.cos(a) + v[1] * math.sin(a),
                 u[2] * math.cos(a) + v[2] * math.sin(a))
            return tuple(c[k] + d[k] * rr + w[k] * off for k in range(3)), d

        if band > 0.0:
            # A flat band: outer and inner faces and its two edges.
            prof = [(r + t, -band / 2), (r + t, band / 2), (r - t, band / 2), (r - t, -band / 2)]
        else:
            prof = [(r + t * math.cos(2 * math.pi * k / sides + math.pi / sides), t * math.sin(2 * math.pi * k / sides + math.pi / sides))
                    for k in range(sides)]
        for k in range(len(prof)):
            (ra, oa), (rb, ob) = prof[k], prof[(k + 1) % len(prof)]
            pa0, d0 = at(a0, ra, oa)
            pb0, _ = at(a0, rb, ob)
            pb1, d1 = at(a1, rb, ob)
            pa1, _ = at(a1, ra, oa)
            # Outward from the tube's axis at this point.
            mid = geom.unit(tuple(d0[q] + d1[q] for q in range(3)))
            out = tuple(mid[q] * ((ra + rb) / 2 - r) + w[q] * (oa + ob) / 2 for q in range(3))
            _face(m, [pa0, pb0, pb1, pa1], zone, out)


def armillary(centre: float = 5.2, top: float = 8.4, radius: float = ARMILLARY_R, tilt: float = 0.5236) -> Piece:
    """`kit_armillary`: the Hanging Armillary, nested rings round a glowing
    orb, hung on a chain from the ceiling: origin on the floor under it, the
    rings' centre `centre` above it, the chain up to `top` (the ceiling). A
    gilt meridian (vertical) and a bronze horizon band hold a polar axis
    tilted `tilt` from the vertical; on it the equator, a broad ecliptic band
    with gilt studs (an invented zodiac, no signs) and a colure ring; a
    bronze orb glowing at the middle (glow_lantern) brings one warm light.
    Its lowest point stays OVERHEAD_CLEARANCE over the floor. At most
    grid.ARMILLARY_TRIS. Walk-through (it hangs over the players)."""
    r = radius
    if centre - r - 0.2 < G.OVERHEAD_CLEARANCE or top <= centre + r:
        raise ValueError("kit_armillary: its lowest ring must stay 2.4 m up and the chain must reach above it")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    c = (0.0, centre, 0.0)
    X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    # The fixed frame: meridian (vertical, x-y plane) and horizon band (horizontal).
    _ring(m, c, X, Y, r + 0.12, TUBE * 1.2, GILT, segs=28)
    _ring(m, c, X, Z, r + 0.12, 0.03, BRONZE, segs=28, band=0.16)
    # The polar axis, tilted in the meridian's plane.
    ax = (math.sin(tilt), math.cos(tilt), 0.0)
    e1 = (math.cos(tilt), -math.sin(tilt), 0.0)  # in the meridian plane, perpendicular to the axis
    mk.rod(m, tuple(c[k] - ax[k] * (r + 0.12) for k in range(3)), tuple(c[k] + ax[k] * (r + 0.12) for k in range(3)), 0.025, 6,
           "metal", IRON)
    for sgn in (-1.0, 1.0):
        mk.lathe(m, [(0.0, -0.06), (0.05, -0.03), (0.05, 0.03), (0.0, 0.06)], 6, "metal", GILT,
                 tuple(c[k] + sgn * ax[k] * (r + 0.2) for k in range(3)))
    # The equator, the colure and the ecliptic band round the axis.
    _ring(m, c, e1, Z, r * 0.86, TUBE, GILT)
    _ring(m, c, ax, Z, r * 0.8, TUBE, BRONZE)
    ecl = 0.41  # the band's tilt from the equator
    v = tuple(Z[k] * math.cos(ecl) + ax[k] * math.sin(ecl) for k in range(3))
    _ring(m, c, e1, v, r * 0.72, 0.025, BRONZE, segs=24, band=0.2)
    n = geom.unit(geom.cross(e1, v))
    for k in range(12):
        a = 2 * math.pi * (k + 0.5) / 12
        d = tuple(e1[q] * math.cos(a) + v[q] * math.sin(a) for q in range(3))
        pos = tuple(c[q] + d[q] * (r * 0.72 + 0.03) for q in range(3))
        mk.lathe(m, [(0.0, -0.035), (0.035, 0.0), (0.0, 0.035)], 4, "metal", GILT, pos)
        _ = n
    # The orb: a glowing core in a bronze cage of four ribs.
    mk.lathe(m, [(0.0, -0.26), (0.16, -0.21), (0.25, -0.08), (0.25, 0.08), (0.16, 0.21), (0.0, 0.26)], 10, "glow", GLOW, c)
    for k in range(4):
        a = k * math.pi / 4
        _ring(m, c, (math.cos(a), 0.0, math.sin(a)), Y, 0.3, 0.015, BRONZE, segs=12, sides=3)
    # The chain from the meridian's top to the ceiling, a ceiling rose.
    ytop = centre + r + 0.12 + TUBE * 1.2
    links = max(1, min(int((top - ytop) / 0.16), 30))
    for i in range(links):
        ya = ytop + (top - ytop) * i / links
        yb = ya + (top - ytop) / links * 0.85
        if i % 2 == 0:
            mk.box(m, (-0.035, ya, -0.008), (0.035, yb, 0.008), "metal", IRON, skip=("+y", "-y"))
        else:
            mk.box(m, (-0.008, ya, -0.035), (0.008, yb, 0.035), "metal", IRON, skip=("+y", "-y"))
    mk.lathe(m, [(0.0, top - 0.12), (0.22, top - 0.08), (0.28, top)], 10, "metal", BRONZE, cap_top=False)
    lo = min(v[1] for v in m.verts)
    p.meta.update(centre=centre, top=top, radius=r, lowest=lo,
                  lights=[{"id": "orb", "pos": [0.0, centre, 0.0], "glow": "candle", "intensity": 18.0, "range": 13.0}])
    return _finish(p, m)


# =====================================================================================
# The library's floor map (kit_floor_inlay_map_<w>x<d>)
# =====================================================================================

MAP_BORDER = (0.3, 0.06)  # the dark frame band and the brass line inside it
MAP_CELL = 0.14  # the plan's grid
MAP_STAR = 0.22  # the compass star's square, a fraction of the field's depth


def _map_kind(u: float, v: float) -> int:
    """The invented island plan at (u, v) in -1..1 across the field (u east,
    v south): the sea round a lobed island, two forests, a lake with a river
    to the sea, a walled town in the middle with four roads out of it."""
    # The island: a lobed shape (three bumps), a bay in the south-west.
    ang = math.atan2(v, u)
    rad = math.hypot(u * 0.98, v * 1.06)
    edge = 0.84 + 0.08 * math.sin(3 * ang + 0.6) + 0.04 * math.sin(7 * ang + 1.3)
    if math.hypot(u + 0.62, v - 0.55) < 0.22:
        return MAP_SEA
    if rad > edge:
        return MAP_SEA
    # The town: an octagonal wall ring and its core.
    town = max(abs(u * 1.3), abs(v * 1.3), (abs(u * 1.3) + abs(v * 1.3)) * 0.72)
    if town < 0.12:
        return BRASS
    if town < 0.2:
        return MAP_LIGHT
    if town < 0.25:
        return MAP_DARK
    # Roads: four, from the town's gates along the axes (a little wobble).
    if abs(v - 0.04 * math.sin(u * 9)) < 0.035 and abs(u) < edge * 0.95:
        return MAP_DARK
    if abs(u - 0.04 * math.sin(v * 8)) < 0.04 and abs(v) < edge * 0.95:
        return MAP_DARK
    # The lake in the north-east and its river south-east to the sea.
    if math.hypot((u - 0.45) * 1.1, v + 0.38) < 0.17:
        return MAP_SEA
    if abs(v - (-0.25 + 0.9 * (u - 0.5) + 0.06 * math.sin(u * 14))) < 0.035 and u > 0.45:
        return MAP_SEA
    # Forests: the west and the south.
    if math.hypot(u + 0.45, v + 0.2) < 0.24 + 0.04 * math.sin(ang * 5) or math.hypot(u - 0.2, v - 0.5) < 0.2:
        return MAP_GREEN
    return MAP_LIGHT


def floor_map(width: float, depth: float) -> Piece:
    """`kit_floor_inlay_map_<w>x<d>`: a flush floor inlay of an invented
    island plan (`_map_kind`), `width` along x by `depth` along z, centred on
    its origin: a dark frame and a brass line round the field, the plan
    sampled on a MAP_CELL grid (runs of one stone merged), a compass star in
    its north-west corner. Like the floor compass it draws its whole square
    (meta "floor_cut": rules.floor_cuts takes it out of the floor). Stones
    and brass by palette name (compass_dark, compass_light, map_sea,
    map_green, brass). At most grid.FLOOR_MAP_TRIS."""
    if not (2.0 <= width <= 12.0 and 2.0 <= depth <= 12.0):
        raise ValueError("kit_floor_inlay_map: 2..12 m each way")
    p = Piece("")
    m = mk.Mesh()
    up = (0.0, 1.0, 0.0)
    hw, hd = width / 2.0, depth / 2.0

    def rect(x0, z0, x1, z1, zone):
        _face(m, [(x0, 0.0, z0), (x1, 0.0, z0), (x1, 0.0, z1), (x0, 0.0, z1)], zone, up)

    # The frame (n and s bands across, w and e between) and the brass line.
    x0, z0, x1, z1 = -hw, -hd, hw, hd
    for band, zone in ((MAP_BORDER[0], MAP_DARK), (MAP_BORDER[1], BRASS)):
        rect(x0, z0, x1, z0 + band, zone)
        rect(x0, z1 - band, x1, z1, zone)
        rect(x0, z0 + band, x0 + band, z1 - band, zone)
        rect(x1 - band, z0 + band, x1, z1 - band, zone)
        x0, z0, x1, z1 = x0 + band, z0 + band, x1 - band, z1 - band
    fw, fd = x1 - x0, z1 - z0
    nx, nz = max(4, round(fw / MAP_CELL)), max(4, round(fd / MAP_CELL))
    cw, cd = fw / nx, fd / nz
    # The compass star's square in the north-west corner (whole cells).
    sq = max(2, round(MAP_STAR * fd / cd))
    for j in range(nz):
        za, zb = z0 + j * cd, z0 + (j + 1) * cd
        i = 0
        while i < nx:
            if j < sq and i < sq:
                i = sq
                continue
            v = -1.0 + 2.0 * (j + 0.5) / nz
            k = _map_kind(-1.0 + 2.0 * (i + 0.5) / nx, v)
            e = i + 1
            while e < nx and not (j < sq and e < sq) and _map_kind(-1.0 + 2.0 * (e + 0.5) / nx, v) == k:
                e += 1
            rect(x0 + i * cw, za, x0 + e * cw, zb, k)
            i = e
    # The compass star: eight points in brass and dark stone over the sea.
    s = sq * cw if cw < cd else sq * cd
    sx0, sz0, sx1, sz1 = x0, z0, x0 + sq * cw, z0 + sq * cd
    cx, cz = (sx0 + sx1) / 2.0, (sz0 + sz1) / 2.0
    rr = 0.45 * min(sx1 - sx0, sz1 - sz0)
    pts = []
    for k in range(16):
        a = 2 * math.pi * k / 16
        r = rr * (1.0 if k % 4 == 0 else (0.6 if k % 2 == 0 else 0.22))
        pts.append((cx + r * math.cos(a), cz + r * math.sin(a), a))
    for k in range(16):
        (ax_, az_, aa), (bx_, bz_, ab) = pts[k], pts[(k + 1) % 16]
        _face(m, [(cx, 0.0, cz), (ax_, 0.0, az_), (bx_, 0.0, bz_)], BRASS if k % 2 == 0 else MAP_DARK, up)
        # The sea between the star and its square: out to the square along both rays.
        qa, qb = _on_rect(cx, cz, aa, sx0, sz0, sx1, sz1), _on_rect(cx, cz, ab if k < 15 else 2 * math.pi, sx0, sz0, sx1, sz1)
        ring = [(ax_, az_), qa]
        for corner_a, corner in _corners_between(cx, cz, aa, ab if k < 15 else 2 * math.pi, sx0, sz0, sx1, sz1):
            ring.append(corner)
        ring += [qb, (bx_, bz_)]
        _face(m, [(x, 0.0, z) for x, z in ring], MAP_SEA, up)
    _ = s
    p.meta.update(floor_cut=[(-hw, -hd, hw, hd)], size=(width, depth), cells=(nx, nz))
    return _finish(p, m)


def _on_rect(cx, cz, a, x0, z0, x1, z1):
    """Where the ray from (cx, cz) at angle `a` leaves the rectangle."""
    c, s = math.cos(a), math.sin(a)
    ts = []
    if c > 1e-9:
        ts.append((x1 - cx) / c)
    if c < -1e-9:
        ts.append((x0 - cx) / c)
    if s > 1e-9:
        ts.append((z1 - cz) / s)
    if s < -1e-9:
        ts.append((z0 - cz) / s)
    t = min(ts)
    return (cx + t * c, cz + t * s)


def _corners_between(cx, cz, a0, a1, x0, z0, x1, z1):
    """The rectangle's corners whose angle from (cx, cz) lies between a0 and a1 (ccw)."""
    out = []
    for x, z in ((x1, z1), (x0, z1), (x0, z0), (x1, z0)):
        a = math.atan2(z - cz, x - cx) % (2 * math.pi)
        if a0 + 1e-9 < a < a1 - 1e-9:
            out.append((a, (x, z)))
    return sorted(out)


# =====================================================================================
# The starry dome (kit_dome_starry_<d>): common room C's ceiling
# =====================================================================================

DOME_RINGS = 9  # rings from the foot to the oculus
DOME_SEGS = 10  # panels across each of the four faces per ring
DOME_OCULUS = 0.1  # the oculus's half-size, a fraction of the half-span
RIB = (0.26, 0.2)  # the ribs' width and depth under the panels


def _dome_point(h: float, rise: float, face: int, th: float, x: float):
    """A point of the domical vault: the face toward wall `face` (0 north
    -z, 1 east +x, 2 south +z, 3 west -x) at ring angle th (0 at the foot,
    pi / 2 at the crown) and `x` in -1..1 across that face's ring."""
    a = h * math.cos(th)
    y = rise * math.sin(th)
    lx, lz = x * a, -a  # the north face, then turned
    c, s = [(1, 0), (0, 1), (-1, 0), (0, -1)][face]
    # Turn (lx, lz) by face * 90 degrees counter-clockwise seen from above (north -> east -> south -> west).
    return (lx * c - lz * s, y, lx * s + lz * c)


def _dome_normal(h: float, rise: float, face: int, th: float):
    """The inward (into the room, downward) normal of a face at ring angle th."""
    nz, ny = rise * math.cos(th), -h * math.sin(th)
    c, s = [(1, 0), (0, 1), (-1, 0), (0, -1)][face]
    return geom.unit((-nz * s, ny, nz * c))


def _th(k: int, oculus: float) -> float:
    return math.acos(oculus) * k / DOME_RINGS


def dome_starry(span: float, rise: float = 4.9, oculus: float = DOME_OCULUS) -> Piece:
    """`kit_dome_starry_<d>`: a ribbed domical vault over a square room `d`
    on a side (common room C), its foot on the springing line (the cornice;
    origin the room's centre there), its crown `rise` above: four curved
    cells (an elliptical profile, upright at the foot) whose panels are
    castle_sky (meta "sky": [pts, uv] per panel, u round each cell 0..1, v 0
    at the foot to 1 at the oculus; assemble.py's Upper rooms block places
    them, as the Great Hall's sky), stone ribs (castle_trim `rib`) on the four
    groins and the four cells' middles, a square oculus ring with a sky panel
    and a gilt star boss at the crown. Its coarse camera shell in meta
    "collider". At most grid.DOME_STARRY_TRIS."""
    h = span / 2.0
    p = Piece("")
    f = p.faces
    m = mk.Mesh()
    ths = [_th(k, oculus) for k in range(DOME_RINGS + 1)]
    sky = []
    for face in range(4):
        for k in range(DOME_RINGS):
            for j in range(DOME_SEGS):
                xa, xb = -1.0 + 2.0 * j / DOME_SEGS, -1.0 + 2.0 * (j + 1) / DOME_SEGS
                q = [_dome_point(h, rise, face, ths[k], xa), _dome_point(h, rise, face, ths[k], xb),
                     _dome_point(h, rise, face, ths[k + 1], xb), _dome_point(h, rise, face, ths[k + 1], xa)]
                uv = [((xa + 1) / 2, k / DOME_RINGS), ((xb + 1) / 2, k / DOME_RINGS),
                      ((xb + 1) / 2, (k + 1) / DOME_RINGS), ((xa + 1) / 2, (k + 1) / DOME_RINGS)]
                n = _dome_normal(h, rise, face, (ths[k] + ths[k + 1]) / 2)
                if geom.dot(geom.newell(q), n) < 0:
                    q, uv = list(reversed(q)), list(reversed(uv))
                sky.append(([tuple(round(c, 6) for c in pt) for pt in q], [tuple(round(c, 6) for c in t) for t in uv]))
    # The oculus: a flat sky panel over the top ring.
    a = h * oculus
    yk = rise * math.sin(ths[-1])
    cap = [(-a, yk, -a), (-a, yk, a), (a, yk, a), (a, yk, -a)]
    if geom.newell(cap)[1] > 0:
        cap.reverse()
    sky.append((cap, [(0.0, 1.0), (0.0, 1.0), (1.0, 1.0), (1.0, 1.0)]))
    # Ribs: the four groins (corner lines) and the four middles, from the foot to the oculus.
    w, dpt = RIB
    for face in range(4):
        mid = [(_dome_point(h, rise, face, th, 0.0), _dome_normal(h, rise, face, th)) for th in ths]
        _rib(f, mid, w, dpt)
        corner = []
        for th in ths:
            pt = _dome_point(h, rise, face, th, 1.0)
            n = geom.unit(geom.add(_dome_normal(h, rise, face, th), _dome_normal(h, rise, (face + 1) % 4, th)))
            corner.append((pt, n))
        _rib(f, corner, w * 1.15, dpt)
    # The oculus ring: a square frame round the cap (mitred soffit, inner and outer faces).
    tag = trim("rib")
    ri, ro, ys = a - w / 2, a + w / 2, yk - dpt
    sq = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    for i in range(4):
        (ax_, az_), (bx_, bz_) = sq[i], sq[(i + 1) % 4]
        out_n = ((ax_ + bx_) / 2, 0.0, (az_ + bz_) / 2)
        add_face(f, [(ax_ * ri, ys, az_ * ri), (bx_ * ri, ys, bz_ * ri), (bx_ * ro, ys, bz_ * ro), (ax_ * ro, ys, az_ * ro)],
                 tag, [(0.0, 0.08), (2 * ri, 0.08), (2 * ro, 0.22), (0.0, 0.22)], (0.0, -1.0, 0.0))
        add_face(f, [(ax_ * ri, ys, az_ * ri), (bx_ * ri, ys, bz_ * ri), (bx_ * ri, yk + 0.02, bz_ * ri),
                     (ax_ * ri, yk + 0.02, az_ * ri)], tag, [(0.0, 0.0), (2 * ri, 0.0), (2 * ri, 0.08), (0.0, 0.08)],
                 tuple(-c for c in out_n))
        add_face(f, [(ax_ * ro, ys, az_ * ro), (bx_ * ro, ys, bz_ * ro), (bx_ * ro, yk - 0.04, bz_ * ro),
                     (ax_ * ro, yk - 0.04, az_ * ro)], tag, [(0.0, 0.22), (2 * ro, 0.22), (2 * ro, 0.3), (0.0, 0.3)], out_n)
    # The crown's boss: a gilt eight-point star under the oculus.
    yb = yk - dpt - 0.02
    star = []
    for k in range(16):
        ang = 2 * math.pi * k / 16
        r = (0.9 if k % 2 == 0 else 0.4) * a * 0.8
        star.append((r * math.cos(ang), r * math.sin(ang)))
    for k in range(16):
        (x0, z0), (x1, z1) = star[k], star[(k + 1) % 16]
        _face(m, [(0.0, yb - 0.08, 0.0), (x0, yb, z0), (x1, yb, z1)], GILT, (0.0, -1.0, 0.0))
        _face(m, [(x0, yb, z0), (x1, yb, z1), (x1, yb + 0.06, z1), (x0, yb + 0.06, z0)], GILT,
              (x0 + x1, 0.0, z0 + z1))
    # The coarse camera shell: the vault's cells at three rings, one quad across each, and the cap.
    shell = []
    coarse = [ths[0], ths[DOME_RINGS // 3], ths[2 * DOME_RINGS // 3], ths[-1]]
    for face in range(4):
        for ta, tb in zip(coarse, coarse[1:]):
            shell.append([_dome_point(h, rise, face, ta, -1.0), _dome_point(h, rise, face, ta, 1.0),
                          _dome_point(h, rise, face, tb, 1.0), _dome_point(h, rise, face, tb, -1.0)])
    shell.append(list(cap))
    p.meta.update(span=span, rise=rise, sky=sky, collider=shell, crown=yk, boss=yb - 0.08)
    return _finish(p, m)


def _rib(f: list, path, w: float, dpt: float, side_hint=None) -> None:
    """A rib of width w hanging dpt under a path of (point, inward normal)
    on the vault: per segment its soffit and two sides (castle_trim `rib`,
    s along the rib, p across the strip), from 0.06 behind the surface."""
    tag = trim("rib")
    s = 0.0
    for (pa, na), (pb, nb) in zip(path, path[1:]):
        d = geom.unit(geom.sub(pb, pa))
        ln = geom.norm(geom.sub(pb, pa))
        sa = geom.unit(geom.cross(d, na)) if side_hint is None else geom.unit(geom.cross(d, side_hint))
        sb = geom.unit(geom.cross(d, nb)) if side_hint is None else sa

        def pt(p, n, sd, k, depth):
            return tuple(p[q] + n[q] * depth + sd[q] * k * w / 2 for q in range(3))

        back = -0.06
        lo_a = [pt(pa, na, sa, -1, dpt), pt(pa, na, sa, 1, dpt)]
        lo_b = [pt(pb, nb, sb, -1, dpt), pt(pb, nb, sb, 1, dpt)]
        add_face(f, [lo_a[0], lo_a[1], lo_b[1], lo_b[0]], tag, [(s, 0.08), (s, 0.22), (s + ln, 0.22), (s + ln, 0.08)],
                 geom.add(na, nb))
        for k, pr in ((-1, (0.0, 0.08)), (1, (0.22, 0.3))):
            q = [pt(pa, na, sa, k, back), pt(pa, na, sa, k, dpt), pt(pb, nb, sb, k, dpt), pt(pb, nb, sb, k, back)]
            add_face(f, q, tag, [(s, pr[0]), (s, pr[1]), (s + ln, pr[1]), (s + ln, pr[0])],
                     tuple(k * c for c in geom.add(sa, sb)))
        s += ln


# =====================================================================================
# The scholar statue (kit_statue_scholar): the robed scholar on a pedestal
# =====================================================================================

PEDESTAL = 0.7


def statue_scholar() -> Piece:
    """`kit_statue_scholar`: the M5a robed scholar (`kit_statue_robed_a`,
    hooded with an open book: an invented figure) standing on a free
    octagonal pedestal PEDESTAL high (a moulded foot and cap in the carved
    stone): 3.37 m in all. Free-standing in a room, so walk-through (the
    server has no prop collision; bots keep out, 26.8). At most
    grid.STATUE_SCHOLAR_TRIS."""
    fig = corridors.statue_robed("a")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    ph = math.pi / 8
    mk.lathe(m, [(0.7, 0.0), (0.7, 0.1), (0.64, 0.16), (0.6, 0.18)], 8, "stone", PLINTH, phase=ph, smooth=False, cap_top=False)
    mk.lathe(m, [(0.6, 0.18), (0.6, PEDESTAL - 0.14)], 8, "stone", PLINTH, phase=ph, smooth=False, cap_top=False)
    mk.lathe(m, [(0.6, PEDESTAL - 0.14), (0.66, PEDESTAL - 0.1), (0.68, PEDESTAL)], 8, "stone", PLINTH, phase=ph,
             smooth=False, cap_top=True)
    p.faces.extend(from_mesh(m, ZONES, walkthrough=True).faces)
    for fc in fig.faces:
        p.faces.append(geom.KFace([(x, y + PEDESTAL, z) for x, y, z in fc.pts], fc.tag, fc.uv, fc.anchor))
    top = max(q[1] for fc in p.faces for q in fc.pts)
    p.meta.update(height=top, pedestal=PEDESTAL, radius=0.7)
    return p


# =====================================================================================
# Dressing: window drapes (kit_drapes_<w>x<h>) and the reading lamp
# =====================================================================================

DRAPE_W = 0.62
DRAPE_Z = (0.16, 0.22)  # the pleats stand off the wall in front of a string course (0.15)
DRAPE_FOOT = 0.7  # the curtains' hem over the floor (over the plinth)


def drapes(width: float, height: float, house: float = 2, cloth: float = 0) -> Piece:
    """`kit_drapes_<w>x<h>`: a pair of pleated curtains in a house colour
    (`house` 0-3: house_a..d) beside a window `width` wide, hung from an iron
    rod with gilt finials `height` above the origin (the window's centre on
    the wall face at the floor), drawn back by gilt tie-backs at 40 %;
    their hem DRAPE_FOOT over the floor. At most DRAPES_TRIS; at most 0.25 m
    off the wall (the intrusion rule). `cloth` 1 hangs them in the
    wine cloth (`tapestry_wine`) instead of the house colour, for a room of
    no house (the Divination tower room)."""
    hcol = int(round(house))
    if int(round(cloth)) not in (0, 1):
        raise ValueError("kit_drapes: cloth 0 (the house colour) or 1 (wine)")
    if not 0 <= hcol <= 3 or not 1.0 <= width <= 6.0 or not 3.0 <= height <= 14.0:
        raise ValueError("kit_drapes: house 0..3, width 1..6 m, height 3..14 m")
    p = Piece("")
    m = mk.Mesh()
    zone = WINE_CLOTH if int(round(cloth)) == 1 else HOUSE0 + hcol
    za, zb = DRAPE_Z
    folds = 6
    tie = DRAPE_FOOT + (height - DRAPE_FOOT) * 0.4
    for sx in (-1.0, 1.0):
        cx = sx * (width / 2 + DRAPE_W / 2 - 0.12)
        rows = ((DRAPE_FOOT, 1.0), (tie, 0.55), (height - 0.15, 1.0))
        for (ya, ka), (yb, kb) in zip(rows, rows[1:]):
            for i in range(folds):
                ua, ub = -0.5 + i / folds, -0.5 + (i + 1) / folds
                z0, z1 = (za if i % 2 == 0 else zb), (zb if i % 2 == 0 else za)
                # The panel gathers towards its outer edge at the tie-back.
                gx = lambda u, k: cx + sx * (1 - k) * DRAPE_W * 0.3 + u * DRAPE_W * k  # noqa: E731
                q = [(gx(ua, ka), ya, z0), (gx(ub, ka), ya, z1), (gx(ub, kb), yb, z1), (gx(ua, kb), yb, z0)]
                _face(m, q, zone, (0.0, 0.0, 1.0))
        # The tie-back: a gilt band round the waist.
        w2 = DRAPE_W * 0.55 / 2
        tx = cx + sx * 0.45 * DRAPE_W * 0.3
        _box(m, (tx - w2 - 0.02, tie - 0.05, za - 0.02), (tx + w2 + 0.02, tie + 0.05, zb + 0.02), GILT, skip=("-z",))
    # The rod, its finials and two brackets.
    half = width / 2 + DRAPE_W + 0.08
    ry, rz = height - 0.08, (za + zb) / 2
    mk.rod(m, (-half, ry, rz), (half, ry, rz), 0.03, 6, "metal", IRON)
    for sx in (-1.0, 1.0):
        mk.lathe(m, [(0.0, -0.06), (0.05, -0.03), (0.05, 0.03), (0.0, 0.07)], 6, "metal", GILT, (sx * (half + 0.05), ry, rz))
        _box(m, (sx * (half - 0.2) - 0.025, ry - 0.1, 0.0), (sx * (half - 0.2) + 0.025, ry, rz), IRON, skip=("-z",))
    p.meta.update(wall=(-half - 0.1, half + 0.1, DRAPE_FOOT, height), proud=zb + 0.02, house=hcol)
    return _finish(p, m)


def lamp_reading() -> Piece:
    """`kit_lamp_reading`: a brass oil lamp for a reading table (its foot on
    the table top): a turned foot and font, a lit glass chimney
    (glow_lantern) and a brass shade ring; brings one warm light (meta
    "lights"). Walk-through. At most LAMP_TRIS."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.09, 0.0), (0.09, 0.015), (0.04, 0.04), (0.025, 0.12), (0.075, 0.17), (0.075, 0.23),
                 (0.03, 0.26)], 8, "metal", BRASS, cap_top=True)
    mk.lathe(m, [(0.03, 0.26), (0.045, 0.3), (0.035, 0.42), (0.02, 0.46)], 6, "glow", GLOW, cap_top=True)
    mk.lathe(m, [(0.06, 0.33), (0.13, 0.4), (0.055, 0.345)], 6, "metal", BRASS, cap_top=False)
    p.meta.update(height=0.46, lights=[{"id": "lamp", "pos": [0.0, 0.42, 0.0], "glow": "candle", "intensity": 7.0,
                                        "range": 7.0}])
    return _finish(p, m)
