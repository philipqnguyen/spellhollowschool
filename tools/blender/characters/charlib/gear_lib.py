"""Toolkit for clothing (and hair) built against a base body.

Clothing is evaluated once per body shape (the base and every build key), so
each item gets the same build shape keys as the body and keeps its distance to
the skin in every build. Three primitives:

* `shell`: a subset of the body's faces pushed out along the body normals by
  an offset (the cage distance of docs/art/base-body-spec.md section 4). The
  item inherits the body's edge loops (so it bends like the skin) and its
  weights.
* `strip`: rings grown from an existing ordered loop of item vertices
  (hems, cuffs, collars, skirts, sleeves): each new ring is a function of the
  loop's position in the current shape.
* `thicken`: duplicates a face set inward as a lining (reversed faces) joined
  by rims along its open edges, so open garments show their inside colour.

Masks are dye channels: PRIMARY / SECONDARY / TRIM, or NONE for fixed
(undyed) colours from the atlas.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import skeleton as K
from .meshutil import MeshData
from .proportions import BUILDS

PRIMARY = (1.0, 0.0, 0.0)
SECONDARY = (0.0, 1.0, 0.0)
TRIM = (0.0, 0.0, 1.0)
NONE = (0.0, 0.0, 0.0)
SHAPES = ["base"] + list(BUILDS)


def vertex_normals(verts: np.ndarray, faces) -> np.ndarray:
    n = np.zeros_like(verts)
    for f in faces:
        p = verts[list(f)]
        fn = np.zeros(3)
        for i in range(len(f)):
            fn += np.cross(p[i], p[(i + 1) % len(f)])
        n[list(f)] += fn
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


@dataclass
class Shell:
    body_to_gear: dict[int, int]
    faces: list[int]  # gear face indices

    def ids(self, body_ids) -> list[int]:
        return [self.body_to_gear[int(i)] for i in body_ids]


@dataclass
class GearBuilder:
    body_md: MeshData
    layout: object
    body: object
    shapes: list[str] = field(default_factory=lambda: list(SHAPES))

    def __post_init__(self):
        self.P = {"base": self.body_md.verts}
        for s in self.shapes[1:]:
            self.P[s] = self.body_md.keys[s]
        self.N = {s: vertex_normals(self.P[s], self.body_md.faces) for s in self.shapes}
        self.pos: dict[str, list[np.ndarray]] = {s: [] for s in self.shapes}
        self.weights: list[dict[str, float]] = []
        self.faces: list[tuple[int, ...]] = []
        self.swatch: list[str] = []
        self.mask: list[tuple[float, float, float]] = []
        self.seed_faces: set[int] = set()
        self.n = 0
        self.joints = K.joint_map(self.body)
        self.remap = self.body_md.old_to_new

    # ------------------------------------------------------------- basics
    def add(self, pts: dict[str, np.ndarray], weights: list[dict[str, float]]) -> np.ndarray:
        k = len(weights)
        for s in self.shapes:
            self.pos[s].append(np.asarray(pts[s], dtype=np.float64).reshape(k, 3))
        self.weights.extend(weights)
        ids = np.arange(self.n, self.n + k)
        self.n += k
        return ids

    def face(self, ids, swatch: str, mask, seed: bool = False) -> int:
        self.faces.append(tuple(int(i) for i in ids))
        self.swatch.append(swatch)
        self.mask.append(tuple(mask))
        if seed:
            self.seed_faces.add(len(self.faces) - 1)
        return len(self.faces) - 1

    def positions(self, shape: str = "base") -> np.ndarray:
        return np.vstack(self.pos[shape]) if self.pos[shape] else np.zeros((0, 3))

    # -------------------------------------------------------- body lookup
    def bv(self, layout_ids) -> np.ndarray:
        """Body mesh ids of layout vertex ids (-1 where the vertex was dropped)."""
        return self.remap[np.asarray(layout_ids, dtype=np.int64)]

    def ring(self, tube: str, k: int) -> np.ndarray:
        return self.bv(self.layout.tubes[tube].idx[k])

    def trunk_row(self, r: int) -> np.ndarray:
        return self.bv([self.layout.tv(r, c) for c in range(40)])

    # -------------------------------------------------------- primitives
    def shell(self, face_ok: Callable[[str, np.ndarray], bool], offset: Callable[[str, np.ndarray], np.ndarray],
              paint: Callable[[str, np.ndarray], tuple[str, tuple]]) -> Shell:
        """Offset copy of the body faces whose (tag, base centre) pass `face_ok`.
        `offset(shape, body_ids)` gives per-vertex offsets in metres."""
        bmd = self.body_md
        chosen = []
        for fi, f in enumerate(bmd.faces):
            c = bmd.verts[list(f)].mean(axis=0)
            if face_ok(bmd.tags[fi], c):
                chosen.append(fi)
        used = sorted({v for fi in chosen for v in bmd.faces[fi]})
        used_arr = np.array(used, dtype=np.int64)
        pts = {}
        for s in self.shapes:
            off = np.asarray(offset(s, used_arr), dtype=np.float64)
            pts[s] = self.P[s][used_arr] + self.N[s][used_arr] * off[:, None]
        ids = self.add(pts, [dict(bmd.weights[i]) for i in used])
        m = {int(b): int(g) for b, g in zip(used, ids)}
        out = []
        for fi in chosen:
            f = bmd.faces[fi]
            c = bmd.verts[list(f)].mean(axis=0)
            sw, mask = paint(bmd.tags[fi], c)
            out.append(self.face([m[v] for v in f], sw, mask, seed=True))
        return Shell(m, out)

    def strip(self, ring0: list[int], make: Callable[[str, np.ndarray, int], np.ndarray], n_rings: int,
              paint: Callable[[int, int], tuple[str, tuple]], closed: bool = True,
              weight: Callable[[int, int, dict], dict] | None = None, flip: bool = False,
              skip: Callable[[int, int], bool] | None = None) -> list[np.ndarray]:
        """Grow `n_rings` rings from the ordered loop `ring0` (item vertex ids).
        `make(shape, ring0_positions, k)` returns ring k (k = 1..n_rings)."""
        ring0 = np.asarray(ring0, dtype=np.int64)
        rings = [ring0]
        base_w = [self.weights[i] for i in ring0]
        for k in range(1, n_rings + 1):
            pts = {s: make(s, self.positions(s)[ring0], k) for s in self.shapes}
            ws = [dict(weight(k, c, base_w[c])) if weight else dict(base_w[c]) for c in range(len(ring0))]
            rings.append(self.add(pts, ws))
        self.connect(rings, paint, closed, flip, skip)
        return rings

    def connect(self, rings: list[np.ndarray], paint: Callable[[int, int], tuple[str, tuple]], closed: bool = True,
                flip: bool = False, skip: Callable[[int, int], bool] | None = None) -> None:
        n = len(rings[0])
        cols = n if closed else n - 1
        for k in range(len(rings) - 1):
            a, b = rings[k], rings[k + 1]
            for c in range(cols):
                if skip is not None and skip(k, c):
                    continue
                c1 = (c + 1) % n
                sw, mask = paint(k, c)
                f = (a[c], a[c1], b[c1], b[c])
                self.face(tuple(reversed(f)) if flip else f, sw, mask)

    def cap(self, ring: np.ndarray, centre_pts: dict[str, np.ndarray], weight: dict, paint: tuple[str, tuple]) -> int:
        cid = int(self.add(centre_pts, [weight])[0])
        n = len(ring)
        for c in range(n):
            self.face((ring[c], ring[(c + 1) % n], cid), *paint)
        return cid

    def thicken(self, face_ids: list[int], thickness: float, inner: tuple[str, tuple], rim: tuple[str, tuple]) -> list[int]:
        """Add a lining behind `face_ids` (call after `orient`)."""
        face_ids = list(face_ids)
        verts = sorted({v for fi in face_ids for v in self.faces[fi]})
        normals = {s: vertex_normals(self.positions(s), self.faces) for s in self.shapes}
        pts = {s: self.positions(s)[verts] - normals[s][verts] * thickness for s in self.shapes}
        new = self.add(pts, [dict(self.weights[v]) for v in verts])
        m = dict(zip(verts, new.tolist()))
        added = []
        edge_count: dict[tuple[int, int], int] = {}
        for fi in face_ids:
            f = self.faces[fi]
            for i in range(len(f)):
                e = (f[i], f[(i + 1) % len(f)])
                edge_count[e] = edge_count.get(e, 0) + 1
            added.append(self.face(tuple(m[v] for v in reversed(f)), *inner))
        for (a, b), _ in edge_count.items():
            if (b, a) in edge_count:
                continue  # interior edge of the set
            added.append(self.face((b, a, m[a], m[b]), *rim))
        return added

    # ------------------------------------------------------------ finish
    def orient(self, reference: np.ndarray | None = None) -> None:
        """Make winding consistent across shared edges, starting from the shell
        faces (which inherit the body's outward winding). Components without a
        shell face are oriented to face away from `reference` (or their centre)."""
        edges: dict[tuple[int, int], list[int]] = {}
        for fi, f in enumerate(self.faces):
            for i in range(len(f)):
                a, b = f[i], f[(i + 1) % len(f)]
                edges.setdefault((min(a, b), max(a, b)), []).append(fi)
        done = [False] * len(self.faces)
        P = self.positions("base")

        def directed(fi):
            f = self.faces[fi]
            return {(f[i], f[(i + 1) % len(f)]) for i in range(len(f))}

        def flood(start: int) -> list[int]:
            comp = [start]
            done[start] = True
            q = deque([start])
            while q:
                fi = q.popleft()
                f = self.faces[fi]
                for i in range(len(f)):
                    a, b = f[i], f[(i + 1) % len(f)]
                    for gj in edges[(min(a, b), max(a, b))]:
                        if done[gj]:
                            continue
                        if (a, b) in directed(gj):
                            self.faces[gj] = tuple(reversed(self.faces[gj]))
                        done[gj] = True
                        comp.append(gj)
                        q.append(gj)
            return comp

        for fi in sorted(self.seed_faces):
            if not done[fi]:
                flood(fi)
        for fi in range(len(self.faces)):
            if done[fi]:
                continue
            comp = flood(fi)
            verts = sorted({v for gj in comp for v in self.faces[gj]})
            # Default: face away from the body's vertical axis at this height.
            ref = reference if reference is not None else np.array([0.0, 0.0, float(P[verts, 2].mean())])
            score = 0.0
            for gj in comp:
                f = self.faces[gj]
                p = P[list(f)]
                nrm = np.zeros(3)
                for i in range(len(f)):
                    nrm += np.cross(p[i], p[(i + 1) % len(f)])
                score += float(nrm @ (p.mean(axis=0) - ref))
            if score < 0:
                for gj in comp:
                    self.faces[gj] = tuple(reversed(self.faces[gj]))

    def meshdata(self, region: str = "gear") -> MeshData:
        md = MeshData()
        md.add_verts(self.positions("base"), self.weights)
        for f, sw, m in zip(self.faces, self.swatch, self.mask):
            md.add_face(f, region, sw, m)
        md.keys = {s: self.positions(s) for s in self.shapes[1:]}
        md.compact()
        return md
