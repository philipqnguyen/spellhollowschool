"""The castle kit: places the dressing of `kit_models.py` (stations, the
Potions classroom, find-spot tells) as listed in `data/world/props.json`
(`kit`), and prepares it for `greybox.py` (pure Python and numpy, no `bpy`).

    "kit": [{ "id": "owl_exchange_counter", "cell": "entrance_hall",
              "model": "owl_exchange_counter", "pos": [0.0, 0.0, 23.0], "yaw": 1.5708,
              "lights"?: true, "args"?: { "width": 4.0 } }]

`pos` is where the model's origin goes (world metres, Y up), `yaw` turns its
front (+Z) the way the layout measures yaw (0 faces +Z, pi/2 faces +X).
Per cell, the textured faces become one mesh with one material, `kit`,
whose base colour is a small atlas painted here: one 64 px tile per (tile,
colour) the kit uses, so the castle cells stay at one extra draw call and
need no dye mask; flames and brews also glow through an emissive copy of
the atlas. Faces drawn in a flat greybox material (`g:<name>`, the tells)
join that material's mesh in the cell instead.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

import numpy as np

import foliage as FO
import kit_models as km
import meshkit as mk
import paint
from meshkit import Mesh

TILE_PX = 64
PAD = 3
# Castle kit M2, the glow rule (castle_kit/grid.py GLOW_BASE_MAX): a glowing
# tile's base (lit) copy is this share of its glow colour in linear light, so
# the toon light adds little on top of the emission and glow stays warm.
GLOW_BASE = 0.08
CELL_RE = re.compile(r"^[a-z0-9_]+$")
LIGHTS_PER_CELL = 6

# Tile -> (painter(size, seed) in neutral or true colours, metres one tile
# spans for UVs in metres (None: the face's own 0..1 UVs), tinted by the
# palette colour?, glows?).
TILES = {
    "wood": (lambda n, s: paint.wood_grain(n, "#E4E0DA", s, contrast=0.16), 0.5, True, False),
    "fabric": (lambda n, s: paint.velvet(n, "#E4E2E0", s, folds=2, depth=0.1), 0.4, True, False),
    "metal": (lambda n, s: paint.plaster(n, "#E6E6E6", s, contrast=0.18), 0.25, True, False),
    "leather": (lambda n, s: paint.plaster(n, "#DAD6D0", s, contrast=0.2), 0.25, True, False),
    "leaf": (lambda n, s: paint.leaf(n, "#DDE3D8", s), None, True, False),
    "stone": (lambda n, s: paint.masonry(n, "#DDD6C8", "#A39C90", [16, 20], (22, 40), s, joint_px=1.2, bevel_px=3), 0.6,
              True, False),
    "glass": (lambda n, s: paint.flat(n, "#E4ECF0", 0.06, s), 0.2, True, False),
    "linen": (lambda n, s: paint.weave(n, "#E9E3D6", s, threads=16), 0.3, False, False),
    "parchment": (lambda n, s: paint.plaster(n, "#D8C9A3", s, contrast=0.12), 0.3, False, False),
    "chalk": (lambda n, s: paint.chalkboard(n, s), None, False, False),
    "books": (lambda n, s: paint.book_spines(n, n, s), None, False, False),
    "flame": (lambda n, s: paint.flame(n), None, False, True),
    "brew": (lambda n, s: paint.brew(n, s), None, False, True),
    "wax": (lambda n, s: paint.flat(n, "#EFE6CC", 0.03, s), 0.1, False, False),
    "dark": (lambda n, s: paint.flat(n, "#3A3530", 0.05, s), 0.25, False, False),
    # A tinted glow (orbs, the floating feather, lantern glass).
    "glow": (lambda n, s: paint.flat(n, "#FFFFFF", 0.02, s), None, True, True),
}


@dataclass(frozen=True)
class KitProp:
    id: str
    cell: str
    model: str
    pos: tuple[float, float, float]
    yaw: float
    lights: bool = True
    # Keyword arguments for the model (e.g. a wall facing's width).
    args: tuple[tuple[str, float], ...] = ()


@dataclass
class CellKit:
    """One cell's kit: textured faces (world coordinates, atlas UVs per face)
    and polygons to add to flat greybox materials."""

    mesh: Mesh = field(default_factory=Mesh)
    uvs: list[list[tuple[float, float]]] = field(default_factory=list)
    flats: dict[str, list[list[tuple[float, float, float]]]] = field(default_factory=dict)
    lights: list[dict] = field(default_factory=list)
    foliage: list = field(default_factory=list)  # vegetation look A: world-placed plantings (world/foliage.py)


class KitError(Exception):
    pass


def load_kit(path: str) -> list[KitProp]:
    """The `kit` entries of a props file (empty when it has none)."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f).get("kit", [])
    out = []
    for r in raw:
        pid = r.get("id")
        if not isinstance(pid, str) or not CELL_RE.match(r.get("cell", "")):
            raise KitError(f"kit entry {pid!r}: needs a string id and a valid cell")
        if r.get("model") not in km.MODELS:
            raise KitError(f"kit entry {pid}: unknown model {r.get('model')!r}")
        pos = r.get("pos")
        if not isinstance(pos, list) or len(pos) != 3 or not all(isinstance(v, (int, float)) for v in pos):
            raise KitError(f"kit entry {pid}: pos must be [x, y, z]")
        yaw = r.get("yaw", 0.0)
        if not isinstance(yaw, (int, float)):
            raise KitError(f"kit entry {pid}: yaw must be a number")
        args = r.get("args", {})
        if not isinstance(args, dict) or not all(isinstance(v, (int, float)) for v in args.values()):
            raise KitError(f"kit entry {pid}: args must map names to numbers")
        out.append(KitProp(pid, r["cell"], r["model"], tuple(float(v) for v in pos), float(yaw), bool(r.get("lights", True)),
                           tuple(sorted((k, float(v)) for k, v in args.items()))))
    ids = [p.id for p in out]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise KitError(f"duplicate kit ids: {dupes}")
    return out


