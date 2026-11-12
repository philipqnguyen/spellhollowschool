"""The sitting drape (the clothing pass, 2026-11-02; docs/architecture.md).

Seated, skirts and robes ballooned out in front: their hanging rings are
skinned partly to the hips and partly to the thighs, so with the thighs
level the front panels stood out between the two, and the hem's flare
(made for standing) turned into a hoop ahead of the knees. A garment with a
hanging skirt (`FittedShape.hanging`) now carries one corrective shape key
per seated pose (`KEYS`), baked here and exported as a glTF morph target;
the client turns it on with the seated clips and off otherwise (assemble.ts,
the toon material's `toonDrape`).

Baking, per key: the skirt is posed by its own weights in the clip's frame,
then settled like a hanging cloth (a small position-based relaxation):
gravity pulls every ring vertex down, each column keeps its rest edge
lengths (cloth does not stretch along its fall), a ring edge may shorten but
stretch only `RING_STRETCH`, and no vertex goes inside the posed legs (plus
`CLEAR_M`) or below the seat and floor. The skirt then lies on the lap, falls
over the knees and hangs down the shins. Each settled vertex is taken back
through its own skinning (the inverse of its blended bone matrix) to a rest
position; the key is that minus the rest position. Every vertex of every
LOD takes the key of the skirt's nearest ring vertices at rest (inverse
distance, `FIELD_M`), faded out near the skin (the tights inside a skirt
and the trunk never move).
"""

from __future__ import annotations

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

# key name -> (clip, frame, seat): "chair" has a seat under the thighs ending
# behind the knees and the floor at the feet, "floor" the floor under all.
# The floor sit is cross-legged; the floor rest (sitting where no seat is)
# has the knees up, so it has a key of its own.
KEYS = {"drape_chair": ("sit", 45, "chair"), "drape_floor": ("floor_sit", 90, "floor"), "drape_rest": ("floor_rest", 45, "floor")}
# The cloth's distance from the posed legs (reference metres), per slot: a
# robe or coat settles outside the trousers or skirt worn under it (straight
# trouser legs stand up to 45 mm off the shin), a skirt over its tights.
CLEAR_M = {"legs": 0.010, "body": 0.030}
RING_STRETCH = 1.08
GRAVITY_M = 0.004
ITERATIONS = 90
FRICTION_UP = 0.35  # a contact normal's z above this holds the cloth (the lap, the seat)
FIELD_M = 0.03
SKIN_FADE = (0.004, 0.010)  # vertices this close to the skin keep still, from the second on they move fully
LEGS = ("hips", "thighs", "calves", "feet")


def _pose_matrices(skel, names) -> dict[str, Matrix]:
    out = {}
    for n in names:
        pb = skel.pose.bones.get(n)
        if pb is not None:
            out[n] = pb.matrix @ pb.bone.matrix_local.inverted()
    return out


def _blend(weights: dict[str, float], mats: dict[str, Matrix]) -> np.ndarray:
    A = np.zeros((4, 4))
    tot = 0.0
    for n, w in weights.items():
        m = mats.get(n)
        if m is None:
            continue
        A += w * np.array(m)
        tot += w
    if tot <= 0:
        return np.eye(4)
    return A / tot


def _posed_legs(bb) -> tuple[BVHTree, float, np.ndarray]:
    dg = bpy.context.evaluated_depsgraph_get()
    V, T = [], []
    zmin = 9.0
    for r, o in bb.meshes[0].items():
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        pts = [mw @ v.co for v in me.vertices]
        zmin = min(zmin, min(p.z for p in pts))
        if r in LEGS:
            off = len(V)
            V += [tuple(p) for p in pts]
            me.calc_loop_triangles()
            T += [tuple(off + i for i in t.vertices) for t in me.loop_triangles]
        ev.to_mesh_clear()
    pb = bb.skel.pose.bones
    knees = np.array([np.array(bb.skel.matrix_world @ pb[f"DEF-shin.{s}"].head) for s in ("L", "R")])
    return BVHTree.FromPolygons(V, T, all_triangles=True), zmin, knees


LEG_BONES = [(f"DEF-thigh.{s}", f"DEF-thigh.{s}.001") for s in ("L", "R")] + [(f"DEF-shin.{s}", f"DEF-shin.{s}.001") for s in ("L", "R")]


