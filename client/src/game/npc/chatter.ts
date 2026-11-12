// What NPCs say and when, by the anti-annoyance rules (binding: portraits
// never nag;
// docs/architecture.md "NPCs", the rules as numbers):
//
// - Passing a talking portrait gives at most a nod or ONE short subtitle
//   line, at most once per `pass_cooldown_s` per portrait for this player,
//   and at most one pass-by line on screen per `pass_gap_s` (a row of
//   portraits gives one line); `pass_line_pct` of noticed passes get a line,
//   the rest a nod.
// - A real conversation only when the player presses interact; portraits
//   never start one. Each interact shows the next line, at most
//   `talk_lines_max` in one conversation; a daily-hint portrait opens with
//   today's hint, the others with a hint toward an unfound secret
//   `hint_pct` of the time.
// - Portrait chatter: On / Quiet (interact only: no nods, no pass-by
//   lines) / Off (interact on a portrait does nothing either). It covers
//   portraits only, and the subjects stay alive whatever it says.
//
// Pure: no DOM, no Babylon. Unit tested in chatter.test.ts.

import type { LineWhen, NpcRulesView } from "../../wasm/sim_wasm";

export type ChatterMode = "on" | "quiet" | "off";
export const CHATTER_MODES: readonly ChatterMode[] = ["on", "quiet", "off"];

export function parseChatter(v: unknown): ChatterMode {
  return v === "quiet" || v === "off" ? v : "on";
}

/** A pass noticed: the portrait nods, or says a short line. */
export interface PassOutcome {
  npc: string;
  kind: "line" | "nod";
}

/** Where the cooldowns live between page loads (per character; try/catch inside). */
export interface PassStore {
  load(): Record<string, number>;
  save(noticed: Record<string, number>): void;
}

type PassRules = Pick<NpcRulesView, "pass_cooldown_s" | "pass_gap_s" | "pass_line_pct">;

/**
 * Pass-by noticing. `step` is given the portraits within `pass_radius_m`
 * (on our floor) each tick; a portrait notices us as we come into its
 * radius, never again within its cooldown, and never while chatter is not
 * On. Times are wall-clock ms (`Date.now()`), so the cooldowns survive a
 * reload through the store.
 */
export class PassWatch {
  private near = new Set<string>();
  private nextNear = new Set<string>();
  private readonly noticed: Map<string, number>;
  private lastLineAt = -Infinity;

  constructor(
    private readonly rules: PassRules,
    private readonly store: PassStore | null = null,
  ) {
    this.noticed = new Map(Object.entries(store?.load() ?? {}).filter(([, v]) => typeof v === "number" && Number.isFinite(v)));
  }

  /** `inRange`: portrait ids within the pass radius now; `roll`: a uniform 0..1 (Math.random). At most one outcome a step. */
  step(nowMs: number, inRange: Iterable<string>, mode: ChatterMode, roll: () => number): PassOutcome | null {
    const next = this.nextNear;
    next.clear();
    let out: PassOutcome | null = null;
    for (const id of inRange) {
      next.add(id);
      if (out || this.near.has(id) || mode !== "on") continue;
      const last = this.noticed.get(id);
      if (last !== undefined && nowMs - last < this.rules.pass_cooldown_s * 1000) continue;
      this.noticed.set(id, nowMs);
      const line = nowMs - this.lastLineAt >= this.rules.pass_gap_s * 1000 && roll() * 100 < this.rules.pass_line_pct;
      if (line) this.lastLineAt = nowMs;
      out = { npc: id, kind: line ? "line" : "nod" };
    }
    this.nextNear = this.near;
    this.near = next;
    if (out) this.persist(nowMs);
    return out;
  }

  /** When `npc` last noticed us (ms), for tests and the probe. */
  noticedAt(npc: string): number | undefined {
    return this.noticed.get(npc);
  }

  private persist(nowMs: number): void {
    if (!this.store) return;
    // Only cooldowns still running are kept.
    const keep: Record<string, number> = {};
    for (const [id, at] of this.noticed) if (nowMs - at < this.rules.pass_cooldown_s * 1000) keep[id] = at;
    this.store.save(keep);
  }
}

/** localStorage under `key` (one per character): a per-viewer convenience, so every access may fail. */
export function localPassStore(key: string): PassStore {
  return {
    load: () => {
      try {
        const raw: unknown = JSON.parse(localStorage.getItem(key) ?? "{}");
        return raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, number>) : {};
      } catch {
        return {};
      }
    },
    save: (noticed) => {
      try {
        localStorage.setItem(key, JSON.stringify(noticed));
      } catch {
        // Storage unavailable: the cooldowns last for this page only.
      }
    },
  };
}

/** One conversation with one NPC: the lines said (excluded from the next picks) and how many. */
export interface Conversation {
  npc: string;
  said: number[];
  /** `performance.now()` of the last line (a conversation left alone ends: `CONVERSATION_IDLE_MS`). */
  at: number;
}

/** A conversation left this long (ms) starts over at the next interact. */
export const CONVERSATION_IDLE_MS = 45_000;

export interface NextLineOptions {
  /** Today's hint of a daily-hint portrait (`NpcBook.dailyHint`), or undefined. */
  dailyHint: number | undefined;
  /** Whether this NPC may open with a hint toward an unfound secret (portraits). */
  hints: boolean;
  /** A uniform 0..1 for the `hint_pct` roll. */
  roll: number;
  /** Picks among `when` lines leaving out `exclude` (`NpcBook.pick` with the context and a seed bound). */
  pick: (when: LineWhen, exclude: readonly number[]) => number | undefined;
}

type TalkRules = Pick<NpcRulesView, "talk_lines_max" | "hint_pct">;

/**
 * The next line of `conv` (null: the conversation is over: it said
 * `talk_lines_max` lines, or has nothing left to say). The first line is
 * today's hint when there is one, else a hint `hint_pct` of the time (when a
 * hint line holds: its secret not found), else a talk line.
 */
export function nextLine(conv: Conversation, rules: TalkRules, o: NextLineOptions): { index: number; when: LineWhen } | null {
  if (conv.said.length >= rules.talk_lines_max) return null;
  if (conv.said.length === 0) {
    if (o.dailyHint !== undefined) return { index: o.dailyHint, when: "hint" };
    if (o.hints && o.roll * 100 < rules.hint_pct) {
      const hint = o.pick("hint", conv.said);
      if (hint !== undefined) return { index: hint, when: "hint" };
    }
  }
  const talk = o.pick("talk", conv.said);
  return talk === undefined ? null : { index: talk, when: "talk" };
}

/** Starts or continues the conversation with `npc` (a different NPC, or one left idle, starts over). */
export function continueConversation(conv: Conversation | null, npc: string, now: number): Conversation {
  if (conv && conv.npc === npc && now - conv.at < CONVERSATION_IDLE_MS) return conv;
  return { npc, said: [], at: now };
}
