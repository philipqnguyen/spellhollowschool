// Which zone the client is in (docs/architecture.md, "Zones"),
// and the interaction prompt for doors. Pure (no Babylon, no DOM) so it is
// unit tested; zones-scene.ts does the scene swapping.
//
// A zone is the castle or one owner's dorm instance. `Welcome` (on every new
// connection) and `ZoneChanged` both put the client somewhere; the scene for
// that zone then loads behind a fade. `ZoneMachine` hands out a ticket for
// each load so that a load overtaken by a newer arrival is thrown away:
//
//   arrive(castle) ──► load #1 ──► loaded(#1) ──► ready (castle)
//   arrive(dorm A) ──► load #2 ─┐
//   arrive(dorm B) ──► load #3 ─┼─► loaded(#2): stale, ignored
//                               └─► loaded(#3) ──► ready (dorm B)
//
// An arrival in the zone that is already shown (a reconnect, a respawn)
// needs no load: the player is just placed.

import type { Interactable, Vec3, Zone } from "../wasm/sim_wasm";
import { parseManifest, type CellInfo } from "./cellplan";
import { toonPointIntensity } from "./toon/torches";
import { mergeFire, parseFire, type FireData } from "./fire/data";

/** Where the server put the client: `Welcome.zone/spawn/yaw` or `ZoneChanged`. */
export interface ZoneArrival {
  zone: Zone;
  spawn: Vec3;
  /** Facing on arrival, radians. */
  yaw: number;
}

/** Identifies a zone's scene: the castle, or one owner's room. */
export function zoneKey(zone: Zone): string {
  switch (zone.kind) {
    case "castle":
      return "castle";
    case "dorm":
      return `dorm:${zone.ownerName}`;
    case "class":
      return `class:${zone.class}:${zone.section}`;
    case "arena":
      return `arena:${zone.duelId}`;
    // One opener's Wishing Room (either room: one instance per opener).
    case "wishing_room":
      return `wishing_room:${zone.ownerName}`;
  }
}

export function sameZone(a: Zone | null, b: Zone | null): boolean {
  return a !== null && b !== null && zoneKey(a) === zoneKey(b);
}

export type DormRole = "owner" | "guest";

/** The player's role in `zone`: owner or guest of a dorm, or null in the castle. */
export function dormRole(zone: Zone | null, myName: string): DormRole | null {
  if (!zone || zone.kind !== "dorm") return null;
  return zone.ownerName === myName ? "owner" : "guest";
}

/**
 * The player's role in an owned room: a dorm or a Wishing
 * Room (its opener owns it: invites, open to friends, sending guests
 * out). Null in the castle, a class or an arena.
 */
export function roomRole(zone: Zone | null, myName: string): DormRole | null {
  if (!zone || (zone.kind !== "dorm" && zone.kind !== "wishing_room")) return null;
  return zone.ownerName === myName ? "owner" : "guest";
}

/** The Wishing Room's door (`InteractableKind` `wishing_room_door`): shown only while the server holds it open for us. */
export const WISHING_ROOM_DOOR = "wishing_room_door";

/** What the client should do about an arrival. */
export type ZoneStep =
  /** Load the arrival's zone, then call `loaded(ticket)`. */
  | { kind: "load"; ticket: number; arrival: ZoneArrival }
  /** Already in that zone: place the player at the arrival. */
  | { kind: "place"; arrival: ZoneArrival }
  /** That zone is already loading; the running load will settle on this arrival. */
  | { kind: "wait" };

export type ZonePhase = "none" | "loading" | "ready";

export class ZoneMachine {
  private ticket = 0;
  private shown: Zone | null = null;
  private pending: ZoneArrival | null = null;
  private state: ZonePhase = "none";

  /** The zone whose scene is shown (the last one settled). */
  get zone(): Zone | null {
    return this.shown;
  }

  /** The zone being loaded, if any. */
  get loadingZone(): Zone | null {
    return this.state === "loading" ? (this.pending?.zone ?? null) : null;
  }

  get phase(): ZonePhase {
    return this.state;
  }

  /** The player may move and interact: a scene is shown and nothing is loading. */
  get ready(): boolean {
    return this.state === "ready";
  }

  arrive(arrival: ZoneArrival): ZoneStep {
    if (this.state === "loading" && sameZone(this.pending!.zone, arrival.zone)) {
      this.pending = arrival;
      return { kind: "wait" };
    }
    if (this.state === "ready" && sameZone(this.shown, arrival.zone)) {
      // The zone is the same, but its details may not be (a guest's view of
      // the owner's house never changes; keep the newest anyway).
      this.shown = arrival.zone;
      return { kind: "place", arrival };
    }
    this.ticket++;
    this.pending = arrival;
    this.state = "loading";
    return { kind: "load", ticket: this.ticket, arrival };
  }

