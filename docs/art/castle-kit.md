# Castle kit

The castle kit turns the greybox castle into finished architecture. A cell
listed in `data/world/castle_kit.json` is **kitted**: when `greybox.py`
exports it, the flat greybox render polygons are replaced with tiling
stone, mouldings, arches, windows, vaults, furniture and props built from
a shared set of pieces. Every other cell exports as greybox.

> Code comments cite this document's headings by name. When you rename a
> heading, update the citations too (`git grep` the old heading).

The look is Gothic collegiate: ashlar walls in warm sandstone, pointed
two-centred arches, clustered and plain wall shafts, lierne and fan vaults,
arch-braced timber roofs, cloister-like arcades round open courts, oak
wainscots and long tables in Oxford-style halls, and slate roofs with
buttresses, parapets and pinnacles outside. Everything is our own invented
design: portraits, tapestries, statues, crests and props are painted or
modelled by the scripts here and copy no real artwork, prop or person.

Pieces, rules, textures and checks are pure Python (no `bpy`) except the
final export and the review renders, so most of the kit can be tested
without Blender. See also:

- [`tools/blender/README.md`](../../tools/blender/README.md): the asset
  pipeline, Blender setup, how `client/scripts/prepare.mjs` runs the steps.
- [`docs/art/style-guide.md`](style-guide.md): the toon look, ramps and
  palette rules the castle shares with characters and rooms.
- [`docs/architecture.md`](../architecture.md): coordinates, the layout and
  how the client streams cells.

## Sources of truth

Numbers live in code and data; this page explains them and points at them.

| File | What |
| --- | --- |
| `tools/blender/castle_kit/grid.py` | Grid constants, mouldings, openings, clearances, the bay solver, wall runs, piece names, face tags, UV formulas, budgets, the AO bake and slicing numbers |
| `tools/blender/castle_kit/schema.py` | Validates `castle_kit.json` against the layout and resolves it into world-space placements (`KitPlan`) |
| `tools/blender/castle_kit/rules.py` | Derived placements (sweeps, shafts, arches, thresholds, flights, facades ...) |
| `tools/blender/castle_kit/pieces/` | One builder per piece family; `pieces/__init__.py` registers them (`BUILDERS`, `BUILDERS_SIZED`, `CATALOGUE`) |
| `tools/blender/castle_kit/assemble.py` | Places pieces, computes UVs, slices and bakes, merges one mesh per material (and group) per cell |
| `tools/blender/castle_kit/textures.py`, `trim.py`, `portraits.py`, `tapestries.py`, `props_atlas.py` | The texture library and its painters |
| `tools/blender/castle_kit/shade.py`, `slice.py` | The vertex-AO bake and the edge loops it needs |
| `tools/blender/castle_kit/visibility.py` | Cell visibility (`sees`) and `COLOR_0` packing |
| `tools/blender/castle_kit/lint.py` | Checks of built cells against the hard rules |
| `data/world/castle_kit.json` | Which cells are kitted and what stands in them |
| `data/art/castle_textures.json` | Every `castle_*` material, its texture and flags; sheet layouts; the named `palette`; the props atlas's extra `props_slots` |
| `data/art/toon_palette.json` | The toon rows the client shades each material with |

Print the resolved placements of every kitted cell:

```sh
python3 -I tools/blender/castle_kit/schema.py
```

