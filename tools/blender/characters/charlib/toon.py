"""The shared cel-shaded review setup (every review and comparison render).

Art direction (docs/art/style-guide.md): a Genshin Impact-like anime look.
Review renders all use this one Blender setup so sheets compare fairly:

* EEVEE, Standard view transform, flat background, no ambient light and no
  self-shadowing on characters (the key light casts no shadows);
* material: the client's tint formula on the atlas (`render.tint_material`'s
  maths: `albedo = atlas * ((1 - r - g - b) + r*C0 + g*C1 + b*C2)`); the
  light goes through **Shader to RGB** into a 2-band ramp with a soft but
  narrow step (`STEP`), and the shadow band is a **warm tint of the albedo**
  (`shadow_tint`, pinkish orange on skin, never grey);
* a thin **rim light** on the shadow side (facing-ratio band, albedo-tinted);
* an **inverted-hull outline** (Solidify after the Armature modifier, flipped
  normals; the hull follows the averaged vertex normals so it never splits):
  `OUTLINE_M` thick (thinner on the face), coloured a darker, saturated
  version of the local material colour rather than black.

The in-game export format is unchanged; this is only how reviews look until
the client's toon shader exists.

    from charlib import toon
    toon.studio((640, 720))
    mat = toon.material("skin_body_a", "#C68E6E", "#3B2A20", shadow_tint=toon.SKIN_SHADOW)
    toon.assign(obj, mat)          # material + outline
"""

from __future__ import annotations

import math

import bpy

from . import atlas as A

OUTLINE_M = 0.0012  # inverted-hull thickness, metres (1-1.5 mm)
OUTLINE_FACE_M = 0.0004  # on the head region (thinner on the face)
OUTLINE_DARK = 0.55  # outline colour = albedo ** OUTLINE_POW * OUTLINE_DARK
OUTLINE_POW = 1.8
OUTLINE_COLOUR = (0.045, 0.035, 0.035)  # untextured fallback
THRESHOLD = 0.19  # Shader-to-RGB light value where lit begins
STEP = 0.05  # width of the soft lit/shadow transition
SHADOW_TINT = (0.80, 0.68, 0.74)  # cloth and hair: warm, slightly violet
SKIN_SHADOW = (0.97, 0.79, 0.74)  # skin: pinkish orange
HAIR_SHADOW = (0.74, 0.62, 0.70)
RIM_STRENGTH = 0.20
NORMAL_ATTR = "toon_normal"


def keep_file_normals(obj: bpy.types.Object) -> None:
    """Store the mesh's own corner normals (the exported custom normals: the
    face's ellipsoid normals) in an attribute the cel material shades with.
    Rest-pose normals: under strong head rotations they lag the pose."""
    me = obj.data
    if not getattr(me, "has_custom_normals", False) or NORMAL_ATTR in me.attributes:
        return
    import numpy as np

    n = np.zeros(len(me.loops) * 3, dtype=np.float32)
    me.corner_normals.foreach_get("vector", n)
    a = me.attributes.new(NORMAL_ATTR, "FLOAT_VECTOR", "CORNER")
    a.data.foreach_set("vector", n)
BANDS = ((0.0, 0.64), (0.2, 1.0))  # kept for older callers (unused)


