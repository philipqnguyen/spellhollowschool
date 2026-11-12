"""The talking portraits' assets (NPCs, docs/art/castle-kit.md "Portraits and tapestries"): one gilt
shadow-box frame per canvas size of data/npcs/portraits.json, the four
painted backdrops and the manifest the client reads.

    blender -b --factory-startup --python tools/blender/npcs/build_portraits.py -- \\
        --out client/public/assets/npcs

Writes into `--out`:

- `portrait_frame_<w>x<h>.glb` (sizes in cm, e.g. portrait_frame_100x130):
  nodes `portrait_frame_<w>x<h>.castle_trim` (the gilt moulding, bosses,
  crest and plaque; the castle's trim sheet, bound by name),
  `portrait_frame_<w>x<h>.npc_backdrop` (the box's back and sides) and
  `portrait_frame_<w>x<h>.npc_backdrop_lod1` (extras `kit_lod`: { base:
  the castle_trim node, distance }); materials are placeholders the client
  binds by name (castle_trim to the trim sheet, npc_backdrop to the
  placement's backdrop); COLOR_0 the castle shader's value; origin at the
  canvas centre on the wall face, +z into the room (frames.py);
- `backdrop_<n>.png` (512 px) and, with basisu, `backdrop_<n>.ktx2` (UASTC,
  zstd, full mips, sRGB: the castle library's encoding);
- `manifest.json`: { version, frames: { "<w>x<h>": file }, backdrops:
  [file], backdrops_ktx2: [file] (when encoded), depth_m, border_m,
  subject_z_m, lod1_m, triangles: { "<w>x<h>": [lod0, lod1] }, draws: {
  lod0, lod1 } }.

Deterministic: the same inputs write the same bytes (the frames from
frames.py, the backdrops from backdrops.py, seeded). Exits non-zero over
budget (frames.TRIS_LOD0 / TRIS_LOD1 triangles, 2 draws at LOD0, 1 at
LOD1)."""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_BLENDER, os.path.join(_BLENDER, "world")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.dont_write_bytecode = True

from castle_kit import assemble as A  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402
from npcs import backdrops as BD  # noqa: E402
from npcs import frames as FR  # noqa: E402
from npcs import placements as PL  # noqa: E402

TRIM = "castle_trim"
BACKDROP = "npc_backdrop"
VERSION = 1


class _Uv(A.Builder):
    """Just enough of the castle kit's Builder to map trim-sheet UVs."""

    def __init__(self, textures: dict):  # noqa: D401 - no layout needed
        self.textures = textures


def sizes(entries: list[dict] | None = None) -> list[tuple[float, float]]:
    """The canvas sizes portraits.json uses, sorted."""
    entries = PL.load() if entries is None else entries
    return sorted({(float(p.get("size", (1.0, 1.3))[0]), float(p.get("size", (1.0, 1.3))[1])) for p in entries})


def frame_file(size) -> str:
    return f"portrait_frame_{PL.size_key(size)}.glb"


def frame_mesh(size, textures: dict) -> A.MeshData:
    piece, shades = FR.frame_piece(size)
    faces = _Uv(textures).piece_faces(piece, geom.Placement((0.0, 0.0, 0.0), 0.0), {}, "npc_frame")
    md = A.Builder._mesh(f"portrait_frame_{PL.size_key(size)}.{TRIM}", TRIM, faces, (0.0, 1.0))
    md.colors = [[(s, s, s)] * len(f.pts) for f, s in zip(faces, shades)]
    return md


def plain_mesh(name: str, material: str, faces) -> A.MeshData:
    """A MeshData from [(pts, uvs, shades)] faces (one polygon each)."""
    md = A.MeshData(name, material)
    for pts, uvs, shades in faces:
        idx = []
        for p in pts:
            idx.append(len(md.verts))
            md.verts.append(tuple(float(c) for c in p))
        md.faces.append(tuple(idx))
        md.uvs.append([tuple(float(c) for c in q) for q in uvs])
    md.colors = [[(s, s, s) for s in shades] for _, _, shades in faces]
    return md


def backdrop_mesh(size) -> A.MeshData:
    return plain_mesh(f"portrait_frame_{PL.size_key(size)}.{BACKDROP}", BACKDROP, FR.backdrop_faces(size))


def lod1_mesh(size) -> A.MeshData:
    return plain_mesh(f"portrait_frame_{PL.size_key(size)}.{BACKDROP}_lod1", BACKDROP, FR.lod1_faces(size))


