"""The outline layer of characters (docs/art/character-look.md section 4).

Characters draw a Genshin-style inverted-hull outline (client
`toon/outline.ts`): a second draw of the character's own skinned geometry,
front faces culled, every vertex pushed out along a **smoothed normal** by a
screen-consistent width. This module bakes what the hull needs per vertex,
on every LOD of every exported mesh, as the custom glTF attribute
`_OUTLINE` (VEC3 float, glTF axes):

* its **direction** is the smoothed normal: the face normals
  (angle-weighted) of every vertex at the same position (within `WELD`), so UV seams, hard
  edges and split normals do not tear the hull open at a crease; a vertex
  whose welded neighbours face the other way (a double-sided sheet, two
  layers touching) keeps its own normal;
* its **length** is the width weight (0..1): 1 on hair, clothing and the
  body; 0 on the face's inner features (eyes, brows, nose, mouth: the
  features' polygons of the face frame, dilated by `FEATURE_PAD`, softened
  over `FEATURE_SOFT`), so only the jaw, chin and cheek contour carry a
  line; `HAND_WEIGHT` on the hands (fine detail); 0 on the hair's inner layer
  (hair_meshy's `inner_twin`: look pass 2, the shell draws the line).

How thick each material's line is (thin on the face) and its colour (a
darker shade of the vertex colour) are palette data on the client
(`data/art/toon_palette.json` `outline`), so they retune without a rebuild.

`bake(objs)` is called once per export (`export.export_glb` and
`hair.export_hair_glb`, marked hooks) and overwrites the attribute, so
re-exports and LODs always carry a fresh layer. Pure numpy apart from reading
the mesh; the weld is O(n log n).
"""

from __future__ import annotations

import numpy as np

ATTR = "_OUTLINE"
WELD = 1e-4  # metres: vertices this close share one smoothed normal
FEATURE_PAD = 0.004  # metres round the face's feature polygons where the weight is 0
FEATURE_SOFT = 0.006  # metres over which the weight returns to 1 beyond the pad
HAND_WEIGHT = 0.7
FLIP_DOT = 0.2  # below this agreement with its own normal a vertex keeps its own


def _mesh_arrays(me) -> tuple[np.ndarray, np.ndarray]:
    n = len(me.vertices)
    co = np.zeros(n * 3)
    me.vertices.foreach_get("co", co)
    me.calc_loop_triangles()
    tri = np.zeros(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tri)
    return co.reshape(-1, 3), tri.reshape(-1, 3)


def smoothed_normals(P: np.ndarray, tris: np.ndarray, weld: float = WELD) -> np.ndarray:
    """Unit smoothed normals (n, 3) of positions `P` and triangles `tris`
    (same frame as `P`). Vertices within `weld` of each other share the sum
    of their angle-weighted face normals, unless that sum disagrees with the
    vertex's own (then the vertex keeps its own)."""
    n = len(P)
    own = np.zeros((n, 3))
    if len(tris):
        corners = [P[tris[:, k]] for k in range(3)]
        fn = np.cross(corners[1] - corners[0], corners[2] - corners[0])
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-20)
        for k in range(3):
            # Angle weighting: how a quad is split into triangles does not matter.
            e1 = corners[(k + 1) % 3] - corners[k]
            e2 = corners[(k + 2) % 3] - corners[k]
            cosang = (e1 * e2).sum(axis=1) / np.maximum(np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1), 1e-20)
            ang = np.arccos(np.clip(cosang, -1.0, 1.0))
            np.add.at(own, tris[:, k], fn * ang[:, None])
    key = np.round(P / weld).astype(np.int64)
    _, group = np.unique(key, axis=0, return_inverse=True)
    group = group.ravel()
    summed = np.zeros((int(group.max()) + 1 if n else 0, 3))
    np.add.at(summed, group, own)
    sm = summed[group]
    on = own / np.maximum(np.linalg.norm(own, axis=1, keepdims=True), 1e-20)
    smn = sm / np.maximum(np.linalg.norm(sm, axis=1, keepdims=True), 1e-20)
    agree = (smn * on).sum(axis=1)
    out = np.where((agree > FLIP_DOT)[:, None], smn, on)
    # Isolated or degenerate vertices: straight up rather than zero.
    bad = np.linalg.norm(out, axis=1) < 0.5
    out[bad] = (0.0, 0.0, 1.0)
    return out


