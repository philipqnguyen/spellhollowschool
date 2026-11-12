"""Walk and run from Quaternius's Universal Animation Library (CC0).

The source is `ual/ual1_walk_sprint.json` (extract_ual.py: the raw keys of
UAL1's in-place `Walk_Loop` and `Sprint_Loop` and the rest transforms of the
bones read here). Each clip function retargets one instant of the source
onto our Rigify controls through the `Poser`, like every other clip, so the
bake, the twist fix and the knee checks treat them alike:

* **Torso**: the source pelvis's movement from its rest, scaled by the hip
  height ratio, moves `torso`; the pelvis, upper spine, neck and head copy
  the source's rotations from rest (`hips`, `chest`, `neck`, `head`), both
  skeletons standing straight at rest. Our `hips` control turns about the
  torso pivot 25 cm above the hip joints, the source pelvis about its hip
  joints, so the torso then moves to put our hip joints where the source's
  are (`hip_shift`). The source looks at the ground
  (the face 15 degrees down walking, 23 running; ours should look
  forward), so the head's mean pitch over the loop is taken off, half
  its bob kept (`HEAD_BOB`), and the neck takes half the lift
  (`level_head`): the face looks level, within about 2 degrees.
* **Arms** (FK): the source's T-pose and our A-pose differ, so each arm bone
  (upper arm, forearm, hand) first turns its source rest direction onto ours
  (the shortest turn); then every bone points where the source bone points.
  The clavicles copy `CLAVICLE` of the source's rotation from rest (the
  sprint shrugs each shoulder up and back about 45 degrees, which our
  narrower shoulders and their weights do not take well). In the run the
  arms pump close to the body, swinging `RUN_ARM_FORWARD_DEG`
  further forward than the source's (the whole arm turned, the elbow bend
  kept): each arm bone keeps
  `RUN_ARM_SPREAD` of its lean out of the chest's midplane (`gather`), the
  chest keeps `RUN_CHEST_TWIST` of the sprint's large turn and roll
  (`calm`), and a fist that would pass too close to the waist or hips
  turns out at the elbow (`clear_hand`).
* **Legs** (IK): each foot control gets the source foot's pitch from rest
  (heel and toe lift; not its yaw and roll, `pitch_only`) and the source
  ankle's position relative to its hip joints, scaled by the hip height
  ratio, then is lifted
  or lowered so its lowest sole point is as high as the source's (scaled):
  a planted source foot is a planted foot here whatever the foot lengths.
  The source's ankles never move sideways, so neither do ours: each foot
  swings along the line of its own footfalls. Rigify's IK legs bend the
  knees from their forward rest bend (`skeleton.KNEE_LEAD`), so the knees
  always bend forward, and each thigh twists so the knee points straight
  ahead (`aim_knees`). The toes copy the source ball's pitch relative to
  the foot. Where a planted leg cannot reach its foot (the sprint lands on
  a straight front leg) the hips come down just enough. The sprint's right leg folds less than its left, so the run's right
  leg is its left mirrored half a loop later (`mirror_legs`).
* **Female gait** (required): the source's feet stay a man's
  hip width apart, too wide for a woman's walk and run, so body_b's ankle
  goals come `NARROW_M` toward the midline in both clips, like Vanai's
  female narrowing layer (4 cm a foot there too): the feet stand, swing and
  land close to one line under the body (a step width of 16 cm becomes
  8 cm). Only the foot goals move sideways (height, forward position and
  rotation kept); the IK legs follow with the knees still bending forward.
  body_a keeps the source's spacing.

The walk plays `WALK_RATE` times faster than the source, so its planted
foot moves about 1.0 m/s against the game's 3.0 m/s walk (the source walks
about 0.7 m/s; the feet slide, less than they did; a longer stride pushed
the legs through the robes); the run is the source's sprint at its own
rate. The bake keys every frame at 30 fps; the client scales the playback
with the ground speed.
"""

from __future__ import annotations

import json
import math
import os

import bpy
from mathutils import Matrix, Quaternion, Vector

SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ual", "ual1_walk_sprint.json")
FPS = 30
# A faster walk with less foot slide (1.25 before; 25 frames).
WALK_RATE = 1.6
RUN_RATE = 1.0
# body_b's feet toward the midline (metres, each foot), on the floor and in the
# swing alike: a swinging foot stays on the line of its own footfalls.
NARROW_M = {"body_b": 0.04}
# The sprint's right leg folds less than its left (the ankle rises 0.40 m
# behind against 0.53 m) and runs a frame late: the run's right leg is the
# left one mirrored half a loop later (`mirror_legs`).
MIRRORED_LEG = ("thigh", "calf", "foot", "ball", "ball_leaf")
# The sprint swings the upper arms 25 to 43 degrees out from the body (and
# the forearms in across it); ours pump close to the torso: the run keeps this share of each arm bone's lean out of the chest's
# midplane (`gather`; the walk's 14 to 20 are kept).
# (0.3 first; second pass: the elbows a little further from the body.)
RUN_ARM_SPREAD = 0.45
# The run's arms swing this much further forward (degrees about the chest's
# left-right axis, the whole arm turned so the elbow bend is kept; second
# pass: they stayed too far back).
RUN_ARM_FORWARD_DEG = 12.0
# The sprint also turns the chest 48 degrees each way and rolls it 24 (the
# walk: 7 and 4); with the arms in close that swings a shoulder over the
# midline and the fist into the waist, so the run keeps this share of the
# chest's yaw and roll (`calm`; its forward lean is kept).
RUN_CHEST_TWIST = 0.5
# With the arms in, a wrist or knuckle passes at least this far outside the
# waist and hips (`clear_hand`).
HAND_CLEAR_M = 0.06
# How much of the source clavicles' rotation the shoulders take.
CLAVICLE = 0.5
# The head looks ahead: of the source head's pitch about its loop mean (its
# bob) HEAD_BOB is kept; the neck takes NECK_SHARE of the head's lift.
HEAD_BOB = 0.5
NECK_SHARE = 0.5
# Finger curl (degrees) and thumb while walking and running (the source's fingers are not read).
CURL = {"walk": (24.0, 14.0), "run": (48.0, 22.0)}
# A source foot this close to the floor (metres, scaled) is planted: the hips
# come down if its leg cannot reach it (reach_drop).
PLANTED_M = 0.02
# Of the leg's full length (thigh + shin), what a planted leg may reach.
REACH = 0.995

# glTF (+Y up, faces +Z) to Blender (+Z up, faces -Y): (x, y, z) -> (x, -z, y).
_G2B = Quaternion((1.0, 0.0, 0.0), math.radians(90.0))


def _sides():
    return ((1, "L", "l"), (-1, "R", "r"))


class Source:
    """The extracted clips, posed by forward kinematics in Blender axes."""

    def __init__(self, path: str = SOURCE):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.meta = data["source"]
        self.nodes = data["nodes"]
        self.clips = data["clips"]
        self.rest = self._world({})

    def duration(self, clip: str) -> float:
        return self.clips[clip]["times"][-1]

    def _local(self, name: str, chans: dict) -> Matrix:
        n = self.nodes[name]
        c = chans.get(name, {})
        t = Vector(c.get("translation", n["translation"]))
        x, y, z, w = c.get("rotation", n["rotation"])
        return Matrix.LocRotScale(t, Quaternion((w, x, y, z)).normalized(), Vector(n["scale"]))

    def _world(self, chans: dict) -> dict[str, tuple[Vector, Quaternion]]:
        conv = _G2B.to_matrix().to_4x4()
        mats: dict[str, Matrix] = {}

        def world(name):
            if name not in mats:
                parent = self.nodes[name]["parent"]
                mats[name] = (world(parent) if parent else Matrix.Identity(4)) @ self._local(name, chans)
            return mats[name]

        out = {}
        for name in self.nodes:
            m = conv @ world(name)
            out[name] = (m.translation.copy(), m.to_quaternion().normalized())
        return out

    def pose(self, clip: str, t: float) -> dict[str, tuple[Vector, Quaternion]]:
        """World positions and rotations at `t` seconds (wrapped into the loop)."""
        c = self.clips[clip]
        times = c["times"]
        t = t % times[-1]
        i = min(max(0, int(t * FPS)), len(times) - 2)
        while i > 0 and times[i] > t:
            i -= 1
        while i < len(times) - 2 and times[i + 1] < t:
            i += 1
        u = (t - times[i]) / (times[i + 1] - times[i])
        chans = {}
        for name, ch in c["channels"].items():
            d = {}
            if "translation" in ch:
                d["translation"] = Vector(ch["translation"][i]).lerp(Vector(ch["translation"][i + 1]), u)
            if "rotation" in ch:
                (x0, y0, z0, w0), (x1, y1, z1, w1) = ch["rotation"][i], ch["rotation"][i + 1]
                q = Quaternion((w0, x0, y0, z0)).slerp(Quaternion((w1, x1, y1, z1)), u)
                d["rotation"] = (q.x, q.y, q.z, q.w)
            chans[name] = d
        return self._world(chans)


