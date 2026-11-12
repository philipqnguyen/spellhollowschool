"""Builds the instance rooms (the Charms classroom and the duel
arena, `school_rooms.py`) and the class rooms built from castle-kit
pieces (`class_potions`, `class_classroom`: `class_rooms.py` through the
instance-room kit adapter `instance_kit.py`) and exports them for the
client, like the dorm rooms (`build_dorms.py`).

    blender -b --factory-startup --python tools/blender/world/build_school.py -- \\
        [--render] [--people] [--only class_charms]

For each room it writes:

* `client/public/assets/instances/<layout id>.glb`: the room, one mesh, one
  primitive per material, painted textures embedded;
* `client/public/assets/world/<cell>.glb`: the same file under its cell id;
* entries merged into `client/public/assets/instances/manifest.json` and
  `client/public/assets/world/instances.json` (the contract the client
  reads: cells, lights most important first, ambient), plus, for the
  classroom, `seats` (`[{ pos, yaw }]`: hips over a bench, for the
  `desk_rest` clip);
* `client/public/assets/instances/school.json`: this step's own manifest
  (the prepare step's freshness stamp), with bounds, triangles and draw
  calls per room;
* `art/world/<cell>.blend` (unless `--blend-dir ""`);
* The kitted rooms: one mesh per castle_* material with the D1
  vertex-AO bake in a greyscale `COLOR_0` (normalized bytes), the castle
  textures embedded from `<assets>/world/textures/` (built by the prepare
  step before this one; the props atlas must be the castle export's), no
  camera collider and no LOD1 nodes; their seats from the seat pieces;
* with `--render`: review images in `art/renders/school/`; `--people`
  also stands mannequins at the seats, spawns and the professor's spot
  (from the exported body, when it exists) for scale.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import bpy  # noqa: E402

import bpyutil  # noqa: E402
from castle_kit import fire  # noqa: E402  (animated fire)
import class_rooms  # noqa: E402
import instance_kit  # noqa: E402
import review  # noqa: E402
import school_rooms  # noqa: E402
import seat_sides  # noqa: E402
import solids  # noqa: E402
from build_dorms import make_materials, uv_scales  # noqa: E402
from check_layout import script_args  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MANIFEST_VERSION = 1

# Rooms under the open sky (the client draws the sky and the castle's
# night there instead of a room's ambient; it read them from its own
# OPEN_SKY_LAYOUTS constant before). school.json marks each `"open_sky": true`.
OPEN_SKY_ROOMS = ("class_astronomy", "class_paddock", "class_pitch")  # The Flying class


def build_one(room: school_rooms.Room, args) -> dict:
    bpyutil.reset_scene()
    mats = make_materials(room)
    obj = bpyutil.mesh_object(room.cell, room.mesh, mats, uv_scale=uv_scales(room))
    inst_path = os.path.join(args.instances, f"{room.layout_id}.glb")
    bpyutil.export_glb([obj], inst_path)
    os.makedirs(args.world, exist_ok=True)
    shutil.copyfile(inst_path, os.path.join(args.world, f"{room.cell}.glb"))
    info = bpyutil.glb_info(inst_path)
    lo, hi = room.mesh.bounds()
    entry = {
        "file": f"{room.layout_id}.glb",
        "cell": room.cell,
        "bounds": {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi]},
        "triangles": info["triangles"],
        "draw_calls": info["draw_calls"],
        "lights": [dict(lt) for lt in room.lights],
        "ambient": room.ambient,
    }
    if room.seats:
        # Each says whether it is two-sided (world/seat_sides.py, from the room's committed solids).
        entry["seats"] = seat_sides.Sides.from_file(solids.layout_path(room.layout_id)).mark_room(room.seats)
    # Animated fire (docs/architecture.md): the room's candles and sconces; lights at a fire flicker with it.
    room_fire = fire.noted(room.mesh)
    if room_fire:
        entry["fire"] = room_fire
        fire.link_lights(entry["lights"], room_fire.get("fires", []))
    for lt in room.lights:
        review.point_light(f"light_{lt['id']}", lt["pos"], lt["color"], lt["intensity"])
    if args.blend_dir:
        os.makedirs(args.blend_dir, exist_ok=True)
        bpy.context.preferences.filepaths.save_version = 0  # no .blend1 backups in the art repo
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(os.path.abspath(args.blend_dir), f"{room.cell}.blend"))
    if args.render:
        if args.people:
            add_people(room, args.assets)
        render_views(room, args.renders)
    print(f"{room.layout_id}: {info['triangles']} triangles, {info['draw_calls']} draw calls, {info['images']} images -> {inst_path}")
    return entry


def kit_material(name: str, tex: dict) -> bpy.types.Material:
    """A castle_* material with its texture library images embedded (the
    client converts it by name to the castle's toon row, which reads the
    baked COLOR_0 as shade)."""
    img = bpy.data.images.load(tex["base"], check_existing=True)
    eimg = bpy.data.images.load(tex["emissive"], check_existing=True) if "emissive" in tex else None
    return bpyutil.principled(name, (1.0, 1.0, 1.0), img, 0.9, emissive=tex["emissive_strength"] if eimg else 0.0,
                              emissive_image=eimg, cull=not tex["double_sided"])


# The instance rooms whose manifest entry carries `floor_seats` (set by build_wishing_room.py).
FLOOR_SEAT_ROOMS: set = set()


def build_kit_room(room: instance_kit.KitRoom, args) -> dict:
    """A class room built from castle-kit pieces (instance_kit.py)."""
    from castle_kit import visibility
    import greybox

    bpyutil.reset_scene()
    cb, b = instance_kit.build(room, cast_ao=True)
    rep = instance_kit.lint(room, (cb, b))
    for e in rep.errors:
        print(f"{room.layout_id}: lint error: {e}")
    if rep.errors:
        raise SystemExit(f"{room.layout_id}: the castle kit lint failed")
    tex_dir = os.path.join(args.assets, "world", "textures")
    textures = instance_kit.texture_files(tex_dir, [md.material for md in cb.meshes.values()])
    key = textures.get("castle_props", {}).get("key")
    if key is not None and key != b.atlas.key():
        raise SystemExit(f"{room.layout_id}: the exported props atlas ({key}) is not this build's ({b.atlas.key()}): "
                         "export the castle textures first")
    objs = [greybox.mesh_from_data(md, kit_material(md.material, textures[md.material]))
            for _, md in sorted(cb.meshes.items())]
    inst_path = os.path.join(args.instances, f"{room.layout_id}.glb")
    os.makedirs(args.instances, exist_ok=True)
    greybox.export_cell(objs, inst_path, extras=False)
    visibility.pack_color0(inst_path)
    os.makedirs(args.world, exist_ok=True)
    shutil.copyfile(inst_path, os.path.join(args.world, f"{room.cell}.glb"))
    info = bpyutil.glb_info(inst_path)
    lo, hi = cb.bounds()
    entry = {
        "file": f"{room.layout_id}.glb",
        "cell": room.cell,
        "bounds": {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi]},
        "triangles": info["triangles"],
        "draw_calls": info["draw_calls"],
        "lights": [dict(lt) for lt in room.lights],
        "ambient": room.ambient,
    }
    seats = instance_kit.seats(cb, seat_sides.Sides.from_file(solids.layout_path(room.layout_id)))
    if seats:
        entry["seats"] = seats
    if cb.foliage:  # vegetation look A: the room's trees and hedges, drawn from the castle's foliage kit
        import foliage as FO
        entry["foliage"] = FO.manifest_rows(cb.foliage)
    # Animated fire (docs/architecture.md): the room's flames; its lights at a fire flicker with it.
    if cb.fire:
        entry["fire"] = cb.fire
        fire.link_lights(entry["lights"], cb.fire.get("fires", []))
    # A room's rugs and floor cushions (the Gathering Room), as the castle cells' floor_seats;
    # only for the rooms build_wishing_room.py names (the class rooms' rugs stay plain floor).
    if room.layout_id in FLOOR_SEAT_ROOMS and getattr(cb, "floor_seats", None):
        entry["floor_seats"] = cb.floor_seats
    sh = cb.shade_stats
    print(f"{room.layout_id}: {info['triangles']} triangles, {info['draw_calls']} draw calls, {info['images']} images, "
          f"AO {sh.rays} rays x {sh.vertices} vertices in {sh.seconds} s -> {inst_path}")
    for lt in room.lights:
        review.point_light(f"light_{lt['id']}", lt["pos"], lt["color"], lt["intensity"])
    if args.blend_dir:
        os.makedirs(args.blend_dir, exist_ok=True)
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(os.path.abspath(args.blend_dir), f"{room.cell}.blend"))
    if args.render:
        shade_for_review(objs)
        if args.people:
            add_people(room, args.assets)
        render_views(room, args.renders)
    return entry


def build_prop(pid: str, fn, args) -> dict:
    """One animated class prop (class_rooms.CLASS_PROPS: a plant
    or a creature). `fn()` builds it in an empty scene and returns its
    objects: the origin at the prop's base, facing +z (the students), palette
    colours only, greyscale baked shade. Exported alone to
    `instances/props/<pid>.glb`; the client stands it at the room layout's
    landmark `<pid>` and moves it (no rig, no glTF animation)."""
    bpyutil.reset_scene()
    objs = fn()
    path = os.path.join(args.instances, "props", f"{pid}.glb")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpyutil.export_glb(objs, path)
    info = bpyutil.glb_info(path)
    print(f"{pid}: {info['triangles']} triangles, {info['draw_calls']} draw calls -> {path}")
    return {"file": f"props/{pid}.glb", "triangles": info["triangles"], "draw_calls": info["draw_calls"]}


def shade_for_review(objs) -> None:
    """After the export: multiplies each material's base colour by the baked
    COLOR_0 so the review stills show the shade the client draws."""
    for o in objs:
        for slot in o.material_slots:
            mat = slot.material
            if mat is None or mat.get("review_shade"):
                continue
            nt = mat.node_tree
            bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
            link = next((lk for lk in nt.links if lk.to_socket == bsdf.inputs["Base Color"]), None)
            if link is None:
                continue
            attr = nt.nodes.new("ShaderNodeVertexColor")
            attr.layer_name = "COLOR_0"
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            src = link.from_socket
            nt.links.remove(link)
            nt.links.new(src, mix.inputs["A"])
            nt.links.new(attr.outputs["Color"], mix.inputs["B"])
            nt.links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
            mat["review_shade"] = 1


def render_views(room, out_dir: str) -> None:
    world = getattr(room, "review_world", None)  # A room open to the night sky
    review.setup(*(world or (room.ambient["sky"], room.ambient["intensity"] * 0.7)))
    os.makedirs(out_dir, exist_ok=True)
    for name, (eye, target, lens) in room.views.items():
        review.camera(eye, target, lens)
        review.render(os.path.join(out_dir, f"{room.cell}_{name}.png"), 1280, 800)


def add_people(room, assets: str) -> None:
    """Mannequins (the exported body_a, grey) at the seats, spawns and the
    professor's spot, to judge scale and sight lines in the review renders."""
    body = os.path.join(assets, "characters", "body_a.glb")
    if not os.path.exists(body):
        print(f"--people: {body} missing (build the characters first)")
        return
    grey = bpyutil.principled("mannequin", "#B8BCC8", None, 0.7)
    # Castle D1: seated mannequins root at the seat's top (the seated clips put the
    # sit bones there; the seats' y is the tier floor, as the client's ROOM_SEAT_HEIGHT).
    seats = getattr(room, "seats", None) or []
    spots = [([s["pos"][0], s["pos"][1] + school_rooms.SEAT_HEIGHT, s["pos"][2]], s["yaw"], "desk_rest")
             for s in seats[::3]]
    spots += [(s["pos"], s["yaw"], "idle") for s in room.layout.get("spawns", [])]
    for lm in room.layout.get("landmarks", []):
        if lm["id"] == "class_professor":
            spots.append((lm["pos"], 0.0, "professor_demonstrate"))
    for pos, yaw, clip in spots:
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=body)
        new = [o for o in bpy.data.objects if o not in before]
        arm = next(o for o in new if o.type == "ARMATURE")
        for o in new:
            if o.type == "MESH":
                o.hide_render = o.get("lod", 0) == 1 or o.name.endswith("_lod1")
                o.data.materials.clear()
                o.data.materials.append(grey)
        arm.location = bpyutil.to_blender(pos)
        arm.rotation_mode = "XYZ"
        arm.rotation_euler = (0.0, 0.0, yaw)
        act = next((a for a in bpy.data.actions if a.name == clip or a.name.startswith(clip + "_")
                    or a.name.startswith(clip + ".")), None)
        if act is not None:
            ad = arm.animation_data or arm.animation_data_create()
            for t in ad.nla_tracks:
                t.mute = True
            ad.action = act
            if hasattr(ad, "action_slot") and act.slots:
                ad.action_slot = act.slots[0]
    bpy.context.scene.frame_set(10)


