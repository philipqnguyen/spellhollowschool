// The `?layout` debug overlay (docs/art/castle-kit.md "The client side"): the walkable rectangles of the zone shown (the same
// layout the server validates movement against) drawn as lines over the
// scene, so reviewers can check the kit against where players can walk:
// rooms cyan, corridors yellow, doors red, stairs violet, courtyard and
// grounds green, each rectangle a few centimetres above its surface (stairs
// follow their slope). `?layout=xray` draws them through walls.
//
// Off by default: game.ts imports this module only with the flag, and the
// lines are one mesh (one draw call) built once per zone.

import { Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { CreateLineSystem } from "@babylonjs/core/Meshes/Builders/linesBuilder";
import type { LinesMesh } from "@babylonjs/core/Meshes/linesMesh";
import type { Scene } from "@babylonjs/core/scene";
import type { LayoutSurface } from "../world";

/** A layout surface as written (stairs and slopes carry `y_top` and `rise`). */
export type OverlaySurface = LayoutSurface;

/** Height above a surface the lines float at (m): clear of the floor's own polygons. */
const LIFT = 0.04;

const KIND_COLOURS: Record<string, [number, number, number]> = {
  room: [0.31, 0.76, 0.97],
  corridor: [1, 0.84, 0.31],
  door: [1, 0.32, 0.32],
  stair: [0.7, 0.53, 1],
  courtyard: [0.41, 0.94, 0.68],
  grounds: [0.41, 0.94, 0.68],
};
const OTHER: [number, number, number] = [1, 1, 1];

/** The surface's height at (x, z): flat surfaces have one, stairs rise linearly towards `rise`. */
export function surfaceHeightAt(s: OverlaySurface, x: number, z: number): number {
  const y = s.y ?? 0;
  if (s.y_top === undefined || !s.rise) return y;
  const [x0, z0] = s.min;
  const [x1, z1] = s.max;
  const fx = x1 > x0 ? (x - x0) / (x1 - x0) : 0;
  const fz = z1 > z0 ? (z - z0) / (z1 - z0) : 0;
  const t = s.rise === "+x" ? fx : s.rise === "-x" ? 1 - fx : s.rise === "+z" ? fz : s.rise === "-z" ? 1 - fz : 0;
  return y + (s.y_top - y) * Math.min(1, Math.max(0, t));
}

/** The overlay's polylines and their colours: one closed outline per surface; doors also get their diagonals. */
export function layoutLines(surfaces: readonly OverlaySurface[]): { lines: Vector3[][]; colors: Color4[][] } {
  const lines: Vector3[][] = [];
  const colors: Color4[][] = [];
  for (const s of surfaces) {
    const [x0, z0] = s.min;
    const [x1, z1] = s.max;
    if (!(x1 > x0) || !(z1 > z0)) continue;
    const p = (x: number, z: number) => new Vector3(x, surfaceHeightAt(s, x, z) + LIFT, z);
    const [r, g, b] = KIND_COLOURS[s.kind] ?? OTHER;
    const c = () => new Color4(r, g, b, 1);
    lines.push([p(x0, z0), p(x1, z0), p(x1, z1), p(x0, z1), p(x0, z0)]);
    colors.push([c(), c(), c(), c(), c()]);
    if (s.kind === "door") {
      lines.push([p(x0, z0), p(x1, z1)], [p(x1, z0), p(x0, z1)]);
      colors.push([c(), c()], [c(), c()]);
    }
  }
  return { lines, colors };
}

export class LayoutOverlay {
  private mesh: LinesMesh | null = null;
  /** Each moving flight's A outline, turned about its pivot with the flight (`setFlightTurns`). */
  private flights = new Map<string, LinesMesh>();

  constructor(
    private readonly scene: Scene,
    private readonly xray = false,
  ) {}

  /** Draws `surfaces` (the zone shown), replacing what was drawn. */
  show(surfaces: readonly OverlaySurface[]): void {
    this.mesh?.dispose();
    this.mesh = null;
    for (const m of this.flights.values()) m.dispose();
    this.flights.clear();
    // A moving flight is drawn once, from its A, and turned (its B is A turned).
    for (const s of surfaces) {
      if (s.moving?.position !== "a") continue;
      const one = layoutLines([s]);
      const m = CreateLineSystem(`layout-overlay-${s.moving.flight}`, one, this.scene);
      m.isPickable = false;
      m.alwaysSelectAsActiveMesh = true;
      if (this.xray) m.renderingGroupId = 1;
      this.flights.set(s.moving.flight, m);
    }
    const { lines, colors } = layoutLines(surfaces.filter((s) => !s.moving));
    if (lines.length === 0) return;
    const mesh = CreateLineSystem("layout-overlay", { lines, colors }, this.scene);
    mesh.isPickable = false;
    mesh.alwaysSelectAsActiveMesh = true;
    // Group 1 draws after the world with the depth cleared: through walls.
    if (this.xray) mesh.renderingGroupId = 1;
    mesh.freezeWorldMatrix();
    this.mesh = mesh;
  }

  /** Turns each moving flight's outline by `rad` about its `pivot` [x, z] (Babylon's Y turn is the layout's). */
  setFlightTurns(turns: ReadonlyMap<string, { pivot: readonly [number, number]; rad: number }>): void {
    for (const [id, t] of turns) {
      const m = this.flights.get(id);
      if (!m) continue;
      m.setPivotPoint(new Vector3(t.pivot[0], 0, t.pivot[1]));
      m.rotation.y = t.rad;
    }
  }

  /** The lines mesh, while something is drawn. */
  get lines(): LinesMesh | null {
    return this.mesh;
  }

  dispose(): void {
    this.mesh?.dispose();
    this.mesh = null;
    for (const m of this.flights.values()) m.dispose();
    this.flights.clear();
  }
}

/** The overlay for the page's URL, or null without `?layout` (`?layout=xray` draws through walls). */
export function layoutOverlayFromUrl(scene: Scene, search: string): LayoutOverlay | null {
  const params = new URLSearchParams(search);
  if (!params.has("layout")) return null;
  return new LayoutOverlay(scene, params.get("layout") === "xray");
}