class Atlas:
    """Tile slots for (tile, colour) pairs, packed in a square texture."""

    def __init__(self):
        self.slots: dict[tuple[str, str], int] = {}

    def slot(self, tile: str, colour: str) -> int:
        if tile not in TILES:
            raise KitError(f"kit face material {tile!r} is not a kit tile")
        key = (tile, colour.upper() if TILES[tile][2] else "-")
        if key not in self.slots:
            self.slots[key] = len(self.slots)
        return self.slots[key]

    @property
    def grid(self) -> int:
        n = max(1, len(self.slots))
        return 1 << math.ceil(math.log2(math.ceil(math.sqrt(n)))) if n > 1 else 1

    @property
    def size(self) -> int:
        return max(4, self.grid) * TILE_PX

    def rect(self, index: int) -> tuple[float, float, float, float]:
        """The slot's usable rectangle (u0, v0, width, height) in 0..1."""
        g = max(4, self.grid)
        cx, cy = index % g, index // g
        s = self.size
        return ((cx * TILE_PX + PAD) / s, (cy * TILE_PX + PAD) / s, (TILE_PX - 2 * PAD) / s, (TILE_PX - 2 * PAD) / s)

    def paint(self) -> tuple[np.ndarray, np.ndarray]:
        """(base colour, emissive) RGBA float arrays, rows bottom to top."""
        s = self.size
        g = max(4, self.grid)
        base = np.zeros((s, s, 4), dtype=np.float32)
        base[..., 3] = 1.0
        glow = base.copy()
        cache: dict[str, np.ndarray] = {}
        for (tile, colour), index in sorted(self.slots.items(), key=lambda kv: kv[1]):
            painter, _, tinted, glows = TILES[tile]
            if tile not in cache:
                cache[tile] = painter(TILE_PX, 17 + len(cache))
            img = cache[tile].copy()
            if tinted:
                img[..., :3] *= paint.hex_rgb(colour)[None, None, :]
            x, y = (index % g) * TILE_PX, (index // g) * TILE_PX
            if glows:
                glow[y : y + TILE_PX, x : x + TILE_PX] = img
                dim = img.copy()
                dim[..., :3] = paint.linear_to_srgb(paint.srgb_to_linear(np.clip(img[..., :3], 0.0, 1.0)) * GLOW_BASE)
                base[y : y + TILE_PX, x : x + TILE_PX] = dim
            else:
                base[y : y + TILE_PX, x : x + TILE_PX] = img
        return base, glow

    def glow_mask(self) -> np.ndarray:
        """True over the glowing tiles' slots (rows bottom to top, as paint())."""
        s = self.size
        g = max(4, self.grid)
        mask = np.zeros((s, s), dtype=bool)
        for (tile, _), index in self.slots.items():
            if TILES[tile][3]:
                x, y = (index % g) * TILE_PX, (index // g) * TILE_PX
                mask[y : y + TILE_PX, x : x + TILE_PX] = True
        return mask


def face_uvs(face: mk.Face, rect, metres: float | None, k: int) -> list[tuple[float, float]]:
    """UVs of one face inside its slot: fit tiles take the face's 0..1 UVs,
    the others keep their density in metres (scaled down to fit), shifted
    by a stable per-face offset so repeats do not line up."""
    u0, v0, tw, th = rect
    if metres is None:
        return [(u0 + min(max(u, 0.0), 1.0) * tw, v0 + min(max(v, 0.0), 1.0) * th) for u, v in face.uvs]
    us = [u / metres for u, _ in face.uvs]
    vs = [v / metres for _, v in face.uvs]
    umin, vmin = min(us), min(vs)
    ext = max(max(us) - umin, max(vs) - vmin, 1e-6)
    s = min(1.0, 1.0 / ext)
    h = (k * 2654435761) % 1000 / 1000.0
    ou = max(0.0, 1.0 - (max(us) - umin) * s) * h
    ov = max(0.0, 1.0 - (max(vs) - vmin) * s) * ((h * 7.31) % 1.0)
    return [(u0 + ((u - umin) * s + ou) * tw, v0 + ((v - vmin) * s + ov) * th) for u, v in zip(us, vs)]


def place(p, prop: KitProp):
    return mk.add(mk.rot_y(p, prop.yaw), prop.pos)


def assemble(props: list[KitProp], extra_slots=()) -> tuple[dict[str, CellKit], Atlas]:
    """Builds every kit prop, in world coordinates, grouped by cell.

    `extra_slots`: further (tile, colour) slots registered after the props'
    own and before any UV is computed: the castle kit pieces' slots on the
    shared `castle_props` atlas (tools/blender/castle_kit/props_atlas.py)."""
    atlas = Atlas()
    cells: dict[str, CellKit] = {}
    staged = []
    for prop in props:
        kit = km.build(prop.model, **dict(prop.args))
        ck = cells.setdefault(prop.cell, CellKit())
        for f in kit.mesh.faces:
            pts = [place(kit.mesh.verts[i], prop) for i in f.idx]
            if f.material.startswith("g:"):
                ck.flats.setdefault(f.material[2:], []).append(pts)
                continue
            colour = kit.palette.get(f.zone, "#FFFFFF")
            staged.append((ck, pts, f, atlas.slot(f.material, colour)))
        # Vegetation look A: the model's plantings, placed with it.
        ck.foliage += [FO.place(p, lambda q, prop=prop: place(q, prop)) for p in kit.foliage]
        if prop.lights:
            for i, lt in enumerate(kit.lights):
                ck.lights.append({"id": f"{prop.id}_{i}", "pos": list(place(tuple(lt["pos"]), prop)), "color": lt["color"],
                                  "intensity": lt["intensity"], "range": lt["range"]})
    for tile, colour in extra_slots:
        atlas.slot(tile, colour)
    # UVs need the final atlas layout (its size depends on the slot count).
    for ck, pts, f, index in staged:
        n = len(ck.mesh.faces)
        ck.mesh.poly(pts, "kit", None, 0, None, f.smooth)
        if len(ck.mesh.faces) > n:  # (degenerate faces are dropped)
            ck.uvs.append(face_uvs(f, atlas.rect(index), TILES[f.material][1], len(ck.uvs)))
    for ck in cells.values():
        ck.lights.sort(key=lambda lt: -lt["intensity"])
    return cells, atlas
