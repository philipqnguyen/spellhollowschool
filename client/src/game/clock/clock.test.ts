import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import scheduleJson from "../../../../data/classes/schedule.json";
import { initSync } from "../../wasm/sim_wasm";
import { ServerClock } from "./clock";
import type { Scene } from "@babylonjs/core/scene";
import { applySky, nightLook, OPEN_SKY_NIGHT_BELOW, skyColours } from "./daynight";
import { castleLight, parseLighting, sceneLights } from "../toon/lighting";
import { classByDoor, CLASSES, DAY_REAL_MS, SchoolClock } from "./schedule";

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../../wasm/sim_wasm_bg.wasm", import.meta.url)) });
});

describe("the server clock", () => {
  it("follows Welcome, then the Pong with the shortest round trip", () => {
    let mono = 1000;
    const c = new ServerClock(() => mono);
    expect(c.synced).toBe(false);
    c.welcome(5_000_000);
    expect(c.synced).toBe(true);
    expect(c.now()).toBe(5_000_000);
    mono = 2000;
    expect(c.now()).toBe(5_001_000);
    // The server stamped its Pong half a round trip before it arrived.
    c.pong(5_001_100, 200);
    expect(c.now()).toBe(5_001_200);
    // A slower sample does not win over a faster one...
    c.pong(5_001_000, 600);
    expect(c.now()).toBe(5_001_200);
    // ...a faster one does.
    c.pong(5_001_150, 20);
    expect(c.now()).toBe(5_001_160);
    expect(c.fromLocal(3000)).toBe(5_002_160);
    expect(c.toLocal(5_002_160)).toBe(3000);
  });

  it("takes a jump (the development clock) at once", () => {
    let mono = 0;
    const c = new ServerClock(() => mono);
    c.welcome(1_000_000);
    c.pong(1_000_000, 10);
    mono = 100;
    c.pong(1_000_000 + 3_600_000, 300);
    expect(c.now()).toBe(1_000_000 + 3_600_000 + 150);
  });

  it("ignores nonsense", () => {
    const c = new ServerClock(() => 0);
    c.welcome(10);
    c.pong(Number.NaN, 5);
    c.pong(-1, 5);
    expect(c.now()).toBe(10);
  });
});

describe("the game clock and the timetable (sim-core via wasm)", () => {
  it("lists the timetable's classes with their doors", () => {
    expect(CLASSES.map((c) => c.id)).toEqual(scheduleJson.classes.map((c) => c.id));
    const charms = CLASSES[0]!;
    expect(classByDoor(charms.door)?.id).toBe(charms.id);
    expect(DAY_REAL_MS).toBe(2 * 3600 * 1000);
  });

  it("runs a game day every two real hours, with night and lessons where the data puts them", () => {
    const clock = new SchoolClock();
    const epoch = scheduleJson.clock.epoch_unix_ms;
    expect(clock.gameSeconds(epoch)).toBe(0);
    expect(clock.period(epoch)).toBe("night");
    // 08:00 game time (Charms' first slot) is 8/24 of two hours after midnight.
    const nine = epoch + (8 / 24) * DAY_REAL_MS;
    expect(clock.gameSeconds(nine)).toBe(8 * 3600);
    expect(clock.period(nine)).toBe("morning");
    expect(clock.daylight(nine)).toBe(1);
    const t = clock.classTimes("charms", nine + 1000);
    expect(t.current?.startMs).toBe(nine);
    expect(t.joinable).toBe(true);
    expect(t.current!.joinUntilMs - t.current!.startMs).toBe(CLASSES[0]!.joinSecs * 1000);
    expect(t.next.startMs).toBeGreaterThan(nine);
    const late = clock.classTimes("charms", nine + CLASSES[0]!.joinSecs * 1000 + 1);
    expect(late.joinable).toBe(false);
    expect(late.current).not.toBeNull();
    clock.dispose();
  });
});


describe("day and night", () => {
  it("follows the art lighting data by game hour: blue moonlight at night, warm at dusk", () => {
    const keys = parseLighting();
    expect(keys.length).toBeGreaterThan(2);
    const noon = castleLight(13);
    const night = castleLight(1);
    const dusk = castleLight(21.25);
    expect(noon.moon).toBe(0);
    expect(night.moon).toBe(1);
    expect(night.sunIntensity).toBeLessThan(noon.sunIntensity);
    expect(night.sunColour[2]).toBeGreaterThan(night.sunColour[0]); // moonlight is blue
    expect(dusk.sunColour[0]).toBeGreaterThan(dusk.sunColour[2]); // sunset is warm
    expect(noon.zenith[2]).toBeGreaterThan(noon.zenith[0]); // a blue sky overhead
    // Wraps at 24 h and interpolates between keys; directions stay unit length.
    expect(castleLight(24 + 13)).toEqual(noon);
    for (const h of [-3, 0, 5.9, 6.1, 10, 22.9, 23.5, Number.NaN]) {
      const l = castleLight(h);
      expect(Math.hypot(...l.sunDirection)).toBeCloseTo(1);
      expect(l.sunDirection[1]).toBeLessThan(0); // always from above
      expect(Math.max(...l.zenith, ...l.horizon, ...l.fog)).toBeLessThanOrEqual(1);
    }
  });

  it("lights stars and torches at night only", () => {
    expect(nightLook(1).stars).toBe(0);
    expect(nightLook(0).stars).toBe(1);
    expect(nightLook(0).torches).toBeGreaterThan(nightLook(1).torches);
    expect(nightLook(Number.NaN).stars).toBe(0);
    const sky = skyColours(castleLight(13), 0);
    expect(sky.glow).toEqual(castleLight(13).sunColour);
  });
});

describe("a room open to the sky (the moonlit Astronomy platform)", () => {
  // A stand-in scene: only the fill light (no sky dome, so applySky leaves that out).
  const fakeScene = () => {
    const hemi = { intensity: 1.2, diffuse: { r: 1, g: 1, b: 1 }, groundColor: { r: 1, g: 1, b: 1 } };
    const scene = { getLightByName: (n: string) => (n === "sky" ? hemi : null), getMeshByName: () => null } as unknown as Scene;
    return { scene, hemi };
  };

  it("takes the castle's low night fill by night", () => {
    const { scene, hemi } = fakeScene();
    applySky(scene, 1, 0);
    const night = sceneLights(castleLight(1));
    expect(hemi.intensity).toBeCloseTo(night.hemiIntensity, 6);
    expect(hemi.intensity).toBeLessThan(1.2);
    expect(hemi.diffuse.r).toBeCloseTo(night.hemiSky[0], 6);
  });

  it("keeps the room's own fill by day", () => {
    const { scene, hemi } = fakeScene();
    applySky(scene, 14.5, 1);
    expect(hemi.intensity).toBe(1.2);
    applySky(scene, 6, OPEN_SKY_NIGHT_BELOW);
    expect(hemi.intensity).toBe(1.2);
  });
});
