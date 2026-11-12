"""The head a hairstyle is built on: the body's own visible head (LOD0).

Hair is fitted to the shipped (sculpt-wrapped) head itself, never to a
generic cage: every style belongs to one body (creator.json v2), so the
skull, ears, brow line and nape it is cut against are that body's mesh.

Head frame (as everywhere in charlib): the centre is
`skeleton.head_centre(body)` (eye height, inside the skull); polar angle
`phi` in degrees from straight up, azimuth `theta` in degrees with 0 = the
face (Blender -Y), 90 = the character's left (+X). Style numbers are in
"head centimetres" (`hcm`: 0.01 m x `Body.head_scale`), so a style scales
with the head it was designed on.
"""

from __future__ import annotations

import math

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import skeleton as K

SKULL_REGIONS = ("head", "scalp", "neck")
COLLIDE_REGIONS = ("head", "scalp", "ears", "neck", "torso", "upper_arms")


def world_mesh(obj) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rest-pose world positions, vertex normals and triangles of a mesh
    object (base shape: shape keys and modifiers ignored)."""
    me = obj.data
    n = len(me.vertices)
    co = np.zeros(n * 3)
    me.vertices.foreach_get("co", co)
    nr = np.zeros(n * 3)
    me.vertices.foreach_get("normal", nr)
    mw = np.array(obj.matrix_world)
    co = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    nr = nr.reshape(-1, 3) @ mw[:3, :3].T
    nr /= np.maximum(np.linalg.norm(nr, axis=1, keepdims=True), 1e-12)
    me.calc_loop_triangles()
    tris = np.zeros(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tris)
    return co, nr, tris.reshape(-1, 3)


def bvh_of(parts: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> BVHTree | None:
    verts, tris, off = [], [], 0
    for co, _, t in parts:
        verts.append(co)
        tris.append(t + off)
        off += len(co)
    if not verts:
        return None
    V = np.vstack(verts)
    T = np.vstack(tris)
    return BVHTree.FromPolygons(V.tolist(), T.tolist(), all_triangles=True)


class HeadFrame:
    """Polar frame, skull radius table and collision trees of one body.

    `bb` needs `id`, `body` (proportions) and `meshes[0]` (region -> LOD0
    object), as `pipeline.BodyBuild` or a body loaded from its .blend."""

    def __init__(self, bb):
        self.id = bb.id
        self.body = bb.body
        self.centre = np.array(K.head_centre(bb.body), dtype=np.float64)
        self.hs = float(bb.body.head_scale)
        self.hcm = 0.01 * self.hs  # metres per head centimetre
        lod0 = bb.meshes[0]
        self.objects = dict(lod0)  # region -> LOD0 object (their skin weights: hair following the body)
        self.parts = {r: world_mesh(o) for r, o in lod0.items() if r in COLLIDE_REGIONS}
        self.skull = bvh_of([self.parts[r] for r in SKULL_REGIONS if r in self.parts])
        self.ears = bvh_of([self.parts[r] for r in ("ears",) if r in self.parts])
        self.collide = bvh_of([self.parts[r] for r in COLLIDE_REGIONS if r in self.parts])
        self.collide_noears = bvh_of([self.parts[r] for r in COLLIDE_REGIONS if r in self.parts and r != "ears"])
        self.trunk = bvh_of([self.parts[r] for r in ("neck", "torso", "upper_arms") if r in self.parts])
        self.arms = bvh_of([self.parts[r] for r in ("upper_arms",) if r in self.parts])
        # One tree per region: a nearest-point query on a merged tree can
        # land on another region's face along a shared edge (the ear rim).
        self.region_trees = {r: bvh_of([self.parts[r]]) for r in COLLIDE_REGIONS if r in self.parts}
        # Every LOD0 region merged in the order check_characters.py merges
        # them (bpy.data.objects order), so a nearest-face query can be asked
        # exactly as the clipping check asks it.
        import bpy

        names = [o.name for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0]
        objs = {o.name: o for o in lod0.values()}
        verts, polys = [], []
        for n in names:
            if n not in objs:
                continue
            o = objs[n]
            co = world_mesh(o)[0]
            off = len(verts)
            verts.extend(co.tolist())
            polys.extend(tuple(off + i for i in p.vertices) for p in o.data.polygons)
        self.check_tree = BVHTree.FromPolygons(verts, polys) if polys else self.collide
        self._table()
        self._landmarks(lod0)

    # ------------------------------------------------------------- polar
    def dirs(self, phi, theta) -> np.ndarray:
        """World unit vectors of head rays (broadcast arrays of degrees)."""
        p = np.radians(np.asarray(phi, dtype=np.float64))
        t = np.radians(np.asarray(theta, dtype=np.float64))
        return np.stack([np.sin(p) * np.sin(t), -np.sin(p) * np.cos(t), np.cos(p)], axis=-1)

    def polar(self, pts) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(phi, theta, r metres) of world points."""
        d = np.asarray(pts, dtype=np.float64).reshape(-1, 3) - self.centre
        x, f, u = d[:, 0], -d[:, 1], d[:, 2]
        r = np.sqrt(x * x + f * f + u * u)
        phi = np.degrees(np.arccos(np.clip(u / np.maximum(r, 1e-12), -1.0, 1.0)))
        theta = np.degrees(np.arctan2(x, f)) % 360.0
        return phi, theta, r

    def _table(self) -> None:
        """Skull radius (outermost hit of head, scalp and neck) on a 2-degree
        grid; ears are left out (hair passes over or behind them)."""
        self.t_phi = np.arange(0.0, 182.0, 2.0)
        self.t_theta = np.arange(0.0, 362.0, 2.0)
        P, T = np.meshgrid(self.t_phi, self.t_theta, indexing="ij")
        D = self.dirs(P, T).reshape(-1, 3)
        R = np.zeros(len(D))
        far = 0.40
        c = Vector(tuple(self.centre))
        for i, d in enumerate(D):
            v = Vector(tuple(d))
            hit = self.skull.ray_cast(c + v * far, -v, far)
            R[i] = far - hit[3] if hit[0] is not None else np.nan
        R = R.reshape(P.shape)
        # Rays that miss (straight down through the open neck): nearest row above.
        for j in range(R.shape[1]):
            col = R[:, j]
            ok = ~np.isnan(col)
            col[~ok] = np.interp(np.nonzero(~ok)[0], np.nonzero(ok)[0], col[ok])
        R[:, -1] = R[:, 0]
        self.t_r = R

    def skull_r(self, phi, theta) -> np.ndarray:
        """Skull radius (metres) along head rays (bilinear in the table)."""
        phi = np.clip(np.asarray(phi, dtype=np.float64), 0.0, 180.0)
        theta = np.asarray(theta, dtype=np.float64) % 360.0
        fp = phi / 2.0
        ft = theta / 2.0
        i0 = np.clip(np.floor(fp).astype(int), 0, len(self.t_phi) - 2)
        j0 = np.clip(np.floor(ft).astype(int), 0, len(self.t_theta) - 2)
        a = fp - i0
        b = ft - j0
        R = self.t_r
        return ((1 - a) * (1 - b) * R[i0, j0] + a * (1 - b) * R[i0 + 1, j0] + (1 - a) * b * R[i0, j0 + 1]
                + a * b * R[i0 + 1, j0 + 1])

    def point(self, phi, theta, extra_m=0.0) -> np.ndarray:
        """World point `extra_m` metres outside the skull along head rays."""
        phi, theta, extra_m = np.broadcast_arrays(np.asarray(phi, dtype=np.float64),
                                                  np.asarray(theta, dtype=np.float64),
                                                  np.asarray(extra_m, dtype=np.float64))
        r = self.skull_r(phi, theta) + extra_m
        return self.centre + self.dirs(phi, theta) * r[..., None]

    # --------------------------------------------------------- landmarks
    def _landmarks(self, lod0) -> None:
        co, nr, _ = self.parts["head"]
        self.chin_z = float(co[:, 2].min())
        sc = self.parts.get("scalp")
        self.top_z = float(max(co[:, 2].max(), sc[0][:, 2].max() if sc is not None else -1))
        self.eye_z = float(self.body.eye_z)
        self.brow_z = self.eye_z + 1.75 * self.hcm
        brow = _brow_vertices(lod0.get("head"))
        if brow is not None and len(brow):
            self.brow_z = float(np.median(brow[:, 2]))
        e = self.parts.get("ears")
        self.ear = None
        if e is not None:
            left = e[0][e[0][:, 0] > 0]
            self.ear = {"top": float(left[:, 2].max()), "bottom": float(left[:, 2].min()),
                        "x": float(left[:, 0].max()), "y": float(left[:, 1].mean())}
        n = self.parts.get("neck")
        self.neck_z = float(n[0][:, 2].min()) if n is not None else self.chin_z - 0.08
        # The scalp region's lower edge per azimuth (hair must cover it all:
        # the client hides `scalp` under any hairstyle).
        sc = self.parts.get("scalp")
        th = np.arange(0.0, 360.0, 10.0)
        border = np.full(len(th), 60.0)
        if sc is not None:
            phi, theta, _ = self.polar(sc[0])
            for i, t in enumerate(th):
                m = np.abs(((theta - t + 180.0) % 360.0) - 180.0) < 12.0
                if m.any():
                    border[i] = phi[m].max()
        self._border = (th, border)

    def scalp_border(self, theta) -> np.ndarray:
        th, b = self._border
        return np.interp(np.asarray(theta, dtype=np.float64) % 360.0, np.concatenate([th, [360.0]]),
                         np.concatenate([b, [b[0]]]))

    def phi_at_z(self, theta: float, z: float, extra_m: float = 0.0) -> float:
        """Polar angle where the skull (plus `extra_m`) is at height `z`, on
        the upper half of azimuth `theta` (bisection)."""
        lo, hi = 0.0, 170.0
        for _ in range(40):
            m = 0.5 * (lo + hi)
            if float(self.point(m, theta, extra_m)[2]) > z:
                lo = m
            else:
                hi = m
        return 0.5 * (lo + hi)

    def summary(self) -> dict:
        return {"centre": [round(float(x), 4) for x in self.centre], "head_scale": round(self.hs, 4),
                "top_z": round(self.top_z, 4), "chin_z": round(self.chin_z, 4), "eye_z": round(self.eye_z, 4),
                "brow_z": round(self.brow_z, 4), "neck_z": round(self.neck_z, 4), "ear": self.ear}

    # ---------------------------------------------------------- queries
    def clear(self, p: np.ndarray, clearance: float, tree: BVHTree | None = None, reach: float = 0.08) -> np.ndarray:
        """`p` moved out of the body (or `tree`) to at least `clearance`."""
        tree = tree or self.collide
        loc, nrm, _, dist = tree.find_nearest(Vector(tuple(p)), reach)
        if loc is None:
            return p
        loc = np.array(loc)
        nrm = np.array(nrm)
        h = float((p - loc) @ nrm)
        if h < clearance:
            return p + nrm * (clearance - h)
        return p

    def inside(self, p: np.ndarray, tree: BVHTree | None = None, reach: float = 0.05) -> float:
        """How far `p` is inside the surface (0 outside). Only counted when
        the offset runs along the nearest face's normal: beside a thin
        part's rim (the ear) the nearest face can be its far side."""
        tree = tree or self.collide
        loc, nrm, _, dist = tree.find_nearest(Vector(tuple(p)), reach)
        if loc is None:
            return 0.0
        h = float((p - np.array(loc)) @ np.array(nrm))
        return -h if h < 0 and -h >= 0.5 * dist else 0.0

    def depth(self, p: np.ndarray, tree: BVHTree | None = None, reach: float = 0.08) -> float:
        """Signed height of `p` over the nearest body surface (negative: inside)."""
        tree = tree or self.collide
        loc, nrm, _, dist = tree.find_nearest(Vector(tuple(p)), reach)
        if loc is None:
            return reach
        return float((p - np.array(loc)) @ np.array(nrm))


