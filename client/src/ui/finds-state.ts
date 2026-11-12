// Exploration finds without the DOM: what a `FindResult` says, and which
// spots are on cooldown for us (from `GET /api/finds` and from results), so
// the world stops making them shimmer until they are ready again. Pure, unit
// tested in finds.test.ts.

import { itemName } from "./crafting-content";
import type { FindOutcome } from "../wasm/sim_wasm";

export interface FindMessage {
  /** "found" toasts celebrate; the others are quiet. */
  tone: "found" | "nothing" | "cooldown" | "failed";
  title: string;
  lines: string[];
}

/** "5 h 12 min", "3 min", "40 s". */
export function formatWait(secs: number): string {
  const s = Math.max(0, Math.round(secs));
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  const hours = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

export function crownsText(n: number): string {
  return `${n} Crown${n === 1 ? "" : "s"}`;
}

export function findMessage(outcome: FindOutcome): FindMessage {
  switch (outcome.kind) {
    case "found": {
      const lines = outcome.items.map((i) => `${i.quantity > 1 ? `${i.quantity} × ` : ""}${itemName(i.itemDef)}`);
      if (outcome.crowns > 0) lines.push(crownsText(outcome.crowns));
      return { tone: "found", title: "You found something!", lines: lines.length ? lines : ["A curious trinket that crumbles to dust."] };
    }
    case "nothing":
      return { tone: "nothing", title: "Nothing here this time", lines: ["Only dust and cobwebs. Try again tomorrow."] };
    case "cooldown":
      return { tone: "cooldown", title: "You have searched here today", lines: [`Come back in ${formatWait(outcome.readyInSecs)}.`] };
    case "failed":
      return { tone: "failed", title: "The search was interrupted", lines: ["Please try again in a moment."] };
  }
}

/** Spots on cooldown for us, with when each is ready (local ms). */
export class FindCooldowns {
  private readonly ready = new Map<string, number>();

  /** A spot searched (or reported on cooldown) is ready again `secs` from `nowMs`. */
  set(spot: string, secs: number, nowMs: number): void {
    this.ready.set(spot, nowMs + Math.max(0, secs) * 1000);
  }

  isCooling(spot: string, nowMs: number): boolean {
    const t = this.ready.get(spot);
    return t !== undefined && t > nowMs;
  }

  /** Drops and returns spots that are ready again. */
  expire(nowMs: number): string[] {
    const out: string[] = [];
    for (const [spot, t] of this.ready) {
      if (t <= nowMs) {
        this.ready.delete(spot);
        out.push(spot);
      }
    }
    return out;
  }

  spots(): IterableIterator<string> {
    return this.ready.keys();
  }
}

/** Seconds until an RFC 3339 time, from wall-clock `nowMs` (0 when passed or unparsable). */
export function secondsUntil(iso: string, nowMs: number): number {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? Math.max(0, (t - nowMs) / 1000) : 0;
}
