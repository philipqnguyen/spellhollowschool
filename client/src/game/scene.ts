// Engine, scene, lights, sky and fog, plus the quality levels (resolution,
// shadows, view distance, the toon look's screen passes). Babylon
// is imported per module so the bundle only carries what the game uses.
//
// The look is the toon one (docs/art/style-guide.md, "Toon rendering"):
// every lit material runs the toon model (game/toon/material.ts) with the
// lights set up here: the "sky" hemisphere is the ambient fill (cool sky
// above, warm bounce below), the "sun" the key light (the moon at night,
// clock/daynight.ts), plus a few torch lights (game/toon/torches.ts). The
// sky is a gradient dome (game/toon/sky.ts); bloom and SSAO are screen
// passes (game/toon/post.ts), off on Low. Characters draw inverted-hull
// outlines (game/toon/outline.ts) at Medium and High; the castle has none
// (docs/art/character-look.md section 4).
//
// Character shadows (docs/art/character-adoption.md section 7,
// game/toon/char-shadows.ts): at Medium and High the nearest characters
// cast into a shadow map of their own, onto the floors (`addStatic`, room
// shells through `addShadowReceivers`) and onto the characters; everyone
// else gets a blob (game/characters/blobs.ts). High's sun shadows are
// unchanged; a floor that receives only the character shadows ignores the
// sun's (`metadata.toonKeyShadow`), as interiors always have.
//
// Resolution and anti-aliasing (docs/art/character-look.md section 5): Low draws
// straight to the multisampled canvas at min(1, 0.75 x DPR); Medium and High
// draw at the screen's resolution (up to DPR 2) into the passes' 4x
// multisampled scene target (toon/post.ts), and a dynamic-resolution guard steps the scale down on a GPU
// that cannot keep up (game/resolution-guard.ts).
//
// The view outdoors (`fogRange`): indoors the fog and the far
// plane follow the quality's view distance as before; standing outdoors
// (the grounds, the courtyard) they reach further, so the lake's far shore,
// the mountains and the far castle (castle/hlod.ts) show through a haze,
// and at night the fog closes in to a mist over the water. Both still scale
// with the quality's view distance; the change eases over about a second.
//
// Materials get their outdoor looks by name as they
// appear (`outdoorLook`): every `castle_roof` the palette's dark slate
// (castle/hlod.ts `RoofSlatePlugin`), the grass, the trees, the backdrop and
// the lake their night tint (castle/mist.ts `NightTintPlugin`); and the lake
// gets its low mist by night (castle/mist.ts `LakeMist`, Medium and High).

import { Engine } from "@babylonjs/core/Engines/engine";
import { Scene } from "@babylonjs/core/scene";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight";
import { ShadowGenerator } from "@babylonjs/core/Lights/Shadows/shadowGenerator";
import "@babylonjs/core/Lights/Shadows/shadowGeneratorSceneComponent";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import { qualityProfile, type Quality, type QualityProfile } from "../settings";
import { DAY_SKY, NOON } from "./clock/daynight";
import { sceneLights } from "./toon/lighting";
import { frameAlpha, postPasses, ToonPost } from "./toon/post";
import { SkyDome } from "./toon/sky";
import { TorchLights } from "./toon/torches";
import { CharacterShadows } from "./toon/char-shadows";
import { drapeShadows } from "./toon/drape-shadow";
import { snapShadowCentre } from "./sun-shadow";
import { ResolutionGuard } from "./resolution-guard";
import { isOutdoorMaterial } from "./castle/materials";
import { attachRoofSlate } from "./castle/hlod";
import { attachNightTint, groundsNight, LakeMist } from "./castle/mist";
import { FireField } from "./fire/field";
import type { Material } from "@babylonjs/core/Materials/material";

