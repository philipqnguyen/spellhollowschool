"""Trims and add-ons: the parts a design adds to a fitted shape
(docs/art/garment-shapes.md, "Designs").

* Trims grow from a shape's named loops (hem, cuffs, neckline, waist, band,
  collar): hems, rib bands, cuffs, waistbands, turn-ups, rolled sleeves, shoe
  collars and soles.
* Add-ons attach at named anchors and lines: collars, ties, buttons, V-neck
  stripes, pompoms, crests.

Both work only from the shape's own vertices (positions per build shape,
weights), never from the body, so they need no fitting: a new body refits
the shape and every trim and add-on follows.

Paint specs are `[swatch, dye]` pairs, dye one of primary / secondary /
trim / none (see atlas.py for the swatches).
"""

from __future__ import annotations

import math

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import gear as G
from .fit import blend_weights, top_weights, unit
from .gear_lib import NONE, PRIMARY, SECONDARY, TRIM

DYES = {"primary": PRIMARY, "secondary": SECONDARY, "trim": TRIM, "none": NONE}


def paint(spec, default=("dye", "primary")):
    if spec is None:
        spec = default
    if isinstance(spec, dict):
        spec = (spec.get("swatch", default[0]), spec.get("dye", default[1]))
    sw, dye = spec
    return sw, DYES[dye]


def shade(spec, swatch: str = "dye_shade"):
    """The same dye on a darker swatch (folds, insides)."""
    return swatch, paint(spec)[1]


def _named_loops(fs, at) -> list:
    """(name, loop) for a loop name, a list of names, or `prefix.*`."""
    names = at if isinstance(at, list) else [at]
    out = []
    for n in names:
        if n.endswith(".*"):
            out += [(k, fs.loops[k]) for k in sorted(fs.loops) if k.startswith(n[:-1])]
        else:
            out.append((n, fs.loops[n]))
    return out


def _loops(fs, at) -> list:
    return [loop for _, loop in _named_loops(fs, at)]


def _vcentre(loop, r0: np.ndarray) -> np.ndarray:
    c = r0.mean(axis=0)
    return c if loop.side else np.array([0.0, c[1], c[2]])


def _axis(loop, r0: np.ndarray):
    c = r0.mean(axis=0)
    if loop.axis == "limb":
        return c, G.gb_axis(r0)
    if loop.axis == "up":
        return _vcentre(loop, r0), np.array([0.0, 0.0, 1.0])
    return _vcentre(loop, r0), np.array([0.0, 0.0, -1.0])


def _sc(fs) -> float:
    return fs.g.frame.sc


def _hat_unit(fs) -> float:
    """The unit stiff-hat parts (crowns, brims, pompoms, bows) are sized in:
    the body's height scale, not the head's width. The band is fitted to the
    head's own cranium, so a hat always fits; its crown and brim keep the same
    size relative to the body instead of growing with a large anime cranium
    (the DJP3D female's head is about 12% wider than the male's: sized by
    head width, her pointed hat came out bigger than his)."""
    return fs.g.frame.sc


def _float_over(fs, shape: str, pts: np.ndarray, lift: float, over: tuple = ()) -> np.ndarray:
    """Points kept at least `lift` outside the shape's own fitted surface
    (nearest shape face, along its outward normal). Lets an add-on that lies
    over the garment (a falling collar) follow its slope in every build
    without reading the body: the shape already clears the skin. `over`:
    further faces (earlier add-ons, such as a cape) the points must lie over
    too."""
    g = fs.g
    P = g.positions(shape)
    trees = fs.__dict__.setdefault("_float_trees", {})  # per design composition, not cloned
    out = pts.copy()
    # The shape's faces, then the earlier add-ons' on their own: a point
    # under a cape is often nearer the coat than the cape over it. Add-on
    # faces are not oriented yet (`Garment.orient` runs after the add-ons),
    # so their normal is taken pointing away from the trunk's axis.
    for key, faces, oriented in (((shape,), fs.shape_faces, True), ((shape, tuple(over)), over, False)):
        if not faces:
            continue
        tree = trees.get(key)
        if tree is None:
            tree = trees[key] = BVHTree.FromPolygons(P.tolist(), [list(g.faces[f]) for f in faces])
        for i, p in enumerate(out):
            loc, nrm, idx, dist = tree.find_nearest(Vector(p), 0.08)
            if loc is None:
                continue
            n = np.array(nrm)
            q = np.array(loc)
            if not oriented:
                axis = np.asarray(g.frame.trunk_origin(float(q[2])), dtype=np.float64)
                away = q - axis
                away[2] = 0.0
                if float(n @ away) < 0.0:
                    n = -n
            d = float((p - q) @ n)
            if d < lift:
                out[i] = p + n * (lift - d)
    return out


# ----------------------------------------------------------------------------
# Trims (grow from loops)
# ----------------------------------------------------------------------------


def turned_hem(fs, t) -> list[int]:
    g, sc = fs.g, _sc(fs)
    drop, out, turn = t.get("drop", 0.028) * sc, t.get("out", 0.006), t.get("turn", 0.012)
    main = paint(t.get("paint"))
    inner = shade(t.get("paint"))
    faces = []
    for loop in _loops(fs, t["at"]):
        def make(s, r0, k, loop=loop):
            c = _vcentre(loop, r0)
            p = G._radial(r0, c, out)
            p[:, 2] -= drop
            if k == 2:
                p = G._radial(p, c, -0.008)
                p[:, 2] += turn
            return p

        n0 = len(g.faces)
        g.strip(list(loop.ids), make, 2, lambda k, c: main if k == 0 else inner, closed=loop.closed)
        faces += list(range(n0, len(g.faces)))
    return faces


NECK_REGIONS = ("neck", "torso")


def _blend_w(w0: dict, w1: dict, t: float) -> dict:
    """Bone weights `t` of the way from `w0` to `w1` (top four, normalised)."""
    return top_weights(blend_weights([(1.0 - t, w0), (t, w1)]))


def _hug_skin(fs, shape: str, pts: np.ndarray, centre: np.ndarray, clear: float, closed: bool = True,
              regions=NECK_REGIONS):
    """A ring of points (in loop order) laid onto the skin of `regions`: a
    point further than `clear` off its nearest skin moves toward it along
    that skin's normal, but only inward toward the vertical axis through
    `centre` and down (under a jaw or the back of the skull the normal points
    down, and the band must not climb it). The moves are then smoothed
    round the ring, so the band's edge stays even over a detailed neck.
    Never pushes out (the trim clearance pass does). Returns the points and,
    per point, the skin weights under it (base shape only)."""
    fr = fs.g.frame
    tree, ids = fr.bvh(regions, shape)
    out = pts.copy()
    for i in range(len(out)):
        u = out[i] - centre
        u[2] = 0.0
        u /= max(float(np.linalg.norm(u)), 1e-9)
        for _ in range(3):
            loc, nrm, idx, dist = tree.find_nearest(Vector(out[i]), 0.2)
            if loc is None:
                break
            n = np.array(nrm)
            d = float((out[i] - np.array(loc)) @ n)
            if d <= clear + 5e-4:
                break
            move = -n * (d - clear)
            radial = min(float(move @ u), 0.0)  # inward only
            out[i] = out[i] + u * radial + np.array([0.0, 0.0, min(float(move[2]), 0.0)])
    delta = out - pts
    for _ in range(6):
        if closed:
            delta = 0.5 * delta + 0.25 * (np.roll(delta, 1, axis=0) + np.roll(delta, -1, axis=0))
        elif len(delta) > 2:
            delta[1:-1] = 0.5 * delta[1:-1] + 0.25 * (delta[:-2] + delta[2:])
    out = pts + delta
    W = [fr.weights(fr.nearest(p, regions, shape)) for p in out] if shape == "base" else None
    return out, W


def _settle_weights(fs, v0: int, n_ring: int, ts, regions=NECK_REGIONS) -> None:
    """A collar leaf's rings (appended from vertex `v0`, `n_ring` vertices
    each) take the weights of the skin they fall onto, ring k `ts[k]` of
    the way from the stand's: a leaf that lies over the shoulders on the
    stand top's neck weights stayed behind when the shoulders moved and the
    skin came through it in the walk (the neck-centred stands, 2026-11-03,
    brought the stands' backs 5 mm off the nape)."""
    g, fr = fs.g, fs.g.frame
    P = g.positions("base")
    for k, t in enumerate(ts):
        for j in range(n_ring):
            v = v0 + k * n_ring + j
            g.weights[v] = _blend_w(g.weights[v], fr.weights(fr.nearest(P[v], regions)), t)


