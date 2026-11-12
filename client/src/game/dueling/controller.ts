// Dueling in the running game, in one place for game.ts
// (docs/architecture.md, "Spells and dueling"):
//
// - **At the Dueling Club** (the castle's `dueling_club` area): the club
//   badge, "Challenge to a duel" in the player menu and `/duel Name`, the
//   challenge toasts (Accept / Decline / Withdraw), the list of duels to
//   watch (`/duels`), and the loadout editor (`/loadout`, the HUD's ⚔).
// - **In the arena**: the duel HUD (ui/duel.ts) and the spell VFX
//   (arena-fx.ts), driven by `DuelView` (state.ts) on the estimated duel
//   clock (clock.ts). Keys 1-5 cast the loadout's spells; WASD sidesteps.
//
// The server decides every outcome. The client predicts only its own cast:
// the cast animation, the cast bar and the cooldown sweep start on the key
// press, and a key pressed a moment before a spell is ready is held and sent
// just in time (so a duel at 150 ms feels like one at 0). Messages from the
// server re-anchor or take back the prediction.
//
// game.ts hands every server message to `onMessage` (true: consumed),
// asks `handleKey` and `command` before its own keys and chat, adds
// `menuItems` to the player menu, calls `pose` before drawing characters,
// `frame` every frame (VFX, HUD) and `hudTick` on its HUD tick.

import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import { audio } from "../../audio";
import { project } from "../../economy/pick";
import { ClubBadge, DuelToasts } from "../../ui/duel-club";
import { DuelHud } from "../../ui/duel";
import { LoadoutWindow } from "../../ui/loadout";
import { SpectateWindow } from "../../ui/spectate";
import {
  encodeDuelCancel,
  encodeDuelCast,
  encodeDuelChallenge,
  encodeDuelChallengeName,
  encodeDuelForfeit,
  encodeDuelListRequest,
  encodeDuelRespond,
  encodeDuelSpectate,
  encodeLeaveInstance,
  type DuelSide,
  type ServerMessage,
  type Vec3,
  type Zone,
} from "../../wasm/sim_wasm";
import type { Anim } from "../../wasm/sim_wasm";
import { SpellVfx } from "../vfx/spell-vfx";
import { ArenaFx, CHEST_M, type DuelistPose } from "./arena-fx";
import { areaAt, loadDuelAreas, type DuelArea } from "./area";
import { ChallengeBook, duelCommand, duelErrorText } from "./challenges";
import { DuelClock } from "./clock";
import { DUEL_RULES, duelSpell, venue, venueByArea } from "./content";
import { houseInfo } from "../../content/houses";
import { duelRewardsText, DuelView, endText, refusalText, sideIndex, type DuelCue } from "./state";

/** A character as the menu and the frame know it (feet, metres). */
export interface DuelTarget {
  id: number;
  name: string;
  x: number;
  y: number;
  z: number;
}

/** A drawn pose the controller may adjust (our own, or a remote's). */
export interface PoseRef {
  x: number;
  y: number;
  z: number;
  yaw: number;
  anim: Anim;
}

export interface DuelingOptions {
  scene: Scene;
  /** Where the HUD, toasts and windows go. */
  hudRoot: HTMLElement;
  /** The HUD bar's buttons (the loadout button). */
  hudActions: HTMLElement;
  send: (frame: Uint8Array) => void;
  /** A line for the chat log. */
  notify: (text: string) => void;
  /** Our entity id (-1 before Welcome). */
  selfId: () => number;
  /** The round trip measured by the connection (ms), null before the first pong. */
  rttMs: () => number | null;
  /** The camera's view-projection (`scene.getTransformMatrix().m`). */
  viewProjection: () => ArrayLike<number>;
  /** The canvas's size in CSS px. */
  viewport: () => { width: number; height: number };
  /** Height of a drawn character's head above its feet (m). */
  headHeight: (id: number) => number;
  /** How long the server plays `Anim::Cast` (ms, `castAnimMs`). */
  castAnimMs: number;
  /** A duelist's Guardian ward stopped a spell (their entity): the guardian flares. */
  onWardBlock?: (entity: number) => void;
}

