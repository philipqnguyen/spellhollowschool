"""Base bodies from MakeHuman (MPFB2), fitted to our skeleton.

    build_characters.py -- --body-source makehuman ...

The MakeHuman hm08 mesh and its targets, rig definition, weights and skin
masks (all CC0, see art/CREDITS-makehuman.md) supply the anatomy; MPFB (GPL)
only runs as a tool to evaluate the macro sliders. Steps:

1. **Generate** (`mh_source`): one MakeHuman human per body and build with
   the macro sliders in `MH_MACROS` (body_a fully male, body_b fully female,
   age 18: young adults), plus the anime style detail targets in `MH_STYLE`
   (small nose, thin lips, larger open eyes without bags, cheekbones, jaw,
   hips), detail targets for the face sliders and expression units for the
   expressions.
2. **Fit to our skeleton** (`Fit`): MakeHuman's Rigify meta-rig is located on
   the mesh (joint cubes and landmark vertices from the CC0 rig file); the
   mesh is posed onto our bones (`skeleton.joints`) with dual-quaternion
   skinning using MakeHuman's CC0 weights, so the A-pose, arm angle, palm
   orientation, finger curl and leg stance become ours without losing volume,
   then stretched along each bone to our lengths. The head scales uniformly
   about the eyes. Joints never move between builds.
3. **Stylise** (`Styliser`): the scalp is pulled radially onto our head
   sculpt (hair and stiff hats are cut against it), the eyes and lids are
   enlarged about each eye centre (`EYE_SCALE`), the face is smoothed into
   simpler planes and the rest of the skin (not hands and feet) into smooth,
   clothing-friendly forms (Taubin passes, volume-preserving). Hands get the
   spec's 105% from the skeleton. The anime eye, lash line and pale lips are
   painted (atlas.py, `feature_flags`).
   With `HEAD = "anime"` (the default) MakeHuman's head is then replaced by
   our dedicated anime head (`mh_head.py`), welded on at a neck edge loop, and
   the face paint, keys and normals are made on it instead.
4. **Visible body** (`visible_meshdata`): MakeHuman's own topology (skin and
   eyeballs) with our regions, paint, dye mask and atlas UVs (lips, mouth,
   nails and ears from MakeHuman's CC0 skin masks, eyes mapped into the
   painted atlas eye, everything else transferred from the cage), skin weights
   from MakeHuman's weights renamed onto our DEF bones, and every shape key
   (builds from macro sliders, face sliders from detail targets, expressions
   from expression units). `pipeline.make_lods` decimates it to the LOD0
   budget (12,000 triangles) and LOD1 like any other asset.
5. **Fitting cage** (`cage`): our body layout (`body.BodyLayout`, the
   ring/row topology every clothing item and hair style is generated from) is
   cast along its normals onto the stylised MakeHuman surface and bound there,
   so gear and hair hug the new body with no change to their builders. The
   scalp, hands and toes of the cage stay our sculpt (stiff hats and shoes are
   sized on it; those regions are hidden under the items that cover them).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

from . import atlas as A
from . import head as H
from . import mh_face as MF
from . import mh_paint as MP
from . import mh_source as MS
from . import sdf as S
from . import skeleton as K
from . import weights as WT
from .body import TCOLS, BodyLayout
from .meshutil import MeshData
from .proportions import BUILDS, Body

# ----------------------------------------------------------------------------
# Parameters
# ----------------------------------------------------------------------------

def age_macro(years: float) -> float:
    """MakeHuman's age slider for an age in years (its own mapping: 0 = 1 y,
    0.1875 = 11 y, 0.5 = 25 y, 1 = 90 y, linear in between)."""
    if years <= 11:
        return (years - 1) / 10 * 0.1875
    if years <= 25:
        return 0.1875 + (years - 11) / 14 * 0.3125
    return 0.5 + (years - 25) / 65 * 0.5


# Characters are final-year students: 18-year-old young adults. MakeHuman's
# 18 y (age 0.34375) blends its "child" and "young" macro targets half and
# half; the fit then imposes the spec's adult skeleton, height and 7.5-7.6
# head proportions, so the result is a youthful but clearly adult body.
# Weight and muscle sit a little under average for a lean, less mature build.
AGE_YEARS = 18.0
MH_MACROS = {
    "body_a": dict(gender=1.0, age=age_macro(AGE_YEARS), muscle=0.45, weight=0.45, proportions=0.75, height=0.5),
    "body_b": dict(gender=0.0, age=age_macro(AGE_YEARS), muscle=0.45, weight=0.45, proportions=0.75, height=0.5,
                   cupsize=0.42, firmness=0.6),
}
# Build keys as macro offsets (applied to the body's macros, clamped to 0..1).
MH_BUILDS = {
    "build_slim": dict(weight=-0.42, muscle=-0.12),
    "build_muscular": dict(muscle=+0.5, weight=+0.05),
    "build_heavy": dict(weight=+0.5),
}
# Face sliders: MakeHuman detail targets and weights.
MH_FACE_TARGETS = {
    "face_jaw_narrow": {"chin/chin-width-decr": 1.0, "chin/chin-bones-decr": 0.7, "head/head-scale-horiz-decr": 0.15},
    "face_jaw_wide": {"chin/chin-width-incr": 1.0, "chin/chin-bones-incr": 0.7, "head/head-scale-horiz-incr": 0.15},
    "face_cheek_soft": {"cheek/l-cheek-bones-decr": 0.8, "cheek/r-cheek-bones-decr": 0.8,
                        "cheek/l-cheek-volume-incr": 0.6, "cheek/r-cheek-volume-incr": 0.6},
    "face_cheek_high": {"cheek/l-cheek-trans-up": 0.8, "cheek/r-cheek-trans-up": 0.8,
                        "cheek/l-cheek-bones-incr": 0.8, "cheek/r-cheek-bones-incr": 0.8},
    "face_nose_small": {"nose/nose-scale-horiz-decr": 0.6, "nose/nose-scale-vert-decr": 0.5,
                        "nose/nose-scale-depth-decr": 0.6},
    "face_nose_large": {"nose/nose-scale-horiz-incr": 0.6, "nose/nose-scale-vert-incr": 0.5,
                        "nose/nose-scale-depth-incr": 0.6},
    "face_brow_low": {"eyebrows/eyebrows-trans-down": 1.0},
    "face_brow_high": {"eyebrows/eyebrows-trans-up": 1.0},
    "face_eyes_narrow": {"eyes/l-eye-height2-decr": 0.8, "eyes/r-eye-height2-decr": 0.8,
                         "eyes/l-eye-corner2-up": 0.4, "eyes/r-eye-corner2-up": 0.4},
    "face_eyes_round": {"eyes/l-eye-height2-incr": 0.8, "eyes/r-eye-height2-incr": 0.8},
}
_UNITS = ("african", "asian", "caucasian")  # expression units are averaged over the three sets
MH_EXPRESSION_TARGETS = {
    "blink_l": {"eye-left-closure": 1.0},
    "blink_r": {"eye-right-closure": 1.0},
    "smile": {"mouth-corner-puller": 0.9, "mouth-elevation": 0.15},
    "frown": {"mouth-depression": 0.9},
    "mouth_open": {"mouth-open": 0.8},
    "brows_up": {"eyebrows-left-up": 1.0, "eyebrows-right-up": 1.0},
    "brows_down": {"eyebrows-left-down": 1.0, "eyebrows-right-down": 1.0},
}
# Radial (girth) scale per fit bone after the length fit (1 = MakeHuman's).
GIRTH = {"body_a": {"neck": 0.88}, "body_b": {"neck": 0.86}}  # anime: a slimmer neck under the smaller head
# Eyes: larger than realistic but not childlike (style guide: anime look;
# the spec's 110% was for the earlier painterly direction). Scale about each
# eye centre (width, depth, height), easing out between r0 and r1.
EYE_SCALE = (1.22, 1.12, 1.32)
EYE_R = (0.019, 0.034)
EAR_SCALE = 0.82  # anime ears: smaller, scaled toward their root ...
EAR_IN = 0.003  # ... and tucked toward the head (hair over the ears clears them)
LASH_BAND = 1.22  # upper-lid skin within this many eyeball radii is painted as the lash line
HEAD_HEIGHT = 0.235  # chin to crown for body_a (x head_scale: 0.225 for body_b), spec section 1
FACE_SMOOTH = 12  # Taubin passes over the face ("simplified planes")
BODY_SMOOTH = 4  # Taubin passes over the rest of the skin (smooth, clothing-friendly forms; hands and feet excluded)

# Anime stylisation (docs/art/style-guide.md): MakeHuman detail targets
# applied to every variant of the body before the fit, so builds and face
# keys inherit them. Small simplified nose, softer, thinner lips, larger eyes
# without bags, defined cheekbones and jaw (young adults, never childlike).
def _lr(name: str, w: float) -> dict[str, float]:
    group, rest = name.split("/")
    return {f"{group}/l-{rest}": w, f"{group}/r-{rest}": w}


_STYLE_COMMON = {
    "nose/nose-scale-horiz-decr": 0.6, "nose/nose-scale-depth-decr": 0.7, "nose/nose-volume-decr": 0.7,
    "nose/nose-flaring-decr": 0.6, "nose/nose-nostrils-width-decr": 0.6, "nose/nose-point-width-decr": 0.6,
    "nose/nose-hump-decr": 0.6,
    "mouth/mouth-upperlip-volume-decr": 0.9, "mouth/mouth-lowerlip-volume-decr": 0.9,
    "mouth/mouth-scale-depth-decr": 0.5, "mouth/mouth-scale-horiz-decr": 0.25, "mouth/mouth-laugh-lines-in": 0.5,
    "neck/neck-double-decr": 1.0,
    **_lr("eyes/eye-bag-decr", 1.0), **_lr("eyes/eye-scale-incr", 0.6), **_lr("eyes/eye-height1-incr", 0.5),
    **_lr("eyes/eye-height2-incr", 0.55), **_lr("eyes/eye-height3-incr", 0.5),
    **_lr("cheek/cheek-bones-incr", 0.35),
}
MH_STYLE = {
    "body_a": {**_STYLE_COMMON, "chin/chin-bones-incr": 0.4, "chin/chin-prominent-incr": 0.3,
               "head/head-square": 0.2, "hip/hip-scale-horiz-incr": 0.8},
    "body_b": {**_STYLE_COMMON, "nose/nose-scale-vert-decr": 0.3, "nose/nose-point-up": 0.2,
               "chin/chin-prominent-incr": 0.2, "chin/chin-width-decr": 0.15, "head/head-oval": 0.3,
               "hip/hip-scale-horiz-incr": 1.0, "torso/measure-hips-circ-incr": 0.4},
}
# The head: "anime" = our dedicated scripted anime head welded on at a neck
# loop (mh_head.py); "makehuman" = MakeHuman's own head with the mh_face
# offsets (the earlier attempt, kept for comparison). Env override for A/B
# renders only.
HEAD = __import__("os").environ.get("MH_HEAD", "anime")
ANIME_POST_KEYS = ("smile", "frown", "brows_up", "brows_down", "face_brow_low", "face_brow_high")
LOD0_BUDGET = int(__import__("os").environ.get("MH_LOD0_BUDGET", "15500"))  # character adoption: LOD0 tier <= 16,000 (env override: debugging only)
PAINT_KEEP = 0.97
MF_EYE_KEYS = ("blink_l", "blink_r", "face_eyes_narrow", "face_eyes_round", "mouth_open")
# Where the LOD0 decimation (28k -> 15.5k triangles) keeps detail, 0..1 per
# region: the face most, the scalp (under hair) least; the feet as the hips
# (at 0.15 the toes were cut into shards).
# The face's paint outlines are kept exactly anyway, so the rest of the head
# (dense in MakeHuman) gives way first; hands keep their fingers.
REGION_KEEP = {"head": 0.45, "ears": 0.2, "scalp": 0.0, "neck": 0.3, "torso": 0.35, "hips": 0.3, "upper_arms": 0.35,
               "forearms": 0.35, "hands": 0.6, "thighs": 0.35, "calves": 0.35, "feet": 0.3}
SCALP_ALLOW = 0.001  # the scalp may stand this far outside our head sculpt (hair borders sit 1.2 mm above it)

# ----------------------------------------------------------------------------
# The fit skeleton: semantically matching chains on both rigs
# ----------------------------------------------------------------------------
#
# MakeHuman's Rigify bones do not mean the same as ours everywhere (its
# `spine` starts at the crotch, its neck is one bone, its `hand` ends at the
# middle knuckle and `palm.01` starts at the thumb), so the fit uses its own
# chains between landmarks both rigs agree on: hip joints, neck base, skull
# base, crown, clavicle, shoulder, elbow, wrist, knuckles, finger joints,
# knee, ankle, ball and toe tip.

FACE_PREFIXES = ("brow", "lid", "lip", "cheek", "nose", "chin", "ear", "forehead", "temple", "jaw", "tongue",
                 "teeth", "eye", "DEF-eye", "face")
FINGERS = ("f_index", "f_middle", "f_ring", "f_pinky")


@dataclass
class FitBone:
    name: str
    parent: str | None
    mh: tuple[np.ndarray, np.ndarray]
    ours: tuple[np.ndarray, np.ndarray]
    ref: str  # 'fwd', 'lat', 'palm' (frame alignment) or 'min' (minimal rotation)
    uniform: bool = False  # scale across the bone like along it (head, hands)


def _weight_target(name: str) -> str | None:
    """Fit bone that a MakeHuman weight group follows."""
    if name.startswith("mhmask"):
        return None
    base = name[4:] if name.startswith("DEF-") else name
    if base.endswith((".L.001", ".R.001")) and base[:-6] in ("upper_arm", "forearm", "thigh", "shin"):
        return base[:-6] + base[-6:-4]  # Rigify twist segment (e.g. DEF-forearm.L.001) follows its limb bone
    side = base[-2:] if base[-2:] in (".L", ".R") else ""
    stem = base[: -2] if side else base
    if stem.startswith(FACE_PREFIXES) or stem == "spine.006":
        return "head"
    if stem == "spine.005":
        return "neck"
    if stem.startswith("spine") or stem in ("pelvis", "breast", "MCH-breast-parent"):
        return "torso"
    if stem.startswith("toe"):
        return "toe" + side
    if stem.startswith("palm"):
        return "hand" + side
    helpers = {"elbow-helper": "forearm", "knee-helper": "shin", "pelvis-helper": "thigh",
               "pelvis-helper.front": "thigh", "shoulder-helper": "upper_arm"}
    if stem in helpers:
        return helpers[stem] + side
    return stem + side


def mh_points(co: np.ndarray, rigdef: dict, groups: dict[str, np.ndarray]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """MakeHuman's Rigify bone heads/tails located on a mesh (CC0 rig file)."""
    def point(spec: dict) -> np.ndarray:
        s = spec["strategy"]
        if s == "CUBE":
            return co[groups[spec["cube_name"]]].mean(axis=0)
        if s == "VERTEX":
            return co[spec["vertex_index"]].copy()
        if s == "MEAN":
            return co[spec["vertex_indices"]].mean(axis=0)
        if s == "XYZ":
            i, j, k = spec["vertex_indices"]
            return np.array([co[i, 0], co[j, 1], co[k, 2]])
        raise ValueError(s)

    return {name: (point(b["head"]), point(b["tail"])) for name, b in rigdef.items() if "head" in b and "tail" in b}


