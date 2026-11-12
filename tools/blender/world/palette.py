"""The castle palette for the anime look (pure Python, no `bpy`).

Art direction (docs/art/style-guide.md, "Castle palette"): clean, flat,
saturated but harmonious colour regions that read well under the client's
two-band cel shading with warm shadows. Stone is warm cream sandstone
(lighter toward the top of a wall, darker at its base), roofs are a clear
slate blue, timber is a warm red-brown, grass a bright yellow-green that
deepens toward the lake, water a clear teal-blue. House colours stay the
four recognisable hues, only cleaner.

Everything here is sRGB hex (as painted); `linear()` converts for material
colour factors. `grade()` is the "anime grade" the room and kit colours were
put through once (lift near-blacks, a little more saturation, warm the
neutrals); it is kept for new colours, so they match without guesswork.
"""

from __future__ import annotations

import colorsys


def rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (1, 3, 5))


def hexc(c) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02X}" for v in c)


def to_linear(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def linear(h: str) -> tuple[float, float, float]:
    """sRGB hex -> linear RGB (material colour factors, vertex colours)."""
    return tuple(round(to_linear(v), 4) for v in rgb(h))


def grade(h: str, sat: float = 1.18, lift: float = 0.12, warm: float = 0.035) -> str:
    """The anime grade: no near-black (value lifted toward `lift`), a bit
    more saturation, and greys nudged toward a warm hue (never a dead grey).
    Pure: the same colour always grades the same way."""
    r, g, b = rgb(h)
    hue, s, v = colorsys.rgb_to_hsv(r, g, b)
    v = lift + v * (1.0 - lift)
    if s < 0.04:
        # A dead grey: give it a gentle warm cast.
        hue, s = 0.09, s + warm
    elif s < 0.1:
        # Nearly neutral: keep its hue (green-grey dungeon stone stays green).
        s *= 1.25
    elif s < 0.88:
        # Saturate, but never into pure, harsh primaries.
        s = min(0.88, s * sat)
    return hexc(colorsys.hsv_to_rgb(hue, s, v))


def luminance(h: str) -> float:
    """Relative (linear) luminance of an sRGB hex colour."""
    r, g, b = (to_linear(v) for v in rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def saturation(h: str) -> float:
    return colorsys.rgb_to_hsv(*rgb(h))[1]


# ---------------------------------------------------------------------------
# The castle (greybox cells: layout_lib.MATERIALS takes these).

CASTLE = {
    # Stone: warm cream sandstone; the cells' vertex colours darken the
    # base of every wall (VALUE_RAMPS), so these are the lit upper values.
    "stone": "#E4D2B0",
    "stone_exterior": "#D3C1A0",
    "stone_trim": "#EEE2C8",
    "stone_low": "#B9AB8E",  # drystone walls on the grounds
    "stone_dungeon": "#7E8C84",  # cool green-grey, still saturated
    "stone_dark": "#5E6372",  # plinths, statues' bases, iron-grey stone
    # Floors: a step darker than the walls so rooms read.
    "flagstone": "#B99A78",
    "flagstone_corridor": "#AA9076",
    "flagstone_courtyard": "#C4B79E",
    "flagstone_dungeon": "#646B66",
    "flagstone_bridge": "#BBA886",
    "marble": "#EDE5D6",
    "threshold_wood": "#93603C",
    # Ceilings and roofs.
    "ceiling": "#7E5842",
    "roof": "#4C6596",  # clear slate blue
    # Timber and metal.
    "oak": "#8E5634",
    "oak_rail": "#7A4529",
    "gold": "#EDBB4C",
    "bronze": "#B97C3C",
    "copper": "#CC6D3E",
    "silver": "#C6CDD8",
    "iron": "#3E4150",
    "leather": "#663A27",
    # Cloth, paper, glass, plants, water, grass.
    "canvas": "#BE9A74",
    "parchment": "#EBD7A8",
    "glass": "#93D3F2",
    "plant": "#4F9A3C",
    "water": "#2C8BB0",
    "grass": "#8CC152",
    # House accents (banners, upholstery, rugs): recognisable, cleaner.
    "house_a": "#B0222A",  # scarlet
    "house_b": "#EDB72F",  # yellow
    "house_c": "#24499A",  # blue
    "house_d": "#1F7444",  # green
    "house_a_2": "#E2B23A",  # gold
    "house_b_2": "#463A36",  # black (lifted)
    "house_c_2": "#B58542",  # bronze
    "house_d_2": "#C3C8CF",  # silver
}

# Emissive colours (fire, candles, windows, magic): sRGB hex. Shared with
# the point lights that come with them (data/art/lighting.json `lights`).
GLOW = {
    "fire": "#FF7A2E",
    "candle": "#FFAD5C",  # castle kit M2: grid.GLOW_CANDLE (warm, never white at glow level 1)
    "lake": "#36687E",  # moonlit lake water through common room D's windows: soft, never a backlight
    "green": "#56F08E",
    "sky": "#A8CCFF",
    "sun": "#FFE29A",
}

# Painted value ramps baked into the castle cells' `COLOR_0` (a plain
# multiplicative vertex colour on world cells, as glTF defines it):
# material -> (mode, bottom RGB multiplier, top RGB multiplier, exponent).
# Castle D1 (docs/art/castle-kit.md "Shading: the AO bake"): every castle ramp is grey (r = g = b,
# its old bottom's Rec. 709 luminance), so COLOR_0 carries value only and a
# palette swap changes the textures alone; `floor_grounds` keeps its hue ramp,
# the one documented exception, until the grounds milestone.
# "cell": from the lowest to the highest point of that material in the cell
# (each room's walls darken toward their base); "world": over absolute
# heights `WORLD_RAMP` (continuous across cells: the outer walls and the
# sloping grounds).
WORLD_RAMP = (-14.0, 30.0)
VALUE_RAMPS = {
    "wall": ("cell", (0.70, 0.70, 0.70), (1.0, 1.0, 1.0), 0.8),
    "wall_dungeon": ("cell", (0.76, 0.76, 0.76), (1.0, 1.0, 1.0), 0.8),
    "wall_exterior": ("world", (0.67, 0.67, 0.67), (1.0, 1.0, 1.0), 0.7),
    "wall_low": ("world", (0.82, 0.82, 0.82), (1.0, 1.0, 1.0), 1.0),
    "trim": ("cell", (0.86, 0.86, 0.86), (1.0, 1.0, 1.0), 1.0),
    # Grass: bright yellow-green by the castle, deeper and cooler by the lake.
    "floor_grounds": ("world", (0.70, 0.86, 0.92), (1.0, 1.0, 0.92), 1.0),
}


def ramp_factor(material: str, y: float, lo: float, hi: float):
    """The RGB multiplier of `material` at height `y` (world metres, Y up);
    `lo`/`hi` are the material's height range in its cell. None when the
    material has no ramp."""
    spec = VALUE_RAMPS.get(material)
    if spec is None:
        return None
    mode, bottom, top, exp = spec
    if mode == "world":
        lo, hi = WORLD_RAMP
    t = 0.0 if hi - lo < 1e-6 else min(1.0, max(0.0, (y - lo) / (hi - lo)))
    t = t ** exp
    return tuple(round(b + (u - b) * t, 4) for b, u in zip(bottom, top))
