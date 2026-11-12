"""Review stills of the NPCs (docs/art/castle-kit.md "Portraits and tapestries"): every NPC's look,
the talking portraits in their frames, and each placement in the built
castle. Review only (nothing here is shipped).

    blender -b --factory-startup --python tools/blender/npcs/render_npcs.py -- \\
        --assets client/public/assets --out .out/renders [--only looks,portraits,castle] [--npcs id,id]

Needs the built characters (characters/manifest.json), the portrait assets
(npcs/manifest.json, build_portraits.py) and, for `castle`, the built world
(world/manifest.json and its texture library).

- `looks`: each NPC front and three-quarter (`npcs/<id>_front.png`,
  `npcs/<id>_three.png`), contact sheets per kind (`looks_<kind>.png`); the
  ghosts in a stand-in of the client's ghost material (toon_palette.json
  `npcs.ghost`: desaturated, tinted, translucent, a fresnel rim), floating
  `ghost_float_m`;
- `portraits`: each portrait's subject in its frame with its backdrop, the
  head turned a little (`portraits/<id>.png`), a contact sheet
  (`portraits_contact.png`) and two oblique views showing the box's depth
  (`portraits_depth.png`);
- `castle`: each placement in the built castle from a few metres in front
  at eye height (`castle/<id>.png`, sheet `portraits_castle.png`).

The subjects are clipped to the box's inside in their materials, the way
the client clips them (the canvas opening, the back); the shading is a
plain EEVEE preview (the client's toon and painterly look differ)."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
for _p in (os.path.join(BLENDER, "characters"), BLENDER, os.path.join(BLENDER, "world")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

import render_characters as RC  # noqa: E402
from charlib import atlas as CA  # noqa: E402
from charlib import content  # noqa: E402
from charlib import render as RD  # noqa: E402
from npcs import frames as FR  # noqa: E402
from npcs import placements as PL  # noqa: E402

REPO = os.path.dirname(os.path.dirname(BLENDER))
CREATOR = content.creator()
PALETTE = {c["index"]: c["hex"] for c in content.load_json("data/items/palette.json")["colours"]}
ITEMS = {i["id"]: i for i in content.clothing_items()}
HAIR = {h["id"]: h["asset"] for h in CREATOR["hair_styles"] if "asset" in h}
SLOTS = ("hat", "body", "legs", "shoes")
KINDS = ("portrait", "ghost", "staff", "shopkeeper", "student")


def load(rel: str) -> dict:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return json.load(f)


ROSTER = load("data/npcs/roster.json")
LOOKS = load("data/npcs/looks.json")["looks"]
NPC_PALETTE = load("data/art/toon_palette.json")["npcs"]


def args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--assets", default=os.path.join(REPO, "client", "public", "assets"))
    p.add_argument("--out", default=os.path.join(REPO, ".out", "renders"))
    p.add_argument("--only", default="looks,portraits,castle")
    p.add_argument("--npcs", default="")
    p.add_argument("--samples", type=int, default=16)
    return p.parse_args(argv)


# -- dressing a character from a look ---------------------------------------------------------


class Cast:
    """One Character per body, the other moved out of the way."""

    def __init__(self, assets: str):
        with open(os.path.join(assets, "characters", "manifest.json"), encoding="utf-8") as f:
            self.manifest = json.load(f)
        self.chars = {}
        for b in ("body_a", "body_b"):
            self.chars[b] = RC.Character(assets, self.manifest, b)
        self.current = None

    def wear(self, look: dict) -> RC.Character:
        body = CREATOR["body_types"][look["body_type"]]["id"]
        for b, ch in self.chars.items():
            show = b == body
            for o in [ch.arm] + ch.regions + [x for v in ch.items.values() for x in v]:
                o.hide_render = not show or o.hide_render if show else True
        ch = self.chars[body]
        dress(ch, look)
        self.current = ch
        return ch


def dress(ch: RC.Character, look: dict) -> None:
    ch.clear_items()
    ch.reset_keys()
    out = look["outfit"]
    hat = out.get("hat") is not None
    hides: set[str] = set()
    dyes: dict[str, tuple] = {}
    for slot in SLOTS:
        w = out.get(slot)
        if not w:
            continue
        asset = ITEMS[w["item_def"]]["asset"]
        ch.add(asset)
        hides |= set(ch.manifest["assets"][asset]["hides"])
        dyes[asset] = tuple(PALETTE[d] for d in w["dyes"])
    hair = HAIR[look["hair_style"]]
    ch.add(hair, cap=hat)
    hides |= set(ch.manifest["assets"][hair]["hides"])
    ch.hide_regions(hides)
    skin = CREATOR["skin_tones"][look["skin_tone"]]["hex"]
    hair_hex = CREATOR["hair_colours"][look["hair_colour"]]["hex"]
    ch.tint(skin, hair_hex, dyes)
    face = face_material(ch, skin, CREATOR["eye_colours"][look["eye_colour"]]["hex"], hair_hex)
    if face is not None:
        for o in ch.regions:
            if o.get("region") == "head" or o.get("face_texture"):
                o.data.materials.clear()
                o.data.materials.append(face)
    for s in CREATOR["sliders"]:
        if s["field"] != "face":
            continue
        v = look["face"][s["byte"]]
        lo, hi = s["shape_keys"]
        ch.set_key(lo, (128 - v) / 128 if v < 128 else 0.0)
        ch.set_key(hi, (v - 128) / 127 if v > 128 else 0.0)
    k = height_m(look) / CREATOR["body_types"][look["body_type"]]["default_height_m"]
    ch.arm.scale = (k, k, k)
    try:
        ch.play("idle", 20)
    except KeyError:
        ch.play(None)


def face_material(ch: RC.Character, skin: str, eye: str, hair: str):
    """The face texture pair as the client samples it (docs/art/character-look.md
    section 3.1): albedo = c.rgb * mix(1, skin, c.a) * mix(1, eye, m.r) * mix(1, hair, m.g)."""
    face = ch.manifest["assets"][f"characters/{ch.body}"].get("face")
    if not face:
        return None
    name = f"face_{ch.body}_{skin}_{eye}_{hair}"
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.7
    col = nt.nodes.new("ShaderNodeTexImage")
    col.image = image(os.path.join(ch.assets, face["colour"]))
    col.extension = "EXTEND"
    msk = nt.nodes.new("ShaderNodeTexImage")
    msk.image = image(os.path.join(ch.assets, face["mask"]))
    msk.image.colorspace_settings.name = "Non-Color"
    msk.image.alpha_mode = "CHANNEL_PACKED"
    col.image.alpha_mode = "CHANNEL_PACKED"
    msk.extension = "EXTEND"
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(msk.outputs["Color"], sep.inputs["Color"])
    acc = col.outputs["Color"]
    for colour, fac in ((skin, col.outputs["Alpha"]), (eye, sep.outputs["Red"]), (hair, sep.outputs["Green"])):
        mx = nt.nodes.new("ShaderNodeMix")
        mx.data_type = "RGBA"
        mx.inputs["A"].default_value = (1, 1, 1, 1)
        mx.inputs["B"].default_value = (*_lin(colour), 1.0)
        nt.links.new(fac, mx.inputs["Factor"])
        mul = nt.nodes.new("ShaderNodeMix")
        mul.data_type = "RGBA"
        mul.blend_type = "MULTIPLY"
        mul.inputs["Factor"].default_value = 1.0
        nt.links.new(acc, mul.inputs["A"])
        nt.links.new(mx.outputs["Result"], mul.inputs["B"])
        acc = mul.outputs["Result"]
    nt.links.new(acc, bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


def height_m(look: dict) -> float:
    b = CREATOR["body_types"][look["body_type"]]
    lo, hi = b["height_m"]
    d = b["default_height_m"]
    h = look["height"]
    return lo + (d - lo) * h / 128 if h <= 128 else d + (hi - d) * (h - 128) / 127


def meshes(ch: RC.Character) -> list:
    return [o for o in ch.visible() if o.type == "MESH"]


# -- materials ---------------------------------------------------------------------------------


def _lin(c):
    return tuple(CA.hex_to_linear(c)) if isinstance(c, str) else tuple(c)


def ghostify(ch: RC.Character) -> None:
    """The client's ghost look, approximated: the toon albedo desaturated and
    tinted, translucent, a fresnel rim (toon_palette.json npcs.ghost)."""
    g = NPC_PALETTE["ghost"]
    done = {}
    for o in meshes(ch):
        for slot in o.material_slots:
            m = slot.material
            if m is None:
                continue
            if m.name not in done:
                gm = m.copy()
                gm.name = f"ghost_{m.name}"
                nt = gm.node_tree
                bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
                src = bsdf.inputs["Base Color"].links[0].from_socket
                hs = nt.nodes.new("ShaderNodeHueSaturation")
                hs.inputs["Saturation"].default_value = 1.0 - g["desaturate"]
                nt.links.new(src, hs.inputs["Color"])
                mul = nt.nodes.new("ShaderNodeMix")
                mul.data_type = "RGBA"
                mul.blend_type = "MULTIPLY"
                mul.inputs["Factor"].default_value = 1.0
                nt.links.new(hs.outputs["Color"], mul.inputs["A"])
                mul.inputs["B"].default_value = (*[c ** 2.2 for c in g["tint"]], 1.0)
                nt.links.new(mul.outputs["Result"], bsdf.inputs["Base Color"])
                lw = nt.nodes.new("ShaderNodeLayerWeight")
                lw.inputs["Blend"].default_value = 1.0 / g["rimPower"]
                rim = nt.nodes.new("ShaderNodeMath")
                rim.operation = "MULTIPLY"
                rim.inputs[1].default_value = g["rimStrength"] * 2.0
                nt.links.new(lw.outputs["Facing"], rim.inputs[0])
                bsdf.inputs["Emission Color"].default_value = (*[c ** 2.2 for c in g["rim"]], 1.0)
                nt.links.new(rim.outputs[0], bsdf.inputs["Emission Strength"])
                alpha = nt.nodes.new("ShaderNodeMath")
                alpha.operation = "ADD"
                alpha.use_clamp = True
                alpha.inputs[0].default_value = g["alpha"]
                nt.links.new(lw.outputs["Facing"], alpha.inputs[1])
                a2 = nt.nodes.new("ShaderNodeMath")
                a2.operation = "MULTIPLY"
                a2.use_clamp = True
                a2.inputs[1].default_value = 0.6
                nt.links.new(alpha.outputs[0], a2.inputs[0])
                nt.links.new(a2.outputs[0], bsdf.inputs["Alpha"])
                gm.surface_render_method = "BLENDED"
                gm.use_transparency_overlap = False
                done[m.name] = gm
            slot.material = done[m.name]


def clip_to_box(ch: RC.Character, inv: Matrix, size) -> None:
    """Hides what lies outside the frame's box (the client clips the subject
    to the canvas opening and the back): `inv` takes world points to the
    frame's local space (Blender axes: x right, -y out of the wall, z up)."""
    w, h = size
    done = {}
    for o in meshes(ch):
        for slot in o.material_slots:
            m = slot.material
            if m is None:
                continue
            if m.name not in done:
                cm = m.copy()
                cm.name = f"clip_{m.name}"
                nt = cm.node_tree
                bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
                geo = nt.nodes.new("ShaderNodeNewGeometry")
                vt = nt.nodes.new("ShaderNodeVectorTransform")  # world -> frame: a mapping by the inverse matrix
                vt.vector_type = "POINT"
                vt.convert_from = "WORLD"
                vt.convert_to = "WORLD"
                nt.links.new(geo.outputs["Position"], vt.inputs["Vector"])
                loc, rot, _ = inv.decompose()
                mp = nt.nodes.new("ShaderNodeMapping")
                mp.vector_type = "POINT"
                mp.inputs["Location"].default_value = loc
                mp.inputs["Rotation"].default_value = rot.to_euler()
                nt.links.new(vt.outputs["Vector"], mp.inputs["Vector"])
                sep = nt.nodes.new("ShaderNodeSeparateXYZ")
                nt.links.new(mp.outputs["Vector"], sep.inputs["Vector"])

                def band(sock, lo, hi):
                    a = nt.nodes.new("ShaderNodeMath")
                    a.operation = "GREATER_THAN"
                    a.inputs[1].default_value = lo
                    nt.links.new(sock, a.inputs[0])
                    b = nt.nodes.new("ShaderNodeMath")
                    b.operation = "LESS_THAN"
                    b.inputs[1].default_value = hi
                    nt.links.new(sock, b.inputs[0])
                    c = nt.nodes.new("ShaderNodeMath")
                    c.operation = "MULTIPLY"
                    nt.links.new(a.outputs[0], c.inputs[0])
                    nt.links.new(b.outputs[0], c.inputs[1])
                    return c.outputs[0]

                x = band(sep.outputs["X"], -w / 2, w / 2)
                z = band(sep.outputs["Z"], -h / 2, h / 2)
                y = band(sep.outputs["Y"], -FR.OPEN_Z, -FR.BACK_Z)
                m1 = nt.nodes.new("ShaderNodeMath")
                m1.operation = "MULTIPLY"
                nt.links.new(x, m1.inputs[0])
                nt.links.new(z, m1.inputs[1])
                m2 = nt.nodes.new("ShaderNodeMath")
                m2.operation = "MULTIPLY"
                nt.links.new(m1.outputs[0], m2.inputs[0])
                nt.links.new(y, m2.inputs[1])
                nt.links.new(m2.outputs[0], bsdf.inputs["Alpha"])
                cm.surface_render_method = "DITHERED"
                cm.use_transparent_shadow = True
                done[m.name] = cm
            slot.material = done[m.name]


