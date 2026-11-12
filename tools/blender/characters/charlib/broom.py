"""The school broom prop and its numbers.

The broom is one opaque mesh, `characters/broom.glb` under the assets root,
at most `MAX_TRIANGLES` triangles: a wooden handle, two dark bindings and a
bundle of twigs, coloured per vertex (`COLOR_0`) from palette data
(`data/items/palette.json`: `PALETTE` names the colours by role). No texture.

**Frame** (glTF axes): y up, the handle forward along +z, x across. The
origin is the **rider's hips bone** (`DEF-spine`'s head) in the riding clips
(`anim.broom_ride`, `anim.broom_hover`): the handle runs under it with its
top `SEAT_BELOW` metres below the origin, so the client stands the broom at
the hips bone of a rider every frame (turned with the character's yaw only)
and the rider sits on the handle. The clips keep the pelvis at its rest
rotation, so the hips bone carries the broom up and down with the hover's
bob and nothing else turns it. The broom is not scaled with the rider's
height (at the shortest and tallest heights the seat moves by under 1 cm).

The riding pose is solved against these numbers (the handle's height under
the hips bone, the hand grips along it), so the clip and the prop agree.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
PALETTE_PATH = os.path.join(REPO, "data", "items", "palette.json")
ASSET = "characters/broom"
FILE = "characters/broom.glb"
MAX_TRIANGLES = 800  # docs/architecture.md budgets; castle_kit/grid.py P3B_BROOM_TRIS

# Model frame here is Blender's (z up, forward -y, x the rider's left), the
# origin at the rider's hips bone. Metres.
SEAT_BELOW = 0.078  # the handle's top under the hips bone (the crotch rests on it)
HANDLE_R = 0.017  # the handle's radius at the seat
HANDLE_R_FRONT = 0.013  # at the front tip
HANDLE_FRONT = 0.95  # the handle's tip ahead of the origin
HANDLE_BACK = 0.62  # the handle's end inside the twigs, behind the origin
BINDING = (0.40, 0.47)  # the twigs' bound neck behind the origin (from, to)
TWIG_END = 1.12  # the twigs' ends behind the origin
TWIG_R = (0.034, 0.07, 0.115, 0.13)  # the bundle's radius at the neck, a third, two thirds, the end
TWIG_DROOP = 0.06  # the bundle's ends hang this much lower than the handle's line
GRIPS = (0.17, 0.28)  # the hands' grips ahead of the origin (back hand, front hand)

# Colours by role: ids of data/items/palette.json "colours".
PALETTE = {"handle": "chestnut", "binding": "umber_black", "twig": "camel", "twig_dark": "tan"}


def palette() -> dict[str, str]:
    """Role -> "#RRGGBB" from data/items/palette.json."""
    with open(PALETTE_PATH, encoding="utf-8") as f:
        cols = {c["id"]: c["hex"] for c in json.load(f)["colours"]}
    return {role: cols[cid] for role, cid in PALETTE.items()}


def linear(hex_: str) -> tuple[float, float, float]:
    """sRGB hex -> linear RGB (glTF COLOR_0 is linear)."""
    h = hex_.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def handle_axis_z() -> float:
    """The handle's centre line under the origin (Blender z)."""
    return -SEAT_BELOW - HANDLE_R


class _Mesh:
    def __init__(self):
        self.verts: list[tuple[float, float, float]] = []
        self.faces: list[tuple[int, ...]] = []
        self.roles: list[str] = []

    def v(self, p) -> int:
        self.verts.append((float(p[0]), float(p[1]), float(p[2])))
        return len(self.verts) - 1

    def f(self, idx, role: str) -> None:
        self.faces.append(tuple(idx))
        self.roles.append(role)

    def tris(self) -> int:
        return sum(len(f) - 2 for f in self.faces)


def _ring(m: _Mesh, y: float, z: float, r: float, n: int, phase: float = 0.0) -> list[int]:
    """n vertices round the y axis at (0, y, z), radius r."""
    return [m.v((r * math.cos(phase + 2 * math.pi * i / n), y, z + r * math.sin(phase + 2 * math.pi * i / n))) for i in range(n)]


