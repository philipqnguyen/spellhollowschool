"""Line-up comparison sheets for the sculpt-wrapped bodies.

    blender -b --factory-startup --python tools/blender/characters/render_wrap_sheets.py -- \\
        --char "pre-wrap male=<assets>:body_a" --char "pre-wrap female=<assets>:body_b" \\
        --char "ALexzart male=<assets>:body_a" --char "DJP3D female=<assets>:body_b" \\
        --char "GreyWax female=<assets>:body_b" --out art/renders/body-trial/wrap [--only bodies,heads,uniform,outfits,builds]

Each `--char` is a caption, an assets root (what the client loads) and the
body id to load from it; the characters are the sheet's columns (or rows),
in the order given. Every frame uses the shared cel-shaded setup
(charlib/toon.py), the same lights, the same skin tone, hair style and
colour, house and dyes, and (full figures) one fixed camera for every
character, so heights and proportions compare directly. Sheets:

* `lineup_bodies.png`: base layer, front / side / back (rows) of every character;
* `lineup_heads.png`: one row per character, the five anime hair styles
  (render_head_trial.ANIME) front and three-quarter, framed on each head;
* `lineup_uniform_poses.png`: the school uniform (house robe, shirt and tie,
  trousers or pleated skirt, school shoes, pointed hat) in the seven checked
  poses (check_characters.FULL_POSES: rest, both walk strides, run, wave,
  bow, sit), one row per character;
* `lineup_outfits.png`: a casual and a knitwear outfit, front / 3/4 / back;
* `lineup_builds.png`: neutral, build_slim, build_muscular, build_heavy,
  front and side;
* `overview.png` (`--only overview`): one labelled summary sheet, a column
  per character: face front and three-quarter (side part hair, no hat), then
  the school uniform full figure front and three-quarter in the rest pose.
  Column headers (caption, sculpt and author, heads tall, LOD0 / LOD1
  triangles, read from each assets root's manifest), row labels and a title
  are burned in with ImageMagick (`magick` on PATH, or `--magick`); without
  it the sheet is written unlabelled.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

import check_characters as CC  # noqa: E402
import render_characters as RC  # noqa: E402
import render_garment_shapes as RGS  # noqa: E402
import render_head_trial as RHT  # noqa: E402
import render_refit_compare as RRC  # noqa: E402
from charlib import atlas as A  # noqa: E402
from charlib import montage  # noqa: E402
from charlib import render as RD  # noqa: E402
from charlib import toon  # noqa: E402

HAIR = "hair/side_part"  # one anime style on every character (has a cap variant for hats)
HOUSE = "roarwing"
SKIN, HAIR_COL = 3, 1
# Outfits per body: body_a wears trousers, body_b a skirt; everything else is the same item.
UNIFORM = {"body_a": ["gear/body/robe_school_house_{h}", "gear/body/shirt_oxford_tie_a", "gear/legs/trousers_school_a",
                      "gear/shoes/oxford_school_a", "gear/hat/pointed_a"],
           "body_b": ["gear/body/robe_school_house_{h}", "gear/body/shirt_oxford_tie_a", "gear/legs/skirt_pleated_a",
                      "gear/shoes/oxford_school_a", "gear/hat/pointed_a"]}
CASUAL = {"body_a": ["gear/body/shirt_linen_rolled_a", "gear/legs/trousers_corduroy_a", "gear/shoes/loafers_buckle_a"],
          "body_b": ["gear/body/shirt_linen_rolled_a", "gear/legs/skirt_midi_wrap_a", "gear/shoes/loafers_buckle_a"]}
KNITWEAR = {"body_a": ["gear/body/sweater_cable_knit_a", "gear/legs/trousers_corduroy_a", "gear/shoes/boots_lace_a",
                       "gear/hat/beanie_cable_a"],
            "body_b": ["gear/body/sweater_cable_knit_a", "gear/legs/kilt_tartan_a", "gear/shoes/boots_lace_a",
                       "gear/hat/beanie_cable_a"]}
BUILDS = (None, "build_slim", "build_muscular", "build_heavy")
REF_H = 1.78  # the fixed full-figure camera is aimed for the tallest spec body
FULL = (400, 500)
HEAD = (300, 340)


def args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--char", action="append", default=[], help="caption=<assets root>:<body id>")
    p.add_argument("--out", default="art/renders/body-trial/wrap")
    p.add_argument("--only", default="bodies,heads,uniform,outfits,builds")
    p.add_argument("--title", default="Body trial: pre-wrap vs sculpt-wrapped bodies")
    p.add_argument("--magick", default=shutil.which("magick") or "", help="ImageMagick 7 binary for the overview labels")
    p.add_argument("--font", default="/System/Library/Fonts/Supplemental/Arial.ttf")
    p.add_argument("--font-bold", default="/System/Library/Fonts/Supplemental/Arial Bold.ttf")
    return p.parse_args(argv)


class Col:
    def __init__(self, text: str):
        self.label, _, rest = text.partition("=")
        root, _, self.body = rest.rpartition(":")
        self.root = os.path.abspath(root)
        with open(os.path.join(self.root, "characters", "manifest.json"), encoding="utf-8") as f:
            self.manifest = json.load(f)
        self.ch: RRC.Char | None = None


def load(cols: list[Col], res) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    A.build_image()
    toon.studio(res)
    for c in cols:
        c.ch = RRC.Char(c.root, c.manifest, c.body)


def solo(cols: list[Col], k: int) -> None:
    RRC.solo([c.ch for c in cols], k)


def dress(c: Col, items: list[str], hair: str = HAIR) -> None:
    ch = c.ch
    ch.clear_items()
    items = RC.resolve(items, HOUSE)
    hat = any(a.startswith("gear/hat/") for a in items)
    hides: set[str] = set()
    for a in items:
        entry = ch.manifest["assets"].get(a)
        if entry is None or c.body not in entry.get("files", {}):
            raise KeyError(f"{c.root}: {a} has no file for {c.body}")
        ch.add(a)
        hides |= set(entry.get("hides", []))
    entry = ch.manifest["assets"][hair]
    ch.add(hair, cap=hat and c.body in entry.get("cap_files", {}))
    hides |= set(entry["hides"])
    ch.hide_regions(hides)
    ch.tint(RC.CREATOR["skin_tones"][SKIN]["hex"], RC.CREATOR["hair_colours"][HAIR_COL]["hex"],
            RGS.dyes_for(items, HOUSE))
    ch.reset_keys()
    if ch.brows is not None:
        bpy.data.objects.remove(ch.brows)
    ch.brows = RHT.brow_overlay(ch)
    if ch.brows is not None:
        ch.brows.parent = ch.arm
    ch._head_frame = None


def caption(text: str):
    cap = RGS.label(text)
    cap.location = (0.0, -0.42, 0.02)  # on the floor in front of the feet
    cap.data.size = 0.085
    return cap


def full_cam(az: float, el: float, dist: float = 3.6, z: float = 0.52) -> None:
    RD.aim((0, 0, REF_H * z), az, el, dist, lens=50)


def shoot(c: Col, path: str, brows: bool) -> str:
    return RRC.shoot(c.ch, path, brows)


def sheet_bodies(cols: list[Col], out: str) -> str:
    load(cols, FULL)
    paths = []
    for v, az, el in (("front", 0, 4), ("side", 90, 4), ("back", 180, 6)):
        for k, c in enumerate(cols):
            solo(cols, k)
            dress(c, [])
            c.ch.play(None)
            caption(c.label if v == "front" else "")
            full_cam(az, el)
            paths.append(shoot(c, os.path.join(out, f"_lu_bodies_{v}_{k}.png"), v == "front"))
    caption("")
    return montage.tile(paths, os.path.join(out, "lineup_bodies.png"), len(cols), remove=True)


def sheet_heads(cols: list[Col], out: str) -> str:
    load(cols, HEAD)
    paths = []
    for k, c in enumerate(cols):
        solo(cols, k)
        c.ch.play(None)
        for hair in RHT.ANIME:
            dress(c, [], hair)
            for v, az, el in (("front", 0, 3), ("three", 35, 5)):
                RHT.face_cam(c.ch, az, el, 0.85, 0.0)
                paths.append(shoot(c, os.path.join(out, f"_lu_heads_{k}_{hair[5:]}_{v}.png"), True))
    return montage.tile(paths, os.path.join(out, "lineup_heads.png"), 2 * len(RHT.ANIME), remove=True)


def sheet_uniform(cols: list[Col], out: str) -> str:
    load(cols, FULL)
    paths = []
    for k, c in enumerate(cols):
        solo(cols, k)
        dress(c, UNIFORM[c.body])
        for i, (pname, clip, frame) in enumerate(CC.FULL_POSES):
            c.ch.play(clip, frame)
            caption(c.label if i == 0 else pname)
            full_cam(35, 8, 3.7, 0.5)
            paths.append(shoot(c, os.path.join(out, f"_lu_uni_{k}_{pname}.png"), True))
        c.ch.play(None)
    caption("")
    return montage.tile(paths, os.path.join(out, "lineup_uniform_poses.png"), len(CC.FULL_POSES), remove=True)


def sheet_outfits(cols: list[Col], out: str) -> str:
    load(cols, FULL)
    paths = []
    for k, c in enumerate(cols):
        solo(cols, k)
        c.ch.play(None)
        for name, outfit in (("casual", CASUAL), ("knitwear", KNITWEAR)):
            dress(c, outfit[c.body])
            for v, az, el in (("front", 0, 4), ("three", 35, 8), ("back", 180, 6)):
                caption(f"{c.label}: {name}" if v == "front" else "")
                full_cam(az, el)
                paths.append(shoot(c, os.path.join(out, f"_lu_out_{k}_{name}_{v}.png"), v != "back"))
    caption("")
    return montage.tile(paths, os.path.join(out, "lineup_outfits.png"), 6, remove=True)


def sheet_builds(cols: list[Col], out: str) -> str:
    load(cols, FULL)
    paths = []
    for k, c in enumerate(cols):
        solo(cols, k)
        c.ch.play(None)
        dress(c, [])
        for key in BUILDS:
            for v, az, el in (("front", 0, 4), ("side", 90, 4)):
                c.ch.reset_keys()
                if key:
                    c.ch.set_key(key, 1.0)
                caption((c.label if key is None else key.replace("build_", "")) if v == "front" else "")
                full_cam(az, el)
                paths.append(shoot(c, os.path.join(out, f"_lu_bld_{k}_{key}_{v}.png"), v == "front"))
        c.ch.reset_keys()
    caption("")
    return montage.tile(paths, os.path.join(out, "lineup_builds.png"), 2 * len(BUILDS), remove=True)


OV_FACE = (400, 400)
OV_FULL = (400, 560)
OV_ROWS = ("face, front", "face, three-quarter", "uniform, front", "uniform, three-quarter")
OPTS = argparse.Namespace(title="", magick="", font="", font_bold="")


def col_info(c: Col) -> list[str]:
    """Header lines for a column: the body's sculpt, heads tall and triangles, from its manifest."""
    e = c.manifest["assets"][f"characters/{c.body}"]
    shape = e.get("shape") or {}
    tri = e.get("triangles", {})
    src = f'{shape["author"]} sculpt (CC BY 4.0)' if shape else "MakeHuman body + scripted head"
    heads = f'{shape["heads_tall"]:.2f} heads tall' if "heads_tall" in shape else "spec proportions"
    return [c.label, src, f"{heads} - {e['bounds']['max'][1]:.2f} m",
            f'LOD0 {tri.get("lod0", 0):,} / LOD1 {tri.get("lod1", 0):,} tris']


