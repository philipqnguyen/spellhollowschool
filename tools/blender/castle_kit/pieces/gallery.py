"""The piece gallery: every catalogue piece on its own stretch of wall or
floor, built through the same placement and UV path as the kitted cells
(assemble.Builder.piece_faces), for review stills. Blender only; rendered
by `world/render_toon.py --views gallery_mouldings,gallery_arches,
gallery_openings,gallery_fixtures,gallery_stairs,gallery_hall,gallery_vaults
[--kit-preview]` (`hall` and `vaults`: castle kit M2; `staircase`: M4).

Gallery coordinates: pieces stand along x on the floor y = 0, their walls'
faces on z = 0 facing +z, the camera in front (+z)."""

from __future__ import annotations

import math

import bpy

import layout_lib as L
from castle_kit import assemble as A
from castle_kit import grid as G
from castle_kit import pieces as P
from castle_kit.pieces import geom

MATS = {"castle_wall": "castle_wall_a", "castle_floor": "castle_floor_square",
        "castle_portraits": "castle_portraits_b"}  # castle kit M4: the sheet with the guardian's canvas
WALL = "castle_wall|world"


def _to_blender(p):
    return (p[0], -p[2], p[1])


def _wall_block(faces, x0, x1, height, depth, holes=(), jambs=()):
    """A wall slab x0..x1, 0..height, from z = 0 back to z = -depth with
    rectangular holes (t0, y0, t1, y1); `jambs` (t0, t1, y1) line door holes
    through the slab up to y1."""
    rect_holes = [(a, b, c, d) for a, b, c, d in holes]
    for z, facing in ((0.0, 1.0), (-depth, -1.0)):
        for q in L.holed_quads("z", z, x0, x1, 0.0, height, facing, rect_holes):
            geom.add_face(faces, q, WALL, None, None)
    geom.add_face(faces, [(x0, height, 0.0), (x1, height, 0.0), (x1, height, -depth), (x0, height, -depth)], WALL,
                  None, (0, 1, 0))
    for x, sx in ((x0, -1.0), (x1, 1.0)):
        geom.add_face(faces, [(x, 0.0, 0.0), (x, 0.0, -depth), (x, height, -depth), (x, height, 0.0)], WALL, None, (sx, 0, 0))
    for t0, t1, y1 in jambs:
        for x, sx in ((t0, 1.0), (t1, -1.0)):
            geom.add_face(faces, [(x, 0.0, 0.0), (x, 0.0, -depth), (x, y1, -depth), (x, y1, 0.0)], WALL, None, (sx, 0, 0))
        geom.add_face(faces, [(t0, y1, 0.0), (t1, y1, 0.0), (t1, y1, -depth), (t0, y1, -depth)], WALL, None, (0, -1, 0))


