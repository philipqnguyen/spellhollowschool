# Garment shapes and designs

Clothing is made from a small set of **fitted shapes** plus any number of
**designs**, instead of one hand-fitted model per catalog item. The build
composes shape + design into the per-item files the client loads
(`gear/<slot>/<item>_<body>.glb`: one material, atlas UVs, the `COLOR_0` dye
mask, LOD0-LOD3, `_SHADE` and `_OUTLINE`, a manifest entry; see
[`base-body-spec.md`](base-body-spec.md) and
[`character-adoption.md`](character-adoption.md)).

> Code comments cite this document's sections by number ("garment-shapes.md section 3.1").
> When you renumber or move a section, update the citations too
> (`git grep garment-shapes.md`).

- Data: `data/items/garment_shapes.json`, `data/items/garment_designs.json`
  (the catalog itself is `data/items/catalog.json`).
- Code (`tools/blender/characters/charlib`): `fit.py` (what a body hands
  over, landmarks, charts, clearance), `shapes.py` (shape builders),
  `designs.py` (painting, composition, export), `addons.py` (trims and
  add-ons), `layers.py` (covered faces), `drape.py` (the sitting drape),
  `shade_bake.py` (the shade layer).
- Tools (`tools/blender/characters`): `check_garment_shapes.py`,
  `check_garment_layers.py`, `check_garment_poke.py`, `check_leg_pass.py`
  (section 10), `render_garment_shapes.py` (review sheets),
  `refit_garment_shapes.py` (refit everything onto any saved body file);
  `tests/test_garment_shapes.py` (data consistency).

The character build composes every catalog item from its design
(`build_characters.py --gear-source shapes`, the default):

```sh
blender -b --factory-startup -t 2 --python tools/blender/characters/build_characters.py -- \
    --assets client/public/assets --art art [--only gear] [--items <asset keys>] [--shape-report report.json]
```

Every shape and design is built and clipping-checked on both bodies
(`body_a` Male, `body_b` Female). Whether a body may *wear* an item is a
catalog rule (`wear`), not a fitting limit.

## 1. Why

Fitting is the expensive, body-specific part of a garment: clearance from
the skin, weights, edge loops at the joints, clearance in every pose.
Pattern, colour layout, trim and small details are not body-specific. With
shapes:

- a **new design** needs no fitting: it paints and decorates a fitted shape;
- a **new body** needs only the shapes refitted, which is automatic because
  fitting reads landmarks and surfaces, not topology (section 6);
- **Transfiguration** restyles between designs on one shape (section 5).

## 2. Shapes

| Slot | Shape | Silhouette | Options (chosen per design) |
| --- | --- | --- | --- |
| body | `top_fitted` | shirts, blouses, fitted tops; a waistcoat painted over a shirt | `neckline` crew / stand / v / open; `sleeve` wrist / elbow / short; `insert` |
| body | `top_knit` | jumpers, sweaters, cardigans over a shirt | `neckline` crew / v / turtle; `sleeve`; `insert` (shirt front in the V) |
| body | `coat_long` | robes, long coats, cloaks, jackets, with a lined skirt | `front` open / closed; `sleeve` wrist / bell; `length` hip / mid-calf / shin; nested `insert`s; `under_sleeve` |
| body | `cloak` | sleeveless cape from a shoulder yoke | planned, not built |
| legs | `trousers_fitted` | straight trousers | `cut` (straight, tapered) |
| legs | `trousers_wide` | wide-leg trousers released below the thigh | - |
| legs | `skirt_short` | knee-length skirt over opaque tights | `pleated` |
| legs | `skirt_long` | midi skirt over tights | `pleated` |
| shoes | `shoe_low` | low shoes, a capsule round the foot | `cut` oxford / low |
| shoes | `boot` | the shoe capsule with a shaft up the leg | `height` ankle / calf / tall |
| hat | `cap_knit` | soft knitted cap fitted round the head | `fit` (close, slouch) |
| hat | `hood` | raised hood round the face to the neck | `cut` deep / cowl |
| hat | `hat_rigid` | stiff hats: only the band is fitted; crown and brim are add-ons sized in head units | `band_style` full / thin / none |

