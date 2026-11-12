"""Review stills of the world for the anime look: the castle and the rooms as
the client loads them, rendered either with plain PBR shading (`standard`,
what the game drew before the toon shader) or with a toon preview that
follows the client's toon rendering (`toon`): two bands with the warm
per-material shadow tints of `data/art/toon_palette.json` (the client's ToonRamp rows), a thin rim,
outlines tinted by the local colour, a little bloom, lit by
`data/art/lighting.json` at a game hour.

    blender -b --factory-startup --python tools/blender/world/render_toon.py -- \\
        [--assets client/public/assets] [--out art/renders/toon] [--prefix after] \\
        [--shading toon|standard] [--hour 13] [--views entrance_hall,great_hall] [--views-file views.json]

Writes `<out>/<prefix>_<view>.png` (1280 x 800). The castle kit's views
(`KIT_VIEWS`: the kitted corridors, `gallery_*` pages of every piece) render
only when named; castle_* materials get the texture library's images
(`--kit-preview`: stand-in sheets where the library has none yet). The toon preview is an
approximation for judging palettes (EEVEE: Shader to RGB,
compositor outlines and bloom), not the client's shader. Lights are the ones the client
uses: the manifest's point lights, the sun or moon (no sun shadows indoors,
where the client has none either) and the hemisphere fill.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

import palette  # noqa: E402
from bpyutil import to_blender  # noqa: E402
from check_layout import script_args  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
LIGHT_WATTS = 25.0  # Blender watts per unit of a manifest light's intensity (README)

# name -> (kind, eye, target, lens mm, exterior?); castle views in world
# coordinates (after the castle kit's scale pass, M1.0), room views in room
# coordinates. `--views-file` renders another set (the pre-pass castle's
# views are tools/blender/scale_pass/views_before.json).
VIEWS = {
    "entrance_hall": ("castle", (16, 1.7, 39.0), (16, 6.0, 0), 16, False),
    "owl_exchange_court": ("castle", (14, 1.7, 30), (1, 3.0, 30), 18, False),
    "great_hall": ("castle", (36, 1.7, 20), (100, 4.0, 20), 18, False),
    "corridor": ("castle", (38.5, 10.7, -8), (-28, 10.5, -8), 18, False),
    "grand_staircase_up": ("castle", (-41, 1.6, 8), (-43.5, 60, 3.5), 12, False),
    "common_room": ("castle", (-40, 64.7, -31.5), (-59, 64.6, -42), 14, False),
    "common_room_b": ("castle", (65, -7.4, 28.5), (87, -7.6, 15), 14, False),
    "dueling_club": ("castle", (52, 2.6, 28), (70, 1.2, 19), 18, False),
    "grounds": ("castle", (52, -5.0, 236), (16, -1, 100), 24, True),
    "aerial": ("castle", (150, 100, 190), (5, 0, 25), 28, True),
    "dorm": ("room:dorm_house_a", None, None, 14, False),
    "charms_classroom": ("room:class_charms", (-1.5, 1.7, -6.75), (1.5, 1.6, 7.5), 14, False),
    "duel_arena": ("room:duel_arena", (-7.6, 2.4, 0.0), (8.0, 1.4, 0.0), 16, False),
}

# Castle kit M1 test bed (data/world/castle_kit.json): the two kitted
# corridors, M2's Entrance Hall views, M4's Grand Staircase views, and the piece gallery (castle_kit/pieces/gallery.py pages,
# kind "gallery:<page>", in gallery coordinates).
KIT_VIEWS = {
    "kit_gf_long": ("castle", (-3.4, 1.7, 8.5), (-28, 3.6, 8.5), 16, False),
    "kit_gf_north_wall": ("castle", (-25.5, 1.7, 12.2), (-11, 4.0, 4), 16, False),
    "kit_gf_eh_arch": ("castle", (-14, 1.7, 9.5), (-2, 3.4, 8.5), 18, False),
    "kit_gf_ceiling": ("castle", (-15, 1.6, 8.5), (-15.5, 8.4, 6.0), 12, False),
    "kit_ff_long": ("castle", (38.6, 10.7, -8.0), (-28, 11.6, -8.0), 16, False),
    "kit_ff_north_wall": ("castle", (24, 10.7, -3.5), (36, 12.5, -14), 16, False),
    "kit_ff_marble_arch": ("castle", (16, 10.7, -11.5), (16, 13.2, 0), 18, False),
    "kit_ff_west": ("castle", (-6, 10.7, -5), (-28, 12, -9), 16, False),
    "gallery_mouldings": ("gallery:mouldings", None, None, 24, False),
    "gallery_arches": ("gallery:arches", None, None, 24, False),
    "gallery_openings": ("gallery:openings", None, None, 24, False),
    "gallery_fixtures": ("gallery:fixtures", None, None, 24, False),
    "gallery_stairs": ("gallery:stairs", None, None, 24, False),
    # Castle kit M2: the Entrance Hall (the review framings) and its pieces.
    "eh_from_doors": ("castle", (16, 1.7, 39), (16, 8, 0), 16, False),
    "eh_exchange_court": ("castle", (14, 1.7, 30), (1, 3, 30), 18, False),
    "eh_stair_head": ("castle", (16, 10.7, 2), (16, 17, 12), 14, False),
    "gallery_hall": ("gallery:hall", None, None, 24, False),
    "gallery_vaults": ("gallery:vaults", None, None, 24, False),
    # Castle kit M3: the Great Hall (the review framings, and two more: the roof
    # from below and the doorway seen from the Entrance Hall) and its pieces.
    "gh_long": ("castle", (36, 1.7, 20), (94, 8, 20), 16, False),
    "gh_side_bay": ("castle", (50, 1.7, 26), (50, 10, 8), 16, False),
    "gh_dais": ("castle", (78, 1.7, 20), (93, 3, 20), 18, False),
    "gh_roof": ("castle", (60, 1.7, 24), (75, 22, 18), 14, False),
    "gh_from_eh": ("castle", (16, 1.7, 20), (60, 4.5, 20), 18, False),
    "gh_fireplace": ("castle", (66, 1.7, 22), (75, 5.0, 9), 16, False),
    # The House Cup hourglasses and the cup banners (--cup-fill, --cup-banners).
    "gh_hourglasses": ("castle", (88.75, 1.7, 16.5), (88.75, 2.4, 9.5), 22, False),
    "gh_cup_banners": ("castle", (36, 1.7, 20), (70, 9.5, 20), 16, False),
    "gh_cup_banners_side": ("castle", (52, 1.7, 22), (64, 12, 12), 16, False),
    "gallery_gh_roof": ("gallery:gh_roof", None, None, 24, False),
    "gallery_gh_beasts": ("gallery:gh_beasts", None, None, 24, False),
    "gallery_gh_openings": ("gallery:gh_openings", None, None, 24, False),
    "gallery_gh_furniture": ("gallery:gh_furniture", None, None, 24, False),
    # Castle kit M4: the Grand Staircase (the review framings; the bottom-up eye
    # moved 2.5 m west of x -48, which is inside the pier stack).
    "gs_bottom_up": ("castle", (-50.5, 1.6, 2), (-50, 60, 2), 12, False),
    "gs_l3_gallery": ("castle", (-63, 28.7, 18), (-40, 31, -10), 16, False),
    "gs_flight": ("castle", (-34, 19.7, 12), (-52, 22.5, -9.5), 16, False),
    "gs_under_flight": ("castle", (-40, 10.6, 13), (-46, 13.5, 2), 14, False),
    "gs_l7_guardian": ("castle", (-48.5, 64.7, -20.5), (-48.5, 65.2, -28), 18, False),
    "gallery_staircase": ("gallery:staircase", None, None, 24, False),
}


# Castle M5a (Corridors): docs/art/castle-kit.md (G2 stills: the undercroft long
# view, the gallery to the rose window, the L4 portraits, the L5 tapestries, the L6
# armour, the Charms door; the Grand Staircase look-up is gs_bottom_up) and a view
# down every other kitted corridor; renders in art/renders/kit/m5a/, --hour 13 and 23.
def m5a_corridor_views() -> dict:
    """The corridor views (castle coordinates, eye 1.7 m over the floor)."""
    return {
        "m5a_uc_long": ("castle", (30.5, 1.7, -6.5), (-28, 3.4, -9), 16, False),
        "m5a_uc_well": ("castle", (24, 1.7, -3.5), (12, 1.6, -10.5), 18, False),
        "m5a_uc_statues": ("castle", (-2, 1.7, -3.5), (-13, 2.4, -14), 16, False),
        "m5a_gallery_rose": ("castle", (43.5, 10.7, -9), (96, 13.4, -9), 16, False),
        "m5a_gallery_rose_near": ("castle", (82, 10.7, -7.5), (96, 13.4, -9), 16, False),
        "m5a_gallery_statue": ("castle", (58, 10.7, -6), (63, 11.4, -14), 18, False),
        "m5a_sf_long": ("castle", (-69, 19.7, 3.5), (-92, 21.2, 2.5), 16, False),
        "m5a_tf_charms_door": ("castle", (-79, 28.7, 6), (-80, 30.4, -2), 18, False),
        "m5a_tf_long": ("castle", (-69, 28.7, 4.5), (-92, 30, 1), 16, False),
        "m5a_l4_portraits": ("castle", (-69.5, 37.7, 6), (-81, 40.2, -2), 16, False),
        "m5a_l4_vestibule": ("castle", (-74, 37.7, 4), (-92, 39, -2.5), 16, False),
        "m5a_l5_tapestries": ("castle", (-69.5, 46.7, 6), (-81, 48.4, -2), 16, False),
        "m5a_l5_vestibule": ("castle", (-84.5, 46.7, 6), (-89, 48, -4), 16, False),
        "m5a_l6_armour": ("castle", (-69, 55.7, 2.5), (-92, 56.4, 2.5), 16, False),
    }


KIT_VIEWS.update(m5a_corridor_views())
VIEWS.update(KIT_VIEWS)

# Castle M5a (Tower): the west tower's spiral stair (docs/art/castle-kit.md): the
# spiral looking up from its foot, a climb on the third turn, the top landing and the
# knocker door, a slit window, and each N3 crossing seen from its corner landing.
TOWER_VIEWS = {
    "wt_bottom_up": ("castle", (-95.2, 28.5, -3.4), (-102.5, 41.0, 0.5), 12, False),
    "wt_climb": ("castle", (-104.0, 41.9, -1.2), (-103.6, 43.2, 7.0), 14, False),
    "wt_top_landing": ("castle", (-94.8, 55.8, -0.6), (-105.5, 55.0, -2.6), 14, False),
    "wt_knocker_door": ("castle", (-100.0, 55.5, -0.4), (-100.0, 56.1, -4.0), 16, False),
    "wt_slit": ("castle", (-103.2, 30.6, -0.6), (-106.0, 31.7, -2.0), 16, False),
    "wt_door_l4": ("castle", (-97.6, 37.6, -1.6), (-86.0, 37.8, -0.5), 16, False),
    "wt_door_l5": ("castle", (-97.6, 46.6, -1.6), (-86.0, 46.8, -0.5), 16, False),
    "wt_door_l6": ("castle", (-97.6, 55.6, -1.6), (-86.0, 55.8, -0.5), 16, False),
}
KIT_VIEWS.update(TOWER_VIEWS)
VIEWS.update(TOWER_VIEWS)


def lin(h: str):
    return palette.linear(h)


# ---------------------------------------------------------------------------
# Scene


def reset(shading: str, samples: int) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = samples
    if hasattr(scene.eevee, "use_raytracing"):
        scene.eevee.use_raytracing = False
    scene.render.image_settings.file_format = "PNG"
    # The toon look wants its flat colours as painted: no filmic roll-off.
    scene.view_settings.view_transform = "Standard" if shading == "toon" else "AgX"
    scene.view_settings.look = "None"


def light_key(lighting: dict, hour: float) -> dict:
    """The castle lighting at `hour`, interpolated between keys (wrapping)."""
    keys = sorted(lighting["castle"]["keys"], key=lambda k: k["hour"])
    h = hour % 24.0
    prev = max((k for k in keys if k["hour"] <= h), key=lambda k: k["hour"], default=keys[-1])
    nxt = min((k for k in keys if k["hour"] > h), key=lambda k: k["hour"], default=keys[0])
    span = (nxt["hour"] - prev["hour"]) % 24.0 or 24.0
    t = ((h - prev["hour"]) % 24.0) / span

    def mix(a, b):
        if isinstance(a, str):
            if not a.startswith("#"):
                return a
            ca, cb = palette.rgb(a), palette.rgb(b)
            return palette.hexc([x + (y - x) * t for x, y in zip(ca, cb)])
        if isinstance(a, (int, float)):
            return a + (b - a) * t
        if isinstance(a, list):  # directions: interpolated, renormalised
            v = [x + (y - x) * t for x, y in zip(a, b)]
            n = math.sqrt(sum(c * c for c in v)) or 1.0
            return [c / n for c in v]
        if isinstance(a, dict):
            return {k: mix(a[k], b.get(k, a[k])) for k in a}
        return a

    return mix(prev, nxt)


def set_world(sky_hex: str, ground_hex: str, strength: float, zenith: str, horizon: str, light_world: bool) -> None:
    """A sky/ground hemisphere fill; camera rays see the zenith-horizon
    gradient. With `light_world` False the world only shows as the sky (the
    toon materials add their own ambient)."""
    scene = bpy.context.scene
    world = bpy.data.worlds.new("world")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Incoming"], sep.inputs["Vector"])
    # Incoming points toward the viewer: up in the sky means -Z incoming.
    up = nt.nodes.new("ShaderNodeMath")
    up.operation = "MULTIPLY"
    up.inputs[1].default_value = -1.0
    nt.links.new(sep.outputs["Z"], up.inputs[0])
    t = nt.nodes.new("ShaderNodeMapRange")
    t.inputs["From Min"].default_value = -0.1
    t.inputs["From Max"].default_value = 0.8
    nt.links.new(up.outputs[0], t.inputs["Value"])

    def gradient(lo, hi):
        m = nt.nodes.new("ShaderNodeMix")
        m.data_type = "RGBA"
        m.inputs["A"].default_value = (*lin(lo), 1.0)
        m.inputs["B"].default_value = (*lin(hi), 1.0)
        nt.links.new(t.outputs["Result"], m.inputs["Factor"])
        return m

    cam_col = gradient(horizon, zenith)
    fill_col = gradient(ground_hex, sky_hex)
    cam = nt.nodes.new("ShaderNodeBackground")
    nt.links.new(cam_col.outputs["Result"], cam.inputs["Color"])
    cam.inputs["Strength"].default_value = 1.0
    fill = nt.nodes.new("ShaderNodeBackground")
    nt.links.new(fill_col.outputs["Result"], fill.inputs["Color"])
    fill.inputs["Strength"].default_value = strength if light_world else 0.0
    path = nt.nodes.new("ShaderNodeLightPath")
    mixs = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(path.outputs["Is Camera Ray"], mixs.inputs["Fac"])
    nt.links.new(fill.outputs["Background"], mixs.inputs[1])
    nt.links.new(cam.outputs["Background"], mixs.inputs[2])
    nt.links.new(mixs.outputs["Shader"], out.inputs["Surface"])


def add_sun(direction, color_hex: str, intensity: float, shadows: bool) -> None:
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = intensity * math.pi
    sun.color = lin(color_hex)
    sun.angle = math.radians(2)
    sun.use_shadow = shadows
    # The toon materials read the sun through a diffuse closure and the point
    # lights through a glossy one, so each light only feeds its own term.
    sun.specular_factor = 0.0
    obj = bpy.data.objects.new("sun", sun)
    d = Vector(to_blender(direction)).normalized()
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.collection.objects.link(obj)


def add_point(name: str, pos, color_hex: str, intensity: float, rng: float) -> None:
    light = bpy.data.lights.new(name, "POINT")
    light.energy = intensity * LIGHT_WATTS
    light.color = lin(color_hex)
    light.shadow_soft_size = 0.15
    light.use_shadow = False
    light.diffuse_factor = 0.0  # see add_sun: point lights feed the glossy term
    if hasattr(light, "use_custom_distance"):
        light.use_custom_distance = True
        light.cutoff_distance = rng
    obj = bpy.data.objects.new(name, light)
    obj.location = to_blender(pos)
    bpy.context.scene.collection.objects.link(obj)


def camera(eye, target, lens: float) -> None:
    cam = bpy.data.cameras.new("cam")
    cam.lens = lens
    cam.clip_start = 0.05
    cam.clip_end = 2000
    obj = bpy.data.objects.new("cam", cam)
    bpy.context.scene.collection.objects.link(obj)
    e, t = Vector(to_blender(eye)), Vector(to_blender(target))
    obj.location = e
    obj.rotation_euler = (t - e).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = obj


def import_glb(path: str) -> list:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in set(bpy.data.objects) - before if o.type == "MESH"]


# ---------------------------------------------------------------------------
# Toon preview materials


# The client's BASE_RAMP (client/src/game/toon/palette.ts): every row of
# data/art/toon_palette.json is a partial ramp over it.
# A copy of the client's BASE_RAMP (farStart / farEnd: the client's far fade, not previewed).
# Castle D1 (docs/art/castle-kit.md "Shading: the AO bake"): the world shade fields, neutral here
# (test_palette.py compares them with the client's).
BASE_RAMP = {"threshold": 0.02, "softness": 0.04, "shadowTint": [0.84, 0.74, 0.76], "shadowLight": 0.5, "rim": 0,
             "specular": 0, "saturation": 0.22, "outline": 1, "farStart": 0, "farEnd": 0,
             "shadeShift": 0, "aoFloor": 1, "skyLift": 0, "rimWidth": 0.3, "valueFloor": 1, "nearFade": [0, 0]}


def ramp_for(name: str, toon: dict) -> dict:
    """The client's lookup (`worldRamp`): the exact material name, then the
    longest key ending in `*` that prefixes it, then `default`; resolved over
    BASE_RAMP."""
    base = name.split(".")[0]
    mats = toon["materials"]
    row = mats.get(base)
    if row is None:
        keys = [k for k in mats if k.endswith("*") and base.startswith(k[:-1])]
        row = mats[max(keys, key=len)] if keys else {}
    out = {**BASE_RAMP, **toon.get("default", {}), **row}
    out["_rimSet"] = "rimWidth" in row  # castle D1: a row that sets rimWidth takes the characters' rim
    return out


def shade_path(ramp: dict) -> bool:
    """Castle D1: the world shade path (the client's TOON_WORLD_SHADE):
    COLOR_0 is the baked shade x, not an albedo multiplier."""
    return ramp.get("shadeShift", 0) > 0 or ramp.get("aoFloor", 1) < 1 or ramp.get("valueFloor", 1) < 1


def _albedo_without_vertex_multiply(nt, bsdf):
    """Castle D1: when Base Color comes through a colour Mix one of whose
    inputs is a vertex colour (the glTF importer's COLOR_0 multiply), the
    Mix's other colour input (a socket); None otherwise."""
    inp = bsdf.inputs["Base Color"]
    if not inp.is_linked:
        return None
    mix = inp.links[0].from_node
    if mix.type != "MIX":
        return None
    cols = [i for i in mix.inputs if i.type == "RGBA" and i.enabled]
    vert = [i for i in cols if i.is_linked and i.links[0].from_node.type in ("VERTEX_COLOR", "ATTRIBUTE")]
    if len(cols) != 2 or len(vert) != 1:
        return None
    other = cols[0] if cols[1] is vert[0] else cols[1]
    if other.is_linked:
        return other.links[0].from_socket
    rgb = nt.nodes.new("ShaderNodeRGB")
    rgb.outputs[0].default_value = other.default_value
    return rgb.outputs[0]


def toonify(mat, ramp: dict, vertex_layer: str | None, env: dict) -> None:
    """Rewires a glTF-imported material into the toon preview: whatever fed
    the Principled base colour (times the cell's COLOR_0 value ramp) is
    shaded in two bands."""
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if bsdf is None or out is None:
        return
    links = nt.links

    # Base colour.
    if bsdf.inputs["Base Color"].is_linked:
        base = bsdf.inputs["Base Color"].links[0].from_socket
    else:
        rgb = nt.nodes.new("ShaderNodeRGB")
        rgb.outputs[0].default_value = bsdf.inputs["Base Color"].default_value
        base = rgb.outputs[0]
    # Castle D1: on the shade path COLOR_0 is x (r = g = b, linear: the
    # glTF importer gives the stored byte / 255), read as shade, never a colour.
    # The importer wires COLOR_0 as a multiply in front of Base Color (left
    # dangling where bind_library put a library texture there instead): the
    # albedo is that multiply's other input, as the client's shade path reads it.
    shade_x = None
    if vertex_layer and shade_path(ramp):
        albedo = _albedo_without_vertex_multiply(nt, bsdf)
        if albedo is not None:
            base = albedo
        attr = nt.nodes.new("ShaderNodeVertexColor")
        attr.layer_name = vertex_layer
        sep_x = nt.nodes.new("ShaderNodeSeparateColor")
        links.new(attr.outputs["Color"], sep_x.inputs["Color"])
        shade_x = sep_x.outputs[0]
        vertex_layer = None

    def mix_x(floor: float):
        """mix(floor, 1, x) as a node output (x the baked shade)."""
        m = nt.nodes.new("ShaderNodeMapRange")
        m.inputs["From Min"].default_value = 0.0
        m.inputs["From Max"].default_value = 1.0
        m.inputs["To Min"].default_value = floor
        m.inputs["To Max"].default_value = 1.0
        links.new(shade_x, m.inputs["Value"])
        return m.outputs["Result"]

    def scale_rgb(col, factor):
        """col x a scalar node output."""
        m = nt.nodes.new("ShaderNodeVectorMath")
        m.operation = "SCALE"
        links.new(col, m.inputs[0])
        links.new(factor, m.inputs["Scale"])
        return m.outputs["Vector"]

    if vertex_layer and not any(n.type in ("VERTEX_COLOR", "ATTRIBUTE") for n in nt.nodes):
        # World cells: COLOR_0 is the painted value ramp (furniture's dye
        # mask is already wired by its dyed preview material).
        attr = nt.nodes.new("ShaderNodeVertexColor")
        attr.layer_name = vertex_layer
        mul = nt.nodes.new("ShaderNodeMix")
        mul.data_type = "RGBA"
        mul.blend_type = "MULTIPLY"
        mul.inputs["Factor"].default_value = 1.0
        links.new(base, mul.inputs["A"])
        links.new(attr.outputs["Color"], mul.inputs["B"])
        base = mul.outputs["Result"]

    # Emission (glTF emissive factor or texture, times strength).
    emission = None
    strength = bsdf.inputs["Emission Strength"].default_value
    ecol = bsdf.inputs["Emission Color"]
    if strength > 0 and (ecol.is_linked or max(ecol.default_value[:3]) > 0):
        em = nt.nodes.new("ShaderNodeMix")
        em.data_type = "RGBA"
        em.blend_type = "MULTIPLY"
        em.inputs["Factor"].default_value = 1.0
        if ecol.is_linked:
            links.new(ecol.links[0].from_socket, em.inputs["A"])
        else:
            em.inputs["A"].default_value = ecol.default_value
        em.inputs["B"].default_value = (strength, strength, strength, 1.0)
        emission = em.outputs["Result"]

    # Light reaching the surface: the sun or moon (diffuse closure) and the
    # point lights (a fully rough glossy closure; see add_sun). The key that
    # makes the two bands is the sun in the castle, the point lights in a room
    # (the client turns the sun off indoors); point lights also add softly.
    dif = nt.nodes.new("ShaderNodeBsdfDiffuse")
    pts = nt.nodes.new("ShaderNodeBsdfGlossy")
    pts.inputs["Roughness"].default_value = 1.0
    if bsdf.inputs["Normal"].is_linked:
        links.new(bsdf.inputs["Normal"].links[0].from_socket, dif.inputs["Normal"])
        links.new(bsdf.inputs["Normal"].links[0].from_socket, pts.inputs["Normal"])
    s2r_sun = nt.nodes.new("ShaderNodeShaderToRGB")
    links.new(dif.outputs["BSDF"], s2r_sun.inputs["Shader"])
    s2r_pts = nt.nodes.new("ShaderNodeShaderToRGB")
    links.new(pts.outputs["BSDF"], s2r_pts.inputs["Shader"])
    s2r = s2r_sun if env["key_source"] == "sun" else s2r_pts
    bw = nt.nodes.new("ShaderNodeRGBToBW")
    links.new(s2r.outputs["Color"], bw.inputs["Color"])
    norm = nt.nodes.new("ShaderNodeMath")
    norm.operation = "DIVIDE"
    links.new(bw.outputs["Val"], norm.inputs[0])
    norm.inputs[1].default_value = env["lref"]
    # Two bands: the step and softness of the ramp, on the light amount.
    lo = 0.03 + ramp["threshold"]
    lit = nt.nodes.new("ShaderNodeMapRange")
    lit.interpolation_type = "SMOOTHSTEP"
    lit.inputs["From Min"].default_value = lo
    lit.inputs["From Max"].default_value = lo + max(0.01, ramp["softness"] * 2.0 * env["soft_scale"])
    links.new(norm.outputs[0], lit.inputs["Value"])
    if shade_x is not None and ramp.get("shadeShift", 0) > 0:
        # t = threshold + (1 - x) shadeShift: occluded corners step into the tinted band.
        shift = nt.nodes.new("ShaderNodeMath")
        shift.operation = "MULTIPLY_ADD"
        inv = nt.nodes.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.inputs[0].default_value = 1.0
        links.new(shade_x, inv.inputs[1])
        links.new(inv.outputs[0], shift.inputs[0])
        shift.inputs[1].default_value = ramp["shadeShift"]
        shift.inputs[2].default_value = lo
        links.new(shift.outputs[0], lit.inputs["From Min"])
        hi = nt.nodes.new("ShaderNodeMath")
        hi.operation = "ADD"
        links.new(shift.outputs[0], hi.inputs[0])
        hi.inputs[1].default_value = max(0.01, ramp["softness"] * 2.0 * env["soft_scale"])
        links.new(hi.outputs[0], lit.inputs["From Max"])
    # Light hue: the light colour normalised to its brightness.
    hue = nt.nodes.new("ShaderNodeVectorMath")
    hue.operation = "DIVIDE"
    links.new(s2r.outputs["Color"], hue.inputs[0])
    mx = nt.nodes.new("ShaderNodeMath")
    mx.operation = "MAXIMUM"
    mx.inputs[1].default_value = 1e-3
    links.new(bw.outputs["Val"], mx.inputs[0])
    comb = nt.nodes.new("ShaderNodeCombineXYZ")
    for k in ("X", "Y", "Z"):
        links.new(mx.outputs[0], comb.inputs[k])
    links.new(comb.outputs["Vector"], hue.inputs[1])
    tint = nt.nodes.new("ShaderNodeMix")
    tint.data_type = "RGBA"
    tint.inputs["Factor"].default_value = 0.6
    tint.inputs["A"].default_value = (1, 1, 1, 1)
    links.new(hue.outputs["Vector"], tint.inputs["B"])
    lit_col = nt.nodes.new("ShaderNodeMix")
    lit_col.data_type = "RGBA"
    lit_col.blend_type = "MULTIPLY"
    lit_col.inputs["Factor"].default_value = 1.0
    links.new(base, lit_col.inputs["A"])
    links.new(tint.outputs["Result"], lit_col.inputs["B"])
    key = nt.nodes.new("ShaderNodeMix")
    key.data_type = "RGBA"
    key.blend_type = "MULTIPLY"
    key.inputs["Factor"].default_value = 1.0
    links.new(lit_col.outputs["Result"], key.inputs["A"])
    key.inputs["B"].default_value = (env["key"], env["key"], env["key"], 1.0)
    # Shadow side: the base colour times the material's warm shadow tint and
    # the hemisphere fill.
    sh = nt.nodes.new("ShaderNodeMix")
    sh.data_type = "RGBA"
    sh.blend_type = "MULTIPLY"
    sh.inputs["Factor"].default_value = 1.0
    links.new(base, sh.inputs["A"])
    st = [palette.to_linear(c) for c in ramp["shadowTint"]]  # the ramp's tint is an sRGB multiplier
    amb = env["ambient"]
    sh.inputs["B"].default_value = (st[0] * amb[0], st[1] * amb[1], st[2] * amb[2], 1.0)
    shadow = sh.outputs["Result"]
    if ramp.get("skyLift", 0) > 0:
        # Castle D1: fill x max(0, 1 + skyLift n.y): undersides take less sky (the
        # preview's fill is the sky's colour alone: no brown ground bounce).
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        sepn = nt.nodes.new("ShaderNodeSeparateXYZ")
        links.new(geo.outputs["Normal"], sepn.inputs["Vector"])
        lift = nt.nodes.new("ShaderNodeMath")
        lift.operation = "MULTIPLY_ADD"
        lift.use_clamp = False
        links.new(sepn.outputs["Z"], lift.inputs[0])  # Blender's Z is the world's up
        lift.inputs[1].default_value = ramp["skyLift"]
        lift.inputs[2].default_value = 1.0
        pos = nt.nodes.new("ShaderNodeMath")
        pos.operation = "MAXIMUM"
        pos.inputs[1].default_value = 0.0
        links.new(lift.outputs[0], pos.inputs[0])
        shadow = scale_rgb(shadow, pos.outputs[0])
    if shade_x is not None and ramp.get("aoFloor", 1) < 1:
        shadow = scale_rgb(shadow, mix_x(ramp["aoFloor"]))  # ambient x mix(aoFloor, 1, x)
    band = nt.nodes.new("ShaderNodeMix")
    band.data_type = "RGBA"
    links.new(lit.outputs["Result"], band.inputs["Factor"])
    links.new(shadow, band.inputs["A"])
    links.new(key.outputs["Result"], band.inputs["B"])
    col = band.outputs["Result"]
    # Point lights add a soft, unstepped glow on top (torch pools).
    glow = nt.nodes.new("ShaderNodeMix")
    glow.data_type = "RGBA"
    glow.blend_type = "MULTIPLY"
    glow.inputs["Factor"].default_value = 1.0
    links.new(base, glow.inputs["A"])
    links.new(s2r_pts.outputs["Color"], glow.inputs["B"])
    pool = glow.outputs["Result"]
    if shade_x is not None and ramp.get("aoFloor", 1) < 1:
        pool = scale_rgb(pool, mix_x(ramp["aoFloor"]))  # local lights likewise (torch pools)
    addg = nt.nodes.new("ShaderNodeMix")
    addg.data_type = "RGBA"
    addg.blend_type = "ADD"
    addg.inputs["Factor"].default_value = env["point_add"]
    links.new(col, addg.inputs["A"])
    links.new(pool, addg.inputs["B"])
    col = addg.outputs["Result"]
    if shade_x is not None and ramp.get("valueFloor", 1) < 1:
        col = scale_rgb(col, mix_x(ramp["valueFloor"]))  # colour x mix(valueFloor, 1, x)
    # Rim: a thin bright edge at grazing angles, mostly on the shadow side.
    if ramp["rim"] > 0:
        lw = nt.nodes.new("ShaderNodeLayerWeight")
        lw.inputs["Blend"].default_value = 0.5
        edge = nt.nodes.new("ShaderNodeMapRange")
        edge.interpolation_type = "SMOOTHSTEP"
        edge.inputs["From Min"].default_value = 0.72
        edge.inputs["From Max"].default_value = 0.8
        if ramp.get("_rimSet"):
            # Castle D1: a row that sets rimWidth takes the characters' rim width.
            edge.inputs["From Min"].default_value = 1.0 - ramp["rimWidth"]
            edge.inputs["From Max"].default_value = 1.0
        links.new(lw.outputs["Facing"], edge.inputs["Value"])
        side = nt.nodes.new("ShaderNodeMath")
        side.operation = "MULTIPLY_ADD"
        links.new(lit.outputs["Result"], side.inputs[0])
        side.inputs[1].default_value = -0.5
        side.inputs[2].default_value = 1.0
        amt = nt.nodes.new("ShaderNodeMath")
        amt.operation = "MULTIPLY"
        links.new(edge.outputs["Result"], amt.inputs[0])
        links.new(side.outputs[0], amt.inputs[1])
        amt2 = nt.nodes.new("ShaderNodeMath")
        amt2.operation = "MULTIPLY"
        links.new(amt.outputs[0], amt2.inputs[0])
        amt2.inputs[1].default_value = ramp["rim"] * env["rim_scale"]
        rim = nt.nodes.new("ShaderNodeMix")
        rim.data_type = "RGBA"
        rim.blend_type = "ADD"
        links.new(amt2.outputs[0], rim.inputs["Factor"])
        links.new(col, rim.inputs["A"])
        rim.inputs["B"].default_value = (*env["rim_colour"], 1.0)
        col = rim.outputs["Result"]
    if emission is not None:
        add = nt.nodes.new("ShaderNodeMix")
        add.data_type = "RGBA"
        add.blend_type = "ADD"
        add.inputs["Factor"].default_value = 1.0
        links.new(col, add.inputs["A"])
        links.new(emission, add.inputs["B"])
        col = add.outputs["Result"]
    shader = nt.nodes.new("ShaderNodeEmission")
    links.new(col, shader.inputs["Color"])
    # A black diffuse closure so EEVEE still writes the normal pass the
    # outline reads.
    black = nt.nodes.new("ShaderNodeBsdfDiffuse")
    black.inputs["Color"].default_value = (0, 0, 0, 1)
    if bsdf.inputs["Normal"].is_linked:
        links.new(bsdf.inputs["Normal"].links[0].from_socket, black.inputs["Normal"])
    both = nt.nodes.new("ShaderNodeAddShader")
    links.new(shader.outputs["Emission"], both.inputs[0])
    links.new(black.outputs["BSDF"], both.inputs[1])
    links.new(both.outputs["Shader"], out.inputs["Surface"])


def setup_post(outline: float, bloom_threshold: float, bloom_strength: float) -> None:
    """The screen-space passes the client runs on the world: outlines from
    depth and normal discontinuities (Sobel on the normal pass and on depth
    relative to distance), drawn as a darker, more saturated version of the
    local colour (never black), then a little bloom on the brightest pixels
    (emissive windows, flames). `outline` scales the line strength (0 = off)."""
    scene = bpy.context.scene
    vl = scene.view_layers[0]
    vl.use_pass_normal = True
    vl.use_pass_z = True
    tree = bpy.data.node_groups.new("toon_post", "CompositorNodeTree")
    scene.compositing_node_group = tree
    nodes, links = tree.nodes, tree.links
    rl = nodes.new("CompositorNodeRLayers")
    image = rl.outputs["Image"]
    if outline > 0:
        nsob = nodes.new("CompositorNodeFilter")
        nsob.inputs["Type"].default_value = "Sobel"
        links.new(rl.outputs["Normal"], nsob.inputs["Image"])
        nlen = nodes.new("ShaderNodeVectorMath")
        nlen.operation = "LENGTH"
        links.new(nsob.outputs["Image"], nlen.inputs[0])
        nedge = nodes.new("ShaderNodeMapRange")
        nedge.inputs["From Min"].default_value = 0.9
        nedge.inputs["From Max"].default_value = 1.6
        links.new(nlen.outputs["Value"], nedge.inputs["Value"])
        dsob = nodes.new("CompositorNodeFilter")
        dsob.inputs["Type"].default_value = "Sobel"
        links.new(rl.outputs["Depth"], dsob.inputs["Image"])
        dsep = nodes.new("ShaderNodeSeparateXYZ")
        links.new(dsob.outputs["Image"], dsep.inputs["Vector"])
        drel = nodes.new("ShaderNodeMath")
        drel.operation = "DIVIDE"
        links.new(dsep.outputs["X"], drel.inputs[0])
        links.new(rl.outputs["Depth"], drel.inputs[1])
        dedge = nodes.new("ShaderNodeMapRange")
        dedge.inputs["From Min"].default_value = 0.08
        dedge.inputs["From Max"].default_value = 0.2
        links.new(drel.outputs["Value"], dedge.inputs["Value"])
        edge = nodes.new("ShaderNodeMath")
        edge.operation = "MAXIMUM"
        links.new(nedge.outputs["Result"], edge.inputs[0])
        links.new(dedge.outputs["Result"], edge.inputs[1])
        amount = nodes.new("ShaderNodeMath")
        amount.operation = "MULTIPLY"
        amount.use_clamp = True
        links.new(edge.outputs["Value"], amount.inputs[0])
        amount.inputs[1].default_value = outline
        ink = nodes.new("CompositorNodeHueSat")
        links.new(image, ink.inputs["Image"])
        ink.inputs["Saturation"].default_value = 1.4
        ink.inputs["Value"].default_value = 0.4
        lines = nodes.new("ShaderNodeMix")
        lines.data_type = "RGBA"
        links.new(amount.outputs["Value"], lines.inputs["Factor"])
        links.new(image, lines.inputs["A"])
        links.new(ink.outputs["Image"], lines.inputs["B"])
        image = lines.outputs["Result"]
    glare = nodes.new("CompositorNodeGlare")
    glare.inputs["Type"].default_value = "Bloom"
    glare.inputs["Threshold"].default_value = bloom_threshold
    glare.inputs["Strength"].default_value = bloom_strength
    links.new(image, glare.inputs["Image"])
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    out = nodes.new("NodeGroupOutput")
    links.new(glare.outputs["Image"], out.inputs[0])


# ---------------------------------------------------------------------------
# Views


def bind_library(objs, assets: str, preview: bool, key: dict | None) -> None:
    """Binds the castle kit's library textures to the castle_* materials the
    cells carry as placeholders (the client does this by name):
    `world/textures/textures.json` when it lists the material, the shared
    props atlas painted from its slot table when the library lacks it, and
    with `preview` the stand-in sheets of castle_kit/pieces/preview.py for
    the rest; otherwise the placeholder's flat colour stays. castle_glass
    glows by its own image, scaled by the hour's sky (tint "sky")."""
    import numpy as np

    from castle_kit import schema as kit_schema
    from castle_kit.pieces import preview as kit_preview

    lib_path = os.path.join(assets, "world", "textures", "textures.json")
    lib = {}
    if os.path.exists(lib_path):
        with open(lib_path, encoding="utf-8") as f:
            lib = json.load(f).get("materials", {})
    spec = kit_schema.load_texture_set()
    cache: dict = {}

    def image(name: str, arr_top_first):
        arr = np.ascontiguousarray(arr_top_first[::-1], dtype=np.float32)
        h, w, _ = arr.shape
        img = bpy.data.images.new(name, w, h, alpha=False)
        img.pixels.foreach_set(arr.ravel())
        img.pack()
        return img

    def library_image(mat_name: str):
        if mat_name in cache:
            return cache[mat_name]
        out = None
        entry = lib.get(mat_name)
        if entry and os.path.exists(os.path.join(assets, "world", "textures", entry["base"])):
            base = bpy.data.images.load(os.path.join(assets, "world", "textures", entry["base"]))
            em = entry.get("emissive")
            emi = bpy.data.images.load(os.path.join(assets, "world", "textures", em)) if em else None
            out = (base, emi, entry.get("emissive_strength", 1.0))
        elif mat_name == "castle_props":
            from castle_kit import props_atlas

            _, atlas = props_atlas.build()
            b, g = atlas.paint()  # rows bottom to top
            out = (image("props_atlas", b[::-1]), image("props_atlas_e", g[::-1]), 2.5)
        elif preview:
            arr = kit_preview.texture(mat_name, spec)
            if arr is not None:
                img = image(f"preview_{mat_name}", arr)
                m = spec["materials"][mat_name]
                out = (img, img if m.get("emissive") == "self" else None, m.get("emissive_strength", 1.0))
        cache[mat_name] = out
        return out

    sky = 1.0
    if key is not None:
        hz = lin(key["sky"]["horizon"])
        sky = min(1.0, (0.2126 * hz[0] + 0.7152 * hz[1] + 0.0722 * hz[2]) / 0.75)
    done = set()
    for o in objs:
        for slot in o.material_slots:
            mat = slot.material
            if mat is None or mat.name in done or not mat.name.split(".")[0].startswith("castle_"):
                continue
            done.add(mat.name)
            if mat.name.split(".")[0] == "castle_sky":
                # Castle kit M3: the enchanted ceiling (unlit, its colours from the lighting data).
                got = library_image("castle_sky")
                if got is not None and key is not None:
                    bind_sky(mat, got[0], key)
                continue
            got = library_image(mat.name.split(".")[0])
            if got is None:
                continue
            base, emi, strength = got
            nt = mat.node_tree
            bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = base
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            if emi is not None:
                etex = nt.nodes.new("ShaderNodeTexImage")
                etex.image = emi
                nt.links.new(etex.outputs["Color"], bsdf.inputs["Emission Color"])
                tint = sky if spec["materials"].get(mat.name.split(".")[0], {}).get("tint") == "sky" else 1.0
                bsdf.inputs["Emission Strength"].default_value = strength * tint


def bind_sky(mat, image, key: dict) -> None:
    """castle_sky (castle kit M3, docs/art/castle-kit.md), as the client
    draws it: unlit; the panel's v runs from the hour's horizon colour (the
    wall plate) to its zenith (the ridge); the clouds (R) lift toward the
    sun's colour by day and stay dark (luminance at most 0.35) at night;
    the stars (A) show by night where there is no cloud. Marked `kit_sky`
    so the toon pass leaves it alone."""
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    image.colorspace_settings.name = "Non-Color"
    image.alpha_mode = "CHANNEL_PACKED"
    uv = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uv.outputs["UV"], sep.inputs["Vector"])
    nt.links.new(uv.outputs["UV"], tex.inputs["Vector"])
    hz, zn = lin(key["sky"]["horizon"]), lin(key["sky"]["zenith"])
    sun = lin(key["sun"]["color"])
    night = 1.0 if key["sun"].get("body") == "moon" else 0.0
    grad = nt.nodes.new("ShaderNodeMix")
    grad.data_type = "RGBA"
    grad.inputs["A"].default_value = (*hz, 1.0)
    grad.inputs["B"].default_value = (*zn, 1.0)
    nt.links.new(sep.outputs["Y"], grad.inputs["Factor"])
    if night:
        lum = 0.2126 * hz[0] + 0.7152 * hz[1] + 0.0722 * hz[2]
        k = min(1.0, 0.3 / max(lum * 1.4 + 0.02, 1e-3))
        cloud = [min(1.0, (c * 1.4 + 0.02) * k) * 0.8 + s * 0.05 for c, s in zip(hz, sun)]
    else:
        cloud = [min(1.0, 0.55 + 0.45 * s) for s in sun]
    cmix = nt.nodes.new("ShaderNodeMix")
    cmix.data_type = "RGBA"
    nt.links.new(grad.outputs["Result"], cmix.inputs["A"])
    cmix.inputs["B"].default_value = (*cloud, 1.0)
    nt.links.new(tex.outputs["Color"], cmix.inputs["Factor"])
    # Stars: alpha x (1 - cloud) x night, added.
    inv = nt.nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    sepc = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(tex.outputs["Color"], sepc.inputs["Color"])
    nt.links.new(sepc.outputs[0], inv.inputs[1])
    st = nt.nodes.new("ShaderNodeMath")
    st.operation = "MULTIPLY"
    nt.links.new(tex.outputs["Alpha"], st.inputs[0])
    nt.links.new(inv.outputs[0], st.inputs[1])
    st2 = nt.nodes.new("ShaderNodeMath")
    st2.operation = "MULTIPLY"
    st2.inputs[1].default_value = 0.9 * night
    nt.links.new(st.outputs[0], st2.inputs[0])
    add = nt.nodes.new("ShaderNodeMix")
    add.data_type = "RGBA"
    add.blend_type = "ADD"
    add.inputs["Factor"].default_value = 1.0
    nt.links.new(cmix.outputs["Result"], add.inputs["A"])
    nt.links.new(st2.outputs[0], add.inputs["B"])
    nt.links.new(add.outputs["Result"], em.inputs["Color"])
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    mat["kit_sky"] = True


def load_castle(assets: str):
    world_dir = os.path.join(assets, "world")
    with open(os.path.join(world_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    objs = []
    for cell in manifest["cells"]:
        objs += import_glb(os.path.join(world_dir, cell["file"]))
    # Castle kit nodes the client never draws as they are: camera colliders
    # (extras.role) and LOD1 meshes (shown only beyond their distance).
    keep = []
    for o in objs:
        if o.get("role") == "camera_collider" or o.get("kit_lod") is not None or o.name.endswith(".collider"):
            o.hide_render = True
            continue
        keep.append(o)
    house_cup(keep)
    lights = [lt for cell in manifest["cells"] for lt in cell.get("lights", [])]
    return keep, lights, None


# The Great Hall's House Cup nodes as the client shows them: each
# hourglass sand node scaled by its fill (`--cup-fill`, house_a..d), the cup banner
# groups hidden but for the houses in `--cup-banners` (the client's `badge`).
CUP = {"fill": {"house_a": 0.55, "house_b": 0.35, "house_c": 1.0, "house_d": 0.45}, "banners": ()}


def house_cup(objs) -> None:
    for o in objs:
        anim = o.get("kit_anim")
        if anim is None:
            continue
        kind, house = anim.get("kind"), anim.get("house")
        if kind == "fill":
            f = CUP["fill"].get(house, 1.0)
            o.scale[2] = max(f, 1e-4)  # glTF +Y is Blender +Z
            o.hide_render = f <= 0.0
        elif kind == "cup_banners":
            o.hide_render = house not in CUP["banners"]


def load_room(assets: str, cell: str, furnish: bool):
    with open(os.path.join(assets, "world", "instances.json"), encoding="utf-8") as f:
        inst = json.load(f)
    entry = next(v for v in inst.values() if any(c["id"] == cell for c in v["cells"]))
    objs = []
    for c in entry["cells"]:
        objs += import_glb(os.path.join(assets, "world", c["file"]))
    if furnish and cell.startswith("dorm_house_"):
        import build_dorms
        import dorms

        shell = dorms.build_all()[cell[len("dorm_"):]]
        before = set(bpy.data.objects)
        build_dorms.furnish(shell, os.path.join(assets, "furniture"))
        objs += [o for o in set(bpy.data.objects) - before if o.type == "MESH"]
    return objs, entry.get("lights", []), entry


def render_view(name: str, args, toon: dict, lighting: dict, views: dict | None = None) -> None:
    kind, eye, target, lens, exterior = (views or VIEWS)[name]
    reset(args.shading, args.samples)
    if kind == "castle":
        objs, lights, entry = load_castle(args.assets)
    elif kind.startswith("gallery:"):
        from castle_kit.pieces import gallery

        objs, lights, (eye, target, lens) = gallery.build_page(kind.split(":", 1)[1])
        entry = None
        kind = "castle"
    else:
        objs, lights, entry = load_room(args.assets, kind.split(":", 1)[1], furnish=True)
    if eye is None:  # the dorm: from the door toward the far wall
        lo, hi = entry["cells"][0]["bounds"]["min"], entry["cells"][0]["bounds"]["max"]
        eye, target = (0.0, 1.62, hi[2] - 0.7), (0.0, 1.5, lo[2] + 0.3)
    toon_mode = args.shading == "toon"
    if kind == "castle":
        key = light_key(lighting, args.hour)
        sun, hemi, sky = key["sun"], key["hemi"], key["sky"]
        set_world(hemi["sky"], hemi["ground"], hemi["intensity"], sky["zenith"], sky["horizon"], not toon_mode)
        add_sun(sun["direction"], sun["color"], sun["intensity"], shadows=exterior)
        # The fill follows the hemisphere's colour and brightness (night
        # shadows go deep blue), the lit side the sun or moon's intensity.
        level = hemi["intensity"] / 0.62
        ambient = [min(1.0, (0.5 + 0.5 * c / 0.85) * level) for c in lin(hemi["sky"])]
        env = {"lref": sun["intensity"] * 0.85, "key": 0.3 + 0.7 * sun["intensity"],
               "ambient": ambient, "key_source": "sun", "soft_scale": 1.0, "point_add": 0.8}
    else:
        amb = entry.get("ambient", {"sky": "#808890", "ground": "#40403C", "intensity": 0.4})
        set_world(amb["sky"], amb["ground"], amb["intensity"], amb["sky"], amb["ground"], not toon_mode)
        sky_rgb = palette.rgb(amb["sky"])
        top = max(sky_rgb)
        level = 0.75 + 0.35 * min(1.0, amb["intensity"])
        env = {"lref": 0.22, "key": 1.0, "ambient": [(0.6 + 0.4 * (c / top)) * level for c in sky_rgb],
               "key_source": "points", "soft_scale": 2.5, "point_add": 0.25}
    rim = lighting.get("rim", {}).get("color", "#FFF2DE")
    env["rim_colour"] = lin(rim)
    env["rim_scale"] = 0.6
    for i, lt in enumerate(lights):
        add_point(f"lt_{i}", lt["pos"], lt["color"], lt["intensity"], lt.get("range", 8.0))
    bind_library(objs, args.assets, args.kit_preview, light_key(lighting, args.hour) if kind == "castle" else None)
    if toon_mode:
        cache = {}
        for o in objs:
            layer = o.data.color_attributes[0].name if o.data.color_attributes else None
            for slot in o.material_slots:
                orig = slot.material
                if orig is None or orig.get("kit_sky"):
                    continue  # (castle kit M3: the sky stays unlit)
                k = (orig.name, layer)
                if k not in cache:
                    mat = orig.copy()
                    toonify(mat, ramp_for(orig.name, toon), layer, env)
                    cache[k] = mat
                slot.material = cache[k]
        setup_post(args.line, 0.9, 0.25)
    camera(eye, target, lens)
    path = os.path.join(args.out, f"{args.prefix}_{name}.png")
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = 1280, 800
    scene.render.resolution_percentage = 100
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print(f"rendered {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Toon review stills of the castle and rooms")
    ap.add_argument("--assets", default=os.path.join(REPO, "client", "public", "assets"))
    ap.add_argument("--out", default=os.path.join(REPO, "art", "renders", "toon"))
    ap.add_argument("--prefix", default="after")
    ap.add_argument("--shading", default="toon", choices=("toon", "standard"))
    ap.add_argument("--hour", type=float, default=13.0)
    ap.add_argument("--views", default=None)
    ap.add_argument("--views-file", default=None,
                    help="JSON {name: [kind, eye, target, lens, exterior]} rendered instead of VIEWS")
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--line", type=float, default=1.0, help="outline strength (0 turns the outline pass off)")
    ap.add_argument("--kit-preview", action="store_true",
                    help="castle kit: stand-in sheets for castle_* materials the texture library lacks")
    ap.add_argument("--palette", default=os.path.join(REPO, "data", "art", "toon_palette.json"))
    ap.add_argument("--lighting", default=os.path.join(REPO, "data", "art", "lighting.json"))
    ap.add_argument("--cup-fill", default=None, help="hourglass fills house_a..d, e.g. 0.6,0.3,1,0.45")
    ap.add_argument("--cup-banners", default="", help="houses whose cup banners hang, e.g. house_c")
    args = ap.parse_args(script_args(sys.argv))
    if args.cup_fill:
        CUP["fill"] = {f"house_{x}": float(v) for x, v in zip("abcd", args.cup_fill.split(","))}
    CUP["banners"] = tuple(h for h in args.cup_banners.split(",") if h)
    with open(args.palette, encoding="utf-8") as f:
        toon = json.load(f)
    with open(args.lighting, encoding="utf-8") as f:
        lighting = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    views = VIEWS
    if args.views_file:
        with open(args.views_file, encoding="utf-8") as f:
            views = {k: tuple(v) for k, v in json.load(f).items()}
    default = [v for v in views if v not in KIT_VIEWS] if views is VIEWS else list(views)
    for name in (args.views.split(",") if args.views else default):
        render_view(name, args, toon, lighting, views)


if __name__ == "__main__":
    main()
