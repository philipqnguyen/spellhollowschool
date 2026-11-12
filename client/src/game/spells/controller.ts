// Spells outside duels (docs/architecture.md, "Spells and dueling"):
// what the player has learned (`Spellbook`), casting by key or from the
// spellbook (L lights or puts out your wand, V levitates a piece of dorm
// furniture, dueling spells cast as flourishes), the server's answers
// (`SpellCast` for everyone near, `Levitating` for the room, `SpellError`),
// and drawing it all: gesture trails and bursts in each spell's colour,
// lit wands, floating furniture.
//
// The server decides every cast; the client only keeps its own cooldowns so
// a key held down does not flood it, and asks again only once a cast could
// succeed.
//
// Spells to level 20 (protocol v11): the new spells aim at the world
// selection (aim.ts: a friend for Rigora, Tardara and Adveni; a
// friend or yourself for Tinctura and Dissolvo, Dissolvo first landing a piece
// floating in reach), Custodio toggles like Lucerna, and the casts are
// drawn: a streak to the target and each spell's signature (cast-fx.ts),
// Volucra's birds (volucra.ts), Aperio's shining spots for its caster
// (reveal.ts).

import type { Camera } from "@babylonjs/core/Cameras/camera";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { Scene } from "@babylonjs/core/scene";
import { audio, isSoundId } from "../../audio";
import type { Quality } from "../../settings";
import { encodeCastSpell, encodeEndSpell, encodeSetHotbar, type ServerMessage, type SpellErrorCode, type VisibleEffect, type Zone } from "../../wasm/sim_wasm";
import type { DormRoom } from "../dorm/room";
import { furnitureDef } from "../dorm/catalog";
import { gestureByKey, LEVITATION, WAND_LIGHT, spellById, spellRgb, targetsFriend, type SpellDef } from "./data";
import { chooseAim, nearestFloating } from "./aim";
import { Volucra } from "./volucra";
import { CastSignatures, signatureOf, type Spot } from "./cast-fx";
import { Levitations } from "./levitate";
import { RevealMarks } from "./reveal";
import { approximateTip, WandLights, type WandHolder } from "./wandlight";
import { LevitationTargeting } from "./targeting";
import { SpellFx } from "./vfx";

/** Ask the skeleton for the wand hand of this many nearest lit wands; the rest are estimated. */
const BONE_TIPS = 8;

export interface SpellbookState {
  learned: number[];
  loadout: number[];
  slots: number;
  /** Protocol v16: the social hotbar, one spell id or null per slot (ui/hotbar-state.ts). */
  hotbar: (number | null)[];
}

export interface SpellControllerOptions {
  scene: Scene;
  canvas: HTMLCanvasElement;
  camera: Camera;
  hudRoot: HTMLElement;
  quality: Quality;
  send: (frame: Uint8Array) => void;
  notify: (text: string) => void;
  selfId: () => number;
  zone: () => Zone | null;
  /** Visible effects of a character (the effects book). */
  effectsOf: (id: number) => readonly VisibleEffect[];
  /** Everyone drawn this frame, nearest the camera first (ourselves included). */
  drawn: () => readonly WandHolder[];
  /** A drawn character's pose, by id (ourselves included). */
  poseOf: (id: number) => WandHolder | null;
  /** Head height of a drawn character (m). */
  headHeight: (id: number) => number;
  /** The wand hand of a drawn character from its skeleton; false when it has none yet. */
  wandTip: (id: number, out: Vector3) => boolean;
  /** Our feet. */
  feet: () => [number, number, number];
  /** The camera's horizontal look direction (x, z). */
  look: () => [number, number];
  /** The dorm room we stand in (empty elsewhere) and lifting its furniture. */
  room: () => DormRoom;
  liftFurniture: (itemId: number, dy: number) => void;
  /** Our cast plays the wand gesture on our own character. */
  onSelfCast: () => void;
  /** Spells L20: the character selected in the world (Tab/E), or null. */
  selected: () => number | null;
  /** Spells L20: whether character `id` is a friend (the server decides; this only words a refusal). */
  isFriend: (id: number) => boolean;
}

/** The logical folder of the game files (src/assets.ts). */
const ASSETS = "assets/";

