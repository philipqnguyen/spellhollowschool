// How the game's characters look and act, in one place for game.ts: the
// renderer, our own appearance (Welcome, AppearanceChanged, wardrobe
// replies), remote appearances, emotes (HUD bar, /wave /bow /sit, our own
// emote state) and the wardrobe window.

import type { Scene } from "@babylonjs/core/scene";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { encodeEmote, type Anim, type Appearance, type Emote, type House, type ServerMessage } from "../../wasm/sim_wasm";
import { EmoteBar, emoteCommand, LocalEmote, type EmoteDurations } from "../../ui/emotes";
import { WardrobeWindow } from "../../ui/wardrobe";
import { trackSpeed, type Remote, type RemotePlayers } from "../remotes";
import type { Quality } from "../../settings";
import { CharacterRenderer, sharedLibrary, type CharacterFx, type CharacterShadowHost, type CharacterStats, type CharacterStyle } from "./index";
import { seatPlace, type SeatPlace } from "../castle/seats";
import { BENCH_TUCK, SeatTurns, type SeatedFacing } from "../bench-flip";
import { RecolouredLooks } from "../effects/state";

/**
 * How potion effects change a character this frame (game/effects): a look
 * to draw instead (Mimic Draught; `version` keys it, negative so it never
 * collides with an appearance version), and the renderer's scale and tint.
 * Null draws the character as it is.
 */
export interface DrawLook {
  appearance: Appearance | null;
  version: number;
  fx: CharacterFx | null;
  /** Spells L20: Tinctura's colour (palette index) for the body garment's dye, or null (effects/state.ts `RecolouredLooks`). */
  recolour: number | null;
}

export type LookSource = (id: number) => DrawLook | null;

/**
 * Whom each character looks at (game/look-at.ts `LookDirector`): an entity
 * id (-1: nobody), or for `pointOf` a point instead (our own spell aim).
 */
export interface LookTargets {
  targetOf(id: number): number;
  pointOf(id: number, out: { x: number; y: number; z: number }): boolean;
}

export interface CastOptions {
  scene: Scene;
  /** Registers character meshes as shadow casters (and removes them again). */
  shadows: { add: (m: Mesh) => void; remove: (m: Mesh) => void };
  /** Character shadow maps of the nearest characters (Medium, High), and receivers. */
  characterShadows?: CharacterShadowHost | null;
  /** The starting quality level (LOD rules). */
  quality?: Quality;
  /** sim-core walk and run speeds (m/s). */
  speeds: { walk: number; run: number };
  emotes: EmoteDurations;
  /** Where the emote bar and the wardrobe window go, and the HUD bar for the wardrobe button. */
  hudRoot: HTMLElement;
  hudActions: HTMLElement;
  send: (frame: Uint8Array) => void;
  /** A line for the chat log. */
  notify: (text: string) => void;
  /** Our house: the wardrobe prices dyes against the house colours an item was granted in. */
  house?: House;
  /** Asked as an emote is chosen: true if it stopped auto run (we stand from this frame, so the emote may start). */
  stopAutoRun?: () => boolean;
}

/**
 * Seats a sitting character is drawn on (castle kit M3, castle/seats.ts):
 * `find` writes the drawn place of a sitter at (x, y, z), or returns false;
 * `facing` (the sitter's yaw) picks the side of a two-sided seat.
 */
export interface SeatLookup {
  find(x: number, y: number, z: number, out: SeatPlace, facing?: number): boolean;
}

/**
 * A figure drawn that is no entity (NPCs: a talking portrait's subject):
 * its own id (negative, never an entity's), look, pose, style and where it
 * looks (null: nowhere). Drawn after the entities, with the same renderer.
 */
export interface Figure {
  id: number;
  version: number;
  appearance: Appearance;
  x: number;
  y: number;
  z: number;
  yaw: number;
  anim: Anim;
  fx: CharacterFx | null;
  style: CharacterStyle | null;
  look: { x: number; y: number; z: number } | null;
}

/** Our own character as the frame loop knows it. */
export interface SelfPose {
  id: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  anim: Anim;
}

