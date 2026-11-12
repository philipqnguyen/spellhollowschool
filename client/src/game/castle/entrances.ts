// The hidden passages' entrances and the Wishing Room's door in the
// castle (docs/art/castle-kit.md; nodes.ts `entranceAnimOf`,
// `isWishingRoomDoorNode`).
//
// Each entrance is one node in the public room's cell, standing in front of
// its passage's door. While anyone stands on (or right at) that door, it
// moves aside, and moves back once they have gone: the tapestry's cloth
// lifts and sways, the painting swings out on its hinge, the statue turns
// aside, the panel slides along the wall, the stretch of wall sinks into the
// floor. Walls stay the absence of ground (no collision): it is only a look.
// The node's own shape gives the motion its axes (its long side runs along
// the wall; the door's middle lies behind it), so the art needs no pivots.
//
// The four house entrances (the guardian portrait, the knocker
// door, the hidden stone door, the barrel stack) are entrances of the same
// kind, on their common rooms' doors; a door with a `house` opens only for
// characters of that house (the gate itself is sim-core's: other houses
// find a wall there).
//
// Knocker door opens as a pair: the knocker door (style "double") is two
// leaves, each its own entrance turning about its own hinge (its nodes'
// origin, the leaf's outer edge), both into the passage, away from the
// corridor, so the pair parts at the centre.
//
// The Wishing Room's door is hidden until the server opens it for us
// (`wishingRoomDoor`); it fades in, and out when it goes. Until the Secrets
// builder's door node is exported, a plain door of palette wood stands in.

import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Matrix, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import type { Node } from "@babylonjs/core/node";
import type { Scene } from "@babylonjs/core/scene";
import { audio, entranceSound } from "../../audio";
import { entranceAnimOf, isWishingRoomDoorNode, type EntranceStyle } from "./nodes";
import { paletteRgb } from "./terrain";

/** How fast an entrance opens and closes (fraction a second). */
const OPEN_RATE = 2.5;
/**
 * How far each leaf of a double door turns when open (radians; castle_kit
 * grid.py DOUBLE_DOOR_TURN): the knocker door's 2 m leaves hang 0.35 m into
 * a 2 m wall, so past about 51 degrees their tall meeting stiles would come
 * out through the common room's wall above its arch.
 */
export const DOUBLE_TURN = (50 * Math.PI) / 180;
/** Someone this close (m, in plan) to a door's rectangle opens its entrance. */
const DOOR_REACH = 0.8;
/** And within this height (m) of the door's floor. */
const DOOR_HEIGHT = 2;

/** A door rectangle (`LayoutSurface` of a passage door, or a common room's door). */
export interface DoorRect {
  min: [number, number];
  max: [number, number];
  y: number;
  /** A common room's door opens only for this house (lower-case id). */
  house?: string;
}

/**
 * How an entrance stands when `open` (0 shut .. 1 aside) at `t` seconds.
 * Pure. `turn`: radians about its axis (the cloth's top edge, the
 * painting's hinge, the statue's middle, a double door's leaf's hinge: each
 * leaf turns this much); `slide`: metres along the wall; `sink`: metres down.
 */
export function entrancePose(style: EntranceStyle, open: number, t: number): { turn: number; slide: number; sink: number } {
  const o = Math.max(0, Math.min(1, open));
  const ease = o * o * (3 - 2 * o);
  switch (style) {
    case "tapestry":
      // Lifted aside with a little sway while held open.
      return { turn: ease * 0.5 + Math.sin(t * 3.1) * 0.05 * ease, slide: 0, sink: 0 };
    case "painting":
      return { turn: ease * 1.25, slide: 0, sink: 0 };
    case "statue":
      return { turn: ease * (Math.PI / 2), slide: 0, sink: 0 };
    case "double":
      return { turn: ease * DOUBLE_TURN, slide: 0, sink: 0 };
    case "panel":
      return { turn: 0, slide: ease * 0.95, sink: 0 };
    case "wall":
      return { turn: 0, slide: 0, sink: ease * 0.97 };
  }
}

/**
 * Whether a character's feet at (x, y, z) open the entrance of `door`; a
 * house's door (`door.house`) only for a character of that house (`house`,
 * lower-case). Pure.
 */
export function atDoor(door: DoorRect, x: number, y: number, z: number, house?: string): boolean {
  if (door.house && house?.toLowerCase() !== door.house.toLowerCase()) return false;
  return (
    x >= door.min[0] - DOOR_REACH &&
    x <= door.max[0] + DOOR_REACH &&
    z >= door.min[1] - DOOR_REACH &&
    z <= door.max[1] + DOOR_REACH &&
    Math.abs(y - door.y) <= DOOR_HEIGHT
  );
}

