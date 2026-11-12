"""Clips: wand gestures, casting, dueling reactions, the class
and the professor (keyed on the Rigify controls like `anim.py`, which
registers them at its end so they are baked and exported with the bodies).

Every clip that holds a wand holds it in the right hand. The wand is not
part of the body: the client attaches `vfx/wand.glb` to `socket_hand.R`
(tools/blender/vfx). Here the wand only exists as geometry the right arm is
solved for: `Grip` describes where it sits in the rest-pose hand (the grip
point inside the fist and the shaft direction, a diagonal grip:
charlib/wand_grip.py), and `wand_pose` places the wand tip somewhere,
pointing some way, by solving the arm with a two-bone IK on the FK
controls, with the hand's roll about the shaft chosen so the wrist stays as
straight and untwisted on the forearm as the aim allows (`wand_hand`); the
fingers close round the handle (`grip_hand`). The gesture clips
trace the templates of `data/classes/gestures.json` with the wand tip, so
the professor demonstrates, and duelists telegraph, exactly the shapes the
players trace on the pad.

Clips (30 fps; names avoid the words the client maps to its built-in
animations, except `cast`, which is protocol v5's `Anim::Cast`):

* `gesture_<key>` for the nine gestures: a short wind-up from the wand-ready
  stance, the stroke (the template, scaled to about 60 cm across in a
  vertical plane in front of the right shoulder), a hold and the return.
  Meta: `gesture`, `stroke_s` [start, end] and `release_s` (= the stroke's
  end, when a spell leaves the wand). The client scales a gesture clip to
  a cast time or a demonstration's `duration_ms`.
* `cast` (0.9 s, `CAST_ANIM_MS`): a generic forward thrust, release at 0.43 s.
* Dueling: `duel_ready` (looping stance, wand on the opponent),
  `shield_raise` (up in 0.1 s, held 0.7 s, Scutara's numbers), `hit_react`,
  `knockback`, `stun_fall` (ends lying on the back), `stun_rise` (from that
  pose back to standing) and `disarmed` (the wand leaves the hand at
  `wand_drop_s`; the client hides it).
* `wand_light` (looping, still): the right arm holding a lit wand up and
  forward like a lantern, the hand closed round it. The client lays this
  arm and hand over idle, walk and run while a wand is held out (Lucerna)
  and takes the hand's grip from it whenever a wand is drawn.
* Class: `professor_stand` and `professor_demonstrate` (both looping),
  `desk_rest` and `desk_practice` (looping, seated like `anim.seated`: the
  root on the bench top, the hands in the lap; desk_practice is seated wand
  practice between lap and chest height; nothing reaches the desk), and
  `spectator_cheer` (looping claps and fist pumps).
"""

from __future__ import annotations

import json
import math
import os

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from . import skeleton as K
from . import wand_grip as WG
from .anim import FPS, Clip, Poser, ease, stand

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
GESTURES_PATH = os.path.join(REPO, "data", "classes", "gestures.json")

RIGHT, LEFT = -1, 1
# Wand geometry shared with tools/blender/vfx (the wand model): from the grip
# point inside the fist to the tip, and from the grip back to the butt.
WAND_TIP = 0.26
WAND_BUTT = 0.08
# Seated clips: bench top under the hips, desk top, and the desk's front
# edge ahead of the hips (the classroom builder uses the same numbers).
SEAT_HEIGHT = 0.46
DESK_HEIGHT = 0.76
DESK_FRONT = 0.22
# Pad units (-100..100) to metres for the gesture plane, and the stroke's
# speed in pad units per second (clamped to STROKE_S).
PAD_M = 0.0042
STROKE_SPEED = 450.0
STROKE_S = (0.35, 1.1)
WIND_S, HOLD_S, BACK_S = 0.25, 0.12, 0.35


def _qd(axis, deg: float) -> Quaternion:
    return Quaternion(Vector(axis).normalized(), math.radians(deg))


def smooth(t: float) -> float:
    """Ease in and out, clamped to 0..1."""
    return ease(t)


def window(t: float, a: float, b: float, c: float, d: float) -> float:
    """0 before a, rising to 1 at b, held until c, falling to 0 at d."""
    if t <= a or t >= d:
        return 0.0
    if t < b:
        return ease((t - a) / (b - a))
    if t <= c:
        return 1.0
    return 1.0 - ease((t - c) / (d - c))


def mix(a, b, k: float):
    return a + (b - a) * k


def basis_rot(a1: Vector, b1: Vector, a2: Vector, b2: Vector) -> Quaternion:
    """The rotation taking direction a1 to a2 that also turns b1 (its part
    across a1) toward b2 (its part across a2)."""

    def basis(a, b):
        x = a.normalized()
        y = b - x * b.dot(x)
        if y.length < 1e-6:
            y = x.orthogonal()
        y.normalize()
        return Matrix((x, y, x.cross(y))).transposed()

    return (basis(a2, b2) @ basis(a1, b1).transposed()).to_quaternion()


def _delta(pb) -> Quaternion:
    """A posed bone's rotation relative to its rest, in armature axes."""
    return pb.matrix.to_quaternion() @ pb.bone.matrix_local.to_quaternion().inverted()


def update() -> None:
    bpy.context.view_layer.update()


class Grip:
    """The wand in the rest-pose right hand (charlib/wand_grip.py): the
    grip point inside the fist, the shaft direction (diagonally across the
    palm, out between the thumb and the index finger), the hand's axis and
    the palm normal."""

    def __init__(self, body):
        f = WG.grip_frame(body)
        self.wrist = Vector(f.wrist)
        self.grip = Vector(f.grip)
        self.axis = Vector(f.axis)
        self.palm = Vector(f.palm)
        self.thumb = Vector(f.thumb)
        self.wand = Vector(f.wand)


def reach(p: Poser, side: int, wrist: Vector, pole: Vector, hand: Quaternion | None = None) -> None:
    """Two-bone IK on the FK arm: put the wrist at `wrist` (armature space),
    the elbow bending toward `pole`, and turn the hand by `hand` (its
    rotation from rest, armature axes) when given. The torso, chest and
    shoulder must already be posed (they move the shoulder)."""
    up, fo, ha = (p.side(n, side) for n in ("upper_arm_fk", "forearm_fk", "hand_fk"))
    update()
    pu = p.pb[up]
    d_par = _delta(pu.parent)
    inv = d_par.inverted()
    a = p.rest_head[fo] - p.rest_head[up]
    b = p.rest_head[ha] - p.rest_head[fo]
    e = p.elbow_axis[side]
    t = inv @ (Vector(wrist) - pu.head)
    # |a + R(e, f) b| = |t|: a . R(e, f) b = A cos f + B sin f + C.
    ae, eb = a.dot(e), e.dot(b)
    A = a.dot(b) - ae * eb
    B = a.dot(e.cross(b))
    C = ae * eb
    reach_max = (a + b).length * 0.995
    dist = min(max(t.length, abs(a.length - b.length) + 0.02), reach_max)
    rhs = (dist * dist - a.length_squared - b.length_squared) / 2.0 - C
    M = math.hypot(A, B)
    phi = math.atan2(B, A)
    k = math.acos(min(1.0, max(-1.0, rhs / M)))
    cands = [phi + k, phi - k]
    cands = [((f + math.pi) % (2 * math.pi)) - math.pi for f in cands]
    good = [f for f in cands if -0.05 <= f <= math.radians(165)]
    f = min(good, key=abs) if good else max(0.0, min(cands, key=abs))
    rf = Quaternion(e, f)
    w = a + rf @ b
    q = basis_rot(w, a, t, inv @ Vector(pole))
    p.rot(up, q)
    p.rot(fo, rf)
    if hand is not None:
        update()
        p.rot(ha, _delta(p.pb[ha].parent).inverted() @ hand)