def compose_overview(cols: list[Col], grid: list[list[str]], out: str) -> str:
    """Burn the title, column headers and row labels round the tiles (ImageMagick)."""
    m = OPTS.magick
    if not m:
        print("[lineup] overview: no ImageMagick, writing the sheet unlabelled", flush=True)
        flat = [p for row in grid for p in row]
        faces = montage.tile(flat[:2 * len(cols)], out + ".faces.png", len(cols))
        bodies = montage.tile(flat[2 * len(cols):], out + ".bodies.png", len(cols))
        return faces + " " + bodies
    tw = OV_FACE[0]
    left = 170
    bg, fg, dim = "#20232a", "#f2f2f2", "#b8bcc6"
    cmd = [m, "-background", bg]
    # Title band.
    cmd += ["(", "-size", f"{left + tw * len(cols)}x70", "xc:" + bg, "-font", OPTS.font_bold, "-pointsize", "34",
            "-fill", fg, "-gravity", "west", "-annotate", "+24+0", OPTS.title, ")"]
    # Header row: a blank corner, then one header per column.
    cmd += ["(", "-size", f"{left}x150", "xc:" + bg]
    for c in cols:
        lines = col_info(c)
        cmd += ["(", "-size", f"{tw}x150", "xc:" + bg, "-gravity", "north",
                "-font", OPTS.font_bold, "-pointsize", "27", "-fill", fg, "-annotate", "+0+14", lines[0],
                "-font", OPTS.font, "-pointsize", "19", "-fill", dim,
                "-annotate", "+0+56", lines[1], "-annotate", "+0+84", lines[2], "-annotate", "+0+112", lines[3], ")"]
    cmd += ["+append", ")"]
    # Tile rows, each with its label at the left.
    for name, row in zip(OV_ROWS, grid):
        h = OV_FACE[1] if name.startswith("face") else OV_FULL[1]
        cmd += ["(", "(", "-size", f"{left}x{h}", "xc:" + bg, "-font", OPTS.font_bold, "-pointsize", "22",
                "-fill", fg, "-gravity", "center", "-annotate", "+0+0", name.replace(", ", "\n"), ")"]
        cmd += row + ["+append", ")"]
    foot = ("Shared cel-shaded setup, same lights, skin tone, hair (side part) and Roarwing uniform; "
            "full figures use one fixed camera, so heights compare directly.")
    cmd += ["(", "-size", f"{left + tw * len(cols)}x44", "xc:" + bg, "-font", OPTS.font, "-pointsize", "18",
            "-fill", dim, "-gravity", "west", "-annotate", "+24+0", foot, ")"]
    cmd += ["-append", "-strip", out]
    subprocess.run(cmd, check=True)
    return out


