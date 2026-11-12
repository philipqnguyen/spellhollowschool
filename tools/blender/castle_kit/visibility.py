"""Cell visibility (`sees`) and the `COLOR_0` byte packing for the world
export (castle kit M2, docs/art/castle-kit.md "Cell visibility (`sees`)", "Shading: the AO bake"). Pure
Python, no `bpy`; `greybox.py` calls both from its Engine hook.

`cell_sees(layout)`: which cells can be seen from each cell, written to the
manifest as `sees` so the client can disable resident cells outside it.

- Cells are joined (one hop) where a surface of one meets a surface of the
  other across a walkable join (`layout_lib.connected`: a door surface of
  one side joining the other, or an open join such as the dungeon and
  kitchen steps' top edges into the hall).
- Cells are stacked (no hop) where a surface of each overlaps the other in
  plan and the lower one's air reaches the upper one's floor (the lower is
  unroofed or its ceiling is above the upper's floor): shafts, stairwells,
  the Grand Staircase's galleries over its void. Stacking is closed
  transitively.
- `sees(A)` is A's stack group, every cell within `SEES_DOORS` hops of it
  (each with its stack group), and, for each outdoor cell one hop from A's
  group, the cells with a surface within `SEES_FACADE` of one of the
  outdoor cell's surfaces in plan that reaches above its floor (their
  shells are its facades). Sorted, A included. Outdoor cells get None:
  they see everything.

`pack_color0(glb_path)`: rewrites every `COLOR_n` accessor of a GLB as
normalized `UNSIGNED_BYTE` `VEC4` (linear values, alpha 255) in its own
tightly packed view; every other accessor's bytes are unchanged.
Idempotent; returns the bytes saved.
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
from collections import deque

_BLENDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BLENDER not in sys.path:
    sys.path.insert(0, _BLENDER)
import layout_lib as L  # noqa: E402
from castle_kit import grid as G  # noqa: E402

# Overlap in plan that counts as stacking (m and m^2): touching edges do not.
_OVERLAP_EPS = 1e-3


def is_outdoor(surfaces) -> bool:
    return any(s.kind in G.OUTDOOR_KINDS for s in surfaces)


def _by_cell(layout: L.Layout) -> dict[str, list[L.Surface]]:
    out: dict[str, list[L.Surface]] = {}
    for s in layout.surfaces:
        out.setdefault(s.cell, []).append(s)
    return out


def portal_graph(layout: L.Layout) -> dict[str, set[str]]:
    """Cell -> the cells joined to it by one walkable join."""
    graph: dict[str, set[str]] = {c: set() for c in layout.cells()}
    ss = layout.surfaces
    for i, a in enumerate(ss):
        for b in ss[i + 1:]:
            if a.cell != b.cell and L.connected(a, b):
                graph[a.cell].add(b.cell)
                graph[b.cell].add(a.cell)
    return graph


def _plan_overlap(a: L.Surface, b: L.Surface) -> bool:
    return min(a.x1, b.x1) - max(a.x0, b.x0) > _OVERLAP_EPS and min(a.z1, b.z1) - max(a.z0, b.z0) > _OVERLAP_EPS


def _stacked(a: L.Surface, b: L.Surface) -> bool:
    """`a` and `b` overlap in plan and the lower one's air reaches the upper's floor."""
    if not _plan_overlap(a, b):
        return False
    lo, hi = (a, b) if a.floor_min <= b.floor_min else (b, a)
    return (not lo.roofed) or lo.ceil_y > hi.floor_min + L.EPS


def stack_groups(layout: L.Layout) -> dict[str, frozenset[str]]:
    """Cell -> its stack group (itself included), stacking closed transitively.
    Only indoor cells stack: the castle's exterior is the interior cells' shells."""
    cells = _by_cell(layout)
    indoor = [c for c, ss in cells.items() if not is_outdoor(ss)]
    parent = {c: c for c in cells}

    def find(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    for i, ca in enumerate(indoor):
        for cb in indoor[i + 1:]:
            if find(ca) == find(cb):
                continue
            if any(_stacked(a, b) for a in cells[ca] for b in cells[cb]):
                parent[find(cb)] = find(ca)
    groups: dict[str, set[str]] = {}
    for c in cells:
        groups.setdefault(find(c), set()).add(c)
    return {c: frozenset(groups[find(c)]) for c in cells}


def _plan_gap(a: L.Surface, b: L.Surface) -> float:
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dz = max(0.0, max(a.z0, b.z0) - min(a.z1, b.z1))
    return math.hypot(dx, dz)


def facades(layout: L.Layout, outdoor: str, cells=None) -> set[str]:
    """Indoor cells with a surface within SEES_FACADE of the outdoor cell's
    surfaces in plan that reaches above that surface's floor."""
    cells = cells or _by_cell(layout)
    out: set[str] = set()
    for o in cells[outdoor]:
        for c, ss in cells.items():
            if c == outdoor or c in out or is_outdoor(ss):
                continue
            if any(_plan_gap(o, s) <= G.SEES_FACADE + L.EPS and s.struct_top() > o.floor_min + L.EPS for s in ss):
                out.add(c)
    return out


