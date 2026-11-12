"""The hook greybox.py calls for the far castle silhouette
(docs/art/castle-kit.md "Exterior", world/outdoor.py).

`build(ctx: outdoor.HlodContext) -> dict` builds every chunk of
`data/world/exterior.json` (world/hlod_build.py: one mesh per chunk and
material, `hlod.<chunk>.<material>`, on the shared library materials
`castle_wall_c@ext` and `castle_roof`), writes `<out>/hlod.glb` with
greybox.py's glTF export (COLOR_0 as bytes) and returns the manifest's
`hlod` entry. It refuses to write a silhouette over its budgets
(hlod_build.report: 6-10 chunks, at most 2 draws a chunk, 60 k triangles,
16 MB).

The crag chunk's rock (`exterior.json` `crag`) is drawn
on a plain material of its own (`crag.material`, `backdrop_crag`) whose base
colour is the palette's `crag.colour` (`crag_rock`, data/art/castle_textures.json):
the client toon-shades it with the backdrop's row."""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEXTURES = os.path.join(_HERE, "..", "..", "..", "data", "art", "castle_textures.json")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import json  # noqa: E402

import hlod_build as HB  # noqa: E402
from castle_kit import grid as G  # noqa: E402


def crag_material(name: str, colour: str):
    """A plain glTF material in the palette's colour `colour` (the crag's rock)."""
    import bpy

    import palette

    with open(_TEXTURES, encoding="utf-8") as f:
        hex_colour = json.load(f)["palette"][colour]
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


def build(ctx) -> dict:
    chunks = HB.build(ctx.layout, ctx.exterior, ctx.kit_plan)
    problems = HB.report(chunks, ctx.exterior)
    # A silhouette face flush with a real wall z-fights with it (HB.coplanar_overlaps).
    problems += [f"{r[0]} face within {G.P3A_HLOD_INSET} m of {r[1]}'s wall (normal {r[3]}, plane {r[6]}): "
                 f"set it at least that far behind" for r in HB.coplanar_overlaps(chunks, HB.layout_plan(ctx.layout))]
    if problems:
        raise SystemExit("hlod_export: " + "; ".join(problems))
    crag = ctx.exterior.get("crag") or {}
    objs = []
    for ch in ctx.exterior["chunks"]:
        for mat, md in sorted(chunks.get(ch["id"], {}).items()):
            material = crag_material(mat, crag["colour"]) if mat == crag.get("material") else ctx.castle_material(mat)
            obj = ctx.mesh_from_data(md, material)
            # The client never picks the silhouette and it neither casts nor takes shadows.
            obj["hlod_chunk"] = ch["id"]
            objs.append(obj)
    path = os.path.join(ctx.out_dir, G.P3A_HLOD_FILE)
    ctx.export(objs, path)
    entry = HB.manifest_entry(chunks, ctx.exterior)
    print(f"hlod: {len(entry['chunks'])} chunks, {sum(c['draws'] for c in entry['chunks'])} meshes, "
          f"{entry['tris']} triangles -> {path}")
    for c in entry["chunks"]:
        print(f"  {c['id']:18s} {c['draws']} meshes {c['tris']:6d} tris  cells {len(c['cells'])}")
    return entry


def main() -> None:
    """Rebuilds only `hlod.glb` and the manifest's `hlod` entry of an existing
    export (quick turns on exterior.json; the full export is greybox.py):

        blender -b --factory-startup --python tools/blender/world/hlod_export.py -- \\
            [--out client/public/assets/world]
    """
    import argparse
    import json

    sys.path.insert(0, os.path.dirname(_HERE))
    import bpy

    import greybox as GB
    import layout_lib as L
    import outdoor
    from castle_kit import schema as S
    from castle_kit import visibility
    from check_layout import script_args

    ap = argparse.ArgumentParser(description="Rebuild hlod.glb and the manifest's hlod entry")
    ap.add_argument("--out", default=os.path.join(S.REPO, "client", "public", "assets", "world"))
    args = ap.parse_args(script_args(sys.argv))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    layout = L.load_layout(S.LAYOUT_PATH)
    textures = S.load_texture_set()
    kit_plan, rep = S.resolve(S.load_json(S.KIT_PATH), layout, S.PROPS_PATH, textures)
    if not rep.ok:
        raise SystemExit("hlod_export: castle_kit.json invalid")
    with open(os.path.join(S.REPO, "data", "world", "exterior.json"), encoding="utf-8") as f:
        exterior = json.load(f)

    def export(objs, path):
        GB.export_cell(objs, path, extras=True)
        visibility.pack_color0(path)

    entry = build(outdoor.HlodContext(layout=layout, exterior=exterior, kit_plan=kit_plan, textures=textures,
                                      castle_material=lambda name: GB.castle_material(name, textures),
                                      mesh_from_data=GB.mesh_from_data, export=export, out_dir=args.out))
    path = os.path.join(args.out, "manifest.json")
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["hlod"] = entry
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
