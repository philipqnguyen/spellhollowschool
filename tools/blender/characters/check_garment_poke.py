"""Legs breaking through skirts, kilts, robes and long coats, per frame of the shipped clips.

    blender -b art/characters/<body>.blend --python tools/blender/characters/check_garment_poke.py -- \\
        <out.json> <garment glb> [<under glb> ...] [--balloon]

Loads exported gear onto a saved body blend (art/characters/<body>.blend,
whose skeleton carries the baked clips that ship) and, for each clip in
CLIPS, poses every STEP-th frame and counts the leg vertices outside the
garment's fabric:

* The legs are the tights inside a skirt's own mesh (the mesh pieces that
  reach the ankles, within TIGHTS_M of the bare body at rest), or, for an
  outer garment worn over something (`under`: the trousers under a robe),
  that garment and the body's thighs and calves. Only below the hip joints.
* The fabric (the shell) is the garment's other faces, except sleeves (faces
  weighted to the arms) and, for outer garments, faces lying on the body
  below the hips (the nested inserts seen through an open front).
* A leg vertex breaks through when, on the ray from the nearest point of its
  leg's bone line out through it, the fabric is met more than DEPTH_M
  before the vertex.

Across the centre (rest |x| < CENTRE_M) it also records, per clip, the
largest stretch of a fabric edge that crosses the midline (posed length over
rest length, also front and back separately: a skirt splitting down the
centre front shows as a large ratio) and the largest separation of two vertices that share a position at
rest (unwelded seam copies: UV, pleat and lining seams), which must stay 0.

`--balloon` also measures, in the run, the largest gap between the fabric in
front of each knee and the legs (the robe's front panel standing off a
lifted knee). An open-front robe shows the legs between its panels; where a
panel edge lies across a leg, or a crossed shin rests on a panel (floor
sitting), that counts too, so read the numbers as before/after comparisons.
"""

from __future__ import annotations

import json
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import intersect_point_line
from mathutils.kdtree import KDTree

TIGHTS_M = 0.008
HUG_M = 0.015
DEPTH_M = 0.002
ARM = ("DEF-upper_arm", "DEF-forearm", "DEF-hand", "DEF-f_", "DEF-thumb", "DEF-palm")
CLIPS = {"walk": 1, "run": 1, "idle": 20, "sit": 6, "floor_sit": 12}
CENTRE_M = 0.03


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


