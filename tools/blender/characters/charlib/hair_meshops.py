"""Triangle-mesh operations in numpy for the Meshy-derived hair
(prepare_hair_meshy.py, charlib/hair_meshy.py): adjacency, Laplacians,
Taubin smoothing, normals, geodesic distances, seeded watershed, submeshes.

Everything is deterministic (no random numbers, stable sorts), so a build
from the same source mesh gives the same file.
"""

from __future__ import annotations

import heapq

import numpy as np


def edges(F: np.ndarray) -> np.ndarray:
    """Unique undirected edges (n, 2), sorted rows, of a triangle list."""
    E = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    E = np.sort(E, axis=1)
    return np.unique(E, axis=0)


def boundary_edges(F: np.ndarray) -> np.ndarray:
    E = np.sort(np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    u, c = np.unique(E, axis=0, return_counts=True)
    return u[c == 1]


def boundary_vertices(F: np.ndarray, n: int) -> np.ndarray:
    b = np.zeros(n, dtype=bool)
    be = boundary_edges(F)
    b[be.ravel()] = True
    return b


def csr(n: int, E: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vertex neighbours as (indptr, indices)."""
    a = np.concatenate([E[:, 0], E[:, 1]])
    b = np.concatenate([E[:, 1], E[:, 0]])
    order = np.lexsort((b, a))
    a, b = a[order], b[order]
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(indptr, a + 1, 1)
    return np.cumsum(indptr), b.astype(np.int64)


def degree(n: int, E: np.ndarray) -> np.ndarray:
    d = np.zeros(n)
    np.add.at(d, E[:, 0], 1)
    np.add.at(d, E[:, 1], 1)
    return d


def laplacian(V: np.ndarray, E: np.ndarray, deg: np.ndarray | None = None) -> np.ndarray:
    """Uniform Laplacian: mean of the neighbours minus the vertex."""
    n = len(V)
    if deg is None:
        deg = degree(n, E)
    S = np.zeros_like(V)
    np.add.at(S, E[:, 0], V[E[:, 1]])
    np.add.at(S, E[:, 1], V[E[:, 0]])
    return S / np.maximum(deg, 1)[:, None] - V


def smooth_values(x: np.ndarray, E: np.ndarray, passes: int, keep: float = 0.5) -> np.ndarray:
    """Neighbour averaging of per-vertex values (scalar or vector)."""
    n = len(x)
    deg = degree(n, E)
    v = np.array(x, dtype=np.float64)
    for _ in range(passes):
        S = np.zeros_like(v)
        np.add.at(S, E[:, 0], v[E[:, 1]])
        np.add.at(S, E[:, 1], v[E[:, 0]])
        m = S / np.maximum(deg, 1).reshape((-1,) + (1,) * (v.ndim - 1))
        v = keep * v + (1 - keep) * np.where(deg.reshape((-1,) + (1,) * (v.ndim - 1)) > 0, m, v)
    return v


def taubin(V: np.ndarray, E: np.ndarray, iters: int, lam: float = 0.5, mu: float = -0.53,
           weight: np.ndarray | None = None) -> np.ndarray:
    """Taubin lambda/mu smoothing (no shrinking); `weight` (0..1 per vertex)
    scales each vertex's step (0 = pinned)."""
    deg = degree(len(V), E)
    w = np.ones(len(V)) if weight is None else np.asarray(weight, dtype=np.float64)
    out = V.copy()
    for _ in range(iters):
        out = out + lam * w[:, None] * laplacian(out, E, deg)
        out = out + mu * w[:, None] * laplacian(out, E, deg)
    return out


def face_normals(V: np.ndarray, F: np.ndarray, unit: bool = True) -> np.ndarray:
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    if unit:
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)
    return n


def vertex_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    fn = face_normals(V, F, unit=False)
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, F[:, k], fn)
    return N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-20)


def mean_curvature(V: np.ndarray, F: np.ndarray, E: np.ndarray, N: np.ndarray | None = None) -> np.ndarray:
    """Signed curvature proxy per vertex: the uniform Laplacian along the
    normal over the mean edge length (positive: concave, a valley or crease;
    negative: convex, a ridge or tip)."""
    if N is None:
        N = vertex_normals(V, F)
    L = laplacian(V, E)
    el = np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1)
    deg = degree(len(V), E)
    ml = np.zeros(len(V))
    np.add.at(ml, E[:, 0], el)
    np.add.at(ml, E[:, 1], el)
    ml /= np.maximum(deg, 1)
    return np.einsum("ij,ij->i", L, N) / np.maximum(ml * ml, 1e-20) * np.maximum(ml, 1e-12)