(Plain `python3 -I` works, as does Blender's bundled Python.)

## How a cell is built

1. `greybox.py` loads the layout (`data/world/layout.json`), the props
   (`props.json`) and the kit (`castle_kit.json`), and plans every cell's
   walls, floors, ceilings, stairs and door holes (`layout_lib.Planner`).
2. `schema.resolve()` turns each kitted cell's entry into a `CellPlan`:
   wall runs, bays, openings, shafts, lanterns, hero placements, groups.
3. `rules.placements()` derives the repeated dressing from the plan and
   the Planner's faces, then adds the cell's hero placements.
4. Each placement's piece is built in its own frame (`pieces.build(name,
   **params)`), placed by `assemble.py`, given UVs from its face tags,
   sliced where the occlusion changes and baked (vertex AO into
   `COLOR_0`).
5. Faces are merged into **one mesh per material per cell** (plus a few
   named groups that move or switch LOD), a camera collider is added, and
   the cell is written as `client/public/assets/world/<cell>.glb` with its
   row in `manifest.json`.

`castle_kit/textures.py` builds the shared texture library separately
(`client/public/assets/world/textures/`); cell files reference materials
by name and never embed images.

## Coordinates, grid and scale

Coordinates are the layout's: metres, Y up, x east, z south.

| Quantity | Value | Name in `grid.py` |
| --- | --- | --- |
| Plan grid | 0.5 m: every authored plan coordinate | `PLAN_GRID` |
| Storey | 9 m floor to floor (L-2 at -18 ... L7 at 63); corridor ceilings 8.4 m under a 0.6 m slab | `STOREY`, `CORRIDOR_CEILING`, `SLAB`, `level_y()` |
| Walls | 2 m between rooms (min 1.5), exterior shell 0.8 m; rooms within 3.2 m share one wall mass | `WALL_THICKNESS`, `EXTERIOR_SHELL`, `WALL_MASS_MAX` |
| Recess depth | at most the wall mass minus 0.1 m | `REVEAL_MARGIN` |
| Stone courses | 0.6 m (0.5-0.7), blocks 0.9-1.8 m, 15 per storey; v = 0 is a course line | `COURSE`, `BLOCK_LENGTH` |
| Flagstones | courses 0.8 / 1.2 m, slabs 1.0-2.0 m; or 0.8 m squares | `FLAG_COURSES`, `FLAG_SLAB` |
| Bays | equal bays per wall run, nearest the target (ties: fewer), within +-15 % | `BAY_TARGETS`, `solve_bays()` |
| Stairs | risers 0.17 m, treads 0.30 m (most flights) or 0.34 m (the Marble Staircase); the walkable ramp within 2 cm of every tread's middle | `RISER`, `TREAD_*` |
| Plinth | 0.6 high, 0.12 proud, on the floor | `PLINTH` |
| String course | 0.3 high, 0.15 proud, at window sills | `STRING` |
| Cornice | 0.7 high, 0.35 proud, its top at the ceiling (or the springing) | `CORNICE` |
| Shaft | 0.7 wide, 0.25 proud, floor to ceiling | `SHAFT` |
| Relief | at least 0.1 m so the eye reads it | `MIN_RELIEF` |
| Arches | two-centred, apex 0.55 x span above the springing; the catalogue 3 x 4.5, 4 x 5, 5 x 6, 6 x 9, 8 x 7, 8 x 10 (the piece is parametric: other door sizes work) | `ARCH_RISE`, `ARCH_SIZES`, `arch_height()` |
| Opening margin | 0.6 m clear of each bay end | `OPENING_MARGIN` |
| Intrusion | below 2.4 m above a walkable surface, kit geometry enters it by at most 0.25 m (the 0.3 m character radius minus 5 cm); walk-through pieces (banners, candles, furniture) are exempt | `intrusion_ok()` |
| Overhead | at least 2.4 m under lanterns, banners, braziers, candles | `OVERHEAD_CLEARANCE` |
| Doors | at least 1 m wide and 2.4 m high clear through their whole depth | `DOOR_CLEAR_*` |
| Coverage | every Planner wall face of a kitted cell covered within 0.05 m | `COVERAGE_TOLERANCE` |

Two scales coexist: the architecture is generous (bays, openings,
mouldings), while human-scale parts keep real sizes (risers, rails 1.0 m,
newels 1.3 m, benches, tables, handles, lanterns at 3-4 m, wainscot up to
4 m).

## Piece names

`kit_<family>[_<piece>][_<w>x<h> | _<size>][_<variant>]`: lowercase,
sizes in metres without trailing zeros, e.g. `kit_stone_arch_5x6`,
`kit_stone_arch_3x4.5`, `kit_stone_shaft_8.4_plain`, `kit_niche_2x3.5`,
`kit_threshold_5`, `kit_door_oak_8x10_open`. `grid.piece_name()` builds
them and `grid.PIECE_RE` checks them. Names are neutral: the four houses
are the slots `a`-`d` (`house_a` ... `house_d`), never house or character
names.

`pieces.build(name, **params)` reads sizes from the name and everything
else from `params`. Sized families are in `BUILDERS_SIZED`, fixed pieces
in `BUILDERS`; `CATALOGUE` lists every family with a default instance for
the piece gallery.

## `castle_kit.json`

```
{
  "version": 1,
  "camera_max": { "default": 16, "low_cap": 18, "cells": { "<cell>": <metres> } },
  "cells": { "<cell id>": { ...fields below... } }
}
```

Unknown fields are errors, so typos fail loudly. `camera_max` gives the
follow camera's furthest zoom per cell (4-40 m; the halls, the Grand
Staircase and the courtyard 24 m, the common rooms and a few big rooms
20 m, everything else 16 m); Low quality caps it at `low_cap`.

### Cell fields

| Field | Meaning |
| --- | --- |
| `budget` | The budget kind (see Budgets): `corridor`, `entrance_hall`, `great_hall`, `grand_staircase`, `common_room`, `tower`, `classroom`, `room`, `courtyard`, `bridge`, `passage` |
| `materials` | Which variant of each family the cell draws: `castle_wall` (`castle_wall_a` ... `_d`), `castle_floor` (`castle_floor_long`, `_square`, `_dungeon`, `_court`), optionally `castle_portraits` (`_a`, `_b`) |
| `build` | `false`: validated and resolved but not built; neighbours treat it as greybox. Use it while a new cell's pieces are in progress |
| `bay_target` | Target bay length for the cell's runs (default 6 m) |
| `runs` | Per run: `bays` (a forced count) or `bounds` (explicit inner bay boundaries on the 0.5 m grid; `[]` = one bay, no shafts) |
| `floor_border` | Draw a border round each floor (default true) |
| `springing` | A vaulted cell's springing line above its floor (4 m up to the wall height minus 0.7): the cornice's top and the shafts' tops; above it the wall rises behind the vault |
| `shaft` | `plain` (default, `kit_stone_shaft_<h>_plain`, at most 200 triangles), `clustered` (the three-roll shaft for hero spots) or `none` |
| `arches` | Exactly one per door edge of the cell: `{"door", "surface", "piece": "kit_stone_arch_<contact width>x<door ceiling>", "leaves"?}` |
| `openings` | Windows, niches, blind doors and fireplaces: `{"kind", "piece", "run", "bays": [...] \| "all"}` or `"t"` (a centre on the 0.5 m grid) or `"line_bays"` (with `lines`) or `"over": "<door id>"` (a window over a door); `"sill"` in whole 0.6 m courses |
| `lanterns` | `{"piece", "every", "y", "runs"?}`: a wall lantern on every inner shaft whose index is a multiple of `every`, bracket `y` above the floor; and/or `"spots": [{"run", "t": [...]}]` on walls without shafts |
| `hero` | Hand placements: `{"id", "piece", "pos": [x, y, z], "yaw", "args"?}` (frame as `props.json` kit: origin, yaw 0 faces +z) |
| `replaces` | `props.json` props or kit ids the kit stands in for; they are not exported in this cell. A repeated prop may be named by its own id (all copies) |
| `lines` | Bay lines shared by every wall run along one axis: `{"axis", "at": [...], "foot", "top", "corbel"}`; a shaft stands on every line where there is wall, from `foot` to `top`, with a corbel on it |
| `wainscot` | Height (0.6-4 m) of an oak wainscot round every wall; windows keep their sill 0.3 m above it |
| `sky` | `{"plate", "ridge"}`: an open painted-sky ceiling of two pitches per line bay (needs `lines`) |
| `candles` | Up to four floating-candle groups `{"id": "candles_<k>", "rect", "y": [y0, y1], "count", "seed", "bob"?, "lod1"?}`; each is one moving node |
| `portraits` | Wall rectangles filled with framed portraits by the cluster generator: `{"id", "run", "t": [t0, t1], "y"?, "seed", "mix"?}` (`mix`: `gallery` or `still_life`) |
| `railings` | `balustrade` (default, the pierced Gothic balustrade) or `gallery_edge` (corbel table, cornice and balustrade under a gallery's slab edge) |
| `keep_clear` | Layout area ids (e.g. the duelling stage) where no hero stands and no kit reaches below 2.4 m |
| `shade` | `{"radius"?: 0.5-6, "rays"?: 16-256}`: overrides the AO bake of the cell's kind |
| `facade` | `{"sides": [...], "ext"?, "roof"?, "parapet"?, "buttresses"?, "pinnacles"?}`: dress the cell's outer faces on those sides (see Exterior) |
| `moving` | The moving staircase flights this cell draws (must match the layout's `moving` surfaces) |

### Wall runs and bays

Every flat non-door surface of a cell has four sides (`grid.wall_runs`):

| Side | Line | Face normal | t (along the run) |
| --- | --- | --- | --- |
| `n` | z = min z | +z | x |
| `s` | z = max z | -z | x |
| `w` | x = min x | +x | z |
| `e` | x = max x | -x | z |

A side is split into runs at every walkable join across it (doors, joined
floors). Run ids are `<surface>:<side><k>`, k counted from low x (n, s) or
low z (w, e). Door surfaces have no runs: their passage walls are reveals.

Bays come from `solve_bays(run length, bay_target)` unless `runs` forces
them; bay 0 is at the low-t end. A shaft stands on every inner bay
boundary, but only where the Planner draws wall from floor to top
(`CellPlan.walled`); a side that is a railing edge or opens onto a lower
ceiling gets no shafts and refuses openings ("no wall behind ...").

### Openings, arches and doors

- An opening is centred in its bay (or at `t`), at least 0.6 m from both
  bay ends, with its sill in whole courses (blind doors and fireplaces at
  0, others on or above the plinth) and its top at least a cornice below
  the ceiling. Windows open only where nothing roofed lies within one wall
  mass behind them; no two openings overlap, and none cuts a kept prop.
- `over` puts a window over a door: centred on it, no wider than it, its
  sill at least the door's apex plus 0.6 m.
- Every door edge of the cell needs its arch. The arch keeps the door's
  1 m x 2.4 m clearance and the intrusion rule. Its **reveal** is how far
  it lines the door passage: the door surface's own cell lines the whole
  depth (less the far side's 0.5 m when that cell is kitted too); the other
  cell lines 0.5 m. Every passage is lined exactly once.
- `leaves: true` hangs a pair of open oak leaves, each half the span, swung
  fully open and never across the passage: in the reveal when it is deep
  enough, else flat on the cell's wall face beside the arch's quoins, at
  most 0.25 m proud. Only one side of a door may have leaves.

Opening pieces (`grid.OPENING_PIECES`): lancets `kit_window_lancet_1.6x5`,
`_2.6x7`, `_3.2x10`; `kit_window_tracery_8x8`, `kit_window_end_9x12`,
`kit_window_rose_6x6`, `kit_window_slit_0.6x2.4`; `kit_niche_2x3.5`;
`kit_door_blind_3x4.5`, `kit_door_class_3x4.5`;
`kit_fireplace_hooded_4.4x3.6` (a 0.7 m firebox with a hood above it, all
of the hood above 2.4 m).

### The kitted cells

| Kind | Cells |
| --- | --- |
| `entrance_hall` | `entrance_hall` |
| `great_hall` | `great_hall` |
| `grand_staircase` | `grand_staircase`, `grand_staircase_upper` (one budget over both) |
| `corridor` | `gf_corridor_west`, `ff_corridor_north`, `sf_corridor_west`, `tf_corridor_west`, `tf_corridor_east`, `l4_corridor_west` ... `l6_corridor_west`, `l7_corridor_north`, `l7_corridor_east`, `gf_undercroft`, `ff_gallery_east`, `dungeon_west`, `basement_east`, `scullery_passage`, `trophy_stair`, `dungeon_cellars` |
| `common_room` | `common_room_house_a` ... `_d` |
| `classroom` | `dungeon_classroom` |
| `room` | `kitchens`, `sf_library`, `trophy_room`, `hospital_wing` |
| `tower` | `west_tower_stair`, `owl_loft` |
| `courtyard` | `courtyard`, `clock_courtyard` |
| `bridge` | `covered_bridge` |
| `passage` | `passage_tapestry`, `passage_panel`, `passage_painting`, `passage_statue`, `passage_dungeon` |

Stone by area: `castle_wall_a` with `castle_floor_square` in most
corridors and upper rooms; the warm `castle_wall_b` with
`castle_floor_long` in the halls, the hearth rooms, the library and the
east basement; the darker `castle_wall_c` on the Grand Staircase; the cool
green-grey `castle_wall_d` with `castle_floor_dungeon` in the dungeons and
secret passages; `castle_floor_court` in the open courts.

## Placement rules

`rules.py` derives the repeated dressing; nothing here is data:

- a plinth sweep along every wall run at the floor (or the wainscot where
  the cell has one), a cornice under the ceiling or at the springing, a
  string course at the window sills of runs with windows;
- shafts on inner bay boundaries (or on `lines`), quoins at external
  corners and arch jambs, an arch surround per door edge, a threshold
  across every door surface (`kit_threshold_<w>`), a floor border;
- on flat ceilings with shafts, transverse ribs from shaft to shaft (or to
  a corbel under the cornice) and, in bays at least 3 m long, four diagonal
  ribs meeting at a boss; vaulted cells carry their ribs in the vault
  pieces instead;
- balustrades (or gallery edges) on railing runs, one newel where two
  railings end together, a flight per stair surface (stone or marble
  nosings);
- the openings, lanterns, portraits, candles and sky of `castle_kit.json`,
  then the hero placements;
- facade pieces on the outer planes of cells with a `facade`.

Sweeps below 2.4 m break round wall-mounted find-spot tells
(`grid.WALL_TELLS`) and hero pieces against a wall, so the tells stay
visible. String-course pieces shorter than 1.2 m are dropped. Placements
are byte-identical for identical inputs.

## Pieces

`castle_kit/pieces/` holds the builders, grouped by what they make:

| Module | Pieces |
| --- | --- |
| `mouldings.py`, `openings.py`, `stairs.py`, `fixtures.py` | Plinth, string, cornice, shafts, quoins, corbels, ribs, bosses, thresholds, floor borders; arches, windows, niches, blind doors, oak leaves; flights, balustrades, newels; wall and hanging lanterns, banners |
| `vaults.py`, `hall.py` | Lierne and fan vaults, armour suits, the Owl Exchange counter and perches |
| `hall_m3.py`, `hall_roof.py`, `hall_furniture.py` | Arch-braced trusses, beast braziers (a hart, a bear, a boar, a hound), the end window, fireplace, long tables, benches, staff chairs, the owl lectern, the hourglass set, table settings, floating candles |
| `frames.py`, `staircase.py` | Portrait frames and the cluster generator, gallery edges, corbelled landings, the pier stack, the skylight lantern, the floor compass, the guardian portrait |
| `corridors.py`, `tower.py` | The class door, robed statues, tapestries, the rose window, the dry well; the spiral core, slit windows, the knocker door |
| `room_furniture.py`, `rooms_upper.py`, `dungeon.py` | Sofas, armchairs, rugs, tables, chairs, noticeboards, dorm doors, ring chandeliers; the starry dome, bookcases, trophy cases, the armillary, floor inlays; barrel vaults, wall torches, the hidden stone door, barrel stacks, the kitchen hearth, pans and pots |
| `facades.py`, `courtyard.py`, `courtyard_hall.py`, `exterior.py` | Buttresses, crenellated parapets, pinnacles, slate roofs, the outer tracery; arcade bays, the tiered fountain, the gatehouse, market stalls, lamp posts, planters, the Owl Exchange court dressing |
| `wings_a.py`, `wings_b.py`, `secrets.py` | The swinging staircase flights, the clock face, the covered bridge, ward beds and screens; the owl loft, cellar racks; secret-passage entrances, the Wishing Room door |
| `school.py`, `school_2c.py`, `astronomy.py`, `greenhouse.py`, `paddock.py`, `pitch.py`, `grounds.py` | Class rooms built with the kit, the glasshouses, the paddock, the flying pitch, grounds buildings and dressing |
| `catalogue.py` | The Owl Order catalogue stand in each common room |
| `geom.py`, `gallery.py`, `preview.py` | Shared mesh helpers; the piece gallery and stand-in textures for review renders only (never exported) |

A builder returns a `geom.Piece` in its own frame: faces with a face tag
each (below), optional LOD1 faces, and `meta` (seats, floor seats, lights,
fires, a coarse camera-collider shell, footprints). Big walk-through
pieces (vaults, the counter, braziers, balustrades, newels, landings, the
pier, the sky) carry a coarse **collider shell** in `meta["collider"]` so
the follow camera cannot end inside them.

## Materials

One mesh per material per cell. The names are fixed; `castle_textures.json`
lists them all:

| Material | Texture | px | Mapping | Notes |
| --- | --- | --- | --- | --- |
| `castle_wall_a`, `_b`, `_c`, `_d` | `stone_ashlar_a/b/c`, `stone_ashlar_dungeon` | 1024 | world, 4.8 m | family `castle_wall` |
| `castle_floor_long`, `_square`, `_dungeon`, `_court`, `_grass` | `stone_flag`, `stone_flag_square`, `stone_flag_dungeon`, `stone_paving_court`, `grass_turf` | 1024 | world, 4.8 m | family `castle_floor` |
| `castle_ceiling` | `vault_web` (0.3 m courses) | 512 | world, 2.4 m | vaults, soffits, ceilings |
| `castle_wood` | `wood_sheet` | 1024 | sheet | wainscot, door planks, planks, beams |
| `castle_trim` | `trim_castle` | 2048 | sheet | every moulding, frame and iron strap |
| `castle_glass` | `glass_sheet` | 1024 | sheet | emissive (its own image), tinted by the sky light |
| `castle_props` | `props_atlas` + glow mask | 1024 (mask 512) | atlas | the shared props atlas; glow at strength 1.0 |
| `castle_cloth` | `banners` (painted for 1.6 x 6 m banners) | 1024 | sheet | double-sided |
| `castle_sky` | `sky_clouds` (R = cloud cover, A = stars) | 1024 x 512 | fit | unlit; coloured at runtime from `lighting.json` |
| `castle_portraits_a`, `_b` | `portraits_a/b` | 2048 | sheet | 64 canvases each |
| `castle_tapestry` | `tapestries` | 1024 | sheet | invented designs |
| `castle_roof` | `roof_slate` | 1024 | world, 4.8 m | outdoor |
| `castle_foliage` | `foliage_atlas` | 2048 x 1024 | sheet | trees and hedges |

- **Variants**: faces are tagged with the family (`castle_wall`,
  `castle_floor`); the cell's `materials` picks the variant, so a cell
  draws one wall and one floor material.
- **Nothing in a greybox material** inside a kitted cell: walls, wall tops
  and roofs go to the wall variant, floors and treads to the floor variant,
  ceilings to `castle_ceiling`, railings and greybox trim to
  `castle_trim`, the `props.json` kit to `castle_props`.
- **The props atlas is shared by every cell**, kitted or not: a cell's
  props mesh is `<cell>.castle_props` with no embedded image. Any change to
  the slot table (a new `props.json` kit model, a new `props_slots` row)
  changes `props_atlas.key()` and repaints the atlas.
- **Exterior clones** (`<material>@ext`, e.g. `castle_wall_b@ext`): outside
  faces use a clone that shares the base material's textures but receives
  the sun's shadow on High (interiors do not). `castle_glass`, `castle_sky`,
  the portrait sheets and `castle_tapestry` are never cloned;
  `castle_roof` and `castle_floor_court` are outdoor by name. A clone has
  no row or texture of its own (`grid.ext_name`, `grid.EXT_NEVER`,
  `grid.OUTDOOR_MATERIALS`).

### Colours come from palette data only

Every stone, floor, trim, wood, cloth and metal colour comes only from
palette data: the painters' base colours (`textures.py`, `trim.py`,
`portraits.py`, `tapestries.py`), `castle_textures.json` (`palette`,
`props_slots`) and `toon_palette.json`. A palette change needs only a
texture rebuild: no code edit, no UV moves.

- **Piece code writes no colour literal.** Props-atlas faces name their
  colour: `castle_props|kit:<tile>:@<name>` (`grid.palette_tag`), where
  `<name>` is a key of `palette` and a `props_slots` row holds the slot. A
  named slot is keyed by its name, so changing a colour never moves a UV.
- `tests/test_castle_grid.py` (`PaletteRuleTests`) fails on any `#RRGGBB`
  literal in `pieces/` (except `preview.py`, `gallery.py`), `rules.py`,
  `assemble.py`, `schema.py`, `lint.py`, `visibility.py`, `shade.py`,
  `slice.py`, and on any catalogue piece whose palette name has no entry
  or slot.
