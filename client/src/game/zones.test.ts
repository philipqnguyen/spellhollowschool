import { describe, expect, it } from "vitest";
import type { Interactable, Zone } from "../wasm/sim_wasm";
import castleLayout from "../../../data/world/layout.json";
import { ZoneMachine, dormRole, interactPrompt, isOwlMail, parseInstanceManifest, sameZone, zoneKey, type ZoneArrival } from "./zones";

const castle: Zone = { kind: "castle" };
const dorm = (ownerName: string): Zone => ({ kind: "dorm", ownerName, house: "skytalon", layout: "dorm_skytalon" });
const at = (zone: Zone, x = 0): ZoneArrival => ({ zone, spawn: [x, 0, 0], yaw: 0 });

describe("zone keys and roles", () => {
  it("tells zones apart by owner", () => {
    expect(zoneKey(castle)).toBe("castle");
    expect(zoneKey(dorm("Basil"))).toBe("dorm:Basil");
    expect(sameZone(dorm("Basil"), dorm("Basil"))).toBe(true);
    expect(sameZone(dorm("Basil"), dorm("Alina"))).toBe(false);
    expect(sameZone(castle, null)).toBe(false);
  });

  it("knows owners from guests", () => {
    expect(dormRole(dorm("Basil"), "Basil")).toBe("owner");
    expect(dormRole(dorm("Basil"), "Alina")).toBe("guest");
    expect(dormRole(castle, "Basil")).toBeNull();
    expect(dormRole(null, "Basil")).toBeNull();
  });
});

describe("ZoneMachine", () => {
  it("loads the first zone, then is ready there", () => {
    const m = new ZoneMachine();
    expect(m.ready).toBe(false);
    const step = m.arrive(at(dorm("Basil")));
    expect(step.kind).toBe("load");
    expect(m.phase).toBe("loading");
    expect(m.loadingZone).toEqual(dorm("Basil"));
    if (step.kind !== "load") throw new Error("unreachable");
    expect(m.loaded(step.ticket)?.zone).toEqual(dorm("Basil"));
    expect(m.ready).toBe(true);
    expect(m.zone).toEqual(dorm("Basil"));
  });

  it("only places the player when arriving in the zone already shown", () => {
    const m = new ZoneMachine();
    const first = m.arrive(at(castle));
    if (first.kind !== "load") throw new Error("expected a load");
    m.loaded(first.ticket);
    const again = m.arrive(at(castle, 5)); // e.g. the Welcome of a reconnect
    expect(again).toEqual({ kind: "place", arrival: at(castle, 5) });
    expect(m.ready).toBe(true);
  });

  it("drops a load overtaken by a newer arrival", () => {
    const m = new ZoneMachine();
    const a = m.arrive(at(dorm("Basil")));
    const b = m.arrive(at(castle));
    if (a.kind !== "load" || b.kind !== "load") throw new Error("expected loads");
    expect(m.isCurrent(a.ticket)).toBe(false);
    expect(m.loaded(a.ticket)).toBeNull();
    expect(m.ready).toBe(false); // still waiting for the castle
    expect(m.loaded(b.ticket)?.zone).toEqual(castle);
    expect(m.zone).toEqual(castle);
  });

  it("settles a running load on the newest arrival for the same zone", () => {
    const m = new ZoneMachine();
    const a = m.arrive(at(castle, 1));
    expect(m.arrive(at(castle, 2))).toEqual({ kind: "wait" });
    if (a.kind !== "load") throw new Error("expected a load");
    expect(m.loaded(a.ticket)?.spawn).toEqual([2, 0, 0]);
  });

  it("goes back to a zone it left", () => {
    const m = new ZoneMachine();
    const steps = [at(castle), at(dorm("Basil")), at(castle)].map((arrival) => {
      const s = m.arrive(arrival);
      if (s.kind === "load") m.loaded(s.ticket);
      return s.kind;
    });
    expect(steps).toEqual(["load", "load", "load"]);
    expect(m.zone).toEqual(castle);
    expect(m.loaded(1)).toBeNull(); // nothing is loading
  });
});

