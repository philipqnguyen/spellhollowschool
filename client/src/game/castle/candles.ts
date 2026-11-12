// Floating candles (castle kit M3, docs/art/castle-kit.md): a hall's
// candles are a few moving groups, each one node `<cell>.castle_props.candles_<k>`
// (plus its `_lod1`, the flame cards) with `extras.kit_anim = { kind: "bob",
// group, amp, period, phase }`. Their flames are the props atlas's
// `glow_candle` texels, so they glow under the glow rule like the lanterns.
//
// - Per load (`wire`): each group's base mesh is unfrozen (nothing else in
//   the castle moves). The LOD1 node moves with it without being touched:
//   Babylon draws a LOD level with its master mesh's world matrix
//   (`AbstractMesh.getWorldMatrix` of a mesh with `_masterMesh`), so one
//   matrix a group moves both, which is the contract's TransformNode
//   (base and LOD1 under one moving node) at half the matrix work.
// - Per frame (`update`): `y = y0 + amp * sin(2 pi t / period + phase)` for
//   the groups of shown cells: 4 matrix updates in a hall, no allocation
//   (numbers kept per group; flat arrays, no closures).
// - Low (`LOW_CANDLE_GROUPS`): only the nearest group of a cell
//   (to its bounding box) is drawn and moved; the scene's LOD selector
//   (castle/lod.ts) skips the others. Chosen again every frame.
// - Groups of disabled cells (outside `sees`, or the castle hidden behind a
//   dorm) are skipped. Candles never collide and are not in the collider.

import type { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import { kitAnimOf } from "./nodes";

/** Candle groups of a cell drawn at Low (grid.LOW_CANDLE_GROUPS). */
export const LOW_CANDLE_GROUPS = 1;

interface Group {
  mesh: AbstractMesh;
  /** The base's own height at load: the bob adds to it. */
  y0: number;
  amp: number;
  /** 2 pi / period (rad/s). */
  omega: number;
  phase: number;
  /** At Low: whether this is its cell's nearest group (drawn). */
  drawn: boolean;
}

interface CellGroups {
  id: string;
  groups: Group[];
}

/** The bob of a group at time `t` (s): its height offset (m). Pure, the per-frame formula. */
export function bobOffset(amp: number, period: number, phase: number, t: number): number {
  return amp * Math.sin((2 * Math.PI * t) / period + phase);
}

export class KitCandles {
  private readonly cells: CellGroups[] = [];
  private readonly groupOf = new WeakMap<AbstractMesh, Group>();
  private low = false;
  private moved = 0;

  /** Low: only the nearest group of each cell is drawn and moved. */
  setLow(on: boolean): void {
    this.low = on;
    if (!on) for (const c of this.cells) for (const g of c.groups) g.drawn = true;
  }

  get isLow(): boolean {
    return this.low;
  }

  /** Groups wired (resident cells). */
  get count(): number {
    let n = 0;
    for (const c of this.cells) n += c.groups.length;
    return n;
  }

  /** Groups moved by the last `update` (one matrix update each). */
  get movedLastFrame(): number {
    return this.moved;
  }

  /** Whether `mesh` is a candle group's base. */
  isGroup(mesh: AbstractMesh): boolean {
    return this.groupOf.has(mesh);
  }

  /** The moving groups among a freshly loaded cell's meshes (after cells.ts froze them). Returns how many. */
  wire(cell: string, meshes: readonly AbstractMesh[]): number {
    let entry: CellGroups | null = null;
    for (const m of meshes) {
      const a = kitAnimOf(m);
      if (!a || this.groupOf.has(m)) continue;
      m.unfreezeWorldMatrix();
      const g: Group = { mesh: m, y0: m.position.y, amp: a.amp, omega: (2 * Math.PI) / a.period, phase: a.phase, drawn: true };
      this.groupOf.set(m, g);
      if (!entry) {
        entry = this.cells.find((c) => c.id === cell) ?? null;
        if (!entry) this.cells.push((entry = { id: cell, groups: [] }));
      }
      entry.groups.push(g);
    }
    return entry ? entry.groups.length : 0;
  }

  /** Forgets a cell's groups (it is being unloaded). */
  unwire(cell: string): void {
    const i = this.cells.findIndex((c) => c.id === cell);
    if (i < 0) return;
    for (const g of this.cells[i]!.groups) this.groupOf.delete(g.mesh);
    this.cells.splice(i, 1);
  }

  /** At Low, whether the LOD selector may draw `mesh` (a group other than its cell's nearest is not drawn). */
  drawnAtLow(mesh: AbstractMesh): boolean {
    const g = this.groupOf.get(mesh);
    return !g || g.drawn;
  }

  /**
   * Once a frame, before the scene renders: picks each cell's nearest group
   * at Low and bobs the groups that are drawn. `t` in seconds, `eye` the
   * camera's world position. Allocates nothing.
   */
  update(t: number, eye: Vector3): void {
    let moved = 0;
    const cells = this.cells;
    for (let c = 0; c < cells.length; c++) {
      const groups = cells[c]!.groups;
      if (this.low) this.pickNearest(groups, eye);
      for (let i = 0; i < groups.length; i++) {
        const g = groups[i]!;
        if (!g.drawn || !g.mesh.isEnabled()) continue;
        g.mesh.position.y = g.y0 + g.amp * Math.sin(g.omega * t + g.phase);
        moved++;
      }
    }
    this.moved = moved;
  }

  private pickNearest(groups: Group[], eye: Vector3): void {
    let best: Group | null = null;
    let bestD = Infinity;
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i]!;
      g.drawn = false;
      if (!g.mesh.isEnabled()) continue;
      // Squared distance to the box, inline (no call returning a double: no heap number a frame).
      const box = g.mesh.getBoundingInfo().boundingBox;
      const lo = box.minimumWorld, hi = box.maximumWorld;
      const dx = eye.x < lo.x ? lo.x - eye.x : eye.x > hi.x ? eye.x - hi.x : 0;
      const dy = eye.y < lo.y ? lo.y - eye.y : eye.y > hi.y ? eye.y - hi.y : 0;
      const dz = eye.z < lo.z ? lo.z - eye.z : eye.z > hi.z ? eye.z - hi.z : 0;
      const d = dx * dx + dy * dy + dz * dz;
      if (d < bestD) {
        bestD = d;
        best = g;
      }
    }
    if (best) best.drawn = true;
  }

  dispose(): void {
    for (const c of this.cells) for (const g of c.groups) this.groupOf.delete(g.mesh);
    this.cells.length = 0;
  }
}
