# Character data, shading and LODs

How a player character goes from data to pixels: the appearance data, the
per-vertex layers the Blender build bakes, the hair file format, the toon
shading rows, LOD selection and shadows. Bodies and the skeleton are in
[`base-body-spec.md`](base-body-spec.md), clothing in
[`garment-shapes.md`](garment-shapes.md), the hair pipeline, faces, outlines
and anti-aliasing in [`character-look.md`](character-look.md).

> Code comments cite this document's sections by number ("character-adoption.md section 3.1").
> When you renumber or move a section, update the citations too
> (`git grep character-adoption.md`).

Targets: **60 fps, at most 1,000 draw calls, at most 1.5 GB GPU memory, up to
100 characters on screen, one draw per character, no per-frame
allocations.** Low stays the minimum-spec fallback (no shadow maps, no
outlines).

## 1. Appearance (`data/characters/creator.json`)

```jsonc
{
  "version": 2,
  "body_types": [
    { "index": 0, "id": "body_a", "name": "Male", "asset": "characters/body_a",
      "height_m": [1.60, 1.88], "default_height_m": 1.78, "default_hair_style": 14, "wear": "male" },
    { "index": 1, "id": "body_b", "name": "Female", "asset": "characters/body_b",
      "height_m": [1.52, 1.80], "default_height_m": 1.68, "default_hair_style": 17, "wear": "female" }
  ],
  "sliders": [ height, face_jaw, face_cheek, face_nose, face_brow, face_eyes ],
  "skin_tones": [ 10: Porcelain ... Mocha ],
  "hair_styles": [
    { "id": 14, "name": "Short Tousled", "asset": "hair/short_tousled", "body": "body_a" },
    // retired ids keep an entry with no asset, mapped to a style of each body
    { "id": 0, "name": "Short Crop", "retired": true, "migrate_to": { "body_a": 14, "body_b": 19 } }
  ],
  "hair_colours": [ 12 natural + 8 with "fantasy": true ],
  "eye_colours": [ 7 natural + 6 with "fantasy": true ],
  "default_eye_colour": 1
}
```

