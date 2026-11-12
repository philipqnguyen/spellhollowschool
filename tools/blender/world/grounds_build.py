"""The grounds' outdoor cells as plain geometry, and their lint
(docs/art/castle-kit.md "Budgets", "Exterior"). Pure Python, no `bpy`: `grounds_export.py`
turns the result into Blender objects; the tests and the export's report run
`lint` on it.

`build(...)` returns a `Grounds3a`:

* `cells`: per grounds cell (`grid.P3A_GROUNDS_CELLS`) its terrain (LOD0 and
  LOD1, `grounds_terrain.py`) and its pieces (`grounds_dressing.placements`)
  merged into one mesh per material (`assemble.MeshData`: `castle_wall_c@ext`,
  `castle_props`, `castle_roof`), each with a `_lod1` node when its far view
  differs (small things by the hut, hedges, fences, lamp posts and the
  glasshouses' glazing bars drop out beyond `grid.P3A_GROUNDS_LOD`), its lights
  (lamp posts, the hut's and the boathouse's windows) and its bounds. COLOR_0
  on the pieces is a plain grey: darker at their foot and under eaves (the
  castle's ray-cast bake is for the kitted cells; these stand in the open).
* `foliage`: the rocks, one `MeshData` per instanced shape on the props atlas,
  and every instance (trees too, for the lint); `plantings`: per cell the
  vegetation look A plantings (world/foliage.py): the grounds' trees
  (`grounds_dressing.SHAPES`' tree shapes) in cell `foliage`, the hedges and
  the garden gate's hedges in their grounds cells, the forest beyond the box
  in `backdrop` (impostors only), all listed in the manifest and drawn by the
  client from the foliage kit.
* `lake` and `backdrop` (`grounds_backdrop.py`).
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
for _p in (BLENDER, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import foliage as FO  # noqa: E402
import grounds_backdrop as BD  # noqa: E402
import grounds_dressing as D  # noqa: E402
import grounds_terrain as GT  # noqa: E402
import palette  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402

STONE = G.ext_name(G.P3A_EXTERIOR_STONE)  # castle_wall_c@ext (the one outside stone)
MATERIALS = {"castle_wall": STONE}  # a piece's castle_wall family -> the grounds' stone
TERRAIN = "grounds_terrain"
TURF = "castle_floor_grass"  # the library texture the client's terrain material samples
WATER = "lake_water"
FAR_PIECES = ("kit_grounds_hut", "kit_boathouse", "kit_glasshouse_garden", "kit_standing_stone", "kit_drystone_wall")
LOD1_SUFFIX = "_lod1"


@dataclass
class CellGeo:
    cell: str
    terrain: GT.TerrainMesh
    terrain_lod1: GT.TerrainMesh
    meshes: dict = field(default_factory=dict)  # node name -> MeshData (LOD0)
    lod_meshes: dict = field(default_factory=dict)  # node name -> (MeshData, base node)
    lights: list = field(default_factory=list)  # {id, pos, color, intensity, range}
    kit_materials: list = field(default_factory=list)  # the props kit's flat-coloured meshes (greybox materials)
    kit_tris: int = 0

    def draws(self) -> int:
        """LOD0 draws: the terrain, each mesh, the props kit's flat-coloured meshes
        (its textured part has joined `castle_props`)."""
        return 1 + len(self.meshes) + len(self.kit_materials)

    def tris(self) -> int:
        return self.terrain.triangles() + sum(m.triangles() for m in self.meshes.values()) + self.kit_tris

    def lod1_tris(self) -> int:
        """Triangles drawn beyond the LOD distance: LOD1 nodes, and nodes without one."""
        n = self.terrain_lod1.triangles()
        for name, md in self.meshes.items():
            lod = self.lod_meshes.get(name + LOD1_SUFFIX)
            n += lod[0].triangles() if lod is not None else md.triangles()
        return n

    def bounds(self):
        pts = list(self.terrain.verts) + [v for m in self.meshes.values() for v in m.verts]
        lo = [round(min(p[k] for p in pts), 3) for k in range(3)]
        hi = [round(max(p[k] for p in pts), 3) for k in range(3)]
        return lo, hi

    def library(self) -> list[str]:
        mats = {m.material for m in self.meshes.values()} | {TURF}
        return sorted(m for m in mats if m.startswith("castle_"))


@dataclass
class Foliage:
    shapes: dict  # shape -> MeshData (local frame, origin at the trunk's foot)
    instances: list  # grounds_dressing.Instance

    def drawn(self) -> list:
        """The instances the cell's file draws (the rocks; the trees are plantings)."""
        return [i for i in self.instances if i.shape in self.shapes]

    def tris(self) -> int:
        return sum(self.shapes[i.shape].triangles() for i in self.drawn())

    def bounds(self):
        lo, hi = [1e9] * 3, [-1e9] * 3
        for i in self.drawn():
            md = self.shapes[i.shape]
            r = max(math.hypot(v[0], v[2]) for v in md.verts) * i.scale
            top = max(v[1] for v in md.verts) * i.scale
            bot = min(v[1] for v in md.verts) * i.scale
            x, y, z = i.pos
            lo = [min(lo[0], x - r), min(lo[1], y + bot), min(lo[2], z - r)]
            hi = [max(hi[0], x + r), max(hi[1], y + top), max(hi[2], z + r)]
        return [round(v, 3) for v in lo], [round(v, 3) for v in hi]


