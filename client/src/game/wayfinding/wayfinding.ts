// The wayfinding trail in the running game (the "Monstra Viam"
// charm): a soft line of glowing wisps on the floor a few metres ahead of
// the player, leading to one key place (data/world/key_places.json: the
// player's dorm and common room, the Owl Exchange, the Great Hall, the
// Entrance Hall, the Dueling Club, each class door; never a find spot or
// anything meant to be discovered), and a HUD chip "Trail to Potions
// Classroom, 120 m" with an X.
//
// The path is sim-core's walkable-surface graph (`Wayfinder`, built once for
// the player's house, so other houses' common rooms are walls), walking
// round the furniture the layout does not model (the load test's `spread`
// keep-outs: the Great Hall's tables, the library's stacks, ...). It is
// client only: the server never hears of a trail.
//
// It ends with the chip's X, Esc, choosing another destination, or
// arriving; leaving the trail (another way round, a stair to the wrong
// floor) plans it again, at most twice a second. Outside the castle (a
// dorm, a classroom) the trail waits, hidden, and picks up on return.
//
// The moving staircases. The trail plans in the staircase state
// with every swinging flight already where it swings to, and plans again
// when the state changes or a flight its way crosses starts to swing
// (`onStairs`, castle/stairs-state.ts `TrailStairs`). Its look is unchanged.
//
// Drawing: one `VfxLayer` (thin instances of one quad, one draw call while
// the trail shows) of soft orbs in a palette colour, bright enough to read
// on stone without an outline; a pulse runs along it toward the destination.
// (solid cobalt discs read grey; this glow reads.)

import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import keyPlacesText from "../../../../data/world/key_places.json?raw";
import keepOutData from "../../../../data/world/keep_out.json";
import { audio } from "../../audio";
import { PALETTE, hexToRgb01 } from "../../content/catalog";
import { Wayfinder, type House, type KeyPlaceView } from "../../wasm/sim_wasm";
import { Shape } from "../vfx/shapes";
import { VfxLayer } from "../vfx/layer";
import { ARRIVE_M, arrived, joinVia, makePath, project, remainingM, STRAY_DY, strayed, wisps, type TrailPath, type Wisp } from "./trail-path";
import { TrailStairs, type StairEvents } from "../castle/stairs-state";

/** The trail's colour: a palette colour (data/items/palette.json), cool against the warm stone. */
export const TRAIL_COLOUR_ID = "sky";
/** Wisp size (m). */
const WISP_SIZE = 0.42;
/** Planning again after straying waits at least this long between tries (s). */
const REPLAN_S = 0.5;
/** The pulse runs toward the destination at this many wisps a second. */
const PULSE_RATE = 7;
const MAX_WISPS = 32;

export interface TrailStatus {
  place: KeyPlaceView;
  /** Metres left (NaN while no way is known). */
  remainingM: number;
  /** No way from here (off the house's ground, or out of the castle). */
  lost: boolean;
}

export interface WayfindingOptions {
  scene: Scene;
  /** The castle layout file's text and the player's house. */
  layoutText: string;
  house: House;
  /** The trail ended by arriving. */
  onArrive: (place: KeyPlaceView) => void;
}

/** The furniture footprints the trail walks round (data/world/keep_out.json, shared with the load-test bots). */
function keepOutJson(): string {
  return JSON.stringify((keepOutData as { keep_out?: unknown[] }).keep_out ?? []);
}

function paletteRgb(id: string): [number, number, number] {
  const c = PALETTE.colours.find((p) => p.id === id) ?? PALETTE.colours[0]!;
  return hexToRgb01(c.hex);
}

export class Wayfinding {
  private readonly finder: Wayfinder;
  readonly places: readonly KeyPlaceView[];
  private readonly layer: VfxLayer;
  private readonly rgb = paletteRgb(TRAIL_COLOUR_ID);
  private readonly wispBuf: Wisp[] = [];
  private place: KeyPlaceView | null = null;
  /** A point the trail leads through first (the tour's front doors), until passed. */
  private via: readonly [number, number, number] | null = null;
  private path: TrailPath | null = null;
  private s = 0;
  private remaining = NaN;
  private replanIn = 0;
  private time = 0;
  /** Whether the trail is drawn (castle only). */
  private shown = false;
  /** The staircase state the trail plans in, and the flights its way crosses. */
  private readonly stairs = new TrailStairs();
  /** The staircases asked for a new plan (the next frame makes it). */
  private replanDue = false;
  /** We ride a swinging flight: the trail waits, hidden, until the switch. */
  private riding = false;

  constructor(private readonly opts: WayfindingOptions) {
    this.finder = new Wayfinder(opts.layoutText, opts.house, keyPlacesText, keepOutJson());
    this.places = this.finder.places();
    this.layer = new VfxLayer(opts.scene, "trail", MAX_WISPS);
  }

  /** The destination, if a trail is on. */
  get destination(): KeyPlaceView | null {
    return this.place;
  }

  /** The key place at class `classId`'s door. */
  classPlace(classId: string): KeyPlaceView | undefined {
    return this.places.find((p) => p.group === "class" && p.class === classId);
  }

  /**
   * Starts a trail to `placeId` from `feet` (replacing any other), by way of
   * `via` while that point is still on the way (the tour's first leg in
   * through the front doors). Returns the place, or null when it is unknown
   * (another house's room).
   */
  start(placeId: string, feet: readonly [number, number, number], via: readonly [number, number, number] | null = null): KeyPlaceView | null {
    const place = this.places.find((p) => p.id === placeId);
    if (!place) return null;
    if (this.place?.id !== place.id) audio.play("spell.monstra_viam");
    this.place = place;
    this.via = via;
    this.path = null;
    this.replanIn = 0;
    this.plan(feet);
    return place;
  }

