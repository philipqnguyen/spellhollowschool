// The map's state (docs/architecture.md "Secrets and the map"): which places the character has revealed (whole places: the surface
// it stands on, `SimWorld.revealAt`, with the rest of its place, `placeOf`; saved by the server, `GET /api/map`, and
// predicted here between saves with the same rule), which secrets it has
// found, and what one layer of the map draws: revealed places in ink, fog
// over the places not yet revealed, found passages by their own names, the
// moving flights where they stand now, friends by the place they are in
// (only a revealed one), and party members: at their spot when they are
// drawn with us, at their place's entrance when they are in a class, a dorm
// or a duel. Unfound secrets are never drawn, fogged or named; nobody's
// footprints are drawn. Pure (no DOM, no wasm), unit tested in
// map.test.ts; map.ts draws it.

import type { MapSurface } from "../wasm/sim_wasm";

/** One map layer: a floor (`level`, 0 the ground floor, below 0 the dungeons) or the grounds. */
export type MapLayer = number | "grounds";

/** What `GET /api/map` answers: the revealed surfaces and the found secrets. */
export interface MapSave {
  surfaces: string[];
  secrets: string[];
}

/** A shape on the map. */
export interface MapShape {
  id: string;
  kind: MapSurface["kind"];
  min: [number, number];
  max: [number, number];
  /** Not revealed yet: drawn as fog (never a secret). */
  fog: boolean;
  /** Part of a found passage. */
  secret: boolean;
  /** The place it belongs to (`placeOf`): a place is often several surfaces, revealed together. */
  place: string;
}

/** A place name on the map, at the middle of its largest revealed surface on the layer. */
export interface MapLabel {
  text: string;
  x: number;
  z: number;
  /** A found passage's name (drawn in its own style). */
  secret: boolean;
}

/** A friend's pin: at the place they are in. */
export interface MapPin {
  name: string;
  x: number;
  z: number;
}

/** A layer as drawn. */
export interface MapView {
  shapes: MapShape[];
  labels: MapLabel[];
  /** Everything drawn on the layer (fog too), for fitting the view; null when empty. */
  bounds: { min: [number, number]; max: [number, number] } | null;
}

/** A party member's pin; `note` says where they are when it stands at an entrance ("in Charms"). */
export interface PartyPin extends MapPin {
  note?: string;
}

/**
 * A party member as the map gets it (game/party.ts): exactly where they
 * stand (`at`: drawn with us in the castle), at an entrance (`entrance`: in a
 * class, a dorm room, a duel), or by their castle place's name (`place`).
 */
export interface PartyMapMember {
  name: string;
  at?: { x: number; z: number; surfaceId: string | undefined };
  /**
   * `surfaceId` undefined: the spot is on ground our house does not walk (another house's
   * common room, where its dormitory door is): the pin goes to the nearest public ground at height `y`.
   */
  entrance?: { x: number; y?: number; z: number; surfaceId: string | undefined; note: string };
  place?: string;
}

/** A friend online, where the list says they are (`Location`). */
export interface FriendPlace {
  name: string;
  /** The place name (`Location::Castle.place`), or null when not in the castle. */
  place: string | null;
}

const ORDINALS = ["Ground", "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth"];

/** The tab's name: "The grounds", "Ground floor", "Third floor", "Dungeons", "Lower dungeons". */
export function layerName(layer: MapLayer): string {
  if (layer === "grounds") return "The grounds";
  if (layer === -1) return "Dungeons";
  if (layer < -1) return "Lower dungeons";
  return `${ORDINALS[layer] ?? `Level ${layer}`} floor`;
}

/** Whether surface `s` shows on `layer` (the grounds also show the ground floor's outline, for bearings). */
export function onLayer(s: Pick<MapSurface, "kind" | "level">, layer: MapLayer): boolean {
  if (layer === "grounds") return s.kind === "grounds" || s.level === 0;
  return s.kind !== "grounds" && s.level === layer;
}

/** The layer a character standing on `s` is drawn on (the grounds tiles: the grounds; anything else: its floor). */
export function layerOf(s: Pick<MapSurface, "kind" | "level">): MapLayer {
  return s.kind === "grounds" ? "grounds" : s.level;
}

/** Labels are drawn for rooms, corridors, courtyards, the bridge and the grounds; doors and stairs stay unnamed. */
const LABELLED: ReadonlySet<MapSurface["kind"]> = new Set(["room", "corridor", "courtyard", "grounds", "bridge"]);

const area = (s: { min: [number, number]; max: [number, number] }): number => (s.max[0] - s.min[0]) * (s.max[1] - s.min[1]);

/**
 * The place surface `s` belongs to (whole places are revealed). A room or corridor is often several surfaces (the Entrance Hall
 * is nine strips); standing on any of them reveals every surface of the same
 * name in the same cell on the same floor. A hidden passage's surfaces stay
 * one place each (they are all named "Hidden Passage").
 */
