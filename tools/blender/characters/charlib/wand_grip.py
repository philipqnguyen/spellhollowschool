"""The wand in the right hand (pure numpy, no `bpy`): where the handle sits
in the rest-pose hand and how the fingers close round it.

The grip is diagonal, the way a wand or a pen is held to point with: the
handle crosses the palm from the heel of the hand (the pommel just out of
the little-finger side) to between the thumb and the index finger, so the
shaft leaves the fist about `ANGLE_DEG` off the hand's axis toward the
thumb. A hammer grip (the handle straight across the fist, about 70 degrees
off the axis) made every clip bend the wrist back past 100 degrees to point
the wand along the arm.

`grip_frame(body)` gives the grip point (the wand's origin, inside the fist),
the shaft direction and the hand's frame, in the rest armature space of the
right hand. `grip_curls(body)` solves, per finger, the bend of each joint
that lays the finger round the handle (a cylinder of the handle's radius
plus the finger's), and the thumb's, which closes over the handle along the
shaft. The clips key these on the finger controls (anim_spells.py
`wand_pose`), the client takes them from the `wand_light` clip as the hand's
grip whenever a wand is out, and tools/blender/vfx fits the wand to the
same frame (`wand_attach`). Unit tested: tests/test_wand_grip.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import skeleton as K
from .proportions import Body, to_json

# The handle: radius at the grip (tools/blender/vfx/vfx_models.py
# `wand_profile`), and a finger's half thickness at hand scale 1.
HANDLE_R = 0.0105
FINGER_R = 0.0072
# The shaft's angle off the hand's axis, toward the thumb (degrees), and the
# grip point in the hand frame (metres at hand scale 1: along the axis from
# the wrist, across toward the thumb, out of the palm).
ANGLE_DEG = 45.0
GRIP_AXIS = 0.097
GRIP_THUMB = 0.032
GRIP_PALM = 0.034
# Where the thumb's tip rests along the shaft from the grip (m): by the
# index finger, just behind the collar.
THUMB_TIP_ALONG = 0.015
THUMB_ALONG_WEIGHT = 0.05
# Joint bend limits (degrees): MCP, PIP, DIP; the DIP follows the PIP.
MCP_MAX, PIP_MAX = 95.0, 105.0
DIP_SHARE = 0.75
THUMB_MAX = (40.0, 60.0, 70.0)
# The thumb's opposition: its base turned about the hand's axis toward the palm (degrees, searched up to this).
THUMB_OPPOSE_MAX = 60.0


@dataclass(frozen=True)
class GripFrame:
    wrist: np.ndarray
    axis: np.ndarray
    palm: np.ndarray
    thumb: np.ndarray
    grip: np.ndarray
    wand: np.ndarray
    scale: float


def grip_frame(body: Body) -> GripFrame:
    """The right hand's rest frame and the wand in it (rest armature space)."""
    hf = K.hand_frame(body)
    s = K.hand_scale(body)
    m = K.mirror
    wrist, axis, palm, thumb = m(hf.wrist), m(hf.axis), m(hf.palm_normal), m(hf.thumb_side)
    a = math.radians(ANGLE_DEG)
    wand = K.unit(math.cos(a) * axis + math.sin(a) * thumb)
    grip = wrist + s * (GRIP_AXIS * axis + GRIP_THUMB * thumb + GRIP_PALM * palm)
    return GripFrame(wrist, axis, palm, thumb, grip, wand, s)


def finger_chains(body: Body) -> dict[str, list[np.ndarray]]:
    """MCP to tip of each right finger and the thumb (rest), 4 points each."""
    out = {f: [K.mirror(p) for p in K.finger_chain(body, f)] for f in K.FINGER_ORDER}
    out["thumb"] = [K.mirror(p) for p in K.thumb_chain(body)]
    return out


def curl_axes(chain: list[np.ndarray], palm: np.ndarray) -> list[np.ndarray]:
    """Each joint's bending axis (rest), as Poser.fingers bends them: a
    positive angle swings the segment toward the palm."""
    return [K.unit(np.cross(K.unit(chain[j + 1] - chain[j]), palm)) for j in range(3)]


def _quat(axis, deg: float) -> np.ndarray:
    h = math.radians(deg) / 2
    return np.concatenate([[math.cos(h)], np.asarray(axis) * math.sin(h)])


def _qmul(a, b) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def _qrot(q, v) -> np.ndarray:
    u = q[1:]
    return v + 2 * np.cross(u, np.cross(u, v) + q[0] * v)


