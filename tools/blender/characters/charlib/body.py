"""Base body mesh: fixed topology sampled from the SDF sculpts.

Topology (`BodyLayout`) is decided once per body type from the neutral shape;
`BodyLayout.positions(build, face)` then evaluates the same vertices for any
build or face/expression parameters, which is how every shape key is made.

Parts:

* trunk: one grid of `TCOLS` columns from the crown pole through the head rows
  (`head.ROWS`), the neck rings, the shoulder dome rows (rays fanned from the
  chest centre so they hug the trapezius and deltoids) and horizontal torso rows
  down to the crotch. Each side of the dome has a 4 x 4-face hole the arm grows
  from.
* legs: the bottom torso row splits at the crotch (plus three crotch vertices)
  into two `LEG_COLS` rings; rings continue down the leg and bend forward
  through the foot to a toe cap.
* arms: `ARM_COLS` rings from the dome hole along the arm, flattening through
  the wrist into the palm, closed at the knuckles; fingers and thumb are
  separate closed tubes rooted inside the palm.
* ears: small closed shells on the head.

Junction rings (the first ring after a hole) are projected onto the smooth
union of both parts' SDFs, so the shoulder and hip blends follow the sculpt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import body_sdf as B
from . import head as H
from . import sdf as S
from . import skeleton as K
from .frames import Polyline, Ring, angles_around, project_to_surface, tube_rings
from .proportions import NEUTRAL, Body, Build

TCOLS = H.HEAD_COLS
ARM_COLS = 16
LEG_COLS = 24
FINGER_COLS = 6
NECK_RINGS = 4
DOME_EPS = (64.0, 56.0, 48.0, 40.5, 33.0, 26.0, 19.0, 12.5, 6.0)
HOLE_DOME = (4, 8)  # dome rows (inclusive) bounding the arm hole
HOLE_COLS = (8, 12)  # torso columns (inclusive) bounding the left hole
N_HORIZ = 17
TORSO_DEG = np.arange(TCOLS) * (360.0 / TCOLS)
FINGER_RADIUS = {"thumb": 1.12, "f_index": 1.0, "f_middle": 1.0, "f_ring": 0.93, "f_pinky": 0.82}

REGIONS = ("head", "scalp", "ears", "neck", "torso", "upper_arms", "forearms", "hands", "hips", "thighs", "calves", "feet")

SKIN = (1.0, 0.0, 0.0)
NO_TINT = (0.0, 0.0, 0.0)


@dataclass
class Tube:
    """Rings of one limb: ring 0 may reference existing vertices."""

    name: str
    side: int
    idx: list[list[int]] = field(default_factory=list)  # [ring][col] vertex ids
    rings: list[Ring] = field(default_factory=list)  # frames of raycast rings (aligned with idx[first_ray:])
    angles: list[np.ndarray] = field(default_factory=list)
    first_ray: int = 2
    cap: int | None = None  # fan vertex at the end
    base_cap: int | None = None
    s_values: list[float] = field(default_factory=list)


class BodyLayout:
    def __init__(self, body: Body):
        self.b = body
        self.sc = body.height / 1.78
        self.n = 0
        self.trunk_rows = H.HEAD_ROWS + NECK_RINGS + len(DOME_EPS) + N_HORIZ
        self.row_neck = H.HEAD_ROWS
        self.row_dome = self.row_neck + NECK_RINGS
        self.row_horiz = self.row_dome + len(DOME_EPS)
        self.n = 1 + (self.trunk_rows - 1) * TCOLS
        self.crotch = [self._alloc(1)[0] for _ in range(3)]
        self.horiz_z = np.linspace(body.chest_z - 0.03 * self.sc, body.crotch_z + 0.012 * self.sc, N_HORIZ)
        self.dome_centre = np.array([0.0, -(body.rib_fwd - body.rib_back) / 2.0, body.chest_z])
        self.tubes: dict[str, Tube] = {}
        self.ears: dict[int, list[int]] = {}
        self.ear_template = _ear_template()
        neutral = self._trunk_positions(NEUTRAL, H.FaceParams(**self.face_defaults()))
        self._layout_legs(neutral)
        self._layout_arms(neutral)
        self._layout_fingers()
        for side in (1, -1):
            self.ears[side] = self._alloc(len(self.ear_template[0]))
        self.faces = self._faces()
        self.neighbours = _neighbours(self.n, [f for f, _ in self.faces])
        self.junctions = {key: self._junction_set(key) for key in self.tubes if key.startswith(("arm", "leg"))}
        # Neck base: the neck rings and the top dome rows.
        neck = [self.tv(r, c) for r in range(self.row_neck, self.row_dome + 3) for c in range(TCOLS)]
        self.neck_junction = np.array(neck)

    def _junction_set(self, key: str) -> np.ndarray:
        """Vertices relaxed where a limb grows out of the trunk."""
        tube = self.tubes[key]
        ids = set()
        for k in range(1, tube.first_ray + (2 if tube.name == "arm" else 1)):
            ids.update(tube.idx[k])
        if tube.name == "arm":
            ids.update(tube.idx[0])  # the hole boundary; the crotch ring stays put
        if tube.name == "arm":
            # Two trunk rings around the hole.
            r0, r1 = self.row_dome + HOLE_DOME[0], self.row_dome + HOLE_DOME[1]
            c0, c1 = self.hole_cols(tube.side)
            for r in range(r0 - 3, r1 + 4):
                for c in range(c0 - 3, c1 + 4):
                    if r0 < r < r1 and c0 < c < c1:
                        continue
                    if self.row_dome <= r < self.trunk_rows:
                        ids.add(self.tv(r, c))
        return np.array(sorted(ids))

    def _settle(self, pts: np.ndarray, ids: np.ndarray, sdf, iters: int, neutral: bool, key: str,
                neutral_build: bool = False) -> None:
        """Relax junction vertices on the neutral shape; every other shape
        projects those neutral positions onto its own surface, so builds and
        expressions move junction vertices along the normal instead of sliding
        them across the skin (their weights stay valid)."""
        if neutral:
            self._relax(pts, ids, sdf, iters)
            if not hasattr(self, "_settled"):
                self._settled = {}
            self._settled[key] = pts[ids].copy()
        elif neutral_build:
            # Junction sculpts depend on the build only: face keys leave them be.
            pts[ids] = self._settled[key]
        else:
            pts[ids] = project_to_surface(sdf, self._settled[key], iters=8)

    def _relax(self, pts: np.ndarray, ids: np.ndarray, sdf, iters: int) -> None:
        """Taubin smoothing (a shrinking step then an inflating step, so loops
        keep their size), re-projected onto the surface after each step."""
        nb = [self.neighbours[i] for i in ids]
        for _ in range(iters):
            for factor in (0.5, -0.53):
                mean = np.array([pts[n].mean(axis=0) for n in nb])
                pts[ids] = project_to_surface(sdf, pts[ids] + factor * (mean - pts[ids]), iters=4)

    # ------------------------------------------------------------------ util
    def face_defaults(self) -> dict:
        return {"softness": 1.0} if self.b.bust > 0 else {}

    def _alloc(self, k: int) -> list[int]:
        ids = list(range(self.n, self.n + k))
        self.n += k
        return ids

    def tv(self, row: int, col: int) -> int:
        """Trunk vertex id."""
        return 0 if row == 0 else 1 + (row - 1) * TCOLS + (col % TCOLS)

    def hole_cols(self, side: int) -> tuple[int, int]:
        if side > 0:
            return HOLE_COLS
        return (TCOLS - HOLE_COLS[1], TCOLS - HOLE_COLS[0])

    def in_hole(self, row: int, col: int, side: int) -> bool:
        """Is the face below `row` between col and col+1 inside the arm hole?"""
        r0, r1 = self.row_dome + HOLE_DOME[0], self.row_dome + HOLE_DOME[1]
        c0, c1 = self.hole_cols(side)
        return r0 <= row < r1 and c0 <= col < c1

    def hole_boundary(self, side: int) -> list[int]:
        r0, r1 = self.row_dome + HOLE_DOME[0], self.row_dome + HOLE_DOME[1]
        c0, c1 = self.hole_cols(side)
        loop = [self.tv(r0, c) for c in range(c0, c1 + 1)]
        loop += [self.tv(r, c1) for r in range(r0 + 1, r1 + 1)]
        loop += [self.tv(r1, c) for c in range(c1 - 1, c0 - 1, -1)]
        loop += [self.tv(r, c0) for r in range(r1 - 1, r0, -1)]
        return loop

    # --------------------------------------------------------------- trunk
    def _neck_angles(self, k: int) -> np.ndarray:
        t = (k + 1) / (NECK_RINGS + 1)
        return H.COLS_DEG + (TORSO_DEG - H.COLS_DEG) * S.smoothstep(0.0, 1.0, t)

    def _trunk_positions(self, build: Build, fp: H.FaceParams) -> np.ndarray:
        """(trunk vertex count + crotch, 3) positions, ids as in tv()."""
        b = self.b
        pts = np.zeros((max(self.crotch) + 1, 3))
        hs = H.sample(fp)
        centre = K.head_centre(b)
        head_world = H.to_world(hs.points, centre, b.head_scale)
        pts[: len(head_world)] = head_world
        torso = B.torso_sdf(b, build)
        # Dome rows.
        th = np.radians(TORSO_DEG)
        for i, eps in enumerate(DOME_EPS):
            e = math.radians(eps)
            dirs = np.stack([np.cos(e) * np.sin(th), -np.cos(e) * np.cos(th), np.full(TCOLS, np.sin(e))], axis=1)
            org = np.repeat(self.dome_centre[None], TCOLS, axis=0)
            t = S.raycast(torso, org, dirs, t_max=0.5)
            r = self.row_dome + i
            pts[self.tv(r, 0):self.tv(r, 0) + TCOLS] = org + dirs * t[:, None]
        # Horizontal rows.
        for i, z in enumerate(self.horiz_z):
            dirs = np.stack([np.sin(th), -np.cos(th), np.zeros(TCOLS)], axis=1)
            org = np.repeat(np.array([[0.0, 0.004, z]]), TCOLS, axis=0)
            t = S.raycast(torso, org, dirs, t_max=0.5)
            r = self.row_horiz + i
            pts[self.tv(r, 0):self.tv(r, 0) + TCOLS] = org + dirs * t[:, None]
        # Neck rings: interpolate between the throat row and the dome top, then
        # cast from the neck axis through the interpolated points.
        top = pts[self.tv(H.HEAD_ROWS - 1, 0):self.tv(H.HEAD_ROWS - 1, 0) + TCOLS]
        bottom = pts[self.tv(self.row_dome, 0):self.tv(self.row_dome, 0) + TCOLS]
        for k in range(NECK_RINGS):
            t = (k + 1) / (NECK_RINGS + 1)
            guide = top + (bottom - top) * t
            ang = np.radians(self._neck_angles(k))
            axis = np.stack([np.zeros(TCOLS), np.full(TCOLS, 0.008), guide[:, 2]], axis=1)
            # Keep the column azimuths (smooth twist from head to torso spacing).
            flat = np.stack([np.sin(ang), -np.cos(ang), np.zeros(TCOLS)], axis=1)
            dz = (guide[:, 2] - axis[:, 2])[:, None]
            dirs = flat + np.array([0, 0, 1.0]) * dz
            dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
            hit = S.raycast(torso, axis, dirs, t_max=0.3)
            r = self.row_neck + k
            pts[self.tv(r, 0):self.tv(r, 0) + TCOLS] = axis + dirs * hit[:, None]
        # Crotch vertices: straight down from inside the pelvis.
        last = self.row_horiz + N_HORIZ - 1
        front = pts[self.tv(last, 0)]
        back = pts[self.tv(last, TCOLS // 2)]
        for j, fr in enumerate((0.25, 0.5, 0.75)):
            y = front[1] + (back[1] - front[1]) * fr
            org = np.array([[0.0, y, b.crotch_z + 0.07 * self.sc]])
            t = S.raycast(torso, org, np.array([[0.0, 0.0, -1.0]]), t_max=0.3)
            hit = org[0] + np.array([0, 0, -1.0]) * t[0]
            # The inner thighs meet below the crotch; stop at the inseam height.
            hit[2] = max(hit[2], b.crotch_z - 0.006 * self.sc)
            pts[self.crotch[j]] = hit
        return pts

    # ---------------------------------------------------------------- legs
    def _leg_path(self, side: int) -> tuple[list[float], list[Ring]]:
        b = self.b
        hip = K.fwd(*b.hip_joint)
        knee = K.fwd(*b.knee)
        fp = K.foot_points(b)
        if side < 0:
            hip, knee = K.mirror(hip), K.mirror(knee)
            fp = {k: K.mirror(v) for k, v in fp.items()}
        sc = self.sc
        zs = list(np.linspace(b.crotch_z - 0.035 * sc, b.knee[2] + 0.05 * sc, 9))
        zs += [b.knee[2] + 0.02 * sc, b.knee[2] - 0.012 * sc]
        zs += list(np.linspace(b.knee[2] - 0.045 * sc, b.ankle[2] + 0.04 * sc, 11))
        zs += [b.ankle[2] + 0.014 * sc]
        rings = []
        ankle = fp["ankle"]
        for z in zs:
            if z >= knee[2]:
                t = (z - knee[2]) / (hip[2] - knee[2])
                c = knee + (hip - knee) * t
            else:
                t = (z - ankle[2]) / (knee[2] - ankle[2])
                c = ankle + (knee - ankle) * t
            c = c.copy()
            c[2] = z
            rings.append(Ring(c, K.vec(0, 0, -1), K.vec(0, -1, 0), K.vec(side, 0, 0) * 1.0))
        # The foot is sliced horizontally down to a flat sole: each slice is cast
        # from a centre that slides from the ankle to the middle of the foot,
        # with rays spread along an ellipse elongated heel-to-toe.
        fs = b.foot_len / 0.265
        mid = 0.5 * (fp["heel_back"] + fp["tip"])
        foot_z = [0.085, 0.071, 0.059, 0.049, 0.041, 0.034, 0.028, 0.022, 0.016, 0.010, 0.005]
        foot = []
        for z in foot_z:
            z = z * fs
            t = float(S.smoothstep(0.075 * fs, 0.035 * fs, z))
            c = ankle + (mid - ankle) * t
            c = K.vec(c[0], c[1], z)
            r = Ring(c, K.vec(0, 0, -1), K.vec(0, -1, 0), K.vec(1, 0, 0))
            r.aspect_frame = (K.vec(0, -1, 0), K.vec(1, 0, 0))
            r.aspect = (1.0 + 1.5 * t, 1.0)
            foot.append(r)
        return zs + [None] * len(foot), rings + foot

    def _layout_legs(self, neutral: np.ndarray) -> None:
        for side in (1, -1):
            last = self.row_horiz + N_HORIZ - 1
            half = TCOLS // 2
            if side > 0:
                cols = list(range(0, half + 1))
                crotch = list(reversed(self.crotch))  # back to front
            else:
                cols = [half] + list(range(half + 1, TCOLS)) + [0]
                crotch = list(self.crotch)  # front to back
            ring0 = [self.tv(last, c) for c in cols] + crotch
            zs, rings = self._leg_path(side)
            tube = Tube("leg", side)
            pts0 = neutral[ring0]
            ref = rings[0]
            ang0 = angles_around(pts0, _leg_frame(ref, side))
            order = _cyclic_order(ang0)
            ring0 = [ring0[i] for i in order]
            ang0 = np.unwrap(ang0[order])
            tube.idx.append(ring0)
            tube.idx.append(self._alloc(LEG_COLS))  # bridge
            uniform = _uniform_like(ang0)
            for k, r in enumerate(rings):
                w = S.smoothstep(0.0, 1.0, min(1.0, (k + 2) / 3.0))
                tube.angles.append(ang0 + (uniform - ang0) * w)
                tube.idx.append(self._alloc(LEG_COLS))
                tube.rings.append(_leg_frame(r, side))
            tube.cap = self._alloc(1)[0]
            tube.s_values = zs
            self.tubes[f"leg_{side}"] = tube

    # ---------------------------------------------------------------- arms
    def arm_path(self, side: int) -> tuple[Polyline, list[float]]:
        b = self.b
        sj, el, wr, _, _ = K.arm_frame(b)
        hf = K.hand_frame(b)
        hs = K.hand_scale(b)
        knuckle = wr + hf.axis * 0.094 * hs
        pts = [sj, el, wr, knuckle]
        if side < 0:
            pts = [K.mirror(p) for p in pts]
        line = Polyline(pts, smooth=0.03)
        ul, fl = b.upper_arm_len, b.forearm_len
        s = list(np.linspace(0.095 * self.sc, ul - 0.034, 7)) + [ul - 0.012, ul + 0.012]
        s += list(np.linspace(ul + 0.036, ul + fl - 0.022, 9)) + [ul + fl - 0.004]
        s += [ul + fl + h * hs for h in (0.022, 0.044, 0.064, 0.080, 0.090)]
        return line, s

    def _layout_arms(self, neutral: np.ndarray) -> None:
        hf = K.hand_frame(self.b)
        for side in (1, -1):
            line, s_vals = self.arm_path(side)
            up = K.vec(0, 0, 1)
            rings = tube_rings(line, s_vals, up)
            ul, fl = self.b.upper_arm_len, self.b.forearm_len
            thumb = hf.thumb_side if side > 0 else K.mirror(hf.thumb_side)
            for r, s in zip(rings, s_vals):
                t = S.smoothstep(ul + fl - 0.05, ul + fl + 0.03, s)
                if t > 0:
                    e1 = K.unit(thumb - r.tangent * float(thumb @ r.tangent))
                    e2 = K.unit(np.cross(r.tangent, e1))
                    r.aspect_frame = (e1, e2)
                    r.aspect = (1.0, 1.0 - 0.55 * float(t))
            # The hole boundary is not star-shaped around the arm axis, so orient
            # it by its own polygon normal (along the arm) and measure angles
            # around its centroid in the first ring's frame.
            loop = self.hole_boundary(side)
            lp = neutral[loop]
            newell = np.zeros(3)
            for i in range(len(lp)):
                newell += np.cross(lp[i], lp[(i + 1) % len(lp)])
            if float(newell @ rings[0].tangent) < 0:
                loop = loop[::-1]
                lp = neutral[loop]
            centred = Ring(lp.mean(axis=0), rings[0].tangent, rings[0].normal, rings[0].binormal)
            ang = angles_around(lp, centred)
            start = int(np.argmin(np.abs(np.angle(np.exp(1j * ang)))))
            order = np.roll(np.arange(len(loop)), -start)
            ring0 = [loop[i] for i in order]
            ang0 = np.unwrap(ang[order])
            tube = Tube("arm", side)
            tube.idx.append(ring0)
            tube.first_ray = 3
            for _ in range(tube.first_ray - 1):
                tube.idx.append(self._alloc(ARM_COLS))  # bridges
            uniform = _uniform_like(ang0)
            for k, r in enumerate(rings):
                w = 1.0  # bridges and relaxation absorb the uneven hole boundary
                tube.angles.append(ang0 + (uniform - ang0) * w)
                tube.idx.append(self._alloc(ARM_COLS))
                tube.rings.append(r)
            tube.cap = self._alloc(1)[0]
            tube.s_values = s_vals
            self.tubes[f"arm_{side}"] = tube

    # ------------------------------------------------------------- fingers
    def finger_paths(self, side: int) -> dict[str, list[np.ndarray]]:
        out = {}
        for f in K.FINGER_ORDER:
            ch = K.finger_chain(self.b, f)
            d = K.unit(ch[1] - ch[0])
            base = ch[0] - d * 0.013 * K.hand_scale(self.b)
            out[f] = [base] + ch
        th = K.thumb_chain(self.b)
        out["thumb"] = th
        if side < 0:
            out = {k: [K.mirror(p) for p in v] for k, v in out.items()}
        return out

    def _finger_s(self, name: str, line: Polyline) -> list[float]:
        L = line.cum
        if name == "thumb":
            l1 = L[1]
            return [0.42 * l1, 0.75 * l1, L[1], 0.5 * (L[1] + L[2]), L[2], L[2] + 0.45 * (L[3] - L[2]), L[2] + 0.8 * (L[3] - L[2])]
        return [0.0, L[1], 0.5 * (L[1] + L[2]), L[2], 0.5 * (L[2] + L[3]), L[3], L[3] + 0.45 * (L[4] - L[3]),
                L[3] + 0.8 * (L[4] - L[3])]

    def _layout_fingers(self) -> None:
        hf = K.hand_frame(self.b)
        for side in (1, -1):
            dorsal = -hf.palm_normal if side > 0 else K.mirror(-hf.palm_normal)
            for name, pts in self.finger_paths(side).items():
                line = Polyline(pts, smooth=0.006)
                s_vals = self._finger_s(name, line)
                rings = tube_rings(line, s_vals, dorsal)
                tube = Tube(name, side, first_ray=0)
                tube.rings = rings
                tube.s_values = s_vals
                base = np.arange(FINGER_COLS) * (2 * np.pi / FINGER_COLS)
                for _ in rings:
                    tube.idx.append(self._alloc(FINGER_COLS))
                    tube.angles.append(base)
                tube.cap = self._alloc(1)[0]
                tube.base_cap = self._alloc(1)[0]
                self.tubes[f"{name}_{side}"] = tube

    # ------------------------------------------------------------ positions
    def positions(self, build: Build = NEUTRAL, fp: H.FaceParams | None = None) -> np.ndarray:
        if fp is None:
            fp = H.FaceParams(**self.face_defaults())
        b = self.b
        neutral = build == NEUTRAL and fp == H.FaceParams(**self.face_defaults())
        if not neutral and not hasattr(self, "_settled"):
            self.positions()  # the neutral shape defines where junction vertices sit
        pts = np.zeros((self.n, 3))
        trunk = self._trunk_positions(build, fp)
        pts[: len(trunk)] = trunk[: len(trunk)]
        torso = B.torso_sdf(b, build)
        self._settle(pts, self.neck_junction, torso, 4, neutral, "neck", build == NEUTRAL)
        for key, tube in self.tubes.items():
            side = tube.side
            if tube.name == "leg":
                part = B.leg_sdf(b, build, side)
                junction = torso
            elif tube.name == "arm":
                part = B.arm_sdf(b, build, side)
                junction = torso
            else:
                continue
            fr = tube.first_ray
            for k, (ring, ang) in enumerate(zip(tube.rings, tube.angles)):
                dirs = ring.dirs(ang)
                org = np.repeat(ring.centre[None], len(ang), axis=0)
                t = S.raycast(part, org, dirs, t_max=0.3)
                pts[tube.idx[k + fr]] = org + dirs * t[:, None]
            # Bridge rings onto the smooth union of both parts, then relax the
            # whole junction so the hole's corners do not crease.
            union = _union(junction, part, 0.035 if tube.name == "arm" else 0.03)
            for j in range(1, fr):
                t = j / fr
                mid = pts[tube.idx[0]] * (1 - t) + pts[tube.idx[fr]] * t
                pts[tube.idx[j]] = project_to_surface(union, mid)
            self._settle(pts, self.junctions[key], union, 14 if tube.name == "arm" else 4, neutral, key, build == NEUTRAL)
            last = tube.rings[-1]
            t = S.raycast(part, last.centre[None], last.tangent[None], t_max=0.2)
            pts[tube.cap] = last.centre + last.tangent * t[0]
        # Fingers and thumbs.
        for key, tube in self.tubes.items():
            if tube.name in ("leg", "arm"):
                continue
            side = tube.side
            chain = self.finger_paths(1)[tube.name]  # left-hand coordinates; the SDF mirrors
            radius = b.finger_r * FINGER_RADIUS[tube.name]
            if tube.name == "thumb":
                fsdf = B.finger_sdf(b, build, chain, radius, side)
            else:
                fsdf = _finger_with_root(b, build, chain, radius, side)
            for k, (ring, ang) in enumerate(zip(tube.rings, tube.angles)):
                dirs = ring.dirs(ang)
                org = np.repeat(ring.centre[None], len(ang), axis=0)
                t = S.raycast(fsdf, org, dirs, t_max=0.05, min_step=0.0002)
                pts[tube.idx[k]] = org + dirs * t[:, None]
            last = tube.rings[-1]
            t = S.raycast(fsdf, last.centre[None], last.tangent[None], t_max=0.05, min_step=0.0002)
            pts[tube.cap] = last.centre + last.tangent * t[0]
            first = tube.rings[0]
            pts[tube.base_cap] = first.centre - first.tangent * 0.002
        # Ears follow the head centre.
        verts, _ = self.ear_template
        centre = K.head_centre(b)
        for side, ids in self.ears.items():
            local = verts.copy()
            if side < 0:
                local[:, 0] = -local[:, 0]
            pts[ids] = H.to_world(local, centre, b.head_scale)
        return pts

    # ---------------------------------------------------------------- faces
    def _faces(self) -> list[tuple[tuple[int, ...], str]]:
        """Faces with a tag naming the part they belong to."""
        faces: list[tuple[tuple[int, ...], str]] = []
        C = TCOLS
        for c in range(C):
            faces.append(((0, self.tv(1, c), self.tv(1, c + 1)), "head:0"))
        for r in range(1, self.trunk_rows - 1):
            for c in range(C):
                if self.in_hole(r, c, 1) or self.in_hole(r, c, -1):
                    continue
                faces.append(((self.tv(r, c), self.tv(r + 1, c), self.tv(r + 1, c + 1), self.tv(r, c + 1)), f"trunk:{r}:{c}"))
        for key, tube in self.tubes.items():
            cols = len(tube.idx[0])
            for k in range(len(tube.idx) - 1):
                a, bb = tube.idx[k], tube.idx[k + 1]
                for c in range(cols):
                    c1 = (c + 1) % cols
                    faces.append(((a[c], bb[c], bb[c1], a[c1]), f"{key}:{k}:{c}"))
            last = tube.idx[-1]
            for c in range(cols):
                faces.append(((last[c], tube.cap, last[(c + 1) % cols]), f"{key}:cap:{c}"))
            if tube.base_cap is not None:
                first = tube.idx[0]
                for c in range(cols):
                    faces.append(((first[(c + 1) % cols], tube.base_cap, first[c]), f"{key}:base:{c}"))
        _, tris = self.ear_template
        for side, ids in self.ears.items():
            for t in tris:
                f = tuple(ids[i] for i in t)
                faces.append((f if side > 0 else tuple(reversed(f)), f"ear_{side}"))
        return faces


def _leg_frame(r: Ring, side: int) -> Ring:
    """Leg rings measure angles like the torso: 0 = front, 90 deg = +X."""
    out = Ring(r.centre, r.tangent, r.normal, r.binormal, r.aspect, r.aspect_frame)
    out.binormal = K.unit(K.vec(1, 0, 0) - r.tangent * float(r.tangent @ K.vec(1, 0, 0)))
    n = np.cross(out.binormal, r.tangent)
    # Pick the normal that points forward (or up on the foot).
    ref = r.normal
    out.normal = K.unit(n if float(n @ ref) >= 0 else -n)
    return out


def _neighbours(n: int, faces: list[tuple[int, ...]]) -> list[np.ndarray]:
    nb: list[set[int]] = [set() for _ in range(n)]
    for f in faces:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            nb[a].add(b)
            nb[b].add(a)
    return [np.array(sorted(s), dtype=np.int64) for s in nb]


def _union(a, b, k: float):
    return lambda p: S.smin(a(p), b(p), k)


def _finger_with_root(b: Body, build: Build, ext: list[np.ndarray], radius: float, side: int):
    """Finger SDF extended back into the palm (the root ring sits inside)."""
    core = B.finger_sdf(b, build, ext[1:], radius, side)
    root = ext[0]
    knuckle = ext[1]

    def f(p: np.ndarray) -> np.ndarray:
        q = p if side > 0 else p * np.array([-1.0, 1.0, 1.0])
        r = S.capsule(q, root, knuckle, radius * 0.95, radius * 1.02)
        return S.smin(core(p), r, 0.003)

    return f


EAR_RINGS = 4
EAR_COLS = 10


def _ear_template() -> tuple[np.ndarray, list[tuple[int, ...]]]:
    """A closed ear shell in head-local cm (left ear).

    Concentric rings over an egg outline (6 cm tall, 3.2 cm wide, top tilted
    back 14 degrees): the outer face is a bowl (concha) with a raised rim, the
    inner face sinks into the head so the root is hidden.
    """
    centre = np.array([6.9, -1.25, -1.6])
    tilt = math.radians(14)
    verts: list[np.ndarray] = []

    def place(x: float, a: float, h: float) -> np.ndarray:
        # (a forward, h up) in the ear plane, tilted so the top leans back.
        f = a * math.cos(tilt) - h * math.sin(tilt)
        u = h * math.cos(tilt) + a * math.sin(tilt)
        return centre + np.array([x, f, u])

    for face in (0, 1):  # 0 = outer bowl, 1 = inner back
        for i in range(EAR_RINGS):
            rr = (i + 1) / EAR_RINGS
            for j in range(EAR_COLS):
                phi = 2 * math.pi * j / EAR_COLS
                h = 3.0 * math.cos(phi) * rr
                w = (1.45 + 0.2 * math.cos(phi)) * rr
                a = -w * math.sin(phi)  # j increasing goes toward the back first
                back_flare = 0.35 * max(0.0, -a / 1.6)
                if face == 0:
                    x = 0.18 + back_flare + (0.35 * rr ** 3) - 0.3 * (1 - rr)
                else:
                    x = -0.35 + back_flare * 0.6 - 0.4 * (1 - rr)
                verts.append(place(x, a, h))
    verts.append(place(-0.05, 0.0, -0.2))  # bowl centre
    verts.append(place(-0.75, 0.0, 0.0))  # inner centre
    c_out, c_in = len(verts) - 2, len(verts) - 1

    def vid(face: int, i: int, j: int) -> int:
        return face * EAR_RINGS * EAR_COLS + i * EAR_COLS + (j % EAR_COLS)

    polys: list[tuple[int, ...]] = []
    for face, c in ((0, c_out), (1, c_in)):
        for j in range(EAR_COLS):
            t = (c, vid(face, 0, j + 1), vid(face, 0, j))
            polys.append(t if face == 0 else tuple(reversed(t)))
        for i in range(EAR_RINGS - 1):
            for j in range(EAR_COLS):
                q = (vid(face, i, j), vid(face, i, j + 1), vid(face, i + 1, j + 1), vid(face, i + 1, j))
                polys.append(q if face == 0 else tuple(reversed(q)))
    o = EAR_RINGS - 1
    for j in range(EAR_COLS):
        polys.append((vid(0, o, j), vid(0, o, j + 1), vid(1, o, j + 1), vid(1, o, j)))
    return np.array(verts), polys


def _uniform_like(ang0: np.ndarray) -> np.ndarray:
    """Evenly spaced angles with the phase that best matches `ang0` (unwrapped,
    increasing), so the first evenly spaced ring does not twist."""
    n = len(ang0)
    step = 2 * np.pi / n
    phase = np.angle(np.mean(np.exp(1j * (ang0 - np.arange(n) * step))))
    base = phase + np.arange(n) * step
    # Keep each angle within half a turn of the original so blends do not spin.
    return base + 2 * np.pi * np.round((ang0 - base) / (2 * np.pi))


def _cyclic_order(ang: np.ndarray) -> np.ndarray:
    """Indices that walk a closed loop (given in its own cyclic order) with
    increasing angle, starting at its smallest angle. Unlike sorting, this
    keeps loops that are not star-shaped around the axis intact."""
    n = len(ang)
    steps = np.angle(np.exp(1j * (np.roll(ang, -1) - ang)))
    idx = np.arange(n)
    if steps.sum() < 0:
        idx = idx[::-1]
    start = int(np.argmin(ang[idx]))
    return np.roll(idx, -start)
