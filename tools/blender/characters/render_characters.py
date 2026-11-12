"""Review renders of the exported characters (what the client loads).

    blender -b --factory-startup --python tools/blender/characters/render_characters.py -- \\
        --assets client/public/assets --out art/renders/characters [--only bodies,faces,outfits,poses,anims,hair]

Imports the .glb files listed in <assets>/characters/manifest.json, tints them
with the client formula (skin tones, hair colours and palette dyes from the
data files), hides the body regions each item lists, and writes contact
sheets:

* `bodies_<body>.png`: neutral and the three build extremes, four views each;
* `faces_<body>.png`: every face slider at both ends and every expression;
* `outfit_<n>_<body>.png`: outfit combinations in four house colours;
* `poses_<outfit>_<body>.png`: the spec's clipping test poses (rest, walk,
  run, sit, cast, arms_up, crouch) at the largest and smallest builds;
* `anim_<clip>_<body>.png`: animation contact sheets, dressed;
* `hair_<body>.png` / `hair_cap_<body>.png`: every hair style, and under a hat;
* `fashion_<n>_<name>_<body>.png`: turntables (eight angles) of outfits that
  together wear every crafted garment , with a detail sheet of
  the head and feet (`fashion_<n>_<name>_detail_<body>.png`; pick outfits
  with `--fashion witch,cloche`);
* `fashion_builds_<body>.png`: those outfits walking, running and sitting at
  the heaviest and slimmest builds.
* `spell_<clip>_<body>.png`: the gesture, cast, dueling and
  class clips of `charlib/anim_spells.py`, in the uniform with the wand
  (`vfx/wand.glb`) in hand.
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
from mathutils import Quaternion, Vector  # noqa: E402

from charlib import atlas as A  # noqa: E402
from charlib import content  # noqa: E402
from charlib import render as RD  # noqa: E402

PALETTE = {c["index"]: c["hex"] for c in content.load_json("data/items/palette.json")["colours"]}
HOUSES = content.load_json("data/items/palette.json")["houses"]
CREATOR = content.creator()
ITEMS = {i["asset"]: i for i in content.clothing_items()}
HOUSE_ROBE = {"roarwing": "a", "burrowsby": "b", "skytalon": "c", "serpentyne": "d"}

# (name, items, hair style index, house, skin tone index, hair colour index)
OUTFITS = [
    ("uniform_trousers", ["gear/body/robe_school_house_{h}", "gear/legs/trousers_school_a", "gear/shoes/oxford_school_a"], 2,
     "roarwing", 3, 1),
    ("uniform_skirt", ["gear/body/robe_school_house_{h}", "gear/legs/skirt_pleated_a", "gear/shoes/loafers_buckle_a",
                       "gear/hat/pointed_a"], 4, "skytalon", 8, 0),
    ("jumper", ["gear/body/jumper_vneck_school_a", "gear/legs/trousers_school_a", "gear/shoes/oxford_school_a"], 11,
     "burrowsby", 1, 7),
    ("shirt_tie", ["gear/body/shirt_oxford_tie_a", "gear/legs/skirt_pleated_a", "gear/shoes/boots_lace_a"], 6,
     "serpentyne", 5, 3),
    ("weekend", ["gear/body/sweater_cable_knit_a", "gear/legs/trousers_corduroy_a", "gear/shoes/boots_lace_a",
                 "gear/hat/bobble_knit_a"], 8, "roarwing", 9, 0),
    ("summer", ["gear/body/shirt_linen_rolled_a", "gear/legs/trousers_corduroy_a", "gear/shoes/loafers_buckle_a"], 1,
     "burrowsby", 2, 5),
    ("travel", ["gear/body/cloak_travelling_a", "gear/legs/trousers_school_a", "gear/shoes/boots_lace_a",
                "gear/hat/pointed_a"], 7, "serpentyne", 6, 10),
]
VIEWS = {"front": (0, 4), "three": (35, 8), "side": (90, 4), "back": (180, 6)}

# Outfits that between them wear every crafted garment, shot as
# turntables (`fashion_<n>_<name>_<body>.png`). Same fields as OUTFITS.
FASHION = [
    ("witch", ["gear/hat/witch_wide_brim_a", "gear/body/robe_formal_velvet_a", "gear/legs/trousers_tapered_wool_a",
               "gear/shoes/boots_ankle_buckle_a"], 4, "serpentyne", 2, 1),
    ("cloche", ["gear/hat/cloche_felt_a", "gear/body/blouse_ruffled_silk_a", "gear/legs/skirt_aline_wool_a",
                "gear/shoes/mary_janes_a"], 6, "burrowsby", 8, 3),
    ("beret", ["gear/hat/beret_velvet_a", "gear/body/jacket_tailored_velvet_a", "gear/legs/trousers_wide_leg_a",
               "gear/shoes/brogues_wingtip_a"], 2, "roarwing", 5, 0),
    ("cable", ["gear/hat/beanie_cable_a", "gear/body/cardigan_chunky_a", "gear/legs/kilt_tartan_a",
               "gear/shoes/boots_riding_tall_a"], 11, "skytalon", 1, 7),
    ("slouch", ["gear/hat/cap_slouch_knit_a", "gear/body/turtleneck_ribbed_a", "gear/legs/skirt_midi_wrap_a",
                "gear/shoes/boots_dragonhide_a"], 1, "roarwing", 10, 5),
    ("earflap", ["gear/hat/earflap_knit_a", "gear/body/jumper_fair_isle_a", "gear/legs/trousers_corduroy_a",
                 "gear/shoes/boots_lace_a"], 8, "burrowsby", 3, 2),
    ("greatcoat", ["gear/hat/hood_fur_lined_a", "gear/body/greatcoat_double_a", "gear/legs/trousers_school_a",
                   "gear/shoes/boots_riding_tall_a"], 7, "serpentyne", 6, 10),
    ("cloak", ["gear/hat/hood_cowl_a", "gear/body/cloak_hooded_wool_a", "gear/legs/trousers_wide_leg_a",
               "gear/shoes/boots_dragonhide_a"], 12, "skytalon", 9, 4),
    ("dress_robe", ["gear/hat/ribbon_bow_a", "gear/body/robe_dress_embroidered_a", "gear/legs/skirt_pleated_a",
                    "gear/shoes/mary_janes_a"], 4, "roarwing", 4, 6),
    ("waistcoat", ["gear/hat/comb_feathered_a", "gear/body/waistcoat_brocade_a", "gear/legs/trousers_tapered_wool_a",
                   "gear/shoes/brogues_wingtip_a"], 9, "burrowsby", 7, 8),
]
TURNTABLE = (0, 45, 90, 135, 180, 225, 270, 315)


def args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--assets", default="client/public/assets")
    p.add_argument("--out", default="art/renders/characters")
    p.add_argument("--only", default="bodies,faces,outfits,poses,anims,hair,fashion,fashion_builds,spells")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--fashion", default="", help="fashion outfit names to render (default all)")
    return p.parse_args(argv)


# ----------------------------------------------------------------------------
# Scene helpers
# ----------------------------------------------------------------------------


class Character:
    """A body glb plus attached item glbs, sharing the body's armature."""

    def __init__(self, assets: str, manifest: dict, body: str):
        self.assets = assets
        self.manifest = manifest
        self.body = body
        self.arm, self.regions = self._import(manifest["assets"][f"characters/{body}"]["file"])
        self.items: dict[str, list] = {}

    def _import(self, rel: str):
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=os.path.join(self.assets, rel))
        new = [o for o in bpy.data.objects if o not in before]
        arm = next(o for o in new if o.type == "ARMATURE")
        meshes = [o for o in new if o.type == "MESH"]
        for o in meshes:
            if o.get("lod", 0) == 1 or o.name.endswith("_lod1"):
                o.hide_render = True
        return arm, meshes

    def add(self, asset: str, cap: bool = False) -> list:
        entry = self.manifest["assets"][asset]
        rel = (entry["cap_files"] if cap else entry["files"])[self.body]
        arm, meshes = self._import(rel)
        for o in meshes:
            o.parent = self.arm
            o.matrix_parent_inverse.identity()
            for m in o.modifiers:
                if m.type == "ARMATURE":
                    m.object = self.arm
        bpy.data.objects.remove(arm)
        self.items[asset] = meshes
        return meshes

    def clear_items(self) -> None:
        for objs in self.items.values():
            for o in objs:
                bpy.data.objects.remove(o)
        self.items.clear()
        for o in self.regions:
            o.hide_render = o.get("lod", 0) == 1 or o.name.endswith("_lod1")

    def hide_regions(self, regions: set[str]) -> None:
        for o in self.regions:
            lod1 = o.get("lod", 0) == 1 or o.name.endswith("_lod1")
            o.hide_render = lod1 or o.get("region") in regions

    def visible(self) -> list:
        return [o for o in self.regions + [x for v in self.items.values() for x in v] if not o.hide_render]

    def set_key(self, name: str, value: float) -> None:
        for o in self.regions + [x for v in self.items.values() for x in v]:
            keys = o.data.shape_keys
            if keys and name in keys.key_blocks:
                keys.key_blocks[name].value = value

    def reset_keys(self) -> None:
        for o in self.regions + [x for v in self.items.values() for x in v]:
            keys = o.data.shape_keys
            if keys:
                for kb in keys.key_blocks[1:]:
                    kb.value = 0.0

    def tint(self, skin: str, hair: str, dyes: dict[str, tuple]) -> None:
        body_mat = RD.tint_material(f"skin_{self.body}", skin, hair)
        for o in self.regions:
            _assign(o, body_mat)
        for asset, objs in self.items.items():
            if asset.startswith("hair/"):
                mat = RD.tint_material(f"hair_{asset}", hair)
            else:
                d = dyes.get(asset, ("#808080", "#808080", "#808080"))
                mat = RD.tint_material(f"dye_{asset}_{'_'.join(d)}", *d)
            for o in objs:
                _assign(o, mat)

    def play(self, clip: str | None, frame: float = 0.0) -> None:
        ad = self.arm.animation_data or self.arm.animation_data_create()
        for t in list(ad.nla_tracks):
            t.mute = True
        if clip is None:
            ad.action = None
            for pb in self.arm.pose.bones:
                pb.rotation_quaternion = (1, 0, 0, 0)
                pb.location = (0, 0, 0)
        else:
            act = _find_action(clip)
            ad.action = act
            if hasattr(ad, "action_slot") and act.slots:
                ad.action_slot = act.slots[0]
        bpy.context.scene.frame_set(int(frame))


