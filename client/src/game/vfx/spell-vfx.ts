// Spell effects for the running game, in one draw call: bolts (a hot orb
// with a comet tail), wand charge while a cast is telegraphed, Scutara's
// shield, and one-off bursts (impact flashes with a shockwave ring and
// sparks, a shattered shield's shards, a fizzle when a cast is broken).
// Spells to level 20 add Rigora's ringed bolt, Adveni's hook and tether,
// Tardara's wave, the statuses (chalk bind rings, slow ripples, a daze,
// a ward's halo) and their bursts (bind, slow, yank, cleanse, ward up,
// ward break, ward fade).
// Clean shapes in each spell's saturated colour (data/spells.json), for the
// stylized look; no textures.
//
// Per frame: `begin(dt)`, then the continuous effects (`bolt`, `charge`,
// `shield`) for what is happening now, then `end(camera)`, which adds the
// bursts and uploads. Bursts are fire-and-forget (`flash`, `shatter`, ...)
// and live in a fixed pool. Everything shares one `VfxLayer`, so a duel with
// a spectator crowd still costs one draw call and no allocation per frame.
// Used by the duel arena (game/dueling); usable for any spell.

import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import { BurstPool } from "./bursts";
import { VfxLayer } from "./layer";
import { Shape } from "./shapes";

export type Rgb = readonly [number, number, number];

const WHITE: Rgb = [1, 1, 1];
const GOLD: Rgb = [1, 0.84, 0.36];

export interface SpellVfxOptions {
  /** Most sprites a frame (continuous effects first, then bursts). */
  capacity?: number;
  /** Most burst particles alive. */
  particles?: number;
}

export class SpellVfx {
  private readonly layer: VfxLayer;
  private readonly bursts: BurstPool;
  private time = 0;

  constructor(scene: Scene, opts: SpellVfxOptions = {}) {
    this.layer = new VfxLayer(scene, "spells", opts.capacity ?? 320);
    this.bursts = new BurstPool(opts.particles ?? 200);
  }

  /** Starts a frame: ages the bursts. */
  begin(dt: number): void {
    this.time += dt;
    this.bursts.step(dt);
    this.layer.begin();
  }

  /** Seconds since this VFX began (for flicker). */
  get t(): number {
    return this.time;
  }

  /**
   * A bolt at (x, y, z) flying along the unit direction (dx, dy, dz): a hot
   * head, a tail `tail` metres long behind it and a flicker. `fade` 0..1
   * dims it (out of range).
   */
  bolt(x: number, y: number, z: number, dx: number, dy: number, dz: number, rgb: Rgb, tail = 1.4, fade = 1, seed = 0): void {
    const t = this.time * 30 + seed * 1.7;
    const flick = 0.85 + 0.15 * Math.sin(t) * Math.sin(t * 0.37 + 1);
    const L = this.layer;
    L.push(Shape.orb, x, y, z, 0.42 * flick, rgb[0], rgb[1], rgb[2], fade);
    L.push(Shape.streak, x, y, z, 0.24, rgb[0], rgb[1], rgb[2], 0.9 * fade, -dx * tail, -dy * tail, -dz * tail);
    // A wide soft glow around the head so it reads at a distance.
    L.push(Shape.orb, x, y, z, 1.05, rgb[0], rgb[1], rgb[2], 0.22 * fade);
    // Two glints trailing on a little spiral.
    for (let k = 0; k < 2; k++) {
      const back = 0.25 + 0.3 * k;
      const a = t * 0.4 + k * Math.PI;
      const ox = Math.cos(a) * 0.09;
      const oy = Math.sin(a) * 0.09;
      L.push(Shape.spark, x - dx * back + ox * dz, y - dy * back + oy, z - dz * back - ox * dx, 0.16, rgb[0], rgb[1], rgb[2], 0.8 * fade);
    }
  }