describe("interactPrompt", () => {
  const door: Interactable = { id: "door_c", kind: "dorm_door", house: "skytalon", pos: [10, 0, 0], yaw: 0, radius: 1.5 };
  const exit: Interactable = { id: "dorm_exit", kind: "dorm_exit", pos: [0, 0, 3.9], yaw: Math.PI, radius: 1.5 };
  const reach = (pos: [number, number, number]) => (id: string) => {
    const it = [door, exit].find((i) => i.id === id)!;
    return Math.hypot(it.pos[0] - pos[0], it.pos[2] - pos[2]) <= it.radius;
  };

  it("offers the door in reach", () => {
    const p: [number, number, number] = [10.5, 0, 0.5];
    expect(interactPrompt([door, exit], p, reach(p), castle, "Basil")).toEqual({ id: "door_c", label: "Enter your dormitory" });
  });

  it("names class doors and the way out of classes and arenas", () => {
    const classDoor: Interactable = { id: "charms_door", kind: "class_door", name: "Charms Classroom", pos: [0, 0, 0], yaw: 0, radius: 1.8 };
    const out: Interactable = { id: "class_exit", kind: "instance_exit", pos: [0, 0, 0], yaw: 0, radius: 1.8 };
    const reachAll = () => true;
    expect(interactPrompt([classDoor], [0, 0, 0], reachAll, castle, "Basil")?.label).toBe("Enter the Charms Classroom");
    const classZone: Zone = { kind: "class", class: "charms", section: 0, layout: "class_charms" };
    const arena: Zone = { kind: "arena", duelId: 1, venue: "dueling_club", layout: "duel_arena" };
    expect(interactPrompt([out], [0, 0, 0], reachAll, classZone, "Basil")?.label).toBe("Leave the class");
    expect(interactPrompt([out], [0, 0, 0], reachAll, arena, "Basil")?.label).toBe("Leave the arena");
  });

  it("offers the Owl Loft's owl mail as sending an owl (it opens the mail on the client)", () => {
    const post = (castleLayout as unknown as { interactables: Interactable[] }).interactables.find((i) => i.id === "owl_mail")!;
    expect(post).toBeDefined();
    expect(isOwlMail(post)).toBe(true);
    expect(isOwlMail(door)).toBe(false);
    expect(interactPrompt([post], post.pos, () => true, castle, "Basil")).toEqual({ id: "owl_mail", label: "Send an owl" });
  });

  it("offers each common room's Owl Order Catalogue stand as browsing the catalogue", () => {
    const stands = (castleLayout as unknown as { interactables: Interactable[] }).interactables.filter((i) => i.kind === "owl_order");
    expect(stands.map((s) => s.house).sort()).toEqual(["burrowsby", "roarwing", "serpentyne", "skytalon"]);
    for (const s of stands) expect(interactPrompt([s], s.pos, () => true, castle, "Basil")).toEqual({ id: s.id, label: "Browse the Owl Order Catalogue" });
  });

  it("offers nothing out of reach", () => {
    const p: [number, number, number] = [5, 0, 0];
    expect(interactPrompt([door, exit], p, reach(p), castle, "Basil")).toBeNull();
  });

  it("words the dorm exit for owners and guests", () => {
    const p: [number, number, number] = [0, 0, 3.5];
    expect(interactPrompt([exit], p, reach(p), dorm("Basil"), "Basil")?.label).toBe("Return to the common room");
    expect(interactPrompt([exit], p, reach(p), dorm("Basil"), "Alina")?.label).toBe("Leave Basil's room");
  });
});

describe("parseInstanceManifest", () => {
  const cell = { id: "dorm_house_c", file: "dorm_house_c.glb", bounds: { min: [-5, 0, -5], max: [5, 5, 5] }, level: 0 };

  it("reads cells and lights from the entry and its cells", () => {
    const m = parseInstanceManifest({
      dorm_skytalon: {
        cells: [{ ...cell, lights: [{ id: "fire", cell: "dorm_house_c", pos: [1, 1, 1], color: "#ff9944", intensity: 2, range: 6 }] }],
        lights: [{ pos: [0, 4, 0], colour: "#ffffff" }],
      },
    });
    const e = m.get("dorm_skytalon")!;
    expect(e.cells.map((c) => c.file)).toEqual(["dorm_house_c.glb"]);
    expect(e.lights).toEqual([
      { pos: [0, 4, 0], color: "#ffffff", intensity: undefined, range: undefined },
      { pos: [1, 1, 1], color: "#ff9944", intensity: 2, range: 6 },
    ]);
  });

  it("reads a room's fill light, leaving out one that does not parse", () => {
    const m = parseInstanceManifest({
      a: { cells: [cell], ambient: { sky: "#8C96A8", ground: "#4a3a2c", intensity: 0.35 } },
      b: { cells: [cell], ambient: { sky: "blue", ground: "#4a3a2c", intensity: 0.35 } },
      c: { cells: [cell] },
    });
    expect(m.get("a")!.ambient).toEqual({ sky: "#8C96A8", ground: "#4a3a2c", intensity: 0.35 });
    expect(m.get("b")!.ambient).toBeUndefined();
    expect(m.get("c")!.ambient).toBeUndefined();
  });

  it("skips entries and lights that don't parse", () => {
    const m = parseInstanceManifest({ broken: { cells: "nope" }, ok: { cells: [cell], lights: [{ pos: [1, 2] }] } });
    expect([...m.keys()]).toEqual(["ok"]);
    expect(m.get("ok")!.lights).toEqual([]);
    expect(parseInstanceManifest(null).size).toBe(0);
  });
});
