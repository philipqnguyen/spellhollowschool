// Stable sun shadows (docs/architecture.md, "Rendering"): the sun's
// shadow map (High, scene.ts) is a fixed 70 m orthographic square that
// follows the player. Its texels are laid out in the light's own space (the
// view Babylon's ShadowGenerator builds: LookAtLH from the light position
// along its direction, up +Y), so the map rasterizes the castle the same way
// frame after frame only if the light's position moves by whole texels along
// that view's right and up axes. Snapping the position on the world x and z
// axes does not do that while the sun is not straight overhead: a step on a
// world axis is a fraction of a texel on both light axes, and every shadow
// edge re-rasterizes (crawls) as the player walks. Pure, unit tested.

export type Vec3 = [number, number, number];

/**
 * The light's view axes for direction `dir` (from the light into the scene):
 * right = normalize(Y × dir), up = dir × right, as Babylon's LookAtLH with up
 * +Y (shadowGenerator.ts `getTransformMatrix`). Null when `dir` is (nearly)
 * vertical or not finite (the caller keeps the unsnapped centre).
 */
export function lightAxes(dir: Readonly<Vec3>): { right: Vec3; up: Vec3; forward: Vec3 } | null {
  const len = Math.hypot(dir[0], dir[1], dir[2]);
  if (!(len > 0) || !Number.isFinite(len)) return null;
  const f: Vec3 = [dir[0] / len, dir[1] / len, dir[2] / len];
  // Y × f = (f.z, 0, -f.x)
  const rl = Math.hypot(f[2], f[0]);
  if (rl < 1e-6) return null;
  const right: Vec3 = [f[2] / rl, 0, -f[0] / rl];
  // f × right
  const up: Vec3 = [f[1] * right[2] - f[2] * right[1], f[2] * right[0] - f[0] * right[2], f[0] * right[1] - f[1] * right[0]];
  return { right, up, forward: f };
}

const dot = (a: Readonly<Vec3>, b: Readonly<Vec3>) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/**
 * The shadow map's centre for a point `c` (the player), snapped to the
 * light's texel grid: its coordinates on the light's right and up axes
 * rounded to `texel`, its depth along the light kept. Moving `c` anywhere
 * then shifts the map by whole texels only (or not at all), so static
 * shadow edges never re-rasterize. Writes into `out` and returns it.
 */
export function snapShadowCentre(c: Readonly<Vec3>, dir: Readonly<Vec3>, texel: number, out: Vec3 = [0, 0, 0]): Vec3 {
  const axes = lightAxes(dir);
  if (!axes || !(texel > 0)) {
    out[0] = c[0];
    out[1] = c[1];
    out[2] = c[2];
    return out;
  }
  const { right, up, forward } = axes;
  const r = Math.round(dot(c, right) / texel) * texel;
  const u = Math.round(dot(c, up) / texel) * texel;
  const d = dot(c, forward);
  for (let k = 0; k < 3; k++) out[k] = right[k]! * r + up[k]! * u + forward[k]! * d;
  return out;
}
