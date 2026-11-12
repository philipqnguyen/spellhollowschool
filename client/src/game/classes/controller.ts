// A class in the running game (docs/architecture.md, "Classes and progression"): from the
// moment the classroom door lets the player in (`ZoneChanged` into a class
// zone, then `ClassJoined`) until the bell sends everyone back to the
// corridor. It follows the lesson (lesson.ts), shows the lesson panel, the
// trace pad, the pick tray and the results card (ui/classes*), plays the
// professor's demonstrations on the pad, and runs the minigame. A beat is
// a wand gesture to trace or a pick: a prompt and 2-6 options.
//
//   LessonRound ─► beats on the pad (traces) or the tray (picks), the
//                  approach ring closing on each beat
//   trace       ─► the beat it belongs to (nearest untried, within the window)
//               ─► local preview with sim-core's own scorer (GestureScorer)
//               ─► TraceGesture to the server (unless the stroke was a slip:
//                  too short or too small to be meant, which would only
//                  waste the beat)
//   pick        ─► the beat it belongs to, the timing previewed with
//                  PickScorer (the client never knows the answer)
//               ─► PickAnswer to the server
//   BeatScored  ─► the server's verdict replaces the preview; a right pick
//                  plays the class's effect hook (game/classes/fx), a
//                  trace tells the class's drill (Defence's
//                  shield and spells) and a passed one its traced hook
//   ClassResult ─► the results card; Progress and Spellbook follow
//
// The Flying class: a beat is a ring to fly through
// (`BeatPrompt::Ring`). No pad or tray: the ring course (rings.ts) glows
// the next ring on the beat and a cue on the HUD counts it down. Nothing is
// sent: the server scores the crossing from the checked flight; our own
// prediction (`SimWorld.passesRing`) lights the ring at once.
//
// Beat times are the server's clock; the answer's offset is measured on the
// same clock (ServerClock), so the server's arrival check agrees.

import gesturesText from "../../../../data/classes/gestures.json?raw";
import type { Scene } from "@babylonjs/core/scene";
import { audio, isSoundId, type SoundId } from "../../audio";
import { RingCue } from "../../ui/flight";
import type { WorldData } from "../world";
import { RingCourseView } from "./rings";
import { encodeLeaveInstance, encodePickAnswer, encodeTraceGesture, GestureScorer, PickScorer, type ClassErrorCode, type ClassRewards, type GestureResult, type Grade, type ServerMessage, type TraceReject, type Zone } from "../../wasm/sim_wasm";
import { LessonPanel, ResultsCard } from "../../ui/classes";
import { TracePad, type PadPip, type PadScene } from "../../ui/classes-pad";
import { PickTray, type PickLook, type PickScene } from "../../ui/classes-pick";
import { recipeById } from "../../ui/crafting-content";
import { fetchLoreEntry } from "../../ui/lore";
import { fortuneBlurb, fortuneName } from "../../ui/potions-state";
import { defName } from "../../economy/items";
import type { ServerClock } from "../clock/clock";
import { scheduledClass } from "../clock/schedule";
import { gestureById, gestureByKey, isDueling, spellById, spellName, type GestureDef } from "../spells/data";
import classFiles from "./class-files";
import { CLASS_DRILLS, CLASS_HITS, CLASS_STAGES, CLASS_TRACED, type ClassDrill, type ClassFxContext, type ClassStage, type DrillContext, type DrillVfx, type FxPose } from "./fx";
import { isConstellation } from "./fx/astronomy";
import { closeupOf } from "./fx/divination";
import { APPROACH_MS, LessonState, type BeatState } from "./lesson";
import { classPicks, pickCue, resolvePick, sharedIcon, type ClassPicks } from "./picks";
import type { TraceRecorder } from "./trace";

/** The pad's warm-up strokes are scored as if this were the window (ms). */
const FREE_WINDOW_MS = 600;
/** How the lesson panel and the pad's words refresh (s); the pad itself draws every frame. */
const PANEL_INTERVAL = 0.25;
/** Rejections that mean the stroke was a slip, not an attempt: not sent, the beat stays open. */
const SLIPS: ReadonlySet<TraceReject> = new Set(["bad_sample_count", "too_small", "bad_duration"]);
/** The demonstration colour when the professor shows a gesture without a spell. */
const DEMO_GOLD = "#ffd36a";

