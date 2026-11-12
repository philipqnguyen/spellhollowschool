"""The furniture texture atlas and the dye mask (numpy, no `bpy`).

Every furniture item is one mesh with one material (one draw call): a
512 x 512 base-colour atlas shared by all items, plus the dye mask in the
`COLOR_0` vertex colours, the convention the client applies:

    albedo = baseColor * (1 - r - g - b + r * P + g * S + b * T)

where (r, g, b) is the mask (primary, secondary, trim weights) and P, S, T
are the item's three palette colours (linear). Dyeable tiles are painted in
light neutral greys so the dye carries the colour and the texture only the
shading; tiles in their true colours (linen, canvas, books, wax, flame,
dark) are left undyed (mask 0). Blender-convention UVs (v up).
"""

from __future__ import annotations

import numpy as np

import meshkit as mk
import paint

SIZE = 512

# Tile -> (x, y, width, height) in pixels (y from the bottom), and how many
# metres of surface one tile spans for UVs in metres.
TILES = {
    "wood": ((0, 256, 256, 256), 0.5),
    "fabric": ((256, 256, 256, 256), 0.5),
    "canvas": ((0, 128, 128, 128), None),
    # Leaves (neutral, dyed like wood and fabric) for plants.
    "leaf": ((128, 128, 128, 128), None),
    "books": ((256, 128, 256, 128), None),
    "metal": ((0, 0, 128, 128), 0.25),
    "linen": ((128, 0, 128, 128), 0.5),
    "leather": ((256, 64, 128, 64), 0.25),
    "glass": ((384, 64, 64, 64), 0.25),
    "flame": ((448, 64, 64, 64), None),
    "wax": ((256, 0, 64, 64), 0.1),
    "dark": ((320, 0, 64, 64), 0.25),
    "stone": ((384, 0, 64, 64), 0.5),
    # A bubbling brew (cauldrons), glowing like the flame.
    "brew": ((448, 0, 64, 64), None),
}
# Model materials that share a tile (kept apart so shells can split them).
ALIASES = {"fabric_cover": "fabric"}
# Padding kept free inside each tile so mipmaps do not bleed between tiles.
PAD = 3
NEUTRAL = {"wood", "fabric", "metal", "leather", "leaf"}
# Tiles that glow (the emissive atlas lights them): candle flames and brews.
GLOWING = {"flame", "brew"}
MASK = {0: (0.0, 0.0, 0.0), 1: (1.0, 0.0, 0.0), 2: (0.0, 1.0, 0.0), 3: (0.0, 0.0, 1.0)}


def paint_atlas(seed: int = 11) -> np.ndarray:
    """The base-colour atlas (RGBA float, rows bottom to top)."""
    img = np.zeros((SIZE, SIZE, 4), dtype=np.float32)
    img[..., 3] = 1.0

    def put(name, tile):
        (x, y, w, h), _ = TILES[name]
        img[y : y + h, x : x + w] = tile[:h, :w]

    # Neutral tiles: light greys, value detail only (the dye brings the hue).
    put("wood", paint.wood_grain(256, "#E4E0DA", seed, contrast=0.16))
    # Soft velvet: gentle folds and a fine weave, calm enough for cushions.
    fabric = paint.velvet(256, "#E4E2E0", seed + 1, folds=2, depth=0.1)
    fabric[..., :3] *= paint.weave(256, "#FFFFFF", seed + 12, threads=48, contrast=0.05)[..., :3]
    put("fabric", fabric)
    put("metal", paint.plaster(128, "#E6E6E6", seed + 2, contrast=0.16))
    leather = paint.plaster(128, "#DAD6D0", seed + 3, contrast=0.2)
    put("leather", leather)
    # True-colour tiles.
    put("canvas", paint.landscape(128, 128, seed + 4))
    put("leaf", paint.leaf(128, "#DDE3D8", seed + 13))
    put("brew", paint.brew(64, seed + 14))
    put("books", paint.book_spines(256, 128, seed + 5))
    put("linen", paint.weave(128, "#E9E3D6", seed + 6, threads=32))
    put("glass", paint.flat(64, "#B8CCD6", 0.05, seed + 7))
    put("flame", paint.flame(64))
    put("wax", paint.flat(64, "#EFE6CC", 0.03, seed + 8))
    put("dark", paint.flat(64, "#3A3530", 0.05, seed + 9))
    put("stone", paint.masonry(64, "#B8B2A6", "#7A746A", [16, 20], (20, 40), seed + 10, joint_px=1.5, bevel_px=3))
    return img


def paint_emissive(seed: int = 11) -> np.ndarray:
    """Emissive atlas: black except the glowing tiles (lantern and candle
    flames, cauldron brews)."""
    img = np.zeros((SIZE, SIZE, 4), dtype=np.float32)
    img[..., 3] = 1.0
    (x, y, w, h), _ = TILES["flame"]
    img[y : y + h, x : x + w] = paint.flame(64)
    (x, y, w, h), _ = TILES["brew"]
    img[y : y + h, x : x + w] = paint.brew(64, seed + 14) * np.array([0.8, 0.8, 0.8, 1.0], dtype=np.float32)
    return img


def _rect01(name: str):
    (x, y, w, h), metres = TILES[name]
    return (x + PAD) / SIZE, (y + PAD) / SIZE, (w - 2 * PAD) / SIZE, (h - 2 * PAD) / SIZE, metres


def atlas_uvs(m: mk.Mesh) -> list[list[tuple[float, float]]]:
    """Per-face atlas UVs: metre UVs keep their density inside the tile
    (scaled down if a face is larger than the tile), shifted by a stable
    per-face offset so neighbouring parts do not repeat the same grain; fit
    UVs (0..1) fill the tile."""
    out = []
    for k, f in enumerate(m.faces):
        tile = ALIASES.get(f.material, f.material)
        if tile not in TILES:
            raise KeyError(f"face material {f.material!r} is not an atlas tile")
        x0, y0, tw, th, metres = _rect01(tile)
        if metres is None:
            out.append([(x0 + min(max(u, 0.0), 1.0) * tw, y0 + min(max(v, 0.0), 1.0) * th) for u, v in f.uvs])
            continue
        us = [u / metres for u, _ in f.uvs]
        vs = [v / metres for _, v in f.uvs]
        umin, vmin = min(us), min(vs)
        ext = max(max(us) - umin, max(vs) - vmin, 1e-6)
        s = min(1.0, 1.0 / ext)
        free_u = max(0.0, 1.0 - (max(us) - umin) * s)
        free_v = max(0.0, 1.0 - (max(vs) - vmin) * s)
        h = (k * 2654435761) % 1000 / 1000.0
        ou, ov = free_u * h, free_v * ((h * 7.31) % 1.0)
        out.append([(x0 + ((u - umin) * s + ou) * tw, y0 + ((v - vmin) * s + ov) * th) for u, v in zip(us, vs)])
    return out


def face_masks(m: mk.Mesh) -> list[tuple[float, float, float]]:
    return [MASK[f.zone] for f in m.faces]
