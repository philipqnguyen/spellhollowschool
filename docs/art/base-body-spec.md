# Base body spec

The two base bodies every player character is built on, their skeleton, the
gear slots and the rules gear follows so outfits mix without clipping. The
build is scripted (`tools/blender/characters`, run by
`build_characters.py`); art direction is in [`style-guide.md`](style-guide.md),
clothing in [`garment-shapes.md`](garment-shapes.md), the character data and
shading in [`character-adoption.md`](character-adoption.md), hair and faces
in [`character-look.md`](character-look.md).

> Code comments cite this document's sections by number ("base-body-spec.md section 3.1").
> When you renumber or move a section, update the citations too
> (`git grep base-body-spec.md`).

All player characters are adult students. Proportions, faces
and every height must read as adult; nothing is sexualised.

## 1. Bodies

Two bodies, offered in the character creator as **Male** (`body_a`, index 0)
and **Female** (`body_b`, index 1). They share one skeleton layout, one
material and the same export format.

| | `body_a` (Male) | `body_b` (Female) |
| --- | --- | --- |
| Default height | 1.78 m | 1.68 m |
| Height range | 1.60-1.88 m | 1.52-1.80 m |
| Heads tall (crown to chin over height) | about 7.4 | about 6.7 |
| Shape source | "Anime boy male model T-pose base basic FREE" by ALexzart (CC BY 4.0) | "Anime BaseMesh Sculpt 01" by DJP3D (CC BY 4.0) |

- **One neutral build.** There are no build shape keys (slim, muscular,
  heavy); garments carry no shape keys at all.
- **Height is the only body choice:** a uniform scale of the whole character
  about the root (`scale = height / default`), never a stretch. The ranges
  live in `data/characters/creator.json` and `charlib/proportions.py`
  `height_range` (a test compares them).
- **Face sliders and expressions** are shape keys on the head: `face_jaw_*`,
  `face_cheek_*`, `face_nose_*`, `face_brow_*`, `face_eyes_narrow` /
  `face_eyes_round`, and `blink_l`, `blink_r`, `smile`, `frown`,
  `mouth_open`, `brows_up`, `brows_down`.
- Each body wears a painted base layer (underwear) that clothing covers.

### How a body is built

1. **Base mesh** from MakeHuman's CC0 base mesh and targets through MPFB
   (`charlib/mh_body.py`, `mh_source.py`; licences in
   [`art/CREDITS-makehuman.md`](../../art/CREDITS-makehuman.md)): one human per
   body at age 18, posed onto our skeleton with MakeHuman's CC0 weights,
   renamed onto our `DEF-*` bones. MPFB must be installed in the Blender user
   resources the build runs with (`tools/blender/install_mpfb.sh`,
   `BLENDER_USER_RESOURCES`).
2. **Anime head** (`charlib/mh_head.py`): MakeHuman's head is removed above
   a neck edge loop and a scripted head is welded on: one clean quad grid
   ray-sampled from an SDF, a face lofted from superellipse sections (a
   small pointed chin, a flat profile, a plane under the jaw), small separate
   ear shells. Its cranium is the shared scalp cage that hats and hairstyles
   fit to.
3. **Sculpt wrap** (`charlib/sculpt_wrap.py`, `shape_target.py`): each body
   takes the *shape* of its CC BY sculpt while keeping our topology,
   skeleton names, weights, UVs, dye mask, regions and shape key names. The
   wrap is baked once into `art/characters/shape_targets/<body>.npz` by
   `wrap_sculpts.py` (needs the sculpts: `--sculpt-dir`, default
   `$SHS_SCULPT_DIR` or `vendor_assets/`); normal builds read the committed
   `.npz` and never need the sculpts. Each sculpt keeps its own proportions
   and is only scaled uniformly to the default height. The sculpts are
   smoothed into neutral forms where detail is not wanted (groin, chest,
   gluteal cleft) and around the features we paint (eye sockets, lips); our
   own ear shells replace theirs.
4. **Painted face** on the wrapped head from the sculpt's face anchors (eye
   sockets, nose, mouth): see [`character-look.md`](character-look.md),
   section 3. The face texture, the eye opening and the mouth corners are
   what the expressions move.
5. **Skeleton refit:** shoulder and hip joints follow the wrap; elbow,
   wrist, knee and ankle keep their place in the limb's cross-section. The
   neck and head bones run through the measured neck centre line
   (`shape_target.neck_line`), so head turns pivot inside the neck. Hats and
   hairstyles are fitted against each body's own visible head.
6. **LODs, shade layer and outline layer** (sections 6 and 7).

Other body sources remain for testing and comparison only:
`--body-shape none` (the unwrapped MakeHuman body, `<body>_mh.blend`) and
`--body-source current` (an older fully scripted body that `test_geometry`
and `test_rig` still build, `<body>_current.blend`). Neither ships.

## 2. Orientation, units and rest pose

