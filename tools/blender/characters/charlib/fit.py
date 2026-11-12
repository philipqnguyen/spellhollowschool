"""Body-agnostic fitting: what a garment shape reads from a body, and how it
lands on it (docs/art/garment-shapes.md, "Fitting").

A garment shape never reads the body's topology (its grid rows, rings or face
tags). Fitting uses only what every base body has to provide:

* the mesh: vertex positions for the base shape and each build key, faces,
  the region of every face (`head`, `torso`, `thighs`, ...) and the bone
  weights of every vertex;
* the skeleton: rest-pose head and tail of the deform bones, by name.

From those, `BodyFrame` measures landmarks (armpit, waist, hip and crotch
heights, the neckline, the torso centre line, the head and foot extents) with
ray casts against region-filtered parts of the mesh, and offers *charts*:
rays defined from the skeleton (around the spine, a limb or the head). A
garment vertex is a chart sample: a ray from a skeleton axis, cast against
the regions that part of the garment covers. The hit is stored as a surface
point (triangle and barycentric coordinates) and evaluated on every build
shape, so build shape keys come for free, and its bone weights are the
barycentric blend of the hit triangle's weights.

So a new body (another topology, e.g. MakeHuman's) refits every shape by
running the same code: the garment's own topology, zones, loops and anchors
stay the same, only the positions and weights change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .frames import Polyline, Ring, tube_rings
from .gear_lib import PRIMARY, GearBuilder, vertex_normals
from .proportions import BUILDS

SHAPES = ["base"] + list(BUILDS)
TRUNK = ("neck", "torso", "hips")
ARMS = ("upper_arms", "forearms", "hands")
LEGS = ("hips", "thighs", "calves")
FEET = ("feet", "calves")
HEAD = ("head", "scalp", "ears")
ALL = ("head", "scalp", "ears", "neck", "torso", "upper_arms", "forearms", "hands", "hips", "thighs", "calves", "feet")
SIDES = {1: "L", -1: "R"}
REF_HEIGHT = 1.78  # the reference body every metre value in the data files is written for


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


def horiz(theta_deg: float) -> np.ndarray:
    """Horizontal direction at azimuth theta: 0 = front (-Y), 90 = the character's left (+X)."""
    t = math.radians(theta_deg)
    return np.array([math.sin(t), -math.cos(t), 0.0])


def barycentric(p, a, b, c) -> np.ndarray:
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


def top_weights(w: dict[str, float], k: int = 4, floor: float = 0.01) -> dict[str, float]:
    items = sorted(((n, v) for n, v in w.items() if v > 0), key=lambda kv: -kv[1])[:k]
    items = [(n, v) for n, v in items if v >= floor] or items[:1]
    s = sum(v for _, v in items) or 1.0
    return {n: v / s for n, v in items}


def blend_weights(parts) -> dict[str, float]:
    out: dict[str, float] = {}
    for f, w in parts:
        if f <= 0:
            continue
        for n, v in w.items():
            out[n] = out.get(n, 0.0) + v * f
    return out


@dataclass
class SurfacePoint:
    """A point on the body surface (triangle + barycentrics) plus a normal offset."""

    tri: int
    bc: np.ndarray
    off: float = 0.0


@dataclass
class Joints:
    """Rest-pose bone heads and tails of the shared skeleton."""

    bones: dict[str, tuple[np.ndarray, np.ndarray]]

    def head(self, name: str) -> np.ndarray:
        return self.bones[name][0]

    def tail(self, name: str) -> np.ndarray:
        return self.bones[name][1]

    @staticmethod
    def from_armature(skel) -> "Joints":
        m = skel.matrix_world
        return Joints({b.name: (np.array(m @ b.head_local), np.array(m @ b.tail_local)) for b in skel.data.bones})

    @staticmethod
    def from_body(body) -> "Joints":
        """From the landmark definition (used when no armature exists yet)."""
        from . import skeleton as K

        return Joints({"DEF-" + b.name: (np.asarray(b.head, float), np.asarray(b.tail, float)) for b in K.joints(body)})