_SOURCE: Source | None = None


def source() -> Source:
    global _SOURCE
    if _SOURCE is None:
        _SOURCE = Source()
    return _SOURCE


def delta(w, rest, name: str) -> Quaternion:
    """A source bone's world rotation from its rest."""
    return w[name][1] @ rest[name][1].inverted()


FACE = Vector((0.0, -1.0, 0.0))  # the face's forward at rest (the character faces -Y)


def pitch(q: Quaternion) -> float:
    """The pitch (radians, + up) of the face's forward turned by q."""
    return math.asin(max(-1.0, min(1.0, (q @ FACE).z)))


def tilt(q: Quaternion, by: float) -> Quaternion:
    """q with the face's forward pitched up by `by` radians (about the
    horizontal axis across it: yaw and roll kept)."""
    f = q @ FACE
    h = Vector((f.x, f.y, 0.0))
    if h.length < 1e-6:
        return q
    h.normalize()
    p = pitch(q) + by
    return f.rotation_difference(h * math.cos(p) + Vector((0.0, 0.0, math.sin(p)))) @ q


_MEAN_PITCH: dict[tuple[str, str], float] = {}


def mean_pitch(clip: str, bone: str) -> float:
    """The source bone's mean face pitch over the loop (radians)."""
    key = (clip, bone)
    if key not in _MEAN_PITCH:
        src = source()
        times = src.clips[clip]["times"][:-1]
        _MEAN_PITCH[key] = sum(pitch(delta(src.pose(clip, t), src.rest, bone)) for t in times) / len(times)
    return _MEAN_PITCH[key]


def level_head(clip: str, w, rest) -> tuple[Quaternion, Quaternion]:
    """The neck's and head's world rotations from the source with the face
    looking ahead (the source's head always faces downward): the
    head's mean pitch over the loop is taken off and its bob kept at
    HEAD_BOB; the neck takes NECK_SHARE of the same lift, so the head is not
    perched on a bowed neck."""
    head = delta(w, rest, "Head")
    lift = -pitch(head) + (pitch(head) - mean_pitch(clip, "Head")) * HEAD_BOB
    return tilt(delta(w, rest, "neck_01"), lift * NECK_SHARE), tilt(head, lift)


def sole_points(p, side: int) -> dict[str, Vector]:
    """Our foot's sole at rest (on the floor): heel, ball, toe tip."""
    f = p.foot_pts[side]
    return {"heel": Vector((f["heel_back"].x, f["heel_back"].y, 0.0)), "ball": Vector((f["ball"].x, f["ball"].y, 0.0)),
            "tip": Vector((f["tip"].x, f["tip"].y, 0.0))}


def source_sole(rest, s: str) -> dict[str, Vector]:
    """The source foot's sole at rest: under its ankle (a little behind), ball and toe end."""
    ankle, ball, tip = rest[f"foot_{s}"][0], rest[f"ball_{s}"][0], rest[f"ball_leaf_{s}"][0]
    heel = Vector((ankle.x, ankle.y + 0.25 * (ankle - ball).length, 0.0))
    return {"heel": heel, "ball": Vector((ball.x, ball.y, 0.0)), "tip": Vector((tip.x, tip.y, 0.0))}


