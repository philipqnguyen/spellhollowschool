"""Landmarks on the base body that clothing is cut against (rows, rings, axes)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import head as H
from . import skeleton as K
from .gear_lib import GearBuilder


def parse_tag(tag: str):
    """("trunk", r, c) | (tube key, k, c) | (tube key, "cap"/"base", c) | (tag,)."""
    parts = tag.split(":")
    if parts[0] == "trunk":
        return ("trunk", int(parts[1]), int(parts[2]))
    if len(parts) == 3:
        k = parts[1]
        return (parts[0], int(k) if k.lstrip("-").isdigit() else k, int(parts[2]))
    return tuple(parts)


# Visible heads that replace the head sculpt for hats and hair (a body
# reshaped onto a sculpt keeps the sculpt's own cranium, which our head
# sculpt cannot match by a uniform scale): body id -> BVH in head-local cm.
HEAD_SURFACES: dict = {}
HEAD_SURFACES_WORLD: dict = {}  # the same heads in world metres (hair pressed under hats)


def register_head_surface(body, V: np.ndarray, tris: np.ndarray) -> None:
    """Hats and hair of `body` are cut against this head mesh (world
    positions, triangles) instead of the head sculpt."""
    from mathutils.bvhtree import BVHTree

    c = K.head_centre(body)
    d = (V - c) / (0.01 * body.head_scale)
    loc = np.stack([d[:, 0], -d[:, 1], d[:, 2]], axis=1)
    HEAD_SURFACES[body.id] = BVHTree.FromPolygons(loc.tolist(), np.asarray(tris).tolist(), all_triangles=True)
    HEAD_SURFACES_WORLD[body.id] = BVHTree.FromPolygons(np.asarray(V, dtype=np.float64).tolist(), np.asarray(tris).tolist(),
                                                        all_triangles=True)


def head_ray_t(body, dirs: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    """Distance (head-local cm) from the head centre to the registered head
    surface along each direction; `fallback` where none is registered or a
    ray misses it."""
    bvh = HEAD_SURFACES.get(getattr(body, "id", None))
    if bvh is None:
        return fallback
    from mathutils import Vector

    out = np.array(fallback, dtype=np.float64).copy()
    for i, d in enumerate(np.asarray(dirs, dtype=np.float64).reshape(-1, 3)):
        hit = bvh.ray_cast(Vector((0.0, 0.0, 0.0)), Vector(tuple(d)), 40.0)
        if hit[0] is not None:
            out[i] = hit[3]
    return out


# Painted brow, upper lash line and eye centre of a reshaped body's face, as
# polar angles round its head centre (hair fringes are designed for our
# heads' brow and eye heights): body id -> (brow phi, lash phi, eye phi).
FACE_PHI: dict = {}
_REF_PHI: dict = {}


def _face_phis(fr, centre: np.ndarray) -> tuple[float, float, float]:
    """Polar angles (degrees from straight up) of the left brow centre, the
    top of the upper lash line and the eye opening's middle of a painted
    face frame."""
    from . import mh_face as MF

    pol = MF.eye_polys(fr.st)
    out = []
    for t in (float(pol["brow"][:, 1].mean()), float(pol["eye_all"][:, 1].max()),
              0.5 * (float(pol["eye_open"][:, 1].max()) + float(pol["eye_open"][:, 1].min()))):
        a = fr.eye[0] / fr.R0
        p = np.array([fr.R0 * np.sin(a), fr.axis_y - fr.R0 * np.cos(a), fr.eye[1] + t])
        d = p - centre
        out.append(float(np.degrees(np.arctan2(np.hypot(d[0], d[1]), d[2]))))
    return out[0], out[1], out[2]


def register_face(body, fr) -> None:
    """Hair fringes of `body` are refitted to this painted face (`phi_warp`)."""
    FACE_PHI[body.id] = _face_phis(fr, np.asarray(K.head_centre(body), dtype=np.float64))
    print(f"[gear_ctx] {body.id}: face brow/lash/eye phi {np.round(FACE_PHI[body.id], 1).tolist()} "
          f"(our head {np.round(ref_face_phi(body.id), 1).tolist()})", flush=True)


def ref_face_phi(body_id: str) -> tuple[float, float, float]:
    """Brow and lash-line polar angles of our scripted anime head (mh_head),
    the head every fringe was designed on."""
    if body_id not in _REF_PHI:
        from . import mh_face as MF
        from . import mh_head as MHH
        from . import proportions as P
        from . import sdf as S

        b = P.get(body_id)
        ex, eu = MHH.EYE_POS[body_id]
        t = S.raycast(MHH.head_sdf(MHH.PARAMS[body_id]), np.array([[ex, 0.0, eu]]), np.array([[0.0, 1.0, 0.0]]), t_max=30.0)
        c = np.asarray(K.head_centre(b), dtype=np.float64)
        sc = 0.01 * b.head_scale
        eye = H.to_world(np.array([[ex, float(t[0]), eu]]), c, b.head_scale)[0]
        R0 = float(np.hypot(eye[0], c[1] - eye[1]))
        st = MF.STYLE[body_id]
        fr = MF.FaceFrame(b, float(c[1]), R0, np.array([R0 * np.arctan2(eye[0], c[1] - eye[1]) + st["eye_du"], eye[2] + st["eye_dv"]]),
                          np.zeros(2), np.zeros(2), np.zeros(3), np.ones(3), 0.0, 0.0)
        del sc
        _REF_PHI[body_id] = _face_phis(fr, c)
    return _REF_PHI[body_id]


def phi_warp(body, phi, theta) -> np.ndarray:
    """Polar angles of a hairstyle (designed on our heads) moved onto a
    reshaped body's face: over the front of the head the brow, the upper
    lash line and the eye's middle of the design go to this face's, so
    fringes end at the same place on the face (the crown, the sides behind the temples and the nape
    are unchanged). Identity for bodies without a registered face."""
    phi = np.asarray(phi, dtype=np.float64)
    f = FACE_PHI.get(getattr(body, "id", None))
    if f is None:
        return phi
    r = ref_face_phi(body.id)
    xs = [0.0, 40.0, r[0], r[1], r[2], 135.0, 180.0]
    ys = [0.0, 40.0, f[0], f[1], f[2], 135.0, 180.0]
    if not (all(a < b for a, b in zip(xs[1:-1], xs[2:])) and all(a < b for a, b in zip(ys[1:-1], ys[2:]))):
        return phi
    th = np.asarray(theta, dtype=np.float64) % 360.0
    side = np.where(th <= 180.0, th, 360.0 - th)
    w = np.clip(1.0 - side / 80.0, 0.0, 1.0) ** 1.2
    return phi + w * (np.interp(phi, xs, ys) - phi)


@dataclass
class Landmarks:
    gb: GearBuilder

    def __post_init__(self):
        lay = self.gb.layout
        b = self.gb.body
        self.lay = lay
        self.b = b
        self.sc = b.height / 1.78
        self.row_neck_base = lay.row_dome
        self.waist_row = lay.row_horiz + int(np.argmin(np.abs(lay.horiz_z - b.waist_z)))
        self.hip_row = lay.row_horiz + int(np.argmin(np.abs(lay.horiz_z - b.hip_z)))
        self.last_row = lay.trunk_rows - 1
        arm = lay.tubes["arm_1"]
        ul, fl = b.upper_arm_len, b.forearm_len
        s = np.array(arm.s_values)
        self.arm_first = arm.first_ray
        self.elbow_ring = arm.first_ray + int(np.argmin(np.abs(s - ul)))
        self.wrist_ring = arm.first_ray + int(np.argmin(np.abs(s - (ul + fl))))
        leg = lay.tubes["leg_1"]
        zs = [z for z in leg.s_values]
        self.leg_first = leg.first_ray
        self.leg_z = {}
        for i, z in enumerate(zs):
            if z is not None:
                self.leg_z[leg.first_ray + i] = z
        self.knee_ring = min(self.leg_z, key=lambda k: abs(self.leg_z[k] - b.knee[2]))
        self.ankle_ring = max(k for k in self.leg_z)  # last horizontal leg ring above the foot
        self.foot_first = self.ankle_ring + 1
        self.n_leg_rings = len(leg.idx)

    # ------------------------------------------------------------ queries
    def trunk(self, tag: str):
        t = parse_tag(tag)
        return (t[1], t[2]) if t[0] == "trunk" else None

    def tube(self, tag: str):
        t = parse_tag(tag)
        if t[0] != "trunk" and len(t) == 3:
            return t
        return None

    def is_arm(self, tag: str) -> bool:
        return tag.startswith("arm_")

    def is_leg(self, tag: str) -> bool:
        return tag.startswith("leg_")

    def is_hand(self, tag: str) -> bool:
        return tag.split("_")[0] in ("f", "thumb") or tag.startswith(("f_index", "f_middle", "f_ring", "f_pinky", "thumb"))

    def arm_ring_s(self, k: int) -> float:
        arm = self.lay.tubes["arm_1"]
        i = k - arm.first_ray
        return arm.s_values[i] if 0 <= i < len(arm.s_values) else 0.0

    def leg_axis(self, side: int, z: float) -> np.ndarray:
        b = self.b
        hip = K.fwd(*b.hip_joint)
        knee = K.fwd(*b.knee)
        ankle = K.foot_points(b)["ankle"]  # the skeleton's ankle (foot_points reads Body.ankle[1] as y)
        if z >= knee[2]:
            t = (z - knee[2]) / (hip[2] - knee[2])
            c = knee + (hip - knee) * t
        else:
            t = (z - ankle[2]) / (knee[2] - ankle[2])
            c = ankle + (knee - ankle) * t
        c = c.copy()
        c[2] = z
        if side < 0:
            c[0] = -c[0]
        return c

    def body_leg_radius(self, shape: str, side: int, z: float) -> float:
        """Largest horizontal distance of the body leg ring nearest height z."""
        gb = self.gb
        k = min(self.leg_z, key=lambda kk: abs(self.leg_z[kk] - z))
        ids = gb.ring(f"leg_{side}", k)
        p = gb.P[shape][ids]
        c = self.leg_axis(side, float(p[:, 2].mean()))
        return float(np.max(np.linalg.norm((p - c)[:, :2], axis=1)))

    def head_centre(self) -> np.ndarray:
        return K.head_centre(self.b)

    def head_scale(self) -> float:
        return self.b.head_scale

    def head_point(self, phi_deg: float, theta_deg: float, extra_cm: float = 0.0, face_fit: bool = False) -> np.ndarray:
        """Point on the neutral head sculpt along a head ray (+ extra cm).
        `face_fit` (hair): the polar angle is refitted to a reshaped body's
        painted face first (`phi_warp`)."""
        from . import sdf as S

        if face_fit:
            phi_deg = float(phi_warp(self.b, phi_deg, theta_deg))
        d = H.ray_dirs(np.array([[phi_deg]]), np.array([[theta_deg]])).reshape(1, 3)
        t = S.raycast(H.head_sdf(H.FaceParams()), np.zeros((1, 3)), d, t_max=30.0)
        t = head_ray_t(self.b, d, t)
        local = d * (t[0] + extra_cm)
        return H.to_world(local, self.head_centre(), self.head_scale())[0]
