"""The castle kit's pieces (M1: every shared piece
but the portraits; M2: the plain shaft, open leaves by the open-leaf rule,
the traceried window, the Entrance Hall's vaults, armour and Owl Exchange
court in `vaults.py` and `hall.py`). Pure Python: each builder returns a `geom.Piece` in its own
frame (geom.py), which `assemble.py` places, UV-maps and merges.

    from castle_kit import pieces
    p = pieces.build("kit_stone_arch_5x6", reveal=0.5, depth=2.0)

`build(name, **params)` reads sizes from the piece name (grid.piece_name:
`kit_stone_arch_5x6`, `kit_stone_shaft_8.4`, `kit_window_lancet_1.6x5`,
`kit_threshold_8`, `kit_door_oak_5x6_open`) and the rest from `params`.
`CATALOGUE` lists every piece family with a default instance (the piece
gallery renders them all).
"""

from __future__ import annotations

import re

from castle_kit import grid as G
from castle_kit.pieces import fixtures, hall, mouldings, openings, stairs, vaults
from castle_kit.pieces import hall_furniture, hall_m3, hall_roof  # castle kit M3 (the Great Hall)
from castle_kit.pieces import frames, staircase  # Castle kit M4 (the Grand Staircase)
from castle_kit.pieces import corridors  # Castle M5a (Corridors)
from castle_kit.pieces import tower  # Castle M5a (Tower)
from castle_kit.pieces import room_furniture  # Castle M5b (Furniture)
from castle_kit.pieces import dungeon  # Castle M5b (Dungeons)
from castle_kit.pieces import rooms_upper  # Castle M5b (Upper rooms)
from castle_kit.pieces import facades  # Castle M5c (Facades)
from castle_kit.pieces import courtyard, courtyard_hall  # Castle M5c (Courtyard)
from castle_kit.pieces import school  # classroom pieces
from castle_kit.pieces import school_2c  # more classroom pieces
from castle_kit.pieces.geom import KFace, Piece, Placement  # noqa: F401

_SIZE = re.compile(r"^(kit_[a-z][a-z0-9_]*?)_(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?(?:_([a-z][a-z0-9]*))?$")


def parse(name: str) -> tuple[str, float | None, float | None, str | None]:
    """(family, w, h, variant) of a piece name: `kit_stone_arch_5x6` ->
    (`kit_stone_arch`, 5, 6, None); names without sizes return (name, None,
    None, None)."""
    if not G.PIECE_RE.match(name):
        raise ValueError(f"not a valid piece name: {name!r}")
    m = _SIZE.match(name)
    if not m:
        return name, None, None, None
    fam, w, h, var = m.groups()
    return fam, float(w), (float(h) if h is not None else None), var


def build(name: str, **params) -> Piece:
    fam, w, h, var = parse(name)
    if fam in BUILDERS_SIZED:
        p = BUILDERS_SIZED[fam](w, h, var, **params)
    elif name in BUILDERS:
        p = BUILDERS[name](**params)
    else:
        raise KeyError(f"unknown piece {name!r}")
    p.name = name
    return p