def _bridge(m: _Mesh, a: list[int], b: list[int], role: str, flip: bool = False) -> None:
    n = len(a)
    for i in range(n):
        j = (i + 1) % n
        quad = (a[i], a[j], b[j], b[i])
        m.f(quad[::-1] if flip else quad, role)


def _cap(m: _Mesh, ring: list[int], centre, role: str, flip: bool = False) -> None:
    c = m.v(centre)
    n = len(ring)
    for i in range(n):
        tri = (ring[i], ring[(i + 1) % n], c)
        m.f(tri[::-1] if flip else tri, role)


def build() -> _Mesh:
    """The broom's geometry (Blender axes, the origin at the rider's hips bone)."""
    m = _Mesh()
    zc = handle_axis_z()
    n = 8
    # The handle: from the front tip (forward is -y) back into the twigs, a
    # gentle taper and a slight upturn at the very front.
    stations = [(-HANDLE_FRONT, HANDLE_R_FRONT * 0.75, 0.012), (-HANDLE_FRONT + 0.03, HANDLE_R_FRONT, 0.008),
                (-0.55, (HANDLE_R + HANDLE_R_FRONT) / 2, 0.0), (-0.1, HANDLE_R, 0.0), (0.3, HANDLE_R, 0.0),
                (HANDLE_BACK, HANDLE_R * 0.95, 0.0)]
    rings = [_ring(m, y, zc + dz, r, n) for y, r, dz in stations]
    _cap(m, rings[0], (0.0, -HANDLE_FRONT - 0.008, zc + 0.012), "handle", flip=False)
    for a, b in zip(rings, rings[1:]):
        _bridge(m, a, b, "handle", flip=True)
    _cap(m, rings[-1], (0.0, HANDLE_BACK, zc), "handle", flip=True)
    # The twig bundle: a flared shell from the bound neck to the ends,
    # drooping a little, closed at the back by a shallow cone.
    k = 12
    y0, y3 = BINDING[0], TWIG_END
    ys = [y0, y0 + (y3 - y0) / 3, y0 + 2 * (y3 - y0) / 3, y3]
    shell = [_ring(m, y, zc - TWIG_DROOP * ((y - y0) / (y3 - y0)) ** 1.5, r, k, phase=math.pi / k)
             for y, r in zip(ys, TWIG_R)]
    _cap(m, shell[0], (0.0, y0 - 0.02, zc), "twig", flip=False)
    for a, b in zip(shell, shell[1:]):
        _bridge(m, a, b, "twig", flip=True)
    _cap(m, shell[-1], (0.0, y3 - 0.06, zc - TWIG_DROOP), "twig_dark", flip=True)
    # Bindings: two short dark collars round the neck.
    for yb in BINDING:
        rb = TWIG_R[0] + 0.006 + (TWIG_R[1] - TWIG_R[0]) * 3 * (yb - y0) / (y3 - y0)
        a = _ring(m, yb - 0.012, zc, rb, k, phase=math.pi / k)
        b = _ring(m, yb + 0.012, zc - 0.0005, rb, k, phase=math.pi / k)
        _bridge(m, a, b, "binding", flip=True)
    # Loose twigs: thin three-sided spikes poking out of the bundle's end and
    # along its outside, so the silhouette reads as twigs, not a cone.
    rng = np.random.default_rng(7)
    end_z = zc - TWIG_DROOP
    for i in range(30):
        ang = 2 * math.pi * (i / 30) + rng.uniform(-0.1, 0.1)
        rr = TWIG_R[3] * rng.uniform(0.25, 0.95)
        base = np.array((rr * math.cos(ang), y3 - 0.05, end_z + rr * math.sin(ang)))
        tip = base + np.array((0.25 * rr * math.cos(ang), rng.uniform(0.07, 0.16), 0.25 * rr * math.sin(ang) - 0.01))
        _spike(m, base, tip, 0.008, "twig" if i % 3 else "twig_dark")
    for i in range(16):
        ang = 2 * math.pi * (i / 16) + 0.2
        u0 = rng.uniform(0.25, 0.55)
        y_a = y0 + (y3 - y0) * u0
        r_a = np.interp(u0, (0, 1 / 3, 2 / 3, 1), TWIG_R) * 0.98
        base = np.array((r_a * math.cos(ang), y_a, zc - TWIG_DROOP * u0 ** 1.5 + r_a * math.sin(ang)))
        out = np.array((math.cos(ang), 0.0, math.sin(ang)))
        tip = base + np.array((0.0, y3 + 0.06 - y_a, -TWIG_DROOP * (1 - u0 ** 1.5))) + out * 0.03
        _spike(m, base, tip, 0.006, "twig_dark" if i % 2 else "twig")
    return m


