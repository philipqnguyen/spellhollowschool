"""The crafted fashion garments  (catalog ids 103-112,
210-219, 304-308, 404-408), cut against the base bodies like gear.py.

Importing this module registers the builders in `gear.BUILDERS`. The
garments come in Transfiguration restyle families (felt hats, knitted
headwear, hoods, hair trims, outer layers, knitted tops, dress robes, fine
tops, skirts, tailored trousers, boots and fine shoes); members of a family
share a construction and differ in cut and detail, so a restyle reads as the
same garment reshaped.

The layer rules of gear.py apply: inner items 3-12 mm off the skin, outer
items 18-35 mm, hats on the head cage (scalp + about 15 mm, over the pressed
hair cap), and hidden body regions are never drawn, so a garment only has to
cover what it hides.
"""

from __future__ import annotations

import math

import numpy as np

from . import gear as G
from . import skeleton as K
from .gear import (HAT_COLS, _down_axis, _front_ness, _limb_axis, _radial, _row_z, _static, builder, cuff,
                   hanging_skirt, head_band_ring, legs_shell, opening_edge, rib_band, row_of_z, shoe_collar, shoe_shell,
                   snap_opening, sole_slab, top_shell, trouser_legs, turned_hem, v_neck, waistband)
from .gear_ctx import Landmarks, parse_tag
from .gear_lib import NONE, PRIMARY, SECONDARY, TRIM, GearBuilder, vertex_normals

HEAD_W = {"DEF-spine.006": 1.0}
# Body items whose LOD0 may use the long-robe budget (8,000 triangles).
LONG_BODY = {"gear/body/cloak_hooded_wool_a", "gear/body/greatcoat_double_a", "gear/body/robe_dress_embroidered_a",
             "gear/body/robe_formal_velvet_a"}


# ----------------------------------------------------------------------------
# Small shared parts
# ----------------------------------------------------------------------------


def _unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-12)


def _frame(normal: np.ndarray, up_hint=(0.0, 0.0, 1.0)) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors spanning the plane facing `normal` (right, up)."""
    n = _unit(np.asarray(normal, dtype=np.float64))
    up = np.asarray(up_hint, dtype=np.float64)
    right = np.cross(up, n)
    if np.linalg.norm(right) < 1e-6:
        right = np.cross(np.array([0.0, 1.0, 0.0]), n)
    right = _unit(right)
    return right, np.cross(n, right)


def _normals(gb: GearBuilder, faces=None) -> dict[str, np.ndarray]:
    """Vertex normals of the item as built so far (or of `faces`, item face
    ids, e.g. a shell whose winding is already outward), per shape."""
    fs = gb.faces if faces is None else [gb.faces[i] for i in faces]
    return {s: vertex_normals(gb.positions(s), fs) for s in gb.shapes}


def normals_outward(gb: GearBuilder, faces) -> dict[str, np.ndarray]:
    """`_normals` for faces of unknown winding (built before `orient`): each
    normal is flipped to point away from the body's vertical axis."""
    out = _normals(gb, faces)
    for s in gb.shapes:
        P = gb.positions(s)
        away = P.copy()
        away[:, 2] = 0.0
        flip = np.sum(out[s] * away, axis=1) < 0
        out[s][flip] *= -1
    return out


def anchored(gb: GearBuilder, vid: int, local, normals: dict, lift: float = 0.0, up_hint=(0.0, 0.0, 1.0)) -> np.ndarray:
    """Vertices placed in the surface frame of item vertex `vid` (x right,
    y up, z out along its normal, metres), in every shape, skinned like it."""
    out = {}
    for s in gb.shapes:
        p = gb.positions(s)[vid]
        n = normals[s][vid]
        r, u = _frame(n, up_hint)
        out[s] = np.array([p + n * (lift + z) + r * x + u * y for x, y, z in local])
    return gb.add(out, [dict(gb.weights[vid]) for _ in local])


def disc(gb: GearBuilder, vid: int, radius: float, lift: float, swatch: str, mask, sides: int = 6,
         normals: dict | None = None, squash: float = 1.0, dome: float = 0.0) -> None:
    """A small round part (button, stud, jewel) sitting on the item surface
    at vertex `vid`, `lift` metres out along its normal, following the vertex
    in every shape and skinned like it. `dome` raises the centre."""
    normals = normals or _normals(gb)
    rims, centres = {}, {}
    for s in gb.shapes:
        p = gb.positions(s)[vid]
        n = normals[s][vid]
        right, up = _frame(n)
        ring = [p + n * lift + (right * math.cos(a) + up * math.sin(a) * squash) * radius
                for a in np.linspace(0, 2 * math.pi, sides, endpoint=False)]
        rims[s] = np.array(ring)
        centres[s] = (p + n * (lift + dome + radius * 0.25))[None]
    w = dict(gb.weights[vid])
    ring = gb.add(rims, [w] * sides)
    gb.cap(ring, centres, w, (swatch, mask))


def plate(gb: GearBuilder, vid: int, width: float, height: float, lift: float, swatch: str, mask,
          normals: dict | None = None, hole: float = 0.0, up_hint=(0.0, 0.0, 1.0)) -> None:
    """A flat rectangle (buckle, tag, clasp) on the surface at `vid`; with
    `hole` > 0 it is a frame whose opening is that fraction of its size."""
    normals = normals or _normals(gb)
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    outer, inner = {}, {}
    for s in gb.shapes:
        p = gb.positions(s)[vid]
        n = normals[s][vid]
        right, up = _frame(n, up_hint)
        c = p + n * lift
        outer[s] = np.array([c + right * x * width / 2 + up * y * height / 2 for x, y in corners])
        inner[s] = np.array([c + n * 0.001 + right * x * width / 2 * hole + up * y * height / 2 * hole for x, y in corners])
    w = dict(gb.weights[vid])
    a = gb.add(outer, [w] * 4)
    if hole <= 0:
        gb.face(tuple(a), swatch, mask)
        return
    b = gb.add(inner, [w] * 4)
    for i in range(4):
        j = (i + 1) % 4
        gb.face((a[i], a[j], b[j], b[i]), swatch, mask)


def boundary_loops(gb: GearBuilder, face_ids) -> list[list[int]]:
    """Ordered vertex loops along the open boundary of a set of item faces."""
    count: dict[tuple[int, int], int] = {}
    directed = []
    for fi in face_ids:
        f = gb.faces[fi]
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            count[(min(a, b), max(a, b))] = count.get((min(a, b), max(a, b)), 0) + 1
            directed.append((a, b))
    nxt = {}
    for a, b in directed:
        if count[(min(a, b), max(a, b))] == 1:
            nxt[a] = b
    loops, seen = [], set()
    for start in list(nxt):
        if start in seen:
            continue
        loop, v = [], start
        while v not in seen and v in nxt:
            seen.add(v)
            loop.append(v)
            v = nxt[v]
        if len(loop) > 2:
            loops.append(loop)
    return loops


def smooth_loop(gb: GearBuilder, loop: list[int], iters: int = 10) -> None:
    """Smooths a closed boundary loop of item vertices along itself (Taubin
    steps, so it does not shrink), in every shape: an opening cut along the
    body grid becomes a clean curve instead of a staircase."""
    loop = list(loop)
    for s in gb.shapes:
        P = gb.positions(s)
        Q = P[loop].copy()
        for i in range(iters * 2):
            lam = 0.5 if i % 2 == 0 else -0.53
            Q = Q + lam * ((np.roll(Q, 1, axis=0) + np.roll(Q, -1, axis=0)) / 2 - Q)
        P[loop] = Q
        gb.pos[s] = [P]


def roll(gb: GearBuilder, loop: list[int], toward: np.ndarray, size: float, paint, closed: bool = True) -> list[int]:
    """A soft rolled edge (fur trim, rolled collar) along an item boundary
    loop: it bulges out along the surface normal and curls toward `toward`
    (a point the edge faces, e.g. the middle of a face opening)."""
    normals = normals_outward(gb, range(len(gb.faces)))
    loop = list(loop)
    toward = np.asarray(toward, dtype=np.float64)

    def make(s, r0, k):
        n = normals[s][loop]
        d = toward - r0
        d -= n * np.sum(d * n, axis=1, keepdims=True)
        d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
        prof = {1: (0.9, 0.25), 2: (1.0, 0.85), 3: (0.35, 1.3), 4: (-0.45, 1.0)}[k]
        return r0 + n * size * prof[0] + d * size * prof[1]

    n0 = len(gb.faces)
    gb.strip(loop, make, 4, paint, closed=closed)
    return list(range(n0, len(gb.faces)))


def head_angles(L: Landmarks, p: np.ndarray) -> tuple[float, float]:
    """(phi, theta) of a point seen from the head centre: phi from straight
    up, theta signed degrees from the front (+ toward the character's left)."""
    centre = L.head_centre()
    d = (np.asarray(p) - centre) / (0.01 * L.head_scale())
    d = np.array([d[0], -d[1], d[2]])
    phi = math.degrees(math.acos(float(np.clip(d[2] / max(np.linalg.norm(d), 1e-9), -1, 1))))
    theta = math.degrees(math.atan2(d[0], d[1]))
    return phi, theta


