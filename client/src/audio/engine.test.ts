// The audio engine's state (docs/architecture.md "Sound and music"): the
// unlock, volumes and mute, the voice cap, cooldowns, ambience and music by
// area and time, the hidden tab, emitters and loops; against a fake WebAudio
// (engine/fake-webaudio.ts) and the pure decisions (engine/logic.ts).

import { describe, expect, it } from "vitest";
import { AUDIO_DEFAULTS, type AudioSettings } from "../settings";
import type { MusicId, SoundId, StingerId } from "./catalogue";
import { createAudio, type EngineDeps } from "./engine";
import { FakeContext, FakeOffline, type FakeNode } from "./engine/fake-webaudio";
import { admit, Cooldowns, MusicDirector, pickVariant, Strides } from "./engine/logic";
import type { AudioPlace } from "./places";
import { FADES, STRIDE_M, VOICE_CAPS } from "./rules";
import { OUTPUT_MAKEUP, safetyCurve } from "./engine/graph";
import type { MusicCue, SoundDefs } from "./types";

// ---------------------------------------------------------------- the pure parts

describe("cooldowns", () => {
  it("drops a repeat of the same id and owner sooner than the cooldown", () => {
    const c = new Cooldowns();
    expect(c.pass("foot.stone", 7, 120, 1000)).toBe(true);
    expect(c.pass("foot.stone", 7, 120, 1100)).toBe(false);
    expect(c.pass("foot.stone", 8, 120, 1100)).toBe(true);
    expect(c.pass("foot.stone", 7, 120, 1121)).toBe(true);
    expect(c.pass("ui.click", undefined, 0, 1121)).toBe(true);
    expect(c.pass("ui.click", undefined, 0, 1121)).toBe(true);
  });
});

describe("the voice cap's choice", () => {
  const v = (priority: number, dist: number, self = false) => ({ priority, dist, self });
  it("admits under the cap and always admits our own", () => {
    expect(admit([v(0, 5)], v(0, 5), 2)).toBe("admit");
    expect(admit([v(0, 5), v(0, 6)], v(0, 1, true), 2)).toBe("admit");
  });
  it("does not count our own voices", () => {
    expect(admit([v(0, 5, true), v(0, 5, true), v(0, 5)], v(0, 5), 2)).toBe("admit");
  });
  it("steals the lowest priority, then the farthest", () => {
    const a = v(2, 3);
    const b = v(0, 4);
    const c = v(0, 9);
    expect(admit([a, b, c], v(1, 20), 3)).toBe(c);
    expect(admit([a, b, c], v(0, 5), 3)).toBe(c);
  });
  it("drops the newcomer when it is the weakest", () => {
    expect(admit([v(1, 3), v(1, 4)], v(0, 1), 2)).toBe("drop");
    expect(admit([v(1, 3), v(1, 4)], v(1, 4), 2)).toBe("drop");
  });
});

describe("the music follows the place", () => {
  const cue = (id: MusicId, night = false): MusicCue => ({ id, night });
  it("hands on a new piece only after it has held for the settle time", () => {
    const d = new MusicDirector(3);
    expect(d.update(cue("music.castle"), 0)).toBeUndefined();
    expect(d.update(cue("music.castle"), 2.9)).toBeUndefined();
    expect(d.update(cue("music.castle"), 3)).toEqual(cue("music.castle"));
    expect(d.update(cue("music.castle"), 4)).toBeUndefined();
  });
  it("ignores a short visit (a doorway) and restarts the wait on each change", () => {
    const d = new MusicDirector(3);
    d.update(cue("music.castle"), 0);
    d.update(cue("music.castle"), 3);
    expect(d.update(cue("music.great_hall"), 10)).toBeUndefined();
    expect(d.update(cue("music.castle"), 11)).toBeUndefined();
    expect(d.update(cue("music.great_hall"), 12)).toBeUndefined();
    expect(d.update(cue("music.great_hall"), 14.9)).toBeUndefined();
    expect(d.update(cue("music.great_hall"), 15)).toEqual(cue("music.great_hall"));
  });
  it("passes day and night at once for the same piece", () => {
    const d = new MusicDirector(3);
    d.update(cue("music.grounds"), 0);
    d.update(cue("music.grounds"), 3);
    expect(d.update(cue("music.grounds", true), 3.25)).toEqual(cue("music.grounds", true));
  });
  it("settles on silence too", () => {
    const d = new MusicDirector(3);
    d.update(cue("music.castle"), 0);
    d.update(cue("music.castle"), 3);
    d.update(null, 5);
    expect(d.update(null, 8)).toBeNull();
  });
});