`hat_rigid` with band `thin` or `none` also carries hair trims (ribbon bows,
combs), which hide nothing. Option values can override shape parameters
through the generic `variants` mechanism (per option value, parameter
overrides merged into the shape's parameters).

## 3. What a shape owns

A shape is one builder (`shapes.BUILDERS`) plus its parameters in
`garment_shapes.json`. Fitting it to a body produces a `FittedShape`:

- **Fitted mesh:** its own topology, a grid in the shape's chart space
  (columns round the trunk, a limb, the foot or the head; rows at measured
  landmarks), so the grid has the same vertex and face counts on every body.
- **Weights:** each fitted vertex takes the barycentric blend of the bone
  weights of the body triangle it was fitted to (top 4, normalised).
  Hanging parts use explicit rules: skirts blend from their fitted loop's
  weights toward the thighs (`shapes._skirt_weights`, with back, front,
  lateral and shin follow parameters), bell sleeves take the arm chart's
  weights, stiff hats are rigid on the head bone; leg tubes drop the other
  leg's bones (`_own_side_weights`).
- **Arm holes:** the trunk chart never lands on an arm. A trunk ray that
  leaves the body through the shoulder junction takes the trunk point
  closest to it (the junction's rim, `shapes._trunk_fit`) instead of the
  arm's skin, and each arm hole is centred on the columns whose rays leave
  that way (`shapes._hole_cols`, measured per body, mirrored, at most two
  columns behind the side). The loop is not a cross-section of the arm (its
  armpit and back corners lie 4-10 cm along it), so each sleeve column's
  first rings start past its own loop vertex by `sleeve_step` per ring.
  Without these the tops grew flat flaps behind the shoulders that swung
  with the arm. The band within three edges of each arm hole loop is then
  relaxed in shape and weights (`shapes._seam_weights`), so the faces
  between the trunk (body weights) and the sleeve (arm weights) do not flip
  when the arm rises or swings forward (a dark diamond on the back of the
  shoulder in the cast). The trunk rows from the hole's top row up (the
  shoulder top and the trapezius) stay out of that band and keep the
  body's weights: with a share of the upper arm they sank into the
  trapezius as the arm rose, and the skin showed beside the collar.
- **Hanging skirt weights:** a shell skirt's hanging rings take the weights
  of the skin nearest each vertex, which jump between the buttocks, thighs
  and calves where an outer garment's skirt hangs a hand's width off the
  legs; on robes, cloaks and coats they are averaged over the skirt's grid
  (`SKIRT_SMOOTH_PASSES`, `skirt.smooth`), so neighbouring vertices follow
  nearly the same bones and the skirt shears less in the walk and run (its
  lining showed through in red squares). The legs-layer skirts keep their
  own weights: smoothed, they stood through the top over them in the floor
  sit.
- **Clearance:** after fitting, every vertex and face is pushed out until it
  clears the skin by `min_clear` (`fit.enforce_clearance`,
  `fit.enforce_face_clearance`, along both the smooth and the face normal).
  A vertex that lies beside the nearest skin rather than in front of or
  behind it (its nearest point is on the rim where the regions checked end,
  as a sole's is at the ankle when the feet are hidden) is not pushed.
  Layers nest by offset: inner tops 5-12 mm, knit tops 8-15 mm, robes and
  coats 11-17 mm (chest to hip); legs 3-11 mm (4 mm at the waist). Hanging
  skirts of outer garments also keep an **envelope** (`skirt.envelope` on
  `coat_long`), so a robe or cloak always hangs outside any skirt worn under
  it. Trouser legs keep **boot room** the same way (`boot_room` on the
  trouser shapes, `shapes._boot_room`): a boot shaft stands 7-12 mm off the
  calf to clear a skirt's tights, and below the knee each leg vertex closer
  to the calf than the room moves out by its own shortfall, evened round
  the ring and fading over the rings above and below. Up the shaft the room
  (9-14 mm) is the cover under which the client hides the shaft (1.25 times
  the cover plus 2 mm), not a clearance over it; only over the tall boot's
  rolled top, just under the knee, where a bent knee leaves the back of the
  leg uncovered, is it a clearance (19 mm). So the legs keep their
  straight, tapered or wide line with any shoes (a larger room carried down
  to the hem flared every leg into a bell). `boot_room_inner`, per body, can scale it on the side facing the
  other leg. Boot paint bands (`strap`, `shaft_band`)
  are placed by the shaft face's height fraction `t`, not by ring number,
  so they keep their place whatever the ring count. The shirt sleeve under
  a bell sleeve is nested inside the bell or sleeve over it (as inserts are
  inside the torso), with room for the bell's lining. All of it is in
  landmark heights and garment-relative distances, so it refits with the
  shape.
- **Named loops** (ordered vertex rings or arcs) that trims grow from:
  `hem`, `neckline`, `cuff.L/R`, `armhole.L/R`, `waist`, `collar.L/R`,
  `opening.L/R`, `band`, `skirt_hem`, `tights_waist`, `tights_hem.L/R`,
  `bell_root.L/R`, `insert_neck`, ... Each knows which way trims grow.
- **Guides:** fitted rings that are not part of the mesh, for trims that
  rise above a loop (the waistband rises along a ring 40 mm above the waist
  loop, so it hugs the body).
- **Lines and anchors** that add-ons attach to: lines `centre_front`,
  `front_edge.L/R`, `fly`, `insert_centre`; anchors `chest.L/R`,
  `back_neck`, `hip.L/R`, `instep.L/R`, `toe.L/R`, `crown`, `band_front`, ...
- **Zones** (named dye and atlas regions): predicates over the shape
  coordinates every face carries (`part`, `layer`, row/col or ring/col,
  azimuth, `front`, ...), for example `placket`, `crease`, `fly`, `laces`,
  `vamp`, `toe_cap`, `skirt`, `tights`, `yoke`, `strap`, `scales`. Designs
  paint by zone and coordinates, never by body topology.
- **Hides:** the body regions it covers, derived from its options (an elbow
  sleeve does not hide the forearms).
- **Budget:** the slot's LOD0 budget (hat 2,000, body 6,000, long body
  8,000, legs 3,000, shoes 1,500 triangles); a shape is laid out so its
  heaviest design stays under it without decimation. LOD1-LOD3 come from the
  shared decimation at the tier budgets of
  [`base-body-spec.md`](base-body-spec.md), section 6.

## 4. What a design owns

A design is data only (`garment_designs.json` `designs`, keyed by catalog
asset key):

```json
"gear/body/shirt_oxford_tie_a": {
  "shape": "top_fitted",
  "options": {"neckline": "stand", "sleeve": "wrist"},
  "base": ["dye", "primary"],
  "paint": [{"zone": "placket", "paint": ["dye_stitch", "primary"]}],
  "trims": [{"type": "turned_hem", "at": "hem"}, {"type": "cuff", "at": "cuff.*"}],
  "addons": [{"type": "collar_leaf", "at": "neckline"},
             {"type": "tie", "at": "centre_front", "stripes": [["dye", "secondary"], ["dye", "trim"]]}]
}
```

- **Shape and options:** which fitted shape and which of its options.
- **Paint:** `base` and `paint` layers. Each picks a zone (plus optional
  coordinate ranges in `where`) and a flat paint or a pattern: `stripes`,
  `ribs`, `check`, `tartan` (a woven sett), `cable`. A paint is
  `[atlas swatch, dye channel]`: the swatch is a cell of the shared atlas
  (`dye`, `dye_shade`, `dye_stitch`, `shirt_white`, `gold`, ...), the channel
  writes `COLOR_0` (primary / secondary / trim / none). Folds read as
  painted shade bands, not geometry.
- **Relief** (optional): outward-only surface relief (cable knit), so it
  cannot break the clearance.
- **Trims** grown from the shape's loops: `turned_hem`, `rib_band`, `cuff`,
  `rolled_cuff`, `waistband`, `hem_fold`, `turn_up`, `shoe_collar`,
  `fold_cuff`, `sole`, `ruffle`, `roll` (fur edge). Neck bands and neck
  frills lie on the skin (`hug`) and take the neck's weights toward their
  top, so necklines sit on the neck when the head turns.
- **Add-ons** at anchors, lines and loops: `collar_leaf`, `open_collar`,
  `lapels`, `tie`, `v_stripes`, `buttons` (with parallel `columns` for
  double-breasted), `pompom`, `crest`, `hood`, `cape`, `bow`, `comb`,
  `earflaps`, and for stiff hats `crown` (height, taper, bulge, bend, end
  radius) and `brim` (profile, waves). Add-ons that lie on the garment
  stay outside it: a `cape` rests on the coat's outer shell and every
  vertex and cell centre keeps 3/4 of its gap off it along the shell's
  outward normal (it showed the coat's front through its band over the
  bust otherwise). The pull onto the coat and the push off it are both
  averaged over the cape's grid before they apply: moved vertex by vertex
  along the shell's normals, the cape's cells crumpled and its lining
  showed through in patches. A `hood` floats over the coat and over a cape
  added before it, so its lining does not cross the cape; only over the
  cape (floated over a shirt collar, stripes or a tie near the neckline,
  its side panels stepped out and showed a wide jagged lining rim). The
  hood grows from one run of the neckline loop, the centre-back arc in
  loop order: a neckline that bulges out over the shoulders and comes back
  in at the side of the neck has points there that are also behind the
  neck, and sorted in with the arc by x they folded the hood's side
  columns forward over the shoulder tops, where they crossed the cape in
  thin lining-coloured strokes.
- **Lining:** `thicken` lists zones that get a thickness with a lining
  colour inside (robe skirts, bell sleeves, skirts).
- **Folds:** `"folds"` adds or removes shade-layer fold bands.
- **Extra hides:** a design may hide extra regions (an earflap hat hides the
  ears).
- `extends` copies another design and overrides keys (the four house robes
  are one design; their colours come from the catalog dyes).
- A design's catalog item is the one whose `asset` is its key; its dyes come
  from the catalog. `preview_dyes` marks a design not in the catalog yet.

Trims and add-ons work only from the shape's vertices (positions, normals,
weights), so they never need fitting either. A build fits each (shape,
options) once per body and clones it for every design that uses it.

