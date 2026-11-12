"""The Owl Loft's owl, `owl` (docs/art/castle-kit.md,
grid.py OWL_*). One small glb,
`client/public/assets/instances/props/owl.glb`, that the client draws as thin
instances at the Owl Loft cell's empty marker nodes `owl_loft.owl_<k>` (their
extras.kit_anim {"kind": "owl", "phase", "yaw"}), bobbing the body and turning
the head by each marker's phase. Cartoonish and invented in look: a round
brown owl about 0.4 m tall, a pale breast and facial disc, gilt eyes with dark
pupils, a short hooked beak, small ear tufts, folded wings and tucked feet.

Two nodes (two meshes): `owl_body` (the root, origin at its feet, facing +z,
out of the perch's wall) and its child `owl_head`, whose origin is the neck
(OWL_NECK above the feet), so the client turns it about +y there. Both share
one material (a small swatch texture of palette names, as world/beast_props.py:
one draw a node, at most grid.OWL_TRIS triangles in all). Colours come from
castle_textures.json `palette` by name (castle_kit/props_atlas.palette); no
colour literal here.

    blender -b --factory-startup --python tools/blender/world/owl_prop.py -- \\
        [--out client/public/assets/instances/props]

Pure Python but `blender_objects` and `main` (bpy).
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_BLENDER, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import beast_props as BP  # noqa: E402  (its Part, swatch and sphere helpers)
import meshkit as mk  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from meshkit import Mesh  # noqa: E402

PROP = G.OWL_PROP  # "owl"
OWL_NECK = 0.25  # the head node's origin above the feet
# Zones -> palette names (castle_textures.json `palette`).
BROWN, PALE, EYE, PUPIL, BEAK = 1, 2, 3, 4, 5
ZONES = {BROWN: "owl_brown", PALE: "owl_pale", EYE: "gilt", PUPIL: "soot", BEAK: "parcel_tan"}


def body_mesh() -> Mesh:
    """The body from the feet up to the neck: an egg of brown with a pale
    breast, folded wings a shade proud on either side, a short tail, two
    tucked feet (origin at the feet, facing +z)."""
    m = Mesh()
    mark = BP._sphere(m, (0.0, 0.0, 0.0), 1.0, BROWN, seg=8, rings=5, scale=(0.13, 0.15, 0.12))
    m.transform(mark, lambda p: (p[0], p[1] + 0.15, p[2]))
    for i, f in enumerate(m.faces[mark[1]:]):
        c = [m.verts[k] for k in f.idx]
        if sum(v[2] for v in c) / len(c) > 0.05 and sum(v[1] for v in c) / len(c) < 0.25:
            f.zone = PALE  # the breast
    for sx in (-1.0, 1.0):
        w = BP._sphere(m, (0.0, 0.0, 0.0), 1.0, BROWN, seg=6, rings=3, scale=(0.035, 0.11, 0.09))
        m.transform(w, lambda p, sx=sx: (sx * 0.11 + p[0], 0.16 + p[1] - 0.25 * p[2], -0.01 + p[2]))
    tail = [(-0.05, 0.05, -0.09), (0.05, 0.05, -0.09), (0.0, 0.0, -0.17)]
    m.poly(tail, "fur", zone=BROWN)
    m.poly(list(reversed(tail)), "fur", zone=BROWN)
    for sx in (-1.0, 1.0):
        mk.centred_box(m, (sx * 0.045, 0.0, 0.07), (0.05, 0.025, 0.06), "fur", BEAK)
    return m


def head_mesh() -> Mesh:
    """The head about its neck (y = 0): a round brown head, a pale facial
    disc of two lobes, gilt eyes with dark pupils, a hooked beak, two ear
    tufts."""
    m = Mesh()
    BP._sphere(m, (0.0, 0.1, 0.0), 1.0, BROWN, seg=8, rings=5, scale=(0.12, 0.1, 0.105))
    for sx in (-1.0, 1.0):
        disc = BP._sphere(m, (0.0, 0.0, 0.0), 1.0, PALE, seg=8, rings=3, scale=(0.055, 0.06, 0.02))
        m.transform(disc, lambda p, sx=sx: (sx * 0.05 + p[0], 0.11 + p[1], 0.085 + p[2]))
        BP._sphere(m, (sx * 0.05, 0.115, 0.1), 0.026, EYE, seg=6, rings=3)
        BP._sphere(m, (sx * 0.05, 0.115, 0.122), 0.012, PUPIL, seg=4, rings=2)
        BP._cone(m, (sx * 0.075, 0.18, 0.02), (sx * 0.1, 0.25, 0.0), 0.022, BROWN, seg=4)
    BP._cone(m, (0.0, 0.085, 0.1), (0.0, 0.05, 0.135), 0.018, BEAK, seg=4)
    return m


def build() -> BP.Beast:
    body = BP.Part("owl_body", body_mesh())
    head = BP.Part("owl_head", head_mesh(), (0.0, OWL_NECK, 0.0))
    return BP.Beast(PROP, ZONES, [body, head])


def blender_objects():
    """The owl as Blender objects (the body, the head its child), one
    material on a swatch of palette names, as beast_props.blender_objects."""
    import bpyutil
    import paint
    from castle_kit import props_atlas

    owl = build()
    names = props_atlas.palette()
    img = bpyutil.image_from_array(f"{PROP}_swatch", paint.swatches(BP.SWATCH_PX, BP.swatch_colours(owl, names),
                                                                   BP.SWATCH_GRID))
    mat = bpyutil.principled(PROP, None, img, 0.85)
    tex = next(n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE")
    tex.interpolation = "Closest"
    objs = []
    for part in owl.parts:
        uvs = [[BP.zone_uv(owl, f.zone)] * len(f.idx) for f in part.mesh.faces]
        o = bpyutil.mesh_object(part.name, part.mesh, {"fur": mat}, uvs=uvs)
        o.location = bpyutil.to_blender(part.origin)
        objs.append(o)
    objs[1].parent = objs[0]
    return objs


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    import argparse

    import bpyutil
    from check_layout import script_args

    repo = os.path.abspath(os.path.join(_BLENDER, "..", ".."))
    ap = argparse.ArgumentParser(description="The Owl Loft's owl prop (instances/props/owl.glb)")
    ap.add_argument("--out", default=os.path.join(repo, "client", "public", "assets", "instances", "props"))
    args = ap.parse_args(script_args(sys.argv))
    bpyutil.reset_scene()
    objs = blender_objects()
    path = os.path.join(args.out, f"{PROP}.glb")
    bpyutil.export_glb(objs, path)
    info = bpyutil.glb_info(path)
    print(f"{PROP}: {info['triangles']} triangles, {info['draw_calls']} draw calls -> {path}")


if __name__ == "__main__":
    main()