describe("strides", () => {
  it("puts a foot down every stride of ground covered, the first half a stride in", () => {
    const s = new Strides();
    let steps = 0;
    s.begin();
    s.step(1, 0, 0, 0);
    for (let i = 1; i <= 100; i++) {
      s.begin();
      if (s.step(1, i * 0.05, 0, 0.75)) steps++;
    }
    // 5 m: half a stride, then one each 0.75 m.
    expect(steps).toBe(Math.floor((5 - 0.375) / 0.75) + 1);
  });
  it("takes no step standing or after a teleport", () => {
    const s = new Strides();
    s.step(1, 0, 0, 0.75);
    expect(s.step(1, 50, 0, 0.75)).toBe(false);
    expect(s.step(1, 50, 0, 0)).toBe(false);
  });
  it("keeps time with the walk and run clips (two steps a loop at sim-core's speeds)", () => {
    // The walk loops in 25 frames at 30 fps at 3.0 m/s, the run in 20 at 6.5 m/s.
    expect(STRIDE_M.walk).toBeCloseTo((3.0 * 25) / 30 / 2, 2);
    expect(STRIDE_M.run).toBeCloseTo((6.5 * 20) / 30 / 2, 2);
  });
});

describe("the output safety", () => {
  it("passes the mix untouched below its knee and never reaches full scale", () => {
    const c = safetyCurve();
    // The curve's input is the signal halved: index i is the signal 2 * (2i / (n - 1) - 1).
    const at = (x: number) => c[Math.round(((x / 2 + 1) / 2) * (c.length - 1))]!;
    expect(at(0.5)).toBeCloseTo(0.5, 3);
    expect(at(-0.7)).toBeCloseTo(-0.7, 3);
    for (let i = 1; i < c.length; i++) expect(c[i]!).toBeGreaterThanOrEqual(c[i - 1]!);
    expect(Math.max(...c.map(Math.abs))).toBeLessThan(1);
    expect(OUTPUT_MAKEUP).toBeGreaterThan(1);
  });
});

describe("variants", () => {
  it("never repeats the last one when there is a choice", () => {
    let r = 0;
    const random = () => (r = (r + 0.37) % 1);
    let last: number | undefined;
    for (let i = 0; i < 50; i++) {
      const v = pickVariant(3, last, random);
      expect(v).not.toBe(last);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(3);
      last = v;
    }
    expect(pickVariant(1, 0, random)).toBe(0);
  });
});

// ---------------------------------------------------------------- the engine against a fake WebAudio

const flush = () => new Promise((r) => setTimeout(r, 0));

const place = (p: Partial<AudioPlace>): AudioPlace => ({ area: "castle", layout: null, classId: null, night: false, flying: false, duel: false, ...p });

/** A synth recipe that draws nothing (the fake renders silence of its length). */
const synth = (seconds = 0.2, variants = 1) => ({ kind: "synth" as const, seconds, variants, draw: () => undefined });

const DEFS: SoundDefs = {
  "ui.click": synth(0.05),
  "ui.slider": synth(0.05),
  "spell.torpeo": synth(0.4, 3),
  "foot.stone": synth(0.15, 4),
  "duel.hit": synth(0.3),
  "broom.wind": { kind: "synth", seconds: 2, seamless: true, draw: () => undefined },
  "loop.fountain": synth(3),
  "amb.castle": { kind: "bed", layers: [{ id: "amb.castle", gain: 0.5 }], sprinkles: [{ id: "sprinkle.drip", everyS: [1, 2], gain: 0.5, distanceM: [3, 8] }] },
  "sprinkle.drip": synth(0.1),
  "door.heavy": { kind: "alias", of: "spell.torpeo", rate: 0.5 },
};

class FakeVisibility extends EventTarget {
  hidden = false;
}

