# Art style guide

How the world of SpellHollow School looks and how its environment assets
are built. Everything is made for the project: modelled and textured with
scripted Blender (`tools/blender`), with third-party sources only where
[`art/CREDITS.md`](../../art/CREDITS.md) lists them. Nothing is traced or
extracted from films, games or official artwork; reference images are for
proportion and mood only.

> Code comments cite this document's sections by number ("style-guide.md section 3.1").
> When you renumber or move a section, update the citations too
> (`git grep style-guide.md`).

Related pages:

- characters: [`base-body-spec.md`](base-body-spec.md) (bodies, rig, slots,
  budgets), [`garment-shapes.md`](garment-shapes.md) (clothing),
  [`character-adoption.md`](character-adoption.md) (character data, shading,
  LODs, shadows), [`character-look.md`](character-look.md) (hair, faces,
  outlines, anti-aliasing);
- the castle: [`castle-kit.md`](castle-kit.md);
- what is committed and how to rebuild it: [`repo-layout.md`](repo-layout.md).

The budgets below exist because the game runs in a browser: 60 fps, at most
1,000 draw calls and 1.5 GB of GPU memory with up to 100 characters on screen.

## 1. Direction

**Stylised anime with real-time toon shading** (in the spirit of games such
as Genshin Impact; reference for style and technique only, never for designs
or assets): believable Gothic collegiate architecture at real scale, drawn
with clean two-band toon shading, warm tinted shadows, thin outlines on the
characters tinted by the local colour (the castle has none and reads through
its shading) and a saturated but harmonious palette. Readable shapes first,
detail second.

**Not flat 2D.** The world must read as clearly three-dimensional, never
paper cut-outs: corners, wall bases, soffits and the space under galleries
fall softly into the tinted shadow band, undersides take the sky's hue, trims
get a rim, and High adds tinted screen-space ambient occlusion (section 11).

### Characters

- **Bodies:** two, offered as Male (`body_a`, about 7.4 heads) and Female
  (`body_b`, about 6.7 heads, stylised on purpose), wrapped onto CC BY
  sculpts ([`base-body-spec.md`](base-body-spec.md)). One neutral build; the
  only body choice is height, a uniform scale of the whole character.
  Characters are final-year students and adults: never chibi or childlike,
  never sexualised. Outfits stay modest.
- **Faces:** flat, simple planes, painted rather than modelled: each body
  has its own script-painted face texture and mask
  ([`character-look.md`](character-look.md), section 3), sampled in the same
  character material, so one draw per character stays. Eyes are larger than
  realistic but never childlike: a bold upper lash line with a small wing, a
  lower lash hint, a layered greyscale iris tinted by the player's eye colour
  with a subtle unlit glow, a pupil, two crisp highlights, a soft cool shadow
  under the upper lid. Brows are clean tapered strokes in the hair colour;
  the nose a small soft shade; the mouth a short line with a faint lip tint;
  a subtle blush. Lines on the skin keep a minimum contrast so they read on
  every skin tone. Light falls on the face as a designed shape (a face-shadow
  threshold map at Medium and High) into a warm peach-rose shadow.
- **Hair** is the biggest part of the silhouette: a few large, clean
  sculpted shapes with smooth shading normals, crisp pointed tips, crown
  volume, an uneven fringe with gaps showing the forehead, side locks framing
  the face; never a smooth helmet-like shell, never a bundle of thin tubes.
  One continuous highlight band (angel ring) in a lighter tone of the hair's
  own colour. The scalp under the hair is painted in the hair colour, and
  hair is solid (no background shows through it). Each style belongs to one
  body and fits under every hat.
- **Shading:** a soft but narrow light-to-shadow step; tinted shadows (warm on
  skin, cool on cloth, never grey); a hemispheric sky fill; a rim light; real
  cast shadows from the nearest characters at Medium and High, blob shadows
  otherwise.
- **Baked shade layer, not flat albedo:** body, garments and hair carry a
  baked greyscale shade layer (ambient occlusion in creases, soft painted
  fold shading, a gentle top-to-bottom gradient). Player dyes tint it, so
  colours stay choosable. Faces stay clean.
