// Potion effects in the running game: keeps every character's visible
// effects and our own timers current from the server's messages, tells the
// character renderer how to draw each one (Mimic Draught look, Diminishing and
// Enlarging Draught scale, Chameleon colour), draws the sprites (Glow aura, Fortune's Draught
// glints, Pick-Me-Up Tonic steam) and the shimmer of find spots nearby, and answers
// the questions the rest of the game asks (hide this nameplate? is this
// character under Chatterbrew? how fast do we walk?).
//
// Spells to level 20 (protocol v11): a bound character's pose freezes
// (`CharacterFx.animRate` 0) under a faint chalky shimmer, a slowed one
// moves in slow motion (`animRate` pct / 100) with soft after-image
// ripples, and Tinctura's colour goes in the look (`DrawLook.recolour`:
// the character is drawn with it as its body garment's dye), so a Chameleon
// hair tint carries on beside it; a Chameleon robe tint waits until it ends. Sound: on a duelist,
// the duel's binds, slows and wards are its cues' (arena-fx), so their
// world effects begin silently; spell effects end without the potion
// wear-off chime (Dissolvo has its own sound, a lapse is quiet).
//
// Cost: the per-frame work is a loop over the characters drawn that have
// effects; all sprites go into three thin-instanced layers (three draw
// calls in total); a Chameleon recolour rewrites one colour buffer every
// `cycle_ms` per character (renderer.ts). Nothing allocates per frame.

import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import { appearanceKey } from "../../content/appearance";
import { paletteRgb } from "../../content/catalog";
import { audio, type SoundId } from "../../audio";
import type { Appearance, EffectKind, EntityInfo, ServerMessage, Vec3, VisibleEffect } from "../../wasm/sim_wasm";
import type { DrawLook } from "../characters/cast";
import type { CharacterFx } from "../characters";
import { cameraAxes, SpriteLayer } from "./billboards";
import { chameleonColour, chameleonOrder, familyColours } from "./chameleon";
import { animRate, EffectBook, glowColour, NO_FX, OwnEffects, summarize, liveTint, type FxSummary } from "./state";
import { SteamPool, type Emitter } from "./steam";

/** A character drawn this frame, as the effects need it. */
export interface FxSubject {
  id: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
}

export interface FindSpot {
  id: string;
  pos: Vec3;
}

export interface EffectsControllerOptions {
  scene: Scene;
  /** Head height (m) of a drawn character, potion scale included. */
  headHeight: (id: number) => number;
  /**
   * Spells L20: whether `id` duels in the duel we see. A duel's binds,
   * slows and wards are its own cues' sounds (arena-fx), so their world
   * effects begin silently on its duelists.
   */
  inDuel?: (id: number) => boolean;
}

/** Most sprites per layer and frame. */
const GLOW_MAX = 100;
const SPARKLE_MAX = 400;
const STEAM_MAX = 480;
/** Glows and glints beyond this camera distance are not drawn (m). */
const SPRITE_FAR = 45;
/** Find spots shimmer within this horizontal distance (m) and height difference. */
const SHIMMER_RANGE = 7;
const SHIMMER_HEIGHT = 3;
/** How fast a character under a Diminishing or Enlarging Draught eases to its new size (1/s). */
const SCALE_RATE = 3.5;
const LUCKY_GOLD: [number, number, number] = [1, 0.84, 0.38];
/** Pick-Me-Up Tonic steam: a cool white the puff shader shades to grey-lilac at the rim. */
const STEAM_RGB: [number, number, number] = [0.9, 0.91, 1];
/** Rigora: a faint chalky white. */
const CHALK_RGB: [number, number, number] = [0.94, 0.95, 1];
/** Tardara: its magenta, softened. */
const SLOW_RGB: [number, number, number] = [1, 0.45, 0.88];