def head_ring(L: Landmarks, phi_front: float, phi_back: float, extra_cm: float, cols: int = HAT_COLS,
              tilt: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Points round the head at polar angle phi (blended from the front to
    the back), `extra_cm` off the sculpt. `tilt` = (left-right, front-back)
    degrees added to phi on the left and the front."""
    pts = []
    for j in range(cols):
        th = 360.0 * j / cols
        side = th if th <= 180 else 360 - th
        w = (1 - math.cos(math.radians(side))) / 2.0
        phi = phi_front + (phi_back - phi_front) * w
        phi += tilt[0] * math.sin(math.radians(th)) + tilt[1] * math.cos(math.radians(th))
        pts.append(L.head_point(phi, th, extra_cm))
    return np.array(pts)


def _knit(k: int, c: int, mask=PRIMARY) -> tuple[str, tuple]:
    return ("dye_soft" if c % 2 else "dye"), mask


# ----------------------------------------------------------------------------
# Hats: felt hats (witch hat, cloche, beret)
# ----------------------------------------------------------------------------


def plate_static(gb: GearBuilder, centre: np.ndarray, normal: np.ndarray, width: float, height: float, swatch: str,
                 mask, hole: float = 0.0, weight: dict | None = None) -> None:
    """`plate` for rigid head items: a rectangle (or frame) at a fixed point."""
    right, up = _frame(normal)
    w = weight or HEAD_W
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    outer = np.array([centre + right * x * width / 2 + up * y * height / 2 for x, y in corners])
    a = _static(gb, outer, w)
    if hole <= 0:
        gb.face(tuple(a), swatch, mask)
        return
    inner = np.array([centre + normal * 0.001 + right * x * width / 2 * hole + up * y * height / 2 * hole for x, y in corners])
    b = _static(gb, inner, w)
    for i in range(4):
        j = (i + 1) % 4
        gb.face((a[i], a[j], b[j], b[i]), swatch, mask)


def _brim(gb: GearBuilder, ring0: np.ndarray, centre: np.ndarray, profile, waves: int, wave: float, mask=PRIMARY,
          sw: str = "dye") -> tuple[list[int], np.ndarray]:
    """Brim rings out from the band ring `ring0` (item vertex ids):
    `profile` = [(out, drop), ...] metres."""
    band = gb.positions("base")[ring0]
    rings = [ring0]
    n0 = len(gb.faces)
    for k, (out, drop) in enumerate(profile, start=1):
        p = _radial(band, centre, out)
        t = k / len(profile)
        p[:, 2] -= drop + wave * t * np.sin(np.arange(len(band)) * 2 * np.pi * waves / len(band))
        rings.append(_static(gb, p, HEAD_W))
    gb.connect(rings, lambda k, c: (sw, mask))
    return list(range(n0, len(gb.faces))), rings[-1]


@builder("gear/hat/witch_wide_brim_a")
def hat_witch(gb: GearBuilder, L: Landmarks) -> dict:
    """A wide, softly waved brim and a tall crown with a crooked tip, a
    ribbon band and a square buckle at the front."""
    s = L.head_scale()
    band = head_band_ring(L, 2.4 * s)
    c = band.mean(axis=0)
    ring0 = _static(gb, band, HEAD_W)
    brim_faces, _ = _brim(gb, ring0, c, [(0.045 * s, 0.003 * s), (0.10 * s, 0.012 * s), (0.155 * s, 0.026 * s),
                                         (0.185 * s, 0.03 * s)], 4, 0.012 * s)
    crown = []
    n_c = 12
    top_z = 0.40 * s
    for k in range(1, n_c + 1):
        t = k / n_c
        r = (1 - t) ** 1.05 * (1.0 + 0.05 * math.sin(t * math.pi))
        # The tip leans back, then folds over at two thirds of the height.
        kink = max(0.0, t - 0.62) / 0.38
        bend = np.array([0.018 * s * math.sin(t * math.pi), 0.09 * s * t ** 2 + 0.07 * s * kink ** 1.5,
                         -0.13 * s * kink ** 2])
        centre = c + np.array([0.0, 0.0, top_z * t]) + bend
        p = centre + (band - c) * np.array([1.0, 1.0, 0.0]) * r
        p[:, 2] = centre[2] + (band[:, 2] - c[2]) * r
        crown.append(_static(gb, p, HEAD_W))
    band_rows = 2  # the ribbon band: the first rings of the crown

    def paint(k, cc):
        return ("dye", SECONDARY) if k < band_rows else ("dye_soft" if k % 3 == 2 else "dye", PRIMARY)

    gb.connect([ring0] + crown, paint)
    tip = gb.positions("base")[crown[-1]].mean(axis=0) + np.array([0.0, 0.012 * s, -0.012 * s])
    gb.cap(crown[-1], {sh: tip[None] for sh in gb.shapes}, HEAD_W, ("dye", PRIMARY))
    # Buckle on the band at the front.
    P = gb.positions("base")
    mid = (P[int(ring0[0])] + P[int(crown[1][0])]) / 2
    out = _unit(np.array([mid[0] - c[0], mid[1] - c[1], 0.0]))
    plate_static(gb, mid + out * 0.004, out, 0.034 * s, 0.03 * s, "gold", TRIM, hole=0.55)
    return {"hides": ["scalp"], "long": False,
            "thicken": [(brim_faces, 0.004, ("dye_shade", PRIMARY), ("dye", PRIMARY))],
            "orient_ref": c + np.array([0.0, 0.0, 0.05])}


@builder("gear/hat/cloche_felt_a")
def hat_cloche(gb: GearBuilder, L: Landmarks) -> dict:
    """A close bell-shaped felt hat with a short downturned brim, a ribbon
    band and a felt rosette on the left side."""
    s = L.head_scale()
    rows = 9
    rings = []
    for i in range(rows + 1):
        t = i / rows
        # A smooth dome: a little fuller over the crown than round the band.
        extra = (2.5 + 0.7 * math.sin(math.pi * 0.5 * min(1.0, t * 1.6))) * s
        rings.append(_static(gb, head_ring(L, 58.0 * (1 - t) + 10.0 * t, 80.0 * (1 - t) + 10.0 * t, extra), HEAD_W))

    def paint(k, c):
        return ("dye", SECONDARY) if k < 2 else ("dye_soft" if k % 4 == 3 else "dye", PRIMARY)

    gb.connect(rings, paint)
    top = gb.positions("base")[rings[-1]].mean(axis=0)
    gb.cap(rings[-1], {sh: top[None] for sh in gb.shapes}, HEAD_W, ("dye", PRIMARY))
    band = gb.positions("base")[rings[0]]
    c = band.mean(axis=0)
    brim_faces, _ = _brim(gb, rings[0], c, [(0.01 * s, 0.014 * s), (0.02 * s, 0.032 * s), (0.028 * s, 0.042 * s),
                                            (0.032 * s, 0.047 * s)], 3, 0.004 * s)
    # A felt flower on the band at the left: two rings of petals and a button.
    j = HAT_COLS // 4 - 2
    p0 = (gb.positions("base")[rings[0][j]] + gb.positions("base")[rings[1][j]]) / 2
    out = _unit(np.array([p0[0] - c[0], p0[1] - c[1], 0.0]))
    right, up = _frame(out)
    for radius, lift, petals, mask in ((0.046, 0.006, 12, TRIM), (0.028, 0.012, 10, SECONDARY)):
        centre = p0 + out * lift
        pts = []
        for i in range(petals):
            a = 2 * math.pi * i / petals
            r = radius * s * (1.0 if i % 2 == 0 else 0.62)
            pts.append(centre + (right * math.cos(a) + up * math.sin(a)) * r + out * 0.003 * (i % 2))
        ring = _static(gb, np.array(pts), HEAD_W)
        gb.cap(ring, {sh: (centre + out * 0.006)[None] for sh in gb.shapes}, HEAD_W, ("dye", mask))
    plate_static(gb, p0 + out * 0.02, out, 0.016 * s, 0.016 * s, "dye_light", TRIM)
    # Worn low in the 1920s way, its brim over the tops of the ears.
    return {"hides": ["scalp", "ears"], "long": False,
            "thicken": [(brim_faces, 0.003, ("dye_shade", PRIMARY), ("dye", PRIMARY))], "orient_ref": c}


@builder("gear/hat/beret_velvet_a")
def hat_beret(gb: GearBuilder, L: Landmarks) -> dict:
    """A soft velvet beret worn tilted to the right: a narrow band, a wide
    flat crown overhanging it, and a little stalk on top."""
    s = L.head_scale()
    band = head_ring(L, 52.0, 72.0, 2.3 * s, tilt=(-9.0, -3.0))
    c = band.mean(axis=0)
    ring0 = _static(gb, band, HEAD_W)
    up = _unit(np.array([-0.24, 0.06, 1.0]))
    rings = [ring0]
    # (radial scale of the band, height above the band centre) per ring: a
    # narrow band, then a soft round crown much wider than the head.
    prof = [(1.0, 0.012), (1.24, 0.026), (1.42, 0.041), (1.43, 0.055), (1.26, 0.068), (0.92, 0.078), (0.46, 0.084)]
    for k, (scale_r, h) in enumerate(prof):
        p = c + (band - c) * scale_r
        p = p - np.outer((p - c) @ up, up) + up * (h * s)
        # The overhang slumps on the right side.
        slump = np.clip(-(band[:, 0] - c[0]) / 0.08, 0, 1) * min(1.0, k / 2.5) * (1 - 0.6 * (k >= 5))
        p[:, 2] -= 0.02 * s * slump
        rings.append(_static(gb, p, HEAD_W))

    def paint(k, cc):
        if k == 0:
            return "dye", SECONDARY
        return ("dye_light" if k in (3, 4) and cc % 7 < 3 else "dye"), PRIMARY

    gb.connect(rings, paint)
    top = c + up * 0.086 * s
    gb.cap(rings[-1], {sh: top[None] for sh in gb.shapes}, HEAD_W, ("dye", PRIMARY))
    # The little stalk on top.
    r, u = _frame(up)
    stalk = [top + (r * math.cos(a) + u * math.sin(a)) * 0.005 * s for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)]
    sr = _static(gb, np.array(stalk), HEAD_W)
    gb.cap(sr, {sh: (top + up * 0.01 * s)[None] for sh in gb.shapes}, HEAD_W, ("dye_shade", PRIMARY))
    return {"hides": ["scalp"], "long": False, "orient_ref": c}


# ----------------------------------------------------------------------------
# Hats: knitted headwear (beanie, slouch cap, earflap hat)
# ----------------------------------------------------------------------------


def _knit_crown(gb: GearBuilder, L: Landmarks, rows: int, extra_fn, paint, phi_front: float = 58.0,
                phi_back: float = 80.0, t_max: float = 1.0) -> list[np.ndarray]:
    """Rings from the band line up toward the crown (t = 0..t_max of the way
    to the pole), `extra_fn(t, column)` cm off the head."""
    s = L.head_scale()
    rings = []
    for i in range(rows + 1):
        t = i / rows * t_max
        pts = []
        for j in range(HAT_COLS):
            th = 360.0 * j / HAT_COLS
            side = th if th <= 180 else 360 - th
            band_phi = phi_front + (phi_back - phi_front) * (1 - math.cos(math.radians(side))) / 2.0
            phi = band_phi * (1 - t) + 4.0 * t
            pts.append(L.head_point(phi, th, extra_fn(t, j) * s))
        rings.append(_static(gb, np.array(pts), HEAD_W))
    gb.connect(rings, paint)
    return rings


def _knit_cuff(gb: GearBuilder, L: Landmarks, crown: list[np.ndarray], rows: int, out: float = 0.008,
               masks=(PRIMARY, PRIMARY)) -> None:
    """A ribbed turn-up folded up from the band over the first `rows` crown
    rings, lying on them `out` metres proud (so it hugs the hat)."""
    s = L.head_scale()
    P = gb.positions("base")
    rings = [crown[0]]
    for k in range(rows + 1):
        q = P[crown[k]]
        c = q.mean(axis=0)
        p = _radial(q, c, (out * (0.75 if k == 0 else 1.0)) * s)
        if k == 0:
            p[:, 2] -= 0.002
        rings.append(_static(gb, p, HEAD_W))
    # Fold the top edge back in.
    q = P[crown[rows]]
    rings.append(_static(gb, _radial(q, q.mean(axis=0), out * 0.3 * s) + np.array([0, 0, 0.003]), HEAD_W))
    gb.connect(rings, lambda k, cc: ("dye_soft" if cc % 2 else "dye", masks[1] if k == 2 else masks[0]))


@builder("gear/hat/beanie_cable_a")
def hat_beanie(gb: GearBuilder, L: Landmarks) -> dict:
    """A close cable-knit beanie: six twisting cables up to a gathered crown
    and a deep ribbed turn-up with a contrast stripe."""
    cables = 6

    def lane(t, j):
        """0..1 across a cable lane, twisting as it climbs."""
        return (j * cables / HAT_COLS + 0.12 * math.sin(t * 9.0)) % 1.0

    def extra(t, j):
        cable = math.sin(math.pi * lane(t, j)) ** 2
        return 2.2 + 0.45 * math.sin(math.pi * t) + 0.45 * cable * (1 - t) ** 0.6

    def paint(k, c):
        x = lane(k / 10.0, c + 0.5)
        if x < 0.18 or x > 0.82:
            return "dye_shade", PRIMARY  # the purl channel between cables
        return ("dye_light" if (k + (x > 0.5)) % 2 else "dye"), PRIMARY

    rings = _knit_crown(gb, L, 10, extra, paint)
    top = gb.positions("base")[rings[-1]].mean(axis=0) + np.array([0, 0, 0.003])
    gb.cap(rings[-1], {sh: top[None] for sh in gb.shapes}, HEAD_W, ("dye_shade", PRIMARY))
    _knit_cuff(gb, L, rings, 3, masks=(PRIMARY, SECONDARY))
    return {"hides": ["scalp"], "long": False, "orient_ref": gb.positions("base")[rings[0]].mean(axis=0)}


@builder("gear/hat/cap_slouch_knit_a")
def hat_slouch(gb: GearBuilder, L: Landmarks) -> dict:
    """A slouchy knitted cap: ribbed band, then a loose body that runs past
    the crown and sags down the back of the head."""
    s = L.head_scale()

    def extra(t, j):
        return 2.2 + 0.6 * math.sin(math.pi * t * 0.8)

    def paint(k, c):
        if k < 2:
            return _knit(k, c, PRIMARY)
        return ("dye_soft" if (c + k) % 4 == 0 else "dye"), PRIMARY

    # Extra fabric toward the crown (the slouch), then gravity: the upper
    # rings slide back and down so the loose top sags behind the head.
    def extra_slouch(t, j):
        return extra(t, j) + 3.6 * t ** 1.6

    rings = _knit_crown(gb, L, 9, extra_slouch, paint)
    for i, ring in enumerate(rings):
        t = i / (len(rings) - 1)
        shift = np.array([0.0, 0.075 * s * t ** 1.8, -0.055 * s * t ** 2.2])
        for sh in gb.shapes:
            P = gb.positions(sh)
            P[ring] += shift
            gb.pos[sh] = [P]
    end = gb.positions("base")[rings[-1]].mean(axis=0)
    gb.cap(rings[-1], {sh: end[None] for sh in gb.shapes}, HEAD_W, ("dye_shade", PRIMARY))
    _knit_cuff(gb, L, rings, 1, out=0.007, masks=(SECONDARY, SECONDARY))
    return {"hides": ["scalp"], "long": False, "orient_ref": gb.positions("base")[rings[0]].mean(axis=0) + np.array([0, 0.03, 0.04])}


@builder("gear/hat/earflap_knit_a")
def hat_earflap(gb: GearBuilder, L: Landmarks) -> dict:
    """A knitted earflap hat with patterned bands, flaps over the ears ending
    in braided ties, and a pompom."""
    s = L.head_scale()

    def extra(t, j):
        return 2.3 + 0.45 * math.sin(math.pi * t)

    def paint(k, c):
        if k in (2, 3):
            return ("dye", TRIM) if (c + k) % 3 == 0 else ("dye", SECONDARY)
        if k == 5:
            return ("dye", SECONDARY) if c % 2 else ("dye", PRIMARY)
        return _knit(k, c, PRIMARY)

    rings = _knit_crown(gb, L, 9, extra, paint, phi_front=60.0, phi_back=84.0)
    top = gb.positions("base")[rings[-1]].mean(axis=0) + np.array([0, 0, 0.003])
    gb.cap(rings[-1], {sh: top[None] for sh in gb.shapes}, HEAD_W, ("dye", PRIMARY))
    # Earflaps: strips hanging from the band over each ear.
    flap_faces = []
    for side in (1, -1):
        mid = HAT_COLS // 4 if side > 0 else 3 * HAT_COLS // 4
        cols = [(mid + d) % HAT_COLS for d in range(-3, 4)]
        top_ids = [int(rings[0][c]) for c in cols]
        n = 5
        prev = np.array(top_ids)
        strip = [prev]
        for k in range(1, n + 1):
            t = k / n
            pts = []
            for i, cidx in enumerate(cols):
                th = 360.0 * cidx / HAT_COLS
                phi = 84.0 + 34.0 * t
                narrow = 1.0 - 0.55 * t * abs(i - 3) / 3
                th_c = 90.0 if side > 0 else 270.0
                th2 = th_c + (th - th_c) * narrow
                pts.append(L.head_point(phi, th2, (3.0 + 1.0 * t) * s))
            strip.append(_static(gb, np.array(pts), HEAD_W))
        n0 = len(gb.faces)
        gb.connect(strip, lambda k, c: ("dye", SECONDARY) if k == 2 else _knit(k, c, PRIMARY), closed=False)
        flap_faces += list(range(n0, len(gb.faces)))
        # A braided tie from the flap tip.
        tip = gb.positions("base")[strip[-1]].mean(axis=0)
        prev_ring = None
        for k in range(6):
            # The tie hangs a little away from the neck.
            cz = tip - np.array([0.0, 0.0, 0.025 * k * s]) + np.array([np.sign(tip[0]) * 0.007 * k, -0.002 * k, 0.0])
            cz[1] += 0.004 * math.sin(k * 1.7)
            ring = np.array([cz + np.array([math.cos(a), math.sin(a), 0.0]) * 0.006 * s
                             for a in np.linspace(0, 2 * math.pi, 5, endpoint=False)])
            ids = _static(gb, ring, HEAD_W)
            if prev_ring is not None:
                gb.connect([prev_ring, ids], lambda kk, c, k=k: ("dye", TRIM if k % 2 else SECONDARY))
            prev_ring = ids
        gb.cap(prev_ring[::-1], {sh: (cz - np.array([0, 0, 0.01]))[None] for sh in gb.shapes}, HEAD_W, ("dye", TRIM))
    # Pompom.
    pc = top + np.array([0.0, 0.0, 0.036 * s])
    pom = []
    for i in range(1, 6):
        a = math.pi * i / 6
        ring = [pc + 0.036 * s * (1.0 + 0.14 * math.sin(3 * b + i)) *
                np.array([math.sin(a) * math.cos(b), math.sin(a) * math.sin(b), math.cos(a)])
                for b in np.linspace(0, 2 * math.pi, 12, endpoint=False)]
        pom.append(_static(gb, np.array(ring), HEAD_W))
    gb.connect(pom, lambda k, cc: ("dye_soft" if (k + cc) % 2 else "dye", TRIM))
    gb.cap(pom[0][::-1], {sh: (pc + np.array([0, 0, 0.036 * s]))[None] for sh in gb.shapes}, HEAD_W, ("dye", TRIM))
    gb.cap(pom[-1], {sh: (pc - np.array([0, 0, 0.036 * s]))[None] for sh in gb.shapes}, HEAD_W, ("dye", TRIM))
    return {"hides": ["scalp", "ears"], "long": False, "orient_ref": gb.positions("base")[rings[0]].mean(axis=0),
            "thicken": [(flap_faces, 0.004, ("dye_shade", PRIMARY), ("dye", PRIMARY))]}


# ----------------------------------------------------------------------------
# Hats: hoods (fur-lined hood, draped cowl)
# ----------------------------------------------------------------------------


HOOD_PHIS = (6.0, 17.0, 28.0, 39.0, 50.0, 61.0, 72.0, 83.0, 94.0, 105.0, 116.0, 127.0, 138.0, 149.0)
HOOD_COLS = 25


def _sample_trunk_row(gb: GearBuilder, row: int, thetas: np.ndarray, offset: float):
    """Points on body trunk row `row` at azimuths `thetas` (degrees, 0 =
    front, + = left; the torso grid has a column every 9 degrees), pushed out
    `offset` along the body normal, per shape, with blended weights."""
    ids = gb.trunk_row(row)
    if (ids < 0).any():
        raise ValueError(f"trunk row {row} has dropped vertices (the arm holes); sample a row above them")
    f = (np.asarray(thetas) % 360.0) / 9.0
    c0 = np.floor(f).astype(int) % 40
    c1 = (c0 + 1) % 40
    t = (f - np.floor(f))[:, None]
    pts = {}
    for s in gb.shapes:
        P = gb.P[s][ids] + gb.N[s][ids] * offset
        pts[s] = P[c0] * (1 - t) + P[c1] * t
    ws = [G.smooth(dict(gb.body_md.weights[int(ids[a])]), dict(gb.body_md.weights[int(ids[b])]), float(w))
          for a, b, w in zip(c0, c1, t[:, 0])]
    return pts, ws


def _hood(gb: GearBuilder, L: Landmarks, open_deg, offset_fn, neck_rows, paint):
    """A hood worn up, on its own grid: rows of points round the head at the
    polar angles HOOD_PHIS (`offset_fn(phi, theta)` metres off the sculpt),
    then rows over the neck and shoulders taken from the body
    (`neck_rows` = [(trunk row, offset)], following the build keys). Each
    row spans the azimuths outside the face opening (`open_deg(phi)` either
    side of the front), so the opening's edge is two clean columns; rows with
    no opening close into rings. Returns (rows, faces, opening loop)."""
    s = L.head_scale()
    rows: list[np.ndarray] = []
    opens = []
    for phi in HOOD_PHIS:
        op = open_deg(phi)
        thetas = np.linspace(op, 360.0 - op, HOOD_COLS)
        pts = np.array([L.head_point(phi, th, offset_fn(phi, th) / (0.01 * s)) for th in thetas])
        lo = float(np.clip((phi - 115.0) / 40.0, 0.0, 1.0))
        w = {"DEF-spine.006": 1.0 - 0.6 * lo, "DEF-spine.005": 0.6 * lo} if lo > 0 else dict(HEAD_W)
        rows.append(_static(gb, pts, w))
        opens.append(op)
    for row, off in neck_rows:
        thetas = np.linspace(0.0, 360.0, HOOD_COLS)
        pts, ws = _sample_trunk_row(gb, row, thetas, off)
        rows.append(gb.add(pts, ws))
        opens.append(0.0)
    # Rows with no opening are rings: weld their ends.
    for k, op in enumerate(opens):
        if op <= 0.0:
            rows[k] = rows[k].copy()
            rows[k][-1] = rows[k][0]
    n0 = len(gb.faces)
    gb.connect(rows, paint, closed=False)
    # The crown: a fan to a pole point.
    pole = L.head_point(0.0, 0.0, offset_fn(0.0, 0.0) / (0.01 * s))
    pid = int(_static(gb, pole[None], HEAD_W)[0])
    r0 = rows[0]
    for j in range(HOOD_COLS - 1):
        gb.face((r0[j + 1], r0[j], pid), *paint(-1, j))
    faces = list(range(n0, len(gb.faces)))
    # The opening: down the left edge column, back up the right one.
    open_k = [k for k, op in enumerate(opens) if op > 0.0]
    a, b = open_k[0], open_k[-1]
    loop = [int(rows[a - 1][0])] + [int(rows[k][0]) for k in range(a, b + 1)] + [int(rows[b + 1][0])]
    loop += [int(rows[k][-1]) for k in range(b, a - 1, -1)]
    return rows, faces, loop


def _hood_offset(s: float, base: float, back_room: float, folds: int = 0, tip: float = 0.0):
    """Offset of a hood over the head: `base` over the crown, `back_room` of
    extra volume behind the head (room for buns and plaits under the pressed
    hair cap), more toward the neck, soft folds below the chin and an
    optional point at the back of the crown."""

    def fn(phi, theta):
        back = (1 - math.cos(math.radians(theta))) / 2
        low = float(np.clip((phi - 70) / 80, 0, 1))
        room = back_room * back ** 1.5 * math.exp(-((phi - 118.0) / 30.0) ** 2)
        out = base * s + 0.022 * low + room
        if folds:
            out += 0.01 * (0.5 + 0.5 * math.cos(math.radians(theta) * folds)) * float(np.clip((phi - 120) / 30, 0, 1))
        if tip:
            out += tip * max(0.0, math.cos(math.radians(phi - 32))) ** 10 * back ** 3
        return out

    return fn


@builder("gear/hat/hood_fur_lined_a")
def hat_hood_fur(gb: GearBuilder, L: Landmarks) -> dict:
    """A deep hood worn up, its face opening edged with a thick fur roll,
    closing under the chin into a short capelet over the shoulders, clasped
    with a toggle."""
    s = L.head_scale()

    def open_deg(phi):
        return float(np.interp(phi, [44, 56, 76, 110, 140, 152], [0, 26, 64, 72, 50, 0]))

    offset_fn = _hood_offset(s, 0.03, back_room=0.05)
    nb = L.row_neck_base
    neck_rows = [(L.lay.row_neck + 1, 0.075), (nb, 0.09), (nb + 2, 0.095), (nb + 3, 0.095)]

    def paint(k, c):
        if c in (HOOD_COLS // 2 - 1, HOOD_COLS // 2) and 0 <= k < len(HOOD_PHIS) - 1:
            return "dye_stitch", PRIMARY  # the back seam
        return "dye", PRIMARY

    rows, faces, loop = _hood(gb, L, open_deg, offset_fn, neck_rows, paint)
    P = gb.positions("base")
    toward = P[loop].mean(axis=0) + np.array([0.0, -0.06, 0.0])
    roll(gb, loop, toward, 0.018 * s, lambda k, c: ("dye_soft" if (c + k) % 2 else "dye_light", SECONDARY))
    hem = [int(v) for v in rows[-1][:-1]]
    roll(gb, hem, P[hem].mean(axis=0) + np.array([0, 0, 0.3]), 0.006, lambda k, c: ("dye", TRIM))
    nrm = normals_outward(gb, faces)
    front = int(rows[len(HOOD_PHIS) + 1][0])
    disc(gb, front, 0.012, 0.004, "dye", TRIM, sides=6, normals=nrm, dome=0.003)
    return {"hides": ["scalp", "ears"], "long": False, "orient_ref": L.head_centre(),
            "thicken": [(faces, 0.004, ("dye", SECONDARY), ("dye_shade", PRIMARY))]}


@builder("gear/hat/hood_cowl_a")
def hat_hood_cowl(gb: GearBuilder, L: Landmarks) -> dict:
    """A loose draped hood with a pointed back, falling into soft cowl folds
    round the neck and shoulders."""
    s = L.head_scale()

    def open_deg(phi):
        return float(np.interp(phi, [40, 54, 76, 112, 146, 156], [0, 30, 72, 80, 64, 0]))

    offset_fn = _hood_offset(s, 0.036, back_room=0.055, folds=7, tip=0.04)
    nb = L.row_neck_base
    neck_rows = [(L.lay.row_neck + 1, 0.085), (nb, 0.1), (nb + 2, 0.105), (nb + 3, 0.1)]

    def paint(k, c):
        if k >= len(HOOD_PHIS) - 2:
            fold = math.cos(2 * math.pi * 7 * c / (HOOD_COLS - 1))
            return ("dye_shade" if fold < -0.3 else "dye_light" if fold > 0.7 else "dye"), PRIMARY
        return "dye", PRIMARY

    rows, faces, loop = _hood(gb, L, open_deg, offset_fn, neck_rows, paint)
    # Drape: push the neck rows in and out round the neck in soft folds.
    for k in range(len(HOOD_PHIS), len(rows)):
        ids = rows[k][:-1]
        for sh in gb.shapes:
            Pk = gb.positions(sh)
            q = Pk[ids]
            c = q.mean(axis=0)
            amp = 0.012 * np.cos(2 * np.pi * 7 * np.arange(len(ids)) / len(ids))
            Pk[ids] = _radial(q, c, amp)
            gb.pos[sh] = [Pk]
    P = gb.positions("base")
    roll(gb, loop, P[loop].mean(axis=0) + np.array([0, -0.08, 0]), 0.009 * s, lambda k, c: ("dye", SECONDARY))
    hem = [int(v) for v in rows[-1][:-1]]
    roll(gb, hem, P[hem].mean(axis=0) + np.array([0, 0, 0.3]), 0.005, lambda k, c: ("dye_shade", PRIMARY))
    return {"hides": ["scalp", "ears"], "long": False, "orient_ref": L.head_centre(),
            "thicken": [(faces, 0.004, ("dye", SECONDARY), ("dye_shade", PRIMARY))]}


# ----------------------------------------------------------------------------
# Hats: hair trims (ribbon bow, feathered comb)
# ----------------------------------------------------------------------------


def head_dir_point(L: Landmarks, d_local: np.ndarray, extra_cm: float) -> np.ndarray:
    """`L.head_point` along a head-local direction (X left, F forward, U up)."""
    d = _unit(np.asarray(d_local, dtype=np.float64))
    phi = math.degrees(math.acos(float(np.clip(d[2], -1, 1))))
    theta = math.degrees(math.atan2(d[0], d[1])) % 360.0
    return L.head_point(max(phi, 0.5), theta, extra_cm)


def band_over_head(gb: GearBuilder, L: Landmarks, reach_deg: float, forward: float, width: float, extra_cm: float,
                   n: int, paint) -> list[np.ndarray]:
    """An alice band: a narrow strip over the crown from one side of the head
    to the other (`reach_deg` either side of the top), leaning `forward`
    (head-local F component) and `width` wide (in the same units)."""
    s = L.head_scale()
    edges = []
    for df in (forward + width / 2, forward - width / 2):
        pts = []
        for i in range(n + 1):
            a = math.radians(-reach_deg + 2 * reach_deg * i / n)
            pts.append(head_dir_point(L, np.array([math.sin(a), df, math.cos(a)]), extra_cm * s))
        edges.append(_static(gb, np.array(pts), HEAD_W))
    gb.connect(edges, paint, closed=False)
    return edges


def _loop_shape(gb: GearBuilder, centre: np.ndarray, axis_u: np.ndarray, axis_v: np.ndarray, normal: np.ndarray,
                length: float, width: float, depth: float, mask, sw: str = "dye", segs: int = 8) -> list[int]:
    """A flattened ribbon loop (half of a bow): an elongated tube from the
    knot outward along `axis_u` and back, `width` tall, `depth` thick."""
    n0 = len(gb.faces)
    rings = []
    for i in range(segs + 1):
        a = math.pi * i / segs
        # A teardrop path out and back.
        x = length * math.sin(a) ** 0.7
        y = 0.45 * width * math.cos(a)
        pts = []
        for b in np.linspace(0, 2 * math.pi, 6, endpoint=False):
            pts.append(centre + axis_u * x + axis_v * (y + math.sin(b) * width * 0.5 * (0.4 + 0.6 * math.sin(a)))
                       + normal * math.cos(b) * depth * (0.5 + math.sin(a)))
        rings.append(_static(gb, np.array(pts), HEAD_W))
    gb.connect(rings, lambda k, c: (("dye_shade" if c in (2, 3) else sw), mask))
    return list(range(n0, len(gb.faces)))


@builder("gear/hat/ribbon_bow_a")
def hat_ribbon_bow(gb: GearBuilder, L: Landmarks) -> dict:
    """A silk ribbon worn as a band over the head, tied in a big bow above
    the left ear with two tails."""
    s = L.head_scale()
    band = band_over_head(gb, L, 78.0, 0.2, 0.2, 1.9, 18, lambda k, c: ("dye", PRIMARY))
    # Bow at the left side, on the band two thirds of the way down.
    P = gb.positions("base")
    knot = (P[int(band[0][15])] + P[int(band[1][15])]) / 2
    knot = knot + _unit(knot - L.head_centre()) * 0.006
    hc = L.head_centre()
    normal = _unit(knot - hc)
    u = _unit(np.cross(np.array([0.0, 0.0, 1.0]), normal))  # along the head (front-back)
    v = np.cross(normal, u)
    for sign in (1, -1):
        _loop_shape(gb, knot, u * sign, v, normal, 0.05 * s, 0.032 * s, 0.006 * s, PRIMARY)
    # The knot: a small ring of ribbon.
    ring = np.array([knot + normal * 0.006 + (u * math.cos(a) * 0.011 + v * math.sin(a) * 0.014) * s
                     for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)])
    kr = _static(gb, ring, HEAD_W)
    gb.cap(kr, {sh: (knot + normal * 0.012)[None] for sh in gb.shapes}, HEAD_W, ("dye", SECONDARY))
    # Tails: two tapering strips falling back and down with a notched end.
    tail_faces = []
    for sign, length in ((1, 0.11), (-1, 0.085)):
        a_pts, b_pts = [], []
        for i in range(7):
            t = i / 6
            p = knot + v * (-length * t * s) + u * sign * (0.012 + 0.03 * t) * s + normal * 0.003
            w = (0.011 + 0.004 * t) * s
            a_pts.append(p + u * w)
            b_pts.append(p - u * w)
        b_pts[-1] = b_pts[-1] + v * 0.012 * s * sign
        a = _static(gb, np.array(a_pts), HEAD_W)
        b = _static(gb, np.array(b_pts), HEAD_W)
        n0 = len(gb.faces)
        gb.connect([a, b], lambda k, c: ("dye", PRIMARY), closed=False)
        tail_faces += list(range(n0, len(gb.faces)))
    # A pearl clasp on the knot.
    disc_c = knot + normal * 0.014
    ring = np.array([disc_c + (u * math.cos(a) + v * math.sin(a)) * 0.004 * s for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)])
    pr = _static(gb, ring, HEAD_W)
    gb.cap(pr, {sh: (disc_c + normal * 0.003)[None] for sh in gb.shapes}, HEAD_W, ("dye_light", TRIM))
    return {"hides": [], "long": False, "orient_ref": hc,
            "thicken": [(tail_faces, 0.0015, ("dye_shade", PRIMARY), ("dye", PRIMARY))]}


def _feather(gb: GearBuilder, root: np.ndarray, direction: np.ndarray, normal: np.ndarray, length: float,
             width: float, curl: float, mask) -> list[int]:
    """A flat feather: a vane either side of a quill, curling at the tip."""
    side = _unit(np.cross(normal, direction))
    n0 = len(gb.faces)
    left, mid, right = [], [], []
    for i in range(9):
        t = i / 8
        p = root + direction * length * t + normal * curl * t ** 2
        w = width * math.sin(math.pi * min(1.0, t * 1.1)) ** 0.8 * (1 - 0.3 * t)
        mid.append(p + normal * 0.0015)
        left.append(p + side * w - direction * 0.006 * t)
        right.append(p - side * w * 0.8 - direction * 0.004 * t)
    a = _static(gb, np.array(left), HEAD_W)
    m = _static(gb, np.array(mid), HEAD_W)
    b = _static(gb, np.array(right), HEAD_W)
    gb.connect([a, m], lambda k, c: ("dye_soft" if c % 2 else "dye", mask), closed=False)
    gb.connect([m, b], lambda k, c: ("dye" if c % 2 else "dye_soft", mask), closed=False)
    vane = list(range(n0, len(gb.faces)))
    return vane


@builder("gear/hat/comb_feathered_a")
def hat_comb(gb: GearBuilder, L: Landmarks) -> dict:
    """A curved metal hair comb above the right ear, set with a jewel and a
    spray of three feathers sweeping back."""
    s = L.head_scale()
    hc = L.head_centre()
    # The comb's spine: an arc of points on the head cage.
    spine = [L.head_point(phi, 268.0 + (phi - 62.0) * 0.9, 1.9 * s) for phi in np.linspace(50.0, 76.0, 7)]
    spine = np.array(spine)
    normals = np.array([_unit(p - hc) for p in spine])
    top = spine + normals * 0.007 * s
    lo = spine - np.array([0.0, 0.0, 0.0]) + normals * 0.001
    a = _static(gb, top, HEAD_W)
    b = _static(gb, lo + np.array([0, 0.006 * s, -0.006 * s]), HEAD_W)
    n0 = len(gb.faces)
    gb.connect([a, b], lambda k, c: ("dye", PRIMARY), closed=False)
    comb_faces = list(range(n0, len(gb.faces)))
    # Jewel in the middle.
    centre = spine[3] + normals[3] * 0.012 * s
    n = normals[3]
    r, u = _frame(n)
    ring = np.array([centre + (r * math.cos(t) + u * math.sin(t)) * 0.008 * s for t in np.linspace(0, 2 * math.pi, 8, endpoint=False)])
    jr = _static(gb, ring, HEAD_W)
    gb.cap(jr, {sh: (centre + n * 0.006 * s)[None] for sh in gb.shapes}, HEAD_W, ("dye_light", TRIM))
    setting = np.array([centre - n * 0.002 + (r * math.cos(t) + u * math.sin(t)) * 0.011 * s for t in np.linspace(0, 2 * math.pi, 8, endpoint=False)])
    sr = _static(gb, setting, HEAD_W)
    gb.connect([sr, jr], lambda k, c: ("dye", PRIMARY))
    # Three feathers fanning up and back.
    feathers = []
    for i, (ang, length) in enumerate(((34, 0.15), (16, 0.18), (0, 0.13))):
        # Sweeping back along the head, curling down at the tips.
        d = _unit(np.array([0.0, math.cos(math.radians(ang)), math.sin(math.radians(ang))]) + n * 0.35)
        root = spine[2 + i] + normals[2 + i] * 0.01 * s
        feathers += _feather(gb, root, d, n, length * s, 0.018 * s, 0.02 * s, SECONDARY if i != 1 else TRIM)
    return {"hides": [], "long": False, "orient_ref": hc,
            "thicken": [(comb_faces, 0.002, ("dye_shade", PRIMARY), ("dye", PRIMARY)),
                        (feathers, 0.0012, ("dye_shade", SECONDARY), ("dye", SECONDARY))]}


# ----------------------------------------------------------------------------
# Body: shared parts
# ----------------------------------------------------------------------------


TOP_HIDES = ["torso", "upper_arms", "forearms"]


def _clip01(x):
    return np.clip(x, 0.0, 1.0)


def theta_col(theta_deg: float) -> int:
    """Trunk vertex column nearest an azimuth (0 = front, + = the left side)."""
    return int(round((theta_deg % 360.0) / 9.0)) % 40


def trunk_vid(gb: GearBuilder, shell, row: int, theta_deg: float) -> int | None:
    """The item vertex of a shell over trunk row `row` at an azimuth."""
    return shell.body_to_gear.get(int(gb.trunk_row(row)[theta_col(theta_deg)]))


def lapels(gb: GearBuilder, shell, rows, width_fn, lift: float, mask, sw: str = "dye", normals=None) -> list[int]:
    """Lapels folded back along both edges of a front opening: flaps lying
    on the garment, `width_fn(t)` metres wide from the top (t = 0) of the
    opening to its bottom (t = 1)."""
    left, right = opening_edge(gb, shell, rows)
    normals = normals or _normals(gb, shell.faces)
    faces = []
    for edge, side in ((left, 1), (right, -1)):
        m = len(edge)
        if m < 2:
            continue
        w = np.array([width_fn(i / (m - 1)) for i in range(m)])
        inner, outer = {}, {}
        for s in gb.shapes:
            P = gb.positions(s)[edge]
            N = normals[s][edge]
            lat = np.cross(np.gradient(P, axis=0), N)
            lat /= np.maximum(np.linalg.norm(lat, axis=1, keepdims=True), 1e-9)
            lat *= np.sign(lat[:, 0:1] * side + 1e-12)
            inner[s] = P + N * lift
            outer[s] = P + lat * w[:, None] + N * (lift + 0.003)
        ws = [dict(gb.weights[i]) for i in edge]
        a, b = gb.add(inner, ws), gb.add(outer, ws)
        n0 = len(gb.faces)
        for i in range(m - 1):
            gb.face((a[i], a[i + 1], b[i + 1], b[i]), sw, mask)
        faces += list(range(n0, len(gb.faces)))
    return faces


def open_collar(gb: GearBuilder, shell, row: int, height: float, spread: float, point: float, mask,
                sw: str = "dye") -> list[int]:
    """A turn-down collar round the back of the neck from one side of a front
    opening to the other: it stands `height`, then folds out `spread` and
    down, with longer `point`s at the front."""
    ids = shell.ids(G.open_arc(shell, gb.trunk_row(row)))

    def make(s, r0, k):
        c = r0.mean(axis=0)
        front = np.clip(_front_ness(r0, c), 0.0, 1.0) ** 2
        if k == 1:
            p = _radial(r0, c, 0.004)
            p[:, 2] += height
            return p
        p = _radial(r0, c, 0.006 + spread * (0.6 + 0.4 * front))
        p[:, 2] -= height * 0.2 + point * front
        return p

    n0 = len(gb.faces)
    gb.strip(list(ids), make, 2, lambda k, c: (sw, mask), closed=False)
    return list(range(n0, len(gb.faces)))


def shirt_front(gb: GearBuilder, L: Landmarks, inside, top_row: int, bottom_row: int, offset: float,
                sw=("shirt_white", NONE), collar=(0.028, 0.016, 0.028)):
    """A shirt front with its turn-down collar, seen inside a V opening
    (`inside(r, c)`) from the collar stand down to `bottom_row`."""
    stand = L.lay.row_neck + 2

    def ok(tag, c):
        t = parse_tag(tag)
        return (t[0] == "trunk" and stand <= t[1] < bottom_row and
                (t[1] < L.row_neck_base or inside(t[1], t[2]) or abs(G._face_theta(t[2])) < 25))

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (0, 39) and t[1] > L.row_neck_base + 1:
            return ("shirt_shade" if sw[0] == "shirt_white" else "dye_stitch"), sw[1]
        return sw

    sh = gb.shell(ok, lambda s, ids: np.full(len(ids), offset), paint)
    leaf = G.collar_leaf(gb, sh.ids(gb.trunk_row(stand)), *collar, mask=sw[1], sw=sw[0])
    return sh, leaf


def bow_tie(gb: GearBuilder, vid: int, normals: dict, mask, width: float = 0.045, height: float = 0.02,
            lift: float = 0.012) -> list[int]:
    """A bow tie (or a ribbon bow) on the surface at `vid`: two pinched wings
    and a knot."""
    n0 = len(gb.faces)
    for side in (1, -1):
        ids = anchored(gb, vid, [(0.0, 0.006, 0.0), (side * width, height, 0.004), (side * width * 1.08, -height, 0.004),
                                 (0.0, -0.006, 0.0)], normals, lift)
        gb.face(tuple(ids), "dye", mask)
    k = anchored(gb, vid, [(-0.009, 0.008, 0.005), (0.009, 0.008, 0.005), (0.009, -0.008, 0.005), (-0.009, -0.008, 0.005)],
                 normals, lift)
    gb.face(tuple(k), "dye_shade", mask)
    return list(range(n0, len(gb.faces)))


def shirt_cuffs(gb: GearBuilder, L: Landmarks, sw=("shirt_white", NONE), offset: float = 0.009, length: float = 0.018,
                end_ring: int | None = None) -> None:
    """Shirt cuffs showing past a sleeve that ends at `end_ring` (default the
    ring before the wrist)."""
    end = end_ring if end_ring is not None else L.wrist_ring - 1
    for side in (1, -1):
        def ok(tag, c, side=side):
            t = parse_tag(tag)
            return t[0] == f"arm_{side}" and isinstance(t[1], int) and end - 3 <= t[1] < end

        cs = gb.shell(ok, lambda s, ids: np.full(len(ids), offset), lambda tag, c: sw)
        cuff(gb, cs.ids(gb.ring(f"arm_{side}", end)), side, length, 1.12, sw=sw[0], mask=sw[1],
             inner=("shirt_shade" if sw[0] == "shirt_white" else "dye_shade", sw[1]))


def coat_skirt(gb: GearBuilder, L: Landmarks, shell, hem_z: float, flare: float, n: int, paint=None,
               open_front: float = 0.06, follow: float = 0.75) -> list[int]:
    """An open-fronted coat skirt hanging from the shell's hip row."""
    order = list(range(1, 40)) + [0]
    ring = shell.ids(gb.trunk_row(L.hip_row)[order])
    n0 = len(gb.faces)
    hanging_skirt(gb, L, ring, hem_z, flare, open_front=open_front, n=n, closed=False, paint=paint, follow=follow)
    return list(range(n0, len(gb.faces)))


def flared_sleeves(gb: GearBuilder, L: Landmarks, shell, length: float, flare: float, droop: float, n: int, paint,
                   start_ring: int | None = None) -> list[int]:
    """Bell sleeves from an arm ring. Returns the faces to line (all but the
    first row, whose lining would sit inside the fitted sleeve it continues
    and show through at the elbow)."""
    start = start_ring if start_ring is not None else L.elbow_ring
    faces = []
    for side in (1, -1):
        ring = shell.ids(gb.ring(f"arm_{side}", start))
        n0 = len(gb.faces)
        G.bell_sleeve(gb, ring, length, flare, droop, n, paint, L=L, side=side, start_ring=start)
        faces += list(range(n0 + len(ring), len(gb.faces)))
    return faces


def strip_from_points(gb: GearBuilder, a: dict, b: dict, weights, swatch: str, mask) -> list[int]:
    """Quads between two point rows (per shape dicts of (n, 3) arrays)."""
    ia, ib = gb.add(a, weights), gb.add(b, weights)
    n0 = len(gb.faces)
    for i in range(len(weights) - 1):
        gb.face((ia[i], ia[i + 1], ib[i + 1], ib[i]), swatch, mask)
    return list(range(n0, len(gb.faces)))


def chain(gb: GearBuilder, va: int, vb: int, normals: dict, sag: float, lift: float, swatch: str = "gold",
          mask=NONE, n: int = 10, width: float = 0.004) -> list[int]:
    """A thin hanging chain (or cord) between two item vertices."""
    a_pts, b_pts = {}, {}
    for s in gb.shapes:
        P = gb.positions(s)
        pa, pb = P[va], P[vb]
        nrm = _unit(normals[s][va] + normals[s][vb])
        ts = np.linspace(0, 1, n)
        line = pa[None] + (pb - pa)[None] * ts[:, None] + nrm * lift - np.array([0, 0, 1.0]) * sag * np.sin(np.pi * ts)[:, None]
        a_pts[s] = line + np.array([0, 0, width / 2])
        b_pts[s] = line - np.array([0, 0, width / 2])
    ws = [G.smooth(dict(gb.weights[va]), dict(gb.weights[vb]), float(t)) for t in np.linspace(0, 1, n)]
    return strip_from_points(gb, a_pts, b_pts, ws, swatch, mask)


# ----------------------------------------------------------------------------
# Body: outer layers (hooded cloak, velvet jacket, greatcoat)
# ----------------------------------------------------------------------------


@builder("gear/body/cloak_hooded_wool_a")
def cloak_hooded(gb: GearBuilder, L: Landmarks) -> dict:
    """A long hooded wool cloak: a fitted coat body with sleeves, a cape to
    the elbows, a deep hood lying down the back, wooden toggles and a skirt
    to the shins."""
    b, sc = L.b, L.sc

    def torso_off(p):
        return 0.027 + 0.008 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    def weave(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (0, 39):
            return "dye_shade", PRIMARY
        col = t[2] if len(t) == 3 and isinstance(t[2], int) else 0
        return ("dye_soft" if col % 3 == 0 else "dye"), PRIMARY

    hem = L.hip_row
    stand = L.lay.row_neck + 2
    sh = top_shell(gb, L, stand, hem, L.wrist_ring - 2, torso_off, lambda p: 0.021, weave)
    skirt = coat_skirt(gb, L, sh, 0.2 * sc, 0.13 * sc, 10, paint=lambda k, c: ("dye_soft" if c % 3 == 0 else "dye", PRIMARY),
                       open_front=0.05)
    for side in (1, -1):
        cuff(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 2)), side, 0.03, 1.16, sw="dye", mask=SECONDARY,
             inner=("dye_shade", SECONDARY))
    # The cape: a fresh loop round the collar base, flaring over the
    # shoulders and arms down to the elbows, open at the front.
    base_ids = sh.ids(gb.trunk_row(L.row_neck_base))
    order = list(range(1, 40)) + [0]
    pts = {s: _radial(gb.positions(s)[base_ids], gb.positions(s)[base_ids].mean(axis=0), 0.01)[order] for s in gb.shapes}
    cape0 = gb.add(pts, [dict(gb.weights[base_ids[i]]) for i in order])
    n_c = 7
    drop = 0.31 * sc

    def cape(s, r0, k):
        c = r0.mean(axis=0)
        t = k / n_c
        d = r0 - c
        d[:, 2] = 0.0
        r = np.linalg.norm(d, axis=1)
        u = d / np.maximum(r[:, None], 1e-9)
        side = np.abs(u[:, 0]) ** 1.5
        target = (0.165 + 0.11 * side) * sc
        rr = r + (target - r) * min(1.0, t * 1.7) ** 0.7 + 0.015 * t
        p = c + u * rr[:, None]
        p[:, 2] = r0[:, 2] - drop * t ** 1.1 - 0.02 * t * (1 - side)
        return p

    def cape_weight(k, col, w0):
        x = gb.positions("base")[cape0[col], 0]
        side = "L" if x > 0 else "R"
        t = min(1.0, abs(x) / 0.12) * min(1.0, k / n_c * 1.4)
        return G.smooth({"DEF-spine.003": 1.0}, {f"DEF-shoulder.{side}": 0.4, f"DEF-upper_arm.{side}": 0.6}, t * 0.8)

    n0 = len(gb.faces)
    rings = gb.strip(list(cape0), cape, n_c, lambda k, c: ("dye", TRIM) if k == n_c - 1 else
                     ("dye_soft" if c % 3 == 0 else "dye", PRIMARY), closed=False, weight=cape_weight)
    cape_faces = list(range(n0, len(gb.faces)))
    # The hood lies on the cape down the back: the back columns of the cape
    # rings, pushed out and narrowing to a soft point.
    P = gb.positions("base")
    back = [i for i in range(len(cape0)) if P[cape0[i]][1] > 0.0 and abs(P[cape0[i]][0]) < 0.08]
    hood_rings = []
    n_h = 5
    for k in range(n_h + 1):
        kk = min(k, n_c)
        src = rings[kk][back]
        pts = {}
        for s in gb.shapes:
            Q = gb.positions(s)[src]
            c = gb.positions(s)[rings[kk]].mean(axis=0)
            t = k / n_h
            bulge = 0.016 + 0.06 * math.sin(math.pi * min(1.0, t * 1.1)) ** 0.8
            Q = _radial(Q, c, bulge)
            mid = Q[:, 0].mean()
            Q[:, 0] = mid + (Q[:, 0] - mid) * (1.0 - 0.7 * t ** 1.6)
            Q[:, 2] += 0.006 * (1 - t)
            pts[s] = Q
        hood_rings.append(gb.add(pts, [dict(gb.weights[i]) for i in src]))
    n0 = len(gb.faces)
    gb.connect(hood_rings, lambda k, c: ("dye", TRIM) if k == 0 else ("dye_soft" if c % 3 == 0 else "dye", PRIMARY),
               closed=False)
    hood_faces = list(range(n0, len(gb.faces)))
    # Toggles down the front.
    nrm = _normals(gb, sh.faces)
    for r in (L.row_neck_base + 2, L.row_neck_base + 4, L.row_neck_base + 6):
        v = trunk_vid(gb, sh, r, 0.0)
        if v is not None:
            ids = anchored(gb, v, [(-0.018, 0.005, 0.0), (0.018, 0.005, 0.0), (0.018, -0.005, 0.0), (-0.018, -0.005, 0.0)],
                           nrm, 0.006)
            gb.face(tuple(ids), "dye", TRIM)
    return {"hides": TOP_HIDES, "long": False,
            "thicken": [(skirt, 0.004, ("dye", SECONDARY), ("dye", PRIMARY)),
                        (cape_faces, 0.004, ("dye", SECONDARY), ("dye", PRIMARY)),
                        (hood_faces, 0.006, ("dye", SECONDARY), ("dye", PRIMARY))]}


