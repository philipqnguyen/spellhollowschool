// The node conventions of kitted cells (docs/art/castle-kit.md "glTF conventions"),
// read from what the glTF loader gives: glTF node extras arrive as
// `node.metadata.gltf.extras` (the loader's ExtrasAsMetadata extension,
// registered by cells.ts).
//
// - `<cell>.collider` with `extras.role = "camera_collider"`: the camera's
//   collision proxy, never drawn.
// - `<cell>.<material>_lod1` with `extras.kit_lod = { base, distance }`:
//   drawn instead of `base` beyond `distance` (castle/lod.ts).

import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";

export const CAMERA_COLLIDER_ROLE = "camera_collider";

export interface KitLodExtras {
  /** Name of the LOD0 node (`<cell>.<material>`). */
  base: string;
  /** From this distance on (m, camera to the base's bounding box) the LOD1 node is drawn. */
  distance: number;
}

export function extrasOf(node: { metadata?: unknown }): Record<string, unknown> | null {
  const md = node.metadata as { gltf?: { extras?: unknown } } | null | undefined;
  const ex = md?.gltf?.extras;
  return ex && typeof ex === "object" ? (ex as Record<string, unknown>) : null;
}

/** Whether `mesh` is a kitted cell's camera collider (by its extras, or failing that its name). */
export function isCameraCollider(mesh: AbstractMesh): boolean {
  const role = extrasOf(mesh)?.role;
  if (role !== undefined) return role === CAMERA_COLLIDER_ROLE;
  return mesh.name.endsWith(".collider");
}

/** The `kit_lod` extras of a LOD1 node, or null (also when malformed). */
export function kitLodOf(mesh: AbstractMesh): KitLodExtras | null {
  const lod = extrasOf(mesh)?.kit_lod as { base?: unknown; distance?: unknown } | undefined;
  if (!lod || typeof lod.base !== "string" || typeof lod.distance !== "number" || !Number.isFinite(lod.distance) || lod.distance < 0) return null;
  return { base: lod.base, distance: lod.distance };
}

// Castle kit M3 + M4 (docs/art/castle-kit.md): one more node extra.
// `kit_anim = { kind: "bob", group, amp, period, phase }` on a candle
// group's base node (`<cell>.castle_props.candles_<k>`): it floats
// (castle/candles.ts).

