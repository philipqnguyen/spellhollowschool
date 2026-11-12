// The dynamic-resolution guard (docs/art/character-look.md section 5): Medium and
// High render at the screen's own resolution with 4x MSAA, which the M1 Pro
// runs in about 5 ms, but a weaker GPU meeting a Retina screen may not. When
// more than 10 % of the frames in the last 2 s took longer than 20 ms, the
// render scale steps down one rung of the ladder (2.0 -> 1.5 -> 1.25, MSAA
// kept); after 10 s with 95 % of the frames under 12 ms it steps back up.
//
// - At most one step per 2 s, never per frame; each step resizes the render
//   targets once (the caller sets the engine's scaling level).
// - Frames are counted in quarter-second buckets in a preallocated ring (10 s),
//   so nothing allocates; only frames since the last step count.
// - Hitches (one frame over `ignoreMs`: streaming a cell, a GC pause) are not
//   counted: they say nothing about the resolution; nor is anything in the
//   first `warmupS` after a reset (loading the castle, compiling shaders).
// - "Slow" is 20 ms, not the contract's 17.5: with vsync on a 60 Hz screen a
//   full-speed frame interval is 16.7 ms with about a millisecond of jitter,
//   so 17.5 counted healthy frames as slow (measured: the guard stepped twice
//   while the castle loaded); a missed 60 Hz frame is 33 ms either way.
// - Hysteresis: a step up that is followed by a step down within `bounceS`
//   doubles the time the next step up waits (up to `maxUpHold` x), so a GPU
//   that sits between two rungs does not flip every 12 s.

/** The guard's numbers (contract 4.4). */
export const GUARD = {
  /** Rungs below the top scale (the profile's resolution); only those below the top are used. */
  ladder: [2, 1.5, 1.25] as readonly number[],
  slowMs: 20,
  /** Step down when more than this share of the frames in `downWindowS` were slow. */
  slowShare: 0.1,
  downWindowS: 2,
  fastMs: 12,
  /** Step up when at least this share of the frames in `upWindowS` were fast. */
  fastShare: 0.95,
  upWindowS: 10,
  /** Frames longer than this are hitches, not counted (ms). */
  ignoreMs: 100,
  /** Nothing counts in the first seconds after a reset (loading, shader compiles). */
  warmupS: 5,
  /** A window needs at least this many frames to decide. */
  minFrames: 20,
  /** A step down this soon after a step up doubles the next wait (s). */
  bounceS: 20,
  maxUpHold: 8,
};

const BUCKET_MS = 250;
const SLOTS = Math.ceil((GUARD.upWindowS * 1000) / BUCKET_MS) * 2;

/** The scales the guard may use under `top`: the top itself, then the ladder's lower rungs. */
export function guardLadder(top: number): number[] {
  const out = [top];
  for (const r of GUARD.ladder) if (r < top - 1e-3) out.push(r);
  return out;
}

export class ResolutionGuard {
  private ladder: number[] = [1];
  private rung = 0;
  private readonly bucketId = new Float64Array(SLOTS).fill(-1);
  private readonly total = new Int32Array(SLOTS);
  private readonly slow = new Int32Array(SLOTS);
  private readonly fast = new Int32Array(SLOTS);
  /** The first bucket that counts (after the last step or reset). */
  private since = -1;
  private lastBucket = -1;
  private lastUpBucket = -Infinity;
  private upHold = 1;
  /** Steps taken (for the F3 overlay and tests). */
  steps = 0;

  constructor(top = 1) {
    this.reset(top);
  }

  /** The scale to render at now. */
  get scale(): number {
    return this.ladder[this.rung]!;
  }

  /** The top scale (the quality's own resolution). */
  get top(): number {
    return this.ladder[0]!;
  }

  /** Starts over at `top` (a quality change, a new display density). */
  reset(top: number): void {
    this.ladder = guardLadder(top);
    this.rung = 0;
    this.upHold = 1;
    this.lastUpBucket = -Infinity;
    this.since = -1;
    this.lastBucket = -1;
    this.bucketId.fill(-1);
  }

  /**
   * One frame that took `dtMs`, ending at `nowMs`. Returns the new scale when
   * the guard steps (the caller resizes once), else 0.
   */
  frame(nowMs: number, dtMs: number): number {
    const id = Math.floor(nowMs / BUCKET_MS);
    if (this.since < 0) this.since = id + Math.round((GUARD.warmupS * 1000) / BUCKET_MS);
    if (dtMs > 0 && dtMs <= GUARD.ignoreMs) {
      const s = id % SLOTS;
      if (this.bucketId[s] !== id) {
        this.bucketId[s] = id;
        this.total[s] = 0;
        this.slow[s] = 0;
        this.fast[s] = 0;
      }
      this.total[s]!++;
      if (dtMs > GUARD.slowMs) this.slow[s]!++;
      if (dtMs < GUARD.fastMs) this.fast[s]!++;
    }
    // Decide once per bucket, never per frame.
    if (id === this.lastBucket) return 0;
    this.lastBucket = id;
    return this.decide(id);
  }

  private decide(id: number): number {
    const age = id - this.since;
    const downN = Math.round((GUARD.downWindowS * 1000) / BUCKET_MS);
    const upN = Math.round((GUARD.upWindowS * 1000) / BUCKET_MS);
    if (age >= downN && this.rung < this.ladder.length - 1) {
      this.sum(id, downN);
      if (this.sTotal >= GUARD.minFrames && this.sSlow > this.sTotal * GUARD.slowShare) {
        if (id - this.lastUpBucket < (GUARD.bounceS * 1000) / BUCKET_MS) this.upHold = Math.min(GUARD.maxUpHold, this.upHold * 2);
        return this.step(id, this.rung + 1);
      }
    }
    if (this.rung > 0 && age >= upN * this.upHold) {
      this.sum(id, upN);
      if (this.sTotal >= GUARD.minFrames && this.sFast >= this.sTotal * GUARD.fastShare) {
        this.lastUpBucket = id;
        return this.step(id, this.rung - 1);
      }
    }
    return 0;
  }

  private sTotal = 0;
  private sSlow = 0;
  private sFast = 0;

  /** Sums the `n` buckets before `id` (complete ones), only those since the last step. */
  private sum(id: number, n: number): void {
    this.sTotal = 0;
    this.sSlow = 0;
    this.sFast = 0;
    for (let k = id - n; k < id; k++) {
      if (k < this.since) continue;
      const s = ((k % SLOTS) + SLOTS) % SLOTS;
      if (this.bucketId[s] !== k) continue;
      this.sTotal += this.total[s]!;
      this.sSlow += this.slow[s]!;
      this.sFast += this.fast[s]!;
    }
  }

  private step(id: number, rung: number): number {
    this.rung = rung;
    this.since = id;
    this.steps++;
    return this.scale;
  }
}