export class Cast {
  private readonly renderer: CharacterRenderer;
  private readonly emote: LocalEmote;
  private readonly bar: EmoteBar;
  readonly wardrobe: WardrobeWindow;
  private self: { appearance: Appearance; version: number } | null = null;
  /** Our pose last frame, for the walk / run playback speed. */
  private readonly selfMotion = { pose: { x: 0, y: 0, z: 0, yaw: 0, anim: "idle" as Anim }, speed: 0, lastX: 0, lastZ: 0, posed: false };
  private standing = true;
  /** Potion effects on characters (game/effects), once the game sets them. */
  private looks: LookSource | null = null;
  /** Our own wand gesture plays until then (`performance.now()` ms). */
  private castUntil = 0;
  /** Seats in the castle (visual only), and the place one gave this frame (reused). */
  private seats: SeatLookup | null = null;
  /** Floor seats (rugs, hearth steps): no facing of their own (`yaw` NaN keeps the sitter's). */
  private floorSeats: SeatLookup | null = null;
  private readonly seat = seatPlace();
  /** The seat's fit as drawn (the drop shortened mid-turn: the knees tuck up), reused. */
  private readonly drawnFit = seatPlace();
  /** Sitters turning round on two-sided benches (bench-flip.ts), eased. */
  private readonly turns = new SeatTurns();
  /** Where we sat last frame (bench-flip.ts reads it), or null. */
  private selfSeat: SeatedFacing | null = null;
  private readonly selfSeatSlot: SeatedFacing = { yaw: 0, twoSided: false };
  /** Characters look at their target (game/look-at.ts), once the game sets it. */
  private lookTargets: LookTargets | null = null;
  private readonly lookPoint = { x: 0, y: 0, z: 0 };
  /** Spells L20: Tinctura's looks, one per recoloured character. */
  private readonly recoloured = new RecolouredLooks();
  /** NPCs: how an entity is drawn apart from its look (a ghost, a background student), once the game sets it. */
  private styles: ((id: number) => CharacterStyle | null) | null = null;

  constructor(private readonly opts: CastOptions) {
    this.renderer = new CharacterRenderer(opts.scene, {
      library: sharedLibrary(opts.speeds),
      onMesh: (m, added) => (added ? opts.shadows.add(m) : opts.shadows.remove(m)),
      shadows: opts.characterShadows ?? null,
    });
    if (opts.quality) this.renderer.setQuality(opts.quality);
    this.emote = new LocalEmote(opts.emotes);
    this.bar = new EmoteBar({ onEmote: (e) => this.play(e) });
    this.wardrobe = new WardrobeWindow({
      appearance: () => this.self?.appearance ?? null,
      onAppearance: (a, version) => this.setSelf(a, version),
      notify: opts.notify,
      house: opts.house ?? null,
    });
    opts.hudActions.append(this.wardrobe.button);
    opts.hudRoot.append(this.bar.root, this.wardrobe.root);
  }

  /** The chat box's local commands: /wave, /bow and /sit. True if `line` was one. */
  command(line: string): boolean {
    const emote = emoteCommand(line);
    if (emote) this.play(emote);
    return emote !== null;
  }

  /**
   * `Welcome`: our own appearance. A new connection starts a new set of
   * entity ids (after a server restart an id may be someone else, even at
   * the same appearance version), so every drawn character is forgotten;
   * assembled looks stay cached by appearance.
   */
  welcome(msg: Extract<ServerMessage, { type: "welcome" }>): void {
    this.renderer.forgetCharacters();
    this.setSelf(msg.appearance, msg.appearanceVersion, true);
  }

  /** `AppearanceChanged`, for us or a character in view. */
  appearanceChanged(msg: Extract<ServerMessage, { type: "appearanceChanged" }>, selfId: number, remotes: RemotePlayers | null): void {
    if (msg.id === selfId) this.setSelf(msg.appearance, msg.appearanceVersion);
    else remotes?.setAppearance(msg.id, msg.appearanceVersion, msg.appearance);
  }

  /** Our appearance changed (`force` takes a reconnect's Welcome even if older). */
  private setSelf(appearance: Appearance, version: number, force = false): void {
    if (!force && this.self && version < this.self.version) return;
    this.self = { appearance, version };
    this.wardrobe.appearanceChanged(appearance);
  }

  /** Plays an emote if we are standing, and tells the server. */
  play(e: Emote): void {
    // An emote (the sit above all) stops auto run; then we stand from this frame on.
    const stopped = this.opts.stopAutoRun?.() ?? false;
    if (!this.emote.start(e, performance.now(), !this.standing && !stopped)) {
      this.opts.notify("Stand still to do that.");
      return;
    }
    this.opts.send(encodeEmote(e));
  }

  /** Keys the cast takes: the wardrobe's (I, and Escape while it is open). */
  handleKey(e: KeyboardEvent): boolean {
    return this.wardrobe.handleKey(e);
  }

  /** Height above the feet of `id`'s nameplate (m). */
  plateHeight(id: number): number {
    return this.renderer.plateHeight(id);
  }

  /** The quality level's character LOD rules. */
  setQuality(q: Quality): void {
    this.renderer.setQuality(q);
  }