def rib_band(fs, t) -> list[int]:
    """A ribbed band grown from a loop: along the loop's axis (down from a
    hem, along the arm from a cuff), with an inner lip turned in at its end.

    From a neckline (an `up` loop) the band lies against the neck, the way
    a crew neck's ribbing does (the clothing pass, 2026-11-03): grown straight
    up at the neckline's radius it stood 30-50 mm off the narrowing neck with
    the sweater's dark inside showing, and on the chest weights of the
    neckline it stayed behind when the head turned. Each ring is pulled in to
    `hug` off the skin (default 5 mm) and takes the skin's weights under it,
    blended from the neckline's weights at the base to the neck's at the top,
    so the band turns with the neck."""
    g, sc = fs.g, _sc(fs)
    length, out = t.get("length", 0.04) * sc, t.get("out", 0.004)
    n = t.get("rings", 2)
    ribs = t.get("ribs", True)
    stripes = [paint(s) for s in t["stripes"]] if t.get("stripes") else None
    main = paint(t.get("paint"))
    faces = []
    for loop in _loops(fs, t["at"]):
        hug = t.get("hug", 0.005) if loop.axis == "up" else None
        # Against the neck the band keeps all `n` rings outside (ribbed to
        # its top edge) and a short lip turns in below the edge.
        rings = n + 1 if hug is not None else n
        hug_w, tops = {}, {}

        def make(s, r0, k, loop=loop, hug=hug, hug_w=hug_w, tops=tops):
            c, d = _axis(loop, r0)
            if hug is None:
                tt = k / n
                p = r0 + d * (length * tt)
                if k < n:
                    return G._push_from_axis(p, c, d, out * (1.0 - 0.3 * tt))
                p = G._push_from_axis(p, c, d, out - 0.008)
                return p - d * 0.006
            if k > n:
                # The lip: the top edge turned in toward the skin and down
                # (the trim clearance pass keeps it off the skin).
                top = tops[s]
                tree, _ = fs.g.frame.bvh(NECK_REGIONS, s)
                nrm = np.array([tree.find_nearest(Vector(q), 0.2)[1] or (0.0, 0.0, 0.0) for q in top])
                if s == "base":
                    hug_w[k] = hug_w[n]
                return top - nrm * 0.0025 - d * 0.003
            tt = k / n
            p = G._push_from_axis(r0 + d * (length * tt), c, d, out * (1.0 - 0.3 * tt))
            p, W = _hug_skin(fs, s, p, c, hug, loop.closed)
            if W is not None:
                hug_w[k] = W
            if k == n:
                tops[s] = p
            return p

        def weight(k, col, w0, hug_w=hug_w):
            W = hug_w.get(k)
            if W is None:
                return w0
            return _blend_w(w0, W[col], min(1.0, k / n))

        def pnt(k, col):
            sw, m = stripes[k] if stripes and k < len(stripes) else main
            if k == rings - 1:
                return "dye_shade", m
            return ("dye_soft" if ribs and col % 2 else sw), m

        n0 = len(g.faces)
        g.strip(list(loop.ids), make, rings, pnt, closed=loop.closed, weight=weight)
        faces += list(range(n0, len(g.faces)))
    return faces


def cuff(fs, t) -> list[int]:
    g, sc = fs.g, _sc(fs)
    length, widen = t.get("length", 0.014) * sc, t.get("widen", 1.14)
    main = paint(t.get("paint"))
    inner = paint(t["inner"]) if t.get("inner") else shade(t.get("paint"))
    faces = []
    for loop in _loops(fs, t["at"]):
        def make(s, r0, k):
            c = r0.mean(axis=0)
            ax = G.gb_axis(r0)
            if k == 1:
                return c + (r0 - c) * widen + ax * length
            return c + (r0 - c) * max(widen * 0.92, 1.03) + ax * (length - 0.006)

        n0 = len(g.faces)
        g.strip(list(loop.ids), make, 2, lambda k, c: main if k == 0 else inner)
        faces += list(range(n0, len(g.faces)))
    return faces


def rolled_cuff(fs, t) -> list[int]:
    """A fat roll showing the fabric's reverse (sleeves rolled up)."""
    g = fs.g
    roll = paint(t.get("paint"), ("dye_shade", "primary"))
    edge = paint(t.get("edge"), ("dye", "trim"))
    prof = {1: (1.22, 0.006), 2: (1.3, 0.022), 3: (1.24, 0.038), 4: (1.06, 0.036)}
    faces = []
    for loop in _loops(fs, t["at"]):
        def make(s, r0, k):
            c = r0.mean(axis=0)
            ax = G.gb_axis(r0)
            return c + (r0 - c) * prof[k][0] + ax * prof[k][1]

        n0 = len(g.faces)
        g.strip(list(loop.ids), make, 4, lambda k, c: roll if k < 3 else edge, weight=_limb_weight(fs, loop, make))
        faces += list(range(n0, len(g.faces)))
    return faces


def _limb_weight(fs, loop, make):
    """Weights for a trim that grows from a limb loop past it along the limb
    (a sleeve rolled up over the elbow reaches the forearm): ring k takes the
    weights of the limb surface under it, half and half with the loop's on the
    first ring. With the loop's weights alone the roll stays with the upper
    arm while the forearm under it bends into it. Read through the shape's
    body frame, like every fitted vertex."""
    if loop.axis != "limb":
        return None
    from . import fit as F

    fr = fs.g.frame
    regions = ("upper_arms", "forearms")  # sleeve loops (rolled cuffs)
    rings: dict = {}

    def weight(k, c, w0):
        P = rings.get(k)
        if P is None:
            P = rings[k] = make("base", fs.g.positions("base")[list(loop.ids)], k)
        wb = fr.weights(fr.nearest(P[c], regions))
        t = 0.5 if k == 1 else 1.0
        return F.top_weights(F.blend_weights([(1.0 - t, w0), (t, wb)]))

    return weight


def waistband(fs, t) -> list[int]:
    g, sc = fs.g, _sc(fs)
    height, out = t.get("height", 0.032) * sc, t.get("out", 0.006)
    main = paint(t.get("paint"))
    faces = []
    for name, loop in _named_loops(fs, t["at"]):
        guide = fs.guides.get(name)

        def make(s, r0, k, guide=guide):
            c = r0.mean(axis=0)
            c = np.array([0.0, c[1], c[2]])
            rise = height if k == 1 else height - 0.004
            if guide is not None and len(guide["pos"][s]) == len(r0):
                # Rise along the shape's fitted guide ring, so the band hugs
                # the body in every build (a heavy belly recedes above the waist).
                top = r0 + (guide["pos"][s] - r0) * min(1.0, rise / guide["h"])
            else:
                top = r0 + np.array([0.0, 0.0, rise])
            if k == 1:
                return G._radial(top, c, out)
            # The inner lip never goes inside the loop it grows from (the cage).
            return G._radial(top, c, max(out - 0.009, t.get("lip", 0.001)))

        n0 = len(g.faces)
        g.strip(list(loop.ids), make, 2, lambda k, c: main if k == 0 else shade(t.get("paint")))
        faces += list(range(n0, len(g.faces)))
    return faces


def hem_fold(fs, t) -> list[int]:
    g = fs.g
    up = t.get("up", 0.014)
    pnt = paint(t.get("paint"), ("dye_shade", "primary"))
    faces = []
    for loop in _loops(fs, t["at"]):
        def make(s, r0, k):
            c = r0.mean(axis=0)
            p = G._radial(r0, c, -0.007)
            p[:, 2] += up
            return p

        n0 = len(g.faces)
        g.strip(list(loop.ids), make, 1, lambda k, c: pnt)
        faces += list(range(n0, len(g.faces)))
    return faces


def turn_up(fs, t) -> list[int]:
    """Trouser turn-ups: a raised band before the fold."""
    g, sc = fs.g, _sc(fs)
    pnt = paint(t.get("paint"), ("dye", "trim"))
    h = t.get("height", 0.03) * sc
    faces = []
    for loop in _loops(fs, t["at"]):
        def make(s, r0, k):
            c = r0.mean(axis=0)
            p = G._radial(r0, c, 0.006 if k == 1 else -0.004)
            p[:, 2] += h if k == 1 else h - 0.004
            return p

        n0 = len(g.faces)
        g.strip(list(loop.ids), make, 2, lambda k, c: pnt if k == 0 else shade(t.get("paint"), "dye_shade"))
        faces += list(range(n0, len(g.faces)))
    return faces


