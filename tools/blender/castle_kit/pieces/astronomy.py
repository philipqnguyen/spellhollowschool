"""Pieces of the Astronomy class room, `class_astronomy`
(tools/blender/world/school_rooms_astronomy.py, docs/architecture.md):
a tower-top platform open to the night sky.

- `kit_stair_head`: the head of the tower's stair, a small stone block on
  the platform's south edge that carries the class door (the room's blind
  door, kit_door_class_3x4.5, stands in its rectangular hole as it stands
  in a wall's) and a crenellated parapet round its flat top (the M5c
  facade parapet, pieces/facades.py). Frame as a wall piece: x along its
  face (to the right seen from the platform), y up, z out of the face onto
  the platform; origin on the face at the floor under the door's middle.
  The block runs `depth` behind the face (off the platform) and down to
  `below` under the floor (the tower's outer wall). Stone in castle_wall
  (world UVs), no props-atlas faces.
- `kit_telescope`: a brass refractor on an oak tripod with iron fittings
  (our own plain design), its tube aimed along +z and tilted up; `seed`
  turns and tilts each a little so a row does not look stamped. Origin on
  the floor at the tripod's centre. Walk-through (the server has no prop
  collision; a floor piece like the courtyard's). At most
  grid.TELESCOPE_TRIS at scale 1; `scale` (1-1.6) sizes the professor's.

Colours: palette names only (grid.palette_tag; existing props_slots rows:
brass, iron and steel_dark on the metal tile, oak on the wood tile, ink_blue
on the glass tile); no colour literal here (PaletteRuleTests)."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import facades
from castle_kit.pieces.geom import KFace, Piece, Placement, add_face
from castle_kit.pieces.hall import from_mesh

WALL = "castle_wall|world"

# -- kit_stair_head ----------------------------------------------------------------------------


def stair_head(width: float = 6.0, depth: float = 3.5, height: float = 6.4, below: float = 0.5,
               door_w: float = 3.0, door_h: float = 4.5, parapet: float = 1.4) -> Piece:
    """`kit_stair_head`: the block (grid.STAIR_HEAD_SIZE), its face cut
    for the door (`door_w` x `door_h` from the floor, centred), and a
    crenellated parapet `parapet` high round its top; at most
    grid.STAIR_HEAD_TRIS."""
    wlo, whi = G.STAIR_HEAD_SIZE[0]
    dlo, dhi = G.STAIR_HEAD_SIZE[1]
    hlo, hhi = G.STAIR_HEAD_SIZE[2]
    if not (wlo <= width <= whi and dlo <= depth <= dhi and hlo <= height <= hhi):
        raise ValueError(f"kit_stair_head: size {width} x {depth} x {height} outside {G.STAIR_HEAD_SIZE}")
    if door_w > width - 2 * G.OPENING_MARGIN or door_h + G.STAIR_HEAD_LINTEL > height:
        raise ValueError("kit_stair_head: the door must fit its face with a margin each side and a lintel over it")
    p = Piece("")
    f = p.faces
    hw, hd = width / 2.0, door_w / 2.0
    y0 = -below

    def quad(pts, n):
        add_face(f, list(pts), WALL, None, n)

    # The face onto the platform: beside the door and over it (the door piece's
    # spandrels, soffit and recess fill the rectangular hole, as in a wall).
    front = (0.0, 0.0, 1.0)
    quad([(-hw, 0.0, 0.0), (-hd, 0.0, 0.0), (-hd, height, 0.0), (-hw, height, 0.0)], front)
    quad([(hd, 0.0, 0.0), (hw, 0.0, 0.0), (hw, height, 0.0), (hd, height, 0.0)], front)
    quad([(-hd, door_h, 0.0), (hd, door_h, 0.0), (hd, height, 0.0), (-hd, height, 0.0)], front)
    # The sides and the back, down to the tower's outer wall; the flat top.
    for sx in (-1.0, 1.0):
        x = sx * hw
        quad([(x, y0, 0.0), (x, y0, -depth), (x, height, -depth), (x, height, 0.0)], (sx, 0.0, 0.0))
    quad([(hw, y0, -depth), (-hw, y0, -depth), (-hw, height, -depth), (hw, height, -depth)], (0.0, 0.0, -1.0))
    quad([(-hw, height, 0.0), (hw, height, 0.0), (hw, height, -depth), (-hw, height, -depth)], (0.0, 1.0, 0.0))
    # The parapet: four facade parapets on the top's edges, fronts outward; the
    # face and back ones run over the corners, the sides between their backs.
    kw = dict(height=parapet, breast=0.9, thick=0.45, proud=0.12)
    # The sides stop two coping overhangs short of the face and back runs (rules.PARAPET_CORNER_CLEAR).
    over = kw["thick"] - kw["proud"] + 0.13
    runs = [
        (width + 2 * kw["proud"], Placement((-hw - kw["proud"], height, 0.0), 0.0)),  # the face (+z)
        (width + 2 * kw["proud"], Placement((hw + kw["proud"], height, -depth), math.pi)),  # the back (-z)
        (depth - 2 * over, Placement((hw, height, -over), math.pi / 2)),  # east (+x)
        (depth - 2 * over, Placement((-hw, height, -depth + over), -math.pi / 2)),  # west (-x)
    ]
    for length, pl in runs:
        part = facades.parapet(length, **kw)
        for face in part.faces:
            f.append(KFace([pl.point(q) for q in face.pts], face.tag, face.uv, face.anchor))
    p.meta.update(door=(-hd, hd, door_h), size=(width, depth, height), top=height + parapet)
    return p


# -- kit_telescope -----------------------------------------------------------------------------

BRASS, OAK, IRON, LENS, DARK = 1, 2, 3, 4, 5
ZONES = {BRASS: "brass", OAK: "oak", IRON: "iron", LENS: "ink_blue", DARK: "steel_dark"}
TILE = {BRASS: "metal", OAK: "wood", IRON: "metal", LENS: "glass", DARK: "metal"}
HEAD = 1.18  # the mount's pivot over the floor (scale 1)
LEG_SPREAD = 0.42  # the tripod's feet from its centre (scale 1)
TUBE = (0.62, 0.95)  # the tube's length behind and in front of the pivot (scale 1)


def _jitter(seed: int) -> tuple[float, float]:
    """(turn, extra tilt) in radians for a seed: small, deterministic."""
    s = (int(seed) * 2654435761 + 97) % 2**32
    turn = ((s >> 7) % 21 - 10) * math.pi / 180.0
    tilt = ((s >> 13) % 11 - 5) * math.pi / 180.0
    return turn, tilt


def telescope(seed: float = 0, scale: float = 1.0, tilt: float = 0.6) -> Piece:
    """`kit_telescope`: a refractor on a tripod (module doc), aimed along +z
    and `tilt` radians up (grid.TELESCOPE_TILT), each `seed` turned and
    tilted a few degrees more."""
    lo, hi = G.TELESCOPE_SCALE
    if not lo - 1e-9 <= scale <= hi + 1e-9:
        raise ValueError(f"kit_telescope: scale {scale} outside {G.TELESCOPE_SCALE}")
    tlo, thi = G.TELESCOPE_TILT
    if not tlo - 1e-9 <= tilt <= thi + 1e-9:
        raise ValueError(f"kit_telescope: tilt {tilt} outside {G.TELESCOPE_TILT}")
    turn, more = _jitter(int(round(seed)))
    a = tilt + more
    m = mk.Mesh()
    head = (0.0, HEAD, 0.0)
    # The tripod: three oak legs from the head to the floor, an iron spreader ring.
    for k in range(3):
        b = 2 * math.pi * k / 3 + math.pi / 6
        foot = (LEG_SPREAD * math.sin(b), 0.0, LEG_SPREAD * math.cos(b))
        mk.rod(m, (foot[0] * 0.12, HEAD - 0.06, foot[2] * 0.12), foot, 0.022, 4, TILE[OAK], OAK)
        mk.rod(m, (foot[0] * 0.55, 0.5, foot[2] * 0.55), (0.0, 0.5, 0.0), 0.008, 3, TILE[IRON], IRON, caps=False)
    mk.lathe(m, [(0.0, HEAD - 0.1), (0.07, HEAD - 0.08), (0.07, HEAD - 0.02), (0.04, HEAD)], 8, TILE[IRON], IRON)
    # The mount: a fork on the head holding the tube at its pivot.
    mk.box(m, (-0.09, HEAD, -0.03), (-0.07, HEAD + 0.12, 0.03), TILE[IRON], IRON)
    mk.box(m, (0.07, HEAD, -0.03), (0.09, HEAD + 0.12, 0.03), TILE[IRON], IRON)
    mk.box(m, (-0.07, HEAD - 0.01, -0.025), (0.07, HEAD + 0.01, 0.025), TILE[IRON], IRON, skip=("-y",))
    piv = (0.0, HEAD + 0.1, 0.0)
    d = (0.0, math.sin(a), math.cos(a))

    def at(t: float, up: float = 0.0):
        return (piv[0], piv[1] + d[1] * t + up * d[2], piv[2] + d[2] * t - up * d[1])

    back, front = TUBE
    # The tube: a long brass body, a wider dew cap at the objective, an iron band at the pivot.
    mk.rod(m, at(-back + 0.12), at(front - 0.2), 0.055, 10, TILE[BRASS], BRASS)
    mk.rod(m, at(front - 0.2), at(front), 0.07, 10, TILE[BRASS], BRASS, caps=False)
    mk.rod(m, at(front - 0.21), at(front - 0.19), 0.074, 10, TILE[DARK], DARK, caps=False)
    mk.rod(m, at(-0.05), at(0.05), 0.062, 10, TILE[IRON], IRON, caps=False)
    # The objective lens a little inside the cap, facing the sky.
    lens = []
    for i in range(10):
        b = 2 * math.pi * i / 10
        side = (math.cos(b), math.sin(b))
        # A circle in the plane across the tube (x and the tube's "up").
        q = at(front - 0.03, 0.066 * side[1])
        lens.append((q[0] + 0.066 * side[0], q[1], q[2]))
    m.poly(lens if mk.dot(mk.newell(lens), d) > 0 else list(reversed(lens)), TILE[LENS], zone=LENS)
    # The draw tube and the eyepiece at the back, the finder along the top.
    mk.rod(m, at(-back), at(-back + 0.12), 0.03, 8, TILE[BRASS], BRASS)
    mk.rod(m, at(-back - 0.06), at(-back), 0.018, 6, TILE[DARK], DARK)
    mk.rod(m, at(0.15, 0.09), at(0.5, 0.09), 0.016, 6, TILE[BRASS], BRASS)
    for t in (0.2, 0.45):
        mk.rod(m, at(t, 0.05), at(t, 0.08), 0.008, 3, TILE[IRON], IRON, caps=False)
    c, s = math.cos(turn), math.sin(turn)
    low = min(v[1] for v in m.verts)  # (the legs' end caps dip a few mm: stand the feet on the floor)
    m.transform((0, 0), lambda q: (scale * (q[0] * c + q[2] * s), scale * (q[1] - low), scale * (q[2] * c - q[0] * s)))
    p = from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(feet=LEG_SPREAD * scale, top=round(max(v[1] for v in m.verts), 4))
    return p
