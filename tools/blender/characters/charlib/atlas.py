"""The shared character atlas: flat swatches plus a painted eye.

Every character asset (bodies, hair, gear) uses this one small texture, so the
client can merge a whole dressed character into one mesh and one material.
Faces point their UVs at the centre of a 16 px swatch (zero UV derivatives, so
they always sample the sharp mip 0); the eye faces map into the painted eye.

The texture holds neutral base colours. Tinting comes from the COLOR_0 mask:
`albedo = texture * ((1 - r - g - b) + r * C0 + g * C1 + b * C2)` where C is
(skin tone, hair colour, eye colour) on bodies (b only on the iris faces), (hair colour, -, -) on hair and the
item's (primary, secondary, trim) dyes on gear. Undyed parts (mask 0, such as
a shirt collar under a jumper or a boot sole) show the texture colour as is.
"""

from __future__ import annotations


import numpy as np

SIZE = 256
CELL = 16
GRID = SIZE // CELL

# name -> (column, row from the bottom, sRGB hex)
SWATCHES: dict[str, tuple[int, int, str]] = {}


def _add(name: str, col: int, row: int, hex_: str) -> None:
    SWATCHES[name] = (col, row, hex_)


# Row 0: skin and face details (body mask: r = skin tone, g = hair colour).
_add("skin", 0, 0, "#FBF8F6")
_add("skin_shade", 1, 0, "#E9DFDB")
_add("skin_deep", 2, 0, "#D8CBC6")
_add("lips", 3, 0, "#F1D9D4")  # anime: lips barely coloured
_add("lips_lower", 4, 0, "#F3DDD8")
_add("mouth", 5, 0, "#4A2224")
_add("nails", 6, 0, "#FFF4F1")
_add("lash", 7, 0, "#2E2522")
_add("brow", 8, 0, "#C9C6C4")
_add("blush", 9, 0, "#F6E3E0")
_add("inner_ear", 10, 0, "#F0DCD8")
_add("sole_skin", 11, 0, "#FFF6F2")
# Row 1: underwear (undyed).
_add("underwear", 0, 1, "#6E6965")
_add("underwear_trim", 1, 1, "#4C4845")
# Row 2: hair (hair mask r = hair colour): one near-white cell (0.92) for
# every hair vertex; root darkening, occlusion and the angel ring come from
# the hair's _SHADE layer in the shader (character-adoption.md section 4).
_add("hair", 0, 2, "#EBEBEB")
# Row 3: dyeable fabric bases (gear mask r/g/b = primary/secondary/trim).
_add("dye", 0, 3, "#F4F4F4")
_add("dye_light", 1, 3, "#FFFFFF")
_add("dye_shade", 2, 3, "#C8C8C8")
_add("dye_deep", 3, 3, "#9C9C9C")
_add("dye_stitch", 4, 3, "#E0E0E0")
_add("dye_soft", 5, 3, "#E6E6E6")
# Row 4: fixed (undyed) materials.
_add("shirt_white", 0, 4, "#EAE8E2")
_add("shirt_shade", 1, 4, "#CFCCC5")
_add("jumper_grey", 2, 4, "#7C7D82")
_add("jumper_shade", 3, 4, "#66676B")
_add("sole", 4, 4, "#2C2826")
_add("sole_edge", 5, 4, "#4A4440")
_add("gold", 6, 4, "#C9A13B")
_add("iron", 7, 4, "#6E7073")
_add("lace", 8, 4, "#3A3634")
_add("white", 9, 4, "#F2F0EC")
_add("inner_dark", 10, 4, "#2A2726")
_add("tie_knot_shade", 11, 4, "#B4B4B4")
_add("tights", 12, 4, "#3E393B")  # opaque dark tights under crafted skirts
# Row 5: anime face paint of the MakeHuman bodies (charlib/mh_face.py): flat
# cel colours, undyed; the feature outlines are cut into the mesh.
_add("sclera", 0, 5, "#F8F6F4")
_add("sclera_shade", 1, 5, "#D3D5E3")
# The iris cells are greyscale (character adoption): the iris faces carry the
# body mask's B channel = eye colour, so the eye colour tints them like skin
# and hair (one material, one draw).
_add("iris_dark", 2, 5, "#4D4D4D")  # 0.30
_add("iris", 3, 5, "#8C8C8C")  # 0.55
_add("iris_light", 4, 5, "#D9D9D9")  # 0.85
_add("iris_rim", 5, 5, "#2C1811")
_add("pupil", 6, 5, "#1F110C")
_add("eye_hl", 7, 5, "#FFFFFF")
_add("mouth_line", 8, 5, "#94554D")

# The painted eye occupies the top-right 64 x 64 px.
EYE_REGION = (SIZE - 64, SIZE - 64, 64)


