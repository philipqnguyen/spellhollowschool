// Character LOD selection (docs/art/character-adoption.md section 6): which
// of the four tiers each character draws, from its rank (by distance to the
// camera; your own character is rank 0) and distance, re-evaluated at 4 Hz,
// then a budget governor that demotes the farthest characters one tier at a
// time until the characters' triangles fit their allowance. Pure, unit tested.
//
// The allowance (castle D1, docs/art/castle-kit.md):
// the quality's total triangles-in-view budget (world plus characters) less
// the world triangles measured in view (everything drawn that is not a
// character: the castle, rooms, furniture, effects), the largest of the last
// `WORLD_WINDOW` ticks (2 s: the allowance drops at once and rises only after
// the world has stayed smaller for 2 s, so tiers do not flicker as the
// camera turns), never below `CHARACTER_FLOOR`; the nearest
// `PROTECT_NEAREST` characters (you first) are never demoted by it.
//
// The outline hulls (character look pass 2, toon/outline.ts) count (castle
// M5c): at Medium and High a character within the outline's reach, at a
// tier the outline draws, draws its geometry twice (the mesh and its
// inverted hull), so it costs twice its tier's triangles (`drawnTriangles`)
// and the floor keeps the protected nearest at LOD0 with their hulls
// (`characterFloor`). Before, the governor counted one copy and a crowd of 47
// in one common room drew 1.42 M triangles at Medium for 735 k counted.
//
// | Quality | LOD0                  | LOD1    | LOD2    | LOD3        |
// | High    | rank <= 8 and <= 12 m | <= 25 m | beyond  | budget only |
// | Medium  | rank <= 8 and <= 10 m | <= 20 m | beyond  | budget only |
// | Low     | rank <= 2 and <= 6 m  | <= 12 m | <= 25 m | beyond      |
//
// Hysteresis: a character is promoted to a finer tier only once it is 1 m
// and one rank inside that tier's limits; it is demoted as soon as it is
// outside them. Your own character never leaves LOD0.

import type { Quality } from "../../settings";

export type Tier = 0 | 1 | 2 | 3;
export const TIER_COUNT = 4;

export interface LodRule {
  /** LOD0: rank at most this and distance at most `lod0Dist` (m). */
  lod0Rank: number;
  lod0Dist: number;
  /** LOD1 up to this distance, LOD2 up to `lod2Dist`, LOD3 beyond. */
  lod1Dist: number;
  lod2Dist: number;
  /** The quality's triangles-in-view budget, world plus characters (`TRIANGLE_BUDGET`). */
  total: number;
}

/**
 * The triangles-in-view budgets, world plus characters (minimum spec,
 * 2026-10-26: High and Medium are the targets; Low is a fallback that only
 * switches things off and gets no assets of its own).
 */
export const TRIANGLE_BUDGET: Record<Quality, number> = { high: 2_000_000, medium: 1_200_000, low: 500_000 };

/**
 * The castle's guidelines of triangles in view: reported by the perf check,
 * not enforced here; the governor uses what it measures. Low draws the same
 * castle as Medium, so it shares Medium's guideline.
 */
export const CASTLE_IN_VIEW_TARGET: Record<Quality, number> = { high: 400_000, medium: 300_000, low: 300_000 };

export const LOD_RULES: Record<Quality, LodRule> = {
  high: { lod0Rank: 8, lod0Dist: 12, lod1Dist: 25, lod2Dist: Infinity, total: TRIANGLE_BUDGET.high },
  medium: { lod0Rank: 8, lod0Dist: 10, lod1Dist: 20, lod2Dist: Infinity, total: TRIANGLE_BUDGET.medium },
  low: { lod0Rank: 2, lod0Dist: 6, lod1Dist: 12, lod2Dist: 25, total: TRIANGLE_BUDGET.low },
};