- **Outlines:** an inverted hull round each near character (Medium and
  High), about one CSS pixel wide, a darker, saturated version of the local
  colour (never black), thinner on the face (jaw, chin and cheek contour
  only), hands and hair than on clothing.
- **Clean edges:** Medium and High draw at native resolution (up to a device
  pixel ratio of 2) with 4x MSAA; a dynamic-resolution guard steps down on
  slow GPUs.
- **Clothing:** clean silhouettes and hems, trim bands, piping and small
  metal accents, layered pieces; folds as soft shading rather than geometry;
  bold graphic patterns. Every garment is a design on one of a few fitted
  garment shapes ([`garment-shapes.md`](garment-shapes.md)).
- **Review renders** use one cel-shaded Blender setup
  (`tools/blender/characters/charlib/toon.py`); in-game shots are the judge.

### Environment

- **Silhouette over surface noise.** Big, clear forms: pointed Gothic arches,
  buttresses, deep window reveals, cloisters, towers with steep slate roofs.
  Exaggerate depth (reveals, mouldings, overhangs) by 10-20% so forms read at
  distance.
- **Flat colour regions, painted edges.** Albedo is procedurally painted as
  one clean tone per block, plank or fold with crisp painted edges; no
  photo-sourced textures, no fine noise or grain, no light or shadow painted
  into the textures (they hold colour only, so a palette swap is a texture
  rebuild).
- **Reads as 3D: a baked greyscale shade layer.** Every kitted cell carries
  one grey value per vertex in `COLOR_0` (r = g = b = x): ray-cast ambient
  occlusion times a gentle height gradient. The shader reads x as shade,
  never as colour (section 11). Every hue stays palette data
  (`tools/blender/world/palette.py`, `data/art/castle_textures.json`,
  `data/art/toon_palette.json`): a palette swap rebuilds textures only and a
  tint swap rebuilds nothing.
- **Warm inside, cool outside.** Interiors glow with candle and torchlight
  (amber), exteriors sit under cool skies (blue-grey). The contrast guides
  players toward doors and gathering spaces.
- **Worn but cared for.** Centuries-old stone with soft wear on edges and
  steps, polished wood where hands touch, grime only in corners.
- **Fun to get a little lost.** Every corridor gets a landmark (a statue,
  stained-glass window, tapestry, portrait cluster) so players can navigate by
  memory. Repetition of kit pieces is broken by landmark props every 15-25 m.

The individual spaces (Entrance Hall, Great Hall, Grand Staircase,
corridors, courtyards, cloisters, grounds) and their sizes are described in
[`castle-kit.md`](castle-kit.md).

## 2. Castle palette

Clean, flat colour regions, saturated but harmonious, made to read under
two-band cel shading with warm, tinted shadows. Large painted value shifts
stay (walls darker at their base); small realistic detail (grain, mottling,
speckle) goes, because the toon ramp turns it into noise.

The source of truth is `tools/blender/world/palette.py`: `CASTLE` (the
greybox castle cells take their colours from it through
`layout_lib.MATERIALS`), `GLOW` (emissive colours) and `VALUE_RAMPS`.
Values are sRGB albedo. Keep albedo between 0.04 and 0.85 linear luminance
(roughly `#383838` to `#ECECEC`): no pure black or white surfaces (the
tests check the palette).

