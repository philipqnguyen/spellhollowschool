"""Castle M5b dungeon and basement pieces (docs/art/castle-kit.md "Pieces"): the
barrel vault with transverse ribs, the wall torch,
common room D's hidden stone door, common room B's barrel stack, the
kitchen hearth, the hanging copper pans and the pots, plus the small
pieces the basement rooms share (a hanging lamp, a glowing lake or sun
window, a potted plant, wall shelves of jars and the Fruit Bowl Painting
over the kitchen door).

Frames as geom.py: x along the wall (to the right seen from the room), y
up, z out of the wall face into the room; wall pieces have their back on
the wall plane z = 0, floor pieces their origin at the bottom centre,
hanging pieces their hook at y = 0 (everything below it). The vault's
origin is its centre on the springing line (0 = the springing).

Everything is our own invented design. Colours: palette
names only (grid.palette_tag: copper, glow_lake, glow_sun and
plant from the `_doc_m5b_dungeons` block of castle_textures.json, the rest
reused), the stone of the cell's own wall family (world UVs), the trim and
wood sheets; no colour literal here (PaletteRuleTests). The castle has no
outline: every piece reads by its relief (proud bands, hoops, rims, lips)
under the toon ramp and the baked AO."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import fire
from castle_kit import grid as G
from castle_kit.pieces import frames, geom, openings, vaults
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall import from_mesh
from castle_kit.pieces.hall_furniture import PLANKS, wbox

WALL = openings.WALL  # castle_wall|world: the cell's own wall stone
FLOOR = openings.FLOOR
CEILING = "castle_ceiling|world"
RIB = vaults.RIB


def trim(strip: str) -> str:
    return openings.trim(strip)


# Zone -> palette name (castle_textures.json `palette`).
IRON, OAK, OAK_AGED, COPPER, SOOT, GLOW_LANTERN, GLOW_CANDLE, GLOW_GREEN, GLOW_LAKE, GLOW_SUN = range(1, 11)
PLANT, CLAY, INK, GLASS, BRASS, PEWTER, LEATHER = range(11, 18)
ZONES = {IRON: "iron", OAK: "oak", OAK_AGED: "oak_aged", COPPER: "copper", SOOT: "soot", GLOW_LANTERN: "glow_lantern",
         GLOW_CANDLE: "glow_candle", GLOW_GREEN: "glow_green", GLOW_LAKE: "glow_lake", GLOW_SUN: "glow_sun",
         PLANT: "plant", CLAY: "parcel_tan", INK: "ink_blue", GLASS: "hourglass_glass", BRASS: "brass", PEWTER: "pewter",
         LEATHER: "upholstery_leather"}


def _finish(p: Piece, m: mk.Mesh) -> Piece:
    p.faces.extend(from_mesh(m, ZONES, walkthrough=p.walkthrough).faces)
    return p


def _turn_z(m: mk.Mesh, mark, centre) -> None:
    """Turns a part built along +y (a lathe) to lie along +z, then moves its
    base to `centre` (a rotation, so the faces keep facing out)."""
    cx, cy, cz = centre
    m.transform(mark, lambda p: (p[0] + cx, -p[2] + cy, p[1] + cz))


# =====================================================================================
# The barrel vault (kit_vault_barrel_<span>x<length>)
# =====================================================================================


class BarrelSurface:
    """A round (segmental) barrel `span` wide rising `rise` from its
    springing: the arc of the circle through both springings and the crown,
    the same section all along its length (canonical frame: x across, z
    along)."""

    def __init__(self, span: float, length: float, rise: float):
        self.hs, self.hl, self.rise = span / 2.0, length / 2.0, rise
        self.r = (self.hs * self.hs + rise * rise) / (2.0 * rise)
        self.yc = rise - self.r

    def height(self, x: float, z: float = 0.0) -> float:
        x = max(-self.hs, min(self.hs, x))
        return self.yc + math.sqrt(max(0.0, self.r * self.r - x * x))

    def up(self, x: float, z: float = 0.0):
        """Outward from the barrel's axis (into the vault mass)."""
        return geom.unit((x, self.height(x) - self.yc, 0.0))

    def xs(self, seg_m: float = 1.0) -> list[float]:
        """Points across the span, evenly by angle, each arc segment at most
        `seg_m` long (and at least 8)."""
        a = math.asin(min(1.0, self.hs / self.r))
        n = max(8, math.ceil(2 * a * self.r / seg_m))
        return [self.r * math.sin(-a + 2 * a * i / n) for i in range(n + 1)]