- **Vertex colours are grey.** `COLOR_0` carries value only (the baked
  shade): r = g = b. No hue lives in vertex colours.
- House accents only through the names `house_a` ... `house_d`.

## Textures

`castle_textures.json` is the spec: per material `texture`, `px`,
`map` (`world` | `sheet` | `atlas` | `fit`), `tile_m`, `family`,
`emissive`, `emissive_strength`, `unlit`, `alpha`, `double_sided`, `tint`
(`sky`, `sky_dome`), `ktx2.mode`, and `milestone` (the textures step paints
every material whose milestone is at most `textures.MILESTONE`; a reserved
name without a painter is left out with a warning and the client keeps the
placeholder colour). `sheets` holds the pixel layouts, `palette` the named
colours, `props_slots` the props atlas's extra slots (append only).

`castle_kit/textures.py` writes `client/public/assets/world/textures/`:

- `<texture>.png` (8-bit RGBA, sRGB values, top row first, no colour
  chunks so every browser decodes the same bytes) and `<texture>_lo.png`
  at half size for Low;
- `<texture>.ktx2` and `<texture>_lo.ktx2` when basisu is available
  (`$BASISU`, see the Blender README): **ETC1S** for the big tiling stone,
  floors and wood, **UASTC** (Zstandard) for trim, glass, props and its
  glow mask, cloth, sky, portraits and tapestries. Full mip chains, rows
  top first (never flipped), deterministic bytes. Without basisu the
  library is PNG only;
