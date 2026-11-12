"""Castle D1: the deterministic vertex-AO bake of kitted cells
(docs/art/castle-kit.md "Shading: the AO bake"; the numbers are grid.py's "Castle D1"
block). Called by `assemble.py` (its `# Castle D1 (Bake)` block) after a
cell's meshes are merged; it writes every kit mesh's `COLOR_0` as one grey
value x per corner (r = g = b, the shader's shade, never a colour):

    x = clamp(ao ^ AO_EXPONENT * g, SHADE_MIN, 1), byte = round(x * 255)

- **Occlusion `ao`** per drawn vertex (position and normal: `_mesh` already
  splits vertices by normal), LOD1 nodes included: `rays` cosine-weighted
  Hammersley directions (`grid.hammersley`) in the vertex's tangent frame,
  from the vertex plus `AO_OFFSET` along its normal; a hit at d < R occludes
  `grid.ao_weight(d, R)`; ao = 1 - mean weight. R and the ray count are
  `CellPlan.shade` (by budget kind, `castle_kit.json` may override them).
- **Occluders**: the cell's drawn LOD0 faces but `AO_NOT_OCCLUDERS`
  (glass, sky, banners), the candle groups and the props atlas's glow
  faces; plus the Planner faces (greybox render polygons, `Builder.plan0`)
  of the cells joined to this one, within R of the join (the door's
  opening): never a neighbour's kit, so a cell's bake depends on the layout
  and its own kit only.
- **Receivers**: every kit mesh (`castle_*`) but `AO_NOT_RECEIVERS` (glass,
  sky), whose corners get x = 1, as do glow faces. Meshes of greybox
  materials a kitted cell keeps (its props.json boxes) get no `COLOR_0`.
- **Long edges** (`edge_samples`, review fix): x is interpolated along a
  vertex's edges, so a vertex whose edge runs metres (the budget left a
  wall above a door head uncut) takes the hat-weighted mean of the
  occlusion at itself and at `EDGE_SAMPLES` along each edge longer than
  `LONG_EDGE`, with points buried in a solid (under a moulding) left out;
  without it one dark corner point smeared a dark band up the whole wall.
- **Weld and smooth**: vertices at the same position whose normals are
  within `SHADE_WELD_DEG` are one cluster (single linkage); the cluster
  takes its vertices' mean (weighted by each vertex's samples), then
  `SHADE_SMOOTH_PASSES` Laplacian passes of `SHADE_SMOOTH_WEIGHT` along the
  clusters' mesh edges (clusters already filtered along long edges keep
  their value): no seams between pieces, meshes or LODs.
- **Gradient `g`** per corner: the face's value ramp (`palette.VALUE_RAMPS`,
  greyed, by `WFace.ramp`) over the cell's wall heights, the `wall` ramp's
  bottom raised to `SHADE_WALL_BOTTOM`; 1 without a ramp.
- **Ray casting**: Blender's geometry-nodes Raycast node over a point cloud
  of ray origins (one point per ray, its direction an attribute) against a
  mesh of the occluders: the same BVH ray cast as `mathutils.bvhtree`, but
  in C++ over every ray of a chunk at once (about 0.7 us a ray against 2 us
  through `BVHTree.ray_cast`). Pure numpy around it; no Cycles. The same
  inputs give the same bytes (the occluder mesh, the ray order and the
  arithmetic are fixed; each ray is cast on its own). Each ray starts
  `BACKSTEP` back along itself and a back-face hit closer than `SELF_HIT`
  is dropped, so a vertex lying in another face's plane (a floor vertex at
  a wall's foot) is occluded by that face toward it and never away from it,
  whatever the rounding. Cross-checked once against Cycles' AO bake on a
  wall corner (R 2 m): mean difference 0.04; the weight 1 - (d / R)^2
  reads up to 0.13 lighter than Cycles' unweighted count about 1 m from
  the wall.
- Without `bpy` (the lint under plain Python) `available()` is False and
  `bake` writes the gradient alone (ao = 1); the export always bakes.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from castle_kit import grid as G
from castle_kit.pieces import geom

RAY_CHUNK = 4_000_000  # rays cast per geometry-nodes evaluation (memory: about 100 MB a chunk)
WELD_KEY = 1e5  # positions weld when they agree to 0.01 mm
GLOW_TILES = ("flame", "brew", "glow")  # the props atlas's glowing tiles (world/kit.py TILES: glow True)
NODE_GROUP = "castle_d1_ao_cast"
SELF_HIT = 2.0 * G.AO_OFFSET  # back-face hits closer than this are the ray's own surface (Caster.cast)
# Each ray starts this far back along itself (its distances measured from the
# true origin): an origin lying in another face's plane (a floor vertex at a
# wall's foot) is then clearly in front of the face for rays toward it (a hit)
# and clearly behind it for rays away from it (a back-face hit, dropped), never
# left to rounding. Far below AO_OFFSET, so no ray reaches its own surface.
BACKSTEP = 1e-3
# Long-edge pre-filter (castle D1 review, `edge_samples`): edges longer than
# LONG_EDGE (m) add samples this far (m) from each end, below half the edge,
# cast with the cell's ray count divided by EDGE_RAYS_DIV (at least 16).
LONG_EDGE = 1.0
EDGE_SAMPLES = (0.5, 1.0, 2.0, 3.0)
EDGE_RAYS_DIV = 2
BURIED = 0.02  # an ao this low: the point lies inside a solid (under a moulding)


def available() -> bool:
    """Can this Python cast rays (inside Blender)?"""
    try:
        import bpy  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class Stats:
    baked: bool = False  # rays were cast (False: the gradient alone, no bpy)
    radius: float = 0.0
    rays: int = 0
    vertices: int = 0  # vertices that cast rays
    casts: int = 0  # rays cast
    occluder_faces: int = 0
    neighbour_faces: int = 0  # Planner faces of joined cells among them
    edge_samples: int = 0  # extra sample points along long edges (edge_samples)
    clusters: int = 0
    seconds: float = 0.0
    ao_mean: float = 1.0
    x_min: float = 1.0
    bytes_hist: dict = field(default_factory=dict)  # x byte -> corners (coarse, 16 bins)


# -- which faces take part -------------------------------------------------------------------------


def is_glow(f) -> bool:
    """A props atlas face on a glowing tile (its texels ignore light, 15.6)."""
    return f.mode == "kit" and bool(f.arg) and f.arg.split(":", 1)[0] in GLOW_TILES


def occludes(f) -> bool:
    if f.lod == "lod1" or f.material in G.AO_NOT_OCCLUDERS or is_glow(f):
        return False
    # The hourglass sand moves and the cup banners are mostly hidden: neither occludes.
    return not (f.group or "").startswith("candles_") and not G.is_sand_group(f.group) \
        and not G.is_cup_banner_group(f.group)


def receives(material: str) -> bool:
    return material.startswith("castle_") and material not in G.AO_NOT_RECEIVERS


def neighbour_polys(builder, cell: str, radius: float) -> list:
    """The Planner faces (`Builder.plan0`, no glow panes) of the cells joined to
    `cell`, within `radius` of the join's opening (the contact edge from the
    lower floor to the higher wall top), in a fixed order."""
    import layout_lib as L

    layout = builder.layout
    mine = [s for s in layout.surfaces if s.cell == cell]
    boxes = []
    for a in mine:
        for b in layout.surfaces:
            if b.cell == cell or not L.connected(a, b):
                continue
            x0, z0, x1, z1 = L.contact(a, b)
            y0 = min(a.floor_min, b.floor_min)
            y1 = max(G.wall_top(a), G.wall_top(b))
            boxes.append((b.cell, (x0, y0, z0), (x1, y1, z1)))
    out = []
    cells = sorted({c for c, _, _ in boxes})
    for (c, mat), polys in sorted(builder.plan0.polys.items()):
        if c not in cells or mat.startswith("glow_"):
            continue
        near = [(lo, hi) for bc, lo, hi in boxes if bc == c]
        for poly in polys:
            plo = [min(p[k] for p in poly) for k in range(3)]
            phi = [max(p[k] for p in poly) for k in range(3)]
            for lo, hi in near:
                gap = math.sqrt(sum(max(lo[k] - phi[k], 0.0, plo[k] - hi[k]) ** 2 for k in range(3)))
                if gap <= radius:
                    out.append([tuple(p) for p in poly])
                    break
    return out


# -- the ray cast -------------------------------------------------------------------------------


def _node_group():
    import bpy

    tree = bpy.data.node_groups.get(NODE_GROUP)
    if tree is not None:
        return tree
    # The target object and the ray length are set on the nodes themselves
    # (Caster.cast): Blender 5's modifier inputs are not ID properties.
    tree = bpy.data.node_groups.new(NODE_GROUP, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = tree.nodes, tree.links
    gi, go = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    info = nodes.new("GeometryNodeObjectInfo")
    info.name = "target"
    cast = nodes.new("GeometryNodeRaycast")
    cast.name = "cast"
    direction = nodes.new("GeometryNodeInputNamedAttribute")
    direction.data_type = "FLOAT_VECTOR"
    direction.inputs["Name"].default_value = "ray"
    store = nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "FLOAT"
    store.inputs["Name"].default_value = "hit"
    miss = nodes.new("GeometryNodeSwitch")
    miss.input_type = "FLOAT"
    miss.inputs["False"].default_value = -1.0
    links.new(info.outputs["Geometry"], cast.inputs["Target Geometry"])
    links.new(direction.outputs["Attribute"], cast.inputs["Ray Direction"])
    links.new(cast.outputs["Is Hit"], miss.inputs["Switch"])
    links.new(cast.outputs["Hit Distance"], miss.inputs["True"])
    links.new(gi.outputs["Geometry"], store.inputs["Geometry"])
    links.new(miss.outputs[0], store.inputs["Value"])
    # The hit face's facing along the ray (hit normal . direction > 0: seen from
    # behind): a back-face hit closer than SELF_HIT is the ray's own surface.
    facing = nodes.new("ShaderNodeVectorMath")
    facing.operation = "DOT_PRODUCT"
    links.new(cast.outputs["Hit Normal"], facing.inputs[0])
    links.new(direction.outputs["Attribute"], facing.inputs[1])
    store2 = nodes.new("GeometryNodeStoreNamedAttribute")
    store2.data_type = "FLOAT"
    store2.inputs["Name"].default_value = "facing"
    links.new(store.outputs["Geometry"], store2.inputs["Geometry"])
    links.new(facing.outputs["Value"], store2.inputs["Value"])
    links.new(store2.outputs["Geometry"], go.inputs["Geometry"])
    return tree


class Caster:
    """Casts batches of rays against one occluder mesh (geometry nodes)."""

    def __init__(self, polys):
        import bpy

        verts, faces = [], []
        for poly in polys:
            faces.append(list(range(len(verts), len(verts) + len(poly))))
            verts += [tuple(float(c) for c in p) for p in poly]
        self.mesh = bpy.data.meshes.new("castle_d1_occluders")
        self.mesh.from_pydata(verts, [], faces)
        self.target = bpy.data.objects.new("castle_d1_occluders", self.mesh)
        bpy.context.scene.collection.objects.link(self.target)
        self.tree = _node_group()
        self.tree.nodes["target"].inputs["Object"].default_value = self.target

    def cast(self, origins: np.ndarray, dirs: np.ndarray, length: float) -> np.ndarray:
        """Hit distances (float32; -1 where nothing is hit within `length`, and
        where the hit is a face seen from behind closer than SELF_HIT: a ray
        whose origin lies in another face's plane, a floor vertex at a wall's
        foot, would otherwise hit that face at d = 0 on either side of it, by
        rounding)."""
        import bpy

        out = np.empty(len(origins), dtype=np.float32)
        for s in range(0, len(origins), RAY_CHUNK):
            o, d = origins[s:s + RAY_CHUNK], dirs[s:s + RAY_CHUNK]
            me = bpy.data.meshes.new("castle_d1_rays")
            me.vertices.add(len(o))
            me.vertices.foreach_set("co", np.ascontiguousarray(o, dtype=np.float32).ravel())
            attr = me.attributes.new("ray", "FLOAT_VECTOR", "POINT")
            attr.data.foreach_set("vector", np.ascontiguousarray(d, dtype=np.float32).ravel())
            ob = bpy.data.objects.new("castle_d1_rays", me)
            bpy.context.scene.collection.objects.link(ob)
            self.tree.nodes["cast"].inputs["Ray Length"].default_value = float(length)
            mod = ob.modifiers.new("ao", "NODES")
            mod.node_group = self.tree
            dg = bpy.context.evaluated_depsgraph_get()
            dg.update()
            ev = ob.evaluated_get(dg)
            buf = np.empty(len(o), dtype=np.float32)
            ev.data.attributes["hit"].data.foreach_get("value", buf)
            back = np.empty(len(o), dtype=np.float32)
            ev.data.attributes["facing"].data.foreach_get("value", back)
            buf[(buf >= 0.0) & (buf < SELF_HIT) & (back > 0.0)] = -1.0
            out[s:s + len(o)] = buf
            bpy.data.objects.remove(ob)
            bpy.data.meshes.remove(me)
        return out

    def close(self) -> None:
        import bpy

        bpy.data.objects.remove(self.target)
        bpy.data.meshes.remove(self.mesh)


def tangent_frames(normals: np.ndarray):
    """(tangent, bitangent) per unit normal: a fixed frame (the reference axis y,
    or x for normals near y)."""
    ref = np.where(np.abs(normals[:, 1:2]) < 0.9, np.array([[0.0, 1.0, 0.0]]), np.array([[1.0, 0.0, 0.0]]))
    tx = np.cross(ref, normals)
    tx /= np.linalg.norm(tx, axis=1, keepdims=True)
    ty = np.cross(normals, tx)
    return tx, ty


def occlusion(caster, points: np.ndarray, normals: np.ndarray, radius: float, rays: int) -> np.ndarray:
    """ao (1 open .. 0) per point: 1 - the mean occlusion weight of its rays."""
    dirs = np.array(G.hammersley(rays), dtype=np.float64)
    out = np.empty(len(points), dtype=np.float64)
    step = max(1, RAY_CHUNK // rays)
    for s in range(0, len(points), step):
        p, n = points[s:s + step], normals[s:s + step]
        tx, ty = tangent_frames(n)
        d = tx[:, None, :] * dirs[None, :, 0:1] + ty[:, None, :] * dirs[None, :, 1:2] + n[:, None, :] * dirs[None, :, 2:3]
        d = d.reshape(-1, 3)
        o = np.repeat(p + n * G.AO_OFFSET, rays, axis=0) - d * BACKSTEP
        hit = caster.cast(o, d, radius + BACKSTEP).astype(np.float64).reshape(len(p), rays)
        hit = np.where(hit >= 0.0, np.maximum(hit - BACKSTEP, 0.0), -1.0)
        w = np.where((hit >= 0.0) & (hit < radius), 1.0 - (np.maximum(hit, 0.0) / radius) ** 2, 0.0)
        out[s:s + step] = 1.0 - w.mean(axis=1)
    return out


def edge_samples(meshes, index: dict, P: np.ndarray, N: np.ndarray):
    """Extra sample points along each receiving vertex's long edges (castle D1
    review): a vertex's x is interpolated linearly along its edges, so the
    occlusion of one point (a door head's moulding corner) spread up a 7 m
    edge as a dark band. The vertex instead takes the hat-weighted mean of the
    occlusion at itself and at `EDGE_SAMPLES` metres along each incident edge
    longer than `LONG_EDGE` (only below half the edge; weight 1 - t / length):
    a pre-filter matched to the mesh's own resolution. Short edges (the edge
    loops, mouldings) add nothing. Returns (points, normals, owner index,
    weight), in mesh, face and edge order (deterministic)."""
    seen = set()
    sp, sn, sk, sw = [], [], [], []
    for mi, md in enumerate(meshes):
        if not receives(md.material):
            continue
        for fi, f in enumerate(md.faces):
            if is_glow(md.src_faces[fi]):
                continue
            for a, b in zip(f, f[1:] + f[:1]):
                key = (mi, min(a, b), max(a, b))
                if key in seen:
                    continue
                seen.add(key)
                ka, kb = index.get((mi, a)), index.get((mi, b))
                if ka is None or kb is None:
                    continue
                d = P[kb] - P[ka]
                length = float(np.linalg.norm(d))
                if length <= LONG_EDGE:
                    continue
                u = d / length
                for k, sign in ((ka, 1.0), (kb, -1.0)):
                    for t in EDGE_SAMPLES:
                        if t >= 0.5 * length:
                            break
                        sp.append(P[k] + sign * t * u)
                        sn.append(N[k])
                        sk.append(k)
                        sw.append(1.0 - t / length)
    return (np.array(sp, dtype=np.float64).reshape(-1, 3), np.array(sn, dtype=np.float64).reshape(-1, 3),
            np.array(sk, dtype=np.int64), np.array(sw, dtype=np.float64))


# -- weld and smooth ----------------------------------------------------------------------------


def weld(points: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """Cluster id per vertex: the same position (to 1/WELD_KEY m) and normals
    within SHADE_WELD_DEG, single linkage; ids in first-vertex order."""
    n = len(points)
    parent = np.arange(n)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    keys = np.round(points * WELD_KEY).astype(np.int64)
    order = np.lexsort((keys[:, 2], keys[:, 1], keys[:, 0]))
    sk = keys[order]
    brk = np.flatnonzero(np.any(sk[1:] != sk[:-1], axis=1)) + 1
    cos = math.cos(math.radians(G.SHADE_WELD_DEG))
    for grp in np.split(order, brk):
        if len(grp) < 2:
            continue
        g = np.sort(grp)
        nn = normals[g] @ normals[g].T
        for a in range(len(g)):
            for b in range(a + 1, len(g)):
                if nn[a, b] >= cos - 1e-9:
                    ra, rb = find(g[a]), find(g[b])
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([find(i) for i in range(n)])
    _, ids = np.unique(roots, return_inverse=True)
    return ids


def smooth(values: np.ndarray, edges: np.ndarray, passes: int, weight: float, fixed=None) -> np.ndarray:
    """Laplacian passes over an undirected edge list (cluster ids); `fixed`
    (a mask) clusters keep their value but still feed their neighbours."""
    v = values.copy()
    if len(edges) == 0:
        return v
    a, b = edges[:, 0], edges[:, 1]
    deg = np.bincount(a, minlength=len(v)) + np.bincount(b, minlength=len(v))
    for _ in range(passes):
        acc = np.bincount(a, weights=v[b], minlength=len(v)) + np.bincount(b, weights=v[a], minlength=len(v))
        mean = np.divide(acc, deg, out=v.copy(), where=deg > 0)
        step = v + weight * (mean - v)
        v = step if fixed is None else np.where(fixed, v, step)
    return v


# -- the gradient -------------------------------------------------------------------------------


def gradient(ramp: str | None, y: float, lo_hi) -> float:
    """g: the face's greyed value ramp at height y (the wall ramp's bottom at
    SHADE_WALL_BOTTOM); 1 without one."""
    import palette

    if not ramp or ramp == "-":
        return 1.0
    spec = palette.VALUE_RAMPS.get(ramp)
    if spec is None:
        return 1.0
    mode, bottom, top, exp = spec
    lo, hi = palette.WORLD_RAMP if mode == "world" else lo_hi
    t = 0.0 if hi - lo < 1e-6 else min(1.0, max(0.0, (y - lo) / (hi - lo)))
    t = t ** exp
    b = G.SHADE_WALL_BOTTOM if ramp == "wall" else 0.2126 * bottom[0] + 0.7152 * bottom[1] + 0.0722 * bottom[2]
    u = 0.2126 * top[0] + 0.7152 * top[1] + 0.0722 * top[2]
    return round(b + (u - b) * t, 4)


# -- the bake -------------------------------------------------------------------------------------


def _normals(md) -> list:
    """Each vertex's normal: its faces' (they agree: `_mesh` splits by normal)."""
    out = [None] * len(md.verts)
    for f in md.faces:
        n = geom.unit(geom.newell([md.verts[i] for i in f]))
        for i in f:
            if out[i] is None:
                out[i] = n
    return [n if n is not None else (0.0, 1.0, 0.0) for n in out]


def bake(cb, builder, lo_hi, cast: bool | None = None) -> Stats:
    """Writes every kit mesh's COLOR_0 (`MeshData.colors`, per face per corner,
    byte / 255 grey) of the cell build `cb`. `lo_hi`: the wall ramps' height
    range. `cast`: cast rays (default: when bpy is available)."""
    t0 = time.perf_counter()
    cp = cb.plan
    st = Stats(radius=cp.shade.radius, rays=cp.shade.rays)
    cast = available() if cast is None else cast
    meshes = [md for md in cb.meshes.values()] + [md for md, _, _ in cb.lod_meshes.values()]
    meshes = sorted((md for md in meshes if md.material.startswith("castle_")), key=lambda m: m.name)
    # Receiving vertices of every kit mesh, in mesh and vertex order.
    pts, nrm, owner = [], [], []
    for mi, md in enumerate(meshes):
        if not receives(md.material):
            continue
        wf = md.src_faces
        used = set()
        for fi, f in enumerate(md.faces):
            if not is_glow(wf[fi]):
                used.update(f)
        normals = _normals(md)
        for vi in sorted(used):
            pts.append(md.verts[vi])
            nrm.append(normals[vi])
            owner.append((mi, vi))
    P = np.array(pts, dtype=np.float64).reshape(-1, 3)
    N = np.array(nrm, dtype=np.float64).reshape(-1, 3)
    st.vertices = len(P)
    ao = np.ones(len(P))
    weight = np.ones(len(P))  # each vertex's share in its weld cluster (its footprint)
    filtered = np.zeros(len(P), dtype=bool)  # took long-edge samples
    if cast and len(P):
        occ = [f.pts for f in cb.faces if occludes(f)]
        nb = neighbour_polys(builder, cb.cell, cp.shade.radius)
        st.occluder_faces, st.neighbour_faces = len(occ) + len(nb), len(nb)
        index0 = {ov: k for k, ov in enumerate(owner)}
        SP, SN, SK, SW = edge_samples(meshes, index0, P, N)
        caster = Caster(occ + nb)
        try:
            ao = occlusion(caster, P, N, cp.shade.radius, cp.shade.rays)
            if len(SP):
                srays = max(16, cp.shade.rays // EDGE_RAYS_DIV)
                ao_s = occlusion(caster, SP, SN, cp.shade.radius, srays)
                st.casts += len(SP) * srays
                st.edge_samples = len(SP)
                # The vertex's value is the hat-weighted mean along its long edges;
                # points buried in a solid (under a moulding: never seen) do not count.
                live = (ao_s >= BURIED).astype(np.float64)
                ws = np.bincount(SK, weights=SW * live, minlength=len(P))
                acc = np.bincount(SK, weights=SW * live * ao_s, minlength=len(P))
                own = np.where((ao >= BURIED) | (ws == 0.0), 1.0, 0.0)
                ao = np.where(ws > 0.0, (own * ao + acc) / (own + ws), ao)
                weight = 1.0 + ws
                filtered = ws > 0.0
        finally:
            caster.close()
        st.baked = True
        st.casts += len(P) * cp.shade.rays
    # Weld, then smooth along the clusters' edges.
    ids = weld(P, N) if len(P) else np.zeros(0, dtype=np.int64)
    nc = int(ids.max()) + 1 if len(ids) else 0
    st.clusters = nc
    sums = np.bincount(ids, weights=ao * weight, minlength=nc)
    cnt = np.bincount(ids, weights=weight, minlength=nc)
    cl = sums / np.maximum(cnt, 1e-9)
    # Clusters already filtered along their long edges keep their value: one
    # smoothing step toward a buried neighbour would undo the filter.
    fixed = np.bincount(ids, weights=filtered.astype(np.float64), minlength=nc) > 0.0
    index = {ov: k for k, ov in enumerate(owner)}
    edges = set()
    for mi, md in enumerate(meshes):
        if not receives(md.material):
            continue
        for f in md.faces:
            for a, b in zip(f, f[1:] + f[:1]):
                ka, kb = index.get((mi, a)), index.get((mi, b))
                if ka is None or kb is None:
                    continue
                ca, cb_ = int(ids[ka]), int(ids[kb])
                if ca != cb_:
                    edges.add((min(ca, cb_), max(ca, cb_)))
    e = np.array(sorted(edges), dtype=np.int64).reshape(-1, 2)
    cl = smooth(cl, e, G.SHADE_SMOOTH_PASSES, G.SHADE_SMOOTH_WEIGHT, fixed)
    vertex_ao = cl[ids] if len(ids) else ao
    st.ao_mean = round(float(vertex_ao.mean()), 4) if len(vertex_ao) else 1.0
    # Combine with the gradient per corner and write the grey bytes.
    hist: dict = {}
    xmin = 1.0
    for mi, md in enumerate(meshes):
        rec = receives(md.material)
        cols = []
        for fi, f in enumerate(md.faces):
            wf = md.src_faces[fi]
            glow = is_glow(wf)
            row = []
            for vi in f:
                if not rec or glow:
                    byte = 255
                else:
                    a = float(vertex_ao[index[(mi, vi)]])
                    x = G.shade_value(a, gradient(wf.ramp, md.verts[vi][1], lo_hi))
                    byte = G.shade_byte(x)
                    xmin = min(xmin, byte / 255.0)
                hist[byte // 16] = hist.get(byte // 16, 0) + 1
                v = byte / 255.0
                row.append((v, v, v))
            cols.append(row)
        md.colors = cols
    st.x_min = round(xmin, 4)
    st.bytes_hist = dict(sorted(hist.items()))
    st.seconds = round(time.perf_counter() - t0, 2)
    return st
