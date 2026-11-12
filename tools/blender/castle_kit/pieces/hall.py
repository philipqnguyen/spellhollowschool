"""Entrance Hall hero pieces (castle kit M2): the suit of armour
that stands in a wall niche, the Owl Exchange counter widened to the court
(the M1 `owl_exchange_counter` model through its `width` argument) and the
owl perches beside it. All `castle_props` faces on the shared props atlas
(data/art/castle_textures.json `props_slots`). Frames as props.json `kit`:
origin at the feet (perch, armour) or the back's middle on the wall
(counter), yaw 0 facing +z.

The armour is our own neutral design (a plain Gothic harness with a
longsword, no heraldry); the counter and perches are furniture the players
walk through (`walkthrough`, like the props kit)."""

from __future__ import annotations

import kit
import kit_models as km
import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece

# Zone -> palette name (data/art/castle_textures.json `palette`; castle kit M3:
# piece code names its colours, docs/art/castle-kit.md "Colours come from palette data only"). The counter and
# the perch's owl are meshkit models of world/kit_models.py: their zones are
# mapped to names here, so the castle palette recolours them too.
ARMOUR_ZONES = {1: "steel", 2: "steel_dark", 3: "gilt", 4: "plume", 5: "wine_red"}
COUNTER_ZONES = {1: "oak", 2: "oak_aged", 3: "brass", 4: "owl_brown", 5: "ledger_green", 6: "wine_red",
                 7: "ink_blue", 8: "owl_brown", 9: "letter_white", 10: "parcel_tan", 11: "owl_pale", 12: "iron_kit",
                 90: "brass"}
PERCH_ZONES = {2: "oak_aged", 3: "brass", 8: "owl_brown", 11: "owl_pale", 90: "brass"}


def from_mesh(mesh: mk.Mesh, zones: dict, walkthrough: bool = False) -> Piece:
    """A meshkit model as castle_props kit faces (tile, the palette name of
    its zone, the model's own UVs in metres or 0..1). A tinted face whose
    zone has no name is an error: every colour comes from the palette."""
    p = Piece("", walkthrough=walkthrough)
    for f in mesh.faces:
        pts = [mesh.verts[i] for i in f.idx]
        if f.material.startswith("g:"):
            raise ValueError("hall pieces use atlas tiles only")
        if kit.TILES[f.material][2]:
            if f.zone not in zones:
                raise ValueError(f"zone {f.zone} of a {f.material!r} face has no palette name")
            tag = G.palette_tag(f.material, zones[f.zone])
        else:
            tag = f"castle_props|kit:{f.material}"
        geom.add_face(p.faces, pts, tag, list(f.uvs), geom.newell(pts))
    # Animated fire (castle_kit/fire.py `note`): the model's flames become the piece's meta.
    notes = mesh.__dict__.get("fire_notes")
    if notes:
        p.meta.setdefault("fires", []).extend(notes["fires"])
        p.meta.setdefault("candles", []).extend(notes["candles"])
    return p


# -- the suit of armour ---------------------------------------------------------------------


def _lathe(m, rings, seg, tile, zone, centre=(0.0, 0.0, 0.0), sx=1.0, sz=1.0, cap_top=False, cap_bottom=False):
    mk.lathe(m, rings, seg, tile, zone, centre, scale_xz=(sx, sz), cap_top=cap_top, cap_bottom=cap_bottom)


