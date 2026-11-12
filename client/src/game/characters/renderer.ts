// Draws every character in a scene: the local player, the visible remotes,
// or the one figure in a wardrobe mirror.
//
// Each character is one skinned mesh (assemble.ts: body + hair + gear merged,
// dyes baked into vertex colours, garment finishes in their alpha, drawn by
// the material's finish plugin, finish.ts; the shade layer in a fourth
// buffer, `toonShade`, for the toon shading) with one shared material, so it
// costs one draw call (plus one in the character shadow pass for the nearest
// few at Medium and High). Assembled geometry is cached by appearance
// (identical outfits share one GPU copy) and each entity keeps what it shows
// keyed by (id, appearance version), so a version is assembled once per
// LOD tier. Skeletons are per character (each has its own pose); the
// animation data they are posed from is shared per rig (clips.ts), and far
// characters are re-posed at a lower rate.
//
// LOD (docs/art/character-adoption.md section 6, lod.ts): four tiers,
// chosen at 4 Hz by rank and distance per quality level, then fitted to the
// characters' triangle allowance (the quality's total budget less the world
// triangles measured in view, castle D1) by demoting the farthest; a tier is assembled
// the first time a character needs it (at most two assemblies a frame, and
// within a time budget), and until then the character keeps drawing the
// tier it has. Your own character (`setSelf`) is always LOD0.
//
// Hair sway (sway.ts): a hair file with sway joints extends the rig; LOD0
// and LOD1 characters swing them on cheap springs every frame they are
// posed; farther ones keep the rest pose.
//
// Shadows (character-adoption.md section 7): the nearest characters (8 at Medium, 12 at High,
// toon/char-shadows.ts) cast into the character shadow map; every other
// character, and everyone at Low, gets a blob (blobs.ts, one draw for all).
// Character meshes receive the character shadows (never on the face).
//
// Usage per frame: `begin()`, then `show(...)` for every character to draw,
// then `update(dt, eye)`. Characters not shown in a frame are hidden, and
// dropped after a while. Steady frames allocate nothing: selection, posing,
// sway, blobs and the caster list work in preallocated buffers; assembly
// (a new look or tier) allocates.
//
// Outlines (docs/art/character-look.md section 4, toon/outline.ts): at Medium and
// High each character within the outline's reach, at LOD0-LOD2, draws a
// second mesh on its own geometry with the inverted-hull material (one more
// draw per outlined character), skinned by the same skeleton, so it follows
// the animation, the hair sway and live recolours. Low draws none. The
// budget governor counts a hull's triangles with its character's (lod.ts).
//
// Looking at a target (look.ts, docs/architecture.md "Anims and emotes"):
// `setLook` names whom (or what point) a character looks
// at; within `LOOK_DRAW_M` of the camera its head, neck and a little of the
// chest turn toward the target's eyes as posed, eased and clamped, on top
// of the clip and before the hair sway (so the hair follows the head).
//
// Potion effects hook in through `show`'s `fx`: a scale
// (Diminishing and Enlarging Draughts) on top of the height slider, and one
// dye channel recoloured live (Chameleon Tonic). A recoloured character
// gets its own copy of its geometry, assembled with that channel kept
// separable (assemble.ts `track`), whose colour buffers are rewritten when
// the colour steps; nobody else's look is touched. Glows, steam and the like
// are drawn by game/effects.
//
// The wand (wand-state.ts, wands.ts): while a character casts, or holds its
// wand out for a lit wand, a trace or an aim (`holdWand`), the wand grows
// into its right hand, posed with it every frame (one thin instance each,
// one draw for all). Spell effects start at its drawn tip (`wandTip`).

import { Bone } from "@babylonjs/core/Bones/bone";
import { Skeleton } from "@babylonjs/core/Bones/skeleton";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Matrix, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Material } from "@babylonjs/core/Materials/material";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Texture } from "@babylonjs/core/Materials/Textures/texture";
import { Geometry } from "@babylonjs/core/Meshes/geometry";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import type { Scene } from "@babylonjs/core/scene";
import type { Light } from "@babylonjs/core/Lights/light";
import type { Anim, Appearance } from "../../wasm/sim_wasm";
import type { Quality } from "../../settings";
import { appearanceKey, heightMetres } from "../../content/appearance";
import { Animator } from "./animator";
import { atlasKey, decodeAtlas } from "./atlas";
import type { AtlasImage } from "./parts";
import { assemble, DRAPE_KEYS, hasLod, hiddenRegions, lodFor, recolour, type MergedGeometry, type PartInput, type TintSplit, type TrackedChannel } from "./assemble";
import type { Rgb } from "./dye";
import { samplePose, type Rig } from "./clips";
import { CharacterFinishPlugin } from "./finish";
import { CHARACTER_SHADOW_RANGE, claimLiveHairSlot, FACE_SDF_SOFTNESS, releaseLiveHairSlot, setLiveHairColour, setToonFaceShadow, setToonKeyDirection, TOON_DRAPE_KINDS, TOON_SHADE_KIND, ToonPlugin } from "../toon/material";
import { invert4, mul4 } from "./math";
import type { FaceTextureUrls, PartLibrary, RigKit } from "./library";
import { recipeFor, type Recipe } from "./recipe";
import { applySway, extendRig, resetSway, swayState, type SwayRig } from "./sway";
import { kneeLift, legBones, liftKnees, measureSeatedLegs, seatShift, type SeatedLegs } from "./seated";
import { anglesTo, applyLook, bodyToWorld, eyeInBody, lookBones, lookState, modelMatrix, resetLook, stepLook, worldToBody, type LookState } from "./look";
import { fallbackMatrix, STAND_IN_TIP, stepWand, WAND_HAND, wandMatrix, wandSize, wandTipOf, type WandFit, type WandRoot } from "./wand-state";
import type { Wands } from "./wands";
import { BlobShadows } from "./blobs";
import { characterAllowance, characterFloor, drawnTriangles, governTiers, LOD_RULES, PROTECT_NEAREST, SELECT_INTERVAL, TIER_COUNT, tierFor, WORLD_WINDOW, type LodRule, type Tier } from "./lod";
import { CHARACTER_FX } from "./fx-flag";
// --- Outlines ---
import { qualityProfile } from "../../settings";
import { toonOutlineMask } from "../toon/material";
import { OUTLINE_KIND, OUTLINE_OVERRIDE, OutlineMaterial, outlineDrawn, outlineLight } from "../toon/outline";
// --- end Outlines ---

/** `?charfx=off` (fx-flag.ts): the two-tier rule of before the adoption (LOD1 beyond 15 m). */
const LEGACY_RULE: LodRule = { lod0Rank: Infinity, lod0Dist: 15, lod1Dist: Infinity, lod2Dist: Infinity, total: Infinity };

/** Within this camera distance (m) characters are posed every frame (and sway, at LOD0-1). */
export const LOD_DISTANCE = 15;
/** Posing rate by distance: every frame near, then at most 30 and 15 times a second. */
const POSE_FAR = 35;
const POSE_INTERVAL_MID = 1 / 30;
const POSE_INTERVAL_FAR = 1 / 15;
/** Characters farther than this from the camera (m) do not turn to look (the nameplates' reach). */
export const LOOK_DRAW_M = 28;
/** A look target farther than this from the looker (m) is let go (protocol v10 `LOOK_RANGE_M` plus the server's slack). */
const LOOK_REACH_M = 22;
/** A character not shown for this long is disposed (s). */
const FORGET_AFTER = 20;
/**
 * At most this many characters are kept hidden for a quick return (each
 * holds a skeleton and its meshes). In a crowd people walk out of the
 * visible set many times a second; without a bound, twenty seconds of that
 * kept hundreds alive. The ones hidden longest go first.
 */
const MAX_HIDDEN = 32;
/** Unused assembled looks kept for quick reuse (LRU). */
const SPARE_GEOMETRIES = 12;
/** Time per frame spent assembling characters (ms), and at most this many assemblies a frame (at least one is done). */
const ASSEMBLY_BUDGET_MS = 4;
const MAX_ASSEMBLIES_PER_FRAME = 2;
/** Nameplates sit this far above the top of the head (m). */
const PLATE_GAP = 0.32;
/** Wands farther than this from the camera (m) are not drawn (a few pixels long). */
const WAND_DRAW_M = 40;
/** The bone a broom's seat goes under (the hips: rigify's root spine bone), as clips.ts finds it. */
const HIPS_BONES = ["DEF-spine", "hips", "root"];
const ORIGIN = Vector3.Zero();

/** What a potion changes about how a character is drawn (game/effects). */
export interface CharacterFx {
  /** Size as a fraction of normal (Diminishing Draught 0.5, Enlarging Draught 1.3); cosmetic only. */
  scale: number;
  /** Chameleon Tonic: the hair colour or the body garment's primary dye, recoloured now (sRGB 0..1). (Tinctura dyes the look itself: effects/state.ts `RecolouredLooks`.) */
  tint: { part: TintPart; colour: Rgb } | null;
  /** Spells L20: how fast the animation plays (1 normal; 0 Rigora: the pose freezes where it is; Tardara's slow motion in between). */
  animRate: number;
}

export type TintPart = "hair" | "robe";

/**
 * NPCs in the open castle (game/npc): how a character is drawn apart from
 * its look. `tweak` makes its material from a copy of the shared one (the
 * atlas and face textures kept; cached per shared material and `key`): the
 * house ghosts' translucent tint, a talking portrait's subject clipped to its
 * canvas. Null `tweak` draws with the shared material.
 */
