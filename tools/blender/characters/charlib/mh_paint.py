"""Regions and paint on the MakeHuman mesh, with crisp borders.

Every border of the current body (region borders that clothing `hides`
lists rely on, the underwear edges and bands, the brows) is a ring, row or
curve of our body layout (`body.BodyLayout`): limb rings are planes, the
shoulder-dome rows are cones around the dome centre, the head rows and the
hairline are curves of polar angle around the head centre. `CutMesh.cut`
splits the MakeHuman faces exactly along those surfaces (new vertices on the
crossed edges, with every shape key and weight interpolated), so on the new
topology the borders are as clean as on the old one and sit exactly where the
clothing hems built on the layout rings expect them.
"""

from __future__ import annotations

import math

import numpy as np

from . import head as H
from . import skeleton as K
from .body import DOME_EPS, BodyLayout

SKIN = (1.0, 0.0, 0.0)
HAIR = (0.0, 1.0, 0.0)
NONE = (0.0, 0.0, 0.0)

ARM_PARTS = ("upper_arm", "forearm", "hand", "f_index", "f_middle", "f_ring", "f_pinky", "thumb")
LEG_PARTS = ("thigh", "shin", "foot", "toe")


# ----------------------------------------------------------------------------
# Cutting
# ----------------------------------------------------------------------------


class CutMesh:
    """Polygons with per-vertex shapes/weights and per-face attributes."""

    def __init__(self, shapes: dict[str, np.ndarray], weights: list[dict[str, float]], faces: list[tuple[int, ...]],
                 attrs: dict[str, list]):
        self.shapes = {k: np.asarray(v, dtype=np.float64) for k, v in shapes.items()}
        self.weights = list(weights)
        self.faces = [tuple(f) for f in faces]
        self.attrs = {k: list(v) for k, v in attrs.items()}
        self.cuts = 0

    @property
    def base(self) -> np.ndarray:
        return self.shapes["base"]

    def centroids(self) -> np.ndarray:
        P = self.base
        sizes = np.fromiter((len(f) for f in self.faces), dtype=np.int64, count=len(self.faces))
        idx = np.fromiter((v for f in self.faces for v in f), dtype=np.int64, count=int(sizes.sum()))
        starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
        return np.add.reduceat(P[idx], starts, axis=0) / sizes[:, None]

    def _new_vertex(self, a: int, b: int, t: float) -> int:
        for k, v in self.shapes.items():
            self.shapes[k] = np.vstack([v, v[a] + (v[b] - v[a]) * t])
        wa, wb = self.weights[a], self.weights[b]
        w = {n: wa.get(n, 0.0) * (1 - t) + wb.get(n, 0.0) * t for n in set(wa) | set(wb)}
        self.weights.append(w)
        return len(self.weights) - 1

    def cut(self, field, face_ok: np.ndarray, snap: float = 0.0012) -> int:
        """Split the faces in `face_ok` along field(points) = 0 (a metric
        field: plane distance or arc length). Vertices within `snap` of the
        border count as on it, so no sliver faces are made."""
        P = self.base
        f = np.asarray(field(P), dtype=np.float64)
        f = np.where(np.abs(f) < snap, 0.0, f)
        sign = np.sign(f)
        crossings: dict[tuple[int, int], tuple[int, int, float]] = {}
        todo = []
        for fi in np.nonzero(face_ok)[0]:
            face = self.faces[fi]
            s = sign[list(face)]
            if not ((s > 0).any() and (s < 0).any()):
                continue
            todo.append(fi)
            for i in range(len(face)):
                a, b = face[i], face[(i + 1) % len(face)]
                if sign[a] * sign[b] < 0:
                    key = (min(a, b), max(a, b))
                    if key not in crossings:
                        t = f[a] / (f[a] - f[b])
                        crossings[key] = (a, b, t)
        if not crossings:
            return 0
        # All new vertices at once (every shape key and weight interpolated).
        items = list(crossings.items())
        ia = np.array([c[0] for _, c in items], dtype=np.int64)
        ib = np.array([c[1] for _, c in items], dtype=np.int64)
        tt = np.array([c[2] for _, c in items])[:, None]
        n0 = len(self.weights)
        for k, v in self.shapes.items():
            self.shapes[k] = np.vstack([v, v[ia] + (v[ib] - v[ia]) * tt])
        for _, (a, b, t) in items:
            wa, wb = self.weights[a], self.weights[b]
            self.weights.append({n: wa.get(n, 0.0) * (1 - t) + wb.get(n, 0.0) * t for n in set(wa) | set(wb)})
        new = {key: n0 + i for i, (key, _) in enumerate(items)}
        if not hasattr(self, "splits"):
            self.splits = {}
        self.splits.update(new)  # split edge (a, b) -> new vertex (for merging sub-meshes back)
        fz = np.concatenate([f, np.zeros(len(new))])
        split = set(todo)
        crossed = set(ia.tolist()) | set(ib.tolist())
        faces, attrs = [], {k: [] for k in self.attrs}
        for fi, face in enumerate(self.faces):
            if fi not in split and crossed.isdisjoint(face):
                faces.append(face)  # untouched
                for k in attrs:
                    attrs[k].append(self.attrs[k][fi])
                continue
            ring = []
            for i in range(len(face)):
                a, b = face[i], face[(i + 1) % len(face)]
                ring.append(a)
                v = new.get((min(a, b), max(a, b)))
                if v is not None:
                    ring.append(v)
            parts = [ring]
            if fi in split:
                zeros = [k for k, v in enumerate(ring) if fz[v] == 0.0]
                n = len(ring)
                if len(zeros) == 2 and (zeros[1] - zeros[0]) % n not in (1, n - 1):
                    i0, i1 = zeros
                    a_part = ring[i0:i1 + 1]
                    b_part = ring[i1:] + ring[:i0 + 1]
                    if len(a_part) >= 3 and len(b_part) >= 3:
                        parts = [a_part, b_part]
            for p in parts:
                faces.append(tuple(p))
                for k in attrs:
                    attrs[k].append(self.attrs[k][fi])
        self.faces = faces
        self.attrs = attrs
        self.cuts += 1
        return len(new)