def _assign(o, mat) -> None:
    attr = o.data.color_attributes[0].name if o.data.color_attributes else "Color"
    for n in mat.node_tree.nodes:
        if n.type == "VERTEX_COLOR":
            n.layer_name = attr
    o.data.materials.clear()
    o.data.materials.append(mat)


def _find_action(clip: str):
    for a in bpy.data.actions:
        if a.name == clip or a.name.startswith(clip + "_") or a.name.startswith(clip + "."):
            return a
    raise KeyError(f"no action {clip}: {[a.name for a in bpy.data.actions]}")


def dyes_for(items: list[str], house: str) -> dict[str, tuple]:
    hc = HOUSES[house]
    out = {}
    for asset in items:
        item = ITEMS.get(asset)
        if item is None:  # a garment-shape design not in the catalog yet: default dyes
            continue
        d = dict(item["dyes"])
        for ch, src in (item.get("house_dyes") or {}).items():
            d[ch] = hc["primary"] if src == "house_primary" else hc["secondary"]
        out[asset] = tuple(PALETTE[d[c]] for c in ("primary", "secondary", "trim"))
    return out


def resolve(items: list[str], house: str) -> list[str]:
    return [a.format(h=HOUSE_ROBE[house]) for a in items]


def dress(ch: Character, items: list[str], hair_idx: int, house: str, skin: int, hair_col: int) -> None:
    ch.clear_items()
    hat = any(a.startswith("gear/hat/") for a in items)
    hides = set()
    for a in items:
        ch.add(a)
        hides |= set(ch.manifest["assets"][a]["hides"])
    hair = CREATOR["hair_styles"][hair_idx]["asset"]
    ch.add(hair, cap=hat)
    hides |= set(ch.manifest["assets"][hair]["hides"])
    ch.hide_regions(hides)
    ch.tint(CREATOR["skin_tones"][skin]["hex"], CREATOR["hair_colours"][hair_col]["hex"], dyes_for(items, house))