def image(path: str):
    name = os.path.basename(path)
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.load(path)
    return img


def textured(name: str, img, shade_layer: str | None, value: float = 1.0):
    """An unlit-ish preview material: image * COLOR_0 (the castle shader's value)."""
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.55 if "trim" in name else 0.9
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    col = tex.outputs["Color"]
    if shade_layer:
        vc = nt.nodes.new("ShaderNodeVertexColor")
        vc.layer_name = shade_layer
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        nt.links.new(col, mix.inputs["A"])
        nt.links.new(vc.outputs["Color"], mix.inputs["B"])
        col = mix.outputs["Result"]
    nt.links.new(col, bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


# -- the portrait piece ------------------------------------------------------------------------


def import_frame(assets: str, placed: PL.Placed, man: dict) -> list:
    """The frame glb at the placement (LOD0 only), materials bound like the
    client binds them (castle_trim: the trim sheet; npc_backdrop: the
    placement's backdrop)."""
    path = os.path.join(assets, "npcs", man["frames"][placed.size_key])
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    objs = [o for o in bpy.data.objects if o not in before]
    trim = image(os.path.join(assets, "world", "textures", "trim_castle.png"))
    back = image(os.path.join(assets, "npcs", man["backdrops"][placed.backdrop]))
    root = bpy.data.objects.new(f"frame_{placed.npc}", None)
    bpy.context.scene.collection.objects.link(root)
    for o in objs:
        if o.type != "MESH":
            continue
        if o.name.endswith("_lod1") or o.get("kit_lod") is not None:
            o.hide_render = True
        layer = o.data.color_attributes[0].name if o.data.color_attributes else None
        if "castle_trim" in o.name:
            mat = textured("npc_trim_preview", trim, layer)
        else:
            mat = textured(f"npc_backdrop_preview_{placed.backdrop}", back, layer)
        o.data.materials.clear()
        o.data.materials.append(mat)
        if o.parent is None:
            o.parent = root
    root.matrix_world = frame_matrix(placed)
    return objs + [root]


def frame_matrix(placed: PL.Placed) -> Matrix:
    """World matrix (Blender axes) of the frame's origin: the canvas centre
    on the wall face, turned by the placement's yaw."""
    x, y, z = placed.pos
    return Matrix.Translation((x, -z, y)) @ Matrix.Rotation(placed.yaw, 4, "Z")


def stand_subject(ch: RC.Character, look: dict, placed: PL.Placed, turn: float = 0.0) -> Matrix:
    """The character inside the box, its eyes in the canvas's upper part,
    facing out of the wall; returns the world-to-frame matrix for clipping."""
    fm = frame_matrix(placed)
    hm = height_m(look)
    eye = hm - 0.11 * hm / 1.75
    w, h = placed.size
    z_eye = h * 0.22 if h >= w else h * 0.2
    local = Matrix.Translation((0.0, -FR.SUBJECT_Z, z_eye - eye)) @ Matrix.Rotation(math.radians(turn), 4, "Z")
    k = ch.arm.scale[0]
    ch.arm.matrix_world = fm @ local @ Matrix.Scale(k, 4)
    return fm.inverted()


# -- the steps -----------------------------------------------------------------------------------


def sheet(paths: list[str], out: str, cols: int) -> str:
    """A contact sheet, keeping the single stills."""
    from charlib import montage

    return montage.tile(paths, out, cols, remove=False)


def npc_ids(kind: str | None, only: set[str]) -> list[str]:
    return [n["id"] for n in ROSTER["npcs"] if (kind is None or n["kind"] == kind) and (not only or n["id"] in only)]


def kind_of(npc: str) -> str:
    return next(n["kind"] for n in ROSTER["npcs"] if n["id"] == npc)


def step_looks(cast: Cast, out: str, only: set[str]) -> None:
    d = os.path.join(out, "npcs")
    os.makedirs(d, exist_ok=True)
    sheets: dict[str, list] = {}
    for npc in npc_ids(None, only):
        look = LOOKS[npc]
        ch = cast.wear(look)
        ch.arm.matrix_world = Matrix.Identity(4)
        k = height_m(look) / CREATOR["body_types"][look["body_type"]]["default_height_m"]
        ch.arm.scale = (k, k, k)
        lift = 0.0
        if kind_of(npc) == "ghost":
            ghostify(ch)
            lift = load("data/npcs/roster.json")["rules"]["ghost_float_m"]
            ch.arm.location = (0, 0, lift)
        z = height_m(look) * 0.52 + lift
        for v in ("front", "three"):
            az, el = RC.VIEWS[v]
            RD.aim((0, 0, z), az, el, 3.4, lens=60)
            sheets.setdefault(kind_of(npc), []).append(RD.shoot(os.path.join(d, f"{npc}_{v}.png")))
        print(f"[npcs] look {npc}", flush=True)
    for kind, paths in sheets.items():
        cols = 8 if kind in ("portrait", "student") else 6
        sheet(paths, os.path.join(out, f"looks_{kind}.png"), cols)


def step_portraits(cast: Cast, assets: str, out: str, only: set[str], man: dict) -> None:
    import layout_lib as L

    d = os.path.join(out, "portraits")
    os.makedirs(d, exist_ok=True)
    layout = L.load_layout(os.path.join(REPO, "data", "world", "layout.json"))
    placed = {p.npc: p for p in PL.resolve(layout)}
    sc = bpy.context.scene
    floor = bpy.data.objects.get("floor")
    if floor:
        floor.hide_render = True
    paths = []
    depth = []
    for npc in npc_ids("portrait", only):
        p = placed[npc]
        # Each in the studio at the origin, facing -y (the frame's own space).
        pz = PL.Placed(p.npc, p.surface, p.edge, (0.0, 0.0, 0.0), 0.0, p.size, p.backdrop, p.level, p.cell, p.axis, p.c,
                       p.sign, p.t, p.floor)
        objs = import_frame(assets, pz, man)
        wall = add_wall(pz)
        look = LOOKS[npc]
        ch = cast.wear(look)
        inv = stand_subject(ch, look, pz, turn=8.0)
        clip_to_box(ch, inv, p.size)
        w, h = p.size
        size = max(w + 0.6, (h + 0.7) * 0.75)
        RD.aim((0, 0, 0.04), 0, 0, size * 2.2, lens=50)
        sc.render.resolution_x, sc.render.resolution_y = 480, 640
        paths.append(RD.shoot(os.path.join(d, f"{npc}.png")))
        if npc in ("portrait_gs_l1", "portrait_kitchens"):
            for az in (35, -35):
                RD.aim((0, 0, 0.0), az, 8, size * 2.0, lens=50)
                depth.append(RD.shoot(os.path.join(d, f"{npc}_az{az}.png")))
        for o in objs + [wall]:
            bpy.data.objects.remove(o)
        print(f"[npcs] portrait {npc}", flush=True)
    sheet(paths, os.path.join(out, "portraits_contact.png"), 6)
    if depth:
        sheet(depth, os.path.join(out, "portraits_depth.png"), 4)
    if floor:
        floor.hide_render = False


def add_wall(placed: PL.Placed):
    """A plain stone-coloured wall plane behind a studio portrait."""
    me = bpy.data.meshes.new("studio_wall")
    s = 3.0
    me.from_pydata([(-s, 0.0, -s), (s, 0.0, -s), (s, 0.0, s), (-s, 0.0, s)], [], [(0, 1, 2, 3)])
    o = bpy.data.objects.new("studio_wall", me)
    bpy.context.scene.collection.objects.link(o)
    m = bpy.data.materials.get("studio_wall") or bpy.data.materials.new("studio_wall")
    m.use_nodes = True
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*_lin("#B9AE9A"), 1.0)
    m.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.95
    me.materials.append(m)
    o.matrix_world = frame_matrix(placed)
    return o


