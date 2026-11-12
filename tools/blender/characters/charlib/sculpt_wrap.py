"""Wraps our body mesh onto a third-party sculpt (shape only).

Run by `wrap_sculpts.py` on our visible mesh as it is just before the
painted face is cut in; the result is baked by `shape_target.py`. Only
vertex positions change: our topology, weights, UVs, body paint, regions and
key names stay (the face is painted on the wrapped head at build time, from
the sculpt's face anchors baked here). Steps (`wrap`):

1. **Target** (`prepare_target`, `neutralise`): the sculpt is imported
   (glTF), turned to face -Y, scaled uniformly to the body's spec height,
   mirrored left onto right (exact symmetry), voxel-remeshed into one closed
   surface and smoothed (Taubin passes; the faceted DJP3D sculpt gets more,
   its face most). The groin and the chest apex are smoothed again locally
   into neutral, featureless forms.
2. **Pose** (`chain_xf`, `dqs`): our body is posed into the sculpt's stance
   by turning its arms at the shoulder and elbow and its legs at the hip and
   knee (dual-quaternion skinning with our own weights) by the rotations
   between the limb segments traced on both meshes.
3. **Landmarks** (`landmarks`): one detector runs on both meshes. Limbs are
   traced with cross-section slices (wrist and ankle at girth minima, elbow
   and knee at the girth minimum nearest our own proportion, the arm root
   where the arm loop merges into the torso, fingertip, heel, toe tip),
   the torso with horizontal slices (armpit where the torso loop stops
   reaching the arm, chest apex, waist, hips, crotch, neck, neck base,
   shoulder top), the head with ray casts (crown, chin, forehead, back,
   temples, ears, jaw) and, on the sculpt only, the eye socket
   (`socket_box`: a depth map of the face front fitted with a smooth
   quartic surface that ignores recesses and the nose; the socket is the
   connected recess round the deepest point of the eye band, measured on
   the sculpt before its sockets are filled) and nose and mouth on the
   midline profile; our eye, nose and mouth anchors come from the face
   frame. Left-side landmarks are mirrored (the target is symmetric).
4. **Warp** (`TPS`): a 3D thin-plate spline from our landmarks to the
   sculpt's moves every vertex coarsely into place.
5. **Project** (`project`): vertices move onto the target surface of their
   own body part in smoothed displacement steps (painted shapes travel
   almost rigidly), then a tangential relax restores the warped layout's
   local shape (weak on the face). Hands, feet and ears are not projected:
   they keep our shapes and follow a similarity transform (size and
   placement from the warp).
6. **Skeleton** (`measure_body`): the shoulder and hip joints follow the
   warp; elbow, wrist, knee and ankle keep their place in the limb's cross
   section (centroid plus our own offset, scaled with the section), the
   body is posed back into our rest pose (arms 45 degrees down, our leg
   stance) with the new bone lengths, set on the floor and scaled uniformly
   to the spec height, and the new `proportions.Body` is measured off the
   mesh; `skeleton.joints` builds the same bones from it. The sculpt keeps
   its own cranium: hats and hair are cut against the visible head
   (`gear_ctx.register_head_surface`); our head sculpt, fitted to the new
   scalp, only sets their scale and the head centre. A local Laplacian pass
   unfolds any triangle the wrap folded over its neighbours (`unfold`).
7. **Face anchors**: the socket centre and its inner, outer, lower and upper
   extremes, the nose tip, mouth, chin and crown, carried into the final rest
   pose (`WrapResult.face`, baked as `face_*` arrays).
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field, replace

import numpy as np

from . import head as H
from . import sdf as S
from . import skeleton as K
from .proportions import Body

# ----------------------------------------------------------------------------
# The sculpts (CC BY 4.0; read from --sculpt-dir, never copied)
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Sculpt:
    file: str
    title: str
    author: str
    url: str
    facing: str  # "+Y" or "-Y" after glTF import
    smooth: int = 4  # Taubin passes over the whole target
    face_smooth: int = 0  # extra passes over the head
    voxel: float = 0.004  # remesh voxel size (metres at 1.7 m)
    licence: str = "CC BY 4.0"


SCULPTS = {
    ("body_a", ""): Sculpt("anime_boy_base_free/anime_boy_male_model_t-pose_base_basic_free.glb",
                           "Anime boy male model T-pose base basic FREE", "ALexzart", "https://skfb.ly/oxIPp", "+Y",
                           smooth=3),
    ("body_b", ""): Sculpt("anime_basemesh_sculpt_01_female/anime_basemesh_sculpt_01.glb",
                           "Anime BaseMesh Sculpt 01", "DJP3D", "https://skfb.ly/oPv6y", "-Y",
                           smooth=10, face_smooth=30),
}

# Groin and chest apex: local smoothing into neutral forms (radius in metres
# at 1.7 m, plain Laplacian passes).
GROIN = (0.065, 160)
NIPPLE = (0.030, 40)
EYE_FILL = (0.12, 25)
EAR_FILL = (0.16, 80)  # the sculpt's ears, flattened under our ear shells  # radius (share of the head height) and plain passes over each eye socket
CLEFT_PASSES = 40
ARM_STEP = 0.01  # metres between arm slices
PROJ_ITERS = 12
RELAX_ITERS = 16
TPS_REG = 2e-4

# ----------------------------------------------------------------------------
# Mesh helpers
# ----------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(f"[sculpt_wrap] {msg}", flush=True)


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


def edges_of(T: np.ndarray) -> np.ndarray:
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    e.sort(axis=1)
    return np.unique(e, axis=0)


@dataclass
class Loop:
    pts: np.ndarray
    centroid: np.ndarray
    perimeter: float

    def extreme(self, d) -> np.ndarray:
        return self.pts[int(np.argmax(self.pts @ np.asarray(d, dtype=np.float64)))]


class Mesh:
    """Triangle mesh with a BVH (mathutils), ray casts and plane slices."""

    def __init__(self, V: np.ndarray, T: np.ndarray):
        self.V = np.asarray(V, dtype=np.float64)
        self.T = np.asarray(T, dtype=np.int64)
        self._bvh = None

    @property
    def bvh(self):
        if self._bvh is None:
            from mathutils.bvhtree import BVHTree

            self._bvh = BVHTree.FromPolygons(self.V.tolist(), self.T.tolist(), all_triangles=True)
        return self._bvh

    def ray(self, o, d, dist: float = 10.0):
        from mathutils import Vector

        loc, nrm, idx, t = self.bvh.ray_cast(Vector(tuple(map(float, o))), Vector(tuple(map(float, unit(d)))), dist)
        return (np.array(loc), np.array(nrm), idx, t) if loc is not None else None

    def nearest(self, p, dist: float = 10.0):
        from mathutils import Vector

        loc, nrm, idx, t = self.bvh.find_nearest(Vector(tuple(map(float, p))), dist)
        return (np.array(loc), np.array(nrm), idx, t) if loc is not None else None

    def slice(self, p0, n, mask: np.ndarray | None = None) -> list[Loop]:
        """Cross-section loops of the plane through p0 with normal n (only
        the triangles in `mask` when given: then chains may be open)."""
        n = unit(n)
        d = (self.V - np.asarray(p0, dtype=np.float64)) @ n
        d[np.abs(d) < 1e-12] = 1e-12
        s = d[self.T] > 0
        cnt = s.sum(axis=1)
        sel = (cnt == 1) | (cnt == 2)
        if mask is not None:
            sel &= mask
        T = self.T[sel]
        if not len(T):
            return []
        dT = d[T]
        keys, pts = [], []
        nv = len(self.V)
        for k in range(3):
            a, b = T[:, k], T[:, (k + 1) % 3]
            da, db = dT[:, k], dT[:, (k + 1) % 3]
            cross = (da > 0) != (db > 0)
            t = da / np.where(cross, da - db, 1.0)
            p = self.V[a] + (self.V[b] - self.V[a]) * t[:, None]
            key = np.minimum(a, b) * nv + np.maximum(a, b)
            keys.append(np.where(cross, key, -1))
            pts.append(p)
        keys = np.stack(keys, axis=1)
        pts = np.stack(pts, axis=1)
        order = np.argsort(keys < 0, axis=1, kind="stable")[:, :2]
        k2 = np.take_along_axis(keys, order, axis=1)
        p2 = np.take_along_axis(pts, order[:, :, None], axis=1)
        uk, inv = np.unique(k2.ravel(), return_inverse=True)
        seg = inv.reshape(-1, 2)
        P = np.zeros((len(uk), 3))
        P[seg.ravel()] = p2.reshape(-1, 3)
        lab = np.arange(len(uk))
        while True:
            m = np.minimum(lab[seg[:, 0]], lab[seg[:, 1]])
            new = lab.copy()
            np.minimum.at(new, seg[:, 0], m)
            np.minimum.at(new, seg[:, 1], m)
            new = new[new]
            if np.array_equal(new, lab):
                break
            lab = new
        loops = []
        sl = lab[seg[:, 0]]
        for L in np.unique(sl):
            sg = seg[sl == L]
            a, b = P[sg[:, 0]], P[sg[:, 1]]
            ln = np.linalg.norm(b - a, axis=1)
            per = float(ln.sum())
            if per < 1e-6:
                continue
            c = ((a + b) * 0.5 * ln[:, None]).sum(axis=0) / per
            loops.append(Loop(P[np.unique(sg)], c, per))
        return loops


def laplacian_smooth(V: np.ndarray, E: np.ndarray, iters: int, lam: float = 0.5, mu: float = -0.53,
                     w: np.ndarray | None = None) -> np.ndarray:
    """Taubin smoothing (volume-preserving) over an edge list; `w` (0..1 per
    vertex) scales the step; mu=0 gives plain Laplacian smoothing."""
    V = V.copy()
    deg = np.zeros(len(V))
    np.add.at(deg, E[:, 0], 1)
    np.add.at(deg, E[:, 1], 1)
    deg = np.maximum(deg, 1)
    ww = np.ones(len(V)) if w is None else w
    steps = (lam, mu) if mu else (lam,)
    for _ in range(iters):
        for f in steps:
            acc = np.zeros_like(V)
            np.add.at(acc, E[:, 0], V[E[:, 1]])
            np.add.at(acc, E[:, 1], V[E[:, 0]])
            V += f * ww[:, None] * (acc / deg[:, None] - V)
    return V


def umbrella(V: np.ndarray, E: np.ndarray) -> np.ndarray:
    """Uniform Laplacian vector (neighbour mean minus vertex)."""
    deg = np.zeros(len(V))
    np.add.at(deg, E[:, 0], 1)
    np.add.at(deg, E[:, 1], 1)
    acc = np.zeros_like(V)
    np.add.at(acc, E[:, 0], V[E[:, 1]])
    np.add.at(acc, E[:, 1], V[E[:, 0]])
    return acc / np.maximum(deg, 1)[:, None] - V


def vertex_normals(V: np.ndarray, T: np.ndarray) -> np.ndarray:
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    n = np.zeros_like(V)
    for k in range(3):
        np.add.at(n, T[:, k], fn)
    return unit(n)


# ----------------------------------------------------------------------------
# 1. The target
# ----------------------------------------------------------------------------


def load_sculpt(path: str) -> tuple[np.ndarray, np.ndarray]:
    """All mesh triangles of a glTF file in world coordinates (glTF import
    only: nothing in the file runs). Leaves an empty scene."""
    import bpy

    if not os.path.exists(path):
        raise FileNotFoundError(f"sculpt not found: {path} (pass --sculpt-dir)")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    Vs, Ts, off = [], [], 0
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        me = o.data
        mw = np.array(o.matrix_world)
        co = np.zeros(len(me.vertices) * 3)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
        me.calc_loop_triangles()
        t = np.zeros(len(me.loop_triangles) * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", t)
        Vs.append(co)
        Ts.append(t.reshape(-1, 3) + off)
        off += len(co)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return np.concatenate(Vs), np.concatenate(Ts)


def orient(V: np.ndarray, facing: str, height: float) -> np.ndarray:
    """Face -Y, stand on z = 0, centred on x = 0, scaled uniformly to `height`,
    torso centred in depth like ours (spine axis near y = 0)."""
    V = V.copy()
    if facing == "+Y":
        V[:, 0] *= -1
        V[:, 1] *= -1
    lo, hi = V.min(axis=0), V.max(axis=0)
    s = height / (hi[2] - lo[2])
    V = (V - np.array([0.5 * (lo[0] + hi[0]), 0.0, lo[2]])) * s
    band = V[(V[:, 2] > 0.45 * height) & (V[:, 2] < 0.72 * height) & (np.abs(V[:, 0]) < 0.10 * height)]
    V[:, 1] -= 0.5 * (band[:, 1].min() + band[:, 1].max())
    return V


def remesh_symmetric(V: np.ndarray, T: np.ndarray, voxel: float) -> tuple[np.ndarray, np.ndarray]:
    """Left half mirrored onto the right, merged and voxel-remeshed into one
    closed surface (Blender's Mirror and Remesh modifiers)."""
    import bmesh
    import bpy

    me = bpy.data.meshes.new("wrap_target")
    me.from_pydata(V.tolist(), [], T.tolist())
    me.update()
    obj = bpy.data.objects.new("wrap_target", me)
    bpy.context.scene.collection.objects.link(obj)
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=voxel * 0.05)
    geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
    bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(0, 0, 0), plane_no=(1, 0, 0), clear_inner=True)
    bm.to_mesh(me)
    bm.free()
    m = obj.modifiers.new("mirror", "MIRROR")
    m.use_axis[0] = True
    m.use_clip = True
    m.use_mirror_merge = True
    m.merge_threshold = voxel * 0.5
    r = obj.modifiers.new("remesh", "REMESH")
    r.mode = "VOXEL"
    r.voxel_size = voxel
    r.adaptivity = 0.0
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    em = ev.to_mesh()
    co = np.zeros(len(em.vertices) * 3)
    em.vertices.foreach_get("co", co)
    em.calc_loop_triangles()
    t = np.zeros(len(em.loop_triangles) * 3, dtype=np.int64)
    em.loop_triangles.foreach_get("vertices", t)
    ev.to_mesh_clear()
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(me)
    return co.reshape(-1, 3), t.reshape(-1, 3)


