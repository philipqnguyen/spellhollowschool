"""Pieces for the courtyard's grounds side
(docs/art/castle-kit.md "Exterior").

`kit_corbel_table_<w>`: a row of stepped stone corbels under an overhang
seen from outside (the gatehouse's towers and gallery run back over the
courtyard's 1 m outer wall and 1.3 m beyond its outer face, castle-kit 32
open item 4). Frame as the courtyard's wall pieces but facing out: origin
on the wall's outer face at the floor, the corbels in front of it (z > 0),
centred on x, their tops at `top` (the overhang's underside), `depth` deep
and `drop` tall. Carved stone on the trim sheet's corbel tiles, as the
gatehouse's own corbels (`castle_trim|fit:corbel_front` /
`corbel_side`). No colour is named here.
"""

from __future__ import annotations

from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece

CORBEL_PITCH = 1.2  # target spacing of the corbels' centres
CORBEL_W = 0.4
CORBEL_TRIS = 24  # LOD0 triangles per corbel (two tiers, five faces each)


def _fit(f: list, lo, hi, tile: str, skip=()) -> None:
    geom.box(f, lo, hi, f"castle_trim|fit:{tile}", skip=skip, uv_fn=lambda side, pts: [(0, 0), (1, 0), (1, 1), (0, 1)])


def corbel_table(width: float, _h=None, _var=None, top: float = G.COURT_WALL_TOP, depth: float = 1.25,
                 drop: float = 1.1) -> Piece:
    """`kit_corbel_table_<w>`: corbels across `width` (about every
    CORBEL_PITCH m, the end ones 0.3 m in), each a lower tier half as deep
    under a full-depth upper tier, both open at the back (the wall) and on
    top (the overhang). At most CORBEL_TRIS triangles a corbel."""
    if not 1.0 <= width <= 40.0:
        raise ValueError(f"kit_corbel_table: width {width} outside 1-40 m")
    if not (0.4 <= depth <= 2.0 and 0.4 <= drop <= 2.0 and top > drop):
        raise ValueError("kit_corbel_table: depth and drop 0.4-2 m, under the top")
    p = Piece("")
    f = p.faces
    span = width - 0.6
    n = max(2, int(round(span / CORBEL_PITCH)) + 1)
    split = top - drop * 0.45
    for i in range(n):
        x = -span / 2.0 + i * span / (n - 1)
        hw = CORBEL_W / 2.0
        _fit(f, (x - hw, split, 0.0), (x + hw, top, depth), "corbel_front", skip=("-z", "+y"))
        _fit(f, (x - hw * 0.85, top - drop, 0.0), (x + hw * 0.85, split, depth * 0.55), "corbel_side", skip=("-z", "+y"))
    p.meta.update(corbels=n, top=top, depth=depth)
    return p
