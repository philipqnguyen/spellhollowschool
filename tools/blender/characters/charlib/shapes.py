"""Garment shapes: a small set of fitted base meshes per slot
(docs/art/garment-shapes.md, data/items/garment_shapes.json).

A shape owns its own topology (a grid in its chart space: columns round the
trunk or a limb, rows placed at measured landmarks), and is fitted to a body
through `fit.BodyFrame` charts only. What it hands to a design:

* the fitted faces, each with shape coordinates (`Garment.coords`: part,
  row/col or ring/col, azimuth `u`, front-ness, level...), which design zones
  and patterns paint by;
* named loops (hem, cuffs, neckline, waist, collar...) that trims grow from;
* named anchors (chest.L, crown, instep.L...) and lines (centre_front) that
  add-ons attach to;
* `hides` (the body regions it covers) and its LOD budget.

Shape options (neckline, sleeve length, shoe cut...) are part of the shape:
every option is fitted by the same code, so a design choosing one never
needs fitting.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import fit as F
from .fit import ALL, ARMS, FEET, HEAD, LEGS, TRUNK, Anchor, BodyFrame, Garment, Loop, SurfacePoint, horiz, unit


@dataclass
class FittedShape:
    name: str
    g: Garment
    params: dict
    loops: dict[str, Loop] = field(default_factory=dict)
    anchors: dict[str, Anchor] = field(default_factory=dict)
    lines: dict[str, list[int]] = field(default_factory=dict)
    hides: list[str] = field(default_factory=list)
    shape_faces: list[int] = field(default_factory=list)
    levels: dict[str, int] = field(default_factory=dict)  # named trunk levels -> row index
    stats: dict = field(default_factory=dict)
    # Guides: fitted rings that are not part of the mesh, keyed by the loop they
    # belong to ({"h": metres above the loop, "pos": {shape: (n, 3) array}}).
    # Trims that rise above a loop (waistbands) follow them, so they hug the
    # body in every build without reading the body themselves.
    guides: dict = field(default_factory=dict)
    # The hanging skirt's rings (rings[0] is the fitted loop it hangs from),
    # as vertex ids: the sitting drape (charlib/drape.py) moves them.
    hanging: list = field(default_factory=list)

    def clone(self) -> "FittedShape":
        """An independent copy for one design to decorate (trims and add-ons
        append to the garment), sharing the body frame. Lets a build fit each
        (shape, options) once per body however many designs use it."""
        import copy

        g = copy.copy(self.g)
        g.pos = {s: [self.g.positions(s).copy()] for s in self.g.shapes}
        g.weights = [dict(w) for w in self.g.weights]
        g.faces, g.swatch, g.mask = list(self.g.faces), list(self.g.swatch), list(self.g.mask)
        g.seed_faces = set(self.g.seed_faces)
        g.coords = {k: dict(v) for k, v in self.g.coords.items()}
        g.vcoords = {k: dict(v) for k, v in self.g.vcoords.items()}
        g.surface = dict(self.g.surface)
        return FittedShape(self.name, g, self.params, loops=copy.deepcopy(self.loops), anchors=copy.deepcopy(self.anchors),
                           lines=copy.deepcopy(self.lines), hides=list(self.hides), shape_faces=list(self.shape_faces),
                           levels=dict(self.levels), stats=copy.deepcopy(self.stats), guides=copy.deepcopy(self.guides),
                           hanging=[list(r) for r in self.hanging])


def _bridge_ring(g: Garment, ids, sag: float = 0.004, spike: float = 0.015, window: int = 2) -> int:
    """Cloth bridges concave skin instead of following it: on a trunk row
    (horizontal, round the centre line), a vertex lying more than `sag`
    inside the running median radius of its neighbours is lifted to it (the
    gluteal cleft, the spine groove, between the breasts), and one more than
    `spike` outside is pulled in (a ray that slipped into a gap). Radii are
    horizontal from the torso centre line, per build shape, so the result is
    body-agnostic: a body with a modelled cleft (MakeHuman) and one without
    get the same taut garment. Rows with an opening (ids of -1, an open
    front) are bridged per contiguous arc. Returns vertices moved."""
    fr = g.frame
    ids = np.asarray(ids, dtype=np.int64)
    n = len(ids)
    have = ids >= 0
    if have.all():
        arcs = [(list(range(n)), True)]
    else:
        # Contiguous runs of present columns, walking round from a gap.
        start = int(np.argmin(have))
        arcs, cur = [], []
        for k in range(1, n + 1):
            i = (start + k) % n
            if have[i]:
                cur.append(i)
            elif cur:
                arcs.append((cur, False))
                cur = []
        if cur:
            arcs.append((cur, False))
    moved = 0
    for cols, closed in arcs:
        m = len(cols)
        if m < 2 * window + 3:
            continue
        sel = ids[cols]
        for s in g.shapes:
            P = g.positions(s)
            q = P[sel]
            c = fr.trunk_origin(float(q[:, 2].mean()))
            d = q - c
            d[:, 2] = 0.0
            r = np.linalg.norm(d, axis=1)
            if closed:
                nb = [[(i + k) % m for k in range(-window, window + 1) if k] for i in range(m)]
            else:
                nb = [[j for j in range(max(0, i - window), min(m, i + window + 1)) if j != i] for i in range(m)]
            med = np.array([np.median(r[b]) for b in nb])
            target = np.clip(r, med - sag, med + spike)
            fix = np.abs(target - r) > 1e-6
            if fix.any():
                q[fix] = c + d[fix] / np.maximum(r[fix], 1e-9)[:, None] * target[fix][:, None]
                q[fix, 2] = P[sel[fix], 2]
                P[sel] = q
                g.pos[s] = [P]
                moved += int(fix.sum())
    return moved


def _smooth_trunk(g: Garment, vid: np.ndarray, rows: int, offsets: dict[int, float], spec: dict, dome_from: int) -> dict:
    """Rounds the trunk grid (the clothing pass, 2026-11-02: tops showed sharp
    points at the bust and the small of the back in side view). Cloth drapes
    over the body's convex points and bridges the hollows between them; the
    grid followed the skin row by row, so the row through the bust's apex
    turned a corner there and the rows below cut straight back to the waist.

    Each vertex of the first `rows` rows moves only along its outward
    direction (horizontal from the torso centre line on the trunk rows, from
    the dome centre over the shoulders), by Taubin smoothing of the grid
    (columns wrap, rows above and below; missing vertices of arm holes and
    openings are skipped), so the surface keeps its size while its corners
    round off; then `floor_passes` of plain smoothing that may only lift a
    vertex (the hollows under the bust and over the small of the back fill
    in the way cloth spans them). No vertex goes further in than `keep` of its
    fitted offset (the cage's clearance pass runs afterwards anyway).
    Returns stats (largest inward and outward moves, mm)."""
    fr = g.frame
    lam, mu = spec.get("lambda", 0.5), spec.get("mu", -0.53)
    passes, floor_passes = int(spec.get("passes", 6)), int(spec.get("floor_passes", 6))
    keep = float(spec.get("keep", 0.6))
    nr, nc = vid.shape
    rows = min(rows, nr)
    ids, nbs = [], []
    index = {}
    for r in range(rows):
        for c in range(nc):
            if vid[r, c] >= 0:
                index[(r, c)] = len(ids)
                ids.append(int(vid[r, c]))
    for r in range(rows):
        for c in range(nc):
            if (r, c) not in index:
                continue
            nb = [index[k] for k in ((r, (c - 1) % nc), (r, (c + 1) % nc), (r - 1, c), (r + 1, c)) if k in index]
            nbs.append(nb)
    if not ids:
        return {}
    ids_a = np.asarray(ids, dtype=np.int64)
    floor_in = np.array([keep * offsets.get(i, 0.0) for i in ids])  # how far in a vertex may go
    out_max, in_max = 0.0, 0.0
    for s in g.shapes:
        P = g.positions(s)
        Q = P[ids_a].copy()
        # Outward directions: horizontal from the torso centre line on the
        # trunk rows, from the dome centre on the rows over the shoulders.
        dome = np.array([r >= dome_from for r, _ in sorted(index, key=index.get)])
        O = np.array([fr.dome_centre() if d else fr.trunk_origin(float(q[2])) for q, d in zip(Q, dome)])
        U = Q - O
        U[~dome, 2] = 0.0
        U /= np.maximum(np.linalg.norm(U, axis=1), 1e-9)[:, None]
        t = np.zeros(len(ids))  # signed move along U

        def lap(t):
            X = Q + U * t[:, None]
            L = np.zeros(len(ids))
            for i, nb in enumerate(nbs):
                if nb:
                    L[i] = float((X[nb].mean(axis=0) - X[i]) @ U[i])
            return L

        for _ in range(passes):
            t = np.maximum(t + lam * lap(t), -floor_in)
            t = np.maximum(t + mu * lap(t), -floor_in)
        for _ in range(floor_passes):
            t = np.maximum(t, t + 0.5 * lap(t))
        P[ids_a] = Q + U * t[:, None]
        g.pos[s] = [P]
        out_max, in_max = max(out_max, float(t.max())), max(in_max, float(-t.min()))
    return {"out_mm": round(out_max * 1000, 1), "in_mm": round(in_max * 1000, 1), "verts": len(ids)}


def _interp_landmarks(fr: BodyFrame, table, z: float) -> float:
    """Piecewise-linear value over landmark heights: [[landmark, value], ...]."""
    if isinstance(table, (int, float)):
        return float(table)
    pts = sorted((fr.landmark(n), float(v)) for n, v in table)
    return float(np.interp(z, [p[0] for p in pts], [p[1] for p in pts]))


def _signed(theta: float) -> float:
    return theta if theta <= 180.0 else theta - 360.0


def _over_base(fr: BodyFrame, base: np.ndarray, th: float, th_base: float, z_ref: float) -> float:
    """The azimuth a neck row (a collar stand, a turtleneck) casts at from
    the neck's centre line so that it rises over its own neckline vertex
    `base`: the azimuth of `base` about the centre line (at `z_ref`, the
    neckline's middle height, or `base`'s own if higher), plus whatever the
    row's own azimuth `th` adds to the neckline's `th_base` (an opening's
    edge narrowing up the stand).

    The neckline is cast from the dome centre in front of the neck and the
    stand from the neck's centre line behind it (25-60 mm on the wrapped
    female), so at the same column azimuth the stand's first row sat 20-25
    degrees further back round the neck than the neckline vertex under it:
    the faces between them twisted over and showed the shirt's inside at
    both sides of the neck (the ruffled silk blouse, 2026-11-04)."""
    c = fr.neck_centre(max(float(base[2]), z_ref))
    v = np.asarray(base[:2], dtype=np.float64) - c[:2]
    if float(np.hypot(v[0], v[1])) < 1e-4:
        return th
    az = math.degrees(math.atan2(v[0], -v[1]))
    return (az + _signed((th - th_base) % 360.0)) % 360.0


def _smooth_angles(a0: np.ndarray, k: int, ramp: int = 3) -> np.ndarray:
    """Angles of ring k: ring 0's (unwrapped, possibly uneven) angles relaxed toward even spacing."""
    n = len(a0)
    a = np.unwrap(a0)
    sign = 1.0 if a[-1] > a[0] else -1.0
    even = sign * 2 * np.pi * np.arange(n) / n
    off = math.atan2(np.mean(np.sin(a - even)), np.mean(np.cos(a - even)))
    even = even + off
    # Match the branch of ring 0.
    even = even + 2 * np.pi * np.round((a - even) / (2 * np.pi))
    t = min(1.0, k / max(ramp, 1))
    return a * (1 - t) + even * t


def _ring_angles(points: np.ndarray, ring) -> np.ndarray:
    d = points - ring.centre
    d = d - np.outer(d @ ring.tangent, ring.tangent)
    return np.arctan2(d @ ring.binormal, d @ ring.normal)


def _dir(ring, a: float) -> np.ndarray:
    return unit(math.cos(a) * ring.normal + math.sin(a) * ring.binormal)


# ----------------------------------------------------------------------------
# Tops: trunk grid with arm holes, sleeves grown from the holes
# ----------------------------------------------------------------------------


def _snap_edge(th: float, w: float, dth: float) -> float:
    """The azimuth of an opening's edge vertex (the first kept column of a
    row, at `th` degrees off the centre line) moved onto the opening's own
    half-angle `w`, by less than a column either way. Moving it only inward
    (when the column lay outside the opening) left every column that lay
    inside it a fraction of a column short, so a V widening by a third of a
    column per row zigzagged every row (the chunky cardigan, the fur hood's
    face, 2026-11-11)."""
    return float(np.clip(w, th - 0.95 * dth, th + 0.45 * dth))


def _top_levels(fr: BodyFrame, p: dict):
    """Rows of the trunk grid (bottom to top) as per-column ray levels."""
    sc = fr.sc
    hem = p["hem"]
    hem_z = fr.landmark(hem["at"]) + hem.get("dz", 0.0) * sc
    anchors = [("hem", hem_z, p["rows"][0][1])]
    for name, n in p["rows"][1:]:
        z = fr.landmark(name)
        if z > anchors[-1][1] + 0.01 * sc:
            anchors.append((name, z, n))
        elif name == p["rows"][-1][0] and len(anchors) > 1:
            # The last landmark (the armpit) always closes the list: drop the one below it.
            lost = anchors.pop()
            anchors[-1] = (anchors[-1][0], anchors[-1][1], anchors[-1][2] + lost[2])
            anchors.append((name, z, n))
    zs, names = [], {}
    for (na, za, n), (nb, zb, _) in zip(anchors, anchors[1:]):
        names[na] = len(zs)
        zs.extend(np.linspace(za, zb, n + 1)[:-1].tolist())
    names[anchors[-1][0]] = len(zs)
    zs.append(anchors[-1][1])
    # A landmark too close to its neighbour was merged: point its name at the row above.
    for name, _ in p["rows"]:
        if name not in names:
            later = [names[n] for n, _ in p["rows"] if n in names and names[n] > 0]
            names[name] = later[-1] if later else 0
    levels = [[("h", z)] * p["cols"] for z in zs]
    # Dome rows over the shoulders, starting at the armpit.
    el_top = max(fr.arm_hole_top(1), fr.arm_hole_top(-1)) + p.get("hole_margin", 4.0)
    names["hole_bottom"] = len(levels) - 1
    for e in np.linspace(0.0, el_top, p["hole_rows"] + 1)[1:]:
        levels.append([("d", float(e))] * p["cols"])
    names["hole_top"] = len(levels) - 1
    nc = p["cols"]
    thetas = [c * 360.0 / nc for c in range(nc)]
    el_neck = [fr.neckline_elevation(th) - p.get("neck_drop", 2.0) for th in thetas]
    ny = p["yoke_rows"]
    for k in range(1, ny + 1):
        levels.append([("d", float(el_top + (max(en, el_top + 6.0) - el_top) * k / ny)) for en in el_neck])
    names["neckline"] = len(levels) - 1
    return levels, names