  /** Whether `id` is drawn with its assembled look yet. */
  isDrawn(id: number): boolean {
    return this.renderer.isReady(id);
  }

  /** Height of `id`'s head above its feet as drawn (m), potion scale included. */
  headHeight(id: number): number {
    return this.renderer.headHeight(id);
  }

  /** Where `id`'s wand tip is as drawn (spells); false while it has no skeleton. */
  wandTip(id: number, out: Vector3): boolean {
    return this.renderer.wandTip(id, out);
  }

  /** Where `id`'s hips are as drawn (a broom's seat), returning its drawn scale; 0 while it has no skeleton. */
  hips(id: number, out: Vector3): number {
    return this.renderer.hips(id, out);
  }

  /**
   * Our own wand gesture for `ms` (a spell we cast): others see the
   * server's `Cast` anim; our pose is predicted, so it plays here. Moving
   * ends it, as on the server.
   */
  playCast(ms: number): void {
    if (this.standing) this.castUntil = performance.now() + ms;
  }

  /** Our level (the wardrobe explains required levels with it). */
  setLevel(level: number): void {
    this.wardrobe.setLevel(level);
  }

  /** Seats a sitting character is drawn on (null: none; the sit plays where the character stands). */
  setSeats(seats: SeatLookup | null, floorSeats: SeatLookup | null = null): void {
    this.seats = seats;
    this.floorSeats = floorSeats;
  }

  /** Where potion effects on characters come from (null: none). */
  setLooks(source: LookSource | null): void {
    this.looks = source;
  }

  /** NPCs: how each entity is drawn apart from its look (null: everyone as they are). */
  setStyles(source: ((id: number) => CharacterStyle | null) | null): void {
    this.styles = source;
  }

  /** Whom characters look at (null: nobody looks at anyone). */
  setLookTargets(source: LookTargets | null): void {
    this.lookTargets = source;
  }

  /** The renderer's look for `id` this frame (call after its `show`). */
  private look(id: number): void {
    const src = this.lookTargets;
    if (!src) return;
    const p = this.lookPoint;
    this.renderer.setLook(id, src.targetOf(id), src.pointOf(id, p) ? p : null);
  }

  /** Whether `id`'s head is turned toward a look target now, and how far (radians), for tests and the probe. */
  looking(id: number): { yaw: number; pitch: number } | null {
    return this.renderer.looking(id);
  }

  /** Our own look as last known (null before Welcome). */
  get selfAppearance(): Appearance | null {
    return this.self?.appearance ?? null;
  }

  get stats(): CharacterStats {
    return this.renderer.stats;
  }

  /** Lines for the F3 overlay. */
  perfLines(): [string, string][] {
    const s = this.renderer.stats;
    return [
      ["skinned", `${s.visible} characters drawn, LOD0-3 ${s.tiers.join("/")}, ${Math.round(s.triangles / 1000)}k triangles of ${Number.isFinite(s.allowance) ? `${Math.round(s.allowance / 1000)}k` : "any"} (world ${Math.round(s.worldInView / 1000)}k)`],
      ["looks", `${s.geometries} assembled, ${Math.round(s.vertices / 1000)}k vertices, ${s.pending} pending, last assembly ${s.assemblyMs.map((m) => m.toFixed(1)).join("/")} ms`],
      ["shadows", `${s.shadowCasters} in the map, ${s.blobs} blobs, ${s.swaying} swaying`],
      ["part files", `${s.partFiles} requested, ${s.missingFiles} missing (greybox)`],
      ["recoloured", `${s.recoloured} live (chameleon), ${this.renderer.styledMaterials} styled materials (npcs)`],
    ];
  }

  /** Where we sit as last drawn on a bench or chair (the side we face, whether the seat turns), or null. */
  seatedFacing(): SeatedFacing | null {
    return this.selfSeat;
  }

  /** Whether `id` is turning round on its bench now (tests, probes). */
  turningOnSeat(id: number): boolean {
    return this.turns.turning(id);
  }

  /**
   * The fit to draw `id` on `seat` with this frame: the turn on a two-sided
   * seat eased (`seat.yaw` rewritten), its drop shortened mid-turn.
   */
  private seatFit(id: number, seat: SeatPlace, dt: number): SeatPlace {
    const tuck = this.turns.draw(id, seat, dt);
    if (tuck <= 0) return seat;
    const f = this.drawnFit;
    Object.assign(f, seat);
    f.drop = seat.drop * (1 - BENCH_TUCK * tuck);
    return f;
  }

