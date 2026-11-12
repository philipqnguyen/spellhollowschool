"""Baked body shapes: our MakeHuman/anime body wrapped onto a third-party sculpt.

    build_characters.py -- --body-source makehuman --body-shape sculpt ...

The wrap itself (`sculpt_wrap.py`, run by `wrap_sculpts.py`) needs the
sculpt files; it bakes a *shape target* per body, committed under
(`art/characters/shape_targets/<body>.npz`): the rest-pose position of every
vertex of the visible mesh as it is just before the painted face is cut in
(MakeHuman body, anime head, body paint; no face features, not yet
decimated), the body's new measurements and the sculpt's face anchors (eye
socket centre and extent, nose, mouth, chin, crown). A normal build makes
the same pre-cut mesh as always, and `Reshaper.precut` moves its vertices
onto the baked positions; the face is then painted onto the wrapped head
from the sculpt's anchors (`mh_face.sculpt_frame`: eyes sized to the
sockets) and the mesh decimated to LOD0 as usual. Topology before the
face cut, triangle budget, bone weights, UVs, COLOR_0 dye mask, regions and
every shape key name stay ours. The shape keys are re-expressed on the new
shape (`transfer_keys`: each key's offsets turned with the local surface
frame and scaled with the local size), the eye, mouth and brow keys are made
on the new face, the skeleton comes from the new measurements
(`proportions.Body`, same bone names and hierarchy), and the fitting cage
gear and hair are cut against is our layout for the new measurements, bound
onto the new surface; hats and hair use the visible head and face
(`gear_ctx.register_head_surface`, `register_face`).

Contributors without the sculpts build from the baked files; the sculpts are
only read when a target is re-baked.
"""

from __future__ import annotations

import hashlib
import json
import os

import numpy as np

from . import proportions as P

FORMAT = 1
DEFAULT_DIR = os.path.join("art", "characters", "shape_targets")


# ----------------------------------------------------------------------------
# Topology
# ----------------------------------------------------------------------------


def topology_hash(faces) -> str:
    h = hashlib.sha256()
    for f in faces:
        h.update(np.asarray(f, dtype=np.int32).tobytes())
        h.update(b"|")
    return h.hexdigest()


def triangles(faces) -> np.ndarray:
    out = []
    for f in faces:
        for i in range(1, len(f) - 1):
            out.append((f[0], f[i], f[i + 1]))
    return np.array(out, dtype=np.int64)


# ----------------------------------------------------------------------------
# Local surface frames and the key transfer
# ----------------------------------------------------------------------------


def vertex_normals(V: np.ndarray, tris: np.ndarray) -> np.ndarray:
    fn = np.cross(V[tris[:, 1]] - V[tris[:, 0]], V[tris[:, 2]] - V[tris[:, 0]])
    n = np.zeros_like(V)
    for k in range(3):
        np.add.at(n, tris[:, k], fn)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def neighbours(n: int, tris: np.ndarray) -> list[np.ndarray]:
    nb: list[set] = [set() for _ in range(n)]
    for a, b, c in tris:
        nb[a].update((b, c))
        nb[b].update((a, c))
        nb[c].update((a, b))
    return [np.array(sorted(s), dtype=np.int64) for s in nb]


