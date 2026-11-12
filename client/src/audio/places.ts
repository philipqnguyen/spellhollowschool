// Where the player is, as sound sees it (docs/architecture.md "Sound and music"):
// the castle's cells and the instance layouts sorted into a few
// areas, each area's ambience bed and music by day and night, the surface
// under a character's feet, the file banks an area needs, the reverb, and the
// looping emitters placed on the layout's landmarks. Pure (no WebAudio, no
// Babylon) so it is unit tested against data/world.
//
// The layout has no floor materials, so the footstep surface comes from this
// file's tables: a surface's own id first, then its cell, then the instance
// layout, then its kind (grounds are turf, courtyards flagstones, bridges
// boards, the rest of the castle stone).

import type { Zone } from "../wasm/sim_wasm";
import type { AmbienceId, BankId, MusicId, SoundId } from "./catalogue";
import type { MusicCue } from "./types";

export const AREAS = [
  "castle",
  "great_hall",
  "library",
  "tower",
  "owl_loft",
  "dungeons",
  "kitchens",
  "common_room",
  "common_room_tower",
  "common_room_lake",
  "passage",
  "courtyard",
  "grounds",
  "lake",
  "pitch",
  "dorm",
  "class",
  "arena",
  "wishing_room",
] as const;
export type AreaKey = (typeof AREAS)[number];

/** Every castle cell (data/world/layout.json) that is not a `grounds_r<row>_c<col>` tile. */
export const CELL_AREA: Readonly<Record<string, AreaKey>> = {
  entrance_hall: "castle",
  great_hall: "great_hall",
  gf_corridor_west: "castle",
  grand_staircase: "castle",
  grand_staircase_upper: "castle",
  ff_corridor_north: "castle",
  gf_undercroft: "castle",
  ff_gallery_east: "castle",
  trophy_room: "castle",
  trophy_stair: "castle",
  sf_corridor_west: "castle",
  sf_library: "library",
  tf_corridor_west: "castle",
  tf_corridor_east: "castle",
  hospital_wing: "castle",
  l4_corridor_west: "tower",
  l5_corridor_west: "tower",
  l6_corridor_west: "tower",
  l7_corridor_north: "tower",
  l7_corridor_east: "tower",
  west_tower_stair: "tower",
  owl_loft: "owl_loft",
  common_room_house_a: "common_room",
  common_room_house_b: "common_room",
  common_room_house_c: "common_room_tower",
  // Under the lake: its own cosy bed (the fire, the water muffled through the glass, pages), not the dungeons' drips.
  common_room_house_d: "common_room_lake",
  dungeon_west: "dungeons",
  dungeon_classroom: "dungeons",
  dungeon_cellars: "dungeons",
  // The Kitchen Steps and Corridor and the Scullery Passage have no hearth: only the Kitchens sound of their fire.
  basement_east: "castle",
  kitchens: "kitchens",
  scullery_passage: "dungeons",
  courtyard: "courtyard",
  clock_courtyard: "courtyard",
  covered_bridge: "courtyard",
  pitch: "pitch",
  passage_tapestry: "passage",
  passage_panel: "passage",
  passage_painting: "passage",
  passage_statue: "passage",
  passage_dungeon: "passage",
};

/** The grounds' tiles (`grounds_r2_c0`, ...) sort by the surface's name. */
const GROUNDS_TILE = /^grounds_r\d+_c\d+$/;
const GROUNDS_BY_NAME: Readonly<Record<string, AreaKey>> = { "Lake Shore": "lake", "Flying Pitch": "pitch" };

/** The parts of a layout surface sound reads (world.ts `LayoutSurface`). */
export interface PlaceSurface {
  id: string;
  kind: string;
  name?: string;
  cell?: string;
}

/** A castle surface's area; null for a cell this table does not know (a new cell: add it). */
export function castleArea(s: PlaceSurface): AreaKey | null {
  const cell = s.cell ?? "";
  if (GROUNDS_TILE.test(cell)) return GROUNDS_BY_NAME[s.name ?? ""] ?? "grounds";
  return CELL_AREA[cell] ?? null;
}