def shoe_collar(fs, t) -> list[int]:
    """A rolled welt round a shoe's opening. Its outward flare is the shape's
    `collar_flare` (default 4 mm). A boot's rolled top under trouser legs is
    covered by the trousers' `boot_room`, not by flattening the roll (1 mm
    lost the riding boot its rim and let a skirt's tights through the shaft
    top, 2026-11-11)."""
    g = fs.g
    pnt = paint(t.get("paint"))
    flare = float(fs.params.get("collar_flare", 0.004))
    faces = []
    for loop in _loops(fs, t["at"]):
        def make(s, r0, k):
            c = r0.mean(axis=0)
            if k == 1:
                p = G._radial(r0, c, flare)
                p[:, 2] += 0.006
                return p
            p = G._radial(r0, c, -0.006)
            p[:, 2] += 0.002
            return p

        n0 = len(g.faces)
        g.strip(list(loop.ids), make, 2, lambda k, c: pnt if k == 0 else ("inner_dark", NONE))
        faces += list(range(n0, len(g.faces)))
    return faces


def fold_cuff(fs, t) -> list[int]:
    """Knitted hat cuff folded up over the band."""
    g = fs.g
    s = g.frame.head_scale
    paints = [paint(p) for p in t.get("paints", [["dye", "primary"]] * 3)]
    ribs = t.get("ribs", True)
    faces = []
    # The crown the cuff folds up over: a cuff ring never dips inside it (a
    # cranium that widens above the band, as some sculpts' do, would
    # otherwise bring the straight-up cuff in to the scalp, under the hair).
    crown = [list(g.faces[fi]) for fi in fs.shape_faces]
    trees = {sh: BVHTree.FromPolygons(g.positions(sh).tolist(), crown) for sh in g.shapes} if crown else {}
    hc = np.asarray(g.frame.head_centre, dtype=np.float64)
    for loop in _loops(fs, t["at"]):
        base = {sh: g.positions(sh)[loop.ids] for sh in g.shapes}
        rings = [np.array(loop.ids)]
        for k, (out, dz) in enumerate(t.get("profile", ((0.007, 0.0), (0.009, 0.035), (0.004, 0.038)))):
            pts = {}
            for sh in g.shapes:
                b = base[sh]
                c = b.mean(axis=0)
                p = G._radial(b, c, out * s)
                p[:, 2] += dz * s
                if k and sh in trees:
                    for i, q in enumerate(p):
                        d = q - hc
                        L = float(np.linalg.norm(d))
                        hit = trees[sh].ray_cast(Vector(hc), Vector(d / max(L, 1e-9)), 1.0)
                        if hit[0] is not None and L < hit[3] + 0.6 * out * s:
                            p[i] = hc + d / max(L, 1e-9) * (hit[3] + 0.6 * out * s)
                pts[sh] = p
            rings.append(g.add(pts, [dict(g.weights[i]) for i in loop.ids]))
        n0 = len(g.faces)
        g.connect(rings, lambda k, cc: (("dye_soft" if ribs and cc % 2 else paints[min(k, len(paints) - 1)][0]),
                                        paints[min(k, len(paints) - 1)][1]))
        faces += list(range(n0, len(g.faces)))
    return faces


def sole(fs, t) -> list[int]:
    """A welt and sole round each shoe, from the shoe's own outline at welt
    height down to the floor (heel block raised at the back)."""
    g, sc = fs.g, _sc(fs)
    thick = t.get("thickness", 0.014) * sc
    heel = t.get("heel", 0.012) * sc
    out = t.get("out", 0.010)
    welt_z = t.get("welt_z", 0.016) * sc
    K = t.get("points", 24)  # 24 (was 28): boots fit their 1500-triangle LOD0 budget undecimated
    faces_all = []
    for side, (A, B) in fs.stats["axis"].items():
        A, B = np.array(A), np.array(B)
        side_faces = [fi for fi in fs.shape_faces if g.coords[fi].get("side") == side]
        polys = [g.faces[fi] for fi in side_faces]
        C = 0.5 * (A + B)
        ax = unit((B - A) * np.array([1, 1, 0]))
        heel_y = A[1]
        ring_top, ring_bot = {}, {}
        for sh in g.shapes:
            P = g.positions(sh)
            tree = BVHTree.FromPolygons(P.tolist(), [list(f) for f in polys])
            top, bot = [], []
            last = None
            rays = []
            for k in range(K):
                psi = 2 * math.pi * k / K
                # Rays from points along the foot axis (a stretched star), so the long foot samples evenly.
                along = math.cos(psi)
                o = C + ax * along * 0.35 * float(np.linalg.norm(B - A))
                d = unit(np.array([ax[0] * math.cos(psi) - ax[1] * math.sin(psi), ax[1] * math.cos(psi) + ax[0] * math.sin(psi), 0.0]))
                o = np.array([o[0], o[1], welt_z])
                loc, nrm, idx, dist = tree.ray_cast(Vector(o), Vector(d), 0.3)
                q = np.array(loc) if loc is not None else (last if last is not None else o + d * 0.05)
                last = q
                rays.append((o, d, float((q - o) @ d)))
            # A smooth sole edge, not one that traces every
            # dip of the upper (the jagged outline): the reach along each ray
            # is smoothed round the outline and never falls inside the upper.
            raw = np.array([r for _, _, r in rays])
            reach = raw.copy()
            for _ in range(6):
                reach = reach + 0.5 * (0.5 * (np.roll(reach, 1) + np.roll(reach, -1)) - reach)
                reach = reach - 0.53 * (0.5 * (np.roll(reach, 1) + np.roll(reach, -1)) - reach)
            reach = np.maximum(reach, raw)
            for _ in range(8):
                reach = np.maximum(raw, 0.5 * reach + 0.25 * (np.roll(reach, 1) + np.roll(reach, -1)))
            for (o, d, _), rk in zip(rays, reach):
                q = o + d * rk
                q2 = q + d * out
                fwd_from_a = float(heel_y - q2[1])  # A sits about 4.5 cm in front of the heel's back
                lift = heel * float(np.clip((0.03 * sc - fwd_from_a) / (0.03 * sc), 0, 1))
                top.append([q2[0], q2[1], thick + lift])
                qb = q + d * (out - 0.002)
                bot.append([qb[0], qb[1], 0.0])
            ring_top[sh], ring_bot[sh] = np.array(top), np.array(bot)
        # Weights from the nearest shoe vertex.
        Pb = g.positions("base")
        side_v = sorted({v for f in polys for v in f})
        ws = []
        for q in ring_top["base"]:
            j = side_v[int(np.argmin(np.linalg.norm(Pb[side_v] - q, axis=1)))]
            ws.append(dict(g.weights[j]))
        a = g.add(ring_top, ws)
        b = g.add(ring_bot, ws)
        n0 = len(g.faces)
        g.connect([a, b], lambda k, c: ("sole_edge", NONE))
        cb = {sh: np.r_[ring_bot[sh][:, :2].mean(axis=0), 0.0][None] for sh in g.shapes}
        g.cap(b[::-1], cb, ws[0], ("sole", NONE))
        ct = {sh: np.r_[ring_top[sh][:, :2].mean(axis=0), thick][None] for sh in g.shapes}
        g.cap(a, ct, ws[0], ("sole", NONE))
        faces_all += list(range(n0, len(g.faces)))
    return faces_all


TRIMS = {"turned_hem": turned_hem, "rib_band": rib_band, "cuff": cuff, "rolled_cuff": rolled_cuff, "waistband": waistband,
         "hem_fold": hem_fold, "turn_up": turn_up, "shoe_collar": shoe_collar, "fold_cuff": fold_cuff, "sole": sole}


# ----------------------------------------------------------------------------
# Add-ons (attach at anchors and lines)
# ----------------------------------------------------------------------------


def collar_leaf(fs, a):
    """Folded shirt collar from a closed neck loop that starts at the front."""
    g, sc = fs.g, _sc(fs)
    pnt = paint(a.get("paint"))
    loop = fs.loops[a.get("at", "neckline")]
    v0 = g.n
    faces = G.collar_leaf(g, list(loop.ids), a.get("drop", 0.03) * sc, a.get("spread", 0.018), a.get("points", 0.03) * sc,
                          mask=pnt[1], sw=pnt[0])
    _settle_weights(fs, v0, len(loop.ids), (0.25, 0.6, 1.0))
    inner = paint(a.get("inner"), (("shirt_shade" if pnt[0] == "shirt_white" else "dye_shade"), _dye_name(pnt[1])))
    return [(faces, 0.003, inner, pnt)]


def _dye_name(mask) -> str:
    for k, v in DYES.items():
        if tuple(v) == tuple(mask):
            return k
    return "none"