/** Fog and clear colour at noon (data/art/lighting.json; clock/daynight.ts follows the hour). */
const HORIZON = new Color3(NOON.fog[0], NOON.fog[1], NOON.fog[2]);
const SUN_DIRECTION = new Vector3(NOON.sunDirection[0], NOON.sunDirection[1], NOON.sunDirection[2]);
const SHADOW_MAP_SIZE = 2048;
/** Side of the square area around the player that casts shadows (m). */
const SHADOW_EXTENT = 70;
/** How far back along the sun the light stands from the player (m; the depth range is shadowMinZ..shadowMaxZ from it). */
const SHADOW_BACK = 120;
/** The sky dome's radius (m): inside the nearest far plane of any quality (Low: 190 m). */
const SKY_RADIUS = 150;

/** The fog and the far plane (m). */
export interface FogRange {
  start: number;
  end: number;
  /** The cameras' far plane: a little past the fog's end. */
  far: number;
}

/**
 * Outdoors, the fog's start and end as multiples of the view distance, by
 * day and at night (the mist): at Medium (220 m) by day the haze starts at
 * 110 m and is whole at 1,540 m (the mountains), at night 55 m and 660 m.
 */
export const OUTDOOR_FOG = { dayStart: 0.5, dayEnd: 7, nightStart: 0.25, nightEnd: 3 } as const;

/** The fog and far plane for a view distance, indoors or outdoors, at `daylight` (0 night .. 1 day). Pure, unit tested. */
export function fogRange(viewDistance: number, outdoors: boolean, daylight = 1): FogRange {
  if (!outdoors) return { start: viewDistance * 0.35, end: viewDistance, far: viewDistance + 50 };
  const d = Math.min(1, Math.max(0, Number.isFinite(daylight) ? daylight : 1));
  const start = viewDistance * (OUTDOOR_FOG.nightStart + (OUTDOOR_FOG.dayStart - OUTDOOR_FOG.nightStart) * d);
  const end = viewDistance * (OUTDOOR_FOG.nightEnd + (OUTDOOR_FOG.dayEnd - OUTDOOR_FOG.nightEnd) * d);
  return { start, end, far: end + 50 };
}

/** How fast the fog follows a change of place (1/s). */
const FOG_EASE = 3;

export class WorldScene {
  readonly engine: Engine;
  readonly scene: Scene;
  readonly sky: SkyDome;
  readonly torches: TorchLights;
  /** Animated fire (game/fire/): every flame of the loaded cells and rooms. */
  readonly fire: FireField;
  /** The nearest characters' shadow map (Medium, High). */
  readonly characterShadows: CharacterShadows;
  private readonly sun: DirectionalLight;
  private shadows: ShadowGenerator | null = null;
  private post: ToonPost | null = null;
  /** Static world meshes (cells): shadow receivers and casters on High. */
  private readonly statics = new Set<AbstractMesh>();
  /** Moving meshes that cast shadows on High (character bodies). */
  private readonly dynamics = new Set<AbstractMesh>();
  private profile: QualityProfile;
  private qualityName: Quality;
  private readonly onResize = () => this.engine.resize();
  /** Whether the canvas multisamples (decided once, for the starting quality). */
  private readonly canvasMsaa: boolean;
  /** Medium and High: steps the render scale down on slow frames (resolution-guard.ts). */
  private readonly guard = new ResolutionGuard();
  private scale = 1;
  private lastFrameAt = 0;
  /** Measurements (perf-probe.ts) hold the scale where it is. */
  private guardHeld = false;
  /** Where the player stands (`setOutdoors`) and the fog it asks for. */
  private outdoors = false;
  private daylight = 1;
  private fogGoal: FogRange = { start: 0, end: 0, far: 0 };
  private fogNow: FogRange = { start: 0, end: 0, far: 0 };
  /** The lake's low mist by night. */
  readonly mist: LakeMist;
  private readonly onFrame = (): void => {
    const now = performance.now();
    const dt = this.lastFrameAt > 0 ? now - this.lastFrameAt : 0;
    this.lastFrameAt = now;
    this.easeFog(dt / 1000);
    this.mist.update(groundsNight(), now);
    if (!this.profile.dynamicResolution || !this.post || this.guardHeld || document.hidden) return;
    const next = this.guard.frame(now, dt);
    if (next > 0) this.setRenderScale(next);
  };