/** An instance zone's area (not the castle). */
export function zoneArea(zone: Exclude<Zone, { kind: "castle" }>): AreaKey {
  switch (zone.kind) {
    case "dorm":
      return "dorm";
    case "class":
      return "class";
    case "arena":
      return "arena";
    case "wishing_room":
      return "wishing_room";
  }
}

// ---------------------------------------------------------------- footsteps

export const FOOT_SURFACES = ["stone", "wood", "carpet", "grass", "gravel"] as const;
export type FootSurface = (typeof FOOT_SURFACES)[number];

export const FOOT_SOUND: Readonly<Record<FootSurface, SoundId>> = {
  stone: "foot.stone",
  wood: "foot.wood",
  carpet: "foot.carpet",
  grass: "foot.grass",
  gravel: "foot.gravel",
};

/** Single surfaces that differ from their cell (castle ids). */
export const SURFACE_FOOT: Readonly<Record<string, FootSurface>> = {
  gh_duel_stage: "wood",
  gh_duel_steps_west: "wood",
  gh_duel_steps_east: "wood",
  gh_dais: "wood",
  gh_dais_steps: "wood",
  pitch_stand_n: "wood",
  pitch_stand_s: "wood",
  pitch_stand_n_steps: "wood",
  pitch_stand_n_steps_w: "wood",
  pitch_stand_s_steps: "wood",
  pitch_stand_s_steps_w: "wood",
};

/** Castle cells whose floor is not stone. */
export const CELL_FOOT: Readonly<Record<string, FootSurface>> = {
  common_room_house_a: "carpet",
  common_room_house_b: "wood",
  common_room_house_c: "carpet",
  sf_library: "wood",
  covered_bridge: "wood",
};

/** Instance layouts (data/world/instances/<id>.json) whose floor is not stone. */
export const LAYOUT_FOOT: Readonly<Record<string, FootSurface>> = {
  dorm_roarwing: "wood",
  dorm_burrowsby: "wood",
  dorm_skytalon: "wood",
  dorm_serpentyne: "stone",
  class_charms: "wood",
  class_divination: "carpet",
  class_greenhouse: "gravel",
  class_paddock: "grass",
  class_pitch: "grass",
  duel_arena: "wood",
  wishing_room_gathering: "carpet",
  lost_property_vault: "wood",
};

/**
 * The surface under someone's feet. `layout`: the instance layout id, or null
 * in the castle; `s`: the walkable surface they stand on (undefined: none, e.g.
 * between two surfaces: stone).
 */
export function footSurface(layout: string | null, s: PlaceSurface | undefined): FootSurface {
  if (layout !== null) return LAYOUT_FOOT[layout] ?? "stone";
  if (!s) return "stone";
  const own = SURFACE_FOOT[s.id];
  if (own) return own;
  const cell = s.cell ? CELL_FOOT[s.cell] : undefined;
  if (cell) return cell;
  if (s.cell && GROUNDS_TILE.test(s.cell) && s.name === "Lake Shore") return "gravel";
  switch (s.kind) {
    case "grounds":
      return "grass";
    case "bridge":
      return "wood";
    default:
      return "stone";
  }
}

// ---------------------------------------------------------------- the place

/**
 * Night, for sound, is when the open sky is night (game/clock/daynight.ts
 * `OPEN_SKY_NIGHT_BELOW`, the same 0.5: the places.test checks they agree).
 */
export const NIGHT_BELOW = 0.5;

export function isNight(daylight: number): boolean {
  return daylight < NIGHT_BELOW;
}

/** What `audio.setPlace` takes: everything ambience and music depend on. */
export interface AudioPlace {
  area: AreaKey;
  /** The instance layout (`class_greenhouse`, `dorm_skytalon`), null in the castle. */
  layout: string | null;
  /** In a class: its id (`charms`, `flying`). */
  classId: string | null;
  night: boolean;
  /** On a broom and off the ground. */
  flying: boolean;
  /** In a duel (an arena) or watching one as its duelist. */
  duel: boolean;
}

