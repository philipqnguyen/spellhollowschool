"""The grounds export hook greybox.py calls (docs/art/castle-kit.md
"Exterior"; the seam is world/outdoor.py). `build(ctx)` turns `grounds_build.build`'s
geometry into Blender objects and returns the 33 outdoor cells of
`grid.P3A_RESIDENT_CELLS`:

* the 30 grounds cells: `<cell>.grounds_terrain` (material `grounds_terrain`:
  TEXCOORD_0 the turf's world UVs, TEXCOORD_1 the path / rock mask, COLOR_0 the
  shade, normals from the whole lattice) and its `_lod1`; the pieces' meshes
  (`castle_wall_c@ext`, `castle_props` with the props kit's textured part,
  `castle_roof`) with their `_lod1` nodes (`extras.kit_lod`); the props kit's
  flat-coloured meshes as greybox.py builds them;
* `foliage`: one empty per shape (`foliage.<shape>`) whose children share its
  mesh, exported as EXT_mesh_gpu_instancing;
* `lake`: `lake.lake_water`; `backdrop`: `backdrop.backdrop_rock` and
  `backdrop.backdrop_forest`.

It prints the lint's report (`grounds_build.lint`) and stops the export on an
error.
"""

from __future__ import annotations

import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foliage as FO  # noqa: E402
import grounds_build as GB  # noqa: E402
import layout_lib as L  # noqa: E402
import outdoor  # noqa: E402
import palette  # noqa: E402
from castle_kit import grid as G  # noqa: E402


def to_blender(p):
    return (p[0], -p[2], p[1])


