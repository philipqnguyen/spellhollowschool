# Character look: hair, faces, outlines and render quality

How the characters' hair and faces are made, how their outlines are drawn
and how the client keeps edges clean. The data formats these build on
(`_SHADE`, the hair file, hats, shading rows) are in
[`character-adoption.md`](character-adoption.md); bodies in
[`base-body-spec.md`](base-body-spec.md).

> Code comments cite this document's sections by number ("character-look.md section 3.1").
> When you renumber or move a section, update the citations too
> (`git grep character-look.md`).

Throughout: characters are adults and never sexualised; colours stay
choosable (skin tone, hair colour, eye colour and garment dyes tint through
masks, baked shading stays greyscale); one draw per character; no per-frame
allocations.

## 1. Overview

| Part | Made by | Drawn by |
| --- | --- | --- |
| Hair | meshes derived from Meshy-generated models, cleaned, fitted and given an inner layer at build time (`charlib/hair_meshy.py`) | the character material, the angel ring, sway |
| Faces | a script-painted face texture and mask per body (`charlib/face_paint.py`) plus a face-shadow threshold map (`charlib/face_shadow.py`) | the character material's face path (`toon/material.ts`) |
| Outlines | a baked smoothed-normal layer `_OUTLINE` (`charlib/outline_bake.py`) | an inverted hull per near character (`toon/outline.ts`) |
| Clean edges | - | render scale and 4x MSAA per quality (`client/src/settings.ts`, `toon/post.ts`) |

## 2. Hair

### 2.1 Styles and sources

Each style is derived from one Meshy-generated model (a guide). The raw
models live in the gitignored `vendor_assets/meshy/` and are not
distributed; the derived, cleaned **source meshes** are committed at
`art/characters/hair_meshy/<asset>_<body>.npz`, so the build never needs
`vendor_assets`. [`art/CREDITS.md`](../../art/CREDITS.md) credits all 11
styles, naming each generated model and its SHA-256.

| Id | Style (asset) | Body | Length class |
| --- | --- | --- | --- |
| 14 | Short Tousled (`short_tousled`) | `body_a` | short |
| 15 | Side-Swept Fringe (`side_swept`) | `body_a` | short |
| 16 | Shaggy Layers (`shaggy_layers`) | `body_a` | medium |
| 20 | Swept-Back Undercut (`swept_undercut`) | `body_a` | short |
| 21 | Tied-Back Ponytail (`low_tail`) | `body_a` | medium |
| 22 | Crew Cut (`crew_cut`) | `body_a` | short |
| 17 | Long with Bangs (`long_bangs`) | `body_b` | long |
| 18 | High Ponytail (`ponytail_locks`) | `body_b` | long |
| 19 | Bob with Front Locks (`bob_locks`) | `body_b` | medium |
| 23 | Twin Tails (`twin_tails`) | `body_b` | long |
| 24 | Low Side Braid (`side_braid`) | `body_b` | long |

A style may have more than one guide (`hair_meshy.GUIDES`: the file, the
registration, segmentation and shaping parameters); the style's `guide_id`
is the default and `prepare_hair_meshy.py --guide <id>` prepares from
another. Styles 14-19 also have a scripted lock builder as a fallback
(`build_hair.py --source locks`, used automatically when a source mesh is
missing); styles 20-24 are Meshy only (`hair_meshy.MESHY_ONLY`: a missing
source mesh is an error).

### 2.2 Pipeline

Two stages, so the build never reads `vendor_assets`:

1. **Prepare** (`tools/blender/characters/prepare_hair_meshy.py`, run by
   hand): guide -> cleaned, registered, hair-only source mesh in the body's
   rest frame, written to `.out/hair_meshy/<asset>_<body>.npz` and then
   copied to `art/characters/hair_meshy/`. Format (numpy npz): `V` float32
   (n, 3) metres in the glTF rest frame (+Y up, facing +Z), `F` int32 (m, 3),
   `N` float32 (n, 3) smooth shading normals, `part` uint8 (n) (0 crown and
   scalp layer, 1 fringe, 2 sides and front locks, 3 back, 4 ponytail or
   tail, 5 tie), `root_to_tip` float32 (n), and `meta` (JSON: source file and
   SHA-256, the registration, the tool version, the parameters). At most
   60,000 triangles.
2. **Build** (`charlib/hair_meshy.py`, called from `build_characters.py` or
   `build_hair.py` per style): source -> clean-up, LOD0-3, inner layer,
   attributes, weights, hat fits, export in the format of
   [`character-adoption.md`](character-adoption.md), section 4.

