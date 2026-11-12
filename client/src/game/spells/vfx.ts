// Spell effects outside duels: clean shapes in each spell's saturated colour
// (docs/art/style-guide.md, the stylized look; no toon shader yet).
//
// - a *trail*: the spell's wand gesture drawn in the air as a ribbon of
//   glints, head first, in front of the caster and facing the camera, so a
//   class sees exactly the movement the professor demonstrates;
// - a *burst*: a ring of glints thrown out from a point (a cast landing, a
//   level-up, a piece of furniture lifting off);
// - single *glows* and *glints* other systems push every frame (lit wands,
//   a floating piece's shimmer).
//
// Everything is drawn with the effects' sprite layers (effects/billboards.ts):
// two thin-instanced meshes, two draw calls in all however many spells are
// in the air, nothing allocated per frame once the pools are warm.

import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import { cameraAxes, SpriteLayer } from "../effects/billboards";

export type Rgb = readonly [number, number, number];

const GLOW_MAX = 160;
const GLINT_MAX = 480;
/** How long a finished trail lingers (ms) and how long a burst lasts. */
const TRAIL_LINGER_MS = 700;
const BURST_MS = 800;
/** Points a trail is resampled to. */
const TRAIL_POINTS = 28;
const TRAILS_MAX = 24;
const BURSTS_MAX = 32;

interface Trail {
  /** World positions, `TRAIL_POINTS` × 3. */
  pts: Float32Array;
  rgb: [number, number, number];
  start: number;
  duration: number;
  size: number;
}

interface Burst {
  x: number;
  y: number;
  z: number;
  rgb: [number, number, number];
  start: number;
  count: number;
  radius: number;
  seed: number;
}

export class SpellFx {
  private readonly glow: SpriteLayer;
  private readonly glint: SpriteLayer;
  private readonly right = new Vector3();
  private readonly up = new Vector3();
  private readonly trails: Trail[] = [];
  private readonly bursts: Burst[] = [];
  private readonly spareTrails: Trail[] = [];

  constructor(scene: Scene) {
    this.glow = new SpriteLayer(scene, "spell-glow", { shape: "glow", blend: "add", capacity: GLOW_MAX });
    this.glint = new SpriteLayer(scene, "spell-glint", { shape: "star", blend: "add", capacity: GLINT_MAX });
  }

  /** The camera's right direction (horizontal), for drawing trails toward the viewer. */
  get cameraRight(): Vector3 {
    return this.right;
  }

  /** Starts a frame: the camera's axes, empty layers. */
  begin(camera: Camera): void {
    cameraAxes(camera, this.right, this.up);
    this.glow.begin();
    this.glint.begin();
  }

  /** A soft glow `size` metres across (a lit wand tip). */
  pushGlow(x: number, y: number, z: number, size: number, rgb: Rgb, a: number): void {
    this.glow.push(x, y, z, size, rgb[0], rgb[1], rgb[2], a);
  }

  /** A four-pointed glint. */
  pushGlint(x: number, y: number, z: number, size: number, rgb: Rgb, a: number): void {
    this.glint.push(x, y, z, size, rgb[0], rgb[1], rgb[2], a);
  }

  /**
   * Draws a gesture in the air: `pad` is its template (-100..100, y up),
   * centred at (`x`, `y`, `z`), `scale` metres per 100 pad units, its x along
   * the horizontal direction (`rx`, `rz`), drawn over `durationMs`.
   */
  trail(pad: readonly (readonly [number, number])[], x: number, y: number, z: number, rx: number, rz: number, scale: number, rgb: Rgb, durationMs: number, now: number): void {
    if (pad.length < 2) return;
    let t = this.spareTrails.pop();
    if (!t) t = { pts: new Float32Array(TRAIL_POINTS * 3), rgb: [1, 1, 1], start: 0, duration: 0, size: 0 };
    const len = Math.hypot(rx, rz) || 1;
    const ux = rx / len;
    const uz = rz / len;
    const flat = resample(pad, TRAIL_POINTS);
    for (let i = 0; i < TRAIL_POINTS; i++) {
      const px = flat[i * 2]! / 100;
      const py = flat[i * 2 + 1]! / 100;
      t.pts[i * 3] = x + ux * px * scale;
      t.pts[i * 3 + 1] = y + py * scale;
      t.pts[i * 3 + 2] = z + uz * px * scale;
    }
    t.rgb = [rgb[0], rgb[1], rgb[2]];
    t.start = now;
    t.duration = Math.max(150, durationMs);
    t.size = 0.07 + scale * 0.12;
    if (this.trails.length >= TRAILS_MAX) this.spareTrails.push(this.trails.shift()!);
    this.trails.push(t);
  }

