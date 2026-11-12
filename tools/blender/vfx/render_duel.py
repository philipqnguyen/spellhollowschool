"""Review render of a duel in the arena with the exported assets: the room,
two duelists mid-exchange (a cast at its release, the other behind a raised
shield), spectators cheering, a stun bolt with its trail in flight, the
shield bubble and a wand light, at game distance. Writes into the art
repository.

    blender -b --factory-startup --python tools/blender/vfx/render_duel.py -- \\
        [--assets client/public/assets] [--out art/renders/vfx]

Needs the arena (world/build_school.py), the characters and the effects
(build_vfx.py) exported first.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "world"), os.path.join(os.path.dirname(HERE), "characters"),
                os.path.dirname(HERE)]

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

import build_vfx  # noqa: E402
import bpyutil  # noqa: E402
import review  # noqa: E402
from check_layout import script_args  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SCENE_FRAME = 100


def import_glb(path: str) -> list:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def place(objs, pos, yaw: float) -> None:
    for o in objs:
        if o.parent is None:
            o.location = bpyutil.to_blender(pos)
            o.rotation_mode = "XYZ"
            o.rotation_euler = (0.0, 0.0, yaw)


def character(assets: str, body: str, pos, yaw: float, clip: str, frame: int, colour: str, wand: bool) -> None:
    """A mannequin playing `clip` at `frame` (through an NLA strip, so each
    character can sit at its own frame), with the wand in hand."""
    from charlib import anim_spells as SP
    from charlib import proportions as P

    objs = import_glb(os.path.join(assets, "characters", f"{body}.glb"))
    arm = next(o for o in objs if o.type == "ARMATURE")
    mat = bpyutil.principled(f"m_{colour}", colour, None, 0.6)
    for o in objs:
        if o.type == "MESH":
            o.hide_render = o.get("lod", 0) == 1 or o.name.endswith("_lod1")
            o.data.materials.clear()
            o.data.materials.append(mat)
    # The importer leaves the first clip active: back to the rest pose
    # before the wand is parented to the hand.
    if arm.animation_data:
        arm.animation_data.action = None
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)
        pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    if wand:
        g = SP.Grip(P.get(body))
        w = import_glb(os.path.join(assets, "vfx", "wand.glb"))
        z = Vector(g.wand).normalized()
        x = Vector(g.palm)
        x = (x - z * x.dot(z)).normalized()
        world = Matrix((x, z.cross(x), z)).transposed().to_4x4()
        world.translation = Vector(g.grip)
        for o in w:
            o.matrix_world = world
            bpy.context.view_layer.update()
            o.parent = arm
            o.parent_type = "BONE"
            o.parent_bone = "DEF-hand.R"
            bpy.context.view_layer.update()
            o.matrix_world = world
    act = next(a for a in bpy.data.actions if a.name == clip or a.name.startswith(clip + "_") or a.name.startswith(clip + "."))
    ad = arm.animation_data or arm.animation_data_create()
    for t in list(ad.nla_tracks):
        ad.nla_tracks.remove(t)
    ad.action = None
    track = ad.nla_tracks.new()
    strip = track.strips.new(clip, SCENE_FRAME - frame, act)
    strip.extrapolation = "HOLD"
    place([arm], pos, yaw)


def effect(nodes: dict, name: str, pos, yaw: float = 0.0, pitch: float = 0.0, scale: float = 1.0):
    src = nodes[name]
    o = src.copy()
    bpy.context.scene.collection.objects.link(o)
    o.hide_render = False
    o.location = bpyutil.to_blender(pos)
    o.rotation_mode = "ZXY"
    o.rotation_euler = (pitch, 0.0, yaw)
    o.scale = (scale, scale, scale)
    return o


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a duel in the arena with the effects")
    ap.add_argument("--assets", default=os.path.join(REPO, "client", "public", "assets"))
    ap.add_argument("--out", default=os.path.join(REPO, "art", "renders", "vfx"))
    args = ap.parse_args(script_args(sys.argv))
    assets = os.path.abspath(args.assets)
    bpyutil.reset_scene()
    with open(os.path.join(assets, "world", "instances.json"), encoding="utf-8") as f:
        arena = json.load(f)["duel_arena"]
    with open(os.path.join(assets, "vfx", "manifest.json"), encoding="utf-8") as f:
        fx = json.load(f)
    import_glb(os.path.join(assets, "instances", "duel_arena.glb"))
    review.setup(arena["ambient"]["sky"], arena["ambient"]["intensity"] * 0.7)
    for lt in arena["lights"]:
        review.point_light(f"l_{lt['id']}", lt["pos"], lt["color"], lt["intensity"])
    character(assets, "body_a", (-5.5, 0.9, 0.0), math.pi / 2, "cast", 13, "#C8C0D8", True)
    character(assets, "body_b", (5.5, 0.9, 0.0), -math.pi / 2, "shield_raise", 10, "#D8C8C0", True)
    for i, (x, z, yaw, frame) in enumerate(((-2.5, -3.6, 0.0, 20), (1.8, -3.9, 0.2, 55), (-1.0, 3.8, math.pi, 70),
                                            (3.2, 4.1, math.pi - 0.3, 5))):
        character(assets, "body_b" if i % 2 else "body_a", (x, 0.0, z), yaw, "spectator_cheer", frame, "#A8B0C0", False)
    nodes = {o.name: o for o in import_glb(os.path.join(assets, "vfx", "effects.glb")) if o.type == "MESH"}
    for o in nodes.values():
        o.hide_render = True
    spells = fx["spells"]
    attack = next(s for s in spells.values() if s.get("shape") == "spike")
    shield = next(s for s in spells.values() if "shield" in s)
    light = next(s for s in spells.values() if "glow" in s)
    placed = [effect(nodes, attack["core"], (0.6, 1.62, 0.0), math.pi / 2),
              effect(nodes, attack["trail"], (0.6, 1.62, 0.0), math.pi / 2),
              effect(nodes, attack["charge"], (-4.62, 2.25, -0.1), 0.0),
              effect(nodes, shield["shield"], (5.35, 1.45, 0.0), -math.pi / 2),
              effect(nodes, light["glow"], (-2.2, 1.55, -3.3), 0.0, scale=0.8)]
    build_vfx.review_material(placed)
    os.makedirs(args.out, exist_ok=True)
    bpy.context.scene.frame_set(SCENE_FRAME)
    for name, (eye, target, lens) in {
        "duel_side": ((0.0, 3.2, 9.5), (0.0, 1.3, 0.0), 20),
        "duel_over_shoulder": ((-8.6, 2.5, 1.2), (5.0, 1.4, -0.2), 22),
        "duel_spectator": ((-1.5, 1.6, -6.0), (1.5, 1.4, 1.0), 18),
    }.items():
        review.camera(eye, target, lens)
        review.render(os.path.join(args.out, f"{name}.png"), 1280, 720)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
