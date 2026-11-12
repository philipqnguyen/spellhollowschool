"""Base body MeshData: regions, paint (masks and swatches), weights, shape keys.

Region names follow docs/art/base-body-spec.md section 4 (`hides` lists refer
to them). The body mask channels are r = skin tone and g = hair colour (for
the brows); unmasked faces (underwear, lashes, mouth, eyes) keep the atlas
colour.
"""

from __future__ import annotations

import math

import numpy as np

from . import atlas as A
from . import head as H
from . import skeleton as K
from . import weights as W
from .body import FINGER_COLS, TCOLS, BodyLayout
from .meshutil import MeshData
from .proportions import BUILDS, NEUTRAL

SKIN = (1.0, 0.0, 0.0)
HAIR = (0.0, 1.0, 0.0)
NONE = (0.0, 0.0, 0.0)

SHAPE_KEYS = list(BUILDS) + list(H.FACE_KEYS) + list(H.EXPRESSION_KEYS)


def shape_key_positions(lay: BodyLayout, names=None) -> dict[str, np.ndarray]:
    names = SHAPE_KEYS if names is None else names
    base_face = H.FaceParams(**lay.face_defaults())
    out = {}
    for name in names:
        if name in BUILDS:
            out[name] = lay.positions(BUILDS[name], base_face)
        else:
            deltas = H.FACE_KEYS.get(name) or H.EXPRESSION_KEYS[name]
            out[name] = lay.positions(NEUTRAL, H.apply_key(base_face, deltas))
    return out


def body_meshdata(lay: BodyLayout, with_keys: bool = True, key_names=None) -> MeshData:
    pts = lay.positions()
    md = MeshData()
    md.add_verts(pts, body_weights(lay, pts))
    paint_faces(lay, pts, md)
    md.tags = [tag for _, tag in lay.faces]
    if with_keys:
        md.keys = shape_key_positions(lay, key_names)
    keep = md.compact()  # the vertices inside the arm holes are not used
    remap = -np.ones(lay.n, dtype=np.int64)
    remap[keep] = np.arange(len(keep))
    md.old_to_new = remap  # layout vertex id -> mesh vertex id (gear builds on the rings)
    return md


# ----------------------------------------------------------------------------
# Paint and regions
# ----------------------------------------------------------------------------


def _vest(lay: BodyLayout, row: int, col: int) -> bool:
    """A fitted boat-neck undershirt: every torso face below the neckline ring
    (one dome row lower at the front). Rows are rings, so the edge is smooth."""
    if row >= lay.row_horiz:
        return True
    i = row - lay.row_dome
    theta = (col + 0.5) * 360.0 / TCOLS
    front = theta < 45.0 or theta > 315.0
    return i >= (3 if front else 2)