| Name | Hex | Use |
| --- | --- | --- |
| `stone` | `#E4D2B0` | Interior walls (greybox `wall`); the lit, upper value |
| `stone_exterior` | `#D3C1A0` | Outer walls (`wall_exterior`) |
| `stone_trim` | `#EEE2C8` | Trim, carved stone, plinth props |
| `stone_low` | `#B9AB8E` | Drystone walls on the grounds |
| `stone_dungeon` | `#7E8C84` | Dungeon walls: cool green-grey |
| `stone_dark` | `#5E6372` | Dark stone and iron-grey bases |
| `flagstone` | `#B99A78` | Room floors, a step darker than the walls |
| `flagstone_corridor` | `#AA9076` | Corridor floors |
| `flagstone_courtyard` | `#C4B79E` | Courtyard paving |
| `flagstone_dungeon` | `#646B66` | Dungeon floors |
| `flagstone_bridge` | `#BBA886` | Bridges |
| `marble` | `#EDE5D6` | Marble stairs and statues |
| `threshold_wood` | `#93603C` | Door thresholds |
| `ceiling` | `#7E5842` | Ceilings: warm timber |
| `roof` | `#4C6596` | Roofs: clear slate blue |
| `oak`, `oak_rail` | `#8E5634`, `#7A4529` | Timber props, railings |
| `gold` | `#EDBB4C` | Gold leaf, frames, trim accents (sparingly) |
| `bronze`, `copper`, `silver` | `#B97C3C`, `#CC6D3E`, `#C6CDD8` | Metals |
| `iron` | `#3E4150` | Wrought iron (lifted: never black) |
| `leather`, `canvas`, `parchment` | `#663A27`, `#BE9A74`, `#EBD7A8` | |
| `glass` | `#93D3F2` | Window glass |
| `plant` | `#4F9A3C` | Potted plants |
| `grass` | `#8CC152` | Grounds: bright yellow-green by the castle (deepens toward the lake) |
| `water` | `#2C8BB0` | The lake: clear teal-blue |

House colours are used only on banners, scarves, crests, upholstery and
table runners, never on architecture (the item dye palette,
`data/items/palette.json`, is separate):

| House slot | Primary | Secondary |
| --- | --- | --- |
| `house_a` | `#B0222A` scarlet | `#E2B23A` gold |
| `house_b` | `#EDB72F` yellow | `#463A36` black (lifted) |
| `house_c` | `#24499A` blue | `#B58542` bronze |
| `house_d` | `#1F7444` green | `#C3C8CF` silver |

**Emissive** (`GLOW`, also the colours of the lights that come with them):
fire `#FF7A2E`, candle `#FFAD5C`, lake `#36687E` (moonlit water), green `#56F08E`, sky
`#A8CCFF`, sun `#FFE29A`. Emissive is the only thing allowed to exceed albedo
brightness; the client's bloom picks it up. **Warm, never white:** lanterns,
lamp newels and braziers glow `#FFA94D`, candles `#FFAD5C`, at emissive level
1.0 (a brighter glow is a hotter colour, not a higher level), over a base at
most 8% of the glow colour.

**Rooms and kit.** The dorm rooms, classrooms, duel arena and the castle kit
keep their own colours, put once through `palette.grade()`: near-blacks
lifted, saturation raised (never above 0.88), dead greys nudged warm. Use
`grade()` for new colours so they match. Painted textures
(`tools/blender/world/paint.py`) keep one clean tone per stone block, plank
or fold, a crisp lit edge on the upper left and a crisp shade edge on the
lower right of blocks, and a large soft value drift.