def cell_sees(layout: L.Layout) -> dict[str, list[str] | None]:
    """Cell -> the sorted cells visible from it (itself included), or None
    for outdoor cells (they see everything). Deterministic."""
    cells = _by_cell(layout)
    graph = portal_graph(layout)
    groups = stack_groups(layout)
    facade_cache: dict[str, set[str]] = {}
    out: dict[str, list[str] | None] = {}
    for cell in sorted(cells):
        if is_outdoor(cells[cell]):
            out[cell] = None
            continue
        hops: dict[str, int] = {c: 0 for c in groups[cell]}
        queue = deque(sorted(hops))
        while queue:
            cur = queue.popleft()
            d = hops[cur]
            if d >= G.SEES_DOORS:
                continue
            for nxt in sorted(graph[cur]):
                for g in sorted(groups[nxt]):
                    if g not in hops:
                        hops[g] = d + 1
                        queue.append(g)
        seen = set(hops)
        for c, d in hops.items():
            if d == 1 and is_outdoor(cells[c]):
                if c not in facade_cache:
                    facade_cache[c] = facades(layout, c, cells)
                seen |= facade_cache[c]
        out[cell] = sorted(seen)
    return out


def add_unplaced(sees: dict[str, list[str] | None], all_cells) -> dict[str, list[str] | None]:
    """Cells the export writes that have no layout surface (the `lake`, a
    props.json water plane) are outdoor scenery: every indoor cell whose
    `sees` holds an outdoor cell sees them too (castle kit M2 integration;
    otherwise they would be hidden from indoors even where the front doors
    and the courtyard gate line up on them)."""
    unplaced = sorted(set(all_cells) - set(sees))
    if not unplaced:
        return dict(sees)
    outdoor = {c for c, s in sees.items() if s is None}
    return {c: (s if s is None or not outdoor & set(s) else sorted(set(s) | set(unplaced))) for c, s in sees.items()}


# -- COLOR_0 as normalized bytes -------------------------------------------------------

_GLB_MAGIC = 0x46546C67
_JSON = 0x4E4F534A
_BIN = 0x004E4942
_FLOAT, _USHORT, _UBYTE = 5126, 5123, 5121
_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_SIZES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_ARRAY_BUFFER = 34962


def _pad4(n: int) -> int:
    return (n + 3) & ~3


def read_glb(path: str) -> tuple[dict, bytes]:
    with open(path, "rb") as f:
        data = f.read()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != _GLB_MAGIC or version != 2:
        raise ValueError(f"{path}: not a glTF 2.0 binary")
    off = 12
    doc, binary = None, b""
    while off < length:
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8:off + 8 + clen]
        if ctype == _JSON:
            doc = json.loads(chunk.decode("utf-8"))
        elif ctype == _BIN:
            binary = bytes(chunk)
        off += 8 + clen
    if doc is None:
        raise ValueError(f"{path}: no JSON chunk")
    return doc, binary