export interface CharacterStyle {
  /** Names the material variant (one per shared material and key). */
  key: string;
  tweak: ((m: StandardMaterial) => void) | null;
  /** Its outline hull at Medium and High. */
  outline: boolean;
  /** Casts into the character shadow map or gets a blob. */
  shadow: boolean;
  /** Drawn this far above its feet (m; the ghosts float). */
  lift: number;
  /** Beyond this camera distance (m) it draws at the coarsest tier (background students); Infinity: by the usual rules. */
  coarseBeyond: number;
}

/** The dye channels a recoloured part spans: hair also colours the brows. */
function trackFor(part: TintPart): TrackedChannel[] {
  return part === "hair"
    ? [
        { role: "hair", channel: 0 },
        { role: "base", channel: 1 },
      ]
    : [{ role: "body", channel: 0 }];
}

/** The scene side of character shadows (toon/char-shadows.ts `CharacterShadows`). */
export interface CharacterShadowHost {
  /** Characters that may cast now (0 at Low: blobs only), and how far away at most (m). */
  readonly maxCasters: number;
  readonly maxDistance: number;
  addReceiver(mesh: Mesh, keyShadow: boolean): void;
  removeReceiver(mesh: Mesh): void;
  /** This frame's casters (the first `count`, nearest first) and the point they stand around. */
  setCasters(meshes: readonly Mesh[], count: number, cx: number, cy: number, cz: number): void;
}

export interface CharacterRendererOptions {
  library: PartLibrary;
  /** A character mesh was created or is about to be disposed (High's sun shadow casters). */
  onMesh?: (mesh: Mesh, added: boolean) => void;
  /** Character shadow maps at Medium and High (null or absent: blobs only). */
  shadows?: CharacterShadowHost | null;
  /** Draw blob shadows (default true). */
  blobs?: boolean;
  /** Wands in the casters' hands (wands.ts; null or absent: none drawn, and the tip is still found). Disposed with the renderer. */
  wands?: Wands | null;
}

export interface CharacterStats {
  characters: number;
  visible: number;
  /** Visible characters at each LOD tier (index 0-3). */
  tiers: [number, number, number, number];
  /** Visible characters at LOD1 or coarser (the old two-tier stat). */
  lod1: number;
  /** Triangles of the visible characters as drawn (their outline hulls included, as the governor counts them). */
  triangles: number;
  geometries: number;
  /** Vertices across the cached geometries (GPU memory scales with this). */
  vertices: number;
  pending: number;
  partFiles: number;
  missingFiles: number;
  /** Characters drawn with a live-recoloured copy (Chameleon Tonic). */
  recoloured: number;
  /** Characters whose hair sways, casting into the shadow map, and with a blob. */
  swaying: number;
  shadowCasters: number;
  blobs: number;
  /** The last assembly's time (ms) per tier. */
  assemblyMs: [number, number, number, number];
  /** Characters drawing their outline hull (one draw each). */
  outlined: number;
  /** Castle D1 governor: world triangles in view (the window's largest) and the characters' allowance. */
  worldInView: number;
  allowance: number;
}

/** A recolourable geometry's colour data for one tier. */
interface TintLod {
  split: TintSplit;
  bytes: Uint8Array;
  buffer: VertexBuffer;
}

interface TierGeometry {
  geometry: Geometry;
  /** A hidden mesh that keeps the geometry alive while no character uses it. */
  keeper: Mesh;
  vertices: number;
  triangles: number;
  tint: TintLod | null;
  /** Whether any vertex carries an outline width (parts exported with `_OUTLINE`). */
  outlined: boolean;
  /** Whether it carries the sitting drape (a skirt or robe: assemble.ts `drape`). */
  draped: boolean;
}

interface GeometryEntry {
  key: string;
  /** The rig (skeleton and clips) the geometry is skinned to. */
  kit: RigKit;
  /** The rig with this look's hair sway joints, or null (no sway). */
  sway: SwayRig | null;
  /** The rig the geometry and the skeleton use (the sway rig's, else the body's). */
  rig: Rig;
  rigKey: string;
  recipe: Recipe;
  parts: PartInput[];
  /** The shared material it draws with (atlas-textured, or plain for all-greybox characters). */
  material: StandardMaterial;
  whiteUv: [number, number] | null;
  track: TrackedChannel[] | null;
  /** Per effective tier: its geometry, once assembled. */
  tiers: (TierGeometry | null)[];
  /** Tier → the coarsest tier at or below it that has its own geometry (parts without LOD3 reuse LOD1, ...). */
  tierOf: Uint8Array;
  /** Triangles per tier (an estimate until assembled). */
  triangles: Float64Array;
  /** Tiers queued for assembly. */
  queued: Uint8Array;
  refs: number;
  vertices: number;
  lastUsed: number;
  /** A live-recolourable copy belongs to one view and is disposed when it lets go. */
  owner: number | null;
  tintApplied: Rgb | null;
  /** A live hair recolour's face palette slot (its brows and lashes), or -1 (toon/material.ts `claimLiveHairSlot`). */
  liveHairSlot: number;
}

interface AtlasMaterial {
  material: StandardMaterial;
  whiteUv: [number, number];
}

interface View {
  id: number;
  version: number;
  /** Geometry key wanted (the latest appearance) and the entry showing. */
  want: string | null;
  showing: GeometryEntry | null;
  root: TransformNode;
  /** A mesh per effective tier, created when first drawn. */
  meshes: (Mesh | null)[];
  /** Outlines: the outline hull per effective tier (created when first outlined), and the tier whose hull is on (-1: none). */
  hulls: (Mesh | null)[];
  hullOn: number;
  /** The sitting drape's weights (chair, floor; animator.ts `drapeWeights`), read by its meshes' materials. */
  drape: Float32Array;
  /** The effective tier drawn now (-1: none yet) and the tier selected. */
  drawn: number;
  tier: Tier;
  skeleton: Skeleton | null;
  rigKey: string;
  rig: Rig | null;
  /** The rig clips are sampled on (the body's). */
  poseRig: Rig | null;
  sway: SwayRig | null;
  swayState: Float32Array;
  animator: Animator | null;
  pose: Float32Array;
  /** Standing height (m) of the appearance shown. */
  height: number;
  /** The height slider's scale, and the potion scale on top of it. */
  baseScale: number;
  fxScale: number;
  /** Which part this view's geometry keeps recolourable (part of its key), and the colour wanted now. */
  tintPart: TintPart | null;
  tintColour: Rgb | null;
  /** Spells L20: the animation's rate (CharacterFx.animRate). */
  animRate: number;
  lastShown: number;
  shownThisFrame: boolean;
  poseClock: number;
  anim: Anim;
  speed: number;
  enabled: boolean;
  yaw: number;
  /** The floor under the character (the seat's floor when seated). */
  floorY: number;
  /** Drawn on a seat (`floorY` below the feet): `sit` plays the seated clip, else the floor sit. */
  seated: boolean;
  /** Drawn on a floor seat (a rug, a hearth step): `sit` plays `floor_sit`. */
  floorSeat: boolean;
  /** The body's seated legs (characters/seated.ts; null: not measured), its leg bones, and the knees' rise on this seat (radians). */
  legs: SeatedLegs | null;
  legBones: Int16Array | null;
  kneeLift: number;
  /** Distance to the camera this frame, rank at the last selection, and whether it casts into the shadow map. */
  dist: number;
  rank: number;
  shadowed: boolean;
  /** Looking at a target (look.ts): whom (-1: nobody), or a point instead, the eased turn, and the rig's look bones (null: none). */
  lookTarget: number;
  lookHasPoint: boolean;
  lookPoint: Float64Array;
  look: LookState;
  lookBones: Int16Array | null;
  /** How it is drawn apart from its look (NPCs; `setStyle`), or null. */
  style: CharacterStyle | null;
  /** Holds its wand out apart from casting (`holdWand`), and the wand's growth now (0 away .. 1 in the hand; wand-state.ts). */
  wandHeld: boolean;
  wandGrow: number;
}

/** A seat's reach along the sitter's facing (castle/seats.ts `SeatPlace`): ahead to its front edge, behind to its back, its drop to the floor (m). */
export interface SeatFit {
  ahead: number;
  behind: number;
  drop: number;
}

/** Scratch for sway roots and pose sampling (no allocation per frame). */
const SWAY_ROOT = { x: 0, y: 0, z: 0, yaw: 0, scale: 1 };
const POSE_SCRATCH = new Float32Array(7);
/** Look scratch: eyes and target in a body's frame, the target in the world, the angles. */
const LOOK_EYE = new Float64Array(3);
const LOOK_AT = new Float64Array(3);
const LOOK_WORLD = new Float64Array(3);
const LOOK_ANGLES: [number, number] = [0, 0];
/** Wand scratch: where a character is drawn and its hand bone's model matrix. */
const WAND_ROOT: WandRoot = { x: 0, y: 0, z: 0, yaw: 0, scale: 1 };
const WAND_BONE_M = new Float32Array(16);

