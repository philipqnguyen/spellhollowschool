// The map's state (map-state.ts): fog over places not yet
// revealed, whole surfaces revealed by standing on them, secrets never drawn
// (not even as fog) or named until found, friends pinned only on revealed
// places, the moving flights where they stand now. Against hand-made
// surfaces and against the shipped castle (sim-wasm's `mapSurfaces`).

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import layoutText from "../../../data/world/layout.json?raw";
import secretsText from "../../../data/world/secrets.json?raw";
import paletteJson from "../../../data/items/palette.json";
import { initSync, SimWorld, type MapSurface } from "../wasm/sim_wasm";
import { layerName, layerOf, MapModel, onLayer, parseMapSave, type MapLayer } from "./map-state";
import { MAP_INKS } from "./map";

const surf = (id: string, name: string, kind: MapSurface["kind"], level: number, min: [number, number], max: [number, number], extra: Partial<MapSurface> = {}): MapSurface => ({
  id,
  name,
  kind,
  level,
  cell: "c",
  min,
  max,
  y: level * 4.5,
  secret: false,
  ...extra,
});

/** A hall, a corridor, a passage (two surfaces), a moving flight's two positions and a grounds tile. */
const SURFACES: MapSurface[] = [
  surf("hall", "Great Hall", "room", 0, [0, 0], [20, 10]),
  surf("hall_door", "Hall Door", "door", 0, [20, 4], [22, 6]),
  surf("corr", "East Corridor", "corridor", 0, [22, 0], [40, 4]),
  surf("p_door", "Hidden Passage", "door", 0, [40, 0], [42, 2], { secret: true }),
  surf("p_walk", "Hidden Passage", "corridor", 0, [42, 0], [60, 2], { secret: true }),
  surf("fl_a", "Moving Flight", "stair", 0, [0, 12], [2, 20], { moving: { flight: "flight_x", position: "a" } }),
  surf("fl_b", "Moving Flight", "stair", 0, [0, 12], [8, 14], { moving: { flight: "flight_x", position: "b" } }),
  surf("up", "Upper Corridor", "corridor", 1, [0, 0], [30, 4]),
  surf("g1", "Castle Grounds", "grounds", 0, [-50, -50], [0, 0]),
];
const SECRET_OF: Record<string, string> = { p_door: "weavers_stair", p_walk: "weavers_stair" };

function model(): MapModel {
  return new MapModel(SURFACES, (id) => SECRET_OF[id], (id) => (id === "weavers_stair" ? "The Weaver's Back Stair" : undefined));
}

const ids = (m: MapModel, layer: MapLayer, fog: boolean) =>
  m
    .view(layer)
    .shapes.filter((s) => s.fog === fog)
    .map((s) => s.id)
    .sort();

describe("the map's fog", () => {
  it("fogs every public place until it is stood on, then shows it whole", () => {
    const m = model();
    expect(ids(m, 0, false)).toEqual([]);
    expect(ids(m, 0, true)).toEqual(["corr", "fl_a", "hall", "hall_door"]);
    expect(m.view(0).labels).toEqual([]);
    expect(m.reveal("hall")).toBe(true);
    expect(m.reveal("hall")).toBe(false);
    expect(m.reveal(undefined)).toBe(false);
    expect(m.reveal("nowhere")).toBe(false);
    expect(ids(m, 0, false)).toEqual(["hall"]);
    // The whole hall is drawn and named at its middle; fog is drawn before ink.
    const v = m.view(0);
    expect(v.labels).toEqual([{ text: "Great Hall", x: 10, z: 5, secret: false }]);
    expect(v.shapes.at(-1)!.id).toBe("hall");
    expect(v.shapes.find((s) => s.id === "hall")).toMatchObject({ min: [0, 0], max: [20, 10], fog: false });
  });

  it("merges the server's saved map with the reveals predicted here", () => {
    const m = model();
    m.reveal("corr");
    const before = m.changes;
    m.load(parseMapSave({ surfaces: ["hall", "gone_surface", 7], secrets: [] }));
    expect(m.changes).toBeGreaterThan(before);
    expect([...m.revealed].sort()).toEqual(["corr", "hall"]);
    expect(parseMapSave(null)).toEqual({ surfaces: [], secrets: [] });
    expect(parseMapSave({ surfaces: "x" })).toEqual({ surfaces: [], secrets: [] });
  });

  it("names doors and stairs nowhere, rooms and corridors once", () => {
    const m = model();
    for (const id of ["hall", "hall_door", "corr", "fl_a"]) m.reveal(id);
    expect(m.view(0).labels.map((l) => l.text).sort()).toEqual(["East Corridor", "Great Hall"]);
  });
});