  /**
   * The load for `ticket` finished. Returns the arrival to place the player
   * at, or null when a newer arrival overtook it (dispose what was loaded).
   */
  loaded(ticket: number): ZoneArrival | null {
    if (ticket !== this.ticket || this.state !== "loading" || !this.pending) return null;
    const arrival = this.pending;
    this.shown = arrival.zone;
    this.pending = null;
    this.state = "ready";
    return arrival;
  }

  /** Whether `ticket` is still the load the client is waiting for. */
  isCurrent(ticket: number): boolean {
    return ticket === this.ticket && this.state === "loading";
  }
}

// ------------------------------------------------------------------ interaction prompt

export interface InteractPrompt {
  /** Interactable id, sent as `Interact.target`. */
  id: string;
  /** "Enter your dormitory". */
  label: string;
}

/**
 * The interactable the player at `pos` can use right now, nearest first, and
 * what the prompt says. `inReach` is the server's rule (`SimWorld.inReach`);
 * `interactables` are those of the zone's world for the player's house (the
 * castle world already leaves out other houses' doors).
 */
export function interactPrompt(
  interactables: readonly Interactable[],
  pos: readonly [number, number, number],
  inReach: (id: string) => boolean,
  zone: Zone | null,
  myName: string,
): InteractPrompt | null {
  let best: Interactable | null = null;
  let bestD = Infinity;
  for (const it of interactables) {
    const d = Math.hypot(it.pos[0] - pos[0], it.pos[2] - pos[2]);
    if (d < bestD && inReach(it.id)) {
      best = it;
      bestD = d;
    }
  }
  if (!best) return null;
  return { id: best.id, label: interactLabel(best, zone, myName) };
}

/** The Owl Loft's owl mail (`InteractableKind` `owl_mail`). */
export const OWL_MAIL = "owl_mail";

/** Whether `it` is the owl mail: it opens the mail panel on the client (the server answers nothing). */
export function isOwlMail(it: Pick<Interactable, "kind">): boolean {
  return it.kind === OWL_MAIL;
}

export function interactLabel(it: Interactable, zone: Zone | null, myName: string): string {
  if (isOwlMail(it)) return "Send an owl";
  switch (it.kind) {
    case "dorm_door":
      return "Enter your dormitory";
    case "dorm_exit":
      return dormRole(zone, myName) === "guest" && zone?.kind === "dorm" ? `Leave ${zone.ownerName}'s room` : "Return to the common room";
    // Crafting stations open their window (`StationOpened`).
    case "find_spot":
      return "Search here";
    case "craft_charms":
      return "Knit";
    case "craft_transfiguration":
      return "Transfigure";
    case "cauldron":
      return "Brew";
    // Class doors admit students during the join window.
    case "class_door":
      return it.name ? `Enter the ${it.name}` : "Enter the classroom";
    case "instance_exit":
      if (zone?.kind === "wishing_room") return zone.ownerName === myName ? "Leave the room" : `Leave ${zone.ownerName}'s room`;
      return zone?.kind === "arena" ? "Leave the arena" : "Leave the class";
    // The door that appears in the blank wall (it offers the two rooms).
    case "wishing_room_door":
      return "Enter the Wishing Room";
    // The House Cup standings and the house's next classes.
    case "notice_board":
      return "Read the notice board";
    // The Owl Order Catalogue's stand in the common room (`StationOpened` opens it).
    case "owl_order":
      return "Browse the Owl Order Catalogue";
    default:
      return it.name ? `Use the ${it.name}` : "Use";
  }
}

// ------------------------------------------------------------------ instance manifest

/**
 * One light of an instance, as the greybox tools write them
 * (`{ id, cell, pos, color, intensity, range }`); all but `pos` optional.
 */
export interface InstanceLight {
  pos: Vec3;
  /** CSS hex; candle glow by default. `colour` is accepted too. */
  color?: string;
  intensity?: number;
  /** Metres. */
  range?: number;
  /** Animated fire: the seed of the fire this light flickers with (game/fire/flicker.ts); absent: steady. */
  flicker?: number;
  /** A lake window's light: the phase (0..1) of its slow swell (game/fire/flicker.ts `ripple`); absent: steady. */
  ripple?: number;
}

/** A room's fill light (`ambient` in the manifest): sky and ground colours (CSS hex) and a strength. */
export interface InstanceAmbient {
  sky: string;
  ground: string;
  intensity: number;
}

/** One layout's entry in `client/public/assets/world/instances.json`. */
export interface InstanceEntry {
  cells: CellInfo[];
  /** The entry's `lights` plus those listed on its cells. */
  lights: InstanceLight[];
  /** The room's fill light, when the manifest gives one. */
  ambient?: InstanceAmbient;
  /** Animated fire (game/fire/): the entry's `fire` and its cells', joined. */
  fire?: FireData;
  /** The room's seats as the manifest gives them (`[{ pos, yaw }]`; castle/seats.ts `Seats.fromRoomSeats`). */
  seats?: unknown[];
}

