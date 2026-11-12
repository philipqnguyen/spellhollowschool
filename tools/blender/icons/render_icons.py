"""Renders a 128 x 128 transparent inventory icon for every item of
data/items/catalog.json.

    blender -b --factory-startup --python tools/blender/icons/render_icons.py -- \\
        [--assets client/public/assets] [--sheet art/renders/icons] [--ids 101,3301]

* Clothing: the item's fitted file for `body_a` (`<asset>_body_a.glb`, LOD0,
  from the character build) on an invisible mannequin (no body), tinted
  with the item's catalog dyes the way the client tints characters.
* Furniture: its model (furniture_models.py) on the furniture atlas,
  tinted with its catalog dyes the way the client tints furniture.
* Materials, dyes, finishes, potions, patterns: a small icon model
  (icon_models.py) on the same atlas and dye convention.

Every icon has the same studio light (a warm key from the upper left, a
cool fill, a rim from behind), an orthographic camera at a three-quarter
view chosen per kind, and is framed to the item's projected bounds with the
same margin. Writes `<assets>/icons/<item id>.png` (each item in its own
catalog dyes); each icon key's file where the client looks for it
(client/src/economy/icons.ts: `<assets>/<asset>.png` for asset keys that are
already icon keys, e.g. `icons/material/wool_skein`, and
`<assets>/icons/<asset>.png` for clothing and furniture; items sharing a key
share the lowest id's icon); and `<assets>/icons/manifest.json`:

    { "version": 1, "size": 128, "generator": "...",
      "icons": ["icons/furniture/bed_four_poster_a", "icons/material/wool_skein", ...],
      "items": { "<item id>": { "file": "<item id>.png", "key": "...", "asset": "...", "kind": "..." } } }

Also the class icons (`icons/class/<name>.png`, the
Transfiguration lesson's objects and changes, icon_models.CLASS_ICONS, in
their own palette.json colours), listed in the manifest's `icons` and
`class_icons`; `--class-only` renders just those and keeps the manifest's
items.

With `--sheet <dir>` it also writes contact sheets of all icons there
(`icons_<kind>.png`, on a neutral background) for review. Icons show the
catalog default dyes; a clothing item missing from the character build is
reported and left out (the client then draws its generated icon).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(TOOLS, "world"), os.path.join(TOOLS, "characters"), TOOLS]

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

import atlas  # noqa: E402
import bpyutil  # noqa: E402
import furniture_models as fm  # noqa: E402
import icon_models  # noqa: E402
import review  # noqa: E402
from charlib import atlas as char_atlas  # noqa: E402
from charlib import montage  # noqa: E402
from charlib import render as char_render  # noqa: E402

REPO = os.path.abspath(os.path.join(TOOLS, "..", ".."))
CATALOG = os.path.join(REPO, "data", "items", "catalog.json")
PALETTE = os.path.join(REPO, "data", "items", "palette.json")
SIZE = 128
MANIFEST_VERSION = 1
ICON_BODY = "body_a"
MARGIN = 1.12  # frame = projected size x margin

# (azimuth, elevation) of the camera in degrees: azimuth 0 looks at the
# item's front (+Z in the game, -Y in Blender), positive turns to its left.
VIEWS = {
    "hat": (28, 16), "body": (22, 6), "legs": (22, 6), "shoes": (38, 24),
    "floor": (34, 24), "table": (34, 26), "wall": (18, 6),
    "material": (32, 34), "dye": (26, 14), "finish": (28, 22), "potion": (24, 12), "pattern": (16, 62),
}


def args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--assets", default=os.path.join(REPO, "client", "public", "assets"))
    p.add_argument("--sheet", default="", help="directory for review contact sheets (e.g. art/renders/icons)")
    p.add_argument("--ids", default="", help="only these item ids (comma separated)")
    p.add_argument("--class-only", action="store_true", help="only the class icons, keeping the manifest's items")
    return p.parse_args(argv)


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def icon_key(item: dict) -> str:
    """The item's icon key, as the client resolves it (client/src/economy/
    icons.ts): an asset key that is already an icon key (`icons/<kind>/...`)
    as is, any other asset under `icons/`."""
    asset = item["asset"]
    return asset if asset.startswith("icons/") else f"icons/{asset}"


def dye_hexes(item: dict, palette: dict) -> list[str]:
    hex_of = {c["index"]: c["hex"] for c in palette["colours"]}
    return [hex_of[item["dyes"][ch]] for ch in ("primary", "secondary", "trim")]


# ---------------------------------------------------------------------------
# Studio


def studio() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x = sc.render.resolution_y = SIZE
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    try:
        sc.eevee.taa_render_samples = 48
    except AttributeError:
        pass
    world = bpy.data.worlds.new("icon_world")
    sc.world = world
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (0.62, 0.64, 0.68, 1.0)
    bg.inputs["Strength"].default_value = 0.55
    for name, rot, energy, colour in (("key", (48, 0, -38), 3.4, (1.0, 0.96, 0.9)), ("fill", (64, 0, 130), 1.0, (0.85, 0.9, 1.0)),
                                      ("rim", (28, 0, 175), 2.2, (1.0, 1.0, 1.0))):
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        light.color = colour
        light.angle = math.radians(12)
        o = bpy.data.objects.new(name, light)
        o.rotation_euler = tuple(math.radians(a) for a in rot)
        sc.collection.objects.link(o)
    cam = bpy.data.cameras.new("icon_cam")
    cam.type = "ORTHO"
    cam.clip_start = 0.001
    cam.clip_end = 100.0
    o = bpy.data.objects.new("icon_cam", cam)
    sc.collection.objects.link(o)
    sc.camera = o


def clear_items() -> None:
    for o in list(bpy.data.objects):
        if o.type in ("MESH", "ARMATURE", "EMPTY"):
            bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.armatures, bpy.data.actions):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def frame(objs, view: tuple[float, float]) -> None:
    """Points the orthographic camera along the view direction and fits it
    to the objects' projected bounds."""
    az, el = (math.radians(v) for v in view)
    forward = Vector((-math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), -math.sin(el)))  # camera looks along this
    right = forward.cross(Vector((0, 0, 1))).normalized()
    up = right.cross(forward).normalized()
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        pts += [o.matrix_world @ v.co for v in me.vertices]
        ev.to_mesh_clear()
    if not pts:
        raise RuntimeError("nothing to frame")
    xs = [p.dot(right) for p in pts]
    ys = [p.dot(up) for p in pts]
    zs = [p.dot(forward) for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    size = max(max(xs) - min(xs), max(ys) - min(ys)) * MARGIN
    cam = bpy.context.scene.camera
    cam.data.ortho_scale = size
    centre = right * cx + up * cy + forward * (min(zs) - 1.0)
    cam.location = centre
    cam.rotation_euler = forward.to_track_quat("-Z", "Y").to_euler()


def render(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


# ---------------------------------------------------------------------------
# Item loaders


class Atlases:
    """The furniture atlas images, made once."""

    def __init__(self):
        self.base = bpyutil.image_from_array("furniture_atlas", atlas.paint_atlas())
        self.glow = bpyutil.image_from_array("furniture_atlas_emissive", atlas.paint_emissive())


def atlas_object(name: str, mesh, hexes: list[str], atl: Atlases, glows: bool):
    mat = bpyutil.principled(f"icon_{name}", image=atl.base, roughness=0.55, emissive=2.0 if glows else 0.0,
                             emissive_image=atl.glow if glows else None, cull=False)
    obj = bpyutil.mesh_object(name, mesh, {m: mat for m in mesh.materials()}, uvs=atlas.atlas_uvs(mesh),
                              colors=atlas.face_masks(mesh))
    obj.material_slots[0].material = review.dyed_preview_material(mat, hexes)
    return [obj]


def clothing_objects(item: dict, assets: str, hexes: list[str]):
    path = os.path.join(assets, f"{item['asset']}_{ICON_BODY}.glb")
    if not os.path.exists(path):
        return None
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = []
    for o in new:
        if o.type != "MESH":
            continue
        if not (o.get("lod") == 0 or o.name.endswith("_lod0")):
            # LOD1, and the bone display shapes the importer adds.
            bpy.data.objects.remove(o, do_unlink=True)
            continue
        mat = char_render.tint_material(f"icon_dye_{item['id']}", *hexes)
        attr = o.data.color_attributes[0].name if o.data.color_attributes else "Color"
        for n in mat.node_tree.nodes:
            if n.type == "VERTEX_COLOR":
                n.layer_name = attr
        o.data.materials.clear()
        o.data.materials.append(mat)
        meshes.append(o)
    return meshes


def view_for(item: dict) -> tuple[float, float]:
    if item["kind"] == "clothing":
        if "comb" in item["asset"]:
            return (-80, 12)  # worn over the right ear: seen from that side
        return VIEWS[item["slot"]]
    if item["kind"] == "furniture":
        return VIEWS[item["furniture"]["surface"]]
    return VIEWS[item["kind"]]


GLOW_TILES = {"flame", "brew"}
# Potions whose brew shines in its own colour (lit through the tinted albedo),
# like any item whose asset name says it glows.
GLOWING_POTIONS = {"glow", "fortunes_draught"}


def glow_in_own_colour(obj, strength: float = 0.9) -> None:
    """Drives the material's emission from its tinted albedo."""
    mat = obj.material_slots[0].material
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    src = bsdf.inputs["Base Color"].links[0].from_socket
    nt.links.new(src, bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = strength


def render_class_icons(assets: str, palette: dict, atl: Atlases) -> list[str]:
    """The class icons (icon_models.CLASS_ICONS) at
    `<assets>/icons/class/<name>.png`, each in its palette.json colours, seen
    like a material. Returns their icon keys."""
    hex_of = {c["id"]: c["hex"] for c in palette["colours"]}
    keys = []
    # The Flying class's icons beside the picks' (icon_models.FLYING_ICONS).
    for name, (_, dyes) in sorted({**icon_models.CLASS_ICONS, **icon_models.FLYING_ICONS}.items()):
        clear_items()
        mesh = icon_models.build_class_icon(name)
        glows = any(f.material in GLOW_TILES for f in mesh.faces)
        objs = atlas_object(f"icon_class_{name}", mesh, [hex_of[d] for d in dyes], atl, glows)
        frame(objs, VIEWS["material"])
        key = f"icons/class/{name}"
        render(os.path.join(assets, f"{key}.png"))
        keys.append(key)
    print(f"[icons] {len(keys)} class icons -> {os.path.join(assets, 'icons', 'class')}", flush=True)
    return keys


def objects_for(item: dict, assets: str, palette: dict, atl: Atlases):
    hexes = dye_hexes(item, palette)
    if item["kind"] == "clothing":
        return clothing_objects(item, assets, hexes)
    if item["kind"] == "furniture":
        mesh = fm.build(item)
    else:
        mesh = icon_models.build(item)
    glows = any(f.material in GLOW_TILES for f in mesh.faces)
    objs = atlas_object(f"icon_{item['id']}", mesh, hexes, atl, glows)
    if item.get("potion", {}).get("effect") in GLOWING_POTIONS or "glowing" in item["asset"]:
        glow_in_own_colour(objs[0])
    return objs


# ---------------------------------------------------------------------------
# Sheets


def write_sheets(paths_by_kind: dict[str, list[str]], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    bg = np.array([0.36, 0.37, 0.4], dtype=np.float32)
    for kind, paths in paths_by_kind.items():
        if not paths:
            continue
        cols = min(12, len(paths))
        rows = (len(paths) + cols - 1) // cols
        pad = 8
        cell = SIZE + pad
        sheet = np.ones((rows * cell + pad, cols * cell + pad, 4), dtype=np.float32)
        sheet[..., :3] = bg
        for i, p in enumerate(paths):
            img = montage.load_rgba(p)
            a = img[..., 3:4]
            r, c = divmod(i, cols)
            y0 = (rows - 1 - r) * cell + pad
            x0 = c * cell + pad
            tile_bg = np.ones((SIZE, SIZE, 3), dtype=np.float32) * (bg + (0.06 if (r + c) % 2 else 0.03))
            sheet[y0:y0 + SIZE, x0:x0 + SIZE, :3] = img[..., :3] * a + tile_bg * (1 - a)
        montage.save_rgba(os.path.join(out_dir, f"icons_{kind}.png"), sheet)
        print(f"[icons] sheet icons_{kind}.png ({len(paths)} icons)", flush=True)


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    a = args()
    assets = os.path.abspath(a.assets)
    out_dir = os.path.join(assets, "icons")
    catalog = load_json(CATALOG)
    palette = load_json(PALETTE)
    only = {int(x) for x in a.ids.split(",") if x}
    class_only = a.class_only
    studio()
    char_atlas.build_image()
    atl = Atlases()
    manifest_path = os.path.join(out_dir, "manifest.json")
    manifest = {"version": MANIFEST_VERSION, "size": SIZE, "generator": "tools/blender/icons/render_icons.py", "items": {}}
    if (only or class_only) and os.path.exists(manifest_path):
        old = load_json(manifest_path)
        if old.get("version") == MANIFEST_VERSION:
            manifest["items"] = old.get("items", {})
    by_kind: dict[str, list[str]] = {}
    missing = []
    keys_done: set[str] = set()
    class_keys = render_class_icons(assets, palette, atl)
    by_kind["class"] = [os.path.join(assets, f"{k}.png") for k in class_keys]
    for item in catalog["items"]:
        if class_only or item.get("retired") or (only and item["id"] not in only):
            continue
        clear_items()
        objs = objects_for(item, assets, palette, atl)
        if not objs:
            missing.append(item["asset"])
            print(f"[icons] {item['id']} {item['asset']}: no {ICON_BODY} file in {assets}; skipped", flush=True)
            continue
        frame(objs, view_for(item))
        path = os.path.join(out_dir, f"{item['id']}.png")
        render(path)
        key = icon_key(item)
        manifest["items"][str(item["id"])] = {"file": f"{item['id']}.png", "key": key, "asset": item["asset"],
                                             "kind": item["kind"]}
        by_kind.setdefault(item["kind"], []).append(path)
        # The key's file is the first (lowest id) item's icon among those
        # sharing the key (the pattern sheets of one slot share theirs).
        if key not in keys_done:
            keys_done.add(key)
            key_path = os.path.join(assets, f"{key}.png")
            os.makedirs(os.path.dirname(key_path), exist_ok=True)
            shutil.copyfile(path, key_path)
    manifest["items"] = dict(sorted(manifest["items"].items(), key=lambda kv: int(kv[0])))
    manifest["class_icons"] = class_keys
    manifest["icons"] = sorted({e["key"] for e in manifest["items"].values()} | set(class_keys))
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"[icons] {len(manifest['items'])} icons -> {out_dir}" + (f"; {len(missing)} missing" if missing else ""), flush=True)
    if a.sheet:
        write_sheets(by_kind, os.path.abspath(a.sheet))
    if missing:
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
