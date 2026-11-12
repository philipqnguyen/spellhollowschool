"""Hidden covered geometry: what a garment covers, and where each garment
vertex lies over the body (the clothing pass, 2026-11-02; docs/architecture.md).

A character wears at most one item per slot, layered shoes < legs < body
(the client's `assemble.ts`, `LAYER_OF_SLOT`). A garment's faces that a
higher layer covers in every pose are never seen, but they can still break
through it (a skirt's waistband through the back of a shirt as the hips
twist in the run, trousers through a robe's panels). So every garment file
carries:

* per vertex, the body vertex it lies over (`_BODYV`: the nearest LOD0 body
  vertex, numbered over the body's LOD0 region objects in region-name order)
  and its height over the skin there (`_BODYH`, metres along the skin's
  normal), on every LOD;
* per file (`extras.covers`, base64), for every body vertex, how far out the
  garment covers it: the distance along the skin's normal to the garment's
  outermost surface, in 2 mm steps (1..255), or 0 where it does not cover it.
  A body vertex is covered when every ray of a cone round its normal
  (`CONE_DEG`) meets the garment within `REACH_M`, at rest and in each
  `POSES` frame, and so is every body vertex within `MARGIN_M` of it.

The client drops a garment's triangle when all three of its vertices lie over
body vertices that a garment of a higher layer covers, no further out than
`TOLERANCE_SCALE` times that garment's cover plus `TOLERANCE_M`. Any outfit works: nothing is
precomputed per pair, each file only describes itself against its body.
"""

from __future__ import annotations

import base64
import math

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

# Body regions a garment can cover (the head, hands and feet stay out: hats
# and shoes cover nothing a higher layer needs hidden).
REGIONS = ("neck", "torso", "upper_arms", "forearms", "hips", "thighs", "calves")
CONE_DEG = (0.0, 30.0)  # ray tilts from the skin's normal (6 azimuths each past the first)
CONE_AZIMUTHS = 6
REACH_M = 0.4
# The cone's rays start this far under the skin: where the skin passes a
# little way through the garment in a pose (the seat through an untucked
# blouse's back in the run) the garment still covers that body vertex, and a
# lower layer lying there, further out still, must stay hidden (the pleated
# skirt's yoke showed through the silk blouse's back, 2026-11-05).
BELOW_M = 0.015
# 15 mm (was 25): the 25 mm margin with the cone's loss near an outer hem left
# the lower layer drawn over the last 6-10 cm above an untucked top's hem
# (the pleated skirt's yoke through the silk blouse's back, 2026-11-05).
MARGIN_M = 0.015
STEP_M = 0.002  # cover heights in the file
# The client's: a vertex up to TOLERANCE_SCALE times the cover's height plus
# TOLERANCE_M out still counts as under it (a skin-tight cover, the tights,
# never hides a boot shaft worn over it; a robe hides a skirt flaring to it).
TOLERANCE_SCALE = 1.25
TOLERANCE_M = 0.002
# Frames the cover must hold in (clip, frame): the walk and run in four
# phases, idle, the chair and floor sits and the broom ride.
POSES = (("walk", 0), ("walk", 6), ("walk", 12), ("walk", 18), ("run", 0), ("run", 5), ("run", 10), ("run", 15),
         ("idle", 60), ("sit", 45), ("floor_sit", 90), ("floor_rest", 45), ("broom_ride", 30))
# Seated clips: rays leaving the skin downward (into the seat, the floor
# or the lap's underside) count as blocked; the seat hides that side.
SEATED = ("sit", "floor_sit", "floor_rest")
SEAT_DOWN = -0.3  # a ray's z component below this goes into the seat
# Standing too, rays that steep downward are left out: they look up under an
# outer hem, and the game's camera never looks up at a character from below
# by more than a few degrees (client/src/game/camera.ts `upperBetaLimit`), so
# a lower layer just above an untucked top's hem may be hidden.
LOOK_UP_UNSEEN = True
# Clips whose drape key (charlib/drape.py) is on in their frames.
DRAPE_FOR = {"sit": "drape_chair", "floor_sit": "drape_floor", "floor_rest": "drape_rest"}

