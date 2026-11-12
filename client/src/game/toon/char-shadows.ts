// Character shadows at Medium and High (docs/art/character-adoption.md
// section 7): a shadow map of the nearest characters only (8 at Medium on a
// 1024 px map, 12 at High on 2048 px with softer filtering), cast onto the
// floors around them and onto the characters themselves (body, garments,
// hair: real shadows on the body; never the face). Everyone else, and
// everyone at Low, gets a blob (characters/blobs.ts).
//
// The map belongs to a shadow-only directional light (toon/material.ts
// `CHARACTER_SHADOW_RANGE`): the toon plugin adds no light for it, its
// shadow only moves the lit share into the shadow band, so the cast shadow
// is the same tinted band as a form shadow. Outdoors it falls along the sun
// or moon (its elevation kept to at least `MIN_ELEVATION`, so evening
// shadows do not stretch across the courtyard); in rooms, where there is no
// sun, it falls from a fixed steep key above and in front.
//
// It only reaches meshes on `CHARACTER_SHADOW_LAYER` (character meshes and
// floors, `addReceiver`), so the rest of the world neither recompiles nor
// samples it. The frustum is fitted every frame to the casters (their
// bounding boxes in light space, Babylon's auto extents); the depth range
// is fixed around them so the floor below still receives.
//
// Cost: one extra draw per caster in the shadow pass (+8 / +12 draw calls),
// the map (1024² / 2048², a half-float red target and 32-bit depth: 6 MB /
// 24 MB), one shadow lookup per
// receiving pixel (Babylon's PCF: one hardware-filtered tap at Medium, four
// at High), and no per-frame allocation (the caster list is rewritten in
// place).

import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight";
import { ShadowGenerator } from "@babylonjs/core/Lights/Shadows/shadowGenerator";
import "@babylonjs/core/Lights/Shadows/shadowGeneratorSceneComponent";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Scene } from "@babylonjs/core/scene";
import type { Quality } from "../../settings";
import { drapeShadows } from "./drape-shadow";
import { CHARACTER_SHADOW_RANGE } from "./material";
import { CHARACTER_FX } from "../characters/fx-flag";

/** The layer bit of meshes that receive character shadows (outside the camera's default 0x0FFFFFFF). */
export const CHARACTER_SHADOW_LAYER = 0x10000000;

export interface CharacterShadowConfig {
  /** Nearest characters casting into the map. */
  casters: number;
  mapSize: number;
  /** Babylon's PCF quality. */
  filtering: number;
  /** Characters farther than this (m) never cast (the map would spread too thin). */
  maxDistance: number;
}

/** Per quality level (null: blobs only). */
export const CHARACTER_SHADOWS: Record<Quality, CharacterShadowConfig | null> = {
  low: null,
  medium: { casters: 8, mapSize: 1024, filtering: ShadowGenerator.QUALITY_LOW, maxDistance: 24 },
  high: { casters: 12, mapSize: 2048, filtering: ShadowGenerator.QUALITY_MEDIUM, maxDistance: 32 },
};

/** The shadow never comes from lower than this above the horizon (rad). */
export const MIN_ELEVATION = (40 * Math.PI) / 180;
/** Rooms: a steep key from above and in front (about 60°, from the room's +Z side toward -Z). */
export const ROOM_KEY: readonly [number, number, number] = [0.25, -0.87, -0.43];
/** How far the light stands back from the casters, and the depth range around them (m). */
const BACK = 30;
const DEPTH = 60;

/**
 * The shadow direction for a key light direction `d` (from the light into
 * the scene), raised to `MIN_ELEVATION` when it comes from lower. Pure.
 */
export function shadowDirection(d: readonly [number, number, number], out: [number, number, number] = [0, -1, 0]): [number, number, number] {
  let x = d[0];
  let y = d[1];
  let z = d[2];
  const flat = Math.hypot(x, z);
  const len = Math.hypot(flat, y) || 1;
  const elevation = Math.asin(Math.max(-1, Math.min(1, -y / len)));
  if (!(elevation >= MIN_ELEVATION) || flat < 1e-6) {
    const e = Math.max(MIN_ELEVATION, Number.isFinite(elevation) ? elevation : MIN_ELEVATION);
    const fx = flat > 1e-6 ? x / flat : 0;
    const fz = flat > 1e-6 ? z / flat : 1;
    x = fx * Math.cos(e);
    z = fz * Math.cos(e);
    y = -Math.sin(e);
  } else {
    x /= len;
    y /= len;
    z /= len;
  }
  out[0] = x;
  out[1] = y;
  out[2] = z;
  return out;
}

export class CharacterShadows {
  private light: DirectionalLight | null = null;
  private gen: ShadowGenerator | null = null;
  private config: CharacterShadowConfig | null = null;
  private readonly receivers = new Map<AbstractMesh, boolean>();
  private readonly dir: [number, number, number] = [0, -1, 0];
  private readonly scratch = new Vector3();
  private readonly centre = new Vector3();
  private casterCount = 0;
  /** The casters last handed to the map (its render list is only touched when they change: a change re-checks every receiver's lights). */
  private readonly current: AbstractMesh[] = [];

  constructor(
    private readonly scene: Scene,
    /** The sun or moon, when it is up and lights the zone (null in rooms). */
    private readonly key: () => DirectionalLight | null,
  ) {}

  /** The configuration in effect (null at Low). */
  get active(): CharacterShadowConfig | null {
    return this.config;
  }

  /** How many characters may cast now (0: blobs only). */
  get maxCasters(): number {
    return this.config?.casters ?? 0;
  }

