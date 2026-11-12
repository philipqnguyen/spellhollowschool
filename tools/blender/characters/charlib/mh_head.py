"""A dedicated, scripted anime head for the MakeHuman bodies.

MakeHuman's head is realistic (a long skull, a forward mouth and a broad,
detailed face) and could not be pushed into the Genshin-like look of
docs/art/style-guide.md with offsets alone, so `mh_body` replaces it: every
MakeHuman face above one of its own neck edge loops is removed and this head
is welded on in its place.

* **Shape** (`head_sdf`): head-local centimetres (X left, F forward, U up,
  origin `skeleton.head_centre`, scaled by `Body.head_scale`). The cranium is
  exactly the shared scalp cage (the cranium of `head.head_sdf`, which every
  hairstyle and stiff hat is fitted to), so any hairstyle fits either head and
  adding a hairstyle never needs head changes. The face is a loft of
  superellipse sections: its front-view outline (`width`) narrows from the
  cheekbones into a small pointed chin, its profile (`front`) is flat from
  the brow to the chin with no forward mouth, and a plane under the jaw makes
  a clean jawline. A tiny nose ridge is the only feature in the geometry; the
  eyes, brows, mouth and nose shade are painted (cut in by `mh_face`).
* **Mesh** (`AnimeHead`): one quad grid sampled by rays from the head centre
  (a pole at the crown, columns denser over the face), so it has clean, even
  topology and identical topology for every shape key. Its last row lies on
  the MakeHuman neck just above the seam loop and a strip of triangles welds
  it to that loop (shared vertices: no visible seam, normals match because
  they are averaged across it). Small ears are separate shells (`ears`
  region, hidden by hair that covers them).
* **Skinning**: MakeHuman's own weights from the removed head and neck,
  transferred by nearest surface point, so the head and neck bones and the
  seam deform exactly like the body below.
* **Keys**: face sliders change the loft parameters (jaw, cheeks, nose);
  expressions and the brow sliders are made after the paint cuts in the face
  frame (`expression_keys`), like `mh_face.eye_keys` does for the eyes and
  the mouth.
* **Normals** (`face_normals`): the face front takes the normals of an
  ellipsoid fitted to it, blended out toward the jaw, ears and hairline, so
  cel shading gives one clean shadow shape and the face reads flat-lit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from . import head as H
from . import sdf as S
from . import skeleton as K
from . import weights as WT

# ----------------------------------------------------------------------------
# Shape parameters (head-local cm for a head of scale 1)
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class HeadParams:
    # Front-view half width of the face per height U: (U, half width).
    width: tuple = ((5.0, 6.4), (2.0, 6.95), (0.0, 7.2), (-2.0, 7.1), (-4.0, 6.75), (-6.0, 5.95), (-7.5, 4.95),
                    (-9.0, 3.6), (-10.4, 2.15), (-11.2, 1.2), (-11.7, 0.6))
    # Profile: the face front on the midline, (U, F).
    front: tuple = ((6.0, 8.55), (4.0, 9.05), (2.0, 9.2), (0.0, 9.05), (-2.0, 8.95), (-4.0, 8.85), (-5.5, 8.7),
                    (-7.0, 8.5), (-8.5, 8.2), (-9.8, 7.7), (-10.8, 6.95), (-11.6, 5.9))
    # Depth of the widest point of each section, (U, F): just in front of
    # the ears at the cheeks, the jaw angle lower down.
    widest: tuple = ((4.0, 2.0), (0.0, 2.4), (-4.0, 2.2), (-7.0, 1.6), (-9.5, 2.6), (-11.5, 4.0))
    back_f: float = -4.5  # where the sections end behind (inside the cranium and neck)
    front_p: float = 2.5  # superellipse exponent of the face front (flat front, rounded sides)
    chin_u: float = -11.35  # chin bottom (U); the jawline plane runs from here ...
    gonion: tuple = (0.6, -7.9)  # ... back to under the jaw angle (F, U)
    neck: tuple = (4.55, 4.1, -0.4)  # neck half width, half depth and centre F (above the seam)
    nose: float = 1.0  # nose ridge size
    jaw: float = 1.0  # lower-face width multiplier (face_jaw_* sliders)
    cheek_high: float = 0.0  # fuller, higher cheekbones (face_cheek_high)
    cheek_soft: float = 0.0  # softer, fuller lower cheeks (face_cheek_soft)
    angular: float = 0.0  # squarer jaw angle (male)


# Male (body_a): a slightly wider, more angular jaw and a firmer chin.
# Female (body_b): narrower, softer, a smaller pointed chin.
PARAMS = {
    "body_a": HeadParams(angular=0.35),
    "body_b": HeadParams(
        width=((5.0, 6.4), (2.0, 6.9), (0.0, 7.1), (-2.0, 6.95), (-4.0, 6.5), (-6.0, 5.55), (-7.5, 4.45),
               (-9.0, 3.1), (-10.3, 1.75), (-11.1, 0.95), (-11.6, 0.5)),
        front=((6.0, 8.55), (4.0, 9.05), (2.0, 9.2), (0.0, 9.05), (-2.0, 8.95), (-4.0, 8.85), (-5.5, 8.65),
               (-7.0, 8.4), (-8.5, 8.05), (-9.8, 7.5), (-10.8, 6.7), (-11.5, 5.7)),
        chin_u=-11.3, gonion=(0.9, -7.6), nose=0.8, angular=0.0),
}
# Face sliders as parameter offsets (expressions and brows: `expression_keys`).
FACE_KEYS = {
    "face_jaw_narrow": dict(jaw=-0.085),
    "face_jaw_wide": dict(jaw=+0.09, angular=+0.25),
    "face_cheek_soft": dict(cheek_soft=1.0),
    "face_cheek_high": dict(cheek_high=1.0),
    "face_nose_small": dict(nose=-0.35),
    "face_nose_large": dict(nose=+0.45),
}
EYE_POS = {"body_a": (3.25, -0.35), "body_b": (3.2, -0.45)}  # left eye centre (X, U), cm
MOUTH_U = {"body_a": -6.75, "body_b": -6.7}
NOSE_U = {"body_a": -3.55, "body_b": -3.6}

# Grid: left-half column azimuths (degrees, 0 = front), denser over the face.
HALF_COLS = (0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.5, 37.0, 42.0, 47.5, 53.0, 59.0, 65.0, 72.0, 79.0,
             86.5, 94.0, 102.0, 110.0, 119.0, 128.0, 138.0, 148.0, 158.5, 169.0, 180.0)
COLS = np.array(list(HALF_COLS) + [360.0 - a for a in reversed(HALF_COLS[1:-1])])
NCOL = len(COLS)
NROW = 36  # rows below the crown pole (the last lies on the MakeHuman neck)
SEAM_GAP = 0.7  # cm between the last head row and the MakeHuman seam loop
BLEND_ROWS = 4  # rows above the last that blend from our neck onto MakeHuman's
EAR_SCALE = 0.78  # anime ears: smaller than our sculpt's
SEAM_BELOW_CHIN = 0.028  # metres: the seam loop is the MakeHuman neck loop nearest this far below the chin


def _curve(ctrl):
    """Smooth (Catmull-Rom) curve through (x, y) control points, x ascending
    or descending; returns f(x) with flat extrapolation."""
    c = np.array(sorted(ctrl), dtype=np.float64)
    pts = np.vstack([2 * c[0] - c[1], c, 2 * c[-1] - c[-2]])
    xs, ys = [], []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for t in np.linspace(0.0, 1.0, 40, endpoint=False):
            t2, t3 = t * t, t * t * t
            q = 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
            xs.append(q[0])
            ys.append(q[1])
    xs.append(c[-1, 0])
    ys.append(c[-1, 1])
    xs, ys = np.array(xs), np.array(ys)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    return lambda x: np.interp(x, xs, ys)


def head_sdf(hp: HeadParams):
    """SDF of the anime head (head-local cm, points (X, F, U))."""
    width = _curve(hp.width)
    front = _curve(hp.front)
    widest = _curve(hp.widest)
    cu = hp.chin_u
    cf = float(front(np.array([cu]))[0]) - 0.25
    gf, gu = hp.gonion
    t = np.array([cf - gf, cu - gu])
    t /= np.linalg.norm(t)
    n_jaw = np.array([t[1], -t[0]])  # (F, U) normal pointing down and back (outside, below the jaw)
    if n_jaw[1] > 0:
        n_jaw = -n_jaw

    def f(p: np.ndarray) -> np.ndarray:
        X, F, U = p[:, 0], p[:, 1], p[:, 2]
        ax = np.abs(X)
        # Cranium: exactly the shared scalp cage (head.head_sdf's cranium).
        m = np.stack([ax, F, U], axis=1)
        cranium = S.ellipsoid(p, (0.0, -0.6, 2.0), (7.6, 9.9, 10.05))
        cranium = S.smax(cranium, S.plane(m, (7.3, 0, 0), (1, 0, 0)), 1.6)
        # Face loft.
        W = width(U)
        low = S.smoothstep(-3.0, -7.0, U)  # 0 above the cheeks, 1 at the jaw
        W = W * (1.0 + (hp.jaw - 1.0) * low)
        W = W + 0.28 * hp.cheek_high * np.exp(-((U + 2.2) / 1.6) ** 2) + 0.32 * hp.cheek_soft * np.exp(-((U + 5.6) / 1.9) ** 2)
        W = W - 0.12 * hp.cheek_soft * np.exp(-((U + 2.0) / 1.4) ** 2)
        Ff = front(U)
        Fw = widest(U)
        Df = np.maximum(Ff - Fw, 0.4)
        Db = np.maximum(Fw - hp.back_f, 0.4)
        fr = F >= Fw
        D = np.where(fr, Df, Db)
        pe = np.where(fr, hp.front_p, 2.0)
        # The jaw angle squares off a little (male): a higher exponent there.
        pe = pe + hp.angular * np.exp(-((U + 7.6) / 1.6) ** 2) * fr * 1.6
        k = ((ax / W) ** pe + (np.abs(F - Fw) / D) ** pe) ** (1.0 / pe)
        face = (k - 1.0) * np.minimum(W, D) * 0.8
        face = S.smax(face, U - 3.2, 1.4)  # above the brows the head is the shared cranium
        jaw_plane = (F - gf) * n_jaw[0] + (U - gu) * n_jaw[1]
        face = S.smax(face, jaw_plane, 0.9)
        d = S.smin(cranium, face, 1.6)
        # Neck: an elliptic cylinder matching the MakeHuman neck at the seam.
        nx, nf, nc = hp.neck
        neck = (np.sqrt((X / nx) ** 2 + ((F - nc) / nf) ** 2) - 1.0) * min(nx, nf)
        neck = S.smax(neck, U + 3.0, 2.0)
        d = S.smin(d, neck, 1.7)
        # The nose: a small ridge with a slightly pointed tip.
        if hp.nose > 0.05:
            n = hp.nose
            nu = -3.45
            tipf = float(front(np.array([nu]))[0]) + 0.42 * n
            topf = float(front(np.array([-1.4]))[0]) - 0.2
            ridge = S.capsule(p, (0.0, topf, -1.4), (0.0, tipf, nu), 0.16 * n, 0.34 * n)
            tip = S.ellipsoid(p, (0.0, tipf - 0.12 * n, nu - 0.12), (0.5 * n, 0.4 * n, 0.38 * n))
            d = S.smin(d, S.smin(ridge, tip, 0.25), 0.55 * n)
        return d

    return f


def apply_key(hp: HeadParams, deltas: dict, w: float = 1.0) -> HeadParams:
    return replace(hp, **{k: getattr(hp, k) + v * w for k, v in deltas.items()})


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------


def to_local(b, P: np.ndarray) -> np.ndarray:
    """Blender metres -> head-local cm (X, F, U)."""
    c = K.head_centre(b)
    q = (P - c) / (0.01 * b.head_scale)
    return np.stack([q[:, 0], -q[:, 1], q[:, 2]], axis=1)


def to_world(b, L: np.ndarray) -> np.ndarray:
    return H.to_world(L, K.head_centre(b), b.head_scale)


def polar(L: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r = np.linalg.norm(L, axis=1)
    phi = np.degrees(np.arccos(np.clip(L[:, 2] / np.maximum(r, 1e-9), -1, 1)))
    theta = np.degrees(np.arctan2(L[:, 0], L[:, 1])) % 360.0
    return phi, theta


def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ----------------------------------------------------------------------------
# The seam: a MakeHuman neck edge loop
# ----------------------------------------------------------------------------


def _edge_maps(faces):
    ef, vf = {}, {}
    for fi, f in enumerate(faces):
        for i in range(len(f)):
            e = (min(f[i], f[i - 1]), max(f[i], f[i - 1]))
            ef.setdefault(e, []).append(fi)
        for v in f:
            vf.setdefault(v, []).append(fi)
    return ef, vf


def neck_loops(V: np.ndarray, faces, centre: np.ndarray, z_lo: float, z_hi: float) -> list[list[int]]:
    """Closed edge loops around the neck (quad edge-loop walks from the front
    midline), as vertex lists."""
    ef, vf = _edge_maps(faces)

    def nxt(prev, cur):
        fs = vf.get(cur, [])
        if len(fs) != 4:
            return None
        adj = set(ef[(min(prev, cur), max(prev, cur))])
        cand = set()
        for fi in fs:
            f = faces[fi]
            k = f.index(cur)
            cand.add(f[k - 1])
            cand.add(f[(k + 1) % len(f)])
        cand.discard(prev)
        for c in cand:
            if adj.isdisjoint(ef[(min(c, cur), max(c, cur))]):
                return c
        return None

    seeds = np.nonzero((V[:, 2] > z_lo) & (V[:, 2] < z_hi) & (np.abs(V[:, 0]) < 0.003) & (V[:, 1] < centre[1]))[0]
    found = {}
    for s in seeds:
        for fi in vf.get(int(s), []):
            f = faces[fi]
            k = f.index(int(s))
            for nb in (f[k - 1], f[(k + 1) % len(f)]):
                if V[nb, 0] - V[s, 0] < 0.002:
                    continue
                loop = [int(s), int(nb)]
                closed = False
                for _ in range(300):
                    n = nxt(loop[-2], loop[-1])
                    if n is None:
                        break
                    if n == loop[0]:
                        closed = True
                        break
                    loop.append(n)
                if closed:
                    found.setdefault(tuple(sorted(loop)), loop)
    return list(found.values())


def pick_seam(b, V: np.ndarray, faces, chin_z: float) -> list[int]:
    """The neck loop nearest SEAM_BELOW_CHIN below the chin that goes round
    the neck almost level (not the jaw or collar loops)."""
    c = K.head_centre(b)
    loops = neck_loops(V, faces, c, chin_z - 0.09, chin_z + 0.005)
    target = chin_z - SEAM_BELOW_CHIN * b.head_scale
    best = None
    for lp in loops:
        P = V[lp]
        dz = float(P[:, 2].max() - P[:, 2].min())
        if dz > 0.045 or P[:, 1].max() < c[1] + 0.02:  # must reach round the back of the neck
            continue
        err = abs(float(P[:, 2].mean()) - target)
        if best is None or err < best[0]:
            best = (err, lp)
    if best is None:
        raise RuntimeError(f"{b.id}: no neck loop found below the chin ({len(loops)} loops)")
    return best[1]


def head_component(faces, seam: list[int], top_vertex: int) -> np.ndarray:
    """Faces on the head side of the seam loop (flood fill from the crown,
    never crossing a seam edge)."""
    seam_edges = {(min(a, b), max(a, b)) for a, b in zip(seam, seam[1:] + seam[:1])}
    ef, vf = _edge_maps(faces)
    start = vf[top_vertex][0]
    seen = {start}
    todo = [start]
    while todo:
        fi = todo.pop()
        f = faces[fi]
        for i in range(len(f)):
            e = (min(f[i], f[i - 1]), max(f[i], f[i - 1]))
            if e in seam_edges:
                continue
            for g in ef[e]:
                if g not in seen:
                    seen.add(g)
                    todo.append(g)
    out = np.zeros(len(faces), dtype=bool)
    out[list(seen)] = True
    return out


# ----------------------------------------------------------------------------
# The head mesh
# ----------------------------------------------------------------------------


@dataclass
class AnimeHead:
    """Our head welded onto a MakeHuman body (see the module docstring)."""

    b: object
    hp: HeadParams
    keep: np.ndarray  # MakeHuman skin vertex ids kept (body below the seam), in order
    remap: np.ndarray  # MakeHuman id -> combined id (-1 if removed)
    seam: list[int]  # MakeHuman ids of the seam loop
    kept_faces: np.ndarray  # indices into the MakeHuman skin faces that stay
    faces: list[tuple[int, ...]]  # combined faces: kept MakeHuman faces, then ours
    n_mh_faces: int
    n_keep: int
    grid: np.ndarray  # (NROW + 1 rows incl. pole handled separately) combined ids of our grid: [pole] + rows x cols
    ear_ids: dict = field(default_factory=dict)
    base: np.ndarray | None = None  # combined base positions
    head_local: np.ndarray | None = None  # our vertices, base, head-local cm
    weights_head: list = field(default_factory=list)
    face_flags: list = field(default_factory=list)  # per our face: {"ears": True} etc.
    col_of: np.ndarray | None = None  # per our grid vertex: column (-1 pole / ears)
    row_of: np.ndarray | None = None
    rays: np.ndarray | None = None  # per grid vertex (rows 1..), unit ray directions (head-local)
    blend: np.ndarray | None = None  # per grid vertex: 1 = on the MakeHuman neck surface, 0 = ours
    mh_hits: np.ndarray | None = None  # per grid vertex: hit distance on the MakeHuman neck (cm)
    seam_theta: np.ndarray | None = None

    @property
    def n_head(self) -> int:
        return len(self.head_local)

    # -------------------------------------------------------------- evaluate
    def head_points(self, hp: HeadParams) -> np.ndarray:
        """Our vertices (head-local cm) for parameters `hp` (same topology)."""
        sdf = head_sdf(hp)
        n_grid = 1 + NROW * NCOL
        out = self.head_local.copy()
        dirs = self.rays
        t = S.raycast(sdf, np.zeros_like(dirs), dirs, t_max=30.0, step_scale=0.5, min_step=0.003)
        pts = dirs * t[:, None]
        w = self.blend[:, None]
        pts = pts * (1 - w) + (dirs * self.mh_hits[:, None]) * w
        out[1:n_grid] = pts
        top = S.raycast(sdf, np.zeros((1, 3)), np.array([[0.0, 0.0, 1.0]]), t_max=30.0)
        out[0] = (0.0, 0.0, float(top[0]))
        return out

    def combine(self, mh: np.ndarray, head_local: np.ndarray | None = None) -> np.ndarray:
        """Combined vertex positions: MakeHuman's kept vertices from `mh`
        (any variant of the fitted body), ours from `head_local` (default:
        the base head), whose lowest rows follow the seam's movement."""
        hl = self.head_local if head_local is None else head_local
        P = to_world(self.b, hl)
        d_seam = mh[self.seam] - self.base_mh_seam
        if np.abs(d_seam).max() > 1e-7:
            # Interpolate the seam movement round the neck and fade it out up the head.
            th = self.seam_theta
            order = np.argsort(th)
            ths = np.concatenate([th[order] - 360.0, th[order], th[order] + 360.0])
            col_d = np.stack([np.interp(COLS, ths, np.tile(d_seam[order, k], 3)) for k in range(3)], axis=1)
            ids = np.nonzero(self.col_of >= 0)[0]
            fade = _smooth((self.row_of[ids] - (NROW - 8)) / 8.0)
            P[ids] += col_d[self.col_of[ids]] * fade[:, None]
        return np.vstack([mh[self.keep], P])