def manifest(sz: list, tris: dict, ktx2: bool) -> dict:
    out = {
        "version": VERSION,
        "frames": {PL.size_key(s): frame_file(s) for s in sz},
        "backdrops": [f"backdrop_{i}.png" for i in range(BD.COUNT)],
        "depth_m": FR.DEPTH,
        "border_m": FR.BORDER,
        "subject_z_m": round(FR.SUBJECT_Z, 4),
        "open_z_m": round(FR.OPEN_Z, 4),
        "lod1_m": FR.LOD1_M,
        "triangles": tris,
        "draws": {"lod0": 2, "lod1": 1},
    }
    if ktx2:
        out["backdrops_ktx2"] = [f"backdrop_{i}.ktx2" for i in range(BD.COUNT)]
    return out


def backdrop_material():
    import bpy

    mat = bpy.data.materials.get(BACKDROP) or bpy.data.materials.new(BACKDROP)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (0.35, 0.3, 0.27, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.9
    mat.use_backface_culling = True
    return mat


def build_frame(size, out_dir: str, textures: dict) -> tuple[str, int, int, int]:
    """Exports one frame; (path, lod0 triangles, lod1 triangles, draws)."""
    import bpyutil
    import greybox
    from castle_kit import visibility

    bpyutil.reset_scene()
    greybox.CASTLE_MATERIALS.clear()  # its cache holds the materials the reset removed
    trim = greybox.mesh_from_data(frame_mesh(size, textures), greybox.castle_material(TRIM, textures))
    back = greybox.mesh_from_data(backdrop_mesh(size), backdrop_material())
    lod = greybox.mesh_from_data(lod1_mesh(size), backdrop_material())
    lod["kit_lod"] = {"base": trim.name, "distance": FR.LOD1_M}
    path = os.path.join(out_dir, frame_file(size))
    greybox.export_cell([trim, back, lod], path, extras=True)
    visibility.pack_color0(path)
    info = bpyutil.glb_info(path)
    lod0 = info["triangles"] - FR.triangles(FR.lod1_faces(size)) * 1
    lod1 = FR.triangles(FR.lod1_faces(size))
    return path, lod0, lod1, info["draw_calls"]


def write_backdrops(out_dir: str, encode: bool = True) -> bool:
    """The PNGs, and their KTX2 when basisu is there; True when encoded."""
    from castle_kit import textures as T

    binary = T.basisu_binary() if encode else None
    for i in range(BD.COUNT):
        png = os.path.join(out_dir, f"backdrop_{i}.png")
        data = T.png_bytes(BD.paint_backdrop(i))
        with open(png, "wb") as f:
            f.write(data)
        if binary:
            mode, tuning = T.ktx2_tuning({"ktx2": {"mode": "uastc"}})
            T.encode_ktx2(binary, png, os.path.join(out_dir, f"backdrop_{i}.ktx2"), mode, tuning)
    if not binary and encode:
        print("[npcs] warning: no basisu; the backdrops are PNG only")
    return bool(binary)


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(description="The talking portraits' frames, backdrops and manifest")
    ap.add_argument("--out", default="client/public/assets/npcs")
    ap.add_argument("--no-ktx2", action="store_true")
    args = ap.parse_args(argv)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    textures = S.load_texture_set()
    sz = sizes()
    tris = {}
    bad = []
    keep = {"manifest.json"}
    for s in sz:
        path, lod0, lod1, draws = build_frame(s, out, textures)
        keep.add(os.path.basename(path))
        tris[PL.size_key(s)] = [lod0, lod1]
        print(f"[npcs] {os.path.basename(path)}: {lod0} + {lod1} (LOD1) triangles, {draws} draws")
        if lod0 > FR.TRIS_LOD0 or lod1 > FR.TRIS_LOD1 or draws != 3:
            bad.append(path)
    ktx2 = write_backdrops(out, not args.no_ktx2)
    for i in range(BD.COUNT):
        keep |= {f"backdrop_{i}.png", f"backdrop_{i}.ktx2"}
    for name in sorted(os.listdir(out)):  # frames of sizes no longer used
        if name.startswith("portrait_frame_") and name not in keep:
            os.remove(os.path.join(out, name))
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest(sz, tris, ktx2), f, indent=2, sort_keys=True)
        f.write("\n")
    if bad:
        print(f"[npcs] over budget: {bad}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