  /** Glints thrown out in a ring from (`x`, `y`, `z`). */
  burst(x: number, y: number, z: number, rgb: Rgb, now: number, count = 12, radius = 0.6): void {
    if (this.bursts.length >= BURSTS_MAX) this.bursts.shift();
    this.bursts.push({ x, y, z, rgb: [rgb[0], rgb[1], rgb[2]], start: now, count, radius, seed: (x * 13.1 + z * 7.7) % 6.283 });
  }

  /** Steps the trails and bursts and uploads the frame's sprites. */
  end(now: number): void {
    for (let i = this.trails.length - 1; i >= 0; i--) {
      const t = this.trails[i]!;
      const age = now - t.start;
      if (age > t.duration + TRAIL_LINGER_MS) {
        this.spareTrails.push(t);
        this.trails.splice(i, 1);
        continue;
      }
      this.drawTrail(t, age);
    }
    for (let i = this.bursts.length - 1; i >= 0; i--) {
      const b = this.bursts[i]!;
      const age = (now - b.start) / BURST_MS;
      if (age > 1) {
        this.bursts.splice(i, 1);
        continue;
      }
      const ease = 1 - (1 - age) * (1 - age);
      for (let k = 0; k < b.count; k++) {
        const a = b.seed + (k / b.count) * Math.PI * 2;
        const lift = Math.sin(k * 2.3 + b.seed) * 0.35;
        const r = b.radius * ease;
        this.glint.push(b.x + Math.cos(a) * r, b.y + lift * ease + 0.25 * ease, b.z + Math.sin(a) * r, 0.16 * (1 - age * 0.6), b.rgb[0], b.rgb[1], b.rgb[2], 1 - age);
      }
      if (age < 0.4) this.glow.push(b.x, b.y, b.z, b.radius * 1.4, b.rgb[0], b.rgb[1], b.rgb[2], 0.7 * (1 - age / 0.4));
    }
    this.glow.end(this.right, this.up);
    this.glint.end(this.right, this.up);
  }

  private drawTrail(t: Trail, age: number): void {
    const head = Math.min(1, age / t.duration) * (TRAIL_POINTS - 1);
    const fade = age > t.duration ? 1 - (age - t.duration) / TRAIL_LINGER_MS : 1;
    const [r, g, b] = t.rgb;
    const n = Math.floor(head);
    for (let i = 0; i <= n; i++) {
      const o = i * 3;
      // Brighter toward the head; the tail cools as the trail lingers.
      const k = 0.35 + 0.65 * (i / Math.max(1, n));
      this.glint.push(t.pts[o]!, t.pts[o + 1]!, t.pts[o + 2]!, t.size * (0.6 + 0.5 * k), r, g, b, fade * k);
    }
    if (age <= t.duration) {
      // The spark at the head, between two samples.
      const f = head - n;
      const o = n * 3;
      const o2 = Math.min(TRAIL_POINTS - 1, n + 1) * 3;
      const hx = t.pts[o]! + (t.pts[o2]! - t.pts[o]!) * f;
      const hy = t.pts[o + 1]! + (t.pts[o2 + 1]! - t.pts[o + 1]!) * f;
      const hz = t.pts[o + 2]! + (t.pts[o2 + 2]! - t.pts[o + 2]!) * f;
      this.glow.push(hx, hy, hz, t.size * 4, r, g, b, 0.9);
      this.glint.push(hx, hy, hz, t.size * 2.2, 1, 1, 1, 1);
    }
  }

  get active(): number {
    return this.trails.length + this.bursts.length;
  }

  dispose(): void {
    this.glow.dispose();
    this.glint.dispose();
  }
}

/** `n` points evenly spaced along a polyline, flat `[x, y, ...]`. */
export function resample(pts: readonly (readonly [number, number])[], n: number): Float32Array {
  const out = new Float32Array(n * 2);
  let total = 0;
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i]![0] - pts[i - 1]![0], pts[i]![1] - pts[i - 1]![1]);
  if (pts.length < 2 || total <= 0) {
    for (let i = 0; i < n; i++) out.set([pts[0]?.[0] ?? 0, pts[0]?.[1] ?? 0], i * 2);
    return out;
  }
  const step = total / (n - 1);
  let seg = 1;
  let segStart = 0;
  for (let i = 0; i < n; i++) {
    const want = Math.min(total, i * step);
    while (seg < pts.length - 1 && segStart + Math.hypot(pts[seg]![0] - pts[seg - 1]![0], pts[seg]![1] - pts[seg - 1]![1]) < want) {
      segStart += Math.hypot(pts[seg]![0] - pts[seg - 1]![0], pts[seg]![1] - pts[seg - 1]![1]);
      seg++;
    }
    const a = pts[seg - 1]!;
    const b = pts[seg]!;
    const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const f = d > 0 ? Math.min(1, (want - segStart) / d) : 0;
    out[i * 2] = a[0] + (b[0] - a[0]) * f;
    out[i * 2 + 1] = a[1] + (b[1] - a[1]) * f;
  }
  return out;
}