**Painted value ramps.** Greybox cells carry a grey `COLOR_0` ramp on the
materials below. `cell` ramps run from the lowest to the highest point of
that material in the cell (each room's walls darken toward their base);
`world` ramps run over absolute heights -14 to 30 m, so they stay continuous
across cells. `floor_grounds` keeps a hue ramp, the one exception:

| Material | Mode | RGB at the bottom | RGB at the top |
| --- | --- | --- | --- |
| `wall` | cell | (0.70, 0.70, 0.70) | (1.0, 1.0, 1.0) |
| `wall_dungeon` | cell | (0.76, 0.76, 0.76) | (1.0, 1.0, 1.0) |
| `wall_exterior` | world | (0.67, 0.67, 0.67) | (1.0, 1.0, 1.0) |
| `wall_low` | world | (0.82, 0.82, 0.82) | (1.0, 1.0, 1.0) |
| `trim` | cell | (0.86, 0.86, 0.86) | (1.0, 1.0, 1.0) |
| `floor_grounds` | world | (0.70, 0.86, 0.92) | (1.0, 1.0, 0.92) |

### Lighting data (`data/art/lighting.json`)

What the client lights the castle with. Colours are sRGB hex, directions
world Y-up unit vectors pointing from the light into the scene (Babylon
`DirectionalLight.direction`; x east, z south), intensities in Babylon units.

- `castle.keys[]`: `{ hour (game hour 0-24, sorted), name, sun: { body:
  "sun" | "moon", direction, color, intensity }, hemi: { sky, ground,
  intensity }, sky: { zenith, horizon }, fog }` (fog is also the clear
  colour). The client interpolates linearly by game hour between
  neighbouring keys, wrapping at 24, and renormalises the direction. The
  sun rises in the east, crosses the south and sets in the west; the moon
  takes over at night. Night reads by hue (saturated blue moonlight, a
  violet-blue fill), never by black.
- `rim.color`: the rim-light colour; the per-material `rim` strength scales it.
- `lights`: the named point-light colours (candle, torch, fire, lantern,
  window, magic, lake, brew, stage and rose-window glows and a few more).
  Every light the world builders emit uses one of these colours; the tests
  enforce it.

Rooms (dorms, classrooms, arena) keep their `ambient` and `lights` in their
manifests (`instances.json`); the client turns the sun off indoors.

### Toon ramp data (`data/art/toon_palette.json`)

The world's toon ramps in the client's `ToonRamp` format
(`client/src/game/toon/palette.ts`): `{ default, materials: { "<material>":
<partial ToonRamp> }, post, outline, characters, ... }` (`_doc` keys are
comments). Each row overrides the client's `BASE_RAMP`; the fields are listed
in section 11.

Lookup, as `worldRamp` does: the exact material name (greybox names such
as `wall`, `floor_room`, `kit`; room materials by full name, e.g.
`dorm_house_a_stone`), then the longest key ending in `*` that prefixes it
(`floor_*`, `glow_*`; `furniture*` covers every furniture model), then
`default`. A new material name needs no code change: add a row. The tests
check that every world material resolves to a row.

`post.ssao` (High only): the screen-space AO's `tint` (the multiplier at
full occlusion: mauve, never grey), `strength`, `radius` (metres), `fadeEnd`
(metres) and `characters` (the share characters get: contact only).

**Review stills.** `tools/blender/world/render_toon.py` renders the castle
and rooms from the exported assets with a toon preview built from these two
files; see [`tools/blender/README.md`](../../tools/blender/README.md).

## 3. Materials

The toon renderer uses **base colour (and emissive) only**: normal,
roughness and metallic maps are not used. glTF PBR materials are converted
to toon materials on load (`client/src/game/toon/convert.ts`).

- **Castle kit:** kitted cells use the shared `castle_*` materials, trim
  sheet strips and face tags of [`castle-kit.md`](castle-kit.md), painted by
  script and loaded once by the client. Piece code names its colours and
  writes none: kit colours come only from palette data
  (`data/art/castle_textures.json` `palette`, `data/art/toon_palette.json`).
  The kit's KTX2 codecs are chosen per material (ETC1S for the tiling stone
  and wood, UASTC for trim, glass, props, cloth, sky and portraits); without
  basisu the build writes PNG, which the client also loads.
- **Portraits:** invented canvases (figures, landscapes, still lifes; no
  text, nobody identifiable) painted by `castle_kit/portraits.py` onto shared
  sheets, with frames in the palette names `frame_gilt`, `frame_oak`,
  `frame_ebony`.
- **Sky ceilings** (`castle_sky`): a greyscale data texture (cloud cover in
  RGB, stars in alpha) whose every hue comes from `data/art/lighting.json`,
  unlit and unfogged, never above 0.92 in any channel (under the bloom cut).
- **Greybox** materials (`tools/blender/layout_lib.py`) are flat colours by
  role, replaced as kit art lands.
- **Trim strips** are laid out in horizontal strips whose heights are powers
  of two in texels (64, 128, 256, 512) so UVs snap to them.

## 4. Texel density