def _trunk_fit(fr: BodyFrame, origin, direction, regions):
    """A trunk chart sample that never lands on an arm. A ray that misses the
    trunk regions leaves the body through the shoulder junction (behind the
    arm hole on the MakeHuman bodies the dome rays at 105-120 degrees do):
    falling back to any region put those vertices on the back of the upper
    arm, 3-4 cm off the trunk and weighted to the arm, and the shirt grew flat
    flaps behind the shoulder that swung with the arm (the reported blouse,
    2026-11-11). Such a ray passes through the opening the arm fills; it takes
    the trunk point closest to the ray on its way out to the arm's skin (the
    rim of that opening, where the ray crosses it) instead."""
    sp = fr.ray(origin, direction, regions)
    if sp is not None:
        return sp
    hit = fr.ray(origin, direction, ALL)
    if hit is None:
        return fr.nearest(np.asarray(origin) + unit(direction) * 0.1, regions)
    if str(fr.tri_region[hit.tri]) not in ARMS:
        return hit
    from mathutils import Vector

    o, d = np.asarray(origin, dtype=np.float64), unit(direction)
    t_out = float((fr.point(hit, off=0.0) - o) @ d)
    tree, ids = fr.bvh(regions)
    best = None
    for t in np.linspace(0.0, t_out, max(8, int(t_out / 0.003))):
        loc, _n, idx, dist = tree.find_nearest(Vector(o + d * t))
        # Ties (within a millimetre) go to the outermost sample.
        if loc is not None and (best is None or dist <= best[0] + 0.001):
            best = (min(dist, best[0]) if best else dist, ids, idx, loc)
    if best is None:
        return fr.nearest(fr.point(hit, off=0.0), regions)
    return fr._sp(best[1], best[2], best[3])


def _hole_cols(fr: BodyFrame, levels, r_hole: int, hc: int, hr: int, nc: int) -> dict[int, int]:
    """First column of each arm hole: centred on the columns whose trunk rays
    leave through the arm (rows of the hole), not on the side meridian. On
    both bodies the shoulder junction sits behind the side (its rays miss the
    trunk at 81-117 degrees), so a hole centred on 90 degrees left a column
    of trunk vertices behind it with nothing under them. Measured on the left
    side and mirrored, so the two holes stay symmetric; at most two columns
    from the side meridian."""
    dth = 360.0 / nc
    base = int(round(90.0 / dth - hc / 2))
    miss = []
    for c in range(base - 3, base + hc + 4):
        for r in range(r_hole, min(r_hole + hr + 1, len(levels))):
            lv = levels[r][c % nc]
            o, d = fr.trunk_ray((c % nc) * dth, lv)
            if fr.ray(o, d, TRUNK if lv[0] == "h" else ("torso", "neck")) is None:
                miss.append(c)
                break
    c0 = base
    if miss:
        c0 = int(round(0.5 * (min(miss) + max(miss)) - hc / 2))
        c0 = max(base - 2, min(base + 2, c0))
    return {1: c0, -1: (nc - c0 - hc) % nc}