function rig(defs: SoundDefs = DEFS) {
  const contexts: FakeContext[] = [];
  const gestures = new EventTarget();
  const visibility = new FakeVisibility();
  const cues: (MusicCue | null)[] = [];
  const stingers: StingerId[] = [];
  let clock = 0;
  let ticker: (() => void) | null = null;
  const later: (() => void)[] = [];
  const deps: Partial<EngineDeps> = {
    makeContext: () => {
      const c = new FakeContext();
      contexts.push(c);
      return c as unknown as AudioContext;
    },
    makeOffline: (ch, len, sr) => new FakeOffline(ch, len, sr) as unknown as OfflineAudioContext,
    createMusic: () => ({
      setCue: (c) => void cues.push(c),
      stinger: (id) => void stingers.push(id),
      setQuality: () => undefined,
      stats: () => ({ cue: null, section: null, voices: 0, resting: false }),
      dispose: () => undefined,
    }),
    defs,
    fetchBytes: () => Promise.resolve(new ArrayBuffer(8)),
    gestures,
    activeGesture: () => false,
    visibility,
    nowMs: () => clock * 1000,
    random: Math.random,
    every: (_ms, fn) => {
      ticker = fn;
      return () => (ticker = null);
    },
    after: (_ms, fn) => void later.push(fn),
    resolveUrl: (path) => `/${path}`,
    log: false,
  };
  const audio = createAudio(deps);
  return {
    audio,
    contexts,
    cues,
    stingers,
    visibility,
    get ctx() {
      return contexts[0]!;
    },
    gesture: (type = "pointerdown") => gestures.dispatchEvent(new Event(type)),
    /** Moves both clocks (the page's and the context's) and runs one engine tick. */
    advance(seconds: number) {
      clock += seconds;
      for (const c of contexts) c.advance(seconds);
      ticker?.();
    },
    runLater: () => later.splice(0).forEach((f) => f()),
  };
}

/** The graph's gains: fade (to the destination), the makeup into the safety, master, then the four buses in order. */
function gains(ctx: FakeContext) {
  type G = FakeNode & { gain: { value: number } };
  const fade = ctx.nodes.find((n) => n.outs.has(ctx.destination)) as G;
  const safety = ctx.nodes.find((n) => n.outs.has(fade))!;
  const trim = ctx.nodes.find((n) => n.outs.has(safety)) as G;
  const master = ctx.nodes.find((n) => n.outs.has(trim)) as G;
  const [music, effects, ambience, iface] = ctx.nodes.filter((n) => n.kind === "gain" && n.outs.has(master)) as G[];
  return { fade, master, music: music!, effects: effects!, ambience: ambience!, interface: iface! };
}

describe("the unlock", () => {
  it("makes no AudioContext and plays nothing before the first gesture", () => {
    const r = rig();
    r.audio.init({ ...AUDIO_DEFAULTS }, "medium");
    r.audio.play("ui.click");
    r.audio.playAt("spell.torpeo", [1, 0, 1]);
    r.audio.stinger("stinger.welcome");
    expect(r.contexts).toHaveLength(0);
    expect(r.audio.unlocked).toBe(false);
    expect(r.audio.stats().state).toBe("locked");
    expect(r.stingers).toHaveLength(0);
  });

  it("unlocks on the first click or key, once", () => {
    const r = rig();
    r.audio.init({ ...AUDIO_DEFAULTS }, "medium");
    r.gesture("keydown");
    r.gesture("pointerdown");
    expect(r.contexts).toHaveLength(1);
    expect(r.audio.unlocked).toBe(true);
    expect(r.audio.stats().state).toBe("running");
  });

  it("unlocks at init when the page still holds the gesture that started the game", () => {
    const r = rig();
    const a = createAudio({ makeContext: () => new FakeContext() as unknown as AudioContext, createMusic: () => ({ setCue: () => undefined, stinger: () => undefined, setQuality: () => undefined, stats: () => ({ cue: null, section: null, voices: 0, resting: false }), dispose: () => undefined }), gestures: null, visibility: null, activeGesture: () => true, every: () => () => undefined, defs: {} });
    a.init({ ...AUDIO_DEFAULTS }, "medium");
    expect(a.unlocked).toBe(true);
    a.dispose();
    expect(r.contexts).toHaveLength(0);
  });

  it("does not listen before init nor after dispose", () => {
    const r = rig();
    r.gesture();
    expect(r.contexts).toHaveLength(0);
    r.audio.init({ ...AUDIO_DEFAULTS }, "medium");
    r.gesture();
    r.audio.dispose();
    expect(r.ctx.state).toBe("closed");
    r.gesture();
    expect(r.contexts).toHaveLength(1);
    // A new init arms the unlock again (sign out, sign in).
    r.audio.init({ ...AUDIO_DEFAULTS }, "medium");
    r.gesture();
    expect(r.contexts).toHaveLength(2);
  });

  it("remembers the place and the settled music and starts them at the unlock", () => {
    const r = rig();
    r.audio.init({ ...AUDIO_DEFAULTS }, "medium");
    r.audio.setPlace(place({ area: "castle" }));
    r.advance(4);
    r.audio.setPlace(place({ area: "castle" }));
    expect(r.audio.stats().ambience).toBe("amb.castle");
    r.gesture();
    expect(r.cues).toEqual([{ id: "music.castle", night: false }]);
  });
});

