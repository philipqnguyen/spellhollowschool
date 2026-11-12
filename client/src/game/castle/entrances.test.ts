// The passages' entrances and the Wishing Room's door
// (castle/entrances.ts, nodes.ts): the node contract (castle-kit.md),
// which doors open an entrance, how each style moves, and that the moving
// nodes cast no static sun shadow.

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { castsSunShadow, entranceAnimOf, inSecretNode, isWishingRoomDoorNode, WISHING_ROOM_DOOR_NODE } from "./nodes";
import { atDoor, DOUBLE_TURN, entrancePose, KitEntrances } from "./entrances";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";

const node = (name: string, kit_anim?: unknown, parent?: unknown) => ({ name, metadata: kit_anim === undefined ? undefined : { gltf: { extras: { kit_anim } } }, parent });

describe("the secrets' nodes", () => {
  it("reads an entrance by its extras, or failing them its name", () => {
    expect(entranceAnimOf(node("l5_corridor_west.castle_props.entrance_sp_tapestry_door_l5", { kind: "entrance", door: "sp_tapestry_door_l5", style: "tapestry" }))).toEqual({
      kind: "entrance",
      door: "sp_tapestry_door_l5",
      style: "tapestry",
    });
    // An unknown style slides like a panel; a missing door is no entrance.
    expect(entranceAnimOf(node("x", { kind: "entrance", door: "d", style: "trapdoor" }))?.style).toBe("panel");
    expect(entranceAnimOf(node("x", { kind: "entrance", style: "wall" }))).toBeNull();
    expect(entranceAnimOf(node("trophy_room.castle_props.entrance_sp_panel_door_trophy"))).toEqual({ kind: "entrance", door: "sp_panel_door_trophy", style: "panel" });
    // Another kind of moving node is not one, whatever its name.
    expect(entranceAnimOf(node("a.castle_props.entrance_x", { kind: "bob" }))).toBeNull();
    expect(entranceAnimOf(node("trophy_room.castle_props.case_w5"))).toBeNull();
  });

  it("knows the Wishing Room's door", () => {
    expect(isWishingRoomDoorNode(node(WISHING_ROOM_DOOR_NODE))).toBe(true);
    expect(isWishingRoomDoorNode(node("anything", { kind: "wishing_room_door" }))).toBe(true);
    expect(isWishingRoomDoorNode(node(WISHING_ROOM_DOOR_NODE, { kind: "swing", flight: "f" }))).toBe(false);
    expect(isWishingRoomDoorNode(node("l7_corridor_east.castle_props.torch_1"))).toBe(false);
  });

  it("keeps the moving nodes out of the static sun shadow", () => {
    const door = node(WISHING_ROOM_DOOR_NODE);
    const child = { ...node("l7_corridor_east.castle_props.wishing_room_door_leaf"), parent: door };
    expect(inSecretNode(child)).toBe(true);
    expect(castsSunShadow(child as unknown as AbstractMesh)).toBe(false);
    const wall = node("l7_corridor_east.castle_stone");
    expect(inSecretNode(wall)).toBe(false);
    expect(castsSunShadow(wall as unknown as AbstractMesh)).toBe(true);
  });
});

