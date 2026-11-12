"""See-through check of the hairstyles: background or bare scalp showing
through the hair (character look pass 2, docs/art/character-look.md section 2).

    blender -b --factory-startup -t 3 --python tools/blender/characters/check_hair_see_through.py -- \\
        --art art [--bodies body_a,body_b] [--styles short_tousled] [--out <dir>] [--json <file>] \\
        [--radius 5] [--height 1100] [--max-ppm 0]

Opens art/characters/<body>.blend (build_characters.py or build_hair.py
output) and renders every style's LOD0 hair on its body in the shipped
uniform (the school robe and the oxford shirt) in the rest pose, against an
empty background, from VIEWS (front, back, both sides, the four
three-quarters, and from slightly above front, back and side), framed as the
creator preview frames it (vertical field of view 0.55 rad, 1.0 m away, 1.5 m
for the long styles; `--height` pixels high: about the preview canvas at
Medium, DPR 2), with back faces culled as the client draws them, in flat
colours: hair, the head's painted scalp (above the border of the scalp
region; the client hides that region under any hair, as it hides the ears
under long hair, so a gap there shows the background), the rest of the body
and the garments, the background.

Per view: the hair's closed silhouette is the hair mask closed with a disc
of `--radius` pixels (slits narrower than about two radii) with its enclosed
holes filled; every background or scalp pixel inside it is a see-through
pixel. Reported per style and view in pixels, and in parts per million of the
hair's own pixels. With `--out`, writes per style a sheet of the views: hair
dark, see-through pixels bright (background yellow, scalp magenta). Exits
non-zero when any view's see-through share exceeds `--max-ppm` (default: no
limit, report only).

The closing also fills the narrow notches between neighbouring pointed tips
in a jagged hem and the gaps between locks (background seen past the hair,
not through it): that part stays when the hair is fixed, so compare numbers
between builds of the same framing, not against zero.

The exact measure of the single-sided shell's breaks is the second one,
**back faces**: a second render of each view with a flipped copy of the hair
(drawn where the hair's own faces are back-facing, so culled in game) counts
the pixels where the nearest hair surface is seen from behind, in total and
where the game shows the background or the scalp through it. An inner layer
in front of those back faces (pass 2) takes them to zero.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

FOV = 0.55  # the creator preview camera (client/src/game/characters/preview.ts)
VIEWS = (("front", 0, 0), ("front_l", 45, 0), ("left", 90, 0), ("back_l", 135, 0), ("back", 180, 0),
         ("back_r", 225, 0), ("right", 270, 0), ("front_r", 315, 0), ("above_front", 0, 25),
         ("above_back", 180, 25), ("above_left", 90, 25))
LONG = ("long_bangs", "ponytail_locks")
GARMENTS = ("gear/body/robe_school_house_a", "gear/body/shirt_oxford_tie_a")
# Flat colours (linear, sRGB "Standard" view: exact in the render) and the classes.
HAIR, SCALP, BODY, GARMENT, BG, HAIR_BACK = range(6)
COLOURS = {HAIR: (1.0, 0.0, 0.0), SCALP: (1.0, 0.0, 1.0), BODY: (0.0, 0.0, 1.0), GARMENT: (0.0, 1.0, 0.0),
           HAIR_BACK: (0.0, 1.0, 1.0)}


# ----------------------------------------------------------------------------
# Image analysis (numpy only: Blender's Python has no scipy)
# ----------------------------------------------------------------------------


def disc(radius: int) -> list[tuple[int, int]]:
    r = int(radius)
    return [(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if dy * dy + dx * dx <= r * r + r]


def _shift(m: np.ndarray, dy: int, dx: int, fill: bool) -> np.ndarray:
    out = np.full_like(m, fill)
    h, w = m.shape
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = m[ys, xs]
    return out


def dilate(m: np.ndarray, radius: int) -> np.ndarray:
    out = m.copy()
    for dy, dx in disc(radius):
        out |= _shift(m, dy, dx, False)
    return out


def erode(m: np.ndarray, radius: int) -> np.ndarray:
    out = m.copy()
    for dy, dx in disc(radius):
        out &= _shift(m, dy, dx, True)
    return out


def closing(m: np.ndarray, radius: int) -> np.ndarray:
    """Morphological closing with a disc (pixels beyond the border count as
    empty for the dilation and as filled for the erosion)."""
    return erode(dilate(m, radius), radius) | m


def fill_holes(m: np.ndarray) -> np.ndarray:
    """`m` with every region of ~m that does not reach the image border
    filled (4-connected flood from the border)."""
    free = ~m
    reach = np.zeros_like(m)
    reach[0, :] = free[0, :]
    reach[-1, :] = free[-1, :]
    reach[:, 0] |= free[:, 0]
    reach[:, -1] |= free[:, -1]
    while True:
        prev = reach
        for _ in range(16):
            grow = reach.copy()
            grow[1:, :] |= reach[:-1, :]
            grow[:-1, :] |= reach[1:, :]
            grow[:, 1:] |= reach[:, :-1]
            grow[:, :-1] |= reach[:, 1:]
            reach = grow & free
        if np.array_equal(prev, reach):
            break
    return ~reach


def see_through(cls: np.ndarray, radius: int, back: np.ndarray | None = None) -> dict:
    """Background and scalp pixels inside the hair's closed silhouette;
    with `back` (the classes of the render with the flipped hair copy), the
    pixels where the nearest hair surface is a back face and the game (`cls`)
    shows the background through it (`backface_bg_px`) or the head's painted
    scalp (`backface_scalp_px`: hair-coloured in game, so counted apart). A
    total of the flipped copy's pixels is not given: with an inner layer the
    copy of the inner sheet lies under the shell and shows through it by
    depth fighting where the two meet, which the game never draws."""
    hair = cls == HAIR
    closed = fill_holes(closing(hair, radius))
    inside = closed & ~hair
    bg = inside & (cls == BG)
    scalp = inside & (cls == SCALP)
    n = int(hair.sum())
    out = {"hair_px": n, "background_px": int(bg.sum()), "scalp_px": int(scalp.sum()),
           "ppm": round(1e6 * (int(bg.sum()) + int(scalp.sum())) / max(n, 1), 1), "_bg": bg, "_scalp": scalp}
    if back is not None:
        bf = back == HAIR_BACK
        out["backface_bg_px"] = int((bf & (cls == BG)).sum())
        out["backface_scalp_px"] = int((bf & (cls == SCALP)).sum())
        out["_back"] = bf & ((cls == BG) | (cls == SCALP))
        out["_back_open"] = bf & (cls == BG)
    return out


def classify(px: np.ndarray) -> np.ndarray:
    """Class per pixel of a flat-colour render (RGBA float, 0..1)."""
    cls = np.full(px.shape[:2], BG, dtype=np.int8)
    a = px[..., 3] > 0.5
    best = np.full(px.shape[:2], np.inf)
    for k, c in COLOURS.items():
        d = ((px[..., :3] - np.array(c)) ** 2).sum(axis=-1)
        m = a & (d < best)
        cls[m] = k
        best[m] = d[m]
    return cls


def overlay(cls: np.ndarray, res: dict) -> np.ndarray:
    """RGBA review image: hair dark brown, scalp grey-pink, body and garments
    light, background white; see-through background yellow, scalp magenta;
    hair seen from behind (back faces, culled in game) cyan where the painted
    scalp shows through them, red where the background does."""
    img = np.ones(cls.shape + (4,), dtype=np.float32)
    img[cls == HAIR, :3] = (0.22, 0.13, 0.08)
    img[cls == SCALP, :3] = (0.75, 0.6, 0.7)
    img[cls == BODY, :3] = (0.8, 0.82, 0.88)
    img[cls == GARMENT, :3] = (0.62, 0.66, 0.72)
    if "_back" in res:
        img[res["_back"], :3] = (0.0, 0.75, 0.95)
    img[dilate(res["_bg"], 1), :3] = (1.0, 0.85, 0.0)
    img[dilate(res["_scalp"], 1), :3] = (1.0, 0.0, 1.0)
    if "_back_open" in res:
        img[dilate(res["_back_open"], 1), :3] = (0.9, 0.0, 0.0)
    return img


# ----------------------------------------------------------------------------
# Scene
# ----------------------------------------------------------------------------


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--art", default="art")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--styles", default="")
    p.add_argument("--out", default="")
    p.add_argument("--json", default="")
    p.add_argument("--radius", type=int, default=5)
    p.add_argument("--height", type=int, default=1100)
    p.add_argument("--width", type=int, default=900)
    p.add_argument("--max-ppm", type=float, default=-1.0)
    p.add_argument("--cap", action="store_true", help="the _cap variants (no hat drawn) instead")
    return p.parse_args(argv)


def setup_scene(width: int, height: int):
    import bpy

    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x, sc.render.resolution_y = width, height
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.display.render_aa = "OFF"
    sh = sc.display.shading
    sh.light = "FLAT"
    sh.color_type = "VERTEX"
    sh.show_backface_culling = True
    sh.show_object_outline = False
    sh.show_cavity = False
    sh.show_shadows = False
    sh.show_specular_highlight = False
    sh.show_xray = False
    cam = bpy.data.objects.get("_st_camera")
    if cam is None:
        cd = bpy.data.cameras.new("_st_camera")
        cam = bpy.data.objects.new("_st_camera", cd)
        sc.collection.objects.link(cam)
    cam.data.sensor_fit = "VERTICAL"
    cam.data.lens_unit = "FOV"
    cam.data.angle_y = FOV
    cam.data.clip_start = 0.05
    sc.camera = cam
    return sc


def aim(target, az: float, el: float, dist: float) -> None:
    import bpy
    from mathutils import Vector

    cam = bpy.context.scene.camera
    a, e = math.radians(az), math.radians(el)
    t = Vector(target)
    pos = t + Vector((dist * math.sin(a) * math.cos(e), -dist * math.cos(a) * math.cos(e), dist * math.sin(e)))
    cam.location = pos
    cam.rotation_euler = (t - pos).to_track_quat("-Z", "Y").to_euler()


SCALP_MARGIN = 5.0  # degrees below the scalp region's border still counted as the head's painted scalp


def flat_colours(o, fr, kind: int) -> None:
    """A per-vertex colour attribute `_st` (made active) of the class
    colour; on the head (`fr` given) the vertices above the scalp region's
    border take the scalp colour."""
    me = o.data
    a = me.color_attributes.get("_st") or me.color_attributes.new("_st", "FLOAT_COLOR", "POINT")
    n = len(me.vertices)
    col = np.tile(np.array((*COLOURS[kind], 1.0), dtype=np.float32), (n, 1))
    if fr is not None:
        co = np.zeros(n * 3)
        me.vertices.foreach_get("co", co)
        M = np.array(o.matrix_world)
        co = co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
        phi, theta, _ = fr.polar(co)
        col[phi < fr.scalp_border(theta) + SCALP_MARGIN] = (*COLOURS[SCALP], 1.0)
    a.data.foreach_set("color", col.ravel())
    me.color_attributes.active_color = a


def render_pixels(path: str) -> np.ndarray:
    import bpy

    sc = bpy.context.scene
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.zeros(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[::-1]  # top row first


def save_png(path: str, img: np.ndarray) -> None:
    import bpy

    h, w = img.shape[:2]
    im = bpy.data.images.new("_st_out", w, h, alpha=True)
    im.pixels.foreach_set(np.ascontiguousarray(img[::-1]).ravel())
    im.filepath_raw = path
    im.file_format = "PNG"
    im.save()
    bpy.data.images.remove(im)


def check_body(path: str, a, out: dict) -> list[str]:
    import bpy

    from check_characters import pose, set_build
    from charlib import hair_frame as HF

    bb = HF.load_body(path, drop_hair=False)
    fr = HF.HeadFrame(bb)
    setup_scene(a.width, a.height)
    pose(bb.skel, None, 0)
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    set_build(objs, "")
    lod0 = [o for o in objs if o.get("lod") == 0]
    regions = {str(o["region"]): o for o in lod0 if o.get("region")}
    garments = [o for o in lod0 if o.get("asset") in GARMENTS and not o.get("region")]
    garment_hides = set()
    for g in garments:
        garment_hides |= set(g.get("hides", []))
    hairs = [o for o in lod0 if str(o.get("asset", "")).startswith("hair/") and bool(o.get("cap")) == a.cap]
    only = set(filter(None, a.styles.split(",")))
    failures = []
    res_body = {}
    for h in sorted(hairs, key=lambda o: o.name):
        name = str(h["asset"]).split("/")[-1]
        if only and name not in only:
            continue
        hides = set(h.get("hides", []))
        for o in objs:
            o.hide_render = True
        # As the client draws it: the regions the hair hides (the scalp, the
        # ears under long hair) are not drawn, so a gap there shows the
        # background; the head's painted scalp (above the scalp region's
        # border, plus SCALP_MARGIN degrees) is told apart from the face.
        for r, o in regions.items():
            if r in garment_hides or r in hides or r in ("hips", "thighs", "calves", "feet"):
                continue
            o.hide_render = False
            flat_colours(o, fr if r == "head" else None, BODY)
        for g in garments:
            g.hide_render = False
            flat_colours(g, None, GARMENT)
        h.hide_render = False
        flat_colours(h, None, HAIR)
        # The flipped copy: drawn exactly where the hair is seen from behind.
        flip = h.copy()
        flip.data = h.data.copy()
        flip.data.flip_normals()
        for c in h.users_collection:
            c.objects.link(flip)
        flat_colours(flip, None, HAIR_BACK)
        flip.hide_render = True
        long = name in LONG
        tgt = (0.0, float(fr.centre[1]), float(fr.centre[2]) - (0.16 if long else 0.05))
        dist = 1.5 if long else 1.0
        rows = {}
        tiles = []
        for vname, az, el in VIEWS:
            aim(tgt, az, el, dist)
            tmp = os.path.join(a.tmp, f"_{name}_{vname}.png")
            px = render_pixels(tmp)
            flip.hide_render = False
            pb = render_pixels(tmp)
            flip.hide_render = True
            os.remove(tmp)
            cls = classify(px)
            r = see_through(cls, a.radius, classify(pb))
            rows[vname] = {k: v for k, v in r.items() if not k.startswith("_")}
            if a.max_ppm >= 0 and r["ppm"] > a.max_ppm:
                failures.append(f"{name} {vname} {r['ppm']} ppm")
            if a.out:
                tiles.append(overlay(cls, r))
        tot = {k: sum(v[k] for v in rows.values()) for k in ("hair_px", "background_px", "scalp_px", "backface_bg_px",
                                                            "backface_scalp_px")}
        tot["ppm"] = round(1e6 * (tot["background_px"] + tot["scalp_px"]) / max(tot["hair_px"], 1), 1)
        rows["total"] = tot
        res_body[name] = rows
        me = flip.data
        bpy.data.objects.remove(flip)
        bpy.data.meshes.remove(me)
        # Per view: closed-silhouette background+scalp / back-face background+scalp.
        print(f"[see-through] {bb.id} {name}: " + " ".join(
            f"{v}={rows[v]['background_px']}+{rows[v]['scalp_px']}/{rows[v]['backface_bg_px']}+{rows[v]['backface_scalp_px']}"
            for v, _, _ in VIEWS)
              + f" | closed silhouette {tot['background_px']}+{tot['scalp_px']} px ({tot['ppm']} ppm);"
              f" through back faces {tot['backface_bg_px']}+{tot['backface_scalp_px']} px", flush=True)
        if a.out and tiles:
            cols = 4
            hh, ww = tiles[0].shape[:2]
            nrow = (len(tiles) + cols - 1) // cols
            sheet = np.ones((nrow * hh, cols * ww, 4), dtype=np.float32)
            for i, t in enumerate(tiles):
                y, x = divmod(i, cols)
                sheet[y * hh:(y + 1) * hh, x * ww:(x + 1) * ww] = t
            save_png(os.path.join(os.path.abspath(a.out), f"{name}{'_cap' if a.cap else ''}_see_through.png"), sheet)
    out[bb.id] = res_body
    return failures


def main() -> None:
    a = args()
    # Scratch frames go to --out when given, else to a temporary folder removed at the end.
    scratch = None if a.out else tempfile.mkdtemp(prefix="shs-see-through-")
    a.tmp = os.path.abspath(a.out or scratch)
    os.makedirs(a.tmp, exist_ok=True)
    try:
        failures = run_bodies(a)
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    sys.exit(1 if failures else 0)


def run_bodies(a) -> list[str]:
    out: dict = {"radius_px": a.radius, "size": [a.width, a.height], "cap": a.cap}
    failures: list[str] = []
    for body in a.bodies.split(","):
        failures += check_body(os.path.join(os.path.abspath(a.art), "characters", f"{body}.blend"), a, out)
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, sort_keys=True)
            f.write("\n")
    print(f"\n{len(failures)} failure(s)" + "".join(f"\n  {x}" for x in failures))
    return failures


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