/** Distance without Math.hypot (V8 allocates for its arguments). */
function dist3(ax: number, ay: number, az: number, bx: number, by: number, bz: number): number {
  const dx = ax - bx;
  const dy = ay - by;
  const dz = az - bz;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

export class CharacterRenderer {
  private readonly views = new Map<number, View>();
  /** Each rig's hips bone index (a broom's seat), found once. */
  private readonly hipsBone = new WeakMap<object, number>();
  /** Each rig's look bones (look.ts), found once. */
  private readonly lookBonesOf = new WeakMap<Rig, Int16Array | null>();
  /** Each body's seated legs (characters/seated.ts), measured once. */
  private readonly legsOf = new WeakMap<RigKit, SeatedLegs | null>();
  private readonly geometries = new Map<string, GeometryEntry>();
  private readonly building = new Map<string, Promise<void>>();
  private readonly queue: { entry: GeometryEntry; tier: number }[] = [];
  /** For characters without an atlas (all greybox). */
  private readonly plain: StandardMaterial;
  /** One textured material per atlas (there is normally exactly one). */
  private readonly atlases = new Map<string, Promise<AtlasMaterial | null>>();
  private readonly textures: Texture[] = [];
  /** Each atlas material's face textures (a style's variant takes them too). */
  private readonly faces = new WeakMap<StandardMaterial, { colour: Texture; mask: Texture }>();
  /** Styled copies of the shared materials, by shared material and style key. */
  private readonly variants = new Map<string, StandardMaterial>();
  private readonly blobs: BlobShadows | null;
  private time = 0;
  private disposed = false;
  private geometrySerial = 0;
  private rule: LodRule = LOD_RULES.medium;
  private selfId = -1;
  private selectClock = SELECT_INTERVAL;
  private readonly lastEye = { x: 0, y: 0, z: 0 };
  // Selection scratch (grown, never shrunk).
  private order = new Int32Array(64);
  private dists = new Float64Array(64);
  /** Shadow casters are the nearest to our own character (the camera stands behind it). */
  private shadowOrder = new Int32Array(64);
  private shadowDists = new Float64Array(64);
  private tierScratch = new Uint8Array(64);
  /** World triangles in view at the last `WORLD_WINDOW` selection ticks (a ring), their largest, and the allowance. */
  private readonly worldTicks = new Float64Array(WORLD_WINDOW);
  private worldTick = 0;
  private worldMax = 0;
  private allowance = Infinity;
  /**
   * Indices the camera's own draw phase drew last frame (castle D1
   * integration): Babylon's active-indices counter also counts every render
   * target drawn before it (the sun's and the characters' shadow maps), which
   * at High tripled the "world in view" and starved the characters.
   */
  private drawPhaseStart = 0;
  private drawPhaseIndices = 0;
  private readonly onBeforeDraw = (): void => {
    this.drawPhaseStart = this.scene.getActiveIndices();
  };
  private readonly onAfterDraw = (): void => {
    this.drawPhaseIndices = this.scene.getActiveIndices() - this.drawPhaseStart;
  };
  private readonly list: View[] = [];
  private readonly casterMeshes: Mesh[] = [];
  private readonly assemblyMs: [number, number, number, number] = [0, 0, 0, 0];
  /**
   * What a character costs the budget at `tier`: its effective tier's
   * triangles, twice where that tier's outline hull is drawn (castle M5c:
   * the hulls count; an unassembled tier is taken as outlined).
   */
  private readonly trianglesOf = (i: number, tier: Tier): number => {
    const v = this.list[i]!;
    const e = v.showing!;
    const t = e.tierOf[tier]!;
    const hull = this.hullsCounted && outlineDrawn(true, t, v.dist) && (e.tiers[t]?.outlined ?? true);
    return drawnTriangles(e.triangles[t]!, hull);
  };
  /** Whether the governor counts outline hulls (outlines on: Medium and High, or the probe's override). */
  private hullsCounted = false;

  constructor(
    private readonly scene: Scene,
    private readonly opts: CharacterRendererOptions,
  ) {
    this.plain = characterMaterial(scene, "characters");
    this.blobs = opts.blobs === false || !CHARACTER_FX.blobs ? null : new BlobShadows(scene);
    if (!CHARACTER_FX.lod) this.rule = LEGACY_RULE;
    scene.onBeforeDrawPhaseObservable.add(this.onBeforeDraw);
    scene.onAfterDrawPhaseObservable.add(this.onAfterDraw);
  }

  /** The quality level's LOD rules (lod.ts). */
  setQuality(q: Quality): void {
    // --- Outlines: the hulls at Medium and High, never at Low (settings.ts `outlines`) ---
    this.outlinesOn = qualityProfile(q, 1).outlines;
    // --- end Outlines ---
    // The face-shadow map at Medium and High (docs/art/character-look.md section 3.4); Low keeps the toon step.
    setToonFaceShadow(q !== "low" && CHARACTER_FX.facesdf);
    this.rule = !CHARACTER_FX.lod ? LEGACY_RULE : CHARACTER_FX.lod0 ? LOD_RULES[q] : { ...LOD_RULES[q], lod0Rank: 0 };
    this.selectClock = SELECT_INTERVAL;
  }

  /** Our own character: always LOD0, rank 0. */
  setSelf(id: number): void {
    this.selfId = id;
  }

  /**
   * The textured material for an atlas and a body's face textures (one per
   * body with a face texture: two in all), decoding each image once.
   */
  private atlasMaterial(img: AtlasImage, face: FaceTextureUrls | null): Promise<AtlasMaterial | null> {
    const key = atlasKey(img) + (face ? `|${face.colour}|${face.mask}` : "");
    let p = this.atlases.get(key);
    if (!p) {
      const faceBitmaps = face ? Promise.all([loadBitmap(face.colour), loadBitmap(face.mask)]).catch((e: unknown) => {
        console.warn("[characters] could not load the face textures; drawing the atlas face:", e);
        return null;
      }) : Promise.resolve(null);
      p = Promise.all([decodeAtlas(img), faceBitmaps]).then(
        ([{ bitmap, whiteUv }, faceImgs]) => {
          const material = characterMaterial(this.scene, `characters-atlas-${this.atlases.size}`);
          // glTF texture coordinates start at the top left: no flip.
          const tex = new Texture(`character-atlas-${key}`, this.scene, { buffer: bitmap, invertY: false, samplingMode: Texture.TRILINEAR_SAMPLINGMODE });
          tex.wrapU = Texture.CLAMP_ADDRESSMODE;
          tex.wrapV = Texture.CLAMP_ADDRESSMODE;
          material.diffuseTexture = tex;
          this.textures.push(tex);
          if (faceImgs && CHARACTER_FX.face) {
            // The face pair (character look 3.1): trilinear, mips, anisotropy 4, clamp.
            const [colour, mask] = faceImgs.map((b, i) => {
              const t = new Texture(`character-face-${i ? "mask" : "colour"}-${key}`, this.scene, { buffer: b, invertY: false, samplingMode: Texture.TRILINEAR_SAMPLINGMODE });
              t.wrapU = Texture.CLAMP_ADDRESSMODE;
              t.wrapV = Texture.CLAMP_ADDRESSMODE;
              t.anisotropicFilteringLevel = 4;
              this.textures.push(t);
              return t;
            }) as [Texture, Texture];
            (material.pluginManager?.getPlugin("Toon") as ToonPlugin | null)?.setFace({ colour, mask }, FACE_SDF_SOFTNESS);
            this.faces.set(material, { colour, mask });
          }
          return { material, whiteUv };
        },
        (e: unknown) => {
          console.warn("[characters] could not decode the character atlas; drawing untextured:", e);
          return null;
        },
      );
      this.atlases.set(key, p);
    }
    return p;
  }

  /** Starts a frame. */
  begin(): void {
    for (const v of this.views.values()) v.shownThisFrame = false;
  }

  /**
   * Draws character `id` this frame: feet at (x, y, z) facing `yaw`, playing
   * `anim` at ground speed `speed` (m/s), dressed as `appearance` (assembled
   * again only when `version` changes). `floorY` is the floor under it when
   * that is not its feet (seated: the seat's floor), for the blob shadow.
   * `floorSeat`: drawn on a floor seat, sitting plays `floor_sit`. `seat`:
   * how far the seat reaches ahead of and behind (x, z) along `yaw` and its
   * top's drop to the floor (castle/seats.ts; NaN or null: unfitted); the
   * hips then slide along it to fit this body's legs and the knees rise
   * where the feet would go through the floor (characters/seated.ts).
   */
  show(id: number, version: number, appearance: Appearance, x: number, y: number, z: number, yaw: number, anim: Anim, speed: number, fx: CharacterFx | null = null, floorY = y, floorSeat = false, seat: SeatFit | null = null): void {
    let v = this.views.get(id);
    const part = fx?.tint?.part ?? null;
    if (!v) {
      v = this.createView(id);
      this.views.set(id, v);
      v.tintPart = part;
      v.root.position.set(x, y, z);
      this.request(v, version, appearance);
    } else if (v.version !== version || v.tintPart !== part) {
      v.tintPart = part;
      this.request(v, version, appearance);
    }
    v.shownThisFrame = true;
    v.lastShown = this.time;
    v.anim = anim;
    v.speed = speed;
    v.yaw = yaw;
    v.floorY = floorY;
    v.seated = floorY !== y;
    v.floorSeat = floorSeat;
    const scale = fx?.scale ?? 1;
    if (scale !== v.fxScale) {
      v.fxScale = scale;
      v.root.scaling.setAll(v.baseScale * scale);
    }
    v.kneeLift = 0;
    if (seat && v.legs && anim === "sit" && !Number.isNaN(seat.ahead) && !Number.isNaN(seat.behind) && seat.drop > 0) {
      const s = v.baseScale * scale;
      const d = seatShift(v.legs, s, seat.ahead, seat.behind, seat.drop);
      x += Math.sin(yaw) * d;
      z += Math.cos(yaw) * d;
      v.kneeLift = kneeLift(v.legs, s, seat.drop);
    }
    v.root.position.set(x, y + (v.style?.lift ?? 0), z);
    v.root.rotation.y = yaw;
    v.tintColour = fx?.tint?.colour ?? null;
    v.animRate = fx ? Math.max(0, Math.min(1, fx.animRate)) : 1;
  }

  /** The seated legs of `kit`'s body in its sitting loop, measured once (null: nothing to measure). */
  private seatedLegs(kit: RigKit): SeatedLegs | null {
    let legs = this.legsOf.get(kit);
    if (legs === undefined) {
      const sit = kit.clips.get("sit");
      legs = sit ? measureSeatedLegs(kit.rig, sit.next ?? sit, kit.body) : null;
      this.legsOf.set(kit, legs);
    }
    return legs;
  }

  /**
   * How `id` is drawn apart from its look (null: as everyone is). Call after
   * its `show`; cheap when unchanged (compared by reference).
   */
  setStyle(id: number, style: CharacterStyle | null): void {
    const v = this.views.get(id);
    if (!v || v.style === style) return;
    const p = v.root.position;
    v.root.position.set(p.x, p.y + (style?.lift ?? 0) - (v.style?.lift ?? 0), p.z);
    v.style = style;
    const base = v.showing?.material;
    if (base) for (const m of v.meshes) if (m) m.material = this.materialFor(v, base);
    this.selectClock = SELECT_INTERVAL;
  }

  /** The material `v` draws with: the shared one, or its style's copy of it. */
  private materialFor(v: View, base: StandardMaterial): Material {
    const style = v.style;
    if (!style?.tweak) return base;
    const key = `${base.uniqueId}|${style.key}`;
    let m = this.variants.get(key);
    if (!m) {
      m = characterMaterial(this.scene, `${base.name}-${style.key}`);
      m.diffuseTexture = base.diffuseTexture;
      const face = this.faces.get(base);
      if (face) (m.pluginManager?.getPlugin("Toon") as ToonPlugin | null)?.setFace(face, FACE_SDF_SOFTNESS);
      style.tweak(m);
      this.variants.set(key, m);
    }
    return m;
  }

  /** Styled materials made so far (the F3 overlay). */
  get styledMaterials(): number {
    return this.variants.size;
  }

  /**
   * Who `id` looks at (an entity id, -1: nobody), or the point `point` instead
   * (our own spell aim). Kept until changed; call after `show`.
   */
  setLook(id: number, target: number, point: { x: number; y: number; z: number } | null = null): void {
    const v = this.views.get(id);
    if (!v) return;
    v.lookTarget = target === id ? -1 : target;
    v.lookHasPoint = !!point;
    if (point) {
      v.lookPoint[0] = point.x;
      v.lookPoint[1] = point.y;
      v.lookPoint[2] = point.z;
    }
  }

  /** Whether `id` is turned toward a look target now (its eased look is on), for tests and the probe. */
  looking(id: number): { yaw: number; pitch: number } | null {
    const v = this.views.get(id);
    return v?.look.active ? { yaw: v.look.yaw, pitch: v.look.pitch } : null;
  }

  /** Where `v` should look this frame, into LOOK_WORLD (the target's eyes as posed, or its point); false: nowhere. */
  private lookTargetPoint(v: View): boolean {
    if (v.lookHasPoint) {
      LOOK_WORLD.set(v.lookPoint);
      return true;
    }
    if (v.lookTarget < 0) return false;
    const t = this.views.get(v.lookTarget);
    if (!t || !t.shownThisFrame || !t.enabled) return false;
    const p = t.root.position;
    const bones = t.lookBones;
    if (t.poseRig && bones) {
      eyeInBody(t.poseRig, t.pose, bones[bones.length - 1]!, LOOK_EYE);
      bodyToWorld(LOOK_EYE[0]!, LOOK_EYE[1]!, LOOK_EYE[2]!, p.x, p.y, p.z, t.yaw, t.root.scaling.x, LOOK_WORLD);
    } else {
      LOOK_WORLD[0] = p.x;
      LOOK_WORLD[1] = p.y + t.height * t.fxScale - 0.1;
      LOOK_WORLD[2] = p.z;
    }
    const q = v.root.position;
    return dist3(p.x, p.y, p.z, q.x, q.y, q.z) <= LOOK_REACH_M;
  }

  /** Turns `v`'s just sampled pose toward its look target, eased over `dt` (look.ts). */
  private poseLook(v: View, dt: number): void {
    const bones = v.lookBones;
    if (!bones || !v.poseRig) return;
    const want = v.dist <= LOOK_DRAW_M && this.lookTargetPoint(v);
    if (!want && !v.look.active) return;
    let yaw = NaN;
    let pitch = 0;
    if (want) {
      const p = v.root.position;
      const s = v.root.scaling.x;
      eyeInBody(v.poseRig, v.pose, bones[bones.length - 1]!, LOOK_EYE);
      worldToBody(LOOK_WORLD[0]!, LOOK_WORLD[1]!, LOOK_WORLD[2]!, p.x, p.y, p.z, v.yaw, s, LOOK_AT);
      anglesTo(LOOK_EYE, LOOK_AT, LOOK_ANGLES);
      yaw = LOOK_ANGLES[0];
      pitch = LOOK_ANGLES[1];
    }
    stepLook(v.look, yaw, pitch, dt);
    if (v.look.active) applyLook(v.poseRig, v.pose, bones, v.look.yaw, v.look.pitch);
  }

  /** Height of the nameplate anchor above the feet for `id` (m), height and potion scale included. */
  plateHeight(id: number): number {
    const v = this.views.get(id);
    return v ? v.height * v.fxScale + PLATE_GAP : 1.75 + PLATE_GAP;
  }

  /** Height of the top of `id`'s head above the feet (m), height and potion scale included. */
  headHeight(id: number): number {
    const v = this.views.get(id);
    return v ? v.height * v.fxScale : 1.75;
  }

  /**
   * Whether `id` holds its wand out apart from casting (a lit wand, a
   * trace in class, an aim: wand-state.ts `holdsWand`). Kept until changed;
   * call after `show`.
   */
  holdWand(id: number, held: boolean): void {
    const v = this.views.get(id);
    if (v) v.wandHeld = held;
  }

  /** Whether `id`'s wand is drawn now (tests and probes). */
  wandOut(id: number): boolean {
    const v = this.views.get(id);
    return !!v?.enabled && v.wandGrow > 0;
  }

  /**
   * Where `id`'s wand tip is in the world, as last posed: the drawn wand's
   * tip in its right hand (Lucerna lights and spell effects start there),
   * out or not. False while the character has no skeleton (not drawn, or
   * still loading).
   */
  wandTip(id: number, out: Vector3): boolean {
    const v = this.views.get(id);
    const fit = v?.enabled ? this.wandFit(v) : null;
    if (!v || !fit) return false;
    // The root moved this frame (`show`); the pose is the last one sampled.
    modelMatrix(v.poseRig!, v.pose, fit.bone, WAND_BONE_M);
    wandTipOf(this.wandRoot(v), WAND_BONE_M, fit.matrix, fit.tip, out);
    return true;
  }

  /** How the wand hangs on `v`'s rig (wands.ts `fit`), or null without a skeleton or a hand bone. */
  private wandFit(v: View): WandFit | null {
    const rig = v.poseRig;
    const kit = v.showing?.kit;
    if (!rig || !kit || !v.skeleton) return null;
    const fit = this.opts.wands ? this.opts.wands.fit(rig, kit.greybox ? null : kit.key) : this.fallbackFit(rig);
    return fit.bone >= 0 ? fit : null;
  }

  /** Without a wand prop (a mirror, a stress scene): the fallback along the hand bone, once per rig. */
  private readonly fallbackFits = new WeakMap<Rig, WandFit>();
  private fallbackFit(rig: Rig): WandFit {
    let f = this.fallbackFits.get(rig);
    if (!f) {
      f = { bone: rig.boneIndex(WAND_HAND), matrix: fallbackMatrix(STAND_IN_TIP), tip: STAND_IN_TIP };
      this.fallbackFits.set(rig, f);
    }
    return f;
  }

  /** Where `v` is drawn, as the wand maths take it (scratch, reused). */
  private wandRoot(v: View): WandRoot {
    const p = v.root.position;
    const r = WAND_ROOT;
    r.x = p.x;
    r.y = p.y;
    r.z = p.z;
    r.yaw = v.yaw;
    r.scale = v.root.scaling.x;
    return r;
  }

  /** Grows or shrinks `v`'s wand by `dt` and, while it is out and near, adds it to this frame's wands. */
  private poseWand(v: View, dt: number): void {
    v.wandGrow = stepWand(v.wandGrow, v.anim, v.wandHeld, dt);
    const wands = this.opts.wands;
    if (!wands || v.wandGrow <= 0 || v.dist > WAND_DRAW_M) return;
    const fit = this.wandFit(v);
    if (!fit) return;
    const o = wands.reserve();
    if (o < 0) return;
    modelMatrix(v.poseRig!, v.pose, fit.bone, WAND_BONE_M);
    wandMatrix(this.wandRoot(v), WAND_BONE_M, fit.matrix, wandSize(v.wandGrow), wands.buffer, o);
  }

  /**
   * Where `id`'s hips are in the world as last posed (a broom's
   * seat goes under them), with the scale it is drawn at. False while the
   * character has no skeleton (not drawn, or still loading).
   */
  hips(id: number, out: Vector3): number {
    const v = this.views.get(id);
    if (!v?.enabled || !v.skeleton || !v.rig) return 0;
    const rig = v.rig;
    let i = this.hipsBone.get(rig);
    if (i === undefined) {
      i = HIPS_BONES.map((b) => rig.boneIndex(b)).find((b) => b >= 0) ?? -1;
      this.hipsBone.set(rig, i);
    }
    const bone = i >= 0 ? v.skeleton.bones[i] : undefined;
    if (!bone) return 0;
    v.root.computeWorldMatrix(true);
    bone.getAbsolutePositionFromLocalToRef(ORIGIN, v.root, out);
    return v.root.scaling.x;
  }

  /** Whether `id` is drawn with its assembled mesh yet. */
  isReady(id: number): boolean {
    const v = this.views.get(id);
    return !!v?.showing && v.drawn >= 0;
  }

  /** The LOD tier `id` draws now (-1 before it is drawn). */
  tierOf(id: number): number {
    return this.views.get(id)?.drawn ?? -1;
  }

  /** Forgets every character (a new connection: ids mean other people now). Assembled looks stay cached. */
  forgetCharacters(): void {
    for (const v of [...this.views.values()]) this.disposeView(v);
  }

  /** Forgets `id` now (e.g. it left the visible set). */
  remove(id: number): void {
    const v = this.views.get(id);
    if (v) this.disposeView(v);
  }

  private createView(id: number): View {
    const root = new TransformNode(`character-${id}`, this.scene);
    root.setEnabled(false);
    return {
      id,
      version: -1,
      want: null,
      showing: null,
      root,
      meshes: [null, null, null, null],
      hulls: [null, null, null, null],
      hullOn: -1,
      drape: new Float32Array(4),
      drawn: -1,
      tier: id === this.selfId ? 0 : 1,
      skeleton: null,
      rigKey: "",
      rig: null,
      poseRig: null,
      sway: null,
      swayState: swayState(),
      animator: null,
      pose: new Float32Array(0),
      height: 1.75,
      baseScale: 1,
      fxScale: 1,
      tintPart: null,
      tintColour: null,
      animRate: 1,
      lastShown: this.time,
      shownThisFrame: false,
      poseClock: 0,
      anim: "idle",
      speed: 0,
      enabled: false,
      yaw: 0,
      floorY: 0,
      seated: false,
      floorSeat: false,
      legs: null,
      legBones: null,
      kneeLift: 0,
      dist: 0,
      rank: 0,
      shadowed: false,
      lookTarget: -1,
      lookHasPoint: false,
      lookPoint: new Float64Array(3),
      look: lookState(),
      lookBones: null,
      style: null,
      wandHeld: false,
      wandGrow: 0,
    };
  }

  private request(v: View, version: number, a: Appearance): void {
    v.version = version;
    const recipe = recipeFor(a);
    // A recolourable copy is the view's own (its colour steps are its own).
    const part = v.tintPart;
    const key = `${recipe.body.id}#${appearanceKey(a)}${part ? `#tint:${part}@${v.id}` : ""}`;
    v.want = key;
    v.height = heightMetres(a);
    v.baseScale = recipe.scale;
    v.root.scaling.setAll(recipe.scale * v.fxScale);
    const known = this.geometries.get(key);
    if (known) {
      this.apply(v, known);
      return;
    }
    if (this.building.has(key)) return; // `prepared` applies it to every view that wants it
    const job = (async () => {
      const kit = await this.opts.library.rig(recipe.body);
      const parts = await this.opts.library.parts(recipe, kit);
      const img = parts.find((p) => p.part.atlas)?.part.atlas ?? null;
      const atlas = img ? await this.atlasMaterial(img, kit.face) : null;
      if (this.disposed) return;
      this.prepared(key, recipe, kit, parts, atlas?.material ?? this.plain, atlas?.whiteUv ?? null, part ? trackFor(part) : null, part ? v.id : null);
    })().catch((e: unknown) => {
      console.error("[characters] could not prepare a character:", e);
      this.building.delete(key);
    });
    this.building.set(key, job);
  }

  /** A look's parts are loaded: its entry exists from now on, tiers assemble on demand. */
  private prepared(key: string, recipe: Recipe, kit: RigKit, parts: PartInput[], material: StandardMaterial, whiteUv: [number, number] | null, track: TrackedChannel[] | null, owner: number | null): void {
    this.building.delete(key);
    // A recolourable copy its view no longer wants (the potion wore off while it loaded).
    if (owner !== null && this.views.get(owner)?.want !== key) return;
    const hair = parts.find((p) => p.role === "hair")?.part;
    const hairReq = recipe.parts.find((p) => p.role === "hair");
    const hairKey = hairReq ? `${hairReq.asset}${hairReq.cap ? "_cap" : ""}` : "";
    const sway = !kit.greybox && hair && CHARACTER_FX.sway ? extendRig(kit.rig, kit.key, hair, hairKey) : null;
    const tierOf = new Uint8Array(TIER_COUNT);
    let last = 0;
    for (let t = 0; t < TIER_COUNT; t++) {
      if (t > 0 && hasLod(parts, t)) last = t;
      tierOf[t] = last;
    }
    const entry: GeometryEntry = {
      key,
      kit,
      sway,
      rig: sway?.rig ?? kit.rig,
      rigKey: sway?.key ?? kit.key,
      recipe,
      parts,
      material,
      whiteUv,
      track,
      tiers: [null, null, null, null],
      tierOf,
      triangles: estimateTriangles(parts, tierOf),
      queued: new Uint8Array(TIER_COUNT),
      refs: 0,
      vertices: 0,
      lastUsed: this.time,
      owner,
      tintApplied: null,
      liveHairSlot: track?.some((t) => t.role === "hair") && !kit.greybox ? claimLiveHairSlot(recipe.hairIndex) : -1,
    };
    this.geometries.set(key, entry);
    for (const v of this.views.values()) if (v.want === key) this.apply(v, entry);
  }

  /** Queues the assembly of `tier` (its effective tier) of `entry` unless it exists or is queued. */
  private need(entry: GeometryEntry, tier: number): void {
    const t = entry.tierOf[tier]!;
    if (entry.tiers[t] || entry.queued[t]) return;
    entry.queued[t] = 1;
    this.queue.push({ entry, tier: t });
  }

  /** Assembles one tier of a look and hands it to every view waiting for it. */
  private assembleTier(entry: GeometryEntry, t: number): void {
    entry.queued[t] = 0;
    if (this.geometries.get(entry.key) !== entry || entry.tiers[t]) return;
    const start = performance.now();
    const merged = assemble({
      rig: entry.rig,
      parts: entry.parts,
      lod: t,
      skin: entry.recipe.skin,
      hair: entry.recipe.hair,
      eye: entry.recipe.eye,
      eyeIndex: entry.recipe.eyeIndex,
      hairIndex: entry.liveHairSlot >= 0 ? entry.liveHairSlot : entry.recipe.hairIndex,
      shapeWeights: entry.recipe.shapeWeights,
      whiteUv: entry.whiteUv,
      track: entry.track ?? undefined,
    });
    if (merged.unknownBones.length) console.warn(`[characters] bones missing from the ${entry.rigKey} rig:`, merged.unknownBones);
    const n = this.geometrySerial++;
    const tint = merged.tint ? this.tintLod(merged) : null;
    const geometry = this.geometry(`character-geometry-${n}-lod${t}`, merged, tint?.buffer);
    const keeper = new Mesh(`character-keeper-${n}`, this.scene);
    geometry.applyToMesh(keeper);
    keeper.setEnabled(false);
    keeper.isPickable = false;
    entry.tiers[t] = {
      geometry,
      keeper,
      vertices: merged.vertexCount,
      triangles: merged.indices.length / 3,
      tint,
      outlined: hasOutline(merged.outline),
      draped: DRAPE_KEYS.some((k) => merged.drape[k]),
    };
    entry.triangles[t] = merged.indices.length / 3;
    entry.vertices += merged.vertexCount;
    if (tint && entry.tintApplied) {
      recolour(tint.split, entry.tintApplied, tint.bytes);
      tint.buffer.update(tint.bytes);
    }
    this.assemblyMs[t] = performance.now() - start;
    for (const v of this.views.values()) if (v.showing === entry) this.applyTier(v);
  }

  /** The updatable colour buffer and split of a recolourable geometry. */
  private tintLod(m: MergedGeometry): TintLod {
    const bytes = new Uint8Array(m.colors);
    const buffer = new VertexBuffer(this.scene.getEngine(), bytes, VertexBuffer.ColorKind, { updatable: true, size: 4, type: VertexBuffer.UNSIGNED_BYTE, normalized: true });
    return { split: m.tint!, bytes, buffer };
  }

  /** Rewrites a recolourable geometry's colours for `colour`, if it changed. */
  private recolourEntry(entry: GeometryEntry, colour: Rgb): void {
    const a = entry.tintApplied;
    if (a && a[0] === colour[0] && a[1] === colour[1] && a[2] === colour[2]) return;
    if (a) {
      a[0] = colour[0];
      a[1] = colour[1];
      a[2] = colour[2];
    } else entry.tintApplied = [colour[0], colour[1], colour[2]];
    if (entry.liveHairSlot >= 0) setLiveHairColour(entry.liveHairSlot, colour[0], colour[1], colour[2]);
    for (const tier of entry.tiers) {
      const t = tier?.tint;
      if (!t) continue;
      recolour(t.split, colour, t.bytes);
      t.buffer.update(t.bytes);
    }
  }

  private geometry(id: string, m: MergedGeometry, colours?: VertexBuffer): Geometry {
    const engine = this.scene.getEngine();
    const g = new Geometry(id, this.scene);
    g.setVerticesBuffer(new VertexBuffer(engine, m.positions, VertexBuffer.PositionKind, { updatable: false, size: 3 }), m.vertexCount);
    g.setVerticesBuffer(new VertexBuffer(engine, m.normals, VertexBuffer.NormalKind, { updatable: false, size: 3 }));
    if (m.uvs) g.setVerticesBuffer(new VertexBuffer(engine, m.uvs, VertexBuffer.UVKind, { updatable: false, size: 2 }));
    g.setVerticesBuffer(colours ?? new VertexBuffer(engine, m.colors, VertexBuffer.ColorKind, { updatable: false, size: 4, type: VertexBuffer.UNSIGNED_BYTE, normalized: true }));
    // The shade layer: 4 normalised bytes per vertex (toon/material.ts).
    if (CHARACTER_FX.shade) g.setVerticesBuffer(new VertexBuffer(engine, m.shade, TOON_SHADE_KIND, { updatable: false, size: 4, type: VertexBuffer.UNSIGNED_BYTE, normalized: true }));
    // Outlines: the hull's smoothed normal and width weight (toon/outline.ts), 4 bytes per vertex.
    g.setVerticesBuffer(new VertexBuffer(engine, m.outline, OUTLINE_KIND, { updatable: false, size: 4, type: VertexBuffer.BYTE, normalized: true }));
    g.setVerticesBuffer(
      new VertexBuffer(engine, m.joints, VertexBuffer.MatricesIndicesKind, {
        updatable: false,
        size: 4,
        type: m.joints instanceof Uint8Array ? VertexBuffer.UNSIGNED_BYTE : VertexBuffer.UNSIGNED_SHORT,
        normalized: false,
      }),
    );
    g.setVerticesBuffer(new VertexBuffer(engine, m.weights, VertexBuffer.MatricesWeightsKind, { updatable: false, size: 4, type: VertexBuffer.UNSIGNED_BYTE, normalized: true }));
    // The sitting drape (skirts and robes): all keys or none, zero where a key is missing.
    const drape = DRAPE_KEYS.map((k) => m.drape[k]);
    if (drape.some((d) => d)) {
      let zero: Float32Array | null = null;
      drape.forEach((d, i) => {
        const data = d ?? (zero ??= new Float32Array(m.vertexCount * 3));
        g.setVerticesBuffer(new VertexBuffer(engine, data, TOON_DRAPE_KINDS[i]!, { updatable: false, size: 3 }));
      });
    }
    g.setIndices(m.indices, m.vertexCount);
    return g;
  }

  /** Makes `v` show `entry`, rebuilding its skeleton when the rig changed. */
  private apply(v: View, entry: GeometryEntry): void {
    if (v.showing === entry) return;
    entry.refs++;
    entry.lastUsed = this.time;
    // Released once the meshes are rebuilt: a view's own copy is disposed on release.
    const previous = v.showing;
    v.showing = entry;
    const rigChanged = !v.skeleton || v.rigKey !== entry.rigKey;
    if (rigChanged) {
      v.skeleton?.dispose();
      v.skeleton = buildSkeleton(this.scene, `${v.id}:${entry.rigKey}`, entry.rig);
      v.rigKey = entry.rigKey;
      v.rig = entry.rig;
      v.pose = new Float32Array(entry.rig.count * 16);
      // The rest pose until the first sample (sway bones keep theirs at LOD2-3).
      samplePose(entry.rig, [], v.pose);
      resetSway(v.swayState);
      const clips = entry.kit.clips;
      if (v.animator) v.animator.setClips(clips);
      else v.animator = new Animator(clips, (Math.abs(v.id) * 0.6180339887) % 1);
    }
    if (v.poseRig !== entry.kit.rig) {
      v.legs = this.seatedLegs(entry.kit);
      v.legBones = legBones(entry.kit.rig);
      const rig = entry.kit.rig;
      let lb = this.lookBonesOf.get(rig);
      if (lb === undefined) {
        lb = lookBones(rig);
        this.lookBonesOf.set(rig, lb);
      }
      v.lookBones = lb;
      resetLook(v.look);
    }
    v.poseRig = entry.kit.rig;
    v.sway = entry.sway;
    // A new look gets new meshes: re-pointing an existing mesh at another
    // geometry keeps its old submesh ranges and vertex array objects, which
    // drew a mix of the two looks (half the triangles of the new trousers
    // showed the robe behind them).
    for (let i = 0; i < TIER_COUNT; i++) {
      const m = v.meshes[i];
      if (m) this.disposeMesh(m);
      v.meshes[i] = null;
    }
    this.disposeHulls(v);
    v.drawn = -1;
    v.tier = v.id === this.selfId ? 0 : this.quickTier(v);
    this.applyTier(v);
    if (previous) this.release(previous);
  }

  /** A first guess at a new character's tier from its distance alone (the next selection refines it). */
  private quickTier(v: View): Tier {
    const p = v.root.position;
    const e = this.lastEye;
    const d = Math.hypot(p.x - e.x, p.y - e.y, p.z - e.z);
    return tierFor(this.rule, Infinity, d, 3, v.id === this.selfId);
  }

  /**
   * Draws `v.tier` when its geometry is assembled, else queues it and keeps
   * drawing what it has (or, with nothing yet, the coarsest ready tier).
   */
  private applyTier(v: View): void {
    const e = v.showing;
    if (!e) return;
    const want = e.tierOf[v.tier]!;
    let use = -1;
    if (e.tiers[want]) use = want;
    else {
      this.need(e, want);
      if (v.drawn >= 0 && e.tiers[v.drawn]) use = v.drawn;
      else for (let t = TIER_COUNT - 1; t >= 0 && use < 0; t--) if (e.tiers[t]) use = t;
    }
    if (use < 0 || use === v.drawn) return;
    let mesh = v.meshes[use];
    if (!mesh) {
      mesh = new Mesh(`character-${v.id}-lod${use}`, this.scene, v.root);
      mesh.isPickable = false;
      mesh.numBoneInfluencers = 4;
      e.tiers[use]!.geometry.applyToMesh(mesh);
      mesh.material = this.materialFor(v, e.material);
      mesh.skeleton = v.skeleton;
      mesh.metadata = { ...(mesh.metadata as object | null), toonDrape: v.drape };
      v.meshes[use] = mesh;
      this.opts.onMesh?.(mesh, true);
      this.opts.shadows?.addReceiver(mesh, false);
    }
    for (let i = 0; i < TIER_COUNT; i++) v.meshes[i]?.setEnabled(i === use);
    v.drawn = use;
  }

  private release(entry: GeometryEntry): void {
    entry.refs--;
    entry.lastUsed = this.time;
    if (entry.owner !== null && entry.refs <= 0) this.disposeGeometry(entry);
  }

  /** Per frame, after the `show` calls: visibility, LOD, posing, sway, shadows, assembly. */
  update(dt: number, eye: { x: number; y: number; z: number }): void {
    this.time += dt;
    this.updateKeyDirection();
    this.updateOutlineMaterial(eye);
    this.lastEye.x = eye.x;
    this.lastEye.y = eye.y;
    this.lastEye.z = eye.z;
    let hidden = 0;
    const list = this.list;
    list.length = 0;
    this.opts.wands?.begin();
    for (const v of this.views.values()) {
      if (!v.shownThisFrame) {
        v.wandGrow = 0;
        if (v.enabled) {
          v.root.setEnabled(false);
          v.enabled = false;
        }
        if (this.time - v.lastShown > FORGET_AFTER) this.disposeView(v);
        else hidden++;
        continue;
      }
      const ready = !!v.showing && v.drawn >= 0 && !!v.skeleton;
      if (v.enabled !== ready) {
        v.root.setEnabled(ready);
        v.enabled = ready;
      }
      if (!ready) {
        v.wandGrow = 0;
        continue;
      }
      list.push(v);
      if (v.showing!.track && v.tintColour) this.recolourEntry(v.showing!, v.tintColour);
      const p = v.root.position;
      v.dist = dist3(p.x, p.y, p.z, eye.x, eye.y, eye.z);
    }

    this.selectClock += dt;
    if (this.selectClock >= SELECT_INTERVAL) {
      this.selectClock = 0;
      this.select();
    }

    const blobs = this.blobs;
    blobs?.begin();
    const casters = this.casterMeshes;
    casters.length = 0;
    let cx = 0;
    let cy = 0;
    let cz = 0;
    for (const v of list) {
      const animator = v.animator!;
      animator.set(v.anim !== "sit" ? v.anim : v.floorSeat ? "floor_sit" : v.seated ? "sit" : "sit_floor", v.speed);
      v.poseClock += dt;
      const d = v.dist;
      const interval = d < LOD_DISTANCE ? 0 : d < POSE_FAR ? POSE_INTERVAL_MID : POSE_INTERVAL_FAR;
      if (v.poseClock >= interval) {
        const step = v.poseClock;
        // Spells L20: bound, the clips stand still (the pose freezes); slowed, slow motion.
        animator.update(step * v.animRate);
        v.poseClock = 0;
        samplePose(v.poseRig!, animator.layers, v.pose, POSE_SCRATCH);
        animator.drapeWeights(v.drape);
        // Seats fit the sitter's legs: the feet rest on the floor, the knees risen.
        if (v.kneeLift > 0 && v.legBones && v.anim === "sit" && v.seated && !v.floorSeat) liftKnees(v.poseRig!, v.pose, v.legBones, v.kneeLift);
        // Looking at a target: the head, neck and chest turn on top of the clip, before the hair sways.
        this.poseLook(v, step);
        if (v.sway && v.drawn <= 1) {
          const r = SWAY_ROOT;
          r.x = v.root.position.x;
          r.y = v.root.position.y;
          r.z = v.root.position.z;
          r.yaw = v.yaw;
          r.scale = v.root.scaling.x;
          applySway(v.sway, v.pose, v.swayState, r, step, this.time, (Math.abs(v.id) * 0.37) % 1);
        }
        applyPose(v.skeleton!, v.pose);
      }
      this.poseWand(v, dt);
      this.updateHull(v);
      const p = v.root.position;
      const m = v.shadowed ? v.meshes[v.drawn] : null;
      if (v.style && !v.style.shadow) {
        // No shadow of any kind (a ghost, a portrait's subject).
      } else if (m) {
        casters.push(m);
        cx += p.x;
        cy += v.floorY;
        cz += p.z;
      } else if (v.anim !== "fly" && v.anim !== "hover") blobs?.add(p.x, v.floorY, p.z, v.yaw, v.baseScale * v.fxScale); // no blob in mid-air
    }
    blobs?.commit();
    this.opts.wands?.commit();
    if (this.opts.shadows) {
      const k = Math.max(1, casters.length);
      this.opts.shadows.setCasters(casters, casters.length, cx / k, cy / k, cz / k);
    }

    if (hidden > MAX_HIDDEN) this.forgetHidden(hidden - MAX_HIDDEN);

    const start = performance.now();
    let done = 0;
    while (this.queue.length && done < MAX_ASSEMBLIES_PER_FRAME) {
      const job = this.queue.shift()!;
      this.assembleTier(job.entry, job.tier);
      done++;
      if (performance.now() - start > ASSEMBLY_BUDGET_MS) break;
    }
    this.evictSpareGeometries();
  }

  /**
   * The 4 Hz selection: ranks by distance (us first), each character's tier
   * by the quality's rules, the triangle budget, and who casts into the
   * shadow map. Allocation-free once the scratch arrays are big enough.
   */
  private select(): void {
    const list = this.list;
    const n = list.length;
    if (this.order.length < n) {
      const size = Math.max(n, this.order.length * 2);
      this.order = new Int32Array(size);
      this.dists = new Float64Array(size);
      this.shadowOrder = new Int32Array(size);
      this.shadowDists = new Float64Array(size);
      this.tierScratch = new Uint8Array(size);
    }
    const order = this.order;
    const dists = this.dists;
    let selfIndex = -1;
    for (let i = 0; i < n; i++) {
      const v = list[i]!;
      if (v.id === this.selfId) selfIndex = i;
      dists[i] = v.id === this.selfId ? -1 : v.dist;
      // Insertion sort by distance (a few hundred at most).
      let k = i;
      while (k > 0 && dists[order[k - 1]!]! > dists[i]!) {
        order[k] = order[k - 1]!;
        k--;
      }
      order[k] = i;
    }
    const tiers = this.tierScratch;
    for (let k = 0; k < n; k++) {
      const i = order[k]!;
      const v = list[i]!;
      v.rank = k;
      tiers[i] = tierFor(this.rule, k, v.dist, v.tier, i === selfIndex);
      if (v.style && v.dist > v.style.coarseBeyond) tiers[i] = TIER_COUNT - 1;
    }
    // Castle D1 (docs/art/castle-kit.md): the characters get what the
    // world in view leaves of the quality's total, the nearest three kept.
    this.hullsCounted = OUTLINE_OVERRIDE.on ?? this.outlinesOn;
    this.allowance = characterAllowance(this.rule.total, this.measureWorld(), characterFloor(this.hullsCounted));
    governTiers(tiers, order, n, PROTECT_NEAREST, this.allowance, this.trianglesOf);
    // Casters: the nearest to our own character (the follow camera stands a
    // few metres behind it, and characters behind the camera cast nothing
    // seen), within the map's reach of the camera; without us, the nearest
    // to the camera.
    const sh = this.opts.shadows;
    const maxCasters = sh?.maxCasters ?? 0;
    const maxDist = sh?.maxDistance ?? 0;
    const so = this.shadowOrder;
    const sd = this.shadowDists;
    const me = selfIndex >= 0 ? list[selfIndex]!.root.position : null;
    for (let i = 0; i < n; i++) {
      const v = list[i]!;
      const p = v.root.position;
      sd[i] = me ? dist3(p.x, p.y, p.z, me.x, me.y, me.z) : v.dist;
      let k = i;
      while (k > 0 && sd[so[k - 1]!]! > sd[i]!) {
        so[k] = so[k - 1]!;
        k--;
      }
      so[k] = i;
    }
    for (let k = 0; k < n; k++) {
      const v = list[so[k]!]!;
      v.shadowed = k < maxCasters && v.dist <= maxDist && v.style?.shadow !== false;
    }
    for (let k = 0; k < n; k++) {
      const i = order[k]!;
      const v = list[i]!;
      const t = tiers[i]! as Tier;
      if (t !== v.tier) {
        v.tier = t;
        this.applyTier(v);
      }
    }
  }

  /**
   * World triangles in view, the largest over the last `WORLD_WINDOW` ticks:
   * the indices the last frame's draw phase drew (Babylon counts the LOD
   * level each mesh draws, after frustum culling; shadow-map and other
   * render-target passes before it are left out) less the skinned meshes'
   * (the characters). Allocation-free.
   */
  private measureWorld(): number {
    const active = this.scene.getActiveMeshes();
    let skinned = 0;
    for (let k = 0; k < active.length; k++) {
      const m = active.data[k]!;
      if (m.skeleton) skinned += m.getTotalIndices();
    }
    const world = Math.max(0, (this.drawPhaseIndices - skinned) / 3);
    const ticks = this.worldTicks;
    ticks[this.worldTick] = world;
    this.worldTick = (this.worldTick + 1) % ticks.length;
    let max = 0;
    for (let k = 0; k < ticks.length; k++) if (ticks[k]! > max) max = ticks[k]!;
    this.worldMax = max;
    return max;
  }

  /** Disposes the `n` characters hidden longest. Rare: only past `MAX_HIDDEN`. */
  private forgetHidden(n: number): void {
    const hidden = [...this.views.values()].filter((v) => !v.shownThisFrame).sort((a, b) => a.lastShown - b.lastShown);
    for (const v of hidden.slice(0, n)) this.disposeView(v);
  }

  private evictSpareGeometries(): void {
    // Counted first, so the usual frame (nothing to evict) allocates nothing.
    let unused = 0;
    for (const g of this.geometries.values()) if (g.refs <= 0) unused++;
    if (unused <= SPARE_GEOMETRIES) return;
    const spare = [...this.geometries.values()].filter((g) => g.refs <= 0);
    spare.sort((a, b) => a.lastUsed - b.lastUsed);
    for (const g of spare.slice(0, spare.length - SPARE_GEOMETRIES)) this.disposeGeometry(g);
  }

  private disposeGeometry(g: GeometryEntry): void {
    if (this.geometries.get(g.key) !== g) return; // already gone
    this.geometries.delete(g.key);
    releaseLiveHairSlot(g.liveHairSlot);
    g.liveHairSlot = -1;
    for (const t of g.tiers) {
      if (!t) continue;
      t.keeper.dispose();
      t.geometry.dispose();
    }
    g.tiers.fill(null);
  }

  private disposeMesh(m: Mesh): void {
    this.opts.onMesh?.(m, false);
    this.opts.shadows?.removeReceiver(m);
    m.dispose(false, false);
  }

  private disposeView(v: View): void {
    this.views.delete(v.id);
    this.disposeHulls(v);
    for (const m of v.meshes) if (m) this.disposeMesh(m);
    if (v.showing) this.release(v.showing);
    v.skeleton?.dispose();
    v.root.dispose();
  }

  get stats(): CharacterStats {
    let visible = 0;
    let recoloured = 0;
    let triangles = 0;
    let swaying = 0;
    let casters = 0;
    let outlined = 0;
    const tiers: [number, number, number, number] = [0, 0, 0, 0];
    for (const v of this.views.values()) {
      if (!v.enabled || v.drawn < 0) continue;
      visible++;
      tiers[v.tier]++;
      // With the outline hull's copy where one is drawn (the governor counts it too).
      triangles += drawnTriangles(v.showing?.tiers[v.drawn]?.triangles ?? 0, v.hullOn >= 0);
      if (v.showing?.track) recoloured++;
      if (v.sway && v.drawn <= 1) swaying++;
      if (v.shadowed) casters++;
      if (v.hullOn >= 0) outlined++;
    }
    let vertices = 0;
    for (const g of this.geometries.values()) vertices += g.vertices;
    const lib = this.opts.library.stats;
    return {
      characters: this.views.size,
      visible,
      tiers,
      lod1: tiers[1] + tiers[2] + tiers[3],
      triangles,
      geometries: this.geometries.size,
      vertices,
      pending: this.building.size + this.queue.length,
      partFiles: lib.files,
      missingFiles: lib.missing,
      recoloured,
      swaying,
      shadowCasters: casters,
      blobs: this.blobs?.size ?? 0,
      assemblyMs: [...this.assemblyMs] as [number, number, number, number],
      worldInView: this.worldMax,
      allowance: this.allowance,
      outlined,
    };
  }

  // --- Outlines: the inverted-hull outlines (toon/outline.ts) ---

  /** Outlines at this quality (settings.ts `outlines`; Medium and High). */
  private outlinesOn = true;
  private outlineMat: OutlineMaterial | null = null;
  /** The hull for characters wearing a skirt or robe (the sitting drape's attributes). */
  private outlineDrapeMat: OutlineMaterial | null = null;
  private readonly lineLight = [1, 1, 1];

  private outlineMaterial(draped = false): OutlineMaterial {
    if (draped) return (this.outlineDrapeMat ??= new OutlineMaterial(this.scene, undefined, true));
    this.outlineMat ??= new OutlineMaterial(this.scene);
    return this.outlineMat;
  }

  /** Shows the hull of the tier `v` draws when it should have one, else none. Allocation-free once created. */
  private updateHull(v: View): void {
    const on = OUTLINE_OVERRIDE.on ?? this.outlinesOn;
    const tier = v.drawn;
    const geo = tier >= 0 ? v.showing?.tiers[tier] : null;
    const want = geo?.outlined && v.style?.outline !== false && outlineDrawn(on, tier, v.dist) ? tier : -1;
    if (want === v.hullOn) return;
    if (v.hullOn >= 0) v.hulls[v.hullOn]?.setEnabled(false);
    v.hullOn = want;
    if (want < 0) return;
    let h = v.hulls[want];
    if (!h) {
      h = new Mesh(`outline-${v.id}-lod${want}`, this.scene, v.root);
      h.isPickable = false;
      h.numBoneInfluencers = 4;
      geo!.geometry.applyToMesh(h);
      h.material = this.outlineMaterial(geo!.draped);
      h.skeleton = v.skeleton;
      h.metadata = { toonDrape: v.drape };
      v.hulls[want] = h;
    }
    h.setEnabled(true);
  }

  private disposeHulls(v: View): void {
    for (let i = 0; i < TIER_COUNT; i++) {
      v.hulls[i]?.dispose(false, false);
      v.hulls[i] = null;
    }
    v.hullOn = -1;
  }

  /**
   * Once a frame: the hull material's camera, render size and light (the
   * scene's first enabled hemisphere fill and lighting directional key; full
   * light without them). No allocation.
   */
  private updateOutlineMaterial(eye: { x: number; y: number; z: number }): void {
    const mat = this.outlineMat ?? this.outlineDrapeMat;
    if (!mat) return;
    const lights = this.scene.lights;
    let fill: Light | null = null;
    let key: Light | null = null;
    for (let i = 0; i < lights.length; i++) {
      const l = lights[i]!;
      if (!l.isEnabled()) continue;
      const type = l.getTypeID();
      if (type === 3 && !fill) fill = l;
      else if (type === 1 && l.range !== CHARACTER_SHADOW_RANGE && !key) key = l;
    }
    const light = this.lineLight;
    if (fill || key) {
      const fd = fill?.diffuse;
      const kd = key?.diffuse;
      const fc = LINE_FILL;
      const kc = LINE_KEY;
      fc[0] = fd?.r ?? 0;
      fc[1] = fd?.g ?? 0;
      fc[2] = fd?.b ?? 0;
      kc[0] = kd?.r ?? 0;
      kc[1] = kd?.g ?? 0;
      kc[2] = kd?.b ?? 0;
      outlineLight(fc, fill?.intensity ?? 0, kc, key?.intensity ?? 0, undefined, light);
    } else light.fill(1);
    const engine = this.scene.getEngine();
    mat.update(eye.x, eye.y, eye.z, engine.getRenderWidth(), engine.getRenderHeight(), light, toonOutlineMask(this.scene));
    if (this.outlineDrapeMat && this.outlineDrapeMat !== mat)
      this.outlineDrapeMat.update(eye.x, eye.y, eye.z, engine.getRenderWidth(), engine.getRenderHeight(), light, toonOutlineMask(this.scene));
  }
  // --- end Outlines ---

  /**
   * The face shadow's key light (toon/material.ts `setToonKeyDirection`): the
   * scene's first enabled directional light that lights (the sun or moon; the
   * creator's key), not the character shadow light. Once a frame, no allocation.
   */
  private updateKeyDirection(): void {
    const lights = this.scene.lights;
    for (let i = 0; i < lights.length; i++) {
      const l = lights[i]!;
      if (l.getTypeID() !== 1 || l.range === CHARACTER_SHADOW_RANGE || !l.isEnabled()) continue;
      const d = (l as unknown as { direction: Vector3 }).direction;
      setToonKeyDirection(-d.x, -d.y, -d.z);
      return;
    }
  }

  dispose(): void {
    this.disposed = true;
    this.outlineMat?.dispose();
    this.outlineDrapeMat?.dispose();
    this.scene.onBeforeDrawPhaseObservable.removeCallback(this.onBeforeDraw);
    this.scene.onAfterDrawPhaseObservable.removeCallback(this.onAfterDraw);
    for (const v of [...this.views.values()]) this.disposeView(v);
    for (const g of [...this.geometries.values()]) this.disposeGeometry(g);
    this.queue.length = 0;
    this.blobs?.dispose();
    this.opts.wands?.dispose();
    for (const m of this.variants.values()) m.dispose(false, false);
    this.variants.clear();
    this.plain.dispose();
    for (const t of this.textures) t.dispose();
    // Atlas materials still decoding are dropped with the scene.
    for (const p of this.atlases.values()) void p.then((a) => a?.material.dispose());
  }
}

/** Outlines: whether an outline layer has any width (its w bytes). */
export function hasOutline(outline: Int8Array): boolean {
  for (let i = 3; i < outline.length; i += 4) if (outline[i]! > 0) return true;
  return false;
}

/** Scratch for the line's light (no allocation per frame). */
const LINE_FILL = [0, 0, 0];
const LINE_KEY = [0, 0, 0];

/** Triangles per tier of a look before assembly: its parts' primitives at each tier, hidden regions left out. */
export function estimateTriangles(parts: readonly PartInput[], tierOf: Uint8Array): Float64Array {
  const hidden = hiddenRegions(parts);
  const out = new Float64Array(TIER_COUNT);
  for (let t = 0; t < TIER_COUNT; t++) {
    let n = 0;
    for (const p of parts) {
      const level = lodFor(p.part, tierOf[t]!);
      for (const prim of p.part.primitives) {
        if (prim.lod !== level) continue;
        if (p.role === "base" && prim.region && hidden.has(prim.region)) continue;
        n += prim.indices.length / 3;
      }
    }
    out[t] = n;
  }
  return out;
}

/**
 * The character material: vertex colours (tints) times the atlas, lit by
 * the toon model with a ramp row per region (toon/material.ts; the region
 * and the garment finish share the vertex colours' alpha, the shade layer
 * comes in `toonShade`), and garment finishes on top (finish.ts).
 * Characters receive the character shadow map (Medium, High; never on the
 * face) but not the sun's (High): indoors the castle's roof would put every
 * character in its shadow.
 */
/** An image as an unpremultiplied bitmap (the face mask's alpha must not touch its colour channels). */
async function loadBitmap(url: string): Promise<ImageBitmap> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return createImageBitmap(await res.blob(), { premultiplyAlpha: "none", colorSpaceConversion: "none" });
}