# ----------------------------------------------------------------------------
# Border fields from the layout
# ----------------------------------------------------------------------------


def _periodic(theta_deg: np.ndarray, cols_deg: np.ndarray, vals: np.ndarray) -> np.ndarray:
    order = np.argsort(cols_deg)
    xs = np.concatenate([cols_deg[order] - 360.0, cols_deg[order], cols_deg[order] + 360.0])
    ys = np.concatenate([vals[order]] * 3)
    return np.interp(theta_deg % 360.0, xs, ys)


class Borders:
    """Every border of the layout's regions and paint, as metric fields."""

    def __init__(self, lay: BodyLayout, eye_offset: np.ndarray | None = None):
        """`eye_offset`: where the visible eyes sit relative to our sculpt's
        eyes; the brows (drawn relative to the eyes) follow it."""
        b = lay.b
        self.eye_offset = np.zeros(3) if eye_offset is None else np.asarray(eye_offset, dtype=np.float64)
        self.lay = lay
        self.b = b
        sc = lay.sc
        wi = int(np.argmin(np.abs(lay.horiz_z - b.waist_z)))
        self.z_waist = float(lay.horiz_z[wi])
        self.z_waist2 = float(lay.horiz_z[wi + 1])
        self.dome = lay.dome_centre.copy()
        fp = H.FaceParams(**lay.face_defaults())
        phis = H.row_phis(fp)
        R = H.ROW_INDEX
        self.cols = H.COLS_DEG
        self.throat = phis[R["throat"]]
        self.brow_top = phis[R["brow_top"]]
        self.brow_bot = phis[R["brow_bottom"]]
        self.brow_in = H.HALF_COLS_DEG[H.BROW_COLS[0]]
        self.brow_out = H.HALF_COLS_DEG[H.BROW_COLS[1]]
        self.centre = K.head_centre(b)
        self.arm = {}
        ul, fl = b.upper_arm_len, b.forearm_len
        an = lay.positions()
        for side in (1, -1):
            tube = lay.tubes[f"arm_{side}"]
            loop = an[lay.hole_boundary(side)]
            c = loop.mean(axis=0)
            _, _, vt = np.linalg.svd(loop - c)
            n = vt[2] * (1 if vt[2][0] * side > 0 else -1)
            s = np.array(tube.s_values)
            je = int(np.nonzero(s <= ul + 0.004)[0].max())
            jw = int(np.nonzero(s <= ul + fl + 0.004)[0].max())
            self.arm[side] = {
                "hole": (c, n),
                "elbow": (tube.rings[je].centre, tube.rings[je].tangent),
                "wrist": (tube.rings[jw].centre, tube.rings[jw].tangent),
            }
        self.leg = {}
        knee_thr = b.knee[2] + 0.025 * sc
        for side in (1, -1):
            tube = lay.tubes[f"leg_{side}"]
            zs = [z for z in tube.s_values if z is not None]
            jk = next(j for j in range(len(zs) - 1) if 0.5 * (zs[j] + zs[j + 1]) <= knee_thr)
            self.leg[side] = {"hips": zs[0], "trim_top": zs[1], "shorts": zs[2], "knee": zs[jk], "ankle": zs[-1]}

    # ---------------------------------------------------------------- fields
    def head_polar(self, P: np.ndarray):
        q = (P - self.centre)
        loc = np.stack([q[:, 0], -q[:, 1], q[:, 2]], axis=1)
        r = np.linalg.norm(loc, axis=1)
        phi = np.degrees(np.arccos(np.clip(loc[:, 2] / np.maximum(r, 1e-9), -1, 1)))
        theta = np.degrees(np.arctan2(loc[:, 0], loc[:, 1])) % 360.0
        return r, phi, theta

    def head_field(self, curve) -> callable:
        """> 0 above the curve phi = curve(theta) (toward the crown)."""
        def f(P):
            r, phi, theta = self.head_polar(P)
            return (curve(theta) - phi) * np.radians(1.0) * r
        return f

    def hairline(self):
        return self.head_field(lambda th: np.array([H.hairline_phi(t) for t in th]))

    def throat_line(self):
        return self.head_field(lambda th: _periodic(th, self.cols, self.throat))

    def brow_fields(self):
        off = self.eye_offset
        top0 = self.head_field(lambda th: _periodic(th, self.cols, self.brow_top))
        bot0 = self.head_field(lambda th: _periodic(th, self.cols, self.brow_bot))
        top = lambda P: top0(P - off)  # noqa: E731
        bot = lambda P: bot0(P - off)  # noqa: E731

        def side_angle(P):
            r, phi, theta = self.head_polar(P - off)
            return r, np.where(theta <= 180.0, theta, 360.0 - theta)

        def inner(P):
            r, s = side_angle(P)
            return (s - self.brow_in) * np.radians(1.0) * r

        def outer(P):
            r, s = side_angle(P)
            return (self.brow_out - s) * np.radians(1.0) * r

        # Brow: below the top curve, above the bottom curve, between the ends.
        return [lambda P: -top(P), bot, inner, outer]

    def dome_polar(self, P: np.ndarray):
        d = P - self.dome
        hor = np.linalg.norm(d[:, :2], axis=1)
        elev = np.degrees(np.arctan2(d[:, 2], hor))
        theta = np.degrees(np.arctan2(d[:, 0], -d[:, 1])) % 360.0
        return np.linalg.norm(d, axis=1), elev, theta

    def neck_line(self):
        """> 0 above dome row 0 (the neck)."""
        def f(P):
            r, elev, _ = self.dome_polar(P)
            return (elev - DOME_EPS[0]) * np.radians(1.0) * r
        return f

    def vest_line(self):
        """> 0 below the undershirt neckline (dome row 3 at the front, 2 behind)."""
        def f(P):
            r, elev, theta = self.dome_polar(P)
            side = np.where(theta <= 180.0, theta, 360.0 - theta)
            t = np.clip((side - 35.0) / 20.0, 0.0, 1.0)
            limit = DOME_EPS[3] + (DOME_EPS[2] - DOME_EPS[3]) * t * t * (3 - 2 * t)
            return (limit - elev) * np.radians(1.0) * r
        return f

    @staticmethod
    def plane(c: np.ndarray, n: np.ndarray):
        n = n / np.linalg.norm(n)
        return lambda P: (P - c) @ n

    @staticmethod
    def z_plane(z: float):
        return lambda P: P[:, 2] - z


