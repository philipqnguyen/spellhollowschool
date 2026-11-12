"""Every clothing item of data/items/catalog.json, cut against the base bodies.

Each builder takes a `GearBuilder` (body + build shapes) and its `Landmarks`
and returns the item's metadata (`hides`, `long`). Offsets follow the layer
cages of docs/art/base-body-spec.md section 4: inner items (tights, shoes,
trousers, fitted shirts) sit 3-12 mm off the skin, outer items (jumpers,
robes, coats) 18-35 mm, and wherever two layers can overlap the outer one is
built further out (shirt hems over waistbands, trouser hems over shoe
collars). Hidden body regions are not drawn under the item, so only borders
need to clear the skin.
"""

from __future__ import annotations

import math

import numpy as np

from . import pipeline as PL
from . import rig as RG
from . import skeleton as K
from .gear_ctx import Landmarks, parse_tag
from .gear_lib import NONE, PRIMARY, SECONDARY, TRIM, GearBuilder

BUILDERS = {}
# LOD0 triangle budgets (docs/art/base-body-spec.md section 6); robes and the
# long cloak may use up to 8,000.
BUDGETS = {"hat": 2000, "body": 6000, "legs": 3000, "shoes": 1500}
LONG_BODY = {f"gear/body/robe_school_house_{h}" for h in "abcd"} | {"gear/body/cloak_travelling_a"}


def builder(asset: str):
    def deco(fn):
        BUILDERS[asset] = fn
        return fn

    return deco


def _dominant(w: dict) -> str:
    return max(w.items(), key=lambda kv: kv[1])[0] if w else ""


def _radial(points: np.ndarray, centre: np.ndarray, amount: float) -> np.ndarray:
    """Push points horizontally away from a vertical axis through centre."""
    d = points - centre
    d[:, 2] = 0.0
    n = np.linalg.norm(d, axis=1, keepdims=True)
    amount = np.asarray(amount, dtype=np.float64)
    if amount.ndim == 1:
        amount = amount[:, None]
    return points + d / np.maximum(n, 1e-9) * amount


def _scale_about(points: np.ndarray, centre: np.ndarray, s: float) -> np.ndarray:
    out = points.copy()
    out[:, :2] = centre[:2] + (points[:, :2] - centre[:2]) * s
    return out


def _front_ness(points: np.ndarray, centre: np.ndarray) -> np.ndarray:
    """1 at the front (-Y) of a ring, -1 at the back."""
    d = points[:, :2] - centre[:2]
    return -d[:, 1] / np.maximum(np.linalg.norm(d, axis=1), 1e-9)



# ----------------------------------------------------------------------------
# Shared garment parts
# ----------------------------------------------------------------------------


def waistband(gb: GearBuilder, L: Landmarks, ring_ids, height: float, out: float, mask=PRIMARY, sw: str = "dye"):
    """A band rising from a trunk-row loop, folded in at the top edge."""
    axis = np.array([0.0, 0.0, 0.0])

    def make(s, r0, k):
        c = r0.mean(axis=0)
        c = np.array([axis[0], c[1], c[2]])
        if k == 1:
            p = _radial(r0, c, out)
            p[:, 2] += height
            return p
        p = _radial(r0, c, out - 0.009)
        p[:, 2] += height - 0.004
        return p

    return gb.strip(list(ring_ids), make, 2, lambda k, c: (sw, mask) if k == 0 else ("dye_shade", mask))


def turned_hem(gb: GearBuilder, ring_ids, centre_fn, drop: float, out: float, turn: float = 0.012, sw: str = "dye",
               mask=PRIMARY, slope=None):
    """Extend a loop down by `drop` (pushed out by `out`), then fold the edge
    in and up by `turn` so the hem shows thickness."""

    def make(s, r0, k):
        c = centre_fn(s, r0)
        p = _radial(r0, c, out)
        dz = drop if slope is None else drop + slope(r0, c)
        p[:, 2] -= dz
        if k == 2:
            p = _radial(p, c, -0.008)
            p[:, 2] += turn
        return p

    return gb.strip(list(ring_ids), make, 2, lambda k, c: (sw, mask) if k == 0 else ("dye_shade", mask))


def collar_leaf(gb: GearBuilder, ring_ids, drop: float, spread: float, points: float, mask=PRIMARY, sw: str = "dye") -> list[int]:
    """Folded shirt collar: from the top of the stand (`ring_ids`, a loop
    round the neck) the leaf turns over and falls `drop` metres, spreading
    `spread` outward, with longer points at the front. Returns the leaf faces
    (to be thickened)."""

    def make(s, r0, k):
        c = r0.mean(axis=0)
        front = np.clip(_front_ness(r0, c), 0.0, 1.0) ** 3
        p = r0.copy()
        if k == 1:
            p = _radial(p, c, 0.007)
            p[:, 2] += 0.004
        elif k == 2:
            p = _radial(p, c, 0.004 + spread * 0.5)
            p[:, 2] -= drop * 0.45
        else:
            p = _radial(p, c, 0.004 + spread + 0.012 * front)
            p[:, 2] -= drop + points * front
        return p

    n0 = len(gb.faces)
    # The two faces either side of the front centre stay open for the tie knot.
    gb.strip(list(ring_ids), make, 3, lambda k, c: (sw, mask), skip=lambda k, c: c in (len(ring_ids) - 1, 0))
    return list(range(n0, len(gb.faces)))


# ----------------------------------------------------------------------------
# Legs
# ----------------------------------------------------------------------------