def pose_chain(chain: list[np.ndarray], axes: list[np.ndarray], angles, base=None) -> list[np.ndarray]:
    """The chain's points with joint j bent by angles[j] degrees about axes[j]
    (rest axes, each joint's rotation carried by the ones before it, as the
    rig composes Poser.rot on a child of a posed parent). `base` (axis,
    degrees) turns the first joint first (the thumb's opposition)."""
    q = _quat(base[0], base[1]) if base else np.array([1.0, 0.0, 0.0, 0.0])
    pts = [chain[0]]
    for j in range(3):
        q = _qmul(q, _quat(axes[j], angles[j]))
        pts.append(pts[-1] + _qrot(q, chain[j + 1] - chain[j]))
    return pts


def axis_distance(points, frame: GripFrame) -> tuple[np.ndarray, np.ndarray]:
    """Distance of each point from the wand's axis, and its place along it from the grip (m)."""
    v = np.asarray(points) - frame.grip
    along = v @ frame.wand
    return np.linalg.norm(v - np.outer(along, frame.wand), axis=1), along


def _samples(pts) -> np.ndarray:
    """Points along a posed chain's segments past the first joint (where it can touch the handle)."""
    out = []
    for j in range(3):
        a, b = pts[j], pts[j + 1]
        for t in ((0.5, 1.0) if j == 0 else (0.33, 0.67, 1.0)):
            out.append(a + (b - a) * t)
    return np.array(out)


def _wrap_cost(pts, frame: GripFrame, reach: float) -> float:
    d, _ = axis_distance(_samples(pts), frame)
    over = np.clip(d - reach, 0.0, None)
    under = np.clip(reach - d, 0.0, None)
    return float(np.sum(over ** 2) + 40.0 * np.sum(under ** 2))


def _solve_curls(body: Body) -> dict[str, tuple[float, float, float]]:
    frame = grip_frame(body)
    chains = finger_chains(body)
    reach = HANDLE_R + FINGER_R * frame.scale
    out = {}
    for f in K.FINGER_ORDER:
        ch = chains[f]
        axes = curl_axes(ch, frame.palm)
        best = None
        for a1 in np.arange(0.0, MCP_MAX + 0.1, 2.5):
            for a2 in np.arange(0.0, PIP_MAX + 0.1, 2.5):
                ang = (a1, a2, a2 * DIP_SHARE)
                # Prefer the closed hand among equal fits (a fist, not a claw).
                c = _wrap_cost(pose_chain(ch, axes, ang), frame, reach) - 1e-7 * (a1 + a2)
                if best is None or c < best[0]:
                    best = (c, ang)
        out[f] = tuple(float(x) for x in best[1])
    ch = chains["thumb"]
    axes = curl_axes(ch, frame.palm)
    best = None
    for opp in np.arange(-THUMB_OPPOSE_MAX, THUMB_OPPOSE_MAX + 0.1, 5.0):
        for t1 in np.arange(0.0, THUMB_MAX[0] + 0.1, 5.0):
            for t2 in np.arange(0.0, THUMB_MAX[1] + 0.1, 5.0):
                ang = (t1, t2, min(THUMB_MAX[2], t2 * 1.1))
                pts = pose_chain(ch, axes, ang, (thumb_oppose_axis(frame), opp))
                c = _wrap_cost(pts, frame, reach)
                _, along = axis_distance([pts[3]], frame)
                c += THUMB_ALONG_WEIGHT * (float(along[0]) - THUMB_TIP_ALONG) ** 2 - 1e-7 * (t1 + t2 + abs(opp))
                if best is None or c < best[0]:
                    best = (c, ang, opp)
    out["thumb"] = tuple(float(x) for x in best[1])
    out["thumb_oppose"] = (float(best[2]), 0.0, 0.0)
    return out


def thumb_oppose_axis(frame: GripFrame) -> np.ndarray:
    """The axis the thumb's base turns about to come across the palm (the
    hand's own axis, signed so a positive angle swings it palm-ward)."""
    # Rotating the thumb side about +axis by a positive angle moves it toward
    # thumb x axis; pick the sign that moves it toward the palm.
    toward = np.cross(frame.axis, frame.thumb)
    return frame.axis if float(toward @ frame.palm) > 0 else -frame.axis


_CURLS: dict[str, dict[str, tuple[float, float, float]]] = {}


def grip_curls(body: Body) -> dict[str, tuple[float, float, float]]:
    """Degrees per joint (MCP, PIP, DIP; the thumb's CMC, MCP, IP) that wrap
    each right finger round the handle. Cached per body (its proportions)."""
    key = to_json(body)
    if key not in _CURLS:
        _CURLS[key] = _solve_curls(body)
    return _CURLS[key]


def gripped_chains(body: Body) -> dict[str, list[np.ndarray]]:
    """Every right finger and the thumb as `grip_curls` closes them (rest hand)."""
    frame = grip_frame(body)
    curls = grip_curls(body)
    out = {}
    for f, ch in finger_chains(body).items():
        base = (thumb_oppose_axis(frame), curls["thumb_oppose"][0]) if f == "thumb" else None
        out[f] = pose_chain(ch, curl_axes(ch, frame.palm), curls[f], base)
    return out
