"""Vegetation look A: writes the foliage kit (world/foliage.py) as `foliage_kit.glb` next to the
world cells: one node per species and LOD (`foliage.<species>`, `foliage.<species>_lod<N>`) on
the castle library's `castle_foliage` placeholder material (the client binds the atlas by
name), with the kit's custom normals (bent away from each crown's centre) and its grey shade in
COLOR_0. greybox.py calls `export_kit`; the manifest's `foliage_kit` entry is
`foliage.kit_manifest()`."""

from __future__ import annotations

import os

import bpy

import foliage as FO


def to_blender(p):
    return (p[0], -p[2], p[1])


def geo_object(name: str, geo: FO.Geo, mat) -> bpy.types.Object:
    faces = []
    for a, b, c in geo.faces:
        pa, pb, pc = geo.pos[a], geo.pos[b], geo.pos[c]
        fn = FO.cross(FO.sub(pb, pa), FO.sub(pc, pa))
        vn = FO.add(FO.add(geo.nrm[a], geo.nrm[b]), geo.nrm[c])
        faces.append((a, b, c) if FO.dot(fn, vn) >= 0 else (a, c, b))  # front faces follow the custom normals
    me = bpy.data.meshes.new(name)
    me.from_pydata([to_blender(p) for p in geo.pos], [], faces)
    me.materials.append(mat)
    uv = me.uv_layers.new(name="UVMap")
    loops = [me.loops[li].vertex_index for poly in me.polygons for li in poly.loop_indices]
    uv.data.foreach_set("uv", [c for vi in loops for c in geo.uv[vi]])
    attr = me.color_attributes.new("COLOR_0", "FLOAT_COLOR", "POINT")
    attr.data.foreach_set("color", [c for s in geo.shade for c in (s, s, s, 1.0)])
    me.color_attributes.active_color = attr
    me.shade_smooth()
    me.normals_split_custom_set_from_vertices([to_blender(n) for n in geo.nrm])
    me.validate(clean_customdata=False)
    me.update()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def export_kit(out_dir: str, castle_material, export_cell, pack_color0) -> str:
    """Builds and exports the kit; returns its path. `castle_material(name)`,
    `export_cell(objs, path)` and `pack_color0(path)` are greybox.py's."""
    mat = castle_material(FO.MATERIAL)
    objs = [geo_object(name, geo, mat) for name, geo in FO.build_kit().items()]
    path = os.path.join(out_dir, FO.KIT_FILE)
    export_cell(objs, path)
    pack_color0(path)
    for o in objs:
        me = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.meshes.remove(me)
    return path