def legs_shell(gb: GearBuilder, L: Landmarks, top_row: int, bottom_ring: int, offset, paint):
    def ok(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk":
            return t[1] >= top_row
        if t[0].startswith("leg") and isinstance(t[1], int):
            return t[1] < bottom_ring
        return False

    return gb.shell(ok, offset, paint)


def trouser_legs(gb: GearBuilder, L: Landmarks, shell, n: int, hem_back: float, hem_front: float, ease: float, paint,
                 min_radius: float):
    """Straight-ish lower legs from the knee ring down to a sloped hem."""
    rings_out = {}
    for side in (1, -1):
        knee_ids = shell.ids(gb.ring(f"leg_{side}", L.knee_ring))

        def make(s, r0, k, side=side):
            t = k / n
            z0 = float(r0[:, 2].mean())
            c0 = L.leg_axis(side, z0)
            front = _front_ness(r0, c0)
            hem = hem_back + (hem_front - hem_back) * np.clip(front * 0.5 + 0.5, 0, 1) ** 1.5
            z = z0 + (hem - z0) * t
            zc = float(z.mean())
            c = L.leg_axis(side, zc)
            v = r0 - c0
            v[:, 2] = 0
            rmax = float(np.max(np.linalg.norm(v[:, :2], axis=1)))
            need = max(L.body_leg_radius(s, side, zc) + ease, min_radius)
            scale = max(1.0 - 0.12 * t, need / rmax)
            p = c + v * scale
            p[:, 2] = z
            return p

        def weight(k, c, w0, side=side):
            # Copy the body leg ring nearest each new ring's height.
            t = k / n
            z = L.leg_z[L.knee_ring] + (0.06 - L.leg_z[L.knee_ring]) * t
            kk = min(L.leg_z, key=lambda q: abs(L.leg_z[q] - z))
            bid = gb.ring(f"leg_{side}", kk)[c]
            return gb.body_md.weights[bid]

        rings = gb.strip(knee_ids, make, n, paint, weight=weight)
        rings_out[side] = rings
    return rings_out


def hem_fold(gb: GearBuilder, L: Landmarks, ring_ids, side: int, sw="dye_shade", mask=PRIMARY, up: float = 0.014):
    def make(s, r0, k):
        c = L.leg_axis(side, float(r0[:, 2].mean()))
        p = _radial(r0, c, -0.007)
        p[:, 2] += up
        return p

    return gb.strip(list(ring_ids), make, 1, lambda k, c: (sw, mask))


@builder("gear/legs/trousers_school_a")
def trousers_school(gb: GearBuilder, L: Landmarks, corduroy: bool = False) -> dict:
    b = L.b

    def offset(s, ids):
        z = gb.P["base"][ids][:, 2]
        return 0.008 + 0.005 * np.clip((b.waist_z - z) / 0.25, 0, 1)

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (0, 39) and c[2] < b.waist_z - 0.02:
            return "dye_stitch", PRIMARY  # fly seam
        if corduroy and t[0].startswith("leg") and t[2] % 2:
            return "dye_soft", PRIMARY
        if not corduroy and t[0].startswith("leg") and t[2] == 0:
            return "dye_light", PRIMARY  # pressed front crease
        return "dye", PRIMARY

    top = L.waist_row - 1
    shell = legs_shell(gb, L, top, L.knee_ring, offset, paint)
    waistband(gb, L, shell.ids(gb.trunk_row(top)), 0.032, 0.011, mask=TRIM if corduroy else PRIMARY)
    n = 9

    def leg_paint(k, c):
        if corduroy:
            if k >= n - 2:
                return "dye", TRIM  # turn-ups
            return ("dye_soft" if c % 2 else "dye"), PRIMARY
        return ("dye_light" if c == 0 else "dye"), PRIMARY

    hem_back, hem_front = 0.034 * L.sc, 0.09 * L.sc
    legs = trouser_legs(gb, L, shell, n, hem_back, hem_front, 0.024 if corduroy else 0.02, leg_paint, 0.068 * L.sc)
    for side, rings in legs.items():
        if corduroy:
            # Turn-up cuff: a raised band before the fold.
            def make(s, r0, k, side=side):
                c = L.leg_axis(side, float(r0[:, 2].mean()))
                p = _radial(r0, c, 0.006 if k == 1 else -0.004)
                p[:, 2] += 0.03 if k == 1 else 0.026
                return p

            gb.strip(list(rings[-1]), make, 2, lambda k, c: ("dye", TRIM) if k == 0 else ("dye_shade", TRIM))
        else:
            hem_fold(gb, L, rings[-1], side)
    return {"hides": ["hips", "thighs", "calves"], "long": False}


@builder("gear/legs/trousers_corduroy_a")
def trousers_corduroy(gb: GearBuilder, L: Landmarks) -> dict:
    return trousers_school(gb, L, corduroy=True)


# ----------------------------------------------------------------------------
# Shoes
# ----------------------------------------------------------------------------


def shoe_shell(gb: GearBuilder, L: Landmarks, top_ring: int, offset, paint):
    def ok(tag, c):
        t = parse_tag(tag)
        if not t[0].startswith("leg"):
            return False
        if t[1] == "cap":
            return True
        return isinstance(t[1], int) and t[1] >= top_ring

    sh = gb.shell(ok, offset, paint)
    # Flatten the sole onto the floor in every shape.
    ids = sorted(sh.body_to_gear.values())
    for s in gb.shapes:
        P = gb.positions(s)
        z = P[ids, 2]
        # Write back through the chunk list (positions() copies).
        _set_positions(gb, s, ids, np.column_stack([P[ids, 0], P[ids, 1], np.maximum(z, 0.0)]))
    return sh


def _set_positions(gb: GearBuilder, shape: str, ids, values: np.ndarray) -> None:
    allp = gb.positions(shape)
    allp[ids] = values
    gb.pos[shape] = [allp]


def _shoe_offsets(gb: GearBuilder, L: Landmarks, base: float, toe: float):
    fp = K.foot_points(L.b)
    ball_fwd = -fp["ball"][1]

    def offset(s, ids):
        p = gb.P["base"][ids]
        fwd = -p[:, 1]
        return base + toe * np.clip((fwd - (ball_fwd - 0.03)) / 0.08, 0, 1)

    return offset


def _shoe_paint(L: Landmarks, laces_from: float, laces_to: float, sole_z: float, lace_mask=SECONDARY, upper_mask=PRIMARY):
    fp = K.foot_points(L.b)
    ankle_fwd = -fp["ankle"][1]

    def paint(tag, c):
        x = abs(c[0]) - abs(fp["ankle"][0])
        fwd = -c[1]
        if c[2] < sole_z:
            return "sole_edge", NONE
        if laces_from < fwd - ankle_fwd < laces_to and abs(x) < 0.016 and c[2] > 0.045 * L.sc:
            return "lace", NONE if lace_mask is None else lace_mask
        return "dye", upper_mask

    return paint


def shoe_collar(gb: GearBuilder, L: Landmarks, ring_ids, side: int, mask=PRIMARY):
    def make(s, r0, k):
        c = L.leg_axis(side, float(r0[:, 2].mean()))
        if k == 1:
            p = _radial(r0, c, 0.004)
            p[:, 2] += 0.006
            return p
        p = _radial(r0, c, -0.006)
        p[:, 2] += 0.002
        return p

    return gb.strip(list(ring_ids), make, 2, lambda k, c: ("dye", mask) if k == 0 else ("inner_dark", NONE))


def sole_slab(gb: GearBuilder, L: Landmarks, side: int, thickness: float, out: float, heel: float = 0.0):
    """A welt and sole around the bottom foot ring, down to the floor."""
    ring = gb.ring(f"leg_{side}", L.n_leg_rings - 1)  # lowest foot slice
    # The slab is its own closed part: top ring, bottom ring and a bottom cap.
    fp = K.foot_points(L.b)
    heel_fwd = -fp["heel_back"][1]
    pts_top, pts_bot = {}, {}
    for s in gb.shapes:
        P = gb.P[s][ring]
        c = P.mean(axis=0)
        top = _radial(P, c, out)
        top[:, 2] = thickness
        bot = _radial(P, c, out - 0.002)
        bot[:, 2] = 0.0
        if heel:
            fwd = -P[:, 1]
            lift = heel * np.clip((heel_fwd + 0.075 - fwd) / 0.03, 0, 1)
            top[:, 2] += lift
        pts_top[s], pts_bot[s] = top, bot
    w = [gb.body_md.weights[i] for i in ring]
    a = gb.add(pts_top, w)
    bring = gb.add(pts_bot, w)
    gb.connect([a, bring], lambda k, c: ("sole_edge", NONE))
    centre = {s: np.r_[pts_bot[s][:, :2].mean(axis=0), 0.0] for s in gb.shapes}
    gb.cap(bring[::-1], centre, w[0], ("sole", NONE))
    centre_top = {s: np.r_[pts_top[s][:, :2].mean(axis=0), thickness] for s in gb.shapes}
    gb.cap(a, centre_top, w[0], ("sole", NONE))


@builder("gear/shoes/oxford_school_a")
def shoes_oxford(gb: GearBuilder, L: Landmarks) -> dict:
    sh = shoe_shell(gb, L, L.ankle_ring + 1, _shoe_offsets(gb, L, 0.006, 0.008), _shoe_paint(L, 0.03, 0.10, 0.012 * L.sc))
    for side in (1, -1):
        shoe_collar(gb, L, sh.ids(gb.ring(f"leg_{side}", L.ankle_ring + 1)), side)
        sole_slab(gb, L, side, 0.014 * L.sc, 0.011, heel=0.012)
    return {"hides": ["feet"], "long": False}


# ----------------------------------------------------------------------------
# Shirts
# ----------------------------------------------------------------------------


def top_shell(gb: GearBuilder, L: Landmarks, neck_row: int, hem_row: int, arm_end: int, torso_off, arm_off, paint,
              exclude=None):
    """Torso from `neck_row` down to `hem_row` plus sleeves up to arm ring `arm_end`."""

    def ok(tag, c):
        t = parse_tag(tag)
        if exclude and exclude(t, c):
            return False
        if t[0] == "trunk":
            return neck_row <= t[1] < hem_row
        if t[0].startswith("arm") and isinstance(t[1], int):
            return t[1] < arm_end
        return False

    def offset(s, ids):
        out = np.zeros(len(ids))
        P = gb.P["base"][ids]
        for i, vid in enumerate(ids):
            dom = _dominant(gb.body_md.weights[vid])
            if "arm" in dom or "hand" in dom:
                out[i] = arm_off(P[i])
            else:
                out[i] = torso_off(P[i])
        return out

    return gb.shell(ok, offset, paint)


def cuff(gb: GearBuilder, ring_ids, side: int, length: float, widen: float, sw="dye", mask=PRIMARY, inner=("dye_shade", PRIMARY)):
    def make(s, r0, k):
        c = r0.mean(axis=0)
        ax = gb_axis(r0)
        if k == 1:
            p = c + (r0 - c) * widen + ax * length
            return p
        p = c + (r0 - c) * max(widen * 0.92, 1.03) + ax * (length - 0.006)
        return p

    return gb.strip(list(ring_ids), make, 2, lambda k, c: (sw, mask) if k == 0 else inner)


def gb_axis(r0: np.ndarray) -> np.ndarray:
    """Unit normal of a ring's best-fit plane, pointing away from the body
    (toward the hand or foot for limb rings)."""
    c = r0.mean(axis=0)
    _, _, vt = np.linalg.svd(r0 - c)
    n = vt[2]
    # Rings run around the limb counter-clockwise seen from outside the tube;
    # orient by the outward direction (away from the body centre line).
    if float(n @ np.array([np.sign(c[0]) or 1.0, 0.0, -0.6])) < 0:
        n = -n
    return n


def tie(gb: GearBuilder, L: Landmarks, top_row: int, bottom_row: int, offset: float, width_top: float, width_bottom: float,
        stripes=(SECONDARY, TRIM)):
    """A tie down the front centre line over the shirt: a knot, then a blade
    whose rows run diagonally, so the alternating rows read as the classic
    diagonal stripes (wide `stripes[0]`, narrow `stripes[1]`)."""
    lay = L.lay
    ids = gb.bv([lay.tv(r, 0) for r in range(top_row, bottom_row + 1)])
    side = np.array([1.0, 0.0, 0.0])
    # Row positions along the tie (0..1): knot, then wide/narrow stripe pairs.
    knot = [0.0, 0.07]
    rows_t = list(knot)
    t = 0.07
    while t < 0.97:
        t += 0.055
        rows_t.append(min(t, 0.97))
        t += 0.025
        rows_t.append(min(t, 0.97))
    rows_t = sorted(set(rows_t)) + [1.0]
    slant = 0.012  # metres the left edge sits above the right (diagonal rows)

    def centre_line(s):
        P = gb.P[s][ids]
        N = gb.N[s][ids]
        seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        return P, N, cum / cum[-1]

    rings = []
    for i, tr in enumerate(rows_t):
        pts = {}
        for s in gb.shapes:
            P, N, u = centre_line(s)
            j = int(np.clip(np.searchsorted(u, tr) - 1, 0, len(u) - 2))
            f = (tr - u[j]) / max(u[j + 1] - u[j], 1e-9)
            c = P[j] + (P[j + 1] - P[j]) * f
            n = N[j] + (N[j + 1] - N[j]) * f
            n /= np.linalg.norm(n)
            if i < len(knot):
                half = width_top * 0.42
                c = c + n * (offset + 0.008)
                pts[s] = np.array([c - side * half, c, c + side * half])
                continue
            half = (width_top + (width_bottom - width_top) * tr) / 2.0
            c = c + n * offset
            if i == len(rows_t) - 1:  # the point
                pts[s] = np.array([c - side * half * 0.25 + [0, 0, 0.02], c, c + side * half * 0.25 + [0, 0, 0.02]])
                continue
            up = np.array([0.0, 0.0, slant])
            pts[s] = np.array([c - side * half + up, c, c + side * half - up])
        j = min(int(tr * (len(ids) - 1)), len(ids) - 1)
        rings.append(gb.add(pts, [dict(gb.body_md.weights[ids[j]])] * 3))

    def paint(k, c):
        if k == 0:
            return "dye", stripes[0]
        return "dye", stripes[(k - 1) % 2]

    n0 = len(gb.faces)
    gb.connect(rings, paint, closed=False)
    return list(range(n0, len(gb.faces)))


@builder("gear/body/shirt_oxford_tie_a")
def shirt_oxford(gb: GearBuilder, L: Landmarks) -> dict:
    b = L.b

    def torso_off(p):
        t = np.clip((b.chest_z - p[2]) / (b.chest_z - b.hip_z), 0, 1)
        return 0.010 + 0.010 * t

    def arm_off(p):
        return 0.009

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (0, 39) and t[1] > L.row_neck_base + 1:
            return "dye_stitch", PRIMARY  # button placket
        return "dye", PRIMARY

    hem = L.hip_row
    stand = L.lay.row_neck + 2  # the collar stand covers the lower neck rings
    sh = top_shell(gb, L, stand, hem, L.wrist_ring - 1, torso_off, arm_off, paint)
    turned_hem(gb, sh.ids(gb.trunk_row(hem)), lambda s, r0: np.array([0.0, r0[:, 1].mean(), 0.0]), 0.028, 0.006)
    for side in (1, -1):
        cuff(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 1)), side, 0.014, 1.14)
    leaf = collar_leaf(gb, sh.ids(gb.trunk_row(stand)), 0.03, 0.018, 0.03)
    tie_faces = tie(gb, L, L.row_neck_base, L.waist_row - 1, 0.02, 0.036, 0.07)
    return {"hides": ["torso", "upper_arms", "forearms"], "long": False,
            "thicken": [(leaf, 0.003, ("dye_shade", PRIMARY), ("dye", PRIMARY)),
                        (tie_faces, 0.004, ("dye_shade", SECONDARY), ("dye", SECONDARY))]}