def _pieces(o) -> list[int]:
    """Connected-piece id of every vertex."""
    parent = list(range(len(o.data.vertices)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for e in o.data.edges:
        a, b = find(e.vertices[0]), find(e.vertices[1])
        if a != b:
            parent[a] = b
    return [find(i) for i in range(len(parent))]


def measure(garment_glb: str, under: list[str] = (), clips: dict = CLIPS, balloon: bool = False) -> dict:
    """Breakthrough per clip on the open body blend (see the module docs)."""
    skel = _skeleton()
    body = [o for o in bpy.data.objects if o.type == "MESH" and o.parent == skel and o.name.endswith("lod0")]
    shell = _load(garment_glb, skel)
    unders = [o for p in under for o in _load(p, skel)]
    bv, bt = [], []
    for o in body:
        off = len(bv)
        bv.extend(o.matrix_world @ v.co for v in o.data.vertices)
        bt.extend(tuple(i + off for i in p.vertices) for p in o.data.polygons)
    body_tree = BVHTree.FromPolygons(bv, bt)
    leg_regions = [o for o in body if any(r in o.name for r in ("thighs", "calves"))]
    hip_z = skel.data.bones["DEF-thigh.L"].head_local.z - 0.05  # legs only: below the hip joints
    trunk_y = skel.data.bones["DEF-thigh.L"].head_local.y  # front (-Y) or back of the centre seam

    shell_faces, legs, centre, twins = {}, {}, {}, {}
    for o in shell:
        rest = [o.matrix_world @ v.co for v in o.data.vertices]
        comp = _pieces(o)
        low = {}
        for i, c in enumerate(comp):
            low[c] = min(low.get(c, 9.0), rest[i].z)
        near = [body_tree.find_nearest(v)[3] for v in rest]
        tights = [not under and low[comp[i]] < 0.2 and near[i] is not None and near[i] < TIGHTS_M for i in range(len(rest))]
        names = {g.index: g.name for g in o.vertex_groups}
        arm = [bool(v.groups) and names.get(max(v.groups, key=lambda g: g.weight).group, "").startswith(ARM) for v in o.data.vertices]

        def hugs(p, o=o):
            c = o.matrix_world @ p.center
            if not under or c.z >= hip_z:
                return False
            n = body_tree.find_nearest(c)
            return n[3] is not None and n[3] < HUG_M

        shell_faces[o.name] = [p.index for p in o.data.polygons
                               if not all(tights[i] for i in p.vertices) and not hugs(p) and not any(arm[i] for i in p.vertices)]
        legs[o.name] = [i for i in range(len(rest)) if tights[i] and rest[i].z < hip_z]
        keep = set(shell_faces[o.name])
        fabric = {i for p in o.data.polygons if p.index in keep for i in p.vertices}
        centre[o.name] = [(e.vertices[0], e.vertices[1], (rest[e.vertices[0]] - rest[e.vertices[1]]).length,
                           "front" if rest[e.vertices[0]].y < trunk_y else "back") for e in o.data.edges
                          if e.vertices[0] in fabric and e.vertices[1] in fabric and rest[e.vertices[0]].z < hip_z + 0.05
                          and rest[e.vertices[0]].x * rest[e.vertices[1]].x <= 0.0
                          and abs(rest[e.vertices[0]].x) < CENTRE_M and abs(rest[e.vertices[1]].x) < CENTRE_M
                          and (rest[e.vertices[0]] - rest[e.vertices[1]]).length > 1e-4]
        at = {}
        for i in fabric:
            at.setdefault(tuple(round(c, 5) for c in rest[i]), []).append(i)
        twins[o.name] = [(ids[0], j) for ids in at.values() for j in ids[1:]]
    for o in unders + (leg_regions if under else []):
        legs[o.name] = [i for i, v in enumerate(o.data.vertices) if (o.matrix_world @ v.co).z < hip_z]
    side = {name: [1 if (bpy.data.objects[name].matrix_world @ bpy.data.objects[name].data.vertices[i].co).x > 0 else -1 for i in ids]
            for name, ids in legs.items()}

    stats = {"garment": garment_glb.split("/")[-1], "under": [u.split("/")[-1] for u in under],
             "leg_verts": sum(len(v) for v in legs.values()), "clips": {}}
    ad = skel.animation_data or skel.animation_data_create()
    for t in ad.nla_tracks:
        t.mute = True
    for clip, step in clips.items():
        act = bpy.data.actions.get(clip)
        if act is None:
            continue
        ad.action = act
        if hasattr(ad, "action_slot") and act.slots:
            ad.action_slot = act.slots[0]
        f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
        frames, over_1cm, worst, deepest, gap_max = [], 0, 0, 0.0, 0.0
        stretch_max, twin_max = 0.0, 0.0
        by_side = {"front": 0.0, "back": 0.0}
        for f in range(f0, f1, step):
            bpy.context.scene.frame_set(f)
            dg = bpy.context.evaluated_depsgraph_get()
            sv, st = [], []
            for o in shell:
                ev = o.evaluated_get(dg)
                me = ev.to_mesh()
                off = len(sv)
                sv.extend(ev.matrix_world @ v.co for v in me.vertices)
                st.extend(tuple(i + off for i in me.polygons[pi].vertices) for pi in shell_faces[o.name])
                ev.to_mesh_clear()
            tree = BVHTree.FromPolygons(sv, st)
            off = 0
            for o in shell:
                for a, b, l0, where in centre[o.name]:
                    r = (sv[off + a] - sv[off + b]).length / l0
                    stretch_max = max(stretch_max, r)
                    by_side[where] = max(by_side[where], r)
                for a, b in twins[o.name]:
                    twin_max = max(twin_max, (sv[off + a] - sv[off + b]).length)
                off += len(o.data.vertices)
            pb = skel.pose.bones
            bones = {s: [(pb[f"DEF-thigh.{S}"].head, pb[f"DEF-thigh.{S}.001"].tail), (pb[f"DEF-shin.{S}"].head, pb[f"DEF-shin.{S}.001"].tail)]
                     for s, S in ((1, "L"), (-1, "R"))}
            n, n_deep, deep, leg_pts = 0, 0, 0.0, []
            for name, ids in legs.items():
                ev = bpy.data.objects[name].evaluated_get(dg)
                me = ev.to_mesh()
                for k, i in enumerate(ids):
                    v = ev.matrix_world @ me.vertices[i].co
                    leg_pts.append(v)
                    best, axis = 9.0, None
                    for a, b in bones[side[name][k]]:
                        _, t = intersect_point_line(v, a, b)
                        p = a.lerp(b, min(max(t, 0.0), 1.0))
                        if (v - p).length < best:
                            best, axis = (v - p).length, p
                    d = v - axis
                    if d.length < 1e-4 or d.length > 0.2:
                        continue
                    hit = tree.ray_cast(axis, d.normalized(), d.length - DEPTH_M)
                    if hit[0] is not None:
                        n += 1
                        deep = max(deep, d.length - hit[3])
                        n_deep += d.length - hit[3] > 0.01
                ev.to_mesh_clear()
            if balloon and clip == "run":
                kd = KDTree(len(leg_pts))
                for i, v in enumerate(leg_pts):
                    kd.insert(v, i)
                kd.balance()
                hips = pb["DEF-spine"].head
                fwd = (pb["DEF-spine"].matrix.to_quaternion() @ pb["DEF-spine"].bone.matrix_local.to_quaternion().inverted()) @ Vector((0, -1, 0))
                for s, S in ((1, "L"), (-1, "R")):
                    knee = pb[f"DEF-shin.{S}"].head
                    for v in sv:
                        if abs(v.z - knee.z) < 0.12 and (v - hips).dot(fwd) > (knee - hips).dot(fwd) - 0.02 and (v.x - hips.x) * s > 0:
                            gap_max = max(gap_max, kd.find(v)[2])
            over_1cm += n_deep > 0
            deepest = max(deepest, deep)
            if n:
                frames.append(f)
                worst = max(worst, n)
        stats["clips"][clip] = {"frames": len(range(f0, f1, step)), "frames_with_poke": len(frames), "frames_over_1cm": over_1cm,
                                "worst_frame_verts": worst, "deepest_m": round(deepest, 4),
                                "centre_stretch_max": round(stretch_max, 3), "centre_stretch_front": round(by_side["front"], 3),
                                "centre_stretch_back": round(by_side["back"], 3), "seam_twin_gap_max_m": round(twin_max, 5), "which": frames}
        if balloon and clip == "run":
            stats["clips"][clip]["front_panel_max_gap_m"] = round(gap_max, 4)
    return stats


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    balloon = "--balloon" in argv
    argv = [a for a in argv if a != "--balloon"]
    stats = measure(argv[1], argv[2:], balloon=balloon)
    for clip, s in stats["clips"].items():
        print("POKE", stats["garment"], clip, s, flush=True)
    with open(argv[0], "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1)


if __name__ == "__main__":
    main()
