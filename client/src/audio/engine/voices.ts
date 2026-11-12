// Playing buffers (docs/architecture.md "Sound and music"): a one-shot is one
// buffer source through a gain (and a panner when placed), torn down when it
// ends; a loop loops natively when its buffer is seamless, else plays as
// overlapping copies crossfaded at the seam (MP3 encoder padding). Every
// finished node is disconnected: a stopped node still wired into a bus keeps
// costing the audio thread.

import type { LoopHandle, Vec3 } from "../types";
import { rampTo, setPannerPos } from "./graph";
import type { Playable } from "./sources";

/** A sounding one-shot or loop as the engine tracks it (the voice cap, stats). */
export interface Voice {
  readonly id: string;
  readonly priority: number;
  dist: number;
  readonly self: boolean;
  /** Counted in this pool of the cap. */
  readonly pool: "world" | "ui" | "sprinkle" | "loop";
  stop(fadeS?: number): void;
}

export interface OneShotOpts {
  gain: number;
  rate: number;
  /** Context time to start at. */
  when: number;
  panner: PannerNode | null;
}

/** Starts `buffer` once into `out`; `onEnd` runs once it has finished and been disconnected. */
export function startOneShot(ctx: BaseAudioContext, buffer: AudioBuffer, out: AudioNode, o: OneShotOpts, onEnd: () => void): (fadeS?: number) => void {
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.playbackRate.value = o.rate;
  const g = ctx.createGain();
  g.gain.value = o.gain;
  src.connect(g);
  if (o.panner) {
    g.connect(o.panner);
    o.panner.connect(out);
  } else g.connect(out);
  let done = false;
  src.onended = () => {
    if (done) return;
    done = true;
    src.disconnect();
    g.disconnect();
    o.panner?.disconnect();
    onEnd();
  };
  src.start(o.when);
  return (fadeS = 0.03) => {
    if (done) return;
    const now = ctx.currentTime;
    rampTo(g.gain, 0, now, fadeS / 3);
    try {
      src.stop(now + fadeS);
    } catch {
      // Not started yet (a delayed beat) or already stopped.
    }
  };
}

/** Seconds the copies of a non-seamless loop overlap at the seam. */
const SEAM_S = 0.5;
/** Copies are scheduled this far ahead of their start (s; the engine ticks every 0.25 s). */
const LOOKAHEAD_S = 1.0;
/** The loop's tone filter at tone 0 and 1 (Hz). */
const TONE_LO = 350;
const TONE_HI = 18000;

interface Segment {
  src: AudioBufferSourceNode;
  env: GainNode;
}

/**
 * A looping sound. Made at once (the handle game code keeps); it starts when
 * `begin` hands it its buffers (they may still be loading), and every setter
 * before that is kept.
 */
export class LoopVoice implements LoopHandle, Voice {
  readonly pool = "loop" as const;
  dist = 0;
  private readonly filter: BiquadFilterNode;
  private readonly gainNode: GainNode;
  private panner: PannerNode | null;
  private playable: Playable | null = null;
  private segments: Segment[] = [];
  private nextAt = 0;
  private variant = 0;
  /** The rate asked (setRate); the playing rate is this times the definition's. */
  private baseRate: number;
  private rate = 1;
  private target: number;
  private stopped = false;
  private torn = false;

  constructor(
    private readonly ctx: BaseAudioContext,
    out: AudioNode,
    readonly id: string,
    readonly priority: number,
    readonly self: boolean,
    gain: number,
    rate: number,
    panner: PannerNode | null,
    /** Fade in over this when it starts (s). */
    private readonly fadeInS: number,
    private readonly onGone: (v: LoopVoice) => void,
  ) {
    this.target = gain;
    this.baseRate = rate;
    this.filter = ctx.createBiquadFilter();
    this.filter.type = "lowpass";
    this.filter.frequency.value = TONE_HI;
    this.gainNode = ctx.createGain();
    this.gainNode.gain.value = 0;
    this.panner = panner;
    this.filter.connect(this.gainNode);
    if (panner) {
      this.gainNode.connect(panner);
      panner.connect(out);
    } else this.gainNode.connect(out);
  }

  get playing(): boolean {
    return !this.stopped;
  }

