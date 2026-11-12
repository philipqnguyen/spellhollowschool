import { describe, expect, it } from "vitest";
import castleLayout from "../../../data/world/layout.json";
import castleKit from "../../../data/world/castle_kit.json";
import { BED_DEFS } from "./sfx/ambience";
import { SOUND_DEFS } from "./sfx/defs";
import { biquad } from "./sfx/dsp";
import { MockContext } from "./sfx/mock-context";
import type { SoundDef } from "./types";
import scheduleJson from "../../../data/classes/schedule.json";
import daynightSource from "../game/clock/daynight.ts?raw";
import type { Zone } from "../wasm/sim_wasm";
import { isSoundId, soundEntry } from "./catalogue";
import {
  AREAS,
  ambienceFor,
  banksFor,
  castleArea,
  CELL_AREA,
  CELL_FOOT,
  CLASS_MUSIC,
  CLOCK_BELL,
  EMITTERS,
  emittersIn,
  ENTRANCE_DOOR_SOUND,
  entranceSound,
  FOOT_SOUND,
  FOOT_SURFACES,
  footSurface,
  isNight,
  LAYOUT_FOOT,
  musicFor,
  NIGHT_BELOW,
  placeOf,
  spaceFor,
  SURFACE_FOOT,
  ZONE_DOOR_SOUND,
  type AudioPlace,
  type PlaceSurface,
} from "./places";

const instances = import.meta.glob<{ surfaces: PlaceSurface[] }>("../../../data/world/instances/*.json", { eager: true, import: "default" });
const layoutIds = Object.keys(instances).map((p) => p.replace(/^.*\/(.+)\.json$/, "$1"));
const surfaces = castleLayout.surfaces as PlaceSurface[];
const surface = (id: string): PlaceSurface => surfaces.find((s) => s.id === id)!;

const castle: Zone = { kind: "castle" };
const place = (over: Partial<AudioPlace>): AudioPlace => ({ area: "castle", layout: null, classId: null, night: false, flying: false, duel: false, ...over });
const day = { daylight: 1, flying: false, duel: false };

describe("areas", () => {
  it("knows every castle cell", () => {
    for (const s of surfaces) expect(castleArea(s), `${s.id} (cell ${s.cell})`).not.toBeNull();
    const cells = new Set(surfaces.map((s) => s.cell));
    for (const c of Object.keys(CELL_AREA)) expect(cells.has(c), `CELL_AREA names ${c}, not in the layout`).toBe(true);
  });

  it("sorts the grounds by name", () => {
    expect(castleArea({ id: "x", kind: "grounds", name: "Lake Shore", cell: "grounds_r3_c1" })).toBe("lake");
    expect(castleArea({ id: "x", kind: "grounds", name: "Flying Pitch", cell: "grounds_r2_c2" })).toBe("pitch");
    expect(castleArea({ id: "x", kind: "grounds", name: "Castle Grounds", cell: "grounds_r0_c0" })).toBe("grounds");
    expect(castleArea(surface("gh_floor"))).toBe("great_hall");
    expect(castleArea(surface("cr_b_floor"))).toBe("common_room");
  });

  it("keeps the room you came from in a doorway, and over open water", () => {
    const inHall = placeOf({ zone: castle, surface: surface("gh_floor"), ...day }, null);
    expect(inHall.area).toBe("great_hall");
    expect(placeOf({ zone: castle, surface: surface("gh_doors"), ...day }, inHall).area).toBe("great_hall");
    expect(placeOf({ zone: castle, surface: undefined, daylight: 1, flying: true, duel: false }, inHall).area).toBe("great_hall");
    expect(placeOf({ zone: castle, surface: undefined, daylight: 1, flying: true, duel: false }, null).area).toBe("grounds");
  });

  it("places instance zones", () => {
    const cls: Zone = { kind: "class", class: "herbology", section: 1, layout: "class_greenhouse" };
    const p = placeOf({ zone: cls, surface: undefined, daylight: 0.2, flying: false, duel: false }, null);
    expect(p).toEqual({ area: "class", layout: "class_greenhouse", classId: "herbology", night: true, flying: false, duel: false });
    expect(placeOf({ zone: { kind: "dorm", ownerName: "A", house: "skytalon", layout: "dorm_skytalon" }, surface: undefined, ...day }, null).area).toBe("dorm");
    expect(placeOf({ zone: { kind: "arena", duelId: 1, venue: "club", layout: "duel_arena" }, surface: undefined, ...day }, null).area).toBe("arena");
  });

  it("calls night where the sky does (daynight.ts OPEN_SKY_NIGHT_BELOW)", () => {
    const m = /export const OPEN_SKY_NIGHT_BELOW = ([0-9.]+);/.exec(daynightSource);
    expect(Number(m?.[1])).toBe(NIGHT_BELOW);
    expect(isNight(0.2)).toBe(true);
    expect(isNight(0.8)).toBe(false);
  });
});