describe("secrets on the map", () => {
  it("never draws an unfound passage, not even as fog, and never names it", () => {
    const m = model();
    // Standing in it before the server's word: revealed, but still not drawn.
    m.reveal("p_door");
    m.reveal("p_walk");
    const v = m.view(0);
    expect(v.shapes.map((s) => s.id)).not.toContain("p_door");
    expect(v.shapes.map((s) => s.id)).not.toContain("p_walk");
    expect(v.labels.map((l) => l.text)).not.toContain("The Weaver's Back Stair");
    expect(v.labels.map((l) => l.text)).not.toContain("Hidden Passage");
    // The bounds give nothing away either.
    expect(v.bounds!.max[0]).toBe(40);
  });

  it("draws a found passage in ink, named by its own name, only where stood on", () => {
    const m = model();
    m.discover("weavers_stair");
    // Found (by the saved map) but the walk not stood on yet: neither drawn nor fogged.
    expect(m.view(0).shapes.map((s) => s.id)).not.toContain("p_walk");
    m.reveal("p_walk");
    const v = m.view(0);
    expect(v.shapes.find((s) => s.id === "p_walk")).toMatchObject({ fog: false, secret: true });
    expect(v.labels).toContainEqual({ text: "The Weaver's Back Stair", x: 51, z: 1, secret: true });
    expect(v.labels.map((l) => l.text)).not.toContain("Hidden Passage");
  });

  it("finds a secret from the saved map", () => {
    const m = model();
    m.load({ surfaces: ["p_walk"], secrets: ["weavers_stair"] });
    expect(m.found.has("weavers_stair")).toBe(true);
    expect(m.view(0).shapes.map((s) => s.id)).toContain("p_walk");
  });

  it("leaves a floor with only a passage on it off the tabs", () => {
    const m = new MapModel([...SURFACES, surf("deep", "Hidden Passage", "corridor", -3, [0, 0], [2, 9], { secret: true })], (id) => SECRET_OF[id] ?? (id === "deep" ? "weavers_stair" : undefined), () => "x");
    expect(m.layers()).toEqual(["grounds", 0, 1]);
  });
});

describe("friends and the flights on the map", () => {
  it("pins a friend at their place only once it is revealed, and never in a passage", () => {
    const m = model();
    const friends = [
      { name: "Alina", place: "Great Hall" },
      { name: "Basil", place: "Hidden Passage" },
      { name: "Cora", place: null },
      { name: "Dev", place: "Upper Corridor" },
    ];
    expect(m.friendPins(friends, 0)).toEqual([]);
    m.reveal("hall");
    m.discover("weavers_stair");
    m.reveal("p_walk");
    const pins = m.friendPins(friends, 0);
    expect(pins.map((p) => p.name)).toEqual(["Alina"]);
    expect(pins[0]).toMatchObject({ x: 10, z: 5 });
    // Dev's place is on the first floor, and not revealed.
    expect(m.friendPins(friends, 1)).toEqual([]);
    m.reveal("up");
    expect(m.friendPins(friends, 1).map((p) => p.name)).toEqual(["Dev"]);
    // Two friends in one place stand side by side.
    const two = m.friendPins([...friends, { name: "Eve", place: "Great Hall" }], 0);
    expect(two).toHaveLength(2);
    expect(two[0]!.x).not.toBe(two[1]!.x);
  });

  it("pins party members where they stand, at the door they went through, or by their place", () => {
    const m = model();
    const party = [
      // Drawn with us: exactly where they stand, revealed or not.
      { name: "Ada", at: { x: 30, z: 2, surfaceId: "corr" } },
      // In a class: at its door, saying where.
      { name: "Bea", entrance: { x: 1, z: 1, surfaceId: "up", note: "in Charms" } },
      // In the castle but not drawn with us: by their place, once it is revealed (as friends).
      { name: "Cy", place: "Great Hall" },
      // Never in a passage.
      { name: "Di", at: { x: 50, z: 1, surfaceId: "p_walk" } },
    ];
    expect(m.partyPins(party, 0)).toEqual([{ name: "Ada", x: 30, z: 2, note: undefined }]);
    m.reveal("hall");
    expect(m.partyPins(party, 0).map((p) => p.name)).toEqual(["Ada", "Cy"]);
    expect(m.partyPins(party, 1)).toEqual([{ name: "Bea", x: 1, z: 1, note: "in Charms" }]);
    expect(m.partyPins([], 0)).toEqual([]);
    // A door on ground we do not walk (another house's common room): at the nearest public ground on its floor.
    const door = [{ name: "Eve", entrance: { x: 45, y: 0, z: 2, surfaceId: undefined, note: "in a dorm room" } }];
    expect(m.partyPins(door, 0)).toEqual([{ name: "Eve", x: 40, z: 2, note: "in a dorm room" }]);
    expect(m.partyPins(door, 1)).toEqual([]);
    expect(MAP_INKS.party).toBe("sky");
  });

  it("draws a moving flight where it stands now", () => {
    const m = model();
    m.reveal("fl_a");
    m.reveal("fl_b");
    expect(m.view(0, () => "a").shapes.map((s) => s.id)).toContain("fl_a");
    expect(m.view(0, () => "a").shapes.map((s) => s.id)).not.toContain("fl_b");
    expect(m.view(0, () => "b").shapes.map((s) => s.id)).toContain("fl_b");
    expect(m.view(0, () => "b").shapes.map((s) => s.id)).not.toContain("fl_a");
  });

  it("puts the grounds on their own layer with the ground floor for bearings", () => {
    const m = model();
    expect(m.layers()).toEqual(["grounds", 0, 1]);
    expect(m.view("grounds").shapes.map((s) => s.id)).toEqual(expect.arrayContaining(["g1", "hall"]));
    expect(m.view(0).shapes.map((s) => s.id)).not.toContain("g1");
    expect(layerOf(SURFACES.at(-1)!)).toBe("grounds");
    expect(layerOf(SURFACES[0]!)).toBe(0);
    expect(onLayer(SURFACES[0]!, "grounds")).toBe(true);
    expect(onLayer(SURFACES[0]!, 1)).toBe(false);
    expect(layerName("grounds")).toBe("The grounds");
    expect(layerName(0)).toBe("Ground floor");
    expect(layerName(3)).toBe("Third floor");
    expect(layerName(-1)).toBe("Dungeons");
    expect(layerName(-2)).toBe("Lower dungeons");
  });
});