def prepare_target(sc: Sculpt, path: str, height: float) -> tuple[Mesh, dict]:
    t0 = time.time()
    V, T = load_sculpt(path)
    raw_tris = len(T)
    V = orient(V, sc.facing, height)
    V, T = remesh_symmetric(V, T, sc.voxel * height / 1.7)
    E = edges_of(T)
    V = laplacian_smooth(V, E, sc.smooth)
    if sc.face_smooth:
        top = V[:, 2].max()
        w = np.clip((V[:, 2] - (top - 0.30 * height / 1.7)) / 0.04, 0.0, 1.0)
        V = laplacian_smooth(V, E, sc.face_smooth, w=w)
    info = {"raw_tris": int(raw_tris), "target_tris": int(len(T)), "seconds": round(time.time() - t0, 1)}
    _log(f"target {os.path.basename(path)}: {raw_tris} -> {len(T)} tris after remesh ({info['seconds']}s)")
    return Mesh(V, T), info


def neutralise(m: Mesh, lm: dict, height: float) -> Mesh:
    """Smooth the groin and the chest apex into featureless forms."""
    E = edges_of(m.T)
    s = height / 1.7
    V = m.V
    # Groin: the pubic area in front of and above the crotch is smoothed
    # until no form is left but a soft, neutral curve.
    g = lm["crotch"] + np.array([0.0, -0.03 * s, 0.04 * s])
    d = np.linalg.norm((V - g) / np.array([1.0, 0.9, 1.0]), axis=1) / (GROIN[0] * s)
    w = np.clip(1.5 - d, 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    V = laplacian_smooth(V, E, GROIN[1], lam=0.5, mu=0.0, w=w)
    V = laplacian_smooth(V, E, 10, w=w)
    # Gluteal cleft: filled to a shallow groove (a deep, narrow cleft folds
    # our much coarser mesh).
    yb = float(lm["crotch"][1])
    zc = float(lm["crotch"][2])
    wc = np.clip(1.0 - np.abs(V[:, 0]) / (0.045 * s), 0.0, 1.0) * np.clip((V[:, 1] - yb) / (0.02 * s), 0.0, 1.0)
    wc *= np.clip((V[:, 2] - (zc - 0.03 * s)) / (0.02 * s), 0.0, 1.0) * np.clip(((zc + 0.15 * s) - V[:, 2]) / (0.04 * s), 0.0, 1.0)
    V = laplacian_smooth(V, E, CLEFT_PASSES, lam=0.5, mu=0.0, w=wc)
    wn = np.zeros(len(V))
    for side in (1, -1):
        c = lm["chest_apex.L"] * np.array([side, 1.0, 1.0])
        d = np.linalg.norm(V - c, axis=1)
        wn = np.maximum(wn, np.clip(1.5 - d / (NIPPLE[0] * s), 0.0, 1.0))
    V = laplacian_smooth(V, E, NIPPLE[1], w=wn)
    # Sternum: the groove between the pectorals (or breasts) is softened.
    za = float(lm["chest_apex.L"][2])
    ws = np.clip(1.0 - np.abs(V[:, 0]) / (0.04 * s), 0.0, 1.0) * (V[:, 1] < 0)
    ws *= np.clip(1.0 - np.abs(V[:, 2] - (za - 0.01 * s)) / (0.08 * s), 0.0, 1.0)
    V = laplacian_smooth(V, E, 20, lam=0.5, mu=0.0, w=ws)
    # Face: our eyes, brows and mouth are painted, so sculpted eye sockets,
    # lids and lips are softened into shallow forms (their rims would fold
    # the painted outlines).
    hh = float(lm["crown"][2] - lm["chin"][2])
    wf = np.zeros(len(V))
    for side in (1, -1):
        c = lm["eye.L"] * np.array([side, 1.0, 1.0])
        wf = np.maximum(wf, np.clip(1.4 - np.linalg.norm(V - c, axis=1) / (EYE_FILL[0] * hh), 0.0, 1.0))
    wf = np.maximum(wf, 0.6 * np.clip(1.4 - np.linalg.norm(V - lm["mouth"], axis=1) / (0.08 * hh), 0.0, 1.0))
    V = laplacian_smooth(V, E, EYE_FILL[1], lam=0.5, mu=0.0, w=wf)
    V = laplacian_smooth(V, E, 6, w=wf)
    # Ears: our own ear shells replace the sculpt's, so its ears are smoothed
    # into the side of the head (the skin under our shells follows that).
    we = np.zeros(len(V))
    for side in (1, -1):
        c = lm["ear.L"] * np.array([side, 1.0, 1.0])
        we = np.maximum(we, np.clip(1.5 - np.linalg.norm(V - c, axis=1) / (EAR_FILL[0] * hh), 0.0, 1.0))
    V = laplacian_smooth(V, E, EAR_FILL[1], lam=0.5, mu=0.0, w=we)
    V = laplacian_smooth(V, E, 6, w=we)
    return Mesh(V, m.T)


# ----------------------------------------------------------------------------
# 3. Landmarks (one detector for both meshes)
# ----------------------------------------------------------------------------


def _nearest_loop(loops: list[Loop], p) -> Loop | None:
    if not loops:
        return None
    return min(loops, key=lambda L: float(np.linalg.norm(L.centroid - p)))


def trace_arm(m: Mesh, H_: float, frac_elbow: float | None, side: int = 1, elbow_hint=None) -> dict:
    """Arm centreline by slices across the arm, from the fingertip to where
    the arm loop merges into the torso (axis refitted once)."""
    V = m.V
    s = H_ / 1.7
    cand = V[(V[:, 2] > 0.45 * H_) & (V[:, 0] * side > 0)]
    tip = cand[int(np.argmax(cand[:, 0] * side))]
    root_guess = np.array([side * 0.10 * s, 0.0, 0.80 * H_])
    axis = unit(root_guess - tip)
    for _ in range(2):
        L = float(np.linalg.norm(root_guess - tip)) * 1.15
        ts = np.arange(0.005 * s, L, ARM_STEP * s)
        cs, gs, ns = [], [], []
        prev = tip + axis * ts[0]
        merged = None
        for i, t in enumerate(ts):
            p = tip + axis * t
            loops = [lp for lp in m.slice(p, axis) if np.linalg.norm(lp.pts - p, axis=1).min() < 0.06 * s]
            ns.append(len(loops))
            lp = _nearest_loop(loops, prev)
            if lp is None:
                cs.append(p)
                gs.append(gs[-1] if gs else 0.0)
                continue
            if i > 8 and t > 0.3 * L:
                med = float(np.median(gs[-6:]))
                if lp.perimeter > 1.6 * med:
                    merged = i
                    break
            cs.append(lp.centroid)
            gs.append(lp.perimeter)
            prev = lp.centroid
        cs, gs = np.array(cs), np.array(gs)
        if merged is None:
            merged = len(cs)
        palm = next((i for i in range(3, len(ns)) if ns[i] == 1), 3)
        sel = cs[palm:merged]
        if len(sel) > 4:
            c0 = sel.mean(axis=0)
            _, _, vt = np.linalg.svd(sel - c0)
            axis = unit(vt[0] * np.sign(vt[0] @ axis))
    n = len(cs)
    palm = next((i for i in range(3, len(ns)) if ns[i] == 1), 3)
    root = max(min(merged, n) - 2, palm + 6)
    span = root - palm
    w_lo, w_hi = palm + max(int(0.05 * span), 1), palm + int(0.40 * span)
    gsm = np.convolve(gs, np.ones(3) / 3, mode="same")
    wrist = w_lo + int(np.argmin(gsm[w_lo:w_hi + 1])) if w_hi > w_lo else palm + 2
    if elbow_hint is not None:  # our own elbow joint
        elbow = int(np.argmin(np.linalg.norm(cs[: root + 1] - elbow_hint, axis=1)))
    else:  # the sculpt's: at our proportion between wrist and arm root
        elbow = int(round(wrist + (0.47 if frac_elbow is None else frac_elbow) * (root - wrist)))
    hand = palm + int(0.35 * (wrist - palm))
    return dict(tip=tip, axis=axis, cs=cs, gs=gs, i_palm=palm, i_wrist=wrist, i_elbow=elbow, i_root=root,
                i_hand=hand, frac_elbow=(elbow - wrist) / max(root - wrist, 1))


def arm_ring(m: Mesh, c: np.ndarray, axis: np.ndarray, prefix: str, lm: dict) -> None:
    lp = _nearest_loop(m.slice(c, axis), c)
    if lp is None:
        return
    up = unit(np.array([0.0, 0.0, 1.0]) - axis * axis[2])
    fw = unit(np.cross(up, axis))
    if fw[1] > 0:
        fw = -fw
    lm[prefix] = lp.centroid
    lm[prefix + "_up"] = lp.extreme(up)
    lm[prefix + "_dn"] = lp.extreme(-up)
    lm[prefix + "_fw"] = lp.extreme(fw)
    lm[prefix + "_bk"] = lp.extreme(-fw)


def leg_profile(m: Mesh, H_: float, z_hi: float, side: int = 1, z_lo: float = 0.03, step: float = 0.005,
                mask: np.ndarray | None = None):
    """Horizontal slices up one leg: heights, centroids and girths."""
    s = H_ / 1.7
    zs = np.arange(z_lo * H_, z_hi, step * s)
    cs, gs = [], []
    prev = None
    for z in zs:
        loops = [lp for lp in m.slice(np.array([0, 0, z]), np.array([0, 0, 1.0]), mask) if lp.centroid[0] * side > 0.015 * s]
        if prev is not None:
            loops = [lp for lp in loops if np.linalg.norm(lp.centroid[:2] - prev[:2]) < 0.08 * s] or loops
            lp = _nearest_loop(loops, prev)
        else:
            lp = max(loops, key=lambda L: L.perimeter) if loops else None
        if lp is None:
            cs.append(np.array([side * 0.09 * s, 0, z]) if prev is None else prev)
            gs.append(gs[-1] if gs else 0.0)
            continue
        cs.append(lp.centroid)
        gs.append(lp.perimeter)
        prev = lp.centroid
    return zs, np.array(cs), np.array(gs)


def ring_points(m: Mesh, z: float, near: np.ndarray, prefix: str, lm: dict, side: int = 1,
                mask: np.ndarray | None = None) -> Loop | None:
    lp = _nearest_loop(m.slice(np.array([0.0, 0.0, z]), np.array([0.0, 0.0, 1.0]), mask), near)
    if lp is None:
        return None
    lm[prefix] = lp.centroid
    lm[prefix + "_fw"] = lp.extreme([0, -1, 0])
    lm[prefix + "_bk"] = lp.extreme([0, 1, 0])
    lm[prefix + "_out"] = lp.extreme([side, 0, 0])
    lm[prefix + "_in"] = lp.extreme([-side, 0, 0])
    return lp


def torso_loop(m: Mesh, z: float) -> Loop | None:
    loops = m.slice(np.array([0.0, 0.0, z]), np.array([0.0, 0.0, 1.0]))
    inside = [lp for lp in loops if lp.pts[:, 0].min() < 0 < lp.pts[:, 0].max()]
    return max(inside, key=lambda L: L.perimeter) if inside else None


def _midline_front(m: Mesh, z: float, H_: float) -> np.ndarray:
    hit = m.ray(np.array([0.0, -H_, z]), np.array([0, 1.0, 0]), 2 * H_)
    return hit[0] if hit else np.array([0.0, 0.0, z])


def _midline_back(m: Mesh, z: float, H_: float) -> np.ndarray:
    hit = m.ray(np.array([0.0, H_, z]), np.array([0, -1.0, 0]), 2 * H_)
    return hit[0] if hit else np.array([0.0, 0.0, z])


def _chin(m: Mesh, crown: np.ndarray, H_: float) -> np.ndarray:
    """The chin tip: rays straight up the midline from below the jaw find the
    face's underside (nose, lips, chin); the chin is the front-most hit
    within 1 cm of the lowest one."""
    s = H_ / 1.7
    z0 = crown[2] - 0.30 * s
    front = _midline_front(m, crown[2] - 0.15 * s, H_)
    hits = []
    for y in np.arange(front[1] - 0.02 * s, front[1] + 0.12 * s, 0.0015 * s):
        hit = m.ray(np.array([0.0, y, z0]), np.array([0, 0, 1.0]), 0.5 * s)
        if hit is not None and z0 + 0.02 * s < hit[0][2] < crown[2] - 0.08 * s and hit[1][2] < -0.4:
            hits.append(hit[0])
    if not hits:
        return np.array([0.0, front[1], crown[2] - 0.25 * s])
    P = np.array(hits)
    # The face's front profile down to the throat (the lowest underside hit),
    # and the chin tip is the profile's extreme forward-and-down.
    zt = float(P[:, 2].min())
    prof = [_midline_front(m, z, H_) for z in np.arange(zt, crown[2] - 0.12 * s, 0.002 * s)]
    C = np.vstack([P, np.array(prof)])
    C = C[C[:, 2] >= zt - 1e-6]
    return C[int(np.argmax(-C[:, 1] - C[:, 2]))]


def _profile(m: Mesh, z0: float, z1: float, n: int = 120) -> tuple[np.ndarray, np.ndarray]:
    zs = np.linspace(z0, z1, n)
    ys = np.array([_midline_front(m, z, 2.0)[1] for z in zs])
    return zs, ys


def _poly2(X: np.ndarray, Z: np.ndarray, deg: int = 4) -> np.ndarray:
    cols = [(X ** i) * (Z ** j) for i in range(deg + 1) for j in range(deg + 1 - i)]
    return np.stack(cols, axis=-1)


def _flood(mask: np.ndarray, seed: tuple[int, int]) -> np.ndarray:
    out = np.zeros_like(mask)
    stack = [seed]
    h, w = mask.shape
    while stack:
        i, j = stack.pop()
        if i < 0 or j < 0 or i >= h or j >= w or out[i, j] or not mask[i, j]:
            continue
        out[i, j] = True
        stack += [(i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)]
    return out


def socket_box(m: Mesh, chin: np.ndarray, crown: np.ndarray, hh: float) -> dict:
    """Left eye socket: a depth map of the face front (rays from the front)
    is fitted with a smooth quartic surface, iteratively ignoring recesses
    and the nose band; the socket is the connected recess around the deepest
    point of the eye band (at 30% of its depth). Returns its weighted centre
    and its inner, outer, lower and upper extremes, all on the surface."""
    step = 0.004 * hh
    xs = np.arange(-0.34 * hh, 0.34 * hh + 1e-9, step)
    zs = np.arange(chin[2] + 0.28 * hh, chin[2] + 0.80 * hh + 1e-9, step)
    D = np.full((len(zs), len(xs)), np.nan)
    for i, z in enumerate(zs):
        for j, x in enumerate(xs):
            hit = m.ray(np.array([x, -3.0, z]), np.array([0, 1.0, 0]), 6.0)
            if hit is not None and hit[0][1] < crown[1]:
                D[i, j] = hit[0][1]
    X, Z = np.meshgrid(xs, zs)
    ok = ~np.isnan(D)
    Xn, Zn = X / hh, (Z - chin[2]) / hh
    Df = np.where(ok, D, 0.0)
    A = _poly2(Xn, Zn)
    w = ok & (np.abs(Xn) > 0.06)
    for _ in range(6):
        sol, *_ = np.linalg.lstsq(A[w], Df[w], rcond=None)
        res = Df - A @ sol  # positive: recessed
        thr = float(np.percentile(res[w], 75))
        w = ok & (np.abs(Xn) > 0.06) & (res < max(thr, 0.0003))
    win = ok & (Xn > 0.06) & (Xn < 0.27) & (Zn > 0.36) & (Zn < 0.74)
    R = np.where(win, res, -1.0)
    i, j = np.unravel_index(int(np.argmax(R)), R.shape)
    mx = float(R[i, j])
    comp = _flood(R > 0.3 * mx, (int(i), int(j)))
    wt = R[comp] - 0.3 * mx
    cx = float((X[comp] * wt).sum() / wt.sum())
    cz = float((Z[comp] * wt).sum() / wt.sum())

    def on(x, z):
        hit = m.ray(np.array([x, -3.0, z]), np.array([0, 1.0, 0]), 6.0)
        return np.array(hit[0]) if hit else np.array([x, 0.0, z])

    return {"centre": on(cx, cz), "inner": on(float(X[comp].min()), cz), "outer": on(float(X[comp].max()), cz),
            "lower": on(cx, float(Z[comp].min())), "upper": on(cx, float(Z[comp].max())), "depth": mx}


def _eye_socket(m: Mesh, chin: np.ndarray, crown: np.ndarray, hh: float) -> np.ndarray:
    """Left eye socket centre (`socket_box`)."""
    return socket_box(m, chin, crown, hh)["centre"]


def _nose(m: Mesh, eye: np.ndarray, chin: np.ndarray) -> np.ndarray:
    zs, ys = _profile(m, chin[2] + 0.25 * (eye[2] - chin[2]), eye[2], 80)
    i = int(np.argmin(ys))
    return np.array([0.0, ys[i], zs[i]])


def _mouth(m: Mesh, nose: np.ndarray, chin: np.ndarray, frac: float) -> np.ndarray:
    """Midline groove between nose and chin, else at our own fraction."""
    zs, ys = _profile(m, chin[2] + 0.2 * (nose[2] - chin[2]), nose[2] - 0.15 * (nose[2] - chin[2]), 60)
    base = np.interp(zs, [zs[0], zs[-1]], [ys[0], ys[-1]])
    dep = ys - base
    i = int(np.argmax(dep))
    if dep[i] > 0.0012:
        return np.array([0.0, ys[i], zs[i]])
    return _midline_front(m, chin[2] + frac * (nose[2] - chin[2]), 2.0)


@dataclass
class Face:
    """Our painted face anchors (from the face frame)."""
    eye: np.ndarray
    nose: np.ndarray
    mouth: np.ndarray


def landmarks(m: Mesh, H_: float, face: Face | None = None, fr: dict | None = None,
              hints: dict | None = None) -> tuple[dict, dict]:
    """Left-side landmarks (x >= 0) and measurements. `face`: our face anchors
    (else detected); `hints`: our own elbow and knee joints and crotch
    point (our mesh); `fr`: our proportions (frac_elbow, frac_knee,
    frac_mouth), which place the sculpt's elbow, knee and (when it has no
    mouth groove) mouth."""
    fr = fr or {}
    hints = hints or {}
    s = H_ / 1.7
    lm: dict[str, np.ndarray] = {}
    info: dict = {}
    V = m.V
    top = V[int(np.argmax(V[:, 2]))]
    lm["crown"] = np.array([0.0, top[1], top[2]])
    # --- arm --------------------------------------------------------------
    arm = trace_arm(m, H_, fr.get("frac_elbow"), elbow_hint=hints.get("elbow"))
    ax, cs = arm["axis"], arm["cs"]
    info["frac_elbow"] = arm["frac_elbow"]
    info["arm"] = {k: v for k, v in arm.items() if k.startswith("i_") or k == "axis"}
    lm["fingertip.L"] = arm["tip"]
    lm["hand.L"] = cs[arm["i_hand"]]
    for name, i in (("wrist", arm["i_wrist"]), ("elbow", arm["i_elbow"]), ("arm_root", arm["i_root"])):
        arm_ring(m, cs[i], ax, f"{name}.L", lm)
    for name, (i, j) in (("forearm_mid", ("i_wrist", "i_elbow")), ("upper_arm_mid", ("i_elbow", "i_root"))):
        arm_ring(m, cs[int(round(0.5 * (arm[i] + arm[j])))], ax, f"{name}.L", lm)
    # --- legs and feet ------------------------------------------------------
    foot = V[(V[:, 2] < 0.025 * H_) & (V[:, 0] > 0.0)]
    lm["toe_tip.L"] = foot[int(np.argmin(foot[:, 1]))]
    lm["heel.L"] = foot[int(np.argmax(foot[:, 1]))]
    ball_y = lm["toe_tip.L"][1] + 0.27 * (lm["heel.L"][1] - lm["toe_tip.L"][1])
    ball = foot[np.abs(foot[:, 1] - ball_y) < 0.012 * s]
    if len(ball):
        lm["ball_out.L"] = ball[int(np.argmax(ball[:, 0]))]
        lm["ball_in.L"] = ball[int(np.argmin(ball[:, 0]))]
    if "crotch" in hints:
        lm["crotch"] = np.array(hints["crotch"], dtype=np.float64)
        crotch_z = float(lm["crotch"][2])
    else:
        mid = V[(V[:, 2] > 0.4 * H_) & (V[:, 2] < 0.55 * H_) & (np.abs(V[:, 0]) < 0.05 * s)]
        yc = float(0.5 * (mid[:, 1].min() + mid[:, 1].max()))
        hit = m.ray(np.array([0.0, yc, 0.05 * s]), np.array([0, 0, 1.0]), H_)
        crotch_z = float(hit[0][2]) if hit else 0.47 * H_
        lm["crotch"] = np.array([0.0, yc, crotch_z])
    leg_mask = hints.get("leg_tris")
    zs, lcs, lgs = leg_profile(m, H_, crotch_z - 0.015 * s, mask=leg_mask)
    n = len(zs)
    q = max(int(0.3 * n), 4)
    i_ank = 2 + int(np.argmin(lgs[2:q]))
    if "knee" in hints:
        i_knee = int(np.argmin(np.abs(zs - hints["knee"][2])))
    else:
        i_knee = int(round(i_ank + fr.get("frac_knee", 0.45) * (n - 1 - i_ank)))
    info["frac_knee"] = (i_knee - i_ank) / max(n - 1 - i_ank, 1)
    i_calf = i_ank + int(np.argmax(lgs[i_ank:i_knee])) if i_knee > i_ank + 2 else (i_ank + i_knee) // 2
    i_thigh = int(round(0.5 * (i_knee + n - 1)))
    for name, i in (("ankle", i_ank), ("calf", i_calf), ("knee", i_knee), ("thigh_mid", i_thigh), ("thigh_top", n - 2)):
        ring_points(m, zs[i], lcs[i], f"{name}.L", lm, mask=leg_mask)
    info["leg"] = {"i_ankle": i_ank, "i_knee": i_knee, "n": n}
    # --- torso ----------------------------------------------------------------
    zt = np.arange(0.45 * H_, lm["arm_root.L"][2] + 0.10 * s, 0.005 * s)
    tl = [torso_loop(m, z) for z in zt]
    half = np.array([lp.pts[:, 0].max() if lp else 0.0 for lp in tl])
    girth = np.array([lp.perimeter if lp else 0.0 for lp in tl])
    i_root = int(np.argmin(np.abs(zt - lm["arm_root.L"][2])))
    arm_x = lm["arm_root.L"][0]
    # Armpit: below the arm root, where the torso loop stops reaching the arm
    # (skipping the narrower shoulder slices above a T-pose arm first).
    thr = arm_x - 0.01 * s
    i = min(i_root + 12, len(zt) - 1)
    while i > 0 and half[i] < thr and zt[i] > lm["arm_root.L"][2] - 0.02 * s:
        i -= 1
    while i > 0 and half[i] >= thr:
        i -= 1
    i_pit = i
    pit = tl[i_pit].extreme([1, 0, 0]).copy() if tl[i_pit] else lm["arm_root.L"].copy()
    pit[2] = zt[min(i_pit + 1, len(zt) - 1)]
    lm["armpit.L"] = pit
    band = V[(V[:, 2] > pit[2] - 0.17 * s) & (V[:, 2] < pit[2] + 0.01 * s) & (V[:, 0] > 0.03 * s)
             & (V[:, 0] < 0.12 * s) & (V[:, 1] < 0)]
    lm["chest_apex.L"] = band[int(np.argmin(band[:, 1]))]
    zc = float(lm["chest_apex.L"][2])
    lm["chest_front"] = _midline_front(m, zc, H_)
    lm["chest_back"] = _midline_back(m, zc, H_)
    lc = tl[int(np.argmin(np.abs(zt - zc)))]
    if lc:
        lm["chest_side.L"] = lc.extreme([1, 0, 0])
    sel = (zt > crotch_z + 0.07 * s) & (zt < zc - 0.06 * s)
    i_w = int(np.nonzero(sel)[0][np.argmin(girth[sel])])
    selh = (zt > crotch_z + 0.01 * s) & (zt < zt[i_w])
    i_h = int(np.nonzero(selh)[0][np.argmax(half[selh])]) if selh.any() else max(i_w - 10, 0)
    for name, i in (("waist", i_w), ("hip", i_h)):
        lm[f"{name}_side.L"] = tl[i].extreme([1, 0, 0])
        lm[f"{name}_front"] = _midline_front(m, zt[i], H_)
        lm[f"{name}_back"] = _midline_back(m, zt[i], H_)
    info["waist_z"], info["hip_z"], info["chest_z"] = float(zt[i_w]), float(zt[i_h]), zc
    # --- neck and head ----------------------------------------------------------
    chin = _chin(m, lm["crown"], H_)
    lm["chin"] = chin
    zn = np.arange(pit[2], chin[2] - 0.005 * s, 0.004 * s)
    nl = [torso_loop(m, z) for z in zn]
    ng = np.array([lp.perimeter if lp else 9.0 for lp in nl])
    i_n = int(np.argmin(ng))
    lm["neck"] = nl[i_n].centroid
    lm["neck_fw"] = nl[i_n].extreme([0, -1, 0])
    lm["neck_bk"] = nl[i_n].extreme([0, 1, 0])
    lm["neck_side.L"] = nl[i_n].extreme([1, 0, 0])
    i_b = next((i for i in range(i_n, -1, -1) if ng[i] > 1.6 * ng[i_n]), 0)
    lm["neck_base_front"] = _midline_front(m, zn[i_b], H_)
    lm["neck_base_back"] = _midline_back(m, zn[i_b], H_)
    info["neck_girth"], info["neck_base_z"] = float(ng[i_n]), float(zn[i_b])
    r = lm["arm_root.L"]
    hit = m.ray(np.array([r[0], r[1], r[2] + 0.3 * s]), np.array([0, 0, -1.0]), 0.4 * s)
    lm["shoulder_top.L"] = hit[0] if hit else r + np.array([0, 0, 0.05 * s])
    hh = float(lm["crown"][2] - chin[2])
    info["head_h"] = hh
    if face is not None:
        eye, nose, mouth = face.eye, face.nose, face.mouth
    else:
        eye = _eye_socket(m, chin, lm["crown"], hh)
        nose = _nose(m, eye, chin)
        mouth = _mouth(m, nose, chin, fr.get("frac_mouth", 0.5))
    lm["eye.L"], lm["nose"], lm["mouth"] = eye, nose, mouth
    info["frac_mouth"] = float((mouth[2] - chin[2]) / max(nose[2] - chin[2], 1e-6))
    ez = float(eye[2])
    cz = float(lm["crown"][2])
    lm["forehead"] = _midline_front(m, ez + 0.45 * (cz - ez), H_)
    lm["head_back"] = _midline_back(m, ez, H_)
    lm["head_back_hi"] = _midline_back(m, ez + 0.55 * (cz - ez), H_)
    lm["occiput_low"] = _midline_back(m, chin[2] + 0.18 * hh, H_)
    ht = torso_loop(m, ez + 0.35 * (cz - ez))
    if ht:
        lm["temple.L"] = ht.extreme([1, 0, 0])
    hv = V[(V[:, 2] > mouth[2]) & (V[:, 2] < ez + 0.03 * s) & (V[:, 0] > 0) & (np.abs(V[:, 1] - eye[1]) < 0.16 * s)
           & (V[:, 0] < lm["neck_side.L"][0] + 0.12 * s)]
    lm["ear.L"] = hv[int(np.argmax(hv[:, 0]))]
    jl = torso_loop(m, chin[2] + 0.3 * (ez - chin[2]))
    if jl:
        lm["jaw.L"] = jl.extreme(unit(np.array([1.0, -0.6, 0.0])))
    return lm, info


def mirrored(lm: dict) -> dict:
    """Both sides: '.L' landmarks mirrored to '.R'; midline points put on x = 0."""
    out = {}
    for k, p in lm.items():
        if k.endswith(".L"):
            out[k] = p
            out[k[:-2] + ".R"] = p * np.array([-1.0, 1.0, 1.0])
        else:
            q = np.array(p, dtype=np.float64)
            q[0] = 0.0
            out[k] = q
    return out


# ----------------------------------------------------------------------------
# 2. Posing (dual quaternions over our weights)
# ----------------------------------------------------------------------------

POSE_BONES = ("torso", "upper_arm.L", "forearm.L", "hand.L", "upper_arm.R", "forearm.R", "hand.R",
              "thigh.L", "shin.L", "foot.L", "thigh.R", "shin.R", "foot.R")


def _stem_side(group: str) -> tuple[str, str]:
    b = group[4:] if group.startswith("DEF-") else group
    side = ".L" if ".L" in b else ".R" if ".R" in b else ""
    return b.split(".")[0], side


def pose_bone_of(group: str) -> str:
    stem, side = _stem_side(group)
    if stem in ("upper_arm", "forearm", "thigh", "shin"):
        return stem + side
    if stem in ("hand", "palm", "thumb") or stem.startswith("f_"):
        return "hand" + side
    if stem in ("foot", "toe", "heel"):
        return "foot" + side
    return "torso"


def pose_weights(weights: list[dict]) -> np.ndarray:
    idx = {b: i for i, b in enumerate(POSE_BONES)}
    W = np.zeros((len(weights), len(POSE_BONES)))
    for vi, w in enumerate(weights):
        for g, v in w.items():
            W[vi, idx[pose_bone_of(g)]] += v
    W[W.sum(axis=1) <= 1e-9, 0] = 1.0
    return W / W.sum(axis=1, keepdims=True)


def dqs(V: np.ndarray, W: np.ndarray, xf: dict[str, tuple[np.ndarray, np.ndarray]], rotations: bool = False):
    """Dual-quaternion skinning: xf[bone] = (R, t) with p -> R p + t. With
    `rotations`, also each vertex's blended rotation matrix."""
    from .mh_body import _qmul, _qrot, _quat

    B = len(POSE_BONES)
    qr = np.zeros((B, 4))
    qd = np.zeros((B, 4))
    for i, b in enumerate(POSE_BONES):
        R, t = xf.get(b, (np.eye(3), np.zeros(3)))
        q = _quat(R)
        qr[i] = q
        qd[i] = 0.5 * _qmul(np.array([0.0, *t]), q)
    dom = np.argmax(W, axis=1)
    sign = np.sign(qr[dom] @ qr.T)
    sign[sign == 0] = 1.0
    Ws = W * sign
    br = Ws @ qr
    bd = Ws @ qd
    nrm = np.linalg.norm(br, axis=1, keepdims=True)
    br /= nrm
    bd /= nrm
    conj = br * np.array([1.0, -1.0, -1.0, -1.0])
    t = 2.0 * _qmul(bd, conj)[:, 1:]
    out = _qrot(br, V) + t
    if not rotations:
        return out
    w, x, y, z = br.T
    R = np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1),
                  np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1),
                  np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1)], 1)
    return out, R