export interface ClassControllerOptions {
  hudRoot: HTMLElement;
  clock: ServerClock;
  send: (frame: Uint8Array) => void;
  notify: (text: string) => void;
  /** Our house's display name, for the house points line. */
  houseName: string;
  /** The countdown to `classId`'s next lesson, in words ("in 23:10"). */
  nextLesson: (classId: string) => string;
  /** The class whose door the player stands at (for a refusal), if any. */
  doorClass: () => string | null;
  /** What the class effect hooks draw with (a right pick's reaction). */
  fx: Omit<ClassFxContext, "swap">;
  /** Where an entity (the professor) and we stand this frame, for the drills. */
  poseOf: (id: number) => FxPose | null;
  self: () => FxPose | null;
  /** The spell VFX a drill draws with this frame (begun before `update`, ended after). */
  vfx: () => DrillVfx;
  /** The skill track's display name ("Potions", "History of Magic"). */
  subjectName: (subject: string) => string;
  /** The scene the Flying class's ring course is drawn in. */
  scene: Scene;
  /** The results card opened (true) or closed (false): the level-up flourish waits for it. */
  onResultsOpen?: (open: boolean) => void;
}

/** A ring's mark stays on the cue this long (ms). */
const RING_FLASH_MS = 1400;

interface PendingTrace {
  gesture: GestureDef;
  /** The beat it is for (null: a warm-up or off-beat stroke, scored locally only). */
  beat: BeatState | null;
  round: number;
  windowMs: number;
  /** Start minus the beat time (ms, server clock). */
  offset: number;
  /** Ended by the pad moving on (`LessonState.cutDue`), not by the player. */
  cut?: boolean;
}

export class ClassController {
  private readonly scorer = new GestureScorer(gesturesText);
  private readonly pickScorer = new PickScorer(gesturesText);
  private readonly tray: PickTray;
  private picks: ClassPicks | null = null;
  private readonly panel: LessonPanel;
  private readonly pad: TracePad;
  private readonly results: ResultsCard;
  private lesson: LessonState | null = null;
  private classId: string | null = null;
  private pending: PendingTrace | null = null;
  /** The pad shows a practice beat's gesture (set each frame by `update`). */
  private practicePad = false;
  private watch: { gesture: GestureDef; colour: string; start: number; duration: number } | null = null;
  /** The last gesture demonstrated, shown faintly between demonstrations. */
  private lastDemo: GestureDef | null = null;
  private panelTimer = 0;
  private announced: number | null = null;
  /** The class's drill (Defence), and how its picks look. */
  private drill: ClassDrill | null = null;
  /** The class's animated props (Herbology's plants, the paddock's creatures). */
  private stageNow: ClassStage | null = null;
  private look: PickLook = {};
  private readonly drillCtx: DrillContext;
  /** The Flying class's ring course, its cue, and the class room's world (its rings, `passesRing`). */
  private readonly ringView: RingCourseView;
  private readonly ringCue = new RingCue();
  private ringWorld: WorldData | null = null;
  private ringFlash: { text: string; kind: string; until: number } | null = null;
  /** Sound: the ring whose glow was last heard. */
  private glowed: BeatState | null = null;
  /** Where we were last frame, and when (server clock): a flight segment tested against the open rings. */
  private lastSelf: { x: number; y: number; z: number; at: number } | null = null;

  constructor(private readonly opts: ClassControllerOptions) {
    const leave = () => opts.send(encodeLeaveInstance());
    this.panel = new LessonPanel(leave);
    this.results = new ResultsCard(leave, (open) => opts.onResultsOpen?.(open));
    this.ringView = new RingCourseView(opts.scene);
    this.pad = new TracePad({ onStart: (t) => this.traceStart(t), onEnd: (tr) => this.traceEnd(tr) });
    this.tray = new PickTray({ onChoose: (i, t) => this.pick(i, t) });
    this.drillCtx = {
      self: () => opts.self(),
      professor: () => {
        const id = this.professor;
        return id === null ? null : opts.poseOf(id);
      },
      get vfx() {
        return opts.vfx();
      },
    };
    opts.hudRoot.append(this.panel.root, this.pad.root, this.tray.root, this.ringCue.root, this.results.root);
  }

  /** The class the player is in (null outside class zones). */
  get inClass(): string | null {
    return this.classId;
  }

  /** The session id being attended, if any. */
  get session(): number | null {
    return this.lesson?.info?.session ?? null;
  }

  /** Whether we trace wand gestures now (the practice pad is up for a beat, or a stroke is in progress): our wand is out (characters/wand-state.ts). */
  get tracing(): boolean {
    return this.lesson !== null && (this.pending !== null || this.practicePad);
  }

