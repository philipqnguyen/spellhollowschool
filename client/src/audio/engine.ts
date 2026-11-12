// The audio engine (docs/architecture.md "Sound and music"): plain WebAudio
// behind the `AudioApi` contract (types.ts). One AudioContext, made at the
// first user gesture after `init`; the buses master > music, effects,
// ambience, interface with the settings' gains (muted: master 0); the effects
// bus's shared reverb by place; 3D voices through equal-power panners at the
// listener (the camera); the voice caps and cooldowns; the CC0 file banks
// fetched by place; synthesized definitions rendered once into buffers; the
// ambience beds (layers crossfaded, sprinkles scattered); the emitters
// started and stopped by distance; the music engine handed `musicFor(place)`
// once the area has held for FADES.musicSettle; the tab hidden: fade out, then
// suspend; shown: resume.
//
// The decisions (cooldowns, the cap, the music's settling, strides) are pure
// in engine/logic.ts; the graph in engine/graph.ts; buffers in
// engine/sources.ts; one-shots and loops in engine/voices.ts.

import type { AudioBus, AudioSettings, Quality } from "../settings";
import { AUDIO_DEFAULTS } from "../settings";
import { soundEntry, SOUNDS, type AmbienceId, type SoundEntry, type SoundId, type StingerId } from "./catalogue";
import { createMusicEngine } from "./music";
import { ambienceFor, banksFor, CLOCK_BELL, FOOT_SOUND, musicFor, spaceFor, type AudioPlace, type FootSurface } from "./places";
import { FADES, HEAR, RATE_SPREAD, VOICE_CAPS, type VoiceCaps } from "./rules";
import { SOUND_DEFS } from "./sfx/defs";
import type { AudioApi, AudioStats, CreateMusicEngine, LoopHandle, MusicEngine, PlacedEmitter, PlayOpts, SoundDefs, Sprinkle, Vec3 } from "./types";
import { Graph, makePanner } from "./engine/graph";
import { admit, Cooldowns, MusicDirector, pickVariant } from "./engine/logic";
import { Sources, type Playable } from "./engine/sources";
import { LoopVoice, startOneShot, type Voice } from "./engine/voices";
import { assetUrl } from "../assets";

/** The engine's own additions to the contract (index.ts exports them on `audio`). */
export interface GameAudio extends AudioApi {
  /** Our entity id: sounds with `owner` = it never count against the cap. */
  setSelf(id: number): void;
  /** A settings slider moved: a short tick on that bus (music and ambience preview nothing). */
  preview(bus: AudioBus): void;
  /** The last events (plays, ambience and music changes), newest last: the dev check and `?audiolog`. */
  recent(): readonly string[];
  /** Every `play` asked so far (a click's own sound tells the HUD's generic click to stay quiet). */
  readonly playCount: number;
}

/** What the engine needs from the page (tests hand in fakes). */
export interface EngineDeps {
  makeContext: () => AudioContext;
  makeOffline: (channels: number, length: number, sampleRate: number) => OfflineAudioContext;
  createMusic: CreateMusicEngine;
  defs: SoundDefs;
  fetchBytes: (url: string) => Promise<ArrayBuffer>;
  /** Where the unlock gestures are listened for (window); null: never unlocks by itself. */
  gestures: EventTarget | null;
  /** The page still holds a recent gesture (navigator.userActivation): `init` may unlock at once. */
  activeGesture: () => boolean;
  /** The page's visibility (document); null: always shown. */
  visibility: (EventTarget & { readonly hidden: boolean }) | null;
  nowMs: () => number;
  random: () => number;
  /** A repeating timer; returns its stop. */
  every: (ms: number, fn: () => void) => () => void;
  after: (ms: number, fn: () => void) => void;
  /** The URL of a file under `public/` by its logical path (src/assets.ts `assetUrl`). */
  resolveUrl: (path: string) => string;
  /** Print each event to the console as well (`?audiolog`). */
  log: boolean;
}