/** Sound: our own effect beginning (ownEffects), by kind. */
const OWN_SOUND: Readonly<Record<EffectKind, SoundId>> = {
  glow: "potion.glow",
  chameleon: "potion.chameleon",
  shrinking: "potion.shrinking",
  swelling: "potion.swelling",
  babbling: "potion.babbling",
  mimic_draught: "potion.mimic_draught",
  fortunes_draught: "potion.fortunes_draught",
  pick_me_up_tonic: "potion.pick_me_up_tonic",
  invisibility: "potion.invisibility",
  fortune: "potion.fortune",
  featherlight: "potion.glow",
  // Spells L20: the spells landing on us.
  petrified: "spell.bound",
  slowed: "spell.slowed",
  recoloured: "spell.recoloured",
  guardian: "guardian.summon",
};

/** Spells L20: the effects a duel's cues already sound on its duelists. */
const DUEL_OWN_KINDS: ReadonlySet<EffectKind> = new Set<EffectKind>(["petrified", "slowed", "guardian"]);
const DUEL_VISIBLE_KINDS: ReadonlySet<VisibleEffect["kind"]> = new Set<VisibleEffect["kind"]>(["bound", "slowed", "guardian"]);
/** Spells L20: spell effects end without the potion wear-off chime (Dissolvo has its own sound; a lapse is silent). */
const SILENT_END_OWN: ReadonlySet<EffectKind> = new Set<EffectKind>(["petrified", "slowed", "recoloured", "guardian"]);
const SILENT_END_VISIBLE: ReadonlySet<VisibleEffect["kind"]> = new Set<VisibleEffect["kind"]>(["lucerna", "bound", "slowed", "recoloured", "guardian"]);

/**
 * Sound: what someone else's visible effects changing from `before` to
 * `after` sounds like: each effect beginning (Lucerna is its cast's; on a
 * duelist, a duel's statuses are its cues'), then one wear-off chime when a
 * potion's effect ended.
 */
export function visibleChangeSounds(before: readonly VisibleEffect[], after: readonly VisibleEffect[], dueling: boolean): SoundId[] {
  const out: SoundId[] = [];
  const had = new Set(before.map((e) => e.kind));
  const has = new Set(after.map((e) => e.kind));
  for (const e of after) {
    if (had.has(e.kind) || (dueling && DUEL_VISIBLE_KINDS.has(e.kind))) continue;
    const sound = visibleSound(e);
    if (sound) out.push(sound);
  }
  // (A guardian fading has its own sound: game/guardian.)
  if (before.some((e) => !SILENT_END_VISIBLE.has(e.kind) && !has.has(e.kind))) out.push("potion.wear_off");
  return out;
}

/** Sound: our own effects changing from `had` to `kinds` (as `visibleChangeSounds`). */
export function ownChangeSounds(had: ReadonlySet<EffectKind>, kinds: ReadonlySet<EffectKind>, dueling: boolean): SoundId[] {
  const out: SoundId[] = [];
  for (const k of kinds) if (!had.has(k) && !(dueling && DUEL_OWN_KINDS.has(k))) out.push(OWN_SOUND[k]);
  for (const k of had) if (!kinds.has(k) && !SILENT_END_OWN.has(k)) out.push("potion.wear_off");
  return out;
}

/** Sound: what someone else's visible effect beginning sounds like (Lucerna is its cast's). */
function visibleSound(e: VisibleEffect): SoundId | null {
  switch (e.kind) {
    case "glow":
      return "potion.glow";
    case "chameleon":
      return "potion.chameleon";
    case "scale":
      return e.pct < 100 ? "potion.shrinking" : e.pct > 100 ? "potion.swelling" : null;
    case "babbling":
      return "potion.babbling";
    case "disguise":
      return "potion.mimic_draught";
    case "lucky":
      return "potion.fortunes_draught";
    case "steam":
      return "potion.pick_me_up_tonic";
    case "nameplateHidden":
      return "potion.invisibility";
    case "lucerna":
      return null;
    // Spells L20: a spell landing on someone.
    case "bound":
      return "spell.bound";
    case "slowed":
      return "spell.slowed";
    case "recoloured":
      return "spell.recoloured";
    case "guardian":
      // A guarding guardian is a duel's ward: its cue sounds it.
      return e.guarding ? null : "guardian.summon";
  }
}

