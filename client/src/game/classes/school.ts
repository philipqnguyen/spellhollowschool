// School life in the running game, in one place for
// game.ts: the game clock (the server's time, day and night over the
// castle, the HUD's timetable), levels (the HUD badge, the level-up
// flourish, levels on nameplates, required levels in the wardrobe),
// classes (controller.ts), NPCs (speech bubbles, demonstrations), spells
// outside duels (game/spells) and the wayfinding trail (the
// "Monstra Viam" charm: game/wayfinding, started from the timetable or the
// Monstra Viam window) with the first-day tour and its one-time class prompt
// (game/wayfinding/tour.ts). game.ts routes server messages here, calls
// `update` before each frame renders and `overlay` after, and asks it for
// nameplate titles.
//
// Messages it only watches (Welcome, Pong, Snapshot, ZoneChanged) are left
// for the rest of the game too; its own (Progress, LevelChanged, Spellbook,
// spells, NPC events and every class message) stop here.

import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { Scene } from "@babylonjs/core/scene";
import { authedRequest } from "../../api";
import { audio } from "../../audio";
import type { Quality } from "../../settings";
import type { BoardClass } from "../../ui/house-cup";
import { LevelBadge, LevelUpFlourish } from "../../ui/progression";
import { levelTitle, levelUp } from "../../ui/progression-state";
import { ClassBar } from "../../ui/class-bar";
import { ScheduleChip } from "../../ui/schedule";
import { classBar, clockText, floorName, formatCountdown, inMinutes, onGrounds, placeText, scheduleView, slotAt, timetableGroups, type ClassBarInput, type ClassSlotState, type PeriodGroup, type ScheduleView } from "../../ui/schedule-state";
import { LoreBook } from "../../ui/lore";
import { ClassToasts, MonstraViamWindow, TrailChip } from "../../ui/wayfinding";
import { distanceText } from "../wayfinding/trail-path";
import { Wayfinding } from "../wayfinding/wayfinding";
import type { StairEvents } from "../castle/stairs-state";
import { promptDoor, promptText, standsAt, Tour, TOUR_ASK, TOUR_DONE, TOUR_INTRO, TOUR_SKIPPED, TOUR_VIA_LANDMARK, tourKey, tourStops, type TourStep } from "../wayfinding/tour";
import { SpellbookWindow } from "../../ui/spellbook";
import type { MapMark } from "../../ui/map";
import { h } from "../../ui/dom";
import type { House, KeyPlaceView, NpcEvent, Period, Progress, ServerMessage, TimetableSlot, VisibleEffect, Zone } from "../../wasm/sim_wasm";
import { ServerClock } from "../clock/clock";
import { applySky, DayNight } from "../clock/daynight";
import { CLASSES, SchoolClock, type ScheduledClass } from "../clock/schedule";
import type { DormRoom } from "../dorm/room";
import { BUBBLE_ABOVE_PLATE, SpeechBubbles } from "../npc/bubbles";
import type { SightTest } from "../../ui/plate-sight";
import { SchoolRoster } from "../npc/roster";
import { SpellController } from "../spells/controller";
import { findSpell, gestureById, LEVITATION, spellById, spellName, spellRgb, WAND_LIGHT } from "../spells/data";
import type { WandHolder } from "../spells/wandlight";
import type { WorldData } from "../world";
import { SpellVfx } from "../vfx/spell-vfx";
import { ClassController } from "./controller";
import { DoorLines } from "./door-lines";
import { ClassProps } from "./props";

/** How often the timetable and the lighting are brought up to date (s). */
const SCHEDULE_INTERVAL = 0.5;
const LIGHTING_INTERVAL = 1;
/** Standing this close to a class door (m) makes it "the door you tried" for a refusal. */
const DOOR_NEAR_M = 4;
const GOLD: readonly [number, number, number] = [1, 0.85, 0.42];
const DEMO_GOLD: readonly [number, number, number] = [1, 0.83, 0.42];
/** A tour stop's line stays up this long (ms). */
const TOUR_TOAST_MS = 12_000;
/** The toast that asks "Skip the tour?" (Esc during the tour). */
const TOUR_ASK_KEY = "tour:ask";
/** The notice board lists the classes of this many slots to come. */
const BOARD_SLOTS = 2;

