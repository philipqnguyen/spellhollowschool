// The audio engine's fixed graph (docs/architecture.md "Sound and music"):
// master > music, effects, ambience, interface; a fade node after master for
// the hidden tab; the effects bus's shared reverb (two convolvers crossfaded
// when the place's space changes, none on Low); the listener (the camera).

import type { AudioSettings } from "../../settings";
import type { Bus } from "../catalogue";
import type { Space } from "../places";
import type { Vec3 } from "../types";

/** A short ramp on every gain change (s, the time constant of setTargetAtTime: ~30 ms to settle). */
const RAMP = 0.01;

/**
 * The output's makeup (+4 dB, linear) after master: the mix measured about
 * -26 LUFS at the default volumes (measured on recordings), quiet beside
 * other pages; with it, about -22. The safety after it keeps the peaks a
 * loud moment or full sliders can reach under full scale.
 */
export const OUTPUT_MAKEUP = 1.585;
/** The safety's knee: linear below this (-2 dBFS), rounded off above it, never past full scale. */
const SAFETY_KNEE = 0.79;

let safety: Float32Array<ArrayBuffer> | null = null;

/** The safety's curve: its input is the signal halved (so it covers +-2), its output the signal, soft-limited under 1. */
export function safetyCurve(): Float32Array<ArrayBuffer> {
  if (safety) return safety;
  const n = 4097;
  const c = new Float32Array(n);
  const k = SAFETY_KNEE;
  for (let i = 0; i < n; i++) {
    const x = 2 * ((2 * i) / (n - 1) - 1);
    const a = Math.abs(x);
    const y = a <= k ? a : k + (1 - k) * Math.tanh((a - k) / (1 - k));
    c[i] = Math.sign(x) * y;
  }
  safety = c;
  return c;
}

/** Each space's impulse: length (s), decay power and the wet level of the send. */
const SPACES: Readonly<Record<Exclude<Space, "open">, { seconds: number; power: number; wet: number }>> = {
  close: { seconds: 0.45, power: 3.5, wet: 0.22 },
  room: { seconds: 0.9, power: 3.2, wet: 0.18 },
  hall: { seconds: 1.8, power: 3.0, wet: 0.24 },
  great_hall: { seconds: 2.6, power: 2.8, wet: 0.28 },
};

/** Sets an AudioParam smoothly (or at once when `at` is 0). */
export function rampTo(p: AudioParam, value: number, now: number, tc = RAMP): void {
  p.cancelScheduledValues(now);
  p.setTargetAtTime(value, now, tc);
}

/** A decaying stereo noise impulse for `space`. */
export function makeImpulse(ctx: BaseAudioContext, space: Exclude<Space, "open">, random: () => number): AudioBuffer {
  const s = SPACES[space];
  const len = Math.max(1, Math.round(ctx.sampleRate * s.seconds));
  const ir = ctx.createBuffer(2, len, ctx.sampleRate);
  for (let ch = 0; ch < 2; ch++) {
    const d = ir.getChannelData(ch);
    for (let i = 0; i < len; i++) d[i] = (random() * 2 - 1) * Math.pow(1 - i / len, s.power);
  }
  return ir;
}

export class Graph {
  readonly master: GainNode;
  /** After master: the makeup and the soft safety (OUTPUT_MAKEUP, safetyCurve). */
  private readonly trim: GainNode;
  private readonly safety: WaveShaperNode;
  /** After the safety: the hidden tab's fade (1 shown, 0 hidden). */
  readonly fade: GainNode;
  readonly buses: Readonly<Record<Bus, GainNode>>;
  /** Effects voices connect here: the effects bus plus the reverb's send. */
  readonly effectsIn: GainNode;
  private readonly send: GainNode;
  private readonly conv: [ConvolverNode, ConvolverNode];
  private readonly wet: [GainNode, GainNode];
  private live = 0;
  private space: Space | null = null;
  private reverbOn = true;
  private readonly impulses = new Map<Space, AudioBuffer>();

  constructor(
    readonly ctx: AudioContext,
    private readonly random: () => number,
  ) {
    this.fade = ctx.createGain();
    this.fade.connect(ctx.destination);
    this.safety = ctx.createWaveShaper();
    this.safety.curve = safetyCurve();
    this.safety.connect(this.fade);
    this.trim = ctx.createGain();
    // The curve's input covers twice full scale: halve into it (the curve gives the level back).
    this.trim.gain.value = OUTPUT_MAKEUP / 2;
    this.trim.connect(this.safety);
    this.master = ctx.createGain();
    this.master.connect(this.trim);
    const bus = () => {
      const g = ctx.createGain();
      g.connect(this.master);
      return g;
    };
    this.buses = { music: bus(), effects: bus(), ambience: bus(), interface: bus() };
    this.effectsIn = ctx.createGain();
    this.effectsIn.connect(this.buses.effects);
    this.send = ctx.createGain();
    this.send.gain.value = 0;
    this.conv = [ctx.createConvolver(), ctx.createConvolver()];
    this.wet = [ctx.createGain(), ctx.createGain()];
    for (let i = 0; i < 2; i++) {
      this.wet[i]!.gain.value = 0;
      this.send.connect(this.conv[i]!);
      this.conv[i]!.connect(this.wet[i]!);
      this.wet[i]!.connect(this.buses.effects);
    }
  }