def paint_faces(lay: BodyLayout, pts: np.ndarray, md: MeshData) -> None:
    b = lay.b
    sc = lay.sc
    ul, fl = b.upper_arm_len, b.forearm_len
    centre = K.head_centre(b)
    waist_row = lay.row_horiz + int(np.argmin(np.abs(lay.horiz_z - b.waist_z)))
    for f, tag in lay.faces:
        c = pts[list(f)].mean(axis=0)
        parts = tag.split(":")
        region, swatch, mask, uv = "torso", "skin", SKIN, None
        if parts[0] == "head":
            region = "scalp"
        elif parts[0] == "trunk":
            r, col = int(parts[1]), int(parts[2])
            if r < H.HEAD_ROWS - 1:
                local = (c - centre) / (0.01 * b.head_scale)
                d = np.array([local[0], -local[1], local[2]])
                phi = math.degrees(math.acos(np.clip(d[2] / np.linalg.norm(d), -1, 1)))
                theta = math.degrees(math.atan2(d[0], d[1])) % 360.0
                region = "scalp" if phi < H.hairline_phi(theta) else "head"
                swatch, mask, is_eye = H.face_paint(r, col)
                if is_eye:
                    left = c[0] > 0
                    loc = np.array([[p[0], -p[1], p[2]] for p in (pts[list(f)] - centre) / (0.01 * b.head_scale)])
                    uv = [tuple(x) for x in A.eye_uv(H.eye_uv(loc, left))]
                    swatch = "skin"
            elif r < lay.row_dome:
                region = "neck"
            else:
                region = "torso" if r < waist_row else "hips"
        elif parts[0].startswith("ear"):
            region = "ears"  # its own region, so hair over the ears can hide them
        else:
            key = parts[0]
            tube = lay.tubes[key]
            if tube.name == "leg":
                if parts[1] in ("cap", "base"):
                    region = "feet"
                else:
                    k = int(parts[1])
                    ring = k + 1 - tube.first_ray
                    z = c[2]
                    if k < 2:
                        region = "hips"
                    elif z > b.knee[2] + 0.025 * sc:
                        region = "thighs"
                    elif ring >= 0 and tube.s_values[ring] is None:
                        region = "feet"
                    else:
                        region = "calves"
            elif tube.name == "arm":
                if parts[1] in ("cap", "base"):
                    region = "hands"
                else:
                    k = int(parts[1])
                    ring = k + 1 - tube.first_ray
                    s = tube.s_values[ring] if ring >= 0 else 0.0
                    region = "upper_arms" if s <= ul + 0.004 else ("forearms" if s <= ul + fl + 0.004 else "hands")
            else:
                region = "hands"
                k = parts[1]
                ring = len(tube.rings) - 1 if k == "cap" else (int(k) if k != "base" else 0)
                col = int(parts[2]) if len(parts) > 2 else 0
                if ring >= 5 and col in (0, FINGER_COLS - 1):
                    swatch = "nails"
        # Underwear: shorts from the waist row to the fourth leg ring, and the
        # undershirt above. Edges follow rows and rings.
        if parts[0] == "trunk" and region == "hips":
            swatch, mask = ("underwear_trim", NONE) if int(parts[1]) == waist_row else ("underwear", NONE)
        elif parts[0].startswith("leg") and parts[1] not in ("cap", "base") and int(parts[1]) < 4:
            swatch, mask = ("underwear_trim", NONE) if int(parts[1]) == 3 else ("underwear", NONE)
        elif parts[0] == "trunk" and region == "torso" and _vest(lay, int(parts[1]), int(parts[2])):
            swatch, mask = "underwear", NONE
        md.add_face(f, region, swatch, mask, uv=uv)


# ----------------------------------------------------------------------------
# Weights
# ----------------------------------------------------------------------------


def _spine_chain(lay: BodyLayout, z: float) -> W.Weights:
    sp = K.spine_points(lay.b)
    bones = ["DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003", "DEF-spine.004", "DEF-spine.005", "DEF-spine.006"]
    bounds = [float(p[2]) for p in sp[1:7]]
    blends = [0.045, 0.045, 0.045, 0.03, 0.02, 0.015]
    return W.chain(z, bones, bounds, blends)