  constructor(canvas: HTMLCanvasElement, quality: Quality) {
    // The canvas multisamples only where the scene draws straight to it
    // (Low). With the screen passes (Medium, High: bloom) the scene renders
    // into the passes' own multisampled target and the canvas only receives
    // the finished frame: a multisampled canvas would then cost its 4x
    // buffers and a resolve every frame for nothing (toon/post.ts). Fixed at
    // startup; a later switch to or from Low changes it from the next page
    // load.
    this.canvasMsaa = postPasses(qualityProfile(quality, window.devicePixelRatio || 1)).length === 0;
    this.engine = new Engine(
      canvas,
      this.canvasMsaa,
      { stencil: false, powerPreference: "high-performance", preserveDrawingBuffer: false, audioEngine: false },
      false,
    );
    this.engine.disableManifestCheck = true;
    const scene = new Scene(this.engine);
    this.scene = scene;
    scene.useRightHandedSystem = true; // docs/architecture.md: glTF, sim-core and the server agree
    scene.clearColor = Color4.FromColor3(HORIZON, 1);
    scene.ambientColor = new Color3(0, 0, 0);
    scene.skipPointerMovePicking = true; // nothing in the world is hover-interactive yet
    scene.fogMode = Scene.FOGMODE_LINEAR;
    scene.fogColor = HORIZON.clone();

    // The ambient fill (sky above, warm bounce below) and the key light, at
    // noon from the art data; clock/daynight.ts follows the game hour. Specular
    // off everywhere: toon highlights come from the ramp rows.
    const hemi = new HemisphericLight("sky", new Vector3(0, 1, 0), scene);
    const noon = sceneLights(NOON);
    hemi.intensity = noon.hemiIntensity;
    hemi.diffuse = Color3.FromArray(noon.hemiSky);
    hemi.groundColor = Color3.FromArray(noon.hemiGround);
    hemi.specular = Color3.Black();

    this.sun = new DirectionalLight("sun", SUN_DIRECTION.clone(), scene);
    this.sun.intensity = noon.sunIntensity;
    this.sun.diffuse = Color3.FromArray(noon.sunColour);
    this.sun.specular = Color3.Black();
    this.sun.shadowFrustumSize = SHADOW_EXTENT;
    this.sun.shadowMinZ = 1;
    this.sun.shadowMaxZ = 260;

    this.qualityName = quality;
    this.torches = new TorchLights(scene, quality);
    this.fire = new FireField(scene, quality);
    // After the sun and the torches: the castle's materials have room for it (convert.ts WORLD_MAX_LIGHTS).
    this.characterShadows = new CharacterShadows(scene, () => this.sun);
    this.sky = new SkyDome(scene, SKY_RADIUS, DAY_SKY, SUN_DIRECTION.scale(-1));
    // The scene announces a new material on the next tick, once it is built (before it is drawn).
    scene.onNewMaterialAddedObservable.add(outdoorLook);
    this.mist = new LakeMist(scene);
    this.profile = qualityProfile(quality, window.devicePixelRatio || 1);
    this.applyProfile();
    window.addEventListener("resize", this.onResize);
    scene.onBeforeRenderObservable.add(this.onFrame);
  }

  get quality(): QualityProfile {
    return this.profile;
  }

  /** The camera the outline and bloom passes run on (once, after it exists). */
  attachCamera(camera: Camera): void {
    this.post?.dispose();
    this.post = new ToonPost(this.scene, camera);
    this.applyProfile();
  }

