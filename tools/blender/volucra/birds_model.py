"""Bird Conjuring's birds (spells to level 20, docs/architecture.md "Spells and dueling"):
one small songbird of our own design, about 0.16 m
from beak to tail, in three parts the client draws as thin instances
(client/src/game/spells/volucra.ts):

* `bird_body`: a plump egg of a body, a round head, two dark eyes, a short
  pointed beak and a little fanned tail; origin at the body's middle,
  facing +z, Y up;
* `bird_wing_l`, `bird_wing_r`: flat tapered wings, each with its origin at
  its shoulder, so the client flaps it about the body's forward axis.

And the spell's conjured object (Bird Conjuring is Transfiguration: a small object
becomes the flutter of birds): `bird_feather`, one pale curved feather
about 0.13 m long, its quill along +z, origin at its middle; the client
conjures it at the wand tip and turns it into the birds.

The bird is pale (the client tints each one with a songbird colour, which
multiplies the swatch): a pale back and wings, a paler breast, dark eyes and
a tan beak, all from castle_textures.json `palette` by name (no colour
literal here), on one small swatch texture: three draws for every bird in
the air, at most `MAX_TRIS` triangles a bird.

Pure Python (no bpy): build_birds.py exports it.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_BLENDER, os.path.join(_BLENDER, "world")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import beast_props as BP  # noqa: E402  (its Part and sphere/cone helpers)
from meshkit import Mesh  # noqa: E402

PROP = "birds"
MAX_TRIS = 300
PARTS = ("bird_body", "bird_wing_l", "bird_wing_r")
FEATHER = "bird_feather"
FEATHER_TRIS = 60
FEATHER_LENGTH = 0.13
# Zones -> palette names (castle_textures.json `palette`).
BACK, BREAST, EYE, BEAK = 1, 2, 3, 4
ZONES = {BACK: "letter_white", BREAST: "owl_pale", EYE: "soot", BEAK: "parcel_tan"}
# The shoulders, either side of the back (the wings' origins).
SHOULDER = (0.028, 0.016, 0.008)
WING_SPAN = 0.11


def body_mesh() -> Mesh:
    """The body about its middle: an egg, a head, eyes, beak and tail."""
    m = Mesh()
    mark = BP._sphere(m, (0.0, 0.0, 0.0), 1.0, BACK, seg=8, rings=5, scale=(0.032, 0.034, 0.056))
    for f in m.faces[mark[1]:]:
        c = [m.verts[k] for k in f.idx]
        if sum(v[1] for v in c) / len(c) < -0.008:
            f.zone = BREAST  # the breast and belly
    BP._sphere(m, (0.0, 0.026, 0.052), 1.0, BACK, seg=8, rings=5, scale=(0.024, 0.023, 0.025))
    for sx in (-1.0, 1.0):
        BP._sphere(m, (sx * 0.017, 0.032, 0.066), 0.006, EYE, seg=4, rings=2)
    BP._cone(m, (0.0, 0.024, 0.074), (0.0, 0.02, 0.094), 0.0075, BEAK, seg=4)
    # A small fanned tail, both sides.
    tail = [(-0.008, 0.004, -0.048), (0.008, 0.004, -0.048), (0.022, 0.012, -0.098), (-0.022, 0.012, -0.098)]
    m.poly(tail, "fur", zone=BACK)
    m.poly(list(reversed(tail)), "fur", zone=BACK)
    return m


def wing_mesh(side: float) -> Mesh:
    """One wing about its shoulder: a flat tapered blade along `side` * x
    (top and underside), swept back a little at the tip."""
    m = Mesh()
    s = side
    blade = [(0.0, 0.0, 0.026), (s * 0.05, 0.004, 0.02), (s * WING_SPAN, 0.006, -0.012),
             (s * 0.07, 0.003, -0.034), (0.0, 0.0, -0.03)]
    up = blade if s > 0 else list(reversed(blade))  # wound to face +y
    m.poly(up, "fur", zone=BACK)
    m.poly(list(reversed(up)), "fur", zone=BREAST)
    return m


def feather_mesh() -> Mesh:
    """The conjured feather about its middle: a curved vane either side of
    its quill (wider on one side, as a flight feather is), top and
    underside, the quill's bare end below it. The quill runs along +z; the
    vane curls up a little toward the tip."""
    m = Mesh()
    half = FEATHER_LENGTH / 2
    # Along the quill (z, from the base of the vane to the tip): the vane's width either side.
    stations = [(-half + 0.012, 0.004, 0.003), (-0.03, 0.014, 0.008), (0.0, 0.019, 0.011),
                (0.03, 0.017, 0.010), (half - 0.01, 0.009, 0.006), (half, 0.0, 0.0)]

    def lift(z):
        return 0.012 * ((z + half) / FEATHER_LENGTH) ** 2

    for side, k in ((1.0, 1), (-1.0, 2)):
        for (z0, *w0), (z1, *w1) in zip(stations, stations[1:]):
            q = [(0.0, lift(z0), z0), (0.0, lift(z1), z1),
                 (side * w1[k - 1], lift(z1) + 0.002, z1), (side * w0[k - 1], lift(z0) + 0.002, z0)]
            up = q if side > 0 else list(reversed(q))  # wound to face +y
            m.poly(up, "fur", zone=BACK)
            m.poly(list(reversed(up)), "fur", zone=BREAST)
    # The bare quill below the vane: a thin strip, both sides.
    z0, z1 = -half - 0.012, stations[0][0]
    strip = [(-0.0015, lift(z0), z0), (0.0015, lift(z0), z0), (0.0015, lift(z1), z1), (-0.0015, lift(z1), z1)]
    m.poly(list(reversed(strip)), "fur", zone=BEAK)
    m.poly(strip, "fur", zone=BEAK)
    return m


def feather() -> BP.Part:
    """The conjured feather (its own node, not part of the bird)."""
    return BP.Part(FEATHER, feather_mesh())


def build() -> list[BP.Part]:
    """The three parts, the wings at their shoulders (Y up, metres)."""
    sx, sy, sz = SHOULDER
    return [
        BP.Part("bird_body", body_mesh()),
        BP.Part("bird_wing_l", wing_mesh(1.0), (sx, sy, sz)),
        BP.Part("bird_wing_r", wing_mesh(-1.0), (-sx, sy, sz)),
    ]


def triangles(parts) -> int:
    return sum(p.mesh.triangle_count() for p in parts)


def manifest(parts, conjured: BP.Part | None = None) -> dict:
    """What the client may read about the model (its parts and size)."""
    lo, hi = parts[0].mesh.bounds()
    extra = {}
    if conjured is not None:
        flo, fhi = conjured.mesh.bounds()
        extra = {"feather": {"part": conjured.name, "length_m": round(fhi[2] - flo[2], 3),
                             "triangles": conjured.mesh.triangle_count()}}
    return {
        "version": 1,
        "parts": [p.name for p in parts],
        "triangles": triangles(parts),
        "length_m": round(hi[2] - lo[2], 3),
        "shoulders": {p.name: list(p.origin) for p in parts[1:]},
        "tint": "the instance colour multiplies the pale swatch",
        **extra,
    }