def step_castle(cast: Cast, assets: str, out: str, only: set[str], man: dict, samples: int) -> None:
    """Every placement in the built castle (render_toon's castle loading and
    library binding, standard shading, the noon light)."""
    import layout_lib as L
    import render_toon as RT

    d = os.path.join(out, "castle")
    os.makedirs(d, exist_ok=True)
    layout = L.load_layout(os.path.join(REPO, "data", "world", "layout.json"))
    placed = [p for p in PL.resolve(layout) if not only or p.npc in only]
    keep = {o.name for o in bpy.data.objects}
    objs, lights, _ = RT.load_castle(assets)
    lighting = load("data/art/lighting.json")
    key = RT.light_key(lighting, 13.0)
    hemi = key["hemi"]
    RT.set_world(hemi["sky"], hemi["ground"], hemi["intensity"] * 1.6, key["sky"]["zenith"], key["sky"]["horizon"], True)
    for i, lt in enumerate(lights):
        RT.add_point(f"lt_{i}", lt["pos"], lt["color"], lt["intensity"], lt.get("range", 8.0))
    RT.bind_library(objs, assets, False, key)
    for name in ("key", "fill", "rim", "floor"):
        o = bpy.data.objects.get(name)
        if o is not None and name == "floor":
            o.hide_render = True
    sc = bpy.context.scene
    sc.eevee.taa_render_samples = samples
    paths = []
    for p in placed:
        fobjs = import_frame(assets, p, man)
        look = LOOKS[p.npc]
        ch = cast.wear(look)
        inv = stand_subject(ch, look, p, turn=8.0)
        clip_to_box(ch, inv, p.size)
        # The eye: 2.6 m out from the wall, 0.6 m along it, 1.65 m above the floor.
        nx, nz = math.sin(p.yaw), math.cos(p.yaw)  # into the room (world x, z)
        ox, oz = math.cos(p.yaw), -math.sin(p.yaw)  # along the wall
        ex = p.pos[0] + nx * 2.6 + ox * 0.6
        ez = p.pos[2] + nz * 2.6 + oz * 0.6
        eye = Vector((ex, -ez, p.floor + 1.65))
        target = Vector((p.pos[0], -p.pos[2], p.pos[1] - 0.15))
        cam = sc.camera
        cam.data.lens = 22
        cam.location = eye
        cam.rotation_euler = (target - eye).to_track_quat("-Z", "Y").to_euler()
        sc.render.resolution_x, sc.render.resolution_y = 960, 640
        paths.append(RD.shoot(os.path.join(d, f"{p.npc}.png")))
        for o in fobjs:
            bpy.data.objects.remove(o)
        print(f"[npcs] castle {p.npc}", flush=True)
    sheet(paths, os.path.join(out, "portraits_castle.png"), 3)
    del keep


def main() -> None:
    a = args()
    assets = os.path.abspath(a.assets)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    only = set(filter(None, a.npcs.split(",")))
    steps = a.only.split(",")
    with open(os.path.join(assets, "npcs", "manifest.json"), encoding="utf-8") as f:
        man = json.load(f)
    for step in steps:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        CA.build_image()
        RD.studio((480, 720))
        bpy.context.scene.eevee.taa_render_samples = a.samples
        cast = Cast(assets)
        if step == "looks":
            step_looks(cast, out, only)
        elif step == "portraits":
            step_portraits(cast, assets, out, only, man)
        elif step == "castle":
            step_castle(cast, assets, out, only, man, a.samples)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
