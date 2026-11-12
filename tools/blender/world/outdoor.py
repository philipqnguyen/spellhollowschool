"""The seams by which greybox.py hands the outdoors to their
exports (docs/art/castle-kit.md "Exterior"). No `bpy` import here (the objects pass
through untouched), so the contract is testable in plain Python.

* **The grounds**: `world/grounds_export.py`, when it exists,
  defines `build(ctx: OutdoorContext) -> dict[str, OutdoorCell]`. Each cell it
  returns replaces greybox.py's M1.0 build of that cell (or adds a cell the
  layout has no surface in: the all-water grounds cells, `backdrop`). It
  returns the cells of `grid.P3A_RESIDENT_CELLS`: the 30 grounds cells,
  `lake` (the water), `backdrop` (the forest edge, the mountains, the castle
  crag) and `foliage` (every tree and rock of the grounds, instanced).
* **The Flying Pitch**: `world/pitch_export.py`,
  when it exists, defines `build(ctx: OutdoorContext) -> dict[str, OutdoorCell]`
  like the grounds export and returns the cell `grid.P3B_PITCH_CELL` (`pitch`:
  the field's turf and markings, the hoops, the towers, the stands with their
  seats in `manifest["seats"]`, the gate), placed by `ctx.grounds["pitch"]`.
* **The far silhouette**: `world/hlod_export.py`, when it
  exists, defines `build(ctx: HlodContext) -> dict`: it writes
  `<out_dir>/hlod.glb` (`grid.P3A_HLOD_FILE`) and returns the manifest's
  `hlod` entry.

Until an export module exists, greybox.py exports what it always did (the
grounds' heightfield floors as one quad per 2 m square, the greybox lake) and
writes no `hlod`.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, field
from typing import Any, Callable


def optional_module(name: str):
    """The export module `name` (on the world tools' path), or None when it
    has not been written yet. An error inside an existing module is raised."""
    if importlib.util.find_spec(name) is None:
        return None
    return importlib.import_module(name)


@dataclass
class OutdoorCell:
    """One cell an export builds.

    `objects`: its Blender objects, already linked to the scene: one mesh per
    material (names `<cell>.<material>`; library materials by their castle_*
    names, e.g. `castle_wall_c@ext`; its own by palette-named materials), LOD1
    copies named `<node>_lod1` carrying `obj["kit_lod"] = {"base": <node>,
    "distance": grid.P3A_GROUNDS_LOD}`. `bounds`: ((x, y, z), (x, y, z)) of
    everything drawn. `manifest`: what the cell's manifest entry gains
    (`library`, `kit`: {tris, draws}, `lod`); greybox.py adds `resident`.
    `lights`: `layout_lib.Light`s of the cell (at most `kit.LIGHTS_PER_CELL`).
    `gpu_instances`: export instanced objects (Blender collection instances)
    as EXT_mesh_gpu_instancing, which Babylon loads as thin instances (the
    `foliage` cell's trees and rocks).
    """

    objects: list
    bounds: tuple
    manifest: dict = field(default_factory=dict)
    lights: list = field(default_factory=list)
    gpu_instances: bool = False


@dataclass
class OutdoorContext:
    """What greybox.py hands the grounds export.

    `layout` (layout_lib.Layout), `props` (layout_lib.Prop list), `grounds`
    (data/world/terrain/grounds.json, parsed), `terrain_dir` (where the
    `<cell>.json` height files are), `textures` (the castle texture set),
    `castle_material(name)` (the library's placeholder material for a castle_*
    name, as kitted cells use), `make_material(name)` (a greybox material of
    layout_lib.MATERIALS), `kit_objects(cell)` (the cell's props-kit objects
    as greybox.py would export them: the class gates, the find-spot tells;
    include them in the cell's `objects`), `mesh_from_data(md, material)`
    (castle_kit.assemble.MeshData to a linked object)."""

    layout: Any
    props: list
    grounds: dict
    terrain_dir: str
    textures: dict
    castle_material: Callable
    make_material: Callable
    kit_objects: Callable
    mesh_from_data: Callable


@dataclass
class HlodContext:
    """What greybox.py hands the HLOD export: `layout`, `exterior`
    (data/world/exterior.json, parsed), `kit_plan` (castle_kit.json resolved:
    the cells' `facade` flags), `textures`, `castle_material(name)`,
    `mesh_from_data`, `export(objects, path)` (greybox.py's glTF export with
    extras and COLOR_0 as bytes) and `out_dir`."""

    layout: Any
    exterior: dict
    kit_plan: Any
    textures: dict
    castle_material: Callable
    mesh_from_data: Callable
    export: Callable
    out_dir: str