def min_rotation(a, b) -> np.ndarray:
    from .mh_body import _min_rotation

    return _min_rotation(np.asarray(a, float), np.asarray(b, float))


def chain_xf(joints: dict[str, np.ndarray], targets: dict[str, np.ndarray]) -> dict:
    """Rigid transforms that turn each limb chain of `joints`
    (sj-el-wr, hip-kn-an per side) onto the directions of `targets` (same
    keys); the chain root may move to the target's root."""
    xf = {}
    for sd in ("L", "R"):
        for (a, b, c), (b1, b2, b3) in ((("sj", "el", "wr"), ("upper_arm", "forearm", "hand")),
                                        (("hip", "kn", "an"), ("thigh", "shin", "foot"))):
            A, B, C = (joints[f"{k}.{sd}"] for k in (a, b, c))
            TA, TB, TC = (targets[f"{k}.{sd}"] for k in (a, b, c))
            R1 = min_rotation(B - A, TB - TA)
            t1 = TA - R1 @ A
            xf[f"{b1}.{sd}"] = (R1, t1)
            B1 = R1 @ B + t1
            R2 = min_rotation(R1 @ (C - B), TC - TB) @ R1
            t2 = B1 - R2 @ B
            xf[f"{b2}.{sd}"] = (R2, t2)
            xf[f"{b3}.{sd}"] = (R2, t2)
    return xf