interface Part {
  mesh: AbstractMesh;
  base: Matrix;
  world: Matrix;
}

interface Entrance {
  cell: string;
  door: string;
  style: EntranceStyle;
  parts: Part[];
  /** The node's middle, its extent along the wall and its height. */
  centre: Vector3;
  along: Vector3;
  width: number;
  bottom: number;
  top: number;
  /** Horizontal unit toward the passage (the door's middle, behind the entrance). */
  inward: Vector3;
  /** A double door's leaf: its hinge (its nodes' origin), about which it turns. */
  hinge: Vector3 | null;
  open: number;
  /** The pose last drawn (skip still frames). */
  drawn: string;
}

/** Characters' feet, ours first (pooled by the caller), each with the character's house (lower-case) for the house doors. */
export type Feet = readonly (readonly [number, number, number, string?])[];

export class KitEntrances {
  private entrances: Entrance[] = [];
  private door: { cell: string; meshes: AbstractMesh[]; nodes: Node[] } | null = null;
  private fallback: Mesh | null = null;
  private doorAlpha = -1;
  private readonly m = new Matrix();
  private readonly r = new Matrix();
  private readonly v = new Vector3();

  constructor(
    private readonly scene: Scene,
    /** The castle layout's door surfaces by id (the passages' doors). */
    private readonly doors: ReadonlyMap<string, DoorRect>,
    /** Where the Wishing Room's door stands (its interactable), for the stand-in door. */
    private readonly wishingRoomDoorAt: { pos: [number, number, number]; yaw: number } | null,
  ) {}

  /** For the castle: its passage doors and the Wishing Room's door from its layout. */
  static forCastle(scene: Scene, layoutSurfaces: readonly { id: string; kind: string; min: [number, number]; max: [number, number]; y?: number; house?: string }[], interactables: readonly { kind: string; pos: [number, number, number]; yaw: number }[]): KitEntrances {
    const doors = new Map<string, DoorRect>();
    for (const s of layoutSurfaces) if (s.kind === "door") doors.set(s.id, { min: s.min, max: s.max, y: s.y ?? 0, ...(s.house ? { house: s.house } : {}) });
    const it = interactables.find((i) => i.kind === "wishing_room_door");
    return new KitEntrances(scene, doors, it ? { pos: it.pos, yaw: it.yaw } : null);
  }

  /** Entrances wired (resident cells) and how many stand open now. */
  get counts(): { wired: number; open: number } {
    return { wired: this.entrances.length, open: this.entrances.filter((e) => e.open > 0).length };
  }

  /** A freshly loaded cell's meshes and transform nodes. Returns how many entrances (and the door) it had. */
  wire(cell: string, nodes: readonly Node[]): number {
    let n = 0;
    // An entrance may come as several nodes (one per
    // material: the guardian portrait 2, each knocker door leaf 3); every node of
    // one door moves as one entrance, about their combined bounds. A double
    // door's leaf is an entrance of its own (keyed by door and leaf), turning
    // about its nodes' origin.
    const groups = new Map<string, { door: string; style: EntranceStyle; meshes: AbstractMesh[]; hinge: Vector3 | null }>();
    for (const node of nodes) {
      if (isWishingRoomDoorNode(node)) {
        const meshes = meshesOf(node);
        this.door = { cell, meshes, nodes: [node] };
        this.doorAlpha = -1;
        this.disposeFallback();
        n++;
        continue;
      }
      const a = entranceAnimOf(node);
      if (!a) continue;
      const key = a.style === "double" && a.leaf ? `${a.door}|${a.leaf}` : a.door;
      const g = groups.get(key);
      if (g) g.meshes.push(...meshesOf(node).filter((m) => !g.meshes.includes(m)));
      else groups.set(key, { door: a.door, style: a.style, meshes: meshesOf(node), hinge: a.style === "double" && a.leaf ? originOf(node) : null });
    }
    for (const a of groups.values()) {
      const door = a.door;
      const parts: Part[] = [];
      const min = new Vector3(Infinity, Infinity, Infinity);
      const max = new Vector3(-Infinity, -Infinity, -Infinity);
      for (const mesh of a.meshes) {
        // It moves: its bounds follow it (frustum culling).
        mesh.doNotSyncBoundingInfo = false;
        mesh.computeWorldMatrix(true);
        const base = mesh.getWorldMatrix().clone();
        mesh.freezeWorldMatrix(base.clone());
        parts.push({ mesh, base, world: base.clone() });
        const info = mesh.getBoundingInfo();
        info.update(base);
        const bb = info.boundingBox;
        min.minimizeInPlace(bb.minimumWorld);
        max.maximizeInPlace(bb.maximumWorld);
      }
      if (!parts.length) continue;
      const centre = min.add(max).scale(0.5);
      const dx = max.x - min.x;
      const dz = max.z - min.z;
      const along = dx >= dz ? new Vector3(1, 0, 0) : new Vector3(0, 0, 1);
      const across = dx >= dz ? new Vector3(0, 0, 1) : new Vector3(1, 0, 0);
      const d = this.doors.get(door);
      let inward = across;
      if (d) {
        const toDoor = new Vector3((d.min[0] + d.max[0]) / 2 - centre.x, 0, (d.min[1] + d.max[1]) / 2 - centre.z);
        if (Vector3.Dot(toDoor, across) < 0) inward = across.scale(-1);
      }
      this.entrances.push({ cell, door, style: a.style, parts, centre, along, width: Math.max(dx, dz), bottom: min.y, top: max.y, inward, hinge: a.hinge, open: 0, drawn: "" });
      n++;
    }
    return n;
  }