def dijkstra(V: np.ndarray, indptr: np.ndarray, indices: np.ndarray, sources: np.ndarray,
             limit: float = np.inf) -> np.ndarray:
    """Geodesic (edge path) distance from any source vertex."""
    n = len(V)
    d = np.full(n, np.inf)
    heap = []
    for s in np.nonzero(sources)[0] if sources.dtype == bool else sources:
        d[s] = 0.0
        heap.append((0.0, int(s)))
    heapq.heapify(heap)
    while heap:
        dv, v = heapq.heappop(heap)
        if dv > d[v] or dv > limit:
            continue
        p = V[v]
        for u in indices[indptr[v]:indptr[v + 1]]:
            nd = dv + float(np.sqrt(((V[u] - p) ** 2).sum()))
            if nd < d[u]:
                d[u] = nd
                heapq.heappush(heap, (nd, int(u)))
    return d


def watershed(cost: np.ndarray, indptr: np.ndarray, indices: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Seeded region growing by priority flood: vertices with a label >= 0
    are seeds; every other vertex takes the label of the front that reaches
    it first when fronts advance through the lowest `cost` first (so fronts
    meet on the highest ridges of `cost`). Ties break by vertex index."""
    out = labels.copy()
    heap = []
    for v in np.nonzero(out >= 0)[0]:
        for u in indices[indptr[v]:indptr[v + 1]]:
            if out[u] < 0:
                heap.append((float(cost[u]), int(u), int(out[v])))
    heapq.heapify(heap)
    while heap:
        c, v, lab = heapq.heappop(heap)
        if out[v] >= 0:
            continue
        out[v] = lab
        for u in indices[indptr[v]:indptr[v + 1]]:
            if out[u] < 0:
                heapq.heappush(heap, (float(cost[u]), int(u), lab))
    return out


def components(n: int, E: np.ndarray) -> np.ndarray:
    """Connected component label per vertex (union-find, path halving)."""
    parent = np.arange(n)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in E:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb
    return np.array([find(i) for i in range(n)])


def submesh(V: np.ndarray, F: np.ndarray, keep_faces: np.ndarray, *extra):
    """Faces `keep_faces` (bool) with only the vertices they use; extra
    per-vertex arrays are cut the same way. Returns (V, F, old index, *extra)."""
    Fk = F[keep_faces]
    used = np.unique(Fk.ravel())
    remap = -np.ones(len(V), dtype=np.int64)
    remap[used] = np.arange(len(used))
    out = [V[used], remap[Fk], used]
    for x in extra:
        out.append(np.asarray(x)[used])
    return tuple(out)


def majority(labels: np.ndarray, E: np.ndarray, passes: int = 2, nlab: int | None = None) -> np.ndarray:
    """Mode filter of small integer labels over the 1-ring (speckle removal)."""
    lab = labels.astype(np.int64).copy()
    k = int(lab.max()) + 1 if nlab is None else nlab
    for _ in range(passes):
        votes = np.zeros((len(lab), k))
        np.add.at(votes, (np.arange(len(lab)), lab), 1.5)
        np.add.at(votes, (E[:, 0], lab[E[:, 1]]), 1.0)
        np.add.at(votes, (E[:, 1], lab[E[:, 0]]), 1.0)
        lab = np.argmax(votes, axis=1)
    return lab


def barycentric(P: np.ndarray, A: np.ndarray, B: np.ndarray, C: np.ndarray) -> np.ndarray:
    v0, v1, v2 = B - A, C - A, P - A
    d00 = np.einsum("ij,ij->i", v0, v0)
    d01 = np.einsum("ij,ij->i", v0, v1)
    d11 = np.einsum("ij,ij->i", v1, v1)
    d20 = np.einsum("ij,ij->i", v2, v0)
    d21 = np.einsum("ij,ij->i", v2, v1)
    den = np.maximum(d00 * d11 - d01 * d01, 1e-30)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    u = 1.0 - v - w
    b = np.clip(np.stack([u, v, w], axis=1), 0.0, 1.0)
    return b / np.maximum(b.sum(axis=1, keepdims=True), 1e-12)


def nearest_on(V: np.ndarray, F: np.ndarray, P: np.ndarray):
    """For points P: (face index, barycentric weights) of the nearest point
    on the mesh (V, F) (BVH)."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    tree = BVHTree.FromPolygons(V.tolist(), F.tolist(), all_triangles=True)
    fi = np.zeros(len(P), dtype=np.int64)
    loc = np.zeros_like(P)
    for i, p in enumerate(P):
        lc, _, idx, _ = tree.find_nearest(Vector(tuple(p)))
        fi[i] = idx
        loc[i] = lc
    tri = F[fi]
    bc = barycentric(loc, V[tri[:, 0]], V[tri[:, 1]], V[tri[:, 2]])
    return fi, bc


def interpolate(values: np.ndarray, F: np.ndarray, fi: np.ndarray, bc: np.ndarray) -> np.ndarray:
    tri = F[fi]
    vals = np.asarray(values)
    if vals.ndim == 1:
        return (vals[tri] * bc).sum(axis=1)
    return np.einsum("ij,ijk->ik", bc, vals[tri])