BUILDERS = {
    "kit_stone_plinth": mouldings.plinth,
    "kit_stone_string": mouldings.string_course,
    "kit_stone_cornice": mouldings.cornice,
    "kit_stone_rib": mouldings.rib,
    "kit_stone_quoin": mouldings.quoin,
    "kit_stone_boss": mouldings.boss,
    "kit_floor_border": mouldings.floor_border,
    "kit_balustrade_gothic": stairs.balustrade,
    "kit_stair_flight": stairs.flight,
    "kit_newel_ball": lambda **kw: fixtures.newel("ball", **kw),
    "kit_newel_lamp": lambda **kw: fixtures.newel("lamp", **kw),
    "kit_lantern_wall": fixtures.lantern_wall,
    "kit_lantern_hanging": fixtures.lantern_hanging,
    "kit_banner_a": lambda **kw: fixtures.banner("banner_house_a", **kw),
    "kit_banner_b": lambda **kw: fixtures.banner("banner_house_b", **kw),
    "kit_banner_c": lambda **kw: fixtures.banner("banner_house_c", **kw),
    "kit_banner_d": lambda **kw: fixtures.banner("banner_house_d", **kw),
    "kit_banner_school": lambda **kw: fixtures.banner("banner_school", **kw),
    # Castle kit M2: the Entrance Hall's heroes.
    "kit_armour_suit": hall.armour_suit,
    "kit_owl_counter": hall.owl_counter,
    "kit_owl_perch": hall.owl_perch,
    # Castle kit M3 (the Great Hall)
    "kit_beam_ridge": hall_roof.beam_ridge,
    "kit_wainscot": hall_roof.wainscot,
    "kit_wainscot_crest": hall_roof.wainscot_crest,
    "kit_beast_brazier_a": lambda **kw: hall_m3.beast_brazier("a", **kw),
    "kit_beast_brazier_b": lambda **kw: hall_m3.beast_brazier("b", **kw),
    "kit_beast_brazier_c": lambda **kw: hall_m3.beast_brazier("c", **kw),
    "kit_beast_brazier_d": lambda **kw: hall_m3.beast_brazier("d", **kw),
    "kit_chair_row_staff": hall_furniture.chair_row_staff,
    "kit_chair_high": hall_furniture.chair_high,
    "kit_owl_lectern": hall_furniture.owl_lectern,
    "kit_hourglass_set": hall_furniture.hourglass_set,
    "kit_candles_floating": hall_furniture.candles_floating,
    # Castle kit M4 (the Grand Staircase): docs/art/castle-kit.md "Portraits and tapestries".
    "kit_gallery_edge": staircase.gallery_edge,
    "kit_stair_flight_stepped": staircase.flight_stepped,
    "kit_skylight_lantern": staircase.skylight_lantern,
    "kit_portrait_cluster": frames.cluster,
    "kit_portrait_guardian_a": frames.guardian,
    # Castle M5a (Corridors): docs/art/castle-kit.md "Pieces".
    "kit_statue_robed_a": lambda **kw: corridors.statue_robed("a", **kw),
    "kit_statue_robed_b": lambda **kw: corridors.statue_robed("b", **kw),
    "kit_well_dry": corridors.well_dry,
    # Castle M5a (Tower): docs/art/castle-kit.md "Pieces".
    "kit_door_knocker_a": tower.knocker_door,
    # Castle M5b (Furniture): docs/art/castle-kit.md "Pieces".
    "kit_armchair": room_furniture.armchair,
    "kit_table_round": room_furniture.table_round,
    "kit_table_study": room_furniture.table_study,
    "kit_chair_study": room_furniture.chair_study,
    "kit_noticeboard": room_furniture.noticeboard,
    "kit_door_dorm": room_furniture.door_dorm,
    "kit_chandelier_ring": room_furniture.chandelier_ring,
    # Castle M5b (Dungeons): docs/art/castle-kit.md "Pieces".
    "kit_torch_wall": dungeon.torch_wall,
    "kit_door_stone_hidden": dungeon.door_stone_hidden,
    "kit_barrel_stack": dungeon.barrel_stack,
    "kit_hearth_kitchen": dungeon.hearth_kitchen,
    "kit_pans_hanging": dungeon.pans_hanging,
    "kit_pots_set": dungeon.pots_set,
    "kit_lamp_hanging": dungeon.lamp_hanging,
    "kit_window_glow": dungeon.window_glow,
    "kit_plant_pot": dungeon.plant_pot,
    "kit_shelf_wall": dungeon.shelf_wall,
    "kit_door_painting": dungeon.door_painting,
    "kit_stair_skirt": dungeon.stair_skirt,
    # Castle M5b (Upper rooms): docs/art/castle-kit.md "Pieces".
    "kit_trophy_case": rooms_upper.trophy_case,
    "kit_armillary": rooms_upper.armillary,
    "kit_statue_scholar": rooms_upper.statue_scholar,
    "kit_lamp_reading": rooms_upper.lamp_reading,
    # Castle M5c (Facades): docs/art/castle-kit.md "Exterior".
    "kit_parapet_crenel": facades.parapet,
    "kit_pinnacle": facades.pinnacle,
    # Castle M5c (Courtyard): docs/art/castle-kit.md "The kitted cells".
    "kit_arcade_bay_6": courtyard.arcade_bay,
    "kit_fountain_tiered": courtyard.fountain_tiered,
    "kit_gatehouse_8x10": courtyard.gatehouse,
    "kit_lamp_post": courtyard.lamp_post,
    "kit_market_stall": courtyard.market_stall,
    "kit_planter_tree": courtyard.planter_tree,
    "kit_notice_wall": courtyard_hall.notice_wall,
    "kit_crate_stack": courtyard_hall.crate_stack,
    "kit_perch_tree": courtyard_hall.perch_tree,
    # The class door's plaque alone (docs/architecture.md).
    "kit_class_plaque": corridors.class_plaque,
    "kit_desk_objects": school.desk_objects,
    # One house's House Cup banners (docs/art/castle-kit.md).
    "kit_banner_cup_a": lambda **kw: fixtures.cup_banners("a", **kw),
    "kit_banner_cup_b": lambda **kw: fixtures.cup_banners("b", **kw),
    "kit_banner_cup_c": lambda **kw: fixtures.cup_banners("c", **kw),
    "kit_banner_cup_d": lambda **kw: fixtures.cup_banners("d", **kw),
    # The Defence drill floor and the Divination tables (docs/architecture.md).
    "kit_practice_target": school_2c.practice_target,
    "kit_drill_ring": school_2c.drill_ring,
    "kit_divination_set": school_2c.divination_set,
}