def _face_theta(col: int) -> float:
    """Azimuth (degrees, 0 = front, signed) of the trunk face right of column col."""
    a = (col + 0.5) * 9.0
    return a if a <= 180 else a - 360.0


def v_neck(L: Landmarks, top_row: int, bottom_z: float, half_angle: float):
    """Predicate: is trunk face (r, c) inside a V opening at the front?"""

    top_z = _row_z(L, top_row)

    def width(r: int) -> float:
        """Half width (degrees) of the opening at trunk row r."""
        if r < top_row:
            return half_angle
        z = _row_z(L, r)
        if z <= bottom_z:
            return 0.0
        return half_angle * (z - bottom_z) / max(top_z - bottom_z, 1e-6)

    def inside(r: int, c: int) -> bool:
        return abs(_face_theta(c)) < min(width(r), width(r + 1)) if r >= top_row else abs(_face_theta(c)) < half_angle

    inside.width = width
    return inside


def _opening_col(kept: list[bool], side: int) -> int | None:
    """The first kept column on `side` of a front opening in a trunk row, or
    None when the row has no opening at the front. The search starts at the
    front and stops at the first kept column, so a gap further round (the
    arm holes beside the shoulders) is never taken for the opening's edge;
    taking it pulled a vertex of each shoulder to the neck."""
    cols = range(1, 21) if side > 0 else range(39, 19, -1)
    cb = next((c for c in cols if kept[c]), None)
    if cb is None or kept[(cb - side) % 40]:
        return None
    return cb


def snap_opening(gb: GearBuilder, shell, rows, width) -> None:
    """Slide the vertices on the edge of a front opening along their trunk row
    onto the exact edge line (`width(r)` degrees either side of the front), so
    a V-neck or robe front is straight instead of stepped by the grid."""
    for r in rows:
        w = width(r)
        if w <= 0:
            continue
        row = [int(v) for v in gb.trunk_row(r)]
        kept = [v in shell.body_to_gear for v in row]
        for side in (1, -1):
            cb = _opening_col(kept, side)
            if cb is None:
                continue
            ci = (cb - side) % 40
            th_b = cb * 9.0 if side > 0 else (40 - cb) * 9.0
            f = float(np.clip((th_b - w) / 9.0, 0.0, 0.95))
            if f <= 0:
                continue
            g = shell.body_to_gear[row[cb]]
            for s in gb.shapes:
                P, N = gb.P[s], gb.N[s]
                G = gb.positions(s)
                off = float((G[g] - P[row[cb]]) @ N[row[cb]])
                n = N[row[cb]] * (1 - f) + N[row[ci]] * f
                n /= np.linalg.norm(n)
                new = P[row[cb]] * (1 - f) + P[row[ci]] * f + n * off
                _set_positions(gb, s, [g], new[None])


def opening_edge(gb: GearBuilder, shell, rows) -> tuple[list[int], list[int]]:
    """Item vertex ids on the left and right edges of a front opening, one per
    trunk row (top to bottom)."""
    left, right = [], []
    for r in rows:
        row = [int(v) for v in gb.trunk_row(r)]
        kept = [v in shell.body_to_gear for v in row]
        if all(kept):
            break
        for side, out in ((1, left), (-1, right)):
            cb = _opening_col(kept, side)
            if cb is not None:
                out.append(shell.body_to_gear[row[cb]])
    return left, right


