import { describe, expect, it } from "vitest";
import { AUDIO_BUSES, AUDIO_DEFAULTS, parseAudioSettings } from "../settings";
import { audio } from "./index";

describe("audio settings (shs.settings `audio`)", () => {
  it("defaults what is missing and clamps what is out of range", () => {
    expect(parseAudioSettings(undefined)).toEqual(AUDIO_DEFAULTS);
    expect(parseAudioSettings({ master: 2, music: -1, effects: "loud", ambience: 0.25, muted: true })).toEqual({
      master: 1,
      music: 0,
      effects: AUDIO_DEFAULTS.effects,
      ambience: 0.25,
      interface: AUDIO_DEFAULTS.interface,
      muted: true,
    });
    expect(parseAudioSettings({ muted: "yes" }).muted).toBe(false);
    for (const b of AUDIO_BUSES) expect(AUDIO_DEFAULTS[b]).toBeGreaterThan(0);
  });
});

describe("the audio object before the engine (and before any gesture)", () => {
  it("accepts every call and plays nothing", () => {
    audio.init({ ...AUDIO_DEFAULTS }, "medium");
    audio.play("ui.click");
    audio.playAt("spell.lucerna", [0, 1, 0], { owner: 1 });
    audio.footstep("stone", [0, 0, 0], false, 1);
    const loop = audio.loop("broom.wind");
    loop.setRate(1.2);
    loop.stop();
    audio.setPlace({ area: "castle", layout: null, classId: null, night: false, flying: false, duel: false });
    audio.stinger("stinger.welcome");
    expect(audio.unlocked).toBe(false);
    expect(audio.stats().state).toBe("locked");
  });
});