# Slots whose vertices carry `_BODYV` / `_BODYH` (a higher layer may hide
# them), and the slots that cover a lower one (shoes < legs < body).
ANCHORED_SLOTS = ("shoes", "legs", "body")
COVERING_SLOTS = ("legs", "body")

_BODY: dict = {}


class Body:
    """The body's LOD0 vertices, numbered in region-name order."""

    def __init__(self, bb):
        self.objs = [bb.meshes[0][r] for r in sorted(bb.meshes[0])]
        P, N, reg, tris = [], [], [], []
        off = 0
        for o in self.objs:
            me = o.data
            mw = o.matrix_world
            P += [tuple(mw @ v.co) for v in me.vertices]
            N += [tuple((mw.to_3x3() @ v.normal).normalized()) for v in me.vertices]
            reg += [str(o["region"])] * len(me.vertices)
            me.calc_loop_triangles()
            tris += [tuple(off + i for i in t.vertices) for t in me.loop_triangles]
            off += len(me.vertices)
        self.P = np.array(P)
        self.N = np.array(N)
        self.sign = np.ones(len(P))
        self.region = np.array(reg)
        self.n = len(P)
        self.kd = KDTree(self.n)
        for i, p in enumerate(P):
            self.kd.insert(p, i)
        self.kd.balance()
        # Normals point away from the skeleton (some MakeHuman parts are wound inward).
        self._orient(bb)
        self.cand = np.isin(self.region, REGIONS)
        self.posed: dict = {}

    def _orient(self, bb) -> None:
        from . import fit as F

        joints = F.Joints.from_armature(bb.skel)
        for r in set(self.region.tolist()):
            ids = np.nonzero(self.region == r)[0]
            votes, n = 0, 0
            for i in ids[:: max(1, len(ids) // 200)]:
                near = None
                best = 1e9
                for name, (h, t) in joints.bones.items():
                    if not name.startswith("DEF-"):
                        continue
                    d = t - h
                    s = float(np.clip((self.P[i] - h) @ d / max(d @ d, 1e-12), 0, 1))
                    q = h + d * s
                    dd = float(np.linalg.norm(self.P[i] - q))
                    if dd < best:
                        best, near = dd, q
                votes += float(self.N[i] @ (self.P[i] - near)) < 0
                n += 1
            if votes * 2 > n:
                self.N[ids] *= -1.0
                self.sign[ids] = -1.0

    def pose_arrays(self, key) -> tuple[np.ndarray, np.ndarray]:
        """Posed positions and normals of the numbered vertices (the scene's current frame)."""
        if key in self.posed:
            return self.posed[key]
        dg = bpy.context.evaluated_depsgraph_get()
        P, N = [], []
        for o in self.objs:
            ev = o.evaluated_get(dg)
            me = ev.to_mesh()
            mw = ev.matrix_world
            P += [tuple(mw @ v.co) for v in me.vertices]
            N += [tuple((mw.to_3x3() @ v.normal).normalized()) for v in me.vertices]
            ev.to_mesh_clear()
        N = np.array(N) * self.sign[:, None]
        self.posed[key] = (np.array(P), N)
        return self.posed[key]


def body_of(bb) -> Body:
    b = _BODY.get(id(bb.skel))
    if b is None:
        b = _BODY[id(bb.skel)] = Body(bb)
    return b


def anchor(objs, bb) -> None:
    """Writes `_BODYV` and `_BODYH` on every object (any LOD)."""
    body = body_of(bb)
    for o in objs:
        me = o.data
        mw = o.matrix_world
        n = len(me.vertices)
        ids = np.zeros(n, dtype=np.float32)
        hs = np.zeros(n, dtype=np.float32)
        for i, v in enumerate(me.vertices):
            p = mw @ v.co
            _, j, _ = body.kd.find(p)
            ids[i] = j
            hs[i] = max(0.0, float((np.array(p) - body.P[j]) @ body.N[j]))
        for name, vals in (("_BODYV", ids), ("_BODYH", hs)):
            a = me.attributes.get(name) or me.attributes.new(name, "FLOAT", "POINT")
            a.data.foreach_set("value", vals)


def _cone(n: np.ndarray) -> list[np.ndarray]:
    t = np.cross(n, [0.0, 0.0, 1.0])
    if np.linalg.norm(t) < 1e-3:
        t = np.cross(n, [1.0, 0.0, 0.0])
    t /= np.linalg.norm(t)
    b = np.cross(n, t)
    out = []
    for tilt in CONE_DEG:
        a = math.radians(tilt)
        for k in range(CONE_AZIMUTHS if tilt else 1):
            ph = 2 * math.pi * k / CONE_AZIMUTHS
            out.append(n * math.cos(a) + (t * math.cos(ph) + b * math.sin(ph)) * math.sin(a))
    return out


def _tree(objs) -> BVHTree:
    dg = bpy.context.evaluated_depsgraph_get()
    V, T = [], []
    for o in objs:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        off = len(V)
        V += [tuple(mw @ v.co) for v in me.vertices]
        me.calc_loop_triangles()
        T += [tuple(off + i for i in t.vertices) for t in me.loop_triangles]
        ev.to_mesh_clear()
    return BVHTree.FromPolygons(V, T, all_triangles=True)


def _set_frame(skel, clip: str | None, frame: int, objs) -> bool:
    ad = skel.animation_data
    if clip is None:
        if ad:
            ad.action = None
        for pb in skel.pose.bones:
            pb.matrix_basis.identity()
        key = None
    else:
        act = bpy.data.actions.get(clip)
        if act is None or ad is None:
            return False
        ad.action = act
        if hasattr(ad, "action_slot") and act.slots:
            ad.action_slot = act.slots[0]
        key = DRAPE_FOR.get(clip)
    for o in objs:
        ks = o.data.shape_keys
        if ks:
            for kb in ks.key_blocks[1:]:
                kb.value = 1.0 if kb.name == key else 0.0
    bpy.context.scene.frame_set(int(frame))
    return True


def covers(objs, bb, poses=POSES) -> tuple[np.ndarray, dict]:
    """Cover heights (metres, 0 = not covered) per numbered body vertex of the
    garment made of `objs` (its LOD0 objects, bound to `bb.skel`)."""
    body = body_of(bb)
    skel = bb.skel
    ad = skel.animation_data
    saved = None
    if ad is not None:
        saved = (ad.action, [t.mute for t in ad.nla_tracks], bpy.context.scene.frame_current)
        for t in ad.nla_tracks:
            t.mute = True
    # Candidates: body vertices near the garment at rest.
    _set_frame(skel, None, 0, objs)
    tree = _tree(objs)
    cand = []
    for i in np.nonzero(body.cand)[0]:
        loc, _, _, d = tree.find_nearest(Vector(body.P[i]), 0.25)
        if loc is not None:
            cand.append(int(i))
    cand = np.array(cand, dtype=np.int64)
    alive = np.ones(len(cand), dtype=bool)
    height = np.zeros(body.n)
    stats = {"candidates": int(len(cand)), "lost": {}}
    for pose in [(None, 0)] + list(poses):
        clip, frame = pose
        if not _set_frame(skel, clip, frame, objs):
            continue
        tree = _tree(objs) if clip is not None else tree
        P, N = (body.P, body.N) if clip is None else body.pose_arrays(pose)
        before = int(alive.sum())
        for k in np.nonzero(alive)[0]:
            i = cand[k]
            p, n = P[i], N[i]
            ok = True
            for d in _cone(n):
                if d[2] < SEAT_DOWN and (clip in SEATED or LOOK_UP_UNSEEN):
                    continue
                if tree.ray_cast(Vector(p - n * BELOW_M + d * 1e-4), Vector(d), REACH_M + BELOW_M)[0] is None:
                    ok = False
                    break
            if not ok:
                alive[k] = False
                continue
            if clip is None:
                # The outermost surface along the normal.
                q, far = Vector(p), 0.0
                for _ in range(8):
                    hit = tree.ray_cast(q + Vector(n) * 1e-4, Vector(n), REACH_M)
                    if hit[0] is None:
                        break
                    far += hit[3] + 1e-4
                    q = hit[0]
                height[i] = far
        stats["lost"][f"{clip}:{frame}"] = before - int(alive.sum())
    covered = np.zeros(body.n, dtype=bool)
    covered[cand[alive]] = True
    # The margin: a body vertex counts only when every one within MARGIN_M does.
    out = np.zeros(body.n)
    for i in np.nonzero(covered)[0]:
        if all(covered[j] or not body.cand[j] for _, j, _ in body.kd.find_range(body.P[i], MARGIN_M)):
            out[i] = height[i]
    stats["covered"] = int(covered.sum())
    stats["kept"] = int((out > 0).sum())
    _set_frame(skel, None, 0, objs)
    if saved is not None:
        ad.action = saved[0]
        for t, m in zip(ad.nla_tracks, saved[1]):
            t.mute = m
        bpy.context.scene.frame_set(saved[2])
    return out, stats


def encode(height: np.ndarray) -> str:
    """Cover heights as the file stores them: one byte per body vertex, 2 mm steps (0 = not covered)."""
    q = np.where(height > 0, np.clip(np.ceil(height / STEP_M), 1, 255), 0).astype(np.uint8)
    return base64.b64encode(q.tobytes()).decode("ascii")


def decode(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype=np.uint8).astype(np.float64) * STEP_M


LAYER_OF_SLOT = {"shoes": 1, "legs": 2, "body": 3}


def hidden_mask(cover: np.ndarray, body_v: np.ndarray, body_h: np.ndarray, faces) -> np.ndarray:
    """The client's rule (`assemble.ts` `coveredTriangles`): a face is hidden
    when each of its vertices lies over a covered body vertex, no further out
    than TOLERANCE_SCALE times the cover plus TOLERANCE_M."""
    bv = body_v.astype(np.int64)
    ok = (bv >= 0) & (bv < len(cover))
    c = np.where(ok, cover[np.clip(bv, 0, len(cover) - 1)], 0.0)
    under = ok & (c > 0) & (body_h <= c * TOLERANCE_SCALE + TOLERANCE_M)
    return np.array([all(under[i] for i in f) for f in faces], dtype=bool)


def hide_covered(objs) -> dict:
    """Deletes, from imported gear objects (any LOD, `slot` and `covers`
    custom properties and the `_BODYV` / `_BODYH` attributes as the files
    carry them), the faces a higher layer hides. For the review renders and
    check_garment_layers.py. Returns hidden faces per object."""
    import bmesh

    cover_of = {}
    for o in objs:
        if o.get("covers"):
            s = str(o.get("slot"))
            cover_of[s] = np.maximum(cover_of.get(s, 0.0), decode(str(o["covers"])))
    out = {}
    for o in objs:
        layer = LAYER_OF_SLOT.get(str(o.get("slot")), 0)
        above = [c for s, c in cover_of.items() if LAYER_OF_SLOT.get(s, 0) > layer]
        if not above or "_BODYV" not in o.data.attributes:
            continue
        n = max(len(c) for c in above)
        cover = np.zeros(n)
        for c in above:
            cover[: len(c)] = np.maximum(cover[: len(c)], c)
        me = o.data
        bv = np.zeros(len(me.vertices))
        bh = np.zeros(len(me.vertices))
        me.attributes["_BODYV"].data.foreach_get("value", bv)
        me.attributes["_BODYH"].data.foreach_get("value", bh)
        mask = hidden_mask(cover, bv, bh, [tuple(p.vertices) for p in me.polygons])
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[i] for i in np.nonzero(mask)[0]], context="FACES_ONLY")
        bm.to_mesh(me)
        bm.free()
        out[o.name] = int(mask.sum())
    return out