def vault_barrel(span: float, length: float, rise: float = 3.0, axis: float = 0, rib0: float = 0.0, pitch: float = 0.0,
                 ribs: float = 0, cap: float = 0, cap_x0: float = 0.0, cap_x1: float = 0.0, bosses: float = 1,
                 web: float = 0) -> Piece:
    """`kit_vault_barrel_<span>x<length>`: a round barrel vault `span` wide
    and `length` long rising `rise` from the springing (origin: its centre
    on the springing line). Its web is castle_ceiling stone (world UVs),
    or with `web` 1 the cell's own wall stone (the dungeons: the cool stone
    runs on over the vault instead of the warm vault web);
    `ribs` transverse ribs (castle_trim `rib` strip) stand at rib0 + k x
    pitch along it, each with a small boss at its crown (`bosses`). The
    canonical frame runs the barrel along local z; `axis` 1 turns it to run
    along local x (the castle_kit.json yaw stays 0: rules.py keeps vault
    heroes unturned). `cap` closes the band between the arc and its crown's
    level at the start (bit 1, z = -length/2) or the end (bit 2) over
    cap_x0..cap_x1 across (where no wall stands there: the dungeon steps'
    stairwell), both faces in the cell's wall stone. Its coarse camera
    shell is meta "collider". At most grid.VAULT_BARREL_TRIS_PER_M per metre
    of length."""
    if rise <= 0 or rise > span / 2.0 + 1e-9:
        raise ValueError("kit_vault_barrel: 0 < rise <= span / 2 (a round barrel at most semicircular)")
    n_ribs = int(round(ribs))
    p = Piece("")
    f = p.faces
    S = BarrelSurface(span, length, rise)
    hl = S.hl
    xs = S.xs()
    web_tag = WALL if web >= 1 else CEILING
    rib_z = [rib0 + k * pitch for k in range(n_ribs)]
    zs = sorted({-hl, hl, *rib_z})
    if any(not -hl + 0.3 <= z <= hl - 0.3 for z in rib_z):
        raise ValueError("kit_vault_barrel: ribs must stand 0.3 m inside its ends")
    # The web: one band per arc segment between the ribs (the ribs' lines are its edges).
    for i in range(len(xs) - 1):
        xa, xb = xs[i], xs[i + 1]
        ya, yb = S.height(xa), S.height(xb)
        nrm = (-(xa + xb) / 2.0, -((ya + yb) / 2.0 - S.yc), 0.0)
        for za, zb in zip(zs, zs[1:]):
            add_face(f, [(xa, ya, za), (xb, yb, za), (xb, yb, zb), (xa, ya, zb)], web_tag, None, nrm)
    # Transverse ribs on their lines, a boss at each crown.
    for k in range(n_ribs):
        z = rib0 + k * pitch
        pts = [(x, S.height(x), z) for x in xs]
        vaults.rib_sweep(f, pts, [S.up(x) for x in xs], vaults.RIB_PROFILE, (0.0, 0.0, 1.0), 1.0, RIB, (0.0, 0.3))
        if bosses:
            vaults.boss_at(f, (0.0, rise - vaults.RIB_DEPTH + 0.1, z), (0.0, 1.0, 0.0), 0.45)
    # End caps over an open end (both faces).
    c = int(round(cap))
    if c and cap_x1 > cap_x0:
        for bit, z in ((1, -hl), (2, hl)):
            if not c & bit:
                continue
            arc = [(x, S.height(x)) for x in [cap_x0] + [x for x in xs if cap_x0 < x < cap_x1] + [cap_x1]]
            poly = arc + [(cap_x1, rise), (cap_x0, rise)]
            for sgn in (-1.0, 1.0):
                add_face(f, [(x, y, z) for x, y in poly], WALL, None, (0.0, 0.0, sgn))
    # The camera shell: the web in a few bands.
    shell = []
    coarse = [S.r * math.sin(-math.asin(S.hs / S.r) + 2 * math.asin(S.hs / S.r) * i / 6) for i in range(7)]
    for xa, xb in zip(coarse, coarse[1:]):
        shell.append([(xa, S.height(xa), -hl), (xb, S.height(xb), -hl), (xb, S.height(xb), hl), (xa, S.height(xa), hl)])
    if int(round(axis)) == 1:
        def rot(q):
            return (q[2], q[1], -q[0])

        for face in f:
            face.pts = [rot(q) for q in face.pts]
        shell = [[rot(q) for q in poly] for poly in shell]
    p.meta.update(rise=rise, radius=S.r, axis=int(round(axis)), collider=shell,
                  ribs=[rib0 + k * pitch for k in range(n_ribs)], span=span, length=length)
    return p


# =====================================================================================
# The wall torch (kit_torch_wall)
# =====================================================================================


def torch_wall(height: float = 2.8, light: float = 0.0) -> Piece:
    """`kit_torch_wall`: an iron wall plate and bracket `height` up (2.6-3.2
    m) holding a pitch torch leaning out of an iron cup, its flame on the
    props atlas's glow (`glow_lantern`). Origin at the floor on the wall
    face. Everything stands over 2.4 m, so nothing intrudes below it;
    `light` 1 brings a warm point light (meta "lights", for the rooms whose
    greybox torches had none). At most grid.TORCH_WALL_TRIS."""
    if not 2.6 - 1e-9 <= height <= 3.2 + 1e-9:
        raise ValueError("kit_torch_wall: the bracket stands 2.6-3.2 m up")
    p = Piece("")
    m = mk.Mesh()
    h = height
    # The plate, a ring and a strap down the wall.
    mk.box(m, (-0.07, h - 0.22, 0.0), (0.07, h + 0.04, 0.03), "metal", IRON, skip=("-z",))
    mk.box(m, (-0.02, h - 0.2, 0.03), (0.02, h - 0.16, 0.2), "metal", IRON, skip=("-z",))
    mk.box(m, (-0.02, h - 0.02, 0.03), (0.02, h + 0.02, 0.2), "metal", IRON, skip=("-z",))
    mk.rod(m, (0.0, h - 0.17, 0.2), (0.0, h - 0.01, 0.2), 0.02, 4, "metal", IRON)
    # The cup round the torch.
    mk.lathe(m, [(0.035, -0.05), (0.07, 0.04), (0.075, 0.08)], 8, "metal", IRON, (0.0, h + 0.02, 0.22), cap_top=False)
    # The torch: a tapering wooden stick leaning out, its pitch head and the flame.
    base, top = (0.0, h - 0.25, 0.16), (0.0, h + 0.28, 0.27)
    mk.rod(m, base, top, 0.03, 6, "wood", OAK_AGED)
    head = top[1]
    mk.lathe(m, [(0.03, 0.0), (0.055, 0.04), (0.06, 0.12), (0.045, 0.16)], 6, "stone", SOOT, (0.0, head, top[2]),
             cap_top=True)
    # The flame on the pitch head is the client's animated fire (docs/architecture.md "Rendering").
    p.meta["fires"] = [fire.fire((0.0, head + 0.12, top[2]), fire.cluster_tongues(0.05, 0.42, count=3, seed=11),
                                 embers=2)]
    p.meta.update(height=h, bracket=h, top=head + 0.52)
    if light >= 1:
        p.meta["lights"] = [{"pos": [0.0, head + 0.3, 0.6], "glow": "fire", "intensity": 10, "range": 7}]
    return _finish(p, m)


# =====================================================================================
# Common room D's hidden stone door (kit_door_stone_hidden)
# =====================================================================================

STONE_DOOR_BACK = 0.06  # the panel stands this far behind the wall face (never on its plane)


def door_stone_hidden(width: float = 4.0, height: float = 4.5) -> Piece:
    """`kit_door_stone_hidden`: the stone wall that opens for common room D:
    a panel in the cell's own wall stone (world UVs, so its courses run on
    from the wall round it) filling a `width` x `height` pointed door
    opening STONE_DOOR_BACK behind the wall face, drawn both ways (from the
    corridor and from the steps). Its arch keeps neither rim nor quoins
    (rules.py), so from the corridor the door is a plain stretch of wall.
    Walk-through (the house gate is the server's). At most
    grid.STONE_DOOR_TRIS."""
    p = Piece("", walkthrough=True)
    outline = geom.lancet_outline(width, height, openings.SEG)
    z = -STONE_DOOR_BACK
    add_face(p.faces, [(x, y, z) for x, y in outline], WALL, None, (0.0, 0.0, 1.0))
    add_face(p.faces, [(x, y, z - 0.02) for x, y in outline], WALL, None, (0.0, 0.0, -1.0))
    p.meta.update(width=width, height=height, back=STONE_DOOR_BACK)
    return p