def height(ch: Character) -> float:
    return ch.manifest["height"]["bodies"][ch.body]["default_height_m"]


def shoot_views(ch: Character, out_dir: str, tag: str, views=("front", "three", "side", "back"), dist: float = 3.6,
                target_z: float | None = None, lens: float = 60) -> list[str]:
    paths = []
    z = height(ch) * 0.52 if target_z is None else target_z
    for v in views:
        az, el = VIEWS[v]
        RD.aim((0, 0, z), az, el, dist, lens=lens)
        paths.append(RD.shoot(os.path.join(out_dir, f"_{tag}_{v}.png")))
    return paths


# ----------------------------------------------------------------------------
# Test poses (posed directly on the deform skeleton)
# ----------------------------------------------------------------------------


def _rot(arm, bone: str, axis, deg: float) -> None:
    """Rotate a pose bone about a world-space (rest) axis."""
    pb = arm.pose.bones[bone]
    rest = pb.bone.matrix_local.to_quaternion()
    q = Quaternion(Vector(axis).normalized(), math.radians(deg))
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = (rest.inverted() @ q @ rest) @ pb.rotation_quaternion


def test_pose(ch: Character, name: str) -> None:
    """The spec's clipping poses; walk/run/sit come from the baked clips."""
    clips = {"walk": ("walk", 4), "run": ("run", 3), "sit": ("sit", 10)}
    if name in clips:
        ch.play(*clips[name])
        return
    ch.play(None)
    arm = ch.arm
    if name == "cast":
        _rot(arm, "DEF-upper_arm.R", (-1, 0, 0), 95)
        _rot(arm, "DEF-upper_arm.R", (0, -1, 0), 35)
        _rot(arm, "DEF-forearm.R", (-1, 0, 0), 20)
        _rot(arm, "DEF-upper_arm.L", (0, -1, 0), -35)
    elif name == "arms_up":
        for side, sign in (("L", 1), ("R", -1)):
            _rot(arm, f"DEF-upper_arm.{side}", (0, -sign, 0), 85)
    elif name == "crouch":
        arm.pose.bones["DEF-spine"].location = (0, 0, 0)
        for side in ("L", "R"):
            _rot(arm, f"DEF-thigh.{side}", (-1, 0, 0), 95)
            _rot(arm, f"DEF-shin.{side}", (1, 0, 0), 120)
            _rot(arm, f"DEF-foot.{side}", (-1, 0, 0), 25)
        _rot(arm, "DEF-spine.001", (1, 0, 0), 25)
        arm.pose.bones["root"].location = (0, 0, 0)
        # Lower the whole character so the feet stay on the floor.
        arm.location = (0, 0, -0.33 * height(ch) / 1.78)
    if name != "crouch":
        arm.location = (0, 0, 0)
    bpy.context.view_layer.update()