  /**
   * A wand charging a spell at (x, y, z): an orb that swells with `progress`
   * (0..1) and sparks circling it, quickening toward the release.
   */
  charge(x: number, y: number, z: number, rgb: Rgb, progress: number, seed = 0): void {
    const p = Math.max(0, Math.min(1, progress));
    const t = this.time;
    const L = this.layer;
    const pulse = 0.9 + 0.1 * Math.sin(t * (12 + p * 18) + seed);
    L.push(Shape.orb, x, y, z, (0.14 + 0.3 * p) * pulse, rgb[0], rgb[1], rgb[2], 0.65 + 0.35 * p);
    L.push(Shape.orb, x, y, z, 0.5 + 0.45 * p, rgb[0], rgb[1], rgb[2], 0.15 + 0.2 * p);
    const n = 3;
    for (let k = 0; k < n; k++) {
      const a = t * (4 + p * 8) + (k * Math.PI * 2) / n + seed;
      const r = 0.24 - 0.12 * p;
      L.push(Shape.spark, x + Math.cos(a) * r, y + Math.sin(a * 1.3) * r * 0.6, z + Math.sin(a) * r, 0.13, rgb[0], rgb[1], rgb[2], 0.5 + 0.5 * p);
    }
  }

  /**
   * Scutara at (x, y, z) (its centre, in front of the caster): a shimmering
   * ring. `strength` 0..1 fades it out at the end; `perfect` (its
   * reflecting window) rims it in gold.
   */
  shield(x: number, y: number, z: number, rgb: Rgb, strength: number, perfect: boolean, size = 1.9, seed = 0): void {
    const t = this.time;
    const s = Math.max(0, Math.min(1, strength));
    const wobble = 1 + 0.025 * Math.sin(t * 9 + seed);
    const L = this.layer;
    L.push(Shape.ring, x, y, z, size * wobble, rgb[0], rgb[1], rgb[2], 0.75 * s);
    L.push(Shape.orb, x, y, z, size * 1.15, rgb[0], rgb[1], rgb[2], 0.12 * s);
    if (perfect) {
      const flare = 0.75 + 0.25 * Math.sin(t * 30);
      L.push(Shape.ring, x, y, z, size * 1.06, GOLD[0], GOLD[1], GOLD[2], 0.9 * flare);
    }
  }

  // ------------------------------------------------------------ spells to level 20

  /** Rigora in flight: a white-hot orb with two bind rings tumbling about its flight line. */
  bindBolt(x: number, y: number, z: number, dx: number, dz: number, rgb: Rgb, fade = 1, seed = 0): void {
    this.bolt(x, y, z, dx, 0, dz, rgb, 0.8, fade, seed);
    const t = this.time * 7 + seed;
    const L = this.layer;
    // The rings' normals turn about the flight direction: across it (dz, -dx) and up.
    for (let k = 0; k < 2; k++) {
      const a = t + k * (Math.PI / 2);
      const c = Math.cos(a);
      const s = Math.sin(a);
      L.push(Shape.disc, x, y, z, 0.62 - k * 0.1, rgb[0], rgb[1], rgb[2], 0.85 * fade, dz * c, s, -dx * c);
    }
  }

  /** Adveni in flight: a bolt with a barbed hook curling back from its head. */
  hookBolt(x: number, y: number, z: number, dx: number, dz: number, rgb: Rgb, fade = 1, seed = 0): void {
    this.bolt(x, y, z, dx, 0, dz, rgb, 1.1, fade, seed);
    const L = this.layer;
    const wob = Math.sin(this.time * 18 + seed) * 0.03;
    // A J: back along the flight, then out to the side and forward again.
    for (let k = 1; k <= 6; k++) {
      const u = k / 6;
      const back = 0.12 + 0.55 * Math.sin(u * Math.PI * 0.8);
      const side = 0.32 * u * u + wob;
      L.push(Shape.spark, x - dx * back + dz * side, y + 0.05 * u, z - dz * back - dx * side, 0.16 - 0.012 * k, rgb[0], rgb[1], rgb[2], 0.9 * fade);
    }
  }