function browserDeps(): EngineDeps {
  const hasWindow = typeof window !== "undefined";
  const search = hasWindow ? window.location.search : "";
  return {
    makeContext: () => new AudioContext({ latencyHint: "interactive" }),
    makeOffline: (channels, length, sampleRate) => new OfflineAudioContext(channels, length, sampleRate),
    createMusic: createMusicEngine,
    defs: SOUND_DEFS,
    fetchBytes: async (url) => {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.arrayBuffer();
    },
    gestures: hasWindow ? window : null,
    // The click that entered the game, if it was only a moment ago (loading the castle usually takes longer).
    activeGesture: () => (typeof navigator !== "undefined" && (navigator as { userActivation?: { isActive: boolean } }).userActivation?.isActive) === true,
    visibility: typeof document !== "undefined" ? document : null,
    nowMs: () => performance.now(),
    random: Math.random,
    every: (ms, fn) => {
      const t = setInterval(fn, ms);
      return () => clearInterval(t);
    },
    after: (ms, fn) => void setTimeout(fn, ms),
    resolveUrl: (path) => assetUrl(path),
    log: /[?&]audiolog\b/.test(search),
  };
}

const GESTURES = ["pointerdown", "keydown", "touchend"] as const;
/** The engine's housekeeping tick (s): loop seams, sprinkles, emitters, the music's settling. */
const TICK_S = 0.25;
/** A sound whose buffers arrive later than this after it was asked for is not played (s). */
const LATE_S = 0.25;
/** Groups whose one-shots get the random rate spread. */
const SPREAD: ReadonlySet<string> = new Set(["footsteps", "doors", "spells", "stairs", "duels", "potions", "sprinkles"]);
/** An emitter stops this far past its radius (m), so standing at the edge never flaps it. */
const EMITTER_HYSTERESIS = 3;
const RECENT_MAX = 200;

/** How far `e` carries and its panner's reference distance (m). */
function hearing(e: SoundEntry): { maxM: number; refM: number } {
  if (e.id === "world.clock_bell") return { maxM: CLOCK_BELL.radius, refM: 15 };
  if (e.group === "footsteps") return { maxM: HEAR.footstepsM, refM: HEAR.refM };
  if (e.group === "spells" || e.group === "duels") return { maxM: HEAR.spellsM, refM: HEAR.refM };
  // The Grand Staircase is seven floors tall: its warning and dock carry from the ground floor.
  if (e.group === "stairs") return { maxM: HEAR.stairsM, refM: HEAR.stairsRefM };
  return { maxM: HEAR.maxM, refM: HEAR.refM };
}

const dist3 = (a: Vec3, b: Vec3): number => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/** A loop handle that waits for the unlock (or its buffers) and keeps every setter meanwhile. */
class LoopProxy implements LoopHandle {
  private inner: LoopVoice | null = null;
  private gain: [number, number] | null = null;
  private rate: number | null = null;
  private tone: number | null = null;
  private pos: Vec3 | null = null;
  private stopped = false;
  /** Runs once on `stop` (the engine forgets a loop still waiting for the unlock). */
  onStop: (() => void) | null = null;

  constructor(
    readonly id: SoundId,
    readonly opts: PlayOpts,
  ) {}

  attach(v: LoopVoice): void {
    this.inner = v;
    if (this.gain) v.setGain(this.gain[0], this.gain[1]);
    if (this.rate !== null) v.setRate(this.rate);
    if (this.tone !== null) v.setTone(this.tone);
    if (this.pos) v.setPosition(this.pos);
  }