- `textures.json`: per material `base`, `lo`, `px`, `emissive`,
  `emissive_lo`, `emissive_px`, `emissive_strength`, `unlit`, `alpha`,
  `double_sided`, `tint`, `ktx2`, `ktx2_lo`, `emissive_ktx2`,
  `emissive_ktx2_lo`, `ktx2_mode`, and for `castle_props` the atlas `key`.
  The client reads `map` from `castle_textures.json` itself.

Painting rules: seeded and deterministic; world textures tile seamlessly;
the ashlar's mortar lines fall on every 128th row from the bottom so 0.6 m
courses register with world y; albedo 0.04-0.85 linear; painted edge bands
(lit top-left, shade bottom-right) stand in for normal maps; `_lo` exactly
half. Glow tiles are the exception to the albedo floor: their base copy is
at most 8 % of the glow colour (`GLOW_BASE_MAX`).

### Face tags and UVs

Every face of a piece carries a tag `<material>|<uv_mode>`
(`grid.face_tag()`, `grid.parse_face_tag()`); a material may carry the
`@ext` suffix.

| uv_mode | Meaning |
| --- | --- |
| `world` | Tiling surfaces: projected on the normal's dominant axis, anchored at the world origin (`grid.world_uv`); walls u to the viewer's right, v = y / tile |
| `trim:<strip>` | Sweeps, mouldings, frames: s = metres along the run (repeating every `u_m`), p = metres across the profile from the strip's bottom (`grid.strip_uv`). Rotated strips (`shaft`, `door_planks`) run s up the member; strips with `cols` cannot wrap, so faces split every `u_m` |
| `fit:<tile>` | A face filling a sheet tile inside its padding (`grid.tile_uv`) |
| `kit:<tile>[:@name]` | `castle_props` only: the props atlas slot |
| `sky` | The sky panels' own UVs onto the whole sky texture |

