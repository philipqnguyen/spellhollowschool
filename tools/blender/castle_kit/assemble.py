"""Assembly of kitted cells: the Planner's
faces with the wall openings cut, re-tagged to the castle_* materials; the
rules' placements built, placed and UV-mapped; the props kit on the shared
castle_props atlas; everything merged into one mesh per material, plus the
camera collider and the LOD1 lists. Pure Python (numpy only through the
props atlas); `greybox.py` turns `CellBuild.meshes` into glTF nodes.

    builder = Builder.from_files()
    cb = builder.build("gf_corridor_west")
    cb.meshes["castle_wall_a"].triangles()
"""

from __future__ import annotations

import hashlib
import math
import json
import os
import sys
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_BLENDER, os.path.join(_BLENDER, "world")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import foliage as FO  # noqa: E402
import kit  # noqa: E402
import kit_models as km  # noqa: E402
import layout_lib as L  # noqa: E402
import meshkit as mk  # noqa: E402
import palette  # noqa: E402
from castle_kit import fire as FI  # noqa: E402  (animated fire)
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit import rules as R  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit import shade as SH  # noqa: E402  (castle D1)
from castle_kit import slice as SL  # noqa: E402  (castle D1; aliased: the name shadows the built-in)
from castle_kit.pieces import geom  # noqa: E402

REPO = os.path.abspath(os.path.join(_BLENDER, "..", ".."))
GREY_UV_M = 2.0  # greybox.UV_METRES: kept props keep their greybox UVs

# Greybox material -> castle family for a kitted cell's own Planner faces.
WALLS = {"wall": "wall", "wall_dungeon": "wall_dungeon", "wall_low": "wall_low"}
COLLIDER_MATERIALS = ("wall", "wall_dungeon", "wall_low", "ceiling")

# Placeholder colours of the castle_* materials (docs/art/castle-kit.md
# "glTF conventions"): the greybox colour each replaces.
PLACEHOLDER = {
    "castle_wall": "wall",
    "castle_floor_long": "floor_room",
    "castle_floor_square": "floor_corridor",
    "castle_ceiling": "ceiling",
    "castle_trim": "trim",
    "castle_wood": "prop_wood",
    "castle_glass": "glow_sky",
    "castle_cloth": "fabric_house_a",
    "castle_sky": "glow_sky",
    "castle_portraits": "prop_canvas",
}


def placeholder_colour(material: str):
    """Linear RGB of a castle_* placeholder (white for castle_props)."""
    if material == "castle_props":
        return (1.0, 1.0, 1.0)
    for key in sorted(PLACEHOLDER, key=len, reverse=True):
        if material.startswith(key):
            return L.MATERIALS[PLACEHOLDER[key]][0]
    return L.MATERIALS["trim"][0]


def greybox_slot(material: str) -> tuple[str, str]:
    """The props-atlas slot a flat greybox colour (`g:<material>` kit faces,
    the find-spot tells) maps to in a kitted cell."""
    if material.startswith("glow_"):
        return "glow", palette.GLOW[material[5:]].upper()
    if material.startswith("fabric_") or material in ("prop_canvas", "prop_parchment"):
        key = material[7:] if material.startswith("fabric_") else material[5:]
        return "fabric", palette.CASTLE[key].upper()
    key = material[5:] if material.startswith("prop_") else material
    return "metal", palette.CASTLE[key].upper()


@dataclass
class WFace:
    """A face of a kitted cell in world space, its material resolved, its
    final UVs (Blender convention) and where it came from."""

    pts: list
    material: str
    mode: str  # world | trim | fit | kit | grey
    arg: str | None
    uv: list
    src: str  # "base", "floor", "props:<kit id>" or a placement id
    walkthrough: bool = False
    lod: str = "both"  # both | lod0 | lod1
    ramp: str | None = None  # value ramp (palette.VALUE_RAMPS) baked into COLOR_0
    group: str | None = None  # castle kit M3 + M4: the named sub-mesh (grid.GROUP_RE) these faces merge into

    def normal(self):
        return geom.unit(geom.newell(self.pts))

    def area(self) -> float:
        return geom.norm(geom.newell(self.pts)) / 2.0


@dataclass
class MeshData:
    name: str
    material: str
    verts: list = field(default_factory=list)
    faces: list = field(default_factory=list)  # vertex index tuples
    uvs: list = field(default_factory=list)  # per face, per corner
    colors: list | None = None  # per face, per corner RGB (COLOR_0)
    # Castle D1 (Bake): the WFace each face came from (the bake reads ramps, glow tiles).
    src_faces: list = field(default_factory=list, repr=False, compare=False)
    # The node's origin in world metres (None: the world origin). Verts stay
    # in world metres here; greybox.py writes them relative to it and puts the node there
    # (the hourglass sand: the base of the sand, so a Y scale fills it from the bottom).
    origin: tuple | None = None

    def triangles(self) -> int:
        return sum(len(f) - 2 for f in self.faces)


@dataclass
class CellBuild:
    cell: str
    plan: S.CellPlan
    uses: list
    faces: list  # WFace
    meshes: dict  # name -> MeshData (drawn at LOD0)
    lod_meshes: dict  # name -> (MeshData, base name, distance)
    collider: list  # polygons
    lights: list
    pieces: dict  # use id -> geom.Piece (for the lint's meta checks)
    # Castle kit M3 + M4: node name -> glTF extras for drawn meshes and their
    # LOD1 nodes beyond kit_lod (kit_anim; docs/art/castle-kit.md).
    extras: dict = field(default_factory=dict)
    # Castle kit M3: the manifest's `seats` rows [x0, z0, x1, z1, y, yaw, front, back, drop] (benches, chairs).
    seats: list = field(default_factory=list)
    # ... and those of them (as tuples) with a back or arms over the seat (seat_rows): never two-sided.
    seat_backs: set = field(default_factory=set)
    # Castle M5b (Furniture, 26.5): the manifest's `floor_seats` rows [x0, z0, x1, z1, y] (rugs, hearth steps).
    floor_seats: list = field(default_factory=list)
    # Castle D1 (Bake): the edge loops' and the bake's numbers (slice.Stats, shade.Stats; None: not run).
    slice_stats: object = None
    shade_stats: object = None
    # Vegetation look A (world/foliage.py): the cell's plantings of the foliage kit (world metres),
    # from its pieces' and its props kit's meta / Kit `foliage`; the manifest's `foliage`.
    foliage: list = field(default_factory=list)
    # Animated fire (docs/architecture.md "Rendering"): the manifest's `fire` entry (None: nothing burns).
    fire: dict | None = None
    # ... and the flames of the cell's props kit models (fires, candles), world metres.
    kit_fires: tuple = ((), ())

    def triangles(self) -> int:
        return sum(m.triangles() for m in self.meshes.values())

    def draws(self) -> int:
        return sum(1 for m in self.meshes.values() if m.faces)  # Empty marker nodes draw nothing

    def collider_triangles(self) -> int:
        return sum(len(p) - 2 for p in self.collider)

    def library(self) -> list[str]:
        mats = {m.material for m in self.meshes.values()} | {m.material for m, _, _ in self.lod_meshes.values()}
        return sorted(m for m in mats if m.startswith("castle_"))

    def bounds(self):
        pts = [v for m in self.meshes.values() for v in m.verts] + [p for poly in self.collider for p in poly]
        lo = [min(p[k] for p in pts) for k in range(3)]
        hi = [max(p[k] for p in pts) for k in range(3)]
        return lo, hi

    def digest(self) -> str:
        """A fingerprint of the drawn geometry (byte-identity tests)."""
        h = hashlib.sha1()
        for name in sorted(self.meshes):
            m = self.meshes[name]
            h.update(name.encode())
            h.update(json.dumps([[round(c, 6) for c in v] for v in m.verts]).encode())
            h.update(json.dumps([list(f) for f in m.faces]).encode())
            h.update(json.dumps([[[round(a, 6), round(b, 6)] for a, b in uv] for uv in m.uvs]).encode())
        return h.hexdigest()[:16]


def wall_holes(cp: S.CellPlan) -> list[L.WallHole]:
    """The Planner holes of a kitted cell's windows, niches and blind doors."""
    out = []
    for op in cp.openings:
        if op.kind == "arch":
            continue
        out.append(L.WallHole(op.surface, op.axis, op.c, op.sign, op.t0, op.t1, op.y0, op.y1, op.depth is None))
    return out


def arch_head_closers(cp) -> list:
    """Camera-collider quads that close each arch's pointed head: from the
    springing line to the top of the door hole, on the wall face and at the
    end of the arch's reveal. The Planner's door hole is a rectangle and the
    arch is not, so without them the camera could pass through the hole
    into the arch's spandrels and soffit (found at integration: about 0.5 %
    of frames at the corridor ends)."""
    out = []
    for o in cp.openings:
        if o.kind != "arch":
            continue
        ys = o.y0 + G.arch_springing(o.t1 - o.t0, o.y1 - o.y0)
        for d in sorted({0.0, o.reveal}):
            off = o.c - o.sign * d
            quad = ((o.t0, ys), (o.t1, ys), (o.t1, o.y1), (o.t0, o.y1))
            out.append([(t, y, off) if o.axis == "z" else (off, y, t) for t, y in quad])
    return out


