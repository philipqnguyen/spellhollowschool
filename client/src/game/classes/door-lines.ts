// The class doors' "starting now" lines in the chat: each
// session's line is said once, also across a reconnect or signing back in.
// Signing out inside a class and back in used to repeat that day's open-door
// lines: the game (and with it the memory of what it had said) is new after
// a sign-in. So the sessions already said are kept per character in the
// browser's storage, and a line waits until the game knows the server's time
// and which zone the player is in (inside a class, a door's line is only
// marked as said, not shown). Storage that throws or is missing (a private
// window) falls back to memory: a line may then repeat after a sign-in, as
// before, but never within one.
//
// Pure apart from the storage it is given; unit tested in door-lines.test.ts.

/** The storage key: { "<character>": { "<class id>": <session>, ... }, ... }. */
export const DOOR_LINES_KEY = "shs.doorLines";
/** At most this many characters are remembered (the oldest written goes first). */
const MAX_OWNERS = 8;

type Seen = Record<string, number>;

/** The part of `Storage` used (tests pass a map). */
export interface LineStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** `localStorage`, or null where it is missing or throws. */
export function browserStore(): LineStore | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export class DoorLines {
  private readonly seen: Seen;

  constructor(
    /** Whose lines (the character's name). */
    private readonly owner: string,
    private readonly store: LineStore | null = browserStore(),
  ) {
    this.seen = { ...(this.readAll()[owner] ?? {}) };
  }

  /** Whether class `classId`'s session `session` has not had its line yet. */
  due(classId: string, session: number): boolean {
    const last = this.seen[classId];
    return last === undefined || session > last;
  }

  /** Session `session` of class `classId` has had its line (said, or passed over inside a class). */
  said(classId: string, session: number): void {
    if (!this.due(classId, session)) return;
    this.seen[classId] = session;
    const all = this.readAll();
    delete all[this.owner];
    // Most recently written last: the oldest owners are dropped first.
    const owners = Object.keys(all);
    for (const o of owners.slice(0, Math.max(0, owners.length - (MAX_OWNERS - 1)))) delete all[o];
    all[this.owner] = { ...this.seen };
    try {
      this.store?.setItem(DOOR_LINES_KEY, JSON.stringify(all));
    } catch {
      // Full or blocked storage: remembered for this game only.
    }
  }

  private readAll(): Record<string, Seen> {
    try {
      const raw = this.store?.getItem(DOOR_LINES_KEY);
      const v: unknown = raw ? JSON.parse(raw) : null;
      if (!v || typeof v !== "object" || Array.isArray(v)) return {};
      const out: Record<string, Seen> = {};
      for (const [owner, seen] of Object.entries(v as Record<string, unknown>)) {
        if (!seen || typeof seen !== "object" || Array.isArray(seen)) continue;
        const s: Seen = {};
        for (const [cls, n] of Object.entries(seen as Record<string, unknown>)) if (typeof n === "number" && Number.isFinite(n)) s[cls] = n;
        out[owner] = s;
      }
      return out;
    } catch {
      return {};
    }
  }
}
