"""The Flying class's icons (docs/architecture.md),
rendered by render_icons.py with the other class icons at
`icons/class/<name>.png`, but kept out of icon_models.CLASS_ICONS (which lists
exactly the picks' icons: the Flying class has no picks, only ring beats).

* `ring`: a practice ring standing on a little foot, as the course's rings
  are drawn (the `ring_hoop` prop's gilt), for the ring beat and the lesson.
* `broom`: a riding broom lying at a slight slant: a long ash handle, a
  bound tail of twigs and a gilt band.

Pure Python (meshkit); faces carry a furniture atlas tile and a dye zone
(1 primary, 2 secondary, 3 trim) like icon_models.py; colours are
palette.json ids. Every model sits on y = 0, under 0.5 m across. Our own
plain designs."""

from __future__ import annotations

import math
import os
import sys

_WORLD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "world")
if _WORLD not in sys.path:
    sys.path.insert(0, _WORLD)

import meshkit as mk  # noqa: E402
from meshkit import Mesh  # noqa: E402

P, S, T = 1, 2, 3


def flying_ring() -> Mesh:
    """A ring 0.2 m across in the xy plane (facing the camera), on a short post and a round foot."""
    m = Mesh()
    r, t, cy = 0.1, 0.018, 0.16
    seg, sides = 28, 8
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        for j in range(sides):
            b0, b1 = 2 * math.pi * j / sides, 2 * math.pi * (j + 1) / sides

            def p(a, b):
                rr = r + t * math.cos(b)
                return (rr * math.cos(a), cy + rr * math.sin(a), t * math.sin(b))

            m.poly([p(a0, b0), p(a1, b0), p(a1, b1), p(a0, b1)], "metal", zone=P)
    mk.rod(m, (0.0, 0.025, 0.0), (0.0, cy - r - t * 0.5, 0.0), 0.01, 8, "metal", T, caps=False)
    mk.lathe(m, [(0.0, 0.0), (0.05, 0.0), (0.052, 0.01), (0.03, 0.022), (0.012, 0.03)], 14, "metal", T, cap_top=False)
    return m


def flying_broom() -> Mesh:
    """A broom 0.42 m long resting on its tail, the handle rising a little."""
    m = Mesh()
    tail0, tail1 = (-0.2, 0.035, 0.0), (-0.08, 0.05, 0.0)
    tip = (0.21, 0.1, 0.0)
    mk.rod(m, (-0.11, 0.048, 0.0), tip, 0.009, 8, "wood", P)
    # The tail: a tapering bundle of twigs (a lathe along x), its end splayed.
    prof = [(0.012, 0.0), (0.03, 0.03), (0.042, 0.08), (0.05, 0.12), (0.046, 0.125)]
    mark = m.mark()
    mk.lathe(m, prof, 12, "wood", S, cap_top=True)
    d = mk.normalize(mk.sub(tail0, tail1))

    def orient(q):  # the lathe's +y onto the tail's axis (toward its end)
        x, y, z = q
        side = (0.0, 0.0, 1.0)
        up = mk.normalize(mk.cross(side, d))
        return (tail1[0] + d[0] * y + up[0] * x, tail1[1] + d[1] * y + up[1] * x, tail1[2] + z)

    m.transform(mark, orient)
    # Two gilt bands binding the tail to the handle.
    for k, at in enumerate((0.0, 0.025)):
        c = mk.add(tail1, mk.scale(d, at))
        mk.rod(m, mk.add(c, mk.scale(d, -0.005)), mk.add(c, mk.scale(d, 0.005)), 0.018 + 0.006 * k, 10, "metal", T)
    low = m.bounds()[0][1]
    m.transform((0, 0), lambda q: (q[0], q[1] - low, q[2]))
    return m


# name -> (model, (primary, secondary, trim) palette.json colour ids)
FLYING_ICONS = {
    "ring": (flying_ring, ("gold", "gold", "bronze")),
    "broom": (flying_broom, ("chestnut", "tan", "gold")),
}
