"""Review renders of the hairstyles (character adoption, docs/art/character-adoption.md section 4).

    blender -b --factory-startup --python tools/blender/characters/render_hair.py -- \\
        --blend art/characters/body_a.blend --out <dir> [--styles short_tousled] \\
        [--meshy vendor_assets/meshy] [--guides <measure_hair_guides.py out dir>] [--colours 1,13]

For every style of the body in the file (built by build_characters.py or
build_hair.py) writes:

* `<style>_views.png`: front, three-quarter, side and back, toon-lit as the
  client's character shading contract describes (character-adoption.md
  section 5: soft narrow
  step moved by the shade layer, cool tinted hair shadow, sky lift, rim,
  the angel ring from `_SHADE.y/z`, the fringe shadow on the forehead from
  `fringe_hem`), in each `--colours` hair colour (creator index; default
  Dark Brown and Sky Blue), without and with the school hat (the `_cap`
  variant under `gear/hat/pointed_a`);
* `<style>_concept.png` (with `--meshy`): the concept's front, side and
  back next to the render from the same side;
* `<style>_guide.png` (with `--meshy` and `--guides`): the registered Meshy
  guide and our hair in the same flat grey, for the volume and silhouette.

The concept and guide images are study material: these sheets stay out of
the repository (review folders only).
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

from charlib import atlas as A  # noqa: E402
from charlib import content  # noqa: E402
from charlib import hair_frame as HF  # noqa: E402
from charlib import montage  # noqa: E402
from charlib import render as RD  # noqa: E402
from charlib import toon  # noqa: E402

CREATOR = content.creator()


def concept(name: str, hair) -> tuple[str, tuple]:
    """The concept id (`m2_b`) and its front, side and back image indices of
    the guide the built hair was made from (the default guide for locks)."""
    from charlib import hair_meshy as HM

    gid = next((g for g, d in HM.GUIDES[name].items() if d["guide"] == hair.get("guide")), None)
    st = HM.style(name, gid)
    return st["concept"][2:], tuple(st["concept_order"])

VIEWS = (("front", 0, 3), ("three", 35, 6), ("side", 90, 3), ("back", 180, 5))
TILE = (420, 540)
ROBE = ("#1E1D22", "#7A1F24", "#C9A13B")  # school robe: black, house red, gold
HAT = ("#1E1D22", "#2B2A30", "#C9A13B")
HAIR_SHADOW = (0.80, 0.78, 0.92)
SKIN_SHADOW = (0.97, 0.74, 0.70)


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--styles", default="")
    p.add_argument("--meshy", default="")
    p.add_argument("--guides", default="")
    p.add_argument("--colours", default="1,13")
    p.add_argument("--skin", type=int, default=2)
    p.add_argument("--only", default="views,concept,guide")
    p.add_argument("--pose", default="", help="clip:frame to render the views in (default the rest pose)")
    p.add_argument("--assets", default="client/public/assets", help="where the face textures are (characters/face_<body>.png)")
    p.add_argument("--eye", type=int, default=1, help="eye colour (creator index) on the painted face")
    return p.parse_args(argv)


# ----------------------------------------------------------------------------
# Materials (preview of the client's character toon shading)
# ----------------------------------------------------------------------------


def _smooth(g, e0, e1, x):
    """smoothstep(e0, e1, x) with math nodes (x a socket)."""
    t = g.math("DIVIDE", g.math("SUBTRACT", x, e0), e1 - e0)
    n = g.nt.nodes.new("ShaderNodeClamp")
    g.L.new(t, n.inputs["Value"])
    t = n.outputs[0]
    return g.math("MULTIPLY", g.math("MULTIPLY", t, t), g.math("SUBTRACT", 3.0, g.math("MULTIPLY", t, 2.0)))


def _vec_dot(g, a, b):
    n = g.nt.nodes.new("ShaderNodeVectorMath")
    n.operation = "DOT_PRODUCT"
    g.L.new(a, n.inputs[0])
    if isinstance(b, tuple):
        n.inputs[1].default_value = b
    else:
        g.L.new(b, n.inputs[1])
    return n.outputs["Value"]


def _scale(g, col, f):
    """colour * factor (socket or number)."""
    n = g.nt.nodes.new("ShaderNodeMix")
    n.data_type = "RGBA"
    n.blend_type = "MULTIPLY"
    n.inputs["Factor"].default_value = 1.0
    g.L.new(col, n.inputs["A"])
    if isinstance(f, (int, float)):
        n.inputs["B"].default_value = (f, f, f, 1.0)
    else:
        comb = g.nt.nodes.new("ShaderNodeCombineColor")
        for i in range(3):
            g.L.new(f, comb.inputs[i])
        g.L.new(comb.outputs[0], n.inputs["B"])
    return n.outputs["Result"]


def face_albedo(g, face: tuple, skin, hair, eye):
    """The client's face albedo (character-look.md section 3.1): the painted face
    texture x mix(1, skin, c.a) x mix(1, eye, m.r) x mix(1, hair, m.g), on
    the head's (face-texture) UVs."""
    paths = []
    for path, colour in zip(face, (True, False)):
        key = os.path.basename(path)
        img = bpy.data.images.get(key) or bpy.data.images.load(path)
        img.colorspace_settings.name = "sRGB" if colour else "Non-Color"
        tex = g.nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.interpolation = "Linear"
        paths.append(tex)
    c, m = paths
    sep = g.nt.nodes.new("ShaderNodeSeparateColor")
    g.L.new(m.outputs["Color"], sep.inputs["Color"])
    col = g.mix("MIX", (1, 1, 1), skin, c.outputs["Alpha"])
    col = g.mix("MULTIPLY", c.outputs["Color"], col)
    col = g.mix("MULTIPLY", col, g.mix("MIX", (1, 1, 1), eye, sep.outputs["Red"]))
    return g.mix("MULTIPLY", col, g.mix("MIX", (1, 1, 1), hair, sep.outputs["Green"]))