describe("volumes and mute", () => {
  it("sets master and each bus from the settings, master 0 when muted", () => {
    const r = rig();
    const s: AudioSettings = { master: 0.7, music: 0.3, effects: 0.9, ambience: 0.4, interface: 0.2, muted: false };
    r.audio.init(s, "medium");
    r.gesture();
    let g = gains(r.ctx);
    expect(g.master.gain.value).toBeCloseTo(0.7);
    expect(g.music.gain.value).toBeCloseTo(0.3);
    expect(g.effects.gain.value).toBeCloseTo(0.9);
    expect(g.ambience.gain.value).toBeCloseTo(0.4);
    expect(g.interface.gain.value).toBeCloseTo(0.2);
    r.audio.setSettings({ ...s, muted: true });
    g = gains(r.ctx);
    expect(g.master.gain.value).toBe(0);
    expect(g.music.gain.value).toBeCloseTo(0.3);
    r.audio.setSettings({ ...s, music: 0.6 });
    expect(g.master.gain.value).toBeCloseTo(0.7);
    expect(g.music.gain.value).toBeCloseTo(0.6);
  });

  it("applies settings changed before the unlock", () => {
    const r = rig();
    r.audio.init({ ...AUDIO_DEFAULTS }, "medium");
    r.audio.setSettings({ ...AUDIO_DEFAULTS, master: 0.25 });
    r.gesture();
    expect(gains(r.ctx).master.gain.value).toBeCloseTo(0.25);
  });
});

/** Unlocked, with every test id rendered (a first play renders it). */
async function warm(quality: "low" | "medium" | "high" = "medium") {
  const r = rig();
  r.audio.init({ ...AUDIO_DEFAULTS }, quality);
  r.audio.setSelf(1);
  r.gesture();
  for (const id of Object.keys(DEFS) as SoundId[]) r.audio.play(id, { owner: 1 });
  await flush();
  await flush();
  r.advance(5);
  return r;
}

describe("one-shots", () => {
  it("plays a placed sound through a panner, and drops it beyond its hearing", async () => {
    const r = await warm();
    const before = r.ctx.sounding.length;
    r.audio.setListener([0, 0, 0], [0, 0, 1]);
    r.audio.playAt("spell.torpeo", [3, 0, 4], { owner: 50 });
    expect(r.ctx.sounding.length).toBe(before + 1);
    r.audio.playAt("spell.torpeo", [300, 0, 0], { owner: 51 });
    expect(r.ctx.sounding.length).toBe(before + 1);
    expect(r.ctx.nodes.some((n) => n.kind === "panner")).toBe(true);
  });

  it("disconnects a finished voice", async () => {
    const r = await warm();
    r.audio.play("ui.click");
    const src = r.ctx.sounding.at(-1)!;
    expect(r.audio.stats().voices).toBeGreaterThan(0);
    r.advance(1);
    expect(src.ended).toBe(true);
    expect(src.outs.size).toBe(0);
    expect(r.audio.stats().voices).toBe(0);
  });

  it("drops rapid repeats by the same owner (cooldown)", async () => {
    const r = await warm();
    const before = r.ctx.sources.length;
    r.audio.play("ui.click");
    r.audio.play("ui.click");
    expect(r.ctx.sources.length).toBe(before + 1);
  });

  it("plays an alias's target at its rate", async () => {
    const r = await warm();
    r.audio.playAt("door.heavy", [1, 0, 0]);
    const src = r.ctx.sources.at(-1)!;
    expect(src.playbackRate.value).toBeGreaterThan(0.4);
    expect(src.playbackRate.value).toBeLessThan(0.6);
  });

  it("plays a quiet placeholder for an id without a definition", async () => {
    const r = await warm();
    r.audio.play("ui.toast");
    await flush();
    expect(r.audio.recent().some((l) => l.includes("play ui.toast (placeholder)"))).toBe(true);
  });

  it("schedules a delayed one-shot on the context's clock", async () => {
    const r = await warm();
    r.audio.play("ui.click", { delayS: 0.5 });
    expect(r.ctx.sources.at(-1)!.startAt).toBeCloseTo(r.ctx.currentTime + 0.5);
  });
});

