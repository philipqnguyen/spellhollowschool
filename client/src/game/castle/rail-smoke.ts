// The landing rails' smoke (castle/stairs.ts): when a gate's rail
// is conjured in or vanishes, a cloud of soft puffs blooms along its line,
// billows a little and thins out. Simulated on the CPU in one pooled,
// fixed-size array (no allocation per puff) and drawn as sprites of one
// SpriteLayer (effects/billboards.ts, the "puff" shape): one draw. Pure (no
// Babylon), unit tested in stairs.test.ts.

/** Floats per puff: x, y, z, vx, vy, vz, age, life, size. */
const STRIDE = 9;
/** Puffs in one gate's cloud. */
export const PUFFS_PER_BURST = 26;
/** A puff's life (s), give or take a fifth. */
const LIFE = 1.5;
/** Horizontal speeds fall off this much per second (the cloud billows, then hangs). */
const DRAG = 2.2;
/** A puff swells to this times its starting size over its life. */
const SWELL = 1.9;
/** The smoke's colour: a pale, faintly violet grey (magic, not soot). */
export const SMOKE_RGB: readonly [number, number, number] = [0.9, 0.88, 0.97];
/** The smoke's opacity at its thickest. */
const PEAK_ALPHA = 0.95;

/** What draws the puffs (a SpriteLayer). */
export interface PuffSink {
  push(x: number, y: number, z: number, size: number, r: number, g: number, b: number, a: number): boolean;
}

export class RailSmoke {
  private readonly data: Float32Array;
  private live = 0;
  private seed = 7;

  constructor(readonly capacity: number) {
    this.data = new Float32Array(capacity * STRIDE);
  }

  get count(): number {
    return this.live;
  }

  /** A cheap deterministic jitter in [-1, 1). */
  private jitter(): number {
    this.seed = (Math.imul(this.seed, 1664525) + 1013904223) >>> 0;
    return this.seed / 2147483648 - 1;
  }

  /**
   * A cloud along the gate line from (x0, z0) to (x1, z1), its foot at `y`:
   * puffs from the floor to above the rail's coping, spread along the line,
   * drifting out to both sides and up. Past the pool's capacity the rest are dropped.
   */
  burst(x0: number, z0: number, x1: number, z1: number, y: number): void {
    const dx = x1 - x0;
    const dz = z1 - z0;
    const len = Math.hypot(dx, dz) || 1;
    // Across the line (either side): the cloud billows over the landing and the drop alike.
    const nx = -dz / len;
    const nz = dx / len;
    for (let k = 0; k < PUFFS_PER_BURST && this.live < this.capacity; k++) {
      const t = (k + 0.5) / PUFFS_PER_BURST + 0.03 * this.jitter();
      const side = 0.35 * this.jitter();
      const o = this.live * STRIDE;
      const d = this.data;
      d[o] = x0 + dx * t + nx * side * 0.4;
      d[o + 1] = y + 0.2 + 0.4 * (k % 3) + 0.15 * this.jitter();
      d[o + 2] = z0 + dz * t + nz * side * 0.4;
      const along = 0.25 * this.jitter();
      d[o + 3] = nx * side * 1.6 + (dx / len) * along;
      d[o + 4] = 0.3 + 0.15 * this.jitter();
      d[o + 5] = nz * side * 1.6 + (dz / len) * along;
      d[o + 6] = 0;
      d[o + 7] = LIFE * (1 + 0.2 * this.jitter());
      d[o + 8] = 0.95 + 0.25 * this.jitter();
      this.live++;
    }
  }

  /** Ages and moves every puff, dropping the spent ones (swap-remove, order does not matter). */
  step(dt: number): void {
    const d = this.data;
    const keep = Math.max(0, 1 - DRAG * dt);
    let i = 0;
    while (i < this.live) {
      const o = i * STRIDE;
      d[o + 6] = d[o + 6]! + dt;
      if (d[o + 6]! >= d[o + 7]!) {
        this.live--;
        if (i !== this.live) d.copyWithin(o, this.live * STRIDE, this.live * STRIDE + STRIDE);
        continue;
      }
      d[o] = d[o]! + d[o + 3]! * dt;
      d[o + 1] = d[o + 1]! + d[o + 4]! * dt;
      d[o + 2] = d[o + 2]! + d[o + 5]! * dt;
      d[o + 3] = d[o + 3]! * keep;
      d[o + 4] = d[o + 4]! * (0.5 + 0.5 * keep);
      d[o + 5] = d[o + 5]! * keep;
      i++;
    }
  }

  /** Pushes every live puff: thick at once (it hides the rail coming and going), swelling, thinning to nothing. */
  draw(sink: PuffSink): void {
    const d = this.data;
    const [r, g, b] = SMOKE_RGB;
    for (let i = 0; i < this.live; i++) {
      const o = i * STRIDE;
      const u = d[o + 6]! / d[o + 7]!;
      const a = PEAK_ALPHA * Math.min(1, u / 0.06) * Math.pow(1 - u, 1.4);
      const size = d[o + 8]! * (1 + (SWELL - 1) * Math.sqrt(u));
      if (!sink.push(d[o]!, d[o + 1]!, d[o + 2]!, size, r, g, b, a)) return;
    }
  }

  clear(): void {
    this.live = 0;
  }
}
