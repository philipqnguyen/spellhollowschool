"""Review renders of the anime head and hair on the MakeHuman bodies.

    blender -b --factory-startup --python tools/blender/characters/render_head_trial.py -- \\
        --new <assets root> [--before <assets root of the earlier MakeHuman-head build>] \\
        --out art/renders/body-trial/head [--bodies body_a,body_b] [--only head,full,expressions,ears,hats,before]

Every sheet uses the shared cel-shaded setup (charlib/toon.py). Sheets per body:

* `head_<body>`: each anime hairstyle, front / 3-4 / profile / back close-ups;
* `full_<body>`: full figures (front and 3/4) with three hairstyles and in uniform;
* `expressions_<body>`: neutral, every expression and face slider (front);
* `ears_<body>`: the closest crop, 3/4 front, profile and 3/4 back (ears, skull);
* `hats_<body>`: hats over the hairstyles' cap variants (3/4 and profile);
* `before_<body>`: the earlier MakeHuman head (left of each pair) against the
  anime head (right), front / 3-4 / profile.

Brows are drawn over the fringe the way anime games do it (a stencil pass):
the brow faces are rendered once more on top of the finished frame in front
and 3/4 views (`--no-brow-overlay` turns it off). That is a review-render
emulation; the client toon shader will need the same pass.

The reference images of the style brief are never composited into these
sheets (they must not enter the repositories); `--refs <dir>` writes extra
side-by-side sheets with them to a scratch directory instead.
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
import numpy as np  # noqa: E402

import render_body_trial as RBT  # noqa: E402
import render_characters as RC  # noqa: E402
from charlib import atlas as A  # noqa: E402
from charlib import montage  # noqa: E402
from charlib import render as RD  # noqa: E402
from charlib import skeleton as K  # noqa: E402
from charlib import toon  # noqa: E402
from charlib.proportions import get as body_of  # noqa: E402

ANIME = ("hair/short_crop", "hair/tousled", "hair/side_part", "hair/long_straight", "hair/ponytail_high")
SKIN = {"body_a": 2, "body_b": 1}
HAIR_COL = {"hair/short_crop": 1, "hair/tousled": 7, "hair/side_part": 3, "hair/long_straight": 0,
            "hair/ponytail_high": 9}
FACE_KEYS = ("blink_l", "smile", "frown", "mouth_open", "brows_up", "brows_down", "face_jaw_narrow", "face_jaw_wide",
             "face_cheek_soft", "face_cheek_high", "face_nose_small", "face_nose_large", "face_brow_low",
             "face_brow_high", "face_eyes_narrow", "face_eyes_round")
OVERLAY = True
_SHOTS: list = []


def args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--new", default="client/public/assets")
    p.add_argument("--before", default="")
    p.add_argument("--out", default="art/renders/body-trial/head")
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--only", default="head,full,expressions,ears,hats,before")
    p.add_argument("--refs", default="", help="scratch dir for side-by-side sheets with the style-brief references")
    p.add_argument("--ref-images", default="", help="dir with the reference PNGs (Kaeya.png, Jean.png)")
    p.add_argument("--no-brow-overlay", action="store_true")
    return p.parse_args(argv)


def load(root: str, body: str, dx: float = 0.0) -> RBT.ToonCharacter:
    with open(os.path.join(root, "characters", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    ch = RBT.ToonCharacter(root, manifest, body)
    ch.arm.location.x = dx
    ch.dx = dx
    return ch


def style(ch, hair: str | None, cap: bool = False, hat: str | None = None, hair_col: int | None = None,
          dress: list[str] | None = None) -> None:
    ch.clear_items()
    hides: set[str] = set()
    if hair and hair in ch.manifest["assets"]:
        ch.add(hair, cap=cap)
        hides |= set(ch.manifest["assets"][hair]["hides"])
    for item in ([hat] if hat else []) + (dress or []):
        if item in ch.manifest["assets"]:
            ch.add(item)
            hides |= set(ch.manifest["assets"][item].get("hides", []))
    ch.hide_regions(hides)
    hc = RC.CREATOR["hair_colours"][HAIR_COL.get(hair, 1) if hair_col is None else hair_col]["hex"]
    items = ([hat] if hat else []) + (dress or [])
    ch.tint(RC.CREATOR["skin_tones"][SKIN[ch.body]]["hex"], hc, RC.dyes_for(items, "skytalon"))
    ch.reset_keys()


# ----------------------------------------------------------------------------
# Brows over the fringe
# ----------------------------------------------------------------------------


def brow_overlay(ch) -> bpy.types.Object | None:
    """A copy of the head's brow faces (same armature and shape keys)."""
    head = next((o for o in ch.regions if o.get("region") == "head" and not o.hide_render), None)
    if head is None:
        return None
    u0, v0 = A.uv("brow")
    me = head.data.copy()
    ob = head.copy()
    ob.data = me
    ob.name = f"{head.name}_brows"
    bpy.context.scene.collection.objects.link(ob)
    uv = me.uv_layers.active.data
    keep = []
    for p in me.polygons:
        c = np.mean([uv[i].uv for i in p.loop_indices], axis=0)
        keep.append(abs(c[0] - u0) < 0.02 and abs(c[1] - v0) < 0.02)
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[f for f, k in zip(bm.faces, keep) if not k], context="FACES")
    bm.to_mesh(me)
    bm.free()
    for m in list(ob.modifiers):
        if m.type != "ARMATURE":
            ob.modifiers.remove(m)
    ob.data.materials.clear()
    for m in head.data.materials[:1]:
        ob.data.materials.append(m)
    ob.hide_render = True
    return ob