def inward_votes(P: np.ndarray, faces, regions, joints: "Joints") -> dict[str, tuple[int, int]]:
    """Per region: (faces whose normal points toward their nearest deform
    bone, faces). A region wound outward has almost no inward votes. The eye
    and jaw bones are left out: they sit just behind (or, on a flat painted
    face, in front of) the face surface, and the face's many small paint
    triangles would vote the whole head inward."""
    segs = [(h, t) for name, (h, t) in joints.bones.items()
            if name.startswith("DEF-") and not name.startswith(("DEF-eye", "DEF-jaw")) and np.linalg.norm(t - h) > 1e-4]
    A = np.array([h for h, _ in segs])
    B = np.array([t for _, t in segs])
    D = B - A
    DD = np.maximum((D * D).sum(axis=1), 1e-12)
    out: dict[str, list[int]] = {}
    for f, r in zip(faces, regions):
        q = P[list(f)]
        c = q.mean(axis=0)
        n = np.zeros(3)
        for i in range(len(f)):
            n += np.cross(q[i], q[(i + 1) % len(f)])
        t = np.clip(((c - A) * D).sum(axis=1) / DD, 0.0, 1.0)
        near = A + D * t[:, None]
        k = int(np.argmin(np.linalg.norm(c - near, axis=1)))
        v = out.setdefault(r, [0, 0])
        v[0] += float(n @ (c - near[k])) < 0
        v[1] += 1
    return {r: (a, b) for r, (a, b) in out.items()}


def meshdata_from_objects(objs, shapes=SHAPES):
    """The fitting inputs read back from a body's rendered LOD0 objects (one
    mesh object per region, carrying a `region` property, bone vertex groups
    and the build shape keys), whatever generated them. This is all a body
    has to hand over to be fitted: no layout, rows, rings or face tags. Parts
    whose build key was dropped (it did not move them) use their base
    positions for that build."""
    from .meshutil import MeshData

    md = MeshData()
    verts, keys = [], {s: [] for s in shapes[1:]}
    off = 0
    for o in sorted(objs, key=lambda o: o.name):
        me = o.data
        n = len(me.vertices)
        M = np.array(o.matrix_world)

        def world(arr):
            return arr.reshape(n, 3) @ M[:3, :3].T + M[:3, 3]

        co = np.empty(n * 3)
        me.vertices.foreach_get("co", co)
        co = world(co)
        verts.append(co)
        kb = me.shape_keys.key_blocks if me.shape_keys else {}
        for s in shapes[1:]:
            if s in kb:
                k = np.empty(n * 3)
                kb[s].data.foreach_get("co", k)
                keys[s].append(world(k))
            else:
                keys[s].append(co)
        names = {g.index: g.name for g in o.vertex_groups}
        md.weights.extend({names[e.group]: float(e.weight) for e in v.groups if e.weight > 0 and e.group in names}
                          for v in me.vertices)
        for p in me.polygons:
            md.faces.append(tuple(off + int(i) for i in p.vertices))
            md.region.append(str(o["region"]))
        off += n
    md.verts = np.vstack(verts)
    # Only the build keys some part carries (the shipped bodies have none:
    # every body is the neutral build).
    present = {s for o in objs if o.data.shape_keys for s in o.data.shape_keys.key_blocks.keys()}
    md.keys = {s: np.vstack(v) for s, v in keys.items() if s in present}
    return md


