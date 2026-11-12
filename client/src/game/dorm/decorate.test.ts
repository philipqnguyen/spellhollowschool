// Decorating mode: the ghost turns as the piece will stand (Q/E), a placed
// piece under the cursor is highlighted and the cursor shows it can be
// clicked, and a piece with something on it is not lifted (the moving rule).

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Quaternion } from "@babylonjs/core/Maths/math.vector";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import { initSync, type PlacedFurniture, type SimWorld } from "../../wasm/sim_wasm";
import { furnitureDef } from "./catalog";
import { DecorateMode } from "./decorate";
import { Ghost, type FurnitureRenderer, type Model } from "./furniture";
import type { Ray } from "./placement";
import { DormRoom } from "./room";

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

describe("the turned preview", () => {
  it("turns the ghost and its box with Q/E (a loaded mesh keeps an identity quaternion)", () => {
    const scene = new Scene(new NullEngine());
    // As the glTF loader leaves a baked mesh: a rotation quaternion that would override `rotation`.
    const mesh = CreateBox("desk", { width: 1.2, height: 0.78, depth: 0.6 }, scene);
    mesh.rotationQuaternion = Quaternion.Identity();
    const model: Model = { def: furnitureDef(1004)!, state: "ready", sources: [{ mesh, mask: null, linear: false }], container: null, variants: new Map() };
    const ghost = new Ghost(scene, model);
    // A quarter turn: the desk's front (+Z) faces +X.
    ghost.place([1, 0, 1], 64, true, [0, 0, 0]);
    const copy = ghost.meshes[0]!;
    copy.computeWorldMatrix(true);
    const front = Vector3.TransformNormal(new Vector3(0, 0, 1), copy.getWorldMatrix());
    expect(front.x).toBeCloseTo(1);
    expect(front.z).toBeCloseTo(0);
    ghost.outline.computeWorldMatrix(true);
    const boxFront = Vector3.TransformNormal(new Vector3(0, 0, 1), ghost.outline.getWorldMatrix()).normalize();
    expect(boxFront.x).toBeCloseTo(1);
    // One 22.5 degree step.
    ghost.place([1, 0, 1], 16, true, [0, 0, 0]);
    copy.computeWorldMatrix(true);
    const step = Vector3.TransformNormal(new Vector3(0, 0, 1), copy.getWorldMatrix());
    expect(step.x).toBeCloseTo(Math.sin(Math.PI / 8));
    expect(step.z).toBeCloseTo(Math.cos(Math.PI / 8));
    ghost.dispose();
    scene.dispose();
  });
});

/** Decorating mode over a room, with the renderer, canvas and camera faked and the cursor's ray given. */
function harness(items: PlacedFurniture[]) {
  const room = new DormRoom();
  room.reset({ openToFriends: false, cap: 150, items });
  const calls = { highlight: [] as (number | null)[], hidden: [] as [number, boolean][], notes: [] as string[], ghosts: [] as (number | null)[] };
  const renderer = {
    showGhost: (itemDef: number | null, _d: readonly number[], _p: unknown, yawUnits: number) => calls.ghosts.push(itemDef === null ? null : yawUnits),
    setHidden: (id: number, hidden: boolean) => calls.hidden.push([id, hidden]),
    setHighlight: (id: number | null) => calls.highlight.push(id),
    preload: () => {},
  } as unknown as FurnitureRenderer;
  const canvas = {
    style: { cursor: "" },
    addEventListener: () => {},
    removeEventListener: () => {},
    getBoundingClientRect: () => ({ left: 0, top: 0 }),
  } as unknown as HTMLCanvasElement;
  const sim = { placeAreas: () => [{ id: "floor", surface: "floor", min: [-4.4, 0, -4.4], max: [4.4, 0, 4.4] }], canPlace: () => true, keepsWay: () => true } as unknown as SimWorld;
  const mode = new DecorateMode({} as Scene, canvas, {} as Camera, room, renderer, {
    sim: () => sim,
    people: () => [],
    send: () => {},
    notify: (t) => calls.notes.push(t),
    onChange: () => {},
  });
  let ray: Ray = { origin: [0, 3, 4], dir: [0, -1, 0] };
  // The picking ray from the cursor (Babylon's, which wants a real camera) is the harness's.
  (mode as unknown as { ray: () => Ray }).ray = () => ray;
  const point = (r: Ray) => {
    ray = r;
    (mode as unknown as { pointer: { x: number; y: number } }).pointer = { x: 1, y: 1 };
    mode.update();
  };
  return { mode, room, canvas, calls, point };
}

const desk: PlacedFurniture = { itemId: 7, itemDef: 1004, dyes: [10, 20, 21], pos: [1, 0, 1], yaw: 0 };
const lantern: PlacedFurniture = { itemId: 20, itemDef: 1015, dyes: [1, 1, 1], pos: [1.3, 0.76, 1], yaw: 0 };
const atDesk: Ray = { origin: [1, 3, 4], dir: [0, -3, -3] };
const atFloor: Ray = { origin: [-3, 3, 3], dir: [0, -1, 0] };

describe("decorating mode", () => {
  it("highlights the placed piece under the cursor and shows a pointer", () => {
    const { mode, canvas, calls, point } = harness([desk]);
    mode.start();
    point(atDesk);
    expect(mode.hovered).toBe(7);
    expect(calls.highlight.at(-1)).toBe(7);
    expect(canvas.style.cursor).toBe("pointer");
    point(atFloor);
    expect(mode.hovered).toBeNull();
    expect(calls.highlight.at(-1)).toBeNull();
    expect(canvas.style.cursor).toBe("");
    // With a piece in hand there is no hover: the ghost follows the cursor instead.
    point(atDesk);
    expect(mode.hovered).toBe(7);
    mode.selectFromInventory({ id: 99, itemDef: 1005, dyes: [1, 2, 3] });
    point(atDesk);
    expect(mode.hovered).toBeNull();
    expect(calls.highlight.at(-1)).toBeNull();
    expect(canvas.style.cursor).toBe("");
    mode.stop();
    expect(canvas.style.cursor).toBe("");
  });

  it("turns the piece in hand: the ghost is drawn at the new yaw", () => {
    const { mode, calls, point } = harness([]);
    mode.start();
    mode.selectFromInventory({ id: 99, itemDef: 1004, dyes: [1, 2, 3] });
    point(atFloor);
    expect(calls.ghosts.at(-1)).toBe(0);
    mode.rotate(1);
    point(atFloor);
    expect(calls.ghosts.at(-1)).toBe(16);
    mode.togglePrecise();
    mode.rotate(-1);
    point(atFloor);
    expect(calls.ghosts.at(-1)).toBe(15); // precise: 1/256 of a turn
  });

  it("will not lift a piece with something on it, and says what to take off", () => {
    const { mode, room, calls, point } = harness([desk, lantern]);
    mode.start();
    point(atDesk);
    (mode as unknown as { click: () => void }).click();
    expect(mode.selection).toBeNull();
    expect(calls.notes.at(-1)).toBe("Take the Candle Lantern off the Writing Desk first.");
    // The lantern taken off: the desk lifts.
    room.apply({ kind: "removed", itemId: 20 });
    point(atDesk);
    (mode as unknown as { click: () => void }).click();
    expect(mode.selection).toMatchObject({ itemId: 7, existing: true });
    expect(calls.hidden.at(-1)).toEqual([7, true]);
  });
});