def write_glb(path: str, doc: dict, binary: bytes) -> None:
    js = json.dumps(doc, separators=(",", ":"), sort_keys=False).encode("utf-8")
    js += b" " * (_pad4(len(js)) - len(js))
    binary = binary + b"\0" * (_pad4(len(binary)) - len(binary))
    total = 12 + 8 + len(js) + (8 + len(binary) if binary else 0)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", _GLB_MAGIC, 2, total))
        f.write(struct.pack("<II", len(js), _JSON))
        f.write(js)
        if binary:
            f.write(struct.pack("<II", len(binary), _BIN))
            f.write(binary)


def _colour_bytes(acc: dict, view: dict, binary: bytes) -> bytes:
    """An accessor's colours as RGBA8: round(clamp(v, 0, 1) * 255), alpha 255 when absent."""
    comps = _COMPONENTS[acc["type"]]
    ctype = acc["componentType"]
    size = _SIZES[ctype]
    stride = view.get("byteStride") or comps * size
    base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    n = acc["count"]
    fmt = {_FLOAT: "f", _USHORT: "H", _UBYTE: "B"}[ctype]
    scale = {_FLOAT: 1.0, _USHORT: 65535.0, _UBYTE: 255.0}[ctype]
    normalized = ctype == _FLOAT or acc.get("normalized", False)
    out = bytearray(n * 4)
    for i in range(n):
        vals = struct.unpack_from("<" + fmt * comps, binary, base + i * stride)
        for k in range(4):
            if k < comps:
                v = vals[k] / scale if normalized else float(vals[k])
                out[i * 4 + k] = int(math.floor(min(1.0, max(0.0, v)) * 255 + 0.5))
            else:
                out[i * 4 + k] = 255
    return bytes(out)


def pack_color0(glb_path: str) -> int:
    """Rewrites every COLOR_n accessor of the GLB at `glb_path` as normalized
    UNSIGNED_BYTE VEC4; returns the bytes saved (0 when there
    is nothing to change). The file is rewritten only when it changes."""
    doc, binary = read_glb(glb_path)
    accessors = doc.get("accessors", [])
    views = doc.get("bufferViews", [])
    colour: list[int] = []
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            for name, idx in sorted(prim.get("attributes", {}).items()):
                if name.startswith("COLOR_") and idx not in colour:
                    colour.append(idx)
    colour = [i for i in colour if "bufferView" in accessors[i] and "sparse" not in accessors[i]
              and accessors[i]["type"] in ("VEC3", "VEC4") and accessors[i]["componentType"] in (_FLOAT, _USHORT, _UBYTE)]
    if not colour:
        return 0
    if len(doc.get("buffers", [])) != 1:
        return 0  # external buffers: not ours to repack
    # Views used only by the colour accessors are dropped; every other view is copied as it is.
    users: dict[int, set[str]] = {}
    for i, a in enumerate(accessors):
        if "bufferView" in a:
            users.setdefault(a["bufferView"], set()).add("c" if i in colour else "o")
        for part in ("indices", "values"):
            sv = a.get("sparse", {}).get(part, {}).get("bufferView")
            if sv is not None:
                users.setdefault(sv, set()).add("o")
    for img in doc.get("images", []):
        if "bufferView" in img:
            users.setdefault(img["bufferView"], set()).add("o")
    packed = {i: _colour_bytes(accessors[i], views[accessors[i]["bufferView"]], binary) for i in colour}
    drop = {v for v, u in users.items() if u == {"c"}}
    keep = [v for v in range(len(views)) if v not in drop]
    remap: dict[int, int] = {}
    out = bytearray()
    new_views = []
    for v in keep:
        view = dict(views[v])
        start = view.get("byteOffset", 0)
        chunk = binary[start:start + view["byteLength"]]
        out += b"\0" * (_pad4(len(out)) - len(out))
        view["byteOffset"] = len(out)
        out += chunk
        remap[v] = len(new_views)
        new_views.append(view)
    for i in colour:
        out += b"\0" * (_pad4(len(out)) - len(out))
        new_views.append({"buffer": 0, "byteOffset": len(out), "byteLength": len(packed[i]), "target": _ARRAY_BUFFER})
        out += packed[i]
    for view in new_views:
        if view.get("byteOffset") == 0:
            view.pop("byteOffset")
    first_colour = len(keep)
    for i, a in enumerate(accessors):
        if i in packed:
            a["bufferView"] = first_colour + colour.index(i)
            a.pop("byteOffset", None)
            a["componentType"] = _UBYTE
            a["normalized"] = True
            a["type"] = "VEC4"
            a.pop("min", None)
            a.pop("max", None)
        elif "bufferView" in a:
            a["bufferView"] = remap[a["bufferView"]]
        for part in ("indices", "values"):
            sp = a.get("sparse", {}).get(part)
            if sp is not None:
                sp["bufferView"] = remap[sp["bufferView"]]
    for img in doc.get("images", []):
        if "bufferView" in img:
            img["bufferView"] = remap[img["bufferView"]]
    doc["bufferViews"] = new_views
    out += b"\0" * (_pad4(len(out)) - len(out))
    doc["buffers"][0]["byteLength"] = len(out)
    saved = len(binary) - len(out)
    new_bin = bytes(out)
    if new_bin == binary and json.dumps(doc, separators=(",", ":")) == json.dumps(read_glb(glb_path)[0], separators=(",", ":")):
        return 0
    write_glb(glb_path, doc, new_bin)
    return max(0, saved)