class BodyFrame:
    """The fitting inputs of one body (see the module docs) and its landmarks."""

    def __init__(self, md, joints: Joints, body_id: str, shapes=None):
        self.md = md
        self.id = body_id
        self.joints = joints
        self.shapes = list(shapes or (["base"] + [s for s in SHAPES[1:] if s in getattr(md, "keys", {})]))
        self.P = {"base": np.asarray(md.verts, dtype=np.float64)}
        for s in self.shapes[1:]:
            self.P[s] = np.asarray(md.keys[s], dtype=np.float64)
        # Faces oriented away from the skeleton, not trusted from the winding:
        # a body may come with inward-wound parts (the current body's arm
        # tubes are), and offsets must always go outward.
        self.faces_out, self.flipped = self._outward_faces(md.faces)
        tris, tri_face = [], []
        for fi, f in enumerate(self.faces_out):
            for i in range(1, len(f) - 1):
                tris.append((f[0], f[i], f[i + 1]))
                tri_face.append(fi)
        self.tris = np.array(tris, dtype=np.int64)
        self.tri_region = np.array(md.region, dtype=object)[np.array(tri_face)]
        self.N = {s: vertex_normals(self.P[s], self.faces_out) for s in self.shapes}
        self._bvh: dict = {}
        self.region_verts = {}
        reg = np.array(md.region, dtype=object)
        for r in set(md.region):
            fs = [f for f, rr in zip(md.faces, reg) if rr == r]
            self.region_verts[r] = np.unique(np.array([v for f in fs for v in f], dtype=np.int64))
        self.measure()

    def _outward_faces(self, faces):
        """Faces rewound, region by region, so normals point away from the
        skeleton. Each face votes (does its normal point away from the nearest
        bone?) and a region whose faces mostly point inward is flipped as a
        whole; a per-face decision would misjudge faces whose nearest bone
        runs close under the skin (the clavicles under the upper chest)."""
        votes = inward_votes(self.P["base"], faces, self.md.region, self.joints)
        self.inward_regions = sorted(r for r, (inward, n) in votes.items() if inward * 2 > n)
        out, flipped = [], 0
        for f, r in zip(faces, self.md.region):
            if r in self.inward_regions:
                out.append(tuple(reversed(f)))
                flipped += 1
            else:
                out.append(tuple(f))
        return out, flipped

    # ------------------------------------------------------------ queries
    def bvh(self, regions=ALL, shape: str = "base"):
        key = (tuple(sorted(regions)), shape)
        hit = self._bvh.get(key)
        if hit is None:
            mask = np.isin(self.tri_region, list(regions))
            ids = np.nonzero(mask)[0]
            tree = BVHTree.FromPolygons(self.P[shape].tolist(), self.tris[ids].tolist())
            hit = self._bvh[key] = (tree, ids)
        return hit

    def _sp(self, ids, idx, loc, shape="base") -> SurfacePoint:
        tri = int(ids[idx])
        t = self.tris[tri]
        P = self.P[shape]
        return SurfacePoint(tri, barycentric(np.asarray(loc), P[t[0]], P[t[1]], P[t[2]]))

    def ray(self, origin, direction, regions=ALL, max_dist: float = 0.6) -> SurfacePoint | None:
        tree, ids = self.bvh(regions)
        loc, nrm, idx, dist = tree.ray_cast(Vector(origin), Vector(unit(direction)), max_dist)
        if idx is None:
            return None
        return self._sp(ids, idx, loc)

    def ray_region(self, origin, direction, max_dist: float = 0.6) -> tuple[str | None, float]:
        """Region and distance of the first body face along a ray."""
        tree, ids = self.bvh(ALL)
        loc, nrm, idx, dist = tree.ray_cast(Vector(origin), Vector(unit(direction)), max_dist)
        if idx is None:
            return None, 0.0
        return str(self.tri_region[ids[idx]]), float(dist)

    def nearest(self, point, regions=ALL, shape: str = "base") -> SurfacePoint:
        tree, ids = self.bvh(regions, shape)
        loc, nrm, idx, dist = tree.find_nearest(Vector(point))
        return self._sp(ids, idx, loc, shape)

    def fit(self, origin, direction, regions, fallback=ALL, guess: float = 0.1, max_dist: float = 0.6) -> SurfacePoint:
        """Chart sample: first hit of the ray on `regions` within `max_dist`,
        else on `fallback`, else the point of `regions` nearest the ray at
        `guess` metres. Limb charts pass no fallback: a ray that leaves the
        arm through the shoulder junction must not land on the chest."""
        sp = self.ray(origin, direction, regions, max_dist)
        if sp is None and fallback:
            sp = self.ray(origin, direction, fallback, max_dist)
        if sp is None:
            sp = self.nearest(np.asarray(origin) + unit(direction) * guess, regions)
        return sp

    def eval(self, sp: SurfacePoint, shape: str = "base") -> tuple[np.ndarray, np.ndarray]:
        t = self.tris[sp.tri]
        P, N = self.P[shape], self.N[shape]
        p = sp.bc @ P[t]
        n = unit(sp.bc @ N[t])
        return p, n

    def point(self, sp: SurfacePoint, shape: str = "base", off: float | None = None) -> np.ndarray:
        p, n = self.eval(sp, shape)
        return p + n * (sp.off if off is None else off)

    def weights(self, sp: SurfacePoint) -> dict[str, float]:
        W = self.md.weights
        acc: dict[str, float] = {}
        for vi, k in zip(self.tris[sp.tri], sp.bc):
            for name, v in W[vi].items():
                acc[name] = acc.get(name, 0.0) + v * k
        return top_weights(acc)

    # --------------------------------------------------------- landmarks
    def measure(self) -> None:
        """Landmarks measured from the mesh and skeleton (never from the layout)."""
        J = self.joints
        P = self.P["base"]
        self.height = float(P[:, 2].max())
        self.sc = self.height / REF_HEIGHT
        self.spine = Polyline([J.head(f"DEF-spine{s}") for s in ("", ".001", ".002", ".003", ".004", ".005", ".006")]
                              + [J.tail("DEF-spine.006")])
        self.shoulder = {s: J.head(f"DEF-upper_arm.{n}") for s, n in SIDES.items()}
        self.elbow = {s: J.head(f"DEF-forearm.{n}") for s, n in SIDES.items()}
        self.wrist = {s: J.head(f"DEF-hand.{n}") for s, n in SIDES.items()}
        self.hand_end = {s: J.tail(f"DEF-hand.{n}") for s, n in SIDES.items()}
        self.hip_joint = {s: J.head(f"DEF-thigh.{n}") for s, n in SIDES.items()}
        self.knee = {s: J.head(f"DEF-shin.{n}") for s, n in SIDES.items()}
        self.ankle = {s: J.head(f"DEF-foot.{n}") for s, n in SIDES.items()}
        self.ball = {s: J.head(f"DEF-toe.{n}") for s, n in SIDES.items()}
        self.toe_tip = {s: J.tail(f"DEF-toe.{n}") for s, n in SIDES.items()}
        neck0 = J.head("DEF-spine.004")
        head0 = J.head("DEF-spine.006")
        self.neck_axis = (neck0, head0)
        # The neck's own centre line, measured from the neck skin (the middle
        # of its front and back in thin height slabs): the neck charts (collar
        # stands, turtlenecks) cast from it. The neck bones need not run
        # through the neck: on the wrapped female they sit 25-40 mm in front
        # of the throat, so rays from them started outside the neck and the
        # front of every stand fell through to the nearest skin.
        nv = P[self.region_verts["neck"]] if "neck" in self.region_verts else np.zeros((0, 3))
        cz, cy = [], []
        if len(nv):
            for z in np.linspace(float(nv[:, 2].min()), float(head0[2]) + 0.03 * self.sc, 16):
                q = nv[np.abs(nv[:, 2] - z) < 0.006 * self.sc]
                if len(q) >= 4:
                    cz.append(float(z))
                    cy.append(0.5 * float(q[:, 1].min() + q[:, 1].max()))
        self._neck_cz = np.array(cz) if cz else np.array([float(neck0[2]), float(head0[2])])
        self._neck_cy = np.array(cy) if cy else np.array([float(neck0[1]), float(head0[1])])
        # Torso centre line: midway between the front and back surfaces.
        zs = np.linspace(float(self.hip_joint[1][2]) - 0.1 * self.sc, float(neck0[2]), 40)
        mids = []
        for z in zs:
            o = self.spine_at(z)
            f = self.ray(o, horiz(0), TRUNK)
            b = self.ray(o, horiz(180), TRUNK)
            if f is None or b is None:
                mids.append(np.nan)
                continue
            mids.append(0.5 * (self.point(f)[1] + self.point(b)[1]))
        mids = np.array(mids)
        ok = ~np.isnan(mids)
        self._mid_z, self._mid_y = zs[ok], mids[ok]
        # Half width at the sides (horizontal rays at +-90 degrees).
        self._w_z = zs
        self._w = np.array([self._half_width(z) for z in zs])
        # Armpit: the lowest height at which a side ray meets the arm first.
        top = float(self.shoulder[1][2])
        self.armpit_z = top
        for side in (1, -1):
            z = top
            while z > top - 0.35 * self.sc:
                reg, _ = self.ray_region(self.trunk_origin(z), horiz(90 * side))
                if reg not in ARMS:
                    break
                z -= 0.003
            self.armpit_z = min(self.armpit_z, z)
        # Crotch: going down the centre line from the hip joints, the first
        # height at which the centre point is outside the body (sideways rays
        # enter a thigh instead of leaving the pelvis). A vertical ray would
        # stop where the inner thighs touch, well below the crotch.
        hj = 0.5 * (self.hip_joint[1] + self.hip_joint[-1])
        tree, _ = self.bvh(ALL)
        self.crotch_z = float(hj[2]) - 0.09 * self.sc
        for z in np.arange(float(hj[2]), float(hj[2]) - 0.3 * self.sc, -0.003):
            o = Vector((0.0, float(self.mid_y(z)), float(z)))
            outside = 0
            for dx in (1.0, -1.0):
                loc, nrm, idx, dist = tree.ray_cast(o, Vector((dx, 0.0, 0.0)), 0.4)
                # Entering a surface, or sitting on the crotch seam itself.
                if idx is not None and (nrm.x * dx < 0 or dist < 0.003 * self.sc):
                    outside += 1
            if outside == 2:
                self.crotch_z = float(z)
                break
        lo, hi = float(hj[2]) + 0.06 * self.sc, self.armpit_z - 0.12 * self.sc
        self.waist_z = self._extreme_width(lo, hi, np.argmin)
        self.hip_z = self._extreme_width(self.crotch_z + 0.03 * self.sc, self.waist_z - 0.04 * self.sc, np.argmax)
        # Chest: the most forward point of the front in the upper half
        # between waist and armpit (a belly that sits further forward than
        # the chest, as on a heavier or more detailed body, is not the chest).
        best, self.chest_z = 1e9, self.armpit_z
        lo = self.waist_z + 0.45 * (self.armpit_z - self.waist_z)
        for z in np.linspace(self.waist_z, self.armpit_z + 0.04 * self.sc, 24):
            if z < lo:
                continue
            sp = self.ray(self.trunk_origin(z), horiz(0), TRUNK)
            if sp is not None and self.point(sp)[1] < best:
                best, self.chest_z = self.point(sp)[1], float(z)
        # The chest line sits below the arm holes even where the pectorals reach up to them.
        self.chest_z = min(self.chest_z, self.armpit_z - 0.06 * self.sc)
        # Head: bounding box of the head and scalp.
        hv = np.concatenate([self.region_verts.get(r, np.zeros(0, int)) for r in ("head", "scalp")])
        hp = P[hv]
        self.crown_z = float(hp[:, 2].max())
        self.chin_z = float(hp[:, 2].min())
        self.head_size = self.crown_z - self.chin_z
        self.head_centre = np.array([0.5 * (hp[:, 0].min() + hp[:, 0].max()), 0.5 * (hp[:, 1].min() + hp[:, 1].max()),
                                     self.chin_z + 0.6 * self.head_size])
        self.head_scale = (hp[:, 0].max() - hp[:, 0].min()) / 0.150
        # Feet.
        fv = P[self.region_verts["feet"]]
        self.foot = {}
        for side in (1, -1):
            q = fv[np.sign(fv[:, 0]) == side]
            self.foot[side] = {"heel_y": float(q[:, 1].max()), "toe_y": float(q[:, 1].min()), "x": float(q[:, 0].mean()),
                               "half_w": float(0.5 * (q[:, 0].max() - q[:, 0].min())), "floor": float(q[:, 2].min())}

    def _half_width(self, z: float) -> float:
        w = []
        for side in (1, -1):
            sp = self.ray(self.spine_at(z), horiz(90 * side), TRUNK)
            if sp is None:
                return np.nan
            w.append(abs(self.point(sp)[0]))
        return float(np.mean(w))

    def _extreme_width(self, lo: float, hi: float, pick) -> float:
        zs = np.linspace(lo, hi, 30)
        w = np.array([self._half_width(z) for z in zs])
        w = np.where(np.isnan(w), np.nanmean(w), w)
        return float(zs[int(pick(w))])

    def spine_at(self, z: float) -> np.ndarray:
        p = self.spine.p
        y = float(np.interp(z, p[:, 2], p[:, 1]))
        return np.array([0.0, y, z])

    def mid_y(self, z: float) -> float:
        return float(np.interp(z, self._mid_z, self._mid_y))

    def trunk_origin(self, z: float) -> np.ndarray:
        if not hasattr(self, "_mid_z"):
            return self.spine_at(z)
        return np.array([0.0, self.mid_y(z), z])

    def landmark(self, name: str) -> float:
        """Height (metres) of a named trunk landmark."""
        return {"crotch": self.crotch_z, "hip": self.hip_z, "waist": self.waist_z, "chest": self.chest_z,
                "armpit": self.armpit_z, "knee": float(np.mean([self.knee[s][2] for s in (1, -1)])),
                "ankle": float(np.mean([self.ankle[s][2] for s in (1, -1)]))}[name]

    def summary(self) -> dict:
        return {k: round(float(v), 4) for k, v in (
            ("faces_rewound", self.flipped), ("height", self.height), ("crotch", self.crotch_z), ("hip", self.hip_z), ("waist", self.waist_z),
            ("chest", self.chest_z), ("armpit", self.armpit_z), ("crown", self.crown_z), ("head_scale", self.head_scale))} | {
            "inward_regions": list(self.inward_regions)}

    # ------------------------------------------------------------ charts
    def dome_centre(self) -> np.ndarray:
        return np.array([0.0, self.mid_y(self.armpit_z), self.armpit_z])

    def trunk_ray(self, theta: float, level) -> tuple[np.ndarray, np.ndarray]:
        """Ray of the trunk chart. `level` is ("h", z) (horizontal from the
        torso centre line), ("d", elevation_deg) (fanned from the dome centre
        at armpit height, over the shoulders) or ("n", z) (horizontal from
        the neck's measured centre line)."""
        kind, v = level
        if kind == "h":
            return self.trunk_origin(v), horiz(theta)
        if kind == "d":
            e = math.radians(v)
            return self.dome_centre(), unit(horiz(theta) * math.cos(e) + np.array([0, 0, math.sin(e)]))
        return self.neck_centre(v), horiz(theta)

    def neck_centre(self, z: float) -> np.ndarray:
        """A point on the neck's measured centre line at height z (clamped to its ends)."""
        return np.array([0.0, float(np.interp(z, self._neck_cz, self._neck_cy)), float(z)])

    def neckline_elevation(self, theta: float, lo: float = 10.0, hi: float = 86.0) -> float:
        """Dome elevation where the meridian at `theta` crosses from the torso onto the neck."""
        c = self.dome_centre()
        for _ in range(22):
            mid = 0.5 * (lo + hi)
            e = math.radians(mid)
            reg, _ = self.ray_region(c, horiz(theta) * math.cos(e) + np.array([0, 0, math.sin(e)]))
            if reg in ("neck", "head", "scalp"):
                hi = mid
            else:
                lo = mid
        return 0.5 * (lo + hi)

    def arm_hole_top(self, side: int) -> float:
        """Highest dome elevation at which the side meridian still meets the arm."""
        c = self.dome_centre()
        top = 0.0
        for e in np.arange(0.0, 70.0, 1.0):
            r = math.radians(e)
            reg, _ = self.ray_region(c, horiz(90 * side) * math.cos(r) + np.array([0, 0, math.sin(r)]))
            if reg in ARMS:
                top = float(e)
        return top

    def limb_line(self, kind: str, side: int) -> Polyline:
        if kind == "arm":
            return Polyline([self.shoulder[side], self.elbow[side], self.wrist[side], self.hand_end[side]])
        return Polyline([self.hip_joint[side], self.knee[side], self.ankle[side]])

    def limb_rings(self, kind: str, side: int, s_values) -> list[Ring]:
        line = self.limb_line(kind, side)
        return tube_rings(line, list(s_values), np.array([0.0, -1.0, 0.0]))

    def head_ray(self, phi: float, theta: float) -> tuple[np.ndarray, np.ndarray]:
        """phi from straight up, theta azimuth (0 = front, 90 = left)."""
        p, t = math.radians(phi), math.radians(theta)
        d = np.array([math.sin(p) * math.sin(t), -math.sin(p) * math.cos(t), math.cos(p)])
        return self.head_centre, d


