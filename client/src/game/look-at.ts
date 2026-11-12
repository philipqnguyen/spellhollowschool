// Characters look at their target (docs/architecture.md "Anims and emotes"):
// whom each drawn character looks at, for the renderer's
// head turn (characters/look.ts).
//
// - Our own character, from our own state at once: the duel opponent, else
//   the piece Elevara is aimed at (a point, drawn here only),
//   else the trade partner, else the selected character (click or Tab).
//   Each must be drawn, not under the Invisibility Draught, and within
//   `lookRangeM` (20 m); the first that is wins, and none means no look.
//   The character we look at goes to the server (`encodeLookAt`) only when
//   it changes, at most `lookSendsPerSec` a second, the last change always
//   following once the rate allows (the server keeps it per player and
//   shows it to everyone near, protocol v10).
// - Other players: what the server says (`snapshot.looks`), only while the
//   target is drawn here.
// - NPCs (the professors), here only: the student who has them selected or
//   just answered in their class, else the nearest student within
//   `NPC_NEAR_M`, kept while still near so two students at the same
//   distance do not make the head flick between them.
//
// Pure apart from the frames it hands to `send`; unit tested in
// look-at.test.ts.

import type { ServerMessage } from "../wasm/sim_wasm";

/** An NPC turns to the nearest student within this (m)... */
export const NPC_NEAR_M = 4;
/** ...and keeps them while they stay within this, unless another comes this much nearer (m). */
export const NPC_KEEP_M = 5;
export const NPC_SWITCH_MARGIN_M = 0.75;
/** A student who answered in class is looked at for this long (ms). */
export const NPC_ANSWER_MS = 2500;

export interface Point {
  x: number;
  y: number;
  z: number;
}

/** What our own character could look at, by priority (null: nothing of that kind). */
export interface OwnLookContext {
  duelOpponent: number | null;
  /** The piece Elevara is aimed at (its middle). */
  spellAim: Point | null;
  tradePartner: number | null;
  selected: number | null;
}

/** Whom (or where) a character looks at: an entity id or -1, and a point used instead when set (our spell aim). */
export interface LookChoice {
  target: number;
  point: Point | null;
}

/**
 * Our own look: the first of duel opponent, spell aim, trade partner and
 * selection that `canSee` (drawn, visible, in range; the aim's point is
 * always in reach). `shared` is what the server is told: the chosen
 * character, or -1 for a point or nothing.
 */
export function chooseOwnLook(ctx: OwnLookContext, canSee: (id: number) => boolean): LookChoice & { shared: number } {
  if (ctx.duelOpponent !== null && canSee(ctx.duelOpponent)) return { target: ctx.duelOpponent, point: null, shared: ctx.duelOpponent };
  if (ctx.spellAim) return { target: -1, point: ctx.spellAim, shared: -1 };
  for (const id of [ctx.tradePartner, ctx.selected]) {
    if (id !== null && canSee(id)) return { target: id, point: null, shared: id };
  }
  return { target: -1, point: null, shared: -1 };
}

/**
 * The look sent to the server: each change once, at most `perSec` a second;
 * a change that comes too soon after the last goes out as soon as it may
 * (the newest one). `reset` after a new connection or zone (the server then
 * holds none).
 */
export class LookSender {
  private sent = -1;
  private sentAt = -Infinity;
  private readonly gapMs: number;

  constructor(perSec: number) {
    this.gapMs = 1000 / Math.max(1, perSec);
  }

  /** The target (-1: none) to send now, or null when there is nothing to send yet. */
  update(nowMs: number, want: number): number | null {
    if (want === this.sent || nowMs - this.sentAt < this.gapMs) return null;
    this.sent = want;
    this.sentAt = nowMs;
    return want;
  }

  reset(): void {
    this.sent = -1;
    this.sentAt = -Infinity;
  }
}

/** A character an NPC could look at. */
export interface Student {
  id: number;
  x: number;
  y: number;
  z: number;
}

/**
 * Whom an NPC at `npc` looks at: `engaged` (a student talking to it or
 * answering, -1: none) when within `range`, else the nearest student
 * within `NPC_NEAR_M`, keeping `current` while it stays within
 * `NPC_KEEP_M` and nobody is `NPC_SWITCH_MARGIN_M` nearer. -1: nobody.
 */
export function npcLook(npc: Point, students: readonly Student[], current: number, engaged: number, range: number): number {
  let nearest = -1;
  let nearestD = Infinity;
  let currentD = Infinity;
  let engagedD = Infinity;
  for (const s of students) {
    const d = Math.hypot(s.x - npc.x, s.y - npc.y, s.z - npc.z);
    if (s.id === engaged) engagedD = d;
    if (s.id === current) currentD = d;
    if (d < nearestD) {
      nearestD = d;
      nearest = s.id;
    }
  }
  if (engaged >= 0 && engagedD <= range) return engaged;
  if (current >= 0 && currentD <= NPC_KEEP_M && !(nearestD < currentD - NPC_SWITCH_MARGIN_M)) return current;
  return nearestD <= NPC_NEAR_M ? nearest : -1;
}

