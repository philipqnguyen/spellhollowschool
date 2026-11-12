// Recording a wand gesture as the server scores it (docs/architecture.md,
// "Classes and progression"; sim-core `gesture`): samples of `{ dtMs, x, y }` on a square
// pad from -100 to 100 (y up), each `dtMs` the milliseconds since the
// sample before (a byte, so at most 255), at most `TRACE_POINTS_MAX` (128)
// of them. The recorder turns pointer or keyboard movement into exactly
// that, without ever breaking those limits:
//
// - samples are taken at most every `interval` ms, chosen from the
//   gesture's longest allowed duration so even the slowest trace fits in
//   the sample budget;
// - a pause longer than 255 ms (the pen held still) becomes repeated
//   samples at the same spot, 255 ms apart, which is what happened;
// - the duration is the sum of the gaps, kept in whole milliseconds, so the
//   server reads the same duration the client measured.
//
// The pad is the player's own drawing frame: x to the right, y up, as the
// professor demonstrates it to the class. Pure, unit tested in trace.test.ts
// (with the real scorer through wasm).

/** The pad's half-width: samples lie in -PAD..=PAD (sim-core `gesture::PAD`). */
export const PAD = 100;
/** `protocol::TRACE_POINTS_MAX`. */
export const TRACE_POINTS_MAX = 128;
/** The longest gap one sample can carry (`TracePoint.dt_ms` is a byte). */
const DT_MAX = 255;
/** Pointer events come about once a frame; sampling faster adds nothing. */
const MIN_INTERVAL_MS = 16;

export interface TracePoint {
  dtMs: number;
  x: number;
  y: number;
}

/** A pad position for a pointer at (`clientX`, `clientY`) over the pad's box: rounded, clamped, y up. */
export function padPoint(clientX: number, clientY: number, rect: { left: number; top: number; width: number; height: number }): [number, number] {
  const u = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5;
  const v = rect.height > 0 ? (clientY - rect.top) / rect.height : 0.5;
  return [clampPad(Math.round(u * 2 * PAD - PAD)), clampPad(Math.round(PAD - v * 2 * PAD))];
}

export function clampPad(v: number): number {
  return Math.max(-PAD, Math.min(PAD, Number.isFinite(v) ? Math.round(v) : 0));
}

/** The sampling interval for a gesture that may take up to `maxMs`: the whole trace fits in the budget. */
export function sampleInterval(maxMs: number, maxPoints = TRACE_POINTS_MAX): number {
  // Every sample but the last is at least one interval after the one before
  // (gap samples are 255 ms apart, longer still), and one is spare for the end.
  return Math.max(MIN_INTERVAL_MS, Math.ceil(maxMs / Math.max(1, maxPoints - 4)));
}

export class TraceRecorder {
  readonly points: TracePoint[] = [];
  /** Local time (ms) of the first sample. */
  readonly startedAt: number;
  /** Local time (ms) of the last sample: the start plus the sum of the gaps. */
  private lastT: number;
  private lastX: number;
  private lastY: number;
  private pending: { t: number; x: number; y: number } | null = null;
  private done = false;
  readonly interval: number;

  /**
   * Starts a trace at local time `t` (ms) at pad position (`x`, `y`) for a
   * gesture that may take up to `maxMs`.
   */
  constructor(
    t: number,
    x: number,
    y: number,
    readonly maxMs: number,
    private readonly maxPoints = TRACE_POINTS_MAX,
  ) {
    this.interval = sampleInterval(maxMs, maxPoints);
    this.startedAt = t;
    this.lastT = t;
    this.lastX = clampPad(x);
    this.lastY = clampPad(y);
    this.points.push({ dtMs: 0, x: this.lastX, y: this.lastY });
  }

  /** The trace's duration so far (ms, as the server will sum it). */
  get durationMs(): number {
    return this.lastT - this.startedAt;
  }

  get ended(): boolean {
    return this.done;
  }