@builder("gear/body/jacket_tailored_velvet_a")
def jacket_velvet(gb: GearBuilder, L: Landmarks) -> dict:
    """A tailored velvet jacket: satin lapels and collar, a white dress shirt
    and bow tie in the V, two buttons, flap pockets and a pocket square."""
    b, sc = L.b, L.sc
    v_bottom = b.waist_z + 0.03 * sc
    inside = v_neck(L, L.row_neck_base + 1, v_bottom, 30.0)

    def exclude(t, c):
        return t[0] == "trunk" and inside(t[1], t[2])

    def torso_off(p):
        return 0.02 + 0.006 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (19, 20) and t[1] > L.row_neck_base + 4:
            return "dye_stitch", PRIMARY  # centre back seam
        return "dye", PRIMARY

    hem = L.hip_row
    rows = range(L.row_neck_base, hem)
    sh = top_shell(gb, L, L.row_neck_base, hem, L.wrist_ring - 1, torso_off, lambda p: 0.016, paint, exclude=exclude)
    snap_opening(gb, sh, rows, inside.width)
    nrm = _normals(gb, sh.faces)

    def lapel_w(t):
        notch = 0.5 if 0.1 < t < 0.18 else 1.0
        return 0.058 * sc * (1 - t) ** 0.75 * notch + 0.004

    lap = lapels(gb, sh, rows, lapel_w, 0.002, SECONDARY, normals=nrm)
    collar = open_collar(gb, sh, L.row_neck_base, 0.026, 0.032, 0.012, SECONDARY)
    turned_hem(gb, sh.ids(gb.trunk_row(hem)), lambda s, r0: np.array([0.0, r0[:, 1].mean(), 0.0]), 0.075 * sc, 0.012)
    for side in (1, -1):
        cuff(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 1)), side, 0.006, 1.04, sw="dye_shade", mask=PRIMARY)
    shirt_cuffs(gb, L, end_ring=L.wrist_ring - 1, length=0.02)
    # Shirt and bow tie.
    ssh, leaf = shirt_front(gb, L, inside, L.row_neck_base + 1, row_of_z(L, v_bottom) + 2, 0.009)
    stand = L.lay.row_neck + 2
    snrm = _normals(gb, ssh.faces)
    tie = bow_tie(gb, ssh.body_to_gear[int(gb.trunk_row(stand)[0])], snrm, SECONDARY, lift=0.016)
    # Buttons, pockets and a pocket square.
    for z in (v_bottom - 0.02 * sc, v_bottom - 0.085 * sc):
        v = trunk_vid(gb, sh, row_of_z(L, z), 0.0)
        if v is not None:
            disc(gb, v, 0.009, 0.004, "dye", TRIM, normals=nrm, dome=0.002)
    for th in (40.0, -40.0):
        v = trunk_vid(gb, sh, row_of_z(L, b.hip_z + 0.045 * sc), th)
        if v is not None:
            plate(gb, v, 0.12, 0.035, 0.004, "dye_shade", PRIMARY, normals=nrm)
    v = trunk_vid(gb, sh, row_of_z(L, b.chest_z + 0.03 * sc), 42.0)
    if v is not None:
        ids = anchored(gb, v, [(-0.03, 0.0, 0.0), (0.03, 0.0, 0.0), (0.024, 0.026, 0.002), (0.0, 0.034, 0.003),
                               (-0.022, 0.024, 0.002)], nrm, 0.004)
        gb.face(tuple(ids), "dye_light", TRIM)
    return {"hides": TOP_HIDES, "long": False,
            "thicken": [(lap, 0.003, ("dye_shade", SECONDARY), ("dye", SECONDARY)),
                        (collar, 0.003, ("dye_shade", SECONDARY), ("dye", SECONDARY)),
                        (leaf, 0.003, ("shirt_shade", NONE), ("shirt_white", NONE)),
                        (tie, 0.004, ("dye_shade", SECONDARY), ("dye", SECONDARY))]}


