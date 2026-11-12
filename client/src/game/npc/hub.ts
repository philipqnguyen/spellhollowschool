// The open castle's residents in the running game (docs/architecture.md
// "NPCs"), in one place for game.ts:
//
// - the talking portraits (portraits.ts): alive near us, nodding or saying
//   one short subtitle line as we pass (chatter.ts `PassWatch`, the
//   anti-annoyance rules), a conversation on interact;
// - the entity NPCs the server moves (ghosts, staff, shopkeepers, students;
//   `EntityInfo.npc` names the roster id): their style (a ghost's
//   translucent float, a student's cheap LOD), nameplates (none for
//   students), who may be selected (never a student), and talk: E sends
//   `NpcInteract` (a shopkeeper: its station's `Interact`, the shop opening
//   as before) and the reply opens the talk card, a gift with a toast; a
//   clerk (the clubs secretary) opens her desk instead (`desk`);
// - Settings > Portrait chatter (On / Quiet / Off).
//
// Every line is picked here (`NpcBook.pick`): nothing an NPC says is sent,
// so nobody else ever sees it.

import type { CastleLibrary } from "../castle/materials";
import type { Scene } from "@babylonjs/core/scene";
import { audio } from "../../audio";
import { defName } from "../../economy/items";
import type { CupHouseView } from "../../ui/house-cup-state";
import { Subtitles, TalkCard, TalkPrompt } from "../../ui/npc-talk";
import { ClassToasts } from "../../ui/wayfinding";
import { encodeInteract, encodeNpcInteract, type Appearance, type House, type LineContext, type LineWhen, type NpcGift, type NpcKind, type NpcReply, type NpcView, type Period, type ServerMessage } from "../../wasm/sim_wasm";
import type { CharacterStyle } from "../characters/renderer";
import type { Figure } from "../characters/cast";
import type { WorldData } from "../world";
import { npcLabel, Npcs } from "./book";
import { continueConversation, localPassStore, nextLine, PassWatch, type ChatterMode, type Conversation } from "./chatter";
import { cupStanding, fillLine, giftKind, lineContext, randomSeed, type LineVars } from "./lines";
import { TalkingPortraits } from "./portraits";
import { FLOOR_BAND_M, inFrontOf } from "./portrait-state";
import { hasPlate, SAME_FLOOR_M, selectable, talksByNpcInteract, talkTarget, type TalkEntity, type TalkTarget } from "./residents";
import { ghostStyle, STUDENT_STYLE } from "./styles";

/** How often pass-bys and the talk prompt are checked (s). */
const PASS_INTERVAL = 0.25;
const PROMPT_INTERVAL = 0.2;
/** A reply not come within this (ms) frees the next interact. */
const REPLY_WAIT_MS = 2000;
/** A gift's toast stays up this long (ms). */
const GIFT_TOAST_MS = 9000;
/** The NPC we talk to is looked at by us, and looks at us, for this long after a line (ms). */
const ENGAGED_MS = 10_000;

export interface NpcHubOptions {
  scene: Scene;
  /** The castle's shared materials (the portrait frames' trim sheet). */
  library?: CastleLibrary;
  hudRoot: HTMLElement;
  assetsBase: string;
  castle: WorldData;
  house: House;
  houseName: string;
  characterName: string;
  send: (frame: Uint8Array) => void;
  notify: (text: string) => void;
  /** Settings > Portrait chatter, as it is now. */
  chatter: () => ChatterMode;
  /** The roster NPCs in view: entity id and roster id (professors and duelists included; they are not in the book). */
  inView: () => Iterable<[number, string]>;
  npcOf: (id: number) => string | null;
  poseOf: (id: number) => { x: number; y: number; z: number; yaw: number } | null;
  headHeight: (id: number) => number;
  /** Our feet while we walk the castle (null in a room, while loading, on a broom). */
  feet: () => [number, number, number] | null;
  selected: () => number | null;
  period: () => Period | null;
  cupHouses: () => readonly CupHouseView[] | null;
  appearance: () => Appearance | null;
  found: () => ReadonlySet<string> | null;
  /** Whether a door's or station's prompt is up (it wins E; ours hides). */
  doorPrompt: () => boolean;
  /** Whether we stand within the station's own reach (the server's rule); a shopkeeper is talked to only then. */
  stationInReach: (station: string) => boolean;
  /** The server's Unix ms (today's hint is the server's UTC day). */
  serverNow: () => number;
  openLore: () => void;
  /** A shopkeeper greets us in its shop's window (`kind`: the station's), which opens with the same message. */
  greet: (kind: "owl_exchange" | "owl_order", name: string, text: string) => void;
  /** A clerk (the clubs secretary) is talked to at her desk: entity `id`, her name and title. */
  desk?: (id: number, name: string, title: string | undefined) => void;
  /** Whether a clerk's desk is open (our prompt hides meanwhile). */
  deskOpen?: () => boolean;
}