def body_weights(lay: BodyLayout, pts: np.ndarray) -> list[W.Weights]:
    b = lay.b
    sc = lay.sc
    n = len(pts)
    w: list[W.Weights] = [dict() for _ in range(n)]
    J = K.joint_map(b)
    R = H.ROW_INDEX
    head_blend = {R["under_chin"]: 0.12, R["under_jaw"]: 0.3, R["throat"]: 0.55}
    # Trunk.
    for r in range(lay.trunk_rows):
        cols = [0] if r == 0 else range(TCOLS)
        for col in cols:
            vi = lay.tv(r, col)
            p = pts[vi]
            if r < H.HEAD_ROWS:
                k = head_blend.get(r, 0.0)
                w[vi] = W.mix((1 - k, {"DEF-spine.006": 1.0}), (k, {"DEF-spine.005": 1.0}))
                continue
            base = _spine_chain(lay, float(p[2]))
            side = 1 if p[0] >= 0 else -1
            sfx = "L" if side > 0 else "R"
            parts = []
            if r >= lay.row_dome and abs(p[0]) > 0.03:
                sh = J[f"shoulder.{sfx}"]
                d_seg = _dist_segment(p, sh.head, sh.tail)
                f_sh = (1 - float(np.clip((d_seg - 0.02) / 0.06, 0, 1))) * float(np.clip((p[2] - b.chest_z) / 0.08, 0, 1)) * 0.6
                ua = J[f"upper_arm.{sfx}"]
                f_ua = (1 - float(np.clip((np.linalg.norm(p - ua.head) - 0.035) / 0.06, 0, 1))) * 0.75
                if abs(p[0]) < abs(ua.head[0]) - 0.05:
                    f_ua = 0.0
                f_sh *= 1 - f_ua
                parts += [(f_ua, {f"DEF-upper_arm.{sfx}": 1.0}), (f_sh, {f"DEF-shoulder.{sfx}": 1.0})]
                rest = 1 - f_ua - f_sh
            elif p[2] < b.hip_z:
                # Groin and hip sides follow the thigh; the buttocks stay with the
                # pelvis until the gluteal fold (thigh weight above and behind
                # the joint would swing backward when the leg lifts).
                joint_z = b.hip_joint[2]
                t = float(np.clip((joint_z - 0.015 - p[2]) / (joint_z - b.crotch_z), 0, 1))
                behind = float(np.clip((p[1] - 0.0) / 0.06, 0, 1))
                f_th = (t ** 1.2) * (0.7 - 0.5 * behind) * float(np.clip((abs(p[0]) - 0.008) / 0.05, 0, 1))
                parts.append((f_th, {f"DEF-thigh.{sfx}": 1.0}))
                rest = 1 - f_th
            else:
                rest = 1.0
            parts.append((rest, base))
            w[vi] = W.mix(*parts)
    for ci in lay.crotch:
        w[ci] = {"DEF-spine": 0.4, "DEF-thigh.L": 0.3, "DEF-thigh.R": 0.3}
    for side, ids in lay.ears.items():
        for vi in ids:
            w[vi] = {"DEF-spine.006": 1.0}
    hip_z, knee_z, ankle_z = b.hip_joint[2], b.knee[2], b.ankle[2]
    fp = K.foot_points(b)
    ball_fwd = -fp["ball"][1]
    ul, fl = b.upper_arm_len, b.forearm_len
    hs = K.hand_scale(b)
    hf = K.hand_frame(b)
    for key, tube in lay.tubes.items():
        side = tube.side
        L = lambda name: W.side_name(name, side)  # noqa: E731
        if tube.name == "leg":
            bones = [L("DEF-thigh"), L("DEF-thigh") + ".001", L("DEF-shin"), L("DEF-shin") + ".001", L("DEF-foot")]
            # Chain positions run down the leg: use -z so they increase.
            bounds = [-0.5 * (hip_z + knee_z), -knee_z, -0.5 * (knee_z + ankle_z), -ankle_z]
            blends = [0.08 * sc, 0.03 * sc, 0.07 * sc, 0.025 * sc]
            rings = tube.idx
            for k, ids in enumerate(rings):
                if k == 0:
                    continue
                for vi in ids:
                    p = pts[vi]
                    cw = W.chain(-float(p[2]), bones, bounds, blends)
                    if p[2] < ankle_z + 0.02:
                        toe = float(np.clip((-p[1] - (ball_fwd - 0.012)) / 0.024, 0, 1))
                        foot_part = W.mix((1 - toe, {L("DEF-foot"): 1.0}), (toe, {L("DEF-toe"): 1.0}))
                        fw = cw.pop(L("DEF-foot"), 0.0)
                        cw = W.mix((1.0, cw), (fw, foot_part))
                    hipk = {1: 0.25, 2: 0.1, 3: 0.03}.get(k, 0.0)
                    w[vi] = W.mix((1 - hipk, cw), (hipk, {"DEF-spine": 1.0}))
            p = pts[tube.cap]
            toe = float(np.clip((-p[1] - (ball_fwd - 0.012)) / 0.024, 0, 1))
            w[tube.cap] = W.mix((1 - toe, {L("DEF-foot"): 1.0}), (toe, {L("DEF-toe"): 1.0}))
        elif tube.name == "arm":
            bones = [L("DEF-upper_arm"), L("DEF-upper_arm") + ".001", L("DEF-forearm"), L("DEF-forearm") + ".001", L("DEF-hand")]
            bounds = [0.5 * ul, ul, ul + 0.5 * fl, ul + fl]
            blends = [0.07, 0.028, 0.06, 0.02]
            fr = tube.first_ray
            wrist = hf.wrist if side > 0 else K.mirror(hf.wrist)
            thumb = hf.thumb_side if side > 0 else K.mirror(hf.thumb_side)
            knuckle_lat = []
            for f in K.FINGER_ORDER:
                ch = K.finger_chain(b, f)
                kp = ch[0] if side > 0 else K.mirror(ch[0])
                knuckle_lat.append(float((kp - wrist) @ thumb))
            for i, s in enumerate(tube.s_values):
                for vi in tube.idx[fr + i]:
                    p = pts[vi]
                    cw = W.chain(s, bones, bounds, blends)
                    shk = 0.35 * max(0.0, 1.0 - (s - 0.08 * sc) / 0.06) if s < 0.14 * sc else 0.0
                    if s > ul + fl:
                        u = (s - (ul + fl)) / (0.094 * hs)
                        palm_k = 0.65 * float(np.clip((u - 0.3) / 0.7, 0, 1))
                        lat = float((p - wrist) @ thumb)
                        g = np.exp(-((np.array(knuckle_lat) - lat) / (0.014 * hs)) ** 2)
                        g = g / g.sum()
                        pw = {L(f"DEF-palm.0{j + 1}"): float(g[j]) for j in range(4)}
                        cw = W.mix((1 - palm_k, cw), (palm_k, pw))
                    w[vi] = W.mix((1 - shk, cw), (shk, {L("DEF-shoulder"): 1.0}))
            # Bridges: blend the trunk ring and the first cast ring.
            for j in range(1, fr):
                t = j / fr
                for c, vi in enumerate(tube.idx[j]):
                    w[vi] = W.mix((1 - t, w[tube.idx[0][c]]), (t, w[tube.idx[fr][c]]))
            last = tube.idx[-1]
            w[tube.cap] = W.mix(*[(1.0 / len(last), w[vi]) for vi in last])
        else:
            line_pts = lay.finger_paths(side)[tube.name]
            cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(np.array(line_pts), axis=0), axis=1))])
            if tube.name == "thumb":
                bones = [L("DEF-hand"), L("DEF-thumb.01"), L("DEF-thumb.02"), L("DEF-thumb.03")]
                bounds = [0.35 * cum[1], cum[1], cum[2]]
                blends = [0.01, 0.005, 0.004]
            else:
                i = K.FINGER_ORDER.index(tube.name)
                bones = [L(f"DEF-palm.0{i + 1}"), L(f"DEF-{tube.name}.01"), L(f"DEF-{tube.name}.02"), L(f"DEF-{tube.name}.03")]
                bounds = [cum[1], cum[2], cum[3]]
                blends = [0.005, 0.005, 0.004]
            for k, s in enumerate(tube.s_values):
                cw = W.chain(s, bones, bounds, blends)
                for vi in tube.idx[k]:
                    w[vi] = cw
            w[tube.cap] = W.chain(cum[-1], bones, bounds, blends)
            w[tube.base_cap] = W.chain(0.0, bones, bounds, blends)
    # Smooth the junctions (shoulders, neck base, hips), never the face or hands.
    smooth_ids = set()
    for key, ids in lay.junctions.items():
        smooth_ids.update(int(i) for i in ids)
    smooth_ids.update(int(i) for i in lay.neck_junction)
    for r in range(lay.row_horiz, lay.trunk_rows):
        for col in range(TCOLS):
            if pts[lay.tv(r, col)][2] < b.hip_z + 0.05:
                smooth_ids.add(lay.tv(r, col))
    W.smooth(w, lay.neighbours, sorted(smooth_ids), iters=3)
    return [W.finalise(x) for x in w]


def _dist_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    t = float(np.clip((p - a) @ ab / float(ab @ ab), 0.0, 1.0))
    return float(np.linalg.norm(p - (a + ab * t)))