describe("footsteps", () => {
  it("maps tables to known surfaces, layouts and cells", () => {
    for (const f of Object.values({ ...SURFACE_FOOT, ...CELL_FOOT, ...LAYOUT_FOOT })) expect(FOOT_SURFACES).toContain(f);
    for (const id of Object.keys(SURFACE_FOOT)) expect(surface(id), id).toBeDefined();
    for (const l of Object.keys(LAYOUT_FOOT)) expect(layoutIds).toContain(l);
    for (const f of FOOT_SURFACES) expect(soundEntry(FOOT_SOUND[f]).group).toBe("footsteps");
  });

  it("reads the floor", () => {
    expect(footSurface(null, surface("eh_floor_south"))).toBe("stone");
    expect(footSurface(null, surface("gh_duel_stage"))).toBe("wood");
    expect(footSurface(null, surface("cr_a_floor"))).toBe("carpet");
    expect(footSurface(null, surface("covered_bridge"))).toBe("wood");
    expect(footSurface(null, { id: "g", kind: "grounds", name: "Castle Grounds", cell: "grounds_r0_c0" })).toBe("grass");
    expect(footSurface(null, { id: "g", kind: "grounds", name: "Lake Shore", cell: "grounds_r3_c1" })).toBe("gravel");
    expect(footSurface(null, surface("courtyard"))).toBe("stone");
    expect(footSurface(null, undefined)).toBe("stone");
    expect(footSurface("dorm_roarwing", undefined)).toBe("wood");
    expect(footSurface("class_paddock", undefined)).toBe("grass");
    expect(footSurface("class_potions", undefined)).toBe("stone");
  });

  it("gives every castle surface a footstep", () => {
    for (const s of surfaces) expect(FOOT_SURFACES).toContain(footSurface(null, s));
  });
});