# ----------------------------------------------------------------------------
# Sheets
# ----------------------------------------------------------------------------


def sheet_bodies(ch: Character, out: str) -> None:
    ch.clear_items()
    ch.hide_regions({"scalp"})
    ch.add(CREATOR["hair_styles"][12]["asset"])
    ch.tint(CREATOR["skin_tones"][3]["hex"], CREATOR["hair_colours"][1]["hex"], {})
    paths = []
    for key in (None, "build_slim", "build_muscular", "build_heavy"):
        ch.reset_keys()
        if key:
            ch.set_key(key, 1.0)
        paths += shoot_views(ch, out, f"b_{key}", dist=4.2)
    RD.sheet(paths, os.path.join(out, f"bodies_{ch.body}.png"), 4, scale=2)


def sheet_faces(ch: Character, out: str) -> None:
    ch.clear_items()
    ch.add(CREATOR["hair_styles"][2]["asset"])
    ch.hide_regions({"scalp"})
    ch.tint(CREATOR["skin_tones"][2]["hex"], CREATOR["hair_colours"][2]["hex"], {})
    keys = [None] + [k for s in CREATOR["sliders"] if s["group"] == "face" for k in s["shape_keys"]]
    keys += ["blink_l", "smile", "frown", "mouth_open", "brows_up", "brows_down"]
    paths = []
    eye = height(ch) - 0.12 * (height(ch) / 1.78)
    for k in keys:
        ch.reset_keys()
        if k:
            ch.set_key(k, 1.0)
        RD.aim((0, 0, eye - 0.03), 20, 2, 0.75, lens=85)
        paths.append(RD.shoot(os.path.join(out, f"_f_{k}.png")))
    RD.sheet(paths, os.path.join(out, f"faces_{ch.body}.png"), 6, scale=2)


