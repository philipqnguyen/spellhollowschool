// Movement maths shared by the local player and the camera. Pure, unit tested.
//
// Yaw convention (docs/art/base-body-spec.md): yaw 0 faces +Z (south) and the
// forward vector is (sin yaw, 0, cos yaw), so yaw π/2 faces +X (east).

/** Unit horizontal vector the player faces at `yaw`. */
export function forwardOf(yaw: number): [number, number] {
  return [Math.sin(yaw), Math.cos(yaw)];
}

/** Yaw facing along the horizontal direction (dx, dz), in [0, 2π). */
export function yawOf(dx: number, dz: number): number {
  const yaw = Math.atan2(dx, dz);
  return yaw < 0 ? yaw + 2 * Math.PI : yaw;
}

/**
 * World-space unit direction for keyboard input relative to the camera.
 *
 * @param forward +1 forward (W), -1 back (S)
 * @param strafe +1 right (D), -1 left (A)
 * @param camDx, camDz horizontal direction the camera looks along (need not be unit length)
 * @returns `[dx, dz]` of length 1, or null when there is no input
 */
export function inputDirection(forward: number, strafe: number, camDx: number, camDz: number): [number, number] | null {
  if (forward === 0 && strafe === 0) return null;
  const len = Math.hypot(camDx, camDz);
  if (len < 1e-6) return null;
  const fx = camDx / len;
  const fz = camDz / len;
  // Right-handed, Y up: right = forward × up = (-fz, 0, fx).
  const rx = -fz;
  const rz = fx;
  const dx = fx * forward + rx * strafe;
  const dz = fz * forward + rz * strafe;
  const d = Math.hypot(dx, dz);
  return [dx / d, dz / d];
}

/** Turns `current` toward `target` by at most `maxStep` radians, the short way round. Result in [0, 2π). */
export function turnToward(current: number, target: number, maxStep: number): number {
  const tau = 2 * Math.PI;
  let d = (target - current) % tau;
  if (d > Math.PI) d -= tau;
  if (d < -Math.PI) d += tau;
  const next = current + Math.max(-maxStep, Math.min(maxStep, d));
  return ((next % tau) + tau) % tau;
}