def armour_suit() -> Piece:
    """`kit_armour_suit`: a full plate harness 1.96 m tall standing at
    attention, hands resting on a longsword point-down in front; at most
    1,200 triangles. Fits a 2 x 3.5 niche 0.7 deep with its feet 0.35 m
    behind the face (it stays within 0.15 m behind and 0.21 m in front of
    its feet)."""
    m = mk.Mesh()
    S, D, G_, R = 1, 2, 3, 4  # zones: steel, dark steel, gilt, plume
    for sx in (-1.0, 1.0):
        x = sx * 0.1
        # Sabatons, greaves, poleyns, cuisses.
        mk.box(m, (x - 0.055, 0.0, -0.07), (x + 0.055, 0.075, 0.15), "metal", D, skip=("-y",))
        _lathe(m, [(0.055, 0.075), (0.062, 0.24), (0.07, 0.38), (0.058, 0.48)], 8, "metal", S, (x, 0.0, 0.0), sz=0.9)
        _lathe(m, [(0.058, 0.48), (0.08, 0.52), (0.082, 0.55), (0.06, 0.59)], 8, "metal", S, (x, 0.0, 0.0), sz=0.9)
        _lathe(m, [(0.064, 0.59), (0.085, 0.74), (0.095, 0.88)], 8, "metal", S, (x, 0.0, 0.0), sz=0.85)
        # Pauldron, rerebrace, couter, vambrace and gauntlet.
        _lathe(m, [(0.105, 1.36), (0.125, 1.42), (0.1, 1.49), (0.0, 1.52)], 8, "metal", S, (sx * 0.235, 0.0, 0.0), sz=0.9)
        _lathe(m, [(0.05, 1.13), (0.057, 1.36)], 6, "metal", D, (sx * 0.25, 0.0, 0.0))
        _lathe(m, [(0.05, 1.03), (0.068, 1.08), (0.05, 1.13)], 6, "metal", S, (sx * 0.25, 0.0, 0.0))
        mk.rod(m, (sx * 0.24, 1.07, 0.02), (sx * 0.07, 0.99, 0.16), 0.045, 6, "metal", S)
        mk.box(m, (sx * 0.06 - 0.04, 0.93, 0.13), (sx * 0.06 + 0.04, 1.02, 0.21), "metal", D)
    # Fauld and tassets, breastplate (gilt neck band), gorget, helm.
    _lathe(m, [(0.2, 0.86), (0.19, 0.96), (0.17, 1.04)], 10, "metal", S, sz=0.68)
    _lathe(m, [(0.17, 1.04), (0.2, 1.18), (0.22, 1.34), (0.2, 1.45)], 10, "metal", S, sz=0.66)
    _lathe(m, [(0.2, 1.45), (0.13, 1.53)], 10, "metal", G_, sz=0.66)
    _lathe(m, [(0.11, 1.53), (0.085, 1.6)], 8, "metal", D)
    _lathe(m, [(0.085, 1.6), (0.11, 1.67), (0.12, 1.77), (0.105, 1.87), (0.06, 1.93), (0.0, 1.95)], 8, "metal", S,
           sz=1.08)
    # The visor's sight and a crest of plume.
    mk.box(m, (-0.07, 1.77, 0.112), (0.07, 1.79, 0.125), "dark", 0)
    fin = [(0.0, 1.9, -0.12), (0.0, 1.98, -0.08), (0.0, 2.02, 0.02), (0.0, 1.95, 0.08)]
    for sx in (-1.0, 1.0):
        pts = [(sx * 0.012, y, z) for _, y, z in fin]
        m.poly(pts if mk.newell(pts)[0] * sx > 0 else list(reversed(pts)), "fabric", zone=R)
    # The longsword, point down in front: blade, cross-guard, grip and pommel.
    mk.box(m, (-0.022, 0.08, 0.17), (0.022, 0.86, 0.182), "metal", S)
    mk.box(m, (-0.13, 0.86, 0.16), (0.13, 0.885, 0.19), "metal", G_)
    mk.box(m, (-0.016, 0.885, 0.166), (0.016, 1.0, 0.186), "leather", 5)
    mk.lathe(m, [(0.0, 0.0), (0.03, 0.02), (0.0, 0.05)], 6, "metal", G_, (0.0, 1.0, 0.176))
    p = from_mesh(m, ARMOUR_ZONES)
    p.meta.update(height=2.02, depth=(-0.15, 0.21), width=0.36)
    return p


# -- the Owl Exchange court ----------------------------------------------------------------


def owl_counter(width: float = 8.0) -> Piece:
    """`kit_owl_counter`: the Owl Exchange counter (`kit_models.
    owl_exchange_counter`), `width` metres along the wall."""
    k = km.owl_exchange_counter(width=width)
    p = from_mesh(k.mesh, COUNTER_ZONES, walkthrough=True)
    # The span it covers on its wall (plinths break round it): x across, heights.
    p.meta.update(wall=(-width / 2.0 - 0.07, width / 2.0 + 0.07, 0.0, 4.8))
    # Castle kit M2 review: a coarse camera shell (assemble.py adds meta
    # "collider" to the cell's collider, as for the vaults). The players walk
    # through the counter, but the camera swinging towards the wall ended
    # inside the pigeonhole cabinet or the desk (1.6 % of a full-zoom sweep's
    # frames in the hall, nearly all in the court).
    p.meta["collider"] = counter_shell(width)
    return p


def counter_shell(width: float) -> list:
    """Boxes round `owl_exchange_counter`'s pigeonhole cabinet (to its
    cornice) and its desk, without bottoms: front, top and ends, in the
    piece's frame (x along the wall, z out of it)."""
    out: list = []

    def box(x0, x1, y1, z0, z1, back: bool):
        out.append([(x0, 0.0, z1), (x1, 0.0, z1), (x1, y1, z1), (x0, y1, z1)])  # front
        out.append([(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)])  # top
        out.append([(x0, 0.0, z0), (x0, 0.0, z1), (x0, y1, z1), (x0, y1, z0)])  # ends
        out.append([(x1, 0.0, z1), (x1, 0.0, z0), (x1, y1, z0), (x1, y1, z1)])
        if back:
            out.append([(x1, 0.0, z0), (x0, 0.0, z0), (x0, y1, z0), (x1, y1, z0)])

    hl = width / 2.0
    box(-hl - 0.07, hl + 0.07, 3.7, 0.0, 0.69, False)  # cabinet to the cornice (kit_models: 0.66 + 2.85 + 0.18)
    box(-hl + 0.12, hl - 0.12, 1.08, 1.55, 2.59, True)  # the desk
    return out


def owl_perch(height: float = 1.55) -> Piece:
    """`kit_owl_perch`: a T-perch on a weighted foot with a perched owl;
    at most 300 triangles."""
    m = mk.Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.26, 0.0), (0.26, 0.05), (0.18, 0.09), (0.05, 0.12)], 8, "wood", 2, cap_bottom=False)
    mk.lathe(m, [(0.035, 0.12), (0.03, height)], 6, "wood", 2, cap_top=False)
    mk.rod(m, (-0.32, height, 0.0), (0.32, height, 0.0), 0.024, 6, "wood", 2)
    for sx in (-1.0, 1.0):
        mk.lathe(m, [(0.0, -0.03), (0.035, 0.0), (0.0, 0.03)], 6, "metal", 3, (sx * 0.33, height, 0.0))
    km._owl(m, (0.12, height + 0.02, 0.0), 0.4, 8, 11, s=1.6)
    p = from_mesh(m, PERCH_ZONES, walkthrough=True)
    p.meta.update(height=height + 0.35)
    return p
