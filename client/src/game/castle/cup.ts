// The House Cup in the Great Hall (docs/architecture.md):
// the four hourglasses fill with the week's standings and the
// winning house's banners hang over its table for the following week.
//
// The castle kit exports, in `great_hall.glb`:
// - `great_hall.castle_props.hourglass_sand_house_a..d`: each house's sand,
//   `extras.kit_anim = { kind: "fill", house: "house_x" }`, modelled full with
//   its origin at the base of the sand, so a Y scale of 0..1 fills it;
// - `great_hall.castle_props.cup_banners_house_a..d`: the cup banners over
//   each house table, `extras.kit_anim = { kind: "cup_banners", house }`,
//   hidden unless that house holds the cup (`badge` of `GET /api/house-cup`).
//
// house_a..d are Roarwing, Burrowsby, Skytalon and Serpentyne
// (`House::ALL`). The nodes are found by their extras or, failing that, by
// their names, as meshes or as groups of meshes (a transform node and its
// children). Older art without them (the static hourglass set) simply has
// nothing to wire: the standings then show in the panel only.
//
// Sand nodes are unfrozen (cells.ts freezes the castle's world matrices) and
// eased toward their fill; once a fill is reached nothing is touched per
// frame. Banners are hidden with `isVisible` (never `setEnabled`: cell
// visibility and the castle hiding behind a dorm enable and disable whole
// cells and would bring them back). Neither casts the sun's shadow
// (castle/nodes.ts `castsSunShadow`): they move or come and go.

import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import { easeFill, slotHouse, type CupHouse } from "../../ui/house-cup-state";
import { cupAnimOf, kitLodOf, type CupAnimKind } from "./nodes";

export interface CupAnim {
  kind: CupAnimKind;
  house: CupHouse;
}

/** A node's House Cup role with its house, or null. */
export function cupNodeOf(node: { name: string; metadata?: unknown }): CupAnim | null {
  const a = cupAnimOf(node);
  const house = a ? slotHouse(a.slot) : null;
  return a && house ? { kind: a.kind, house } : null;
}

type Node = AbstractMesh | TransformNode;

interface Sand {
  node: Node;
  house: CupHouse;
  shown: number;
}

interface Banner {
  house: CupHouse;
  meshes: AbstractMesh[];
}

/** The meshes drawn for `node`: itself if it is a mesh, and its child meshes. */
function meshesOf(node: Node): AbstractMesh[] {
  const own = "getTotalVertices" in node ? [node as AbstractMesh] : [];
  return [...own, ...node.getChildMeshes(false)];
}

export class KitCup {
  private sands: { cell: string; s: Sand }[] = [];
  private banners: { cell: string; b: Banner }[] = [];
  private readonly targets = new Map<CupHouse, number>();
  private badge: ReadonlySet<CupHouse> = new Set();
  /** Whether some sand is still on its way to its fill. */
  private moving = false;

  /** Sand nodes and banner groups wired (resident cells). */
  get counts(): { sands: number; banners: number } {
    return { sands: this.sands.length, banners: this.banners.length };
  }

  /** The House Cup nodes among a freshly loaded cell's meshes and transform nodes (after cells.ts froze them). Returns how many. */
  wire(cell: string, nodes: readonly Node[]): number {
    let n = 0;
    for (const node of nodes) {
      const a = cupNodeOf(node);
      if (!a) continue;
      if (a.kind === "fill") {
        for (const m of [node, ...node.getChildMeshes(false)]) m.unfreezeWorldMatrix();
        const s: Sand = { node, house: a.house, shown: this.targets.get(a.house) ?? 0 };
        node.scaling.y = Math.max(s.shown, 1e-3);
        this.sands.push({ cell, s });
      } else {
        const meshes = meshesOf(node);
        // Their LOD1 nodes (`kit_lod.base` one of them) go with them.
        const names = new Set(meshes.map((m) => m.name));
        for (const other of nodes) {
          const lod = "getTotalVertices" in other ? kitLodOf(other as AbstractMesh) : null;
          if (lod && names.has(lod.base)) meshes.push(other as AbstractMesh);
        }
        const b: Banner = { house: a.house, meshes };
        this.show(b);
        this.banners.push({ cell, b });
      }
      n++;
    }
    if (n) this.moving = true;
    return n;
  }

  /** Forgets a cell's nodes (it is being unloaded). */
  unwire(cell: string): void {
    this.sands = this.sands.filter((e) => e.cell !== cell);
    this.banners = this.banners.filter((e) => e.cell !== cell);
  }

  /** The hourglasses' fills (0..1 by house; missing houses empty). They ease there. */
  setFills(fills: ReadonlyMap<CupHouse, number>): void {
    this.targets.clear();
    for (const [h, f] of fills) this.targets.set(h, Math.min(1, Math.max(0, f)));
    this.moving = true;
  }

  /** The houses whose cup banners hang now (all others hidden). */
  setBadge(houses: ReadonlySet<CupHouse>): void {
    this.badge = houses;
    for (const { b } of this.banners) this.show(b);
  }

  private show(b: Banner): void {
    const on = this.badge.has(b.house);
    for (const m of b.meshes) m.isVisible = on;
  }

  /** Once a frame: eases the sand toward its fill (nothing to do once there). */
  update(dt: number): void {
    if (!this.moving) return;
    let still = false;
    for (const { s } of this.sands) {
      const target = this.targets.get(s.house) ?? 0;
      if (s.shown === target) continue;
      s.shown = easeFill(s.shown, target, dt);
      // A zero scale would make a singular world matrix: keep a sliver.
      s.node.scaling.y = Math.max(s.shown, 1e-3);
      if (s.shown !== target) still = true;
    }
    this.moving = still;
  }

  /** The fill each wired hourglass shows now (F3 overlay, tests). */
  shownFills(): Map<CupHouse, number> {
    return new Map(this.sands.map(({ s }) => [s.house, s.shown]));
  }

  dispose(): void {
    this.sands = [];
    this.banners = [];
  }
}