**Prepare, in short:**

- Import the guide as glTF only (never open a third-party `.blend` with
  scripts enabled).
- Register it onto our head (`measure_hair_guides.py`'s registration), then
  transfer the hair skull-relative: above the ear line each vertex keeps its
  height over the guide's skull, placed over ours; below it the lengths
  follow a similarity transform, then the hair is pushed out of the body.
- Segment the hair from the fused bust (distance and normal to our body,
  region growing, plus per-style keep and drop boxes).
- Smooth while keeping lock tips sharp and fringe gaps open; give it smooth
  shading normals blended from a proxy of the hair mass (`proxy_share`,
  default 0.6), the tips keeping more of their own.
- Optional per-style tools: `hug` (lay short hair on the scalp with a
  capped height and a pinned hem), `cut_u` (a clean cut line, e.g. an
  undercut's edge), `rest_drape` and `gather` (long hair resting on the
  chest and back instead of flaring), `add_braid` (a built three-strand
  plait, `charlib/hair_braid.py`), `hair_off` / `protrude` segmentation
  boxes.

**Build, in short:**

- **Clean-up** (`clean_source`): style edits (`drop` boxes, `shorten`,
  `move`), flyaways under 1.5 cm² dropped, small holes filled, strands
  narrower than 3 mm widened (tips left pointed).
- **LODs** by quadric decimation with lock tips pinned. Budgets per length
  class (`hair_meshy.BUDGETS`):

  | LOD | Short | Medium | Long |
  | --- | --- | --- | --- |
  | LOD0 | 20,000 | 22,000 | 24,000 |
  | LOD1 | 6,000 | 7,000 | 8,000 |
  | LOD2 | 1,000 | 1,150 | 1,300 |
  | LOD3 | 350 | 400 | 450 |

  The `_cap` variants have the same budgets.
- **Inner layer** on LOD0 and LOD1, so no background ever shows through the
  single-sided shell: the shell's vertices moved in by 2.5 mm
  (`INNER_DEPTH`), tapered to 0 at the open edge, faces flipped, taking the
  shell's shading normals and sway weights; `_SHADE.x` at most 0.45,
  `_SHADE.z` 0 (no ring), no outline. The inner scalp cap gets a flipped copy
  too. LOD2 and LOD3 have none.
- **Attributes:** atlas `hair` cell UVs, `COLOR_0` R = 1 (hair colour),
  `_SHADE` (occlusion and root darkening, root-to-tip, band phase, 0.45),
  custom normals from `N`, `_OUTLINE`, `_SCALP`.
- **Short-hair extras:** `clip` (a clipped band with comb ridges below the
  shell, down to the face texture's painted scalp line), `crease_dark`
  (darker strokes in the shell's grooves), `no_fringe`.
- **Weights and sway:** scalp and crown 100% head; the `part` labels drive
  the sway chains; hair lying on the body can follow the torso
  (`body_follow`).
- **Hats:** the per-hat fits of
  [`character-adoption.md`](character-adoption.md), section 4.4.

**New guides** come from Meshy (image to multi-view to 3D, on a bald
mannequin bust, untextured, not remeshed, GLB). Generating them needs a Meshy
account; the results go in `vendor_assets/meshy/` and only their derived
`.npz` is committed.

### 2.3 Quality bar

- The style's silhouette, volume and lock layout read as the guide's on our
  head from the front, side, back and three-quarter; crisp pointed tips; the
  fringe has gaps showing the forehead; never a smooth helmet.
- No scalp or background shows through from any game camera (eye level of a
  standing player, 1-6 m): `check_hair_see_through.py` reports 0 pixels
  through back faces.
- No visible facets or terminator steps at LOD0 at Medium; the angel ring
  stays one continuous band.
- Clipping: `check_characters.py --poses full` (0 in rest, at most 25 per
  moving pose), `check_hair.py` (skin and under the cloth, same limits, in
  rest, walk, walk_b, run, sit, wave and bow), `check_hair_hats.py` (0 hair
  vertices through any hat in rest, walk and sit).

## 3. Faces

### 3.1 The face texture pair

Each body has its own face texture pair, sampled in the same character
material, so one draw per character stays:

| File | Size, format | Channels |
| --- | --- | --- |
| `characters/face_<body>.png` | 1024 x 1024, RGBA8, sRGB | RGB: the painted face (white or a blush/lip multiplier on skin; features in their own colours, greyscale where tinted); A: skin weight (1 = multiply by the skin tone) |
| `characters/face_mask_<body>.png` | 512 x 512, RGBA8, linear | R: eye-colour weight (iris); G: hair-colour weight (brows, lashes, scalp); B: face-shadow threshold (3.4); A: flat weight up to 192/255, iris glow above it |

About 14 MB of GPU memory for both bodies. The manifest's body entry has
`"face": { "colour": ..., "mask": ..., "size": [1024, 512] }`; the head
primitive carries `extras.face_texture = true`. Trilinear with mips,
anisotropy 4, clamp.

**Albedo on the face** (the `face` region, in the shader):

```
c = texture(faceColour, uv);  m = texture(faceMask, uv)
albedo = c.rgb * (1 + ((skinTone - 1) * c.a + (hairColour - 1) * m.g) / max(1, c.a + m.g))
               * mix(1, eyeColour, m.r)
```

Skin and hair weights blend linearly, so no light halo appears where hair
paint meets dark skin. `skinTone` is the vertex colour; the eye and hair
colour palette indices are written at assembly into the face vertices'
`toonShade.y` and `.z`, and the material holds the creator palettes as
uniform arrays (32 entries; entries past the creator's colours are live
slots that let the Chameleon Tonic recolour one character's brows and
lashes with its hair).

**UVs:** the whole head primitive (every LOD) maps into the face texture,
the face front through the face projection, symmetric about u = 0.5 (the
face-shadow map is mirrored for light from the other side); the rest of the
head in a low-density margin painted as skin. At least 700 texels across
the face's width at LOD0.

### 3.2 Painted by script (`charlib/face_paint.py`)

Painted at 2048² in the face projection and downsampled to 1024², so every
edge is anti-aliased in the texture itself, from the per-body style
(`mh_face.STYLE`, `SCULPT_EYE`):

- **Eye white:** near white with a soft cool shadow under the upper lid.
- **Iris** (greyscale, tinted by the eye colour): a dark rim; darker at the
  top (about 0.40) to light at the bottom (1.0); a light crescent low in the
  iris; a dark pupil; two catchlights (a larger soft oval upper outer, a
  small one lower inner). A glow (mask A above the flat level) is strongest
  in the lower iris and on the catchlights; the shader adds `faceIrisGlow`
  of the albedo unlit, so irises read luminous by day and at night.
- **Lashes:** a thick upper lash line with a small outer wing, tapering
  toward the inner corner (partly hair-tinted); a light lower lash hint.
- **Brows:** clean tapered strokes in the hair colour; thicker and
  straighter on `body_a`.
- **Nose:** a small soft shade on the shadow side of the tip.
- **Mouth:** a short line with softened ends and a faint lip tint.
- **Blush:** soft and subtle; lighter on `body_a`.
- **Scalp:** the head above the hairline (forehead line, temples,
  sideburns, round the ears to the nape) is painted in the hair colour, a
  shade darker, so no skin shows between locks.

**Lines on every skin tone:** the painted lines on the skin (mouth, nose,
lid crease) are multipliers of the skin tone. The shader deepens a line
where the skin is darker than `faceLineSkin` (0.75 luma), so every skin
loses at least the value a light skin loses, never below `faceLineFloor`
of itself; lighter skins keep the paint exactly (`toon/material.ts`
`faceLinePaint`).

### 3.3 Geometry and expressions

Only what the expressions need stays cut into the mesh: the **eye opening
and lash band outlines** (blink and the eye keys collapse the opening onto
the lower lid) and enough vertices at the mouth corners for the mouth keys.
Everything else is texture, so LOD1-LOD3 keep the face. The face has smooth
custom normals from an ellipsoid fitted to the face front.

### 3.4 Face shadow (Medium and High)

Instead of the toon step on the face's normals, the key light's lit share on
the `face` region comes from a threshold map, driven by the light's
direction in head space:

- **Bake** (`charlib/face_shadow.py`, into the mask's B): for the key light
  swept round the head's up axis from in front (0) to behind (180 degrees),
  each texel stores the light angle / 180 at which it goes dark. Monotonic,
  smooth, symmetric layout.
- **Shader** (`TOON_FACE_SDF`): the vertex shader computes the key light's
  direction in head space from the skinned head frame and passes one `vec2`
  varying; the fragment shader samples the threshold (mirrored in u when the
  light is on the right) and computes
  `lit = smoothstep(t - w, t + w, threshold)` with `t` the light's angle /
  180 and `w` from `faceSdfSoftness`. Where the light is nearly overhead the
  map fades to lit. At night the key's elevation is held to at most 40
  degrees, so a high moon still gives a lit and a shadow side.
- The fringe shadow still darkens under the bangs; shadow maps never fall on
  the face; flat texels are always lit.
- **Low:** the face texture is used, but lighting falls back to the
  forced-open face with the ellipsoid normals.

The face shadow is a warm peach-rose (`skin` and `face` rows'
`shadowTint` in `data/art/toon_palette.json`).

## 4. Outlines

Characters draw **inverted-hull outlines** tinted per material; the castle
has none and reads through its shading.

- **Hull** (`client/src/game/toon/outline.ts`): each character tier mesh
  gets a second mesh on the same geometry (same vertex buffers, so the same
  skinning, sway and recolouring), drawn with one shared `OutlineMaterial`:
  front faces culled, every vertex pushed out in clip space along its baked
  smoothed normal, and pushed a few millimetres back along the view ray so
  only the rim past a silhouette shows. One extra draw per outlined
  character; the scene's 4x MSAA anti-aliases it.
- **Width:** 1.3 render pixels at a 1000-pixel-high frame, times
  `clamp(renderHeight / 1000, 1, 2.5)` (about one CSS pixel at any density),
  times the region's width (face 0.6, skin 0.8, hair 0.9, cloth 1, metal
  0.7, flat 0), times the baked weight; thinning with distance and fading
  out by 34 m. LOD0-LOD2 only; Medium and High only.
- **The outline layer** (`charlib/outline_bake.py`): every exported character
  mesh carries `_OUTLINE` (VEC3 float): the smoothed normal (angle-weighted
  face normals summed over all vertices at one position, so hard edges and
  UV seams do not tear the hull) times the width weight: 1, 0.7 on the
  hands, **0 over the face's eyes, brows, nose and mouth**, 0 on the hair's
  inner layer. The client packs it into 4 signed bytes.
- **Colour** (data only, `data/art/toon_palette.json` `outline`): per
  region, `max(pow(c, gamma) * scale * tint, floor)` of the vertex colour
  (the skin tone, hair colour or dye): a dark warm brown round skin, a
  deeper shade of the hair colour round hair, of the dye round cloth, never
  pure black. So creator colours, dyes and the Chameleon Tonic recolour the
  line. The line is scaled by the scene's light, so it darkens at night.

## 5. Render quality

Per quality (`client/src/settings.ts`; summary in
[`style-guide.md`](style-guide.md), section 10):

- **Low:** render scale `min(1, 0.75 x DPR)` (1.0 on a Retina screen), the
  canvas's own MSAA, no passes, no hulls.
- **Medium and High:** native resolution up to DPR 2, **4x MSAA on the
  scene target** (`PostProcess.samples = 4`, with the depth resolved for the
  passes that read it), no FXAA.
- **Dynamic-resolution guard** (Medium, High;
  `client/src/game/resolution-guard.ts`): when more than 10% of recent
  frames are slow, the render scale steps down (2.0 -> 1.5 -> 1.25, MSAA
  kept); it steps back up after a sustained run of fast frames. At most one
  step per 2 s, no allocation. The F3 overlay shows the current scale.
- The character atlas is trilinear with mips and swatch-centre UVs; the
  face textures as in 3.1.

## 6. Checks

- **Hair:** `check_hair.py`, `check_hair_hats.py`,
  `check_hair_see_through.py` (`--out` writes review sheets per style),
  `render_hair.py` (review sheets with the painted face); tests
  `tests/test_hair_meshy.py`, `tests/test_hair_braid.py`, `tests/test_hair.py`.
- **Faces:** `tests/test_face.py` (texture sizes, symmetric UVs inside
  [0, 1], a monotonic threshold map, features in the sculpt's sockets, blink
  closing fully, the line and glow encoding); client `face.test.ts` (the
  face albedo and line rules on every skin tone, the mask decoding).
- **Outlines:** `tests/test_outline.py`; client `outline.test.ts`.
- **Palette:** `tools/blender/tests/test_palette.py` checks the character
  rows and their fields.
- **In game:** close-ups of both bodies front, three-quarter and side, at
  noon and at night, at Low, Medium and High, with dark and light skin and
  several eye colours.