  /** Forgets a cell's entrances (it is being unloaded). */
  unwire(cell: string): void {
    this.entrances = this.entrances.filter((e) => e.cell !== cell);
    if (this.door?.cell === cell) this.door = null;
  }

  /**
   * Once a frame in the castle: `feet` of everyone drawn (ourselves
   * included), `t` seconds for the sway, `doorAlpha` how much of the Wishing
   * Room's door shows (0..1).
   */
  update(dt: number, t: number, feet: Feet, doorAlpha: number): void {
    for (const e of this.entrances) {
      const d = this.doors.get(e.door);
      let want = 0;
      if (d) for (const f of feet) if (atDoor(d, f[0], f[1], f[2], f[3])) want = 1;
      const step = OPEN_RATE * Math.min(dt, 0.1);
      const was = e.open;
      e.open = want > e.open ? Math.min(want, e.open + step) : Math.max(want, e.open - step);
      // Sound: it starts to open (or to close) at its middle; a double door's leaves share one.
      if ((was === 0 && e.open > 0) || (was === 1 && e.open < 1)) {
        const id = entranceSound(e.style, e.door);
        if (id) audio.playAt(id, [e.centre.x, e.centre.y, e.centre.z], { rate: e.open > was ? 1 : 0.92 });
      }
      this.pose(e, t);
    }
    this.showDoor(doorAlpha);
  }

  private pose(e: Entrance, t: number): void {
    const p = entrancePose(e.style, e.open, t);
    const key = `${p.turn.toFixed(4)}|${p.slide.toFixed(4)}|${p.sink.toFixed(4)}`;
    if (key === e.drawn) return;
    e.drawn = key;
    const m = this.m;
    if (p.turn !== 0) {
      // About the cloth's top edge (along the wall), the painting's hinge or the statue's middle (upright).
      let axis: Vector3;
      let pivot: Vector3;
      if (e.style === "tapestry") {
        axis = e.along;
        pivot = new Vector3(e.centre.x, e.top, e.centre.z);
      } else if (e.style === "double") {
        // About the leaf's hinge (its outer edge; failing an origin, the end of the leaf farther from the door's middle).
        axis = Vector3.Up();
        pivot = e.hinge ?? this.outerEnd(e);
      } else {
        axis = Vector3.Up();
        pivot = e.style === "painting" ? e.centre.subtract(e.along.scale(e.width / 2)) : e.centre;
      }
      // The cloth's hem lifts toward the passage; the painting's free edge swings into the room;
      // a double door's leaves (their middles) swing into the passage, away from the corridor.
      const probe =
        e.style === "tapestry" ? new Vector3(e.centre.x, e.bottom, e.centre.z) : e.style === "double" ? e.centre : e.centre.add(e.along.scale(e.width / 2));
      const want = e.style === "tapestry" || e.style === "double" ? e.inward : e.inward.scale(-1);
      const sign = this.turnSign(axis, pivot, probe, want);
      Matrix.TranslationToRef(-pivot.x, -pivot.y, -pivot.z, m);
      Matrix.RotationAxisToRef(axis, p.turn * sign, this.r);
      m.multiplyToRef(this.r, m);
      Matrix.TranslationToRef(pivot.x, pivot.y, pivot.z, this.r);
      m.multiplyToRef(this.r, m);
    } else {
      const s = e.along.scale(p.slide * e.width);
      Matrix.TranslationToRef(s.x, -p.sink * (e.top - e.bottom), s.z, m);
    }
    for (const part of e.parts) {
      part.base.multiplyToRef(m, part.world);
      part.mesh.freezeWorldMatrix(part.world);
    }
  }