# =====================================================================================
# Common room B's barrel stack (kit_barrel_stack)
# =====================================================================================

BARREL_D = 0.9  # diameter at the bilge
BARREL_L = 1.2  # length (out from the wall)
BARREL_PITCH = 0.95  # centres along a row
STACK_ROWS = (6, 5, 4, 3, 2)


def _barrel(m: mk.Mesh, cx: float, cy: float, z0: float) -> None:
    """A barrel lying along +z from z0, its axis at (cx, cy): bulged oak
    staves, two iron hoops, its front head (+z, the corridor's) set 0.04 m
    into the staves (the chime shows round it) in the lighter oak, its back
    head flat against the wall side."""
    seg = 8
    r1, r0 = BARREL_D / 2.0, BARREL_D / 2.0 - 0.06
    mk_ = m.mark()
    mk.lathe(m, [(r0, 0.0), (r1, 0.3), (r1, BARREL_L - 0.3), (r0, BARREL_L)], seg, "wood", OAK_AGED, cap_top=False)
    for y in (0.16, BARREL_L - 0.24):
        rr = r0 + (r1 - r0) * min(1.0, y / 0.3) + 0.012
        mk.lathe(m, [(rr, y), (rr, y + 0.08)], seg, "metal", IRON, cap_top=False)

    def ring(r, y):
        return [(r * math.sin(2 * math.pi * i / seg), y, r * math.cos(2 * math.pi * i / seg)) for i in range(seg)]

    m.poly(list(reversed(ring(r0, 0.0))), "wood", zone=OAK_AGED)
    outer, inner, well = ring(r0, BARREL_L), ring(r0 - 0.035, BARREL_L), ring(r0 - 0.035, BARREL_L - 0.04)
    for i in range(seg):
        j = (i + 1) % seg
        m.poly([outer[i], outer[j], inner[j], inner[i]], "wood", zone=OAK_AGED)
        m.poly([inner[i], inner[j], well[j], well[i]], "wood", zone=OAK_AGED)
    m.poly(well, "wood", zone=OAK)
    _turn_z(m, mk_, (cx, cy, z0))


def barrel_stack(rows: float = 5) -> Piece:
    """`kit_barrel_stack`: common room B's door, barrels stacked in rows of
    6, 5, 4, 3 and 2 (each row in the grooves of the one below) against the
    corridor face of the 3.5 x 3.5 `cr_b_barrels` opening, lying out of the
    wall (0.02 m off it) into the corridor; origin at the bottom centre on
    the wall face. Walk-through (the house gate is the server's). At most
    grid.BARREL_STACK_TRIS."""
    n = int(round(rows))
    if not 1 <= n <= len(STACK_ROWS):
        raise ValueError(f"kit_barrel_stack: 1..{len(STACK_ROWS)} rows")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    rise = BARREL_PITCH * math.sqrt(3.0) / 2.0
    for r, count in enumerate(STACK_ROWS[:n]):
        y = BARREL_D / 2.0 + 0.015 + r * rise
        for i in range(count):
            _barrel(m, (i - (count - 1) / 2.0) * BARREL_PITCH, y, 0.02)
    top = BARREL_D / 2.0 + (n - 1) * rise + BARREL_D / 2.0
    p.meta.update(rows=n, barrels=sum(STACK_ROWS[:n]), height=top, width=STACK_ROWS[0] * BARREL_PITCH,
                  depth=BARREL_L + 0.02)
    _finish(p, m)
    stack_backing(p.faces)
    return p


# Portrait back (the entrances seen from their common rooms): between round barrels the
# stack showed straight through, the common room's warm light from the corridor and the
# corridor from the common room. A soot panel (both ways, 2 cm thick) fills the opening
# BARREL_BACKING_Z into the stack, inside the barrels' depth: only the gaps show it, as
# the shadow between the barrels; it slides with them.
BARREL_BACKING_Z = 0.5
BARREL_OPENING = (3.5, 3.5)  # the cr_b_barrels arch (kit_stone_arch_3.5x3.5)
BACKING_TAG = G.palette_tag("stone", "soot")