describe("ambience and music", () => {
  const allPlaces: AudioPlace[] = [];
  for (const area of AREAS) for (const night of [false, true]) allPlaces.push(place({ area, night }));
  for (const layout of layoutIds) for (const night of [false, true]) allPlaces.push(place({ area: layout.startsWith("class_") ? "class" : layout.startsWith("dorm_") ? "dorm" : layout.startsWith("wishing_room_") || layout === "lost_property_vault" ? "wishing_room" : "arena", layout, night }));
  for (const c of scheduleJson.classes) allPlaces.push(place({ area: "class", layout: c.layout, classId: c.id }));

  it("gives every place a bed, music or deliberate silence, banks and a space", () => {
    for (const p of allPlaces) {
      const amb = ambienceFor(p);
      expect(isSoundId(amb) && soundEntry(amb).group, JSON.stringify(p)).toBe("ambience");
      const m = musicFor(p);
      if (m) expect(soundEntry(m.id).group).toBe("music");
      expect(banksFor(p)).toContain("core");
      expect(["open", "room", "hall", "great_hall", "close"]).toContain(spaceFor(p));
    }
  });

  it("plays a fire only in castle rooms with a fireplace or hearth", () => {
    const FIRE = new Set(["loop.hearth", "sprinkle.fire_pop", "amb.dorm"]);
    const hasFire = (id: string, seen = new Set<string>()): boolean => {
      if (FIRE.has(id)) return true;
      if (seen.has(id)) return false;
      seen.add(id);
      const def = BED_DEFS[id as keyof typeof BED_DEFS] as { layers?: { id: string }[]; sprinkles?: { id: string }[] } | undefined;
      return [...(def?.layers ?? []), ...(def?.sprinkles ?? [])].some((l) => hasFire(l.id, seen));
    };
    const cells = (castleKit as { cells: Record<string, unknown> }).cells;
    const fireCells = Object.keys(CELL_AREA).filter((c) => hasFire(ambienceFor(place({ area: CELL_AREA[c]! }))));
    expect(fireCells.length).toBeGreaterThan(0);
    for (const c of fireCells) expect(JSON.stringify(cells[c] ?? {}), c).toMatch(/fire|hearth/);
    expect(hasFire(ambienceFor(place({ area: castleArea(surface("cr_c_floor"))! })))).toBe(false);
  });

  describe("indoors: quiet, with no noise bed and nothing from outside", () => {
    // Open to the air by design: the Owl Loft and the Astronomy platform (wind), the outdoor class layouts.
    const OPEN_AIR_LAYOUTS = new Set(["class_greenhouse", "class_paddock", "class_pitch", "class_astronomy"]);
    const OUTDOOR = new Set(["courtyard", "grounds", "lake", "pitch", "owl_loft"]);
    const indoor = allPlaces.filter((p) => !OUTDOOR.has(p.area) && !(p.layout && OPEN_AIR_LAYOUTS.has(p.layout)));
    // The only recordings that loop indoors: a fire in the room (the kitchens', a common room's hearth).
    const INDOOR_FILES = new Set(["loop.hearth"]);
    const SR = 22050;

    /** A synth loop rendered as the engine plays it (its level from levels.ts applied). */
    const renderLoop = (id: string): Float32Array => {
      const d = SOUND_DEFS[id as keyof typeof SOUND_DEFS] as Extract<SoundDef, { kind: "synth" }>;
      const ctx = new MockContext(d.channels ?? 1, Math.ceil(d.seconds * SR), SR);
      d.draw(ctx as unknown as OfflineAudioContext, ctx.destination as unknown as AudioNode, 0);
      const y = ctx.buffers.at(-1)!.getChannelData(0).slice();
      for (let i = 0; i < y.length; i++) y[i]! *= d.gain ?? 1;
      return y;
    };
    const db = (ms: number) => 10 * Math.log10(ms + 1e-20);
    const bedsIndoors = () => new Set(indoor.map((p) => ambienceFor(p)));
    /** A bed's layers and sprinkles; a plain loop played as the bed is its own one layer. */
    const partsOf = (amb: string): { layers: readonly { id: string; gain: number }[]; sprinkles: readonly { id: string }[] } => {
      const def = SOUND_DEFS[amb as keyof typeof SOUND_DEFS];
      expect(def, amb).toBeDefined();
      return def!.kind === "bed" ? def! : { layers: [{ id: amb, gain: 1 }], sprinkles: [] };
    };

    it("covers every indoor cell and instance", () => {
      for (const c of Object.keys(CELL_AREA)) if (!OUTDOOR.has(CELL_AREA[c]!)) expect(indoor.some((p) => p.area === CELL_AREA[c]), c).toBe(true);
      expect(indoor.filter((p) => p.layout).length).toBeGreaterThan(10);
    });

    it("plays an indoor bed in every indoor place, day and night: no wind, sky, lake, grounds or courtyard", () => {
      for (const p of indoor) {
        const amb = ambienceFor(p);
        expect(amb, JSON.stringify(p)).not.toMatch(/^amb\.(sky|tower|owl_loft|courtyard|grounds|lake|greenhouse)/);
        const { layers, sprinkles } = partsOf(amb);
        for (const l of layers) expect(l.id, `${amb} layer`).not.toMatch(/^(amb\.(sky|tower|courtyard|grounds|lake)|loop\.(lake|fountain))/);
        for (const s of sprinkles) expect(s.id, `${amb} sprinkle`).not.toMatch(/gust|bird|crow|frog|owl_far/);
      }
    });

    it("loops no noise indoors: a bed's synth layers are tones (no energy above 6 kHz), quiet and steady; its recordings a fire", () => {
      for (const amb of bedsIndoors()) {
        for (const l of partsOf(amb).layers) {
          const ld = SOUND_DEFS[l.id as keyof typeof SOUND_DEFS]!;
          if (ld.kind === "file") {
            expect(INDOOR_FILES.has(l.id), `${amb}: recording ${l.id} indoors`).toBe(true);
            continue;
          }
          expect(ld.kind, `${amb} layer ${l.id}`).toBe("synth");
          const y = renderLoop(l.id);
          // Noise (white under any filter but a steep low-pass: even the old brown room tone kept ~1%) leaves
          // energy up high; a tone does not.
          const up = biquad(biquad(y, "highpass", 6000, 0.7, SR), "highpass", 6000, 0.7, SR);
          let all = 0, hi = 0;
          for (let i = 0; i < y.length; i++) {
            all += y[i]! ** 2;
            hi += up[i]! ** 2;
          }
          expect(hi / all, `${amb} layer ${l.id}: ${((hi / all) * 100).toFixed(3)}% above 6 kHz (noise)`).toBeLessThan(0.001);
          expect(db(all / y.length) + 20 * Math.log10(l.gain), `${amb} layer ${l.id}: loudness`).toBeLessThan(-38);
        }
      }
    });
  });

  it("gives the rooms under the lake a cosy bed of their own: the fire, muffled water, pages, no drips", () => {
    expect(castleArea(surface("cr_d_floor"))).toBe("common_room_lake");
    expect(castleArea(surface("cr_d_steps"))).toBe("common_room_lake");
    const room = ambienceFor(place({ area: castleArea(surface("cr_d_floor"))! }));
    const dorm = ambienceFor(place({ area: "dorm", layout: "dorm_serpentyne" }));
    expect(room).toBe("amb.common_room_lake");
    expect(dorm).toBe("amb.dorm_lake");
    for (const amb of [room, dorm]) {
      const def = BED_DEFS[amb as keyof typeof BED_DEFS] as Extract<SoundDef, { kind: "bed" }>;
      const ids = [...def.layers, ...def.sprinkles].map((l) => l.id);
      expect(ids, amb).toEqual(expect.arrayContaining(["loop.hearth", "amb.under_lake", "sprinkle.fire_pop", "sprinkle.page"]));
      expect(ids.some((id) => /drip/.test(id)), amb).toBe(false);
    }
    expect(musicFor(place({ area: "common_room_lake" }))?.id).toBe("music.common_room");
    expect(spaceFor(place({ area: "common_room_lake" }))).toBe("room");
    // The other dorms and common rooms, and the real dungeons, keep theirs.
    for (const d of ["dorm_roarwing", "dorm_burrowsby", "dorm_skytalon"]) expect(ambienceFor(place({ area: "dorm", layout: d }))).toBe("amb.dorm");
    expect(ambienceFor(place({ area: castleArea(surface("cr_b_floor"))! }))).toBe("amb.common_room");
    expect(ambienceFor(place({ area: CELL_AREA.dungeon_west! }))).toBe("amb.dungeon");
  });

  it("follows the time of day outdoors", () => {
    expect(ambienceFor(place({ area: "grounds" }))).toBe("amb.grounds_day");
    expect(ambienceFor(place({ area: "grounds", night: true }))).toBe("amb.grounds_night");
    expect(ambienceFor(place({ area: "lake", night: true }))).toBe("amb.lake_night");
    expect(ambienceFor(place({ area: "class", layout: "class_paddock", night: true }))).toBe("amb.grounds_night");
    expect(ambienceFor(place({ area: "great_hall", night: true }))).toBe("amb.great_hall");
    expect(ambienceFor(place({ area: "grounds", flying: true }))).toBe("amb.sky");
  });

  it("mixes the music by place: orchestral castle, folk grounds and common rooms, celesta classes", () => {
    expect(musicFor(place({ area: "castle" }))).toEqual({ id: "music.castle", night: false });
    expect(musicFor(place({ area: "great_hall", night: true }))).toEqual({ id: "music.great_hall", night: true });
    expect(musicFor(place({ area: "grounds" }))?.id).toBe("music.grounds");
    expect(musicFor(place({ area: "lake" }))?.id).toBe("music.lake");
    expect(musicFor(place({ area: "common_room" }))?.id).toBe("music.common_room");
    expect(musicFor(place({ area: "class", classId: "charms" }))?.id).toBe("music.class");
    expect(musicFor(place({ area: "passage" }))).toBeNull();
  });

  it("puts a duel over a class over a broom over the area", () => {
    expect(musicFor(place({ area: "arena", duel: true }))?.id).toBe("music.duel");
    expect(musicFor(place({ area: "great_hall", duel: true }))?.id).toBe("music.duel");
    expect(musicFor(place({ area: "class", classId: "flying", flying: true }))?.id).toBe("music.flying");
    expect(musicFor(place({ area: "class", classId: "astronomy" }))?.id).toBe("music.astronomy");
    expect(musicFor(place({ area: "lake", flying: true }))?.id).toBe("music.flying");
  });

  it("names real classes and outdoor banks", () => {
    for (const c of Object.keys(CLASS_MUSIC)) expect(scheduleJson.classes.map((x) => x.id)).toContain(c);
    expect(banksFor(place({ area: "lake" }))).toEqual(["core", "grounds"]);
    expect(banksFor(place({ area: "dungeons" }))).toEqual(["core", "castle"]);
    expect(spaceFor(place({ area: "great_hall" }))).toBe("great_hall");
    expect(spaceFor(place({ area: "grounds" }))).toBe("open");
  });
});