def open_collar(fs, a):
    """Open shirt collar: the leaf rises from the neckline arc and falls open at the front."""
    g = fs.g
    pnt = paint(a.get("paint"))
    loop = fs.loops[a.get("at", "neckline")]

    def make(s, r0, k):
        c = r0.mean(axis=0)
        front = np.clip(G._front_ness(r0, c), 0.0, 1.0) ** 2
        p = G._radial(r0, c, 0.004 + (0.008 + 0.034 * front) * k / 2.0)
        p[:, 2] += 0.004 if k == 1 else -(0.024 + 0.026 * front)
        # The falling leaf lies over the shirt: never under its surface.
        return _float_over(fs, s, p, a.get("lift", 0.005)) if k == 2 else p

    n0, v0 = len(g.faces), g.n
    g.strip(list(loop.ids), make, 2, lambda k, c: pnt, closed=loop.closed)
    _settle_weights(fs, v0, len(loop.ids), (0.25, 1.0))
    return [(list(range(n0, len(g.faces))), 0.003, shade(a.get("paint")), pnt)]


def tie(fs, a):
    """A tie down a centre line over the garment: a knot, then a blade whose
    rows run diagonally so alternating rows read as stripes."""
    g, sc = fs.g, _sc(fs)
    line = fs.lines[a.get("at", "centre_front")]
    rows_n = a.get("rows")
    if rows_n:
        line = line[:rows_n]
    offset = a.get("offset", 0.008)
    wt, wb = a.get("width_top", 0.036) * sc, a.get("width_bottom", 0.07) * sc
    stripes = [paint(s)[1] for s in a.get("stripes", [["dye", "secondary"], ["dye", "trim"]])]
    side = np.array([1.0, 0.0, 0.0])
    knot = [0.0, 0.07]
    rows_t = list(knot)
    tt = 0.07
    while tt < 0.97:
        tt += 0.055
        rows_t.append(min(tt, 0.97))
        tt += 0.025
        rows_t.append(min(tt, 0.97))
    rows_t = sorted(set(rows_t)) + [1.0]
    slant = 0.012
    normals = {s: g.normals(s) for s in g.shapes}
    ids = np.array(line)

    def centre_line(s):
        P = g.positions(s)[ids]
        N = normals[s][ids]
        seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        return P, N, cum / cum[-1]

    # Row weights: interpolated along the centre line, then averaged over
    # the neighbouring rows, so the blade bends as one strip. Each row took
    # the weights of the one line vertex under it; neighbouring rows then
    # switched between the upper spine, the breasts and the waist, and in
    # the run the blade sheared into an accordion (the oxford tie,
    # 2026-11-11).
    _P, _N, u_line = centre_line("base")
    raw = []
    for tr in rows_t:
        j = int(np.clip(np.searchsorted(u_line, tr) - 1, 0, len(u_line) - 2))
        f = float(np.clip((tr - u_line[j]) / max(u_line[j + 1] - u_line[j], 1e-9), 0.0, 1.0))
        raw.append(blend_weights([(1.0 - f, g.weights[ids[j]]), (f, g.weights[ids[j + 1]])]))
    row_w = []
    for i in range(len(rows_t)):
        nb = [raw[k] for k in range(max(0, i - 2), min(len(raw), i + 3))]
        row_w.append(top_weights(blend_weights([(1.0 / len(nb), w) for w in nb])))
    rings = []
    for i, tr in enumerate(rows_t):
        pts = {}
        for s in g.shapes:
            P, N, u = centre_line(s)
            j = int(np.clip(np.searchsorted(u, tr) - 1, 0, len(u) - 2))
            f = (tr - u[j]) / max(u[j + 1] - u[j], 1e-9)
            c = P[j] + (P[j + 1] - P[j]) * f
            n = unit(N[j] + (N[j + 1] - N[j]) * f)
            if i < len(knot):
                half = wt * 0.42
                c = c + n * (offset + 0.008)
                pts[s] = np.array([c - side * half, c, c + side * half])
                continue
            half = (wt + (wb - wt) * tr) / 2.0
            c = c + n * offset
            if i == len(rows_t) - 1:
                pts[s] = np.array([c - side * half * 0.25 + [0, 0, 0.02], c, c + side * half * 0.25 + [0, 0, 0.02]])
                continue
            up = np.array([0.0, 0.0, slant])
            pts[s] = np.array([c - side * half + up, c, c + side * half - up])
        rings.append(g.add(pts, [dict(row_w[i])] * 3))

    def pnt(k, c):
        if k == 0:
            return "dye", stripes[0]
        return "dye", stripes[(k - 1) % len(stripes)]

    n0 = len(g.faces)
    g.connect(rings, pnt, closed=False)
    faces = list(range(n0, len(g.faces)))
    return [(faces, 0.004, ("dye_shade", stripes[0]), ("dye", stripes[0]))]


def v_stripes(fs, a):
    """Knitted stripes along both edges of a V opening, laid on the surface."""
    g = fs.g
    widths = a.get("widths", [0.009, 0.009])
    masks = [paint(m)[1] for m in a.get("stripes", [["dye", "secondary"], ["dye", "trim"]])]
    lift = a.get("lift", 0.002)
    normals = {s: g.normals(s) for s in g.shapes}
    prefix = a.get("edges", "front_edge")
    for key, side in ((f"{prefix}.L", 1), (f"{prefix}.R", -1)):
        edge = fs.lines.get(key, [])
        if len(edge) < 2:
            continue
        lines = [None]
        for j in range(len(widths)):
            pts = {}
            for s in g.shapes:
                P = g.positions(s)[edge]
                N = normals[s][edge]
                d = np.gradient(P, axis=0)
                lat = np.cross(d, N)
                lat /= np.maximum(np.linalg.norm(lat, axis=1, keepdims=True), 1e-9)
                lat *= np.sign(lat[:, 0:1] * side + 1e-12)
                pts[s] = P + lat * sum(widths[:j + 1]) + N * lift
            lines.append(g.add(pts, [dict(g.weights[i]) for i in edge]))
        base = {s: g.positions(s)[edge] + normals[s][edge] * lift for s in g.shapes}
        lines[0] = g.add(base, [dict(g.weights[i]) for i in edge])
        for j in range(len(widths)):
            la, lb = lines[j], lines[j + 1]
            for i in range(len(edge) - 1):
                g.face((la[i], la[i + 1], lb[i + 1], lb[i]), "dye", masks[j])
    return []


def _disc(g, centre: dict, normal: dict, radius: float, weight: dict, rim, face, n: int = 8, lift: float = 0.0015):
    """A small round disc lying on the surface (buttons, studs)."""
    ring_pts = {}
    cen_pts = {}
    for s in g.shapes:
        c, nn = centre[s], unit(normal[s])
        t1 = unit(np.cross(nn, [0.0, 0.0, 1.0]) if abs(nn[2]) < 0.9 else np.cross(nn, [1.0, 0.0, 0.0]))
        t2 = np.cross(nn, t1)
        ring_pts[s] = np.array([c + nn * lift + (t1 * math.cos(2 * math.pi * k / n) + t2 * math.sin(2 * math.pi * k / n)) * radius
                                for k in range(n)])
        cen_pts[s] = (c + nn * (lift + radius * 0.35))[None]
    ids = g.add(ring_pts, [dict(weight)] * n)
    g.cap(ids, cen_pts, weight, face)
    return ids


def buttons(fs, a):
    g, sc = fs.g, _sc(fs)
    line = fs.lines[a.get("at", "centre_front")]
    n = a.get("count", 5)
    lo, hi = a.get("span", [0.08, 0.92])
    r = a.get("radius", 0.006) * sc
    face = paint(a.get("paint"), ("dye", "trim"))
    # Each disc lies on the shape's own surface: its normal is that of the
    # shape face nearest its centre. The interpolated vertex normals of the
    # line were taken over every face sharing its vertices (the collar
    # leaf, the placket fold, the open front's edge), and some pointed
    # sideways: those buttons stood edge-on, as pins (the greatcoat and the
    # silk blouse, 2026-11-11).
    shape_polys = [list(g.faces[fi]) for fi in fs.shape_faces]
    trees = {s: BVHTree.FromPolygons(g.positions(s).tolist(), shape_polys) for s in g.shapes}

    def surface_normal(s, c, fallback):
        loc, nrm, _i, _d = trees[s].find_nearest(Vector(c), 0.05)
        return np.array(nrm) if loc is not None else fallback

    normals = {s: g.normals(s) for s in g.shapes}
    P = g.positions("base")[line]
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    u = cum / cum[-1]
    # `columns`: lateral offsets (reference metres, + = the character's left)
    # of parallel button rows (double-breasted coats), floated onto the surface.
    for dx in a.get("columns", [0.0]):
        for k in range(n):
            tt = lo + (hi - lo) * k / max(n - 1, 1)
            j = int(np.clip(np.searchsorted(u, tt) - 1, 0, len(u) - 2))
            f = (tt - u[j]) / max(u[j + 1] - u[j], 1e-9)
            cen = {s: g.positions(s)[line[j]] * (1 - f) + g.positions(s)[line[j + 1]] * f for s in g.shapes}
            nrm = {s: normals[s][line[j]] * (1 - f) + normals[s][line[j + 1]] * f for s in g.shapes}
            if dx:
                cen = {s: _float_over(fs, s, (cen[s] + np.array([dx * sc, 0.0, 0.0]))[None], 0.0)[0] for s in g.shapes}
            nrm = {s: surface_normal(s, cen[s], nrm[s]) for s in g.shapes}
            _disc(g, cen, nrm, r, g.weights[line[j]], None, face, lift=a.get("lift", 0.0015))
    return []


