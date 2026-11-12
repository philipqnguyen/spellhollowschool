"""Joint landmarks of a base body: the single source for the rig and the mesh.

`joints(body)` returns every bone of the Rigify Human meta-rig we use (face
removed) as head/tail points plus a roll hint, in Blender coordinates for the
A-pose rest of docs/art/base-body-spec.md section 2. The mesh builder reads the
same landmarks, so the skeleton always sits inside the flesh it deforms.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .proportions import Body

V = np.ndarray


def vec(x, y, z) -> V:
    return np.array([x, y, z], dtype=np.float64)


def unit(v) -> V:
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


def rotate(v, axis, angle: float) -> V:
    """Rodrigues rotation of v about a unit axis."""
    v = np.asarray(v, dtype=np.float64)
    k = unit(axis)
    c, s = math.cos(angle), math.sin(angle)
    return v * c + np.cross(k, v) * s + k * float(k @ v) * (1 - c)


def mirror(p: V) -> V:
    return vec(-p[0], p[1], p[2])


def fwd(x: float, f: float, z: float) -> V:
    """Point from (x, forward, z): the character faces -Y."""
    return vec(x, -f, z)


@dataclass
class Bone:
    name: str
    head: V
    tail: V
    parent: str | None
    connect: bool = False
    # Roll hint: the direction the bone's local Z axis should point to.
    z_axis: V | None = None


@dataclass
class HandFrame:
    """Orientation of a hand in the rest pose (left side; mirror for right)."""

    wrist: V
    axis: V  # wrist toward the fingers
    palm_normal: V  # out of the palm
    thumb_side: V  # across the palm toward the thumb


# Finger phalanx lengths for body_a (proximal, middle, distal), metres, with
# the spec's 105% hand scale already applied.
FINGERS = {
    "f_index": ((0.041, 0.026, 0.021), 0.032, 6.0),
    "f_middle": ((0.045, 0.029, 0.022), 0.011, 0.0),
    "f_ring": ((0.042, 0.027, 0.021), -0.010, -5.0),
    "f_pinky": ((0.034, 0.021, 0.019), -0.029, -11.0),
}
FINGER_ORDER = ("f_index", "f_middle", "f_ring", "f_pinky")
THUMB = (0.044, 0.032, 0.027)
# Rest curl per finger joint (degrees), the spec's "very slightly bent".
FINGER_CURL = (7.0, 9.0, 7.0)


def arm_frame(b: Body) -> tuple[V, V, V, V, V]:
    """Shoulder joint, elbow, wrist and the upper/fore arm directions (left)."""
    sj = fwd(*b.shoulder_joint)
    down45 = unit(vec(1.0, 0.0, -1.0))
    elbow = sj + down45 * b.upper_arm_len
    # 5 degree elbow bend, the forearm swinging forward.
    fore = unit(math.cos(math.radians(5)) * down45 + math.sin(math.radians(5)) * vec(0, -1, 0))
    wrist = elbow + fore * b.forearm_len
    return sj, elbow, wrist, down45, fore


def hand_frame(b: Body) -> HandFrame:
    _, _, wrist, _, d = arm_frame(b)
    down = unit(vec(0, 0, -1) - d * float(d @ vec(0, 0, -1)))
    front = unit(vec(0, -1, 0) - d * float(d @ vec(0, -1, 0)))
    # Palms face down and slightly forward.
    a = math.radians(22)
    palm_normal = unit(math.cos(a) * down + math.sin(a) * front)
    thumb_side = unit(np.cross(palm_normal, d))
    return HandFrame(wrist, d, palm_normal, thumb_side)


def hand_scale(b: Body) -> float:
    return b.hand_len / 0.200


def finger_chain(b: Body, name: str) -> list[V]:
    """Knuckle (MCP) to tip points of one finger (left hand), 4 points."""
    hf = hand_frame(b)
    s = hand_scale(b)
    lengths, side, spread = FINGERS[name]
    palm_len = 0.098 * s
    if name == "f_pinky":
        palm_len -= 0.010 * s
    elif name == "f_ring":
        palm_len -= 0.002 * s
    elif name == "f_index":
        palm_len -= 0.002 * s
    base = hf.wrist + hf.axis * palm_len + hf.thumb_side * side * s + hf.palm_normal * 0.004 * s
    direction = rotate(hf.axis, hf.palm_normal, math.radians(spread))
    curl_axis = unit(np.cross(direction, hf.palm_normal))
    pts = [base]
    for length, curl in zip(lengths, FINGER_CURL):
        direction = rotate(direction, curl_axis, math.radians(curl))
        pts.append(pts[-1] + direction * length * s)
    return pts


def thumb_chain(b: Body) -> list[V]:
    """Thumb CMC to tip (left hand), 4 points."""
    hf = hand_frame(b)
    s = hand_scale(b)
    base = hf.wrist + hf.axis * 0.016 * s + hf.thumb_side * 0.016 * s + hf.palm_normal * 0.006 * s
    direction = rotate(hf.axis, hf.palm_normal, math.radians(40))
    direction = unit(direction + hf.palm_normal * 0.22)
    pts = [base]
    curl_axis = unit(np.cross(direction, hf.palm_normal))
    for i, length in enumerate(THUMB):
        if i:
            direction = rotate(direction, curl_axis, math.radians(10))
        pts.append(pts[-1] + direction * length * s)
    return pts


def foot_points(b: Body) -> dict[str, V]:
    """Ankle, ball, toe tip and heel landmarks of the left foot."""
    ax, af, _ = b.ankle[0], -b.ankle[1], b.ankle[2]
    s = b.foot_len / 0.265
    heel_back = af - 0.062 * s
    tip = heel_back + b.foot_len
    return {
        "ankle": fwd(ax, af, b.ankle[2]),
        "ball": fwd(ax + 0.006 * s, heel_back + 0.192 * s, 0.024 * s),
        "tip": fwd(ax + 0.004 * s, tip, 0.020 * s),
        "heel_in": fwd(ax - 0.028 * s, heel_back + 0.035 * s, 0.0),
        "heel_out": fwd(ax + 0.036 * s, heel_back + 0.035 * s, 0.0),
        "heel_back": fwd(ax, heel_back, 0.030 * s),
    }


# Forward offsets (m) of the neck and head bones' heads (spine.004, .005,
# .006) from the body's midline: the standard neck line, a little behind it.
NECK_FWD = (-0.010, -0.006, -0.008)


def spine_points(b: Body) -> list[V]:
    """Eight points: hips, waist, ..., skull base, crown (spine..spine.006).

    The neck and head bones sit on `NECK_FWD` unless the body measured its
    own neck line (`b.extra["neck_fwd"]`, shape_target.Reshaper.finish: a
    sculpt whose neck sits further back than the standard line, the wrapped
    female's by 5-6 cm, which put the turn's pivots in front of her throat).
    The crown moves with the skull base, so the head bone keeps its
    direction; only the pivots move, never the mesh."""
    hz = b.hip_joint[2]
    crown = b.height
    skull_base = b.eye_z - 0.075 * b.head_scale
    neck_mid = 0.5 * (b.neck_base_z + b.neck_back_rise + skull_base)
    f4, f5, f6 = (b.extra or {}).get("neck_fwd") or NECK_FWD
    return [
        fwd(0, -0.018, hz + 0.015),
        fwd(0, -0.030, b.waist_z + 0.010),
        fwd(0, -0.028, 0.5 * (b.waist_z + b.chest_z) + 0.020),
        fwd(0, -0.024, b.chest_z + 0.015),
        fwd(0, f4, b.neck_base_z + b.neck_back_rise * 0.55),
        fwd(0, f5, neck_mid),
        fwd(0, f6, skull_base),
        fwd(0, -0.004 + (f6 - NECK_FWD[2]), crown),
    ]


def head_centre(b: Body) -> V:
    """Centre the head rays are cast from (at eye height, inside the skull)."""
    return fwd(0, b.head_fwd - 0.010 * b.head_scale, b.eye_z)


def eye_centres(b: Body) -> tuple[V, V]:
    from .head import EYE  # local import: head imports this module

    c = head_centre(b)
    s = 0.01 * b.head_scale  # EYE is in head-local centimetres
    left = c + fwd(EYE["x"] * s, EYE["fwd"] * s, EYE["z"] * s)
    return left, mirror(left)


# The rig's knee stands this far ahead of the hip-ankle line (m). Rigify's IK
# leg bends the way its rest chain bends; the wrapped bodies' legs measure
# straight to slightly hyperextended (the knee 0.3 cm behind the line), so
# every IK pose bent the knee backward (walk, run, duels) and the seated
# poses had to wind the whole leg about the hip-foot line by 135-180 degrees
# to bring the knee forward again, pinching the knee. A small forward rest
# bend (the joint a little toward the kneecap, as rigs usually place it)
# makes every IK bend go forward.
KNEE_LEAD = 0.02


def rig_knee(b: Body) -> V:
    """The knee joint the rig uses: `b.knee`, moved straight forward until it
    is at least KNEE_LEAD ahead of the hip-ankle line (the mesh, its weights
    and the shape targets keep `b.knee`)."""
    hip, knee, ankle = fwd(*b.hip_joint), fwd(*b.knee), foot_points(b)["ankle"]
    line = ankle - hip
    ll = float(line @ line)
    off = knee - (hip + line * float((knee - hip) @ line) / ll)
    ahead = -float(off[1])  # forward is -Y
    if ahead >= KNEE_LEAD:
        return knee
    # Moving the knee by d along -Y moves it ahead of the line by d * gain.
    gain = 1.0 - float(line[1]) ** 2 / ll
    return knee + vec(0.0, -(KNEE_LEAD - ahead) / gain, 0.0)


def joints(b: Body, rig: bool = False) -> list[Bone]:
    """Every bone of the trimmed meta-rig, parents first. `rig`: the rig's
    own knee (`rig_knee`, a forward rest bend) instead of the mesh's."""
    bones: list[Bone] = []
    sp = spine_points(b)
    zf = vec(0, -1, 0)
    names = ["spine", "spine.001", "spine.002", "spine.003", "spine.004", "spine.005", "spine.006"]
    for i, n in enumerate(names):
        bones.append(Bone(n, sp[i], sp[i + 1], names[i - 1] if i else None, connect=i > 0 and i != 4, z_axis=zf))
    # Pelvis and chest helpers.
    pelvis_tail = fwd(0.112, 0.060, b.hip_z + 0.065)
    bust_f = b.rib_fwd + 0.035 + 0.02 * b.bust
    bones.append(Bone("pelvis.L", sp[0].copy(), pelvis_tail, "spine", z_axis=vec(0, 0, 1)))
    bones.append(Bone("breast.L", fwd(0.075, bust_f - 0.12, b.chest_z + 0.015), fwd(0.075, bust_f, b.chest_z + 0.015), "spine.003",
                      z_axis=vec(0, 0, 1)))
    # Shoulder and arm.
    sj, elbow, wrist, _, _ = arm_frame(b)
    bones.append(Bone("shoulder.L", fwd(0.020, 0.040, b.neck_base_z + 0.005), sj + vec(-0.012, 0.0, 0.020), "spine.003",
                      z_axis=vec(0, 0, 1)))
    hf = hand_frame(b)
    s = hand_scale(b)
    bones.append(Bone("upper_arm.L", sj, elbow, "shoulder.L", z_axis=zf))
    bones.append(Bone("forearm.L", elbow, wrist, "upper_arm.L", connect=True, z_axis=zf))
    bones.append(Bone("hand.L", wrist, wrist + hf.axis * 0.078 * s, "forearm.L", connect=True, z_axis=zf))
    palm_side = (0.019, 0.006, -0.006, -0.018)
    for i, f in enumerate(FINGER_ORDER):
        chain = finger_chain(b, f)
        head = wrist + hf.axis * 0.024 * s + hf.thumb_side * palm_side[i] * s + hf.palm_normal * 0.002 * s
        bones.append(Bone(f"palm.0{i + 1}.L", head, chain[0], "hand.L", z_axis=hf.palm_normal))
        for j in range(3):
            bones.append(Bone(f"{f}.0{j + 1}.L", chain[j], chain[j + 1], f"palm.0{i + 1}.L" if j == 0 else f"{f}.0{j}.L",
                              connect=j > 0, z_axis=hf.palm_normal))
        if f == "f_index":
            th = thumb_chain(b)
            for j in range(3):
                bones.append(Bone(f"thumb.0{j + 1}.L", th[j], th[j + 1], "palm.01.L" if j == 0 else f"thumb.0{j}.L",
                                  connect=j > 0, z_axis=-hf.thumb_side))
    # Leg.
    hip = fwd(*b.hip_joint)
    knee = rig_knee(b) if rig else fwd(*b.knee)
    fp = foot_points(b)
    zb = vec(0, 1, 0)
    bones.append(Bone("thigh.L", hip, knee, "spine", z_axis=zb))
    bones.append(Bone("shin.L", knee, fp["ankle"], "thigh.L", connect=True, z_axis=zb))
    bones.append(Bone("foot.L", fp["ankle"], fp["ball"], "shin.L", connect=True, z_axis=zb))
    bones.append(Bone("toe.L", fp["ball"], fp["tip"], "foot.L", connect=True, z_axis=vec(0, 0, 1)))
    bones.append(Bone("heel.02.L", fp["heel_in"], fp["heel_out"], "foot.L", z_axis=vec(0, 0, 1)))
    # Mirror every .L bone.
    out = []
    for bone in bones:
        out.append(bone)
    for bone in bones:
        if bone.name.endswith(".L"):
            z = None if bone.z_axis is None else mirror(bone.z_axis)
            out.append(Bone(bone.name[:-2] + ".R", mirror(bone.head), mirror(bone.tail),
                            None if bone.parent is None else (bone.parent[:-2] + ".R" if bone.parent.endswith(".L") else bone.parent),
                            bone.connect, z))
    return out


def joint_map(b: Body, rig: bool = False) -> dict[str, Bone]:
    return {bone.name: bone for bone in joints(b, rig)}