def build_top(fr: BodyFrame, p: dict, name: str = "top") -> FittedShape:
    g = Garment(fr)
    sc = fr.sc
    nc = p["cols"]
    dth = 360.0 / nc
    levels, names = _top_levels(fr, p)
    neck_rows = p.get("neck_rows", 0) if p.get("neckline", "crew") in ("stand", "open") else 0
    if p.get("neckline") == "turtle":
        # A turtleneck: a tall knitted tube up the neck (folded over by a trim).
        neck_rows = p.get("turtle_rows", 4)
    # Opening (V-neck, open collar, open front): half-angle per row.
    op = p.get("openings", {}).get(p.get("neckline", "crew"))
    if p.get("front") == "open":
        op = p["openings"]["front_open"]
    R = len(levels) + neck_rows
    w = _control_widths(names, op["control"] if op else None, R, bool(op and op.get("to_hem")))
    # Arm holes (left: centred on +90 degrees, right on -90).
    hc, hr = p["hole_cols"], p["hole_rows"]
    r_hole = names["hole_bottom"]
    hole_c0 = _hole_cols(fr, levels, r_hole, hc, hr, nc)

    def in_hole(r, c):
        return any(r_hole <= r < r_hole + hr and hole_c0[s] <= c < hole_c0[s] + hc for s in (1, -1))

    def face_theta(c):
        return _signed(((c + 0.5) * dth) % 360.0)

    def opened(r, c):
        if not op:
            return False
        return abs(face_theta(c)) < min(w[r], w[min(r + 1, R - 1)])

    keep = [[not in_hole(r, c) and not opened(r, c) for c in range(nc)] for r in range(R - 1)]
    used = np.zeros((R, nc), dtype=bool)
    for r in range(R - 1):
        for c in range(nc):
            if keep[r][c]:
                for rr, cc in ((r, c), (r, (c + 1) % nc), (r + 1, (c + 1) % nc), (r + 1, c)):
                    used[rr, cc] = True
    # Vertex azimuths, snapped onto the opening edge.
    TH = np.array([[c * dth for c in range(nc)] for _ in range(R)])
    edge = {1: {}, -1: {}}
    if op:
        for r in range(R):
            if w[r] <= 0:
                continue
            for side in (1, -1):
                cols = range(1, nc // 2 + 1) if side > 0 else range(nc - 1, nc // 2 - 1, -1)
                cb = next((c for c in cols if used[r, c]), None)
                if cb is None or used[r, (cb - side) % nc]:
                    continue
                th = _snap_edge(abs(_signed(cb * dth)), w[r], dth)
                TH[r, cb] = (side * th) % 360.0
                edge[side][r] = cb
    # Fit the grid vertices.
    vid = -np.ones((R, nc), dtype=np.int64)
    off_t = p["offset"]["torso"]
    stand_off = p["offset"].get("neck", 0.006)
    neck_h = p.get("neck_height", 0.025) * sc
    if p.get("neckline") == "turtle":
        stand_off = p.get("turtle_offset", 0.012)
        neck_h = p.get("turtle_height", 0.07) * sc
    z_ref = None
    for r in range(R):
        cols = [c for c in range(nc) if used[r, c]]
        if not cols:
            continue
        sps, coords = [], []
        for c in cols:
            th = TH[r, c]
            if r < len(levels):
                lv = levels[r][c]
                o, d = fr.trunk_ray(th, lv)
                sp = _trunk_fit(fr, o, d, TRUNK if lv[0] == "h" else ("torso", "neck"))
                z = fr.point(sp)[2]
                sp.off = _interp_landmarks(fr, off_t, z)
            else:
                k = r - len(levels) + 1
                rb = len(levels) - 1
                base_id = vid[rb, c]
                z0 = g.positions("base")[base_id][2] if base_id >= 0 else fr.neck_axis[0][2]
                z = z0 + neck_h * k / neck_rows
                th_cast = th
                if base_id >= 0:
                    if z_ref is None:
                        z_ref = float(np.median(g.positions("base")[vid[rb][vid[rb] >= 0]][:, 2]))
                    th_cast = _over_base(fr, g.positions("base")[base_id], th, TH[rb, c], z_ref)
                o, d = fr.trunk_ray(th_cast, ("n", z))
                sp = fr.fit(o, d, ("neck",), fallback=ALL)
                sp.off = stand_off
            sps.append(sp)
            coords.append({"part": "torso", "row": r, "col": c, "u": _signed(th)})
        ids = g.add_surface(sps, coords)
        for c, i in zip(cols, ids):
            vid[r, c] = i
    fs = FittedShape(name, g, p, levels=dict(names))
    fs.levels["stand_top"] = R - 1
    fs.stats["bridged"] = sum(_bridge_ring(g, vid[r]) for r in range(names["hole_bottom"] + 1))
    if p.get("smooth"):
        offs = {int(v): float(g.surface[int(v)].off) for v in vid[: len(levels)].ravel() if v >= 0}
        fs.stats["smoothed"] = _smooth_trunk(g, vid, len(levels), offs, p["smooth"], names["hole_bottom"] + 1)
    # Faces.
    for r in range(R - 1):
        for c in range(nc):
            if not keep[r][c]:
                continue
            c1 = (c + 1) % nc
            ids = [vid[r, c], vid[r, c1], vid[r + 1, c1], vid[r + 1, c]]
            P = g.positions("base")[ids]
            cen = P.mean(axis=0)
            outward = cen - fr.trunk_origin(cen[2])
            outward[2] = 0.0
            th = face_theta(c)
            fi = g.shape_face(ids, {"part": "torso", "row": r, "col": c, "u": th, "front": math.cos(math.radians(th)),
                                    "z": float(cen[2]), "neck": r >= len(levels) - 1}, outward)
            fs.shape_faces.append(fi)
    if p.get("insert"):
        w_par = w
        parent = "torso"
        for spec in p.get("insert_specs") or [p["insert_spec"]]:
            w_par = _insert(fs, levels, names, w_par, dth, spec)
            part = spec.get("part", "insert")
            fs.stats[f"{part}_nested"] = _nest_inside(fs, part, parent, p.get("nest_gap", 0.002) * sc)
            parent = part
    # Sleeves.
    hides = ["torso"]
    sleeve = p.get("sleeve", "wrist")
    for side in (1, -1):
        c0 = hole_c0[side]
        ring0 = ([vid[r_hole, c] for c in range(c0, c0 + hc)] + [vid[r, c0 + hc] for r in range(r_hole, r_hole + hr)] +
                 [vid[r_hole + hr, c] for c in range(c0 + hc, c0, -1)] + [vid[r, c0] for r in range(r_hole + hr, r_hole, -1)])
        ring0 = [int(i) for i in ring0]
        fs.loops[f"armhole.{F.SIDES[side]}"] = Loop(ring0, True, "limb", side)
        cuff = _sleeve(fs, side, ring0, "elbow" if sleeve == "bell" else sleeve)
        fs.stats[f"armhole_weights.{F.SIDES[side]}"] = _seam_weights(fs, ring0, p.get("seam_depth", 3), p.get("seam_passes", 6))
        if sleeve == "bell":
            fs.loops[f"bell_root.{F.SIDES[side]}"] = Loop(cuff, True, "limb", side)
            cuff = _bell(fs, side, cuff)
        fs.loops[f"cuff.{F.SIDES[side]}"] = Loop(cuff, True, "limb", side)
        if p.get("under_sleeve") and sleeve in ("bell", "elbow", "short"):
            fs.loops[f"under_cuff.{F.SIDES[side]}"] = Loop(_under_sleeve(fs, side), True, "limb", side)
    if sleeve in ("wrist", "elbow", "long", "bell"):
        hides.append("upper_arms")
    if sleeve in ("wrist", "long") or (p.get("under_sleeve") and sleeve in ("bell", "elbow")):
        hides.append("forearms")
    fs.hides = hides
    # Loops, lines, anchors.
    hem = [int(vid[0, c]) for c in range(nc) if vid[0, c] >= 0]
    fs.loops["hem"] = Loop(hem if not op or w[0] <= 0 else _open_arc(vid[0], used[0]), not (op and w[0] > 0), "down")
    top = R - 1
    if op and w[top] > 0:
        fs.loops["neckline"] = Loop(_open_arc(vid[top], used[top]), False, "up")
    else:
        fs.loops["neckline"] = Loop([int(vid[top, c]) for c in range(nc)], True, "up")
    for side in (1, -1):
        rows = sorted(edge[side])
        if rows:
            fs.lines[f"front_edge.{F.SIDES[side]}"] = [int(vid[r, edge[side][r]]) for r in reversed(rows)]
    # Centre front: col 0 from the top (or the bottom of the opening) to the waist.
    waist_r = names.get("waist", 0)
    cf = [int(vid[r, 0]) for r in range(names["neckline"], waist_r - 1, -1) if vid[r, 0] >= 0]
    fs.lines["centre_front"] = cf
    fs.lines["centre_front_full"] = [int(vid[r, 0]) for r in range(top, -1, -1) if vid[r, 0] >= 0]
    chest_r = min(names.get("chest", r_hole) + 1, r_hole)
    for side in (1, -1):
        c = int(round((56.0 if side > 0 else 304.0) / dth)) % nc
        if vid[chest_r, c] >= 0:
            fs.anchors[f"chest.{F.SIDES[side]}"] = Anchor(int(vid[chest_r, c]))
    if vid[top, nc // 2] >= 0:
        fs.anchors["back_neck"] = Anchor(int(vid[top, nc // 2]))
    fs.stats.update({"rows": R, "cols": nc, "levels": dict(names)})
    # Cage guarantee for every fitted vertex.
    fs.stats["clearance_moves"] = F.enforce_clearance(g, range(g.n), p.get("min_clear", 0.004))
    fs.stats["face_clearance_moves"] = F.enforce_face_clearance(g, fs.shape_faces, p.get("min_clear", 0.004))
    if p.get("under_sleeve") and sleeve in ("bell", "elbow", "short"):
        # The under-sleeve stays inside the outer sleeve (as an insert inside
        # the torso): where the bell's root ring runs along the forearm its
        # chords come closer to the arm than the under-sleeve's 10 mm, and
        # the shirt cuff showed through the bell in white triangles (the
        # house robes, 2026-11-11). After the clearance passes, which push
        # each layer out on its own; the gap leaves room for the bell's
        # 4 mm lining.
        for parent in ("bell", "sleeve"):
            fs.stats[f"under_sleeve_nested_{parent}"] = _nest_inside(fs, "under_sleeve", parent, p.get("under_sleeve_gap", 0.006) * sc)
    if p.get("insert"):
        # Again after the clearance passes, which push each layer on its own.
        parent = "torso"
        for spec in p.get("insert_specs") or [p["insert_spec"]]:
            part = spec.get("part", "insert")
            fs.stats[f"{part}_nested"] += _nest_inside(fs, part, parent, p.get("nest_gap", 0.002) * sc)
            parent = part
    if p.get("skirt"):
        sk = dict(p["skirt"])
        if p.get("length") in p.get("lengths", {}):
            sk.update(p["lengths"][p["length"]])
        _skirt(fs, sk)
    return fs


def _height_of(fr: BodyFrame, spec) -> float:
    """A height from landmarks: [landmark_a, landmark_b, t] (t = 0 at a, 1 at
    b), or a plain number (metres on the 1.78 m reference, scaled by height)."""
    if isinstance(spec, (int, float)):
        return float(spec) * fr.sc
    a, b, t = spec
    za, zb = fr.landmark(a), fr.landmark(b)
    return za + (zb - za) * float(t)


def _guide_ring(fs: FittedShape, nc: int, z: float, h: float, off_table, regions=("hips", "torso")) -> dict:
    """A fitted guide ring `h` above a trunk loop at height `z` (same
    columns and offset table as the loop), for fs.guides."""
    fr, g = fs.g.frame, fs.g
    dth = 360.0 / nc
    sps = []
    for c in range(nc):
        o, d = fr.trunk_ray(c * dth, ("h", float(z + h)))
        sp = fr.fit(o, d, regions, fallback=ALL)
        sp.off = _interp_landmarks(fr, off_table, fr.point(sp)[2])
        sps.append(sp)
    return {"h": float(h), "pos": {s: np.array([fr.point(sp, s) for sp in sps]) for s in g.shapes}}


def _skirt(fs: FittedShape, spec: dict) -> None:
    """A skirt hanging from the hem loop (coats and robes). It is not fitted
    to the legs: it hangs from the fitted hip loop, flares, and is weighted
    from the spine toward the thighs so the front swings with the stride."""
    from . import gear as G

    g, sc = fs.g, fs.g.frame.sc
    loop = fs.loops["hem"]
    n = spec.get("rings", 9)
    ids = list(loop.ids)
    n0 = len(g.faces)
    rings = G.hanging_skirt(g, None, ids, _height_of(fs.g.frame, spec["hem"]), spec.get("flare", 0.13) * sc, open_front=spec.get("open_front", 0.0),
                            pleats=spec.get("pleats", 0.0), n=n, follow=spec.get("follow", 0.75), closed=loop.closed)
    cols = len(ids) if loop.closed else len(ids) - 1
    for i, fi in enumerate(range(n0, len(g.faces))):
        k, c = divmod(i, cols)
        g.coords[fi] = {"part": "skirt", "ring": k + 1, "col": c}
        fs.shape_faces.append(fi)
    fs.stats["skirt_weighted"] = _skirt_weights(fs, rings, spec.get("follow", 0.75), spec.get("back_follow", 0.0),
                                                spec.get("lateral_sharp", 1.0), spec.get("shin_follow", 0.0), spec.get("front_follow", 0.0),
                                                float(spec.get("shell", 0.0)), loop.closed, int(spec.get("smooth", SKIRT_SMOOTH_PASSES)))
    if spec.get("envelope"):
        fs.stats["envelope_moves"] = _skirt_envelope(fs, rings, spec["envelope"])
    if spec.get("body_clear"):
        fs.stats["body_clear_moves"] = _skirt_body_clear(fs, rings, float(spec["body_clear"]) * sc)
    fs.loops["skirt_hem"] = Loop([int(v) for v in rings[-1]], loop.closed, "down")
    fs.hanging = [[int(v) for v in r] for r in rings]
    fs.stats["skirt_faces"] = len(g.faces) - n0


SHELL_REGIONS = ("thighs", "hips", "calves")
# The midline band of a `shell` skirt, in hip-joint half widths: the weights
# blend from the left-right average on the midline to the nearer leg's at
# this distance out (`_skirt_weights`).
MIDLINE_BAND = 0.6


def _mirror_weights(w: dict[str, float]) -> dict[str, float]:
    """The same weights on the other side's bones (.L <-> .R)."""
    def swap(n: str) -> str:
        return re.sub(r"\.(L|R)(?=$|\.)", lambda m: ".R" if m.group(1) == "L" else ".L", n)
    return {swap(n): v for n, v in w.items()}


def _skirt_weights(fs: FittedShape, rings, follow: float, back_follow: float = 0.0, lateral_sharp: float = 1.0,
                   shin_follow: float = 0.0, front_follow: float = 0.0, shell: float = 0.0, closed: bool = True,
                   smooth: int = 0) -> int:
    """Weights of a hanging skirt (rings[0] is the fitted loop it hangs from).
    Ring k blends from the loop vertex above it toward the thighs on its side
    (by lateral position): t = (k / n) ** 0.8 * follow, as gear.skirt_weights,
    but starting from the loop's own fitted weights instead of the spine
    alone. The loop carries the weights of the body under it, as do the
    layers worn beneath (trouser seat, tights, a top's hem), so the skirt's
    upper rings move with them however the body is weighted (MakeHuman's
    seat follows the thighs much more than the sculpted body's). The lateral
    scale is relative to the hip joints, not metres.

    `back_follow` lets the back of the skirt follow the thighs more (t times
    1 + back_follow * back-ness ** 1.5) and `lateral_sharp` splits it
    between the two thighs more sharply: a long stride (the wrapped female's
    long legs) swings the trailing thigh back through a skirt whose back
    columns average both thighs. `shin_follow` hands the rings below the
    knee over to the shins (up to that share at the hem), so a seated robe
    falls from the knees along the shins instead of sticking out level with
    the thighs, and the hem follows the trailing calf.

    `shell` (the stretch skirts, 2026-10-31; skirts, the kilt, robes
    and long coats; true = 1): that share of every hanging vertex's weights
    is instead the weights of the skin under it (the nearest thigh, hip or
    calf), so the cloth moves with the legs and keeps its distance from them.
    Within `MIDLINE_BAND` of the centre front and back the weights blend
    toward their left-right average (half and half on the midline), so the
    cloth there stretches between the thighs instead of splitting. Each side's front, back and
    outer panels follow their own leg; where the two legs' sides meet (the
    centre front and back) the cloth stretches open between the legs like
    elastic when one leg goes forward and the other back, instead of the
    lifted thigh breaking through a skirt that stays with the hips. The
    waistband, the yoke and the trunk above the hanging loop are unchanged;
    the other parameters then only shape the hanging skirt, not its weights."""
    g, fr = fs.g, fs.g.frame
    n = len(rings) - 1
    if n < 1:
        return 0
    ring0 = [int(v) for v in rings[0]]
    w0 = [dict(g.weights[v]) for v in ring0]
    x0 = g.positions("base")[ring0][:, 0]
    lat_w = 1.36 * max(abs(float(fr.hip_joint[1][0])), 0.05)
    y0 = g.positions("base")[ring0][:, 1]
    knee_z = fr.landmark("knee")
    hem_z = float(g.positions("base")[[int(v) for v in rings[-1]]][:, 2].mean())
    yc = 0.5 * (float(y0.min()) + float(y0.max()))
    half_d = max(0.5 * (float(y0.max()) - float(y0.min())), 1e-3)
    count = 0
    for k, ids in enumerate(rings[1:], start=1):
        t0 = (k / n) ** 0.8 * follow
        for c, vid in enumerate(ids):
            back = float(np.clip((y0[c] - yc) / half_d, 0.0, 1.0))  # +Y is behind the character
            front = float(np.clip((yc - y0[c]) / half_d, 0.0, 1.0))
            # front_follow: the front panels over the thighs follow them more
            # (a seated thigh rises through a skirt hanging from the hips).
            t = min(0.95, t0 * (1.0 + back_follow * back ** 1.5 + front_follow * front ** 1.5))
            wl = 0.5 + 0.5 * float(np.clip(lateral_sharp * x0[c] / lat_w, -1.0, 1.0))
            st = 0.0
            if shin_follow > 0.0:
                z = float(g.positions("base")[int(vid)][2])
                st = shin_follow * float(np.clip((knee_z - z) / max(knee_z - hem_z, 1e-3), 0.0, 1.0)) ** 0.7
            legs = {"DEF-thigh.L": wl * 0.7 * (1 - st), "DEF-thigh.L.001": wl * 0.3 * (1 - st), "DEF-shin.L": wl * st,
                    "DEF-thigh.R": (1 - wl) * 0.7 * (1 - st), "DEF-thigh.R.001": (1 - wl) * 0.3 * (1 - st), "DEF-shin.R": (1 - wl) * st}
            w = F.top_weights(F.blend_weights([(1.0 - t, w0[c]), (t, legs)]))
            if shell:
                # Skinned like the skin under it: the cloth keeps its distance from the legs as they move.
                under = fr.weights(fr.nearest(g.positions("base")[int(vid)], SHELL_REGIONS))
                w = under if shell >= 1.0 else F.top_weights(F.blend_weights([(1.0 - shell, w), (shell, under)]))
                # Across the centre front and back the cloth spans both legs: blend toward the
                # left-right average (all of it on the midline, none over the thigh's centre) so it
                # stretches between the thighs instead of splitting down the middle.
                a = 1.0 - float(np.clip(abs(float(g.positions("base")[int(vid)][0])) / (MIDLINE_BAND * abs(float(fr.hip_joint[1][0]))),
                                        0.0, 1.0))
                if a > 0.0:
                    w = F.top_weights(F.blend_weights([(1.0 - 0.5 * a, w), (0.5 * a, _mirror_weights(w))]))
            g.weights[int(vid)] = w
            count += 1
    if shell and smooth:
        _smooth_grid_weights(g, rings, closed, smooth)
    return count


# Passes of `_smooth_grid_weights` over an outer garment's hanging skirt
# (`_skirt`: robes, cloaks, coats; `skirt.smooth` overrides). The legs-layer
# skirts keep their unsmoothed weights: smoothed, they followed the thighs
# less closely and stood through the top over them in the floor sit.
SKIRT_SMOOTH_PASSES = 2


def _smooth_grid_weights(g, rings, closed: bool, passes: int) -> None:
    """Each hanging vertex (rings[1:]) takes half its own weights and half
    its grid neighbours' mean, `passes` times; the hanging loop (rings[0])
    stays. A shell skirt's weights are those of the skin nearest each
    vertex, and a robe's skirt hangs a hand's width off the legs, where the
    nearest skin jumps between the buttocks, the two thighs and the calves
    from one column or ring to the next: neighbouring vertices then followed
    different bones, the skirt's faces sheared and folded in the walk and
    run, and the lining 4 mm inside showed through in red squares, with
    holes where the shell's faces turned over (the house robes and cloaks,
    2026-11-11)."""
    R = [[int(v) for v in r] for r in rings]
    n, m = len(R), len(R[0])
    if any(len(r) != m for r in R):
        return
    for _ in range(passes):
        new = {}
        for k in range(1, n):
            for c in range(m):
                ns = [R[k - 1][c]]
                if k + 1 < n:
                    ns.append(R[k + 1][c])
                for dc in (-1, 1):
                    cc = c + dc
                    if closed:
                        ns.append(R[k][cc % m])
                    elif 0 <= cc < m:
                        ns.append(R[k][cc])
                v = R[k][c]
                new[v] = F.top_weights(F.blend_weights([(0.5, g.weights[v])] + [(0.5 / len(ns), g.weights[u]) for u in ns]))
        for v, w in new.items():
            g.weights[v] = w


def _skirt_envelope(fs: FittedShape, rings, envelope) -> int:
    """The outer-layer cage for hanging skirts: an outer garment's skirt
    (robe, coat, cloak) must stay outside every legs-layer skirt it can be
    worn over. `envelope` lists [height, metres]: by that height (landmark
    spec, as for hems) each skirt ring must stand at least that far out
    (horizontally) from the hip loop it hangs from; linear in between,
    constant below the last point. Body-agnostic: heights are landmarks,
    distances are relative to the garment's own fitted loop."""
    g, fr = fs.g, fs.g.frame
    pts = [(_height_of(fr, h), float(a) * fr.sc) for h, a in envelope]
    ring0 = np.asarray(rings[0])
    moved = 0
    for s in g.shapes:
        P = g.positions(s)
        r0 = P[ring0]
        c = np.array([0.0, r0[:, 1].mean() * 0.5, 0.0])  # the axis hanging_skirt flares from
        d0 = r0 - c
        d0[:, 2] = 0.0
        rad0 = np.linalg.norm(d0, axis=1)
        z_loop = float(r0[:, 2].mean())
        zs = [z_loop] + [z for z, _ in pts]
        amounts = [0.0] + [a for _, a in pts]
        for ids in rings[1:]:
            q = P[ids]
            d = q - c
            d[:, 2] = 0.0
            rad = np.linalg.norm(d, axis=1)
            need = np.interp(-q[:, 2], [-z for z in zs], amounts)
            short = need - (rad - rad0)
            push = short > 0
            if push.any():
                q[push] += d[push] / np.maximum(rad[push], 1e-9)[:, None] * short[push][:, None]
                P[ids] = q
                moved += int(push.sum())
        g.pos[s] = [P]
    return moved


def _skirt_body_clear(fs: FittedShape, rings, clear: float, regions=("hips", "thighs")) -> int:
    """An outer garment's hanging skirt clears the seat and thighs by `clear`
    (the room a legs-layer skirt's yoke and first rings take there): with the
    robe's hip loop close to the body (the clothing pass, 2026-11-02) the
    rings hanging straight from it cut into the buttocks below the hip line.
    Vertices move horizontally away from the skirt's flare axis, as the
    envelope does. Returns vertex moves."""
    from mathutils import Vector

    g, fr = fs.g, fs.g.frame
    moved = 0
    for s in g.shapes:
        tree, _ = fr.bvh(regions, s)
        P = g.positions(s)
        r0 = P[np.asarray(rings[0])]
        c = np.array([0.0, r0[:, 1].mean() * 0.5, 0.0])
        for ids in rings[1:]:
            for v in ids:
                for _ in range(3):
                    loc, nrm, _, dist = tree.find_nearest(Vector(P[v]), 0.3)
                    if loc is None:
                        break
                    h = float((P[v] - np.array(loc)) @ np.array(nrm))
                    if h >= clear - 1e-4:
                        break
                    d = P[v] - c
                    d[2] = 0.0
                    d /= max(float(np.linalg.norm(d)), 1e-9)
                    P[v] = P[v] + d * (clear - h)
                    moved += 1
        g.pos[s] = [P]
    return moved


def _bell(fs: FittedShape, side: int, root: list[int]) -> list[int]:
    """A bell sleeve hanging open from the elbow loop. Each ring takes the
    weights of the body's arm at the same distance along the arm (from the
    arm chart), so it bends with the elbow and twists with the forearm."""
    g, fr, p = fs.g, fs.g.frame, fs.params
    sc = fr.sc
    spec = p.get("bell", {})
    length, flare, droop, n = spec.get("length", 0.27) * sc, spec.get("flare", 1.25), spec.get("droop", 0.03), spec.get("rings", 6)
    ua = float(np.linalg.norm(fr.elbow[side] - fr.shoulder[side]))
    s0 = ua - 0.01 * sc
    from . import gear as G

    r0 = {s: g.positions(s)[root] for s in g.shapes}
    c0 = r0["base"].mean(axis=0)
    ax = G.gb_axis(r0["base"])
    rings = fr.limb_rings("arm", side, [s0 + length * k / n for k in range(1, n + 1)])
    prev = list(root)
    for k in range(1, n + 1):
        t = k / n
        ring = rings[k - 1]
        pts = {}
        for s in g.shapes:
            q = r0[s]
            c = q.mean(axis=0)
            pp = c + (q - c) * (1.0 + flare * t ** 1.4) + ax * (length * t)
            low = np.clip(-(q - c)[:, 2] / max(np.abs((q - c)[:, 2]).max(), 1e-6), 0, 1)
            pp[:, 2] -= droop * t ** 1.5 * (0.4 + 0.6 * low)
            pts[s] = pp
        ws = []
        for q in r0["base"]:
            d = q - c0
            d = d - ring.tangent * float(d @ ring.tangent)
            sp = fr.fit(ring.centre, d, ARMS, fallback=None, guess=0.04, max_dist=0.15)
            ws.append(fr.weights(sp))
        ids = [int(i) for i in g.add(pts, ws)]
        m = len(ids)
        P = g.positions("base")
        axis_c = P[ids].mean(axis=0)
        for i in range(m):
            f = [prev[i], prev[(i + 1) % m], ids[(i + 1) % m], ids[i]]
            # Wound away from the bell's own axis (a shape face, so `orient`
            # never flips it and the lining always goes inside).
            cen = P[f].mean(axis=0)
            mid = 0.5 * (P[prev].mean(axis=0) + axis_c)
            fi = g.shape_face(f, {"part": "bell", "side": side, "ring": k, "col": i}, cen - mid)
            fs.shape_faces.append(fi)
        prev = ids
    return prev


def _under_sleeve(fs: FittedShape, side: int) -> list[int]:
    """The shirt sleeve under a bell or short sleeve: a fitted forearm tube
    from just inside the outer sleeve to the wrist. Returns its cuff ring."""
    g, fr, p = fs.g, fs.g.frame, fs.params
    sc = fr.sc
    spec = p.get("under_sleeve_spec", {})
    ua = float(np.linalg.norm(fr.elbow[side] - fr.shoulder[side]))
    fa = float(np.linalg.norm(fr.wrist[side] - fr.elbow[side]))
    # It starts 15 mm inside the outer sleeve's end (was 40 mm): its first
    # ring, 5 mm inside the fitted sleeve's offset, showed through the
    # sleeve's chords above the elbow as white patches (the house robes,
    # 2026-11-11).
    s_vals = np.linspace(ua - spec.get("overlap", 0.015) * sc, ua + fa - 0.022 * sc, spec.get("rings", 7))
    rings = fr.limb_rings("arm", side, s_vals)
    m = spec.get("cols", 16)
    prev = None
    for k, (s, ring) in enumerate(zip(s_vals, rings)):
        sps = []
        for i in range(m):
            sp = fr.fit(ring.centre, _dir(ring, 2 * math.pi * i / m), ARMS, fallback=None, guess=0.04, max_dist=0.15)
            sp.off = spec.get("offset", 0.010)
            sps.append(sp)
        ids = [int(v) for v in g.add_surface(sps, [{"part": "under_sleeve", "side": side, "ring": k, "col": i} for i in range(m)])]
        if prev is not None:
            for i in range(m):
                f = [prev[i], prev[(i + 1) % m], ids[(i + 1) % m], ids[i]]
                cen = g.positions("base")[f].mean(axis=0)
                fi = g.shape_face(f, {"part": "under_sleeve", "side": side, "ring": k, "col": i}, cen - ring.centre)
                fs.shape_faces.append(fi)
        prev = ids
    return prev


def _control_widths(names, control, R: int, to_hem: bool = False) -> np.ndarray:
    """Opening half-angle per row from [[level name, row offset, degrees], ...]."""
    w = np.zeros(R)
    if not control:
        return w
    rs = [names[a] + off for a, off, _ in control]
    ws = [float(v) for _, _, v in control]
    for r in range(R):
        if r < rs[0]:
            w[r] = ws[0] if to_hem else 0.0
        elif r > rs[-1]:
            w[r] = ws[-1]
        else:
            w[r] = float(np.interp(r, rs, ws))
    return w


def _insert(fs: FittedShape, levels, names, w_parent, dth, spec: dict) -> np.ndarray:
    """An inner panel seen through an opening (a shirt front under a V-neck,
    a jumper and shirt front under an open robe): the front of the trunk grid
    at a smaller offset, covering the opening above it plus `half_angle`,
    optionally with its own V opening and a collar stand all round the neck.
    Fitted like the rest of the shape. Returns its own opening widths, so
    inserts can nest (robe > jumper > shirt)."""
    g, fr, p = fs.g, fs.g.frame, fs.params
    part = spec.get("part", "insert")
    nc = p["cols"]
    sc = fr.sc
    r_lo = max(0, names[spec["from"][0]] + spec["from"][1])
    r_top = len(levels) - 1
    nn = spec.get("neck_rows", 2)
    R = r_top + 1 + nn
    w_own = _control_widths(names, (spec.get("opening") or {}).get("control"), R)
    wp = np.concatenate([np.asarray(w_parent, float), np.zeros(max(0, R - len(w_parent)))])

    def half(r):
        return max(float(wp[min(r, len(wp) - 1)]) + 1.5 * dth, spec["half_angle"])

    def face_ok(r, c):
        th = abs(_signed(((c + 0.5) * dth) % 360))
        if th < min(w_own[r], w_own[min(r + 1, R - 1)]):
            return False
        if r >= r_top:
            return nn > 0
        return th < min(half(r), half(r + 1))

    used = np.zeros((R, nc), dtype=bool)
    keep = []
    for r in range(r_lo, R - 1):
        for c in range(nc):
            if face_ok(r, c):
                keep.append((r, c))
                for rr, cc in ((r, c), (r, (c + 1) % nc), (r + 1, (c + 1) % nc), (r + 1, c)):
                    used[rr, cc] = True
    TH = np.array([[c * dth for c in range(nc)] for _ in range(R)])
    edge = {1: {}, -1: {}}
    for r in range(R):
        if w_own[r] <= 0:
            continue
        for side in (1, -1):
            cols = range(1, nc // 2 + 1) if side > 0 else range(nc - 1, nc // 2 - 1, -1)
            cb = next((c for c in cols if used[r, c]), None)
            if cb is None or used[r, (cb - side) % nc]:
                continue
            th = _snap_edge(abs(_signed(cb * dth)), w_own[r], dth)
            TH[r, cb] = (side * th) % 360.0
            edge[side][r] = cb
    vid = -np.ones((R, nc), dtype=np.int64)
    off = spec["offset"]
    nh = spec.get("neck_height", 0.025) * sc
    z_ref = None
    for r in range(r_lo, R):
        cols = [c for c in range(nc) if used[r, c]]
        if not cols:
            continue
        sps, coords = [], []
        for c in cols:
            th = TH[r, c]
            if r <= r_top:
                lv = levels[r][c]
                o, d = fr.trunk_ray(th, lv)
                sp = _trunk_fit(fr, o, d, TRUNK if lv[0] == "h" else ("torso", "neck"))
            else:
                base = g.positions("base")[vid[r_top, c]]
                if z_ref is None:
                    z_ref = float(np.median(g.positions("base")[vid[r_top][vid[r_top] >= 0]][:, 2]))
                o, d = fr.trunk_ray(_over_base(fr, base, th, TH[r_top, c], z_ref), ("n", base[2] + nh * (r - r_top) / nn))
                sp = fr.fit(o, d, ("neck",), fallback=ALL)
            # A number, or [[landmark, metres], ...] down the trunk (an open
            # robe's jumper front stands further out at the waist, over the
            # waistband of a skirt or kilt worn under the robe).
            sp.off = _interp_landmarks(fr, off, float(fr.point(sp, off=0.0)[2]))
            sps.append(sp)
            coords.append({"part": part, "row": r, "col": c, "u": _signed(th)})
        for c, i in zip(cols, g.add_surface(sps, coords)):
            vid[r, c] = i
    if p.get("smooth"):
        # Rounded like the shell over it (`_smooth_trunk`), so it stays under it.
        offs = {int(v): float(g.surface[int(v)].off) for v in vid[: r_top + 1].ravel() if v >= 0}
        fs.stats[f"{part}_smoothed"] = _smooth_trunk(g, vid, r_top + 1, offs, p["smooth"], names["hole_bottom"] + 1)
    for (r, c) in keep:
        c1 = (c + 1) % nc
        ids = [vid[r, c], vid[r, c1], vid[r + 1, c1], vid[r + 1, c]]
        cen = g.positions("base")[ids].mean(axis=0)
        outward = cen - fr.trunk_origin(cen[2])
        outward[2] = 0
        th = _signed(((c + 0.5) * dth) % 360)
        fi = g.shape_face(ids, {"part": part, "row": r, "col": c, "u": th, "front": math.cos(math.radians(th)),
                                "z": float(cen[2]), "neck": r >= r_top}, outward)
        fs.shape_faces.append(fi)
    if nn:
        fs.loops[f"{part}_neck"] = Loop([int(vid[R - 1, c]) for c in range(nc)], True, "up")
    fs.lines[f"{part}_centre"] = [int(vid[r, 0]) for r in range(r_top, r_lo - 1, -1) if vid[r, 0] >= 0]
    for side in (1, -1):
        rows = sorted(edge[side])
        if rows:
            fs.lines[f"{part}_edge.{F.SIDES[side]}"] = [int(vid[r, edge[side][r]]) for r in reversed(rows)]
    fs.levels[f"{part}_top"] = R - 1
    return w_own


def _nest_inside(fs: FittedShape, part: str, parent: str, gap: float) -> int:
    """Keeps an insert under the layer it is seen through: every vertex of
    `part` lying under a face of `parent` stays at least `gap` inside it
    (along that face's outward normal). The two grids are fitted and rounded
    (`_smooth_trunk`) on their own: the shirt front, whole across the chest,
    bridged the hollow between the breasts while the jumper over it, open in
    a V there, followed it down, and the shirt showed through the jumper
    either side of the V in white triangles (the house robes and the V-neck
    jumper, 2026-11-11). Vertices beside the parent (in its opening) are
    left alone. Returns vertices moved."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    g = fs.g
    pf = [list(g.faces[f]) for f in fs.shape_faces if g.coords.get(f, {}).get("part") == parent]
    ids = [i for i, c in g.vcoords.items() if c.get("part") == part]
    if not pf or not ids:
        return 0
    moved = 0
    for s in g.shapes:
        P = g.positions(s)
        tree = BVHTree.FromPolygons(P.tolist(), pf)
        for v in ids:
            loc, nrm, _i, dist = tree.find_nearest(Vector(P[v]), 0.04)
            if loc is None:
                continue
            n = np.array(nrm)
            off = P[v] - np.array(loc)
            d = float(off @ n)
            if dist > 2 * gap and abs(d) < 0.5 * dist:
                continue  # beside the parent: seen through its opening
            if d > -gap:
                P[v] = P[v] - n * (d + gap)
                moved += 1
        g.pos[s] = [P]
    return moved


def _seam_weights(fs: FittedShape, loop, depth: int = 2, passes: int = 4) -> int:
    """Weights blended across a seam (the arm hole): every shape vertex within
    `depth` edges of the loop takes, `passes` times, half its own weights and
    half its neighbours' mean. The trunk grid takes the body's weights under
    it and the sleeve the arm's, so the loop's back corner (on the back of the
    shoulder blade, 56-59% upper arm) sat between a row weighted to the spine
    and a sleeve ring weighted to the arm: when the arm swung forward or rose
    (the cast, the run) the faces between them flipped, and the reported
    linen shirt and silk blouse showed a dark diamond or a pale patch on the
    back of the shoulder (2026-11-11). Returns vertices reweighted."""
    g = fs.g
    nb: dict[int, set] = {}
    for fi in fs.shape_faces:
        f = g.faces[fi]
        for i in range(len(f)):
            a, b = int(f[i]), int(f[(i + 1) % len(f)])
            nb.setdefault(a, set()).add(b)
            nb.setdefault(b, set()).add(a)
    zone = {int(v) for v in loop}
    front = set(zone)
    for _ in range(depth):
        front = {n for v in front for n in nb.get(v, ())} - zone
        zone |= front
    zone = sorted(v for v in zone if v in nb)
    # Not over the top of the shoulder: the trunk vertices on the hole's top
    # row and above it (the shoulder top and the trapezius up to the collar)
    # keep the body's weights. Blended toward the sleeve's, they took a share
    # of the upper arm, slid down into the trapezius as the arm rose, and the
    # skin showed through beside the collar in the wave and with the arms up
    # (the linen shirt and the silk blouse). The corners the blend is for lie
    # below that row.
    top = max(int(g.vcoords.get(int(v), {}).get("row", -1)) for v in loop)
    zone = [v for v in zone if g.vcoords.get(v, {}).get("part") == "sleeve" or int(g.vcoords.get(v, {}).get("row", -1)) < top]
    # The seam's shape too: Taubin passes (they keep its size) over the same
    # band unfold the faces between the loop's corners and the first sleeve
    # ring, which start 4-10 cm apart along the arm (`_sleeve`); the
    # clearance passes after the sleeves keep it off the skin.
    zi = np.array(zone, dtype=np.int64)
    nbl = [list(nb[v]) for v in zone]
    for sh in g.shapes:
        P = g.positions(sh)
        for _ in range(passes):
            for lam in (0.5, -0.53):
                avg = np.array([P[ns].mean(axis=0) for ns in nbl])
                P[zi] = P[zi] + lam * (avg - P[zi])
        g.pos[sh] = [P]
    for _ in range(passes):
        new = {}
        for v in zone:
            ns = list(nb[v])
            new[v] = F.top_weights(F.blend_weights([(0.5, g.weights[v])] + [(0.5 / len(ns), g.weights[n]) for n in ns]))
        for v, w in new.items():
            g.weights[v] = w
    return len(zone)


def _open_arc(row_ids, row_used) -> list[int]:
    """Kept vertices of a row as one arc round the back, from one edge of the opening to the other."""
    n = len(row_ids)
    start = next((i for i in range(n) if row_used[i] and not row_used[(i - 1) % n]), 0)
    arc, i = [], start
    while row_used[i % n] and len(arc) < n:
        arc.append(int(row_ids[i % n]))
        i += 1
    return arc


def _sleeve(fs: FittedShape, side: int, ring0: list[int], cut: str) -> list[int]:
    """Rings along the arm from the arm hole loop; returns the cuff ring."""
    g, fr, p = fs.g, fs.g.frame, fs.params
    sc = fr.sc
    ua = float(np.linalg.norm(fr.elbow[side] - fr.shoulder[side]))
    fa = float(np.linalg.norm(fr.wrist[side] - fr.elbow[side]))
    ends = {"wrist": ua + fa - 0.022 * sc, "long": ua + fa - 0.006 * sc, "elbow": ua - 0.01 * sc, "short": 0.45 * ua}
    s_end = ends[cut]
    s1 = p.get("sleeve_start", 0.035) * sc
    n = max(2, int(round(p["sleeve_rings"] * (s_end - s1) / (ua + fa - s1))))
    s_values = np.linspace(s1, s_end, n)
    rings = fr.limb_rings("arm", side, s_values)
    P0 = g.positions("base")[ring0]
    a0 = _ring_angles(P0, rings[0])
    # The arm hole loop is not a cross-section of the arm: its armpit and
    # back corners lie 4-10 cm along the arm (the trunk under the arm), its
    # top 4 cm before the shoulder joint. A first ring at one distance along
    # the arm lay behind the bottom of the loop, and the faces joining them
    # folded back (flat flaps at the front of the armpit and behind the
    # shoulder, every top, 2026-11-11). Each column's first rings start past
    # its own loop vertex (`s0`) by at least `step` per ring, and join the
    # even rings once those are further along.
    ax = unit(fr.elbow[side] - fr.shoulder[side])
    s0 = (P0 - fr.shoulder[side]) @ ax
    step = p.get("sleeve_step", 0.02) * sc
    prev = ring0
    off_tab = p["offset"]["sleeve"]
    for k, (s, ring) in enumerate(zip(s_values, rings), start=1):
        ang = _smooth_angles(a0, k)
        sk = np.maximum(s, s0 + step * k)
        frac = s / (ua + fa)
        off = float(np.interp(frac, [a for a, _ in off_tab], [b for _, b in off_tab])) if not isinstance(off_tab, (int, float)) else off_tab
        sps, coords, cen_i = [], [], []
        for i, a in enumerate(ang):
            ri = ring if sk[i] <= s + 1e-6 else fr.limb_rings("arm", side, [float(sk[i])])[0]
            d = _dir(ri, a)
            sp = fr.fit(ri.centre, d, ARMS, fallback=None, guess=0.05, max_dist=0.15)
            sp.off = off
            sps.append(sp)
            cen_i.append(ri.centre)
            coords.append({"part": "sleeve", "side": side, "ring": k, "col": i})
        ids = [int(i) for i in g.add_surface(sps, coords)]
        m = len(ids)
        for i in range(m):
            i1 = (i + 1) % m
            f = [prev[i], prev[i1], ids[i1], ids[i]]
            cen = g.positions("base")[f].mean(axis=0)
            dvec = _dir(ring, float(ang[i]))
            axis_c = 0.5 * (cen_i[i] + cen_i[i1])
            out = cen - axis_c
            out = out - ax * float(out @ ax)
            fi = g.shape_face(f, {"part": "sleeve", "side": side, "ring": k, "col": i, "s": float(s / sc), "sf": float(frac),
                                  "front": float(-dvec[1]), "up": float(dvec[2])}, out)
            fs.shape_faces.append(fi)
        prev = ids
    return prev


# ----------------------------------------------------------------------------
# Legs: hips grid split at the crotch into two leg tubes
# ----------------------------------------------------------------------------


def build_legs(fr: BodyFrame, p: dict, name: str = "legs") -> FittedShape:
    g = Garment(fr)
    sc = fr.sc
    nc = p["cols"]
    dth = 360.0 / nc
    top = p["top"]
    top_z = fr.landmark(top["at"]) + top.get("dz", 0.0) * sc
    crotch_line = fr.crotch_z + p.get("crotch_lift", 0.012) * sc
    hip_z = min(fr.hip_z, top_z - 0.02 * sc)
    n_upper, n_lower = p["rows"]  # top -> hip, hip -> crotch line
    zs = np.concatenate([np.linspace(crotch_line, hip_z, n_lower + 1)[:-1], np.linspace(hip_z, top_z, n_upper + 1)])
    R = len(zs)
    off_h = p["offset"]["hips"]
    vid = -np.ones((R, nc), dtype=np.int64)
    for r, z in enumerate(zs):
        sps, coords = [], []
        for c in range(nc):
            th = c * dth
            o, d = fr.trunk_ray(th, ("h", float(z)))
            sp = fr.fit(o, d, ("hips", "torso"), fallback=ALL)
            sp.off = _interp_landmarks(fr, off_h, fr.point(sp)[2])
            sps.append(sp)
            coords.append({"part": "hips", "row": r, "col": c, "u": _signed(th)})
        vid[r] = g.add_surface(sps, coords)
    fs = FittedShape(name, g, p, levels={"crotch_line": 0, "hip": n_lower, "top": R - 1})
    fs.stats["bridged"] = sum(_bridge_ring(g, vid[r]) for r in range(R))
    # `covered_from` (a skirt's tights): the rows from the hip line up lie
    # under the skirt's yoke in every pose, so they get no faces (they broke
    # through the yoke's back over the buttocks; the clothing pass).
    covered = p.get("covered_from")
    for r in range(R - 1):
        if covered is not None and r >= {"hip": n_lower}.get(covered, R):
            continue
        for c in range(nc):
            c1 = (c + 1) % nc
            ids = [vid[r, c], vid[r, c1], vid[r + 1, c1], vid[r + 1, c]]
            cen = g.positions("base")[ids].mean(axis=0)
            th = _signed(((c + 0.5) * dth) % 360)
            outward = cen - fr.trunk_origin(cen[2])
            outward[2] = 0
            fi = g.shape_face(ids, {"part": "hips", "row": r, "col": c, "u": th, "front": math.cos(math.radians(th)),
                                    "z": float(cen[2])}, outward)
            fs.shape_faces.append(fi)
    # Crotch vertices: under the crotch, front to back.
    Pb = g.positions("base")
    yf, yb = Pb[vid[0, 0], 1], Pb[vid[0, nc // 2], 1]
    ncr = p.get("crotch_points", 3)
    crotch = []
    sps = []
    for i in range(ncr):
        y = yf + (yb - yf) * (i + 1) / (ncr + 1)
        sp = fr.fit(np.array([0.0, y, crotch_line + 0.06 * sc]), (0, 0, -1), ("hips", "thighs", "torso"), fallback=ALL)
        sp.off = p["offset"]["leg"] if isinstance(p["offset"]["leg"], (int, float)) else p["offset"]["leg"][0][1]
        sps.append(sp)
    crotch = [int(i) for i in g.add_surface(sps, [{"part": "crotch", "col": i} for i in range(ncr)])]
    hides = ["hips", "thighs", "calves"]
    fs.hides = hides
    fs.loops["waist"] = Loop([int(v) for v in vid[R - 1]], True, "up")
    fs.guides["waist"] = _guide_ring(fs, nc, top_z, p.get("band_guide", 0.04) * sc, off_h)
    for side in (1, -1):
        cols = list(range(0, nc // 2 + 1)) if side > 0 else [0] + list(range(nc - 1, nc // 2 - 1, -1))
        ring0 = [int(vid[0, c]) for c in cols] + list(reversed(crotch))
        hem = _leg_tube(fs, side, ring0)
        fs.loops[f"hem.{F.SIDES[side]}"] = Loop(hem, True, "down", side)
    fs.lines["fly"] = [int(vid[r, 0]) for r in range(R - 1, -1, -1)]
    for side in (1, -1):
        c = int(round((60.0 if side > 0 else 300.0) / dth)) % nc
        fs.anchors[f"hip.{F.SIDES[side]}"] = Anchor(int(vid[max(R - 3, 0), c]))
    fs.stats.update({"rows": R, "cols": nc})
    fs.stats["clearance_moves"] = F.enforce_clearance(g, range(g.n), p.get("min_clear", 0.003))
    fs.stats["face_clearance_moves"] = F.enforce_face_clearance(g, fs.shape_faces, p.get("min_clear", 0.003))
    return fs


def _leg_tube(fs: FittedShape, side: int, ring0: list[int]) -> list[int]:
    g, fr, p = fs.g, fs.g.frame, fs.params
    sc = fr.sc
    line = fr.limb_line("leg", side)
    hj = fr.hip_joint[side]
    knee_s = float(np.linalg.norm(fr.knee[side] - hj))
    s1 = float(hj[2] - (fr.crotch_z - 0.035 * sc))
    hem = p["hem"]
    leg_len = line.length
    if hem.get("at", "ankle") == "ankle":
        s_end = _s_at_z(line, 0.5 * (hem.get("back", 0.034) + hem.get("front", 0.09)) * sc)
    else:
        s_end = knee_s + hem.get("ds", 0.0) * sc
    n_thigh = p["leg_rings"][0]
    n_shin = p["leg_rings"][1] if s_end > knee_s + 0.02 else 0
    if n_shin:
        s_vals = np.concatenate([np.linspace(s1, knee_s, n_thigh + 1), np.linspace(knee_s, s_end, n_shin + 1)[1:]])
    else:
        s_vals = np.linspace(s1, s_end, n_thigh + 1)
    rings = fr.limb_rings("leg", side, s_vals)
    a0 = _ring_angles(g.positions("base")[ring0], rings[0])
    prev = ring0
    off_leg = p["offset"]["leg"]
    # `inner_offset` (per body, metres): the leg's offset from the skin on the
    # side facing the other leg (the female's legs pass close on her one-line
    # gait; the second walk and run pass).
    inner_off = p.get("inner_offset", {}).get(fr.id)
    inner_off = None if inner_off is None else float(inner_off)
    knee_k = n_thigh + 1  # index (1-based ring number) of the knee ring
    if p.get("release_ring"):
        # Wide legs: released from this thigh ring (1-based) to the hem.
        knee_k = int(p["release_ring"])
    straight = p.get("straight_below_knee", True) and n_shin > 0
    knee_d = None
    all_rings = []
    for k, (s, ring) in enumerate(zip(s_vals, rings), start=1):
        ang = _smooth_angles(a0, k)
        sps, coords = [], []
        off = off_leg if isinstance(off_leg, (int, float)) else float(np.interp(s / leg_len, *zip(*off_leg)))
        for i, a in enumerate(ang):
            sp = fr.fit(ring.centre, _dir(ring, a), LEGS, fallback=None, guess=0.07, max_dist=0.2)
            sp.off = off
            if inner_off is not None:
                # The side facing the other leg sits closer to the skin (`inner_offset`).
                w = float(np.clip(-_dir(ring, a)[0] * side, 0.0, 1.0)) ** 0.5
                sp.off = off * (1.0 - w) + min(off, inner_off) * w
            sps.append(sp)
            coords.append({"part": "leg", "side": side, "ring": k, "col": i})
        ids = [int(i) for i in g.add_surface(sps, coords)]
        all_rings.append((k, s, ring, ang, ids))
        m = len(ids)
        for i in range(m):
            i1 = (i + 1) % m
            f = [prev[i], prev[i1], ids[i1], ids[i]]
            dvec = _dir(ring, float(ang[i]))
            cen = g.positions("base")[f].mean(axis=0)
            fi = g.shape_face(f, {"part": "leg", "side": side, "ring": k, "col": i, "s": float(s / sc),
                                  "below_knee": s > knee_s + 0.01, "front": float(-dvec[1]), "u": float(math.degrees(
                                      math.atan2(dvec[0] * side, -dvec[1])))}, cen - ring.centre)
            fs.shape_faces.append(fi)
        prev = ids
    if straight:
        _straighten(fs, side, all_rings, knee_k)
        _hem_weights(fs, side, [ids for _, _, _, _, ids in all_rings], p.get("hem_follow", 0.06))
    if p.get("boot_room"):
        fs.stats[f"boot_room_moves.{F.SIDES[side]}"] = _boot_room(fs, [(ring, ids) for _, s, ring, _, ids in all_rings],
                                                                  p["boot_room"], side,
                                                                  float(p.get("boot_room_inner", {}).get(fr.id, 1.0)))
    fs.stats[f"leg_other_side_fixed.{F.SIDES[side]}"] = _own_side_weights(g, [i for *_, ids in all_rings for i in ids], side)
    seam = p.get("inner_seam", {}).get(fr.id)  # per body: the band in hip-joint half widths
    if seam:
        fs.stats[f"inner_seam.{F.SIDES[side]}"] = _inner_seam_weights(fs, [ids for *_, ids in all_rings], float(seam))
    return prev


# How far below the crotch (reference metres) the trousers' inner seam blend
# fades out (none at the first, all of it from the second up).
INNER_SEAM_DEPTH = (0.16, 0.06)


def _inner_seam_weights(fs: FittedShape, rings, band: float) -> int:
    """The inner thighs of a pair of trousers near the crotch (the second
    walk and run pass, 2026-11-01: with the female's feet on one line and no
    swing out, a leg swinging past the other pushed its trouser leg's inner
    thigh through the other's). Like a stretch skirt's centre seam
    (`MIDLINE_BAND`): within `band` hip-joint half widths of the midline the
    weights blend toward their left-right average (half and half on the
    midline, the leg's own at the band's edge; faded out by
    `INNER_SEAM_DEPTH` below the crotch), so the inner-thigh fabric of
    both legs stretches across the crotch instead of each panel following its
    own thigh into the other's. Returns the count."""
    g, fr = fs.g, fs.g.frame
    width = band * abs(float(fr.hip_joint[1][0]))
    low, high = fr.crotch_z - INNER_SEAM_DEPTH[0] * fr.sc, fr.crotch_z - INNER_SEAM_DEPTH[1] * fr.sc
    P = g.positions("base")
    n = 0
    for ids in rings:
        for vid in ids:
            a = 1.0 - float(np.clip(abs(float(P[int(vid)][0])) / width, 0.0, 1.0))
            a *= float(np.clip((float(P[int(vid)][2]) - low) / (high - low), 0.0, 1.0))  # the upper inner thigh only
            if a <= 0.0:
                continue
            w = g.weights[int(vid)]
            g.weights[int(vid)] = F.top_weights(F.blend_weights([(1.0 - 0.5 * a, w), (0.5 * a, _mirror_weights(w))]))
            n += 1
    return n


def _own_side_weights(g, ids, side: int) -> int:
    """A trouser leg moves with its own leg only. Its vertices take their
    weights from the nearest body surface, and where the legs touch (the
    wrapped female stands with her knees and feet together) that is the
    other leg: the inner hem then stretched across to the other foot in
    every stride. Bones of the other side are dropped (renormalised); a
    vertex left with nothing takes the mirrored bones. Returns the count."""
    own, other = F.SIDES[side], F.SIDES[-side]
    tag = lambda n, sd: n.endswith(f".{sd}") or f".{sd}." in n
    swap = lambda n: n.replace(f".{other}.", f".{own}.") if f".{other}." in n else n[: -len(other)] + own
    fixed = 0
    for vid in ids:
        w = g.weights[int(vid)]
        if not any(tag(n, other) for n in w):
            continue
        keep = {n: v for n, v in w.items() if not tag(n, other)}
        if sum(keep.values()) < 0.05:
            keep = F.blend_weights([(1.0, {swap(n) if tag(n, other) else n: v for n, v in w.items()})])
        g.weights[int(vid)] = F.top_weights(keep)
        fixed += 1
    return fixed


def _hem_weights(fs: FittedShape, side: int, rings, span: float) -> None:
    """Near the ankle a trouser hem hangs round the instep and heel, so it
    should follow the foot a little as well as the shin (else the shoe pokes
    out of the hem when the foot flexes). Vertices within `span` (reference
    metres) above the ankle joint blend toward the weights of the nearest
    calf or foot surface, more the lower they sit."""
    g, fr = fs.g, fs.g.frame
    top = float(fr.ankle[side][2]) + span * fr.sc
    P = g.positions("base")
    for ids in rings:
        for vid in ids:
            z = float(P[vid][2])
            if z >= top:
                continue
            t = float(np.clip((top - z) / (span * fr.sc), 0.0, 1.0))
            near = fr.weights(fr.nearest(P[vid], ("calves", "feet")))
            g.weights[vid] = F.top_weights(F.blend_weights([(1.0 - 0.6 * t, g.weights[vid]), (0.6 * t, near)]))


def _boot_room(fs: FittedShape, rings, room, side: int = 1, inner: float = 1.0) -> int:
    """Trouser legs below the knee leave room for a boot shaft worn under
    them (the trouser-side envelope, as `skirt.envelope` is for a robe over a
    skirt). `room` lists [height, metres] (heights as hems: landmark specs):
    each leg vertex at that height stands at least that far off the calf or
    foot, linear in between, constant below the first point, fading to
    nothing over 3 cm above the last. The boot shaft itself must clear the
    tights of a skirt (3-7 mm off the calf), so it stands 7-12 mm off the
    skin plus its rolled top; the trouser legs hugging the calf at 4-11 mm
    (behind the knee, and on the inner side where `inner_hug` keeps them
    close) let it break through as a band with teeth behind the knee (the
    riding boot, 2026-11-11).

    Only the vertices closer than the room move, out from the leg's axis
    (the ring centre), by their own shortfall. Up the shaft the room is the
    cover under which the client hides it (1.25 times the cover plus 2 mm,
    `charlib/layers.py`): 9-14 mm, not a clearance over it. Over the tall
    boot's rolled top, just under the knee, it is a clearance (19 mm): the
    bent knee leaves the back of the leg there uncovered, so the roll is
    drawn and must stay inside. The push is evened round each ring
    (never less than half the neighbours' mean) and fades up and down the
    leg (`BOOT_ROOM_SPREAD`), so the leg keeps its straight, tapered or wide
    line whatever shoes are worn: carried all the way to the hem, with a
    larger room and a heel room under the hem, every trouser leg flared
    below the knee into a bell and the back hem dropped over the heel.
    `rings` are the whole leg tube's (ring, ids), top down. `inner` (per
    body, `boot_room_inner`) scales the room on the side facing the other
    leg. Returns vertex moves."""
    from mathutils import Vector

    g, fr = fs.g, fs.g.frame
    pts = sorted((_height_of(fr, h), float(a) * fr.sc) for h, a in room)
    zs = [z for z, _ in pts] + [pts[-1][0] + 0.03 * fr.sc]
    amounts = [a for _, a in pts] + [0.0]
    moved = 0
    for s in g.shapes:
        tree, tri_ids = fr.bvh(("calves", "feet"), s)
        P = g.positions(s)
        pushes, dirs_all = [], []
        for ring, ids in rings:
            ids = np.asarray(ids, dtype=np.int64)
            q = P[ids]
            need = np.interp(q[:, 2], zs, amounts)
            radial = q - ring.centre
            radial = radial - np.outer(radial @ ring.tangent, ring.tangent)
            rad = np.linalg.norm(radial, axis=1)
            dirs = radial / np.maximum(rad, 1e-9)[:, None]
            if inner != 1.0:
                w_in = np.clip(-dirs[:, 0] * side, 0.0, 1.0) ** 0.5
                need = need * (1.0 - w_in + w_in * inner)
            push = np.zeros(len(ids))
            for j, p in enumerate(q):
                if need[j] <= 0.0:
                    continue
                loc, nrm, idx, dist = tree.find_nearest(Vector(p), 0.1)
                if loc is None:
                    continue
                h = float((p - np.array(loc)) @ np.array(nrm))
                if h < need[j]:
                    # Along the radial direction, the deficit grows as the
                    # skin's normal leans away from it (by at most 1/0.8:
                    # beside a steep calf the full 1/cos pushed the leg's
                    # sides out by twice the shortfall).
                    cos = max(float(dirs[j] @ np.array(nrm)), 0.8)
                    push[j] = (need[j] - h) / cos
            for _ in range(2):  # round the ring: never less than half the neighbours' mean
                push = np.maximum(push, 0.5 * (0.5 * (np.roll(push, 1) + np.roll(push, -1))))
            pushes.append(push)
            dirs_all.append(dirs)
        # Up and down the leg: a push only on the rings that need it would
        # step the leg's line; it fades over the rings above and below
        # (BOOT_ROOM_SPREAD of it per ring).
        Pu = np.array(pushes)
        for k in range(len(Pu) - 2, -1, -1):
            Pu[k] = np.maximum(Pu[k], BOOT_ROOM_SPREAD[0] * Pu[k + 1])
        for k in range(1, len(Pu)):
            Pu[k] = np.maximum(Pu[k], BOOT_ROOM_SPREAD[1] * Pu[k - 1])
        for (ring, ids), push, dirs in zip(rings, Pu, dirs_all):
            if push.any():
                P[np.asarray(ids)] += dirs * push[:, None]
                moved += int((push > 0).sum())
        g.pos[s] = [P]
    return moved


# `_boot_room`'s push carried to the ring above (up the leg) and below. Down
# the leg it fades out (0.5 per ring) instead of reaching the hem.
BOOT_ROOM_SPREAD = (0.4, 0.5)


def _s_at_z(line, z: float) -> float:
    """Arc length along a (downward) limb line where it reaches height z."""
    ss = np.linspace(0.0, line.length, 200)
    zs = np.array([line.point(s)[2] for s in ss])
    return float(np.interp(-z, -zs, ss))


def _straighten(fs: FittedShape, side: int, rings, knee_k: int) -> None:
    """Below the knee the leg hangs straight from the knee ring to a round,
    sloped hem (front higher than the back), never closer to the skin than
    the fitted offset."""
    g, fr, p = fs.g, fs.g.frame, fs.params
    sc = fr.sc
    hem = p["hem"]
    ease = p.get("ease", 0.02)
    r_min = p.get("min_radius", 0.068) * sc
    # `inner_hug` (per body): the side facing the other leg keeps to the
    # fitted offset instead of hanging straight out to the hem, so a leg
    # swinging past the other on the female's one-line gait does not pass
    # through the other trouser leg (1: the innermost column hugs fully).
    hug = float(p.get("inner_hug", {}).get(fr.id, 0.0))
    lower = [rr for rr in rings if rr[0] >= knee_k]
    if len(lower) < 2:
        return
    last_k = lower[-1][0]
    for s in g.shapes:
        P = g.positions(s)
        kn = lower[0]
        c0 = kn[2].centre
        t0 = kn[2].tangent

        def radial(ids, ring):
            v = P[ids] - ring.centre
            v = v - np.outer(v @ ring.tangent, ring.tangent)
            return np.linalg.norm(v, axis=1)

        d_knee = radial(kn[4], kn[2])
        hem_ids, hem_ring = lower[-1][4], lower[-1][2]
        r_hem = max(float(radial(hem_ids, hem_ring).max()) + ease, r_min)
        for k, sv, ring, ang, ids in lower[1:]:
            t = (k - knee_k) / max(last_k - knee_k, 1)
            d_body = radial(ids, ring)
            target = d_knee * (1 - t) + r_hem * t
            d_new = np.maximum(d_body + (ease * 0.5 if k == last_k else 0.0), target)
            for j, vid in enumerate(ids):
                dvec = _dir(ring, float(ang[j]))
                if hug:
                    inner = hug * float(np.clip(-dvec[0] * side, 0.0, 1.0)) ** 0.5
                    d_new[j] = d_new[j] * (1.0 - inner) + d_body[j] * inner
                q = ring.centre + dvec * d_new[j]
                if k == last_k:
                    front = float(-dvec[1])
                    zh = hem["back"] + (hem["front"] - hem["back"]) * np.clip(front * 0.5 + 0.5, 0, 1) ** 1.5
                    q[2] = zh * sc
                P[vid] = q
        g.pos[s] = [P]


# ----------------------------------------------------------------------------
# Skirts: tights (the legs chart at a small offset), a yoke, a hanging skirt
# ----------------------------------------------------------------------------


def build_skirt(fr: BodyFrame, p: dict, name: str = "skirt") -> FittedShape:
    """A skirt over opaque tights. The tights are the trousers chart (hips grid
    and two leg tubes, fitted close: `tights` overrides the trousers params);
    a yoke from the waist to the hip line sits over them at the yoke offset,
    and the skirt hangs from the yoke's bottom loop to a hem placed between
    two landmarks, flaring and weighted from the spine toward the thighs so
    it swings with the stride. Nothing is fitted to the current body's rows:
    the hem height, flare and yoke rows come from measured landmarks."""
    from . import gear as G

    tp = {**p, **p.get("tights", {}), "covered_from": "hip"}
    fs = build_legs(fr, tp, name)
    g, sc = fs.g, fr.sc
    # Everything built so far is the tights panel.
    for fi in fs.shape_faces:
        g.coords[fi]["layer"] = "tights"
    fs.loops["tights_waist"] = fs.loops.pop("waist")
    fs.guides["tights_waist"] = fs.guides.pop("waist", None)
    for side in ("L", "R"):
        fs.loops[f"tights_hem.{side}"] = fs.loops.pop(f"hem.{side}")
    # Yoke rows: hip line up to the waist (top) at the yoke offset.
    nc = p["cols"]
    dth = 360.0 / nc
    top_z = fr.landmark(p["top"]["at"]) + p["top"].get("dz", 0.0) * sc
    hang_z = min(_height_of(fr, p["yoke"]["bottom"]), top_z - 0.04 * sc)
    zs = np.linspace(hang_z, top_z, p["yoke"]["rows"] + 1)
    off = p["yoke"]["offset"]
    vid = -np.ones((len(zs), nc), dtype=np.int64)
    for r, z in enumerate(zs):
        sps, coords = [], []
        for c in range(nc):
            th = c * dth
            o, d = fr.trunk_ray(th, ("h", float(z)))
            sp = fr.fit(o, d, ("hips", "torso"), fallback=ALL)
            sp.off = _interp_landmarks(fr, off, fr.point(sp)[2])
            sps.append(sp)
            coords.append({"part": "yoke", "row": r, "col": c, "u": _signed(th)})
        vid[r] = g.add_surface(sps, coords)
    fs.stats["yoke_bridged"] = sum(_bridge_ring(g, vid[r]) for r in range(len(zs)))
    yoke_faces = []
    for r in range(len(zs) - 1):
        for c in range(nc):
            c1 = (c + 1) % nc
            ids = [vid[r, c], vid[r, c1], vid[r + 1, c1], vid[r + 1, c]]
            cen = g.positions("base")[ids].mean(axis=0)
            th = _signed(((c + 0.5) * dth) % 360)
            outward = cen - fr.trunk_origin(cen[2])
            outward[2] = 0
            fi = g.shape_face(ids, {"part": "yoke", "row": r, "col": c, "u": th, "front": math.cos(math.radians(th)),
                                    "z": float(cen[2])}, outward)
            fs.shape_faces.append(fi)
            yoke_faces.append(fi)
    yoke_ids = sorted({int(v) for f in yoke_faces for v in g.faces[f]})
    fs.stats["yoke_clearance_moves"] = F.enforce_clearance(g, yoke_ids, p.get("min_clear", 0.003) + 0.003)
    fs.stats["yoke_face_clearance_moves"] = F.enforce_face_clearance(g, yoke_faces, p.get("min_clear", 0.003) + 0.003)
    fs.levels["yoke_top"] = len(zs) - 1
    fs.loops["waist"] = Loop([int(v) for v in vid[-1]], True, "up")
    fs.guides["waist"] = _guide_ring(fs, nc, top_z, p.get("band_guide", 0.04) * sc, off)
    fs.lines["fly"] = [int(vid[r, 0]) for r in range(len(zs) - 1, -1, -1)]
    for side in (1, -1):
        c = int(round((60.0 if side > 0 else 300.0) / dth)) % nc
        fs.anchors[f"hip.{F.SIDES[side]}"] = Anchor(int(vid[max(len(zs) - 2, 0), c]))
    # The hanging skirt.
    sk = p["skirt"]
    n = sk.get("rings", 8)
    n0 = len(g.faces)
    loop = [int(v) for v in vid[0]]
    rings = G.hanging_skirt(g, None, loop, _height_of(fr, sk["hem"]), sk.get("flare", 0.085) * sc,
                            pleats=sk.get("pleats", 0.0) if p.get("pleated", True) else 0.0, n=n, follow=sk.get("follow", 0.6))
    fs.stats["skirt_weighted"] = _skirt_weights(fs, rings, sk.get("follow", 0.6), shin_follow=sk.get("shin_follow", 0.0),
                                                shell=float(sk.get("shell", 0.0)))
    for i, fi in enumerate(range(n0, len(g.faces))):
        k, c = divmod(i, nc)
        g.coords[fi] = {"part": "skirt", "ring": k + 1, "col": c, "u": _signed(((c + 0.5) * dth) % 360)}
        fs.shape_faces.append(fi)
    fs.loops["skirt_hem"] = Loop([int(v) for v in rings[-1]], True, "down")
    fs.hanging = [[int(v) for v in r] for r in rings]
    fs.stats["skirt_faces"] = len(g.faces) - n0
    return fs


# ----------------------------------------------------------------------------
# Shoes: a capsule round the foot, opened at the top for the ankle
# ----------------------------------------------------------------------------


# The shoe's last. Fitted point by point, the shoe copied
# every lump of the foot under it (the toes' tips, the creases of the
# instep) and its outline traced them as jagged edges. The capsule's rays
# keep their directions, but the distance along each ray is smoothed over
# the ring/column grid (Taubin-like passes that let it tighten a little),
# then held at least LAST_CLEAR off the skin under it and relaxed again with
# that floor: a smooth, close toe box, vamp and heel counter that still
# clear the foot.
LAST_CLEAR = 0.005
LAST_PASSES = 8
LAST_FLOOR_PASSES = 24
LAST_TOE_ROOM = 0.006  # the toe box's room over the toes' top (m at the reference height), besides LAST_CLEAR


def _grid_avg(R: np.ndarray, ends: tuple[float, float]) -> np.ndarray:
    """Mean of each grid value's neighbours: the columns wrap round, the
    first and last rings also see their pole's value."""
    nb = np.roll(R, 1, axis=1) + np.roll(R, -1, axis=1)
    cnt = np.full(R.shape, 2.0)
    nb[1:] += R[:-1]
    nb[:-1] += R[1:]
    cnt[1:] += 1
    cnt[:-1] += 1
    nb[0] += ends[0]
    nb[-1] += ends[1]
    cnt[0] += 1
    cnt[-1] += 1
    return nb / cnt


def _toe_tops(fr: BodyFrame, side: int):
    """The highest foot skin along the foot (centres along Y, heights), per
    centimetre, lightly smoothed: what the toe box may not stand far over."""
    fv = fr.P["base"][fr.region_verts["feet"]]
    fv = fv[np.sign(fv[:, 0]) == side]
    edges = np.arange(fv[:, 1].min() - 0.005, fv[:, 1].max() + 0.015, 0.01)
    k = np.clip(np.digitize(fv[:, 1], edges) - 1, 0, len(edges) - 2)
    top = np.full(len(edges) - 1, -1.0)
    np.maximum.at(top, k, fv[:, 2])
    for i in range(len(top)):  # empty bins take their neighbours' height
        if top[i] < 0:
            top[i] = max(top[max(i - 1, 0)], top[min(i + 1, len(top) - 1)], 0.0)
    top = np.convolve(np.pad(top, 1, mode="edge"), np.ones(3) / 3.0, mode="valid")
    return 0.5 * (edges[:-1] + edges[1:]), top


def _smooth_last(g: Garment, fr: BodyFrame, grid, params, poles, ends, t, nrm, bino, side: int, clear: float) -> float:
    """Moves the shoe's grid vertices along their own rays onto a smooth
    last (see LAST_CLEAR); returns the largest move in millimetres."""
    ids = np.array(grid, dtype=np.int64)
    nr, M = ids.shape
    phis = np.arange(M) * 2 * np.pi / M
    O = np.zeros((nr, M, 3))
    D = np.zeros((nr, M, 3))
    for r, (o, beta) in enumerate(params):
        b = math.radians(beta)
        for j, ph in enumerate(phis):
            radial = math.cos(ph) * nrm + math.sin(ph) * bino * side
            O[r, j] = o
            D[r, j] = unit(-t * math.cos(b) + radial * math.sin(b))
    P = g.positions("base")
    R = ((P[ids] - O) * D).sum(axis=-1)
    skin = np.array([[fr.point(g.surface[int(v)], off=0.0) for v in row] for row in ids])
    floor = ((skin - O) * D).sum(axis=-1) + clear
    # A ray that meets no foot (over the low toes) was fitted to the nearest
    # skin instead (the old beak at the toe): it has no floor of its own.
    for r in range(nr):
        for j in range(M):
            if fr.ray(O[r, j], D[r, j], FEET, 0.15) is None:
                floor[r, j] = 0.01
    # The toe box follows the toes down: ahead of the ball no ray reaches
    # higher than LAST_TOE_ROOM over the highest skin at its place along the
    # foot (a ceiling along the ray, so the vertices stay on their rays).
    centres, top = _toe_tops(fr, side)
    ceil = np.full((nr, M), np.inf)
    ball_back = -fr.ball[side][1] - 0.06 * fr.sc
    for r in range(nr):
        for j in range(M):
            dz = D[r, j, 2]
            if dz < 0.15:
                continue
            p = O[r, j] + D[r, j] * R[r, j]
            if -p[1] < ball_back:
                continue
            h = float(np.interp(p[1], centres, top)) + clear + LAST_TOE_ROOM * fr.sc
            ceil[r, j] = max((h - O[r, j, 2]) / dz, floor[r, j])
    Pp = P[np.array(poles)]
    pole_r = (float((Pp[0] - ends[0]) @ -t), float((Pp[1] - ends[1]) @ t))
    S = np.minimum(R, ceil)
    for _ in range(LAST_PASSES):  # Taubin-like: smooth, then inflate most of it back
        S = S + 0.5 * (_grid_avg(S, pole_r) - S)
        S = S - 0.4 * (_grid_avg(S, pole_r) - S)
    S = np.clip(S, floor, np.maximum(ceil, floor))
    for _ in range(LAST_FLOOR_PASSES):
        S = np.clip(S + 0.5 * (_grid_avg(S, pole_r) - S), floor, np.maximum(ceil, floor))
    move = (S - R)[..., None] * D
    for s in g.shapes:
        Q = g.positions(s)
        Q[ids.ravel()] += move.reshape(-1, 3)
        g.pos[s] = [Q]
    return round(float(np.abs(S - R).max()) * 1000, 1)


def build_shoe(fr: BodyFrame, p: dict, name: str = "shoe") -> FittedShape:
    g = Garment(fr)
    if "cuts" in p:
        p = {**p, **p["cuts"][p.get("cut", next(iter(p["cuts"])))]}
    fs = FittedShape(name, g, p)
    for side in (1, -1):
        _shoe_side(fs, side)
    fs.hides = ["feet"]
    fs.stats.update({"cols": p["cols"], "rings": p["rings"]})
    return fs


def _shoe_side(fs: FittedShape, side: int) -> None:
    g, fr, p = fs.g, fs.g.frame, fs.params
    sc = fr.sc
    ft = fr.foot[side]
    ank = fr.ankle[side]
    tip = fr.toe_tip[side]
    # Capsule from the measured foot: radii as fractions of the foot half
    # width, axis heights as fractions of the ankle joint height.
    r_h, r_t = p["heel_radius"] * ft["half_w"], p["toe_radius"] * ft["half_w"]
    A = np.array([ank[0], ft["heel_y"] - r_h, p["axis_z"] * float(ank[2])])
    B = np.array([tip[0], ft["toe_y"] + r_t, p["toe_axis_z"] * float(ank[2])])
    # The toe cap's centre inside the toes (at most halfway up them), so its
    # rays fan out from within the toes rather than from above them.
    centres, top = _toe_tops(fr, side)
    B[2] = min(B[2], 0.5 * float(np.interp(B[1], centres, top)))
    t = unit(B - A)
    nrm = unit(np.array([0.0, 0.0, 1.0]) - t * t[2])
    bino = np.cross(t, nrm)
    M = p["cols"]
    nr = p["rings"]
    ch, ct = p.get("heel_cap_rings", 3), p.get("toe_cap_rings", 3)
    L = float(np.linalg.norm(B - A))
    # Ring parameters: (origin, beta) where beta is the angle from the axis
    # (0 = straight back from A, 90 = radial, 180 = straight ahead from B).
    params = []
    for i in range(ch):
        params.append((A, 90.0 * (i + 1) / (ch + 0.5)))
    mid = nr - ch - ct
    for i in range(mid):
        params.append((A + (B - A) * (i + 0.5) / mid, 90.0))
    for i in range(ct):
        params.append((B, 90.0 + 90.0 * (i + 0.5) / (ct + 0.5)))
    off_base, off_toe = p["offset"]["base"], p["offset"]["toe"]
    ball_fwd = -fr.ball[side][1]
    phis = np.arange(M) * 2 * np.pi / M
    grid = []
    for r, (o, beta) in enumerate(params):
        b = math.radians(beta)
        sps, coords = [], []
        for j, ph in enumerate(phis):
            radial = math.cos(ph) * nrm + math.sin(ph) * bino * side
            d = unit(-t * math.cos(b) + radial * math.sin(b))
            sp = fr.fit(o, d, FEET, fallback=None, guess=0.04, max_dist=0.15)
            q = fr.point(sp)
            fwd = -q[1]
            sp.off = off_base + (off_toe - off_base) * float(np.clip((fwd - (ball_fwd - 0.03 * sc)) / (0.08 * sc), 0, 1))
            sps.append(sp)
            coords.append({"part": "shoe", "side": side, "ring": r, "col": j})
        grid.append([int(i) for i in g.add_surface(sps, coords)])
    # Poles.
    poles = []
    for o, d in ((A, -t), (B, t)):
        sp = fr.fit(o, d, FEET, fallback=None, guess=0.05, max_dist=0.15)
        sp.off = off_toe if d is t else off_base
        poles.append(int(g.add_surface([sp], [{"part": "shoe", "side": side, "pole": True}])[0]))
    fs.stats.setdefault("last_moves_mm", {})[side] = _smooth_last(g, fr, grid, params, poles, (A, B), t, nrm, bino, side,
                                                                    p.get("last_clear", LAST_CLEAR))
    # Opening for the ankle: top columns over the first rings.
    op = p["opening"]
    r0, r1 = op["rings"]
    half = op["half_angle"]

    def col_phi(j):  # signed degrees of a face column centre, 0 = top
        a = math.degrees((j + 0.5) * 2 * np.pi / M)
        return a if a <= 180 else a - 360

    def opened(r, j):
        return r0 <= r < r1 and abs(col_phi(j)) < half

    for r in range(len(grid) - 1):
        for j in range(M):
            if opened(r, j):
                continue
            j1 = (j + 1) % M
            f = [grid[r][j], grid[r][j1], grid[r + 1][j1], grid[r + 1][j]]
            cen = g.positions("base")[f].mean(axis=0)
            axis_pt = A + t * float(np.clip((cen - A) @ t, 0, L))
            fi = g.shape_face(f, {"part": "shoe", "side": side, "ring": r, "col": j, "phi": col_phi(j),
                                  "fwd": float(-cen[1] + ank[1]) / sc, "z": float(cen[2]) / sc,
                                  "x": float(abs(cen[0]) - abs(ank[0])) / sc}, cen - axis_pt)
            fs.shape_faces.append(fi)
    for pole, ring, ref in ((poles[0], grid[0], A), (poles[1], grid[-1], B)):
        for j in range(M):
            if pole == poles[0] and opened(-1, j):
                continue
            j1 = (j + 1) % M
            f = [ring[j], ring[j1], pole]
            cen = g.positions("base")[f].mean(axis=0)
            fi = g.shape_face(f, {"part": "shoe", "side": side, "ring": -1 if pole == poles[0] else len(grid), "col": j,
                                  "phi": col_phi(j), "fwd": float(-cen[1] + ank[1]) / sc, "z": float(cen[2]) / sc,
                                  "x": float(abs(cen[0]) - abs(ank[0])) / sc}, cen - ref)
            fs.shape_faces.append(fi)
    # The collar: the opening's boundary, kept below `collar_max`.
    side_faces = [fs.g.faces[f] for f in fs.shape_faces if fs.g.coords[f].get("side") == side]
    loops = F.boundary_loops(side_faces)
    collar = max(loops, key=len)
    zmax = p.get("collar_max", 0.075) * sc
    # The opening is cut on whole grid cells, so its loop turns square
    # corners where the cut ends (the oxford's "ears" either side of the
    # tongue, the jagged top line, 2026-11-11): rounded along the loop
    # (Taubin, so it keeps its size) before the welt grows from it.
    cl = np.array(collar, dtype=np.int64)
    for s in g.shapes:
        P = g.positions(s)
        Q = P[cl].copy()
        for _ in range(4):
            for lam in (0.5, -0.53):
                Q = Q + lam * (0.5 * (np.roll(Q, 1, axis=0) + np.roll(Q, -1, axis=0)) - Q)
        P[cl] = Q
        g.pos[s] = [P]
    for s in g.shapes:
        P = g.positions(s)
        z = P[collar, 2]
        if (z > zmax).any():
            P[np.array(collar)[z > zmax], 2] = zmax
        g.pos[s] = [P]
    fs.loops[f"collar.{F.SIDES[side]}"] = Loop([int(i) for i in collar], True, "up", side)
    # Instep anchor: top centre of the first ring in front of the opening.
    fs.anchors[f"instep.{F.SIDES[side]}"] = Anchor(grid[min(r1, len(grid) - 1)][0])
    fs.anchors[f"toe.{F.SIDES[side]}"] = Anchor(poles[1])
    fs.lines[f"sole_axis.{F.SIDES[side]}"] = [poles[0], poles[1]]
    fs.stats.setdefault("axis", {})[side] = (A.tolist(), B.tolist())
    # Flatten the bottom onto the floor (the sole slab covers it).
    ids = sorted({v for f in side_faces for v in f})
    floor = p.get("sole_inner", 0.006) * sc
    for s in g.shapes:
        P = g.positions(s)
        P[ids, 2] = np.maximum(P[ids, 2], floor)
        g.pos[s] = [P]


def build_boot(fr: BodyFrame, p: dict, name: str = "boot") -> FittedShape:
    """A boot: the shoe_low capsule opened round the ankle, with a shaft grown
    up the leg from the opening loop (the way sleeves grow from arm holes:
    rings on the leg chart, starting at the loop's own angles and relaxing
    to even spacing), to a top height between two landmarks."""
    fs = build_shoe(fr, p, name)
    g, sc = fs.g, fr.sc
    p = fs.params
    spec = dict(p["shaft"])
    if p.get("height") in p.get("heights", {}):
        spec["top"] = p["heights"][p["height"]]
    for side in (1, -1):
        key = f"collar.{F.SIDES[side]}"
        ring0 = list(fs.loops[key].ids)
        line = fr.limb_line("leg", side)
        P0 = g.positions("base")[ring0]
        z0 = float(P0[:, 2].mean()) + spec.get("start", 0.012) * sc
        z1 = _height_of(fr, spec["top"])
        s0, s1 = _s_at_z(line, z0), _s_at_z(line, z1)
        n = spec.get("rings", 7)
        s_values = np.linspace(s0, s1, n)
        rings = fr.limb_rings("leg", side, s_values)
        a0 = _ring_angles(P0, rings[0])
        # As a sleeve's first rings (`_sleeve`): the opening loop is not
        # level (it rises at the heel), so each column's first rings start
        # above its own loop vertex; one level ring folded back over the
        # heel's collar.
        step = spec.get("step", 0.008) * sc
        s_loop = np.array([_s_at_z(line, float(q[2])) for q in P0])
        # Ring winding must match the loop's direction round the leg.
        prev = ring0
        off_tab = spec["offset"]
        for k, (s, ring) in enumerate(zip(s_values, rings), start=1):
            ang = _smooth_angles(a0, k, ramp=2)
            t = (k - 1) / max(n - 1, 1)
            off = float(np.interp(t, [a for a, _ in off_tab], [b for _, b in off_tab]))
            sk = np.minimum(s, s_loop - step * k)  # s grows down the leg
            sps, coords = [], []
            for i, a in enumerate(ang):
                ri = ring if sk[i] >= s - 1e-6 else fr.limb_rings("leg", side, [float(sk[i])])[0]
                sp = fr.fit(ri.centre, _dir(ri, a), ("calves", "feet"), fallback=None, guess=0.05, max_dist=0.2)
                sp.off = off
                sps.append(sp)
                coords.append({"part": "shaft", "side": side, "ring": k, "col": i})
            ids = [int(v) for v in g.add_surface(sps, coords)]
            m = len(ids)
            for i in range(m):
                i1 = (i + 1) % m
                f = [prev[i], prev[i1], ids[i1], ids[i]]
                dvec = _dir(ring, float(ang[i]))
                cen = g.positions("base")[f].mean(axis=0)
                # `t`: the face's height up the shaft (0 at the opening, 1 at
                # the top), so paint bands (a strap, a shaft band) keep their
                # place whatever the shaft's ring count.
                fi = g.shape_face(f, {"part": "shaft", "side": side, "ring": k, "col": i, "front": float(-dvec[1]),
                                      "x": float(dvec[0] * side), "z": float(cen[2]) / sc, "t": (k - 0.5) / n},
                                  cen - ring.centre)
                fs.shape_faces.append(fi)
            prev = ids
        fs.loops[f"opening.{F.SIDES[side]}"] = fs.loops[key]
        fs.loops[key] = Loop(prev, True, "up", side)
    shaft_ids = [v for v, c in g.vcoords.items() if c.get("part") == "shaft"]
    shaft_faces = [f for f in fs.shape_faces if g.coords[f].get("part") == "shaft"]
    fs.stats["shaft_clearance_moves"] = F.enforce_clearance(g, shaft_ids, p.get("min_clear", 0.004), ("calves", "feet"))
    fs.stats["shaft_face_clearance_moves"] = F.enforce_face_clearance(g, shaft_faces, p.get("min_clear", 0.004), ("calves", "feet"))
    fs.hides = ["feet"]
    return fs


# ----------------------------------------------------------------------------
# Soft hats: a knitted cap round the head
# ----------------------------------------------------------------------------


def build_cap(fr: BodyFrame, p: dict, name: str = "cap") -> FittedShape:
    g = Garment(fr)
    fs = FittedShape(name, g, p)
    M = p["cols"]
    rows = p["rows"]
    s = fr.head_scale
    band = p["band"]
    rings = []
    W = {"DEF-spine.006": 1.0}
    for i in range(rows + 1):
        t = i / rows
        sps, coords = [], []
        for j in range(M):
            th = 360.0 * j / M
            side = th if th <= 180 else 360 - th
            band_phi = band["front"] + band["side"] * (1 - math.cos(math.radians(side))) / 2.0 + band.get("ear", 0.0) * math.sin(
                math.radians(side)) ** 2
            phi = band_phi * (1 - t) + p.get("top_phi", 4.0) * t
            o, d = fr.head_ray(phi, th)
            sp = fr.fit(o, d, ("head", "scalp"), fallback=HEAD, guess=0.09)
            sp.off = (p["offset"] + p.get("bulge", 0.0) * math.sin(math.pi * t)) * s
            sps.append(sp)
            coords.append({"part": "crown", "row": i, "col": j})
        rings.append([int(v) for v in g.add_surface(sps, coords, weights=[dict(W) for _ in sps])])
    droop = p.get("droop")
    if droop:
        # A slouch: the upper rings slide back and down behind the head
        # (head-scale units), so the loose top sags. The sag only ever adds
        # room: a vertex never ends nearer the head centre than it was
        # fitted (sliding the crown back and down used to pull its front and
        # top into the hair under it, the cap variants' pressed crown).
        back, down = droop
        hc = np.asarray(fr.head_centre, dtype=np.float64)
        for i, ring in enumerate(rings):
            t = i / rows
            shift = np.array([0.0, back * s * t ** 1.8, -down * s * t ** 2.2])
            for sh in g.shapes:
                P = g.positions(sh)
                Q = P[ring] + shift
                r0 = np.linalg.norm(P[ring] - hc, axis=1)
                d = Q - hc
                r1 = np.maximum(np.linalg.norm(d, axis=1), 1e-9)
                Q = np.where((r1 < r0)[:, None], hc + d * (r0 / r1)[:, None], Q)
                P[ring] = Q
                g.pos[sh] = [P]
    for i in range(rows):
        for j in range(M):
            j1 = (j + 1) % M
            f = [rings[i][j], rings[i][j1], rings[i + 1][j1], rings[i + 1][j]]
            cen = g.positions("base")[f].mean(axis=0)
            th = 360.0 * (j + 0.5) / M
            fi = g.shape_face(f, {"part": "crown", "row": i, "col": j, "u": _signed(th), "t": (i + 0.5) / rows},
                              cen - fr.head_centre)
            fs.shape_faces.append(fi)
    top = g.positions("base")[rings[-1]].mean(axis=0)
    cid = int(g.add({sh: g.positions(sh)[rings[-1]].mean(axis=0)[None] + np.array([[0, 0, 0.004]]) for sh in g.shapes}, [dict(W)])[0])
    for j in range(M):
        f = [rings[-1][j], rings[-1][(j + 1) % M], cid]
        cen = g.positions("base")[f].mean(axis=0)
        fi = g.shape_face(f, {"part": "crown", "row": rows, "col": j, "u": _signed(360.0 * (j + 0.5) / M), "t": 1.0},
                          cen - fr.head_centre)
        fs.shape_faces.append(fi)
    fs.loops["band"] = Loop(rings[0], True, "down")
    fs.anchors["crown"] = Anchor(cid)
    fs.hides = list(p.get("hides", ["scalp"]))
    fs.stats = {"rows": rows, "cols": M, "head_scale": round(float(s), 3)}
    return fs


def build_rigid(fr: BodyFrame, p: dict, name: str = "hat_rigid") -> FittedShape:
    """Stiff hats: only the head band is fitted (the cap_knit head chart at the
    band line, `offset` off the scalp), plus a ribbon band strip on it. The
    hat body (crown, brim) is an add-on built from the band's loops in
    head-scale units, so it is designed once and only rides on the fitted
    ring; it is weighted rigidly to the head (socket_head's bone)."""
    g = Garment(fr)
    fs = FittedShape(name, g, p)
    M = p["cols"]
    s = fr.head_scale
    band = p["band"]
    W = {"DEF-spine.006": 1.0}
    sps, coords = [], []
    for j in range(M):
        th = 360.0 * j / M
        side = th if th <= 180 else 360 - th
        phi = band["front"] + band["side"] * (1 - math.cos(math.radians(side))) / 2.0 + band.get("ear", 0.0) * math.sin(
            math.radians(side)) ** 2
        o, d = fr.head_ray(phi, th)
        sp = fr.fit(o, d, ("head", "scalp"), fallback=HEAD, guess=0.09)
        sp.off = p["offset"] * s
        sps.append(sp)
        coords.append({"part": "band", "row": 0, "col": j})
    ring0 = [int(v) for v in g.add_surface(sps, coords, weights=[dict(W) for _ in sps])]
    h = p.get("band_height", 0.018) * s
    ring1 = [int(v) for v in g.add({sh: g.positions(sh)[ring0] + np.array([0.0, 0.0, h]) for sh in g.shapes},
                                   [dict(W) for _ in ring0])]
    for j in range(M):
        if p.get("band_faces", True) is False:
            break  # hair trims (a comb): the ring only carries anchors and loops
        j1 = (j + 1) % M
        f = [ring0[j], ring0[j1], ring1[j1], ring1[j]]
        cen = g.positions("base")[f].mean(axis=0)
        fi = g.shape_face(f, {"part": "band", "row": 0, "col": j, "u": _signed(360.0 * (j + 0.5) / M)},
                          cen - np.array([fr.head_centre[0], fr.head_centre[1], cen[2]]))
        fs.shape_faces.append(fi)
    fs.loops["band"] = Loop(ring0, True, "down")
    fs.loops["band_top"] = Loop(ring1, True, "up")
    top = g.positions("base")[ring1].mean(axis=0)
    fs.anchors["crown"] = Anchor(int(g.add({sh: top[None] for sh in g.shapes}, [dict(W)])[0]))
    for name, th in (("front", 0.0), ("left", 90.0), ("right", 270.0), ("back", 180.0), ("left_front", 50.0), ("right_front", 310.0),
                     ("left_back", 130.0), ("right_back", 230.0)):
        fs.anchors[f"band_{name}"] = Anchor(ring0[int(round(th / 360.0 * M)) % M])
    fs.hides = list(p.get("hides", ["scalp"]))
    fs.stats = {"cols": M, "head_scale": round(float(s), 3)}
    return fs


def build_hood(fr: BodyFrame, p: dict, name: str = "hood") -> FittedShape:
    """A raised hood: rings of the head chart from the crown (`phis[0]`) down
    past the jaw to the neck (`phis[-1]`), each `offset` off the head plus
    `back_room` behind it (head-scale units), with the face opening cut by
    `open` ([[phi, half-angle in degrees], ...]). Optional `tip` pulls the top
    back into a point (cowls) and `folds` adds soft radial folds to the lower
    rings. Weighted to the head above `neck_phi` and blended toward the neck
    and upper spine below, so the lower rings ride the shoulders, not the
    turning head. Loops: `face` (the opening, for fur rolls) and `hem`."""
    g = Garment(fr)
    fs = FittedShape(name, g, p)
    M = p["cols"]
    s = fr.head_scale
    phis = list(p["phis"])
    op = p["open"]
    off_tab = p["offset"]
    neck_phi, blend = p.get("neck_phi", 95.0), p.get("neck_blend", 40.0)
    # The rings are offset along the fitted surface's normal. The ear's
    # normals point every way, so `fit_ears: false` fits the rings to the
    # skull and neck only (the radial exit and the clearance pass below still
    # keep the hood clear of the ears): the cowl's side rings twisted over the
    # male ear and dipped into the hair pressed behind it.
    fit_regions = ("head", "scalp", "ears", "neck") if p.get("fit_ears", True) else ("head", "scalp", "neck")

    def half(phi):
        return float(np.interp(phi, [a for a, _ in op], [b for _, b in op]))

    # Which faces stay, and the opening's edge vertex of each ring turned onto
    # the opening's own angle (as the tops' V openings), so the face opening
    # is a smooth curve instead of steps along the grid columns.
    dth = 360.0 / M
    keep = [[abs(_signed(360.0 * (j + 0.5) / M)) >= min(half(phis[i]), half(phis[i + 1])) for j in range(M)]
            for i in range(len(phis) - 1)]
    used = [[False] * M for _ in phis]
    for i in range(len(phis) - 1):
        for j in range(M):
            if keep[i][j]:
                for ii, jj in ((i, j), (i, (j + 1) % M), (i + 1, j), (i + 1, (j + 1) % M)):
                    used[ii][jj] = True
    TH = [[360.0 * j / M for j in range(M)] for _ in phis]
    for i, phi in enumerate(phis):
        h = half(phi)
        for side in (1, -1):
            cols = [j for j in range(M) if used[i][j] and side * _signed(360.0 * j / M) > 0]
            if not cols or used[i][0]:
                continue
            cb = min(cols, key=lambda j: abs(_signed(360.0 * j / M)))
            if used[i][(cb - side) % M]:
                continue
            TH[i][cb] = (side * _snap_edge(abs(_signed(360.0 * cb / M)), h, dth)) % 360.0
    rings = []
    for i, phi in enumerate(phis):
        sps, coords, ws = [], [], []
        for j in range(M):
            th = TH[i][j]
            o, d = fr.head_ray(phi, th)
            sp = fr.fit(o, d, fit_regions, fallback=ALL, guess=0.1)
            back = max(0.0, -math.cos(math.radians(th)))
            room = p.get("back_room", 0.02) * back * float(np.clip(phi / 110.0, 0.0, 1.0)) ** 1.5
            fold = p.get("folds", 0.0) * float(np.clip((phi - 90.0) / 40.0, 0.0, 1.0)) * (0.5 + 0.5 * math.cos(5 * math.radians(th)))
            sp.off = (float(np.interp(phi, [a for a, _ in off_tab], [b for _, b in off_tab])) + room + fold) * s
            sps.append(sp)
            coords.append({"part": "hood", "row": i, "col": j})
            t = float(np.clip((phi - neck_phi) / blend, 0.0, 1.0))
            ws.append(F.top_weights({"DEF-spine.006": 1.0 - t, "DEF-spine.004": 0.6 * t, "DEF-spine.003": 0.4 * t}))
        rings.append([int(v) for v in g.add_surface(sps, coords, weights=ws)])
    tip = p.get("tip")
    if tip:
        # The cowl's point: the top rings pulled back (and a little up) behind the crown.
        for i, ring in enumerate(rings[:3]):
            k = 1.0 - i / 3.0
            for sh in g.shapes:
                P = g.positions(sh)
                for j, v in enumerate(ring):
                    th = 360.0 * j / M
                    back = max(0.0, -math.cos(math.radians(th))) ** 2
                    P[v] += np.array([0.0, tip[0] * s * k * back, tip[1] * s * k * back])
                g.pos[sh] = [P]
    # Every ring vertex outside the outermost head or neck surface along its
    # ray from the head centre (the throat rings' rays graze the jaw: a
    # vertex offset from the jaw can land inside the neck, deeper than the
    # clearance pass reaches).
    _radial_exit(g, fr, [v for r in rings for v in r], p.get("min_clear", 0.008) + 0.006)
    for i in range(len(rings) - 1):
        for j in range(M):
            th = _signed(360.0 * (j + 0.5) / M)
            if abs(th) < min(half(phis[i]), half(phis[i + 1])):
                continue
            j1 = (j + 1) % M
            f = [rings[i][j], rings[i][j1], rings[i + 1][j1], rings[i + 1][j]]
            cen = g.positions("base")[f].mean(axis=0)
            fi = g.shape_face(f, {"part": "hood", "row": i, "col": j, "u": th, "t": (i + 0.5) / (len(rings) - 1)},
                              cen - fr.head_centre)
            fs.shape_faces.append(fi)
    # The crown: closed by a centre vertex over the top ring.
    cen_pts = {sh: g.positions(sh)[rings[0]].mean(axis=0)[None] + np.array([[0.0, 0.0, 0.004]]) for sh in g.shapes}
    cid = int(g.add(cen_pts, [{"DEF-spine.006": 1.0}])[0])
    for j in range(M):
        f = [rings[0][j], rings[0][(j + 1) % M], cid]
        cen = g.positions("base")[f].mean(axis=0)
        fi = g.shape_face(f, {"part": "hood", "row": -1, "col": j, "u": _signed(360.0 * (j + 0.5) / M), "t": 0.0}, cen - fr.head_centre)
        fs.shape_faces.append(fi)
    fs.anchors["crown"] = Anchor(cid)
    loops = F.boundary_loops([g.faces[f] for f in fs.shape_faces])
    P = g.positions("base")
    loops.sort(key=lambda lp: float(P[lp][:, 1].mean()))  # most forward first
    if loops:
        fs.loops["face"] = Loop([int(v) for v in loops[0]], True, "out")
        # The opening's half-angle changes by more than a column between
        # rings at its sides, so its loop steps along whole grid cells even
        # with the edge vertices snapped (the fur roll round it showed the
        # steps as square notches, 2026-11-11): rounded along the loop
        # (Taubin, so it keeps its size), then kept clear of the head.
        face = np.array(fs.loops["face"].ids, dtype=np.int64)
        for sh in g.shapes:
            P = g.positions(sh)
            Q = P[face].copy()
            for _ in range(p.get("face_smooth", 8)):
                for lam in (0.5, -0.53):
                    Q = Q + lam * (0.5 * (np.roll(Q, 1, axis=0) + np.roll(Q, -1, axis=0)) - Q)
            P[face] = Q
            g.pos[sh] = [P]
        _radial_exit(g, fr, face.tolist(), p.get("min_clear", 0.008) + 0.006)
    if len(loops) > 1:
        fs.loops["hem"] = Loop([int(v) for v in loops[-1]], True, "down")
    fs.anchors["throat"] = Anchor(int(rings[-1][0]))
    fs.hides = list(p.get("hides", ["scalp", "ears"]))
    fs.stats = {"rows": len(rings), "cols": M, "head_scale": round(float(s), 3)}
    fs.stats["clearance_moves"] = F.enforce_clearance(g, range(g.n), p.get("min_clear", 0.008))
    fs.stats["face_clearance_moves"] = F.enforce_face_clearance(g, fs.shape_faces, p.get("min_clear", 0.008))
    return fs


def _radial_exit(g: Garment, fr: BodyFrame, ids, clear: float) -> int:
    """Move vertices out along their ray from the head centre to `clear`
    beyond the last head/neck surface that ray crosses. Returns moves."""
    from mathutils import Vector

    tree, _ = fr.bvh(("head", "scalp", "ears", "neck"))
    hc = np.asarray(fr.head_centre, dtype=np.float64)
    moved = 0
    for s in g.shapes:
        P = g.positions(s)
        for v in ids:
            d = P[v] - hc
            L = float(np.linalg.norm(d))
            if L < 1e-6:
                continue
            u = d / L
            last, start = 0.0, 0.0
            for _ in range(8):
                loc, nrm, idx, dist = tree.ray_cast(Vector(hc + u * (start + 1e-4)), Vector(u), 0.5)
                if loc is None:
                    break
                start += dist + 1e-4
                last = start
            if last and L < last + clear:
                P[v] = hc + u * (last + clear)
                moved += 1
        g.pos[s] = [P]
    return moved


BUILDERS = {"top": build_top, "legs": build_legs, "skirt": build_skirt, "shoe": build_shoe, "boot": build_boot, "cap": build_cap,
            "rigid": build_rigid, "hood": build_hood}


def build(fr: BodyFrame, shape_name: str, shape_def: dict, options: dict | None = None) -> FittedShape:
    """Fit one shape (with the given options) to a body."""
    params = dict(shape_def["params"])
    for k, v in (options or {}).items():
        allowed = shape_def.get("options", {}).get(k)
        if allowed is not None and v not in allowed:
            raise ValueError(f"{shape_name}: option {k}={v!r} not in {allowed}")
        params[k] = v
        # `variants`: per option value, parameter overrides (a tapered cut,
        # a slouchier cap, a taller boot shaft...), fitted by the same builder.
        over = params.get("variants", {}).get(k, {}).get(str(v).lower() if isinstance(v, bool) else v)
        if over:
            for pk, pv in over.items():
                params[pk] = dict(params[pk], **pv) if isinstance(pv, dict) and isinstance(params.get(pk), dict) else pv
    if shape_def.get("status") != "built" or shape_def["builder"] not in BUILDERS:
        raise ValueError(f"shape {shape_name} has no builder yet ({shape_def.get('status')})")
    return BUILDERS[shape_def["builder"]](fr, params, shape_name)