# ----------------------------------------------------------------------------
# The garment under construction
# ----------------------------------------------------------------------------


@dataclass
class Loop:
    """An ordered vertex loop or line of a garment (hem, cuff, neckline...)."""

    ids: list[int]
    closed: bool = True
    axis: str = "down"  # which way trims grow: "down", "up", "limb" (along the limb, away from the body), "out"
    side: int = 0


@dataclass
class Anchor:
    """A named attachment point: a garment vertex (follows its position, normal and weights)."""

    vid: int
    tangent: tuple[float, float, float] = (0.0, 0.0, -1.0)


class Garment(GearBuilder):
    """Garment mesh accumulator (GearBuilder's primitives without the body layout).

    Shape faces also carry coordinates in shape space (`coords`), which is what
    design zones and patterns paint by, so designs never see body topology."""

    def __init__(self, frame: BodyFrame):
        self.frame = frame
        self.body_md = frame.md
        self.layout = None
        self.body = None
        self.shapes = list(frame.shapes)
        self.P = frame.P
        self.N = frame.N
        self.pos: dict[str, list[np.ndarray]] = {s: [] for s in self.shapes}
        self.weights: list[dict[str, float]] = []
        self.faces: list[tuple[int, ...]] = []
        self.swatch: list[str] = []
        self.mask: list[tuple[float, float, float]] = []
        self.seed_faces: set[int] = set()
        self.n = 0
        self.joints = None
        self.remap = None
        self.coords: dict[int, dict] = {}  # face index -> shape coordinates
        self.vcoords: dict[int, dict] = {}  # vertex index -> shape coordinates
        self.surface: dict[int, SurfacePoint] = {}  # vertex -> body surface point (fitted vertices)

    def add_surface(self, sps: list[SurfacePoint], coords: list[dict] | None = None, weights=None) -> np.ndarray:
        """Add fitted vertices: body surface points pushed out by their offsets."""
        fr = self.frame
        pts = {s: np.array([fr.point(sp, s) for sp in sps]) for s in self.shapes}
        ws = weights or [fr.weights(sp) for sp in sps]
        ids = self.add(pts, ws)
        for i, sp in zip(ids, sps):
            self.surface[int(i)] = sp
        if coords:
            for i, c in zip(ids, coords):
                self.vcoords[int(i)] = c
        return ids

    def shape_face(self, ids, coords: dict, outward: np.ndarray | None = None) -> int:
        """A face of the fitted shape (painted later by the design)."""
        ids = tuple(int(i) for i in ids)
        if outward is not None:
            P = self.positions("base")
            p = P[list(ids)]
            nrm = np.zeros(3)
            for i in range(len(ids)):
                nrm += np.cross(p[i], p[(i + 1) % len(ids)])
            if float(nrm @ outward) < 0:
                ids = tuple(reversed(ids))
        fi = self.face(ids, "dye", PRIMARY, seed=outward is not None)
        self.coords[fi] = coords
        return fi

    def orient(self, reference: np.ndarray | None = None) -> None:
        """Consistent winding, with the shape faces as the authority: every
        shape face was wound outward on its own (`shape_face`), so they are
        never flipped; the other faces (trims, add-ons, hanging parts) take the
        winding of the shape faces they connect to, breadth first. A flood
        that may flip seeds (GearBuilder.orient) let one mismatched seam turn
        a whole sleeve inside out on the wrapped female (the bell lining
        showed outside). Components without a shape face face away from
        `reference` (or the body axis), as before."""
        from collections import deque

        edges: dict[tuple[int, int], list[int]] = {}
        for fi, f in enumerate(self.faces):
            for i in range(len(f)):
                a, b = f[i], f[(i + 1) % len(f)]
                edges.setdefault((min(a, b), max(a, b)), []).append(fi)
        done = [fi in self.seed_faces for fi in range(len(self.faces))]
        q = deque(sorted(self.seed_faces))
        while q:
            fi = q.popleft()
            f = self.faces[fi]
            for i in range(len(f)):
                a, b = f[i], f[(i + 1) % len(f)]
                for gj in edges[(min(a, b), max(a, b))]:
                    if done[gj]:
                        continue
                    g = self.faces[gj]
                    if any((g[k], g[(k + 1) % len(g)]) == (a, b) for k in range(len(g))):
                        self.faces[gj] = tuple(reversed(g))
                    done[gj] = True
                    q.append(gj)
        if all(done):
            return
        rest = set(fi for fi in range(len(self.faces)) if not done[fi])
        seeds, self.seed_faces = self.seed_faces, set()
        saved = list(self.faces)
        GearBuilder.orient(self, reference)
        # GearBuilder.orient only touched faces outside the seeded components
        # (it floods from no seed): keep ours for the rest.
        self.faces = [self.faces[fi] if fi in rest else saved[fi] for fi in range(len(self.faces))]
        self.seed_faces = seeds

    def set_positions(self, shape: str, ids, values: np.ndarray) -> None:
        allp = self.positions(shape)
        allp[list(ids)] = values
        self.pos[shape] = [allp]

    def normals(self, shape: str = "base") -> np.ndarray:
        return vertex_normals(self.positions(shape), self.faces)