def hand_rot(p: Poser, side: int, axis: Vector, palm: Vector) -> Quaternion:
    """The hand rotation that points the fingers along `axis` with the palm
    facing `palm` (armature space)."""
    hf = K.hand_frame(p.body)
    m = (lambda v: v) if side > 0 else K.mirror
    return basis_rot(Vector(m(hf.axis)), Vector(m(hf.palm_normal)), Vector(axis), Vector(palm))


def knee_twist(p: Poser, side: int, deg: float) -> None:
    """Turn an IK leg's knee about the hip-to-foot line (the `thigh_ik`
    control's own Y axis; mirrored per side). The rig's knee has a forward
    rest bend (`skeleton.KNEE_LEAD`), so every IK bend already goes forward
    and the twist only aims the knee (a few degrees in or out); a large
    twist winds the thigh's twist bones and pinches the knee."""
    name = p.side("thigh_ik", side)
    p.pb[name].rotation_quaternion = Quaternion((0.0, 1.0, 0.0), math.radians(deg * side))
    p.touched.add(name)


_KNEE_ANGLES: dict = {}


def seated_knee_angle(p: Poser, side: int, key=None) -> float:
    """The `knee_twist` that points this body's knee straight ahead (a
    little outside the hip) with its feet as placed now: searched once per
    `key` (default: body and side; the seated legs do not move), cached."""
    key = key or (p.body.id, side)
    if key not in _KNEE_ANGLES:
        name = p.side("thigh_ik", side)
        hip = p.pb[p.side("ORG-thigh", side)]
        knee = p.pb[p.side("ORG-shin", side)]
        best = None
        for deg in range(-45, 50, 5):  # about 0 with the forward rest bend (was 135-180)
            p.pb[name].rotation_quaternion = Quaternion((0.0, 1.0, 0.0), math.radians(deg * side))
            update()
            off = abs(knee.head.x - hip.head.x * 1.12) - 0.5 * (knee.head.z - hip.head.z)
            if best is None or off < best[0]:
                best = (off, deg)
        _KNEE_ANGLES[key] = float(best[1])
    return _KNEE_ANGLES[key]


def posed_wrist(p: Poser, side: int) -> Vector:
    """Where the wrist is in the pose set so far (e.g. `stand`'s hanging arm)."""
    update()
    return p.pb[p.side("hand_fk", side)].head.copy()


def place_hand(p: Poser, side: int, wrist, axis, palm, pole) -> None:
    reach(p, side, Vector(wrist), Vector(pole), hand_rot(p, side, Vector(axis), Vector(palm)))


# Choosing the hand's roll about the wand (wand_pose): candidates every
# ROLL_STEP degrees, then refined; the wrist's bend and its twist about the
# forearm from straight are what is kept small (twist counts more: the rig
# has no forearm twist bones, so a twisted wrist wrings the skin).
ROLL_STEP, ROLL_FINE = 15.0, 2.5
TWIST_WEIGHT = 2.0
# A mild preference for the palm facing in and down (toward the body's left
# and the floor), the way a wand hand rests: it keeps the choice of roll
# from flipping between equally good wrists from one frame to the next.
PALM_PREFERRED = Vector((0.7, 0.0, -0.7)).normalized()
PALM_WEIGHT = 0.4


def _swing_twist_deg(q: Quaternion, axis: Vector) -> tuple[float, float]:
    """A rotation's swing (degrees off `axis`'s direction) and twist about `axis` (degrees, signed)."""
    p = Vector((q.x, q.y, q.z)).dot(axis)
    twist = math.degrees(2.0 * math.atan2(p, q.w))
    twist = (twist + 180.0) % 360.0 - 180.0
    swing = math.degrees((q @ axis).angle(axis)) if axis.length > 0 else 0.0
    return swing, twist


class _Arm:
    """The right arm's rest geometry, for the analytic elbow of `wand_pose`."""

    def __init__(self, p: Poser):
        rh = p.rest_head
        up, fo, ha = (p.side(n, RIGHT) for n in ("upper_arm_fk", "forearm_fk", "hand_fk"))
        self.up = up
        self.a = (rh[fo] - rh[up]).length
        self.b = (rh[ha] - rh[fo]).length
        self.fore = (rh[ha] - rh[fo]).normalized()
        self.hinge = p.elbow_axis[RIGHT].copy()
        bone = p.pb[ha].bone
        self.hand = (bone.tail_local - bone.head_local).normalized()

    def forearm(self, shoulder: Vector, wrist: Vector, pole: Vector) -> tuple[Quaternion, bool]:
        """The forearm's rotation from rest when the wrist is at `wrist` (the
        elbow bent toward `pole`, as `reach` solves it), and whether it reaches."""
        t = wrist - shoulder
        d = t.length
        tn = t / d if d > 1e-6 else Vector((0.0, 0.0, -1.0))
        ok = d <= (self.a + self.b) * 0.995
        d = min(max(d, abs(self.a - self.b) + 0.02), (self.a + self.b) * 0.995)
        x = (self.a * self.a - self.b * self.b + d * d) / (2.0 * d)
        h = math.sqrt(max(self.a * self.a - x * x, 0.0))
        u = pole - tn * pole.dot(tn)
        u = u.normalized() if u.length > 1e-6 else tn.orthogonal().normalized()
        elbow = shoulder + tn * x + u * h
        fore = (shoulder + tn * d - elbow).normalized()
        hinge = u.cross(tn).normalized()
        return basis_rot(self.fore, self.hinge, fore, hinge), ok