function characterMaterial(scene: Scene, name: string): StandardMaterial {
  const mat = new StandardMaterial(name, scene);
  new ToonPlugin(mat, "regions");
  new CharacterFinishPlugin(mat);
  mat.diffuseColor = Color3.White();
  // Highlights come from the toon rows (metal, hair), not Blinn-Phong.
  mat.specularColor = Color3.Black();
  // Lit by a dorm's lamps and fire too (zones-scene.ts uses up to 6 plus the
  // sky fill), plus the character shadow light; Babylon's default of 4 left
  // characters black in a room.
  mat.maxSimultaneousLights = 9;
  // glTF front faces wind counter-clockwise; Babylon's default assumes the
  // left-handed convention unless told (its glTF loader does the same).
  mat.sideOrientation = scene.useRightHandedSystem ? Material.CounterClockWiseSideOrientation : Material.ClockWiseSideOrientation;
  // Not frozen: shadow defines must be able to recompile it.
  return mat;
}

/**
 * A Babylon skeleton for one character on `rig`. Bind matrices come from the
 * rig's inverse bind matrices, so skinning matches the files exactly.
 */
function buildSkeleton(scene: Scene, name: string, rig: Rig): Skeleton {
  const skel = new Skeleton(name, name, scene);
  const data = rig.data;
  const absBind = new Float32Array(16);
  const local = new Float32Array(16);
  const bones: Bone[] = [];
  const rest = new Float32Array(rig.count * 16);
  samplePose(rig, [], rest); // the rest pose as local matrices
  for (let i = 0; i < rig.count; i++) {
    const p = rig.parents[i]!;
    // bindLocal = parentInverseBind · inverse(inverseBind): glTF maths, Babylon's memory layout.
    invert4(data.inverseBind.subarray(i * 16, i * 16 + 16), absBind);
    if (p >= 0) mul4(data.inverseBind, absBind, local, p * 16, 0, 0);
    else local.set(absBind);
    const bindM = Matrix.FromArray(local);
    const restM = Matrix.FromArray(rest, i * 16);
    bones.push(new Bone(rig.bones[i]!, skel, p >= 0 ? bones[p]! : null, restM, restM.clone(), bindM, i));
  }
  return skel;
}

function applyPose(skel: Skeleton, pose: Float32Array): void {
  const bones = skel.bones;
  for (let i = 0; i < bones.length; i++) Matrix.FromArrayToRef(pose, i * 16, bones[i]!.getLocalMatrix());
  bones[0]?.markAsDirty();
}