def read_json(path: str, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    ap = argparse.ArgumentParser(description="Build the class and arena instance rooms")
    ap.add_argument("--assets", default=os.path.join(REPO, "client", "public", "assets"))
    ap.add_argument("--instances", default=None)
    ap.add_argument("--world", default=None)
    ap.add_argument("--blend-dir", default=os.path.join(REPO, "art", "world"))
    ap.add_argument("--renders", default=os.path.join(REPO, "art", "renders", "school"))
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--people", action="store_true")
    ap.add_argument("--only", default=None, help="comma-separated layout ids")
    args = ap.parse_args(script_args(sys.argv))
    args.instances = args.instances or os.path.join(args.assets, "instances")
    args.world = args.world or os.path.join(args.assets, "world")
    rooms = school_rooms.build_all()
    kit_rooms = class_rooms.build_all()  # Built from castle-kit pieces
    wanted = args.only.split(",") if args.only else [*rooms, *kit_rooms]
    # Merge into the shared manifests (the dorm rooms are build_dorms.py's).
    manifest_path = os.path.join(args.instances, "manifest.json")
    world_path = os.path.join(args.world, "instances.json")
    school_path = os.path.join(args.instances, "school.json")
    manifest = read_json(manifest_path, {"version": 1, "instances": {}})
    world_entries = read_json(world_path, {})
    school = read_json(school_path, {"version": MANIFEST_VERSION, "rooms": {}})
    school["version"] = MANIFEST_VERSION
    school["generator"] = "tools/blender/world/build_school.py"
    for lid in wanted:
        room = rooms.get(lid) or kit_rooms[lid]
        entry = build_one(room, args) if lid in rooms else build_kit_room(room, args)
        manifest["instances"][lid] = entry
        world_entries[lid] = {
            "cells": [{"id": room.cell, "file": f"{room.cell}.glb", "bounds": entry["bounds"], "level": 0,
                       **({"floor_seats": entry["floor_seats"]} if "floor_seats" in entry else {}),
                       **({"foliage": entry["foliage"]} if "foliage" in entry else {})}],
            "lights": entry["lights"],
            "ambient": entry["ambient"],
            **({"seats": entry["seats"]} if "seats" in entry else {}),
            **({"fire": entry["fire"]} if "fire" in entry else {}),
        }
        school["rooms"][lid] = {k: entry[k] for k in ("file", "cell", "bounds", "triangles", "draw_calls")}
        if "foliage" in entry:
            import foliage as FO
            school["rooms"][lid]["foliage_tris"] = FO.lod0_tris(
                [FO.Planting(entry["foliage"]["species"][r[0]], tuple(r[1:4]), tuple(r[4:7]), tuple(r[7:10]), tuple(r[10:13]))
                 for r in entry["foliage"]["plants"]])
        # The room's animated props, one glb each.
        for pid, fn in class_rooms.CLASS_PROPS.get(lid, {}).items():
            school.setdefault("props", {})[pid] = {"layout": lid, **build_prop(pid, fn, args)}
    manifest["instances"] = dict(sorted(manifest["instances"].items()))
    write_json(manifest_path, manifest)
    write_json(world_path, dict(sorted(world_entries.items())))
    school["rooms"] = dict(sorted(school["rooms"].items()))
    for lid, room_entry in school["rooms"].items():
        if lid in OPEN_SKY_ROOMS:
            room_entry["open_sky"] = True
        else:
            room_entry.pop("open_sky", None)
    write_json(school_path, school)
    print(f"wrote {manifest_path}, {world_path} and {school_path}")


if __name__ == "__main__":
    main()
