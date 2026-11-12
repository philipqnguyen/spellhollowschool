"""LOD1 meshes: decimate the base shape, then rebuild shape keys from LOD0.

Shape keys cannot survive a Decimate modifier, so LOD1 is decimated from the
base shape (weights, UVs and the dye mask are interpolated by the modifier),
then each LOD1 vertex is bound to the nearest point of the LOD0 base mesh
(triangle + barycentric coordinates) and every LOD0 shape key is re-evaluated
at that point. Vertices on region borders and open edges are protected so the
LOD keeps the same region split and silhouette.
"""

from __future__ import annotations

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

from .meshutil import MeshData

PROTECT_GROUP = "_lod_protect"


def _triangles(verts: np.ndarray, faces) -> np.ndarray:
    tris = []
    for f in faces:
        for i in range(1, len(f) - 1):
            tris.append((f[0], f[i], f[i + 1]))
    return np.array(tris, dtype=np.int64)


def barycentric(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
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


class Binder:
    """Nearest-surface binding to a reference mesh (positions + keys + weights)."""

    def __init__(self, md: MeshData):
        self.md = md
        self.tris = _triangles(md.verts, md.faces)
        self.bvh = BVHTree.FromPolygons([tuple(v) for v in md.verts], [tuple(t) for t in self.tris])

    def bind(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(triangle ids, barycentrics, signed normal offsets) for points."""
        tri_ids = np.zeros(len(pts), dtype=np.int64)
        bcs = np.zeros((len(pts), 3))
        offs = np.zeros(len(pts))
        V = self.md.verts
        for i, p in enumerate(pts):
            loc, nrm, idx, dist = self.bvh.find_nearest(p)
            if idx is None:
                continue
            t = self.tris[idx]
            bc = barycentric(np.asarray(p), V[t[0]], V[t[1]], V[t[2]])
            tri_ids[i] = idx
            bcs[i] = bc
            n = _tri_normal(V[t[0]], V[t[1]], V[t[2]])
            offs[i] = float((np.asarray(p) - (bc @ V[t])) @ n)
        return tri_ids, bcs, offs

    def evaluate(self, tri_ids, bcs, offs, positions: np.ndarray, smooth_normals: np.ndarray | None = None) -> np.ndarray:
        """Re-evaluate bound points on another shape of the reference mesh."""
        t = self.tris[tri_ids]
        a, b, c = positions[t[:, 0]], positions[t[:, 1]], positions[t[:, 2]]
        base = a * bcs[:, :1] + b * bcs[:, 1:2] + c * bcs[:, 2:3]
        if smooth_normals is not None:
            n = (smooth_normals[t[:, 0]] * bcs[:, :1] + smooth_normals[t[:, 1]] * bcs[:, 1:2] + smooth_normals[t[:, 2]] * bcs[:, 2:3])
        else:
            n = np.cross(b - a, c - a)
        n = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        return base + n * offs[:, None]

    def weights(self, tri_ids, bcs) -> list[dict[str, float]]:
        out = []
        W = self.md.weights
        for ti, bc in zip(tri_ids, bcs):
            acc: dict[str, float] = {}
            for vi, k in zip(self.tris[ti], bc):
                for name, v in W[vi].items():
                    acc[name] = acc.get(name, 0.0) + v * k
            out.append(acc)
        return out


def _tri_normal(a, b, c) -> np.ndarray:
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n)
    return n / ln if ln > 1e-12 else np.array([0.0, 0.0, 1.0])


def vertex_normals(verts: np.ndarray, faces) -> np.ndarray:
    n = np.zeros_like(verts)
    for f in faces:
        p = verts[list(f)]
        fn = np.zeros(3)
        for i in range(len(f)):
            fn += np.cross(p[i], p[(i + 1) % len(f)])
        for i in f:
            n[i] += fn
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def decimate_object(obj: bpy.types.Object, ratio: float, protect: np.ndarray | None = None,
                    keep_weights: np.ndarray | None = None) -> bpy.types.Object:
    """Duplicate `obj` (without shape keys) and decimate it with Collapse.
    `protect` vertices are kept; optional `keep_weights` (0..1 per vertex)
    make the collapse prefer other areas (1 = like `protect`)."""
    dup = obj.copy()
    dup.data = obj.data.copy()
    dup.name = obj.name + "_lod1"
    dup.data.name = dup.name
    for c in obj.users_collection:
        c.objects.link(dup)
    if dup.data.shape_keys:
        dup.shape_key_clear()
    for m in list(dup.modifiers):
        dup.modifiers.remove(m)
    use_group = (protect is not None and len(protect)) or keep_weights is not None
    if use_group:
        g = dup.vertex_groups.new(name=PROTECT_GROUP)
        if keep_weights is not None:
            w = np.clip(np.asarray(keep_weights, dtype=np.float64), 0.0, 1.0)
            for value in np.unique(np.round(w, 2)):
                if value > 0:
                    g.add([int(i) for i in np.nonzero(np.round(w, 2) == value)[0]], float(value), "REPLACE")
        if protect is not None and len(protect):
            g.add([int(i) for i in protect], 1.0, "REPLACE")
    mod = dup.modifiers.new("decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    if use_group:
        mod.vertex_group = PROTECT_GROUP
        mod.vertex_group_factor = 1.0
        mod.invert_vertex_group = True  # weight 1 in the group = keep
    bpy.context.view_layer.objects.active = dup
    with bpy.context.temp_override(object=dup, active_object=dup):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    g = dup.vertex_groups.get(PROTECT_GROUP)
    if g is not None:
        dup.vertex_groups.remove(g)
    clean(dup)
    return dup


def split_nonmanifold(obj: bpy.types.Object) -> bpy.types.Object:
    """A copy of `obj` (no shape keys) with its non-manifold edges split, so
    a collapse can reduce each sheet on its own."""
    import bmesh

    dup = obj.copy()
    dup.data = obj.data.copy()
    dup.name = obj.name + "_split"
    for c in obj.users_collection:
        c.objects.link(dup)
    if dup.data.shape_keys:
        dup.shape_key_clear()
    bm = bmesh.new()
    bm.from_mesh(dup.data)
    edges = [e for e in bm.edges if len(e.link_faces) > 2]
    if edges:
        bmesh.ops.split_edges(bm, edges=edges)
    bm.to_mesh(dup.data)
    bm.free()
    return dup


def cluster(obj: bpy.types.Object, dist: float) -> bpy.types.Object:
    """A copy of `obj` with vertices closer than `dist` merged (a coarse
    vertex clustering that lets a stalled collapse continue)."""
    import bmesh

    dup = obj.copy()
    dup.data = obj.data.copy()
    dup.name = obj.name + "_cluster"
    for c in obj.users_collection:
        c.objects.link(dup)
    if dup.data.shape_keys:
        dup.shape_key_clear()
    bm = bmesh.new()
    bm.from_mesh(dup.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    bm.to_mesh(dup.data)
    bm.free()
    dup.data.validate(clean_customdata=False)
    return dup


def cull_small_parts(obj: bpy.types.Object, target: int) -> int:
    """Delete the smallest loose parts (by bounding-box size) until the mesh
    has at most `target` triangles (the largest part always stays). Returns
    the number of parts removed."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    seen, parts = set(), []
    for f in bm.faces:
        if f.index in seen:
            continue
        comp, stack = [], [f]
        seen.add(f.index)
        while stack:
            g = stack.pop()
            comp.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        co = np.array([v.co for g in comp for v in g.verts])
        size = float(np.linalg.norm(co.max(axis=0) - co.min(axis=0)))
        tris = sum(len(g.verts) - 2 for g in comp)
        parts.append((size, tris, comp))
    parts.sort(key=lambda p: (p[1], p[0]))  # fewest triangles first (buttons, crests, feathers)
    total = sum(p[1] for p in parts)
    kill, removed = [], 0
    for size, tris, comp in parts[:-1]:
        if total <= target:
            break
        kill += comp
        total -= tris
        removed += 1
    if kill:
        bmesh.ops.delete(bm, geom=kill, context="FACES")
        loose = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return removed


def clean(obj: bpy.types.Object, max_influences: int = 4) -> None:
    """After a collapse: drop degenerate faces (strong decimation to the low
    tiers leaves some, which the exporter flags as invalid meshes) and keep
    at most `max_influences` normalized bone weights per vertex (collapses
    blend the vertex groups of merged vertices)."""
    obj.data.validate(clean_customdata=False)
    if not obj.vertex_groups:
        return
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=max_influences)
        bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)