def lowest(points: dict[str, Vector], ankle0: Vector, toe0: Vector, ankle: Vector, q_foot: Quaternion,
           q_toe: Quaternion) -> float:
    """Height of the lowest sole point of a foot moved from rest (ankle0) to
    `ankle` turned by q_foot, the toe tip turned further by q_toe about the
    toe joint (toe0, at rest)."""
    out = math.inf
    for k, v in points.items():
        if k == "tip":
            v = toe0 + q_toe @ (v - toe0)
        out = min(out, (ankle + q_foot @ (v - ankle0)).z)
    return out


def ratio(p, rest) -> float:
    """Our hip height over the source's."""
    return p.rest_head["ORG-thigh.L"].z / rest["thigh_l"][0].z


def set_world(p, name: str, q_world: Quaternion) -> None:
    """Turns a control so its bone's rotation from rest, in armature axes, is
    q_world, whatever its parents do (they must be evaluated)."""
    pb = p.pb[name]
    m = Matrix.LocRotScale(pb.matrix.translation, q_world @ p.rest_q[name], None)
    local = p.rig.convert_space(pose_bone=pb, matrix=m, from_space="POSE", to_space="LOCAL")
    pb.rotation_quaternion = local.to_quaternion()
    p.touched.add(name)


def _update() -> None:
    bpy.context.view_layer.update()


def pitch_only(q: Quaternion) -> Quaternion:
    """q's turn about the left-right (X) axis alone: a foot keeps its heel
    and toe lift but not the sprint's yaw and roll (up to 13 degrees, the
    toes turning out while the foot swings under the body and in at
    landing)."""
    t = Quaternion((q.w, q.x, 0.0, 0.0))
    return t.normalized() if t.magnitude > 1e-9 else Quaternion()


def feet(p, w, rest, k: float) -> dict[int, tuple[Vector, Quaternion, Quaternion, bool]]:
    """Each foot control's ankle position, rotation, the toe's rotation
    relative to the foot, and whether the source foot is on the floor.

    Forward and back the ankle sits where the source's does relative to its
    hip joints at rest, scaled, so a trailing
    leg reaches as far behind the hips as the source's (the source's ankle
    stands 3.7 cm behind its hip joint at rest, ours under it). Sideways it
    keeps the source's offset from rest (the source's ankles do not move
    sideways), with body_b's `NARROW_M` in, on the floor and in the swing
    alike: a swinging foot stays on the line of its own footfalls."""
    out = {}
    narrow = NARROW_M.get(p.body.id, 0.0)
    for side, S, s in _sides():
        src_q = delta(w, rest, f"foot_{s}")
        src_toe = src_q.inverted() @ delta(w, rest, f"ball_{s}")
        q_foot = pitch_only(src_q)
        q_toe = pitch_only(src_toe)
        ankle0 = p.rest_head[f"foot_ik.{S}"]
        ankle = ankle0 + (w[f"foot_{s}"][0] - rest[f"foot_{s}"][0]) * k
        ankle.y = p.rest_head[f"ORG-thigh.{S}"].y + (w[f"foot_{s}"][0].y - rest[f"thigh_{s}"][0].y) * k
        ankle.x -= narrow * side
        src_low = lowest(source_sole(rest, s), rest[f"foot_{s}"][0], rest[f"ball_{s}"][0], w[f"foot_{s}"][0], src_q, src_toe)
        # Never below the floor (the source's heel is estimated, so its heel strike reads a few millimetres under it).
        target = max(0.0, src_low * k)
        ankle.z += target - lowest(sole_points(p, side), ankle0, p.rest_head[f"ORG-toe.{S}"], ankle, q_foot, q_toe)
        out[side] = (ankle, q_foot, q_toe, target < PLANTED_M)
    return out


def mirror_q(q: Quaternion) -> Quaternion:
    """A world rotation reflected through the midplane (x -> -x)."""
    return Quaternion((q.w, q.x, -q.y, -q.z))