def _page(name):
    """(entries, walls, camera): entries are (piece, params, origin, yaw);
    walls are geom faces in gallery coordinates."""
    entries, walls = [], []
    if name == "mouldings":
        _wall_block(walls, -1.0, 13.0, 8.4, 0.8)
        entries += [
            ("kit_stone_plinth", {"length": 3.0}, (-0.5, 0.0, 0.0), 0.0),
            ("kit_stone_string", {"length": 3.0}, (-0.5, 2.1, 0.0), 0.0),
            ("kit_stone_cornice", {"length": 3.0, "ends": ["cap", "cap"]}, (-0.5, 8.4, 0.0), 0.0),
            ("kit_stone_shaft_8.4", {}, (3.5, 0.0, 0.0), 0.0),
            ("kit_stone_corbel_0.6", {}, (5.5, 6.0, 0.0), 0.0),
            ("kit_stone_corbel_1", {}, (7.0, 6.0, 0.0), 0.0),
            ("kit_stone_corbel_1.4", {}, (8.9, 6.0, 0.0), 0.0),
            ("kit_stone_quoin", {"y0": 0.0, "y1": 4.8, "corner": True, "hand": -1}, (13.0, 0.0, 0.0), 0.0),
            ("kit_stone_rib", {"length": 3.0, "ends": ["cap", "cap"]}, (10.0, 8.4, 0.0), -math.pi / 2),
            ("kit_stone_boss", {"size": 0.9}, (10.0, 8.4, 1.5), 0.0),
        ]
        camera = ((12.5, 3.6, 10.0), (5.0, 4.0, 0.0), 20)
    elif name == "arches":
        x = 0.0
        holes, jambs = [], []
        for w, h in G.ARCH_SIZES:
            reveal = 2.0 if (w, h) == (8.0, 7.0) else 0.5
            entries.append((G.arch_piece(w, h), {"reveal": reveal, "depth": 2.0}, (x + w / 2, 0.0, 0.0), 0.0))
            holes.append((x, 0.0, x + w, h))
            jambs.append((x, x + w, h))
            x += w + 2.0
        _wall_block(walls, -1.5, x - 0.5, 11.0, 2.0, holes, jambs)
        camera = ((x / 2 - 1.0, 5.0, 36.0), (x / 2 - 1.0, 5.0, 0.0), 24)
    elif name == "openings":
        holes, jambs = [], []
        x = 0.0
        for piece, w, h, sill, extra in (("kit_window_lancet_1.6x5", 1.6, 5.0, 1.2, {"depth": 0.8}),
                                         ("kit_window_lancet_2.6x7", 2.6, 7.0, 1.2, {"depth": 0.8}),
                                         ("kit_window_lancet_3.2x10", 3.2, 10.0, 0.6, {"depth": 0.8}),
                                         ("kit_niche_2x3.5", 2.0, 3.5, 0.6, {}),
                                         ("kit_door_blind_3x4.5", 3.0, 4.5, 0.0, {})):
            entries.append((piece, extra, (x + w / 2, sill, 0.0), 0.0))
            holes.append((x, sill, x + w, sill + h))
            x += w + 2.2
        # Oak doors (closed, open) in 5 x 6 arches, a threshold under each.
        for variant in ("", "_open"):
            w, h = 5.0, 6.0
            entries.append((G.arch_piece(w, h), {"reveal": 0.8, "depth": 0.8}, (x + w / 2, 0.0, 0.0), 0.0))
            entries.append((f"kit_door_oak_5x6{variant}", {"depth": 0.8, "inset": 0.4}, (x + w / 2, 0.0, 0.0), 0.0))
            entries.append(("kit_threshold_5", {"depth": 0.8}, (x + w / 2, 0.0, 0.0), 0.0))
            holes.append((x, 0.0, x + w, h))
            jambs.append((x, x + w, h))
            x += w + 2.2
        _wall_block(walls, -1.5, x - 0.7, 11.0, 0.8, holes, jambs)
        camera = ((x / 2 - 1.0, 5.0, 30.0), (x / 2 - 1.0, 5.0, 0.0), 24)
    elif name == "fixtures":
        _wall_block(walls, -1.0, 14.5, 7.0, 0.8)
        entries += [
            ("kit_lantern_wall", {}, (0.3, 3.6, 0.0), 0.0),
            ("kit_lantern_hanging", {"drop": 1.5}, (1.8, 5.2, 1.0), 0.0),
            ("kit_banner_a", {}, (3.8, 6.8, 0.0), 0.0),
            ("kit_banner_b", {}, (6.0, 6.8, 0.0), 0.0),
            ("kit_banner_c", {}, (8.2, 6.8, 0.0), 0.0),
            ("kit_banner_d", {}, (10.4, 6.8, 0.0), 0.0),
            ("kit_banner_school", {}, (12.6, 6.8, 0.0), 0.0),
            ("kit_floor_border", {"rects": [[-1.0, 0.0, 14.5, 0.6]], "y": 0.0}, (0.0, 0.0, 0.0), 0.0),
        ]
        camera = ((6.75, 3.3, 12.5), (6.75, 3.3, 0.0), 20)
    elif name == "stairs":
        # A free-standing flight rising toward -z, its open sides with a
        # sloping balustrade and newels, and a level balustrade on a landing edge.
        entries += [
            ("kit_stair_flight", {"width": 3.0, "length": 3.4, "rise": 1.7, "solid": False}, (3.0, 0.0, 6.0), math.pi),
            ("kit_balustrade_gothic", {"length": 2.95, "rise": 2.95 * 0.5}, (3.0, 0.085 + 0.1125, 5.775), math.pi / 2),
            ("kit_newel_ball", {}, (3.0, 0.085, 6.0), 0.0),
            ("kit_newel_lamp", {}, (3.0, 1.785, 2.6), 0.0),
            ("kit_balustrade_gothic", {"length": 3.0}, (5.225, 0.0, 6.0), 0.0),
            ("kit_newel_ball", {}, (8.45, 0.0, 6.0), 0.0),
        ]
        camera = ((10.5, 2.6, 11.5), (4.5, 1.0, 4.0), 22)
    elif name == "hall":
        # Castle kit M2: an open door pair on the wall face beside an 8 x 10
        # arch, the traceried window over it, an armour suit in its niche,
        # the plain shaft, the Owl Exchange counter (8 m here) and a perch.
        holes, jambs = [(0.0, 0.0, 8.0, 10.0), (0.0, 10.8, 8.0, 18.8), (14.0, 0.6, 16.0, 4.1)], [(0.0, 8.0, 10.0)]
        entries += [
            ("kit_stone_arch_8x10", {"reveal": 2.0, "depth": 2.0}, (4.0, 0.0, 0.0), 0.0),
            ("kit_door_oak_8x10_open", {"depth": 2.0, "mode": "face", "reveal": 2.0}, (4.0, 0.0, 0.0), 0.0),
            ("kit_threshold_8", {"depth": 2.0}, (4.0, 0.0, 0.0), 0.0),
            ("kit_window_tracery_8x8", {"depth": 2.0}, (4.0, 10.8, 0.0), 0.0),
            ("kit_niche_2x3.5", {}, (15.0, 0.6, 0.0), 0.0),
            ("kit_armour_suit", {}, (15.0, 0.6, -0.35), 0.0),
            ("kit_stone_shaft_14_plain", {}, (17.5, 0.0, 0.0), 0.0),
            ("kit_stone_shaft_14", {}, (-6.5, 0.0, 0.0), 0.0),
            ("kit_owl_counter", {"width": 8.0}, (23.0, 0.0, 0.0), 0.0),
            ("kit_owl_perch", {}, (28.0, 0.0, 1.0), 0.0),
            ("kit_lantern_hanging", {"drop": 4.0}, (19.0, 8.0, 3.0), 0.0),
            ("kit_newel_lamp", {"height": 1.6}, (17.5, 0.0, 4.0), 0.0),
        ]
        _wall_block(walls, -8.0, 28.5, 20.0, 2.0, holes, jambs)
        camera = ((10.0, 6.0, 31.0), (10.0, 7.0, 0.0), 22)
    elif name == "vaults":
        # One lierne bay with its gap and the fan vault in it, seen from
        # below, and a full bay beside them (their frames at y = 6).
        entries += [
            ("kit_vault_lierne_bay_32x10", {"rise": 6.0, "gap": 12.0}, (16.0, 6.0, 5.0), 0.0),
            ("kit_vault_fan_12x10", {"rise": 6.0}, (16.0, 6.0, 5.0), 0.0),
            ("kit_vault_lierne_bay_32x10", {"rise": 6.0}, (16.0, 6.0, 15.0), 0.0),
        ]
        camera = ((16.0, 1.0, 24.0), (16.0, 9.0, 5.0), 16)
    elif name == "gh_roof":
        # Castle kit M3 (the Great Hall): a bay of the hall's wall and roof at
        # full size: wainscot with a crest, a line shaft on it with its corbel
        # and a beast brazier, a truss on the corbel (seen end-on across the
        # bay: its other half stands against the far gable), the ridge beam.
        _wall_block(walls, -1.0, 23.0, 18.0, 1.0)
        entries += [
            ("kit_wainscot", {"length": 24.0, "height": 3.2, "ends": ["cap", "cap"]}, (-1.0, 0.0, 0.0), 0.0),
            ("kit_wainscot_crest", {}, (3.75, 1.75, 0.0), 0.0),
            ("kit_stone_shaft_8.8_plain", {"top_clear": 1.26}, (7.5, 3.2, 0.0), 0.0),
            ("kit_stone_corbel_1.4", {}, (7.5, 12.0, 0.0), 0.0),
            ("kit_beast_brazier_a", {"drop": 2.5}, (7.5, 8.0, 0.25), 0.0),
            ("kit_truss_archbraced_22", {"plate": 5.5, "ridge": 16.5}, (7.5, 12.0, 11.0), math.pi / 2),
            ("kit_stone_shaft_8.8_plain", {"top_clear": 1.26}, (15.0, 3.2, 0.0), 0.0),
            ("kit_beast_brazier_b", {"drop": 2.5}, (15.0, 8.0, 0.25), 0.0),
            ("kit_stone_shaft_8.8_plain", {"top_clear": 1.26}, (0.0, 3.2, 0.0), 0.0),
            ("kit_beast_brazier_c", {"drop": 2.5}, (0.0, 8.0, 0.25), 0.0),
            ("kit_stone_shaft_8.8_plain", {"top_clear": 1.26}, (22.0, 3.2, 0.0), 0.0),
            ("kit_beast_brazier_d", {"drop": 2.5}, (22.0, 8.0, 0.25), 0.0),
            ("kit_stone_cornice", {"length": 24.0, "ends": ["cap", "cap"]}, (-1.0, 17.5, 0.0), 0.0),
        ]
        camera = ((21.0, 4.0, 24.0), (8.0, 11.0, 5.0), 16)
    elif name == "gh_beasts":
        # The four beast braziers close up, on shaft stubs.
        _wall_block(walls, -2.0, 12.0, 11.0, 1.0)
        for i, kind in enumerate("abcd"):
            x = i * 3.4
            entries.append(("kit_stone_shaft_8.8_plain", {"top_clear": 1.26}, (x, 2.2, 0.0), 0.0))
            entries.append((f"kit_beast_brazier_{kind}", {"drop": 2.5}, (x, 8.0, 0.25), 0.0))
        camera = ((12.5, 6.2, 6.5), (4.6, 8.0, 0.4), 24)
    elif name == "gh_openings":
        # The hooded fireplace and the end window (9 x 12, sill 2).
        holes = [(0.0, 0.0, 4.4, 3.6), (8.0, 2.0, 17.0, 14.0)]
        entries += [
            ("kit_fireplace_hooded_4.4x3.6", {}, (2.2, 0.0, 0.0), 0.0),
            ("kit_window_end_9x12", {"depth": 0.8}, (12.5, 2.0, 0.0), 0.0),
            ("kit_wainscot", {"length": 2.0, "height": 3.2, "ends": ["cap", "cap"]}, (5.0, 0.0, 0.0), 0.0),
        ]
        _wall_block(walls, -3.0, 19.0, 16.0, 0.8, holes)
        camera = ((8.0, 6.0, 26.0), (8.0, 6.5, 0.0), 22)
    elif name == "gh_furniture":
        # Tables, benches and settings (13 m), the staff table with its
        # chairs, high chair and setting, the owl lectern, the hourglasses
        # against a wall, a few floating candles.
        _wall_block(walls, -8.0, 24.0, 7.0, 0.6)
        entries += [
            ("kit_table_house_13", {}, (0.0, 0.0, 9.0), 0.0),
            ("kit_bench_13", {}, (0.0, 0.0, 7.85), 0.0),
            ("kit_bench_13", {}, (0.0, 0.0, 10.15), math.pi),
            ("kit_table_setting_13", {}, (0.0, 0.76, 9.0), 0.0),
            ("kit_table_staff_16", {}, (14.0, 0.0, 4.0), 0.0),
            ("kit_chair_row_staff", {"count": 18, "pitch": 0.8, "gap": 1.4}, (14.0, 0.0, 2.85), 0.0),
            ("kit_chair_high", {}, (14.0, 0.0, 2.75), 0.0),
            ("kit_table_setting_16", {}, (14.0, 0.76, 4.0), 0.0),
            ("kit_owl_lectern", {}, (14.0, 0.0, 6.8), 0.0),
            ("kit_hourglass_set", {"count": 4, "pitch": 2.5}, (-3.0, 0.0, 0.0), 0.0),
            ("kit_candles_floating", {"rect": [-4.0, 5.0, 4.0, 12.0], "y0": 3.0, "y1": 4.5, "count": 40, "seed": 7}, (0.0, 0.0, 0.0), 0.0),
        ]
        camera = ((6.0, 4.2, 21.0), (7.0, 1.0, 4.0), 22)
    elif name == "staircase":
        # Castle kit M4 (the Grand Staircase): a gallery slab with its edge
        # (full, plain and band dressings), a hanging flight with its stepped
        # underside, both corbelled landings, portraits of each shape and a
        # small cluster, the guardian in its 3 x 4 arch, the floor compass
        # and the skylight lantern hanging from a ceiling plane.
        _wall_block(walls, -2.0, 30.0, 12.0, 2.0, [(22.0, 0.0, 25.0, 4.0)], [(22.0, 25.0, 4.0)])
        slab = [(-2.0, 4.6, 0.0), (8.0, 4.6, 0.0), (8.0, 4.6, 4.0), (-2.0, 4.6, 4.0)]
        walls += [geom.KFace(geom.oriented(slab, None, (0, -1, 0))[0], "castle_ceiling|world")]
        walls += [geom.KFace(geom.oriented([(-2.0, 5.0, 0.0), (8.0, 5.0, 0.0), (8.0, 5.0, 4.0), (-2.0, 5.0, 4.0)], None,
                                           (0, 1, 0))[0], "castle_floor|world")]
        walls += [geom.KFace(geom.oriented([(-2.0, 4.6, 4.0), (8.0, 4.6, 4.0), (8.0, 5.0, 4.0), (-2.0, 5.0, 4.0)], None,
                                           (0, 0, 1))[0], "castle_trim|trim:rib", [(0, 0), (10, 0), (10, 0.3), (0, 0.3)])]
        entries += [
            ("kit_gallery_edge", {"length": 10.0, "drops": [[0.0, 4.0, "full"], [4.0, 7.0, "plain"], [7.0, 10.0, "band"]]},
             (8.0, 5.0, 4.0), math.pi),
            ("kit_landing_corbel_4.5x4", {}, (10.5, 5.0, 0.0), 0.0),
            ("kit_landing_corbel_3.5x4", {"drop": 0.9}, (15.5, 5.0, 0.0), 0.0),
            ("kit_stair_flight_stepped", {"width": 3.0, "length": 5.1, "rise": 3.0}, (-1.0, 0.0, 9.0), math.pi),
            ("kit_portrait_1.2x1.6_rect", {"canvas": 3}, (0.5, 6.4, 0.0), 0.0),
            ("kit_portrait_1.2x1.6_arch", {"canvas": 12}, (2.2, 6.4, 0.0), 0.0),
            ("kit_portrait_1x1.2_oval", {"canvas": 20}, (3.8, 6.4, 0.0), 0.0),
            ("kit_portrait_2x1.4_rect", {"canvas": 36}, (5.8, 6.4, 0.0), 0.0),
            ("kit_portrait_cluster", P.CATALOGUE["kit_portrait_cluster"], (17.0, 0.0, 0.0), 0.0),
            ("kit_stone_arch_3x4", {"reveal": 0.5, "depth": 2.0}, (23.5, 0.0, 0.0), 0.0),
            ("kit_portrait_guardian_a", {}, (23.5, 0.0, 0.0), 0.0),
            ("kit_floor_compass_12", {"inner": 3.4, "pier": 4.5}, (12.0, 0.0, 14.0), 0.0),
            ("kit_skylight_lantern", {"w": 6.0, "d": 8.0}, (26.0, 12.0, 9.0), 0.0),
        ]
        camera = ((14.0, 7.5, 34.0), (14.0, 4.5, 2.0), 20)
    else:
        raise KeyError(name)
    return entries, walls, camera