def _grid_faces(off: int) -> list[tuple[int, ...]]:
    """Pole fan and row quads of our grid (combined ids from `off`)."""
    def gv(r, c):
        return off + 1 + (r - 1) * NCOL + (c % NCOL)

    faces = [(off, gv(1, c), gv(1, c + 1)) for c in range(NCOL)]
    for r in range(1, NROW):
        for c in range(NCOL):
            faces.append((gv(r, c), gv(r + 1, c), gv(r + 1, c + 1), gv(r, c + 1)))
    return faces


def _zip(row_ids, row_th, loop_ids, loop_th) -> list[tuple[int, int, int]]:
    """Triangle strip between two closed rings ordered by increasing angle."""
    a_ids, b_ids = list(row_ids), list(loop_ids)
    a_th, b_th = np.asarray(row_th, dtype=np.float64), np.asarray(loop_th, dtype=np.float64)
    na, nb = len(a_ids), len(b_ids)
    # Start both at their smallest angle.
    ia0 = int(np.argmin(a_th))
    ib0 = int(np.argmin(b_th))
    A = [(a_ids[(ia0 + k) % na], (a_th[(ia0 + k) % na] - a_th[ia0]) % 360.0) for k in range(na)]
    B = [(b_ids[(ib0 + k) % nb], (b_th[(ib0 + k) % nb] - a_th[ia0]) % 360.0) for k in range(nb)]
    A.append((A[0][0], 360.0))
    if B[0][1] > 180:
        B[0] = (B[0][0], B[0][1] - 360.0)
    B.append((B[0][0], B[0][1] + 360.0))
    i = j = 0
    tris = []
    while i < na or j < nb:
        if j >= nb or (i < na and A[i + 1][1] <= B[j + 1][1]):
            tris.append((A[i][0], B[j][0], A[i + 1][0]))
            i += 1
        else:
            tris.append((A[i][0], B[j][0], B[j + 1][0]))
            j += 1
    return tris