export interface SchoolHubOptions {
  scene: Scene;
  canvas: HTMLCanvasElement;
  camera: Camera;
  hudRoot: HTMLElement;
  /** The HUD's action buttons (the Monstra Viam button goes there). */
  hudActions: HTMLElement;
  /** Our house: the trail walks the castle as this house does. */
  house: House;
  /** The HUD's top-right stack on screen (px; the strip below the name is part of it); null before layout. */
  topBar: () => { left: number; bottom: number } | null;
  /** Where the level and clock strip goes: the HUD's top-right stack, under the name and counts (Hud.corner). */
  stripHost: HTMLElement;
  quality: Quality;
  /** Our house's display name. */
  houseName: string;
  /** Our character's name (the door lines already said are kept per character). */
  characterName: string;
  /** The castle's world (landmarks for "where", class doors). */
  castle: WorldData;
  send: (frame: Uint8Array) => void;
  /** A line in the chat log. */
  notify: (text: string) => void;
  /** An NPC's line in the chat log, as spoken. */
  say: (from: string, text: string) => void;
  selfId: () => number;
  /** The zone shown (null while one loads). */
  zone: () => Zone | null;
  /** Whether the instance room `layout` is open to the sky (`instances/school.json` `open_sky`). */
  openSky: (layout: string) => boolean;
  rttMs: () => number | null;
  effectsOf: (id: number) => readonly VisibleEffect[];
  /** Everyone drawn this frame, nearest the camera first (ourselves included). */
  drawn: () => readonly WandHolder[];
  poseOf: (id: number) => WandHolder | null;
  nameOf: (id: number) => string | null;
  headHeight: (id: number) => number;
  plateHeight: (id: number) => number;
  wandTip: (id: number, out: Vector3) => boolean;
  feet: () => [number, number, number];
  look: () => [number, number];
  room: () => DormRoom;
  liftFurniture: (itemId: number, dy: number) => void;
  /** Our own cast: play the wand gesture on our character. */
  onSelfCast: () => void;
  /** Spells L20 (SOCIAL): the character selected in the world (Tab/E), or null. */
  selected: () => number | null;
  /** Spells L20 (SOCIAL): whether character `id` is a friend (only for the refusal's words; the server decides). */
  isFriend: (id: number) => boolean;
  /** Our level changed (the wardrobe shows required levels against it). */
  onLevel: (level: number) => void;
  /** Projection for the speech bubbles: the scene's view-projection and the canvas size. */
  viewProjection: () => ArrayLike<number>;
  canvasSize: () => [number, number];
}

export class SchoolHub {
  readonly clock = new ServerClock();
  private readonly school = new SchoolClock();
  private readonly daynight: DayNight;
  private readonly roster = new SchoolRoster();
  private readonly bubbles: SpeechBubbles;
  private readonly badge: LevelBadge;
  private readonly flourish = new LevelUpFlourish();
  private readonly chip: ScheduleChip;
  /** The class bar: "Classes starting in ..." beside the clock, and its panel of the slot's classes. */
  private readonly classBar: ClassBar;
  /** The wayfinding trail (null if the castle's graph could not be built). */
  private readonly way: Wayfinding | null;
  private readonly trailChip: TrailChip;
  private readonly monstraViam: MonstraViamWindow;
  private readonly toasts = new ClassToasts();
  private readonly lore = new LoreBook();
  private readonly slots: TimetableSlot[];
  /** The latest schedule line of each class (for the Monstra Viam window). */
  private readonly views = new Map<string, ScheduleView>();
  private readonly book: SpellbookWindow;
  /** The level badge, the clock chip and the class bar, in a strip just below the name and counts (top right). */
  private readonly strip = h("div", { class: "school-strip" });
  /** The strip's bottom edge (px): speech bubbles keep below it. */
  private stripBottom = 96;
  private readonly classes: ClassController;
  readonly spells: SpellController;
  private progress: Progress | null = null;
  /** Our level as the server last said (1 before the first `Progress`). */
  get level(): number {
    return this.progress?.level ?? 1;
  }
  /** Whether a `Progress` came since `Welcome` (the first one is not a level-up). */
  private progressSeen = false;
  private scheduleTimer = 0;
  private lightTimer = 0;
  /** The class the student was in at the last update (entering one clears the class prompt). */
  private wasInClass: string | null = null;
  /** The sessions whose door lines were said (once each, across sign-ins: door-lines.ts). */
  private readonly doorLines: DoorLines;
  /** The class room's animated props (Herbology's plants, the paddock's creatures). */
  private readonly props: ClassProps;
  private readonly where = new Map<string, string>();
  /** The surface under each key place (for the map's floors). */
  private readonly markSurfaces = new Map<string, string | undefined>();
  private readonly plates = new Map<number, { name: string; level: number; text: string }>();
  /** The first-day tour and its one-time class prompt. */
  private readonly tour = new Tour();
  /** The timetable by period as last shown (the notice board's next classes). */
  private groups: PeriodGroup[] = [];
  /** The duel's spell VFX for class drills (Defence; made on first use, one draw call). */
  private classVfx: SpellVfx | null = null;
  /** The class VFX drew last frame (it runs once more after its last effect, to clear). */
  private classVfxLive = false;