export interface PlaceInput {
  zone: Zone;
  /** The surface under the player (castle zone; also the ground under a broom). */
  surface: PlaceSurface | undefined;
  /** `SchoolHub.daylight` (0 night .. 1 day). */
  daylight: number;
  /** On a broom in the air (`p.anim` fly or hover). */
  flying: boolean;
  /** In a duel as a duelist. */
  duel: boolean;
}

/**
 * The place for `input`. A doorway (a `door` surface, a step long) or no
 * surface at all (over the lake on a broom) keeps `previous`'s area, as the
 * HUD keeps the room you came from.
 */
export function placeOf(input: PlaceInput, previous: AudioPlace | null): AudioPlace {
  const { zone, surface } = input;
  const base = { night: isNight(input.daylight), flying: input.flying, duel: input.duel };
  if (zone.kind !== "castle") {
    return { area: zoneArea(zone), layout: zone.layout, classId: zone.kind === "class" ? zone.class : null, ...base };
  }
  const keep = previous && previous.layout === null ? previous.area : null;
  let area: AreaKey | null;
  if (!surface) area = keep ?? (input.flying ? "grounds" : "castle");
  else if (surface.kind === "door" && keep) area = keep;
  else area = castleArea(surface) ?? keep ?? "castle";
  return { area, layout: null, classId: null, ...base };
}

// ---------------------------------------------------------------- ambience

/** Indoor areas: one bed day and night. Outdoor areas pick by time (OUTDOOR_AMBIENCE). */
const AREA_AMBIENCE: Readonly<Partial<Record<AreaKey, AmbienceId>>> = {
  castle: "amb.castle",
  great_hall: "amb.great_hall",
  library: "amb.library",
  // The tower corridors and stairs are indoors: the halls' tone, not amb.tower's wind (the open Owl Loft and Astronomy keep it).
  tower: "amb.castle",
  owl_loft: "amb.owl_loft",
  dungeons: "amb.dungeon",
  kitchens: "amb.kitchens",
  common_room: "amb.common_room",
  common_room_tower: "amb.common_room_tower",
  common_room_lake: "amb.common_room_lake",
  passage: "amb.passage",
  dorm: "amb.dorm",
  arena: "amb.arena",
  wishing_room: "amb.wishing_room",
};

const OUTDOOR_AMBIENCE: Readonly<Partial<Record<AreaKey, readonly [day: AmbienceId, night: AmbienceId]>>> = {
  courtyard: ["amb.courtyard_day", "amb.courtyard_night"],
  grounds: ["amb.grounds_day", "amb.grounds_night"],
  pitch: ["amb.grounds_day", "amb.grounds_night"],
  lake: ["amb.lake_day", "amb.lake_night"],
};

/** The classes' rooms (instance layouts) by their own bed; the rest are classrooms. */
const CLASS_AMBIENCE: Readonly<Record<string, AmbienceId | "outdoors">> = {
  class_greenhouse: "amb.greenhouse",
  class_paddock: "outdoors",
  class_pitch: "outdoors",
  class_astronomy: "amb.tower",
  class_potions: "amb.dungeon",
};

/** Instance rooms (not classes) whose bed is not their area's: the dorm under the lake. */
export const LAYOUT_AMBIENCE: Readonly<Record<string, AmbienceId>> = {
  dorm_serpentyne: "amb.dorm_lake",
};

export function ambienceFor(p: AudioPlace): AmbienceId {
  if (p.flying) return "amb.sky";
  const own = p.layout !== null && p.area !== "class" ? LAYOUT_AMBIENCE[p.layout] : undefined;
  if (own) return own;
  if (p.area === "class") {
    const c = (p.layout ? CLASS_AMBIENCE[p.layout] : undefined) ?? "amb.classroom";
    return c === "outdoors" ? (p.night ? "amb.grounds_night" : "amb.grounds_day") : c;
  }
  const out = OUTDOOR_AMBIENCE[p.area];
  if (out) return p.night ? out[1] : out[0];
  return AREA_AMBIENCE[p.area] ?? "amb.castle";
}