def sheet_outfits(ch: Character, out: str) -> None:
    for i, (name, items, hair, house, skin, hc) in enumerate(OUTFITS):
        dress(ch, resolve(items, house), hair, house, skin, hc)
        ch.reset_keys()
        paths = shoot_views(ch, out, f"o_{name}", dist=3.8)
        RD.sheet(paths, os.path.join(out, f"outfit_{i + 1}_{name}_{ch.body}.png"), 4, scale=2)
    # The four house robes side by side.
    paths = []
    for house in HOUSES:
        name, items, hair, _, skin, hc = OUTFITS[0]
        dress(ch, resolve(items, house), hair, house, skin, hc)
        paths += shoot_views(ch, out, f"h_{house}", views=("three",), dist=3.6)
    RD.sheet(paths, os.path.join(out, f"houses_{ch.body}.png"), 4, scale=2)


def sheet_fashion(ch: Character, out: str, picks: set[str] | None = None) -> None:
    """Turntables of the crafted-fashion outfits (8 angles each), then a
    close-up row of the head pieces and shoes for each."""
    for i, (name, items, hair, house, skin, hc) in enumerate(FASHION):
        if picks and name not in picks:
            continue
        missing = [a for a in items if a not in ch.manifest["assets"] or ch.body not in ch.manifest["assets"][a]["files"]]
        if missing:
            print(f"[render] fashion {name}: not exported for {ch.body}: {missing}; skipped", flush=True)
            continue
        dress(ch, items, hair, house, skin, hc)
        ch.reset_keys()
        z = height(ch) * 0.52
        paths = []
        for az in TURNTABLE:
            RD.aim((0, 0, z), az, 6, 3.7, lens=60)
            paths.append(RD.shoot(os.path.join(out, f"_fa_{name}_{az}.png")))
        eye = height(ch) - 0.1
        for az in (30, 150, 270):
            RD.aim((0, 0, eye), az, 8, 1.1, lens=60)
            paths.append(RD.shoot(os.path.join(out, f"_fa_{name}_head_{az}.png")))
        for az in (20, 200):
            RD.aim((0, 0, 0.12), az, 18, 1.0, lens=60)
            paths.append(RD.shoot(os.path.join(out, f"_fa_{name}_feet_{az}.png")))
        RD.sheet(paths[:8], os.path.join(out, f"fashion_{i + 1:02d}_{name}_{ch.body}.png"), 8, scale=2)
        RD.sheet(paths[8:], os.path.join(out, f"fashion_{i + 1:02d}_{name}_detail_{ch.body}.png"), 5, scale=2)