interface Runtime {
  revision: number;
  summary: FxSummary;
  /** Eased potion scale (1 = normal). */
  scale: number;
  look: DrawLook;
  fx: CharacterFx;
  tint: { part: "hair" | "robe"; colour: [number, number, number] };
  /** Chameleon: the seeded order of the family, and the colour index shown. */
  order: number[];
  orderFamily: number;
  colour: number;
  steam: Emitter;
  /** The Glow colour as sRGB 0..1 (looked up when the effects change, not per frame). */
  glowRgb: [number, number, number];
}

export class EffectsController {
  readonly book = new EffectBook();
  readonly own = new OwnEffects();
  private selfId = -1;
  private readonly runtime = new Map<number, Runtime>();
  private readonly glow: SpriteLayer;
  private readonly sparkle: SpriteLayer;
  private readonly steamLayer: SpriteLayer;
  private readonly steam = new SteamPool(STEAM_MAX);
  private readonly right = new Vector3();
  private readonly up = new Vector3();
  private readonly disguiseSerials = new Map<string, number>();
  private spots: FindSpot[] = [];
  private readonly cooling = new Set<string>();
  private time = 0;
  /** Sound: our effects last heard (null: none since the connection opened, the next is a report). */
  private ownKinds: Set<EffectKind> | null = null;
  /** Sound: the characters drawn last frame (where someone's effect sounds). */
  private lastSubjects: readonly FxSubject[] = [];

  constructor(private readonly opts: EffectsControllerOptions) {
    // Glows tint pale floors as well as lighting dark ones (`halo`); glints
    // are pure light; steam is shaded so it reads on any ground.
    this.glow = new SpriteLayer(opts.scene, "glow", { shape: "glow", blend: "halo", capacity: GLOW_MAX, stretch: [1, 1.5] });
    this.sparkle = new SpriteLayer(opts.scene, "sparkle", { shape: "star", blend: "add", capacity: SPARKLE_MAX });
    this.steamLayer = new SpriteLayer(opts.scene, "steam", { shape: "puff", blend: "alpha", capacity: STEAM_MAX });
  }

  // ---------------------------------------------------------------- messages

  /** `Welcome`: a new connection, new entity ids; our own effects arrive with it. */
  welcome(msg: Extract<ServerMessage, { type: "welcome" }>): void {
    this.selfId = msg.yourId;
    this.ownKinds = null;
    this.book.clear();
    this.runtime.clear();
    this.steam.clear();
    this.book.set(msg.yourId, msg.effectsVersion, msg.effects);
  }

  snapshot(entered: readonly EntityInfo[], left: readonly number[]): void {
    if (!entered.length && !left.length) return;
    this.book.applySnapshot(entered, left, this.selfId);
    for (const id of left) if (id !== this.selfId) this.runtime.delete(id);
  }

  /** A zone change: the visible set starts over; our own effects stay. */
  zoneChanged(): void {
    this.book.clearOthers(this.selfId);
    for (const id of [...this.runtime.keys()]) if (id !== this.selfId) this.runtime.delete(id);
    this.steam.clear();
  }

  effectsChanged(msg: Extract<ServerMessage, { type: "effectsChanged" }>): void {
    const before = msg.id === this.selfId ? null : this.book.get(msg.id);
    if (this.book.set(msg.id, msg.effectsVersion, msg.effects) && before) this.effectSounds(msg.id, before, msg.effects);
  }

  /** Sound: someone else's effects began or ended, heard where they stand (ours come with `ownEffects`). */
  private effectSounds(id: number, before: readonly VisibleEffect[], after: readonly VisibleEffect[]): void {
    const s = this.lastSubjects.find((x) => x.id === id);
    if (!s) return;
    const at: [number, number, number] = [s.x, s.y + 1, s.z];
    for (const sound of visibleChangeSounds(before, after, this.opts.inDuel?.(id) ?? false)) audio.playAt(sound, at, { owner: id });
  }

  ownEffects(msg: Extract<ServerMessage, { type: "ownEffects" }>): void {
    this.own.set(msg.effects, msg.speedPct, performance.now(), msg.broomPct);
    // Sound: an effect of ours began or wore off (the first list after a connection is a report).
    const kinds = new Set(msg.effects.map((e) => e.kind));
    const had = this.ownKinds;
    this.ownKinds = kinds;
    if (!had) return;
    for (const sound of ownChangeSounds(had, kinds, this.opts.inDuel?.(this.selfId) ?? false)) audio.play(sound, { owner: this.selfId });
  }