def neighbour_table(nb: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Padded neighbour index table and row-normalised mask."""
    deg = max(len(x) for x in nb)
    tab = np.zeros((len(nb), deg), dtype=np.int64)
    msk = np.zeros((len(nb), deg))
    for i, x in enumerate(nb):
        tab[i, : len(x)] = x
        tab[i, len(x):] = i
        msk[i, : len(x)] = 1.0
    msk /= np.maximum(msk.sum(axis=1, keepdims=True), 1.0)
    return tab, msk


def local_frames(V: np.ndarray, tris: np.ndarray, tab: np.ndarray, msk: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per vertex: orthonormal frame (columns tangent, bitangent, normal) and
    local size (mean distance to the neighbours). The tangent is the
    direction to the weighted neighbour centroid's first-ring spread (the
    first principal axis of the one-ring), so it is stable under smooth
    deformations of the same topology."""
    N = vertex_normals(V, tris)
    D = V[tab] - V[:, None, :]  # (n, k, 3)
    w = (msk > 0).astype(float)
    # First neighbour direction (consistent across shapes: same topology).
    t = D[:, 0, :] - N * np.einsum("nd,nd->n", D[:, 0, :], N)[:, None]
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    b = np.cross(N, t)
    F = np.stack([t, b, N], axis=2)  # (n, 3, 3) columns
    size = (np.linalg.norm(D, axis=2) * w).sum(axis=1) / np.maximum(w.sum(axis=1), 1.0)
    return F, size


def smooth_scalar(x: np.ndarray, tab: np.ndarray, msk: np.ndarray, iters: int) -> np.ndarray:
    for _ in range(iters):
        x = 0.5 * x + 0.5 * np.einsum("nk,nk->n", msk, x[tab])
    return x


def smooth_frames(F: np.ndarray, tab: np.ndarray, msk: np.ndarray, iters: int) -> np.ndarray:
    """Average rotation fields over the one-ring (then re-orthonormalise)."""
    for _ in range(iters):
        A = 0.5 * F + 0.5 * np.einsum("nk,nkij->nij", msk, F[tab])
        U, _, Vt = np.linalg.svd(A)
        F = U @ Vt
    return F


def transfer_keys(V0: np.ndarray, V1: np.ndarray, faces, keys: dict[str, np.ndarray],
                  smooth: int = 3) -> dict[str, np.ndarray]:
    """Shape keys of a mesh at rest shape V0, re-expressed on rest shape V1
    (same topology): each key offset is written in V0's local frame
    (tangent, bitangent, normal), rotated into V1's frame and scaled with the
    local size ratio (both fields smoothed over a few rings so single
    stretched edges do not show)."""
    tris = triangles(faces)
    nb = neighbours(len(V0), tris)
    tab, msk = neighbour_table(nb)
    F0, s0 = local_frames(V0, tris, tab, msk)
    F1, s1 = local_frames(V1, tris, tab, msk)
    R = np.einsum("nij,nkj->nik", F1, F0)  # F1 F0^T
    R = smooth_frames(R, tab, msk, smooth)
    ratio = smooth_scalar(s1 / np.maximum(s0, 1e-9), tab, msk, smooth * 2)
    out = {}
    for name, K in keys.items():
        d = K - V0
        moved = np.abs(d).max(axis=1) > 1e-9
        nd = np.einsum("nij,nj->ni", R, d) * ratio[:, None]
        nd[~moved] = 0.0
        out[name] = V1 + nd
    return out


# ----------------------------------------------------------------------------
# Baked target files
# ----------------------------------------------------------------------------


def target_path(body_id: str, shape_dir: str, variant: str = "") -> str:
    return os.path.join(shape_dir, f"{body_id}{('_' + variant) if variant else ''}.npz")


def save_target(path: str, *, verts0: np.ndarray, verts: np.ndarray, faces, body: P.Body, meta: dict,
                extra: dict | None = None) -> None:
    """`verts0`: the pre-wrap LOD0 rest positions (to check the build still
    makes the same mesh); `verts`: the wrapped rest positions."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    arrays = {
        "format": np.array(FORMAT),
        "verts": np.asarray(verts, dtype=np.float32),
        "verts_prewrap": np.asarray(verts0, dtype=np.float32),
        "topology": np.array(topology_hash(faces)),
        "n_faces": np.array(len(faces)),
        "body": np.array(P.to_json(body)),
        "meta": np.array(json.dumps(meta, sort_keys=True)),
    }
    for k, v in (extra or {}).items():
        arrays[k] = np.asarray(v)
    np.savez_compressed(path, **arrays)


def load_target(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"no baked body shape at {path} (bake it with tools/blender/characters/wrap_sculpts.py)")
    z = np.load(path, allow_pickle=False)
    out = {k: z[k] for k in z.files}
    out["body"] = P.from_json(str(out["body"]))
    out["meta"] = json.loads(str(out["meta"]))
    out["topology"] = str(out["topology"])
    return out


# ----------------------------------------------------------------------------
# Build-time hooks (pipeline.build_body(shape=...))
# ----------------------------------------------------------------------------


class Captured(Exception):
    """Raised by `Capture` once it has the pre-cut mesh (the rest of the build is not needed)."""


def _cm_arrays(cm, regions) -> dict:
    from . import weights as WT

    return {"verts": np.array(cm.base, dtype=np.float64), "faces": [tuple(f) for f in cm.faces],
            "keys": {k: np.array(v) for k, v in cm.shapes.items() if k != "base"},
            "weights": [WT.finalise(dict(w)) for w in cm.weights], "regions": list(regions)}


class Capture:
    """Records the pre-cut visible mesh (`mh_body.build`, just before the
    painted face is cut in) and stops the build (the wrap tool builds the
    body with it)."""

    def __init__(self):
        self.data: dict | None = None
        self.body = None
        self.frame = None

    def precut(self, body, cm, regions, frame):
        self.data = _cm_arrays(cm, regions)
        self.body, self.frame = body, frame
        raise Captured()


class Reshaper:
    """Moves the pre-cut visible mesh onto a baked shape target; the face is
    then painted on the new head (`mh_face.sculpt_frame`)."""

    def __init__(self, path: str, check_tol: float = 0.004):
        self.path = path
        self.target = load_target(path)
        self.check_tol = check_tol
        self.report: dict = {}
        self.body = None

    def precut(self, body0, cm, regions, frame0):
        """`cm` (mh_paint.CutMesh, pre-wrap shape) is reshaped in place;
        returns the new Body and the painted-face frame on the new head."""
        from . import mh_face as MF

        t = self.target
        faces = [tuple(f) for f in cm.faces]
        V0 = np.array(cm.base, dtype=np.float64)
        if topology_hash(faces) != t["topology"] or len(V0) != len(t["verts"]):
            raise RuntimeError(
                f"{self.path}: the pre-wrap mesh changed ({len(V0)} verts, {len(faces)} faces; "
                f"the target has {len(t['verts'])} verts, {int(t['n_faces'])} faces): re-bake the body shape with "
                "tools/blender/characters/wrap_sculpts.py")
        dev = float(np.abs(V0 - t["verts_prewrap"]).max())
        if dev > self.check_tol:
            raise RuntimeError(f"{self.path}: the pre-wrap mesh moved by up to {dev * 1000:.1f} mm since the bake: re-bake it")
        V1 = t["verts"].astype(np.float64)
        keys = transfer_keys(V0, V1, faces, {k: v for k, v in cm.shapes.items() if k != "base"})
        cm.shapes = {k: (V1.copy() if k == "base" else keys[k]) for k in cm.shapes}
        body = t["body"]
        P.register(body)
        body = P.get(body.id)  # the registered body (the spec height range)
        self.body = body
        frame = MF.sculpt_frame(body, V1, faces, regions, face_layout(t))
        self.report = {"target": os.path.basename(self.path), "prewrap_max_dev_mm": round(dev * 1000, 2),
                       **{k: v for k, v in t["meta"].items() if k in ("source", "author", "heads_tall")}}
        return body, frame

    def finish(self, mh):
        """After mh_body.build: the new body, its layout and the fitting cage
        bound onto the reshaped (and painted) visible mesh; hats and hair are
        cut against its visible head."""
        from . import body as BD
        from . import gear_ctx as GC

        body = self.body
        md = mh.visible
        body = neck_line(body, md)
        if body is not self.body:
            P.register(body)
            body = self.body = P.get(body.id)
            self.report["neck_fwd"] = [round(float(f), 4) for f in body.extra["neck_fwd"]]
        head = [f for f, r in zip(md.faces, md.region) if r in ("head", "scalp", "neck")]  # not the ears
        GC.register_head_surface(body, np.asarray(md.verts, dtype=np.float64), triangles(head))
        if mh.frame is not None:
            GC.register_face(body, mh.frame)
        lay = BD.BodyLayout(body)
        return body, lay, bind_cage(lay, md)


# How far (m) the standard neck line (skeleton.NECK_FWD) may sit from the
# neck's measured centre before the bones move onto the measured line.
NECK_TOL = 0.012


def neck_line(body, md):
    """The body with its neck and head bones (spine.004-.006 heads) on the
    centre of the reshaped neck when the standard line misses it by more
    than NECK_TOL: the middle of the neck skin's front and back in a thin
    slab at each bone's height. The wrapped female's neck sits 5-6 cm behind
    the standard line, which put the bones in front of her throat (a head
    turn swung the neck sideways and the neck charts cast from outside it).
    Only pivots move: the mesh, its weights and the face stay."""
    from . import skeleton as K

    V = np.asarray(md.verts, dtype=np.float64)
    ids = np.unique([v for f, r in zip(md.faces, md.region) if r == "neck" for v in f])
    if not len(ids):
        return body
    nv = V[ids]
    sp = K.spine_points(body)
    fwd = []
    for k, f0 in zip((4, 5, 6), K.NECK_FWD):
        z = float(sp[k][2])
        q = nv[np.abs(nv[:, 2] - z) < 0.006]
        if len(q) < 4:
            return body
        fwd.append(-0.5 * float(q[:, 1].min() + q[:, 1].max()))
    if max(abs(f - f0) for f, f0 in zip(fwd, K.NECK_FWD)) <= NECK_TOL:
        return body
    return P.with_extra(body, neck_fwd=fwd)


FACE_KEYS = ("face_eye", "face_box", "face_nose", "face_mouth", "face_chin", "face_crown")


def face_layout(t: dict) -> dict:
    """The sculpt's face anchors baked with the target (final rest pose):
    left eye socket centre, its inner/outer/lower/upper extremes, nose tip,
    mouth, chin and crown."""
    missing = [k for k in FACE_KEYS if k not in t]
    if missing:
        raise RuntimeError(f"shape target without face anchors ({missing}): re-bake it with wrap_sculpts.py")
    return {k[5:]: np.asarray(t[k], dtype=np.float64) for k in FACE_KEYS}


def tri_bones(md, faces) -> list[str]:
    """Fit-bone name (mh_body PART_BONES vocabulary) of each triangle's
    dominant skin weight."""
    out = []
    for f in faces:
        acc: dict[str, float] = {}
        for v in f:
            for g, w in md.weights[v].items():
                acc[g] = acc.get(g, 0.0) + w
        bone = max(acc, key=acc.get) if acc else "DEF-spine.003"
        out.extend([fit_bone_name(bone)] * (len(f) - 2))
    return out


def fit_bone_name(deform: str) -> str:
    """DEF bone -> the fit-bone vocabulary of mh_body (torso, neck, head,
    shoulder.L, upper_arm.L, ..., f_index.01.L, thigh.L, toe.L)."""
    b = deform[4:] if deform.startswith("DEF-") else deform
    if b in ("jaw",) or b.startswith("eye"):
        return "head"
    if b.startswith("spine"):
        return {"spine.004": "neck", "spine.005": "neck", "spine.006": "head"}.get(b, "torso")
    if b.startswith(("pelvis", "breast")):
        return "torso"
    if b.startswith("heel"):
        return "foot" + b[-2:]
    if b.endswith((".L.001", ".R.001")):
        return b[:-4]
    if b.startswith("palm"):
        return "hand" + b[-2:]
    return b


def bind_cage(lay, md):
    """Our layout (for the new measurements) bound onto the reshaped LOD0
    surface, like mh_body binds it onto the MakeHuman skin."""
    from . import body_mesh as BM
    from . import mh_body as MB
    from .proportions import BUILDS

    an = {"base": lay.positions()}
    builds = [k for k in md.keys if k in BUILDS]
    an.update(BM.shape_key_positions(lay, builds))
    tris = triangles(md.faces)
    binding = MB.bind(lay, an["base"], md.verts, tris, tri_bones(md, md.faces))
    cmask = MB.cage_mask(lay, an["base"])

    def cage_pos(vis: np.ndarray, analytic: np.ndarray) -> np.ndarray:
        p = analytic + cmask[:, None] * (binding.eval(vis) - analytic)
        MB._move_ears(lay, p, analytic)
        return p

    cage = BM.body_meshdata(lay, with_keys=False)
    keep = np.nonzero(cage.old_to_new >= 0)[0]
    cage.verts = cage_pos(md.verts, an["base"])[keep]
    cage.keys = {k: cage_pos(md.keys[k], an[k])[keep] for k in builds}
    return cage


def affine_fit(A0: np.ndarray, A1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares affine map A0 -> A1 (3x3 matrix, translation)."""
    c0, c1 = A0.mean(axis=0), A1.mean(axis=0)
    X, Y = A0 - c0, A1 - c1
    M, *_ = np.linalg.lstsq(X, Y, rcond=None)
    return M.T, c1 - M.T @ c0


# ----------------------------------------------------------------------------
# Quality checks (wrap tool log and tests)
# ----------------------------------------------------------------------------


CONTACT_REGIONS = ("hands", "feet", "ears")  # fingers, toes and the ear shells touch by design


def flipped_triangles(V0: np.ndarray, V1: np.ndarray, faces, max_angle: float = 90.0,
                      min_area: float = 0.0, min_angle: float = 0.0, local: bool = False) -> np.ndarray:
    """Triangles whose normal turned by more than `max_angle` degrees from V0
    to V1 (both rest poses), or that collapsed. `min_area` (m^2) and
    `min_angle` (degrees, smallest corner in V0) skip slivers, whose normals
    mean nothing. `local`: only those that are also folded over their
    edge neighbours in V1 (a crease of V0 that the new shape smooths out,
    such as our crotch, turns its triangles without folding them)."""
    T = triangles(faces)

    def tri_n(V):
        n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
        a = np.linalg.norm(n, axis=1)
        return n / np.maximum(a, 1e-18)[:, None], a

    n0, a0 = tri_n(V0)
    n1, a1 = tri_n(V1)
    cos = np.einsum("nd,nd->n", n0, n1)
    bad = (cos < np.cos(np.radians(max_angle))) | (a1 < 1e-12)
    if min_area > 0 or min_angle > 0:
        P = V0[T]
        ang = []
        for k in range(3):
            u = P[:, (k + 1) % 3] - P[:, k]
            v = P[:, (k + 2) % 3] - P[:, k]
            c = np.einsum("nd,nd->n", u, v) / np.maximum(np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1), 1e-18)
            ang.append(np.degrees(np.arccos(np.clip(c, -1, 1))))
        bad &= (0.5 * a0 >= min_area) & (np.min(ang, axis=0) >= min_angle)
    if local and bad.any():
        e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
        e.sort(axis=1)
        tid = np.tile(np.arange(len(T)), 3)
        order = np.lexsort((e[:, 1], e[:, 0]))
        e, tid = e[order], tid[order]
        same = (e[1:] == e[:-1]).all(axis=1)
        acc = np.zeros_like(n1)
        w1 = n1 * a1[:, None]
        np.add.at(acc, tid[1:][same], w1[tid[:-1][same]])
        np.add.at(acc, tid[:-1][same], w1[tid[1:][same]])
        bad &= np.einsum("nd,nd->n", n1, acc) < 0
    return np.nonzero(bad)[0]