describe("the voice cap", () => {
  it("keeps at most the quality's world voices from others, and never drops our own", async () => {
    const r = await warm("medium");
    const cap = VOICE_CAPS.medium.world;
    for (let i = 0; i < cap + 6; i++) r.audio.playAt("spell.torpeo", [1 + i * 0.5, 0, 0], { owner: 100 + i });
    expect(r.audio.stats().voices).toBe(cap);
    expect(r.audio.stats().dropped).toBe(6);
    for (let i = 0; i < 3; i++) r.audio.playAt("duel.hit", [2, 0, 0], { owner: 1, variant: i });
    expect(r.audio.stats().voices).toBe(cap + 3);
  });

  it("lets a higher priority sound take the place of a footstep", async () => {
    const r = await warm("low");
    const cap = VOICE_CAPS.low.world;
    for (let i = 0; i < cap; i++) r.audio.footstep("stone", [1 + i, 0, 0], false, 200 + i);
    expect(r.audio.stats().voices).toBe(cap);
    r.audio.playAt("duel.hit", [5, 0, 0], { owner: 300 });
    expect(r.audio.stats().voices).toBe(cap);
    expect(r.audio.stats().dropped).toBe(1);
    expect(r.audio.recent().at(-1)).toContain("play duel.hit");
  });
});

describe("ambience and music by area and time", () => {
  it("picks the bed by area, day and night, and the sky on a broom", async () => {
    const r = await warm();
    r.audio.setPlace(place({ area: "castle" }));
    expect(r.audio.stats().ambience).toBe("amb.castle");
    r.audio.setPlace(place({ area: "grounds" }));
    expect(r.audio.stats().ambience).toBe("amb.grounds_day");
    r.audio.setPlace(place({ area: "grounds", night: true }));
    expect(r.audio.stats().ambience).toBe("amb.grounds_night");
    r.audio.setPlace(place({ area: "lake", night: true, flying: true }));
    expect(r.audio.stats().ambience).toBe("amb.sky");
    r.audio.setPlace(place({ area: "class", layout: "class_greenhouse", classId: "herbology" }));
    expect(r.audio.stats().ambience).toBe("amb.greenhouse");
  });

  it("crossfades the bed's layers and scatters its sprinkles", async () => {
    const r = await warm();
    r.audio.setPlace(place({ area: "castle" }));
    await flush();
    r.advance(0.25);
    const layers = r.audio.stats().voices;
    expect(layers).toBeGreaterThan(0);
    for (let i = 0; i < 12; i++) r.advance(0.25);
    expect(r.audio.recent().some((l) => l.includes("play sprinkle.drip"))).toBe(true);
    r.audio.setPlace(place({ area: "grounds" }));
    expect(r.audio.recent().some((l) => l.includes("ambience amb.grounds_day"))).toBe(true);
  });

  it("changes the music after the area has held for the settle time, with the fade", async () => {
    const r = await warm();
    r.audio.setPlace(place({ area: "great_hall" }));
    r.advance(FADES.musicSettle);
    r.audio.setPlace(place({ area: "great_hall" }));
    expect(r.cues.at(-1)).toEqual({ id: "music.great_hall", night: false });
    const n = r.cues.length;
    // A doorway's second in the corridor changes nothing.
    r.audio.setPlace(place({ area: "castle" }));
    r.advance(1);
    r.audio.setPlace(place({ area: "great_hall" }));
    r.advance(5);
    expect(r.cues.length).toBe(n);
    // The courtyard, held: the folk piece; then night, at once.
    r.audio.setPlace(place({ area: "courtyard" }));
    r.advance(FADES.musicSettle);
    expect(r.cues.at(-1)).toEqual({ id: "music.grounds", night: false });
    r.audio.setPlace(place({ area: "courtyard", night: true }));
    expect(r.cues.at(-1)).toEqual({ id: "music.grounds", night: true });
    expect(r.audio.stats().music).toBe("music.grounds (night)");
    // A duel's music comes first; a hidden passage is silent.
    r.audio.setPlace(place({ area: "arena", duel: true }));
    r.advance(FADES.musicSettle);
    expect(r.cues.at(-1)).toEqual({ id: "music.duel", night: false });
    r.audio.setPlace(place({ area: "passage" }));
    r.advance(FADES.musicSettle);
    expect(r.cues.at(-1)).toBeNull();
  });

  it("passes stingers to the music engine once unlocked", async () => {
    const r = await warm();
    r.audio.stinger("stinger.level_up");
    r.audio.play("stinger.discovery");
    expect(r.stingers).toEqual(["stinger.level_up", "stinger.discovery"]);
  });
});