Rules the catalog validation enforces (sim-core `Creator::validate`,
mirrored by the client's content tests):

- Indices equal positions; entries referred to by index (body types, skin
  tones, hair colours, eye colours) are only appended once the game is live.
- Sliders cover `height` (no shape keys: height is a uniform scale) and the
  face bytes 0-4 (one or two shape keys each), each exactly once.
- A shipped hair style has an `asset` and a `body`; a retired one has
  `"retired": true`, no `asset`, and `migrate_to` naming a shipped style of
  each body. Ids are unique and never reused. Each body's
  `default_hair_style` is a shipped style of that body.
- `default_eye_colour` is a valid index; swatches are valid `#RRGGBB`.

**Height mapping:** byte 0 / 128 / 255 = the body's minimum / default /
maximum height, piecewise linear. The client scales the character's root
uniformly (`content/appearance.ts` `heightScale`); the camera target, the
first-person eye, nameplates, the chat bubble and the blob shadow follow the
scale. The server's collision capsule is the same for everyone.

**Shipped hair styles** (one body each):

| Id | Body | Name | Asset |
| --- | --- | --- | --- |
| 14 | Male | Short Tousled | `hair/short_tousled` |
| 15 | Male | Side-Swept Fringe | `hair/side_swept` |
| 16 | Male | Shaggy Layers | `hair/shaggy_layers` |
| 20 | Male | Swept-Back Undercut | `hair/swept_undercut` |
| 21 | Male | Tied-Back Ponytail | `hair/low_tail` |
| 22 | Male | Crew Cut | `hair/crew_cut` |
| 17 | Female | Long with Bangs | `hair/long_bangs` |
| 18 | Female | High Ponytail | `hair/ponytail_locks` |
| 19 | Female | Bob with Front Locks | `hair/bob_locks` |
| 23 | Female | Twin Tails | `hair/twin_tails` |
| 24 | Female | Low Side Braid | `hair/side_braid` |

The creator UI shows a Height slider (metres and feet/inches), hair styles
filtered to the chosen body, and hair and eye colour swatch rows grouped into
natural and fantasy colours.

## 2. Files and the manifest

The character build writes `client/public/assets/characters/`:

- `body_<a|b>.glb`: the body (four LODs, face and expression keys, all clips);
- `face_<body>.png`, `face_mask_<body>.png`: the face texture pair;
- `hair/<asset>_<body>.glb` (and `hair/<asset>_cap_<body>.glb` only for
  styles whose tails change under a hat);
- `gear/<slot>/<item>_<body>.glb`: one file per catalog item and body;
- `manifest.json`: per asset its files, triangle counts per LOD, bounds,
  `hides`, tint channels (`tint.body` = skin tone, hair colour, eye colour),
  the `lods` block (tiers and per-part budgets), the `shade` block, and for
  hair `body`, `sway` and `hat_fit`.

## 3. The shade layer (`_SHADE`)

Characters carry a baked greyscale shade layer so they read as solid forms,
not flat swatches. **Format: a per-vertex attribute, not a texture.** Every
LOD of every character asset exports a custom glTF vertex attribute
**`_SHADE`** (VEC4; float, normalized unsigned byte or unsigned short; the
client packs it into 4 normalized bytes). Bodies and garments are baked by
`charlib/shade_bake.py`, hair by `charlib/hair.py` / `hair_meshy.py`.

| Channel | Body and garments | Hair |
| --- | --- | --- |
| x: shade | 1 open, 0.35 deepest crease: ambient occlusion in creases + soft painted fold bands + a gentle top-to-bottom gradient. **Faces: 1** | occlusion and darkening toward the roots; the inner layer at most 0.45 |
| y | 0 | root-to-tip coordinate (the client replaces it with the ring elevation, section 5) |
| z: band phase | 0.5 | per-lock offset of the highlight band (0.5 neutral, about ±0.03); 0 on inner surfaces (no ring) |
| w | SSAO class hint (1 on silhouettes, about 0.5 in creases) | about 0.45 |

Why per vertex: zero texture memory, no new sampler, no change to the shared
atlas; dyes still tint it, so every colour stays choosable. Cost: 4 bytes
per vertex.

Bake (bodies and garments): in the rest pose with the asset fitted on its
body (garments with the layers they are worn over), a Cycles AO bake to a
colour attribute (body and other layers as occluders), smoothed; times the
garment shape's fold bands (`shade_bake.DEFAULT_FOLDS`: elbow, wrist, under
the chest, waist sides, skirt folds, knee, ankle; a design may add or remove
bands with `"folds"`); times a height gradient; then
`x = clamp(ao^0.8 * folds * gradient, 0.35, 1)`, faces forced to 1.
LOD1-3 take the layer from LOD0 by nearest surface point.

## 4. Hair asset format

### 4.1 Files

- Per style, only for its own body: `hair/<asset>_<body>.glb`. One file holds
  LOD0-LOD3, one mesh object per LOD named `hair_<asset name>_lod<n>`, with
  `toon: "hair"` (and no `region`), skinned to the body skeleton plus the
  style's sway bones. One material (`character`); every hair vertex samples
  the atlas's near-white `hair` cell and takes the hair colour from the dye
  mask (`COLOR_0` R).
- The hair's inner scalp cap is marked by the `_SCALP` vertex attribute
  (1 on the cap), so the client knows what to keep under a hat.
- The manifest entry lists `files`, `cap_files` (only styles that need one),
  triangles per LOD, `bounds`, `body`, `sway` and `hat_fit`.

### 4.2 Sway bones

