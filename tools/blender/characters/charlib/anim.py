"""Animations keyed on the Rigify controls, then baked onto the game skeleton.

Every clip is a function of time that poses the rig through `Poser`, which
takes rotations as world-space axes measured in the rest pose (anatomical
directions such as "swing the arm forward") and converts them into the local
channels of Rigify's controls. Legs use Rigify's IK feet; arms, spine, neck
and fingers use FK controls.

Locomotion is in place. Walk and run are Quaternius's Universal Animation
Library motion retargeted onto the rig (charlib/ual.py): the walk faster
than the source with a longer stride, the run at its own rate. The client
plays them at rate 1 at sim-core's walk (3.0 m/s) and run (6.5 m/s) speeds,
so the walking feet slide (less than at the old 4.0 m/s).

Clips (frames at 30 fps): idle (breathing, weight shift), walk (the default
3.0 m/s gait), run, wave (2.5 s), bow (2.0 s), sit_down (from
standing in front of a seat onto it) and sit (looping, seated on a seat: the
root is the seat top, the hands rest in the lap, the heels reach a floor
SEAT_M below the seat at the body's maximum height), floor_rest (looping,
cross-legged on the floor, hands in the lap: for sitting where no seat is).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from . import skeleton as K
from . import ual as UAL
from .proportions import Body

FPS = 30
WALK_SPEED = 3.0
RUN_SPEED = 6.5


def _q(axis, deg: float) -> Quaternion:
    return Quaternion(Vector(axis).normalized(), math.radians(deg))


def ease(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


class Poser:
    """Sets Rigify control channels from world-space (rest) rotations."""

    def __init__(self, rig: bpy.types.Object, body: Body):
        self.rig = rig
        self.body = body
        self.pb = rig.pose.bones
        self.touched: set[str] = set()
        self.rest_q = {b.name: b.matrix_local.to_quaternion() for b in rig.data.bones}
        self.rest_head = {b.name: Vector(b.head_local) for b in rig.data.bones}
        sj, el, wr, d_up, d_fore = K.arm_frame(body)
        self.elbow_axis = {1: Vector(np.cross(d_up, d_fore)).normalized()}
        self.elbow_axis[-1] = Vector(np.cross(K.mirror(d_up), K.mirror(d_fore))).normalized()
        self.arm_rest_dir = {1: Vector(d_up), -1: Vector(K.mirror(d_up))}
        hf = K.hand_frame(body)
        self.palm_normal = {1: Vector(hf.palm_normal), -1: Vector(K.mirror(hf.palm_normal))}
        self.thumb_side = {1: Vector(hf.thumb_side), -1: Vector(K.mirror(hf.thumb_side))}
        fp = K.foot_points(body)
        self.foot_pts = {}
        for side in (1, -1):
            m = (lambda v: v) if side > 0 else K.mirror
            self.foot_pts[side] = {k: Vector(m(v)) for k, v in fp.items()}
        for pbone in self.pb:
            pbone.rotation_mode = "QUATERNION"

    def reset(self) -> None:
        for pbone in self.pb:
            pbone.location = (0, 0, 0)
            pbone.rotation_quaternion = (1, 0, 0, 0)
            pbone.scale = (1, 1, 1)

    def rot(self, name: str, q_world: Quaternion) -> None:
        r = self.rest_q[name]
        self.pb[name].rotation_quaternion = r.inverted() @ q_world @ r
        self.touched.add(name)

    def loc(self, name: str, v_world) -> None:
        r = self.rest_q[name]
        self.pb[name].location = r.inverted() @ Vector(v_world)
        self.touched.add(name)

    def side(self, base: str, side: int) -> str:
        return f"{base}.{'L' if side > 0 else 'R'}"

    # -- limbs ---------------------------------------------------------------
    def foot(self, side: int, forward: float, lift: float = 0.0, pitch: float = 0.0, yaw: float = 0.0,
             lateral: float = 0.0, roll: float = 0.0, toe_flat: bool = True) -> None:
        """Place an IK foot: `forward` (metres, toward -Y) of the flat foot,
        `lift` above the floor, `pitch` degrees (+ heel up, rolling over the
        ball; - toes up, rolling over the heel), `yaw` degrees (turning the toes
        toward +X for positive values on either side), `roll` about the toe axis."""
        f = self.foot_pts[side]
        ankle0 = self.rest_head[self.side("foot_ik", side)]
        ball0 = f["ball"].copy()
        ball0.z = 0.0
        heel0 = Vector((f["heel_back"].x, f["heel_back"].y - 0.02, 0.0))
        pivot0 = ball0 if pitch >= 0 else heel0
        q = _q((0, 0, 1), yaw) @ _q((0, -1, 0), roll * side) @ _q((1, 0, 0), pitch)
        offset = Vector((lateral, -forward, lift))
        # Flat foot moved by offset, then rotated about the pivot (yaw about the ankle).
        ankle = pivot0 + offset + q @ (ankle0 - pivot0)
        self.loc(self.side("foot_ik", side), ankle - ankle0)
        self.rot(self.side("foot_ik", side), q)
        if toe_flat and pitch > 0:
            self.rot(self.side("toe_ik", side), _q((1, 0, 0), -pitch * 0.9))
        else:
            self.rot(self.side("toe_ik", side), Quaternion())

    def arm(self, side: int, abduct: float = 0.0, flex: float = 0.0, twist: float = 0.0, elbow: float = 0.0,
            wrist: float = 0.0, wrist_side: float = 0.0) -> None:
        """FK arm. abduct + raises the arm sideways (from the A-pose), flex +
        swings it forward, twist + rotates it about its own axis (outward),
        elbow + flexes, wrist + bends the hand toward the palm."""
        qa = _q((0, -side, 0), abduct)
        qf = _q((-1, 0, 0), flex)
        dir_after = qf @ qa @ self.arm_rest_dir[side]
        qt = _q(dir_after * -side, twist)
        self.rot(self.side("upper_arm_fk", side), qt @ qf @ qa)
        self.rot(self.side("forearm_fk", side), _q(self.elbow_axis[side], elbow))
        q_w = _q(Vector(np.cross(self.arm_rest_dir[side], self.palm_normal[side])), wrist) @ _q(self.palm_normal[side], wrist_side)
        self.rot(self.side("hand_fk", side), q_w)

    def fingers(self, side: int, curl: float, thumb: float = 0.0, spread: float = 0.0) -> None:
        """Curl every finger joint by `curl` degrees (pinky a little more)."""
        b = self.body
        pn = np.array(self.palm_normal[side])
        for i, f in enumerate(K.FINGER_ORDER):
            ch = K.finger_chain(b, f)
            if side < 0:
                ch = [K.mirror(p) for p in ch]
            extra = 1.0 + 0.12 * i
            for j in range(3):
                name = self.side(f"{f}.0{j + 1}", side)
                d = K.unit(ch[j + 1] - ch[j])
                axis = np.cross(d, pn)
                ang = curl * extra * (0.8 if j == 0 else 1.0)
                q = _q(axis, ang)
                if j == 0 and spread:
                    q = _q(pn, spread * (i - 1.5) * (-side)) @ q
                self.rot(name, q)
        th = K.thumb_chain(b)
        if side < 0:
            th = [K.mirror(p) for p in th]
        for j in range(3):
            name = self.side(f"thumb.0{j + 1}", side)
            d = K.unit(th[j + 1] - th[j])
            self.rot(name, _q(np.cross(d, pn), thumb * (0.5 if j == 0 else 1.0)))

    def key(self, frame: int) -> None:
        for name in self.touched:
            pbone = self.pb[name]
            pbone.keyframe_insert("location", frame=frame, group=name)
            pbone.keyframe_insert("rotation_quaternion", frame=frame, group=name)


# ----------------------------------------------------------------------------
# Poses
# ----------------------------------------------------------------------------


def stand(p: Poser, t: float = 0.0, breathe: float = 1.0) -> None:
    """Relaxed stand: arms down at the sides, soft hands. t in seconds."""
    w = 2 * math.pi * t / 4.0
    p.loc("torso", (0.011 * math.sin(w), 0.0, -0.006 + 0.0025 * breathe * math.sin(2 * w)))
    p.rot("torso", Quaternion())
    p.rot("hips", _q((0, 1, 0), 1.6 * math.sin(w)) @ _q((0, 0, 1), 1.2 * math.sin(w + 0.6)))
    p.rot("chest", _q((0, 1, 0), -1.0 * math.sin(w)) @ _q((1, 0, 0), 1.0 - 1.2 * breathe * math.sin(2 * w)))
    p.rot("neck", _q((1, 0, 0), -2.0))
    p.rot("head", _q((0, 0, 1), 3.5 * math.sin(w * 0.5 + 1.0)) @ _q((1, 0, 0), 1.5 * math.sin(w)))
    for side in (1, -1):
        p.foot(side, forward=0.015 if side < 0 else -0.01, yaw=6.0 * side, lateral=0.012 * side)
        p.arm(side, abduct=-36.0 - 1.0 * breathe * math.sin(2 * w), flex=3.0, twist=0.0, elbow=14.0, wrist=6.0)
        p.fingers(side, 18.0, thumb=10.0)
    p.rot("shoulder.L", Quaternion())
    p.rot("shoulder.R", Quaternion())


def idle(p: Poser, frame: float) -> None:
    stand(p, frame / FPS)


def wave(p: Poser, frame: float) -> None:
    stand(p, 0.0, breathe=0.0)
    k = ease(frame / 12.0) * (1 - ease((frame - 62.0) / 13.0))
    waving = max(0.0, min(1.0, (frame - 10) / 6.0)) * (1 - max(0.0, min(1.0, (frame - 58) / 6.0)))
    osc = math.sin(2 * math.pi * 3.0 * max(0.0, frame - 12) / 48.0) * waving
    side = -1  # right hand
    # Upper arm out and up, rotated outward so the bent forearm stands up;
    # the hand sways side to side from the elbow.
    p.arm(side, abduct=-36.0 + k * 118.0, flex=3.0 + k * 12.0, twist=k * 85.0, elbow=14.0 + k * 62.0 + osc * 16.0,
          wrist=6.0 - k * 14.0, wrist_side=osc * 6.0)
    p.fingers(side, 18.0 * (1 - k) + 5.0 * k, thumb=10.0 * (1 - k), spread=7.0 * k)
    p.rot("chest", _q((0, 0, 1), -6.0 * k) @ _q((0, 1, 0), 3.0 * k) @ _q((1, 0, 0), 1.0))
    p.rot("head", _q((0, 1, 0), 6.0 * k) @ _q((0, 0, 1), -5.0 * k))


def bow(p: Poser, frame: float) -> None:
    stand(p, 0.0, breathe=0.0)
    k = ease(frame / 18.0) * (1 - ease((frame - 38.0) / 20.0))
    p.loc("torso", (0.0, 0.055 * k, -0.006 - 0.02 * k))
    p.rot("hips", _q((1, 0, 0), 24.0 * k))
    p.rot("chest", _q((1, 0, 0), 1.0 + 16.0 * k))
    p.rot("neck", _q((1, 0, 0), -2.0 + 6.0 * k))
    p.rot("head", _q((1, 0, 0), 8.0 * k))
    for side in (1, -1):
        p.arm(side, abduct=-38.0, flex=3.0 - 26.0 * k, elbow=14.0 + 6.0 * k, wrist=6.0)


def floor_rest(p: Poser, frame: float) -> None:
    """Sitting on the floor where there is no seat (the root on the floor):
    the hips on the ground, the legs out in front with the knees a little
    up, the heels on the floor, leaning back slightly, the hands resting in
    the lap. The legs reach from the body's own hip joints, so no knee or
    foot goes below the floor at any height."""
    from . import anim_spells as AS

    b = p.body
    t = frame / FPS
    br = math.sin(2 * math.pi * t / 3.0)
    hip_z = sit_height(b)
    p.loc("torso", (0.0, 0.04 * b.height / 1.78, hip_z - b.hip_joint[2] + 0.002 * br))
    p.rot("torso", _q((1, 0, 0), -6.0))
    p.rot("hips", _q((1, 0, 0), 2.0))
    p.rot("chest", _q((1, 0, 0), 10.0 - 1.2 * br) @ _q((0, 0, 1), 2.0 * math.sin(2 * math.pi * t / 6.0)))
    p.rot("neck", _q((1, 0, 0), 2.0))
    p.rot("head", _q((1, 0, 0), -4.0) @ _q((0, 0, 1), 4.0 * math.sin(2 * math.pi * t / 6.0 + 1.0)))
    for side in (1, -1):
        p.foot(side, forward=0.0)
    AS._lying_feet(p, 1.0, yaws=((-1, 10.0), (1, 10.0)), reach_k=0.93, pitch=-55.0)
    for side in (1, -1):
        AS.knee_twist(p, side, AS.knee_up_twist(p, side))  # the knees rise, never into the floor
    AS.update()
    ahead = 0.45 * b.height / 1.78
    lap_hands(p, 1.0, hip_z, ahead)


# Sitting cross-legged on a floor seat (a rug or a hearth step,
# the manifest's `floor_seats`; the root is the seat's top). Numbers at the
# default height, scaled by the body's height: where each ankle rests (x
# across, + the body's left; y ahead of the hips; z above the floor), the
# toes' yaw and the roll onto the foot's outer edge, the knee's height.
# The right foot tucks under the left knee, the left foot rests in front of
# the right shin, so the shins never meet.
FLOOR_SIT = {
    "ankle": {-1: (0.11, -0.21, 0.065), 1: (-0.12, -0.37, 0.065)},
    "yaw": {-1: 70.0, 1: -70.0},
    "roll": 60.0,
    "knee_z": 0.17,
    "lean": 8.0,
    "back": 0.02,
}


def _floor_sit_knee(p: Poser, side: int, knee_z: float) -> float:
    """The `knee_twist` that rests this side's knee out to the side at
    `knee_z` above the floor (coarse search, then refined; the knee must
    point outward, never across the body)."""
    from . import anim_spells as AS

    name = p.side("thigh_ik", side)
    hip = p.pb[p.side("ORG-thigh", side)]
    knee = p.pb[p.side("ORG-shin", side)]

    def cost(deg):
        p.pb[name].rotation_quaternion = Quaternion((0.0, 1.0, 0.0), math.radians(deg * side))
        AS.update()
        out = (knee.head.x - hip.head.x) * side
        return abs(knee.head.z - knee_z) + (0.0 if out > 0.12 else 1.0 + (0.12 - out))

    best = min(range(-180, 180, 10), key=cost)
    best = min(range(best - 9, best + 10, 1), key=cost)
    p.touched.add(name)
    return float(best)


_FLOOR_SIT_KNEES: dict = {}


def floor_sit(p: Poser, frame: float) -> None:
    """Sitting cross-legged on a floor seat (the root on the
    rug's or the step's top): upright with a slight forward lean, the knees
    resting out to the sides, the feet tucked on their outer edges, the
    hands in the lap; breathing. Compact (about 0.6 m across), no IK
    against the world. Modest, relaxed."""
    from . import anim_spells as AS

    b = p.body
    s = b.height / 1.78
    t = frame / FPS
    br = math.sin(2 * math.pi * t / 3.0)  # 2 breaths and 1 sway a loop (6 s)
    hip_z = sit_height(b)
    p.loc("torso", (0.0, FLOOR_SIT["back"] * s, hip_z - b.hip_joint[2] + 0.002 * br))
    p.rot("torso", _q((1, 0, 0), FLOOR_SIT["lean"] * 0.5))
    p.rot("hips", _q((1, 0, 0), -FLOOR_SIT["lean"] * 0.5))
    p.rot("chest", _q((1, 0, 0), FLOOR_SIT["lean"] * 0.5 - 1.0 * br) @ _q((0, 0, 1), 1.5 * math.sin(2 * math.pi * t / 6.0)))
    p.rot("neck", _q((1, 0, 0), -2.0))
    p.rot("head", _q((1, 0, 0), 2.0) @ _q((0, 0, 1), 4.0 * math.sin(2 * math.pi * t / 6.0 + 1.0)))
    for side in (1, -1):
        name = p.side("foot_ik", side)
        ankle0 = p.rest_head[name]
        x, y, z = FLOOR_SIT["ankle"][side]
        target = Vector((x * s, -b.hip_joint[1] + y * s, z * s))
        q = _q((0, 0, 1), FLOOR_SIT["yaw"][side]) @ _q((0, -1, 0), FLOOR_SIT["roll"] * side)
        r = p.rest_q[name]
        p.pb[name].location = r.inverted() @ (target - ankle0)
        p.pb[name].rotation_quaternion = r.inverted() @ q @ r
        p.touched.add(name)
        p.rot(p.side("toe_ik", side), Quaternion())
    for side in (1, -1):
        key = (b.id, round(b.height, 4), side)
        if key not in _FLOOR_SIT_KNEES:
            _FLOOR_SIT_KNEES[key] = _floor_sit_knee(p, side, FLOOR_SIT["knee_z"] * s)
        AS.knee_twist(p, side, _FLOOR_SIT_KNEES[key])
    AS.update()
    lap_hands(p, 1.0, hip_z, 0.5 * s)  # on the front shin, well ahead of the hips


# Riding a broom (protocol v8 `fly` and `hover`). The root
# is the flyer's position (the server keeps it at least the flight rules'
# ground clearance, 0.3 m, over the ground or the water); the broom prop
# (charlib/broom.py) hangs from the hips bone, its handle `SEAT_BELOW` under
# it, so the clip puts the hips where the rider sits and solves the legs and
# hands against that handle line. The pelvis keeps its rest rotation (the
# lean is in the chest), so the hips bone carries the broom without turning
# it. Seated astride: the thighs forward and down either side of the handle,
# the knees bent and the feet tucked back under the hips, toes down; both
# hands rest on the handle in front of the lap (the back hand the left);
# no IK against the world. Numbers at the reference height (1.78 m), scaled
# by the body's height; leg reaches by the body's own leg length.
BROOM_RIDE = {
    "hip_lift": 0.05,  # the lowest point (the toes) above the root, at the reference height
    "ankle_back": 0.12,  # the ankles behind the hip joints, a fraction of the leg's length
    "ankle_drop": 0.52,  # the ankles under the hip joints, a fraction of the leg's length
    "ankle_out": 0.095,  # the ankles from the middle (m)
    "knee_out": 0.11,  # the knees from the middle (m)
    "toe_pitch": 48.0,  # the feet pointed, toes down and back (degrees)
    "lean": {"broom_ride": 26.0, "broom_hover": 12.0},  # the chest's forward lean (degrees)
}


def _broom_knee(p: Poser, side: int, knee_out: float) -> float:
    """The `knee_twist` that points this side's knee ahead, `knee_out`
    from the middle (never across the handle): coarse, then refined."""
    from . import anim_spells as AS

    name = p.side("thigh_ik", side)
    knee = p.pb[p.side("ORG-shin", side)]

    def cost(deg):
        p.pb[name].rotation_quaternion = Quaternion((0.0, 1.0, 0.0), math.radians(deg * side))
        AS.update()
        out = knee.head.x * side
        return knee.head.y + 2.0 * abs(out - knee_out) + (0.0 if out > 0.06 else 1.0)

    best = min(range(-180, 180, 10), key=cost)
    best = min(range(best - 9, best + 10, 1), key=cost)
    p.touched.add(name)
    return float(best)


_BROOM_KNEES: dict = {}


def _broom_hip_z(b: Body) -> float:
    """The hip joints' height over the root when riding (reference pose,
    before the bob): the tucked feet's toes `hip_lift` over the root."""
    s = b.height / 1.78
    leg = float(np.linalg.norm(np.array(b.hip_joint) - np.array(b.ankle)))
    toe_drop = b.foot_len * 0.75 * math.sin(math.radians(BROOM_RIDE["toe_pitch"])) + b.ankle[2] * 0.5
    return BROOM_RIDE["ankle_drop"] * leg + toe_drop + BROOM_RIDE["hip_lift"] * s


