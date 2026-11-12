// Warm torch pools in the castle: the world manifest lists the torches and
// candles of each cell (`lights` on manifest entries, docs/architecture.md),
// and a small pool of real point lights follows the ones nearest the player.
// A real light is what costs (every lit material evaluates it per pixel and
// adding one recompiles shaders), so the pool is fixed per quality level and
// created once; a light without a torch nearby is turned down, never
// removed. Torches burn from dusk to dawn (`setLevel` from clock/daynight.ts)
// and are switched off in full day, when the sun outshines them (a switch
// recompiles the lit shaders once; Babylon caches them for the next time). In rooms (dorms, classes) they are switched off: rooms
// bring their own lamps.
//
// Cell visibility (castle kit M2, docs/art/castle-kit.md "Cell visibility (`sees`)"): only torches
// of visible cells are picked (`setVisibleCells`, when the set changes), so
// a torch behind a disabled cell's wall never lights the room in view.

import { PointLight } from "@babylonjs/core/Lights/pointLight";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { Scene } from "@babylonjs/core/scene";
import type { Quality } from "../../settings";
import { FIRE_LOOK } from "../fire/data";
import { fireTime, flicker, ripple } from "../fire/flicker";

export interface Torch {
  pos: [number, number, number];
  colour: [number, number, number];
  intensity: number;
  range: number;
  /** Animated fire: the seed of the fire it flickers with (manifest `flicker`); none: steady. */
  flicker?: number;
  /** A lake window's light: the phase of its slow swell (manifest `ripple`, game/fire/flicker.ts `ripple`); none: steady. */
  ripple?: number;
  /** The world cell the torch belongs to (manifest entry), for cell visibility. */
  cell?: string;
}

/** Real lights per quality level. */
export const TORCH_LIGHTS: Record<Quality, number> = { low: 1, medium: 2, high: 3 };
/** Re-pick the nearest torches this often (s). */
const PICK_INTERVAL = 0.5;
/** Brightness by day, as a share of night's. */
const DAY_LEVEL = 0.35;

/**
 * The indices of the `n` torches nearest `p` (squared distance), written
 * into `out` (length n; -1 where there are fewer torches). With `usable`,
 * only torches whose entry is non-zero count. No allocation.
 */
export function nearestTorches(torches: readonly Torch[], p: readonly [number, number, number], out: Int32Array, dist: Float64Array, usable?: Uint8Array): void {
  const n = out.length;
  out.fill(-1);
  dist.fill(Infinity);
  for (let i = 0; i < torches.length; i++) {
    if (usable && usable[i] === 0) continue;
    const t = torches[i]!.pos;
    const d = (t[0] - p[0]) ** 2 + (t[1] - p[1]) ** 2 + (t[2] - p[2]) ** 2;
    if (d >= dist[n - 1]!) continue;
    let k = n - 1;
    while (k > 0 && dist[k - 1]! > d) {
      dist[k] = dist[k - 1]!;
      out[k] = out[k - 1]!;
      k--;
    }
    dist[k] = d;
    out[k] = i;
  }
}

/** Torch brightness for `daylight` (0 night .. 1 day): a soft glow by day, full at night. */
export function torchLevel(daylight: number): number {
  const d = Math.min(1, Math.max(0, Number.isFinite(daylight) ? daylight : 1));
  return DAY_LEVEL + (1 - DAY_LEVEL) * (1 - d);
}

const registry = new WeakMap<Scene, TorchLights>();

export function torchesOf(scene: Scene): TorchLights | undefined {
  return registry.get(scene);
}

export class TorchLights {
  private readonly lights: PointLight[] = [];
  private torches: Torch[] = [];
  /** Per torch: 1 while its cell is visible (castle kit M2 `sees`). */
  private usable = new Uint8Array(0);
  private visibleCells: ReadonlySet<string> | null = null;
  private readonly picked: Int32Array;
  private readonly dist: Float64Array;
  private clock = PICK_INTERVAL;
  private level = 1;
  private indoor = false;
  /** Full day: torches off (see `setLevel`). */
  private dayOff = false;

  constructor(
    private readonly scene: Scene,
    quality: Quality,
  ) {
    const n = TORCH_LIGHTS[quality];
    this.picked = new Int32Array(TORCH_LIGHTS.high);
    this.dist = new Float64Array(TORCH_LIGHTS.high);
    // Created once at the highest count; lower qualities keep the extra ones disabled.
    for (let i = 0; i < TORCH_LIGHTS.high; i++) {
      const l = new PointLight(`torch-${i}`, new Vector3(0, -500, 0), scene);
      l.diffuse = new Color3(1, 0.72, 0.4);
      l.specular = Color3.Black();
      l.intensity = 0;
      l.range = 12;
      l.setEnabled(i < n);
      this.lights.push(l);
    }
    this.active = n;
    this.pickedView = this.picked.subarray(0, n);
    this.distView = this.dist.subarray(0, n);
    registry.set(scene, this);
  }

