"""The baked shade layer of characters (docs/art/character-adoption.md section 3).

Every LOD of every character asset carries a per-vertex attribute `_SHADE`
(VEC4, exported as normalized unsigned shorts; the client packs it into four
normalized bytes). No texture: zero texture memory, the one shared atlas stays
as it is, and the dyes still tint the albedo because the shade is applied in
the shader.

| channel | body and garments | hair |
| --- | --- | --- |
| x shade | AO in creases * fold bands * top-to-bottom gradient; faces 1 | AO and root darkening |
| y strand | 0 | 0 at the scalp .. 1 at the tip |
| z band phase | 0.5 | per-lock band offset |
| w outline | 1 on silhouettes, about 0.5 on inner seams and folds | about 0.45 |

How it is made (the hair build uses `ambient_occlusion`, `write`, `transfer` and
`check` too):

1. `ambient_occlusion`: a Cycles AO bake to a temporary point-domain colour
   attribute (distance 6 cm, hair 3 cm, 64 samples) in the rest pose, the
   asset fitted on its body; the occluders are present but not baked; then
   two passes of vertex smoothing.
2. `fold_factor`: soft procedural fold bands (sleeve bends at the elbow and
   wrist, under the chest, the waist sides, skirt folds from the hip, the
   trouser knee and ankle) as a 0.85-1 multiplier, chosen per garment shape
   (`DEFAULT_FOLDS`, a design may override them with `"folds"` in
   data/items/garment_designs.json).
3. `gradient`: by height, 1.0 at the shoulders to 0.85 at the feet.
4. `x = clamp(ao ** 0.8 * folds * gradient, 0.35, 1)`; the body's `head`
   region (the face) is forced to 1 so faces stay clean.
5. LOD1-3 take the layer from LOD0 by nearest surface point (`transfer`).
"""

from __future__ import annotations

import math
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ATTR = "_SHADE"
AO_DISTANCE = 0.06
HAIR_AO_DISTANCE = 0.03
SAMPLES = 64
SMOOTH_PASSES = 2
X_MIN = 0.35
AO_POW = 0.8
GRADIENT_FEET = 0.85  # gradient multiplier at the floor (1.0 from the shoulders up)
SHOULDER_FRAC = 0.82  # shoulder height as a fraction of the body height
OUTLINE_INNER = 0.5  # outline hint on fold creases
TMP_ATTR = "_ao_bake"
STATS: dict = {"bakes": 0, "seconds": 0.0, "vertices": 0}


# ----------------------------------------------------------------------------
# Ambient occlusion (Cycles bake)
# ----------------------------------------------------------------------------