# Families whose name carries a size: builder(w, h, variant, **params).
BUILDERS_SIZED = {
    "kit_stone_shaft": lambda w, h, v, **kw: mouldings.shaft_plain(w, **kw) if v == "plain" else mouldings.shaft(w, **kw),
    "kit_stone_corbel": lambda w, h, v, **kw: mouldings.corbel(w, **kw),
    "kit_stone_arch": lambda w, h, v, **kw: openings.arch(w, h, **kw),
    "kit_door_oak": lambda w, h, v, **kw: openings.door_oak(w, h, open=(v == "open"), **kw),
    "kit_threshold": lambda w, h, v, **kw: openings.threshold(w, **kw),
    "kit_window_lancet": lambda w, h, v, **kw: openings.window(w, h, **kw),
    "kit_niche": lambda w, h, v, **kw: openings.niche(w, h, **kw),
    "kit_door_blind": lambda w, h, v, **kw: openings.blind_door(w, h, **kw),
    "kit_window_tracery": lambda w, h, v, **kw: openings.tracery_window(w, h, **kw),
    "kit_vault_lierne_bay": lambda w, h, v, **kw: vaults.lierne_bay(w, h, **kw),
    "kit_vault_fan": lambda w, h, v, **kw: vaults.fan_vault(w, h, **kw),
    # Castle kit M3 (the Great Hall)
    "kit_truss_archbraced": lambda w, h, v, **kw: hall_roof.truss_archbraced(w, **kw),
    "kit_window_end": lambda w, h, v, **kw: hall_m3.end_window(w, h, **kw),
    "kit_fireplace_hooded": lambda w, h, v, **kw: hall_m3.fireplace_hooded(w, h, **kw),
    "kit_table_house": lambda w, h, v, **kw: hall_furniture.table_house(w, **kw),
    "kit_table_staff": lambda w, h, v, **kw: hall_furniture.table_staff(w, **kw),
    "kit_bench": lambda w, h, v, **kw: hall_furniture.bench(w, **kw),
    "kit_table_setting": lambda w, h, v, **kw: hall_furniture.table_setting(w, **kw),
    # Castle kit M4 (the Grand Staircase).
    "kit_landing_corbel": lambda w, h, v, **kw: staircase.landing_corbel(w, h, **kw),
    "kit_pier_stack": lambda w, h, v, **kw: staircase.pier_stack(w, **kw),
    "kit_floor_compass": lambda w, h, v, **kw: staircase.floor_compass(w, **kw),
    "kit_portrait": lambda w, h, v, **kw: frames.portrait(w, h, v, **kw),
    # Castle M5a (Corridors): docs/art/castle-kit.md "Pieces".
    "kit_door_class": lambda w, h, v, **kw: corridors.class_door(w, h, **kw),
    "kit_tapestry": lambda w, h, v, **kw: corridors.tapestry(w, h, **kw),
    "kit_window_rose": lambda w, h, v, **kw: corridors.rose_window(w, h, **kw),
    # Castle M5a (Tower)
    "kit_spiral_core": lambda w, h, v, **kw: tower.spiral_core(w, **kw),
    "kit_window_slit": lambda w, h, v, **kw: tower.slit_window(w, h, **kw),
    # Castle M5b (Furniture)
    "kit_sofa": lambda w, h, v, **kw: room_furniture.sofa(w, **kw),
    "kit_rug": lambda w, h, v, **kw: room_furniture.rug(w, h, **kw),
    # Castle M5b (Dungeons)
    "kit_vault_barrel": lambda w, h, v, **kw: dungeon.vault_barrel(w, h, **kw),
    # Castle M5b (Upper rooms)
    "kit_bookcase": lambda w, h, v, **kw: rooms_upper.bookcase(w, h, **kw),
    "kit_floor_inlay_map": lambda w, h, v, **kw: rooms_upper.floor_map(w, h, **kw),
    "kit_dome_starry": lambda w, h, v, **kw: rooms_upper.dome_starry(w, **kw),
    "kit_drapes": lambda w, h, v, **kw: rooms_upper.drapes(w, h, **kw),
    # Castle M5c (Facades)
    "kit_buttress": lambda w, h, v, **kw: facades.buttress(w, **kw),
    "kit_roof_slate": lambda w, h, v, **kw: facades.roof_slate(w, h, **kw),
    "kit_window_tracery_outer": lambda w, h, v, **kw: facades.tracery_outer(w, h, **kw),
    "kit_window_blind": lambda w, h, v, **kw: facades.blind_window(w, h, **kw),  # castle M5c review
}

