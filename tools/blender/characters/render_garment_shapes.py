"""Review renders for the garment-shape trial: old builders vs shape system.

    blender -b --factory-startup --python tools/blender/characters/render_garment_shapes.py -- \\
        --old <assets root built with --gear-source builders> \\
        --new <assets root built with --gear-source shapes> \\
        --out art/renders/garment-shapes [--only compare,closeups,poses,new] [--bodies body_a,body_b]

Sheets (one character per frame, labelled "builder" or "shapes" over the
head; each old-builder frame is followed by the shape-system frame of the
same body, dyes, hair and pose):

* `compare_<outfit>.png`: rows body_a (male) then body_b (female); columns
  front, three-quarter, side, back (old, new each);
* `closeup_<outfit>.png`: chest and collar, back neck, hem, feet (old, new each);
* `poses_<outfit>_<body>.png`: shape system only, the spec's test poses
  (rest, walk, run, sit, cast, arms_up, crouch) at build_heavy (top row) and
  build_slim (bottom row);
* `new_designs.png`, `new_skirts_designs.png`, `new_midi_designs.png`: designs
  that exist only in the shape system (not in the trial's catalog), both
  bodies in the same design.

Everything is lit with the shared cel-shaded setup (charlib/toon.py: EEVEE,
Shader to RGB and a banded ramp, rim light, inverted-hull outline).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

import render_characters as RC  # noqa: E402
from charlib import atlas as A  # noqa: E402
from charlib import content  # noqa: E402
from charlib import render as RD  # noqa: E402
from charlib import toon  # noqa: E402

PALETTE = RC.PALETTE
HOUSES = RC.HOUSES
CREATOR = RC.CREATOR
DESIGNS = content.load_json("data/items/garment_designs.json")["designs"]
ITEMS = {i["asset"]: i for i in content.clothing_items()}

OUTFITS = [
    ("shirt_tie", ["gear/body/shirt_oxford_tie_a", "gear/legs/trousers_school_a", "gear/shoes/oxford_school_a"], 2, "serpentyne", 5, 3),
    ("jumper", ["gear/body/jumper_vneck_school_a", "gear/legs/trousers_school_a", "gear/shoes/oxford_school_a"], 11, "burrowsby", 1, 7),
    ("weekend", ["gear/body/sweater_cable_knit_a", "gear/legs/trousers_corduroy_a", "gear/shoes/loafers_buckle_a",
                 "gear/hat/bobble_knit_a"], 8, "roarwing", 10, 0),
    ("summer", ["gear/body/shirt_linen_rolled_a", "gear/legs/trousers_corduroy_a", "gear/shoes/loafers_buckle_a"], 1, "skytalon", 2, 5),
    ("robe_roarwing", ["gear/body/robe_school_house_a", "gear/legs/trousers_school_a", "gear/shoes/oxford_school_a"], 2,
     "roarwing", 3, 1),
    ("robe_burrowsby", ["gear/body/robe_school_house_b", "gear/legs/trousers_school_a", "gear/shoes/oxford_school_a"], 4,
     "burrowsby", 8, 0),
    ("robe_skytalon", ["gear/body/robe_school_house_c", "gear/legs/trousers_corduroy_a", "gear/shoes/loafers_buckle_a"], 11,
     "skytalon", 1, 7),
    ("robe_serpentyne", ["gear/body/robe_school_house_d", "gear/legs/trousers_school_a", "gear/shoes/oxford_school_a"], 6,
     "serpentyne", 5, 3),
    ("uniform_skirt", ["gear/body/robe_school_house_c", "gear/legs/skirt_pleated_a", "gear/shoes/loafers_buckle_a",
                       "gear/hat/pointed_a"], 4, "skytalon", 8, 0),
    ("shirt_skirt_boots", ["gear/body/shirt_oxford_tie_a", "gear/legs/skirt_pleated_a", "gear/shoes/boots_lace_a"], 6,
     "serpentyne", 5, 3),
    ("travel", ["gear/body/cloak_travelling_a", "gear/legs/trousers_school_a", "gear/shoes/boots_lace_a", "gear/hat/pointed_a"], 7,
     "serpentyne", 6, 10),
]
# Designs that exist only in the shape system (both bodies wear the same design).
NEW_OUTFITS = [
    ("new", ["gear/body/jumper_rugby_stripe_a", "gear/legs/trousers_pinstripe_a", "gear/shoes/oxford_school_a",
             "gear/hat/beanie_cable_a"], 6, "skytalon", 4, 2),
    ("new_skirts", ["gear/body/sweater_cable_knit_a", "gear/legs/kilt_tartan_a", "gear/shoes/boots_lace_a"], 8, "roarwing", 2, 4),
    ("new_midi", ["gear/body/shirt_oxford_tie_a", "gear/legs/skirt_midi_wrap_a", "gear/shoes/loafers_buckle_a",
                  "gear/hat/beanie_cable_a"], 1, "burrowsby", 9, 6),
]
LABELS = ["builder", "shapes"]  # captions of the old and new frames (--labels)
OFFSTAGE = (60.0, 0.0, 0.0)  # where the character not in the current frame waits
FRAME = (560, 760)  # one character per frame (portrait)
VIEWS = (("front", 0, 4), ("three", 35, 8), ("side", 90, 4), ("back", 180, 6))


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--out", default="art/renders/garment-shapes")
    p.add_argument("--only", default="compare,closeups,poses,new")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--outfits", default="")
    p.add_argument("--labels", default="builder,shapes", help="captions of the --old and --new frames")
    return p.parse_args(argv)


def dyes_for(items: list[str], house: str) -> dict:
    hc = HOUSES[house]
    out = {}
    for asset in items:
        item = ITEMS.get(asset)
        d = dict(item["dyes"]) if item else dict(DESIGNS[asset].get("preview_dyes", {"primary": 3, "secondary": 9, "trim": 9}))
        for ch, src in ((item or {}).get("house_dyes") or {}).items():
            d[ch] = hc["primary"] if src == "house_primary" else hc["secondary"]
        out[asset] = tuple(PALETTE[d[c]] for c in ("primary", "secondary", "trim"))
    return out


def dress(ch, items, hair_idx, house, skin, hc) -> None:
    ch.clear_items()
    hat = any(a.startswith("gear/hat/") for a in items)
    hides = set()
    for a in items:
        if a in ch.manifest["assets"]:
            ch.add(a)
            hides |= set(ch.manifest["assets"][a]["hides"])
    styles = [h["asset"] for h in CREATOR["hair_styles"] if h["asset"] in ch.manifest["assets"]]
    if styles:  # the creator list may change while the trial runs: wrap the index
        hair = styles[hair_idx % len(styles)]
        ch.add(hair, cap=hat and "cap_files" in ch.manifest["assets"][hair])
        hides |= set(ch.manifest["assets"][hair]["hides"])
    ch.hide_regions(hides)
    toon_tint(ch, CREATOR["skin_tones"][skin]["hex"], CREATOR["hair_colours"][hc]["hex"], dyes_for(items, house))


def toon_tint(ch, skin: str, hair: str, dyes: dict) -> None:
    """The shared cel-shaded review look (charlib/toon.py): banded light, rim,
    inverted-hull outline; same tint formula as the client."""
    body_mat = toon.material(f"toon_skin_{ch.body}_{skin}_{hair}", skin, hair, shadow_tint=toon.SKIN_SHADOW)
    for o in ch.regions:
        toon.assign(o, body_mat)
    for asset, objs in ch.items.items():
        if asset.startswith("hair/"):
            mat = toon.material(f"toon_hair_{hair}", hair, shadow_tint=toon.HAIR_SHADOW)
        else:
            d = dyes.get(asset, ("#808080", "#808080", "#808080"))
            mat = toon.material(f"toon_dye_{'_'.join(d)}", *d)
        for o in objs:
            toon.assign(o, mat)


def place(ch, x: float) -> None:
    ch.arm.location = (x, 0.0, 0.0)


def label(text: str):
    """A small caption over the head of the character in frame, always facing the camera."""
    ob = bpy.data.objects.get("caption")
    if ob is None:
        cu = bpy.data.curves.new("caption", "FONT")
        cu.align_x = "CENTER"
        cu.size = 0.085
        ob = bpy.data.objects.new("caption", cu)
        bpy.context.scene.collection.objects.link(ob)
        m = bpy.data.materials.new("caption")
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (0.05, 0.06, 0.1, 1.0)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(em.outputs[0], out.inputs["Surface"])
        cu.materials.append(m)
        c = ob.constraints.new("TRACK_TO")
        c.target = bpy.context.scene.camera
        c.track_axis = "TRACK_Z"
        c.up_axis = "UP_Y"
    ob.data.body = text
    ob.hide_render = not text
    return ob


def pair(old_root, new_root, manifest_old, manifest_new, body):
    old = RC.Character(old_root, manifest_old, body) if manifest_old else None
    new = RC.Character(new_root, manifest_new, body)
    return old, new


def shoot(out_dir, tag, target, az, el, dist, lens=50):
    RD.aim(target, az, el, dist, lens=lens)
    return RD.shoot(os.path.join(out_dir, f"_{tag}.png"))


def solo(chars, k: int) -> None:
    """Only character k in frame (at the origin); the others wait offstage."""
    for i, ch in enumerate(chars):
        ch.arm.location = (0.0, 0.0, 0.0) if i == k else OFFSTAGE


def sheet_compare(chars, out, name, items, hair, house, skin, hc, closeups=False, paths=None):
    """One row (one body) of a comparison sheet; returns the frame paths.
    Each view is shot twice: the old builder's item, then the shape system's."""
    paths = [] if paths is None else paths
    for body, (old, new) in chars.items():
        for ch in (old, new):
            dress(ch, items, hair, house, skin, hc)
            ch.reset_keys()
            ch.play(None)
        h = RC.height(new)
        if closeups:
            views = [("chest", (0, 0, h * 0.83), 28, 6, 1.75), ("back_neck", (0, 0, h * 0.84), 160, 22, 1.3),
                     ("hem", (0, 0, h * 0.45), 30, 4, 1.6), ("feet", (0, 0, h * 0.07), 30, 14, 1.25)]
        else:
            views = [(v, (0, 0, h * 0.52), az, el, 3.25) for v, az, el in VIEWS]
        cap = label("")
        cap.location = (0.0, 0.0, h + 0.13)
        for v, tgt, az, el, dist in views:
            for k, tag in enumerate(LABELS):
                solo((old, new), k)
                label(tag if not closeups or v == "chest" else "")
                paths.append(shoot(out, f"{name}_{body}_{v}_{tag}", tgt, az, el, dist, lens=50))
    return paths


def sheet_poses(new, out, name, items, hair, house, skin, hc):
    place(new, 0.0)
    dress(new, items, hair, house, skin, hc)
    paths = []
    for build in ("build_heavy", "build_slim"):
        new.reset_keys()
        new.set_key(build, 1.0)
        for pose in ("rest", "walk", "run", "sit", "cast", "arms_up", "crouch"):
            RC.test_pose(new, pose)
            paths += RC.shoot_views(new, out, f"p_{name}_{build}_{pose}", views=("three",), dist=4.0)
    new.play(None)
    new.arm.location = (0, 0, 0)
    new.reset_keys()
    RD.sheet(paths, os.path.join(out, f"poses_{name}_{new.body}.png"), 7, scale=2)


def main():
    a = args()
    LABELS[:] = a.labels.split(",")[:2]
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    roots = {k: os.path.abspath(v) for k, v in (("old", a.old), ("new", a.new))}
    mf = {}
    for k, r in roots.items():
        with open(os.path.join(r, "characters", "manifest.json"), encoding="utf-8") as f:
            mf[k] = json.load(f)
    only = set(a.only.split(","))
    outfits = [o for o in OUTFITS if not a.outfits or o[0] in a.outfits.split(",")]
    bodies = a.bodies.split(",")
    if only & {"compare", "closeups"}:
        frames = {}
        for b in bodies:
            # One scene per body, so the other body's characters are not in frame.
            bpy.ops.wm.read_factory_settings(use_empty=True)
            A.build_image()
            toon.studio(FRAME)
            chars = {b: pair(roots["old"], roots["new"], mf["old"], mf["new"], b)}
            for name, items, hair, house, skin, hc in outfits:
                for kind in ("compare", "closeups"):
                    if kind in only:
                        print(f"[render] {kind} {name} {b}", flush=True)
                        sheet_compare(chars, out, name, items, hair, house, skin, hc, closeups=kind == "closeups",
                                      paths=frames.setdefault((kind, name), []))
        for (kind, name), paths in frames.items():
            RD.sheet(paths, os.path.join(out, f"{'closeup' if kind == 'closeups' else 'compare'}_{name}.png"), 8, scale=1)
    if "new" in only:
        # Both bodies in the same design: male, then female, per view.
        for name, items, hair, house, skin, hc in NEW_OUTFITS:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            A.build_image()
            toon.studio(FRAME)
            chars = [RC.Character(roots["new"], mf["new"], b) for b in bodies]
            for ch in chars:
                dress(ch, items, hair, house, skin, hc)
                ch.reset_keys()
                ch.play(None)
            h = max(RC.height(ch) for ch in chars)
            cap = label("")
            cap.location = (0.0, 0.0, h + 0.13)
            paths = []
            for v, az, el in VIEWS:
                for k, ch in enumerate(chars):
                    solo(chars, k)
                    label({"body_a": "male", "body_b": "female"}.get(ch.body, ch.body) if v == "front" else "")
                    paths.append(shoot(out, f"{name}_{v}_{ch.body}", (0, 0, h * 0.52), az, el, 3.25))
            RD.sheet(paths, os.path.join(out, f"{name}_designs.png" if name != "new" else "new_designs.png"), 2 * len(VIEWS), scale=1)
    if "poses" in only:
        for b in bodies:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            A.build_image()
            toon.studio((480, 720))
            new = RC.Character(roots["new"], mf["new"], b)
            for name, items, hair, house, skin, hc in outfits + NEW_OUTFITS:
                print(f"[render] poses {name} {b}", flush=True)
                sheet_poses(new, out, name, items, hair, house, skin, hc)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
