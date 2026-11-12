"""Builds the greybox castle from the layout and exports one glTF per streaming cell.

    blender -b --factory-startup --python tools/blender/greybox.py -- \\
        --layout data/world/layout.json --out client/public/assets/world

Reads `data/world/layout.json` (sim_core::layout::Layout) and the optional
greybox props next to it (`props.json`), validates the layout and checks that
every surface is reachable from a spawn, plans floors, walls with openings,
ceilings, roofs, stepped stairs and railings (layout_lib.py), and writes:

* `<out>/<cell>.glb`: the cell's geometry, merged into one mesh per material
  (so one draw call per material per cell);
* `<out>/manifest.json`: `{ "cells": [{ "id", "file", "bounds", "level" }] }`
  as specified in docs/architecture.md.

Cells listed in `data/world/castle_kit.json` are kitted (castle kit M1,
docs/art/castle-kit.md): their render polygons come from
`castle_kit/assemble.py` (one mesh per castle_* material, placeholder
materials without images, a `<cell>.collider` node for the camera, LOD1
nodes), and the manifest gains `library`, `kit`, `collider`, `lod` and
`camera`. Every other cell exports its M1.0 geometry; the props kit of any
cell is one `<cell>.castle_props` mesh on the shared props atlas (painted by
`castle_kit/textures.py`, not embedded).

Outdoors (docs/art/castle-kit.md "Exterior"): the outdoor cells (the 30
grounds cells, `lake`, `backdrop`) come from
`world/grounds_export.py` and the far silhouette (`hlod.glb`, the manifest's
`hlod`) from `world/hlod_export.py`, each when it
exists (`world/outdoor.py`); the manifest marks the outdoor cells
`resident`.

Exits non-zero if the layout is invalid. Pass `--blend <path>` to also save the
built scene for inspection.
"""

import argparse
import dataclasses
import json
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.abspath(__file__)), "world"))

import bpy  # noqa: E402

import bpyutil  # noqa: E402
import foliage as FO  # noqa: E402
import kit  # noqa: E402
import layout_lib  # noqa: E402
import outdoor  # noqa: E402
import palette  # noqa: E402
from castle_kit import assemble  # noqa: E402
from castle_kit import fire  # noqa: E402  (animated fire)
from castle_kit import grid as kit_grid  # noqa: E402
from castle_kit import schema as kit_schema  # noqa: E402
from check_layout import script_args  # noqa: E402

# glTF metres per UV unit for the world-projected greybox UVs (one tile = 2 m),
# matching the 2 m kit grid of docs/art/style-guide.md.
UV_METRES = 2.0


def to_blender(p):
    """Y-up world (x east, y up, z south) -> Blender Z-up (x, -z, y)."""
    return (p[0], -p[2], p[1])


def make_material(name: str) -> bpy.types.Material:
    color, roughness = layout_lib.MATERIALS[name]
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    strength = layout_lib.EMISSIVE.get(name)
    if strength:
        # Exported as emissiveFactor (+ KHR_materials_emissive_strength above 1).
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    mat.diffuse_color = (*color, 1.0)
    mat.roughness = roughness
    # Single-sided: exported as doubleSided = false so the client culls back faces.
    mat.use_backface_culling = True
    return mat


def world_uv(poly, normal):
    """Planar UVs from world position on the polygon's dominant plane."""
    ax = max(range(3), key=lambda k: abs(normal[k]))
    out = []
    for x, y, z in poly:
        if ax == 1:
            u, v = x, -z
        elif ax == 0:
            u, v = (-z if normal[0] > 0 else z), y
        else:
            u, v = (x if normal[2] > 0 else -x), y
        out.append((u / UV_METRES, v / UV_METRES))
    return out


def ramp_colors(mat: str, polys):
    """Per-corner RGB multipliers of the material's painted value ramp
    (palette.VALUE_RAMPS: walls darker at their base, grass deeper toward
    the lake), or None when the material has none."""
    if mat not in palette.VALUE_RAMPS:
        return None
    ys = [p[1] for poly in polys for p in poly]
    lo, hi = min(ys), max(ys)
    return [palette.ramp_factor(mat, p[1], lo, hi) for poly in polys for p in poly]