Every sheet keeps 4 px free inside each strip and tile.

**Trim sheet** `trim_castle.png`, 2048 x 2048, rows from the top:

| Rows | Strip | Profile (m) | u repeat (m) |
| --- | --- | --- | --- |
| 0-255 | `plinth` | 0.6 | 4.8 |
| 256-383 | `string` | 0.3 | 4.8 |
| 384-735 | `cornice` | 0.7 | 3.6 |
| 736-863 | `arch_rim` (voussoir joints every 0.45 m) | 0.3 | 4.95 |
| 864-991 | `window_frame` | 0.3 | 4.8 |
| 992-1119 | `rib` | 0.3 | 4.8 |
| 1120-1407 | `shaft` (rotated) | 0.7 | 4.8 |
| 1408-1535 | `capital_band` | 0.3 | 4.8 |
| 1536-1663 | `nosing_marble` (cols 0-1023), `nosing_stone` (cols 1024-2047) | 0.25 | 2.4 |
| 1664-1791 | `balustrade_arcade` (painted, for LOD1) | 1.0 | 16 |
| 1792-1919 | 128 px fit tiles: `corbel_front`, `corbel_side`, `keystone`, `boss`, `capital_leaf`, `base_block`, `frame_gilt`, `frame_oak`, `finial`, `crest_blank`, `plaque`, `newel_cap` | | |
| 1920-2047 | `iron` | 0.25 | 4.8 |

**Wood sheet** `wood_sheet.png`: `wainscot` (3.2 m), `door_planks`
(rotated), `planks`, `beam`. **Glass sheet**: `light_lancet`, `light_tall`,
`head_tracery`, `quarries`. **Banners**: `banner_house_a` ... `_d` and
`banner_school`. The JSON is authoritative where it and this page differ.