  setGain(gain: number, fadeS = 0.1): void {
    this.gain = [gain, fadeS];
    this.inner?.setGain(gain, fadeS);
  }
  setRate(rate: number): void {
    this.rate = rate;
    this.inner?.setRate(rate);
  }
  setTone(tone: number): void {
    this.tone = tone;
    this.inner?.setTone(tone);
  }
  setPosition(at: Vec3): void {
    this.pos = [at[0], at[1], at[2]];
    this.inner?.setPosition(at);
  }
  stop(fadeS?: number): void {
    if (this.stopped) return;
    this.stopped = true;
    this.inner?.stop(fadeS);
    this.onStop?.();
  }
  get playing(): boolean {
    return !this.stopped;
  }
  get position(): Vec3 | null {
    return this.pos;
  }
}

/** A running ambience bed: its looping layers and the sprinkles' next times. */
interface Bed {
  id: AmbienceId;
  layers: LoopVoice[];
  sprinkles: { s: Sprinkle; nextAt: number }[];
}

/** The game's engine. */
export function createAudio(overrides: Partial<EngineDeps> = {}): GameAudio {
  const deps: EngineDeps = { ...browserDeps(), ...overrides };
  let settings: AudioSettings = { ...AUDIO_DEFAULTS };
  let quality: Quality = "medium";
  let caps: VoiceCaps = VOICE_CAPS.medium;
  let armed = false;
  let ctx: AudioContext | null = null;
  let graph: Graph | null = null;
  let sources: Sources | null = null;
  let music: MusicEngine | null = null;
  let stopTicker: (() => void) | null = null;
  let hidden = false;
  let selfId = -1;
  let dropped = 0;
  let playCount = 0;
  let place: AudioPlace | null = null;
  let ambience: AmbienceId | null = null;
  let bed: Bed | null = null;
  let emitters: readonly PlacedEmitter[] = [];
  const emitting = new Map<string, LoopVoice>();
  const listener: { at: [number, number, number]; forward: [number, number, number] } = { at: [0, 0, 0], forward: [0, 0, 1] };
  const voices = new Set<Voice>();
  const loops = new Set<LoopVoice>();
  const pendingLoops = new Set<LoopProxy>();
  const lastVariant = new Map<string, number>();
  const cooldowns = new Cooldowns();
  const director = new MusicDirector(FADES.musicSettle);
  const recentLog: string[] = [];

  const nowS = () => deps.nowMs() / 1000;

  function note(line: string): void {
    const l = `${nowS().toFixed(2)} ${line}`;
    recentLog.push(l);
    if (recentLog.length > RECENT_MAX) recentLog.splice(0, recentLog.length - RECENT_MAX);
    if (deps.log) console.debug(`[audio] ${l}`);
  }

  // ---------------------------------------------------------------- unlock

  const onGesture = () => {
    if (!armed) return;
    if (!ctx) unlock();
    else if (ctx.state !== "running" && !hidden) void ctx.resume().catch(() => undefined);
    if (ctx && ctx.state === "running") disarmGestures();
  };

  function armGestures(): void {
    for (const g of GESTURES) deps.gestures?.addEventListener(g, onGesture, true);
  }

  function disarmGestures(): void {
    for (const g of GESTURES) deps.gestures?.removeEventListener(g, onGesture, true);
  }

  const onVisibility = () => {
    const vis = deps.visibility;
    if (!vis) return;
    hidden = vis.hidden;
    if (!ctx || !graph) return;
    const c = ctx;
    if (hidden) {
      graph.fadeTo(0, FADES.hide);
      deps.after(FADES.hide * 1000 + 30, () => {
        if (hidden && ctx === c && c.state === "running") void c.suspend().catch(() => undefined);
      });
    } else {
      void c.resume().catch(() => undefined);
      graph.fadeTo(1, FADES.show);
    }
    note(hidden ? "hidden" : "shown");
  };

  function unlock(): void {
    let c: AudioContext;
    try {
      c = deps.makeContext();
    } catch (e) {
      console.warn("audio: no AudioContext", e);
      disarmGestures();
      return;
    }
    ctx = c;
    if (c.state !== "running") void c.resume().catch(() => undefined);
    c.onstatechange = () => {
      if (ctx === c && c.state === "running" && !hidden) disarmGestures();
    };
    graph = new Graph(c, deps.random);
    graph.setSettings(settings);
    graph.setReverb(caps.reverb);
    graph.setListener(listener.at, listener.forward);
    if (hidden) graph.fade.gain.value = 0;
    sources = new Sources(c, deps.defs, { fetchBytes: deps.fetchBytes, makeOffline: deps.makeOffline, resolveUrl: deps.resolveUrl, warn: (m) => console.warn(m) });
    try {
      music = deps.createMusic(c, graph.buses.music, quality);
    } catch (e) {
      console.warn("audio: the music engine did not start", e);
      music = null;
    }
    note("unlocked");
    // The core bank's files and recipes, then whatever the place needs.
    void sources.loadBank("core");
    sources.prerender(SOUNDS.filter((s) => s.bank === "core" && s.source === "synth").map((s) => s.id));
    if (place) applyPlace(place);
    if (director.current !== undefined) handMusic(director.current, 0);
    for (const p of pendingLoops) attachLoop(p);
    pendingLoops.clear();
    stopTicker = deps.every(TICK_S * 1000, tick);
  }

  // ---------------------------------------------------------------- one-shots

  type Pool = Voice["pool"];

  /** The cap pool a one-shot counts in; null: never capped (flat sounds that are ours by nature). */
  function poolFor(e: SoundEntry, placed: boolean): Pool | null {
    if (e.group === "sprinkles") return "sprinkle";
    if (placed) return "world";
    // Flat: the interface's own are capped; the rest (crafting, mail, the class beats) are ours.
    return e.bus === "interface" && e.group !== "classes" ? "ui" : null;
  }

  function capOf(pool: Pool): number {
    switch (pool) {
      case "world":
        return caps.world;
      case "ui":
        return caps.ui;
      case "sprinkle":
        return caps.sprinkles;
      default:
        return Infinity;
    }
  }

  function busNode(e: SoundEntry, force?: "ambience"): AudioNode {
    const g = graph!;
    const bus = force ?? e.bus;
    return bus === "effects" ? g.effectsIn : g.buses[bus];
  }

  function play(id: SoundId, opts: PlayOpts = {}): void {
    playCount++;
    if (!ctx || !graph || !sources || hidden) return;
    const e = soundEntry(id);
    if (e.source === "score") {
      if (e.group === "stingers") stinger(id as StingerId);
      return;
    }
    if (e.loop) return; // loops start with `loop`
    const owner = opts.owner;
    const self = owner !== undefined && owner === selfId;
    const at = opts.at ?? null;
    const dist = at ? dist3(listener.at, at) : 0;
    const hear = hearing(e);
    if (at && dist > hear.maxM) return;
    if (!cooldowns.pass(id, owner, e.cooldownMs, deps.nowMs())) return;
    const ready = sources.get(id);
    if (ready) {
      start(e, ready, opts, at, dist, self, hear);
      return;
    }
    // Not ready: render or fetch it, and play it if it comes soon enough (a first use).
    const asked = ctx.currentTime;
    const c = ctx;
    void sources.load(id).then((p) => {
      if (!p || ctx !== c || c.currentTime - asked > LATE_S) return;
      start(e, p, opts, at, at ? dist3(listener.at, at) : 0, self, hear, asked);
    });
  }

  function start(e: SoundEntry, p: Playable, opts: PlayOpts, at: Vec3 | null, dist: number, self: boolean, hear: { maxM: number; refM: number }, askedAt?: number, force?: "ambience"): void {
    const c = ctx!;
    const pool = poolFor(e, at !== null);
    const exempt = self || pool === null;
    if (pool) {
      const pick = admit([...voices].filter((v) => v.pool === pool), { priority: e.priority, dist, self: exempt }, capOf(pool));
      if (pick === "drop") {
        dropped++;
        return;
      }
      if (pick !== "admit") {
        pick.stop(0.03);
        voices.delete(pick);
        dropped++;
      }
    }
    const n = p.buffers.length;
    const variant = opts.variant !== undefined ? Math.abs(Math.trunc(opts.variant)) % n : pickVariant(n, lastVariant.get(e.id), deps.random);
    lastVariant.set(e.id, variant);
    const spread = SPREAD.has(e.group) ? 1 + (deps.random() * 2 - 1) * RATE_SPREAD : 1;
    const rate = (opts.rate ?? 1) * p.rate * spread;
    const gain = Math.max(0, opts.gain ?? 1) * p.gain;
    const delay = Math.max(0, opts.delayS ?? 0);
    const when = askedAt !== undefined ? Math.max(c.currentTime, askedAt + delay) : c.currentTime + delay;
    const panner = at ? makePanner(c, at, hear.refM, HEAR.rolloff, hear.maxM) : null;
    const voice: Voice = { id: e.id, priority: e.priority, dist, self: exempt, pool: pool ?? "world", stop: () => undefined };
    voice.stop = startOneShot(c, p.buffers[variant]!, busNode(e, force), { gain, rate, when, panner }, () => voices.delete(voice));
    voices.add(voice);
    note(`play ${e.id}${at ? ` @${dist.toFixed(1)}m` : ""}${p.placeholder ? " (placeholder)" : ""}`);
  }

  // ---------------------------------------------------------------- loops

  function attachLoop(proxy: LoopProxy): void {
    if (!ctx || !graph || !sources || !proxy.playing) return;
    const e = soundEntry(proxy.id);
    const o = proxy.opts;
    const at = proxy.position ?? o.at ?? null;
    const hear = hearing(e);
    const v = makeLoop(e, o.gain ?? 1, o.rate ?? 1, at, hear.refM, hear.maxM, 0.05, o.owner !== undefined && o.owner === selfId);
    proxy.attach(v);
    note(`loop ${e.id}`);
  }

  function makeLoop(e: SoundEntry, gain: number, rate: number, at: Vec3 | null, refM: number, maxM: number, fadeInS: number, self: boolean, force?: "ambience"): LoopVoice {
    const c = ctx!;
    const panner = at ? makePanner(c, at, refM, HEAR.rolloff, maxM) : null;
    const v = new LoopVoice(c, busNode(e, force), e.id, e.priority, self, gain, rate, panner, fadeInS, (gone) => loops.delete(gone));
    loops.add(v);
    void sources!.load(e.id as SoundId).then((p) => {
      if (p) v.begin(p, pickVariant(p.buffers.length, undefined, deps.random));
    });
    return v;
  }

  // ---------------------------------------------------------------- place: ambience, banks, reverb, music

  function applyPlace(p: AudioPlace): void {
    if (!sources || !graph) return;
    for (const b of banksFor(p)) void sources.loadBank(b);
    graph.setSpace(spaceFor(p));
    const amb = ambienceFor(p);
    if (amb !== bed?.id) startBed(amb);
  }

  function startBed(id: AmbienceId): void {
    stopBed(FADES.ambience);
    if (!ctx || !sources) return;
    const def = sources.def(id);
    const b: Bed = { id, layers: [], sprinkles: [] };
    const fade = FADES.ambience;
    if (def?.kind === "bed") {
      for (const l of def.layers) b.layers.push(makeLoop(soundEntry(l.id), l.gain, 1, null, 1, 1, fade, true, "ambience"));
      const t = ctx.currentTime;
      for (const s of def.sprinkles) b.sprinkles.push({ s, nextAt: t + s.everyS[0] * (0.3 + 0.7 * deps.random()) });
    } else if (def) b.layers.push(makeLoop(soundEntry(id), 1, 1, null, 1, 1, fade, true, "ambience"));
    bed = b;
  }

  function stopBed(fadeS: number): void {
    if (!bed) return;
    for (const l of bed.layers) l.stop(fadeS);
    bed = null;
  }

  function handMusic(cue: ReturnType<typeof musicFor>, fadeS: number): void {
    note(`music ${cue ? `${cue.id} ${cue.night ? "night" : "day"}` : "none"}`);
    music?.setCue(cue, fadeS);
  }

  function followMusic(): void {
    if (!place) return;
    const first = director.current === undefined;
    const cue = director.update(musicFor(place), nowS());
    if (cue !== undefined && ctx) handMusic(cue, first ? 0 : FADES.musicOut);
  }

  // ---------------------------------------------------------------- the tick

  function tick(): void {
    if (!ctx || !graph || !sources) return;
    for (const l of loops) l.tick();
    followMusic();
    if (hidden) return;
    // Sprinkles round the listener.
    if (bed) {
      const t = ctx.currentTime;
      for (const sp of bed.sprinkles) {
        if (t < sp.nextAt) continue;
        const s = sp.s;
        sp.nextAt = t + s.everyS[0] + deps.random() * Math.max(0, s.everyS[1] - s.everyS[0]);
        sprinkle(s);
      }
    }
    // Emitters by distance, and only in their own areas (no sound through the walls).
    for (const em of emitters) {
      const d = dist3(listener.at, em.at);
      const here = !em.areas || (place !== null && place.layout === null && em.areas.includes(place.area));
      const on = emitting.get(em.key);
      if (!on && here && d < em.radius) {
        emitting.set(em.key, makeLoop(soundEntry(em.sound), 1, 1, em.at, Math.max(1, em.radius / 6), em.radius, 1.0, true));
        note(`emitter ${em.key} on`);
      } else if (on && (!here || d > em.radius + EMITTER_HYSTERESIS)) {
        on.stop(0.8);
        emitting.delete(em.key);
        note(`emitter ${em.key} off`);
      }
    }
  }

  function sprinkle(s: Sprinkle): void {
    if (!sources) return;
    const p = sources.get(s.id);
    if (!p) {
      void sources.load(s.id);
      return;
    }
    let at: Vec3 | null = null;
    const [lo, hi] = s.distanceM;
    if (hi > 0) {
      const r = lo + deps.random() * (hi - lo);
      const a = deps.random() * Math.PI * 2;
      const l = listener.at;
      at = [l[0] + Math.cos(a) * r, l[1] + 1 + deps.random() * 3, l[2] + Math.sin(a) * r];
    }
    start(soundEntry(s.id), p, { gain: s.gain }, at, at ? dist3(listener.at, at) : 0, false, { refM: Math.max(HEAR.refM, hi / 4), maxM: Math.max(hi + 5, HEAR.maxM) }, undefined, "ambience");
  }

  // ---------------------------------------------------------------- the API

  function stinger(id: StingerId): void {
    if (!ctx || !music || hidden) return;
    note(`stinger ${id}`);
    music.stinger(id);
  }

  function stopAll(): void {
    for (const v of voices) v.stop(0.02);
    voices.clear();
    for (const l of [...loops]) l.stop(0.05);
    loops.clear();
    emitting.clear();
    bed = null;
  }

  const api: GameAudio = {
    init(s, q) {
      settings = { ...s };
      quality = q;
      caps = VOICE_CAPS[q];
      if (armed) return;
      armed = true;
      armGestures();
      deps.visibility?.addEventListener("visibilitychange", onVisibility);
      hidden = deps.visibility?.hidden ?? false;
      if (deps.activeGesture()) onGesture();
    },
    get unlocked() {
      return ctx !== null;
    },
    play,
    playAt(id, at, opts) {
      play(id, { ...opts, at });
    },
    loop(id, opts = {}) {
      const proxy = new LoopProxy(id, opts);
      if (opts.at) proxy.setPosition(opts.at);
      if (ctx) attachLoop(proxy);
      else if (armed) {
        // Starts at the unlock; a loop stopped before then is forgotten.
        pendingLoops.add(proxy);
        proxy.onStop = () => pendingLoops.delete(proxy);
      }
      return proxy;
    },
    footstep(surface: FootSurface, at, run, owner) {
      play(FOOT_SOUND[surface], { at, owner, gain: run ? 1 : 0.7 });
    },
    setPlace(p) {
      const prevAmb = ambience;
      place = p;
      ambience = ambienceFor(p);
      if (ambience !== prevAmb) note(`ambience ${ambience}`);
      if (ctx) applyPlace(p);
      followMusic();
    },
    stinger,
    setListener(at, forward) {
      const l = listener.at;
      l[0] = at[0];
      l[1] = at[1];
      l[2] = at[2];
      const f = listener.forward;
      f[0] = forward[0];
      f[1] = forward[1];
      f[2] = forward[2];
      graph?.setListener(l, f);
    },
    setEmitters(list) {
      emitters = list;
      const keep = new Set(list.map((e) => e.key));
      for (const [k, v] of emitting) {
        if (keep.has(k)) continue;
        v.stop(0.5);
        emitting.delete(k);
      }
    },
    setSettings(s) {
      settings = { ...s };
      graph?.setSettings(settings);
    },
    setQuality(q) {
      quality = q;
      caps = VOICE_CAPS[q];
      graph?.setReverb(caps.reverb);
      music?.setQuality(q);
    },
    stats(): AudioStats {
      const state: AudioStats["state"] = !ctx ? "locked" : ctx.state === "running" ? "running" : ctx.state === "closed" ? "closed" : "suspended";
      const cue = director.current;
      return {
        state,
        voices: voices.size + loops.size,
        dropped,
        banks: sources?.banksLoaded ?? [],
        ambience,
        music: cue === undefined ? null : cue ? `${cue.id}${cue.night ? " (night)" : ""}` : "none",
        musicVoices: music?.stats().voices ?? 0,
      };
    },
    dispose() {
      disarmGestures();
      deps.visibility?.removeEventListener("visibilitychange", onVisibility);
      armed = false;
      stopTicker?.();
      stopTicker = null;
      stopAll();
      pendingLoops.clear();
      try {
        music?.dispose();
      } catch {
        // Nothing to keep.
      }
      music = null;
      sources?.dispose();
      sources = null;
      if (ctx && ctx.state !== "closed") void ctx.close().catch(() => undefined);
      ctx = null;
      graph = null;
      place = null;
      ambience = null;
      emitters = [];
      director.reset();
      cooldowns.clear();
      lastVariant.clear();
    },
    setSelf(id) {
      selfId = id;
    },
    preview(bus) {
      if (bus === "music" || bus === "ambience") return;
      if (!ctx || !graph || !sources) return;
      const e = soundEntry("ui.slider");
      const p = sources.get(e.id as SoundId);
      if (!p) {
        void sources.load(e.id as SoundId);
        return;
      }
      if (!cooldowns.pass(e.id, undefined, e.cooldownMs, deps.nowMs())) return;
      // Master and interface preview on the interface bus, effects on the effects bus.
      const out = bus === "effects" ? graph.buses.effects : graph.buses.interface;
      const voice: Voice = { id: e.id, priority: 3, dist: 0, self: true, pool: "ui", stop: () => undefined };
      voice.stop = startOneShot(ctx, p.buffers[0]!, out, { gain: p.gain, rate: 1, when: ctx.currentTime, panner: null }, () => voices.delete(voice));
      voices.add(voice);
    },
    recent: () => recentLog,
    get playCount() {
      return playCount;
    },
  };
  return api;
}

/** Plays nothing (tools, and anything that wants the API without sound): never unlocks. */
export function createSilentAudio(): GameAudio {
  return createAudio({ gestures: null, visibility: null, every: () => () => undefined, after: () => undefined });
}
