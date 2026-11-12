// Short-lived VFX particles (impact flashes, shockwave rings, sparks,
// shards) in one pooled, fixed-size array: no allocation per particle, and
// a full pool simply refuses new ones. Each particle moves, falls by its own
// gravity, grows from its start size to its end size (eased out) and fades
// over its life. Pure (no Babylon), unit tested in vfx.test.ts.

import { Shape, type ShapeId } from "./shapes";

/** Floats per particle. */
const STRIDE = 16;
// Offsets into a particle.
const X = 0, Y = 1, Z = 2, VX = 3, VY = 4, VZ = 5, AGE = 6, LIFE = 7, S0 = 8, S1 = 9, R = 10, G = 11, B = 12, A = 13, GRAV = 14, SHAPE = 15;

export interface BurstSpec {
  shape: ShapeId;
  x: number;
  y: number;
  z: number;
  vx?: number;
  vy?: number;
  vz?: number;
  /** Seconds. */
  life: number;
  /** Size at birth and at death (m). */
  size: number;
  sizeEnd?: number;
  rgb: readonly [number, number, number];
  alpha?: number;
  /** Downward acceleration (m/s²); negative floats up. */
  gravity?: number;
}

export type ParticleVisitor = (shape: ShapeId, x: number, y: number, z: number, size: number, r: number, g: number, b: number, a: number) => void;

export class BurstPool {
  private readonly data: Float32Array;
  private live = 0;
  private seed = 1;

  constructor(readonly capacity: number) {
    this.data = new Float32Array(capacity * STRIDE);
  }

  get count(): number {
    return this.live;
  }

  /** A cheap deterministic jitter in [-1, 1). */
  jitter(): number {
    this.seed = (Math.imul(this.seed, 1664525) + 1013904223) >>> 0;
    return this.seed / 2147483648 - 1;
  }

  /** Adds one particle; false when the pool is full. */
  spawn(p: BurstSpec): boolean {
    if (this.live >= this.capacity || p.life <= 0) return false;
    const d = this.data;
    const o = this.live * STRIDE;
    d[o + X] = p.x;
    d[o + Y] = p.y;
    d[o + Z] = p.z;
    d[o + VX] = p.vx ?? 0;
    d[o + VY] = p.vy ?? 0;
    d[o + VZ] = p.vz ?? 0;
    d[o + AGE] = 0;
    d[o + LIFE] = p.life;
    d[o + S0] = p.size;
    d[o + S1] = p.sizeEnd ?? p.size;
    d[o + R] = p.rgb[0];
    d[o + G] = p.rgb[1];
    d[o + B] = p.rgb[2];
    d[o + A] = p.alpha ?? 1;
    d[o + GRAV] = p.gravity ?? 0;
    d[o + SHAPE] = p.shape;
    this.live++;
    return true;
  }

  /** `n` sparks flying out from (x, y, z) at about `speed` m/s, a little upward. */
  sparks(n: number, x: number, y: number, z: number, speed: number, rgb: readonly [number, number, number], life = 0.45, size = 0.12): void {
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 + this.jitter() * 0.6;
      const up = 0.35 + 0.45 * this.jitter();
      const v = speed * (0.6 + 0.4 * Math.abs(this.jitter()));
      if (!this.spawn({ shape: Shape.spark, x, y, z, vx: Math.cos(a) * v, vy: up * v, vz: Math.sin(a) * v, life: life * (0.75 + 0.25 * Math.abs(this.jitter())), size, sizeEnd: size * 0.3, rgb, gravity: 6 })) return;
    }
  }

  /** Ages and moves every particle, dropping the spent ones (swap-remove: order does not matter). */
  step(dt: number): void {
    const d = this.data;
    let i = 0;
    while (i < this.live) {
      const o = i * STRIDE;
      d[o + AGE]! += dt;
      if (d[o + AGE]! >= d[o + LIFE]!) {
        this.live--;
        if (i !== this.live) d.copyWithin(o, this.live * STRIDE, this.live * STRIDE + STRIDE);
        continue;
      }
      d[o + VY]! -= d[o + GRAV]! * dt;
      d[o + X]! += d[o + VX]! * dt;
      d[o + Y]! += d[o + VY]! * dt;
      d[o + Z]! += d[o + VZ]! * dt;
      i++;
    }
  }

  /** Visits every live particle with its size and opacity now. */
  forEach(visit: ParticleVisitor): void {
    const d = this.data;
    for (let i = 0; i < this.live; i++) {
      const o = i * STRIDE;
      const t = d[o + AGE]! / d[o + LIFE]!;
      const grow = 1 - (1 - t) * (1 - t); // ease out
      const size = d[o + S0]! + (d[o + S1]! - d[o + S0]!) * grow;
      const a = d[o + A]! * (1 - t) * (1 - t * 0.35);
      visit(d[o + SHAPE]! as ShapeId, d[o + X]!, d[o + Y]!, d[o + Z]!, size, d[o + R]!, d[o + G]!, d[o + B]!, a);
    }
  }

  clear(): void {
    this.live = 0;
  }
}