def build_page(name: str):
    """Builds a gallery page into the current scene: (mesh objects, point
    lights, (eye, target, lens))."""
    builder = A.Builder.from_files()
    entries, walls, camera = _page(name)
    faces: list[A.WFace] = []
    for f in walls:
        faces.append(builder.face(f.pts, f.tag, f.uv, 0.0, "wall", MATS, 0))
    floor = [(-4.0, 0.0, -3.0), (60.0, 0.0, -3.0), (60.0, 0.0, 30.0), (-4.0, 0.0, 30.0)]
    faces.append(builder.face(geom.oriented(floor, None, (0, 1, 0))[0], "castle_floor|world", None, 0.0, "floor", MATS, 0))
    k = 0
    for piece_name, params, origin, yaw in entries:
        piece = P.build(piece_name, **params)
        placed = builder.piece_faces(piece, geom.Placement(origin, yaw), MATS, piece_name, k0=k)
        k += len(placed)
        faces.extend(placed)
    groups: dict[str, list] = {}
    for f in faces:
        groups.setdefault(f.material, []).append(f)
    objs = []
    lo_hi = builder._ramp_range(faces)
    for mat_name, fs in sorted(groups.items()):
        md = builder._mesh(f"gallery_{name}.{mat_name}", mat_name, fs, lo_hi)
        objs.append(_object(md, _material(mat_name, builder.textures)))
    eye, target, lens = camera
    lights = [{"pos": [eye[0], 6.0, 6.0], "color": "#FFE0B0", "intensity": 6.0, "range": 30.0}]
    return objs, lights, (eye, target, lens)


def _material(name: str, textures: dict):
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    col = A.placeholder_colour(name)
    bsdf.inputs["Base Color"].default_value = (*col, 1.0)
    spec = textures["materials"].get(name, {})
    if spec.get("emissive") == "self":
        bsdf.inputs["Emission Color"].default_value = (*col, 1.0)
        bsdf.inputs["Emission Strength"].default_value = spec.get("emissive_strength", 1.0)
    mat.use_backface_culling = not spec.get("double_sided", False)
    return mat


def _object(md: A.MeshData, mat):
    me = bpy.data.meshes.new(md.name)
    me.from_pydata([_to_blender(v) for v in md.verts], [], [list(f) for f in md.faces])
    me.materials.append(mat)
    uv = me.uv_layers.new(name="UVMap")
    uv.data.foreach_set("uv", [c for face in md.uvs for uv_ in face for c in uv_])
    if md.colors is not None:
        attr = me.color_attributes.new("COLOR_0", "FLOAT_COLOR", "CORNER")
        attr.data.foreach_set("color", [c for face in md.colors for rgb in face for c in (*rgb, 1.0)])
        me.color_attributes.active_color = attr
    me.validate(clean_customdata=False)
    me.update()
    obj = bpy.data.objects.new(md.name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj
