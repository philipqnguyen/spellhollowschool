"""The shared `castle_props` atlas: one slot table for every cell (pure
Python and numpy, no `bpy`).

Before the castle kit, `greybox.py` painted the kit atlas (`world/kit.py`)
and embedded a copy in every cell that used it. From M1 the atlas is a
library texture, painted once by `castle_kit/textures.py` into
`textures/props_atlas.png` (+ `props_atlas_e.png`) and referenced by
material name only. The painter and the exporter must agree on the slot
table, so both build it here, and only here:

    slots = the props.json `kit` entries' slots, in kit.assemble order,
            then data/art/castle_textures.json `props_slots`, in list order

`build()` returns the cells' kit (as `kit.assemble`) and a `PropsAtlas`
that refuses slots it does not already hold, so a piece cannot add a slot
the painted texture lacks. `key()` fingerprints the table; textures.json
stores it as `materials.castle_props.key` and the tests compare.

Named colours (castle kit M3, docs/art/castle-kit.md "Colours come from palette data only"): a `props_slots`
row may give `"@<name>"` instead of a hex colour; the name is looked up in
castle_textures.json `palette` when the atlas is painted. Such a slot is
keyed by its name, never by the colour, so it never merges with another
slot and a palette change repaints the texture without moving any UV.
Castle kit pieces use only named slots (`kit:<tile>:@<name>`); the hex rows
stay for the props.json kit models and the older rows (append only).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
_WORLD = os.path.join(_BLENDER, "world")
for _p in (_BLENDER, _WORLD):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import kit  # noqa: E402

REPO = os.path.abspath(os.path.join(_BLENDER, "..", ".."))
PROPS_PATH = os.path.join(REPO, "data", "world", "props.json")
TEXTURES_PATH = os.path.join(REPO, "data", "art", "castle_textures.json")
MAX_PX = 1024  # castle_props stays 1024 x 1024 (at most 256 slots of 64 px)
WHITE = "#FFFFFF"  # the colour of a tinted tile no palette names (props.json kit models without a zone colour)
HEX_RE = re.compile(r"^#[0-9A-F]{6}$")
NAME_RE = re.compile(r"^@[a-z][a-z0-9_]*$")


def palette(textures_path: str = TEXTURES_PATH) -> dict[str, str]:
    """castle_textures.json `palette`: {name: "#RRGGBB"} (names without the @)."""
    with open(textures_path, encoding="utf-8") as f:
        raw = json.load(f).get("palette", {})
    out = {}
    for name, colour in raw.items():
        if name.startswith("_"):
            continue  # comments
        if not re.match(r"^[a-z][a-z0-9_]*$", name) or not isinstance(colour, str) or not HEX_RE.match(colour):
            raise ValueError(f"palette.{name}: must be a lowercase name -> \"#RRGGBB\" (upper case), not {colour!r}")
        out[name] = colour
    return out


def extra_slots(textures_path: str = TEXTURES_PATH) -> list[tuple[str, str]]:
    with open(textures_path, encoding="utf-8") as f:
        raw = json.load(f).get("props_slots", [])
    names = palette(textures_path)
    out = []
    for i, s in enumerate(raw):
        if not (isinstance(s, list) and len(s) == 2 and s[0] in kit.TILES and isinstance(s[1], str)):
            raise ValueError(f"props_slots[{i}] must be [<kit tile>, \"#RRGGBB\", \"@name\" or \"-\"]: {s!r}")
        if s[1].startswith("@"):
            if not NAME_RE.match(s[1]) or s[1][1:] not in names:
                raise ValueError(f"props_slots[{i}]: {s[1]!r} is not a name of castle_textures.json palette")
            if not kit.TILES[s[0]][2]:
                raise ValueError(f"props_slots[{i}]: {s[0]!r} is not a tinted tile (a name gives it a colour)")
        out.append((s[0], s[1]))
    return out


class _Slots:
    """A slot table view kit.Atlas.paint can walk with every named colour
    resolved (two names may share a colour; their slots stay apart)."""

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def items(self):
        return list(self._pairs)

    def __len__(self):
        return len(self._pairs)


class PropsAtlas:
    """A finished slot table: lookups only."""

    def __init__(self, atlas: kit.Atlas, names: dict[str, str] | None = None):
        self._atlas = atlas
        self._names = {f"@{k}".upper(): v for k, v in (names or {}).items()}
        if atlas.size > MAX_PX:
            raise ValueError(f"castle_props would need {atlas.size} px ({len(atlas.slots)} slots); the budget is {MAX_PX}")

    @property
    def size(self) -> int:
        return self._atlas.size

    @property
    def slots(self) -> dict[tuple[str, str], int]:
        return dict(self._atlas.slots)

    def index(self, tile: str, colour: str = "-") -> int:
        if tile not in kit.TILES:
            raise KeyError(f"{tile!r} is not a kit tile")
        key = (tile, colour.upper() if kit.TILES[tile][2] else "-")
        if key not in self._atlas.slots:
            raise KeyError(f"castle_props has no slot {key}: add it to castle_textures.json props_slots")
        return self._atlas.slots[key]

    def rect(self, tile: str, colour: str = "-") -> tuple[float, float, float, float]:
        """(u0, v0, width, height) in 0..1, Blender UVs, inside the slot's padding."""
        return self._atlas.rect(self.index(tile, colour))

    def metres(self, tile: str) -> float | None:
        """Metres one slot spans for UVs in metres (None: the face's own 0..1 UVs)."""
        return kit.TILES[tile][1]

    def colour(self, key: str) -> str:
        """The hex colour of a slot's colour key (`@NAME` resolved)."""
        if key.startswith("@"):
            return self._names[key.upper()]
        return key

    def _resolved(self) -> kit.Atlas:
        view = kit.Atlas()
        view.slots = _Slots(((t, self.colour(c) if c != "-" else c), i) for (t, c), i in self._atlas.slots.items())
        return view

    def paint(self):
        """(base colour, emissive) RGBA float arrays, rows bottom to top."""
        return self._resolved().paint()

    def glow_mask(self):
        """True over the glowing tiles' slots (rows bottom to top, as paint()):
        castle_kit/textures.py keeps their base below the albedo floor (M2
        open item 4: the mask comes from here, not a rebuilt kit.Atlas)."""
        return self._atlas.glow_mask()

    def key(self) -> str:
        return key(self._atlas, {k[1:].lower(): v for k, v in self._names.items()})


def key(atlas: kit.Atlas, names: dict[str, str] | None = None) -> str:
    """Fingerprint of the slot table and the colours it paints (a palette
    change changes it, so textures.json's key follows the painted atlas)."""
    res = {f"@{k}".upper(): v for k, v in (names or {}).items()}
    table = sorted(((i, t, c, res.get(c, c)) for (t, c), i in atlas.slots.items()))
    return hashlib.sha1(json.dumps(table).encode("utf-8")).hexdigest()[:12]


def build(props_path: str = PROPS_PATH, textures_path: str = TEXTURES_PATH):
    """(cell kits, PropsAtlas): the props.json kit built per cell, with
    UVs into the shared slot table."""
    entries = kit.load_kit(props_path) if os.path.exists(props_path) else []
    cells, atlas = kit.assemble(entries, extra_slots(textures_path))
    return cells, PropsAtlas(atlas, palette(textures_path))
