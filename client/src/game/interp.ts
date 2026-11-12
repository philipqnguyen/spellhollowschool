// Snapshot interpolation for other players.
//
// Snapshots carry the server tick, so each state has a server timestamp
// (tick / tickHz). `SnapshotClock` maps server time onto the local clock, and
// each entity's `Track` is sampled at "now minus the interpolation delay", so
// remote characters move smoothly between two known states instead of jumping
// at 10 Hz. Pure (no Babylon, no DOM) so it is unit tested.

import type { Anim } from "../wasm/sim_wasm";

/** How far behind the newest state remote characters are drawn (ms). */
export const INTERP_DELAY_MS = 100;
/** Longest extrapolation past the newest state when the next one is late (ms). */
export const MAX_EXTRAPOLATE_MS = 100;
/** A jump longer than this between two states is a teleport and is not interpolated (m). */
export const TELEPORT_DISTANCE = 8;
const TRACK_LENGTH = 8;

/**
 * Estimates `localTime - serverTime`. Network delay only ever adds to that
 * difference, so the smallest one seen is the best estimate; it then creeps up
 * slowly so clock drift and route changes are followed.
 */
export class SnapshotClock {
  private offset: number | null = null;

  /** Records a snapshot stamped `serverMs` that arrived at local time `localMs`. */
  observe(serverMs: number, localMs: number): void {
    const sample = localMs - serverMs;
    if (this.offset === null || sample < this.offset) this.offset = sample;
    else this.offset += (sample - this.offset) * 0.02;
  }

  /** Server time corresponding to local time `localMs` (or `localMs` before any snapshot). */
  serverTime(localMs: number): number {
    return localMs - (this.offset ?? 0);
  }

  reset(): void {
    this.offset = null;
  }
}

export interface Sample {
  /** Server time, ms. */
  t: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  anim: Anim;
}

/** Output of `Track.sampleAt`; reused to avoid per-frame allocation. */
export interface Pose {
  x: number;
  y: number;
  z: number;
  yaw: number;
  anim: Anim;
}

/** Shortest signed angle from `a` to `b`, in (-π, π]. */
export function angleDelta(a: number, b: number): number {
  const d = (b - a) % (2 * Math.PI);
  if (d > Math.PI) return d - 2 * Math.PI;
  if (d <= -Math.PI) return d + 2 * Math.PI;
  return d;
}

/** The recent states of one remote entity. */
export class Track {
  private readonly samples: Sample[] = [];

  /**
   * @param snapshotMs the server's snapshot interval. The server only sends
   *   entities that moved, so a state after a longer gap means the entity was
   *   standing still until shortly before it; see `push`.
   */
  constructor(private readonly snapshotMs: number) {}

  get length(): number {
    return this.samples.length;
  }

  get latest(): Sample | undefined {
    return this.samples[this.samples.length - 1];
  }

  push(s: Sample): void {
    const last = this.latest;
    if (last) {
      if (s.t <= last.t) {
        // Same or older tick: replace a duplicate, ignore anything older.
        if (s.t === last.t) this.samples[this.samples.length - 1] = s;
        return;
      }
      if (Math.hypot(s.x - last.x, s.y - last.y, s.z - last.z) > TELEPORT_DISTANCE) {
        this.samples.length = 0;
      } else if (s.t - last.t > this.snapshotMs * 1.5) {
        // It stood at `last` until it started moving, about one snapshot before `s`;
        // without this it would glide slowly across the whole quiet gap.
        this.samples.push({ ...last, t: s.t - this.snapshotMs });
      }
    }
    this.samples.push(s);
    if (this.samples.length > TRACK_LENGTH) this.samples.splice(0, this.samples.length - TRACK_LENGTH);
  }

  /** The pose at server time `t`, written into `out`. Returns false when the track is empty. */
  sampleAt(t: number, out: Pose): boolean {
    const n = this.samples.length;
    if (n === 0) return false;
    const first = this.samples[0]!;
    if (n === 1 || t <= first.t) return copy(t <= first.t ? first : this.samples[n - 1]!, out);

    const last = this.samples[n - 1]!;
    if (t >= last.t) {
      // Late state: keep moving briefly along the last velocity, unless it stopped
      // (idle, or an emote: emotes are only played standing still).
      const prev = this.samples[n - 2]!;
      const ahead = Math.min(t - last.t, MAX_EXTRAPOLATE_MS);
      copy(last, out);
      if ((last.anim === "walk" || last.anim === "run") && ahead > 0) {
        const k = ahead / (last.t - prev.t);
        out.x += (last.x - prev.x) * k;
        out.y += (last.y - prev.y) * k;
        out.z += (last.z - prev.z) * k;
      }
      return true;
    }

    // Find the pair a.t <= t < b.t (tracks are short, so a linear scan from the end).
    let i = n - 2;
    while (i > 0 && this.samples[i]!.t > t) i--;
    const a = this.samples[i]!;
    const b = this.samples[i + 1]!;
    const k = (t - a.t) / (b.t - a.t);
    out.x = a.x + (b.x - a.x) * k;
    out.y = a.y + (b.y - a.y) * k;
    out.z = a.z + (b.z - a.z) * k;
    out.yaw = a.yaw + angleDelta(a.yaw, b.yaw) * k;
    out.anim = k < 0.5 ? a.anim : b.anim;
    return true;
  }
}

function copy(s: Sample, out: Pose): true {
  out.x = s.x;
  out.y = s.y;
  out.z = s.z;
  out.yaw = s.yaw;
  out.anim = s.anim;
  return true;
}