// ---------------------------------------------------------------- music

/** Each area's piece; null: no music there (silence suits it: ambience only). */
export const AREA_MUSIC: Readonly<Record<AreaKey, MusicId | null>> = {
  castle: "music.castle",
  great_hall: "music.great_hall",
  library: "music.castle",
  tower: "music.castle",
  owl_loft: "music.castle",
  dungeons: "music.dungeons",
  kitchens: "music.common_room",
  common_room: "music.common_room",
  common_room_tower: "music.common_room",
  common_room_lake: "music.common_room",
  passage: null,
  courtyard: "music.grounds",
  grounds: "music.grounds",
  lake: "music.lake",
  pitch: "music.grounds",
  dorm: "music.dorm",
  class: "music.class",
  arena: "music.duel",
  wishing_room: "music.wishing_room",
};

/** The classes whose piece is not `music.class` (data/classes/schedule.json ids). */
export const CLASS_MUSIC: Readonly<Record<string, MusicId>> = {
  flying: "music.flying",
  herbology: "music.class_grounds",
  creatures: "music.class_grounds",
  astronomy: "music.astronomy",
};

/** What the music plays: a duel, then a class, then a broom in the air, then the area. */
export function musicFor(p: AudioPlace): MusicCue | null {
  let id: MusicId | null;
  if (p.duel) id = "music.duel";
  else if (p.area === "class") id = (p.classId && CLASS_MUSIC[p.classId]) || "music.class";
  else if (p.flying) id = "music.flying";
  else id = AREA_MUSIC[p.area];
  return id ? { id, night: p.night } : null;
}

// ---------------------------------------------------------------- banks and reverb

const OUTDOOR_AREAS: ReadonlySet<AreaKey> = new Set(["courtyard", "grounds", "lake", "pitch"]);

/** The CC0 file banks a place needs (the engine fetches each once, on first need). */
export function banksFor(p: AudioPlace): BankId[] {
  const outdoors = p.flying || OUTDOOR_AREAS.has(p.area) || (p.area === "class" && p.layout !== null && ["class_greenhouse", "class_paddock", "class_pitch"].includes(p.layout));
  return outdoors ? ["core", "grounds"] : ["core", "castle"];
}

/** The effects bus's reverb: none outdoors, a small room, a stone hall, the Great Hall's big one, a close passage. */
export type Space = "open" | "room" | "hall" | "great_hall" | "close";

const AREA_SPACE: Readonly<Record<AreaKey, Space>> = {
  castle: "hall",
  great_hall: "great_hall",
  library: "room",
  tower: "hall",
  owl_loft: "room",
  dungeons: "hall",
  kitchens: "room",
  common_room: "room",
  common_room_tower: "room",
  common_room_lake: "room",
  passage: "close",
  courtyard: "open",
  grounds: "open",
  lake: "open",
  pitch: "open",
  dorm: "room",
  class: "room",
  arena: "hall",
  wishing_room: "room",
};

export function spaceFor(p: AudioPlace): Space {
  if (p.flying) return "open";
  if (p.area === "class" && p.layout && ["class_greenhouse", "class_paddock", "class_pitch", "class_astronomy"].includes(p.layout)) return "open";
  return AREA_SPACE[p.area];
}

// ---------------------------------------------------------------- emitters

/** A looping sound anchored on a castle landmark or interactable (data/world/layout.json ids). */
export interface EmitterDef {
  anchor: string;
  from: "landmark" | "interactable";
  sound: SoundId;
  /** Heard within (m). */
  radius: number;
  /** Raise the sound this far above the anchor (m): a hearth's fire, not its floor. */
  lift?: number;
  /**
   * Heard only while the listener is in one of these areas: the walls stop it
   * (the courtyard's running water is not heard from the dungeons or a common
   * room under it, a hearth not from the Great Hall beside it).
   */
  areas: readonly AreaKey[];
}