  /**
   * Tardara in flight: a wall of light `2 * halfWidth` wide across its
   * flight (dx, dz), its middle at (x, y, z), with streaks trailing.
   */
  wave(x: number, y: number, z: number, dx: number, dz: number, halfWidth: number, rgb: Rgb, fade = 1, seed = 0): void {
    const t = this.time;
    const L = this.layer;
    // Across the flight (horizontal): (-dz, dx).
    const ax = -dz * halfWidth * 2;
    const az = dx * halfWidth * 2;
    const swell = 1 + 0.06 * Math.sin(t * 7 + seed);
    L.push(Shape.beam, x, y - 0.2, z, 1.7 * swell, rgb[0], rgb[1], rgb[2], 0.5 * fade, ax, 0, az);
    L.push(Shape.beam, x, y - 0.2, z, 0.55, rgb[0], rgb[1], rgb[2], 0.85 * fade, ax, 0, az);
    // A second, fainter sheet a little behind for depth.
    L.push(Shape.beam, x - dx * 0.35, y - 0.25, z - dz * 0.35, 1.3, rgb[0], rgb[1], rgb[2], 0.25 * fade, ax * 0.9, 0, az * 0.9);
    const n = 5;
    for (let k = 0; k < n; k++) {
      const u = (k / (n - 1) - 0.5) * 2 * halfWidth * 0.85;
      const bob = 0.25 * Math.sin(t * 5 + k * 1.7 + seed);
      L.push(Shape.streak, x - dz * u, y - 0.2 + bob, z + dx * u, 0.3, rgb[0], rgb[1], rgb[2], 0.55 * fade, -dx * 1.2, 0, -dz * 1.2);
    }
    for (let k = 0; k < 6; k++) {
      const u = (((k * 0.37 + t * 0.9 + seed * 0.1) % 1) - 0.5) * 2 * halfWidth;
      const top = k % 2 ? 0.55 : -0.95;
      L.push(Shape.spark, x - dz * u, y + top, z + dx * u, 0.2, rgb[0], rgb[1], rgb[2], 0.7 * fade);
    }
  }

  /** A tether of light from (ax, ay, az) to (bx, by, bz) (Adveni's pull), with glints running toward a. */
  tether(ax: number, ay: number, az: number, bx: number, by: number, bz: number, rgb: Rgb, strength = 1): void {
    const s = Math.max(0, Math.min(1, strength));
    if (s <= 0) return;
    const t = this.time;
    const L = this.layer;
    const mx = (ax + bx) / 2;
    const my = (ay + by) / 2;
    const mz = (az + bz) / 2;
    const vx = bx - ax;
    const vy = by - ay;
    const vz = bz - az;
    const thrum = 1 + 0.25 * Math.sin(t * 40);
    L.push(Shape.beam, mx, my, mz, 0.1 * thrum, rgb[0], rgb[1], rgb[2], 0.95 * s, vx, vy, vz);
    L.push(Shape.beam, mx, my, mz, 0.38, rgb[0], rgb[1], rgb[2], 0.3 * s, vx, vy, vz);
    for (let k = 0; k < 4; k++) {
      const u = 1 - ((t * 2.2 + k / 4) % 1);
      L.push(Shape.spark, ax + vx * u, ay + vy * u, az + vz * u, 0.18, rgb[0], rgb[1], rgb[2], 0.8 * s);
    }
    L.push(Shape.orb, bx, by, bz, 0.45, rgb[0], rgb[1], rgb[2], 0.5 * s);
  }

  /** Rigora holding a duelist whose feet are at (x, y, z): chalk rings round the body and on the floor. */
  rooted(x: number, y: number, z: number, rgb: Rgb, strength = 1, seed = 0): void {
    const s = Math.max(0, Math.min(1, strength));
    const t = this.time;
    const L = this.layer;
    const pulse = 1 + 0.04 * Math.sin(t * 6 + seed);
    L.push(Shape.disc, x, y + 0.04, z, 1.25 * pulse, rgb[0], rgb[1], rgb[2], 0.75 * s, 0, 1, 0);
    L.push(Shape.disc, x, y + 0.05, z, 0.85, rgb[0], rgb[1], rgb[2], 0.45 * s, 0, 1, 0);
    for (let k = 0; k < 3; k++) {
      const h = 0.45 + k * 0.45 + 0.04 * Math.sin(t * 3 + k + seed);
      L.push(Shape.disc, x, y + h, z, 0.8 - k * 0.07, rgb[0], rgb[1], rgb[2], 0.55 * s, 0, 1, 0);
    }
    // Chalk motes drifting up.
    for (let k = 0; k < 4; k++) {
      const u = (t * 0.5 + k / 4 + seed * 0.13) % 1;
      const a = k * 1.9 + seed;
      L.push(Shape.spark, x + Math.cos(a) * 0.38, y + 0.1 + u * 1.8, z + Math.sin(a) * 0.38, 0.12, rgb[0], rgb[1], rgb[2], 0.6 * s * (1 - u));
    }
  }