def pompom(fs, a):
    g = fs.g
    s = _hat_unit(fs)
    vid = fs.anchors[a.get("at", "crown")].vid
    W = dict(g.weights[vid])
    pnt = paint(a.get("paint"), ("dye", "trim"))
    rad = a.get("radius", 0.042) * s
    rings = []
    lat = 6
    for i in range(1, lat):
        ang = math.pi * i / lat
        pts = {}
        for sh in g.shapes:
            pc = g.positions(sh)[vid] + np.array([0.0, 0.0, rad * 0.95])
            ring = []
            for j in range(12):
                b = 2 * math.pi * j / 12
                rr = rad * (1.0 + 0.12 * math.sin(3 * b + i))
                ring.append(pc + rr * np.array([math.sin(ang) * math.cos(b), math.sin(ang) * math.sin(b), math.cos(ang)]))
            pts[sh] = np.array(ring)
        rings.append(g.add(pts, [dict(W)] * 12))
    g.connect(rings, lambda k, cc: pnt)
    pcs = {sh: g.positions(sh)[vid] + np.array([0.0, 0.0, rad * 0.95]) for sh in g.shapes}
    g.cap(rings[0][::-1], {sh: (pcs[sh] + [0, 0, rad])[None] for sh in g.shapes}, W, pnt)
    g.cap(rings[-1], {sh: (pcs[sh] - [0, 0, rad])[None] for sh in g.shapes}, W, pnt)
    return []


def crest(fs, a):
    """A small shield badge lying on the surface at an anchor (house crests)."""
    g, sc = fs.g, _sc(fs)
    vid = fs.anchors[a.get("at", "chest.L")].vid
    field = paint(a.get("paint"), ("dye", "secondary"))
    border = paint(a.get("border"), ("dye", "trim"))
    w, h = a.get("width", 0.05) * sc, a.get("height", 0.058) * sc
    lift = a.get("lift", 0.002)
    normals = {s: g.normals(s) for s in g.shapes}
    W = dict(g.weights[vid])
    # Outline (shield) and an inner field.
    prof = [(-0.5, 0.5), (0.5, 0.5), (0.5, -0.1), (0.0, -0.5), (-0.5, -0.1)]
    rings = []
    for scale, lf in ((1.0, lift), (0.72, lift + 0.0006)):
        pts = {}
        for s in g.shapes:
            c, nn = g.positions(s)[vid], unit(normals[s][vid])
            t1 = unit(np.cross([0.0, 0.0, 1.0], nn))
            t2 = np.cross(nn, t1)
            pts[s] = np.array([c + nn * lf + (t1 * x * w + t2 * y * h) * scale for x, y in prof])
        rings.append(g.add(pts, [dict(W)] * len(prof)))
    g.connect(rings, lambda k, c: border)
    cen = {s: (g.positions(s)[vid] + unit(normals[s][vid]) * (lift + 0.0008))[None] for s in g.shapes}
    g.cap(rings[1], cen, W, field)
    return []


def hood(fs, a):
    """A hood lying down the upper back, gathered at the back of the neckline
    (lining colour inside, outer colour on its thickened back)."""
    from types import SimpleNamespace

    g = fs.g
    loop = fs.loops[a.get("at", "neckline")]
    outer, lining = paint(a.get("paint"), ("dye", "primary")), paint(a.get("lining"), ("dye", "secondary"))
    thickness = 0.006
    # The hood's profile is a fixed drop and set-back from the neckline; on a
    # back that curves further out below the shoulders (the sculpt-wrapped
    # bodies) its lower rings sank through the garment's own back. Every ring
    # below the gather lies over the garment, by more than the thickness its
    # outer side gains inward.
    lift = a.get("lift", thickness + 0.004)
    # It lies over a shoulder cape added before it as well: floated over the
    # coat alone, its lining crossed the cape at the shoulders in tan slivers
    # (the hooded cloak, 2026-11-11). Only the cape: the other add-ons near
    # the neckline (a shirt collar, the front's stripes, a tie) are not under
    # the hood, and floated over them along their guessed normals its side
    # panels stepped out, showing the lining as a wide jagged rim (the house
    # robes).
    over = tuple(fs.__dict__.get("_cape_faces", ()))

    def post(s, p, k):
        return _float_over(fs, s, p, lift, over) if k >= 2 else p

    faces = G.hood(g, SimpleNamespace(sc=_sc(fs)), list(loop.ids), mask=outer[1], lining=lining[1], post=post)
    return [(faces, thickness, outer, lining)]


def _static_ring(g, pts: dict, W: dict):
    return g.add(pts, [dict(W) for _ in range(len(next(iter(pts.values()))))])


def brim(fs, a):
    """A stiff hat brim from the band's bottom loop: rings out and down in
    head-scale units ([out, drop] per ring), with an optional wavy edge."""
    g = fs.g
    s = _hat_unit(fs)
    loop = fs.loops[a.get("at", "band")]
    W = dict(g.weights[loop.ids[0]])
    pnt = paint(a.get("paint"))
    prof = a.get("profile", [[0.035, 0.002], [0.075, 0.008], [0.105, 0.016]])
    waves, amp = a.get("waves", 3), a.get("wave", 0.006)
    m = len(loop.ids)
    rings = [np.array(loop.ids)]
    for k, (out, drop) in enumerate(prof, start=1):
        pts = {}
        for sh in g.shapes:
            band = g.positions(sh)[loop.ids]
            c = band.mean(axis=0)
            p = G._radial(band, c, out * s)
            p[:, 2] -= drop * s + amp * np.sin(np.arange(m) * 2 * np.pi * waves / m) * (k / len(prof))
            pts[sh] = p
        rings.append(_static_ring(g, pts, W))
    n0 = len(g.faces)
    g.connect(rings, lambda k, c: pnt)
    faces = list(range(n0, len(g.faces)))
    return [(faces, a.get("thickness", 0.004), paint(a.get("under"), ("dye_shade", _dye_name(pnt[1]))), pnt)]


def crown(fs, a):
    """A stiff hat crown from the band's top loop, in head-scale units:
    `height`, a radius taper (`taper` exponent: >1 cone, <1 dome), an outward
    `bulge`, and a tip that bends back by `bend` [back, down]. Closed with a
    tip (pointed hats) or a flat-ish cap (`end_radius` > 0: cloches, berets)."""
    g = fs.g
    s = _hat_unit(fs)
    loop = fs.loops[a.get("at", "band_top")]
    W = dict(g.weights[loop.ids[0]])
    pnt = paint(a.get("paint"))
    n = a.get("rings", 9)
    height, taper, bulge = a.get("height", 0.33) * s, a.get("taper", 1.15), a.get("bulge", 0.06)
    back, down = (b * s for b in a.get("bend", [0.11, 0.06]))
    end_r = a.get("end_radius", 0.0)
    rings = [np.array(loop.ids)]
    tips = {}
    for k in range(1, n + 1):
        t = k / n
        pts = {}
        for sh in g.shapes:
            band = g.positions(sh)[loop.ids]
            c = band.mean(axis=0)
            r = end_r + (1 - end_r) * (1 - t) ** taper
            bend = np.array([0.0, back * t ** 2.4, -down * t ** 4])
            centre = c + np.array([0.0, 0.0, height * t]) + bend
            p = centre + (band - c) * r * np.array([1.0, 1.0, 0.0]) * (1.0 + bulge * math.sin(t * math.pi))
            # The band's tilt (it sits higher at the brow than at the nape)
            # levels out up the crown. Kept at full tilt, every ring was the
            # same slanted section moved back by the bend, and seen from the
            # front the sides of the crown stepped out ring by ring (the
            # pointed and witch hats, 2026-11-11).
            p[:, 2] = centre[2] + (band[:, 2] - c[2]) * r * (1.0 - t) ** 0.5
            pts[sh] = p
            tips[sh] = centre
        rings.append(_static_ring(g, pts, W))
    g.connect(rings, lambda k, c: pnt)
    cid = g.cap(rings[-1], {sh: (tips[sh] + np.array([0.0, 0.0, 0.004 if end_r else 0.01]))[None] for sh in g.shapes}, W, pnt)
    from .fit import Anchor

    fs.anchors["crown_tip"] = Anchor(int(cid))  # for a beret's stalk or a hat's pompom
    return []


