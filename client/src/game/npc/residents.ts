// The open castle's residents on this client (docs/architecture.md "NPCs"):
// which drawn characters are roster NPCs and of what kind
// (`EntityInfo.npc` names the roster id), who may be selected or targeted
// (never a student), whose nameplate shows (never a student's), and whom E
// talks to: the nearest NPC within `talk_radius_m` on our floor, entity or
// portrait (portraits only while chatter is not Off). Pure; unit tested in
// residents.test.ts.

import type { NpcKind } from "../../wasm/sim_wasm";
import type { ChatterMode } from "./chatter";
import { inFrontOf } from "./portrait-state";

/** Two points this far apart in height (m) are on different floors. */
export const SAME_FLOOR_M = 2.5;

/** Whether a character of `kind` (null: a player, a professor) may be selected, targeted, challenged, traded with or spelled. */
export function selectable(kind: NpcKind | null): boolean {
  return kind !== "student";
}

/** Whether a character of `kind` wears a nameplate (students never: they must not pass for players, and are background). */
export function hasPlate(kind: NpcKind | null): boolean {
  return kind !== "student";
}

/** Whether a resident of `kind` answers `NpcInteract` (shopkeepers are talked to through their station's `Interact`). */
export function talksByNpcInteract(kind: NpcKind | null): boolean {
  return kind === "ghost" || kind === "staff" || kind === "student";
}

export interface Point {
  x: number;
  y: number;
  z: number;
}

export interface TalkEntity extends Point {
  id: number;
  npc: string;
  kind: NpcKind;
}

export interface TalkPortrait {
  npc: string;
  pos: readonly [number, number, number] | readonly number[];
  /** The way the canvas faces (radians): it is talked to from in front of its wall only. */
  yaw: number;
}

export type TalkTarget = { kind: "entity"; id: number; npc: string; npcKind: NpcKind; d: number } | { kind: "portrait"; npc: string; d: number };

/**
 * Whom E talks to from `me`: the selected resident when it is in reach,
 * else the nearest entity NPC or portrait within `radius` (horizontal, on
 * our floor; a portrait from in front of its wall only). Portraits are left out while chatter is Off. Students count
 * (they bark), professors and players are never in `entities`.
 */
export function talkTarget(me: Point, entities: readonly TalkEntity[], portraits: readonly TalkPortrait[], radius: number, mode: ChatterMode, selected: number | null = null): TalkTarget | null {
  let best: TalkTarget | null = null;
  for (const e of entities) {
    if (Math.abs(e.y - me.y) > SAME_FLOOR_M) continue;
    const d = Math.hypot(e.x - me.x, e.z - me.z);
    if (d > radius) continue;
    if (e.id === selected) return { kind: "entity", id: e.id, npc: e.npc, npcKind: e.kind, d };
    if (!best || d < best.d) best = { kind: "entity", id: e.id, npc: e.npc, npcKind: e.kind, d };
  }
  if (mode === "off") return best;
  for (const p of portraits) {
    const [x, y, z] = p.pos as [number, number, number];
    // The canvas hangs about two metres above the floor it is on.
    if (Math.abs(y - 2 - me.y) > SAME_FLOOR_M || !inFrontOf(p, me.x, me.z)) continue;
    const d = Math.hypot(x - me.x, z - me.z);
    if (d > radius) continue;
    if (!best || d < best.d) best = { kind: "portrait", npc: p.npc, d };
  }
  return best;
}
