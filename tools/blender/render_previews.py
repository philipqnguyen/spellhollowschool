"""Renders review images of the exported greybox (EEVEE, fast settings).

    blender -b --factory-startup --python tools/blender/render_previews.py -- \\
        --world client/public/assets/world --layout data/world/layout.json --out art/renders/greybox

Imports the per-cell .glb files listed in the manifest (so the renders show
exactly what the client loads) and writes:

* `plan_level<N>.png`: a top-down section per floor level. Geometry above the
  cut height (level floor + 4.2 m) is removed and the cut walls are drawn as
  dark bands, with room names and landmarks labelled.
* `view_<name>.png`: eye-level and aerial perspective views.

Use `--only plan` / `--only views` or `--views name,name` to render a subset.
"""

import argparse
import json
import math
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

import layout_lib  # noqa: E402
from check_layout import script_args  # noqa: E402
from greybox import to_blender  # noqa: E402

# Height of the plan section above each level's floor (below 8.4 m corridor ceilings).
PLAN_CUT = 4.2
SECTION_WIDTH = 0.35

# Perspective views: name -> (eye, target, lens mm), in world Y-up coordinates.
VIEWS = {
    # Castle coordinates after the castle kit's scale pass (M1.0).
    "entrance_hall": ((16, 1.7, 39.0), (16, 6.0, 0), 16),
    "entrance_hall_to_great_hall": ((3, 1.7, 32.5), (34, 4.0, 20), 16),
    "great_hall": ((35.5, 1.7, 20), (100, 4.0, 20), 18),
    "marble_staircase_top": ((16, 10.7, 0.5), (16, 1.0, 35), 16),
    "first_floor_corridor": ((38.5, 10.7, -8), (-28, 10.5, -8), 18),
    "grand_staircase_base": ((-31.5, 1.7, 19), (-50, 20, -8), 12),
    "grand_staircase_landing": ((-31.5, 10.7, 19), (-55, 20, -12), 14),
    "grand_staircase_top": ((-31.5, 28.7, -15), (-55, 6, 14), 14),
    "courtyard": ((16, 1.7, 87), (16, 8, 40), 18),
    "grounds_from_lake": ((52, -5.0, 236), (16, -1, 100), 24),
    "aerial": ((150, 100, 190), (5, 0, 25), 28),
    # The Grand Staircase up to level 7 and the four common rooms.
    "aerial_west": ((-200, 120, 96), (-66, 31, -14), 30),
    "grand_staircase_upper": ((-32, 37.7, 19), (-53, 65, -13), 12),
    "grand_staircase_l7": ((-31.5, 64.7, 19), (-48.5, 64.5, -16), 16),
    "l7_lantern_keeper": ((-41, 64.7, -19), (-48.5, 64.6, -28), 20),
    "common_room_house_a": ((-40, 64.7, -31.5), (-60, 64.6, -43), 14),
    "west_tower_stair": ((-93.2, 28.7, 2), (-105.3, 31.4, -2), 13),
    "west_tower_door": ((-95.3, 56.6, -0.5), (-100, 56.4, -4), 18),
    "common_room_house_c": ((-91, 55.7, -7.5), (-105, 56.2, -28), 14),
    "dungeon_steps": ((7, 1.7, 0.6), (7, -7.5, 25.7), 16),
    "dungeon_corridor": ((7.5, -7.3, 23), (7.5, -7.5, 50), 18),
    "common_room_house_d": ((-6.5, -16.3, 70.5), (17.5, -16.5, 88), 14),
    "kitchen_corridor": ((22.5, -7.3, 26.5), (61, -7.3, 26.5), 18),
    "common_room_house_b": ((64.5, -7.4, 28.5), (87.5, -7.6, 14.6), 14),
    # The Owl Exchange counter, the crafting stations, the
    # Potions classroom and a few find-spot tells.
    "owl_exchange": ((10, 1.8, 36), (0.5, 1.8, 27), 18),
    "owl_exchange_close": ((5.5, 1.6, 27.5), (1.2, 1.5, 30.5), 24),
    "knitting_corner_house_a": ((-41.4, 64.5, -33.7), (-38, 63.9, -32.3), 22),
    "transfiguration_table_house_a": ((-41.2, 64.6, -49.2), (-38, 63.7, -47.8), 22),
    "knitting_corner_house_b": ((65, -7.5, 26.8), (66.6, -8.2, 30), 22),
    "transfiguration_table_house_d": ((21.3, -16.4, 86), (24.5, -17.2, 87.2), 22),
    "potions_classroom": ((0.1, -7.0, 46.4), (-14.6, -8.6, 28.5), 15),
    "potions_classroom_front": ((-9.4, -7.3, 46.2), (-9.5, -8.0, 26), 17),
    "potions_shelves": ((-14.8, -7.4, 36.5), (-20, -8.0, 40), 20),
    "tell_entrance_hall_stairs": ((10.2, 1.2, 5.2), (11.6, 0.3, 1.6), 24),
    "tell_dungeon_corridor": ((5.4, -7.9, 42.0), (3.4, -8.6, 45.5), 22),
    "tell_l5_tapestry": ((-80.5, 46.0, 1.4), (-83, 45.3, -1.9), 24),
    # The Dueling Club stage in the Great Hall and the
    # third-floor Charms classroom door.
    "dueling_club": ((54, 2.6, 30), (72, 1.2, 19), 18),
    "dueling_club_stage": ((55.8, 2.6, 20), (76, 1.6, 20), 16),
    "dueling_club_side": ((67, 2.2, 27.5), (67, 1.5, 16), 16),
    "charms_door": ((-76.6, 28.6, 2.7), (-80, 29.6, -2), 16),
}

