// The duel clock as this client estimates it. The server stamps every duel
// message with its duel time (`DuelStart.state.atMs`, `DuelEvents.atMs`:
// milliseconds since the arena opened, advanced 50 ms per world tick); the
// client wants "what time is it in the duel *now*" to fill cast bars, sweep
// cooldowns and fly projectiles where the server has them.
//
// Each message gives a sample `atMs - receivedAt`, which is the true offset
// minus that message's one-way delay. The largest sample is the least
// delayed one, so the estimate keeps the largest seen (jitter only ever
// makes samples smaller), decaying slowly so a server that fell behind
// (a slow tick) pulls it back down. Adding half the round-trip time turns
// "when the server sent it" into "the server's time now". Pure, unit tested.

/** How fast the offset estimate may sink between samples (ms per ms): 2 %. */
const DECAY = 0.02;
/** Half the round trip counts for at most this much (ms): beyond, the link is broken anyway. */
const MAX_ONE_WAY_MS = 400;

export class DuelClock {
  private offset: number | null = null;
  private lastLocal = 0;

  /** A message stamped `atMs` (duel time) arrived at local time `localMs` (performance.now). */
  observe(atMs: number, localMs: number): void {
    const sample = atMs - localMs;
    if (this.offset === null) this.offset = sample;
    else {
      const decayed = this.offset - DECAY * Math.max(0, localMs - this.lastLocal);
      this.offset = Math.max(sample, decayed);
    }
    this.lastLocal = localMs;
  }

  get synced(): boolean {
    return this.offset !== null;
  }

  /** One-way delay counted from a round-trip time (ms), clamped. */
  static oneWay(rttMs: number | null): number {
    return rttMs === null || !Number.isFinite(rttMs) ? 0 : Math.max(0, Math.min(MAX_ONE_WAY_MS, rttMs / 2));
  }

  /** The duel time now (ms), or 0 before the first sample. */
  now(localMs: number, rttMs: number | null): number {
    if (this.offset === null) return 0;
    return localMs + this.offset + DuelClock.oneWay(rttMs);
  }

  /** The local time (performance.now) at which the duel reaches `duelMs`. */
  localAt(duelMs: number, rttMs: number | null): number {
    if (this.offset === null) return duelMs;
    return duelMs - this.offset - DuelClock.oneWay(rttMs);
  }

  reset(): void {
    this.offset = null;
    this.lastLocal = 0;
  }
}