export class SpellController {
  readonly fx: SpellFx;
  private readonly wandLights: WandLights;
  private readonly floats = new Levitations();
  private readonly targeting: LevitationTargeting;
  private book: SpellbookState = { learned: [], loadout: [], slots: 3, hotbar: [] };
  /** Local time each spell may be cast again (client-side pacing only). */
  private readonly ready = new Map<number, number>();
  private readonly lit: WandHolder[] = [];
  private readonly tip = new Vector3();
  private bones = 0;
  private listeners: (() => void)[] = [];
  /** Spells L20: signatures where casts land, Volucra's flocks, Aperio's spots. */
  private readonly signatures = new CastSignatures();
  private readonly birds: Volucra;
  /** Where a caster stands now (Volucra's circle keeps over them). */
  private readonly casterAt = (id: number): WandHolder | null => this.opts.poseOf(id);
  private readonly reveals: RevealMarks;
  private readonly spot: Spot = { x: 0, y: 0, z: 0, head: 1.7 };
  private readonly tipAt: [number, number, number] = [0, 0, 0];

  constructor(private readonly opts: SpellControllerOptions) {
    this.fx = new SpellFx(opts.scene);
    this.wandLights = new WandLights(opts.scene, opts.quality);
    this.birds = new Volucra(opts.scene, ASSETS);
    this.reveals = new RevealMarks(opts.scene);
    this.targeting = new LevitationTargeting({
      scene: opts.scene,
      canvas: opts.canvas,
      camera: opts.camera,
      hudRoot: opts.hudRoot,
      room: opts.room,
      feet: opts.feet,
      look: opts.look,
      cast: (itemId) => this.send(LEVITATION, "furniture", itemId),
      notify: opts.notify,
    });
  }

  /** Learned spells, the loadout and its slots, as the server last said. */
  get spellbook(): SpellbookState {
    return this.book;
  }

  knows(id: number): boolean {
    return this.book.learned.includes(id);
  }

  /** Called whenever the spellbook changes. */
  onChange(fn: () => void): void {
    this.listeners.push(fn);
  }

  /** Whether our wand is lit (the server's word: our visible effects). */
  get wandLit(): boolean {
    return hasWandLight(this.opts.effectsOf(this.opts.selfId()));
  }

  setQuality(q: Quality): void {
    this.wandLights.setQuality(q);
  }

  // ---------------------------------------------------------------- messages