  /** Volumes: master (0 when muted) and each bus. */
  setSettings(s: AudioSettings): void {
    const now = this.ctx.currentTime;
    rampTo(this.master.gain, s.muted ? 0 : s.master, now);
    rampTo(this.buses.music.gain, s.music, now);
    rampTo(this.buses.effects.gain, s.effects, now);
    rampTo(this.buses.ambience.gain, s.ambience, now);
    rampTo(this.buses.interface.gain, s.interface, now);
  }

  /** The hidden tab: fade to `to` over `seconds`. */
  fadeTo(to: number, seconds: number): void {
    const now = this.ctx.currentTime;
    const g = this.fade.gain;
    g.cancelScheduledValues(now);
    g.setValueAtTime(g.value, now);
    g.linearRampToValueAtTime(to, now + seconds);
  }

  /** Low quality: no reverb (the send is cut from the convolvers, which then cost nothing). */
  setReverb(on: boolean): void {
    if (on === this.reverbOn) return;
    this.reverbOn = on;
    if (on) {
      for (const c of this.conv) this.send.connect(c);
      const s = this.space;
      this.space = null;
      if (s) this.setSpace(s);
    } else {
      this.send.disconnect();
      const now = this.ctx.currentTime;
      rampTo(this.send.gain, 0, now);
      for (const w of this.wet) rampTo(w.gain, 0, now);
    }
  }

  /** The place's space: the reverb crossfades to its impulse (open: no send). */
  setSpace(space: Space): void {
    if (space === this.space) return;
    this.space = space;
    if (!this.reverbOn) return;
    const now = this.ctx.currentTime;
    if (space === "open") {
      rampTo(this.send.gain, 0, now, 0.3);
      return;
    }
    let ir = this.impulses.get(space);
    if (!ir) {
      ir = makeImpulse(this.ctx, space, this.random);
      this.impulses.set(space, ir);
    }
    const next = 1 - this.live;
    // A convolver's buffer is set once per swap on the idle one, then the two crossfade.
    try {
      this.conv[next]!.buffer = ir;
    } catch {
      return;
    }
    rampTo(this.wet[this.live]!.gain, 0, now, 0.3);
    rampTo(this.wet[next]!.gain, SPACES[space].wet, now, 0.3);
    rampTo(this.send.gain, 1, now, 0.3);
    this.live = next;
  }

  get currentSpace(): Space | null {
    return this.space;
  }

  /**
   * The listener at the camera. Babylon is left-handed (+z forward), WebAudio
   * right-handed: z is mirrored here and in every panner position.
   */
  setListener(at: Vec3, forward: Vec3): void {
    const l = this.ctx.listener;
    const fz = -forward[2];
    if (l.positionX) {
      l.positionX.value = at[0];
      l.positionY.value = at[1];
      l.positionZ.value = -at[2];
      l.forwardX.value = forward[0];
      l.forwardY.value = forward[1];
      l.forwardZ.value = fz;
      l.upX.value = 0;
      l.upY.value = 1;
      l.upZ.value = 0;
    } else {
      // Firefox: the listener has no AudioParams.
      (l as unknown as { setPosition(x: number, y: number, z: number): void }).setPosition(at[0], at[1], -at[2]);
      (l as unknown as { setOrientation(...v: number[]): void }).setOrientation(forward[0], forward[1], fz, 0, 1, 0);
    }
  }
}

/** A panner for a world sound at `at` (Babylon coordinates), heard to `maxM`. */
export function makePanner(ctx: BaseAudioContext, at: Vec3, refM: number, rolloff: number, maxM: number): PannerNode {
  const p = ctx.createPanner();
  p.panningModel = "equalpower";
  p.distanceModel = "inverse";
  p.refDistance = refM;
  p.rolloffFactor = rolloff;
  p.maxDistance = maxM;
  setPannerPos(p, at);
  return p;
}

export function setPannerPos(p: PannerNode, at: Vec3): void {
  if (p.positionX) {
    p.positionX.value = at[0];
    p.positionY.value = at[1];
    p.positionZ.value = -at[2];
  } else {
    (p as unknown as { setPosition(x: number, y: number, z: number): void }).setPosition(at[0], at[1], -at[2]);
  }
}