# ----------------------------------------------------------------------------
# Cuts and classification
# ----------------------------------------------------------------------------


def _stem_side(name: str) -> tuple[str, int]:
    side = 1 if name.endswith(".L") else -1 if name.endswith(".R") else 0
    s = name[:-2] if side else name
    if s.startswith(("f_", "thumb")):
        s = s.split(".0")[0]
    return s, side


def apply_cuts(cm: CutMesh, bd: Borders, brows: bool = True) -> dict[str, int]:
    """Cut every border into the mesh (neutral shape). Returns new vertex
    counts. `brows=False` leaves out the layout's brows (the anime face of
    mh_face.py draws its own)."""
    b = bd.b
    out = {}

    def parts():
        return [_stem_side(p) for p in cm.attrs["part"]]

    def run(name, field, ok_fn, near=0.05):
        C = cm.centroids()
        ps = parts()
        f = field(C)
        ok = np.array([(not e) and ok_fn(c, p) and abs(v) < near
                       for c, p, e, v in zip(C, ps, cm.attrs["eye"], f)])
        out[name] = cm.cut(field, ok)

    trunk = ("torso", "neck", "head", "shoulder")
    run("hairline", bd.hairline(), lambda c, p: p[0] in ("head", "neck"), near=0.03)
    run("throat", bd.throat_line(), lambda c, p: p[0] in ("head", "neck", "torso"), near=0.03)
    run("neck", bd.neck_line(), lambda c, p: p[0] in trunk or (p[0] == "upper_arm" and abs(c[0]) < 0.2), near=0.04)
    run("vest", bd.vest_line(), lambda c, p: p[0] in trunk + ("upper_arm",) and abs(c[0]) < 0.25, near=0.04)
    for side in (1, -1):
        a = bd.arm[side]
        on_side = lambda c, p, s=side: c[0] * s > 0.04  # noqa: E731
        run(f"armhole_{side}", bd.plane(*a["hole"]),
            lambda c, p, f=on_side, zc=a["hole"][0][2]: f(c, p) and p[0] in trunk + ARM_PARTS[:1] and c[2] > zc - 0.08,
            near=0.06)
        run(f"elbow_{side}", bd.plane(*a["elbow"]), lambda c, p, f=on_side: f(c, p) and p[0] in ARM_PARTS, near=0.05)
        run(f"wrist_{side}", bd.plane(*a["wrist"]), lambda c, p, f=on_side: f(c, p) and p[0] in ARM_PARTS, near=0.04)
    for z in (bd.z_waist, bd.z_waist2):
        run(f"waist_{z:.3f}", bd.z_plane(z), lambda c, p: p[0] in trunk + LEG_PARTS and abs(c[0]) < 0.26, near=0.03)
    for side in (1, -1):
        lg = bd.leg[side]
        leg_ok = (lambda c, p, s=side: c[0] * s > -0.002 and abs(c[0]) < 0.3
                  and (p[0] in LEG_PARTS or (p[0] == "torso" and c[2] < b.crotch_z + 0.08)))
        for key in ("hips", "trim_top", "shorts", "knee", "ankle"):
            run(f"leg_{key}_{side}", bd.z_plane(lg[key]), leg_ok, near=0.03)
    head_ok = lambda c, p: p[0] == "head"  # noqa: E731
    for i, fld in enumerate(bd.brow_fields() if brows else []):
        run(f"brow_{i}", fld, head_ok, near=0.02)
    return out