describe("the entrances' motion", () => {
  it("opens for anyone on or right at the door, on its floor", () => {
    const door = { min: [-71, 7] as [number, number], max: [-69, 9] as [number, number], y: 45 };
    expect(atDoor(door, -70, 45, 8)).toBe(true);
    expect(atDoor(door, -70, 45, 6.5)).toBe(true); // just in front of it
    expect(atDoor(door, -70, 45, 5)).toBe(false);
    expect(atDoor(door, -70, 36, 8)).toBe(false); // the floor below
    // A passage door opens for anyone, whatever their house.
    expect(atDoor(door, -70, 45, 8, "serpentyne")).toBe(true);
  });

  it("opens a house's door only for that house", () => {
    const layout = JSON.parse(readFileSync(new URL("../../../../data/world/layout.json", import.meta.url), "utf8")) as { surfaces: { id: string; kind: string; house?: string; min: [number, number]; max: [number, number]; y?: number }[] };
    const doors = layout.surfaces.filter((s) => s.kind === "door" && s.house);
    // The guardian portrait, the knocker door, the hidden stone door and the barrel stack.
    expect(doors.map((d) => d.id).sort()).toEqual(["cr_a_portrait", "cr_b_barrels", "cr_c_door", "cr_d_wall"]);
    for (const d of doors) {
      const rect = { min: d.min, max: d.max, y: d.y ?? 0, house: d.house };
      const x = (d.min[0] + d.max[0]) / 2;
      const z = (d.min[1] + d.max[1]) / 2;
      for (const house of ["roarwing", "burrowsby", "skytalon", "serpentyne"]) expect(atDoor(rect, x, rect.y, z, house)).toBe(house === d.house);
      expect(atDoor(rect, x, rect.y, z, d.house!.toUpperCase())).toBe(true);
      expect(atDoor(rect, x, rect.y, z)).toBe(false);
    }
  });

  it("moves each style its own way, shut at 0", () => {
    for (const style of ["tapestry", "panel", "painting", "statue", "wall", "double"] as const) expect(entrancePose(style, 0, 1.3)).toEqual({ turn: 0, slide: 0, sink: 0 });
    expect(entrancePose("tapestry", 1, 0).turn).toBeCloseTo(0.5, 5);
    expect(entrancePose("painting", 1, 0).turn).toBeCloseTo(1.25, 5);
    expect(entrancePose("statue", 1, 0).turn).toBeCloseTo(Math.PI / 2, 5);
    expect(entrancePose("panel", 1, 0)).toEqual({ turn: 0, slide: 0.95, sink: 0 });
    expect(entrancePose("wall", 1, 0)).toEqual({ turn: 0, slide: 0, sink: 0.97 });
    // Eased: half open is half way; the cloth sways while held open.
    expect(entrancePose("panel", 0.5, 0).slide).toBeCloseTo(0.475, 5);
    expect(entrancePose("tapestry", 1, 0.5).turn).not.toBeCloseTo(entrancePose("tapestry", 1, 0).turn, 3);
  });

  it("turns each leaf of a double door 50 degrees, eased (Knocker door opens as a pair)", () => {
    expect(DOUBLE_TURN).toBeCloseTo((50 * Math.PI) / 180, 6); // castle_kit grid.py DOUBLE_DOOR_TURN
    expect(entrancePose("double", 0, 0)).toEqual({ turn: 0, slide: 0, sink: 0 }); // shut
    expect(entrancePose("double", 0.5, 2).turn).toBeCloseTo(DOUBLE_TURN / 2, 6); // half: half way, no sway
    expect(entrancePose("double", 0.25, 0).turn).toBeCloseTo(0.15625 * DOUBLE_TURN, 6); // eased in
    expect(entrancePose("double", 1, 0)).toEqual({ turn: DOUBLE_TURN, slide: 0, sink: 0 }); // open
    expect(entrancePose("double", 1.5, 0).turn).toBeCloseTo(DOUBLE_TURN, 6); // never past open
  });

  it("reads a double door's leaf", () => {
    expect(entranceAnimOf(node("west_tower_stair.castle_wood.entrance_cr_c_door_leaf_l", { kind: "entrance", door: "cr_c_door", style: "double", leaf: "l" }))).toEqual({
      kind: "entrance",
      door: "cr_c_door",
      style: "double",
      leaf: "l",
    });
    expect(entranceAnimOf(node("x", { kind: "entrance", door: "d", style: "double", leaf: "middle" }))).toEqual({ kind: "entrance", door: "d", style: "double" });
  });
});