def _orient_out(P: np.ndarray, faces, centre: np.ndarray) -> list[tuple[int, ...]]:
    """Faces wound so their normals point away from `centre` (majority vote)."""
    score = 0.0
    for f in faces:
        p = P[list(f)]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        score += float(n @ (p.mean(axis=0) - centre))
    return faces if score > 0 else [tuple(reversed(f)) for f in faces]


def _ear_shells(b) -> tuple[np.ndarray, list[tuple[int, ...]]]:
    """Two small ear shells (head-local cm): our sculpt's ear, scaled toward its root."""
    from .body import _ear_template

    verts, tris = _ear_template()
    root = verts[verts[:, 0] < np.percentile(verts[:, 0], 30)].mean(axis=0)
    v = root + (verts - root) * EAR_SCALE
    v = v + np.array([0.2, 0.35, 0.15])  # a little forward; the rim stands just off the head
    out_v, out_f = [], []
    for side in (1, -1):
        q = v.copy()
        q[:, 0] *= side
        off = len(out_v)
        out_v.extend(q.tolist())
        for t in tris:
            f = tuple(off + i for i in t)
            out_f.append(f if side > 0 else tuple(reversed(f)))
    return np.array(out_v), out_f


def build(b, V: np.ndarray, skin_faces: list[tuple[int, ...]], mh_weights: list[dict], chin_z: float) -> AnimeHead:
    """Cut MakeHuman's head off at a neck loop and weld ours on (base pose)."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    hp = PARAMS[b.id]
    n_mh = len(V)
    seam = pick_seam(b, V, skin_faces, chin_z)
    top = int(np.argmax(V[:, 2]))
    comp = head_component(skin_faces, seam, top)
    kept_faces = np.nonzero(~comp)[0]
    used = np.zeros(n_mh, dtype=bool)
    for fi in kept_faces:
        used[list(skin_faces[fi])] = True
    keep = np.nonzero(used)[0]
    remap = -np.ones(n_mh, dtype=np.int64)
    remap[keep] = np.arange(len(keep))
    n_keep = len(keep)
    # MakeHuman's removed head (and neck above the seam) as a ray target.
    head_tris = [(f[0], f[i], f[i + 1]) for fi in np.nonzero(comp)[0] for f in [skin_faces[fi]] for i in range(1, len(f) - 1)]
    bvh = BVHTree.FromPolygons([tuple(p) for p in V], head_tris, all_triangles=True)
    hc = K.head_centre(b)
    sc = 0.01 * b.head_scale
    seam_L = to_local(b, V[seam])
    seam_phi, seam_th = polar(seam_L)
    order = np.argsort(seam_th)
    ths = np.concatenate([seam_th[order] - 360.0, seam_th[order], seam_th[order] + 360.0])
    phis = np.tile(seam_phi[order], 3)
    rs = np.tile(np.linalg.norm(seam_L, axis=1)[order], 3)
    phi_seam = np.interp(COLS, ths, phis)
    r_seam = np.interp(COLS, ths, rs)
    phi_last = phi_seam - np.degrees(SEAM_GAP / r_seam)
    # Rays: row r (1..NROW) at phi = phi_last * (r / NROW) ** g, slightly
    # denser toward the crown-to-brow distance than uniform.
    rows = np.arange(1, NROW + 1)
    s = rows / NROW
    phi = phi_last[None, :] * s[:, None]
    dirs = H.ray_dirs(phi, np.broadcast_to(COLS, phi.shape)).reshape(-1, 3)
    # MakeHuman neck surface along the lowest rows' rays.
    row_of_ray = np.repeat(rows, NCOL)
    blend = _smooth((row_of_ray - (NROW - BLEND_ROWS)) / BLEND_ROWS)
    mh_hits = np.zeros(len(dirs))
    for i in np.nonzero(blend > 0)[0]:
        d_w = np.array([dirs[i, 0], -dirs[i, 1], dirs[i, 2]])
        # The last hit along the ray: the outer skin (the ray may pass
        # through the inside of MakeHuman's mouth first).
        o, last, total = Vector(hc), None, 0.0
        for _ in range(8):
            loc, nrm, idx, dist = bvh.ray_cast(o, Vector(d_w), 0.3)
            if loc is None:
                break
            total += float(dist)
            last = total
            o = loc + Vector(d_w) * 1e-5
            total += 1e-5
        if last is None:
            blend[i] = 0.0
            continue
        mh_hits[i] = last / sc
    ah = AnimeHead(b, hp, keep, remap, seam, kept_faces, [], len(kept_faces), n_keep, np.zeros(0))
    ah.rays = dirs
    ah.blend = blend
    ah.mh_hits = mh_hits
    ah.seam_theta = seam_th
    ah.base_mh_seam = V[seam].copy()
    n_grid = 1 + NROW * NCOL
    ear_v, ear_f = _ear_shells(b)
    ah.head_local = np.zeros((n_grid + len(ear_v), 3))
    ah.head_local[n_grid:] = ear_v
    ah.head_local = ah.head_points(hp)
    ah.col_of = np.full(len(ah.head_local), -1)
    ah.row_of = np.full(len(ah.head_local), -1)
    ah.col_of[1:n_grid] = np.tile(np.arange(NCOL), NROW)
    ah.row_of[1:n_grid] = row_of_ray
    ah.row_of[0] = 0
    # Faces: kept MakeHuman faces (combined ids), our grid, the weld strip, the ears.
    faces = [tuple(int(remap[v]) for v in skin_faces[fi]) for fi in kept_faces]
    off = n_keep
    ours = _grid_faces(off)
    last = [off + 1 + (NROW - 1) * NCOL + c for c in range(NCOL)]
    so = np.argsort(seam_th)
    strip = _zip(last, COLS, [int(remap[seam[i]]) for i in so], seam_th[so])
    P = np.vstack([V[keep], to_world(b, ah.head_local)])
    hcw = hc + np.array([0.0, 0.0, -0.02])
    ours = _orient_out(P, ours, hcw)
    strip = _orient_out(P, strip, hcw)
    ears = [tuple(off + n_grid + i for i in f) for f in ear_f]
    ah.faces = faces + ours + strip + ears
    ah.face_flags = [{}] * (len(ours) + len(strip)) + [{"ears": True}] * len(ears)
    ah.n_grid_faces = len(ours)
    ah.n_strip_faces = len(strip)
    ah.base = P
    ah.ear_ids = {1: list(range(off + n_grid, off + n_grid + len(ear_v) // 2)),
                  -1: list(range(off + n_grid + len(ear_v) // 2, off + n_grid + len(ear_v)))}
    ah.weights_head = transfer_weights(P[n_keep:], V, skin_faces, comp, mh_weights, ear_from=n_grid)
    return ah


def transfer_weights(Q: np.ndarray, V: np.ndarray, faces, comp: np.ndarray, mh_w: list[dict],
                     ear_from: int | None = None) -> list[dict]:
    """MakeHuman's weights at the nearest point of its removed head and neck
    (barycentric), so our head and neck bend exactly like MakeHuman's did."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    tris = [(f[0], f[i], f[i + 1]) for fi in np.nonzero(comp)[0] for f in [faces[fi]] for i in range(1, len(f) - 1)]
    bvh = BVHTree.FromPolygons([tuple(p) for p in V], tris, all_triangles=True)
    out = []
    for k, q in enumerate(Q):
        if ear_from is not None and k >= ear_from:
            out.append({"DEF-spine.006": 1.0})
            continue
        loc, nrm, idx, dist = bvh.find_nearest(Vector(q))
        a, b_, c = tris[idx]
        pa, pb, pc = V[a], V[b_], V[c]
        v0, v1, v2 = pb - pa, pc - pa, np.array(loc) - pa
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = d00 * d11 - d01 * d01
        if abs(den) < 1e-18:
            bc = np.array([1.0, 0.0, 0.0])
        else:
            bv = (d11 * d20 - d01 * d21) / den
            bw = (d00 * d21 - d01 * d20) / den
            bc = np.clip(np.array([1 - bv - bw, bv, bw]), 0, 1)
            bc /= bc.sum()
        w = WT.mix((bc[0], mh_w[a]), (bc[1], mh_w[b_]), (bc[2], mh_w[c]))
        out.append(WT.finalise(w))
    return out


