"""Rigify rig generation and the exported game skeleton.

1. `build_metarig` adds Rigify's Human meta-rig, removes the face rig (too many
   bones for the browser; the face uses shape keys plus jaw and eye bones) and
   moves every bone onto the body's landmarks from `skeleton.joints`.
2. `generate` runs Rigify. Animations are keyed on its controls (`anim.py`).
3. `build_skeleton` makes the exported armature: Rigify's DEF bones with the
   same names, rest transforms and a clean parent chain, plus `root`,
   `DEF-jaw`, `DEF-eye.L/R` and the sockets (docs/art/base-body-spec.md
   section 3). Every exported bone is flagged deforming so the glTF exporter
   keeps it; only DEF bones carry weights.
4. `constrain_to_rig` makes the skeleton follow the Rigify rig, so actions can
   be baked onto it (`anim.bake`).
"""

from __future__ import annotations

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import skeleton as K
from .proportions import Body

SOCKETS = ("socket_hand.L", "socket_hand.R", "socket_head", "socket_back", "socket_hip.L")
EXTRA_DEFORM = ("DEF-jaw", "DEF-eye.L", "DEF-eye.R")


def enable_rigify() -> None:
    import addon_utils

    loaded, enabled = addon_utils.check("rigify")
    if not enabled:
        bpy.ops.preferences.addon_enable(module="rigify")


def _activate(obj: bpy.types.Object) -> None:
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)


def build_metarig(body: Body, name: str) -> bpy.types.Object:
    enable_rigify()
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.armature_human_metarig_add()
    mr = bpy.context.active_object
    mr.name = name
    mr.data.name = name
    bpy.ops.object.mode_set(mode="EDIT")
    eb = mr.data.edit_bones
    face = eb["face"]
    for b in [b for b in eb if b == face or face in b.parent_recursive]:
        eb.remove(b)
    joints = K.joint_map(body, rig=True)  # the knee with its forward rest bend (skeleton.KNEE_LEAD)
    # Disconnect first so heads can move freely, then reconnect as specified.
    for b in eb:
        b.use_connect = False
    for jname, j in joints.items():
        b = eb.get(jname)
        if b is None:
            raise KeyError(f"meta-rig has no bone {jname}")
        b.head = Vector(j.head)
        b.tail = Vector(j.tail)
    for jname, j in joints.items():
        b = eb[jname]
        if j.z_axis is not None:
            b.align_roll(Vector(j.z_axis))
        if j.connect and b.parent is not None and (b.parent.tail - b.head).length < 1e-5:
            b.use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")
    return mr


def generate(metarig: bpy.types.Object, name: str) -> bpy.types.Object:
    _activate(metarig)
    metarig.data.rigify_target_rig = None
    bpy.ops.pose.rigify_generate()
    rig = bpy.context.active_object
    rig.name = name
    rig.data.name = name
    # No stretchy IK in game animation; legs IK, arms FK by default.
    for pb in rig.pose.bones:
        if "IK_Stretch" in pb.keys():
            pb["IK_Stretch"] = 0.0
        if pb.name.startswith("upper_arm_parent"):
            pb["IK_FK"] = 1.0
        if pb.name.startswith("thigh_parent"):
            pb["IK_FK"] = 0.0
    metarig.hide_set(True)
    metarig.hide_render = True
    # Control widgets are display-only meshes; keep them out of renders.
    for o in bpy.data.objects:
        if o.name.startswith("WGT-"):
            o.hide_render = True
    for c in bpy.data.collections:
        if c.name.startswith("WGTS"):
            c.hide_render = True
    rig.hide_render = True
    return rig


def _def_parent(rig_bones, bone) -> str:
    """Nearest exported ancestor of a generated DEF bone."""
    p = bone.parent
    while p is not None:
        if p.name == "root":
            return "root"
        if p.name.startswith("DEF-") and p.name != bone.name:
            return p.name
        if p.name.startswith("ORG-"):
            cand = "DEF-" + p.name[4:]
            if cand in rig_bones and cand != bone.name:
                return cand
        p = p.parent
    return "root"