## 5. Restyle families (Transfiguration)

`families` in `garment_designs.json` groups designs that share a shape:
`shirts_collared`, `knit_tops`, `trousers`, `wide_trousers`, `skirts`,
`long_skirts`, `low_shoes`, `boots`, `knit_headwear`, `stiff_hats`, `hoods`,
`hair_trims`, `school_robes`, `dress_robes`, `cloaks`. Transfiguration may
restyle an item to any design in its family, because silhouette, hides,
layering and fit are identical; only paint, trims and add-ons change.
Designs on different options of one shape (a V-neck and a crew-neck jumper)
can restyle into each other; designs on different shapes cannot.

## 6. Fitting (body-agnostic)

Fitting never reads the body's topology or how the body was generated. A
body only has to hand over what any rigged mesh has:

1. its rendered LOD0 mesh objects, one per region (the twelve region names
   of [`base-body-spec.md`](base-body-spec.md), section 4), carrying bone
   vertex groups;
2. the skeleton: rest-pose heads and tails of the deform bones, by name.

`designs.frame_for` reads exactly that back from the built body, so the
shapes are fitted to the very surface the client draws. On it,
`fit.BodyFrame`:

- **orients regions** away from the skeleton (each face votes; a region
  that mostly points inward is flipped as a whole; the eye and jaw bones do
  not vote);