def _brow_vertices(obj) -> np.ndarray | None:
    """World positions of the painted brows (head vertices tinted by the
    hair colour: dye mask G), front-facing."""
    if obj is None or not obj.data.color_attributes:
        return None
    me = obj.data
    col = me.color_attributes[0]
    if col.domain != "CORNER":
        return None
    c = np.zeros(len(me.loops) * 4)
    col.data.foreach_get("color", c)
    c = c.reshape(-1, 4)
    vi = np.zeros(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("vertex_index", vi)
    g = np.zeros(len(me.vertices))
    np.maximum.at(g, vi, c[:, 1])
    co, nr, _ = world_mesh(obj)
    sel = (g > 0.5) & (nr[:, 1] < -0.3)
    return co[sel]


def band_phi(theta: float) -> float:
    """Polar angle (degrees) of the lowest hat band line: hats cover above it
    (the contract with every hat; unchanged from the first hair system)."""
    side = theta if theta <= 180 else 360 - theta
    return 61.0 + 22.0 * (1 - math.cos(math.radians(side))) / 2.0 + 6.0 * math.sin(math.radians(side)) ** 2


def load_body(path: str, drop_hair: bool = True):
    """A body saved by build_characters.py, as the hair builder needs it
    (`id`, `body`, `meshes` {0: LOD0 regions, 1: LOD1}, `skel`, `material`).
    `drop_hair`: remove the hair objects the file was saved with."""
    import os
    from types import SimpleNamespace

    import bpy

    from . import proportions as P

    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
    name = os.path.splitext(os.path.basename(path))[0]
    body_id = "_".join(name.split("_")[:2])
    skel = bpy.data.objects[f"{body_id}_skeleton"]
    if drop_hair:
        for o in [o for o in bpy.data.objects if str(o.get("asset", "")).startswith("hair/") and not o.get("region")]:
            me = o.data
            bpy.data.objects.remove(o)
            if me.users == 0:
                bpy.data.meshes.remove(me)
    meshes = {}
    for o in bpy.data.objects:
        if o.type == "MESH" and o.get("region") and o.get("lod") is not None:
            meshes.setdefault(int(o["lod"]), {})[str(o["region"])] = o
    mat = bpy.data.materials.get("character") or next(iter(meshes[0].values())).data.materials[0]
    source = str(skel.get("body_source") or "makehuman")
    shape = skel.get("body_shape")
    report = {"shape": {"target": str(shape)}} if shape else {}
    return SimpleNamespace(id=body_id, body=P.of_skeleton(skel, body_id), meshes=meshes, skel=skel, material=mat,
                           source=source, report=report, blend=name)