def build_mesh(name: str, polys, material: bpy.types.Material, colors=None) -> bpy.types.Object:
    verts, faces, uvs = [], [], []
    for poly in polys:
        n = layout_lib.newell_normal(poly)
        start = len(verts)
        verts.extend(to_blender(p) for p in poly)
        faces.append(list(range(start, start + len(poly))))
        uvs.extend(world_uv(poly, n))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    uv = mesh.uv_layers.new(name="UVMap")
    flat = [c for pair in uvs for c in pair]
    uv.data.foreach_set("uv", flat)
    if colors is not None:
        # Exported as COLOR_0, which glTF (and the client) multiplies into the
        # base colour. Corners are in polygon order, matching `verts`.
        attr = mesh.color_attributes.new("COLOR_0", "FLOAT_COLOR", "CORNER")
        attr.data.foreach_set("color", [c for rgb in colors for c in (*rgb, 1.0)])
        mesh.color_attributes.active_color = attr
    mesh.materials.append(material)
    mesh.validate()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    return obj


def build_scene(plan: layout_lib.Plan, skip=()) -> dict[str, list[bpy.types.Object]]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    materials = {name: make_material(name) for name in layout_lib.MATERIALS}
    by_cell: dict[str, list[bpy.types.Object]] = {}
    for cell in plan.cells():
        if cell in skip:
            continue
        coll = bpy.data.collections.new(cell)
        bpy.context.scene.collection.children.link(coll)
        for (c, mat), polys in sorted(plan.polys.items()):
            if c != cell:
                continue
            obj = build_mesh(f"{cell}.{mat}", polys, materials[mat], ramp_colors(mat, polys))
            coll.objects.link(obj)
            by_cell.setdefault(cell, []).append(obj)
    return by_cell


CASTLE_MATERIALS: dict[str, bpy.types.Material] = {}


def castle_material(name: str, textures: dict) -> bpy.types.Material:
    """A castle_* placeholder (docs/art/castle-kit.md "glTF conventions"): no image,
    the flat greybox colour it replaces; the client binds the library's
    textures by name. castle_glass glows its colour (emissive "self");
    castle_props stays unlit here, its glow is the library's emissive atlas."""
    if name in CASTLE_MATERIALS:
        return CASTLE_MATERIALS[name]
    spec = textures["materials"].get(name, {})
    color = assemble.placeholder_colour(name)
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.9
    if spec.get("emissive") == "self":
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = spec.get("emissive_strength", 1.0)
    mat.diffuse_color = (*color, 1.0)
    mat.use_backface_culling = not spec.get("double_sided", False)
    CASTLE_MATERIALS[name] = mat
    return mat


def add_kit(by_cell: dict, cell_kits: dict, textures: dict, skip=()) -> None:
    """Adds each cell's textured kit (stations, classroom dressing) as one
    mesh, `<cell>.castle_props`, on the shared props atlas: the material
    carries no image (castle kit M1: the client loads props_atlas.png once
    for every cell); the UVs are the slot table's (castle_kit/props_atlas.py)."""
    if not cell_kits:
        return
    mat = castle_material("castle_props", textures)
    for cell, ck in sorted(cell_kits.items()):
        if not ck.mesh.faces or cell in skip:
            continue
        obj = bpyutil.mesh_object(f"{cell}.castle_props", ck.mesh, {"kit": mat}, uvs=ck.uvs)
        by_cell.setdefault(cell, []).append(obj)


