"""Procedurally painted textures for the world art (numpy only, no `bpy`).

Everything here is made by our scripts: no photos, no third-party images.
The look follows docs/art/style-guide.md (the anime look): clean, flat
colour regions with large painted value shifts, crisp edge highlights
(light from the upper left), a little per-piece colour variation, no
high-frequency noise or grain that would fight the client's cel shading,
albedo kept between roughly #383838 and #ECECEC.

Arrays are float32 `(height, width, 4)` RGBA in sRGB-encoded 0..1 values,
row 0 at the **bottom** (Blender's pixel order), so UV (u, v) maps to
`[v * height, u * width]`. Every tiling texture wraps seamlessly. All
randomness is seeded, so the same call paints the same pixels.
"""

from __future__ import annotations

import numpy as np


def hex_rgb(h: str) -> np.ndarray:
    return np.array([int(h[i : i + 2], 16) / 255.0 for i in (1, 3, 5)], dtype=np.float32)


def srgb_to_linear(c):
    """sRGB-encoded 0..1 -> linear (for material colour factors)."""
    c = np.asarray(c, dtype=np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgba(rgb: np.ndarray) -> np.ndarray:
    h, w, _ = rgb.shape
    out = np.ones((h, w, 4), dtype=np.float32)
    out[..., :3] = np.clip(rgb, 0.0, 1.0)
    return out


def periodic_noise(h: int, w: int, cells: tuple[int, int], seed: int, octaves: int = 3,
                   persistence: float = 0.5) -> np.ndarray:
    """Tileable value noise in [0, 1]: smooth interpolation of a random
    lattice that wraps, summed over octaves (each with twice the cells)."""
    rng = np.random.default_rng(seed)
    total = np.zeros((h, w), dtype=np.float32)
    amp, norm = 1.0, 0.0
    cy, cx = cells
    for _ in range(octaves):
        lattice = rng.random((cy, cx)).astype(np.float32)
        ys = np.arange(h, dtype=np.float32) * cy / h
        xs = np.arange(w, dtype=np.float32) * cx / w
        y0 = np.floor(ys).astype(int)
        x0 = np.floor(xs).astype(int)
        ty = ys - y0
        tx = xs - x0
        ty = ty * ty * (3 - 2 * ty)
        tx = tx * tx * (3 - 2 * tx)
        y1, x1 = (y0 + 1) % cy, (x0 + 1) % cx
        y0, x0 = y0 % cy, x0 % cx
        a = lattice[np.ix_(y0, x0)]
        b = lattice[np.ix_(y0, x1)]
        c = lattice[np.ix_(y1, x0)]
        d = lattice[np.ix_(y1, x1)]
        top = a + (b - a) * tx[None, :]
        bot = c + (d - c) * tx[None, :]
        total += amp * (top + (bot - top) * ty[:, None])
        norm += amp
        amp *= persistence
        cy, cx = cy * 2, cx * 2
    return total / norm


def smoothstep(e0: float, e1: float, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def tint(base: np.ndarray, amount) -> np.ndarray:
    """Scales a colour by (1 + amount): broad value shifts."""
    return base[None, None, :] * (1.0 + amount)[..., None]


# ---------------------------------------------------------------------------
# Block patterns (ashlar walls, flagstones, planks)


def _rows_layout(total: int, sizes: list[int], rng) -> list[tuple[int, int]]:
    """Splits `total` pixels into consecutive (start, size) runs drawn from `sizes`."""
    out, pos = [], 0
    while pos < total:
        s = int(rng.choice(sizes))
        s = min(s, total - pos)
        if total - pos - s < min(sizes) // 2 and total - pos - s > 0:
            s = total - pos  # absorb a sliver into this run
        out.append((pos, s))
        pos += s
    return out


def blocks(size: int, rows: list[tuple[int, int]], row_joints: list[np.ndarray], seed: int):
    """Per-pixel block id, distance to the nearest joint (pixels) and the
    signed position inside the block (-1..1 in x and y) for a running-bond
    pattern: `rows` (start, height) bottom to top, `row_joints[r]` the sorted
    x positions of vertical joints in row r (wrapping)."""
    ids = np.zeros((size, size), dtype=np.int32)
    dist = np.zeros((size, size), dtype=np.float32)
    fx = np.zeros((size, size), dtype=np.float32)
    fy = np.zeros((size, size), dtype=np.float32)
    xs = np.arange(size, dtype=np.float32)
    nid = 0
    for r, (start, height) in enumerate(rows):
        joints = np.asarray(row_joints[r], dtype=np.float32)
        ext = np.concatenate([joints - size, joints, joints + size])
        k = np.searchsorted(ext, xs, side="right")
        left, right = ext[k - 1], ext[k]
        bid = (k - 1) % len(joints) + nid
        dx = np.minimum(xs - left, right - xs)
        cxr = (xs - left) / np.maximum(right - left, 1) * 2 - 1
        ys = np.arange(height, dtype=np.float32)
        dy = np.minimum(ys + 0.5, height - ys - 0.5)
        cyr = (ys + 0.5) / height * 2 - 1
        sl = slice(start, start + height)
        ids[sl] = bid[None, :]
        dist[sl] = np.minimum(dx[None, :], dy[:, None])
        fx[sl] = cxr[None, :]
        fy[sl] = cyr[:, None]
        nid += len(joints)
    return ids, dist, fx, fy


def _running_bond(size: int, row_px: list[int], block_px: tuple[int, int], seed: int):
    rng = np.random.default_rng(seed)
    rows = _rows_layout(size, row_px, rng)
    joints = []
    for _ in rows:
        xs, x = [], float(rng.uniform(0, size))
        while len(xs) == 0 or x < xs[0] + size - block_px[0]:
            xs.append(x % size)
            x += rng.uniform(*block_px)
        joints.append(np.sort(np.array(xs)))
    return rows, joints


def masonry(size: int, base: str, mortar: str, row_px: list[int], block_px: tuple[int, int], seed: int,
            variation: float = 0.12, joint_px: float = 2.5, bevel_px: float = 6.0, grime: float = 0.0) -> np.ndarray:
    """Stone blocks in running bond with soft bevels (ashlar, vaults, flags)."""
    rows, joints = _running_bond(size, row_px, block_px, seed)
    ids, dist, fx, fy = blocks(size, rows, joints, seed)
    rng = np.random.default_rng(seed + 1)
    # Anime look: each block one clean tone (stepped, so neighbours differ
    # by a visible but gentle amount), a large soft value drift across the
    # tile, no fine mottling.
    per_block = np.round(rng.normal(0.0, variation * 0.6, ids.max() + 1) / 0.03) * 0.03
    per_block = per_block.astype(np.float32)
    warm = rng.normal(0.0, variation * 0.15, ids.max() + 1).astype(np.float32)
    broad = periodic_noise(size, size, (2, 2), seed + 2, 2) - 0.5
    shade = per_block[ids] + broad * 0.08
    # Light from the upper left: a crisp lit band on top-left edges and a
    # crisp shade band on bottom-right edges (painted, not bevelled).
    edge = 1.0 - smoothstep(bevel_px * 0.45, bevel_px * 0.7, dist)
    light = np.clip((-fx + fy) * 3.0, -1.0, 1.0) * edge * 0.12
    col = tint(hex_rgb(base), shade + light)
    col[..., 0] *= 1.0 + warm[ids]
    col[..., 2] *= 1.0 - warm[ids]
    if grime:
        rim = smoothstep(0.0, bevel_px * 2, dist)
        col *= (1.0 - grime * (1.0 - rim))[..., None]
    joint = smoothstep(joint_px * 0.5, joint_px * 1.5, dist)[..., None]
    m = hex_rgb(mortar)[None, None, :]
    return rgba(col * joint + m * (1.0 - joint))


def planks(size: int, base: str, planks_per_tile: int, seed: int, variation: float = 0.10,
           joint_dark: float = 0.45, grain: float = 0.16) -> np.ndarray:
    """Floorboards running along v (rows of the image), staggered end joints."""
    rng = np.random.default_rng(seed)
    w = size // planks_per_tile
    col = np.zeros((size, size, 3), dtype=np.float32)
    # Grain streaks run along the boards (along v, the image rows).
    streak = periodic_noise(size, size, (3, 64), seed + 1, 3)
    knots = periodic_noise(size, size, (8, 8), seed + 2, 2)
    base_c = hex_rgb(base)
    ys = np.arange(size)
    for p in range(planks_per_tile):
        x0, x1 = p * w, (p + 1) * w if p < planks_per_tile - 1 else size
        # Two boards per column, end joints at a random height.
        cut = int(rng.uniform(0.2, 0.8) * size)
        t_a, t_b = rng.normal(0, variation, 2)
        tone = np.where(((ys - cut) % size) < size // 2, t_a, t_b).astype(np.float32)
        joint_y = np.minimum(np.abs(ys - cut), np.abs(ys - (cut + size // 2) % size))
        sl = slice(x0, x1)
        local = np.arange(x1 - x0, dtype=np.float32)
        dx = np.minimum(local + 0.5, (x1 - x0) - local - 0.5)
        # A faint long grain only (the anime look keeps boards flat).
        shade = tone[:, None] * 0.7 + (streak[:, sl] - 0.5) * grain * 0.5 + (knots[:, sl] - 0.5) * 0.02
        edge = smoothstep(0.0, 2.5, dx)[None, :] * smoothstep(0.0, 2.0, joint_y.astype(np.float32))[:, None]
        hl = (1.0 - smoothstep(0.0, 5.0, dx))[None, :] * 0.06 * np.where(local < (x1 - x0) / 2, 1.0, -1.0)[None, :]
        c = base_c[None, None, :] * (1.0 + shade + hl)[..., None]
        col[:, sl] = c * (joint_dark + (1 - joint_dark) * edge[..., None])
    return rgba(col)


def wood_grain(size: int, base: str, seed: int, contrast: float = 0.18) -> np.ndarray:
    """Plain long-grain wood for beams, doors and furniture; the grain runs
    along u (the image's x)."""
    streak = periodic_noise(size, size, (48, 4), seed, 4)
    ring = np.sin((streak * 18.0 + periodic_noise(size, size, (6, 6), seed + 1, 2) * 3.0) * np.pi)
    broad = periodic_noise(size, size, (3, 3), seed + 2, 2) - 0.5
    shade = (streak - 0.5) * contrast * 0.5 + ring * contrast * 0.06 + broad * 0.08
    return rgba(tint(hex_rgb(base), shade))


def plaster(size: int, base: str, seed: int, contrast: float = 0.10) -> np.ndarray:
    broad = periodic_noise(size, size, (3, 3), seed, 4) - 0.5
    mid = periodic_noise(size, size, (10, 10), seed + 1, 2) - 0.5
    return rgba(tint(hex_rgb(base), broad * contrast * 1.1 + mid * contrast * 0.12))


def velvet(size: int, base: str, seed: int, folds: int = 6, depth: float = 0.28) -> np.ndarray:
    """Hanging fabric: vertical folds (light on their left flanks) and sheen."""
    xs = np.arange(size, dtype=np.float32) / size
    wob = periodic_noise(size, size, (2, 6), seed, 2) - 0.5
    phase = (xs[None, :] * folds + wob * 0.6) * 2 * np.pi
    fold = np.sin(phase)
    sheen = np.maximum(np.cos(phase - 0.6), 0.0) ** 6
    shade = fold * depth * 0.5 + sheen * 0.12
    return rgba(tint(hex_rgb(base), shade))


def weave(size: int, base: str, seed: int, threads: int = 64, contrast: float = 0.08) -> np.ndarray:
    """Close woven cloth (linen, upholstery) with a soft broad mottle."""
    xs = np.arange(size, dtype=np.float32)
    wx = np.sin(xs * 2 * np.pi * threads / size)
    wy = np.sin(xs * 2 * np.pi * threads / size)
    cloth = wx[None, :] * wy[:, None]
    broad = periodic_noise(size, size, (4, 4), seed, 3) - 0.5
    return rgba(tint(hex_rgb(base), cloth * contrast * 0.3 + broad * 0.06))


def patchwork(size: int, colours: list[str], seed: int, squares: int = 6) -> np.ndarray:
    """A patchwork quilt: squares of the given colours with darker stitching."""
    rng = np.random.default_rng(seed)
    cell = size // squares
    grid = rng.integers(0, len(colours), (squares, squares))
    pal = np.stack([hex_rgb(c) for c in colours])
    ys, xs = np.mgrid[0:size, 0:size]
    gy, gx = np.minimum(ys // cell, squares - 1), np.minimum(xs // cell, squares - 1)
    col = pal[grid[gy, gx]]
    ly, lx = ys % cell, xs % cell
    d = np.minimum(np.minimum(ly, cell - 1 - ly), np.minimum(lx, cell - 1 - lx)).astype(np.float32)
    puff = smoothstep(0.0, cell * 0.35, d)
    stitch = 1.0 - 0.35 * (1.0 - smoothstep(0.5, 2.0, d))
    shade = (puff - 0.5) * 0.16
    return rgba(col * (1.0 + shade)[..., None] * stitch[..., None])


# ---------------------------------------------------------------------------
# Glass and painted views (emissive in the shells)


def leaded_glass(size: int, glass: str, lead: str, seed: int, diamonds: int = 6) -> np.ndarray:
    """Diamond-paned leaded glass: each pane slightly different, lead cames dark."""
    rng = np.random.default_rng(seed)
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    u = (xs + ys) * diamonds
    v = (xs - ys) * diamonds
    fu, fv = u - np.floor(u), v - np.floor(v)
    d = np.minimum(np.minimum(fu, 1 - fu), np.minimum(fv, 1 - fv))
    pane = (np.floor(u).astype(int) * 31 + np.floor(v).astype(int) * 17) % 97
    jitter = rng.normal(0, 0.06, 97).astype(np.float32)[pane]
    sky = 0.1 * (ys - 0.5)  # a little brighter toward the top
    col = tint(hex_rgb(glass), jitter + sky)
    came = smoothstep(0.012, 0.03, d)[..., None]
    return rgba(col * came + hex_rgb(lead)[None, None, :] * (1 - came))


def lake_view(size: int, seed: int) -> np.ndarray:
    """Moonlit lake water seen through a dungeon window: a soft teal-blue
    lit from above (a calm view, not a green backlight), slow caustics,
    reed silhouettes rising from the bottom."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    deep, shallow = hex_rgb("#0E161B"), hex_rgb("#4C6A76")
    col = deep[None, None, :] + (shallow - deep)[None, None, :] * (ys ** 1.4)[..., None]
    caustic = periodic_noise(size, size, (6, 6), seed, 3)
    caustic = np.abs(caustic - 0.5) * 2
    col *= (1.0 + 0.35 * (1 - smoothstep(0.0, 0.25, caustic)) * ys)[..., None]
    rays = np.sin((xs * 5 + ys * 1.5) * np.pi) * 0.5 + 0.5
    col *= (1.0 + 0.15 * rays * ys)[..., None]
    weeds = periodic_noise(size, size, (1, 24), seed + 1, 2)
    height = 0.15 + 0.35 * weeds
    weed = smoothstep(0.02, 0.0, ys - height) * smoothstep(0.35, 0.6, periodic_noise(size, size, (2, 40), seed + 2, 1))
    col = col * (1 - weed[..., None] * 0.75)
    return rgba(col)


def garden_view(size: int, seed: int) -> np.ndarray:
    """Through a round burrow window at ground level: grass stems and
    dandelions against a bright sky."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    sky_lo, sky_hi = hex_rgb("#E9F0DA"), hex_rgb("#9CC6E8")
    col = sky_lo[None, None, :] + (sky_hi - sky_lo)[None, None, :] * smoothstep(0.3, 1.0, ys)[..., None]
    blades = periodic_noise(size, size, (1, 48), seed, 2)
    top = 0.32 + 0.25 * blades
    grass = smoothstep(0.01, -0.01, ys - top)
    green = hex_rgb("#5E8F34")[None, None, :] * (0.75 + 0.5 * ys[..., None])
    col = col * (1 - grass[..., None]) + green * grass[..., None]
    rng = np.random.default_rng(seed + 1)
    for _ in range(9):
        cx, cy, r = rng.uniform(0.05, 0.95), rng.uniform(0.25, 0.5), rng.uniform(0.018, 0.03)
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        flower = smoothstep(r, r * 0.6, d)
        col = col * (1 - flower[..., None]) + hex_rgb("#F2C230")[None, None, :] * flower[..., None]
    return rgba(col)


def star_dome(size: int, base: str, star: str, seed: int, count: int = 220) -> np.ndarray:
    """A painted night sky for a dome: deep blue with soft gold stars."""
    rng = np.random.default_rng(seed)
    broad = periodic_noise(size, size, (3, 3), seed, 3) - 0.5
    col = tint(hex_rgb(base), broad * 0.18)
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    s = hex_rgb(star)
    for _ in range(count):
        cx, cy = rng.uniform(0, size), rng.uniform(0, size * 0.92)
        r = rng.choice([1.2, 1.6, 2.4, 3.5], p=[0.5, 0.3, 0.15, 0.05])
        x0, x1 = int(max(0, cx - r * 3)), int(min(size, cx + r * 3 + 1))
        y0, y1 = int(max(0, cy - r * 3)), int(min(size, cy + r * 3 + 1))
        d = np.sqrt((xs[y0:y1, x0:x1] - cx) ** 2 + (ys[y0:y1, x0:x1] - cy) ** 2)
        a = smoothstep(r, r * 0.3, d)[..., None]
        col[y0:y1, x0:x1] = col[y0:y1, x0:x1] * (1 - a) + s[None, None, :] * a
    return rgba(col)


def landscape(w: int, h: int, seed: int) -> np.ndarray:
    """A small painted Highland landscape: sky, distant hills, a loch."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    ys /= h
    xs /= w
    sky_lo, sky_hi = hex_rgb("#E8D9B8"), hex_rgb("#7E9DB8")
    col = sky_lo[None, None, :] + (sky_hi - sky_lo)[None, None, :] * smoothstep(0.45, 1.0, ys)[..., None]
    clouds = periodic_noise(h, w, (3, 6), seed, 3)
    col = col * (1 + 0.12 * smoothstep(0.55, 0.75, clouds)[..., None] * (ys > 0.6)[..., None])
    for k, (hexc, base_y, amp, freq) in enumerate(
        (("#6E7E8E", 0.52, 0.12, 2.0), ("#4E6447", 0.42, 0.1, 3.0), ("#3C4E32", 0.30, 0.06, 5.0))
    ):
        ridge = base_y + amp * (np.sin(xs * freq * np.pi + k) * 0.6 + (periodic_noise(h, w, (1, 8), seed + k, 2) - 0.5))
        mask = (ys < ridge).astype(np.float32)
        col = col * (1 - mask[..., None]) + hex_rgb(hexc)[None, None, :] * mask[..., None]
    loch = (ys < 0.22) & (ys > 0.10)
    col[loch] = col[loch] * 0.3 + hex_rgb("#5E7F98") * 0.7
    shore = ys <= 0.10
    col[shore] = hex_rgb("#5E6B3A") * (0.9 + 0.2 * periodic_noise(h, w, (4, 8), seed + 7, 2)[shore][:, None])
    return rgba(col)


def book_spines(w: int, h: int, seed: int) -> np.ndarray:
    """Rows of book spines in varied leather colours with gilt bands."""
    rng = np.random.default_rng(seed)
    colours = ["#5A1A24", "#1F2E5A", "#2F4A2F", "#4E2A20", "#6B4A2A", "#3A3837", "#7A4A2A", "#2D6E73"]
    col = np.zeros((h, w, 3), dtype=np.float32)
    x = 0
    while x < w:
        bw = int(rng.integers(max(2, w // 28), max(3, w // 12)))
        c = hex_rgb(colours[int(rng.integers(0, len(colours)))]) * rng.uniform(0.85, 1.15)
        col[:, x : x + bw] = c
        col[:, x : x + 1] *= 0.6
        for band in (0.15, 0.85):
            y = int(band * h)
            col[y : y + max(1, h // 40), x : x + bw] = hex_rgb("#C9A13B")
        x += bw
    return rgba(col)


def flat(size: int, hexc: str, noise: float = 0.04, seed: int = 0) -> np.ndarray:
    n = periodic_noise(size, size, (4, 4), seed, 2) - 0.5
    return rgba(tint(hex_rgb(hexc), n * noise * 0.6))


def flame(size: int) -> np.ndarray:
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    d = np.sqrt(((xs - 0.5) * 2.2) ** 2 + ((ys - 0.35) * 1.3) ** 2)
    core = smoothstep(0.9, 0.0, d)
    col = hex_rgb("#FF8A2A")[None, None, :] * (1 - core[..., None]) + hex_rgb("#FFF0B0")[None, None, :] * core[..., None]
    return rgba(col)


def leaf(size: int, base: str, seed: int) -> np.ndarray:
    """A neutral leaf surface (dyed green by the item's primary): a lit
    midrib along v, side veins sloping out to the edges, soft mottling and
    a darker rim. Mapped 0..1 over each leaf or leaflet."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    u = xs - 0.5
    mottle = periodic_noise(size, size, (4, 4), seed, 3) - 0.5
    rib = 1.0 - smoothstep(0.0, 0.035, np.abs(u))
    veins = 0.5 + 0.5 * np.cos((ys * 9.0 - np.abs(u) * 6.0) * 2 * np.pi)
    vein = smoothstep(0.85, 1.0, veins) * smoothstep(0.03, 0.12, np.abs(u))
    rim = smoothstep(0.32, 0.5, np.abs(u))
    shade = mottle * 0.06 + rib * 0.14 + vein * 0.08 - rim * 0.12 + (ys - 0.5) * 0.08
    return rgba(tint(hex_rgb(base), shade))


def brew(size: int, seed: int) -> np.ndarray:
    """A bubbling potion brew seen from above: a swirl of green and gold with
    pale bubbles (also the cauldron's emissive glow)."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    dx, dy = xs - 0.5, ys - 0.5
    r = np.sqrt(dx * dx + dy * dy)
    ang = np.arctan2(dy, dx)
    swirl = 0.5 + 0.5 * np.sin(ang * 3.0 + r * 18.0 + periodic_noise(size, size, (3, 3), seed, 2) * 4.0)
    deep, bright = hex_rgb("#2F7A3E"), hex_rgb("#B8E06A")
    col = deep[None, None, :] + (bright - deep)[None, None, :] * (swirl ** 1.5)[..., None]
    rng = np.random.default_rng(seed)
    for _ in range(9):
        cx, cy, br = rng.uniform(0.15, 0.85), rng.uniform(0.15, 0.85), rng.uniform(0.03, 0.07)
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        ring = smoothstep(br, br * 0.7, d) * (1.0 - smoothstep(br * 0.7, br * 0.3, d) * 0.6)
        col = col * (1 - ring[..., None] * 0.6) + hex_rgb("#E8F6C0")[None, None, :] * ring[..., None] * 0.6
    return rgba(col)


def chalkboard(size: int, seed: int) -> np.ndarray:
    """A slate board with smudged chalk diagrams: circles, arrows and lines
    of scribbled notes (shapes only, no lettering)."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    smudge = periodic_noise(size, size, (3, 3), seed, 3)
    col = tint(hex_rgb("#33403A"), (smudge - 0.5) * 0.15)
    chalk = np.zeros((size, size), dtype=np.float32)
    for cx, cy, r in ((0.22, 0.62, 0.13), (0.7, 0.35, 0.09)):
        d = np.abs(np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) - r)
        chalk = np.maximum(chalk, smoothstep(0.012, 0.0, d))
    for row in range(5):
        y = 0.86 - row * 0.07
        x1 = 0.5 + 0.4 * ((seed + row * 7) % 5) / 5
        wiggle = 0.006 * np.sin(xs * 90.0 + row)
        on = (xs > 0.45) & (xs < x1)
        chalk = np.maximum(chalk, smoothstep(0.008, 0.0, np.abs(ys - y - wiggle)) * on)
    arrow = smoothstep(0.01, 0.0, np.abs(ys - (0.62 - (xs - 0.36) * 0.9))) * ((xs > 0.36) & (xs < 0.6))
    chalk = np.maximum(chalk, arrow)
    col = col * (1 - chalk[..., None] * 0.75) + hex_rgb("#D9DCD4")[None, None, :] * chalk[..., None] * 0.75
    return rgba(col)


def to_bytes(img: np.ndarray) -> bytes:
    """8-bit RGBA, rows bottom to top (for tests and PNG writing)."""
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes()


def is_tileable(img: np.ndarray, tol: float = 0.12) -> bool:
    """True if opposite edges match closely (no visible seam when tiled)."""
    dx = np.abs(img[:, 0, :3] - img[:, -1, :3]).mean()
    dy = np.abs(img[0, :, :3] - img[-1, :, :3]).mean()
    inner = np.abs(img[:, 1, :3] - img[:, 0, :3]).mean() + np.abs(img[1, :, :3] - img[0, :, :3]).mean()
    return dx <= max(tol, inner * 3) and dy <= max(tol, inner * 3)


# ---------------------------------------------------------------------------
# The Charms classroom and the duel arena. Saturated, clean
# colours (the stylized look the game moves to), still painted by hand-like
# broad shapes only.


def swatches(size: int, colours: list[str], grid: int = 8) -> np.ndarray:
    """A grid of flat colour swatches (row-major from the bottom left), so
    many small parts can share one material: their UVs point at a swatch
    centre (`swatch_uv`). Each swatch gets a faint vertical shading."""
    col = np.zeros((size, size, 3), dtype=np.float32)
    cell = size // grid
    shade = np.linspace(0.92, 1.06, cell, dtype=np.float32)[:, None, None]
    for i, hexc in enumerate(colours[: grid * grid]):
        x, y = (i % grid) * cell, (i // grid) * cell
        col[y : y + cell, x : x + cell] = hex_rgb(hexc)[None, None, :] * shade
    return rgba(col)


def swatch_uv(index: int, grid: int = 8) -> tuple[float, float]:
    """UV of the centre of swatch `index` in `swatches`."""
    return ((index % grid + 0.5) / grid, (index // grid + 0.5) / grid)


def _stroke(canvas: np.ndarray, xs, ys, pts, width: float) -> None:
    """Draws a polyline (canvas coordinates 0..1) into a coverage mask."""
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        ln2 = dx * dx + dy * dy
        if ln2 < 1e-12:
            continue
        lo_x, hi_x = min(x0, x1) - width * 3, max(x0, x1) + width * 3
        lo_y, hi_y = min(y0, y1) - width * 3, max(y0, y1) + width * 3
        h, w = canvas.shape
        c0, c1 = max(0, int(lo_x * w)), min(w, int(hi_x * w) + 1)
        r0, r1 = max(0, int(lo_y * h)), min(h, int(hi_y * h) + 1)
        if c0 >= c1 or r0 >= r1:
            continue
        px, py = xs[r0:r1, c0:c1], ys[r0:r1, c0:c1]
        t = np.clip(((px - x0) * dx + (py - y0) * dy) / ln2, 0.0, 1.0)
        d = np.sqrt((px - x0 - t * dx) ** 2 + (py - y0 - t * dy) ** 2)
        canvas[r0:r1, c0:c1] = np.maximum(canvas[r0:r1, c0:c1], smoothstep(width, width * 0.35, d))


def chalk_gestures(size: int, gestures: list, seed: int, aspect: float = 2.5) -> np.ndarray:
    """A blackboard with the wand movements of the lesson drawn in chalk:
    each gesture template (`[[x, y], ...]` on the -100..100 pad) in its own
    panel with a dot where it starts and an arrowhead where it ends, under
    a row of scribbled note lines (shapes only, no lettering). `aspect` is
    the board's width / height, so the square texture stretched over the
    board keeps round circles."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    smudge = periodic_noise(size, size, (3, 3), seed, 3)
    col = tint(hex_rgb("#26443A"), (smudge - 0.5) * 0.15)
    chalk = np.zeros((size, size), dtype=np.float32)
    n = max(1, len(gestures))
    panel = 1.0 / n
    for k, pts in enumerate(gestures):
        cx, cy = panel * (k + 0.5), 0.46
        sx = panel * 0.36
        sy = sx * aspect
        path = [(cx + x / 100.0 * sx, cy + y / 100.0 * sy) for x, y in pts]
        _stroke(chalk, xs, ys, path, 0.006)
        # Start dot and arrowhead.
        x0, y0 = path[0]
        d = np.sqrt((xs - x0) ** 2 + ((ys - y0) / aspect) ** 2)
        chalk = np.maximum(chalk, smoothstep(0.012, 0.006, d))
        (xa, ya), (xb, yb) = path[-2], path[-1]
        ang = np.arctan2((yb - ya) / aspect, xb - xa)
        for s in (-1, 1):
            a = ang + np.pi + s * 0.5
            tip = (xb + np.cos(a) * 0.035, yb + np.sin(a) * 0.035 * aspect)
            _stroke(chalk, xs, ys, [(xb, yb), tip], 0.006)
        # A wavy underline with a few dashes (the incantation's syllables).
        under = [(cx - sx + 2 * sx * i / 12, 0.12 + 0.012 * np.sin(i + k)) for i in range(13)]
        _stroke(chalk, xs, ys, under, 0.004)
    for row in range(2):
        y = 0.9 - row * 0.06
        _stroke(chalk, xs, ys, [(0.06, y), (0.44 + 0.1 * row, y + 0.005)], 0.004)
        _stroke(chalk, xs, ys, [(0.56, y), (0.94 - 0.15 * row, y - 0.004)], 0.004)
    col = col * (1 - chalk[..., None] * 0.8) + hex_rgb("#E8ECE2")[None, None, :] * chalk[..., None] * 0.8
    return rgba(col)


def stained_rose(size: int, seed: int, petals: int = 12) -> np.ndarray:
    """A round stained-glass window: a gold star at the centre, rings of
    saturated petals (blues, violets, gold, rose) between dark lead cames."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    dx, dy = xs - 0.5, ys - 0.5
    r = np.sqrt(dx * dx + dy * dy) * 2
    a = (np.arctan2(dy, dx) / (2 * np.pi) + 0.5) * petals
    seg = np.floor(a).astype(int) % petals
    fa = a - np.floor(a)
    palette = [hex_rgb(h) for h in ("#2F5FE0", "#7A3FD0", "#E8B83A", "#D84A7A", "#36A8E0", "#5A48C8")]
    col = np.zeros((size, size, 3), dtype=np.float32)
    rings = [(0.0, 0.22), (0.22, 0.55), (0.55, 0.82), (0.82, 1.0)]
    for i, (r0, r1) in enumerate(rings):
        mask = (r >= r0) & (r < r1)
        for p in range(petals):
            m = mask & (seg == p)
            col[m] = palette[(p + i * 2) % len(palette)]
    # Star at the centre.
    star = r < 0.22 * (0.6 + 0.4 * np.abs(np.cos(np.arctan2(dy, dx) * 4)))
    col[star] = hex_rgb("#F6D86A")
    glow = 1.0 + 0.25 * (1 - smoothstep(0.0, 1.0, r))
    col *= glow[..., None]
    lead = np.zeros((size, size), dtype=np.float32)
    for r0, _ in rings[1:]:
        lead = np.maximum(lead, smoothstep(0.012, 0.004, np.abs(r - r0)))
    spoke = np.minimum(fa, 1 - fa) * (2 * np.pi * np.maximum(r, 0.05) / petals) / 2
    lead = np.maximum(lead, smoothstep(0.008, 0.002, spoke) * (r > 0.22))
    lead = np.maximum(lead, smoothstep(0.97, 1.0, r))
    col = col * (1 - lead[..., None]) + hex_rgb("#2A2430")[None, None, :] * lead[..., None]
    return rgba(col)


def sky_view(size: int, seed: int, night: bool = False) -> np.ndarray:
    """A bright stylized sky seen through tall leaded windows: a clean
    blue gradient with soft white cloud banks low down (or, at `night`, a
    violet dusk with stars), crossed by a light diamond leading."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    if night:
        lo, hi = hex_rgb("#7A5AC8"), hex_rgb("#1C2A6A")
    else:
        lo, hi = hex_rgb("#CFE8FF"), hex_rgb("#3E8EF0")
    col = lo[None, None, :] + (hi - lo)[None, None, :] * smoothstep(0.0, 1.0, ys)[..., None]
    clouds = periodic_noise(size, size, (3, 4), seed, 3)
    bank = smoothstep(0.5, 0.62, clouds + (0.35 - ys) * 0.9)
    cloud_col = hex_rgb("#F4EEFF") if night else hex_rgb("#FFFFFF")
    col = col * (1 - bank[..., None] * (0.25 if night else 0.85)) + cloud_col[None, None, :] * bank[..., None] * (
        0.25 if night else 0.85)
    if night:
        rng = np.random.default_rng(seed)
        for _ in range(40):
            cx, cy = rng.uniform(0, 1), rng.uniform(0.4, 1)
            d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
            col = np.maximum(col, smoothstep(0.006, 0.0, d)[..., None] * hex_rgb("#FFF4C8")[None, None, :])
    u, v = (xs + ys) * 5, (xs - ys) * 5
    fu, fv = u - np.floor(u), v - np.floor(v)
    d = np.minimum(np.minimum(fu, 1 - fu), np.minimum(fv, 1 - fv))
    came = smoothstep(0.01, 0.025, d)[..., None]
    lead = hex_rgb("#3A3A48")
    return rgba(col * (0.35 + 0.65 * came) + lead[None, None, :] * (1 - came) * 0.65)


# ---------------------------------------------------------------------------
# Castle kit M1 (castle_kit/textures.py, castle_kit/trim.py): the shared
# castle library. Painted in metres at a fixed texel density (world-space
# UVs anchored at the origin, docs/art/castle-kit.md), so block joints fall
# on exact metre positions and courses register with world height. Same
# conventions as above: float32 RGBA, sRGB-encoded, row 0 at the bottom.

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
ALBEDO_RANGE = (0.04, 0.85)  # linear luminance, docs/art/style-guide.md


def linear_to_srgb(c):
    c = np.asarray(c, dtype=np.float32)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(np.maximum(c, 0.0031308), 1 / 2.4) - 0.055)


def luminance(img: np.ndarray) -> np.ndarray:
    """Linear relative luminance per pixel of an sRGB-encoded image."""
    return srgb_to_linear(np.clip(img[..., :3], 0.0, 1.0)) @ LUMA


def fit_albedo(img: np.ndarray, lo: float = 0.045, hi: float = 0.835) -> np.ndarray:
    """Keeps every pixel's linear luminance inside [lo, hi] (a little inside
    the style guide's 0.04-0.85, so 8-bit rounding cannot step outside) by
    scaling its linear colour; hue is kept. Returns a new array."""
    out = img.copy()
    lin = srgb_to_linear(np.clip(img[..., :3], 0.0, 1.0))
    y = lin @ LUMA
    scale = np.ones_like(y)
    dark = y < lo
    scale[dark] = lo / np.maximum(y[dark], 1e-6)
    bright = y > hi
    scale[bright] = hi / y[bright]
    lin = lin * scale[..., None]
    # Pure black has no hue to keep: it becomes grey at `lo`.
    lin[y < 1e-5] = lo
    # Very dark, saturated pixels can push a channel past 1 when scaled up:
    # mix those toward grey of the same luminance instead.
    over = lin.max(axis=-1) > 1.0
    if over.any():
        g = (lin[over] @ LUMA)[:, None]
        m = lin[over].max(axis=-1, keepdims=True)
        t = np.clip((m - 1.0) / np.maximum(m - g, 1e-6), 0.0, 1.0)
        lin[over] = lin[over] + (g - lin[over]) * t
    out[..., :3] = linear_to_srgb(np.clip(lin, 0.0, 1.0))
    return out


def half(img: np.ndarray) -> np.ndarray:
    """Exactly half size each side: a 2 x 2 box filter in linear light (the
    `_lo` copies for Low; tiles and strips stay where they were in UV)."""
    h, w, c = img.shape
    if h % 2 or w % 2:
        raise ValueError("half() needs even sizes")
    lin = img.copy()
    lin[..., :3] = srgb_to_linear(np.clip(img[..., :3], 0.0, 1.0))
    q = lin.reshape(h // 2, 2, w // 2, 2, c).mean(axis=(1, 3))
    q[..., :3] = linear_to_srgb(q[..., :3])
    return q.astype(np.float32)


def band(d, width: float):
    """Crisp coverage of a band `width` px wide measured from an edge at
    distance d (px): 1 inside, one pixel of anti-aliasing, 0 beyond."""
    return np.clip(width - d + 0.5, 0.0, 1.0)


def hue_tint(t: np.ndarray, warm=(1.05, 1.0, 0.9), cool=(0.94, 0.975, 1.05)) -> np.ndarray:
    """Per-element RGB multipliers: t > 0 toward a warm (rose-ochre) cast,
    t < 0 toward a cool (lavender-grey) cast, 0 neutral."""
    t = np.asarray(t, dtype=np.float32)[..., None]
    w = np.asarray(warm, dtype=np.float32) - 1.0
    c = np.asarray(cool, dtype=np.float32) - 1.0
    return 1.0 + np.where(t > 0, t * w, -t * c)


def bond_rows(rng, width_m: float, block_m: tuple[float, float], n_rows: int, min_gap: float,
              tries: int = 1000) -> list[np.ndarray]:
    """Vertical joint positions (metres, sorted, in [0, width_m)) for
    `n_rows` courses of running bond that wrap around `width_m`: block
    lengths within `block_m`, and joints kept `min_gap` from the joints of
    the course below and above (the last course also against the first,
    since the tile wraps vertically) where one of `tries` random offsets
    allows it, else the best offset found."""
    lo, hi = block_m

    def course():
        n_lo, n_hi = int(np.ceil(width_m / hi)), int(np.floor(width_m / lo))
        while True:
            n = int(rng.integers(n_lo, n_hi + 1))
            lens = rng.uniform(lo, hi, n)
            lens *= width_m / lens.sum()
            if lens.min() >= lo - 1e-6 and lens.max() <= hi + 1e-6:
                return np.concatenate([[0.0], np.cumsum(lens)[:-1]])

    def gap(a, b):
        d = np.abs(a[:, None] - b[None, :]) % width_m
        return np.minimum(d, width_m - d).min()

    rows = []
    for r in range(n_rows):
        best, best_gap = None, -1.0
        for _ in range(tries):
            j = np.sort((course() + rng.uniform(0, width_m)) % width_m)
            g = gap(j, rows[-1]) if rows else width_m
            if r == n_rows - 1 and rows:
                g = min(g, gap(j, rows[0]))
            if g > best_gap:
                best, best_gap = j, g
            if g >= min_gap:
                break
        rows.append(best)
    return rows


def block_map(w: int, h: int, ppm: float, rows_m: list[tuple[float, float]], joints_m: list[np.ndarray],
              width_m: float) -> dict:
    """Per-pixel block layout of courses `rows_m` ((y0, y1) metres, bottom
    up, covering the image) with vertical joints `joints_m[r]` (wrapping at
    `width_m`): block `id` (a block straddling the wrap keeps one id, so
    anything painted per block tiles), the distances in px from each
    pixel centre to the block's left, right, bottom and top joint lines, and
    the position inside the block (`fx`, `fy` in -1..1, `bw`, `bh` its size
    in px), plus `row` and the number of blocks `n`."""
    xs = (np.arange(w, dtype=np.float64) + 0.5) / ppm
    ys = (np.arange(h, dtype=np.float64) + 0.5) / ppm
    out = {k: np.zeros((h, w), dtype=np.float32) for k in ("dl", "dr", "db", "dt", "fx", "fy", "bw", "bh")}
    ids = np.zeros((h, w), dtype=np.int32)
    row_of = np.zeros((h, w), dtype=np.int32)
    nid = 0
    for r, (y0, y1) in enumerate(rows_m):
        sel = np.nonzero((ys >= y0) & (ys < y1))[0]
        if not len(sel):
            continue
        j = np.asarray(joints_m[r], dtype=np.float64)
        ext = np.concatenate([j - width_m, j, j + width_m])
        k = np.searchsorted(ext, xs, side="right")
        left, right = ext[k - 1], ext[k]
        sl = slice(sel[0], sel[-1] + 1)
        ids[sl] = ((k - 1) % len(j) + nid)[None, :]
        row_of[sl] = r
        yy = ys[sel]
        out["dl"][sl] = ((xs - left) * ppm)[None, :]
        out["dr"][sl] = ((right - xs) * ppm)[None, :]
        out["db"][sl] = ((yy - y0) * ppm)[:, None]
        out["dt"][sl] = ((y1 - yy) * ppm)[:, None]
        out["fx"][sl] = ((xs - left) / (right - left) * 2 - 1)[None, :]
        out["fy"][sl] = ((yy - y0) / (y1 - y0) * 2 - 1)[:, None]
        out["bw"][sl] = ((right - left) * ppm)[None, :]
        out["bh"][sl] = (y1 - y0) * ppm
        nid += len(j)
    out["id"] = ids
    out["row"] = row_of
    out["n"] = nid
    return out


def paint_blocks(bm: dict, tones: np.ndarray, mortar: str, seed: int, joint_px: float = 2.5,
                 bands: tuple[float, float, float, float] = (4.0, 3.0, 5.0, 4.0),
                 band_values: tuple[float, float, float, float] = (0.11, 0.07, -0.13, -0.09),
                 patch: float = 0.035, patch_cells: tuple[int, int] = (6, 6), gradient: float = 0.025,
                 chips: float = 0.22, chip_px: tuple[float, float] = (6.0, 14.0)) -> np.ndarray:
    """Stone blocks the anime way: each block one clean tone (`tones[id]`,
    sRGB rgb per block), a soft top-left-to-bottom-right gradient, a few
    flat painted wash patches, crisp painted edge bands (lit on the top and
    left edges, shaded on the bottom and right, mitred at the corners:
    widths and values in `bands` / `band_values` as top, left, bottom,
    right), the odd chipped corner and crisp mortar `joint_px` wide (one
    tone, so the joint rows either side of a tile edge match)."""
    h, w = bm["id"].shape
    ids = bm["id"]
    rng = np.random.default_rng(seed)
    half_j = joint_px / 2.0
    e = np.stack([bm["dt"], bm["dl"], bm["db"], bm["dr"]]) - half_j  # from the mortar's edge
    near = np.argmin(e, axis=0)
    emin = np.take_along_axis(e, near[None], axis=0)[0]
    widths = np.asarray(bands, dtype=np.float32)[near]
    values = np.asarray(band_values, dtype=np.float32)[near]
    edge = band(emin, widths) * values
    # Flat wash patches: a stepped (not gradient) second tone inside blocks.
    noise = periodic_noise(h, w, patch_cells, seed + 3, 2)
    sign = rng.choice(np.array([-1.0, -0.5, 0.6], dtype=np.float32), bm["n"] + 1)
    wash = smoothstep(0.58, 0.62, noise) * sign[ids] * patch
    grad = (bm["fy"] - bm["fx"]) * 0.5 * gradient
    shade = 1.0 + edge + wash + grad
    col = tones[ids] * shade[..., None]
    # Chipped corners: a small mitred triangle at one corner of some blocks.
    has = rng.random(bm["n"] + 1) < chips
    corner = rng.integers(0, 4, bm["n"] + 1)
    size = rng.uniform(*chip_px, bm["n"] + 1).astype(np.float32)
    ex = np.where(np.isin(corner[ids], (0, 3)), bm["dl"], bm["dr"]) - half_j
    ey = np.where(np.isin(corner[ids], (0, 1)), bm["dt"], bm["db"]) - half_j
    chip = has[ids] & (ex + ey < size[ids]) & (ex > -half_j) & (ey > -half_j)
    m = hex_rgb(mortar)
    col[chip] = col[chip] * 0.55 + m * 0.45 * 0.92
    # Mortar: crisp, one tone.
    dmin = np.minimum(np.minimum(bm["dl"], bm["dr"]), np.minimum(bm["db"], bm["dt"]))
    cover = band(dmin, half_j)[..., None]
    return rgba(col * (1 - cover) + m[None, None, :] * cover)


def block_tones(rng, n: int, base: str, steps=(-0.08, -0.04, 0.0, 0.035, 0.07), weights=(0.12, 0.24, 0.3, 0.22, 0.12),
                hue: float = 0.7, accents: tuple[tuple[str, float], ...] = ()) -> np.ndarray:
    """One sRGB colour per block: `base` stepped in value (`steps`), pushed
    warm or cool (up to `hue`), and with probability p replaced by an accent
    colour (e.g. blue-grey flags among buff ones), also stepped."""
    b = hex_rgb(base)
    p = np.asarray(weights) / sum(weights)
    tones = b[None, :] * (1.0 + rng.choice(np.asarray(steps, dtype=np.float32), n, p=p))[:, None]
    tones *= hue_tint(rng.uniform(-hue, hue, n))
    for hexc, p in accents:  # accents keep their own hue (no warm/cool push)
        pick = rng.random(n) < p
        a = hex_rgb(hexc)[None, :] * (1.0 + rng.choice(np.asarray(steps, dtype=np.float32), n)[:, None] * 0.8)
        tones[pick] = a[pick]
    return tones.astype(np.float32)


def ashlar(size: int, base: str, mortar: str, seed: int, tile_m: float = 4.8, course_m: float = 0.6,
           block_m: tuple[float, float] = (0.9, 1.8), hue: float = 0.7, chips: float = 0.22,
           steps=(-0.08, -0.04, 0.0, 0.035, 0.07), weights=(0.12, 0.24, 0.3, 0.22, 0.12)) -> np.ndarray:
    """Ashlar walls (castle_wall_a/b/c): courses of exactly `course_m`, so
    the mortar lines fall on every course boundary from the bottom row (v = 0
    is a course line at y = 0) and register with world height; blocks
    `block_m` long in running bond; one tone per block with warm/cool
    variation; crisp mortar; lit top-left / shaded bottom-right edge bands.
    Tiles in both directions."""
    rng = np.random.default_rng(seed)
    n = int(round(tile_m / course_m))
    rows = [(i * course_m, (i + 1) * course_m) for i in range(n)]
    joints = bond_rows(rng, tile_m, block_m, n, min_gap=0.3)
    bm = block_map(size, size, size / tile_m, rows, joints, tile_m)
    tones = block_tones(rng, bm["n"] + 1, base, steps, weights, hue)
    img = paint_blocks(bm, tones, mortar, seed + 1, chips=chips)
    return fit_albedo(img)


def flagstones(size: int, base: str, joint: str, seed: int, tile_m: float = 4.8,
               courses_m: tuple[float, ...] = (1.2, 0.8, 0.8, 1.2, 0.8), slab_m: tuple[float, float] = (1.0, 2.0),
               accents: tuple[tuple[str, float], ...] = (), hue: float = 0.5) -> np.ndarray:
    """Floor flags in long running courses (`courses_m` along v, which is
    -z on floors) of slabs `slab_m` long; buff slabs with accent slabs
    (blue-grey) mixed in. Tiles in both directions."""
    if abs(sum(courses_m) - tile_m) > 1e-6:
        raise ValueError("flag courses must add up to the tile")
    rng = np.random.default_rng(seed)
    edges = np.concatenate([[0.0], np.cumsum(courses_m)])
    rows = list(zip(edges[:-1], edges[1:]))
    joints = bond_rows(rng, tile_m, slab_m, len(rows), min_gap=0.35)
    bm = block_map(size, size, size / tile_m, rows, joints, tile_m)
    tones = block_tones(rng, bm["n"] + 1, base, (-0.07, -0.03, 0.0, 0.03, 0.06), (0.14, 0.24, 0.26, 0.22, 0.14), hue,
                        accents)
    img = paint_blocks(bm, tones, joint, seed + 1, joint_px=3.0, bands=(3.0, 2.5, 4.0, 3.0),
                       band_values=(0.08, 0.05, -0.1, -0.07), patch=0.03, patch_cells=(5, 5), chips=0.15)
    return fit_albedo(img)


def square_flags(size: int, base: str, joint: str, seed: int, tile_m: float = 4.8, square_m: float = 1.2,
                 split: float = 0.22, accents: tuple[tuple[str, float], ...] = (), hue: float = 0.5) -> np.ndarray:
    """Square flags on a grid (`square_m`, joints on whole multiples from the
    world origin), some squares split into two halves, accent squares mixed
    in. Tiles in both directions."""
    rng = np.random.default_rng(seed)
    n = int(round(tile_m / square_m))
    rows = [(i * square_m, (i + 1) * square_m) for i in range(n)]
    joints = []
    for _ in rows:
        j = [i * square_m for i in range(n)]
        j += [i * square_m + square_m / 2 for i in range(n) if rng.random() < split]
        joints.append(np.sort(np.array(j)))
    bm = block_map(size, size, size / tile_m, rows, joints, tile_m)
    tones = block_tones(rng, bm["n"] + 1, base, (-0.07, -0.03, 0.0, 0.03, 0.06), (0.14, 0.24, 0.26, 0.22, 0.14), hue,
                        accents)
    img = paint_blocks(bm, tones, joint, seed + 1, joint_px=3.0, bands=(3.0, 2.5, 4.0, 3.0),
                       band_values=(0.08, 0.05, -0.1, -0.07), patch=0.03, patch_cells=(5, 5), chips=0.12)
    return fit_albedo(img)