  setQuality(quality: Quality): void {
    this.qualityName = quality;
    this.profile = qualityProfile(quality, window.devicePixelRatio || 1);
    this.applyProfile();
  }

  /** Bytes held by the screen passes' render targets (F3 overlay). */
  get postBytes(): number {
    return this.post?.memoryBytes() ?? 0;
  }

  /** The render scale in use (x CSS pixels) and the quality's own; they differ after the guard stepped down. */
  get renderScale(): { now: number; top: number; samples: number } {
    return { now: this.scale, top: this.profile.resolution, samples: this.post?.samples ?? 1 };
  }

  /** One line for the F3 overlay: the render scale, the guard's steps and the scene's MSAA. */
  get resolutionLine(): string {
    const r = this.renderScale;
    const guard = this.profile.dynamicResolution ? (r.now < r.top ? `, guard ${r.now} of ${r.top}` : ", guard idle") : "";
    return `scale ${r.now.toFixed(2)}${guard}, msaa ${r.samples}x`;
  }

  /** Holds the guard at the quality's own scale (a measurement: perf-probe.ts); releasing starts it over there. */
  holdResolution(hold: boolean): void {
    if (this.guardHeld === hold) return;
    this.guardHeld = hold;
    this.guard.reset(this.profile.resolution);
    if (this.scale !== this.profile.resolution) this.setRenderScale(this.profile.resolution);
  }

  private setRenderScale(scale: number): void {
    this.scale = scale;
    // Resizes the canvas's drawing buffer and, on the next frame, the passes' targets: once a step.
    this.engine.setHardwareScalingLevel(1 / scale);
  }

  /** The far plane the cameras use now (m): the view distance indoors, further outdoors (`fogRange`). */
  get farPlane(): number {
    return this.fogNow.far;
  }

  /**
   * Whether the player stands outdoors, and the daylight (0 at
   * night, 1 by day; the night's mist). Called on place updates; the fog
   * eases to the new range.
   */
  setOutdoors(outdoors: boolean, daylight: number): void {
    const d = Math.round(Math.min(1, Math.max(0, Number.isFinite(daylight) ? daylight : 1)) * 50) / 50;
    if (outdoors === this.outdoors && d === this.daylight) return;
    this.outdoors = outdoors;
    this.daylight = d;
    this.fogGoal = fogRange(this.profile.viewDistance, outdoors, d);
  }

  /** Moves the fog and the far plane toward the goal (per frame; nothing to do once there). */
  private easeFog(dt: number): void {
    const goal = this.fogGoal;
    const now = this.fogNow;
    if (now.start === goal.start && now.end === goal.end && now.far === goal.far) return;
    const k = 1 - Math.exp(-Math.min(dt, 0.25) * FOG_EASE);
    const step = (from: number, to: number) => (Math.abs(to - from) < 0.5 ? to : from + (to - from) * k);
    now.start = step(now.start, goal.start);
    now.end = step(now.end, goal.end);
    now.far = step(now.far, goal.far);
    this.applyFog();
  }

  private applyFog(): void {
    const f = this.fogNow;
    this.scene.fogStart = f.start;
    this.scene.fogEnd = f.end;
    for (const cam of this.scene.cameras) cam.maxZ = f.far;
  }