- **measures landmarks** with ray casts: torso centre line, armpit height,
  crotch, waist, hip, chest line, neckline, the neck's own centre line, head
  box and scale, foot extents; knee and ankle come from the skeleton;
- offers **charts**: rays defined from the skeleton and landmarks, not from
  vertices (trunk rays from the torso centre line, fanned over the
  shoulders; limb rays round the bone polyline; head rays from the head
  centre; shoe rays from a capsule inside the foot). Collar stands rise over
  their own neckline vertex about the neck's centre line.

A shape vertex is one chart sample: a ray cast against the regions that
part of the garment covers, plus the shape's offset. Rows sit at landmark
heights with a fixed count per span, so the topology is the same on every
body and only positions and weights change. Metre values in the shape data
are written for the 1.78 m reference body and scaled by the measured height;
other sizes are fractions of measured features (shoe radii of the foot
width, hems and boot tops between two landmarks, hat sizes in head units).

Two rules make the result independent of surface detail:

- **cloth bridges concavities** (`shapes._bridge_ring`): on each trunk row,
  a vertex well inside the running median radius of its neighbours is
  lifted to it, so a gluteal cleft or spine groove is spanned, not followed;
- **trims and add-ons keep the clearance too** (`designs.compose`): after a
  design adds collars, cuffs and hems, their vertices are pushed out of any
  skin the item leaves visible.