# ----------------------------------------------------------------------------
# 4. Thin-plate spline
# ----------------------------------------------------------------------------


class TPS:
    """3D thin-plate (biharmonic, phi = r) warp from `src` to `dst` landmarks."""

    def __init__(self, src: np.ndarray, dst: np.ndarray, reg: float = TPS_REG):
        n = len(src)
        Km = np.linalg.norm(src[:, None, :] - src[None, :, :], axis=2) + reg * np.eye(n)
        Pm = np.hstack([np.ones((n, 1)), src])
        A = np.zeros((n + 4, n + 4))
        A[:n, :n] = Km
        A[:n, n:] = Pm
        A[n:, :n] = Pm.T
        b = np.zeros((n + 4, 3))
        b[:n] = dst
        sol = np.linalg.solve(A, b)
        self.src, self.w, self.a = src, sol[:n], sol[n:]

    def __call__(self, P: np.ndarray) -> np.ndarray:
        P = np.atleast_2d(P)
        out = np.zeros_like(P, dtype=np.float64)
        for i in range(0, len(P), 4096):
            Q = P[i:i + 4096]
            U = np.linalg.norm(Q[:, None, :] - self.src[None, :, :], axis=2)
            out[i:i + 4096] = U @ self.w + np.hstack([np.ones((len(Q), 1)), Q]) @ self.a
        return out


