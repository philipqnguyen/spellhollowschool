// Elevara in dorm rooms: a piece of furniture floats up, bobs
// for a few seconds and settles back (`Levitating { itemId, by, durationMs }`,
// sent to everyone in the room; its placement never changes). The height,
// bob and timing come from data/spells.json (`social.levitate`); the room's
// furniture renderer lifts the piece (DormController.liftFurniture) and the
// piece shimmers in the spell's colour while it floats.
//
// `liftAt` is pure and unit tested (spells.test.ts).

import { LEVITATION, spellRgb } from "./data";
import type { SpellFx } from "./vfx";

const rules = LEVITATION.social.kind === "levitate" ? LEVITATION.social : { duration_ms: 5000, height_m: 0.5, bob_m: 0.08, bob_hz: 0.8, range_m: 6, cooldown_ms: 6000, kind: "levitate" as const };
const RGB = spellRgb(LEVITATION);
/** Rising and settling take this long (ms), within the float's duration. */
const RISE_MS = 500;
const SETTLE_MS = 700;

/** Height (m) of a floating piece `elapsedMs` into a float of `durationMs`: rise, bob, settle. */
export function liftAt(elapsedMs: number, durationMs: number, heightM = rules.height_m, bobM = rules.bob_m, bobHz = rules.bob_hz): number {
  if (!(elapsedMs > 0) || !(durationMs > 0) || elapsedMs >= durationMs) return 0;
  const rise = Math.min(RISE_MS, durationMs / 3);
  const settle = Math.min(SETTLE_MS, durationMs / 3);
  let env = 1;
  if (elapsedMs < rise) {
    const t = elapsedMs / rise;
    env = 1 - (1 - t) * (1 - t); // ease out
  } else if (elapsedMs > durationMs - settle) {
    const t = (durationMs - elapsedMs) / settle;
    env = t * t * (3 - 2 * t); // smooth landing
  }
  const bob = Math.sin((elapsedMs / 1000) * bobHz * Math.PI * 2) * bobM;
  return Math.max(0, env * (heightM + bob));
}

/** Where a placed piece is, for the shimmer: the centre of its top and its footprint radius (m). */
export type ItemSpot = (itemId: number) => { x: number; y: number; z: number; radius: number; height: number } | null;

interface Float {
  start: number;
  duration: number;
}

export class Levitations {
  private readonly floats = new Map<number, Float>();
  private time = 0;

  /** `Levitating`: `itemId` floats for `durationMs` from local time `now`. */
  start(itemId: number, durationMs: number, now: number): void {
    this.floats.set(itemId, { start: now, duration: Math.max(0, Math.min(durationMs, 60_000)) });
  }

  get count(): number {
    return this.floats.size;
  }

  /** Spells L20: the pieces floating now. */
  ids(): IterableIterator<number> {
    return this.floats.keys();
  }

  /**
   * Spells L20: Dissolvo lands `itemId` (`Levitating` with duration 0): it
   * settles from where it is now instead of floating on. Returns whether it
   * was floating.
   */
  land(itemId: number, now: number): boolean {
    const f = this.floats.get(itemId);
    if (!f) return false;
    const elapsed = Math.max(0, now - f.start);
    // The settle of a float ending SETTLE_MS from now (liftAt's own landing; continuous from the bob).
    if (f.duration - elapsed > SETTLE_MS) f.duration = elapsed + SETTLE_MS;
    return true;
  }

  /** Per frame: lifts every floating piece and draws its shimmer; settled pieces go back down. */
  update(now: number, dt: number, setLift: (itemId: number, dy: number) => void, spot: ItemSpot, fx: SpellFx): void {
    if (this.floats.size === 0) return;
    this.time += dt;
    for (const [itemId, f] of this.floats) {
      const elapsed = now - f.start;
      const dy = liftAt(elapsed, f.duration);
      setLift(itemId, dy);
      if (elapsed >= f.duration) {
        this.floats.delete(itemId);
        continue;
      }
      const s = spot(itemId);
      if (!s) continue;
      // A few glints circling the piece, and a soft glow under it.
      for (let k = 0; k < 4; k++) {
        const a = this.time * 1.6 + k * (Math.PI / 2) + itemId;
        const r = s.radius * 0.9 + 0.1;
        fx.pushGlint(s.x + Math.cos(a) * r, s.y + dy + s.height * (0.3 + 0.2 * Math.sin(this.time * 2 + k)), s.z + Math.sin(a) * r, 0.12, RGB, 0.85);
      }
      fx.pushGlow(s.x, s.y + dy * 0.4, s.z, s.radius * 1.6 + 0.3, RGB, 0.25);
    }
  }

  /** Puts every floating piece down at once (leaving the room). */
  clear(setLift: (itemId: number, dy: number) => void): void {
    for (const id of this.floats.keys()) setLift(id, 0);
    this.floats.clear();
  }
}