### Texel density

| Surface | Density |
| --- | --- |
| Tiling stone and floors | 213 px/m (1024 px per 4.8 m) |
| Vault webs, soffits | 213 px/m (512 px per 2.4 m) |
| Trim strips | 400-600 px/m (the `_lo` half on Low) |
| Wood sheet | 157-210 px/m |
| Banners | about 170 px/m |
| Props atlas | 64 px slots |

## Shading: the AO bake

The castle uses the characters' shading model, so walls, corners and
undersides read with depth rather than as flat cut-outs. Geometry carries
one grey occlusion value per vertex; every hue and response comes from
`toon_palette.json`.

- **Bake** (`shade.py`, run by `assemble.py` inside the export): a
  deterministic ray cast per drawn vertex, `ao_rays(kind)` cosine-weighted
  Hammersley directions (64; 128 in the Great Hall and Grand Staircase),
  origins 2 cm off the surface, hits within the kind's radius
  (`ao_radius`: corridor 2 m, halls 3 m, Grand Staircase 4 m, rooms
  2.5 m, passages 1.5 m) weighted 1 - (d / R)^2. Occluders are the cell's
  own drawn faces (not glass, sky, cloth, candles or glow) plus the
  Planner faces of neighbours near each door, so a cell's bake depends only
  on the layout and its own kit. Values are welded across coincident
  vertices and smoothed once.
- **Combine**: `shade_value(ao, g)` = clamp(ao^0.8 x g, 0.35, 1), where g
  is the face's greyed value ramp (`world/palette.py` `VALUE_RAMPS`).
- **Storage**: `COLOR_0` on every kit mesh, r = g = b = round(x x 255),
  a = 255, packed as normalized unsigned bytes
  (`visibility.pack_color0`). The only cell colour allowed a hue is
  `floor_grounds` (`grid.COLOR0_HUE_EXCEPTIONS`).
- **Edge loops** (`slice.py`): vertex AO needs vertices where the
  occlusion changes. Only world-mapped faces are sliced (mouldings and
  props already have edges): walls at 0.6, 1.5 and 3 m above the floor and
  below the ceiling, near inner corners, jambs, shafts and heroes; floors
  near walls and round floor-standing heroes; vaults wherever a segment is
  longer than 1.5 m; elsewhere on the 4.8 m world grid. UVs are recomputed
  with the same world formula. The added triangles are capped per cell
  (`slice_allowance`).

The client (`toon/material.ts`) reads the value under `TOON_WORLD_SHADE`:
it shifts the light/shadow threshold, scales fill and colour, lifts
down-facing surfaces with sky fill rather than a brown ground bounce, and
fades walk-through props (`nearFade`) near the camera. High adds a
half-resolution SSAO pass (`post.ssao` in `toon_palette.json`). See the
style guide for the ramp fields.

## Glow

Lanterns, lamp newels, braziers, torches and candles glow through the props
atlas's glow mask at emissive strength 1.0 (`grid.GLOW_LEVEL`). A brighter
glow is a hotter colour, never a higher level. Colours: `GLOW_LANTERN`
`#FFA94D`, `GLOW_CANDLE` `#FFAD5C`, in a band (`GLOW_BAND`) chosen so bloom
halos them by their red while green and blue stay low enough never to clip
to white. No rim or highlight is drawn on glow texels.

## glTF conventions

- **Drawn meshes**: one node and mesh per material, `<cell>.<material>`
  (`ff_corridor_north.castle_wall_a`), merged at build time, with
  `POSITION`, `NORMAL`, `TEXCOORD_0` and `COLOR_0`. Single-sided except
  `castle_cloth`.
- **Groups**: a group of one material is `<cell>.<material>.<group>`
  (`grid.GROUP_RE`): portrait bands `l0`-`l9`, candle groups
  `candles_0`-`candles_3`, `hourglass_sand_house_<x>`,
  `cup_banners_house_<x>`, `swing_flight_<a>_<b>`, secret entrances and
  door leaves, `wishing_room_door`.
- **LOD**: `<node>_lod1` with `extras.kit_lod = {base, distance}`, drawn
  instead of the base beyond `distance` from the camera (to the base's
  bounding box) and always on Low. Distances: balustrades 30 m, portrait
  bands one storey (9 m), candles 40 m, facades and arcades 60 m.