/** A key pressed this much before its spell is ready is held and sent on time (ms). */
const BUFFER_MS = 250;
/** A prediction the server has not answered after this (plus two round trips) is dropped (ms). */
const PENDING_GRACE_MS = 600;
/** Callouts float no higher than this (px from the top: below the HUD bar). */
const ANCHOR_TOP_PX = 130;
type PlayerMenuAction = { label: string; run: () => void };

export class DuelingController {
  private readonly book = new ChallengeBook();
  private readonly toasts: DuelToasts;
  private readonly badge: ClubBadge;
  readonly loadout: LoadoutWindow;
  private readonly spectate: SpectateWindow;
  private readonly hud: DuelHud;
  private readonly vfx: SpellVfx;
  private readonly arena: ArenaFx;
  private readonly clock = new DuelClock();
  private areas: DuelArea[] = [];
  private area: DuelArea | null = null;
  private zone: Zone | null = null;
  private view: DuelView | null = null;
  /** A cast held until it is ready. */
  private buffered: { spell: number; untilLocal: number } | null = null;
  /** Our predicted cast animation runs until then (local ms). */
  private selfAnimUntil = 0;
  /** The duelists' cast animations (from `CastStarted`) run until then (local ms). */
  private readonly sideAnimUntil: [number, number] = [0, 0];
  /** Where each duelist is drawn this frame (filled by `pose`). */
  private readonly poses: [DuelistPose & { seen: boolean }, DuelistPose & { seen: boolean }] = [
    { x: 0, y: 0, z: 0, yaw: 0, seen: false },
    { x: 0, y: 0, z: 0, yaw: 0, seen: false },
  ];
  private readonly anchors: [{ x: number; y: number } | null, { x: number; y: number } | null] = [null, null];
  private readonly anchorPool: [{ x: number; y: number }, { x: number; y: number }] = [
    { x: 0, y: 0 },
    { x: 0, y: 0 },
  ];
  private readonly poseOf = (side: DuelSide): DuelistPose | null => this.drawnPose(side);
  /** Sound: whose a side's sounds are (ours are never dropped for the cap). */
  private readonly ownerOf = (side: DuelSide): number | undefined => (this.view?.you === side ? this.opts.selfId() : undefined);
  private disposed = false;

  constructor(private readonly opts: DuelingOptions) {
    this.vfx = new SpellVfx(opts.scene);
    this.arena = new ArenaFx(this.vfx);
    this.toasts = new DuelToasts(this.book, {
      onAnswer: (id, accept) => this.answer(id, accept),
      onWithdraw: (id) => opts.send(encodeDuelCancel(id)),
      onLapse: (text) => opts.notify(text),
    });
    this.loadout = new LoadoutWindow({ notify: opts.notify });
    this.spectate = new SpectateWindow({
      requestList: () => opts.send(encodeDuelListRequest()),
      watch: (id) => opts.send(encodeDuelSpectate(id)),
    });
    this.badge = new ClubBadge({ onWatch: () => this.spectate.toggle(true), onLoadout: () => this.loadout.toggle(true) });
    this.hud = new DuelHud({
      onCast: (spell) => this.tryCast(spell),
      onForfeit: () => opts.send(encodeDuelForfeit()),
      onLeave: () => opts.send(encodeLeaveInstance()),
    });
    opts.hudActions.append(this.loadout.button);
    opts.hudRoot.append(this.hud.root, this.badge.root, this.toasts.root, this.loadout.root, this.spectate.root);
    void loadDuelAreas().then((a) => {
      if (!this.disposed) this.areas = a;
    });
  }

  /** Sound: we are one of the duelists of the duel shown (its music comes first). */
  get inDuel(): boolean {
    return !!this.view?.you;
  }

  /** Spells L20: whether entity `id` is a duelist of the duel shown (its statuses sound through its cues). */
  isDuelist(id: number): boolean {
    return !!this.view && this.view.sideOf(id) !== null;
  }