export const EMITTERS: readonly EmitterDef[] = [
  { anchor: "courtyard_fountain", from: "landmark", sound: "loop.fountain", radius: 18, areas: ["courtyard"] },
  { anchor: "common_room_1_fireplace", from: "landmark", sound: "loop.hearth", radius: 12, lift: 0.6, areas: ["common_room"] },
  { anchor: "common_room_2_hearth", from: "landmark", sound: "loop.hearth", radius: 12, lift: 0.6, areas: ["common_room"] },
  { anchor: "cauldron_classroom_1", from: "interactable", sound: "loop.cauldron", radius: 6, lift: 0.8, areas: ["dungeons"] },
  { anchor: "cauldron_classroom_2", from: "interactable", sound: "loop.cauldron", radius: 6, lift: 0.8, areas: ["dungeons"] },
  { anchor: "cauldron_classroom_3", from: "interactable", sound: "loop.cauldron", radius: 6, lift: 0.8, areas: ["dungeons"] },
  { anchor: "cauldron_classroom_4", from: "interactable", sound: "loop.cauldron", radius: 6, lift: 0.8, areas: ["dungeons"] },
  { anchor: "owl_loft", from: "landmark", sound: "loop.owls", radius: 16, lift: 2, areas: ["owl_loft"] },
];

/** Where the clock tower's bell sounds from (`world.clock_bell`, a landmark), and how far it carries (m). */
export const CLOCK_BELL = { anchor: "clock_courtyard", radius: 120 } as const;

interface Anchored {
  id: string;
  pos: readonly number[];
}

/** The emitters of a layout (the castle's): each def resolved to its anchor's position; unknown anchors are skipped. */
export function emittersIn(layout: { landmarks?: readonly Anchored[]; interactables?: readonly Anchored[] }): { key: string; sound: SoundId; at: [number, number, number]; radius: number; areas: readonly AreaKey[] }[] {
  const out: { key: string; sound: SoundId; at: [number, number, number]; radius: number; areas: readonly AreaKey[] }[] = [];
  for (const d of EMITTERS) {
    const list = d.from === "landmark" ? layout.landmarks : layout.interactables;
    const a = list?.find((x) => x.id === d.anchor);
    if (!a || a.pos.length < 3) continue;
    out.push({ key: d.anchor, sound: d.sound, at: [a.pos[0]!, a.pos[1]! + (d.lift ?? 0), a.pos[2]!], radius: d.radius, areas: d.areas });
  }
  return out;
}

// ---------------------------------------------------------------- doors and entrances

/** castle/nodes.ts ENTRANCE_STYLES, each style's sound as it opens (the house doors by their own, below). */
export const ENTRANCE_STYLE_SOUND: Readonly<Record<string, SoundId>> = {
  tapestry: "entrance.tapestry",
  panel: "entrance.panel",
  painting: "entrance.painting",
  statue: "entrance.statue",
  wall: "entrance.wall",
  double: "entrance.double",
};

/** House entrances whose look is not their style's (door surface ids). */
export const ENTRANCE_DOOR_SOUND: Readonly<Record<string, SoundId>> = {
  cr_a_portrait: "entrance.portrait",
  cr_b_barrels: "entrance.barrels",
};

/** An entrance opening (castle/entrances.ts): its door's own sound, else its style's; null for a style this table lacks. */
export function entranceSound(style: string, door: string): SoundId | null {
  return ENTRANCE_DOOR_SOUND[door] ?? ENTRANCE_STYLE_SOUND[style] ?? null;
}

/** Using an interactable that takes the player to another zone (layout `interactables[].kind`): the door heard as the fade starts. */
export const ZONE_DOOR_SOUND: Readonly<Record<string, SoundId>> = {
  class_door: "door.heavy",
  dorm_door: "door.wood",
  dorm_exit: "door.wood",
  wishing_room_door: "door.heavy",
};
