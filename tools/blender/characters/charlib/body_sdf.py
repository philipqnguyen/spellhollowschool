"""SDF sculpts of the torso, arms, legs, hands and fingers (metres, Blender axes).

Each function returns an SDF closure for one body and build. Left-side parts
are written once; right-side queries mirror the point (x -> -x). Builds change
flesh radii only, around the fixed joints from `skeleton`.
"""

from __future__ import annotations

import numpy as np

from . import sdf as S
from . import skeleton as K
from .proportions import Body, Build


def _mx(p: np.ndarray) -> np.ndarray:
    q = p.copy()
    q[:, 0] = np.abs(q[:, 0])
    return q


def _flip(p: np.ndarray) -> np.ndarray:
    q = p.copy()
    q[:, 0] = -q[:, 0]
    return q


def torso_sdf(b: Body, w: Build):
    """Torso and neck, including the shoulder caps and the tops of the thighs."""
    k_chest = w.k(slim=-0.07, muscular=0.08, heavy=0.10)
    k_waist = w.k(slim=-0.10, muscular=0.02, heavy=0.24)
    k_belly = w.k(slim=-0.12, muscular=-0.02, heavy=0.42)
    k_hip = w.k(slim=-0.06, muscular=0.02, heavy=0.13)
    k_glute = w.k(slim=-0.10, muscular=0.10, heavy=0.18)
    k_lat = w.k(slim=-0.06, muscular=0.14, heavy=0.07)
    k_trap = w.k(slim=-0.10, muscular=0.28, heavy=0.12)
    k_delt = w.k(slim=-0.10, muscular=0.22, heavy=0.12)
    k_neck = w.k(slim=-0.07, muscular=0.13, heavy=0.15)
    k_thigh = w.k(slim=-0.11, muscular=0.10, heavy=0.20)
    k_pec = w.k(slim=-0.3, muscular=0.9, heavy=0.3)
    sjx, sjf, sjz = b.shoulder_joint
    hj = K.fwd(*b.hip_joint)
    nb = b.neck_base_z
    rib_c = (0.0, -(b.rib_fwd - b.rib_back) / 2.0, b.chest_z)
    rib_r = (b.rib_w * k_chest, (b.rib_fwd + b.rib_back) / 2.0 * k_chest, (0.215 - 0.02 * b.bust) * b.height / 1.78)
    waist_c = (0.0, -(b.waist_fwd - b.waist_back) / 2.0 - 0.032 * w.heavy, b.waist_z + 0.015)
    waist_r = (b.waist_w * k_waist, (b.waist_fwd + b.waist_back) / 2.0 * k_belly, 0.17 * b.height / 1.78)
    hip_c = (0.0, (b.hip_back - b.hip_fwd) / 2.0, b.hip_z)
    hip_r = (b.hip_w * k_hip, (b.hip_fwd + b.hip_back) / 2.0 * (0.5 + 0.5 * k_hip), 0.15 * b.height / 1.78)
    sc = b.height / 1.78

    def f(p: np.ndarray) -> np.ndarray:
        m = _mx(p)
        rib = S.ellipsoid(p, rib_c, rib_r)
        lat = S.ellipsoid(p, (0.0, 0.022, b.chest_z - 0.03 * sc), (b.rib_w * 1.02 * k_lat, 0.092 * k_chest, 0.15 * sc))
        belly = S.ellipsoid(p, waist_c, waist_r)
        pelvis = S.ellipsoid(p, hip_c, hip_r)
        glute = S.ellipsoid(m, (0.064 * sc, b.hip_back - 0.058 * k_glute, b.hip_z - 0.035 * sc),
                            (0.082 * k_glute, 0.062 * k_glute, 0.088 * sc))
        thigh = S.ellipsoid(m, (hj[0], hj[1] + 0.004, b.crotch_z + 0.035 * sc),
                            (b.thigh_r * k_thigh * 0.96, b.thigh_r * k_thigh, 0.09 * sc))
        trap = S.capsule(m, (0.0, 0.040, nb + 0.035 * sc), (sjx - 0.032, 0.012, sjz + 0.012 * sc), 0.040 * k_trap, 0.026 * k_trap)
        delt = S.ellipsoid(m, (b.deltoid_x - 0.05 * k_delt, -0.002, sjz - 0.04 * sc), (0.05 * k_delt, 0.054 * k_delt, 0.05 * sc))
        clav = S.capsule(m, (0.015, -(b.rib_fwd - 0.05), nb - 0.008), (sjx - 0.012, -0.035, sjz + 0.022 * sc), 0.016, 0.017)
        neck = S.capsule(p, (0.0, 0.014, nb - 0.06 * sc), (0.0, 0.006, nb + 0.20 * sc), b.neck_r * k_neck, b.neck_r * k_neck * 0.97)
        d = S.union([rib, lat], 0.04)
        d = S.smin(d, belly, 0.05)
        d = S.smin(d, pelvis, 0.08)
        d = S.smin(d, glute, 0.03)
        d = S.smin(d, thigh, 0.04)
        d = S.smin(d, trap, 0.035)
        d = S.smin(d, delt, 0.03)
        d = S.smin(d, clav, 0.02)
        d = S.smin(d, neck, 0.035)
        if b.bust > 0:
            bust = S.ellipsoid(m, (0.068 * sc, -(b.rib_fwd - 0.022), b.chest_z + 0.0 * sc),
                               (0.058 * sc, 0.042 * b.bust * (0.6 + 0.4 * k_chest), 0.05 * sc))
            d = S.smin(d, bust, 0.035)
        else:
            pec = S.ellipsoid(m, (0.068 * sc, -(b.rib_fwd - 0.03), b.chest_z + 0.035 * sc),
                              (0.08 * sc, 0.014 * k_pec * b.chest_bulk + 0.008, 0.06 * sc))
            d = S.smin(d, pec, 0.03)
        return d

    return f