def _sdf_box(q: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Signed distance of 2D points to an axis-aligned box (negative inside)."""
    c = 0.5 * (lo + hi)
    h = 0.5 * (hi - lo)
    d = np.abs(q - c) - h
    outside = np.linalg.norm(np.maximum(d, 0.0), axis=1)
    inside = np.minimum(np.maximum(d[:, 0], d[:, 1]), 0.0)
    return outside + inside


def feature_box(fr) -> tuple[np.ndarray, np.ndarray]:
    """The face frame's inner features (both eyes with their lashes and
    brows, the nose tip, the mouth) as one box in face (u, v) coordinates."""
    pts = [p for p in fr.world_polys().values() if len(p)]
    pts.append(np.asarray(fr.nose, dtype=np.float64)[None, :2])
    pts.append(np.asarray(fr.mouth, dtype=np.float64)[None, :2])
    allp = np.vstack(pts)
    return allp.min(axis=0), allp.max(axis=0)


def face_weights(fr, P_world: np.ndarray, pad: float = FEATURE_PAD, soft: float = FEATURE_SOFT) -> np.ndarray:
    """Width weights of head vertices (Blender world positions): 0 over the
    inner features (the box of `feature_box`, `pad` round it), back to 1 over
    `soft`; only the front of the head can fall in the box."""
    lo, hi = feature_box(fr)
    q = fr.uv(P_world)
    d = _sdf_box(q, lo - pad, hi + pad)
    t = np.clip(d / soft, 0.0, 1.0)
    w = t * t * (3.0 - 2.0 * t)
    front = fr.front(P_world)
    return np.where(front, w, 1.0)


def _face_frame(obj):
    """The face frame of a face-textured head object (built this run), else None."""
    if obj.get("region") != "head" and not obj.get("face_texture"):
        return None
    try:
        from . import face_paint as FP
    except ImportError:  # pragma: no cover
        return None
    for body_id, (fr, _fmap) in FP.FACE_BUILDS.items():
        if obj.name.startswith(body_id):
            return fr
    return None


def weights_for(obj, P_local: np.ndarray) -> np.ndarray:
    """The width weight of each vertex of `obj` (see the module docstring)."""
    n = len(P_local)
    w = np.ones(n)
    region = str(obj.get("region", ""))
    if region == "hands":
        w[:] = HAND_WEIGHT
    fr = _face_frame(obj)
    if fr is not None:
        mw = np.array(obj.matrix_world)
        Pw = P_local @ mw[:3, :3].T + mw[:3, 3]
        w *= face_weights(fr, Pw)
    # Look pass 2 integration: the hair's inner layer (hair_meshy, the
    # vertices whose `inner_twin` is another vertex) carries no line. Its
    # faces are flipped, so its own normal points into the head: its hull
    # would only bulge inward and, at the tips where it meets the shell, push
    # against the shell's hull. The shell draws the outline.
    me = getattr(obj, "data", None)
    tw = me.attributes.get("inner_twin") if me is not None else None
    if tw is not None and len(tw.data) == n:
        twin = np.zeros(n, dtype=np.int32)
        tw.data.foreach_get("value", twin)
        w[twin != np.arange(n)] = 0.0
    return w


def to_gltf_axes(v: np.ndarray) -> np.ndarray:
    """Blender (x, y, z) -> glTF (x, z, -y): custom attributes are exported as is."""
    return np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1)


def layer(obj) -> np.ndarray:
    """The `_OUTLINE` values (n, 3) of a mesh object: smoothed normal (glTF
    axes, the mesh's own frame) times the width weight."""
    P, tris = _mesh_arrays(obj.data)
    N = smoothed_normals(P, tris)
    w = weights_for(obj, P)
    return to_gltf_axes(N) * w[:, None]


def write(obj, values: np.ndarray) -> None:
    me = obj.data
    old = me.attributes.get(ATTR)
    if old is not None:
        me.attributes.remove(old)
    a = me.attributes.new(ATTR, "FLOAT_VECTOR", "POINT")
    a.data.foreach_set("vector", np.asarray(values, dtype=np.float32).ravel())


def read(obj) -> np.ndarray | None:
    a = obj.data.attributes.get(ATTR)
    if a is None:
        return None
    v = np.zeros(len(obj.data.vertices) * 3, dtype=np.float32)
    a.data.foreach_get("vector", v)
    return v.reshape(-1, 3).astype(np.float64)


def bake(objs) -> int:
    """Writes the outline layer on every mesh object of `objs`; returns the vertex count."""
    total = 0
    for o in objs:
        if getattr(o, "type", None) != "MESH" or not len(o.data.vertices):
            continue
        write(o, layer(o))
        total += len(o.data.vertices)
    return total
