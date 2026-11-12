"""The head: an SDF sculpt sampled on a feature-following spherical grid.

Coordinates here are head-local centimetres for the body_a head: X toward the
character's left, F forward, U up, origin at the head centre (eye height,
inside the skull; `skeleton.head_centre`). body_b uses the same sculpt scaled
by `Body.head_scale`.

The grid has `HEAD_COLS` columns (shared with the neck and torso) at the
azimuths in `COLS_DEG` and one row per entry of `ROWS`. A row is a curve of
polar angle phi (from straight up) over the azimuth theta; feature rows bend
to follow the eyelids, brows and lips so that those features are crisp edges
of the mesh instead of blurry vertex paint. Shape keys (face sliders and
expressions) change the sculpt parameters and the row curves, never the
topology.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from . import sdf as S

# Eye centre and eyeball radius (cm), left eye. Radius includes the spec's 110%.
EYE = {"x": 3.3, "fwd": 7.68, "z": 0.0, "r": 1.42, "lid": 0.24}
IRIS_R = 0.72
PUPIL_R = 0.29

# Left-half column azimuths in degrees (0 = front, 90 = left, 180 = back).
HALF_COLS_DEG = (0.0, 3.6, 7.2, 10.6, 13.8, 17.5, 21.2, 25.0, 28.8, 34.0, 41.0, 50.0, 61.0, 74.0,
                 88.0, 103.0, 118.0, 133.0, 149.0, 164.5, 180.0)
HEAD_COLS = 2 * (len(HALF_COLS_DEG) - 1)
COLS_DEG = np.array(list(HALF_COLS_DEG) + [360.0 - a for a in reversed(HALF_COLS_DEG[1:-1])])
EYE_COLS = (4, 8)  # inner and outer canthus column (left side)
MOUTH_CORNER_COL = 4  # lips are painted on the faces inside this column
MOUTH_CORNER_DEG = 15.0
BROW_COLS = (3, 10)


@dataclass(frozen=True)
class FaceParams:
    """Sculpt parameters. Sliders and expressions are offsets from these."""

    jaw_w: float = 5.3  # half width at the jaw angle
    chin_w: float = 2.4
    cheek_dz: float = 0.0
    cheek_prom: float = 1.0
    nose: float = 1.0  # overall nose size
    nose_w: float = 1.0
    brow_dz: float = 0.0
    eye_open: float = 1.0  # aperture height multiplier
    eye_tilt: float = 0.12  # outer corner lift (cm)
    # Expressions.
    blink_l: float = 0.0
    blink_r: float = 0.0
    smile: float = 0.0
    frown: float = 0.0
    mouth_open: float = 0.0
    brows_up: float = 0.0
    brows_down: float = 0.0
    # body_b softer jaw, fuller lips.
    softness: float = 0.0


FACE_KEYS = {
    "face_jaw_narrow": dict(jaw_w=-0.75, chin_w=-0.35),
    "face_jaw_wide": dict(jaw_w=+0.85, chin_w=+0.45),
    "face_cheek_soft": dict(cheek_dz=-0.45, cheek_prom=-0.45),
    "face_cheek_high": dict(cheek_dz=+0.55, cheek_prom=+0.45),
    "face_nose_small": dict(nose=-0.2, nose_w=-0.12),
    "face_nose_large": dict(nose=+0.22, nose_w=+0.18),
    "face_brow_low": dict(brow_dz=-0.45),
    "face_brow_high": dict(brow_dz=+0.55),
    "face_eyes_narrow": dict(eye_open=-0.3, eye_tilt=+0.12),
    "face_eyes_round": dict(eye_open=+0.28, eye_tilt=-0.1),
}
EXPRESSION_KEYS = {
    "blink_l": dict(blink_l=1.0),
    "blink_r": dict(blink_r=1.0),
    "smile": dict(smile=1.0),
    "frown": dict(frown=1.0),
    "mouth_open": dict(mouth_open=1.0),
    "brows_up": dict(brows_up=1.0),
    "brows_down": dict(brows_down=1.0),
}


def apply_key(p: FaceParams, deltas: dict, weight: float = 1.0) -> FaceParams:
    return replace(p, **{k: getattr(p, k) + v * weight for k, v in deltas.items()})


# ----------------------------------------------------------------------------
# The sculpt
# ----------------------------------------------------------------------------


def _mirror_x(p: np.ndarray) -> np.ndarray:
    q = p.copy()
    q[:, 0] = np.abs(q[:, 0])
    return q


def head_sdf(fp: FaceParams):
    """SDF of the head sculpt (cm). Points are (X, F, U)."""
    jaw_drop = 1.15 * fp.mouth_open
    eye = EYE

    def f(p: np.ndarray) -> np.ndarray:
        m = _mirror_x(p)  # symmetric parts are written for the left side
        cranium = S.ellipsoid(p, (0.0, -0.6, 2.0), (7.6, 9.9, 10.05))
        # Flatten the cranium sides slightly (temples).
        temple = S.plane(m, (7.3, 0, 0), (1, 0, 0))
        cranium = S.smax(cranium, temple, 1.6)
        maxilla = S.ellipsoid(p, (0.0, 3.7, -3.5), (6.3, 6.0, 4.8))
        # Mandible: jaw angle to chin bone line, plus the filled interior. The
        # lower jaw swings down with mouth_open.
        jd = jaw_drop
        gonion = (fp.jaw_w, 0.3, -7.2 - 0.25 * jd)
        chin_pt = (fp.chin_w * 0.6, 6.7 - 0.15 * jd, -10.0 - jd)
        jaw_line = S.capsule(m, gonion, chin_pt, 1.5, 1.45)
        ramus = S.capsule(m, gonion, (6.0, -0.6, -2.4), 1.35, 1.6)
        jaw_fill = S.ellipsoid(p, (0.0, 3.2, -7.0 - 0.6 * jd), (fp.jaw_w * 0.96, 5.2, 3.8))
        chin = S.ellipsoid(p, (0.0, 7.15 - 0.1 * jd, -9.65 - jd), (fp.chin_w, 1.8, 1.85))
        cheek = S.ellipsoid(m, (4.45, 6.2, -1.9 + fp.cheek_dz + 0.35 * fp.smile),
                            (2.0 * fp.cheek_prom ** 0.3, 1.6 + 0.35 * fp.cheek_prom, 1.3))
        zyg = S.capsule(m, (4.7, 5.6, -1.6 + fp.cheek_dz), (6.3, 1.6, -1.9), 0.8 * fp.cheek_prom ** 0.3, 0.65)
        brow_u = 1.95 + fp.brow_dz + 0.55 * fp.brows_up - 0.4 * fp.brows_down
        brow = S.capsule(m, (0.0, 8.75, brow_u - 0.1), (4.6, 7.65, brow_u + 0.35), 1.05, 0.95)
        d = S.union([cranium, maxilla], 2.4)
        d = S.smin(d, jaw_fill, 2.2)
        d = S.smin(d, jaw_line, 1.4)
        d = S.smin(d, ramus, 1.4)
        d = S.smin(d, chin, 1.2)
        d = S.smin(d, cheek, 1.5)
        d = S.smin(d, zyg, 1.1)
        d = S.smin(d, brow, 1.1)
        # Soft cheek fat over the jaw so the face is not gaunt.
        cheek_fat = S.ellipsoid(m, (4.05, 5.0, -4.7 + 0.3 * fp.smile), (2.75, 2.6, 2.9))
        d = S.smin(d, cheek_fat, 1.6)
        # Neck (blends under the jaw; the torso continues it).
        neck = S.capsule(p, (0.0, -1.2, -4.0), (0.0, -0.4, -22.0), 5.75, 5.9)
        d = S.smin(d, neck, 1.6)
        # Eye sockets, then the lid shell around the eyeball.
        ec = (eye["x"], eye["fwd"], eye["z"])
        socket = S.ellipsoid(m, (eye["x"] + 0.2, eye["fwd"] + 1.35, 0.15), (1.95, 1.7, 1.5))
        d = S.smax(d, -socket, 0.55)
        lids = S.sphere(m, ec, eye["r"] + eye["lid"])
        d = S.smin(d, lids, 0.45)
        # Nose.
        n = fp.nose
        nw = fp.nose_w
        nasion = np.array([0.0, 8.6, 0.6])
        tip = nasion + n * np.array([0.0, 2.2, -3.45])
        bridge = S.capsule(p, nasion, tip, 0.62 * nw, 0.9 * nw)
        tipb = S.ellipsoid(p, tip + n * np.array([0.0, -0.15, -0.2]), (1.0 * nw, 0.95 * n, 0.95 * n))
        alae = S.ellipsoid(m, (1.42 * nw, 8.95 + 0.4 * (n - 1), -3.55 - 0.3 * (n - 1)), (0.82 * nw, 0.95 * n, 0.72 * n))
        base = S.ellipsoid(p, (0.0, 9.0 + 0.6 * (n - 1), -3.95 - 0.3 * (n - 1)), (1.25 * nw, 1.2 * n, 0.55))
        nose = S.union([bridge, tipb], 0.5)
        nose = S.smin(nose, alae, 0.45)
        nose = S.smin(nose, base, 0.4)
        d = S.smin(d, nose, 0.5)
        nostril = S.ellipsoid(m, (0.68 * nw, 9.6 + 0.9 * (n - 1), -4.42 - 0.35 * (n - 1)), (0.32 * nw, 0.5, 0.28))
        d = S.smax(d, -nostril, 0.18)
        # Mouth: muzzle, then the lips. Corners rise with smile, drop with frown.
        corner_u = 0.42 * fp.smile - 0.35 * fp.frown
        muzzle = S.ellipsoid(p, (0.0, 6.55, -6.35 - 0.4 * jaw_drop), (3.4, 3.0, 2.8 + 0.3 * jaw_drop))
        d = S.smin(d, muzzle, 1.3)
        lip_full = 1.0 + 0.12 * fp.softness
        upper = S.ellipsoid(p, (0.0, 9.0, -5.85 + 0.2 * corner_u), (2.1 + 0.15 * fp.smile, 0.8 * lip_full, 0.72 * lip_full))
        lower = S.ellipsoid(p, (0.0, 8.78 - 0.15 * jaw_drop, -7.2 - jaw_drop + 0.15 * corner_u),
                            (1.9 + 0.12 * fp.smile, 0.86 * lip_full, 0.76 * lip_full))
        d = S.smin(d, upper, 0.55)
        d = S.smin(d, lower, 0.55)
        # Mouth line groove (deepens as the mouth opens).
        mouth_u = -6.5 - 0.5 * jaw_drop + 0.25 * corner_u
        groove = S.capsule(p, (-2.1, 9.9, mouth_u), (2.1, 9.9, mouth_u), 0.16 + 0.5 * fp.mouth_open)
        d = S.smax(d, -groove, 0.14)
        # Mentolabial sulcus.
        sulcus = S.capsule(p, (-1.6, 9.35, -8.45 - jaw_drop), (1.6, 9.35, -8.45 - jaw_drop), 0.42)
        d = S.smax(d, -sulcus, 0.5)
        return d

    return f


def eyeball_hit(dirs: np.ndarray, left: np.ndarray) -> np.ndarray:
    """Distance along unit rays from the origin to the near side of the eyeball."""
    c = np.array([EYE["x"], EYE["fwd"], EYE["z"]])
    c = np.where(left[:, None], c, c * np.array([-1.0, 1.0, 1.0]))
    b = np.sum(dirs * c, axis=1)
    cc = np.sum(c * c, axis=1) - EYE["r"] ** 2
    disc = np.maximum(b * b - cc, 0.0)
    return b - np.sqrt(disc)


# ----------------------------------------------------------------------------
# The grid
# ----------------------------------------------------------------------------

# (name, front phi, back phi). Row curves are built in row_phis().
ROWS = [
    ("r1", 9.0, 9.0),
    ("r2", 18.0, 18.0),
    ("r3", 27.0, 27.0),
    ("r4", 36.0, 36.0),
    ("r5", 44.0, 44.0),
    ("r6", 51.0, 51.0),
    ("r7", 58.0, 58.0),
    ("r8", 64.5, 64.5),
    ("r9", 70.0, 70.0),
    ("brow_top", 74.5, 74.0),
    ("brow_bottom", 78.5, 78.0),
    ("crease", 82.0, 82.0),
    ("lash", 85.0, 85.5),
    ("lid_upper", 87.0, 87.5),
    ("band_1", 88.6, 89.0),
    ("band_2", 90.2, 90.5),
    ("lid_lower", 91.8, 92.0),
    ("under_eye", 95.5, 95.5),
    ("r18", 100.5, 100.0),
    ("nose_tip", 105.0, 104.0),
    ("r20", 109.5, 108.0),
    ("subnasale", 113.5, 112.0),
    ("lip_top", 118.5, 115.0),
    ("mouth_upper", 123.9, 118.0),
    ("mouth_lower", 124.6, 121.0),
    ("lip_bottom", 129.5, 124.0),
    ("sulcus", 134.5, 127.0),
    ("chin", 139.5, 130.0),
    ("chin_bottom", 145.0, 133.0),
    ("under_chin", 151.0, 136.0),
    ("under_jaw", 157.0, 138.5),
    ("throat", 163.0, 141.0),
]
ROW_INDEX = {name: i + 1 for i, (name, _, _) in enumerate(ROWS)}  # row 0 is the crown pole
HEAD_ROWS = len(ROWS) + 1


def _eye_param(theta_deg: np.ndarray) -> np.ndarray:
    """0 at the inner canthus column, 1 at the outer one (left side azimuths)."""
    a0 = HALF_COLS_DEG[EYE_COLS[0]]
    a1 = HALF_COLS_DEG[EYE_COLS[1]]
    return (theta_deg - a0) / (a1 - a0)


def _phi_for_height(u_cm: np.ndarray, dist: np.ndarray) -> np.ndarray:
    return 90.0 - np.degrees(np.arctan2(u_cm, dist))


def row_phis(fp: FaceParams) -> np.ndarray:
    """(HEAD_ROWS, HEAD_COLS) polar angles in degrees; row 0 is the pole."""
    theta = COLS_DEG
    side = np.where(theta <= 180.0, theta, 360.0 - theta)  # mirrored azimuth 0..180
    is_left = theta <= 180.0
    back_w = S.smoothstep(20.0, 165.0, side)
    out = np.zeros((HEAD_ROWS, HEAD_COLS))
    for i, (name, front, back) in enumerate(ROWS, start=1):
        out[i] = front + (back - front) * back_w
    R = ROW_INDEX

    # --- Eyes ---------------------------------------------------------------
    u = _eye_param(side)
    uc = np.clip(u, 0.0, 1.0)
    dist = 9.0 - 1.4 * (uc - 0.45) ** 2  # the eye surface curves back to the canthi
    tilt = fp.eye_tilt * uc
    open_k = max(fp.eye_open, 0.2)
    up_h = 0.74 * open_k * np.sin(np.pi * uc ** 0.88) ** 0.75
    lo_h = 0.55 * open_k * np.sin(np.pi * uc ** 1.08) ** 0.9
    lift = 0.06 * fp.smile
    upper_u = tilt + up_h
    lower_u = tilt - lo_h + lift
    blink = np.where(is_left, fp.blink_l, fp.blink_r)
    closed_u = lower_u + 0.12 * (upper_u - lower_u)
    upper_u = upper_u + (closed_u - upper_u) * blink
    e1 = _phi_for_height(upper_u, dist)
    e2 = _phi_for_height(lower_u, dist)
    # Keep a little separation at the canthi so faces never collapse.
    mid = 0.5 * (e1 + e2)
    half = np.maximum(0.5 * (e2 - e1), 0.45)
    e1 = mid - half
    e2 = mid + half
    rest = {k: out[R[k]].copy() for k in ("lash", "lid_upper", "band_1", "band_2", "lid_lower", "crease")}
    # Blend from the rest rows outside the eye to the lid curves inside.
    w = np.clip(1.0 - np.maximum(-u, u - 1.0) / 0.55, 0.0, 1.0)
    w = S.smoothstep(0.0, 1.0, w)
    eye_rows = {
        "lid_upper": e1,
        "band_1": e1 + (e2 - e1) / 3.0,
        "band_2": e1 + 2.0 * (e2 - e1) / 3.0,
        "lid_lower": e2,
        "lash": e1 - 0.9 - 0.3 * np.sin(np.pi * uc),
        "crease": e1 - 3.6 - 1.2 * np.sin(np.pi * uc) + 2.4 * blink,
    }
    for k, v in eye_rows.items():
        out[R[k]] = rest[k] + (v - rest[k]) * w

    # --- Brows --------------------------------------------------------------
    b0, b1 = HALF_COLS_DEG[BROW_COLS[0]], HALF_COLS_DEG[BROW_COLS[1]]
    ub = np.clip((side - b0) / (b1 - b0), 0.0, 1.0)
    in_brow = S.smoothstep(-0.25, 0.05, (side - b0) / (b1 - b0)) * (1.0 - S.smoothstep(0.95, 1.3, (side - b0) / (b1 - b0)))
    dz = fp.brow_dz + 0.5 * fp.brows_up - 0.35 * fp.brows_down
    # Brow arc: higher at 60%, thick inside, thin tail; frown pulls the inner end down.
    arc = 2.35 + 0.55 * np.sin(np.pi * np.clip(ub * 0.85 + 0.05, 0, 1)) - 0.25 * ub
    inner_drop = (0.45 * fp.brows_down + 0.35 * fp.frown) * (1.0 - ub) ** 2
    thick = 0.62 - 0.3 * ub
    top_u = arc + dz - inner_drop + 0.5 * thick
    bot_u = arc + dz - inner_drop - 0.5 * thick
    top = _phi_for_height(top_u, 9.2 - 1.2 * ub)
    bot = _phi_for_height(bot_u, 9.2 - 1.2 * ub)
    out[R["brow_top"]] += (top - out[R["brow_top"]]) * in_brow
    out[R["brow_bottom"]] += (bot - out[R["brow_bottom"]]) * in_brow
    # Keep the crease row below the brow.
    out[R["crease"]] = np.maximum(out[R["crease"]], out[R["brow_bottom"]] + 1.2)
    out[R["lash"]] = np.maximum(out[R["lash"]], out[R["crease"]] + 0.8)

    # --- Mouth ----------------------------------------------------------------
    corner = MOUTH_CORNER_DEG
    um = np.clip(side / corner, 0.0, 1.6)
    in_mouth = 1.0 - S.smoothstep(0.85, 1.5, um)
    corner_shift = (-3.0 * fp.smile + 2.6 * fp.frown) * S.smoothstep(0.35, 1.0, np.minimum(um, 1.0))
    open_gap = 7.5 * fp.mouth_open * (1.0 - np.minimum(um, 1.0) ** 2)
    bow = 0.6 * np.exp(-((um - 0.28) / 0.17) ** 2)  # cupid's bow peaks
    top_lip = 119.6 - bow + 3.6 * um ** 2.2
    m1 = 123.9 + 1.0 * um ** 2
    m2 = 124.6 + 0.6 * um ** 2 + open_gap
    bottom_lip = 129.0 - 2.8 * um ** 2 + 1.1 * open_gap
    for k, v in (("lip_top", top_lip), ("mouth_upper", m1), ("mouth_lower", m2), ("lip_bottom", bottom_lip)):
        base = out[R[k]]
        out[R[k]] = base + (v + corner_shift - base) * in_mouth
    # The jaw (and everything below the lower lip) drops when the mouth opens.
    drop = fp.mouth_open * (1.0 - back_w)
    for k, amount in (("sulcus", 6.0), ("chin", 5.0), ("chin_bottom", 3.5), ("under_chin", 2.0), ("under_jaw", 1.0)):
        out[R[k]] += amount * drop
    # Monotonic rows (never fold).
    for i in range(2, HEAD_ROWS):
        out[i] = np.maximum(out[i], out[i - 1] + 0.35)
    return out


def ray_dirs(phi_deg: np.ndarray, theta_deg: np.ndarray) -> np.ndarray:
    phi = np.radians(phi_deg)
    th = np.radians(theta_deg)
    return np.stack([np.sin(phi) * np.sin(th), np.sin(phi) * np.cos(th), np.cos(phi)], axis=-1)


@dataclass
class HeadSample:
    points: np.ndarray  # (HEAD_ROWS * HEAD_COLS - HEAD_COLS + 1, 3) local cm; index 0 is the pole
    phis: np.ndarray  # (HEAD_ROWS, HEAD_COLS)


def is_eye_band(row: int, col: int) -> bool:
    """Vertices strictly inside the eye aperture lie on the eyeball."""
    side = HALF_COLS_DEG[col] if col <= HEAD_COLS // 2 else HALF_COLS_DEG[HEAD_COLS - col]
    a0, a1 = HALF_COLS_DEG[EYE_COLS[0]], HALF_COLS_DEG[EYE_COLS[1]]
    return row in (ROW_INDEX["band_1"], ROW_INDEX["band_2"]) and a0 < side < a1


def sample(fp: FaceParams) -> HeadSample:
    phis = row_phis(fp)
    theta = np.broadcast_to(COLS_DEG, phis.shape)
    dirs = ray_dirs(phis, theta)
    flat = dirs[1:].reshape(-1, 3)
    t = S.raycast(head_sdf(fp), np.zeros_like(flat), flat, t_max=30.0, step_scale=0.7, min_step=0.004)
    pts = flat * t[:, None]
    # Eye band vertices sit on the eyeball (just behind the lid shell).
    rows = np.repeat(np.arange(1, HEAD_ROWS), HEAD_COLS)
    cols = np.tile(np.arange(HEAD_COLS), HEAD_ROWS - 1)
    band = np.array([is_eye_band(r, c) for r, c in zip(rows, cols)])
    if band.any():
        left = COLS_DEG[cols[band]] <= 180.0
        te = eyeball_hit(flat[band], left)
        pts[band] = flat[band] * te[:, None]
    top = S.raycast(head_sdf(fp), np.zeros((1, 3)), np.array([[0.0, 0.0, 1.0]]), t_max=30.0)
    pole = np.array([[0.0, 0.0, top[0]]])
    return HeadSample(np.vstack([pole, pts]), phis)


def to_world(local_cm: np.ndarray, centre: np.ndarray, scale: float) -> np.ndarray:
    """Head-local cm (X, F, U) to Blender metres."""
    p = local_cm * (0.01 * scale)
    return np.stack([centre[0] + p[:, 0], centre[1] - p[:, 1], centre[2] + p[:, 2]], axis=1)


def eye_uv(local_cm: np.ndarray, left: bool) -> np.ndarray:
    """Eye-texture coordinates (-1..1 over the iris box) of eyeball points."""
    c = np.array([EYE["x"] if left else -EYE["x"], EYE["fwd"], EYE["z"]])
    d = local_cm - c
    # Project onto the plane facing forward; the iris looks straight ahead.
    return np.stack([d[:, 0] / (IRIS_R * 1.6), d[:, 2] / (IRIS_R * 1.6)], axis=1)


def hairline_phi(theta_deg: float) -> float:
    """Polar angle of the scalp boundary (scalp above, face below)."""
    side = theta_deg if theta_deg <= 180 else 360 - theta_deg
    # The hairline curves above the ears (so short hair clears them) and down
    # behind them to the nape.
    pts = [(0, 50.0), (30, 52.0), (55, 60.0), (78, 70.0), (95, 72.0), (115, 76.0), (135, 112.0), (180, 128.0)]
    for (a0, p0), (a1, p1) in zip(pts, pts[1:]):
        if side <= a1:
            t = (side - a0) / (a1 - a0)
            return p0 + (p1 - p0) * t
    return pts[-1][1]




def face_side(col: int) -> int:
    """Mirrored index of the face between columns col and col + 1 (0 = the
    face just left of the front midline, on either side)."""
    return col if col < HEAD_COLS // 2 else HEAD_COLS - 1 - col


def face_paint(row: int, col: int) -> tuple[str, tuple[float, float, float], bool]:
    """Swatch, body dye mask and eyeball flag of the head face below `row`
    between columns col and col + 1. Masks: r = skin tone, g = hair colour."""
    R = ROW_INDEX
    s = face_side(col)
    skin = (1.0, 0.0, 0.0)
    eye0, eye1 = EYE_COLS
    if row == R["brow_top"] and BROW_COLS[0] <= s < BROW_COLS[1]:
        return "brow", (0.0, 1.0, 0.0), False
    if R["lid_upper"] <= row < R["lid_lower"] and eye0 <= s < eye1:
        return "eye", (0.0, 0.0, 0.0), True
    if row == R["lash"] and eye0 <= s < eye1:
        return "lash", (0.0, 0.0, 0.0), False
    if row == R["crease"] and eye0 <= s < eye1:
        return "skin_shade", skin, False
    if s < MOUTH_CORNER_COL:
        if row == R["lip_top"]:
            return "lips", skin, False
        if row == R["mouth_upper"]:
            return "mouth", (0.0, 0.0, 0.0), False
        if row == R["mouth_lower"]:
            return "lips_lower", skin, False
    return "skin", skin, False