def v_trim(gb: GearBuilder, shell, rows, widths=(0.009, 0.009), masks=(SECONDARY, TRIM), lift: float = 0.002) -> list[int]:
    """Knitted stripes along both edges of a V opening: bands laid on the item
    surface beside the (snapped) edge, `widths` metres each, away from the
    opening. Returns the band faces."""
    left, right = opening_edge(gb, shell, rows)
    faces = []
    normals = {s: vertex_normals_of(gb, s) for s in gb.shapes}
    for edge, side in ((left, 1), (right, -1)):
        if len(edge) < 2:
            continue
        lines = [np.array(edge)]
        for j in range(len(widths)):
            pts = {}
            for s in gb.shapes:
                P = gb.positions(s)[edge]
                N = normals[s][edge]
                d = np.gradient(P, axis=0)
                lat = np.cross(d, N)
                lat /= np.maximum(np.linalg.norm(lat, axis=1, keepdims=True), 1e-9)
                lat *= np.sign(lat[:, 0:1] * side + 1e-12)
                pts[s] = P + lat * sum(widths[:j + 1]) + N * lift
            lines.append(gb.add(pts, [dict(gb.weights[i]) for i in edge]))
        # Lift the edge line itself too, so the band floats just above the knit.
        base = {s: gb.positions(s)[edge] + normals[s][edge] * lift for s in gb.shapes}
        lines[0] = gb.add(base, [dict(gb.weights[i]) for i in edge])
        n0 = len(gb.faces)
        for j in range(len(widths)):
            a, b = lines[j], lines[j + 1]
            for i in range(len(edge) - 1):
                gb.face((a[i], a[i + 1], b[i + 1], b[i]), "dye", masks[j])
        faces += list(range(n0, len(gb.faces)))
    return faces


def vertex_normals_of(gb: GearBuilder, shape: str) -> np.ndarray:
    from .gear_lib import vertex_normals

    return vertex_normals(gb.positions(shape), gb.faces)


def open_arc(shell, row_ids) -> list[int]:
    """The vertices of a trunk row that the shell kept, as one arc running
    round the back from one side of a front opening to the other."""
    row = [int(v) for v in row_ids]
    kept = [v in shell.body_to_gear for v in row]
    n = len(row)
    if all(kept):
        return row
    start = next(i for i in range(n) if kept[i] and not kept[(i - 1) % n])
    arc = []
    i = start
    while kept[i % n] and len(arc) < n:
        arc.append(row[i % n])
        i += 1
    return arc


def row_of_z(L: Landmarks, z: float) -> int:
    """Trunk row (from the neck base down) whose front point is nearest z."""
    rows = range(L.row_neck_base, L.last_row + 1)
    return min(rows, key=lambda r: abs(_row_z(L, r) - z))


def _row_z(L: Landmarks, r: int) -> float:
    """Height of trunk row r at the front centre (neutral body)."""
    gb = L.gb
    vid = gb.bv([L.lay.tv(r, 0)])[0]
    return float(gb.P["base"][vid, 2])


def rib_band(gb: GearBuilder, ring_ids, axis_fn, length: float, out: float, ribs: bool = True, mask=PRIMARY,
             stripes=None, n: int = 2):
    """A knitted rib band (hem or cuff) continuing a loop: `axis_fn(s, r0)`
    gives (centre, direction) of the band. Alternate columns are shaded for
    ribbing; `stripes` (list of masks per ring) adds coloured bands."""

    def make(s, r0, k):
        c, d = axis_fn(s, r0)
        t = k / n
        p = r0 + d * (length * t)
        p = c + (p - c)
        if k < n:
            p = _push_from_axis(p, c, d, out * (1.0 - 0.3 * t))
        else:
            p = _push_from_axis(p, c, d, out - 0.008)
            p = p - d * 0.006
        return p

    def paint(k, col):
        m = stripes[k] if stripes and k < len(stripes) else mask
        if k == n - 1:
            return "dye_shade", m
        return ("dye_soft" if ribs and col % 2 else "dye"), m

    return gb.strip(list(ring_ids), make, n, paint)


def _push_from_axis(p: np.ndarray, c: np.ndarray, d: np.ndarray, amount) -> np.ndarray:
    v = p - c
    v = v - np.outer(v @ d, d)
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return p + v / np.maximum(n, 1e-9) * amount


def _down_axis(s, r0):
    return r0.mean(axis=0), np.array([0.0, 0.0, -1.0])


def _limb_axis(s, r0):
    return r0.mean(axis=0), gb_axis(r0)


def shirt_under_v(gb: GearBuilder, L: Landmarks, inside, top_row: int, offset: float, tie_bottom_row: int,
                  shirt_sw=("shirt_white", NONE), tie_masks=(SECONDARY, TRIM)) -> list[int]:
    """The shirt front, collar and tie seen inside a V-neck (undyed shirt)."""
    stand = L.lay.row_neck + 2

    def ok(tag, c):
        t = parse_tag(tag)
        return t[0] == "trunk" and stand <= t[1] < top_row + 8 and (t[1] < L.row_neck_base or inside(t[1], t[2]) or
                                                                    abs(_face_theta(t[2])) < 25)

    sh = gb.shell(ok, lambda s, ids: np.full(len(ids), offset), lambda tag, c: shirt_sw)
    leaf = collar_leaf(gb, sh.ids(gb.trunk_row(stand)), 0.028, 0.016, 0.028, mask=shirt_sw[1], sw=shirt_sw[0])
    tie_faces = tie(gb, L, L.row_neck_base, tie_bottom_row, offset + 0.008, 0.034, 0.066, stripes=tie_masks)
    return leaf + tie_faces


@builder("gear/body/jumper_vneck_school_a")
def jumper_school(gb: GearBuilder, L: Landmarks) -> dict:
    """V-neck school jumper with house-striped V, cuffs and hem; the shirt
    collar and tie show in the V."""
    b = L.b
    v_bottom = b.chest_z + 0.01 * L.sc
    inside = v_neck(L, L.row_neck_base + 1, v_bottom, 28.0)

    def exclude(t, c):
        return t[0] == "trunk" and inside(t[1], t[2])

    def torso_off(p):
        return 0.017 + 0.009 * np.clip((b.chest_z - p[2]) / (b.chest_z - b.hip_z), 0, 1)

    def paint(tag, c):
        return "dye", PRIMARY

    hem = L.hip_row
    sh = top_shell(gb, L, L.row_neck_base, hem, L.wrist_ring - 1, torso_off, lambda p: 0.013, paint, exclude=exclude)
    snap_opening(gb, sh, range(L.row_neck_base, hem), inside.width)
    v_trim(gb, sh, range(L.row_neck_base, hem))
    rib_band(gb, sh.ids(gb.trunk_row(hem)), _down_axis, 0.045, 0.004, stripes=[PRIMARY, SECONDARY])
    for side in (1, -1):
        rib_band(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 1)), _limb_axis, 0.024, 0.003, stripes=[PRIMARY, TRIM])
    faces = shirt_under_v(gb, L, inside, L.row_neck_base + 1, 0.009, row_of_z(L, v_bottom + 0.03 * L.sc))
    return {"hides": ["torso", "upper_arms", "forearms"], "long": False,
            "thicken": [(faces, 0.003, ("shirt_shade", NONE), ("shirt_white", NONE))]}