class LegSides:
    """Which side of each leg bone a skirt vertex lies on, kept from rest
    into the pose: the front panel lies in front of the thighs standing, so
    it lies on top of them seated (the pose's thigh rotation applied to its
    rest direction from the bone), and is pushed out of the legs that way,
    never out through their underside."""

    def __init__(self, skel, rest_pts: np.ndarray):
        bones = skel.data.bones
        self.rest = [(np.array(bones[a].head_local), np.array(bones[b].tail_local)) for a, b in LEG_BONES]
        pts = rest_pts.reshape(-1, 3)
        self.u = np.zeros((len(pts), len(LEG_BONES), 3))
        self.t = np.zeros((len(pts), len(LEG_BONES)))
        for j, (a, b) in enumerate(self.rest):
            d = b - a
            t = np.clip(((pts - a) @ d) / max(float(d @ d), 1e-12), 0.0, 1.0)
            q = a + t[:, None] * d
            u = pts - q
            self.u[:, j] = u / np.maximum(np.linalg.norm(u, axis=1), 1e-9)[:, None]
            self.t[:, j] = t
        self.shape = rest_pts.shape[:2]

    def pose(self, skel) -> None:
        pb = skel.pose.bones
        mw = np.array(skel.matrix_world)
        self.seg, self.rot = [], []
        for a, b in LEG_BONES:
            m = mw @ np.array(pb[a].matrix @ pb[a].bone.matrix_local.inverted())
            self.seg.append((np.array(skel.matrix_world @ pb[a].head), np.array(skel.matrix_world @ pb[b].tail)))
            self.rot.append(m[:3, :3])

    def out(self, p: np.ndarray, idx: int, tree: BVHTree, clear: float) -> np.ndarray:
        best, jb, qb = 9.0, -1, None
        for j, (a, b) in enumerate(self.seg):
            d = b - a
            t = float(np.clip((p - a) @ d / max(float(d @ d), 1e-12), 0.0, 1.0))
            q = a + d * t
            r = float(np.linalg.norm(p - q))
            if r < best:
                best, jb, qb = r, j, q
        if best > 0.25:
            return p
        u = self.rot[jb] @ self.u[idx, jb]
        u /= max(float(np.linalg.norm(u)), 1e-9)
        hit = tree.ray_cast(Vector(qb), Vector(u), 0.3)
        if hit[0] is None:
            return p
        reach = hit[3] + clear
        h = float((p - qb) @ u)
        return p + u * (reach - h) if h < reach else p


def settle(X0: np.ndarray, rest: np.ndarray, fixed: np.ndarray, closed: bool, tree: BVHTree, support, clear: float,
           sides: LegSides | None = None) -> np.ndarray:
    """Position-based settling of a hanging skirt grid (rings x columns) from
    its posed state `X0`; `rest` gives the edge lengths, row 0 is held."""
    X = X0.copy()
    nr, nc = X.shape[:2]
    Lc = np.linalg.norm(rest[1:] - rest[:-1], axis=2)
    cols_next = np.arange(nc) + 1
    if closed:
        cols_next %= nc
    m = nc if closed else nc - 1
    Lr = np.linalg.norm(rest[:, cols_next[:m]] - rest[:, :m], axis=2)
    free = ~fixed
    down = np.array([0.0, 0.0, -GRAVITY_M])
    # Friction: cloth resting on a surface facing up (the lap) stays put.
    resting = np.zeros(X.shape[:2], dtype=bool)
    for _ in range(ITERATIONS):
        X[free & ~resting] += down
        for _ in range(2):
            # Columns: rest lengths, top down (the vertex above is held when it is fixed).
            for k in range(1, nr):
                d = X[k] - X[k - 1]
                L = np.linalg.norm(d, axis=1)
                corr = (d * ((L - Lc[k - 1]) / np.maximum(L, 1e-9))[:, None])
                top_fixed = fixed[k - 1]
                X[k] -= np.where(top_fixed[:, None], corr, 0.5 * corr)
                X[k - 1] += np.where(top_fixed[:, None], 0.0, 0.5 * corr)
            # Rings: may gather, stretch only a little.
            for k in range(1, nr):
                a, b = X[k, :m], X[k, cols_next[:m]]
                d = b - a
                L = np.linalg.norm(d, axis=1)
                over = np.maximum(L - Lr[k] * RING_STRETCH, 0.0)
                c = d * (0.5 * over / np.maximum(L, 1e-9))[:, None]
                np.add.at(X[k], np.arange(m), c)
                np.add.at(X[k], cols_next[:m], -c)
        # Collisions: the posed legs, then the seat or the floor.
        for k in range(1, nr):
            for c in range(nc):
                p = sides.out(X[k, c], k * nc + c, tree, clear) if sides else X[k, c]
                X[k, c] = p
                loc, nrm, _, dist = tree.find_nearest(Vector(p), 0.25)
                if loc is not None:
                    q, n = np.array(loc), np.array(nrm)
                    h = float((p - q) @ n)
                    if h < clear:
                        X[k, c] = p + n * (clear - h)
                    resting[k, c] = h < 1.5 * clear and n[2] > FRICTION_UP
                z = support(X[k, c]) + 0.5 * clear
                if X[k, c][2] < z:
                    X[k, c][2] = z
                    resting[k, c] = True
    return X