# ----------------------------------------------------------------------------
# Face frame (the mh_face paint machinery on our head)
# ----------------------------------------------------------------------------


def make_frame(b, ah: AnimeHead):
    """mh_face.FaceFrame of our head: the eyes, nose and mouth anchors and an
    ellipsoid fitted to the face front (normals)."""
    from . import mh_face as MF

    c = K.head_centre(b)
    sc = 0.01 * b.head_scale
    ex, eu = EYE_POS[b.id]
    sdf = head_sdf(ah.hp)
    # Face front straight ahead of the eye.
    o = np.array([[ex, 0.0, eu]])
    t = S.raycast(sdf, o, np.array([[0.0, 1.0, 0.0]]), t_max=30.0)
    eye_l = to_world(b, o + np.array([[0.0, t[0], 0.0]]))[0]
    axis_y = float(c[1])
    R0 = math.hypot(eye_l[0], axis_y - eye_l[1])
    u_e = R0 * math.atan2(eye_l[0], axis_y - eye_l[1])
    st = MF.STYLE[b.id]
    eye = np.array([u_e + st["eye_du"], float(eye_l[2]) + st["eye_dv"]])
    mouth = np.array([0.0, c[2] + MOUTH_U[b.id] * sc])
    nose = np.array([0.0, c[2] + NOSE_U[b.id] * sc])
    chin_z = float(c[2] + ah.hp.chin_u * sc)
    fr = MF.FaceFrame(b, axis_y, R0, eye, mouth, nose, np.zeros(3), np.ones(3), chin_z, float(eye_l[2] + 0.05 * b.head_scale))
    # Ellipsoid fit over the face front (our grid vertices).
    n_grid = 1 + NROW * NCOL
    L = ah.head_local[1:n_grid]
    phi, th = polar(L)
    side = np.where(th <= 180, th, 360 - th)
    sel = (side < 58) & (L[:, 2] > -9.5) & (L[:, 2] < 4.5) & (L[:, 1] > 0)
    Q = to_world(b, L[sel])
    best = None
    for yc in np.linspace(axis_y - 0.02, axis_y + 0.07, 19):
        for zc in np.linspace(eye_l[2] - 0.06, eye_l[2] + 0.03, 19):
            M = np.stack([Q[:, 0] ** 2, (Q[:, 1] - yc) ** 2, (Q[:, 2] - zc) ** 2], axis=1)
            sol, *_ = np.linalg.lstsq(M, np.ones(len(Q)), rcond=None)
            if (sol <= 0).any():
                continue
            r = 1.0 / np.sqrt(sol)
            k = np.sqrt((Q[:, 0] / r[0]) ** 2 + ((Q[:, 1] - yc) / r[1]) ** 2 + ((Q[:, 2] - zc) / r[2]) ** 2)
            err = float(np.mean((k - 1.0) ** 2))
            if best is None or err < best[0]:
                best = (err, np.array([0.0, yc, zc]), r)
    fr.ell_c, fr.ell_r = best[1], best[2]
    fr.world_polys()
    print(f"[mh_head] {b.id}: ellipsoid c={np.round(fr.ell_c, 4)} r={np.round(fr.ell_r, 4)} rms={best[0] ** 0.5:.4f} "
          f"R0={R0:.4f} eye={np.round(eye, 4)}", flush=True)
    return fr