@builder("gear/body/sweater_cable_knit_a")
def sweater_cable(gb: GearBuilder, L: Landmarks) -> dict:
    """Chunky crew-neck sweater: cable columns knitted into the front and
    back, ribbed collar, cuffs and hem."""
    b = L.b

    def torso_off(p):
        theta = math.atan2(p[0], -p[1])
        twist = 1.8 * math.sin(p[2] * 70.0)
        cable = 0.5 + 0.5 * math.cos(10 * theta + twist * (abs(math.sin(5 * theta)) > 0.4))
        front_back = abs(math.cos(theta)) ** 0.5
        return 0.02 + 0.008 * np.clip((b.chest_z - p[2]) / (b.chest_z - b.hip_z), 0, 1) + 0.0045 * cable * front_back

    def arm_off(p):
        return 0.016

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk":
            theta = math.radians(_face_theta(t[2]))
            if math.cos(10 * theta) < -0.55 and abs(math.cos(theta)) > 0.45:
                return "dye_soft", PRIMARY
        if t[0].startswith("arm") and t[2] % 4 == 0:
            return "dye_soft", PRIMARY
        return "dye", PRIMARY

    hem = L.hip_row
    neck = L.row_neck_base
    sh = top_shell(gb, L, neck, hem, L.wrist_ring - 1, torso_off, arm_off, paint)

    def up_axis(s, r0):
        return r0.mean(axis=0), np.array([0.0, 0.0, 1.0])

    rib_band(gb, sh.ids(gb.trunk_row(neck)), up_axis, 0.03, 0.004, mask=TRIM)
    rib_band(gb, sh.ids(gb.trunk_row(hem)), _down_axis, 0.05, 0.002, mask=TRIM)
    for side in (1, -1):
        rib_band(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 1)), _limb_axis, 0.024, 0.003, mask=TRIM)
    return {"hides": ["torso", "upper_arms", "forearms"], "long": False}


@builder("gear/body/shirt_linen_rolled_a")
def shirt_linen(gb: GearBuilder, L: Landmarks) -> dict:
    """Relaxed linen shirt, open collar, sleeves rolled above the elbow."""
    b = L.b
    inside = v_neck(L, L.row_neck_base + 1, b.chest_z + 0.035 * L.sc, 30.0)

    def exclude(t, c):
        return t[0] == "trunk" and inside(t[1], t[2])

    def torso_off(p):
        return 0.012 + 0.012 * np.clip((b.chest_z - p[2]) / (b.chest_z - b.hip_z), 0, 1)

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (0, 39) and not inside(t[1], t[2]):
            return "dye_stitch", PRIMARY
        return "dye", PRIMARY

    hem = L.hip_row
    stand = L.lay.row_neck + 2
    arm_end = L.elbow_ring - 1
    sh = top_shell(gb, L, stand, hem, arm_end, torso_off, lambda p: 0.012, paint, exclude=exclude)
    snap_opening(gb, sh, range(stand, hem), inside.width)
    turned_hem(gb, sh.ids(gb.trunk_row(hem)), lambda s, r0: np.array([0.0, r0[:, 1].mean(), 0.0]), 0.045, 0.01)
    # Rolled cuff: a fat roll showing the fabric's reverse.
    for side in (1, -1):
        ring = sh.ids(gb.ring(f"arm_{side}", arm_end))

        def make(s, r0, k):
            c = r0.mean(axis=0)
            ax = gb_axis(r0)
            prof = {1: (1.22, 0.006), 2: (1.3, 0.022), 3: (1.24, 0.038), 4: (1.06, 0.036)}[k]
            return c + (r0 - c) * prof[0] + ax * prof[1]

        gb.strip(list(ring), make, 4, lambda k, c: ("dye_shade", PRIMARY) if k < 3 else ("dye", TRIM))
    # Open collar: the stand and leaf are split at the front.
    arc = open_arc(sh, gb.trunk_row(stand))
    leaf_ids = sh.ids(arc)

    def make_leaf(s, r0, k):
        c = r0.mean(axis=0)
        front = np.clip(_front_ness(r0, c), 0.0, 1.0) ** 2
        p = _radial(r0, c, 0.004 + (0.008 + 0.034 * front) * k / 2.0)
        p[:, 2] += 0.004 if k == 1 else -(0.024 + 0.026 * front)
        return p

    n0 = len(gb.faces)
    gb.strip(list(leaf_ids), make_leaf, 2, lambda k, c: ("dye", PRIMARY), closed=False)
    leaf = list(range(n0, len(gb.faces)))
    return {"hides": ["torso", "upper_arms"], "long": False,
            "thicken": [(leaf, 0.003, ("dye_shade", PRIMARY), ("dye", PRIMARY))]}


# ----------------------------------------------------------------------------
# Robes and cloak
# ----------------------------------------------------------------------------


def skirt_weights(gb: GearBuilder, ring0_w: list[dict], ring0_pos: np.ndarray, n: int, follow: float):
    """Weights for a hanging skirt: from the anchor ring's weights toward the
    thighs on each side (by lateral position), so the front of a skirt swings
    with the leg beneath it."""

    def weight(k, c, w0):
        t = (k / n) ** 0.8 * follow
        x = ring0_pos[c, 0]
        lat = np.clip(x / 0.12, -1.0, 1.0)
        wl = 0.5 + 0.5 * lat
        legs = {"DEF-thigh.L": wl * 0.7, "DEF-thigh.L.001": wl * 0.3, "DEF-thigh.R": (1 - wl) * 0.7, "DEF-thigh.R.001": (1 - wl) * 0.3}
        base = {"DEF-spine": 1.0}
        out = {}
        for d, f in ((base, 1 - t), (legs, t)):
            for kk, v in d.items():
                out[kk] = out.get(kk, 0.0) + v * f
        return out

    return weight


def hanging_skirt(gb: GearBuilder, L: Landmarks, ring_ids, hem_z: float, flare: float, open_front: float = 0.0,
                  pleats: float = 0.0, n: int = 8, paint=None, follow: float = 0.75, closed: bool = True):
    """A skirt hanging from a loop around the hips to `hem_z`, flaring by
    `flare` metres at the hem. `open_front` widens a front split (metres at the
    hem); `pleats` adds alternating in/out folds."""
    ring_ids = list(ring_ids)
    base = gb.positions("base")[ring_ids]
    w0 = [gb.weights[i] for i in ring_ids]

    def make(s, r0, k):
        t = k / n
        c = np.array([0.0, r0[:, 1].mean() * 0.5, 0.0])
        z = r0[:, 2] + (hem_z - r0[:, 2]) * t
        p = _radial(r0, c, flare * t ** 1.3 + 0.004)
        if pleats:
            sign = np.array([1.0 if i % 2 else -1.0 for i in range(len(r0))])
            p = _radial(p, c, sign * pleats * min(1.0, t * 2.0))
        if open_front:
            front = _front_ness(r0, c)
            side = np.sign(r0[:, 0])
            p[:, 0] += side * open_front * t * np.clip(front, 0, 1) ** 4
        p[:, 2] = z
        return p

    paint = paint or (lambda k, c: ("dye", PRIMARY))
    return gb.strip(ring_ids, make, n, paint, closed=closed, weight=skirt_weights(gb, w0, base, n, follow))


def bell_sleeve(gb: GearBuilder, ring_ids, length: float, flare: float, droop: float, n: int, paint,
                L: Landmarks | None = None, side: int = 1, start_ring: int = 0) -> list[np.ndarray]:
    """A sleeve widening from an arm ring toward the hand. Each ring takes the
    weights of the body arm ring at the same distance along the arm, so the
    sleeve bends with the elbow and twists with the forearm."""
    s0 = L.arm_ring_s(start_ring) if L else 0.0
    arm_rings = {k: L.arm_ring_s(k) for k in range(L.arm_first, len(L.lay.tubes["arm_1"].idx))} if L else {}

    def weight(k, c, w0):
        if not L:
            return w0
        target = s0 + length * k / n
        kk = min(arm_rings, key=lambda q: abs(arm_rings[q] - target))
        return gb.body_md.weights[gb.ring(f"arm_{side}", kk)[c]]

    def make(s, r0, k):
        t = k / n
        c = r0.mean(axis=0)
        ax = gb_axis(r0)
        p = c + (r0 - c) * (1.0 + flare * t ** 1.4) + ax * (length * t)
        # The open sleeve hangs: lower points drop.
        low = np.clip(-(r0 - c)[:, 2] / max(np.abs((r0 - c)[:, 2]).max(), 1e-6), 0, 1)
        p[:, 2] -= droop * t ** 1.5 * (0.4 + 0.6 * low)
        return p

    return gb.strip(list(ring_ids), make, n, paint, weight=weight)


