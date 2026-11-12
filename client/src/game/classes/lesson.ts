// A class lesson as the client follows it (docs/architecture.md,
// "Classes and progression"): the session it joined (`ClassJoined`), the phase
// (`LessonPhaseChanged`), the round under way (`LessonRound`) and each
// beat's fate: answered and sent (with the local preview score), scored by
// the server (`BeatScored`), missed, or skipped. Beats are of two kinds
//: a wand gesture to trace on the pad, or a pick (a prompt and
// 2-6 options) on the pick tray. It answers the pad's and the tray's
// questions: which beat to show now, which beat an answer at a given
// moment belongs to (the nearest one not yet tried, within the round's
// timing window, as the server's arrival check requires), and how the
// lesson has gone so far.
//
// Times are the server clock (Unix ms). Pure, unit tested in lesson.test.ts.

import classFiles from "./class-files";
import type { ClassInfo, GestureResult, LessonBeat, LessonPhase } from "../../wasm/sim_wasm";

export type BeatStatus =
  /** Not yet reached, or its window is open. */
  | "upcoming"
  /** Answered (traced or picked) and sent; the server's score has not come back. */
  | "sent"
  /** The server scored it (`result`). */
  | "scored"
  /** Its window passed with no answer. */
  | "missed";

/** Protocol v8 adds `ring` (the Flying class: fly through a ring on the beat; the client's ring course). */
export type BeatKind = "trace" | "pick" | "ring";

/** The approach ring starts closing this long before a beat (ms); the pad shows a beat's gesture from then. */
export const APPROACH_MS = 1600;
/**
 * A trace beat's gesture stays on the pad this long after the beat (ms),
 * whatever the player does, so a stroke started on the beat has time to
 * finish; a stroke still being drawn then is cut off and sent as drawn.
 * Trace beats are at least this plus `APPROACH_MS` apart (sim-core
 * `classes::TRACE_BEAT_GAP_MIN_MS`), so the next beat's approach never
 * has to start sooner.
 */
export const TRACE_HOLD_MS = 3000;

export interface BeatState {
  beat: number;
  kind: BeatKind;
  /** The gesture to trace (trace beats; 0 for picks). */
  gesture: number;
  /** A pick's prompt and options (indices into the class's vocabularies; empty for traces). */
  prompt: number;
  options: readonly number[];
  /** A ring beat's ring (an index into the class layout's `rings`; -1 for other beats). */
  ring: number;
  /** The option picked (a position in `options`), once sent. */
  choice: number | null;
  /** When the trace should start (server Unix ms). */
  atMs: number;
  status: BeatStatus;
  /** The local preview of the answer sent for it (a pick's: as if right). */
  preview: GestureResult | null;
  /** The server's verdict. */
  result: GestureResult | null;
}

export interface RoundState {
  round: number;
  windowMs: number;
  beats: BeatState[];
}

/** How the lesson has gone so far, over every beat of every round. */
export interface LessonSummary {
  beats: number;
  /** Beats answered (traced or picked) and sent. */
  answered: number;
  scored: number;
  /** Mean server score over the beats scored (0 when none). */
  average: number;
  perfect: number;
}

/** The class data the client reads (data/classes/<id>.json): names and the script's shape. */
export interface ClassScript {
  id: string;
  professorName: string;
  /** Practice rounds in order, with their beat counts. */
  rounds: { round: number; beats: number }[];
}

/** The class file for `id`, if this build ships it. */
export function classScript(id: string): ClassScript | undefined {
  const def = classFiles[id];
  if (!def) return undefined;
  const rounds = def.lesson.filter((a) => a.kind === "practice").map((a) => ({ round: a.round ?? 0, beats: a.beats?.length ?? 0 }));
  return { id: def.id, professorName: def.professor.name, rounds };
}

export class LessonState {
  info: ClassInfo | null = null;
  phase: LessonPhase = { kind: "gathering" };
  /** When the phase ends (server Unix ms). */
  phaseUntilMs = 0;
  round: RoundState | null = null;
  /** Every round seen this lesson, the current one last. */
  private readonly rounds: RoundState[] = [];
  readonly script: ClassScript | null;

  constructor(classId: string | null) {
    this.script = classId ? (classScript(classId) ?? null) : null;
  }

  /** `ClassJoined`. */
  join(info: ClassInfo): void {
    this.info = info;
    this.phase = info.phase;
    this.phaseUntilMs = info.phaseUntilUnixMs;
  }

  /** `LessonPhaseChanged`. */
  setPhase(phase: LessonPhase, untilMs: number): void {
    this.phase = phase;
    this.phaseUntilMs = untilMs;
  }

  /** `LessonRound`: a round starts (a repeat of a round already held replaces it). */
  startRound(round: number, windowMs: number, beats: readonly LessonBeat[]): RoundState {
    const r: RoundState = {
      round,
      windowMs,
      beats: [...beats].sort((a, b) => a.atUnixMs - b.atUnixMs).map(beatState),
    };
    const i = this.rounds.findIndex((x) => x.round === round);
    if (i >= 0) this.rounds[i] = r;
    else this.rounds.push(r);
    this.round = r;
    return r;
  }

  get practising(): boolean {
    return this.phase.kind === "practice" && this.round !== null && this.round.round === this.phase.round;
  }

  /** Practice rounds the script holds (0 when the class file is unknown). */
  get roundsTotal(): number {
    return this.script?.rounds.length ?? 0;
  }

  /**
   * The beat to show at `nowMs`: the first one whose window has not closed
   * and that has not been traced, or null when the round is through.
   */
  current(nowMs: number): BeatState | null {
    const r = this.round;
    if (!r || !this.practising) return null;
    for (const b of r.beats) if (b.status === "upcoming" && nowMs <= b.atMs + r.windowMs) return b;
    return null;
  }