def uv(name: str) -> tuple[float, float]:
    col, row, _ = SWATCHES[name]
    return ((col + 0.5) * CELL / SIZE, (row + 0.5) * CELL / SIZE)


def eye_uv(local: np.ndarray) -> np.ndarray:
    """Map eye-local coordinates (-1..1, the iris box) into the eye region."""
    x0, y0, size = EYE_REGION
    inset = 3.0
    half = (size - 2 * inset) / 2.0
    loc = np.clip(local, -1.0, 1.0)
    u = (x0 + inset + half + loc[:, 0] * half) / SIZE
    v = (y0 + inset + half + loc[:, 1] * half) / SIZE
    return np.stack([u, v], axis=1)


def _srgb(hex_: str) -> np.ndarray:
    h = hex_.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


def _paint_eye(img: np.ndarray) -> None:
    """Anime-style eye (docs/art/style-guide.md): sclera with the upper-lid
    shadow band, a tall iris graded dark (top) to light (bottom) inside a
    dark rim, an oval pupil and two catch lights. Flat regions with crisp
    edges, no fibres (sRGB, rows bottom-up)."""
    x0, y0, size = EYE_REGION
    inset = 3.0
    half = (size - 2 * inset) / 2.0
    ys, xs = np.mgrid[0:size, 0:size]
    u = (xs + 0.5 - inset - half) / half  # -1..1 over the iris box
    v = (ys + 0.5 - inset - half) / half
    aa = 0.035  # edge softness in box units (about one texel)

    def inside(d):  # 1 inside (d < 0), crisp edge
        return np.clip(0.5 - d / aa, 0.0, 1.0)[..., None]

    def ellipse(cu, cv, ru, rv):
        return np.sqrt(((u - cu) / ru) ** 2 + ((v - cv) / rv) ** 2) - 1.0

    out = _srgb("#F6F4F2")[None, None, :] * np.ones((size, size, 1))
    lid = inside(0.30 - v)  # v > 0.30: under the upper lid
    out = out * (1 - lid) + _srgb("#C9CCD8") * lid
    iris_ru, iris_rv = 0.80, 0.96
    e = ellipse(0.0, -0.02, iris_ru, iris_rv)
    t = np.clip((v + 0.9) / 1.8, 0.0, 1.0)[..., None]  # 0 bottom .. 1 top
    iris = _srgb("#B98A55") * (1 - t) + _srgb("#4A3020") * t
    iris = np.where(t > 0.62, _srgb("#3A2618"), iris)  # upper shadow band
    out = out * (1 - inside(e)) + iris * inside(e)
    rim = inside(e) * (1 - inside(ellipse(0.0, -0.02, iris_ru - 0.09, iris_rv - 0.09)))
    out = out * (1 - rim) + _srgb("#24170F") * rim
    pupil = inside(ellipse(0.0, 0.04, 0.27, 0.40))
    out = out * (1 - pupil) + _srgb("#1A110C") * pupil
    for cu, cv, r in ((-0.26, 0.34, 0.17), (0.24, -0.38, 0.075)):
        hl = inside(ellipse(cu, cv, r * 1.15, r))
        out = out * (1 - hl) + _srgb("#FFFFFF") * hl
    # Upper lash line: the top of the eyeball under the lid reads as a bold
    # lash, overlapping the iris like a drawn eye.
    lash = inside(0.46 - 0.18 * u * u - v)
    out = out * (1 - lash) + _srgb("#2A1E1A") * lash
    img[y0:y0 + size, x0:x0 + size, :3] = out


def pixels() -> np.ndarray:
    """(SIZE, SIZE, 4) sRGB floats, bottom row first (Blender's order)."""
    img = np.zeros((SIZE, SIZE, 4))
    img[..., :3] = _srgb("#808080")
    img[..., 3] = 1.0
    for _, (col, row, hex_) in SWATCHES.items():
        img[row * CELL:(row + 1) * CELL, col * CELL:(col + 1) * CELL, :3] = _srgb(hex_)
    _paint_eye(img)
    return img


def build_image(path: str | None = None, name: str = "character_atlas"):
    """Create (or refresh) the Blender image; save it as PNG when `path` is given."""
    import bpy

    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, SIZE, SIZE, alpha=False)
    img.colorspace_settings.name = "sRGB"
    px = pixels().astype(np.float32)
    # Blender stores float pixels linear for float buffers but byte images
    # take sRGB values directly.
    img.pixels.foreach_set(px.ravel())
    img.update()
    if path:
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
    return img


def material(name: str = "character", image=None):
    """Principled material using the atlas, with the vertex colour wired as a
    preview tint (the client does its own tinting from COLOR_0)."""
    import bpy

    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.72
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image or build_image()
    tex.interpolation = "Closest"
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def hex_to_linear(hex_: str) -> tuple[float, float, float]:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    s = _srgb(hex_)
    return tuple(lin(float(c)) for c in s)
