"""Blender glue for the world art: meshkit meshes -> objects, painted
textures -> images, material specs -> glTF-friendly Principled materials,
and glb export. Everything else in tools/blender/world is pure Python.
"""

from __future__ import annotations

import os

import bpy
import numpy as np

import meshkit as mk
import paint


def to_blender(p):
    """Y-up world (x east, y up, z south) -> Blender Z-up (x, -z, y)."""
    return (p[0], -p[2], p[1])


def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def image_from_array(name: str, arr: np.ndarray, colorspace: str = "sRGB") -> bpy.types.Image:
    """A packed PNG image from an RGBA float array (rows bottom to top)."""
    h, w, _ = arr.shape
    img = bpy.data.images.get(name)
    if img is None or img.size[0] != w or img.size[1] != h:
        img = bpy.data.images.new(name, w, h, alpha=False)
    img.colorspace_settings.name = colorspace
    img.pixels.foreach_set(np.ascontiguousarray(arr, dtype=np.float32).ravel())
    img.file_format = "PNG"
    img.pack()
    return img


def painted(spec_texture, size: int, name: str) -> bpy.types.Image:
    fn, kwargs = spec_texture
    return image_from_array(name, getattr(paint, fn)(size, **kwargs))


def principled(name: str, color=None, image: bpy.types.Image | None = None, roughness: float = 0.8,
               metallic: float = 0.0, emissive: float = 0.0, emissive_image: bpy.types.Image | None = None,
               emissive_color=None, cull: bool = True) -> bpy.types.Material:
    """A Principled BSDF material the glTF exporter maps one to one:
    base colour (factor or texture), roughness, metallic, emission."""
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    rgb = tuple(paint.srgb_to_linear(paint.hex_rgb(color))) if isinstance(color, str) else (color or (0.8, 0.8, 0.8))
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    if image is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.interpolation = "Linear"
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if emissive > 0:
        bsdf.inputs["Emission Strength"].default_value = emissive
        if emissive_image is not None or (image is not None and emissive_color is None):
            etex = nt.nodes.new("ShaderNodeTexImage")
            etex.image = emissive_image or image
            nt.links.new(etex.outputs["Color"], bsdf.inputs["Emission Color"])
        else:
            ec = emissive_color or rgb
            if isinstance(ec, str):
                ec = tuple(paint.srgb_to_linear(paint.hex_rgb(ec)))
            bsdf.inputs["Emission Color"].default_value = (*ec, 1.0)
    mat.diffuse_color = (*rgb, 1.0)
    mat.use_backface_culling = cull
    return mat


def mesh_object(name: str, m: mk.Mesh, materials: dict[str, bpy.types.Material], uvs=None, uv_scale=None,
                colors=None) -> bpy.types.Object:
    """A Blender object from a meshkit mesh.

    `uvs`: per-face corner UVs replacing the mesh's own; `uv_scale`: dict
    material -> factor applied to the mesh's metre UVs (1 / tile size);
    `colors`: per-face RGB written as the `COLOR_0` corner attribute.
    """
    verts = [to_blender(p) for p in m.verts]
    faces = [list(f.idx) for f in m.faces]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    # One slot per distinct material (several roles may share one material,
    # e.g. every atlas tile of a furniture item): one glTF primitive each.
    slots = list(dict.fromkeys(materials[f.material] for f in m.faces))
    for mat in slots:
        me.materials.append(mat)
    index = {mat.name: i for i, mat in enumerate(slots)}
    me.polygons.foreach_set("material_index", [index[materials[f.material].name] for f in m.faces])
    me.polygons.foreach_set("use_smooth", [f.smooth for f in m.faces])
    uv = me.uv_layers.new(name="UVMap")
    flat = []
    for k, f in enumerate(m.faces):
        corner = uvs[k] if uvs is not None else f.uvs
        s = (uv_scale or {}).get(f.material, 1.0)
        for u, v in corner:
            flat += [u * s, v * s]
    uv.data.foreach_set("uv", flat)
    if colors is not None:
        attr = me.color_attributes.new("COLOR_0", "FLOAT_COLOR", "CORNER")
        data = []
        for k, f in enumerate(m.faces):
            r, g, b = colors[k]
            data += [r, g, b, 1.0] * len(f.idx)
        attr.data.foreach_set("color", data)
        me.color_attributes.active_color = attr
        me.color_attributes.render_color_index = 0
    me.validate(clean_customdata=False)
    me.update()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def export_glb(objs, path: str, vertex_colors: bool = False, extras: bool = False) -> None:
    """Exports `objs` as a binary glTF (Y up, no cameras or lights)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    kwargs = dict(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        export_yup=True,
        export_apply=False,
        export_materials="EXPORT",
        export_texcoords=True,
        export_normals=True,
        export_tangents=False,
        export_cameras=False,
        export_lights=False,
        export_animations=False,
        export_skins=False,
        export_morph=False,
        export_extras=extras,
        export_image_format="AUTO",
    )
    props = bpy.ops.export_scene.gltf.get_rna_type().properties
    if "export_vertex_color" in props:
        kwargs["export_vertex_color"] = "ACTIVE" if vertex_colors else "NONE"
        if vertex_colors and "export_all_vertex_colors" in props:
            kwargs["export_all_vertex_colors"] = False
    else:
        kwargs["export_colors"] = vertex_colors
    bpy.ops.export_scene.gltf(**kwargs)


def glb_info(path: str) -> dict:
    """Reads a glb's JSON chunk: draw calls (primitives), triangles, and
    which attributes the primitives carry."""
    import json
    import struct

    with open(path, "rb") as f:
        data = f.read()
    (json_len,) = struct.unpack_from("<I", data, 12)
    doc = json.loads(data[20 : 20 + json_len])
    prims = [p for m in doc.get("meshes", []) for p in m["primitives"]]
    tris = 0
    for p in prims:
        acc = doc["accessors"][p["indices"]] if "indices" in p else doc["accessors"][p["attributes"]["POSITION"]]
        tris += acc["count"] // 3
    attrs = sorted({a for p in prims for a in p["attributes"]})
    return {"draw_calls": len(prims), "triangles": tris, "attributes": attrs, "materials": len(doc.get("materials", [])),
            "images": len(doc.get("images", []))}