  private applyProfile(): void {
    const p = this.profile;
    this.guard.reset(p.resolution);
    this.setRenderScale(p.resolution);
    // A quality change moves the fog at once (no easing).
    this.fogGoal = fogRange(p.viewDistance, this.outdoors, this.daylight);
    this.fogNow = { ...this.fogGoal };
    this.applyFog();
    this.torches.setQuality(this.qualityName);
    this.fire.setQuality(this.qualityName);
    this.characterShadows.setQuality(this.qualityName);
    this.mist.setQuality(this.qualityName);
    if (this.post) {
      // The scene target multisamples at Medium and High; the in-pass FXAA only
      // where neither it nor the canvas (created for the starting quality) does.
      // No screen-space outline (character look pass 2, 9.2): characters draw
      // their own inverted-hull outlines (characters/renderer.ts), the castle none.
      const opts = { bloom: p.bloom, fxaa: p.bloom && !this.canvasMsaa && p.sceneSamples <= 1, ssao: p.ssao, samples: p.sceneSamples };
      this.post.configure(opts);
      // The sky writes alpha 0 (no AO) while a pass reads the frame alpha.
      this.sky.setOutlineMask(frameAlpha(opts));
    }

    if (p.shadows && !this.shadows) {
      const gen = new ShadowGenerator(SHADOW_MAP_SIZE, this.sun);
      gen.usePercentageCloserFiltering = true;
      gen.filteringQuality = ShadowGenerator.QUALITY_MEDIUM;
      gen.bias = 0.0015;
      gen.normalBias = 0.02;
      // The toon model steps the shadow term itself (toon/material.ts): full strength here.
      gen.darkness = 0;
      // Characters cast here too (High): seated skirts, robes and coats with their sitting drape.
      drapeShadows(gen);
      this.shadows = gen;
      for (const m of this.statics) this.addShadowMesh(m, true);
      for (const m of this.dynamics) this.addShadowMesh(m, false);
    } else if (!p.shadows && this.shadows) {
      this.shadows.dispose();
      this.shadows = null;
    }
    for (const m of this.statics) this.updateReceive(m);
    for (const m of this.roomReceivers) this.updateReceive(m);
  }

  /** Meshes of the room shown (dorms, classes) that receive character shadows. */
  private readonly roomReceivers = new Set<AbstractMesh>();

  /** Whether a mesh takes shadows: the sun's (High, outdoor materials) or the characters' (Medium, High, floors). */
  private updateReceive(m: AbstractMesh): void {
    setReceiveShadows(m, (!!this.shadows && receivesSun(m)) || this.characterShadows.receives(m));
  }

  private addShadowMesh(mesh: AbstractMesh, receive: boolean): void {
    this.shadows?.addShadowCaster(mesh, false);
    if (receive) this.updateReceive(mesh);
  }

  /** Registers loaded world geometry for shadows (its floors receive the characters'). */
  addStatic(meshes: readonly AbstractMesh[]): void {
    for (const m of meshes) {
      this.statics.add(m);
      if (receivesCharacterShadows(m)) this.characterShadows.addReceiver(m, receivesSun(m));
      if (this.shadows) this.addShadowMesh(m, true);
      else this.updateReceive(m);
    }
  }

  /** Unregisters world geometry before it is disposed. */
  removeStatic(meshes: readonly AbstractMesh[]): void {
    for (const m of meshes) {
      this.statics.delete(m);
      this.shadows?.removeShadowCaster(m, false);
      this.characterShadows.removeReceiver(m);
    }
  }

  /** A room shell's meshes (dorms, classes): their floors receive character shadows while shown. */
  addShadowReceivers(meshes: readonly AbstractMesh[]): void {
    for (const m of meshes) {
      if (!receivesCharacterShadows(m)) continue;
      this.roomReceivers.add(m);
      this.characterShadows.addReceiver(m, false);
      this.updateReceive(m);
    }
  }

  removeShadowReceivers(meshes: readonly AbstractMesh[]): void {
    for (const m of meshes) {
      if (!this.roomReceivers.delete(m)) continue;
      this.characterShadows.removeReceiver(m);
    }
  }

  addDynamicCaster(mesh: AbstractMesh): void {
    this.dynamics.add(mesh);
    if (this.shadows) this.addShadowMesh(mesh, false);
  }

  /** Unregisters a moving caster before it is disposed (characters come and go). */
  removeDynamicCaster(mesh: AbstractMesh): void {
    this.dynamics.delete(mesh);
    this.shadows?.removeShadowCaster(mesh, false);
  }