**A new body:** build it, give its faces the twelve region names, skin it to
the skeleton; `--gear-source shapes` refits every shape and every design
follows. Nothing in the shapes or designs is edited.
`refit_garment_shapes.py` does the same on a saved body file without
running the body generator:

```sh
blender -b --factory-startup -t 2 --python tools/blender/characters/refit_garment_shapes.py -- \
    --blend art/characters/body_b.blend --out <dir> --render
```

## 7. Layers, covered faces and the sitting drape

- **Covered faces** (`charlib/layers.py`, client `assemble.ts`): a character
  wears at most one item per slot, layered shoes < legs < body. Every
  garment file records, per vertex, the body vertex it lies over (`_BODYV`)
  and its height over the skin (`_BODYH`), and per file how far out it
  covers each body vertex (`extras.covers`). A lower layer's faces that a
  higher layer covers in every pose are not drawn, so they cannot break
  through it.
- **Sitting drape** (`charlib/drape.py`): a garment with a hanging skirt
  carries one corrective shape key per seated clip, baked by settling the
  skirt like hanging cloth (it lies on the lap, falls over the knees and
  hangs down the shins); the client turns it on with the seated clips
  (`toonDrape`).

## 8. When a new silhouette justifies a new shape

Make a new **design** when only colour layout, pattern, trims or add-ons
change. Add a shape **option** when the change is a variation of an
existing fit (neckline, sleeve length, shoe cut, boot height, coat length,
inner insert) that the same builder fits with different parameters. Add a
new **shape** only when:

- it covers or hides different body regions (sleeveless, cropped, skirt vs
  trousers);
- it needs a different layer (inner vs outer vs head);
- it hangs or deforms differently (free skirts and capes, bell sleeves,
  stiff hats on the head bone);
- or its triangle budget or LOD behaviour differs substantially.

Each new shape must be fitted, weighted and clipping-checked on both bodies,
at every height and in the test poses, before designs go on it.

## 9. Adding a clothing item

1. Add the item to `data/items/catalog.json` (a new `id`, kind `clothing`,
   `slot`, `dyes`, `price`, `exchange` category, `asset` key such as
   `gear/body/<name>_a`, optional `family` and `wear`).
2. Add a design under the same key in `garment_designs.json`: pick the shape
   and options, paint zones, add trims and add-ons. Add it to a family if it
   should be restylable.
3. Build it: `build_characters.py -- --items gear/body/<name>_a` limits the
   build to that asset (see the script's header for `--only`); render a
   review sheet with `render_garment_shapes.py`.
4. Run the checks of section 10 and `tests/test_garment_shapes.py`.

## 10. Checks

- `check_garment_shapes.py --poses full [--heights min,default,max]`: skin
  clipping (vertices more than 2 mm inside visible skin) and layering (inner
  vertices outside an outer item that covers them) in the rest pose and
  frames of walk (both strides), run, wave, bow and sit, on both bodies.
- `check_garment_layers.py`: an inner garment breaking through the one worn
  over it, per frame of the shipped clips, with covered faces hidden as the
  client hides them.
- `check_garment_poke.py`: legs breaking through skirts, kilts, robes and
  long coats, per frame (`--balloon` also reports skirts ballooning).
- `check_leg_pass.py`: legs and leg garments passing through each other in
  walk and run.
- `check_characters.py --poses full`: every item against visible skin.
- `tests/test_garment_shapes.py`, `tests/test_garment_necklines.py`
  (necklines sit on the neck, also with the head turned to the look limit).

Known limits: there is no cloth simulation, so hanging skirts can still meet
a raised thigh in run and sit; the checks report it per pose.