  private active: number;
  private pickedView: Int32Array;
  private distView: Float64Array;

  /** The castle's torches (manifest `lights`). */
  setTorches(list: readonly Torch[]): void {
    this.torches = [...list];
    this.usable = new Uint8Array(this.torches.length);
    this.applyVisible();
  }

  /** Only torches of these cells light (null: every torch); re-picks at the next update. */
  setVisibleCells(cells: ReadonlySet<string> | null): void {
    this.visibleCells = cells;
    this.applyVisible();
  }

  private applyVisible(): void {
    const v = this.visibleCells;
    for (let i = 0; i < this.torches.length; i++) {
      const c = this.torches[i]!.cell;
      this.usable[i] = v === null || c === undefined || v.has(c) ? 1 : 0;
    }
    this.clock = PICK_INTERVAL;
  }

  /** Torches that may light now (in visible cells). */
  get usableCount(): number {
    let n = 0;
    for (let i = 0; i < this.usable.length; i++) n += this.usable[i]!;
    return n;
  }

  get count(): number {
    return this.torches.length;
  }

  setQuality(q: Quality): void {
    this.active = TORCH_LIGHTS[q];
    this.pickedView = this.picked.subarray(0, this.active);
    this.distView = this.dist.subarray(0, this.active);
    this.applyEnabled();
  }

  /** Rooms light themselves: torches off while one is shown. */
  setIndoor(indoor: boolean): void {
    this.indoor = indoor;
    this.applyEnabled();
  }

  /** Night 1, day `DAY_LEVEL` (see `torchLevel`). */
  setLevel(level: number): void {
    this.level = level;
    this.clock = PICK_INTERVAL;
    // In full day the sun outshines them: switched off, so day frames pay nothing for torches.
    const off = level <= DAY_LEVEL + 0.02;
    if (off !== this.dayOff) {
      this.dayOff = off;
      this.applyEnabled();
    }
  }

  private applyEnabled(): void {
    for (let i = 0; i < this.lights.length; i++) this.lights[i]!.setEnabled(!this.indoor && !this.dayOff && i < this.active);
  }

  /** Per frame: follows the player (re-picks twice a second; no allocation). */
  update(dt: number, x: number, y: number, z: number): void {
    if (this.indoor || this.dayOff || this.active === 0) return;
    this.clock += dt;
    if (this.clock < PICK_INTERVAL) {
      this.flicker();
      return;
    }
    this.clock = 0;
    const p = this.pos;
    p[0] = x;
    p[1] = y;
    p[2] = z;
    const picked = this.pickedView;
    nearestTorches(this.torches, p, picked, this.distView, this.usable);
    for (let i = 0; i < this.active; i++) {
      const l = this.lights[i]!;
      const t = this.torches[picked[i]!];
      if (!t) {
        l.intensity = 0;
        this.base[i] = 0;
        this.seed[i] = -1;
        this.swell[i] = -1;
        continue;
      }
      l.position.set(t.pos[0], t.pos[1], t.pos[2]);
      l.diffuse.set(t.colour[0], t.colour[1], t.colour[2]);
      l.range = t.range;
      l.intensity = t.intensity * this.level;
      this.base[i] = l.intensity;
      this.seed[i] = t.flicker ?? -1;
      this.swell[i] = t.ripple ?? -1;
    }
    this.flicker();
  }

  /**
   * Animated fire: a light at a fire flickers with its flames (game/fire/flicker.ts, the same clock and sum);
   * a lake window's light swells slowly (`ripple`).
   */
  private flicker(): void {
    let t = -1;
    for (let i = 0; i < this.active; i++) {
      const s = this.seed[i]!;
      const w = this.swell[i]!;
      if (s < 0 && w < 0) continue;
      if (t < 0) t = fireTime(performance.now());
      this.lights[i]!.intensity = this.base[i]! * (s >= 0 ? flicker(t, s, FIRE_LOOK.lightFlicker) : ripple(t, w));
    }
  }

  /** Per active light: its picked torch's intensity, flicker seed and ripple seed (-1: steady). */
  private readonly base = new Float64Array(TORCH_LIGHTS.high);
  private readonly seed = new Float64Array(TORCH_LIGHTS.high).fill(-1);
  private readonly swell = new Float64Array(TORCH_LIGHTS.high).fill(-1);

  private readonly pos: [number, number, number] = [0, 0, 0];

  dispose(): void {
    for (const l of this.lights) l.dispose();
    this.lights.length = 0;
    registry.delete(this.scene);
  }
}

/**
 * A point light's Babylon intensity in the toon model from the art tools'
 * relative strength (1 ≈ a candle at a metre or two; manifests use about
 * 1.5-40): scaled to the exposure of the castle's lights (toon/lighting.ts).
 */
export function toonPointIntensity(raw: number | undefined): number {
  const v = (Number.isFinite(raw) ? (raw as number) : 6) * 0.09;
  return Math.min(0.9, Math.max(0.08, v));
}