def _neighbours(me) -> list[np.ndarray]:
    n = len(me.vertices)
    e = np.zeros(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", e)
    e = e.reshape(-1, 2)
    nb: list[list[int]] = [[] for _ in range(n)]
    for a, b in e:
        nb[a].append(int(b))
        nb[b].append(int(a))
    return [np.array(x, dtype=np.int64) for x in nb]


def smooth(values: np.ndarray, me, passes: int = SMOOTH_PASSES) -> np.ndarray:
    """Vertex smoothing over mesh edges (half self, half the neighbours' mean)."""
    if passes <= 0 or not len(values):
        return values
    nb = _neighbours(me)
    v = values.astype(np.float64).copy()
    for _ in range(passes):
        out = v.copy()
        for i, idx in enumerate(nb):
            if len(idx):
                out[i] = 0.5 * v[i] + 0.5 * float(v[idx].mean())
        v = out
    return v


def ambient_occlusion(targets: list, occluders: list = (), distance: float = AO_DISTANCE, samples: int = SAMPLES,
                      passes: int = SMOOTH_PASSES, rest_pose: list | None = None) -> dict[str, np.ndarray]:
    """Per target object, per vertex AO (1 open .. 0 fully occluded), baked
    by Cycles in one pass for all targets, with `occluders` present (visible
    to rays, not baked). Every other object is hidden from the render while
    baking. `rest_pose`: armatures put in their rest position meanwhile.
    Targets keep their attributes as they were (the bake goes to a temporary
    colour attribute that is removed again)."""
    t0 = time.time()
    scene = bpy.context.scene
    targets = [o for o in targets if o is not None and o.type == "MESH" and len(o.data.vertices)]
    if not targets:
        return {}
    keep = set(o.name for o in targets) | set(o.name for o in occluders if o is not None)
    saved_render = {o.name: o.hide_render for o in scene.objects}
    saved_view = {o.name: (o.hide_get(), o.hide_viewport) for o in scene.objects}
    saved_engine = scene.render.engine
    arm_pos = {}
    for a in rest_pose or []:
        arm_pos[a.name] = a.data.pose_position
        a.data.pose_position = "REST"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("shade_bake")
    saved_dist = scene.world.light_settings.distance
    saved_active = {}
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = False
        scene.world.light_settings.distance = distance
        scene.render.bake.target = "VERTEX_COLORS"
        for o in scene.objects:
            o.hide_render = o.name not in keep
        for o in targets + [x for x in occluders if x is not None]:
            o.hide_set(False)
            o.hide_viewport = False
        for o in targets:
            me = o.data
            old = me.attributes.get(TMP_ATTR)
            if old is not None:
                me.attributes.remove(old)
            saved_active[o.name] = me.color_attributes.active_color_name
            a = me.color_attributes.new(TMP_ATTR, "FLOAT_COLOR", "POINT")
            me.color_attributes.active_color = a
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        for o in targets:
            o.select_set(True)
        bpy.context.view_layer.objects.active = targets[0]
        bpy.ops.object.bake(type="AO", target="VERTEX_COLORS", use_clear=True)
        out = {}
        for o in targets:
            me = o.data
            a = me.color_attributes[TMP_ATTR]
            c = np.zeros(len(me.vertices) * 4, dtype=np.float32)
            a.data.foreach_get("color", c)
            ao = np.clip(c.reshape(-1, 4)[:, 0].astype(np.float64), 0.0, 1.0)
            out[o.name] = smooth(ao, me, passes)
            me.color_attributes.remove(a)
            name = saved_active.get(o.name)
            if name and name in me.color_attributes:
                me.color_attributes.active_color = me.color_attributes[name]
                me.color_attributes.render_color_index = me.color_attributes.find(name)
    finally:
        scene.render.engine = saved_engine
        scene.world.light_settings.distance = saved_dist
        for o in scene.objects:
            if o.name in saved_render:
                o.hide_render = saved_render[o.name]
            if o.name in saved_view:
                h, hv = saved_view[o.name]
                o.hide_viewport = hv
                try:
                    o.hide_set(h)
                except RuntimeError:
                    pass
        for a in rest_pose or []:
            a.data.pose_position = arm_pos.get(a.name, "POSE")
    STATS["bakes"] += 1
    STATS["seconds"] += time.time() - t0
    STATS["vertices"] += sum(len(o.data.vertices) for o in targets)
    return out


# ----------------------------------------------------------------------------
# Gradient and painted folds
# ----------------------------------------------------------------------------


def world_points(obj) -> np.ndarray:
    me = obj.data
    co = np.zeros(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    M = np.array(obj.matrix_world)
    return co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]


def gradient(z: np.ndarray, height: float) -> np.ndarray:
    """1.0 from the shoulders up to GRADIENT_FEET at the floor (smooth)."""
    t = np.clip(np.asarray(z) / max(SHOULDER_FRAC * height, 1e-6), 0.0, 1.0)
    t = t * t * (3 - 2 * t) * 0.5 + 0.5 * t
    return GRADIENT_FEET + (1.0 - GRADIENT_FEET) * t


class Skeleton:
    """Rest-pose joints read from the game skeleton (what fold bands need)."""

    def __init__(self, skel):
        m = skel.matrix_world
        b = skel.data.bones

        def head(n):
            return np.array(m @ b[n].head_local)

        def tail(n):
            return np.array(m @ b[n].tail_local)

        self.arm = {s: np.array([head(f"DEF-upper_arm.{s}"), head(f"DEF-forearm.{s}"), head(f"DEF-hand.{s}"), tail(f"DEF-hand.{s}")])
                    for s in ("L", "R")}
        self.leg = {s: np.array([head(f"DEF-thigh.{s}"), head(f"DEF-shin.{s}"), head(f"DEF-foot.{s}"), tail(f"DEF-toe.{s}")])
                    for s in ("L", "R")}
        names = ["DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003", "DEF-spine.004", "DEF-spine.005", "DEF-spine.006"]
        self.spine = np.array([head(n) for n in names] + [tail(names[-1])])
        self.height = float(self.spine[-1][2]) * 1.02
        self.sc = self.height / 1.78


def _polyline_project(p: np.ndarray, line: np.ndarray):
    """(distance to the polyline, arc length of the nearest point, the nearest point) per point."""
    best_d = np.full(len(p), np.inf)
    best_s = np.zeros(len(p))
    best_q = np.zeros_like(p)
    s0 = 0.0
    for a, b in zip(line[:-1], line[1:]):
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1e-9:
            continue
        t = np.clip(((p - a) @ d) / (L * L), 0.0, 1.0)
        q = a + t[:, None] * d
        dist = np.linalg.norm(p - q, axis=1)
        better = dist < best_d
        best_d[better] = dist[better]
        best_s[better] = s0 + t[better] * L
        best_q[better] = q[better]
        s0 += L
    return best_d, best_s, best_q


# Default fold bands per garment shape (designs override with "folds").
DEFAULT_FOLDS = {
    "top_fitted": ["elbow", "wrist", "underbust", "waist"],
    "top_knit": ["elbow", "wrist", "underbust", "waist"],
    "coat_long": ["elbow", "wrist", "underbust", "skirt"],
    "cloak": ["skirt"],
    "trousers_fitted": ["knee", "ankle", "crotch"],
    "trousers_wide": ["knee", "ankle", "crotch"],
    "skirt_short": ["skirt"],
    "skirt_long": ["skirt"],
    "shoe_low": [],
    "boot": ["ankle_boot"],
    "cap_knit": [],
    "hood": ["hood"],
    "hat_rigid": [],
}
FOLD_NAMES = ("elbow", "wrist", "underbust", "waist", "skirt", "knee", "ankle", "crotch", "ankle_boot", "hood")


def folds_for(shape: str, design: dict) -> list[str]:
    spec = design.get("folds")
    if isinstance(spec, list):
        return [f for f in spec if f in FOLD_NAMES]
    out = list(DEFAULT_FOLDS.get(shape, []))
    if isinstance(spec, dict):
        out = [f for f in out if f not in spec.get("remove", [])] + [f for f in spec.get("add", []) if f not in out]
    return out


def fold_factor(p: np.ndarray, sk: Skeleton, bands: list[str]) -> np.ndarray:
    """Soft painted folds (0.85..1 multiplier) at the rest-pose points `p`
    of a garment, from the skeleton only (body-agnostic, works on any LOD)."""
    m = np.ones(len(p))
    if not bands or not len(p):
        return m
    sc = sk.sc
    # Which chain each point belongs to (nearest bone polyline).
    da = {s: _polyline_project(p, sk.arm[s]) for s in ("L", "R")}
    dl = {s: _polyline_project(p, sk.leg[s]) for s in ("L", "R")}
    dsp = _polyline_project(p, sk.spine)
    d_arm = np.minimum(da["L"][0], da["R"][0])
    d_leg = np.minimum(dl["L"][0], dl["R"][0])
    on_arm = d_arm < np.minimum(dsp[0], d_leg) * 0.95
    on_leg = (d_leg < dsp[0]) & ~on_arm
    hip_z = float(0.5 * (sk.leg["L"][0][2] + sk.leg["R"][0][2]))
    chest_z = float(sk.spine[3][2])
    waist_z = float(sk.spine[2][2])
    knee_z = float(0.5 * (sk.leg["L"][1][2] + sk.leg["R"][1][2]))
    ankle_z = float(0.5 * (sk.leg["L"][2][2] + sk.leg["R"][2][2]))
    mid_y = float(sk.spine[2][1])
    az = np.arctan2(p[:, 0], -(p[:, 1] - mid_y))  # 0 = front, +pi/2 = the character's left

    def arm_bands():
        out = np.ones(len(p))
        for s in ("L", "R"):
            d, sv, q = da[s]
            sel = on_arm & (d <= np.minimum(da["L" if s == "R" else "R"][0], 1e9))
            if not sel.any():
                continue
            line = sk.arm[s]
            ua = float(np.linalg.norm(line[1] - line[0]))
            fa = float(np.linalg.norm(line[2] - line[1]))
            ax = (line[2] - line[1]) / max(fa, 1e-9)
            rad = p - q
            rad = rad - np.outer(rad @ ax, ax)
            rad /= np.maximum(np.linalg.norm(rad, axis=1, keepdims=True), 1e-9)
            crease = np.array([0.0, -1.0, 0.0]) - ax * float(-ax[1])
            crease /= max(np.linalg.norm(crease), 1e-9)
            ang = np.arctan2(rad @ np.cross(ax, crease), rad @ crease)
            if "elbow" in bands:
                g = np.exp(-((sv - ua) / (0.055 * sc)) ** 2)
                inner = 0.35 + 0.65 * np.clip(np.cos(ang), 0, 1) ** 1.5
                wav = 0.55 + 0.45 * np.cos(2 * np.pi * (sv - ua) / (0.045 * sc) + 1.3 * ang)
                out[sel] *= (1.0 - 0.13 * g * inner * wav)[sel]
            if "wrist" in bands:
                s_w = ua + fa
                ramp = np.clip(1.0 - (s_w - sv) / (0.13 * sc), 0.0, 1.0) * (sv < s_w + 0.01 * sc)
                wav = 0.5 + 0.5 * np.cos(2 * np.pi * sv / (0.036 * sc) + 1.6 * ang)
                out[sel] *= (1.0 - 0.09 * ramp * wav ** 2)[sel]
        return out

    if "elbow" in bands or "wrist" in bands:
        m *= arm_bands()
    trunk = ~on_arm & ~on_leg
    front = np.clip(np.cos(az), 0.0, 1.0)
    if "underbust" in bands:
        zc = chest_z - 0.075 * sc
        g = np.exp(-((p[:, 2] - zc) / (0.03 * sc)) ** 2) * front ** 1.5
        m *= np.where(trunk, 1.0 - 0.10 * g, 1.0)
    if "waist" in bands:
        g = np.exp(-((p[:, 2] - waist_z) / (0.035 * sc)) ** 2) * np.abs(np.sin(az)) ** 1.5
        wav = 0.5 + 0.5 * np.cos(7 * az + 0.6)
        m *= np.where(trunk, 1.0 - 0.08 * g * wav, 1.0)
    if "skirt" in bands:
        # Vertical folds hanging from the hips, deepening downward.
        depth = np.clip((hip_z - p[:, 2]) / (0.28 * sc), 0.0, 1.0)
        wav = (0.5 + 0.5 * np.cos(9 * az + 0.8 + 0.6 * np.sin(3 * az))) ** 3
        far = d_leg > 0.07 * sc  # hanging cloth, not tights against the leg
        m *= np.where(far, 1.0 - 0.15 * depth * wav, 1.0)
    if "crotch" in bands:
        for s in ("L", "R"):
            hip = sk.leg[s][0]
            r = np.linalg.norm(p[:, :2] - np.array([0.0, hip[1]]), axis=1)
            g = np.exp(-((p[:, 2] - (hip_z - 0.06 * sc)) / (0.05 * sc)) ** 2) * np.exp(-(r / (0.07 * sc)) ** 2) * front
            m *= 1.0 - 0.09 * g
    for name, z0, width, amount, period in (("knee", knee_z, 0.07, 0.11, 0.05), ("ankle", ankle_z + 0.07 * sc, 0.06, 0.09, 0.035),
                                             ("ankle_boot", ankle_z + 0.03 * sc, 0.035, 0.08, 0.03)):
        if name not in bands:
            continue
        g = np.exp(-((p[:, 2] - z0) / (width * sc)) ** 2)
        for s in ("L", "R"):
            d, sv, q = dl[s]
            sel = on_leg & (d <= dl["L" if s == "R" else "R"][0])
            rel = p - q
            back = np.clip(rel[:, 1] / np.maximum(np.linalg.norm(rel[:, :2], axis=1), 1e-9), -1, 1)  # +1 behind the leg
            w = (0.35 + 0.65 * np.clip(back, 0, 1)) if name == "knee" else 1.0
            wav = 0.55 + 0.45 * np.cos(2 * np.pi * p[:, 2] / (period * sc) + 2.0 * np.arctan2(rel[:, 0], -rel[:, 1]))
            m *= np.where(sel, 1.0 - amount * g * w * wav, 1.0)
    if "hood" in bands:
        g = np.clip((p[:, 2] - chest_z) / (0.3 * sc), 0, 1)
        wav = 0.5 + 0.5 * np.cos(5 * az)
        m *= 1.0 - 0.08 * g * wav * np.clip(-np.cos(az), 0, 1)
    return np.clip(m, 0.85, 1.0)


# ----------------------------------------------------------------------------
# Writing, transferring and checking the attribute
# ----------------------------------------------------------------------------


def write(obj, x, y=0.0, z=0.5, w=1.0) -> None:
    """Store the layer on `obj` as the point-domain `_SHADE` attribute
    (BYTE_COLOR: glTF VEC4 normalized unsigned short with export_attributes)."""
    me = obj.data
    n = len(me.vertices)

    def col(v):
        a = np.asarray(v, dtype=np.float64)
        return np.full(n, float(a)) if a.ndim == 0 else a

    vals = np.stack([np.clip(col(x), 0, 1), np.clip(col(y), 0, 1), np.clip(col(z), 0, 1), np.clip(col(w), 0, 1)], axis=1)
    old = me.attributes.get(ATTR)
    if old is not None:
        me.attributes.remove(old)
    active = me.color_attributes.active_color_name
    a = me.color_attributes.new(ATTR, "BYTE_COLOR", "POINT")
    a.data.foreach_set("color", vals.astype(np.float32).ravel())
    if active and active in me.color_attributes:
        me.color_attributes.active_color = me.color_attributes[active]
        me.color_attributes.render_color_index = me.color_attributes.find(active)


def read(obj) -> np.ndarray | None:
    me = obj.data
    a = me.attributes.get(ATTR)
    if a is None:
        return None
    v = np.zeros(len(me.vertices) * 4, dtype=np.float32)
    a.data.foreach_get("color", v)
    return v.reshape(-1, 4).astype(np.float64)


def transfer(sources: list, dst, keep_x_floor: float = X_MIN) -> None:
    """`dst` takes the layer of the nearest surface point of `sources`
    (barycentric blend of the hit triangle's vertex values): LOD1-3 from LOD0."""
    verts, tris, vals = [], [], []
    off = 0
    for o in sources:
        v = read(o)
        if v is None:
            continue
        p = world_points(o)
        me = o.data
        me.calc_loop_triangles()
        t = np.zeros(len(me.loop_triangles) * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", t)
        verts.append(p)
        tris.append(t.reshape(-1, 3) + off)
        vals.append(v)
        off += len(p)
    if not verts:
        return
    P = np.vstack(verts)
    T = np.vstack(tris)
    V = np.vstack(vals)
    tree = BVHTree.FromPolygons(P.tolist(), T.tolist(), all_triangles=True)
    q = world_points(dst)
    out = np.zeros((len(q), 4))
    for i, pt in enumerate(q):
        loc, nrm, idx, dist = tree.find_nearest(Vector(pt))
        if idx is None:
            out[i] = (1.0, 0.0, 0.5, 1.0)
            continue
        a, b, c = P[T[idx]]
        bc = _bary(np.array(loc), a, b, c)
        out[i] = bc @ V[T[idx]]
    out[:, 0] = np.clip(out[:, 0], keep_x_floor, 1.0)
    write(dst, out[:, 0], out[:, 1], out[:, 2], out[:, 3])


def _bary(p, a, b, c) -> np.ndarray:
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
    d20, d21 = v2 @ v0, v2 @ v1
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-18:
        return np.array([1.0, 0.0, 0.0])
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    bc = np.clip(np.array([1.0 - v - w, v, w]), 0.0, 1.0)
    return bc / bc.sum()


def combine(ao: np.ndarray, folds: np.ndarray | float, grad: np.ndarray | float) -> np.ndarray:
    return np.clip(np.power(np.clip(ao, 0, 1), AO_POW) * folds * grad, X_MIN, 1.0)


def check(obj, face: bool = False) -> list[str]:
    """Contract checks of one exported mesh: `_SHADE` present with 4
    components, x in [0.35, 1]; on a face mesh x == 1."""
    v = read(obj)
    if v is None:
        return [f"{obj.name}: no {ATTR}"]
    errs = []
    eps = 1.0 / 255.0
    if v[:, 0].min() < X_MIN - eps or v[:, 0].max() > 1.0 + eps:
        errs.append(f"{obj.name}: x outside [0.35, 1] ({v[:, 0].min():.3f}..{v[:, 0].max():.3f})")
    if face and v[:, 0].min() < 1.0 - eps:
        errs.append(f"{obj.name}: face x < 1 ({v[:, 0].min():.3f})")
    return errs


# ----------------------------------------------------------------------------
# Body and garments (called by the build)
# ----------------------------------------------------------------------------


def bake_body(meshes: dict, skel, face_regions=("head",)) -> dict:
    """The body: AO (the whole body as occluder) * gradient, the face clean,
    outline hint 1. `meshes` is lod -> region -> object; LOD1-3 by transfer."""
    lod0 = list(meshes[0].values())
    ao = ambient_occlusion(lod0, lod0, rest_pose=[skel])
    sk = Skeleton(skel)
    for region, o in meshes[0].items():
        p = world_points(o)
        x = combine(ao[o.name], 1.0, gradient(p[:, 2], sk.height))
        if region in face_regions:
            x = np.ones(len(p))
        write(o, x, 0.0, 0.5, 1.0)
    for lod in sorted(meshes):
        if lod == 0:
            continue
        for region, o in meshes[lod].items():
            src = [meshes[0][region]] if region in meshes[0] else lod0
            transfer(src, o)
            if region in face_regions:
                v = read(o)
                write(o, 1.0, v[:, 1], v[:, 2], v[:, 3])
    return {"ao_mean": float(np.mean(np.concatenate(list(ao.values())))) if ao else 1.0}


def bake_item(meshes: dict, skel, body_objs: list, bands: list[str], extra_occluders: list = ()) -> dict:
    """A garment: AO with the body (and optional inner layers) as occluders,
    * fold bands * gradient; outline hint 0.5 in fold creases."""
    lod0 = [o for o in meshes[0].values()]
    ao = ambient_occlusion(lod0, list(body_objs) + list(extra_occluders) + lod0, rest_pose=[skel])
    sk = Skeleton(skel)
    stats = {}
    for o in lod0:
        p = world_points(o)
        f = fold_factor(p, sk, bands)
        x = combine(ao[o.name], f, gradient(p[:, 2], sk.height))
        w = np.where(f < 0.94, OUTLINE_INNER + (1 - OUTLINE_INNER) * np.clip((f - 0.88) / 0.06, 0, 1), 1.0)
        write(o, x, 0.0, 0.5, w)
        stats = {"x_min": round(float(x.min()), 3), "x_mean": round(float(x.mean()), 3), "folds_min": round(float(f.min()), 3)}
    for lod in sorted(meshes):
        if lod == 0:
            continue
        for o in meshes[lod].values():
            transfer(lod0, o)
    return stats


def shade_bytes(objs: list) -> int:
    """Client-side cost of the layer: 4 bytes per vertex (packed)."""
    return 4 * sum(len(o.data.vertices) for o in objs)