@dataclass
class Grounds3a:
    g: GT.Grounds
    dctx: D.Context
    placed: list
    cells: dict
    foliage: Foliage
    lake: BD.Mesh
    backdrop: list  # BD.Mesh: rock, forest
    keep_outs: list
    atlas_key: str = ""
    plantings: dict = field(default_factory=dict)  # cell -> [foliage.Planting] (vegetation look A)
    castle: list = field(default_factory=list)  # the castle's plan rectangles (the backdrop keeps clear)

    def foliage_tris(self) -> dict:
        """LOD0 triangles of the plantings standing in each grounds cell (the tile budget)."""
        out: dict = {}
        for cell, plants in self.plantings.items():
            if cell == "backdrop":
                continue
            for p in plants:
                c = D.cell_of(self.g, p.t[0], p.t[2]) if cell == "foliage" else cell
                out[c] = out.get(c, 0) + FO.species_geo(p.species, 0).tris
        return out


class _Uv(A.Builder):
    """Just enough of the castle kit's Builder to place pieces and map their UVs
    (world-mapped faces, the props atlas)."""

    def __init__(self, textures: dict, atlas):  # noqa: D401 - no layout needed
        self.textures = textures
        self.atlas = atlas


def _shear_k(piece: geom.Piece, rise: float) -> float:
    """How much a run piece climbs per metre along it (`rise` over its length)."""
    if abs(rise) < 1e-9:
        return 0.0
    xs = [p[0] for f in piece.faces for p in f.pts]
    length = (max(xs) - min(xs) if xs else piece.meta.get("length", 1.0)) or 1.0
    return rise / length


def _shear(piece: geom.Piece, rise: float) -> geom.Piece:
    """A run piece (x along it from 0 to its length) climbing `rise` over its length."""
    if abs(rise) < 1e-9:
        return piece
    k = _shear_k(piece, rise)
    out = geom.Piece(piece.name, walkthrough=piece.walkthrough, meta=dict(piece.meta))
    for f in piece.faces:
        out.faces.append(geom.KFace([(x, y + k * x, z) for x, y, z in f.pts], f.tag, f.uv, f.anchor, f.group))
    return out


def _shade(g: GT.Grounds, f: A.WFace) -> list:
    """COLOR_0 greys of a piece face's corners."""
    if f.mode == "kit" and (f.arg or "").startswith("glow"):
        return [(1.0, 1.0, 1.0)] * len(f.pts)
    n = f.normal()
    out = []
    for x, y, z in f.pts:
        ground = g.height(x, z)
        v = 0.66 + 0.34 * GT.smoothstep(0.0, 1.8, y - ground)
        if n[1] < -0.5:
            v *= 0.82
        v = round(min(max(v, 0.4), 1.0), 4)
        out.append((v, v, v))
    return out