  /** The broom speed (percent) to predict our flight with (`flyParams(tier, broomPct)`; Featherlight). */
  get broomPct(): number {
    return this.own.broomPct;
  }

  /** The speed (percent) to predict our movement with (`moveParams(speedPct)`). */
  get speedPct(): number {
    return this.own.speedPct;
  }

  // ---------------------------------------------------------------- questions

  summaryOf(id: number): FxSummary {
    return this.runtime.get(id)?.summary ?? (this.book.get(id).length ? summarize(this.book.get(id)) : NO_FX);
  }

  /** Spells L20: our guardian is a duel ward now (guarding: it cannot be dismissed). */
  get ownGuardianGuarding(): boolean {
    return this.summaryOf(this.selfId).guardian?.guarding ?? false;
  }

  /** Invisibility Draught: no nameplate. */
  plateHidden(id: number): boolean {
    return this.summaryOf(id).nameplateHidden;
  }

  babbling(id: number): boolean {
    return this.summaryOf(id).babbling;
  }

  /** Our eased potion scale, for the camera's height. */
  get selfScale(): number {
    return this.runtime.get(this.selfId)?.scale ?? 1;
  }

  /** How the character renderer should draw `id` this frame (Cast's look source). */
  look(id: number): DrawLook | null {
    const rt = this.runtimeFor(id);
    return rt ? rt.look : null;
  }

  // ---------------------------------------------------------------- find spots

  /** The find spots of the zone shown (none outside the castle). */
  setFindSpots(spots: readonly FindSpot[]): void {
    this.spots = [...spots];
  }

  /** Spots on cooldown for us do not shimmer. */
  setCooling(spot: string, cooling: boolean): void {
    if (cooling) this.cooling.add(spot);
    else this.cooling.delete(spot);
  }

  // ---------------------------------------------------------------- per frame

  /**
   * The runtime of `id`, created or refreshed from the book; null when the
   * character has no effects (and its size has eased back to normal).
   */
  private runtimeFor(id: number): Runtime | null {
    let rt = this.runtime.get(id);
    const rev = this.book.revision;
    if (rt && rt.revision === rev) return rt;
    const effects = this.book.get(id);
    if (!effects.length && (!rt || Math.abs(rt.scale - 1) < 0.002)) {
      if (rt) this.runtime.delete(id);
      return null;
    }
    const summary = effects.length ? summarize(effects) : NO_FX;
    if (!rt) {
      rt = {
        revision: rev,
        summary,
        scale: 1,
        look: { appearance: null, version: 0, fx: null, recolour: null },
        fx: { scale: 1, tint: null, animRate: 1 },
        tint: { part: "hair", colour: [1, 1, 1] },
        order: [],
        orderFamily: -1,
        colour: -1,
        steam: { owed: 0 },
        glowRgb: [1, 1, 1],
      };
      this.runtime.set(id, rt);
    }
    rt.revision = rev;
    rt.summary = summary;
    if (summary.glow !== null) rt.glowRgb = glowColour(paletteRgb(summary.glow));
    rt.look.recolour = summary.recolour;
    rt.fx.animRate = animRate(summary);
    rt.look.appearance = summary.disguise;
    rt.look.version = summary.disguise ? this.disguiseVersion(summary.disguise) : 0;
    rt.look.fx = rt.fx;
    if (summary.chameleon && summary.chameleon.family !== rt.orderFamily) {
      rt.orderFamily = summary.chameleon.family;
      rt.order = chameleonOrder(familyColours(summary.chameleon.family), id);
      rt.colour = -1;
    }
    return rt;
  }

  /** A version for a Mimic Draught look: negative, the same for the same look. */
  private disguiseVersion(a: Appearance): number {
    const key = appearanceKey(a);
    let n = this.disguiseSerials.get(key);
    if (n === undefined) {
      if (this.disguiseSerials.size > 256) this.disguiseSerials.clear();
      n = this.disguiseSerials.size + 1;
      this.disguiseSerials.set(key, n);
    }
    return -n;
  }