def sheet_fashion_builds(ch: Character, out: str) -> None:
    """The fashion outfits at the slim and heavy build extremes, in motion
    (walk, run and sit frames), to catch clipping."""
    paths = []
    for name, items, hair, house, skin, hc in FASHION:
        if any(a not in ch.manifest["assets"] for a in items):
            continue
        dress(ch, items, hair, house, skin, hc)
        for build in ("build_heavy", "build_slim"):
            ch.reset_keys()
            ch.set_key(build, 1.0)
            for pose in ("walk", "run", "sit"):
                test_pose(ch, pose)
                paths += shoot_views(ch, out, f"fb_{name}_{build}_{pose}", views=("three",), dist=4.0)
        ch.play(None)
    if paths:
        RD.sheet(paths, os.path.join(out, f"fashion_builds_{ch.body}.png"), 6, scale=2)


def sheet_poses(ch: Character, out: str) -> None:
    for oi in (0, 1, 6):
        name, items, hair, house, skin, hc = OUTFITS[oi]
        dress(ch, resolve(items, house), hair, house, skin, hc)
        paths = []
        for build in ("build_heavy", "build_slim"):
            ch.reset_keys()
            ch.set_key(build, 1.0)
            for pose in ("rest", "walk", "run", "sit", "cast", "arms_up", "crouch"):
                test_pose(ch, pose)
                paths += shoot_views(ch, out, f"p_{build}_{pose}", views=("three",), dist=4.0)
        ch.play(None)
        ch.arm.location = (0, 0, 0)
        RD.sheet(paths, os.path.join(out, f"poses_{name}_{ch.body}.png"), 7, scale=2)


def sheet_anims(ch: Character, out: str) -> None:
    meta = ch.manifest["animations"]
    name, items, hair, house, skin, hc = OUTFITS[0]
    dress(ch, resolve(items, house), hair, house, skin, hc)
    ch.reset_keys()
    for clip, m in meta.items():
        if clip not in BASE_CLIPS:
            continue  # the spell clips have their own sheets (sheet_spells)
        n = 8
        frames = [round(m["frames"] * i / n) for i in range(n)]
        paths = []
        for v in ("side", "front"):
            for f in frames:
                ch.play(clip, f)
                paths += shoot_views(ch, out, f"a_{clip}_{v}_{f}", views=(v,), dist=4.0)
        RD.sheet(paths, os.path.join(out, f"anim_{clip}_{ch.body}.png"), n, scale=2)
    ch.play(None)


# The clips that existed before the spell clips (anim_spells.py).
BASE_CLIPS = {"idle", "walk", "run", "wave", "bow", "sit_down", "sit"}


def attach_wand(ch: Character) -> list:
    """Puts the exported wand (vfx/wand.glb, else a plain rod) in the right
    hand, where the clips solve it: placed in the rest pose from
    anim_spells.Grip, then parented to the hand bone."""
    from charlib import anim_spells as SP
    from charlib import proportions as P

    ch.play(None)
    bpy.context.view_layer.update()
    g = SP.Grip(P.get(ch.body))
    path = os.path.join(ch.assets, "vfx", "wand.glb")
    before = set(bpy.data.objects)
    if os.path.exists(path):
        bpy.ops.import_scene.gltf(filepath=path)
        objs = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    else:
        bpy.ops.mesh.primitive_cylinder_add(radius=0.006, depth=SP.WAND_TIP + SP.WAND_BUTT, vertices=8,
                                            location=(0, 0, (SP.WAND_TIP - SP.WAND_BUTT) / 2))
        bpy.ops.object.transform_apply(location=True)
        objs = [bpy.context.active_object]
    z = Vector(g.wand).normalized()
    x = Vector(g.palm)
    x = (x - z * x.dot(z)).normalized()
    y = z.cross(x)
    from mathutils import Matrix

    world = Matrix((x, y, z)).transposed().to_4x4()
    world.translation = Vector(g.grip)
    for o in objs:
        o.matrix_world = world
        bpy.context.view_layer.update()
        o.parent = ch.arm
        o.parent_type = "BONE"
        o.parent_bone = "DEF-hand.R"
        bpy.context.view_layer.update()
        o.matrix_world = world
    return objs