| Asset type | Texel density | Notes |
| --- | --- | --- |
| Castle architecture, tiling stone and floors | 213 px/m | 1024 px per 4.8 m tile (mortar about 2.5 px); vault webs 512 px per 2.4 m. [`castle-kit.md`](castle-kit.md), section 8 |
| Castle trims | 400-600 px/m at High, 200-300 px/m at Medium and Low | the 2048 trim sheet at High, its 1024 half at Medium and Low |
| Other architecture (rooms not yet kitted) | 512 px/m | 2 m tile at 1024 px |
| Hero props (staff table, lectern, fountain) | 512-1024 px/m | up to 1024 px/m on parts seen within 2 m |
| Small props (goblets, books, candles) | 1024 px/m | packed into shared atlases |
| Characters | swatch atlas plus a 1024 px face texture | [`base-body-spec.md`](base-body-spec.md) |

The castle kit's UVs are computed by formula (`grid.world_uv`, `strip_uv`,
`tile_uv`), so its densities hold by construction. For hand-modelled assets,
check density against a 1 m reference cube before export; deviations over
25% need a reason.

## 5. Kit grid and snapping

The modular kit matches the walkable layout (`data/world/layout.json`) so
greybox and final art line up one to one. The castle kit's grid (0.5 m plan
grid, bays solved per wall run, 0.6 m courses, mouldings, opening sizes) is
`tools/blender/castle_kit/grid.py`, summarised in
[`castle-kit.md`](castle-kit.md), section 2. General rules:

- **Heights:** 9 m floor-to-floor; corridor ceilings at 8.4 m with a 0.6 m
  slab; big halls 20-29 m.
- **Wall thickness** fills the gap between rooms in the layout: 1-1.5 m
  inside, 2 m for exterior walls. A wall's inner face sits exactly on the
  layout rectangle's edge.
- **Openings:** doors 2, 3, 4 or 6 m wide, 3.5-8 m high; arches are pointed
  (two-centred, apex at 0.55 x span above the springing line).
- **Stairs:** risers 15-17 cm, treads 30-45 cm (grand staircases up to
  56 cm); the layout ramp passes through the middle of each tread.
- **Pivots** at the bottom of the piece, on the inner face, at its left end
  seen from the walkable side. Snap at 0.25 m and 90 degrees for kit pieces
  (15 degrees for props).

## 6. Polycount and texture budgets

Triangles at LOD0. Most assets share trims and atlases instead of owning
textures.

| Asset type | Triangles (LOD0) | Textures |
| --- | --- | --- |
| Kit wall 4 x 6 m | 300-800 | shared tiling + trim |
| Kit arch / doorway | 800-1,500 | shared trim |
| Kit column, buttress | 600-1,200 | shared trim |
| Kit stair flight (per 3 m) | 400-900 | shared trim |
| Window with tracery | 1,000-2,500 | shared trim + glass |
| Hero prop | 3,000-8,000 | one set at 1024 |
| Furniture (bench, chair, desk) | 500-1,500 | shared wood |
| Small prop | 50-400 | shared atlas |
| Tree (section 12) | 1,400-2,100 (cap 6,000); LOD3 a 36-triangle impostor | the shared foliage atlas (2048 x 1024) |
| Hedge | at most 360 per metre of run | the shared foliage atlas |
| Rock / boulder | 300-1,200 | shared tiling |
| Dressed character | 52,000 max | [`base-body-spec.md`](base-body-spec.md) |

**Per streaming cell** (a room or a 40 m grounds tile): at most 150,000
triangles and 40 draw calls at LOD0, merged by material. Interiors should use
4-8 materials per cell. The world as a whole targets at most 600 draw calls,
leaving the rest for characters and UI.

**GPU memory:** the whole environment texture set resident at once stays
under 600 MB (with mips); characters get 400 MB.

## 7. Naming

Lowercase `snake_case` everywhere; ASCII only; no spaces. Houses use neutral
slots in file names and identifiers (`banner_house_a`, not a house name);
see [`CONTRIBUTING.md`](../../CONTRIBUTING.md).

