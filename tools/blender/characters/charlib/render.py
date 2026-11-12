"""Review renders: a neutral studio, tinted preview materials and cameras.

The preview material reproduces the client's tint formula from `atlas.py`
(`albedo = texture * ((1 - r - g - b) + r * C0 + g * C1 + b * C2)`) so the
renders show what players will see for a given skin tone, hair colour or dye
choice.
"""

from __future__ import annotations

import math
import os

import bpy
from mathutils import Vector

from . import atlas as A
from . import montage


def studio(resolution=(640, 960), background=(0.46, 0.48, 0.52)) -> bpy.types.Scene:
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = resolution
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    try:
        sc.eevee.taa_render_samples = 24
    except AttributeError:
        pass
    world = bpy.data.worlds.get("studio") or bpy.data.worlds.new("studio")
    sc.world = world
    if world.node_tree is None:
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (*background, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    for name, rot, energy, angle in (("key", (52, 0, -32), 3.2, 20), ("fill", (70, 0, 140), 1.1, 45), ("rim", (35, 0, 180), 1.4, 10)):
        if bpy.data.objects.get(name):
            continue
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        light.angle = math.radians(angle)
        o = bpy.data.objects.new(name, light)
        o.rotation_euler = tuple(math.radians(a) for a in rot)
        sc.collection.objects.link(o)
    if not bpy.data.objects.get("floor"):
        bpy.ops.mesh.primitive_plane_add(size=8.0)
        floor = bpy.context.active_object
        floor.name = "floor"
        m = bpy.data.materials.new("floor")
        m.diffuse_color = (0.36, 0.37, 0.4, 1)
        if m.node_tree is None:
            m.use_nodes = True
        m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.3, 0.31, 0.34, 1)
        m.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.9
        floor.data.materials.append(m)
    if not bpy.data.objects.get("camera"):
        cam = bpy.data.cameras.new("camera")
        o = bpy.data.objects.new("camera", cam)
        sc.collection.objects.link(o)
        sc.camera = o
    return sc


def tint_material(name: str, c0, c1=(1, 1, 1), c2=(1, 1, 1), roughness: float = 0.7) -> bpy.types.Material:
    """Preview material: atlas texture tinted by the COLOR_0 mask (sRGB hex or linear tuples)."""
    def lin(c):
        return A.hex_to_linear(c) if isinstance(c, str) else tuple(c)

    cols = [lin(c0), lin(c1), lin(c2)]
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = roughness
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.get("character_atlas") or A.build_image()
    tex.interpolation = "Closest"
    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = "Color"
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(vc.outputs["Color"], sep.inputs["Color"])
    comb = nt.nodes.new("ShaderNodeCombineColor")
    chans = [sep.outputs["Red"], sep.outputs["Green"], sep.outputs["Blue"]]

    def math_node(op, a, b):
        n = nt.nodes.new("ShaderNodeMath")
        n.operation = op
        for i, v in enumerate((a, b)):
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = float(v)
            else:
                nt.links.new(v, n.inputs[i])
        return n.outputs[0]

    total = math_node("ADD", math_node("ADD", chans[0], chans[1]), chans[2])
    rest = math_node("SUBTRACT", 1.0, total)
    for ch in range(3):
        acc = rest
        for k in range(3):
            acc = math_node("ADD", acc, math_node("MULTIPLY", chans[k], cols[k][ch]))
        nt.links.new(acc, comb.inputs[ch])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs["Factor"].default_value = 1.0
    nt.links.new(tex.outputs["Color"], mix.inputs["A"])
    nt.links.new(comb.outputs["Color"], mix.inputs["B"])
    nt.links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def assign(obj: bpy.types.Object, mat: bpy.types.Material) -> None:
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def aim(target, azimuth_deg: float, elevation_deg: float, distance: float, lens: float = 50.0) -> None:
    cam = bpy.context.scene.camera
    cam.data.lens = lens
    a = math.radians(azimuth_deg)
    e = math.radians(elevation_deg)
    t = Vector(target)
    pos = t + Vector((distance * math.sin(a) * math.cos(e), -distance * math.cos(a) * math.cos(e), distance * math.sin(e)))
    cam.location = pos
    cam.rotation_euler = (t - pos).to_track_quat("-Z", "Y").to_euler()


def shoot(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return path


def sheet(paths: list[str], out: str, cols: int, scale: int = 1) -> str:
    """Tile frames into one sheet and delete the individual frames."""
    return montage.tile(paths, out, cols, scale=scale, remove=True)