- Metres; glTF Y up, the character faces **+Z**. In Blender the character
  faces -Y and stands on the origin; export with "+Y Up".
- Game convention: yaw 0 faces +Z (south) and forward is
  `(sin yaw, 0, cos yaw)`, so yaw pi/2 faces +X (east) and yaw pi faces north.
- Rest pose: **A-pose**, arms 45 degrees down from horizontal, palms facing
  down and slightly forward, elbows and knees very slightly bent, feet
  parallel at hip width, mouth closed, eyes forward.
- The origin is on the floor between the feet; the `root` bone sits there.

## 3. Skeleton (Rigify compatible)

The rig is generated with Rigify from the **Human (Meta-Rig)** template
fitted to the body; only Rigify's deform bones (`DEF-*`) plus `root` and the
sockets are exported. Names are exactly as Rigify generates them, so clips
keyed on the Rigify control rig bake straight onto the exported skeleton.
Rigify's face rig is not used; the face uses shape keys.

| Region | Bones (parent first) |
| --- | --- |
| Root | `root` (non-deforming, origin; carries root motion) |
| Spine | `DEF-spine` (hips) → `DEF-spine.001` → `DEF-spine.002` → `DEF-spine.003` (chest) |
| Neck and head | `DEF-spine.004` → `DEF-spine.005` (neck) → `DEF-spine.006` (head) |
| Face | `DEF-jaw`, `DEF-eye.L`, `DEF-eye.R` (children of `DEF-spine.006`; carry no weights) |
| Pelvis and chest | `DEF-pelvis.L/R` (children of `DEF-spine`), `DEF-breast.L/R` (children of `DEF-spine.003`) |
| Shoulders | `DEF-shoulder.L/R` |
| Arms | `DEF-upper_arm.L` → `DEF-upper_arm.L.001` → `DEF-forearm.L` → `DEF-forearm.L.001` → `DEF-hand.L` (same for `.R`) |
| Palm | `DEF-palm.01.L` … `DEF-palm.04.L` |
| Thumb | `DEF-thumb.01.L` → `DEF-thumb.02.L` → `DEF-thumb.03.L` |
| Fingers | `DEF-f_index.01.L` → `.02` → `.03`; likewise `DEF-f_middle`, `DEF-f_ring`, `DEF-f_pinky` |
| Legs | `DEF-thigh.L` → `DEF-thigh.L.001` → `DEF-shin.L` → `DEF-shin.L.001` → `DEF-foot.L` → `DEF-toe.L` (same for `.R`) |

76 deforming bones plus `root` and the sockets. The `.001` bones are
Rigify's twist segments; they stop candy-wrapping at the wrists, elbows and
knees. After every clip bake, `anim.unflip_twists` removes half-turn flips
of the twist bones (`test_no_twist_bone_flips` guards it).

**Sockets** (non-deforming, `charlib/rig.py` `SOCKETS`) attach props that do
not skin:

| Socket | Parent | Use |
| --- | --- | --- |
| `socket_hand.R`, `socket_hand.L` | `DEF-hand.R/L` | wand, books, goblets |
| `socket_head` | `DEF-spine.006` | rigid head props |
| `socket_back` | `DEF-spine.003` | items carried on the back |
| `socket_hip.L` | `DEF-spine` | wand holster |

Hair styles add up to 12 sway bones of their own, appended to the rig by
name ([`character-adoption.md`](character-adoption.md), section 4).

**Export rules:** deform bones plus sockets, no leaf bones, no constraints
or drivers. At most 4 influences per vertex, normalised, weights under 0.01
removed. Clips are baked to the deform bones at 30 fps.

## 4. Body regions and gear slots

The body is split into regions that are drawn or hidden individually:
`head`, `scalp`, `ears`, `neck`, `torso`, `upper_arms`, `forearms`, `hands`,
`hips`, `thighs`, `calves`, `feet` (`charlib/body.py` `REGIONS`). Each gear
item lists the regions it hides (`hides`); hidden skin is not drawn, so it
cannot poke through and costs nothing.

Four slots, one item each, layered inner to outer **shoes < legs < body**,
with the hat on the head:

| Slot | Covers | Examples |
| --- | --- | --- |
| `hat` | top of the head | pointed and brimmed hats, caps, hoods, hair trims |
| `body` | torso and arms; robes and coats extend down the legs | robes, shirts, jumpers, coats |
| `legs` | hips to ankles | trousers, skirts over tights |
| `shoes` | feet and ankles | boots, shoes |

Hair is part of the character (a creator choice), not a slot. A hat that
hides the scalp is fitted over the hair per hairstyle
([`character-adoption.md`](character-adoption.md), section 4.4).

Every clothing item is a design on a fitted garment shape, built and
clipping-checked on both bodies ([`garment-shapes.md`](garment-shapes.md)).
Some items are restricted to one body by the catalog's `wear` field
(`"male"` / `"female"`, matched against `wear` on the body type in
`creator.json`); only wearing is restricted, anyone may own or trade any
item.

