// Day and night over the castle and grounds, from the game clock: the hour
// picks the light from the art side's lighting data (toon/lighting.ts,
// data/art/lighting.json: the sun or moon with its direction, colour and
// strength, the sky/ground fill, the sky gradient and the fog), and
// `daylight` (0 at night, 1 in full day, ramping over a game hour at dawn
// and dusk; sim-core `ClockRules::daylight`) turns on the stars and the
// torch pools (toon/torches.ts) for the night.
//
// The toon look (docs/art/style-guide.md, "Toon rendering") reads night by
// hue, not by black: cool moonlight on the lit bands, a violet-blue fill,
// warm torchlight in the corridors. No new lights and no per-frame work: the
// look is re-applied about once a second, and only when the hour moved.
//
// The night outside. The far castle's lit windows glow
// from dusk to dawn (castle/hlod.ts `setHlodNight`), and the grass, the
// trees, the backdrop and the lake take their night tint by 1 - daylight
// (castle/mist.ts `setGroundsNight`; the mist over the lake follows it).
//
// The Great Hall's enchanted ceiling (castle kit M3, docs/art/castle-kit.md;
// castle/sky.ts) takes the same hour: its sky colours, the sun's or
// moon's colour, the fog and the night's stars, set here with the rest.
//
// Only the castle is lit this way; rooms (dorms, classrooms, arenas) light
// themselves (zones-scene.ts `applyIndoor`). zones-scene.ts restores its
// captured outdoor look whenever the castle is shown again, so the game
// calls `apply` right after (`zoneShown`).

import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { DirectionalLight } from "@babylonjs/core/Lights/directionalLight";
import type { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import type { Scene } from "@babylonjs/core/scene";
import { castleLight, sceneLights, type CastleLight, type Rgb } from "../toon/lighting";
import { skyDomeOf, type SkyColours } from "../toon/sky";
import { torchesOf, torchLevel } from "../toon/torches";
import { castleSkyOf } from "../castle/sky";
import { setToonKeyNight } from "../toon/material";
import { setHlodNight } from "../castle/hlod";
import { setGroundsNight } from "../castle/mist";

export type { Rgb };

/** What the clock means for the castle beyond the light data: stars and torches. */
export interface NightLook {
  /** 0 by day, 1 at night: how many stars show. */
  stars: number;
  /** Torch brightness (toon/torches.ts): off in full day, full at night. */
  torches: number;
  /** The far castle's lit windows (castle/hlod.ts): off by day, coming on through the dusk. */
  windows: number;
  /** The grounds' night tint and the lake's mist (castle/mist.ts): 1 - daylight. */
  grounds: number;
}

/** Stars and torches for `daylight` (clamped to 0..1). Pure, unit tested. */
export function nightLook(daylight: number): NightLook {
  const d = Math.min(1, Math.max(0, Number.isFinite(daylight) ? daylight : 1));
  const torches = torchLevel(d);
  return { stars: Math.max(0, 1 - d * 1.6), torches, windows: Math.min(1, Math.max(0, (0.6 - d) / 0.5)), grounds: 1 - d };
}

/** The sky dome's colours for a castle light (the data gives zenith, horizon and fog). */
export function skyColours(l: CastleLight, stars: number): SkyColours {
  return {
    zenith: l.zenith,
    horizon: l.horizon,
    ground: [(l.fog[0] + l.hemiGround[0]) / 2, (l.fog[1] + l.hemiGround[1]) / 2, (l.fog[2] + l.hemiGround[2]) / 2],
    glow: l.sunColour,
    stars,
  };
}

/** The castle at noon (scene.ts starts with it, before the clock is known). */
export const NOON: CastleLight = castleLight(13);
export const DAY_SKY: SkyColours = skyColours(NOON, 0);

/** Re-apply when the hour moved by more than this (about a game minute), or on demand. */
const STEP_HOURS = 0.02;

export class DayNight {
  private shownHour = Number.NaN;
  private shownDaylight = -1;
  private readonly dir = new Vector3();

  constructor(private readonly scene: Scene) {}

  /** The daylight last applied (-1 before the first). */
  get daylight(): number {
    return this.shownDaylight;
  }

  /** Lights the castle for game hour `hour` (0-24); `force` after zones-scene restored its look. */
  apply(hour: number, daylight: number, force = false): void {
    if (!force && Math.abs(hour - this.shownHour) < STEP_HOURS) return;
    this.shownHour = hour;
    this.shownDaylight = daylight;
    const l = castleLight(hour);
    const s = sceneLights(l);
    setToonKeyNight(l.moon);
    const night = nightLook(daylight);
    const hemi = this.scene.getLightByName("sky") as HemisphericLight | null;
    if (hemi) {
      hemi.intensity = s.hemiIntensity;
      hemi.diffuse = colour(s.hemiSky);
      hemi.groundColor = colour(s.hemiGround);
    }
    const sun = this.scene.getLightByName("sun") as DirectionalLight | null;
    if (sun) {
      sun.intensity = s.sunIntensity;
      sun.diffuse = colour(s.sunColour);
      sun.direction.set(l.sunDirection[0], l.sunDirection[1], l.sunDirection[2]);
    }
    this.scene.fogColor = colour(l.fog);
    this.scene.clearColor = new Color4(l.fog[0], l.fog[1], l.fog[2], 1);
    const sky = skyDomeOf(this.scene);
    if (sky) {
      sky.setLook(skyColours(l, night.stars));
      this.dir.set(-l.sunDirection[0], -l.sunDirection[1], -l.sunDirection[2]);
      sky.setSunDirection(this.dir);
    }
    torchesOf(this.scene)?.setLevel(night.torches);
    setHlodNight(night.windows);
    setGroundsNight(night.grounds);
    castleSkyOf(this.scene).setFromLight(l, night.stars);
  }
}

/** Below this `daylight` a room open to the sky is lit as the castle's night (`applySky`). */
export const OPEN_SKY_NIGHT_BELOW = 0.5;

/**
 * The sky dome for game hour `hour` in a room open to the sky (Astronomy's tower top, the paddock). By day the
 * room keeps its own fill and lamps; by night (the moonlit Astronomy platform) it takes the castle's
 * night fill (low, violet-blue) and the toon key's moonlight in place of the room's bright indoor fill, so the
 * stone reads moonlit; the room's own lights (its cool moon, the door lamp) stay on top.
 */
export function applySky(scene: Scene, hour: number, daylight: number): void {
  const l = castleLight(hour);
  if (daylight < OPEN_SKY_NIGHT_BELOW) {
    const s = sceneLights(l);
    const hemi = scene.getLightByName("sky") as HemisphericLight | null;
    if (hemi) {
      hemi.intensity = s.hemiIntensity;
      hemi.diffuse = colour(s.hemiSky);
      hemi.groundColor = colour(s.hemiGround);
    }
    setToonKeyNight(l.moon);
  }
  const sky = skyDomeOf(scene);
  if (!sky) return;
  sky.setLook(skyColours(l, nightLook(daylight).stars));
  sky.setSunDirection(new Vector3(-l.sunDirection[0], -l.sunDirection[1], -l.sunDirection[2]));
}

function colour(c: Rgb): Color3 {
  return new Color3(c[0], c[1], c[2]);
}