def _back_arc(ids: list[int], P: np.ndarray, half_width: float = 0.075) -> list[int]:
    """The back arc of an ordered neckline loop, left to right: the run of
    consecutive loop vertices behind the neck (y > 0) within `half_width` of
    the centre line that holds the centre back. Points of the loop that pass
    back inside that band further round (a wide neckline bulging out over
    the shoulders, then in again at the side of the neck) are not part of it:
    sorted in by x, they zig-zagged the hood's side columns forward over the
    front of the shoulders (the hooded cloak on body_b)."""
    n = len(ids)
    ok = [bool(p[1] > 0.0 and abs(p[0]) < half_width) for p in P]
    if not any(ok):
        return []
    if all(ok):
        run = list(range(n))
    else:
        c = int(np.argmax(np.where(ok, P[:, 1], -np.inf)))
        lo = c
        while ok[(lo - 1) % n] and (lo - 1) % n != c:
            lo = (lo - 1) % n
        run = [lo]
        while ok[(run[-1] + 1) % n] and (run[-1] + 1) % n != lo:
            run.append((run[-1] + 1) % n)
    arc = [ids[i] for i in run]
    if P[run[0], 0] > P[run[-1], 0]:
        arc.reverse()
    return arc


def hood(gb: GearBuilder, L: Landmarks, neck_ids, mask=PRIMARY, lining=SECONDARY, post=None) -> list[int]:
    """A hood lying down the upper back, gathered at the back of the neckline.
    `post(shape, points, k)` may adjust each ring (the garment shapes keep it
    over the garment's own back)."""
    ids = list(neck_ids)
    P = gb.positions("base")[ids]
    back = _back_arc(ids, P)
    sc = L.sc

    def make(s, r0, k):
        c = r0.mean(axis=0)
        width = np.linspace(-1, 1, len(r0))
        p = r0.copy()
        # Folded flat on the back (character adoption): long hair hangs over
        # it, so it stands at most a few centimetres off the neckline and no
        # wider than the shoulder blades (it stood 6 cm off and the long
        # styles' side locks went through its edges).
        prof = {1: (0.0, 0.022, 1.15), 2: (-0.06, 0.034, 1.15), 3: (-0.14, 0.03, 1.0), 4: (-0.22, 0.022, 0.55)}[k]
        p[:, 0] = c[0] + (r0[:, 0] - c[0]) * prof[2] * 1.35
        p[:, 1] = r0[:, 1] + prof[1] * sc * (1.0 - 0.3 * width ** 2)
        p[:, 2] = r0[:, 2] + prof[0] * sc - 0.02 * width ** 2 * (k > 2)
        return post(s, p, k) if post else p

    n0 = len(gb.faces)
    # Folding the hood down over the back turns it inside out relative to the
    # robe, so the strip carries the lining colour and its thickened copy (the
    # side seen from outside) the robe colour.
    gb.strip(back, make, 4, lambda k, c: ("dye", lining), closed=False)
    return list(range(n0, len(gb.faces)))


def crest(paint_fn, L: Landmarks, centre_theta: float, centre_z: float):
    """Wrap a paint function so a small shield crest shows on the chest."""

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk":
            # A 3 x 3-face badge: house colour field, trim border.
            col_off = round((_face_theta(t[2]) - centre_theta) / 9.0)
            row_off = t[1] - row_of_z(L, centre_z)
            if abs(col_off) <= 1 and abs(row_off) <= 1:
                return "dye", SECONDARY if (col_off == 0 and row_off == 0) else TRIM
        return paint_fn(tag, c)

    return paint


@builder("gear/body/robe_school_house_a")
def robe_school(gb: GearBuilder, L: Landmarks) -> dict:
    """The school robe: an open-front black robe with house-lined hood and
    sleeves, worn over a V-neck jumper, shirt and house tie."""
    b = L.b
    sc = L.sc

    def robe_width(r: int) -> float:
        return 22.0 + 22.0 * float(np.clip((b.neck_base_z - _row_z(L, r)) / 0.16, 0, 1))

    def opening(r: int, c: int) -> bool:
        th = abs(_face_theta(c))
        width = min(robe_width(r), robe_width(r + 1))
        return th < width

    def exclude(t, c):
        return t[0] == "trunk" and opening(t[1], t[2])

    def torso_off(p):
        return 0.03 + 0.008 * np.clip((b.chest_z - p[2]) / (b.chest_z - b.hip_z), 0, 1)

    def arm_off(p):
        return 0.024

    def paint(tag, c):
        return "dye", PRIMARY

    paint = crest(paint, L, 56.0, b.chest_z + 0.04 * sc)
    hem = L.hip_row
    sh = top_shell(gb, L, L.row_neck_base, hem, L.elbow_ring, torso_off, arm_off, paint, exclude=exclude)
    snap_opening(gb, sh, range(L.row_neck_base, hem + 1), robe_width)

    # Under the robe: V-neck jumper front with shirt collar and tie.
    inside = v_neck(L, L.row_neck_base + 1, b.chest_z - 0.01 * sc, 26.0)

    def jumper_ok(tag, c):
        t = parse_tag(tag)
        return t[0] == "trunk" and L.row_neck_base <= t[1] < hem and opening(t[1], t[2]) and not inside(t[1], t[2])

    def jumper_paint(tag, c):
        return "jumper_grey", NONE

    jsh = gb.shell(jumper_ok, lambda s, ids: np.full(len(ids), 0.016), jumper_paint)
    snap_opening(gb, jsh, range(L.row_neck_base, hem), inside.width)
    v_trim(gb, jsh, range(L.row_neck_base, hem))
    under = shirt_under_v(gb, L, inside, L.row_neck_base + 1, 0.009, row_of_z(L, b.chest_z + 0.02 * sc))
    # Skirt from the hips to mid-calf, open at the front.
    hip_loop = gb.trunk_row(hem)
    cols = [c for c in range(40) if not opening(hem - 1, c)]
    # Rotate so the loop starts just right of the opening and runs round the back.
    start = next(c for c in range(40) if not opening(hem - 1, c) and opening(hem - 1, (c - 1) % 40))
    order = [(start + i) % 40 for i in range(40) if (start + i) % 40 in cols]
    order.append((order[-1] + 1) % 40)
    skirt_ring = sh.ids(hip_loop[order])
    hem_z = (0.30 if b.id == "body_a" else 0.29) * sc / 1.0
    n_sk = 9
    hanging_skirt(gb, L, skirt_ring, hem_z, 0.13 * sc, open_front=0.05, n=n_sk, closed=False)
    skirt_faces = list(range(len(gb.faces) - (len(order) - 1) * n_sk, len(gb.faces)))
    # Bell sleeves from the elbow.
    sleeve_faces = []
    for side in (1, -1):
        n0 = len(gb.faces)
        bell_sleeve(gb, sh.ids(gb.ring(f"arm_{side}", L.elbow_ring)), 0.27 * sc, 1.25, 0.03, 6, lambda k, c: ("dye", PRIMARY),
                    L=L, side=side, start_ring=L.elbow_ring)
        sleeve_faces += list(range(n0, len(gb.faces)))
        # Shirt cuff peeking out of the sleeve.
        wr = gb.ring(f"arm_{side}", L.wrist_ring - 1)
        cuff_sh = gb.shell(lambda tag, c, side=side: tag.startswith(f"arm_{side}:") and isinstance(parse_tag(tag)[1], int) and
                           L.wrist_ring - 4 <= parse_tag(tag)[1] < L.wrist_ring - 1,
                           lambda s, ids: np.full(len(ids), 0.010), lambda tag, c: ("shirt_white", NONE))
        cuff(gb, cuff_sh.ids(wr), side, 0.012, 1.12, sw="shirt_white", mask=NONE, inner=("shirt_shade", NONE))
    neck = [v for v in gb.trunk_row(L.row_neck_base) if int(v) in sh.body_to_gear]
    hood_faces = hood(gb, L, sh.ids(neck))
    lining = ("dye", SECONDARY)
    return {"hides": ["torso", "upper_arms", "forearms"], "long": False,
            "thicken": [(skirt_faces, 0.004, lining, ("dye", PRIMARY)),
                        (sleeve_faces, 0.004, lining, ("dye", PRIMARY)),
                        (hood_faces, 0.006, ("dye", PRIMARY), ("dye", SECONDARY)),
                        (under, 0.003, ("shirt_shade", NONE), ("shirt_white", NONE))]}


for _house in "bcd":
    BUILDERS[f"gear/body/robe_school_house_{_house}"] = robe_school


