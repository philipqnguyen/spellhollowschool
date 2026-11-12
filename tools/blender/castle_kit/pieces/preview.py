"""Stand-in textures for reviewing kit geometry and UVs before the texture
library exists (castle_kit/textures.py is the Textures work): flat palette
colours with the sheet layouts drawn in, so renders show where every face's
UVs land (stone courses on 0.6 m lines, every trim strip's band and joints,
fit tiles framed, glass leading, planks). Never shipped: only
`world/render_toon.py --kit-preview` and the piece gallery use them.
numpy only."""

from __future__ import annotations

import numpy as np

STONE = "#E4D2B0"
TRIM = "#EEE2C8"
FLOOR = "#AA9076"
CEILING = "#7E5842"
WOOD = "#8E5634"
GLASS = "#93D3F2"
IRON = "#3E4150"


def _rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)


def _img(w: int, h: int, colour: str) -> np.ndarray:
    a = np.ones((h, w, 4), dtype=np.float32)
    a[..., :3] = _rgb(colour)
    return a


def _rng(seed: int):
    return np.random.default_rng(seed)


def ashlar(px: int = 1024, colour: str = STONE, seed: int = 1, course_px: int = 128) -> np.ndarray:
    """Coursed blocks: a mortar line every course_px rows from the bottom
    (0.6 m at 4.8 m per tile), blocks 0.9-1.8 m staggered per course, a
    tone per block, lit top-left and shaded bottom-right edges."""
    img = _img(px, px, colour)
    rng = _rng(seed)
    base = _rgb(colour)
    rows = px // course_px
    for r in range(rows):
        y1 = px - r * course_px  # rows from the top: course r spans y1 - course_px .. y1
        y0 = y1 - course_px
        x = int(rng.integers(0, 192))
        starts = [x - px]
        while starts[-1] < px:
            starts.append(starts[-1] + int(rng.integers(192, 384)))
        for a, b in zip(starts, starts[1:]):
            tone = base * float(rng.uniform(0.86, 1.06))
            for xa, xb in ((a, b),):
                for off in (-px, 0, px):
                    s0, s1 = max(0, xa + off), min(px, xb + off)
                    if s1 <= s0:
                        continue
                    img[y0:y1, s0:s1, :3] = tone
                    img[y0:y0 + 4, s0:s1, :3] = np.minimum(1.0, tone * 1.12)
                    img[y1 - 5:y1, s0:s1, :3] = tone * 0.8
                    img[y0:y1, s0:s0 + 3, :3] = np.minimum(1.0, tone * 1.08)
                    img[y0:y1, max(s0, s1 - 4):s1, :3] = tone * 0.84
                    img[y0:y1, s0:min(px, s0 + 2), :3] = base * 0.55
        img[y1 - 2:y1, :, :3] = base * 0.55
    return img


def flags(px: int = 1024, colour: str = FLOOR, seed: int = 2, slab_px: int = 256) -> np.ndarray:
    img = _img(px, px, colour)
    rng = _rng(seed)
    base = _rgb(colour)
    n = px // slab_px
    for i in range(n):
        for j in range(n):
            tone = base * float(rng.uniform(0.85, 1.08))
            img[j * slab_px:(j + 1) * slab_px, i * slab_px:(i + 1) * slab_px, :3] = tone
    for k in range(n + 1):
        img[max(0, k * slab_px - 2):k * slab_px + 2, :, :3] = base * 0.5
        img[:, max(0, k * slab_px - 2):k * slab_px + 2, :3] = base * 0.5
    return img