- Up to 12 bones per style in fixed chains: `hair.back.01`-`04` (ponytail,
  long back hair), `hair.side.L.01`-`03`, `hair.side.R.01`-`03` (side
  locks, long front locks, a plait) and `hair.front.01`-`02` (long fringe
  pieces). Each chain's first bone is a child of `DEF-spine.006`. Rest
  transforms are per style (in the hair file's skin); the body skeleton does
  not change.
- Weights: hair near the scalp is 100% head; down a chain, smooth weights to
  at most 2 sway bones plus the head (4 influences max). Long hair lying on
  the chest and back can also follow the torso bones (`hair_meshy.body_follow`).
- **Client sway** (`client/src/game/characters/sway.ts`): the client
  appends the sway joints to the rig and, for characters at LOD0 and LOD1,
  swings each chain on one damped spring (pitch and roll, about 2.5 Hz,
  damping 0.6) driven by the head's acceleration and turn rate, with gravity
  and a slow idle sine. Limits: back chains 25 degrees back, 15 forward and
  15 sideways; side and front chains 12 degrees. No allocations per frame.

### 4.3 Fringe shadow

Each style's LOD0 mesh carries `extras.fringe_hem` (the fringe's lower edge
seen from the front: 8-24 points `[x, y, z]` in the glTF rest frame, sorted
by x) and `extras.fringe_shadow = { "depth_m", "soft_m", "strength" }`. At
assembly the client darkens `_SHADE.x` on the forward-facing face vertices
below the hem's smoothed line (`parts.ts` `shadowLine`), so one soft band
falls under the bangs; the `face` row's shadow band renders it. Recomputed
only when the look changes. Styles without a fringe carry a single
placeholder point.

### 4.4 Hats over hair

Every style fits under every hat that hides the scalp, with the hat touching
the full hair. A style is worn under hats **in its own file**; only where its
tails must change does it ship a `_cap` file (`hair_meshy.cap_needed`:
`ponytail_locks`, whose high tie drops to the nape, and `twin_tails`, whose
roots press under the band). The hair build (`hair_meshy.hat_fits`) measures
every hat and writes, per body, into the hair's manifest entry:

```
hat_fit: { body_x: { centre: [x, y, z], cover_step, cut_margin, limit_rows: [...],
           hats: { "gear/hat/...": { scale, cover: [...], limit?: [[...] per row] } } } }
```