def bake(fs, meshes: dict, bb, slot: str = "legs") -> dict:
    """Adds the KEYS shape keys to every object in `meshes` (lod -> {name:
    object}) of a garment with a hanging skirt. Returns stats."""
    from . import layers as LY

    if not fs.hanging or len(fs.hanging) < 2:
        return {}
    g, fr = fs.g, fs.g.frame
    skel = bb.skel
    act_saved = skel.animation_data.action if skel.animation_data else None
    rings = np.array(fs.hanging, dtype=np.int64)  # (rings, columns)
    closed = fs.loops.get("skirt_hem").closed if fs.loops.get("skirt_hem") else True
    rest = g.positions("base")[rings]
    fixed = np.zeros(rings.shape, dtype=bool)
    fixed[0] = True
    names = sorted({n for v in rings.ravel() for n in g.weights[int(v)]})
    objs = [o for parts in meshes.values() for o in parts.values()]
    stats = {}
    sides = LegSides(skel, rest)
    ad = skel.animation_data
    mutes = [t.mute for t in ad.nla_tracks] if ad else []
    frame0 = bpy.context.scene.frame_current
    for t in (ad.nla_tracks if ad else []):
        t.mute = True
    deltas = {}
    for key, (clip, frame, seat) in KEYS.items():
        if not LY._set_frame(skel, clip, frame, []):
            continue
        mats = _pose_matrices(skel, names)
        A = np.array([[_blend(g.weights[int(v)], mats) for v in row] for row in rings])  # (r, c, 4, 4)
        hom = np.concatenate([rest, np.ones(rest.shape[:2] + (1,))], axis=2)
        X0 = np.einsum("rcij,rcj->rci", A, hom)[..., :3]
        tree, zmin, knees = _posed_legs(bb)
        clear = CLEAR_M.get(slot, CLEAR_M["legs"]) * fr.sc
        if seat == "chair":
            seat_z = float(skel.matrix_world.translation.z)  # the clip's root is the seat's top
            front_y = float(knees[:, 1].mean()) + 0.06 * fr.sc

            def support(p, seat_z=seat_z, front_y=front_y, zmin=zmin):
                return seat_z if p[1] > front_y else zmin
        else:
            def support(p, zmin=zmin):
                return zmin
        sides.pose(skel)
        nc = X0.shape[1]
        X0 = np.array([[sides.out(p, k * nc + c, tree, clear) for c, p in enumerate(row)] for k, row in enumerate(X0)])
        X = settle(X0, rest, fixed, closed, tree, support, clear, sides)
        stats.setdefault("seat", {})[key] = [round(float(v), 3) for v in (zmin, knees[:, 1].mean(), skel.pose.bones["root"].head.z if "root" in skel.pose.bones else 0.0)]
        # Back through each vertex's skinning to rest positions.
        inv = np.linalg.inv(A)
        hx = np.concatenate([X, np.ones(X.shape[:2] + (1,))], axis=2)
        R = np.einsum("rcij,rcj->rci", inv, hx)[..., :3]
        deltas[key] = (R - rest).reshape(-1, 3)
        stats[key] = {"moved_max_m": round(float(np.linalg.norm(X - X0, axis=2).max()), 3),
                      "drop_m": round(float((X0[..., 2] - X[..., 2]).max()), 3),
                      "forward_cut_m": round(float(np.max(X[-1, :, 1]) - np.max(X0[-1, :, 1])), 3)}
    LY._set_frame(skel, None, 0, [])
    if ad:
        ad.action = act_saved
        for t, m in zip(ad.nla_tracks, mutes):
            t.mute = m
    bpy.context.scene.frame_set(frame0)
    if not deltas:
        return {}
    # The field: every vertex takes its nearest ring vertices' keys.
    pts = rest.reshape(-1, 3)
    kd = KDTree(len(pts))
    for i, p in enumerate(pts):
        kd.insert(p, i)
    kd.balance()
    radius = FIELD_M * fr.sc
    for o in objs:
        me = o.data
        n = len(me.vertices)
        co = np.empty(n * 3)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        mw = np.array(o.matrix_world)
        wco = co @ mw[:3, :3].T + mw[:3, 3]
        field = {k: np.zeros((n, 3)) for k in deltas}
        for i, p in enumerate(wco):
            near = kd.find_n(p, 4)
            near = [(j, d) for _, j, d in near if d < radius]
            if not near:
                continue
            loc = fr.nearest(p, LEGS + ("torso",))
            h = float(np.linalg.norm(fr.point(loc, off=0.0) - p))
            fade = float(np.clip((h - SKIN_FADE[0]) / (SKIN_FADE[1] - SKIN_FADE[0]), 0.0, 1.0))
            if fade <= 0:
                continue
            w = np.array([1.0 / max(d, 1e-4) for _, d in near])
            w /= w.sum()
            for k, dl in deltas.items():
                field[k][i] = fade * (w[:, None] * dl[[j for j, _ in near]]).sum(axis=0)
        if not any(np.abs(f).max() > 1e-5 for f in field.values()):
            continue
        if me.shape_keys is None:
            o.shape_key_add(name="Basis", from_mix=False)
        inv3 = np.linalg.inv(mw[:3, :3])
        for k, f in field.items():
            kb = o.shape_key_add(name=k, from_mix=False)
            kb.data.foreach_set("co", (co + f @ inv3.T).ravel())
            kb.value = 0.0
    return stats