export function placeOf(s: Pick<MapSurface, "id" | "name" | "cell" | "level" | "secret">): string {
  return s.secret ? `secret:${s.id}` : `${s.level}|${s.cell}|${s.name}`;
}

export class MapModel {
  private readonly byId = new Map<string, MapSurface>();
  /** Secret id of each secret surface (from `SimWorld.secretOf`). */
  private readonly secretOfSurface = new Map<string, string>();
  private readonly revealedSet = new Set<string>();
  /** The places (`placeOf`) with any of their surfaces revealed. */
  private readonly placesRevealed = new Set<string>();
  private readonly foundSet = new Set<string>();
  private version = 0;

  /**
   * `surfaces`: `SimWorld.mapSurfaces()`; `secretOf`: `SimWorld.secretOf`;
   * `secretName`: a found secret's own name (`SimWorld.secrets()`).
   */
  constructor(
    readonly surfaces: readonly MapSurface[],
    secretOf: (surfaceId: string) => string | undefined,
    private readonly secretName: (secretId: string) => string | undefined,
  ) {
    for (const s of surfaces) {
      this.byId.set(s.id, s);
      if (s.secret) {
        const id = secretOf(s.id);
        if (id) this.secretOfSurface.set(s.id, id);
      }
    }
  }

  /** Bumped by every change (the map redraws when it moves). */
  get changes(): number {
    return this.version;
  }

  get revealed(): ReadonlySet<string> {
    return this.revealedSet;
  }

  get found(): ReadonlySet<string> {
    return this.foundSet;
  }

  surface(id: string): MapSurface | undefined {
    return this.byId.get(id);
  }

  /** The layers that have anything on them: the grounds first, then the floors from the lowest dungeon up. */
  layers(): MapLayer[] {
    const levels = new Set<number>();
    let grounds = false;
    for (const s of this.surfaces) {
      if (s.secret) continue; // a floor with only a passage on it is not a tab
      if (s.kind === "grounds") grounds = true;
      else levels.add(s.level);
    }
    return [...(grounds ? ["grounds" as const] : []), ...[...levels].sort((a, b) => a - b)];
  }

  /** The server's saved map (merged: anything already predicted here stays). */
  load(save: MapSave): void {
    for (const id of save.surfaces) {
      const s = this.byId.get(id);
      if (!s) continue;
      this.revealedSet.add(id);
      this.placesRevealed.add(placeOf(s));
    }
    for (const id of save.secrets) this.foundSet.add(id);
    this.version++;
  }

  /** The character stands on `surfaceId` (`SimWorld.revealAt`): true when it is newly revealed. */
  reveal(surfaceId: string | undefined): boolean {
    const s = surfaceId ? this.byId.get(surfaceId) : undefined;
    if (!s || this.revealedSet.has(s.id)) return false;
    this.revealedSet.add(s.id);
    this.placesRevealed.add(placeOf(s));
    this.version++;
    return true;
  }

  /** A secret was found (`discovered`, or the saved map). */
  discover(secretId: string): void {
    if (this.foundSet.has(secretId)) return;
    this.foundSet.add(secretId);
    this.version++;
  }

  /** Whether `s`'s place has been stood in (on any of its surfaces). */
  private placeShown(s: MapSurface): boolean {
    return this.placesRevealed.has(placeOf(s));
  }

  /** Whether `s` may be drawn at all: public ground, or a passage that was found and stood on. */
  private drawable(s: MapSurface): boolean {
    if (!s.secret) return true;
    const secret = this.secretOfSurface.get(s.id);
    return secret !== undefined && this.foundSet.has(secret) && this.revealedSet.has(s.id);
  }

  /** Whether `s` is revealed and drawn in ink (a found passage's surfaces included). */
  isShown(s: MapSurface): boolean {
    return this.drawable(s) && this.placeShown(s);
  }