def shoot(ch, path: str, overlay: bool) -> str:
    """Render; with `overlay`, the brows are drawn again over the frame."""
    RD.shoot(path)
    ov = getattr(ch, "brows", None)
    if not (overlay and OVERLAY and ov is not None):
        return path
    sc = bpy.context.scene
    shown = [o for o in sc.objects if not o.hide_render]
    for o in shown:
        o.hide_render = True
    ov.hide_render = False
    sc.render.film_transparent = True
    tmp = path[:-4] + "_brows.png"
    RD.shoot(tmp)
    sc.render.film_transparent = False
    ov.hide_render = True
    for o in shown:
        o.hide_render = False
    base = montage.load_rgba(path)
    top = montage.load_rgba(tmp)
    a = top[..., 3:4]
    out = base.copy()
    out[..., :3] = base[..., :3] * (1 - a) + top[..., :3] * a
    montage.save_rgba(path, out)
    os.remove(tmp)
    return path


def head_frame(ch) -> tuple[np.ndarray, float]:
    """Eye-level head centre and head scale of the loaded character, measured
    on its head and scalp meshes (a body reshaped onto a sculpt has its own
    head size and height, unlike the spec body of the same id)."""
    cached = getattr(ch, "_head_frame", None)
    if cached is not None:
        return cached
    pts = []
    for o in ch.regions:
        if o.get("region") in ("head", "scalp") and not (o.get("lod", 0) == 1 or o.name.endswith("_lod1")):
            mw = o.matrix_world
            pts += [tuple(mw @ v.co) for v in o.data.vertices]
    if not pts:
        b = body_of(ch.body)
        out = (np.array(K.head_centre(b)), b.head_scale)
    else:
        P = np.array(pts)
        chin, crown = float(P[:, 2].min()), float(P[:, 2].max())
        h = crown - chin
        lvl = P[np.abs(P[:, 2] - (chin + 0.47 * h)) < 0.01]
        y = 0.5 * (float(lvl[:, 1].min()) + float(lvl[:, 1].max())) if len(lvl) else float(P[:, 1].mean())
        out = (np.array([float(P[:, 0].mean()), y, chin + 0.47 * h]), h / 0.234)
    ch._head_frame = out
    return out


def face_cam(ch, az: float, el: float, dist: float = 0.62, dz: float = -0.035) -> None:
    hc, s = head_frame(ch)
    RD.aim((ch.arm.location.x, hc[1], hc[2] + dz * s), az, el, dist * max(s, 0.8), lens=85)


def _brows(ch) -> None:
    old = getattr(ch, "brows", None)
    if old is not None:
        bpy.data.objects.remove(old)
    ch.brows = brow_overlay(ch)
    if ch.brows is not None:
        ch.brows.parent = ch.arm


VIEWS = (("front", 0, 3, True), ("three", 35, 5, True), ("side", 90, 2, False), ("back", 160, 8, False))


def sheet_head(ch, out: str) -> list[str]:
    paths = []
    for hair in ANIME:
        style(ch, hair)
        _brows(ch)
        for v, az, el, ov in VIEWS:
            face_cam(ch, az, el, 0.85, 0.0)
            paths.append(shoot(ch, os.path.join(out, f"_hd_{ch.body}_{hair[5:]}_{v}.png"), ov))
    return paths


def sheet_ears(ch, out: str) -> list[str]:
    """Ears and head shape with the closest crop: 3/4 front, profile, 3/4 back."""
    paths = []
    style(ch, "hair/close_crop")
    _brows(ch)
    for v, az, el in (("three", 40, 4), ("side", 90, 2), ("back3", 130, 4)):
        face_cam(ch, az, el, 0.75, 0.0)
        paths.append(shoot(ch, os.path.join(out, f"_er_{ch.body}_{v}.png"), v == "three"))
    return paths


def sheet_full(ch, out: str) -> list[str]:
    paths = []
    b = body_of(ch.body)
    looks = [("hair/short_crop", None), ("hair/side_part", None), ("hair/long_straight", None),
             ("hair/ponytail_high", ["gear/body/robe_school_house_c", "gear/legs/skirt_pleated_a",
                                     "gear/shoes/loafers_buckle_a"]),
             ("hair/tousled", ["gear/body/robe_school_house_a", "gear/legs/trousers_school_a",
                               "gear/shoes/oxford_school_a"])]
    for hair, dress in looks:
        style(ch, hair, dress=dress)
        _brows(ch)
        for v, az, el in (("front", 0, 4), ("three", 35, 8)):
            RD.aim((ch.arm.location.x, 0, b.height * 0.52), az, el, 3.8, lens=50)
            paths.append(shoot(ch, os.path.join(out, f"_fb_{ch.body}_{hair[5:]}_{v}.png"), False))
    return paths


