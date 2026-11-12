"""Review-render helpers (EEVEE) shared by the world build scripts."""

from __future__ import annotations

import bpy
from mathutils import Vector

import paint
from bpyutil import to_blender

# Blender watts per unit of a manifest light's `intensity` (see README).
LIGHT_WATTS = 25.0


def setup(ambient_hex: str = "#808890", ambient_strength: float = 0.3, samples: int = 32) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    eevee = scene.eevee
    eevee.taa_render_samples = samples
    if hasattr(eevee, "use_shadows"):
        eevee.use_shadows = True
    if hasattr(eevee, "use_raytracing"):
        eevee.use_raytracing = False
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX" if "AgX" in [i.name for i in type(scene.view_settings).bl_rna.properties["view_transform"].enum_items] else "Filmic"
    scene.view_settings.look = "None"
    world = bpy.data.worlds.new("world")
    scene.world = world
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (*paint.srgb_to_linear(paint.hex_rgb(ambient_hex)), 1.0)
    bg.inputs["Strength"].default_value = ambient_strength


def point_light(name: str, pos, color_hex: str, intensity: float, radius: float = 0.08, shadow: bool = True) -> None:
    light = bpy.data.lights.new(name, "POINT")
    light.energy = intensity * LIGHT_WATTS
    light.color = tuple(paint.srgb_to_linear(paint.hex_rgb(color_hex)))
    light.shadow_soft_size = radius
    light.use_shadow = shadow
    obj = bpy.data.objects.new(name, light)
    obj.location = to_blender(pos)
    bpy.context.scene.collection.objects.link(obj)


def camera(eye, target, lens: float) -> None:
    cam = bpy.data.cameras.new("cam")
    cam.lens = lens
    cam.clip_start = 0.03
    cam.clip_end = 300
    obj = bpy.data.objects.get("cam") or bpy.data.objects.new("cam", cam)
    obj.data = cam
    if obj.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(obj)
    e, t = Vector(to_blender(eye)), Vector(to_blender(target))
    obj.location = e
    obj.rotation_euler = (t - e).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = obj


def ortho_camera(eye, target, scale: float) -> None:
    camera(eye, target, 50)
    cam = bpy.context.scene.camera.data
    cam.type = "ORTHO"
    cam.ortho_scale = scale


def render(path: str, width: int = 1280, height: int = 800) -> None:
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print(f"rendered {path}")


def dyed_preview_material(base: bpy.types.Material, dyes_hex: list[str]) -> bpy.types.Material:
    """A copy of a furniture material that applies the dye rule to its
    COLOR_0 mask, the way the client does:
    albedo = tex * (1 - r - g - b + r * P + g * S + b * T)."""
    mat = base.copy()
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = next(n for n in nt.nodes if n.type == "TEX_IMAGE")
    attr = nt.nodes.new("ShaderNodeVertexColor")
    attr.layer_name = "COLOR_0"
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(attr.outputs["Color"], sep.inputs["Color"])
    # tint = 1 + r (P - 1) + g (S - 1) + b (T - 1), per channel.
    acc = None
    for chan, hexc in zip(("Red", "Green", "Blue"), dyes_hex):
        lin = paint.srgb_to_linear(paint.hex_rgb(hexc))
        term = nt.nodes.new("ShaderNodeMix")
        term.data_type = "RGBA"
        term.blend_type = "MIX"
        term.inputs["A"].default_value = (1.0, 1.0, 1.0, 1.0)
        term.inputs["B"].default_value = (*lin, 1.0)
        nt.links.new(sep.outputs[chan], term.inputs["Factor"])
        if acc is None:
            acc = term
        else:
            mul = nt.nodes.new("ShaderNodeMix")
            mul.data_type = "RGBA"
            mul.blend_type = "MULTIPLY"
            mul.inputs["Factor"].default_value = 1.0
            nt.links.new(acc.outputs["Result"], mul.inputs["A"])
            nt.links.new(term.outputs["Result"], mul.inputs["B"])
            acc = mul
    final = nt.nodes.new("ShaderNodeMix")
    final.data_type = "RGBA"
    final.blend_type = "MULTIPLY"
    final.inputs["Factor"].default_value = 1.0
    nt.links.new(tex.outputs["Color"], final.inputs["A"])
    nt.links.new(acc.outputs["Result"], final.inputs["B"])
    nt.links.new(final.outputs["Result"], bsdf.inputs["Base Color"])
    return mat