  /**
   * Before the characters are drawn: eases sizes and steps Chameleon
   * colours for everyone drawn this frame (`subjects`, ourselves included).
   */
  prepare(dt: number, subjects: readonly FxSubject[], wallMs: number): void {
    this.time += dt;
    this.lastSubjects = subjects;
    const ease = 1 - Math.exp(-dt * SCALE_RATE);
    for (const s of subjects) {
      const rt = this.runtimeFor(s.id);
      if (!rt) continue;
      const target = rt.summary.scalePct / 100;
      rt.scale += (target - rt.scale) * ease;
      if (Math.abs(target - rt.scale) < 0.002) rt.scale = target;
      rt.fx.scale = rt.scale;
      // Spells L20: Tinctura's robe colour is in the look; a Chameleon robe tint waits for it, a hair one goes on.
      const part = liveTint(rt.summary);
      const ch = part ? rt.summary.chameleon : null;
      if (ch && rt.order.length) {
        const colour = chameleonColour(rt.order, s.id, wallMs, undefined, rt.order); // the order is already seeded
        if (colour !== rt.colour) {
          rt.colour = colour;
          const [r, g, b] = paletteRgb(colour);
          rt.tint.colour = [r, g, b];
        }
        rt.tint.part = ch.part;
        rt.fx.tint = rt.tint;
      } else {
        rt.fx.tint = null;
      }
    }
  }

  /**
   * After the characters are drawn: the sprites for this frame. `subjects`
   * should come nearest first so a full layer drops the far ones.
   */
  render(dt: number, subjects: readonly FxSubject[], camera: Camera, feet: Vec3 | null): void {
    const eye = camera.globalPosition;
    cameraAxes(camera, this.right, this.up);
    this.glow.begin();
    this.sparkle.begin();
    this.steamLayer.begin();
    this.steam.step(dt);
    const t = this.time;

    for (const s of subjects) {
      const rt = this.runtime.get(s.id);
      if (!rt) continue;
      const fx = rt.summary;
      if (fx.glow === null && !fx.lucky && !fx.steam && !fx.bound && fx.slowedPct === null) continue;
      const dx = s.x - eye.x;
      const dz = s.z - eye.z;
      const head = this.opts.headHeight(s.id);
      const dist = Math.hypot(dx, s.y + head * 0.5 - eye.y, dz);
      if (fx.steam) this.steam.emit(rt.steam, dt, s.x, s.y, s.z, s.yaw, head, rt.scale, dist);
      if (dist > SPRITE_FAR) continue;
      if (fx.glow !== null) {
        // Behind the character as the camera sees it, so the body occludes
        // the middle and the aura reads as a halo around the silhouette.
        const inv = dist > 0.01 ? 0.3 / dist : 0;
        const cy = s.y + head * 0.52;
        const c = rt.glowRgb;
        const pulse = 0.66 + 0.12 * Math.sin(t * 2.2 + s.id);
        this.glow.push(s.x + dx * inv, cy + (cy - eye.y) * inv, s.z + dz * inv, head * 0.95, c[0], c[1], c[2], pulse);
      }
      if (fx.bound) this.chalk(s, head, rt.scale, t);
      if (fx.slowedPct !== null) this.ripples(s, head, rt.scale, t);
      if (fx.lucky) {
        for (let k = 0; k < 3; k++) {
          const a = t * 1.3 + k * 2.094 + s.id;
          const tw = 0.5 + 0.5 * Math.sin(t * 5.1 + k * 1.7 + s.id * 0.37);
          const rad = 0.36 * rt.scale;
          this.sparkle.push(s.x + Math.cos(a) * rad, s.y + head * (0.7 + 0.12 * Math.sin(t * 1.9 + k)), s.z + Math.sin(a) * rad, (0.1 + 0.08 * tw) * rt.scale, LUCKY_GOLD[0], LUCKY_GOLD[1], LUCKY_GOLD[2], 0.25 + 0.75 * tw);
        }
      }
    }

    this.steam.forEach((x, y, z, size, alpha) => {
      this.steamLayer.push(x, y, z, size, STEAM_RGB[0], STEAM_RGB[1], STEAM_RGB[2], alpha);
    });
    if (feet) this.shimmer(feet, t);

    this.glow.end(this.right, this.up);
    this.sparkle.end(this.right, this.up);
    this.steamLayer.end(this.right, this.up);
  }