export interface KitAnimExtras {
  kind: "bob";
  /** The group's name (`candles_<k>`). */
  group: string;
  /** Bob height (m), period (s) and phase (radians). */
  amp: number;
  period: number;
  phase: number;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The `kit_anim` extras of a moving group's base, or null (also when malformed or of an unknown kind). */
export function kitAnimOf(mesh: AbstractMesh): KitAnimExtras | null {
  const a = extrasOf(mesh)?.kit_anim as Record<string, unknown> | undefined;
  if (!a || a.kind !== "bob" || !finite(a.amp) || !finite(a.period) || !finite(a.phase) || a.period <= 0 || a.amp < 0) return null;
  return { kind: "bob", group: typeof a.group === "string" ? a.group : "", amp: a.amp, period: a.period, phase: a.phase };
}

// The House Cup's nodes in the
// Great Hall (castle/cup.ts): `kit_anim = { kind: "fill", house: "house_x" }`
// on each hourglass's sand and `{ kind: "cup_banners", house }` on each
// house's cup banner group; failing the extras, their names
// `<cell>.castle_props.hourglass_sand_house_x` / `cup_banners_house_x`.

export type CupAnimKind = "fill" | "cup_banners";

export interface CupAnimExtras {
  kind: CupAnimKind;
  /** The castle kit's house slot, `house_a`..`house_d`. */
  slot: string;
}

const CUP_NAME = /\.castle_props\.(hourglass_sand|cup_banners)_(house_[abcd])$/;
const CUP_SLOT = /^house_[abcd]$/;

/** A node's House Cup role, or null. */
export function cupAnimOf(node: { name: string; metadata?: unknown }): CupAnimExtras | null {
  const a = extrasOf(node)?.kit_anim as { kind?: unknown; house?: unknown } | undefined;
  if (a && (a.kind === "fill" || a.kind === "cup_banners")) {
    return typeof a.house === "string" && CUP_SLOT.test(a.house) ? { kind: a.kind, slot: a.house } : null;
  }
  const m = CUP_NAME.exec(node.name);
  return m ? { kind: m[1] === "hourglass_sand" ? "fill" : "cup_banners", slot: m[2]! } : null;
}

/** Whether `node` or one of its parents (a banner group's meshes) is a House Cup node. */
export function inCupNode(node: { name: string; metadata?: unknown; parent?: unknown }): boolean {
  let n: { name: string; metadata?: unknown; parent?: unknown } | null = node;
  for (let i = 0; n && i < 4; i++) {
    if (cupAnimOf(n)) return true;
    n = (n.parent as typeof n | null | undefined) ?? null;
  }
  return false;
}

// Moving staircases (docs/architecture.md "Moving staircases", castle_kit/grid.py):
// each moving staircase flight is ONE node `<cell>.castle_props.swing_<flight>`,
// modelled at rest in position A with its origin at the pivot, with
// `kit_anim = { kind: "swing", flight, pivot: [x, z], angle }` (radians);
// the client turns it about its pivot (castle/stairs.ts). The Owl Loft's owls
// are empty marker nodes `<cell>.owl_<k>` with `kit_anim = { kind: "owl",
// phase }` (castle/owls.ts).

export interface SwingAnimExtras {
  kind: "swing";
  /** The flight's id in data/world/stairs.json (`flight_3_4`). */
  flight: string;
}

/** A node's swing role, or null (also when malformed). */
export function swingAnimOf(node: { metadata?: unknown }): SwingAnimExtras | null {
  const a = extrasOf(node)?.kit_anim as { kind?: unknown; flight?: unknown } | undefined;
  if (!a || a.kind !== "swing" || typeof a.flight !== "string" || !a.flight) return null;
  return { kind: "swing", flight: a.flight };
}

/** Whether `node` or one of its parents is a swing node (its meshes move with it). */
export function inSwingNode(node: { metadata?: unknown; parent?: unknown }): boolean {
  let n: { metadata?: unknown; parent?: unknown } | null = node;
  for (let i = 0; n && i < 4; i++) {
    if (swingAnimOf(n)) return true;
    n = (n.parent as typeof n | null | undefined) ?? null;
  }
  return false;
}

/** An owl marker's phase (radians), or null when `node` is not one. */
export function owlPhaseOf(node: { metadata?: unknown }): number | null {
  const a = extrasOf(node)?.kit_anim as { kind?: unknown; phase?: unknown } | undefined;
  if (a && a.kind === "owl") return finite(a.phase) ? a.phase : 0;
  return null;
}

// Secrets (docs/art/castle-kit.md, castle_kit grid.py ENTRANCE_NODE,
// WISHING_ROOM_DOOR_NODE): each hidden passage's walk-through entrance is one
// node `<cell>.castle_props.entrance_<door surface id>` with `kit_anim =
// { kind: "entrance", door, style }` (castle/entrances.ts sways the cloth or
// slides the panel while someone stands on that door); the Wishing
// Room's door is one node `l7_corridor_east.castle_props.wishing_room_door`
// with `kit_anim = { kind: "wishing_room_door" }`, hidden until the server
// opens it for us.
//
// Knocker door opens as a pair: style "double" is a pair of leaves opening
// from the centre, each its own nodes (`..._leaf_l` / `_leaf_r`, kit_anim
// `leaf: "l" | "r"`) with their origin on that leaf's hinge.

export const ENTRANCE_STYLES = ["tapestry", "panel", "painting", "statue", "wall", "double"] as const;
export type EntranceStyle = (typeof ENTRANCE_STYLES)[number];

export interface EntranceAnimExtras {
  kind: "entrance";
  /** The door surface it stands in front of (`sp_tapestry_door_l5`). */
  door: string;
  style: EntranceStyle;
  /** A double door's leaf (its nodes' origin is the leaf's hinge). */
  leaf?: "l" | "r";
}

export const WISHING_ROOM_DOOR_NODE = "l7_corridor_east.castle_props.wishing_room_door";
const ENTRANCE_NAME = /\.castle_props\.entrance_([a-z0-9_]+)$/;

/** A node's entrance role (by its extras, or failing them its name: a panel), or null. */
export function entranceAnimOf(node: { name: string; metadata?: unknown }): EntranceAnimExtras | null {
  const a = extrasOf(node)?.kit_anim as { kind?: unknown; door?: unknown; style?: unknown; leaf?: unknown } | undefined;
  if (a && a.kind === "entrance") {
    if (typeof a.door !== "string" || !a.door) return null;
    const style = (ENTRANCE_STYLES as readonly unknown[]).includes(a.style) ? (a.style as EntranceStyle) : "panel";
    if (a.leaf === "l" || a.leaf === "r") return { kind: "entrance", door: a.door, style, leaf: a.leaf };
    return { kind: "entrance", door: a.door, style };
  }
  if (a && a.kind !== undefined) return null;
  const m = ENTRANCE_NAME.exec(node.name);
  return m ? { kind: "entrance", door: m[1]!, style: "panel" } : null;
}

/** Whether `node` is the Wishing Room's door (by its extras or its name). */
export function isWishingRoomDoorNode(node: { name: string; metadata?: unknown }): boolean {
  const a = extrasOf(node)?.kit_anim as { kind?: unknown } | undefined;
  if (a && a.kind !== undefined) return a.kind === "wishing_room_door";
  return node.name === WISHING_ROOM_DOOR_NODE;
}

/** Whether `node` or one of its parents is an entrance or the Wishing Room's door (they move or come and go). */
export function inSecretNode(node: { name: string; metadata?: unknown; parent?: unknown }): boolean {
  let n: { name: string; metadata?: unknown; parent?: unknown } | null = node;
  for (let i = 0; n && i < 4; i++) {
    if (entranceAnimOf(n) || isWishingRoomDoorNode(n)) return true;
    n = (n.parent as typeof n | null | undefined) ?? null;
  }
  return false;
}

/**
 * Whether a drawn castle mesh casts the sun's shadow (High). Not the
 * enchanted ceiling (`castle_sky`: a picture of the sky under the hall's roof,
 * which the roof shell already shadows) nor the moving candle groups or the
 * House Cup's sand and banners (castle/cup.ts; a
 * static caster that moves would leave its shadow behind, and they are
 * centimetres of wax): neither may cost draws in the shadow pass,
 * nor the swinging staircase flights (they move), nor the passages'
 * entrances and the Wishing Room's door.
 */
export function castsSunShadow(mesh: AbstractMesh): boolean {
  return kitAnimOf(mesh) === null && !inCupNode(mesh) && !inSwingNode(mesh) && !inSecretNode(mesh) && !/\.castle_sky(\.|_lod1|$)/.test(mesh.name);
}
