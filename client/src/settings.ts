// Player settings (graphics and, since the sound work, audio), remembered per browser in localStorage.

import { parseChatter, type ChatterMode } from "./game/npc/chatter";

export type Quality = "low" | "medium" | "high";
export const VIEW_LIMITS = [20, 50, 100] as const;
export type ViewLimit = (typeof VIEW_LIMITS)[number];

/**
 * Sound (docs/architecture.md "Sound and music"): a volume per bus, 0..1, and
 * a mute over all of it. The effective gain of a sound is master x its bus.
 */
export interface AudioSettings {
  master: number;
  music: number;
  /** The world: footsteps, doors, spells, duels, brooms, potions. */
  effects: number;
  /** Ambience beds, sprinkles, emitters, the clock bell. */
  ambience: number;
  /** Interface sounds and the class beats. */
  interface: number;
  muted: boolean;
}

export const AUDIO_BUSES = ["master", "music", "effects", "ambience", "interface"] as const;
export type AudioBus = (typeof AUDIO_BUSES)[number];

export const AUDIO_DEFAULTS: Readonly<AudioSettings> = { master: 0.8, music: 0.5, effects: 0.8, ambience: 0.6, interface: 0.6, muted: false };

export interface Settings {
  /** How many other characters the server sends and the client draws. */
  viewLimit: ViewLimit;
  quality: Quality;
  /** Performance overlay (F3). */
  showPerf: boolean;
  audio: AudioSettings;
  /** NPCs: Portrait chatter, On / Quiet (interact only) / Off (docs/architecture.md "NPCs"). */
  chatter: ChatterMode;
}

const KEY = "shs.settings";
const DEFAULTS: Settings = { viewLimit: 50, quality: "medium", showPerf: false, audio: { ...AUDIO_DEFAULTS }, chatter: "on" };

/** A saved `audio` object, each volume clamped to 0..1; anything missing or malformed takes its default. */
export function parseAudioSettings(raw: unknown): AudioSettings {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const vol = (k: AudioBus): number => {
    const v = o[k];
    return typeof v === "number" && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : AUDIO_DEFAULTS[k];
  };
  return { master: vol("master"), music: vol("music"), effects: vol("effects"), ambience: vol("ambience"), interface: vol("interface"), muted: o.muted === true };
}

export function loadSettings(): Settings {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}") as Partial<Settings>;
    return {
      viewLimit: VIEW_LIMITS.includes(raw.viewLimit as ViewLimit) ? (raw.viewLimit as ViewLimit) : DEFAULTS.viewLimit,
      quality: raw.quality === "low" || raw.quality === "medium" || raw.quality === "high" ? raw.quality : DEFAULTS.quality,
      showPerf: raw.showPerf === true,
      audio: parseAudioSettings(raw.audio),
      chatter: parseChatter(raw.chatter),
    };
  } catch {
    return { ...DEFAULTS, audio: { ...AUDIO_DEFAULTS } };
  }
}

export function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    // Storage unavailable: settings last for this page only.
  }
}

/** Rendering parameters for each quality level. */
export interface QualityProfile {
  /**
   * Render resolution relative to CSS pixels, the full (top) scale: Medium and
   * High may step below it on a slow GPU (game/resolution-guard.ts).
   */
  resolution: number;
  /**
   * MSAA samples on the scene's render target when the screen passes run
   * (toon/post.ts; its depth is resolved for the passes). 1: none (Low draws
   * straight to the canvas, which multisamples itself).
   */
  sceneSamples: number;
  /** The dynamic-resolution guard (Medium, High): steps the scale down on slow frames. */
  dynamicResolution: boolean;
  shadows: boolean;
  /** Linear fog end and camera far plane (m). */
  viewDistance: number;
  /**
   * The characters' inverted-hull outlines (game/toon/outline.ts, one more
   * draw per near character; docs/art/character-look.md section 4). The castle has
   * no outline at any quality.
   */
  outlines: boolean;
  /** Subtle bloom on highlights (game/toon/post.ts). */
  bloom: boolean;
  /** Screen-space ambient occlusion (game/toon/post.ts; castle D1): High only, never Medium or Low. */
  ssao: boolean;
}

/**
 * Character look pass (docs/art/character-look.md section 5): Medium and High
 * render at the screen's own resolution (up to DPR 2) with 4x MSAA on the
 * scene target (the character outlines are anti-aliased by it too); Low
 * renders at 0.75 of the display's pixels but never above one per CSS pixel
 * (1.0 on a Retina screen, 0.75 at DPR 1, as before).
 */
export function qualityProfile(q: Quality, devicePixelRatio: number): QualityProfile {
  const dpr = devicePixelRatio > 0 ? devicePixelRatio : 1;
  switch (q) {
    case "low":
      // Weak GPUs: the toon shading stays, the screen passes go.
      return { resolution: Math.min(1, 0.75 * dpr), sceneSamples: 1, dynamicResolution: false, shadows: false, viewDistance: 140, outlines: false, bloom: false, ssao: false };
    case "medium":
      return { resolution: Math.min(dpr, 2), sceneSamples: 4, dynamicResolution: true, shadows: false, viewDistance: 220, outlines: true, bloom: true, ssao: false };
    case "high":
      return { resolution: Math.min(dpr, 2), sceneSamples: 4, dynamicResolution: true, shadows: true, viewDistance: 320, outlines: true, bloom: true, ssao: true };
  }
}

/** One line per quality level for the settings menu. */
export const QUALITY_HINTS: Record<Quality, string> = {
  low: "Toon shading only: no character outlines or bloom, lower resolution.",
  medium: "Character outlines and bloom, full resolution, smoothed edges.",
  high: "As Medium, plus ambient occlusion and sun shadows.",
};
