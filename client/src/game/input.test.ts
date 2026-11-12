// The keys (input.ts): arrows turn the camera and never move the character,
// running is the default with Z to walk, R auto runs and R / S / Esc and the
// game's events stop it.

import { describe, expect, it } from "vitest";
import { moveChipText } from "../ui/move-chip";
import { autoRunStopFor, CAMERA_PITCH_RATE, CAMERA_YAW_RATE, cameraStep, InputState, STALL_SECONDS, StallWatch } from "./input";

const FREE = { typing: false, claimed: false, owned: false };

describe("the arrow keys", () => {
  it("never move the character", () => {
    const s = new InputState();
    // Even if a stray arrow reached keyDown, it is no movement key.
    for (const code of ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]) {
      s.keyDown(code);
      s.cameraKeyDown(code, FREE);
    }
    const m = s.read();
    expect(m.forward).toBe(0);
    expect(m.strafe).toBe(0);
  });

  it("turn the camera: Left / Right orbit, Up / Down tilt, at a steady rate while held", () => {
    const s = new InputState();
    expect(s.cameraKeyDown("ArrowRight", FREE)).toBe(true);
    expect(s.cameraKeys()).toEqual({ yaw: 1, pitch: 0 });
    expect(cameraStep(s.cameraKeys(), 0.05).dAlpha).toBeCloseTo(CAMERA_YAW_RATE * 0.05);
    s.keyUp("ArrowRight");
    s.cameraKeyDown("ArrowLeft", FREE);
    s.cameraKeyDown("ArrowUp", FREE);
    expect(s.cameraKeys()).toEqual({ yaw: -1, pitch: 1 });
    const step = cameraStep(s.cameraKeys(), 0.02);
    expect(step.dAlpha).toBeCloseTo(-CAMERA_YAW_RATE * 0.02);
    expect(step.dBeta).toBeCloseTo(CAMERA_PITCH_RATE * 0.02);
    // A long frame (a hitch) turns no more than a tenth of a second's worth.
    expect(cameraStep(s.cameraKeys(), 0.5).dAlpha).toBeCloseTo(-CAMERA_YAW_RATE * 0.1);
    s.keyUp("ArrowLeft");
    s.keyUp("ArrowUp");
    s.cameraKeyDown("ArrowDown", FREE);
    expect(s.cameraKeys()).toEqual({ yaw: 0, pitch: -1 });
    s.clear();
    expect(s.cameraKeys()).toEqual({ yaw: 0, pitch: 0 });
  });

  it("are ignored while typing, when something took them (spell targeting, the trade list) or a focused control uses them", () => {
    const s = new InputState();
    expect(s.cameraKeyDown("ArrowLeft", { ...FREE, typing: true })).toBe(false);
    expect(s.cameraKeyDown("ArrowRight", { ...FREE, claimed: true })).toBe(false);
    expect(s.cameraKeyDown("ArrowUp", { ...FREE, owned: true })).toBe(false);
    expect(s.cameraKeyDown("KeyW", FREE)).toBe(false);
    expect(s.cameraKeys()).toEqual({ yaw: 0, pitch: 0 });
  });
});

describe("walk and run", () => {
  it("runs by default; Z walks and Z again runs; Shift changes nothing on foot", () => {
    const s = new InputState();
    s.keyDown("KeyW");
    expect(s.read()).toMatchObject({ forward: 1, run: true });
    expect(s.toggleWalk()).toBe(true);
    expect(s.walking).toBe(true);
    expect(s.read().run).toBe(false);
    s.keyDown("ShiftLeft");
    expect(s.read().run).toBe(false);
    expect(s.toggleWalk()).toBe(false);
    expect(s.read().run).toBe(true);
  });

  it("on a broom, `run` is still Shift: the dive", () => {
    const s = new InputState();
    expect(s.read(true).run).toBe(false);
    s.keyDown("ShiftRight");
    expect(s.read(true).run).toBe(true);
  });
});