describe("the hidden tab", () => {
  it("fades out and suspends, drops one-shots while hidden, and comes back", async () => {
    const r = await warm();
    r.visibility.hidden = true;
    r.visibility.dispatchEvent(new Event("visibilitychange"));
    expect(gains(r.ctx).fade.gain.value).toBe(0);
    r.runLater();
    expect(r.ctx.state).toBe("suspended");
    const n = r.ctx.sources.length;
    r.audio.play("ui.click", { variant: 0 });
    expect(r.ctx.sources.length).toBe(n);
    r.visibility.hidden = false;
    r.visibility.dispatchEvent(new Event("visibilitychange"));
    await flush();
    expect(r.ctx.state).toBe("running");
    expect(gains(r.ctx).fade.gain.value).toBe(1);
  });
});

describe("loops and emitters", () => {
  it("starts a loop asked for before the unlock at the unlock, and a stopped one never", async () => {
    const r = rig();
    r.audio.init({ ...AUDIO_DEFAULTS }, "medium");
    const wind = r.audio.loop("broom.wind");
    wind.setGain(0.4);
    const gone = r.audio.loop("broom.wind");
    gone.stop();
    r.gesture();
    await flush();
    await flush();
    r.advance(0.25);
    expect(wind.playing).toBe(true);
    expect(r.ctx.sounding.filter((s) => s.loop)).toHaveLength(1);
    wind.stop(0.1);
    expect(wind.playing).toBe(false);
  });

  it("starts an emitter as the listener comes near and stops it as it goes", async () => {
    const r = await warm();
    r.audio.setEmitters([{ key: "courtyard_fountain", sound: "loop.fountain", at: [0, 0, 10], radius: 18 }]);
    r.audio.setListener([0, 0, 50], [0, 0, 1]);
    r.advance(0.25);
    expect(r.audio.recent().some((l) => l.includes("emitter courtyard_fountain on"))).toBe(false);
    r.audio.setListener([0, 0, 20], [0, 0, 1]);
    r.advance(0.25);
    expect(r.audio.recent().at(-1)).toContain("emitter courtyard_fountain on");
    r.audio.setListener([0, 0, 40], [0, 0, 1]);
    r.advance(0.25);
    expect(r.audio.recent().at(-1)).toContain("emitter courtyard_fountain off");
  });

  it("hears an emitter only in its own areas: not through the walls", async () => {
    const r = await warm();
    r.audio.setEmitters([{ key: "courtyard_fountain", sound: "loop.fountain", at: [0, 0, 10], radius: 18, areas: ["courtyard"] }]);
    r.audio.setPlace(place({ area: "dungeons" }));
    r.audio.setListener([0, 0, 15], [0, 0, 1]);
    r.advance(0.25);
    expect(r.audio.recent().some((l) => l.includes("emitter courtyard_fountain on"))).toBe(false);
    r.audio.setPlace(place({ area: "courtyard" }));
    r.advance(0.25);
    expect(r.audio.recent().at(-1)).toContain("emitter courtyard_fountain on");
    r.audio.setPlace(place({ area: "castle" }));
    r.advance(0.25);
    expect(r.audio.recent().at(-1)).toContain("emitter courtyard_fountain off");
  });
});

describe("quality", () => {
  it("cuts the reverb's send on Low", async () => {
    const r = await warm("medium");
    r.audio.setPlace(place({ area: "great_hall" }));
    const convs = r.ctx.nodes.filter((n) => n.kind === "convolver");
    const send = r.ctx.nodes.find((n) => n.kind === "gain" && [...n.outs].some((o) => o.kind === "convolver"));
    expect(convs).toHaveLength(2);
    expect(send).toBeDefined();
    r.audio.setQuality("low");
    expect(send!.outs.size).toBe(0);
    r.audio.setQuality("high");
    expect(send!.outs.size).toBe(2);
  });
});