  /** Characters look at their target: our opponent's entity id while we duel (null otherwise). */
  get opponent(): number | null {
    const v = this.view;
    if (!v?.you || v.ended) return null;
    return v.info(v.you === "a" ? "b" : "a").entity;
  }

  // ---------------------------------------------------------------- messages

  /** Handles the dueling messages; true when `msg` was one (and is done with). Others are only watched. */
  onMessage(msg: ServerMessage): boolean {
    const now = performance.now();    switch (msg.type) {
      case "welcome":
        // A new connection: every challenge and duel ended with the old one.
        this.zone = msg.zone;
        this.book.clear();
        this.toasts.render();
        this.endDuel();
        return false;
      case "zoneChanged":
        this.zone = msg.zone;
        this.spectate.toggle(false);
        this.badge.show(null);
        this.area = null;
        if (msg.zone.kind === "arena") {
          this.book.clear();
          this.toasts.render();
        } else this.endDuel();
        return false;
      case "spellbook": {
        this.loadout.spellbook(msg);
        const fresh = msg.new.filter((s) => duelSpell(s));
        if (fresh.length && this.area) this.badge.learned(fresh);
        return false;
      }
      case "progress":
        this.loadout.setLevel(msg.progress.level);
        return false;
      case "duelChallenged": {
        const line = this.book.challenged(msg, this.opts.selfId(), now);
        if (line) this.opts.notify(line);
        if (line && msg.from !== this.opts.selfId()) audio.play("ui.invite");
        this.toasts.render();
        return true;
      }
      case "duelChallengeEnded": {
        const line = this.book.ended(msg.challengeId, msg.reason);
        if (line) this.opts.notify(line);
        this.toasts.render();
        return true;
      }
      case "duelError": {
        const text = this.book.error(msg.challengeId, msg.code);
        if (this.spectate.isOpen && (msg.code === "unknown_duel" || msg.code === "full" || msg.code === "not_in_area")) this.spectate.error(text);
        else this.opts.notify(text);
        this.toasts.render();
        return true;
      }
      case "duelList":
        this.spectate.show(msg.duels);
        return true;
      case "duelStart":
        this.startDuel(msg, now);
        return true;
      case "duelEvents":
        if (this.view && msg.duelId === this.view.duelId) {
          this.clock.observe(msg.atMs, now);
          this.cues(this.view.apply(msg), now);
        }
        return true;
      case "duelResult":
        if (this.view && msg.duelId === this.view.duelId) {
          this.view.applyResult(msg, now);
          this.buffered = null;
          const v = this.view;
          if (v.you) {
            const verdict = v.verdict();
            audio.stinger(verdict === "win" ? "stinger.duel_won" : "stinger.duel_lost");
            const opp = v.info(v.you === "a" ? "b" : "a").name;
            const xp = msg.rewards ? ` ${duelRewardsText(msg.rewards, houseInfo(v.info(v.you).house).name)}` : "";
            this.opts.notify(`${verdict === "win" ? `You beat ${opp}!` : verdict === "loss" ? `${opp} won the duel.` : `Your duel with ${opp} was a draw.`} ${endText(v, msg.winner, msg.reason)}${xp}`);
          }
        }
        return true;
      default:
        return false;
    }
  }

  private startDuel(msg: Extract<ServerMessage, { type: "duelStart" }>, now: number): void {
    const view = new DuelView(msg);
    this.view = view;
    this.clock.reset();
    this.clock.observe(msg.state.atMs, now);
    this.buffered = null;
    this.selfAnimUntil = 0;
    this.sideAnimUntil[0] = this.sideAnimUntil[1] = 0;
    this.arena.start(this.zone?.kind === "arena" ? this.zone.venue : "dueling_club");
    this.vfx.clear();
    this.spectate.toggle(false);
    this.loadout.toggle(false);
    this.hud.reset();
    this.hud.start(view);
    // Sound: the count to the fight, one tick a second (the fight's gong is its cue).
    const left = view.countdownLeft(msg.state.atMs) / 1000;
    for (let s = Math.floor(left); s >= 1; s--) if (left - s >= 0) audio.play("duel.countdown", { delayS: left - s, variant: 0 });
    if (view.you) {
      const keys = view.loadout.length > 1 ? `1-${view.loadout.length}` : "1";
      this.opts.notify(`Duel with ${view.info(view.you === "a" ? "b" : "a").name}! Keys ${keys} cast, A and D sidestep along the stage.`);
    }
  }