def sheet_expressions(ch, out: str) -> list[str]:
    paths = []
    style(ch, "hair/short_crop")
    _brows(ch)
    for k in (None,) + FACE_KEYS:
        ch.reset_keys()
        if k:
            ch.set_key(k, 1.0)
            if ch.brows is not None and ch.brows.data.shape_keys and k in ch.brows.data.shape_keys.key_blocks:
                for kb in ch.brows.data.shape_keys.key_blocks[1:]:
                    kb.value = 1.0 if kb.name == k else 0.0
        elif ch.brows is not None and ch.brows.data.shape_keys:
            for kb in ch.brows.data.shape_keys.key_blocks[1:]:
                kb.value = 0.0
        face_cam(ch, 0, 2, 0.55)
        paths.append(shoot(ch, os.path.join(out, f"_ex_{ch.body}_{k}.png"), True))
    ch.reset_keys()
    return paths


def sheet_hats(ch, out: str) -> list[str]:
    paths = []
    hats = sorted(a for a in ch.manifest["assets"] if a.startswith("gear/hat/") and ch.body in ch.manifest["assets"][a].get("files", {}))
    for hat in hats[:4]:
        for hair in ("hair/short_crop", "hair/side_part", "hair/long_straight", "hair/ponytail_high"):
            style(ch, hair, cap=True, hat=hat)
            ch.brows = None
            face_cam(ch, 35, 6, 1.0, 0.02)
            paths.append(shoot(ch, os.path.join(out, f"_ht_{ch.body}_{hat[9:]}_{hair[5:]}.png"), False))
    return paths


def sheet_before(before, ch, out: str) -> list[str]:
    paths = []
    for c in (before, ch):
        style(c, "hair/short_crop")
        c.brows = None
    for v, az, el, ov in VIEWS[:3]:
        for c in (before, ch):
            face_cam(c, az, el, 0.7)
            paths.append(shoot(c, os.path.join(out, f"_bf_{ch.body}_{v}_{c.dx:+.1f}.png"), False))
    return paths


def ref_tile(path: str, crop, size) -> str:
    """A square crop of a reference image, nearest-resized (scratch sheets only)."""
    px = montage.load_rgba(path)
    h = px.shape[0]
    x0, y0, s = crop
    sub = px[h - (y0 + s):h - y0, x0:x0 + s]
    idx = (np.arange(size[1]) * s / size[1]).astype(int)
    sq = sub[idx][:, idx]
    out = np.ones((size[1], size[0], 4), dtype=np.float32)
    off = (size[0] - size[1]) // 2
    out[:, off:off + size[1]] = sq
    out[..., 3] = 1.0
    tmp = f"{path}.{len(_SHOTS)}.tile.png"
    _SHOTS.append(tmp)
    montage.save_rgba(tmp, out)
    return tmp


REF_CROPS = {"body_a": ("Kaeya.png", (380, 60, 300)), "body_b": ("Jean.png", (360, 110, 300))}


def main() -> None:
    global OVERLAY
    a = args()
    OVERLAY = not a.no_brow_overlay
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    only = set(a.only.split(","))
    for body in a.bodies.split(","):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        A.build_image()
        toon.studio((800, 800))
        ch = load(os.path.abspath(a.new), body, 0.0)
        if "head" in only:
            p = sheet_head(ch, out)
            if a.refs:
                os.makedirs(a.refs, exist_ok=True)
                name, crop = REF_CROPS[body]
                rows = []
                for i in range(len(ANIME)):
                    rows.append(ref_tile(os.path.join(a.ref_images, name), crop, (800, 800)))
                    rows += p[4 * i:4 * i + 4]
                montage.tile(rows, os.path.join(a.refs, f"refs_head_{body}.png"), 5, remove=False)
                for x in set(rows[::5]):
                    os.remove(x)
            RD.sheet(p, os.path.join(out, f"head_{body}.png"), 4)
        if "ears" in only:
            RD.sheet(sheet_ears(ch, out), os.path.join(out, f"ears_{body}.png"), 3)
        if "expressions" in only:
            RD.sheet(sheet_expressions(ch, out), os.path.join(out, f"expressions_{body}.png"), 6)
        if "hats" in only:
            p = sheet_hats(ch, out)
            if p:
                RD.sheet(p, os.path.join(out, f"hats_{body}.png"), 4)
        if "full" in only:
            sc = bpy.context.scene
            sc.render.resolution_x, sc.render.resolution_y = 600, 900
            RD.sheet(sheet_full(ch, out), os.path.join(out, f"full_{body}.png"), 5)
            sc.render.resolution_x, sc.render.resolution_y = 800, 800
        if "before" in only and a.before:
            ch.arm.location.x = 0.6
            ch.dx = 0.6
            before = load(os.path.abspath(a.before), body, -0.6)
            RD.sheet(sheet_before(before, ch, out), os.path.join(out, f"before_{body}.png"), 6)
        print(f"[head renders] {body} done", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