def studio(resolution=(640, 960), background=(0.62, 0.66, 0.72)) -> bpy.types.Scene:
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = resolution
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    try:
        sc.eevee.taa_render_samples = 16
    except AttributeError:
        pass
    world = bpy.data.worlds.get("toon") or bpy.data.worlds.new("toon")
    sc.world = world
    if world.node_tree is None:
        world.use_nodes = True
    # The camera sees the flat background; nothing is lit by it.
    wn = world.node_tree.nodes
    wn.clear()
    wout = wn.new("ShaderNodeOutputWorld")
    seen = wn.new("ShaderNodeBackground")
    seen.inputs["Color"].default_value = (*background, 1.0)
    dark = wn.new("ShaderNodeBackground")
    dark.inputs["Strength"].default_value = 0.0
    path = wn.new("ShaderNodeLightPath")
    mix = wn.new("ShaderNodeMixShader")
    world.node_tree.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    world.node_tree.links.new(dark.outputs[0], mix.inputs[1])
    world.node_tree.links.new(seen.outputs[0], mix.inputs[2])
    world.node_tree.links.new(mix.outputs[0], wout.inputs["Surface"])
    # One key light from the front left, above; no shadows (characters are
    # not self-shadowed). The rim is done in the material.
    for name, rot, energy in (("toon_key", (48, 0, -16), 3.0),):
        if bpy.data.objects.get(name):
            continue
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        light.angle = math.radians(2)
        try:
            light.use_shadow = False
        except AttributeError:
            pass
        o = bpy.data.objects.new(name, light)
        o.rotation_euler = tuple(math.radians(a) for a in rot)
        sc.collection.objects.link(o)
    if not bpy.data.objects.get("floor"):
        bpy.ops.mesh.primitive_plane_add(size=12.0)
        floor = bpy.context.active_object
        floor.name = "floor"
        m = bpy.data.materials.new("toon_floor")
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (*[c * 0.85 for c in background], 1.0)
        nt.links.new(em.outputs[0], out.inputs["Surface"])
        floor.data.materials.append(m)
    if not bpy.data.objects.get("camera"):
        cam = bpy.data.cameras.new("camera")
        o = bpy.data.objects.new("camera", cam)
        sc.collection.objects.link(o)
        sc.camera = o
    return sc


def _lin(c):
    return A.hex_to_linear(c) if isinstance(c, str) else tuple(c)


class _Graph:
    def __init__(self, mat):
        self.nt = mat.node_tree
        self.L = self.nt.links

    def node(self, kind, **inputs):
        n = self.nt.nodes.new(kind)
        for k, v in inputs.items():
            n.inputs[k].default_value = v
        return n

    def math(self, op, a, b=0.0):
        n = self.nt.nodes.new("ShaderNodeMath")
        n.operation = op
        for i, v in enumerate((a, b)):
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = float(v)
            else:
                self.L.new(v, n.inputs[i])
        return n.outputs[0]

    def mix(self, blend, a, b, fac=1.0):
        n = self.nt.nodes.new("ShaderNodeMix")
        n.data_type = "RGBA"
        n.blend_type = blend
        for sock, v in (("A", a), ("B", b)):
            if isinstance(v, tuple):
                n.inputs[sock].default_value = (*v[:3], 1.0)
            else:
                self.L.new(v, n.inputs[sock])
        if isinstance(fac, (int, float)):
            n.inputs["Factor"].default_value = float(fac)
        else:
            self.L.new(fac, n.inputs["Factor"])
        return n.outputs["Result"]

    def albedo(self, cols, attr: str):
        """Atlas texel x the COLOR_0 tint (the client's formula)."""
        tex = self.nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.get("character_atlas") or A.build_image()
        tex.interpolation = "Closest"
        vc = self.nt.nodes.new("ShaderNodeVertexColor")
        vc.layer_name = attr
        sep = self.nt.nodes.new("ShaderNodeSeparateColor")
        self.L.new(vc.outputs["Color"], sep.inputs["Color"])
        chans = [sep.outputs["Red"], sep.outputs["Green"], sep.outputs["Blue"]]
        comb = self.nt.nodes.new("ShaderNodeCombineColor")
        rest = self.math("SUBTRACT", 1.0, self.math("ADD", self.math("ADD", chans[0], chans[1]), chans[2]))
        for ch in range(3):
            acc = rest
            for k in range(3):
                acc = self.math("ADD", acc, self.math("MULTIPLY", chans[k], cols[k][ch]))
            self.L.new(acc, comb.inputs[ch])
        return self.mix("MULTIPLY", tex.outputs["Color"], comb.outputs["Color"])