  private endDuel(): void {
    this.view = null;
    this.buffered = null;
    this.hud.hide();
    this.vfx.clear();
    this.arena.clear();
    this.clock.reset();
  }

  /** Callouts, the hit flash and the remote cast animations for a tick's cues. */
  private cues(cues: readonly DuelCue[], now: number): void {
    const v = this.view!;
    this.arena.play(cues, this.poseOf, this.ownerOf);
    for (const c of cues) {
      switch (c.kind) {
        case "fight":
          audio.play("duel.fight");
          break;
        case "cast":
          this.sideAnimUntil[sideIndex(c.side)] = now + this.opts.castAnimMs;
          break;
        case "refused":
          if (c.side === v.you) {
            this.buffered = null;
            audio.play("ui.error");
            this.hud.refused(c.spell, refusalText(c.reason));
          }
          break;
        case "interrupted":
          this.hud.callout(c.side, "Interrupted!", "break");
          break;
        case "blocked":
          this.hud.callout(c.side, "Blocked!", "block");
          break;
        case "reflected":
          this.hud.callout(c.side, "Reflected!", "reflect");
          break;
        case "shattered":
          this.hud.callout(c.side, "Shield broken!", "break");
          break;
        case "hit":
          this.hud.callout(c.side, `−${c.damage}`, "hit");
          if (c.side === v.you) this.hud.flash();
          break;
        case "disarmed":
          this.hud.callout(c.side, "Disarmed!", "break");
          break;
        case "rooted":
          this.hud.callout(c.side, "Bound!", "bind");
          break;
        case "slowed":
          this.hud.callout(c.side, "Slowed!", "slow");
          break;
        case "pulled":
          this.hud.callout(c.side, "Adveni!", "pull");
          break;
        case "cleansed":
          if (c.freed) this.hud.callout(c.side, "Freed!", "good");
          break;
        case "wardUp":
          this.hud.callout(c.side, "Guardian!", "ward");
          break;
        case "warded":
          this.hud.callout(c.side, "Warded!", "ward");
          this.opts.onWardBlock?.(v.info(c.side).entity);
          break;
        case "wardFaded":
          this.hud.callout(c.side, "Ward faded", "info");
          break;
        default:
          break;
      }
    }
  }

  // ---------------------------------------------------------------- challenges

  /** Whether `pos` is in a dueling area of the castle (and we are in the castle). */
  private inArea(x: number, y: number, z: number): DuelArea | null {
    return this.zone?.kind === "castle" ? areaAt(this.areas, x, y, z) : null;
  }

  /** The player menu's dueling entries for `target` (only where a challenge can work). */
  menuItems(target: DuelTarget, self: Vec3): PlayerMenuAction[] {
    if (this.view || !this.inArea(self[0], self[1], self[2]) || !this.inArea(target.x, target.y, target.z)) return [];
    return [{ label: "Challenge to a duel", run: () => this.challenge(target.name, target.id) }];
  }

  /** Challenges `name` (by entity when we can see them, else by name). */
  challenge(name: string, entity: number | null): void {
    if (this.view) {
      this.opts.notify("You're already in a duel.");
      return;
    }
    if (this.book.outgoing) {
      this.opts.notify(`You're still waiting for ${this.book.outgoing.targetName} to answer.`);
      return;
    }
    if (this.zone?.kind === "castle" && this.areas.length && !this.area) {
      this.opts.notify(duelErrorText("not_in_area", null));
      return;
    }
    if (!this.loadout.loadout.length) {
      this.opts.notify("Your loadout is empty: put a spell in it first (/loadout).");
      return;
    }
    this.opts.send(entity !== null ? encodeDuelChallenge(entity) : encodeDuelChallengeName(name));
    this.book.sent(name, performance.now(), DUEL_RULES.challenge_secs);
    this.toasts.render();
  }