def mirror_legs(w, h, rest) -> dict:
    """The pose `w` with the right leg replaced by the left leg of `h` (half
    a loop later) reflected through the midplane: a symmetric gait whose
    legs both move like the source's left."""
    out = dict(w)
    for bone in MIRRORED_LEG:
        pos, _ = h[f"{bone}_l"]
        d = mirror_q(delta(h, rest, f"{bone}_l"))
        out[f"{bone}_r"] = (Vector((-pos.x, pos.y, pos.z)), d @ rest[f"{bone}_r"][1])
    return out


def hip_shift(p, w, rest, k: float) -> Vector:
    """How far the torso must move so our hip joints' midpoint is where the
    source's is (scaled). The source pelvis turns about its hip joints; our
    `hips` control turns about the torso pivot 25 cm above them, so copying
    the sprint pelvis's 23-degree forward tilt left our hip joints 8 cm
    behind and 4 cm above the source's (and the trailing foot that much
    short of the source's reach behind them). The hips must be evaluated."""
    ours = (p.pb["ORG-thigh.L"].head + p.pb["ORG-thigh.R"].head) * 0.5
    rest_mid = (p.rest_head["ORG-thigh.L"] + p.rest_head["ORG-thigh.R"]) * 0.5
    src = (w["thigh_l"][0] + w["thigh_r"][0]) * 0.5 - (rest["thigh_l"][0] + rest["thigh_r"][0]) * 0.5
    return rest_mid + src * k - ours


FORWARD = Vector((0.0, -1.0, 0.0))


def knee_turn(hip: Vector, knee: Vector, ankle: Vector) -> float:
    """The signed angle (radians) about the hip-ankle line from the knee's
    direction to straight ahead (the direction of travel)."""
    a = ankle - hip
    if a.length < 1e-6:
        return 0.0
    a.normalize()
    u = (knee - hip) - a * (knee - hip).dot(a)
    d = FORWARD - a * FORWARD.dot(a)
    if u.length < 1e-4 or d.length < 1e-4:
        return 0.0
    return math.atan2(a.dot(u.cross(d)), u.dot(d))


def aim_knees(p) -> None:
    """Twists each IK thigh (`thigh_ik`, about its own axis) so the knee
    bends straight ahead, in the direction of travel. Rigify's IK without a
    pole bends the knee in the rest bend's plane carried by the hips, which
    leans out (the rest knee sits 3 mm outside the hip-ankle line, 2 cm
    ahead of it) and turns with the pelvis: the sprint's knees went up to
    4 to 5 cm out and the folded shin with them. The ankle goals are
    unchanged."""
    for _, S, _ in _sides():
        pb = p.pb[f"thigh_ik.{S}"]
        for _ in range(4):
            _update()
            phi = knee_turn(p.pb[f"ORG-thigh.{S}"].head, p.pb[f"ORG-shin.{S}"].head, p.pb[f"ORG-foot.{S}"].head)
            if abs(phi) < math.radians(0.2):
                break
            pb.rotation_quaternion = pb.rotation_quaternion @ Quaternion((0.0, 1.0, 0.0), phi)
        p.touched.add(f"thigh_ik.{S}")


def reach_drop(p, targets) -> float:
    """How far the hips must come down so each planted foot's leg reaches its
    ankle (the source's legs are a little longer for their hip height: its
    sprint lands on a straight front leg). The hips must be evaluated."""
    drop = 0.0
    for side, S, _ in _sides():
        ankle, _, _, planted = targets[side]
        if not planted:
            continue
        bones = p.rig.data.bones
        reach = (bones[f"ORG-thigh.{S}"].length + bones[f"ORG-shin.{S}"].length) * REACH
        v = ankle - p.pb[f"ORG-thigh.{S}"].head
        across = v.x * v.x + v.y * v.y
        if across < reach * reach and v.length > reach:
            drop = max(drop, -math.sqrt(reach * reach - across) - v.z)
    return drop


def side_of(S: str) -> int:
    return 1 if S == "L" else -1


def chest_axes(p) -> tuple[Vector, Vector]:
    """The evaluated chest's left (+X) and forward (-Y) axes."""
    pb = p.pb["ORG-spine.003"]
    q = pb.matrix.to_quaternion() @ pb.bone.matrix_local.to_quaternion().inverted()
    return q @ Vector((1.0, 0.0, 0.0)), q @ Vector((0.0, -1.0, 0.0))