def arm_sdf(b: Body, w: Build, side: int = 1):
    """Upper arm, forearm, wrist and palm (no fingers). side = 1 left, -1 right."""
    k_up = w.k(slim=-0.12, muscular=0.22, heavy=0.22)
    k_fore = w.k(slim=-0.10, muscular=0.13, heavy=0.12)
    k_hand = w.k(slim=-0.03, muscular=0.04, heavy=0.07)
    sj, el, wr, d_up, d_fore = K.arm_frame(b)
    hf = K.hand_frame(b)
    hs = K.hand_scale(b)
    front = K.unit(np.cross(d_up, K.vec(0, 0, 1)))  # toward -Y for the left arm
    if front[1] > 0:
        front = -front
    up = K.unit(np.cross(front, d_up))
    if up[2] < 0:
        up = -up
    biceps_c = sj + d_up * (0.55 * b.upper_arm_len) + front * 0.012
    triceps_c = sj + d_up * (0.45 * b.upper_arm_len) - front * 0.010 + up * 0.006
    delt_c = sj + d_up * 0.035 + up * 0.012
    brach_c = el + d_fore * 0.07 + up * 0.006
    basis_up = np.stack([K.unit(np.cross(d_up, up)), d_up, up])
    basis_fore = np.stack([K.unit(np.cross(d_fore, up)), d_fore, up])
    knuckles = wr + hf.axis * 0.094 * hs
    thenar_c = wr + hf.axis * 0.03 * hs + hf.thumb_side * 0.024 * hs + hf.palm_normal * 0.008 * hs
    ar = b.arm_r * k_up
    er = b.elbow_r * (0.5 + 0.5 * k_fore)
    fr = b.forearm_r * k_fore

    def f(p: np.ndarray) -> np.ndarray:
        q = p if side > 0 else _flip(p)
        upper = S.capsule(q, sj, el, ar * 0.98, er)
        delt = S.ellipsoid_rot(q, delt_c, (0.054 * k_up, 0.10, 0.054 * k_up), basis_up)
        biceps = S.ellipsoid_rot(q, biceps_c, (ar * 0.78, 0.10, ar * 0.72), basis_up)
        triceps = S.ellipsoid_rot(q, triceps_c, (ar * 0.8, 0.11, ar * 0.78), basis_up)
        fore = S.capsule(q, el, wr - d_fore * 0.03, fr * 0.92, b.wrist_t * 1.15 * k_hand)
        brach = S.ellipsoid_rot(q, brach_c, (fr * 0.95, 0.085, fr * 0.82), basis_fore)
        wrist = S.flat_capsule(q, wr - d_fore * 0.05, wr + hf.axis * 0.012,
                               (b.wrist_w * 1.12 * k_hand, b.wrist_t * 1.12 * k_hand), (b.wrist_w * k_hand, b.wrist_t * k_hand),
                               hf.palm_normal)
        palm = S.flat_capsule(q, wr + hf.axis * 0.004, knuckles,
                              (b.wrist_w * 1.0 * k_hand, b.palm_t * 0.5 * k_hand),
                              (b.palm_w * 0.47 * k_hand, b.palm_t * 0.42 * k_hand), hf.palm_normal)
        thenar = S.ellipsoid(q, thenar_c, (0.017 * hs, 0.017 * hs, 0.017 * hs))
        d = S.smin(upper, delt, 0.045)
        d = S.smin(d, biceps, 0.04)
        d = S.smin(d, triceps, 0.04)
        d = S.smin(d, fore, 0.025)
        d = S.smin(d, brach, 0.025)
        d = S.smin(d, wrist, 0.02)
        d = S.smin(d, palm, 0.018)
        d = S.smin(d, thenar, 0.012)
        return d

    return f