def _spike(m: _Mesh, base, tip, r: float, role: str) -> None:
    """A thin three-sided spike (three triangles; the base is hidden in the bundle)."""
    d = np.asarray(tip) - np.asarray(base)
    d /= np.linalg.norm(d)
    a = np.cross(d, (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0))
    a /= np.linalg.norm(a)
    b = np.cross(d, a)
    ring = [m.v(np.asarray(base) + r * (math.cos(t) * a + math.sin(t) * b)) for t in (0.0, 2.094, 4.189)]
    t = m.v(tip)
    for i in range(3):
        m.f((ring[i], ring[(i + 1) % 3], t), role)


def to_object(name: str = "broom"):
    """The broom as a Blender object: one mesh, one material, COLOR_0."""
    import bpy

    m = build()
    cols = {role: linear(hx) for role, hx in palette().items()}
    me = bpy.data.meshes.new(name)
    me.from_pydata(m.verts, [], m.faces)
    attr = me.color_attributes.new("COLOR_0", "FLOAT_COLOR", "CORNER")
    data = []
    for f, role in zip(m.faces, m.roles):
        data += [*cols[role], 1.0] * len(f)
    attr.data.foreach_set("color", data)
    me.color_attributes.active_color = attr
    me.color_attributes.render_color_index = 0
    for poly, role in zip(me.polygons, m.roles):
        poly.use_smooth = role == "handle"
    me.validate(clean_customdata=False)
    me.update()
    mat = bpy.data.materials.get("broom") or bpy.data.materials.new("broom")
    mat.use_nodes = True
    mat.use_backface_culling = True  # closed shapes: single-sided in glTF
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Roughness"].default_value = 0.85
        vc = nt.nodes.new("ShaderNodeVertexColor")
        vc.layer_name = "COLOR_0"
        nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    me.materials.append(mat)
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def export(assets: str, manifest: dict | None = None) -> dict:
    """Writes `<assets>/characters/broom.glb` and returns (and, given a
    manifest, stores under "broom") its entry."""
    import bpy

    for o in list(bpy.data.objects):
        if o.name == "broom" or o.name.startswith("broom."):
            bpy.data.objects.remove(o)
    obj = to_object("broom")
    path = os.path.join(assets, FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                              export_materials="EXPORT", export_normals=True, export_tangents=False, export_texcoords=False,
                              export_cameras=False, export_lights=False, export_animations=False, export_skins=False,
                              export_morph=False, export_extras=False, export_vertex_color="ACTIVE",
                              export_all_vertex_colors=False)
    tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)
    if tris > MAX_TRIANGLES:
        raise RuntimeError(f"broom: {tris} triangles over the {MAX_TRIANGLES} budget")
    pts = np.array([v.co[:] for v in obj.data.vertices])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    bpy.data.objects.remove(obj)
    entry = {
        "file": FILE,
        "triangles": int(tris),
        "attach": "DEF-spine",
        "origin": "the rider's hips bone (DEF-spine head) in broom_ride and broom_hover; stand it there each frame, turned with the character's yaw only, not scaled",
        "seat_below_m": SEAT_BELOW,
        "handle_radius_m": HANDLE_R,
        "forward": "+z",
        # glTF axes (x, y up, z forward = Blender -y).
        "bounds": {"min": [round(float(lo[0]), 4), round(float(lo[2]), 4), round(float(-hi[1]), 4)],
                   "max": [round(float(hi[0]), 4), round(float(hi[2]), 4), round(float(-lo[1]), 4)]},
        "colours": PALETTE,
    }
    if manifest is not None:
        manifest["broom"] = entry
    return entry