# Passes of `_cape_rest`'s push out of the coat (each measures again).
CAPE_PUSH_PASSES = 12


def _grid_mean(D: np.ndarray, closed: bool, passes: int) -> np.ndarray:
    """A (rings, cols, 3) field over a cape's grid, each entry averaged with
    its grid neighbours `passes` times; ring 0 (the neckline) stays at 0."""
    R = D.shape[:2]
    for _ in range(passes):
        A = D.copy()
        cnt = np.ones(R)
        if closed:
            A += np.roll(D, 1, axis=1) + np.roll(D, -1, axis=1)
            cnt += 2
        else:
            A[:, 1:] += D[:, :-1]
            A[:, :-1] += D[:, 1:]
            cnt[:, 1:] += 1
            cnt[:, :-1] += 1
        A[1:] += D[:-1]
        A[:-1] += D[1:]
        cnt[1:] += 1
        cnt[:-1] += 1
        D = A / cnt[..., None]
        D[0] = 0.0
    return D


def _cape_rest(fs, rings, spec: dict, closed: bool = True) -> int:
    """The cape lies on the garment under it (the clothing pass, 2026-11-02:
    flared out from the neckline on its own, it stood off the shoulders like
    a stiff collar once the coat under it came close to the body): every
    cape vertex further than `gap` + `hang` * t^2 (t = ring / rings) from
    the garment's own shape faces is pulled toward its nearest point on them,
    so the top lies on the shoulders and the lower rings keep some fullness.
    Then any part sunk under the garment's outer shell is pushed back out.
    Returns moves."""
    g, sc = fs.g, _sc(fs)
    gap, hang = float(spec.get("gap", 0.008)) * sc, float(spec.get("hang", 0.03)) * sc
    n = len(rings) - 1
    moved = 0
    R = np.array([[int(v) for v in r] for r in rings])  # (rings, cols)
    # The garment's own outer shell only: an open front's nested inserts
    # (the jumper and shirt seen through it) lie 1-2 cm inside the coat, and
    # cape vertices pulled onto them sank through the coat's front panels
    # (charcoal notches through the cloak's cape band over the bust).
    outer = [f for f in fs.shape_faces if g.coords.get(f, {}).get("part") in ("torso", "sleeve", "bell", "skirt")]
    for sh in g.shapes:
        P = g.positions(sh)
        shell = [g.faces[f] for f in (outer or fs.shape_faces)]
        tree = BVHTree.FromPolygons(P.tolist(), [list(f) for f in shell])
        D = np.zeros(R.shape + (3,))
        for k in range(1, n + 1):
            target = gap + hang * (k / n) ** 2
            for c, v in enumerate(R[k]):
                loc, nrm, _, dist = tree.find_nearest(Vector(P[v]), 0.5)
                if loc is None or dist <= target:
                    continue
                q = np.array(loc)
                D[k, c] = (q + (P[v] - q) * (target / dist)) - P[v]
                moved += 1
        # The pull, smoothed over the cape's grid before it is applied: each
        # vertex pulled to its own nearest point creased the cape over the
        # coat's seams and shoulder points, and the lining (4 mm inside)
        # crossed it there in triangles (the travelling cloak, 2026-11-11).
        D = _grid_mean(D, closed, 6)
        P[R[1:].ravel()] += D[1:].reshape(-1, 3)
        # Never closer to the shell than 3/4 of the gap (the lining goes 4 mm
        # inside), measured along the shell face's outward normal: a vertex
        # that had sunk under the shell (over the bust, where the open
        # coat's front panels stand out further than the cape's grid rows
        # follow) was pushed further in along the unsigned direction, and
        # the coat showed through the cape's band in square notches (the
        # travelling cloak on body_b, 2026-11-11).
        for _ in range(CAPE_PUSH_PASSES):
            E = np.zeros(R.shape + (3,))
            m = R.shape[1]
            for k in range(1, n + 1):
                for c in range(m):
                    # The vertex, and the centre of each cell it starts (the
                    # shell bulges between the cape's vertices over the bust).
                    samples = [(P[R[k, c]], [(k, c)])]
                    c1 = (c + 1) % m if closed else c + 1
                    if c1 < m:
                        cell = [(k, c), (k, c1), (k - 1, c1), (k - 1, c)]
                        samples.append((np.mean([P[R[i, j]] for i, j in cell], axis=0), [(i, j) for i, j in cell if i > 0]))
                    for q0, owners in samples:
                        loc, nrm, _, dist = tree.find_nearest(Vector(q0), 0.5)
                        if loc is None:
                            continue
                        nv = np.array(nrm)
                        h = float((q0 - np.array(loc)) @ nv)
                        if h < 0.75 * gap:
                            for i, j in owners:
                                if 0.75 * gap - h > float(np.linalg.norm(E[i, j])):
                                    E[i, j] = nv * (0.75 * gap - h)
            if not E.any():
                break
            # The push, averaged over the cape's grid as the pull is: each
            # vertex pushed on its own along its nearest shell face's normal
            # (they turn at the coat's seams and shoulder points) crumpled
            # the cape's cells, and its lining crossed it in tan or maroon
            # patches on the shoulders (the hooded and travelling cloaks).
            # Spread out, a pass moves less than the deficit, so the passes
            # measure again (up to CAPE_PUSH_PASSES) until no sample is short.
            E = _grid_mean(E, closed, 3)
            P[R[1:].ravel()] += E[1:].reshape(-1, 3)
        g.pos[sh] = [P]
    return moved


def cape(fs, a):
    """A shoulder cape from the neckline loop: rings flaring out over the
    shoulders and down, weighted from the upper spine toward the shoulders
    and upper arms so it rides the arms."""
    g, sc = fs.g, _sc(fs)
    loop = fs.loops[a.get("at", "neckline")]
    ids = list(loop.ids)
    n = a.get("rings", 4)
    length, flare = a.get("length", 0.165), a.get("flare", 0.09)
    base = {sh: G._radial(g.positions(sh)[ids], g.positions(sh)[ids].mean(axis=0), a.get("lift", 0.008)) for sh in g.shapes}
    x0 = g.positions("base")[ids][:, 0]
    ring0 = g.add(base, [dict(g.weights[i]) for i in ids])

    def make(sh, r0, k):
        c = r0.mean(axis=0)
        t = k / n
        p = G._radial(r0, c, (flare * 0.55 + flare * 0.45 * np.abs(r0[:, 0] - c[0]) / 0.1) * t ** 0.8 * 1.6)
        p[:, 2] -= (0.035 + (length - 0.035) * t) * t * sc
        return p

    def weight(k, col, w0):
        side = "L" if x0[col] > 0 else "R"
        t = min(1.0, abs(x0[col]) / 0.16) * (k / n)
        return {"DEF-spine.003": 1.0 - t, f"DEF-shoulder.{side}": 0.5 * t, f"DEF-upper_arm.{side}": 0.5 * t}

    main, edge = paint(a.get("paint")), paint(a.get("edge"), ("dye", "trim"))
    n0 = len(g.faces)
    rings = g.strip(list(ring0), make, n, lambda k, c: main if k < n - 1 else edge, closed=loop.closed, weight=weight)
    faces = list(range(n0, len(g.faces)))
    fs.__dict__.setdefault("_cape_faces", []).extend(faces)  # a hood added later lies over them
    if a.get("rest"):
        fs.stats["cape_rest_moves"] = _cape_rest(fs, rings, a["rest"], loop.closed)
    return [(faces, a.get("thickness", 0.004), paint(a.get("lining"), ("dye", "secondary")), edge)]


# ----------------------------------------------------------------------------
# Character adoption: the add-ons the remaining catalog items needed
# ----------------------------------------------------------------------------


def _frame_at(g, vid: int, sh: str, normals: dict):
    """(position, normal, tangent round the body, up-ish) at a garment vertex."""
    c = g.positions(sh)[vid]
    n = np.asarray(normals[sh][vid], dtype=np.float64)
    if np.linalg.norm(n) < 0.5:
        # A vertex with no face (a band-less hat's anchor ring): out from the head.
        n = c - np.asarray(g.frame.head_centre, dtype=np.float64)
    n = unit(n)
    t = np.cross([0.0, 0.0, 1.0], n)
    t = unit(t) if np.linalg.norm(t) > 1e-6 else np.array([1.0, 0.0, 0.0])
    v = np.cross(n, t)
    return c, n, t, v