describe("a double door (Knocker door opens as a pair)", () => {
  // The knocker door as exported: a 4 m pair in the tower landing's south wall (face z -4,
  // the leaves 0.35-0.43 m into the passage, the common room beyond at z < -6), each leaf
  // three nodes (props, trim, wood) with their origin on its hinge (the outer edge's back face).
  function pair(scene: Scene): TransformNode[] {
    const out: TransformNode[] = [];
    for (const [leaf, sx] of [["l", -1], ["r", 1]] as const) {
      for (const mat of ["castle_props", "castle_trim", "castle_wood"]) {
        const n = new TransformNode(`west_tower_stair.${mat}.entrance_cr_c_door_leaf_${leaf}`, scene);
        n.metadata = { gltf: { extras: { kit_anim: { kind: "entrance", door: "cr_c_door", style: "double", leaf, yaw: 0 } } } };
        n.position.set(-100 + sx * 2, 54, -4.43);
        const box = CreateBox(`${leaf}_${mat}`, { width: 2, height: mat === "castle_props" ? 0.9 : 4.4, depth: 0.08 }, scene);
        box.position.set(-sx * 1, 2.2, 0.04); // the leaf's middle, 1 m in from its hinge
        box.parent = n;
        out.push(n);
      }
    }
    return out;
  }

  it("opens from the centre: each leaf about its outer edge, both into the passage, for its house only", () => {
    const engine = new NullEngine();
    const scene = new Scene(engine);
    const doors = new Map([["cr_c_door", { min: [-102, -6] as [number, number], max: [-98, -4] as [number, number], y: 54, house: "skytalon" }]]);
    const e = new KitEntrances(scene, doors, null);
    expect(e.wire("west_tower_stair", pair(scene))).toBe(2); // a leaf each
    const mid = (name: string) => {
      const m = scene.getMeshByName(name)!.getWorldMatrix();
      return [m.m[12]!, m.m[14]!] as const;
    };
    expect(mid("l_castle_wood")[0]).toBeCloseTo(-101, 5);
    expect(mid("r_castle_wood")[0]).toBeCloseTo(-99, 5);
    // Another house: shut.
    for (let i = 0; i < 10; i++) e.update(0.1, i * 0.1, [[-100, 54, -2.5, "serpentyne"]], 0);
    expect(e.counts.open).toBe(0);
    // Half open (0.5 s at 2.5 a second would overshoot; 0.2 s is half way).
    e.update(0.1, 0, [[-100, 54, -3.5, "skytalon"]], 0);
    e.update(0.1, 0, [[-100, 54, -3.5, "skytalon"]], 0);
    expect(e.counts.open).toBe(2);
    const [lx, lz] = mid("l_castle_wood");
    const [rx, rz] = mid("r_castle_wood");
    // Each leaf's middle (1 m in, 4 cm in front of its hinge) turned half way into the passage (-z).
    const at = (turn: number) => [Math.cos(turn) + 0.04 * Math.sin(turn), -Math.sin(turn) + 0.04 * Math.cos(turn)] as const;
    const [hx, hz] = at(DOUBLE_TURN / 2);
    expect(Math.hypot(lx + 102, lz + 4.43)).toBeCloseTo(Math.hypot(1, 0.04), 5);
    expect(lx).toBeCloseTo(-102 + hx, 4);
    expect(lz).toBeCloseTo(-4.43 + hz, 4);
    expect(rx).toBeCloseTo(-98 - hx, 4);
    expect(rz).toBeCloseTo(lz, 4); // mirror images
    // Open: both leaves fully turned into the passage, parted at the middle.
    for (let i = 0; i < 10; i++) e.update(0.1, 0, [[-100, 54, -6.5, "skytalon"]], 0); // from inside too
    const [ox, oz] = mid("l_castle_wood");
    const [fx, fz] = at(DOUBLE_TURN);
    expect(ox).toBeCloseTo(-102 + fx, 3);
    expect(oz).toBeCloseTo(-4.43 + fz, 3);
    expect(mid("r_castle_wood")[0]).toBeCloseTo(-98 - fx, 3);
    expect(mid("r_castle_wood")[0] - ox).toBeGreaterThan(2); // the middles 1 m in from each jamb now over 2 m apart
    // The knocker (the props node) went with its leaf.
    expect(mid("l_castle_props")[0]).toBeCloseTo(ox, 4);
    // Gone: it closes again.
    for (let i = 0; i < 10; i++) e.update(0.1, 0, [], 0);
    expect(e.counts.open).toBe(0);
    expect(mid("l_castle_wood")[0]).toBeCloseTo(-101, 5);
    e.dispose();
    scene.dispose();
    engine.dispose();
  });
});

describe("an entrance in several nodes", () => {
  it("moves every node of one door as one entrance", () => {
    const engine = new NullEngine();
    const scene = new Scene(engine);
    const anim = { kind: "entrance", door: "cr_c_door", style: "painting" };
    // The knocker door comes as three nodes, one per material.
    const nodes = [0, 1, 2].map((i) => {
      const n = new TransformNode(`west_tower_stair.castle_props.entrance_cr_c_door_${i}`, scene);
      n.metadata = { gltf: { extras: { kit_anim: anim } } };
      const box = CreateBox(`part_${i}`, { width: 0.6, height: 2.4, depth: 0.1 }, scene);
      box.position.set(-0.6 + i * 0.6, 1.2, 0);
      box.parent = n;
      return n;
    });
    const doors = new Map([["cr_c_door", { min: [-1, 0.2] as [number, number], max: [1, 1] as [number, number], y: 0, house: "skytalon" }]]);
    const e = new KitEntrances(scene, doors, null);
    expect(e.wire("west_tower_stair", nodes)).toBe(1);
    expect(e.counts.wired).toBe(1);
    // It swings for its house only, every part about the one hinge.
    e.update(1, 0, [[0, 0, 0.5, "serpentyne"]], 0);
    expect(e.counts.open).toBe(0);
    for (let i = 0; i < 5; i++) e.update(0.1, i * 0.1, [[0, 0, 0.5, "skytalon"]], 0);
    expect(e.counts.open).toBe(1);
    const box0 = scene.getMeshByName("part_0")!;
    const box2 = scene.getMeshByName("part_2")!;
    // The part at the hinge barely moves; the free edge swings out.
    const moved = (m: AbstractMesh, x: number) => Math.hypot(m.getWorldMatrix().m[12]! - x, m.getWorldMatrix().m[14]!);
    expect(moved(box0, -0.6)).toBeLessThan(moved(box2, 0.6));
    e.dispose();
    scene.dispose();
    engine.dispose();
  });
});