def _plain_material(name: str, hex_colour: str) -> bpy.types.Material:
    """A plain, single-sided glTF material whose base colour is a palette colour
    (the client builds the real one by name, or toon-shades this colour)."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    rgb = palette.linear(hex_colour)
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.9
    mat.diffuse_color = (*rgb, 1.0)
    mat.use_backface_culling = True
    return mat


def _link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def terrain_object(tm, material) -> bpy.types.Object:
    me = bpy.data.meshes.new(tm.name)
    me.from_pydata([to_blender(v) for v in tm.verts], [], [list(t) for t in tm.tris])
    me.materials.append(material)
    uv0 = me.uv_layers.new(name="UVMap")
    uv1 = me.uv_layers.new(name="Mask")
    loops = [li for poly in me.polygons for li in poly.loop_indices]
    vi = [me.loops[li].vertex_index for li in loops]
    uv0.data.foreach_set("uv", [c for k in vi for c in tm.uv0[k]])
    uv1.data.foreach_set("uv", [c for k in vi for c in tm.uv1[k]])
    attr = me.color_attributes.new("COLOR_0", "FLOAT_COLOR", "CORNER")
    attr.data.foreach_set("color", [c for k in vi for c in (tm.shade[k], tm.shade[k], tm.shade[k], 1.0)])
    me.color_attributes.active_color = attr
    me.shade_smooth()
    me.normals_split_custom_set_from_vertices([to_blender(n) for n in tm.normals])
    me.validate(clean_customdata=False)
    me.update()
    return _link(bpy.data.objects.new(tm.name, me))


def plain_object(m, material) -> bpy.types.Object:
    """A grounds_backdrop.Mesh as an object (COLOR_0 grey, its uv if any)."""
    me = bpy.data.meshes.new(m.name)
    me.from_pydata([to_blender(v) for v in m.verts], [], [list(p) for p in m.polys])
    me.materials.append(material)
    loops = [li for poly in me.polygons for li in poly.loop_indices]
    vi = [me.loops[li].vertex_index for li in loops]
    if m.uv is not None:
        uv = me.uv_layers.new(name="UVMap")
        uv.data.foreach_set("uv", [c for k in vi for c in m.uv[k]])
    attr = me.color_attributes.new("COLOR_0", "FLOAT_COLOR", "CORNER")
    attr.data.foreach_set("color", [c for k in vi for c in (m.grey[k], m.grey[k], m.grey[k], 1.0)])
    me.color_attributes.active_color = attr
    me.polygons.foreach_set("use_smooth", [k not in m.flat for k in range(len(m.polys))])
    me.validate(clean_customdata=False)
    me.update()
    return _link(bpy.data.objects.new(m.name, me))


def _cellars(layout, exterior_path: str) -> list:
    """Plan rectangles (grown 2 m) of the cells with no outside (exterior.json `no_outside`)."""
    try:
        with open(exterior_path, encoding="utf-8") as f:
            cells = set(json.load(f).get("no_outside", []))
    except OSError:
        return []
    out = []
    for s in layout.surfaces:
        if s.cell in cells:
            out.append((s.x0 - 2.0, s.z0 - 2.0, s.x1 + 2.0, s.z1 + 2.0))
    return out


def build(ctx: outdoor.OutdoorContext) -> dict:
    world_dir = os.path.dirname(ctx.terrain_dir)
    props_path = os.path.join(world_dir, "props.json")
    textures_path = os.path.join(world_dir, "..", "art", "castle_textures.json")
    gr = GB.build(ctx.layout, ctx.terrain_dir, ctx.textures, props_path, textures_path,
                  cellars=_cellars(ctx.layout, os.path.join(world_dir, "exterior.json")))
    errs, warns, rep = GB.lint(gr)
    print("grounds (Terrain):")
    for line in rep:
        print(f"  {line}")
    low = [w for w in warns if "low end" in w]
    if low:
        print(f"  {len(low)} grounds cells under the guideline's low end ({G.P3A_GROUNDS_BUDGET.tris[0]} triangles): "
              "the terrain is cheap and these cells have little on them")
    for w in warns:
        if w not in low:
            print(f"  warning: {w}")
    if errs:
        for e in errs:
            print(f"  error: {e}")
        raise SystemExit("grounds_export: lint failed, nothing exported")
    pal = {k: v for k, v in ctx.textures["palette"].items() if not k.startswith("_")}
    terrain_mat = _plain_material(GB.TERRAIN, palette.CASTLE["grass"])
    out: dict[str, outdoor.OutdoorCell] = {}

    for cell, cg in gr.cells.items():
        # The props kit as greybox.py builds it: keep its flat-coloured meshes; its textured
        # part is already in the cell's castle_props (grounds_build).
        objs = []
        for o in ctx.kit_objects(cell):
            if o.name.endswith(".castle_props") or o.name.startswith(f"{cell}.castle_props"):
                bpy.data.objects.remove(o, do_unlink=True)
            else:
                objs.append(o)
        objs.append(terrain_object(cg.terrain, terrain_mat))
        lod = terrain_object(cg.terrain_lod1, terrain_mat)
        lod["kit_lod"] = {"base": cg.terrain.name, "distance": float(G.P3A_GROUNDS_LOD)}
        objs.append(lod)
        for name, md in sorted(cg.meshes.items()):
            o = ctx.mesh_from_data(md, ctx.castle_material(md.material))
            objs.append(o)
        for name, (md, base) in sorted(cg.lod_meshes.items()):
            o = ctx.mesh_from_data(md, ctx.castle_material(md.material))
            o["kit_lod"] = {"base": base, "distance": float(G.P3A_GROUNDS_LOD)}
            objs.append(o)
        lights = [L.Light(lt["id"], cell, tuple(lt["pos"]), lt["color"], lt["intensity"], lt["range"])
                  for lt in cg.lights[:6]]
        meta = {"library": cg.library(), "kit": {"tris": cg.tris(), "draws": cg.draws()}, "lod": 1 + len(cg.lod_meshes)}
        if gr.plantings.get(cell):  # vegetation look A: the cell's hedges
            meta["foliage"] = FO.manifest_rows(gr.plantings[cell])
        out[cell] = outdoor.OutdoorCell(objs, cg.bounds(), meta, lights)

    # The foliage: one empty per shape, its instances sharing the shape's mesh.
    fol = gr.foliage
    objs = []
    for shape, md in fol.shapes.items():
        tmpl = ctx.mesh_from_data(md, ctx.castle_material("castle_props"))
        tmpl.name = f"_template_{shape}"  # frees the shape's name for its holder
        holder = _link(bpy.data.objects.new(f"foliage.{shape}", None))
        objs.append(holder)
        for inst in [i for i in fol.drawn() if i.shape == shape]:
            o = _link(bpy.data.objects.new(f"foliage.{inst.id}", tmpl.data))
            o.parent = holder
            o.location = to_blender(inst.pos)
            o.rotation_euler = (0.0, 0.0, inst.yaw)
            o.scale = (inst.scale, inst.scale, inst.scale)
            objs.append(o)
        bpy.data.objects.remove(tmpl, do_unlink=True)
    out["foliage"] = outdoor.OutdoorCell(objs, fol.bounds(), {"library": ["castle_props"],
                                                              "kit": {"tris": fol.tris(), "draws": len(fol.shapes)},
                                                              "instances": len(fol.drawn()),
                                                              # vegetation look A: the grounds' trees
                                                              "foliage": FO.manifest_rows(gr.plantings["foliage"])},
                                         gpu_instances=True)

    water = plain_object(gr.lake, _plain_material(GB.WATER, pal["lake_deep"]))
    lo = [min(v[k] for v in gr.lake.verts) for k in range(3)]
    hi = [max(v[k] for v in gr.lake.verts) for k in range(3)]
    out["lake"] = outdoor.OutdoorCell([water], (lo, hi), {"kit": {"tris": gr.lake.triangles(), "draws": 1}})

    bobjs = []
    for m in gr.backdrop:
        bobjs.append(plain_object(m, _plain_material(m.material, pal[m.material])))
    pts = [v for m in gr.backdrop for v in m.verts]
    lo = [min(v[k] for v in pts) for k in range(3)]
    hi = [max(v[k] for v in pts) for k in range(3)]
    out["backdrop"] = outdoor.OutdoorCell(bobjs, (lo, hi), {"kit": {"tris": sum(m.triangles() for m in gr.backdrop),
                                                                    "draws": len(gr.backdrop)},
                                                            # vegetation look A: the forest's trees, impostors only
                                                            "foliage": FO.manifest_rows(gr.plantings["backdrop"], min_lod=3)})
    missing = sorted(set(G.P3A_RESIDENT_CELLS) - set(out))
    if missing:
        raise SystemExit(f"grounds_export: cells not built: {missing}")
    return out