  /** Whether a drill draws in the room this frame (the spell VFX must be begun and ended around `update`). */
  get drawing(): boolean {
    return this.drill !== null;
  }

  /** The stage of the class followed (its props' poses and needs), if its room has props. */
  get stage(): ClassStage | null {
    return this.stageNow;
  }

  /** Characters look at their target: when we last answered a beat (`performance.now()` ms), so the professor turns to us. */
  answeredAt = -Infinity;

  /** The professor's entity id in this section, if known. */
  get professor(): number | null {
    return this.lesson?.info?.professor ?? null;
  }

  // ---------------------------------------------------------------- zones

  /** A zone is shown: a class zone starts following its lesson; anything else ends it. With its `world`, a room with rings shows its course. */
  zoneShown(zone: Zone, world?: WorldData): void {
    if (zone.kind === "class") {
      if (this.classId !== zone.class || !this.lesson) this.follow(zone.class);
      this.panelTimer = 0;
      const rings = world?.sim.rings() ?? [];
      this.ringWorld = rings.length ? world! : null;
      this.ringView.show(rings);
      this.lastSelf = null;
      return;
    }
    this.end();
  }

  /** Whether this class is flown (its file has ring beats; known before its room loads). */
  private get ringClass(): boolean {
    return this.ringWorld !== null || (!!this.classId && isFlown(this.classId));
  }

  /** A new connection: whatever lesson was followed is over (a relog lands in the corridor). */
  welcome(): void {
    this.end();
  }

  /** Starts following class `classId`'s lesson. */
  private follow(classId: string): void {
    this.classId = classId;
    this.lesson = new LessonState(classId);
    this.picks = classPicks(classId);
    this.drill?.clear();
    this.drill = CLASS_DRILLS[classId]?.() ?? null;
    this.stageNow?.clear();
    this.stageNow = CLASS_STAGES[classId]?.() ?? null;
    const p = this.picks;
    this.look = p ? { question: sharedIcon(p.prompts), labelOnly: sharedIcon(p.options) } : {};
  }

  /** Whether the class plays with picks (the tray) rather than gestures (the pad). */
  private get pickClass(): boolean {
    return this.picks !== null && !(this.lesson?.round && !this.lesson.picking);
  }

  private end(): void {
    this.classId = null;
    this.lesson = null;
    this.picks = null;
    this.drill?.clear();
    this.drill = null;
    this.stageNow?.clear();
    this.stageNow = null;
    this.look = {};
    this.tray.show(null);
    this.pending = null;
    this.watch = null;
    this.lastDemo = null;
    this.announced = null;
    this.pad.show(null);
    this.panel.show(null);
    this.ringWorld = null;
    this.ringView.show([]);
    this.ringCue.show(null);
    this.ringFlash = null;
    this.lastSelf = null;
  }

  // ---------------------------------------------------------------- messages