  /** Spells L20: Rigora: a faint chalky shimmer creeping over the stiff body. */
  private chalk(s: FxSubject, head: number, scale: number, t: number): void {
    for (let k = 0; k < 6; k++) {
      const ph = t * 0.7 + k * 1.047 + s.id * 0.3;
      const climb = (t * 0.35 + k / 6) % 1;
      const tw = 0.5 + 0.5 * Math.sin(t * 3.1 + k * 2.3 + s.id);
      const r = 0.24 * scale;
      this.sparkle.push(s.x + Math.cos(ph) * r, s.y + head * (0.08 + 0.9 * climb), s.z + Math.sin(ph) * r, (0.07 + 0.05 * tw) * scale, CHALK_RGB[0], CHALK_RGB[1], CHALK_RGB[2], (0.25 + 0.45 * tw) * Math.sin(Math.PI * climb));
    }
    this.glow.push(s.x, s.y + head * 0.5, s.z, head * 0.8, CHALK_RGB[0], CHALK_RGB[1], CHALK_RGB[2], 0.18);
  }

  /** Spells L20: Tardara: soft rings drifting slowly outward round the body, like after-images (halo blend: they tint pale floors too). */
  private ripples(s: FxSubject, head: number, scale: number, t: number): void {
    for (let ring = 0; ring < 2; ring++) {
      const age = (t * 0.4 + ring * 0.5) % 1;
      const r = (0.25 + 0.5 * age) * scale;
      const y = s.y + head * (0.3 + 0.3 * ring);
      const a = 0.7 * (1 - age);
      for (let k = 0; k < 10; k++) {
        const ang = (k / 10) * Math.PI * 2 + ring * 0.4 + t * 0.2;
        const x = s.x + Math.cos(ang) * r;
        const z = s.z + Math.sin(ang) * r;
        this.glow.push(x, y, z, 0.16 * scale, SLOW_RGB[0], SLOW_RGB[1], SLOW_RGB[2], a);
        if (k % 2 === 0) this.sparkle.push(x, y, z, 0.08 * scale, 1, 0.8, 0.96, a);
      }
    }
  }

  /** A faint glint over find spots within a few steps, so wanderers notice them. */
  private shimmer(feet: Vec3, t: number): void {
    for (const spot of this.spots) {
      if (this.cooling.has(spot.id)) continue;
      const d = Math.hypot(spot.pos[0] - feet[0], spot.pos[2] - feet[2]);
      if (d > SHIMMER_RANGE || Math.abs(spot.pos[1] - feet[1]) > SHIMMER_HEIGHT) continue;
      const near = 1 - d / SHIMMER_RANGE;
      const seed = spot.pos[0] * 1.3 + spot.pos[2] * 0.7;
      for (let k = 0; k < 3; k++) {
        const ph = t * (0.9 + k * 0.23) + seed + k * 2.1;
        const tw = Math.max(0, Math.sin(ph * 2.3));
        this.sparkle.push(
          spot.pos[0] + Math.cos(ph) * 0.35,
          spot.pos[1] + 0.45 + 0.35 * ((k + 1) / 3) + 0.08 * Math.sin(ph * 1.7),
          spot.pos[2] + Math.sin(ph) * 0.35,
          0.12 + 0.06 * tw,
          1,
          0.93,
          0.75,
          (0.15 + 0.7 * tw) * near,
        );
      }
    }
  }

  /** Lines for the F3 overlay. */
  perfLines(): [string, string][] {
    return [["effects", `${this.runtime.size} characters, ${this.glow.size} glows, ${this.sparkle.size} glints, ${this.steamLayer.size} puffs`]];
  }

  dispose(): void {
    this.glow.dispose();
    this.sparkle.dispose();
    this.steamLayer.dispose();
    this.runtime.clear();
  }
}