# -- Castle M5a: world draws in view (docs/art/castle-kit.md) ---------------------


def cell_draws(world_dir: str) -> dict[str, int]:
    """Cell -> the meshes its exported file draws (one draw each): every mesh
    node but the camera collider (never drawn) and the LOD1 copies (drawn
    instead of their base, `kit_lod`). Read from `world_dir`'s manifest.
    The House Cup banner nodes (`kit_anim` kind cup_banners,
    hidden unless that house won) count for grid.CUP_BANNERS_SHOWN houses only."""
    with open(os.path.join(world_dir, "manifest.json")) as f:
        manifest = json.load(f)
    out: dict[str, int] = {}
    for c in manifest["cells"]:
        doc, _ = read_glb(os.path.join(world_dir, c["file"]))
        n_draws, cup = 0, {}
        for n in doc.get("nodes", []):
            ex = n.get("extras") or {}
            if "mesh" not in n or ex.get("role") == "camera_collider" or "kit_lod" in ex:
                continue
            anim = ex.get("kit_anim") or {}
            if anim.get("kind") == G.ANIM_CUP_BANNERS:
                cup[anim.get("house")] = cup.get(anim.get("house"), 0) + 1
            else:
                n_draws += 1
        out[c["id"]] = n_draws + sum(sorted(cup.values(), reverse=True)[:G.CUP_BANNERS_SHOWN])
    return out


def draws_in_view(sees: dict[str, list[str] | None], draws: dict[str, int]) -> dict[str, int]:
    """Cell -> the world draws while standing in it: the draws of every cell
    it sees (an outdoor cell sees every cell). Frustum culling only lowers
    this; it is the most the castle can ask of one view."""
    return {c: sum(draws.get(o, 0) for o in (draws if s is None else s)) for c, s in sees.items()}


def main() -> None:
    """Prints each cell's `sees` (python3 -I tools/blender/castle_kit/visibility.py);
    with `--world DIR` (an export, e.g. client/public/assets/world) also its
    own draws and the world draws in view."""
    here = os.path.dirname(os.path.abspath(__file__))
    layout = L.load_layout(os.path.join(here, "..", "..", "..", "data", "world", "layout.json"))
    sees = cell_sees(layout)
    if "--world" in sys.argv:
        draws = cell_draws(sys.argv[sys.argv.index("--world") + 1])
        sees = add_unplaced(sees, draws)
        in_view = draws_in_view(sees, draws)
        for cell, seen in sees.items():
            n = "all" if seen is None else str(len(seen))
            print(f"{cell:24s} sees {n:>3s}  draws {draws.get(cell, 0):3d}  in view {in_view[cell]:4d}")
        return
    for cell, seen in sees.items():
        print(f"{cell:24s} {'(outdoor: everything)' if seen is None else f'{len(seen):2d} ' + ' '.join(seen)}")


if __name__ == "__main__":
    main()
