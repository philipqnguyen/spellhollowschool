// A stand-in OfflineAudioContext for tests (node has no WebAudio): records
// every node made, every param automation and every start/stop, and checks
// the rules WebAudio would enforce (an exponential ramp to a non-positive
// value throws; times are never negative). The sfx tests render each recipe
// into one and look at the log.

type Event = [string, ...unknown[]];

class MockParam {
  value: number;
  readonly defaultValue: number;
  constructor(
    private readonly log: Event[],
    private readonly name: string,
    v = 0,
  ) {
    this.value = v;
    this.defaultValue = v;
  }
  private check(op: string, v: number, t: number): this {
    if (!Number.isFinite(v) || !Number.isFinite(t)) throw new Error(`${this.name}.${op}: not finite (${v}, ${t})`);
    if (t < 0) throw new Error(`${this.name}.${op}: negative time ${t}`);
    this.log.push([`${this.name}.${op}`, round(v), round(t)]);
    return this;
  }
  setValueAtTime(v: number, t: number) {
    return this.check("set", v, t);
  }
  linearRampToValueAtTime(v: number, t: number) {
    return this.check("lin", v, t);
  }
  exponentialRampToValueAtTime(v: number, t: number) {
    if (v <= 0) throw new Error(`${this.name}.exp: ramp to ${v} (must be > 0)`);
    return this.check("exp", v, t);
  }
  setTargetAtTime(v: number, t: number, c: number) {
    if (c <= 0) throw new Error(`${this.name}.target: time constant ${c}`);
    return this.check("target", v, t);
  }
  cancelScheduledValues(t: number) {
    return this.check("cancel", 0, t);
  }
}

const round = (x: number) => Math.round(x * 1e6) / 1e6;

let nextId = 0;

class MockNode {
  readonly id = nextId++;
  readonly context: MockContext;
  constructor(
    protected readonly ctx: MockContext,
    readonly kind: string,
  ) {
    this.context = ctx;
    ctx.nodes.push(this);
  }
  connect(to: MockNode | MockParam) {
    this.ctx.log.push(["connect", this.kind, to instanceof MockNode ? to.kind : "param"]);
    return to;
  }
  disconnect() {}
  protected param(name: string, v = 0): MockParam {
    return new MockParam(this.ctx.log, `${this.kind}.${name}`, v);
  }
}

class MockSource extends MockNode {
  started: number | null = null;
  stopped: number | null = null;
  start(t = 0, offset = 0) {
    if (t < 0 || offset < 0) throw new Error(`${this.kind}.start(${t}, ${offset})`);
    if (this.started !== null) throw new Error(`${this.kind} started twice`);
    this.started = t;
    this.ctx.log.push([`${this.kind}.start`, round(t), round(offset)]);
  }
  stop(t = 0) {
    if (t < 0) throw new Error(`${this.kind}.stop(${t})`);
    this.stopped = t;
    this.ctx.log.push([`${this.kind}.stop`, round(t)]);
  }
}

class MockOsc extends MockSource {
  type = "sine";
  frequency = this.param("frequency", 440);
  detune = this.param("detune", 0);
  constructor(ctx: MockContext) {
    super(ctx, "osc");
  }
}

export class MockBuffer {
  private readonly data: Float32Array[];
  constructor(
    readonly numberOfChannels: number,
    readonly length: number,
    readonly sampleRate: number,
  ) {
    if (!Number.isInteger(length) || length <= 0) throw new Error(`buffer length ${length}`);
    this.data = Array.from({ length: numberOfChannels }, () => new Float32Array(length));
  }
  get duration() {
    return this.length / this.sampleRate;
  }
  getChannelData(c: number): Float32Array {
    return this.data[c]!;
  }
}

class MockBufferSource extends MockSource {
  buffer: MockBuffer | null = null;
  loop = false;
  playbackRate = this.param("playbackRate", 1);
  constructor(ctx: MockContext) {
    super(ctx, "buffer");
  }
}

class MockGain extends MockNode {
  gain = this.param("gain", 1);
  constructor(ctx: MockContext) {
    super(ctx, "gain");
  }
}

class MockBiquad extends MockNode {
  type = "lowpass";
  frequency = this.param("frequency", 350);
  Q = this.param("Q", 1);
  gain = this.param("gain", 0);
  constructor(ctx: MockContext) {
    super(ctx, "biquad");
  }
}

class MockDelay extends MockNode {
  delayTime = this.param("delayTime", 0);
  constructor(ctx: MockContext) {
    super(ctx, "delay");
  }
}

class MockShaper extends MockNode {
  curve: Float32Array | null = null;
  constructor(ctx: MockContext) {
    super(ctx, "shaper");
  }
}

class MockPanner extends MockNode {
  pan = this.param("pan", 0);
  constructor(ctx: MockContext) {
    super(ctx, "panner");
  }
}

export class MockContext {
  readonly log: Event[] = [];
  readonly nodes: MockNode[] = [];
  readonly buffers: MockBuffer[] = [];
  readonly destination: MockNode;
  readonly currentTime = 0;
  constructor(
    readonly numberOfChannels: number,
    readonly length: number,
    readonly sampleRate: number,
  ) {
    this.destination = new MockNode(this, "destination");
  }
  createOscillator() {
    return new MockOsc(this);
  }
  createGain() {
    return new MockGain(this);
  }
  createBiquadFilter() {
    return new MockBiquad(this);
  }
  createBufferSource() {
    return new MockBufferSource(this);
  }
  createDelay() {
    return new MockDelay(this);
  }
  createWaveShaper() {
    return new MockShaper(this);
  }
  createStereoPanner() {
    return new MockPanner(this);
  }
  createBuffer(channels: number, length: number, sampleRate: number) {
    const b = new MockBuffer(channels, length, sampleRate);
    this.buffers.push(b);
    return b;
  }
  /** Sources started and their stop times (s), to check they end within the render. */
  sources(): MockSource[] {
    return this.nodes.filter((n): n is MockSource => n instanceof MockSource);
  }
}