def stack_backing(faces: list, width: float = BARREL_OPENING[0], height: float = BARREL_OPENING[1]) -> None:
    outline = geom.lancet_outline(width, height, openings.SEG // 2)
    z = BARREL_BACKING_Z
    add_face(faces, [(x, y, z + 0.02) for x, y in outline], BACKING_TAG, None, (0.0, 0.0, 1.0))
    add_face(faces, [(x, y, z) for x, y in outline], BACKING_TAG, None, (0.0, 0.0, -1.0))


# =====================================================================================
# The kitchen hearth (kit_hearth_kitchen)
# =====================================================================================

HEARTH_JAMB = 0.6  # each stone jamb's width beside the firebox
HEARTH_DEPTH = 1.0  # the chimney breast's projection from the wall
HEARTH_STEP = (0.3, 0.8)  # the raised step: height, depth in front of the breast
HEARTH_BACK = 0.15  # the firebox's back this far off the wall


def hearth_kitchen(width: float = 4.0, opening: float = 2.4, top: float = 6.0, flue: float = 0.0, hood: float = 0,
                   light: float = 1) -> Piece:
    """`kit_hearth_kitchen`: an open kitchen hearth on a wall: a stone
    chimney breast HEARTH_DEPTH proud with a firebox `width` wide and
    `opening` high over a raised hearth, a moulded lintel, a tapering hood
    up to `top` (`hood` 0: the cell's wall stone; 1: copper with iron
    bands, common room B's) and, when `flue` > `top`, a straight flue on up
    to that height (the ceiling); inside, a fire of logs, an iron spit on
    two uprights with its crank; in front a raised stone step HEARTH_STEP
    exporting one floor seat. Brings one warm light (`light`), a
    coarse camera shell of the breast and hood (meta "collider"). The
    breast stands on the wall (meta "wall": the plinth stops at it, it never
    covers a bay shaft). Walk-through (it stands 1.8 m into the room below
    2.4 m, like the furniture). At most grid.KITCHEN_HEARTH_TRIS."""
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    hw = width / 2.0
    W = hw + HEARTH_JAMB
    D = HEARTH_DEPTH
    sh, sd = HEARTH_STEP
    yl = sh + opening  # the lintel's underside
    lintel_h = 0.5
    soot = G.palette_tag("stone", "soot")
    # The step (its top a floor seat), in floor stone with wall-stone sides.
    sx = W + 0.2
    add_face(f, [(-sx, sh, D), (sx, sh, D), (sx, sh, D + sd), (-sx, sh, D + sd)], FLOOR, None, (0.0, 1.0, 0.0))
    add_face(f, [(-sx, 0.0, D + sd), (sx, 0.0, D + sd), (sx, sh, D + sd), (-sx, sh, D + sd)], trim("plinth"),
             [(0.0, 0.0), (2 * sx, 0.0), (2 * sx, 0.3), (0.0, 0.3)], (0.0, 0.0, 1.0))
    for s in (-1.0, 1.0):
        add_face(f, [(s * sx, 0.0, D), (s * sx, 0.0, D + sd), (s * sx, sh, D + sd), (s * sx, sh, D)], trim("plinth"),
                 [(0.0, 0.0), (sd, 0.0), (sd, 0.3), (0.0, 0.3)], (s, 0.0, 0.0))
    # The jambs: front, outer and inner sides (the firebox's sides are the inner ones, in soot).
    for s in (-1.0, 1.0):
        xi, xo = s * hw, s * W
        add_face(f, [(xi, 0.0, D), (xo, 0.0, D), (xo, yl, D), (xi, yl, D)], WALL, None, (0.0, 0.0, 1.0))
        add_face(f, [(xo, 0.0, 0.0), (xo, 0.0, D), (xo, yl + lintel_h, D), (xo, yl + lintel_h, 0.0)], WALL, None,
                 (s, 0.0, 0.0))
        add_face(f, [(xi, sh, HEARTH_BACK), (xi, sh, D), (xi, yl, D), (xi, yl, HEARTH_BACK)], soot, None, (-s, 0.0, 0.0))
        # A chamfered stone edge up each jamb's front corner (relief for the toon ramp).
        add_face(f, [(xi, 0.0, D), (xi + s * 0.08, 0.0, D + 0.06), (xi + s * 0.08, yl, D + 0.06), (xi, yl, D)], trim("shaft"),
                 [(0.0, 0.0), (0.0, 0.1), (yl, 0.1), (yl, 0.0)], (-s, 0.0, 1.0))
        add_face(f, [(xi + s * 0.08, 0.0, D + 0.06), (xi + s * 0.2, 0.0, D), (xi + s * 0.2, yl, D), (xi + s * 0.08, yl, D + 0.06)],
                 trim("shaft"), [(0.0, 0.1), (0.0, 0.2), (yl, 0.2), (yl, 0.1)], (s * 0.3, 0.0, 1.0))
    # The firebox: back, ceiling (the lintel's underside), hearth floor, ash.
    add_face(f, [(-hw, sh, HEARTH_BACK), (hw, sh, HEARTH_BACK), (hw, yl, HEARTH_BACK), (-hw, yl, HEARTH_BACK)], soot, None,
             (0.0, 0.0, 1.0))
    add_face(f, [(-hw, yl, HEARTH_BACK), (hw, yl, HEARTH_BACK), (hw, yl, D), (-hw, yl, D)], soot, None, (0.0, -1.0, 0.0))
    add_face(f, [(-hw, sh, HEARTH_BACK), (hw, sh, HEARTH_BACK), (hw, sh, D), (-hw, sh, D)], soot, None, (0.0, 1.0, 0.0))
    # The lintel: a stone beam across the breast with a moulded string band on its front.
    lx = W + 0.05
    geom.box(f, (-lx, yl, 0.0), (lx, yl + lintel_h, D + 0.08), trim("cornice"), skip=("-z", "-y"),
             uv_fn=lambda side, pts: [(q[0] + q[2], min(0.7, (q[1] - yl) / lintel_h * 0.7)) for q in pts])
    add_face(f, [(-lx, yl, D + 0.08), (lx, yl, D + 0.08), (lx, yl, D), (-lx, yl, D)], trim("cornice"),
             [(0.0, 0.0), (2 * lx, 0.0), (2 * lx, 0.05), (0.0, 0.05)], (0.0, -1.0, 0.0))
    for s in (-1.0, 1.0):
        add_face(f, [(s * hw, yl, D), (s * W, yl, D), (s * W, yl, 0.0), (s * hw, yl, 0.0)], WALL, None, (0.0, -1.0, 0.0))
    # The hood: from the lintel's top (W wide, D proud) tapering to `top` (60 %, half as proud).
    y0, y1 = yl + lintel_h, top
    if y1 <= y0 + 0.5:
        raise ValueError("kit_hearth_kitchen: the hood's top must clear the lintel by 0.5 m")
    bx, bp, tx, tp = W, D, W * 0.6, D * 0.5
    copper = hood >= 1
    hood_tag = G.palette_tag("metal", "copper") if copper else WALL
    front = [(-bx, y0, bp), (bx, y0, bp), (tx, y1, tp), (-tx, y1, tp)]
    add_face(f, front, hood_tag, None if not copper else [(-bx, 0.0), (bx, 0.0), (tx, y1 - y0), (-tx, y1 - y0)],
             (0.0, bp - tp, y1 - y0))
    for s in (-1.0, 1.0):
        side = [(s * bx, y0, 0.0), (s * bx, y0, bp), (s * tx, y1, tp), (s * tx, y1, 0.0)]
        add_face(f, side, hood_tag, None if not copper else [(0.0, 0.0), (bp, 0.0), (tp, y1 - y0), (0.0, y1 - y0)],
                 (s * (y1 - y0), s * (bx - tx), 0.0))
    if copper:
        # Iron bands round the hood (proud straps) and rivet heads along them.
        for k in (0.15, 0.55, 0.92):
            yk = y0 + (y1 - y0) * k
            xk, zk = bx + (tx - bx) * k, bp + (tp - bp) * k
            mk.box(m, (-xk - 0.03, yk - 0.05, zk - 0.02), (xk + 0.03, yk + 0.05, zk + 0.03), "metal", IRON, skip=("-z",))
    else:
        # A moulded band across the hood a third of the way up.
        k = 0.3
        yk = y0 + (y1 - y0) * k
        xk, zk = bx + (tx - bx) * k, bp + (tp - bp) * k
        geom.box(f, (-xk - 0.06, yk - 0.12, 0.0), (xk + 0.06, yk + 0.12, zk + 0.06), trim("string"), skip=("-z",),
                 uv_fn=lambda side, pts: [(q[0] + q[2], (q[1] - yk + 0.12) / 0.24 * 0.3) for q in pts])
    # The hood's top: a flue on up to the ceiling, or a cap.
    if flue > y1 + 0.1:
        fx, fz = tx, tp
        fl_tag = hood_tag
        add_face(f, [(-fx, y1, fz), (fx, y1, fz), (fx, flue, fz), (-fx, flue, fz)], fl_tag,
                 None if not copper else [(-fx, 0.0), (fx, 0.0), (fx, flue - y1), (-fx, flue - y1)], (0.0, 0.0, 1.0))
        for s in (-1.0, 1.0):
            add_face(f, [(s * fx, y1, 0.0), (s * fx, y1, fz), (s * fx, flue, fz), (s * fx, flue, 0.0)], fl_tag,
                     None if not copper else [(0.0, 0.0), (fz, 0.0), (fz, flue - y1), (0.0, flue - y1)], (s, 0.0, 0.0))
    else:
        add_face(f, [(-tx, y1, 0.0), (tx, y1, 0.0), (tx, y1, tp), (-tx, y1, tp)], hood_tag,
                 None if not copper else [(-tx, 0.0), (tx, 0.0), (tx, tp), (-tx, tp)], (0.0, 1.0, 0.0))
    # The fire: logs and flames on the hearth, an iron spit over it.
    fz = (HEARTH_BACK + D) / 2.0
    for (xa, za), (xb, zb), yy in (((-0.9, fz - 0.1), (0.9, fz - 0.05), sh + 0.1), ((-0.7, fz + 0.12), (0.8, fz + 0.08),
                                                                                       sh + 0.1),
                                    ((-0.5, fz), (0.6, fz + 0.02), sh + 0.27)):
        mk.rod(m, (xa, yy, za), (xb, yy, zb), 0.09, 6, "wood", OAK_AGED)
    # The flames on the logs and their glow on the hearth are the client's animated fire (meta "fires").
    p.meta["fires"] = [fire.fire((0.0, sh + 0.12, fz), fire.hearth_tongues(2.1, 1.15, depth=0.3, rows=2, count=8,
                                                                         seed=5), pool=1.2, embers=8, floor=sh)]
    sp = hw - 0.25
    for s in (-1.0, 1.0):
        mk.box(m, (s * sp - 0.03, sh, fz - 0.03), (s * sp + 0.03, sh + 0.96, fz + 0.03), "metal", IRON, skip=("-y", "+y"))
        mk.box(m, (s * sp - 0.06, sh + 0.95, fz - 0.05), (s * sp + 0.06, sh + 1.05, fz + 0.05), "metal", IRON)
    mk.rod(m, (-sp - 0.25, sh + 1.0, fz), (sp + 0.05, sh + 1.0, fz), 0.025, 4, "metal", IRON)
    mk.rod(m, (-sp - 0.25, sh + 1.0, fz), (-sp - 0.25, sh + 0.75, fz), 0.02, 4, "metal", IRON)
    _finish(p, m)
    # The camera shell (front of the breast and hood, both sides, the lintel's top).
    p.meta["collider"] = [
        [(-W, 0.0, D), (W, 0.0, D), (W, yl + lintel_h, D), (-W, yl + lintel_h, D)],
        front,
        *[[(s * W, 0.0, 0.0), (s * W, 0.0, D), (s * W, y1, D), (s * W, y1, 0.0)] for s in (-1.0, 1.0)],
    ]
    p.meta.update(width=width, wall=(-lx, lx, 0.0, max(y1, flue)), proud=D + sd, top=max(y1, flue),
                  floor_seats=[[round(-sx + 0.05, 4), round(D + 0.05, 4), round(sx - 0.05, 4), round(D + sd - 0.05, 4), sh]])
    if light >= 1:
        p.meta["lights"] = [{"pos": [0.0, sh + 0.8, D + 0.4], "glow": "fire", "intensity": 25, "range": 10}]
    return p


# =====================================================================================
# Hanging copper pans (kit_pans_hanging) and pots (kit_pots_set)
# =====================================================================================

PAN_SIZES = ((0.22, 0.08), (0.16, 0.06), (0.19, 0.07), (0.13, 0.05))  # (radius, depth)


def _pan(m: mk.Mesh, x: float, hook_y: float, z: float, r: float, d: float, face: float) -> float:
    """A copper pan hanging by its handle from a hook at (x, hook_y, z), its
    bowl facing +z (`face` 1) or -z; returns its lowest point."""
    handle = 0.22 + r
    cy = hook_y - handle - r  # the bowl's centre
    mk.rod(m, (x, hook_y, z), (x, cy + r, z), 0.015, 4, "metal", COPPER)
    mark = m.mark()
    # The bowl along +y (outside then inside: two-sided), turned to face along z.
    mk.lathe(m, [(0.0, 0.0), (r * 0.85, 0.0), (r, d)], 8, "metal", COPPER, cap_top=False)
    mk.lathe(m, [(r * 0.97, d), (r * 0.82, 0.01), (0.0, 0.01)], 8, "metal", COPPER, cap_top=False)
    s = 1.0 if face > 0 else -1.0
    m.transform(mark, lambda p: (x + p[0], cy - p[2], z + s * (p[1] - d * 0.5)))
    if s < 0:
        # (mirrored along z: rewind the new faces so they keep facing out)
        for fc in m.faces[mark[1]:]:
            fc.idx = tuple(reversed(fc.idx))
            fc.uvs = list(reversed(fc.uvs))
    return cy - r


def pans_hanging(length: float = 2.4, drop: float = 1.0, count: float = 8) -> Piece:
    """`kit_pans_hanging`: an iron rack `length` long and 0.5 m wide hung by
    four chains `drop` below its hook point (y = 0: the ceiling), with
    `count` copper pans hanging from hooks along both rails (bowls facing
    out). Everything is meant to stay over 2.4 m (meta "lowest" is its
    lowest point below the hook; lint checks it over the floor). At most
    grid.PANS_HANGING_TRIS."""
    n = int(round(count))
    if not 2 <= n <= 12 or not 1.2 <= length <= 4.0:
        raise ValueError("kit_pans_hanging: 2-12 pans on a 1.2-4 m rack")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    hl, y = length / 2.0, -drop
    for z in (-0.25, 0.25):
        mk.rod(m, (-hl, y, z), (hl, y, z), 0.022, 4, "metal", IRON)
    for x in (-hl + 0.05, hl - 0.05):
        mk.rod(m, (x, y, -0.25), (x, y, 0.25), 0.02, 4, "metal", IRON)
        for z in (-0.25, 0.25):
            # A chain (alternating flat links) up to the hook point.
            links = max(2, int(drop / 0.16))
            for i in range(links):
                ya, yb = y + drop * i / links, y + drop * (i + 0.8) / links
                if i % 2 == 0:
                    mk.box(m, (x - 0.025, ya, z - 0.005), (x + 0.025, yb, z + 0.005), "metal", IRON, skip=("+y", "-y"))
                else:
                    mk.box(m, (x - 0.005, ya, z - 0.025), (x + 0.005, yb, z + 0.025), "metal", IRON, skip=("+y", "-y"))
    lowest = y
    per = (n + 1) // 2
    for side, z in ((0, -0.25), (1, 0.25)):
        k = per if side == 0 else n - per
        for i in range(k):
            x = -hl + 0.3 + (length - 0.6) * (i + 0.5) / k
            r, d = PAN_SIZES[(i + side) % len(PAN_SIZES)]
            lowest = min(lowest, _pan(m, x, y, z, r, d, 1.0 if side else -1.0))
    p.meta.update(lowest=round(lowest, 4), drop=drop, pans=n)
    return _finish(p, m)


def pots_set(count: float = 4) -> Piece:
    """`kit_pots_set`: `count` (2-5) kitchen pieces in a row along local x on
    a table top or the hearth step (origin at the surface, their middle): a
    copper stockpot with handles, an iron cauldron on three feet, a clay jug,
    a copper saucepan, a pewter ladle in a crock. Walk-through. At most
    grid.POTS_SET_TRIS."""
    n = int(round(count))
    if not 2 <= n <= 5:
        raise ValueError("kit_pots_set: 2-5 pots")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    xs = [(i - (n - 1) / 2.0) * 0.55 for i in range(n)]
    for i, x in enumerate(xs):
        kind = i % 5
        if kind == 0:  # stockpot
            mk.lathe(m, [(0.0, 0.0), (0.17, 0.0), (0.19, 0.04), (0.19, 0.3), (0.2, 0.32)], 10, "metal", COPPER, (x, 0.0, 0.0),
                     cap_top=False)
            mk.lathe(m, [(0.19, 0.32), (0.17, 0.3), (0.0, 0.3)], 10, "stone", SOOT, (x, 0.0, 0.0), cap_top=False)
            for s in (-1.0, 1.0):
                mk.box(m, (x + s * 0.19, 0.24, -0.03), (x + s * 0.25, 0.27, 0.03), "metal", IRON)
        elif kind == 1:  # cauldron on three feet
            mk.lathe(m, [(0.0, 0.06), (0.14, 0.08), (0.2, 0.18), (0.18, 0.32), (0.2, 0.34)], 10, "metal", IRON,
                     (x, 0.0, 0.0), cap_top=False)
            mk.lathe(m, [(0.2, 0.34), (0.17, 0.31), (0.0, 0.3)], 10, "stone", SOOT, (x, 0.0, 0.0), cap_top=False)
            for k in range(3):
                a = 2 * math.pi * k / 3
                mk.rod(m, (x + 0.12 * math.cos(a), 0.08, 0.12 * math.sin(a)), (x + 0.15 * math.cos(a), 0.02, 0.15 * math.sin(a)),
                       0.02, 4, "metal", IRON)
        elif kind == 2:  # clay jug
            mk.lathe(m, [(0.0, 0.0), (0.1, 0.0), (0.13, 0.12), (0.1, 0.28), (0.06, 0.34), (0.075, 0.38)], 8, "stone", CLAY,
                     (x, 0.0, 0.0), cap_top=True)
        elif kind == 3:  # saucepan with a long handle
            mk.lathe(m, [(0.0, 0.0), (0.12, 0.0), (0.13, 0.12)], 10, "metal", COPPER, (x, 0.0, 0.0), cap_top=False)
            mk.lathe(m, [(0.13, 0.12), (0.115, 0.11), (0.0, 0.11)], 10, "stone", SOOT, (x, 0.0, 0.0), cap_top=False)
            mk.box(m, (x + 0.12, 0.09, -0.02), (x + 0.42, 0.11, 0.02), "metal", COPPER)
        else:  # a crock with a pewter ladle
            mk.lathe(m, [(0.0, 0.0), (0.1, 0.0), (0.11, 0.22), (0.09, 0.24)], 8, "stone", CLAY, (x, 0.0, 0.0), cap_top=True)
            mk.rod(m, (x, 0.2, 0.0), (x + 0.04, 0.5, 0.02), 0.012, 4, "metal", PEWTER)
    p.meta.update(count=n, length=(n - 1) * 0.55 + 0.5)
    return _finish(p, m)


# =====================================================================================
# Shared small pieces of the basement rooms
# =====================================================================================


def lamp_hanging(drop: float = 1.0, glow: float = 0) -> Piece:
    """`kit_lamp_hanging`: a lamp on a chain `drop` below its hook (y = 0):
    `glow` 0 a candle globe under a copper bell shade (common room B), 1 a
    brass lantern on a brass chain, its globe glowing candle-warm in a brass
    cage (common room D, palette `glow_lantern`: the room's key light is warm,
    its green stays in the fabrics). Meta "lowest" for the overhead check.
    At most 300 triangles."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    g = int(round(glow))
    links = max(2, min(int(drop / 0.12), 30))
    metal = IRON if g == 0 else BRASS
    for i in range(links):
        ya, yb = -drop * (i + 0.85) / links, -drop * i / links
        if i % 2 == 0:
            mk.box(m, (-0.03, ya, -0.006), (0.03, yb, 0.006), "metal", metal, skip=("+y", "-y"))
        else:
            mk.box(m, (-0.006, ya, -0.03), (0.006, yb, 0.03), "metal", metal, skip=("+y", "-y"))
    y = -drop
    if g == 0:
        # A copper bell shade (two-sided: outside and in) over a candle-glow globe.
        mk.lathe(m, [(0.42, y - 0.42), (0.36, y - 0.3), (0.18, y - 0.12), (0.05, y - 0.02), (0.0, y)], 10, "metal", COPPER,
                 cap_top=False)
        mk.lathe(m, [(0.0, y - 0.03), (0.17, y - 0.14), (0.35, y - 0.32), (0.4, y - 0.42)], 10, "metal", COPPER,
                 cap_top=False)
        mk.lathe(m, [(0.0, y - 0.62), (0.13, y - 0.54), (0.15, y - 0.42), (0.1, y - 0.3), (0.0, y - 0.26)], 8, "glow",
                 GLOW_CANDLE, cap_top=False)
        low = y - 0.62
    else:
        mk.lathe(m, [(0.0, y - 0.7), (0.17, y - 0.6), (0.22, y - 0.42), (0.17, y - 0.22), (0.0, y - 0.12)], 10, "glow",
                 GLOW_LANTERN, cap_top=False)
        for k in range(4):
            a = math.pi / 4 + k * math.pi / 2
            ca, sa = math.cos(a), math.sin(a)
            mk.rod(m, (0.06 * ca, y - 0.05, 0.06 * sa), (0.235 * ca, y - 0.42, 0.235 * sa), 0.012, 4, "metal", BRASS)
            mk.rod(m, (0.235 * ca, y - 0.43, 0.235 * sa), (0.03 * ca, y - 0.76, 0.03 * sa), 0.012, 5, "metal", BRASS)
        mk.lathe(m, [(0.0, y - 0.1), (0.09, y - 0.08), (0.09, y), (0.0, y + 0.02)], 8, "metal", BRASS, cap_top=False)
        mk.lathe(m, [(0.24, y - 0.44), (0.25, y - 0.4)], 10, "metal", BRASS, cap_top=False)
        low = y - 0.78
    p.meta.update(lowest=round(low, 4), drop=drop, glow=g)
    return _finish(p, m)


WINDOW_FRAME = 0.16  # the stone surround's width
WINDOW_PROUD = 0.1


def window_glow(w: float = 1.4, h: float = 3.6, round: float = 0, glow: float = 0, lights: float = 2) -> Piece:  # noqa: A002
    """`kit_window_glow`: a window that glows rather than opens (the
    basement's: common room D's lake windows, `glow` 0, `glow_lake`;
    common room B's round sun windows, `glow` 1, `glow_sun`), on the wall
    face with its sill at y = 0: a pointed `w` x `h` light (or, `round` 1,
    a round one `w` across) in a moulded stone surround WINDOW_FRAME wide,
    WINDOW_PROUD proud, `lights` lights divided by mullions and a transom
    (stone on the trim sheet), the glass glowing on the props atlas. On the
    wall (meta "wall"), never over a bay shaft. At most 400 triangles."""
    p = Piece("")
    f = p.faces
    m = mk.Mesh()
    zone = GLOW_LAKE if int(round_(glow)) == 0 else GLOW_SUN
    is_round = round_(round) >= 1
    if is_round:
        r = w / 2.0
        n = 20
        inner = [(r * math.cos(math.pi / 2 + 2 * math.pi * k / n), r + r * math.sin(math.pi / 2 + 2 * math.pi * k / n))
                 for k in range(n)]
        hh = w
    else:
        inner = geom.lancet_outline(w, h, 8)
        hh = h
    outer = mk.offset_convex(inner, WINDOW_FRAME)
    k = len(inner)
    s_acc = 0.0
    for i in range(k):
        a, b = inner[i], inner[(i + 1) % k]
        c, d = outer[(i + 1) % k], outer[i]
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        add_face(f, [(a[0], a[1], WINDOW_PROUD), (b[0], b[1], WINDOW_PROUD), (c[0], c[1], WINDOW_PROUD), (d[0], d[1], WINDOW_PROUD)],
                 trim("window_frame"), [(s_acc, 0.0), (s_acc + ln, 0.0), (s_acc + ln, 0.3), (s_acc, 0.3)], (0.0, 0.0, 1.0))
        add_face(f, [(a[0], a[1], 0.02), (b[0], b[1], 0.02), (b[0], b[1], WINDOW_PROUD), (a[0], a[1], WINDOW_PROUD)],
                 trim("window_frame"), [(s_acc, 0.0), (s_acc + ln, 0.0), (s_acc + ln, 0.1), (s_acc, 0.1)], None)
        lo = math.hypot(c[0] - d[0], c[1] - d[1])
        add_face(f, [(d[0], d[1], 0.0), (c[0], c[1], 0.0), (c[0], c[1], WINDOW_PROUD), (d[0], d[1], WINDOW_PROUD)],
                 trim("window_frame"), [(0.0, 0.2), (lo, 0.2), (lo, 0.3), (0.0, 0.3)], None)
        s_acc += ln
    # Fix the edge faces' winding: the inner reveal faces the opening's centre, the outer edge away from it.
    cx = sum(q[0] for q in inner) / k
    cy = sum(q[1] for q in inner) / k
    for face in f[-3 * k:]:
        if abs(face.normal()[2]) > 0.5:
            continue
        mx = sum(q[0] for q in face.pts) / len(face.pts)
        my = sum(q[1] for q in face.pts) / len(face.pts)
        n_ = face.normal()
        outward = (mx - cx) * n_[0] + (my - cy) * n_[1] > 0
        is_outer = any(abs(q[2]) < 1e-9 for q in face.pts)
        if outward != is_outer:
            face.pts = list(reversed(face.pts))
            if face.uv is not None:
                face.uv = list(reversed(face.uv))
    # The glass, glowing (a hair proud of the wall), and the stone mullions / transom over it.
    m.poly([(x, y, 0.02) for x, y in inner], "glow", zone=zone)
    n_l = max(1, round_(lights))
    span = w
    for i in range(1, n_l):
        x = -span / 2 + span * i / n_l
        top_y = max(y for xx, y in inner if abs(xx - x) < span / n_l) if not is_round else hh
        ylo = 0.0 if not is_round else r - math.sqrt(max(0.0, r * r - x * x))
        yhi = top_y if not is_round else r + math.sqrt(max(0.0, r * r - x * x))
        mk.box(m, (x - 0.05, ylo, 0.02), (x + 0.05, yhi - 0.02, 0.08), "stone", SOOT, skip=("-z",))
    if is_round:
        mk.box(m, (-w / 2 + 0.02, hh / 2 - 0.05, 0.02), (w / 2 - 0.02, hh / 2 + 0.05, 0.07), "stone", SOOT, skip=("-z",))
    else:
        yt = (h - G.ARCH_RISE * w) * 0.55
        mk.box(m, (-w / 2, yt - 0.05, 0.02), (w / 2, yt + 0.05, 0.07), "stone", SOOT, skip=("-z",))
    xs_ = [q[0] for q in outer]
    ys_ = [q[1] for q in outer]
    p.meta.update(wall=(min(xs_), max(xs_), min(ys_), max(ys_)), proud=WINDOW_PROUD, height=max(ys_))
    return _finish(p, m)


def round_(v: float) -> int:
    return int(math.floor(float(v) + 0.5))


def plant_pot(scale: float = 1.0) -> Piece:
    """`kit_plant_pot`: a copper pot of earth with a leafy plant (broad
    blades fanning up and out round a bushy heart), about 1.1 m tall at
    `scale` 1, palette `plant`. Walk-through. At most 300 triangles."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    s = scale
    mk.lathe(m, [(0.0, 0.0), (0.2 * s, 0.0), (0.27 * s, 0.38 * s), (0.3 * s, 0.42 * s), (0.28 * s, 0.45 * s)], 10, "metal",
             COPPER, cap_top=False)
    mk.lathe(m, [(0.28 * s, 0.45 * s), (0.26 * s, 0.4 * s), (0.0, 0.4 * s)], 10, "stone", SOOT, cap_top=False)
    mk.lathe(m, [(0.0, 0.38 * s), (0.22 * s, 0.45 * s), (0.25 * s, 0.7 * s), (0.15 * s, 0.9 * s), (0.0, 0.95 * s)], 8, "leaf",
             PLANT, cap_top=False)
    for k in range(7):
        a = 2 * math.pi * k / 7 + 0.3
        ca, sa = math.cos(a), math.sin(a)
        base = (0.08 * s * ca, 0.5 * s, 0.08 * s * sa)
        tip = (0.62 * s * ca, (0.95 + 0.18 * (k % 3)) * s, 0.62 * s * sa)
        mid = ((base[0] + tip[0]) / 2, (base[1] + tip[1]) / 2 + 0.12 * s, (base[2] + tip[2]) / 2)
        side = (-sa * 0.11 * s, 0.0, ca * 0.11 * s)
        left = (mid[0] + side[0], mid[1], mid[2] + side[2])
        right = (mid[0] - side[0], mid[1], mid[2] - side[2])
        blade = [base, right, tip, left]
        m.poly(blade, "leaf", zone=PLANT)
        m.poly(list(reversed(blade)), "leaf", zone=PLANT)
    p.meta.update(height=1.2 * s, radius=0.65 * s)
    return _finish(p, m)


SHELF_DEPTH = 0.42


def shelf_wall(width: float = 2.4, height: float = 2.4, shelves: float = 4) -> Piece:
    """`kit_shelf_wall`: open oak shelves against a wall (`width` x `height`,
    SHELF_DEPTH deep, `shelves` boards over a plinth board, a planked back)
    with jars, pots and crocks on every shelf (glass, copper and clay by
    palette name). Walk-through like the bookcases (it is deeper than the
    intrusion limit); on the wall (meta "wall": the plinth stops at it).
    At most 1,000 triangles."""
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    hw, d = width / 2.0, SHELF_DEPTH
    n = int(round(shelves))
    add_face(f, [(-hw, 0.0, 0.02), (hw, 0.0, 0.02), (hw, height, 0.02), (-hw, height, 0.02)], PLANKS,
             [(-hw, 0.0), (hw, 0.0), (hw, height), (-hw, height)], (0.0, 0.0, 1.0))
    for s in (-1.0, 1.0):
        x0, x1 = sorted((s * hw, s * (hw - 0.05)))
        wbox(f, (x0, 0.0, 0.02), (x1, height, d), skip=("-z", "-y"), axis=2)
    wbox(f, (-hw, height, 0.02), (hw, height + 0.06, d + 0.03), skip=("-z",))
    levels = [0.08 + (height - 0.2) * i / n for i in range(n)]
    kinds = (INK, COPPER, CLAY, GLASS, COPPER, CLAY, INK)
    for j, y in enumerate(levels):
        wbox(f, (-hw + 0.05, y, 0.02), (hw - 0.05, y + 0.04, d), skip=("-z",))
        k = max(2, int((width - 0.2) / 0.32))
        for i in range(k):
            if (i + j) % 4 == 3:
                continue
            x = -hw + 0.2 + (width - 0.4) * i / max(1, k - 1)
            zone = kinds[(i * 3 + j) % len(kinds)]
            hgt = 0.18 + 0.08 * ((i + 2 * j) % 3)
            r = 0.07 + 0.02 * ((i + j) % 2)
            tile = "metal" if zone == COPPER else ("glass" if zone in (INK, GLASS) else "stone")
            mk.lathe(m, [(0.0, 0.0), (r, 0.0), (r * 1.05, hgt * 0.7), (r * 0.6, hgt), (r * 0.7, hgt + 0.03)], 6, tile, zone,
                     (x, y + 0.04, d / 2 + 0.02), cap_top=True)
    p.meta.update(wall=(-hw, hw, 0.0, height + 0.06), proud=d + 0.03, height=height + 0.06)
    return _finish(p, m)


def door_painting(width: float = 4.6, height: float = 5.3, canvas: float = 52) -> Piece:
    """`kit_door_painting`: the Fruit Bowl Painting over the kitchen door:
    one framed canvas `width` x `height` (an invented still life from the
    portrait sheets, `canvas` 48-62) standing on the floor on the corridor's
    wall face, covering the 4 x 5 doorway (its arch keeps no rim or quoins,
    rules.py); players walk through it like common room A's portrait.
    Faces in the castle_portraits family (the cell's sheet). LOD1 one quad."""
    c = int(round(canvas))
    lo, hi = G.PORTRAIT_KINDS["still_life"]
    if not lo <= c < hi:
        raise ValueError(f"kit_door_painting: canvas must be a still life ({lo}..{hi - 1})")
    p = Piece("", walkthrough=True)
    p.lod1 = []
    frames.frame_faces(p.faces, p.lod1, -width / 2.0, 0.0, width, height, "rect", c, 0.3, 0.16)
    frames.board_back(p.faces, -width / 2.0, width / 2.0, 0.0, height)  # Portrait back: seen from the kitchens
    p.meta.update(width=width, height=height, canvas=c)
    return p


# =====================================================================================
# The walled steps' skirts (kit_stair_skirt)
# =====================================================================================


def stair_skirt(width: float, length: float, rise: float, riser: float = G.RISER) -> Piece:
    """`kit_stair_skirt`: for a solid kit_stair_flight (same frame and
    sizes) that runs between two walls, the strip of wall stone the walls
    leave open beside each step: the Planner's walls stop at the stair's
    ramp line, and over the back half of each tread the tread lies under it
    (a triangle per step on each side, from where the ramp crosses the
    tread's top to the next riser). Both faces face the stair (they lie in
    the wall planes x = 0 and x = width, under the walls' faces, never on
    them)."""
    p = Piece("")
    n = max(1, round(rise / riser))
    d, r = length / n, rise / n
    k = rise / length
    for i in range(n):
        a, b = i * d, (i + 1) * d
        top = (i + 0.5) * r
        zm = top / k  # where the ramp (y = k z) crosses the tread's top
        if b - zm < 1e-4:
            continue
        for x, sx in ((0.0, 1.0), (width, -1.0)):
            add_face(p.faces, [(x, top, zm), (x, top, b), (x, k * b, b)], WALL, None, (sx, 0.0, 0.0))
    p.meta.update(steps=n)
    return p