  /**
   * The pen moved to (`x`, `y`) at local time `t`. Returns false once the
   * trace cannot take more (its longest duration or the sample budget):
   * end it then.
   */
  move(t: number, x: number, y: number): boolean {
    if (this.done) return false;
    // Past the gesture's longest duration nothing more is taken (`end` closes it there).
    if (t - this.startedAt > this.maxMs) return false;
    this.settle(t);
    this.pending = { t, x: clampPad(x), y: clampPad(y) };
    if (t - this.lastT >= this.interval) this.commit(this.pending);
    return !this.full && t - this.startedAt < this.maxMs;
  }

  /** Ends the trace at local time `t`, at (`x`, `y`) if given (else where the pen last was). */
  end(t: number, x?: number, y?: number): void {
    if (this.done) return;
    // Never past the gesture's longest duration (that trace would score nothing).
    const at = Math.min(t, this.startedAt + this.maxMs);
    const p = { t: at, x: x === undefined ? (this.pending?.x ?? this.lastX) : clampPad(x), y: y === undefined ? (this.pending?.y ?? this.lastY) : clampPad(y) };
    this.settle(at);
    if (Math.round(p.t - this.lastT) >= 1) this.commit(p, true);
    else if (this.points.length > 1) {
      // Lifted within the same millisecond as the last sample: that sample ends here.
      const last = this.points[this.points.length - 1]!;
      last.x = p.x;
      last.y = p.y;
    }
    this.done = true;
  }

  /**
   * A new event at `t` long after the last one: the pen rested where that
   * one left it, so that spot is a sample of its own (otherwise the stroke
   * would seem to glide there from the sample before).
   */
  private settle(t: number): void {
    const prev = this.pending;
    if (prev && t - prev.t > this.interval && Math.round(prev.t - this.lastT) >= 1) this.commit(prev);
  }

  /** No room for more samples (the last slot is kept for the end). */
  private get full(): boolean {
    return this.points.length >= this.maxPoints - 1;
  }

  private commit(p: { t: number; x: number; y: number }, final = false): void {
    let dt = Math.round(p.t - this.lastT);
    if (dt < 1) return;
    // The pen held still longer than one gap can say: repeat the spot.
    while (dt > DT_MAX && this.points.length < this.maxPoints - 1) {
      this.points.push({ dtMs: DT_MAX, x: this.lastX, y: this.lastY });
      this.lastT += DT_MAX;
      dt -= DT_MAX;
    }
    if (dt > DT_MAX) dt = DT_MAX; // out of room: the trace ends a little early
    if (!final && this.full) return;
    this.points.push({ dtMs: dt, x: p.x, y: p.y });
    this.lastT += dt;
    this.lastX = p.x;
    this.lastY = p.y;
    this.pending = null;
  }

  /** The samples as `encodeTraceGesture` and `GestureScorer.score` take them: flat `[dt, x, y, ...]`. */
  flat(): Int32Array {
    const out = new Int32Array(this.points.length * 3);
    this.points.forEach((p, i) => out.set([p.dtMs, p.x, p.y], i * 3));
    return out;
  }
}

/** Arrow keys held on the pad, for drawing with the keyboard. */
export interface PenKeys {
  left: boolean;
  right: boolean;
  up: boolean;
  down: boolean;
}

/** How fast the keyboard pen moves (pad units per ms): the pad's width in under a second. */
export const PEN_SPEED = 0.24;

/** Moves a keyboard pen at (`x`, `y`) for `dtMs` with `keys` held; diagonals are as fast as straight lines. */
export function penStep(x: number, y: number, keys: PenKeys, dtMs: number): [number, number] {
  const dx = (keys.right ? 1 : 0) - (keys.left ? 1 : 0);
  const dy = (keys.up ? 1 : 0) - (keys.down ? 1 : 0);
  if (!dx && !dy) return [x, y];
  const k = (PEN_SPEED * dtMs) / Math.hypot(dx, dy);
  return [Math.max(-PAD, Math.min(PAD, x + dx * k)), Math.max(-PAD, Math.min(PAD, y + dy * k))];
}