  constructor(private readonly opts: SchoolHubOptions) {
    this.chip = new ScheduleChip((id) => this.pointToClass(id));
    this.classBar = new ClassBar({ onWay: (id) => this.pointToClass(id), onTimetable: () => this.chip.toggle(true) });
    this.doorLines = new DoorLines(opts.characterName);
    this.props = new ClassProps(opts.scene, opts.hudRoot);
    this.slots = this.school.timetable();
    let way: Wayfinding | null = null;
    try {
      way = new Wayfinding({ scene: opts.scene, layoutText: opts.castle.layoutText, house: opts.house, onArrive: (p) => this.arrived(p) });
    } catch (e) {
      console.error("The Monstra Viam trail is unavailable:", e);
    }
    this.way = way;
    this.trailChip = new TrailChip(() => this.endTrail(true));
    this.monstraViam = new MonstraViamWindow({
      places: () => this.way?.places ?? [],
      current: () => this.way?.destination?.id ?? null,
      classNote: (id) => this.classNote(id),
      floorOf: (p) => this.floorOf(p),
      onChoose: (id) => this.startTrail(id),
      onStop: () => this.endTrail(true),
    });
    this.daynight = new DayNight(opts.scene);
    this.bubbles = new SpeechBubbles(opts.hudRoot);
    this.badge = new LevelBadge(() => this.book.toggle());
    this.book = new SpellbookWindow({ onCast: (s) => this.spells.cast(s) });
    this.classes = new ClassController({
      hudRoot: opts.hudRoot,
      clock: this.clock,
      send: opts.send,
      notify: opts.notify,
      houseName: opts.houseName,
      nextLesson: (id) => this.nextLesson(id),
      doorClass: () => this.doorClass(),
      subjectName: (subject) => this.school.subjectName(subject),
      fx: {
        desk: () => this.desk(),
        burst: (x, y, z, rgb, count, radius) => this.spells.fx.burst(x, y, z, rgb, performance.now(), count, radius),
      },
      poseOf: (id) => opts.poseOf(id),
      self: () => opts.poseOf(opts.selfId()),
      vfx: () => this.ensureClassVfx(),
      scene: opts.scene,
      // The level-up flourish never covers the results card: it waits for the card to close.
      onResultsOpen: (open) => this.flourish.hold(open),
    });
    this.spells = new SpellController({
      scene: opts.scene,
      canvas: opts.canvas,
      camera: opts.camera,
      hudRoot: opts.hudRoot,
      quality: opts.quality,
      send: opts.send,
      notify: opts.notify,
      selfId: opts.selfId,
      zone: opts.zone,
      effectsOf: opts.effectsOf,
      drawn: opts.drawn,
      poseOf: opts.poseOf,
      headHeight: opts.headHeight,
      wandTip: opts.wandTip,
      feet: opts.feet,
      look: opts.look,
      room: opts.room,
      liftFurniture: opts.liftFurniture,
      onSelfCast: opts.onSelfCast,
      // Spells L20 (SOCIAL).
      selected: opts.selected,
      isFriend: opts.isFriend,
    });
    this.spells.onChange(() => this.refreshBook());
    // Under the name and counts at the top right (in the HUD layer, first in the HUD, so toasts and windows cover it).
    this.strip.append(this.badge.root, this.chip.button, this.classBar.button, this.classBar.live);
    opts.stripHost.append(this.strip);
    opts.hudRoot.append(this.chip.root, this.classBar.root, this.book.root, this.flourish.root, this.trailChip.root, this.monstraViam.root, this.toasts.root, this.lore.root);
    opts.hudActions.append(this.book.button);
    if (this.way) opts.hudActions.append(this.monstraViam.button);
    this.updateSchedule();
  }

  // ---------------------------------------------------------------- messages

