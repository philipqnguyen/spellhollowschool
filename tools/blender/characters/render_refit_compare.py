"""Comparison sheets for the body trial: current body + old builders against
the MakeHuman body + refitted garment shapes, in the same outfits.

    blender -b --factory-startup --python tools/blender/characters/render_refit_compare.py -- \\
        --old <assets root: current bodies, --gear-source builders> \\
        --new <assets root: MakeHuman bodies, --gear-source shapes> \\
        --out art/renders/body-trial/compare [--bodies body_a,body_b] [--outfits school,knitwear,fashion]

Per body and outfit, `compare_<body>_<outfit>.png` has two rows, the old
version on top and the new one below, with the same dyes, hair style and
colour, skin tone, camera and lights. Columns:

* front, three-quarter and back from the in-game camera position (6.5 m
  away, 21 degrees down; client/src/game/camera.ts) with the view narrowed
  to the character (the same perspective as the game, about 1.6x the pixels
  a 1080p screen gives a character at that distance);
* four walk-cycle frames from the same camera, three-quarter view;
* the face and head close up, front and three-quarter.

`contact_<body>.png` stacks the three outfits (six rows) on one sheet.

Every frame uses the shared cel-shaded setup (charlib/toon.py). Brows are
drawn again over the fringe (as render_head_trial.py does) but stay hidden
under hats. Every outfit is worn by both bodies: no garment is locked to one.
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
from mathutils import Vector  # noqa: E402

import render_body_trial as RBT  # noqa: E402
import render_characters as RC  # noqa: E402
import render_garment_shapes as RGS  # noqa: E402
import render_head_trial as RHT  # noqa: E402
from charlib import atlas as A  # noqa: E402
from charlib import montage  # noqa: E402
from charlib import render as RD  # noqa: E402
from charlib import toon  # noqa: E402

# (name, items; "{h}" is the house robe letter). Both bodies wear every outfit.
OUTFITS = [
    ("school", ["gear/body/robe_school_house_{h}", "gear/legs/trousers_school_a", "gear/shoes/oxford_school_a"]),
    ("knitwear", ["gear/body/sweater_cable_knit_a", "gear/legs/trousers_corduroy_a", "gear/shoes/boots_lace_a",
                  "gear/hat/bobble_knit_a"]),
    ("fashion", ["gear/body/shirt_oxford_tie_a", "gear/legs/skirt_pleated_a", "gear/shoes/boots_lace_a",
                 "gear/hat/pointed_a"]),
]
# Per body: hair style, house (robe colour and house dyes), skin tone index, hair colour index.
LOOK = {"body_a": ("hair/side_part", "roarwing", 3, 1), "body_b": ("hair/ponytail_high", "skytalon", 1, 6)}
TILE = (400, 560)
OFFSTAGE = (60.0, 0.0, 0.0)
ROWS = ("current + builders", "MakeHuman + shapes")
WALK_FRAMES = (0.0, 0.25, 0.5, 0.75)


def args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--out", default="art/renders/body-trial/compare")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--outfits", default="")
    return p.parse_args(argv)


class Char(RBT.ToonCharacter):
    """A toon character that plays its own baked clips (the two bodies'
    skeletons can differ in rest pose, so a clip is never shared)."""

    def __init__(self, assets: str, manifest: dict, body: str):
        before = set(bpy.data.actions)
        super().__init__(assets, manifest, body)
        self.actions = [a for a in bpy.data.actions if a not in before]
        self.brows = None

    def action(self, clip: str):
        for a in self.actions:
            if a.name == clip or a.name.startswith(clip + "_") or a.name.startswith(clip + "."):
                return a
        raise KeyError(f"{self.body}: no clip {clip} in {[a.name for a in self.actions]}")

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


def dress(ch: Char, body: str, items: list[str]) -> None:
    hair, house, skin, hc = LOOK[body]
    ch.clear_items()
    hat = any(a.startswith("gear/hat/") for a in items)
    hides: set[str] = set()
    for a in items:
        entry = ch.manifest["assets"].get(a)
        if entry is None or body not in entry.get("files", {}):
            raise KeyError(f"{ch.assets}: {a} has no file for {body}")
        ch.add(a)
        hides |= set(entry["hides"])
    entry = ch.manifest["assets"][hair]
    ch.add(hair, cap=hat and body in entry.get("cap_files", {}))
    hides |= set(entry["hides"])
    ch.hide_regions(hides)
    ch.tint(RC.CREATOR["skin_tones"][skin]["hex"], RC.CREATOR["hair_colours"][hc]["hex"], RGS.dyes_for(items, house))
    ch.reset_keys()
    if ch.brows is not None:
        bpy.data.objects.remove(ch.brows)
    ch.brows = RHT.brow_overlay(ch)
    if ch.brows is not None:
        ch.brows.parent = ch.arm


def solo(chars: list[Char], k: int) -> None:
    for i, ch in enumerate(chars):
        ch.arm.location = (0.0, 0.0, 0.0) if i == k else OFFSTAGE
    bpy.context.view_layer.update()


def ingame_view(azimuth: float, height: float) -> None:
    """The in-game camera position (distance, elevation, target height of
    client/src/game/camera.ts), turned to the character's middle with the
    field of view narrowed to frame it: a crop of the game's perspective."""
    cam = bpy.context.scene.camera
    g = RBT.INGAME
    a, e, d = math.radians(azimuth), math.radians(g["elevation"]), g["distance"]
    t = Vector((0.0, 0.0, g["target"]))
    pos = t + Vector((d * math.sin(a) * math.cos(e), -d * math.cos(a) * math.cos(e), d * math.sin(e)))
    mid = Vector((0.0, 0.0, height * 0.5))
    cam.location = pos
    cam.rotation_euler = (mid - pos).to_track_quat("-Z", "Y").to_euler()
    cam.data.sensor_fit = "VERTICAL"
    cam.data.angle_y = 2.0 * math.atan(height * 0.6 / (mid - pos).length)