def abduction(p, d: Vector, side: int) -> float:
    """How far (radians) the arm direction d leans out of the chest's
    midplane, + away from the body."""
    left, _ = chest_axes(p)
    return math.asin(max(-1.0, min(1.0, d.dot(left * side))))


def gather(p, d: Vector, side: int, spread: float) -> Quaternion:
    """The turn bringing an arm bone pointing along d toward the chest's
    midplane: its lean out of it (or, for a forearm reaching across the
    body, in past it) scaled by `spread`, its swing in the plane (forward
    and back) kept."""
    left, _ = chest_axes(p)
    out = left * side
    a = abduction(p, d, side)
    flat = d - out * d.dot(out)
    if flat.length < 1e-6:
        return Quaternion()
    flat.normalize()
    a2 = a * spread
    return d.rotation_difference(out * math.sin(a2) + flat * math.cos(a2))


def trunk_half_width(b, z: float) -> float:
    """The body's half width at height z (rest), from its measurements: the
    ribs at the chest, the waist, the hips and below."""
    pts = ((b.hip_z, b.hip_w), (b.waist_z, b.waist_w), (b.chest_z, b.rib_w))
    if z <= pts[0][0]:
        return pts[0][1]
    for (z0, w0), (z1, w1) in zip(pts, pts[1:]):
        if z <= z1:
            return w0 + (w1 - w0) * (z - z0) / (z1 - z0)
    return pts[-1][1]


def clear_hand(p, side: int, S: str) -> None:
    """Turns the forearm (and the hand with it) out at the elbow just enough
    that the forearm's middle, the wrist and the knuckles pass
    HAND_CLEAR_M outside the waist and hips (the sprint rolls the chest from
    side to side, so with the arms in close a fist would otherwise brush the
    waist). Arms already clear are left alone."""
    hips = p.pb["ORG-spine"]
    root0 = hips.bone.head_local
    for _ in range(8):
        _update()
        qh = hips.matrix.to_quaternion() @ hips.bone.matrix_local.to_quaternion().inverted()
        out = qh @ Vector((side, 0.0, 0.0))
        root = hips.head
        elbow = p.pb[f"ORG-forearm.{S}"].head
        hand = p.pb[f"ORG-hand.{S}"]
        need, worst = 0.0, None
        for pt in (elbow.lerp(hand.head, 0.5), hand.head, hand.tail):
            short = trunk_half_width(p.body, pt.z - root.z + root0.z) + HAND_CLEAR_M - (pt - root).dot(out)
            if short > need:
                need, worst = short, pt
        if worst is None:
            return
        r = worst - elbow
        axis = r.cross(out)
        if axis.length < 1e-6:
            return
        axis.normalize()  # turning about it swings r straight out
        turn = Quaternion(axis, min(math.radians(15.0), need / max(r.length, 0.05) + 0.01))
        for ctrl, org in (("forearm_fk", "ORG-forearm"), ("hand_fk", "ORG-hand")):
            pb = p.pb[f"{org}.{S}"]
            q = pb.matrix.to_quaternion() @ pb.bone.matrix_local.to_quaternion().inverted()
            set_world(p, f"{ctrl}.{S}", turn @ q)
            _update()


def calm(q: Quaternion, keep: float) -> Quaternion:
    """q with `keep` of its turn about the vertical (yaw) and of its sideways
    roll; its forward lean kept."""
    if keep == 1.0:
        return q
    f = q @ FACE
    yaw = math.atan2(-f.x, -f.y)
    q = Quaternion((0.0, 0.0, 1.0), (1.0 - keep) * yaw) @ q
    left = q @ Vector((1.0, 0.0, 0.0))
    roll = math.asin(max(-1.0, min(1.0, left.z)))
    return Quaternion(q @ FACE, -(1.0 - keep) * roll) @ q


def retarget(p, clip: str, t: float, curl: tuple[float, float], spread: float = 1.0, twist: float = 1.0,
             mirrored: bool = False, forward: float = 0.0) -> None:
    """Poses the rig as the source clip at `t` seconds; `spread` scales the
    arm bones' lean out of the chest's midplane (`gather`), `twist` the
    chest's yaw and roll (`calm`), `forward` (degrees) turns the arms'
    swing forward about the chest's left-right axis; `mirrored` gives the right leg the left leg's motion half a loop later
    (`mirror_legs`)."""
    src = source()
    rest = src.rest
    w = src.pose(clip, t)
    if mirrored:
        w = mirror_legs(w, src.pose(clip, t + src.duration(clip) * 0.5), rest)
    k = ratio(p, rest)
    targets = feet(p, w, rest, k)

    hips = (w["pelvis"][0] - rest["pelvis"][0]) * k
    p.loc("torso", hips)
    p.rot("torso", Quaternion())
    _update()
    set_world(p, "hips", delta(w, rest, "pelvis"))
    set_world(p, "chest", calm(delta(w, rest, "spine_03"), twist))
    _update()
    hips = hips + hip_shift(p, w, rest, k)
    p.loc("torso", hips)
    _update()
    drop = reach_drop(p, targets)
    if drop > 0.0:
        p.loc("torso", hips - Vector((0.0, 0.0, drop)))
        _update()
    neck, head = level_head(clip, w, rest)
    set_world(p, "neck", neck)
    for _, S, s in _sides():
        set_world(p, f"shoulder.{S}", Quaternion().slerp(delta(w, rest, f"clavicle_{s}"), CLAVICLE))
    _update()
    set_world(p, "head", head)
    # Arm bones, each with the source's rest direction turned onto ours first.
    chain = (("upper_arm_fk", "ORG-upper_arm", "upperarm", "lowerarm"), ("forearm_fk", "ORG-forearm", "lowerarm", "hand"),
             ("hand_fk", "ORG-hand", "hand", "middle_01"))
    for ctrl, org, a, b in chain:
        for _, S, s in _sides():
            ours = (p.rig.data.bones[f"{org}.{S}"].tail_local - p.rig.data.bones[f"{org}.{S}"].head_local).normalized()
            theirs = (rest[f"{b}_{s}"][0] - rest[f"{a}_{s}"][0]).normalized()
            c = theirs.rotation_difference(ours)
            q = delta(w, rest, f"{a}_{s}") @ c.inverted()
            if forward:
                q = Quaternion(chest_axes(p)[0], -math.radians(forward)) @ q
            if spread != 1.0:
                # The upper arm comes in toward the body; the forearm and the
                # hand, which the sprint turns in across it, turn to pump
                # straight forward and back, so the fist stays off the belly.
                q = gather(p, q @ ours, side_of(S), spread) @ q
            set_world(p, f"{ctrl}.{S}", q)
        _update()
    if spread != 1.0:
        for side, S, _ in _sides():
            clear_hand(p, side, S)
    for side, S, _ in _sides():
        p.fingers(side, curl[0], thumb=curl[1])
        ankle, q_foot, q_toe, _ = targets[side]
        p.loc(f"foot_ik.{S}", ankle - p.rest_head[f"foot_ik.{S}"])
        p.rot(f"foot_ik.{S}", q_foot)
        p.rot(f"toe_ik.{S}", q_toe)
    aim_knees(p)


def walk(p, frame: float) -> None:
    retarget(p, "Walk_Loop", frame / FPS * WALK_RATE, CURL["walk"])


def run(p, frame: float) -> None:
    retarget(p, "Sprint_Loop", frame / FPS * RUN_RATE, CURL["run"], RUN_ARM_SPREAD, RUN_CHEST_TWIST, mirrored=True,
             forward=RUN_ARM_FORWARD_DEG)


def frames(clip: str, rate: float) -> int:
    """The baked loop's length in frames at 30 fps."""
    return round(source().duration(clip) * FPS / rate)


def clip_source(clip: str, rate: float) -> dict:
    """The manifest's note of where a clip comes from."""
    return {"source": f"Quaternius UAL1 {clip} (CC0)", "source_rate": rate}
