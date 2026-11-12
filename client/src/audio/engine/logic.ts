// The audio engine's decisions, kept apart from WebAudio so they are unit
// tested (engine.test.ts): cooldowns, the voice cap's choice of what to drop
// or steal, when the music follows the place, and the footstep strides.
// docs/architecture.md "Sound and music".

import type { MusicCue } from "../types";

// ---------------------------------------------------------------- cooldowns

/** Per id and owner: a repeat sooner than the id's cooldown is dropped. */
export class Cooldowns {
  private readonly last = new Map<string, number>();

  /** True (and noted) when `id` by `owner` may sound at `nowMs`. */
  pass(id: string, owner: number | undefined, cooldownMs: number, nowMs: number): boolean {
    if (cooldownMs <= 0) return true;
    const key = owner === undefined ? id : `${id}#${owner}`;
    const t = this.last.get(key);
    if (t !== undefined && nowMs - t < cooldownMs) return false;
    this.last.set(key, nowMs);
    // Many owners come and go (a crowded hall): forget the stale ones now and then.
    if (this.last.size > 512) for (const [k, v] of this.last) if (nowMs - v > 10_000) this.last.delete(k);
    return true;
  }

  clear(): void {
    this.last.clear();
  }
}

// ---------------------------------------------------------------- the voice cap

/** A sounding voice as the cap sees it. */
export interface CapVoice {
  /** Catalogue priority, 0 (first dropped) .. 3. */
  priority: number;
  /** Metres from the listener when it started (flat: 0). */
  dist: number;
  /** Ours: never counted, never stolen. */
  self: boolean;
}

/**
 * Room for `cand` among `active` under `cap`: "admit" (room left, or ours),
 * the voice to steal (the weakest: lowest priority, then the farthest), or
 * "drop" when `cand` is itself no stronger than the weakest. Pure.
 */
export function admit<V extends CapVoice>(active: readonly V[], cand: CapVoice, cap: number): "admit" | "drop" | V {
  if (cand.self) return "admit";
  let counted = 0;
  let weakest: V | null = null;
  for (const v of active) {
    if (v.self) continue;
    counted++;
    if (!weakest || v.priority < weakest.priority || (v.priority === weakest.priority && v.dist > weakest.dist)) weakest = v;
  }
  if (counted < cap) return "admit";
  if (!weakest) return "drop";
  const stronger = cand.priority > weakest.priority || (cand.priority === weakest.priority && cand.dist < weakest.dist);
  return stronger ? weakest : "drop";
}

// ---------------------------------------------------------------- the music follows the place

const cueKey = (c: MusicCue | null): string => (c ? c.id : "none");

/**
 * Hands the music engine a new piece only once the place's cue has held for
 * `settleS` (doorways and short visits never flap it). The same piece with
 * the other `night` passes at once (the music engine changes variant at the
 * next section). Pure: time comes in.
 */
export class MusicDirector {
  /** What the music engine was last told; undefined before the first. */
  current: MusicCue | null | undefined = undefined;
  private pending: { cue: MusicCue | null; since: number } | null = null;

  constructor(private readonly settleS: number) {}

  /** The place's cue now; returns the cue to hand on, or undefined for no change. */
  update(cue: MusicCue | null, nowS: number): MusicCue | null | undefined {
    const cur = this.current;
    if (cur !== undefined && cueKey(cue) === cueKey(cur)) {
      this.pending = null;
      if (cue && cur && cue.night !== cur.night) {
        this.current = cue;
        return cue;
      }
      return undefined;
    }
    if (!this.pending || cueKey(this.pending.cue) !== cueKey(cue)) this.pending = { cue, since: nowS };
    else this.pending.cue = cue;
    if (nowS - this.pending.since < this.settleS) return undefined;
    this.pending = null;
    this.current = cue;
    return cue;
  }

  reset(): void {
    this.current = undefined;
    this.pending = null;
  }
}

// ---------------------------------------------------------------- footsteps

interface Stride {
  x: number;
  z: number;
  acc: number;
  seen: number;
}

/** A jump this long (m) between two frames is a teleport, not a step. */
const STRIDE_JUMP = 3;

/**
 * Ground covered per character (ourselves and remotes, by id): a step each
 * `stride` metres while walking or running. Entries are pooled by id and
 * forgotten after a while unseen.
 */
export class Strides {
  private readonly by = new Map<number, Stride>();
  private frame = 0;

  /** Called once a frame before the `step`s. */
  begin(): void {
    this.frame++;
    if (this.frame % 600 === 0) for (const [id, s] of this.by) if (this.frame - s.seen > 600) this.by.delete(id);
  }

  /** True when `id`, now at (x, z), puts a foot down. `stride` 0: not moving on foot. */
  step(id: number, x: number, z: number, stride: number): boolean {
    let s = this.by.get(id);
    if (!s) {
      s = { x, z, acc: 0, seen: this.frame };
      this.by.set(id, s);
    }
    const d = Math.hypot(x - s.x, z - s.z);
    s.x = x;
    s.z = z;
    s.seen = this.frame;
    if (stride <= 0 || d > STRIDE_JUMP) {
      // Standing (or teleported): the next walk's first step comes half a stride in.
      s.acc = 0.5;
      return false;
    }
    s.acc += d / stride;
    if (s.acc < 1) return false;
    s.acc %= 1;
    return true;
  }

  forget(id: number): void {
    this.by.delete(id);
  }

  clear(): void {
    this.by.clear();
  }
}

// ---------------------------------------------------------------- helpers

/** A variant index in 0..n-1, never `last` again when there is a choice. */
export function pickVariant(n: number, last: number | undefined, random: () => number): number {
  if (n <= 1) return 0;
  if (last === undefined || last < 0 || last >= n) return Math.floor(random() * n) % n;
  const v = Math.floor(random() * (n - 1)) % (n - 1);
  return v >= last ? v + 1 : v;
}

/** A small stable hash of a string (placeholder pitches). */
export function hashOf(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}