# One instance of every piece for the gallery and the per-piece tests:
# name -> params.
CATALOGUE = {
    "kit_stone_plinth": {"length": 3.0},
    "kit_stone_string": {"length": 3.0},
    "kit_stone_cornice": {"length": 3.0},
    "kit_stone_rib": {"length": 3.0},
    "kit_stone_quoin": {"y0": 0.0, "y1": 3.0, "corner": True},
    "kit_stone_boss": {},
    "kit_stone_shaft_8.4": {},
    "kit_stone_corbel_0.6": {},
    "kit_stone_corbel_1": {},
    "kit_stone_corbel_1.4": {},
    "kit_stone_arch_3x4.5": {"reveal": 0.5, "depth": 2.0},
    "kit_stone_arch_4x5": {"reveal": 0.5, "depth": 2.0},
    "kit_stone_arch_5x6": {"reveal": 0.5, "depth": 2.0},
    "kit_stone_arch_6x9": {"reveal": 0.5, "depth": 2.0},
    "kit_stone_arch_8x7": {"reveal": 2.0, "depth": 2.0},
    "kit_stone_arch_8x10": {"reveal": 0.5, "depth": 2.0},
    "kit_door_oak_5x6": {"depth": 2.0},
    "kit_door_oak_5x6_open": {"depth": 2.0},
    "kit_threshold_5": {"depth": 2.0},
    "kit_window_lancet_1.6x5": {"depth": 0.8},
    "kit_window_lancet_2.6x7": {"depth": 0.8},
    "kit_window_lancet_3.2x10": {"depth": 0.8},
    "kit_niche_2x3.5": {},
    "kit_door_blind_3x4.5": {},
    "kit_floor_border": {"rects": [[0.0, 0.0, 3.0, 0.6]]},
    "kit_stair_flight": {"width": 3.0, "length": 3.0, "rise": 1.7, "solid": False},
    "kit_balustrade_gothic": {"length": 3.0},
    "kit_newel_ball": {},
    "kit_newel_lamp": {},
    "kit_lantern_wall": {},
    "kit_lantern_hanging": {"drop": 1.5},
    "kit_banner_a": {},
    "kit_banner_b": {},
    "kit_banner_c": {},
    "kit_banner_d": {},
    "kit_banner_school": {},
    # Castle kit M2 (the Entrance Hall).
    "kit_stone_shaft_8.4_plain": {},
    "kit_stone_shaft_14_plain": {},
    "kit_door_oak_8x10_open": {"depth": 2.0, "mode": "face", "reveal": 2.0},
    "kit_door_oak_6x9_open": {"depth": 2.0, "mode": "face", "reveal": 0.5},
    "kit_door_oak_3x4.5_open": {"depth": 2.0, "mode": "reveal", "reveal": 2.0},
    "kit_window_tracery_8x8": {"depth": 0.8},
    "kit_vault_lierne_bay_32x10": {"rise": 6.0},
    "kit_vault_fan_12x10": {"rise": 6.0},
    "kit_armour_suit": {},
    "kit_owl_counter": {"width": 12.0},
    "kit_owl_perch": {},
    # Castle kit M3 (the Great Hall)
    "kit_truss_archbraced_22": {"plate": 5.5, "ridge": 16.5},
    "kit_beam_ridge": {"length": 7.5},
    "kit_wainscot": {"length": 3.0},
    "kit_wainscot_crest": {},
    "kit_stone_shaft_8.8_plain": {"top_clear": 1.26},
    "kit_beast_brazier_a": {"drop": 2.5},
    "kit_beast_brazier_b": {"drop": 2.5},
    "kit_beast_brazier_c": {"drop": 2.5},
    "kit_beast_brazier_d": {"drop": 2.5},
    "kit_fireplace_hooded_4.4x3.6": {},
    "kit_window_end_9x12": {"depth": 0.8},
    "kit_table_house_13": {},
    "kit_table_house_43": {},
    "kit_bench_13": {},
    "kit_bench_43": {},
    "kit_table_staff_16": {},
    "kit_chair_row_staff": {"count": 18, "pitch": 0.8, "gap": 1.4},
    "kit_chair_high": {},
    "kit_owl_lectern": {},
    "kit_hourglass_set": {"count": 4, "pitch": 2.5},
    "kit_table_setting_13": {},
    "kit_table_setting_16": {},
    "kit_table_setting_43": {},
    "kit_candles_floating": {"rect": [0.0, 0.0, 3.0, 3.0], "y0": 5.0, "y1": 6.0, "count": 12, "seed": 1},
    # Castle kit M4 (the Grand Staircase).
    "kit_gallery_edge": {"length": 4.0, "drops": [[0.0, 2.0, "full"], [2.0, 4.0, "band"]]},
    "kit_stair_flight_stepped": {"width": 4.0, "length": 3.0, "rise": 1.7},
    "kit_landing_corbel_4.5x4": {},
    "kit_landing_corbel_3.5x4": {"drop": 0.8},
    "kit_pier_stack_4.5": {"top": 49.5},
    "kit_skylight_lantern": {"w": 12.0, "d": 16.0},
    "kit_floor_compass_12": {"inner": 3.4},
    "kit_portrait_1.2x1.6_rect": {"canvas": 3},
    "kit_portrait_1.2x1.6_arch": {"canvas": 12},
    "kit_portrait_1x1.2_oval": {"canvas": 20},
    "kit_portrait_2x1.4_rect": {"canvas": 36},
    "kit_portrait_cluster": {"frames": [[0.0, 1.2, 1.2, 1.6, 0, 3, 0.07, 0.07], [1.35, 1.2, 1.6, 1.2, 0, 34, 0.07, 0.07],
                                        [1.35, 2.55, 1.0, 1.2, 2, 50, 0.06, 0.06]]},
    "kit_portrait_guardian_a": {},
    # Castle M5a (Corridors).
    "kit_door_class_3x4.5": {},
    "kit_statue_robed_a": {},
    "kit_statue_robed_b": {},
    "kit_tapestry_2.5x4": {"tile": 0},
    "kit_tapestry_6x3": {"tile": 3},
    "kit_window_rose_6x6": {"depth": 0.8},
    "kit_well_dry": {},
    # Castle M5a (Tower)
    "kit_spiral_core_4": {"top": 60.75},
    "kit_window_slit_0.6x2.4": {"depth": 0.8},
    "kit_door_knocker_a": {},
    # Castle M5b (Furniture)
    "kit_sofa_2.4": {"cover": 0},
    "kit_sofa_3.2": {"cover": 4},
    "kit_armchair": {"cover": 2},
    "kit_rug_4x3": {"field": 0},
    "kit_rug_1.5x1.5": {"field": 4},
    "kit_table_round": {},
    "kit_table_study": {"length": 2.4},
    "kit_chair_study": {},
    "kit_noticeboard": {},
    "kit_door_dorm": {"house": 3},
    "kit_chandelier_ring": {"radius": 1.0, "count": 12, "chain": 3.0, "group": 0},
    # Castle M5b (Dungeons)
    "kit_vault_barrel_9x28": {"rise": 3.0, "rib0": -8.5, "pitch": 8.5, "ribs": 3},
    "kit_torch_wall": {"height": 2.8},
    "kit_door_stone_hidden": {},
    "kit_barrel_stack": {},
    "kit_hearth_kitchen": {"width": 4.0, "top": 6.0},
    "kit_pans_hanging": {"length": 2.4, "drop": 1.0, "count": 8},
    "kit_pots_set": {"count": 5},
    "kit_lamp_hanging": {"drop": 1.0, "glow": 1},
    "kit_window_glow": {"w": 1.4, "h": 3.6},
    "kit_plant_pot": {},
    "kit_shelf_wall": {"width": 2.4, "height": 2.4},
    "kit_door_painting": {"canvas": 55},
    "kit_stair_skirt": {"width": 4.0, "length": 18.0, "rise": 9.0},
    # Castle M5b (Upper rooms)
    "kit_bookcase_5.2x4.2": {"seed": 1},
    "kit_bookcase_2x3": {"seed": 2, "cupboard": 0},
    "kit_trophy_case": {"width": 2.4, "seed": 1, "house": 2},
    "kit_armillary": {"centre": 5.2, "top": 8.4},
    "kit_floor_inlay_map_8x6": {},
    "kit_dome_starry_24": {"rise": 4.9},
    "kit_statue_scholar": {},
    "kit_drapes_2.6x9": {"house": 2},
    "kit_lamp_reading": {},
    # Castle M5c (Facades)
    "kit_buttress_12": {"proud": 1.2},
    "kit_buttress_24": {"proud": 1.4, "top_proud": 0.9},
    "kit_parapet_crenel": {"length": 8.0},
    "kit_pinnacle": {"height": 4.5},
    "kit_roof_slate_12x16": {"pitch": 50.0},
    "kit_window_tracery_outer_8x8": {"depth": 1.2, "top": 5.2},
    "kit_window_blind_1.6x5": {},  # castle M5c review
    # Castle M5c (Courtyard)
    "kit_arcade_bay_6": {"pier1": 1, "end0": 1, "end1": 1},
    "kit_fountain_tiered": {},
    "kit_gatehouse_8x10": {},
    "kit_lamp_post": {},
    "kit_market_stall": {"house": 2, "goods": 1},
    "kit_planter_tree": {"seed": 1},
    "kit_notice_wall": {},
    "kit_crate_stack": {},
    "kit_perch_tree": {},
    "kit_class_plaque": {"y": 2.0},
    "kit_desk_objects": {"seed": 1},
    "kit_banner_cup_a": {"count": 2, "pitch": 2.4, "rows": 1.2},
    "kit_banner_cup_b": {"count": 2, "pitch": 2.4, "rows": 1.2},
    "kit_banner_cup_c": {"count": 2, "pitch": 2.4, "rows": 1.2},
    "kit_banner_cup_d": {"count": 2, "pitch": 2.4, "rows": 1.2},
    "kit_practice_target": {},
    "kit_drill_ring": {"radius": 1.6},
    "kit_divination_set": {"kind": 0, "places": 3, "centre": 1},
}

