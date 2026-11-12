"""Seated and lying poses against the floor and the furniture (character adoption).

    blender -b --factory-startup --python tools/blender/characters/check_seating.py -- \\
        --blend-dir <dir with body_a.blend, body_b.blend> [--world client/public/assets] [--json out.json]

docs/art/character-adoption.md. For each body at its minimum,
default and maximum height (uniform scale of the skeleton, as the client):

* **Seated clips** (`sit`, `desk_rest`, `desk_practice`; the root is the
  seat's top): how far the sit bones sit into or above the seat (`seat_gap`,
  the lowest hips/thighs vertex near the root), how far the heels are above
  a floor 0.46 m below the seat (`heel_gap`: about 0 at the maximum height,
  positive = hanging), the knee top above that floor and how far the knees
  reach ahead of the root;
* **Lying and floor clips** (`stun_fall` end, `stun_rise` start,
  `floor_rest`, `knockback`, the `floor_sit` on a floor seat): the lowest body vertex (no knee or foot below
  the floor);
* **Knee room**: the seated character (body plus the school uniform's
  robe, trousers and shoes, LOD0) placed on the castle's seats with the
  furniture in front, from the furniture code's own numbers (the castle and
  world files are not changed by this check): the class desk and bench of
  `world/school_rooms.py desk_row`, the Great Hall house table and bench of
  `castle_kit/pieces/hall_furniture.py` at the spacing of the world
  manifest's seat rows, and the common rooms' writing desk with the wooden
  chair and the velvet armchair of `world/furniture_models.py` /
  `world/build_dorms.py`. A box is cleared when no character vertex lies in
  it; the report gives the smallest gap (metres; negative = the knee or
  thigh is that deep inside).

* **The broom** (`broom_ride` and `broom_hover`; `check_broom`):
  the broom (charlib/broom.py) at the hips bone, not scaled, as the client
  draws it: the lowest vertex under the root, the crotch's gap over the
  handle's top, each hand's gap to the handle, body vertices inside the
  handle (judged against `BROOM_LIMITS`, `BROOM_SEATING ok|FAIL` per body),
  and the uniform's vertices inside the handle and the robe's spread
  against the legs (reported: the robes' small bell is accepted).

With `--world`, the Great Hall's own geometry (`world/great_hall.glb`) is
also tested at every seat row of the world manifest.

Exit code 0; read the report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
import numpy as np  # noqa: E402

import check_characters as CC  # noqa: E402

SEAT_DROP = 0.46  # seat top to the floor (the clips are authored for it at the maximum height)
OUTFIT = ("gear/body/robe_school_house_a", "gear/legs/trousers_school_a", "gear/shoes/oxford_school_a")
SEATED = (("sit", 30), ("desk_rest", 30), ("desk_practice", 0), ("desk_practice", 15), ("desk_practice", 30), ("desk_practice", 45))
FLOOR = (("stun_fall", 14), ("stun_fall", 22), ("stun_fall", 30), ("stun_fall", 40), ("stun_rise", 0), ("stun_rise", 6),
         ("stun_rise", 12), ("stun_rise", 20), ("stun_rise", 28), ("stun_rise", 36), ("floor_rest", 30), ("knockback", 8),
         ("floor_sit", 0), ("floor_sit", 45), ("floor_sit", 90), ("floor_sit", 135),  # On a floor seat
         ("knockback", 20))

# Furniture in the sitter's frame: the root on the seat top, facing -Y;
# boxes (x0, x1, y0, y1, z0, z1) with z above the seat top. From the code's
# numbers (see the module docs).
FURNITURE = {
    # world/school_rooms.py desk_row: seat 0.46 (SEAT_HEIGHT), the root at
    # the seat's "hips" mark, the desk top's front edge 0.22 ahead, 0.5 deep,
    # top 0.76 (0.05 thick), a shelf under it 0.61-0.63 high from 0.47 to 0.68
    # ahead, a modesty panel from 0.12 high at 0.68-0.72 ahead.
    "class_desk": {"seat": 0.46, "boxes": {
        "desk_top": (-0.6, 0.6, -0.72, -0.22, 0.71 - 0.46, 0.76 - 0.46),
        "desk_lip": (-0.6, 0.6, -0.22, -0.19, 0.725 - 0.46, 0.755 - 0.46),
        "shelf": (-0.6, 0.6, -0.68, -0.47, 0.61 - 0.46, 0.63 - 0.46),
        "modesty_panel": (-0.6, 0.6, -0.72, -0.68, 0.12 - 0.46, 0.71 - 0.46)}},
    # castle_kit hall_furniture: bench 0.45 high, 0.4 deep; house table top
    # 0.76 (0.07 thick), 1 m deep, aprons 0.12 deep under the long edges
    # 0.08-0.12 in; the world manifest's bench rows put the table's near
    # edge 0.65 ahead of the bench's middle (bench 10.45-10.85, the facing
    # bench 12.75-13.15: table 11.3-12.3).
    "great_hall_table": {"seat": 0.45, "boxes": {
        "table_top": (-0.6, 0.6, -1.65, -0.65, 0.69 - 0.45, 0.76 - 0.45),
        "apron": (-0.6, 0.6, -0.77, -0.73, 0.57 - 0.45, 0.69 - 0.45)}},
    # world/build_dorms.py: the wooden chair (seat 0.46) 0.7 m from the
    # writing desk's centre (0.6 deep, top 0.76, a front apron to 0.13 below
    # the top, a drawer pedestal 0.18-0.58 to one side of the centre line).
    "common_room_desk": {"seat": 0.46, "boxes": {
        "desk_top": (-0.6, 0.6, -1.0, -0.4, 0.725 - 0.46, 0.76 - 0.46),
        "apron": (-0.6, 0.6, -0.45, -0.43, 0.63 - 0.46, 0.725 - 0.46),
        "pedestal_left": (0.18, 0.58, -0.97, -0.43, -0.46, 0.725 - 0.46),
        "pedestal_right": (-0.58, -0.18, -0.97, -0.43, -0.46, 0.725 - 0.46)}},
    # world/furniture_models.py armchair_velvet: 0.85 wide, arms 0.15 wide
    # each up to 0.66 (the cushion top 0.44), so 0.51 between the arms.
    "common_room_armchair": {"seat": 0.44, "boxes": {
        "arm_left": (0.255, 0.425, -0.4, 0.3, -0.36, 0.66 - 0.44),
        "arm_right": (-0.425, -0.255, -0.4, 0.3, -0.36, 0.66 - 0.44)}},
}


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--blend-dir", required=True)
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--world", default="", help="client assets root (for world/great_hall.glb and the seat rows)")
    p.add_argument("--json", default="")
    return p.parse_args(argv)


def gather(objs) -> tuple[np.ndarray, list[str]]:
    pts, regs = [], []
    for o in objs:
        p, _ = CC.evaluated(o)
        pts.append(p)
        regs += [str(o.get("region") or o.get("slot") or "item")] * len(p)
    return (np.vstack(pts) if pts else np.zeros((0, 3))), regs


def box_gap(P: np.ndarray, box) -> tuple[float, int]:
    """(smallest clearance under/around the box, vertices inside it). The
    clearance is measured for vertices within the box's x and y span: the
    box bottom minus the highest such vertex below it (negative if a vertex
    is inside); vertices above the box (hands on a desk) do not count."""
    x0, x1, y0, y1, z0, z1 = box
    sel = (P[:, 0] >= x0) & (P[:, 0] <= x1) & (P[:, 1] >= y0) & (P[:, 1] <= y1)
    if not sel.any():
        return None, 0
    q = P[sel]
    inside = int(((q[:, 2] >= z0) & (q[:, 2] <= z1)).sum())
    below = q[q[:, 2] < z1]
    if not len(below):
        return None, inside
    top = float(below[:, 2].max())
    return round(z0 - top, 4), inside


def check_body(path: str, world: dict | None) -> dict:
    bpy.ops.wm.open_mainfile(filepath=path)
    body_id = "_".join(os.path.splitext(os.path.basename(path))[0].split("_")[:2])
    skel = bpy.data.objects[f"{body_id}_skeleton"]
    parts = [o for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0]
    items = [o for o in bpy.data.objects if o.type == "MESH" and o.get("asset") in OUTFIT and o.get("lod") == 0]
    hides = set().union(*[set(o.get("hides", [])) for o in items]) if items else set()
    for o in parts + items:
        o.hide_set(False)
        o.hide_viewport = False
    out: dict = {"seated": {}, "floor": {}, "knee_room": {}}
    for h in CC.HEIGHTS:
        s = CC.set_height(skel, h)
        for clip, frame in SEATED:
            if clip not in bpy.data.actions:
                continue
            CC.pose(skel, clip, frame)
            P, R = gather(parts)
            R = np.array(R)
            hipthigh = np.isin(R, ["hips", "thighs"]) & (np.abs(P[:, 1]) < 0.18) & (np.abs(P[:, 0]) < 0.25)
            feet = np.isin(R, ["feet"])
            legs = np.isin(R, ["thighs", "calves"])
            seat_gap = float(P[hipthigh, 2].min()) if hipthigh.any() else float("nan")
            heel = float(P[feet, 2].min()) + SEAT_DROP
            front = float(P[legs, 1].min())
            knee_sel = legs & (P[:, 1] < front + 0.12)
            knee_top = float(P[knee_sel, 2].max()) + SEAT_DROP
            key = f"{clip}@{frame}"
            out["seated"].setdefault(key, {})[h] = {"scale": round(s, 4), "seat_gap": round(seat_gap, 4),
                                                    "heel_gap": round(heel, 4), "knee_top_above_floor": round(knee_top, 4),
                                                    "knee_ahead": round(-front, 4)}
            # Knee room, clothed (body regions the outfit leaves visible + the outfit).
            visible = [o for o in parts if o.get("region") not in hides]
            PC, _ = gather(visible + items)
            body_x = P[np.isin(R, ["hips", "thighs", "calves"]) & (P[:, 2] > -0.3)][:, 0]
            out["seated"][key][h]["leg_width"] = round(float(body_x.max() - body_x.min()), 4)
            out["seated"][key][h]["leg_width_clothed"] = round(float(np.ptp(PC[(PC[:, 2] > -0.3) & (PC[:, 2] < 0.25)][:, 0])), 4)
            for name, fur in FURNITURE.items():
                # The furniture's seat height differs from the clip's 0.46 m drop:
                # the root is the seat top, the floor is `seat` below it.
                rows = out["knee_room"].setdefault(name, {}).setdefault(key, {})
                rows[h] = {"uniform": {b: dict(zip(("gap", "inside"), box_gap(PC, bx))) for b, bx in fur["boxes"].items()},
                           "body": {b: dict(zip(("gap", "inside"), box_gap(P, bx))) for b, bx in fur["boxes"].items()}}
            if world and clip == "sit":
                rows = out.setdefault("great_hall_world", {}).setdefault(key, {})
                rows[h] = world_room(PC, world)
        for clip, frame in FLOOR:
            if clip not in bpy.data.actions:
                continue
            CC.pose(skel, clip, frame)
            P, R = gather(parts)
            R = np.array(R)
            low = {r: round(float(P[np.array(R) == r, 2].min()), 4) for r in ("thighs", "calves", "feet", "hips", "torso", "hands")
                   if (np.array(R) == r).any()}
            out["floor"].setdefault(f"{clip}@{frame}", {})[h] = {"lowest": round(float(P[:, 2].min()), 4), "by_region": low}
    out["broom"] = check_broom(skel, parts, items)
    CC.set_height(skel, "default")
    CC.pose(skel, None, 0)
    return out


# The riding clips against the broom (charlib/broom.py), at
# every height. The broom stands at the hips bone (DEF-spine's head), not
# scaled, its handle's top SEAT_BELOW under it, as the client draws it.
BROOM_FRAMES = (("broom_ride", 0), ("broom_ride", 15), ("broom_ride", 30), ("broom_ride", 45),
                ("broom_hover", 0), ("broom_hover", 22), ("broom_hover", 45), ("broom_hover", 67))
BROOM_LIMITS = {
    "lowest": -0.25,  # the lowest vertex under the root (the flight rules keep the root 0.3 m up)
    "seat_gap": (-0.025, 0.05),  # the crotch over the handle's top (sitting on it, not sunk in or floating)
    "hand_gap": 0.03,  # each hand's nearest vertex to the handle's surface (resting on it)
    "hand_deep": -0.015,  # how deep a hand's fingers may wrap into the handle (2 mm short of its axis)
    "body_deep": -0.025,  # how deep any other body vertex lies in the handle (the crotch on its top)
}


def check_broom(skel, parts, items) -> dict:
    """Per clip frame and height: the lowest vertex, the crotch's gap over
    the handle, the hands' gaps to it, body and clothing vertices inside the
    handle, and the robe's spread (the 1d "robes balloon when seated" item:
    its width under the seat against the legs'). `ok` and `fails` judge the
    body against BROOM_LIMITS; clothing is reported, not judged."""
    from charlib import broom as BR
    from charlib import proportions as P_

    res: dict = {"limits": BROOM_LIMITS, "rows": {}, "fails": []}
    for h in CC.HEIGHTS:
        scale = CC.set_height(skel, h) * P_.of_skeleton(skel, skel.name.replace("_skeleton", "")).height / 1.78
        for clip, frame in BROOM_FRAMES:
            if clip not in bpy.data.actions:
                res["fails"].append(f"no clip {clip}")
                continue
            CC.pose(skel, clip, frame)
            sp = skel.matrix_world @ skel.pose.bones["DEF-spine"].head
            zc = sp.z - BR.SEAT_BELOW - BR.HANDLE_R
            top = zc + BR.HANDLE_R

            def depth(Q, sp=sp, zc=zc):
                """Distance outside the handle's surface (negative inside), along its length."""
                d = np.hypot(Q[:, 0] - sp.x, Q[:, 2] - zc) - BR.HANDLE_R
                along = (Q[:, 1] > sp.y - BR.HANDLE_FRONT) & (Q[:, 1] < sp.y + BR.BINDING[0])
                return np.where(along, d, np.inf)

            P, R = gather(parts)
            R = np.array(R)
            dP = depth(P)
            row = {"hips_bone": [round(float(v), 4) for v in sp], "lowest": round(float(P[:, 2].min()), 4)}
            # The crotch: the lowest hips or thighs vertex right over the handle (the inner
            # thighs hang either side of it, below its top, and do not count).
            crotch = (np.isin(R, ["hips", "thighs"]) & (np.abs(P[:, 0] - sp.x) < BR.HANDLE_R)
                      & (np.abs(P[:, 1] - sp.y) < 0.12) & (P[:, 2] > zc))
            row["seat_gap"] = round(float(P[crotch, 2].min() - top), 4) if crotch.any() else None
            hands = R == "hands"
            # The left hand rests behind the right one (anim.broom_hands, BR.GRIPS).
            mid = sp.y - 0.5 * (BR.GRIPS[0] + BR.GRIPS[1]) * scale
            for nm, sel in (("left", hands & (P[:, 1] > mid)), ("right", hands & (P[:, 1] <= mid))):
                row[f"hand_gap_{nm}"] = round(float(dP[sel].min()), 4) if sel.any() else None
            inside = ~hands & (dP < 0)
            row["body_in_handle"] = int(inside.sum())
            row["body_in_handle_regions"] = sorted(set(R[inside].tolist()))
            row["body_deepest"] = round(float(dP[~hands].min()), 4)
            row["clothing_in_handle"] = {str(o.get("asset")).split("/")[-1]: int((depth(gather([o])[0]) < 0).sum()) for o in items}
            robe = [o for o in items if "robe" in str(o.get("asset"))]
            if robe:
                Q = gather(robe)[0]
                low = Q[Q[:, 2] < top - 0.05]
                legs = P[np.isin(R, ["thighs", "calves"]) & (P[:, 2] < top - 0.05)]
                if len(low) and len(legs):
                    row["robe_width"] = round(float(np.ptp(low[:, 0])), 4)
                    row["legs_width"] = round(float(np.ptp(legs[:, 0])), 4)
                    row["robe_spread"] = round(row["robe_width"] - row["legs_width"], 4)
                    row["robe_lowest"] = round(float(Q[:, 2].min()), 4)
            fails = []
            if row["lowest"] < BROOM_LIMITS["lowest"]:
                fails.append("lowest")
            g = row["seat_gap"]
            if g is None or not (BROOM_LIMITS["seat_gap"][0] <= g <= BROOM_LIMITS["seat_gap"][1]):
                fails.append("seat_gap")
            for nm in ("left", "right"):
                hg = row[f"hand_gap_{nm}"]
                if hg is None or hg > BROOM_LIMITS["hand_gap"] or hg < BROOM_LIMITS["hand_deep"]:
                    fails.append(f"hand_{nm}")
            if row["body_deepest"] < BROOM_LIMITS["body_deep"]:
                fails.append("body_deep")
            row["ok"] = not fails
            res["rows"].setdefault(f"{clip}@{frame}", {})[h] = row
            res["fails"] += [f"{clip}@{frame} {h}: {f}" for f in fails]
    res["ok"] = not res["fails"]
    return res


def load_world(root: str) -> dict | None:
    """The Great Hall's geometry (BVH) and seat rows, imported once into a
    scene of its own."""
    from mathutils.bvhtree import BVHTree

    man = os.path.join(root, "world", "manifest.json")
    if not os.path.exists(man):
        return None
    with open(man, encoding="utf-8") as f:
        cell = next((c for c in json.load(f)["cells"] if c["id"] == "great_hall"), None)
    if cell is None:
        return None
    glb = os.path.join(root, "world", cell["file"])
    before = set(bpy.data.objects)
    try:
        bpy.ops.import_scene.gltf(filepath=glb)
    except Exception as e:  # noqa: BLE001
        print(f"world import failed: {e}")
        return None
    new = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    verts, tris = [], []
    dg = bpy.context.evaluated_depsgraph_get()
    for o in new:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        base = len(verts)
        M = o.matrix_world
        verts += [tuple(M @ v.co) for v in me.vertices]
        tris += [tuple(base + i for i in t.vertices) for t in me.loop_triangles]
        ev.to_mesh_clear()
    for o in new:
        bpy.data.objects.remove(o)
    # Blender's import is Z-up; the manifest is glTF Y-up (x, y up, z): Blender (x, -z, y).
    return {"verts": verts, "tris": tris, "seats": cell.get("seats", []), "tree": BVHTree.FromPolygons(verts, tris, all_triangles=True)}


def world_room(PC: np.ndarray, world: dict) -> dict:
    """The clothed seated character placed on every seat row of the Great
    Hall (in the middle of the row's rectangle, facing its yaw, the root on
    its top, as castle/seats.ts places sitters): per row, how many vertices
    lie inside the hall's geometry (ray parity test) and the deepest one."""
    from mathutils import Vector

    tree = world["tree"]
    res = {}
    for i, (x0, z0, x1, z1, y, yaw, *_fit) in enumerate(world["seats"]):
        cx, cz = 0.5 * (x0 + x1), 0.5 * (z0 + z1)
        # Sample along long benches: the middle and quarter points.
        long_x = (x1 - x0) > (z1 - z0)
        spots = [(cx, cz)]
        if max(x1 - x0, z1 - z0) > 2.0:
            for f in (0.27, 0.5, 0.73):
                spots.append((x0 + (x1 - x0) * f, cz) if long_x else (cx, z0 + (z1 - z0) * f))
        worst = {"inside": 0, "deepest": 0.0}
        for sx, sz in spots:
            # Character frame: faces -Y (Blender). glTF yaw 0 faces +z (glTF) = -Y (Blender).
            c, s_ = math.cos(yaw), math.sin(yaw)
            inside, deepest = 0, 0.0
            for p in PC[::3]:
                if p[2] < 0.04 and p[1] > -0.2:
                    continue  # resting on the seat itself (flesh and cloth on the seat top), not furniture in the way
                # Rotate the character's (x, -y forward) by yaw about up, then to Blender world.
                fx, fy = p[0], -p[1]  # x right, forward
                gx = sx + fx * c + fy * s_  # glTF x
                gz = sz - fx * s_ + fy * c  # glTF z
                q = Vector((gx, -gz, y + p[2]))
                loc, nrm, idx, dist = tree.find_nearest(q, 0.08)
                if loc is None:
                    continue
                d = (q - loc).dot(nrm)
                if d < -0.002:
                    inside += 1
                    deepest = min(deepest, d)
            if inside > worst["inside"]:
                worst = {"inside": inside, "deepest": round(deepest, 4), "at": [round(sx, 2), round(sz, 2)]}
        res[f"row{i}_y{y}"] = worst
    return res


def main() -> None:
    a = args()
    out = {}
    world = None
    for body in a.bodies.split(","):
        out[body] = check_body(os.path.join(os.path.abspath(a.blend_dir), f"{body}.blend"), None)
        if a.world:
            # The world is imported into the opened body file (one scene).
            world = load_world(os.path.abspath(a.world))
            if world:
                skel = bpy.data.objects[f"{body}_skeleton"]
                parts = [o for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0]
                items = [o for o in bpy.data.objects if o.type == "MESH" and o.get("asset") in OUTFIT and o.get("lod") == 0]
                hides = set().union(*[set(o.get("hides", [])) for o in items]) if items else set()
                rows = {}
                for h in CC.HEIGHTS:
                    CC.set_height(skel, h)
                    CC.pose(skel, "sit", 30)
                    PC, _ = gather([o for o in parts if o.get("region") not in hides] + items)
                    rows[h] = world_room(PC, world)
                out[body]["great_hall_world"] = rows
    print(json.dumps(out, indent=1))
    for body, rows in out.items():  # The broom verdict per body
        b = rows.get("broom", {})
        print(f"BROOM_SEATING {body}: {'ok' if b.get('ok') else 'FAIL'} {b.get('fails', [])[:12]}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, sort_keys=True)
            f.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