| Thing | Pattern | Example |
| --- | --- | --- |
| Kit piece | `kit_<family>[_<piece>][_<w>x<h>][_<variant>]` | `kit_stone_arch_5x6` |
| Prop | `prop_<category>_<name>[_<variant>]` | `prop_furniture_bench_long` |
| Material | `<family>_<name>` | `castle_trim`, `stone_flag` |
| LOD node | `<asset>_lod<N>` | `kit_stone_arch_3x4_lod1` |
| Cell file | `<cell id>.glb` (from the layout) | `entrance_hall.glb` |
| Blender scene | `<asset>.blend` under `art/`, saved by its build script (generated and gitignored) | `furniture.blend` |

## 8. glTF export rules

- glTF 2.0 binary (`.glb`), **Y up, metres**, +Z forward (the front of a prop
  or character faces +Z). In Blender: Z up, front facing -Y, export with
  "+Y Up".
- Apply all transforms and modifiers before export; object scale exactly 1.
- One material per primitive; no unused materials, images, cameras, lights or
  animations (except on characters).
- Single-sided materials unless the asset is a card (foliage, banners, cloth
  edges).
- Alpha: `OPAQUE` by default, `MASK` (cutoff 0.5) for foliage and tracery,
  `BLEND` only for glass and magic effects.
- Attributes: `POSITION`, `NORMAL`, `TEXCOORD_0`, `COLOR_0` (masks; the
  castle's shade value) and the character attributes of section 11. 16-bit
  indices: split meshes above 65,535 vertices.
- No collision meshes: collision and walkability come from `layout.json`
  (solid furniture blockers are generated, see `tools/blender/world/solids.py`).
  The one exception: a kitted cell carries a never-drawn camera collider
  (`role: "camera_collider"` in node `extras`) for the follow camera's rays.
- Custom data in node `extras` only when the client needs it, documented in
  the exporter.

## 9. LODs

- Assets over 1,000 triangles get LODs: LOD1 at about 50% triangles, LOD2 at
  about 25%; trees and hero props add LOD3 at about 10%. LODs keep the same
  materials and pivot.
- Blender does not export `MSFT_lod`, so LOD meshes are separate nodes: the
  castle kit exports `<cell>.<material>_lod1` nodes with `extras.kit_lod` and
  the client switches by distance to the base mesh's box (Low always draws
  LOD1); characters ship `_lod0`-`_lod3` meshes in one file.
- **Far castle:** `hlod.glb` holds cheap stand-ins for the whole castle,
  one mesh per chunk and material, drawn only while the player is outdoors
  and hidden for chunks whose real cells are loaded
  (`client/src/game/castle/hlod.ts`).
- Trees and hedges switch per planting: the client picks each one's LOD by
  its size on screen (never nearer than 16 / 40 / 90 m) whenever the camera
  has moved or turned; a tree's LOD3 is an impostor (three crossed quads
  showing a picture of its LOD0); Low starts at LOD1. A change crossfades
  over 0.3 s through complementary screen-door dithers, with a 4% hysteresis.

There are no lightmaps: the toon lighting, the baked shade value and the
painted edges carry the forms, so environment meshes need no second UV set.

## 10. Quality levels

| Quality | Render scale | Anti-aliasing | Passes |
| --- | --- | --- | --- |
| Low | min(1, 0.75 x DPR) | the canvas's MSAA | toon shading only: no outlines, no bloom, no shadow maps, blob shadows |
| Medium | min(DPR, 2) | 4x MSAA on the scene target | character outlines, bloom, character shadow map (8 nearest) |
| High | min(DPR, 2) | 4x MSAA on the scene target | as Medium, plus sun shadows outdoors, SSAO, 12 character shadow casters |

Medium and High have a dynamic-resolution guard. The profiles are in
`client/src/settings.ts`.

## 11. Toon rendering