def mesh_from_data(md: assemble.MeshData, material) -> bpy.types.Object:
    me = bpy.data.meshes.new(md.name)
    # A node with its own origin (the hourglass sand) keeps its verts relative to it.
    o = getattr(md, "origin", None) or (0.0, 0.0, 0.0)
    me.from_pydata([to_blender((v[0] - o[0], v[1] - o[1], v[2] - o[2])) for v in md.verts], [],
                   [list(f) for f in md.faces])
    if material is not None:
        me.materials.append(material)
        uv = me.uv_layers.new(name="UVMap")
        uv.data.foreach_set("uv", [c for face in md.uvs for uv_ in face for c in uv_])
    if md.colors is not None:
        attr = me.color_attributes.new("COLOR_0", "FLOAT_COLOR", "CORNER")
        attr.data.foreach_set("color", [c for face in md.colors for rgb in face for c in (*rgb, 1.0)])
        me.color_attributes.active_color = attr
    me.validate(clean_customdata=False)
    me.update()
    obj = bpy.data.objects.new(md.name, me)
    if getattr(md, "origin", None):
        obj.location = to_blender(md.origin)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def add_kitted(by_cell: dict, cb: assemble.CellBuild, textures: dict) -> None:
    """A kitted cell's meshes (one per material), its LOD1 nodes and its
    camera collider."""
    objs = []
    extras = getattr(cb, "extras", {}) or {}
    for name, md in sorted(cb.meshes.items()):
        mat = castle_material(md.material, textures) if md.material.startswith("castle_") else make_material(md.material)
        obj = mesh_from_data(md, mat)
        # Castle kit M3 + M4 (docs/art/castle-kit.md): kit_anim (moving
        # candle groups).
        for key, value in sorted(extras.get(name, {}).items()):
            obj[key] = value
        objs.append(obj)
    for name, (md, base, dist) in sorted(cb.lod_meshes.items()):
        mat = castle_material(md.material, textures)
        obj = mesh_from_data(md, mat)
        obj["kit_lod"] = {"base": base, "distance": float(dist)}
        for key, value in sorted(extras.get(name, {}).items()):
            obj[key] = value
        objs.append(obj)
    coll = assemble.MeshData(f"{cb.cell}.collider", "", [], [], [], None)
    index = {}
    for poly in cb.collider:
        idx = []
        for p in poly:
            key = tuple(round(c, 5) for c in p)
            if key not in index:
                index[key] = len(coll.verts)
                coll.verts.append(tuple(p))
            idx.append(index[key])
        coll.faces.append(tuple(idx))
    obj = mesh_from_data(coll, None)
    obj["role"] = "camera_collider"
    objs.append(obj)
    by_cell[cb.cell] = objs


def kit_lights(cell_kits: dict, lights: list) -> list:
    """Lights the kit brings, strongest first, so that no cell lists more
    than `kit.LIGHTS_PER_CELL` lights in all."""
    out = []
    for cell, ck in sorted(cell_kits.items()):
        room = kit.LIGHTS_PER_CELL - sum(1 for lt in lights if lt.cell == cell)
        for lt in ck.lights[:max(0, room)]:
            out.append(layout_lib.Light(lt["id"], cell, tuple(lt["pos"]), lt["color"], lt["intensity"], lt["range"]))
    return out


def fire_links(lights: list, cell: str, fires: list) -> list:
    """`lights` with those of `cell` near one of its fires carrying that
    fire's flicker seed (castle_kit/fire.py `link_lights`)."""
    out = []
    for lt in lights:
        if lt.cell == cell and fires:
            probe = [{"pos": lt.pos}]
            if fire.link_lights(probe, fires):
                lt = dataclasses.replace(lt, flicker=probe[0]["flicker"])
        out.append(lt)
    return out


def export_cell(objs, path: str, extras: bool = False, gpu_instances: bool = False) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.export_scene.gltf(
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
        # Kitted cells only: the collider's role and the LOD nodes' kit_lod.
        export_extras=extras,
        # Instanced foliage as EXT_mesh_gpu_instancing (Babylon: thin instances).
        **({"export_gpu_instances": True} if gpu_instances else {}),
        # The painted value ramps (COLOR_0) of the meshes that have one.
        export_vertex_color="ACTIVE",
    )