  /** Server messages: true when `msg` was school business only (stop routing it). */
  onMessage(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "welcome":
        this.clock.welcome(msg.unixMs);
        this.roster.clear();
        this.plates.clear();
        this.bubbles.clear();
        this.classes.welcome();
        this.progressSeen = false;
        this.scheduleTimer = 0;
        this.lightTimer = 0;
        return false;
      case "pong":
        this.clock.pong(msg.serverTimeMs, this.opts.rttMs());
        return false;
      case "snapshot":
        if (msg.entered.length || msg.left.length) this.roster.snapshot(msg.entered, msg.left);
        return false;
      case "zoneChanged":
        this.roster.clear();
        this.plates.clear();
        this.bubbles.clear();
        return false;
      case "progress":
        this.onProgress(msg.progress, msg.gained);
        return true;
      case "levelChanged":
        this.roster.levelChanged(msg.id, msg.level, msg.effectsVersion);
        if (msg.id !== this.opts.selfId()) this.burstAt(msg.id, GOLD, true);
        return true;
      case "spellbook":
        this.spells.onMessage(msg);
        if (msg.new.length) {
          const names = msg.new.map((id) => spellName(id));
          this.opts.notify(`You learned ${listWords(names)}! Open your spellbook (Y) to see ${names.length > 1 ? "them" : "it"}.`);
        }
        return true;
      case "spellCast":
      case "levitating":
      case "spellError":
      // Spells L20 (SOCIAL): Aperio's spots, to its caster.
      case "revealed":
        return this.spells.onMessage(msg);
      case "npcEvent":
        this.npcEvent(msg.id, msg.event);
        return true;
      default:
        return this.classes.onMessage(msg);
    }
  }

  private onProgress(p: Progress, gained: Extract<ServerMessage, { type: "progress" }>["gained"]): void {
    const before = this.progress?.level ?? null;
    this.progress = p;
    this.badge.update(p);
    if (gained) this.badge.gained(gained);
    if (this.progressSeen && before !== null) {
      const up = levelUp(before, p.level);
      if (up) {
        this.flourish.show(up);
        this.opts.notify(`You reached level ${up.level}!${up.slots !== null ? ` Your dueling loadout holds ${up.slots} spells now.` : ""}`);
        audio.stinger("stinger.level_up");
        this.burstAt(this.opts.selfId(), GOLD, true);
      }
    }
    this.progressSeen = true;
    this.opts.onLevel(p.level);
    this.refreshBook();
  }

  private npcEvent(id: number, ev: NpcEvent): void {
    switch (ev.kind) {
      case "say": {
        const name = this.opts.nameOf(id) ?? "Professor";
        this.bubbles.say(id, name, ev.text, performance.now());
        this.opts.say(name, ev.text);
        break;
      }
      case "demonstrate": {
        this.classes.demonstrate(ev.gesture, ev.spell, ev.durationMs);
        const pose = this.opts.poseOf(id);
        const g = gestureById(ev.gesture);
        if (pose && g) {
          const spell = ev.spell !== null ? spellById(ev.spell) : undefined;
          const right = this.spells.fx.cameraRight;
          const fx = Math.sin(pose.yaw);
          const fz = Math.cos(pose.yaw);
          const head = this.opts.headHeight(id);
          this.spells.fx.trail(g.points, pose.x + fx * 0.7, pose.y + head * 0.8, pose.z + fz * 0.7, right.x, right.z, 0.5, spell ? spellRgb(spell) : DEMO_GOLD, ev.durationMs, performance.now());
          audio.playAt("spell.trail", [pose.x + fx * 0.7, pose.y + head * 0.8, pose.z + fz * 0.7], { owner: id });
        }
        break;
      }
      case "emote":
        break; // the server plays it on the character
    }
  }

  /** Glints round `id`'s middle (a level-up), with their sound when `sound`. */
  private burstAt(id: number, rgb: readonly [number, number, number], sound = false): void {
    const pose = this.opts.poseOf(id);
    if (!pose) return;
    const y = pose.y + this.opts.headHeight(id) * 0.55;
    this.spells.fx.burst(pose.x, y, pose.z, rgb, performance.now(), 18, 0.9);
    if (sound) audio.playAt("level.up", [pose.x, y, pose.z], { owner: id });
  }

  /** The game period now (an NPC's lines by time of day), or null before the server's time is known. */
  period(): Period | null {
    return this.clock.synced ? this.school.period(this.clock.now()) : null;
  }

  /** NPCs in view (open-castle residents, professors, duelists): entity id and data id. */
  npcsInView(): IterableIterator<[number, string]> {
    return this.roster.npcs();
  }

  /** The NPC data id of entity `id` (`EntityInfo.npc`), or null for players. */
  npcOf(id: number): string | null {
    return this.roster.npc(id);
  }

  /** Opens the lore book (an NPC's lore gift). */
  openLore(): void {
    this.lore.toggle(true);
  }

  /** The game hour now (0-23), or null before the server's time is known (the clock tower's bell). */
  gameHour(): number | null {
    if (!this.clock.synced) return null;
    return Math.floor(this.school.gameSeconds(this.clock.now()) / 3600) % 24;
  }

  // ---------------------------------------------------------------- zones

  /** The game is leaving the zone shown. */
  leaving(): void {
    this.spells.leaving();
    this.bubbles.clear();
    this.props.clear();
  }

  /** Lights the castle for the game hour (clock/daynight.ts). */
  private applyLight(force: boolean): void {
    const now = this.clock.now();
    this.daynight.apply(this.school.gameSeconds(now) / 3600, this.school.daylight(now), force);
  }

  /** A room open to the sky (Astronomy's tower top): its sky dome for the hour. */
  private applyOpenSky(): void {
    const z = this.opts.zone();
    if (!z || z.kind === "castle" || !this.opts.openSky(z.layout)) return;
    const now = this.clock.now();
    applySky(this.opts.scene, this.school.gameSeconds(now) / 3600, this.school.daylight(now));
  }

  /** A zone is shown (a different one from before), with its world (a class room's landmarks place its props). */
  zoneShown(zone: Zone, world?: WorldData): void {
    this.classes.zoneShown(zone, world);
    const stage = zone.kind === "class" ? this.classes.stage : null;
    if (stage && world) this.props.load(stage.props, world.sim.landmarks());
    else this.props.clear();
    // zones-scene.ts restored the full-day look outside: light it for the hour again.
    if (zone.kind === "castle") this.applyLight(true);
    else this.applyOpenSky();
    this.scheduleTimer = 0;
  }

  setQuality(q: Quality): void {
    this.spells.setQuality(q);
  }

  // ---------------------------------------------------------------- input

  handleKey(e: KeyboardEvent): boolean {
    return this.spells.handleKey(e) || this.book.handleKey(e) || this.chip.handleKey(e) || this.classBar.handleKey(e) || this.classes.handleKey(e) || this.lore.handleKey(e) || (!!this.way && this.monstraViam.handleKey(e));
  }

  /** Esc ends the trail: asked last, after every window has had the key. */
  handleTrailKey(e: KeyboardEvent): boolean {
    // X skips the first-day tour; Esc only asks (and a second Esc puts the question away).
    if (this.tour.running) {
      const act = tourKey(e, this.toasts.isShown(TOUR_ASK_KEY));
      if (act === "skip") this.skipTour();
      else if (act === "ask") {
        audio.play("ui.toast");
        this.toasts.show(TOUR_ASK_KEY, TOUR_ASK, performance.now() + TOUR_TOAST_MS, () => this.skipTour(), "Skip the tour");
      }
      else if (act === "dismiss") this.toasts.clear(TOUR_ASK_KEY);
      if (act) return true;
    }
    if (e.key === "Escape" && this.way?.destination) {
      this.endTrail(true);
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- the trail

  /** Starts the Monstra Viam trail to key place `placeId` (another destination replaces the trail, and ends the tour). */
  startTrail(placeId: string): void {
    if (this.tour.running && this.tour.stop !== placeId) this.skipTour(false);
    this.trailTo(placeId, true);
  }

  private trailTo(placeId: string, announce: boolean, via: readonly [number, number, number] | null = null): void {
    const way = this.way;
    if (!way) return;
    const place = way.start(placeId, this.opts.feet(), via);
    if (!place) {
      this.opts.notify("The charm cannot find that place.");
      return;
    }
    this.chip.toggle(false);
    this.classBar.toggle(false);
    const st = way.status();
    const how = st && !st.lost ? `follow the light (${distanceText(st.remainingM)}).` : "the trail will show once you are back in the castle.";
    if (announce) this.opts.notify(`Monstra Viam! The way to the ${place.name}: ${how} Press Esc to end it.`);
    this.refreshTrailChip();
  }

  // ---------------------------------------------------------------- the first-day tour

  /** Once in game: asks the server how far the tour went (it begins once we stand in the castle). */
  async startTour(): Promise<void> {
    if (!this.way) return;
    try {
      const r = await authedRequest<{ step?: number }>("GET", "/characters/me/tour");
      this.tour.known(r.step ?? 2);
    } catch (e) {
      console.warn("[tour] unavailable:", e);
    }
  }

  /** Sends the tour's new step (it only rises; the answer is the server's). */
  private saveTour(step: TourStep): void {
    void authedRequest<{ step?: number }>("POST", "/characters/me/tour", { step })
      .then((r) => this.tour.known(r.step ?? step))
      .catch((e) => console.warn("[tour] could not save the step:", e));
  }

  private beginTour(): void {
    const places = this.way?.places ?? [];
    const first = this.tour.begin(tourStops(places));
    if (!first) {
      this.saveTour(1);
      return;
    }
    this.opts.notify(TOUR_INTRO);
    audio.play("ui.toast");
    this.toasts.show("tour:intro", TOUR_INTRO, performance.now() + TOUR_TOAST_MS, null);
    // New characters arrive on the front steps and walk in to the first stop (said at once only at the counter).
    const at = places.find((p) => p.id === first);
    if (at && standsAt(at.pos, this.opts.feet()) && this.tourArrived(at)) return;
    // In through the front doors (from inside the castle the trail goes direct).
    const doors = this.opts.castle.sim.landmarks().find((l) => l.id === TOUR_VIA_LANDMARK);
    this.trailTo(first, false, doors ? doors.pos : null);
  }

  /** X, Esc, the chip's X or another destination: the tour ends here (step 1: the class prompt is still due). */
  private skipTour(say = true): void {
    if (!this.tour.skip()) return;
    this.way?.stop();
    this.toasts.clear("tour:");
    if (say) this.opts.notify(TOUR_SKIPPED);
    this.saveTour(1);
    this.scheduleTimer = 0; // a door already open may take the class prompt now
    this.refreshTrailChip();
    this.monstraViam.refresh();
  }

  /** The tour arrived at a stop: say what it is and lead on, or end it. True when it was the tour's stop. */
  private tourArrived(p: KeyPlaceView): boolean {
    const a = this.tour.arrived(p.id, p.group);
    if (!a) return false;
    const line = a.next ? a.line : `${a.line} ${TOUR_DONE}`;
    this.opts.notify(line);
    this.toasts.clear("tour:stop");
    audio.play("ui.toast");
    this.toasts.show(`tour:stop:${p.id}`, line, performance.now() + TOUR_TOAST_MS, null);
    if (a.next) this.trailTo(a.next, false);
    else this.saveTour(1);
    this.scheduleTimer = 0; // a door already open may take the class prompt now
    return true;
  }

  /** Whether `id` is an NPC (E on one does what E always did; NPCs have no card). */
  isNpc(id: number): boolean {
    return this.roster.npc(id) !== null;
  }

  /** A subject's name ("History of Magic"), for the player card. */
  subjectName(id: string): string {
    try {
      return this.school.subjectName(id);
    } catch {
      return "";
    }
  }

  /** Aiming Elevara (a click on the world is the spell's). */
  get aiming(): boolean {
    return this.spells.aiming;
  }

  /** Characters look at their target: our class's professor (null: none) and when we last answered a beat in class (`performance.now()` ms). */
  get answering(): { professor: number | null; at: number } {
    return { professor: this.classes.professor, at: this.classes.answeredAt };
  }

  /** The house's next classes for the notice board: the classes of the next slots, with their status. */
  boardClasses(): BoardClass[] {
    const out: BoardClass[] = [];
    let slots = 0;
    for (const g of this.groups) {
      for (const slot of g.slots) {
        if (slots >= BOARD_SLOTS) return out;
        slots++;
        for (const c of slot.classes) out.push({ classId: c.classId, name: c.name, at: slot.at, status: c.status, where: c.where });
      }
    }
    return out;
  }

  /** The key places and class doors for the map (each class door with its next lesson). */
  mapMarks(): MapMark[] {
    const sim = this.opts.castle.sim;
    return (this.way?.places ?? []).map((p) => {
      let surfaceId = this.markSurfaces.get(p.id);
      if (surfaceId === undefined && !this.markSurfaces.has(p.id)) {
        surfaceId = sim.surfaceIdAt(p.pos[0], p.pos[2], p.pos[1]);
        this.markSurfaces.set(p.id, surfaceId);
      }
      const note = p.class ? this.classNote(p.class) : "";
      return { name: p.name, note: note || undefined, x: p.pos[0], z: p.pos[2], surfaceId, kind: p.group === "class" ? "class" : "place" };
    });
  }

  /** Key place `id`'s spot on the castle map (a class door, a dormitory door, the Dueling Club), or null. */
  keyPlaceSpot(id: string): { x: number; y: number; z: number; surfaceId: string | undefined } | null {
    const p = this.way?.places.find((x) => x.id === id);
    if (!p) return null;
    let surfaceId = this.markSurfaces.get(p.id);
    if (surfaceId === undefined && !this.markSurfaces.has(p.id)) {
      surfaceId = this.opts.castle.sim.surfaceIdAt(p.pos[0], p.pos[2], p.pos[1]);
      this.markSurfaces.set(p.id, surfaceId);
    }
    return { x: p.pos[0], y: p.pos[1], z: p.pos[2], surfaceId };
  }

  /** The trail to class `classId`'s door. */
  pointToClass(classId: string): void {
    const p = this.way?.classPlace(classId);
    if (p) this.startTrail(p.id);
  }

  private endTrail(byPlayer: boolean): void {
    if (byPlayer && this.tour.running) {
      this.skipTour();
      return;
    }
    if (this.way?.stop() && byPlayer) this.opts.notify("The Wayfinder Charm's trail fades.");
    this.refreshTrailChip();
    this.monstraViam.refresh();
  }

  private arrived(p: KeyPlaceView): void {
    if (!this.tourArrived(p)) this.opts.notify(`You have arrived: ${p.name}.`);
    this.refreshTrailChip();
    this.monstraViam.refresh();
  }

  private refreshTrailChip(): void {
    const st = this.way?.status();
    if (!st) {
      this.trailChip.show(null);
      return;
    }
    const inCastle = this.opts.zone()?.kind === "castle";
    this.trailChip.show({ name: st.place.name, distance: st.lost ? "" : distanceText(st.remainingM), waiting: !inCastle, tour: this.tour.running ? this.tour.label : undefined });
  }

  /** "door open", "in 12 min": a class door's lesson, for the Monstra Viam window. */
  private classNote(classId: string): string {
    const v = this.views.get(classId);
    if (!v) return "";
    switch (v.kind) {
      case "join":
        return "door open";
      case "attending":
        return "you are here";
      case "running":
        return "in session";
      case "next":
        return `next lesson ${inMinutes(v.countdownMs)}`;
    }
  }

  private floorOf(p: KeyPlaceView): string {
    const sim = this.opts.castle.sim;
    const level = sim.levelAt(p.pos[0], p.pos[2], p.pos[1]);
    // The Owl Loft is a tower's upper floor, but the place is on the grounds.
    return level === undefined ? "" : floorName(level, p.group === "grounds" || onGrounds(sim.surfaceIdAt(p.pos[0], p.pos[2], p.pos[1])));
  }

  /** The staircases' events for the Monstra Viam trail (castle/stairs.ts). */
  onStairs(e: Pick<StairEvents, "changed" | "swingStarted" | "plan">, riding: boolean): void {
    this.way?.onStairs(e, riding);
  }

  /** Dust from the staircase (the spell VFX's burst, in a palette stone colour). */
  dust(x: number, y: number, z: number, rgb: readonly [number, number, number]): void {
    this.spells.fx.burst(x, y, z, rgb, performance.now(), 10, 0.9);
  }

  /** Chat commands: /cast <spell>, /light, /levitate, /spells, /timetable, /lore, /monstraviam [place | off]. True if `line` was one. */
  command(line: string): boolean {
    const [cmd, ...rest] = line.trim().split(/\s+/);
    switch (cmd?.toLowerCase()) {
      case "/cast": {
        const spell = findSpell(rest.join(" "));
        if (spell) this.spells.cast(spell);
        else this.opts.notify(rest.length ? `There is no spell called “${rest.join(" ")}”. Your spellbook (Y) lists them.` : "Cast which spell? For example /cast and the spell's name.");
        return true;
      }
      case "/light":
        this.spells.cast(WAND_LIGHT);
        return true;
      case "/levitate":
        this.spells.cast(LEVITATION);
        return true;
      case "/spells":
      case "/spellbook":
        this.book.toggle(true);
        return true;
      case "/schedule":
      case "/timetable":
        this.chip.toggle(true);
        return true;
      case "/lore":
        this.lore.toggle(true);
        return true;
      case "/monstraviam":
      case "/way":
      case "/findmyway": {
        if (!this.way) return false;
        const want = rest.join(" ").trim().toLowerCase();
        if (!want) {
          this.monstraViam.toggle(true);
          return true;
        }
        if (want === "off" || want === "stop") {
          this.endTrail(true);
          return true;
        }
        const p = this.way.places.find((x) => x.name.toLowerCase().includes(want) || x.id === want);
        if (p) this.startTrail(p.id);
        else this.opts.notify(`The Wayfinder Charm knows no place called “${rest.join(" ")}”. Press F for the list.`);
        return true;
      }
      default:
        return false;
    }
  }

  /** The daylight the castle is lit for (0 at night, 1 by day; 1 before the clock is known). The night's mist. */
  get daylight(): number {
    const d = this.daynight.daylight;
    return d < 0 ? 1 : d;
  }

  // ---------------------------------------------------------------- nameplates

  /** The nameplate text for a character in view: its name and level. */
  plateName(id: number, name: string): string {
    const level = this.roster.npc(id) ? 0 : this.roster.level(id);
    const held = this.plates.get(id);
    if (held && held.name === name && held.level === level) return held.text;
    const title = levelTitle(level);
    const text = title ? `${name} · ${title}` : name;
    this.plates.set(id, { name, level, text });
    return text;
  }

  // ---------------------------------------------------------------- per frame

  /** Before the scene renders: spells, the lesson, the clock and the lighting. */
  update(dt: number): void {
    this.spells.update(dt);
    this.updateClass(dt);
    this.props.update(this.classes.stage, this.clock.now());
    // Review fix: the one-time class prompt points at a door; inside a class it only covers
    // the lesson, so entering one puts it away (tour toasts stay).
    const inClass = this.classes.inClass;
    if (inClass !== this.wasInClass) {
      if (inClass !== null) this.toasts.clear("prompt:");
      this.wasInClass = inClass;
    }
    if (this.way) {
      const castle = this.opts.zone()?.kind === "castle";
      if (this.tour.shouldStart(castle)) this.beginTour();
      this.way.update(dt, castle ? this.opts.feet() : null, this.opts.camera);
      if (this.way.destination) this.refreshTrailChip();
    }
    this.scheduleTimer -= dt;
    if (this.scheduleTimer <= 0) {
      this.scheduleTimer = SCHEDULE_INTERVAL;
      this.updateSchedule();
      this.refreshBook(); // cheap: the window only redraws while open, and only what changed
      this.toasts.tick(performance.now());
      this.monstraViam.refresh();
    }
    this.lightTimer -= dt;
    if (this.lightTimer <= 0) {
      this.lightTimer = LIGHTING_INTERVAL;
      if (this.opts.zone()?.kind === "castle") this.applyLight(false);
      else this.applyOpenSky();
    }
  }

  /** The lesson, with the class drill's VFX begun before and ended after it (only while a drill runs or its effects fade). */
  private updateClass(dt: number): void {
    if (!this.classes.drawing && !this.classVfxLive) {
      this.classes.update(dt);
      return;
    }
    const vfx = this.ensureClassVfx();
    vfx.begin(dt);
    this.classes.update(dt);
    vfx.end(this.opts.camera);
    this.classVfxLive = this.classes.drawing || vfx.busy;
  }

  private ensureClassVfx(): SpellVfx {
    this.classVfx ??= new SpellVfx(this.opts.scene, { capacity: 160, particles: 160 });
    return this.classVfx;
  }

  /** The level a speaker must be seen past for its bubble to show (plate-sight-ray.ts). */
  setSightTest(test: SightTest | null): void {
    this.bubbles.setSightTest(test);
  }

  /** After the scene renders: speech bubbles over their speakers. */
  overlay(): void {
    const [w, h] = this.opts.canvasSize();
    const eye = this.opts.camera.globalPosition;
    this.bubbles.update(
      (id) => {
        const p = this.opts.poseOf(id);
        return p ? { x: p.x, y: p.y + this.opts.plateHeight(id) + BUBBLE_ABOVE_PLATE, z: p.z } : null;
      },
      this.opts.viewProjection(),
      eye,
      w,
      h,
      performance.now(),
      this.stripBottom,
    );
    this.props.overlay(this.classes.stage, this.opts.viewProjection(), w, h, this.clock.now());
  }

  /**
   * The strip sits in the HUD's top-right stack (its stylesheet places it);
   * speech bubbles keep below the stack, and the timetable opens just under it
   * at the right (schedule.css, by --hud-corner-bottom).
   */
  private placeStrip(): void {
    this.stripBottom = Math.round(this.opts.topBar()?.bottom ?? 96);
  }

  private updateSchedule(): void {
    this.placeStrip();
    const now = this.clock.now();
    const states = new Map<string, ClassSlotState>();
    const bar: ClassBarInput[] = [];
    const open: { classId: string; leftMs: number; name: string; where: string; session: number }[] = [];
    let joinMins = 0;
    let lessonMins = 0;
    for (const c of CLASSES) {
      let times;
      try {
        times = this.school.classTimes(c.id, now);
      } catch {
        continue; // a class this build's wasm does not know
      }
      const attending = this.classes.inClass === c.id ? this.classes.session : null;
      const view = scheduleView(c, times, now, this.whereOf(c), attending);
      this.views.set(c.id, view);
      bar.push({ id: c.id, name: c.name, where: view.where, times, attending: view.kind === "attending" });
      joinMins = Math.max(joinMins, Math.round(c.joinSecs / 60));
      lessonMins = Math.max(lessonMins, Math.round(c.durationSecs / 60));
      const slotOf = (startMs: number) => slotAt(this.slots, this.school.gameSeconds(startMs));
      states.set(c.id, {
        name: c.name,
        view,
        currentSlot: times.current ? slotOf(times.current.startMs) : null,
        nextSlot: slotOf(times.next.startMs) ?? -1,
        nextInMs: times.next.startMs - now,
      });
      if (view.kind === "join" && times.current) open.push({ classId: c.id, leftMs: view.countdownMs, name: c.name, where: view.where, session: times.current.session });
      // A door opening: the bell and one line in the chat for each session, when not already inside (the
      // class bar shows the doors open; there are no door toasts). The line waits until the server's time and
      // the zone are known; inside a class it is passed over. Each session's line is said once, across
      // reconnects and sign-ins too (door-lines.ts).
      const zone = this.opts.zone();
      if (view.kind === "join" && times.current && zone && this.clock.synced && this.doorLines.due(c.id, times.current.session)) {
        this.doorLines.said(c.id, times.current.session);
        if (zone.kind !== "class" && this.classes.inClass === null) {
          const text = `${c.name} is starting in the ${view.where}! Its door admits students for the next ${formatCountdown(view.countdownMs)}.`;
          // The school bell: a class's door opens (heard anywhere, not inside a class).
          audio.play("class.bell");
          this.opts.notify(text);
          this.classBar.announce(text);
        }
      }
    }
    // The one-time prompt after the first-day tour (if due), for one open door.
    this.classPrompt(open);
    const period = this.school.period(now);
    const nextAt = this.school.nextPeriodAt(now);
    this.groups = timetableGroups(this.slots, states, this.school.gameSeconds(now));
    this.chip.update({
      clock: clockText(this.school.gameSeconds(now)),
      gameSecs: this.school.gameSeconds(now),
      nextPeriodGameSecs: this.school.gameSeconds(nextAt),
      period,
      nextPeriodMs: nextAt - now,
      nextPeriod: this.school.period(nextAt + 1),
      groups: this.groups,
      joinMins,
      lessonMins,
    });
    this.classBar.update(classBar(bar, now));
  }

  /**
   * The one-time "class starting" prompt after the tour, for a door opening now or
   * one already open (as the tour ends, or at login at step 1) while it stays open long enough to
   * get there. Shown, it is done whether followed or not.
   */
  private classPrompt(open: readonly { classId: string; leftMs: number; name: string; where: string; session: number }[]): void {
    if (!this.tour.promptDue || this.classes.inClass !== null || !this.clock.synced) return;
    const door = promptDoor(open.filter((d) => this.way?.classPlace(d.classId)));
    const place = door ? this.way?.classPlace(door.classId) : undefined;
    if (!door || !place || !this.tour.takePrompt()) return;
    this.saveTour(2);
    audio.play("ui.toast");
    this.toasts.show(`prompt:${door.classId}:${door.session}`, `${promptText(door.name)} to the ${door.where}.`, performance.now() + Math.min(door.leftMs, 45_000), () => this.startTrail(place.id), "Follow the trail");
  }

  /** In a classroom, the student's desk or cauldron: just in front of them at hand height. */
  private desk(): [number, number, number] | null {
    const me = this.opts.poseOf(this.opts.selfId());
    if (!me) return null;
    return [me.x + Math.sin(me.yaw) * 0.6, me.y + 0.95, me.z + Math.cos(me.yaw) * 0.6];
  }

  /** "Charms Classroom, third floor": the class's landmark in the castle. */
  private whereOf(c: ScheduledClass): string {
    let w = this.where.get(c.id);
    if (w === undefined) {
      const sim = this.opts.castle.sim;
      const lm = sim.landmarks().find((l) => l.id === c.landmark);
      w = lm ? placeText(lm.name, sim.levelAt(lm.pos[0], lm.pos[2], lm.pos[1]), onGrounds(sim.surfaceIdAt(lm.pos[0], lm.pos[2], lm.pos[1]))) : c.name;
      this.where.set(c.id, w);
    }
    return w;
  }

  /** "in 23:10" to `classId`'s next lesson. */
  private nextLesson(classId: string): string {
    try {
      const now = this.clock.now();
      return `in ${formatCountdown(this.school.classTimes(classId, now).next.startMs - now)}`;
    } catch {
      return "";
    }
  }

  /** The class whose door is right by us (castle only). */
  private doorClass(): string | null {
    if (this.opts.zone()?.kind !== "castle") return null;
    const feet = this.opts.feet();
    const doors = this.opts.castle.sim.interactables();
    for (const c of CLASSES) {
      const d = doors.find((i) => i.id === c.door);
      if (d && Math.hypot(d.pos[0] - feet[0], d.pos[2] - feet[2]) < DOOR_NEAR_M && Math.abs(d.pos[1] - feet[1]) < 2) return c.id;
    }
    return null;
  }

  private refreshBook(): void {
    const b = this.spells.spellbook;
    this.book.update({ progress: this.progress, learned: b.learned, loadout: b.loadout, slots: b.slots, lit: this.spells.wandLit, guardian: this.spells.guardianOut });
  }

  /** Lines for the F3 overlay. */
  perfLines(): [string, string][] {
    return [...this.spells.perfLines(), ...(this.way ? [this.way.perfLine()] : []), this.props.perfLine(), ...(this.classVfx ? [this.classVfx.perfLine("class vfx")] : []), ["clock", `server ${this.clock.synced ? "synced" : "not synced"}, daylight ${this.daynight.daylight.toFixed(2)}`]];
  }

  dispose(): void {
    this.spells.dispose();
    this.classes.dispose();
    this.props.dispose();
    this.classVfx?.dispose();
    this.bubbles.dispose();
    this.flourish.dispose();
    this.school.dispose();
    this.way?.dispose();
    this.toasts.clear();
    this.trailChip.root.remove();
    this.monstraViam.root.remove();
    this.monstraViam.button.remove();
    this.toasts.root.remove();
    this.lore.root.remove();
    this.strip.remove();
    this.chip.root.remove();
    this.classBar.dispose();
    this.book.root.remove();
    this.book.button.remove();
    this.flourish.root.remove();
  }
}

/** "A", "A and B", "A, B and C". */
function listWords(words: readonly string[]): string {
  if (words.length <= 1) return words[0] ?? "";
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}
