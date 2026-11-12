// Duel challenges as this client knows them: the ones waiting for our
// answer and the one we made, with their countdowns, kept current from
// `DuelChallenged` (to the target, and echoed to the challenger with `from`
// = our own id: that echo is how we learn our challenge's id),
// `DuelChallengeEnded` and `DuelError`. Also the words for every code.
// Pure, unit tested.

import type { ChallengeEnd, DuelErrorCode, ServerMessage } from "../../wasm/sim_wasm";

type Challenged = Extract<ServerMessage, { type: "duelChallenged" }>;

export interface IncomingChallenge {
  challengeId: number;
  from: number;
  fromName: string;
  fromLevel: number;
  /** performance.now() when it lapses. */
  until: number;
}

export interface OutgoingChallenge {
  /** Null until the server's echo names it. */
  challengeId: number | null;
  targetName: string;
  until: number;
}

/** Seconds left until `until` (rounded up, never negative). */
export function secondsLeft(until: number, now: number): number {
  return Math.max(0, Math.ceil((until - now) / 1000));
}

export class ChallengeBook {
  incoming: IncomingChallenge[] = [];
  outgoing: OutgoingChallenge | null = null;

  /** We asked `targetName` (the echo follows with the id). */
  sent(targetName: string, now: number, secs: number): void {
    this.outgoing = { challengeId: null, targetName, until: now + secs * 1000 };
  }

  /** `DuelChallenged`: ours echoed back (`from === selfId`) or one to us. Returns a line for the chat, if any. */
  challenged(msg: Challenged, selfId: number, now: number): string | null {
    const until = now + msg.expiresInSecs * 1000;
    if (msg.from === selfId) {
      const o = this.outgoing;
      if (o) {
        o.challengeId = msg.challengeId;
        o.until = until;
      } else {
        this.outgoing = { challengeId: msg.challengeId, targetName: "your opponent", until };
      }
      return null;
    }
    this.incoming = this.incoming.filter((c) => c.challengeId !== msg.challengeId);
    this.incoming.push({ challengeId: msg.challengeId, from: msg.from, fromName: msg.fromName, fromLevel: msg.fromLevel, until });
    return `${msg.fromName} (level ${msg.fromLevel}) challenges you to a duel!`;
  }

  /** We answered `challengeId`; returns it (to name it in a message). */
  answered(challengeId: number): IncomingChallenge | null {
    const c = this.incoming.find((x) => x.challengeId === challengeId) ?? null;
    this.incoming = this.incoming.filter((x) => x.challengeId !== challengeId);
    return c;
  }

  /** `DuelChallengeEnded`. Returns a line for the chat, if any. */
  ended(challengeId: number, reason: ChallengeEnd): string | null {
    const inc = this.incoming.find((c) => c.challengeId === challengeId);
    const out = this.outgoing && this.outgoing.challengeId === challengeId ? this.outgoing : null;
    this.incoming = this.incoming.filter((c) => c.challengeId !== challengeId);
    if (out) this.outgoing = null;
    if (reason === "started") return null;
    if (out) return endedText(reason, out.targetName, true);
    if (inc) return endedText(reason, inc.fromName, false);
    return null;
  }

  /** `DuelError`. Returns the words. */
  error(challengeId: number | null, code: DuelErrorCode): string {
    let name: string | null = null;
    if (challengeId !== null) {
      const inc = this.incoming.find((c) => c.challengeId === challengeId);
      name = inc?.fromName ?? null;
      this.incoming = this.incoming.filter((c) => c.challengeId !== challengeId);
      if (this.outgoing?.challengeId === challengeId) this.outgoing = null;
    } else if (this.outgoing && this.outgoing.challengeId === null) {
      // Our challenge was refused before it was made.
      name = this.outgoing.targetName;
      this.outgoing = null;
    }
    return duelErrorText(code, name);
  }

  /** Drops lapsed challenges (the server sends `Expired` too; this keeps the toasts honest meanwhile). */
  expire(now: number): boolean {
    const before = this.incoming.length + (this.outgoing ? 1 : 0);
    this.incoming = this.incoming.filter((c) => c.until > now);
    // Our own waits a little longer for the server's word.
    if (this.outgoing && this.outgoing.until + 2000 < now) this.outgoing = null;
    return this.incoming.length + (this.outgoing ? 1 : 0) !== before;
  }

  get empty(): boolean {
    return this.incoming.length === 0 && this.outgoing === null;
  }

  /** A new connection or a duel starting: nothing is pending any more. */
  clear(): void {
    this.incoming = [];
    this.outgoing = null;
  }
}

function endedText(reason: ChallengeEnd, other: string, ours: boolean): string | null {
  switch (reason) {
    case "started":
      return null;
    case "declined":
      return ours ? `${other} declined your duel.` : `You declined ${other}'s duel.`;
    case "cancelled":
      return ours ? "You withdrew your challenge." : `${other} withdrew the challenge.`;
    case "expired":
      return ours ? `${other} didn't answer your challenge.` : `${other}'s challenge lapsed.`;
    case "walked_away":
      return "The challenge ended: duels are fought from the Dueling Club.";
    case "disconnected":
      return `The challenge ended: ${ours ? other : other} left.`;
  }
}

export function duelErrorText(code: DuelErrorCode, name: string | null): string {
  switch (code) {
    case "unknown_target":
      return name ? `${name} isn't here to duel.` : "Nobody by that name is here to duel.";
    case "self_duel":
      return "You can't duel yourself.";
    case "not_in_area":
      return "Duels are fought at the Dueling Club: step up by the stage in the Great Hall.";
    case "target_not_in_area":
      return `${name ?? "They"} must be at the Dueling Club too.`;
    case "busy":
      return "One of you is already in a duel or has a challenge waiting.";
    case "unknown_challenge":
    case "expired":
      return "That challenge has lapsed.";
    case "unknown_duel":
      return "That duel is over.";
    case "full":
      return "There's no room for that right now. Try again in a moment.";
    case "no_spells":
      return "Learn a dueling spell first: the Dueling Club teaches them.";
    case "rate_limited":
      return "Not so fast: wait a moment.";
    case "failed":
      return "Something went wrong. Please try again.";
  }
}

/** `/duel Name` → the name (quotes allowed), `/duel` alone → "", anything else → null. */
export function duelCommand(line: string): { verb: "duel" | "duels" | "loadout" | "forfeit"; arg: string } | null {
  const m = /^\/(duel|duels|loadout|forfeit)\b\s*(.*)$/i.exec(line.trim());
  if (!m) return null;
  return { verb: m[1]!.toLowerCase() as "duel" | "duels" | "loadout" | "forfeit", arg: m[2]!.trim().replace(/^"(.*)"$/, "$1").trim() };
}