def plaster(px: int = 512, colour: str = CEILING, seed: int = 3) -> np.ndarray:
    img = _img(px, px, colour)
    rng = _rng(seed)
    noise = rng.uniform(0.92, 1.05, size=(px // 16, px // 16)).repeat(16, 0).repeat(16, 1)
    img[..., :3] *= noise[..., None]
    return img


def sheet(spec: dict, colour: str, seed: int = 4, iron: str = IRON) -> np.ndarray:
    """A sheet (castle_textures.json `sheets.<texture>`): every strip a
    moulding-like value ramp (light at its top, dark at its bottom) with
    joint lines every metre of u (or its joint_m), every tile a framed
    panel."""
    w, h = spec["px"]
    img = _img(w, h, colour)
    base = _rgb(colour)
    for name, st in spec.get("strips", {}).items():
        top, bottom = st["rows"]
        c0, c1 = st.get("cols", [0, w])
        tone = _rgb(iron) if name == "iron" else base
        ramp = np.linspace(1.08, 0.72, bottom - top)[:, None, None]
        img[top:bottom, c0:c1, :3] = np.minimum(1.0, tone[None, None, :] * ramp)
        for k in range(top, bottom, max(8, (bottom - top) // 4)):
            img[k:k + 2, c0:c1, :3] *= 0.82
        joint = st.get("joint_m") or (st["u_m"] / 4.0)
        px_per = (c1 - c0) / st["u_m"]
        x = 0.0
        while x < st["u_m"] - 1e-6:
            xi = c0 + int(x * px_per)
            img[top:bottom, xi:xi + 3, :3] *= 0.6
            x += joint
        img[top:top + 3, c0:c1, :3] = tone * 0.5
        img[bottom - 3:bottom, c0:c1, :3] = tone * 0.5
    for name, (x, y, tw, th) in spec.get("tiles", {}).items():
        img[y:y + th, x:x + tw, :3] = base * 0.95
        img[y:y + 6, x:x + tw, :3] = base * 1.0
        img[y + th - 6:y + th, x:x + tw, :3] = base * 0.6
        img[y:y + th, x:x + 6, :3] = base * 1.0
        img[y:y + th, x + tw - 6:x + tw, :3] = base * 0.6
        cx, cy = x + tw // 2, y + th // 2
        img[cy - th // 6:cy + th // 6, cx - tw // 6:cx + tw // 6, :3] = base * 0.75
    return np.clip(img, 0.0, 1.0)


def glass(spec: dict) -> np.ndarray:
    w, h = spec["px"]
    img = _img(w, h, GLASS)
    yy, xx = np.mgrid[0:h, 0:w]
    img[..., :3] *= (0.75 + 0.35 * (1 - yy / h))[..., None]
    diag = ((xx + yy) % 64 < 3) | ((xx - yy) % 64 < 3)
    img[diag, :3] = _rgb("#2A3040")
    for _, (x, y, tw, th) in spec.get("tiles", {}).items():
        img[y:y + th, x:x + 5, :3] = _rgb("#2A3040")
        img[y:y + th, x + tw - 5:x + tw, :3] = _rgb("#2A3040")
    return img


def wood(spec: dict) -> np.ndarray:
    img = sheet(spec, WOOD, iron=WOOD)
    rng = _rng(7)
    w, h = spec["px"]
    for x in range(0, w, 64):
        img[:, x:x + 64, :3] *= float(rng.uniform(0.85, 1.05))
        img[:, x:x + 2, :3] *= 0.6
    return np.clip(img, 0.0, 1.0)


def banners(spec: dict) -> np.ndarray:
    cols = {"banner_house_a": "#B0222A", "banner_house_b": "#EDB72F", "banner_house_c": "#24499A",
            "banner_house_d": "#1F7444", "banner_school": "#5E4A8A"}
    w, h = spec["px"]
    img = _img(w, h, "#808080")
    for name, (x, y, tw, th) in spec.get("tiles", {}).items():
        img[y:y + th, x:x + tw, :3] = _rgb(cols.get(name, "#808080"))
        img[y:y + 40, x:x + tw, :3] = _rgb("#EDBB4C")
        cx, cy = x + tw // 2, y + th // 3
        img[cy - 30:cy + 30, cx - 30:cx + 30, :3] = _rgb("#EDBB4C")
    return img


def texture(material: str, textures: dict) -> np.ndarray | None:
    """A stand-in image (rows top first) for a castle_* material, or None."""
    m = textures["materials"].get(material)
    if m is None:
        return None
    tex = m["texture"]
    sheets = textures["sheets"]
    if tex.startswith("stone_ashlar"):
        return ashlar(seed={"a": 1, "b": 5, "c": 9}.get(tex[-1], 1))
    if tex == "stone_flag_square":
        return flags(slab_px=256)
    if tex == "stone_flag":
        return flags(slab_px=171, colour="#B99A78")
    if tex == "vault_web":
        return plaster()
    if tex == "trim_castle":
        return sheet(sheets[tex], TRIM)
    if tex == "wood_sheet":
        return wood(sheets[tex])
    if tex == "glass_sheet":
        return glass(sheets[tex])
    if tex == "banners":
        return banners(sheets[tex])
    return None
