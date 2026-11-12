"""Side-by-side review renders: current base body vs the MakeHuman body.

    blender -b --factory-startup --python tools/blender/characters/render_body_trial.py -- \\
        --current <assets root with the current bodies> --new client/public/assets \\
        --out art/renders/body-trial [--bodies body_a,body_b] [--only ingame,closeup,builds,walk,anims,faces,outfits,hair,lods]

Both asset roots are what the client loads (manifest + .glb files); each body
is shown with the clothing and hair built against it. The current body stands
on the left, the new one on the right. Every sheet uses the shared cel-shaded
setup (charlib/toon.py: EEVEE, Shader to RGB with a 3-band ramp, rim light,
1.8 mm inverted-hull outline), so the current body is judged under the same
toon look as the new one. Sheets per body:

* `ingame_<body>`: front, 3/4, side, back at the in-game camera (6.5 m,
  vertical FOV 0.9 rad, 21 degrees down, target 1.45 m; client/src/game/camera.ts);
* `closeup_<body>`: the same four views from 4 m;
* `builds_<body>`: neutral and the three build extremes, front/3-4/side;
* `walk_<body>`: eight walk-cycle frames, side view at the in-game distance and 3/4 from 4 m;
* `anims_<body>`: wave, bow and sit frames (deformation);
* `faces_<body>`: every face slider and expression (pairs: current, new);
* `outfits_<body>`: dressed in the review outfits (gear fitted to each body);
* `hair_<body>`: hair styles and a hat on both;
* `lods_<body>`: the new body's LOD0 and LOD1, full figure and face.
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
from charlib import atlas as A  # noqa: E402
from charlib import render as RD  # noqa: E402
from charlib import toon  # noqa: E402

GAP = 0.75  # each character's offset from the centre line (m; A-pose arms span about 1.3 m)
INGAME = {"distance": 6.5, "fov": 0.9, "elevation": math.degrees(math.pi / 2 - 1.2), "target": 1.45}
SKIN, HAIR_COL, HAIR = 3, 1, 0  # base layer: the anime short crop (never a bald head)


def args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--current", required=True)
    p.add_argument("--new", default="client/public/assets")
    p.add_argument("--out", default="art/renders/body-trial")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--only", default="ingame,closeup,builds,walk,anims,faces,outfits,hair,lods")
    return p.parse_args(argv)


class ToonCharacter(RC.Character):
    """render_characters.Character with the cel material and outline."""

    def _import(self, rel: str):
        # The glTF importer adds an "Icosphere" bone-shape mesh per file; keep
        # it out of the render and out of the character's mesh lists.
        arm, meshes = super()._import(rel)
        keep = []
        for o in meshes:
            if o.name.startswith("Icosphere"):
                o.hide_render = True
            else:
                toon.keep_file_normals(o)
                keep.append(o)
        return arm, keep

    def tint(self, skin: str, hair: str, dyes: dict[str, tuple]) -> None:
        body_mat = toon.material(f"skin_{self.body}_{skin}_{hair}", skin, hair, shadow_tint=toon.SKIN_SHADOW)
        for o in self.regions:
            toon.assign(o, body_mat)
        for asset, objs in self.items.items():
            if asset.startswith("hair/"):
                mat = toon.material(f"hair_{asset}_{hair}", hair, shadow_tint=toon.HAIR_SHADOW)
            else:
                d = dyes.get(asset, ("#808080", "#808080", "#808080"))
                mat = toon.material(f"dye_{asset}_{'_'.join(d)}", *d)
            for o in objs:
                toon.assign(o, mat)


class Pair:
    """The current (left) and new (right) character of one body."""

    def __init__(self, cur_root: str, new_root: str, body: str):
        self.chars = []
        for root, dx in ((cur_root, -GAP), (new_root, GAP)):
            with open(os.path.join(root, "characters", "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            ch = ToonCharacter(root, manifest, body)
            ch.arm.location.x = dx
            ch.dx = dx
            self.chars.append(ch)
        self.body = body
        self.az = 0.0
        global _PAIR
        _PAIR = self

    def place(self, azimuth: float) -> None:
        """Stand the two side by side across the view (current left, new
        right), so side views do not hide one behind the other."""
        self.az = azimuth
        a = math.radians(azimuth)
        for ch in self.chars:
            s = 1.0 if ch.dx > 0 else -1.0
            ch.arm.location.x = s * GAP * math.cos(a)
            ch.arm.location.y = s * GAP * math.sin(a)
        bpy.context.view_layer.update()

    def anim(self, clip: str) -> dict:
        return self.chars[1].manifest["animations"][clip]

    def base_layer(self, hair_idx: int = HAIR) -> None:
        for ch in self.chars:
            ch.clear_items()
            hair = RC.CREATOR["hair_styles"][hair_idx]["asset"]
            if hair in ch.manifest["assets"] and self.body in ch.manifest["assets"][hair].get("files", {}):
                ch.add(hair)
                ch.hide_regions(set(ch.manifest["assets"][hair]["hides"]))
            ch.tint(RC.CREATOR["skin_tones"][SKIN]["hex"], RC.CREATOR["hair_colours"][HAIR_COL]["hex"], {})

    def dress(self, outfit: int, house: str | None = None) -> None:
        name, items, hair, h, skin, hc = RC.OUTFITS[outfit]
        for ch in self.chars:
            RC.dress(ch, RC.resolve(items, house or h), hair, house or h, skin, hc)

    def keys(self, name: str | None) -> None:
        for ch in self.chars:
            ch.reset_keys()
            if name:
                ch.set_key(name, 1.0)

    def play(self, clip: str | None, frame: float = 0) -> None:
        for ch in self.chars:
            ch.play(clip, frame)
        self.place(self.az)


_PAIR: "Pair | None" = None


def aim(target, azimuth: float, elevation: float, distance: float, lens: float = 50.0) -> None:
    """RD.aim at the pair's centre, with the pair placed across the view."""
    if _PAIR is not None:
        _PAIR.place(azimuth)
    RD.aim(target, azimuth, elevation, distance, lens=lens)