def toon_material(name: str, cols, kind: str, shade_attr: str | None = None, fringe_attr: str | None = None,
                  face: tuple | None = None, eye=(0.5, 0.35, 0.2)):
    """kind "hair" | "skin" | "cloth". Emission of: tinted atlas albedo x a
    soft step on N.L (threshold moved by the shade layer), tinted shadow
    band, sky lift, rim, and on hair the angel ring. `face` (colour, mask
    PNG paths): the head's painted face texture instead of the atlas."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    g = toon._Graph(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    lin = [toon._lin(c) for c in cols]
    albedo = face_albedo(g, face, tuple(lin[0]), tuple(lin[1]), tuple(toon._lin(eye))) if face else g.albedo(lin, "Color")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    normal = geo.outputs["Normal"]
    if kind == "hair":
        # The file's own (custom) normals, stored by `keep_normals`.
        at = nt.nodes.new("ShaderNodeAttribute")
        at.attribute_name = "file_normal"
        vt = nt.nodes.new("ShaderNodeVectorTransform")
        vt.vector_type = "NORMAL"
        vt.convert_from = "OBJECT"
        vt.convert_to = "WORLD"
        g.L.new(at.outputs["Vector"], vt.inputs["Vector"])
        nn = nt.nodes.new("ShaderNodeVectorMath")
        nn.operation = "NORMALIZE"
        g.L.new(vt.outputs["Vector"], nn.inputs[0])
        normal = nn.outputs["Vector"]
    diff = g.node("ShaderNodeBsdfDiffuse", Color=(1, 1, 1, 1))
    g.L.new(normal, diff.inputs["Normal"])
    s2r = nt.nodes.new("ShaderNodeShaderToRGB")
    g.L.new(diff.outputs[0], s2r.inputs[0])
    bw = nt.nodes.new("ShaderNodeRGBToBW")
    g.L.new(s2r.outputs["Color"], bw.inputs[0])
    v = bw.outputs[0]
    x = None
    if shade_attr:
        at = nt.nodes.new("ShaderNodeAttribute")
        at.attribute_name = shade_attr
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        g.L.new(at.outputs["Color"], sep.inputs["Color"])
        x, y, z = sep.outputs["Red"], sep.outputs["Green"], sep.outputs["Blue"]
    shift = {"hair": 0.45, "cloth": 0.55, "skin": 0.28}[kind]
    base_t = {"hair": 0.20, "cloth": 0.20, "skin": 0.12}[kind]
    t = g.math("ADD", base_t, g.math("MULTIPLY", g.math("SUBTRACT", 1.0, x), shift)) if x is not None else base_t
    if isinstance(t, float):
        lit = _smooth(g, t - 0.05, t + 0.05, v)
    else:
        lit = _smooth(g, 0.0, 1.0, g.math("ADD", g.math("MULTIPLY", g.math("SUBTRACT", v, t), 10.0), 0.5))
    if fringe_attr:
        fa = nt.nodes.new("ShaderNodeAttribute")
        fa.attribute_name = fringe_attr
        lit = g.math("MULTIPLY", lit, g.math("SUBTRACT", 1.0, fa.outputs["Fac"]))
    tint = {"hair": HAIR_SHADOW, "skin": SKIN_SHADOW, "cloth": (0.74, 0.78, 0.92)}[kind]
    sl = {"hair": 0.58, "skin": 0.78, "cloth": 0.55}[kind]
    shadow = g.mix("MULTIPLY", albedo, tuple(c * sl for c in tint))
    col = g.mix("MIX", shadow, albedo, lit)
    up = _vec_dot(g, normal, (0.0, 0.0, 1.0))
    sky = g.math("ADD", 1.0, g.math("MULTIPLY", up, 0.18))
    if x is not None:
        sky = g.math("MULTIPLY", sky, g.math("ADD", 0.80, g.math("MULTIPLY", x, 0.20)))
    col = _scale(g, col, sky)
    if kind == "hair":
        view_up = _vec_dot(g, geo.outputs["Incoming"], (0.0, 0.0, 1.0))
        c = g.math("ADD", g.math("ADD", 0.28, g.math("SUBTRACT", z, 0.5)), g.math("MULTIPLY", view_up, 0.06))
        d = g.math("ABSOLUTE", g.math("SUBTRACT", y, c))
        band = g.math("SUBTRACT", 1.0, _smooth(g, 0.0, 0.05, d))
        amount = g.math("MULTIPLY", g.math("MULTIPLY", band, 0.45),
                        g.math("ADD", lit, g.math("MULTIPLY", g.math("SUBTRACT", 1.0, lit), 0.333)))
        # A lighter, slightly desaturated hair colour (not white).
        light = g.mix("SCREEN", albedo, (0.34, 0.32, 0.32), 1.0)
        col = g.mix("MIX", col, light, amount)
    ndv = _vec_dot(g, normal, geo.outputs["Incoming"])
    # No rim where the (bent) normal faces away from the camera: visible
    # undersides would light up at their edges.
    rim = g.math("MULTIPLY", _smooth(g, 0.70, 1.0, g.math("SUBTRACT", 1.0, ndv)), g.math("GREATER_THAN", ndv, 0.0))
    rim = g.math("MULTIPLY", rim, g.math("ADD", 0.15, g.math("MULTIPLY", lit, 0.25)) if kind != "skin" else 0.10)
    rim_col = g.mix("SCREEN", albedo, (0.45, 0.47, 0.55), 1.0)
    col = g.mix("MIX", col, rim_col, rim)
    em = nt.nodes.new("ShaderNodeEmission")
    g.L.new(col, em.inputs["Color"])
    g.L.new(em.outputs[0], out.inputs["Surface"])
    mat["outline"] = toon.outline_material(cols[0], cols[1], cols[2], name=f"{name}_outline").name
    return mat


def fringe_attribute(head, hem: list, strength=0.55, depth=0.03, soft=0.012) -> None:
    """The client's fringe shadow on the forehead (character-adoption.md section 4.3) as a vertex
    attribute of the head: front-facing skin below the hem, fading over
    `depth`."""
    me = head.data
    n = len(me.vertices)
    co = np.zeros(n * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3) @ np.array(head.matrix_world)[:3, :3].T + np.array(head.matrix_world)[:3, 3]
    nr = np.zeros(n * 3)
    me.vertices.foreach_get("normal", nr)
    nr = nr.reshape(-1, 3)
    val = np.zeros(n)
    if hem:
        H = np.array(hem)  # glTF (x, y up, z fwd)
        hx, hy = H[:, 0], H[:, 1]
        z_hem = np.interp(co[:, 0], hx, hy, left=np.nan, right=np.nan)
        d = z_hem - co[:, 2]
        front = -nr[:, 1] > 0.2
        ok = front & ~np.isnan(d) & (d > -0.002)
        dd = np.nan_to_num(d)
        f_in = np.clip(dd / (soft * 0.35), 0, 1)
        f_out = 1 - np.clip((dd - (depth - soft)) / soft, 0, 1)
        val = np.where(ok, strength * f_in * f_out, 0.0)
    a = me.attributes.get("fringe") or me.attributes.new("fringe", "FLOAT", "POINT")
    a.data.foreach_set("value", val.astype(np.float32))


def keep_normals(obj) -> None:
    """The mesh's corner normals (its exported custom normals) as an
    attribute: the preview shades with exactly what the file carries."""
    me = obj.data
    if "file_normal" in me.attributes:
        return
    n = np.zeros(len(me.loops) * 3, dtype=np.float32)
    me.corner_normals.foreach_get("vector", n)
    a = me.attributes.new("file_normal", "FLOAT_VECTOR", "CORNER")
    a.data.foreach_set("vector", n)


def flat_grey(name="grey"):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.72, 0.72, 0.75, 1)
    nt.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.8
    return mat


# ----------------------------------------------------------------------------
# Scene
# ----------------------------------------------------------------------------


def outline(obj, mat, width):
    toon.assign(obj, mat)
    mod = obj.modifiers.get("toon_outline")
    if mod is not None and width is not None:
        mod.thickness = width


def shoot_views(path_base: str, target, dist: float, views=VIEWS) -> list[str]:
    out = []
    for name, az, el in views:
        RD.aim(target, az, el, dist, lens=85)
        out.append(RD.shoot(f"{path_base}_{name}.png"))
    return out


def fit_image(path: str, size=TILE) -> np.ndarray:
    """A concept image scaled to fit a tile (white margins), Blender pixel order."""
    px = montage.load_rgba(path)
    h, w = px.shape[:2]
    W, Hh = size
    s = min(W / w, Hh / h)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    ys = (np.arange(nh) / s).astype(int).clip(0, h - 1)
    xs = (np.arange(nw) / s).astype(int).clip(0, w - 1)
    img = px[ys][:, xs]
    if img.shape[2] == 4:
        a = img[:, :, 3:4]
        img = img * a + (1 - a)
        img[:, :, 3] = 1
    out = np.ones((Hh, W, 4), dtype=np.float32)
    y0, x0 = (Hh - nh) // 2, (W - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = img
    return out


def save_tile(px: np.ndarray, path: str) -> str:
    montage.save_rgba(path, px)
    return path


def main() -> None:
    a = args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    bb = HF.load_body(os.path.abspath(a.blend), drop_hair=False)
    fr = HF.HeadFrame(bb)
    only = set(a.only.split(","))
    old = bpy.data.images.get("character_atlas")
    if old is not None:
        bpy.data.images.remove(old)
    A.build_image()
    toon.studio(TILE)
    import check_characters as CC

    if a.pose:
        clip, frame = a.pose.split(":")
        CC.pose(bb.skel, clip, int(frame))
    else:
        CC.pose(bb.skel, None, 0)
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    CC.set_build(objs, "")  # every shape key off (saved files keep them at 1: closed eyes, mixed builds)
    lod0 = [o for o in objs if o.get("lod") == 0]
    regions = {o["region"]: o for o in lod0 if o.get("region")}
    hairs = [o for o in lod0 if str(o.get("asset", "")).startswith("hair/")]
    for o in hairs:
        keep_normals(o)
    robe = next((o for o in lod0 if o.get("asset") == "gear/body/robe_school_house_a"), None)
    hat = next((o for o in lod0 if o.get("asset") == "gear/hat/pointed_a"), None)
    for o in objs:
        o.hide_render = True
    skin_hex = CREATOR["skin_tones"][a.skin]["hex"]
    eye_hex = CREATOR["eye_colours"][a.eye]["hex"]
    # The painted face (character look): the head samples its own texture pair when the export has it.
    body_id = "_".join(os.path.splitext(os.path.basename(a.blend))[0].split("_")[:2])
    face = tuple(os.path.abspath(os.path.join(a.assets, "characters", f"{n}_{body_id}.png")) for n in ("face", "face_mask"))
    face = face if all(os.path.exists(p) for p in face) else None
    colours = [int(c) for c in a.colours.split(",")]
    styles = sorted({str(o["asset"]).split("/")[-1] for o in hairs})
    if a.styles:
        styles = [s for s in styles if s in set(a.styles.split(","))]
    if robe is not None:
        outline(robe, toon_material("robe", ROBE, "cloth"), None)
    if hat is not None:
        outline(hat, toon_material("hat", HAT, "cloth"), None)
    for name in styles:
        hair = next(o for o in hairs if o["asset"] == f"hair/{name}" and not o.get("cap"))
        # (Styles without a hat variant wear their own file under hats.)
        capv = next((o for o in hairs if o["asset"] == f"hair/{name}" and o.get("cap")), hair)
        long = name in ("long_bangs", "ponytail_locks")
        tgt = (0.0, float(fr.centre[1]), float(fr.centre[2]) - (0.16 if long else 0.05))
        dist = 2.1 if long else 1.45
        files = []
        if "views" in only:
            for hc in colours:
                hex_ = CREATOR["hair_colours"][hc]["hex"]
                for with_hat in (False, True):
                    h = capv if with_hat else hair
                    hides = set(h.get("hides", []))
                    for o in objs:
                        o.hide_render = True
                    for r, o in regions.items():
                        if r in hides or r in ("hips", "thighs", "calves", "feet"):
                            continue
                        if robe is not None and r in set(robe.get("hides", [])):
                            continue
                        o.hide_render = False
                        if r == "head":
                            fringe_attribute(o, list(h.get("fringe_hem", [])) if not with_hat else list(h.get("fringe_hem", [])))
                        outline(o, toon_material(f"skin_{hc}{'_face' if r == 'head' and face else ''}", (skin_hex, hex_, "#7A5232"), "skin",
                                                 fringe_attr="fringe" if r == "head" else None,
                                                 face=face if r == "head" else None, eye=eye_hex),
                                0.0004 if r == "head" else None)
                    if robe is not None:
                        robe.hide_render = False
                    if with_hat and hat is not None:
                        hat.hide_render = False
                    h.hide_render = False
                    outline(h, toon_material(f"hair_{hc}", (hex_, "#FFFFFF", "#FFFFFF"), "hair", shade_attr="_SHADE"), 0.0007)
                    files += shoot_views(os.path.join(out, f"_{name}_{hc}_{int(with_hat)}"), tgt, dist)
            montage.tile(files, os.path.join(out, f"{name}_views.png"), 4, remove=True)
            print(f"[hair-render] {name}_views.png", flush=True)
        if "hero" in only:
            hc = colours[0]
            hex_ = CREATOR["hair_colours"][hc]["hex"]
            for o in objs:
                o.hide_render = True
            for r, o in regions.items():
                if r in set(hair.get("hides", [])) or r in ("hips", "thighs", "calves", "feet"):
                    continue
                if robe is not None and r in set(robe.get("hides", [])):
                    continue
                o.hide_render = False
                if r == "head":
                    fringe_attribute(o, list(hair.get("fringe_hem", [])))
                outline(o, toon_material(f"skin_{hc}{'_face' if r == 'head' and face else ''}", (skin_hex, hex_, "#7A5232"), "skin",
                                         fringe_attr="fringe" if r == "head" else None,
                                         face=face if r == "head" else None, eye=eye_hex), 0.0004 if r == "head" else None)
            if robe is not None:
                robe.hide_render = False
            hair.hide_render = False
            outline(hair, toon_material(f"hair_{hc}", (hex_, "#FFFFFF", "#FFFFFF"), "hair", shade_attr="_SHADE"), 0.0007)
            sc = bpy.context.scene
            rx, ry = sc.render.resolution_x, sc.render.resolution_y
            sc.render.resolution_x, sc.render.resolution_y = 900, 1000
            tiles = []
            for az, el in ((30, 8), (150, 10)):
                RD.aim((0.0, float(fr.centre[1]), float(fr.centre[2]) - (0.12 if long else 0.02)), az, el,
                       1.7 if long else 1.0, lens=85)
                tiles.append(RD.shoot(os.path.join(out, f"_h_{name}_{az}.png")))
            montage.tile(tiles, os.path.join(out, f"{name}_hero.png"), 2, remove=True)
            sc.render.resolution_x, sc.render.resolution_y = rx, ry
            print(f"[hair-render] {name}_hero.png", flush=True)
        if "concept" in only and a.meshy:
            cid, order = concept(name, hair)
            hc = colours[0]
            hex_ = CREATOR["hair_colours"][hc]["hex"]
            for o in objs:
                o.hide_render = True
            for r, o in regions.items():
                if r in set(hair.get("hides", [])) or r in ("hips", "thighs", "calves", "feet", "torso", "upper_arms",
                                                             "forearms", "hands"):
                    continue
                o.hide_render = False
                if r == "head":
                    fringe_attribute(o, list(hair.get("fringe_hem", [])))
                outline(o, toon_material(f"skin_{hc}{'_face' if r == 'head' and face else ''}", (skin_hex, hex_, "#7A5232"), "skin",
                                         fringe_attr="fringe" if r == "head" else None,
                                         face=face if r == "head" else None, eye=eye_hex), 0.0004 if r == "head" else None)
            for r in ("torso",):
                if r in regions:
                    regions[r].hide_render = False
            hair.hide_render = False
            outline(hair, toon_material(f"hair_{hc}", (hex_, "#FFFFFF", "#FFFFFF"), "hair", shade_attr="_SHADE"), 0.0007)
            tiles = []
            for (vname, az, el), idx in zip((("front", 0, 0), ("side", 90, 0), ("back", 180, 0)), order):
                tiles.append(save_tile(fit_image(os.path.join(a.meshy, f"c_{cid}_{idx}.png")),
                                       os.path.join(out, f"_c_{name}_{vname}.png")))
                RD.aim((0.0, float(fr.centre[1]), float(fr.centre[2]) - (0.10 if long else 0.03)), az, el,
                       2.0 if long else 1.3, lens=85)
                tiles.append(RD.shoot(os.path.join(out, f"_r_{name}_{vname}.png")))
            montage.tile(tiles, os.path.join(out, f"{name}_concept.png"), 6, remove=True)
            print(f"[hair-render] {name}_concept.png", flush=True)
        if "guide" in only and a.meshy and a.guides:
            cid, _ = concept(name, hair)
            reg = os.path.join(a.guides, f"{cid}_{bb.id}.json")
            if os.path.exists(reg):
                guide_sheet(a, out, name, cid, reg, objs, regions, hair, fr, long)
    print("[hair-render] done", flush=True)


def guide_sheet(a, out, name, cid, reg, objs, regions, hair, fr, long) -> None:
    from mathutils import Matrix

    with open(reg, encoding="utf-8") as f:
        r = json.load(f)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(a.meshy, f"g_{cid}.glb"))
    guide = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    for o in guide:
        o.matrix_world = Matrix.Translation(tuple(r["offset"])) @ Matrix.Scale(r["scale"], 4) @ o.matrix_world
        o.data.materials.clear()
        o.data.materials.append(flat_grey())
    sky = bpy.data.lights.new("guide_fill", "SUN")
    sky.energy = 2.0
    lo = bpy.data.objects.new("guide_fill", sky)
    lo.rotation_euler = (math.radians(50), 0, math.radians(-30))
    bpy.context.scene.collection.objects.link(lo)
    tiles = []
    tgt = (0.0, float(fr.centre[1]), float(fr.centre[2]) - (0.10 if long else 0.03))
    for vname, az in (("front", 0), ("three", 35), ("side", 90), ("back", 180)):
        for show_guide in (True, False):
            for o in objs:
                o.hide_render = True
            for g in guide:
                g.hide_render = not show_guide
            if not show_guide:
                for rr, o in regions.items():
                    if rr in ("head", "ears", "neck", "torso") and rr not in set(hair.get("hides", [])):
                        o.hide_render = False
                        o.data.materials.clear()
                        o.data.materials.append(flat_grey("grey_skin"))
                        if o.modifiers.get("toon_outline"):
                            o.modifiers.remove(o.modifiers["toon_outline"])
                hair.hide_render = False
                hair.data.materials.clear()
                hair.data.materials.append(flat_grey())
                if hair.modifiers.get("toon_outline"):
                    hair.modifiers.remove(hair.modifiers["toon_outline"])
            RD.aim(tgt, az, 3, 2.0 if long else 1.3, lens=85)
            tiles.append(RD.shoot(os.path.join(out, f"_g_{name}_{vname}_{int(show_guide)}.png")))
    montage.tile(tiles, os.path.join(out, f"{name}_guide.png"), 8, remove=True)
    for o in guide:
        bpy.data.objects.remove(o)
    bpy.data.objects.remove(lo)
    print(f"[hair-render] {name}_guide.png", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