def broom_seat(p: Poser) -> Vector:
    """Where the hips bone is in the pose so far (the broom's origin)."""
    from . import anim_spells as AS

    AS.update()
    return p.pb["DEF-spine"].head.copy()


def broom_hands(p: Poser, origin: Vector) -> None:
    """Both hands resting on the handle in front of the lap, one behind the
    other (the left behind), each laid across it from its own side: the
    palm down on its top, the fingers loosely over the far side; the elbows
    out and back. `origin` is the hips bone (the broom's origin)."""
    from . import anim_spells as AS
    from . import broom as BR

    s = p.body.height / 1.78
    top = origin.z - BR.SEAT_BELOW
    for side, ahead in ((1, BR.GRIPS[0]), (-1, BR.GRIPS[1])):
        axis = Vector((-0.72 * side, -0.62, -0.22)).normalized()
        palm = Vector((-0.35 * side, -0.1, -0.93)).normalized()
        centre = Vector((0.004 * side, origin.y - ahead * s, top + 0.052 * s))
        wrist = centre - axis * 0.055 * s
        AS.place_hand(p, side, wrist, axis, palm, (side * 0.75, 0.45, -0.5))
        p.fingers(side, 36.0, thumb=20.0)


def broom_pose(p: Poser, frame: float, clip: str) -> None:
    """Riding a broom (`broom_ride`: leaning into the flight, a slight
    sway) or hovering (`broom_hover`: upright, a slow bob of the rider and
    the broom together, looking round)."""
    from . import anim_spells as AS

    b = p.body
    s = b.height / 1.78
    t = frame / FPS
    hover = clip == "broom_hover"
    lean = BROOM_RIDE["lean"][clip]
    if hover:
        bob = 0.03 * s * math.sin(2 * math.pi * t / 3.0)  # one bob a loop (3 s)
        sway = 0.0
    else:
        bob = 0.006 * s * math.sin(2 * math.pi * t / 2.0)  # breathing and the air (2 s loop)
        sway = math.sin(2 * math.pi * t / 2.0)
    hip_z = _broom_hip_z(b) + bob
    p.loc("torso", (0.0, 0.0, hip_z - b.hip_joint[2]))
    p.rot("torso", Quaternion())
    p.rot("hips", Quaternion())
    p.rot("chest", _q((1, 0, 0), lean) @ _q((0, 1, 0), 1.2 * sway))
    # The shoulders a little forward and down, toward the hands on the handle.
    p.rot("shoulder.L", _q((0, 0, 1), -10.0) @ _q((0, 1, 0), 6.0))
    p.rot("shoulder.R", _q((0, 0, 1), 10.0) @ _q((0, 1, 0), -6.0))
    # The head up, looking ahead (hovering: looking round now and then).
    look = math.sin(2 * math.pi * t / 3.0 + 0.8) if hover else 0.2 * sway
    p.rot("neck", _q((1, 0, 0), -lean * 0.45))
    p.rot("head", _q((0, 0, 1), 9.0 * look) @ _q((1, 0, 0), -lean * 0.4))
    leg = float(np.linalg.norm(np.array(b.hip_joint) - np.array(b.ankle)))
    for side in (1, -1):
        name = p.side("foot_ik", side)
        ankle0 = p.rest_head[name]
        target = Vector((BROOM_RIDE["ankle_out"] * s * side, -b.hip_joint[1] + BROOM_RIDE["ankle_back"] * leg,
                         hip_z - BROOM_RIDE["ankle_drop"] * leg))
        q = _q((0, 0, 1), 4.0 * side) @ _q((1, 0, 0), BROOM_RIDE["toe_pitch"])
        r = p.rest_q[name]
        p.pb[name].location = r.inverted() @ (target - ankle0)
        p.pb[name].rotation_quaternion = r.inverted() @ q @ r
        p.touched.add(name)
        p.rot(p.side("toe_ik", side), Quaternion())
    for side in (1, -1):
        key = (b.id, round(b.height, 4), side)
        if key not in _BROOM_KNEES:
            _BROOM_KNEES[key] = _broom_knee(p, side, BROOM_RIDE["knee_out"] * s)
        AS.knee_twist(p, side, _BROOM_KNEES[key])
    broom_hands(p, broom_seat(p))