def wand_hand(p: Poser, tip: Vector, d: Vector, pole: Vector) -> Quaternion:
    """The hand's rotation that points the wand along `d` with its tip at
    `tip` and the wrist as straight as the arm allows: the roll about the
    shaft that keeps the wrist's bend and twist off the forearm smallest."""
    g = Grip(p.body)
    arm = _Arm(p)
    update()
    shoulder = p.pb[arm.up].head.copy()
    r0 = g.wand.rotation_difference(d)
    grip = tip - d * WAND_TIP

    def score(roll: float) -> tuple[float, Quaternion]:
        r = Quaternion(d, math.radians(roll)) @ r0
        fq, ok = arm.forearm(shoulder, grip - r @ (g.grip - g.wrist), pole)
        _, twist = _swing_twist_deg(fq.inverted() @ r, arm.fore)
        bend = math.degrees((fq.inverted() @ r @ arm.hand).angle(arm.hand))
        palm = math.degrees((r @ g.palm).angle(PALM_PREFERRED))
        cost = bend * bend + TWIST_WEIGHT * twist * twist + PALM_WEIGHT * palm * palm + (0.0 if ok else 1e6)
        return cost, r

    best = min((score(k * ROLL_STEP) + (k * ROLL_STEP,) for k in range(int(360 / ROLL_STEP))), key=lambda c: c[0])
    roll = best[2]
    fine = min((score(roll + k * ROLL_FINE) + (roll + k * ROLL_FINE,) for k in range(-6, 7)), key=lambda c: c[0])
    return fine[1]


def grip_hand(p: Poser, side: int = RIGHT) -> None:
    """The right hand closed round the wand's handle (wand_grip.grip_curls)."""
    if side != RIGHT:
        raise ValueError("the wand is held in the right hand")
    f = WG.grip_frame(p.body)
    curls = WG.grip_curls(p.body)
    for name, chain in WG.finger_chains(p.body).items():
        axes = WG.curl_axes(chain, f.palm)
        ang = curls[name]
        for j in range(3):
            q = _qd(axes[j], ang[j])
            if name == "thumb" and j == 0:
                q = _qd(WG.thumb_oppose_axis(f), curls["thumb_oppose"][0]) @ q
            p.rot(p.side(f"{name}.0{j + 1}", side), q)


def wand_pose(p: Poser, tip, direction, palm=None, pole=(-0.55, 0.35, -0.75)) -> None:
    """Right hand holding the wand with its tip at `tip`, pointing along
    `direction` (armature space), the elbow bent toward `pole`. The hand's
    roll about the shaft is chosen for the most natural wrist (`wand_hand`);
    `palm` is accepted for older callers and ignored."""
    g = Grip(p.body)
    d = Vector(direction).normalized()
    tip = Vector(tip)
    pole = Vector(pole)
    r = wand_hand(p, tip, d, pole)
    grip = tip - d * WAND_TIP
    reach(p, RIGHT, grip - r @ (g.grip - g.wrist), pole, r)
    grip_hand(p, RIGHT)


# ----------------------------------------------------------------------------
# Gestures
# ----------------------------------------------------------------------------