def write_manifest(plan, levels, out_dir: str, lights=(), extra=None, bounds=None, more_cells=(), hlod=None,
                   foliage_kit=None) -> dict:
    """`extra`: per-cell castle kit additions (library, kit, collider, lod,
    camera); `bounds`: per-cell bounds overriding the plan's (kitted cells);
    `more_cells`: cells with no layout surface (the all-water grounds
    cells, `backdrop`), whose bounds `bounds` gives; `hlod`: the far
    silhouette's entry."""
    cells = []
    for cell in sorted(set(plan.cells()) | set(more_cells)):
        lo, hi = (bounds or {}).get(cell) or plan.bounds(cell)
        entry = {
            "id": cell,
            "file": f"{cell}.glb",
            "bounds": {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi]},
            "level": levels.get(cell, 0),
        }
        # Optional: point lights the client adds with the cell.
        cell_lights = [lt.to_json() for lt in lights if lt.cell == cell]
        if cell_lights:
            entry["lights"] = cell_lights
        entry.update((extra or {}).get(cell, {}))
        # Outdoor cells stay loaded wherever the player is.
        if cell in kit_grid.P3A_RESIDENT_CELLS or cell in kit_grid.P3B_RESIDENT_CELLS:
            entry["resident"] = True
        cells.append(entry)
    manifest = {"cells": cells}
    if hlod is not None:
        manifest["hlod"] = hlod
    if foliage_kit is not None:  # vegetation look A: the species the cells' `foliage` plantings draw
        manifest["foliage_kit"] = foliage_kit
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return manifest


def kit_objects(cell: str, cell_kits: dict, textures: dict) -> list:
    """A cell's props kit as greybox.py exports it (its flat-coloured
    parts per material, its textured part on the props atlas), for an export
    that builds the cell itself (outdoor.OutdoorContext.kit_objects)."""
    ck = cell_kits.get(cell)
    if ck is None:
        return []
    objs = []
    for mat, polys in sorted(ck.flats.items()):
        obj = build_mesh(f"{cell}.kit_{mat}", polys, make_material(mat), ramp_colors(mat, polys))
        bpy.context.scene.collection.objects.link(obj)
        objs.append(obj)
    if ck.mesh.faces:
        objs.append(bpyutil.mesh_object(f"{cell}.castle_props", ck.mesh,
                                        {"kit": castle_material("castle_props", textures)}, uvs=ck.uvs))
    return objs


