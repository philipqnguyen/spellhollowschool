"""Checks of built kitted cells against the hard rules (docs/art/castle-kit.md
"Coordinates, grid and scale", "Budgets"). Pure Python.

    python3 -I tools/blender/castle_kit/lint.py [--cells a,b] [--json]

builds every kitted cell (assemble.Builder) and prints, per cell:

- intrusion: below 2.4 m above any walkable surface, kit geometry enters
  it by at most 0.25 m (walk-through pieces exempt; floors, borders and
  thresholds lie on it; a flight's treads on its own stair);
- doors: every door edge of the cell keeps a clear box 1 m wide (centred,
  and 0.3 m inside each jamb) x 2.4 m high through the passage and 0.25 m
  into the rooms on both sides;
- overhead: lanterns, banners and other hanging pieces leave 2.4 m;
- treads: every flight's tread centres within 2 cm of the ramp;
- coverage: every Planner wall face of the cell is covered by drawn faces
  within 0.05 m (openings by their pieces: glass, niche backs, leaves);
- z-fighting (coplanar, same-facing, overlapping faces, also against the
  neighbouring cells' greybox) and degenerate faces;
- UVs inside their trim strip, fit tile or props-atlas slot;
- on wall (castle kit M2): shafts, lanterns, windows, niches, blind doors
  and open leaves only where the Planner draws wall behind them (never on a
  railing edge);
- budgets: draws, LOD0 triangles (the cap is an error, the low target a
  warning), the collider's triangles; castle kit M4: budgets shared by
  several cells (the Grand Staircase's two) summed, with the triangles
  drawn from every gallery (LOD1 beyond each mesh's distance) capped;
- castle D1 (docs/art/castle-kit.md "Shading: the AO bake"): every kit mesh carries a
  grey COLOR_0 (r = g = b, whole bytes, x within [SHADE_MIN, 1]); the edge
  loops within the cell's loop budget (`grid.slice_allowance`, a budget over
  several cells summed), the Grand Staircase's drawn estimate grown by at most
  SLICE_DRAWN_GROWTH; and every COLOR_0 byte of every cell file of the export
  (`--world`, default client/public/assets/world) grey, a = 255, but
  `floor_grounds` (grid.COLOR0_HUE_EXCEPTIONS).

Exits non-zero on errors.

    python3 -I tools/blender/castle_kit/lint.py --compare-glb OLD_DIR NEW_DIR

compares two exports of `client/public/assets/world` instead: every cell
that is not kitted must keep its geometry byte for byte (every attribute
accessor and the indices of every mesh; the props kit's `<cell>.kit` mesh
and material may be renamed `castle_props` and lose its embedded images).
Castle D1: a cell whose COLOR_0 alone differs (the greyed value ramps) is
reported as "colour only" and passes; `--strict` fails it too.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_BLENDER, os.path.join(_BLENDER, "world")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import layout_lib as L  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402

FLOOR_LIKE = 0.02  # faces this close to a walkable floor and facing up lie on it
SAMPLE = 0.5  # coverage sample spacing on wall faces (plus hole edges)
COVER_BEHIND = 1.2  # an opening's piece may close it this far behind the face


@dataclass
class Report:
    cell: str
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


# -- polygon clipping ---------------------------------------------------------------------


def clip(poly, n, d):
    """Sutherland-Hodgman: the part of `poly` with dot(n, p) >= d."""
    out = []
    m = len(poly)
    for i in range(m):
        a, b = poly[i], poly[(i + 1) % m]
        da, db = geom.dot(n, a) - d, geom.dot(n, b) - d
        if da >= 0:
            out.append(a)
        if (da >= 0) != (db >= 0):
            t = da / (da - db)
            out.append(tuple(a[k] + (b[k] - a[k]) * t for k in range(3)))
    return out


def clip_box(poly, planes):
    for n, d in planes:
        poly = clip(poly, n, d)
        if len(poly) < 3:
            return []
    return poly


def _size(poly) -> float:
    """Area of a clipped polygon, or its extent when it is flat on a plane edge."""
    if len(poly) < 3:
        return 0.0
    a = geom.norm(geom.newell(poly)) / 2
    if a > 1e-7:
        return a
    return max(geom.norm(geom.sub(p, q)) for p in poly for q in poly) * 1e-4


# -- checks ---------------------------------------------------------------------------------


def floor_plane(s: L.Surface):
    """(n, d) of a surface's walking plane: y = a x + b z + c as n.p = d."""
    if not s.sloped:
        return (0.0, 1.0, 0.0), s.y
    if s.rise in ("+x", "-x"):
        k = (s.y_top - s.y) / (s.x1 - s.x0) * (1 if s.rise == "+x" else -1)
        x_ref = s.x0 if s.rise == "+x" else s.x1
        # y = s.y + k (x - x_ref)
        return (-k, 1.0, 0.0), s.y - k * x_ref
    k = (s.y_top - s.y) / (s.z1 - s.z0) * (1 if s.rise == "+z" else -1)
    z_ref = s.z0 if s.rise == "+z" else s.z1
    return (0.0, 1.0, -k), s.y - k * z_ref


