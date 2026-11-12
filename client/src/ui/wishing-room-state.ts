// The Wishing Room on the client (docs/architecture.md
// "Secrets and the map"): the door that appears for us in the blank wall of the
// seventh-floor east corridor (`wishingRoomDoor { openUntilMs }`, the
// server's Unix ms; 0 takes it away), how it fades in and out, and the room
// choice card (`encodeEnterWishingRoom`). Pure (no DOM, no wasm), unit
// tested in wishing-room.test.ts; wishing-room.ts draws the card.

import type { WishingRoomKind, WishingRoomView } from "../wasm/sim_wasm";

/** The door fades in (and out) over this long (ms). */
export const DOOR_FADE_MS = 900;

/** One choice on the card. */
export interface RoomChoice {
  room: WishingRoomKind;
  /** "Lost Property Vault" (secrets.json `wishing_room.rooms[].name`). */
  name: string;
  /** Our own line about it. */
  blurb: string;
}

/** Our own lines for the two rooms (the names come from secrets.json). */
const BLURBS: Record<WishingRoomKind, (capacity: number) => string> = {
  lost_property: () => "A towering hall of everything the castle ever mislaid. Search the mound once a day for something rare.",
  gathering: (capacity) => `A warm lounge with sofas, rugs and cushions for up to ${capacity} people. Invite your friends in.`,
};

const FALLBACK_NAMES: Record<WishingRoomKind, string> = { lost_property: "Lost Property Vault", gathering: "Gathering Room" };

/** A room's display name (the rules' name when known). */
export function wishingRoomName(room: WishingRoomKind, rules?: WishingRoomView | null): string {
  return rules?.rooms.find((r) => r.room === room)?.name ?? FALLBACK_NAMES[room];
}

/** The card's choices, in the rules' order (both rooms even before the rules load). */
export function roomChoices(rules: WishingRoomView | null | undefined): RoomChoice[] {
  const capacity = rules?.capacity ?? 20;
  const rooms: WishingRoomKind[] = rules?.rooms.length ? rules.rooms.map((r) => r.room) : ["lost_property", "gathering"];
  return rooms.map((room) => ({ room, name: wishingRoomName(room, rules), blurb: BLURBS[room](capacity) }));
}

/**
 * The door's state for us: open until `untilMs` (server ms; 0 when the
 * server took it away or never opened it). `shownAt` is when it began to
 * show (server ms) and `hiddenAt` when it began to go, for the fades.
 */
export class WishingRoomDoor {
  private until = 0;
  private shownAt = -Infinity;
  private hiddenAt = -Infinity;

  /** `wishingRoomDoor` arrived at server time `nowMs`. Returns "opened", "closed" or null (no change). */
  set(openUntilMs: number, nowMs: number): "opened" | "closed" | null {
    const was = this.isOpen(nowMs);
    // It timed out before this message: its fade keeps counting from then.
    if (!was && this.until > 0) this.hiddenAt = this.until;
    this.until = openUntilMs > nowMs ? openUntilMs : 0;
    const is = this.isOpen(nowMs);
    if (is && !was) {
      this.shownAt = nowMs;
      return "opened";
    }
    if (!is && was) {
      this.hiddenAt = nowMs;
      return "closed";
    }
    return null;
  }

  /** Whether the door is ours to use at `nowMs` (the prompt and the card). */
  isOpen(nowMs: number): boolean {
    return this.until > nowMs;
  }

  /** Server ms the door goes at (0: not open). */
  get openUntil(): number {
    return this.until;
  }

  /** Whole seconds left at `nowMs` (0 when closed). */
  secondsLeft(nowMs: number): number {
    return this.isOpen(nowMs) ? Math.ceil((this.until - nowMs) / 1000) : 0;
  }

  /**
   * How much of the door shows at `nowMs`, 0..1: fading in after opening,
   * fading out after it times out or is taken away.
   */
  alpha(nowMs: number): number {
    if (this.isOpen(nowMs)) return clamp01((nowMs - this.shownAt) / DOOR_FADE_MS);
    // Timed out without a message: it fades from its time; taken away: from then.
    const goneAt = this.until > 0 ? this.until : this.hiddenAt;
    if (!Number.isFinite(goneAt) || !Number.isFinite(this.shownAt)) return 0;
    const fullAt = Math.min(1, (goneAt - this.shownAt) / DOOR_FADE_MS);
    return clamp01(fullAt * (1 - (nowMs - goneAt) / DOOR_FADE_MS));
  }

  /** A new connection or zone: no door. */
  clear(): void {
    this.until = 0;
    this.shownAt = -Infinity;
    this.hiddenAt = -Infinity;
  }
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/** "You found The Weaver's Back Stair!": the discovery toast's line (`discovered`). */
export function discoveryText(name: string, xp: number, housePoint: boolean, recipe: string | null, houseName: string): string {
  const parts = [`+${xp} XP`];
  parts.push(housePoint ? `+1 point for ${houseName}` : `no House point (today's finds already counted)`);
  if (recipe) parts.push(`a new recipe: ${recipe}`);
  return `A hidden passage: ${name}! ${parts.join(", ")}. It is on your map now (M).`;
}

/**
 * The blank wall's whispers (review fix): the server counts our passes
 * between the two pace marks (`sim_core::Pacing`) and says nothing until the
 * door opens, so nothing in the game taught the ritual. This mirrors the
 * same rule on the client, for words only: walking past the wall once, and
 * again, says that something there is paying attention. The door itself
 * still comes only from the server (`wishingRoomDoor`).
 */
export class PaceWhisper {
  /** The mark last stood in and when (ms). */
  private last: { mark: 0 | 1; at: number } | null = null;
  /** When each pass of this run started (ms). */
  private starts: number[] = [];

  constructor(
    private readonly passes: number,
    private readonly windowMs: number,
  ) {}

  /**
   * After a frame on the corridor: `mark` is the pace mark we stand in (null
   * between them). Returns the passes counted when this frame ended one, else 0.
   */
  step(mark: 0 | 1 | null, nowMs: number): number {
    if (mark === null) return 0;
    const last = this.last;
    this.last = { mark, at: nowMs };
    if (!last || last.mark === mark) return 0;
    this.starts.push(last.at);
    this.starts = this.starts.filter((t) => t >= nowMs - this.windowMs);
    const n = this.starts.length;
    if (n >= this.passes) this.reset();
    return n;
  }

  /** Off the corridor (or the door opened): start over. */
  reset(): void {
    this.last = null;
    this.starts = [];
  }
}

/** What the wall seems to do after pass `n` of `passes` (null: nothing; the last pass opens the door). */
export function paceWhisperText(n: number, passes: number): string | null {
  if (n <= 0 || n >= passes) return null;
  if (n === 1) return "As you walk past the long blank wall, the stones seem to turn and watch you go.";
  return "The blank wall stirs as you pass it again, as if it is waiting for you to make up your mind.";
}
