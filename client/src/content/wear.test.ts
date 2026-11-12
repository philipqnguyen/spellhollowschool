// The wear rule as the client applies it (labels, the
// creator's TS mirror of sim-wasm `canWear`).

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import { canWear as wasmCanWear, initSync, type Wear } from "../wasm/sim_wasm";
import { bodyCanWear, bodyWear, canWear, CATALOG, CREATOR, itemDef, WEAR_LABELS, wearLabel, wearRefusal } from "./catalog";

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

describe("the wear rule", () => {
  it("agrees with sim-wasm canWear for every body and item class", () => {
    const classes: (Wear | undefined)[] = ["female", "male", undefined];
    for (const body of ["female", "male"] as Wear[]) for (const item of classes) expect(canWear(body, item)).toBe(wasmCanWear(body, item));
  });

  it("labels exactly the restricted items, in the agreed words", () => {
    expect(WEAR_LABELS).toEqual({ female: "Female only", male: "Male only" });
    const female = CATALOG.items.filter((i) => wearLabel(i) === "Female only").map((i) => i.id);
    const male = CATALOG.items.filter((i) => wearLabel(i) === "Male only").map((i) => i.id);
    expect(female.sort((a, b) => a - b)).toEqual([111, 112, 218, 302, 304, 306, 407]);
    expect(male).toEqual([305]);
    expect(wearLabel(301)).toBeNull();
    expect(wearLabel(1001)).toBeNull();
    expect(wearLabel(999_999)).toBeNull();
    for (const i of CATALOG.items) if (i.wear) expect(i.kind).toBe("clothing");
  });

  it("knows each body's class and refuses with the server's words", () => {
    expect(CREATOR.body_types.map((b) => bodyWear(b.index))).toEqual(["male", "female"]);
    const skirt = itemDef(302);
    const kilt = itemDef(305);
    const trousers = itemDef(301);
    expect(bodyCanWear(1, skirt)).toBe(true);
    expect(bodyCanWear(0, skirt)).toBe(false);
    expect(bodyCanWear(0, kilt)).toBe(true);
    expect(bodyCanWear(1, kilt)).toBe(false);
    for (const b of [0, 1]) expect(bodyCanWear(b, trousers)).toBe(true);
    expect(wearRefusal(0, skirt)).toBe("That is female only.");
    expect(wearRefusal(1, kilt)).toBe("That is male only.");
    expect(wearRefusal(1, skirt)).toBeNull();
    expect(wearRefusal(0, undefined)).toBeNull();
  });
});
