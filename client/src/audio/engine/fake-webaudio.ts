// A small stand-in for the WebAudio API, for the engine's tests (node has no
// AudioContext). Only what the engine uses; gains keep the last value asked
// for, sources record their starts and stops, `advance` moves the clock and
// ends the sources whose time is up. Test-only (imported by engine.test.ts).

class FakeParam {
  value: number;
  constructor(v = 0) {
    this.value = v;
  }
  setValueAtTime(v: number): this {
    this.value = v;
    return this;
  }
  setTargetAtTime(v: number): this {
    this.value = v;
    return this;
  }
  linearRampToValueAtTime(v: number): this {
    this.value = v;
    return this;
  }
  exponentialRampToValueAtTime(v: number): this {
    this.value = v;
    return this;
  }
  cancelScheduledValues(): this {
    return this;
  }
}

export class FakeNode {
  readonly outs = new Set<FakeNode>();
  connected = 0;
  constructor(readonly ctx: FakeContext, readonly kind: string) {
    ctx.nodes.push(this);
  }
  connect(n: FakeNode): FakeNode {
    this.outs.add(n);
    this.connected++;
    return n;
  }
  disconnect(): void {
    this.outs.clear();
  }
}

class FakeGain extends FakeNode {
  readonly gain = new FakeParam(1);
  constructor(ctx: FakeContext) {
    super(ctx, "gain");
  }
}

export class FakeSource extends FakeNode {
  buffer: FakeBuffer | null = null;
  loop = false;
  readonly playbackRate = new FakeParam(1);
  startAt: number | null = null;
  stopAt: number | null = null;
  ended = false;
  onended: (() => void) | null = null;
  constructor(ctx: FakeContext) {
    super(ctx, "source");
    ctx.sources.push(this);
  }
  start(at = 0): void {
    this.startAt = at;
  }
  stop(at = 0): void {
    this.stopAt = at;
  }
  /** Ends it now (the clock reached its end). */
  end(): void {
    if (this.ended) return;
    this.ended = true;
    this.onended?.();
  }
}

class FakePanner extends FakeNode {
  panningModel = "";
  distanceModel = "";
  refDistance = 1;
  rolloffFactor = 1;
  maxDistance = 10000;
  readonly positionX = new FakeParam();
  readonly positionY = new FakeParam();
  readonly positionZ = new FakeParam();
  constructor(ctx: FakeContext) {
    super(ctx, "panner");
  }
}

class FakeFilter extends FakeNode {
  type = "lowpass";
  readonly frequency = new FakeParam(350);
  readonly Q = new FakeParam(1);
  constructor(ctx: FakeContext) {
    super(ctx, "filter");
  }
}

class FakeShaper extends FakeNode {
  curve: Float32Array | null = null;
  oversample = "none";
  constructor(ctx: FakeContext) {
    super(ctx, "shaper");
  }
}

class FakeConvolver extends FakeNode {
  buffer: FakeBuffer | null = null;
  constructor(ctx: FakeContext) {
    super(ctx, "convolver");
  }
}

export class FakeBuffer {
  private readonly data: Float32Array[];
  constructor(
    readonly numberOfChannels: number,
    readonly length: number,
    readonly sampleRate: number,
  ) {
    this.data = Array.from({ length: numberOfChannels }, () => new Float32Array(length));
  }
  get duration(): number {
    return this.length / this.sampleRate;
  }
  getChannelData(ch: number): Float32Array {
    return this.data[ch]!;
  }
}

const param = () => new FakeParam();

export class FakeContext {
  readonly nodes: FakeNode[] = [];
  readonly sources: FakeSource[] = [];
  readonly sampleRate = 8000;
  currentTime = 0;
  state: "running" | "suspended" | "closed" = "running";
  onstatechange: (() => void) | null = null;
  readonly destination = new FakeNode(this, "destination");
  readonly listener = { positionX: param(), positionY: param(), positionZ: param(), forwardX: param(), forwardY: param(), forwardZ: param(), upX: param(), upY: param(), upZ: param() };
  createGain(): FakeGain {
    return new FakeGain(this);
  }
  createBufferSource(): FakeSource {
    return new FakeSource(this);
  }
  createPanner(): FakePanner {
    return new FakePanner(this);
  }
  createBiquadFilter(): FakeFilter {
    return new FakeFilter(this);
  }
  createConvolver(): FakeConvolver {
    return new FakeConvolver(this);
  }
  createWaveShaper(): FakeShaper {
    return new FakeShaper(this);
  }
  createBuffer(channels: number, length: number, sampleRate: number): FakeBuffer {
    return new FakeBuffer(channels, length, sampleRate);
  }
  decodeAudioData(): Promise<FakeBuffer> {
    return Promise.resolve(new FakeBuffer(1, 800, this.sampleRate));
  }
  resume(): Promise<void> {
    this.state = "running";
    return Promise.resolve();
  }
  suspend(): Promise<void> {
    this.state = "suspended";
    return Promise.resolve();
  }
  close(): Promise<void> {
    this.state = "closed";
    return Promise.resolve();
  }
  /** Moves the clock, ending the one-shots whose buffer (or stop) has run out. */
  advance(seconds: number): void {
    this.currentTime += seconds;
    for (const s of this.sources) {
      if (s.ended || s.startAt === null || s.loop) continue;
      const len = s.buffer ? s.buffer.duration / Math.max(0.01, s.playbackRate.value) : 0;
      const end = Math.min(s.startAt + len, s.stopAt ?? Infinity);
      if (this.currentTime >= end) s.end();
    }
  }
  /** Sources started and not ended. */
  get sounding(): FakeSource[] {
    return this.sources.filter((s) => s.startAt !== null && !s.ended);
  }
}

/** An OfflineAudioContext stand-in: renders a silent buffer of the asked size. */
export class FakeOffline extends FakeContext {
  constructor(
    readonly channels: number,
    readonly length: number,
    readonly rate: number,
  ) {
    super();
  }
  startRendering(): Promise<FakeBuffer> {
    return Promise.resolve(new FakeBuffer(this.channels, this.length, this.rate));
  }
}