  /**
   * Centres the shadow map on the player. The centre snaps to the shadow
   * map's texel grid in the light's own space (sun-shadow.ts), so the map
   * only ever shifts by whole texels and the shadow edges don't crawl as the
   * player moves. The torch pool follows the player too.
   */
  followShadows(x: number, y: number, z: number, dt = 0): void {
    this.torches.update(dt, x, y, z);
    if (!this.shadows) return;
    const d = this.sun.direction; // follows the hour (clock/daynight.ts)
    const p = this.shadowFeet;
    p[0] = x;
    p[1] = y;
    p[2] = z;
    const dir = this.shadowDir;
    dir[0] = d.x;
    dir[1] = d.y;
    dir[2] = d.z;
    const c = snapShadowCentre(p, dir, SHADOW_EXTENT / SHADOW_MAP_SIZE, this.shadowCentre);
    const len = Math.hypot(d.x, d.y, d.z) || 1;
    const back = SHADOW_BACK / len;
    this.sun.position.set(c[0] - d.x * back, c[1] - d.y * back, c[2] - d.z * back);
  }

  /** followShadows's scratch (no per-frame allocation). */
  private readonly shadowFeet: [number, number, number] = [0, 0, 0];
  private readonly shadowDir: [number, number, number] = [0, -1, 0];
  private readonly shadowCentre: [number, number, number] = [0, 0, 0];

  dispose(): void {
    window.removeEventListener("resize", this.onResize);
    this.scene.onBeforeRenderObservable.removeCallback(this.onFrame);
    this.post?.dispose();
    this.shadows?.dispose();
    this.characterShadows.dispose();
    this.torches.dispose();
    this.fire.dispose();
    this.mist.dispose();
    this.sky.dispose();
    this.scene.dispose();
    this.engine.dispose();
  }
}

/** A material's outdoor look by name (the slate roofs, the night tint); a no-op for any other. */
export function outdoorLook(m: Material): void {
  attachRoofSlate(m);
  attachNightTint(m);
}

/**
 * World materials the sun's shadow falls on (High): the outdoor ones. The
 * castle is a shell under its roofs, so every interior surface lies in the
 * roof's (and the upper floors') shadow: indoors on High read entirely as the
 * shadow band, darker and flatter than Medium. Interiors are lit as on
 * Medium instead (the toon step on the sun's direction, no shadow map);
 * the grounds, the courtyard and the castle's outside take the castle's and
 * the characters' shadows. Everything static still casts. The kitted castle's
 * outdoor materials (castle M5c, docs/art/castle-kit.md "Exterior": the `@ext`
 * exterior clones, `castle_roof`, `castle_floor_court`) receive it too, and
 * so does the grounds' terrain (`grounds_terrain`, castle/terrain.ts).
 */
const SUN_RECEIVERS = new Set(["floor_grounds", "floor_courtyard", "water", "wall_exterior", "roof", "grounds_terrain"]);

export function receivesSun(mesh: AbstractMesh): boolean {
  const name = mesh.material?.name;
  return name !== undefined && (SUN_RECEIVERS.has(name) || isOutdoorMaterial(name));
}

/**
 * World materials character shadows fall on (Medium, High): floors, the
 * class tiers and the arena deck. Walls do not take them (the cost would be
 * a shadow lookup on most of the screen for a shadow seldom seen there).
 */
const CHARACTER_RECEIVER = /floor|grounds|courtyard|_tier$|_deck$/;

export function receivesCharacterShadows(mesh: AbstractMesh): boolean {
  const name = mesh.material?.name;
  return name !== undefined && CHARACTER_RECEIVER.test(name);
}

/**
 * World materials are deliberately not frozen: a cell has only a handful, and
 * toggling shadows must be able to recompile them.
 */
function setReceiveShadows(mesh: AbstractMesh, on: boolean): void {
  mesh.receiveShadows = on;
}
