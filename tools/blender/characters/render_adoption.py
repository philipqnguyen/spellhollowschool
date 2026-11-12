"""Review stills of the adopted characters (docs/art/character-adoption.md).

    blender -b --factory-startup -t 3 --python tools/blender/characters/render_adoption.py -- \\
        --assets client/public/assets --out .out/renders [--only stills,designs,poses,heights]

Toon-lit with the adoption shading (charlib/toon.py `shaded_material`: the
baked `_SHADE` layer, tinted shadow bands, sky lift, rim, cast shadows on the
body and the floor, clean faces, outline weight from `_SHADE.w`), LOD0, from
the client assets (the body files, every garment, and whatever hair files the
manifest lists; with no hair files the review uses an old style).

Sheets (PNG, into --out):

* `stills_<body>.png`: the school uniform and two outfits, front /
  three-quarter / back, at the body's minimum, default and maximum height
  (uniform root scale, one fixed camera so heights compare);
* `designs_<body>.png`: the 27 catalog items that became designs on the
  garment shapes, each worn over a base outfit, three-quarter view;
* `poses_<body>.png`: walk, run, wave, bow, the seated clips (sit, desk_rest,
  desk_practice: hands in the lap, wand practice), stun_fall's last frame,
  knockback, at the maximum height, on a 0.46 m seat where seated;
* `floor_sit_<body>.png` (`--only floor_sit`): the floor_sit clip
  on a rug and on a 0.3 m hearth step at the minimum and maximum heights;
* `broom_<body>.png` (`--only broom`): broom_ride and broom_hover
  in the school uniform at the minimum and maximum heights, side,
  three-quarter and front, the broom (`characters/broom.glb`) at the hips
  bone as the client draws it, the root 0.3 m over the floor (the flight
  rules' lowest); each still is also kept as `broom_<body>_<height>_<clip>_<view>.png`.
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

import render_characters as RC  # noqa: E402
import render_garment_shapes as RGS  # noqa: E402
import render_head_trial as RHT  # noqa: E402
from charlib import atlas as A  # noqa: E402
from charlib import montage  # noqa: E402
from charlib import render as RD  # noqa: E402
from charlib import toon  # noqa: E402

HOUSE = "roarwing"
SKIN = {"body_a": 3, "body_b": 1}
HAIR_COL = {"body_a": 1, "body_b": 6}
EYE_COL = {"body_a": 5, "body_b": 4}
HAIR = {"body_a": ["hair/short_tousled", "hair/side_part"], "body_b": ["hair/bob_locks", "hair/ponytail_high"]}
UNIFORM = {"body_a": ["gear/body/robe_school_house_a", "gear/body/shirt_oxford_tie_a", "gear/legs/trousers_school_a",
                      "gear/shoes/oxford_school_a", "gear/hat/pointed_a"],
           "body_b": ["gear/body/robe_school_house_a", "gear/body/shirt_oxford_tie_a", "gear/legs/skirt_pleated_a",
                      "gear/shoes/oxford_school_a", "gear/hat/pointed_a"]}
OUTFIT_1 = {"body_a": ["gear/body/jacket_tailored_velvet_a", "gear/legs/trousers_tapered_wool_a", "gear/shoes/brogues_wingtip_a"],
            "body_b": ["gear/body/cardigan_chunky_a", "gear/legs/skirt_aline_wool_a", "gear/shoes/mary_janes_a",
                       "gear/hat/ribbon_bow_a"]}
OUTFIT_2 = {"body_a": ["gear/body/greatcoat_double_a", "gear/legs/trousers_wide_leg_a", "gear/shoes/boots_riding_tall_a",
                       "gear/hat/witch_wide_brim_a"],
            "body_b": ["gear/body/robe_dress_embroidered_a", "gear/legs/trousers_wide_leg_a", "gear/shoes/boots_ankle_buckle_a",
                       "gear/hat/cloche_felt_a"]}
NEW_ITEMS = [
    "gear/hat/witch_wide_brim_a", "gear/hat/cloche_felt_a", "gear/hat/beret_velvet_a", "gear/hat/cap_slouch_knit_a",
    "gear/hat/earflap_knit_a", "gear/hat/hood_fur_lined_a", "gear/hat/hood_cowl_a", "gear/hat/ribbon_bow_a",
    "gear/hat/comb_feathered_a", "gear/body/cloak_hooded_wool_a", "gear/body/jacket_tailored_velvet_a",
    "gear/body/greatcoat_double_a", "gear/body/cardigan_chunky_a", "gear/body/turtleneck_ribbed_a",
    "gear/body/jumper_fair_isle_a", "gear/body/robe_dress_embroidered_a", "gear/body/robe_formal_velvet_a",
    "gear/body/blouse_ruffled_silk_a", "gear/body/waistcoat_brocade_a", "gear/legs/skirt_aline_wool_a",
    "gear/legs/trousers_wide_leg_a", "gear/legs/trousers_tapered_wool_a", "gear/shoes/boots_riding_tall_a",
    "gear/shoes/boots_ankle_buckle_a", "gear/shoes/boots_dragonhide_a", "gear/shoes/mary_janes_a", "gear/shoes/brogues_wingtip_a"]
BASE = {"body": "gear/body/shirt_linen_rolled_a", "legs": "gear/legs/trousers_school_a", "shoes": "gear/shoes/oxford_school_a"}
POSES = [("walk", "walk", 5), ("run", "run", 4), ("wave", "wave", 30), ("bow", "bow", 28), ("sit", "sit", 30),
         ("desk_rest", "desk_rest", 30), ("desk_practice", "desk_practice", 20), ("desk_practice_b", "desk_practice", 40),
         ("knockback", "knockback", 8), ("stun_fall", "stun_fall", 40)]
SEATED = {"sit", "desk_rest", "desk_practice"}
SEAT_M = 0.46
FULL = (420, 600)
REF_H = 1.85


def args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--assets", default="client/public/assets")
    p.add_argument("--out", default=".out/renders")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--only", default="stills,designs,poses")
    return p.parse_args(argv)


class Char(RC.Character):
    """A body with its items, cel-shaded with the adoption look (LOD0)."""

    def __init__(self, assets: str, manifest: dict, body: str):
        before = set(bpy.data.actions)
        super().__init__(assets, manifest, body)
        self.actions = [a for a in bpy.data.actions if a not in before]
        self.brows = None

    def _import(self, rel: str):
        arm, meshes = super()._import(rel)
        keep = []
        for o in meshes:
            if o.name.startswith("Icosphere"):
                o.hide_render = True
                continue
            o.hide_render = int(o.get("lod", 0)) != 0 or any(o.name.endswith(f"_lod{k}") for k in (1, 2, 3))
            toon.keep_file_normals(o)
            keep.append(o)
        return arm, keep

    def clear_items(self) -> None:
        super().clear_items()
        self.hide_regions(set())

    def hide_regions(self, regions: set[str]) -> None:
        for o in self.regions:
            low = int(o.get("lod", 0)) != 0 or any(o.name.endswith(f"_lod{k}") for k in (1, 2, 3))
            o.hide_render = low or o.get("region") in regions

    def action(self, clip: str):
        for a in self.actions:
            if a.name == clip or a.name.startswith(clip + "_") or a.name.startswith(clip + "."):
                return a
        raise KeyError(f"{self.body}: no clip {clip}")

    def play(self, clip: str | None, frame: float = 0.0) -> None:
        ad = self.arm.animation_data or self.arm.animation_data_create()
        for t in list(ad.nla_tracks):
            t.mute = True
        if clip is None:
            ad.action = None
            for pb in self.arm.pose.bones:
                pb.rotation_quaternion = (1, 0, 0, 0)
                pb.location = (0, 0, 0)
                pb.scale = (1, 1, 1)
        else:
            act = self.action(clip)
            ad.action = act
            if hasattr(ad, "action_slot") and act.slots:
                ad.action_slot = act.slots[0]
        bpy.context.scene.frame_set(int(round(frame)))

    def tint(self, skin: str, hair: str, eye: str, dyes: dict) -> None:
        body = toon.shaded_material(f"skin_{self.body}_{skin}_{hair}_{eye}", skin, hair, eye, kind="skin")
        face = toon.shaded_material(f"face_{self.body}_{skin}_{hair}_{eye}", skin, hair, eye, kind="face")
        for o in self.regions:
            if o.get("region") == "head":
                toon.assign_shaded(o, face, "face")
            else:
                toon.assign_shaded(o, body, "skin")
        for asset, objs in self.items.items():
            if asset.startswith("hair/"):
                mat, kind = toon.shaded_material(f"hair_{asset}_{hair}", hair, kind="hair"), "hair"
            else:
                d = dyes.get(asset, ("#808080", "#808080", "#808080"))
                mat, kind = toon.shaded_material(f"dye_{asset}_{'_'.join(d)}", *d, kind="cloth"), "cloth"
            for o in objs:
                toon.assign_shaded(o, mat, kind)


def hair_for(ch: Char) -> str | None:
    for h in HAIR[ch.body]:
        e = ch.manifest["assets"].get(h)
        if e and ch.body in e.get("files", {}):
            return h
    return None


def dress(ch: Char, items: list[str]) -> None:
    ch.clear_items()
    hat = any(a.startswith("gear/hat/") for a in items)
    hides: set[str] = set()
    for a in items:
        ch.add(a)
        hides |= set(ch.manifest["assets"][a].get("hides", []))
    hair = hair_for(ch)
    if hair:
        e = ch.manifest["assets"][hair]
        ch.add(hair, cap=hat and ch.body in e.get("cap_files", {}))
        hides |= set(e.get("hides", []))
    ch.hide_regions(hides)
    cr = RC.CREATOR
    eyes = cr.get("eye_colours") or [{"hex": "#6B4226"}] * 8
    ch.tint(cr["skin_tones"][SKIN[ch.body]]["hex"], cr["hair_colours"][HAIR_COL[ch.body]]["hex"],
            eyes[EYE_COL[ch.body] % len(eyes)]["hex"], RGS.dyes_for(items, HOUSE))
    ch.reset_keys()
    if ch.brows is not None:
        bpy.data.objects.remove(ch.brows)
    ch.brows = RHT.brow_overlay(ch)
    if ch.brows is not None:
        ch.brows.parent = ch.arm


def heights(ch: Char) -> dict:
    h = ch.manifest["height"]["bodies"][ch.body]
    lo, hi = h["height_m"]
    return {"min": lo, "default": h["default_height_m"], "max": hi}


def set_height(ch: Char, metres: float) -> float:
    s = metres / ch.manifest["height"]["bodies"][ch.body]["default_height_m"]
    ch.arm.scale = (s, s, s)
    bpy.context.view_layer.update()
    return s


def seat(on: bool, z: float = SEAT_M):
    """A bench block under the hips for the seated clips (the character's
    root goes on the seat's top, as the client does)."""
    ob = bpy.data.objects.get("review_seat")
    if ob is None:
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        ob = bpy.context.active_object
        ob.name = "review_seat"
        ob.scale = (0.5, 0.4, z / 2)
        m = bpy.data.materials.new("seat")
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (0.36, 0.25, 0.17, 1.0)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(em.outputs[0], out.inputs[0])
        ob.data.materials.append(m)
    ob.location = (0.0, 0.12, z / 2)
    ob.hide_render = not on
    return ob


def label(text: str):
    cap = RGS.label(text)
    cap.location = (0.0, -0.5, 0.02)
    cap.rotation_euler = (math.radians(90), 0, 0)
    cap.data.size = 0.07
    return cap


def cam(az: float, el: float = 6.0, dist: float = 3.4, z: float = 0.5, target_h: float = REF_H) -> None:
    RD.aim((0, 0, target_h * z), az, el, dist, lens=50)


def shoot(ch: Char, path: str, brows: bool = True) -> str:
    import render_refit_compare as RRC

    return RRC.shoot(ch, path, brows)


def load(body: str, assets: str, res) -> Char:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    A.build_image()
    toon.studio_shaded(res)
    with open(os.path.join(assets, "characters", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    return Char(assets, manifest, body)


def sheet_stills(body: str, assets: str, out: str) -> str:
    ch = load(body, assets, FULL)
    paths = []
    for name, items in (("uniform", UNIFORM[body]), ("outfit 1", OUTFIT_1[body]), ("outfit 2", OUTFIT_2[body])):
        dress(ch, items)
        ch.play(None)
        for hname, metres in heights(ch).items():
            set_height(ch, metres)
            for v, az in (("front", 0), ("three", 35), ("back", 180)):
                label(f"{name}, {hname} {metres:.2f} m" if v == "front" else "")
                cam(az)
                paths.append(shoot(ch, os.path.join(out, f"_st_{body}_{name[:2]}{name[-1]}_{hname}_{v}.png"), v != "back"))
        set_height(ch, heights(ch)["default"])
    label("")
    return montage.tile(paths, os.path.join(out, f"stills_{body}.png"), 9, remove=True)


def base_for(item: str) -> list[str]:
    slot = item.split("/")[1]
    items = [item]
    for s, a in BASE.items():
        if s != slot and not (slot == "body" and s == "body"):
            items.append(a)
    if slot == "body" and any(k in item for k in ("robe", "cloak", "coat", "jacket", "cardigan")):
        pass
    return items


def sheet_designs(body: str, assets: str, out: str) -> str:
    ch = load(body, assets, (360, 520))
    paths = []
    for item in NEW_ITEMS:
        items = base_for(item)
        if item.split("/")[1] == "body" and "cardigan" not in item:
            items = [i for i in items if i != BASE["body"]]
        dress(ch, items)
        ch.play(None)
        label(item.split("/")[-1].replace("_a", ""))
        hat = item.startswith("gear/hat/")
        shoes = item.startswith("gear/shoes/")
        if hat:
            cam(30, 4, 1.6, 0.9)
        elif shoes:
            cam(30, 14, 1.8, 0.16)
        else:
            cam(30, 6, 4.0, 0.5)
        paths.append(shoot(ch, os.path.join(out, f"_ds_{body}_{item.split('/')[-1]}.png"), True))
    label("")
    return montage.tile(paths, os.path.join(out, f"designs_{body}.png"), 9, remove=True)


def sheet_poses(body: str, assets: str, out: str) -> str:
    ch = load(body, assets, (420, 520))
    dress(ch, UNIFORM[body])
    paths = []
    hs = heights(ch)
    for hname in ("max", "min"):
        s = set_height(ch, hs[hname])
        for pname, clip, frame in POSES:
            seated = clip in SEATED
            ch.arm.location = (0.0, 0.0, SEAT_M if seated else 0.0)
            seat(seated)
            ch.play(clip, frame)
            label(f"{pname} {hs[hname]:.2f} m")
            cam(55, 8, 4.0, 0.42)
            paths.append(shoot(ch, os.path.join(out, f"_ps_{body}_{hname}_{pname}.png"), True))
        ch.arm.location = (0.0, 0.0, 0.0)
        seat(False)
    label("")
    return montage.tile(paths, os.path.join(out, f"poses_{body}.png"), len(POSES), remove=True)


def floor_block(kind: str | None):
    """A floor seat under the root for floor_sit: a rug (1.5 x 1.5 m,
    1 cm) or a hearth step (0.6 m deep, 0.3 m high; the root on its top), in
    the castle palette's `rug_neutral` and `beast_stone` (castle_textures.json)."""
    with open(os.path.join(HERE, "..", "..", "..", "data", "art", "castle_textures.json"), encoding="utf-8") as f:
        pal = json.load(f)["palette"]
    for name in ("review_rug", "review_step"):
        ob = bpy.data.objects.get(name)
        if ob is not None:
            ob.hide_render = True
    if kind is None:
        return None
    name, colour, size, top = (("review_rug", pal["rug_neutral"], (1.5, 1.5), 0.01) if kind == "rug"
                               else ("review_step", pal["beast_stone"], (1.6, 0.6), 0.3))
    ob = bpy.data.objects.get(name)
    if ob is None:
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        ob = bpy.context.active_object
        ob.name = name
        ob.scale = (size[0], size[1], top)
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        em = nt.nodes.new("ShaderNodeEmission")
        h = colour.lstrip("#")
        em.inputs["Color"].default_value = (*[(int(h[i:i + 2], 16) / 255.0) ** 2.2 for i in (0, 2, 4)], 1.0)
        out_ = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(em.outputs[0], out_.inputs[0])
        ob.data.materials.append(m)
    ob.location = (0.0, 0.0, -top / 2)  # its top at the root (z 0)
    ob.hide_render = False
    return ob


def sheet_floor_sit(body: str, assets: str, out: str) -> str:
    """floor_sit on a rug and on a 0.3 m hearth step, front,
    three-quarter and side, at the body's minimum and maximum height."""
    ch = load(body, assets, (420, 420))
    dress(ch, UNIFORM[body])
    paths = []
    hs = heights(ch)
    for hname in ("max", "min"):
        set_height(ch, hs[hname])
        for where in ("rug", "step"):
            floor_block(where)
            ch.play("floor_sit", 0)
            for az in (0, 35, 90):
                label(f"floor_sit {where} {hs[hname]:.2f} m")
                cam(az, 14, 3.0, 0.25)
                paths.append(shoot(ch, os.path.join(out, f"_fs_{body}_{hname}_{where}_{az}.png"), True))
    floor_block(None)
    label("")
    return montage.tile(paths, os.path.join(out, f"floor_sit_{body}.png"), 6, remove=True)


FLY_CLEARANCE = 0.3  # data/flying.json ground_clearance: the lowest a flyer's root goes


def broom_prop(assets: str):
    """The exported broom (characters/broom.glb), cel-shaded from its
    vertex colours (two light bands, a darker outline-free shadow band)."""
    ob = bpy.data.objects.get("review_broom")
    if ob is not None:
        return ob
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(assets, "characters", "broom.glb"))
    new = [o for o in bpy.data.objects if o not in before]
    ob = next(o for o in new if o.type == "MESH")
    for o in new:
        if o is not ob:
            ob.parent = None
            bpy.data.objects.remove(o)
    ob.name = "review_broom"
    ob.matrix_world.identity()
    layer = ob.data.color_attributes.active_color.name
    m = bpy.data.materials.new("review_broom")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = layer
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    s2r = nt.nodes.new("ShaderNodeShaderToRGB")
    bw = nt.nodes.new("ShaderNodeRGBToBW")
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value = 0.17
    ramp.inputs["From Max"].default_value = 0.23
    ramp.inputs["To Min"].default_value = 0.55
    ramp.inputs["To Max"].default_value = 1.0
    mul = nt.nodes.new("ShaderNodeMix")
    mul.data_type = "RGBA"
    mul.blend_type = "MULTIPLY"
    mul.inputs["Factor"].default_value = 1.0
    em = nt.nodes.new("ShaderNodeEmission")
    out_ = nt.nodes.new("ShaderNodeOutputMaterial")
    L = nt.links
    L.new(diff.outputs[0], s2r.inputs[0])
    L.new(s2r.outputs["Color"], bw.inputs[0])
    L.new(bw.outputs[0], ramp.inputs["Value"])
    L.new(vc.outputs["Color"], mul.inputs["A"])
    L.new(ramp.outputs["Result"], mul.inputs["B"])
    L.new(mul.outputs["Result"], em.inputs["Color"])
    L.new(em.outputs[0], out_.inputs[0])
    ob.data.materials.clear()
    ob.data.materials.append(m)
    return ob


def sheet_broom(body: str, assets: str, out: str) -> str:
    """The riding and hovering clips on the broom at the minimum
    and maximum heights (side, three-quarter, front), the broom standing at
    the hips bone each frame, as the client draws it."""
    ch = load(body, assets, (460, 520))
    dress(ch, UNIFORM[body])
    broom = broom_prop(assets)
    paths = []
    hs = heights(ch)
    ch.arm.location = (0.0, 0.0, FLY_CLEARANCE)
    for hname in ("min", "max"):
        set_height(ch, hs[hname])
        for clip, frame in (("broom_ride", 0), ("broom_hover", 22)):
            ch.play(clip, frame)
            bpy.context.view_layer.update()
            broom.location = ch.arm.matrix_world @ ch.arm.pose.bones["DEF-spine"].head
            for view, az, el in (("side", 90, 6), ("three", 35, 10), ("front", 0, 4)):
                label(f"{clip} {hname} {hs[hname]:.2f} m" if view == "side" else "")
                cam(az, el, 4.4, 0.42)
                p = shoot(ch, os.path.join(out, f"broom_{body}_{hname}_{clip}_{view}.png"), view != "side")
                paths.append(p)
    ch.arm.location = (0.0, 0.0, 0.0)
    label("")
    return montage.tile(paths, os.path.join(out, f"broom_{body}.png"), 6)  # the stills stay too


def main() -> None:
    a = args()
    assets = os.path.abspath(a.assets)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    only = set(a.only.split(","))
    for body in a.bodies.split(","):
        if "stills" in only:
            print("wrote", sheet_stills(body, assets, out))
        if "designs" in only:
            print("wrote", sheet_designs(body, assets, out))
        if "poses" in only:
            print("wrote", sheet_poses(body, assets, out))
        if "floor_sit" in only:
            print("wrote", sheet_floor_sit(body, assets, out))
        if "broom" in only:
            print("wrote", sheet_broom(body, assets, out))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
