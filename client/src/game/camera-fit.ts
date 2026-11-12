// How the follow camera fits into tight rooms (camera.ts). Pure, unit tested.
//
// Backed against a wall in a dorm or a common room, pulling the camera in
// along its line of sight put it a hand's width behind the player's head.
// Instead, the less room there is behind the player, the more the camera
// rises and looks down over their shoulder: going up and back usually has
// more room than going straight back (rooms are taller than they are deep
// behind you), and a view from above stays readable even very close.

/** With less free space than this behind the player (m), the camera starts to rise. */
export const COMFORT_DISTANCE = 2.4;
/** At this little space (m) or less it has risen all the way... */
export const TIGHT_DISTANCE = 0.9;
/** ...by this much pitch (radians, toward looking down). */
export const MAX_LIFT = 0.55;

/** Pitch to add (radians) when only `room` metres are free behind the player. */
export function liftFor(room: number): number {
  if (!(room < COMFORT_DISTANCE)) return 0; // also NaN: no hit, no lift
  const t = (COMFORT_DISTANCE - room) / (COMFORT_DISTANCE - TIGHT_DISTANCE);
  return MAX_LIFT * Math.min(1, Math.max(0, t));
}

/**
 * The lift to apply given the player's own pitch `beta` (Babylon's angle
 * from straight up) and the highest the camera may go (`lowestBeta`): never
 * past it, so the camera's own limit never fights the lift.
 */
export function clampLift(lift: number, beta: number, lowestBeta: number): number {
  return Math.max(0, Math.min(lift, beta - lowestBeta));
}

// ------------------------------------------------------------------ The flight camera

/** On a broom the camera sits at least this far back (m): more of the sky and the ground ahead. */
export const FLIGHT_DISTANCE = 9;
/** ...and follows the broom this smoothly (1/s; walking follows at 18). */
export const FLIGHT_FOLLOW_RATE = 5;
/** The flight camera keeps at least this far over the floor under it (m): never inside the ground or the lake. */
export const FLIGHT_FLOOR_CLEARANCE = 0.8;

/**
 * The pitch (`beta`, Babylon's angle from straight up) that keeps a camera
 * `radius` from a target at height `targetY` at least `clearance` over
 * `floorY`: `beta` itself when it already is, else the lowest look-down
 * that is, never higher than `lowestBeta` (the camera's own limit).
 */
export function betaAboveFloor(targetY: number, radius: number, beta: number, floorY: number, clearance: number, lowestBeta: number): number {
  const minY = floorY + clearance;
  if (!(radius > 0) || !Number.isFinite(floorY) || targetY + radius * Math.cos(beta) >= minY) return beta;
  const c = (minY - targetY) / radius;
  if (c >= 1) return lowestBeta;
  return Math.max(lowestBeta, Math.min(beta, Math.acos(c)));
}
