// The server's clock as the client knows it (docs/architecture.md, "Classes and progression"):
// `Welcome.unixMs` sets it, and every
// `Pong.serverTimeMs` refines it. Game time, the lighting, the class
// schedule and the lesson beats are all read from this clock, so every
// client agrees with the server however wrong the computer's own clock is.
//
// The offset is kept against the monotonic clock (`performance.now()`), so a
// system clock adjustment never moves game time. A Pong is stamped when the
// server handled the Ping, half a round trip before it arrives; the sample
// with the shortest round trip among the last few is the most accurate one.
// A sample far from the current offset is a real jump (the development
// clock, `POST /api/dev/clock`, or a server restart) and is taken at once.
//
// Pure (the time source is injected), unit tested in clock.test.ts.

/** Samples kept for the shortest-round-trip choice. */
const SAMPLES = 8;
/** A sample this far (ms) from the offset in use is a jump, not jitter. */
const JUMP_MS = 1500;

interface Sample {
  offset: number;
  rtt: number;
}

export class ServerClock {
  /** Server Unix ms minus local monotonic ms; null before `Welcome`. */
  private offset: number | null = null;
  private samples: Sample[] = [];

  constructor(private readonly monotonic: () => number = () => performance.now()) {}

  /** `Welcome.unixMs`: a fresh estimate (the frame's one-way delay is not known yet). */
  welcome(unixMs: number): void {
    this.samples = [];
    this.offset = unixMs - this.monotonic();
  }

  /** `Pong.serverTimeMs`, with the round trip the connection measured for it (ms). */
  pong(serverTimeMs: number, rttMs: number | null): void {
    if (!Number.isFinite(serverTimeMs) || serverTimeMs <= 0) return;
    const rtt = rttMs !== null && Number.isFinite(rttMs) && rttMs >= 0 ? rttMs : 0;
    const offset = serverTimeMs + rtt / 2 - this.monotonic();
    if (this.offset === null || Math.abs(offset - this.offset) > JUMP_MS) {
      this.samples = [{ offset, rtt }];
      this.offset = offset;
      return;
    }
    this.samples.push({ offset, rtt });
    if (this.samples.length > SAMPLES) this.samples.shift();
    let best = this.samples[0]!;
    for (const s of this.samples) if (s.rtt < best.rtt) best = s;
    this.offset = best.offset;
  }

  /** Whether the server has told us the time yet. */
  get synced(): boolean {
    return this.offset !== null;
  }

  /** The server's Unix time now (ms); the computer's own clock until `Welcome`. */
  now(): number {
    return this.offset === null ? Date.now() : this.monotonic() + this.offset;
  }

  /** The server's Unix time (ms) at local monotonic time `localMs` (`performance.now()`). */
  fromLocal(localMs: number): number {
    return localMs + (this.offset ?? Date.now() - this.monotonic());
  }

  /** Local monotonic ms (`performance.now()`) at server time `unixMs`. */
  toLocal(unixMs: number): number {
    return unixMs - (this.offset ?? Date.now() - this.monotonic());
  }
}
