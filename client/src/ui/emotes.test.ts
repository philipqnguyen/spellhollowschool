import { describe, expect, it } from "vitest";
import { emoteCommand, LocalEmote } from "./emotes";

const DURATIONS = { waveMs: 2500, bowMs: 2000 };

describe("emote commands", () => {
  it("recognises /wave, /bow and /sit alone on the line", () => {
    expect(emoteCommand("/wave")).toBe("wave");
    expect(emoteCommand("  /BOW ")).toBe("bow");
    expect(emoteCommand("/sit")).toBe("sit");
    expect(emoteCommand("/sit down please")).toBeNull();
    expect(emoteCommand("wave")).toBeNull();
    expect(emoteCommand("/w Anne hello")).toBeNull();
  });
});

describe("LocalEmote", () => {
  it("starts only while standing", () => {
    const e = new LocalEmote(DURATIONS);
    expect(e.start("wave", 0, true)).toBe(false);
    expect(e.anim(0, false)).toBeNull();
    expect(e.start("wave", 0, false)).toBe(true);
    expect(e.anim(100, false)).toBe("wave");
  });

  it("times out wave and bow like the server", () => {
    const e = new LocalEmote(DURATIONS);
    e.start("bow", 1000, false);
    expect(e.anim(2999, false)).toBe("bow");
    expect(e.anim(3000, false)).toBeNull();
    expect(e.active).toBeNull();
  });

  it("sits until the character moves", () => {
    const e = new LocalEmote(DURATIONS);
    e.start("sit", 0, false);
    expect(e.anim(60_000, false)).toBe("sit");
    expect(e.anim(60_001, true)).toBeNull();
    expect(e.anim(60_002, false)).toBeNull();
  });

  it("a new emote replaces the current one", () => {
    const e = new LocalEmote(DURATIONS);
    e.start("sit", 0, false);
    e.start("wave", 10, false);
    expect(e.anim(20, false)).toBe("wave");
    expect(e.anim(2600, false)).toBeNull();
  });
});