def leg_sdf(b: Body, w: Build, side: int = 1):
    """Thigh, knee, calf, ankle and foot. side = 1 left, -1 right."""
    k_thigh = w.k(slim=-0.11, muscular=0.10, heavy=0.20)
    k_calf = w.k(slim=-0.10, muscular=0.11, heavy=0.12)
    k_foot = w.k(slim=-0.02, muscular=0.02, heavy=0.05)
    hip = K.fwd(*b.hip_joint)
    knee = K.fwd(*b.knee)
    fp = K.foot_points(b)
    ankle = fp["ankle"]
    sc = b.height / 1.78
    fs = b.foot_len / 0.265
    tr = b.thigh_r * k_thigh
    kr = b.knee_r * (0.6 + 0.4 * k_calf)
    cr = b.calf_r * k_calf
    ar = b.ankle_r * k_foot
    heel_c = fp["heel_back"] + K.vec(0, -0.030 * fs, 0.008 * fs)
    ball_c = fp["ball"] + K.vec(0.0, 0.0, 0.004 * fs)
    toe_c = fp["tip"] + K.vec(-0.004, 0.028 * fs, 0.0)

    def f(p: np.ndarray) -> np.ndarray:
        q = p if side > 0 else _flip(p)
        thigh = S.capsule(q, hip + K.vec(0.004, 0.0, 0.02), knee + K.vec(0, 0, 0.05), tr, kr * 1.12)
        quad = S.ellipsoid(q, knee + K.vec(0.004, -0.028, 0.15 * sc), (tr * 0.78, tr * 0.62, 0.16 * sc))
        add = S.ellipsoid(q, hip + K.vec(-0.032, 0.0, -0.10 * sc), (tr * 0.62, tr * 0.72, 0.13 * sc))
        ham = S.ellipsoid(q, knee + K.vec(0.0, 0.026, 0.2 * sc), (tr * 0.72, tr * 0.6, 0.17 * sc))
        kn = S.sphere(q, knee, kr)
        patella = S.ellipsoid(q, knee + K.vec(0, -kr * 0.72, 0.01), (0.026 * sc, 0.016, 0.03 * sc))
        shin = S.capsule(q, knee, ankle + K.vec(0, 0, 0.03), kr * 0.82, ar * 1.05)
        calf = S.ellipsoid(q, knee + K.vec(0.002, 0.022, -0.13 * sc), (cr * 0.9, cr * 0.82, 0.13 * sc))
        ank = S.sphere(q, ankle + K.vec(0, 0, 0.004), ar * 1.1)
        heel = S.ellipsoid(q, heel_c, (0.031 * fs * k_foot, 0.038 * fs, 0.036 * fs))
        body = S.flat_capsule(q, heel_c + K.vec(0, -0.01, 0.004), ball_c,
                              (0.032 * fs * k_foot, 0.034 * fs), (0.045 * fs * k_foot, 0.020 * fs), K.vec(0, 0, 1))
        instep = S.ellipsoid(q, ankle + K.vec(0.002, -0.045 * fs, -0.03 * fs), (0.034 * fs, 0.05 * fs, 0.03 * fs))
        toes = S.ellipsoid(q, toe_c + K.vec(0, 0.0, 0.0), (0.045 * fs * k_foot, 0.04 * fs, 0.019 * fs))
        d = S.smin(thigh, quad, 0.04)
        d = S.smin(d, add, 0.04)
        d = S.smin(d, ham, 0.04)
        d = S.smin(d, kn, 0.03)
        d = S.smin(d, patella, 0.012)
        d = S.smin(d, shin, 0.03)
        d = S.smin(d, calf, 0.035)
        d = S.smin(d, ank, 0.02)
        foot = S.smin(heel, body, 0.02)
        foot = S.smin(foot, instep, 0.02)
        foot = S.smin(foot, toes, 0.02)
        d = S.smin(d, foot, 0.02)
        # Flat sole.
        d = S.smax(d, -q[:, 2], 0.006)
        return d

    return f


def finger_sdf(b: Body, w: Build, chain: list[np.ndarray], radius: float, side: int = 1):
    """One finger along its 4 joint points (left-hand coordinates)."""
    k = w.k(slim=-0.04, muscular=0.05, heavy=0.09)
    r0 = radius * k
    radii = (r0 * 1.0, r0 * 0.92, r0 * 0.86, r0 * 0.78)
    hf = K.hand_frame(b)

    def f(p: np.ndarray) -> np.ndarray:
        q = p if side > 0 else _flip(p)
        d = None
        for i in range(3):
            seg = S.capsule(q, chain[i], chain[i + 1], radii[i], radii[i + 1])
            d = seg if d is None else S.smin(d, seg, 0.003)
        for i in range(1, 3):
            # Knuckle bumps sit on the back of the finger.
            kn = S.sphere(q, chain[i] - hf.palm_normal * radii[i] * 0.12, radii[i] * 1.06)
            d = S.smin(d, kn, 0.003)
        return d

    return f