def _merge(cell: str, faces: list, g: GT.Grounds) -> tuple[dict, dict]:
    """One mesh per material for the faces (WFace with .lod both | lod0 | lod1),
    plus a `_lod1` mesh where the far view differs."""
    by: dict[str, list] = {}
    for f in faces:
        by.setdefault(f.material, []).append(f)
    meshes, lods = {}, {}
    for mat in sorted(by):
        near = [f for f in by[mat] if f.lod in ("both", "lod0")]
        far = [f for f in by[mat] if f.lod in ("both", "lod1")]
        name = G.node_name(cell, mat)
        md = A.Builder._mesh(name, mat, near, (0.0, 1.0))
        md.colors = [_shade(g, f) for f in near]
        meshes[name] = md
        if [f.pts for f in far] != [f.pts for f in near]:
            lname = name + LOD1_SUFFIX
            lm = A.Builder._mesh(lname, mat, far, (0.0, 1.0))
            lm.colors = [_shade(g, f) for f in far]
            lods[lname] = (lm, name)
    return meshes, lods


def _lights(cell: str, p: D.Placed, piece: geom.Piece) -> list:
    out = []
    pl = geom.Placement(p.pos, p.yaw)
    for i, lt in enumerate(piece.meta.get("lights", [])):
        pos = pl.point(tuple(lt["pos"]))
        out.append({"id": f"{cell}_{p.id}_{i}", "pos": [round(v, 3) for v in pos], "color": palette.GLOW[lt["glow"]],
                    "intensity": lt["intensity"], "range": lt["range"]})
    if p.piece == "kit_grounds_hut":
        r = piece.meta["radius"]
        for k, a in enumerate((2 * math.pi * 2 / 10, -2 * math.pi * 2 / 10)):  # the windows beside the door
            pos = pl.point((math.sin(a) * (r + 0.8), 1.9, math.cos(a) * (r + 0.8)))
            out.append({"id": f"{cell}_{p.id}_window_{k}", "pos": [round(v, 3) for v in pos],
                        "color": palette.GLOW["candle"], "intensity": 3.0, "range": 7.0})
    return out