class Builder:
    """Shared inputs of every kitted cell in one export."""

    def __init__(self, layout: L.Layout, props: list, kit_entries: list, open_edges, kit_plan: S.KitPlan,
                 textures: dict, props_path: str, textures_path: str):
        self.layout = layout
        self.props = props
        self.kit_entries = kit_entries
        self.open_edges = set(open_edges)
        self.kit_plan = kit_plan
        self.textures = textures
        self.cell_kits, self.atlas = PA.build(props_path, textures_path)
        replaced = {r for cp in kit_plan.cells.values() for r in cp.replaces}
        holes = [h for cp in kit_plan.cells.values() for h in wall_holes(cp)]
        # The M1.0 greybox (un-kitted cells, colliders) and the kitted cells'
        # Planner faces with their wall openings and without replaced props.
        self.plan0 = L.plan_layout(layout, props, self.open_edges)
        self.plan1 = L.plan_layout(layout, [p for p in props if p.id not in replaced], self.open_edges, holes)
        # Castle D1 (Bake): the edge loops run on every build; rays are cast only
        # when asked (the export, greybox.py; inside Blender), otherwise every kit
        # mesh's COLOR_0 is the gradient alone (lint, tests).
        self.d1_slice = True
        self.cast_ao = False

    def wall_pieces(self) -> list:
        """The Planner's wall pieces of the layout (no holes), built once."""
        if getattr(self, "_wall_pieces", None) is None:
            self._wall_pieces = G.planner_pieces(self.layout)
        return self._wall_pieces

    @classmethod
    def from_files(cls, layout_path=S.LAYOUT_PATH, props_path=S.PROPS_PATH, kit_path=S.KIT_PATH,
                   textures_path=S.TEXTURES_PATH) -> "Builder":
        layout = L.load_layout(layout_path)
        props = L.load_props(props_path) if os.path.exists(props_path) else []
        entries = kit.load_kit(props_path) if os.path.exists(props_path) else []
        open_edges = L.load_open_edges(props_path) if os.path.exists(props_path) else set()
        plan, rep = S.resolve(S.load_json(kit_path), layout, props_path, S.load_texture_set(textures_path))
        if not rep.ok:
            raise S.KitDataError("castle_kit.json:\n  " + "\n  ".join(rep.errors))
        return cls(layout, props, entries, open_edges, plan, S.load_texture_set(textures_path), props_path, textures_path)

    # -- UVs -------------------------------------------------------------------------

    def sheet(self, material: str) -> dict:
        tex = self.textures["materials"][G.ext_base(material)]["texture"]  # Castle M5c (Facades): a clone's base row
        return self.textures["sheets"][tex]

    def tile_m(self, material: str) -> float:
        return self.textures["materials"][G.ext_base(material)].get("tile_m", G.STONE_TILE_M)  # Castle M5c (Facades)

    def uvs(self, material: str, mode: str, arg, pts, local_uv, s_off: float, k: int):
        if mode == "world":
            n = geom.newell(pts)
            return [G.world_uv(p, n, self.tile_m(material)) for p in pts]
        if mode == "trim":
            sheet = self.sheet(material)
            return [G.strip_uv(sheet, arg, s + s_off, q) for s, q in local_uv]
        if mode == "fit":
            sheet = self.sheet(material)
            return [G.tile_uv(sheet, arg, a, b) for a, b in local_uv]
        if mode == "kit":
            tile, _, colour = arg.partition(":")
            rect = self.atlas.rect(tile, colour or "-")
            face = mk.Face((), tile, list(local_uv))
            return kit.face_uvs(face, rect, kit.TILES[tile][1], k)
        if mode == "grey":
            n = geom.newell(pts)
            ax = max(range(3), key=lambda i: abs(n[i]))
            out = []
            for x, y, z in pts:
                if ax == 1:
                    u, v = x, -z
                elif ax == 0:
                    u, v = (-z if n[0] > 0 else z), y
                else:
                    u, v = (x if n[2] > 0 else -x), y
                out.append((u / GREY_UV_M, v / GREY_UV_M))
            return out
        raise ValueError(mode)

    # -- the build -------------------------------------------------------------------

    def face(self, pts, tag: str, local_uv, s_off: float, src: str, materials: dict, k: int, walkthrough=False,
             lod="both", ramp=None) -> WFace:
        """A world face from a face tag: material resolved through `materials`
        (family -> variant), UVs computed (`k` seeds the props atlas's
        per-face offset)."""
        ft = G.parse_face_tag(tag)
        mat = ext_material_m5c(ft.material, materials)  # Castle M5c (Facades): the family resolved, `@ext` kept
        if ft.mode in ("trim", "fit", "kit") and local_uv is None:
            local_uv = mk.planar_uv(pts, geom.newell(pts)) if ft.mode == "kit" else [(0.0, 0.0)] * len(pts)
        uv = self.uvs(mat, ft.mode, ft.arg, pts, local_uv, s_off, k)
        if ramp is None and mat.startswith("castle_wall"):
            ramp = "wall"
        return WFace(list(pts), mat, ft.mode, ft.arg, uv, src, walkthrough, lod, ramp)

    def piece_faces(self, piece: geom.Piece, pl: geom.Placement, materials: dict, src: str, lod_split: bool = False,
                    k0: int = 0, group: str | None = None) -> list[WFace]:
        """A built piece placed in the world (LOD0 faces, and with
        `lod_split` its LOD1 faces marked lod1), in `group` (castle kit M3 + M4)."""
        out = []
        s_off = pl.s_offset()
        k = k0
        split = lod_split and piece.lod1 is not None
        for faces, lod in ((piece.faces, "lod0" if split else "both"), (piece.lod1 if split else [], "lod1")):
            for f in faces:
                out.append(self.face([pl.point(p) for p in f.pts], f.tag, f.uv, s_off if f.anchor else 0.0, src,
                                     materials, k, piece.walkthrough, lod))
                # A face's own group wins; a sub-group tag (a double door's leaf) extends the placement's.
                out[-1].group = G.sub_group(group, f.group)
                if out[-1].mode == "kit":
                    k += 1
        return out

    def build(self, cell: str) -> CellBuild:
        cp = self.kit_plan.cells[cell]
        ctx = R.context(self.layout, cp, self.kit_entries, self.open_edges)
        uses = R.placements(ctx)
        uses = wings_b_uses_p3c(cp, uses)  # The Owl Loft's railings, the cellars' steps
        faces: list[WFace] = []
        built: dict = {}
        thresholds = {u.id.split(":", 1)[1] for u in uses if u.role == "threshold"}
        borders = {u.id.split(":", 1)[1]: u for u in uses if u.role == "floor_border"}
        k = [0]

        def emit(pts, tag, local_uv, s_off, src, walkthrough=False, lod="both", ramp=None):
            faces.append(self.face(pts, tag, local_uv, s_off, src, cp.materials, k[0], walkthrough, lod, ramp))
            if faces[-1].mode == "kit":
                k[0] += 1

        # 1. The Planner's faces (walls with their openings, ceilings, roofs).
        # A vaulted cell (castle kit M2) drops its flat ceiling above the
        # springing line: the vault heroes are its ceiling.
        vault_y = None
        if cp.springing is not None:
            vault_y = min(s.y for s in self.layout.surfaces if s.cell == cell and s.kind != "door") + cp.springing
        for (c, mat), polys in sorted(self.plan1.polys.items()):
            if c != cell:
                continue
            for poly in polys:
                n = geom.newell(poly)
                if mat in ("railing", "floor_stair") or mat.startswith("floor_"):
                    continue
                if mat in WALLS:
                    emit(poly, "castle_wall|world", None, 0.0, "base", ramp=WALLS[mat])
                elif mat == "wall_exterior":
                    emit(poly, "castle_wall|world", None, 0.0, "base", ramp="wall_exterior")
                elif mat == "roof":
                    emit(poly, "castle_wall|world", None, 0.0, "base", ramp="-")
                elif mat == "ceiling":
                    if vault_y is not None and min(p[1] for p in poly) >= vault_y - 1e-6:
                        continue
                    emit(poly, "castle_ceiling|world", None, 0.0, "base")
                elif mat == "trim":
                    if abs(n[1]) > 0.5 * geom.norm(n):
                        emit(poly, "castle_ceiling|world", None, 0.0, "base")
                    else:
                        ys = [p[1] for p in poly]
                        y0, h = min(ys), (max(ys) - min(ys)) or 1.0
                        uv = [(G.world_uv(p, n, 1.0)[0], (p[1] - y0) / h * 0.3) for p in poly]
                        emit(poly, "castle_trim|trim:rib", uv, 0.0, "base")
                else:
                    # A greybox prop box the cell keeps: flat, as before.
                    faces.append(WFace(list(poly), mat, "grey", None, self.uvs(mat, "grey", None, poly, None, 0.0, 0),
                                       "base"))

        tower_shell_m5a(self, cp, emit)  # Castle M5a (Tower): the tower shell the Planner leaves out

        # 2. Floors: the field inside the border, door floors without a threshold.
        for s in sorted((s for s in self.layout.surfaces if s.cell == cell and not s.sloped), key=lambda s: s.id):
            if s.kind == "door" and s.id in thresholds:
                continue
            # Castle kit M4: flush hero inlays (the floor compass) draw that floor themselves;
            # Only the floor they lie on.
            cuts = R.floor_cuts(cp, s.y)
            rect = (s.x0, s.z0, s.x1, s.z1)
            field_rects = [rect]
            if s.id in borders:
                field_rects = L.rect_subtract(rect, [tuple(r) for r in borders[s.id].kwargs()["rects"]])
            if cuts:
                field_rects = [r for fr in field_rects for r in L.rect_subtract(fr, cuts)]
            for x0, z0, x1, z1 in field_rects:
                emit(L.horizontal_quad(x0, z0, x1, z1, s.height, True), "castle_floor|world", None, 0.0, "floor")

        # 3. Placed pieces.
        for u in uses:
            piece = P.build(u.piece, **u.kwargs())
            built[u.id] = piece
            group = getattr(u, "group", None)
            placed = self.piece_faces(piece, geom.Placement(u.origin, u.yaw), cp.materials, u.id,
                                      lod_split=u.lod_distance is not None or group is not None, k0=k[0], group=group)
            k[0] += sum(1 for f in placed if f.mode == "kit")
            faces.extend(placed)
        # Castle kit M3 (the Great Hall): the sky panels; no castle_ceiling under a sky.
        if cp.sky:
            faces[:] = self.sky_m3(cp, faces)
        faces.extend(dome_sky_m5b(uses, built))  # Castle M5b (Upper rooms): the starry dome's castle_sky panels

        # 4. The props kit of this cell (stations, tells) on the shared atlas.
        kit_fires: tuple = ([], [])  # animated fire: the models' flames (fires, candles)
        lights = []
        plants = []
        for u in uses:  # Vegetation look A: the pieces' plantings, placed with them
            pl = geom.Placement(u.origin, u.yaw)
            plants += [FO.place(p, pl.point) for p in built[u.id].meta.get("foliage", [])]
        for e in self.kit_entries:
            if e.cell != cell or e.id in cp.replaces:
                continue
            model = km.build(e.model, **dict(e.args))
            plants += [FO.place(p, lambda q, e=e: kit.place(q, e)) for p in model.foliage]
            for f in model.mesh.faces:
                pts = [kit.place(model.mesh.verts[i], e) for i in f.idx]
                if f.material.startswith("g:"):
                    tile, colour = greybox_slot(f.material[2:])
                else:
                    tile, colour = f.material, model.palette.get(f.zone, PA.WHITE)
                arg = tile if not kit.TILES[tile][2] else f"{tile}:{colour.upper()}"
                emit(pts, f"castle_props|kit:{arg}", f.uvs, 0.0, f"props:{e.id}", True)
            # Animated fire: the model's flames (fire.note), placed like its faces.
            notes = model.mesh.__dict__.get("fire_notes")
            if notes:
                pf, pc = FI.placed(notes, lambda p, e=e: kit.place(p, e))
                kit_fires[0].extend(pf)
                kit_fires[1].extend(pc)
        ck = self.cell_kits.get(cell)
        if ck is not None:
            lights = list(ck.lights)

        swing_p3c(self, cp, faces, uses)  # The moving flights' swing nodes
        secrets_p3d(cp, uses)  # The entrances and the door as moving nodes
        swing_trim_p3d(self, cp, faces)  # The 3c leftover, the swing nodes' corners trimmed
        clock_parapet_p3c(self, cp, faces)  # The Clock Tower Courtyard's parapet
        bridge_base_p3c(cp, faces)  # The bridge kind
        ext_split_m5c(self, cp, faces, uses)  # Castle M5c (Facades): outside faces on their `@ext` clones
        exterior_stone_p3a(self, cp, faces)  # The courtyard's grounds side in the outside stone
        owl_loft_ext_p3c(cp, faces)  # The Owl Loft's outside on the outside stone's @ext clone

        # Castle D1 (Bake): edge loops on the world-mapped faces (castle_kit/slice.py, 19.3).
        slice_stats = None
        if self.d1_slice:
            faces[:], slice_stats = self.slice_d1(cp, faces)

        meshes, lod_meshes, extras = self._merge(cell, faces, uses, cp.groups)
        swing_origins_p3c(self, cell, meshes)  # Origins at the pivots
        secrets_origins_p3d(cell, uses, meshes, built)  # Origins on the wall faces (a leaf's on its hinge)
        facade_lod_m5c(cp, lod_meshes)  # Castle M5c (Facades): the outside meshes' LOD1 at grid.FACADE_LOD
        collider = [list(poly) for (c, mat), polys in sorted(self.plan0.polys.items()) if c == cell
                    and (mat in COLLIDER_MATERIALS or mat.startswith("floor_")) for poly in polys]
        collider += arch_head_closers(cp)
        # Castle kit M2: the coarse shells hero pieces carry (the vaults).
        for u in uses:
            shell = built[u.id].meta.get("collider")
            if shell and not is_swing_use(u):  # No static shell for a moving flight
                pl = geom.Placement(u.origin, u.yaw)
                collider += [[pl.point(p) for p in poly] for poly in shell]
        cb = CellBuild(cell, cp, uses, faces, meshes, lod_meshes, collider, lights, built, extras)
        cb.foliage = plants
        cb.kit_fires = kit_fires
        self.finish_m3(cb)  # castle kit M3: the sky's collider shell, seats, the pieces' lights
        cb.floor_seats = floor_seats_m5b(cb)  # Castle M5b (Furniture): rugs and hearth steps
        lights_m5b_upper(cb)  # Castle M5b (Upper rooms): one id per light a hero piece brings
        lights_m5c_courtyard(cb)  # Castle M5c (Courtyard): one id per lamp post's light
        # Castle D1 (Bake): the vertex-AO bake writes every kit mesh's COLOR_0.
        cb.slice_stats = slice_stats
        cb.shade_stats = SH.bake(cb, self, self._ramp_range(faces), cast=self.cast_ao)
        wings_b_finish_p3c(cb)  # The Owl Loft's outside LOD distance and owl markers
        return cb

    # -- Castle D1 (Bake): the edge loops (docs/art/castle-kit.md "Shading: the AO bake") -------------------

    @staticmethod
    def slice_budget(kind: str, unsliced: int) -> int:
        """The LOD0 triangles the loops may add to one cell: its share of
        grid.slice_allowance (a budget over several cells splits the cap and
        the floor evenly), and for a budget with a drawn cap (the Grand
        Staircase) never more than its share of SLICE_DRAWN_GROWTH, so the
        drawn estimate cannot grow by more."""
        b = G.BUDGETS[kind]
        share = min(G.SLICE_ADD_MAX[kind] // b.cells, max(G.SLICE_GROWTH_FLOOR // b.cells, int(G.SLICE_GROWTH * unsliced)))
        if b.drawn is not None:
            share = min(share, G.SLICE_DRAWN_GROWTH // b.cells)
        return share

    def slice_d1(self, cp: S.CellPlan, faces: list) -> tuple[list, object]:
        """The cell's faces with the world-mapped ones cut (slice.slice_faces)
        within the cell's loop budget; their UVs recomputed by the world formula."""
        unsliced = sum(len(f.pts) - 2 for f in faces if f.lod != "lod1")
        ctx = SL.context(self.layout, cp.cell, faces, cp.springing)
        out, st = SL.slice_faces(faces, ctx, lambda m, p: self.uvs(m, "world", None, p, None, 0.0, 0),
                                 self.slice_budget(cp.budget, unsliced))
        st.unsliced = unsliced
        return out, st

    # -- castle kit M3 (the Great Hall, docs/art/castle-kit.md) ---------------------

    def sky_m3(self, cp: S.CellPlan, faces: list[WFace]) -> list[WFace]:
        """A sky cell's faces: the Planner's ceilings left under the sky
        (the door passage's) become wall stone, so the cell draws no
        castle_ceiling; then the enchanted ceiling's panels in castle_sky
        (unlit, the panel's own UVs: u along the hall, v 0 at the plate and
        1 at the ridge)."""
        out = []
        for f in faces:
            if f.src == "base" and f.material == "castle_ceiling":
                f = self.face(f.pts, "castle_wall|world", None, 0.0, "base", cp.materials, 0)
            out.append(f)
        for panel in cp.sky:
            out.append(WFace([tuple(p) for p in panel.corners], "castle_sky", "sky", None, [tuple(v) for v in panel.uv],
                             f"sky:{panel.id}"))
        return out

    def finish_m3(self, cb: CellBuild) -> None:
        """The sky's coarse camera shell (the panels themselves: the camera at
        full zoom stays under the sky), the manifest's seats and the lights
        hero pieces bring (meta "lights", within kit.LIGHTS_PER_CELL)."""
        cp = cb.plan
        if cp.sky:
            cb.collider += [[tuple(p) for p in panel.corners] for panel in cp.sky]
        pairs = seat_rows(cb, [e for e in self.kit_entries if e.cell == cb.cell and e.id not in cp.replaces])
        cb.seats = sorted(r for r, _ in pairs)
        cb.seat_backs = {tuple(r) for r, backed in pairs if backed}
        for u in cb.uses:
            got = cb.pieces[u.id].meta.get("lights") if u.id in cb.pieces else None
            for i, lt in enumerate(got if isinstance(got, list) else []):  # (a window's "lights" counts its lights)
                pos = geom.Placement(u.origin, u.yaw).point(tuple(lt["pos"]))
                if len(cb.lights) >= kit.LIGHTS_PER_CELL:
                    break
                cb.lights.append({"id": f"{cb.cell}_{u.role}_{i}", "pos": [round(v, 3) for v in pos],
                                  "color": palette.GLOW[lt["glow"]], "intensity": lt["intensity"], "range": lt["range"]})
        fire_m(cb)

    def _merge(self, cell: str, faces: list[WFace], uses, specs: dict | None = None):
        """One mesh per (material, group): `<cell>.<material>` for the main
        mesh and `<cell>.<material>.<group>` for named groups (castle kit M3 +
        M4), each with a `_lod1` node when its faces have LOD1 ones. Returns
        (meshes, lod meshes, extras by node name)."""
        specs = specs or {}
        lo_hi = self._ramp_range(faces)
        groups: dict[tuple, list[WFace]] = {}
        lod_groups: dict[tuple, list[WFace]] = {}
        has_lod = {(f.material, f.group) for f in faces if f.lod != "both"}
        for f in faces:
            key = (f.material, f.group)
            if f.lod in ("both", "lod0"):
                groups.setdefault(key, []).append(f)
            if key in has_lod and f.lod in ("both", "lod1"):
                lod_groups.setdefault(key, []).append(f)
        order = lambda k: (k[0], k[1] or "")  # noqa: E731
        meshes = {}
        for mat, grp in sorted(groups, key=order):
            name = G.node_name(cell, mat, grp)
            meshes[name] = self._mesh(name, mat, groups[(mat, grp)], lo_hi)
        dist = min((u.lod_distance for u in uses if u.lod_distance is not None), default=R.LOD_DISTANCE)
        lod_meshes = {}
        extras: dict[str, dict] = {}
        for mat, grp in sorted(lod_groups, key=order):
            base = G.node_name(cell, mat, grp)
            spec = specs.get(grp) if grp else None
            d = spec.lod_distance if spec is not None and spec.lod_distance is not None else dist
            name = G.lod1_name(base)
            lod_meshes[name] = (self._mesh(name, mat, lod_groups[(mat, grp)], lo_hi), base, d)
        for name in meshes:
            grp = name.split(".", 2)[2] if name.count(".") == 2 else None
            spec = specs.get(grp) if grp else None
            if spec is not None and spec.anim:
                extras.setdefault(name, {})[G.EXTRAS_ANIM] = dict(spec.anim, group=grp)
                if dict(spec.anim).get("kind") == G.ANIM_FILL:
                    meshes[name].origin = fill_origin(meshes[name])  # the hourglass sand
        return meshes, lod_meshes, extras

    @staticmethod
    def _ramp_range(faces):
        ys = [p[1] for f in faces if f.src == "base" and f.ramp == "wall" for p in f.pts]
        return (min(ys), max(ys)) if ys else (0.0, 1.0)

    @staticmethod
    def _mesh(name: str, material: str, faces: list[WFace], lo_hi) -> MeshData:
        """One merged mesh. Castle D1 (Bake): COLOR_0 is no longer the wall ramp
        here (assemble.grey retired for kit cells): shade.bake writes it on every
        kit mesh from the faces kept in `src_faces`."""
        m = MeshData(name, material)
        index: dict = {}
        for f in faces:
            idx = []
            # Faces share corners only with faces facing the same way: the
            # glass's two sides would otherwise be one face twice (Blender
            # drops duplicates), and flat shading splits them anyway.
            n = f.normal()
            nk = (round(n[0], 3), round(n[1], 3), round(n[2], 3))
            for p in f.pts:
                key = (round(p[0], 6), round(p[1], 6), round(p[2], 6), nk)
                if key not in index:
                    index[key] = len(m.verts)
                    m.verts.append((float(p[0]), float(p[1]), float(p[2])))
                idx.append(index[key])
            m.faces.append(tuple(idx))
            m.uvs.append(list(f.uv))
            m.src_faces.append(f)
        return m


def fire_m(cb: CellBuild) -> None:
    """Animated fire (docs/architecture.md "Rendering"): the flames the
    placed pieces list (meta "fires", "candles") in world metres as the
    manifest's `fire` entry; candles of a moving group under its node's
    name. The cell's own lights near a fire flicker with it."""
    fires, candles, groups = list(cb.kit_fires[0]), list(cb.kit_fires[1]), {}
    for u in cb.uses:
        piece = cb.pieces.get(u.id)
        if piece is None:
            continue
        pf, pc = FI.placed(piece.meta, geom.Placement(u.origin, u.yaw).point)
        fires += pf
        group = getattr(u, "group", None)
        if group is not None and pc:
            groups.setdefault(G.node_name(cb.cell, "castle_props", group), []).extend(pc)
        else:
            candles += pc
    cb.fire = FI.manifest(fires, candles, groups)
    FI.link_lights(cb.lights, fires)


SEAT_MODELS = ("spectator_bench",)  # props.json kit models people sit on (castle kit M3 seats)


def seats_m3(cb: CellBuild, kit_entries) -> list:
    """Manifest `seats` (castle kit M3, 17.4): one row [x0, z0, x1, z1, y,
    yaw, front, back, drop] per bench or chair seat in world metres (`y` the
    seat's top, `yaw` the way a sitter faces, 0 = +z; `front`, `back` and
    `drop` as the piece says, hall_furniture.py): the placed pieces' meta
    "seats" and the kept props.json benches (backless: their half depth
    either way, their top over their foot)."""
    return sorted(r for r, _ in seat_rows(cb, kit_entries))


def seat_rows(cb: CellBuild, kit_entries) -> list:
    """`seats_m3`'s rows, unsorted, each as (row, backed): whether the seat
    has a back or arms over it (world/seat_sides.py `backed`, from its own
    piece's faces; the props.json benches are backless), so it is never
    two-sided."""
    import math

    import seat_sides

    def norm(a):  # into (-pi, pi], 4 decimals
        a = (a + math.pi) % (2 * math.pi) - math.pi
        if a <= -math.pi + 1e-4:
            a += 2 * math.pi
        return round(a, 4) + 0.0

    rows = []
    for u in cb.uses:
        piece = cb.pieces.get(u.id)
        if piece is None:
            continue
        pl = geom.Placement(u.origin, u.yaw)
        own = None
        for local in piece.meta.get("seats", []):
            x0, z0, x1, z1, y, yaw, front, back, drop = local
            if own is None:
                own = [p for f in piece.faces for p in f.pts]
            pts = [pl.point((x, 0.0, z)) for x in (x0, x1) for z in (z0, z1)]
            rows.append(([round(min(p[0] for p in pts), 3), round(min(p[2] for p in pts), 3), round(max(p[0] for p in pts), 3),
                          round(max(p[2] for p in pts), 3), round(u.origin[1] + y, 3), norm(u.yaw + yaw),
                          round(front, 3), round(back, 3), round(drop, 3)], seat_sides.backed(own, local)))
    for e in kit_entries:
        if e.model not in SEAT_MODELS:
            continue
        lo, hi = km.build(e.model, **dict(e.args)).mesh.bounds()
        pts = [kit.place((x, 0.0, z), e) for x in (lo[0], hi[0]) for z in (lo[2], hi[2])]
        hd = round((hi[2] - lo[2]) / 2.0, 3)
        rows.append(([round(min(p[0] for p in pts), 3), round(min(p[2] for p in pts), 3), round(max(p[0] for p in pts), 3),
                      round(max(p[2] for p in pts), 3), round(e.pos[1] + hi[1], 3), norm(e.yaw), hd, hd, round(hi[1], 3)],
                     False))
    return rows


def grey(rgb) -> float:
    """A value ramp's multiplier as a grey (castle kit M3: COLOR_0 on kit
    meshes carries value only, r = g = b, so a palette change of the
    textures is the only change of hue; docs/art/castle-kit.md "Colours come from palette data only").
    Rec. 709 luminance, rounded like palette.ramp_factor."""
    return round(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2], 4)


def piece_triangles() -> dict[str, int]:
    """Triangles of every catalogue piece (README, the gallery)."""
    out = {}
    for name, params in P.CATALOGUE.items():
        p = P.build(name, **params)
        out[name] = p.triangles()
        if p.lod1 is not None:
            out[name + " (LOD1)"] = sum(len(f.pts) - 2 for f in p.lod1)
    return out


# =====================================================================================
# Castle M5a (Tower): docs/art/castle-kit.md "The kitted cells", "Pieces".
# =====================================================================================


def tower_shell_m5a(builder: "Builder", cp: S.CellPlan, emit) -> None:
    """A tower cell's exterior faces the Planner leaves out behind its slit
    windows (rules.tower_shell_patches: the wall mass counted as shared with
    common room C, which stands only above them), drawn as the cell's own
    shell, holed by the windows (their outer spandrels close the heads)."""
    for axis, c, sign, ta, tb, y0, y1, plane, holes in R.tower_shell_patches(builder.layout, cp):
        for q in L.holed_quads(axis, plane, ta, tb, y0, y1, -sign, holes):
            emit(q, "castle_wall|world", None, 0.0, "base", ramp="wall_exterior")


# =====================================================================================
# Castle M5b (Furniture): docs/art/castle-kit.md.
# =====================================================================================


def floor_seats_m5b(cb: CellBuild) -> list:
    """Manifest `floor_seats` (castle M5b, 26.5): one row [x0, z0, x1, z1, y]
    per rug or hearth step in world metres (`y` the top a character sits
    on), from the placed pieces' meta "floor_seats" ([x0, z0, x1, z1, y] in
    the piece's frame); sorted."""
    rows = []
    for u in cb.uses:
        piece = cb.pieces.get(u.id)
        if piece is None:
            continue
        pl = geom.Placement(u.origin, u.yaw)
        for x0, z0, x1, z1, y in piece.meta.get("floor_seats", []):
            pts = [pl.point((x, 0.0, z)) for x in (x0, x1) for z in (z0, z1)]
            rect = [min(p[0] for p in pts), min(p[2] for p in pts), max(p[0] for p in pts), max(p[2] for p in pts)]
            # The furniture standing on it is cut out (the round coffee tables).
            for r in cut_floor_seat(rect, floor_seat_obstacles(cb, u, u.origin[1])):
                rows.append([round(r[0], 3), round(r[1], 3), round(r[2], 3), round(r[3], 3), round(u.origin[1] + y, 3)])
    return sorted(rows)


# No floor-seat point inside furniture (docs/architecture.md).
FLOOR_SEAT_FLAT = 0.031  # a piece wholly this low over the floor (a rug, an inlay) is sat on, not cut out
FLOOR_SEAT_CLEAR = 1.0  # what stands under this over the floor (a table, a chair, pots on a step) is cut out
FLOOR_SEAT_ON_TOP = 0.5  # a piece placed this high or more, nothing under its foot, stands on furniture (not cut out)


def floor_seat_obstacles(cb: CellBuild, own, floor_y: float) -> list:
    """Plan rectangles [x0, z0, x1, z1] (world) of the cell's other hero
    pieces standing on the floor at `floor_y`: their points from the floor to
    FLOOR_SEAT_CLEAR over it; pieces wholly under FLOOR_SEAT_FLAT (rugs),
    hung ones (lamps, beams, chandeliers) and ones standing on furniture
    (a candlestick on a table: placed FLOOR_SEAT_ON_TOP or more over the
    floor, nothing below its own foot) are none."""
    out = []
    for u in cb.uses:
        piece = cb.pieces.get(u.id)
        if piece is None or u is own or u.role != "hero":
            continue
        if u.origin[1] - floor_y >= FLOOR_SEAT_ON_TOP and min(p[1] for f in piece.faces for p in f.pts) >= -1e-6:
            continue  # on a table top: the table under it is the obstacle
        pl = geom.Placement(u.origin, u.yaw)
        pts = [q for f in piece.faces for q in (pl.point(p) for p in f.pts) if -0.05 <= q[1] - floor_y < FLOOR_SEAT_CLEAR]
        if not pts or max(q[1] for q in pts) - floor_y < FLOOR_SEAT_FLAT:
            continue
        out.append([min(q[0] for q in pts), min(q[2] for q in pts), max(q[0] for q in pts), max(q[2] for q in pts)])
    return out


def cut_floor_seat(rect: list, obstacles: list) -> list:
    """`rect` [x0, z0, x1, z1] less every obstacle rectangle over it: each
    cut splits a piece into up to four, either the full-depth strips west and
    east of the obstacle and the strips north and south of it between them,
    or the full-width strips north and south and the strips west and east
    between them, whichever keeps more area; pieces with a side under
    FLOOR_SEAT_MIN are dropped. Deterministic (the obstacles in order);
    sorted."""

    def keep(ps):
        return [p for p in ps if p[2] - p[0] >= G.FLOOR_SEAT_MIN - 1e-6 and p[3] - p[1] >= G.FLOOR_SEAT_MIN - 1e-6]

    pieces = [list(rect)]
    for ox0, oz0, ox1, oz1 in obstacles:
        nxt = []
        for x0, z0, x1, z1 in pieces:
            if ox0 >= x1 or ox1 <= x0 or oz0 >= z1 or oz1 <= z0:
                nxt.append([x0, z0, x1, z1])
                continue
            cx0, cx1, cz0, cz1 = max(x0, ox0), min(x1, ox1), max(z0, oz0), min(z1, oz1)
            a = keep([[x0, z0, cx0, z1], [cx1, z0, x1, z1], [cx0, z0, cx1, cz0], [cx0, cz1, cx1, z1]])
            b = keep([[x0, z0, x1, cz0], [x0, cz1, x1, z1], [x0, cz0, cx0, cz1], [cx1, cz0, x1, cz1]])
            area = [sum((p[2] - p[0]) * (p[3] - p[1]) for p in ps) for ps in (a, b)]
            nxt += b if area[1] > area[0] + 1e-9 else a
        pieces = nxt
    return sorted(pieces)


# =====================================================================================
# Castle M5b (Upper rooms): docs/art/castle-kit.md "Pieces".
# =====================================================================================


def dome_sky_m5b(uses, built: dict) -> list:
    """The starry dome's panels (kit_dome_starry: meta "sky", [pts, uv] in
    its frame) as castle_sky faces with their own UVs (mode "sky": u round
    each cell, v 0 at the foot to 1 at the oculus), as the Great Hall's
    sky_m3 panels; face tags carry no such mode, so the piece hands them over
    in its meta."""
    out = []
    for u in uses:
        piece = built.get(u.id)
        sky = piece.meta.get("sky") if piece is not None else None
        if not sky:
            continue
        pl = geom.Placement(u.origin, u.yaw)
        for pts, uv in sky:
            out.append(WFace([pl.point(p) for p in pts], "castle_sky", "sky", None, [tuple(v) for v in uv], u.id))
    return out


def lights_m5b_upper(cb: CellBuild) -> None:
    """The Upper rooms' cells: the lights hero pieces bring (the reading
    lamps, the armillary's orb) are named `<cell>_hero_<i>` by finish_m3, so
    two lamps would share an id and the export would keep one; each takes
    its hero's id instead (`<cell>_<hero id>_<i>`)."""
    if G.m5b_rules(cb.cell) is None:  # integration: every M5b cell (the Dungeons' torches, hearths and lamps too)
        return
    names = []
    for u in cb.uses:
        got = cb.pieces[u.id].meta.get("lights") if u.id in cb.pieces else None
        if u.role == "hero" and isinstance(got, list):
            names += [f"{cb.cell}_{u.id.split(':', 1)[1]}_{i}" for i in range(len(got))]
    k = 0
    for lt in cb.lights:
        if lt["id"].startswith(f"{cb.cell}_hero_") and k < len(names):
            lt["id"] = names[k]
            k += 1


# =====================================================================================
# Castle M5c (Facades): docs/art/castle-kit.md "Exterior". The exterior material clones:
# outside faces on `<material>@ext` (grid.ext_name), the same
# castle_* row and textures, so the client gives them High's sun shadow.
# =====================================================================================


def ext_material_m5c(material: str, materials: dict) -> str:
    """A face tag's material resolved for a cell: the family to its variant
    (`castle_wall` -> `castle_wall_b`), keeping an `@ext` suffix
    (`castle_wall@ext` -> `castle_wall_b@ext`)."""
    base = G.ext_base(material)
    mat = materials.get(base, base)
    return G.ext_name(mat) if material.endswith(G.EXT_SUFFIX) else mat


def _poly_key(pts) -> tuple:
    return tuple((round(p[0], 5), round(p[1], 5), round(p[2], 5)) for p in pts)


def facade_piece(name: str) -> bool:
    """Is `name` one of the facade pieces (grid.FACADE_PIECES, sized or not)?"""
    fam = P.parse(name)[0]
    return name in G.FACADE_PIECES or fam in G.FACADE_PIECES


def ext_split_m5c(builder: "Builder", cp: S.CellPlan, faces: list, uses) -> None:
    """In a cell whose `facade` has `ext` (castle M5c, 30.4), after the faces
    are gathered and before the slicing, the outside faces take their
    exterior clones (`@ext`; grid.EXT_NEVER and grid.OUTDOOR_MATERIALS keep
    their names):

    - an outdoor cell (the courtyard): every castle_* face;
    - a facing cell: its Planner faces `wall_exterior` and `roof`
      (grid.EXT_PLANNER_FACES; `castle_wall|world` here), every face of its
      facade pieces and the facade rule's other pieces (the rule's, the `fac_`
      heroes', the storeys' string courses; in the outside
      stone's ramp), and the faces of other pieces lying on one of its outer
      wall planes facing out (the lancets' outer spandrels).

    Cells without a facade are left exactly as they were."""
    fa = cp.facade
    if fa is None or not fa.ext:
        return
    if R.is_outdoor_cell(builder.layout, cp.cell):
        for f in faces:
            if f.material.startswith("castle_"):
                f.material = G.ext_name(f.material)
        return
    keys = set()
    planes = set()
    for mat in G.EXT_PLANNER_FACES:
        for poly in builder.plan1.polys.get((cp.cell, mat), []):
            keys.add(_poly_key(poly))
            n = geom.unit(geom.newell(poly))
            for k in (0, 2):
                if abs(abs(n[k]) - 1.0) < 1e-6:
                    planes.add((k, round(poly[0][k], 4), 1.0 if n[k] > 0 else -1.0))
    outside = {u.id for u in uses if u.role == "facade" or facade_piece(u.piece)}
    for f in faces:
        if not f.material.startswith("castle_"):
            continue
        if f.src in outside:
            f.material = G.ext_name(f.material)
            if f.material.startswith("castle_wall"):
                f.ramp = "wall_exterior"
        elif f.src == "base" and _poly_key(f.pts) in keys:
            f.material = G.ext_name(f.material)
        elif f.src != "floor" and _on_outer_plane(f, planes):
            f.material = G.ext_name(f.material)


def _on_outer_plane(f, planes: set) -> bool:
    n = f.normal()
    for k in (0, 2):
        if abs(abs(n[k]) - 1.0) > 1e-4:
            continue
        c = f.pts[0][k]
        if all(abs(p[k] - c) < 1e-4 for p in f.pts) and (k, round(c, 4), 1.0 if n[k] > 0 else -1.0) in planes:
            return True
    return False


def facade_lod_m5c(cp: S.CellPlan, lod_meshes: dict) -> None:
    """The LOD1 nodes of a facing cell's outside meshes (its `@ext` clones and
    castle_roof, main meshes only) switch at grid.FACADE_LOD, whatever other
    pieces of the cell ask (the balustrades' 30 m): the facade pieces' LOD1
    (castle M5c, 30.2)."""
    if cp.facade is None:
        return
    for name, (md, base, dist) in list(lod_meshes.items()):
        if base.count(".") == 1 and G.is_outdoor_material(md.material):
            lod_meshes[name] = (md, base, G.FACADE_LOD)


# =====================================================================================
# Castle M5c (Courtyard): docs/art/castle-kit.md "The kitted cells".
# =====================================================================================


def lights_m5c_courtyard(cb: CellBuild) -> None:
    """The courtyard's lamp posts each bring a light, which finish_m3 names
    `<cell>_hero_<i>` (one id for all six, and the export keeps one per id);
    each takes its hero's id instead (`courtyard_lamp_<k>_0`), as
    lights_m5b_upper does for the M5b cells."""
    if cb.cell not in G.M5C_CELLS:
        return
    names = []
    for u in cb.uses:
        got = cb.pieces[u.id].meta.get("lights") if u.id in cb.pieces else None
        if u.role == "hero" and isinstance(got, list):
            names += [f"{cb.cell}_{u.id.split(':', 1)[1]}_{i}" for i in range(len(got))]
    k = 0
    for lt in cb.lights:
        if lt["id"].startswith(f"{cb.cell}_hero_") and k < len(names):
            lt["id"] = names[k]
            k += 1


# =====================================================================================
# Docs/art/castle-kit.md "Exterior". The courtyard's grounds side: the
# faces of its walls outside its outer planes (the outer faces seen from the grounds,
# the gatehouse's overhang and its corbels) in the outside stone,
# castle_wall_c (its `@ext` clone); a face across an outer plane is split on it.
# =====================================================================================


def outer_planes_p3a(builder: "Builder", cell: str) -> list[tuple[int, float, float]]:
    """(axis index, offset, outward sign) of a cell's vertical outer wall
    planes (the Planner's `wall_exterior` faces facing out)."""
    out = set()
    for poly in builder.plan1.polys.get((cell, "wall_exterior"), []):
        n = geom.unit(geom.newell(poly))
        for k in (0, 2):
            if abs(abs(n[k]) - 1.0) < 1e-6:
                out.add((k, round(poly[0][k], 4), 1.0 if n[k] > 0 else -1.0))
    return sorted(out)


def _clip_p3a(pts: list, uv: list, k: int, c: float, sign: float, keep_out: bool):
    """The part of a polygon (and its corner UVs) beyond the plane
    (`keep_out`: (p[k] - c) * sign >= 0) or inside it."""
    def d(p):
        v = (p[k] - c) * sign
        return v if keep_out else -v

    out_p, out_uv = [], []
    for i, p in enumerate(pts):
        q, quv, puv = pts[(i + 1) % len(pts)], uv[(i + 1) % len(pts)], uv[i]
        dp, dq = d(p), d(q)
        if dp >= -1e-6:
            out_p.append(p)
            out_uv.append(puv)
        if (dp > 1e-6 and dq < -1e-6) or (dp < -1e-6 and dq > 1e-6):
            t = dp / (dp - dq)
            out_p.append(tuple(p[j] + (q[j] - p[j]) * t for j in range(3)))
            out_uv.append(tuple(puv[j] + (quv[j] - puv[j]) * t for j in range(2)))
    return out_p, out_uv


def exterior_stone_p3a(builder: "Builder", cp: S.CellPlan, faces: list) -> None:
    """In an outdoor cell whose `facade` has `ext` (the courtyard), every
    world-mapped wall-stone face (`castle_wall*`) lying beyond one of its
    outer planes takes grid.P3A_EXTERIOR_STONE's exterior clone; a face
    crossing a plane is split on it (UVs interpolated: world-mapped, so
    they stay the world's). Every other cell, and every face inside the
    planes, is left exactly as it was."""
    fa = cp.facade
    if fa is None or not fa.ext or not R.is_outdoor_cell(builder.layout, cp.cell):
        return
    from dataclasses import replace

    stone = G.ext_name(G.P3A_EXTERIOR_STONE)
    planes = outer_planes_p3a(builder, cp.cell)
    out = []
    for f in faces:
        if f.mode != "world" or not f.material.startswith("castle_wall") or f.material == stone:
            out.append(f)
            continue
        rest = [(list(f.pts), list(f.uv))]
        beyond = []
        for k, c, sign in planes:
            nxt = []
            for pts, uv in rest:
                ds = [(p[k] - c) * sign for p in pts]
                if min(ds) >= -1e-6:
                    beyond.append((pts, uv))
                elif max(ds) <= 1e-6:
                    nxt.append((pts, uv))
                else:
                    bp, buv = _clip_p3a(pts, uv, k, c, sign, True)
                    ip, iuv = _clip_p3a(pts, uv, k, c, sign, False)
                    if len(bp) >= 3:
                        beyond.append((bp, buv))
                    if len(ip) >= 3:
                        nxt.append((ip, iuv))
            rest = nxt
        if not beyond:
            out.append(f)
            continue
        for pts, uv in rest:
            out.append(replace(f, pts=pts, uv=uv))
        for pts, uv in beyond:
            out.append(replace(f, pts=pts, uv=uv, material=stone))
    faces[:] = out


# =====================================================================================
# Docs/art/castle-kit.md.
# =====================================================================================


def fill_origin(md: MeshData) -> tuple[float, float, float]:
    """A `fill` group's node origin: the middle of its bounding box in x and z
    at its lowest y (the base of the hourglass's sand), so the client's Y
    scale 0..1 fills it from the bottom."""
    lo = [min(v[k] for v in md.verts) for k in range(3)]
    hi = [max(v[k] for v in md.verts) for k in range(3)]
    return (round((lo[0] + hi[0]) / 2.0, 6), round(lo[1], 6), round((lo[2] + hi[2]) / 2.0, 6))


def budget_draws(cb: CellBuild) -> int:
    """The draws a cell's budget counts: every mesh but the cup banner groups
    (hidden unless that house won), plus the most draws of grid.CUP_BANNERS_SHOWN
    houses' cup banners (one winner)."""
    base = 0
    cup: dict[str, int] = {}
    for name in cb.meshes:
        if not cb.meshes[name].faces:
            continue  # An empty marker node (the owls) draws nothing
        grp = name.split(".", 2)[2] if name.count(".") == 2 else None
        if G.is_cup_banner_group(grp):
            cup[grp] = cup.get(grp, 0) + 1
        else:
            base += 1
    return base + sum(sorted(cup.values(), reverse=True)[:G.CUP_BANNERS_SHOWN])


# =====================================================================================
# Docs/art/castle-kit.md.
# =====================================================================================

# A swing node is ONE castle_props mesh (grid.SWING_NODE): the flight's stone, trim and
# marble re-tagged to props-atlas tiles in the Grand Staircase's colours (castle_textures.json
# `_doc_p3c_wings_a`), so each moving flight costs one draw.
SWING_TAGS = {
    "castle_wall": G.palette_tag("stone", "gs_flight_stone"),
    "castle_ceiling": G.palette_tag("stone", "gs_flight_under"),
    "castle_floor": G.palette_tag("metal", "gs_flight_marble"),
    "castle_trim": G.palette_tag("metal", "gs_flight_trim"),
    "nosing": G.palette_tag("metal", "gs_flight_marble"),
}


def is_swing_group(group) -> bool:
    return bool(group) and str(group).startswith("swing_")


def swing_tag(f: WFace) -> str | None:
    """The props-atlas tag a swing node's face takes (None: it keeps its own, castle_props)."""
    if f.material == "castle_props":
        return None
    if f.material == "castle_trim" and f.arg and "nosing" in f.arg:
        return SWING_TAGS["nosing"]
    for key in ("castle_wall", "castle_ceiling", "castle_floor", "castle_trim"):
        if f.material.startswith(key):
            return SWING_TAGS[key]
    return SWING_TAGS["castle_trim"]


def swing_p3c(builder: "Builder", cp: S.CellPlan, faces: list, uses: list) -> None:
    """The cell's moving flights (rules.p3c_wings_a put the flight and its balustrades in
    `swing_<flight>`): each group's faces re-tagged onto the props atlas (SWING_TAGS),
    drawn at every distance (no LOD1), the flight's faces on its two end planes left out
    (the landings' slab edges show there: the Planner's at a railing edge, the gate
    fascia at a walkable join), and the group's spec: kit_anim {kind swing, flight,
    pivot [x, z], angle (radians)}."""
    flights = {R.swing_group(f["id"]): f for f in R.moving_flights(builder.layout, cp.cell)}
    if not flights:
        return
    out = []
    k = 0
    for f in faces:
        if not is_swing_group(f.group):
            out.append(f)
            continue
        if f.lod == "lod1":
            continue
        fl = flights[f.group]
        s = builder.layout.by_id(fl["a"])
        if f.src.startswith("flight:") and _on_end_plane(f, s):
            continue
        tag = swing_tag(f)
        if tag is not None:
            nf = builder.face(f.pts, tag, None, 0.0, f.src, cp.materials, k, f.walkthrough, "both", None)
            k += 1
        else:
            nf = f
            nf.lod = "both"
        nf.group = f.group
        out.append(nf)
    faces[:] = out
    for grp, fl in flights.items():
        anim = {"kind": G.ANIM_SWING, "flight": fl["id"], "pivot": (float(fl["pivot"][0]), float(fl["pivot"][1])),
                "angle": round(math.radians(fl["angle_deg"]), 6)}
        cp.groups[grp] = G.GroupSpec(grp, None, tuple(sorted(anim.items())))


def _on_end_plane(f: WFace, s) -> bool:
    """Is a vertical face of the flight on the plane of its foot or its top edge?"""
    n = f.normal()
    if abs(n[1]) > 0.1:
        return False
    k = 2 if s.rise in ("+z", "-z") else 0
    for c in ((s.z0, s.z1) if k == 2 else (s.x0, s.x1)):
        if all(abs(p[k] - c) < 0.012 for p in f.pts):
            return True
    return False


def swing_origins_p3c(builder: "Builder", cell: str, meshes: dict) -> None:
    """Each swing node's origin: its flight's pivot [x, z] at the flight's lowest y (stairs.json)."""
    for fl in R.moving_flights(builder.layout, cell):
        name = G.node_name(cell, "castle_props", R.swing_group(fl["id"]))
        if name in meshes:
            s = builder.layout.by_id(fl["a"])
            meshes[name].origin = (float(fl["pivot"][0]), float(min(s.y, s.y_top)), float(fl["pivot"][1]))


def is_swing_use(u) -> bool:
    return is_swing_group(getattr(u, "group", None))


# The Clock Tower Courtyard's south wall cut down to a parapet (rules.CLOCK_PARAPET): the
# Planner's faces of the wall between the side walls' corner blocks clipped to the parapet's
# height, the lintel over the bridge gone, the corner blocks' new end faces and the gap's jambs.
CLOCK_SOUTH = (74.0, 75.0)  # the wall's inner and outer faces (z)
CLOCK_INNER_X = (62.0, 108.0)  # the side walls' inner faces
CLOCK_GAP = (101.0, 107.0)  # the bridge's opening


def _rect_face(builder, cp, axis: str, c: float, a0: float, a1: float, y0: float, y1: float, normal, src="base"):
    """A wall rectangle on the plane axis = c (a along the other horizontal axis)."""
    if axis == "z":
        pts = [(a0, y0, c), (a1, y0, c), (a1, y1, c), (a0, y1, c)]
    else:
        pts = [(c, y0, a0), (c, y0, a1), (c, y1, a1), (c, y1, a0)]
    pts, _ = geom.oriented(pts, None, normal)
    return builder.face(pts, "castle_wall|world", None, 0.0, src, cp.materials, 0, ramp="wall_exterior")


def clock_parapet_p3c(builder: "Builder", cp: S.CellPlan, faces: list) -> None:
    if cp.cell != "clock_courtyard":
        return
    zi, zo = CLOCK_SOUTH
    xi0, xi1 = CLOCK_INNER_X
    top = 0.0 + R.CLOCK_PARAPET
    out = []
    for f in faces:
        if f.src != "base" or not all(zi - 0.01 <= p[2] <= zo + 0.01 for p in f.pts):
            out.append(f)
            continue
        xs = [p[0] for p in f.pts]
        ys = [p[1] for p in f.pts]
        n = f.normal()
        if min(xs) >= xi1 - 0.01 or max(xs) <= xi0 + 0.01:
            out.append(f)  # a corner block's own face
            continue
        if abs(n[1]) > 0.5:
            # The wall top: only the corner blocks keep theirs.
            for a0, a1 in ((min(xs), min(max(xs), xi0)), (max(min(xs), xi1), max(xs))):
                if a1 - a0 > 0.01:
                    pts = [(a0, max(ys), zo), (a1, max(ys), zo), (a1, max(ys), zi), (a0, max(ys), zi)]
                    pts, _ = geom.oriented(pts, None, (0.0, 1.0, 0.0))
                    out.append(builder.face(pts, "castle_wall|world", None, 0.0, "base", cp.materials, 0,
                                            ramp="wall_exterior"))
            continue
        if min(ys) >= top - 0.01:
            continue  # the lintel over the bridge
        c = f.pts[0][2]
        # Corner parts at full height, the rest cut to the parapet.
        for a0, a1, full in ((min(xs), min(max(xs), xi0), True), (max(min(xs), xi0), min(max(xs), xi1), False),
                             (max(min(xs), xi1), max(xs), True)):
            if a1 - a0 > 0.01:
                out.append(_rect_face(builder, cp, "z", c, a0, a1, min(ys), max(ys) if full else min(max(ys), top), n))
    # The corner blocks' faces toward the parapet, the bridge gap's jambs.
    for x, nx in ((xi0, 1.0), (xi1, -1.0)):
        out.append(_rect_face(builder, cp, "x", x, zi, zo, top, 16.0, (nx, 0.0, 0.0)))
    for x, nx in ((CLOCK_GAP[0], 1.0), (CLOCK_GAP[1], -1.0)):
        out.append(_rect_face(builder, cp, "x", x, zi, zo, -0.5, top, (nx, 0.0, 0.0)))
    faces[:] = out


def bridge_base_p3c(cp: S.CellPlan, faces: list) -> None:
    """The bridge kind draws none of the Planner's walls, ceiling or roof: the hero
    (kit_bridge_covered) is the bridge; the camera collider keeps the Planner's box."""
    if cp.budget == "bridge":
        faces[:] = [f for f in faces if f.src != "base"]


# =====================================================================================
# Docs/art/castle-kit.md. The Owl Loft (N10): the rules hang a
# railing on every edge of its surfaces that drops to the knoll's ground, but its walls
# (the hero kit_owl_loft_shell over the layout's solids) and its newel stand there: only the
# loft's railings over the stairwell stay; the shell's outside switches to its LOD1 at
# grid.FACADE_LOD; the owls are empty marker nodes (grid.OWL_MARKER, kit_anim "owl") on
# the perches and the loft's open arches' sills. The dungeon cellars (N12): the steps run
# between walls (solid flights with skirts and arches without quoins, as the M5b basement
# steps, rules.py m5b_dungeons) and the cellars keep no outside-corner quoins (the landing's
# and the vault's corners are a wall's width apart: their quoins overlapped).
# =====================================================================================

P3C_OWL_LOFT = "owl_loft"
P3C_CELLARS = "dungeon_cellars"
OWL_LOFT_SHELL = "kit_owl_loft_shell"
OWL_LOFT_PERCH = "kit_owl_perch"
# The loft's open arches whose sills an owl sits on: (side, the arch's t; pieces/wings_b.py OWL_LOFT_ARCHES).
OWL_LOFT_SILL_OWLS = (("n", -3.6), ("n", 0.0), ("n", 3.6), ("w", -3.6), ("w", 3.6), ("e", 3.6))
OWL_LOFT_RAIL_CORNER = 0.26  # a kept loft railing's end at a corner another kept one shares (rules.CORNER_INSET)
OWL_PHASE_STEP = 0.381966  # the owls' phases: k x this (mod 1), so neighbours never bob together


def _owl_loft_frame(cp) -> tuple:
    """(centre x, centre z, inside half width, loft y) of the Owl Loft's shell hero."""
    from castle_kit.pieces import wings_b as WB

    h = next(h for h in cp.hero if h.piece == OWL_LOFT_SHELL)
    return h.pos[0], h.pos[2], WB.OWL_LOFT_HALF, dict(h.args).get("loft", 22.14)


def wings_b_uses_p3c(cp, uses: list) -> list:
    """The Wings B cells' placements on top of the rules' (see the block's head)."""
    from dataclasses import replace

    if cp.cell == P3C_OWL_LOFT:
        cx, cz, half, loft = _owl_loft_frame(cp)
        out = []
        for u in uses:
            if u.role in ("balustrade", "newel"):
                ends = [u.origin]
                if u.role == "balustrade":
                    ends.append(geom.Placement(u.origin, u.yaw).point((u.kwargs()["length"], 0.0, 0.0)))
                on_wall = all(abs(abs(p[0] - cx) - half) < 1e-3 for p in ends) or \
                    all(abs(abs(p[2] - cz) - half) < 1e-3 for p in ends)
                if on_wall or min(p[1] for p in ends) < loft - 0.1:
                    continue  # against the tower's walls or its newel
            if u.piece == OWL_LOFT_SHELL:
                u = replace(u, lod_distance=G.FACADE_LOD)  # its outside's LOD1 (the plain walls and the spire)
            out.append(u)
        return _owl_loft_rail_corners(out)
    if cp.cell == P3C_CELLARS:
        out = []
        for u in uses:
            if u.id.startswith("quoin:"):
                continue
            if u.role == "flight":
                kw = u.kwargs()
                kw["solid"] = True
                kw.pop("strip", None)
                u = replace(u, params=R._p(**kw))
                out.append(R.Use(f"skirt:{u.run}", "skirt", "kit_stair_skirt", u.origin, u.yaw,
                                 R._p(width=kw["width"], length=kw["length"], rise=kw["rise"]), u.run))
            elif u.role == "arch":
                u = replace(u, params=R._p(**dict(u.kwargs(), quoins=False)))
            out.append(u)
        out.sort(key=lambda u: u.id)
        return out
    return uses


def _owl_loft_rail_corners(uses: list) -> list:
    """The loft's kept railings meet at the corner of its west part over the
    stairwell, their rails overlapping under the corner's newel: each stops
    OWL_LOFT_RAIL_CORNER short of a corner another kept railing ends at."""
    from dataclasses import replace

    rails = [u for u in uses if u.role == "balustrade"]

    def ends(u):
        return u.origin, geom.Placement(u.origin, u.yaw).point((u.kwargs()["length"], 0.0, 0.0))

    def corner_of(p):
        return (round(p[0] * 2) / 2, round(p[2] * 2) / 2)  # the corner on the 0.5 m plan grid

    count: dict = {}
    for u in rails:
        for p in ends(u):
            count[corner_of(p)] = count.get(corner_of(p), 0) + 1
    out = []
    for u in uses:
        if u.role == "balustrade":
            kw = u.kwargs()
            a, b = ends(u)
            cut0 = count[corner_of(a)] > 1 and abs(geom.norm(geom.sub(a, (corner_of(a)[0], a[1], corner_of(a)[1])))) < 0.3
            cut1 = count[corner_of(b)] > 1 and abs(geom.norm(geom.sub(b, (corner_of(b)[0], b[1], corner_of(b)[1])))) < 0.3
            d = OWL_LOFT_RAIL_CORNER - R.NEWEL_HALF
            length = kw["length"] - d * (cut0 + cut1)
            origin = geom.Placement(u.origin, u.yaw).point((d, 0.0, 0.0)) if cut0 else u.origin
            if cut0 or cut1:
                u = replace(u, origin=R._o(origin), params=R._p(**dict(kw, length=R._r(length))))
        out.append(u)
    return out


def owl_loft_ext_p3c(cp, faces: list) -> None:
    """The Owl Loft's shell draws its outside (faces, reveals, bands) in the
    outside stone (grid.P3A_EXTERIOR_STONE); in the cell it takes that
    stone's `@ext` clone (High's sun shadows, as the facades' outsides)."""
    if cp.cell != P3C_OWL_LOFT:
        return
    for f in faces:
        if f.src.startswith("hero:") and f.material == G.P3A_EXTERIOR_STONE:
            f.material = G.ext_name(f.material)
            f.ramp = "wall_exterior"


def owl_markers_p3c(cb: CellBuild) -> list[tuple[str, tuple, float, float]]:
    """The Owl Loft's owls [(node name, position, yaw, phase)]: one on every
    perch's places (facing out of its wall) and one on each sill of
    OWL_LOFT_SILL_OWLS (in the wall's middle, facing into the tower)."""
    import math

    from castle_kit.pieces import wings_b as WB

    cx, cz, half, loft = _owl_loft_frame(cb.plan)
    spots = []
    for u in sorted((u for u in cb.uses if u.piece == OWL_LOFT_PERCH), key=lambda u: u.id):
        pl = geom.Placement(u.origin, u.yaw)
        for p in cb.pieces[u.id].meta["owls"]:
            spots.append((pl.point(p), u.yaw))
    for side, k in OWL_LOFT_SILL_OWLS:
        s = WB._Side(side)
        tc, _, sill, _ = next(h for h in WB.owl_loft_holes(side, 0.0, loft) if h[2] > loft and abs(h[0] - k) < 1e-6)
        x, y, z = s.pt(tc, sill, half + WB.OWL_LOFT_WALL / 2.0)
        n = s.normal(False)
        spots.append(((cx + x, y, cz + z), round(math.atan2(n[0], n[2]), 4)))
    out = []
    for k, (pos, yaw) in enumerate(spots):
        out.append((G.OWL_MARKER.format(cell=cb.cell, k=k), tuple(round(v, 4) for v in pos), float(yaw),
                    round((k * OWL_PHASE_STEP) % 1.0, 4)))
    return out


def wings_b_finish_p3c(cb: CellBuild) -> None:
    """The Owl Loft after its bake: its LOD1 nodes all at grid.FACADE_LOD like a
    facade's (the shell's LOD1 closes its openings, so the inside's LOD1 may
    go with it), and its owl markers as empty nodes (no faces, no draw;
    greybox.py exports them as plain glTF nodes with their extras)."""
    if cb.cell != P3C_OWL_LOFT:
        return
    for name, (md, base, _dist) in list(cb.lod_meshes.items()):
        cb.lod_meshes[name] = (md, base, G.FACADE_LOD)  # one distance: the inside goes when the walls close
    for name, pos, yaw, phase in owl_markers_p3c(cb):
        md = MeshData(name, "castle_props", [], [], [], [])
        md.origin = pos
        cb.meshes[name] = md
        cb.extras[name] = {G.EXTRAS_ANIM: {"kind": G_OWL_ANIM, "phase": phase, "yaw": yaw}}


G_OWL_ANIM = "owl"  # grid.py OWL_*: kit_anim {"kind": "owl", "phase"} (+ "yaw": the owl's facing, radians)


# =====================================================================================
# Docs/art/castle-kit.md. Each passage entrance (rules.p3d_secrets:
# the hero `entrance_<door>` in its own group) is one castle_props node,
# grid.ENTRANCE_NODE, with extras.kit_anim {"kind": "entrance", "door": <door surface id>,
# "style": tapestry | panel | painting | statue | wall, "yaw": the way it faces into its room
# (radians, 0 = +z)} and its origin at the entrance's bottom centre on the wall face (the
# middle of the door's opening on the room's wall plane); the client sways the cloth or
# slides the panel about that origin while a character stands on the door surface. The
# Wishing Room's door (hero `wishing_room_door` in l7_corridor_east) is the node
# grid.WISHING_ROOM_DOOR_NODE, kit_anim {"kind": "wishing_room_door", "yaw"}, its origin at the
# door's bottom centre on the blank wall; the client keeps it hidden until the server opens it.
# =====================================================================================


def _p3d_style(piece: str) -> str | None:
    from castle_kit.pieces import secrets as SP

    if piece in G.HOUSE_ENTRANCE_STYLES:  # The common rooms' entrances
        return G.HOUSE_ENTRANCE_STYLES[piece]
    return next((st for st, name in SP.ENTRANCE_PIECES.items() if name == piece), None)


def secrets_p3d(cp: S.CellPlan, uses: list) -> None:
    """The cell's secrets groups' specs (their kit_anim), before the merge."""
    for u in uses:
        grp = R.p3d_group(u)
        if grp is None:
            continue
        yaw = round(math.atan2(math.sin(u.yaw), math.cos(u.yaw)), 4) + 0.0
        if grp == R.P3D_DOOR_HERO:
            anim = {"kind": G.ANIM_WISHING_ROOM_DOOR, "yaw": yaw}
        else:
            style = _p3d_style(u.piece)
            if style is None:
                raise ValueError(f"{cp.cell}: {u.id}: {u.piece} is not an entrance piece")
            anim = {"kind": G.ANIM_ENTRANCE, "door": grp[len(R.P3D_ENTRANCE_PREFIX):], "style": style, "yaw": yaw}
        cp.groups[grp] = G.GroupSpec(grp, None, tuple(sorted(anim.items())))
        if anim.get("style") == "double":  # Knocker door opens as a pair: a node per leaf
            for leaf in G.ENTRANCE_LEAVES:
                lg = G.leaf_group(grp, leaf)
                cp.groups[lg] = G.GroupSpec(lg, None, tuple(sorted(dict(anim, leaf=leaf).items())))


def secrets_origins_p3d(cell: str, uses: list, meshes: dict, built: dict | None = None) -> None:
    """Each secrets node's origin: its hero's position (the bottom centre on the wall face);
    a double entrance's leaf node's: its hinge (the piece's meta "hinges", placed)."""
    for u in uses:
        grp = R.p3d_group(u)
        if grp is None:
            continue
        # (every material's node of the group; the common rooms' entrances span several.)
        for name, md in meshes.items():
            if name == G.node_name(cell, "castle_props", grp) or name.endswith("." + grp):
                md.origin = tuple(float(v) for v in u.origin)
        piece = (built or {}).get(u.id)
        for leaf, hinge in sorted((piece.meta.get("hinges") or {}).items() if piece is not None else ()):
            at = geom.Placement(tuple(u.origin), u.yaw).point(hinge)
            for name, md in meshes.items():
                if name.endswith("." + G.leaf_group(grp, leaf)):
                    md.origin = tuple(round(float(v), 6) + 0.0 for v in at)


# The swing's corners (docs/architecture.md "Moving staircases"): mid-swing a moving flight's corners passed through a pier slab's gallery
# edge, a gallery corner or a gate's hinge newel (each stands at a corner of the flight's rest
# place). Every swing node is trimmed at the four plan corners of its flight's A surface by a
# vertical chamfer SWING_CHAMFER m along each side (its stone, treads and balustrade ends cut
# away there), the cuts capped where the cut solid closes, so no corner sweeps through them.
SWING_CHAMFER = 0.75


def _clip_keep(pts, n, d):
    """The part of convex `pts` with n . p <= d (n horizontal), and its cut segment (or None)."""
    out, cut = [], []
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        sp = n[0] * p[0] + n[2] * p[2] - d
        sq = n[0] * q[0] + n[2] * q[2] - d
        if sp <= 1e-9:
            out.append(p)
        if (sp < -1e-9 and sq > 1e-9) or (sp > 1e-9 and sq < -1e-9):
            t = sp / (sp - sq)
            x = tuple(p[k] + (q[k] - p[k]) * t for k in range(3))
            out.append(x)
            cut.append(x)
        elif abs(sp) <= 1e-9 and sq > 1e-9 or abs(sq) <= 1e-9 and sp > 1e-9:
            cut.append(p if abs(sp) <= 1e-9 else q)
    seg = tuple(cut[:2]) if len(cut) >= 2 and geom.norm(geom.sub(cut[0], cut[1])) > 1e-6 else None
    return out, seg


def _loops(segs) -> list:
    """Closed loops chained from cut segments (endpoints matched to 0.1 mm); open chains dropped."""
    key = lambda p: (round(p[0], 4), round(p[1], 4), round(p[2], 4))  # noqa: E731
    adj: dict = {}
    for a, b in segs:
        ka, kb = key(a), key(b)
        if ka == kb:
            continue
        adj.setdefault(ka, []).append(kb)
        adj.setdefault(kb, []).append(ka)
    loops, seen = [], set()
    for start in sorted(adj):
        if start in seen or len(adj[start]) != 2:
            continue
        loop, prev, cur = [start], None, start
        while True:
            seen.add(cur)
            nxt = [k for k in adj[cur] if k != prev]
            if not nxt or len(adj[cur]) != 2:
                loop = None
                break
            prev, cur = cur, nxt[0]
            if cur == start:
                break
            if cur in seen:
                loop = None
                break
            loop.append(cur)
        if loop and len(loop) >= 3:
            loops.append(loop)
    return loops


def swing_trim_p3d(builder: "Builder", cp: S.CellPlan, faces: list) -> None:
    """The swing nodes' corners chamfered (see SWING_CHAMFER)."""
    flights = {R.swing_group(f["id"]): f for f in R.moving_flights(builder.layout, cp.cell)}
    if not flights:
        return
    out = [f for f in faces if f.group not in flights]
    for grp, fl in sorted(flights.items()):
        s = builder.layout.by_id(fl["a"])
        cx, cz = (s.x0 + s.x1) / 2.0, (s.z0 + s.z1) / 2.0
        mine = [f for f in faces if f.group == grp]
        c = SWING_CHAMFER
        for kx, kz in ((s.x0, s.z0), (s.x1, s.z0), (s.x1, s.z1), (s.x0, s.z1)):
            ux, uz = (1.0 if cx > kx else -1.0), (1.0 if cz > kz else -1.0)
            # The cut through (kx + c ux, kz) and (kx, kz + c uz); keep the side of the centre.
            n = (-ux, 0.0, -uz)  # points to the corner (the side cut away)
            d = n[0] * (kx + c * ux) + n[2] * kz
            kept, segs = [], []
            for f in mine:
                pts, seg = _clip_keep(f.pts, n, d)
                if seg is not None:
                    segs.append(seg)
                if len(pts) >= 3 and geom.norm(geom.newell(pts)) > 1e-9:
                    nf = builder.face(pts, f"{f.material}|{f.mode}:{f.arg}" if f.arg else f"{f.material}|{f.mode}",
                                      None, 0.0, f.src, cp.materials, 0, f.walkthrough, "both", f.ramp)
                    nf.group = grp
                    kept.append(nf)
            tag = SWING_TAGS["castle_wall"]
            for loop in _loops(segs):
                cap: list = []
                geom.add_face(cap, [tuple(p) for p in loop], tag, None, n)
                for kf in cap:
                    nf = builder.face(kf.pts, tag, None, 0.0, f"trim:{grp}", cp.materials, 0, False, "both", None)
                    nf.group = grp
                    kept.append(nf)
            mine = kept
        out.extend(mine)
    faces[:] = out