def fit_bones(b: Body, co: np.ndarray, rigdef: dict, groups: dict[str, np.ndarray]) -> list[FitBone]:
    mh = mh_points(co, rigdef, groups)
    J = K.joint_map(b)
    cube = lambda n: co[groups[n]].mean(axis=0)  # noqa: E731
    body = co[: MS.BODY_VERTS]
    top = body[np.argmax(body[:, 2])]
    m_hip = 0.5 * (cube("joint-l-upper-leg") + cube("joint-r-upper-leg"))
    m_hip[0] = 0.0
    m_neck = cube("joint-neck")
    # The head scales uniformly to the spec's head height (chin to crown),
    # crown at the body height, eyes at our eyes' depth; MakeHuman keeps its
    # own facial proportions (its eyes end up slightly above the spec's eye
    # height). The skull base (top of the neck) keeps its place in the head.
    m_eye = 0.5 * (cube("joint-l-eye") + cube("joint-r-eye"))
    m_eye[0] = 0.0
    face = body[(np.abs(body[:, 0]) < 0.006) & (body[:, 2] > m_eye[2] - 0.17) & (body[:, 2] < m_eye[2] - 0.07)
                & (body[:, 1] < m_eye[1] + 0.02)]
    m_chin = np.array([0.0, m_eye[1], face[:, 2].min()])
    m_crown = np.array([0.0, m_eye[1], top[2]])
    o_hip = 0.5 * (J["thigh.L"].head + J["thigh.R"].head)
    o_hip[0] = 0.0
    o_neck = J["spine.004"].head
    el, er = K.eye_centres(b)
    o_eye = 0.5 * (el + er)
    o_crown = np.array([0.0, o_eye[1], b.height])
    o_chin = o_crown - np.array([0.0, 0.0, HEAD_HEIGHT * b.head_scale])
    s = HEAD_HEIGHT * b.head_scale / (m_crown[2] - m_chin[2])
    m_skull = cube("joint-head")
    o_skull = o_chin + (m_skull - m_chin) * s
    out = [
        FitBone("torso", None, (m_hip, m_neck), (o_hip, o_neck), "fwd"),
        FitBone("neck", "torso", (m_neck, m_skull), (o_neck, o_skull), "fwd"),
        FitBone("head", "neck", (m_chin, m_crown), (o_chin, o_crown), "fwd", uniform=True),
    ]
    for sfx in ("L", "R"):
        def m(n, end=0):
            return mh[f"{n}.{sfx}"][end]

        def o(n, end=0):
            j = J[f"{n}.{sfx}"]
            return j.head if end == 0 else j.tail

        mid_m = mh[f"f_middle.01.{sfx}"][0]
        mid_o = J[f"f_middle.01.{sfx}"].head
        out += [
            FitBone(f"shoulder.{sfx}", "torso", (m("shoulder"), m("upper_arm")), (o("shoulder"), o("upper_arm")), "fwd"),
            FitBone(f"upper_arm.{sfx}", f"shoulder.{sfx}", (m("upper_arm"), m("forearm")), (o("upper_arm"), o("forearm")), "min"),
            FitBone(f"forearm.{sfx}", f"upper_arm.{sfx}", (m("forearm"), m("hand")), (o("forearm"), o("hand")), "min"),
            FitBone(f"hand.{sfx}", f"forearm.{sfx}", (m("hand"), mid_m), (o("hand"), mid_o), "palm", uniform=True),
        ]
        for f in FINGERS + ("thumb",):
            for k in (1, 2, 3):
                n = f"{f}.0{k}"
                parent = f"hand.{sfx}" if k == 1 else f"{f}.0{k - 1}.{sfx}"
                out.append(FitBone(f"{n}.{sfx}", parent, (m(n), m(n, 1)), (o(n), o(n, 1)), "palm", uniform=True))
        out += [
            FitBone(f"thigh.{sfx}", "torso", (m("thigh"), m("shin")), (o("thigh"), o("shin")), "fwd"),
            FitBone(f"shin.{sfx}", f"thigh.{sfx}", (m("shin"), m("foot")), (o("shin"), o("foot")), "fwd"),
            FitBone(f"foot.{sfx}", f"shin.{sfx}", (m("foot"), m("toe")), (o("foot"), o("toe")), "lat"),
            FitBone(f"toe.{sfx}", f"foot.{sfx}", (m("toe"), m("toe", 1)), (o("toe"), o("toe", 1)), "lat"),
        ]
    return out