def broom_ride(p: Poser, frame: float) -> None:
    broom_pose(p, frame, "broom_ride")


def broom_hover(p: Poser, frame: float) -> None:
    broom_pose(p, frame, "broom_hover")


# Seated on a seat (character adoption, docs/art/character-adoption.md):
# the root is the seat's top (the client puts it there,
# castle/seats.ts) and the sit bones rest on it; thighs about level, shins
# upright, and the heels reach a floor SEAT_M below the seat at the body's
# maximum height (shorter characters' feet hang a little, as in real life).
# The hands rest in the lap, so nothing has to reach a desk or a table, and
# there is no IK against the world.
SEAT_M = 0.46


def seat_floor(b: Body) -> float:
    """The floor's height under the seat in the clip's space (the body at its
    default height): SEAT_M at the maximum height, so -SEAT_M * default / max."""
    return -SEAT_M * b.height / b.height_range[1]


def sit_height(b: Body) -> float:
    """Hip joint above the seat surface when seated: the body's own thigh
    radius at the hip (measured on each body: the wrapped female's high
    crotch would sink her into the seat if the crotch drop were used)."""
    return float(b.thigh_r)


def seated(p: Poser, t: float, k: float = 1.0, lean: float = 6.0, hands: bool = True, breathe: float = 1.0) -> None:
    """The seated pose blended from standing in front of the seat (k = 0,
    the feet on the floor of the default height) to sitting (k = 1)."""
    from . import anim_spells as AS

    b = p.body
    s = b.height / 1.78
    floor_k = seat_floor(b)
    floor_0 = -SEAT_M  # standing in front of the seat at the default height
    hip_z = sit_height(b)
    br = math.sin(2 * math.pi * t / 3.0) * breathe
    # Standing (k=0): the whole body down by the seat height, a step ahead.
    z0 = floor_0 - 0.006
    y0 = -0.16 * s
    z1 = hip_z - b.hip_joint[2]
    y1 = 0.0
    kz = ease(k)
    dz = z0 + (z1 - z0) * kz
    dy = y0 + (y1 - y0) * ease(min(1.0, k * 1.3))
    p.loc("torso", (0.0, dy, dz + 0.002 * br * k))
    bend = math.sin(math.pi * min(1.0, k)) * 22.0  # the hips fold forward on the way down
    p.rot("torso", _q((1, 0, 0), 6.0 * k + bend * 0.6))
    p.rot("hips", _q((1, 0, 0), -4.0 * k - bend * 0.2))
    p.rot("chest", _q((1, 0, 0), lean * k + bend * 0.5 - 1.0 * br * k))
    p.rot("neck", _q((1, 0, 0), -2.0 - 2.0 * k))
    p.rot("head", _q((1, 0, 0), -4.0 * k - bend * 0.4))
    # Legs: shins upright under the knees, the heels on the floor (at the
    # maximum height), the thigh from the hip joint to the knee.
    thigh = float(np.linalg.norm(np.array(b.hip_joint) - np.array(b.knee)))
    shin_h = b.knee[2]  # knee joint above the floor in the rest pose
    fl = floor_0 + (floor_k - floor_0) * kz
    knee_z = fl + shin_h
    hz = (b.hip_joint[2] + dz)
    dzk = min(abs(hz - knee_z), thigh * 0.98)
    ahead = math.sqrt(max(0.0, thigh * thigh - dzk * dzk)) * kz
    for side in (1, -1):
        hip_y = -b.hip_joint[1] + dy
        target_y = hip_y - ahead
        fwd = (-b.ankle[1]) - target_y
        p.foot(side, forward=(-y0) * (1.0 - kz) + fwd * kz, lift=fl, lateral=0.035 * s * side * kz, yaw=6.0 * side)
        # Always keyed (from frame 0), so no frame inherits a later twist.
        ang = 0.0
        if k > 0.25:
            bucket = round(min(k, 1.0), 1)
            ang = AS.seated_knee_angle(p, side, key=("seated", p.body.id, side, bucket)) * min(1.0, (k - 0.25) / 0.35)
        AS.knee_twist(p, side, ang)
    if hands:
        lap_hands(p, k, hip_z, ahead)