# Blender watts per unit of a manifest light's `intensity` (see tools/blender/README.md).
LIGHT_WATTS = 25.0


def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    eevee = scene.eevee
    eevee.taa_render_samples = 16
    if hasattr(eevee, "use_shadows"):
        eevee.use_shadows = True
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("world")
    scene.world = world
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (0.62, 0.70, 0.82, 1.0)
    bg.inputs["Strength"].default_value = 0.6


def import_world(world_dir: str) -> list[bpy.types.Object]:
    with open(os.path.join(world_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    for cell in manifest["cells"]:
        bpy.ops.import_scene.gltf(filepath=os.path.join(world_dir, cell["file"]))
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def emission_material(name: str, color, strength: float = 1.0) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for n in list(nodes):
        nodes.remove(n)
    out = nodes.new("ShaderNodeOutputMaterial")
    em = nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1.0)
    em.inputs["Strength"].default_value = strength
    mat.node_tree.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def add_sun(rotation=(math.radians(50), math.radians(10), math.radians(35)), energy=3.5) -> None:
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = energy
    sun.angle = math.radians(3)
    obj = bpy.data.objects.new("sun", sun)
    obj.rotation_euler = rotation
    bpy.context.scene.collection.objects.link(obj)


def hex_rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))


def add_cell_lights(lights) -> None:
    """The manifest's point lights, as the client places them."""
    for lt in lights:
        light = bpy.data.lights.new(f"cell_{lt.id}", "POINT")
        light.energy = lt.intensity * LIGHT_WATTS
        light.color = hex_rgb(lt.color)
        light.shadow_soft_size = 0.3
        light.use_shadow = False
        if hasattr(light, "use_custom_distance"):
            light.use_custom_distance = True
            light.cutoff_distance = lt.range
        obj = bpy.data.objects.new(light.name, light)
        obj.location = to_blender(lt.pos)
        bpy.context.scene.collection.objects.link(obj)


