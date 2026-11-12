"""Contact sheets: tile rendered PNGs into one image (Blender image API, no PIL)."""

from __future__ import annotations

import os

import numpy as np


def load_rgba(path: str) -> np.ndarray:
    import bpy

    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)


def save_rgba(path: str, px: np.ndarray) -> None:
    import bpy

    h, w = px.shape[:2]
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=False)
    img.pixels.foreach_set(px.astype(np.float32).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def tile(paths: list[str], out: str, cols: int, scale: int = 1, remove: bool = False) -> str:
    """Tile same-sized images left to right, top to bottom; `scale` > 1 shrinks."""
    tiles = [load_rgba(p)[::scale, ::scale] for p in paths]
    h, w = tiles[0].shape[:2]
    rows = (len(tiles) + cols - 1) // cols
    sheet = np.ones((rows * h, cols * w, 4), dtype=np.float32)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        # Blender pixel rows run bottom-up: row 0 of the sheet is at the top.
        y0 = (rows - 1 - r) * h
        sheet[y0:y0 + t.shape[0], c * w:c * w + t.shape[1]] = t[:h, :w]
    save_rgba(out, sheet)
    if remove:
        for p in paths:
            os.remove(p)
    return out