def boundary_loops(faces) -> list[list[int]]:
    """Ordered boundary loops of a face set (edges used by one face)."""
    count: dict[tuple[int, int], int] = {}
    directed = {}
    for f in faces:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            k = (min(a, b), max(a, b))
            count[k] = count.get(k, 0) + 1
            directed[k] = (a, b)
    nxt = {}
    for k, c in count.items():
        if c == 1:
            a, b = directed[k]
            nxt[a] = b
    loops, seen = [], set()
    for start in list(nxt):
        if start in seen:
            continue
        loop, v = [], start
        while v not in seen and v in nxt:
            seen.add(v)
            loop.append(v)
            v = nxt[v]
        loops.append(loop)
    return loops


MAX_PUSH = 0.02  # metres a clearance pass may move a vertex at once
BESIDE = 0.5  # |cos| between a vertex's offset from its nearest skin and that skin's normal, below which it lies beside it
DEEP = 0.03  # deeper than this inside the skin, the nearest skin is not the part a vertex covers


def enforce_clearance(g: Garment, ids, min_clear: float, regions=ALL) -> int:
    """Push vertices out along the body normal until they clear the skin by
    `min_clear` in every build shape (a cage guarantee for junction and
    bridge vertices). Returns the number of vertex moves.

    The clearance is the smaller of the distances along the interpolated
    (smooth) normal and along the nearest face's own normal: on a sharply
    curved part (the MakeHuman heel, a knuckle) a vertex can clear the skin
    by the smooth normal and still sit inside the face the clipping check
    and the renderer see. A second pass catches vertices whose nearest skin
    changed after the first push."""
    fr = g.frame
    moved = 0
    ids = list(ids)
    for s in g.shapes:
        tree, tri_ids = fr.bvh(regions, s)
        P = g.positions(s)
        changed = set()
        for _ in range(2):
            again = False
            for vid in ids:
                p = P[vid]
                loc, nrm, idx, dist = tree.find_nearest(Vector(p))
                if loc is None:
                    continue
                loc = np.array(loc)
                t = fr.tris[tri_ids[idx]]
                bc = barycentric(loc, *fr.P[s][t])
                n = unit(bc @ fr.N[s][t])
                off = p - loc
                d = min(float(off @ n), float(off @ np.array(nrm)))
                L = float(np.linalg.norm(off))
                # Deeper than DEEP means the nearest skin is another part (an arm
                # vertex inside the torso at the armpit): pushing it along that
                # normal would throw it across the body. A vertex that lies
                # beside the skin rather than in front of or behind it (its
                # nearest point is on the rim where `regions` end, as the
                # ankle's for a sole when the feet are hidden) is not inside
                # it: pushing it along the rim's normal threw the soles' arch
                # vertices 4 cm sideways (the shoe fins, 2026-11-11).
                beside = L > 2.0 * min_clear and abs(float(off @ n)) < BESIDE * L
                if -DEEP < d < min_clear - 1e-5 and not beside:
                    P[vid] = p + n * min(min_clear - d, MAX_PUSH)
                    changed.add(vid)
                    again = True
            if not again:
                break
        if changed:
            g.pos[s] = [P]
            moved += len(changed)
    return moved