def instance_files(out_dir: str) -> set[str]:
    """Cell files of instance layouts (`instances.json`), which this script
    does not build."""
    path = os.path.join(out_dir, "instances.json")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {c["file"] for inst in data.values() for c in inst.get("cells", [])}


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    ap = argparse.ArgumentParser(description="Greybox generator: layout.json -> per-cell .glb + manifest.json")
    ap.add_argument("--layout", default="data/world/layout.json")
    ap.add_argument("--props", default=None, help="greybox props (default: props.json next to the layout)")
    ap.add_argument("--kit", default=None, help="castle kit placements (default: castle_kit.json next to the layout)")
    ap.add_argument("--textures", default=None, help="castle texture set (default: ../art/castle_textures.json)")
    ap.add_argument("--out", default="client/public/assets/world")
    ap.add_argument("--blend", default=None, help="also save the built scene to this .blend file")
    args = ap.parse_args(script_args(sys.argv))

    layout = layout_lib.load_layout(args.layout)
    rep = layout_lib.validate(layout)
    for w in rep.warnings:
        print(f"warning: {w}")
    if not rep.ok:
        for e in rep.errors:
            print(f"error: {e}")
        print("greybox: layout invalid, nothing exported")
        sys.exit(1)

    props_path = args.props or os.path.join(os.path.dirname(args.layout), "props.json")
    kit_path = args.kit or os.path.join(os.path.dirname(args.layout), "castle_kit.json")
    textures_path = args.textures or os.path.join(os.path.dirname(args.layout), "..", "art", "castle_textures.json")
    props = layout_lib.load_props(props_path) if os.path.exists(props_path) else []
    lights = layout_lib.load_lights(props_path) if os.path.exists(props_path) else []
    entries = kit.load_kit(props_path) if os.path.exists(props_path) else []
    open_edges = layout_lib.load_open_edges(props_path) if os.path.exists(props_path) else set()
    missing = sorted(open_edges - {s.id for s in layout.surfaces})
    if missing:
        print(f"greybox: open_edges name unknown surfaces {missing}")
        sys.exit(1)
    textures = kit_schema.load_texture_set(textures_path)
    if os.path.exists(kit_path):
        kit_plan, krep = kit_schema.resolve(kit_schema.load_json(kit_path), layout, props_path, textures)
        for w in krep.warnings:
            print(f"warning: castle_kit: {w}")
        if not krep.ok:
            for e in krep.errors:
                print(f"error: castle_kit: {e}")
            print("greybox: castle_kit.json invalid, nothing exported")
            sys.exit(1)
    else:
        kit_plan = kit_schema.KitPlan({}, 16.0, 18.0, {})
    # The M1.0 greybox plan (`plan0`), the shared props atlas and, per
    # kitted cell, the castle kit build.
    builder = assemble.Builder(layout, props, entries, open_edges, kit_plan, textures, props_path, textures_path)
    builder.cast_ao = True  # Castle D1: the export casts the vertex-AO rays (castle_kit/shade.py, 19.2)
    plan = builder.plan0
    cell_kits = builder.cell_kits
    kitted = sorted(kit_plan.cells)
    unknown = sorted(({lt.cell for lt in lights} | set(cell_kits)) - set(plan.cells()))
    if unknown:
        print(f"greybox: lights or kit reference unknown cells {unknown}")
        sys.exit(1)
    for issue in layout_lib.degenerate_polygons(plan) + layout_lib.find_z_fighting(plan):
        print(f"warning: {issue}")
    # The kit's flat-coloured parts (find-spot tells) join the cell's greybox
    # meshes of the same material; its textured parts get their own mesh. In
    # kitted cells both go to castle_props (assemble.py).
    for cell, ck in cell_kits.items():
        if cell in kit_plan.cells:
            continue
        for mat, polys in ck.flats.items():
            if mat not in layout_lib.MATERIALS:
                print(f"greybox: kit in {cell} uses unknown material {mat!r}")
                sys.exit(1)
            plan.polys.setdefault((cell, mat), []).extend(polys)
    lights = list(lights) + kit_lights(cell_kits, lights)
    by_cell = build_scene(plan, skip=set(kitted))
    # The grounds export builds the
    # outdoor cells; each replaces the cell's M1.0 build.
    outdoor_cells: dict[str, outdoor.OutdoorCell] = {}
    terrain_dir = os.path.join(os.path.dirname(args.layout), "terrain")

    def outdoor_context() -> outdoor.OutdoorContext:
        with open(os.path.join(terrain_dir, "grounds.json"), encoding="utf-8") as f:
            grounds_meta = json.load(f)
        return outdoor.OutdoorContext(
            layout=layout, props=props, grounds=grounds_meta, terrain_dir=terrain_dir, textures=textures,
            castle_material=lambda name: castle_material(name, textures), make_material=make_material,
            kit_objects=lambda cell: kit_objects(cell, cell_kits, textures), mesh_from_data=mesh_from_data)

    grounds_export = outdoor.optional_module("grounds_export")
    if grounds_export is not None:
        outdoor_cells = grounds_export.build(outdoor_context())
    # The pitch export builds the
    # `pitch` cell (grid.P3B_PITCH_CELL) the same way.
    pitch_export = outdoor.optional_module("pitch_export")
    if pitch_export is not None:
        outdoor_cells.update(pitch_export.build(outdoor_context()))
    if outdoor_cells:
        for cell, oc in outdoor_cells.items():
            for obj in by_cell.pop(cell, []):
                bpy.data.objects.remove(obj, do_unlink=True)
            by_cell[cell] = list(oc.objects)
            lights = [lt for lt in lights if lt.cell != cell] + list(oc.lights)
    add_kit(by_cell, cell_kits, textures, skip=set(kitted) | set(outdoor_cells))
    builds = {}
    for cell in kitted:
        cb = builder.build(cell)
        builds[cell] = cb
        add_kitted(by_cell, cb, textures)

    # Manifest additions (docs/art/castle-kit.md "Manifest fields").
    extra: dict[str, dict] = {}
    for cell, objs in by_cell.items():
        lib = sorted({slot.material.name for o in objs for slot in o.material_slots
                      if slot.material is not None and slot.material.name.startswith("castle_")})
        if lib:
            extra.setdefault(cell, {})["library"] = lib
    for cell, cb in builds.items():
        e = extra.setdefault(cell, {})
        e["kit"] = {"tris": cb.triangles(), "draws": cb.draws()}
        e["collider"] = True
        if cb.lod_meshes:
            e["lod"] = len(cb.lod_meshes)
    for cell in plan.cells():
        cmax = kit_plan.camera_max(cell)
        if abs(cmax - kit_plan.camera_default) > 1e-9:
            extra.setdefault(cell, {})["camera"] = {"max": cmax}
    # Castle kit M2 (Engine): cell visibility (docs/art/castle-kit.md "Cell visibility (`sees`)").
    from castle_kit import visibility
    # Cells with no surface (the all-water grounds cells, the backdrop) are seen like the lake.
    placed_and_more = list(plan.cells()) + sorted(set(outdoor_cells) - set(plan.cells()))
    for cell, seen in sorted(visibility.add_unplaced(visibility.cell_sees(layout), placed_and_more).items()):
        if seen is not None and cell in plan.cells():
            extra.setdefault(cell, {})["sees"] = seen
    # Castle kit M3 (Great Hall): seats for the sit emote (docs/art/castle-kit.md), each
    # row's tenth number whether it is two-sided (world/seat_sides.py, from the layout's solids).
    import seat_sides
    sides = seat_sides.Sides.from_file(args.layout)
    for cell, cb in builds.items():
        if getattr(cb, "seats", None):
            extra.setdefault(cell, {})["seats"] = sides.mark(cb.seats, cb.seat_backs)
        # Castle M5b (Furniture): floor seats (rugs, hearth steps; export only, docs/art/castle-kit.md).
        if getattr(cb, "floor_seats", None):
            extra.setdefault(cell, {})["floor_seats"] = cb.floor_seats
        # ... and the lights its hero pieces bring (the fireplace), within kit.LIGHTS_PER_CELL.
        for lt in getattr(cb, "lights", []):
            if lt["id"] not in {x.id for x in lights} and sum(1 for x in lights if x.cell == cell) < kit.LIGHTS_PER_CELL:
                lights.append(layout_lib.Light(lt["id"], cell, tuple(lt["pos"]), lt["color"], lt["intensity"], lt["range"]))
        # Animated fire (docs/architecture.md "Rendering"): the cell's flames, and its lights
        # near a fire (the pieces' and props.json's) flicker with it.
        if getattr(cb, "fire", None):
            extra.setdefault(cell, {})["fire"] = cb.fire
            lights = fire_links(lights, cell, cb.fire.get("fires", []))
    bounds = {cell: cb.bounds() for cell, cb in builds.items()}
    for cell, oc in outdoor_cells.items():
        extra.setdefault(cell, {}).update(oc.manifest)
        bounds[cell] = oc.bounds
    # Vegetation look A (world/foliage.py, docs/architecture.md "Rendering"): each cell's
    # plantings of the foliage kit (the outdoor cells' come with their manifest above).
    for cell, cb in builds.items():
        if cb.foliage:
            extra.setdefault(cell, {})["foliage"] = FO.manifest_rows(cb.foliage)
    for cell, ck in cell_kits.items():
        if ck.foliage and cell not in builds and cell not in outdoor_cells:
            extra.setdefault(cell, {})["foliage"] = FO.manifest_rows(ck.foliage)

    os.makedirs(args.out, exist_ok=True)
    saved = 0
    for cell, objs in by_cell.items():
        export_cell(objs, os.path.join(args.out, f"{cell}.glb"), extras=cell in builds or cell in outdoor_cells,
                    gpu_instances=cell in outdoor_cells and outdoor_cells[cell].gpu_instances)
        # Castle kit M2 (Engine): COLOR_0 as normalized bytes.
        saved += visibility.pack_color0(os.path.join(args.out, f"{cell}.glb"))
    # The far silhouette's chunks.
    hlod = None
    hlod_export = outdoor.optional_module("hlod_export")
    if hlod_export is not None:
        exterior_path = os.path.join(os.path.dirname(args.layout), "exterior.json")
        with open(exterior_path, encoding="utf-8") as f:
            exterior = json.load(f)

        def export_hlod(objs, path):
            export_cell(objs, path, extras=True)
            visibility.pack_color0(path)

        hlod = hlod_export.build(outdoor.HlodContext(
            layout=layout, exterior=exterior, kit_plan=kit_plan, textures=textures,
            castle_material=lambda name: castle_material(name, textures), mesh_from_data=mesh_from_data,
            export=export_hlod, out_dir=args.out))
    more = sorted(set(outdoor_cells) - set(plan.cells()))
    import foliage_export
    foliage_export.export_kit(args.out, lambda name: castle_material(name, textures),
                              lambda objs, path: export_cell(objs, path), visibility.pack_color0)
    manifest = write_manifest(plan, layout_lib.cell_levels(layout, props), args.out, lights, extra, bounds,
                              more_cells=more, hlod=hlod, foliage_kit=FO.kit_manifest())
    # Drop files of cells that no longer exist (instance cells, listed in
    # instances.json by tools/blender/world/build_dorms.py, are kept; so is hlod.glb
    # while the manifest lists it).
    current = {c["file"] for c in manifest["cells"]} | instance_files(args.out) | {FO.KIT_FILE}
    if hlod is not None:
        current.add(hlod.get("file", kit_grid.P3A_HLOD_FILE))
    for name in os.listdir(args.out):
        if name.endswith(".glb") and name not in current:
            os.remove(os.path.join(args.out, name))

    if args.blend:
        # art/world/ is not in git (the saved scenes are generated): make it in a fresh clone.
        os.makedirs(os.path.dirname(os.path.abspath(args.blend)), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.blend))

    total_tris = sum(len(p) - 2 for (c, _), ps in plan.polys.items() if c not in builds for p in ps)
    total_tris += sum(cb.triangles() for cb in builds.values())
    draws = sum(1 for (c, _) in plan.polys if c not in builds) + sum(cb.draws() for cb in builds.values())
    print(f"greybox: {len(by_cell)} cells, {draws} meshes (draw calls), {total_tris} triangles -> {args.out}; COLOR_0 as bytes saved {saved} bytes")
    kit_tris = {cell: ck.mesh.triangle_count() for cell, ck in cell_kits.items() if cell not in builds}
    if cell_kits:
        print(f"kit: {sum(kit_tris.values())} textured triangles in {len(kit_tris)} cells on castle_props "
              f"({builder.atlas.size} px, {len(builder.atlas.slots)} slots, key {builder.atlas.key()})")
    for cell in sorted(by_cell):
        if cell in builds:
            cb = builds[cell]
            print(f"  {cell:22s} {cb.draws():2d} meshes {cb.triangles():6d} tris  castle kit "
                  f"({', '.join(m.material for m in cb.meshes.values())}; collider {cb.collider_triangles()} tris)")
            sl, sh = cb.slice_stats, cb.shade_stats
            if sl is not None and sh is not None:  # Castle D1: the loops and the bake
                print(f"  {'':22s} D1 loops +{sl.added} tris (budget {sl.budget}); AO {sh.rays} rays x {sh.vertices} "
                      f"vertices, R {sh.radius} m, mean {sh.ao_mean}, {sh.seconds} s")
            continue
        tris = sum(len(p) - 2 for (c, _), ps in plan.polys.items() if c == cell for p in ps)
        extra_s = f" + {kit_tris[cell]:6d} kit" if kit_tris.get(cell) else ""
        print(f"  {cell:22s} {len(by_cell[cell]):2d} meshes {tris:6d} tris{extra_s}")


if __name__ == "__main__":
    main()