  /** A leaf's end along the wall farther from its door's middle (its outer edge), on its floor. */
  private outerEnd(e: Entrance): Vector3 {
    const d = this.doors.get(e.door);
    const a = e.centre.add(e.along.scale(e.width / 2));
    const b = e.centre.subtract(e.along.scale(e.width / 2));
    let end = a;
    if (d) {
      const mid = new Vector3((d.min[0] + d.max[0]) / 2, 0, (d.min[1] + d.max[1]) / 2);
      if (Vector3.Dot(b.subtract(mid), e.along) ** 2 > Vector3.Dot(a.subtract(mid), e.along) ** 2) end = b;
    }
    return new Vector3(end.x, e.bottom, end.z);
  }

  /** +1 or -1: the turn that moves `probe` toward `want`. */
  private turnSign(axis: Vector3, pivot: Vector3, probe: Vector3, want: Vector3): number {
    Matrix.RotationAxisToRef(axis, 0.3, this.r);
    const rel = probe.subtract(pivot);
    Vector3.TransformCoordinatesToRef(rel, this.r, this.v);
    return Vector3.Dot(this.v.subtract(rel), want) >= 0 ? 1 : -1;
  }

  /** The Wishing Room's door: hidden at 0, faded in up to 1. */
  private showDoor(alpha: number): void {
    const a = Math.max(0, Math.min(1, alpha));
    if (Math.abs(a - this.doorAlpha) < 0.005) return;
    this.doorAlpha = a;
    if (!this.door && a > 0) this.makeFallback();
    const meshes = this.door ? this.door.meshes : this.fallback ? [this.fallback] : [];
    const nodes: Node[] = this.door ? this.door.nodes : this.fallback ? [this.fallback] : [];
    for (const n of nodes) n.setEnabled(a > 0);
    for (const mesh of meshes) {
      mesh.setEnabled(a > 0);
      mesh.visibility = a;
    }
  }

  /** A plain door of palette wood where the Wishing Room's door stands (until its node is exported). */
  private makeFallback(): void {
    const at = this.wishingRoomDoorAt;
    if (this.fallback || !at) return;
    const box = CreateBox("wishing_room_door_standin", { width: 1.7, height: 2.8, depth: 0.12 }, this.scene);
    box.position.set(at.pos[0], at.pos[1] + 1.4, at.pos[2]);
    box.rotation.y = at.yaw;
    const mat = new StandardMaterial("wishing_room_door_standin", this.scene);
    const rgb = paletteRgb(["oak_aged", "oak"]) ?? ([0.36, 0.23, 0.13] as const);
    mat.diffuseColor = new Color3(rgb[0], rgb[1], rgb[2]);
    mat.specularColor = Color3.Black();
    // The toon lights leave a plain material dark: a little of its own colour.
    mat.emissiveColor = new Color3(rgb[0] * 0.45, rgb[1] * 0.45, rgb[2] * 0.45);
    box.material = mat;
    box.isPickable = false;
    box.setEnabled(false);
    this.fallback = box;
  }

  private disposeFallback(): void {
    this.fallback?.material?.dispose();
    this.fallback?.dispose();
    this.fallback = null;
  }

  dispose(): void {
    this.entrances = [];
    this.door = null;
    this.disposeFallback();
  }
}

/** A node's origin in the world (a double door's leaf: its hinge), or null for a plain node. */
function originOf(node: Node): Vector3 | null {
  const t = node as Partial<TransformNode>;
  return typeof t.computeWorldMatrix === "function" ? t.computeWorldMatrix.call(node, true).getTranslation() : null;
}

/** A node's drawn meshes (itself, when it is one, and its children). */
function meshesOf(node: Node): AbstractMesh[] {
  const own = "getTotalVertices" in node && (node as AbstractMesh).getTotalVertices() > 0 ? [node as AbstractMesh] : [];
  return [...own, ...node.getChildMeshes(false).filter((m) => m.getTotalVertices() > 0)];
}