  /** Draws us, every visible remote and the `figures` (NPCs that are no entity) for this frame. */
  draw(nowMs: number, dt: number, me: SelfPose | null, visible: readonly Remote[], eye: { x: number; y: number; z: number }, figures: readonly Figure[] = []): void {
    const r = this.renderer;
    r.begin();
    this.turns.begin();
    this.selfSeat = null;
    if (me) r.setSelf(me.id);
    if (me && this.self && me.id >= 0) {
      const motion = this.selfMotion;
      motion.pose.x = me.x;
      motion.pose.z = me.z;
      motion.pose.anim = me.anim;
      trackSpeed(motion, dt);
      const moving = me.anim === "walk" || me.anim === "run";
      // On a broom the seat stays (no emote or wand gesture plays over it).
      const riding = me.anim === "fly" || me.anim === "hover";
      this.standing = !moving && !riding;
      if (moving || riding) this.castUntil = 0;
      const anim = riding ? me.anim : ((nowMs < this.castUntil ? "cast" : null) ?? this.emote.anim(nowMs, moving) ?? me.anim);
      const look = this.looks?.(me.id) ?? null;
      const shown = this.recoloured.shown(me.id, look, this.self.appearance, this.self.version);
      const seat = this.seat;
      if (anim === "sit" && this.seats?.find(me.x, me.y, me.z, seat, me.yaw)) {
        const s = this.selfSeatSlot;
        s.yaw = seat.yaw;
        s.twoSided = seat.twoSided;
        this.selfSeat = s;
        const floor = seatFloor(seat, me.y);
        const fit = this.seatFit(me.id, seat, dt);
        r.show(me.id, shown ? shown.version : this.self.version, shown?.appearance ?? this.self.appearance, seat.x, seat.y, seat.z, seat.yaw, anim, motion.speed, look?.fx ?? null, floor, false, fit);
      } else if (anim === "sit" && this.floorSeats?.find(me.x, me.y, me.z, seat)) {
        r.show(me.id, shown ? shown.version : this.self.version, shown?.appearance ?? this.self.appearance, seat.x, seat.y, seat.z, Number.isNaN(seat.yaw) ? me.yaw : seat.yaw, anim, motion.speed, look?.fx ?? null, me.y, true);
      } else {
        r.show(me.id, shown ? shown.version : this.self.version, shown?.appearance ?? this.self.appearance, me.x, me.y, me.z, me.yaw, anim, motion.speed, look?.fx ?? null);
      }
      this.look(me.id);
    }
    for (const v of visible) {
      const look = this.looks?.(v.id) ?? null;
      const shown = this.recoloured.shown(v.id, look, v.appearance, v.appearanceVersion);
      const p = v.pose;
      const seat = this.seat;
      if (p.anim === "sit" && this.seats?.find(p.x, p.y, p.z, seat, p.yaw)) {
        const floor = seatFloor(seat, p.y);
        const fit = this.seatFit(v.id, seat, dt);
        r.show(v.id, shown ? shown.version : v.appearanceVersion, shown?.appearance ?? v.appearance, seat.x, seat.y, seat.z, seat.yaw, p.anim, v.speed, look?.fx ?? null, floor, false, fit);
      } else if (p.anim === "sit" && this.floorSeats?.find(p.x, p.y, p.z, seat)) {
        r.show(v.id, shown ? shown.version : v.appearanceVersion, shown?.appearance ?? v.appearance, seat.x, seat.y, seat.z, Number.isNaN(seat.yaw) ? p.yaw : seat.yaw, p.anim, v.speed, look?.fx ?? null, p.y, true);
      } else {
        r.show(v.id, shown ? shown.version : v.appearanceVersion, shown?.appearance ?? v.appearance, p.x, p.y, p.z, p.yaw, p.anim, v.speed, look?.fx ?? null);
      }
      if (this.styles) r.setStyle(v.id, this.styles(v.id));
      this.look(v.id);
    }
    for (const f of figures) {
      r.show(f.id, f.version, f.appearance, f.x, f.y, f.z, f.yaw, f.anim, 0, f.fx);
      r.setStyle(f.id, f.style);
      r.setLook(f.id, -1, f.look);
    }
    r.update(dt, eye);
    this.bar.setActive(this.emote.active);
  }

  /** A new zone or connection: our emote does not carry over. */
  reset(): void {
    this.emote.clear();
    this.castUntil = 0;
  }

  dispose(): void {
    this.wardrobe.dispose();
    this.renderer.dispose();
  }
}

/** The floor under a seat: its top less the drop it was placed with when the seat says, else the sitter's own feet (`feetY`). */
function seatFloor(seat: SeatPlace, feetY: number): number {
  return Number.isFinite(seat.floor) ? seat.floor : feetY;
}