@builder("gear/body/greatcoat_double_a")
def greatcoat(gb: GearBuilder, L: Landmarks) -> dict:
    """A double-breasted greatcoat to below the knee: wide lapels and collar,
    two rows of buttons, deep turned-back cuffs, a half belt at the back."""
    b, sc = L.b, L.sc
    v_bottom = b.chest_z + 0.035 * sc
    inside = v_neck(L, L.row_neck_base + 1, v_bottom, 24.0)
    v_row = row_of_z(L, v_bottom)

    def exclude(t, c):
        return t[0] == "trunk" and inside(t[1], t[2])

    def torso_off(p):
        return 0.03 + 0.008 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[1] > v_row and t[2] == 37:
            return "dye_shade", PRIMARY  # edge of the overlapping front
        return "dye", PRIMARY

    hem = L.hip_row
    rows = range(L.row_neck_base, hem)
    sh = top_shell(gb, L, L.row_neck_base, hem, L.wrist_ring - 2, torso_off, lambda p: 0.022, paint, exclude=exclude)
    snap_opening(gb, sh, rows, inside.width)
    nrm = _normals(gb, sh.faces)

    def lapel_w(t):
        notch = 0.55 if 0.12 < t < 0.22 else 1.0
        return 0.075 * sc * (1 - t) ** 0.6 * notch + 0.006

    lap = lapels(gb, sh, rows, lapel_w, 0.002, SECONDARY, normals=nrm)
    collar = open_collar(gb, sh, L.row_neck_base, 0.04, 0.045, 0.02, SECONDARY)
    skirt = coat_skirt(gb, L, sh, b.knee[2] - 0.1 * sc, 0.12 * sc, 10,
                       paint=lambda k, c: ("dye_shade" if c == 36 else "dye", PRIMARY), open_front=0.05)
    for side in (1, -1):
        cuff(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 2)), side, 0.05, 1.2, sw="dye", mask=SECONDARY,
             inner=("dye_shade", SECONDARY))
    under = G.shirt_under_v(gb, L, inside, L.row_neck_base + 1, 0.009, v_row + 1)
    for z in np.linspace(v_bottom - 0.03 * sc, b.waist_z - 0.03 * sc, 3):
        for th in (17.0, -17.0):
            v = trunk_vid(gb, sh, row_of_z(L, z), th)
            if v is not None:
                disc(gb, v, 0.01, 0.004, "dye", TRIM, normals=nrm, dome=0.002)
    # Half belt at the back with two buttons.
    v = trunk_vid(gb, sh, row_of_z(L, b.waist_z - 0.02 * sc), 180.0)
    if v is not None:
        plate(gb, v, 0.22, 0.045, 0.004, "dye_shade", PRIMARY, normals=nrm)
        for x in (-0.085, 0.085):
            ids = anchored(gb, v, [(x + 0.009 * math.cos(a), 0.009 * math.sin(a), 0.0)
                                   for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)], nrm, 0.008)
            gb.face(tuple(ids), "dye", TRIM)
    for th in (42.0, -42.0):
        v = trunk_vid(gb, sh, row_of_z(L, b.hip_z + 0.05 * sc), th)
        if v is not None:
            plate(gb, v, 0.14, 0.04, 0.004, "dye_shade", PRIMARY, normals=nrm)
    return {"hides": TOP_HIDES, "long": False,
            "thicken": [(lap, 0.003, ("dye_shade", SECONDARY), ("dye", SECONDARY)),
                        (collar, 0.004, ("dye_shade", SECONDARY), ("dye", SECONDARY)),
                        (skirt, 0.004, ("dye", SECONDARY), ("dye", PRIMARY)),
                        (under, 0.003, ("shirt_shade", NONE), ("shirt_white", NONE))]}