def enforce_face_clearance(g: Garment, faces, min_clear: float, regions=ALL, iters: int = 3) -> int:
    """Faces can cut into convex skin between cleared vertices (a quad
    spanning the shoulder or the knee). Sample each face's centre and edge
    midpoints; where one is closer than `min_clear`, push the face's
    vertices out along the body normal by the deficit."""
    fr = g.frame
    moved = 0
    faces = [g.faces[f] for f in faces]
    for s in g.shapes:
        tree, tri_ids = fr.bvh(regions, s)
        for _ in range(iters):
            P = g.positions(s)
            push = np.zeros_like(P)
            hits = np.zeros(len(P))
            for f in faces:
                q = P[list(f)]
                samples = [q.mean(axis=0)] + [0.5 * (q[i] + q[(i + 1) % len(f)]) for i in range(len(f))]
                worst, wn = 0.0, None
                for p in samples:
                    loc, nrm, idx, dist = tree.find_nearest(Vector(p))
                    if loc is None:
                        continue
                    loc = np.array(loc)
                    t = fr.tris[tri_ids[idx]]
                    n = unit(barycentric(loc, *fr.P[s][t]) @ fr.N[s][t])
                    d = float((p - loc) @ n)
                    if d > -DEEP and min_clear - d > worst:
                        worst, wn = min(min_clear - d, MAX_PUSH * 0.5), n
                if wn is not None:
                    for v in f:
                        push[v] += wn * worst
                        hits[v] += 1
            ids = np.nonzero(hits)[0]
            if not len(ids):
                break
            P[ids] += push[ids] / hits[ids][:, None]
            g.pos[s] = [P]
            moved += len(ids)
    return moved
