// Solid furniture in a dorm (docs/architecture.md "Layouts"):
// the room's placed pieces are solid in the world the player
// predicts with (`SimWorld.setFurniture`, as the server has them), a rug is
// walked over, and `keepsWay` refuses a piece on someone or across the way
// out, as the server does.

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import dormText from "../../../../data/world/instances/dorm_skytalon.json?raw";
import { initSync, SimWorld } from "../../wasm/sim_wasm";
import { furnitureDef } from "./catalog";
import { DormRoom } from "./room";

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

const DESK = 1004; // a floor table, 1.2 x 0.6 m, 0.76 m tall
function rugId(): number {
  for (let id = 1000; id < 1200; id++) {
    const d = furnitureDef(id);
    if (d && d.category === "rugs") return id;
  }
  throw new Error("no rug in the catalog");
}

function walk(sim: SimWorld, from: [number, number, number], dx: number, dz: number, frames: number): ArrayLike<number> {
  let p: ArrayLike<number> = from;
  for (let i = 0; i < frames; i++) p = sim.moveAndSlide(p[0]!, p[1]!, p[2]!, dx, dz);
  return p;
}

describe("solid dorm furniture", () => {
  it("stops a walk at a placed desk, and not at a rug", () => {
    const sim = new SimWorld(dormText);
    const room = new DormRoom();
    room.reset({ openToFriends: false, cap: 150, items: [{ itemId: 7, itemDef: DESK, dyes: [0, 0, 0], pos: [0, 0, -1.5], yaw: 0 }] });
    // Before: straight through where the desk will be.
    expect(walk(sim, [0, 0, 1], 0, -0.05, 40)[2]).toBeLessThan(-0.9);
    sim.setFurniture(room.pieces());
    // The desk's near edge is at z -1.2; the character's radius keeps it 0.3 m off.
    const stopped = walk(sim, [0, 0, 1], 0, -0.05, 40);
    expect(stopped[2]).toBeGreaterThan(-1.2 + 0.3 - 0.05);
    expect(stopped[2]).toBeLessThan(-1.2 + 0.3 + 0.05);
    // A rug is walked over.
    const rug = rugId();
    room.reset({ openToFriends: false, cap: 150, items: [{ itemId: 8, itemDef: rug, dyes: [0, 0, 0], pos: [0, 0, -1.5], yaw: 0 }] });
    sim.setFurniture(room.pieces());
    expect(walk(sim, [0, 0, 1], 0, -0.05, 40)[2]).toBeLessThan(-0.9);
    // Taking the desk away opens the way again.
    sim.setFurniture([]);
    expect(walk(sim, [0, 0, 1], 0, -0.05, 40)[2]).toBeLessThan(-0.9);
  });

  it("refuses a piece on someone, on the entry or across the way out", () => {
    const sim = new SimWorld(dormText);
    const def = furnitureDef(DESK)!;
    const piece = (x: number, z: number) => ({ id: 9, furniture: def.raw, pos: [x, 0, z] as [number, number, number], yaw: 0 });
    expect(sim.keepsWay([], piece(0, -1.5), [])).toBe(true);
    // Standing where it would go.
    expect(sim.keepsWay([], piece(0, -1.5), [[0.2, 0, -1.5]])).toBe(false);
    // On the entry (the spawn at z 3.4) or the way out (the exit at z 4.3).
    expect(sim.keepsWay([], piece(0, 3.4), [])).toBe(false);
  });
});