def border_vertices(md: MeshData) -> np.ndarray:
    """Vertices on region borders or open edges (kept by the decimation)."""
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for fi, f in enumerate(md.faces):
        for i in range(len(f)):
            e = tuple(sorted((f[i], f[(i + 1) % len(f)])))
            edge_faces.setdefault(e, []).append(fi)
    out = set()
    for e, fs in edge_faces.items():
        if len(fs) == 1 or len({md.region[f] for f in fs}) > 1 or any(f in md.corner_uv for f in fs):
            out.update(e)
    return np.array(sorted(out), dtype=np.int64)


def rebind_keys(lod: bpy.types.Object, md0: MeshData) -> None:
    """Rebuild md0's shape keys on a decimated object."""
    me = lod.data
    pts = np.array([v.co for v in me.vertices])
    binder = Binder(md0)
    tri_ids, bcs, offs = binder.bind(pts)
    if not md0.keys:
        return
    lod.shape_key_add(name="Basis", from_mix=False)
    for name, kpos in md0.keys.items():
        # Same displacement as LOD0 at the bound point.
        zero = np.zeros(len(pts))
        p = pts + binder.evaluate(tri_ids, bcs, zero, kpos) - binder.evaluate(tri_ids, bcs, zero, md0.verts)
        kb = lod.shape_key_add(name=name, from_mix=False)
        kb.data.foreach_set("co", p.astype(np.float32).ravel())


def region_ids_to_names(lod: bpy.types.Object, names: list[str]) -> list[str]:
    attr = lod.data.attributes.get("region_id")
    vals = np.zeros(len(lod.data.polygons), dtype=np.int32)
    attr.data.foreach_get("value", vals)
    return [names[i] for i in vals]


def add_region_attribute(obj: bpy.types.Object, regions: list[str], names: list[str]) -> None:
    attr = obj.data.attributes.new("region_id", "INT", "FACE")
    attr.data.foreach_set("value", np.array([names.index(r) for r in regions], dtype=np.int32))


def triangle_count(obj: bpy.types.Object) -> int:
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