# ----------------------------------------------------------------------------
# Body: knitted tops (cardigan, turtleneck, Fair Isle jumper)
# ----------------------------------------------------------------------------


def _rib_paint(tag, c, mask=PRIMARY):
    t = parse_tag(tag)
    col = t[2] if len(t) == 3 and isinstance(t[2], int) else 0
    return ("dye_soft" if col % 2 else "dye"), mask


@builder("gear/body/cardigan_chunky_a")
def cardigan(gb: GearBuilder, L: Landmarks) -> dict:
    """A chunky ribbed cardigan, open to the waist over a white tee, with a
    contrast button band, wooden buttons and patch pockets."""
    b, sc = L.b, L.sc
    v_bottom = b.waist_z + 0.01 * sc
    inside = v_neck(L, L.row_neck_base + 1, v_bottom, 25.0)
    v_row = row_of_z(L, v_bottom)

    def exclude(t, c):
        return t[0] == "trunk" and inside(t[1], t[2])

    def torso_off(p):
        return 0.022 + 0.006 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[1] > v_row and t[2] in (0, 39):
            return "dye", SECONDARY  # button band below the V
        return _rib_paint(tag, c)

    hem = L.hip_row
    rows = range(L.row_neck_base, hem)
    sh = top_shell(gb, L, L.row_neck_base, hem, L.wrist_ring - 1, torso_off, lambda p: 0.019, paint, exclude=exclude)
    snap_opening(gb, sh, rows, inside.width)
    nrm = _normals(gb, sh.faces)
    band = G.v_trim(gb, sh, rows, widths=(0.017,), masks=(SECONDARY,), lift=0.003)
    rib_band(gb, sh.ids(gb.trunk_row(hem)), _down_axis, 0.05, 0.003)
    for side in (1, -1):
        rib_band(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 1)), _limb_axis, 0.04, 0.003)
    # A plain tee in the V.
    tee_ok_rows = row_of_z(L, v_bottom) + 2

    def tee_ok(tag, c):
        t = parse_tag(tag)
        return (t[0] == "trunk" and L.row_neck_base <= t[1] < tee_ok_rows and
                (inside(t[1], t[2]) or abs(G._face_theta(t[2])) < 30))

    tee = gb.shell(tee_ok, lambda s, ids: np.full(len(ids), 0.008), lambda tag, c: ("shirt_white", NONE))
    rib_band(gb, tee.ids(G.open_arc(tee, gb.trunk_row(L.row_neck_base))), lambda s, r0: (r0.mean(axis=0), np.array([0, 0, 1.0])),
             0.012, 0.002, ribs=False, mask=NONE)
    # Buttons: on the left band in the V and down the closed front.
    left, _ = opening_edge(gb, sh, rows)
    for v in left[-3::2]:
        disc(gb, int(v), 0.01, 0.007, "dye", TRIM, normals=nrm, dome=0.002)
    for z in (b.waist_z - 0.04 * sc, b.hip_z + 0.02 * sc):
        v = trunk_vid(gb, sh, row_of_z(L, z), 0.0)
        if v is not None:
            disc(gb, v, 0.01, 0.005, "dye", TRIM, normals=nrm, dome=0.002)
    for th in (38.0, -38.0):
        v = trunk_vid(gb, sh, row_of_z(L, b.hip_z + 0.07 * sc), th)
        if v is not None:
            plate(gb, v, 0.11, 0.1, 0.004, "dye_soft", PRIMARY, normals=nrm)
    return {"hides": TOP_HIDES, "long": False,
            "thicken": [(band, 0.003, ("dye_shade", SECONDARY), ("dye", SECONDARY))]}


@builder("gear/body/turtleneck_ribbed_a")
def turtleneck(gb: GearBuilder, L: Landmarks) -> dict:
    """A fine ribbed turtleneck: a close neck tube folded over into a soft
    roll collar, ribbed cuffs and hem."""
    b = L.b
    neck_top = L.lay.row_neck - 1

    def torso_off(p):
        if p[2] > b.neck_base_z + 0.01:
            return 0.009
        return 0.014 + 0.006 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    hem = L.hip_row
    sh = top_shell(gb, L, neck_top, hem, L.wrist_ring - 1, torso_off, lambda p: 0.012, _rib_paint)

    def roll_make(s, r0, k):
        c = r0.mean(axis=0)
        prof = {1: (0.008, 0.002), 2: (0.02, -0.006), 3: (0.025, -0.03), 4: (0.018, -0.046)}[k]
        p = _radial(r0, c, prof[0])
        p[:, 2] += prof[1]
        return p

    n0 = len(gb.faces)
    gb.strip(list(sh.ids(gb.trunk_row(neck_top))), roll_make, 4, lambda k, c: ("dye_soft" if c % 2 else "dye", PRIMARY))
    roll_faces = list(range(n0, len(gb.faces)))
    rib_band(gb, sh.ids(gb.trunk_row(hem)), _down_axis, 0.05, 0.002)
    for side in (1, -1):
        rib_band(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 1)), _limb_axis, 0.026, 0.002)
    return {"hides": TOP_HIDES + ["neck"], "long": False,
            "thicken": [(roll_faces, 0.003, ("dye_shade", PRIMARY), ("dye", PRIMARY))]}