def sheet_overview(cols: list[Col], out: str) -> str:
    grid: list[list[str]] = [[] for _ in OV_ROWS]
    load(cols, OV_FACE)
    for k, c in enumerate(cols):
        solo(cols, k)
        c.ch.play(None)
        dress(c, [])
        for r, (az, el) in enumerate(((0, 3), (35, 5))):
            RHT.face_cam(c.ch, az, el, 0.85, 0.0)
            grid[r].append(shoot(c, os.path.join(out, f"_ov_face_{r}_{k}.png"), True))
    load(cols, OV_FULL)
    for k, c in enumerate(cols):
        solo(cols, k)
        dress(c, UNIFORM[c.body])
        c.ch.play(None)
        for r, (az, el) in enumerate(((0, 4), (35, 8)), start=2):
            full_cam(az, el, 3.7, 0.5)
            grid[r].append(shoot(c, os.path.join(out, f"_ov_uni_{r}_{k}.png"), True))
    path = compose_overview(cols, grid, os.path.join(out, "overview.png"))
    for row in grid:
        for p in row:
            os.remove(p)
    return path


SHEETS = {"overview": sheet_overview, "bodies": sheet_bodies, "heads": sheet_heads, "uniform": sheet_uniform, "outfits": sheet_outfits,
          "builds": sheet_builds}


def main() -> None:
    a = args()
    for k in ("title", "magick", "font", "font_bold"):
        setattr(OPTS, k, getattr(a, k))
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    cols = [Col(t) for t in a.char]
    for name in a.only.split(","):
        path = SHEETS[name](cols, out)
        print(f"[lineup] {name}: {path} columns {[c.label for c in cols]}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