# ----------------------------------------------------------------------------
# Keys
# ----------------------------------------------------------------------------


def face_key_positions(ah: AnimeHead, mh_base: np.ndarray) -> dict[str, np.ndarray]:
    """Combined positions for the parametric face sliders."""
    out = {}
    for name, deltas in FACE_KEYS.items():
        hl = ah.head_points(apply_key(ah.hp, deltas))
        n_grid = 1 + NROW * NCOL
        hl[n_grid:] = ah.head_local[n_grid:]  # ears stay
        out[name] = ah.combine(mh_base, hl)
    return out


def _move_vertical(P, N, dz):
    from . import mh_face as MF

    return MF._move_vertical(P, N, dz)


def expression_keys(fr, P: np.ndarray, movable: np.ndarray) -> dict[str, np.ndarray]:
    """smile, frown, brows_up, brows_down and the brow sliders, made after
    the paint cuts in the face frame (the painted mouth line bends, the
    painted brows move)."""
    from . import mh_face as MF

    st = fr.st
    k = fr.k
    g = (P - fr.ell_c) / fr.ell_r ** 2
    N = g / np.maximum(np.linalg.norm(g, axis=1, keepdims=True), 1e-12)
    q = fr.uv(P)
    u, v = q[:, 0], q[:, 1]
    front = fr.front(P) * movable
    out = {}
    mw = st["mouth_w"]
    au = np.abs(u)
    # Mouth: corners up (smile) or down (frown), the centre barely moves;
    # the influence fades within a few millimetres above and below.
    km = st.get("mouth_k", 1.0)
    near_v = 1.0 - _smooth((np.abs(v - fr.mouth[1]) - 0.002 * km) / (0.012 * km))
    corner = _smooth(au / (0.5 * mw)) * (1.0 - _smooth((au - 0.5 * mw) / (0.012 * km)))
    smile = 0.0026 * km * corner ** 1.4 * near_v
    # Cheeks lift a little and the lower lids rise (a gentle smile in the eyes).
    for side in (1, -1):
        loc = fr.eye_local(P, side)
        s, t = loc[:, 0], loc[:, 1]
        below = (t < 0.0) & (t > -0.016 * k)
        lift = 0.0009 * k * (1.0 - _smooth((np.abs(s) - 0.012 * k) / (0.01 * k))) * (1.0 - _smooth(-t / (0.016 * k))) * below
        smile = smile + lift
    out["smile"] = _move_vertical(P, N, smile * front)
    frown = (-0.0017 * corner ** 1.2 * near_v + 0.0004 * (1.0 - _smooth(au / (0.3 * mw))) * near_v) * km
    out["frown"] = _move_vertical(P, N, frown * front)
    # Brows: everything around the painted brows moves with them.
    brow_v = fr.eye[1] + st.get("brow_v", 0.012)

    def brow_zone(lift_inner: float, lift_outer: float) -> np.ndarray:
        dz = np.zeros(len(P))
        for side in (1, -1):
            loc = fr.eye_local(P, side)
            s, t = loc[:, 0], loc[:, 1]
            w = (1.0 - _smooth((np.abs(t - (brow_v - fr.eye[1])) - 0.004 * k) / (0.01 * k))) * \
                (1.0 - _smooth((np.abs(s) - 0.026 * k) / (0.012 * k)))
            # Keep the eye itself still.
            w = w * _smooth((t - st.get("brow_still", 0.008)) / (0.004 * k))
            qn = np.clip((s + 0.02 * k) / (0.04 * k), 0.0, 1.0)  # 0 inner .. 1 outer
            dz = dz + w * k * (lift_inner + (lift_outer - lift_inner) * qn)
        return dz

    out["brows_up"] = _move_vertical(P, N, brow_zone(0.0034, 0.0026) * front)
    out["brows_down"] = _move_vertical(P, N, brow_zone(-0.0026, -0.0008) * front)
    out["face_brow_low"] = _move_vertical(P, N, brow_zone(-0.0016, -0.0016) * front)
    out["face_brow_high"] = _move_vertical(P, N, brow_zone(0.0019, 0.0019) * front)
    return out