# A Fair Isle motif: rows top to bottom, '#' = trim, '+' = secondary.
FAIR_ISLE = ["++++", "#..#", ".##.", "#..#", "++++", "....", ".#..", "#.#.", ".#..", "....", "++++"]


@builder("gear/body/jumper_fair_isle_a")
def jumper_fair_isle(gb: GearBuilder, L: Landmarks) -> dict:
    """A crew-neck Fair Isle jumper: a patterned yoke and sleeve bands in two
    contrast colours, ribbed collar, cuffs and hem."""
    b = L.b
    top = L.row_neck_base

    def motif(row: int, col: int):
        if 0 <= row < len(FAIR_ISLE):
            ch = FAIR_ISLE[row][col % 4]
            if ch == "#":
                return "dye", TRIM
            if ch == "+":
                return "dye", SECONDARY
        return None

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk":
            m = motif(t[1] - top, t[2])
            if m:
                return m
        elif t[0].startswith("arm") and isinstance(t[1], int):
            m = motif(t[1] - (L.wrist_ring - 9), t[2])
            if m:
                return m
        return "dye", PRIMARY

    def torso_off(p):
        return 0.018 + 0.008 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    hem = L.hip_row
    sh = top_shell(gb, L, top, hem, L.wrist_ring - 1, torso_off, lambda p: 0.015, paint)

    def up_axis(s, r0):
        return r0.mean(axis=0), np.array([0.0, 0.0, 1.0])

    rib_band(gb, sh.ids(gb.trunk_row(top)), up_axis, 0.028, 0.004, mask=TRIM)
    rib_band(gb, sh.ids(gb.trunk_row(hem)), _down_axis, 0.05, 0.002, mask=TRIM)
    for side in (1, -1):
        rib_band(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 1)), _limb_axis, 0.035, 0.003, mask=TRIM)
    return {"hides": TOP_HIDES, "long": False}


# ----------------------------------------------------------------------------
# Body: dress robes (embroidered, velvet formal)
# ----------------------------------------------------------------------------


def _embroidery(k: int, c: int) -> tuple[str, tuple]:
    """Gold-work on a border: staggered bright knots on a darker ground."""
    return ("dye_light" if (c + 2 * (k % 2)) % 4 == 0 else "dye_shade"), TRIM


@builder("gear/body/robe_dress_embroidered_a")
def robe_dress(gb: GearBuilder, L: Landmarks) -> dict:
    """A floor-length dress robe open from the neck, its front edges, hem and
    wide sleeves bordered with gold embroidery, over a high-collared tunic
    with a row of small buttons."""
    b, sc = L.b, L.sc

    def robe_width(r: int) -> float:
        return 14.0 + 26.0 * float(_clip01((b.neck_base_z - _row_z(L, r)) / 0.2))

    def opening(r: int, c: int) -> bool:
        return abs(G._face_theta(c)) < min(robe_width(r), robe_width(r + 1))

    def exclude(t, c):
        return t[0] == "trunk" and opening(t[1], t[2])

    def torso_off(p):
        return 0.03 + 0.008 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk":
            w = min(robe_width(t[1]), robe_width(t[1] + 1))
            if abs(G._face_theta(t[2])) < w + 15:
                return _embroidery(t[1], t[2])
        return "dye", PRIMARY

    hem = L.hip_row
    sh = top_shell(gb, L, L.row_neck_base, hem, L.elbow_ring, torso_off, lambda p: 0.024, paint, exclude=exclude)
    snap_opening(gb, sh, range(L.row_neck_base, hem + 1), robe_width)

    def tunic_ok(tag, c):
        t = parse_tag(tag)
        return t[0] == "trunk" and L.row_neck_base <= t[1] < hem and opening(t[1], t[2])

    tunic = gb.shell(tunic_ok, lambda s, ids: np.full(len(ids), 0.016),
                     lambda tag, c: (("dye_stitch" if parse_tag(tag)[2] in (0, 39) else "dye"), SECONDARY))
    # A mandarin collar round the whole neck.
    neck_ids = gb.trunk_row(L.row_neck_base)
    pts = {s: gb.P[s][neck_ids] + gb.N[s][neck_ids] * 0.022 for s in gb.shapes}
    ring0 = gb.add(pts, [dict(gb.body_md.weights[int(i)]) for i in neck_ids])

    def collar(s, r0, k):
        c = r0.mean(axis=0)
        p = _radial(r0, c, -0.004 if k == 1 else -0.01)
        p[:, 2] += 0.034 if k == 1 else 0.032
        return p

    n0 = len(gb.faces)
    gb.strip(list(ring0), collar, 2, lambda k, c: _embroidery(k, c))
    collar_faces = list(range(n0, len(gb.faces)))
    n_sk = 11
    order = [c for c in range(40) if not opening(hem - 1, c)]
    start = next(c for c in range(40) if not opening(hem - 1, c) and opening(hem - 1, (c - 1) % 40))
    order = [(start + i) % 40 for i in range(40) if (start + i) % 40 in order]
    order.append((order[-1] + 1) % 40)
    ncols = len(order) - 1

    def skirt_paint(k, c):
        if c < 2 or c >= ncols - 2 or k >= n_sk - 2:
            return _embroidery(k, c)
        return "dye", PRIMARY

    n0 = len(gb.faces)
    hang(gb, L, sh.ids(gb.trunk_row(hem)[order]), 0.07 * sc, 0.15 * sc, n_sk, skirt_paint, closed=False, follow=0.75,
         power=0.8, open_front=0.04)
    skirt = list(range(n0, len(gb.faces)))
    sleeves = flared_sleeves(gb, L, sh, 0.24 * sc, 1.55, 0.02, 7,
                             lambda k, c: _embroidery(k, c) if k >= 5 else ("dye", PRIMARY))
    tnrm = _normals(gb, tunic.faces)
    for r in range(L.row_neck_base + 2, row_of_z(L, b.waist_z), 2):
        v = trunk_vid(gb, tunic, r, 0.0)
        if v is not None:
            disc(gb, v, 0.006, 0.003, "dye", TRIM, normals=tnrm, sides=5)
    lining = ("dye", SECONDARY)
    return {"hides": TOP_HIDES, "long": False,
            "thicken": [(skirt, 0.004, lining, ("dye", TRIM)), (sleeves, 0.004, lining, ("dye", TRIM)),
                        (collar_faces, 0.003, ("dye_shade", TRIM), ("dye", TRIM))]}


@builder("gear/body/robe_formal_velvet_a")
def robe_formal(gb: GearBuilder, L: Landmarks) -> dict:
    """A velvet formal robe: a fitted bodice under a wide shawl collar, a
    knotted sash with hanging ends, lined trumpet sleeves
    and a full skirt to the floor."""
    b, sc = L.b, L.sc
    waist = L.waist_row

    def torso_off(p):
        return 0.024 + 0.008 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and waist - 1 <= t[1] <= waist + 1:
            return ("dye_light" if t[1] == waist else "dye"), TRIM  # the sash
        if t[0] == "trunk" and t[2] in (0, 39):
            return "dye_shade", PRIMARY
        return "dye", PRIMARY

    hem = L.hip_row
    sh = top_shell(gb, L, L.row_neck_base, hem, L.elbow_ring, torso_off, lambda p: 0.02, paint)
    # Shawl collar: a deep collar lying over the shoulders (the dome rows of
    # the body pushed out, so it follows their slope), cut to a V at the front.
    def shawl_ok(tag, c):
        t = parse_tag(tag)
        if t[0] != "trunk":
            return False
        th = abs(G._face_theta(t[2]))
        depth = 4 + int(round(5 * max(0.0, 1.0 - th / 50.0)))
        return L.row_neck_base <= t[1] < L.row_neck_base + depth and th > 6

    shawl = gb.shell(shawl_ok, lambda s, ids: np.full(len(ids), 0.04),
                     lambda tag, c: ("dye_light" if parse_tag(tag)[1] == L.row_neck_base + 1 else "dye", SECONDARY))
    shawl_faces = list(shawl.faces)
    for loop in boundary_loops(gb, shawl.faces):
        smooth_loop(gb, loop, 4)
    # Skirt with gored panels.
    n_sk = 11
    n0 = len(gb.faces)
    order = list(range(1, 40)) + [0]
    skirt_ring = sh.ids(gb.trunk_row(hem)[order])
    hang(gb, L, skirt_ring, 0.04 * sc, 0.17 * sc, n_sk, lambda k, c: ("dye_soft" if (c // 5) % 2 else "dye", PRIMARY),
         closed=False, follow=0.75, power=0.8, open_front=0.035)
    skirt = list(range(n0, len(gb.faces)))
    # Sash ends hanging from a knot at the left hip, over the skirt.
    nrm = _normals(gb, sh.faces)
    v = trunk_vid(gb, sh, waist, 32.0)
    tails = []
    if v is not None:
        ids = anchored(gb, v, [(-0.03, 0.03, 0.0), (0.03, 0.03, 0.0), (0.03, -0.03, 0.0), (-0.03, -0.03, 0.0)], nrm, 0.008)
        gb.face(tuple(ids), "dye_shade", TRIM)
        for dx, length in ((-0.012, 0.36), (0.02, 0.3)):
            n = 8
            a_pts, b_pts = {}, {}
            for s in gb.shapes:
                p0 = gb.positions(s)[v]
                n_ = nrm[s][v]
                ts = np.linspace(0, 1, n)
                out = np.array([n_[0], n_[1], 0.0])
                out = out / max(np.linalg.norm(out), 1e-9)
                line = p0[None] + out * (0.01 + 0.17 * sc * ts ** 1.3 * 0.62)[:, None]
                line[:, 2] = p0[2] - length * sc * ts
                side = np.cross(np.array([0, 0, 1.0]), out)
                a_pts[s] = line + side * (dx - 0.022)
                b_pts[s] = line + side * (dx + 0.022)
            ws = [G.smooth(dict(gb.weights[v]), {"DEF-thigh.L": 0.7, "DEF-spine": 0.3}, float(t) * 0.8) for t in np.linspace(0, 1, n)]
            tails += strip_from_points(gb, a_pts, b_pts, ws, "dye", TRIM)
    sleeves = flared_sleeves(gb, L, sh, 0.24 * sc, 1.45, 0.015, 7,
                             lambda k, c: ("dye", SECONDARY) if k >= 6 else ("dye", PRIMARY))
    return {"hides": TOP_HIDES, "long": False,
            "thicken": [(shawl_faces, 0.004, ("dye_shade", SECONDARY), ("dye", SECONDARY)),
                        (skirt, 0.004, ("dye", SECONDARY), ("dye", PRIMARY)),
                        (sleeves, 0.004, ("dye", SECONDARY), ("dye", PRIMARY)),
                        (tails, 0.002, ("dye_shade", TRIM), ("dye", TRIM))]}


# ----------------------------------------------------------------------------
# Body: fine tops (ruffled blouse, brocade waistcoat)
# ----------------------------------------------------------------------------


@builder("gear/body/blouse_ruffled_silk_a")
def blouse_ruffled(gb: GearBuilder, L: Landmarks) -> dict:
    """A silk blouse with softly puffed sleeve heads, a cascading jabot of
    ruffles down the front, ruffled cuffs and a ribbon bow at the collar."""
    b = L.b
    sh_z = b.shoulder_joint[2]

    def torso_off(p):
        return 0.011 + 0.009 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    def arm_off(p):
        return 0.011 + 0.012 * float(_clip01((p[2] - (sh_z - 0.12)) / 0.1))

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (0, 39) and t[1] > L.row_neck_base + 1:
            return "dye_stitch", PRIMARY
        return "dye", PRIMARY

    hem = L.hip_row
    stand = L.lay.row_neck + 2
    sh = top_shell(gb, L, stand, hem, L.wrist_ring - 1, torso_off, arm_off, paint)
    turned_hem(gb, sh.ids(gb.trunk_row(hem)), lambda s, r0: np.array([0.0, r0[:, 1].mean(), 0.0]), 0.03, 0.008)
    leaf = G.collar_leaf(gb, sh.ids(gb.trunk_row(stand)), 0.026, 0.014, 0.02)
    # Jabot: stacked ruffles across the front centre.
    cols = [36, 37, 38, 39, 0, 1, 2, 3, 4]
    ruffles = []
    for i, r in enumerate(range(L.row_neck_base + 1, L.row_neck_base + 9, 2)):
        ids = sh.ids(gb.trunk_row(r)[cols])
        spread = 1.0 + 0.08 * i

        def make(s, r0, k, spread=spread):
            c = r0.mean(axis=0)
            wave = np.array([1.0 if j % 2 else -1.0 for j in range(len(r0))])
            p = r0.copy()
            p[:, 0] = c[0] + (p[:, 0] - c[0]) * (1.0 + (spread - 1.0 + 0.12) * k / 2)
            out = np.array([0.0, -1.0, 0.0])
            p += out * (0.008 * k + wave[:, None] * 0.005 * k)
            p[:, 2] -= 0.022 * k
            return p

        n0 = len(gb.faces)
        gb.strip(list(ids), make, 2, lambda k, c: ("dye_light" if c % 2 else "dye", PRIMARY), closed=False)
        ruffles += list(range(n0, len(gb.faces)))
    # Ruffled cuffs.
    for side in (1, -1):
        ring = sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 3))

        def cmake(s, r0, k):
            c = r0.mean(axis=0)
            ax = G.gb_axis(r0)
            wave = np.array([1.08 if j % 2 else 0.94 for j in range(len(r0))])
            return c + (r0 - c) * (1.0 + 0.3 * k) * wave[:, None] ** k + ax * 0.011 * k

        n0 = len(gb.faces)
        gb.strip(list(ring), cmake, 2, lambda k, c: ("dye_light" if c % 2 else "dye", PRIMARY))
        ruffles += list(range(n0, len(gb.faces)))
    nrm = _normals(gb, sh.faces)
    bow = bow_tie(gb, sh.body_to_gear[int(gb.trunk_row(stand)[0])], nrm, TRIM, width=0.035, height=0.016, lift=0.012)
    return {"hides": TOP_HIDES, "long": False,
            "thicken": [(leaf, 0.003, ("dye_shade", PRIMARY), ("dye", PRIMARY)),
                        (ruffles, 0.002, ("dye_shade", PRIMARY), ("dye", PRIMARY)),
                        (bow, 0.003, ("dye_shade", TRIM), ("dye", TRIM))]}