  /** Tardara on a duelist whose feet are at (x, y, z): slow ripples spreading on the floor, a dragging haze. */
  slowed(x: number, y: number, z: number, rgb: Rgb, strength = 1, seed = 0): void {
    const s = Math.max(0, Math.min(1, strength));
    const t = this.time;
    const L = this.layer;
    for (let k = 0; k < 3; k++) {
      const ph = (t * 0.45 + k / 3 + seed * 0.07) % 1;
      L.push(Shape.disc, x, y + 0.04, z, 0.5 + 1.5 * ph, rgb[0], rgb[1], rgb[2], 0.6 * s * (1 - ph), 0, 1, 0);
    }
    L.push(Shape.orb, x, y + 1.0, z, 1.6, rgb[0], rgb[1], rgb[2], 0.1 * s);
    for (let k = 0; k < 3; k++) {
      const u = (t * 0.2 + k / 3) % 1;
      const a = t * 0.6 + k * 2.1 + seed;
      L.push(Shape.spark, x + Math.cos(a) * 0.45, y + 1.6 - u * 1.4, z + Math.sin(a) * 0.45, 0.14, rgb[0], rgb[1], rgb[2], 0.55 * s);
    }
  }

  /** Staggered (Adveni): glints circling above a head at (x, y, z). */
  dazed(x: number, y: number, z: number, strength = 1, seed = 0): void {
    const s = Math.max(0, Math.min(1, strength));
    const t = this.time * 7 + seed;
    for (let k = 0; k < 3; k++) {
      const a = t + (k * Math.PI * 2) / 3;
      this.layer.push(Shape.spark, x + Math.cos(a) * 0.3, y + 0.08 * Math.sin(a * 2), z + Math.sin(a) * 0.3, 0.17, GOLD[0], GOLD[1], GOLD[2], 0.9 * s);
    }
  }

  /** A Guardian ward round a duelist whose chest is at (x, y, z): a faint silver halo and motes (the animal itself is drawn elsewhere). */
  ward(x: number, y: number, z: number, rgb: Rgb, strength = 1, seed = 0): void {
    const s = Math.max(0, Math.min(1, strength));
    const t = this.time;
    const L = this.layer;
    const breathe = 1 + 0.05 * Math.sin(t * 2.4 + seed);
    L.push(Shape.disc, x, y - 0.1, z, 1.5 * breathe, rgb[0], rgb[1], rgb[2], 0.35 * s, 0, 1, 0);
    L.push(Shape.orb, x, y, z, 1.9, rgb[0], rgb[1], rgb[2], 0.08 * s);
    for (let k = 0; k < 4; k++) {
      const a = t * 1.3 + (k * Math.PI) / 2 + seed;
      L.push(Shape.spark, x + Math.cos(a) * 0.75, y - 0.4 + 0.5 * Math.sin(t * 1.7 + k), z + Math.sin(a) * 0.75, 0.13, rgb[0], rgb[1], rgb[2], 0.6 * s);
    }
  }

  // ------------------------------------------------------------ bursts