# ----------------------------------------------------------------------------
# Normals
# ----------------------------------------------------------------------------


def normal_weight(fr, P: np.ndarray) -> np.ndarray:
    """1 over the face front (brow to chin, inside the cheeks), 0 toward the
    jawline, the ears, the hairline and under the chin."""
    b = fr.b
    s = b.head_scale
    ang = np.abs(fr.angle(P))
    z = P[:, 2]
    w = 1.0 - _smooth((ang - 52.0) / 26.0)
    w *= 1.0 - _smooth((fr.chin_z + 0.020 * s - z) / (0.022 * s))
    w *= 1.0 - _smooth((z - (fr.eye[1] + 0.05 * s)) / (0.03 * s))
    return np.clip(w, 0.0, 1.0) * (P[:, 1] < fr.axis_y)


def face_normals(obj, fr, strength: float = 1.0) -> None:
    """Blend the head object's normals toward the face ellipsoid's (custom
    split normals, exported by glTF)."""
    me = obj.data
    nl = len(me.loops)
    if not nl:
        return
    co = np.zeros(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(obj.matrix_world)
    co_w = co @ mw[:3, :3].T + mw[:3, 3]
    cur = np.zeros(nl * 3, dtype=np.float32)
    me.corner_normals.foreach_get("vector", cur)
    cur = cur.reshape(-1, 3).astype(np.float64)
    vi = np.zeros(nl, dtype=np.int64)
    me.loops.foreach_get("vertex_index", vi)
    en = fr.ell_normal(co_w)
    w = normal_weight(fr, co_w) * strength
    n = cur * (1 - w[vi, None]) + en[vi] * w[vi, None]
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    me.normals_split_custom_set(n.tolist())