def ingame_camera(azimuth: float) -> None:
    if _PAIR is not None:
        _PAIR.place(azimuth)
    cam = bpy.context.scene.camera
    cam.data.sensor_fit = "VERTICAL"
    cam.data.angle_y = INGAME["fov"]
    a, e, d = math.radians(azimuth), math.radians(INGAME["elevation"]), INGAME["distance"]
    from mathutils import Vector

    t = Vector((0.0, 0.0, INGAME["target"]))
    pos = t + Vector((d * math.sin(a) * math.cos(e), -d * math.cos(a) * math.cos(e), d * math.sin(e)))
    cam.location = pos
    cam.rotation_euler = (t - pos).to_track_quat("-Z", "Y").to_euler()


def normal_camera() -> None:
    cam = bpy.context.scene.camera
    cam.data.sensor_fit = "AUTO"


def shoot(out: str, tag: str) -> str:
    return RD.shoot(os.path.join(out, f"_{tag}.png"))


VIEWS = (("front", 0, 4), ("three", 35, 8), ("side", 90, 4), ("back", 180, 6))


def sheet_ingame(p: Pair, out: str) -> None:
    p.base_layer()
    p.keys(None)
    p.play(None)
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = 960, 540
    paths = []
    for v, az, _ in VIEWS:
        ingame_camera(az)
        paths.append(shoot(out, f"ig_{v}"))
    normal_camera()
    sc.render.resolution_x, sc.render.resolution_y = 900, 720
    RD.sheet(paths, os.path.join(out, f"ingame_{p.body}.png"), 2)


def _views(p: Pair, out: str, tag: str, dist: float, views=VIEWS, z: float | None = None) -> list[str]:
    h = RC.height(p.chars[0])
    paths = []
    for v, az, el in views:
        aim((0, 0, h * 0.52 if z is None else z), az, el, dist, lens=50)
        paths.append(shoot(out, f"{tag}_{v}"))
    return paths


def sheet_closeup(p: Pair, out: str) -> None:
    p.base_layer()
    p.keys(None)
    p.play(None)
    RD.sheet(_views(p, out, "cu", 4.0), os.path.join(out, f"closeup_{p.body}.png"), 4)


def sheet_builds(p: Pair, out: str) -> None:
    p.base_layer()
    p.play(None)
    paths = []
    for key in (None, "build_slim", "build_muscular", "build_heavy"):
        p.keys(key)
        paths += _views(p, out, f"b_{key}", 4.0, VIEWS[:3])
    p.keys(None)
    RD.sheet(paths, os.path.join(out, f"builds_{p.body}.png"), 3)