def add_interior_lights(layout: layout_lib.Layout, skip_cells=frozenset()) -> None:
    """Point lights under the ceilings of roofed spaces, roughly every 12 m.
    Cells with their own manifest lights (common rooms) keep their mood."""
    for s in layout.surfaces:
        if not s.roofed or s.kind == "door" or s.area < 12 or s.cell in skip_cells:
            continue
        nx = max(1, round((s.x1 - s.x0) / 12))
        nz = max(1, round((s.z1 - s.z0) / 12))
        y = min(s.ceil_y - 0.8, s.floor_max + 7.0)
        power = 350 * (s.area / (nx * nz)) / 100
        for i in range(nx):
            for j in range(nz):
                x = s.x0 + (i + 0.5) * (s.x1 - s.x0) / nx
                z = s.z0 + (j + 0.5) * (s.z1 - s.z0) / nz
                light = bpy.data.lights.new(f"lamp_{s.id}_{i}_{j}", "POINT")
                light.energy = power
                light.shadow_soft_size = 1.0
                # Dozens of shadowed lamps overflow EEVEE's shadow pool; the sun
                # keeps shadows and these just fill the interiors.
                light.use_shadow = False
                obj = bpy.data.objects.new(light.name, light)
                obj.location = to_blender((x, y, z))
                bpy.context.scene.collection.objects.link(obj)


def add_camera(eye, target, lens=None, ortho_scale=None) -> bpy.types.Object:
    cam = bpy.data.cameras.new("cam")
    cam.clip_start = 0.05
    cam.clip_end = 2000
    if ortho_scale is not None:
        cam.type = "ORTHO"
        cam.ortho_scale = ortho_scale
    else:
        cam.lens = lens
    obj = bpy.data.objects.new("cam", cam)
    bpy.context.scene.collection.objects.link(obj)
    e, t = Vector(to_blender(eye)), Vector(to_blender(target))
    obj.location = e
    obj.rotation_euler = (t - e).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = obj
    return obj


def render(path: str, width: int, height: int) -> None:
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print(f"rendered {path}")


def section_cut(objs, cut_z: float) -> list[tuple[Vector, Vector]]:
    """Removes geometry above `cut_z` (Blender Z) and returns the cut edges."""
    segments = []
    for obj in objs:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.transform(obj.matrix_world)
        res = bmesh.ops.bisect_plane(
            bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-4,
            plane_co=(0, 0, cut_z), plane_no=(0, 0, 1), clear_outer=True,
        )
        for e in res["geom_cut"]:
            if isinstance(e, bmesh.types.BMEdge):
                a, b = e.verts[0].co.copy(), e.verts[1].co.copy()
                if (a - b).length > 1e-3:
                    segments.append((a, b))
        bm.transform(obj.matrix_world.inverted())
        bm.to_mesh(obj.data)
        bm.free()
    return segments


def add_section_bands(segments, z: float, mat) -> None:
    verts, faces = [], []
    for a, b in segments:
        d = (b - a).normalized()
        n = Vector((-d.y, d.x, 0)) * (SECTION_WIDTH / 2)
        a2 = a - d * (SECTION_WIDTH / 2)
        b2 = b + d * (SECTION_WIDTH / 2)
        k = len(verts)
        for p in (a2 - n, b2 - n, b2 + n, a2 + n):
            verts.append((p.x, p.y, z))
        faces.append((k, k + 1, k + 2, k + 3))
    mesh = bpy.data.meshes.new("section")
    mesh.from_pydata(verts, [], faces)
    mesh.materials.append(mat)
    obj = bpy.data.objects.new("section", mesh)
    bpy.context.scene.collection.objects.link(obj)


def add_label(text: str, pos_world, size: float, mat) -> None:
    curve = bpy.data.curves.new("label", "FONT")
    curve.body = text
    curve.size = size
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    obj = bpy.data.objects.new("label", curve)
    obj.data.materials.append(mat)
    obj.location = to_blender(pos_world)
    obj.visible_shadow = False
    bpy.context.scene.collection.objects.link(obj)


def level_floor(layout, level: int) -> float:
    ys = [s.y for s in layout.surfaces if s.level == level and s.kind not in ("grounds", "stair")]
    return min(ys) if ys else 0.0