- **Animated nodes** carry `extras.kit_anim` with a `kind`: `bob`
  (candle groups and chandeliers), `fill` (hourglass sand, origin at its
  base so a Y scale fills it), `cup_banners` (shown for the leading house),
  `swing` (a moving staircase flight about its pivot), `owl`, `entrance`
  (secret-passage entrances and the knocker door's leaves), and
  `wishing_room_door`.
- **Placeholder materials**: each `castle_*` material is exported by name
  with no images and the flat greybox colour of what it replaces; the
  client swaps it for the library's material by name. `castle_props` is
  exported white with no emission; `castle_glass` glows its own colour so
  windows read as light without textures.
- **Camera collider**: node `<cell>.collider`, `extras.role =
  "camera_collider"`, no material, never drawn: the Planner's walls, floors
  and ceilings (door holes open, no window holes), closers over each arch's
  pointed head, and the pieces' coarse shells. At most 6,000 triangles.
- Only these node kinds carry extras. Un-kitted cells export exactly as
  greybox, with their kit mesh renamed `<cell>.castle_props`.

### Manifest fields

`client/public/assets/world/manifest.json` gains, per cell (all optional):

| Field | Meaning |
| --- | --- |
| `library` | The `castle_*` materials the file uses (sorted) |
| `kit` | `{tris, draws}` at LOD0 |
| `collider` | The file has `<cell>.collider` |
| `lod` | How many `_lod1` nodes it has |
| `camera` | `{max}` when it differs from 16 m |
| `sees` | Cells visible from this one (indoor cells only) |
| `seats` | `[[x0, z0, x1, z1, y, yaw, front, back, drop], ...]`: bench and chair seats for the sit emote |
| `floor_seats` | `[[x0, z0, x1, z1, y], ...]`: rugs and hearth steps for the floor-sit pose |
| `fire`, `lights` | Animated fires and point lights the pieces bring |

Seats follow the seated pose: a seat top 0.42-0.50 m up, rows 0.40-0.55 m
deep and at least 0.55 m per sitter, arms at least 0.55 m apart, and knee
room kept clear 0.65 m ahead of the row (`grid.SEAT_*`, `KNEE_*`; checked
by the lint).

## Cell visibility (`sees`)

`visibility.cell_sees(layout)` gives every indoor cell the cells it can
see: cells joined by up to two doors or open joins (`SEES_DOORS`), each
with the cells stacked over or under it through stairwells, galleries and
shafts, plus the facades of any outdoor cell in that set. Outdoor cells
(courtyards, grounds) see everything and get no `sees`. The client
disables (but keeps resident) every cell outside the current set,
recomputed on place updates and immediately after a teleport; within 3 m
of a door it uses the union of both sides. `?sees=off` turns culling off.

## The client side

`client/src/game/castle/` loads and draws the kit:

- `materials.ts`: one shared toon material per `castle_*` name (and per
  `@ext` clone), reference-counted by resident cells, freed a few seconds
  after the last release. KTX2 first when the decoder starts (`?ktx2=off`
  forces PNG); any failure or a 15 s timeout falls back to that texture's
  PNG. Low uses the `_lo` files. Glass emission follows the sky light.
- `ktx2.ts`: the self-hosted `@babylonjs/ktx2decoder` (copied into
  `client/public/assets/ktx2/` by `prepare.mjs`); no CDN at runtime.
- `lod.ts`: the LOD selector (distance to the base mesh's bounding box);
  Low forces LOD1 and draws only the nearest candle group of a cell.
- `camera-max.ts`: the per-cell zoom limit, eased as the player changes
  cells. `camera.ts` keeps the camera 0.3 m off wall faces and 0.8 m off
  ceiling faces of a collider, so pieces must keep their relief within
  those numbers.
- `candles.ts`, `cup.ts`, `owls.ts`, `stairs.ts`, `entrances.ts`: the
  animated nodes. `sky.ts`: the sky panels' colours from `lighting.json`,
  set once per clock tick. `visibility.ts`: `sees`. `hlod.ts`,
  `terrain.ts`, `water.ts`, `foliage.ts`: the grounds and the far castle.
- `?layout` draws the walkable rectangles (`?layout=xray` through walls);
  the F3 overlay has a castle line (materials, texture memory, LOD, zoom).

No per-frame allocations: everything is per load, per clock tick or per
quality change, apart from one matrix update per moving group.

## Portraits and tapestries

- **Portrait sheets** `portraits_a` / `portraits_b` (2048 x 2048, `_lo`
  1024): an 8 x 8 grid of 256 px canvases each, 128 in all. A canvas has
  4 px padding, a 20 px painted frame ring and a 208 px painting. Kinds by
  index: 0-31 figures, 32-47 landscapes, 48-63 still lifes; sheet b's
  canvas 63 is the guardian portrait's.
- **The painter** `castle_kit/portraits.py`: seeded, numpy only, our own
  invented work: busts and half-lengths of invented people in period dress,
  landscapes of hills, lakes and towers, still lifes of fruit, books and
  instruments. Never a copy of a real painting, no recognisable person, no
  text. The guardian is a tall elderly keeper in a deep green robe with a
  lantern and a ring of keys under a stone arch.
- **Clusters** (`pieces/frames.py`): a `portraits` rectangle is filled with
  frames from the gallery mix (0.8 x 1.0 m up to 3 x 4 m; rectangular,
  arched, oval), 0.15 m apart, 0.3 m inside the rectangle and 0.3 m from
  openings, lanterns, tells, heroes and talking portraits, covering 65-85 %
  of it (`PORTRAIT_FILL`). At most 40 triangles a frame (LOD1: one quad).
  All of a cluster's faces go in its level band group, one draw per band.
- **Tapestries** (`tapestries.py`, sheet `tapestries`): invented designs on
  `kit_tapestry_2.5x4` and `kit_tapestry_6x3`, at most 0.1 m off the wall,
  their bottom at least 1.2 m up.
- **Talking portraits** (the NPCs) are separate: gilt shadow-box frames
  built by `tools/blender/npcs/` into `client/public/assets/npcs/`, placed
  by `data/npcs/portraits.json`; the clusters keep 0.3 m clear of them.

## Exterior

- **Facades** (`facade` field, `pieces/facades.py`): the outer planes of the
  cells that face the courtyards get buttresses that step back as they
  rise, crenellated parapets with real thickness, crocketed pinnacles and
  pitched slate roofs (45-60 degrees, with eaves), all on `@ext` clones or
  `castle_roof`, each with an LOD1 beyond 60 m. The traceried window over
  the front doors is cut through the shared wall mass so it reads from the
  courtyard too (`layout_lib`, the kitted export only).
- **The courtyard**: arcade walks of 6 m pointed bays with lean-to roofs, a
  tiered fountain, the gatehouse with a raised portcullis, a market corner
  of stalls (awnings in house colours), benches and notice boards, lamp
  posts and planters, all kept clear of the doors' approaches.
- **The grounds and the far castle**: the grounds cells, foliage, lake and
  backdrop are built by `world/grounds_export.py` and related modules; the
  far silhouette (HLOD) from `data/world/exterior.json` by
  `world/hlod_export.py`, on the library's outside stone and slate only,
  set 0.3 m inside the real cells' faces so a partly loaded chunk never
  shows in front of real walls. See the Blender README for these steps.

## Budgets

Per cell, at Medium, LOD0, edge loops included (`grid.BUDGETS`; the lint
treats draws and the triangle cap as errors and a count under the low end
as a warning):

| Kind | Draws | Triangles | AO radius | Loops cap |
| --- | --- | --- | --- | --- |
| `corridor` | 8 | 10-35 k | 2.0 m | 6 k |
| `entrance_hall` | 11 | 80-120 k | 3.0 m | 15 k |
| `great_hall` | 19 | 130-190 k | 3.0 m | 20 k |
| `grand_staircase` (both cells) | 25 | 250-320 k stored, at most 180 k drawn from any gallery | 4.0 m | 30 k |
| `common_room` | 12 | 40-70 k | 2.5 m | 10 k |
| `tower` | 12 | 25-45 k | 2.0 m | 8 k |
| `classroom` | 10 | 40-70 k | 2.5 m | 10 k |
| `room` | 10 | 30-60 k | 2.5 m | 8 k |
| `courtyard` | 12 | 60-120 k | 3.0 m | 12 k |
| `bridge` | 6 | 10-30 k | 2.0 m | 4 k |
| `passage` | 5 | 5-15 k | 1.5 m | 3 k |

- A cell with a `facade` may draw two more meshes than its kind allows
  (its `@ext` wall and `castle_roof`: `grid.FACADE_DRAWS`).
- Moving groups count as draws; the Great Hall counts one house's cup
  banners, since only the leader's show.
- Camera collider at most 6,000 triangles.
- Frame budgets: triangles in view (world plus characters) at most 2 M /
  1.2 M / 500 k at High / Medium / Low, with the castle's own share about
  400 k / 300 k / 300 k as a guideline (`grid.TRIANGLE_BUDGET`,
  `CASTLE_IN_VIEW_TARGET`); 60 fps on the minimum spec, at most 1,000
  draws, at most 1.5 GB of GPU memory, up to 100 characters on screen.
  Low is a fallback: it draws the Medium castle with LOD1 forced and
  half-size textures, and nothing is modelled for Low alone.

## Building and checking

`client/scripts/prepare.mjs` (or `bin/dev`) rebuilds what changed. To run
the castle steps by hand (Blender as described in the Blender README):

```sh
# The texture library (PNG, plus KTX2 when basisu is available)
blender -b --factory-startup --python tools/blender/castle_kit/textures.py -- \
    --out client/public/assets/world/textures

# The castle cells (greybox and kitted) and the manifest
blender -b --factory-startup --python tools/blender/greybox.py -- \
    --layout data/world/layout.json --out client/public/assets/world

# Resolved placements, and the lint of the built kitted cells
python3 -I tools/blender/castle_kit/schema.py
python3 -I tools/blender/castle_kit/lint.py [--cells great_hall,courtyard] [--json]

# Compare two exports: un-kitted cells must keep their geometry
python3 -I tools/blender/castle_kit/lint.py --compare-glb OLD_WORLD_DIR client/public/assets/world

# Unit tests (grid, schema, textures, kit, visibility, ...)
blender -b --factory-startup --python tools/blender/tests/run_tests.py

# Review stills (written under art/renders/, which is gitignored)
blender -b --factory-startup --python tools/blender/world/render_toon.py -- \
    --out art/renders/kit --prefix day --hour 13 --views great_hall
blender -b --factory-startup --python tools/blender/world/render_toon.py -- \
    --out art/renders/kit --prefix gallery \
    --views gallery_mouldings,gallery_arches,gallery_openings,gallery_fixtures,gallery_stairs
```

The lint checks, per kitted cell: intrusion below 2.4 m, door clearances,
overhead clearance, tread centres against the ramp, wall coverage,
z-fighting and degenerate faces (also against neighbours' greybox), UVs
inside their strip, tile or slot, pieces only on real wall, seats and knee
room, draw and triangle budgets (summed over shared budgets, with the
Grand Staircase's drawn estimate), the collider size, grey `COLOR_0` with
values in range, and the edge-loop budget. It exits non-zero on errors.

`prepare.mjs` reruns a step when one of its sources changes (the step
table is `client/scripts/asset-steps.mjs`). Editing anything under
`castle_kit/` rebuilds the castle cells; `textures.py`, `trim.py`,
`props_atlas.py`, `portraits.py`, `tapestries.py`, `grid.py`,
`castle_textures.json` and `props.json` also repaint the textures; editing
`world/` rebuilds the furniture, dorms, school rooms, icons and textures
as well.

## Recipes

### Kit a new cell

1. Make sure the cell's surfaces, doors and stairs are in
   `data/world/layout.json` and `check_layout.py` is clean (see the Blender
   README).
2. Add an entry to `castle_kit.json` with `"build": false`, its `budget`,
   `materials`, one arch per door edge and, if needed, `bay_target` or
   `runs` bounds. Run `schema.py` until it resolves without errors or
   warnings.
3. Add openings, lanterns and hero placements; list the `props.json` props
   the kit replaces (keep interactables and find-spot tells).
4. Set `"build": true`, rebuild the cells and run the lint. Keep within
   the kind's budget; give the cell a `shade` override only if its spaces
   are unusually tall or low.
5. Look at it in the game (`bin/dev`) and in a `render_toon.py` still, by
   day and night.

### Add a piece

1. Write a builder in the right module under `castle_kit/pieces/` that
   returns a `geom.Piece` in its own frame (origin at the bottom centre,
   yaw 0 facing +z; a wall piece's back on the wall). Tag every face with
   a material and UV mode; name props-atlas colours through
   `grid.palette_tag` and add any new palette name and `props_slots` row to
   `castle_textures.json`.
2. Register it in `pieces/__init__.py` (`BUILDERS` or `BUILDERS_SIZED`, and
   a `CATALOGUE` default for the gallery).
3. Keep it inside the clearances: nothing proud of 0.25 m below 2.4 m
   unless it is walk-through furniture, hanging parts above 2.4 m. Give a
   big walk-through piece a coarse collider shell, a seat piece its seat
   rows, a piece with more than a few hundred triangles seen from afar an
   LOD1.
4. Place it as a hero (or from a rule), rebuild, lint, and check it in the
   piece gallery still.

### Change a colour

Edit the palette name in `castle_textures.json` (or a painter's base
colour, or a toon row in `toon_palette.json`) and rebuild the textures.
Cell files, the manifest and `textures.json` stay byte-identical; a toon
row change touches no asset at all.
