"""glTF export of characters, hair and gear, and the asset manifest.

File layout follows docs/architecture.md "Content data", relative to the
assets root (client/public/assets):

* bodies: `<asset>.glb`, e.g. `characters/body_a.glb` (skeleton, region meshes
  for LOD0 to LOD3, all animation clips as glTF animations);
* clothing and hair: `<asset>_<body id>.glb`, hair caps
  `<asset>_cap_<body id>.glb` (skeleton and meshes, no animations);
* `characters/atlas.png` (the shared texture, also embedded in every file);
* `characters/manifest.json` (see `write_manifest`).

Every file carries the full, identical skeleton (same bone order), so the
client can bind gear and hair to the body's skeleton by index or name. Mesh
nodes are named `<name>_lod<N>` (N = 0..3) with node extras describing them,
and every mesh carries the custom vertex attribute `_SHADE` (VEC4, normalized
unsigned short; docs/art/character-adoption.md section 3) and `_OUTLINE` (VEC3
float: the inverted-hull outline's smoothed normal times its width weight,
charlib/outline_bake.py; docs/art/character-look.md section 4).
"""

from __future__ import annotations

import json
import os

import bpy
import numpy as np

MANIFEST_VERSION = 1


def select_only(objs) -> None:
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.hide_set(False)
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def export_glb(path: str, skel: bpy.types.Object, meshes: list[bpy.types.Object], animations: bool) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    # --- Outlines: the inverted-hull outline layer (charlib/outline_bake.py) ---
    from . import outline_bake as OB

    OB.bake(meshes)
    # --- end Outlines ---
    select_only([skel] + list(meshes))
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        export_yup=True,
        export_apply=False,
        export_extras=True,
        export_skins=True,
        export_influence_nb=4,
        export_all_influences=False,
        export_def_bones=False,
        export_leaf_bone=False,
        export_morph=True,
        export_morph_normal=False,
        export_morph_animation=False,
        export_attributes=True,  # the _SHADE layer (charlib/shade_bake.py) and _OUTLINE (outline_bake.py)
        export_animations=animations,
        export_animation_mode="NLA_TRACKS",
        export_force_sampling=True,
        export_optimize_animation_size=True,
        export_anim_slide_to_zero=False,
        export_normals=True,
        export_tangents=False,
        export_vertex_color="ACTIVE",
        export_all_vertex_colors=False,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_unused_images=False,
        export_unused_textures=False,
    )


def bounds(objs: list[bpy.types.Object]) -> dict:
    """Rest-pose bounds in glTF axes (Y up, +Z forward = Blender -Y)."""
    pts = []
    for o in objs:
        m = o.matrix_world
        pts.extend(m @ v.co for v in o.data.vertices)
    p = np.array([[v.x, v.z, -v.y] for v in pts])
    lo, hi = p.min(axis=0), p.max(axis=0)
    return {"min": [round(float(x), 4) for x in lo], "max": [round(float(x), 4) for x in hi]}


def lod_tris(meshes: dict) -> dict:
    """{"lod0": n, ..., "lod3": n} of a lod -> {name: object} dict."""
    return {f"lod{lvl}": tris(list(parts.values())) for lvl, parts in sorted(meshes.items())}


def tris(objs: list[bpy.types.Object]) -> int:
    return int(sum(len(p.vertices) - 2 for o in objs for p in o.data.polygons))


def read_manifest(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") == MANIFEST_VERSION:
                return data
        except (OSError, ValueError):
            pass
    return {"version": MANIFEST_VERSION, "assets": {}}


def write_manifest(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data["assets"] = dict(sorted(data["assets"].items()))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=False)
        f.write("\n")