/**
 * Instance rooms open to the sky (Astronomy's tower top, the creature
 * paddock, the pitch): the castle's sky dome stays on over
 * them, lit for the hour by the game clock (clock/daynight.ts `applySky`).
 * Read from `instances/school.json`, whose rooms carry `"open_sky": true`
 * (tools/blender/world/build_school.py `OPEN_SKY_ROOMS`). Anything malformed is closed.
 */
export function parseOpenSky(json: unknown): Set<string> {
  const out = new Set<string>();
  const rooms = (json as { rooms?: unknown } | null)?.rooms;
  if (!rooms || typeof rooms !== "object" || Array.isArray(rooms)) return out;
  for (const [id, room] of Object.entries(rooms as Record<string, unknown>)) {
    if ((room as { open_sky?: unknown } | null)?.open_sky === true) out.add(id);
  }
  return out;
}

/** Parses `instances.json` (`{ "<layout id>": { cells, lights? } }`); entries that don't parse are left out. */
export function parseInstanceManifest(json: unknown): Map<string, InstanceEntry> {
  const out = new Map<string, InstanceEntry>();
  if (!json || typeof json !== "object") return out;
  for (const [id, raw] of Object.entries(json as Record<string, unknown>)) {
    try {
      const cells = parseManifest(raw).cells;
      const lists = [(raw as { lights?: unknown }).lights, ...cells.map((c) => (c as { lights?: unknown }).lights)];
      const entry: InstanceEntry = { cells, lights: lists.flatMap((l) => (Array.isArray(l) ? l.filter(isLight).map(normalLight) : [])) };
      const ambient = parseAmbient((raw as { ambient?: unknown }).ambient);
      if (ambient) entry.ambient = ambient;
      const seats = (raw as { seats?: unknown }).seats;
      if (Array.isArray(seats)) entry.seats = seats;
      const fire = mergeFire([parseFire((raw as { fire?: unknown }).fire), ...cells.map((c) => parseFire((c as { fire?: unknown }).fire))]);
      if (fire) entry.fire = fire;
      out.set(id, entry);
    } catch (e) {
      console.warn(`instances.json: skipping ${id}`, e);
    }
  }
  return out;
}

const HEX = /^#[0-9a-f]{6}$/i;

function parseAmbient(a: unknown): InstanceAmbient | undefined {
  const { sky, ground, intensity } = (a ?? {}) as Partial<InstanceAmbient>;
  if (typeof sky !== "string" || !HEX.test(sky) || typeof ground !== "string" || !HEX.test(ground)) return undefined;
  if (typeof intensity !== "number" || !Number.isFinite(intensity) || intensity < 0) return undefined;
  return { sky, ground, intensity };
}

/**
 * The castle's torches and candles for the torch pool (toon/torches.ts):
 * the `lights` of the world manifest's cells. Their intensities are the art
 * tools' (Blender watts, about 3-40), scaled to the scene's lights.
 */
export function castleTorches(cells: readonly CellInfo[]): { pos: Vec3; colour: [number, number, number]; intensity: number; range: number; cell: string; flicker?: number; ripple?: number }[] {
  const out: { pos: Vec3; colour: [number, number, number]; intensity: number; range: number; cell: string; flicker?: number; ripple?: number }[] = [];
  for (const c of cells) {
    const lights = (c as { lights?: unknown }).lights;
    if (!Array.isArray(lights)) continue;
    for (const raw of lights) {
      if (!isLight(raw)) continue;
      const l = normalLight(raw as InstanceLight);
      const hex = l.color && HEX.test(l.color) ? l.color : "#ffc877";
      const n = Number.parseInt(hex.slice(1), 16);
      out.push({
        pos: [l.pos[0], l.pos[1], l.pos[2]],
        colour: [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255],
        intensity: toonPointIntensity(l.intensity),
        range: Math.min(16, Math.max(3, l.range ?? 8)),
        // Castle kit M2: the torch pool lights only visible cells (toon/torches.ts).
        cell: c.id,
        ...(l.flicker !== undefined ? { flicker: l.flicker } : {}),
        ...(l.ripple !== undefined ? { ripple: l.ripple } : {}),
      });
    }
  }
  return out;
}

function isLight(l: unknown): boolean {
  const p = (l as InstanceLight | null)?.pos;
  return Array.isArray(p) && p.length === 3 && p.every((v) => typeof v === "number");
}

function normalLight(l: InstanceLight & { colour?: string }): InstanceLight {
  const out: InstanceLight = { pos: l.pos, color: l.color ?? l.colour, intensity: l.intensity, range: l.range };
  if (typeof l.flicker === "number" && Number.isFinite(l.flicker)) out.flicker = l.flicker;
  if (typeof l.ripple === "number" && Number.isFinite(l.ripple)) out.ripple = l.ripple;
  return out;
}
