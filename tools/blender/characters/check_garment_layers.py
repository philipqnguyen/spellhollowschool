"""Layer against layer: an inner garment breaking through the one worn over it,
per frame of the shipped clips (the clothing pass, 2026-11-02).

    blender -b art/characters/<body>.blend --python tools/blender/characters/check_garment_layers.py -- \\
        <out.json> <outer glb> <inner glb> [--no-hide] [--drape]

Loads two exported garments (LOD0) onto a saved body blend (whose skeleton
carries the baked clips that ship), hides what the client hides
(`charlib/layers.py` `hide_covered`: the inner garment's triangles a higher
layer covers; files without covers hide nothing; `--no-hide` skips it) and
turns the sitting drape keys on in their clips. For each clip in CLIPS, every
STEP-th frame, an inner vertex breaks through when the outer garment's
surface lies between it and the skin under it (the nearest point of the
posed body, so a draped skirt lying on the lap is measured from the lap)
more than DEPTH_M before the vertex. Only vertices of faces the client
draws count. Recorded per clip: frames with any vertex
through, frames with one more than 1 cm through, the worst frame's count
and the deepest, and the worst frame's count on the back above the outer
garment's hem (`back_worst_verts`: inner vertices behind the spine at rest,
more than 3 cm above the outer's lowest back vertex; an untucked top's back,
where a skirt's yoke showing through reads as patches from behind).

`--drape` also measures, for the outer garment alone in the seated clips
(chair sit, floor sit), how far its fabric below the hips stands off the
legs: the 95th percentile and the largest distance of those vertices from
the posed legs' skin (a skirt ballooning out in front of the knees shows as
a large value).
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

from charlib import layers as LY  # noqa: E402

DEPTH_M = 0.002
CLIPS = {"walk": 1, "run": 1, "idle": 20, "sit": 6, "floor_sit": 12, "broom_ride": 6}
SEATED = ("sit", "floor_sit")


def _skeleton():
    return next(o for o in bpy.data.objects if o.type == "ARMATURE" and o.name.endswith("_skeleton"))


def _body():
    """The saved body as charlib/layers.py numbers it."""
    from types import SimpleNamespace

    skel = _skeleton()
    lod0 = {str(o["region"]): o for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0}
    return LY.Body(SimpleNamespace(meshes={0: lod0}, skel=skel))


def _load(path: str, skel) -> list:
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


def _posed(objs) -> tuple[list, list]:
    dg = bpy.context.evaluated_depsgraph_get()
    V, T = [], []
    for o in objs:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        off = len(V)
        V += [mw @ v.co for v in me.vertices]
        me.calc_loop_triangles()
        T += [tuple(off + i for i in t.vertices) for t in me.loop_triangles]
        ev.to_mesh_clear()
    return V, T


def _set(skel, clip: str, frame: int, objs) -> bool:
    return LY._set_frame(skel, clip, frame, objs)


def measure(outer_glb: str, inner_glb: str | None, hide: bool = True, drape: bool = False, clips: dict = CLIPS) -> dict:
    skel = _skeleton()
    body = _body()
    outer = _load(outer_glb, skel)
    inner = _load(inner_glb, skel) if inner_glb else []
    hidden = LY.hide_covered(outer + inner) if hide else {}
    anchors = {}
    for o in inner:
        me = o.data
        if "_BODYV" in me.attributes:
            a = np.zeros(len(me.vertices))
            me.attributes["_BODYV"].data.foreach_get("value", a)
            anchors[o.name] = a.astype(np.int64)
        else:
            anchors[o.name] = np.array([body.kd.find(o.matrix_world @ v.co)[1] for v in o.data.vertices], dtype=np.int64)
        used = np.zeros(len(me.vertices), dtype=bool)
        for p in me.polygons:
            used[list(p.vertices)] = True
        anchors[o.name] = np.where(used, anchors[o.name], -1)
    # The back above the outer's hem, at rest: inner vertices behind the spine
    # and over 3 cm above the outer's lowest vertex there.
    spine_y = float((skel.matrix_world @ skel.pose.bones["DEF-spine"].head).y)
    o_back = [o.matrix_world @ v.co for o in outer for v in o.data.vertices]
    o_back = [p.z for p in o_back if p.y > spine_y]
    hem_z = min(o_back) if o_back else -1e9
    back = {o.name: np.array([(o.matrix_world @ v.co).y > spine_y and (o.matrix_world @ v.co).z > hem_z + 0.03
                              for v in o.data.vertices], dtype=bool) for o in inner}
    stats = {"outer": os.path.basename(outer_glb), "inner": os.path.basename(inner_glb) if inner_glb else None,
             "hidden_faces": hidden, "clips": {}}
    ad = skel.animation_data or skel.animation_data_create()
    for t in ad.nla_tracks:
        t.mute = True
    for clip, step in clips.items():
        act = bpy.data.actions.get(clip)
        if act is None:
            continue
        f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
        frames, over, worst, deepest, back_worst = 0, 0, 0, 0.0, 0
        gaps = []
        for f in range(f0, f1, step):
            _set(skel, clip, f, outer + inner)
            Vb, Tb = _posed(body.objs)
            body_tree = BVHTree.FromPolygons(Vb, Tb, all_triangles=True)
            V, T = _posed(outer)
            tree = BVHTree.FromPolygons(V, T, all_triangles=True)
            n, deep_n, deep, nb = 0, 0, 0.0, 0
            if inner:
                Vi, _ = _posed(inner)
                off = 0
                for o in inner:
                    for i, b in enumerate(anchors[o.name]):
                        if b < 0:
                            continue
                        p = Vi[off + i]
                        q = body_tree.find_nearest(p, 0.4)[0]
                        if q is None:
                            continue
                        d = p - q
                        L = d.length
                        if L < 1e-4 or L > 0.4:
                            continue
                        u = d / L
                        hit = tree.ray_cast(q + u * 1e-4, u, L - DEPTH_M)
                        if hit[0] is not None:
                            n += 1
                            nb += bool(back[o.name][i])
                            depth = L - hit[3]
                            deep = max(deep, depth)
                            deep_n += depth > 0.01
                    off += len(o.data.vertices)
            frames += n > 0
            over += deep_n > 0
            worst = max(worst, n)
            back_worst = max(back_worst, nb)
            deepest = max(deepest, deep)
            if drape and clip in SEATED:
                legs = [o for o in body.objs if o.get("region") in ("hips", "thighs", "calves", "feet")]
                Vb, Tb = _posed(legs)
                bt = BVHTree.FromPolygons(Vb, Tb, all_triangles=True)
                hip_z = float((skel.matrix_world @ skel.pose.bones["DEF-thigh.L"].head).z)
                for v in V:
                    if v.z < hip_z:
                        hit = bt.find_nearest(v, 1.0)
                        if hit[0] is not None:
                            gaps.append(hit[3])
        s = {"frames": len(range(f0, f1, step)), "frames_with_poke": frames, "frames_over_1cm": over, "worst_frame_verts": worst, "back_worst_verts": back_worst,
             "deepest_m": round(deepest, 4)}
        if gaps:
            s["stand_off_p95_m"] = round(float(np.percentile(gaps, 95)), 3)
            s["stand_off_max_m"] = round(float(np.max(gaps)), 3)
        stats["clips"][clip] = s
    LY._set_frame(skel, None, 0, outer + inner)
    return stats


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    flags = {a for a in argv if a.startswith("--")}
    argv = [a for a in argv if not a.startswith("--")]
    stats = measure(argv[1], argv[2] if len(argv) > 2 else None, hide="--no-hide" not in flags, drape="--drape" in flags)
    for clip, s in stats["clips"].items():
        print("LAYERS", stats["outer"], stats["inner"], clip, s, flush=True)
    with open(argv[0], "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1)


if __name__ == "__main__":
    main()