/** A stable u32 from our character's name (today's hint varies by character; the client knows no character id). */
export function nameSeed(name: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export class NpcHub {
  readonly npcs: Npcs | null;
  private readonly portraits: TalkingPortraits | null;
  private readonly pass: PassWatch | null;
  private readonly card: TalkCard;
  private readonly subtitles = new Subtitles();
  private readonly prompt: TalkPrompt;
  private readonly toasts = new ClassToasts();
  private readonly ghost: CharacterStyle | null;
  private readonly inRange: string[] = [];
  private readonly entities: TalkEntity[] = [];
  private readonly entityPool: TalkEntity[] = [];
  private conv: Conversation | null = null;
  /** The resident the card's conversation is with: entity id (portraits: null). */
  private talkingTo: number | null = null;
  private engagedAt = -Infinity;
  /** Asked and not yet answered: whom, and when (local ms). */
  private pending: { id: number; npc: string; at: number } | null = null;
  /** NPCs whose "given today" line was said this session (said once, then plain talk). */
  private readonly givenSaid = new Set<string>();
  private passTimer = 0;
  private promptTimer = 0;
  private readonly seed: number;

  constructor(private readonly opts: NpcHubOptions) {
    this.npcs = Npcs.load(opts.castle.layoutText);
    this.seed = nameSeed(opts.characterName);
    this.portraits = this.npcs ? new TalkingPortraits(opts.scene, this.npcs, opts.assetsBase, opts.headHeight, opts.library) : null;
    this.pass = this.npcs ? new PassWatch(this.npcs.rules, localPassStore(`shs.npc.passed.${this.seed.toString(36)}`)) : null;
    this.ghost = this.npcs ? ghostStyle(this.npcs.rules.ghost_float_m) : null;
    this.card = new TalkCard({ onMore: () => this.talk(), onClose: () => this.closed() });
    this.prompt = new TalkPrompt(() => this.talk());
    this.toasts.root.classList.add("npc-toasts");
    opts.hudRoot.append(this.card.root, this.subtitles.root, this.prompt.root, this.toasts.root);
  }

  // ---------------------------------------------------------------- who is who

  /** The roster NPC entity `id` is (undefined: a player, a professor, a duelist). */
  private residentOf(id: number): NpcView | undefined {
    return this.npcs?.npc(this.opts.npcOf(id));
  }

  /** The roster kind of entity `id` (null: a player, a professor). */
  kindOf(id: number): NpcKind | null {
    return this.residentOf(id)?.kind ?? null;
  }

  /** Whether entity `id` may be selected, targeted, challenged, traded with or spelled (never a student). */
  selectable(id: number): boolean {
    return selectable(this.kindOf(id));
  }

  /** How entity `id` is drawn apart from its look (a ghost, a student), for the cast. */
  readonly styleOf = (id: number): CharacterStyle | null => {
    const kind = this.kindOf(id);
    if (kind === "ghost") return this.ghost;
    if (kind === "student") return STUDENT_STYLE;
    return null;
  };

  /** Entity `id`'s nameplate: null for none (students), a resident's name and title, or undefined: not a resident (the usual plate). */
  plate(id: number): { text: string } | null | undefined {
    const n = this.residentOf(id);
    if (!n) return undefined;
    if (!hasPlate(n.kind)) return null;
    return { text: npcLabel(n) };
  }

  /** Whether we are talking with entity `id` now (it looks at us; we at it). */
  engaged(id: number): boolean {
    return this.talkingTo === id && performance.now() - this.engagedAt < ENGAGED_MS;
  }

  // ---------------------------------------------------------------- per frame

  /** The portrait subjects to draw this frame (in the castle only), for the cast. */
  figures(nowMs: number): readonly Figure[] {
    if (!this.portraits) return [];
    const feet = this.opts.feet();
    const head = feet ? { x: feet[0], y: feet[1] + 1.55, z: feet[2] } : null;
    return this.portraits.update(feet, head, nowMs);
  }

  /** Once a frame: pass-bys, the prompt, subtitles and toasts. */
  update(dt: number): void {
    const now = performance.now();
    this.subtitles.tick(now);
    this.toasts.tick(now);
    if (!this.npcs) return;
    this.passTimer -= dt;
    if (this.passTimer <= 0) {
      this.passTimer = PASS_INTERVAL;
      this.passBy();
    }
    this.promptTimer -= dt;
    if (this.promptTimer <= 0) {
      this.promptTimer = PROMPT_INTERVAL;
      this.updatePrompt();
      this.closeIfLeft();
    }
  }

  /** The card goes when we walk (or are moved) out of the speaker's reach, or it leaves our view. */
  private closeIfLeft(): void {
    if (!this.card.isOpen || !this.npcs) return;
    const speaker = this.card.speaker;
    const feet = this.opts.feet();
    let at: { x: number; y: number; z: number } | null = null;
    if (feet && speaker) {
      if (this.talkingTo !== null) at = this.opts.poseOf(this.talkingTo);
      else {
        const v = this.portraits?.canvasOf(speaker);
        if (v) at = { x: v.pos[0]!, y: v.pos[1]! - 2, z: v.pos[2]! };
      }
    }
    const reach = this.npcs.rules.talk_reach_m + 1;
    if (!at || !feet || Math.abs(at.y - feet[1]) > SAME_FLOOR_M || Math.hypot(at.x - feet[0], at.z - feet[2]) > reach) this.card.close();
  }

  private passBy(): void {
    const npcs = this.npcs!;
    const feet = this.opts.feet();
    const near = this.inRange;
    near.length = 0;
    if (feet) {
      const r = npcs.rules.pass_radius_m;
      for (const p of npcs.portraits) {
        const dy = p.pos[1]! - feet[1];
        if (dy < FLOOR_BAND_M[0] || dy > FLOOR_BAND_M[1]) continue;
        // Never through the wall: in the room next door a portrait is not there.
        if (!inFrontOf(p, feet[0], feet[2])) continue;
        if (Math.hypot(p.pos[0]! - feet[0], p.pos[2]! - feet[2]) <= r) near.push(p.npc);
      }
    }
    const out = this.pass!.step(Date.now(), near, this.opts.chatter(), Math.random);
    if (!out) return;
    const now = performance.now();
    this.portraits?.nod(out.npc, now);
    if (out.kind !== "line") return;
    const n = npcs.npc(out.npc);
    const text = this.say(out.npc, "pass", this.context(null, null), randomSeed(), []);
    if (n && text) this.subtitles.say(n.name, text, npcs.rules.subtitle_ms, now);
  }

  private updatePrompt(): void {
    const t = this.findTarget();
    // The card says "E More" itself while it is up.
    if (!t || this.opts.doorPrompt() || this.card.isOpen || this.opts.deskOpen?.()) {
      this.prompt.set(null);
      return;
    }
    const n = this.npcs!.npc(t.npc);
    this.prompt.set(n ? `Talk to ${n.name}` : null);
  }

  private findTarget(): TalkTarget | null {
    const npcs = this.npcs!;
    const feet = this.opts.feet();
    if (!feet) return null;
    const list = this.entities;
    list.length = 0;
    for (const [id, npcId] of this.opts.inView()) {
      const n = npcs.npc(npcId);
      const pose = n ? this.opts.poseOf(id) : null;
      if (!n || !pose) continue;
      // A shopkeeper opens its shop through the station's Interact, which the
      // server grants only within the station's reach (often less than ours).
      if (n.kind === "shopkeeper" && !(n.station && this.opts.stationInReach(n.station))) continue;
      const e = (this.entityPool[list.length] ??= { id: 0, npc: "", kind: "staff", x: 0, y: 0, z: 0 });
      e.id = id;
      e.npc = n.id;
      e.kind = n.kind;
      e.x = pose.x;
      e.y = pose.y;
      e.z = pose.z;
      list.push(e);
    }
    return talkTarget({ x: feet[0], y: feet[1], z: feet[2] }, list, npcs.portraits, npcs.rules.talk_radius_m, this.opts.chatter(), this.opts.selected());
  }

  // ---------------------------------------------------------------- talk

  /** E: the next line with whoever the card shows, else whoever is in reach. True when E was ours. */
  handleKey(e: KeyboardEvent): boolean {
    if (this.card.handleKey(e)) return true;
    if (e.code !== "KeyE" || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return false;
    // A door's or station's prompt wins E (its own handler has had the key first).
    if (this.opts.doorPrompt()) return false;
    return this.talk();
  }

  /** A double-click on a resident (selected by that click): talk to it when it is in reach. */
  talkSelected(): void {
    this.talk();
  }

  /** Talks to the resident in reach (or the card's): true when someone was there. */
  private talk(): boolean {
    if (!this.npcs) return false;
    const t = this.findTarget();
    if (!t) {
      // Walked away: the card goes.
      if (this.card.isOpen) {
        this.card.close();
        return true;
      }
      return false;
    }
    const now = performance.now();
    // The conversation said its last line: E puts the card away.
    if (this.card.isOpen && this.card.speaker === t.npc && this.conv && this.conv.npc === t.npc && this.conv.said.length >= this.npcs.rules.talk_lines_max) {
      this.card.close();
      return true;
    }
    if (t.kind === "portrait") {
      this.talkPortrait(t.npc, now);
      return true;
    }
    const n = this.npcs.npc(t.npc)!;
    if (n.kind === "shopkeeper") {
      // The shop opens exactly as from its counter; the greeting comes with it (stationOpened).
      if (n.station) this.opts.send(encodeInteract(n.station));
      return true;
    }
    if (n.kind === "clerk") {
      // Her desk's own conversation (the clubs secretary: game/club.ts); E again leaves it be.
      if (!this.opts.deskOpen?.()) {
        this.talkingTo = t.id;
        this.engagedAt = now;
        this.opts.desk?.(t.id, n.name, n.title);
      }
      return true;
    }
    if (!talksByNpcInteract(n.kind)) return false;
    if (this.pending && this.pending.id === t.id && now - this.pending.at < REPLY_WAIT_MS) return true;
    this.pending = { id: t.id, npc: n.id, at: now };
    this.opts.send(encodeNpcInteract(t.id));
    return true;
  }

  private talkPortrait(npc: string, now: number): void {
    const npcs = this.npcs!;
    const n = npcs.npc(npc);
    if (!n) return;
    const cx = this.context(null, null);
    const conv = (this.conv = continueConversation(this.conv, npc, now));
    // A hint only once we know which secrets we found: never one already found.
    const known = this.opts.found() !== null;
    let daily: number | undefined;
    if (known && n.daily_hint && conv.said.length === 0) {
      try {
        daily = npcs.book.dailyHint(npc, cx, this.opts.serverNow(), this.seed);
      } catch {
        daily = undefined;
      }
    }
    const seed = randomSeed();
    const next = nextLine(conv, npcs.rules, { dailyHint: daily, hints: known, roll: Math.random(), pick: (when, exclude) => this.pickIndex(npc, when, cx, seed, exclude) });
    if (!next) {
      this.card.close();
      return;
    }
    this.showLine(n, null, next.index, {}, now);
  }

  /** The server's answer to our `NpcInteract`. */
  private onReply(id: number, reply: NpcReply): void {
    const npcs = this.npcs;
    if (!npcs) return;
    const npcId = this.opts.npcOf(id) ?? (this.pending?.id === id ? this.pending.npc : null);
    if (this.pending?.id === id) this.pending = null;
    const n = npcs.npc(npcId);
    if (!n) return;
    const now = performance.now();
    switch (reply.kind) {
      case "talk":
        this.residentTalk(n, id, reply.givenToday, now);
        break;
      case "gift":
        this.residentGift(n, id, reply.gift, now);
        break;
      case "refused":
        switch (reply.code) {
          case "too_far":
            this.opts.notify(`Step closer to talk to ${n.name}.`);
            break;
          case "rate_limited":
            // Words cost nothing: the line is ours either way.
            this.residentTalk(n, id, false, now);
            break;
          default:
            this.opts.notify(`${n.name} does not answer just now.`);
        }
        break;
    }
  }

  private residentTalk(n: NpcView, id: number, givenToday: boolean, now: number): void {
    const npcs = this.npcs!;
    const cx = this.context(n.house ?? null, null);
    const seed = randomSeed();
    if (n.kind === "student") {
      // A bark: one short subtitle, no conversation.
      const text = this.say(n.id, "talk", cx, seed, []);
      if (text) this.subtitles.say(n.name, text, npcs.rules.subtitle_ms, now);
      return;
    }
    const conv = (this.conv = continueConversation(this.conv, n.id, now));
    if (givenToday && conv.said.length === 0 && !this.givenSaid.has(n.id)) {
      const given = this.pickIndex(n.id, "given", cx, seed, []);
      if (given !== undefined) {
        this.givenSaid.add(n.id);
        this.showLine(n, id, given, {}, now);
        return;
      }
    }
    const next = nextLine(conv, npcs.rules, { dailyHint: undefined, hints: false, roll: 1, pick: (when, exclude) => this.pickIndex(n.id, when, cx, seed, exclude) });
    if (!next) {
      this.card.close();
      return;
    }
    this.showLine(n, id, next.index, {}, now);
  }

  private residentGift(n: NpcView, id: number, gift: NpcGift, now: number): void {
    const kind = giftKind(gift);
    const cx = this.context(n.house ?? null, kind);
    const vars = giftVars(gift);
    const conv = (this.conv = continueConversation(this.conv, n.id, now));
    const index = this.pickIndex(n.id, "gift", cx, randomSeed(), conv.said) ?? this.pickIndex(n.id, "talk", cx, randomSeed(), conv.said);
    if (index !== undefined) this.showLine(n, id, index, vars, now);
    this.givenSaid.add(n.id);
    const what = giftWords(gift, this.opts.houseName);
    this.opts.notify(`${n.name} gave you ${what}.`);
    audio.play("find.found");
    const lore = gift.kind === "lore";
    this.toasts.show(`npc-gift:${n.id}`, `${n.name} gave you ${what}.`, now + GIFT_TOAST_MS, lore ? () => this.opts.openLore() : null, "Read it");
  }

  /** Shows line `index` of `n` on the card (as said in this conversation), placeholders filled. */
  private showLine(n: NpcView, id: number | null, index: number, vars: Partial<LineVars>, now: number): void {
    const raw = this.npcs!.book.text(n.id, index);
    if (raw === undefined) return;
    const conv = (this.conv = continueConversation(this.conv, n.id, now));
    conv.said.push(index);
    conv.at = now;
    this.talkingTo = id;
    this.engagedAt = now;
    const text = fillLine(raw, { name: this.opts.characterName, house: this.opts.houseName, ...vars });
    this.card.show(n.id, n.name, n.title, text, conv.said.length >= this.npcs!.rules.talk_lines_max);
  }

  private closed(): void {
    this.talkingTo = null;
    if (this.conv && this.conv.said.length >= (this.npcs?.rules.talk_lines_max ?? 4)) this.conv = null;
  }

  // ---------------------------------------------------------------- lines

  private pickIndex(npc: string, when: LineWhen, cx: LineContext, seed: number, exclude: readonly number[]): number | undefined {
    try {
      return this.npcs!.book.pick(npc, when, cx, seed, Uint16Array.from(exclude)) ?? undefined;
    } catch (e) {
      console.warn("[npcs] no line:", e);
      return undefined;
    }
  }

  /** A `when` line of `npc`, filled, or null. */
  private say(npc: string, when: LineWhen, cx: LineContext, seed: number, exclude: readonly number[]): string | null {
    const i = this.pickIndex(npc, when, cx, seed, exclude);
    const raw = i === undefined ? undefined : this.npcs!.book.text(npc, i);
    return raw === undefined ? null : fillLine(raw, { name: this.opts.characterName, house: this.opts.houseName });
  }

  private context(npcHouse: House | null, gift: LineContext["gift"]): LineContext {
    return lineContext({
      house: this.opts.house,
      npcHouse,
      period: this.opts.period(),
      cup: cupStanding(this.opts.cupHouses(), this.opts.house),
      appearance: this.opts.appearance(),
      found: this.opts.found(),
      gift,
    });
  }

  // ---------------------------------------------------------------- messages

  /** `npcReply` is ours (true); `stationOpened` is only watched (a shopkeeper's greeting), as are zone changes. */
  onMessage(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "npcReply":
        this.onReply(msg.id, msg.reply);
        return true;
      case "stationOpened":
        if (msg.kind === "owl_exchange" || msg.kind === "owl_order") this.greet(msg.kind, msg.target);
        return false;
      case "welcome":
      case "zoneChanged":
        this.leaving();
        return false;
      case "snapshot":
        this.lastTick = msg.tick;
        return false;
      default:
        return false;
    }
  }

  /** The shop at `station` opened: its keeper, when in view, greets us in the shop's window (a subtitle would sit under it). */
  private greet(kind: "owl_exchange" | "owl_order", station: string): void {
    const keeper = this.npcs?.keeperOf(station);
    if (!keeper) return;
    let seen = false;
    for (const [, npcId] of this.opts.inView()) if (npcId === keeper.id) seen = true;
    if (!seen) return;
    const text = this.say(keeper.id, "shop", this.context(keeper.house ?? null, null), randomSeed(), []);
    if (text) this.opts.greet(kind, keeper.name, text);
  }

  /**
   * Development only (`?npcdev`, game.ts): a snapshot that brings roster NPC
   * `npc` into view as entity `id` at `pos`, as the server will once the
   * residents spawn (client playtests without the server's residents). Null for an unknown NPC.
   */
  devSnapshot(id: number, npc: string, pos: readonly [number, number, number], yaw: number, anim: "idle" | "walk" | "sit"): ServerMessage | null {
    const n = this.npcs?.npc(npc);
    const look = n ? this.npcs!.book.look(npc) : undefined;
    if (!n || !look) return null;
    this.lastTick++;
    return {
      type: "snapshot",
      tick: this.lastTick,
      entered: [{ id, character: { name: n.name, house: n.house ?? this.opts.house }, appearanceVersion: 1, appearance: look, effectsVersion: 0, effects: [], level: 0, npc }],
      states: [{ id, x: pos[0], y: pos[1], z: pos[2], yaw, anim }],
      left: [],
      looks: [],
    };
  }

  private lastTick = 0;

  /** A zone change or a new connection: the card and the lines go. */
  leaving(): void {
    this.card.close();
    this.subtitles.clear();
    this.conv = null;
    this.pending = null;
    this.prompt.set(null);
  }

  perfLines(): [string, string][] {
    if (!this.npcs) return [["npcs", "unavailable"]];
    let n = 0;
    let students = 0;
    for (const [, id] of this.opts.inView()) {
      const k = this.npcs.kind(id);
      if (!k) continue;
      n++;
      if (k === "student") students++;
    }
    return [this.portraits!.perfLine(), ["residents", `${n} in view (${students} students), chatter ${this.opts.chatter()}, talking ${this.conv ? this.conv.npc : "to nobody"}`]];
  }

  dispose(): void {
    this.portraits?.dispose();
    this.npcs?.dispose();
    this.card.root.remove();
    this.subtitles.root.remove();
    this.prompt.root.remove();
    this.toasts.root.remove();
  }
}

/** The placeholders a gift line fills. */
export function giftVars(g: NpcGift): Partial<LineVars> {
  switch (g.kind) {
    case "lore":
      return { lore: g.title };
    case "item":
      return { item: g.quantity > 1 ? `${g.quantity} × ${defName(g.itemDef)}` : defName(g.itemDef) };
    case "housePoint":
      return { points: g.points === 1 ? "1 point" : `${g.points} points` };
    case "hint":
      return { place: g.place };
  }
}

/** What a gift is, in words for the chat line and the toast. */
export function giftWords(g: NpcGift, houseName: string): string {
  switch (g.kind) {
    case "lore":
      return `a page for your lore book: “${g.title}”`;
    case "item":
      return g.quantity > 1 ? `${g.quantity} × ${defName(g.itemDef)}` : defName(g.itemDef);
    case "housePoint":
      return `${g.points} ${g.points === 1 ? "point" : "points"} for ${houseName}`;
    case "hint":
      return `a hint: try ${g.place}`;
  }
}