  private answer(challengeId: number, accept: boolean): void {
    const c = this.book.answered(challengeId);
    this.opts.send(encodeDuelRespond(challengeId, accept));
    if (!accept && c) this.opts.notify(`You declined ${c.fromName}'s duel.`);
    this.toasts.render();
  }

  /** Chat commands: /duel Name, /duels, /loadout, /forfeit. True if `line` was one. */
  command(line: string, nearby: () => readonly DuelTarget[]): boolean {
    const cmd = duelCommand(line);
    if (!cmd) return false;
    switch (cmd.verb) {
      case "duel":
        if (!cmd.arg) this.opts.notify("Duel whom? At the Dueling Club, type /duel Name or right-click someone.");
        else {
          const seen = nearby().find((t) => t.name.toLowerCase() === cmd.arg.toLowerCase());
          this.challenge(seen?.name ?? cmd.arg, seen?.id ?? null);
        }
        break;
      case "duels":
        if (this.zone?.kind === "castle" && this.areas.length && !this.area) this.opts.notify("Duels can be watched from the Dueling Club, by the stage in the Great Hall.");
        else this.spectate.toggle(true);
        break;
      case "loadout":
        this.loadout.toggle(true);
        break;
      case "forfeit":
        if (this.view?.you && !this.view.ended) this.opts.send(encodeDuelForfeit());
        else this.opts.notify("You're not in a duel.");
        break;
    }
    return true;
  }

  // ---------------------------------------------------------------- casting

  /** A spell key or button: cast now, hold it until ready, or say why not. */
  tryCast(spell: number): void {
    const v = this.view;
    if (!v || !v.you || v.ended) return;
    const local = performance.now();
    const arrival = this.arrival(local);
    const wait = v.readyIn(spell, arrival);
    if (wait === 0) this.sendCast(spell, arrival, local);
    else if (wait !== null && wait <= BUFFER_MS) this.buffered = { spell, untilLocal: local + wait + BUFFER_MS };
    else this.hud.refused(spell, refusalText(v.refusal(spell, arrival) ?? "not_in_loadout"));
  }

  /** Duel time at which a message sent now reaches the server. */
  private arrival(local: number): number {
    const rtt = this.opts.rttMs();
    return this.clock.now(local, rtt) + DuelClock.oneWay(rtt);
  }

  private sendCast(spell: number, arrival: number, local: number): void {
    this.opts.send(encodeDuelCast(spell));
    this.view!.predict(spell, arrival, local);
    this.selfAnimUntil = local + this.opts.castAnimMs;
    this.buffered = null;
  }

  // ---------------------------------------------------------------- input

  handleKey(e: KeyboardEvent): boolean {
    if (this.loadout.handleKey(e) || this.spectate.handleKey(e)) return true;
    const v = this.view;
    if (!v || !v.you || e.ctrlKey || e.metaKey || e.altKey) return false;
    const m = /^Digit([1-9])$/.exec(e.code);
    if (!m) return false;
    const spell = v.loadout[Number(m[1]) - 1];
    if (spell === undefined) return false;
    if (!e.repeat) this.tryCast(spell);
    return true;
  }

  // ---------------------------------------------------------------- frame

  /** The HUD tick: are we at the Dueling Club? */
  hudTick(feet: Vec3): void {
    const area = this.view ? null : this.inArea(feet[0], feet[1], feet[2]);
    if (area !== this.area) {
      this.area = area;
      this.badge.show(area ? (venueByArea(area.id)?.name ?? area.name) : null);
      if (!area) this.spectate.toggle(false);
    }
  }