  /** The class side's server messages. Returns whether `msg` was one. */
  onMessage(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "classJoined": {
        if (!this.lesson || this.classId !== msg.info.class) this.follow(msg.info.class);
        const lesson = this.lesson!;
        lesson.join(msg.info);
        this.results.close();
        audio.play("class.join");
        const name = scheduledClass(msg.info.class)?.name ?? "the lesson";
        this.opts.notify(this.ringClass ? `Welcome to ${name}. Watch the course, then fly through each ring as it glows, on the beat. W and S set your speed, A and D, the arrow keys or the mouse steer, Space climbs and Shift dives.` : this.picks ? `Welcome to ${name}. Listen to the professor, then choose each answer on the beat (click it, or press 1-6).` : `Welcome to ${name}. Stay for the demonstration, then trace each gesture on the beat.`);
        this.panelTimer = 0;
        return true;
      }
      case "lessonPhaseChanged":
        this.lesson?.setPhase(msg.phase, msg.untilUnixMs);
        if (msg.phase.kind !== "practice") this.pad.cancel();
        this.panelTimer = 0;
        return true;
      case "lessonRound":
        this.lesson?.startRound(msg.round, msg.windowMs, msg.beats);
        this.beatSounds(msg.beats);
        this.panelTimer = 0;
        return true;
      case "beatScored":
        this.scored(msg.round, msg.beat, msg.result);
        return true;
      case "classResult":
        this.showResults(msg.class, msg.scorePct, msg.grade, msg.rewards);
        audio.stinger("stinger.class_result");
        return true;
      case "classError":
        this.opts.notify(this.errorText(msg.code));
        return true;
      default:
        return false;
    }
  }

  /** The professor demonstrates `gesture` (an NPC event in this zone). */
  demonstrate(gestureId: number, spellId: number | null, durationMs: number): void {
    const g = gestureById(gestureId);
    // A pick class's professor waves a wand for show; the pad stays away.
    if (!g || !this.lesson || this.picks) return;
    const spell = spellId !== null ? spellById(spellId) : undefined;
    this.watch = { gesture: g, colour: spell?.colour ?? DEMO_GOLD, start: performance.now(), duration: Math.max(300, durationMs) };
    this.lastDemo = g;
    this.pad.announce(`The professor demonstrates the ${g.name}${spell ? ` for ${spell.name}` : ""}.`);
  }

  /**
   * Sound: each beat of a round on the beat clock, a tick half a second
   * before its window opens and a chime as it does (already past: none).
   */
  private beatSounds(beats: readonly { atUnixMs: number }[]): void {
    const now = this.opts.clock.now();
    for (const b of beats) {
      const lead = (b.atUnixMs - now) / 1000;
      if (lead > 0.5) audio.play("class.beat_tick", { delayS: lead - 0.5 });
      if (lead > 0) audio.play("class.beat_open", { delayS: lead });
    }
  }

  /** Sound: the class's own reaction to a passed trace or a right pick. */
  private reactSound(): void {
    const id = `class.fx.${this.classId ?? ""}`;
    if (isSoundId(id)) audio.play(id);
  }

  private scored(round: number, beat: number, result: GestureResult): void {
    const b = this.lesson?.scored(round, beat, result);
    if (!b) return;
    audio.play(result.rejected ? "class.reject" : result.score > 0 ? (`class.grade_${result.grade}` as SoundId) : "class.miss");
    if (b.kind === "ring") {
      const kind = result.rejected ? "reject" : result.score > 0 ? result.grade : "miss";
      const text = result.rejected ? `Not counted: ${rejectText(result.rejected)}` : result.score > 0 ? `Ring ${b.ring + 1}: ${PickTray.gradeText(result.grade, result.score)}` : `Ring ${b.ring + 1}: missed`;
      this.ringFlash = { text, kind, until: performance.now() + RING_FLASH_MS };
      return;
    }
    if (b.kind === "pick") {
      // The tray only previewed the timing: the server says whether it was right.
      // The props answer the verdict (a plant recoils or snaps, a creature settles or startles).
      this.stageNow?.scored(b, !result.rejected && result.score > 0, this.opts.clock.now());
      if (result.rejected) this.tray.flash(`Not counted: ${rejectText(result.rejected)}`, "reject");
      else if (result.score > 0) {
        this.tray.flash(PickTray.gradeText(result.grade, result.score), result.grade);
        this.hit(b);
      } else this.tray.flash("Not quite", "miss");
      return;
    }
    const now = this.opts.clock.now();
    this.drill?.scored(this.drillCtx, b, result, now);
    if (!result.rejected && result.score > 0 && this.classId) {
      const traced = CLASS_TRACED[this.classId];
      const g = gestureById(b.gesture);
      if (traced && g) traced({ ...this.opts.fx, swap: () => undefined }, g.key);
      this.reactSound();
    }
    const p = b.preview;
    if (result.rejected) this.pad.flash(`Not counted: ${rejectText(result.rejected)}`, "reject");
    else if (!p || p.grade !== result.grade || Math.abs(p.score - result.score) > 2) this.pad.flash(TracePad.gradeText(result.grade, result.score), result.grade);
  }

  /** A right pick: the class's effect hook (a cauldron's puff, an object's change). */
  private hit(b: BeatState): void {
    const hook = this.classId ? CLASS_HITS[this.classId] : undefined;
    if (!hook || b.choice === null) return;
    const { prompt, options } = resolvePick(this.picks, b.prompt, b.options);
    const chosen = options[b.choice];
    if (!chosen) return;
    hook({ ...this.opts.fx, swap: (text) => this.tray.swap(text, prompt.key) }, prompt, chosen);
    this.reactSound();
  }

  private showResults(classId: string, scorePct: number, grade: Grade, rewards: ClassRewards): void {
    const file = classFiles[classId];
    const discipline = file ? this.opts.subjectName(file.subject) : "Skill";
    this.results.show({
      className: scheduledClass(classId)?.name ?? capitalise(classId),
      scorePct,
      grade,
      rewards,
      discipline,
      houseName: this.opts.houseName,
      spells: rewards.spells.map((id) => ({ name: spellName(id), how: howToCast(id) })),
      items: rewards.items.map((it) => ({ itemDef: it.itemDef, name: defName(it.itemDef), quantity: it.quantity })),
      recipes: rewards.recipes.map((id) => recipeById(id)?.name ?? `Recipe #${id}`),
      lore: rewards.lore !== null ? "a new page in your lore book" : null,
      fortune: rewards.fortune ? { name: fortuneName(rewards.fortune), blurb: fortuneBlurb(rewards.fortune) } : null,
      picking: !!this.picks,
      flying: this.ringClass,
    });
    this.pad.show(null);
    this.tray.show(null);
    this.ringCue.show(null);
    // The entry's title and text live on the server only (the client bundles no lore).
    const loreId = rewards.lore;
    if (loreId !== null) {
      void fetchLoreEntry(loreId)
        .then((e) => {
          if (e && this.results.isOpen) this.results.setLore(e.title);
        })
        .catch(() => undefined);
    }
  }

  private errorText(code: ClassErrorCode): string {
    const id = this.opts.doorClass() ?? this.classId;
    const cls = id ? scheduledClass(id) : undefined;
    const name = cls?.name ?? "The class";
    const next = id ? this.opts.nextLesson(id) : "";
    switch (code) {
      case "not_open":
        return `The ${name} classroom door is closed: students may only go in during the first minutes of a lesson.${next ? ` The next lesson starts ${next}.` : ""}`;
      case "full":
        return `Every section of this ${name} lesson is full. Please come to the next one${next ? ` (${next})` : ""}.`;
      case "already_attended":
        return `You have already been to this ${name} lesson.${next ? ` The next one starts ${next}.` : ""}`;
      case "rate_limited":
        return "One moment: the door is still swinging.";
      case "failed":
        return "The door is stuck. Please try again.";
    }
  }

  // ---------------------------------------------------------------- tracing

  private traceStart(t: number): { maxMs: number } | null {
    const l = this.lesson;
    if (!l || this.watching(performance.now())) return null;
    const start = this.opts.clock.fromLocal(t);
    const r = l.round;
    if (l.practising && r) {
      const b = l.beatFor(start);
      const gesture = b ? gestureById(b.gesture) : null;
      if (b && gesture) {
        this.pending = { gesture, beat: b, round: r.round, windowMs: r.windowMs, offset: Math.round(start - b.atMs) };
        return { maxMs: gesture.max_ms };
      }
      // Off the beat: drawn and scored for practice, nothing sent (against the gesture on the pad).
      const cur = l.shown(start);
      const g = cur ? gestureById(cur.gesture) : null;
      if (!g) return null;
      this.pending = { gesture: g, beat: null, round: r.round, windowMs: r.windowMs, offset: 0 };
      return { maxMs: g.max_ms };
    }
    const g = this.freeGesture();
    if (!g) return null;
    this.pending = { gesture: g, beat: null, round: 0, windowMs: FREE_WINDOW_MS, offset: 0 };
    return { maxMs: g.max_ms };
  }

  private traceEnd(trace: TraceRecorder): void {
    const p = this.pending;
    this.pending = null;
    if (!p || !this.lesson) return;
    const flat = trace.flat();
    const local = this.scorer.score(p.gesture.id, p.windowMs, p.offset, flat);
    if (!p.beat) {
      const words = local.rejected ? rejectText(local.rejected) : TracePad.gradeText(local.grade, local.score);
      const practising = this.lesson.practising;
      this.pad.flash(practising ? `${words} · wait for the ring` : `Warm-up: ${words}`, practising ? "info" : local.rejected ? "reject" : local.grade);
      return;
    }
    if (local.rejected && SLIPS.has(local.rejected)) {
      // Cut off before it was a stroke at all: the beat goes by as missed.
      this.pad.flash(p.cut ? "Out of time: missed" : `${rejectText(local.rejected)}: try again`, "reject");
      return;
    }
    if (p.beat.status !== "upcoming") return; // scored meanwhile (a second stroke over one beat)
    this.opts.send(encodeTraceGesture(p.round, p.beat.beat, clampI16(p.offset), flat));
    this.answeredAt = performance.now();
    this.lesson.sent(p.beat, local);
    this.pad.flash(local.rejected ? rejectText(local.rejected) : TracePad.gradeText(local.grade, local.score), local.rejected ? "reject" : local.grade);
  }

  // ---------------------------------------------------------------- picking

  /** Option `index` of the beat shown was picked at local time `t`. */
  private pick(index: number, t: number): void {
    const l = this.lesson;
    const r = l?.round;
    if (!l || !r || !l.practising) return;
    const at = this.opts.clock.fromLocal(t);
    const b = l.beatFor(at);
    if (!b || b.kind !== "pick") {
      const cur = l.current(at);
      if (cur && cur.atMs > at) this.tray.flash("Too early: wait for the ring", "info");
      return;
    }
    if (index >= b.options.length) return;
    const offset = Math.round(at - b.atMs);
    const preview = this.pickScorer.preview(r.windowMs, offset);
    if (preview.rejected) {
      this.tray.flash(rejectText(preview.rejected), "reject");
      return;
    }
    this.opts.send(encodePickAnswer(r.round, b.beat, index, clampI16(offset)));
    this.answeredAt = performance.now();
    audio.play("class.pick");
    l.sent(b, preview, index);
  }

  /** The gesture to warm up with before practice: the last one shown, else the first the lesson uses. */
  private freeGesture(): GestureDef | null {
    if (this.lastDemo) return this.lastDemo;
    const file = this.classId ? classFiles[this.classId] : undefined;
    const first = file?.lesson.find((a) => a.kind === "practice")?.beats?.[0]?.gesture ?? file?.lesson.find((a) => a.kind === "demonstrate")?.gesture;
    return (first ? gestureByKey(first) : undefined) ?? null;
  }

  private watching(now: number): boolean {
    const w = this.watch;
    return !!w && now < w.start + w.duration + 400;
  }

  // ---------------------------------------------------------------- per frame

  update(dt: number): void {
    const l = this.lesson;
    if (!l) return;
    const nowLocal = performance.now();
    const now = this.opts.clock.now();
    // A stroke for a beat whose time on the pad is over is cut off where it
    // is and sent as drawn (scored for what it is, or missed if too little).
    const p = this.pending;
    if (p?.beat && l.cutDue(now, p.beat)) {
      p.cut = true;
      if (!this.pad.cut(nowLocal)) this.pending = null;
    }
    const expired = l.expire(now, this.pending?.beat ?? null);
    if (this.drill) {
      for (const b of expired) this.drill.missed(this.drillCtx, b, now);
      this.drill.frame(this.drillCtx, l.practising && l.round ? { windowMs: l.round.windowMs, beats: l.round.beats } : null, now);
    }
    if (this.stageNow) {
      for (const b of expired) this.stageNow.missed(b, now);
      this.stageNow.frame(l.practising && l.round ? { windowMs: l.round.windowMs, beats: l.round.beats } : null, now);
    }
    this.practicePad = false;
    if (this.ringClass) {
      this.pad.show(null);
      this.tray.show(null);
      this.flyRings(l, now);
    } else if (this.pickClass) {
      this.pad.show(null);
      this.tray.show(this.trayScene(l, now));
    } else {
      this.tray.show(null);
      const scene = this.padScene(l, now, nowLocal);
      this.practicePad = scene?.mode === "practice";
      this.pad.show(scene);
      this.pad.frame(nowLocal, dt * 1000);
    }
    this.panelTimer -= dt;
    if (this.panelTimer <= 0) {
      this.panelTimer = PANEL_INTERVAL;
      const info = l.info;
      const s = l.summary();
      this.panel.show({
        className: scheduledClass(this.classId ?? "")?.name ?? capitalise(this.classId ?? "Class"),
        professor: l.script?.professorName ?? "The professor",
        // Sections are numbered from 0 on the wire; people count from 1.
        section: (info?.section ?? 0) + 1,
        phase: l.phase,
        phaseLeftMs: l.phaseUntilMs - now,
        roundsTotal: l.roundsTotal,
        answered: s.answered,
        beats: s.beats,
        average: s.average,
        picking: !!this.picks,
        flying: this.ringClass,
      });
    }
  }

  /**
   * Each frame of a flown class: our flight since last frame
   * against the open ring beats (a pass lights its ring at once; the server's
   * `BeatScored` gives the marks), the course's glow and the HUD cue.
   */
  private flyRings(l: LessonState, now: number): void {
    const world = this.ringWorld;
    const me = this.opts.self();
    const r = l.practising ? l.round : null;
    if (me) {
      const last = this.lastSelf;
      if (last && r) {
        for (const b of l.openRings(last.at, now)) {
          if (!world?.sim.passesRing(b.ring, last.x, last.y, last.z, me.x, me.y, me.z)) continue;
          const preview = this.pickScorer.preview(r.windowMs, clampI16(now - b.atMs));
          if (!preview.rejected) {
            l.sent(b, preview);
            audio.play("class.ring_through");
          }
        }
      }
      this.lastSelf = { x: me.x, y: me.y, z: me.z, at: now };
    }
    this.ringView.update(r ? r.beats : null, r?.windowMs ?? 0, now);
    const flash = this.ringFlash && performance.now() < this.ringFlash.until ? this.ringFlash : null;
    if (!flash) this.ringFlash = null;
    const phase = l.phase.kind;
    if (phase === "results" || phase === "ended" || this.results.isOpen) {
      this.ringCue.show(null);
      return;
    }
    if (r) {
      const b = l.current(now);
      if (b && b.kind === "ring") {
        const lead = b.atMs - now;
        const open = Math.abs(lead) <= r.windowMs;
        const ofRound = r.beats.indexOf(b) + 1;
        // Sound: the ring lights up as its window opens (once a ring).
        if (open && this.glowed !== b) {
          this.glowed = b;
          audio.play("class.ring_glow");
        }
        this.ringCue.show({ caption: `Round ${r.round} · ring ${ofRound} of ${r.beats.length}`, countdown: open ? "Now!" : lead > 0 ? `in ${(lead / 1000).toFixed(1)} s` : "", open, flash: flash ? { text: flash.text, kind: flash.kind } : null });
        return;
      }
      this.ringCue.show({ caption: `Round ${r.round} done`, countdown: "", open: false, flash: flash ? { text: flash.text, kind: flash.kind } : null });
      return;
    }
    const caption = phase === "gathering" ? "The lesson begins soon" : phase === "demonstration" ? "Watch the course" : "Get ready";
    this.ringCue.show({ caption, countdown: "", open: false, flash: null });
  }

  private padScene(l: LessonState, now: number, nowLocal: number): PadScene | null {
    const phase = l.phase.kind;
    if (phase === "results" || phase === "ended" || this.results.isOpen) return null;
    const w = this.watch;
    if (w && this.watching(nowLocal)) {
      return { mode: "watch", gesture: w.gesture, colour: w.colour, approach: null, open: false, watch: Math.min(1, (nowLocal - w.start) / w.duration), caption: `Watch: ${w.gesture.name}`, countdown: "", pips: this.pips(l, null), sky: isConstellation(w.gesture.key) };
    }
    if (l.practising && l.round) {
      const r = l.round;
      // A beat's gesture stays up after the beat (TRACE_HOLD_MS) so a stroke can finish; the next comes up as its ring starts closing.
      const b = l.shown(now);
      const g = b ? gestureById(b.gesture) : null;
      if (b && g) {
        if (this.announced !== b.beat) {
          this.announced = b.beat;
          this.pad.announce(`Next: ${g.name}, in ${Math.max(0, Math.round((b.atMs - now) / 1000))} seconds.`);
        }
        const waiting = b.status === "upcoming";
        const open = waiting && Math.abs(now - b.atMs) <= r.windowMs;
        const lead = b.atMs - now;
        return {
          mode: "practice",
          gesture: g,
          colour: "#f0c36a",
          approach: waiting ? Math.min(1, Math.max(0, lead / APPROACH_MS)) : null,
          open,
          watch: null,
          caption: g.name,
          countdown: open ? "Now!" : waiting && lead > 0 ? `in ${(lead / 1000).toFixed(1)} s` : "",
          pips: this.pips(l, waiting ? b : null),
          sky: isConstellation(g.key),
        };
      }
      return { mode: "free", gesture: null, colour: "#f0c36a", approach: null, open: false, watch: null, caption: `Round ${r.round} done`, countdown: "", pips: this.pips(l, null), sky: isConstellation(this.lastDemo?.key) };
    }
    // Before practice and between rounds: warm up with the gesture last shown.
    const g = this.freeGesture();
    return { mode: "free", gesture: g, colour: "#f0c36a", approach: null, open: false, watch: null, caption: g ? `Warm up: ${g.name}` : "Warm up", countdown: "", pips: [], sky: isConstellation(g?.key) };
  }

  private trayScene(l: LessonState, now: number): PickScene | null {
    const phase = l.phase.kind;
    if (phase === "results" || phase === "ended" || this.results.isOpen) return null;
    if (l.practising && l.round) {
      const r = l.round;
      // The beat answered last stays on the card until its window has passed, so its result shows over it.
      const b = l.current(now) ?? this.lastAnswered(l, now);
      if (b && b.kind === "pick") {
        const { prompt, options } = resolvePick(this.picks, b.prompt, b.options);
        if (b.status === "upcoming" && this.announced !== b.beat) {
          this.announced = b.beat;
          this.tray.announce(`Next: ${prompt.label}, in ${Math.max(0, Math.round((b.atMs - now) / 1000))} seconds.`);
        }
        const lead = b.atMs - now;
        // A long window (History's questions) closes its ring over the whole window: time to read.
        const approachMs = Math.max(APPROACH_MS, r.windowMs);
        // "Now!" (and the green ring) only as the ring meets the card: the window opens a whole window
        // before the beat, but the marks are on the beat.
        const { open, onBeat, countdown } = pickCue(lead, r.windowMs, b.status === "upcoming");
        return {
          prompt,
          options,
          approach: b.status === "upcoming" ? Math.min(1, Math.max(0, lead / approachMs)) : null,
          open,
          onBeat,
          look: { ...this.look, closeup: closeupOf(prompt.key) },
          chosen: b.choice,
          caption: `Round ${r.round}`,
          countdown,
          pips: this.pips(l, b.status === "upcoming" ? b : null),
        };
      }
      return { prompt: null, options: [], approach: null, open: false, chosen: null, caption: `Round ${r.round} done`, countdown: "", pips: this.pips(l, null) };
    }
    const caption = phase === "gathering" ? "The lesson begins soon" : "Listen to the professor";
    return { prompt: null, options: [], approach: null, open: false, chosen: null, caption, countdown: "", pips: [] };
  }

  /** The last beat answered whose window is still open (so its card stays up a moment). */
  private lastAnswered(l: LessonState, now: number): BeatState | null {
    const r = l.round;
    if (!r) return null;
    let last: BeatState | null = null;
    for (const b of r.beats) if (b.status !== "upcoming" && b.status !== "missed" && now <= b.atMs + r.windowMs) last = b;
    return last;
  }

  private pips(l: LessonState, current: BeatState | null): PadPip[] {
    const r = l.round;
    if (!r || !l.practising) return [];
    return r.beats.map((b) => {
      if (b === current) return { status: "current" };
      if (b.status === "scored") return { status: "scored", grade: b.result?.grade, rejected: !!b.result?.rejected };
      return { status: b.status === "upcoming" ? "upcoming" : b.status };
    });
  }

  handleKey(e: KeyboardEvent): boolean {
    return this.results.handleKey(e) || this.tray.handleKey(e);
  }

  dispose(): void {
    this.scorer.free();
    this.pickScorer.free();
    this.tray.root.remove();
    this.pad.dispose();
    this.panel.root.remove();
    this.results.root.remove();
    this.ringCue.root.remove();
    this.ringView.dispose();
  }
}

