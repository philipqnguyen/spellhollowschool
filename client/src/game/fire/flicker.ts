// Animated fire (docs/architecture.md "Rendering"): the one flicker every
// fire shares between its flames, its floor glow and its light. The shaders
// evaluate the same sum (`FLICKER_GLSL`) on the same clock (`fireTime`), so a
// fire's light brightens and dims with its flames without any message
// between them. Three slow sines, never a step: a gentle pulse, no strobe.

/** The fire clock wraps every this many seconds (keeps the shaders' noise inputs small). */
export const FIRE_PERIOD = 600;

/** The fire clock (s) at `nowMs` (performance.now()); shaders get the same number as `fireTime`. */
export function fireTime(nowMs: number): number {
  const t = nowMs / 1000;
  return t - Math.floor(t / FIRE_PERIOD) * FIRE_PERIOD;
}

/** Angular speeds (rad/s) and weights of the flicker's three sines: about 1.2, 2 and 3.5 Hz. */
const W = [7.3, 12.9, 21.7] as const;
const K = [0.55, 0.3, 0.15] as const;

/**
 * A fire's brightness at clock `t` (s) for its seed (0..1): 1 +- `amount`
 * (the sines' weights sum to 1, so the swing never exceeds `amount`).
 */
export function flicker(t: number, seed: number, amount: number): number {
  const s = seed * 6.2831853;
  return 1 + amount * (K[0] * Math.sin(W[0] * t + s * 5.0) + K[1] * Math.sin(W[1] * t + s * 3.0) + K[2] * Math.sin(W[2] * t + s * 7.0));
}

/**
 * Light through water (a lake window's light, manifest `ripple`): two slow
 * sines, periods 6 s and 3.5 s, so the room swells softly rather than
 * flickers. Their speeds are whole turns per `FIRE_PERIOD`, so the swell runs
 * on across the clock's wrap without a jump.
 */
const RIPPLE_W = [(2 * Math.PI * 100) / FIRE_PERIOD, (2 * Math.PI * 170) / FIRE_PERIOD] as const;
const RIPPLE_K = [0.6, 0.4] as const;
/** How far a rippling light's brightness swings (+-). */
export const RIPPLE_AMOUNT = 0.15;

/** A rippling light's brightness at clock `t` (s) for its seed (0..1): 1 +- `RIPPLE_AMOUNT`. */
export function ripple(t: number, seed: number): number {
  const s = seed * 6.2831853;
  return 1 + RIPPLE_AMOUNT * (RIPPLE_K[0] * Math.sin(RIPPLE_W[0] * t + s * 3.0) + RIPPLE_K[1] * Math.sin(RIPPLE_W[1] * t + s * 5.0));
}

/** `flicker` in GLSL (for the flame, glow and ember shaders). */
export const FLICKER_GLSL = `
float fireFlicker(float t, float seed, float amount) {
  float s = seed * 6.2831853;
  return 1.0 + amount * (${K[0].toFixed(2)} * sin(${W[0].toFixed(1)} * t + s * 5.0) + ${K[1].toFixed(2)} * sin(${W[1].toFixed(1)} * t + s * 3.0) + ${K[2].toFixed(2)} * sin(${W[2].toFixed(1)} * t + s * 7.0));
}
`;
