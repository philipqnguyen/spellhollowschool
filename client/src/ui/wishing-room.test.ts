// The Wishing Room on the client (wishing-room-state.ts):
// the room choice card's choices, the door that shows for us while the
// server holds it open (its fades), and the discovery toast's words.

import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import layoutText from "../../../data/world/layout.json?raw";
import secretsText from "../../../data/world/secrets.json?raw";
import { initSync, SimWorld, type WishingRoomView } from "../wasm/sim_wasm";
import { DOOR_FADE_MS, discoveryText, PaceWhisper, paceWhisperText, WishingRoomDoor, wishingRoomName, roomChoices } from "./wishing-room-state";
import { interactLabel, roomRole } from "../game/zones";
import { locationText } from "./friends-state";
import { parseLocation } from "./friends-api";

let rules: WishingRoomView;

beforeAll(() => {
  initSync({ module: readFileSync(new URL("../wasm/sim_wasm_bg.wasm", import.meta.url)) });
  const sim = new SimWorld(layoutText);
  expect(sim.wishingRoom()).toBeUndefined();
  sim.setSecrets(secretsText);
  rules = sim.wishingRoom()!;
});

describe("the room choice card", () => {
  it("offers the two rooms by their names, in the rules' order", () => {
    const choices = roomChoices(rules);
    expect(choices.map((c) => c.room)).toEqual(["lost_property", "gathering"]);
    expect(choices.map((c) => c.name)).toEqual(["Lost Property Vault", "Gathering Room"]);
    expect(choices[0]!.blurb).toMatch(/once a day/);
    expect(choices[1]!.blurb).toContain(`up to ${rules.capacity} people`);
    expect(rules.capacity).toBe(20);
  });

  it("still offers both rooms before the rules are known", () => {
    expect(roomChoices(null).map((c) => c.room)).toEqual(["lost_property", "gathering"]);
    expect(wishingRoomName("gathering")).toBe("Gathering Room");
    expect(wishingRoomName("lost_property", rules)).toBe("Lost Property Vault");
  });
});

describe("the door in the blank wall", () => {
  it("opens for its time, fades in, and fades out when it times out", () => {
    const d = new WishingRoomDoor();
    expect(d.isOpen(0)).toBe(false);
    expect(d.alpha(0)).toBe(0);
    expect(d.set(61_000, 1000)).toBe("opened");
    expect(d.isOpen(1000)).toBe(true);
    expect(d.secondsLeft(1000)).toBe(60);
    expect(d.alpha(1000)).toBe(0);
    expect(d.alpha(1000 + DOOR_FADE_MS / 2)).toBeCloseTo(0.5, 5);
    expect(d.alpha(30_000)).toBe(1);
    // A repeat of the same time changes nothing.
    expect(d.set(61_000, 2000)).toBeNull();
    // Timed out: gone for use at once, fading away.
    expect(d.isOpen(61_000)).toBe(false);
    expect(d.secondsLeft(61_000)).toBe(0);
    expect(d.alpha(61_000 + DOOR_FADE_MS / 2)).toBeCloseTo(0.5, 5);
    expect(d.alpha(61_000 + DOOR_FADE_MS)).toBe(0);
    // The server's late `{ 0 }` does not restart the fade.
    expect(d.set(0, 61_100)).toBeNull();
    expect(d.alpha(61_000 + DOOR_FADE_MS / 2)).toBeCloseTo(0.5, 5);
  });

  it("goes when the server takes it away (we walked in), and a past time never opens it", () => {
    const d = new WishingRoomDoor();
    d.set(61_000, 1000);
    expect(d.set(0, 5000)).toBe("closed");
    expect(d.isOpen(5000)).toBe(false);
    expect(d.alpha(5000)).toBe(1);
    expect(d.alpha(5000 + DOOR_FADE_MS)).toBe(0);
    expect(d.set(4000, 5000)).toBeNull();
    expect(d.isOpen(5000)).toBe(false);
    d.clear();
    expect(d.alpha(5000)).toBe(0);
  });
});