The client does it all at run time from the art as exported: no lightmaps,
no special shader assets. What the art provides is flat, clean colour
regions, good normals, a baked greyscale shade value per vertex (characters'
`_SHADE`, the kitted castle's `COLOR_0`) and the ramp tables.

### The lighting model

Every lit material (castle, rooms, furniture, characters) is a toon
material (`client/src/game/toon/material.ts`). Per pixel, in sRGB:

- **Two bands.** The key light (sun or moon) becomes lit / shadow with a
  narrow soft step over n·l (`threshold` ± `softness`; a total step of
  0.05-0.1). Where the key is weak next to the fill (rooms have no sun) a
  surface takes the lit band. Local lights (torches, room lamps, lit wands)
  add soft pools on top and never darken the fill. On High the sun's shadow
  map feeds the same step, so cast shadows are the shadow band too; it falls
  on the outdoor materials only (`floor_grounds`, `floor_courtyard`,
  `water`, `wall_exterior`, `roof`).
- **Warm shadows, never grey.** The shadow band keeps `shadowLight` of the
  direct light and is multiplied by `shadowTint`, plus `saturation` extra
  saturation.
- **Ambient** is the scene's sky/ground hemisphere, tinted by `shadowTint`
  where unlit.
- **Rim** (`rim`), **highlight** (`specular`: a crisp step for metal).
- **Grade:** slightly more saturation and a soft shoulder above 0.86 instead
  of clipping. No filmic tone curve: it greys flat colour.
- **Baked shade.** On characters and the kitted castle the shade value x
  moves the key's threshold, so creases and corners fall into the *tinted*
  shadow band (`shadeShift`); the fill keeps only `aoFloor` of itself where
  x is lowest; on world rows the colour keeps `valueFloor` of itself there;
  undersides take a darker sky fill in the sky's hue (`skyLift`); trims,
  wood and props get a rim (`rimWidth`). Walk-through props (braziers,
  candles, banners) dither out near the camera (`nearFade`).
- **Characters** add the angel ring on hair, a face-shadow map, cast
  shadows and outlines: [`character-adoption.md`](character-adoption.md) and
  [`character-look.md`](character-look.md).
- **SSAO** (High): a half-resolution screen-space AO pass on the scene's
  depth (8 samples, one depth-aware blur, tinted mauve in the final pass;
  characters get 0.3 of it, glass, glow and sky none).
- **Bloom** picks out the brightest parts (emissive candles and windows,
  spell light) at a quarter resolution.
- **The castle has no outline**: it reads through its shading.

### The ramp fields (`client/src/game/toon/palette.ts`)

One row (`ToonRamp`) per material. Unset fields fall back to `BASE_RAMP`.

| Field | Range | Meaning |
| --- | --- | --- |
| `threshold` | -1..1 | n·l where the lit band is half on (0 = terminator, -1 = always lit) |
| `softness` | 0.001..0.5 | half-width of the soft step (0.025-0.06 on the world: narrow) |
| `shadowTint` | sRGB 0..1 ×3 | multiplier of the shadow band: warm, saturated, not grey |
| `shadowLight` | 0..1 | share of the direct light left in the shadow band |
| `rim` | 0..2 | rim light (low on architecture, higher on metal, glass and cloth) |
| `specular` | 0..2 | crisp highlight strength (metals, glass) |
| `saturation` | 0..1 | extra saturation in the shadow band |
| `outline` | 0..1 | the frame alpha's SSAO class: 0 takes no SSAO (glass, glows) |
| `shadeShift` | 0..1 | how far the baked shade x moves the threshold |
| `aoFloor` | 0..1 | share of the fill and local light kept where x is lowest |
| `skyLift` | 0..1 | fill times `1 + skyLift · n.y`; world rows also take the sky's hue for the ground term |
| `rimWidth` | 0..1 | width of the characters' rim; a world row that sets it takes that rim |
| `valueFloor` | 0..1 | world only: colour times `mix(valueFloor, 1, x)` (default 1) |
| `nearFade` | metres ×2 | world only: `[near, far]` dithered fade near the camera (default off) |
| `farStart`, `farEnd` | metres | world only: the step softens and rim and highlight fade with distance, so distant thin facets do not alias |

A world row reads x from `COLOR_0` (and stops multiplying the albedo by it)
when it has `shadeShift` > 0, `aoFloor` < 1 or `valueFloor` < 1 and its mesh
has vertex colours. Emissive materials (`glow_*`) want `threshold: -1,
outline: 0`; glass `outline: 0`.

### What character files can say

- **Region:** `extras.toon` on a primitive, mesh or node (`"metal"`,
  `"skin"`, `"hair"`, `"flat"`, `"primary"`, ...) sets the region of every
  vertex in it; otherwise the client derives it at assembly
  (`characters/assemble.ts` `vertexRegion`) from the dye mask. On gear, a
  `COLOR_0` alpha below 0.5 marks a vertex as metal.
- **Shade layer:** the vertex attribute `_SHADE` (VEC4): x the baked shade,
  y and z the hair's band coordinates, w an outline/SSAO hint
  ([`character-adoption.md`](character-adoption.md), section 3).
- **Outline layer:** `_OUTLINE` (VEC3): the hull's smoothed normal times its
  width weight ([`character-look.md`](character-look.md), section 4).
- **Fringe hem:** a hair file's LOD0 mesh carries `extras.fringe_hem` and
  `extras.fringe_shadow` for the forehead shadow.

The merged character keeps one draw call: the region shares the vertex
colour alpha byte with the garment finish (`region · 16 + finish · 4`).

### Day and night

`client/src/game/clock/daynight.ts` lights the castle by game hour from
`data/art/lighting.json` through `toon/lighting.ts` `sceneLights`, the same
mapping as the review renders (`render_toon.py`): key light
`0.3 + 0.7 · intensity` in its hue, fill `intensity / 0.62` of a
half-whitened sky colour, both times one exposure (about 0.38) so a surface
lit at noon is about 1. The sky dome (`toon/sky.ts`) takes zenith and
horizon, the glow around the sun or moon and stars at night. The world
manifest's cell `lights` become warm torch pools from dusk to dawn (the
nearest few, `toon/torches.ts`). A room's manifest `ambient` becomes its fill
(`toon/lighting.ts` `roomFill`; rooms have no sun, so their fill sits above
the castle's).

## 12. Vegetation

Stylised painted foliage in the spirit of painted animation backgrounds.
Built by `tools/blender/world/foliage.py`, drawn by
`client/src/game/castle/foliage.ts`.

- **Crowns** are many overlapping leaf-cluster cards (a painted cluster of
  small two-tone leaves with a jagged outline, alpha mask at 0.5) round a
  dozen and a half clumps, a third standing as fins, over a dark solid core
  so no sky shows through the middle. Normals are bent away from the
  crown's centre, so the toon step draws two or three soft bands across the
  crown and each clump has a lit top and a shaded underside; never facets.
- **Trunks** taper and curve, flare at the roots and fork into the crown's
  limbs, on a painted bark strip.
- **Pines** are whorls of drooping needle-spray strips round a solid needle
  cone, their tips ragged and the whorls irregular; the lowest needles stay
  over 2.4 m (`TREE_CROWN_CLEAR`) at every scale.
- **Hedges** are one continuous clipped mass covered in leaf cards and
  tufts; a run is a rounded end, middle segments stretched to fit and the
  other end, so any length and height has no seam.
- **Colour** is palette data only (`castle_textures.json` `palette` rows
  `plant`, `leaf_light`, `foliage_*`); a palette swap is an atlas rebuild.
- **Shading:** one toon row, `castle_foliage`, the one exception to the
  narrow-step rule: softness 0.14, so the bent normals give soft bands;
  shadows a cool blue-green. Both faces of a card light with the same
  authored normal.
- **Budgets:** section 6; one draw per species and LOD for the whole world
  (thin instances); a grounds tile with its vegetation stays under the
  per-cell 150,000 triangles (the grounds lint checks it).
- **Wind:** a gentle sway in the vertex shader (bend growing with height,
  phased per planting, a small leaf flutter).
- **The forest beyond the grounds** is the same species as impostors over a
  dark, smooth inner mass, kept 3 m from the castle's plan rectangles (the
  grounds lint checks it).