describe("the map of the shipped castle", () => {
  let sim: SimWorld;
  beforeAll(() => {
    initSync({ module: readFileSync(new URL("../wasm/sim_wasm_bg.wasm", import.meta.url)) });
    sim = new SimWorld(layoutText);
    sim.setSecrets(secretsText);
  });

  it("starts all fog, with no passage anywhere on any floor", () => {
    const names = new Map(sim.secrets().map((s) => [s.id, s.name]));
    const m = new MapModel(sim.mapSurfaces(), (id) => sim.secretOf(id), (id) => names.get(id));
    const layers = m.layers();
    expect(layers[0]).toBe("grounds");
    expect(layers).toEqual(expect.arrayContaining([-2, -1, 0, 1, 2, 3, 4, 5, 6, 7]));
    const secretIds = new Set(sim.mapSurfaces().filter((s) => s.secret).map((s) => s.id));
    expect(secretIds.size).toBeGreaterThan(20);
    for (const l of layers) {
      const v = m.view(l);
      expect(v.shapes.every((s) => s.fog)).toBe(true);
      expect(v.shapes.some((s) => secretIds.has(s.id))).toBe(false);
      expect(v.labels).toEqual([]);
    }
  });

  it("reveals the surface stood on (sim-wasm's rule) and a passage only once found", () => {
    const names = new Map(sim.secrets().map((s) => [s.id, s.name]));
    const m = new MapModel(sim.mapSurfaces(), (id) => sim.secretOf(id), (id) => names.get(id));
    // In the Entrance Hall (the spawns are outside, on the front steps).
    const [x, y, z] = [16, 0, 30];
    const here = sim.revealAt(x, y, z);
    expect(here).toBeDefined();
    expect(m.reveal(here)).toBe(true);
    expect(m.view(0).shapes.find((s) => s.id === here)?.fog).toBe(false);
    // Review fix (whole places): the Entrance Hall is nine floor strips; standing on
    // one shows all nine, and nothing else on the floor.
    const hall = m.view(0).shapes.filter((s) => s.place === "0|entrance_hall|Entrance Hall");
    expect(hall).toHaveLength(9);
    expect(hall.every((s) => !s.fog)).toBe(true);
    expect(new Set(m.view(0).shapes.filter((s) => !s.fog).map((s) => s.place))).toEqual(new Set(["0|entrance_hall|Entrance Hall"]));
    // In the dungeon passage before the server's word: not drawn; after: drawn and named.
    const walk = sim.mapSurfaces().find((s) => s.id === "sp_dungeon_walk")!;
    const id = sim.revealAt((walk.min[0] + walk.max[0]) / 2, walk.y, (walk.min[1] + walk.max[1]) / 2);
    expect(id).toBe("sp_dungeon_walk");
    m.reveal(id);
    expect(m.view(-2).shapes.some((s) => s.id === "sp_dungeon_walk")).toBe(false);
    m.discover(sim.secretOf(id!)!);
    expect(m.view(-2).labels.map((l) => l.text)).toContain("The Cellarer's Way");
    // A found passage is still drawn only where stood on (its surfaces are a place each).
    const drawn = m.view(-2).shapes.filter((s) => s.secret).map((s) => s.id);
    expect(drawn).toEqual(["sp_dungeon_walk"]);
  });
});

describe("the map's inks", () => {
  it("are all palette colours", () => {
    const ids = new Set((paletteJson as { colours: { id: string }[] }).colours.map((c) => c.id));
    for (const id of Object.values(MAP_INKS)) expect(ids.has(id), id).toBe(true);
  });
});