describe("the room in the game's words", () => {
  const wishingRoom = (ownerName: string) => ({ kind: "wishing_room" as const, ownerName, room: "gathering" as const, layout: "wishing_room_gathering" });

  it("knows the opener as the room's owner", () => {
    expect(roomRole(wishingRoom("Basil"), "Basil")).toBe("owner");
    expect(roomRole(wishingRoom("Basil"), "Alina")).toBe("guest");
    expect(roomRole({ kind: "dorm", ownerName: "Basil", house: "skytalon", layout: "dorm_skytalon" }, "Basil")).toBe("owner");
    expect(roomRole({ kind: "castle" }, "Basil")).toBeNull();
  });

  it("labels the door and the way out", () => {
    const door = { id: "wishing_room_door", kind: "wishing_room_door" as const, pos: [-3, 63, 8.5] as [number, number, number], yaw: Math.PI, radius: 2 };
    expect(interactLabel(door, { kind: "castle" }, "Basil")).toBe("Enter the Wishing Room");
    const exit = { id: "wishing_room_exit", kind: "instance_exit" as const, pos: [0, 0, 0] as [number, number, number], yaw: 0, radius: 2 };
    expect(interactLabel(exit, wishingRoom("Basil"), "Basil")).toBe("Leave the room");
    expect(interactLabel(exit, wishingRoom("Basil"), "Alina")).toBe("Leave Basil's room");
  });

  it("says where a friend in a Wishing Room is", () => {
    const f = (ownerName: string) => ({ characterId: 1, name: "Alina", house: "skytalon" as const, online: true, location: { kind: "wishing_room" as const, ownerName }, roomOpen: false, since: "" });
    expect(locationText(f("Alina"), "Me")).toBe("In the Wishing Room");
    expect(locationText(f("Me"), "Me")).toBe("In your Wishing Room");
    expect(locationText(f("Basil"), "Me")).toBe("In Basil's Wishing Room");
    expect(parseLocation({ kind: "wishing_room", owner_name: "Basil" })).toEqual({ kind: "wishing_room", ownerName: "Basil" });
  });

  it("toasts a discovery with its reward", () => {
    expect(discoveryText("The Weaver's Back Stair", 100, true, "Weave a Hooded Wool Cloak", "Skytalon")).toBe(
      "A hidden passage: The Weaver's Back Stair! +100 XP, +1 point for Skytalon, a new recipe: Weave a Hooded Wool Cloak. It is on your map now (M).",
    );
    expect(discoveryText("The Sculptor's Gap", 100, false, null, "Skytalon")).toContain("no House point");
  });

  it("counts passes past the blank wall with the server's rule, for its whispers (review fix)", () => {
    const p = new PaceWhisper(rules.passes, rules.window_s * 1000);
    // Into the west mark, between the marks, into the east mark: one pass.
    expect(p.step(0, 0)).toBe(0);
    expect(p.step(0, 500)).toBe(0);
    expect(p.step(null, 2000)).toBe(0);
    expect(p.step(1, 4000)).toBe(1);
    expect(paceWhisperText(1, rules.passes)).toMatch(/blank wall/);
    expect(p.step(0, 8000)).toBe(2);
    expect(paceWhisperText(2, rules.passes)).toMatch(/again/);
    // The third pass is the server's (the door): no whisper, and the count starts over.
    expect(p.step(1, 12000)).toBe(3);
    expect(paceWhisperText(3, rules.passes)).toBeNull();
    expect(p.step(0, 13000)).toBe(0);
    // Passes older than the window drop out.
    const slow = new PaceWhisper(3, 60_000);
    slow.step(0, 0);
    slow.step(1, 10_000);
    slow.step(0, 20_000);
    expect(slow.step(1, 75_000)).toBe(1);
  });
});