@builder("gear/body/cloak_travelling_a")
def cloak_travelling(gb: GearBuilder, L: Landmarks) -> dict:
    """A long travelling coat-cloak: fitted body and sleeves, a shoulder cape,
    an open skirt to the shins, lined, with a clasp at the collar."""
    b = L.b
    sc = L.sc

    def torso_off(p):
        return 0.028 + 0.008 * np.clip((b.chest_z - p[2]) / (b.chest_z - b.hip_z), 0, 1)

    def paint(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk" and t[2] in (0, 39):
            return "dye", TRIM  # front closure piping
        return "dye", PRIMARY

    hem = L.hip_row
    stand = L.lay.row_neck + 2
    sh = top_shell(gb, L, stand, hem, L.wrist_ring - 2, torso_off, lambda p: 0.022, paint)
    # Skirt, open from the hips down.
    loop = gb.trunk_row(hem)
    order = list(range(1, 40)) + [0]
    hem_z = 0.24 * sc
    n_sk = 10
    hanging_skirt(gb, L, sh.ids(loop[order]), hem_z, 0.11 * sc, open_front=0.07, n=n_sk, closed=False)
    skirt_faces = list(range(len(gb.faces) - 39 * n_sk, len(gb.faces)))
    # Turned-back cuffs showing the lining.
    for side in (1, -1):
        cuff(gb, sh.ids(gb.ring(f"arm_{side}", L.wrist_ring - 2)), side, 0.035, 1.18, sw="dye", mask=SECONDARY,
             inner=("dye_shade", SECONDARY))
    # Shoulder cape: a fresh loop just outside the collar base, flaring over the shoulders.
    base_ids = sh.ids(gb.trunk_row(L.row_neck_base))
    pts = {s: _radial(gb.positions(s)[base_ids], gb.positions(s)[base_ids].mean(axis=0), 0.008) for s in gb.shapes}
    cape0 = gb.add(pts, [dict(gb.weights[i]) for i in base_ids])

    def cape(s, r0, k):
        c = r0.mean(axis=0)
        t = k / 4
        p = _radial(r0, c, (0.05 + 0.04 * np.abs(r0[:, 0] - c[0]) / 0.1) * t ** 0.8 * 1.6)
        p[:, 2] -= (0.035 + 0.13 * t) * t * sc
        return p

    def cape_weight(k, col, w0):
        x = gb.positions("base")[cape0[col], 0]
        side = "L" if x > 0 else "R"
        t = min(1.0, abs(x) / 0.16) * (k / 4)
        return smooth({"DEF-spine.003": 1.0}, {f"DEF-shoulder.{side}": 0.5, f"DEF-upper_arm.{side}": 0.5}, t)

    n0 = len(gb.faces)
    gb.strip(list(cape0), cape, 4, lambda k, c: ("dye", PRIMARY) if k < 3 else ("dye", TRIM), weight=cape_weight)
    cape_faces = list(range(n0, len(gb.faces)))
    # Collar: a short stand-up band, and the clasp.
    leaf = collar_leaf(gb, sh.ids(gb.trunk_row(stand)), 0.012, 0.012, 0.0)
    return {"hides": ["torso", "upper_arms", "forearms"], "long": False,
            "thicken": [(skirt_faces, 0.004, ("dye", SECONDARY), ("dye", TRIM)),
                        (cape_faces, 0.004, ("dye", SECONDARY), ("dye", TRIM)),
                        (leaf, 0.003, ("dye", SECONDARY), ("dye", PRIMARY))]}


def smooth(a: dict, b: dict, t: float) -> dict:
    out = {k: v * (1 - t) for k, v in a.items()}
    for k, v in b.items():
        out[k] = out.get(k, 0.0) + v * t
    return out


# ----------------------------------------------------------------------------
# Skirt and tights
# ----------------------------------------------------------------------------


@builder("gear/legs/skirt_pleated_a")
def skirt_pleated(gb: GearBuilder, L: Landmarks) -> dict:
    """Knee-length pleated school skirt over opaque tights."""
    b = L.b
    top = L.waist_row - 1

    def ok(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk":
            return top <= t[1] < L.hip_row
        return False

    sh = gb.shell(ok, lambda s, ids: np.full(len(ids), 0.009), lambda tag, c: ("dye", PRIMARY))
    waistband(gb, L, sh.ids(gb.trunk_row(top)), 0.03, 0.011, mask=TRIM)
    hem_z = b.knee[2] + 0.07 * L.sc

    def pleat_paint(k, c):
        return ("dye_shade" if c % 2 else "dye"), PRIMARY

    n = 8
    hanging_skirt(gb, L, sh.ids(gb.trunk_row(L.hip_row)), hem_z, 0.085 * L.sc, pleats=0.006, n=n,
                          paint=pleat_paint, follow=0.6)
    skirt_faces = list(range(len(gb.faces) - 40 * n, len(gb.faces)))

    # Tights: hips below the skirt line and both legs down to the shoe line.
    def tights_ok(tag, c):
        t = parse_tag(tag)
        if t[0] == "trunk":
            return t[1] >= L.hip_row
        if t[0].startswith("leg") and isinstance(t[1], int):
            return t[1] < L.ankle_ring + 2
        return False

    gb.shell(tights_ok, lambda s, ids: np.full(len(ids), 0.003), lambda tag, c: ("dye", SECONDARY))
    return {"hides": ["hips", "thighs", "calves"], "long": False,
            "thicken": [(skirt_faces, 0.004, ("dye_shade", PRIMARY), ("dye", PRIMARY))]}


# ----------------------------------------------------------------------------
# Boots and loafers
# ----------------------------------------------------------------------------


def _ring_at_z(L: Landmarks, z: float) -> int:
    return min(L.leg_z, key=lambda k: abs(L.leg_z[k] - z))


@builder("gear/shoes/boots_lace_a")
def boots_lace(gb: GearBuilder, L: Landmarks) -> dict:
    top = _ring_at_z(L, 0.25 * L.sc)

    def offset(s, ids):
        p = gb.P["base"][ids]
        base = _shoe_offsets(gb, L, 0.007, 0.01)(s, ids)
        return base + 0.004 * np.clip((p[:, 2] - 0.12) / 0.1, 0, 1)

    fp = K.foot_points(L.b)

    def paint(tag, c):
        x = abs(c[0]) - abs(fp["ankle"][0])
        fwd = -c[1] - (-fp["ankle"][1])
        if c[2] < 0.012 * L.sc:
            return "sole_edge", NONE
        if abs(x) < 0.014 and c[2] > 0.05 * L.sc and fwd > -0.02 and (c[2] > 0.1 or fwd < 0.09):
            front = -(c[1] - fp["ankle"][1])
            if front > 0.0:
                return "lace", NONE
        return "dye", PRIMARY

    sh = shoe_shell(gb, L, top, offset, paint)
    for side in (1, -1):
        shoe_collar(gb, L, sh.ids(gb.ring(f"leg_{side}", top)), side, mask=SECONDARY)
        sole_slab(gb, L, side, 0.022 * L.sc, 0.012, heel=0.018)
    return {"hides": ["feet"], "long": False}


@builder("gear/shoes/loafers_buckle_a")
def loafers_buckle(gb: GearBuilder, L: Landmarks) -> dict:
    top = L.foot_first + 2  # low cut below the ankle bones
    fp = K.foot_points(L.b)

    def paint(tag, c):
        fwd = -c[1] - (-fp["ankle"][1])
        x = abs(c[0]) - abs(fp["ankle"][0])
        if c[2] < 0.012 * L.sc:
            return "sole_edge", NONE
        if 0.03 < fwd < 0.062 and c[2] > 0.04 * L.sc:
            return ("gold" if abs(x) < 0.012 and 0.038 < fwd < 0.054 else "dye"), (NONE if abs(x) < 0.012 and 0.038 < fwd < 0.054 else TRIM)
        if fwd > 0.11 and c[2] > 0.03 * L.sc and abs(x) < 0.02:
            return "dye_stitch", PRIMARY  # moccasin seam
        return "dye", PRIMARY

    sh = shoe_shell(gb, L, top, _shoe_offsets(gb, L, 0.006, 0.006), paint)
    for side in (1, -1):
        shoe_collar(gb, L, sh.ids(gb.ring(f"leg_{side}", top)), side, mask=SECONDARY)
        sole_slab(gb, L, side, 0.013 * L.sc, 0.010, heel=0.01)
    return {"hides": ["feet"], "long": False}


# ----------------------------------------------------------------------------
# Hats
# ----------------------------------------------------------------------------


HAT_COLS = 28


def head_band_ring(L: Landmarks, extra_cm: float, lift_cm: float = 0.0) -> np.ndarray:
    """Points round the head on the line a hat band sits on (front above the
    brows, back over the occipital bone), pushed out by extra_cm."""
    pts = []
    for j in range(HAT_COLS):
        th = 360.0 * j / HAT_COLS
        side = th if th <= 180 else 360 - th
        phi = 55.0 + 22.0 * (1 - math.cos(math.radians(side))) / 2.0 + 6.0 * math.sin(math.radians(side)) ** 2 - lift_cm * 3
        pts.append(L.head_point(phi, th, extra_cm))
    return np.array(pts)


def _static(gb: GearBuilder, pts: np.ndarray, weight: dict) -> np.ndarray:
    return gb.add({s: pts for s in gb.shapes}, [dict(weight) for _ in range(len(pts))])


@builder("gear/hat/pointed_a")
def hat_pointed(gb: GearBuilder, L: Landmarks) -> dict:
    """The classic pointed hat: wide soft brim, ribbon band, a tall crown
    whose tip folds back."""
    W = {"DEF-spine.006": 1.0}
    s = L.head_scale()
    band = head_band_ring(L, 2.4 * s)
    c = band.mean(axis=0)
    ring0 = _static(gb, band, W)
    # Brim: out and slightly down, wavy edge.
    n0 = len(gb.faces)
    brim = []
    for k, (out, drop) in enumerate(((0.035, 0.002), (0.075, 0.008), (0.105, 0.016)), start=1):
        p = _radial(band, c, out * s)
        wave = 0.006 * np.sin(np.arange(HAT_COLS) * 2 * np.pi * 3 / HAT_COLS)
        p[:, 2] -= drop * s + wave * (k / 3)
        brim.append(_static(gb, p, W))
    gb.connect([ring0] + brim, lambda k, cc: ("dye", PRIMARY))
    brim_faces = list(range(n0, len(gb.faces)))
    # Crown: up from the band, narrowing to a tip that bends back.
    top_z = c[2] + 0.33 * s
    crown = []
    n_c = 9
    for k in range(1, n_c + 1):
        t = k / n_c
        r = (1 - t) ** 1.15
        bend = np.array([0.0, 0.11 * s * t ** 2.4, -0.06 * s * t ** 4])
        centre = c + np.array([0.0, 0.0, (top_z - c[2]) * t]) + bend
        p = centre + (band - c) * r * np.array([1.0, 1.0, 0.0]) * (1.0 + 0.06 * math.sin(t * math.pi))
        p[:, 2] = centre[2] + (band[:, 2] - c[2]) * r
        crown.append(_static(gb, p, W))
    gb.connect([ring0] + crown, lambda k, cc: ("dye", SECONDARY) if k < 1 else ("dye", PRIMARY))
    tip = c + np.array([0.0, 0.11 * s, top_z - c[2] - 0.06 * s + 0.01])
    gb.cap(crown[-1], {sh: tip[None] for sh in gb.shapes}, W, ("dye", PRIMARY))
    return {"hides": ["scalp"], "long": False, "thicken": [(brim_faces, 0.004, ("dye_shade", PRIMARY), ("dye", PRIMARY))],
            "orient_ref": c + np.array([0.0, 0.0, 0.05])}


@builder("gear/hat/bobble_knit_a")
def hat_bobble(gb: GearBuilder, L: Landmarks) -> dict:
    """Knitted bobble hat: striped body over the head, folded cuff, pompom."""
    W = {"DEF-spine.006": 1.0}
    s = L.head_scale()
    rings = []
    rows = 8
    for i in range(rows + 1):
        t = i / rows
        pts = []
        for j in range(HAT_COLS):
            th = 360.0 * j / HAT_COLS
            side = th if th <= 180 else 360 - th
            band_phi = 58.0 + 22.0 * (1 - math.cos(math.radians(side))) / 2.0
            phi = band_phi * (1 - t) + 4.0 * t
            pts.append(L.head_point(phi, th, 2.2 * s + 0.4 * math.sin(math.pi * t)))
        rings.append(_static(gb, np.array(pts), W))

    def paint(k, c):
        band = (k // 2) % 2
        return ("dye_soft" if c % 2 else "dye"), (SECONDARY if band else PRIMARY)

    gb.connect(rings, paint)
    top = gb.positions("base")[rings[-1]].mean(axis=0)
    gb.cap(rings[-1], {sh: top[None] + np.array([[0, 0, 0.004]]) for sh in gb.shapes}, W, ("dye", PRIMARY))
    # Folded cuff.
    base = gb.positions("base")[rings[0]]
    c = base.mean(axis=0)
    cuff_rings = [rings[0]]
    for out, dz in ((0.007, 0.0), (0.009, 0.035), (0.004, 0.038)):
        p = _radial(base, c, out * s)
        p[:, 2] += dz * s
        cuff_rings.append(_static(gb, p, W))
    gb.connect(cuff_rings, lambda k, cc: ("dye_soft" if cc % 2 else "dye", SECONDARY if k == 1 else PRIMARY))
    # Pompom: a small lumpy sphere on top.
    pc = top + np.array([0.0, 0.0, 0.04 * s])
    pom = []
    lat = 6
    for i in range(1, lat):
        a = math.pi * i / lat
        ring = []
        for j in range(12):
            b = 2 * math.pi * j / 12
            r = 0.042 * s * (1.0 + 0.12 * math.sin(3 * b + i))
            ring.append(pc + r * np.array([math.sin(a) * math.cos(b), math.sin(a) * math.sin(b), math.cos(a)]))
        pom.append(_static(gb, np.array(ring), W))
    gb.connect(pom, lambda k, cc: ("dye", TRIM))
    gb.cap(pom[0][::-1], {sh: (pc + np.array([0, 0, 0.042 * s]))[None] for sh in gb.shapes}, W, ("dye", TRIM))
    gb.cap(pom[-1], {sh: (pc - np.array([0, 0, 0.042 * s]))[None] for sh in gb.shapes}, W, ("dye", TRIM))
    return {"hides": ["scalp"], "long": False, "orient_ref": c}


# ----------------------------------------------------------------------------
# Item entry point
# ----------------------------------------------------------------------------


def build_item(bb: PL.BodyBuild, item: dict, assets: str, manifest: dict) -> None:
    from . import content

    asset = item["asset"]
    fn = BUILDERS.get(asset)
    if fn is None:
        PL.log(f"no builder for {asset}; skipped")
        return
    gb = GearBuilder(bb.md, bb.layout, bb.body)
    L = Landmarks(gb)
    meta = fn(gb, L)
    gb.orient(meta.pop("orient_ref", None))
    for faces, thickness, inner, rim in meta.pop("thicken", []):
        gb.thicken(faces, thickness, inner, rim)
    md = gb.meshdata()
    name = asset.split("/")[-1] + "_" + bb.id
    coll = PL.collection(f"{bb.id}_gear")
    budget = 8000 if asset in LONG_BODY else BUDGETS[item["slot"]]
    meshes = PL.make_lods(name, md, bb.material, coll, budget=budget)
    for lvl, parts in meshes.items():
        for o in parts.values():
            o["asset"] = asset
            o["slot"] = item["slot"]
            o["hides"] = meta["hides"]
            o["long"] = bool(meta.get("long", False))
            RG.bind(o, bb.skel)
    base = {"kind": "clothing", "slot": item["slot"], "item_ids": [item["id"]], "hides": meta["hides"],
            "long": bool(meta.get("long", False)), "dye_mask": ["primary", "secondary", "trim"]}
    entry = manifest["assets"].get(asset)
    if entry and item["id"] not in entry.get("item_ids", []):
        base["item_ids"] = sorted(set(entry.get("item_ids", [])) | {item["id"]})
    content.export_item(bb, asset, meshes, assets, manifest, base)
    for o in [x for parts in meshes.values() for x in parts.values()]:
        o.hide_set(True)
        o.hide_render = True


# The crafted fashion garments live in their own module; it
# registers its builders in BUILDERS on import (after everything above is
# defined, since it builds on these helpers).
from . import gear_fashion  # noqa: E402

LONG_BODY |= gear_fashion.LONG_BODY