def sheet_walk(p: Pair, out: str) -> None:
    p.base_layer()
    p.keys(None)
    n = p.anim("walk")["frames"]
    frames = [round(n * i / 8) for i in range(8)]
    paths = []
    for f in frames:
        p.play("walk", f)
        ingame_camera(90)
        paths.append(shoot(out, f"w_ig_{f}"))
    normal_camera()
    for f in frames:
        p.play("walk", f)
        aim((0, 0, RC.height(p.chars[0]) * 0.5), 35, 8, 4.0, lens=50)
        paths.append(shoot(out, f"w_34_{f}"))
    p.play(None)
    RD.sheet(paths, os.path.join(out, f"walk_{p.body}.png"), 8)


def sheet_anims(p: Pair, out: str) -> None:
    p.dress(0)
    p.keys(None)
    paths = []
    for clip in ("wave", "bow", "sit"):
        n = p.anim(clip)["frames"]
        for f in (round(n * 0.25), round(n * 0.5), round(n * 0.75)):
            p.play(clip, f)
            for v, az, el in (("three", 35, 8), ("side", 90, 4)):
                aim((0, 0, RC.height(p.chars[0]) * (0.38 if clip == "sit" else 0.5)), az, el, 4.0, lens=50)
                paths.append(shoot(out, f"an_{clip}_{f}_{v}"))
    p.play(None)
    RD.sheet(paths, os.path.join(out, f"anims_{p.body}.png"), 6)


def sheet_faces(p: Pair, out: str) -> None:
    p.base_layer(hair_idx=2)
    p.play(None)
    keys = [None] + [k for s in RC.CREATOR["sliders"] if s["group"] == "face" for k in s["shape_keys"]]
    keys += ["blink_l", "smile", "frown", "mouth_open", "brows_up", "brows_down"]
    h = RC.height(p.chars[0])
    eye = h - 0.12 * (h / 1.78)
    paths = []
    p.place(0)
    for k in keys:
        p.keys(k)
        for i, ch in enumerate(p.chars):  # current, then new: one close-up each
            RD.aim((ch.arm.location.x, -0.05, eye - 0.03), 12, 2, 0.8, lens=85)
            paths.append(shoot(out, f"fa_{k}_{i}"))
    p.keys(None)
    RD.sheet(paths, os.path.join(out, f"faces_{p.body}.png"), 6)


def sheet_outfits(p: Pair, out: str) -> None:
    paths = []
    for i in range(len(RC.OUTFITS)):
        p.dress(i)
        p.keys(None)
        p.play(None)
        paths += _views(p, out, f"o_{i}", 4.0, (VIEWS[1],))
    for i, key in ((0, "build_heavy"), (1, "build_slim"), (2, "build_muscular")):
        p.dress(i)
        p.keys(key)
        paths += _views(p, out, f"ob_{i}", 4.0, (VIEWS[1],))
    p.keys(None)
    RD.sheet(paths, os.path.join(out, f"outfits_{p.body}.png"), 5)


def sheet_hair(p: Pair, out: str) -> None:
    paths = []
    h = RC.height(p.chars[0])
    for i in (0, 2, 4, 6, 8, 11):
        for cap in (False, True):
            for ch in p.chars:
                ch.clear_items()
                style = RC.CREATOR["hair_styles"][i]["asset"]
                ch.add(style, cap=cap)
                if cap:
                    ch.add("gear/hat/pointed_a")
                ch.hide_regions(set(ch.manifest["assets"][style]["hides"]) | ({"scalp"} if cap else set()))
                ch.tint(RC.CREATOR["skin_tones"][(i * 7) % len(RC.CREATOR["skin_tones"])]["hex"], RC.CREATOR["hair_colours"][(i * 5) % 12]["hex"],
                        RC.dyes_for(["gear/hat/pointed_a"], "roarwing"))
            p.place(0)
            for j, ch in enumerate(p.chars):
                RD.aim((ch.arm.location.x, ch.arm.location.y, h - 0.12), 30, 6, 1.5, lens=85)
                paths.append(shoot(out, f"h_{i}_{cap}_{j}"))
    RD.sheet(paths, os.path.join(out, f"hair_{p.body}.png"), 4)