  /**
   * The beat whose gesture the trace pad shows at `nowMs`. A beat comes up
   * as its approach ring starts closing (`APPROACH_MS` before it), so the
   * ring closes at once, and only once the beat before is done with: traced
   * and sent, or `TRACE_HOLD_MS` after it (the map never changes under a
   * stroke in that time; one still being drawn then is cut off, `cutDue`).
   * A beat traced early stays on the pad, done, until the next comes up.
   * Before the round's first approach, the first beat (its countdown);
   * after the last beat is done with, null (the round is through).
   */
  shown(nowMs: number): BeatState | null {
    const r = this.round;
    if (!r || !this.practising || r.beats.length === 0) return null;
    const over = (b: BeatState) => nowMs > b.atMs + (b.status === "sent" || b.status === "scored" ? r.windowMs : Math.max(r.windowMs, TRACE_HOLD_MS));
    const doneWith = (b: BeatState) => b.status === "sent" || b.status === "scored" || nowMs > b.atMs + TRACE_HOLD_MS;
    let out = r.beats[0]!;
    for (let i = 1; i < r.beats.length; i++) {
      const b = r.beats[i]!;
      if (nowMs < b.atMs - APPROACH_MS || !doneWith(out)) break;
      out = b;
    }
    return out === r.beats[r.beats.length - 1] && over(out) ? null : out;
  }

  /**
   * Whether a stroke still being drawn for `tracing` must be cut off at
   * `nowMs`: its beat's hold is over (or the pad has moved on), so it is
   * ended where it is and sent as drawn.
   */
  cutDue(nowMs: number, tracing: BeatState): boolean {
    return tracing.status === "upcoming" && (nowMs > tracing.atMs + TRACE_HOLD_MS || this.shown(nowMs) !== tracing);
  }

  /**
   * The beat a trace starting at `startMs` belongs to: of the beats not yet
   * tried, the one nearest in time within the round's window (the server
   * refuses a start further off, `BadTiming`). Null outside every window.
   */
  beatFor(startMs: number): BeatState | null {
    const r = this.round;
    if (!r || !this.practising) return null;
    let best: BeatState | null = null;
    for (const b of r.beats) {
      if (b.status !== "upcoming") continue;
      const off = Math.abs(startMs - b.atMs);
      if (off <= r.windowMs && (!best || off < Math.abs(startMs - best.atMs))) best = b;
    }
    return best;
  }

  /**
   * Marks the beats whose window closed by `nowMs` without a trace as missed;
   * returns them. `tracing` is the beat of a stroke still being drawn: only
   * its start must fall in the window (a careful swish takes longer than the
   * window lasts), so it stays open until the stroke ends.
   */
  expire(nowMs: number, tracing: BeatState | null = null): BeatState[] {
    const r = this.round;
    if (!r) return [];
    const out: BeatState[] = [];
    for (const b of r.beats) {
      if (b.status === "upcoming" && b !== tracing && nowMs > b.atMs + r.windowMs) {
        b.status = "missed";
        out.push(b);
      }
    }
    return out;
  }

  /** An answer for `b` went to the server, with its local preview (and, for a pick, the option chosen). */
  sent(b: BeatState, preview: GestureResult, choice: number | null = null): void {
    b.status = "sent";
    b.preview = preview;
    b.choice = choice;
  }

  /** Whether the round under way has pick beats (the pick tray shows, not the pad). */
  get picking(): boolean {
    return !!this.round?.beats.some((b) => b.kind === "pick");
  }

  /** Whether the round under way has ring beats (the ring course shows, not the pad or the tray). */
  get flying(): boolean {
    return !!this.round?.beats.some((b) => b.kind === "ring");
  }

  /**
   * The ring beats a flight from `fromMs` to `toMs` (server clock)
   * may count for: beats not yet tried whose window overlaps that span, as
   * the server tests every open ring beat against each checked move.
   */
  openRings(fromMs: number, toMs: number): BeatState[] {
    const r = this.round;
    if (!r || !this.practising) return [];
    return r.beats.filter((b) => b.kind === "ring" && b.status === "upcoming" && toMs >= b.atMs - r.windowMs && fromMs <= b.atMs + r.windowMs);
  }

  /** `BeatScored`. Returns the beat, or null for one this client does not know. */
  scored(round: number, beat: number, result: GestureResult): BeatState | null {
    const r = this.rounds.find((x) => x.round === round);
    const b = r?.beats.find((x) => x.beat === beat);
    if (!b) return null;
    b.status = "scored";
    b.result = result;
    return b;
  }

  summary(): LessonSummary {
    let beats = 0;
    let answered = 0;
    let scored = 0;
    let total = 0;
    let perfect = 0;
    for (const r of this.rounds) {
      for (const b of r.beats) {
        beats++;
        if (b.status === "sent" || b.status === "scored") answered++;
        if (b.result && !b.result.rejected) {
          scored++;
          total += b.result.score;
          if (b.result.grade === "perfect") perfect++;
        }
      }
    }
    return { beats, answered, scored, average: scored ? Math.round(total / scored) : 0, perfect };
  }
}

function beatState(b: LessonBeat): BeatState {
  const p = b.prompt;
  return {
    beat: b.beat,
    kind: p.kind,
    gesture: p.kind === "trace" ? p.gesture : 0,
    prompt: p.kind === "pick" ? p.prompt : 0,
    options: p.kind === "pick" ? [...p.options] : [],
    ring: p.kind === "ring" ? p.ring : -1,
    choice: null,
    atMs: b.atUnixMs,
    status: "upcoming",
    preview: null,
    result: null,
  };
}
