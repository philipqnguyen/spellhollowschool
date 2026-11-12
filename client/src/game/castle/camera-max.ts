// How far the follow camera may zoom out, per cell (docs/art/castle-kit.md,
// "The client side"): big halls let the player see the
// room (24 m in the Entrance Hall, Great Hall, Grand Staircase and
// courtyard; 20 m in the common rooms), everywhere else keeps 16 m, and Low
// caps every cell at 18 m. The manifest's `camera.max` decides when a cell
// carries one; until the export writes it, the one table is
// data/world/castle_kit.json's `camera_max` (the same numbers the export
// writes from), so the longer zoom works today.
//
// `ZoomLimit` is the camera's zoom state: the wheel's chosen distance, the
// current cell's limit, and the limit actually applied, which eases towards
// the cell's (so walking into a smaller cell pulls the camera in smoothly).
// Pure and allocation-free; camera.ts calls it every frame.

import type { Quality } from "../../settings";
import { camera_max as KIT_CAMERA } from "../../../../data/world/castle_kit.json";

/** The camera's default follow distance (m); unchanged by the kit. */
export const DEFAULT_DISTANCE = 6.5;
/** Closest wheel zoom (m). */
export const MIN_DISTANCE = 1.6;
/** Furthest zoom of a cell without its own value (m): camera.ts's limit before the kit. */
export const DEFAULT_MAX_DISTANCE: number = KIT_CAMERA.default;
/** Low's cap on every cell's furthest zoom (m). */
export const LOW_MAX_DISTANCE: number = KIT_CAMERA.low_cap;
/** No cell zooms further than this (m), whatever a manifest says. */
export const ABSOLUTE_MAX_DISTANCE = 40;
/** How fast the applied limit follows the cell's (1/s): about a second to settle. */
export const LIMIT_RATE = 3;

const TABLE = KIT_CAMERA.cells as Record<string, number | undefined>;

/** The manifest fields this reads (docs/art/castle-kit.md "Manifest fields"). */
export interface CellCamera {
  camera?: { max?: unknown };
}

/**
 * The furthest zoom in `cellId` (m): the manifest's `camera.max` when the
 * cell has one, else castle_kit.json's table, else 16 m; capped at 18 m on
 * Low. An unknown cell (a dorm, nowhere) gets the default.
 */
export function cameraMaxFor(cellId: string | null | undefined, manifest: CellCamera | undefined, quality: Quality): number {
  const fromManifest = manifest?.camera?.max;
  let max = typeof fromManifest === "number" && Number.isFinite(fromManifest) ? fromManifest : cellId ? (TABLE[cellId] ?? DEFAULT_MAX_DISTANCE) : DEFAULT_MAX_DISTANCE;
  max = Math.min(ABSOLUTE_MAX_DISTANCE, Math.max(MIN_DISTANCE + 1, max));
  return quality === "low" ? Math.min(max, LOW_MAX_DISTANCE) : max;
}

export class ZoomLimit {
  /** The distance chosen with the wheel (m). */
  desired = DEFAULT_DISTANCE;
  /** The current cell's furthest zoom (m). */
  target = DEFAULT_MAX_DISTANCE;
  /** The furthest zoom applied now, easing towards `target` (m). */
  limit = DEFAULT_MAX_DISTANCE;
  private snapped = false;

  /** The current cell's furthest zoom (cameraMaxFor). The first call applies it at once. */
  setTarget(max: number): void {
    this.target = max;
    if (!this.snapped) {
      this.snapped = true;
      this.limit = max;
    }
  }

  /**
   * One wheel step (positive zooms out). It starts from the distance shown,
   * not a longer choice the current cell is holding in, so zooming in
   * always responds at once; it may go out to the cell's own limit straight
   * away (the applied limit eases after it).
   */
  wheel(step: number): void {
    // Already zoomed out as far as this cell lets: keep the longer choice for the next big room.
    if (step > 0 && this.desired >= this.target) return;
    const from = Math.min(this.desired, this.limit);
    this.desired = Math.min(this.target, Math.max(MIN_DISTANCE, from * (1 + step * 0.12)));
  }

  /** Eases the applied limit; returns the distance the camera wants this frame (before walls). */
  update(dt: number): number {
    if (this.limit !== this.target) {
      const k = 1 - Math.exp(-Math.max(0, dt) * LIMIT_RATE);
      this.limit += (this.target - this.limit) * k;
      if (Math.abs(this.target - this.limit) < 0.01) this.limit = this.target;
    }
    return Math.min(this.desired, this.limit);
  }
}
