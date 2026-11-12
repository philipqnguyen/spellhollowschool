import { describe, expect, it } from "vitest";
import { Matrix, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { lightAxes, snapShadowCentre, type Vec3 } from "./sun-shadow";

const TEXEL = 70 / 2048;
const BACK = 120;
// Sun directions over a day (data/art/lighting.json keys: morning, late morning, noon, late afternoon).
const DIRS: Vec3[] = [
  [-0.8, -0.35, -0.49],
  [-0.331, -0.759, -0.56],
  [0.1, -0.9, -0.42],
  [0.468, -0.641, -0.609],
];

/** The light's view as Babylon's ShadowGenerator builds it (getTransformMatrix: LookAtLH, up +Y). */
function lightView(pos: Vec3, dir: Vec3): Matrix {
  const p = new Vector3(...pos);
  const d = new Vector3(...dir).normalize();
  const m = new Matrix();
  Matrix.LookAtLHToRef(p, p.add(d), Vector3.Up(), m);
  return m;
}

/** A world point's coordinates in the shadow map's texels (light space x, y over the texel). */
function texelCoords(view: Matrix, world: Vec3): [number, number] {
  const v = Vector3.TransformCoordinates(new Vector3(...world), view);
  return [v.x / TEXEL, v.y / TEXEL];
}

const lightAt = (c: Vec3, dir: Vec3): Vec3 => {
  const len = Math.hypot(...dir);
  return [c[0] - (dir[0] / len) * BACK, c[1] - (dir[1] / len) * BACK, c[2] - (dir[2] / len) * BACK];
};

/** Fractional part distance to the nearest integer. */
const offGrid = (v: number) => Math.abs(v - Math.round(v));

describe("sun shadow texel snapping", () => {
  it("builds Babylon's light axes (orthonormal, right horizontal)", () => {
    for (const dir of DIRS) {
      const a = lightAxes(dir)!;
      const view = lightView([0, 0, 0], dir);
      // Babylon's view rows: the light-space x, y, z of the world axes.
      const vx = Vector3.TransformNormal(new Vector3(...a.right), view);
      const vy = Vector3.TransformNormal(new Vector3(...a.up), view);
      const vz = Vector3.TransformNormal(new Vector3(...a.forward), view);
      expect(vx.x).toBeCloseTo(1, 6); // Babylon's matrices are float32
      expect(vy.y).toBeCloseTo(1, 6);
      expect(vz.z).toBeCloseTo(1, 6);
      expect(a.right[1]).toBe(0);
    }
    expect(lightAxes([0, -1, 0])).toBeNull();
    expect(lightAxes([0, 0, 0])).toBeNull();
    expect(lightAxes([Number.NaN, -1, 0])).toBeNull();
  });

  it("moves the map by whole texels only, wherever the player stands", () => {
    // A fixed point of the castle keeps the same position within its texel
    // for every player position: the map rasterizes it the same way.
    const castlePoint: Vec3 = [61.37, 7.2, 50.004];
    let seed = 7;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) * 40 - 20;
    for (const dir of DIRS) {
      const ref = texelCoords(lightView(lightAt(snapShadowCentre([96, 0, 66], dir, TEXEL), dir), dir), castlePoint);
      for (let i = 0; i < 50; i++) {
        const feet: Vec3 = [96 + rand(), Math.abs(rand()) / 4, 66 + rand()];
        const t = texelCoords(lightView(lightAt(snapShadowCentre(feet, dir, TEXEL), dir), dir), castlePoint);
        expect(offGrid(t[0] - ref[0])).toBeLessThan(1e-4);
        expect(offGrid(t[1] - ref[1])).toBeLessThan(1e-4);
      }
    }
  });

  it("the old world-axis snapping did not (the regression this replaces)", () => {
    const dir = DIRS[1]!;
    const worldSnap = (c: Vec3): Vec3 => [Math.round(c[0] / TEXEL) * TEXEL, c[1], Math.round(c[2] / TEXEL) * TEXEL];
    const castlePoint: Vec3 = [61.37, 7.2, 50.004];
    const ref = texelCoords(lightView(lightAt(worldSnap([96, 0, 66]), dir), dir), castlePoint);
    const moved = texelCoords(lightView(lightAt(worldSnap([96 + 5 * TEXEL, 0, 66]), dir), dir), castlePoint);
    expect(Math.max(offGrid(moved[0] - ref[0]), offGrid(moved[1] - ref[1]))).toBeGreaterThan(0.05);
  });

  it("keeps the centre within half a texel of the player and its depth along the light", () => {
    for (const dir of DIRS) {
      const f = lightAxes(dir)!.forward;
      for (const c of [[0, 0, 0], [96.123, 1.7, 66.45], [-500.5, 40, 1234.25]] as Vec3[]) {
        const s = snapShadowCentre(c, dir, TEXEL);
        expect(Math.hypot(s[0] - c[0], s[1] - c[1], s[2] - c[2])).toBeLessThanOrEqual((TEXEL * Math.SQRT2) / 2 + 1e-9);
        expect(s[0] * f[0] + s[1] * f[1] + s[2] * f[2]).toBeCloseTo(c[0] * f[0] + c[1] * f[1] + c[2] * f[2], 9);
      }
    }
  });

  it("is idempotent and leaves the centre alone for a vertical sun", () => {
    const dir = DIRS[2]!;
    const once = snapShadowCentre([12.34, 5, -7.89], dir, TEXEL);
    const twice = snapShadowCentre([...once] as Vec3, dir, TEXEL);
    for (let k = 0; k < 3; k++) expect(twice[k]).toBeCloseTo(once[k]!, 9);
    expect(snapShadowCentre([1.01, 2, 3.03], [0, -1, 0], TEXEL)).toEqual([1.01, 2, 3.03]);
    const out: Vec3 = [0, 0, 0];
    expect(snapShadowCentre([1, 2, 3], dir, TEXEL, out)).toBe(out);
  });
});