/** Remote players' look targets as the server last said (`snapshot.looks`), forgotten as they leave. */
export class RemoteLooks {
  private readonly byId = new Map<number, number>();

  snapshot(msg: Extract<ServerMessage, { type: "snapshot" }>): void {
    for (const id of msg.left) this.byId.delete(id);
    // An entering character looks at nobody unless the same snapshot says so.
    for (const e of msg.entered) this.byId.delete(e.id);
    for (const l of msg.looks) {
      if (l.target === null) this.byId.delete(l.id);
      else this.byId.set(l.id, l.target);
    }
  }

  /** Whom `id` looks at (-1: nobody). */
  of(id: number): number {
    return this.byId.get(id) ?? -1;
  }

  /** A new connection or zone: the visible set starts over. */
  clear(): void {
    this.byId.clear();
  }
}

/** A drawn character as the director sees it. */
export interface Drawn {
  id: number;
  pose: Point;
}

/** What the director reads from the game each frame. */
export interface LookHooks {
  selfId(): number;
  /** Our feet, or null before we are in the world. */
  self(): Point | null;
  /** Everyone drawn but us (players and NPCs). */
  drawn(): readonly Drawn[];
  isNpc(id: number): boolean;
  /** Under the Invisibility Draught (no nameplate): never looked at. */
  hidden(id: number): boolean;
  own(): OwnLookContext;
  /** Whether we are engaging NPC `id` (selected it, or just answered in its class). */
  engages(id: number): boolean;
  /** Tells the server whom we look at (null: nobody). */
  send(target: number | null): void;
}

/** Decides every drawn character's look each frame (see the module notes). */
export class LookDirector {
  readonly remote = new RemoteLooks();
  private readonly sender: LookSender;
  private own: LookChoice = { target: -1, point: null };
  private readonly npcs = new Map<number, number>();
  private readonly students: Student[] = [];
  private readonly pool: Student[] = [];

  constructor(
    private readonly hooks: LookHooks,
    private readonly rangeM: number,
    sendsPerSec: number,
  ) {
    this.sender = new LookSender(sendsPerSec);
  }

  /** `snapshot`: the server's look changes. */
  snapshot(msg: Extract<ServerMessage, { type: "snapshot" }>): void {
    this.remote.snapshot(msg);
    for (const id of msg.left) this.npcs.delete(id);
  }

  /** `welcome` or `zoneChanged`: nobody is drawn yet, and the server holds no look of ours. */
  reset(): void {
    this.remote.clear();
    this.npcs.clear();
    this.sender.reset();
    this.own = { target: -1, point: null };
  }

  private find(id: number): Point | null {
    for (const d of this.hooks.drawn()) if (d.id === id) return d.pose;
    return null;
  }

  /** Per frame, before the characters are drawn. */
  frame(nowMs: number): void {
    const me = this.hooks.self();
    const selfId = this.hooks.selfId();
    if (!me || selfId < 0) {
      this.own = { target: -1, point: null };
      return;
    }
    const r2 = this.rangeM * this.rangeM;
    const canSee = (id: number): boolean => {
      if (id === selfId || this.hooks.hidden(id)) return false;
      const p = this.find(id);
      return !!p && (p.x - me.x) ** 2 + (p.y - me.y) ** 2 + (p.z - me.z) ** 2 <= r2;
    };
    const choice = chooseOwnLook(this.hooks.own(), canSee);
    this.own = choice;
    const send = this.sender.update(nowMs, choice.shared);
    if (send !== null) this.hooks.send(send < 0 ? null : send);

    // NPCs: the students drawn here (us included), pooled.
    const students = this.students;
    students.length = 0;
    const add = (id: number, p: Point): void => {
      const s = (this.pool[students.length] ??= { id: 0, x: 0, y: 0, z: 0 });
      s.id = id;
      s.x = p.x;
      s.y = p.y;
      s.z = p.z;
      students.push(s);
    };
    add(selfId, me);
    let npcs = false;
    for (const d of this.hooks.drawn()) {
      if (this.hooks.isNpc(d.id)) npcs = true;
      else if (!this.hooks.hidden(d.id)) add(d.id, d.pose);
    }
    if (!npcs) return;
    for (const d of this.hooks.drawn()) {
      if (!this.hooks.isNpc(d.id)) continue;
      const engaged = this.hooks.engages(d.id) ? selfId : -1;
      this.npcs.set(d.id, npcLook(d.pose, students, this.npcs.get(d.id) ?? -1, engaged, this.rangeM));
    }
  }

  /** Whom `id` looks at now (-1: nobody). */
  targetOf(id: number): number {
    if (id === this.hooks.selfId()) return this.own.target;
    const npc = this.npcs.get(id);
    if (npc !== undefined) return npc;
    return this.remote.of(id);
  }

  /** The point `id` looks at instead of a character (our spell aim), into `out`; false when none. */
  pointOf(id: number, out: Point): boolean {
    const p = id === this.hooks.selfId() ? this.own.point : null;
    if (!p) return false;
    out.x = p.x;
    out.y = p.y;
    out.z = p.z;
    return true;
  }
}