  /** The spell side's server messages. Returns whether `msg` was one. */
  onMessage(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "spellbook":
        this.book = { learned: [...msg.learned], loadout: [...msg.loadout], slots: msg.slots, hotbar: [...msg.hotbar] };
        for (const fn of this.listeners) fn();
        return true;
      case "hotbar":
        // The layout the server kept (ours, or the old one if it refused).
        this.book = { ...this.book, hotbar: [...msg.slots] };
        for (const fn of this.listeners) fn();
        return true;
      case "spellCast":
        this.spellCast(msg.caster, msg.spell, msg.target);
        return true;
      case "levitating":
        this.levitating(msg.itemId, msg.durationMs);
        return true;
      case "revealed":
        this.revealed(msg.durationMs, msg.spots);
        return true;
      case "spellError":
        audio.play("spell.fizzle");
        this.opts.notify(spellErrorText(msg.spell, msg.code, this.opts.zone(), this.opts.effectsOf(this.opts.selfId()).some((e) => e.kind === "bound")));
        // A refused cast may be tried again at once.
        this.ready.delete(msg.spell);
        return true;
      default:
        return false;
    }
  }

  /** A zone change: floats and targeting end with the room; birds, reveals and signatures stay behind. */
  leaving(): void {
    this.targeting.stop();
    this.floats.clear(this.opts.liftFurniture);
    this.birds.clear();
    this.reveals.clear();
    this.signatures.clear();
  }

  // ---------------------------------------------------------------- casting

  /** Casts `spell` the way it works outside duels. Returns false (with a note) when it cannot. */
  cast(spell: SpellDef): boolean {
    const zone = this.opts.zone();
    if (!this.knows(spell.id)) {
      this.opts.notify(`You have not learned ${spell.name} yet.`);
      return false;
    }
    if (zone?.kind === "arena") {
      this.opts.notify("Only dueling casts work on the dueling stage.");
      return false;
    }
    switch (spell.social.kind) {
      case "light":
        if (this.wandLit) {
          this.opts.send(encodeEndSpell(spell.id));
          return true;
        }
        return this.send(spell, "none");
      case "levitate":
        if (zone?.kind !== "dorm") {
          this.opts.notify(`${spell.name} lifts furniture in a dorm room. Try it in yours, or a friend's.`);
          return false;
        }
        if (this.targeting.active) {
          this.targeting.stop();
          return true;
        }
        return this.targeting.start();
      case "flourish":
        return this.send(spell, "none");
      case "guardian":
        // Like Lucerna: casting again while ours is out dismisses it.
        if (this.guardianOut) {
          this.opts.send(encodeEndSpell(spell.id));
          return true;
        }
        return this.send(spell, "none");
      default:
        return this.aimed(spell, this.opts.selected());
    }
  }

  /** Spells L20: a cast aimed by the rules of aim.ts at `selected` (or nothing). */
  private aimed(spell: SpellDef, selected: number | null): boolean {
    const aim = chooseAim(spell, {
      selected,
      selfId: this.opts.selfId(),
      isFriend: this.opts.isFriend,
      floatingInReach: spell.social.kind === "dispel" && this.opts.zone()?.kind === "dorm" ? this.floatingInReach(spell.social.range_m) : null,
    });
    switch (aim.kind) {
      case "refuse":
        this.opts.notify(aim.text);
        return false;
      case "entity":
        return this.send(spell, "entity", aim.id);
      case "furniture":
        return this.send(spell, "furniture", aim.itemId);
      case "none":
        return this.send(spell, "none");
    }
  }

  /** Spells L20: the learned spells that can be aimed at a friend (the player card's "Cast on" row). */
  friendSpells(): SpellDef[] {
    return this.book.learned.map((id) => spellById(id)).filter((s): s is SpellDef => !!s && targetsFriend(s));
  }

  /** Spells L20: casts `spell` on the character called `name` (the player card), found by `find` among those in view. */
  castOnName(spell: SpellDef, name: string, find: (name: string) => number | null): boolean {
    if (!this.knows(spell.id)) {
      this.opts.notify(`You have not learned ${spell.name} yet.`);
      return false;
    }
    if (this.opts.zone()?.kind === "arena") {
      this.opts.notify("Only dueling casts work on the dueling stage.");
      return false;
    }
    const id = find(name);
    if (id === null) {
      this.opts.notify(`${name} is not near enough to cast on.`);
      return false;
    }
    return this.aimed(spell, id);
  }

  /** Whether our guardian is out (the server's word: our visible effects). */
  get guardianOut(): boolean {
    return this.opts.effectsOf(this.opts.selfId()).some((e) => e.kind === "guardian" && !e.guarding);
  }

  /** The hotbar: the fraction (0..1) of `spell`'s cooldown still to run (the client's own pacing). */
  cooldownLeft(spell: SpellDef, now = performance.now()): number {
    const left = (this.ready.get(spell.id) ?? 0) - now;
    return left > 0 && spell.social.cooldown_ms > 0 ? left / spell.social.cooldown_ms : 0;
  }

  /** The hotbar: a new layout, shown at once and sent (`SetHotbar`); the server answers with the one it keeps. */
  setHotbar(slots: readonly (number | null)[]): void {
    this.book = { ...this.book, hotbar: [...slots] };
    for (const fn of this.listeners) fn();
    this.opts.send(encodeSetHotbar(Uint16Array.from(slots, (id) => id ?? 0)));
  }

  /** The floating piece nearest us within `range` (m), or null. */
  private floatingInReach(range: number): number | null {
    const room = this.opts.room();
    const list: { itemId: number; x: number; y: number; z: number }[] = [];
    for (const id of this.floats.ids()) {
      const it = room.items.get(id);
      if (it) list.push({ itemId: id, x: it.pos[0], y: it.pos[1], z: it.pos[2] });
    }
    return nearestFloating(list, this.opts.feet(), range);
  }

  private send(spell: SpellDef, kind: "none" | "furniture" | "entity", itemId?: number): boolean {
    const now = performance.now();
    if (now < (this.ready.get(spell.id) ?? 0)) {
      this.opts.notify(`${spell.name} needs a moment before you cast it again.`);
      return false;
    }
    this.ready.set(spell.id, now + spell.social.cooldown_ms);
    this.opts.send(encodeCastSpell(spell.id, kind, itemId ?? null));
    return true;
  }

  /** Choosing a piece to levitate (a click on the world is the spell's, not the selection's). */
  get aiming(): boolean {
    return this.targeting.active;
  }

  /** Characters look at their target: the piece Elevara is aimed at (its middle), into `out`; false when not aiming. */
  aimPoint(out: { x: number; y: number; z: number }): boolean {
    return this.targeting.aimPoint(out);
  }

  /** L lights the wand, V levitates; the targeting keys while choosing. */
  handleKey(e: KeyboardEvent): boolean {
    if (this.targeting.handleKey(e)) return true;
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return false;
    if (e.code === "KeyL") {
      this.cast(WAND_LIGHT);
      return true;
    }
    if (e.code === "KeyV") {
      this.cast(LEVITATION);
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- effects

  private spellCast(caster: number, spellId: number, target: Extract<ServerMessage, { type: "spellCast" }>["target"]): void {
    const spell = spellById(spellId);
    const pose = this.opts.poseOf(caster);
    if (caster === this.opts.selfId()) this.opts.onSelfCast();
    if (!spell || !pose) return;
    const now = performance.now();
    const rgb = spellRgb(spell);
    this.tipOf(caster, pose, this.tip);
    const tip: [number, number, number] = [this.tip.x, this.tip.y, this.tip.z];
    if (spell.social.kind === "light") {
      this.fx.burst(this.tip.x, this.tip.y, this.tip.z, rgb, now, 8, 0.35);
      // The cast turns the light on or off: the light is still as it was until the effect changes.
      audio.playAt(hasWandLight(this.opts.effectsOf(caster)) ? "spell.lucerna_off" : "spell.lucerna", tip, { owner: caster });
      return;
    }
    const id = `spell.${spell.key}`;
    if (isSoundId(id)) audio.playAt(id, tip, { owner: caster });
    // The gesture drawn in the air in front of the caster, facing the camera.
    const g = gestureByKey(spell.gesture);
    const right = this.fx.cameraRight;
    const fx = Math.sin(pose.yaw);
    const fz = Math.cos(pose.yaw);
    const head = this.opts.headHeight(caster);
    if (g) {
      this.fx.trail(g.points, pose.x + fx * 0.55, pose.y + head * 0.72, pose.z + fz * 0.55, right.x, right.z, 0.32, rgb, 650, now);
      audio.playAt("spell.trail", [pose.x + fx * 0.55, pose.y + head * 0.72, pose.z + fz * 0.55], { owner: caster });
    }
    if (target.kind === "furniture") {
      const it = this.opts.room().items.get(target.itemId);
      if (it) this.fx.burst(it.pos[0], it.pos[1] + 0.2, it.pos[2], rgb, now + 200, 14, 0.7);
    }
    // Spells L20: where it lands.
    const social = spell.social;
    if (social.kind === "birds") {
      this.birds.release(caster, pose.x, pose.y, pose.z, pose.yaw, this.opts.headHeight(caster), tip, social.count, social.duration_ms, now);
      return;
    }
    const sig = signatureOf(social.kind);
    if (!sig || target.kind === "furniture") return;
    const on = target.kind === "entity" ? target.id : sig === "sweep" ? -1 : caster;
    this.signatures.add(sig, caster, on, rgb, now, [pose.x, pose.y, pose.z], social.kind === "reveal" ? social.radius_m : 0, social.kind === "pull" ? social.move_ms + social.hold_ms : 0);
    if (sig === "tether" && target.kind === "entity") {
      const p = this.opts.poseOf(target.id);
      if (p) audio.playAt("spell.pulled", [p.x, p.y + 1, p.z], { owner: target.id, delayS: 0.2 });
    }
    if (sig === "unravel") {
      const p = this.opts.poseOf(on);
      if (p) audio.playAt("spell.dispelled", [p.x, p.y + 1, p.z], { owner: on, delayS: on === caster ? 0 : 0.2 });
    }
  }

  /** Spells L20: Aperio's spots, for us alone. */
  private revealed(durationMs: number, spots: Extract<ServerMessage, { type: "revealed" }>["spots"]): void {
    const me = this.opts.poseOf(this.opts.selfId());
    const list = spots.map((s) => ({ kind: s.kind, x: s.pos.x / 100, y: s.pos.y / 100, z: s.pos.z / 100 }));
    this.reveals.show(list, durationMs, performance.now(), me ? [me.x, me.y, me.z] : null);
    if (!list.length) this.opts.notify("The Revealing Charm shows nothing hidden nearby.");
  }

  private levitating(itemId: number, durationMs: number): void {
    const it = this.opts.room().items.get(itemId);
    if (!it) return;
    // Spells L20: Dissolvo lands it now.
    if (durationMs === 0) {
      if (this.floats.land(itemId, performance.now())) audio.playAt("spell.levitate_end", [it.pos[0], it.pos[1], it.pos[2]], { delayS: 0.5 });
      return;
    }
    this.floats.start(itemId, durationMs, performance.now());
    // It settles back down as the float ends.
    audio.playAt("spell.levitate_end", [it.pos[0], it.pos[1], it.pos[2]], { delayS: durationMs / 1000 });
  }

  /** Where `id`'s wand hand is: the skeleton for the nearest few, an estimate beyond. */
  private tipOf(id: number, pose: WandHolder, out: Vector3): void {
    if (this.bones < BONE_TIPS && this.opts.wandTip(id, out)) {
      this.bones++;
      return;
    }
    approximateTip(pose, this.opts.headHeight(id), out);
  }

  /** Per frame, before the scene renders. */
  update(dt: number): void {
    const now = performance.now();
    this.bones = 0;
    this.fx.begin(this.opts.camera);
    // Lit wands, nearest first.
    const lit = this.lit;
    lit.length = 0;
    for (const s of this.opts.drawn()) if (hasWandLight(this.opts.effectsOf(s.id))) lit.push(s);
    this.wandLights.update(dt, lit, (id, holder, out) => this.tipOf(id, holder, out), this.fx);
    this.floats.update(now, dt, this.opts.liftFurniture, (id) => this.itemSpot(id), this.fx);
    this.targeting.update(dt);
    if (this.signatures.count) this.signatures.update(now, this.fx, (id) => this.spotOf(id), (id) => this.tipOfId(id));
    // Volucra's feathers shimmer and puff in the effect layers: before they end the frame.
    this.birds.update(now, this.fx, this.casterAt);
    this.fx.end(now);
    this.reveals.update(now, this.opts.camera);
  }

  /** A drawn character as the signatures need it. */
  private spotOf(id: number): Spot | null {
    const p = this.opts.poseOf(id);
    if (!p) return null;
    this.spot.x = p.x;
    this.spot.y = p.y;
    this.spot.z = p.z;
    this.spot.head = this.opts.headHeight(id);
    return this.spot;
  }

  private tipOfId(id: number): readonly [number, number, number] | null {
    const p = this.opts.poseOf(id);
    if (!p) return null;
    this.tipOf(id, p, this.tip);
    this.tipAt[0] = this.tip.x;
    this.tipAt[1] = this.tip.y;
    this.tipAt[2] = this.tip.z;
    return this.tipAt;
  }

  /** A placed piece's middle and size, for the shimmer while it floats. */
  private itemSpot(itemId: number): { x: number; y: number; z: number; radius: number; height: number } | null {
    const it = this.opts.room().items.get(itemId);
    const def = it ? furnitureDef(it.itemDef) : undefined;
    if (!it || !def) return null;
    return { x: it.pos[0], y: it.pos[1], z: it.pos[2], radius: Math.hypot(def.footprint[0], def.footprint[1]) / 2, height: def.height };
  }

  /** Lines for the F3 overlay. */
  perfLines(): [string, string][] {
    const b = this.birds.counts;
    return [
      ["spells", `${this.wandLights.shown} lit wands (${this.wandLights.realLights} lights), ${this.floats.count} floating, ${this.fx.active} effects, ${this.signatures.count} landing`],
      ["spells L20", `${b.birds} birds and ${b.feathers} feathers in ${b.flocks} flocks (${b.source}), ${this.reveals.count} revealed`],
    ];
  }

  dispose(): void {
    this.targeting.dispose();
    this.birds.dispose();
    this.reveals.dispose();
    this.wandLights.dispose();
    this.fx.dispose();
    this.listeners = [];
  }
}

function hasWandLight(effects: readonly VisibleEffect[]): boolean {
  for (const e of effects) if (e.kind === "lucerna") return true;
  return false;
}

/** Words for a refused cast (`bound`: we are petrified, Spells L20). */
export function spellErrorText(spellId: number, code: SpellErrorCode, zone: Zone | null, bound = false): string {
  const spell = spellById(spellId);
  const name = spell?.name ?? "That spell";
  switch (code) {
    case "not_learned":
      return `You have not learned ${name} yet.`;
    case "not_here":
      return zone?.kind === "arena" ? "Only dueling casts work on the dueling stage." : `${name} can't be cast here.`;
    case "cooldown":
      return `${name} needs a moment before you cast it again.`;
    case "bad_target":
      return `${name} can't be cast on that.`;
    case "too_far":
      return "That is out of reach. Step a little closer.";
    case "busy":
      // Spells L20: bound stiff, only the Unweaving works; a friend just hindered is immune for a few seconds.
      if (bound) return "You are bound stiff: only the Unweaving works now.";
      if (spell?.kind === "bind" || spell?.kind === "slow" || spell?.kind === "pull") return "They were hindered a moment ago; give them a few seconds.";
      return "You are in the middle of something else.";
    case "rate_limited":
      return "Slow down a little: your wand needs a breather.";
    // Spells L20.
    case "not_friend":
      return `${name} works only on a friend.`;
    case "needs_target":
      return "Select a friend first (Tab).";
    case "unavailable":
      return `${name} can't reach them right now: they are sitting, flying, trading, dueling, in class or already moving.`;
    case "no_path":
      return "There is no safe way to pull them to you from there (a ledge, a doorway, the moving stairs or a wall), or they are close already.";
  }
}