  get maxDistance(): number {
    return this.config?.maxDistance ?? 0;
  }

  /** The map's bytes (a half-float red target and a 32-bit depth texture: 6 bytes a texel), for the F3 overlay. */
  get mapBytes(): number {
    return this.config ? this.config.mapSize * this.config.mapSize * 6 : 0;
  }

  get casters(): number {
    return this.casterCount;
  }

  setQuality(q: Quality): void {
    const next = CHARACTER_FX.shadows ? CHARACTER_SHADOWS[q] : null;
    if (next && this.config && next.mapSize === this.config.mapSize && next.filtering === this.config.filtering) {
      this.config = next;
      return;
    }
    this.gen?.dispose();
    this.light?.dispose();
    this.gen = null;
    this.light = null;
    this.current.length = 0;
    this.config = next;
    if (next) {
      const light = new DirectionalLight("character-shadow", new Vector3(0, -1, 0), this.scene);
      // The marker the toon plugin reads: this light's shadow, never its light.
      light.range = CHARACTER_SHADOW_RANGE;
      light.diffuse = Color3.Black();
      light.specular = Color3.Black();
      light.intensity = 1;
      light.includeOnlyWithLayerMask = CHARACTER_SHADOW_LAYER;
      light.autoUpdateExtends = true;
      light.autoCalcShadowZBounds = false;
      light.shadowMinZ = 0.5;
      light.shadowMaxZ = DEPTH;
      // Margins for animated limbs past the rest-pose boxes.
      light.shadowOrthoScale = 0.15;
      // A one-channel colour target (useRedTextureType) next to the 32-bit depth: half the memory of RGBA.
      const gen = new ShadowGenerator(next.mapSize, light, false, undefined, true);
      gen.usePercentageCloserFiltering = true;
      gen.filteringQuality = next.filtering;
      gen.bias = 0.002;
      gen.normalBias = 0.015;
      gen.darkness = 0;
      gen.transparencyShadow = false;
      // Seated skirts, robes and coats cast their draped shape, not their standing one.
      drapeShadows(gen);
      this.light = light;
      this.gen = gen;
    }
    for (const [m, keyShadow] of this.receivers) this.applyReceiver(m, keyShadow);
  }

  /**
   * A mesh that receives character shadows: character meshes (`keyShadow`
   * false: the sun's own shadow map does not darken them) and floors
   * (`keyShadow` true only where the High sun shadows fall: outdoors).
   */
  addReceiver(mesh: AbstractMesh, keyShadow: boolean): void {
    this.receivers.set(mesh, keyShadow);
    this.applyReceiver(mesh, keyShadow);
  }

  removeReceiver(mesh: AbstractMesh): void {
    this.receivers.delete(mesh);
  }

  /** Whether `mesh` needs `receiveShadows` for the character map. */
  receives(mesh: AbstractMesh): boolean {
    return !!this.config && this.receivers.has(mesh);
  }

  private applyReceiver(mesh: AbstractMesh, keyShadow: boolean): void {
    const meta = (mesh.metadata ?? {}) as Record<string, unknown>;
    if (meta.toonKeyShadow !== keyShadow) mesh.metadata = { ...meta, toonKeyShadow: keyShadow };
    mesh.layerMask = this.config ? mesh.layerMask | CHARACTER_SHADOW_LAYER : mesh.layerMask & ~CHARACTER_SHADOW_LAYER;
    if (this.config) mesh.receiveShadows = true;
  }

  /**
   * This frame's casters: the first `count` of `meshes` (enabled character
   * meshes, nearest first), and the point they stand around. Rewrites the
   * map's render list in place.
   */
  setCasters(meshes: readonly AbstractMesh[], count: number, cx: number, cy: number, cz: number): void {
    const gen = this.gen;
    if (!gen || !this.light) {
      this.casterCount = 0;
      return;
    }
    const map = gen.getShadowMap();
    if (!map) return;
    const n = Math.min(count, meshes.length);
    const cur = this.current;
    let same = cur.length === n;
    for (let i = 0; same && i < n; i++) same = cur[i] === meshes[i];
    if (!same) {
      const had = cur.length > 0;
      cur.length = 0;
      for (let i = 0; i < n; i++) cur.push(meshes[i]!);
      // One assignment: Babylon re-checks the casters' lights once, not per push.
      map.renderList = cur.slice();
      // A receiver's material only compiles the light's shadow while the map
      // has casters (Babylon's PrepareDefinesForLight): floors prepared before
      // the first character arrived would never sample it. Re-check every
      // mesh of the light when the map gains its first caster or loses its
      // last (rare: not on every change of the nearest set).
      if (had !== n > 0) this.light._markMeshesAsLightDirty();
    }
    this.casterCount = n;
    this.centre.set(cx, cy, cz);
    this.aim();
  }

  /** Points the light at the casters along the key (or the rooms' fixed key). */
  private aim(): void {
    const light = this.light!;
    const key = this.key();
    const d = key && key.isEnabled() ? shadowDirection([key.direction.x, key.direction.y, key.direction.z], this.dir) : shadowDirection(ROOM_KEY, this.dir);
    light.direction.set(d[0], d[1], d[2]);
    const c = this.centre;
    this.scratch.set(c.x - d[0] * BACK, c.y - d[1] * BACK, c.z - d[2] * BACK);
    light.position.copyFrom(this.scratch);
  }

  dispose(): void {
    this.gen?.dispose();
    this.light?.dispose();
    this.gen = null;
    this.light = null;
    this.receivers.clear();
  }
}