def landmark_vertices(rigdef: dict) -> dict[str, list[int]]:
    """MakeHuman vertex ids of face landmarks (left side), from the rig."""
    def ids(spec):
        return [spec["vertex_index"]] if spec["strategy"] == "VERTEX" else list(spec["vertex_indices"])

    r = rigdef
    return {
        "canthus_in": ids(r["lid.T.L"]["head"]),
        "lid_top": ids(r["lid.T.L.002"]["head"]),
        "canthus_out": ids(r["lid.T.L.003"]["tail"]),
        "lid_bottom": ids(r["lid.B.L.002"]["head"]),
        "mouth_corner": ids(r["lip.T.L.001"]["tail"]),
        "lip_mid": ids(r["lip.T.L"]["head"]),
        "nose_tip": ids(r["nose.002"]["head"]),
        "chin": ids(r["chin"]["head"]),
    }


# ----------------------------------------------------------------------------
# Small math helpers
# ----------------------------------------------------------------------------


def _unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-12)


def _frame(d: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Orthonormal columns (d, e1, e2) with e1 the part of `ref` across d."""
    d = _unit(d)
    e1 = ref - d * float(ref @ d)
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(d, [0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.cross(d, [1.0, 0.0, 0.0])
    e1 = _unit(e1)
    return np.stack([d, e1, np.cross(d, e1)], axis=1)


def _min_rotation(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a, b = _unit(a), _unit(b)
    v = np.cross(a, b)
    c = float(a @ b)
    if c < -0.999999:
        axis = _unit(np.cross(a, [1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.cross(a, [0.0, 1.0, 0.0]))
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1.0 + c)


def _axis_rotation(axis: np.ndarray, angle: float) -> np.ndarray:
    k = _unit(axis)
    K_ = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * K_ + (1 - math.cos(angle)) * K_ @ K_


def _quat(R: np.ndarray) -> np.ndarray:
    """Rotation matrix -> unit quaternion (w, x, y, z)."""
    from mathutils import Matrix

    q = Matrix(R.tolist()).to_quaternion()
    return np.array([q.w, q.x, q.y, q.z])


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product of (..., 4) arrays."""
    aw, ax, ay, az = np.moveaxis(a, -1, 0)
    bw, bx, by, bz = np.moveaxis(b, -1, 0)
    return np.stack([aw * bw - ax * bx - ay * by - az * bz,
                     aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw], axis=-1)


def _qrot(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate (n, 3) vectors by (n, 4) unit quaternions."""
    w = q[:, :1]
    u = q[:, 1:]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


# ----------------------------------------------------------------------------
# Fit: MakeHuman mesh -> our skeleton
# ----------------------------------------------------------------------------


class Fit:
    """Poses a MakeHuman mesh onto our skeleton (dual quaternions), then
    stretches each fit bone to our length."""

    def __init__(self, b: Body, fb: list[FitBone], weights: np.ndarray, girth: dict[str, float] | None = None):
        self.fb = fb
        self.names = [x.name for x in fb]
        self.W = weights
        girth = girth or {}
        hf = K.hand_frame(b)
        by = {x.name: x for x in fb}
        pn = {}
        for side, sfx in ((1, "L"), (-1, "R")):
            h = by[f"hand.{sfx}"]
            ax = _unit(h.mh[1] - h.mh[0])
            lat = by[f"f_index.01.{sfx}"].mh[0] - by[f"f_pinky.01.{sfx}"].mh[0]
            t = _unit(lat - ax * float(lat @ ax))
            pn[("mh", sfx)] = np.cross(ax, t) * side
            pn[("ours", sfx)] = hf.palm_normal if side > 0 else K.mirror(hf.palm_normal)
        refs = {"fwd": np.array([0.0, -1.0, 0.0]), "lat": np.array([1.0, 0.0, 0.0])}
        self.R, self.P, self.M = {}, {}, {}
        self.twist: dict[str, float] = {}
        for x in fb:
            dm = x.mh[1] - x.mh[0]
            do = x.ours[1] - x.ours[0]
            if x.ref == "min":
                R = _min_rotation(dm, do)
                if x.name.startswith("forearm"):
                    # Pronation: half of the hand's twist happens along the
                    # forearm, so the wrist blends two close rotations.
                    sfx = x.name[-1]
                    full = _frame(do, pn[("ours", sfx)]) @ _frame(dm, pn[("mh", sfx)]).T
                    a = _unit(do)
                    e = _unit(np.cross(a, [0.0, 0.0, 1.0]))
                    u, v = R @ (np.linalg.inv(R) @ e), full @ (np.linalg.inv(R) @ e)
                    u, v = u - a * (u @ a), v - a * (v @ a)
                    ang = math.atan2(float(np.cross(_unit(u), _unit(v)) @ a), float(_unit(u) @ _unit(v)))
                    self.twist[x.name] = math.degrees(ang)
                    R = _axis_rotation(a, 0.5 * ang) @ R
            elif x.ref == "palm":
                sfx = x.name[-1]
                R = _frame(do, pn[("ours", sfx)]) @ _frame(dm, pn[("mh", sfx)]).T
            else:
                R = _frame(do, refs[x.ref]) @ _frame(dm, refs[x.ref]).T
            self.R[x.name] = R
            if x.parent is None:
                self.P[x.name] = x.mh[0].copy()
            else:
                p = by[x.parent]
                self.P[x.name] = self.R[p.name] @ (x.mh[0] - p.mh[0]) + self.P[p.name]
            lm = float(np.linalg.norm(dm))
            lo = float(np.linalg.norm(do))
            r = lo / max(lm, 1e-6)
            d = _unit(do)
            stem = x.name[:-2] if x.name[-2:] in (".L", ".R") else x.name
            g = r if x.uniform else girth.get(stem, 1.0)
            self.M[x.name] = r * np.outer(d, d) + g * (np.eye(3) - np.outer(d, d))
        self.by = by

    def apply(self, co: np.ndarray, ids: np.ndarray) -> np.ndarray:
        x = co[ids]
        W = self.W[ids]
        B = len(self.fb)
        qr = np.zeros((B, 4))
        qd = np.zeros((B, 4))
        for i, fbn in enumerate(self.fb):
            q = _quat(self.R[fbn.name])
            t = self.P[fbn.name] - self.R[fbn.name] @ fbn.mh[0]
            qr[i] = q
            qd[i] = 0.5 * _qmul(np.array([0.0, *t]), q)
        dom = np.argmax(W, axis=1)
        sign = np.sign(qr[dom] @ qr.T)
        sign[sign == 0] = 1.0
        Ws = W * sign
        br = Ws @ qr
        bd = Ws @ qd
        norm = np.linalg.norm(br, axis=1, keepdims=True)
        br /= norm
        bd /= norm
        conj = br * np.array([1.0, -1.0, -1.0, -1.0])
        t = 2.0 * _qmul(bd, conj)[:, 1:]
        y = _qrot(br, x) + t
        out = np.zeros_like(y)
        for i, fbn in enumerate(self.fb):
            w = W[:, i]
            nz = w > 0
            if not nz.any():
                continue
            out[nz] += w[nz, None] * (fbn.ours[0] + (y[nz] - self.P[fbn.name]) @ self.M[fbn.name].T)
        return out


def mapped_weights(n: int, names: list[str]) -> np.ndarray:
    idx = {b: i for i, b in enumerate(names)}
    W = np.zeros((n, len(names)))
    for group, entries in MS.rig_weights().items():
        tgt = _weight_target(group)
        if tgt is None or tgt not in idx:
            continue
        j = idx[tgt]
        for vi, w in entries:
            if vi < n:
                W[vi, j] += w
    s = W.sum(axis=1)
    empty = s <= 1e-9
    W[empty, idx["torso"]] = 1.0  # unweighted helper geometry
    s[empty] = 1.0
    return W / s[:, None]


# ----------------------------------------------------------------------------
# Skin weights on our DEF bones
# ----------------------------------------------------------------------------

SPINE_GROUPS = {"spine", "spine.001", "spine.002", "spine.003", "spine.004", "spine.005", "pelvis.L", "pelvis.R"}


def _skin_target(group: str) -> list[tuple[str, float]] | None:
    """Our DEF bones (with shares) for a MakeHuman weight group; 'SPINE' is
    redistributed along our spine by height."""
    if group.startswith("mhmask"):
        return None
    if group in SPINE_GROUPS:
        return [("SPINE", 1.0)]
    base = group[4:] if group.startswith("DEF-") else group
    if base.startswith(FACE_PREFIXES):
        return [("DEF-spine.006", 1.0)]  # face groups (incl. brow.B.L.001 etc.): the face uses shape keys
    side = base[-2:] if base[-2:] in (".L", ".R") else ""
    if base.endswith((".L.001", ".R.001")):
        side = base[-6:-4]
        stem = base[:-6]
        return [(f"DEF-{stem}.{side[1]}.001", 1.0)]
    stem = base[:-2] if side else base
    s = side[1:] if side else ""
    helpers = {
        "elbow-helper": [(f"DEF-upper_arm.{s}.001", 0.5), (f"DEF-forearm.{s}", 0.5)],
        "knee-helper": [(f"DEF-thigh.{s}.001", 0.5), (f"DEF-shin.{s}", 0.5)],
        "pelvis-helper": [(f"DEF-thigh.{s}", 1.0)],
        "pelvis-helper.front": [(f"DEF-thigh.{s}", 1.0)],
        "shoulder-helper": [(f"DEF-shoulder.{s}", 0.5), (f"DEF-upper_arm.{s}", 0.5)],
    }
    if stem in helpers:
        return helpers[stem]
    if stem == "spine.006" or stem.startswith(FACE_PREFIXES) or stem == "eye_master":
        return [("DEF-spine.006", 1.0)]
    if stem.startswith("toe"):
        return [(f"DEF-toe.{s}", 1.0)]
    if stem in ("MCH-breast-parent",):
        return [("SPINE", 1.0)]
    return [(f"DEF-{stem}.{s}" if s else f"DEF-{stem}", 1.0)]


def skin_weights(b: Body, co: np.ndarray, n: int) -> list[dict[str, float]]:
    """MakeHuman's CC0 weights on our skeleton (the fit puts its bones on
    ours). The spine groups are re-split along our spine joints by height,
    because MakeHuman's spine segments sit elsewhere."""
    acc: list[dict[str, float]] = [dict() for _ in range(n)]
    for group, entries in MS.rig_weights().items():
        tgt = _skin_target(group)
        if not tgt:
            continue
        for vi, w in entries:
            if vi >= n:
                continue
            d = acc[vi]
            for bone, share in tgt:
                d[bone] = d.get(bone, 0.0) + w * share
    sp = K.spine_points(b)
    bones = ["DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003", "DEF-spine.004", "DEF-spine.005", "DEF-spine.006"]
    bounds = [float(p[2]) for p in sp[1:7]]
    blends = [0.045, 0.045, 0.045, 0.03, 0.02, 0.015]
    out = []
    for vi, d in enumerate(acc):
        s = d.pop("SPINE", 0.0)
        if s > 0:
            for bone, w in WT.chain(float(co[vi, 2]), bones[:6], bounds[:5], blends[:5]).items():
                d[bone] = d.get(bone, 0.0) + s * w
        if not d:
            d = {"DEF-spine.006": 1.0}  # eyeball helpers without weights
        out.append(WT.finalise(d))
    return out


# ----------------------------------------------------------------------------
# Stylisation
# ----------------------------------------------------------------------------


def _head_local(b: Body, p: np.ndarray) -> np.ndarray:
    """Blender metres -> head-local cm (X, F, U) of our head sculpt."""
    c = K.head_centre(b)
    q = (p - c) / (0.01 * b.head_scale)
    return np.stack([q[:, 0], -q[:, 1], q[:, 2]], axis=1)


def _head_world(b: Body, local: np.ndarray) -> np.ndarray:
    return H.to_world(local, K.head_centre(b), b.head_scale)


def scalp_weight(b: Body, p: np.ndarray) -> np.ndarray:
    """1 on the scalp (down to just below our hairline), easing to 0 into the forehead."""
    loc = _head_local(b, p)
    r = np.linalg.norm(loc, axis=1)
    phi = np.degrees(np.arccos(np.clip(loc[:, 2] / np.maximum(r, 1e-9), -1, 1)))
    theta = np.degrees(np.arctan2(loc[:, 0], loc[:, 1])) % 360.0
    hl = np.array([H.hairline_phi(t) for t in theta])
    # Full pull down to just below the hairline (hair borders snap to
    # hairline + 1.5 degrees on our sculpt), easing out over the forehead.
    t = np.clip((phi - (hl + 3.0)) / 12.0, 0.0, 1.0)
    return 1.0 - t * t * (3 - 2 * t)


class Styliser:
    """Position-dependent stylisation, fixed by the neutral shape so every
    shape key gets the same treatment."""

    def __init__(self, b: Body, neutral: np.ndarray, head_w: np.ndarray, eye_centres: tuple[np.ndarray, np.ndarray],
                 faces: list[tuple[int, ...]], body_ids: np.ndarray, eye_ids: np.ndarray,
                 extremity_w: np.ndarray | None = None, ear_w: np.ndarray | None = None):
        if extremity_w is None:
            extremity_w = np.zeros(len(neutral))
        # Ears: per side, the weighted vertices and the root vertices (the
        # third nearest the midline) they scale toward.
        self.ears = []
        if ear_w is not None:
            for side in (1, -1):
                ids = np.nonzero((ear_w > 0) & (neutral[:, 0] * side > 0))[0]
                if len(ids):
                    ax = np.abs(neutral[ids, 0])
                    root = ids[ax <= np.percentile(ax, 33)]
                    self.ears.append((side, ids, ear_w[ids], root))
        self.b = b
        self.head_w = head_w
        self.eyes = eye_centres
        self.eye_ids = eye_ids
        # Scalp: weight per vertex, and the radius of our sculpt along each ray.
        sw = scalp_weight(b, neutral) * (head_w > 0.5)
        sw[eye_ids] = 0.0
        self.scalp = sw
        loc = _head_local(b, neutral)
        dirs = loc / np.maximum(np.linalg.norm(loc, axis=1, keepdims=True), 1e-9)
        sel = np.nonzero(sw > 0)[0]
        t = np.zeros(len(neutral))
        if len(sel):
            t[sel] = S.raycast(H.head_sdf(H.FaceParams(**({"softness": 1.0} if b.bust > 0 else {}))),
                               np.zeros((len(sel), 3)), dirs[sel], t_max=30.0, step_scale=0.7, min_step=0.004)
        self.scalp_dirs = dirs
        self.scalp_t = t
        # Eyes: radial scale about each centre with a soft falloff.
        s = b.head_scale
        self.eye_r0, self.eye_r1 = EYE_R[0] * s, EYE_R[1] * s
        # Face smoothing neighbourhoods (skin only, face region).
        self.smooth_ids = np.nonzero((head_w > 0.5) & (sw < 0.05))[0]
        self.smooth_ids = self.smooth_ids[np.isin(self.smooth_ids, body_ids)]
        body_mask = np.zeros(len(neutral), dtype=bool)
        body_mask[body_ids] = True
        body_mask &= (head_w < 0.05) & (extremity_w < 0.05)
        self.body_smooth_ids = np.nonzero(body_mask)[0]
        nb: dict[int, set[int]] = {}
        for f in faces:
            for i in range(len(f)):
                a, c = f[i], f[(i + 1) % len(f)]
                nb.setdefault(a, set()).add(c)
                nb.setdefault(c, set()).add(a)
        self.nb = nb
        self.boundary = _boundary_verts(faces)
        self.passes = [(self._table(self.smooth_ids), FACE_SMOOTH), (self._table(self.body_smooth_ids), BODY_SMOOTH)]

    def _table(self, ids: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Movable ids (open edges stay fixed), padded neighbour table, mask."""
        ids = np.array([i for i in ids if i not in self.boundary and self.nb.get(i)], dtype=np.int64)
        deg = max((len(self.nb[i]) for i in ids), default=1)
        tab = np.zeros((len(ids), deg), dtype=np.int64)
        msk = np.zeros((len(ids), deg))
        for r, i in enumerate(ids):
            n = sorted(self.nb[i])
            tab[r, : len(n)] = n
            msk[r, : len(n)] = 1.0
        return ids, tab, msk / msk.sum(axis=1, keepdims=True)

    def __call__(self, p: np.ndarray) -> np.ndarray:
        q = p.copy()
        # Simplified planes: a few Taubin passes over the face (fixed open
        # edges: the eye and mouth openings keep their shape).
        for (ids, tab, msk), passes in self.passes:
            for _ in range(passes):
                for lam in (0.5, -0.53):
                    mean = np.einsum("nk,nkd->nd", msk, q[tab])
                    q[ids] += lam * (mean - q[ids])
        # Larger eyes (EYE_SCALE).
        for c in self.eyes:
            d = q - c
            r = np.linalg.norm(d, axis=1)
            k = np.clip((self.eye_r1 - r) / (self.eye_r1 - self.eye_r0), 0.0, 1.0)
            k = k * k * (3 - 2 * k)
            q += d * (np.array(EYE_SCALE) - 1.0)[None, :] * k[:, None]
        for side, ids, w, root in self.ears:
            c = q[root].mean(axis=0)
            k = 1.0 - (1.0 - EAR_SCALE) * w
            q[ids] = c + (q[ids] - c) * k[:, None]
            q[ids, 0] -= side * EAR_IN * w
        # Scalp: never more than SCALP_ALLOW outside our sculpt (radially from
        # our head centre), so stiff hats sized on it keep their clearance.
        sel = np.nonzero(self.scalp > 0)[0]
        if len(sel):
            c = K.head_centre(self.b)
            r = np.linalg.norm(q[sel] - c, axis=1)
            limit = (self.scalp_t[sel] * 0.01 * self.b.head_scale) + SCALP_ALLOW
            pull = self.scalp[sel] * np.maximum(r - limit, 0.0)
            q[sel] -= (q[sel] - c) / r[:, None] * pull[:, None]
        return q


def _boundary_verts(faces: list[tuple[int, ...]]) -> set[int]:
    count: dict[tuple[int, int], int] = {}
    for f in faces:
        for i in range(len(f)):
            e = tuple(sorted((f[i], f[(i + 1) % len(f)])))
            count[e] = count.get(e, 0) + 1
    return {v for e, n in count.items() if n == 1 for v in e}


# ----------------------------------------------------------------------------
# Binding our layout (the fitting cage) onto the MakeHuman surface
# ----------------------------------------------------------------------------

PART_BONES = {
    # layout part -> fit bones whose MakeHuman faces it may land on
    "trunk": ("torso", "neck", "head", "shoulder", "upper_arm", "thigh"),
    "arm": ("shoulder", "upper_arm", "forearm", "hand", "torso"),
    "leg": ("thigh", "shin", "foot", "toe", "torso"),
}


def _stem(name: str) -> str:
    s = name[:-2] if name[-2:] in (".L", ".R") else name
    return s.split(".0")[0] if s.startswith(("f_", "thumb")) else s


@dataclass
class Binding:
    vids: np.ndarray  # (n, 3) MakeHuman vertex ids
    bary: np.ndarray  # (n, 3)
    dist: np.ndarray  # (n,) signed distance travelled along the normal

    def eval(self, co: np.ndarray) -> np.ndarray:
        return np.einsum("nk,nkd->nd", self.bary, co[self.vids])


def _layout_normals(lay: BodyLayout, pts: np.ndarray) -> np.ndarray:
    n = np.zeros_like(pts)
    for f, _ in lay.faces:
        p = pts[list(f)]
        fn = np.zeros(3)
        for i in range(len(f)):
            fn += np.cross(p[i], p[(i + 1) % len(f)])
        n[list(f)] += fn
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def _vertex_parts(lay: BodyLayout) -> list[str]:
    """Part key of every layout vertex ('trunk', 'arm_1', 'leg_-1', 'f_index_1', 'ear')."""
    part = ["trunk"] * lay.n
    for key, tube in lay.tubes.items():
        first = 1 if tube.name in ("arm", "leg") else 0  # ring 0 of arms/legs is trunk
        for ring in tube.idx[first:]:
            for v in ring:
                part[v] = key
        if tube.cap is not None:
            part[tube.cap] = key
        if tube.base_cap is not None:
            part[tube.base_cap] = key
    for side, ids in lay.ears.items():
        for v in ids:
            part[v] = "ear"
    return part


def _tri(faces: list[tuple[int, ...]]) -> np.ndarray:
    out = []
    for f in faces:
        for i in range(1, len(f) - 1):
            out.append((f[0], f[i], f[i + 1]))
    return np.array(out, dtype=np.int64)


def _bary(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
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


def bind(lay: BodyLayout, pts: np.ndarray, mh_co: np.ndarray, tris: np.ndarray, tri_bone: list[str],
         max_d: float = 0.045) -> Binding:
    """Cast each layout vertex along its normal onto the MakeHuman surface of
    its body part (nearest hit either way), falling back to the nearest point."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    normals = _layout_normals(lay, pts)
    parts = _vertex_parts(lay)
    tri_stem = np.array([_stem(b) for b in tri_bone])
    tri_side = np.array([(1 if b.endswith(".L") else -1 if b.endswith(".R") else 0) for b in tri_bone])
    trees: dict[str, tuple] = {}

    def tree_for(part: str):
        if part in trees:
            return trees[part]
        if part in ("trunk", "ear"):
            ok = np.isin(tri_stem, PART_BONES["trunk"])
        else:
            name, side = part.rsplit("_", 1)
            side = int(side)
            if name in ("arm", "leg"):
                ok = np.isin(tri_stem, PART_BONES[name])
            elif name == "thumb":
                ok = np.isin(tri_stem, ("thumb", "hand"))
            else:
                ok = np.isin(tri_stem, (name, "hand"))
            ok &= (tri_side == side) | (tri_side == 0)
        sel = np.nonzero(ok)[0]
        bvh = BVHTree.FromPolygons([tuple(v) for v in mh_co], [tuple(x) for x in tris[sel]], all_triangles=True)
        trees[part] = (bvh, sel)
        return trees[part]

    n = lay.n
    vids = np.zeros((n, 3), dtype=np.int64)
    bary = np.zeros((n, 3))
    dist = np.zeros(n)
    for i in range(n):
        p = pts[i]
        bvh, sel = tree_for(parts[i])
        best = None
        for s in (1.0, -1.0):
            loc, nrm, idx, t = bvh.ray_cast(Vector(p), Vector(normals[i] * s), max_d)
            if loc is not None and (best is None or abs(t) < abs(best[2])):
                best = (np.array(loc), idx, t * s)
        if best is None:
            loc, nrm, idx, t = bvh.find_nearest(Vector(p))
            best = (np.array(loc), idx, float(t) if t is not None else 0.0)
        loc, idx, t = best
        tri = tris[sel[idx]]
        vids[i] = tri
        bary[i] = _bary(loc, *mh_co[tri])
        dist[i] = t
    return Binding(vids, bary, dist)


def cage_mask(lay: BodyLayout, pts: np.ndarray) -> np.ndarray:
    """1 = the cage follows MakeHuman, 0 = it keeps our sculpt."""
    b = lay.b
    w = np.ones(lay.n)
    head = np.array([lay.tv(r, c) for r in range(H.HEAD_ROWS) for c in ([0] if r == 0 else range(TCOLS))])
    for side, ids in lay.ears.items():
        w[ids] = 0.0  # moved with the head surface instead
    ul, fl = b.upper_arm_len, b.forearm_len
    fp = K.foot_points(b)
    ball_fwd = -fp["ball"][1]
    for key, tube in lay.tubes.items():
        if tube.name == "arm":
            for i, s in enumerate(tube.s_values):
                # The wrist and heel of the hand follow MakeHuman too, so
                # cuffs built at the wrist clear the visible skin.
                t = float(S.smoothstep(ul + fl + 0.03, ul + fl + 0.07, s))
                w[tube.idx[tube.first_ray + i]] = 1.0 - t
            w[tube.cap] = 0.0
        elif tube.name == "leg":
            ids = np.array([v for ring in tube.idx[1:] for v in ring] + [tube.cap])
            fwd = -pts[ids, 1]
            t = np.clip((fwd - (ball_fwd - 0.07)) / 0.05, 0.0, 1.0)
            w[ids] = np.minimum(w[ids], 1.0 - t * t * (3 - 2 * t))
        else:  # fingers and thumbs
            for ring in tube.idx:
                w[ring] = 0.0
            w[tube.cap] = 0.0
            w[tube.base_cap] = 0.0
    return w


def _move_ears(lay: BodyLayout, pos: np.ndarray, analytic: np.ndarray) -> None:
    """Ear shells follow the head surface around their root."""
    head = np.array([lay.tv(r, c) for r in range(1, H.HEAD_ROWS) for c in range(TCOLS)])
    for side, ids in lay.ears.items():
        root = analytic[ids].mean(axis=0)
        d = np.linalg.norm(analytic[head] - root, axis=1)
        near = head[np.argsort(d)[:8]]
        pos[ids] = analytic[ids] + (pos[near] - analytic[near]).mean(axis=0)


# ----------------------------------------------------------------------------
# Paint: regions, swatches, dye mask and UVs of the visible mesh
# ----------------------------------------------------------------------------

def face_mask_flags(polys: list[tuple[int, ...]], uvs: list[np.ndarray], names=("lips", "inside-mouth", "fingernails",
                                                                                 "toenails", "ears", "eyelids")) -> dict[str, np.ndarray]:
    out = {}
    for name in names:
        img = MS.mask_image(name)
        size = img.shape[0]
        vals = np.zeros(len(polys))
        for i, uv in enumerate(uvs):
            c = uv.mean(axis=0)
            x = int(np.clip(c[0] * size, 0, size - 1))
            y = int(np.clip(c[1] * size, 0, size - 1))
            vals[i] = img[y, x]
        out[name] = vals > 0.5
    return out


def feature_flags(skin_faces: list[tuple[int, ...]], co: np.ndarray, flags: dict[str, np.ndarray],
                  eye_centres, eye_pts) -> list[dict[str, bool]]:
    """Per-face feature flags: MakeHuman's masks plus the upper-lid lash line
    and the lower lip (below the mouth seam). `eye_pts`: the eyeball
    vertices of each eye (the hm08 skin is closed, so the lid rim is found
    as the upper-lid skin lying just outside the eyeball)."""
    lips = flags["lips"]
    inside = flags["inside-mouth"]
    lip_v = {v for f, l in zip(skin_faces, lips) if l for v in f}
    seam = np.array(sorted({v for f, m in zip(skin_faces, inside) if m for v in f} & lip_v))
    # Anime lash line: a bold band of upper-lid skin hugging the eyeball
    # (measured in the eye's own scaled frame, so it follows EYE_SCALE).
    cent = np.array([co[list(f)].mean(axis=0) for f in skin_faces])
    lash = np.zeros(len(skin_faces), dtype=bool)
    sc = np.array(EYE_SCALE)
    for c, pts in zip(eye_centres, eye_pts):
        r = float(np.mean(np.linalg.norm((pts - c) / sc, axis=1)))
        q = (cent - c) / sc
        d = np.linalg.norm(q, axis=1) / r
        lash |= (d > 0.98) & (d < LASH_BAND) & (q[:, 2] > 0.18 * r) & (q[:, 1] < 0.25 * r)
    out = []
    for fi, f in enumerate(skin_faces):
        d = {k: bool(v[fi]) for k, v in flags.items()}
        c = cent[fi]
        if d.get("lips") and len(seam):
            near = seam[np.argsort(np.abs(co[seam, 0] - c[0]))[:4]]
            d["lower_lip"] = bool(c[2] < co[near, 2].mean())
        if lash[fi]:
            d["lash"] = True
        out.append(d)
    return out


def eye_corner_uvs(b: Body, pts: np.ndarray, eye_centres, eye_radius: float) -> list[tuple[float, float]]:
    """Atlas UVs of eyeball corners: the painted eye, iris straight ahead,
    covering the same share of the eyeball as on our sculpted eye."""
    c = pts.mean(axis=0)
    ec = eye_centres[0] if c[0] > 0 else eye_centres[1]
    d = (pts - ec) / (0.01 * b.head_scale)
    scale = (H.EYE["r"] * 0.01 * b.head_scale) / eye_radius / (H.IRIS_R * 1.6)
    return [tuple(x) for x in A.eye_uv(np.stack([d[:, 0] * scale, d[:, 2] * scale], axis=1))]


# ----------------------------------------------------------------------------
# The whole build
# ----------------------------------------------------------------------------


def _clamp_macro(base: dict, delta: dict) -> dict:
    m = dict(base)
    for k, v in delta.items():
        m[k] = float(np.clip(m.get(k, 0.5) + v, 0.0, 1.0))
    return m


def _log(msg: str) -> None:
    print(f"[mh_body] {msg}", flush=True)


@dataclass
class MHBuild:
    cage: MeshData  # our layout on the MakeHuman surface (gear and hair are cut against it)
    visible: MeshData  # MakeHuman topology with regions, paint, weights and every shape key
    protect: np.ndarray  # visible vertices the decimation must keep (paint borders)
    report: dict
    keep_weights: np.ndarray | None = None  # per visible vertex: where LOD0 decimation keeps detail
    frame: object = None  # mh_face.FaceFrame (face normals after the LODs)
    head: str = "anime"  # which head (HEAD)
    body: object = None  # the reshaped body (a baked shape target, shape_target.Reshaper), else None


def style_delta(body_id: str, n: int = 19158) -> np.ndarray:
    """Sum of the body's anime-style detail targets (MH_STYLE)."""
    d = np.zeros((n, 3))
    for rel, wt in MH_STYLE.get(body_id, {}).items():
        d += wt * MS.target_delta(rel, n)
    return d


def key_spec(name: str) -> dict[str, float] | None:
    spec = MH_FACE_TARGETS.get(name)
    if spec is None and name in MH_EXPRESSION_TARGETS:
        spec = {f"expression/units/{u}/{t}": wt / len(_UNITS)
                for t, wt in MH_EXPRESSION_TARGETS[name].items() for u in _UNITS}
    return spec


# The bare foot. MakeHuman's toes and instep came through the
# wrap and the LOD0 decimation (which keeps least on the feet) as crumpled,
# shard-like faces. The feet are relaxed into a smooth, simple foot (Taubin
# passes, so they keep their size): the instep, heel and toes soften (and
# LOD0 keeps more of them, REGION_KEEP). Vertices on the feet's border with
# the calves stay; near the floor only the horizontal moves, so the sole
# stays flat on it.
FOOT_SMOOTH = 60
FOOT_FLOOR_Z = 0.02


def smooth_feet(cm, regions: list[str], passes: int = FOOT_SMOOTH) -> float:
    """Relaxes the `feet` region of a CutMesh in place (every shape key gets
    the same move, so face keys stay as they were). Returns the largest
    move (m)."""
    feet = np.zeros(len(cm.base), dtype=bool)
    other = np.zeros(len(cm.base), dtype=bool)
    nb: dict[int, set[int]] = {}
    for f, r in zip(cm.faces, regions):
        (feet if r == "feet" else other)[list(f)] = True
        for i in range(len(f)):
            a, c = f[i], f[(i + 1) % len(f)]
            nb.setdefault(a, set()).add(c)
            nb.setdefault(c, set()).add(a)
    ids = np.array([i for i in np.nonzero(feet & ~other)[0] if nb.get(int(i))], dtype=np.int64)
    if not len(ids):
        return 0.0
    ids = ids[~np.isin(ids, _boundary_verts(cm.faces))]
    deg = max(len(nb[int(i)]) for i in ids)
    tab = np.zeros((len(ids), deg), dtype=np.int64)
    msk = np.zeros((len(ids), deg))
    for r, i in enumerate(ids):
        n = sorted(nb[int(i)])
        tab[r, :len(n)] = n
        msk[r, :len(n)] = 1.0
    msk /= msk.sum(axis=1, keepdims=True)
    P0 = cm.base.copy()
    q = P0.copy()
    for _ in range(passes):
        for lam in (0.5, -0.53):
            mean = np.einsum("nk,nkd->nd", msk, q[tab])
            q[ids] += lam * (mean - q[ids])
    d = q - P0
    z0 = P0[:, 2]
    floor_k = np.clip(z0 / FOOT_FLOOR_Z, 0.0, 1.0)
    d[:, 2] *= floor_k * floor_k * (3 - 2 * floor_k)
    for k in cm.shapes:
        cm.shapes[k] = cm.shapes[k] + d
    return float(np.linalg.norm(d, axis=1).max())


def build(lay: BodyLayout, key_names: list[str], shape=None) -> MHBuild:
    """`shape` (optional, shape_target.Reshaper or Capture): called with the
    finished visible mesh just before the painted face is cut in. A Reshaper
    moves it onto a baked sculpt shape and returns the new body and the face
    frame measured on the new head, so the face is painted onto the sculpt's
    own features; Capture records it (wrap_sculpts.py) and stops the build."""
    from . import body_mesh as BM

    t0 = time.time()
    b = lay.b
    MS.enable()
    macro = MS.default_macro(**MH_MACROS[b.id])
    rigdef = MS.rig_definition()
    groups = MS.vertex_groups()
    polys, uvs = MS.face_uvs()
    eye_ids = np.concatenate([groups["helper-l-eye"], groups["helper-r-eye"]])
    eye_set = set(int(i) for i in eye_ids)
    skin_idx = [i for i, f in enumerate(polys) if max(f) < MS.BODY_VERTS]
    eye_idx = [i for i, f in enumerate(polys) if all(int(v) in eye_set for v in f)]
    skin_faces = [polys[i] for i in skin_idx]
    eye_faces = [polys[i] for i in eye_idx]
    # The visible mesh is the skin only: the anime eyes are painted on the
    # (flattened) lids, so MakeHuman's eyeballs are left out.
    ids = np.arange(MS.BODY_VERTS)
    style = style_delta(b.id)
    co0 = MS.human(macro) + style
    names = [x.name for x in fit_bones(b, co0, rigdef, groups)]
    Wfit = mapped_weights(len(co0), names)
    head_w = Wfit[:, names.index("head")]
    ext = [i for i, n in enumerate(names) if _stem(n) in ("hand", "foot", "toe", "thumb") or n.startswith("f_")]
    ext_w = Wfit[:, ext].sum(axis=1)

    def fitted(co: np.ndarray, fit: Fit | None = None) -> tuple[np.ndarray, Fit]:
        fit = fit or Fit(b, fit_bones(b, co, rigdef, groups), Wfit, GIRTH.get(b.id, {}))
        out = co.copy()
        fit_ids = np.concatenate([ids, eye_ids])  # eyeballs too: their centres anchor the painted eyes
        out[fit_ids] = fit.apply(co, fit_ids)
        return out, fit

    fco0, fit0 = fitted(co0)
    # Eyeball centres (the joint cubes sit lower, at MakeHuman's eye pivot).
    ec = (fco0[groups["helper-l-eye"]].mean(axis=0), fco0[groups["helper-r-eye"]].mean(axis=0))
    ear_faces = face_mask_flags(skin_faces, [uvs[i] for i in skin_idx], names=("ears",))["ears"]
    ear_w = np.zeros(len(co0))
    n_in, n_all = np.zeros(len(co0)), np.zeros(len(co0))
    for f, e in zip(skin_faces, ear_faces):
        n_all[list(f)] += 1
        if e:
            n_in[list(f)] += 1
    ear_w = np.where(n_all > 0, n_in / np.maximum(n_all, 1), 0.0)  # 1 inside the ear, partial on its rim
    sty0 = Styliser(b, fco0, head_w, ec, skin_faces, np.arange(MS.BODY_VERTS), eye_ids, ext_w, ear_w)
    vis_raw = sty0(fco0)
    # Anime face (mh_face): frame, ellipsoid and the fixed shape offset that
    # every shape key shares.
    lm = landmark_vertices(rigdef)
    skin_ids = np.arange(MS.BODY_VERTS)
    mflags = face_mask_flags(skin_faces, [uvs[i] for i in skin_idx], names=("inside-mouth", "lips"))
    inner_ids = np.array(sorted({v for f, m in zip(skin_faces, mflags["inside-mouth"]) if m for v in f}), dtype=np.int64)
    lip_ids = {v for f, m in zip(skin_faces, mflags["lips"]) if m for v in f}
    seam_all = np.array(sorted(lip_ids & set(inner_ids.tolist())), dtype=np.int64)
    seam = seam_all[np.abs(vis_raw[seam_all, 0]) < 0.008] if len(seam_all) else seam_all
    mouth_pt = vis_raw[seam].mean(axis=0) if len(seam) else vis_raw[lm["lip_mid"]].mean(axis=0)
    chin_z = float(vis_raw[lm["chin"]][:, 2].mean())
    ah = None
    if HEAD == "anime":
        # Our dedicated anime head (mh_head.py) replaces MakeHuman's above a
        # neck edge loop; MakeHuman's face offsets are not needed.
        from . import mh_head as MHH

        ah = MHH.build(b, vis_raw[:MS.BODY_VERTS], skin_faces, skin_weights(b, vis_raw, len(co0)),
                       b.height - HEAD_HEIGHT * b.head_scale)
        frame = MHH.make_frame(b, ah)
        D = np.zeros_like(vis_raw)
        surf = list(ah.faces)
        face_bone = [names[int(np.argmax(Wfit[list(skin_faces[fi])].sum(axis=0)))] for fi in ah.kept_faces]
        face_bone += ["head"] * (len(surf) - len(face_bone))

        def comb(p: np.ndarray) -> np.ndarray:
            return ah.combine(p[:MS.BODY_VERTS])
    else:
        frame = MF.make_frame(b, vis_raw, skin_ids, ec, mouth_pt,
                              vis_raw[lm["nose_tip"]].mean(axis=0), chin_z, head_w)
        D = MF.anime_offset(frame, vis_raw, skin_ids, inner_ids, skin_faces, seam=seam_all)
        surf = list(skin_faces)
        face_bone = [names[int(np.argmax(Wfit[list(f)].sum(axis=0)))] for f in surf]

        def comb(p: np.ndarray) -> np.ndarray:
            return p[:MS.BODY_VERTS]

    def sty(p: np.ndarray) -> np.ndarray:
        return sty0(p) + D

    vis0 = vis_raw + D
    V0 = comb(vis0)  # the visible body's base positions (MakeHuman topology, plus our head)
    _log(f"{b.id}: fitted ({time.time() - t0:.1f}s)")

    # --- Cage: our layout bound onto the stylised MakeHuman skin. ---------
    an = {"base": lay.positions()}
    builds = [k for k in key_names if k in BUILDS]
    an.update(BM.shape_key_positions(lay, builds))
    tris = _tri(surf)
    tri_bone = [fb for f, fb in zip(surf, face_bone) for _ in range(len(f) - 2)]
    binding = bind(lay, an["base"], V0, tris, tri_bone)
    cmask = cage_mask(lay, an["base"])

    def cage_pos(vis: np.ndarray, analytic: np.ndarray) -> np.ndarray:
        p = analytic + cmask[:, None] * (binding.eval(vis) - analytic)
        _move_ears(lay, p, analytic)
        return p

    variants = {}
    for k in builds:
        co = MS.human(_clamp_macro(macro, MH_BUILDS[k])) + style
        variants[k] = comb(sty(fitted(co)[0]))
    cage = BM.body_meshdata(lay, with_keys=False)
    keep = np.nonzero(cage.old_to_new >= 0)[0]
    cage.verts = cage_pos(V0, an["base"])[keep]
    cage.keys = {k: cage_pos(variants[k], an[k])[keep] for k in builds}
    _log(f"{b.id}: cage bound ({time.time() - t0:.1f}s); normal travel mean "
         f"{np.abs(binding.dist).mean() * 1000:.1f} mm, max {np.abs(binding.dist).max() * 1000:.1f} mm")

    # --- Visible mesh: MakeHuman topology. ---------------------------------
    keys = {}
    head_keys = {}
    if ah is not None:
        from . import mh_head as MHH

        head_keys = MHH.face_key_positions(ah, vis0[:MS.BODY_VERTS])
    for k in key_names:
        if k in variants:
            keys[k] = variants[k]
            continue
        if k in MF_EYE_KEYS or (ah is not None and k in ANIME_POST_KEYS):
            keys[k] = V0.copy()  # made in the painted face frame after the cuts
            continue
        if k in head_keys:
            keys[k] = head_keys[k]
            continue
        spec = key_spec(k)
        if not spec:
            continue
        delta = np.zeros_like(co0)
        for rel, wt in spec.items():
            delta += wt * MS.target_delta(rel, len(co0))
        moved, _ = fitted(co0 + delta, fit0)
        keys[k] = comb(sty(moved))
    flags = face_mask_flags(skin_faces, [uvs[i] for i in skin_idx])
    eye_pts = (vis0[groups["helper-l-eye"]], vis0[groups["helper-r-eye"]])
    feats = feature_flags(skin_faces, vis0, flags, ec, eye_pts)
    wts = skin_weights(b, vis0, len(co0))
    if ah is not None:
        feats = [feats[fi] for fi in ah.kept_faces] + list(ah.face_flags)
        wts = [wts[i] for i in ah.keep] + list(ah.weights_head)
    else:
        wts = wts[:MS.BODY_VERTS]
    part = list(face_bone)
    cm = MP.CutMesh({"base": V0, **keys}, wts, surf, {"eye": [False] * len(surf), "flags": feats, "part": part})
    n_orig = len(V0)
    el, er = K.eye_centres(b)
    bd = MP.Borders(lay, eye_offset=0.5 * (ec[0] + ec[1]) - 0.5 * (el + er))
    cut_counts = MP.apply_cuts(cm, bd, brows=False)
    body_new = None
    if shape is not None:
        # Regions and paint are decided on the pre-wrap shape (the borders are
        # our layout's) and carried through the face cuts as a face attribute.
        cls = MP.classify(cm, bd)
        res = shape.precut(b, cm, cls[0], frame)
        if res is not None:
            body_new, frame = res
            cm.attrs["cls"] = list(zip(*cls))
            _log(f"{b.id}: reshaped onto {getattr(shape, 'path', '?')} ({time.time() - t0:.1f}s)")
    feet_moved = smooth_feet(cm, cls[0] if shape is not None else MP.classify(cm, bd)[0])
    _log(f"{b.id}: feet smoothed (up to {feet_moved * 1000:.1f} mm)")
    dump = __import__("os").environ.get("MH_FACE_DUMP")
    if dump:  # debugging: the mesh just before the face cuts (mh_face iteration)
        import pickle

        with open(f"{dump}_{b.id}.pkl", "wb") as fh:
            pickle.dump((cm, frame), fh)
    cut_counts["face"] = MF.cut_features(cm, frame, "head")
    if "cls" in cm.attrs:
        regions = [c[0] for c in cm.attrs["cls"]]
        swatches = [c[1] for c in cm.attrs["cls"]]
        masks = [c[2] for c in cm.attrs["cls"]]
        del cm.attrs["cls"]
    else:
        regions, swatches, masks = MP.classify(cm, bd)
    MF.paint_features(cm, frame, regions, swatches, masks)
    movable = np.zeros(len(cm.base))
    for f, r in zip(cm.faces, regions):
        if r == "head":
            movable[list(f)] = 1.0
    for k, pos in MF.eye_keys(frame, cm.base, cm.faces, movable).items():
        if k in key_names:
            cm.shapes[k] = pos
    if ah is not None:
        for k, pos in MHH.expression_keys(frame, cm.base, movable).items():
            if k in key_names:
                cm.shapes[k] = pos
    _log(f"{b.id}: face painted ({time.time() - t0:.1f}s)")
    # Drop the refining points that ended up inside a flat paint region (they
    # lie on the original faces): only MakeHuman's vertices and the outline
    # points stay.
    paint = list(zip(swatches, masks, regions))
    tmp = MeshData()
    tmp.add_verts(cm.base, [{} for _ in cm.weights])
    for f, (sw, m, r) in zip(cm.faces, paint):
        tmp.add_face(f, r, sw, m)
    on_border = np.zeros(len(cm.base), dtype=bool)
    on_border[paint_borders(tmp, with_region=True)] = True
    removable = (np.arange(len(cm.base)) >= n_orig) & ~on_border
    n_before = len(cm.faces)
    made = np.arange(len(cm.base)) >= n_orig
    new_faces, src = MF.simplify(cm.base, cm.faces, paint, removable, frame, made)
    md = MeshData()
    md.add_verts(cm.base, [WT.finalise(w) for w in cm.weights])
    seen, dropped = set(), 0
    for f, si in zip(new_faces, src):
        key = tuple(sorted(f))
        if len(set(f)) != len(f) or key in seen:  # what mesh.validate would remove
            dropped += 1
            continue
        seen.add(key)
        sw, m, r = paint[si]
        md.add_face(f, r, sw, m)
    if dropped:
        _log(f"{b.id}: dropped {dropped} degenerate faces")
    md.keys = {k: v for k, v in cm.shapes.items() if k != "base"}
    md.compact()
    _log(f"{b.id}: simplified {n_before} -> {len(md.faces)} faces, {md.tri_count()} tris ({time.time() - t0:.1f}s)")
    borders = paint_borders(md)
    keep = np.zeros(len(md.verts))
    for f, r in zip(md.faces, md.region):
        idx = list(f)
        keep[idx] = np.maximum(keep[idx], REGION_KEEP[r])
    # Paint borders (face features, underwear edges) are nearly but not
    # absolutely kept: the decimation reaches the budget by thinning the
    # densest outline points last instead of overshooting it.
    keep[borders] = np.maximum(keep[borders], PAINT_KEEP)
    # The face's outlines are kept exactly (the decimation thins the body).
    head_v = np.zeros(len(md.verts), dtype=bool)
    for f, r in zip(md.faces, md.region):
        if r == "head":
            head_v[list(f)] = True
    protect = np.intersect1d(borders, np.nonzero(head_v)[0])
    if ah is not None:
        # Our head is already as light as it should be (a clean, even grid):
        # the decimation reaches the budget on MakeHuman's body instead.
        for f, r in zip(md.faces, md.region):
            if r in ("head", "ears"):  # the scalp (under hair) decimates like the body
                head_v[list(f)] = True
        protect = np.nonzero(head_v)[0]
    report = {"verts": len(md.verts), "tris_full": md.tri_count(), "keys": sorted(keys),
              "border_cut_verts": int(sum(cut_counts.values())), "face_cut_verts": int(cut_counts.get("face", 0)),
              "face_paint": {k: md.swatch.count(k) for k in ("sclera", "iris", "pupil", "eye_hl", "lash", "brow", "mouth_line", "skin_deep")},
              "missing_keys": sorted(set(key_names) - set(keys)),
              "travel_mm_mean": round(float(np.abs(binding.dist).mean() * 1000), 1),
              "seconds": round(time.time() - t0, 1)}
    report["measures"] = measure(md.verts, md.faces, body_new or b)
    current = BM.body_meshdata(lay, with_keys=False)
    report["measures_current"] = measure(current.verts, current.faces, b)
    _log(f"{b.id}: done {report}")
    return MHBuild(cage, md, protect, report, keep, frame, "anime" if ah is not None else "makehuman", body_new)


def paint_borders(md: MeshData, with_region: bool = False) -> np.ndarray:
    """Vertices on swatch or mask borders (kept by the decimation, so the
    underwear edges, lips and nails survive LOD0/LOD1)."""
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for fi, f in enumerate(md.faces):
        for i in range(len(f)):
            e = tuple(sorted((f[i], f[(i + 1) % len(f)])))
            edge_faces.setdefault(e, []).append(fi)
    out = set()
    for e, fs in edge_faces.items():
        if len({(md.swatch[f], md.mask[f]) + ((md.region[f],) if with_region else ()) for f in fs}) > 1 \
                or (with_region and len(fs) == 1):
            out.update(e)
    return np.array(sorted(out), dtype=np.int64)


def measure(pos: np.ndarray, faces: list[tuple[int, ...]], b: Body) -> dict:
    """The spec table's measures (docs/art/base-body-spec.md section 1), taken
    the same way on any body mesh so the current and the MakeHuman body can be
    compared like for like."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    z = pos[:, 2]
    top = float(z.max())
    # Deltoid to deltoid: widest point at shoulder-joint height (A-pose arms
    # leave the joint downward, so the deltoid cap is outermost there).
    sh = pos[(np.abs(z - b.shoulder_joint[2]) < 0.012) & (np.abs(pos[:, 1]) < 0.12)]
    # Hip width: widest point of the pelvis band (hands excluded).
    hip = pos[(z > b.hip_z - 0.06) & (z < b.hip_z + 0.02) & (np.abs(pos[:, 0]) < 0.3)]
    # Chin: lowest point of the face in front of the neck.
    face = pos[(z > b.eye_z - 0.15) & (z < b.eye_z - 0.05) & (pos[:, 1] < K.head_centre(b)[1] - 0.06)
               & (np.abs(pos[:, 0]) < 0.01)]
    tris = _tri(faces)
    bvh = BVHTree.FromPolygons([tuple(v) for v in pos], [tuple(t) for t in tris], all_triangles=True)
    # Inseam: first surface straight up the midline from the floor (the
    # crotch, or where the inner thighs meet below it).
    hit = bvh.ray_cast(Vector((0.0, float(K.fwd(*b.hip_joint)[1]), 0.2)), Vector((0, 0, 1)), 2.0)
    return {
        "height_m": round(top - float(z.min()), 3),
        "head_m": round(top - float(face[:, 2].min()), 3) if len(face) else None,
        "shoulder_w_m": round(float(sh[:, 0].max() - sh[:, 0].min()), 3) if len(sh) else None,
        "hip_w_m": round(float(hip[:, 0].max() - hip[:, 0].min()), 3) if len(hip) else None,
        "inseam_m": round(float(hit[0][2]), 3) if hit[0] is not None else None,
    }


def finish_lods(meshes: dict, mh: MHBuild) -> None:
    """After pipeline.make_lods: the face's custom normals from the face
    ellipsoid (mh_face.face_normals) on every LOD's head."""
    if mh.frame is None:
        return
    from . import mh_head as MHH

    from . import face_paint as FP

    normals = MHH.face_normals if mh.head == "anime" else MF.face_normals
    for parts in meshes.values():
        head = parts.get("head")
        if head is not None:
            normals(head, mh.frame)
    # The face texture (docs/art/character-look.md section 3.1): every LOD's head
    # maps into it by the face projection of its own vertices.
    head0 = meshes.get(0, {}).get("head")
    if head0 is None:
        return
    z = np.array([(head0.matrix_world @ v.co).z for v in head0.data.vertices])
    fmap = FP.FaceMap.for_frame(mh.frame, float(z.min()), float(z.max()))
    for parts in meshes.values():
        head = parts.get("head")
        if head is not None:
            FP.set_head_uvs(head, fmap)
    FP.FACE_BUILDS[mh.frame.b.id] = (mh.frame, fmap)


def clear_skin(objs: list, bb, hides: set[str], clearance: float = 0.0015, builds: bool = False) -> int:
    """Gear on a MakeHuman body: the items are cut against the fitting cage,
    which follows the visible skin to within a few millimetres; push any item
    vertex that ends up inside (or within `clearance` of) visible skin back
    out, moving every shape key by the same amount. `builds` (a body
    reshaped onto a sculpt): each build key is then cleared once more
    against that build's skin. Returns the number of vertices moved."""
    build_trees: dict = {}
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    skin = [o for r, o in bb.meshes.get(0, {}).items() if r not in hides]
    if not skin:
        return 0
    verts, tris = [], []
    for o in skin:
        me = o.data
        base = len(verts)
        mw = o.matrix_world
        verts += [tuple(mw @ v.co) for v in me.vertices]
        me.calc_loop_triangles()
        tris += [tuple(base + i for i in t.vertices) for t in me.loop_triangles]
    bvh = BVHTree.FromPolygons(verts, tris, all_triangles=True)
    moved = 0
    for o in objs:
        me = o.data
        n = len(me.vertices)
        co = np.zeros(n * 3)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        delta = np.zeros_like(co)
        for i, p in enumerate(co):
            loc, nrm, _, _ = bvh.find_nearest(Vector(p), 0.02)
            if loc is None:
                continue
            h = float((Vector(p) - loc) @ nrm)
            if -0.02 < h < clearance:
                delta[i] = np.array(nrm) * (clearance - h)
        if delta.any():
            moved += int((np.abs(delta).sum(axis=1) > 0).sum())
            me.vertices.foreach_set("co", (co + delta).ravel())
            if me.shape_keys:
                for kb in me.shape_keys.key_blocks:
                    k = np.zeros(n * 3)
                    kb.data.foreach_get("co", k)
                    kb.data.foreach_set("co", (k.reshape(-1, 3) + delta).ravel())
            me.update()
        if builds and me.shape_keys:
            # Each build key on its own, against the skin of that build.
            for kb in me.shape_keys.key_blocks[1:]:
                if kb.name not in BUILDS:
                    continue
                tree = build_trees.get(kb.name)
                if tree is None:
                    tree = build_trees[kb.name] = _skin_tree(skin, kb.name)
                k = np.zeros(n * 3)
                kb.data.foreach_get("co", k)
                k = k.reshape(-1, 3)
                d = np.zeros_like(k)
                for i, p in enumerate(k):
                    loc, nrm, _, _ = tree.find_nearest(Vector(p), 0.02)
                    if loc is None:
                        continue
                    h = float((Vector(p) - loc) @ nrm)
                    if -0.02 < h < clearance:
                        d[i] = np.array(nrm) * (clearance - h)
                if d.any():
                    kb.data.foreach_set("co", (k + d).ravel())
                    me.update()
    return moved


def _skin_tree(skin: list, key: str):
    """BVH of the visible skin objects with build key `key` applied."""
    from mathutils.bvhtree import BVHTree

    verts, tris = [], []
    for o in skin:
        me = o.data
        base = len(verts)
        mw = np.array(o.matrix_world)
        co = np.zeros(len(me.vertices) * 3)
        kb = me.shape_keys.key_blocks.get(key) if me.shape_keys else None
        (kb.data if kb is not None else me.vertices).foreach_get("co", co)
        verts += (co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]).tolist()
        me.calc_loop_triangles()
        tris += [tuple(base + i for i in t.vertices) for t in me.loop_triangles]
    return BVHTree.FromPolygons(verts, tris, all_triangles=True)