def lap_hands(p: Poser, k: float, hip_z: float, ahead: float, sides=(1, -1)) -> None:
    """Both hands resting in the lap, on the thighs near the knees' middle,
    palms down, one a little over the other (blended from hanging by k)."""
    from . import anim_spells as AS

    b = p.body
    s = b.height / 1.78
    kk = ease(min(1.0, max(0.0, (k - 0.2) / 0.8)))
    for side in sides:
        rest = AS.posed_wrist(p, side) if kk < 1.0 else None
        lap = Vector((side * 0.075 * s, -b.hip_joint[1] - 0.52 * ahead - 0.02 * s, hip_z + 0.105 * s + (0.012 * s if side < 0 else 0.0)))
        target = lap if rest is None else rest.lerp(lap, kk)
        AS.place_hand(p, side, target, (-0.25 * side, -0.95, -0.15), (0.0, 0.15, -1.0), (side * 0.9, 0.3, -0.3))
        p.fingers(side, 22.0 + 8.0 * kk, thumb=12.0)


def sit_down(p: Poser, frame: float) -> None:
    seated(p, frame / FPS, ease(frame / 36.0))


def sit(p: Poser, frame: float) -> None:
    seated(p, frame / FPS, 1.0)


@dataclass
class Clip:
    name: str
    frames: int  # last frame; loops repeat frame 0 at `frames`
    loop: bool
    fn: Callable[[Poser, float], None]
    speed: float = 0.0  # ground speed in m/s at scale 1 (locomotion)
    meta: dict = field(default_factory=dict)  # extra manifest fields (anim_spells.py: release times)


