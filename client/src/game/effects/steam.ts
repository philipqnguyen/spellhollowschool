// Pick-Me-Up Tonic steam: puffs from both ears, simulated on the CPU in one
// pooled, fixed-size array (no allocation per puff) and drawn as sprites.
// The pool is capped: in a crowd of steaming characters the nearest ones,
// which emit first each frame, keep their steam and the far ones thin out.
// Pure (no Babylon), unit tested in effects.test.ts.

/** Floats per puff: x, y, z, vx, vy, vz, age, life. */
const STRIDE = 8;
/** Puffs per second from each ear. */
export const PUFFS_PER_EAR = 6;
const LIFE = 0.95;
/** Beyond this camera distance (m) a character emits at half rate; beyond `STEAM_FAR` not at all. */
const STEAM_HALF = 22;
export const STEAM_FAR = 40;

export interface Emitter {
  /** Fractional puffs owed (keeps the rate steady across frames). */
  owed: number;
}

export class SteamPool {
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
  private jitter(): number {
    this.seed = (Math.imul(this.seed, 1664525) + 1013904223) >>> 0;
    return this.seed / 2147483648 - 1;
  }

  /**
   * Emits for one character this frame: feet at (x, y, z) facing `yaw`, head
   * top `head` metres up, at `scale`, `distance` from the camera. Puffs come
   * from both ears and drift up and outwards.
   */
  emit(e: Emitter, dt: number, x: number, y: number, z: number, yaw: number, head: number, scale: number, distance: number): void {
    if (distance > STEAM_FAR) {
      e.owed = 0;
      return;
    }
    const rate = PUFFS_PER_EAR * 2 * (distance > STEAM_HALF ? 0.5 : 1);
    e.owed += rate * dt;
    // Ears: a little below the top of the head, either side (right = (cos yaw, 0, -sin yaw)).
    const ey = y + head * 0.9;
    const rx = Math.cos(yaw);
    const rz = -Math.sin(yaw);
    const side = 0.085 * scale;
    let ear = 0;
    while (e.owed >= 1) {
      e.owed -= 1;
      if (this.live >= this.capacity) {
        e.owed = 0;
        return;
      }
      const s = ear++ % 2 === 0 ? 1 : -1;
      const o = this.live * STRIDE;
      const d = this.data;
      d[o] = x + rx * side * s;
      d[o + 1] = ey;
      d[o + 2] = z + rz * side * s;
      const out = (0.22 + 0.08 * this.jitter()) * scale;
      d[o + 3] = rx * out * s + 0.05 * this.jitter();
      d[o + 4] = (0.5 + 0.12 * this.jitter()) * scale;
      d[o + 5] = rz * out * s + 0.05 * this.jitter();
      d[o + 6] = 0;
      d[o + 7] = LIFE * (0.85 + 0.15 * this.jitter());
      this.live++;
    }
  }

  /** Ages and moves every puff, dropping the spent ones (swap-remove, order does not matter). */
  step(dt: number): void {
    const d = this.data;
    let i = 0;
    while (i < this.live) {
      const o = i * STRIDE;
      d[o + 6]! += dt;
      if (d[o + 6]! >= d[o + 7]!) {
        this.live--;
        if (i !== this.live) d.copyWithin(o, this.live * STRIDE, this.live * STRIDE + STRIDE);
        continue;
      }
      d[o]! += d[o + 3]! * dt;
      d[o + 1]! += d[o + 4]! * dt;
      d[o + 2]! += d[o + 5]! * dt;
      // Puffs slow as they spread.
      const drag = Math.max(0, 1 - 1.6 * dt);
      d[o + 3]! *= drag;
      d[o + 5]! *= drag;
      i++;
    }
  }

  /** Calls `draw(x, y, z, size, alpha)` for every live puff. */
  forEach(draw: (x: number, y: number, z: number, size: number, alpha: number) => void): void {
    const d = this.data;
    for (let i = 0; i < this.live; i++) {
      const o = i * STRIDE;
      const t = d[o + 6]! / d[o + 7]!;
      draw(d[o]!, d[o + 1]!, d[o + 2]!, 0.06 + 0.22 * t, 0.72 * (1 - t) * Math.min(1, t * 6));
    }
  }

  clear(): void {
    this.live = 0;
  }
}