# The Astronomy tower top (class_astronomy).
from castle_kit.pieces import astronomy  # noqa: E402

BUILDERS.update({"kit_stair_head": astronomy.stair_head, "kit_telescope": astronomy.telescope})
CATALOGUE.update({"kit_stair_head": {}, "kit_telescope": {"seed": 1}})

# The Herbology glasshouse (class_greenhouse).
from castle_kit.pieces import greenhouse  # noqa: E402

BUILDERS.update({"kit_glasshouse": greenhouse.glasshouse, "kit_glasshouse_porch": greenhouse.glasshouse_porch,
                 "kit_potting_bench": greenhouse.potting_bench, "kit_plant_staging": greenhouse.plant_staging})
CATALOGUE.update({"kit_glasshouse": {}, "kit_glasshouse_porch": {}, "kit_potting_bench": {"seed": 1},
                  "kit_plant_staging": {}})

# The Creature Keeping paddock (class_paddock).
from castle_kit.pieces import paddock  # noqa: E402

BUILDERS.update(paddock.BUILDERS)
CATALOGUE.update(paddock.CATALOGUE)

# The grounds (docs/art/castle-kit.md "Exterior"; world/grounds_export.py places them).
from castle_kit.pieces import grounds  # noqa: E402

BUILDERS.update(grounds.BUILDERS)
CATALOGUE.update(grounds.CATALOGUE)