CLIPS = [
    Clip("idle", 120, True, idle),
    # Walk and run: Quaternius's Universal Animation Library, retargeted (charlib/ual.py).
    Clip("walk", UAL.frames("Walk_Loop", UAL.WALK_RATE), True, UAL.walk, WALK_SPEED, meta=UAL.clip_source("Walk_Loop", UAL.WALK_RATE)),
    Clip("run", UAL.frames("Sprint_Loop", UAL.RUN_RATE), True, UAL.run, RUN_SPEED, meta=UAL.clip_source("Sprint_Loop", UAL.RUN_RATE)),
    Clip("wave", 75, False, wave),
    Clip("bow", 60, False, bow),
    Clip("sit_down", 36, False, sit_down, meta={"root": "seat_top", "seat_m": SEAT_M}),
    Clip("sit", 90, True, sit, meta={"root": "seat_top", "seat_m": SEAT_M, "hands": "lap"}),
    Clip("floor_rest", 90, True, floor_rest, meta={"root": "floor", "hands": "lap"}),
    # Cross-legged on a floor seat (rugs, hearth steps; the root is its top).
    Clip("floor_sit", 180, True, floor_sit, meta={"root": "floor_seat_top", "hands": "lap"}),
    # On a broom (protocol v8 fly / hover; the root is the flyer's position, the
    # broom prop hangs from the hips bone: charlib/broom.py, the manifest's "broom").
    Clip("broom_ride", 60, True, broom_ride, meta={"root": "flyer", "hands": "broom_handle", "broom": "DEF-spine"}),
    Clip("broom_hover", 90, True, broom_hover, meta={"root": "flyer", "hands": "broom_handle", "broom": "DEF-spine"}),
]


def key_clip(rig: bpy.types.Object, body: Body, clip: Clip) -> bpy.types.Action:
    """Key every frame of a clip on the rig controls; returns the action."""
    p = Poser(rig, body)
    act = bpy.data.actions.new(f"{rig.name}_{clip.name}_ctrl")
    rig.animation_data_create()
    rig.animation_data.action = act
    for f in range(clip.frames + 1):
        p.reset()
        clip.fn(p, float(f % clip.frames) if clip.loop else float(f))
        p.key(f)
    return act


def pose_rig(rig: bpy.types.Object, body: Body, clip: Clip, frame: float) -> None:
    """Pose the rig statically (for test poses and renders)."""
    p = Poser(rig, body)
    p.reset()
    clip.fn(p, frame)


# The limbs' twist bones (Rigify's DEF-<limb>.001 halves). On some bodies
# (the wrapped female's legs) the rig's twist solution flips by half a turn
# for a few frames of a stride: DEF-shin.001 keys a 150-170 degree twist about
# its own axis while the foot under it counter-rotates, so the lower leg
# (and the trouser leg over it) wrings into a knot and the hem stretches
# toward the other foot. No limb twists that far, so a twist past
# TWIST_FLIP_DEG is taken off by half a turn and the child gets the
# opposite half turn: the child's own pose is unchanged.
TWIST_BONES = ("DEF-thigh.{s}.001", "DEF-shin.{s}.001", "DEF-upper_arm.{s}.001", "DEF-forearm.{s}.001")
TWIST_FLIP_DEG = 100.0


def _quat_curves(act: bpy.types.Action, bone: str):
    path = f'pose.bones["{bone}"].rotation_quaternion'
    curves = [None] * 4
    for fc in _fcurves(act):
        if fc.data_path == path and 0 <= fc.array_index < 4:
            curves[fc.array_index] = fc
    return curves if all(curves) else None


def _fcurves(act: bpy.types.Action):
    """An action's F-curves (Blender 4.4+ slotted actions keep them in layers)."""
    if getattr(act, "fcurves", None) is not None and len(act.fcurves):
        return list(act.fcurves)
    out = []
    for layer in getattr(act, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out.extend(bag.fcurves)
    return out


def unflip_twists(skel: bpy.types.Object, act: bpy.types.Action) -> int:
    """Takes half a turn off every keyed twist past TWIST_FLIP_DEG on the
    limbs' twist bones, compensating the child bone (see TWIST_BONES).
    Returns the number of keys changed."""
    half = Quaternion((0.0, 0.0, 1.0, 0.0))  # 180 degrees about the bone's own Y axis
    changed = 0
    for side in ("L", "R"):
        for pattern in TWIST_BONES:
            name = pattern.format(s=side)
            bone = skel.data.bones.get(name)
            if bone is None:
                continue
            pc = _quat_curves(act, name)
            if pc is None:
                continue
            kids = [c for c in bone.children if _quat_curves(act, c.name) is not None]
            for k in range(len(pc[0].keyframe_points)):
                q = Quaternion([pc[i].keyframe_points[k].co[1] for i in range(4)])
                twist = math.degrees(2.0 * math.atan2(q.y, q.w))
                twist = (twist + 180.0) % 360.0 - 180.0
                if abs(twist) <= TWIST_FLIP_DEG:
                    continue
                qn = q @ half
                for i in range(4):
                    pc[i].keyframe_points[k].co[1] = qn[i]
                for c in kids:
                    cc = _quat_curves(act, c.name)
                    if len(cc[0].keyframe_points) != len(pc[0].keyframe_points):
                        continue
                    # child pose = rel^-1 . R . rel . pose (rel: child's rest in the parent's frame)
                    rel = bone.matrix_local.inverted() @ c.matrix_local
                    qc = Quaternion([cc[i].keyframe_points[k].co[1] for i in range(4)])
                    m = rel.inverted() @ half.to_matrix().to_4x4() @ rel @ qc.to_matrix().to_4x4()
                    qcn = m.to_quaternion()
                    if qcn.dot(qc) < 0.0:
                        qcn.negate()
                    for i in range(4):
                        cc[i].keyframe_points[k].co[1] = qcn[i]
                changed += 1
            # Keep each curve on one hemisphere, key to key (no long-way interpolation).
            for curves in [pc] + [_quat_curves(act, c.name) for c in kids]:
                prev = None
                for k in range(len(curves[0].keyframe_points)):
                    q = Quaternion([curves[i].keyframe_points[k].co[1] for i in range(4)])
                    if prev is not None and q.dot(prev) < 0.0:
                        q.negate()
                        for i in range(4):
                            curves[i].keyframe_points[k].co[1] = q[i]
                    prev = q
            for curves in [pc] + [_quat_curves(act, c.name) for c in kids]:
                for fc in curves:
                    fc.update()
    return changed


def bake(rig: bpy.types.Object, skel: bpy.types.Object, body: Body, clips=None) -> dict[str, bpy.types.Action]:
    """Bake each clip onto the skeleton (which must be constrained to the rig)
    and store it as an NLA track named after the clip."""
    clips = CLIPS if clips is None else clips
    out = {}
    sc = bpy.context.scene
    sc.render.fps = FPS
    for clip in clips:
        key_clip(rig, body, clip)
        sc.frame_start, sc.frame_end = 0, clip.frames
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        bpy.context.view_layer.objects.active = skel
        skel.select_set(True)
        bpy.ops.object.mode_set(mode="POSE")
        if skel.animation_data:
            skel.animation_data.action = None
        bpy.ops.nla.bake(frame_start=0, frame_end=clip.frames, step=1, only_selected=False, visual_keying=True,
                         clear_constraints=False, clear_parents=False, use_current_action=False, bake_types={"POSE"})
        bpy.ops.object.mode_set(mode="OBJECT")
        act = skel.animation_data.action
        unflip_twists(skel, act)
        act.name = clip.name
        act.use_fake_user = True
        track = skel.animation_data.nla_tracks.new()
        track.name = clip.name
        strip = track.strips.new(clip.name, 0, act)
        strip.name = clip.name
        skel.animation_data.action = None
        out[clip.name] = act
    rig.animation_data.action = None
    return out


def clip_meta() -> dict:
    return {c.name: {"frames": c.frames, "fps": FPS, "loop": c.loop, **({"speed_mps": c.speed} if c.speed else {}), **c.meta}
            for c in CLIPS}


# The spell, duel and class clips (anim_spells.py adds them to
# CLIPS when it is imported).
from . import anim_spells  # noqa: E402, F401