def ruffle(fs, t) -> list[int]:
    """A frill grown from a loop: two rings along the loop's axis, the outer
    one waved in and out column by column (pleated ruffle collars and cuffs).

    From a neckline (an `up` loop) the frill stands against the neck, as
    `rib_band` does (the ruffled silk blouse, 2026-11-04): grown straight up
    at the stand's radius and pushed out by `out` it stood 13-31 mm off the
    narrowing neck, skin showing under it all round and its inside seen over
    the shoulders. Each ring is laid on the skin at `hug` (default 4 mm)
    with its valleys there and only its peaks pleated out (`out` is then the
    edge's flare past the hug, `wave` the pleat's depth), and takes the
    skin's weights toward its top so it turns with the neck."""
    g, sc = fs.g, _sc(fs)
    length, out = t.get("length", 0.03) * sc, t.get("out", 0.012) * sc
    wave = t.get("wave", 0.008) * sc
    main = paint(t.get("paint"))
    faces = []
    for loop in _loops(fs, t["at"]):
        hug = t.get("hug", 0.004) if loop.axis == "up" else None
        hug_w = {}

        def make(s, r0, k, loop=loop, hug=hug, hug_w=hug_w):
            c, d = _axis(loop, r0)
            tt = k / 2.0
            sign = np.array([1.0 if i % 2 else -1.0 for i in range(len(r0))])
            p = r0 + d * (length * tt)
            if hug is None:
                return G._push_from_axis(p, c, d, (out * tt + sign * wave * tt ** 1.5 + 0.002)[:, None])
            p, W = _hug_skin(fs, s, p, c, hug, loop.closed)
            if W is not None:
                hug_w[k] = W
            return G._push_from_axis(p, c, d, (out * tt + 0.5 * (sign + 1.0) * wave * tt ** 1.5)[:, None])

        def weight(k, col, w0, hug_w=hug_w):
            W = hug_w.get(k)
            return w0 if W is None else _blend_w(w0, W[col], k / 2.0)

        n0 = len(g.faces)
        g.strip(list(loop.ids), make, 2, lambda k, c: main if k == 0 else (shade(t.get("paint"), "dye_light")),
                closed=loop.closed, weight=weight)
        faces += list(range(n0, len(g.faces)))
    return faces


def roll(fs, t) -> list[int]:
    """A thick soft roll along a loop (the fur edge of a hood's face opening):
    a half tube over the edge, curling toward the loop's centre."""
    g, sc = fs.g, _sc(fs)
    size = t.get("size", 0.016) * sc
    paints = [paint(p) for p in t.get("paints", [["dye_light", "secondary"], ["dye_soft", "secondary"]])]
    normals = {s: g.normals(s) for s in g.shapes}
    faces = []
    for loop in _loops(fs, t["at"]):
        ids = list(loop.ids)
        rings = [np.array(ids)]
        prof = ((0.75, 0.15), (0.9, 0.7), (0.45, 1.05), (-0.25, 0.85))  # (along the normal, toward the centre) x size
        for a_n, a_in in prof:
            pts = {}
            for s in g.shapes:
                P = g.positions(s)[ids]
                c = P.mean(axis=0)
                inward = c - P
                inward /= np.maximum(np.linalg.norm(inward, axis=1, keepdims=True), 1e-9)
                N = normals[s][ids]
                pts[s] = P + N * (a_n * size) + inward * (a_in * size)
            rings.append(g.add(pts, [dict(g.weights[i]) for i in ids]))
        n0 = len(g.faces)
        g.connect(rings, lambda k, cc: (paints[(k + cc) % len(paints)]), closed=loop.closed)
        faces += list(range(n0, len(g.faces)))
    return faces


def lapels(fs, a):
    """Lapels folded back along both edges of a front opening (jackets,
    greatcoats): a flap from each front edge line over the top `span` of it,
    widest just under the collar, floated over the garment."""
    g, sc = fs.g, _sc(fs)
    pnt = paint(a.get("paint"))
    width = a.get("width", 0.06) * sc
    lo, hi = a.get("span", [0.0, 0.45])
    lift = a.get("lift", 0.004)
    faces = []
    for key, side in (("front_edge.L", 1), ("front_edge.R", -1)):
        line = fs.lines.get(key, [])
        if len(line) < 3:
            continue
        n = len(line)
        sel = line[int(lo * (n - 1)): int(hi * (n - 1)) + 1]
        if len(sel) < 2:
            continue
        m = len(sel)
        pts = {}
        for s in g.shapes:
            P = g.positions(s)[sel]
            prof = np.array([math.sin(math.pi * min(1.0, (m - 1 - i) / max(m - 1, 1) * 1.15)) ** 0.7 for i in range(m)])
            prof[0] = max(prof[0], 0.55)  # the notch under the collar
            q = P + np.array([side * 1.0, 0.0, 0.0]) * (width * prof)[:, None]
            pts[s] = _float_over(fs, s, q, lift)
        outer = g.add(pts, [dict(g.weights[i]) for i in sel])
        n0 = len(g.faces)
        for i in range(m - 1):
            g.face((sel[i], sel[i + 1], outer[i + 1], outer[i]), *pnt)
        faces += list(range(n0, len(g.faces)))
    return [(faces, 0.003, paint(a.get("inner"), ("dye_shade", _dye_name(pnt[1]))), pnt)]


def _flat_loop(g, centre: dict, u: dict, v: dict, ru: float, rv: float, W: dict, pnt, n: int = 10, width: float = 0.35):
    """A flat ribbon loop (half a bow): an annulus in the (u, v) plane."""
    outer, inner = {}, {}
    for s in g.shapes:
        outer[s] = np.array([centre[s] + u[s] * ru * math.cos(2 * math.pi * k / n) + v[s] * rv * math.sin(2 * math.pi * k / n) for k in range(n)])
        inner[s] = np.array([centre[s] + u[s] * ru * (1 - width) * math.cos(2 * math.pi * k / n) + v[s] * rv * (1 - width) * math.sin(2 * math.pi * k / n)
                             for k in range(n)])
    a = g.add(outer, [dict(W)] * n)
    b = g.add(inner, [dict(W)] * n)
    n0 = len(g.faces)
    g.connect([a, b], lambda k, c: pnt)
    return list(range(n0, len(g.faces)))


def bow(fs, a):
    """A ribbon bow at an anchor: two flat loops, a knot and two tails
    falling back and down (hair ribbons)."""
    g, s = fs.g, _hat_unit(fs)
    vid = fs.anchors[a.get("at", "band_left")].vid
    W = dict(g.weights[vid])
    pnt = paint(a.get("paint"))
    normals = {sh: g.normals(sh) for sh in g.shapes}
    lift = a.get("lift", 0.01) * s
    size = a.get("size", 1.0)
    faces = []
    fr = {sh: _frame_at(g, vid, sh, normals) for sh in g.shapes}
    for sign in (1, -1):
        cen = {sh: fr[sh][0] + fr[sh][1] * lift + fr[sh][2] * (sign * 0.04 * s * size) + fr[sh][3] * (0.01 * s * size) for sh in g.shapes}
        u = {sh: unit(fr[sh][2] * sign + fr[sh][3] * 0.35) for sh in g.shapes}
        v = {sh: unit(fr[sh][3] - fr[sh][2] * sign * 0.35) for sh in g.shapes}
        faces += _flat_loop(g, cen, u, v, 0.042 * s * size, 0.026 * s * size, W, pnt)
    knot = {sh: fr[sh][0] + fr[sh][1] * (lift + 0.004) for sh in g.shapes}
    _disc(g, knot, {sh: fr[sh][1] for sh in g.shapes}, 0.012 * s * size, W, None, pnt, lift=0.0)
    for sign, length in ((1, 0.10), (-1, 0.08)):
        rows = []
        for i in range(6):
            tt = i / 5
            pts = {}
            for sh in g.shapes:
                c, n, tg, up = fr[sh]
                along = c + n * (lift * (1 - 0.6 * tt)) + tg * (sign * 0.012 * s + sign * 0.03 * s * tt) - up * (length * s * size * tt)
                half = 0.011 * s * size
                pts[sh] = np.array([along - tg * half, along + tg * half])
            rows.append(g.add(pts, [dict(W)] * 2))
        n0 = len(g.faces)
        g.connect(rows, lambda k, c: pnt, closed=False)
        faces += list(range(n0, len(g.faces)))
    return [(faces, 0.0015, paint(a.get("inner"), ("dye_shade", _dye_name(pnt[1]))), pnt)]


