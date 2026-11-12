// Spells to level 20 (SOCIAL): the server moving the local player (Adveni,
// `ForcedMove`): the predicted position lerps to where it says, the keys do
// nothing until the hold is over, then walking carries on from there.

import { describe, expect, it } from "vitest";
import type { MoveParams, SimWorld } from "../wasm/sim_wasm";
import { forcedAt, LocalPlayer } from "./player";

/** A flat, open world: every step goes where it is asked. */
const open = {
  moveAndSlide: (x: number, y: number, z: number, dx: number, dz: number) => new Float32Array([x + dx, y, z + dz]),
} as unknown as SimWorld;
const params = { walkSpeed: 3, runSpeed: 6.5 } as unknown as MoveParams;
const north = { forward: 1, strafe: 0, run: false } as unknown as Parameters<LocalPlayer["update"]>[1];
const still = { forward: 0, strafe: 0, run: false } as unknown as Parameters<LocalPlayer["update"]>[1];
const look: [number, number] = [0, 1];

describe("forced moves", () => {
  it("lerps linearly over the slide, then stays", () => {
    expect(forcedAt([0, 0, 0], [2, 1, -4], 0.35, 0)).toEqual([0, 0, 0]);
    const half = forcedAt([0, 0, 0], [2, 1, -4], 0.35, 0.175);
    expect(half[0]).toBeCloseTo(1);
    expect(half[1]).toBeCloseTo(0.5);
    expect(half[2]).toBeCloseTo(-2);
    expect(forcedAt([0, 0, 0], [2, 1, -4], 0.35, 5)).toEqual([2, 1, -4]);
    expect(forcedAt([0, 0, 0], [2, 1, -4], 0, 0)).toEqual([2, 1, -4]);
  });

  it("slides the player, ignores the keys until the hold is over, then walks from there", () => {
    const p = new LocalPlayer(open, params);
    p.placeAt([10, 0, 10]);
    p.force([12.5, 0, 10], 350, 450);
    expect(p.beingMoved).toBe(true);
    // Keys held all the while: no walking of our own.
    p.update(0.175, north, look);
    expect(p.x).toBeCloseTo(11.25);
    expect(p.z).toBeCloseTo(10);
    expect(p.anim).toBe("idle");
    p.update(0.2, north, look);
    expect([p.x, p.y, p.z]).toEqual([12.5, 0, 10]);
    expect(p.beingMoved).toBe(true);
    p.update(0.074, north, look);
    expect([p.x, p.z]).toEqual([12.5, 10]);
    // The hold ends at 450 ms.
    p.update(0.002, north, look);
    expect(p.beingMoved).toBe(false);
    p.update(0.1, north, look);
    expect(p.x).toBeCloseTo(12.5);
    expect(p.z).toBeCloseTo(10.3);
    expect(p.anim).toBe("walk");
  });

  it("ends with a correction or a zone change", () => {
    const p = new LocalPlayer(open, params);
    p.placeAt([0, 0, 0]);
    p.force([2, 0, 0], 300, 800);
    p.update(0.1, still, look);
    p.placeAt([5, 0, 5]);
    expect(p.beingMoved).toBe(false);
    p.update(0.1, north, look);
    expect(p.z).toBeCloseTo(5.3);
  });

  it("turns but does not walk while bound (speed 0)", () => {
    const p = new LocalPlayer(open, { walkSpeed: 0, runSpeed: 0 } as unknown as MoveParams);
    p.placeAt([0, 0, 0], 0);
    p.update(0.1, { forward: 0, strafe: 1, run: false } as unknown as Parameters<LocalPlayer["update"]>[1], look);
    expect([p.x, p.z]).toEqual([0, 0]);
    expect(p.anim).toBe("idle");
    expect(p.yaw).not.toBe(0);
  });
});