def close_view() -> None:
    bpy.context.scene.camera.data.sensor_fit = "AUTO"


def shoot(ch: Char, path: str, brows: bool) -> str:
    """Render; then (front-ish views) the brows once more over the frame,
    with every hat as a holdout so brows never show through a hat."""
    RD.shoot(path)
    if not brows or ch.brows is None:
        return path
    sc = bpy.context.scene
    shown = [o for o in sc.objects if not o.hide_render]
    hats = [o for a, objs in ch.items.items() if a.startswith("gear/hat/") for o in objs if not o.hide_render]
    for o in shown:
        if o not in hats:
            o.hide_render = True
    for o in hats:
        o.is_holdout = True
    ch.brows.hide_render = False
    sc.render.film_transparent = True
    tmp = path[:-4] + "_brows.png"
    RD.shoot(tmp)
    sc.render.film_transparent = False
    ch.brows.hide_render = True
    for o in hats:
        o.is_holdout = False
    for o in shown:
        o.hide_render = False
    base = montage.load_rgba(path)
    top = montage.load_rgba(tmp)
    a = top[..., 3:4]
    base[..., :3] = base[..., :3] * (1 - a) + top[..., :3] * a
    montage.save_rgba(path, base)
    os.remove(tmp)
    return path


def caption(text: str, height: float):
    cap = RGS.label(text)
    cap.location = (0.0, -0.38, 0.02)  # on the floor in front of the feet (front view only)
    cap.data.size = 0.075
    return cap


def render_row(ch: Char, body: str, items: list[str], out: str, tag: str, row_label: str) -> list[str]:
    dress(ch, body, items)
    h = RC.height(ch)
    n = ch.manifest["animations"]["walk"]["frames"]
    paths = []
    ch.play(None)
    for v, az in (("front", 0), ("three", 35), ("back", 180)):
        caption(row_label if v == "front" else "", h)
        ingame_view(az, h)
        paths.append(shoot(ch, os.path.join(out, f"_{tag}_ig_{v}.png"), v != "back"))
    caption("", h)
    for f in WALK_FRAMES:
        ch.play("walk", f * n)
        ingame_view(35, h)
        paths.append(shoot(ch, os.path.join(out, f"_{tag}_walk_{f:.2f}.png"), True))
    ch.play(None)
    close_view()
    for v, az, el in (("front", 0, 3), ("three", 35, 5)):
        RHT.face_cam(ch, az, el, 0.95, 0.0)
        paths.append(shoot(ch, os.path.join(out, f"_{tag}_face_{v}.png"), True))
    return paths


def main() -> None:
    a = args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    roots = [os.path.abspath(a.old), os.path.abspath(a.new)]
    manifests = []
    for r in roots:
        with open(os.path.join(r, "characters", "manifest.json"), encoding="utf-8") as f:
            manifests.append(json.load(f))
    outfits = [o for o in OUTFITS if not a.outfits or o[0] in a.outfits.split(",")]
    for body in a.bodies.split(","):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        A.build_image()
        toon.studio(TILE)
        chars = [Char(r, m, body) for r, m in zip(roots, manifests)]
        house = LOOK[body][1]
        contact = []
        for name, items in outfits:
            items = RC.resolve(items, house)
            rows = []
            for k, ch in enumerate(chars):
                solo(chars, k)
                print(f"[compare] {body} {name} {ROWS[k]}", flush=True)
                rows += render_row(ch, body, items, out, f"{body}_{name}_{k}", ROWS[k])
            cols = len(rows) // 2
            montage.tile(rows, os.path.join(out, f"compare_{body}_{name}.png"), cols)
            contact += rows
        montage.tile(contact, os.path.join(out, f"contact_{body}.png"), len(contact) // (2 * len(outfits)), remove=True)
        print(f"[compare] {body} done", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
