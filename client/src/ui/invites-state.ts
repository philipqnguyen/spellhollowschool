// Incoming dorm invitations (`DormInviteReceived`): each is valid for
// `expires_in_secs` from when it arrived, counted on the local clock. Pure so
// it is unit tested; invites.ts shows them as toasts.

export interface Invite {
  inviteId: number;
  fromName: string;
  /** Local time (ms, `performance.now()` clock) when it lapses. */
  expiresAt: number;
}

/** At most this many toasts at once; a newer invite pushes out the oldest. */
export const MAX_INVITES = 3;

export class InviteQueue {
  private list: Invite[] = [];

  /** Adds an invitation; a newer one from the same person replaces theirs. */
  add(inviteId: number, fromName: string, expiresInSecs: number, nowMs: number): Invite {
    const invite = { inviteId, fromName, expiresAt: nowMs + expiresInSecs * 1000 };
    this.list = this.list.filter((i) => i.fromName !== fromName);
    this.list.push(invite);
    if (this.list.length > MAX_INVITES) this.list.splice(0, this.list.length - MAX_INVITES);
    return invite;
  }

  remove(inviteId: number): Invite | undefined {
    const i = this.list.find((x) => x.inviteId === inviteId);
    this.list = this.list.filter((x) => x.inviteId !== inviteId);
    return i;
  }

  /** Drops lapsed invitations; returns them. */
  expire(nowMs: number): Invite[] {
    const gone = this.list.filter((i) => i.expiresAt <= nowMs);
    if (gone.length) this.list = this.list.filter((i) => i.expiresAt > nowMs);
    return gone;
  }

  get items(): readonly Invite[] {
    return this.list;
  }

  clear(): void {
    this.list = [];
  }
}

/** Whole seconds left, rounded up, for the countdown ("0:42"). */
export function secondsLeft(invite: Invite, nowMs: number): number {
  return Math.max(0, Math.ceil((invite.expiresAt - nowMs) / 1000));
}

export function formatCountdown(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