def build(layout, terrain_dir: str, textures: dict, props_path: str = PA.PROPS_PATH,
          textures_path: str = PA.TEXTURES_PATH, cellars=None) -> Grounds3a:
    g = GT.Grounds.load(terrain_dir, layout)
    dctx = D.context(g, layout)
    placed = D.placements(dctx)
    inst = D.foliage(dctx)
    kos = D.keep_outs(dctx, placed, inst)
    cell_kits, atlas = PA.build(props_path, textures_path)
    uv = _Uv(textures, atlas)
    painter = GT.Painter(g, D.occluders(placed, inst))

    by_cell: dict[str, list] = {}
    lights: dict[str, list] = {}
    plantings: dict[str, list] = {}
    for p in placed:
        raw = P.build(p.piece, **p.args)
        if raw.meta.get("foliage"):  # vegetation look A: the piece's plantings, placed and sheared with it
            plantings.setdefault(p.cell, []).extend(placed_plantings(raw, p))
        piece = _shear(raw, p.rise)
        faces = uv.piece_faces(piece, geom.Placement(p.pos, p.yaw), MATERIALS, p.id, lod_split=True)
        if p.piece not in FAR_PIECES:
            for f in faces:
                f.lod = "lod0" if f.lod == "both" else ("lod0" if f.lod == "lod0" else "drop")
            faces = [f for f in faces if f.lod != "drop"]
        by_cell.setdefault(p.cell, []).extend(faces)
        lights.setdefault(p.cell, []).extend(_lights(p.cell, p, piece))

    cells = {}
    for cell in G.P3A_GROUNDS_CELLS:
        terrain = GT.terrain_mesh(g, cell, painter)
        lod1 = GT.terrain_lod1(g, cell, painter)
        meshes, lods = _merge(cell, by_cell.get(cell, []), g)
        cg = CellGeo(cell, terrain, lod1, meshes, lods, lights.get(cell, []))
        ck = cell_kits.get(cell)
        if ck is not None and ck.foliage:
            plantings.setdefault(cell, []).extend(ck.foliage)
        if ck is not None:
            # The props kit (the class gates, the find-spot tells): its textured part joins the
            # cell's castle_props at LOD0 (it drops out at LOD1); its flat-coloured parts stay
            # greybox meshes of their own (grounds_export takes them from greybox.py).
            if ck.mesh.faces:
                _add_kit(cg, ck, g)
            cg.kit_materials = sorted(ck.flats)
            cg.kit_tris = sum(len(q) - 2 for polys in ck.flats.values() for q in polys)
            cg.lights += [dict(lt, id=f"{cell}_{lt['id']}") for lt in ck.lights]
        cells[cell] = cg

    shapes = {}
    for tr in inst_trees(inst):
        plantings.setdefault("foliage", []).append(tree_planting(tr))
    for shape, (piece, args) in D.SHAPES.items():
        if shape in TREE_SHAPES:
            continue  # vegetation look A: trees are plantings of the foliage kit
        faces = uv.piece_faces(P.build(piece, **args), geom.Placement((0.0, 0.0, 0.0), 0.0), MATERIALS, shape)
        md = A.Builder._mesh(f"foliage.{shape}", "castle_props", faces, (0.0, 1.0))
        top = max(v[1] for v in md.verts) or 1.0
        md.colors = [[(_tree_grey(p[1], top),) * 3 for p in f.pts] for f in faces]
        shapes[shape] = md

    castle = [(s.x0, s.z0, s.x1, s.z1) for s in layout.surfaces
              if not s.cell.startswith("grounds_") and s.cell != G.P3B_PITCH_CELL]
    bd = BD.Backdrop(g, list(cellars or []), castle)
    lake = BD.water_mesh(bd)
    backdrop = [BD.rock_mesh(bd), BD.forest_mesh(bd)]
    plantings["backdrop"] = BD.forest_plantings(bd)
    return Grounds3a(g, dctx, placed, cells, Foliage(shapes, inst), lake, backdrop, kos, atlas.key(), plantings, castle)


TREE_SHAPES = ("tree_round", "tree_broad", "tree_pine")


def tree_planting(inst) -> FO.Planting:
    """The foliage kit's planting of one of the grounds' tree instances (grounds_dressing.Instance;
    world/solids.py takes its trunk from it too)."""
    _, args = D.SHAPES[inst.shape]
    kind = {"tree_round": "round", "tree_broad": "broad", "tree_pine": "pine"}[inst.shape]
    seed = sum(ord(c) for c in inst.id)  # a pine's variant by its id (stable)
    return FO.tree(kind, args["height"], seed, pos=inst.pos, yaw=inst.yaw, scale=inst.scale)


def placed_plantings(raw: geom.Piece, p) -> list:
    """A placed grounds piece's (grounds_dressing.Placed) plantings in world metres, sheared with
    its run's rise like its faces."""
    k, pl = _shear_k(raw, p.rise), geom.Placement(p.pos, p.yaw)
    return [FO.place(f, lambda q: pl.point((q[0], q[1] + k * q[0], q[2]))) for f in raw.meta.get("foliage", [])]


def inst_trees(inst: list) -> list:
    return [i for i in inst if i.shape in TREE_SHAPES]


def _add_kit(cg: CellGeo, ck, g: GT.Grounds) -> None:
    """Appends the props kit's textured faces (world metres, atlas UVs) to the cell's castle_props."""
    name = G.node_name(cg.cell, "castle_props")
    md = cg.meshes.get(name)
    if md is None:
        md = A.MeshData(name, "castle_props", colors=[])
        cg.meshes[name] = md
        lod = A.MeshData(name + LOD1_SUFFIX, "castle_props", colors=[])
        cg.lod_meshes[lod.name] = (lod, name)
    elif name + LOD1_SUFFIX not in cg.lod_meshes:
        lod = A.MeshData(name + LOD1_SUFFIX, "castle_props", list(md.verts), list(md.faces), list(md.uvs),
                         list(md.colors))
        cg.lod_meshes[lod.name] = (lod, name)
    for k, f in enumerate(ck.mesh.faces):
        base = len(md.verts)
        pts = [ck.mesh.verts[i] for i in f.idx]
        md.verts.extend(tuple(float(c) for c in p) for p in pts)
        md.faces.append(tuple(range(base, base + len(pts))))
        md.uvs.append(list(ck.uvs[k]))
        md.colors.append([(v, v, v) for v in (round(0.7 + 0.3 * GT.smoothstep(0.0, 1.5, p[1] - g.height(p[0], p[2])), 4)
                                              for p in pts)])


