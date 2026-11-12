"""The castle texture library -> client/public/assets/world/textures/
(textures.json + <texture>.png + <texture>_lo.png), castle kit M1.

Every material of data/art/castle_textures.json up to the current
milestone is painted here, by script and seeded, so the same inputs give
byte-identical PNGs: the tiling stone by `world/paint.py`, the sheets (trim,
wood, glass, banners) by `castle_kit/trim.py`, and the castle_props atlas by
`castle_kit/props_atlas.py`, whose slot table the cells' UVs use. Each
texture is base colour only (+ emissive), since the client's toon converter
keeps nothing else (docs/art/castle-kit.md "Materials", "Face tags and UVs"). Every image also
gets a `_lo` copy at exactly half size for Low.

    blender -b --factory-startup --python tools/blender/castle_kit/textures.py -- \\
        --out client/public/assets/world/textures [--contact .out/contact]

Only numpy is needed, so Blender's own Python runs it too:
`python3.13 -I tools/blender/castle_kit/textures.py --out ...`.
`--contact DIR` also writes review sheets: every texture tiled 3 x 3.

Castle kit M4 (docs/art/castle-kit.md "Textures"): every PNG listed in
textures.json is also encoded to KTX2 (`<stem>.ktx2`, `<stem>_lo.ktx2`)
with basisu (`$BASISU`, default /opt/homebrew/bin/basisu), ETC1S or UASTC
per castle_textures.json `materials.<m>.ktx2.mode`, full mip chains, rows
top first, byte-identical for identical PNGs. The PNGs stay the source and
the fallback; without basisu the library is PNG only (a warning, no KTX2
fields). `--no-ktx2` skips the encoding.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib

import numpy as np

_BLENDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORLD = os.path.join(_BLENDER, "world")
for _p in (_WORLD, _BLENDER):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from check_layout import script_args  # noqa: E402

import paint  # noqa: E402
from castle_kit import grid, portraits, props_atlas, tapestries, trim  # noqa: E402

VERSION = 1
MILESTONE = 7  # castle kit M3: castle_sky (the sky_clouds painter); M4: the portrait sheets;
# castle M5a: castle_tapestry (the tapestry sheet); castle M5b: the dungeon wall and floor (castle_wall_d,
# castle_floor_dungeon)
REPO = os.path.abspath(os.path.join(_BLENDER, "..", ".."))
SPEC_PATH = os.path.join(REPO, "data", "art", "castle_textures.json")
DEFAULT_OUT = os.path.join(REPO, "client", "public", "assets", "world", "textures")


# -- the painters: texture name -> f(spec) -> {image name: RGBA array} -------------------
#
# Colours follow world/palette.py CASTLE: walls the cream sandstone
# `stone` (#E4D2B0) and two neighbours (a rosier gold, a cooler cream),
# floors `flagstone` / `flagstone_corridor` with a few blue-grey slabs,
# vault webs a warm stone a step darker than the walls.


def _ashlar(base, mortar, seed, **kw):
    return lambda spec: paint.ashlar(1024, base, mortar, seed, **kw)


PAINTERS = {
    "stone_ashlar_a": _ashlar("#E4D2B0", "#AE997C", 1101),
    "stone_ashlar_b": _ashlar("#E5CDA8", "#AC9476", 1201, block_m=(1.0, 1.8), hue=0.8),
    # Castle M5a (docs/art/castle-kit.md "Textures"): a step darker for the Grand Staircase
    # (each sRGB channel x 0.88 of #DED3BB / #A89C87: the same hue, linear luminance 0.657 -> 0.494).
    "stone_ashlar_c": _ashlar("#C3BAA5", "#948977", 1301, block_m=(1.2, 1.8), hue=0.6),
    "stone_flag": lambda spec: paint.flagstones(1024, "#B99A78", "#75614F", 2101, accents=(("#9C979B", 0.18),)),
    # 0.8 m squares (36 to a repeat, not 16) and three soft accents rather
    # than one strong blue-grey: with 1.2 m squares the few blue flags of a
    # repeat lined up into lanes down the corridors every 4.8 m.
    "stone_flag_square": lambda spec: paint.square_flags(1024, "#AA9076", "#6F5D4C", 2201, square_m=0.8, split=0.12,
                                                         hue=0.7, accents=(("#A09A9E", 0.12), ("#9E8B7B", 0.16),
                                                                           ("#B59C80", 0.14))),
    "vault_web": lambda spec: paint.ashlar(512, "#CDB592", "#9E8770", 3101, tile_m=2.4, course_m=0.3,
                                           block_m=(0.45, 0.9), chips=0.12),
    "wood_sheet": lambda spec: trim.paint_wood(spec["sheets"]["wood_sheet"]),
    "trim_castle": lambda spec: trim.paint_trim(spec["sheets"]["trim_castle"]),
    "glass_sheet": lambda spec: trim.paint_glass(spec["sheets"]["glass_sheet"]),
    "banners": lambda spec: trim.paint_banners(spec["sheets"]["banners"]),
    # Castle kit M3 (the Great Hall): the enchanted ceiling's data texture.
    "sky_clouds": lambda spec: paint_sky_clouds(spec["materials"]["castle_sky"]["px"]),
    # Castle kit M4 (the Grand Staircase): invented portraits, the frames in palette names.
    "portraits_a": lambda spec: portraits.paint_sheet("portraits_a", spec.get("_path", SPEC_PATH)),
    "portraits_b": lambda spec: portraits.paint_sheet("portraits_b", spec.get("_path", SPEC_PATH)),
    # Castle M5a (the corridors): four invented tapestries in the palette's tapestry_* names.
    "tapestries": lambda spec: tapestries.paint_sheet(spec["sheets"]["tapestries"], spec.get("_path", SPEC_PATH)),
    # Castle M5b (Dungeons, docs/art/castle-kit.md "Textures"): the dungeon stone of the west basement
    # (world/palette.py CASTLE stone_dungeon, a cool green-grey, linear luminance 0.25) and its
    # flags (flagstone_dungeon, 0.14): dark but never black, the toon ramp and the AO darken them.
    "stone_ashlar_dungeon": _ashlar("#7E8C84", "#5F6B64", 1401, block_m=(1.2, 1.8)),
    "stone_flag_dungeon": lambda spec: paint.flagstones(1024, "#646B66", "#454A47", 2401),
    # Castle M5c (Facades, docs/art/castle-kit.md "Textures"): the roofs' coursed slates (world/palette.py
    # CASTLE roof, the clear slate blue, linear luminance 0.13; joints each sRGB channel x 0.74).
    "roof_slate": lambda spec: paint_roof_slate("#4C6596", "#384B6F", 5101),
    # Castle M5c (Courtyard, docs/art/castle-kit.md "Textures"): the courtyard's paving, setts in 0.48 m courses
    # (smaller than the halls' flags, so the outside reads apart) from world/palette.py CASTLE
    # flagstone_courtyard (linear luminance 0.48), joints each sRGB channel x 0.70.
    "stone_paving_court": lambda spec: paint.flagstones(1024, "#C4B79E", "#89806F", 2501, courses_m=(0.48,) * 10,
                                                        slab_m=(0.45, 0.9)),
}
# The paddock's turf (castle_floor_grass), from world/palette.py CASTLE grass and plant.
PAINTERS["grass_turf"] = lambda spec: __import__("castle_kit.pieces.paddock", fromlist=["turf"]).turf(1024)
# Vegetation look A (world/foliage.py): the trees' and hedges' atlas, its colours palette names.
PAINTERS["foliage_atlas"] = lambda spec: __import__("foliage").paint_atlas(spec.get("_path", SPEC_PATH))


# -- castle kit M3: sky_clouds (docs/art/castle-kit.md) ---------------------------------
#
# A data texture, not an albedo: R = G = B = cloud cover (0 clear .. 1 full
# cloud), A = the star field (0..1). Its colours come from the lighting data
# on the client (horizon to zenith, sun or moon on the clouds, stars by
# night), so a palette change never repaints it. u runs along the hall and
# tiles; v is 0 at the wall plate and 1 at the ridge.

SKY_SEED = 3301
SKY_CLOUD_COVER = 0.48  # the share of the sky under cloud at the plate, before the ridge's quiet band
SKY_RIDGE_QUIET = 0.14  # the top rows (v > 1 - this) fade to clear sky, so the two pitches meet cleanly
SKY_STARS = 900  # stars over the whole texture (more toward the ridge)


def paint_sky_clouds(px, seed: int = SKY_SEED) -> np.ndarray:
    """sky_clouds (1024 x 512): soft-edged drifting clouds tiling in u
    (periodic noise stretched along the hall), thinning toward the ridge
    and clear in its top rows; a star field in alpha, small points that
    thicken toward the ridge. Rows bottom to top (v = 0 first), seeded."""
    w, h = int(px[0]), int(px[1])
    rng = np.random.default_rng(seed)
    # Cells so that clouds come out about 6 m along the hall and 3 m across
    # the slope (the texture spans 66 m in u and about 16 m of pitch in v).
    base = paint.periodic_noise(h, w, (5, 11), seed, octaves=4, persistence=0.5)
    # Domain warp: billowy edges instead of the lattice's blocks.
    wx = paint.periodic_noise(h, w, (4, 8), seed + 2, octaves=2) - 0.5
    wy = paint.periodic_noise(h, w, (4, 8), seed + 3, octaves=2) - 0.5
    yy, xx = np.mgrid[0:h, 0:w]
    sx = ((xx + wx * 70.0).astype(np.int64)) % w
    sy = np.clip((yy + wy * 40.0).astype(np.int64), 0, h - 1)
    n = base[sy, sx]
    v = (np.arange(h, dtype=np.float32) + 0.5) / h
    # Thinning toward the ridge, and the quiet band at the top.
    thin = (1.0 - 0.3 * v) * paint.smoothstep(1.0, 1.0 - SKY_RIDGE_QUIET, v)
    level = float(np.quantile(n, 1.0 - SKY_CLOUD_COVER))
    # Crisp painted edges and two values (toon-friendly): a body and its lit top.
    body = paint.smoothstep(level, level + 0.035, n)
    core = paint.smoothstep(level + 0.07, level + 0.1, n)
    cover = np.clip((0.72 * body + 0.28 * core) * thin[:, None], 0.0, 1.0).astype(np.float32)
    stars = np.zeros((h, w), dtype=np.float32)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    for _ in range(SKY_STARS):
        vy = float(np.sqrt(rng.random()))  # denser toward the ridge
        cy = vy * (h - 4) + 2.0
        cx = float(rng.uniform(0, w))
        r = float(rng.choice([0.7, 1.0, 1.4, 2.0], p=[0.45, 0.32, 0.17, 0.06]))
        b = float(rng.uniform(0.45, 1.0))
        y0, y1 = int(max(0, cy - 3 * r)), int(min(h, cy + 3 * r + 1))
        for off in (-w, 0, w):  # wrap in u
            x0, x1 = int(max(0, cx + off - 3 * r)), int(min(w, cx + off + 3 * r + 1))
            if x1 <= x0:
                continue
            d = np.sqrt((xs[y0:y1, x0:x1] - cx - off) ** 2 + (ys[y0:y1, x0:x1] - cy) ** 2)
            stars[y0:y1, x0:x1] = np.maximum(stars[y0:y1, x0:x1], b * paint.smoothstep(r, r * 0.25, d))
    img = np.zeros((h, w, 4), dtype=np.float32)
    img[..., 0] = img[..., 1] = img[..., 2] = cover
    img[..., 3] = stars
    return img


def paint_props(spec_path: str = SPEC_PATH):
    """(base, emissive, key) of the shared castle_props atlas: painted only
    through props_atlas.build(), so its slots are the ones the cells' UVs
    point at. The base gets the albedo range like every library texture
    (the unused slots and the near-black `dark` tile lift a little), except
    the glowing tiles: their base is kit.GLOW_BASE (8 %) of the glow colour
    (castle kit M2, the glow rule: the albedo floor would lift it to about
    9 % for the lantern glow), never seen without its emission. The
    emissive stays as painted (black except the flames, brews and glows)."""
    _, atlas = props_atlas.build(textures_path=spec_path)
    base, glow = atlas.paint()
    fitted = paint.fit_albedo(base)
    keep = atlas.glow_mask()  # castle kit M3 contracts: PropsAtlas.glow_mask (M2 open item 4)
    fitted[keep] = base[keep]
    return fitted, glow, atlas.key()


def load_spec(path: str = SPEC_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def library(spec: dict, milestone: int = MILESTONE):
    """Paints every material up to `milestone`: ({image stem: RGBA array},
    {material: textures.json entry}). Images are full size; `_lo` copies
    are made when writing."""
    images: dict[str, np.ndarray] = {}
    entries: dict[str, dict] = {}
    for name, m in sorted(spec["materials"].items()):
        if m["milestone"] > milestone:
            continue
        tex = m["texture"]
        entry = {"base": f"{tex}.png", "lo": f"{tex}_lo.png", "px": list(m["px"])}
        if m["map"] == "atlas":
            if tex not in images:
                base, glow, key = paint_props(spec.get("_path", SPEC_PATH))
                images[tex] = base
                if m.get("emissive") not in (None, "self"):
                    # A glow mask needs little resolution: stored at half the
                    # atlas (512, `_lo` 256), the same UVs. Saves 4 MB of GPU
                    # memory in every scene that shows the props.
                    images[m["emissive"]] = paint.half(glow)
                entry["key"] = key
            else:
                raise ValueError(f"{name}: two atlas materials share {tex}")
        else:
            if tex not in PAINTERS:
                if m["milestone"] > 1:
                    # A later milestone's texture whose painter has not
                    # merged yet: the client keeps the placeholder colour
                    # (docs/art/castle-kit.md "Textures").
                    print(f"textures: warning: {name}: no painter for {tex!r} yet, left out")
                    continue
                raise KeyError(f"{name}: no painter for texture {tex!r}")
            if tex not in images:
                images[tex] = PAINTERS[tex](spec)
        h, w = images[tex].shape[:2]
        if [w, h] != list(m["px"]):
            raise ValueError(f"{name}: painted {w} x {h}, the spec says {m['px']}")
        em = m.get("emissive")
        if em == "self":
            entry["emissive"], entry["emissive_lo"] = entry["base"], entry["lo"]
        elif em:
            entry["emissive"], entry["emissive_lo"] = f"{em}.png", f"{em}_lo.png"
            eh, ew = images[em].shape[:2]
            if [ew, eh] != list(m["px"]):
                entry["emissive_px"] = [ew, eh]
        if "emissive_strength" in m:
            entry["emissive_strength"] = m["emissive_strength"]
        if m.get("unlit"):
            entry["unlit"] = True
        if m.get("alpha", "opaque") != "opaque":
            entry["alpha"] = m["alpha"]
            if m["alpha"] == "mask":
                entry["alpha_cutoff"] = m.get("alpha_cutoff", 0.5)
        if m.get("double_sided"):
            entry["double_sided"] = True
        if m.get("shared_normals"):  # vegetation look A: both faces light with the authored normal
            entry["shared_normals"] = True
        if m.get("medium", "full") not in ("full", "lo"):
            raise ValueError(f"{name}: medium must be full or lo, not {m['medium']!r}")
        if m.get("medium") == "lo":
            entry["medium"] = "lo"
        if m.get("tint"):
            entry["tint"] = m["tint"]
        entries[name] = entry
    return images, entries


# -- PNG (deterministic: no timestamps or text chunks, fixed filter and zlib level) --------


def _paeth_rows(a: np.ndarray) -> bytes:
    """PNG scanlines, every row with the Paeth filter (type 4): the smallest
    files for these flat, banded images, and vectorised over the image."""
    h, w, c = a.shape
    raw = a.reshape(h, w * c).astype(np.int16)
    up = np.vstack([np.zeros((1, w * c), dtype=np.int16), raw[:-1]])
    zero = np.zeros((h, c), dtype=np.int16)
    left = np.hstack([zero, raw[:, :-c]])
    upleft = np.hstack([zero, up[:, :-c]])
    p = left + up - upleft
    pa, pb, pc = np.abs(p - left), np.abs(p - up), np.abs(p - upleft)
    pred = np.where((pa <= pb) & (pa <= pc), left, np.where(pb <= pc, up, upleft))
    rows = ((raw - pred) & 0xFF).astype(np.uint8)
    return np.hstack([np.full((h, 1), 4, dtype=np.uint8), rows]).tobytes()


def png_bytes(img: np.ndarray) -> bytes:
    """8-bit RGBA, sRGB (untagged), top row first, of a float RGBA image in
    paint.py's order (rows bottom to top)."""
    a = np.ascontiguousarray((np.clip(img, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)[::-1])
    h, w, c = a.shape
    if c != 4:
        raise ValueError("png_bytes expects RGBA")

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(_paeth_rows(a), 9)) + chunk(
        b"IEND", b"")