  /** Ends the trail (the X, Esc, arrival). Returns whether one was on. */
  stop(): boolean {
    const was = this.place !== null;
    this.place = null;
    this.via = null;
    this.path = null;
    this.remaining = NaN;
    this.layer.begin();
    return was;
  }

  /** The chip's state, or null without a trail. */
  status(): TrailStatus | null {
    if (!this.place) return null;
    return { place: this.place, remainingM: this.remaining, lost: !this.path };
  }

  private plan(feet: readonly [number, number, number]): void {
    const place = this.place;
    if (!place) return;
    let flat: ArrayLike<number> | undefined = this.finder.pathTo(place.id, feet[0], feet[1], feet[2]);
    const via = this.via;
    if (via) {
      // Through the point while it is still ahead of us (joinVia), then direct for good.
      const joined = joinVia(flat, this.finder.path(feet[0], feet[1], feet[2], via[0], via[1], via[2]), this.finder.pathTo(place.id, via[0], via[1], via[2]));
      if (joined) flat = joined;
      else this.via = null;
    }
    this.path = flat && flat.length >= 3 ? makePath(flat) : null;
    // Which moving flights this way crosses (a swing of one plans again).
    this.stairs.flights = this.path ? this.finder.pathFlights(place.id, feet[0], feet[1], feet[2]) : 0;
    this.s = 0;
    this.remaining = this.path ? this.path.length : NaN;
  }

  /**
   * Once a frame before `update`: the staircases' events. The
   * trail's graph follows the plan state (each swinging flight already where
   * it swings to); the trail plans again when the walk state changes or a
   * flight its way crosses begins to swing. While we ride a swinging flight
   * the trail waits, hidden (its wisps would hang over the well).
   */
  onStairs(e: Pick<StairEvents, "changed" | "swingStarted" | "plan">, riding: boolean): void {
    const replan = this.stairs.onStairs(e);
    if (this.finder.stairState !== e.plan) {
      try {
        this.finder.setStairState(e.plan);
      } catch (err) {
        console.error("The Monstra Viam trail cannot plan in staircase state", e.plan, err);
      }
    }
    if (replan && this.place) this.replanDue = true;
    this.riding = riding;
  }

  /** The flights the current trail crosses (bits), for tests and the F3 overlay. */
  get crossesFlights(): number {
    return this.stairs.flights;
  }

  /**
   * Per frame. `feet` is null outside the castle (the trail waits, hidden).
   * Returns the place when the player just arrived (the trail is over).
   */
  update(dt: number, feet: readonly [number, number, number] | null, camera: Camera): KeyPlaceView | null {
    this.time += dt;
    this.layer.begin();
    if (!this.place || !feet || this.riding) {
      this.finish(camera);
      return null;
    }
    this.replanIn -= dt;
    if (this.replanDue) {
      this.replanDue = false;
      this.replanIn = REPLAN_S;
      this.plan(feet);
    }
    // Past the way point: later plans go direct.
    const via = this.via;
    if (via && Math.hypot(via[0] - feet[0], via[2] - feet[2]) <= ARRIVE_M && Math.abs(via[1] - feet[1]) <= STRAY_DY) this.via = null;
    let path = this.path;
    if (path) {
      const p = project(path, feet[0], feet[1], feet[2]);
      if (strayed(p)) {
        if (this.replanIn <= 0) {
          this.replanIn = REPLAN_S;
          this.plan(feet);
          path = this.path;
        }
      } else {
        this.s = p.s;
        this.remaining = remainingM(path, p);
      }
    } else if (this.replanIn <= 0) {
      // No way from where we stood: try again as the player moves.
      this.replanIn = REPLAN_S;
      this.plan(feet);
      path = this.path;
    }
    // Already there (a trail started at its door: no path to plan) counts too.
    const at = this.place.pos;
    const there = !path && Math.hypot(at[0] - feet[0], at[2] - feet[2]) <= ARRIVE_M && Math.abs(at[1] - feet[1]) <= STRAY_DY;
    if (there || (path && arrived(path, feet[0], feet[1], feet[2]))) {
      const place = this.place;
      this.stop();
      this.finish(camera);
      this.opts.onArrive(place);
      return place;
    }
    if (path) this.draw(path);
    this.finish(camera);
    return null;
  }

  private draw(path: TrailPath): void {
    const n = wisps(path, this.s, this.wispBuf, MAX_WISPS);
    const [r, g, b] = this.rgb;
    const head = this.time * PULSE_RATE;
    for (let i = 0; i < n; i++) {
      const w = this.wispBuf[i]!;
      // A soft pulse running toward the destination: brightest every 6 wisps.
      const phase = (((w.k - head) % 6) + 6) % 6;
      const pulse = 0.62 + 0.38 * Math.max(0, 1 - phase / 2.2);
      this.layer.push(Shape.orb, w.x, w.y, w.z, WISP_SIZE * (0.85 + 0.25 * pulse), r, g, b, w.alpha * pulse);
    }
  }

  private finish(camera: Camera): void {
    this.layer.end(camera);
    this.shown = this.layer.size > 0;
  }

  /** For the F3 overlay. */
  perfLine(): [string, string] {
    const p = this.place;
    return ["trail", p ? `${p.name}: ${this.path ? `${this.path.cum.length} points, ${this.remaining.toFixed(0)} m` : "no way"}, ${this.shown ? "1 draw" : "hidden"}` : "off"];
  }

  dispose(): void {
    this.layer.dispose();
    this.finder.free();
  }
}
