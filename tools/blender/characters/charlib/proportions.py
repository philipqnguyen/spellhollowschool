"""Measurements of the two base bodies (docs/art/base-body-spec.md section 1).

All values are metres in Blender coordinates: +X is the character's left, the
character faces -Y and stands on the origin with Z up. `fwd` values are
measured toward the front (so y = -fwd).

Only the skeleton-defining landmarks (joint centres) and the flesh radii live
here; the shapes built from them are in `body.py` and `head.py`. Builds
(`build_slim`, `build_muscular`, `build_heavy`) scale flesh only, never joints,
so one skeleton serves every build.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

BODY_IDS = ("body_a", "body_b")
BUILD_KEYS = ("build_slim", "build_muscular", "build_heavy")


@dataclass(frozen=True)
class Build:
    """Shape-key weights, each 0..1."""

    slim: float = 0.0
    muscular: float = 0.0
    heavy: float = 0.0

    def k(self, slim: float = 0.0, muscular: float = 0.0, heavy: float = 0.0) -> float:
        """Multiplier 1 + sum(weight * delta) for a flesh measurement."""
        return 1.0 + self.slim * slim + self.muscular * muscular + self.heavy * heavy


NEUTRAL = Build()
BUILDS = {
    "build_slim": Build(slim=1.0),
    "build_muscular": Build(muscular=1.0),
    "build_heavy": Build(heavy=1.0),
}


@dataclass(frozen=True)
class Body:
    id: str
    height: float
    height_range: tuple[float, float]
    # Head (the head module scales its sculpt by head_scale).
    head_scale: float
    eye_z: float
    head_fwd: float  # forward offset of the head centre from the spine axis
    # Neck.
    neck_r: float
    neck_base_z: float  # front (sternal notch); the back is higher
    neck_back_rise: float
    # Shoulders and chest.
    shoulder_joint: tuple[float, float, float]  # (x, fwd, z) of the humeral head, left side
    deltoid_x: float  # outer deltoid (half the deltoid-to-deltoid width)
    chest_z: float
    rib_w: float
    rib_fwd: float
    rib_back: float
    chest_bulk: float  # pectoral (body_a) or bust (body_b) size, 0..1
    bust: float  # 0 = flat pectorals, 1 = a modest bust
    # Waist, hips, crotch.
    waist_z: float
    waist_w: float
    waist_fwd: float
    waist_back: float
    hip_z: float
    hip_w: float
    hip_fwd: float
    hip_back: float
    crotch_z: float
    # Legs (left side).
    hip_joint: tuple[float, float, float]
    knee: tuple[float, float, float]
    ankle: tuple[float, float, float]
    thigh_r: float
    knee_r: float
    calf_r: float
    ankle_r: float
    foot_len: float
    foot_w: float
    # Arms (left side): A-pose, 45 degrees down, elbow bent 5 degrees.
    upper_arm_len: float
    forearm_len: float
    arm_r: float
    elbow_r: float
    forearm_r: float
    wrist_w: float
    wrist_t: float
    hand_len: float  # wrist to middle finger tip (already includes the 105%)
    palm_w: float
    palm_t: float
    finger_r: float
    extra: dict = field(default_factory=dict)

    def scaled_height(self, target: float) -> float:
        """Uniform root scale that gives `target` metres (see export notes)."""
        return target / self.height


BODY_A = Body(
    id="body_a",
    height=1.78,
    height_range=(1.60, 1.88),
    head_scale=1.0,
    eye_z=1.66,
    head_fwd=0.012,
    neck_r=0.057,
    neck_base_z=1.452,
    neck_back_rise=0.045,
    shoulder_joint=(0.176, 0.0, 1.418),
    deltoid_x=0.230,
    chest_z=1.300,
    rib_w=0.150,
    rib_fwd=0.112,
    rib_back=0.100,
    chest_bulk=0.6,
    bust=0.0,
    waist_z=1.065,
    waist_w=0.138,
    waist_fwd=0.095,
    waist_back=0.085,
    hip_z=0.925,
    hip_w=0.178,
    hip_fwd=0.098,
    hip_back=0.112,
    crotch_z=0.840,
    hip_joint=(0.088, 0.008, 0.930),
    knee=(0.098, 0.006, 0.500),
    ankle=(0.102, -0.010, 0.082),
    thigh_r=0.086,
    knee_r=0.050,
    calf_r=0.056,
    ankle_r=0.033,
    foot_len=0.265,
    foot_w=0.100,
    upper_arm_len=0.300,
    forearm_len=0.262,
    arm_r=0.046,
    elbow_r=0.037,
    forearm_r=0.041,
    wrist_w=0.031,
    wrist_t=0.021,
    hand_len=0.200,
    palm_w=0.090,
    palm_t=0.030,
    finger_r=0.0098,
)

BODY_B = Body(
    id="body_b",
    height=1.68,
    height_range=(1.52, 1.80),
    head_scale=0.957,
    eye_z=1.560,
    head_fwd=0.012,
    neck_r=0.049,
    neck_base_z=1.366,
    neck_back_rise=0.042,
    shoulder_joint=(0.150, 0.0, 1.336),
    deltoid_x=0.195,
    chest_z=1.222,
    rib_w=0.128,
    rib_fwd=0.100,
    rib_back=0.090,
    chest_bulk=0.55,
    bust=1.0,
    waist_z=1.020,
    waist_w=0.118,
    waist_fwd=0.082,
    waist_back=0.078,
    hip_z=0.880,
    hip_w=0.183,
    hip_fwd=0.094,
    hip_back=0.118,
    crotch_z=0.780,
    hip_joint=(0.086, 0.006, 0.872),
    knee=(0.090, 0.004, 0.468),
    ankle=(0.094, -0.008, 0.076),
    thigh_r=0.078,
    knee_r=0.045,
    calf_r=0.051,
    ankle_r=0.030,
    foot_len=0.245,
    foot_w=0.090,
    upper_arm_len=0.282,
    forearm_len=0.244,
    arm_r=0.040,
    elbow_r=0.032,
    forearm_r=0.035,
    wrist_w=0.027,
    wrist_t=0.018,
    hand_len=0.185,
    palm_w=0.080,
    palm_t=0.026,
    finger_r=0.0088,
)

BODIES = {b.id: b for b in (BODY_A, BODY_B)}
SPEC = dict(BODIES)  # the spec bodies: height ranges always come from here (data/characters/creator.json)


def get(body_id: str) -> Body:
    return BODIES[body_id]


def with_extra(body: Body, **extra) -> Body:
    merged = dict(body.extra)
    merged.update(extra)
    return replace(body, extra=merged)


def to_json(body: Body) -> str:
    """The body's measurements as JSON (stored on built skeletons and in baked
    body shapes, charlib/shape_target.py)."""
    import dataclasses
    import json

    return json.dumps(dataclasses.asdict(body), sort_keys=True)


def from_json(text: str) -> Body:
    import json

    d = json.loads(text)
    for k in ("height_range", "shoulder_joint", "hip_joint", "knee", "ankle"):
        d[k] = tuple(d[k])
    return Body(**d)


def of_skeleton(obj, body_id: str) -> Body:
    """The measurements a built skeleton object was made from (falls back to
    the spec body for files built before they were stored)."""
    text = obj.get("body_params") if obj is not None else None
    return from_json(text) if text else get(body_id)


def register(body: Body) -> None:
    """Use these measurements for `body.id` from now on in this process (a
    body reshaped onto a baked sculpt shape, charlib/shape_target.py). The
    height range stays the spec's (creator.json; shape targets baked before
    the character adoption carry an older one)."""
    if body.id in SPEC:
        body = replace(body, height_range=SPEC[body.id].height_range)
    BODIES[body.id] = body