/** Words for a refused trace. */
export function rejectText(r: TraceReject): string {
  switch (r) {
    case "bad_sample_count":
    case "bad_duration":
      return "too short";
    case "too_small":
      return "too small";
    case "too_fast":
      return "too fast for a hand";
    case "too_perfect":
      return "too perfect to be traced by hand";
    case "out_of_range":
      return "off the pad";
    case "replayed":
      return "the same stroke as before";
    case "bad_timing":
      return "off the beat";
    case "unknown_beat":
      return "no such beat";
    case "wrong_beat_kind":
      return "not that kind of beat";
    case "bad_choice":
      return "not one of the choices";
    case "not_in_practice":
      return "the rounds are over";
    case "rate_limited":
      return "too many strokes at once";
  }
}

/** How a spell learned in class is cast. */
function howToCast(spellId: number): string {
  const s = spellById(spellId);
  switch (s?.social.kind) {
    case "light":
      return "Press L to light your wand.";
    case "levitate":
      return "Press V in a dorm room to make furniture float.";
    default:
      return s && isDueling(s) ? "Add it to your dueling loadout at the Dueling Club." : "Find it in your spellbook (Y).";
  }
}

/** Whether class `classId`'s lesson is flown (its practice beats are rings). */
export function isFlown(classId: string): boolean {
  return !!classFiles[classId]?.lesson.some((a) => a.beats?.some((b) => b.ring !== undefined));
}

function clampI16(v: number): number {
  return Math.max(-32768, Math.min(32767, Math.round(v)));
}

function capitalise(s: string): string {
  return s ? s[0]!.toUpperCase() + s.slice(1).replace(/_/g, " ") : s;
}