@builder("gear/body/waistcoat_brocade_a")
def waistcoat_brocade(gb: GearBuilder, L: Landmarks) -> dict:
    """A brocade waistcoat with a pointed hem and gilt buttons over a shirt
    and slim tie, a watch chain looped to the pocket."""
    b, sc = L.b, L.sc
    stand = L.lay.row_neck + 2
    hem = L.hip_row

    def shirt_off(p):
        return 0.009 + 0.006 * _clip01((b.chest_z - p[2]) / (b.chest_z - b.hip_z))

    def shirt_paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (0, 39) and t[1] > L.row_neck_base + 1:
            return "dye_stitch", SECONDARY
        return "dye", SECONDARY

    shirt = top_shell(gb, L, stand, hem, L.wrist_ring - 1, shirt_off, lambda p: 0.009, shirt_paint)
    leaf = G.collar_leaf(gb, shirt.ids(gb.trunk_row(stand)), 0.028, 0.016, 0.028, mask=SECONDARY)
    for side in (1, -1):
        cuff(gb, shirt.ids(gb.ring(f"arm_{side}", L.wrist_ring - 1)), side, 0.016, 1.13, sw="dye", mask=SECONDARY,
             inner=("dye_shade", SECONDARY))
    v_bottom = b.chest_z - 0.07 * sc
    v_row = row_of_z(L, v_bottom)
    tie_faces = G.tie(gb, L, L.row_neck_base, v_row + 1, 0.02, 0.03, 0.05, stripes=(PRIMARY, TRIM))
    inside = v_neck(L, L.row_neck_base + 1, v_bottom, 28.0)
    arm_cut = L.lay.row_dome + 9  # armholes reach this far down

    def ok(tag, c):
        t = parse_tag(tag)
        if t[0] != "trunk" or not (L.row_neck_base + 1 <= t[1] < hem) or inside(t[1], t[2]):
            return False
        th = abs(G._face_theta(t[2]))
        # Leave the shoulder caps and armholes open; narrow straps over the shoulder.
        if t[1] < arm_cut and 48 < th < 132:
            return t[1] < L.row_neck_base + 3 and (th < 62 or th > 118)
        return True

    def brocade(tag, c):
        # A woven diamond lattice, a shade lighter than the ground.
        t = parse_tag(tag)
        d1, d2 = (t[1] + t[2]) % 6, (t[1] - t[2]) % 6
        if d1 == 0 or d2 == 0:
            return "dye_light", PRIMARY
        if d1 == 3 and d2 == 3:
            return "dye_soft", PRIMARY
        return "dye_shade" if (t[1] + t[2]) % 2 and d1 in (2, 4) else "dye", PRIMARY

    def wc_off(s, ids):
        z = gb.P["base"][ids][:, 2]
        return 0.021 + 0.005 * _clip01((b.chest_z - z) / (b.chest_z - b.hip_z))

    wc = gb.shell(ok, wc_off, brocade)
    snap_opening(gb, wc, range(L.row_neck_base, hem), inside.width)

    def points(r0, c):
        th = np.degrees(np.abs(np.arctan2(r0[:, 0], -(r0[:, 1] - c[1]))))
        return 0.05 * sc * np.clip(1 - np.abs(th - 12.0) / 24.0, 0, 1)

    turned_hem(gb, wc.ids(gb.trunk_row(hem)), lambda s, r0: np.array([0.0, r0[:, 1].mean(), 0.0]), 0.02, 0.004,
               slope=points)
    nrm = _normals(gb, wc.faces)
    buttons = []
    for z in np.linspace(v_bottom - 0.01 * sc, b.hip_z + 0.01 * sc, 5):
        v = trunk_vid(gb, wc, row_of_z(L, z), 0.0)
        if v is not None:
            buttons.append(v)
            disc(gb, v, 0.007, 0.003, "dye", TRIM, normals=nrm, dome=0.002)
    for th in (34.0, -34.0):
        v = trunk_vid(gb, wc, row_of_z(L, b.waist_z - 0.02 * sc), th)
        if v is not None:
            plate(gb, v, 0.075, 0.012, 0.003, "dye_shade", PRIMARY, normals=nrm)
    pocket = trunk_vid(gb, wc, row_of_z(L, b.waist_z - 0.02 * sc), 34.0)
    if buttons and pocket is not None:
        chain(gb, buttons[2], pocket, nrm, 0.035, 0.006)
    return {"hides": TOP_HIDES, "long": False,
            "thicken": [(leaf, 0.003, ("dye_shade", SECONDARY), ("dye", SECONDARY)),
                        (tie_faces, 0.003, ("dye_shade", PRIMARY), ("dye", PRIMARY))]}


# ----------------------------------------------------------------------------
# Legs: skirts (A-line, kilt, midi wrap)
# ----------------------------------------------------------------------------


LEG_HIDES = ["hips", "thighs", "calves"]
# The crafted skirts are lined only along their last two rows (the inside
# only shows near the hem), which keeps them inside the legs budget without
# decimating their woven patterns.


def tights(gb: GearBuilder, L: Landmarks, sw=("tights", NONE), top_z: float | None = None) -> None:
    """Opaque tights from the hips (or from `top_z`, when a skirt hides
    everything above it) to the shoe line. The crafted skirts wear dark
    undyed tights, so the skirt's three dyes all go to the skirt."""

    def ok(tag, c):
        t = parse_tag(tag)
        if top_z is not None and c[2] > top_z:
            return False
        if t[0] == "trunk":
            return t[1] >= L.hip_row
        if t[0].startswith("leg") and isinstance(t[1], int):
            return t[1] < L.ankle_ring + 2
        return False

    gb.shell(ok, lambda s, ids: np.full(len(ids), 0.003), lambda tag, c: sw)


def skirt_yoke(gb: GearBuilder, L: Landmarks, paint, band_mask=TRIM):
    """The fitted top of a skirt (waist to hips) and its waistband."""
    top = L.waist_row - 1

    def ok(tag, c):
        t = parse_tag(tag)
        return t[0] == "trunk" and top <= t[1] < L.hip_row

    sh = gb.shell(ok, lambda s, ids: np.full(len(ids), 0.009), paint)
    waistband(gb, L, sh.ids(gb.trunk_row(top)), 0.03, 0.011, mask=band_mask)
    return sh


def hang(gb: GearBuilder, L: Landmarks, ring_ids, hem_z: float, flare: float, n: int, paint, pleat_fn=None,
         pleat: float = 0.0, slit: float = 0.0, closed: bool = True, follow: float = 0.6, power: float = 1.3,
         open_front: float = 0.0) -> list[np.ndarray]:
    """`gear.hanging_skirt` with pleats weighted per column (`pleat_fn(theta)`
    0..1, theta in degrees from the front), a flare profile (`flare * t **
    power`: below 1 a long robe clears the knees sooner), a front split
    (`open_front`) and, for an open skirt, a slit that opens toward the hem
    (`slit` metres)."""
    ring_ids = list(ring_ids)
    base = gb.positions("base")[ring_ids]
    w0 = [gb.weights[i] for i in ring_ids]
    th = np.degrees(np.arctan2(base[:, 0], -base[:, 1]))
    pw = np.array([pleat_fn(t) for t in th]) if pleat_fn else np.ones(len(ring_ids))
    sign = np.array([1.0 if i % 2 else -1.0 for i in range(len(ring_ids))])

    def make(s, r0, k):
        t = k / n
        c = np.array([0.0, r0[:, 1].mean() * 0.5, 0.0])
        z = r0[:, 2] + (hem_z - r0[:, 2]) * t
        p = _radial(r0, c, flare * t ** power + 0.004)
        if pleat:
            p = _radial(p, c, sign * pw * pleat * min(1.0, t * 2.0))
        if open_front:
            front = _front_ness(r0, c)
            p[:, 0] += np.sign(r0[:, 0]) * open_front * t * np.clip(front, 0, 1) ** 4
        if slit and not closed:
            m = 4
            for ends, far in ((range(m), m), (range(len(p) - 1, len(p) - 1 - m, -1), len(p) - 1 - m)):
                for j, i in enumerate(ends):
                    d = p[far] - p[i]
                    d[2] = 0.0
                    p[i] = p[i] + _unit(d) * slit * t ** 2 * (1 - j / m)
        p[:, 2] = z
        return p

    return gb.strip(ring_ids, make, n, paint, closed=closed, weight=G.skirt_weights(gb, w0, base, n, follow))


@builder("gear/legs/skirt_aline_wool_a")
def skirt_aline(gb: GearBuilder, L: Landmarks) -> dict:
    """A plain A-line wool skirt to below the knee: panel seams, a leather
    belt with a brass buckle, worn over opaque tights."""
    b, sc = L.b, L.sc

    def yoke(tag, c):
        return ("dye_stitch" if parse_tag(tag)[2] in (0, 39, 19, 20) else "dye"), PRIMARY

    sh = skirt_yoke(gb, L, yoke)
    n = 8

    def paint(k, c):
        if c in (0, 10, 20, 30):
            return "dye_stitch", PRIMARY
        return ("dye_shade" if k == n - 1 else "dye"), PRIMARY

    n0 = len(gb.faces)
    hang(gb, L, sh.ids(gb.trunk_row(L.hip_row)), b.knee[2] - 0.05 * sc, 0.13 * sc, n, paint)
    skirt = list(range(n0, len(gb.faces)))
    tights(gb, L, top_z=b.knee[2] + 0.06 * sc)
    nrm = _normals(gb, sh.faces)
    v = trunk_vid(gb, sh, L.waist_row - 1, 0.0)
    if v is not None:
        plate(gb, v, 0.04, 0.032, 0.016, "gold", NONE, normals=nrm, hole=0.55)
    return {"hides": LEG_HIDES, "long": False,
            "thicken": [(skirt[-80:], 0.004, ("dye_shade", PRIMARY), ("dye", PRIMARY))]}


def tartan(k: int, c: int) -> tuple[str, tuple]:
    """A simple sett at the mesh's resolution: broad secondary bands every
    four rows and eight columns (darker where they cross), a thin trim
    stripe down the middle of each block, on the primary ground."""
    cx, ky = c % 8, k % 4
    vb, hb = cx in (0, 1), ky == 0
    if vb and hb:
        return "dye_shade", SECONDARY
    if vb or hb:
        return "dye", SECONDARY
    if cx == 4:
        return "dye", TRIM
    return ("dye_soft" if ky == 2 else "dye"), PRIMARY


@builder("gear/legs/kilt_tartan_a")
def kilt(gb: GearBuilder, L: Landmarks) -> dict:
    """A knee-length tartan kilt: a flat apron at the front with a fringed
    edge and a kilt pin, knife pleats round the back, buckled straps."""
    b, sc = L.b, L.sc

    def yoke(tag, c):
        t = parse_tag(tag)
        return tartan(t[1], t[2])

    sh = skirt_yoke(gb, L, yoke, band_mask=PRIMARY)
    n = 9
    back_rows = L.hip_row - (L.waist_row - 1)

    def paint(k, c):
        if c == 35:
            return "dye_stitch", TRIM  # fringed apron edge
        sw, m = tartan(k + back_rows, c)
        if 12 < c < 28 and c % 2 and sw == "dye":
            return "dye_shade", m  # pleat shadows round the back
        return sw, m

    rings = hang(gb, L, sh.ids(gb.trunk_row(L.hip_row)), b.knee[2] + 0.02 * sc, 0.075 * sc, n, paint,
                 pleat_fn=lambda th: float(np.clip((abs(th) - 65.0) / 40.0, 0.0, 1.0)), pleat=0.008)
    skirt_faces = list(range(len(gb.faces) - 40 * n, len(gb.faces)))
    tights(gb, L, top_z=b.knee[2] + 0.12 * sc)
    nrm = _normals(gb, sh.faces)
    snrm = _normals(gb, skirt_faces)
    plate(gb, int(rings[n - 2][35]), 0.012, 0.07, 0.008, "gold", NONE, normals=snrm)
    for th in (-75.0, -95.0):
        v = trunk_vid(gb, sh, L.waist_row, th)
        if v is not None:
            plate(gb, v, 0.022, 0.03, 0.004, "iron", NONE, normals=nrm, hole=0.5)
    return {"hides": LEG_HIDES, "long": False,
            "thicken": [(skirt_faces[-80:], 0.004, ("dye_shade", PRIMARY), ("dye", PRIMARY))]}


@builder("gear/legs/skirt_midi_wrap_a")
def skirt_wrap(gb: GearBuilder, L: Landmarks) -> dict:
    """A mid-calf wrap skirt: the wrapped edge falls at the front left and
    opens into a slit toward the hem, tied at the waist with a bow."""
    b, sc = L.b, L.sc
    sh = skirt_yoke(gb, L, lambda tag, c: ("dye", PRIMARY), band_mask=PRIMARY)
    order = [(3 + i) % 40 for i in range(40)]
    ring = sh.ids(gb.trunk_row(L.hip_row)[order])
    n = 10

    def paint(k, c):
        if c in (0, 38):
            return "dye_shade", PRIMARY
        return ("dye_soft" if c % 5 == 0 else "dye"), PRIMARY

    n0 = len(gb.faces)
    hang(gb, L, ring, b.knee[2] - 0.2 * sc, 0.11 * sc, n, paint, slit=0.045, closed=False, follow=0.7)
    skirt = list(range(n0, len(gb.faces)))
    tights(gb, L, top_z=b.knee[2] + 0.04 * sc)
    nrm = _normals(gb, sh.faces)
    v = trunk_vid(gb, sh, L.waist_row - 1, 27.0)
    bow = []
    if v is not None:
        bow = bow_tie(gb, v, nrm, TRIM, width=0.032, height=0.015, lift=0.016)
        for dx, length in ((-0.006, 0.16), (0.012, 0.13)):
            ids = anchored(gb, v, [(dx - 0.008, 0.0, 0.0), (dx + 0.008, 0.0, 0.0), (dx + 0.016, -length, 0.012),
                                   (dx, -length - 0.01, 0.012)], nrm, 0.018)
            gb.face(tuple(ids), "dye", TRIM)
            bow.append(len(gb.faces) - 1)
    return {"hides": LEG_HIDES, "long": False,
            "thicken": [(skirt[-78:], 0.004, ("dye_shade", PRIMARY), ("dye", PRIMARY)),
                        (bow, 0.002, ("dye_shade", TRIM), ("dye", TRIM))]}


# ----------------------------------------------------------------------------
# Legs: tailored trousers (wide-leg, tapered)
# ----------------------------------------------------------------------------