def sheet_spells(ch: Character, out: str) -> None:
    """The clips (gestures, casting, dueling, class), dressed
    in the uniform with the wand in hand: eight frames from the front and
    the side each, plus a three-quarter row."""
    meta = ch.manifest["animations"]
    name, items, hair, house, skin, hc = OUTFITS[0]
    dress(ch, resolve(items, house), hair, house, skin, hc)
    ch.reset_keys()
    wand = attach_wand(ch)
    for clip, m in meta.items():
        if clip in BASE_CLIPS:
            continue
        n = 8
        last = m["frames"] if m["loop"] else m["frames"] - 1
        frames = [round(last * i / (n - 1)) for i in range(n)] if not m["loop"] else [round(m["frames"] * i / n) for i in range(n)]
        paths = []
        for v in ("front", "side", "three"):
            for f in frames:
                ch.play(clip, f)
                paths += shoot_views(ch, out, f"s_{clip}_{v}_{f}", views=(v,), dist=4.2)
        RD.sheet(paths, os.path.join(out, f"spell_{clip}_{ch.body}.png"), n, scale=2)
    for o in wand:
        bpy.data.objects.remove(o)
    ch.play(None)


def sheet_hair(ch: Character, out: str) -> None:
    for cap in (False, True):
        paths = []
        for i, style in enumerate(CREATOR["hair_styles"]):
            ch.clear_items()
            ch.add(style["asset"], cap=cap)
            if cap:
                ch.add("gear/hat/pointed_a")
            ch.hide_regions(set(ch.manifest["assets"][style["asset"]]["hides"]))
            hc = CREATOR["hair_colours"][(i * 5) % 12]["hex"]
            ch.tint(CREATOR["skin_tones"][(i * 7) % len(CREATOR["skin_tones"])]["hex"], hc, dyes_for(["gear/hat/pointed_a"], "roarwing"))
            long = any(k in style["asset"] for k in ("long", "braids", "curls", "plait", "pony", "waves"))
            eye = height(ch) - 0.12
            for v in ("three", "back"):
                az, el = VIEWS[v]
                RD.aim((0, 0, eye - (0.18 if long else 0.03)), az, el, 1.9 if long else 1.15, lens=60)
                paths.append(RD.shoot(os.path.join(out, f"_hair_{i}_{v}.png")))
        RD.sheet(paths, os.path.join(out, f"hair{'_cap' if cap else ''}_{ch.body}.png"), 8, scale=2)


def main() -> None:
    a = args()
    assets = os.path.abspath(a.assets)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(assets, "characters", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    only = set(a.only.split(","))
    for body in a.bodies.split(","):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        A.build_image()
        RD.studio((480, 720))
        ch = Character(assets, manifest, body)
        picks = set(filter(None, a.fashion.split(",")))
        steps = [("bodies", sheet_bodies), ("faces", sheet_faces), ("outfits", sheet_outfits), ("poses", sheet_poses),
                 ("anims", sheet_anims), ("hair", sheet_hair), ("fashion", lambda c, o: sheet_fashion(c, o, picks)),
                 ("fashion_builds", sheet_fashion_builds), ("spells", sheet_spells)]
        for name, fn in steps:
            if name in only:
                print(f"[render] {body} {name}", flush=True)
                fn(ch, out)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