- `cover`: per azimuth, how far down the hat's crown reaches round the head
  (rays from the head centre; a brim's underside does not count).
- `scale`: the smallest scale about `centre` (at least 1, at most
  `HAT_FIT_MAX` 1.15) at which the hat clears the hair under its crown.
  Where the hair needs more, `limit` records how far from the centre the
  hair may reach, and the client presses it in to that.
- The client (`characters/hatfit.ts`, once per look and LOD, never per
  frame) drops the hair triangles well inside the cover line (the inner cap
  stays), presses the rest under the crown where there is a `limit`, and
  scales the hat. Hoods take the same rule and are never scaled. Hair trims
  (bows, combs) hide nothing and need no fit.
- Hats are fixed in size; when a hat is rebuilt, rebuild the hair after it
  (`build_hair.py`), or `check_hair_hats.py` fails.

## 5. Character shading

Inputs per vertex (merged mesh, **one draw per character**): position,
normal, atlas UV, `COLOR_0` (tinted albedo; alpha = `region * 16 + finish *
4`, `toonAlpha`), joints, weights, `toonShade` (from `_SHADE`) and the
outline layer.

**Regions** (`characters/assemble.ts` `vertexRegion`): `skin`, `face` (the
skin of the body's head primitive), `hair`, `primary`, `secondary`, `trim`
(the dye channels), `metal`, `flat` (painted features: always lit) and
`neutral` (undyed gear). Body mask R = skin, G = brows (hair), B = iris (eye
colour, flat).

The region rows live in `data/art/toon_palette.json` under `characters`
(fields of the world rows in [`style-guide.md`](style-guide.md), section 11,
plus these):

| Field | Meaning |
| --- | --- |
| `shadeShift` | `t = threshold + (1 - x) * shadeShift`: creases fall into the tinted shadow band instead of going grey |
| `aoFloor` | ambient kept in the deepest crease: `ambient *= mix(aoFloor, 1, x)` |
| `skyLift` | `fill *= 1 + skyLift * n.y` |
| `rimWidth` | rim on both bands, brighter on the lit side; part of it is added over dark albedo so black robes keep an edge |
| `band`, `bandCentre`, `bandWidth` (hair) | the angel ring: a lighter tone of the hair colour mixed in at one elevation round the head |
| `faceSdfSoftness`, `faceLineSkin`, `faceLineFloor`, `faceIrisGlow` (face) | the face-shadow map and the face paint rules ([`character-look.md`](character-look.md), section 3) |

- **Shadows are warm on skin and face** (a light peach-rose), **cool on
  cloth and hair**; metal neutral.
- **Faces stay clean:** the `face` region's shade is 1 everywhere except the
  fringe shadow; it takes no shadow-map shadow and only a faint rim. At
  Medium and High its light comes from the face-shadow map.
- **The angel ring** is one continuous band: at assembly each hair vertex's
  `_SHADE.y` becomes its elevation round the head's centre (`assemble.ts`
  `ringY`), so the band sits at one height on every lock. Its colour keeps
  the hair's hue (`toon/material.ts` `hairBandColour`): dark hair gets a
  clear ring, light hair a subtle one.
- **Iris:** the iris is greyscale paint tinted by the eye colour; no shader
  work beyond the face texture.
- **Measurement switch:** `?charfx=off`, or for one feature at a time
  `?charfx=-shade,-shadows,-blobs,-sway,-lod0,-lod` (`characters/fx-flag.ts`).

## 6. LOD selection

Tiers and per-part budgets: [`base-body-spec.md`](base-body-spec.md),
section 6. The client (`client/src/game/characters/lod.ts`) ranks
characters by distance to the camera (your own character is rank 0) and
re-evaluates at 4 Hz, not every frame:

| Quality | LOD0 | LOD1 | LOD2 | LOD3 |
| --- | --- | --- | --- | --- |
| High | rank <= 8 and <= 12 m | <= 25 m | beyond | budget only |
| Medium | rank <= 8 and <= 10 m | <= 20 m | beyond | budget only |
| Low | rank <= 2 and <= 6 m | <= 12 m | <= 25 m | beyond |

- **Budget governor:** if the selected characters' triangles exceed the
  quality's share of the triangles-in-view budget, the farthest characters
  are demoted one tier at a time until it fits, never below
  `CHARACTER_FLOOR` (the nearest three at LOD0); your own character never
  leaves LOD0. Hysteresis of 1 m and one rank before promoting again.
- A LOD change re-assembles that character's merged mesh (cached per
  appearance version and tier, LRU, at most 2 assemblies per frame);
  assembly allocates, frames do not. Sway runs only at LOD0-1.

## 7. Shadows

- **Medium and High** (`client/src/game/toon/char-shadows.ts`): a shadow map
  of the characters nearest to *you* (8 at Medium on a 1024 px map, 12 at
  High on 2048 px with softer filtering), from a shadow-only light along the
  sun or moon outdoors, or a fixed steep key above and in front indoors. It
  falls on the floors around them and on the characters themselves (body,
  garments, hair; never the face) and feeds the toon step, so a cast shadow
  is the same tinted band as a form shadow. One extra draw per caster.
- **Everyone else, and everyone at Low** (`characters/blobs.ts`): a soft
  round blob under each character, one thin-instanced quad for all of them
  (one draw), 0.9 m x 0.6 m at default height (scaled with height), alpha
  0.35, fading as the feet leave the floor. Characters with a real shadow
  drop their blob.

## 8. Checks

Before changing character assets, run on the shipped build
(`art/characters/<body>.blend`):

- `check_characters.py --poses full`, `check_garment_shapes.py --poses full`,
  `check_hair.py`, `check_hair_hats.py`, `check_hair_see_through.py`,
  `check_seating.py` (all under `tools/blender/characters/`);
- the Blender tests (`tools/blender/characters/tests/`, see
  [`tools/blender/README.md`](../../tools/blender/README.md));
- the client's vitest and typecheck, and `scripts/check.sh`;
- in-game comparisons at Low, Medium and High: the creator
  (`client/creator-preview.html` mounts it without a server;
  `window.previewStats()` reports draw calls), a close-up of two
  characters, a group of 20 and a class.