def sheet_preview(p: Pair, out: str) -> None:
    """Quick look while iterating: full figures front and 3/4, face close-ups
    (front, 3/4, side) of both, with the base-layer hair."""
    p.base_layer(hair_idx=PREVIEW_HAIR)
    p.keys(None)
    p.play(None)
    h = RC.height(p.chars[0])
    paths = _views(p, out, "pv", 3.6, (VIEWS[0], VIEWS[1]))
    eye = h - 0.12 * (h / 1.78)
    p.place(0)
    for v, az, el in (("front", 0, 3), ("three", 32, 4), ("side", 88, 2)):
        for i, ch in enumerate(p.chars):
            RD.aim((ch.arm.location.x, -0.05, eye - 0.035), az, el, 0.75, lens=85)
            paths.append(shoot(out, f"pvf_{v}_{i}"))
    RD.sheet(paths, os.path.join(out, f"preview_{p.body}.png"), 4)


PREVIEW_HAIR = 2
ANIME_HAIR = (0, 1, 2, 4, 6)  # creator hair indices restyled as anime clumps (charlib/hair.py)


def sheet_anime_hair(p: Pair, out: str) -> None:
    """The anime hair styles on both bodies: front, 3/4 and back head shots,
    current body left, new right."""
    paths = []
    h = RC.height(p.chars[0])
    p.keys(None)
    p.play(None)
    for k, i in enumerate(ANIME_HAIR):
        style = RC.CREATOR["hair_styles"][i]["asset"]
        for ch in p.chars:
            ch.clear_items()
            if style in ch.manifest["assets"] and p.body in ch.manifest["assets"][style].get("files", {}):
                ch.add(style)
                ch.hide_regions(set(ch.manifest["assets"][style]["hides"]))
            ch.tint(RC.CREATOR["skin_tones"][(2 + 3 * k) % len(RC.CREATOR["skin_tones"])]["hex"], RC.CREATOR["hair_colours"][(1, 7, 3, 0, 9)[k]]["hex"], {})
        p.place(0)
        for v, az, el in (("front", 0, 4), ("three", 35, 8), ("back", 125, 8)):
            for j, ch in enumerate(p.chars):
                RD.aim((ch.arm.location.x, ch.arm.location.y, h - 0.14 * h / 1.78), az, el, 1.1, lens=85)
                paths.append(shoot(out, f"ah_{i}_{v}_{j}"))
    for ch in p.chars:
        ch.clear_items()
    RD.sheet(paths, os.path.join(out, f"anime_hair_{p.body}.png"), 6)


def sheet_lods(p: Pair, out: str) -> None:
    ch = p.chars[1]
    p.place(0)
    p.base_layer()
    p.keys(None)
    p.play(None)
    paths = []
    for lod in (0, 1):
        for o in ch.regions:
            is1 = o.get("lod", 0) == 1 or o.name.endswith("_lod1")
            o.hide_render = is1 != (lod == 1)
        for o in p.chars[0].regions:
            o.hide_render = True
        for v, az, el in (("front", 0, 4), ("three", 35, 8)):
            RD.aim((GAP, 0, RC.height(ch) * 0.52), az, el, 2.6, lens=50)
            paths.append(shoot(out, f"l_{lod}_{v}"))
            RD.aim((GAP, -0.05, RC.height(ch) - 0.14), az, 2, 0.9, lens=85)
            paths.append(shoot(out, f"lf_{lod}_{v}"))
    for o in p.chars[0].regions:
        o.hide_render = o.get("lod", 0) == 1 or o.name.endswith("_lod1")
    p.base_layer()
    RD.sheet(paths, os.path.join(out, f"lods_{p.body}.png"), 4)


def main() -> None:
    a = args()
    cur = os.path.abspath(a.current)
    new = os.path.abspath(a.new)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    only = set(a.only.split(","))
    steps = [("ingame", sheet_ingame), ("closeup", sheet_closeup), ("builds", sheet_builds), ("walk", sheet_walk),
             ("anims", sheet_anims), ("faces", sheet_faces), ("outfits", sheet_outfits), ("hair", sheet_hair),
             ("lods", sheet_lods), ("preview", sheet_preview), ("anime_hair", sheet_anime_hair)]
    for body in a.bodies.split(","):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        A.build_image()
        toon.studio((900, 720))
        p = Pair(cur, new, body)
        for name, fn in steps:
            if name in only:
                print(f"[render] {body} {name}", flush=True)
                fn(p, out)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
