"""Turning generated geometry into Blender meshes (the only bpy code for meshes).

Generators produce plain data: vertex positions (numpy), faces (tuples of
vertex indices), per-face attributes (region, swatch, dye mask) and per-vertex
bone weights. `MeshData` collects that; `to_object` builds the Blender object
with shape keys, the COLOR_0 dye mask, UVs into the shared atlas and vertex
groups.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import bpy
import numpy as np


@dataclass
class MeshData:
    verts: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    faces: list[tuple[int, ...]] = field(default_factory=list)
    # Per face.
    region: list[str] = field(default_factory=list)
    swatch: list[str] = field(default_factory=list)
    mask: list[tuple[float, float, float]] = field(default_factory=list)
    # Optional per-face-corner UV overrides: face index -> list of (u, v) in atlas space.
    corner_uv: dict[int, list[tuple[float, float]]] = field(default_factory=dict)
    # Per vertex: {bone: weight}.
    weights: list[dict[str, float]] = field(default_factory=list)
    # Shape keys: name -> (n, 3) absolute positions.
    keys: dict[str, np.ndarray] = field(default_factory=dict)
    # Optional per-face tags (generator bookkeeping, not exported).
    tags: list[str] = field(default_factory=list)

    def add_verts(self, pts: np.ndarray, weights: list[dict[str, float]] | None = None) -> int:
        """Append vertices; returns the index of the first one."""
        start = len(self.verts)
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
        self.verts = np.vstack([self.verts, pts]) if start else pts.copy()
        if weights is None:
            weights = [{} for _ in range(len(pts))]
        self.weights.extend(weights)
        return start

    def add_face(self, idx, region: str, swatch: str, mask=(0.0, 0.0, 0.0), uv=None) -> int:
        self.faces.append(tuple(int(i) for i in idx))
        self.region.append(region)
        self.swatch.append(swatch)
        self.mask.append(tuple(mask))
        if uv is not None:
            self.corner_uv[len(self.faces) - 1] = list(uv)
        return len(self.faces) - 1

    def tri_count(self) -> int:
        return sum(len(f) - 2 for f in self.faces)

    def compact(self) -> np.ndarray:
        """Drop vertices no face uses; returns the old index of each kept vertex."""
        used = np.zeros(len(self.verts), dtype=bool)
        for f in self.faces:
            used[list(f)] = True
        keep = np.nonzero(used)[0]
        remap = -np.ones(len(self.verts), dtype=np.int64)
        remap[keep] = np.arange(len(keep))
        self.verts = self.verts[keep]
        self.weights = [self.weights[i] for i in keep]
        self.faces = [tuple(int(remap[i]) for i in f) for f in self.faces]
        self.keys = {k: v[keep] for k, v in self.keys.items()}
        return keep

    def extend(self, other: "MeshData") -> None:
        """Append another mesh (keys present in either are merged; missing = base)."""
        off = len(self.verts)
        base_self = self.verts.copy()
        self.add_verts(other.verts, other.weights)
        for f, r, s, m in zip(other.faces, other.region, other.swatch, other.mask):
            self.faces.append(tuple(i + off for i in f))
            self.region.append(r)
            self.swatch.append(s)
            self.mask.append(m)
        for fi, uv in other.corner_uv.items():
            self.corner_uv[fi + len(self.faces) - len(other.faces)] = uv
        names = set(self.keys) | set(other.keys)
        for k in names:
            a = self.keys.get(k, base_self)
            b = other.keys.get(k, other.verts)
            self.keys[k] = np.vstack([a, b])


def link(obj: bpy.types.Object, collection: bpy.types.Collection | None = None) -> bpy.types.Object:
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def to_object(name: str, md: MeshData, atlas, material: bpy.types.Material | None = None,
              collection: bpy.types.Collection | None = None, base_key_name: str = "Basis") -> bpy.types.Object:
    """Build a mesh object from MeshData. `atlas` maps swatch names to UVs."""
    mesh = bpy.data.meshes.new(name)
    verts = md.verts.astype(np.float32)
    mesh.vertices.add(len(verts))
    mesh.vertices.foreach_set("co", verts.ravel())
    sizes = np.array([len(f) for f in md.faces], dtype=np.int32)
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]]).astype(np.int32)
    loops = np.array([i for f in md.faces for i in f], dtype=np.int32)
    mesh.loops.add(len(loops))
    mesh.loops.foreach_set("vertex_index", loops)
    mesh.polygons.add(len(md.faces))
    mesh.polygons.foreach_set("loop_start", starts)
    mesh.polygons.foreach_set("loop_total", sizes)
    mesh.update(calc_edges=True)
    mesh.validate(clean_customdata=False)
    mesh.shade_smooth()

    # UVs: swatch centre, or explicit per-corner coordinates.
    uv = mesh.uv_layers.new(name="UVMap")
    uvs = np.zeros((len(loops), 2), dtype=np.float32)
    for fi, f in enumerate(md.faces):
        s = starts[fi]
        if fi in md.corner_uv:
            uvs[s:s + len(f)] = np.asarray(md.corner_uv[fi], dtype=np.float32)
        else:
            uvs[s:s + len(f)] = atlas.uv(md.swatch[fi])
    uv.data.foreach_set("uv", uvs.ravel())

    # Dye mask as a face-corner byte colour (exported as COLOR_0).
    col = mesh.color_attributes.new(name="Color", type="BYTE_COLOR", domain="CORNER")
    cols = np.ones((len(loops), 4), dtype=np.float32)
    for fi, f in enumerate(md.faces):
        s = starts[fi]
        cols[s:s + len(f), :3] = md.mask[fi]
    col.data.foreach_set("color", cols.ravel())
    mesh.color_attributes.active_color = col
    mesh.color_attributes.render_color_index = 0

    obj = bpy.data.objects.new(name, mesh)
    link(obj, collection)
    if material is not None:
        mesh.materials.append(material)

    # Vertex groups.
    groups: dict[str, bpy.types.VertexGroup] = {}
    for vi, w in enumerate(md.weights):
        for bone, value in w.items():
            if value <= 0:
                continue
            g = groups.get(bone)
            if g is None:
                g = groups[bone] = obj.vertex_groups.new(name=bone)
            g.add([vi], float(value), "REPLACE")

    # Shape keys.
    if md.keys:
        obj.shape_key_add(name=base_key_name, from_mix=False)
        for kname, pts in md.keys.items():
            kb = obj.shape_key_add(name=kname, from_mix=False)
            kb.data.foreach_set("co", np.asarray(pts, dtype=np.float32).ravel())
        mesh.shape_keys.use_relative = True
    return obj


def face_region_split(obj: bpy.types.Object, regions: list[str]) -> dict[str, bpy.types.Object]:
    """Split a mesh into one object per region name (face attribute order).

    Custom split normals are stored first so the seams between regions shade
    exactly like the unsplit mesh.
    """
    import bmesh

    mesh = obj.data
    # Freeze the smooth normals of the whole mesh into custom normals.
    normals = np.zeros((len(mesh.loops), 3), dtype=np.float32)
    mesh.corner_normals.foreach_get("vector", normals.ravel())
    mesh.normals_split_custom_set(normals.tolist())
    out = {}
    names = sorted(set(regions))
    for name in names:
        dup = obj.copy()
        dup.data = obj.data.copy()
        dup.name = f"{obj.name}_{name}"
        dup.data.name = dup.name
        for c in obj.users_collection:
            c.objects.link(dup)
        bm = bmesh.new()
        bm.from_mesh(dup.data)
        bm.faces.ensure_lookup_table()
        kill = [f for f, r in zip(bm.faces, regions) if r != name]
        bmesh.ops.delete(bm, geom=kill, context="FACES")
        loose = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(dup.data)
        bm.free()
        out[name] = dup
    return out