def wrap_quality(V0: np.ndarray, V1: np.ndarray, faces, regions) -> dict:
    """Folded triangles (turned from V0 and folded over their neighbours;
    non-slivers: 1 mm^2, 5 degree corners), turned ones (any crease
    smoothed out), and
    self-intersecting triangle pairs outside the contact regions, before
    (V0) and after (V1)."""
    tri_region = [r for f, r in zip(faces, regions) for _ in range(len(f) - 2)]

    def crossings(V):
        return sum(1 for i, j in self_intersections(V, faces)
                   if tri_region[i] not in CONTACT_REGIONS and tri_region[j] not in CONTACT_REGIONS)

    return {"flipped": int(len(flipped_triangles(V0, V1, faces, min_area=1e-6, min_angle=5.0, local=True))),
            "turned": int(len(flipped_triangles(V0, V1, faces, min_area=1e-6, min_angle=5.0))),
            "flipped_any": int(len(flipped_triangles(V0, V1, faces, local=True))),
            "self_intersections": crossings(V1), "self_intersections_prewrap": crossings(V0)}


def self_intersections(V: np.ndarray, faces) -> list[tuple[int, int]]:
    """Pairs of triangles that cross each other (pairs sharing a vertex are
    neighbours and do not count)."""
    from mathutils.bvhtree import BVHTree

    T = triangles(faces)
    bvh = BVHTree.FromPolygons(V.tolist(), T.tolist(), all_triangles=True)
    out = []
    for i, j in bvh.overlap(bvh):
        if i >= j:
            continue
        if set(T[i]) & set(T[j]):
            continue
        out.append((int(i), int(j)))
    return out