  /** An impact: a flash, a shockwave ring and sparks, `scale` times the size of a bolt's. */
  flash(x: number, y: number, z: number, rgb: Rgb, scale = 1, sparks = 10): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.orb, x, y, z, life: 0.32, size: 0.5 * scale, sizeEnd: 1.6 * scale, rgb });
    b.spawn({ shape: Shape.orb, x, y, z, life: 0.16, size: 0.6 * scale, sizeEnd: 0.9 * scale, rgb: WHITE, alpha: 0.9 });
    b.spawn({ shape: Shape.ring, x, y, z, life: 0.4, size: 0.4 * scale, sizeEnd: 2.4 * scale, rgb, alpha: 0.8 });
    b.sparks(sparks, x, y, z, 3.2 * scale, rgb);
  }

  /** A shield breaking: shards flung outward and down, a blue-white flash. */
  shatter(x: number, y: number, z: number, rgb: Rgb): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.ring, x, y, z, life: 0.3, size: 1.9, sizeEnd: 2.6, rgb, alpha: 0.9 });
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * Math.PI * 2 + b.jitter() * 0.3;
      const r = 0.7 + 0.2 * b.jitter();
      const v = 2.4 + 1.2 * Math.abs(b.jitter());
      b.spawn({ shape: Shape.shard, x: x + Math.cos(a) * r * 0.3, y: y + Math.sin(a) * r * 0.9, z: z + Math.cos(a) * r * 0.3, vx: Math.cos(a) * v * 0.6, vy: Math.sin(a) * v + 1, vz: Math.sin(a) * v * 0.6, life: 0.7, size: 0.22, sizeEnd: 0.1, rgb, gravity: 9 });
    }
  }

  /** A broken cast: a puff of grey-tinted sparks falling from the wand. */
  fizzle(x: number, y: number, z: number, rgb: Rgb): void {
    const b = this.bursts;
    const grey: Rgb = [0.55 + rgb[0] * 0.3, 0.55 + rgb[1] * 0.3, 0.55 + rgb[2] * 0.3];
    b.spawn({ shape: Shape.orb, x, y, z, life: 0.25, size: 0.35, sizeEnd: 0.6, rgb: grey, alpha: 0.7 });
    b.sparks(7, x, y, z, 1.3, grey, 0.6, 0.1);
  }

  /** A wand knocked away: gold glints arcing up and back from (x, y, z), away along (dx, dz). */
  disarm(x: number, y: number, z: number, dx: number, dz: number): void {
    const b = this.bursts;
    for (let i = 0; i < 8; i++) {
      const v = 2 + 1.5 * Math.abs(b.jitter());
      b.spawn({ shape: Shape.spark, x, y, z, vx: dx * v + b.jitter() * 0.6, vy: 3 + b.jitter(), vz: dz * v + b.jitter() * 0.6, life: 0.8, size: 0.18, sizeEnd: 0.08, rgb: GOLD, gravity: 9 });
    }
    b.spawn({ shape: Shape.ring, x, y, z, life: 0.35, size: 0.3, sizeEnd: 1.4, rgb: GOLD, alpha: 0.8 });
  }

  /** A small puff for a spell launching from a wand. */
  muzzle(x: number, y: number, z: number, rgb: Rgb): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.orb, x, y, z, life: 0.18, size: 0.3, sizeEnd: 0.8, rgb });
    b.sparks(4, x, y, z, 1.8, rgb, 0.3, 0.1);
  }

  /** Rigora lands on a duelist whose feet are at (x, y, z): chalk rings closing round the body. */
  bind(x: number, y: number, z: number, rgb: Rgb): void {
    const b = this.bursts;
    for (let k = 0; k < 3; k++) b.spawn({ shape: Shape.disc, x, y: y + 0.35 + k * 0.5, z, life: 0.45 + k * 0.08, size: 2.2, sizeEnd: 0.75, rgb, alpha: 0.95 });
    b.spawn({ shape: Shape.orb, x, y: y + 1.2, z, life: 0.3, size: 0.8, sizeEnd: 1.8, rgb, alpha: 0.8 });
    b.sparks(10, x, y + 1.0, z, 2.2, rgb, 0.5, 0.12);
  }

  /** Tardara lands on a duelist whose feet are at (x, y, z): a heavy, slow ripple. */
  slowHit(x: number, y: number, z: number, rgb: Rgb): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.disc, x, y: y + 0.05, z, life: 1.1, size: 0.4, sizeEnd: 3.2, rgb, alpha: 0.9 });
    b.spawn({ shape: Shape.disc, x, y: y + 0.05, z, life: 0.8, size: 0.2, sizeEnd: 2.0, rgb, alpha: 0.7 });
    b.spawn({ shape: Shape.orb, x, y: y + 1.0, z, life: 0.7, size: 1.2, sizeEnd: 2.4, rgb, alpha: 0.5 });
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2;
      b.spawn({ shape: Shape.spark, x: x + Math.cos(a) * 0.3, y: y + 1.6, z: z + Math.sin(a) * 0.3, vx: Math.cos(a) * 0.6, vy: -0.8, vz: Math.sin(a) * 0.6, life: 1.0, size: 0.16, sizeEnd: 0.06, rgb });
    }
  }

  /** Adveni yanks a duelist at (x, y, z) (chest) toward (dx, dz): a flash and sparks thrown that way. */
  yank(x: number, y: number, z: number, dx: number, dz: number, rgb: Rgb): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.orb, x, y, z, life: 0.25, size: 0.6, sizeEnd: 1.4, rgb });
    b.spawn({ shape: Shape.ring, x, y, z, life: 0.3, size: 0.3, sizeEnd: 1.6, rgb, alpha: 0.8 });
    for (let i = 0; i < 10; i++) {
      const v = 4 + 3 * Math.abs(b.jitter());
      b.spawn({ shape: Shape.spark, x, y: y + b.jitter() * 0.4, z, vx: dx * v + b.jitter() * 0.8, vy: b.jitter() * 0.8, vz: dz * v + b.jitter() * 0.8, life: 0.4, size: 0.15, sizeEnd: 0.05, rgb });
    }
  }

  /** Dissolvo at a duelist whose feet are at (x, y, z): a gold spiral unwinding upward. */
  cleanse(x: number, y: number, z: number, rgb: Rgb = GOLD): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.disc, x, y: y + 0.05, z, life: 0.6, size: 0.4, sizeEnd: 2.4, rgb, alpha: 0.9 });
    b.spawn({ shape: Shape.orb, x, y: y + 1.1, z, life: 0.35, size: 0.7, sizeEnd: 1.8, rgb, alpha: 0.8 });
    for (let i = 0; i < 14; i++) {
      const a = (i / 14) * Math.PI * 4;
      const r = 0.45;
      // Outward and around (tangent), rising: an unwinding spiral.
      b.spawn({ shape: Shape.spark, x: x + Math.cos(a) * r, y: y + 0.2 + i * 0.09, z: z + Math.sin(a) * r, vx: -Math.sin(a) * 1.4 + Math.cos(a) * 0.5, vy: 1.6, vz: Math.cos(a) * 1.4 + Math.sin(a) * 0.5, life: 0.7, size: 0.16, sizeEnd: 0.05, rgb, gravity: -1 });
    }
  }

  /** A Guardian ward rising round a duelist whose feet are at (x, y, z): silver mist welling up. */
  wardUp(x: number, y: number, z: number, rgb: Rgb): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.disc, x, y: y + 0.05, z, life: 0.8, size: 0.5, sizeEnd: 3.0, rgb, alpha: 0.8 });
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * Math.PI * 2 + b.jitter() * 0.3;
      b.spawn({ shape: Shape.orb, x: x + Math.cos(a) * 0.6, y: y + 0.2, z: z + Math.sin(a) * 0.6, vx: Math.cos(a) * 0.3, vy: 1.2 + 0.6 * Math.abs(b.jitter()), vz: Math.sin(a) * 0.3, life: 0.9, size: 0.35, sizeEnd: 0.9, rgb, alpha: 0.45 });
    }
    b.sparks(8, x, y + 1.2, z, 1.8, rgb, 0.7, 0.14);
  }

  /** A spell breaking on a Guardian ward at (x, y, z): a silver flash, a ring and shards flung out. */
  wardBreak(x: number, y: number, z: number, rgb: Rgb): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.orb, x, y, z, life: 0.3, size: 0.9, sizeEnd: 2.6, rgb: WHITE, alpha: 0.9 });
    b.spawn({ shape: Shape.ring, x, y, z, life: 0.45, size: 0.6, sizeEnd: 3.2, rgb, alpha: 0.9 });
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * Math.PI * 2 + b.jitter() * 0.3;
      const up = b.jitter();
      const v = 2.6 + 1.2 * Math.abs(b.jitter());
      b.spawn({ shape: Shape.shard, x, y, z, vx: Math.cos(a) * v, vy: up * v * 0.6 + 1, vz: Math.sin(a) * v, life: 0.75, size: 0.2, sizeEnd: 0.08, rgb, gravity: 7 });
    }
    b.sparks(10, x, y, z, 3, rgb, 0.6, 0.14);
  }

  /** A ward fading unused at a chest at (x, y, z): a soft silver sigh. */
  wardFade(x: number, y: number, z: number, rgb: Rgb): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.orb, x, y, z, life: 0.6, size: 1.6, sizeEnd: 0.6, rgb, alpha: 0.4 });
    b.sparks(6, x, y, z, 0.8, rgb, 0.8, 0.1);
  }

  /** Ends the frame: adds the bursts (dropped first when the layer is full) and uploads. */
  end(camera: Camera): void {
    const L = this.layer;
    this.bursts.forEach((shape, x, y, z, size, r, g, b, a) => {
      L.push(shape, x, y, z, size, r, g, b, a);
    });
    L.end(camera);
  }

  /** Whether anything is still showing (bursts in flight). */
  get busy(): boolean {
    return this.bursts.count > 0;
  }

  clear(): void {
    this.bursts.clear();
  }

  perfLine(label = "spell vfx"): [string, string] {
    return [label, `${this.layer.size} sprites (1 draw call), ${this.bursts.count} particles`];
  }

  dispose(): void {
    this.layer.dispose();
  }
}