@builder("gear/legs/trousers_wide_leg_a")
def trousers_wide(gb: GearBuilder, L: Landmarks) -> dict:
    """High-waisted wide-leg trousers: front pleats, pressed creases, legs
    falling straight and wide to the floor, two buttons on the waistband."""
    b, sc = L.b, L.sc

    def offset(s, ids):
        P = gb.P["base"][ids]
        N = gb.N["base"][ids]
        z = P[:, 2]
        base = 0.008 + 0.005 * _clip01((b.waist_z - z) / 0.25)
        widen = 0.03 * _clip01((b.crotch_z - z) / (b.crotch_z - b.knee[2]))
        outward = _clip01(N[:, 0] * np.sign(P[:, 0] + 1e-9) + 0.6)
        return base + widen * outward

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (3, 36) and c[2] > b.crotch_z:
            return "dye_shade", PRIMARY  # front pleats
        if t[0] == "trunk" and t[2] in (0, 39) and c[2] < b.waist_z - 0.02:
            return "dye_stitch", PRIMARY  # fly
        if t[0].startswith("leg") and t[2] == 0:
            return "dye_light", PRIMARY
        return "dye", PRIMARY

    top = L.waist_row - 2
    shell = legs_shell(gb, L, top, L.knee_ring, offset, paint)
    waistband(gb, L, shell.ids(gb.trunk_row(top)), 0.04, 0.012)
    n = 10
    for side in (1, -1):
        knee_ids = shell.ids(gb.ring(f"leg_{side}", L.knee_ring))

        def make(s, r0, k, side=side):
            t = k / n
            z0 = float(r0[:, 2].mean())
            # The hem breaks over the shoe: low at the heel, higher over the instep.
            front = np.clip(_front_ness(r0, L.leg_axis(side, z0)), 0.0, 1.0) ** 1.5
            hem_z = (0.026 + 0.05 * front) * sc
            z = r0[:, 2] + (hem_z - r0[:, 2]) * t
            zc = float(z.mean())
            c0 = L.leg_axis(side, z0)
            c = L.leg_axis(side, zc)
            c[0] += side * 0.01 * sc * t
            v = r0 - c0
            v[:, 2] = 0.0
            # The knee ring blends into a round, wide leg falling straight down.
            u = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
            target = (0.082 + 0.016 * t) * sc
            radius = max(target, L.body_leg_radius(s, side, zc) + 0.028)
            blend = min(1.0, t * 2.5)
            p = c + v * (1 - blend) + u * radius * blend
            p[:, 2] = z
            return p

        def weight(k, col, w0, side=side):
            t = k / n
            z = L.leg_z[L.knee_ring] + (0.06 - L.leg_z[L.knee_ring]) * t
            kk = min(L.leg_z, key=lambda q: abs(L.leg_z[q] - z))
            return gb.body_md.weights[gb.ring(f"leg_{side}", kk)[col]]

        rings = gb.strip(knee_ids, make, n, lambda k, c: ("dye_light" if c == 0 else "dye", PRIMARY), weight=weight)
        G.hem_fold(gb, L, rings[-1], side)
    nrm = _normals(gb, shell.faces)
    for th in (12.0, -12.0):
        v = trunk_vid(gb, shell, top, th)
        if v is not None:
            disc(gb, v, 0.009, 0.02, "dye", TRIM, normals=nrm, dome=0.002)
    return {"hides": LEG_HIDES, "long": False}


@builder("gear/legs/trousers_tapered_wool_a")
def trousers_tapered(gb: GearBuilder, L: Landmarks) -> dict:
    """Pleated wool trousers tapering to a cropped ankle with turn-ups."""
    b, sc = L.b, L.sc

    def offset(s, ids):
        z = gb.P["base"][ids][:, 2]
        return 0.009 + 0.006 * _clip01((b.waist_z - z) / 0.25)

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (4, 6, 33, 35) and c[2] > b.crotch_z + 0.03:
            return "dye_shade", PRIMARY  # double pleats
        if t[0] == "trunk" and t[2] in (0, 39) and c[2] < b.waist_z - 0.02:
            return "dye_stitch", PRIMARY
        if t[0].startswith("leg") and t[2] == 0:
            return "dye_light", PRIMARY
        return "dye", PRIMARY

    top = L.waist_row - 1
    shell = legs_shell(gb, L, top, L.knee_ring, offset, paint)
    waistband(gb, L, shell.ids(gb.trunk_row(top)), 0.032, 0.011, mask=SECONDARY)
    n = 9
    legs = trouser_legs(gb, L, shell, n, 0.07 * sc, 0.085 * sc, 0.013,
                        lambda k, c: (("dye_light" if c == 0 else "dye"), PRIMARY), 0.05 * sc)
    for side, rings in legs.items():
        def make(s, r0, k, side=side):
            c = L.leg_axis(side, float(r0[:, 2].mean()))
            p = _radial(r0, c, 0.006 if k == 1 else -0.004)
            p[:, 2] += 0.032 if k == 1 else 0.028
            return p

        gb.strip(list(rings[-1]), make, 2, lambda k, c: ("dye", PRIMARY) if k == 0 else ("dye_shade", PRIMARY))
    return {"hides": LEG_HIDES, "long": False}


# ----------------------------------------------------------------------------
# Shoes: boots (tall riding, buckled ankle, dragonhide)
# ----------------------------------------------------------------------------


def nearest_vid(gb: GearBuilder, shell, target) -> int:
    ids = np.array(sorted(shell.body_to_gear.values()))
    P = gb.positions("base")[ids]
    return int(ids[int(np.argmin(np.linalg.norm(P - np.asarray(target), axis=1)))])


def _foot(L: Landmarks, side: int, name: str) -> np.ndarray:
    p = np.array(K.foot_points(L.b)[name], dtype=np.float64)
    if side < 0:
        p[0] = -p[0]
    return p


@builder("gear/shoes/boots_riding_tall_a")
def boots_riding(gb: GearBuilder, L: Landmarks) -> dict:
    """Knee-high riding boots: a slim shaft with a back seam, a contrast top
    band and a stacked heel."""
    sc = L.sc
    top = G._ring_at_z(L, L.b.knee[2] + 0.035 * sc)

    def offset(s, ids):
        p = gb.P["base"][ids]
        return G._shoe_offsets(gb, L, 0.008, 0.009)(s, ids) + 0.003 * _clip01((p[:, 2] - 0.15) / 0.2)

    def paint(tag, c):
        t = parse_tag(tag)
        if c[2] < 0.014 * sc:
            return "sole_edge", NONE
        if isinstance(t[1], int) and t[1] <= top + 1:
            return "dye", TRIM
        side = 1 if c[0] > 0 else -1
        ax = L.leg_axis(side, float(c[2]))
        if c[2] > 0.12 and c[1] - ax[1] > 0 and abs(c[0] - ax[0]) < 0.009:
            return "dye_stitch", PRIMARY
        return "dye", PRIMARY

    sh = shoe_shell(gb, L, top, offset, paint)
    for side in (1, -1):
        shoe_collar(gb, L, sh.ids(gb.ring(f"leg_{side}", top)), side, mask=TRIM)
        sole_slab(gb, L, side, 0.02 * sc, 0.012, heel=0.03)
    return {"hides": ["feet", "calves"], "long": False}


@builder("gear/shoes/boots_ankle_buckle_a")
def boots_ankle(gb: GearBuilder, L: Landmarks) -> dict:
    """Ankle boots with a buckled strap round the ankle and a pull tab."""
    sc = L.sc
    top = G._ring_at_z(L, 0.15 * sc)

    def paint(tag, c):
        if c[2] < 0.012 * sc:
            return "sole_edge", NONE
        side = 1 if c[0] > 0 else -1
        ankle = _foot(L, side, "ankle")
        if 0.085 * sc < c[2] < 0.112 * sc and -(c[1] - ankle[1]) < 0.035:
            return "dye_shade", SECONDARY  # the strap
        return "dye", PRIMARY

    sh = shoe_shell(gb, L, top, G._shoe_offsets(gb, L, 0.007, 0.009), paint)
    nrm = _normals(gb, sh.faces)
    for side in (1, -1):
        shoe_collar(gb, L, sh.ids(gb.ring(f"leg_{side}", top)), side)
        sole_slab(gb, L, side, 0.02 * sc, 0.011, heel=0.024)
        ankle = _foot(L, side, "ankle")
        v = nearest_vid(gb, sh, ankle + np.array([side * 0.05, -0.01, 0.1 * sc - ankle[2]]))
        plate(gb, v, 0.024, 0.022, 0.004, "dye", TRIM, normals=nrm, hole=0.5)
        v = nearest_vid(gb, sh, ankle + np.array([0.0, 0.06, 0.14 * sc - ankle[2]]))
        plate(gb, v, 0.018, 0.03, 0.003, "dye_shade", PRIMARY, normals=nrm)
    return {"hides": ["feet"], "long": False}


@builder("gear/shoes/boots_dragonhide_a")
def boots_dragonhide(gb: GearBuilder, L: Landmarks) -> dict:
    """Mid-calf dragonhide boots: overlapping scales, a turned-down cuff, a
    thick sole and a row of gilt studs up the outer side."""
    sc = L.sc
    top = G._ring_at_z(L, 0.26 * sc)

    def offset(s, ids):
        p = gb.P["base"][ids]
        bump = 0.0012 * (np.sin(p[:, 2] * 240.0) > 0.3)
        return G._shoe_offsets(gb, L, 0.009, 0.01)(s, ids) + bump

    def paint(tag, c):
        t = parse_tag(tag)
        if c[2] < 0.018 * sc:
            return "sole_edge", NONE
        k = t[1] if isinstance(t[1], int) else 0
        col = t[2]
        # Small overlapping scales: staggered rows, each scale lit along its top edge.
        stagger = (col + (k % 2)) % 2
        return ("dye_light" if stagger and k % 2 else "dye_soft" if stagger else "dye_shade" if k % 2 else "dye"), PRIMARY

    sh = shoe_shell(gb, L, top, offset, paint)
    nrm = _normals(gb, sh.faces)
    for side in (1, -1):
        def cuff_make(s, r0, k, side=side):
            c = L.leg_axis(side, float(r0[:, 2].mean()))
            p = _radial(r0, c, {1: 0.006, 2: 0.014, 3: 0.014}[k])
            p[:, 2] += {1: 0.006, 2: 0.0, 3: -0.04}[k]
            return p

        gb.strip(list(sh.ids(gb.ring(f"leg_{side}", top))), cuff_make, 3,
                 lambda k, c: ("dye_shade" if k == 0 else "dye", SECONDARY))
        sole_slab(gb, L, side, 0.026 * sc, 0.013, heel=0.02)
        ankle = _foot(L, side, "ankle")
        for z in np.linspace(0.07, 0.22, 5) * sc:
            v = nearest_vid(gb, sh, np.array([ankle[0] + side * 0.06, ankle[1] + 0.005, z]))
            disc(gb, v, 0.005, 0.003, "dye", TRIM, normals=nrm, sides=5, dome=0.002)
    return {"hides": ["feet"], "long": False}


# ----------------------------------------------------------------------------
# Shoes: fine shoes (Mary Janes, wingtip brogues)
# ----------------------------------------------------------------------------


@builder("gear/shoes/mary_janes_a")
def mary_janes(gb: GearBuilder, L: Landmarks) -> dict:
    """Round-toed Mary Janes with a buckled strap over the instep."""
    sc = L.sc
    top = L.foot_first + 2

    def paint(tag, c):
        if c[2] < 0.012 * sc:
            return "sole_edge", NONE
        return "dye", PRIMARY

    sh = shoe_shell(gb, L, top, G._shoe_offsets(gb, L, 0.006, 0.005), paint)
    nrm = _normals(gb, sh.faces)
    straps = []
    for side in (1, -1):
        shoe_collar(gb, L, sh.ids(gb.ring(f"leg_{side}", top)), side, mask=SECONDARY)
        sole_slab(gb, L, side, 0.013 * sc, 0.01, heel=0.018)
        ring = sh.ids(gb.ring(f"leg_{side}", top + 1))
        P = gb.positions("base")[ring]
        upper = [ring[i] for i in np.argsort(P[:, 0]) if P[i, 2] > np.median(P[:, 2])]
        ax = G.gb_axis(P)
        a_pts, b_pts = {}, {}
        for s in gb.shapes:
            Q = gb.positions(s)[upper]
            Nn = nrm[s][upper]
            a_pts[s] = Q + Nn * 0.003 - ax * 0.007
            b_pts[s] = Q + Nn * 0.003 + ax * 0.007
        straps += strip_from_points(gb, a_pts, b_pts, [dict(gb.weights[i]) for i in upper], "dye_deep", SECONDARY)
        outer = upper[-1] if side > 0 else upper[0]
        plate(gb, int(outer), 0.014, 0.014, 0.006, "dye", TRIM, normals=nrm, hole=0.45)
    return {"hides": ["feet"], "long": False,
            "thicken": [(straps, 0.002, ("dye_shade", SECONDARY), ("dye", SECONDARY))]}


@builder("gear/shoes/brogues_wingtip_a")
def brogues(gb: GearBuilder, L: Landmarks) -> dict:
    """Wingtip brogues: a W-shaped toe cap and heel counter in a contrast
    leather with punched edges, laced over the instep."""
    sc = L.sc
    fp = K.foot_points(L.b)
    ball_fwd = -fp["ball"][1]
    ankle_fwd = -fp["ankle"][1]
    heel_fwd = -fp["heel_back"][1]

    def paint(tag, c):
        if c[2] < 0.012 * sc:
            return "sole_edge", NONE
        x = abs(c[0]) - abs(fp["ankle"][0])
        fwd = -c[1]
        if 0.03 < fwd - ankle_fwd < 0.10 and abs(x) < 0.016 and c[2] > 0.045 * sc:
            return "lace", NONE
        point = ball_fwd - 0.02 + 0.03 * max(0.0, 1.0 - abs(x) / 0.026)
        wing = c[2] < 0.034 * sc and fwd > ankle_fwd - 0.005
        cap = fwd > point or wing
        edge = abs(fwd - point) < 0.006 or (abs(c[2] - 0.034 * sc) < 0.004 and fwd > ankle_fwd)
        heel = fwd < heel_fwd + 0.055 and c[2] < 0.065 * sc
        if edge and (cap or fwd > ankle_fwd):
            return "dye_stitch", SECONDARY
        if cap or heel:
            return "dye", SECONDARY
        return "dye", PRIMARY

    sh = shoe_shell(gb, L, L.ankle_ring + 1, G._shoe_offsets(gb, L, 0.006, 0.008), paint)
    for side in (1, -1):
        shoe_collar(gb, L, sh.ids(gb.ring(f"leg_{side}", L.ankle_ring + 1)), side, mask=SECONDARY)
        sole_slab(gb, L, side, 0.015 * sc, 0.012, heel=0.014)
    return {"hides": ["feet"], "long": False}