def outline_material(c0=None, c1=(1, 1, 1), c2=(1, 1, 1), name: str | None = None) -> bpy.types.Material:
    """Back-face-culled emission: a darker, saturated version of the albedo
    (or a flat dark colour without tint colours)."""
    key = name or "toon_outline"
    mat = bpy.data.materials.get(key)
    if mat:
        return mat
    mat = bpy.data.materials.new(key)
    mat.use_nodes = True
    mat.use_backface_culling = True
    try:
        mat.use_backface_culling_shadow = True
    except AttributeError:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    g = _Graph(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    if c0 is None:
        em.inputs["Color"].default_value = (*OUTLINE_COLOUR, 1.0)
    else:
        alb = g.albedo([_lin(c0), _lin(c1), _lin(c2)], "Color")
        dark = g.node("ShaderNodeGamma")
        g.L.new(alb, dark.inputs["Color"])
        dark.inputs["Gamma"].default_value = OUTLINE_POW
        col = g.mix("MULTIPLY", dark.outputs[0], (OUTLINE_DARK,) * 3)
        g.L.new(col, em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    mat["toon_outline"] = True
    return mat


def material(name: str, c0, c1=(1, 1, 1), c2=(1, 1, 1), attr: str = "Color",
             shadow_tint=SHADOW_TINT) -> bpy.types.Material:
    """Cel material: tinted atlas albedo, 2 light bands with a warm shadow
    tint, and a thin rim on the shadow side. Its outline twin is stored in
    mat["outline"]."""
    cols = [_lin(c0), _lin(c1), _lin(c2)]
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    g = _Graph(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    albedo = g.albedo(cols, attr)
    # Light -> Shader to RGB -> soft 2-band step. The shading normal is the
    # file's own (custom) normal where `keep_file_normals` stored it: Blender
    # drops custom normals on meshes with shape keys when it evaluates them.
    diff = g.node("ShaderNodeBsdfDiffuse", Color=(1, 1, 1, 1))
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = NORMAL_ATTR
    vt = nt.nodes.new("ShaderNodeVectorTransform")
    vt.vector_type = "NORMAL"
    vt.convert_from = "OBJECT"
    vt.convert_to = "WORLD"
    g.L.new(attr.outputs["Vector"], vt.inputs["Vector"])
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    length = nt.nodes.new("ShaderNodeVectorMath")
    length.operation = "LENGTH"
    g.L.new(attr.outputs["Vector"], length.inputs[0])
    has = g.math("GREATER_THAN", length.outputs["Value"], 0.5)
    nmix = nt.nodes.new("ShaderNodeMix")
    nmix.data_type = "VECTOR"
    g.L.new(has, nmix.inputs["Factor"])
    g.L.new(geo.outputs["Normal"], nmix.inputs[4])
    g.L.new(vt.outputs["Vector"], nmix.inputs[5])
    nrm = nt.nodes.new("ShaderNodeVectorMath")
    nrm.operation = "NORMALIZE"
    g.L.new(nmix.outputs[1], nrm.inputs[0])
    g.L.new(nrm.outputs["Vector"], diff.inputs["Normal"])
    s2r = nt.nodes.new("ShaderNodeShaderToRGB")
    g.L.new(diff.outputs[0], s2r.inputs[0])
    bw = nt.nodes.new("ShaderNodeRGBToBW")
    g.L.new(s2r.outputs["Color"], bw.inputs[0])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "EASE"
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = THRESHOLD - STEP / 2, (0, 0, 0, 1)
    els[1].position, els[1].color = THRESHOLD + STEP / 2, (1, 1, 1, 1)
    g.L.new(bw.outputs[0], ramp.inputs["Fac"])
    lit = ramp.outputs["Color"]
    shadow = g.mix("MULTIPLY", albedo, tuple(shadow_tint))
    # Saturate the shadow a little (warm, never grey): mix toward albedo^1.3.
    sat = g.node("ShaderNodeGamma")
    g.L.new(shadow, sat.inputs["Color"])
    sat.inputs["Gamma"].default_value = 1.18
    shaded = g.mix("MIX", sat.outputs[0], albedo, g.math("MAXIMUM", g.math("ADD", lit, 0.0), 0.0))
    # Rim: a thin band at grazing angles on the shadow side.
    lw = g.node("ShaderNodeLayerWeight", Blend=0.3)
    rim = g.math("GREATER_THAN", lw.outputs["Facing"], 0.80)
    unlit = g.math("SUBTRACT", 1.0, bw.outputs[0])
    rim = g.math("MULTIPLY", g.math("MULTIPLY", rim, g.math("GREATER_THAN", unlit, 1.0 - THRESHOLD)), RIM_STRENGTH)
    rim_col = g.mix("SCREEN", albedo, (0.9, 0.9, 1.0), 0.6)
    final = g.mix("MIX", shaded, rim_col, rim)
    em = nt.nodes.new("ShaderNodeEmission")
    g.L.new(final, em.inputs["Color"])
    g.L.new(em.outputs[0], out.inputs["Surface"])
    mat["outline"] = outline_material(c0, c1, c2, name=f"{name}_outline").name
    return mat


def assign(obj: bpy.types.Object, mat: bpy.types.Material, outline: bool = True) -> None:
    """Material plus the inverted-hull outline (slot 2, Solidify after the
    Armature modifier). The vertex-colour nodes follow the object's layer."""
    attr = obj.data.color_attributes[0].name if obj.data.color_attributes else "Color"
    omat = bpy.data.materials.get(mat.get("outline", "")) or outline_material()
    for m in (mat, omat):
        for n in m.node_tree.nodes:
            if n.type == "VERTEX_COLOR":
                n.layer_name = attr
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    mod = obj.modifiers.get("toon_outline")
    if not outline:
        if mod:
            obj.modifiers.remove(mod)
        return
    obj.data.materials.append(omat)
    if mod is None:
        mod = obj.modifiers.new("toon_outline", "SOLIDIFY")
    face = obj.get("region") == "head" or "_head_" in obj.name
    mod.thickness = OUTLINE_FACE_M if face else OUTLINE_M
    if face:
        # Thinner still where the face looks straight ahead, so the hull
        # never draws lines along the soft creases of the face front; the
        # jaw and cheek silhouette keep the full width.
        import numpy as np

        me = obj.data
        n = np.zeros(len(me.vertices) * 3)
        me.vertices.foreach_get("normal", n)
        n = n.reshape(-1, 3)
        w = 1.0 - np.clip(-n[:, 1], 0.0, 1.0) ** 2
        g = obj.vertex_groups.get("toon_outline_w") or obj.vertex_groups.new(name="toon_outline_w")
        for value in np.unique(np.round(w, 1)):
            g.add([int(i) for i in np.nonzero(np.round(w, 1) == value)[0]], float(value), "REPLACE")
        mod.vertex_group = g.name
        mod.thickness_vertex_group = 0.1
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.use_rim = False
    mod.use_even_offset = False
    mod.use_quality_normals = True
    mod.material_offset = 1


# ----------------------------------------------------------------------------
# Character adoption (docs/art/character-adoption.md section 5): the shaded
# look the client's toon pass aims for, for review stills. Reads the baked
# `_SHADE` layer (charlib/shade_bake.py): creases move the light threshold
# into a tinted shadow band, the ambient keeps `aoFloor` in the deepest
# crease, a hemispheric sky lift brightens up-facing surfaces, a stronger rim
# on both bands, and real cast shadows (the key light casts, the floor and the
# body receive), except on faces, which stay clean.
# ----------------------------------------------------------------------------

KEY_ROT = (48.0, 0.0, -16.0)  # the studio key light (degrees, XYZ Euler)
KINDS = {
    #        threshold softness shadowTint            shadeShift aoFloor skyLift rim   rimWidth outline
    "cloth": (0.19, 0.05, (0.74, 0.78, 0.92), 0.6, 0.55, 0.25, 0.70, 0.30, 1.0),
    "skin": (0.09, 0.05, (0.97, 0.74, 0.70), 0.3, 0.55, 0.25, 0.60, 0.30, 0.85),
    "face": (0.09, 0.05, (0.97, 0.74, 0.70), 0.0, 1.00, 0.15, 0.15, 0.25, 0.6),
    "hair": (0.19, 0.05, (0.80, 0.78, 0.92), 0.5, 0.55, 0.25, 0.60, 0.30, 0.45),
    "metal": (0.19, 0.04, (0.85, 0.85, 0.85), 0.6, 0.55, 0.25, 0.60, 0.30, 1.0),
}
SHADOW_LIGHT = 0.52  # direct light kept in the shadow band


def key_direction():
    from mathutils import Euler, Vector

    e = Euler(tuple(math.radians(a) for a in KEY_ROT), "XYZ")
    return tuple(-(e.to_matrix() @ Vector((0.0, 0.0, -1.0))))  # toward the light


def studio_shaded(resolution=(640, 960), background=(0.66, 0.70, 0.76)) -> bpy.types.Scene:
    """`studio` with the key light casting shadows (soft) and a floor that
    receives them (cel-stepped)."""
    sc = studio(resolution, background)
    key = bpy.data.objects.get("toon_key")
    if key is not None:
        key.rotation_euler = tuple(math.radians(a) for a in KEY_ROT)
        key.data.use_shadow = True
        key.data.angle = math.radians(3.0)
    floor = bpy.data.objects.get("floor")
    if floor is not None:
        m = bpy.data.materials.new("toon_floor_shadow")
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        g = _Graph(m)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        diff = g.node("ShaderNodeBsdfDiffuse", Color=(1, 1, 1, 1))
        s2r = nt.nodes.new("ShaderNodeShaderToRGB")
        g.L.new(diff.outputs[0], s2r.inputs[0])
        bw = nt.nodes.new("ShaderNodeRGBToBW")
        g.L.new(s2r.outputs["Color"], bw.inputs[0])
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.interpolation = "EASE"
        e = ramp.color_ramp.elements
        lit = tuple(c * 0.86 for c in background)
        shade = tuple(c * 0.86 * f for c, f in zip(background, (0.66, 0.70, 0.84)))
        e[0].position, e[0].color = 0.05, (*shade, 1.0)
        e[1].position, e[1].color = 0.25, (*lit, 1.0)
        g.L.new(bw.outputs[0], ramp.inputs["Fac"])
        em = nt.nodes.new("ShaderNodeEmission")
        g.L.new(ramp.outputs["Color"], em.inputs["Color"])
        g.L.new(em.outputs[0], out.inputs["Surface"])
        floor.data.materials.clear()
        floor.data.materials.append(m)
    try:
        sc.eevee.use_shadows = True
        sc.eevee.shadow_ray_count = 2
        sc.eevee.shadow_step_count = 6
    except AttributeError:
        pass
    return sc


def _smoothstep(g, e0, e1, x):
    """smoothstep(e0, e1, x) with node sockets or floats for e0, e1."""
    span = g.math("SUBTRACT", e1, e0)
    t = g.math("DIVIDE", g.math("SUBTRACT", x, e0), g.math("MAXIMUM", span, 1e-4))
    t = g.math("MINIMUM", g.math("MAXIMUM", t, 0.0), 1.0)
    return g.math("MULTIPLY", g.math("MULTIPLY", t, t), g.math("SUBTRACT", 3.0, g.math("MULTIPLY", t, 2.0)))


def shaded_material(name: str, c0, c1=(1, 1, 1), c2=(1, 1, 1), kind: str = "cloth", attr: str = "Color") -> bpy.types.Material:
    """The adoption shading for one region kind (KINDS). Faces use a fixed
    key direction (no cast shadow on the face region); every other kind
    takes the lit value through Shader to RGB, cast shadows included."""
    th, soft, tint, shift, ao_floor, sky, rim_k, rim_w, _ = KINDS[kind]
    cols = [_lin(c0), _lin(c1), _lin(c2)]
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    g = _Graph(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    albedo = g.albedo(cols, attr)
    sh = nt.nodes.new("ShaderNodeAttribute")
    sh.attribute_name = "_SHADE"
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    g.L.new(sh.outputs["Color"], sep.inputs["Color"])
    has_shade = g.math("GREATER_THAN", g.math("ADD", sep.outputs["Red"], sep.outputs["Blue"]), 0.01)
    x = g.math("ADD", g.math("MULTIPLY", sep.outputs["Red"], has_shade), g.math("SUBTRACT", 1.0, has_shade))
    # Normal (the file's custom normal where stored, else the geometry's).
    nattr = nt.nodes.new("ShaderNodeAttribute")
    nattr.attribute_name = NORMAL_ATTR
    vt = nt.nodes.new("ShaderNodeVectorTransform")
    vt.vector_type, vt.convert_from, vt.convert_to = "NORMAL", "OBJECT", "WORLD"
    g.L.new(nattr.outputs["Vector"], vt.inputs["Vector"])
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    length = nt.nodes.new("ShaderNodeVectorMath")
    length.operation = "LENGTH"
    g.L.new(nattr.outputs["Vector"], length.inputs[0])
    nmix = nt.nodes.new("ShaderNodeMix")
    nmix.data_type = "VECTOR"
    g.L.new(g.math("GREATER_THAN", length.outputs["Value"], 0.5), nmix.inputs["Factor"])
    g.L.new(geo.outputs["Normal"], nmix.inputs[4])
    g.L.new(vt.outputs["Vector"], nmix.inputs[5])
    nrm = nt.nodes.new("ShaderNodeVectorMath")
    nrm.operation = "NORMALIZE"
    g.L.new(nmix.outputs[1], nrm.inputs[0])
    N = nrm.outputs["Vector"]
    if kind == "face":
        dot = nt.nodes.new("ShaderNodeVectorMath")
        dot.operation = "DOT_PRODUCT"
        g.L.new(N, dot.inputs[0])
        dot.inputs[1].default_value = key_direction()
        light = g.math("MAXIMUM", dot.outputs["Value"], 0.0)
        light = g.math("MULTIPLY", light, 0.95)
    else:
        diff = g.node("ShaderNodeBsdfDiffuse", Color=(1, 1, 1, 1))
        g.L.new(N, diff.inputs["Normal"])
        s2r = nt.nodes.new("ShaderNodeShaderToRGB")
        g.L.new(diff.outputs[0], s2r.inputs[0])
        bw = nt.nodes.new("ShaderNodeRGBToBW")
        g.L.new(s2r.outputs["Color"], bw.inputs[0])
        light = bw.outputs[0]
    t = g.math("ADD", th, g.math("MULTIPLY", g.math("SUBTRACT", 1.0, x), shift))
    lit = _smoothstep(g, g.math("SUBTRACT", t, soft), g.math("ADD", t, soft), light)
    sep_n = nt.nodes.new("ShaderNodeSeparateXYZ")
    g.L.new(N, sep_n.inputs[0])
    skyf = g.math("ADD", 1.0, g.math("MULTIPLY", sep_n.outputs["Z"], sky))
    aof = g.math("ADD", ao_floor, g.math("MULTIPLY", x, 1.0 - ao_floor))
    shadow_k = g.math("MULTIPLY", g.math("MULTIPLY", aof, skyf), SHADOW_LIGHT + 0.33)
    shadow = g.mix("MULTIPLY", albedo, tuple(tint))
    shadow = g.mix("MULTIPLY", shadow, (1, 1, 1), 0.0)
    shadow_c = nt.nodes.new("ShaderNodeMix")
    shadow_c.data_type = "RGBA"
    shadow_c.blend_type = "MULTIPLY"
    shadow_c.inputs["Factor"].default_value = 1.0
    g.L.new(shadow, shadow_c.inputs["A"])
    comb = nt.nodes.new("ShaderNodeCombineColor")
    for i in range(3):
        g.L.new(shadow_k, comb.inputs[i])
    g.L.new(comb.outputs["Color"], shadow_c.inputs["B"])
    sat = g.node("ShaderNodeGamma")
    g.L.new(shadow_c.outputs["Result"], sat.inputs["Color"])
    sat.inputs["Gamma"].default_value = 1.12
    litk = g.math("MULTIPLY", g.math("ADD", 0.93, g.math("MULTIPLY", x, 0.07)), g.math("ADD", 1.0, g.math("MULTIPLY", sep_n.outputs["Z"], 0.06)))
    comb2 = nt.nodes.new("ShaderNodeCombineColor")
    for i in range(3):
        g.L.new(litk, comb2.inputs[i])
    lit_c = g.mix("MULTIPLY", albedo, comb2.outputs["Color"])
    shaded = g.mix("MIX", sat.outputs[0], lit_c, lit)
    # Rim on both bands (brighter on the lit side).
    lw = g.node("ShaderNodeLayerWeight", Blend=0.5)
    facing = lw.outputs["Facing"]
    rim = _smoothstep(g, 1.0 - rim_w, 1.0, facing)
    rim = g.math("MULTIPLY", rim, g.math("MULTIPLY", rim_k, g.math("ADD", 0.45, g.math("MULTIPLY", lit, 0.55))))
    rim_col = g.mix("SCREEN", albedo, (0.95, 0.95, 1.0), 0.55)
    final = g.mix("MIX", shaded, rim_col, g.math("MULTIPLY", rim, 0.6))
    em = nt.nodes.new("ShaderNodeEmission")
    g.L.new(final, em.inputs["Color"])
    g.L.new(em.outputs[0], out.inputs["Surface"])
    mat["outline"] = outline_material(c0, c1, c2, name=f"{name}_outline").name
    mat["kind"] = kind
    return mat


def assign_shaded(obj: bpy.types.Object, mat: bpy.types.Material, kind: str = "cloth") -> None:
    """`assign` plus outline thickness from the region kind and the
    `_SHADE.w` outline hint (thin lines on folds, hair and faces)."""
    assign(obj, mat, outline=True)
    mod = obj.modifiers.get("toon_outline")
    me = obj.data
    a = me.attributes.get("_SHADE")
    base = KINDS[kind][8]
    import numpy as np

    w = np.ones(len(me.vertices))
    if a is not None and a.domain == "POINT":
        v = np.zeros(len(me.vertices) * 4, dtype=np.float32)
        a.data.foreach_get("color", v)
        w = v.reshape(-1, 4)[:, 3].astype(float)
    if kind == "face":
        n = np.zeros(len(me.vertices) * 3)
        me.vertices.foreach_get("normal", n)
        w = w * (1.0 - np.clip(-n.reshape(-1, 3)[:, 1], 0.0, 1.0) ** 2)
    grp = obj.vertex_groups.get("toon_outline_w") or obj.vertex_groups.new(name="toon_outline_w")
    for value in np.unique(np.round(w, 1)):
        grp.add([int(i) for i in np.nonzero(np.round(w, 1) == value)[0]], float(value), "REPLACE")
    mod.vertex_group = grp.name
    mod.thickness_vertex_group = 0.15
    mod.thickness = OUTLINE_M * base