def classify(cm: CutMesh, bd: Borders) -> tuple[list[str], list[str], list[tuple]]:
    """Region, swatch and dye mask of every (non-eye) face."""
    b = bd.b
    C = cm.centroids()
    hair_f = bd.hairline()(C)
    throat_f = bd.throat_line()(C)
    neck_f = bd.neck_line()(C)
    vest_f = bd.vest_line()(C)
    brow = np.all([f(C) > 0 for f in bd.brow_fields()], axis=0)
    regions, swatches, masks = [], [], []
    trunk = ("torso", "neck", "head", "shoulder")
    for i, c in enumerate(C):
        stem, side = _stem_side(cm.attrs["part"][i])
        fl = cm.attrs["flags"][i]
        if cm.attrs["eye"][i]:
            regions.append("head")
            swatches.append("skin")
            masks.append(NONE)
            continue
        x_side = 1 if c[0] >= 0 else -1
        branch = "trunk"
        if stem in ARM_PARTS or (stem == "shoulder" and abs(c[0]) > 0.08 and c[2] > bd.arm[x_side]["hole"][0][2] - 0.08):
            a = bd.arm[x_side]
            if bd.plane(*a["hole"])(c[None])[0] > 0 or stem in ARM_PARTS[1:]:
                branch = "arm"
        if stem in LEG_PARTS:
            branch = "leg"
        region = None
        swatch, mask = "skin", SKIN
        if branch == "arm":
            a = bd.arm[x_side]
            if bd.plane(*a["wrist"])(c[None])[0] > 0:
                region = "hands"
            elif bd.plane(*a["elbow"])(c[None])[0] > 0:
                region = "forearms"
            else:
                region = "upper_arms"
        elif branch == "leg":
            lg = bd.leg[x_side]
            z = c[2]
            if z > lg["hips"]:
                region = "hips"
                swatch, mask = "underwear", NONE
            elif z > lg["knee"]:
                region = "thighs"
                if z > lg["shorts"]:
                    swatch, mask = ("underwear_trim" if z <= lg["trim_top"] else "underwear"), NONE
            elif z > lg["ankle"]:
                region = "calves"
            else:
                region = "feet"
        else:
            if throat_f[i] > 0 and stem in ("head", "neck"):
                region = "scalp" if hair_f[i] > 0 else "head"
            elif neck_f[i] > 0:
                region = "neck"
            elif c[2] > bd.z_waist:
                region = "torso"
                if vest_f[i] > 0:
                    swatch, mask = "underwear", NONE
            else:
                region = "hips"
                swatch, mask = ("underwear_trim" if c[2] > bd.z_waist2 else "underwear"), NONE
        if fl.get("ears"):
            region = "ears"
        if region == "head" and brow[i]:
            swatch, mask = "brow", HAIR
        if fl.get("lips"):
            swatch, mask = ("lips_lower" if fl.get("lower_lip") else "lips"), SKIN
        elif fl.get("inside-mouth"):
            swatch, mask = "mouth", NONE
        elif fl.get("fingernails") or fl.get("toenails"):
            swatch, mask = "nails", SKIN
        elif fl.get("lash"):
            swatch, mask = "lash", NONE
        regions.append(region)
        swatches.append(swatch)
        masks.append(mask)
    return regions, swatches, masks