def render_plans(args, layout) -> None:
    if args.region:
        x0, z0, x1, z1 = (float(v) for v in args.region.split(","))
    else:
        castle = [s for s in layout.surfaces if s.kind != "grounds"]
        x0, x1 = min(s.x0 for s in castle) - 6, max(s.x1 for s in castle) + 6
        z0, z1 = min(s.z0 for s in castle) - 6, max(s.z1 for s in castle) + 6
    width_m, depth_m = x1 - x0, z1 - z0
    px_w = 2000
    px_h = int(px_w * depth_m / width_m)
    suffix = f"_{args.tag}" if args.tag else ""
    levels = [int(v) for v in args.levels.split(",")] if args.levels else sorted({s.level for s in layout.surfaces})
    for level in levels:
        reset_scene()
        objs = import_world(args.world)
        cut = level_floor(layout, level) + PLAN_CUT
        segments = section_cut(objs, cut)
        add_section_bands(segments, cut + 0.02, emission_material("section", (0.08, 0.05, 0.05)))
        label_mat = emission_material("label", (0.0, 0.0, 0.0))
        mark_mat = emission_material("marker", (0.85, 0.1, 0.1), 2.0)
        named: dict[str, layout_lib.Surface] = {}
        for s in layout.surfaces:
            if s.level == level and s.kind != "door":
                if s.name not in named or s.area > named[s.name].area:
                    named[s.name] = s
        for name, s in named.items():
            size = max(0.9, min(2.2, (s.x1 - s.x0) / max(8, len(name)) * 1.4))
            add_label(name, ((s.x0 + s.x1) / 2, cut + 0.05, (s.z0 + s.z1) / 2), size, label_mat)
        for lm in layout.landmarks:
            surf = layout_lib.spawn_surface(layout, lm["pos"])
            if surf is None or surf.level != level:
                continue
            x, _, z = lm["pos"]
            add_label("x " + lm["name"], (x, cut + 0.05, z + 1.2), 0.7, mark_mat)
        add_sun(rotation=(math.radians(20), 0, math.radians(30)), energy=3.0)
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        add_camera((cx, cut + 200, cz), (cx, cut, cz - 1e-3), ortho_scale=width_m)
        render(os.path.join(args.out, f"plan_level{level}{suffix}.png"), px_w, px_h)


def load_cell_lights(layout_path: str, world_dir: str | None = None):
    """The cells' lights as the client gets them: the world manifest's (which
    adds the kit's, see greybox.py), else props.json's."""
    manifest = os.path.join(world_dir, "manifest.json") if world_dir else None
    if manifest and os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as f:
            cells = json.load(f)["cells"]
        return [layout_lib.Light(lt["id"], c["id"], tuple(lt["pos"]), lt["color"], lt["intensity"], lt["range"])
                for c in cells for lt in c.get("lights", [])]
    props_path = os.path.join(os.path.dirname(layout_path), "props.json")
    return layout_lib.load_lights(props_path) if os.path.exists(props_path) else []


def render_views(args, layout, names) -> None:
    reset_scene()
    import_world(args.world)
    add_sun()
    lights = load_cell_lights(args.layout, args.world)
    moody = {lt.cell for lt in lights if lt.cell.startswith("common_room_") or lt.cell == "dungeon_classroom"}
    add_interior_lights(layout, moody)
    add_cell_lights(lights)
    for name in names:
        eye, target, lens = VIEWS[name]
        add_camera(eye, target, lens=lens)
        render(os.path.join(args.out, f"view_{name}.png"), 1280, 800)


def main() -> None:
    ap = argparse.ArgumentParser(description="Render greybox review images")
    ap.add_argument("--world", default="client/public/assets/world")
    ap.add_argument("--layout", default="data/world/layout.json")
    ap.add_argument("--out", default="art/renders/greybox")
    ap.add_argument("--only", choices=("plan", "views"), default=None)
    ap.add_argument("--views", default=None, help="comma-separated subset of view names")
    ap.add_argument("--levels", default=None, help="comma-separated subset of plan levels")
    ap.add_argument("--region", default=None, help="plan crop in world metres: x0,z0,x1,z1")
    ap.add_argument("--tag", default=None, help="suffix for cropped plan file names")
    args = ap.parse_args(script_args(sys.argv))
    os.makedirs(args.out, exist_ok=True)
    layout = layout_lib.load_layout(args.layout)
    if args.only in (None, "plan"):
        render_plans(args, layout)
    if args.only in (None, "views"):
        names = args.views.split(",") if args.views else list(VIEWS)
        render_views(args, layout, names)


if __name__ == "__main__":
    main()