## 5. Materials and UVs

- **One material for everything:** every body, hair and clothing file uses
  the material `character` with one shared 256 px swatch atlas
  (`charlib/atlas.py`), so a dressed character merges into one mesh and one
  draw call.
- **`COLOR_0` is a tint mask**:
  `albedo = atlas * ((1 - r - g - b) + r*C0 + g*C1 + b*C2)`: skin tone, hair
  colour and eye colour on bodies, hair colour on hair, the three dyes
  (primary, secondary, trim) on clothing. Colours stay choosable.
- **The face** (the body's `head` primitive) samples its own texture pair
  instead of the atlas: [`character-look.md`](character-look.md), section 3.
- Folds and occlusion are a per-vertex shade layer, not texture
  ([`character-adoption.md`](character-adoption.md), section 3).

## 6. LODs and triangle budgets

Every character asset ships four LODs in one file: mesh objects named
`<name>_lod0` ... `_lod3`, not `MSFT_lod`. Hidden regions and `hides` lists
are the same on every LOD. Items over budget are decimated automatically
(region borders and open edges protected; `charlib/lod.py`,
`pipeline.make_lods`).

| Part | LOD0 | LOD1 | LOD2 | LOD3 |
| --- | --- | --- | --- | --- |
| Base body (incl. head) | 16,000 | 5,000 | 1,500 | 500 |
| Hair | 24,000 | 8,000 | 1,300 | 450 |
| `body` garment | 8,000 | 2,700 | 800 | 250 |
| `legs` | 4,000 | 1,300 | 400 | 120 |
| `shoes` | 1,500 | 500 | 150 | 50 |
| `hat` | 2,500 | 800 | 250 | 80 |
| **Dressed character (max)** | **52,000** | **17,500** | **4,000** | **1,350** |

These are `pipeline.LOD_BUDGETS` and `pipeline.DRESSED_MAX`; the client's
`characters/lod.ts` `TIER_TRIANGLES` must match the dressed row. Garment
shapes keep their own LOD0 `budget` under these tiers
([`garment-shapes.md`](garment-shapes.md)); hair budgets depend on the
style's length class ([`character-look.md`](character-look.md), section 2).
LOD2 and LOD3 bodies carry no shape keys. How the client picks a tier:
[`character-adoption.md`](character-adoption.md), section 6.

## 7. Animation clips

Baked into each body file (30 fps), defined in `charlib/anim.py` `CLIPS` and
`charlib/anim_spells.py`:

- **Locomotion and emotes:** `idle`, `walk`, `run` (both retargeted from
  Quaternius's CC0 Universal Animation Library, `charlib/ual.py`; credits in
  [`art/CREDITS.md`](../../art/CREDITS.md)), `wave`, `bow`.
- **Sitting:** `sit_down` (lowering from standing in front of a seat), `sit`
  (on a seat), `floor_rest` (on the floor, legs out, where there is no seat),
  `floor_sit` (cross-legged on a floor seat such as a rug or hearth step).
- **Flying:** `broom_ride`, `broom_hover` (the broom prop hangs from the hips
  bone).
- **Wand work:** `gesture_<key>` for each gesture of
  `data/classes/gestures.json` (the wand tip traces the template), `cast`.
- **Dueling:** `duel_ready`, `shield_raise`, `hit_react`, `knockback`,
  `stun_fall`, `stun_rise`, `disarmed`.
- **Class:** `professor_stand`, `professor_demonstrate`, `desk_rest`,
  `desk_practice` (seated wand practice), `spectator_cheer`.

**Seated clips** (`sit`, `desk_rest`, `desk_practice`): the root is the
seat's top and the hip joints sit at the body's own thigh radius above it;
thighs about level, shins upright; the hands rest in the lap, so nothing has
to reach a desk or table. At the body's maximum height the heels reach a
floor 0.46 m below the seat; shorter characters' feet hang a little above
it. Garments with a hanging skirt carry a corrective sitting-drape shape key
per seated clip ([`garment-shapes.md`](garment-shapes.md), section 7).

**Lying and getting up** (`stun_fall`, `stun_rise`): the feet are placed
where the body's own leg reaches from its posed hip joints, so no knee or
foot goes below the floor at any height.

## 8. Checks

- `tools/blender/characters/tests/` (run with the Blender test runner, see
  [`tools/blender/README.md`](../../tools/blender/README.md)): rig bone names
  and hierarchy (`test_rig.py`), the shape targets (topology identical to the
  unwrapped body, LOD0 within budget, height on the floor, no folded
  triangles, symmetry, face anchors, credits present), asset budgets and
  attributes (`test_assets.py`).
- `check_characters.py --poses full`: items and hair against visible skin in
  the rest pose and frames of the shipped clips.
- `check_seating.py`: seated and lying poses against the seat, the floor and
  the furniture at minimum, default and maximum height.
