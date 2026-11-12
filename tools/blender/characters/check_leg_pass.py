"""Legs passing through each other in walk and run, per frame of the shipped clips.

    blender -b art/characters/<body>.blend --python tools/blender/characters/check_leg_pass.py -- \\
        <out.json> [<garment glb> ...]

Loads exported gear onto a saved body blend (art/characters/<body>.blend,
whose skeleton carries the baked clips that ship) and, for every frame of
`walk` and `run`, tests the left side against the right below the crotch:

* `bare`: the body's thighs, calves and feet (no garment);
* each garment (trousers, skirts, shoes): its faces whose vertices all lie
  more than SIDE_M off the midline at rest, left against right.

A frame counts when a left face intersects a right face (BVH overlap). Also
recorded: the closest the two sides come in the frames where they do not
intersect (a left vertex to the right surface); `which` lists the
intersecting frames and `heights_m` the height range of the left faces hit
in each.
"""

from __future__ import annotations

import json
import sys

import bpy
from mathutils.bvhtree import BVHTree

SIDE_M = 0.005
CLIPS = ("walk", "run")
BELOW_M = 0.02  # below the hip joints by this much (the crotch and seat join the sides)


def _skeleton():
    return next(o for o in bpy.data.objects if o.type == "ARMATURE" and o.name.endswith("_skeleton"))


def _load(path: str, skel) -> list:
    """Imports a gear glb's LOD0 meshes onto the body's skeleton."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    keep = [o for o in new if o.type == "MESH" and o.name.split(".")[0].endswith("lod0")]  # ".001" when the blend has one
    for o in new:
        if o not in keep:
            bpy.data.objects.remove(o, do_unlink=True)
    for o in keep:
        mw = o.matrix_world.copy()
        o.parent = skel
        o.matrix_world = mw
        for m in o.modifiers:
            if m.type == "ARMATURE":
                m.object = skel
    return keep


def _sides(objs, top_z: float) -> dict:
    """Per object, the faces of each side (+1 left, -1 right) below top_z at rest."""
    out = {}
    for o in objs:
        rest = [o.matrix_world @ v.co for v in o.data.vertices]
        out[o.name] = {s: [p.index for p in o.data.polygons
                           if all(rest[i].x * s > SIDE_M and rest[i].z < top_z for i in p.vertices)] for s in (1, -1)}
    return out


def measure(objs, faces) -> dict:
    skel = _skeleton()
    ad = skel.animation_data or skel.animation_data_create()
    for t in ad.nla_tracks:
        t.mute = True
    out = {}
    for clip in CLIPS:
        act = bpy.data.actions.get(clip)
        if act is None:
            continue
        ad.action = act
        if hasattr(ad, "action_slot") and act.slots:
            ad.action_slot = act.slots[0]
        f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
        which, worst, closest, heights = [], 0, 9.0, []
        for f in range(f0, f1):
            bpy.context.scene.frame_set(f)
            dg = bpy.context.evaluated_depsgraph_get()
            pts = {1: [], -1: []}
            tris = {1: [], -1: []}
            for o in objs:
                ev = o.evaluated_get(dg)
                me = ev.to_mesh()
                vs = [ev.matrix_world @ v.co for v in me.vertices]
                for s in (1, -1):
                    ids = faces[o.name][s]
                    used = sorted({i for pi in ids for i in me.polygons[pi].vertices})
                    remap = {i: len(pts[s]) + k for k, i in enumerate(used)}
                    pts[s].extend(vs[i] for i in used)
                    tris[s].extend(tuple(remap[i] for i in me.polygons[pi].vertices) for pi in ids)
                ev.to_mesh_clear()
            if not tris[1] or not tris[-1]:
                continue
            left, right = BVHTree.FromPolygons(pts[1], tris[1]), BVHTree.FromPolygons(pts[-1], tris[-1])
            hits = left.overlap(right)
            if hits:
                which.append(f)
                worst = max(worst, len(hits))
                zs = [sum(pts[1][i].z for i in tris[1][a]) / len(tris[1][a]) for a, _ in hits]
                heights.append((f, round(min(zs), 3), round(max(zs), 3)))
            else:
                near = min(right.find_nearest(v)[3] for v in pts[1])
                closest = min(closest, near)
        out[clip] = {"frames": f1 - f0, "frames_through": len(which), "worst_pairs": worst,
                     "closest_clear_m": round(closest, 4), "which": which, "heights_m": heights}
    return out


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    skel = _skeleton()
    top_z = skel.data.bones["DEF-thigh.L"].head_local.z - BELOW_M
    body = [o for o in bpy.data.objects if o.type == "MESH" and o.parent == skel and o.name.endswith("lod0")
            and any(r in o.name for r in ("thighs", "calves", "feet"))]
    stats = {"bare": measure(body, _sides(body, top_z))}
    for path in argv[1:]:
        objs = _load(path, skel)
        stats[path.split("/")[-1]] = measure(objs, _sides(objs, top_z))
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
    for k, v in stats.items():
        print("PASS", k, {c: {kk: vv for kk, vv in s.items() if kk != "which"} for c, s in v.items()}, flush=True)
    with open(argv[0], "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1)


if __name__ == "__main__":
    main()