def _tree_grey(y: float, top: float) -> float:
    """Trees and rocks: darker toward their foot and inside the crown's lower half."""
    return round(0.62 + 0.38 * GT.smoothstep(0.0, top * 0.8, y), 4)


# -- the lint -----------------------------------------------------------------------------------


def _grey_ok(colors) -> bool:
    return all(abs(c[0] - c[1]) < 1e-6 and abs(c[1] - c[2]) < 1e-6 for face in colors for c in face)


def lint(gr: Grounds3a) -> tuple[list, list, list]:
    """(errors, warnings, report lines): docs/art/castle-kit.md "Budgets", "Exterior"."""
    errs, warns, rep = [], [], []
    b = G.P3A_GROUNDS_BUDGET
    for cell, cg in gr.cells.items():
        d, t, t1 = cg.draws(), cg.tris(), cg.lod1_tris()
        rep.append(f"{cell:16s} {d} draws {t:6d} tris  LOD1 {t1:5d} tris  ({', '.join(sorted(cg.meshes))})")
        if d > b.draws:
            errs.append(f"{cell}: {d} draws (at most {b.draws})")
        if t > b.tris[1]:
            errs.append(f"{cell}: {t} triangles at LOD0 (at most {b.tris[1]})")
        if t < b.tris[0]:
            warns.append(f"{cell}: {t} triangles at LOD0 (the guideline's low end is {b.tris[0]})")
        if t1 > G.P3A_GROUNDS_LOD1_TRIS:
            errs.append(f"{cell}: {t1} triangles at LOD1 (at most {G.P3A_GROUNDS_LOD1_TRIS})")
        errs += GT.height_errors(gr.g, cg.terrain, cell, G.P3A_HEIGHT_TOLERANCE)
        for m in (cg.terrain, cg.terrain_lod1):
            if any(not 0.0 <= s <= 1.0 for s in m.shade):
                errs.append(f"{m.name}: a shade outside 0..1")
            if any(not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0) for u, v in m.uv1):
                errs.append(f"{m.name}: a mask weight outside 0..1")
        for md in list(cg.meshes.values()) + [lm for lm, _ in cg.lod_meshes.values()]:
            if md.colors is None or not _grey_ok(md.colors):
                errs.append(f"{md.name}: COLOR_0 is not greyscale")
        for name, (lm, base) in cg.lod_meshes.items():
            if base not in cg.meshes or name != base + LOD1_SUFFIX:
                errs.append(f"{name}: no LOD0 node {base}")
    f = gr.foliage
    rep.append(f"foliage          {len(f.shapes)} draws {f.tris():6d} tris  ({len(f.drawn())} instances: "
               + ", ".join(f"{s} {sum(1 for i in f.instances if i.shape == s)} x {md.triangles()}" for s, md in f.shapes.items())
               + ")")
    # Vegetation look A: the plantings at LOD0 per grounds cell, with the cell's own triangles,
    # against the style guide's per-cell cap (docs/art/style-guide.md section 6).
    ft = gr.foliage_tris()
    for cell in sorted(ft):
        own = gr.cells[cell].tris() if cell in gr.cells else 0
        rep.append(f"vegetation       {cell:16s} {ft[cell]:6d} tris at LOD0 (+ {own} the cell's own)")
        if own + ft[cell] > G.P3A_TILE_TRIS_CAP:
            errs.append(f"{cell}: {own + ft[cell]} triangles at LOD0 with its vegetation (at most {G.P3A_TILE_TRIS_CAP})")
    nb = len(gr.plantings.get("backdrop", []))
    # The backdrop forest keeps clear of the castle (BD.CASTLE_CLEAR from its plan rectangles).
    bd = BD.Backdrop(gr.g, [], gr.castle)
    if gr.castle:
        for p in gr.plantings.get("backdrop", []):
            if not bd.castle_clear(p.t[0], p.t[2], FO.species_radius(p.species) * p.scale()):
                errs.append(f"backdrop tree at ({p.t[0]:.1f}, {p.t[2]:.1f}): within {BD.CASTLE_CLEAR} m of the castle")
        fm = next((m for m in gr.backdrop if m.material == "backdrop_forest"), None)
        for v in (fm.verts if fm else []):
            if not bd.castle_clear(v[0], v[2], 0.0):
                errs.append(f"backdrop forest mass at ({v[0]:.1f}, {v[2]:.1f}): within {BD.CASTLE_CLEAR} m of the castle")
                break
    rep.append(f"vegetation       backdrop {nb} impostor trees ({nb * FO.species_geo('pine_a', 3).tris} tris)")
    if len(f.shapes) > G.P3A_FOLIAGE_DRAWS:
        errs.append(f"foliage: {len(f.shapes)} draws (at most {G.P3A_FOLIAGE_DRAWS})")
    if f.tris() > G.P3A_FOLIAGE_TRIS:
        warns.append(f"foliage: {f.tris()} triangles (the guideline is about {G.P3A_FOLIAGE_TRIS})")
    for md in f.shapes.values():
        if not _grey_ok(md.colors):
            errs.append(f"{md.name}: COLOR_0 is not greyscale")
    bt = sum(m.triangles() for m in gr.backdrop)
    rep.append(f"backdrop         {len(gr.backdrop)} draws {bt:6d} tris  ("
               + ", ".join(f"{m.name} {m.triangles()}" for m in gr.backdrop) + ")")
    if bt > G.P3A_BACKDROP_TRIS:
        errs.append(f"backdrop: {bt} triangles (at most {G.P3A_BACKDROP_TRIS})")
    if len(gr.backdrop) > G.P3A_BACKDROP_DRAWS:
        errs.append(f"backdrop: {len(gr.backdrop)} draws (at most {G.P3A_BACKDROP_DRAWS})")
    # Grounds edges: no sheer drop from the terrain's end to the backdrop's rock beyond it.
    for m in gr.backdrop:
        if m.material == "backdrop_rock":
            errs += BD.edge_gaps(BD.Backdrop(gr.g), m)
    rep.append(f"lake             1 draws {gr.lake.triangles():6d} tris")
    for m in [gr.lake] + gr.backdrop:
        if any(not 0.0 <= v <= 1.0 for v in m.grey):
            errs.append(f"{m.name}: COLOR_0 outside 0..1")
    # Nothing on a reserved site; every keep-out on its cell's walkable ground; few keep-outs.
    for p in gr.placed:
        x, _, z = p.pos
        if D._in_reserved(gr.dctx, x, z, 0.0):
            errs.append(f"{p.id}: stands on a reserved site")
    for i in f.instances:
        x, _, z = i.pos
        if D._in_reserved(gr.dctx, x, z, 0.0):
            errs.append(f"{i.id}: stands on a reserved site")
    walk_by_cell: dict[str, list] = {}
    for s in gr.dctx.g.walk:
        walk_by_cell.setdefault(D.cell_of(gr.g, (s[0] + s[2]) / 2, (s[1] + s[3]) / 2), []).append(s)
    for k in gr.keep_outs:
        (x0, z0), (x1, z1) = k["min"], k["max"]
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        if not any(a0 <= cx <= a1 and b0 <= cz <= b1 for a0, b0, a1, b1, _ in walk_by_cell.get(k["cell"], [])):
            errs.append(f"{k['id']}: not on {k['cell']}'s walkable ground")
    if len(gr.keep_outs) > D.MAX_KEEP_OUTS:
        errs.append(f"{len(gr.keep_outs)} grounds keep-outs (at most about {D.MAX_KEEP_OUTS})")
    rep.append(f"keep-outs        {len(gr.keep_outs)}")
    return errs, warns, rep