# The courtyard's grounds side (docs/art/castle-kit.md "Exterior").
from castle_kit.pieces import exterior  # noqa: E402

BUILDERS_SIZED.update({"kit_corbel_table": exterior.corbel_table})
CATALOGUE.update({"kit_corbel_table_18": {}})

# The Flying Pitch and the Flying class field (docs/architecture.md;
# world/pitch_build.py and world/school_rooms_pitch.py place them).
from castle_kit.pieces import pitch  # noqa: E402

BUILDERS.update(pitch.BUILDERS)
CATALOGUE.update(pitch.CATALOGUE)

# The Clock Tower Courtyard, the covered bridge, the east wing, the Hospital
# Wing and the Grand Staircase's moving flights (docs/art/castle-kit.md). The pier
# stack's builder is replaced: the corner colonnettes the moving half-flights sweep past are left out.
from castle_kit.pieces import wings_a  # noqa: E402

BUILDERS.update(wings_a.BUILDERS)
BUILDERS_SIZED.update(wings_a.BUILDERS_SIZED)
CATALOGUE.update(wings_a.CATALOGUE)
G.OPENING_PIECES["kit_door_locked_3x4.5"] = G.OpeningSpec("kit_door_locked_3x4.5", "blind_door", 3.0, 4.5, 0.3)
# (end Wings A)

# The Owl Loft, the seventh-floor east corridor's tapestry, the cellars'
# barrels and wine racks (docs/art/castle-kit.md).
from castle_kit.pieces import wings_b  # noqa: E402

BUILDERS.update(wings_b.BUILDERS)
CATALOGUE.update(wings_b.CATALOGUE)

# The hidden passages' entrances and their own pieces, the
# Wishing Room's door, the Lost Property Vault's unclaimed oddments and the Gathering Room's floor
# cushions (docs/art/castle-kit.md).
from castle_kit.pieces import secrets  # noqa: E402

BUILDERS.update(secrets.BUILDERS)
CATALOGUE.update(secrets.CATALOGUE)
# (end Secrets)

# The Owl Order Catalogue in the common rooms: the catalogue's stand, one per house common room
# (docs/architecture.md "Economy and crafting"; docs/art/castle-kit.md).
from castle_kit.pieces import catalogue  # noqa: E402

BUILDERS.update(catalogue.BUILDERS)
CATALOGUE.update(catalogue.CATALOGUE)