def build_skeleton(rig: bpy.types.Object, body: Body, name: str) -> bpy.types.Object:
    """The exported armature (see module docs)."""
    src = {}
    for b in rig.data.bones:
        if b.use_deform and b.name.startswith("DEF-"):
            src[b.name] = (b.head_local.copy(), b.tail_local.copy(), b.matrix_local.copy(), _def_parent(rig.data.bones, b))
    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    bpy.context.scene.collection.objects.link(obj)
    _activate(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.edit_bones
    root = eb.new("root")
    root.head = (0, 0, 0)
    root.tail = (0, 0.25, 0)
    root.roll = 0.0
    created = {"root": root}
    for bname, (head, tail, mat, _) in src.items():
        e = eb.new(bname)
        e.head = head
        e.tail = tail
        # Recover the roll from the rest matrix (Z axis).
        e.align_roll(mat.to_3x3().col[2])
        created[bname] = e
    for bname, (_, _, _, parent) in src.items():
        created[bname].parent = created[parent]
    # Face bones: jaw hinge behind the jaw angle, eyes at the eyeball centres.
    c = K.head_centre(body)
    s = body.head_scale
    jaw = eb.new("DEF-jaw")
    jaw.head = Vector(c + K.fwd(0, -0.005 * s, -0.04 * s))
    jaw.tail = Vector(c + K.fwd(0, 0.07 * s, -0.10 * s))
    jaw.align_roll(Vector((0, 0, 1)))
    jaw.parent = created["DEF-spine.006"]
    el, er = K.eye_centres(body)
    for nm, p in (("DEF-eye.L", el), ("DEF-eye.R", er)):
        e = eb.new(nm)
        e.head = Vector(p)
        e.tail = Vector(p + K.fwd(0, 0.02, 0))
        e.align_roll(Vector((0, 0, 1)))
        e.parent = created["DEF-spine.006"]
    # Sockets.
    hf = K.hand_frame(body)
    hs = K.hand_scale(body)
    grip = hf.wrist + hf.axis * 0.07 * hs + hf.palm_normal * 0.028 * hs
    sockets = {
        "socket_hand.L": (grip, grip + hf.axis * 0.05, "DEF-hand.L", -hf.palm_normal),
        "socket_hand.R": (K.mirror(grip), K.mirror(grip + hf.axis * 0.05), "DEF-hand.R", K.mirror(-hf.palm_normal)),
        "socket_head": (c + K.fwd(0, -0.01 * s, 0.075 * s), c + K.fwd(0, -0.01 * s, 0.125 * s), "DEF-spine.006", K.vec(0, -1, 0)),
        "socket_back": (K.fwd(0, -(body.rib_back + 0.03), body.chest_z + 0.02), K.fwd(0, -(body.rib_back + 0.03), body.chest_z + 0.07),
                        "DEF-spine.003", K.vec(0, -1, 0)),
        "socket_hip.L": (K.fwd(body.hip_w + 0.02, 0.0, body.hip_z + 0.04), K.fwd(body.hip_w + 0.02, 0.0, body.hip_z - 0.01), "DEF-spine",
                         K.vec(0, -1, 0)),
    }
    for nm, (h, t, parent, zax) in sockets.items():
        e = eb.new(nm)
        e.head = Vector(h)
        e.tail = Vector(t)
        e.align_roll(Vector(zax))
        e.parent = created[parent]
    for e in eb:
        e.use_deform = True
        e.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def constrain_to_rig(skel: bpy.types.Object, rig: bpy.types.Object) -> None:
    """Copy Transforms (world space) from the rig's DEF bones and root."""
    for pb in skel.pose.bones:
        target = pb.name if pb.name in rig.pose.bones else None
        if target is None or pb.name in SOCKETS or pb.name in EXTRA_DEFORM:
            continue
        c = pb.constraints.new("COPY_TRANSFORMS")
        c.name = "bake"
        c.target = rig
        c.subtarget = target


def clear_constraints(skel: bpy.types.Object) -> None:
    for pb in skel.pose.bones:
        for c in list(pb.constraints):
            pb.constraints.remove(c)


def exported_bone_names(skel: bpy.types.Object) -> list[str]:
    return [b.name for b in skel.data.bones]


def bind(mesh_obj: bpy.types.Object, skel: bpy.types.Object) -> None:
    mesh_obj.parent = skel
    mesh_obj.matrix_parent_inverse = Matrix.Identity(4)
    mod = mesh_obj.modifiers.new("Armature", "ARMATURE")
    mod.object = skel


def rest_positions(skel: bpy.types.Object) -> dict[str, np.ndarray]:
    return {b.name: np.array(b.head_local) for b in skel.data.bones}