def comb(fs, a):
    """A curved hair comb at an anchor: an arched spine with a jewel and a
    spray of feathers curling back (feathered combs)."""
    g, s = fs.g, _hat_unit(fs)
    vid = fs.anchors[a.get("at", "band_right")].vid
    W = dict(g.weights[vid])
    metal = paint(a.get("paint"), ("gold", "none"))
    jewel = paint(a.get("jewel"), ("dye", "trim"))
    normals = {sh: g.normals(sh) for sh in g.shapes}
    fr = {sh: _frame_at(g, vid, sh, normals) for sh in g.shapes}
    faces = []
    lift = a.get("lift", 0.004) * s
    rows = []
    for i in range(9):
        tt = i / 8 - 0.5
        pts = {}
        for sh in g.shapes:
            c, n, tg, up = fr[sh]
            p = c + n * (lift + 0.006 * s * math.cos(tt * math.pi)) + tg * (0.07 * s * tt) + up * (0.008 * s * math.cos(tt * math.pi))
            pts[sh] = np.array([p - up * 0.007 * s, p + up * 0.007 * s])
        rows.append(g.add(pts, [dict(W)] * 2))
    n0 = len(g.faces)
    g.connect(rows, lambda k, c: metal, closed=False)
    faces += list(range(n0, len(g.faces)))
    _disc(g, {sh: fr[sh][0] + fr[sh][1] * (lift + 0.012 * s) for sh in g.shapes}, {sh: fr[sh][1] for sh in g.shapes},
          0.011 * s, W, None, jewel, lift=0.0)
    feather = paint(a.get("feather"), ("dye", "primary"))
    light = shade(a.get("feather") or ["dye", "primary"], "dye_light")
    for ang, length in zip(a.get("angles", [-30.0, 0.0, 28.0]), a.get("lengths", [0.11, 0.14, 0.10])):
        r = math.radians(ang)
        rows = []
        for i in range(7):
            tt = i / 6
            pts = {}
            for sh in g.shapes:
                c, n, tg, up = fr[sh]
                d = unit(up * math.cos(r) + tg * math.sin(r) + n * 0.25)
                q = c + n * (lift + 0.01 * s) + d * (length * s * tt) + np.array([0.0, 1.0, 0.0]) * (0.03 * s * tt * tt)
                half = 0.016 * s * math.sin(math.pi * min(1.0, tt * 1.15 + 0.05)) + 0.001
                side = unit(np.cross(d, n))
                pts[sh] = np.array([q - side * half, q, q + side * half])
            rows.append(g.add(pts, [dict(W)] * 3))
        n0 = len(g.faces)
        g.connect(rows, lambda kk, c: (feather if c == 0 else light), closed=False)
        faces += list(range(n0, len(g.faces)))
    return [(faces, 0.0015, shade(a.get("feather") or ["dye", "primary"]), metal)]


def earflaps(fs, a):
    """Knitted flaps over the ears from a hat's band loop, ending in a point,
    with a braided tie hanging from each (earflap hats)."""
    g, s = fs.g, fs.g.frame.head_scale
    loop = fs.loops[a.get("at", "band")]
    ids = list(loop.ids)
    M = len(ids)
    pnt = paint(a.get("paint"))
    tie_p = paint(a.get("tie"), ("dye", "trim"))
    half = a.get("half_angle", 26.0)
    drop = a.get("drop", 0.075)
    faces = []
    for centre in (90.0, 270.0):
        cols = [j for j in range(M) if abs(((360.0 * j / M - centre + 180.0) % 360.0) - 180.0) <= half]
        cols.sort(key=lambda j: ((360.0 * j / M - centre + 180.0) % 360.0) - 180.0)
        top = [ids[j] for j in cols]
        rings = [np.array(top)]
        W = {"DEF-spine.006": 1.0}
        m = len(top)
        out = np.array([1.0 if centre < 180 else -1.0, 0.0, 0.0])
        for k in (1, 2, 3):
            tt = k / 3
            pts = {}
            for sh in g.shapes:
                P = g.positions(sh)[top]
                c = P.mean(axis=0)
                narrow = 1.0 - 0.8 * tt ** 1.3
                q = c + (P - c) * np.array([narrow, narrow, 0.0])
                q[:, 2] = P[:, 2] - drop * s * tt - (1 - np.abs(np.linspace(-1, 1, m))) * 0.012 * s * tt
                pts[sh] = q + out * (0.004 + 0.006 * tt) * s
            rings.append(g.add(pts, [dict(W)] * m))
        # The flaps clear the cheeks, jaw and neck they hang over.
        from . import fit as F

        F.enforce_clearance(g, [int(v) for r in rings[1:] for v in r], 0.007, ("head", "neck"))
        n0 = len(g.faces)
        g.connect(rings, lambda k, c: pnt, closed=False)
        faces += list(range(n0, len(g.faces)))
        tip_id = int(rings[-1][m // 2])
        tie_rows = []
        for i in range(6):
            tt = i / 5
            pts = {}
            for sh in g.shapes:
                q = g.positions(sh)[tip_id] + np.array([0.0, 0.006 * s * tt, -a.get("tie_length", 0.06) * s * tt]) + out * 0.004 * s * tt
                pts[sh] = np.array([q - [0.004 * s, 0, 0], q + [0.004 * s, 0, 0]])
            tie_rows.append(g.add(pts, [{"DEF-spine.006": 1.0}] * 2))
        n1 = len(g.faces)
        g.connect(tie_rows, lambda k, c: tie_p, closed=False)
        faces += list(range(n1, len(g.faces)))
    return [(faces, 0.003, shade(a.get("paint")), pnt)]


ADDONS = {"hood": hood, "collar_leaf": collar_leaf, "open_collar": open_collar, "tie": tie, "v_stripes": v_stripes, "buttons": buttons,
          "pompom": pompom, "crest": crest, "brim": brim, "crown": crown, "cape": cape, "lapels": lapels, "bow": bow, "comb": comb,
          "earflaps": earflaps}
TRIMS.update({"ruffle": ruffle, "roll": roll})


# ----------------------------------------------------------------------------
# Neck weights
# ----------------------------------------------------------------------------

NECK_BONES = ("DEF-spine.004", "DEF-spine.005", "DEF-spine.006")


def even_neck_weights(fs, passes: int = 4) -> int:
    """Evens out the head-bone weight round a top's neck. On every vertex
    weighted to a neck or head bone, the head bone's share takes, `passes`
    times, half its own value and half its mesh neighbours' mean; what it
    loses goes to the upper neck bone, what it gains comes from the neck
    bones first, then from the rest. The balance between neck and chest that
    the fit gave stays: evening every bone dragged the neckline's neck
    weights toward the chest rows below it, and the run pushed the throat
    through the linen shirt's open collar.

    Fitted vertices take the skin weights under them, and the wrapped
    female's neck skin carries 0-0.4 head weight unevenly from vertex to
    vertex (MakeHuman's weights on her neck), so neighbouring collar
    vertices turned by different amounts with the head and collars shredded
    (the neck agent's finding, 2026-11-04). Returns the vertices evened."""
    g = fs.g
    head, upper = NECK_BONES[2], NECK_BONES[1]
    nb: list[set] = [set() for _ in range(g.n)]
    for f in g.faces:
        for i, v in enumerate(f):
            nb[v].add(f[i - 1])
            nb[v].add(f[(i + 1) % len(f)])
    ids = [v for v in range(g.n) if sum(g.weights[v].get(b, 0.0) for b in NECK_BONES) > 0.02 and nb[v]]
    for _ in range(passes):
        target = {v: 0.5 * g.weights[v].get(head, 0.0) + 0.5 * float(np.mean([g.weights[u].get(head, 0.0) for u in nb[v]]))
                  for v in ids}
        for v, h1 in target.items():
            w = dict(g.weights[v])
            h0 = w.get(head, 0.0)
            d = h1 - h0
            if abs(d) < 1e-5:
                continue
            if d < 0:
                w[head] = h1
                w[upper] = w.get(upper, 0.0) - d
            else:
                need = d
                for pool in ((NECK_BONES[0], upper), tuple(k for k in w if k != head)):
                    have = sum(w.get(k, 0.0) for k in pool)
                    if have <= 1e-9:
                        continue
                    take = min(need, have)
                    for k in pool:
                        if k in w:
                            w[k] *= 1.0 - take / have
                    need -= take
                    if need <= 1e-9:
                        break
                w[head] = h0 + d - need
            g.weights[v] = top_weights(w)
    return len(ids)