describe("auto run", () => {
  it("R runs forward as if W were held, and R again stops", () => {
    const s = new InputState();
    expect(s.toggleAutoRun()).toBe(true);
    expect(s.read()).toMatchObject({ forward: 1, strafe: 0, run: true });
    expect(s.toggleAutoRun()).toBe(false);
    expect(s.lastStop).toBe("toggle");
    expect(s.read().forward).toBe(0);
  });

  it("follows the Z mode: walking if Z chose walking", () => {
    const s = new InputState();
    s.toggleWalk();
    s.toggleAutoRun();
    expect(s.read()).toMatchObject({ forward: 1, run: false });
  });

  it("steers with A and D as they do with W held; W just keeps going", () => {
    const s = new InputState();
    s.toggleAutoRun();
    s.keyDown("KeyA");
    expect(s.read()).toMatchObject({ forward: 1, strafe: -1 });
    s.keyUp("KeyA");
    s.keyDown("KeyD");
    s.keyDown("KeyW");
    expect(s.read()).toMatchObject({ forward: 1, strafe: 1 });
    expect(s.autoRun).toBe(true);
  });

  it("S stops it (and then backs up as usual)", () => {
    const s = new InputState();
    s.toggleAutoRun();
    s.keyDown("KeyS");
    expect(s.autoRun).toBe(false);
    expect(s.lastStop).toBe("back");
    expect(s.read().forward).toBe(-1);
  });

  it("Esc and the game's events stop it; stopping twice is harmless", () => {
    const s = new InputState();
    s.toggleAutoRun();
    expect(s.stopAutoRun("escape")).toBe(true);
    expect(s.stopAutoRun("escape")).toBe(false);
    s.toggleAutoRun();
    expect(s.stopAutoRun(autoRunStopFor("zoneChanged")!)).toBe(true);
    expect(s.lastStop).toBe("zone");
  });

  it("stops on a zone change, a reconnect, a broom, a class, a duel or a crafting station; not on a snapshot", () => {
    expect(autoRunStopFor("zoneChanged")).toBe("zone");
    expect(autoRunStopFor("welcome")).toBe("zone");
    expect(autoRunStopFor("mounted")).toBe("mount");
    expect(autoRunStopFor("classJoined")).toBe("activity");
    expect(autoRunStopFor("duelStart")).toBe("activity");
    expect(autoRunStopFor("stationOpened")).toBe("activity");
    expect(autoRunStopFor("snapshot")).toBeNull();
    expect(autoRunStopFor("correction")).toBeNull();
  });

  it("keeps running while typing (no keys read), and clearing held keys keeps it on", () => {
    const s = new InputState();
    s.keyDown("KeyA");
    s.toggleAutoRun();
    s.clear();
    expect(s.autoRun).toBe(true);
    expect(s.read(false, false)).toMatchObject({ forward: 1, strafe: 0, run: true });
  });

  it("flies forward on a broom like W, without diving", () => {
    const s = new InputState();
    s.toggleAutoRun();
    expect(s.read(true)).toMatchObject({ forward: 1, run: false });
  });
});

describe("the stall watch", () => {
  it("says stuck after a second of less than half a metre, never while moving", () => {
    const w = new StallWatch();
    expect(w.step(0, 0, 0, 0.016)).toBe(false);
    // Running at 6.5 m/s for two seconds: never stuck.
    let x = 0;
    for (let t = 0; t < 2; t += 0.016) {
      x += 6.5 * 0.016;
      expect(w.step(x, 0, 0, 0.016)).toBe(false);
    }
    // Pressed against a wall: a little sliding, then stuck after STALL_SECONDS.
    let stuck = 0;
    for (let t = 0; t < 2 && !stuck; t += 0.05) {
      x += 0.001;
      if (w.step(x, 0, 0, 0.05)) stuck = t;
    }
    expect(stuck).toBeGreaterThanOrEqual(STALL_SECONDS - 0.1);
    expect(stuck).toBeLessThan(STALL_SECONDS + 0.1);
    // Reset: starts afresh.
    w.reset();
    expect(w.step(x, 0, 0, 5)).toBe(false);
  });
});

describe("the movement chip", () => {
  it("shows auto run (and walking), a recent Z, or nothing", () => {
    expect(moveChipText(true, false, false)).toBe("Auto run · R or S to stop");
    expect(moveChipText(true, true, true)).toBe("Auto run, walking · R or S to stop");
    expect(moveChipText(false, true, true)).toBe("Walking · Z to run");
    expect(moveChipText(false, false, true)).toBe("Running · Z to walk");
    expect(moveChipText(false, false, false)).toBeNull();
  });
});