def load_gestures(path: str = GESTURES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["gestures"]


def polyline_length(pts) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def stroke_seconds(points) -> float:
    lo, hi = STROKE_S
    return min(hi, max(lo, polyline_length(points) / STROKE_SPEED))


def along(points, u: float) -> tuple[float, float]:
    """The point at fraction u (0..1) of a polyline's length."""
    total = polyline_length(points)
    s = min(max(u, 0.0), 1.0) * total
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        if s <= seg or b is points[-1]:
            k = 0.0 if seg < 1e-9 else min(s / seg, 1.0)
            return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
        s -= seg
    return tuple(points[-1])


def stroke_progress(u: float) -> float:
    """Arc-length progress over the stroke: a gentle start and a quick,
    flicking finish (half eased, half linear)."""
    u = min(max(u, 0.0), 1.0)
    return 0.45 * (u * u * (3 - 2 * u)) + 0.55 * u


class Plane:
    """The gesture plane of a body: pad (x right, y up, -100..100) to wand
    tip positions in front of the right shoulder, and the pivot behind the
    tip the wand points away from (so the wrist does part of the work)."""

    def __init__(self, body):
        s = body.height / 1.78
        self.s = s
        # The character faces -Y; its right is -X.
        # Far enough out that the arm is half extended (the elbow about 60-90
        # degrees), and the wand aimed up from a pivot below the shoulder: a
        # diagonal grip on a straight wrist points the shaft that much above
        # the forearm, so the wrist barely bends to aim.
        self.centre = Vector((-0.2 * s, -0.78 * s, 1.42 * s))
        self.pivot = Vector((-0.16 * s, 0.05 * s, 0.98 * s))
        self.scale = PAD_M * s

    def tip(self, x: float, y: float, depth: float = 0.0) -> Vector:
        return self.centre + Vector((-x * self.scale, -depth * self.s, y * self.scale))

    def direction(self, tip: Vector) -> Vector:
        return (tip - self.pivot).normalized()


READY = (10.0, -40.0, -0.16)  # pad x, y and depth (m, toward the body) of the wand-ready tip


def body_follow(p: Poser, x: float, y: float, k: float = 1.0) -> None:
    """Chest, head and hips lean toward the wand tip at pad (x, y)."""
    xn, yn = x / 100.0, y / 100.0
    p.rot("chest", _qd((0, 0, 1), -7.0 * xn * k) @ _qd((1, 0, 0), 2.0 - 3.0 * yn * k))
    p.rot("hips", _qd((0, 0, 1), -3.0 * xn * k))
    p.rot("neck", _qd((1, 0, 0), -2.0))
    p.rot("head", _qd((0, 0, 1), -10.0 * xn * k) @ _qd((1, 0, 0), -7.0 * yn * k))


def ready_stance(p: Poser, t: float, breathe: float = 1.0) -> None:
    """The relaxed stand with the left arm eased forward (the wand arm is
    solved afterwards)."""
    stand(p, t, breathe)
    p.arm(LEFT, abduct=-31.0, flex=10.0, twist=-6.0, elbow=30.0, wrist=4.0)
    p.fingers(LEFT, 24.0, thumb=12.0)


def gesture_clip(g: dict) -> Clip:
    pts = [tuple(map(float, q)) for q in g["points"]]
    stroke = stroke_seconds(pts)
    t_stroke0 = WIND_S
    t_stroke1 = WIND_S + stroke
    total = t_stroke1 + HOLD_S + BACK_S
    frames = int(math.ceil(total * FPS))

    def fn(p: Poser, frame: float) -> None:
        t = frame / FPS
        plane = Plane(p.body)
        ready_stance(p, t, breathe=0.3)
        rx, ry, rd = READY
        if t < t_stroke0:
            k = smooth(t / t_stroke0)
            x, y, depth = mix(rx, pts[0][0], k), mix(ry, pts[0][1], k), mix(rd, 0.0, k)
        elif t < t_stroke1:
            x, y = along(pts, stroke_progress((t - t_stroke0) / stroke))
            depth = 0.0
        else:
            k = smooth((t - t_stroke1 - HOLD_S) / BACK_S)
            x, y, depth = mix(pts[-1][0], rx, k), mix(pts[-1][1], ry, k), mix(0.0, rd, k)
        # A little anticipation dip before the stroke and a lean into it.
        active = window(t, 0.0, t_stroke0, t_stroke1 + HOLD_S, total)
        body_follow(p, x, y, active)
        p.loc("torso", (0.0, -0.02 * active, -0.012 * active))
        tip = plane.tip(x, y, depth)
        wand_pose(p, tip, plane.direction(tip))

    meta = {"gesture": g["key"], "stroke_s": [round(t_stroke0, 3), round(t_stroke1, 3)], "release_s": round(t_stroke1, 3)}
    return Clip(f"gesture_{g['key']}", frames, False, fn, meta=meta)


# ----------------------------------------------------------------------------
# Casting and dueling
# ----------------------------------------------------------------------------


# The cast's release: the wand thrust out at arm's length, aimed a little
# above the arm (the diagonal grip on a straight wrist carries it so).
OUT_DIR = Vector((0.05, -1.0, 0.36)).normalized()


def cast(p: Poser, frame: float) -> None:
    """Generic forward cast: draw the wand back by the ear, thrust it at
    arm's length (release at frame 13), hold, recover."""
    t = frame / FPS
    plane = Plane(p.body)
    ready_stance(p, t, breathe=0.0)
    s = plane.s
    rx, ry, rd = READY
    ready_tip = plane.tip(rx, ry, rd)
    back_tip = Vector((-0.27 * s, -0.27 * s, 1.68 * s))
    out_tip = Vector((-0.12 * s, -0.95 * s, 1.36 * s))
    if frame < 8:
        k = smooth(frame / 8)
        tip = ready_tip.lerp(back_tip, k)
        d = ((plane.direction(ready_tip)).lerp(Vector((0.1, -0.5, 0.86)).normalized(), k)).normalized()
    elif frame < 13:
        k = ((frame - 8) / 5) ** 1.6
        tip = back_tip.lerp(out_tip, k)
        d = Vector((0.1, -0.5, 0.86)).normalized().lerp(OUT_DIR, k).normalized()
    else:
        k = smooth((frame - 17) / 10)
        tip = out_tip.lerp(ready_tip, k)
        d = OUT_DIR.lerp(plane.direction(ready_tip), k).normalized()
    lunge = window(frame, 6, 13, 16, 27)
    p.loc("torso", (0.0, -0.06 * lunge * s, -0.02 * lunge))
    p.rot("chest", _qd((0, 0, 1), 9.0 * lunge) @ _qd((1, 0, 0), 2.0 + 5.0 * lunge))
    p.rot("head", _qd((0, 0, 1), -6.0 * lunge))
    p.foot(RIGHT, forward=0.1 * lunge * s, yaw=-6.0, lateral=-0.012)
    wand_pose(p, tip, d)


def duel_stance(p: Poser, t: float, k: float = 1.0, breathe: float = 1.0) -> None:
    """Side-on fighting stance: right foot forward, knees soft, the chest
    turned so the wand shoulder leads, the left arm up for balance. `k`
    blends from the plain stand (0) to the stance (1)."""
    s = p.body.height / 1.78
    stand(p, t, breathe * (1 - k))
    w = 2 * math.pi * t / 2.0
    bob = 0.006 * math.sin(w) * breathe
    p.loc("torso", (0.0, 0.0, (-0.05 * s + bob) * k))
    p.rot("torso", _qd((0, 0, 1), 14.0 * k))
    p.rot("hips", _qd((0, 0, 1), 6.0 * k))
    p.rot("chest", _qd((0, 0, 1), 8.0 * k) @ _qd((1, 0, 0), (4.0 + 1.0 * math.sin(w)) * k))
    p.rot("neck", _qd((0, 0, 1), -10.0 * k))
    p.rot("head", _qd((0, 0, 1), -10.0 * k) @ _qd((1, 0, 0), -3.0 * k))
    p.foot(RIGHT, forward=0.19 * s * k, lateral=-0.06 * s * k, yaw=-10.0 * k)
    p.foot(LEFT, forward=-0.17 * s * k, lateral=0.05 * s * k, yaw=34.0 * k)
    p.arm(LEFT, abduct=-36.0 + 58.0 * k, flex=3.0 - 13.0 * k, twist=-30.0 * k, elbow=14.0 + 56.0 * k, wrist=6.0 - 16.0 * k)
    p.fingers(LEFT, 18.0 - 6.0 * k, thumb=10.0, spread=6.0 * k)


def duel_aim(p: Poser, k: float = 1.0, dx: float = 0.0, dz: float = 0.0) -> tuple[Vector, Vector]:
    """Wand tip and direction of the stance: pointed at the opponent at
    chest height, blended from the ready tip by `k`."""
    plane = Plane(p.body)
    s = plane.s
    rx, ry, rd = READY
    ready_tip = plane.tip(rx, ry, rd)
    aim_tip = Vector((-0.08 * s + dx, -0.94 * s, 1.36 * s + dz))
    tip = ready_tip.lerp(aim_tip, k)
    d = plane.direction(ready_tip).lerp(Vector((0.08, -1.0, 0.38)).normalized(), k).normalized()
    return tip, d


def duel_ready(p: Poser, frame: float) -> None:
    t = frame / FPS
    duel_stance(p, t)
    w = 2 * math.pi * t / 2.0
    tip, d = duel_aim(p, 1.0, 0.01 * math.sin(w), 0.012 * math.sin(w + 0.7))
    wand_pose(p, tip, d)


def shield_raise(p: Poser, frame: float) -> None:
    """Scutara: the wand snaps up across the face in 3 frames (100 ms), the
    off hand braced behind it, held for 0.7 s, then lowered."""
    t = frame / FPS
    duel_stance(p, t, breathe=0.0)
    s = p.body.height / 1.78
    k = window(frame, 0, 3, 24, 30)
    tip0, d0 = duel_aim(p)
    tip1 = Vector((-0.02 * s, -0.5 * s, 1.84 * s))
    d1 = Vector((-0.35, -0.45, 0.82)).normalized()
    p.rot("chest", _qd((0, 0, 1), 4.0) @ _qd((1, 0, 0), 4.0 - 6.0 * k))
    p.loc("torso", (0.0, 0.03 * k * s, -0.05 * s))
    wand_pose(p, tip0.lerp(tip1, k), d0.lerp(d1, k).normalized(), palm=(0.5, -0.5, -0.7))
    # Off hand: palm out in front of the chest, as if pushing on the shield.
    if k > 0:
        wrist = Vector((0.1 * s, -0.42 * s, 1.36 * s))
        rest_w = posed_wrist(p, LEFT)
        cur = rest_w.lerp(wrist, k)
        place_hand(p, LEFT, cur, (0.15, -0.2, 1.0), (0.05, -1.0, 0.1), (0.7, 0.4, -0.6))
        p.fingers(LEFT, 8.0, thumb=4.0, spread=10.0 * k)


def hit_react(p: Poser, frame: float) -> None:
    """A flinch: struck in the chest at frame 2, recovered by 0.6 s."""
    t = frame / FPS
    duel_stance(p, t, breathe=0.0)
    k = window(frame, 0, 2, 5, 18)
    s = p.body.height / 1.78
    p.loc("torso", (0.0, 0.06 * k * s, -0.05 * s - 0.02 * k))
    p.rot("chest", _qd((0, 0, 1), 8.0 - 6.0 * k) @ _qd((1, 0, 0), 4.0 - 16.0 * k))
    p.rot("head", _qd((0, 0, 1), -10.0 + 8.0 * k) @ _qd((1, 0, 0), -3.0 - 14.0 * k))
    p.arm(LEFT, abduct=22.0 + 18.0 * k, flex=-10.0 - 10.0 * k, twist=-30.0, elbow=70.0 - 20.0 * k, wrist=-10.0)
    tip, d = duel_aim(p, 1.0 - 0.6 * k)
    wand_pose(p, tip + Vector((0.0, 0.1 * k, 0.12 * k)), d)


def knockback(p: Poser, frame: float) -> None:
    """Thrown back by a heavy hit: arched back, a stumbling step with the
    right foot, arms flung, and back to the stance by 1.1 s."""
    t = frame / FPS
    duel_stance(p, t, breathe=0.0)
    s = p.body.height / 1.78
    k = window(frame, 0, 3, 9, 30)
    step = window(frame, 2, 9, 14, 26)
    p.loc("torso", (0.0, (0.16 * k + 0.08 * step) * s, -0.05 * s - 0.05 * k))
    p.rot("torso", _qd((0, 0, 1), 14.0 - 10.0 * k))
    p.rot("chest", _qd((1, 0, 0), 4.0 - 28.0 * k) @ _qd((0, 0, 1), 8.0 * (1 - k)))
    p.rot("neck", _qd((1, 0, 0), -10.0 * k))
    p.rot("head", _qd((1, 0, 0), -3.0 - 16.0 * k))
    lift = 0.08 * window(frame, 3, 7, 8, 12) * s
    p.foot(RIGHT, forward=(0.19 - 0.42 * step) * s, lift=lift, lateral=-0.06 * s, yaw=-10.0,
           pitch=-10.0 * window(frame, 9, 11, 12, 16))
    p.arm(LEFT, abduct=22.0 + 40.0 * k, flex=-10.0 - 30.0 * k, twist=-30.0, elbow=70.0 - 50.0 * k, wrist=-10.0)
    p.fingers(LEFT, 12.0 - 8.0 * k, thumb=10.0, spread=12.0 * k)
    tip, d = duel_aim(p, 1.0 - k)
    fling = Vector((-0.25 * s, 0.25 * s, 0.28 * s)) * k
    wand_pose(p, tip + fling, (d + Vector((-0.4, 0.6, 0.6)) * k).normalized())


def _lying(p: Poser, k: float, t: float) -> None:
    """Blend from the duel stance's hips (k = 0) to lying flat on the back
    (k = 1): the torso pivot behind the feet, legs straight, arms splayed."""
    s = p.body.height / 1.78
    pivot_h = p.rest_head["torso"].z
    back = 0.42 * s
    p.loc("torso", (0.0, back * k, (-0.05 * s) * (1 - k) + (0.16 * s - pivot_h) * k))
    p.rot("torso", _qd((0, 0, 1), 14.0 * (1 - k)) @ _qd((1, 0, 0), -86.0 * k))
    p.rot("hips", _qd((1, 0, 0), 6.0 * k))
    p.rot("chest", _qd((1, 0, 0), 4.0 * (1 - k) - 4.0 * k))
    p.rot("neck", _qd((1, 0, 0), 10.0 * k))
    p.rot("head", _qd((0, 0, 1), 18.0 * k) @ _qd((1, 0, 0), 4.0 * k))


def _lying_feet(p: Poser, w: float, yaws=((RIGHT, 12.0), (LEFT, 12.0)), reach_k: float = 0.985, pitch: float = -75.0) -> None:
    """Blend the feet already placed (by `p.foot`) toward lying with straight
    legs from the body's own hip joints (character adoption: the wrapped
    bodies' legs differ from the spec's; a fixed 0.92 * height leg drove the
    knees of the long-legged female below the floor). The ankle goes where
    the leg of this body reaches from the posed hip joint (a slight knee
    bend), the heel on the floor, the toes up. Call after the torso is posed."""
    if w <= 0.0:
        return
    update()
    s = p.body.height / 1.78
    for side, yaw in yaws:
        name = p.side("foot_ik", side)
        thigh = p.side("ORG-thigh", side)
        hip_rest = p.rest_head[thigh]
        ankle0 = p.rest_head[name]
        L = (hip_rest - ankle0).length
        hip = p.pb[thigh].head
        za = p.body.ankle[2] + 0.025 * s  # the calf (behind the leg line) rests on, not in, the floor
        dz = hip.z - za
        reach = reach_k * L
        d = math.sqrt(max(0.0, reach * reach - dz * dz))
        target = Vector((hip.x + side * 0.03 * s, hip.y - d, za))
        q = _qd((0, 0, 1), yaw * side) @ _qd((1, 0, 0), pitch)
        r = p.rest_q[name]
        loc1 = r.inverted() @ (target - ankle0)
        rot1 = r.inverted() @ q @ r
        pb = p.pb[name]
        pb.location = pb.location.lerp(loc1, w)
        pb.rotation_quaternion = pb.rotation_quaternion.slerp(rot1, w)
        p.touched.add(name)


def knee_up_twist(p: Poser, side: int) -> float:
    """The `knee_twist` that lifts this side's knee highest in the pose set so
    far (getting up from lying on the back: the knees must rise, not fold
    through the floor). Coarse search, then refined."""
    name = p.side("thigh_ik", side)
    knee = p.pb[p.side("ORG-shin", side)]

    def height(deg):
        p.pb[name].rotation_quaternion = Quaternion((0.0, 1.0, 0.0), math.radians(deg * side))
        update()
        return knee.head.z

    best = max(range(-180, 180, 20), key=height)
    best = max(range(best - 15, best + 16, 5), key=height)
    p.touched.add(name)
    return float(best)


def stun_fall(p: Poser, frame: float) -> None:
    """Stunned: the knees buckle and the body topples backward, landing
    flat at 0.9 s and lying still (the last frame holds)."""
    t = frame / FPS
    duel_stance(p, t, breathe=0.0)
    s = p.body.height / 1.78
    buckle = window(frame, 0, 6, 40, 41)
    k = ease(max(0.0, (frame - 4) / 22.0)) ** 1.5
    _lying(p, k, t)
    p.loc("torso", (0.0, 0.42 * s * k, mix(-0.05 * s - 0.06 * s * buckle, 0.16 * s - p.rest_head["torso"].z, k)))
    leg = 0.92 * s
    for side, lat in ((RIGHT, -0.12), (LEFT, 0.12)):
        f0 = 0.19 * s if side == RIGHT else -0.17 * s
        fwd = mix(f0, leg - 0.42 * s, k)
        p.foot(side, forward=fwd, lift=0.07 * s * k, pitch=-75.0 * k, lateral=lat * s * k,
               yaw=(-10.0 if side == RIGHT else 34.0) * (1 - k) + side * 12.0 * k)
    # The body's own legs (per-body leg lengths): straight from the hips.
    _lying_feet(p, k)
    # Arms fly up, then fall out to the sides.
    up = window(frame, 2, 8, 12, 26)
    p.arm(LEFT, abduct=22.0 + 70.0 * up + 30.0 * k, flex=-10.0 + 50.0 * up - 20.0 * k, twist=-30.0 * (1 - k),
          elbow=78.0 - 60.0 * max(up, k), wrist=-10.0 + 10.0 * k)
    p.fingers(LEFT, 25.0, thumb=12.0)
    p.arm(RIGHT, abduct=10.0 + 70.0 * up + 30.0 * k, flex=40.0 + 40.0 * up - 30.0 * k, twist=0.0,
          elbow=40.0 - 30.0 * max(up, k), wrist=0.0)
    p.fingers(RIGHT, 60.0, thumb=30.0)


def stun_rise(p: Poser, frame: float) -> None:
    """From lying on the back (`stun_fall`'s last pose): knees up, sit up
    on the hands, gather the feet, push up to the stance."""
    t = frame / FPS
    s = p.body.height / 1.78
    duel_stance(p, t, breathe=0.0)
    knees = ease(frame / 14.0)
    sit = ease((frame - 8) / 14.0)
    squat = ease((frame - 22) / 12.0)
    rise = ease((frame - 32) / 16.0)
    lie_k = 1.0 - sit
    pivot_h = p.rest_head["torso"].z
    # Torso: lying -> sitting up (leaning on the hands) -> over the feet -> standing.
    ang = mix(-86.0, -20.0, sit)
    ang = mix(ang, 28.0, squat)
    ang = mix(ang, 0.0, rise)
    back = mix(0.42 * s, 0.22 * s, sit)
    back = mix(back, 0.0, squat)
    z = mix(0.16 * s - pivot_h, 0.2 * s - pivot_h, sit)
    z = mix(z, 0.42 * s - pivot_h, squat)
    z = mix(z, -0.05 * s, rise)
    p.loc("torso", (0.0, back, z))
    p.rot("torso", _qd((0, 0, 1), 14.0 * rise) @ _qd((1, 0, 0), ang))
    p.rot("chest", _qd((1, 0, 0), -4.0 * lie_k + 10.0 * squat * (1 - rise) + 4.0 * rise))
    p.rot("head", _qd((0, 0, 1), 18.0 * lie_k - 10.0 * rise) @ _qd((1, 0, 0), 4.0 * lie_k + 10.0 * sit * (1 - rise)))
    leg = 0.92 * s
    for side, lat, f_end, yaw_end in ((RIGHT, -0.12, 0.19, -10.0), (LEFT, 0.12, -0.17, 34.0)):
        lying_fwd = leg - 0.42 * s
        fwd = mix(lying_fwd, 0.55 * s - 0.42 * s, knees)  # knees drawn up: feet toward the hips
        fwd = mix(fwd, 0.04 * s * side, squat)
        fwd = mix(fwd, f_end * s, rise)
        pitch = mix(-75.0, -10.0, knees)
        pitch = mix(pitch, 0.0, squat)
        lat_v = mix(lat * s, lat * s * 1.3, squat)
        lat_v = mix(lat_v, (-0.06 if side == RIGHT else 0.05) * s, rise)
        # In the deep squat the folded leg lets the ankle sink below its flat
        # height on the wrapped bodies (about 2-4 cm): hold the feet up there.
        squat_lift = 0.035 * s * squat * (1.0 - rise)
        p.foot(side, forward=fwd, lift=mix(0.07 * s, 0.0, knees) + squat_lift, pitch=pitch, lateral=lat_v,
               yaw=mix(side * 12.0, yaw_end, rise))
    # Start from `stun_fall`'s last pose exactly (the body's own legs).
    _lying_feet(p, 1.0 - knees)
    # Knees up (never through the floor) while the feet are drawn in, back to
    # no twist by the time the body stands.
    for side in (LEFT, RIGHT):
        w = min(1.0, knees * 3.0) * (1.0 - squat)
        ang = knee_up_twist(p, side) * w if w > 0.0 else 0.0
        knee_twist(p, side, ang)
    # Arms: from `stun_fall`'s splay, hands pushing on the floor behind while
    # sitting up, forward over the knees in the squat, then the stance.
    gather = ease(frame / 12.0)
    for side, lie in ((LEFT, (52.0, -30.0, 0.0, 10.0, 0.0)), (RIGHT, (40.0, 10.0, 0.0, 10.0, 0.0))):
        v = [mix(a, b, gather) for a, b in zip(lie, (-16.0, -30.0, 0.0, 10.0, -40.0))]
        v = [mix(a, b, squat) for a, b in zip(v, (-36.0, 30.0, 0.0, 40.0, 0.0))]
        if side == LEFT:
            v = [mix(a, b, rise) for a, b in zip(v, (22.0, -10.0, -30.0, 70.0, -10.0))]
        p.arm(side, abduct=v[0], flex=v[1], twist=v[2], elbow=v[3], wrist=v[4])
        p.fingers(side, 60.0 if side == RIGHT else 15.0, thumb=25.0)
    # While sitting up the hands push on the floor behind the hips, placed on
    # it (IK to the body's own posed hips: the FK arms alone went through the
    # floor on the wrapped bodies).
    wp = gather * (1.0 - squat)
    if wp > 0.0:
        update()
        for side in (LEFT, RIGHT):
            hip = p.pb[p.side("ORG-thigh", side)].head
            rest = posed_wrist(p, side)
            floor = Vector((side * 0.21 * s, hip.y + 0.16 * s, 0.07 * s))
            target = rest.lerp(floor, wp)
            target.z = max(target.z, 0.07 * s)
            place_hand(p, side, target, (side * 0.45, 0.88, 0.05), (0.0, 0.0, -1.0), (side * 0.6, 0.7, 0.4))
            p.fingers(side, 10.0, thumb=10.0)
    if rise > 0.0:
        # From wherever the hand hangs now to the stance's aim.
        update()
        g = Grip(p.body)
        hand = p.pb["hand_fk.R"]
        start = hand.head + _delta(hand) @ (g.grip - g.wrist + g.wand * WAND_TIP)
        start_d = (_delta(hand) @ g.wand).normalized()
        tip, d = duel_aim(p, 1.0)
        wand_pose(p, start.lerp(tip, rise), start_d.lerp(d, rise).normalized())


def disarmed(p: Poser, frame: float) -> None:
    """The wand is torn from the hand (frame 3, `wand_drop_s`): the hand
    flies open and back, the body recoils, then looks at the empty hand
    and shakes it out."""
    t = frame / FPS
    duel_stance(p, t, breathe=0.0)
    s = p.body.height / 1.78
    jolt = window(frame, 1, 4, 7, 18)
    look = window(frame, 10, 15, 22, 30)
    p.loc("torso", (0.0, 0.05 * jolt * s, -0.05 * s - 0.015 * jolt))
    p.rot("chest", _qd((0, 0, 1), 8.0 - 10.0 * jolt - 6.0 * look) @ _qd((1, 0, 0), 4.0 - 10.0 * jolt + 6.0 * look))
    p.rot("head", _qd((0, 0, 1), -10.0 - 14.0 * look) @ _qd((1, 0, 0), -3.0 + 16.0 * look - 8.0 * jolt))
    tip, d = duel_aim(p, 1.0 - jolt)
    hand_at = Vector((-0.3 * s, -0.25 * s, 1.55 * s))
    looked = Vector((-0.12 * s, -0.42 * s, 1.18 * s))
    shake = 0.025 * math.sin(2 * math.pi * 5.0 * t) * window(frame, 18, 21, 25, 29)
    g = Grip(p.body)
    target = tip.lerp(hand_at, jolt).lerp(looked + Vector((shake, 0.0, 0.0)), look)
    direction = d.lerp(Vector((-0.5, 0.6, 0.62)).normalized(), jolt).lerp(Vector((0.4, -0.3, 0.86)).normalized(), look)
    palm = Vector((0.75, 0.0, -0.65)).lerp(Vector((0.0, -0.5, 0.86)), look)
    r = basis_rot(g.wand, g.palm, direction.normalized(), palm)
    grip = target - direction.normalized() * WAND_TIP
    reach(p, RIGHT, grip - r @ (g.grip - g.wrist), Vector((-0.55, 0.35, -0.75)), r)
    open_k = window(frame, 2, 4, 30, 31)
    if open_k < 1e-3:
        grip_hand(p, RIGHT)
    else:
        p.fingers(RIGHT, mix(68.0, 6.0, open_k), thumb=mix(34.0, 4.0, open_k), spread=14.0 * jolt)
    p.arm(LEFT, abduct=22.0 + 20.0 * jolt, flex=-10.0 - 10.0 * jolt, twist=-30.0, elbow=70.0 - 30.0 * jolt, wrist=-10.0)


def wand_light(p: Poser, frame: float) -> None:
    """A lit wand held up like a lantern (Lucerna): the right hand a little
    forward of the chest, the wand rising forward from it, the hand closed
    round the handle. Still: the client lays this arm over idle and walk."""
    stand(p, 0.0, breathe=0.0)
    s = p.body.height / 1.78
    d = Vector((0.12, -0.55, 0.83)).normalized()
    grip = Vector((-0.2 * s, -0.3 * s, 1.13 * s))
    wand_pose(p, grip + d * WAND_TIP, d, palm=(0.6, 0.2, -0.75), pole=(-0.7, 0.45, -0.55))


# ----------------------------------------------------------------------------
# Class: professor, desks, spectators
# ----------------------------------------------------------------------------


def professor_stand(p: Poser, frame: float) -> None:
    """At the front of the class: upright, both hands holding the wand
    across the waist, rocking up on the toes, looking over the rows."""
    t = frame / FPS
    s = p.body.height / 1.78
    stand(p, t)
    w = 2 * math.pi * t / 4.0
    rock = 0.5 - 0.5 * math.cos(2 * w)
    p.loc("torso", (0.0, -0.01 * rock, -0.004 + 0.012 * rock * s))
    for side in (LEFT, RIGHT):
        p.foot(side, forward=0.0, lateral=0.03 * s * side, yaw=10.0 * side, pitch=10.0 * rock)
    p.rot("chest", _qd((1, 0, 0), -2.0) @ _qd((0, 0, 1), 3.0 * math.sin(w)))
    p.rot("head", _qd((0, 0, 1), 22.0 * math.sin(w)) @ _qd((1, 0, 0), -6.0 + 3.0 * math.sin(2 * w + 0.5)))
    tip = Vector((0.16 * s, -0.24 * s, 1.0 * s))
    wand_pose(p, tip, Vector((0.95, -0.1, 0.25)), palm=(0.0, 0.3, -0.95), pole=(-0.6, 0.6, -0.4))
    place_hand(p, LEFT, Vector((0.07 * s, -0.24 * s, 0.98 * s)), (-0.4, -0.2, -0.9), (-0.9, 0.0, 0.3), (0.6, 0.6, -0.4))
    p.fingers(LEFT, 40.0, thumb=20.0)


def professor_demonstrate(p: Poser, frame: float) -> None:
    """Explaining to the class: the wand held up for everyone to see,
    the free hand opening toward the rows, turning from side to side."""
    t = frame / FPS
    s = p.body.height / 1.78
    stand(p, t)
    w = 2 * math.pi * t / 3.0
    sweep = math.sin(w)
    p.rot("chest", _qd((0, 0, 1), 12.0 * sweep) @ _qd((1, 0, 0), -3.0))
    p.rot("hips", _qd((0, 0, 1), 4.0 * sweep))
    p.rot("head", _qd((0, 0, 1), 14.0 * sweep) @ _qd((1, 0, 0), -8.0 + 4.0 * math.sin(3 * w)))
    p.loc("torso", (0.0, 0.0, -0.004 + 0.008 * math.sin(2 * w)))
    bob = 0.03 * math.sin(2 * w + 0.4)
    tip = Vector((-0.32 * s, -0.36 * s, (1.86 + 0.0) * s + bob))
    wand_pose(p, tip, Vector((-0.2, -0.25, 0.95)), palm=(0.9, -0.2, -0.3), pole=(-0.9, 0.3, -0.3))
    open_k = 0.5 + 0.5 * math.sin(w + 1.2)
    place_hand(p, LEFT, Vector((0.3 * s, -0.34 * s, 1.18 * s + 0.08 * open_k)), (0.55, -0.6, 0.3), (0.2, -0.4, 0.9),
               (0.7, 0.5, -0.5))
    p.fingers(LEFT, 10.0, thumb=6.0, spread=12.0)


def desk_rest(p: Poser, frame: float) -> None:
    """Seated in class (the root on the bench top, as every seated clip),
    the hands resting in the lap with the wand lying across them; looking
    round now and then. Nothing reaches the desk (character adoption)."""
    from .anim import lap_hands, seated, sit_height

    t = frame / FPS
    s = p.body.height / 1.78
    seated(p, t, 1.0, lean=8.0, hands=False)
    look = math.sin(2 * math.pi * t / 3.0)
    p.rot("head", _qd((0, 0, 1), 6.0 * look) @ _qd((1, 0, 0), -8.0))
    hip_z = sit_height(p.body)
    ahead = _seated_ahead(p)
    lap_hands(p, 1.0, hip_z, ahead, sides=(LEFT,))
    # The wand hand rests on the right thigh, the wand pointing ahead and
    # across over the lap.
    tip = Vector((0.07 * s, -p.body.hip_joint[1] - 0.52 * ahead - 0.24 * s, hip_z + 0.16 * s))
    wand_pose(p, tip, Vector((0.45, -0.88, 0.12)), palm=(0.25, 0.1, -0.96), pole=(-0.9, 0.3, -0.3))


def desk_practice(p: Poser, frame: float) -> None:
    """Seated wand practice (the clip id class data uses): small sweep and
    flick strokes with the wand hand between lap and chest height, the other
    hand resting in the lap. Nothing reaches the desk (character adoption)."""
    from .anim import lap_hands, seated, sit_height

    t = frame / FPS
    s = p.body.height / 1.78
    seated(p, t, 1.0, lean=4.0, hands=False)
    hip_z = sit_height(p.body)
    ahead = _seated_ahead(p)
    lap_hands(p, 1.0, hip_z, ahead, sides=(LEFT,))
    u = (t % 2.0) / 2.0
    # A small sweep (down and across) then a flick up, then rest.
    if u < 0.55:
        k = stroke_progress(u / 0.55)
        x, y = mix(-50.0, 40.0, k), -40.0 * math.sin(math.pi * k)
    elif u < 0.7:
        k = ease((u - 0.55) / 0.15)
        x, y = 40.0 + 10.0 * k, 50.0 * k
    else:
        k = ease((u - 0.7) / 0.3)
        x, y = mix(50.0, -50.0, k), mix(50.0, 0.0, k)
    # Between the lap (y = -50: about 0.12 m over the thighs) and the chest
    # (y = +50: about 0.4 m above the seat), a little to the right, in front.
    centre = Vector((-0.08 * s, -p.body.hip_joint[1] - 0.42 * s, hip_z + 0.27 * s))
    tip = centre + Vector((-x * 0.0018 * s, 0.0, y * 0.003 * s))
    pivot = centre + Vector((0.0, 0.42 * s, -0.12 * s))
    p.rot("head", _qd((0, 0, 1), -4.0 * x / 50.0) @ _qd((1, 0, 0), -14.0))
    wand_pose(p, tip, (tip - pivot).normalized(), pole=(-0.8, 0.5, -0.2))


def _seated_ahead(p: Poser) -> float:
    """How far the knees are ahead of the hip joints in `anim.seated` (k = 1)."""
    from .anim import seat_floor, sit_height

    b = p.body
    thigh = float(np.linalg.norm(np.array(b.hip_joint) - np.array(b.knee)))
    knee_z = seat_floor(b) + b.knee[2]
    dz = min(abs(sit_height(b) - knee_z), thigh * 0.98)
    return math.sqrt(max(0.0, thigh * thigh - dz * dz))


def spectator_cheer(p: Poser, frame: float) -> None:
    """Watching a duel: four claps, then two fist pumps with a bounce."""
    t = frame / FPS
    s = p.body.height / 1.78
    stand(p, t)
    u = t % 3.0
    bounce = abs(math.sin(math.pi * u / 0.75)) if u >= 1.5 else 0.0
    p.loc("torso", (0.0, 0.0, -0.004 - 0.03 * s * (1 - bounce) * (1.0 if u >= 1.5 else 0.0)))
    p.rot("chest", _qd((1, 0, 0), -2.0))
    p.rot("head", _qd((1, 0, 0), -4.0) @ _qd((0, 0, 1), 4.0 * math.sin(2 * math.pi * t / 3.0)))
    for side in (LEFT, RIGHT):
        p.foot(side, forward=0.0, lateral=0.03 * s * side, yaw=8.0 * side, pitch=8.0 * (1.0 - bounce) if u >= 1.5 else 0.0)
    if u < 1.5:
        # Claps: the hands meet every 0.375 s in front of the chest.
        gap = 0.5 - 0.5 * math.cos(2 * math.pi * u / 0.375)
        enter = ease(u / 0.2) if u < 0.2 else (1.0 - ease((u - 1.3) / 0.2) if u > 1.3 else 1.0)
        for side in (LEFT, RIGHT):
            rest = posed_wrist(p, side)
            clap = Vector((side * (0.035 + 0.11 * gap) * s, -0.34 * s, 1.2 * s))
            place_hand(p, side, rest.lerp(clap, enter), (side * -0.2, -0.6, 0.78), (-side, 0.0, 0.0),
                       (side * 0.8, 0.5, -0.4))
            p.fingers(side, 6.0, thumb=4.0)
    else:
        v = u - 1.5
        pump = abs(math.sin(math.pi * v / 0.75))
        rest_r = posed_wrist(p, RIGHT)
        enter = ease(v / 0.2) * (1.0 - ease((v - 1.3) / 0.2))
        up = Vector((-0.24 * s, -0.12 * s, (1.62 + 0.28 * pump) * s))
        place_hand(p, RIGHT, rest_r.lerp(up, enter), (0.0, -0.1, 1.0), (0.3, -0.9, 0.0), (-0.8, 0.4, -0.3))
        p.fingers(RIGHT, 85.0 * enter + 18.0 * (1 - enter), thumb=40.0)
        p.arm(LEFT, abduct=-30.0, flex=14.0, elbow=60.0 * enter + 14.0, wrist=6.0)
        p.fingers(LEFT, 60.0, thumb=30.0)


# ----------------------------------------------------------------------------


def clips() -> list[Clip]:
    out = [gesture_clip(g) for g in load_gestures()]
    out += [
        Clip("cast", 27, False, cast, meta={"release_s": round(13 / FPS, 3)}),
        Clip("duel_ready", 60, True, duel_ready),
        Clip("shield_raise", 30, False, shield_raise, meta={"raised_s": round(3 / FPS, 3), "lowered_s": round(24 / FPS, 3)}),
        Clip("hit_react", 18, False, hit_react),
        Clip("knockback", 33, False, knockback),
        Clip("stun_fall", 40, False, stun_fall),
        Clip("stun_rise", 48, False, stun_rise),
        Clip("disarmed", 30, False, disarmed, meta={"wand_drop_s": round(3 / FPS, 3)}),
        Clip("wand_light", 30, True, wand_light, meta={"hands": "wand_right", "bones": "right_arm"}),
        Clip("professor_stand", 120, True, professor_stand),
        Clip("professor_demonstrate", 90, True, professor_demonstrate),
        Clip("desk_rest", 90, True, desk_rest, meta={"root": "seat_top", "seat_m": SEAT_HEIGHT, "hands": "lap"}),
        Clip("desk_practice", 60, True, desk_practice, meta={"root": "seat_top", "seat_m": SEAT_HEIGHT, "hands": "lap_and_wand"}),
        Clip("spectator_cheer", 90, True, spectator_cheer),
    ]
    return out


CLIPS = clips()

# Register with anim.CLIPS (baked and exported with the bodies), once,
# whichever of the two modules is imported first.
from . import anim as _anim  # noqa: E402

if not any(c.name == CLIPS[0].name for c in _anim.CLIPS):
    _anim.CLIPS += CLIPS