  /** The buffers arrived: start (unless stopped meanwhile). */
  begin(p: Playable, variant: number): void {
    if (this.stopped || this.playable) return;
    this.playable = p;
    this.variant = variant % p.buffers.length;
    this.rate = this.baseRate * p.rate;
    const now = this.ctx.currentTime;
    this.gainNode.gain.setValueAtTime(0, now);
    this.gainNode.gain.linearRampToValueAtTime(this.target * p.gain, now + Math.max(0.02, this.fadeInS));
    this.nextAt = now;
    this.tick();
  }

  /** Schedules the next copy of a crossfaded loop when its start is near. */
  tick(): void {
    const p = this.playable;
    if (!p || this.stopped) return;
    const buf = p.buffers[this.variant]!;
    if (p.seamless) {
      if (this.segments.length) return;
      this.segments.push(this.segment(buf, this.ctx.currentTime, true));
      return;
    }
    const now = this.ctx.currentTime;
    while (this.nextAt < now + LOOKAHEAD_S) {
      const len = buf.duration / this.rate;
      const seam = Math.min(SEAM_S, len / 4);
      const at = Math.max(this.nextAt, now);
      this.segments.push(this.segment(buf, at, false, seam, len));
      this.nextAt = at + len - seam;
      if (len - seam <= 0.01) break;
    }
  }

  private segment(buf: AudioBuffer, at: number, loop: boolean, seam = 0, len = 0): Segment {
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.loop = loop;
    src.playbackRate.value = this.rate;
    const env = this.ctx.createGain();
    src.connect(env);
    env.connect(this.filter);
    if (!loop) {
      const first = this.segments.length === 0;
      env.gain.setValueAtTime(first ? 1 : 0, at);
      if (!first) env.gain.linearRampToValueAtTime(1, at + seam);
      env.gain.setValueAtTime(1, at + len - seam);
      env.gain.linearRampToValueAtTime(0, at + len);
    }
    const seg = { src, env };
    src.onended = () => {
      src.disconnect();
      env.disconnect();
      this.segments = this.segments.filter((s) => s !== seg);
      if (this.stopped && this.segments.length === 0) this.teardown();
    };
    src.start(at);
    if (!loop) src.stop(at + len + 0.05);
    return seg;
  }

  setGain(gain: number, fadeS = 0.1): void {
    this.target = gain;
    if (!this.playable || this.stopped) return;
    rampTo(this.gainNode.gain, gain * this.playable.gain, this.ctx.currentTime, Math.max(0.005, fadeS / 3));
  }

  setRate(rate: number): void {
    this.baseRate = rate;
    if (!this.playable) return;
    const r = rate * this.playable.rate;
    if (Math.abs(r - this.rate) < 1e-3) return;
    this.rate = r;
    const now = this.ctx.currentTime;
    for (const s of this.segments) rampTo(s.src.playbackRate, r, now, 0.05);
  }

  setTone(tone: number): void {
    const t = Math.min(1, Math.max(0, tone));
    rampTo(this.filter.frequency, TONE_LO * Math.pow(TONE_HI / TONE_LO, t), this.ctx.currentTime, 0.05);
  }

  setPosition(at: Vec3): void {
    if (this.panner) setPannerPos(this.panner, at);
  }

  stop(fadeS = 0.3): void {
    if (this.stopped) return;
    this.stopped = true;
    const now = this.ctx.currentTime;
    rampTo(this.gainNode.gain, 0, now, Math.max(0.005, fadeS / 3));
    if (this.segments.length === 0) {
      this.teardown();
      return;
    }
    for (const s of this.segments) {
      try {
        s.src.stop(now + fadeS + 0.05);
      } catch {
        // Already stopping.
      }
    }
  }

  private teardown(): void {
    if (this.torn) return;
    this.torn = true;
    this.filter.disconnect();
    this.gainNode.disconnect();
    this.panner?.disconnect();
    this.panner = null;
    this.onGone(this);
  }
}

/** A loop that plays nothing (no context yet is not a reason: those are LoopVoices that start later). */
export const SILENT_LOOP: LoopHandle = {
  setGain: () => undefined,
  setRate: () => undefined,
  setTone: () => undefined,
  setPosition: () => undefined,
  stop: () => undefined,
  playing: false,
};