def check_intrusion(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    worst = 0.0
    hits = {}
    lim = G.INTRUSION_MAX
    surfaces = list(layout.surfaces)
    lo, hi = cb.bounds()
    near = [s for s in surfaces if s.x1 >= lo[0] - 1 and s.x0 <= hi[0] + 1 and s.z1 >= lo[2] - 1 and s.z0 <= hi[2] + 1
            and s.floor_max >= lo[1] - 3 and s.floor_min <= hi[1] + 1]
    flights = {u.id: u.run for u in cb.uses if u.role == "flight"}
    for f in cb.faces:
        if f.walkthrough or f.src in ("base", "floor"):
            continue
        xs = [p[0] for p in f.pts]
        zs = [p[2] for p in f.pts]
        ys = [p[1] for p in f.pts]
        for s in near:
            if max(xs) <= s.x0 + lim or min(xs) >= s.x1 - lim or max(zs) <= s.z0 + lim or min(zs) >= s.z1 - lim:
                continue
            if flights.get(f.src) == s.id:
                continue
            n, d = floor_plane(s)
            if min(ys) > s.floor_max + G.INTRUSION_HEIGHT or max(ys) < s.floor_min - 0.01:
                continue
            nrm = f.normal()
            if nrm[1] > 0.99 and all(abs(geom.dot(n, p) - d) <= FLOOR_LIKE for p in f.pts):
                continue
            # (Faces lying on the limit itself, a balustrade's rail at 0.25 m, are within it.)
            planes = [((1.0, 0.0, 0.0), s.x0 + lim + 1e-4), ((-1.0, 0.0, 0.0), -(s.x1 - lim - 1e-4)),
                      ((0.0, 0.0, 1.0), s.z0 + lim + 1e-4), ((0.0, 0.0, -1.0), -(s.z1 - lim - 1e-4)),
                      (n, d + 0.01), ((-n[0], -n[1], -n[2]), -(d + G.INTRUSION_HEIGHT))]
            part = clip_box(f.pts, planes)
            if _size(part) > 1e-6:
                depth = lim + max(min(p[0] - s.x0 - lim, s.x1 - lim - p[0], p[2] - s.z0 - lim, s.z1 - lim - p[2])
                                  for p in part)
                worst = max(worst, depth)
                key = (f.src, s.id)
                hits[key] = max(hits.get(key, 0.0), depth)
    for (src, sid), depth in sorted(hits.items())[:20]:
        rep.errors.append(f"intrusion: {src} enters {sid} by {depth:.2f} m below {G.INTRUSION_HEIGHT} m")
    rep.stats["intrusion_max"] = round(_max_relief(cb, layout), 3)


def _max_relief(cb: A.CellBuild, layout: L.Layout) -> float:
    """The deepest kit relief into the cell's walkable surfaces below 2.4 m
    (what the rule allows up to 0.25)."""
    worst = 0.0
    for s in layout.surfaces:
        if s.cell != cb.cell or s.kind == "door":
            continue
        n, d = floor_plane(s)
        for f in cb.faces:
            if f.walkthrough or f.src in ("base", "floor") or f.src.startswith("flight"):
                continue
            for p in f.pts:
                h = p[1] - (d - n[0] * p[0] - n[2] * p[2])
                if not (-0.01 < h < G.INTRUSION_HEIGHT):
                    continue
                if not (s.x0 - 1e-6 <= p[0] <= s.x1 + 1e-6 and s.z0 - 1e-6 <= p[2] <= s.z1 + 1e-6):
                    continue
                if f.normal()[1] > 0.99 and abs(h) <= FLOOR_LIKE:
                    continue
                depth = min(p[0] - s.x0, s.x1 - p[0], p[2] - s.z0, s.z1 - p[2])
                worst = max(worst, depth)
    return worst


def check_doors(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    """A clear box through every door edge of the cell."""
    n = 0
    for op in cb.plan.openings:
        if op.kind != "arch":
            continue
        door = layout.by_id(op.door)
        depth = (door.x1 - door.x0) if op.axis == "x" else (door.z1 - door.z0)
        y0 = door.y
        for name, (t0, t1) in (("centre", (op.centre - G.DOOR_CLEAR_WIDTH / 2, op.centre + G.DOOR_CLEAR_WIDTH / 2)),
                               ("jambs", (op.t0 + G.ARCH_CLEAR_INSET, op.t1 - G.ARCH_CLEAR_INSET))):
            # Along the normal: from 0.25 m into the room to 0.25 m beyond the passage.
            c_room, c_far = op.c + op.sign * 0.25, op.c - op.sign * (depth + 0.25)
            ca, cb_ = sorted((c_room, c_far))
            if op.axis == "z":
                box = [((1, 0, 0), t0), ((-1, 0, 0), -t1), ((0, 0, 1), ca), ((0, 0, -1), -cb_)]
            else:
                box = [((0, 0, 1), t0), ((0, 0, -1), -t1), ((1, 0, 0), ca), ((-1, 0, 0), -cb_)]
            box += [((0, 1, 0), y0 + 0.01), ((0, -1, 0), -(y0 + G.DOOR_CLEAR_HEIGHT))]
            for f in cb.faces:
                if f.src in ("base", "floor") or f.walkthrough:
                    continue
                if f.normal()[1] > 0.99 and all(abs(p[1] - y0) <= FLOOR_LIKE for p in f.pts):
                    continue
                part = clip_box(f.pts, box)
                if _size(part) > 1e-6:
                    rep.errors.append(f"door {op.door} ({name}): {f.src} blocks the {t1 - t0:.2f} x "
                                      f"{G.DOOR_CLEAR_HEIGHT} m clear box")
                    break
        n += 1
    rep.stats["doors_checked"] = n


def check_overhead(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    worst = math.inf
    for u in cb.uses:
        if u.role not in ("lantern",) and not u.piece.startswith(("kit_banner", "kit_lantern")):
            continue
        ys = [p[1] for f in cb.faces if f.src == u.id for p in f.pts]
        if not ys:
            continue
        floor = max((s.floor_max for s in layout.surfaces if s.contains(u.origin[0], u.origin[2], 0.6)
                     and s.floor_max <= u.origin[1] + 0.01), default=None)
        if floor is None:
            continue
        clear = min(ys) - floor
        worst = min(worst, clear)
        if clear < G.OVERHEAD_CLEARANCE - 1e-6:
            rep.errors.append(f"overhead: {u.id} hangs to {clear:.2f} m above the floor")
    rep.stats["overhead_min"] = None if worst == math.inf else round(worst, 3)


def check_treads(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    worst = 0.0
    for u in cb.uses:
        if u.role != "flight":
            continue
        s = layout.by_id(u.run)
        pl = geom.Placement(u.origin, u.yaw)
        piece = cb.pieces[u.id]
        w = piece.meta and u.kwargs()["width"]
        for zm, top in piece.meta["treads"]:
            for xf in (0.25, 0.5, 0.75):
                p = pl.point((w * xf, top, zm))
                err = abs(p[1] - s.height(p[0], p[2]))
                worst = max(worst, err)
                if err > G.TREAD_RAMP_TOLERANCE + 1e-9:
                    rep.errors.append(f"treads: {u.id} tread at {zm:.2f} m is {err:.3f} m off the ramp")
                    break
    rep.stats["tread_error_max"] = round(worst, 4)


def check_degenerate(cb: A.CellBuild, rep: Report) -> None:
    bad = [f for f in cb.faces if f.area() < 1e-6 or len(f.pts) < 3]
    for f in bad[:10]:
        rep.errors.append(f"degenerate face in {f.src} ({f.material}) at {[tuple(round(c, 3) for c in p) for p in f.pts[:2]]}")
    rep.stats["degenerate"] = len(bad)


def _plane_key(pts):
    n = geom.unit(geom.newell(pts))
    if geom.norm(n) < 0.5:
        return None, None
    d = geom.dot(n, pts[0])
    return (round(n[0], 3) + 0.0, round(n[1], 3) + 0.0, round(n[2], 3) + 0.0, round(d / 0.002)), n


def _flat2d(pts, n):
    ax = max(range(3), key=lambda k: abs(n[k]))
    return [tuple(p[k] for k in range(3) if k != ax) for p in pts]


def check_zfight(cb: A.CellBuild, others, rep: Report, limit: int = 20) -> None:
    """Coplanar, same-facing faces that overlap (within the cell, and
    against neighbouring cells' greybox polygons `others`)."""
    groups: dict = {}
    for f in cb.faces:
        if f.lod == "lod1":
            continue
        key, n = _plane_key(f.pts)
        if key is None:
            continue
        groups.setdefault(key, []).append((f.src + ":" + f.material, _flat2d(f.pts, n), True))
    for name, poly in others:
        key, n = _plane_key(poly)
        if key is not None and key in groups:
            groups[key].append((name, _flat2d(poly, n), False))
    issues = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        for i, (na, pa, mine_a) in enumerate(items):
            boxa = (min(p[0] for p in pa), min(p[1] for p in pa), max(p[0] for p in pa), max(p[1] for p in pa))
            for nb, pb, mine_b in items[i + 1:]:
                if not (mine_a or mine_b):
                    continue
                if na == nb and na.startswith("props:"):
                    # Castle kit M3: a props.json kit model against itself (the Dueling Club's
                    # stage, inlay, benches and banners the Great Hall keeps as they are) is
                    # world/kit art, not the castle kit's geometry; its own overlaps are not ours.
                    continue
                boxb = (min(p[0] for p in pb), min(p[1] for p in pb), max(p[0] for p in pb), max(p[1] for p in pb))
                if boxa[2] <= boxb[0] + 1e-3 or boxb[2] <= boxa[0] + 1e-3 or boxa[3] <= boxb[1] + 1e-3 or \
                        boxb[3] <= boxa[1] + 1e-3:
                    continue
                if L._sat_overlap(pa, pb):
                    issues.append(f"z-fighting: {na} and {nb} overlap on plane {key[:3]}")
                    if len(issues) >= limit:
                        break
            if len(issues) >= limit:
                break
    rep.errors.extend(issues)
    rep.stats["zfight"] = len(issues)


def _ray_hit(orig, d, f_pts, f_n) -> float | None:
    den = geom.dot(f_n, d)
    if abs(den) < 1e-9:
        return None
    t = geom.dot(f_n, geom.sub(f_pts[0], orig)) / den
    if t < 0:
        return None
    p = (orig[0] + d[0] * t, orig[1] + d[1] * t, orig[2] + d[2] * t)
    m = len(f_pts)
    sgn = 0
    for i in range(m):
        a, b = f_pts[i], f_pts[(i + 1) % m]
        c = geom.dot(geom.cross(geom.sub(b, a), geom.sub(p, a)), f_n)
        if abs(c) < 1e-9:
            continue
        s = 1 if c > 0 else -1
        if sgn == 0:
            sgn = s
        elif s != sgn:
            return None
    return t


def _in_poly(x: float, y: float, poly) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def check_coverage(cb: A.CellBuild, builder: A.Builder, rep: Report) -> None:
    """Every Planner wall face of the cell (M1.0, no holes) is covered by
    drawn faces facing the room within COVERAGE_TOLERANCE (behind it by up
    to COVER_BEHIND where an opening's piece closes the hole)."""
    walls = [poly for (c, mat), polys in builder.plan0.polys.items() if c == cb.cell and mat in A.WALLS for poly in polys]
    drawn = [f for f in cb.faces if f.lod != "lod1"]
    # Buckets of drawn faces by plan cell (1 m).
    buckets: dict = {}
    for f in drawn:
        n = f.normal()
        xs, zs = [p[0] for p in f.pts], [p[2] for p in f.pts]
        for ix in range(math.floor(min(xs)), math.floor(max(xs)) + 1):
            for iz in range(math.floor(min(zs)), math.floor(max(zs)) + 1):
                buckets.setdefault((ix, iz), []).append((f, n))
    missing = 0
    samples = 0
    worst = None
    holes = A.wall_holes(cb.plan)
    tol = G.COVERAGE_TOLERANCE
    for poly in walls:
        n = geom.unit(geom.newell(poly))
        axis = "x" if abs(n[0]) > 0.5 else "z"
        ts = [p[2] if axis == "x" else p[0] for p in poly]
        ys = [p[1] for p in poly]
        c = poly[0][0] if axis == "x" else poly[0][2]
        t0, t1, y0, y1 = min(ts), max(ts), min(ys), max(ys)
        pts = []
        nt, ny = max(1, round((t1 - t0) / SAMPLE)), max(1, round((y1 - y0) / SAMPLE))
        for i in range(nt):
            for j in range(ny):
                pts.append((t0 + (i + 0.5) * (t1 - t0) / nt, y0 + (j + 0.5) * (y1 - y0) / ny))
        for h in holes:
            if h.axis != axis or abs(h.c - c) > 1e-6:
                continue
            for e in (h.t0 + tol * 0.8, h.t1 - tol * 0.8, h.t0 - tol * 0.8, h.t1 + tol * 0.8):
                k = max(1, round((h.y1 - h.y0) / 0.25))
                pts += [(e, h.y0 + (j + 0.5) * (h.y1 - h.y0) / k) for j in range(k)]
            for e in (h.y0 + tol * 0.8, h.y1 - tol * 0.8, h.y1 + tol * 0.8):
                k = max(1, round((h.t1 - h.t0) / 0.25))
                pts += [(h.t0 + (j + 0.5) * (h.t1 - h.t0) / k, e) for j in range(k)]
        outline = [((q[2] if axis == "x" else q[0]), q[1]) for q in poly]
        pts = outer_cut_free_m5c(cb, builder, axis, c, pts)  # Castle M5c (integration): the outer-shell cut
        pts = coverage_free_p3c(cb, axis, c, pts)  # The bridge, the parapet
        for t, y in pts:
            if not (t0 - 1e-6 <= t <= t1 + 1e-6 and y0 - 1e-6 <= y <= y1 + 1e-6) or not _in_poly(t, y, outline):
                continue
            samples += 1
            p = (c, y, t) if axis == "x" else (t, y, c)
            orig = (p[0] + n[0] * 0.5, p[1], p[2] + n[2] * 0.5)
            d = (-n[0], 0.0, -n[2])
            hit = False
            # Faces from 0.5 m in front to COVER_BEHIND behind: their buckets.
            keys = []
            for k in (-0.5, 0.0, 0.4, 0.8, COVER_BEHIND):
                key = (math.floor(p[0] - n[0] * k), math.floor(p[2] - n[2] * k))
                if key not in keys:
                    keys.append(key)
            cand = [x for key in keys for x in buckets.get(key, [])]
            for f, fn in cand:
                if geom.dot(fn, n) <= 0.2:
                    continue
                tt = _ray_hit(orig, d, f.pts, fn)
                if tt is not None and 0.5 - 0.5 <= tt <= 0.5 + COVER_BEHIND:
                    hit = True
                    break
            if not hit:
                missing += 1
                if worst is None:
                    worst = tuple(round(v, 2) for v in p)
    if missing:
        rep.errors.append(f"coverage: {missing} of {samples} wall samples uncovered (first at {worst})")
    rep.stats["coverage_samples"] = samples
    rep.stats["coverage_missing"] = missing


def check_uvs(cb: A.CellBuild, builder: A.Builder, rep: Report) -> None:
    bad = 0
    first = None
    for f in cb.faces:
        if f.mode == "trim":
            sheet = builder.sheet(f.material)
            st = sheet["strips"][f.arg]
            w, h = sheet["px"]
            pad = sheet["pad"]
            top, bottom = st["rows"]
            vlo, vhi = 1 - (bottom - pad) / h, 1 - (top + pad) / h
            c0, c1 = st.get("cols", [0, w])
            ok = all(vlo - 1e-6 <= v <= vhi + 1e-6 for _, v in f.uv)
            if (c0, c1) != (0, w):
                ok = ok and all((c0 + pad) / w - 1e-6 <= u <= (c1 - pad) / w + 1e-6 for u, _ in f.uv)
        elif f.mode == "fit":
            sheet = builder.sheet(f.material)
            x, y, tw, th = sheet["tiles"][f.arg]
            w, h = sheet["px"]
            pad = sheet["pad"]
            ulo, uhi = (x + pad) / w, (x + tw - pad) / w
            vlo, vhi = 1 - (y + th - pad) / h, 1 - (y + pad) / h
            ok = all(ulo - 1e-6 <= u <= uhi + 1e-6 and vlo - 1e-6 <= v <= vhi + 1e-6 for u, v in f.uv)
        elif f.mode == "kit":
            tile, _, colour = f.arg.partition(":")
            u0, v0, tw, th = builder.atlas.rect(tile, colour or "-")
            ok = all(u0 - 1e-6 <= u <= u0 + tw + 1e-6 and v0 - 1e-6 <= v <= v0 + th + 1e-6 for u, v in f.uv)
        else:
            ok = all(math.isfinite(u) and math.isfinite(v) for u, v in f.uv)
        if not ok:
            bad += 1
            first = first or f"{f.src} {f.material} {f.mode}:{f.arg} {f.uv[:2]}"
    if bad:
        rep.errors.append(f"uvs: {bad} faces outside their strip, tile or slot (first: {first})")
    rep.stats["uv_bad"] = bad


def check_window_depth(cb: A.CellBuild, rep: Report) -> None:
    """A window's reveals reach exactly the Planner's outer face (its hole
    in the shell): no gap to the outside, nothing poking through."""
    bad = 0
    for u in cb.uses:
        if u.role != "window":
            continue
        op = next(o for o in cb.plan.openings if u.id == f"window:{o.id}")
        depth = u.kwargs()["depth"]
        plane = op.c - op.sign * depth
        k = 0 if op.axis == "x" else 2
        outer = [f for f in cb.faces if f.src == "base" and f.material.startswith("castle_wall")
                 and all(abs(p[k] - plane) < 1e-6 for p in f.pts) and f.normal()[k] * -op.sign > 0.99]
        near = [f for f in outer if any(op.t0 - 0.01 <= (p[2] if k == 0 else p[0]) <= op.t1 + 0.01 for p in f.pts)]
        if not near:
            bad += 1
            rep.errors.append(f"window {op.id}: no outer wall face at depth {depth:.2f} m (the shell's hole)")
    rep.stats["window_depth_bad"] = bad


def check_on_wall(cb: A.CellBuild, builder: A.Builder, rep: Report) -> None:
    """Castle kit M2 (the railing-edge rule): every shaft and wall lantern
    stands on Planner wall from the floor to the top (grid.SHAFT_ON_WALL
    either side), every window, niche and blind door has wall behind its
    whole rectangle, and open leaves lying on a wall face have wall behind
    them: nothing hangs on a railing edge or over a drop."""
    pieces = builder.wall_pieces()
    runs = {r.id: r for r in cb.plan.runs}
    bad = []
    shafts = {sh.id: sh for sh in cb.plan.shafts}
    for u in cb.uses:
        if u.role == "shaft":
            sh = shafts[u.id.split(":", 1)[1]]
            spans = G.walled_spans(pieces, runs[sh.run], "full")
            if not any(a - 1e-6 <= sh.t - G.SHAFT_ON_WALL and sh.t + G.SHAFT_ON_WALL <= b + 1e-6 for a, b in spans):
                bad.append(u.id)
        elif u.role == "lantern":
            lt = next(x for x in cb.plan.lanterns if u.id == f"lantern:{x.id}")
            if lt.shaft == "":
                # Castle kit M4: a spot lantern on wall any surface owns (its bracket plate).
                r = runs[lt.run]
                if not G.face_walled(pieces, r.axis, r.c, r.sign, lt.t - 0.3, lt.t + 0.3, lt.y - 0.4, lt.y + 0.2):
                    bad.append(u.id)
            elif f"shaft:{lt.shaft}" not in {v.id for v in cb.uses}:
                bad.append(u.id)
    for op in cb.plan.openings:
        if op.kind == "arch":
            if op.leaf_mode == "face":
                w = op.leaf_width
                for za, zb in ((op.t0 - G.ARCH_SURROUND - w, op.t0 - G.ARCH_SURROUND),
                               (op.t1 + G.ARCH_SURROUND, op.t1 + G.ARCH_SURROUND + w)):
                    if not G.face_covered(pieces, None, op.axis, op.c, op.sign, za, zb, op.y0, op.y1, cell=cb.cell):
                        bad.append(f"leaves:{op.id}")
            continue
        y0 = op.y0 if op.kind == "window" else (runs[op.run].floor if op.run else op.y0)
        if not G.face_covered(pieces, op.surface, op.axis, op.c, op.sign, op.t0, op.t1, y0, op.y1):
            bad.append(op.id)
    for b in bad[:20]:
        rep.errors.append(f"on wall: {b} has no Planner wall behind it (a railing edge or a drop)")
    rep.stats["off_wall"] = len(bad)


def check_budget(cb: A.CellBuild, rep: Report) -> None:
    b = G.BUDGETS[cb.plan.budget]
    tris, draws = cb.triangles(), A.budget_draws(cb)  # One house's cup banners counted
    rep.stats.update(tris=tris, draws=draws, nodes=cb.draws(), collider_tris=cb.collider_triangles(),
                     materials=sorted(m.material for m in cb.meshes.values()))
    cap = G.draw_cap(cb.cell, cb.plan.budget)  # Castle M5c (contracts): + FACADE_DRAWS on a facing cell
    if draws > cap:
        rep.errors.append(f"budget: {draws} draws (budget {cap})")
    if tris > b.tris[1]:
        rep.errors.append(f"budget: {tris} triangles (cap {b.tris[1]})")
    elif tris < b.tris[0]:
        rep.warnings.append(f"budget: {tris} triangles, under the {b.tris[0]} target")
    if cb.collider_triangles() > G.COLLIDER_TRIS:
        rep.errors.append(f"collider: {cb.collider_triangles()} triangles (cap {G.COLLIDER_TRIS})")
    grey = sorted({m.material for m in cb.meshes.values() if not m.material.startswith("castle_")})
    if grey:
        rep.warnings.append(f"greybox materials kept (props the cell keeps): {grey}")


def neighbours(cb: A.CellBuild, builder: A.Builder):
    lo, hi = cb.bounds()
    out = []
    for (c, mat), polys in builder.plan0.polys.items():
        if c == cb.cell:
            continue
        for poly in polys:
            if all(lo[k] - 0.01 <= p[k] <= hi[k] + 0.01 for p in poly for k in range(3)) or any(
                    all(lo[k] - 0.01 <= p[k] <= hi[k] + 0.01 for k in range(3)) for p in poly):
                out.append((f"{c}.{mat}", poly))
    return out


def lint_cell(cb: A.CellBuild, builder: A.Builder) -> Report:
    rep = Report(cb.cell)
    layout = builder.layout
    check_budget(cb, rep)
    check_degenerate(cb, rep)
    check_intrusion(cb, layout, rep)
    check_doors(cb, layout, rep)
    check_overhead(cb, layout, rep)
    check_treads(cb, layout, rep)
    check_zfight(cb, neighbours(cb, builder), rep)
    check_coverage(cb, builder, rep)
    check_uvs(cb, builder, rep)
    check_window_depth(cb, rep)
    check_on_wall(cb, builder, rep)
    # Castle kit M3 (the Great Hall): areas kept clear, braziers' and candles' overhead.
    check_keep_clear(cb, builder, rep)
    check_overhead_m3(cb, layout, rep)
    # Castle D1: the baked COLOR_0 and the loop budget.
    check_shade(cb, rep)
    check_loops(cb, rep)
    check_furniture_m5b(cb, layout, rep)  # Castle M5b (Furniture): knee room, floor seats
    check_dungeons_m5b(cb, layout, rep)  # Castle M5b (Dungeons): hanging pieces, torches, the cauldrons
    check_upper_m5b(cb, layout, rep)  # Castle M5b (Upper rooms): the armillary, the dome, the cases' depth
    check_facades_m5c(cb, builder, rep)  # Castle M5c (Facades): the exterior clones, facade pieces, LOD1
    check_courtyard_m5c(cb, layout, rep)  # Castle M5c (Courtyard): lanes, the window, the yard, heights
    check_exterior_p3a(cb, builder, rep)  # The courtyard's grounds side, the corbels
    check_p3c_wings_a(cb, builder, rep)  # The bridge's walls, the swing nodes
    check_wings_b_p3c(cb, builder, rep)  # The Owl Loft's outside and its owl markers
    check_secrets_p3d(cb, builder, rep)  # The passages' stair feet, the entrance nodes
    check_floor_under_f1(cb, layout, rep)  # Drawn floor under every walkable surface
    check_house_entrances_f1(cb, layout, rep)  # The common rooms' entrances move
    return rep


# -- castle D1 (docs/art/castle-kit.md "Shading: the AO bake") --------------------------------------------


def check_shade(cb: A.CellBuild, rep: Report) -> None:
    """Every kit mesh (LOD1 nodes too) carries COLOR_0 on every corner, grey
    (r = g = b), a whole byte (byte / 255, so the packing keeps it), x within
    [SHADE_MIN, 1]; meshes of greybox materials the cell keeps carry none."""
    meshes = list(cb.meshes.values()) + [md for md, _, _ in cb.lod_meshes.values()]
    bad = []
    corners = 0
    for md in meshes:
        if not md.material.startswith("castle_"):
            if md.colors is not None:
                bad.append(f"{md.name}: COLOR_0 on a greybox material")
            continue
        if md.colors is None or len(md.colors) != len(md.faces):
            bad.append(f"{md.name}: no COLOR_0")
            continue
        for face, cols in zip(md.faces, md.colors):
            if len(cols) != len(face):
                bad.append(f"{md.name}: COLOR_0 corners do not match its faces")
                break
            for r, g, b in cols:
                corners += 1
                byte = round(r * 255.0)
                if not (r == g == b) or abs(r * 255.0 - byte) > 1e-4 or not G.shade_byte(G.SHADE_MIN) <= byte <= 255:
                    bad.append(f"{md.name}: COLOR_0 ({r}, {g}, {b}) is not a grey shade byte")
                    break
            else:
                continue
            break
    for b in bad[:10]:
        rep.errors.append(f"shade: {b}")
    st = cb.shade_stats
    rep.stats["shade"] = {"corners": corners, "baked": bool(st and st.baked), "x_min": st.x_min if st else None}


def check_loops(cb: A.CellBuild, rep: Report) -> None:
    """The edge loops of one cell within its share of the loop budget."""
    st = cb.slice_stats
    if st is None:
        rep.warnings.append("loops: the cell was built without edge loops")
        return
    allow = A.Builder.slice_budget(cb.plan.budget, st.unsliced)
    rep.stats["loops"] = {"added": st.added, "allowance": allow, "unsliced": st.unsliced, "planes": st.planes}
    if st.added > allow:
        rep.errors.append(f"loops: +{st.added} LOD0 triangles over the cell's loop budget {allow}")
    if G.BUDGETS[cb.plan.budget].cells == 1 and st.added > G.slice_allowance(cb.plan.budget, st.unsliced):
        rep.errors.append(f"loops: +{st.added} LOD0 triangles (allowance {G.slice_allowance(cb.plan.budget, st.unsliced)})")


def colour0_files(world_dir: str) -> tuple[list[str], dict]:
    """Every COLOR_0 byte of every cell file in `world_dir` (the export:
    kitted and greybox cells alike) is grey with a = 255, but the materials
    of grid.COLOR0_HUE_EXCEPTIONS. Returns (problems, {material: corners})."""
    problems, seen = [], {}
    for name in sorted(os.listdir(world_dir)):
        if not name.endswith(".glb"):
            continue
        doc, binary = _glb(os.path.join(world_dir, name))
        for mesh in doc.get("meshes", []):
            for p in mesh.get("primitives", []):
                mat = doc["materials"][p["material"]]["name"] if "material" in p else "-"
                for key, idx in sorted(p["attributes"].items()):
                    if not key.startswith("COLOR_"):
                        continue
                    raw = _colour_bytes(doc, binary, idx) if doc["accessors"][idx]["componentType"] != 5121 else \
                        _accessor(doc, binary, idx)
                    seen[mat] = seen.get(mat, 0) + len(raw) // 4
                    if doc["accessors"][idx]["type"] != "VEC4" or doc["accessors"][idx]["componentType"] != 5121:
                        problems.append(f"{name}: {mesh.get('name')} {key} is not normalized RGBA bytes")
                    if mat.split(".")[0] in G.COLOR0_HUE_EXCEPTIONS:
                        continue
                    if not G.colour0_grey(raw):
                        problems.append(f"{name}: {mesh.get('name')} ({mat}) {key} has a non-grey byte")
    return problems, dict(sorted(seen.items()))


# -- castle kit M3 (the Great Hall, docs/art/castle-kit.md) ------------------------


def check_keep_clear(cb: A.CellBuild, builder: A.Builder, rep: Report) -> None:
    """No kit geometry below 2.4 m inside a kept-clear area (castle_kit.json
    `keep_clear`: the Dueling Club's stage, area and spectator spots) but
    the area's own props.json kit, the Planner's faces and the floors."""
    hits = 0
    for aid, lo, hi in cb.plan.keep_clear:
        own = {f"props:{e.id}" for e in builder.kit_entries if e.cell == cb.cell
               and lo[0] <= e.pos[0] <= hi[0] and lo[2] <= e.pos[2] <= hi[2]}
        y1 = lo[1] + 0.5 + G.INTRUSION_HEIGHT
        box = [((1.0, 0.0, 0.0), lo[0] + 1e-4), ((-1.0, 0.0, 0.0), -(hi[0] - 1e-4)), ((0.0, 0.0, 1.0), lo[2] + 1e-4),
               ((0.0, 0.0, -1.0), -(hi[2] - 1e-4)), ((0.0, 1.0, 0.0), lo[1]), ((0.0, -1.0, 0.0), -y1)]
        bad = set()
        for f in cb.faces:
            if f.src in ("base", "floor") or f.src in own or f.lod == "lod1":
                continue
            if _size(clip_box(f.pts, box)) > 1e-6:
                bad.add(f.src)
        for src in sorted(bad)[:10]:
            rep.errors.append(f"keep clear: {src} stands in area {aid} below {G.INTRUSION_HEIGHT} m")
        hits += len(bad)
    rep.stats["keep_clear_hits"] = hits


def check_overhead_m3(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    """The overhead rule for the beast braziers' baskets and the floating
    candles (at the bottom of their bob): at least 2.4 m over the floor
    under every point."""
    worst = math.inf
    surfaces = [s for s in layout.surfaces if s.cell == cb.cell]
    for u in cb.uses:
        amp = 0.0
        if u.role == "candles":
            spec = cb.plan.groups.get(u.group)
            amp = dict(spec.anim).get("amp", 0.0) if spec is not None else 0.0
        elif not u.piece.startswith("kit_beast_brazier"):
            continue
        low = None
        for f in cb.faces:
            if f.src != u.id:
                continue
            for x, y, z in f.pts:
                floors = [s.height(x, z) for s in surfaces if s.contains(x, z, 0.0) and s.floor_min <= y]
                if not floors:
                    continue
                clear = y - amp - max(floors)
                if low is None or clear < low:
                    low = clear
        if low is None:
            continue
        worst = min(worst, low)
        if low < G.OVERHEAD_CLEARANCE - 1e-6:
            rep.errors.append(f"overhead: {u.id} comes to {low:.2f} m over the floor")
    rep.stats["overhead_m3_min"] = None if worst == math.inf else round(worst, 3)


def _glb(path: str):

    with open(path, "rb") as f:
        data = f.read()
    jlen = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + jlen])
    return doc, data[20 + jlen + 8:]


def _accessor(doc, binary: bytes, i: int) -> bytes:
    a = doc["accessors"][i]
    bv = doc["bufferViews"][a["bufferView"]]
    comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    size = {5126: 4, 5125: 4, 5123: 2, 5121: 1}[a["componentType"]] * comps
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride", size)
    return b"".join(binary[start + k * stride:start + k * stride + size] for k in range(a["count"]))


def _colour_bytes(doc, binary: bytes, i: int) -> bytes:
    """A COLOR_n accessor as the RGBA bytes `visibility.pack_color0` writes (castle kit M2, 15.5).

    Float, unsigned-short and byte colours of the same linear values compare
    equal, so an M1 export (float VEC3) and an M2 one (normalized bytes) of
    the same geometry are the same cell."""
    a = doc["accessors"][i]
    comps = {"VEC3": 3, "VEC4": 4}[a["type"]]
    fmt, scale = {5126: ("f", 1.0), 5123: ("H", 65535.0), 5121: ("B", 255.0)}[a["componentType"]]
    raw = _accessor(doc, binary, i)
    vals = struct.unpack(f"<{len(raw) // struct.calcsize(fmt)}{fmt}", raw)
    out = bytearray()
    for k in range(0, len(vals), comps):
        px = [vals[k + c] / scale for c in range(comps)] + ([1.0] if comps == 3 else [])
        out += bytes(round(min(1.0, max(0.0, v)) * 255) for v in px[:3]) + b"\xff"
    return bytes(out)


def _meshes(path: str) -> dict:
    doc, binary = _glb(path)
    out = {}
    for node in doc.get("nodes", []):
        if "mesh" not in node:
            continue
        name = node["name"][:-4] + ".castle_props" if node["name"].endswith(".kit") else node["name"]
        prims = []
        for p in doc["meshes"][node["mesh"]]["primitives"]:
            mat = doc["materials"][p["material"]]["name"] if "material" in p else None
            prims.append(({k: (_colour_bytes if k.startswith("COLOR_") else _accessor)(doc, binary, v)
                           for k, v in sorted(p["attributes"].items())},
                          _accessor(doc, binary, p["indices"]) if "indices" in p else b"",
                          "castle_props" if mat == "kit" else mat))
        out[name] = prims
    return out


def compare_glb(old_dir: str, new_dir: str, kitted, colour_only: list | None = None) -> list[str]:
    """Differences between two exports for every cell not in `kitted`. A
    cell whose meshes differ in their COLOR_n accessors alone goes to
    `colour_only` when it is given (castle D1: the greyed ramps), else it is a
    problem too."""
    problems = []
    for name in sorted(os.listdir(new_dir)):
        cell = name[:-4]
        if not name.endswith(".glb") or cell in kitted:
            continue
        old = os.path.join(old_dir, name)
        if not os.path.exists(old):
            problems.append(f"{cell}: not in {old_dir}")
            continue
        a, b = _meshes(old), _meshes(os.path.join(new_dir, name))
        if sorted(a) != sorted(b):
            problems.append(f"{cell}: meshes {sorted(set(a) ^ set(b))}")
            continue
        diff = [k for k in a if a[k] != b[k]]
        geometry = [k for k in diff if _strip_colour(a[k]) != _strip_colour(b[k])]
        if colour_only is not None and diff and not geometry:
            colour_only.append(f"{cell}: {', '.join(diff)}")
            continue
        for k in diff:
            problems.append(f"{cell}: {k} differs")
    return problems


def _strip_colour(prims):
    return [({k: v for k, v in attrs.items() if not k.startswith("COLOR_")}, idx, mat) for attrs, idx, mat in prims]


# -- castle kit M4: budgets over several cells, the drawn estimate ---------------------------

EYE_HEIGHT = 1.45  # the drawn estimate's eye over each gallery's middle


def _aabb(verts):
    return ([min(v[k] for v in verts) for k in range(3)], [max(v[k] for v in verts) for k in range(3)])


def _box_distance(p, box) -> float:
    lo, hi = box
    return math.sqrt(sum(max(lo[k] - p[k], 0.0, p[k] - hi[k]) ** 2 for k in range(3)))


def drawn_estimate(cbs, layout: L.Layout) -> dict:
    """Castle kit M4 (docs/art/castle-kit.md): for an eye EYE_HEIGHT
    over the middle of every flat surface of the cells (each gallery, floor
    and landing), the triangles of every mesh of the cells, LOD1 where the
    mesh has one and its base's bounding box lies beyond the LOD distance,
    no frustum culling. Returns {"worst": (tris, surface), "eyes": {surface: tris}}."""
    meshes = []
    for cb in cbs:
        lods = {base: (md, d) for _, (md, base, d) in cb.lod_meshes.items()}
        for name, md in cb.meshes.items():
            lod = lods.get(name)
            meshes.append((md.triangles(), _aabb(md.verts), lod[0].triangles() if lod else None, lod[1] if lod else None))
    cells = {cb.cell for cb in cbs}
    eyes = {}
    for s in layout.surfaces:
        if s.cell not in cells or s.kind == "door" or s.sloped:
            continue
        eye = ((s.x0 + s.x1) / 2, s.y + EYE_HEIGHT, (s.z0 + s.z1) / 2)
        eyes[s.id] = sum(t0 if t1 is None or _box_distance(eye, box) <= d else t1 for t0, box, t1, d in meshes)
    worst = max(eyes.items(), key=lambda kv: (kv[1], kv[0]))
    return {"worst": (worst[1], worst[0]), "eyes": dict(sorted(eyes.items()))}


def lint_budget_group(cbs, layout: L.Layout, builder=None) -> Report:
    """A budget that covers several cells (grid.Budget.cells, the Grand
    Staircase's two): draws and LOD0 triangles summed, and the drawn
    estimate from every gallery (Budget.drawn)."""
    b = G.BUDGETS[cbs[0].plan.budget]
    rep = Report("+".join(cb.cell for cb in cbs))
    draws = sum(cb.draws() for cb in cbs)
    tris = sum(cb.triangles() for cb in cbs)
    est = drawn_estimate(cbs, layout)
    rep.stats.update(draws=draws, tris=tris, drawn_worst=est["worst"][0], drawn_worst_at=est["worst"][1],
                     lod1_meshes=sum(len(cb.lod_meshes) for cb in cbs))
    if len(cbs) != b.cells:
        rep.errors.append(f"budget {cbs[0].plan.budget}: covers {b.cells} cells, {len(cbs)} built")
    # Castle M5c (contracts): + FACADE_DRAWS for each facing cell of the group.
    cap = b.draws + sum(G.FACADE_DRAWS for cb in cbs if cb.cell in G.M5C_FACADE_CELLS)
    if draws > cap:
        rep.errors.append(f"budget: {draws} draws over the cells (budget {cap})")
    if tris > b.tris[1]:
        rep.errors.append(f"budget: {tris} triangles over the cells (cap {b.tris[1]})")
    elif tris < b.tris[0]:
        rep.warnings.append(f"budget: {tris} triangles over the cells, under the {b.tris[0]} target")
    if b.drawn is not None and est["worst"][0] > b.drawn:
        rep.errors.append(f"budget: {est['worst'][0]} triangles drawn from {est['worst'][1]} (cap {b.drawn})")
    # Castle D1: the loops over the cells together, and the drawn estimate's growth.
    if all(cb.slice_stats is not None for cb in cbs):
        added = sum(cb.slice_stats.added for cb in cbs)
        unsliced = sum(cb.slice_stats.unsliced for cb in cbs)
        allow = G.slice_allowance(cbs[0].plan.budget, unsliced)
        rep.stats["loops"] = {"added": added, "allowance": allow}
        if added > allow:
            rep.errors.append(f"loops: +{added} LOD0 triangles over the cells (allowance {allow})")
        if b.drawn is not None and builder is not None:
            was = builder.d1_slice
            builder.d1_slice = False
            try:
                before = drawn_estimate([builder.build(cb.cell) for cb in cbs], layout)["worst"][0]
            finally:
                builder.d1_slice = was
            rep.stats["drawn_before_loops"] = before
            if est["worst"][0] - before > G.SLICE_DRAWN_GROWTH:
                rep.errors.append(f"loops: the drawn estimate grows {est['worst'][0] - before} (at most {G.SLICE_DRAWN_GROWTH})")
    # Both cells are kit: their faces must not overlap where they meet (the
    # per-cell check sees only the neighbours' greybox).
    cross = Report(rep.cell)
    for i, cb in enumerate(cbs):
        others = [(f"{o.cell}:{f.src}:{f.material}", f.pts) for o in cbs[i + 1:] for f in o.faces if f.lod != "lod1"]
        if others:
            check_zfight(cb, others, cross)
    rep.errors += [e for e in cross.errors if " and " in e and ":" in e.split(" and ")[1].split(" overlap")[0]
                   and e.split(" and ")[1].split(":")[0] in {o.cell for o in cbs}]
    rep.stats["zfight_across"] = sum(1 for e in cross.errors if e.split(" and ")[1].split(":")[0] in {o.cell for o in cbs})
    return rep


def budget_groups(plan) -> dict:
    """Budget name -> the kitted cells sharing a multi-cell budget."""
    out = {}
    for cell, cp in sorted(plan.cells.items()):
        if G.BUDGETS[cp.budget].cells > 1:
            out.setdefault(cp.budget, []).append(cell)
    return out


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Lint the kitted cells of data/world/castle_kit.json")
    ap.add_argument("--cells", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--compare-glb", nargs=2, metavar=("OLD_DIR", "NEW_DIR"), default=None)
    ap.add_argument("--strict", action="store_true", help="--compare-glb: a COLOR_0-only change fails too")
    ap.add_argument("--world", default=os.path.join(A.REPO, "client", "public", "assets", "world"),
                    help="castle D1: the export whose cell files' COLOR_0 must be grey ('' skips it)")
    args = ap.parse_args(argv)
    if args.compare_glb:
        from castle_kit import schema as S

        kitted = set(S.kitted_cells())
        colour: list = []
        problems = compare_glb(args.compare_glb[0], args.compare_glb[1], kitted, None if args.strict else colour)
        n = sum(1 for f in os.listdir(args.compare_glb[1]) if f.endswith(".glb") and f[:-4] not in kitted)
        for p in problems:
            print(f"error: {p}")
        for c in colour:
            print(f"colour only: {c}")
        bad = {p.split(':')[0] for p in problems}
        print(f"{n - len(bad)} of {n} un-kitted cells keep their geometry ({len(colour)} changed in COLOR_0 only)")
        return 0 if not problems else 1
    builder = A.Builder.from_files()
    cells = args.cells.split(",") if args.cells else sorted(builder.kit_plan.cells)
    ok = True
    out = {}
    for cell in cells:
        rep = lint_cell(builder.build(cell), builder)
        ok = ok and rep.ok
        out[cell] = {"errors": rep.errors, "warnings": rep.warnings, "stats": rep.stats}
        if not args.json:
            print(f"{cell}: {'ok' if rep.ok else 'FAILED'}  {json.dumps(rep.stats, sort_keys=True)}")
            for w in rep.warnings:
                print(f"  warning: {w}")
            for e in rep.errors:
                print(f"  error: {e}")
    # Castle kit M4: budgets shared by several cells (built here if not linted above).
    for name, group in sorted(budget_groups(builder.kit_plan).items()):
        if not set(group) & set(cells):
            continue
        rep = lint_budget_group([builder.build(c) for c in group], builder.layout, builder)
        ok = ok and rep.ok
        out[rep.cell] = {"errors": rep.errors, "warnings": rep.warnings, "stats": rep.stats}
        if not args.json:
            print(f"{rep.cell} ({name} budget): {'ok' if rep.ok else 'FAILED'}  {json.dumps(rep.stats, sort_keys=True)}")
            for w in rep.warnings:
                print(f"  warning: {w}")
            for e in rep.errors:
                print(f"  error: {e}")
    # Castle D1: every COLOR_0 byte of the export grey.
    if args.world and os.path.isdir(args.world) and not args.cells:
        problems, seen = colour0_files(args.world)
        ok = ok and not problems
        out["colour0"] = {"errors": problems[:50], "corners": seen}
        if not args.json:
            print(f"COLOR_0 of {args.world}: {'ok' if not problems else 'FAILED'}  "
                  f"{sum(seen.values())} corners over {len(seen)} materials")
            for p in problems[:20]:
                print(f"  error: {p}")
    # The resident `pitch` cell (world/pitch_build.py's lint).
    if not args.cells:
        ok = lint_pitch_p3b(builder, out, args.json) and ok
    ok = lint_hlod_p3a(builder, out, args.json) and ok  # The far silhouette's budgets
    if args.json:
        print(json.dumps(out, indent=1, sort_keys=True))
    return 0 if ok else 1


# =====================================================================================
# Castle M5b (Furniture): docs/art/castle-kit.md.
# =====================================================================================


def knee_boxes(cb: A.CellBuild) -> list:
    """(use id, placement, row centre, row yaw, box) of every seat row of the
    cell's placed pieces. The knee-room box is in the row's frame (origin
    the row's centre on the piece's floor, +z the way the sitter faces):
    from grid.SEAT_FRONT_MAX to KNEE_CLEAR_AHEAD ahead, the row's width less
    KNEE_CLEAR_INSET at each end, from 3 cm over its floor (SEAT_TOP under
    the seat) to KNEE_CLEAR_TOP."""
    out = []
    for u in cb.uses:
        piece = cb.pieces.get(u.id)
        if piece is None:
            continue
        for x0, z0, x1, z1, y, yaw, *_fit in piece.meta.get("seats", []):
            floor = y - G.SEAT_TOP
            hw = (x1 - x0) / 2.0 - G.KNEE_CLEAR_INSET
            box = (-hw, floor + 0.03, G.SEAT_FRONT_MAX, hw, floor + G.KNEE_CLEAR_TOP, G.KNEE_CLEAR_AHEAD)
            out.append((u.id, geom.Placement(u.origin, u.yaw), ((x0 + x1) / 2.0, (z0 + z1) / 2.0), yaw, box))
    return out


def _to_row(p, pl: geom.Placement, centre, yaw: float):
    """A world point in a seat row's frame (geom.Placement inverted, then the row's)."""
    dx, dy, dz = p[0] - pl.origin[0], p[1] - pl.origin[1], p[2] - pl.origin[2]
    c, s = math.cos(pl.yaw), math.sin(pl.yaw)
    x, z = dx * c - dz * s - centre[0], dx * s + dz * c - centre[1]
    c, s = math.cos(yaw), math.sin(yaw)
    return (x * c - z * s, dy, x * s + z * c)


def check_furniture_m5b(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    """Castle M5b cells (grid.M5B_CELLS): nothing of the cell's drawn faces
    (the seat's own piece included) inside a seat row's knee box;
    no wall piece over a bay shaft; every floor seat at least
    FLOOR_SEAT_MIN each way, its top at most FLOOR_SEAT_HEIGHT_MAX over the
    cell's floor under it."""
    if G.m5b_rules(cb.cell) is None:  # The kitted instance rooms too
        return
    bad = []
    boxes = knee_boxes(cb)
    if boxes:
        faces = [f for f in cb.faces if f.lod != "lod1"]
        aabbs = [_aabb(f.pts) for f in faces]
        for uid, pl, centre, yaw, (bx0, by0, bz0, bx1, by1, bz1) in boxes:
            reach = math.hypot(max(abs(bx0), abs(bx1)), bz1) + 0.01
            o = pl.point((centre[0], 0.0, centre[1]))
            for f, (flo, fhi) in zip(faces, aabbs):
                if (fhi[0] < o[0] - reach or flo[0] > o[0] + reach or fhi[2] < o[2] - reach or flo[2] > o[2] + reach
                        or fhi[1] < pl.origin[1] + by0 or flo[1] > pl.origin[1] + by1):
                    continue
                lo, hi = _aabb([_to_row(p, pl, centre, yaw) for p in f.pts])
                if (hi[0] > bx0 + 1e-3 and lo[0] < bx1 - 1e-3 and hi[1] > by0 + 1e-3 and lo[1] < by1 - 1e-3
                        and hi[2] > bz0 + 1e-3 and lo[2] < bz1 - 1e-3):
                    bad.append(f"knee room: {f.src} reaches into the knee box of {uid}")
                    break
    # Wall pieces (meta "wall": the noticeboard, the dorm door, ...) stand between the bay shafts.
    shafts = [u for u in cb.uses if u.role == "shaft"]
    for u in cb.uses:
        meta = cb.pieces[u.id].meta if u.id in cb.pieces else {}
        if u.role != "hero" or "wall" not in meta:
            continue
        x0, x1, _, _ = meta["wall"]
        pl = geom.Placement(u.origin, u.yaw)
        for sh in shafts:
            x, _, z = _to_row(sh.origin, pl, (0.0, 0.0), 0.0)
            if abs(z) < 0.5 and x0 - G.SHAFT.height / 2 < x < x1 + G.SHAFT.height / 2:
                bad.append(f"wall piece {u.id} covers {sh.id} (stand it between the bay shafts)")
                break
    floors = [s for s in layout.surfaces if s.cell == cb.cell and s.kind != "door"]
    for x0, z0, x1, z1, y in getattr(cb, "floor_seats", []):
        if x1 - x0 < G.FLOOR_SEAT_MIN - 1e-6 or z1 - z0 < G.FLOOR_SEAT_MIN - 1e-6:
            bad.append(f"floor seat {[x0, z0, x1, z1]}: under {G.FLOOR_SEAT_MIN} m")
        cx, cz = (x0 + x1) / 2.0, (z0 + z1) / 2.0
        under = [s for s in floors if s.x0 <= cx <= s.x1 and s.z0 <= cz <= s.z1]
        if not under or not 0.0 <= y - max(s.floor_max for s in under) <= G.FLOOR_SEAT_HEIGHT_MAX + 1e-6:
            bad.append(f"floor seat {[x0, z0, x1, z1]}: its top {y} is not within {G.FLOOR_SEAT_HEIGHT_MAX} m over the floor")
    for b in bad[:20]:
        rep.errors.append(b)
    rep.stats["knee_boxes"] = len(boxes)
    rep.stats["floor_seats"] = len(getattr(cb, "floor_seats", []))


# =====================================================================================
# Castle M5b (Dungeons): docs/art/castle-kit.md "The kitted cells", "Pieces".
# =====================================================================================

DUNGEON_HANGING = ("kit_pans_hanging", "kit_lamp_hanging")  # all of them over OVERHEAD_CLEARANCE


def check_dungeons_m5b(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    """The dungeon cells (grid.M5B_CELLS "dungeons"): every
    hanging pan rack and lamp keeps OVERHEAD_CLEARANCE over the floor under
    it, a wall torch's bracket stands 2.6-3.2 m up; a barrel vault's crown stays at or under
    the ceiling of the floor under it; the Potions cauldrons
    (grid.M5B_KEEP_KIT) are never replaced."""
    if G.m5b_rules(cb.cell) != "dungeons":
        return
    floors = [s for s in layout.surfaces if s.cell == cb.cell and s.kind != "door"]
    worst = math.inf
    by_src: dict = {}
    for f in cb.faces:
        by_src.setdefault(f.src, []).append(f)
    for u in cb.uses:
        if u.role != "hero":
            continue
        piece = cb.pieces.get(u.id)
        if u.piece in DUNGEON_HANGING:
            ys = [q[1] for f in by_src.get(u.id, []) for q in f.pts]
            under = [s for s in floors if s.contains(u.origin[0], u.origin[2], 0.0) and s.floor_max <= max(ys, default=0) + 1e-6]
            if not ys or not under:
                rep.errors.append(f"hanging: {u.id} has no floor under it")
                continue
            clear = min(ys) - max(s.floor_max for s in under)
            worst = min(worst, clear)
            if clear < G.OVERHEAD_CLEARANCE - 1e-6:
                rep.errors.append(f"hanging: {u.id} reaches {clear:.2f} m over the floor (at least {G.OVERHEAD_CLEARANCE} m)")
        elif u.piece == G.TORCH_WALL and piece is not None:
            # (below 2.4 m a torch stays within the intrusion limit of the wall: check_intrusion)
            if not 2.6 - 1e-6 <= piece.meta["bracket"] <= 3.2 + 1e-6:
                rep.errors.append(f"torch: {u.id}'s bracket stands {piece.meta['bracket']} m up (2.6-3.2)")
        elif u.piece.startswith(G.VAULT_BARREL + "_") and piece is not None:
            crown = u.origin[1] + piece.meta["rise"]
            under = [s for s in floors if s.contains(u.origin[0], u.origin[2], 0.0) and s.roofed]
            if under and crown > min(s.ceil_y for s in under) + 1e-6:
                rep.errors.append(f"vault: {u.id}'s crown at {crown:.2f} rises over the ceiling ({min(s.ceil_y for s in under):.2f})")
    for kid in G.M5B_KEEP_KIT:
        if kid in cb.plan.replaces:
            rep.errors.append(f"keep: {kid} is an interactable position and stays (grid.M5B_KEEP_KIT)")
    rep.stats["hanging_min"] = None if worst == math.inf else round(worst, 3)


# =====================================================================================
# Castle M5b (Upper rooms): docs/art/castle-kit.md "Pieces".
# =====================================================================================

UPPER_DEPTH = {"kit_bookcase": 0.5, "kit_trophy_case": 0.6}  # against a wall: how far each stands into the room


def check_upper_m5b(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    """The Upper rooms' pieces in any cell: the armillary's lowest point
    at least OVERHEAD_CLEARANCE over the floor under it; a dome's panels and
    ribs between the springing line and the cell's ceiling; the bookcases
    and trophy cases at most UPPER_DEPTH deep, their backs on a wall face."""
    from castle_kit import pieces as P

    bad = []
    flat = [s for s in layout.surfaces if s.cell == cb.cell and s.kind != "door" and not s.sloped]
    for u in cb.uses:
        if u.role != "hero":
            continue
        fam = P.parse(u.piece)[0]
        ys = [p[1] for f in cb.faces if f.src == u.id for p in f.pts]
        if not ys:
            continue
        x, y, z = u.origin
        under = [s for s in flat if s.x0 - 1e-6 <= x <= s.x1 + 1e-6 and s.z0 - 1e-6 <= z <= s.z1 + 1e-6]
        if fam == G.ARMILLARY:
            floor = max((s.floor_max for s in under), default=y)
            hang = ys
            if min(hang) - floor < G.OVERHEAD_CLEARANCE - 1e-6:
                bad.append(f"upper: {u.id} hangs to {min(hang) - floor:.2f} m over the floor")
            rep.stats["armillary_clear"] = round(min(hang) - floor, 3)
        elif fam == G.DOME_STARRY:
            ceil = min((s.ceil_y for s in under if s.roofed), default=None)
            top = max(ys + [p[1] for f in cb.faces if f.src == u.id and f.material == "castle_sky" for p in f.pts])
            if ceil is not None and top > ceil + 1e-6:
                bad.append(f"upper: {u.id} rises to {top:.2f}, over the ceiling {ceil:.2f}")
            if min(ys) < y - 1e-6:
                bad.append(f"upper: {u.id} hangs below its springing line")
            rep.stats["dome_top"] = round(top, 3)
        elif fam in UPPER_DEPTH:
            piece = cb.pieces[u.id]
            zs = [q[2] for f in piece.faces for q in f.pts]
            if max(zs) > UPPER_DEPTH[fam] + 1e-6 or min(zs) < -1e-6:
                bad.append(f"upper: {u.id} stands {max(zs):.2f} m deep (at most {UPPER_DEPTH[fam]})")
    for b in bad[:20]:
        rep.errors.append(b)


# =====================================================================================
# Castle M5c (Facades): docs/art/castle-kit.md "Exterior".
# =====================================================================================

FACADE_PIECE_CAPS = {  # LOD0 triangles per piece (grid.py's caps; the parapet's per metre)
    G.BUTTRESS: G.BUTTRESS_TRIS, G.PINNACLE: G.PINNACLE_TRIS, G.ROOF_SLATE: G.ROOF_TRIS,
    G.TRACERY_OUTER: G.TRACERY_OUTER_TRIS, G.BLIND_WINDOW: G.BLIND_WINDOW_TRIS,
}


def outer_cut_free_m5c(cb: A.CellBuild, builder: A.Builder, axis: str, c: float, pts: list) -> list:
    """(Integration, castle-kit.md) The coverage samples (t, y) of a wall
    plane `axis = c` less those in the outer-shell cut: points the
    no-holes plan (plan0) walls but the kitted plan (plan1) leaves open and
    no window, niche or blind door of the cell's own covers. The cut's
    reveal is closed in the owner's cell (the hall), not here."""
    k = 0 if axis == "x" else 2
    walls1 = []
    for (cell, mat), polys in builder.plan1.polys.items():
        if cell != cb.cell or mat not in A.WALLS:
            continue
        for poly in polys:
            if all(abs(q[k] - c) < 1e-6 for q in poly):
                walls1.append([((q[2] if axis == "x" else q[0]), q[1]) for q in poly])
    holes = [h for h in A.wall_holes(cb.plan) if h.axis == axis and abs(h.c - c) < 1e-6]
    out = []
    for t, y in pts:
        if any(_in_poly(t, y, w) for w in walls1) \
                or any(h.t0 - 0.5 <= t <= h.t1 + 0.5 and h.y0 - 0.5 <= y <= h.y1 + 0.5 for h in holes):
            out.append((t, y))
    return out


def check_facades_m5c(cb: A.CellBuild, builder: A.Builder, rep: Report) -> None:
    """Castle M5c (Facades): the exterior clones only where a facade asks for
    them, and on every outside face there (the Planner's wall_exterior and roof
    faces, every face of the facade pieces); each facade piece within its
    triangle cap and drawing an LOD1 at grid.FACADE_LOD; a facing cell's facade
    pieces about grid.FACADE_TRIS (a guideline: a warning); the buttresses
    clear of every opening's reveal on their face; nothing of the facades in
    front of the window over the front doors (grid.FRONT_WINDOW_OUTSIDE) but
    its outer tracery."""
    cp = cb.plan
    fa = cp.facade
    mats = sorted({m.material for m in cb.meshes.values()} | {md.material for md, _, _ in cb.lod_meshes.values()})
    outdoor_mats = [m for m in mats if G.is_outdoor_material(m)]
    allowed = set(G.OUTDOOR_MATERIALS) & set(cp.materials.values())  # (the courtyard's paving by its materials)
    if fa is None or not fa.ext:
        bad = [m for m in outdoor_mats if m.endswith(G.EXT_SUFFIX) or (m not in allowed and fa is None)]
        if bad:
            rep.errors.append(f"facades: outdoor materials {bad} in a cell without a facade's ext")
        return
    uses = {u.id: u for u in cb.uses if u.role == "facade" or A.facade_piece(u.piece)}
    outdoor = A.R.is_outdoor_cell(builder.layout, cb.cell)
    inside = [f for f in cb.faces if f.material.startswith("castle_") and not G.is_outdoor_material(f.material)
              and G.ext_base(f.material) not in G.EXT_NEVER
              and (outdoor or f.src in uses or (f.src == "base" and f.ramp in ("wall_exterior", "-")))]
    for f in inside[:5]:
        rep.errors.append(f"facades: an outside face of {f.src} on {f.material}, not its exterior clone")
    tris = 0
    for uid, u in sorted(uses.items()):
        piece = cb.pieces[uid]
        n = piece.triangles()
        fam = A.P.parse(u.piece)[0]
        cap = FACADE_PIECE_CAPS.get(u.piece, FACADE_PIECE_CAPS.get(fam))
        if u.piece == G.PARAPET_CRENEL:
            cap = G.PARAPET_TRIS_PER_M * u.kwargs()["length"]
        if cap is not None and n > cap:
            rep.errors.append(f"facades: {uid} has {n} triangles (cap {cap:.0f})")
        if A.facade_piece(u.piece) and (piece.lod1 is None or u.lod_distance != G.FACADE_LOD and uid.startswith("facade:")):
            rep.errors.append(f"facades: {uid} draws no LOD1 at {G.FACADE_LOD} m")
        tris += n
    for name, (md, base, dist) in sorted(cb.lod_meshes.items()):
        if base.count(".") == 1 and G.is_outdoor_material(md.material) and dist != G.FACADE_LOD:
            rep.errors.append(f"facades: {name} switches at {dist} m, not {G.FACADE_LOD}")
    rep.stats["facade_tris"] = tris
    if not outdoor and tris > G.FACADE_TRIS:
        rep.warnings.append(f"facades: {tris} triangles of facade pieces (guideline {G.FACADE_TRIS})")
    # Buttresses clear of the openings' reveals on their face.
    for uid, u in sorted(uses.items()):
        if A.P.parse(u.piece)[0] != G.BUTTRESS:
            continue
        pts = [p for f in cb.faces if f.src == uid for p in f.pts]
        axis = "z" if abs(math.sin(u.yaw)) < 0.5 else "x"
        k = 0 if axis == "z" else 2
        t0, t1 = min(p[k] for p in pts), max(p[k] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        for op in cp.openings:
            if op.axis != axis or op.kind == "arch" or abs(op.c - (u.origin[2] if axis == "z" else u.origin[0])) > G.WALL_MASS_MAX:
                continue
            if min(t1, op.t1) - max(t0, op.t0) > 1e-6 and min(y1, op.y1) - max(y0, op.y0) > 1e-6:
                rep.errors.append(f"facades: {uid} crosses the reveal of {op.id}")
    # The window over the front doors stays open from the courtyard (its outer tracery excepted):
    # nothing of the facades in front of it, and in the courtyard nothing at all (its cornice and
    # string courses break there, its arcades stop short).
    x0, x1, wy0, wy1 = G.FRONT_WINDOW_OUTSIDE
    zc = G.COURT_RECT[1]
    for f in cb.faces:
        if f.src in ("base", "floor") or (f.src in uses and uses[f.src].piece == G.TRACERY_OUTER):
            continue
        if f.src not in uses and not outdoor:
            continue
        box = [((1, 0, 0), x0 + 0.01), ((-1, 0, 0), -(x1 - 0.01)), ((0, 1, 0), wy0), ((0, -1, 0), -(wy1 + 2.0)),
               ((0, 0, 1), zc - 1.3), ((0, 0, -1), -(zc + 3.0))]
        if _size(clip_box(f.pts, box)) > 1e-6:
            rep.errors.append(f"facades: {f.src} stands in front of the window over the front doors "
                              f"(grid.FRONT_WINDOW_OUTSIDE)")
            break


# =====================================================================================
# Castle M5c (Courtyard): docs/art/castle-kit.md "The kitted cells".
# =====================================================================================


def _hero_hits(cb: A.CellBuild, rect, y0: float, y1: float, prefix: str = "hero:") -> list[str]:
    """Hero placements (ids starting `prefix`) with LOD0 faces inside the box
    rect (x0, z0, x1, z1) between the heights y0 and y1."""
    x0, z0, x1, z1 = rect
    box = [((1.0, 0.0, 0.0), x0 + 1e-4), ((-1.0, 0.0, 0.0), -(x1 - 1e-4)), ((0.0, 0.0, 1.0), z0 + 1e-4),
           ((0.0, 0.0, -1.0), -(z1 - 1e-4)), ((0.0, 1.0, 0.0), y0), ((0.0, -1.0, 0.0), -y1)]
    out = set()
    for f in cb.faces:
        if not f.src.startswith(prefix) or f.lod == "lod1":
            continue
        if _size(clip_box(f.pts, box)) > 1e-6:
            out.add(f.src)
    return sorted(out)


def check_courtyard_m5c(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    """The courtyard: no hero below OVERHEAD_CLEARANCE in the kept-clear lanes
    (grid.COURT_KEEP_CLEAR), nothing in front of the traceried window's hole
    (grid.FRONT_WINDOW_OUTSIDE, 4 m out from the north wall), the stalls in
    the Exchange Yard, the planters' crowns, the portcullis's teeth and the
    gatehouse's top, the arcades' depth and apex, the lights (at most
    kit.LIGHTS_PER_CELL, one id each). The hall: the court_* heroes inside the
    court (grid.EH_COURT) and out of its kept-clear parts below
    OVERHEAD_CLEARANCE."""
    import kit

    bad = []
    if cb.cell in G.M5C_CELLS:
        y0 = min(s.y for s in layout.surfaces if s.cell == cb.cell)
        clear = G.OVERHEAD_CLEARANCE
        for lane, rect in sorted(G.COURT_KEEP_CLEAR.items()):
            for src in _hero_hits(cb, rect, y0 + 0.01, y0 + clear):
                bad.append(f"courtyard: {src} stands in the {lane} lane below {clear} m")
        wx0, wx1, wy0, wy1 = G.FRONT_WINDOW_OUTSIDE
        north = G.COURT_RECT[1]
        for src in _hero_hits(cb, (wx0, north, wx1, north + 4.0), y0 + wy0, y0 + wy1):
            bad.append(f"courtyard: {src} stands in front of the traceried window")
        heights = {}
        yx0, yz0, yx1, yz1 = G.EXCHANGE_YARD
        for u in cb.uses:
            if u.role != "hero":
                continue
            meta = cb.pieces[u.id].meta
            pts = [q for f in cb.faces if f.src == u.id and f.lod != "lod1" for q in f.pts]
            if u.piece == G.MARKET_STALL:
                if any(not (yx0 - 1e-6 <= q[0] <= yx1 + 1e-6 and yz0 - 1e-6 <= q[2] <= yz1 + 1e-6) for q in pts):
                    bad.append(f"courtyard: {u.id} reaches out of the Exchange Yard")
            elif u.piece == G.PLANTER_TREE:
                low = u.origin[1] + meta["crown_low"] - y0
                heights.setdefault("crown_low", low)
                heights["crown_low"] = min(heights["crown_low"], low)
                if low < clear - 1e-6:
                    bad.append(f"courtyard: {u.id}'s crown comes down to {low:.2f} m")
            elif u.piece == G.GATEHOUSE:
                heights["teeth"] = meta["teeth"]
                heights["gatehouse_top"] = max(q[1] for q in pts) - y0
                if meta["teeth"] < G.PORTCULLIS_CLEAR - 1e-6:
                    bad.append(f"courtyard: the portcullis's teeth hang to {meta['teeth']:.2f} m")
                if not G.GATEHOUSE_TOP[0] - 1e-6 <= heights["gatehouse_top"] <= G.GATEHOUSE_TOP[1] + 1e-6:
                    bad.append(f"courtyard: the gatehouse tops out at {heights['gatehouse_top']:.2f} m")
            elif u.piece == G.ARCADE_BAY:
                if not (G.ARCADE_DEPTH[0] <= meta["depth"] <= G.ARCADE_DEPTH[1]
                        and G.ARCADE_APEX[0] <= meta["apex"] <= G.ARCADE_APEX[1]):
                    bad.append(f"courtyard: {u.id} is {meta['depth']} m deep with its apex at {meta['apex']} m")
            elif u.piece == G.FOUNTAIN:
                if abs(u.origin[0] - G.FOUNTAIN_CENTRE[0]) > 1e-6 or abs(u.origin[2] - G.FOUNTAIN_CENTRE[1]) > 1e-6:
                    bad.append(f"courtyard: {u.id} is off the Courtyard Fountain landmark")
        ids = [lt["id"] for lt in cb.lights]
        if len(ids) > kit.LIGHTS_PER_CELL or len(set(ids)) != len(ids):
            bad.append(f"courtyard: lights {ids} (at most {kit.LIGHTS_PER_CELL}, one id each)")
        rep.stats["m5c_courtyard"] = {k: round(v, 3) for k, v in sorted(heights.items())}
    if cb.cell == "entrance_hall":
        cx0, cz0, cx1, cz1 = G.EH_COURT
        for u in cb.uses:
            if not u.id.startswith("hero:court_"):
                continue
            pts = [q for f in cb.faces if f.src == u.id for q in f.pts]
            if any(not (cx0 - 1e-6 <= q[0] <= cx1 + 1e-6 and cz0 - 1e-6 <= q[2] <= cz1 + 1e-6) for q in pts):
                bad.append(f"court: {u.id} reaches out of the Owl Exchange court")
        y0 = min(s.y for s in layout.surfaces if s.cell == cb.cell)
        for part, rect in sorted(G.EH_COURT_KEEP_CLEAR.items()):
            for src in _hero_hits(cb, rect, y0 - 0.01, y0 + G.OVERHEAD_CLEARANCE, "hero:court_"):
                bad.append(f"court: {src} stands in the {part} kept clear")
    for b in bad[:20]:
        rep.errors.append(b)


# =====================================================================================
# Docs/art/castle-kit.md "Exterior". The courtyard's grounds side (its
# outer faces in the outside stone, the corbels under the gatehouse's overhang) and the
# far silhouette (world/hlod_build.py: the chunk budgets, the chunk-to-cell table).
# =====================================================================================


def check_exterior_p3a(cb: A.CellBuild, builder: A.Builder, rep: Report) -> None:
    """An outdoor cell with an `ext` facade (the courtyard): every drawn
    wall-stone face beyond one of its outer planes is in
    grid.P3A_EXTERIOR_STONE's clone, and none inside them; a gatehouse has a
    corbel table (`kit_corbel_table_*`) whose tops meet its overhang's
    underside (the wall top) on the wall's outer face."""
    cp = cb.plan
    if cp.facade is None or not cp.facade.ext or not A.R.is_outdoor_cell(builder.layout, cb.cell):
        return
    stone = G.ext_name(G.P3A_EXTERIOR_STONE)
    planes = A.outer_planes_p3a(builder, cb.cell)
    wrong = outside = 0
    for f in cb.faces:
        if f.mode != "world" or not f.material.startswith("castle_wall"):
            continue
        beyond = any(min((p[k] - c) * sign for p in f.pts) >= -1e-6 for k, c, sign in planes)
        if beyond:
            outside += 1
        if beyond != (f.material == stone):
            wrong += 1
    if wrong:
        rep.errors.append(f"exterior: {wrong} wall faces on the wrong side of the outer planes for {stone}")
    gate = [u for u in cb.uses if u.role == "hero" and u.piece == G.GATEHOUSE]
    corbels = [u for u in cb.uses if u.role == "hero" and _family_p3a(u.piece) == "kit_corbel_table"]
    if gate and not corbels:
        rep.errors.append("exterior: no corbel table under the gatehouse's overhang")
    for u in corbels:
        meta = cb.pieces[u.id].meta
        top = u.origin[1] + meta["top"]
        on_plane = any(abs(u.origin[k] - c) < 1e-6 for k, c, _ in planes)
        if abs(top - (min(s.y for s in builder.layout.surfaces if s.cell == cb.cell) + G.COURT_WALL_TOP)) > 0.05 \
                or not on_plane:
            rep.errors.append(f"exterior: {u.id} is not under the overhang on the outer face")
    rep.stats["p3a_exterior"] = {"outside_faces": outside, "corbels": sum(cb.pieces[u.id].meta["corbels"] for u in corbels)}


def _family_p3a(name: str) -> str:
    from castle_kit import pieces as P

    return P.parse(name)[0]


def lint_hlod_p3a(builder: A.Builder, out: dict, quiet: bool) -> bool:
    """The far silhouette (docs/art/castle-kit.md "Exterior"): 6-10 chunks, at most
    2 draws a chunk on the two library materials, 60 k triangles and 16 MB
    over all, grey COLOR_0, and every playable castle cell in exactly one
    chunk or in `no_outside`."""
    import hlod_build as HB

    with open(os.path.join(A.REPO, "data", "world", "exterior.json"), encoding="utf-8") as f:
        exterior = json.load(f)
    chunks = HB.build(builder.layout, exterior, builder.kit_plan)
    problems = HB.report(chunks, exterior)
    seen = [c for ch in exterior["chunks"] for c in ch["cells"]] + list(exterior["no_outside"])
    # The pitch stands on the grounds (its stands' steps are stairs), not in the castle.
    castle = {s.cell for s in builder.layout.surfaces if s.kind != "grounds"} - {G.P3B_PITCH_CELL}
    if len(seen) != len(set(seen)):
        problems.append("a cell in two chunks")
    if set(seen) != castle:
        problems.append(f"chunk table: missing {sorted(castle - set(seen))}, unknown {sorted(set(seen) - castle)}")
    entry = HB.manifest_entry(chunks, exterior)
    mb = HB.gpu_bytes([md for m in chunks.values() for md in m.values()]) / (1024 * 1024)
    stats = {"chunks": len(entry["chunks"]), "tris": entry["tris"], "draws": sum(c["draws"] for c in entry["chunks"]),
             "gpu_mb": round(mb, 2), "per_chunk": {c["id"]: [c["tris"], c["draws"]] for c in entry["chunks"]}}
    out["hlod"] = {"errors": problems, "stats": stats}
    if not quiet:
        print(f"hlod: {'ok' if not problems else 'FAILED'}  {json.dumps(stats, sort_keys=True)}")
        for p in problems:
            print(f"  error: {p}")
    return not problems


# =====================================================================================
# Docs/architecture.md. The Flying Pitch's resident cell
# (world/pitch_build.py): its budgets (grid P3B_PITCH_*), every node's LOD1, grey COLOR_0,
# the turf and markings within 2 cm of the terrace, the stands and towers inside their
# no-fly boxes, the decks' tops on the layout's decks and the seats on them.
# =====================================================================================


def lint_pitch_p3b(builder: A.Builder, out: dict, quiet: bool) -> bool:
    import pitch_build as PB

    terrain = os.path.join(A.REPO, "data", "world", "terrain")
    pt = PB.build(builder.layout, terrain, builder.textures)
    errs, warns, rep = PB.lint(pt, builder.layout, terrain)
    stats = {"tris": pt.tris(), "draws": pt.draws(), "lod1_tris": pt.lod1_tris(), "seats": len(pt.seats)}
    out[pt.cell] = {"errors": errs, "warnings": warns, "stats": stats}
    if not quiet:
        print(f"{pt.cell}: {'ok' if not errs else 'FAILED'}  {json.dumps(stats, sort_keys=True)}")
        for w in warns:
            print(f"  warning: {w}")
        for e in errs:
            print(f"  error: {e}")
    return not errs


# =====================================================================================
# Docs/art/castle-kit.md.
# =====================================================================================

SWING_B_TOLERANCE = 0.02  # a swing node's B pose matches the layout's B surface within 2 cm


def coverage_free_p3c(cb: A.CellBuild, axis: str, c: float, pts: list) -> list:
    """Coverage samples the cells leave bare on purpose: the bridge kind's
    Planner walls (its hero is the bridge) and, in the Clock Tower Courtyard, its
    south wall over the parapet (rules.CLOCK_PARAPET)."""
    from castle_kit import rules as R

    if cb.plan.budget == "bridge":
        return []
    if cb.cell == "clock_courtyard" and axis == "z" and abs(c - A.CLOCK_SOUTH[0]) < 1e-6:
        return [(t, y) for t, y in pts if y <= R.CLOCK_PARAPET + 0.01]
    return pts


def swing_b_error(cb: A.CellBuild, layout: L.Layout, flight: dict) -> float | None:
    """How far the swing node's treads, turned by the flight's angle about its pivot,
    lie from the layout's B surface: the largest plan-bounds difference (m)."""
    from castle_kit import rules as R

    name = G.node_name(cb.cell, "castle_props", R.swing_group(flight["id"]))
    md = cb.meshes.get(name)
    if md is None:
        return None
    a = layout.by_id(flight["a"])
    treads = [f for f in md.src_faces if f.src.startswith("flight:") and f.normal()[1] > 0.99]
    if not treads:
        return None
    px, pz = flight["pivot"]
    ang = math.radians(flight["angle_deg"])
    ca, sa = math.cos(ang), math.sin(ang)
    pts = []
    for f in treads:
        for x, y, z in f.pts:
            dx, dz = x - px, z - pz
            pts.append((px + dx * ca + dz * sa, y, pz - dx * sa + dz * ca))
    b = L.load_layout(R.STAIRS_PATH.replace("stairs.json", "layout.json"), moving=True).by_id(flight["b"])
    xs, zs = [q[0] for q in pts], [q[2] for q in pts]
    ys = [q[1] for q in pts]
    err = max(abs(min(xs) - b.x0), abs(max(xs) - b.x1), abs(min(zs) - b.z0), abs(max(zs) - b.z1))
    # The treads' heights: inside the B surface's (one riser at most below its top).
    if min(ys) < min(b.y, b.y_top) - 1e-6 or max(ys) > max(b.y, b.y_top) + 1e-6:
        err = max(err, 1.0)
    del a
    return err


def check_p3c_wings_a(cb: A.CellBuild, builder: A.Builder, rep: Report) -> None:
    from castle_kit import rules as R

    # The bridge kind draws none of its Planner walls: no z-fight against them.
    bridges = {c for c, cp in builder.kit_plan.cells.items() if cp.budget == "bridge"}
    rep.errors[:] = [e for e in rep.errors if not (e.startswith("z-fighting:")
                                                   and any(f" {c}.wall" in e for c in bridges))]
    for fl in R.moving_flights(builder.layout, cb.cell):
        name = G.node_name(cb.cell, "castle_props", R.swing_group(fl["id"]))
        md = cb.meshes.get(name)
        if md is None:
            rep.errors.append(f"swing: no node {name}")
            continue
        anim = cb.extras.get(name, {}).get(G.EXTRAS_ANIM, {})
        if anim.get("kind") != G.ANIM_SWING or anim.get("flight") != fl["id"]:
            rep.errors.append(f"swing: {name} has no kit_anim swing for {fl['id']}")
        s = builder.layout.by_id(fl["a"])
        want = (float(fl["pivot"][0]), float(min(s.y, s.y_top)), float(fl["pivot"][1]))
        if md.origin is None or max(abs(a - b) for a, b in zip(md.origin, want)) > 1e-6:
            rep.errors.append(f"swing: {name} origin {md.origin}, not the pivot {want}")
        if any(G.lod1_name(name) == n for n in cb.lod_meshes):
            rep.errors.append(f"swing: {name} has a LOD1 node (a moving node draws at every distance)")
        err = swing_b_error(cb, builder.layout, fl)
        rep.stats.setdefault("swing_b_error", {})[fl["id"]] = None if err is None else round(err, 4)
        if err is None or err > SWING_B_TOLERANCE:
            rep.errors.append(f"swing: {name} at B lies {err} m from {fl['b']} (the cap: {SWING_B_TOLERANCE} m)")


# =====================================================================================
# Docs/art/castle-kit.md. The Owl Loft stands alone on the grounds
# with no facade rule (grid.M5C_FACADE_CELLS): its own shell hero draws its outside in the
# @ext stone and the slate, the only outdoor materials in it; its owls are empty marker
# nodes with their kit_anim, OWLS[0]..OWLS[1] of them.
# =====================================================================================


def check_wings_b_p3c(cb: A.CellBuild, builder: A.Builder, rep: Report) -> None:
    if cb.cell != A.P3C_OWL_LOFT:
        return
    rep.errors[:] = [e for e in rep.errors if not e.startswith("facades: outdoor materials")]
    shell = {u.id for u in cb.uses if u.piece == A.OWL_LOFT_SHELL}
    stray = sorted({f.src for f in cb.faces if G.is_outdoor_material(f.material) and f.src not in shell})
    if stray:
        rep.errors.append(f"owl_loft: outdoor materials outside the shell: {stray[:5]}")
    markers = [n for n, md in cb.meshes.items() if not md.faces]
    owls = A.owl_markers_p3c(cb)
    if len(markers) != len(owls) or not G.OWLS[0] <= len(owls) <= G.OWLS[1]:
        rep.errors.append(f"owl_loft: {len(markers)} owl markers, {len(owls)} owls (grid.OWLS {G.OWLS})")
    for name in markers:
        anim = cb.extras.get(name, {}).get(G.EXTRAS_ANIM, {})
        if anim.get("kind") != "owl" or not 0.0 <= anim.get("phase", -1.0) < 1.0:
            rep.errors.append(f"owl_loft: {name} lacks its kit_anim owl phase")
    rep.stats["p3c_owl_loft"] = {"owls": len(owls), "markers": len(markers)}


# =====================================================================================
# Docs/art/castle-kit.md. The hidden passages and their entrances.
# - A passage door opening straight onto its stair's foot (the Reader's Shortcut at the
#   library: the layout's) has the stair's first riser at the door's edge, inside the door
#   check's 0.25 m beyond the door: that flight is not counted as blocking it.
# - Every passage door's public side (a kitted cell) shows one entrance node: a castle_props
#   group `entrance_<door>`, every face walk-through, kit_anim {kind entrance, door, style}
#   with a style of grid.ENTRANCE_STYLES; l7_corridor_east has the Wishing Room's
#   door node (kit_anim wishing_room_door).
# =====================================================================================


def _p3d_stair_feet(layout) -> dict:
    """Passage door id -> the passage stair it opens straight onto (none: absent)."""
    from castle_kit import rules as R
    out = {}
    doors = R.secret_doors(layout)
    for s in layout.surfaces:
        if s.kind != "stair" or not getattr(s, "secret", False):
            continue
        for d in doors:
            dd = layout.by_id(d)
            touch_x = (abs(dd.x1 - s.x0) < 1e-6 or abs(dd.x0 - s.x1) < 1e-6) and dd.z0 < s.z1 and dd.z1 > s.z0
            touch_z = (abs(dd.z1 - s.z0) < 1e-6 or abs(dd.z0 - s.z1) < 1e-6) and dd.x0 < s.x1 and dd.x1 > s.x0
            if touch_x or touch_z:
                out[d] = s.id
    return out


def check_secrets_p3d(cb: A.CellBuild, builder: A.Builder, rep: Report) -> None:
    from castle_kit import rules as R

    layout = builder.layout
    doors = R.secret_doors(layout)
    if cb.plan.budget == "passage":
        feet = _p3d_stair_feet(layout)
        rep.errors[:] = [e for e in rep.errors if not any(
            e.startswith(f"door {d} (") and f": flight:{st} blocks" in e for d, st in feet.items())]
    mine = [op.door for op in cb.plan.openings if op.kind == "arch" and op.door in doors]
    if cb.plan.budget == "passage" or not mine:
        if cb.cell != "l7_corridor_east":
            return
    for d in mine:
        grp = R.P3D_ENTRANCE_PREFIX + d
        name = G.node_name(cb.cell, "castle_props", grp)
        md = cb.meshes.get(name)
        if md is None or not md.faces:
            rep.errors.append(f"secrets: {d} has no entrance node {name}")
            continue
        anim = cb.extras.get(name, {}).get(G.EXTRAS_ANIM, {})
        if anim.get("kind") != G.ANIM_ENTRANCE or anim.get("door") != d or anim.get("style") not in G.ENTRANCE_STYLES:
            rep.errors.append(f"secrets: {name} kit_anim {anim}")
        if any(not f.walkthrough for f in md.src_faces):
            rep.errors.append(f"secrets: {name} has faces that are not walk-through")
        if md.origin is None:
            rep.errors.append(f"secrets: {name} has no origin")
    if cb.cell == "l7_corridor_east":
        md = cb.meshes.get(G.WISHING_ROOM_DOOR_NODE)
        anim = cb.extras.get(G.WISHING_ROOM_DOOR_NODE, {}).get(G.EXTRAS_ANIM, {})
        if md is None or not md.faces or anim.get("kind") != G.ANIM_WISHING_ROOM_DOOR:
            rep.errors.append(f"secrets: no {G.WISHING_ROOM_DOOR_NODE} with kit_anim wishing_room_door")
    rep.stats["p3d_entrances"] = len(mine)


# =====================================================================================
# A drawn floor under every walkable surface (the Grand Staircase's pier
# landing at 13.5 had none: the floor compass round the pier's foot cut it out).
# =====================================================================================

FLOOR_UNDER_STEP = 0.5  # sample spacing over a surface ...
FLOOR_UNDER_INSET = 0.3  # ... this far inside its edges
FLOOR_UNDER_FLAT = 0.06  # a flat surface's drawn floor within this of its height
FLOOR_UNDER_STAIR = 0.35  # a stair's treads within this of its ramp
FLOOR_UNDER_BIN = 2.0


def floor_under_misses(cb: A.CellBuild, layout: L.Layout) -> dict:
    """Surface id -> [(x, z)] of the sample points over it (this cell's
    surfaces) with no drawn, up-facing LOD0 face at its walking height."""
    ups = []
    for f in cb.faces:
        if f.lod == "lod1":
            continue
        n = f.normal()
        if n[1] < 0.3:
            continue
        xs = [p[0] for p in f.pts]
        zs = [p[2] for p in f.pts]
        ups.append((f.pts, n, min(xs), min(zs), max(xs), max(zs)))
    bins: dict = {}
    for i, (_, _, x0, z0, x1, z1) in enumerate(ups):
        for bx in range(math.floor(x0 / FLOOR_UNDER_BIN), math.floor(x1 / FLOOR_UNDER_BIN) + 1):
            for bz in range(math.floor(z0 / FLOOR_UNDER_BIN), math.floor(z1 / FLOOR_UNDER_BIN) + 1):
                bins.setdefault((bx, bz), []).append(i)
    from castle_kit import rules as R

    swinging = {fl[k] for fl in R.stairs_file()["flights"] for k in ("a", "b")}
    out: dict = {}
    for s in layout.surfaces:
        if s.cell != cb.cell:
            continue
        # A moving flight's corners are chamfered away on purpose (assemble.SWING_CHAMFER).
        chamfers = []
        if s.id in swinging:
            cx, cz = (s.x0 + s.x1) / 2.0, (s.z0 + s.z1) / 2.0
            chamfers = [(kx, kz, 1.0 if cx > kx else -1.0, 1.0 if cz > kz else -1.0)
                        for kx in (s.x0, s.x1) for kz in (s.z0, s.z1)]
        n, d = floor_plane(s)
        tol = FLOOR_UNDER_STAIR if s.sloped else FLOOR_UNDER_FLAT
        nx = max(1, int((s.x1 - s.x0 - 2 * FLOOR_UNDER_INSET) / FLOOR_UNDER_STEP) + 1)
        nz = max(1, int((s.z1 - s.z0 - 2 * FLOOR_UNDER_INSET) / FLOOR_UNDER_STEP) + 1)
        for i in range(nx):
            for j in range(nz):
                x = s.x0 + FLOOR_UNDER_INSET + i * FLOOR_UNDER_STEP if nx > 1 else (s.x0 + s.x1) / 2
                z = s.z0 + FLOOR_UNDER_INSET + j * FLOOR_UNDER_STEP if nz > 1 else (s.z0 + s.z1) / 2
                if any((x - kx) * ux + (z - kz) * uz < A.SWING_CHAMFER + 0.05 for kx, kz, ux, uz in chamfers):
                    continue
                y = (d - n[0] * x - n[2] * z) / n[1]
                orig = (x, y + 1.0, z)
                hit = False
                for k in bins.get((math.floor(x / FLOOR_UNDER_BIN), math.floor(z / FLOOR_UNDER_BIN)), ()):
                    pts, fn, x0, z0, x1, z1 = ups[k]
                    if not (x0 - 1e-6 <= x <= x1 + 1e-6 and z0 - 1e-6 <= z <= z1 + 1e-6):
                        continue
                    t = _ray_hit(orig, (0.0, -1.0, 0.0), pts, fn)
                    if t is not None and abs(1.0 - t) <= tol:
                        hit = True
                        break
                if not hit:
                    out.setdefault(s.id, []).append((round(x, 2), round(z, 2)))
    return out


def house_entrance_nodes(cb: A.CellBuild) -> dict:
    """Door surface id -> this cell's entrance nodes (name -> kit_anim) of the common rooms'
    entrances (heroes of grid.HOUSE_ENTRANCE_STYLES)."""
    from castle_kit import rules as R

    out: dict = {}
    for u in cb.uses:
        if u.role != "hero" or u.piece not in G.HOUSE_ENTRANCE_STYLES:
            continue
        grp = R.p3d_group(u)
        if grp is None:
            continue  # the piece as furniture (a barrel stack in a Wishing Room)
        door = grp[len(R.P3D_ENTRANCE_PREFIX):]
        ends = ("." + grp,) + tuple("." + G.leaf_group(grp, leaf) for leaf in G.ENTRANCE_LEAVES)
        out[door] = {n: cb.extras.get(n, {}).get(G.EXTRAS_ANIM, {}) for n, md in cb.meshes.items()
                     if n.endswith(ends) and md.faces}
    return out


def double_door_leaves(cb: A.CellBuild, door: str, nodes: dict) -> list[str]:
    """Knocker door opens as a pair: the errors of a "double" entrance's nodes. Every face sits
    on a leaf's node (`<entrance group>_leaf_<l|r>`, kit_anim leaf l | r), both leaves are
    there, each leaf's nodes share one origin, its hinge: on the leaf's outer edge (the end of
    its faces farther from the other leaf, within 2 cm) and its back face (the side away from
    the hero's room, kit_anim yaw), at its foot."""
    errs = []
    grp = "entrance_" + door
    by_leaf: dict = {}
    for name, anim in nodes.items():
        leaf = anim.get("leaf")
        if leaf not in G.ENTRANCE_LEAVES or not name.endswith("." + G.leaf_group(grp, leaf)):
            errs.append(f"double door: {name} is not on a leaf (kit_anim leaf {leaf!r})")
            continue
        by_leaf.setdefault(leaf, []).append(name)
    if sorted(by_leaf) != sorted(G.ENTRANCE_LEAVES):
        return errs + [f"double door: {door} has leaves {sorted(by_leaf)}, not {list(G.ENTRANCE_LEAVES)}"]
    yaw = next(iter(nodes.values())).get("yaw", 0.0)
    along = (math.cos(yaw), 0.0, -math.sin(yaw))  # the piece's +x (geom.rot_y)
    out = (math.sin(yaw), 0.0, math.cos(yaw))  # the piece's +z: toward the hero's room
    pts = {leaf: [p for n in names for f in cb.meshes[n].src_faces for p in f.pts] for leaf, names in by_leaf.items()}
    dot = lambda p, d: p[0] * d[0] + p[2] * d[2]  # noqa: E731
    mid = {leaf: sum(dot(p, along) for p in ps) / len(ps) for leaf, ps in pts.items()}
    for leaf, names in sorted(by_leaf.items()):
        origins = {cb.meshes[n].origin for n in names}
        if len(origins) != 1 or None in origins:
            errs.append(f"double door: {door} leaf {leaf} nodes have origins {sorted(map(str, origins))}")
            continue
        o = origins.pop()
        other = mid["r" if leaf == "l" else "l"]
        sides = [dot(p, along) for p in pts[leaf]]
        outer = min(sides) if mid[leaf] < other else max(sides)
        if abs(dot(o, along) - outer) > 0.02:
            errs.append(f"double door: {door} leaf {leaf}'s origin is not on its outer edge ({dot(o, along):.3f} vs {outer:.3f})")
        back = min(dot(p, out) for p in pts[leaf])
        if abs(dot(o, out) - back) > 0.02:
            errs.append(f"double door: {door} leaf {leaf}'s origin is not on its back face ({dot(o, out):.3f} vs {back:.3f})")
        if abs(o[1] - min(p[1] for p in pts[leaf])) > 0.02:
            errs.append(f"double door: {door} leaf {leaf}'s origin is not at its foot")
    return errs


def check_house_entrances_f1(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    for door, nodes in sorted(house_entrance_nodes(cb).items(), key=lambda kv: str(kv[0])):
        s = layout.by_id(door) if door else None
        if s is None or s.kind != "door" or getattr(s, "house", None) is None:
            rep.errors.append(f"house entrance: hero for {door!r} is not named entrance_<a house door's id>")
            continue
        if not nodes:
            rep.errors.append(f"house entrance: {door} has no entrance node")
        for name, anim in sorted(nodes.items()):
            if anim.get("kind") != G.ANIM_ENTRANCE or anim.get("door") != door or anim.get("style") not in G.ENTRANCE_STYLES:
                rep.errors.append(f"house entrance: {name} kit_anim {anim}")
            if any(not f.walkthrough for f in cb.meshes[name].src_faces):
                rep.errors.append(f"house entrance: {name} has faces that are not walk-through")
        if nodes and any(a.get("style") == "double" for a in nodes.values()):
            rep.errors.extend(double_door_leaves(cb, door, nodes))
        rep.stats["house_entrances"] = rep.stats.get("house_entrances", 0) + 1


def check_floor_under_f1(cb: A.CellBuild, layout: L.Layout, rep: Report) -> None:
    misses = floor_under_misses(cb, layout)
    for sid, pts in sorted(misses.items()):
        rep.errors.append(f"floor under: {sid} has no drawn floor at {len(pts)} points (first {pts[0]})")
    rep.stats["floor_under_misses"] = sum(len(v) for v in misses.values())


if __name__ == "__main__":  # (at the end: the M5b checks above are defined first)
    sys.exit(main(sys.argv[1:]))