describe("emitters", () => {
  it("anchors every emitter on the castle layout, with a looping emitter sound", () => {
    const placed = emittersIn(castleLayout);
    expect(placed.length).toBe(EMITTERS.length);
    for (const e of EMITTERS) {
      const s = soundEntry(e.sound);
      expect(s.group === "emitters" && s.loop && s.spatial, e.anchor).toBe(true);
    }
    const fountain = placed.find((p) => p.key === "courtyard_fountain")!;
    expect(fountain.at).toEqual([16, 0, 61.2]);
    expect(castleLayout.landmarks.some((l) => l.id === CLOCK_BELL.anchor)).toBe(true);
  });

  it("hears each emitter only in the areas it sounds in: no running water indoors, no fire through a wall", () => {
    type Rect = PlaceSurface & { min?: number[]; max?: number[]; y?: number };
    /** The cell of the floor under an anchor (the nearest floor at its height). */
    const cellOf = (anchor: string) => {
      const a = [...castleLayout.landmarks, ...castleLayout.interactables].find((x) => x.id === anchor)!;
      const [x, y, z] = a.pos as [number, number, number];
      const under = (surfaces as Rect[]).filter((s) => s.cell && s.min && s.max && x >= s.min[0]! && x <= s.max[0]! && z >= s.min[1]! && z <= s.max[1]! && Math.abs((s.y ?? 0) - y) < 3);
      under.sort((p, q) => Math.abs((p.y ?? 0) - y) - Math.abs((q.y ?? 0) - y));
      return under[0]?.cell;
    };
    for (const e of EMITTERS) {
      expect(e.areas.length, e.anchor).toBeGreaterThan(0);
      const cell = cellOf(e.anchor);
      if (cell) expect(e.areas, `${e.anchor} sounds in its own room (${cell})`).toContain(castleArea({ id: "", kind: "", cell })!);
    }
    const water = EMITTERS.filter((e) => /fountain|lake|water/.test(e.sound));
    for (const e of water) for (const a of e.areas) expect(["courtyard", "grounds", "lake", "pitch"], e.anchor).toContain(a);
    for (const p of emittersIn(castleLayout)) expect(p.areas, p.key).toEqual(EMITTERS.find((e) => e.anchor === p.key)!.areas);
  });

  it("skips anchors a layout does not have", () => {
    expect(emittersIn({ landmarks: [], interactables: [] })).toEqual([]);
  });
});

describe("doors and entrances", () => {
  it("has a sound for every entrance style and zone door", async () => {
    const { ENTRANCE_STYLES } = await import("../game/castle/nodes");
    for (const s of ENTRANCE_STYLES) expect(entranceSound(s, "sp_x"), s).not.toBeNull();
    expect(entranceSound("painting", "cr_a_portrait")).toBe("entrance.portrait");
    expect(entranceSound("panel", "cr_b_barrels")).toBe("entrance.barrels");
    for (const d of Object.keys(ENTRANCE_DOOR_SOUND)) expect(surface(d), d).toBeDefined();
    const kinds = new Set(castleLayout.interactables.map((i) => i.kind));
    for (const k of Object.keys(ZONE_DOOR_SOUND)) if (k !== "dorm_exit") expect(kinds.has(k), k).toBe(true);
    for (const s of Object.values(ZONE_DOOR_SOUND)) expect(soundEntry(s).group).toBe("doors");
  });
});