def png_info(data: bytes) -> tuple[int, int, int, int]:
    """(width, height, bit depth, colour type) from a PNG's IHDR."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    w, h, depth, ctype = struct.unpack(">IIBB", data[16:26])
    return w, h, depth, ctype


def decode_png(data: bytes) -> np.ndarray:
    """Decodes an 8-bit RGBA or RGB PNG (any filter) to uint8 [h, w, 4]
    (RGB gets alpha 255), top row first. Pure Python per pixel for Average
    and Paeth rows: for tests on small images."""
    w, h, depth, ctype = png_info(data)
    if depth != 8 or ctype not in (2, 6):
        raise ValueError("expected 8-bit RGBA or RGB")
    c = 4 if ctype == 6 else 3
    pos, idat = 8, b""
    while pos < len(data):
        n = struct.unpack(">I", data[pos : pos + 4])[0]
        if data[pos + 4 : pos + 8] == b"IDAT":
            idat += data[pos + 8 : pos + 8 + n]
        pos += 12 + n
    raw = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(h, 1 + w * c)
    out = np.zeros((h, w * c), dtype=np.uint8)
    prev = [0] * (w * c)
    for y in range(h):
        f, line = raw[y, 0], raw[y, 1:].tolist()
        cur = [0] * (w * c)
        for x in range(w * c):
            a = cur[x - c] if x >= c else 0
            b = prev[x]
            ul = prev[x - c] if x >= c else 0
            if f == 0:
                pr = 0
            elif f == 1:
                pr = a
            elif f == 2:
                pr = b
            elif f == 3:
                pr = (a + b) >> 1
            else:
                pp = a + b - ul
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - ul)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else ul)
            cur[x] = (line[x] + pr) & 0xFF
        out[y] = cur
        prev = cur
    img = out.reshape(h, w, c)
    if c == 3:
        img = np.concatenate([img, np.full((h, w, 1), 255, dtype=np.uint8)], axis=2)
    return img


# -- writing ----------------------------------------------------------------------------


def write_manifest(out_dir: str, materials: dict) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "textures.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "materials": materials}, f, indent=2, sort_keys=True)
        f.write("\n")
    return path


def mip_bytes(w: int, h: int, block_bytes: int | None) -> int:
    """GPU bytes of a full mip chain down to 1 x 1: 4x4-block formats round
    every level up to whole blocks (`block_bytes` per block: 8 for ETC2 RGB /
    BC1, 16 for ASTC 4x4 / BC7 / ETC2 RGBA); None is RGBA8 (4 B/px)."""
    total = 0
    while True:
        total += ((w + 3) // 4) * ((h + 3) // 4) * block_bytes if block_bytes else w * h * 4
        if w == 1 and h == 1:
            return total
        w, h = max(1, w // 2), max(1, h // 2)


def ktx2_block_bytes(mode: str, alpha: bool = False) -> int:
    """Bytes per 4x4 block of the GPU format a KTX2 transcodes to (castle
    kit M4, docs/art/castle-kit.md "Textures"): UASTC to ASTC 4x4 or BC7 (16);
    ETC1S without alpha to ETC2 RGB or BC1 (8, the client's decision tree
    prefers BC1 over BC7 on desktop), with alpha to ETC2 RGBA / BC3 (16)."""
    if mode == "uastc":
        return 16
    if mode == "etc1s":
        return 16 if alpha else 8
    raise ValueError(f"no KTX2 mode {mode!r}")


def gpu_bytes(entries: dict, lo: bool = False, ktx2: bool = False, alpha: frozenset = frozenset()) -> int:
    """Estimated GPU memory of the listed materials' textures, each image
    counted once (castle_glass's emissive is its base image again): the PNG
    path as RGBA8 with a full mip chain (x 4/3); with `ktx2`, the GPU format
    each KTX2 transcodes to (`ktx2_block_bytes`, exact block-rounded mips;
    `alpha`: the image stems whose ETC1S copy carries alpha). Entries
    without KTX2 fields count as RGBA8 either way."""
    seen, total = set(), 0
    for e in entries.values():
        for key in (("lo", "emissive_lo") if lo else ("base", "emissive")):
            f = e.get(key)
            if f and f not in seen:
                seen.add(f)
                w, h = e.get("emissive_px", e["px"]) if key.startswith("emissive") else e["px"]
                if lo:
                    w, h = w // 2, h // 2
                if ktx2 and e.get("ktx2_mode"):
                    total += mip_bytes(w, h, ktx2_block_bytes(e["ktx2_mode"], f[:-4].removesuffix("_lo") in alpha))
                else:
                    total += w * h * 4 * 4 // 3
    return total


# -- KTX2 (castle kit M4; docs/art/castle-kit.md "Textures") --------------------------------------
#
# Every image the manifest lists is encoded from its PNG (the same pixels)
# into `<stem>.ktx2` and `<stem>_lo.ktx2`. The choices, measured on the M1-M3
# library (tools/blender/README.md, "KTX2"):
#
# - Mip chains from basisu with a 2 x 2 box filter on the stored values
#   (`-mip_filter box -mip_linear`): the mips the PNG path gets from the GPU's
#   own generateMipmap on an RGBA8 texture, so KTX2 and PNG agree at every
#   distance (no sRGB-light filtering, no sharper Kaiser kernel; the M2 rib
#   speckle was tuned on box-filtered mips).
# - The KTX2 files keep basisu's default sRGB transfer function (perceptual
#   error metrics). The client still samples them as stored values, as it does
#   the PNGs: Babylon's KTX2 decoder transcodes to UNORM formats only (never an
#   *_SRGB format the GPU would convert), and StandardMaterial reads diffuse and
#   emissive textures without a gamma conversion.
# - ETC1S at quality 100 (`-q 255` in low-level terms; RDO left on: turning it
#   off or raising effort bought < 0.1 dB); UASTC at quality 100 without RDO
#   (RDO lambda 0.5-3 saved only 2-25 % of the file on these flat painted
#   sheets for 5-12 dB of PSNR and 5x the encode time) and Zstandard level 19
#   (6 % smaller than the default 6, same decode cost).
# - Deterministic: the same PNG gives the same bytes at any thread count
#   (checked); UASTC RDO, if a material asks for it, runs single-threaded
#   (`-uastc_rdo_m`). No timestamps are written.

BASISU_DEFAULT = "/opt/homebrew/bin/basisu"
KTX2_THREADS = 3  # one basisu job at a time, 3 threads (the shared machine's rule)
KTX2_COMMON = ("-ktx2", "-mipmap", "-mip_filter", "box", "-mip_linear", "-quiet")
KTX2_TUNING = {  # castle_textures.json materials.<m>.ktx2: keys a material may set next to `mode`
    "etc1s": {"quality": 100, "effort": None},
    "uastc": {"quality": 100, "effort": None, "rdo": None, "zstd": 19},
}
KTX2_KEYS = ("ktx2", "ktx2_lo", "emissive_ktx2", "emissive_ktx2_lo", "ktx2_mode")


def basisu_binary() -> str | None:
    """The basisu to encode with: `$BASISU` (empty: none), else the Homebrew
    default, else `basisu` on PATH; None when it is not an executable file.
    The pinned version is tools/blender/BASISU_VERSION (prepare.mjs warns on
    another; tools/assets/publish.sh refuses)."""
    if "BASISU" not in os.environ and not os.access(BASISU_DEFAULT, os.X_OK):
        return shutil.which("basisu")
    path = os.environ.get("BASISU", BASISU_DEFAULT)
    if not path:
        return None
    found = shutil.which(path) if os.sep not in path else (path if os.access(path, os.X_OK) else None)
    return found


def ktx2_tuning(m: dict) -> tuple[str, dict]:
    """(mode, tuning) of a castle_textures.json material: `ktx2.mode` (one of
    grid.KTX2_MODES, default grid.KTX2_DEFAULT_MODE) and its tuning keys over
    KTX2_TUNING's defaults. Unknown keys are errors."""
    k = dict(m.get("ktx2") or {})
    mode = k.pop("mode", grid.KTX2_DEFAULT_MODE)
    if mode not in grid.KTX2_MODES:
        raise ValueError(f"ktx2 mode {mode!r} is not one of {grid.KTX2_MODES}")
    tuning = dict(KTX2_TUNING[mode])
    for key, v in k.items():
        if key not in tuning:
            raise ValueError(f"ktx2 tuning key {key!r} is not one of {sorted(tuning)} for {mode}")
        tuning[key] = v
    return mode, tuning


def ktx2_args(mode: str, tuning: dict) -> list[str]:
    """basisu options for one mode (without the files): the common mip and
    container options, then the codec's."""
    args = list(KTX2_COMMON) + ["-max_threads", str(KTX2_THREADS)]
    q = int(tuning["quality"])
    if not 1 <= q <= 100:
        raise ValueError(f"ktx2 quality {q} not in 1..100")
    if mode == "etc1s":
        args += ["-etc1s", "-quality", str(q)]
    else:
        args += ["-uastc", "-quality", str(q), "-ktx2_zstandard_level", str(int(tuning["zstd"]))]
        if tuning.get("rdo") is not None:
            args += ["-uastc_rdo_l", str(float(tuning["rdo"]))]
        if q < 100 or tuning.get("rdo") is not None:
            args.append("-uastc_rdo_m")  # RDO single-threaded: deterministic
    if tuning.get("effort") is not None:
        args += ["-effort", str(int(tuning["effort"]))]
    return args


def image_modes(spec: dict, entries: dict) -> dict:
    """{image stem: (mode, tuning)} for every image the manifest lists (base
    and emissive; the `_lo` copies share their stem's). An image two materials
    list must not get two different encodings."""
    out: dict[str, tuple[str, dict]] = {}
    for name, e in sorted(entries.items()):
        mt = ktx2_tuning(spec["materials"][name])
        for key in ("base", "emissive"):
            if key in e:
                stem = e[key][:-4]
                if out.setdefault(stem, mt) != mt:
                    raise ValueError(f"{stem}: listed with two KTX2 encodings ({name})")
    return out


def ktx2_entries(spec: dict, entries: dict) -> dict:
    """The textures.json entries with the KTX2 fields: `ktx2` /
    `ktx2_lo` next to `base` / `lo`, `emissive_ktx2` / `emissive_ktx2_lo` when
    the emissive image is its own (castle_glass's is its base again), and
    `ktx2_mode`."""
    out = {}
    for name, e in entries.items():
        e = dict(e)
        mode, _ = ktx2_tuning(spec["materials"][name])
        e["ktx2"], e["ktx2_lo"] = e["base"][:-4] + ".ktx2", e["lo"][:-4] + ".ktx2"
        if "emissive" in e and e["emissive"] != e["base"]:
            e["emissive_ktx2"], e["emissive_ktx2_lo"] = e["emissive"][:-4] + ".ktx2", e["emissive_lo"][:-4] + ".ktx2"
        e["ktx2_mode"] = mode
        out[name] = e
    return out


def encode_ktx2(binary: str, png_path: str, ktx2_path: str, mode: str, tuning: dict) -> None:
    """One basisu run: `png_path` -> `ktx2_path`. Raises with basisu's output
    on failure. Runs in the output directory (basisu writes nothing else),
    so both paths are made absolute first (prepare.mjs passes a relative
    `--out`)."""
    png_path, ktx2_path = os.path.abspath(png_path), os.path.abspath(ktx2_path)
    tmp = ktx2_path + ".tmp"
    cmd = [binary, *ktx2_args(mode, tuning), png_path, "-output_file", tmp]
    r = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(ktx2_path)), capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(f"basisu failed on {os.path.basename(png_path)} ({r.returncode}):\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    os.replace(tmp, ktx2_path)


KTX2_ID = b"\xabKTX 20\xbb\r\n\x1a\n"


def ktx2_info(data: bytes) -> dict:
    """The header and basic data format descriptor of a KTX2 file: size,
    level count, supercompression (1 BasisLZ = ETC1S, 2 Zstandard), the DFD's
    colour model (163 ETC1S, 166 UASTC), transfer function (1 linear, 2 sRGB)
    and whether it has alpha (ETC1S: a second sample; UASTC: the channel id)."""
    if data[:12] != KTX2_ID:
        raise ValueError("not a KTX2 file")
    vk, _, w, h, _, layers, faces, levels, scheme = struct.unpack_from("<9I", data, 12)
    dfd_off = struct.unpack_from("<I", data, 48)[0]
    model, primaries, transfer, _ = struct.unpack_from("<4B", data, dfd_off + 4 + 8)
    block_size = struct.unpack_from("<H", data, dfd_off + 4 + 6)[0]
    samples = (block_size - 24) // 16
    first_channel = data[dfd_off + 4 + 24 + 3] & 0x0F
    alpha = samples > 1 if model == 163 else first_channel in (3, 15) or samples > 1
    return {"vk_format": vk, "width": w, "height": h, "layers": layers, "faces": faces, "levels": levels,
            "supercompression": scheme, "model": model, "primaries": primaries, "transfer": transfer,
            "samples": samples, "alpha": alpha}


def write_ktx2(out_dir: str, spec: dict, entries: dict, binary: str, quiet: bool = False) -> dict:
    """Encodes every image the manifest lists, and its `_lo`, from the PNGs
    in `out_dir`; returns {file: KTX2 bytes}."""
    sizes = {}
    for stem, (mode, tuning) in sorted(image_modes(spec, entries).items()):
        for suffix in ("", "_lo"):
            png = os.path.join(out_dir, f"{stem}{suffix}.png")
            name = f"{stem}{suffix}.ktx2"
            t = time.time()
            encode_ktx2(binary, png, os.path.join(out_dir, name), mode, tuning)
            sizes[name] = os.path.getsize(os.path.join(out_dir, name))
            if not quiet:
                print(f"  {name:28s} {sizes[name]:9d} B  {mode:5s} {time.time() - t:5.1f} s")
    return sizes


def build(out_dir: str, spec_path: str = SPEC_PATH, milestone: int = MILESTONE, contact: str | None = None,
          quiet: bool = False, ktx2: bool = True) -> dict:
    """Paints and writes the library; returns `sizes` ({file: PNG bytes}),
    `ktx2_sizes` ({file: KTX2 bytes}, empty without basisu or with
    `ktx2=False`), the textures.json `entries` and the painted full-size
    `images`. Stale PNGs and KTX2 files in `out_dir` are removed."""
    t0 = time.time()
    spec = load_spec(spec_path)
    spec["_path"] = spec_path
    images, entries = library(spec, milestone)
    os.makedirs(out_dir, exist_ok=True)
    sizes = {}
    for stem, img in sorted(images.items()):
        for name, arr in ((f"{stem}.png", img), (f"{stem}_lo.png", paint.half(img))):
            data = png_bytes(arr)
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(data)
            sizes[name] = len(data)
    t1 = time.time()
    binary = basisu_binary() if ktx2 else None
    if ktx2 and not binary:
        print(f"textures: warning: no basisu (BASISU={os.environ.get('BASISU', BASISU_DEFAULT)!r}); "
              "writing the PNG library only (no KTX2)")
    ktx2_sizes = write_ktx2(out_dir, spec, entries, binary, quiet) if binary else {}
    if ktx2_sizes:
        entries = ktx2_entries(spec, entries)
    for name in os.listdir(out_dir):
        if (name.endswith(".png") and name not in sizes) or (name.endswith((".ktx2", ".ktx2.tmp")) and name not in ktx2_sizes):
            os.remove(os.path.join(out_dir, name))
    path = write_manifest(out_dir, entries)
    if contact:
        contact_sheets(images, contact)
    if not quiet:
        for name, n in sorted(sizes.items()):
            print(f"  {name:28s} {n:9d} B")
        print(f"textures: {len(entries)} materials, {len(sizes)} PNGs, {sum(sizes.values()) / 1e6:.2f} MB on disk, "
              f"GPU {gpu_bytes(entries) / 2 ** 20:.1f} MiB (High/Medium), {gpu_bytes(entries, True) / 2 ** 20:.1f} MiB "
              f"(Low), {time.time() - t0:.1f} s -> {path}")
        if ktx2_sizes:
            alpha = frozenset(s for s, img in images.items() if np.any(img[..., 3] < 1.0))
            print(f"textures: {len(ktx2_sizes)} KTX2, {sum(ktx2_sizes.values()) / 1e6:.2f} MB on disk, "
                  f"GPU {gpu_bytes(entries, ktx2=True, alpha=alpha) / 2 ** 20:.1f} MiB (High/Medium), "
                  f"{gpu_bytes(entries, True, ktx2=True, alpha=alpha) / 2 ** 20:.1f} MiB (Low), "
                  f"encoded in {time.time() - t1:.1f} s with {binary}")
    return {"sizes": sizes, "ktx2_sizes": ktx2_sizes, "entries": entries, "images": images}


def contact_sheets(images: dict, out_dir: str, tile_px: int = 512) -> None:
    """Review sheets: each texture tiled 3 x 3 (reduced to `tile_px` per
    repeat), and `all_3x3.png`, every texture's 3 x 3 at a third of that,
    four to a row in name order."""
    os.makedirs(out_dir, exist_ok=True)
    small = {}
    for stem, img in sorted(images.items()):
        while max(img.shape[:2]) > tile_px:
            img = paint.half(img)
        sheet = np.tile(img, (3, 3, 1))
        with open(os.path.join(out_dir, f"{stem}_3x3.png"), "wb") as f:
            f.write(png_bytes(sheet))
        thumb = img
        while max(thumb.shape[:2]) > tile_px // 4:
            thumb = paint.half(thumb)
        small[stem] = np.tile(thumb, (3, 3, 1))
    cell = 3 * (tile_px // 4) + 16
    names = sorted(small)
    cols = 4
    rows = (len(names) + cols - 1) // cols
    board = np.ones((rows * cell, cols * cell, 4), dtype=np.float32)
    board[..., :3] = paint.hex_rgb("#2E2A30")
    for i, stem in enumerate(names):
        r, c = i // cols, i % cols
        t = small[stem]
        y = (rows - 1 - r) * cell + 8
        x = c * cell + 8
        board[y : y + t.shape[0], x : x + t.shape[1]] = t
    with open(os.path.join(out_dir, "all_3x3.png"), "wb") as f:
        f.write(png_bytes(board))
    with open(os.path.join(out_dir, "all_3x3.txt"), "w", encoding="utf-8") as f:
        f.write("all_3x3.png, left to right, top to bottom:\n" + "\n".join(names) + "\n")


# =====================================================================================
# Castle M5c (Facades): docs/art/castle-kit.md "Textures". The slate painter (castle_roof).
# =====================================================================================

SLATE_COURSE_M = 0.3  # a course's height in world y (the slopes' world UVs run v = y over 45 degrees)
SLATE_WIDTH_M = (0.32, 0.56)  # slates in a course, staggered over the course below


def paint_roof_slate(base: str, joint: str, seed: int, size: int = 1024, tile_m: float = 4.8) -> np.ndarray:
    """Coursed slates (castle_roof's `roof_slate`): courses of exactly
    SLATE_COURSE_M (16 to a 4.8 m repeat, so they register with world height
    on every roof), slates SLATE_WIDTH_M wide with staggered joints, one tone
    per slate round `base` (a cool hue push only), a lit top edge and a deep
    shaded band along each slate's lower edge (the overlap's shadow), crisp
    `joint`-coloured joints; tiles both ways. Colours only from the
    arguments (palette data)."""
    rng = np.random.default_rng(seed)
    n = int(round(tile_m / SLATE_COURSE_M))
    rows = [(i * SLATE_COURSE_M, (i + 1) * SLATE_COURSE_M) for i in range(n)]
    joints = paint.bond_rows(rng, tile_m, SLATE_WIDTH_M, n, min_gap=0.1)
    bm = paint.block_map(size, size, size / tile_m, rows, joints, tile_m)
    tones = paint.block_tones(rng, bm["n"] + 1, base, (-0.1, -0.05, 0.0, 0.04, 0.08), (0.12, 0.24, 0.3, 0.22, 0.12),
                              hue=0.25)
    img = paint.paint_blocks(bm, tones, joint, seed + 1, joint_px=2.0, bands=(2.0, 1.5, 7.0, 1.5),
                             band_values=(0.07, 0.03, -0.2, -0.05), patch=0.03, patch_cells=(8, 8), gradient=0.04,
                             chips=0.12, chip_px=(4.0, 9.0))
    return paint.fit_albedo(img)


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    ap = argparse.ArgumentParser(description="Castle texture library -> textures/ + textures.json")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--spec", default=SPEC_PATH)
    ap.add_argument("--milestone", type=int, default=MILESTONE)
    ap.add_argument("--contact", default=None, help="also write 3 x 3 review sheets into this directory")
    ap.add_argument("--no-ktx2", action="store_true", help="PNG only: skip the KTX2 encoding")
    args = ap.parse_args(script_args(sys.argv))
    build(args.out, args.spec, args.milestone, args.contact, ktx2=not args.no_ktx2)


if __name__ == "__main__":
    main()