# ----------------------------------------------------------------------------
# 5. Projection and relax
# ----------------------------------------------------------------------------

PARTS = ("torso", "head", "arm.L", "arm.R", "hand.L", "hand.R", "leg.L", "leg.R", "foot.L", "foot.R")


def part_of(group: str) -> str:
    stem, side = _stem_side(group)
    b = group[4:] if group.startswith("DEF-") else group
    if stem in ("upper_arm", "forearm"):
        return "arm" + side
    if stem in ("thigh", "shin"):
        return "leg" + side
    if stem in ("hand", "palm", "thumb") or stem.startswith("f_"):
        return "hand" + side
    if stem in ("foot", "toe", "heel"):
        return "foot" + side
    if b in ("spine.005", "spine.006", "jaw") or stem == "eye":
        return "head"
    return "torso"


def vertex_parts(weights: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Dominant part per vertex and the per-part weight matrix."""
    idx = {p: i for i, p in enumerate(PARTS)}
    W = np.zeros((len(weights), len(PARTS)))
    for vi, w in enumerate(weights):
        for g, v in w.items():
            W[vi, idx[part_of(g)]] += v
    W[W.sum(axis=1) <= 0, 0] = 1.0
    W /= W.sum(axis=1, keepdims=True)
    return np.argmax(W, axis=1), W


class PartTargets:
    """BVHs over the target triangles of part sets; a target triangle takes
    the part of the nearest vertex of our warped mesh."""

    def __init__(self, target: Mesh, ours_V: np.ndarray, ours_part: np.ndarray):
        from mathutils import Vector
        from mathutils.kdtree import KDTree

        kd = KDTree(len(ours_V))
        for i, p in enumerate(ours_V):
            kd.insert(Vector(tuple(map(float, p))), i)
        kd.balance()
        cent = target.V[target.T].mean(axis=1)
        lab = np.zeros(len(cent), dtype=np.int64)
        for i, c in enumerate(cent):
            _, j, _ = kd.find(Vector(tuple(map(float, c))))
            lab[i] = ours_part[j]
        # Limbs never cross the midline: a left-limb label on the right side
        # (our thighs touch) becomes the right limb's, and vice versa.
        side = np.sign(cent[:, 0])
        for p, q in (("arm.L", "arm.R"), ("hand.L", "hand.R"), ("leg.L", "leg.R"), ("foot.L", "foot.R")):
            il, ir = PARTS.index(p), PARTS.index(q)
            lab[(lab == il) & (side < 0)] = ir
            lab[(lab == ir) & (side > 0)] = il
        self.target, self.lab = target, lab
        self.trees: dict[frozenset, object] = {}

    def tree(self, parts: frozenset):
        t = self.trees.get(parts)
        if t is None:
            from mathutils.bvhtree import BVHTree

            T = self.target.T[np.isin(self.lab, list(parts))]
            t = BVHTree.FromPolygons(self.target.V.tolist(), T.tolist(), all_triangles=True)
            self.trees[parts] = t
        return t


def tri_normals(P: np.ndarray, T: np.ndarray) -> np.ndarray:
    return unit(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]))


def project(V: np.ndarray, T: np.ndarray, pt: PartTargets, allowed: list[frozenset], proj_w: np.ndarray,
            relax_w: np.ndarray, soft: np.ndarray, untangle: np.ndarray | None = None, iters: int = PROJ_ITERS,
            relax: int = RELAX_ITERS) -> np.ndarray:
    """Vertices onto the target in smoothed displacement steps, then a
    tangential relax toward the warped layout's local shape (re-projected
    after each pass), then a final snap that skips any vertex whose
    triangles it would fold. proj_w: 0 = not projected, 1 = on the surface;
    soft: vertices whose snaps are always smoothed over their neighbours
    (the painted face: its tiny outline triangles must not fold); untangle
    (0..1): where the relax evens the edges instead of keeping the warped
    layout (crotch, armpits: our own layout is folded there)."""
    from mathutils import Vector

    E = edges_of(T)
    groups: dict[frozenset, list[int]] = {}
    for i, a in enumerate(allowed):
        if proj_w[i] > 0:
            groups.setdefault(a, []).append(i)

    def closest(P: np.ndarray) -> np.ndarray:
        out = P.copy()
        for parts, ids in groups.items():
            tree = pt.tree(parts)
            for i in ids:
                loc = tree.find_nearest(Vector(tuple(map(float, P[i]))))[0]
                if loc is not None:
                    out[i] = loc
        return out

    def snap(P: np.ndarray, k: int = 2) -> np.ndarray:
        d = (closest(P) - P) * proj_w[:, None]
        ds = laplacian_smooth(d, E, k, lam=0.6, mu=0.0)
        d[soft] = ds[soft]
        return d

    ref_L = umbrella(V, E)
    if untangle is not None:
        ref_L *= (1.0 - untangle)[:, None]
        relax_w = np.maximum(relax_w, untangle * proj_w)
    P = V.copy()
    for it in range(iters):
        d = (closest(P) - P) * proj_w[:, None]
        k = max(1, int(round(10 * (1 - it / iters))))
        P = P + laplacian_smooth(d, E, k, lam=0.6, mu=0.0)
    for _ in range(relax):
        P = P + snap(P)
        Nv = vertex_normals(P, T)
        Ls = umbrella(P, E) - ref_L
        Ls -= Nv * np.einsum("nd,nd->n", Ls, Nv)[:, None]
        P = P + 0.6 * relax_w[:, None] * Ls
    # Final snap, vertex by vertex where it folds nothing.
    for _ in range(3):
        d = snap(P)
        Q = P + d
        n0, n1 = tri_normals(P, T), tri_normals(Q, T)
        bad = np.einsum("nd,nd->n", n0, n1) < 0.7
        hold = np.zeros(len(P), dtype=bool)
        hold[T[bad].ravel()] = True
        Q[hold] = P[hold] + 0.5 * d[hold]
        P = Q
    return P


def relax_pose(P: np.ndarray, Pref: np.ndarray, T: np.ndarray, W: np.ndarray, R: np.ndarray, iters: int = 60) -> np.ndarray:
    """After a large re-pose: restore the local shape (Laplacian coordinates of
    `Pref`, turned by each vertex's blended rotation `R`) where bones blend,
    so the skinning's folds at the joints smooth out."""
    E = edges_of(T)
    target = np.einsum("nij,nj->ni", R, umbrella(Pref, E))
    w = np.clip((1.0 - W.max(axis=1)) * 3.0, 0.0, 1.0)
    w = laplacian_smooth(w[:, None], E, 4, lam=0.5, mu=0.0)[:, 0]
    deg = np.zeros(len(P))
    np.add.at(deg, E[:, 0], 1)
    np.add.at(deg, E[:, 1], 1)
    for _ in range(iters):
        acc = np.zeros_like(P)
        np.add.at(acc, E[:, 0], P[E[:, 1]])
        np.add.at(acc, E[:, 1], P[E[:, 0]])
        new = acc / np.maximum(deg, 1)[:, None] - target
        P = P + 0.5 * w[:, None] * (new - P)
    return P


def unfold(V0: np.ndarray, V: np.ndarray, T: np.ndarray, faces, rounds: int = 12) -> np.ndarray:
    """Local Laplacian passes over the few triangles the wrap folded (the
    crotch, where our much denser layout meets a narrow sculpted groove):
    each round smooths the folded triangles' vertices and two rings
    around them until none is left."""
    from .shape_target import flipped_triangles

    E = edges_of(T)
    V = V.copy()
    for r in range(rounds):
        bad = flipped_triangles(V0, V, faces, min_area=1e-6, min_angle=5.0, local=True)
        if not len(bad):
            break
        w = np.zeros(len(V))
        # flipped_triangles indexes the fan triangles of `faces` (same order as T)
        w[T[bad].ravel()] = 1.0
        for _ in range(2):
            acc = np.zeros(len(V))
            np.maximum.at(acc, E[:, 0], w[E[:, 1]])
            np.maximum.at(acc, E[:, 1], w[E[:, 0]])
            w = np.maximum(w, 0.7 * acc)
        V = laplacian_smooth(V, E, 4 + 2 * r, lam=0.5, mu=0.0, w=w)
        _log(f"unfold round {r}: {len(bad)} folded triangles")
    return V


def similarity(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares similarity (rotation, uniform scale, translation): (M, t)."""
    c0, c1 = src.mean(axis=0), dst.mean(axis=0)
    X, Y = src - c0, dst - c1
    U, Sg, Vt = np.linalg.svd(X.T @ Y)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    s = float((Sg * np.diag(D)).sum() / max((X ** 2).sum(), 1e-12))
    M = s * R
    return M, c1 - M @ c0


# ----------------------------------------------------------------------------
# 6. Joints, measurements, scalp
# ----------------------------------------------------------------------------


def loop_radius(lp: Loop | None) -> float:
    return lp.perimeter / (2 * math.pi) if lp else 0.0


def _head_rays(b: Body, P: np.ndarray):
    sdf = H.head_sdf(H.FaceParams(**({"softness": 1.0} if b.bust > 0 else {})))
    c = K.head_centre(b)
    d = P - c
    r = np.linalg.norm(d, axis=1)
    loc = np.stack([d[:, 0], -d[:, 1], d[:, 2]], axis=1) / r[:, None]
    t = S.raycast(sdf, np.zeros((len(loc), 3)), loc, t_max=30.0, step_scale=0.7, min_step=0.004)
    return d, r, t


def fit_head(b: Body, P: np.ndarray, scalp_ids: np.ndarray) -> tuple[float, float, dict]:
    """head_scale and head_fwd of our head sculpt that best fit the new scalp
    (median radial fit). Hats and hair are cut against the visible head of a
    reshaped body (gear_ctx.register_head_surface); the head sculpt still
    sets their scale (offsets in head centimetres) and the head centre."""
    best = None
    for hf in np.arange(-0.08, 0.081, 0.0025):
        bb = replace(b, head_fwd=float(hf), head_scale=1.0)
        _, r, t = _head_rays(bb, P[scalp_ids])
        ok = t > 1.0
        ratio = r[ok] / (0.01 * t[ok])
        hs = float(np.median(ratio))
        gap = float(np.mean(np.abs(hs * 0.01 * t[ok] - r[ok])))
        if best is None or gap < best[0]:
            best = (gap, hs, float(hf), float(np.max(np.abs(r[ok] - hs * 0.01 * t[ok]))))
    return best[1], best[2], {"mean_gap_mm": round(best[0] * 1000, 1), "max_gap_mm": round(best[3] * 1000, 1)}


def measure_body(b0: Body, V: np.ndarray, T: np.ndarray, rest: dict, lm: dict, info: dict, lm0: dict,
                 ua: float, fa: float) -> Body:
    """The new proportions.Body off the rest-pose mesh. Hands and feet keep
    our shapes, so their sizes scale by how much they grew."""
    m = Mesh(V, T)
    sj, hip, kn, an = rest["sj.L"], rest["hip.L"], rest["kn.L"], rest["an.L"]

    def flat(a, b_):
        return float(np.linalg.norm((a - b_)[:2]))

    foot_k = flat(lm["toe_tip.L"], lm["heel.L"]) / flat(lm0["toe_tip.L"], lm0["heel.L"])
    hand_k = float(np.linalg.norm(lm["fingertip.L"] - lm["wrist.L"]) / np.linalg.norm(lm0["fingertip.L"] - lm0["wrist.L"]))

    def ring_r(z, near):
        return loop_radius(_nearest_loop(m.slice(np.array([0, 0, z]), np.array([0, 0, 1.0])), near))

    def arm_r(p, axis):
        return loop_radius(_nearest_loop(m.slice(p, axis), p))

    ax_u = unit(rest["el.L"] - sj)
    ax_f = unit(rest["wr.L"] - rest["el.L"])
    zc, zw, zh = info["chest_z"], info["waist_z"], info["hip_z"]
    lc, lw, lh = torso_loop(m, zc), torso_loop(m, zw), torso_loop(m, zh)

    def w_fwd_back(lp, d):
        return (float(lp.pts[:, 0].max()), float(-lp.pts[:, 1].min()), float(lp.pts[:, 1].max())) if lp else d

    rib = w_fwd_back(lc, (b0.rib_w, b0.rib_fwd, b0.rib_back))
    wst = w_fwd_back(lw, (b0.waist_w, b0.waist_fwd, b0.waist_back))
    hp = w_fwd_back(lh, (b0.hip_w, b0.hip_fwd, b0.hip_back))
    shoulder_band = V[(np.abs(V[:, 2] - sj[2]) < 0.012) & (np.abs(V[:, 1]) < 0.12)]
    return replace(
        b0,
        eye_z=float(lm["eye.L"][2]),
        neck_r=float(info["neck_girth"] / (2 * math.pi)),
        neck_base_z=float(info["neck_base_z"]),
        shoulder_joint=(float(sj[0]), float(-sj[1]), float(sj[2])),
        deltoid_x=float(max(shoulder_band[:, 0].max() if len(shoulder_band) else 0.0, sj[0] + 0.02)),
        chest_z=float(zc), rib_w=rib[0], rib_fwd=rib[1], rib_back=rib[2],
        waist_z=float(zw), waist_w=wst[0], waist_fwd=wst[1], waist_back=wst[2],
        hip_z=float(zh), hip_w=hp[0], hip_fwd=hp[1], hip_back=hp[2],
        crotch_z=float(lm["crotch"][2]),
        hip_joint=(float(hip[0]), float(-hip[1]), float(hip[2])),
        knee=(float(kn[0]), float(-kn[1]), float(kn[2])),
        # skeleton.foot_points reads Body.ankle's middle value as y (it
        # negates twice), unlike hip_joint and knee (forward values).
        ankle=(float(an[0]), float(an[1]), float(an[2])),
        thigh_r=ring_r(0.5 * (hip[2] + kn[2]), 0.5 * (hip + kn)) or b0.thigh_r,
        knee_r=ring_r(kn[2], kn) or b0.knee_r,
        calf_r=ring_r(float(lm["calf.L"][2]), lm["calf.L"]) or b0.calf_r,
        ankle_r=ring_r(an[2] + 0.02, an) or b0.ankle_r,
        foot_len=b0.foot_len * foot_k, foot_w=b0.foot_w * foot_k,
        upper_arm_len=float(ua), forearm_len=float(fa),
        arm_r=arm_r(sj + ax_u * 0.4 * ua, ax_u) or b0.arm_r,
        elbow_r=arm_r(rest["el.L"], ax_f) or b0.elbow_r,
        forearm_r=arm_r(rest["el.L"] + ax_f * 0.3 * fa, ax_f) or b0.forearm_r,
        wrist_w=b0.wrist_w * hand_k, wrist_t=b0.wrist_t * hand_k,
        hand_len=b0.hand_len * hand_k, palm_w=b0.palm_w * hand_k, palm_t=b0.palm_t * hand_k,
        finger_r=b0.finger_r * hand_k,
    )


# ----------------------------------------------------------------------------
# The whole wrap
# ----------------------------------------------------------------------------


@dataclass
class WrapResult:
    verts: np.ndarray
    body: Body
    meta: dict
    stages: dict = field(default_factory=dict)
    face: dict = field(default_factory=dict)  # the sculpt's face anchors in the final rest pose (shape_target.face_layout)


def our_face(fr, m: Mesh) -> Face:
    """3D anchors of our painted face from the face frame: the left eye
    centre (on the surface) and the nose tip and mouth on the midline."""
    a = fr.eye[0] / fr.R0
    eye = np.array([fr.R0 * math.sin(a), fr.axis_y - fr.R0 * math.cos(a), fr.eye[1]])
    n = m.nearest(eye)
    return Face(n[0] if n else eye, _midline_front(m, fr.nose[1], 2.0), _midline_front(m, fr.mouth[1], 2.0))


def our_hints(V: np.ndarray, T: np.ndarray, Wpart: np.ndarray, elbow: np.ndarray, knee: np.ndarray, H_: float) -> dict:
    """Our own elbow and knee joints, crotch (the lowest torso-weighted vertex
    on the midline) and left-leg triangles (our thighs touch below the
    crotch, so leg slices use the leg's own triangles)."""
    tor = Wpart[:, PARTS.index("torso")]
    sel = (np.abs(V[:, 0]) < 0.012) & (tor > 0.6) & (V[:, 2] > 0.3 * H_) & (V[:, 2] < 0.6 * H_)
    c = V[sel][int(np.argmin(V[sel, 2]))].copy()
    c[0] = 0.0
    leg = Wpart[:, PARTS.index("leg.L")] + Wpart[:, PARTS.index("foot.L")]
    return {"elbow": elbow, "knee": knee, "crotch": c, "leg_tris": leg[T].mean(axis=1) > 0.5}


def carry(points: list[np.ndarray], V0: np.ndarray, V1: np.ndarray, k: int = 6) -> list[np.ndarray]:
    """Points carried by the mean displacement of their nearest vertices."""
    out = []
    for p in points:
        near = np.argsort(np.linalg.norm(V0 - p, axis=1))[:k]
        out.append(p + (V1[near] - V0[near]).mean(axis=0))
    return out


def surface_dist(m: Mesh, P: np.ndarray) -> np.ndarray:
    return np.array([m.nearest(p)[3] for p in P])


def face_flags(faces, regions, name: str, n: int) -> np.ndarray:
    v = np.zeros(n, dtype=bool)
    for f, r in zip(faces, regions):
        if r == name:
            v[list(f)] = True
    return v


def wrap(body_id: str, variant: str, pre: dict, sculpt_dir: str, debug: str = "") -> WrapResult:
    t0 = time.time()
    tag = body_id + (f"_{variant}" if variant else "")
    sc = SCULPTS[(body_id, variant)]
    b0: Body = pre["body"]
    mesh = pre["mesh"]
    H_ = b0.height
    s = H_ / 1.7
    V0 = mesh["verts"].astype(np.float64)
    from .shape_target import triangles

    faces = mesh["faces"]
    T = triangles(faces)
    weights, regions = mesh["weights"], mesh["regions"]
    n = len(V0)
    ours = Mesh(V0, T)
    J = K.joint_map(b0)
    stages: dict = {"prewrap": V0}

    # 1. target
    target, tinfo = prepare_target(sc, os.path.join(sculpt_dir, sc.file), H_)
    lm_t0, _ = landmarks(target, H_)
    sock = socket_box(target, lm_t0["chin"], lm_t0["crown"], float(lm_t0["crown"][2] - lm_t0["chin"][2]))
    target = neutralise(target, lm_t0, H_)

    # 2. our landmarks at rest, the sculpt's, and the pose between them
    face = our_face(pre["frame"], ours)
    vpart, Wpart = vertex_parts(weights)
    hints0 = our_hints(V0, T, Wpart, J["forearm.L"].head, J["shin.L"].head, H_)
    lm_o0, info_o0 = landmarks(ours, H_, face, None, hints0)
    fr = {"frac_elbow": info_o0["frac_elbow"], "frac_knee": info_o0["frac_knee"], "frac_mouth": info_o0["frac_mouth"]}
    lm_t, info_t = landmarks(target, H_, None, fr)
    # The eye landmark is the socket of the sculpt as it was (the neutralised
    # target has the sockets filled), on the neutralised surface.
    c = sock["centre"]
    hit = target.ray(np.array([c[0], -3.0, c[2]]), np.array([0, 1.0, 0]), 6.0)
    lm_t["eye.L"] = np.array(hit[0]) if hit else c
    joints0 = {}
    for sd in ("L", "R"):
        joints0[f"sj.{sd}"] = J[f"upper_arm.{sd}"].head
        joints0[f"el.{sd}"] = J[f"forearm.{sd}"].head
        joints0[f"wr.{sd}"] = J[f"hand.{sd}"].head
        joints0[f"hip.{sd}"] = J[f"thigh.{sd}"].head
        joints0[f"kn.{sd}"] = J[f"shin.{sd}"].head
        joints0[f"an.{sd}"] = J[f"foot.{sd}"].head
    A0, B0 = mirrored(lm_o0), mirrored(lm_t)
    tgt = dict(joints0)
    for sd in ("L", "R"):
        def seg(lmd, a, b_):
            return lmd[f"{b_}.{sd}"] - lmd[f"{a}.{sd}"]

        R_ua = min_rotation(seg(A0, "arm_root", "elbow"), seg(B0, "arm_root", "elbow"))
        R_fa = min_rotation(seg(A0, "elbow", "wrist"), seg(B0, "elbow", "wrist"))
        R_th = min_rotation(seg(A0, "thigh_top", "knee"), seg(B0, "thigh_top", "knee"))
        R_sh = min_rotation(seg(A0, "knee", "ankle"), seg(B0, "knee", "ankle"))
        sj, el, wr = joints0[f"sj.{sd}"], joints0[f"el.{sd}"], joints0[f"wr.{sd}"]
        tgt[f"el.{sd}"] = sj + R_ua @ (el - sj)
        tgt[f"wr.{sd}"] = tgt[f"el.{sd}"] + R_fa @ (wr - el)
        hp, kn, an = joints0[f"hip.{sd}"], joints0[f"kn.{sd}"], joints0[f"an.{sd}"]
        tgt[f"kn.{sd}"] = hp + R_th @ (kn - hp)
        tgt[f"an.{sd}"] = tgt[f"kn.{sd}"] + R_sh @ (an - kn)
    xf = chain_xf(joints0, tgt)
    Wp = pose_weights(weights)
    V1 = dqs(V0, Wp, xf)
    stages["posed"] = V1
    posed = Mesh(V1, T)
    hints1 = dict(hints0, elbow=tgt["el.L"], knee=tgt["kn.L"])
    lm_o, _ = landmarks(posed, H_, face, fr, hints1)
    A, B = mirrored(lm_o), mirrored(lm_t)
    names = sorted(set(A) & set(B))
    src = np.array([A[k] for k in names])
    dst = np.array([B[k] for k in names])
    _log(f"{tag}: {len(names)} landmarks, mean distance {np.linalg.norm(src - dst, axis=1).mean() * 1000:.0f} mm "
         f"({time.time() - t0:.0f}s)")

    # 4. thin-plate spline
    tps = TPS(src, dst)
    V2 = tps(V1)
    stages["tps"] = V2

    # Hands, feet and ears keep our shapes: a similarity of the warp.
    pidx = {p: i for i, p in enumerate(PARTS)}
    V2s = V2.copy()
    keep_w = np.zeros(n)
    for sd in ("L", "R"):
        for part in ("hand", "foot"):
            w = Wpart[:, pidx[f"{part}.{sd}"]]
            ids = np.nonzero(w > 0.01)[0]
            core = np.nonzero(w > 0.9)[0]
            M, t = similarity(V1[core], V2[core])
            V2s[ids] = (1 - w[ids, None]) * V2[ids] + w[ids, None] * (V1[ids] @ M.T + t)
            keep_w = np.maximum(keep_w, w)
    ear_v = face_flags(faces, regions, "ears", n)
    for sg in (1, -1):
        ids = np.nonzero(ear_v & (V0[:, 0] * sg > 0))[0]
        if len(ids):
            M, t = similarity(V1[ids], V2[ids])
            V2s[ids] = V1[ids] @ M.T + t
    keep_w[ear_v] = 1.0

    # 5. project
    pt = PartTargets(target, V2s, vpart)
    nbp: list[set] = [{int(vpart[i])} for i in range(n)]
    for a, b_, c in T:
        for x, y in ((a, b_), (b_, c), (c, a)):
            nbp[x].add(int(vpart[y]))
            nbp[y].add(int(vpart[x]))
    allowed = [frozenset(x) for x in nbp]
    proj_w = np.clip(1.0 - keep_w, 0.0, 1.0)
    head_v = face_flags(faces, regions, "head", n)
    relax_w = np.where(head_v, 0.15, 1.0) * proj_w
    # Crotch, armpits and gluteal cleft: even out (our layout folds there).
    def near(p, r):
        return np.clip(1.5 - np.linalg.norm(V2s - p, axis=1) / (r * s), 0.0, 1.0)

    unt = near(B["crotch"], 0.07)
    for sd in ("L", "R"):
        unt = np.maximum(unt, near(B[f"armpit.{sd}"], 0.06))
    cz, cy = float(B["crotch"][2]), float(B["crotch"][1])
    cleft = (np.abs(V2s[:, 0]) < 0.035 * s) & (V2s[:, 1] > cy) & (V2s[:, 2] > cz) & (V2s[:, 2] < cz + 0.12 * s)
    unt = np.maximum(unt, cleft.astype(float))
    unt = laplacian_smooth(unt[:, None], edges_of(T), 2, lam=0.5, mu=0.0)[:, 0]
    V3 = project(V2s, T, pt, allowed, proj_w, relax_w, head_v, unt)
    head_ids = np.nonzero(head_v & ~ear_v)[0]
    for sg in (1, -1):
        ids = np.nonzero(ear_v & (V0[:, 0] * sg > 0))[0]
        if len(ids):
            root = V2s[ids].mean(axis=0)
            near = head_ids[np.argsort(np.linalg.norm(V2s[head_ids] - root, axis=1))[:10]]
            V3[ids] += (V3[near] - V2s[near]).mean(axis=0)
    stages["projected"] = V3
    dist = surface_dist(target, V3[proj_w > 0.99])
    _log(f"{tag}: projected, target distance mean {dist.mean() * 1000:.2f} mm, max {dist.max() * 1000:.1f} mm "
         f"({time.time() - t0:.0f}s)")

    # 6. joints, pose back, measurements. Shoulder and hip joints (inside the
    # trunk) follow the warp; elbow, wrist, knee and ankle keep their place
    # in the limb's cross-section (centroid plus our own offset, scaled).
    jm = {}
    for sd in ("L", "R"):
        for name in ("sj", "hip"):
            jm[f"{name}.{sd}"] = tps(tgt[f"{name}.{sd}"][None])[0]
    tri_part = np.argmax(Wpart[T].sum(axis=1), axis=1)
    m1, m3 = Mesh(V1, T), Mesh(V3, T)
    for sd in ("L", "R"):
        chains = (("sj", "el", "wr", ("arm", "hand")), ("hip", "kn", "an", ("leg", "foot")))
        for root, mid, end, parts in chains:
            mask = np.isin(tri_part, [PARTS.index(f"{p}.{sd}") for p in parts])
            for jn, prev, nxt in ((mid, root, end), (end, mid, None)):
                p0 = tgt[f"{jn}.{sd}"]
                a0 = unit(p0 - tgt[f"{prev}.{sd}"]) if nxt is None else unit(tgt[f"{nxt}.{sd}"] - tgt[f"{prev}.{sd}"])
                q = tps(p0[None])[0]
                qp = tps(tgt[f"{prev}.{sd}"][None])[0]
                a1 = unit(q - qp) if nxt is None else unit(tps(tgt[f"{nxt}.{sd}"][None])[0] - qp)
                l0 = _nearest_loop(m1.slice(p0, a0, mask), p0)
                l1 = _nearest_loop(m3.slice(q, a1, mask), q)
                if l0 is None or l1 is None:
                    jm[f"{jn}.{sd}"] = q
                    continue
                k = loop_radius(l1) / max(loop_radius(l0), 1e-6)
                jm[f"{jn}.{sd}"] = l1.centroid + min_rotation(a0, a1) @ (p0 - l0.centroid) * k
    for k in [k for k in jm if k.endswith(".L")]:
        r = k[:-2] + ".R"
        avg = 0.5 * (jm[k] + jm[r] * np.array([-1.0, 1.0, 1.0]))
        jm[k], jm[r] = avg, avg * np.array([-1.0, 1.0, 1.0])

    def mean_len(a, b_):
        return 0.5 * sum(float(np.linalg.norm(jm[f"{b_}.{x}"] - jm[f"{a}.{x}"])) for x in "LR")

    ua, fa, th, sh = mean_len("sj", "el"), mean_len("el", "wr"), mean_len("hip", "kn"), mean_len("kn", "an")
    rest = dict(jm)
    for sd in ("L", "R"):
        def u(a, b_):
            return unit(joints0[f"{b_}.{sd}"] - joints0[f"{a}.{sd}"])

        rest[f"el.{sd}"] = jm[f"sj.{sd}"] + u("sj", "el") * ua
        rest[f"wr.{sd}"] = rest[f"el.{sd}"] + u("el", "wr") * fa
        rest[f"kn.{sd}"] = jm[f"hip.{sd}"] + u("hip", "kn") * th
        rest[f"an.{sd}"] = rest[f"kn.{sd}"] + u("kn", "an") * sh
    xb = chain_xf(jm, rest)
    V4, Rv = dqs(V3, Wp, xb, rotations=True)
    V4 = relax_pose(V4, V3, T, Wp, Rv)
    zmin = float(V4[:, 2].min())
    k = H_ / (float(V4[:, 2].max()) - zmin)
    V4 = (V4 - np.array([0.0, 0.0, zmin])) * k
    rest = {key: (p - np.array([0.0, 0.0, zmin])) * k for key, p in rest.items()}
    V4 = unfold(V0, V4, T, faces)
    stages["rest"] = V4.copy()
    m4 = Mesh(V4, T)
    face4 = Face(*carry([face.eye, face.nose, face.mouth], V0, V4))
    hints4 = dict(hints0, elbow=rest["el.L"], knee=rest["kn.L"], crotch=carry([hints0["crotch"]], V0, V4)[0])
    lm_r, info_r = landmarks(m4, H_, face4, fr, hints4)
    b1 = measure_body(b0, V4, T, rest, lm_r, info_r, lm_o0, ua * k, fa * k)
    scalp_ids = np.nonzero(face_flags(faces, regions, "scalp", n))[0]
    hs, hf, head_fit = fit_head(b1, V4, scalp_ids)
    b1 = replace(b1, head_scale=hs, head_fwd=hf)
    stages["final"] = V4
    names_f = ("centre", "inner", "outer", "lower", "upper")
    pts = carry([sock[k] for k in names_f] + [lm_t["nose"], lm_t["mouth"], lm_t["chin"], lm_t["crown"]], V3, V4)
    face_out = {"face_eye": pts[0], "face_box": np.array(pts[1:5]), "face_nose": pts[5], "face_mouth": pts[6],
                "face_chin": pts[7], "face_crown": np.array([0.0, float(pts[8][1]), float(V4[:, 2].max())])}
    for kk in ("face_nose", "face_mouth", "face_chin"):
        face_out[kk] = np.array([0.0, face_out[kk][1], face_out[kk][2]])
    heads = H_ / (H_ - float(lm_r["chin"][2]))
    from .shape_target import wrap_quality

    quality = wrap_quality(V0, V4, faces, regions)
    heads_sculpt = H_ / (H_ - float(lm_t["chin"][2]))
    meta = {
        "source": sc.title, "author": sc.author, "url": sc.url, "licence": sc.licence,
        "adapted": "retopologised onto our base mesh, posed, smoothed",
        "body": body_id, "variant": variant, "landmarks": len(names),
        "heads_tall": round(heads, 2), "heads_tall_sculpt": round(heads_sculpt, 2), "head_fit": head_fit,
        "quality": quality,
        "target_distance_mm": {"mean": round(float(dist.mean()) * 1000, 2), "max": round(float(dist.max()) * 1000, 1)},
        "target": tinfo, "seconds": round(time.time() - t0, 1),
        "lengths_m": {"upper_arm": round(b1.upper_arm_len, 3), "forearm": round(b1.forearm_len, 3),
                      "thigh": round(th * k, 3), "shin": round(sh * k, 3), "hand": round(b1.hand_len, 3),
                      "foot": round(b1.foot_len, 3)},
    }
    _log(f"{tag}: done, {heads:.2f} heads tall (sculpt {heads_sculpt:.2f}); quality {quality}; head fit {head_fit} ({time.time() - t0:.0f}s)")
    if debug:
        os.makedirs(debug, exist_ok=True)
        np.savez_compressed(os.path.join(debug, "stages.npz"), T=T, target_V=target.V, target_T=target.T,
                            lm_names=np.array(names), lm_t=dst, lm_o=src, regions=np.array(regions), **stages)
    meta["socket_cm"] = {"w": round(float(sock["outer"][0] - sock["inner"][0]) * 100, 2),
                         "h": round(float(sock["upper"][2] - sock["lower"][2]) * 100, 2),
                         "depth_mm": round(sock["depth"] * 1000, 1)}
    return WrapResult(V4, b1, meta, stages, face_out)
