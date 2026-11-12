"""The Guardian Charm's silver animals: writes
`client/public/assets/guardian/guardian.glb` and `manifest.json` from
`guardian_models.py` (pure Python geometry; see its docstring for the frame,
the part names and the budget).

One glb holds the six animals. Each animal is an empty `guardian_<animal>`
at the origin with its part nodes `guardian_<animal>_<part>` as children,
each placed at its joint (the node's origin), smooth shaded, all sharing one
plain white material (`guardian`): the client merges an animal's parts into
one mesh and shades it silver itself (one draw a guardian).

    blender -b --factory-startup --python tools/blender/guardian/build_guardian.py -- \\
        [--out client/public/assets/guardian] [--preview DIR]

`--preview DIR` also renders a still of each animal (Workbench) into DIR,
for review.
"""

from __future__ import annotations

import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
_REPO = os.path.abspath(os.path.join(_BLENDER, "..", ".."))
for _p in (_HERE,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import guardian_models as pm  # noqa: E402


def to_blender(p):
    """Y-up animal frame (x left, y up, z forward) -> Blender Z-up (x, -z, y)."""
    return (p[0], -p[2], p[1])


def script_args(argv):
    return argv[argv.index("--") + 1:] if "--" in argv else []


def material():
    import bpy

    mat = bpy.data.materials.new("guardian")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.5
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    mat.use_backface_culling = True
    return mat


def part_object(name: str, mesh: pm.Mesh, mat):
    import bpy

    me = bpy.data.meshes.new(name)
    me.from_pydata([to_blender(v) for v in mesh.verts], [], [list(t) for t in mesh.tris])
    me.materials.append(mat)
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.validate(clean_customdata=False)
    me.update()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def blender_objects(animals):
    """Every animal as an empty with its parts as children: the empties
    first, then the parts."""
    import bpy

    mat = material()
    roots, parts = [], []
    for a in animals:
        root = bpy.data.objects.new(f"guardian_{a.key}", None)
        bpy.context.scene.collection.objects.link(root)
        roots.append(root)
        for p in a.parts:
            o = part_object(pm.node_name(a.key, p.name), p.mesh, mat)
            o.location = to_blender(p.pivot)
            o.parent = root
            parts.append(o)
    return roots, parts


def export_glb(objs, path: str) -> None:
    import bpy

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
        export_texcoords=False,
        export_normals=True,
        export_tangents=False,
        export_cameras=False,
        export_lights=False,
        export_animations=False,
        export_skins=False,
        export_morph=False,
    )
    props = bpy.ops.export_scene.gltf.get_rna_type().properties
    if "export_vertex_color" in props:
        kwargs["export_vertex_color"] = "NONE"
    else:
        kwargs["export_colors"] = False
    bpy.ops.export_scene.gltf(**kwargs)


def glb_triangles(path: str) -> dict:
    """Triangles per mesh node name, read back from the glb's JSON chunk."""
    import struct

    with open(path, "rb") as f:
        data = f.read()
    (json_len,) = struct.unpack_from("<I", data, 12)
    doc = json.loads(data[20:20 + json_len])
    out = {}
    for node in doc.get("nodes", []):
        if "mesh" not in node:
            continue
        prims = doc["meshes"][node["mesh"]]["primitives"]
        out[node["name"]] = sum(doc["accessors"][p["indices"]]["count"] // 3 for p in prims)
    return out


def render_previews(animals, roots, out_dir: str) -> None:
    """A three-quarter still of each animal alone (Workbench, studio light)."""
    import bpy

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 640
    scene.render.resolution_y = 480
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "SINGLE"
    scene.display.shading.single_color = (0.75, 0.85, 1.0)
    scene.display.shading.show_cavity = False
    scene.display.shading.show_object_outline = True
    cam_data = bpy.data.cameras.new("preview")
    cam = bpy.data.objects.new("preview", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    os.makedirs(out_dir, exist_ok=True)
    for a, root in zip(animals, roots):
        for r in roots:
            hide = r is not root
            for c in r.children:
                c.hide_render = hide
        lo, hi = a.bounds()
        centre = [(lo[k] + hi[k]) / 2 for k in range(3)]
        extent = max(hi[k] - lo[k] for k in range(3))
        d = extent * 2.2 + 0.3
        # Front-left three-quarter view, a little above.
        eye = (centre[0] + d * 0.6, centre[1] + d * 0.35, centre[2] + d * 0.75)
        cam.location = to_blender(eye)
        target = to_blender(centre)
        dx, dy, dz = (target[0] - cam.location[0], target[1] - cam.location[1], target[2] - cam.location[2])
        cam.rotation_euler = (math.atan2(math.hypot(dx, dy), -dz), 0.0, math.atan2(dy, dx) - math.pi / 2)
        for view, eye2 in (("34", None), ("side", (centre[0] + d, centre[1] + 0.05, centre[2]))):
            if eye2 is not None:
                cam.location = to_blender(eye2)
                dx, dy, dz = (target[0] - cam.location[0], target[1] - cam.location[1], target[2] - cam.location[2])
                cam.rotation_euler = (math.atan2(math.hypot(dx, dy), -dz), 0.0, math.atan2(dy, dx) - math.pi / 2)
            scene.render.filepath = os.path.join(out_dir, f"guardian-{a.key}-{view}.png")
            bpy.ops.render.render(write_still=True)


def main() -> None:
    sys.path.append(_BLENDER)  # tools/blender, for pins
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    import argparse

    import bpy

    ap = argparse.ArgumentParser(description="The guardian animals (guardian/guardian.glb)")
    ap.add_argument("--out", default=os.path.join(_REPO, "client", "public", "assets", "guardian"))
    ap.add_argument("--preview", default=None)
    args = ap.parse_args(script_args(sys.argv))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    animals = pm.animals()
    for a in animals:
        assert a.triangles() <= pm.TRI_BUDGET, (a.key, a.triangles())
    roots, parts = blender_objects(animals)
    path = os.path.join(args.out, "guardian.glb")
    export_glb(roots + parts, path)
    tris = glb_triangles(path)
    man = pm.manifest(animals)
    for key, entry in man["animals"].items():
        got = sum(tris.get(n, 0) for n in entry["nodes"].values())
        assert got == entry["triangles"], (key, got, entry["triangles"])
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, indent=2)
        f.write("\n")
    for key, entry in man["animals"].items():
        print(f"guardian {key}: {entry['triangles']} triangles, {len(entry['parts'])} parts, gait {entry['gait']}")
    print(f"-> {path}")
    if args.preview:
        render_previews(animals, roots, args.preview)


if __name__ == "__main__":
    main()