  /**
   * One layer as drawn. `walk(flight)` is where a moving flight stands now
   * (`FlightView.walk`; the other position is left out); unknown: "a".
   */
  view(layer: MapLayer, walk: (flight: string) => "a" | "b" | undefined = () => undefined): MapView {
    const shapes: MapShape[] = [];
    const best = new Map<string, { s: MapSurface; area: number; secret: boolean }>();
    let minX = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxZ = -Infinity;
    for (const s of this.surfaces) {
      if (!onLayer(s, layer) || !this.drawable(s)) continue;
      if (s.moving && (walk(s.moving.flight) ?? "a") !== s.moving.position) continue;
      const shown = this.placeShown(s);
      shapes.push({ id: s.id, kind: s.kind, min: s.min, max: s.max, fog: !shown, secret: s.secret, place: placeOf(s) });
      minX = Math.min(minX, s.min[0]);
      minZ = Math.min(minZ, s.min[1]);
      maxX = Math.max(maxX, s.max[0]);
      maxZ = Math.max(maxZ, s.max[1]);
      if (!shown) continue;
      // A found passage is named by its own name ("Hidden Passage" names none), on its longest piece.
      const secret = s.secret ? this.secretOfSurface.get(s.id) : undefined;
      const text = secret ? this.secretName(secret) : LABELLED.has(s.kind) ? s.name : undefined;
      if (!text) continue;
      const a = area(s);
      const held = best.get(text);
      if (!held || a > held.area) best.set(text, { s, area: a, secret: secret !== undefined });
    }
    // Fog first, ink over it.
    shapes.sort((a, b) => Number(b.fog) - Number(a.fog));
    const labels: MapLabel[] = [...best].map(([text, b]) => ({ text, x: (b.s.min[0] + b.s.max[0]) / 2, z: (b.s.min[1] + b.s.max[1]) / 2, secret: b.secret }));
    const bounds = shapes.length ? { min: [minX, minZ] as [number, number], max: [maxX, maxZ] as [number, number] } : null;
    return { shapes, labels, bounds };
  }

  /**
   * Friends' pins on `layer`: each at the middle of the largest revealed
   * public surface on the layer named as their place. A friend whose place
   * is not revealed (or not on this layer, or not in the castle) gets none;
   * a hidden passage's surfaces never take a pin (the place name "Hidden
   * Passage" names no passage). Several friends in one place stand in a row.
   */
  /**
   * Party members' pins on `layer`: at their spot or entrance when it lies on
   * this layer (whether or not it is revealed: they are ours to find), or by
   * their place like a friend (`friendPins`).
   */
  partyPins(members: readonly PartyMapMember[], layer: MapLayer): PartyPin[] {
    const out: PartyPin[] = [];
    const byPlace: FriendPlace[] = [];
    for (const m of members) {
      if (m.at) {
        const s = m.at.surfaceId ? this.byId.get(m.at.surfaceId) : undefined;
        if (s && !s.secret && onLayer(s, layer)) out.push({ name: m.name, x: m.at.x, z: m.at.z });
      } else if (m.entrance) {
        const e = m.entrance;
        const known = e.surfaceId ? this.byId.get(e.surfaceId) : undefined;
        const near = known ? { s: known, x: e.x, z: e.z } : e.y !== undefined ? this.nearestPublic(e.x, e.y, e.z) : null;
        if (near && !near.s.secret && onLayer(near.s, layer)) out.push({ name: m.name, x: near.x, z: near.z, note: e.note });
      } else if (m.place) {
        byPlace.push({ name: m.name, place: m.place });
      }
    }
    return [...out, ...this.friendPins(byPlace, layer)];
  }

  /** The public surface nearest (x, y, z) (height counts double: the same floor wins), and the point of it nearest (x, z). */
  private nearestPublic(x: number, y: number, z: number): { s: MapSurface; x: number; z: number } | null {
    let best: { s: MapSurface; x: number; z: number; d: number } | null = null;
    for (const s of this.surfaces) {
      if (s.secret || s.kind === "grounds") continue;
      const px = Math.min(Math.max(x, s.min[0]), s.max[0]);
      const pz = Math.min(Math.max(z, s.min[1]), s.max[1]);
      const d = Math.hypot(px - x, pz - z) + Math.abs(s.y - y) * 2;
      if (!best || d < best.d) best = { s, x: px, z: pz, d };
    }
    return best;
  }

  friendPins(friends: readonly FriendPlace[], layer: MapLayer): MapPin[] {
    const out: MapPin[] = [];
    const used = new Map<string, number>();
    for (const f of friends) {
      if (!f.place) continue;
      let pick: MapSurface | null = null;
      for (const s of this.surfaces) {
        if (s.secret || s.name !== f.place || !onLayer(s, layer) || !this.placeShown(s)) continue;
        if (!pick || area(s) > area(pick)) pick = s;
      }
      if (!pick) continue;
      const n = used.get(pick.id) ?? 0;
      used.set(pick.id, n + 1);
      const x = (pick.min[0] + pick.max[0]) / 2 + (n % 4) * 1.6;
      const z = (pick.min[1] + pick.max[1]) / 2 + Math.floor(n / 4) * 1.6;
      out.push({ name: f.name, x, z });
    }
    return out;
  }
}

/** The saved map from `GET /api/map`'s body (anything malformed is left out). */
export function parseMapSave(json: unknown): MapSave {
  const o = (json ?? {}) as { surfaces?: unknown; secrets?: unknown };
  const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
  return { surfaces: strings(o.surfaces), secrets: strings(o.secrets) };
}