  /**
   * Before the characters are drawn: plays the cast animation for our own
   * predicted cast and the duelists' telegraphed ones (standing only: a
   * moving duelist keeps walking), and notes where the duelists are.
   */
  pose(me: (PoseRef & { id: number }) | null, visible: readonly { id: number; pose: PoseRef }[], local: number): void {
    const v = this.view;
    if (!v) return;
    this.poses[0].seen = this.poses[1].seen = false;
    if (me && me.id >= 0) this.notePose(v, me.id, me, local, true);
    for (const r of visible) this.notePose(v, r.id, r.pose, local, false);
  }

  private notePose(v: DuelView, id: number, p: PoseRef, local: number, self: boolean): void {
    const side = v.sideOf(id);
    if (side === null) return;
    const i = sideIndex(side);
    const until = self ? this.selfAnimUntil : this.sideAnimUntil[i];
    if (local < until && !v.ended && (p.anim === "idle" || p.anim === "cast")) p.anim = "cast";
    const s = this.poses[i];
    s.x = p.x;
    s.y = p.y;
    s.z = p.z;
    s.yaw = p.yaw;
    s.seen = true;
  }

  /** Where `side` is drawn, or its mark on the stage when it is not drawn. */
  private drawnPose(side: DuelSide): DuelistPose | null {
    const s = this.poses[sideIndex(side)];
    if (s.seen) return s;
    const mark = venue(this.zone?.kind === "arena" ? this.zone.venue : "dueling_club")?.duelists[sideIndex(side)];
    return mark ? { x: mark.pos[0], y: mark.pos[1], z: mark.pos[2], yaw: mark.yaw } : null;
  }

  /** Every frame: held casts, the VFX and the duel HUD. */
  frame(dt: number, local: number, camera: Camera): void {
    const v = this.view;
    if (!v && !this.vfx.busy) return;
    this.vfx.begin(dt);
    if (v) {
      const rtt = this.opts.rttMs();
      const now = this.clock.now(local, rtt);
      if (v.expirePending(local - PENDING_GRACE_MS - 2 * (rtt ?? 100))) this.selfAnimUntil = 0;
      if (this.buffered) {
        const b = this.buffered;
        const arrival = this.arrival(local);
        const wait = v.readyIn(b.spell, arrival);
        if (wait === 0) this.sendCast(b.spell, arrival, local);
        else if (wait === null || local > b.untilLocal) {
          this.buffered = null;
          this.hud.refused(b.spell, refusalText(v.refusal(b.spell, arrival) ?? "busy"));
        }
      }
      this.arena.draw(v, now, local, DuelClock.oneWay(rtt), this.poseOf);
      this.placeAnchors(v);
      this.hud.render(now, local, this.anchors);
    }
    this.vfx.end(camera);
  }

  /** Where callouts float: above each duelist's head on screen. */
  private placeAnchors(v: DuelView): void {
    const m = this.opts.viewProjection();
    const { width, height } = this.opts.viewport();
    for (const side of ["a", "b"] as const) {
      const i = sideIndex(side);
      const p = this.drawnPose(side);
      const head = p ? this.opts.headHeight(v.info(side).entity) || CHEST_M + 0.5 : 0;
      const at = p ? project(m, p.x, p.y + head + 0.15, p.z, width, height) : null;
      if (at && at.x >= 0 && at.x <= width && at.y >= 0 && at.y <= height) {
        const a = this.anchorPool[i];
        a.x = at.x;
        // Clear of the HUD bar, so a word over a distant head is never hidden under it.
        a.y = Math.max(ANCHOR_TOP_PX, at.y);
        this.anchors[i] = a;
      } else this.anchors[i] = null;
    }
  }

  /** Lines for the F3 overlay. */
  perfLines(): [string, string][] {
    const v = this.view;
    const lines: [string, string][] = [this.vfx.perfLine()];
    if (v) lines.push(["duel", `#${v.duelId} ${v.you ? `as ${v.you}` : "watching"}, ${v.projectiles.size} in flight, clock ${this.clock.synced ? "synced" : "waiting"}`]);
    return lines;
  }

  dispose(): void {
    this.disposed = true;
    this.toasts.dispose();
    this.hud.dispose();
    this.loadout.dispose();
    this.spectate.dispose();
    this.badge.root.remove();
    this.vfx.dispose();
  }
}