/**
 * Per-tier triangle budget of a dressed character (the art's contract, for
 * estimates and checks): the character look's Meshy-direct hair raised the
 * hair's LOD budgets and the tiers by exactly that (docs/art/character-look.md section 2);
 * look pass 2's inner hair layer (LOD0 and LOD1) raised them again (9.1), so
 * they follow `pipeline.DRESSED_MAX` (52,000 / 17,500 / 4,000 / 1,350).
 */
export const TIER_TRIANGLES: readonly number[] = [52_000, 17_500, 4_000, 1_350];

/** The nearest characters (you first) the governor never demotes. */
export const PROTECT_NEAREST = 3;
/** The characters' allowance never falls below this: the protected nearest at LOD0 (no hulls: Low). */
export const CHARACTER_FLOOR = PROTECT_NEAREST * TIER_TRIANGLES[0]!;

/** The allowance's floor: the protected nearest at LOD0, with their outline hulls where outlines are on. */
export function characterFloor(hulls: boolean): number {
  return hulls ? 2 * CHARACTER_FLOOR : CHARACTER_FLOOR;
}

/** Triangles a character draws: its tier's mesh, twice where its outline hull is drawn (the same geometry again). */
export function drawnTriangles(meshTriangles: number, hull: boolean): number {
  return hull ? 2 * meshTriangles : meshTriangles;
}
/** The world count the allowance uses is the largest of this many selection ticks (2 s at 4 Hz). */
export const WORLD_WINDOW = 8;

/** The characters' triangle allowance for a `total` budget with `world` triangles in view (never below `floor`). */
export function characterAllowance(total: number, world: number, floor = CHARACTER_FLOOR): number {
  return Math.max(floor, total - Math.max(0, world));
}

/** How often the tiers are re-evaluated (s). */
export const SELECT_INTERVAL = 0.25;
/** Hysteresis before a promotion: metres and ranks inside the finer tier's limits. */
export const HYSTERESIS_M = 1;
export const HYSTERESIS_RANK = 1;

function rawTier(rule: LodRule, rank: number, d: number, marginRank: number, marginM: number): Tier {
  if (rank <= rule.lod0Rank - marginRank && d <= rule.lod0Dist - marginM) return 0;
  if (d <= rule.lod1Dist - marginM) return 1;
  if (d <= rule.lod2Dist - marginM) return 2;
  return 3;
}

/**
 * The tier for a character at `rank`, `d` metres from the camera, that
 * draws `current` now (hysteresis); `self` is always LOD0.
 */
export function tierFor(rule: LodRule, rank: number, d: number, current: Tier, self = false): Tier {
  if (self) return 0;
  const loose = rawTier(rule, rank, d, 0, 0);
  if (loose >= current) return loose;
  const strict = rawTier(rule, rank, d, HYSTERESIS_RANK, HYSTERESIS_M);
  return strict < current ? strict : current;
}

/**
 * The budget governor: `tiers[i]` for the characters in `order` (indices,
 * nearest first; you are first), demoted from the farthest one tier at a
 * time (a pass over all, then again) until `triangles(i, tier)` summed fits
 * `budget`. The first `protect` characters of `order` are never demoted.
 * Returns the total. Allocation-free.
 */
export function governTiers(tiers: Uint8Array, order: ArrayLike<number>, count: number, protect: number, budget: number, triangles: (i: number, tier: Tier) => number): number {
  let total = 0;
  for (let k = 0; k < count; k++) {
    const i = order[k]!;
    total += triangles(i, tiers[i]! as Tier);
  }
  let changed = true;
  while (total > budget && changed) {
    changed = false;
    for (let k = count - 1; k >= protect && total > budget; k--) {
      const i = order[k]!;
      const t = tiers[i]! as Tier;
      if (t >= 3) continue;
      const next = (t + 1) as Tier;
      total += triangles(i, next) - triangles(i, t);
      tiers[i] = next;
      changed = true;
    }
  }
  return total;
}
